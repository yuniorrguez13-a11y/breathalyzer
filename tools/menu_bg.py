#!/usr/bin/env python3
"""Put your own picture on the game's main menu (BotW 1.9.0 on Savage Wind), for example a shot of your character.

    python tools/menu_bg.py set <picture> [<picture> ...] [--mode art|full] [--name NAME] [--sets all|2|1,3,5]
                            [--focus left|center|right] [--no-deploy] [--priority normal] [--library <folder>]
    python tools/menu_bg.py reset [--no-deploy] [--library <folder>]
    python tools/menu_bg.py preview [<picture> ...] --out <file.png> [--mode art|full] [--set 1-7|all]
                                [--focus left|center|right] [--library <folder>]
    python tools/menu_bg.py status [--library <folder>]

The main menu shows a slideshow of 7 pictures. `set` puts your picture into all of them (or only the ones --sets
names), as a mod in your mod library (tools/modman.py), and deploys it when the game is closed. Only the menu's
texture file changes (inside Pack/Title.pack): every texture keeps its name, format and size, so the menu file keeps
its size and no size table (RSTB) is needed.

  --mode art   (default) your picture where each original picture is, on the left, with soft edges, over the game's
               own blue pattern. A PNG with see-through parts is used as a cut-out.
  --mode full  your picture fills the screen, darker on the right so the menu stays readable.

With several pictures, slideshow picture k gets picture (k-1) mod n. PNG works everywhere (pure Python); JPG, WEBP,
BMP and GIF need Pillow. `preview` draws one menu frame (or all 7, --set all) to a PNG and changes nothing; without a
picture it draws the menu as it is. Exit codes: 0 done, 2 error (one plain line), 130 stopped.
Everything is read from the files: no texture or pane name of the game is written in this tool. docs/menu-background.md
has the details.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from itertools import accumulate
from operator import itemgetter

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
import modman  # noqa: E402
import romfs_mods as rm  # noqa: E402
import wiiu_convert as wc  # noqa: E402
from wiiu_model import nx_bntx  # noqa: E402
from wiiu_model.errors import ModelError  # noqa: E402

TOOL_ID = "botw-mod-loader menu_bg"
TOOL_VERSION = "1.0.0"
MOD_ID = "botw-mod-loader.menu-picture"
AUTHOR = "giruka"
PACK_REL = "Pack/Title.pack"
RSTB_REL = "System/Resource/ResourceSizeTable.product.srsizetable"
INFO_FILE = "menu-picture.json"
BUILD_190 = modman.BUILD_190
SCRIPT = os.path.abspath(__file__)

# Picture limits
MAX_SIDE = 8192
MAX_PIXELS = 36_000_000
MAX_FILE = 512 * 1024 * 1024
MIN_FULL = (640, 360)
MIN_ART = 360
SOFT_FULL = (1280, 720)
CUTOUT_SHARE = 0.01        # a PNG is a cut-out when more than 1 % of its pixels ...
CUTOUT_ALPHA = 250         # ... are below this alpha

# Looks (tuned with screenshots in the game; see docs/menu-background.md)
FEATHER = {"left": 0.06, "right": 0.25, "top": 0.08, "bottom": 0.12}   # soft edge widths, share of the box
GLOW_GAIN = 0.63           # the menu's glow adds about this much of a picture's light (linear) on flat areas
FULL_SHADE = (0.55, 0.5)   # full mode: from this share of the screen width, darker to the right, down to this light
SOFTEN_PX = 1.5            # cut-outs: their edge is softened by about this many texels
EDGE_TOL = 1.0             # a box edge this close to (or past) the screen border counts as off screen

MODES = ("art", "full")
FOCUS = ("left", "center", "right")


class MenuBgError(Exception):
    """Something the player has to fix; the message (one plain line) says what."""


def say(msg: str = "") -> None:
    print(msg, flush=True)


# ================================================================================================ pictures

@dataclass
class Picture:
    """RGBA8, straight alpha, rows top to bottom, packed."""
    w: int
    h: int
    px: bytearray
    name: str = ""
    sha256: str = ""
    cutout: bool = False

    def row(self, y: int) -> bytearray:
        return self.px[y * self.w * 4:(y + 1) * self.w * 4]


def blank(w: int, h: int, rgba=(0, 0, 0, 0)) -> Picture:
    return Picture(w, h, bytearray(bytes(rgba) * (w * h)))


# ------------------------------------------------------------------------------------------------ PNG (zlib only)

PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
PNG_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
_AND255 = (255).__and__


class PngError(ValueError):
    """why: 'short' (ends too early), 'crc' (a checksum is wrong), 'bad' (not a PNG this reads)."""

    def __init__(self, why: str, detail: str = ""):
        super().__init__(detail or why)
        self.why = why


def _png_chunks(data: bytes):
    if data[:8] != PNG_SIG:
        raise PngError("bad", "not a PNG")
    p, n = 8, len(data)
    while True:
        if p + 12 > n:
            raise PngError("short")
        length, ctype = struct.unpack_from(">I4s", data, p)
        if length > 0x7FFFFFFF:
            raise PngError("bad", "chunk length")
        end = p + 12 + length
        if end > n:
            raise PngError("short")
        body = data[p + 8:p + 8 + length]
        if zlib.crc32(ctype + body) & 0xFFFFFFFF != struct.unpack_from(">I", data, p + 8 + length)[0]:
            raise PngError("crc")
        yield ctype, body
        if ctype == b"IEND":
            return
        p = end


def png_info(data: bytes) -> tuple[int, int, int, int, int]:
    """(width, height, bit depth, colour type, interlace) from the header, without decoding."""
    if data[:8] != PNG_SIG or len(data) < 33 or data[12:16] != b"IHDR":
        raise PngError("bad", "no PNG header")
    w, h, depth, ctype, comp, filt, inter = struct.unpack_from(">IIBBBBB", data, 16)
    if (not w or not h or ctype not in PNG_CHANNELS or depth not in PNG_DEPTHS[ctype] or comp or filt
            or inter not in (0, 1)):
        raise PngError("bad", "unsupported PNG header")
    return w, h, depth, ctype, inter


def _unfilter(raw, pos: int, rows: int, stride: int, bpp: int) -> bytearray:
    out = bytearray(stride * rows)
    prev = bytes(stride)
    m7 = int.from_bytes(b"\x7f" * stride, "little")
    m8 = int.from_bytes(b"\x80" * stride, "little")
    for y in range(rows):
        ft = raw[pos]
        line = raw[pos + 1:pos + 1 + stride]
        pos += stride + 1
        if ft == 0:
            cur = line
        elif ft == 1:
            cur = bytearray(stride)
            for c in range(bpp):
                cur[c::bpp] = bytes(map(_AND255, accumulate(line[c::bpp])))
        elif ft == 2:  # byte-wise add without carries between bytes
            a = int.from_bytes(line, "little")
            b = int.from_bytes(prev, "little")
            cur = (((a & m7) + (b & m7)) ^ ((a ^ b) & m8)).to_bytes(stride, "little")
        elif ft == 3:
            cur = bytearray(line)
            for i in range(min(bpp, stride)):
                cur[i] = (cur[i] + (prev[i] >> 1)) & 255
            for i in range(bpp, stride):
                cur[i] = (cur[i] + ((cur[i - bpp] + prev[i]) >> 1)) & 255
        elif ft == 4:
            cur = bytearray(line)
            for i in range(min(bpp, stride)):
                cur[i] = (cur[i] + prev[i]) & 255
            for i in range(bpp, stride):
                a, b, c = cur[i - bpp], prev[i], prev[i - bpp]
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                cur[i] = (cur[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        else:
            raise PngError("bad", f"filter type {ft}")
        out[y * stride:(y + 1) * stride] = cur
        prev = cur
    return out


def _unpack_bits(lines: bytearray, w: int, rows: int, stride: int, depth: int) -> bytearray:
    """Samples of depth 1/2/4 as one byte each (row-major, w per row)."""
    per = 8 // depth
    mask = (1 << depth) - 1
    table = [bytes((b >> (8 - depth * (k + 1))) & mask for k in range(per)) for b in range(256)]
    out = bytearray()
    for y in range(rows):
        out += b"".join(table[b] for b in lines[y * stride:(y + 1) * stride])[:w]
    return out


def _to_rgba(lines: bytearray, w: int, rows: int, stride: int, depth: int, ctype: int, plte, trns) -> bytearray:
    n = w * rows
    ch = PNG_CHANNELS[ctype]
    key_alpha = None   # 0/255 per pixel from a tRNS colour key, decided on the full-depth samples
    if depth == 16:
        hi = bytearray()
        full = bytearray()
        for y in range(rows):
            line = lines[y * stride:(y + 1) * stride]
            hi += line[0::2]
            full += line
        if trns is not None and ctype in (0, 2):
            keys = struct.unpack(">" + "H" * ch, trns[:2 * ch]) if len(trns) >= 2 * ch else None
            if keys is not None:
                vals = struct.unpack(f">{n * ch}H", bytes(full))
                key_alpha = bytes(0 if tuple(vals[i * ch:(i + 1) * ch]) == keys else 255 for i in range(n))
        samples = hi
    elif depth < 8:
        samples = _unpack_bits(lines, w, rows, stride, depth)
        if ctype == 0:
            if trns is not None and len(trns) >= 2:
                key = struct.unpack(">H", trns[:2])[0]
                key_alpha = bytes(0 if v == key else 255 for v in samples)
            scale = 255 // ((1 << depth) - 1)
            samples = samples.translate(bytes(min(255, v * scale) for v in range(256)))
    else:
        if stride == w * ch:
            samples = lines
        else:
            samples = bytearray()
            for y in range(rows):
                samples += lines[y * stride:y * stride + w * ch]
        if trns is not None and ctype in (0, 2) and len(trns) >= 2 * ch:
            keys = tuple(v & 255 for v in struct.unpack(">" + "H" * ch, trns[:2 * ch]))
            if ch == 1:
                key_alpha = samples.translate(bytes(0 if v == keys[0] else 255 for v in range(256)))
            else:
                k = bytes(keys)
                key_alpha = bytes(0 if bytes(t) == k else 255
                                  for t in zip(samples[0::3], samples[1::3], samples[2::3]))
    out = bytearray(n * 4)
    if ctype == 6:
        out[:] = samples
    elif ctype == 2:
        out[0::4], out[1::4], out[2::4] = samples[0::3], samples[1::3], samples[2::3]
        out[3::4] = key_alpha if key_alpha is not None else b"\xff" * n
    elif ctype == 0:
        out[0::4] = out[1::4] = out[2::4] = samples
        out[3::4] = key_alpha if key_alpha is not None else b"\xff" * n
    elif ctype == 4:
        out[0::4] = out[1::4] = out[2::4] = samples[0::2]
        out[3::4] = samples[1::2]
    else:
        if not plte or len(plte) % 3:
            raise PngError("bad", "palette missing")
        k = len(plte) // 3
        pal = list(plte) + [0] * (3 * (256 - k))
        alpha = [255] * 256
        for i, a in enumerate((trns or b"")[:k]):
            alpha[i] = a
        out[0::4] = samples.translate(bytes(pal[0::3]))
        out[1::4] = samples.translate(bytes(pal[1::3]))
        out[2::4] = samples.translate(bytes(pal[2::3]))
        out[3::4] = samples.translate(bytes(alpha))
    return out


def read_png(data: bytes) -> Picture:
    """A PNG as RGBA8: colour types 0/2/3/4/6, bit depths 1-16, tRNS, Adam7. Raises PngError."""
    w, h, depth, ctype, inter = png_info(data)
    plte = trns = None
    idat = []
    for t, body in _png_chunks(data):
        if t == b"PLTE":
            plte = body
        elif t == b"tRNS":
            trns = body
        elif t == b"IDAT":
            idat.append(body)
    if not idat:
        raise PngError("short")
    bits = PNG_CHANNELS[ctype] * depth
    bpp = max(1, bits // 8)
    if inter:
        passes = []
        for x0, y0, dx, dy in ADAM7:
            pw, ph = (w - x0 + dx - 1) // dx, (h - y0 + dy - 1) // dy
            if pw > 0 and ph > 0:
                passes.append((pw, ph, (x0, y0, dx, dy)))
    else:
        passes = [(w, h, None)]
    expected = sum(ph * (1 + (pw * bits + 7) // 8) for pw, ph, _ in passes)
    try:
        raw = zlib.decompressobj().decompress(b"".join(idat), expected + 1)
    except zlib.error:
        raise PngError("short", "picture data is damaged") from None
    if len(raw) < expected:
        raise PngError("short")
    if not inter:
        stride = (w * bits + 7) // 8
        lines = _unfilter(raw, 0, h, stride, bpp)
        return Picture(w, h, _to_rgba(lines, w, h, stride, depth, ctype, plte, trns))
    out = bytearray(w * h * 4)
    pos = 0
    for pw, ph, (x0, y0, dx, dy) in passes:
        stride = (pw * bits + 7) // 8
        lines = _unfilter(raw, pos, ph, stride, bpp)
        pos += ph * (stride + 1)
        sub = _to_rgba(lines, pw, ph, stride, depth, ctype, plte, trns)
        for j in range(ph):
            y = y0 + j * dy
            row = out[y * w * 4:(y + 1) * w * 4]
            src = sub[j * pw * 4:(j + 1) * pw * 4]
            for c in range(4):
                row[x0 * 4 + c::dx * 4] = src[c::4]
            out[y * w * 4:(y + 1) * w * 4] = row
    return Picture(w, h, out)


def png_bytes(pic: Picture, level: int = 6) -> bytes:
    """An RGBA8 PNG of a picture (filter: none)."""
    stride = pic.w * 4
    raw = b"".join(b"\0" + bytes(pic.px[y * stride:(y + 1) * stride]) for y in range(pic.h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (PNG_SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", pic.w, pic.h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, level)) + chunk(b"IEND", b""))


def write_png(path: str, pic: Picture) -> None:
    data = png_bytes(pic)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


# ------------------------------------------------------------------------------------------------ loading pictures

KINDS = {"png": "PNG", "jpg": "JPG", "webp": "WEBP", "bmp": "BMP", "gif": "GIF"}


def pillow():
    """PIL.Image, or None when Pillow isn't there (it is optional)."""
    try:
        from PIL import Image
        return Image
    except Exception:  # (ImportError, or a broken install)
        return None


def sniff(data: bytes) -> str:
    if data[:8] == PNG_SIG:
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"BM":
        return "bmp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return ""


def check_size(name: str, w: int, h: int, mode: str) -> None:
    if w > MAX_SIDE or h > MAX_SIDE:
        raise MenuBgError(f"{name} is too big: {w} x {h}. Use at most {MAX_SIDE} pixels a side.")
    if w * h > MAX_PIXELS:
        raise MenuBgError(f"{name} is too big: {w} x {h}. Use at most {MAX_PIXELS // 1_000_000} million pixels.")
    if mode == "full" and (w < MIN_FULL[0] or h < MIN_FULL[1]):
        raise MenuBgError(f"{name} is too small: {w} x {h}. Use at least {MIN_FULL[0]} x {MIN_FULL[1]}.")
    if mode == "art" and min(w, h) < MIN_ART:
        raise MenuBgError(f"{name} is too small: {w} x {h}. Use at least {MIN_ART} pixels on the short side.")


def is_cutout(pic: Picture) -> bool:
    alpha = pic.px[3::4]
    solid = sum(alpha.count(v) for v in range(CUTOUT_ALPHA, 256))
    return (len(alpha) - solid) > CUTOUT_SHARE * len(alpha)


def _damaged(name: str, why: str) -> MenuBgError:
    text = {"short": "it ends too early", "crc": "a checksum is wrong"}.get(why, "it can't be read")
    return MenuBgError(f"{name} is damaged ({text}). Save it again.")


