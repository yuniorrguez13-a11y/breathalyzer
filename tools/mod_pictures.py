#!/usr/bin/env python3
"""Mod pictures (Mods page v2): the picture each mod shows in Mod Manager and, when turned on, in the game.

    python tools/mod_pictures.py status [--library <folder>]
    python tools/mod_pictures.py on | off [--rows] [--background] [--library <folder>]
    python tools/mod_pictures.py refresh [<mod>] [--library <folder>]

Where a mod's picture comes from, first that exists: the picture you chose (`<library>\\pictures\\<id>\\chosen.png`,
written by `choose` / modman), the mod's `[mod] icon`, a BCML package's `image` only when it names a file in the
package (a web address is never downloaded), `thumbnail.png` (or `icon.png`) at the mod's root, else a tile with the
mod's first letter. Pictures are cached in the library (icon-64.png, icon-128.png, bg-320.png) and made again only
when the source changes.

In the game (off until you turn it on): at deploy, Pack/Bootup.pack's Common.sblarc gets a picture panel beside the
Mods page (and, when asked, small row icons and a picture behind the list), built over the Bootup.pack that would be
used anyway (the last mod's, or the game's). Every other file of that pack stays byte for byte the same (checked; on
any difference the pictures are left out). It needs a bigger size-table entry: see docs/mod-pictures.md.
Modman calls check / on_add / deploy_files by itself (docs/mod-format.md). Exit codes: 0 done, 2 error.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import struct
import sys
import zlib

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
import layout_patch as lp  # noqa: E402

try:   # (pictures, resizing and BC encoding; without it: letter tiles in RGBA8 only)
    import menu_bg as mb  # noqa: E402
except Exception:  # pragma: no cover  (a menu_bg another job is still writing must not stop pictures)
    mb = None

TOOL_ID = "botw-mod-loader mod_pictures"
VERSION = "1.1.0"          # in every cache key: a new version makes the pictures again (1.1.0: panes at scale 1)
PICTURES_DIR = "pictures"
SETTINGS_FILE = "settings.json"
INFO_FILE = "pictures.json"
CACHE_DIR = os.path.join("cache", "pictures")   # in the library: the last Bootup.pack made (one, 32 MB)
EXTS = (".png", ".jpg", ".jpeg", ".webp")
MAX_FILE = 8 * 1024 * 1024
MAX_PX_PLAIN = 2_100_000   # without Pillow (pure-Python PNG reading is slow past this)
MAX_SIDE_PILLOW = 4096
ICON = 128                 # cached icon sizes: 128 (the game's panel, the window's details) and 64 (lists)
SMALL = 64
BG = (320, 180)            # a background cell
BOOTUP = "Pack/Bootup.pack"
RSTB = "System/Resource/ResourceSizeTable.product.srsizetable"
ATLAS_JSON = "deploy/pictures/atlas.json"
GENERATED = "generated:pictures"

# The panes and textures added to the game's layouts (names the Mods-page mod looks for)
PANEL = ("blyt/OptionWindow_00.bflyt", "P_ModPanel_00", (460, 20), (256, 256), None)
BACK = ("blyt/OptionWindow_00.bflyt", "P_ModBg_00", (0, 0), (1280, 720), "P_Capture_00")
ROWS = [("blyt/PaOptionBtnL_00.bflyt", "P_ModIcon_00", (-375, 0), (40, 40), None),
        ("blyt/PaOptionBtnLevel_00.bflyt", "P_ModIcon_00", (-375, 0), (40, 40), None)]
ICONS_TEX = "BzModIcons_00^l"
BG_TEX = "BzModBg_00^l"
MAX_CELLS = 64             # 8 x 8 icons of 128 px (the Mods page lists up to 30 mods)
BG_GRID = (4, 4)           # 16 backgrounds of 320 x 180 in one 1280 x 720 texture

DEFAULTS = {"in_game": False, "rows": False, "background": False}

# Letter tiles: 5 x 7 capitals and digits, drawn big (one bit a column, top row first)
GLYPHS = {
    "A": "0E 11 11 1F 11 11 11", "B": "1E 11 11 1E 11 11 1E", "C": "0E 11 10 10 10 11 0E",
    "D": "1E 11 11 11 11 11 1E", "E": "1F 10 10 1E 10 10 1F", "F": "1F 10 10 1E 10 10 10",
    "G": "0E 11 10 17 11 11 0F", "H": "11 11 11 1F 11 11 11", "I": "0E 04 04 04 04 04 0E",
    "J": "07 02 02 02 02 12 0C", "K": "11 12 14 18 14 12 11", "L": "10 10 10 10 10 10 1F",
    "M": "11 1B 15 15 11 11 11", "N": "11 11 19 15 13 11 11", "O": "0E 11 11 11 11 11 0E",
    "P": "1E 11 11 1E 10 10 10", "Q": "0E 11 11 11 15 12 0D", "R": "1E 11 11 1E 14 12 11",
    "S": "0F 10 10 0E 01 01 1E", "T": "1F 04 04 04 04 04 04", "U": "11 11 11 11 11 11 0E",
    "V": "11 11 11 11 11 0A 04", "W": "11 11 11 15 15 15 0A", "X": "11 11 0A 04 0A 11 11",
    "Y": "11 11 0A 04 04 04 04", "Z": "1F 01 02 04 08 10 1F", "0": "0E 11 13 15 19 11 0E",
    "1": "04 0C 04 04 04 04 0E", "2": "0E 11 01 02 04 08 1F", "3": "1F 02 04 02 01 11 0E",
    "4": "02 06 0A 12 1F 02 02", "5": "1F 10 1E 01 01 11 0E", "6": "06 08 10 1E 11 11 0E",
    "7": "1F 01 02 04 08 08 08", "8": "0E 11 11 0E 11 11 0E", "9": "0E 11 11 0F 01 02 0C",
    "?": "0E 11 01 02 04 00 04",
}
# Dark, strong colours: the white letter stays easy to read on every one
TILE_COLOURS = [(0x1F, 0x4E, 0x8C), (0x8C, 0x2F, 0x1F), (0x1F, 0x6E, 0x3A), (0x6A, 0x2C, 0x8C), (0x8C, 0x5A, 0x12),
                (0x12, 0x6A, 0x74), (0x8C, 0x1F, 0x5A), (0x3A, 0x46, 0x5A)]


class PictureError(Exception):
    pass


class Generated(list):
    """deploy_files' result: (RomFS or deploy\\ path, bytes, size-table entry or None) items, with `notes`,
    `attention` and plain values (in_game, cached, cells) for launch.json made["pictures"]."""

    def __init__(self, items=(), *, notes=None, attention=None, **values):
        super().__init__(items)
        self.notes = list(notes or [])
        self.attention = list(attention or [])
        self.__dict__.update(values)


class Source:
    """Where a mod's picture comes from: kind is chosen / icon / package / thumbnail / letter."""

    def __init__(self, path: str | None, kind: str, label: str):
        self.path, self.kind, self.label = path, kind, label

    def __repr__(self):
        return f"Source({self.path!r}, {self.kind!r})"


