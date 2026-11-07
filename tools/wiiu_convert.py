#!/usr/bin/env python3
"""Convert the data files of a Wii U BotW mod to the Switch's byte order and folder layout.

    python tools/wiiu_convert.py check <mod folder or .zip> [--json] [--game <romfs>]
    python tools/wiiu_convert.py convert <mod folder or .zip> --out <new folder> [--drop-unconverted] [--game <romfs>]
    python tools/wiiu_convert.py verify <game romfs folder> --out <report folder> [--count N] [--groups G]

`check` says what can be converted and what can't, without writing anything. `convert` writes a Switch-layout mod
(romfs/ and 01007EF00011F001/romfs/, as tools/romfs_mods.py and modman read it) plus wiiu-convert-report.txt.
`verify` proves the converters lose nothing, on your own Switch files: each sampled file goes Switch -> Wii U ->
Switch and must come back the same. It only reads the game folder and writes one report to --out.

Wii U mods keep base game files in content/ and DLC files in aoc/0010/ (a Cemu graphic pack has them in
<pack>/content/). Where the two versions differ only in byte order and layout, the file is converted:
SARC packs (inside Yaz0 too, and nested), BYML (maps, actor info, game data...), MSBT texts. AAMP files (actor
parameters) are little-endian on both, so they are used as they are. Models (X.sbfres, with their skeletal and
material animations), textures (X.Tex1 + X.Tex2 become one X.Tex.sbfres) and item icons go through tools/wiiu_model;
--game (your game's RomFS, read only; modman passes it) adds your vanilla materials, mipmaps, TexInfo.txt, packs and
size table entries. Audio, Havok physics, shaders, effects, fonts, movies, layouts and event flows are made
differently for each console: they are kept unchanged and reported, as are files of unknown formats. The Wii U size
table (RSTB) is left out: its sizes are Wii U sizes. UKMM (Switch mode) stays the better route for merging mods.
"""
from __future__ import annotations

import argparse
import array
import json
import os
import random
import re
import shutil
import struct
import sys
import time
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from romfs_mods import _bad_name, _cemu_pack  # noqa: E402  (the same rules as the mod installer)
from wiiu_model import convert as wm_convert  # noqa: E402  (Wii U models and textures)
from wiiu_model import nx_bfres, nx_model, wiiu_bfres, wiiu_models  # noqa: E402
from wiiu_model.errors import Damaged as ModelDamaged, ModelError  # noqa: E402
from wiiu_model.neutral import describe as model_describe  # noqa: E402

BE, LE = ">", "<"
ORDER_NAME = {BE: "big-endian (Wii U)", LE: "little-endian (Switch)"}
BASE_OUT = "romfs"
DLC_OUT = "01007EF00011F001/romfs"
REPORT = "wiiu-convert-report.txt"
VERIFY_REPORT = "wiiu-roundtrip-report"

# What happened to a file
CONVERTED = "converted"
SAME = "same"            # same bytes on both consoles: used as is
ALREADY = "already"      # already in the target byte order
PARTLY = "partly"        # a pack that was converted, holding files that weren't
CANT = "cant"            # made differently for each console: kept unchanged
UNKNOWN = "unknown"      # format we don't know: kept unchanged
BROKEN = "broken"        # damaged or a variant we don't support: kept unchanged
LEFT_OUT = "left-out"    # not written (the RSTB; a model a pack can't hold)
REPLACED = "game-file"   # a model or texture that can't be converted: your game's own file is used in its place
OK = {CONVERTED, SAME, ALREADY}
NOTHING = ("nothing could be converted", "nothing to convert")  # verdicts of a mod with no usable game file
LABEL = {CONVERTED: "converted", SAME: "same on both", ALREADY: "already Switch", PARTLY: "converted in part",
         CANT: "can't convert", UNKNOWN: "unknown format", BROKEN: "broken/unsupported", LEFT_OUT: "left out",
         REPLACED: "your game's file"}
STATUS_ORDER = [CONVERTED, SAME, ALREADY, PARTLY, CANT, UNKNOWN, BROKEN, REPLACED, LEFT_OUT]

RSTB_NOTE = ("left out: it holds Wii U resource sizes, and the Switch's are different, so swapping its byte order "
             "would give the game wrong sizes. The game's own RSTB is used. If the mod adds files or makes them "
             "bigger, merge it with UKMM (Switch mode) instead")

# Formats made differently for each console, by magic (at the start of the file) and by file extension
PLATFORM_MAGIC = {
    b"FRES": "model/texture", b"BNTX": "model/texture", b"Gfx2": "model/texture",
    b"FSTM": "audio", b"FWAV": "audio", b"BARS": "audio", b"FSAR": "audio", b"AGST": "audio", b"BLAL": "audio",
    b"FSTP": "audio", b"FGRP": "audio", b"FWSD": "audio", b"BWAV": "audio",
    b"PTCL": "effect", b"VFXB": "effect", b"EFTB": "effect", b"SPBD": "effect",
    b"FFNT": "font", b"CFNT": "font", b"RFNT": "font",
    b"FLYT": "UI layout", b"FLAN": "UI layout", b"FLIM": "UI layout",
    b"BFEV": "event flow", b"XLNK": "effect/sound link", b"PrOD": "object placement (PrOD)", b"TSCB": "terrain",
    b"BNSH": "shader", b"\x57\xe0\xe0\x57": "Havok physics",
}
# Wii U BFRES files (models, Tex1/Tex2 textures, item icons) go through tools/wiiu_model; by extension when the magic
# can't tell (a damaged one is then reported as such)
MODEL_EXT = {"bfres", "bitemico"}
PLATFORM_EXT = {
    "model/texture": {"bntx", "gtx", "bflim", "bmaptex", "bstftex", "breviewtex"},
    "audio": {"bfstm", "bfwav", "bars", "bfsar", "bwav", "bagst", "blal", "bfgrp", "bfwsd", "bfstp", "baatn",
              "baacl", "baroc"},
    "shader": {"sharc", "sharcb", "sharcfb", "bfsha", "bnsh", "bushvt"},
    "effect": {"ptcl", "esetlist", "bptcl"},
    "font": {"bffnt", "bfotf", "bfttf"},
    "movie": {"mp4", "webm", "mov", "mkv", "bfmov"},
    "UI layout": {"bflyt", "bflan"},
    "event flow": {"bfevfl", "bfevtm"},
    "effect/sound link": {"belnk", "bslnk"},
    "object placement (PrOD)": {"blwp"},
    "terrain": {"hght", "mate", "extm", "tscb", "tera"},
    "ecosystem": {"beco"},
}
EXT_KIND = {ext: kind for kind, exts in PLATFORM_EXT.items() for ext in exts}
WHY = {
    "model/texture": "this model/texture format isn't converted (only Wii U BFRES models, Tex1/Tex2 textures and "
                     "item icons are)",
    "audio": "audio is encoded differently for each console",
    "Havok physics": "Havok files are built for each console",
    "shader": "shaders are compiled for each console's GPU",
    "effect": "effects hold GPU data built for each console",
    "font": "fonts are built for each console",
    "movie": "Wii U movies are a different video format",
    "UI layout": "layout byte order isn't converted (not supported yet)",
    "event flow": "event flow byte order isn't converted (not supported yet)",
    "effect/sound link": "link database byte order isn't converted (not supported yet)",
    "object placement (PrOD)": "PrOD byte order isn't converted (not supported yet)",
    "terrain": "terrain byte order isn't converted (not supported yet)",
    "ecosystem": "ecosystem byte order isn't converted (not supported yet)",
}
TEXT_EXT = {"txt", "json", "xml", "yml", "yaml", "csv", "ini", "fxparam", "md"}
JUNK = {"thumbs.db", "desktop.ini", "__macosx", ".ds_store"}

YAZ0_WINDOW = 0x1000
YAZ0_MAX = 0x111
SARC_ALIGN_CAP = 0x2000


class FormatError(Exception):
    """A file that isn't what its magic says, is damaged, or uses a variant we don't support."""


class ConvertError(Exception):
    """A mod we can't work on (wrong layout, bad output folder...). The message is for the player. It follows the
    mod's name ("<mod> is a Switch mod..."), unless `whole` is set: then it names its own path (the output folder,
    say) and stands alone."""

    def __init__(self, message: str, *, whole: bool = False):
        super().__init__(message)
        self.whole = whole


# ---------------------------------------------------------------------------------------------------------- helpers

def _swap16(buf: bytearray, off: int) -> None:
    buf[off], buf[off + 1] = buf[off + 1], buf[off]


def _swap24(buf: bytearray, off: int) -> None:
    buf[off], buf[off + 2] = buf[off + 2], buf[off]


def _swap32(buf: bytearray, off: int) -> None:
    buf[off:off + 4] = buf[off:off + 4][::-1]


def _swap64(buf: bytearray, off: int) -> None:
    buf[off:off + 8] = buf[off:off + 8][::-1]


_U32 = "I" if array.array("I").itemsize == 4 else "L"


def _swap32_run(buf: bytearray, off: int, count: int) -> None:
    """Swap `count` 32-bit values in a row."""
    if count > 0:
        a = array.array(_U32, bytes(buf[off:off + 4 * count]))
        a.byteswap()
        buf[off:off + 4 * count] = a.tobytes()


def _swap16_run(buf: bytearray, off: int, count: int) -> None:
    if count > 0:
        a = array.array("H", bytes(buf[off:off + 2 * count]))
        a.byteswap()
        buf[off:off + 2 * count] = a.tobytes()


def _need(data: bytes, off: int, size: int, what: str) -> None:
    if off < 0 or size < 0 or off + size > len(data):
        raise FormatError(f"{what} at 0x{off:x} runs past the end of the file")


def _u16(data: bytes, off: int, order: str) -> int:
    return struct.unpack_from(order + "H", data, off)[0]


def _u32(data: bytes, off: int, order: str) -> int:
    return struct.unpack_from(order + "I", data, off)[0]


def _align_up(n: int, a: int) -> int:
    return (n + a - 1) // a * a if a > 1 else n


def _alignment_of(offset: int, cap: int = SARC_ALIGN_CAP) -> int:
    """The largest power of two (up to cap) that divides offset: the alignment a file had in a pack."""
    return cap if offset == 0 else min(offset & -offset, cap)