def load_picture(path: str, mode: str = "art") -> Picture:
    """A picture file as RGBA8, checked against the limits. PNG without Pillow; other kinds need Pillow."""
    name = os.path.basename(path)
    try:
        size = os.path.getsize(path)
        if size > MAX_FILE:
            raise MenuBgError(f"{name} is too big ({size // (1024 * 1024)} MB). Save it smaller.")
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        raise MenuBgError(f"Can't find {path}.") from None
    except IsADirectoryError:
        raise MenuBgError(f"{path} is a folder, not a picture.") from None
    except PermissionError:
        raise MenuBgError(f"Can't open {name}: no permission to read it.") from None
    kind = sniff(data)
    if not kind:
        raise MenuBgError(f"Can't read {name}. This tool reads PNG (and JPG, WEBP, BMP with Pillow). Save it as PNG.")
    Image = pillow()
    pic = None
    if kind == "png":
        try:
            w, h, depth, _, _ = png_info(data)
        except PngError as e:
            raise _damaged(name, e.why) from None
        check_size(name, w, h, mode)
        if Image is not None and depth <= 8:
            pic = _pillow_load(Image, data, name, mode)
        else:
            try:
                pic = read_png(data)
            except PngError as e:
                raise _damaged(name, e.why) from None
    else:
        if Image is None:
            raise MenuBgError(f"Can't read {name}: {KINDS[kind]} needs Pillow, and this PC doesn't have it. Save it as "
                              "PNG, or run: py -m pip install pillow")
        pic = _pillow_load(Image, data, name, mode)
    pic.name = name
    pic.sha256 = hashlib.sha256(data).hexdigest()
    pic.cutout = kind == "png" and is_cutout(pic)
    return pic


def _pillow_load(Image, data: bytes, name: str, mode: str) -> Picture:
    try:
        from PIL import ImageOps
        im = Image.open(io.BytesIO(data))
        check_size(name, im.width, im.height, mode)
        im.load()
        im = ImageOps.exif_transpose(im)
        check_size(name, im.width, im.height, mode)
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        return Picture(im.width, im.height, bytearray(im.tobytes()))
    except MenuBgError:
        raise
    except Exception as e:  # (Pillow raises many kinds for a damaged file)
        why = "short" if "truncated" in str(e).lower() else "bad"
        raise _damaged(name, why) from None


# ================================================================================================ image operations
# Pictures stay RGBA8 byte buffers; per-pixel work is done row by row with gathers (itemgetter) and translate tables,
# never with one Python object per pixel for a whole picture.

_SRGB2LIN = [((v / 255) / 12.92 if v <= 10 else (((v / 255) + 0.055) / 1.055) ** 2.4) for v in range(256)]
_LIN_STEPS = 4096
_LIN2SRGB = bytes(min(255, max(0, round(255 * ((x / (_LIN_STEPS - 1)) * 12.92 if x / (_LIN_STEPS - 1) <= 0.0031308
                                               else 1.055 * (x / (_LIN_STEPS - 1)) ** (1 / 2.4) - 0.055))))
                  for x in range(_LIN_STEPS))


def srgb_to_lin(v: int) -> float:
    return _SRGB2LIN[v]


def lin_to_srgb(x: float) -> int:
    return _LIN2SRGB[min(_LIN_STEPS - 1, max(0, int(x * (_LIN_STEPS - 1) + 0.5)))]


def gain_table(gain: float) -> bytes:
    """translate() table: an sRGB value times `gain` in linear light."""
    return bytes(lin_to_srgb(_SRGB2LIN[v] * gain) for v in range(256))


def _taps(src_len: int, x0: float, x1: float, n: int) -> list[list[tuple[int, int]]]:
    """Per output index: [(source index, weight / 2^14)]. Area filter going down, bilinear going up; indices are
    clamped to the source (edge pixels repeat)."""
    k = (x1 - x0) / n
    out = []
    last = src_len - 1
    if k <= 1.0:
        for j in range(n):
            c = x0 + (j + 0.5) * k - 0.5
            i = math.floor(c)
            t = c - i
            w1 = int(t * 16384 + 0.5)
            out.append([(min(last, max(0, i)), 16384 - w1), (min(last, max(0, i + 1)), w1)])
        return out
    for j in range(n):
        a, b = x0 + j * k, x0 + (j + 1) * k
        i, taps, total = math.floor(a), [], 0.0
        while i < b:
            ov = min(b, i + 1) - max(a, i)
            if ov > 0:
                taps.append([min(last, max(0, i)), ov])
                total += ov
            i += 1
        ws = [int(ov / total * 16384 + 0.5) for _, ov in taps]
        ws[max(range(len(ws)), key=ws.__getitem__)] += 16384 - sum(ws)
        out.append([(i, w) for (i, _), w in zip(taps, ws)])
    return out


