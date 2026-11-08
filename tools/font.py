#!/usr/bin/env python3
"""Fonts for the game's menus: the missing symbols, font mods, the easy-read font, and one place to choose.

    python tools/font.py status [--library <folder>]
    python tools/font.py choose auto|game|easy-read|<mod id> [--library <folder>]
    python tools/font.py fix-symbols on|off [--library <folder>]
    python tools/font.py check <font.ttf>
    python tools/font.py build <Font_US.sbfarc> --out <file> [--font easy-read|<font.ttf>] [--fix-symbols]
                               [--fonts Caption_00,NormalS_00,Normal_00] [--install <Savage Wind folder>]
    python tools/font.py preview <Font_US.sbfarc> --out <picture.png> [--text "..."] [--font-name Caption_00]

The game draws its English, European and Japanese menus from bitmap fonts in Font/Font_US, Font_EU and
Font_JP.sbfarc (Yaz0, then SARC): Caption_00 (Options row names and the description line), NormalS_00 (values,
buttons; outlined) and Normal_00 (titles, dialogue). Caption_00 lacks { } # $ % @ ^ ` | ~ (Japanese also
" & ' * ; = \\), so those show as gaps. This tool edits those archives at deploy, on this PC, from the archive
that is in use (a RomFS mod's, else the game's own; both only read):

  fix-symbols  draws every printable ASCII character a menu font lacks into free cells, from Fira Sans (the copy
               Savage Wind ships next to savagewind.exe, else assets/fonts/), sized and thinned to match the font.
               Nothing else in the archive changes, and the archive keeps its size, so no size-table entry is needed.
  a font       redraws every character the chosen TrueType font has, in Caption_00, NormalS_00 and Normal_00, in the
               game's own cell grid (characters the font lacks keep the game's drawing). "easy-read" is Fira Sans
               SemiBold; a font mod brings its own font ([font] in its mod.toml, docs/fonts.md).

External_00 (button icons; Savage Wind finds it by its hash) is never touched. The output holds Nintendo's glyphs, so
it is built on this PC for this PC and never shared. modman's deploy calls deploy_files(lib, plan). The settings
(choice, fix-symbols) are kept in <library>\\font.json; built archives are cached in <library>\\fonts\\cache\\.
Nothing here comes from a decompilation; the formats were read from the user's own files (docs/fonts.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import sys
import tomllib
import zlib
from typing import NamedTuple

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
from wiiu_model import tegra  # noqa: E402  (Tegra block-linear swizzle only)

TOOL_ID = "botw-mod-loader font"
FORMAT = 1
BUILD_VERSION = 1            # part of the cache key: raise it when the bytes this tool writes change
LANGS = ("US", "EU", "JP")   # the bitmap-font languages; KR/CN/TW/TH use outline fonts (later)
ARCHIVE = "Font/Font_{}.sbfarc"
SIZE_KEY = "Font/Font_{}.bfarc"  # the archive's name in the size table (RSTB): no "s"
MENU_FONTS = ("Caption_00", "NormalS_00", "Normal_00")  # everything the Options and Mods pages draw with
NEVER = "External_00"        # Savage Wind finds this sheet by its hash (button icons): never edited
ASCII = "".join(chr(c) for c in range(0x20, 0x7F))
ASSETS = os.path.join(REPO, "assets", "fonts")
FIRA_MEDIUM = "FiraSans-Medium.ttf"      # fix-symbols (thinned to the game font's stems)
FIRA_SEMIBOLD = "FiraSans-SemiBold.ttf"  # the easy-read font
FIRA_LICENSE = "FiraSans-OFL.txt"
SETTINGS = "font.json"
CACHE_DIR = os.path.join("fonts", "cache")
CACHE_KEEP = 6               # cached archives kept (3 languages x the last 2 recipes)
FIX_SYMBOLS_DEFAULT = False  # turned on by default only after the game check (docs/fonts.md)
CHOICES = ("auto", "game", "easy-read")
EASY_READ = "Easy-read"
GAME_FONT = "Game font"
OUTLINE_PX = 2.0             # NormalS_00's dark ring around each letter (2 px, read from its sheets)
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_FONT_FILE = 32 * 1024 * 1024
LABEL_MAX = 28               # the chooser row's value (ASCII; the menu font can't show more)


class FontError(Exception):
    pass


# ================================================================================================= small helpers

def _u16(d, o: int) -> int:
    return struct.unpack_from("<H", d, o)[0]


def _u32(d, o: int) -> int:
    return struct.unpack_from("<I", d, o)[0]


def _u64(d, o: int) -> int:
    return struct.unpack_from("<Q", d, o)[0]


def _need(d, o: int, n: int, what: str) -> None:
    if o < 0 or o + n > len(d):
        raise FontError(f"{what} runs past the end of the file")


def _ascii_label(s: str, n: int = LABEL_MAX) -> str:
    s = " ".join("".join(c if 0x20 <= ord(c) < 0x7F else "?" for c in s).split())
    return s if len(s) <= n else s[:n - 3].rstrip() + "..."


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ================================================================================================= Yaz0, SARC

def yaz0_decode(data: bytes) -> tuple[bytes, int]:
    """(decompressed bytes, the header's alignment field)."""
    if len(data) < 16 or data[:4] != b"Yaz0":
        raise FontError("not Yaz0 data")
    size, align = struct.unpack_from(">II", data, 4)
    if size > MAX_ARCHIVE:
        raise FontError(f"Yaz0 data says it unpacks to {size} bytes: too big for a font archive")
    out = bytearray()
    i = 16
    try:
        while len(out) < size:
            code = data[i]
            i += 1
            if code == 0xFF and size - len(out) >= 8:  # eight stored bytes: one slice
                if i + 8 > len(data):
                    raise IndexError
                out += data[i:i + 8]
                i += 8
                continue
            for bit in (0x80, 0x40, 0x20, 0x10, 8, 4, 2, 1):
                if code & bit:
                    out.append(data[i])
                    i += 1
                else:
                    b1, b2 = data[i], data[i + 1]
                    i += 2
                    dist = ((b1 & 0x0F) << 8 | b2) + 1
                    n = b1 >> 4
                    if n:
                        n += 2
                    else:
                        n = data[i] + 0x12
                        i += 1
                    start = len(out) - dist
                    if start < 0:
                        raise FontError("Yaz0 data refers to bytes before its start")
                    if dist >= n:
                        out += out[start:start + n]
                    else:
                        out += (bytes(out[start:]) * (n // dist + 1))[:n]
                if len(out) >= size:
                    break
    except IndexError:
        raise FontError("Yaz0 data is cut short") from None
    del out[size:]
    return bytes(out), align


def yaz0_stored(data: bytes, align: int = 0) -> bytes:
    """Valid Yaz0 that stores every byte as is: made at once (the game unpacks it like any other)."""
    n = len(data)
    groups = (n + 7) // 8
    padded = bytes(data) + bytes(groups * 8 - n)
    body = bytearray(groups * 9)
    body[0::9] = b"\xff" * groups
    for j in range(8):
        body[j + 1::9] = padded[j::8]
    cut = groups * 9 - (groups * 8 - n)
    return b"Yaz0" + struct.pack(">II", n, align) + bytes(4) + bytes(body[:cut])


class Sarc:
    """A little-endian (Switch) SARC: its files in data order, with their slots."""

    def __init__(self, data: bytes):
        d = self.data = bytes(data)
        if len(d) < 0x20 or d[:4] != b"SARC":
            raise FontError("not a SARC archive")
        if d[6:8] != b"\xff\xfe":
            raise FontError("a big-endian (Wii U) SARC: only Switch font archives are edited")
        hs = _u16(d, 4)
        self.data_off = _u32(d, 0x0C)
        if d[hs:hs + 4] != b"SFAT":
            raise FontError("SARC without its file table")
        count = _u16(d, hs + 6)
        self.nodes_at = hs + _u16(d, hs + 4)
        sfnt = self.nodes_at + 16 * count
        _need(d, sfnt, 8, "SARC name table")
        if d[sfnt:sfnt + 4] != b"SFNT":
            raise FontError("SARC without its name table")
        names_at = sfnt + _u16(d, sfnt + 4)
        self.files: list[list] = []  # [name, node index, start, end] (relative to the data offset)
        for k in range(count):
            _, attr, start, end = struct.unpack_from("<IIII", d, self.nodes_at + 16 * k)
            if not attr >> 24 & 1:
                raise FontError("a SARC file without a name")
            p = names_at + (attr & 0xFFFF) * 4
            q = d.find(b"\0", p)
            if q < 0 or self.data_off + end > len(d) or start > end:
                raise FontError("damaged SARC file table")
            self.files.append([d[p:q].decode("utf-8", "replace"), k, start, end])
        self.files.sort(key=lambda f: f[2])

    def names(self) -> list[str]:
        return [f[0] for f in self.files]

    def get(self, name: str) -> bytes:
        for n, _, s, e in self.files:
            if n == name:
                return self.data[self.data_off + s:self.data_off + e]
        raise KeyError(name)

    def rebuild(self, changes: dict[str, bytes]) -> bytes:
        """The archive with some files' bytes replaced. A file that still fits its slot (its bytes plus the padding
        before the next file) stays in place, so every other byte and the archive's size stay the same. A bigger
        one moves the files after it by a multiple of their alignment."""
        d = self.data
        out = bytearray(d[:self.data_off])
        shift = 0
        total_rel = len(d) - self.data_off
        for i, (name, k, start, end) in enumerate(self.files):
            nxt = self.files[i + 1][2] if i + 1 < len(self.files) else total_rel
            slot = nxt - start
            body = changes.get(name, d[self.data_off + start:self.data_off + end])
            if len(body) > slot:
                if i + 1 < len(self.files):
                    later = 0
                    for f in self.files[i + 1:]:
                        later |= self.data_off + f[2]
                    unit = min(later & -later if later else 4, 0x10000)
                    grow = -(-(len(body) - slot) // unit) * unit
                else:
                    grow = len(body) - slot
                slot += grow
                shift += grow
            new_start = len(out) - self.data_off
            out += body
            out += bytes(slot - len(body))
            struct.pack_into("<II", out, self.nodes_at + 16 * k + 8, new_start, new_start + len(body))
        struct.pack_into("<I", out, 8, len(out))
        return bytes(out)


# ================================================================================================= BC4 blocks

def bc4_decode(block) -> list[int]:
    """16 texel values (row by row) of one BC4 block."""
    e0, e1 = block[0], block[1]
    if e0 > e1:
        pal = [e0, e1] + [((7 - k) * e0 + k * e1 + 3) // 7 for k in range(1, 7)]
    else:
        pal = [e0, e1] + [((5 - k) * e0 + k * e1 + 2) // 5 for k in range(1, 5)] + [0, 255]
    bits = int.from_bytes(bytes(block[2:8]), "little")
    return [pal[(bits >> (3 * t)) & 7] for t in range(16)]


_ORDER8 = (0, 2, 3, 4, 5, 6, 7, 1)   # palette index by position from e0 to e1 (8-value mode)
_ORDER6 = (0, 2, 3, 4, 5, 1)         # (6-value mode; its indices 6 and 7 are 0 and 255)


def _bc4_try(px: list[int], e0: int, e1: int) -> tuple[int, int]:
    """(squared error, index bits) of the block with these endpoints (e0 != e1), each texel at its nearest palette
    value (found by position along the ramp, not by search)."""
    err = bits = 0
    if e0 > e1:
        pal = [e0, e1] + [((7 - k) * e0 + k * e1 + 3) // 7 for k in range(1, 7)]
        span = e0 - e1
        for t, v in enumerate(px):
            pos = ((e0 - v) * 7 + span // 2) // span
            i = _ORDER8[0 if pos < 0 else 7 if pos > 7 else pos]
            err += (v - pal[i]) ** 2
            bits |= i << (3 * t)
        return err, bits
    pal = [e0, e1] + [((5 - k) * e0 + k * e1 + 2) // 5 for k in range(1, 5)] + [0, 255]
    span = e1 - e0 or 1
    for t, v in enumerate(px):
        pos = ((v - e0) * 5 + span // 2) // span
        i = _ORDER6[0 if pos < 0 else 5 if pos > 5 else pos]
        e = (v - pal[i]) ** 2
        if v * v < e:
            i, e = 6, v * v
        if (255 - v) ** 2 < e:
            i, e = 7, (255 - v) ** 2
        err += e
        bits |= i << (3 * t)
    return err, bits


def bc4_encode(px: list[int]) -> bytes:
    """One BC4 block from 16 texel values. Exact for flat blocks and for 0 and 255 (glyph edges and gaps)."""
    hi, lo = max(px), min(px)
    if hi == lo:
        return bytes((hi, hi)) + bytes(6)
    best = _bc4_try(px, hi, lo) + (hi, lo)
    if best[0]:
        mid = [v for v in px if 0 < v < 255]
        if mid and (lo == 0 or hi == 255):  # the 6-value mode has 0 and 255 for free
            a, b = min(mid), max(mid)
            got = _bc4_try(px, a, b) + (a, b)
            if got[0] < best[0]:
                best = got
    _, bits, e0, e1 = best
    return bytes((e0, e1)) + bits.to_bytes(6, "little")


# ================================================================================================= BFFNT

FMT_R8, FMT_BC4 = 8, 12      # TGLP sheet formats seen in the game's fonts
BNTX_FMT = {FMT_R8: 0x0201, FMT_BC4: 0x1D01}


class Glyph(NamedTuple):
    left: int     # where the cell's column 0 is drawn, from the pen (signed)
    width: int    # columns of the cell that are drawn
    advance: int  # how far the pen moves


class Bffnt:
    """A Switch bitmap font (FFNT 4.x): its metrics, its character map and its sheets (one BNTX texture whose layers
    are the sheets, inside TGLP). Edits keep the sheets in place; only the widths (CWDH) and character maps (CMAP),
    which come after the sheets, are rewritten."""

    def __init__(self, data: bytes, name: str = "font"):
        self.name = name
        d = self.d = bytearray(data)
        if len(d) < 0x14 or d[:4] != b"FFNT":
            raise FontError(f"{name}: not a bitmap font (FFNT)")
        if d[4:6] != b"\xff\xfe":
            raise FontError(f"{name}: a big-endian (Wii U) font; only Switch fonts are edited")
        if d[11] != 4:
            raise FontError(f"{name}: font version {_u32(d, 8):#x}; only version 4 is edited")
        self.secs: list[tuple[str, int, int]] = []
        p = _u16(d, 6)
        end = min(len(d), _u32(d, 0x0C))
        while p + 8 <= end:
            magic, size = d[p:p + 4].decode("latin1"), _u32(d, p + 4)
            if size < 8 or p + size > len(d):
                raise FontError(f"{name}: damaged section {magic!r}")
            self.secs.append((magic, p, size))
            p += size
        kinds = [m for m, _, _ in self.secs]
        if kinds[:2] != ["FINF", "TGLP"] or "CWDH" not in kinds or "CMAP" not in kinds:
            raise FontError(f"{name}: unexpected sections {kinds[:8]}")
        self.other = sorted({k for k in kinds if k not in ("FINF", "TGLP", "CWDH", "CMAP")})
        f = self.secs[0][1]
        self.finf = f
        (self.ftype, self.height, self.fwidth, self.ascent, self.linefeed, self.alt, dl, dw, da,
         self.encoding) = struct.unpack_from("<BBBBHHbBBB", d, f + 8)
        self.default = Glyph(dl, dw, da)
        t = self.secs[1][1]
        self.tglp_end = t + self.secs[1][2]
        (self.cw, self.ch, self.nsheets, self.maxw, _, self.baseline, self.fmt, self.cols, self.rows,
         self.sw, self.sh, img) = struct.unpack_from("<BBBBIHHHHHHI", d, t + 8)
        if self.fmt not in BNTX_FMT:
            raise FontError(f"{name}: sheet format {self.fmt} isn't one this tool edits (BC4 or R8)")
        if not (self.cols and self.rows and self.nsheets) or (self.cw + 1) * self.cols > self.sw \
                or (self.ch + 1) * self.rows > self.sh:
            raise FontError(f"{name}: its cell grid doesn't fit its sheets")
        self._bntx(img)
        self.widths: dict[int, Glyph] = {}
        for m, o, s in self.secs:
            if m == "CWDH":
                a, b = _u16(d, o + 8), _u16(d, o + 10)
                _need(d, o + 16, 3 * (b - a + 1), f"{name}: CWDH")
                for i in range(a, b + 1):
                    self.widths[i] = Glyph(*struct.unpack_from("<bBB", d, o + 16 + 3 * (i - a)))
        self.blocks: list[dict] = []
        for m, o, s in self.secs:
            if m != "CMAP":
                continue
            first, last, method = struct.unpack_from("<IIH", d, o + 8)
            q = o + 24
            blk = {"first": first, "last": last, "method": method}
            if method == 0:
                blk["index"] = _u16(d, q)
            elif method == 1:
                _need(d, q, 2 * (last - first + 1), f"{name}: CMAP")
                blk["table"] = list(struct.unpack_from(f"<{last - first + 1}H", d, q))
            elif method == 2:
                n = _u32(d, q)
                _need(d, q + 4, 8 * n, f"{name}: CMAP")
                blk["pairs"] = [struct.unpack_from("<II", d, q + 4 + 8 * i) for i in range(n)]
            else:
                raise FontError(f"{name}: character map method {method} isn't known")
            self.blocks.append(blk)
        self.new_codes: dict[int, int] = {}
        self._widths_dirty = False
        self._lin: dict[int, bytearray] = {}
        self._dirty: set[int] = set()

    # ------------------------------------------------------------------------------------- sheets
    def _bntx(self, b: int) -> None:
        d, n = self.d, self.name
        _need(d, b, 0x40, f"{n}: sheet texture")
        if d[b:b + 4] != b"BNTX":
            raise FontError(f"{n}: the sheets aren't a BNTX texture")
        o = b + _u64(d, b + _u64(d, b + 0x28))
        _need(d, o, 0x80, f"{n}: sheet texture info")
        if d[o:o + 4] != b"BRTI":
            raise FontError(f"{n}: the sheet texture has no BRTI block")
        dim, tile, mips = d[o + 0x11], _u16(d, o + 0x12), _u16(d, o + 0x16)
        fmt = _u32(d, o + 0x1C)
        w, h = struct.unpack_from("<ii", d, o + 0x24)
        arr, lay = _u32(d, o + 0x30), _u32(d, o + 0x34)
        image_size = _u32(d, o + 0x50)
        if (dim, tile, mips) != (2, 0, 1) or (w, h, arr) != (self.sw, self.sh, self.nsheets) \
                or fmt != BNTX_FMT[self.fmt]:
            raise FontError(f"{n}: its sheet texture isn't laid out as this tool expects "
                            f"({w}x{h}x{arr}, format {fmt:#x}, {mips} mips)")
        self.data_at = b + _u64(d, b + _u64(d, o + 0x70))
        self.bh = 1 << (lay & 7)
        if self.fmt == FMT_BC4:
            if self.sw % 4 or self.sh % 4:
                raise FontError(f"{n}: BC4 sheets must be a multiple of 4 pixels")
            self.wb, self.brows = self.sw // 4 * 8, self.sh // 4
        else:
            self.wb, self.brows = self.sw, self.sh
        self.stride = image_size // arr
        if tegra.level_size(self.wb, self.brows, self.bh) > self.stride:
            raise FontError(f"{n}: its sheets are smaller than their size says")
        _need(d, self.data_at, self.stride * arr, f"{n}: sheets")

    def _layer(self, s: int) -> bytearray:
        if s not in self._lin:
            at = self.data_at + s * self.stride
            size = tegra.level_size(self.wb, self.brows, self.bh)
            self._lin[s] = bytearray(tegra.untile(bytes(self.d[at:at + size]), self.wb, self.brows, self.bh))
        return self._lin[s]

    def cell_of(self, gi: int) -> tuple[int, int, int]:
        """(sheet, x, y) of a glyph's cell, y counted from the top as the glyph is drawn (sheets are stored bottom
        row first)."""
        per = self.cols * self.rows
        s, k = divmod(gi, per)
        r, c = divmod(k, self.cols)
        return s, (self.cw + 1) * c + 1, (self.ch + 1) * r + 1

    @property
    def cells(self) -> int:
        return self.cols * self.rows * self.nsheets

    def read_cell(self, gi: int) -> list[list[int]]:
        """The glyph's cell (ch rows of cw texel values), top row first."""
        s, x0, y0 = self.cell_of(gi)
        lin = self._layer(s)
        rows = []
        if self.fmt == FMT_R8:
            for y in range(y0, y0 + self.ch):
                at = (self.sh - 1 - y) * self.sw
                rows.append(list(lin[at + x0:at + x0 + self.cw]))
            return rows
        cache: dict[tuple[int, int], list[int]] = {}
        bw = self.sw // 4
        for y in range(y0, y0 + self.ch):
            ry = self.sh - 1 - y
            row = []
            for x in range(x0, x0 + self.cw):
                key = (x >> 2, ry >> 2)
                blk = cache.get(key)
                if blk is None:
                    at = (key[1] * bw + key[0]) * 8
                    blk = cache[key] = bc4_decode(lin[at:at + 8])
                row.append(blk[(ry & 3) * 4 + (x & 3)])
            rows.append(row)
        return rows

    def _blocks_of(self, gi: int) -> set[tuple[int, int, int]]:
        s, x0, y0 = self.cell_of(gi)
        r0, r1 = self.sh - (y0 + self.ch), self.sh - 1 - y0  # raw rows the cell covers
        return {(s, bx, by) for bx in range(x0 >> 2, ((x0 + self.cw - 1) >> 2) + 1)
                for by in range(r0 >> 2, (r1 >> 2) + 1)}

    def cell_is_free(self, gi: int, taken: set[int]) -> bool:
        """True when a cell no glyph uses has no ink, and the 4x4 blocks it shares hold no ink of a glyph that stays
        (so rewriting those blocks can't change one texel of another glyph)."""
        if gi in self.widths or gi >= self.cells:
            return False
        if self.fmt == FMT_R8:
            return not any(any(r) for r in self.read_cell(gi))
        mine = {}
        for g in taken | {gi}:
            s, x0, y0 = self.cell_of(g)
            mine[g] = (s, x0, self.sh - (y0 + self.ch), x0 + self.cw - 1, self.sh - 1 - y0)
        bw = self.sw // 4
        for s, bx, by in self._blocks_of(gi):
            lin = self._layer(s)
            at = (by * bw + bx) * 8
            vals = bc4_decode(lin[at:at + 8])
            for t, v in enumerate(vals):
                if not v:
                    continue
                x, ry = bx * 4 + (t & 3), by * 4 + (t >> 2)
                if not any(m[0] == s and m[1] <= x <= m[3] and m[2] <= ry <= m[4] for m in mine.values()):
                    return False
        return True

    def write_cells(self, cells: dict[int, list[list[int]]]) -> None:
        """Draw glyph cells (ch rows of cw values each, top row first). Only the 4x4 blocks they touch are written."""
        if self.fmt == FMT_R8:
            for gi, rows in cells.items():
                s, x0, y0 = self.cell_of(gi)
                lin = self._layer(s)
                for dy, row in enumerate(rows):
                    at = (self.sh - 1 - (y0 + dy)) * self.sw + x0
                    lin[at:at + self.cw] = bytes(row)
                self._dirty.add(s)
            return
        per_block: dict[tuple[int, int, int], dict[int, int]] = {}
        for gi, rows in cells.items():
            s, x0, y0 = self.cell_of(gi)
            for dy, row in enumerate(rows):
                ry = self.sh - 1 - (y0 + dy)
                for dx, v in enumerate(row):
                    x = x0 + dx
                    per_block.setdefault((s, x >> 2, ry >> 2), {})[(ry & 3) * 4 + (x & 3)] = v
        bw = self.sw // 4
        for (s, bx, by), texels in per_block.items():
            lin = self._layer(s)
            at = (by * bw + bx) * 8
            vals = bc4_decode(lin[at:at + 8]) if len(texels) < 16 else [0] * 16
            for t, v in texels.items():
                vals[t] = v
            new = bc4_encode(vals)
            if new != lin[at:at + 8]:
                lin[at:at + 8] = new
                self._dirty.add(s)

    # ------------------------------------------------------------------------------------- characters
    def codes(self) -> dict[int, int]:
        """Character code -> glyph index, as the game looks it up: the first block whose range holds the code
        decides."""
        out: dict[int, int] = {}
        seen: list[tuple[int, int]] = []

        def claimed(c: int) -> bool:
            return any(a <= c <= b for a, b in seen)
        for blk in self.blocks:
            a, b = blk["first"], blk["last"]
            if blk["method"] == 0:
                for c in range(a, b + 1):
                    if not claimed(c):
                        out[c] = blk["index"] + c - a
            elif blk["method"] == 1:
                for c, gi in enumerate(blk["table"], a):
                    if gi != 0xFFFF and not claimed(c):
                        out[c] = gi
            else:
                for c, gi in blk["pairs"]:
                    if a <= c <= b and not claimed(c):
                        out[c] = gi
            seen.append((a, b))
        for c, gi in self.new_codes.items():
            out[c] = gi
        return out

    def lacks(self, chars: str = ASCII) -> str:
        have = self.codes()
        return "".join(c for c in chars if ord(c) not in have)

    def map_code(self, code: int, gi: int) -> None:
        """Map a code the font lacks to a glyph (written by to_bytes, into the first block whose range holds it)."""
        self.new_codes[code] = gi

    def set_glyph(self, gi: int, g: Glyph) -> None:
        g = Glyph(max(-128, min(127, g.left)), max(0, min(255, g.width)), max(0, min(255, g.advance)))
        if self.widths.get(gi) != g:
            self.widths[gi] = g
            self._widths_dirty = True

    def to_bytes(self) -> bytes:
        for s in sorted(self._dirty):
            at = self.data_at + s * self.stride
            tiled = tegra.tile(bytes(self._lin[s]), self.wb, self.brows, self.bh)
            self.d[at:at + len(tiled)] = tiled
        self._dirty.clear()
        if not self.new_codes and not self._widths_dirty:
            return bytes(self.d)
        if self.other:
            raise FontError(f"{self.name}: it has {', '.join(self.other)} data this tool doesn't rewrite")
        return self._rebuild_tail()

    def _rebuild_tail(self) -> bytes:
        out = bytearray(self.d[:self.tglp_end])
        lo, hi = min(self.widths), max(self.widths)
        cw = bytearray(b"CWDH" + bytes(4) + struct.pack("<HHI", lo, hi, 0))
        for i in range(lo, hi + 1):
            cw += struct.pack("<bBB", *self.widths.get(i, self.default))
        cw += bytes(-len(cw) % 4)
        struct.pack_into("<I", cw, 4, len(cw))
        cwdh_at = len(out)
        out += cw
        blocks = [dict(b, table=list(b["table"])) if "table" in b else
                  dict(b, pairs=list(b["pairs"])) if "pairs" in b else dict(b) for b in self.blocks]
        loose: dict[int, int] = {}
        for c, gi in sorted(self.new_codes.items()):
            blk = next((b for b in blocks if b["first"] <= c <= b["last"]), None)
            if blk is None:
                loose[c] = gi
            elif blk["method"] == 1:
                if blk["table"][c - blk["first"]] != 0xFFFF:
                    raise FontError(f"{self.name}: U+{c:04X} is already mapped")
                blk["table"][c - blk["first"]] = gi
            elif blk["method"] == 2:
                if any(x == c for x, _ in blk["pairs"]):
                    raise FontError(f"{self.name}: U+{c:04X} is already mapped")
                blk["pairs"] = sorted(blk["pairs"] + [(c, gi)])
            else:
                raise FontError(f"{self.name}: U+{c:04X} is already mapped")
        if loose:
            blocks.append({"first": min(loose), "last": max(loose), "method": 2, "pairs": sorted(loose.items())})
        starts = []
        for b in blocks:
            sec = bytearray(b"CMAP" + bytes(4) + struct.pack("<IIHHI", b["first"], b["last"], b["method"], 0, 0))
            if b["method"] == 0:
                sec += struct.pack("<H", b["index"])
            elif b["method"] == 1:
                sec += struct.pack(f"<{len(b['table'])}H", *b["table"])
            else:
                sec += struct.pack("<I", len(b["pairs"]))
                for c, gi in b["pairs"]:
                    sec += struct.pack("<II", c, gi)
            sec += bytes(-len(sec) % 4)
            struct.pack_into("<I", sec, 4, len(sec))
            starts.append(len(out))
            out += sec
        for i, st in enumerate(starts):  # each block points at the next one's data; the last at nothing
            struct.pack_into("<I", out, st + 20, starts[i + 1] + 8 if i + 1 < len(starts) else 0)
        struct.pack_into("<I", out, self.finf + 0x18, cwdh_at + 8)
        struct.pack_into("<I", out, self.finf + 0x1C, starts[0] + 8)
        struct.pack_into("<I", out, 0x0C, len(out))
        struct.pack_into("<H", out, 0x10, 3 + len(starts))
        return bytes(out)

    # ------------------------------------------------------------------------------------- the look
    @property
    def outlined(self) -> bool:
        """NormalS_00's style: 0 outside, 128 on a dark ring, 255 inside (R8 sheets)."""
        return self.fmt == FMT_R8

    def fill(self, v: int) -> float:
        """How much of the letter itself (not its ring) a texel holds."""
        return max(0.0, (v - 128) / 127) if self.outlined else v / 255

    def measure(self) -> dict[str, float]:
        """Cap height ('H') and stem width ('l', else 'I'), in pixels of the letter's fill."""
        have = self.codes()
        out = {}
        if ord("H") in have:
            rows = self.read_cell(have[ord("H")])
            out["cap"] = max(sum(self.fill(r[x]) for r in rows) for x in range(self.cw))
        for c in "lI":
            if ord(c) in have:
                rows = self.read_cell(have[ord(c)])
                inked = [i for i, r in enumerate(rows) if any(self.fill(v) > 0.5 for v in r)]
                if inked:
                    mid = rows[(inked[0] + inked[-1]) // 2]
                    out["stem"] = sum(self.fill(v) for v in mid)
                break
        return out


# ================================================================================================= TrueType

class TrueType:
    """A TrueType (glyf) font: character map, advances and outlines. CFF (.otf) and collections are refused."""

    def __init__(self, data: bytes, name: str = "font"):
        self.name = name
        self.data = d = bytes(data)
        if d[:4] == b"ttcf":
            raise FontError(f"{name} is a font collection (.ttc): pick one font of it as a .ttf")
        if d[:4] == b"OTTO":
            raise FontError(f"{name} has CFF outlines (.otf): this tool reads TrueType outlines; use the font's .ttf "
                            "version")
        if d[:4] not in (b"\x00\x01\x00\x00", b"true") or len(d) < 12:
            raise FontError(f"{name} isn't a TrueType font")
        n = struct.unpack_from(">H", d, 4)[0]
        _need(d, 12, 16 * n, f"{name}: table list")
        self.tables = {d[12 + 16 * i:16 + 16 * i].decode("latin1"): struct.unpack_from(">II", d, 20 + 16 * i)
                       for i in range(n)}
        for t in ("head", "hhea", "hmtx", "maxp", "cmap", "loca", "glyf"):
            if t not in self.tables:
                raise FontError(f"{name}: no '{t}' table (not a TrueType outline font)")
            o, ln = self.tables[t]
            _need(d, o, ln, f"{name}: '{t}' table")
        head = self.tables["head"][0]
        self.upem = struct.unpack_from(">H", d, head + 18)[0] or 1000
        self.long_loca = struct.unpack_from(">h", d, head + 50)[0] == 1
        hhea = self.tables["hhea"][0]
        self.ascender, self.descender = struct.unpack_from(">hh", d, hhea + 4)
        self.n_hmetrics = struct.unpack_from(">H", d, hhea + 34)[0]
        self.n_glyphs = struct.unpack_from(">H", d, self.tables["maxp"][0] + 4)[0]
        self.cmap = self._cmap()

    def _cmap(self) -> dict[int, int]:
        d = self.data
        base = self.tables["cmap"][0]
        n = struct.unpack_from(">H", d, base + 2)[0]
        subs = {}
        for i in range(n):
            pid, eid, off = struct.unpack_from(">HHI", d, base + 4 + 8 * i)
            subs[(pid, eid)] = base + off
        for key in ((3, 10), (0, 6), (0, 4), (3, 1), (0, 3), (0, 2), (0, 1), (0, 0)):
            if key not in subs:
                continue
            o = subs[key]
            fmt = struct.unpack_from(">H", d, o)[0]
            if fmt == 12:
                groups = struct.unpack_from(">I", d, o + 12)[0]
                out = {}
                for g in range(min(groups, 100000)):
                    a, b, gid = struct.unpack_from(">III", d, o + 16 + 12 * g)
                    for c in range(a, min(b, 0x10FFFF, a + 0x30000) + 1):
                        out[c] = gid + c - a
                return out
            if fmt == 4:
                seg2 = struct.unpack_from(">H", d, o + 6)[0]
                ends, starts = o + 14, o + 16 + seg2
                deltas, ranges = starts + seg2, starts + 2 * seg2
                out = {}
                for s in range(seg2 // 2):
                    end = struct.unpack_from(">H", d, ends + 2 * s)[0]
                    start = struct.unpack_from(">H", d, starts + 2 * s)[0]
                    delta = struct.unpack_from(">h", d, deltas + 2 * s)[0]
                    ro = struct.unpack_from(">H", d, ranges + 2 * s)[0]
                    for c in range(start, end + 1):
                        if c == 0xFFFF:
                            continue
                        if ro == 0:
                            g = (c + delta) & 0xFFFF
                        else:
                            at = ranges + 2 * s + ro + 2 * (c - start)
                            if at + 2 > len(d):
                                continue
                            g = struct.unpack_from(">H", d, at)[0]
                            g = (g + delta) & 0xFFFF if g else 0
                        if g:
                            out[c] = g
                return out
        raise FontError(f"{self.name}: no Unicode character map this tool reads")

    def has(self, code: int) -> bool:
        return self.cmap.get(code, 0) != 0

    def advance(self, gid: int) -> int:
        h = self.tables["hmtx"][0]
        i = min(gid, self.n_hmetrics - 1)
        return struct.unpack_from(">H", self.data, h + 4 * i)[0]

    def _glyf_range(self, gid: int) -> tuple[int, int]:
        loca = self.tables["loca"][0]
        if gid + 1 >= self.n_glyphs + 1:
            return 0, 0
        if self.long_loca:
            a, b = struct.unpack_from(">II", self.data, loca + 4 * gid)
        else:
            a, b = (2 * x for x in struct.unpack_from(">HH", self.data, loca + 2 * gid))
        return a, b

    def contours(self, gid: int, depth: int = 0) -> list[list[tuple[float, float, bool]]]:
        """The glyph's contours in font units (y up): points (x, y, on the curve)."""
        if depth > 8 or not 0 <= gid < self.n_glyphs:
            return []
        a, b = self._glyf_range(gid)
        if b <= a:
            return []
        d = self.data
        o = self.tables["glyf"][0] + a
        nc = struct.unpack_from(">h", d, o)[0]
        p = o + 10
        if nc >= 0:
            ends = struct.unpack_from(f">{nc}H", d, p)
            p += 2 * nc
            p += 2 + struct.unpack_from(">H", d, p)[0]
            npts = ends[-1] + 1 if nc else 0
            flags = []
            while len(flags) < npts:
                f = d[p]
                p += 1
                flags.append(f)
                if f & 8:
                    flags += [f] * d[p]
                    p += 1
            xs, ys = [], []
            for out, short, same in ((xs, 2, 16), (ys, 4, 32)):
                v = 0
                for f in flags[:npts]:
                    if f & short:
                        dv = d[p]
                        p += 1
                        v += dv if f & same else -dv
                    elif not f & same:
                        v += struct.unpack_from(">h", d, p)[0]
                        p += 2
                    out.append(v)
            pts = [(float(x), float(y), bool(f & 1)) for x, y, f in zip(xs, ys, flags)]
            res, s = [], 0
            for e in ends:
                res.append(pts[s:e + 1])
                s = e + 1
            return res
        res = []
        while True:
            flags, sub = struct.unpack_from(">HH", d, p)
            p += 4
            if flags & 1:
                a1, a2 = struct.unpack_from(">hh" if flags & 2 else ">HH", d, p)
                p += 4
            else:
                a1, a2 = struct.unpack_from(">bb" if flags & 2 else ">BB", d, p)
                p += 2
            xx, xy, yx, yy = 1.0, 0.0, 0.0, 1.0
            if flags & 8:
                xx = yy = struct.unpack_from(">h", d, p)[0] / 16384
                p += 2
            elif flags & 0x40:
                xx, yy = (v / 16384 for v in struct.unpack_from(">hh", d, p))
                p += 4
            elif flags & 0x80:
                xx, xy, yx, yy = (v / 16384 for v in struct.unpack_from(">hhhh", d, p))
                p += 8
            dx, dy = (a1, a2) if flags & 2 else (0, 0)
            for c in self.contours(sub, depth + 1):
                res.append([(xx * x + yx * y + dx, xy * x + yy * y + dy, on) for x, y, on in c])
            if not flags & 0x20:
                break
        return res

    def cap_height(self) -> float:
        """'H' height in font units (from its outline)."""
        gid = self.cmap.get(ord("H"), 0)
        ys = [y for c in self.contours(gid) for _, y, _ in c]
        return float(max(ys)) if ys else self.upem * 0.7

    def names(self) -> dict[int, str]:
        if "name" not in self.tables:
            return {}
        d = self.data
        off = self.tables["name"][0]
        cnt, so = struct.unpack_from(">HH", d, off + 2)
        out: dict[int, str] = {}
        for i in range(cnt):
            pid, _, _, nid, ln, o = struct.unpack_from(">6H", d, off + 6 + 12 * i)
            if nid in (0, 1, 2, 4, 13, 14) and nid not in out:
                raw = d[off + so + o:off + so + o + ln]
                out[nid] = raw.decode("utf-16-be", "replace") if pid in (0, 3) else raw.decode("latin1")
        return out

    def fstype(self) -> int | None:
        if "OS/2" not in self.tables:
            return None
        return struct.unpack_from(">H", self.data, self.tables["OS/2"][0] + 8)[0]


def load_truetype(path: str) -> TrueType:
    try:
        if os.path.getsize(path) > MAX_FONT_FILE:
            raise FontError(f"{path} is bigger than {MAX_FONT_FILE // 2**20} MB")
        with open(path, "rb") as f:
            return TrueType(f.read(), os.path.basename(path))
    except OSError as e:
        raise FontError(f"can't read {path}: {e.strerror or e}") from None


FREE_WORDS = ("open font license", "scripts.sil.org/ofl", "openfontlicense", "apache license", "ubuntu font licence",
              "public domain", "cc0", "font exception", "bitstream vera", "mit license")
NOT_FREE_WORDS = ("microsoft supplied font", "any other use is prohibited", "all rights reserved")


def license_info(tt: TrueType) -> dict:
    """The font's own words about its licence (name IDs 0, 13, 14) and embedding flags. A warning, never a block."""
    names = tt.names()
    fst = tt.fstype()
    # (the start of the licence field and its address, else the copyright line: further down, a licence text can
    # name another licence for a part, as Arial's does for its Hebrew layout code)
    lic = names.get(13, "")
    head = (lic[:200] + " " + names.get(14, "")).lower() if lic or names.get(14) else names.get(0, "").lower()
    free = any(w in head for w in FREE_WORDS) and not any(w in lic.lower() for w in NOT_FREE_WORDS)
    warn = []
    if not free:
        warn.append("its licence isn't one this tool knows as free: use it on your own PC, and don't share game "
                    "files built with it")
    if fst is not None and fst & 0x2:
        warn.append("its maker marked it 'restricted': it may not be put into other files")
    return {"family": names.get(1, ""), "style": names.get(2, ""), "copyright": names.get(0, "")[:160],
            "license": names.get(13, "")[:200], "url": names.get(14, ""), "fsType": fst, "free": free,
            "warnings": warn}


# ================================================================================================= drawing

def _flatten(contour: list[tuple[float, float, bool]], s: float) -> list[tuple[float, float]]:
    """A contour (font units) as a polygon in pixels (y up): quadratic curves cut into short lines."""
    pts = [(x * s, y * s, on) for x, y, on in contour]
    if len(pts) < 2:
        return []
    start = next((i for i, p in enumerate(pts) if p[2]), None)
    if start is None:
        a, b = pts[0], pts[1]
        seq = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, True)] + pts[1:] + [pts[0]]
    else:
        seq = pts[start:] + pts[:start]
    out = [(seq[0][0], seq[0][1])]
    prev = seq[0]
    ctrl = None

    def quad(p0, c, p2):
        dev = math.hypot(p0[0] - 2 * c[0] + p2[0], p0[1] - 2 * c[1] + p2[1])
        n = max(1, min(16, int(math.sqrt(dev * 3)) + 1))
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            out.append((u * u * p0[0] + 2 * u * t * c[0] + t * t * p2[0],
                        u * u * p0[1] + 2 * u * t * c[1] + t * t * p2[1]))
    for p in seq[1:] + [seq[0]]:
        if p[2]:
            if ctrl is None:
                out.append((p[0], p[1]))
            else:
                quad(prev, ctrl, p)
                ctrl = None
            prev = p
        elif ctrl is None:
            ctrl = p
        else:
            mid = ((ctrl[0] + p[0]) / 2, (ctrl[1] + p[1]) / 2, True)
            quad(prev, ctrl, mid)
            prev, ctrl = mid, p
    out.pop()
    clean = []
    for p in out:
        if not clean or abs(p[0] - clean[-1][0]) > 1e-7 or abs(p[1] - clean[-1][1]) > 1e-7:
            clean.append(p)
    while len(clean) > 1 and abs(clean[0][0] - clean[-1][0]) < 1e-7 and abs(clean[0][1] - clean[-1][1]) < 1e-7:
        clean.pop()
    return clean if len(clean) >= 3 else []


def _area(poly: list[tuple[float, float]]) -> float:
    return sum(poly[i - 1][0] * p[1] - p[0] * poly[i - 1][1] for i, p in enumerate(poly)) / 2


def offset_polys(polys: list[list[tuple[float, float]]], dist: float) -> list[list[tuple[float, float]]]:
    """Every edge moved `dist` pixels away from the ink (negative: into it): bolder or thinner strokes."""
    if not dist:
        return polys
    sign = 1.0 if sum(_area(p) for p in polys) < 0 else -1.0  # TrueType: outer contours run clockwise (y up)
    out = []
    for poly in polys:
        n = len(poly)
        res = []
        for i in range(n):
            x0, y0 = poly[i - 1]
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            la = math.hypot(x1 - x0, y1 - y0) or 1e-9
            lb = math.hypot(x2 - x1, y2 - y1) or 1e-9
            n1 = (-(y1 - y0) / la * sign, (x1 - x0) / la * sign)
            n2 = (-(y2 - y1) / lb * sign, (x2 - x1) / lb * sign)
            den = max(1 + n1[0] * n2[0] + n1[1] * n2[1], 0.25)
            res.append((x1 + dist * (n1[0] + n2[0]) / den, y1 + dist * (n1[1] + n2[1]) / den))
        out.append(res)
    return out


def raster(polys: list[list[tuple[float, float]]], w: int, h: int) -> list[list[float]]:
    """Coverage (0..1) of polygons given in cell pixels (y down), h rows of w: exact area per pixel, non-zero
    filling (overlapping strokes add up to 1)."""
    acc = [[0.0] * (w + 2) for _ in range(h)]
    for poly in polys:
        n = len(poly)
        for i in range(n):
            x0, y0 = poly[i - 1]
            x1, y1 = poly[i]
            if y0 == y1:
                continue
            if y0 < y1:
                dirn = 1.0
            else:
                dirn = -1.0
                x0, y0, x1, y1 = x1, y1, x0, y0
            dxdy = (x1 - x0) / (y1 - y0)
            ys, ye = max(y0, 0.0), min(y1, float(h))
            if ys >= ye:
                continue
            x = x0 + dxdy * (ys - y0)
            row = int(ys)
            while row < h and row < ye:
                top, bot = max(float(row), ys), min(row + 1.0, ye)
                dy = bot - top
                if dy <= 0:
                    row += 1
                    continue
                xn = x + dxdy * dy
                dd = dy * dirn
                xa, xb = (x, xn) if x < xn else (xn, x)
                xa = min(max(xa, 0.0), float(w))
                xb = min(max(xb, 0.0), float(w))
                r = acc[row]
                ia = int(xa)
                if xb - ia <= 1.0:
                    xm = 0.5 * (xa + xb) - ia
                    r[ia] += dd * (1 - xm)
                    r[ia + 1] += dd * xm
                else:
                    sl = 1.0 / (xb - xa)
                    xaf = xa - ia
                    a0 = 0.5 * sl * (1 - xaf) ** 2
                    ib = int(math.ceil(xb))
                    xbf = xb - ib + 1
                    am = 0.5 * sl * xbf * xbf
                    r[ia] += dd * a0
                    if ib == ia + 2:
                        r[ia + 1] += dd * (1 - a0 - am)
                    else:
                        a1 = sl * (1.5 - xaf)
                        r[ia + 1] += dd * (a1 - a0)
                        for k in range(ia + 2, ib - 1):
                            r[k] += dd * sl
                        a2 = a1 + (ib - ia - 3) * sl
                        r[ib - 1] += dd * (1 - a2 - am)
                    r[ib] += dd * am
                x = xn
                row += 1
    out = []
    for r in acc:
        s = 0.0
        line = []
        for k in range(w):
            s += r[k]
            v = abs(s)
            line.append(1.0 if v > 1 else v)
        out.append(line)
    return out


class Style(NamedTuple):
    """How one TrueType font is drawn into one bitmap font."""
    px: float        # pixels per em
    thin: float      # pixels taken off each side of every stroke (negative: added)
    ring: float      # the outlined style's ring width (0 for plain fonts)


def style_for(bf: Bffnt, tt: TrueType, match_stems: bool) -> Style:
    """The size that gives the TrueType font's 'H' the bitmap font's cap height; with match_stems, also the
    thinning that gives its 'l' the bitmap font's stem width."""
    m = bf.measure()
    cap = m.get("cap") or bf.baseline * 0.75
    px = cap * tt.upem / tt.cap_height()
    thin = 0.0
    if match_stems and m.get("stem"):
        gid = tt.cmap.get(ord("l")) or tt.cmap.get(ord("I"), 0)
        polys = [_flatten(c, px / tt.upem) for c in tt.contours(gid)]
        polys = [p for p in polys if p]
        if polys:
            xs = [x for p in polys for x, _ in p]
            ys = [y for p in polys for _, y in p]
            h = int(max(ys) - min(ys)) + 3
            cov = raster([[(x - min(xs) + 1, max(ys) + 1 - y) for x, y in p] for p in polys],
                         int(max(xs) - min(xs)) + 3, h)
            stem = sum(cov[h // 2])
            thin = max(-1.0, min((stem - m["stem"]) / 2, stem / 2 - 0.4))
    return Style(px, thin, OUTLINE_PX if bf.outlined else 0.0)


class Drawn(NamedTuple):
    rows: list[list[int]]
    glyph: Glyph
    clipped: bool    # ink fell outside the cell's rows
    squeezed: bool   # narrowed, or its accent or descender squashed, to fit the cell


def draw(bf: Bffnt, tt: TrueType, code: int, st: Style, keep: Glyph | None = None) -> Drawn:
    """One character of the TrueType font as a cell of the bitmap font: ink starts at column 0 (as in the game's
    own fonts), the baseline is the font's, and a glyph wider than the cell is narrowed to fit."""
    gid = tt.cmap.get(code, 0)
    if not gid:
        raise FontError(f"{tt.name} has no U+{code:04X}")
    s = st.px / tt.upem
    adv = tt.advance(gid) * s
    polys = [p for p in (_flatten(c, s) for c in tt.contours(gid)) if p]
    if st.thin:
        polys = offset_polys(polys, -st.thin)
    empty = [[0] * bf.cw for _ in range(bf.ch)]
    if not polys:  # a space: only its advance (the game's own spaces put it in `left` too)
        left = keep.left if keep is not None and keep.width == 0 else round(adv)
        return Drawn(empty, Glyph(left, 0, round(adv)), False, False)
    # Accents above the cell's top and descenders below its bottom: squash only that part (above the cap height,
    # below the baseline) so the letter's body keeps its size.
    ys = [y for p in polys for _, y in p]
    cap = tt.cap_height() * s
    fitted = False
    top_room, low_room = bf.baseline - st.ring, bf.ch - bf.baseline - st.ring
    if max(ys) > top_room + 0.3 and cap < top_room - 1:
        k = (top_room - cap) / (max(ys) - cap)
        polys = [[(x, cap + (y - cap) * k if y > cap else y) for x, y in p] for p in polys]
        fitted = True
    if -min(ys) > low_room + 0.3 and low_room > 1:
        k = low_room / -min(ys)
        polys = [[(x, y * k if y < 0 else y) for x, y in p] for p in polys]
        fitted = True
    xs = [x for p in polys for x, _ in p]
    x0, x1 = min(xs) - st.ring, max(xs) + st.ring
    squeezed = False
    if x1 - x0 > bf.cw:
        k = (bf.cw - 2 * st.ring - 0.02) / max(x1 - x0 - 2 * st.ring, 1e-6)
        base = x0 + st.ring
        polys = [[(base + (x - base) * k, y) for x, y in p] for p in polys]
        adv *= k
        x1 = base + (x1 - st.ring - base) * k + st.ring
        squeezed = True
    shift = -x0

    def cell(ps):
        return [[(x + shift, bf.baseline - y) for x, y in p] for p in ps]
    fill = raster(cell(polys), bf.cw, bf.ch)
    if st.ring:
        ring = raster(cell(offset_polys(polys, st.ring)), bf.cw, bf.ch)
        rows = [[min(255, round(128 * o + 127 * f)) for o, f in zip(ro, rf)] for ro, rf in zip(ring, fill)]
    else:
        rows = [[min(255, round(255 * f)) for f in rf] for rf in fill]
    ys = [y for p in polys for _, y in p]
    clipped = bf.baseline - max(ys) < -0.5 or bf.baseline - min(ys) > bf.ch + 0.5
    width = min(bf.cw, max(1, math.ceil(x1 - x0 - 1e-6)))
    return Drawn(rows, Glyph(round(x0), width, max(0, round(adv))), clipped, squeezed or fitted)


# ================================================================================================= building

class Recipe(NamedTuple):
    """What to do to one archive. `font`: redraw the menu fonts with it (None keeps the game's look); `fix`: draw the
    printable ASCII characters still missing from Fira Sans Medium, thinned to the game font's stems."""
    font: TrueType | None = None
    font_key: str = "game"
    fix: TrueType | None = None
    fonts: tuple[str, ...] = MENU_FONTS

    def key(self) -> str:
        parts = {"v": BUILD_VERSION, "font": _sha(self.font.data) if self.font else None,
                 "fix": _sha(self.fix.data) if self.fix else None, "fonts": list(self.fonts)}
        return _sha(json.dumps(parts, sort_keys=True).encode())[:24]


def restyle(bf: Bffnt, tt: TrueType, report: dict) -> None:
    """Redraw every character the TrueType font has, in place (same glyph index, same cell); add the printable
    ASCII characters the bitmap font lacks and the TrueType font has, in free cells."""
    st = style_for(bf, tt, match_stems=False)
    have = bf.codes()
    by_glyph: dict[int, int] = {}
    for c, gi in sorted(have.items()):
        if tt.has(c):
            by_glyph.setdefault(gi, c)
    cells = {}
    clipped = squeezed = 0
    for gi, c in by_glyph.items():
        dr = draw(bf, tt, c, st, bf.widths.get(gi))
        cells[gi] = dr.rows
        bf.set_glyph(gi, dr.glyph)
        clipped += dr.clipped
        squeezed += dr.squeezed
    bf.write_cells(cells)
    added = add_chars(bf, tt, "".join(ch for ch in bf.lacks() if tt.has(ord(ch))), st)
    report.update(redrawn=len(cells), clipped=clipped, squeezed=squeezed, px=round(st.px, 2),
                  added=report.get("added", "") + added["chars"])


def add_chars(bf: Bffnt, tt: TrueType, chars: str, st: Style) -> dict:
    """Draw characters into free cells (never sharing a 4x4 block with a glyph that stays) and map them."""
    if not chars:
        return {"chars": "", "cells": []}
    taken: set[int] = set()
    first = gi = max(bf.widths) + 1 if bf.widths else 0
    picks = []
    while len(picks) < len(chars):
        if gi >= bf.cells:
            raise FontError(f"{bf.name}: no free cells left for {len(chars) - len(picks)} more characters")
        if bf.cell_is_free(gi, taken):
            picks.append(gi)
            taken.add(gi)
        gi += 1
    cells = {}
    for ch, gi in zip(chars, picks):
        dr = draw(bf, tt, ord(ch), st)
        cells[gi] = dr.rows
        bf.set_glyph(gi, dr.glyph)
        bf.map_code(ord(ch), gi)
    for g in range(first, max(picks)):  # cells skipped (shared blocks with ink) stay empty, zero width
        if g not in bf.widths:
            bf.set_glyph(g, Glyph(0, 0, 0))
    bf.write_cells(cells)
    return {"chars": chars, "cells": picks}


def build(arc: bytes, recipe: Recipe) -> tuple[bytes | None, dict]:
    """The edited archive (unpacked SARC bytes), or None when nothing needed a change; and a report."""
    sarc = Sarc(arc)
    report: dict = {"fonts": {}, "size_before": len(arc)}
    changes: dict[str, bytes] = {}
    full = True
    for name in sarc.names():
        stem = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if stem == NEVER or stem not in recipe.fonts or not name.lower().endswith(".bffnt"):
            continue
        data = sarc.get(name)
        bf = Bffnt(data, name)
        rep: dict = {"lacked": bf.lacks()}
        if recipe.font is not None:
            restyle(bf, recipe.font, rep)
        still = bf.lacks()
        if still and recipe.fix is not None:
            got = add_chars(bf, recipe.fix, still, style_for(bf, recipe.fix, match_stems=True))
            rep["fixed"] = got["chars"]
        rep["lacks"] = bf.lacks()
        if stem in MENU_FONTS and rep["lacks"]:
            full = False
        new = bf.to_bytes()
        if new != data:
            changes[name] = new
            rep.update(size_before=len(data), size_after=len(new))
        report["fonts"][stem] = rep
    report["symbols_full"] = full and all(f in report["fonts"] for f in MENU_FONTS)
    if not changes:
        report["size_after"] = len(arc)
        return None, report
    out = sarc.rebuild(changes)
    back = Sarc(out)
    for name in sarc.names():
        if name not in changes and back.get(name) != sarc.get(name):
            raise FontError(f"{name} changed although it wasn't edited")
        if name.rsplit("/", 1)[-1].startswith(NEVER) and back.get(name) != sarc.get(name):
            raise FontError(f"{NEVER} changed")
    report["size_after"] = len(out)
    return out, report


def size_entry(lang: str, unpacked: int) -> tuple[str, int]:
    """The size-table entry a grown Font_xx archive needs (the game's own entries fit this rule)."""
    return SIZE_KEY.format(lang), (unpacked + 31) // 32 * 32 + 8192


def build_archive(packed: bytes, recipe: Recipe, lang: str) -> tuple[bytes | None, dict]:
    """A Font_xx.sbfarc (Yaz0) edited by the recipe: (new Yaz0 bytes or None when unchanged, report). The report's
    "rstb" is a size-table entry only when the archive grew."""
    arc, align = yaz0_decode(packed) if packed[:4] == b"Yaz0" else (packed, 0)
    out, rep = build(arc, recipe)
    rep["rstb"] = None
    if out is None:
        return None, rep
    if len(out) > len(arc):
        rep["rstb"] = list(size_entry(lang, len(out)))
    return yaz0_stored(out, align), rep


# ================================================================================================= the library

def fira_path(file: str, install: str | None = None) -> str:
    """Savage Wind's own copy (next to savagewind.exe) when it is there, else the one in assets/fonts/."""
    if install:
        p = os.path.join(install, file)
        if os.path.isfile(p):
            return p
    p = os.path.join(ASSETS, file)
    if os.path.isfile(p):
        return p
    raise FontError(f"{file} isn't in Savage Wind's folder or in {ASSETS}")


def _root(lib) -> str:
    return lib if isinstance(lib, str) else lib.root


def load_settings(lib) -> dict:
    """{"choice": "auto"|"game"|"easy-read"|"mod:<id>", "fix_symbols": bool}; defaults when the file is missing."""
    out = {"choice": "auto", "fix_symbols": FIX_SYMBOLS_DEFAULT}
    try:
        with open(os.path.join(_root(lib), SETTINGS), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return out
    if isinstance(data, dict) and data.get("tool") == TOOL_ID:
        c = data.get("choice")
        if isinstance(c, str) and (c in CHOICES or c.startswith("mod:")):
            out["choice"] = c
        if isinstance(data.get("fix_symbols"), bool):
            out["fix_symbols"] = data["fix_symbols"]
    return out


def save_settings(lib, **changes) -> dict:
    """Change and write font.json (a temporary file, then renamed). Profiles can save and restore this dict."""
    cur = load_settings(lib)
    for k, v in changes.items():
        if k not in cur:
            raise FontError(f"unknown font setting {k}")
        cur[k] = v
    root = _root(lib)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, SETTINGS)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"tool": TOOL_ID, "format": FORMAT, **cur}, f, indent=1)
    os.replace(tmp, path)
    return cur


class FontMod(NamedTuple):
    id: str
    name: str
    enabled: bool
    file: str | None          # the font file on disk (None when the table is wrong)
    languages: tuple[str, ...]
    problems: list[str]
    notes: list[str]


def check_font_table(t, files: set[str] | None = None, base_rel: str = "") -> tuple[dict, list[str], list[str]]:
    """A mod.toml [font] table checked: (clean values, problems, notes). `files`: the mod's files ("/"-separated,
    from the mod root) to check that `file` is there; `base_rel`: the folder mod.toml is in."""
    probs: list[str] = []
    notes: list[str] = []
    clean: dict = {"for": "menu", "languages": list(LANGS)}
    if not isinstance(t, dict):
        return clean, ["mod.toml: font must be a table, written [font]"], notes
    for k in t:
        if k not in ("file", "for", "languages", "license"):
            notes.append(f"mod.toml: [font] {k} isn't something modman knows; ignored")
    v = t.get("file")
    if not isinstance(v, str) or not v.strip():
        probs.append('mod.toml: [font] file is missing (like file = "fonts/MyFont.ttf")')
    else:
        p = v.replace("\\", "/").strip()
        parts = [x for x in p.split("/") if x not in ("", ".")]
        if p.startswith("/") or (len(p) > 1 and p[1] == ":") or not parts or ".." in parts:
            probs.append(f'mod.toml: [font] file = "{v[:60]}" must be a path inside the mod folder')
        elif not parts[-1].lower().endswith(".ttf"):
            probs.append(f'mod.toml: [font] file = "{v[:60]}" must be a .ttf font (TrueType outlines)')
        else:
            clean["file"] = "/".join(parts)
            if files is not None and (base_rel + clean["file"]).lower() not in {f.lower() for f in files}:
                probs.append(f'mod.toml: [font] file "{clean["file"]}" isn\'t in the mod')
    f = t.get("for", "menu")
    if f == "all":
        notes.append('mod.toml: [font] for = "all" (Special_00, place names) comes later: used as "menu" for now')
    elif f != "menu":
        probs.append('mod.toml: [font] for must be "menu"')
    langs = t.get("languages", list(LANGS))
    if not isinstance(langs, list) or not langs or not all(isinstance(x, str) for x in langs):
        probs.append('mod.toml: [font] languages must be a list like ["US", "EU", "JP"]')
    else:
        up = [x.upper() for x in langs]
        later = [x for x in up if x in ("KR", "CN", "TW", "TH")]
        bad = [x for x in up if x not in LANGS and x not in later]
        if bad:
            probs.append(f"mod.toml: [font] languages: {', '.join(bad)} isn't one of US, EU, JP")
        if later:
            notes.append(f"mod.toml: [font] languages {', '.join(later)} come later (outline fonts); ignored")
        clean["languages"] = [x for x in LANGS if x in up]
        if not clean["languages"] and not bad:
            probs.append("mod.toml: [font] languages has none of US, EU, JP")
    lic = t.get("license")
    if lic is not None and not isinstance(lic, str):
        probs.append('mod.toml: [font] license must be text, like license = "OFL-1.1"')
    elif lic:
        clean["license"] = lic.strip()[:100]
    return clean, probs, notes


def _mod_toml(root: str) -> tuple[str, str] | None:
    """(mod.toml path, its folder relative to the mod root) at the root or inside the one folder holding it."""
    p = os.path.join(root, "mod.toml")
    if os.path.isfile(p):
        return p, ""
    try:
        subs = [e for e in os.scandir(root) if e.is_dir()]
    except OSError:
        return None
    hits = [e for e in subs if os.path.isfile(os.path.join(e.path, "mod.toml"))]
    if len(hits) == 1:
        return os.path.join(hits[0].path, "mod.toml"), hits[0].name + "/"
    return None


def font_mods(lib) -> list[FontMod]:
    """The library's mods with a [font] table, in load order (a later mod wins)."""
    out = []
    for m in getattr(lib, "mods", []):
        root = os.path.join(lib.mods_dir, m["id"])
        found = _mod_toml(root)
        if not found:
            continue
        try:
            with open(found[0], "rb") as f:
                data = tomllib.loads(f.read().decode("utf-8-sig"))
        except (OSError, ValueError, UnicodeDecodeError, RecursionError):
            continue
        if "font" not in data:
            continue
        clean, probs, notes = check_font_table(data["font"])
        path = None
        if "file" in clean:
            path = os.path.join(root, *(found[1] + clean["file"]).split("/"))
            if not os.path.isfile(path):
                probs.append(f"its font file {clean['file']} is missing from the library copy")
                path = None
        out.append(FontMod(m["id"], m.get("name", m["id"]), bool(m.get("enabled")), path,
                           tuple(clean["languages"]), probs, notes))
    return out


def on_add(lib, entry: dict, folder: str, man) -> list[str]:
    """modman add (after the mod is in the library): a font mod's font is read once, so a font this tool can't draw
    (CFF outlines, a collection) or a licence that isn't free is said at once. Never refuses the mod."""
    ft = getattr(man, "font", None)
    if not isinstance(ft, dict) or not ft.get("file"):
        return []
    rel = man.rel(ft["file"]) if hasattr(man, "rel") else ft["file"]
    try:
        tt = load_truetype(os.path.join(folder, *rel.split("/")))
    except FontError as e:
        return [f"Its font can't be used: {e}"]
    info = license_info(tt)
    out = [f"Its font ({info['family']}) can be chosen: python tools/font.py choose {entry.get('id', '<id>')}"]
    return out + [f"Its font: {w}." for w in info["warnings"]]


class Choice(NamedTuple):
    key: str       # "game", "easy-read" or "mod:<id>"
    label: str     # ASCII, at most 28 characters (the game's chooser row)
    file: str | None
    languages: tuple[str, ...]


def choices(lib) -> list[Choice]:
    """What the player can pick: the game's font, the easy-read font, and every font mod that is on."""
    install = getattr(lib, "install", None)
    out = [Choice("game", GAME_FONT, None, LANGS)]
    try:
        out.append(Choice("easy-read", EASY_READ, fira_path(FIRA_SEMIBOLD, install), LANGS))
    except FontError:
        pass
    for fm in font_mods(lib):
        if fm.enabled and fm.file and not fm.problems:
            out.append(Choice("mod:" + fm.id, _ascii_label(fm.name), fm.file, fm.languages))
    return out


def current(lib, settings: dict | None = None) -> tuple[Choice, list[str]]:
    """The font in use: the player's choice, else ("auto") the last font mod that is on, else the game's."""
    st = settings or load_settings(lib)
    opts = choices(lib)
    notes: list[str] = []
    want = st["choice"]
    if want != "auto":
        hit = next((c for c in opts if c.key == want), None)
        if hit:
            return hit, notes
        notes.append(f"The chosen font ({want.removeprefix('mod:')}) isn't available (its mod is off, removed or "
                     "broken): using the automatic choice.")
    mods = [c for c in opts if c.key.startswith("mod:")]
    return (mods[-1] if mods else opts[0]), notes


# ================================================================================================= deploy

class GenFile(NamedTuple):
    path: str                         # RomFS path for the overlay, like "Font/Font_US.sbfarc"
    data: bytes
    rstb: tuple[str, int] | None      # a size-table entry when the archive grew; None: the table needs no change


class Generated(list):
    """deploy_files' result: a list of GenFile (so `gen += other_list` works) with what modmenu.sync needs."""
    owner = "generated:fonts"

    def __init__(self, items=(), *, symbols_full: bool = False, font: str = GAME_FONT, font_key: str = "game",
                 notes: list[str] | None = None, attention: list[str] | None = None):
        super().__init__(items)
        self.symbols_full = symbols_full   # every menu font of US, EU and JP has all printable ASCII
        self.font = font                   # the font in use (ASCII, for menu.txt's `font` record)
        self.font_key = font_key
        self.notes = notes if notes is not None else []
        self.attention = attention if attention is not None else []


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read(MAX_ARCHIVE + 1)


def _entry_bytes(plan, hit) -> bytes | None:
    """Bytes of what a plan names as the winner of a path: romfs_mods' Winner (a mod's entry, or the game's file),
    or (mod index, entry) from plan.winners."""
    if hit is None:
        return None
    if hasattr(hit, "game_path"):  # romfs_mods.Winner
        e, z, idx = hit.entry, getattr(hit, "zip", None), None
        if e is None:
            return _read(hit.game_path) if hit.game_path else None
    else:
        idx, e = hit
        z = getattr(plan.mods[idx], "zip", None)
    if getattr(e, "data", None) is not None:
        return bytes(e.data)
    if getattr(e, "src", ""):
        return _read(e.src)
    zname = getattr(e, "zname", "")
    if zname:
        try:
            return z.read(zname)
        except (AttributeError, ValueError):  # (no zip, or the plan closed it: open the mod's .zip again)
            if idx is None:
                return None
            import zipfile
            with zipfile.ZipFile(plan.mods[idx].path) as z2:
                return z2.read(zname)
    return None


def winner(plan, rel: str, game_romfs: str | None) -> tuple[bytes | None, str]:
    """(bytes, where from) of the archive in use at a RomFS path: the RomFS plan's winning mod, else the game's."""
    if plan is not None:
        fn = getattr(plan, "winner", None)
        if callable(fn):
            w = fn(rel)
            got = _entry_bytes(plan, w)
            if got is not None:
                return got, getattr(w, "mod", None) or "the game"
        for target, hit in (getattr(plan, "winners", None) or {}).items():
            if target.lower() == rel.lower():
                got = _entry_bytes(plan, hit)
                if got is not None:
                    idx = hit[0] if isinstance(hit, tuple) else None
                    name = plan.mods[idx].name if idx is not None else "a mod"
                    return got, name
    if game_romfs:
        p = os.path.join(game_romfs, *rel.split("/"))
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read(MAX_ARCHIVE + 1), "the game"
    return None, ""


def _cache(lib) -> str:
    return os.path.join(_root(lib), CACHE_DIR)


def deploy_files(lib, plan=None, *, game_romfs: str | None = None) -> Generated:
    """The font archives to put in the overlay for the library's settings, built over the archive in use (the RomFS
    plan's winner, else the game's own). An empty list when nothing changes. Built archives are cached in the library
    by (input bytes, recipe), so a deploy with the same mods and settings only reads."""
    install = getattr(lib, "install", None)
    if game_romfs is None and install:
        game_romfs = os.path.join(install, "extracted", "romfs")
    st = load_settings(lib)
    pick, notes = current(lib, st)
    attention: list[str] = []
    font_tt = None
    if pick.file:
        try:
            font_tt = load_truetype(pick.file)
            lic = license_info(font_tt)
            if pick.key.startswith("mod:"):
                notes += [f"{pick.label}: {w}." for w in lic["warnings"]]
        except FontError as e:
            attention.append(f"{pick.label}: {e}; the game's font is used")
            pick, font_tt = Choice("game", GAME_FONT, None, LANGS), None
    fix_tt = None
    if st["fix_symbols"]:
        try:
            fix_tt = load_truetype(fira_path(FIRA_MEDIUM, install))
        except FontError as e:
            attention.append(f"Missing symbols can't be fixed: {e}")
    gen = Generated(font=pick.label, font_key=pick.key, notes=notes, attention=attention)
    if font_tt is None and fix_tt is None:
        return gen
    full = True
    cache = _cache(lib)
    used = set()
    for lang in LANGS:
        rel = ARCHIVE.format(lang)
        recipe = Recipe(font_tt if lang in pick.languages else None, pick.key, fix_tt)
        if recipe.font is None and recipe.fix is None:
            full = False
            continue
        packed, where = winner(plan, rel, game_romfs)
        if packed is None:
            full = False
            attention.append(f"{rel} isn't in the game files or any mod: its font is left as it is")
            continue
        key = f"{lang}-{_sha(packed)[:16]}-{recipe.key()}"
        used.add(key)
        out, rep = _cached(cache, key)
        if rep is None:
            try:
                out, rep = build_archive(packed, recipe, lang)
            except FontError as e:
                full = False
                attention.append(f"{rel} (from {where}): {e}; its font is left as it is")
                continue
            _store(cache, key, out, rep)
        full = full and bool(rep.get("symbols_full"))
        if out is not None:
            entry = tuple(rep["rstb"]) if rep.get("rstb") else None
            if entry:
                notes.append(f"{rel} grew: it needs a size-table entry ({entry[1]} bytes).")
            gen.append(GenFile(rel, out, entry))
    _prune(cache, used)
    gen.symbols_full = full
    return gen


def _cached(cache: str, key: str) -> tuple[bytes | None, dict | None]:
    try:
        with open(os.path.join(cache, key + ".json"), encoding="utf-8") as f:
            rep = json.load(f)
        if not rep.get("changed"):
            return None, rep
        with open(os.path.join(cache, key + ".sbfarc"), "rb") as f:
            data = f.read()
        if _sha(data) != rep.get("sha256"):
            return None, None
        return data, rep
    except (OSError, ValueError):
        return None, None


def _store(cache: str, key: str, data: bytes | None, rep: dict) -> None:
    try:
        os.makedirs(cache, exist_ok=True)
        rep = dict(rep, changed=data is not None, sha256=_sha(data) if data is not None else None)
        if data is not None:
            with open(os.path.join(cache, key + ".sbfarc.tmp"), "wb") as f:
                f.write(data)
            os.replace(os.path.join(cache, key + ".sbfarc.tmp"), os.path.join(cache, key + ".sbfarc"))
        with open(os.path.join(cache, key + ".json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
    except OSError:
        pass  # (a cache that can't be written only costs time)


def _prune(cache: str, used: set[str]) -> None:
    try:
        keys = {}
        for e in os.scandir(cache):
            if e.name.endswith(".json"):
                keys[e.name[:-5]] = e.stat().st_mtime
    except OSError:
        return
    old = sorted((k for k in keys if k not in used), key=lambda k: -keys[k])[max(0, CACHE_KEEP - len(used)):]
    for k in old:
        for ext in (".json", ".sbfarc"):
            try:
                os.remove(os.path.join(cache, k + ext))
            except OSError:
                pass


# ================================================================================================= preview

def write_png(path: str, rows: list[list[int]]) -> None:
    """A grey-scale PNG (no Pillow needed)."""
    h, w = len(rows), len(rows[0]) if rows else 0
    raw = b"".join(b"\0" + bytes(r) for r in rows)

    def chunk(t: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def render(bf: Bffnt, lines: list[str], scale: int = 3) -> list[list[int]]:
    """Text drawn with a bitmap font as the game spaces it (white on black), each pixel scaled up."""
    have = bf.codes()
    width = 8 + max(sum(bf.widths.get(have.get(ord(c), bf.alt), bf.default).advance for c in ln) for ln in lines)
    img = [[0] * width for _ in range(bf.linefeed * len(lines) + 8)]
    for li, ln in enumerate(lines):
        pen, top = 4, 4 + li * bf.linefeed
        for c in ln:
            gi = have.get(ord(c), bf.alt)
            g = bf.widths.get(gi, bf.default)
            cell = bf.read_cell(gi)
            for y, row in enumerate(cell[:min(bf.ch, len(img) - top)]):
                for x in range(min(g.width, bf.cw)):
                    px = pen + g.left + x
                    if 0 <= px < width:
                        img[top + y][px] = max(img[top + y][px], row[x])
            pen += g.advance
    return [[v for v in row for _ in range(scale)] for row in img for _ in range(scale)]


# ================================================================================================= command line

def _read_archive(path: str) -> bytes:
    try:
        if os.path.getsize(path) > MAX_ARCHIVE:
            raise FontError(f"{path} is too big for a font archive")
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise FontError(f"can't read {path}: {e.strerror or e}") from None


def _lang_of(path: str) -> str:
    base = os.path.basename(path).upper()
    return next((x for x in LANGS if f"FONT_{x}." in base), "US")


def _guard_out(path: str, install: str | None) -> None:
    if install:
        ex = os.path.normcase(os.path.abspath(os.path.join(install, "extracted")))
        if os.path.normcase(os.path.abspath(path)).startswith(ex + os.sep):
            raise FontError("refusing to write into Savage Wind's game files; pick another --out")


def _library(arg: str | None):
    import modman
    root = modman.resolve_library(arg)
    lib = modman.Library.load(root)
    if lib is None:
        raise FontError(f"No mod library in {root} yet. Run: python tools/modman.py init")
    return lib


def cmd_status(args) -> int:
    lib = _library(args.library)
    st = load_settings(lib)
    pick, notes = current(lib, st)
    print(f"Font: {pick.label}" + ("  (chosen)" if st["choice"] != "auto" else "  (automatic: the last font mod that "
                                                                                 "is on, else the game's)"))
    print(f"Missing symbols fixed: {'yes' if st['fix_symbols'] else 'no'}")
    print("You can choose: " + ", ".join(f"{c.label} [{c.key.removeprefix('mod:')}]" for c in choices(lib)))
    for fm in font_mods(lib):
        for p in fm.problems:
            print(f"  {fm.name}: {p}")
    for n in notes:
        print(n)
    print("Changes apply at the next deploy (python tools/modman.py deploy, or Play).")
    return 0


def cmd_choose(args) -> int:
    lib = _library(args.library)
    want = args.choice.strip().lower()
    if want not in CHOICES:
        want = "mod:" + want.removeprefix("mod:")
        fm = next((m for m in font_mods(lib) if "mod:" + m.id == want), None)
        if fm is None:
            raise FontError(f"No font mod '{want[4:]}'. Choices: auto, game, easy-read, or a font mod's id "
                            "(python tools/font.py status lists them).")
        if not fm.enabled:
            raise FontError(f"{fm.name} is off. Turn it on first: python tools/modman.py enable {fm.id}")
        if fm.problems:
            raise FontError(f"{fm.name} can't be used: {fm.problems[0]}")
    save_settings(lib, choice=want)
    pick, _ = current(lib)
    print(f"Font: {pick.label}. It applies at the next deploy (or Play).")
    return 0


def cmd_fix(args) -> int:
    lib = _library(args.library)
    save_settings(lib, fix_symbols=args.state == "on")
    print(f"Missing symbols fixed: {'yes' if args.state == 'on' else 'no'}. It applies at the next deploy (or Play).")
    return 0


def cmd_check(args) -> int:
    tt = load_truetype(args.font)
    lic = license_info(tt)
    print(f"{lic['family']} {lic['style']}".strip())
    lacks = "".join(c for c in ASCII if not tt.has(ord(c)) and c != " ")
    print("Printable ASCII: " + ("all there" if not lacks else f"lacks {lacks}"))
    print("Licence: " + (lic["license"][:120] or lic["copyright"][:120] or "(the font doesn't say)"))
    for w in lic["warnings"]:
        print("Warning: " + w)
    return 0


def cmd_build(args) -> int:
    install = args.install or None
    packed = _read_archive(args.archive)
    font = None
    key = "game"
    if args.font == "easy-read":
        font, key = load_truetype(fira_path(FIRA_SEMIBOLD, install)), "easy-read"
    elif args.font:
        font, key = load_truetype(args.font), "file"
    fix = load_truetype(fira_path(FIRA_MEDIUM, install)) if args.fix_symbols else None
    if font is None and fix is None:
        raise FontError("nothing to do: give --font and/or --fix-symbols")
    fonts = tuple(x.strip() for x in args.fonts.split(",") if x.strip()) if args.fonts else MENU_FONTS
    if NEVER in fonts:
        raise FontError(f"{NEVER} is never edited (Savage Wind finds its button icons by their hash)")
    _guard_out(args.out, install)
    lang = _lang_of(args.archive)
    out, rep = build_archive(packed, Recipe(font, key, fix, fonts), lang)
    for name, r in rep["fonts"].items():
        bits = [f"lacked {r['lacked'] or 'nothing'}"]
        if "redrawn" in r:
            bits.append(f"{r['redrawn']} redrawn at {r['px']} px ({r['clipped']} cut off at the top or bottom, "
                        f"{r['squeezed']} made to fit)")
        if r.get("fixed") or r.get("added"):
            bits.append(f"added {r.get('added', '')}{r.get('fixed', '')}")
        bits.append(f"lacks {r['lacks'] or 'nothing'}")
        print(f"{name}: " + "; ".join(bits))
    if out is None:
        print("Nothing changed; no file written.")
        return 0
    with open(args.out, "wb") as f:
        f.write(out)
    same = rep["size_after"] == rep["size_before"]
    print(f"Wrote {args.out}. Unpacked size {rep['size_before']} -> {rep['size_after']}"
          + (" (the same: no size-table entry needed)." if same else f": needs size-table entry {rep['rstb']}."))
    print("It holds the game's own glyphs: keep it on this PC, never share it.")
    return 0


def cmd_preview(args) -> int:
    packed = _read_archive(args.archive)
    arc = yaz0_decode(packed)[0] if packed[:4] == b"Yaz0" else packed
    sarc = Sarc(arc)
    name = next((n for n in sarc.names() if n.rsplit("/", 1)[-1].rsplit(".", 1)[0] == args.font_name), None)
    if name is None:
        raise FontError(f"no {args.font_name} in {args.archive}")
    bf = Bffnt(sarc.get(name), name)
    lines = args.text or ["Mods {beta} #1 50% @giruka ^_^ `x` a|b ~ok", "The quick brown fox: \"&'*;=\\\""]
    write_png(args.out, render(bf, lines, args.scale))
    print(f"Wrote {args.out}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="font.py", description="Menu fonts: missing symbols, font mods, easy-read.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", help="the font in use, the choices and the symbol fix")
    p.add_argument("--library")
    p = sub.add_parser("choose", help="pick the font: auto, game, easy-read or a font mod's id")
    p.add_argument("choice")
    p.add_argument("--library")
    p = sub.add_parser("fix-symbols", help="draw the missing { } # $ %% @ ^ ` | ~ (on) or not (off)")
    p.add_argument("state", choices=("on", "off"))
    p.add_argument("--library")
    p = sub.add_parser("check", help="a font file's licence and what it can draw")
    p.add_argument("font")
    p = sub.add_parser("build", help="edit one Font_xx.sbfarc into a new file (offline check)")
    p.add_argument("archive")
    p.add_argument("--out", required=True)
    p.add_argument("--font", help="easy-read, or a .ttf file")
    p.add_argument("--fix-symbols", action="store_true")
    p.add_argument("--fonts", help="comma-separated font names (default Caption_00,NormalS_00,Normal_00)")
    p.add_argument("--install", help="the Savage Wind folder (for its Fira Sans copy)")
    p = sub.add_parser("preview", help="draw text with a font of an archive into a PNG")
    p.add_argument("archive")
    p.add_argument("--out", required=True)
    p.add_argument("--text", action="append")
    p.add_argument("--font-name", default="Caption_00")
    p.add_argument("--scale", type=int, default=3)
    args = ap.parse_args(argv)
    try:
        return {"status": cmd_status, "choose": cmd_choose, "fix-symbols": cmd_fix, "check": cmd_check,
                "build": cmd_build, "preview": cmd_preview}[args.cmd](args)
    except FontError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