def _ext(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base.rsplit(".", 1)[-1] if "." in base else ""


def _renamed(path: str, new_base: str) -> str:
    """path with its last part replaced."""
    return path.rsplit("/", 1)[0] + "/" + new_base if "/" in path else new_base


def _header_align(data: bytes) -> int:
    """The alignment an uncompressed Switch BFRES / BNTX asks for (1 << header byte 0x0E; 0x1000 for models and
    textures), else 1."""
    if len(data) >= 0x10 and data[:4] in (b"FRES", b"BNTX") and data[0x0C:0x0E] == b"\xff\xfe" and data[0x0E] <= 13:
        return 1 << data[0x0E]
    return 1


# ------------------------------------------------------------------------------------------------------------- Yaz0
# Header: "Yaz0", u32 size, u32 alignment, 4 reserved bytes - big-endian on Wii U and on Switch alike.

def yaz0_decompress(data: bytes) -> tuple[bytes, int]:
    """(decompressed data, the header's alignment field)."""
    if len(data) < 16 or data[:4] != b"Yaz0":
        raise FormatError("not Yaz0")
    size, align = struct.unpack_from(">II", data, 4)
    out = bytearray()
    src, i = data, 16
    try:
        while len(out) < size:
            code = src[i]
            i += 1
            if code == 0xFF and size - len(out) >= 8:  # eight literal bytes (incompressible data): one slice
                if i + 8 > len(src):
                    raise IndexError
                out += src[i:i + 8]
                i += 8
                continue
            for bit in (0x80, 0x40, 0x20, 0x10, 8, 4, 2, 1):
                if code & bit:
                    out.append(src[i])
                    i += 1
                else:
                    b1, b2 = src[i], src[i + 1]
                    i += 2
                    dist = ((b1 & 0x0F) << 8 | b2) + 1
                    n = b1 >> 4
                    if n:
                        n += 2
                    else:
                        n = src[i] + 0x12
                        i += 1
                    start = len(out) - dist
                    if start < 0:
                        raise FormatError("Yaz0 data refers to bytes before its start")
                    if dist >= n:
                        out += out[start:start + n]
                    else:  # the copy overlaps what it writes: repeat the last `dist` bytes
                        out += (bytes(out[start:]) * (n // dist + 1))[:n]
                if len(out) >= size:
                    break
    except IndexError:
        raise FormatError("Yaz0 data is cut short") from None
    del out[size:]
    return bytes(out), align


def yaz0_size(data: bytes) -> int:
    """The decompressed size of a Yaz0 file (from its header; len() of a plain one)."""
    return struct.unpack_from(">I", data, 4)[0] if data[:4] == b"Yaz0" and len(data) >= 16 else len(data)


def yaz0_store(data: bytes, align: int = 0) -> bytes:
    """Yaz0 holding every byte as a literal: a valid file made at once (verify uses it as a mod's compressed file)."""
    n = len(data)
    pad = bytes(data) + bytes(-n % 8)
    k = len(pad) // 8
    body = bytearray(9 * k)
    body[0::9] = bytes((0xFF,)) * k
    for j in range(8):
        body[1 + j::9] = pad[j::8]
    return b"Yaz0" + struct.pack(">II", n, align) + bytes(4) + bytes(body)


def yaz0_compress(data: bytes, align: int = 0) -> bytes:
    """Yaz0 with a greedy longest-match search (valid for the game; the bytes differ from Nintendo's encoder)."""
    n = len(data)
    out = bytearray(b"Yaz0" + struct.pack(">II", n, align) + bytes(4))
    find = data.rfind
    i = 0
    while i < n:
        flag_at = len(out)
        out.append(0)
        flags = 0
        for bit in (0x80, 0x40, 0x20, 0x10, 8, 4, 2, 1):
            if i >= n:
                break
            best = where = 0
            if i and n - i >= 3:
                lo = i - YAZ0_WINDOW if i > YAZ0_WINDOW else 0
                # a match starts before i (it may run into i: the decoder copies byte by byte)
                pos = find(data[i:i + 3], lo, i + 2)
                if pos >= 0:
                    top = min(YAZ0_MAX, n - i)
                    good, bad, where, probe = 3, top + 1, pos, 4
                    while good < top:  # gallop up, then halve the gap
                        size = min(probe, top)
                        pos = find(data[i:i + size], lo, i - 1 + size)
                        if pos < 0:
                            bad = size
                            break
                        good, where = size, pos
                        probe *= 2
                    while bad - good > 1:
                        mid = (good + bad) // 2
                        pos = find(data[i:i + mid], lo, i - 1 + mid)
                        if pos >= 0:
                            good, where = mid, pos
                        else:
                            bad = mid
                    best = good
            if best >= 3:
                dist = i - where - 1
                if best >= 0x12:
                    out += bytes((dist >> 8, dist & 0xFF, best - 0x12))
                else:
                    out += bytes((((best - 2) << 4) | (dist >> 8), dist & 0xFF))
                i += best
            else:
                flags |= bit
                out.append(data[i])
                i += 1
        out[flag_at] = flags
    return bytes(out)


# ------------------------------------------------------------------------------------------------------------- SARC
# Header (0x14): "SARC", u16 header size, u16 BOM, u32 file size, u32 data offset, u16 version, u16 reserved.
# SFAT (0xC): "SFAT", u16 header size, u16 node count, u32 hash key; nodes of 16 bytes: u32 name hash,
# u32 attributes (has-name flag << 24 | name offset / 4), u32 data start, u32 data end (relative to the data offset).
# SFNT (8): "SFNT", u16 header size, u16 reserved; then the names, each 0-terminated and padded to 4.

SARC_BOM = {BE: b"\xfe\xff", LE: b"\xff\xfe"}


@dataclass
class SarcNode:
    name: str
    hash: int
    attr: int
    start: int
    end: int


@dataclass
class Sarc:
    order: str
    file_size: int
    data_offset: int
    version: int
    hash_key: int
    sfat: int
    sfnt: int
    nodes: list[SarcNode]


def sarc_parse(data: bytes) -> Sarc:
    if len(data) < 0x14 + 0xC + 8 or data[:4] != b"SARC":
        raise FormatError("not a SARC")
    order = {b"\xfe\xff": BE, b"\xff\xfe": LE}.get(bytes(data[6:8]))
    if not order:
        raise FormatError("SARC byte order mark is neither FE FF nor FF FE")
    hsize, _bom, fsize, doff, version = struct.unpack_from(order + "HHIIH", data, 4)
    if hsize != 0x14:
        raise FormatError(f"SARC header size 0x{hsize:x}, expected 0x14")
    if fsize > len(data) or doff > fsize:
        raise FormatError("SARC sizes don't fit the file")
    sfat = hsize
    if data[sfat:sfat + 4] != b"SFAT":
        raise FormatError("SARC has no SFAT block")
    shsize, count, key = struct.unpack_from(order + "HHI", data, sfat + 4)
    if shsize != 0xC:
        raise FormatError("SFAT header size isn't 0xC")
    sfnt = sfat + 0xC + 16 * count
    _need(data, sfnt, 8, "SFNT")
    if data[sfnt:sfnt + 4] != b"SFNT" or _u16(data, sfnt + 4, order) != 8:
        raise FormatError("SARC has no SFNT block where expected")
    names_at = sfnt + 8
    nodes = []
    for k in range(count):
        h, attr, start, end = struct.unpack_from(order + "IIII", data, sfat + 0xC + 16 * k)
        if start > end or doff + end > fsize:
            raise FormatError(f"SARC file {k} lies outside the archive")
        name = ""
        if attr >> 24:
            at = names_at + (attr & 0xFFFFFF) * 4
            stop = data.find(b"\0", at, doff)
            if at >= doff or stop < 0:
                raise FormatError(f"SARC file {k} has a bad name offset")
            name = data[at:stop].decode("utf-8", "replace")
        nodes.append(SarcNode(name or f"(unnamed {k:04d}, hash {h:08x})", h, attr, start, end))
    return Sarc(order, fsize, doff, version, key, sfat, sfnt, nodes)


def sarc_files(data: bytes) -> list[tuple[str, bytes]]:
    s = sarc_parse(data)
    return [(n.name, data[s.data_offset + n.start:s.data_offset + n.end]) for n in s.nodes]


def sarc_build(files: list[tuple[str, bytes]], order: str = LE, *, align: dict[str, int] | None = None,
               hash_key: int = 0x65, data_align: int = 0x100, name_align: dict[str, int] | None = None) -> bytes:
    """A SARC from (name, data) pairs, sorted by name hash like the game's. `align` gives the alignment by file
    extension, `name_align` by file name (it wins). For tests and tools, and for packs whose file names change."""
    def h(name: str) -> int:
        v = 0
        for c in name.encode():
            v = (v * hash_key + (c - 256 if c >= 128 else c)) & 0xFFFFFFFF
        return v
    files = sorted(files, key=lambda f: h(f[0]))
    names = bytearray()
    name_off = []
    for name, _ in files:
        name_off.append(len(names))
        names += name.encode() + b"\0"
        names += bytes(_align_up(len(names), 4) - len(names))
    header_end = 0x14 + 0xC + 16 * len(files) + 8 + len(names)
    doff = _align_up(header_end, data_align)
    body = bytearray()
    spans = []
    for name, data in files:
        a = (name_align or {}).get(name) or (align or {}).get(_ext(name), 4)
        pad = _align_up(doff + len(body), a) - doff - len(body)
        body += bytes(pad)
        spans.append((len(body), len(body) + len(data)))
        body += data
    out = bytearray(b"SARC" + struct.pack(order + "H", 0x14) + SARC_BOM[order])
    out += struct.pack(order + "IIHH", doff + len(body), doff, 0x100, 0)
    out += b"SFAT" + struct.pack(order + "HHI", 0xC, len(files), hash_key)
    for (name, _), off, (s, e) in zip(files, name_off, spans):
        out += struct.pack(order + "IIII", h(name), 0x01000000 | off // 4, s, e)
    out += b"SFNT" + struct.pack(order + "HH", 8, 0) + names
    out += bytes(doff - len(out)) + body
    return bytes(out)


# ------------------------------------------------------------------------------------------------------------- BYML
# Header (v2-v7): "BY" (big-endian) or "YB" (little-endian), u16 version, u32 key table offset, u32 string table
# offset, u32 root offset. Containers start with a type byte and a 24-bit count. Switch BotW: "YB", version 2.

BYML_STRING, BYML_BINARY = 0xA0, 0xA1
BYML_ARRAY, BYML_DICT, BYML_STRINGS = 0xC0, 0xC1, 0xC2
BYML_BOOL, BYML_INT, BYML_FLOAT, BYML_UINT = 0xD0, 0xD1, 0xD2, 0xD3
BYML_INT64, BYML_UINT64, BYML_DOUBLE = 0xD4, 0xD5, 0xD6
BYML_NULL = 0xFF
BYML_INLINE = {BYML_STRING, BYML_BOOL, BYML_INT, BYML_FLOAT, BYML_UINT, BYML_NULL}
BYML_LONG = {BYML_INT64, BYML_UINT64, BYML_DOUBLE}
BYML_NAMES = {BYML_STRING: "string", BYML_BINARY: "binary", BYML_ARRAY: "array", BYML_DICT: "dictionary",
              BYML_STRINGS: "string table", BYML_BOOL: "bool", BYML_INT: "int", BYML_FLOAT: "float",
              BYML_UINT: "uint", BYML_INT64: "int64", BYML_UINT64: "uint64", BYML_DOUBLE: "double",
              BYML_NULL: "null"}


def byml_order(data: bytes) -> str | None:
    if len(data) >= 0x10 and data[:2] in (b"BY", b"YB"):
        order = BE if data[:2] == b"BY" else LE
        if 1 <= _u16(data, 2, order) <= 7:
            return order
    return None


def _u24(data: bytes, off: int, order: str) -> int:
    b = data[off:off + 3]
    return (b[0] << 16 | b[1] << 8 | b[2]) if order == BE else (b[0] | b[1] << 8 | b[2] << 16)


def byml_convert(data: bytes, to: str, counts: Counter | None = None) -> bytes:
    """Swap a BYML document to the byte order `to`, keeping its layout (offsets don't change)."""
    order = byml_order(data)
    if order is None:
        raise FormatError("not a BYML document")
    if order == to:
        return data
    version = _u16(data, 2, order)
    if version < 2:
        raise FormatError(f"BYML version {version} isn't supported (BotW uses version 2)")
    buf = bytearray(data)
    buf[0:2] = b"BY" if to == BE else b"YB"
    _swap16(buf, 2)
    _swap32_run(buf, 4, 3)
    keys, strings, root = struct.unpack_from(order + "III", data, 4)
    counts = counts if counts is not None else Counter()

    def where(off: int, size: int, what: str) -> None:
        if off < 0x10:
            raise FormatError(f"BYML {what} offset 0x{off:x} points into the header")
        _need(data, off, size, f"BYML {what}")

    for off in (keys, strings):
        if off:
            where(off, 4, "string table")
            if data[off] != BYML_STRINGS:
                raise FormatError(f"BYML string table at 0x{off:x} has type 0x{data[off]:02x}")
            n = _u24(data, off + 1, order)
            _swap24(buf, off + 1)
            where(off + 4, 4 * (n + 1), "string table")
            _swap32_run(buf, off + 4, n + 1)
            counts["string table"] += 1
    seen: set[int] = set()
    longs: set[int] = set()
    blobs: set[int] = set()
    stack = []
    if root:
        where(root, 4, "root")
        if data[root] not in (BYML_ARRAY, BYML_DICT):
            raise FormatError(f"BYML root has type 0x{data[root]:02x}, not an array or dictionary")
        stack.append((root, data[root]))
    while stack:
        off, expect = stack.pop()
        if off in seen:
            continue
        seen.add(off)
        where(off, 4, "node")
        kind = data[off]
        if kind != expect:
            raise FormatError(f"BYML node at 0x{off:x} has type 0x{kind:02x}, expected 0x{expect:02x}")
        n = _u24(data, off + 1, order)
        _swap24(buf, off + 1)
        counts[BYML_NAMES[kind]] += 1
        if kind == BYML_ARRAY:
            where(off + 4, n, "array types")
            values = off + 4 + _align_up(n, 4)
            where(values, 4 * n, "array values")
            items = [(data[off + 4 + k], values + 4 * k) for k in range(n)]
        else:
            where(off + 4, 8 * n, "dictionary")
            items = []
            for k in range(n):
                e = off + 4 + 8 * k
                _swap24(buf, e)  # key index (24 bits), then the type byte
                items.append((data[e + 3], e + 4))
        for vt, voff in items:
            value = _u32(data, voff, order)
            _swap32(buf, voff)
            if vt in BYML_INLINE:
                counts[BYML_NAMES[vt]] += 1
            elif vt in (BYML_ARRAY, BYML_DICT):
                stack.append((value, vt))
            elif vt in BYML_LONG:
                counts[BYML_NAMES[vt]] += 1
                if value not in longs:
                    where(value, 8, BYML_NAMES[vt])
                    _swap64(buf, value)
                    longs.add(value)
            elif vt == BYML_BINARY:
                counts["binary"] += 1
                if value not in blobs:
                    where(value, 4, "binary data")
                    where(value + 4, _u32(data, value, order), "binary data")
                    _swap32(buf, value)
                    blobs.add(value)
            else:
                raise FormatError(f"unknown BYML node type 0x{vt:02x} (in the node at 0x{off:x})")
    return bytes(buf)


def byml_load(data: bytes):
    """A BYML document as Python values, with each value's type kept (so two files compare exactly)."""
    order = byml_order(data)
    if order is None:
        raise FormatError("not a BYML document")
    keys_off, strings_off, root = struct.unpack_from(order + "III", data, 4)

    def table(off: int) -> list[str]:
        if not off:
            return []
        n = _u24(data, off + 1, order)
        offs = struct.unpack_from(order + f"{n + 1}I", data, off + 4)
        return [data[off + offs[k]:data.index(b"\0", off + offs[k])].decode("utf-8") for k in range(n)]

    keys, strings = table(keys_off), table(strings_off)

    def value(vt: int, raw: int):
        if vt == BYML_STRING:
            return strings[raw]
        if vt == BYML_ARRAY or vt == BYML_DICT:
            return node(raw)
        if vt in BYML_LONG:
            return (BYML_NAMES[vt], struct.unpack_from(order + "Q", data, raw)[0])
        if vt == BYML_BINARY:
            size = _u32(data, raw, order)
            return bytes(data[raw + 4:raw + 4 + size])
        if vt == BYML_NULL:
            return None
        if vt == BYML_BOOL:
            return bool(raw)
        return (BYML_NAMES[vt], raw)  # int, uint, float (as its bits)

    def node(off: int):
        kind, n = data[off], _u24(data, off + 1, order)
        if kind == BYML_ARRAY:
            values = off + 4 + _align_up(n, 4)
            return [value(data[off + 4 + k], _u32(data, values + 4 * k, order)) for k in range(n)]
        out = {}
        for k in range(n):
            e = off + 4 + 8 * k
            out[keys[_u24(data, e, order)]] = value(data[e + 3], _u32(data, e + 4, order))
        return out

    return node(root) if root else None


# ------------------------------------------------------------------------------------------------------------- MSBT
# Header (0x20): "MsgStdBn", BOM, u16 0, u8 encoding (1 = UTF-16), u8 version (3), u16 section count, u16 0,
# u32 file size, padding. Sections: magic, u32 size, 8 bytes padding, body, padded to 16 with 0xAB.
# BotW (Switch, all 16 languages) uses LBL1, ATR1 and TXT2 only.

MSBT_BOM = {BE: b"\xfe\xff", LE: b"\xff\xfe"}
# Parameter layouts of the text tags (0x0E, group, type, size, params) the game's own text uses. Most parameters are
# 16-bit values and UTF-16 strings with a 16-bit byte length, so they swap per 16 bits. The exceptions were read from
# the game's texts (all 16 languages): fields that hold one 32-bit value (a float in 1.1), tags whose last two
# bytes are 8-bit values (choice indexes), and tags made only of 8-bit values (0xCD is padding).
MSBT_TAG_U32 = {(1, 0), (1, 1), (1, 3)}
MSBT_TAG_U16_THEN_BYTES = {(1, 4), (1, 5), (1, 6), (1, 8), (1, 9), (1, 10)}
MSBT_TAG_BYTES = {(1, 7), (3, 1), (4, 1), (201, 0)}
MSBT_TAG_U16 = ({(0, t) for t in range(5)} | {(1, 2)} | {(2, t) for t in range(1, 20)} | {(4, 0), (4, 2), (4, 3)}
                | {(5, t) for t in range(3)} | {(201, t) for t in range(1, 9)})


def _tag_layout(group: int, kind: int, size: int) -> tuple[list[tuple[str, int]], bool]:
    """How to swap a tag's parameters: [(unit, bytes)] with unit 'h' (16-bit), 'i' (32-bit) or 'b' (8-bit,
    unchanged); and whether the layout is a guess."""
    key = (group, kind)
    if size == 0:
        return [], False
    if key in MSBT_TAG_U32 and size == 4:
        return [("i", 4)], False
    if key in MSBT_TAG_U16_THEN_BYTES and size >= 2 and size % 2 == 0:
        return [("h", size - 2), ("b", 2)], False
    if key in MSBT_TAG_BYTES:
        return [("b", size)], False
    if size % 2:
        raise FormatError(f"text tag {group}.{kind} has an odd parameter size ({size})")
    return [("h", size)], key not in MSBT_TAG_U16


def _msbt_sections(data: bytes, order: str) -> list[tuple[bytes, int, int, int]]:
    """(magic, header offset, body offset, body size) for each section."""
    count = _u16(data, 0x0E, order)
    out, p = [], 0x20
    for _ in range(count):
        _need(data, p, 16, "MSBT section header")
        size = _u32(data, p + 4, order)
        _need(data, p + 16, size, f"MSBT section {data[p:p + 4]!r}")
        out.append((bytes(data[p:p + 4]), p, p + 16, size))
        p = _align_up(p + 16 + size, 16)
    return out


def msbt_order(data: bytes) -> str:
    if len(data) < 0x20 or data[:8] != b"MsgStdBn":
        raise FormatError("not an MSBT")
    order = {b"\xfe\xff": BE, b"\xff\xfe": LE}.get(bytes(data[8:10]))
    if not order:
        raise FormatError("MSBT byte order mark is neither FE FF nor FF FE")
    return order


def _atr1_strings(data: bytes, body: int, size: int, count: int, offs: list[int]) -> bool:
    """Whether ATR1 entries are offsets to UTF-16 strings after the table (BotW's case)."""
    table_end = 8 + 4 * count
    return count > 0 and size > table_end and all(table_end <= o < size and o % 2 == 0 for o in offs)


def msbt_convert(data: bytes, to: str, guesses: list[str] | None = None) -> bytes:
    order = msbt_order(data)
    if order == to:
        return data
    if data[0x0C] != 1:
        raise FormatError(f"MSBT text encoding {data[0x0C]} isn't supported (BotW uses UTF-16)")
    buf = bytearray(data)
    buf[8:10] = MSBT_BOM[to]
    _swap16(buf, 0x0A)
    _swap16(buf, 0x0E)
    _swap16(buf, 0x10)
    _swap32(buf, 0x12)
    for magic, head, body, size in _msbt_sections(data, order):
        _swap32(buf, head + 4)
        end = body + size
        if magic == b"LBL1":
            _need(data, body, 4, "LBL1")
            slots = _u32(data, body, order)
            _need(data, body + 4, 8 * slots, "LBL1 slots")
            _swap32_run(buf, body, 1 + 2 * slots)
            for k in range(slots):
                n, rel = struct.unpack_from(order + "II", data, body + 4 + 8 * k)
                q = body + rel
                for _ in range(n):
                    if q >= end:
                        raise FormatError("LBL1 label runs past its section")
                    q += 1 + data[q]
                    if q + 4 > end:
                        raise FormatError("LBL1 label runs past its section")
                    _swap32(buf, q)
                    q += 4
        elif magic == b"ATR1":
            _need(data, body, 8, "ATR1")
            count, esize = struct.unpack_from(order + "II", data, body)
            _swap32_run(buf, body, 2)
            if esize == 0 or count == 0:
                continue
            if esize != 4 or 8 + 4 * count > size:
                raise FormatError(f"ATR1 with {esize}-byte attributes isn't supported")
            offs = list(struct.unpack_from(order + f"{count}I", data, body + 8))
            _swap32_run(buf, body + 8, count)
            if _atr1_strings(data, body, size, count, offs):
                units: set[int] = set()
                for o in offs:
                    q = body + o
                    while q + 2 <= end:
                        units.add(q)
                        if data[q] == 0 and data[q + 1] == 0:
                            break
                        q += 2
                for q in units:
                    _swap16(buf, q)
            elif size != 8 + 4 * count:
                raise FormatError("ATR1 layout isn't supported")
        elif magic == b"TXT2":
            _need(data, body, 4, "TXT2")
            count = _u32(data, body, order)
            _need(data, body + 4, 4 * count, "TXT2 offsets")
            offs = struct.unpack_from(order + f"{count}I", data, body + 4)
            _swap32_run(buf, body, 1 + count)
            bounds = sorted(set(offs) | {size})
            if bounds[0] < 4 + 4 * count:
                raise FormatError("TXT2 string offset points into its table")
            for a, b in zip(bounds, bounds[1:]):
                q, stop = body + a, body + b
                while q + 2 <= stop:
                    c = _u16(data, q, order)
                    _swap16(buf, q)
                    if c == 0x0E:
                        if q + 8 > stop:
                            raise FormatError("text tag runs past its string")
                        g, t, ps = struct.unpack_from(order + "HHH", data, q + 2)
                        _swap16_run(buf, q + 2, 3)
                        p = q + 8
                        if p + ps > stop:
                            raise FormatError("text tag parameters run past their string")
                        layout, guess = _tag_layout(g, t, ps)
                        if guess and guesses is not None:
                            guesses.append(f"{g}.{t}")
                        for unit, n in layout:
                            if unit == "h":
                                _swap16_run(buf, p, n // 2)
                            elif unit == "i":
                                _swap32(buf, p)
                            p += n
                        q += 8 + ps
                    elif c == 0x0F:
                        if q + 6 > stop:
                            raise FormatError("closing text tag runs past its string")
                        _swap16_run(buf, q + 2, 2)
                        q += 6
                    else:
                        q += 2
                if q != stop:
                    raise FormatError("TXT2 string has an odd length")
        elif magic in (b"TSY1", b"NLI1"):
            if size % 4:
                raise FormatError(f"{magic.decode()} size isn't a multiple of 4")
            _swap32_run(buf, body, size // 4)
        else:
            raise FormatError(f"MSBT section {magic!r} isn't supported")
    return bytes(buf)


def msbt_load(data: bytes):
    """An MSBT as Python values: header fields, labels, attributes and texts (tags split into their fields)."""
    order = msbt_order(data)
    out: dict = {"header": (data[0x0C], data[0x0D], _u16(data, 0x0A, order), _u16(data, 0x10, order)),
                 "size": _u32(data, 0x12, order)}
    for magic, _head, body, size in _msbt_sections(data, order):
        name = magic.decode("ascii", "replace")
        if magic == b"LBL1":
            slots = _u32(data, body, order)
            labels = []
            for k in range(slots):
                n, rel = struct.unpack_from(order + "II", data, body + 4 + 8 * k)
                q = body + rel
                for _ in range(n):
                    ln = data[q]
                    labels.append((k, bytes(data[q + 1:q + 1 + ln]), _u32(data, q + 1 + ln, order)))
                    q += 5 + ln
            out[name] = labels
        elif magic == b"ATR1":
            count, esize = struct.unpack_from(order + "II", data, body)
            offs = list(struct.unpack_from(order + f"{count}I", data, body + 8)) if esize == 4 else []
            if offs and _atr1_strings(data, body, size, count, offs):
                vals = []
                for o in offs:
                    q, s = body + o, []
                    while q + 2 <= body + size and _u16(data, q, order):
                        s.append(_u16(data, q, order))
                        q += 2
                    vals.append(tuple(s))
                out[name] = (count, esize, vals)
            else:
                out[name] = (count, esize, offs)
        elif magic == b"TXT2":
            count = _u32(data, body, order)
            offs = struct.unpack_from(order + f"{count}I", data, body + 4)
            ends = sorted(set(offs) | {size})
            texts = []
            for a in offs:
                b = ends[ends.index(a) + 1]
                q, stop, toks = body + a, body + b, []
                while q + 2 <= stop:
                    c = _u16(data, q, order)
                    if c == 0x0E:
                        g, t, ps = struct.unpack_from(order + "HHH", data, q + 2)
                        layout, _ = _tag_layout(g, t, ps)
                        p, fields = q + 8, []
                        for unit, n in layout:
                            if unit == "h":
                                fields += struct.unpack_from(order + f"{n // 2}H", data, p)
                            elif unit == "i":
                                fields.append(_u32(data, p, order))
                            else:
                                fields.append(bytes(data[p:p + n]))
                            p += n
                        toks.append(("tag", g, t, tuple(fields)))
                        q += 8 + ps
                    elif c == 0x0F:
                        toks.append(("end", _u16(data, q + 2, order), _u16(data, q + 4, order)))
                        q += 6
                    else:
                        toks.append(c)
                        q += 2
                texts.append(toks)
            out[name] = texts
        else:
            out[name] = struct.unpack_from(order + f"{size // 4}I", data, body)
    return out


# ------------------------------------------------------------------------------------------------------------- RSTB
# "RSTB", u32 CRC entry count, u32 name entry count; CRC entries (u32 CRC-32 of the path, u32 size); name entries
# (128-byte path, u32 size). Big-endian on Wii U, little-endian on Switch. The sizes themselves differ between the
# consoles (the Switch build is 64-bit), so a converted Wii U table is still a Wii U table.

def rstb_order(data: bytes) -> str:
    if len(data) < 12 or data[:4] != b"RSTB":
        raise FormatError("not an RSTB")
    fits = [o for o in (BE, LE) if 12 + 8 * _u32(data, 4, o) + 132 * _u32(data, 8, o) == len(data)]
    if not fits:
        raise FormatError("RSTB size doesn't match its entry counts")
    if len(fits) == 2 and len(data) > 12:
        raise FormatError("RSTB byte order can't be told from its entry counts")
    return fits[0]  # (an empty table reads the same both ways)


def rstb_convert(data: bytes, to: str) -> bytes:
    order = rstb_order(data)
    if order == to:
        return data
    crcs, names = struct.unpack_from(order + "II", data, 4)
    buf = bytearray(data)
    _swap32_run(buf, 4, 2 + 2 * crcs)
    base = 12 + 8 * crcs
    for k in range(names):
        _swap32(buf, base + 132 * k + 128)
    return bytes(buf)


def rstb_load(data: bytes):
    order = rstb_order(data)
    crcs, names = struct.unpack_from(order + "II", data, 4)
    table = struct.unpack_from(order + f"{2 * crcs}I", data, 12)
    base = 12 + 8 * crcs
    named = [(bytes(data[base + 132 * k:base + 132 * k + 128]), _u32(data, base + 132 * k + 128, order))
             for k in range(names)]
    return list(zip(table[::2], table[1::2])), named


# ------------------------------------------------------------------------------------------------------- converter

@dataclass
class Note:
    path: str
    status: str
    kind: str
    detail: str = ""
    warnings: list = field(default_factory=list)   # converted, but check in game (models and textures)


@dataclass
class Result:
    data: bytes
    status: str
    kind: str
    detail: str = ""
    rename: str | None = None      # new file name (the last path part), e.g. X.Tex1.sbfres -> X.Tex.sbfres
    drop: bool = False             # not written (an X.Tex2 joined into X.Tex)
    warnings: list = field(default_factory=list)
    yaz0_align: int | None = None  # Yaz0 alignment field of the output (None: the input's)
    game_bytes: bytes | None = None  # the game's own file (as stored): the converted data holds the same values


def identify(data: bytes, name: str) -> str:
    """The format of a file: yaz0, sarc, byml, msbt, aamp, rstb, text, a platform-specific kind, or unknown."""
    head = bytes(data[:8])
    if head[:4] == b"Yaz0":
        return "yaz0"
    if head[:4] == b"SARC":
        return "sarc"
    if byml_order(data):
        return "byml"
    if head == b"MsgStdBn":
        return "msbt"
    if head[:4] == b"AAMP":
        return "aamp"
    if head[:4] == b"RSTB":
        return "rstb"
    if head[:4] in PLATFORM_MAGIC:
        return PLATFORM_MAGIC[head[:4]]
    if len(data) >= 8 and data[4:8] in (b"TAG0", b"TCM0"):
        return "Havok physics"
    ext = _ext(name)
    if ext.startswith(("hk", "shk")):
        return "Havok physics"
    if ext in EXT_KIND:
        return EXT_KIND[ext]
    if ext[:1] == "s" and ext[1:] in EXT_KIND:
        return EXT_KIND[ext[1:]]
    if ext in MODEL_EXT or (ext[:1] == "s" and ext[1:] in MODEL_EXT):
        return "model/texture"
    if (ext in TEXT_EXT or head.startswith(b"<?xml")) and b"\0" not in data[:4096]:
        return "text"
    return "unknown"


KIND_LABEL = {"yaz0": "Yaz0", "sarc": "SARC", "byml": "BYML", "msbt": "MSBT", "aamp": "AAMP", "rstb": "RSTB",
              "text": "text", "unknown": "unknown"}


class Converter:
    """Converts files to the byte order `to`, recursing into Yaz0 and SARC. Every file it meets (nested ones too)
    gets a Note. `recompress=False` (for check) skips Yaz0 compression: the result then keeps the old bytes."""

    def __init__(self, to: str = LE, *, recompress: bool = True, rstb: str = "leave",
                 models: "ModelJob | None" = None):
        self.to = to
        self.recompress = recompress
        self.rstb = rstb  # "leave" (mods: left out) or "convert" (verify)
        # Wii U models and textures (tools/wiiu_model): converted when going to the Switch
        self.models = models if models is not None or to != LE else ModelJob()
        self.notes: list[Note] = []
        self.counts: Counter = Counter()       # (format, status) -> files, nested ones included
        self.byml_nodes: Counter = Counter()
        self.aamp_flags: Counter = Counter()
        self.tag_guesses: Counter = Counter()

    def _note(self, path: str, r: Result) -> Result:
        self.notes.append(Note(path, r.status, r.kind, r.detail, list(r.warnings)))
        self.counts[(r.kind, r.status)] += 1
        return r

    def file(self, data: bytes, path: str) -> Result:
        if not data:
            return self._note(path, Result(data, SAME, "empty", "empty file"))
        name = path.rsplit("//", 1)[-1]
        fmt = identify(data, name)
        label = KIND_LABEL.get(fmt, fmt)
        try:
            if fmt == "yaz0":
                return self._yaz0(data, path)
            if fmt == "sarc":
                return self._sarc(data, path)
            if fmt == "byml":
                if byml_order(data) == self.to:
                    return self._note(path, Result(data, ALREADY, label))
                return self._note(path, Result(byml_convert(data, self.to, self.byml_nodes), CONVERTED, label))
            if fmt == "msbt":
                if msbt_order(data) == self.to:
                    return self._note(path, Result(data, ALREADY, label))
                guesses: list[str] = []
                out = msbt_convert(data, self.to, guesses)
                detail = ""
                if guesses:
                    self.tag_guesses.update(guesses)
                    detail = (f"text tag(s) {', '.join(sorted(set(guesses)))} aren't used by the game's own text: "
                              "their values were converted as 16-bit numbers (check these lines in game)")
                return self._note(path, Result(out, CONVERTED, label, detail))
            if fmt == "aamp":
                flags = _u32(data, 8, LE) if len(data) >= 12 else 0
                self.aamp_flags[flags] += 1
                if flags & 1:
                    return self._note(path, Result(data, SAME, label, "little-endian on both consoles"))
                return self._note(path, Result(data, CANT, label, "big-endian AAMP: BotW doesn't use these"))
            if fmt == "rstb":
                if self.rstb == "leave":
                    rstb_order(data)
                    return self._note(path, Result(data, LEFT_OUT, label, RSTB_NOTE))
                out = rstb_convert(data, self.to)
                return self._note(path, Result(out, CONVERTED if out != data else ALREADY, label))
            if fmt == "text":
                return self._note(path, Result(data, SAME, label))
            if fmt == "unknown":
                return self._note(path, Result(data, UNKNOWN, label, "kept as it is"))
            if fmt == "model/texture" and data[:4] == b"FRES" and self.models is not None:
                r = self.models.fres(self, data, path)
                if r is not None:
                    return self._note(path, r)
            detail = WHY.get(fmt, "")
            if name.lower().endswith((".tex1.sbfres", ".tex2.sbfres", ".tex1.bfres", ".tex2.bfres")):
                detail = "not a Wii U texture BFRES (Tex1/Tex2), so it can't be joined into one .Tex.sbfres"
            return self._note(path, Result(data, CANT, fmt, detail))
        except FormatError as e:
            return self._note(path, Result(data, BROKEN, label, str(e)))
        except (struct.error, IndexError, ValueError, UnicodeDecodeError) as e:
            return self._note(path, Result(data, BROKEN, label, f"damaged: {e}"))

    def _yaz0(self, data: bytes, path: str) -> Result:
        raw, align = yaz0_decompress(data)
        inner = self.file(raw, path)
        keep = dict(rename=inner.rename, drop=inner.drop, warnings=inner.warnings)
        if inner.drop or inner.data == raw or not self.recompress:
            return Result(data, inner.status, inner.kind, inner.detail, **keep)
        game = inner.game_bytes
        if game is None and self.models is not None and inner.status in OK and inner.kind not in MODEL_KINDS:
            game = self.models.same_as_game(path, inner.data)
        if game is not None:  # the same as the game's own file: its bytes, no compressing
            return Result(game, inner.status, inner.kind, inner.detail, **keep)
        out_align = align if inner.yaz0_align is None else inner.yaz0_align
        return Result(yaz0_compress(inner.data, out_align), inner.status, inner.kind, inner.detail, **keep)

    def _sarc(self, data: bytes, path: str) -> Result:
        s = sarc_parse(data)
        if s.order == self.to:
            return self._note(path, Result(data, ALREADY, "SARC"))
        at = len(self.notes)
        self.notes.append(Note(path, CONVERTED, "SARC"))  # filled in below
        doff = s.data_offset
        spans = sorted({(n.start, n.end) for n in s.nodes})
        first_name = {}
        for n in s.nodes:
            first_name.setdefault((n.start, n.end), n.name)
        if self.models is not None:  # (Tex1 / Tex2 pairs inside this pack)
            for span in spans:
                self.models.register(f"{path}//{first_name[span]}",
                                     lambda a=doff + span[0], b=doff + span[1]: data[a:b])
        results = {}
        for span in spans:
            child = data[doff + span[0]:doff + span[1]]
            results[span] = self.file(child, f"{path}//{first_name[span]}")
        if any(r.rename or r.drop for r in results.values()):
            self._check_renames(data, s, path, results)
        if self.models is not None:
            self.models.settle(self, path, s, results)
        statuses = [r.status for r in results.values()]
        bad = [st for st in statuses if st not in OK]
        if bad and not any(st in OK or st in (PARTLY, REPLACED) for st in statuses):
            # nothing inside could be converted: keep the whole pack as it is
            status = bad[0] if bad[0] in (UNKNOWN, BROKEN) and len(set(bad)) == 1 else CANT
            kinds = Counter(r.kind for r in results.values())
            r = Result(data, status, "SARC", "holds only files that can't be converted: "
                       + ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
            self.notes[at] = Note(path, r.status, r.kind, r.detail)
            self.counts[("SARC", r.status)] += 1
            return r
        status = PARTLY if bad else CONVERTED
        detail = f"{len(bad)} of {len(statuses)} file(s) inside not converted" if bad else ""
        game_files = statuses.count(REPLACED)
        if game_files:
            detail += f" ({game_files} gave way to your game's file)"
        buf = bytearray(data)
        _swap16(buf, 4)
        buf[6:8] = SARC_BOM[self.to]
        _swap32_run(buf, 8, 2)
        _swap16(buf, 16)
        _swap16(buf, 18)
        _swap16(buf, s.sfat + 4)
        _swap16(buf, s.sfat + 6)
        _swap32(buf, s.sfat + 8)
        _swap32_run(buf, s.sfat + 0xC, 4 * len(s.nodes))
        _swap16(buf, s.sfnt + 4)
        _swap16(buf, s.sfnt + 6)
        fill = self.models.fill(self, path, s, results) if self.models is not None else []
        if fill or any(r.rename or r.drop for r in results.values()):
            out = self._rebuild(s, results, fill)
            if fill:
                detail = (detail + "; " if detail else "") + (f"{len(fill)} file(s) your game's pack has were added "
                                                              "from it")
        elif all(len(results[sp].data) == sp[1] - sp[0] for sp in spans):
            for sp in spans:
                buf[doff + sp[0]:doff + sp[1]] = results[sp].data
            out = bytes(buf)
        else:
            out = self._relayout(data, buf, s, spans, results)
        self.notes[at] = Note(path, status, "SARC", detail)
        self.counts[("SARC", status)] += 1
        return Result(out, status, "SARC", detail)

    def _check_renames(self, data: bytes, s: Sarc, path: str, results: dict) -> None:
        """A renamed file must not take the name of another file of the pack, and a pack with unnamed files can't be
        rebuilt: such a file keeps its old bytes and is reported as not converted."""
        doff = s.data_offset
        unnamed = any(not n.attr >> 24 for n in s.nodes)
        kept = {n.name.lower() for n in s.nodes if not results[(n.start, n.end)].rename
                and not results[(n.start, n.end)].drop}
        for n in s.nodes:
            sp = (n.start, n.end)
            r = results[sp]
            if not (r.rename or r.drop):
                continue
            new = _renamed(n.name, r.rename) if r.rename else None
            why = ("the pack has files without names, so it can't be laid out again" if unnamed else
                   f"the pack already has {new}" if new and new.lower() in kept else None)
            if why:
                results[sp] = Result(data[doff + sp[0]:doff + sp[1]], BROKEN, r.kind, why)
                self._renote(f"{path}//{n.name}", results[sp])
            elif new:
                kept.add(new.lower())

    def _renote(self, path: str, r: Result) -> None:
        for i in range(len(self.notes) - 1, -1, -1):
            if self.notes[i].path == path:
                old = self.notes[i]
                self.counts[(old.kind, old.status)] -= 1
                self.notes[i] = Note(path, r.status, r.kind, r.detail, list(r.warnings))
                self.counts[(r.kind, r.status)] += 1
                return

    def _rebuild(self, s: Sarc, results: dict, fill: list) -> bytes:
        """The pack built again (names and hashes of renamed files change, joined X.Tex2 files go, files filled from
        the game come in). Each file keeps at least its old alignment (largest power of two dividing its old offset,
        up to 0x2000); an uncompressed Switch BFRES or BNTX gets the alignment its header asks for (0x1000)."""
        files, align = [], {}
        for n in s.nodes:
            r = results[(n.start, n.end)]
            if r.drop:
                continue
            name = _renamed(n.name, r.rename) if r.rename else n.name
            files.append((name, r.data))
            align[name] = max(_alignment_of(s.data_offset + n.start), _header_align(r.data))
        for name, d, a in fill:
            files.append((name, d))
            align[name] = max(a, _header_align(d))
        return sarc_build(files, self.to, name_align=align, hash_key=s.hash_key)

    def _relayout(self, data: bytes, buf: bytearray, s: Sarc, spans, results) -> bytes:
        """Lay the files out again when one changed size: same order, each at an offset aligned at least as well as
        before (largest power of two dividing its old offset, up to 0x2000), and an uncompressed Switch BFRES / BNTX
        (a converted model) as its header asks (0x1000)."""
        doff = s.data_offset
        out = bytearray(buf[:doff])
        moved = {}
        for sp in spans:  # sorted by old start: the files keep their order
            pos = _align_up(len(out), max(_alignment_of(doff + sp[0]), _header_align(results[sp].data)))
            out += bytes(pos - len(out))
            out += results[sp].data
            moved[sp] = (pos - doff, len(out) - doff)
        old_end = doff + max((e for _, e in spans), default=0)
        out += data[old_end:s.file_size]
        size = len(out)
        out += data[s.file_size:]
        struct.pack_into(self.to + "I", out, 8, size)
        for k, n in enumerate(s.nodes):
            start, end = moved[(n.start, n.end)]
            struct.pack_into(self.to + "II", out, s.sfat + 0xC + 16 * k + 8, start, end)
        return bytes(out)


# ------------------------------------------------------------------------------------------- models and textures
# Wii U model mods go through tools/wiiu_model: X.sbfres models (and X_Animation files), X.Tex1 + X.Tex2 textures
# (joined into one Switch X.Tex.sbfres), item icons. With the player's own Switch RomFS (--game; modman passes it) the
# converter also uses the vanilla files, read only: materials are built on the vanilla ones, unchanged textures without
# a Tex2 keep the vanilla mipmaps, TexInfo.txt gets the right count, packs holding converted models are filled with the
# files the game's own pack has, and a size table (RSTB) is written with the converted files' entries raised.

TEX_RE = re.compile(r"^(?P<base>.+)\.Tex(?P<n>[12])(?P<ext>\.s?bfres)$", re.I)
MODEL_EXT_RE = re.compile(r"\.s?bfres$", re.I)
SPLIT_RE = re.compile(r"^(?P<base>.+)-\d\d$")        # X-NN pieces use X.Tex
MODEL_KINDS = ("model", "texture", "animation")
GAME_PACKS = ("Pack/TitleBG.pack",)                # vanilla models not loose in Model/ (Link, Armor_Default...)
RSTB_PATH = "System/Resource/ResourceSizeTable.product.srsizetable"
HEAD_BYTES = 0x10000                               # decompressed bytes read for a texture file's names


def _yaz0_head(data: bytes, limit: int) -> bytes:
    """The first `limit` decompressed bytes of a Yaz0 file (or of a plain one), padded with zeros to its full size."""
    if data[:4] != b"Yaz0":
        return data
    size = struct.unpack_from(">I", data, 4)[0]
    out = bytearray()
    i, src = 16, data
    try:
        while len(out) < min(size, limit):
            code = src[i]
            i += 1
            for bit in (0x80, 0x40, 0x20, 0x10, 8, 4, 2, 1):
                if code & bit:
                    out.append(src[i])
                    i += 1
                else:
                    b1, b2 = src[i], src[i + 1]
                    i += 2
                    dist = ((b1 & 0x0F) << 8 | b2) + 1
                    n = b1 >> 4
                    n = n + 2 if n else src[i] + 0x12
                    i += 0 if b1 >> 4 else 1
                    start = len(out) - dist
                    if start < 0:
                        raise FormatError("Yaz0 data refers to bytes before its start")
                    out += (bytes(out[start:]) * (n // dist + 1))[:n] if dist < n else out[start:start + n]
                if len(out) >= min(size, limit):
                    break
    except IndexError:
        raise FormatError("Yaz0 data is cut short") from None
    return bytes(out[:size]) + bytes(max(0, size - len(out)))


def resource_path(root: str, rel: str) -> str:
    """The size table's name of a file: its RomFS path, Yaz0's "s" taken off the extension, DLC files under
    Aoc/0010/."""
    d, _, name = rel.rpartition("/")
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        if ext[:1] == "s" and len(ext) > 1:
            name = f"{stem}.{ext[1:]}"
    path = f"{d}/{name}" if d else name
    return f"Aoc/0010/{path}" if root == DLC_OUT else path


def rstb_build(crcs: list[tuple[int, int]], named: list[tuple[bytes, int]]) -> bytes:
    """A little-endian RSTB (Switch) from CRC entries and name entries (128-byte names)."""
    out = bytearray(b"RSTB" + struct.pack("<II", len(crcs), len(named)))
    for crc, size in sorted(crcs):
        out += struct.pack("<II", crc, size)
    for name, size in named:
        out += name[:128].ljust(128, b"\0") + struct.pack("<I", size)
    return bytes(out)


class GameFiles:
    """The player's own Switch RomFS, read only: vanilla models and textures (loose in Model/, inside the mod's pack's
    vanilla twin, or inside Pack/TitleBG.pack), packs and the size table."""

    DONORS_KEPT = 4    # vanilla models kept read (a big pack has dozens: only the latest stay in memory)

    def __init__(self, romfs: str):
        romfs = os.path.abspath(romfs)
        if os.path.isdir(os.path.join(romfs, "romfs")):
            romfs = os.path.join(romfs, "romfs")
        if not os.path.isdir(os.path.join(romfs, "Model")):
            raise ConvertError(f"{romfs} isn't a game RomFS folder (no Model folder): --game takes your game's "
                               "RomFS (Savage Wind's extracted\\romfs)", whole=True)
        self.romfs = romfs
        self._index: dict[str, dict | None] = {}
        self._donors: dict[tuple, object] = {}
        self._names: dict[tuple, list[str]] = {}

    def path(self, rel: str) -> str:
        return os.path.join(self.romfs, *rel.split("/"))

    def loose(self, rel: str) -> bytes | None:
        p = self.path(rel)
        if not os.path.isfile(p):
            return None
        with open(p, "rb") as f:
            return f.read()

    def pack_index(self, pack: str) -> dict | None:
        """{name: (offset, size)} of an uncompressed vanilla pack, from its SARC tables only (cached)."""
        if pack not in self._index:
            self._index[pack] = None
            p = self.path(pack)
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    head = f.read(0x14)
                    if head[:4] == b"SARC" and len(head) == 0x14:
                        order = BE if head[6:8] == b"\xfe\xff" else LE
                        doff = _u32(head, 0x0C, order)
                        f.seek(0)
                        idx = f.read(doff)
                        count = _u16(idx, 0x1A, order)
                        names_at = 0x20 + 16 * count + 8
                        out = {}
                        for k in range(count):
                            _h, attr, start, end = struct.unpack_from(order + "IIII", idx, 0x20 + 16 * k)
                            if attr >> 24:
                                at = names_at + (attr & 0xFFFFFF) * 4
                                out[idx[at:idx.index(b"\0", at)].decode("utf-8", "replace")] = (doff + start,
                                                                                                 end - start)
                        self._index[pack] = out
        return self._index[pack]

    def pack_entry(self, pack: str, name: str) -> bytes | None:
        idx = self.pack_index(pack) or {}
        if name not in idx:
            return None
        off, size = idx[name]
        with open(self.path(pack), "rb") as f:
            f.seek(off)
            return f.read(size)

    def pack_files(self, pack: str) -> list[tuple[str, bytes, int]] | None:
        """(name, data, alignment) of every file of a vanilla pack (None: not an uncompressed pack of the game)."""
        idx = self.pack_index(pack)
        if idx is None:
            return None
        return [(n, self.pack_entry(pack, n), _alignment_of(off)) for n, (off, _size) in idx.items()]

    def in_pack(self, pack: str, name: str) -> bool:
        return name in (self.pack_index(pack) or {})

    def has(self, rel: str, pack: str | None = None) -> bool:
        """Whether raw() finds a file (without reading it)."""
        return ((pack is not None and self.in_pack(pack, rel)) or os.path.isfile(self.path(rel))
                or any(self.in_pack(p, rel) for p in GAME_PACKS))

    def raw(self, rel: str, pack: str | None = None) -> bytes | None:
        """A vanilla file by its RomFS path: in the vanilla twin of the mod's pack, loose, or in TitleBG.pack."""
        for where in ([pack] if pack else []) + [None] + list(GAME_PACKS):
            data = self.loose(rel) if where is None else self.pack_entry(where, rel)
            if data is not None:
                return data
        return None

    def find(self, rel: str, pack: str | None = None) -> bytes | None:
        """raw(), decompressed."""
        data = self.raw(rel, pack)
        return yaz0_decompress(data)[0] if data is not None and data[:4] == b"Yaz0" else data

    def tex_names(self, rel: str, pack: str | None = None) -> list[str]:
        """Texture names of a vanilla Switch X.Tex (empty if there is none or it is unreadable; cached)."""
        key = (rel.lower(), pack)
        if key not in self._names:
            names: list[str] = []
            raw = self.raw(rel, pack)
            if raw is not None:
                try:
                    names = wm_convert.switch_texture_names(_yaz0_head(raw, HEAD_BYTES))
                    if not all(names):
                        raise ModelDamaged("names past the head")
                except (ModelError, FormatError, struct.error, ValueError, IndexError):
                    try:
                        names = wm_convert.switch_texture_names(_plain(raw))
                    except (ModelError, FormatError, struct.error, ValueError, IndexError):
                        names = []
            self._names[key] = names
        return self._names[key]

    def donor(self, rel: str, pack: str | None = None):
        """(neutral ResFile, decompressed size) of the vanilla Switch model file, or None (the latest few cached)."""
        key = (rel.lower(), pack)
        if key not in self._donors:
            try:
                data = self.find(rel, pack)
                value = (nx_model.read(data), len(data)) if data and data[:4] == b"FRES" else None
            except (ModelError, FormatError, struct.error, ValueError):
                value = None
            while len(self._donors) >= self.DONORS_KEPT:
                del self._donors[next(iter(self._donors))]
            self._donors[key] = value
        return self._donors[key]


@dataclass
class ModelOutput:
    root: str
    rel: str                    # RomFS path of the written file (inside its pack, for a pack's file)
    kind: str                   # model / animation / texture
    size: int                   # decompressed size
    count: int = 0              # shapes + materials + bones (models)
    vanilla: tuple | None = None  # (decompressed size, count) of the vanilla twin


TEX1_ERRORS = (ModelError, FormatError, struct.error, ValueError, IndexError, KeyError)


class ModelJob:
    """The Wii U models and textures of one mod: where each file goes, which X.Tex2 belongs to which X.Tex1 (the same
    pack first, then loose Model/, then any other pack), the converted outputs (for the size table) and what the game
    files added."""

    def __init__(self, game: GameFiles | None = None):
        self.game = game
        self.targets: dict[str, str] = {}
        self.tex: dict[tuple, dict] = {}
        self.getters: dict[str, object] = {}
        self.done: dict[str, Result | BaseException] = {}   # X.Tex1 results (or why it failed), made once
        self.names: dict[str, list] = {}
        self.model_keys: set[tuple] = set()     # (root, dir/X) of the models (X.sbfres) the mod has
        self.outputs: list[ModelOutput] = []
        self.fills: list[str] = []
        self.same: list[str] = []               # files that came out the same as the game's: its bytes are used
        self.fixed: dict[tuple, tuple] = {}     # (pack path, name): your vanilla model with another TexInfo.txt
        self.extras: list[tuple] = []           # (target, plain bytes, Yaz0 alignment or None, note): written too
        self.rewritten: list[str] = []
        self.stats: Counter = Counter()
        self.writing = True                     # False for check: nothing is written, so vanilla bytes aren't sought

    # ---------------------------------------------------------------- where files are
    def where(self, path: str) -> tuple[str, str, str]:
        """(Switch root, RomFS path, container: '' for a loose file or the pack path) of a file of the mod."""
        parts = path.split("//")
        target = self.targets.get(parts[0], "")
        root = DLC_OUT if target.startswith(DLC_OUT + "/") else BASE_OUT
        rel = parts[-1] if len(parts) > 1 else (target[len(root) + 1:] if target else parts[0])
        return root, rel, "//".join(parts[:-1])

    def game_pack(self, container: str) -> str | None:
        """The RomFS path of the pack a file is in (its vanilla twin holds the vanilla files); None for a loose file
        or a pack inside a pack."""
        if not container or "//" in container:
            return None
        root, rel, _ = self.where(container)
        return rel

    def _key(self, root: str, rel: str, base: str) -> tuple:
        d = rel.rpartition("/")[0]
        return root, f"{d}/{base}".lower() if d else base.lower()

    def register(self, loc: str, getter) -> None:
        """Note a file that may be an X.Tex1 / X.Tex2 (getter gives its bytes, maybe Yaz0) or a model."""
        root, rel, _ = self.where(loc)
        name = rel.rsplit("/", 1)[-1]
        m = TEX_RE.match(name)
        if not m:
            if MODEL_EXT_RE.search(name):
                self.model_keys.add(self._key(root, rel, MODEL_EXT_RE.sub("", name)))
            return
        lst = self.tex.setdefault(self._key(root, rel, m.group("base")), {"1": [], "2": []})[m.group("n")]
        if loc not in lst:
            lst.append(loc)
        self.getters[loc] = getter  # (the latest: a pack being converted gives slices of its bytes in memory)

    def prepare(self, src: "ModSource", layout: "Layout") -> None:
        """Targets of the mod's files, and the X.Tex1 / X.Tex2 files and models that are loose or in an uncompressed
        pack."""
        for member, target in layout.files:
            self.targets[member] = target
        for member, target in layout.files:
            self.register(member, lambda m=member: src.read(m))
            if _ext(member) in ("pack", "sarc") or member.lower().endswith(".ssarc"):
                head = src.read(member, 4)
                if head != b"SARC":
                    continue
                try:
                    names = [n.name for n in sarc_parse(src.read(member)).nodes]
                except FormatError:
                    continue
                for name in names:
                    if TEX_RE.match(name.rsplit("/", 1)[-1]) or MODEL_EXT_RE.search(name):
                        self.register(f"{member}//{name}", lambda m=member, n=name: dict(sarc_files(src.read(m)))[n])

    def _rank(self, container: str):
        return lambda loc: (0 if self.where(loc)[2] == container else 1 if "//" not in loc else 2, loc)

    def partner(self, loc: str, n: str) -> str | None:
        """The X.Tex<n> that goes with the texture file at loc."""
        root, rel, container = self.where(loc)
        m = TEX_RE.match(rel.rsplit("/", 1)[-1])
        cands = self.tex.get(self._key(root, rel, m.group("base")), {}).get(n, []) if m else []
        return min(cands, key=self._rank(container)) if cands else None

    def dec(self, loc: str) -> bytes:
        raw = self.getters[loc]()
        return yaz0_decompress(raw)[0] if raw[:4] == b"Yaz0" else raw

    def tex_names(self, loc: str) -> list[str] | None:
        """Texture names of the Wii U texture file at loc (from its header; None if unreadable)."""
        if loc not in self.names:
            try:
                raw = self.getters[loc]()
                try:
                    names = wm_convert.wiiu_texture_names(_yaz0_head(raw, HEAD_BYTES))
                    if not all(names):
                        raise ModelDamaged("names past the head")
                except ModelError:
                    names = wm_convert.wiiu_texture_names(self.dec(loc))
                self.names[loc] = names
            except (ModelError, FormatError, struct.error, ValueError, KeyError):
                self.names[loc] = None
        return self.names[loc]

    def model_tex1(self, path: str) -> str | None:
        """The X.Tex1 of the mod that the model at path uses (X, or X for an X-NN piece)."""
        root, rel, container = self.where(path)
        name = rel.rsplit("/", 1)[-1]
        stem = MODEL_EXT_RE.sub("", name)
        bases = [stem] + ([SPLIT_RE.match(stem).group("base")] if SPLIT_RE.match(stem) else [])
        for base in bases:
            cands = self.tex.get(self._key(root, rel, base), {}).get("1", [])
            if cands:
                return min(cands, key=self._rank(container))
        return None

    def _vanilla_tex(self, rel: str, pack: str | None) -> tuple[str, list[str]]:
        """(name, texture names) of your game's X.Tex for the model at rel (X, or X for an X-NN piece)."""
        name = rel.rsplit("/", 1)[-1]
        stem, ext = MODEL_EXT_RE.sub("", name), MODEL_EXT_RE.search(name).group(0)
        bases = [stem] + ([SPLIT_RE.match(stem).group("base")] if SPLIT_RE.match(stem) else [])
        for base in bases:
            names = self.game.tex_names(_renamed(rel, f"{base}.Tex{ext}"), pack)
            if names:
                return f"{base}.Tex", names
        return f"{stem}.Tex", []

    # ---------------------------------------------------------------- conversion
    def fres(self, conv: Converter, data: bytes, path: str) -> Result | None:
        """A BFRES of the mod: a Wii U one is converted (model, animation file, X.Tex1 + X.Tex2, single texture file);
        a Switch one is already Switch; None for anything else (kept as before)."""
        if conv.to != LE:
            return None
        if data[:8] == b"FRES    " and data[0x0C:0x0E] == b"\xff\xfe":
            return Result(data, ALREADY, "model/texture")
        if data[8:10] != b"\xfe\xff":
            return None
        self.writing = conv.recompress
        root, rel, container = self.where(path)
        name = rel.rsplit("/", 1)[-1]
        m = TEX_RE.match(name)
        kind = "texture" if m else "model"
        try:
            counts = wm_convert.wiiu_counts(data)
            if m and m.group("n") == "2":
                return self._tex2(path, data, m)
            if m:
                return self._tex1(path, data)
            if counts[1] and not counts[0] and not any(counts[2:11]):
                return self._single(path, data)
            return self._model(path, data)
        except ModelError as e:
            status = BROKEN if isinstance(e, ModelDamaged) else CANT
            r = Result(data, status, kind, f"{'damaged' if status == BROKEN else 'not converted'}: {e}")
        except (FormatError, struct.error, IndexError, ValueError, UnicodeDecodeError, KeyError) as e:
            r = Result(data, BROKEN, kind, f"damaged: {e}")
        return self._refused_loose(path, r, m)

    def _refused_loose(self, path: str, r: Result, m) -> Result:
        """A loose model or single texture file that can't be converted, with your game files: when your game has a
        file of that path it is left out (the game then loads its own; the Wii U bytes would crash it). X.Tex1 /
        X.Tex2 names stay (the Switch game never loads them)."""
        root, rel, container = self.where(path)
        if self.game is None or m is not None or container or root != BASE_OUT or not self.game.has(rel):
            return r
        return Result(r.data, REPLACED, r.kind, f"{r.detail}; left out, so the game uses its own file", drop=True,
                      warnings=["not converted: left out, so your game's own file is used (the mod's change to this "
                                "file is lost)"])

    def _tex1(self, path: str, data: bytes | None = None) -> Result:
        """The X.Tex1 at path converted (once: the result, or the error, is kept)."""
        if path in self.done:
            r = self.done[path]
            if isinstance(r, BaseException):
                raise r
            return r
        try:
            r = self._tex1_convert(path, data)
        except TEX1_ERRORS as e:
            self.done[path] = e
            raise
        self.done[path] = r
        return r

    def _tex1_ok(self, loc: str) -> Result | None:
        """The converted X.Tex1 at loc, or None when it can't be converted (the game then loads its own X.Tex)."""
        try:
            r = self._tex1(loc)
        except TEX1_ERRORS:
            return None
        return r if r.status == CONVERTED else None

    def _tex1_convert(self, path: str, data: bytes | None) -> Result:
        if data is None:
            data = self.dec(path)
        root, rel, container = self.where(path)
        m = TEX_RE.match(rel.rsplit("/", 1)[-1])
        loc2 = self.partner(path, "2")
        tex2 = self.dec(loc2) if loc2 else None
        notes: list[str] = []
        vanilla = None
        tex_rel = _renamed(rel, f"{m.group('base')}.Tex{m.group('ext')}")
        pack = self.game_pack(container)
        if self.game is not None:  # (vanilla mipmaps when there is no Tex2, and the BNTX swizzle field)
            vanilla = self.game.find(tex_rel, pack)
        out, ts = wm_convert.textures_to_switch(data, tex2, vanilla, notes)
        new = f"{m.group('base')}.Tex{m.group('ext')}"
        if loc2:
            detail = f"{len(ts.textures)} texture(s), joined with {loc2.rsplit('/', 1)[-1]} into {new}"
        else:
            detail = f"{len(ts.textures)} texture(s) into {new}; no {m.group('base')}.Tex2 (mipmaps) in the mod"
            if self.game is None:
                notes.append("no Tex2 and no game files (--game): every texture has only its main image, no "
                             "mipmaps; with your game files, unchanged textures keep the game's mipmaps")
        kept = [n for n in notes if n.startswith("vanilla mipmaps kept")]   # (information, not a warning)
        if kept:
            notes = [n for n in notes if n not in kept]
            detail += f"; {kept[0].split(':')[0]}"
        self.names[path] = [t.name for t in ts.textures]
        self.stats["textures"] += len(ts.textures)
        game = self._same_textures(path, out, ts, vanilla, tex_rel, pack)
        if game is not None:
            detail += "; " + SAME_NOTE
        else:
            self.outputs.append(ModelOutput(root, tex_rel, "texture", len(out)))
        fix = self._texinfo_fix(root, rel, container, m, len(ts.textures), notes)
        if fix:
            detail += f"; {fix}"
        return Result(out if game is None else game, CONVERTED, "texture", detail, rename=new, warnings=notes,
                      yaz0_align=0, game_bytes=game)

    def _same_textures(self, path: str, out: bytes, ts, vanilla: bytes | None, rel: str,
                       pack: str | None) -> bytes | None:
        """Your game's X.Tex bytes (as stored) when the converted textures hold the same values; else None."""
        if vanilla is None or not self.writing:
            return None
        if out != vanilla:
            try:
                if not wm_convert.same_as_vanilla(ts, nx_bfres.read_textures(vanilla)):
                    return None
            except (ModelError, struct.error, ValueError, IndexError):
                return None
        self.same.append(path)
        return self.game.raw(rel, pack)

    def _texinfo_fix(self, root: str, rel: str, container: str, m, count: int, notes: list) -> str:
        """A texture-only mod (an X.Tex1 and no X model) whose texture count differs from your vanilla model's
        TexInfo.txt: your model is written again with the new count, into the same pack (filled from your game's)
        or loose, where your game has it. Otherwise a warning. Returns a note for the detail ("" when nothing)."""
        base = m.group("base")
        if self.game is None or root != BASE_OUT or self._key(root, rel, base) in self.model_keys:
            return ""
        model_rel = _renamed(rel, base + m.group("ext"))
        pack = self.game_pack(container)
        donor = self.game.donor(model_rel, pack)
        old = wm_convert.texinfo(donor[0]) if donor else None
        if old is None or old == count:
            return ""
        what = f"your game's {base} model (TexInfo.txt {old} -> {count}, the textures of this {base}.Tex)"
        if container and pack and self.game.in_pack(pack, model_rel):
            self.fixed[(container, model_rel.lower())] = (model_rel, pack, count, what)
            return f"{what} goes into the pack too"
        raw = self.game.loose(model_rel) if not container else None
        if raw is not None:
            plain = wm_convert.with_texinfo(_plain(raw), count)
            align = yaz0_decompress(raw)[1] if raw[:4] == b"Yaz0" else None
            self.extras.append((f"{root}/{model_rel}", plain, align, what))
            self.outputs.append(ModelOutput(root, model_rel, "model", len(plain), wm_convert.size_count(donor[0]),
                                            (donor[1], wm_convert.size_count(donor[0]))))
            return f"{what} is written too"
        where = "not in this pack" if container else "in a pack"
        notes.append(f"your game's {base} model has TexInfo.txt {old} but this {base}.Tex has {count} textures, and "
                     f"the model can't be written where your game keeps it ({where}): the game may crash or miss "
                     "textures")
        return ""

    def _tex2(self, path: str, data: bytes, m) -> Result:
        for loc1 in self.tex.get(self._key(*self.where(path)[:2], m.group("base")), {}).get("1", []):
            if self.partner(loc1, "2") != path:
                continue
            try:
                r1 = self._tex1(loc1)
            except TEX1_ERRORS as e:
                return Result(data, CANT, "texture", f"mipmaps of {loc1.rsplit('/', 1)[-1]}, which can't be "
                              f"converted ({e})")
            if r1.status == CONVERTED:
                return Result(data, CONVERTED, "texture", f"mipmaps, joined into {r1.rename}", drop=True)
        return Result(data, CANT, "texture", f"mipmaps only ({m.group('base')}.Tex2): its main image "
                      f"({m.group('base')}.Tex1) isn't in the mod, and the Switch keeps both in one X.Tex file")

    def _single(self, path: str, data: bytes) -> Result:
        root, rel, container = self.where(path)
        pack = self.game_pack(container)
        notes: list[str] = []
        vanilla = self.game.find(rel, pack) if self.game else None
        if vanilla is not None and vanilla[:8] != b"FRES    ":
            vanilla = None
        out, ts = wm_convert.textures_to_switch(data, None, vanilla, notes)
        self.stats["textures"] += len(ts.textures)
        game = self._same_textures(path, out, ts, vanilla, rel, pack)
        if game is None:
            self.outputs.append(ModelOutput(root, rel, "texture", len(out)))
        return Result(out if game is None else game, CONVERTED, "texture",
                      f"{len(ts.textures)} texture(s)" + (f"; {SAME_NOTE}" if game else ""), warnings=notes,
                      yaz0_align=0, game_bytes=game)

    def _model(self, path: str, data: bytes) -> Result:
        root, rel, container = self.where(path)
        pack = self.game_pack(container)
        notes: list[str] = []
        res = wm_convert.read_wiiu_model(data, notes)
        kind = "model" if wm_convert.is_model_file(res) else "animation"
        # the vanilla model (materials, TexInfo.txt); an animation file only needs your file's size
        donor = self.game.donor(rel, pack) if self.game is not None and wm_convert.needs_donor(res) else None
        loc1 = self.model_tex1(path)
        tex1 = loc1.rsplit("/", 1)[-1] if loc1 else ""
        r1 = self._tex1_ok(loc1) if loc1 is not None else None
        vtex, vnames = self._vanilla_tex(rel, pack) if self.game is not None and kind == "model" else ("", [])
        count, how = None, "no TexInfo.txt"
        if r1 is not None:
            count = len(self.names[loc1])
            how = f"TexInfo.txt {count} (the mod's {tex1})"
        elif loc1 is not None and vnames:
            count = len(vnames)
            how = f"TexInfo.txt {count} (your game's {vtex}: the mod's {tex1} can't be converted)"
        elif donor is not None:
            count = wm_convert.texinfo(donor[0])
            how = (f"TexInfo.txt {count} (your game's model)" if count is not None
                   else "no TexInfo.txt (as your game's model)")
        out, info = wm_convert.model_to_switch(res, notes, count, donor[0] if donor else None)
        if count is None and kind == "model" and donor is None:
            if loc1 is not None:
                notes.append(f"TexInfo.txt not written: the mod's {tex1} can't be converted"
                             + (" and your game has no X.Tex for this model" if self.game else
                                "; with your game files (--game) it gets the count of the game's X.Tex"))
            else:
                notes.append("TexInfo.txt not written: the mod has no X.Tex1 for this model" + (
                    " and your game has no model of this name" if self.game else
                    "; with your game files (--game) it gets the count of the game's X.Tex"))
        if self.game is not None and kind == "model":
            self._check_textures(res, path, rel, pack, donor, notes)
        vanilla = None
        game = None
        if donor is not None:
            vanilla = (donor[1], wm_convert.size_count(donor[0]))
            if self.writing and wm_convert.same_as_vanilla(res, donor[0]):
                game = self.game.raw(rel, pack)
        elif self.game is not None:
            raw = self.game.raw(rel, pack)
            if raw is not None:
                # an animation file: the vanilla part count isn't read (only the size: the size table compares sizes)
                vanilla = (yaz0_size(raw), wm_convert.size_count(res))
                if self.writing and vanilla[0] == len(out):
                    plain = _plain(raw)
                    try:
                        if plain == out or wm_convert.same_as_vanilla(res, nx_model.read(plain)):
                            game = raw
                    except (ModelError, FormatError, struct.error, ValueError):
                        pass
        if game is not None:
            self.same.append(path)
        else:
            self.outputs.append(ModelOutput(root, rel, kind, len(out), wm_convert.size_count(res), vanilla))
        bits = [f"{info['models']} model(s)" if info["models"] else "",
                f"{info['skel_anims'] + info['mat_anims']} animation(s)" if info["skel_anims"] + info["mat_anims"]
                else "", how if kind == "model" else ""]
        mats = info["donor_materials"] + info["alone_materials"]
        if mats:
            bits.append(f"{info['donor_materials']}/{mats} material(s) built on your game's" if self.game else
                        f"{mats} material(s) converted field by field")
        if game is not None:
            bits.append(SAME_NOTE)
        if self.game is not None and info["alone_materials"] and kind == "model":
            notes.append(f"{info['alone_materials']} material(s) are not in your game's model of this name and were "
                         f"converted field by field (Wii U and Switch names may differ: check them in game)")
        self.stats["models" if kind == "model" else "animation files"] += 1
        return Result(out if game is None else game, CONVERTED, kind, ", ".join(b for b in bits if b),
                      warnings=notes, yaz0_align=0, game_bytes=game)

    def _check_textures(self, res, path: str, rel: str, pack: str | None, donor, notes: list) -> None:
        """Every texture a material names should be in an X.Tex the game loads: the model's own (X, or X for an X-NN
        piece) or Link.Tex (armour uses Link's skin and hair). For each, that is the mod's X.Tex when the mod ships one
        that converts (it replaces your game's), else your game's. Names your vanilla model uses that none of your
        game's X.Tex files above holds are not flagged (the game finds them elsewhere: terrain material textures,
        say)."""
        root = self.where(path)[0]
        name = rel.rsplit("/", 1)[-1]
        stem, ext = MODEL_EXT_RE.sub("", name), MODEL_EXT_RE.search(name).group(0)
        base = SPLIT_RE.match(stem).group("base") if SPLIT_RE.match(stem) else stem
        d = rel.rpartition("/")[0]
        known: set[str] = set()
        game_has: set[str] = set()
        for x in dict.fromkeys((stem, base, "Link")):
            vanilla = self.game.tex_names(_renamed(rel, f"{x}.Tex{ext}"), pack)
            game_has.update(vanilla)
            mod = [loc for loc in self.tex.get((root, f"{d}/{x}".lower() if d else x.lower()), {}).get("1", [])
                   if self._tex1_ok(loc) is not None]
            if mod:
                for loc in mod:
                    known.update(self.names.get(loc) or ())
            else:
                known.update(vanilla)
        if donor is not None:
            known.update(t for mm in donor[0].models for mat in mm.materials for t in mat.textures if t not in game_has)
        missing = sorted({t for mm in res.models for mat in mm.materials for t in mat.textures} - known)
        if missing:
            notes.append(f"{len(missing)} texture name(s) are in no X.Tex the game loads ({base}.Tex, Link.Tex): "
                         f"{', '.join(missing[:5])}{'...' if len(missing) > 5 else ''}; those parts will render "
                         f"without that texture")

    def same_as_game(self, path: str, data: bytes) -> bytes | None:
        """Your game's own file (as stored) when the converted file at path (a loose one or one in a pack, not nested
        deeper) is byte for byte your game's of that path, decompressed; else None."""
        if self.game is None or not self.writing:
            return None
        root, rel, container = self.where(path)
        if root != BASE_OUT or "//" in container:
            return None
        raw = self.game.raw(rel, self.game_pack(container))
        if raw is None or yaz0_size(raw) != len(data) or _plain(raw) != data:
            return None
        self.same.append(path)
        return raw

    # ---------------------------------------------------------------- packs and the size table
    def settle(self, conv: Converter, path: str, s: Sarc, results: dict) -> None:
        """Model and texture files of a pack (not nested in another) that can't be converted, with your game files:
        Wii U bytes must not stay where the Switch game loads them. A model or single texture file takes your game's
        file of that name; with none it is left out of the pack. An X.Tex1 / X.Tex2 (a name the Switch game never
        loads) is left out: the pack is filled with your game's X.Tex."""
        if self.game is None or "//" in path or conv.to != LE:
            return
        root, prel, _ = self.where(path)
        if root != BASE_OUT:
            return
        unnamed = any(not n.attr >> 24 for n in s.nodes)
        for n in s.nodes:
            sp = (n.start, n.end)
            r = results[sp]
            if r.kind not in MODEL_KINDS or r.status not in (CANT, BROKEN) or r.drop:
                continue
            m = TEX_RE.match(n.name.rsplit("/", 1)[-1])
            if m:
                if unnamed:
                    continue
                tex = _renamed(n.name, f"{m.group('base')}.Tex{m.group('ext')}")
                if self.game.has(tex, prel):
                    new = Result(r.data, REPLACED, r.kind, f"{r.detail}; left out of the pack (your game's "
                                 f"{tex.rsplit('/', 1)[-1]} is used)", drop=True,
                                 warnings=[f"not converted: left out of the pack, so your game's "
                                           f"{tex.rsplit('/', 1)[-1]} is used (the mod's textures in it are lost)"])
                else:
                    new = Result(r.data, LEFT_OUT, r.kind, f"{r.detail}; left out of the pack (the Switch game "
                                 "never loads an X.Tex1 / X.Tex2)", drop=True,
                                 warnings=["not converted: left out of the pack (the Switch game never loads this "
                                           "name, and your game has no X.Tex for it)"])
            else:
                twin = self.game.raw(n.name, prel)
                if twin is not None:
                    new = Result(twin, REPLACED, r.kind, f"{r.detail}; your game's file was put in the pack in its "
                                 "place", warnings=["not converted: your game's own file was put in the pack in its "
                                                    "place (the mod's change to this file is lost)"])
                elif unnamed:
                    continue
                else:
                    new = Result(r.data, LEFT_OUT, r.kind, f"{r.detail}; left out of the pack", drop=True,
                                 warnings=["not converted, and your game has no file of this name: left out of the "
                                           "pack (Wii U bytes there would crash the game when it loads them)"])
            results[sp] = new
            conv._renote(f"{path}//{n.name}", new)

    def fill(self, conv: Converter, path: str, s: Sarc, results: dict) -> list[tuple[str, bytes, int]]:
        """Files the game's own pack (same path) has and the converted pack lacks, when the pack holds models or
        textures and the game files are given (a Wii U pack names textures X.Tex1 / X.Tex2, the Switch X.Tex). A
        pack inside a pack is not filled. Your vanilla model goes in with a new TexInfo.txt when the mod's X.Tex has
        another texture count (_texinfo_fix)."""
        if self.game is None or "//" in path or conv.to != LE:
            return []
        if not any(r.kind in MODEL_KINDS for r in results.values()):
            return []
        root, rel, _ = self.where(path)
        if root != BASE_OUT:
            return []
        idx = self.game.pack_index(rel)
        if not idx:
            return []
        have = set()
        for n in s.nodes:
            r = results[(n.start, n.end)]
            if not r.drop:
                have.add((_renamed(n.name, r.rename) if r.rename else n.name).lower())
        out = []
        for name, (off, _size) in idx.items():
            if name.lower() in have:
                continue
            data = self.game.pack_entry(rel, name)
            fix = self.fixed.pop((path, name.lower()), None)
            if fix is not None:
                model_rel, pack, count, what = fix
                plain = wm_convert.with_texinfo(_plain(data), count)
                donor = self.game.donor(model_rel, pack)
                if donor:
                    self.outputs.append(ModelOutput(root, model_rel, "model", len(plain),
                                                    wm_convert.size_count(donor[0]),
                                                    (donor[1], wm_convert.size_count(donor[0]))))
                if data[:4] == b"Yaz0":
                    plain = yaz0_compress(plain, yaz0_decompress(data)[1]) if conv.recompress else data
                data = plain
                self.rewritten.append(f"{path}//{name}: {what}")
            out.append((name, data, _alignment_of(off)))
        if out:
            self.fills.append(f"{path}: {len(out)} file(s) from your game's {rel} ("
                              + ", ".join(n for n, _, _ in out[:4]) + (" ..." if len(out) > 4 else "") + ")")
        return out

    def rstb(self) -> tuple[bytes | None, dict]:
        """(the game's size table with the entries of the converted files raised, Yaz0; or None when nothing needs
        raising) and a summary. Needs the game files. X.Tex: decompressed size + 8208 (exact on the user's files);
        models and animation files: wm_convert.rstb_model (a generous guess); a converted model without a vanilla
        entry gets none (merge with UKMM). Files that came out the same as the game's need nothing."""
        info = {"raised": [], "added": [], "no_entry": [], "kept": 0}
        if self.game is None or not self.outputs:
            return None, info
        raw = self.game.loose(RSTB_PATH)
        if raw is None:
            info["no_rstb"] = True
            return None, info
        data, align = yaz0_decompress(raw) if raw[:4] == b"Yaz0" else (raw, 0)
        crcs, named = rstb_load(data)
        table = dict(crcs)
        names = {n.rstrip(b"\0").decode("utf-8", "replace"): i for i, (n, _) in enumerate(named)}
        named = list(named)
        changed = False
        for o in self.outputs:
            path = resource_path(o.root, o.rel)
            crc = zlib.crc32(path.encode("utf-8"))
            old = named[names[path]][1] if path in names else table.get(crc)
            if o.kind == "texture":
                new = wm_convert.rstb_tex(o.size)
            elif old is not None and o.vanilla is not None:
                new = wm_convert.rstb_model(o.size, o.count, old, o.vanilla[0], o.vanilla[1])
            else:
                info["no_entry"].append(path)
                continue
            if old is not None and new <= old:
                info["kept"] += 1
                continue
            if path in names:
                named[names[path]] = (named[names[path]][0], new)
            else:
                table[crc] = new
            info["added" if old is None else "raised"].append(f"{path} {old} -> {new}")
            changed = True
        if not changed:
            return None, info
        return yaz0_compress(rstb_build(list(table.items()), named), align), info

    def summary(self) -> dict:
        return {"game": self.game.romfs if self.game else None, "stats": dict(self.stats), "fills": self.fills,
                "same": len(self.same), "rewritten": self.rewritten}


SAME_NOTE = "the same as your game's file: its own bytes are used"


# ---------------------------------------------------------------------------------------------------- mod layout

@dataclass
class Layout:
    files: list[tuple[str, str]]               # (path in the mod, path in the Switch mod)
    other: list[str]                           # files outside content/ and aoc/0010/
    root: str                                  # the folder holding content/ (or "")
    unsafe: list[str] = field(default_factory=list)  # game files with a name that can't be written safely


def plan_layout(paths: list[str], top_name: str = "") -> Layout:
    """Map a Wii U mod's files (paths with "/") to the Switch layout. `top_name` is the name of the folder the
    paths are in: a mod given as its content/ folder (or aoc/0010) works too. A game file whose path, from
    content/ on, has a part Windows can't use or that could lead out of the output folder ("..", "C:", "a\\b",
    a trailing dot or space, a reserved name like "con") goes in `unsafe` and is never written."""
    low = [p.lower() for p in paths]
    roots: dict[tuple[str, ...], str] = {}
    files, other, unsafe = [], [], []
    whole = None
    if top_name.lower() == "content":
        whole = BASE_OUT
    elif top_name.lower() == "0010":
        whole = DLC_OUT
    for path, lp in zip(paths, low):
        parts, lparts = path.split("/"), lp.split("/")
        if any(x in JUNK or x.startswith(".") for x in lparts):
            other.append(path)
            continue
        if whole:
            if any(_bad_name(x) for x in parts):
                unsafe.append(path)
                continue
            files.append((path, f"{whole}/{path}"))
            continue
        dirs = lparts[:-1]
        hit = None
        for j, d in enumerate(dirs):
            if d == "content":
                hit = (j, j + 1, BASE_OUT)
                break
            if d == "aoc" and dirs[j + 1:j + 2] == ["0010"]:
                hit = (j, j + 2, DLC_OUT)
                break
        if not hit:
            other.append(path)
            continue
        j, k, out = hit
        roots.setdefault(tuple(lparts[:j]), "/".join(parts[:j]))
        if any(_bad_name(x) for x in parts[k:]):
            unsafe.append(path)
            continue
        files.append((path, f"{out}/{'/'.join(parts[k:])}"))
    if len(roots) > 1:
        shown = ", ".join(sorted((v or "(top)") for v in roots.values())[:4])
        raise ConvertError(f"holds several mods or options ({shown}): convert each folder on its own")
    if not files and not unsafe:
        if any("romfs" in lp.split("/")[:-1] for lp in low):
            raise ConvertError("is a Switch mod (it has a romfs folder): it needs no conversion")
        if "meta.yml" in low and "manifest.yml" in low:
            raise ConvertError("is a UKMM package: install it in UKMM (Switch mode) instead")
        raise ConvertError("has no content/ or aoc/0010/ folder: it doesn't look like a Wii U mod")
    root = next(iter(roots.values()), "")
    base = root.lower() + "/" if root else ""
    if base + "info.json" in low and any(lp.startswith(base + "logs/") for lp in low):
        # (as tools/romfs_mods.py and modman refuse it: its packs hold only what the mod changes)
        raise ConvertError("is a BCML package (BNP), whose packs only hold the changes: install it in UKMM or BCML "
                           "(Switch mode) instead")
    seen: dict[str, str] = {}
    for member, target in files:
        if target.lower() in seen:
            raise ConvertError(f"has {seen[target.lower()]} and {member}, which become the same file")
        seen[target.lower()] = member
    return Layout(files, other, root, unsafe)


class ModSource:
    """A mod folder or .zip: its file paths (with "/", wrapper folders kept) and a way to read them."""

    def __init__(self, path: str):
        p = Path(path)
        self.zip: zipfile.ZipFile | None = None
        self.root = p
        if p.is_dir():
            self.files = sorted(Path(d, n).relative_to(p).as_posix() for d, _, names in os.walk(p) for n in names)
            return
        # (the messages follow the mod's path, which main() and modman put in front)
        if not p.is_file():
            raise ConvertError("doesn't exist")
        with open(p, "rb") as f:
            magic = f.read(4)
        if not magic.startswith(b"PK"):
            raise ConvertError("isn't a folder or a .zip. Extract it first (7-Zip opens .7z, .rar and .bnp), then "
                               "use the folder")
        try:
            self.zip = zipfile.ZipFile(p)
        except zipfile.BadZipFile as e:
            raise ConvertError(f"is a damaged .zip ({e})") from None
        self.files = sorted(n for n in self.zip.namelist() if not n.endswith("/"))

    def read(self, rel: str, limit: int | None = None) -> bytes:
        try:
            if self.zip:
                data = self.zip.read(rel)
                return data if limit is None else data[:limit]
            with open(_long(str(self.root / rel)), "rb") as f:
                return f.read(-1 if limit is None else limit)
        except NotImplementedError:
            raise ConvertError(f"{rel}: this .zip uses a compression Python can't read; extract it first") from None
        except (OSError, zipfile.BadZipFile, EOFError) as e:
            raise ConvertError(f"can't read {rel}: {e}") from None


def _long(path: str) -> str:
    """A path Windows opens even past 260 characters."""
    p = os.path.abspath(path)
    if os.name == "nt" and len(p) > 240 and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + p
    return p


@dataclass
class ModReport:
    mod: str
    out: str = ""
    files: list[dict] = field(default_factory=list)     # one per game file of the mod: path, target, status...
    notes: list[Note] = field(default_factory=list)     # every file, nested ones too
    other: list[str] = field(default_factory=list)
    written: int = 0
    dropped: list[str] = field(default_factory=list)
    unsafe: list[str] = field(default_factory=list)     # game files left out for their name (see plan_layout)
    models: dict = field(default_factory=dict)          # ModelJob.summary() + "rstb" (Wii U models and textures)

    def by_status(self) -> Counter:
        return Counter(f["status"] for f in self.files)

    def attention(self) -> list[Note]:
        """Files with warnings (models and textures): converted ones work, but check them in game; one that couldn't
        be converted and gave way to your game's file (or was left out of a pack) lost the mod's change."""
        return [n for n in self.notes if n.warnings and (n.status in OK or n.status in (REPLACED, LEFT_OUT))]

    def model_lines(self) -> list[str]:
        """What the model converter used and added (for the Notes)."""
        m = self.models
        if not m or not any(m.get("stats", {}).values()):
            return []
        st = m["stats"]
        what = ", ".join(f"{v} {k}" for k, v in st.items() if v)
        out = [f"models and textures: {what} converted"
               + (f", with your game files from {m['game']} (vanilla materials, mipmaps, TexInfo.txt, packs, size "
                  "table)" if m.get("game") else
                  " without your game files (--game): materials field by field, no vanilla mipmaps for textures "
                  "without a Tex2, TexInfo.txt only from the mod's own X.Tex1, no size table")]
        if m.get("same"):
            out.append(f"{m['same']} file(s) came out the same as your game's: its own bytes were used (nothing "
                       "compressed again)")
        out += [f"pack filled: {x}" for x in m.get("fills", [])]
        out += [f"written again: {x}" for x in m.get("rewritten", [])]
        r = m.get("rstb") or {}
        if r.get("written"):
            out.append(f"size table: {r['written']} (the game's, with {len(r['raised'])} entr(y/ies) raised and "
                       f"{len(r['added'])} added for the converted files)")
        elif r.get("checked"):
            out.append("size table: not needed (no converted file is bigger than the game's entry for it)")
        if r.get("no_entry"):
            out.append(f"size table: no entry for {len(r['no_entry'])} new model file(s) ({_short(r['no_entry'])}): "
                       "the game has no size for them; if they crash on load, merge the mod with UKMM (Switch mode)")
        if r.get("blocked"):
            out.append(f"size table: not written, {r['blocked']}")
        return out

    def problems(self) -> list[Note]:
        """Files kept as Wii U data, outermost first: a pack kept whole is listed, not the files in it."""
        bad = {n.path for n in self.notes if n.status in (CANT, UNKNOWN, BROKEN)}
        out = []
        for n in self.notes:
            parts = n.path.split("//")
            if n.path in bad and not any("//".join(parts[:k]) in bad for k in range(1, len(parts))):
                out.append(n)
        return out

    def guesses(self) -> list[Note]:
        return [n for n in self.notes if n.kind == "MSBT" and n.detail]

    def verdict(self) -> str:
        st = self.by_status()
        usable = sum(st[s] for s in OK) + st[PARTLY]
        bad = sum(st[s] for s in (PARTLY, CANT, UNKNOWN, BROKEN, REPLACED)) + len(self.unsafe)
        if not usable:
            # every game file was left out (only the RSTB, say), or none could be converted
            return "nothing could be converted" if bad else "nothing to convert"
        if not bad:
            return "everything converted"
        return "converted in part"

    def exit_code(self) -> int:
        """0 everything converted, 1 in part, 2 nothing usable (convert then writes nothing)."""
        v = self.verdict()
        return 0 if v == "everything converted" else 2 if v in NOTHING else 1

    def counts(self) -> dict[str, int]:
        """What happened to the game files: modman keeps these (config.json's "wiiu" entry), and report_counts()
        reads them back from the report text."""
        st = self.by_status()
        return {"files": len(self.files) + len(self.unsafe), "converted": sum(st[s] for s in OK) + st[PARTLY],
                "partly": st[PARTLY], "wiiu_data": len(self.problems()),
                "left_out": st[LEFT_OUT] + len(self.unsafe) + len(self.other), "attention": len(self.attention())}

    def as_dict(self) -> dict:
        return {"mod": self.mod, "out": self.out, "verdict": self.verdict(),
                "counts": {LABEL[k]: v for k, v in self.by_status().items()},
                "files": self.files,
                "problems": [n.__dict__ for n in self.problems()],
                "attention": [n.__dict__ for n in self.attention()],
                "models": self.models,
                "unsafe_names_left_out": self.unsafe,
                "other_files_left_out": self.other, "dropped": self.dropped}

    def text(self) -> str:
        st = self.by_status()
        dlc = sum(1 for f in self.files if f["target"].startswith(DLC_OUT))
        lines = [f"Wii U mod: {self.mod}"]
        if self.out:
            lines.append(f"Switch mod written to: {self.out} ({self.written} file(s))")
        lines.append(f"{len(self.files)} game file(s): {len(self.files) - dlc} base game, {dlc} DLC")
        for s in STATUS_ORDER:
            if st[s]:
                kinds = Counter(f["kind"] for f in self.files if f["status"] == s)
                lines.append(f"  {LABEL[s]:<18} {st[s]:>5}  ({', '.join(f'{k} {v}' for k, v in kinds.most_common())})")
        probs = self.problems()
        if probs:
            lines.append("")
            lines.append("Still Wii U data (the game can't read these; kept unchanged):")
            for n in probs[:200]:
                lines.append(f"  {n.path}: {n.kind}" + (f" - {n.detail}" if n.detail else ""))
            if len(probs) > 200:
                lines.append(f"  ... and {len(probs) - 200} more")
        att = self.attention()
        if att:
            lines.append("")
            lines.append(f"Needs attention ({len(att)} file(s) with warnings; check them in game):")
            for n in att[:100]:
                lines.append(f"  {n.path}: {n.kind}" + (f" - {n.detail}" if n.detail else ""))
                lines += [f"    - {w}" for w in n.warnings[:8]]
                if len(n.warnings) > 8:
                    lines.append(f"    - ... and {len(n.warnings) - 8} more")
            if len(att) > 100:
                lines.append(f"  ... and {len(att) - 100} more")
        if self.unsafe:
            lines.append("")
            lines.append(f"Left out, unsafe file names ({len(self.unsafe)}): Windows can't use them, or they could "
                         "write outside the output folder:")
            lines += [f"  {p}" for p in self.unsafe[:50]]
            if len(self.unsafe) > 50:
                lines.append(f"  ... and {len(self.unsafe) - 50} more")
        notes = [f for f in self.files if f["status"] == LEFT_OUT] + [n.__dict__ for n in self.guesses()]
        mlines = self.model_lines()
        if notes or self.dropped or self.other or mlines:
            lines.append("")
            lines.append("Notes:")
            lines += [f"  {x}" for x in mlines]
            for f in notes:
                lines.append(f"  {f['path']}: {f['detail']}")
            if self.dropped:
                lines.append(f"  left out with --drop-unconverted: {len(self.dropped)} file(s)")
            if self.other:
                shown = ", ".join(self.other[:6]) + (" ..." if len(self.other) > 6 else "")
                lines.append(f"  not game data, left out ({len(self.other)}): {shown}")
        lines.append("")
        v = self.verdict()
        if v == "everything converted":
            lines.append("Result: everything converted.")
        elif v == "converted in part":
            lines.append("Result: converted in part. The files listed above are still Wii U data or left out: the "
                         "game may crash or look wrong where the mod uses them. UKMM (Switch mode) may handle more.")
        elif v == "nothing to convert":
            lines.append("Result: nothing to convert. Every game file of the mod is left out (see Notes).")
        else:
            lines.append("Result: nothing could be converted.")
        return "\n".join(lines) + "\n"


_FILES_RE = re.compile(r"^(\d+) game file\(s\): ")
_STATUS_RE = re.compile(r"^  (.{18}) +(\d+)  \(")
_MORE_RE = re.compile(r"^  \.\.\. and (\d+) more$")
_UNSAFE_RE = re.compile(r"^Left out, unsafe file names \((\d+)\)")
_OTHER_RE = re.compile(r"^  not game data, left out \((\d+)\)")
_ATTENTION_RE = re.compile(r"^Needs attention \((\d+) file\(s\)")


def _short(items: list[str], n: int = 3) -> str:
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def report_counts(text: str) -> dict | None:
    """ModReport.counts() and the verdict, read back from a report ModReport.text() wrote (convert leaves one in its
    output folder: modman reads it when that folder is added). None when the text isn't such a report."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("Wii U mod: "):
        return None
    verdict = next((v for ln in lines for v in ("everything converted", "converted in part") + NOTHING
                    if ln.startswith(f"Result: {v}.")), None)
    files = next((int(m.group(1)) for m in map(_FILES_RE.match, lines) if m), None)
    if verdict is None or files is None:
        return None
    label = {v: k for k, v in LABEL.items()}
    st: Counter = Counter()
    wiiu_data = unsafe = other = attention = 0
    section = ""
    for ln in lines:
        if not ln:
            section = ""
        elif _FILES_RE.match(ln):
            section = "counts"
        elif ln.startswith("Still Wii U data ("):
            section = "problems"
        elif section == "counts" and _STATUS_RE.match(ln):
            m = _STATUS_RE.match(ln)
            if m.group(1).strip() in label:
                st[label[m.group(1).strip()]] += int(m.group(2))
        elif section == "problems" and ln.startswith("  "):
            m = _MORE_RE.match(ln)
            wiiu_data += int(m.group(1)) if m else 1
        elif _UNSAFE_RE.match(ln):
            unsafe = int(_UNSAFE_RE.match(ln).group(1))
        elif _OTHER_RE.match(ln):
            other = int(_OTHER_RE.match(ln).group(1))
        elif _ATTENTION_RE.match(ln):
            attention = int(_ATTENTION_RE.match(ln).group(1))
    return {"verdict": verdict, "files": files + unsafe, "converted": sum(st[s] for s in OK) + st[PARTLY],
            "partly": st[PARTLY], "wiiu_data": wiiu_data, "left_out": st[LEFT_OUT] + unsafe + other,
            "attention": attention}


def _open(path: str) -> tuple[ModSource, Layout]:
    """The mod and where its files go, or a ConvertError saying why it can't be converted."""
    src = ModSource(path)
    if not any("romfs" in f.lower().split("/")[:-1] for f in src.files) and _cemu_pack(src) == "other":
        # (as tools/romfs_mods.py and modman refuse it; a Switch mod is refused below for what it is)
        raise ConvertError("is a Cemu graphic pack made for another game, not BotW (its rules.txt has no BotW title "
                           "id)")
    return src, plan_layout(src.files, Path(path).name if src.zip is None else "")


def _write_new(write_to: str, target: str, data: bytes, what: str) -> None:
    dest = os.path.join(write_to, *target.split("/"))
    if not _inside(dest, write_to):  # plan_layout keeps such names out; this is the second guard
        raise ConvertError(f"would write {what} outside the output folder")
    os.makedirs(_long(os.path.dirname(dest)), exist_ok=True)
    try:
        with open(_long(dest), "xb") as f:  # never replace a file
            f.write(data)
    except FileExistsError:
        raise ConvertError(f"would write {what} over a file already written") from None


def _run(path: str, conv: Converter, write_to: str | None = None, drop: bool = False,
         opened: tuple | None = None, collect: dict | None = None) -> ModReport:
    """Convert every game file of the mod (and write it under write_to). `opened`: (source, layout) when the mod is
    not a folder or .zip (verify); `collect`: {target: bytes} of what would be written."""
    src, layout = opened or _open(path)
    p = Path(path)
    rep = ModReport(mod=str(p), out=write_to or "", other=layout.other, unsafe=layout.unsafe)
    job = conv.models
    if job is not None:
        job.prepare(src, layout)
    targets = {t.lower() for _, t in layout.files}
    out_targets: set[str] = set()     # what is (or would be) written
    for member, target in layout.files:
        data = src.read(member)
        start = len(conv.notes)
        r = conv.file(data, member)
        if r.rename:
            new = _renamed(target, r.rename)
            if new.lower() != target.lower() and new.lower() in targets:
                r = Result(data, BROKEN, r.kind, f"the mod also has {new}, the name this file gets on the Switch")
                conv._renote(member, r)
            else:
                targets.add(new.lower())
                target = new
        rep.files.append({"path": member, "target": target, "status": r.status, "kind": r.kind,
                          "detail": r.detail})
        rep.notes.extend(conv.notes[start:])
        if r.status == LEFT_OUT or r.drop:
            continue
        if drop and r.status not in OK and r.status != PARTLY:
            rep.dropped.append(member)
            continue
        out_targets.add(target.lower())
        if collect is not None:
            collect[target] = r.data
        if write_to is not None:
            _write_new(write_to, target, r.data, member)
            rep.written += 1
    if job is not None:
        for target, plain, align, what in job.extras:   # (your vanilla model with a new TexInfo.txt)
            if target.lower() in out_targets:
                continue
            if collect is not None or write_to is not None:
                data = yaz0_compress(plain, align) if align is not None else plain
                if collect is not None:
                    collect[target] = data
                if write_to is not None:
                    _write_new(write_to, target, data, "your game's model")
                    rep.written += 1
            out_targets.add(target.lower())
            job.rewritten.append(f"{target}: {what}")
        rep.models = job.summary()
        data, info = job.rstb()
        info["checked"] = job.game is not None
        target = f"{BASE_OUT}/{RSTB_PATH}"
        if data is not None and target.lower() in out_targets:
            info["blocked"] = f"the mod has its own Switch size table at {target}"
        elif data is not None:
            info["written"] = target
            if collect is not None:
                collect[target] = data
            if write_to is not None:
                _write_new(write_to, target, data, "the size table")
                rep.written += 1
        rep.models["rstb"] = info
    return rep


def check_mod(path: str, game: str | None = None) -> ModReport:
    return _run(path, Converter(LE, recompress=False, models=ModelJob(GameFiles(game) if game else None)))


def _inside(child: str, parent: str) -> bool:
    c, p = os.path.normcase(os.path.abspath(child)), os.path.normcase(os.path.abspath(parent))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def convert_mod(path: str, out: str, *, drop_unconverted: bool = False, game: str | None = None) -> ModReport:
    """Write the Switch copy of the mod into out (new or empty). When no game file could be converted (only the RSTB,
    say), nothing is left in out: the report says why, and its verdict is one of NOTHING. `game`: the player's Switch
    RomFS (read only) for the model converter's vanilla help (materials, mipmaps, TexInfo.txt, packs, size table)."""
    out = os.path.abspath(out)
    if os.path.exists(out) and (not os.path.isdir(out) or os.listdir(out)):
        raise ConvertError(f"{out} already exists and isn't an empty folder: pick a new one", whole=True)
    if _inside(out, path) or _inside(path, out):
        raise ConvertError("the output folder can't be inside the mod (or the other way round)", whole=True)
    files = GameFiles(game) if game else None
    if files is not None and _inside(out, files.romfs):
        raise ConvertError("the output folder can't be inside the game folder", whole=True)
    _open(path)  # (fails early, before the output folder is made)
    existed = os.path.isdir(out)
    os.makedirs(out, exist_ok=True)

    def clean() -> None:
        shutil.rmtree(_long(out), ignore_errors=True)  # it was new or empty: only our files are in it
        if existed:
            os.makedirs(out, exist_ok=True)

    try:
        rep = _run(path, Converter(LE, models=ModelJob(files)), out, drop_unconverted)
        if rep.verdict() not in NOTHING:
            with open(os.path.join(out, REPORT), "w", encoding="utf-8") as f:
                f.write(rep.text())
    except BaseException:
        clean()
        raise
    if rep.verdict() in NOTHING:
        clean()  # (a Switch mod with no usable file in it: modman and romfs_mods would only take it by mistake)
        rep.out, rep.written = "", 0
    return rep


# ---------------------------------------------------------------------------------------------------- verification

def describe(data: bytes):
    """A file's content with byte order taken out: the same for a file and its converted copy if nothing was lost
    (packs are opened, Yaz0 decompressed, BYML/MSBT/RSTB read into values; other files stay bytes)."""
    fmt = identify(data, "")
    try:
        if fmt == "yaz0":
            raw, align = yaz0_decompress(data)
            return ("Yaz0", align, describe(raw))
        if fmt == "sarc":
            s = sarc_parse(data)
            return ("SARC", s.version, s.hash_key, [(n.name, n.hash, n.attr, describe(
                data[s.data_offset + n.start:s.data_offset + n.end])) for n in s.nodes])
        if fmt == "byml":
            return ("BYML", _u16(data, 2, byml_order(data)), byml_load(data))
        if fmt == "msbt":
            return ("MSBT", msbt_load(data))
        if fmt == "rstb":
            return ("RSTB", rstb_load(data))
    except (FormatError, struct.error, IndexError, ValueError):
        pass
    return ("bytes", bytes(data))


def same_unpacked(a: bytes, b: bytes) -> tuple[bool, list[str]]:
    """Whether b equals a once Yaz0 is decompressed at every level (and packs that had to be laid out again keep
    their names, order, header bytes, and every file at least as aligned as before). Also says why not."""
    if a == b:
        return True, []
    if a[:4] == b[:4] == b"Yaz0":
        if a[4:16] != b[4:16]:
            return False, ["Yaz0 header differs"]
        return same_unpacked(yaz0_decompress(a)[0], yaz0_decompress(b)[0])
    if a[:4] == b[:4] == b"SARC":
        sa, sb = sarc_parse(a), sarc_parse(b)
        if (sa.order, sa.data_offset, len(sa.nodes)) != (sb.order, sb.data_offset, len(sb.nodes)):
            return False, ["SARC header differs"]
        ha, hb = bytearray(a[:sa.data_offset]), bytearray(b[:sb.data_offset])
        for h, s in ((ha, sa), (hb, sb)):
            h[8:12] = bytes(4)
            for k in range(len(s.nodes)):
                h[s.sfat + 0xC + 16 * k + 8:s.sfat + 0xC + 16 * k + 16] = bytes(8)
        if ha != hb:
            return False, ["SARC names or table differ"]
        why = []
        for na, nb in zip(sa.nodes, sb.nodes):
            if (sb.data_offset + nb.start) % _alignment_of(sa.data_offset + na.start) and nb.end > nb.start:
                why.append(f"{na.name}: less aligned than before")
            ok, sub = same_unpacked(a[sa.data_offset + na.start:sa.data_offset + na.end],
                                    b[sb.data_offset + nb.start:sb.data_offset + nb.end])
            why += [f"{na.name}: {w}" for w in sub] if not ok else []
        return not why, why
    return False, ["bytes differ"]


def _pick(romfs: str, count: int, seed: int) -> list[tuple[str, str]]:
    """(group, path inside romfs) samples of each convertible format."""
    rnd = random.Random(seed)

    def files(sub: str, pattern: str) -> list[str]:
        base = os.path.join(romfs, *sub.split("/"))
        if not os.path.isdir(base):
            return []
        found = []
        for d, _dirs, names in os.walk(base):
            for n in names:
                if re.fullmatch(pattern, n, re.I):
                    found.append(os.path.relpath(os.path.join(d, n), romfs).replace("\\", "/"))
        return sorted(found)

    def some(items: list[str], n: int) -> list[str]:
        return sorted(rnd.sample(items, min(n, len(items))))

    out = []
    out += [("SARC actor packs (Yaz0, AAMP and BYML inside)", p)
            for p in some(files("Actor/Pack", r".*\.sbactorpack"), count)]
    out += [("SARC packs with nested Yaz0 SARCs (MSBT texts)", p)
            for p in files("Pack", r"Bootup_[A-Z]{2}[a-z]{2}\.pack")]
    out += [("SARC dungeon and Divine Beast packs (nested Yaz0 maps and actor packs)", p)
            for p in some(files("Pack", r"(Dungeon\d+|Remains\w+)\.pack"), max(2, count // 5))]
    out += [("SARC Bootup.pack (big, nested)", p) for p in files("Pack", r"Bootup\.pack")]
    out += [("BYML maps", p) for p in some(files("Map", r".*\.smubin"), count)]
    out += [("BYML other", p) for p in files("Actor", r"ActorInfo\.product\.sbyml")]
    out += [("RSTB", p) for p in files("System/Resource", r".*\.srsizetable")]
    return out


class MemSource:
    """A mod held in memory, {path: bytes} (verify builds Wii U mods from the player's Switch files this way)."""

    def __init__(self, files: dict[str, bytes]):
        self.data = files
        self.files = sorted(files)
        self.zip = None

    def read(self, rel: str, limit: int | None = None) -> bytes:
        d = self.data[rel]
        return d if limit is None else d[:limit]


MODEL_SAMPLE_MAX = 600_000      # compressed bytes per sampled model / X.Tex (keeps the run light)


def _pick_models(romfs: str, count: int, seed: int) -> list[tuple[str, str]]:
    """(group, RomFS path) model samples: `count` loose models with their X.Tex, one animation file, two item icons,
    and Link in Pack/TitleBG.pack."""
    rnd = random.Random(seed)
    mdir = os.path.join(romfs, "Model")
    names = set(os.listdir(mdir)) if os.path.isdir(mdir) else set()

    def small(n: str) -> bool:
        return os.path.getsize(os.path.join(mdir, n)) <= MODEL_SAMPLE_MAX

    pairs = sorted(n for n in names if n.endswith(".sbfres") and ".Tex" not in n and "_Animation" not in n
                   and n[:-7] + ".Tex.sbfres" in names)
    pairs = [n for n in rnd.sample(pairs, min(len(pairs), count * 4)) if small(n) and small(n[:-7] + ".Tex.sbfres")]
    out = [("models with X.Tex1 + X.Tex2", f"Model/{n}") for n in sorted(pairs[:count])]
    anims = sorted(n for n in names if n.endswith("_Animation.sbfres") and small(n))
    out += [("animation files", f"Model/{n}") for n in rnd.sample(anims, min(1, len(anims)))]
    idir = os.path.join(romfs, "UI", "StockItem")
    icons = sorted(n for n in os.listdir(idir) if n.endswith(".sbitemico")) if os.path.isdir(idir) else []
    out += [("item icons", f"UI/StockItem/{n}") for n in sorted(rnd.sample(icons, min(2, len(icons))))]
    if os.path.isfile(os.path.join(romfs, "Pack", "TitleBG.pack")):
        out.append(("Link in Pack/TitleBG.pack (pack rebuilt and filled)", "Pack/TitleBG.pack//Model/Link.sbfres"))
    return out


def _convert_mem(files: dict[str, bytes], game: GameFiles | None) -> tuple[ModReport, dict]:
    out: dict[str, bytes] = {}
    src = MemSource(files)
    rep = _run("verify", Converter(LE, models=ModelJob(game)), opened=(src, plan_layout(src.files)), collect=out)
    return rep, out


def _model_diff(orig: bytes, new: bytes | None) -> list[str]:
    if new is None:
        return ["not written"]
    a, b = nx_model.read(orig), nx_model.read(_plain(new))
    return [f"model {x}" for x in model_describe_diff(a, b)]


def _tex_diff(orig: bytes, new: bytes | None, hints: list | None = None) -> list[str]:
    """Differences of two Switch texture files, value by value (every mip's blocks). `hints`: when given, a different
    BNTX swizzle field is not a difference but goes into the list (without the game files the converter uses the
    role default: Alb 0, Nrm 2, others 3)."""
    if new is None:
        return ["not written"]
    new = _plain(new)
    if new == orig:
        return []
    a, b = nx_bfres.read_textures(orig), nx_bfres.read_textures(new)
    if hints is not None:
        for x, y in zip(a.textures, b.textures):
            if x.swizzle_hint != y.swizzle_hint:
                hints.append(f"{x.name} {x.swizzle_hint}->{y.swizzle_hint}")
                y.swizzle_hint = x.swizzle_hint
    return [f"textures {x}" for x in model_describe_diff(a, b)]


def _plain(data: bytes) -> bytes:
    return yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data


def model_describe_diff(a, b) -> list[str]:
    from wiiu_model.neutral import diff
    return diff(a, b, "cross", 5)


def _change_param(res) -> str | None:
    """Change one float shader param of the first material that has one (to 1.0, or 2.0 if it was 1.0); what was
    changed, or None."""
    for m in res.models:
        for mat in m.materials:
            for p in mat.params:
                if p.type == 12 and p.size == 4 and p.offset + 4 <= len(mat.param_data):
                    data = bytearray(mat.param_data)
                    one = struct.pack("<f", 1.0)
                    data[p.offset:p.offset + 4] = struct.pack("<f", 2.0) if data[p.offset:p.offset + 4] == one else one
                    mat.param_data = bytes(data)
                    return f"{mat.name}/{p.name}"
    return None


def _verify_b(game: GameFiles, rel: str, tex_rel: str, t1: str, orig_m: bytes, orig_t: bytes) -> dict:
    """Scenario B of a model sample (see _verify_model): the mod's model changes one param, its Tex1 one texture's
    main image (the first texture's first 16 bytes inverted); only those may differ from your game's files."""
    from wiiu_model.neutral import diff
    out_b: dict = {"problems": []}
    mod_res = nx_model.read(orig_m)
    out_b["b_changed"] = [_change_param(mod_res) or "no float param to change"]
    mod_ts = nx_bfres.read_textures(orig_t)
    tex = next((t for t in mod_ts.textures if t.mips > 1), mod_ts.textures[0])
    lv0 = bytes(b ^ 0xFF for b in tex.levels[0][:16]) + tex.levels[0][16:]
    had_mips = tex.mips > 1
    tex.levels = [lv0] + tex.levels[1:]
    out_b["b_changed"].append(f"{tex.name} main image")
    tex1 = wiiu_bfres.write_textures(mod_ts)[0]
    rep, out = _convert_mem({f"content/{rel}": wiiu_models.write(mod_res, keep_render_infos=False),
                             f"content/{t1}": tex1}, game)
    new_m, new_t = out.get(f"romfs/{rel}"), out.get(f"romfs/{tex_rel}")
    if new_m is None or new_t is None:
        out_b["problems"].append("model or X.Tex not written")
        return out_b
    tex.levels = [lv0]                                   # what must come out: that texture with one mip
    got_t = nx_bfres.read_textures(_plain(new_t))
    out_b["problems"] += [f"model {x}" for x in diff(mod_res, nx_model.read(_plain(new_m)), "cross", 5)]
    out_b["problems"] += [f"textures {x}" for x in diff(mod_ts, got_t, "cross", 5)]
    out_b["b_mips"] = {t.name: t.mips for t in got_t.textures if t.name == tex.name}
    out_b["b_verdict"] = rep.verdict()
    out_b["b_attention"] = [w for n in rep.attention() for w in n.warnings][:3]
    if had_mips and not any("no mipmaps for 1 texture(s)" in w for n in rep.attention() for w in n.warnings):
        out_b["problems"].append("no warning for the texture without mipmaps")
    rstb = rep.models.get("rstb", {})
    out_b["rstb"] = {k: rstb.get(k) for k in ("raised", "added", "no_entry", "kept", "written")}
    return out_b


def _verify_model(game: GameFiles, group: str, rel: str) -> dict:
    """One model sample through the mod converter: Switch file -> our Wii U writer (materials as a real Wii U file has
    them: a RenderState, not the 16 render infos) -> wiiu_convert -> Switch, without and with the game files."""
    row: dict = {"group": group, "path": rel, "problems": []}
    probs = row["problems"]
    if group.startswith("item icons"):
        orig = game.find(rel)
        ts = nx_bfres.read_textures(orig)
        wu = wiiu_bfres.write_textures(ts)[0]
        rep, out = _convert_mem({f"content/{rel}": wu}, game)
        probs += _tex_diff(orig, out.get(f"romfs/{rel}"))
        row["textures"] = len(ts.textures)
        row["identical"] = _plain(out.get(f"romfs/{rel}", b"")) == orig
    elif group.startswith("animation"):
        orig = game.find(rel)
        res = nx_model.read(orig)
        rep, out = _convert_mem({f"content/{rel}": wiiu_models.write(res, keep_render_infos=False)}, None)
        probs += _model_diff(orig, out.get(f"romfs/{rel}"))
        row["animations"] = [len(res.skel_anims), len(res.mat_anims)]
    elif "//" not in rel:
        tex_rel = rel[:-7] + ".Tex.sbfres"
        orig_m, orig_t = game.find(rel), game.find(tex_rel)
        res = nx_model.read(orig_m)
        wu_m = wiiu_models.write(res, keep_render_infos=False)
        tex1, tex2 = wiiu_bfres.write_textures(nx_bfres.read_textures(orig_t))
        t1, t2 = rel[:-7] + ".Tex1.sbfres", rel[:-7] + ".Tex2.sbfres"
        files = {f"content/{rel}": wu_m, f"content/{t1}": tex1}
        if tex2 is not None:
            files[f"content/{t2}"] = tex2
        # A: no game files, Tex1 + Tex2: field-by-field materials, TexInfo.txt from the Tex1
        rep, out = _convert_mem(files, None)
        row["a_hint_default"] = []
        probs += [f"A {x}" for x in _model_diff(orig_m, out.get(f"romfs/{rel}"))]
        probs += [f"A {x}" for x in _tex_diff(orig_t, out.get(f"romfs/{tex_rel}"), row["a_hint_default"])]
        if any(k.endswith((".Tex1.sbfres", ".Tex2.sbfres")) for k in out):
            probs.append("A: a Tex1 / Tex2 file was written")
        row["a_verdict"] = rep.verdict()
        row["a_tex_identical"] = _plain(out.get(f"romfs/{tex_rel}", b"")) == orig_t
        # B: game files, the Tex1 only, and the mod changed one material param and one texture's main image: the
        # materials are built on your vanilla ones and the other textures keep your mipmaps, so exactly those two
        # changes must come out (the changed texture with 1 mip: its main image no longer matches your game's)
        b = _verify_b(game, rel, tex_rel, t1, orig_m, orig_t)
        probs += [f"B {x}" for x in b.pop("problems")]
        row.update(b)
        row["models"] = len(res.models)
        row["materials"] = sum(len(m.materials) for m in res.models)
        row["textures"] = len(nx_bfres.read_textures(orig_t).textures)
        row["animations"] = [len(res.skel_anims), len(res.mat_anims)]
    else:
        pack, inner = rel.split("//")
        orig_m = game.find(inner, pack)
        tex_inner = inner[:-7] + ".Tex.sbfres"
        orig_t = game.find(tex_inner, pack)
        tex1, tex2 = wiiu_bfres.write_textures(nx_bfres.read_textures(orig_t))
        wu = sarc_build([(n, d) for n, d in ((inner, wiiu_models.write(nx_model.read(orig_m), keep_render_infos=False)),
                                             (inner[:-7] + ".Tex1.sbfres", tex1), (inner[:-7] + ".Tex2.sbfres", tex2))
                         if d is not None], BE)
        rep, out = _convert_mem({f"content/{pack}": wu}, game)
        sw = out.get(f"romfs/{pack}")
        if sw is None:
            probs.append("pack not written")
        else:
            s = sarc_parse(sw)
            got = {n.name: (sw[s.data_offset + n.start:s.data_offset + n.end], s.data_offset + n.start)
                   for n in s.nodes}
            vanilla = set(game.pack_index(pack) or {})
            row["pack_files"] = len(got)
            row["filled"] = len(set(got) - {inner, tex_inner})
            if set(got) != vanilla:
                probs.append(f"pack names differ from the game's: {sorted(set(got) ^ vanilla)[:4]}")
            probs += _model_diff(orig_m, got.get(inner, (None,))[0])
            probs += _tex_diff(orig_t, got.get(tex_inner, (None,))[0])
            for n in (inner, tex_inner):
                if n in got and got[n][0][:4] == b"FRES" and got[n][1] % 0x1000:
                    probs.append(f"{n} is not 0x1000-aligned in the pack")
            if s.order != LE:
                probs.append("pack is not little-endian")
        rstb = rep.models.get("rstb", {})
        row["rstb"] = {k: rstb.get(k) for k in ("raised", "added", "no_entry", "kept", "written")}
        row["verdict"] = rep.verdict()
    return row


TITLEBG = "Pack/TitleBG.pack"


def _verify_titlebg(game: GameFiles, say=print) -> dict:
    """Every model, animation and texture file of your Pack/TitleBG.pack as a Wii U mod ships them (a Wii U pack
    replaces the whole pack, so a Linkle-style mod brings every vanilla file in it as Wii U data): our Wii U writer
    makes each one (X.Tex as X.Tex1 + X.Tex2), stored as Yaz0 with literal bytes only (made at once; a real mod's
    Yaz0 takes longer to decompress), and the pack goes through the mod converter with your game files. Each file
    must come out as your game's: its own bytes, or the same values. Both steps are timed; run it at low priority."""
    row: dict = {"group": "whole Pack/TitleBG.pack: its models, animations and textures as Wii U files", "path": TITLEBG,
                 "problems": []}
    probs = row["problems"]
    idx = game.pack_index(TITLEBG) or {}
    names = [n for n in idx if MODEL_EXT_RE.search(n)]
    t0 = time.time()
    files, wiiu_bytes = [], 0
    for n in names:
        dec = _plain(game.pack_entry(TITLEBG, n))
        m = re.match(r"^(?P<base>.+)\.Tex(?P<ext>\.s?bfres)$", n)
        try:
            if m:
                pair = wiiu_bfres.write_textures(nx_bfres.read_textures(dec))
                made = [(f"{m.group('base')}.Tex{k}{m.group('ext')}", d) for k, d in zip((1, 2), pair)]
            else:
                made = [(n, wiiu_models.write(nx_model.read(dec), keep_render_infos=False))]
        except (ModelError, FormatError, struct.error, ValueError) as e:
            probs.append(f"{n}: our Wii U writer refused it ({e})")
            continue
        for wn, wd in made:
            if wd is not None:
                wiiu_bytes += len(wd)
                files.append((wn, yaz0_store(wd)))
    wu = sarc_build(files, BE)
    row.update(bfres=len(names), wiiu_files=len(files), wiiu_mb=wiiu_bytes / 1e6,
               build_seconds=round(time.time() - t0, 1))
    del files
    say(f"  Wii U TitleBG.pack built: {len(names)} model/texture files, {round(wiiu_bytes / 1e6, 1)} MB "
        f"({row['build_seconds']} s); converting")
    t1 = time.time()
    rep, out = _convert_mem({f"content/{TITLEBG}": wu}, game)
    del wu
    row["convert_seconds"] = round(time.time() - t1, 1)
    row["verdict"] = rep.verdict()
    row["same_reported"] = rep.models.get("same", 0)
    row["vanilla_bytes"] = row["value_equal"] = 0
    sw = out.pop(f"romfs/{TITLEBG}", None)
    if sw is None:
        probs.append("pack not written")
        return row
    s = sarc_parse(sw)
    got = {n.name: sw[s.data_offset + n.start:s.data_offset + n.end] for n in s.nodes}
    if set(got) != set(idx):
        probs.append(f"pack names differ from the game's: {sorted(set(got) ^ set(idx))[:4]}")
    for n in names:
        if n not in got:
            continue
        van = game.pack_entry(TITLEBG, n)
        if got[n] == van:
            row["vanilla_bytes"] += 1
            continue
        try:
            dif = (_tex_diff if ".Tex." in n else _model_diff)(_plain(van), got[n])
        except (ModelError, FormatError, struct.error, ValueError) as e:
            dif = [f"{type(e).__name__}: {e}"]
        if dif:
            probs.append(f"{n}: {dif[:2]}")
        else:
            row["value_equal"] += 1
    row["attention"] = [f"{n.path.rsplit('//', 1)[-1]}: {n.warnings[0]}" for n in rep.attention()][:5]
    return row


def verify(romfs: str, out: str, count: int = 20, seed: int = 1, say=print, groups: str = "all") -> dict:
    """Round trip of sampled Switch files: Switch -> Wii U byte order -> Switch, and models / textures through the
    model converter. Reads romfs only; writes the report (text and JSON) into `out`."""
    romfs = os.path.abspath(romfs)
    if os.path.isdir(os.path.join(romfs, "romfs")):
        romfs = os.path.join(romfs, "romfs")
    if not os.path.isdir(os.path.join(romfs, "Pack")):
        raise ConvertError(f"{romfs} isn't a game RomFS folder (no Pack folder)", whole=True)
    if _inside(out, romfs):
        raise ConvertError("the report folder can't be inside the game folder", whole=True)
    samples = _pick(romfs, count, seed) if groups in ("all", "data") else []
    if groups == "titlebg" and not os.path.isfile(os.path.join(romfs, *TITLEBG.split("/"))):
        raise ConvertError(f"{romfs} has no {TITLEBG}", whole=True)
    to_be, to_le = Converter(BE, rstb="convert"), Converter(LE, rstb="convert")
    rows = []
    t0 = time.time()
    for group, rel in samples:
        with open(os.path.join(romfs, *rel.split("/")), "rb") as f:
            orig = f.read()
        t = time.time()
        n0 = len(to_be.notes)
        be = to_be.file(orig, rel)
        inner = to_be.notes[n0:]
        back = to_le.file(be.data, rel)
        row = {"group": group, "path": rel, "size": len(orig), "status": be.status,
               "converted_inside": dict(Counter(n.kind for n in inner if n.status == CONVERTED)),
               "kept_inside": dict(Counter(n.kind for n in inner if n.status not in (CONVERTED, ALREADY))),
               "identical": back.data == orig, "kept": be.data == orig}
        problems = []
        if be.data == orig and be.status == CONVERTED:
            problems.append("conversion changed nothing")
        if not row["identical"]:
            ok, why = same_unpacked(orig, back.data)
            if not ok:
                problems += why[:5]
            row["same_after_decompression"] = ok
        if describe(orig) != describe(be.data):
            problems.append("the Wii U byte order copy reads differently")
        row["problems"] = problems
        row["seconds"] = round(time.time() - t, 2)
        rows.append(row)
        say(f"  {'OK ' if not problems else 'BAD'} {rel} ({row['seconds']} s)"
            + ("" if row["identical"] else " - same after decompression" if not problems else f": {problems}"))
    fmt_counts = Counter()
    for n in to_be.notes:
        fmt_counts[(n.kind, n.status)] += 1
    data_seconds = round(time.time() - t0, 1)
    model_rows = []
    if groups in ("all", "models") and os.path.isdir(os.path.join(romfs, "Model")):
        game = GameFiles(romfs)
        for group, rel in _pick_models(romfs, max(2, count // 2), seed):
            t = time.time()
            try:
                row = _verify_model(game, group, rel)
            except (ModelError, FormatError, ConvertError, struct.error, ValueError, KeyError) as e:
                row = {"group": group, "path": rel, "problems": [f"{type(e).__name__}: {e}"]}
            row["seconds"] = round(time.time() - t, 2)
            model_rows.append(row)
            say(f"  {'OK ' if not row['problems'] else 'BAD'} {rel} ({row['seconds']} s)"
                + (f": {row['problems'][:3]}" if row["problems"] else ""))
    if groups == "titlebg":
        t = time.time()
        row = _verify_titlebg(GameFiles(romfs), say)
        row["seconds"] = round(time.time() - t, 2)
        model_rows.append(row)
        say(f"  {'OK ' if not row['problems'] else 'BAD'} {TITLEBG} ({row['seconds']} s)"
            + (f": {row['problems'][:3]}" if row["problems"] else ""))
    result = {
        "romfs": romfs, "samples": len(rows), "seconds": round(time.time() - t0, 1), "data_seconds": data_seconds,
        "byte_identical": sum(r["identical"] and not r["kept"] for r in rows),
        "kept_unchanged": sum(r["kept"] for r in rows),
        "same_after_decompression": sum(1 for r in rows if not r["identical"] and not r["problems"]),
        "failed": [r["path"] for r in rows + model_rows if r["problems"]],
        "files_by_format": {f"{k} {LABEL[s]}": v for (k, s), v in sorted(fmt_counts.items())},
        "byml_nodes": dict(to_be.byml_nodes),
        "aamp_flags": {f"0x{k:x}": v for k, v in to_be.aamp_flags.items()},
        "msbt_unknown_tags": dict(to_be.tag_guesses),
        "rows": rows,
        "model_rows": model_rows,
    }
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, VERIFY_REPORT + ".json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    with open(os.path.join(out, VERIFY_REPORT + ".txt"), "w", encoding="utf-8") as f:
        f.write(verify_text(result))
    return result


def verify_text(r: dict) -> str:
    lines = []
    mr = r.get("model_rows") or []
    if mr:
        bad = [x for x in mr if x["problems"]]
        lines += [f"Models and textures through the mod converter, {len(mr)} sample(s) of {r['romfs']} "
                  f"({round(r['seconds'] - r.get('data_seconds', 0), 1)} s): {len(mr) - len(bad)} passed, "
                  f"{len(bad)} failed",
                  "  (each: your Switch file -> our Wii U writer, materials with a RenderState as on Wii U -> "
                  "wiiu_convert -> Switch, compared value by value; models A without your game files, Tex1 + Tex2, "
                  "everything must come back; B with your game files and the Tex1 only, the mod changing one "
                  "material param and one texture's main image: exactly those must come out different)"]
        for x in mr:
            bits = []
            if "materials" in x:
                bits.append(f"{x['models']} model(s), {x['materials']} material(s), {x['textures']} texture(s)")
            if x.get("animations") and any(x["animations"]):
                bits.append(f"animations {x['animations'][0]} skeletal / {x['animations'][1]} material")
            if "a_tex_identical" in x:
                bits.append(f"X.Tex A {'byte-identical' if x['a_tex_identical'] else 'same blocks'}")
            if x.get("b_changed"):
                bits.append(f"B changed {', '.join(x['b_changed'])}: "
                            + ("only these came out different" if not any(p.startswith("B ") for p in x["problems"])
                               else "other values differ too")
                            + f" (mips {x.get('b_mips')})")
            if "bfres" in x:
                bits.append(f"{x['bfres']} model/texture files ({x['wiiu_files']} Wii U files, "
                            f"{round(x['wiiu_mb'], 1)} MB), {x['vanilla_bytes']} written as your game's own bytes, "
                            f"{x['value_equal']} equal value by value; Wii U pack built in {x['build_seconds']} s, "
                            f"converted in {x['convert_seconds']} s")
            if x.get("a_hint_default"):
                bits.append(f"A swizzle field by role differs for {len(x['a_hint_default'])} "
                            f"({', '.join(x['a_hint_default'][:2])})")
            if x.get("rstb"):
                rs = x["rstb"]
                bits.append(f"RSTB: {len(rs.get('raised') or [])} raised, {rs.get('kept')} kept"
                            + (f" ({'; '.join(rs['raised'][:2])})" if rs.get("raised") else ""))
            if "filled" in x:
                bits.append(f"pack rebuilt: {x['pack_files']} files, {x['filled']} filled from the game")
            if "identical" in x:
                bits.append("byte-identical" if x["identical"] else "same blocks")
            if x.get("b_attention"):
                bits.append(f"warnings {x['b_attention'][:2]}")
            if x["problems"]:
                bits.append(f"PROBLEMS {x['problems'][:4]}")
            lines.append(f"  {'OK ' if not x['problems'] else 'BAD'} {x['path']} ({x.get('seconds')} s): "
                         + "; ".join(bits))
        lines.append("")
    if not r["samples"]:
        return "\n".join(lines) + "\n"
    lines += [f"Round trip Switch -> Wii U -> Switch on {r['samples']} file(s) of {r['romfs']} "
             f"({r.get('data_seconds', r['seconds'])} s)",
             f"  converted and back, byte-identical: {r['byte_identical']}",
             f"  same after decompression (our Yaz0 encoder makes other bytes than Nintendo's): "
             f"{r['same_after_decompression']}",
             f"  kept unchanged (nothing inside converts): {r['kept_unchanged']}",
             f"  failed: {len(r['failed'])}" + (f" ({', '.join(r['failed'][:10])})" if r["failed"] else ""),
             "Files met (nested ones too), in the Switch -> Wii U step:"]
    lines += [f"  {k}: {v}" for k, v in r["files_by_format"].items()]
    lines.append("BYML values converted: " + ", ".join(f"{k} {v}" for k, v in sorted(r["byml_nodes"].items())))
    lines.append("AAMP flags seen: " + ", ".join(f"{k}: {v}" for k, v in r["aamp_flags"].items())
                 + " (bit 0 = little-endian)")
    if r["msbt_unknown_tags"]:
        lines.append(f"MSBT tags with a guessed layout: {r['msbt_unknown_tags']}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------------------- CLI

def _safe_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _safe_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    game_help = ("your game's RomFS (Savage Wind's extracted\\romfs), read only: models get your vanilla materials, "
                 "TexInfo.txt and size table entries; textures without a Tex2 keep the vanilla mipmaps")
    c = sub.add_parser("check", help="say what can be converted, without writing anything")
    c.add_argument("mod", help="Wii U mod folder or .zip")
    c.add_argument("--json", action="store_true", help="print JSON instead of text")
    c.add_argument("--game", help=game_help)
    v = sub.add_parser("convert", help="write a Switch-layout copy of the mod")
    v.add_argument("mod", help="Wii U mod folder or .zip")
    v.add_argument("--out", required=True, help="new (or empty) folder for the Switch mod")
    v.add_argument("--drop-unconverted", action="store_true",
                   help="leave out files that stay Wii U data, instead of keeping them unchanged")
    v.add_argument("--game", help=game_help)
    r = sub.add_parser("verify", help="round-trip your own Switch files to prove nothing is lost")
    r.add_argument("romfs", help="the game's RomFS folder (Savage Wind's extracted\\romfs)")
    r.add_argument("--out", required=True, help="folder for the report (keep it out of the repo, e.g. out\\)")
    r.add_argument("--count", type=int, default=20, help="files sampled per group (default 20; models: half)")
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--groups", choices=("all", "data", "models", "titlebg"), default="all",
                   help="data (packs, maps, texts), models (models and textures) or all (default); titlebg: every "
                        "model and texture of Pack/TitleBG.pack as one Wii U pack (minutes: run it at low priority)")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "check":
            rep = check_mod(args.mod, args.game)
            print(json.dumps(rep.as_dict(), indent=1) if args.json else rep.text(), end="" if not args.json else "\n")
            return rep.exit_code()
        if args.cmd == "convert":
            rep = convert_mod(args.mod, args.out, drop_unconverted=args.drop_unconverted, game=args.game)
            print(rep.text(), end="")
            if rep.verdict() in NOTHING:
                print("Nothing written: no game file of the mod could be converted, so the game couldn't use it.")
                return rep.exit_code()
            print(f"Report: {os.path.join(os.path.abspath(args.out), REPORT)}")
            print(f'Next: python tools/modman.py add "{os.path.abspath(args.out)}"')
            return rep.exit_code()
        result = verify(args.romfs, args.out, args.count, args.seed, say=lambda s: print(s, flush=True),
                        groups=args.groups)
        print(verify_text(result), end="")
        return 0 if not result["failed"] else 1
    except ConvertError as e:
        mod = getattr(args, "mod", None)
        print(f"error: {e}" if e.whole or not mod else f"error: {mod} {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