def _resample_rows(rows: list[bytes], src_w: int, x0: float, x1: float, out_w: int) -> list[bytes]:
    """Horizontal pass: every row (RGBA) to out_w pixels over [x0, x1)."""
    while (x1 - x0) / out_w > 2.0:   # (2x box steps first: at most 3 taps in the last pass)
        half = max(1, (src_w + 1) // 2)
        get_a = itemgetter(*[4 * min(src_w - 1, 2 * j) + c for j in range(half) for c in range(4)])
        get_b = itemgetter(*[4 * min(src_w - 1, 2 * j + 1) + c for j in range(half) for c in range(4)])
        rows = [bytes((p + q + 1) >> 1 for p, q in zip(get_a(r), get_b(r))) for r in rows]
        src_w, x0, x1 = half, x0 / 2, x1 / 2
    taps = _taps(src_w, x0, x1, out_w)
    n = max(len(t) for t in taps)
    for t in taps:
        t += [(t[0][0], 0)] * (n - len(t))
    getters = [itemgetter(*[4 * t[s][0] + c for t in taps for c in range(4)]) for s in range(n)]
    weights = [[t[s][1] for t in taps for _ in range(4)] for s in range(n)]
    out = []   # (4 indices per pixel: itemgetter always returns tuples)
    if n == 2:
        ga, gb = getters
        wa, wb = weights
        for r in rows:
            out.append(bytes((a * x + b * y + 8192) >> 14 for a, b, x, y in zip(ga(r), gb(r), wa, wb)))
    elif n == 3:
        ga, gb, gc = getters
        wa, wb, wc_ = weights
        for r in rows:
            out.append(bytes((a * x + b * y + c * z + 8192) >> 14
                             for a, b, c, x, y, z in zip(ga(r), gb(r), gc(r), wa, wb, wc_)))
    else:
        for r in rows:
            gs = [g(r) for g in getters]
            out.append(bytes((sum(v * w for v, w in zip(col, ws)) + 8192) >> 14
                             for col, ws in zip(zip(*gs), zip(*weights))))
    return out


def resample(pic: Picture, box: tuple[float, float, float, float], out_w: int, out_h: int) -> Picture:
    """The source box (pixel units, may extend past the picture: edges repeat) scaled to out_w x out_h.
    Pillow's LANCZOS when Pillow is there, else an area/bilinear filter in pure Python."""
    x0, y0, x1, y1 = box
    Image = pillow()
    if Image is not None and 0 <= x0 < x1 <= pic.w and 0 <= y0 < y1 <= pic.h:
        im = Image.frombytes("RGBA", (pic.w, pic.h), bytes(pic.px))
        out = im.resize((out_w, out_h), Image.Resampling.LANCZOS, box=(x0, y0, x1, y1))
        return Picture(out_w, out_h, bytearray(out.tobytes()))
    stride = pic.w * 4
    ky = (y1 - y0) / out_h
    while ky > 2.0 and pic.h > 1:   # (vertical 2x box steps)
        h2 = (pic.h + 1) // 2
        rows = []
        for j in range(h2):
            a = pic.px[2 * j * stride:(2 * j + 1) * stride]
            b = pic.px[min(pic.h - 1, 2 * j + 1) * stride:(min(pic.h - 1, 2 * j + 1) + 1) * stride]
            rows.append(bytes((p + q + 1) >> 1 for p, q in zip(a, b)))
        pic = Picture(pic.w, h2, bytearray(b"".join(rows)))
        y0, y1, ky = y0 / 2, y1 / 2, ky / 2
    vt = _taps(pic.h, y0, y1, out_h)
    need = sorted({i for t in vt for i, _ in t})
    src_rows = [bytes(pic.px[i * stride:(i + 1) * stride]) for i in need]
    hrows = dict(zip(need, _resample_rows(src_rows, pic.w, x0, x1, out_w)))
    out = bytearray()
    for t in vt:
        if len(t) == 1 or all(w == 0 for _, w in t[1:]):
            out += hrows[t[0][0]]
        elif len(t) == 2:
            (ia, wa), (ib, wb) = t
            out += bytes((a * wa + b * wb + 8192) >> 14 for a, b in zip(hrows[ia], hrows[ib]))
        else:
            rs = [hrows[i] for i, _ in t]
            ws = [w for _, w in t]
            out += bytes((sum(v * w for v, w in zip(col, ws)) + 8192) >> 14 for col in zip(*rs))
    return Picture(out_w, out_h, out)


def premultiply(pic: Picture) -> Picture:
    px = pic.px
    out = bytearray(px)
    a = px[3::4]
    for c in range(3):
        out[c::4] = bytes((v * x + 127) // 255 for v, x in zip(px[c::4], a))
    return Picture(pic.w, pic.h, out, pic.name, pic.sha256, pic.cutout)


def unpremultiply(pic: Picture) -> Picture:
    px = pic.px
    out = bytearray(px)
    a = px[3::4]
    for c in range(3):
        out[c::4] = bytes(min(255, (v * 255 + x // 2) // x) if x else 0 for v, x in zip(px[c::4], a))
    return Picture(pic.w, pic.h, out, pic.name, pic.sha256, pic.cutout)


def sample(pic: Picture, box, out_w: int, out_h: int, outside: str = "clamp") -> Picture:
    """resample(), where the part of the box outside the picture repeats its edge ("clamp") or is clear ("clear").
    A picture with alpha is filtered premultiplied, so no dark or light fringe comes from its clear parts."""
    x0, y0, x1, y1 = box
    kx, ky = (x1 - x0) / out_w, (y1 - y0) / out_h
    # output columns / rows whose centre is inside the picture
    j0 = max(0, min(out_w, math.ceil((0 - x0) / kx - 0.5)))
    j1 = max(j0, min(out_w, math.ceil((pic.w - x0) / kx - 0.5)))
    i0 = max(0, min(out_h, math.ceil((0 - y0) / ky - 0.5)))
    i1 = max(i0, min(out_h, math.ceil((pic.h - y0) / ky - 0.5)))
    if j1 <= j0 or i1 <= i0:
        return blank(out_w, out_h) if outside == "clear" else blank(out_w, out_h, bytes(pic.px[0:4]))
    sub = (max(0.0, x0 + j0 * kx), max(0.0, y0 + i0 * ky), min(float(pic.w), x0 + j1 * kx),
           min(float(pic.h), y0 + i1 * ky))
    alpha = pic.cutout
    src = premultiply(pic) if alpha else pic
    inner = resample(src, sub, j1 - j0, i1 - i0)
    if alpha:
        inner = unpremultiply(inner)
    if (j0, i0, j1, i1) == (0, 0, out_w, out_h):
        return inner
    out = bytearray()
    iw = j1 - j0
    for i in range(out_h):
        if outside == "clear" and not i0 <= i < i1:
            out += bytes(out_w * 4)
            continue
        ii = min(i1 - 1, max(i0, i)) - i0
        r = inner.px[ii * iw * 4:(ii + 1) * iw * 4]
        if outside == "clear":
            out += bytes(j0 * 4) + r + bytes((out_w - j1) * 4)
        else:
            out += r[0:4] * j0 + r + r[-4:] * (out_w - j1)
    return Picture(out_w, out_h, out)


def smooth(t: float) -> float:
    t = 0.0 if t <= 0 else 1.0 if t >= 1 else t
    return t * t * (3 - 2 * t)


def ramp(n: int, start: float, step: float, edges: list[tuple[float, float, int]]) -> list[float]:
    """Per texel (centre at start + (i + 0.5) * step): the product of smooth fades. edges: (edge position, width,
    direction): +1 fades in after the edge, -1 fades out before it."""
    out = []
    for i in range(n):
        p = start + (i + 0.5) * step
        f = 1.0
        for pos, width, d in edges:
            if width > 0:
                f *= smooth((p - pos) / width if d > 0 else (pos - p) / width)
        out.append(f)
    return out


def mul_alpha(pic: Picture, fx: list[float], fy: list[float]) -> None:
    """alpha *= fx[column] * fy[row], in place (only the rows and columns that change are touched)."""
    w = pic.w
    cols = [j for j, f in enumerate(fx) if f < 1.0]
    for y, g in enumerate(fy):
        if g >= 1.0 and not cols:
            continue
        o = y * w * 4
        a = pic.px[o + 3:o + w * 4:4]
        if g >= 1.0:
            for j in cols:
                a[j] = int(a[j] * fx[j] + 0.5)
        else:
            a = bytearray(int(v * f * g + 0.5) for v, f in zip(a, fx))
        pic.px[o + 3:o + w * 4:4] = a


def apply_gain_columns(pic: Picture, gains: list[float]) -> None:
    """RGB times gains[column] in linear light (alpha kept), in place. Equal neighbouring gains share a table."""
    w = pic.w
    tables: dict[int, bytes] = {}
    runs = []
    j = 0
    while j < w:
        q = int(round(gains[j] * 256))
        k = j
        while k + 1 < w and int(round(gains[k + 1] * 256)) == q:
            k += 1
        if q < 256:
            if q not in tables:
                tables[q] = gain_table(q / 256)
            runs.append((j, k + 1, tables[q]))
        j = k + 1
    if not runs:
        return
    alpha = pic.px[3::4]
    for y in range(pic.h):
        o = y * w * 4
        for a, b, tab in runs:
            pic.px[o + 4 * a:o + 4 * b] = pic.px[o + 4 * a:o + 4 * b].translate(tab)
    pic.px[3::4] = alpha


def apply_gain(pic: Picture, gain: float) -> None:
    if gain >= 0.999:
        return
    alpha = pic.px[3::4]
    pic.px[:] = pic.px.translate(gain_table(gain))
    pic.px[3::4] = alpha


def soften_alpha(pic: Picture, radius: int) -> None:
    """A small box blur of the alpha channel (cut-out edges), in place."""
    if radius <= 0:
        return
    w, h = pic.w, pic.h
    a = pic.px[3::4]
    k = 2 * radius + 1
    rows = []
    for y in range(h):
        r = a[y * w:(y + 1) * w]
        pad = r[:1] * radius + r + r[-1:] * radius
        acc = list(accumulate(pad, initial=0))
        rows.append([(acc[j + k] - acc[j]) / k for j in range(w)])
    out = bytearray(w * h)
    for x in range(w):
        col = [rows[y][x] for y in range(h)]
        pad = col[:1] * radius + col + col[-1:] * radius
        acc = list(accumulate(pad, initial=0.0))
        for y in range(h):
            out[y * w + x] = min(255, int((acc[y + k] - acc[y]) / k + 0.5))
    pic.px[3::4] = out


def push_pull(pic: Picture) -> None:
    """Colour under clear texels (alpha 0) from the nearest visible ones (a pull-push pyramid), so filtering and
    compression make no dark fringe. Visible texels keep their colour."""
    w, h = pic.w, pic.h
    a = pic.px[3::4]
    if not a.count(0) or a.count(0) == len(a):
        return
    chans = [list(pic.px[c::4]) for c in range(3)]
    mask = [1 if v else 0 for v in a]
    levels = [(w, h, chans, mask)]
    while w > 1 or h > 1:
        nw, nh = (w + 1) // 2, (h + 1) // 2
        cs, m = levels[-1][2], levels[-1][3]
        ncs = [[0] * (nw * nh) for _ in range(3)]
        nm = [0] * (nw * nh)
        for y in range(nh):
            for x in range(nw):
                tot = 0
                acc = [0, 0, 0]
                for yy in (2 * y, 2 * y + 1):
                    if yy >= h:
                        continue
                    for xx in (2 * x, 2 * x + 1):
                        if xx >= w:
                            continue
                        i = yy * w + xx
                        if m[i]:
                            tot += 1
                            acc[0] += cs[0][i]
                            acc[1] += cs[1][i]
                            acc[2] += cs[2][i]
                if tot:
                    o = y * nw + x
                    nm[o] = 1
                    for c in range(3):
                        ncs[c][o] = acc[c] // tot
        w, h = nw, nh
        levels.append((w, h, ncs, nm))
    for li in range(len(levels) - 2, -1, -1):
        w, h, cs, m = levels[li]
        pw, _, pcs, _ = levels[li + 1]
        for i in range(w * h):
            if not m[i]:
                p = (i // w // 2) * pw + (i % w) // 2
                for c in range(3):
                    cs[c][i] = pcs[c][p]
    for c in range(3):
        pic.px[c::4] = bytes(levels[0][2][c])


def fill_clear(pic: Picture, rgba=(0, 0, 0, 0)) -> None:
    pic.px[:] = bytes(rgba) * (pic.w * pic.h)


def psnr(a: bytes, b: bytes, channels=(0, 1, 2, 3)) -> float:
    se = n = 0
    for c in channels:
        x, y = a[c::4], b[c::4]
        se += sum((p - q) * (p - q) for p, q in zip(x, y))
        n += len(x)
    return float("inf") if se == 0 else 10 * math.log10(255 * 255 * n / se)


# ================================================================================================ BC codec
# Written from Microsoft's public block-compression documentation. Images are RGBA8 row-major. BC1 is only written
# for opaque pictures and never in its 3-colour (see-through) mode: every block has c0 > c1, or one colour (index 0).

def _565(c: int) -> tuple[int, int, int]:
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _to565(r: int, g: int, b: int) -> int:
    return ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | ((b * 31 + 127) // 255)


def _padded(rgba, w: int, h: int) -> tuple[bytes, int, int]:
    """RGBA padded to a multiple of 4 by repeating the last column/row (edge blocks stay clean)."""
    pw, ph = (w + 3) & ~3, (h + 3) & ~3
    if (pw, ph) == (w, h):
        return bytes(rgba), w, h
    rows = []
    for y in range(ph):
        yy = min(y, h - 1)
        row = bytes(rgba[yy * w * 4:(yy + 1) * w * 4])
        rows.append(row + row[-4:] * (pw - w))
    return b"".join(rows), pw, ph


def _crop(rgba: bytearray, pw: int, w: int, h: int) -> bytearray:
    if pw == w:
        return rgba[:w * h * 4]
    return bytearray(b"".join(bytes(rgba[y * pw * 4:y * pw * 4 + w * 4]) for y in range(h)))


def _bc1_palette(c0: int, c1: int, four: bool) -> list[bytes]:
    r0, g0, b0 = _565(c0)
    r1, g1, b1 = _565(c1)
    if four:
        return [bytes((r0, g0, b0, 255)), bytes((r1, g1, b1, 255)),
                bytes(((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255)),
                bytes(((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255))]
    return [bytes((r0, g0, b0, 255)), bytes((r1, g1, b1, 255)),
            bytes(((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255)), bytes(4)]


def _alpha_palette(a0: int, a1: int) -> list[int]:
    if a0 > a1:
        return [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    return [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]


def decode_bc1(data, w: int, h: int, bc3: bool = False) -> bytearray:
    bx, by = (w + 3) // 4, (h + 3) // 4
    pw = bx * 4
    out = bytearray(pw * by * 16)
    bpb = 16 if bc3 else 8
    cache: dict = {}
    acache: dict = {}
    mv = memoryview(data)
    row_bytes = pw * 4
    for j in range(by):
        base_row = j * 4 * row_bytes
        for i in range(bx):
            o = (j * bx + i) * bpb
            if bc3:
                a0, a1 = mv[o], mv[o + 1]
                abits = int.from_bytes(mv[o + 2:o + 8], "little")
                c0, c1, bits = struct.unpack_from("<HHI", mv, o + 8)
            else:
                c0, c1, bits = struct.unpack_from("<HHI", mv, o)
            key = (c0, c1)
            pal = cache.get(key)
            if pal is None:
                pal = cache[key] = _bc1_palette(c0, c1, bc3 or c0 > c1)
            px = [pal[(bits >> (2 * k)) & 3] for k in range(16)]
            if bc3:
                ap = acache.get((a0, a1))
                if ap is None:
                    ap = acache[(a0, a1)] = _alpha_palette(a0, a1)
                px = [p[:3] + bytes((ap[(abits >> (3 * k)) & 7],)) for k, p in enumerate(px)]
            col = base_row + i * 16
            for r in range(4):
                p = col + r * row_bytes
                out[p:p + 16] = b"".join(px[4 * r:4 * r + 4])
    return _crop(out, pw, w, h)


def decode_bc3(data, w: int, h: int) -> bytearray:
    return decode_bc1(data, w, h, bc3=True)


def decode_bc4(data, w: int, h: int) -> bytes:
    """BC4 as one byte per pixel (the R channel)."""
    bx, by = (w + 3) // 4, (h + 3) // 4
    pw = bx * 4
    out = bytearray(pw * by * 4)
    cache: dict = {}
    for j in range(by):
        for i in range(bx):
            o = (j * bx + i) * 8
            a0, a1 = data[o], data[o + 1]
            bits = int.from_bytes(data[o + 2:o + 8], "little")
            pal = cache.get((a0, a1))
            if pal is None:
                pal = cache[(a0, a1)] = _alpha_palette(a0, a1)
            for r in range(4):
                p = (j * 4 + r) * pw + i * 4
                out[p:p + 4] = bytes(pal[(bits >> (3 * (4 * r + k))) & 7] for k in range(4))
    if pw == w:
        return bytes(out[:w * h])
    return b"".join(bytes(out[y * pw:y * pw + w]) for y in range(h))


def channels_rgba(planes: dict[int, bytes], n: int, sel) -> bytearray:
    """RGBA8 from channel planes {2: R, 3: G, 4: B, 5: A} through BNTX selectors (0 zero, 1 one, 2-5 R/G/B/A)."""
    out = bytearray(n * 4)
    for c, s in enumerate(sel):
        if s == 1:
            out[c::4] = b"\xff" * n
        elif s in planes:
            out[c::4] = planes[s]
    return out


def decode_texture(fmt: int, w: int, h: int, data, channels=(2, 3, 4, 5)) -> Picture:
    """A linear (untiled) level as RGBA8 through the texture's channel selectors. BC1/BC3/BC4/R8/RGBA8."""
    lay = fmt >> 8
    n = w * h
    if lay == 0x1A or lay == 0x1C:
        rgba = decode_bc1(data, w, h, bc3=lay == 0x1C)
        planes = {2: rgba[0::4], 3: rgba[1::4], 4: rgba[2::4], 5: rgba[3::4]}
    elif lay == 0x1D:
        planes = {2: decode_bc4(data, w, h)}
    elif lay == 0x02:
        planes = {2: bytes(data[:n])}
    elif lay == 0x0B:
        d = bytes(data[:n * 4])
        planes = {2: d[0::4], 3: d[1::4], 4: d[2::4], 5: d[3::4]}
    else:
        raise MenuBgError(f"texture format {fmt:#06x} can't be drawn by this tool")
    if tuple(channels) == (2, 3, 4, 5) and len(planes) == 4:
        return Picture(w, h, bytearray(rgba if lay in (0x1A, 0x1C) else d))
    return Picture(w, h, channels_rgba(planes, n, channels))


# --- BC1/BC3 encoder: endpoints from the colour bounding box (diagonal from the covariance signs, inset by 1/16),
# indices by projection; blocks with at most 4 colours are solved exactly from per-channel inverse tables.

_IDX4 = (0, 2, 3, 1)       # step along e0 -> e1 (0..3) -> BC1 index
_SWAP4 = (1, 0, 3, 2)
_AIDX = (0, 2, 3, 4, 5, 6, 7, 1)   # step from a0 (max) to a1 (min) -> BC3 alpha index


def _inverse_tables() -> dict[int, dict]:
    """(step, 8-bit value) -> set of (e0, e1) 5- or 6-bit endpoint pairs whose 4-colour palette has that value."""
    inv: dict[int, dict] = {5: {}, 6: {}}
    for bits in (5, 6):
        n = 1 << bits
        ex = [(x << 3) | (x >> 2) for x in range(32)] if bits == 5 else [(x << 2) | (x >> 4) for x in range(64)]
        t = inv[bits]
        for a in range(n):
            A = ex[a]
            for b in range(n):
                B = ex[b]
                for s, v in enumerate((A, B, (2 * A + B) // 3, (A + 2 * B) // 3)):
                    t.setdefault((s, v), set()).add((a, b))
    return inv


_INV4: dict | None = None


def _pick(cands):
    for p in sorted(cands):
        if p[0] != p[1]:
            return p
    return min(cands)


def _exact_color(px: list[int], bc1: bool):
    """A 4-colour block that decodes to exactly these 16 pixels when they hold at most 4 colours that some 565
    endpoint pair reproduces (used or not), else None."""
    global _INV4
    cols = list(zip(px[0::3], px[1::3], px[2::3]))
    uniq = list(dict.fromkeys(cols))
    k = len(uniq)
    if k > 4:
        return None
    if _INV4 is None:
        _INV4 = _inverse_tables()
    from itertools import permutations
    for perm in permutations(range(4), k):
        chans = []
        for ch, bits in ((0, 5), (1, 6), (2, 5)):
            tab = _INV4[bits]
            s = None
            for u, st in zip(uniq, perm):
                c = tab.get((st, u[ch]))
                if not c:
                    s = None
                    break
                s = c if s is None else (s & c)
                if not s:
                    break
            if not s:
                break
            chans.append(_pick(s))
        if len(chans) < 3:
            continue
        (r0, r1), (g0, g1), (b0, b1) = chans
        c0, c1 = (r0 << 11) | (g0 << 5) | b0, (r1 << 11) | (g1 << 5) | b1
        idx = dict(zip(uniq, perm))
        remap = (0, 1, 2, 3)
        if c0 == c1:
            if bc1:   # (equal endpoints: every step is the same colour; index 0 only)
                return struct.pack("<HHI", c0, c1, 0)
        elif c0 < c1:
            c0, c1, remap = c1, c0, _SWAP4
        bits = 0
        for n, p in enumerate(cols):
            bits |= remap[idx[p]] << (2 * n)
        return struct.pack("<HHI", c0, c1, bits)
    return None


def _color_block(px: list[int], bc1: bool) -> bytes:
    """px: 16 x (r, g, b) flattened. An 8-byte colour block in 4-colour mode (c0 > c1, or c0 == c1 with index 0)."""
    ex = _exact_color(px, bc1)
    if ex is not None:
        return ex
    rs, gs, bs = px[0::3], px[1::3], px[2::3]
    rmin, rmax, gmin, gmax, bmin, bmax = min(rs), max(rs), min(gs), max(gs), min(bs), max(bs)
    rr, gr, br = rmax - rmin, gmax - gmin, bmax - bmin
    rm, gm, bm = (rmin + rmax) / 2, (gmin + gmax) / 2, (bmin + bmax) / 2
    if gr >= rr and gr >= br:
        cr = sum((r - rm) * (g - gm) for r, g in zip(rs, gs))
        cb = sum((b - bm) * (g - gm) for b, g in zip(bs, gs))
        if cr < 0:
            rmin, rmax = rmax, rmin
        if cb < 0:
            bmin, bmax = bmax, bmin
    elif rr >= br:
        cg = sum((g - gm) * (r - rm) for r, g in zip(rs, gs))
        cb = sum((b - bm) * (r - rm) for r, b in zip(rs, bs))
        if cg < 0:
            gmin, gmax = gmax, gmin
        if cb < 0:
            bmin, bmax = bmax, bmin
    else:
        cr = sum((r - rm) * (b - bm) for r, b in zip(rs, bs))
        cg = sum((g - gm) * (b - bm) for g, b in zip(gs, bs))
        if cr < 0:
            rmin, rmax = rmax, rmin
        if cg < 0:
            gmin, gmax = gmax, gmin
    ir, ig, ib = (rmax - rmin) / 16, (gmax - gmin) / 16, (bmax - bmin) / 16
    hi = (min(255, max(0, round(rmax - ir))), min(255, max(0, round(gmax - ig))), min(255, max(0, round(bmax - ib))))
    lo = (min(255, max(0, round(rmin + ir))), min(255, max(0, round(gmin + ig))), min(255, max(0, round(bmin + ib))))
    c0, c1 = _to565(*hi), _to565(*lo)
    if c0 < c1:
        c0, c1 = c1, c0
    if c0 == c1:
        return struct.pack("<HHI", c0, c1, 0)
    r0, g0, b0 = _565(c0)
    r1, g1, b1 = _565(c1)
    dr, dg, db = r1 - r0, g1 - g0, b1 - b0
    k = 3 / (dr * dr + dg * dg + db * db)
    bits = 0
    sh = 0
    for n in range(0, 48, 3):
        t = ((px[n] - r0) * dr + (px[n + 1] - g0) * dg + (px[n + 2] - b0) * db) * k
        bits |= _IDX4[0 if t < 0.5 else 1 if t < 1.5 else 2 if t < 2.5 else 3] << sh
        sh += 2
    return struct.pack("<HHI", c0, c1, bits)


def _alpha_block(al) -> bytes:
    amin, amax = min(al), max(al)
    if amin == amax:
        return bytes((amax, amin)) + bytes(6)
    pal = _alpha_palette(amax, amin)
    where = {}
    for k, v in enumerate(pal):
        where.setdefault(v, k)
    if all(a in where for a in al):   # (every value is on the palette: exact)
        bits = 0
        for k, a in enumerate(al):
            bits |= where[a] << (3 * k)
        return bytes((amax, amin)) + bits.to_bytes(6, "little")
    k7 = 7 / (amax - amin)
    bits = 0
    sh = 0
    for a in al:
        s = int((amax - a) * k7 + 0.5)
        best = min((x for x in (s - 1, s, s + 1) if 0 <= x <= 7), key=lambda x: abs(pal[_AIDX[x]] - a))
        bits |= _AIDX[best] << sh
        sh += 3
    return bytes((amax, amin)) + bits.to_bytes(6, "little")


CLEAR_BC3 = bytes(8) + struct.pack("<HHI", 0, 0, 0)   # alpha 0, colour black: a fully clear block


def encode_bc(rgba, w: int, h: int, bc3: bool) -> bytes:
    """BC3 (bc3=True) or opaque BC1 blocks, row-major. BC1 ignores alpha (the picture must be opaque)."""
    src, pw, ph = _padded(rgba, w, h)
    bx, by = pw // 4, ph // 4
    stride = pw * 4
    out = []
    ap = out.append
    clear16 = bytes(16)
    for j in range(by):
        r0 = j * 4 * stride
        rows = (src[r0:r0 + stride], src[r0 + stride:r0 + 2 * stride], src[r0 + 2 * stride:r0 + 3 * stride],
                src[r0 + 3 * stride:r0 + 4 * stride])
        for i in range(bx):
            a = i * 16
            blk = rows[0][a:a + 16] + rows[1][a:a + 16] + rows[2][a:a + 16] + rows[3][a:a + 16]
            if bc3:
                al = blk[3::4]
                if al == clear16:
                    ap(CLEAR_BC3)
                    continue
                rgb = list(blk)
                del rgb[3::4]
                ap(_alpha_block(al))
                ap(_color_block(rgb, False))
            else:
                rgb = list(blk)
                del rgb[3::4]
                ap(_color_block(rgb, True))
    return b"".join(out)


def bc_bytes(w: int, h: int, bc3: bool) -> int:
    return ((w + 3) // 4) * ((h + 3) // 4) * (16 if bc3 else 8)


# ================================================================================================ layout readers
# Switch layouts (FLYT 8.x and FLAN), written from the public format notes (nn::ui2d sections). Only what the menu
# picture needs is read: the pane tree, picture panes, materials, texture names, user data and animation keys.

class LayoutError(Exception):
    """The layout or animation isn't one this tool reads (the message says why)."""


PANE_KINDS = (b"pan1", b"pic1", b"txt1", b"wnd1", b"prt1", b"bnd1", b"ali1", b"scr1")
STD_UV = (0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0)   # top left, top right, bottom left, bottom right


def _cstr(b: bytes) -> str:
    return bytes(b).split(b"\0", 1)[0].decode("utf-8", "replace")


@dataclass
class Material:
    name: str
    flags: int
    black: tuple
    white: tuple
    texmaps: list = field(default_factory=list)    # (texture index, wrap s, wrap t); wrap 0 clamp, 1 repeat, 2 mirror
    srt: list = field(default_factory=list)        # (translate x, y, rotate, scale x, y)
    texgen: list = field(default_factory=list)     # (matrix, source)
    blend: tuple | None = None                     # (op, source factor, destination factor, logic op)
    proj: list = field(default_factory=list)       # (translate x, y, scale x, y, flags)


@dataclass
class Pane:
    kind: str
    name: str
    flags: int
    origin: int
    alpha: int
    t: tuple
    r: tuple
    s: tuple
    size: tuple
    index: int = 0
    parent: "Pane | None" = None
    children: list = field(default_factory=list)
    colors: list = field(default_factory=list)     # pic1: 4 RGBA corners (top left, top right, bottom left, right)
    material: int = -1
    uvs: list = field(default_factory=list)
    user: dict = field(default_factory=dict)

    @property
    def visible(self) -> bool:
        return bool(self.flags & 1)

    @property
    def influence(self) -> bool:
        """Its alpha also applies to its children."""
        return bool(self.flags & 2)

    def ancestors(self):
        p = self.parent
        while p is not None:
            yield p
            p = p.parent


@dataclass
class Layout:
    width: float
    height: float
    centered: bool
    textures: list
    materials: list
    root: Pane
    panes: list
    by_name: dict

    def texture_of(self, pane: Pane, slot: int = 0) -> str | None:
        if pane.kind != "pic1" or not 0 <= pane.material < len(self.materials):
            return None
        m = self.materials[pane.material]
        if len(m.texmaps) <= slot or not 0 <= m.texmaps[slot][0] < len(self.textures):
            return None
        return self.textures[m.texmaps[slot][0]]

    def material_of(self, pane: Pane) -> Material | None:
        return self.materials[pane.material] if 0 <= pane.material < len(self.materials) else None


def _material(m: bytes) -> Material:
    if len(m) < 0x2C:
        raise LayoutError("a material is cut short")
    flags = struct.unpack_from("<I", m, 0x1C)[0]
    mat = Material(_cstr(m[:0x1C]), flags, tuple(m[0x24:0x28]), tuple(m[0x28:0x2C]))
    p = 0x2C
    try:
        for _ in range(flags & 3):
            idx, fs, ft = struct.unpack_from("<HBB", m, p)
            mat.texmaps.append((idx, fs & 3, ft & 3))
            p += 4
        for _ in range((flags >> 2) & 3):
            mat.srt.append(struct.unpack_from("<5f", m, p))
            p += 20
        for _ in range((flags >> 4) & 3):
            mat.texgen.append((m[p], m[p + 1]))
            p += 16
        p += 4 * ((flags >> 6) & 7)                     # TEV stages
        if flags & 0x200:
            p += 8                                      # alpha compare
        if flags & 0x400:
            mat.blend = tuple(m[p:p + 4])
            p += 4
        if flags & 0x1000:
            p += 4                                      # blend logic / separate alpha blend
        if flags & 0x4000:
            p += 12                                     # indirect
        for _ in range((flags >> 15) & 3):
            tx, ty, sx, sy, f = struct.unpack_from("<4fB", m, p)
            mat.proj.append((tx, ty, sx, sy, f))
            p += 20
        if flags & 0x20000:
            p += 8                                      # font shadow
    except struct.error:
        raise LayoutError(f"material {mat.name!r} is cut short") from None
    if p > len(m):
        raise LayoutError(f"material {mat.name!r} is cut short")
    return mat


def _user_data(body: bytes) -> dict:
    out = {}
    n = struct.unpack_from("<H", body, 8)[0]
    for k in range(n):
        e = 12 + 12 * k
        if e + 12 > len(body):
            raise LayoutError("user data is cut short")
        noff, doff, cnt, typ = struct.unpack_from("<IIHB", body, e)
        if e + noff >= len(body):
            raise LayoutError("user data is cut short")
        name = _cstr(body[e + noff:])
        try:
            if typ == 0:
                out[name] = bytes(body[e + doff:e + doff + cnt]).decode("utf-8", "replace")
            elif typ == 1:
                out[name] = struct.unpack_from(f"<{cnt}i", body, e + doff)
            elif typ == 2:
                out[name] = struct.unpack_from(f"<{cnt}f", body, e + doff)
        except struct.error:
            raise LayoutError("user data is cut short") from None
    return out


def read_flyt(data: bytes) -> Layout:
    """The pane tree, materials and texture names of a Switch layout (FLYT 8.x, little-endian)."""
    if bytes(data[:4]) != b"FLYT":
        raise LayoutError("not a layout file")
    if bytes(data[4:6]) != b"\xff\xfe":
        raise LayoutError("big-endian layout")
    hsize, ver = struct.unpack_from("<HI", data, 6)
    if ver >> 24 != 8:
        raise LayoutError(f"layout version {ver >> 24}.{(ver >> 16) & 255} (this tool reads 8.x)")
    nsec = struct.unpack_from("<H", data, 0x10)[0]
    o = hsize
    width = height = 0.0
    centered = True
    textures: list[str] = []
    materials: list[Material] = []
    panes: list[Pane] = []
    stack: list[Pane] = []
    last: Pane | None = None
    root = None
    for _ in range(nsec):
        if o + 8 > len(data):
            raise LayoutError("layout is cut short")
        magic = bytes(data[o:o + 4])
        size = struct.unpack_from("<I", data, o + 4)[0]
        if size < 8 or o + size > len(data):
            raise LayoutError(f"section {magic!r} is cut short")
        body = bytes(data[o:o + size])
        try:
            if magic == b"lyt1":
                centered = bool(body[8])
                width, height = struct.unpack_from("<2f", body, 12)
            elif magic == b"txl1":
                n = struct.unpack_from("<H", body, 8)[0]
                offs = struct.unpack_from(f"<{n}I", body, 12)
                textures = [_cstr(body[12 + x:]) for x in offs]
            elif magic == b"mat1":
                n = struct.unpack_from("<H", body, 8)[0]
                offs = struct.unpack_from(f"<{n}I", body, 12)
                for k, x in enumerate(offs):
                    end = offs[k + 1] if k + 1 < n else size
                    materials.append(_material(body[x:end]))
            elif magic in PANE_KINDS:
                flags, origin, alpha, _ = body[8:12]
                vals = struct.unpack_from("<10f", body, 0x2C)
                p = Pane(magic.decode()[:4], _cstr(body[12:36]), flags, origin, alpha, vals[0:3], vals[3:6],
                         vals[6:8], vals[8:10], index=len(panes))
                if magic == b"pic1":
                    p.colors = [tuple(body[0x54 + 4 * k:0x58 + 4 * k]) for k in range(4)]
                    p.material, ntc = struct.unpack_from("<HB", body, 0x64)
                    p.uvs = [struct.unpack_from("<8f", body, 0x68 + 32 * k) for k in range(ntc)]
                p.parent = stack[-1] if stack else None
                if p.parent is not None:
                    p.parent.children.append(p)
                elif root is None:
                    root = p
                panes.append(p)
                last = p
            elif magic == b"usd1":
                if last is not None:
                    last.user.update(_user_data(body))
            elif magic == b"pas1":
                if last is None:
                    raise LayoutError("pane children without a pane")
                stack.append(last)
            elif magic == b"pae1":
                if not stack:
                    raise LayoutError("pane tree doesn't match")
                stack.pop()
                last = None
        except struct.error:
            raise LayoutError(f"section {magic!r} is cut short") from None
        o += size
    if root is None or not width or not height:
        raise LayoutError("layout has no panes or no size")
    return Layout(width, height, centered, textures, materials, root, panes, {p.name: p for p in panes})


@dataclass
class Curve:
    step: bool
    keys: list        # (frame, value, slope)

    def at(self, f: float) -> float:
        keys = self.keys
        if f <= keys[0][0]:
            return keys[0][1]
        if f >= keys[-1][0]:
            return keys[-1][1]
        if self.step:
            v = keys[0][1]
            for kf, kv, _ in keys:
                if kf <= f:
                    v = kv
                else:
                    break
            return v
        for i in range(len(keys) - 1):
            f0, v0, s0 = keys[i]
            f1, v1, s1 = keys[i + 1]
            if f0 <= f < f1:
                d = f1 - f0
                t = (f - f0) / d
                t2, t3 = t * t, t * t * t
                return (v0 * (2 * t3 - 3 * t2 + 1) + v1 * (3 * t2 - 2 * t3)
                        + (s0 * (t3 - 2 * t2 + t) + s1 * (t3 - t2)) * d)
        return keys[-1][1]


@dataclass
class Anim:
    name: str
    frames: int
    loop: bool
    panes: dict = field(default_factory=dict)       # pane name -> {(tag, target): Curve}
    materials: dict = field(default_factory=dict)   # material name -> {(tag, index, target): Curve}

    def moves(self, pane: str) -> bool:
        return any(tag == "FLPA" for tag, _ in self.panes.get(pane, {}))


def read_flan(data: bytes, name: str = "") -> Anim:
    """The keys of a Switch layout animation (FLAN 8.x, little-endian): pane and material curves."""
    if bytes(data[:4]) != b"FLAN" or bytes(data[4:6]) != b"\xff\xfe":
        raise LayoutError(f"{name or 'animation'} isn't a little-endian layout animation")
    hsize = struct.unpack_from("<H", data, 6)[0]
    nsec = struct.unpack_from("<H", data, 0x10)[0]
    o = hsize
    anim = None
    try:
        for _ in range(nsec):
            magic = bytes(data[o:o + 4])
            size = struct.unpack_from("<I", data, o + 4)[0]
            if size < 8 or o + size > len(data):
                raise LayoutError(f"{name}: section {magic!r} is cut short")
            if magic == b"pai1":
                frames, loop, _, _ntex, nent, ent_off = struct.unpack_from("<HBBHHI", data, o + 8)
                anim = Anim(name, frames, bool(loop))
                for eo in struct.unpack_from(f"<{nent}I", data, o + ent_off):
                    e = o + eo
                    ename = _cstr(data[e:e + 28])
                    ntag, kind = data[e + 28], data[e + 29]
                    for to in struct.unpack_from(f"<{ntag}I", data, e + 32):
                        t = e + to
                        tag = bytes(data[t:t + 4]).decode("ascii", "replace")
                        ngroups = data[t + 4]
                        for go in struct.unpack_from(f"<{ngroups}I", data, t + 8):
                            g = t + go
                            idx, target, ctype, _, nkeys, _, koff = struct.unpack_from("<BBBBHHI", data, g)
                            if not nkeys:
                                continue
                            if ctype == 2:
                                v = struct.unpack_from(f"<{3 * nkeys}f", data, g + koff)
                                curve = Curve(False, [tuple(v[3 * k:3 * k + 3]) for k in range(nkeys)])
                            elif ctype == 1:
                                curve = Curve(True, [(f, float(val), 0.0) for f, val, _ in
                                                     (struct.unpack_from("<fHH", data, g + koff + 8 * k)
                                                      for k in range(nkeys))])
                            else:
                                continue
                            if kind == 0:
                                anim.panes.setdefault(ename, {})[(tag, target)] = curve
                            elif kind == 1:
                                anim.materials.setdefault(ename, {})[(tag, idx, target)] = curve
            o += size
    except struct.error:
        raise LayoutError(f"{name or 'animation'} is cut short") from None
    if anim is None:
        raise LayoutError(f"{name or 'animation'} has no keys")
    return anim


# ------------------------------------------------------------------------------------------------ pane states

@dataclass
class PaneState:
    visible: bool
    alpha: float
    t: list
    r: list
    s: list
    size: list
    colors: list


def pane_states(lay: Layout, anims: list[tuple[Anim, float]]) -> tuple[dict, dict]:
    """({pane name: PaneState}, {material name: [black RGBA, white RGBA]}) with the animations applied in order."""
    st = {}
    for p in lay.panes:
        st[p.name] = PaneState(p.visible, float(p.alpha), list(p.t), list(p.r), list(p.s), list(p.size),
                               [list(c) for c in p.colors])
    mats = {m.name: [list(m.black), list(m.white)] for m in lay.materials}
    for anim, f in anims:
        for name, curves in anim.panes.items():
            s = st.get(name)
            if s is None:
                continue
            for (tag, target), c in curves.items():
                v = c.at(f)
                if tag == "FLPA":
                    if target < 3:
                        s.t[target] = v
                    elif target < 6:
                        s.r[target - 3] = v
                    elif target < 8:
                        s.s[target - 6] = v
                    elif target < 10:
                        s.size[target - 8] = v
                elif tag == "FLVC":
                    if target == 16:
                        s.alpha = min(255.0, max(0.0, v))
                    elif target < 16 and s.colors:
                        s.colors[target // 4][target % 4] = min(255.0, max(0.0, v))
                elif tag == "FLVI":
                    s.visible = v >= 0.5
        for name, curves in anim.materials.items():
            m = mats.get(name)
            if m is None:
                continue
            for (tag, _, target), c in curves.items():
                if tag == "FLMC" and target < 8:
                    m[target // 4][target % 4] = min(255.0, max(0.0, c.at(f)))
    return st, mats


def _local_rect(origin: int, w: float, h: float) -> tuple[float, float, float, float]:
    """(left, bottom, right, top) of a pane around its position (y up), from its origin bits."""
    ox, oy = origin & 3, (origin >> 2) & 3
    l, r = ((-w / 2, w / 2), (0.0, w), (-w, 0.0))[ox if ox < 3 else 0]
    b, t = ((-h / 2, h / 2), (-h, 0.0), (0.0, h))[oy if oy < 3 else 0]
    return l, b, r, t


def transforms(lay: Layout, st: dict) -> dict:
    """{pane name: (x, y, scale x, scale y)}: layout position = (x + sx * local x, y + sy * local y)."""
    out = {}

    def walk(p: Pane, parent):
        s = st[p.name]
        if parent is None:
            px, py, psx, psy = 0.0, 0.0, 1.0, 1.0
            ax = ay = 0.0
        else:
            px, py, psx, psy = out[parent.name]
            ps = st[parent.name]
            l, b, r, t = _local_rect(parent.origin, ps.size[0], ps.size[1])
            po = p.origin >> 4
            hx, vy = po & 3, (po >> 2) & 3
            ax = (l + r) / 2 if hx == 0 else l if hx == 1 else r
            ay = (b + t) / 2 if vy == 0 else t if vy == 1 else b
        out[p.name] = (px + psx * (ax + s.t[0]), py + psy * (ay + s.t[1]), psx * s.s[0], psy * s.s[1])
        for c in p.children:
            walk(c, p)

    walk(lay.root, None)
    return out


def screen_rect(lay: Layout, st: dict, tf: dict, pane: Pane, scale: float = 1.0) -> tuple:
    """(x0, y0, x1, y1) in screen pixels (y down) of a pane."""
    s = st[pane.name]
    x, y, sx, sy = tf[pane.name]
    l, b, r, t = _local_rect(pane.origin, s.size[0], s.size[1])
    xs = sorted((x + sx * l, x + sx * r))
    ys = sorted((y + sy * b, y + sy * t))
    cx, cy = (lay.width / 2, lay.height / 2) if lay.centered else (0.0, 0.0)
    return ((cx + xs[0]) * scale, (cy - ys[1]) * scale, (cx + xs[1]) * scale, (cy - ys[0]) * scale)


def global_alphas(lay: Layout, st: dict) -> dict:
    """{pane name: alpha 0..1}: a pane's alpha times its parent's when the parent passes its alpha on."""
    out = {}

    def walk(p: Pane, inherited: float):
        a = st[p.name].alpha / 255 * inherited
        out[p.name] = a
        for c in p.children:
            walk(c, a if p.influence else 1.0)

    walk(lay.root, 1.0)
    return out


def global_visible(lay: Layout, st: dict) -> dict:
    out = {}

    def walk(p: Pane, vis: bool):
        v = vis and st[p.name].visible
        out[p.name] = v
        for c in p.children:
            walk(c, v)

    walk(lay.root, True)
    return out


# ================================================================================================ slideshow model

BC1_SRGB, BC3_SRGB = 0x1A06, 0x1C06
CAPTURE_KEYS = ("DynamicCaptureOn", "CaptureOn")
CAPTURE_USES = ("DynamicCaptureUseName", "CaptureUseName")


@dataclass
class Slot:
    texture: str
    pane: str
    fmt: int
    w: int
    h: int
    order: int
    copies: list = field(default_factory=list)
    role: str = "single"            # back (opaque, whole screen), front (over a back), single
    window: tuple = ()              # first and last visible frame
    hold: tuple = ()                # first and last fully visible frame
    rect: tuple = ()                # screen rectangle at the set's mid-hold
    pan: tuple = (0.0, 0.0, 0.0, 0.0)   # x min/max and y min/max shift of the rectangle while it shows
    glow: float = 0.0               # how strongly the menu's glow copies it (0: not copied)


@dataclass
class SlideSet:
    number: int
    window: tuple
    hold: tuple
    mid: float
    slots: list
    tint: tuple = ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))   # pattern black and white colour (linear 0..1) at mid-hold

    def ext(self, w: float, h: float) -> tuple:
        """The screen grown by how far the set's pictures pan, so panning never shows an edge."""
        mx = max([max(abs(s.pan[0]), abs(s.pan[1])) for s in self.slots] + [0.0]) + 2
        my = max([max(abs(s.pan[2]), abs(s.pan[3])) for s in self.slots] + [0.0]) + 2
        return (-mx, -my, w + mx, h + my)


@dataclass
class Pattern:
    pane: str
    texture: str
    scale: tuple
    offset: tuple
    center: tuple


@dataclass
class Model:
    lay: Layout
    loop: Anim
    sets: list
    pattern: Pattern | None
    width: float
    height: float

    def slots(self) -> list:
        return [s for st in self.sets for s in st.slots]

    def art_box(self) -> tuple:
        """Where the picture goes in art mode on a back slot: the first single picture's place (else the left half)."""
        for st in self.sets:
            for s in st.slots:
                if s.role == "single":
                    x0, y0, x1, y1 = s.rect
                    return (max(0.0, x0), max(0.0, y0), min(self.width, x1), min(self.height, y1))
        return (0.0, 0.0, self.width * FULL_SHADE[0], self.height)


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    """Runs of True as (first, last) frames; a run that wraps around the loop end is joined."""
    runs = []
    start = None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(flags) - 1))
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == len(flags) - 1:
        runs[0] = (runs[-1][0], runs[0][1] + len(flags))
        runs.pop()
    return runs


def _overlap(a: tuple, b: tuple) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]) + 1)


def capture_panes(lay: Layout) -> set:
    """Names of the panes inside a capture group (drawn into an off-screen copy for the glow, not on screen)."""
    out = set()
    for p in lay.panes:
        if any(k in p.user for k in CAPTURE_KEYS):
            stack = list(p.children)
            while stack:
                q = stack.pop()
                out.add(q.name)
                stack.extend(q.children)
    return out


def build_model(lay: Layout, anims: dict, textures: dict) -> Model:
    """The slideshow, found from the files: picture slots (textures of picture panes the looping animation moves),
    grouped into sets by when they show; their roles, places at mid-hold, pans and pattern tint. `textures`:
    {name: (format, width, height, mips)} of the layout's texture file. Raises LayoutError when it isn't one."""
    if not lay.centered:
        raise LayoutError("the layout isn't drawn from the centre")
    loops = [a for a in anims.values() if a.loop and a.frames > 0 and any(a.moves(p.name) for p in lay.panes)]
    if not loops:
        raise LayoutError("no looping animation moves a picture")
    loop = max(loops, key=lambda a: a.frames)
    capture = capture_panes(lay)
    n = loop.frames
    frames = list(range(n))

    def alpha_curve(p: Pane) -> list[float] | None:
        """The loop's alpha of a pane (with the animated alphas of the parents that pass theirs on)."""
        chain = [p]
        q = p
        while q.parent is not None and q.parent.influence:
            q = q.parent
            chain.append(q)
        curves = [loop.panes.get(c.name, {}).get(("FLVC", 16)) for c in chain]
        if not any(curves):
            return None
        vals = [1.0] * n
        for c, cv in zip(chain, curves):
            if cv is None:
                if c.alpha < 255:
                    vals = [v * c.alpha / 255 for v in vals]
                continue
            vals = [v * min(255.0, max(0.0, cv.at(f))) / 255 for v, f in zip(vals, frames)]
        return vals

    cand: dict[str, Slot] = {}
    for p in lay.panes:
        if p.kind != "pic1" or p.name in capture or not loop.moves(p.name):
            continue
        tex = lay.texture_of(p)
        if tex is None:
            continue
        curve = alpha_curve(p)
        if curve is None:
            continue
        runs = [r for r in _runs([v > 0.002 for v in curve]) if r[1] - r[0] >= 2]
        if not runs:
            continue
        if len(runs) > 1:
            raise LayoutError(f"picture pane {p.name} shows more than once in the loop")
        window = runs[0]
        full = [curve[f % n] >= 0.998 for f in range(window[0], window[1] + 1)]
        holds = _runs(full) if any(full) else []
        if holds:
            h = max(holds, key=lambda r: r[1] - r[0])
            hold = (window[0] + h[0], window[0] + h[1])
        else:
            hold = window
        if tex in cand:
            raise LayoutError(f"texture {tex} is used by two picture panes the loop shows")
        if tex not in textures:
            raise LayoutError(f"texture {tex} isn't in the layout's texture file")
        fmt, w, h, mips = textures[tex]
        cand[tex] = Slot(tex, p.name, fmt, w, h, p.index, window=window, hold=hold)
    if not cand:
        raise LayoutError("no slideshow found")
    # capture copies (same texture inside a capture group): the glow
    for p in lay.panes:
        if p.kind == "pic1" and p.name in capture:
            tex = lay.texture_of(p)
            if tex in cand:
                cand[tex].copies.append(p.name)
                m = lay.material_of(p)
                if m is not None:
                    c = sum(m.white[:3]) / (3 * 255) - sum(m.black[:3]) / (3 * 255)
                    cand[tex].glow = max(cand[tex].glow, min(1.0, max(0.0, c)))
    # sets: slots that show at the same time
    groups: list[list[Slot]] = []
    for s in sorted(cand.values(), key=lambda s: (s.window[0], s.order)):
        if groups:
            g = groups[-1]
            win = (min(x.window[0] for x in g), max(x.window[1] for x in g))
            shorter = min(win[1] - win[0], s.window[1] - s.window[0]) + 1
            if _overlap(win, s.window) > shorter / 2:
                g.append(s)
                continue
        groups.append([s])
    sets = []
    for number, g in enumerate(groups, 1):
        g.sort(key=lambda s: s.order)
        h0, h1 = max(s.hold[0] for s in g), min(s.hold[1] for s in g)
        if h0 > h1:
            h0, h1 = min(s.hold[0] for s in g), max(s.hold[1] for s in g)
        window = (min(s.window[0] for s in g), max(s.window[1] for s in g))
        sets.append(SlideSet(number, window, (h0, h1), (h0 + h1) / 2, g))
    # places, pans, roles, checks
    W, H = lay.width, lay.height
    for st_ in sets:
        states = {}
        for f in (st_.mid, st_.window[0], st_.window[1]):
            ps, _ = pane_states(lay, [(loop, f % n if f >= n else f)])
            states[f] = (ps, transforms(lay, ps))
        ps_mid, tf_mid = states[st_.mid]
        for s in st_.slots:
            p = lay.by_name[s.pane]
            for q in [p, *p.ancestors()]:
                if any(abs(v) > 1e-3 for v in ps_mid[q.name].r):
                    raise LayoutError(f"picture pane {p.name} is rotated")
            if p.uvs and any(abs(a - b) > 1e-4 for a, b in zip(p.uvs[0], STD_UV)):
                raise LayoutError(f"picture pane {p.name} doesn't show its whole texture")
            m = lay.material_of(p)
            if m is not None and m.srt and any(abs(a - b) > 1e-4 for a, b in zip(m.srt[0], (0, 0, 0, 1, 1))):
                raise LayoutError(f"picture pane {p.name} moves or scales its texture")
            s.rect = screen_rect(lay, ps_mid, tf_mid, p)
            xs, ys = [], []
            for f in (st_.window[0], st_.window[1]):
                r = screen_rect(lay, states[f][0], states[f][1], p)
                xs.append(r[0] - s.rect[0])
                ys.append(r[1] - s.rect[1])
            s.pan = (min(xs + [0.0]), max(xs + [0.0]), min(ys + [0.0]), max(ys + [0.0]))
            if s.fmt not in (BC1_SRGB, BC3_SRGB) or textures[s.texture][3] != 1:
                raise LayoutError(f"texture {s.texture} isn't BC1 or BC3 sRGB with one mip")
        backs = [s for s in st_.slots if s.fmt == BC1_SRGB]
        for s in backs:
            x0, y0, x1, y1 = s.rect
            if not (x0 <= 1 and y0 <= 1 and x1 >= W - 1 and y1 >= H - 1):
                raise LayoutError(f"opaque picture {s.texture} doesn't cover the screen")
            s.role = "back"
        for s in st_.slots:
            if s.fmt == BC3_SRGB:
                s.role = "front" if backs else "single"
                if backs and s.order < min(b.order for b in backs):
                    raise LayoutError(f"picture {s.texture} is drawn under the back picture")
    # the pattern: a full-screen picture pane whose material colour the loop animates
    pattern = None
    for p in lay.panes:
        m = lay.material_of(p)
        if p.kind != "pic1" or p.name in capture or m is None or m.name not in loop.materials:
            continue
        if p.size[0] * p.s[0] < W - 1 or p.size[1] * p.s[1] < H - 1:
            continue
        tex = lay.texture_of(p)
        if tex is None or tex not in textures:
            continue
        sx, sy = (m.srt[0][3], m.srt[0][4]) if m.srt else (1.0, 1.0)
        tx, ty = (m.srt[0][0], m.srt[0][1]) if m.srt else (0.0, 0.0)
        ps, _ = pane_states(lay, [])
        tf = transforms(lay, ps)
        r = screen_rect(lay, ps, tf, p)
        pattern = Pattern(p.name, tex, (sx, sy), (tx, ty), ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2))
        for st_ in sets:
            _, mats = pane_states(lay, [(loop, st_.mid)])
            black, white = mats[m.name]
            st_.tint = (tuple(v / 255 for v in black[:3]), tuple(v / 255 for v in white[:3]))
        break
    return Model(lay, loop, sets, pattern, W, H)


# ================================================================================================ slot pictures

@dataclass
class Place:
    """Where a picture lands on screen: screen = (ox + picture x * s, oy + picture y * s)."""
    s: float
    ox: float
    oy: float


FOCUS_SHARE = {"left": 0.0, "center": 0.5, "right": 1.0}


def fit(pic: Picture, box: tuple, contain: bool, focus: str = "center") -> Place:
    """cover: the picture fills the box (cropped around the focus); contain: it fits whole inside, on the box's bottom
    edge."""
    bx0, by0, bx1, by1 = box
    bw, bh = bx1 - bx0, by1 - by0
    f = FOCUS_SHARE.get(focus, 0.5)
    if contain:
        s = min(bw / pic.w, bh / pic.h)
        return Place(s, bx0 + (bw - pic.w * s) * f, by1 - pic.h * s)
    s = max(bw / pic.w, bh / pic.h)
    return Place(s, bx0 - (pic.w - bw / s) * f * s, by0 - (pic.h - bh / s) * 0.5 * s)


def source_box(rect: tuple, place: Place) -> tuple:
    """The part of the picture (its pixels) that falls on a screen rectangle."""
    x0, y0, x1, y1 = rect
    return ((x0 - place.ox) / place.s, (y0 - place.oy) / place.s, (x1 - place.ox) / place.s, (y1 - place.oy) / place.s)


def intersect(a: tuple, b: tuple) -> tuple:
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    if r[2] - r[0] < 1 or r[3] - r[1] < 1:
        return a
    return r


def edges_on_screen(box: tuple, pan: tuple, w: float, h: float) -> dict:
    """Which edges of a box come inside the screen while it pans (those get a soft edge)."""
    x0, y0, x1, y1 = box
    dx0, dx1, dy0, dy1 = pan

    def in_x(x):
        return x + dx1 > EDGE_TOL and x + dx0 < w - EDGE_TOL

    def in_y(y):
        return y + dy1 > EDGE_TOL and y + dy0 < h - EDGE_TOL

    return {"left": in_x(x0), "right": in_x(x1), "top": in_y(y0), "bottom": in_y(y1)}


def feather(img: Picture, rect: tuple, box: tuple, sides: dict, visible: tuple) -> None:
    """Soft edges (alpha) on the given sides of a screen box, for a slot texture covering the screen rect."""
    vw, vh = visible[2] - visible[0], visible[3] - visible[1]
    ex, ey = [], []
    if sides["left"]:
        ex.append((box[0], FEATHER["left"] * vw, 1))
    if sides["right"]:
        ex.append((box[2], FEATHER["right"] * vw, -1))
    if sides["top"]:
        ey.append((box[1], FEATHER["top"] * vh, 1))
    if sides["bottom"]:
        ey.append((box[3], FEATHER["bottom"] * vh, -1))
    if not ex and not ey:
        return
    fx = ramp(img.w, rect[0], (rect[2] - rect[0]) / img.w, ex)
    fy = ramp(img.h, rect[1], (rect[3] - rect[1]) / img.h, ey)
    mul_alpha(img, fx, fy)


def shade_gains(img_w: int, rect: tuple, screen_w: float) -> list[float]:
    """full mode: per texel column, how much light is kept (darker towards the right of the screen)."""
    start, end = FULL_SHADE[0] * screen_w, FULL_SHADE[1]
    k = (rect[2] - rect[0]) / img_w
    out = []
    for i in range(img_w):
        x = rect[0] + (i + 0.5) * k
        t = 0.0 if x <= start else min(1.0, (x - start) / (screen_w - start))
        out.append(1.0 - (1.0 - end) * t)
    return out


def flatten(pic: Picture) -> Picture:
    """The picture over black, opaque (full mode: a cut-out's clear parts become black)."""
    out = premultiply(pic)
    out.px[3::4] = b"\xff" * (pic.w * pic.h)
    out.cutout = False
    return out


def set_opaque(img: Picture) -> None:
    img.px[3::4] = b"\xff" * (img.w * img.h)


def bake_pattern(pattern: Pattern | None, plane: tuple | None, tint: tuple, rect: tuple, tw: int, th: int) -> Picture:
    """The menu's own pattern, as it shows behind a slot covering the screen rect: the grey texture repeated at the
    material's scale around the pane's centre, coloured between the set's black and white tint (linear)."""
    black, white = tint
    luts = [bytes(lin_to_srgb(black[c] + (white[c] - black[c]) * v / 255) for v in range(256)) for c in range(3)]
    img = blank(tw, th, (0, 0, 0, 255))
    if pattern is None or plane is None:
        for c in range(3):
            img.px[c::4] = bytes((luts[c][0],)) * (tw * th)
        return img
    plane_v = pattern_values(pattern, plane, rect, tw, th)
    for c in range(3):
        img.px[c::4] = plane_v.translate(luts[c])
    return img


def pattern_values(pattern: Pattern, plane: tuple, rect: tuple, tw: int, th: int, unit: float = 1.0) -> bytes:
    """The grey pattern texture as it falls on a screen rect (tw x th samples): one texel per layout unit, scaled by
    the material's texture scale around the pane's centre (that is how the menu maps it; checked on a screenshot),
    repeated. `unit`: screen pixels per layout unit."""
    vals, pw, ph = plane
    sx, sy = pattern.scale
    cx, cy = pattern.center
    kx, ky = (rect[2] - rect[0]) / tw, (rect[3] - rect[1]) / th
    us = [pw / 2 + ((rect[0] + (i + 0.5) * kx) / unit - cx) * sx - 0.5 - pattern.offset[0] * pw for i in range(tw)]
    u0 = [math.floor(u) % pw for u in us]
    u1 = [(i + 1) % pw for i in u0]
    wu = [int((u - math.floor(u)) * 256) for u in us]
    g0, g1 = itemgetter(*u0), itemgetter(*u1)
    if tw == 1:
        g0, g1 = (lambda r, f=g0: (f(r),)), (lambda r, f=g1: (f(r),))
    rows = []
    for j in range(th):
        v = ph / 2 + ((rect[1] + (j + 0.5) * ky) / unit - cy) * sy - 0.5 + pattern.offset[1] * ph
        v0 = math.floor(v)
        wv = int((v - v0) * 256)
        ra = vals[(v0 % ph) * pw:(v0 % ph + 1) * pw]
        rb = vals[((v0 + 1) % ph) * pw:((v0 + 1) % ph + 1) * pw]
        row = [(a * (256 - wv) + b * wv) >> 8 for a, b in zip(ra, rb)]
        rows.append(bytes((a * (256 - w) + b * w + 128) >> 8 for a, b, w in zip(g0(row), g1(row), wu)))
    return b"".join(rows)


def composite(top: Picture, base: Picture) -> Picture:
    """top over base (opaque), blended in linear light."""
    L = _SRGB2LIN
    K = (_LIN_STEPS - 1) / 255
    S = _LIN2SRGB
    out = bytearray(base.px)
    alpha = top.px[3::4]
    for c in range(3):
        out[c::4] = bytes(t if a == 255 else b if a == 0 else S[int((L[t] * a + L[b] * (255 - a)) * K + 0.5)]
                          for t, b, a in zip(top.px[c::4], base.px[c::4], alpha))
    out[3::4] = b"\xff" * (top.w * top.h)
    return Picture(top.w, top.h, out)


def make_slots(model: Model, pics: list[Picture], mode: str, numbers: set, focus: str = "center",
               plane: tuple | None = None) -> dict:
    """{texture name: RGBA8 picture at the slot's size, or None for a clear slot} for the chosen sets."""
    out: dict = {}
    W, H = model.width, model.height
    art = model.art_box()
    flat: dict[int, Picture] = {}
    for st in model.sets:
        if st.number not in numbers:
            continue
        pic = pics[(st.number - 1) % len(pics)]
        ext = st.ext(W, H)
        screen = (0.0, 0.0, W, H)
        for s in st.slots:
            R, tw, th = s.rect, s.w, s.h
            if s.role == "front":
                out[s.texture] = None
                continue
            if mode == "full":
                src = pic
                if pic.cutout:
                    src = flat.setdefault(id(pic), flatten(pic))
                img = sample(src, source_box(R, fit(src, ext, False, focus)), tw, th, "clamp")
                apply_gain_columns(img, shade_gains(tw, R, W))
                if s.role == "single":
                    feather(img, R, R, edges_on_screen(R, s.pan, W, H), intersect(R, screen))
                else:
                    set_opaque(img)
            elif s.role == "single":
                F = intersect(R, ext)
                img = sample(pic, source_box(R, fit(pic, F, pic.cutout, focus)), tw, th,
                             "clear" if pic.cutout else "clamp")
                if pic.cutout:
                    soften_alpha(img, max(1, round(SOFTEN_PX * tw / max(1.0, R[2] - R[0]))))
                    push_pull(img)
                else:
                    feather(img, R, R, edges_on_screen(R, s.pan, W, H), intersect(R, screen))
            else:   # art mode, a back slot: the pattern, with the picture where the first single picture is
                FA = intersect(art, ext)
                top = sample(pic, source_box(R, fit(pic, FA, pic.cutout, focus)), tw, th, "clear")
                if pic.cutout:
                    soften_alpha(top, 1)
                else:
                    feather(top, R, art, edges_on_screen(art, s.pan, W, H), intersect(art, screen))
                img = composite(top, bake_pattern(model.pattern, plane, st.tint, R, tw, th))
            apply_gain(img, 1 / (1 + GLOW_GAIN * s.glow))
            out[s.texture] = img
    return out


def encode_slots(model: Model, images: dict) -> dict:
    """{texture name: BC level bytes} for the slot pictures (clear slots: one clear block repeated)."""
    slots = {s.texture: s for s in model.slots()}
    out = {}
    for name, img in images.items():
        s = slots[name]
        bc3 = s.fmt == BC3_SRGB
        if img is None:
            out[name] = CLEAR_BC3 * (bc_bytes(s.w, s.h, True) // 16)
        else:
            out[name] = encode_bc(img.px, s.w, s.h, bc3)
    return out


# ================================================================================================ the menu file

@dataclass
class MenuFile:
    source: str             # "game", or the id of the library mod it comes from
    source_name: str        # "the game", or that mod's name
    data: bytes             # Pack/Title.pack as read
    pack: wc.Sarc
    pack_files: list
    archive_name: str       # the layout archive inside the pack
    yaz_align: int | None   # its Yaz0 alignment field (None: stored uncompressed)
    archive: bytes          # the archive, unpacked
    arc: wc.Sarc
    arc_files: list
    bntx_name: str
    textures: object        # nx_bntx TextureSet
    model: Model
    anims: dict

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def texture(self, name: str):
        return next(t for t in self.textures.textures if t.name == name)

    def pattern_plane(self) -> tuple | None:
        p = self.model.pattern
        if p is None:
            return None
        t = self.texture(p.texture)
        img = decode_texture(t.format, t.width, t.height, t.levels[0], t.channels)
        return bytes(img.px[0::4]), t.width, t.height


def _align_of(offset: int) -> int:
    return (offset & -offset) if offset else 4


def unknown_menu(source_name: str, why: str) -> MenuBgError:
    return MenuBgError(f"The menu file ({PACK_REL}) isn't one this tool knows. It comes from {source_name}. ({why})")


def open_menu(data: bytes, source: str = "game", source_name: str = "the game") -> MenuFile:
    """Unpack a Pack/Title.pack and find its slideshow. Raises MenuBgError when it isn't a menu file this tool knows."""
    try:
        pack = wc.sarc_parse(data)
        pack_files = wc.sarc_files(data)
        arcs = [(n, d) for n, d in pack_files if n.lower().endswith((".sblarc", ".blarc"))]
        if len(arcs) != 1:
            raise unknown_menu(source_name, "it should hold one layout archive")
        archive_name, comp = arcs[0]
        if bytes(comp[:4]) == b"Yaz0":
            archive, yaz_align = wc.yaz0_decompress(bytes(comp))
        else:
            archive, yaz_align = bytes(comp), None
        arc = wc.sarc_parse(archive)
        arc_files = wc.sarc_files(archive)
    except wc.FormatError as e:
        raise unknown_menu(source_name, str(e)) from None
    layouts = [(n, d) for n, d in arc_files if n.lower().startswith("blyt/") and n.lower().endswith(".bflyt")]
    bntxs = [(n, d) for n, d in arc_files if n.lower().endswith(".bntx")]
    if len(layouts) != 1 or len(bntxs) != 1:
        raise unknown_menu(source_name, "it should hold one layout and one texture file")
    try:
        lay = read_flyt(layouts[0][1])
        anims = {}
        for n, d in arc_files:
            if n.lower().endswith(".bflan"):
                try:
                    anims[n] = read_flan(d, n)
                except LayoutError:
                    pass
        textures = nx_bntx.read(bytes(bntxs[0][1]), bntxs[0][0])
        info = {t.name: (t.format, t.width, t.height, t.mips) for t in textures.textures}
        model = build_model(lay, anims, info)
    except (LayoutError, ModelError, ValueError) as e:
        raise unknown_menu(source_name, str(e)) from None
    return MenuFile(source, source_name, data, pack, pack_files, archive_name, yaz_align, archive, arc, arc_files,
                    bntxs[0][0], textures, model, anims)


def build_pack(menu: MenuFile, levels: dict, say_step=None) -> tuple[bytes, int]:
    """(new Pack/Title.pack, size of the new unpacked archive) with the given texture levels replaced. Names,
    formats, sizes and every other file stay as they were."""
    from dataclasses import replace
    ts = menu.textures
    texs = [replace(t, levels=[levels[t.name]]) if t.name in levels else t for t in ts.textures]
    old = dict(menu.arc_files)[menu.bntx_name]
    new_bntx = nx_bntx.write(replace(ts, textures=texs))
    if len(new_bntx) != len(old):
        raise MenuBgError("The new texture file came out a different size. Nothing was changed.")
    files = [(n, new_bntx if n == menu.bntx_name else d) for n, d in menu.arc_files]
    archive = wc.sarc_build(files, menu.arc.order, align={"bntx": 0x1000}, hash_key=menu.arc.hash_key,
                            data_align=_align_of(menu.arc.data_offset))
    if say_step:
        say_step()
    comp = wc.yaz0_compress(archive, menu.yaz_align) if menu.yaz_align is not None else archive
    pfiles = [(n, comp if n == menu.archive_name else d) for n, d in menu.pack_files]
    pack = wc.sarc_build(pfiles, menu.pack.order, hash_key=menu.pack.hash_key,
                         data_align=_align_of(menu.pack.data_offset))
    return pack, len(archive)


def self_check(menu: MenuFile, pack: bytes, levels: dict, images: dict) -> float:
    """Re-open the new pack: every other file and texture byte-identical, the new ones as encoded, and one new
    picture decodes close to what was meant. Returns that picture's PSNR (dB, inf when there is none)."""
    new = open_menu(pack, "new", "the new menu file")
    old_files = dict(menu.arc_files)
    for n, d in new.arc_files:
        if n != menu.bntx_name and old_files.get(n) != d:
            raise MenuBgError(f"Self-check failed: {n} changed. Nothing was changed in your library.")
    if sorted(n for n, _ in new.arc_files) != sorted(n for n, _ in menu.arc_files):
        raise MenuBgError("Self-check failed: the files in the menu archive changed. Nothing was changed.")
    old = {t.name: t for t in menu.textures.textures}
    for t in new.textures.textures:
        o = old.get(t.name)
        want = [levels[t.name]] if t.name in levels else (o.levels if o else None)
        if o is None or (t.format, t.width, t.height, t.channels) != (o.format, o.width, o.height, o.channels) \
                or t.levels != want:
            raise MenuBgError(f"Self-check failed: texture {t.name} isn't as written. Nothing was changed.")
    pics = [(n, img) for n, img in images.items() if img is not None]
    if not pics:
        return float("inf")
    name, img = min(pics, key=lambda x: x[1].w * x[1].h)
    t = new.texture(name)
    got = decode_texture(t.format, t.width, t.height, t.levels[0])
    if t.format == BC1_SRGB:
        return psnr(got.px, img.px, (0, 1, 2))
    return psnr(got.px, img.px)


# ------------------------------------------------------------------------------------------------ size table (RSTB)

def rstb_value(data: bytes, name: str) -> int | None:
    """A file's entry in a size table (Yaz0 or plain), or None when it has none."""
    if bytes(data[:4]) == b"Yaz0":
        data = wc.yaz0_decompress(bytes(data))[0]
    crcs, named = wc.rstb_load(data)
    for n, v in named:
        if n.rstrip(b"\0").decode("utf-8", "replace") == name:
            return v
    crc = zlib.crc32(name.encode()) & 0xFFFFFFFF
    for c, v in crcs:
        if c == crc:
            return v
    return None


# ================================================================================================ library and game

@dataclass
class Game:
    install: str
    romfs: str
    build: str


def open_lib(library: str | None, create: bool) -> modman.Library | None:
    """The mod library as modman opens it (--library, else SWMOD_LIBRARY, else next to the Savage Wind folder)."""
    if create:
        return modman.open_library(argparse.Namespace(library=library), create=True)
    return modman.Library.load(modman.resolve_library(library))


def check_game(install: str) -> Game:
    romfs = os.path.join(install, "extracted", "romfs")
    info = modman.game_info(install)
    if not info.ids or "main" not in info.ids or not os.path.isdir(romfs):
        raise MenuBgError("The game files aren't set up. Start Savage Wind and press Set up game files.")
    bid = info.ids["main"][:16].lower()
    if bid != BUILD_190:
        v = modman.mi.KNOWN_BUILDS.get(bid)
        raise MenuBgError(f"This tool works with BotW 1.9.0 only. Your game is {v or 'a version this tool does not know'}"
                          f" (build {bid}).")
    return Game(install, romfs, bid)


def dlc_menu_files(install: str) -> list[str]:
    """A DLC folder's own copy of the menu file (not supported yet)."""
    ext = os.path.join(install, "extracted")
    hits = []
    try:
        names = os.listdir(ext)
    except OSError:
        return []
    for n in names:
        top = os.path.join(ext, n)
        if not modman.AOC_RE.fullmatch(n) or not os.path.isdir(top):
            continue
        for d, _, files in os.walk(top):
            for f in files:
                rel = os.path.relpath(os.path.join(d, f), top).replace("\\", "/").lower()
                if rel.endswith(("pack/title.pack", "layout/title.sblarc")):
                    hits.append(os.path.join(d, f))
    return hits


def mod_files(lib: modman.Library, m: dict, rels: tuple[str, ...]) -> dict:
    """{rel: file} of the RomFS files a library mod ships (as deploy sees them), for the given RomFS paths."""
    root = os.path.join(lib.mods_dir, m["id"])
    if not os.path.isdir(root):
        return {}
    try:
        mod = rm.load_mod(root)
    except (rm.DeployError, OSError, ValueError):
        return {}
    want = {r.lower(): r for r in rels}
    out = {}
    try:
        for e in mod.entries:
            if not e.dlc and e.rel.lower() in want and e.src:
                out[want[e.rel.lower()]] = e.src
    finally:
        if mod.zip is not None:
            mod.zip.close()
    return out


@dataclass
class Base:
    source: str
    name: str
    path: str
    rstbs: list            # (mod name, size table file) of the enabled mods that ship one
    menu_mods: list        # ids of the enabled mods (not ours) that ship the menu file


def find_base(lib: modman.Library | None, game: Game) -> Base:
    """The menu file the picture goes into: the last enabled library mod (not ours) that ships one, else the game's."""
    base = Base("game", "the game", os.path.join(game.romfs, *PACK_REL.split("/")), [], [])
    for m in (lib.enabled() if lib else []):
        if m["id"] == MOD_ID:
            continue
        files = mod_files(lib, m, (PACK_REL, RSTB_REL))
        if PACK_REL in files:
            base.source, base.name, base.path = m["id"], m["name"], files[PACK_REL]
            base.menu_mods.append(m["id"])
        if RSTB_REL in files:
            base.rstbs.append((m["name"], files[RSTB_REL]))
    if not os.path.isfile(base.path):
        raise MenuBgError(f"The menu file ({PACK_REL}) is missing from {base.name}. Set up the game files in Savage "
                          "Wind again.")
    return base


def size_limit(game: Game, base: Base, rstb_name: str) -> tuple[int | None, list[str]]:
    """The smallest size-table entry of the menu archive (the game's table and every enabled mod's), and notes."""
    values, notes = [], []
    game_rstb = os.path.join(game.romfs, *RSTB_REL.split("/"))
    for who, path in [("the game", game_rstb)] + base.rstbs:
        try:
            with open(path, "rb") as f:
                v = rstb_value(f.read(), rstb_name)
        except FileNotFoundError:
            continue
        except (OSError, wc.FormatError) as e:
            notes.append(f"Couldn't read the size table of {who}: {e}")
            continue
        if v is None:
            if who != "the game":
                notes.append(f"{who}'s size table has no entry for the menu file (the game then sizes it itself).")
        else:
            values.append(v)
    return (min(values) if values else None), notes


def toml_str(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out) + '"'


def manifest_text(name: str) -> str:
    return (f'[mod]\nid = "{MOD_ID}"\nname = {toml_str(name)}\nversion = "{TOOL_VERSION}"\nauthors = ["{AUTHOR}"]\n'
            'description = "Your own picture on the main menu (made by tools/menu_bg.py)."\n'
            'game_versions = ["1.9.0"]\n\n[data]\nromfs = "romfs"\n')


def mod_name(pics: list[Picture], given: str | None) -> str:
    if given:
        name = modman.one_line(given).strip()
    else:
        name = f"Menu picture: {pics[0].name}" + (f" and {len(pics) - 1} more" if len(pics) > 1 else "")
    name = modman.one_line(name).strip() or "Menu picture"
    return name if len(name) <= 100 else name[:97] + "..."


def sets_text(numbers: list[int], total: int) -> str:
    if len(numbers) == total:
        return f"all {total} slideshow pictures"
    if len(numbers) == 1:
        return f"slideshow picture {numbers[0]}"
    return "slideshow pictures " + ", ".join(str(n) for n in numbers)


def parse_sets(text: str, total: int) -> list[int]:
    t = (text or "all").strip().lower()
    if t == "all":
        return list(range(1, total + 1))
    out = []
    for part in t.replace(" ", "").split(","):
        if not part.isdigit() or not 1 <= int(part) <= total:
            raise MenuBgError(f"--sets {text}: the menu has {total} slideshow pictures, so use numbers 1 to {total} "
                              "(like --sets 2 or --sets 1,3,5), or all.")
        if int(part) not in out:
            out.append(int(part))
    return sorted(out)


def write_mod(folder: str, pack: bytes, info: dict, name: str) -> None:
    os.makedirs(os.path.join(folder, "romfs", *PACK_REL.split("/")[:-1]), exist_ok=True)
    with open(os.path.join(folder, "romfs", *PACK_REL.split("/")), "wb") as f:
        f.write(pack)
    with open(os.path.join(folder, modman.MANIFEST), "w", encoding="utf-8", newline="\n") as f:
        f.write(manifest_text(name))
    with open(os.path.join(folder, INFO_FILE), "w", encoding="utf-8", newline="\n") as f:
        json.dump(info, f, indent=2)
        f.write("\n")


def put_in_library(lib: modman.Library, folder: str, name: str, menu_mods: list[str]) -> dict:
    """Add (or replace) the menu-picture mod, turned on, in its old place or after the mods that ship a menu file."""
    old = next((i for i, m in enumerate(lib.mods) if m["id"] == MOD_ID), None)
    if old is not None:
        modman.remove_mod(lib, MOD_ID)
    entry, _, _ = modman.add_mod(lib, folder, name)
    lib.mods = [m for m in lib.mods if m["id"] != MOD_ID]
    target = len(lib.mods) if old is None else min(old, len(lib.mods))
    after = [i for i, m in enumerate(lib.mods) if m["id"] in menu_mods]
    if after and target <= max(after):
        target = max(after) + 1
    entry["enabled"] = True
    lib.mods.insert(target, entry)
    lib.save()
    return entry


def deploy_now(lib: modman.Library, no_deploy: bool) -> tuple[bool, list[str]]:
    """Deploy when the game is closed and no game code rebuild is needed. (deployed, lines to print)."""
    if no_deploy:
        return False, ["Not deployed (--no-deploy). To use it: python tools/modman.py deploy"]
    if modman.game_running():
        return False, ["Close the game, then deploy: python tools/modman.py deploy"]
    dry = modman.deploy(lib, dry_run=True)
    parts = dry.get("parts") or {}
    if (parts.get("exefs") or {}).get("rebuild") == "build" or (parts.get("hooks") or {}).get("rebuild") == "build":
        return False, [f"Not deployed: your other mods need the game code rebuilt first ({modman.REBUILD_TIME}). "
                       "To do it: python tools/modman.py deploy"]
    launch = modman.deploy(lib)
    lines = []
    att = launch.get("attention") or []
    if att:
        lines.append(f"Deploy note: {att[0]}" + (f" (and {len(att) - 1} more: python tools/modman.py deploy "
                                                 "--dry-run shows them)" if len(att) > 1 else ""))
    return True, lines


# ================================================================================================ preview renderer
# A small renderer for the panes the menu uses: picture panes with their material colours, vertex colours, alpha and
# blend mode, the projected pattern, the glow (capture groups drawn small, blurred, added) and plain bars where the
# menu words go. Light is blended in linear space and written as sRGB.

MENU_WORDS = 4          # the menu shows 4 words (Continue, New Game, Options, Quit)
WORD_BAR = (85.0, 90.0, 14.0)   # bar right edge from the part's centre, width, height (layout units)


class Frame:
    def __init__(self, w: int, h: int, rgb=(0.0, 0.0, 0.0), alpha: float = 0.0):
        self.w, self.h = w, h
        self.rows = [[rgb[0], rgb[1], rgb[2]] * w for _ in range(h)]
        self.alpha = [[alpha] * w for _ in range(h)]

    def picture(self, linear: bool = False) -> Picture:
        out = bytearray()
        S, K = _LIN2SRGB, _LIN_STEPS - 1
        for row, arow in zip(self.rows, self.alpha):
            px = bytearray(self.w * 4)
            for c in range(3):
                vals = row[c::3]
                if linear:
                    px[c::4] = bytes(min(255, max(0, int(v * 255 + 0.5))) for v in vals)
                else:
                    px[c::4] = bytes(S[min(K, max(0, int(v * K + 0.5)))] for v in vals)
            px[3::4] = bytes(min(255, max(0, int(a * 255 + 0.5))) for a in arow)
            out += px
        return Picture(self.w, self.h, out)


def box_blur(frame: Frame, radius: int, passes: int = 3) -> None:
    def blur_line(vals: list, stride: int, k: int) -> list:
        n = len(vals) // stride
        out = [0.0] * len(vals)
        for c in range(stride):
            ch = vals[c::stride]
            pad = ch[:1] * k + ch + ch[-1:] * k
            acc = list(accumulate(pad, initial=0.0))
            out[c::stride] = [(acc[i + 2 * k + 1] - acc[i]) / (2 * k + 1) for i in range(n)]
        return out

    for _ in range(passes):
        frame.rows = [blur_line(r, 3, radius) for r in frame.rows]
        frame.alpha = [blur_line(a, 1, radius) for a in frame.alpha]
        cols = list(zip(*frame.rows))
        cols = [blur_line(list(c), 1, radius) for c in cols]
        frame.rows = [list(r) for r in zip(*cols)]
        acols = [blur_line(list(c), 1, radius) for c in zip(*frame.alpha)]
        frame.alpha = [list(r) for r in zip(*acols)]


class Renderer:
    def __init__(self, menu: MenuFile, overrides: dict | None = None):
        self.menu = menu
        self.lay = menu.model.lay
        self.overrides = overrides or {}
        self.cache: dict = {}
        self.fmt = {t.name: t.format for t in menu.textures.textures}
        self.plane = None
        loop = menu.model.loop
        # fades that run once when the menu opens (alpha and colour keys only): shown at their end
        self.fades = [a for a in menu.anims.values()
                      if not a.loop and a is not loop and a.panes
                      and all(tag == "FLVC" for c in a.panes.values() for tag, _ in c) and not a.materials]
        # language variants: the animation that swaps textures (by region) also shows or hides panes; a pane it
        # switches is shown when it holds a full-colour picture (the logo most players see)
        self.variant: dict[str, bool] = {}
        for a in menu.anims.values():
            if a is loop or not any(tag == "FLTP" for c in a.materials.values() for tag, _, _ in c):
                continue
            for name, curves in a.panes.items():
                if any(tag == "FLVI" for tag, _ in curves) and name in self.lay.by_name:
                    self.variant[name] = self._has_colour_picture(self.lay.by_name[name])

    def _has_colour_picture(self, p: Pane) -> bool:
        stack = [p]
        while stack:
            q = stack.pop()
            tex = self.lay.texture_of(q)
            if tex and self.fmt.get(tex, 0) >> 8 == 0x0B:
                return True
            stack.extend(q.children)
        return False

    def texture(self, name: str) -> tuple[Picture, bool]:
        """(RGBA8 picture, is sRGB) of a texture, with the slot pictures of the preview in place."""
        if name in self.overrides:
            return self.overrides[name], True
        if name not in self.cache:
            t = self.menu.texture(name)
            self.cache[name] = (decode_texture(t.format, t.width, t.height, t.levels[0], t.channels),
                                t.format & 0xFF == 6)
        return self.cache[name]

    def render(self, frame_no: float, scale: float = 1.0) -> Picture:
        lay = self.lay
        anims = [(a, float(a.frames)) for a in self.fades] + [(self.menu.model.loop, frame_no)]
        self.st, self.mats = pane_states(lay, anims)
        for name, show in self.variant.items():
            self.st[name].visible = show
        self.tf = transforms(lay, self.st)
        self.alphas = global_alphas(lay, self.st)
        self.vis = global_visible(lay, self.st)
        self.captures: dict = {}
        self.capture_set = capture_panes(lay)
        w, h = int(round(lay.width * scale)), int(round(lay.height * scale))
        fb = Frame(w, h)
        self.words = []
        self._draw(lay.root, fb, scale, top=True)
        for p in self.words[:MENU_WORDS]:
            self._bar(fb, p, scale)
        return fb.picture()

    def _draw(self, p: Pane, fb: Frame, scale: float, top: bool) -> None:
        if not self.vis.get(p.name, False):
            return
        if top and any(k in p.user for k in CAPTURE_KEYS):
            return   # (drawn into its capture only)
        if p.kind == "pic1":
            self._pic(p, fb, scale)
        elif p.kind == "prt1" and top:
            self.words.append(p)
        for c in p.children:
            self._draw(c, fb, scale, top)

    def _capture(self, root_name: str, scale: float) -> Frame:
        key = (root_name, scale)
        if key not in self.captures:
            root = self.lay.by_name[root_name]
            cs = float((root.user.get("CaptureScale") or (0.25,))[0])
            bg = [v / 255 for v in (root.user.get("CaptureBGColor") or (0, 0, 0))[:3]]
            w = max(1, int(round(self.lay.width * scale * cs)))
            h = max(1, int(round(self.lay.height * scale * cs)))
            cap = Frame(w, h, tuple(bg), 0.0)
            for c in root.children:
                self._draw(c, cap, scale * cs, False)
            box_blur(cap, max(1, int(round(2 * scale))))
            self.captures[key] = cap
        return self.captures[key]

    def _pic(self, p: Pane, fb: Frame, scale: float) -> None:
        lay = self.lay
        m = lay.material_of(p)
        if m is None:
            return
        black, white = self.mats.get(m.name, [list(m.black), list(m.white)])
        ga = self.alphas[p.name]
        if ga * max(black[3], white[3]) / 255 < 1 / 255:
            return
        rect = screen_rect(lay, self.st, self.tf, p, scale)
        ix0, iy0 = max(0, int(math.floor(rect[0] + 0.5))), max(0, int(math.floor(rect[1] + 0.5)))
        ix1, iy1 = min(fb.w, int(math.floor(rect[2] + 0.5))), min(fb.h, int(math.floor(rect[3] + 0.5)))
        if ix1 <= ix0 or iy1 <= iy0:
            return
        rw, rh = ix1 - ix0, iy1 - iy0
        use = next((p.user[k] for k in CAPTURE_USES if k in p.user), None)
        pat = self.menu.model.pattern
        uv = p.uvs[0] if p.uvs else STD_UV
        if m.texgen and m.texgen[0][1] >= 3 and pat is not None and pat.pane == p.name:
            if self.plane is None:
                self.plane = self.menu.pattern_plane()
            vals = pattern_values(pat, self.plane, (ix0, iy0, ix1, iy1), rw, rh, unit=scale)
            img = Picture(rw, rh, bytearray(rw * rh * 4))
            img.px[0::4] = img.px[1::4] = img.px[2::4] = vals
            img.px[3::4] = b"\xff" * (rw * rh)
            srgb = False
        else:
            if use and use in lay.by_name:
                src, srgb = self._capture(use, scale).picture(linear=True), False
                uv = STD_UV
            else:
                tex = lay.texture_of(p)
                if tex is None:
                    return
                src, srgb = self.texture(tex)
            u0, v0, u1, v1 = min(uv[0::2]), min(uv[1::2]), max(uv[0::2]), max(uv[1::2])
            if src.px == src.px[0:4] * (src.w * src.h):
                img = Picture(rw, rh, bytearray(bytes(src.px[0:4]) * (rw * rh)))
            else:
                def fx(x):
                    return (u0 + (u1 - u0) * (x - rect[0]) / (rect[2] - rect[0])) * src.w

                def fy(y):
                    return (v0 + (v1 - v0) * (y - rect[1]) / (rect[3] - rect[1])) * src.h

                img = resample(src, (fx(ix0), fy(iy0), fx(ix1), fy(iy1)), rw, rh)
        self._blend(fb, img, srgb, (ix0, iy0, ix1, iy1), rect, black, white, p, ga, m.blend)

    def _blend(self, fb: Frame, img: Picture, srgb: bool, region, rect, black, white, p: Pane, ga: float, blend):
        L = _SRGB2LIN
        dec = L if srgb else [v / 255 for v in range(256)]
        luts = [[black[c] / 255 + (white[c] - black[c]) / 255 * dec[v] for v in range(256)] for c in range(3)]
        alut = [(black[3] + (white[3] - black[3]) * v / 255) / 255 * ga for v in range(256)]
        ix0, iy0, ix1, iy1 = region
        rw = ix1 - ix0
        cols = self.st[p.name].colors or [[255.0] * 4] * 4
        flat = all(c == cols[0] for c in cols)
        op, sf, df = (blend[0], blend[1], blend[2]) if blend else (1, 4, 5)
        for y in range(iy0, iy1):
            j = y - iy0
            trow = img.px[j * rw * 4:(j + 1) * rw * 4]
            a = [alut[t] for t in trow[3::4]]
            src = [[luts[c][t] for t in trow[c::4]] for c in range(3)]
            if not flat:
                ty = min(1.0, max(0.0, (y + 0.5 - rect[1]) / max(1e-6, rect[3] - rect[1])))
                left = [cols[0][k] + (cols[2][k] - cols[0][k]) * ty for k in range(4)]
                right = [cols[1][k] + (cols[3][k] - cols[1][k]) * ty for k in range(4)]
                txs = [min(1.0, max(0.0, (x + 0.5 - rect[0]) / max(1e-6, rect[2] - rect[0]))) for x in range(ix0, ix1)]
                a = [v * (left[3] + (right[3] - left[3]) * t) / 255 for v, t in zip(a, txs)]
                for c in range(3):
                    src[c] = [v * (left[c] + (right[c] - left[c]) * t) / 255 for v, t in zip(src[c], txs)]
            elif cols[0] != [255.0] * 4:
                ka = cols[0][3] / 255
                a = [v * ka for v in a]
                for c in range(3):
                    kc = cols[0][c] / 255
                    src[c] = [v * kc for v in src[c]]
            row = fb.rows[y]
            for c in range(3):
                d = row[ix0 * 3 + c:ix1 * 3:3]
                s = src[c]
                if op == 1 and df == 1:
                    new = [min(1.0, dv + sv * av) for dv, sv, av in zip(d, s, a)]
                elif op == 3:
                    new = [max(0.0, dv - sv * av) for dv, sv, av in zip(d, s, a)]
                elif op == 2:
                    new = [max(0.0, sv * av - dv) for dv, sv, av in zip(d, s, a)]
                else:
                    new = [sv * av + dv * (1 - av) for dv, sv, av in zip(d, s, a)]
                row[ix0 * 3 + c:ix1 * 3:3] = new
            arow = fb.alpha[y]
            arow[ix0:ix1] = [max(da, av) for da, av in zip(arow[ix0:ix1], a)]

    def _bar(self, fb: Frame, p: Pane, scale: float) -> None:
        x, y, _, _ = self.tf[p.name]
        cx, cy = self.lay.width / 2, self.lay.height / 2
        right = (cx + x + WORD_BAR[0]) * scale
        x0, x1 = int(right - WORD_BAR[1] * scale), int(right)
        y0, y1 = int((cy - y - WORD_BAR[2] / 2) * scale), int((cy - y + WORD_BAR[2] / 2) * scale)
        for yy in range(max(0, y0), min(fb.h, y1)):
            row = fb.rows[yy]
            for xx in range(max(0, x0), min(fb.w, x1)):
                for c in range(3):
                    row[xx * 3 + c] = row[xx * 3 + c] * 0.2 + 0.8


DIGITS = {"1": ("010", "110", "010", "010", "111"), "2": ("111", "001", "111", "100", "111"),
          "3": ("111", "001", "111", "001", "111"), "4": ("101", "101", "111", "001", "001"),
          "5": ("111", "100", "111", "001", "111"), "6": ("111", "100", "111", "101", "111"),
          "7": ("111", "001", "010", "010", "010"), "8": ("111", "101", "111", "101", "111"),
          "9": ("111", "101", "111", "001", "111"), "0": ("111", "101", "101", "101", "111")}


def draw_number(pic: Picture, text: str, x: int, y: int, cell: int = 12) -> None:
    """Big white digits with a black border (the sheet's set numbers)."""
    for k, ch in enumerate(text):
        glyph = DIGITS.get(ch)
        if not glyph:
            continue
        gx = x + k * 4 * cell
        for colour, grow in ((b"\x00\x00\x00\xff", cell // 3), (b"\xff\xff\xff\xff", 0)):
            for gy, line in enumerate(glyph):
                for gxx, bit in enumerate(line):
                    if bit != "1":
                        continue
                    for yy in range(y + gy * cell - grow, y + (gy + 1) * cell + grow):
                        if 0 <= yy < pic.h:
                            x0 = max(0, gx + gxx * cell - grow)
                            x1 = min(pic.w, gx + (gxx + 1) * cell + grow)
                            if x1 > x0:
                                o = (yy * pic.w + x0) * 4
                                pic.px[o:o + (x1 - x0) * 4] = colour * (x1 - x0)


def sheet(frames: list[tuple[int, Picture]], cols: int = 2) -> Picture:
    fw, fh = frames[0][1].w, frames[0][1].h
    rows = (len(frames) + cols - 1) // cols
    out = blank(fw * cols, fh * rows, (0, 0, 0, 255))
    for k, (number, pic) in enumerate(frames):
        ox, oy = (k % cols) * fw, (k // cols) * fh
        for y in range(fh):
            o = ((oy + y) * out.w + ox) * 4
            out.px[o:o + fw * 4] = pic.px[y * fw * 4:(y + 1) * fw * 4]
        draw_number(out, str(number), ox + 16, oy + 16)
    return out


# ================================================================================================ commands

def lower_priority() -> None:
    """Keep the PC light: BelowNormal priority on Windows, nice elsewhere."""
    try:
        if os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)
        else:
            os.nice(5)
    except Exception:  # (not allowed here: run at normal priority)
        pass


class Steps:
    def __init__(self, total: int):
        self.n, self.total = 0, total

    def __call__(self, text: str) -> None:
        self.n += 1
        say(f"Step {self.n} of {self.total}: {text}")


def took(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60} min {s % 60} s" if s >= 60 else f"{s} s"


def estimate(kind: str, fast: bool) -> str:
    t = {"read": ("", " (about 10 s a picture)"), "fit": (" (about 10 s)", " (about 45 s)"),
         "encode": " (about 25 s)", "pack": " (about 30 s)"}[kind]
    return t if isinstance(t, str) else t[0 if fast else 1]


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def cmd_set(args) -> int:
    t0 = time.monotonic()
    lib = open_lib(args.library, create=True)
    install = lib.require_install()
    game = check_game(install)
    if dlc_menu_files(install):
        raise MenuBgError("Your DLC has its own menu file. That isn't supported yet.")
    fast = pillow() is not None
    step = Steps(5)
    step(("reading your pictures" if len(args.pictures) > 1 else "reading your picture") + estimate("read", fast))
    pics = [load_picture(p, args.mode) for p in args.pictures]
    notes = []
    for p in pics:
        if args.mode == "full" and (p.w < SOFT_FULL[0] or p.h < SOFT_FULL[1]):
            notes.append(f"Note: {p.name} is smaller than the screen ({p.w} x {p.h}), so it will look soft.")
        if args.mode == "full" and p.cutout:
            notes.append(f"Note: {p.name} has see-through parts. In full mode they are black.")
    step("reading the menu file")
    base = find_base(lib, game)
    menu = open_menu(_read(base.path), base.source, base.name)
    if base.source != "game":
        say(f"Building on the menu file of {base.name}.")
    numbers = parse_sets(args.sets, len(menu.model.sets))
    limit, more = size_limit(game, base, wc.resource_path("", menu.archive_name))
    notes += more
    step("fitting your picture" + estimate("fit", fast))
    images = make_slots(menu.model, pics, args.mode, set(numbers), args.focus, menu.pattern_plane())
    step("compressing the textures" + estimate("encode", fast))
    levels = encode_slots(menu.model, images)
    step("packing the menu file" + estimate("pack", fast))
    pack, size = build_pack(menu, levels)
    if limit is not None and size > limit:
        raise MenuBgError("The new menu file would be too big for the game's size table. Nothing was changed.")
    quality = self_check(menu, pack, levels, images)
    name = mod_name(pics, args.name)
    info = {"tool": TOOL_ID, "version": TOOL_VERSION, "made": datetime.datetime.now().isoformat(timespec="seconds"),
            "mode": args.mode, "focus": args.focus, "sets": numbers, "slideshow": len(menu.model.sets),
            "pictures": [{"name": p.name, "sha256": p.sha256, "size": [p.w, p.h], "cutout": p.cutout} for p in pics],
            "game_build": game.build, "base": {"source": base.source, "name": base.name, "sha256": menu.sha256},
            "textures": sorted(levels), "self_check_db": None if quality == float("inf") else round(quality, 1)}
    tmp = tempfile.mkdtemp(prefix="menu-bg-")
    try:
        folder = os.path.join(tmp, "menu-picture")
        write_mod(folder, pack, info, name)
        scan = modman.scan_mod(folder, modman.game_info(install), name)
        if scan.kinds != ["romfs"] or scan.problems or scan.manifest_errors:
            raise MenuBgError("The menu-picture mod didn't pass modman's check: "
                              + "; ".join(scan.manifest_errors + scan.problems or [f"kinds {scan.kinds}"]))
        put_in_library(lib, folder, name, base.menu_mods)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    what = sets_text(numbers, len(menu.model.sets))
    if not args.no_deploy and not modman.game_running():
        say("Deploying...")
    deployed, lines = deploy_now(lib, args.no_deploy)
    if deployed:
        say(f"Done: your picture is on the main menu ({what}).")
        say("Deployed. Start the game to see it.")
    else:
        say(f"Done: the menu picture is in your library ({what}).")
    for line in lines + notes:
        say(line)
    say(f"Took {took(time.monotonic() - t0)}.")
    return 0


def cmd_reset(args) -> int:
    lib = open_lib(args.library, create=False)
    if lib is None or not any(m["id"] == MOD_ID for m in lib.mods):
        say("No menu picture in the library. Nothing to do.")
        return 0
    lib.require_install()
    modman.remove_mod(lib, MOD_ID)
    deployed, lines = deploy_now(lib, args.no_deploy)
    say("Done: the menu picture is gone from your library.")
    if deployed:
        say("Deployed. The game's own menu is back.")
    for line in lines:
        say(line)
    return 0


def cmd_status(args) -> int:
    root = modman.resolve_library(args.library)
    lib = modman.Library.load(root)
    entry = next((m for m in (lib.mods if lib else []) if m["id"] == MOD_ID), None)
    if entry is None:
        say("No menu picture in the library.")
        return 0
    info = modman.read_json(os.path.join(lib.mods_dir, MOD_ID, INFO_FILE)) or {}
    pics = ", ".join(p.get("name", "?") for p in info.get("pictures", [])) or "?"
    total = info.get("slideshow") or len(info.get("sets") or [])
    what = sets_text(info.get("sets") or [], total) if info.get("sets") else "?"
    say(f"Menu picture: {'on' if entry['enabled'] else 'off'}, mode {info.get('mode', '?')}, {what}, picture: {pics}.")
    pos = lib.mods.index(entry)
    later = []
    for m in lib.mods[pos + 1:]:
        if m.get("enabled") and PACK_REL in mod_files(lib, m, (PACK_REL,)):
            later.append(m["name"])
    if later:
        say(f"{', '.join(later)} comes after it and also changes the menu, so it wins. Run set again to use it as "
            "the base.")
    launch = modman.read_json(os.path.join(lib.deploy_dir, modman.LAUNCH)) or {}
    if any(m.get("id") == MOD_ID for m in launch.get("mods", [])):
        say("Deployed.")
    else:
        say("Not deployed yet: python tools/modman.py deploy")
    return 0


def cmd_preview(args) -> int:
    out = os.path.abspath(args.out)
    if not out.lower().endswith(".png"):
        raise MenuBgError("--out must be a .png file.")
    lib = open_lib(args.library, create=False)
    install = lib.require_install() if lib else modman.default_install()
    if not lib and not modman.looks_like_install(install):
        raise MenuBgError("No mod library yet. Run: python tools/modman.py init")
    for folder in (install, lib.root if lib else None):
        if folder and modman._inside(out, folder):
            raise MenuBgError(f"Write the preview outside {folder} (preview only reads the game and the library).")
    game = check_game(install)
    base = find_base(lib, game)
    menu = open_menu(_read(base.path), base.source, base.name)
    model = menu.model
    total = len(model.sets)
    if args.set == "all":
        numbers = list(range(1, total + 1))
    elif args.set:
        numbers = parse_sets(args.set, total)[:1]
    else:
        numbers = [next((st.number for st in model.sets if any(s.role == "single" for s in st.slots)), 1)]
    overrides = {}
    if args.pictures:
        pics = [load_picture(p, args.mode) for p in args.pictures]
        images = make_slots(model, pics, args.mode, set(numbers), args.focus, menu.pattern_plane())
        slots = {s.texture: s for s in model.slots()}
        for name, data in encode_slots(model, images).items():
            s = slots[name]
            overrides[name] = decode_texture(s.fmt, s.w, s.h, data)
    r = Renderer(menu, overrides)
    if args.set == "all":
        pic = sheet([(st.number, r.render(st.mid, 0.5)) for st in model.sets if st.number in numbers])
    else:
        pic = r.render(model.sets[numbers[0] - 1].mid, 1.0)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    write_png(out, pic)
    say(f"Done: the preview is in {out}.")
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python tools/menu_bg.py", description="Put your own picture on the game's "
                                 "main menu (as a mod in your mod library).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, priority=True):
        p.add_argument("--library", help="the mod library (default: SWMOD_LIBRARY, else next to Savage Wind)")
        if priority:
            p.add_argument("--priority", choices=("low", "normal"), default="low",
                           help="normal: don't lower this tool's priority")

    s = sub.add_parser("set", help="put your picture on the main menu")
    s.add_argument("pictures", nargs="+", metavar="picture")
    s.add_argument("--mode", choices=MODES, default="art", help="art: next to the menu (default); full: whole screen")
    s.add_argument("--name", help="the mod's name in the library")
    s.add_argument("--sets", default="all", help="which slideshow pictures: all (default), or numbers like 2 or 1,3,5")
    s.add_argument("--focus", choices=FOCUS, default="center", help="which part to keep when cropping")
    s.add_argument("--no-deploy", action="store_true", help="only put it in the library")
    common(s)
    r = sub.add_parser("reset", help="take the menu picture out again")
    r.add_argument("--no-deploy", action="store_true")
    common(r, priority=False)
    p = sub.add_parser("preview", help="draw a menu frame to a PNG (changes nothing)")
    p.add_argument("pictures", nargs="*", metavar="picture")
    p.add_argument("--out", required=True, help="the PNG to write")
    p.add_argument("--mode", choices=MODES, default="art")
    p.add_argument("--set", help="slideshow picture 1-7, or all (a sheet); default: the first one next to the menu")
    p.add_argument("--focus", choices=FOCUS, default="center")
    common(p)
    st = sub.add_parser("status", help="is a menu picture on, and which")
    common(st, priority=False)
    return ap


COMMANDS = {"set": cmd_set, "reset": cmd_reset, "preview": cmd_preview, "status": cmd_status}


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    args = parser().parse_args(argv)
    if args.cmd in ("set", "preview") and args.priority != "normal":
        lower_priority()
    try:
        return COMMANDS[args.cmd](args)
    except (MenuBgError, modman.ModmanError, rm.DeployError) as e:
        say(str(e))
        return 2
    except OSError as e:
        say(f"Error: {e}")
        return 2
    except KeyboardInterrupt:
        say("Stopped.")
        return 130


# ================================================================================================ window entry point
# For tools/modman_gui.py (one button): pick pictures, pick a mode, then run `set` in a child process inside a
# modal window. The child keeps the window smooth and is the only part that runs at lower priority.

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GUI_COLORS = {"bg": "#ffffff", "fg": "#000000", "button": "#e3e8ef", "active": "#c7d4e5", "ok": "#00661a",
              "error": "#b00000", "focus": "#0045d0"}


def console_python() -> str:
    """python.exe for the child, also when the window runs under pythonw.exe."""
    exe = sys.executable or "python"
    folder, base = os.path.split(exe)
    if base.lower() == "pythonw.exe" and os.path.isfile(os.path.join(folder, "python.exe")):
        return os.path.join(folder, "python.exe")
    return exe


def gui_filetypes() -> list:
    if pillow() is None:
        return [("PNG pictures", "*.png"), ("All files", "*.*")]
    return [("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp"), ("PNG", "*.png"), ("JPG", "*.jpg *.jpeg"),
            ("WEBP", "*.webp"), ("BMP", "*.bmp"), ("All files", "*.*")]


def gui_command(files: list[str], mode: str, library: str) -> list[str]:
    return [console_python(), "-u", SCRIPT, "set", *files, "--mode", mode, "--library", library]


def gui_result(code: int, lines: list[str]) -> tuple[str, str]:
    """('ok' or 'error', the headline) for the child's exit code and output."""
    if code == 0:
        return "ok", next((x for x in lines if x.startswith("Done:")), "Done.")
    if code == 130:
        return "error", "Stopped."
    last = next((x for x in reversed(lines) if x.strip() and not x.startswith("Step ")), "")
    return "error", f"It didn't work: {last}" if last else "It didn't work."


def gui_pick_and_set(parent, library: str, *, size: int = 18, colors: dict | None = None, done=None) -> None:
    """File dialog, a mode question, then `set` in a modal window. Never raises into Tk."""
    t = {**GUI_COLORS, **(colors or {})}
    try:
        from tkinter import filedialog
        files = filedialog.askopenfilenames(parent=parent, title="Pick your menu picture (one or several)",
                                            filetypes=gui_filetypes())
        if not files:
            return
        mode = _gui_ask_mode(parent, size, t)
        if mode:
            _gui_run(parent, gui_command(list(files), mode, library), size, t, done)
    except Exception as e:  # (a window must never get an exception from here)
        try:
            from tkinter import messagebox
            messagebox.showerror("Menu picture", f"The menu picture tool couldn't run: {e}", parent=parent)
        except Exception:
            pass


def _gui_button(tk, parent, text: str, command, size: int, t: dict):
    return tk.Button(parent, text=text, command=command, font=("Segoe UI", size, "bold"), bg=t["button"], fg=t["fg"],
                     activebackground=t["active"], activeforeground=t["fg"], padx=size, pady=size // 3, bd=2)


def _gui_ask_mode(parent, size: int, t: dict) -> str | None:
    import tkinter as tk
    top = tk.Toplevel(parent, bg=t["bg"], padx=24, pady=20)
    top.title("Menu picture")
    top.transient(parent)
    result = {"mode": None}

    def pick(mode):
        result["mode"] = mode
        top.destroy()

    tk.Label(top, text="Where should your picture go?", font=("Segoe UI", size, "bold"), bg=t["bg"],
             fg=t["fg"]).pack(anchor="w")
    row = tk.Frame(top, bg=t["bg"])
    row.pack(anchor="w", pady=(16, 0))
    for text, mode in (("Next to the menu", "art"), ("Whole screen", "full"), ("Cancel", None)):
        _gui_button(tk, row, text, lambda m=mode: pick(m), size, t).pack(side="left", padx=(0, 10))
    top.bind("<Escape>", lambda e: pick(None))
    top.grab_set()
    parent.wait_window(top)
    return result["mode"]


def _gui_run(parent, cmd: list[str], size: int, t: dict, done) -> None:
    import queue
    import threading
    import tkinter as tk
    top = tk.Toplevel(parent, bg=t["bg"], padx=20, pady=16)
    top.title("Menu picture")
    top.transient(parent)
    head = tk.Label(top, text="Making your menu picture. This takes about 2 minutes.", font=("Segoe UI", size, "bold"),
                    bg=t["bg"], fg=t["fg"], anchor="w", justify="left", wraplength=size * 40)
    head.pack(fill="x")
    text = tk.Text(top, font=("Segoe UI", size), bg=t["bg"], fg=t["fg"], height=10, width=60, wrap="word",
                   relief="flat", state="disabled")
    text.pack(fill="both", expand=True, pady=10)
    lines: list[str] = []
    q: queue.Queue = queue.Queue()
    state = {"running": True}

    def close():
        if state["running"]:
            return
        top.destroy()
        if done:
            try:
                done()
            except Exception:
                pass

    btn = _gui_button(tk, top, "Close", close, size, t)
    btn.configure(state="disabled")
    btn.pack(anchor="e")
    top.protocol("WM_DELETE_WINDOW", close)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", creationflags=NO_WINDOW, env=env)
    except OSError as e:
        state["running"] = False
        head.configure(text=f"It didn't work: {e}", fg=t["error"])
        btn.configure(state="normal")
        return

    def reader():
        for line in proc.stdout:
            q.put(line.rstrip("\n"))
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()

    def poll():
        finished = False
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                break
            if item is None:
                finished = True
                break
            lines.append(item)
            text.configure(state="normal")
            text.insert("end", item + "\n")
            text.see("end")
            text.configure(state="disabled")
        if finished:
            code = proc.wait()
            kind, headline = gui_result(code, lines)
            head.configure(text=headline, fg=t["ok"] if kind == "ok" else t["error"])
            state["running"] = False
            btn.configure(state="normal")
            btn.focus_set()
        else:
            top.after(100, poll)

    top.grab_set()
    top.after(100, poll)


if __name__ == "__main__":
    sys.exit(main())