# ================================================================================================ library files

def _root(lib) -> str:
    return getattr(lib, "root", lib)


def folder(lib, key: str) -> str:
    """<library>\\pictures\\<key> (a mod id, "_title" or "_mods-page")."""
    if not key or "/" in key or "\\" in key or key in (".", "..") or key.startswith("."):
        raise PictureError(f"'{key}' isn't a mod id")
    return os.path.join(_root(lib), PICTURES_DIR, key)


def load_settings(lib) -> dict:
    """The in-game picture settings (all off until the player turns them on)."""
    try:
        with open(os.path.join(_root(lib), PICTURES_DIR, SETTINGS_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    out = dict(DEFAULTS)
    if isinstance(data, dict):
        out.update({k: bool(data[k]) for k in DEFAULTS if isinstance(data.get(k), bool)})
    return out


def save_settings(lib, **changes) -> dict:
    st = load_settings(lib)
    for k, v in changes.items():
        if k not in DEFAULTS:
            raise PictureError(f"no picture setting {k}")
        st[k] = bool(v)
    path = os.path.join(_root(lib), PICTURES_DIR, SETTINGS_FILE)
    _write(path, json.dumps({"tool": TOOL_ID, "format": 1, **st}, indent=1).encode())
    return st


def _write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def choice(lib, key: str) -> str | None:
    """The picture the player chose for `key`, or None."""
    d = folder(lib, key)
    for ext in EXTS:
        p = os.path.join(d, "chosen" + ext)
        if os.path.isfile(p):
            return p
    return None


def choose(lib, key: str, file: str) -> str:
    """Copy the player's picture to <library>\\pictures\\<key>\\chosen.<ext> (it never goes into the mod's folder).
    Refuses a file that isn't a picture this tool can read. Returns the path written."""
    ext = os.path.splitext(file)[1].lower()
    if ext not in EXTS:
        raise PictureError(f"{os.path.basename(file)}: use a PNG (or JPG, WEBP with Pillow)")
    load(file)   # (refuses what can't be shown)
    d = folder(lib, key)
    os.makedirs(d, exist_ok=True)
    for e in EXTS:
        p = os.path.join(d, "chosen" + e)
        if os.path.isfile(p):
            os.remove(p)
    dest = os.path.join(d, "chosen" + ext)
    shutil.copyfile(file, dest + ".tmp")
    os.replace(dest + ".tmp", dest)
    return dest


def clear_choice(lib, key: str) -> bool:
    gone = False
    for ext in EXTS:
        p = os.path.join(folder(lib, key), "chosen" + ext)
        if os.path.isfile(p):
            os.remove(p)
            gone = True
    return gone


# ================================================================================================ pictures

def _pillow():
    try:
        from PIL import Image
        return Image
    except Exception:
        return None


def _sniff(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def probe(path: str) -> tuple[str, int, int]:
    """(kind, width, height) of a picture file, read from its header; raises PictureError when it can't be used."""
    name = os.path.basename(path)
    try:
        size = os.path.getsize(path)
        if size > MAX_FILE:
            raise PictureError(f"{name} is too big ({size // (1024 * 1024)} MB): at most 8 MB")
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError as e:
        raise PictureError(f"can't open {name} ({e.strerror or e})") from None
    kind = _sniff(head)
    if not kind:
        raise PictureError(f"{name} isn't a PNG, JPG or WEBP picture")
    if kind != "png":
        Image = _pillow()
        if Image is None:
            raise PictureError(f"{name}: {kind.upper()} needs Pillow (py -m pip install pillow); PNG always works")
        try:
            with Image.open(path) as im:
                w, h = im.size
        except Exception:
            raise PictureError(f"{name} is damaged") from None
    else:
        if head[12:16] != b"IHDR" or len(head) < 24:
            raise PictureError(f"{name} is damaged (no PNG header)")
        w, h = struct.unpack(">II", head[16:24])
    if w <= 0 or h <= 0:
        raise PictureError(f"{name} is damaged (size {w} x {h})")
    if _pillow() is None and w * h > MAX_PX_PLAIN:
        raise PictureError(f"{name} is {w} x {h}: without Pillow at most about 2 million pixels (1920 x 1080)")
    if w > MAX_SIDE_PILLOW or h > MAX_SIDE_PILLOW:
        raise PictureError(f"{name} is {w} x {h}: at most {MAX_SIDE_PILLOW} pixels a side")
    return kind, w, h


def load(path: str):
    """A picture file as menu_bg.Picture (RGBA8)."""
    kind, w, h = probe(path)
    if mb is None:
        raise PictureError("pictures need tools/menu_bg.py")
    with open(path, "rb") as f:
        data = f.read()
    Image = _pillow()
    try:
        if Image is not None:
            im = Image.open(io.BytesIO(data))
            im.load()
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            return mb.Picture(im.width, im.height, bytearray(im.tobytes()))
        return mb.read_png(data)
    except PictureError:
        raise
    except Exception:
        raise PictureError(f"{os.path.basename(path)} is damaged") from None


def letter_of(name: str) -> str:
    for c in name or "":
        if c.isalnum():
            c = c.upper()
            return c if c in GLYPHS else "?"
    return "?"


def letter_tile(name: str, size: int = ICON):
    """A square tile in a strong colour with the mod's first letter in white."""
    c = TILE_COLOURS[zlib.crc32((name or "").encode()) % len(TILE_COLOURS)]
    px = bytearray(bytes((c[0], c[1], c[2], 255)) * (size * size))
    rows = [int(x, 16) for x in GLYPHS[letter_of(name)].split()]
    cell = max(1, (size * 3 // 5) // 7)
    gw, gh = 5 * cell, 7 * cell
    x0, y0 = (size - gw) // 2, (size - gh) // 2
    white = b"\xff\xff\xff\xff" * cell
    for gy, bits in enumerate(rows):
        for gx in range(5):
            if bits >> (4 - gx) & 1:
                for yy in range(y0 + gy * cell, y0 + (gy + 1) * cell):
                    a = (yy * size + x0 + gx * cell) * 4
                    px[a:a + 4 * cell] = white
    if mb is not None:
        return mb.Picture(size, size, px, name=f"letter {letter_of(name)}")
    return _Pic(size, size, px)


class _Pic:   # (stand-in for menu_bg.Picture when menu_bg isn't there)
    def __init__(self, w, h, px):
        self.w, self.h, self.px = w, h, px


def cover(pic, w: int, h: int):
    """The picture scaled to fill w x h, cropped around its middle."""
    s = max(w / pic.w, h / pic.h)
    bw, bh = w / s, h / s
    x0, y0 = (pic.w - bw) / 2, (pic.h - bh) / 2
    return mb.resample(pic, (x0, y0, x0 + bw, y0 + bh), w, h)


def dark_blur(pic):
    """A background cell: small, soft and dark, so words over it stay readable."""
    w, h = pic.w, pic.h
    small = mb.resample(pic, (0, 0, w, h), max(1, w // 8), max(1, h // 8))
    soft = mb.resample(small, (0, 0, small.w, small.h), w, h)
    table = bytes(int(v * 0.4) for v in range(256))
    px = soft.px
    for ch in range(3):
        px[ch::4] = bytes(px[ch::4]).translate(table)
    px[3::4] = b"\xff" * (w * h)
    return soft


PNG_SIG = bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))
OPAQUE = bytes((255,))


def _read_png(data: bytes):
    """A PNG as RGBA8: menu_bg's reader, else a small one for 8-bit RGB/RGBA PNGs (the cache's own files)."""
    if mb is not None:
        return mb.read_png(data)
    if data[:8] != PNG_SIG:
        raise PictureError("not a PNG")
    pos, idat, head = 8, [], None
    while pos + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            head = struct.unpack(">IIBBBBB", body[:13])
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + n
    if head is None or head[2] != 8 or head[3] not in (2, 6) or head[6] != 0:
        raise PictureError("only 8-bit RGB or RGBA PNGs are read without tools/menu_bg.py")
    w, h, bpp = head[0], head[1], 4 if head[3] == 6 else 3
    raw = zlib.decompress(b"".join(idat))
    stride = w * bpp
    prev = bytearray(stride)
    out = bytearray()
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        if bpp == 4:
            out += line
        else:
            px = bytearray(w * 4)
            px[0::4], px[1::4], px[2::4] = line[0::3], line[1::3], line[2::3]
            px[3::4] = OPAQUE * w
            out += px
    return _Pic(w, h, out)


def png_bytes(pic) -> bytes:
    if mb is not None:
        return mb.png_bytes(pic)
    stride = pic.w * 4
    raw = b"".join(b"\0" + bytes(pic.px[y * stride:(y + 1) * stride]) for y in range(pic.h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", pic.w, pic.h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# ================================================================================================ sources and cache

def _manifest(lib, entry):
    try:
        import modman
        root = os.path.join(_root(lib), "mods", entry["id"])
        return modman.read_manifest(root)[0] if os.path.isdir(root) else None
    except Exception:
        return None


def _package_image(root: str, pkg: dict | None) -> str | None:
    if pkg is None:
        try:
            import modman
            pkg = modman._package_info(root)
        except Exception:   # (modman's reader missing: read the package's own file)
            pkg = {}
            for rel in (".bnp/info.json", ".ukmm/ukmm.json"):
                try:
                    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    continue
                if isinstance(data, dict):
                    pkg = data.get("meta") if isinstance(data.get("meta"), dict) else data
                    break
    img = (pkg or {}).get("image")
    if not isinstance(img, str) or not img or "://" in img or os.path.isabs(img) or ":" in img:
        return None   # (a web address is never downloaded: S7)
    parts = img.replace("\\", "/").split("/")
    if ".." in parts or not img.lower().endswith(EXTS):
        return None
    p = os.path.join(root, *parts)
    return p if os.path.isfile(p) else None


def source_for(lib, entry: dict, manifest=None, pkg: dict | None = None) -> Source:
    """Where a mod's picture comes from (the order in docs/mod-pictures.md). A source that can't be read is skipped."""
    mid = entry["id"]
    root = os.path.join(_root(lib), "mods", mid)
    cands: list[tuple[str | None, str, str]] = [(choice(lib, mid), "chosen", "your choice")]
    man = manifest if manifest is not None else _manifest(lib, entry)
    if man is not None and getattr(man, "icon", None):
        cands.append((os.path.join(root, *man.rel(man.icon).split("/")), "icon", "the mod's icon"))
    if os.path.isdir(root):
        cands.append((_package_image(root, pkg), "package", "the package's picture"))
        for n in ("thumbnail.png", "icon.png"):
            cands.append((os.path.join(root, n), "thumbnail", f"the mod's {n}"))
    for path, kind, label in cands:
        if path and os.path.isfile(path):
            try:
                probe(path)
            except PictureError:
                continue
            return Source(path, kind, label)
    return Source(None, "letter", "a letter tile")


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _info(lib, mid: str) -> dict:
    try:
        with open(os.path.join(folder(lib, mid), INFO_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data.get("format") == 1 else {}
    except (OSError, ValueError):
        return {}


def refresh(lib, entry: dict, manifest=None, force: bool = False, pkg: dict | None = None) -> dict:
    """Make the cached pictures of one mod when their source changed (or `force`). Returns pictures.json."""
    mid = entry["id"]
    d = folder(lib, mid)
    man = manifest if manifest is not None else _manifest(lib, entry)
    src = source_for(lib, entry, man, pkg)
    key = hashlib.sha256(f"{VERSION}|{src.kind}|".encode()
                         + (_file_hash(src.path).encode() if src.path else entry.get("name", mid).encode())).hexdigest()
    bg_src = None
    if man is not None and getattr(man, "background", None):
        p = os.path.join(_root(lib), "mods", mid, *man.rel(man.background).split("/"))
        if os.path.isfile(p):
            bg_src = p
    bg_key = hashlib.sha256(f"{VERSION}|bg|".encode() + _file_hash(bg_src).encode()).hexdigest() if bg_src else None
    old = _info(lib, mid)
    names = (f"icon-{ICON}.png", f"icon-{SMALL}.png")
    have = src.kind == "letter" or all(os.path.isfile(os.path.join(d, n)) for n in names)
    fresh = old.get("hash") == key and have and old.get("bg_hash") == bg_key
    if not force and fresh and (bg_key is None or os.path.isfile(os.path.join(d, "bg-320.png"))):
        return old
    note = None
    big = None
    if mb is None and (src.path or bg_src):   # (no resizing without tools/menu_bg.py: a letter tile for now)
        note, src, bg_src = "pictures need tools/menu_bg.py", Source(None, "letter", "a letter tile"), None
    if src.path:
        try:
            big = cover(load(src.path), ICON, ICON)
        except PictureError as e:
            note, src = str(e), Source(None, "letter", "a letter tile")
    os.makedirs(d, exist_ok=True)
    if big is not None:
        _write(os.path.join(d, names[0]), png_bytes(big))
        _write(os.path.join(d, names[1]), png_bytes(mb.resample(big, (0, 0, big.w, big.h), SMALL, SMALL)))
    else:   # (a letter tile isn't kept as a file: it is drawn when needed, and Mod Manager draws its own)
        for n in names:
            if os.path.isfile(os.path.join(d, n)):
                os.remove(os.path.join(d, n))
    info = {"tool": TOOL_ID, "format": 1, "source": src.kind, "file": src.path, "label": src.label, "hash": key}
    if bg_src:
        try:
            _write(os.path.join(d, "bg-320.png"), png_bytes(dark_blur(cover(load(bg_src), *BG))))
            info.update(bg=bg_src, bg_hash=bg_key)
        except PictureError as e:
            note = note or str(e)
    elif os.path.isfile(os.path.join(d, "bg-320.png")):
        os.remove(os.path.join(d, "bg-320.png"))
    if note:
        info["note"] = note
    _write(os.path.join(d, INFO_FILE), json.dumps(info, indent=1).encode())
    return info


def icon_path(lib, mod_id: str, size: int = SMALL) -> str | None:
    p = os.path.join(folder(lib, mod_id), f"icon-{ICON if size > SMALL else SMALL}.png")
    return p if os.path.isfile(p) else None


# ================================================================================================ modman hooks

def check(manifest, mod_root: str) -> tuple[list[str], list[str]]:
    """(problems, notes) of a mod.toml's pictures. A picture that can't be shown is a note, never a refusal: the next
    source (or a letter tile) shows instead. (problems stays empty; modman itself refuses a file that is missing.)"""
    problems: list[str] = []
    notes: list[str] = []
    for key in ("icon", "background", "title_picture"):
        rel = getattr(manifest, key, None)
        if not rel:
            continue
        path = os.path.join(mod_root, *manifest.rel(rel).split("/"))
        if not os.path.isfile(path):
            continue   # (modman says it's missing)
        try:
            kind, w, h = probe(path)
        except PictureError as e:
            notes.append(f"[mod] {key}: {e}" + (" (a letter tile shows instead)" if key == "icon" else ""))
            continue
        if key == "icon" and max(w, h) > 2 * min(w, h):
            notes.append(f"[mod] icon {rel} is {w} x {h}: a square picture looks best (it is cut to a square)")
        if key == "title_picture" and min(w, h) < 360:
            notes.append(f"[mod] title_picture {rel} is small ({w} x {h}): it will look blurry on the title screen")
    return problems, notes


def on_add(lib, entry: dict, mod_folder: str, manifest) -> list[str]:
    """After add: make the mod's cached pictures. Returns one short note."""
    try:
        info = refresh(lib, entry, manifest)
    except (PictureError, OSError) as e:
        return [f"picture: not made ({e})"]
    note = f"picture: {info.get('label', 'a letter tile')}"
    if info.get("note"):
        note += f" ({info['note']})"
    return [note]


def hash_inputs(lib):
    """What the game's pictures are made from, cheaply (file sizes and times, no reading), for modman's deploy hash:
    None while pictures in the game are off (the hash stays as without this tool)."""
    st = load_settings(lib)
    if not st["in_game"]:
        return None

    def sig(path: str):
        try:
            s_ = os.stat(path)
            return [os.path.basename(path), s_.st_size, s_.st_mtime_ns]
        except OSError:
            return None
    out = {"v": VERSION, **st, "mods": []}
    for m in list(getattr(lib, "mods", []) or [])[:MAX_CELLS]:
        root = os.path.join(_root(lib), "mods", m["id"])
        files = [choice(lib, m["id"])] + [os.path.join(root, *n.split("/")) for n in
                                           ("mod.toml", "thumbnail.png", "icon.png", ".bnp/info.json")]
        out["mods"].append([m["id"], m.get("name"), [sig(f) for f in files if f and os.path.isfile(f)]])
    page = choice(lib, "_mods-page")
    out["page"] = sig(page) if page and st["background"] else None
    return out


def _entry_in_use(plan, name: str) -> int | None:
    w = plan.winner(RSTB)
    if w is None:
        return None
    try:
        data = w.read()
        if mb is not None:
            return mb.rstb_value(data, name)
        import bnp
        return bnp.Rstb(data).get(name)
    except Exception:
        return None


def _round32(n: int) -> int:
    return (n + 31) & ~31


def _cells(lib, settings: dict) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    """(icon cells [(key, png path)], background cells [(key, png path)], notes)."""
    icons, bgs, notes = [], [], []
    mods = list(getattr(lib, "mods", []) or [])
    for m in mods[:MAX_CELLS]:
        try:
            info = refresh(lib, m)
        except (PictureError, OSError) as e:
            notes.append(f"{m.get('name', m['id'])}: no picture ({e})")
            continue
        p = os.path.join(folder(lib, m["id"]), f"icon-{ICON}.png")
        icons.append((m["id"], p if info.get("source") != "letter" and os.path.isfile(p) else
                      "letter:" + (m.get("name") or m["id"])))
        if settings["background"] and info.get("bg_hash"):
            bgs.append((m["id"], os.path.join(folder(lib, m["id"]), "bg-320.png")))
    if len(mods) > MAX_CELLS:
        notes.append(f"only the first {MAX_CELLS} mods get a picture in the game")
    if settings["background"] and mb is None:
        notes.append("no picture behind the list: it needs tools/menu_bg.py")
    elif settings["background"]:
        page = choice(lib, "_mods-page")
        if page:
            try:
                d = folder(lib, "_mods-page")
                h = _file_hash(page)
                out = os.path.join(d, "bg-320.png")
                if _info(lib, "_mods-page").get("bg_hash") != h or not os.path.isfile(out):
                    _write(out, png_bytes(dark_blur(cover(load(page), *BG))))
                    _write(os.path.join(d, INFO_FILE), json.dumps({"tool": TOOL_ID, "format": 1, "bg": page,
                                                                    "bg_hash": h}).encode())
                bgs.insert(0, ("_mods-page", out))
            except (PictureError, OSError) as e:
                notes.append(f"the Mods page picture isn't used ({e})")
        bgs = bgs[:BG_GRID[0] * BG_GRID[1]]
    return icons, bgs, notes


def _atlas(paths: list[tuple[str, str]], cell: tuple[int, int], cols: int, rows: int):
    w, h = cell[0] * cols, cell[1] * rows
    px = bytearray(w * h * 4)
    for k, (_, p) in enumerate(paths):
        if p.startswith("letter:"):
            pic = letter_tile(p[len("letter:"):], cell[0])
        else:
            with open(p, "rb") as f:
                pic = _read_png(f.read())
        if pic is None or (pic.w, pic.h) != cell:
            continue
        cx, cy = (k % cols) * cell[0], (k // cols) * cell[1]
        for y in range(cell[1]):
            a = ((cy + y) * w + cx) * 4
            px[a:a + cell[0] * 4] = pic.px[y * cell[0] * 4:(y + 1) * cell[0] * 4]
    return w, h, px


def _encode(px: bytearray, w: int, h: int, alpha: bool) -> tuple[int, bytes]:
    if mb is not None and hasattr(mb, "encode_bc"):
        return (lp.BC3 if alpha else lp.BC1), mb.encode_bc(px, w, h, alpha)
    return lp.RGBA8, bytes(px)


def deploy_files(lib, plan) -> Generated:
    """The game's pictures for modman's deploy (runs last, over the final winning Bootup.pack). Off (an empty list)
    until the player turns pictures on. Problems are one `attention` line each, and the game's files stay."""
    st = load_settings(lib)
    if not st["in_game"]:
        return Generated(in_game=False)
    if plan is None or not callable(getattr(plan, "winner", None)):
        return Generated(attention=["pictures in the game are off this time: the deploy has no RomFS plan"],
                         in_game=False)
    try:
        w = plan.winner(BOOTUP)
        if w is None:
            raise PictureError("the game's Pack/Bootup.pack isn't known (set up the game files in Savage Wind)")
        base = w.mod or "the game"
        pack = w.read()
        icons, bgs, notes = _cells(lib, st)
        n = len(icons)
        cols = 4 if n <= 16 else 8
        recipe = {"v": VERSION, "rows": st["rows"], "bg": bool(bgs), "cols": cols,
                  "icons": [(k, p if p.startswith("letter:") else _file_hash(p)) for k, p in icons],
                  "bgs": [(k, _file_hash(p)) for k, p in bgs]}
        key = hashlib.sha256(hashlib.sha256(pack).digest() + json.dumps(recipe, sort_keys=True).encode()
                             ).hexdigest()[:32]
        cache = os.path.join(_root(lib), CACHE_DIR)
        hit = os.path.join(cache, key + ".pack")
        meta_path = os.path.join(cache, key + ".json")
        cached = os.path.isfile(hit) and os.path.isfile(meta_path)
        if cached:
            with open(hit, "rb") as f:
                new_pack = f.read()
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            size, old_size = int(meta["common_size"]), int(meta["old_common_size"])
            damaged = meta.get("sha256") != hashlib.sha256(new_pack).hexdigest()
            if damaged or lp.archive_check(pack, new_pack, {lp.COMMON}):   # (S1 again on every kept pack)
                _cache_put(cache, "", b"", None)
                raise PictureError("the kept Bootup.pack was damaged; it is made again at the next deploy")
        else:
            texs = []
            aw, ah, apx = _atlas(icons, (ICON, ICON), cols, cols)
            fmt, data = _encode(apx, aw, ah, True)
            texs.append(lp.NewTexture(ICONS_TEX, fmt, aw, ah, data))
            lays: dict[str, list] = {}
            for lay, name, pos, size_, after in [PANEL] + (ROWS if st["rows"] else []):
                lays.setdefault(lay, []).append((name, ICONS_TEX, pos, size_, after))
            if bgs:
                bw, bh, bpx = _atlas(bgs, BG, *BG_GRID)
                fmt, data = _encode(bpx, bw, bh, False)
                texs.append(lp.NewTexture(BG_TEX, fmt, bw, bh, data))
                lay, name, pos, size_, after = BACK
                lays.setdefault(lay, []).insert(0, (name, BG_TEX, pos, size_, after))
            r = lp.rebuild_bootup(pack, lambda raw: lp.patch_common(raw, lays, texs))
            new_pack, size, old_size = r.pack, r.common_size, r.old_common_size
            _cache_put(cache, key, new_pack, {"common_size": size, "old_common_size": old_size, "base": base,
                                              "sha256": hashlib.sha256(new_pack).hexdigest()})
        old_entry = _entry_in_use(plan, lp.COMMON_RSTB)
        need = _round32(size) + max(0, (old_entry or 0) - _round32(old_size)) if old_entry else _round32(size) + 0x1000
        entry = (lp.COMMON_RSTB, need) if old_entry is None or need > old_entry else None
    except (PictureError, lp.PatchError, OSError, ValueError, KeyError) as e:
        return Generated(attention=[f"pictures in the game are off this time: {e}"], in_game=False)
    atlas = {"format": 1, "grids": [{"tex": ICONS_TEX, "w": ICON * cols, "h": ICON * cols, "cols": cols,
                                     "rows": cols, "pane": "P_ModPanel_00",
                                     **({"row_pane": "P_ModIcon_00"} if st["rows"] else {})}],
             "cells": {k: {"icon": i, "bg": -1} for i, (k, _) in enumerate(icons)}}
    if bgs:
        atlas["grids"].append({"tex": BG_TEX, "w": BG[0] * BG_GRID[0], "h": BG[1] * BG_GRID[1], "cols": BG_GRID[0],
                               "rows": BG_GRID[1], "pane": "P_ModBg_00"})
        for j, (k, _) in enumerate(bgs):
            atlas["cells"].setdefault(k, {"icon": -1, "bg": -1})["bg"] = j
    what = "a big picture of the selected mod beside the list" + (", small row pictures" if st["rows"] else "") \
        + (", a picture behind the list" if bgs else "")
    notes = [f"in the game: {what} ({n} mod(s)" + (", made earlier" if cached else "") + ")."] + notes
    if st["background"] and not bgs and mb is not None:
        notes.append("no picture behind the list yet: pick one for the Mods page in Mod Manager")
    if base.startswith("generated:"):
        notes.append("built over the Pack/Bootup.pack merged from your mods (its other files stay as they are)")
    elif base != "the game":
        notes.append(f"built over {base}'s Pack/Bootup.pack (its other files stay as they are)")
    items = [(BOOTUP, new_pack, entry), (ATLAS_JSON, json.dumps(atlas, indent=1).encode(), None)]
    return Generated(items, notes=notes, in_game=True, cached=cached, cells=n)


def _cache_put(folder_: str, key: str, pack: bytes, meta: dict | None) -> None:
    """Keep one made Bootup.pack (32 MB): the others go. meta None: only clear."""
    os.makedirs(folder_, exist_ok=True)
    for e in os.scandir(folder_):
        if e.name.endswith((".pack", ".json", ".tmp")):
            try:
                os.remove(e.path)
            except OSError:
                pass
    if meta is None:
        return
    _write(os.path.join(folder_, key + ".pack"), pack)
    _write(os.path.join(folder_, key + ".json"), json.dumps(meta).encode())


# ================================================================================================ command line

NOT_SHOWN_YET = ("Note: the game doesn't show these pictures yet (the Mods page reads them; drawing them is still to "
                 "come). While on, the deploy still replaces Bootup.pack (Savage Wind's HUD button letters go back "
                 "to the game's) and may ship a full size table. Turn them off with: python tools/mod_pictures.py off")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mod_pictures", description="Mod pictures (Mods page v2).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "on", "off", "refresh"):
        s = sub.add_parser(name)
        s.add_argument("--library")
        if name == "on":
            s.add_argument("--rows", action="store_true", help="also small pictures on the rows")
            s.add_argument("--background", action="store_true", help="also a picture behind the list")
        if name == "refresh":
            s.add_argument("mod", nargs="?")
    a = ap.parse_args(argv)
    try:
        import modman
        lib = modman.Library.load(modman.resolve_library(a.library))
    except Exception as e:
        print(f"Can't open the mod library: {e}")
        return 2
    try:
        if a.cmd == "on":
            save_settings(lib, in_game=True, rows=a.rows, background=a.background)
            print("Pictures in the game: on at your next start (deploy, or Play in Mod Manager).")
            print(NOT_SHOWN_YET)
            return 0
        if a.cmd == "off":
            save_settings(lib, in_game=False)
            print("Pictures in the game: off at your next start.")
            return 0
        if a.cmd == "refresh":
            hits = [m for m in lib.mods
                    if not a.mod or a.mod.lower() in (m["id"].lower(), str(m.get("name", "")).lower())]
            if a.mod and not hits:
                print(f"No mod named {a.mod} in the library.")
                return 2
            for m in hits:
                info = refresh(lib, m, force=True)
                print(f"{m.get('name', m['id'])}: {info.get('label')}")
            return 0
        st = load_settings(lib)
        print(f"Pictures in the game: {'on' if st['in_game'] else 'off'}"
              + (f" (row pictures {'on' if st['rows'] else 'off'}, picture behind the list "
                 f"{'on' if st['background'] else 'off'})" if st["in_game"] else ""))
        for m in lib.mods:
            print(f"  {m.get('name', m['id'])}: {source_for(lib, m).label}")
        return 0
    except (PictureError, OSError) as e:
        print(f"Error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
