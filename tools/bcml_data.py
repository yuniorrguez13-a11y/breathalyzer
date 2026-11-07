#!/usr/bin/env python3
"""Merge BCML's data logs into the game's files: actor info, game data flags, save data flags, event info, status
effects, area data and resident actors. tools/bnp.py routes a package's logs here (see `read_log`, `combine`, `apply`).

A BCML 3 package keeps some of its changes only as logs (logs/<name>, options/<folder>/logs/<name>), written as
BYML text (YAML with typed numbers). This module reads them, combines the logs of one package (the base, then each
chosen option) and of several packages (lower priority first), and applies the result to the game's own file:

  actorinfo.yml  Actor/ActorInfo.product.sbyml               actors by CRC-32 of their name: new ones added, changed
                                                             keys merged (maps merge, lists and values are replaced);
                                                             across mods the largest instSize wins
  gamedata.yml   Pack/Bootup.pack//GameData/gamedata.ssarc   flags by DataName per type: added or replaced, deleted;
                                                             the game's files stay (only changed ones are rewritten),
                                                             new flags go into the last non-revival file of their type
  savedata.yml   Pack/Bootup.pack//GameData/savedataformat.ssarc   save entries by HashValue: added, deleted, sorted
  eventinfo.yml  Pack/Bootup.pack//Event/EventInfo.product.sbyml   events added or replaced whole
  effects.yml    Pack/Bootup.pack//Ecosystem/StatusEffectList.sbyml effects replaced whole
  areadata.yml   Pack/Bootup.pack//Ecosystem/AreaData.sbyml         areas by AreaNumber: changed keys merged
  residents.yml  Pack/Bootup.pack//Actor/ResidentActors.byml        residents by name: added, replaced, removed

The merge rules are BCML's (read on GitHub for the formats and rules only; this is our own code), except where ours
are safer: gamedata `del` lists are joined instead of the last one winning, the game's gamedata file layout is kept,
and the save data revision is the game's (BCML hardcodes an older one). A log this can't read is refused whole, with
a message; parts it can't apply are named in the notes ("needs attention"). Nothing here writes files: callers get
bytes. The BYML types, reader and writer and the reader for BCML's BYML text below are the one copy the BCML log
modules share (tools/bcml_maps.py uses them too).
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import struct
import sys
import zlib
from functools import reduce

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bnp  # noqa: E402
import ukmm  # noqa: E402
import wiiu_convert as wc  # noqa: E402

LOGS = {
    "actorinfo.yml": "actor info",
    "gamedata.yml": "game data flags",
    "savedata.yml": "save data flags",
    "eventinfo.yml": "event info",
    "effects.yml": "status effects",
    "areadata.yml": "area data",
    "residents.yml": "resident actors",
}
MAX_DEPTH = 64                        # BotW's BYML files nest at most ~8 deep; more is refused, not followed
MAX_LOG = 256 << 20                   # a log's size (the game's whole game data is 6.5 MB)
MAX_FLOW = 1 << 20                    # one flow collection ([..] / {..}) in a log
GAMEDATA_CHUNK = 4096                 # flags per game data file (the game's own files hold at most this many)
SAVEDATA_CHUNK = 8192                 # entries per game_data.sav format file (the game's own files)
BOOTUP = "Pack/Bootup.pack"


class LogError(Exception):
    """A log that can't be read or used. The message is for the player."""


# ---------------------------------------------------------------------------------------------------- typed values
# BYML types beyond Python's own: int is I32, float is F32 (rounded to 32 bits), bool, str, None, list, dict, bytes
# (binary data). These subclasses mark the other number types. (tools/byml.py will hold the shared copy.)

class U32(int):
    """An unsigned 32-bit BYML value (type 0xD3)."""


class I64(int):
    """A signed 64-bit BYML value (type 0xD4)."""


class U64(int):
    """An unsigned 64-bit BYML value (type 0xD5)."""


class F64(float):
    """A 64-bit float BYML value (type 0xD6)."""


def f32(x: float) -> float:
    """x rounded to a 32-bit float. ValueError when it is too big for one."""
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        raise ValueError(f"{x!r} is too big for a 32-bit float") from None


def _bits(x: float, fmt: str) -> bytes:
    try:
        return struct.pack(fmt, x)
    except OverflowError:
        return struct.pack("<d", x)


def same(a, b) -> bool:
    """Deep equality that tells the types apart (I32 1 != U32 1 != True; F32 compared as 32-bit floats)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return len(a) == len(b) and all(k in b and same(v, b[k]) for k, v in a.items())
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, F64):
        return _bits(a, "<d") == _bits(b, "<d")
    if isinstance(a, float):
        return _bits(a, "<f") == _bits(b, "<f")
    return a == b


def dict_merge(base: dict, diff: dict) -> dict:
    """BCML's util.dict_merge with overwrite_lists=True, as a new dict: maps merge key by key, anything else
    (lists too) is replaced by diff's. The inputs are never changed."""
    out = dict(base)
    for k, v in diff.items():
        old = out.get(k)
        out[k] = dict_merge(old, v) if isinstance(old, dict) and isinstance(v, dict) else v
    return out


def _bad_value(v, where: str = "", depth: int = 0) -> str | None:
    """Where in v a value is that BotW's BYML files never hold (64-bit numbers, binary data), or None."""
    if depth > MAX_DEPTH:
        return f"{where or 'the log'} (nests too deep)"
    if isinstance(v, dict):
        for k, x in v.items():
            bad = _bad_value(x, f"{where}/{k}" if where else str(k), depth + 1)
            if bad:
                return bad
    elif isinstance(v, list):
        for i, x in enumerate(v):
            bad = _bad_value(x, f"{where}[{i}]", depth + 1)
            if bad:
                return bad
    elif isinstance(v, (I64, U64, F64, bytes, bytearray)):
        kind = "binary data" if isinstance(v, (bytes, bytearray)) else "a 64-bit number"
        return f"{where or 'the log'} ({kind})"
    return None


# ---------------------------------------------------------------------------------------------------- BYML binary
# Header: "YB" (little-endian, Switch) or "BY", u16 version (BotW: 2), u32 key table, u32 string table (0 when there
# are none), u32 root. Containers: type byte + u24 count. Dict 0xC1: entries of u24 key index, u8 type, u32 value,
# sorted by key. Array 0xC0: count type bytes padded to 4, then count u32 values. Tables 0xC2: count + 1 u32 offsets
# from the table's start, then the strings (NUL-terminated), sorted by their bytes.

T_STR, T_BIN, T_ARRAY, T_DICT, T_TABLE = 0xA0, 0xA1, 0xC0, 0xC1, 0xC2
T_BOOL, T_INT, T_FLOAT, T_UINT, T_INT64, T_UINT64, T_DOUBLE, T_NULL = 0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xFF


def _enc(s: str) -> bytes:
    return s.encode("utf-8", "surrogateescape")


def byml_load(data: bytes):
    """A BYML document (Yaz0 or not) as typed Python values (see U32...). wc.FormatError when it is damaged."""
    if data[:4] == b"Yaz0":
        data = wc.yaz0_decompress(data)[0]
    data = bytes(data)
    n = len(data)
    if n < 16 or data[:2] not in (b"YB", b"BY"):
        raise wc.FormatError("not a BYML document")
    o = "<" if data[:2] == b"YB" else ">"
    version, keys_off, strs_off, root = struct.unpack_from(o + "HIII", data, 2)
    if not 1 <= version <= 7:
        raise wc.FormatError(f"BYML version {version} isn't known")

    def need(off: int, size: int, what: str) -> None:
        if off < 16 or off + size > n:
            raise wc.FormatError(f"BYML {what} at 0x{off:x} lies outside the file")

    def u24(off: int) -> int:
        return int.from_bytes(data[off:off + 3], "little" if o == "<" else "big")

    def table(off: int, what: str) -> list[str]:
        if not off:
            return []
        need(off, 4, what)
        if data[off] != T_TABLE:
            raise wc.FormatError(f"BYML {what} has type 0x{data[off]:02x}")
        cnt = u24(off + 1)
        need(off + 4, 4 * (cnt + 1), what)
        offs = struct.unpack_from(f"{o}{cnt + 1}I", data, off + 4)
        out = []
        for k in range(cnt):
            at = off + offs[k]
            end = data.find(b"\0", at) if at < n else -1
            if end < 0:
                raise wc.FormatError(f"BYML {what} entry {k} lies outside the file")
            out.append(data[at:end].decode("utf-8", "surrogateescape"))
        return out

    keys, strs = table(keys_off, "key table"), table(strs_off, "string table")
    budget = [16 * n + 1024]          # values read: a document that shares nodes can't make this run forever
    wide = {T_INT64: (I64, "q"), T_UINT64: (U64, "Q"), T_DOUBLE: (F64, "d")}

    def value(t: int, raw: int, pos: int, depth: int):
        if t == T_STR:
            if raw >= len(strs):
                raise wc.FormatError(f"BYML string {raw} isn't in its string table")
            return strs[raw]
        if t == T_ARRAY or t == T_DICT:
            return node(raw, t, depth + 1)
        if t == T_BOOL:
            return bool(raw)
        if t == T_INT:
            return raw - (1 << 32) if raw & 0x80000000 else raw
        if t == T_FLOAT:
            return struct.unpack_from(o + "f", data, pos)[0]
        if t == T_UINT:
            return U32(raw)
        if t == T_NULL:
            return None
        if t in wide:
            need(raw, 8, "64-bit value")
            cls, fmt = wide[t]
            return cls(struct.unpack_from(o + fmt, data, raw)[0])
        if t == T_BIN:
            need(raw, 4, "binary data")
            size = struct.unpack_from(o + "I", data, raw)[0]
            need(raw + 4, size, "binary data")
            return data[raw + 4:raw + 4 + size]
        raise wc.FormatError(f"unknown BYML value type 0x{t:02x}")

    def node(off: int, t: int, depth: int):
        if depth > MAX_DEPTH:
            raise wc.FormatError("BYML nests too deep")
        need(off, 4, "node")
        if data[off] != t:
            raise wc.FormatError(f"BYML node at 0x{off:x} has type 0x{data[off]:02x}, expected 0x{t:02x}")
        cnt = u24(off + 1)
        budget[0] -= cnt
        if budget[0] < 0:
            raise wc.FormatError("BYML holds more values than its size allows (shared nodes in a loop?)")
        if t == T_ARRAY:
            vals = off + 4 + (-cnt % 4) + cnt
            need(off + 4, vals - off - 4 + 4 * cnt, "array")
            raws = struct.unpack_from(f"{o}{cnt}I", data, vals)
            return [value(data[off + 4 + k], raws[k], vals + 4 * k, depth) for k in range(cnt)]
        need(off + 4, 8 * cnt, "dictionary")
        words = struct.unpack_from(f"{o}{2 * cnt}I", data, off + 4)
        out = {}
        for k in range(cnt):
            w = words[2 * k]
            ki, vt = (w & 0xFFFFFF, w >> 24) if o == "<" else (w >> 8, w & 0xFF)
            if ki >= len(keys):
                raise wc.FormatError(f"BYML key {ki} isn't in its key table")
            out[keys[ki]] = value(vt, words[2 * k + 1], off + 8 + 8 * k, depth)
        return out

    if not root:
        return None
    need(root, 4, "root")
    if data[root] not in (T_ARRAY, T_DICT):
        raise wc.FormatError("BYML root isn't an array or a dictionary")
    return node(root, data[root], 0)


def byml_dump(root, version: int = 2, share: bool = True) -> bytes:
    """A little-endian (Switch) BYML document: key and string tables sorted, containers after them, every node at a
    multiple of 4 (64-bit values at a multiple of 8). share: equal containers are written once, as in most of the
    game's files (its quest list shares none). ValueError/TypeError for what BYML can't hold."""
    if not isinstance(root, (dict, list)):
        raise TypeError("a BYML document's root is a dictionary or an array")
    keys: set[str] = set()
    strs: set[str] = set()
    stack = [(root, 0)]
    while stack:
        v, d = stack.pop()
        if d > MAX_DEPTH:
            raise ValueError("nests too deep for BYML")
        items = v.items() if isinstance(v, dict) else enumerate(v)
        if isinstance(v, dict):
            for k in v:
                if not isinstance(k, str):
                    raise TypeError(f"BYML keys are text, not {k!r}")
                keys.add(k)
        for _, x in items:
            if isinstance(x, str):
                strs.add(x)
            elif isinstance(x, (dict, list)):
                stack.append((x, d + 1))
    out = bytearray(16)

    def table(names: set[str]) -> tuple[int, dict[str, int]]:
        if not names:
            return 0, {}
        if len(names) >= 1 << 24:
            raise ValueError("too many strings for BYML")
        order = sorted(names, key=_enc)
        raw = [_enc(s) for s in order]
        if any(b"\0" in r for r in raw):
            raise ValueError("BYML text can't hold a NUL character")
        off = len(out)
        pos, offs = 4 + 4 * (len(raw) + 1), []
        for r in raw:
            offs.append(pos)
            pos += len(r) + 1
        offs.append(pos)
        out.extend(struct.pack("<I", T_TABLE | len(raw) << 8) + struct.pack(f"<{len(offs)}I", *offs))
        for r in raw:
            out.extend(r + b"\0")
        out.extend(bytes(-len(out) % 4))
        return off, {s: i for i, s in enumerate(order)}

    keys_off, kidx = table(keys)
    strs_off, sidx = table(strs)

    def inline(x) -> tuple[int, int | None]:
        if x is None:
            return T_NULL, 0
        if isinstance(x, bool):
            return T_BOOL, int(x)
        if isinstance(x, str):
            return T_STR, sidx[x]
        if isinstance(x, U32):
            if not 0 <= x < 1 << 32:
                raise ValueError(f"{int(x)} doesn't fit an unsigned 32-bit value")
            return T_UINT, int(x)
        if isinstance(x, I64):
            return T_INT64, None
        if isinstance(x, U64):
            return T_UINT64, None
        if isinstance(x, int):
            if not -(1 << 31) <= x < 1 << 31:
                raise ValueError(f"{x} doesn't fit a signed 32-bit value")
            return T_INT, x & 0xFFFFFFFF
        if isinstance(x, F64):
            return T_DOUBLE, None
        if isinstance(x, float):
            try:
                return T_FLOAT, struct.unpack("<I", struct.pack("<f", x))[0]
            except OverflowError:
                raise ValueError(f"{x!r} is too big for a 32-bit float") from None
        if isinstance(x, (bytes, bytearray)):
            return T_BIN, None
        if isinstance(x, dict):
            return T_DICT, None
        if isinstance(x, list):
            return T_ARRAY, None
        raise TypeError(f"BYML can't hold {type(x).__name__} values")

    def child(t: int, x) -> int:
        if t in (T_DICT, T_ARRAY):
            return write(x)
        if t == T_BIN:
            at = len(out)
            out.extend(struct.pack("<I", len(x)) + bytes(x) + bytes(-len(x) % 4))
            return at
        out.extend(bytes(-len(out) % 8))
        at = len(out)
        if t == T_INT64:
            if not -(1 << 63) <= x < 1 << 63:
                raise ValueError(f"{int(x)} doesn't fit a signed 64-bit value")
            out.extend(struct.pack("<q", x))
        elif t == T_UINT64:
            if not 0 <= x < 1 << 64:
                raise ValueError(f"{int(x)} doesn't fit an unsigned 64-bit value")
            out.extend(struct.pack("<Q", x))
        else:
            out.extend(struct.pack("<d", x))
        return at

    # Identical containers are written once and shared, as in the game's own files (EventInfo is 2.4 times bigger
    # without it): each container gets the number of its canonical form (built from its children's numbers).
    forms: dict[tuple, int] = {}
    form_of: dict[int, int] = {}
    written: dict[int, int] = {}

    def form(v) -> int:
        f = form_of.get(id(v))
        if f is None:
            if isinstance(v, dict):
                key = (T_DICT,) + tuple(sorted((kidx[k],) + part(x) for k, x in v.items()))
            else:
                key = (T_ARRAY,) + tuple(part(x) for x in v)
            f = form_of[id(v)] = forms.setdefault(key, len(forms))
        return f

    def part(x) -> tuple:
        t, raw = inline(x)
        if raw is not None:
            return t, raw
        if t in (T_DICT, T_ARRAY):
            return t, form(x)
        return t, repr(x)

    def write(v) -> int:
        f = form(v) if share else None
        if f in written:
            return written[f]
        off = len(out)
        if share:
            written[f] = off
        later = []
        if len(v) >= 1 << 24:
            raise ValueError("too many values in one BYML container")
        if isinstance(v, dict):
            items = sorted(v.items(), key=lambda kv: kidx[kv[0]])
            out.extend(struct.pack("<I", T_DICT | len(items) << 8) + bytes(8 * len(items)))
            for j, (k, x) in enumerate(items):
                t, raw = inline(x)
                at = off + 4 + 8 * j
                struct.pack_into("<II", out, at, kidx[k] | t << 24, raw or 0)
                if raw is None:
                    later.append((at + 4, t, x))
        else:
            cnt = len(v)
            vals = off + 4 + cnt + (-cnt % 4)
            out.extend(struct.pack("<I", T_ARRAY | cnt << 8) + bytes(vals - off - 4 + 4 * cnt))
            for j, x in enumerate(v):
                t, raw = inline(x)
                out[off + 4 + j] = t
                if raw is None:
                    later.append((vals + 4 * j, t, x))
                else:
                    struct.pack_into("<I", out, vals + 4 * j, raw)
        for at, t, x in later:
            struct.pack_into("<I", out, at, child(t, x))
        return off

    root_off = len(out)
    write(root)
    struct.pack_into("<2sHIII", out, 0, b"YB", version, keys_off, strs_off, root_off)
    return bytes(out)


# ---------------------------------------------------------------------------------------------------- BYML text
# What oead (BCML's Python mergers) and roead (its Rust parts) write: block maps and lists, flow collections ([..],
# {..}) that libyaml may wrap over several lines, quoted, folded and block strings, comments, "---". Typed scalars:
# true/false, null, an unquoted integer is I32 (0x.. hex, a leading 0 octal), an unquoted number with "." or an
# exponent (or .inf/.nan) is F32, quoted text is always text; tags !u (U32), !l (I64), !ul (U64), !f64 (F64),
# !!binary. Keys are always text. Anchors and aliases are refused: neither library writes them.

_TAGS = {"!u": "u32", "!l": "i64", "!ul": "u64", "!f64": "f64", "!!binary": "bin", "!binary": "bin",
         "!<tag:yaml.org,2002:binary>": "bin", "tag:yaml.org,2002:binary": "bin", "!!str": "str", "!!int": "i32",
         "!!float": "f32", "!!bool": "bool", "!!null": "null"}
_INT_RE = re.compile(r"[-+]?(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)")
_FLOAT_RE = re.compile(r"[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+|[0-9]+)(?:[eE][-+]?[0-9]+)?")
_TRUE, _FALSE, _NULL = {"true", "True", "TRUE"}, {"false", "False", "FALSE"}, {"null", "Null", "NULL", "~", ""}


def _int_text(t: str) -> int | None:
    """An integer as C's strtoull(base 0) reads it (0x.. hex, a leading 0 octal), or None."""
    if not _INT_RE.fullmatch(t):
        return None
    body = t.lstrip("+-")
    if body[:2] in ("0x", "0X"):
        v = int(body[2:], 16)
    else:
        v = int(body, 8) if body[0] == "0" and len(body) > 1 else int(body)
    return -v if t[0] == "-" else v


def _float_text(t: str) -> float | None:
    body = t.lstrip("+-")
    if body in (".inf", ".Inf", ".INF"):
        return -math.inf if t[0] == "-" else math.inf
    if t in (".nan", ".NaN", ".NAN"):
        return math.nan
    if _FLOAT_RE.fullmatch(t) and any(c in t for c in ".eE"):
        return float(t)
    return None


def _typed(text: str, tag: str | None, quoted: bool, line: int):
    """A scalar's value from its text, tag and whether it was quoted."""
    def bad(what: str) -> LogError:
        return LogError(f"line {line}: {text!r} isn't {what}")

    kind = None
    if tag is not None:
        kind = _TAGS.get(tag)
        if kind is None:
            raise LogError(f"line {line}: the tag {tag} isn't one BCML writes for BotW")
    if kind is None and quoted or kind == "str":
        return text
    t = text.strip()
    if kind is None:
        if t in _TRUE or t in _FALSE:
            return t in _TRUE
        if t in _NULL:
            return None
        v = _int_text(t)
        if v is not None:
            if t.lstrip("+-")[:1] == "0" and len(t.lstrip("+-")) > 1 and 0 <= v < 1 << 32:
                v = v - (1 << 32) if v >= 1 << 31 else v      # (hex/octal: strtoull, then the signed type)
            if not -(1 << 31) <= v < 1 << 31:
                raise bad("a 32-bit integer (a bigger one needs a tag such as !u)")
            return v
        x = _float_text(t)
        if x is not None:
            try:
                return f32(x)
            except ValueError:
                raise bad("a 32-bit float") from None
        return text
    if kind in ("u32", "i64", "u64", "i32"):
        v = _int_text(t)
        lo, hi, cls = {"u32": (0, 1 << 32, U32), "i64": (-(1 << 63), 1 << 63, I64), "u64": (0, 1 << 64, U64),
                       "i32": (-(1 << 31), 1 << 31, int)}[kind]
        if v is None or not lo <= v < hi:
            raise bad(f"a {tag} value")
        return cls(v)
    if kind in ("f32", "f64"):
        x = _float_text(t)
        if x is None:
            v = _int_text(t)
            x = float(v) if v is not None else None
        if x is None:
            raise bad(f"a {tag} value")
        if kind == "f64":
            return F64(x)
        try:
            return f32(x)
        except ValueError:
            raise bad("a 32-bit float") from None
    if kind == "bool":
        if t not in _TRUE and t not in _FALSE:
            raise bad("true or false")
        return t in _TRUE
    if kind == "null":
        return None
    try:                                                   # bin
        return base64.b64decode("".join(t.split()), validate=True)
    except ValueError:
        raise bad("base64 data") from None


def _join_quoted(parts: list[str], q: str) -> str:
    """A quoted scalar's lines (inside the quotes) as one text, folded as YAML does."""
    if len(parts) > 1:
        parts = [parts[0].rstrip()] + [p.strip() for p in parts[1:-1]] + [parts[-1].lstrip()]
    if q == "'":
        return ukmm._fold([p.replace("''", "'") for p in parts])
    text = ""
    for k, p in enumerate(parts):          # (an escaped line break joins the lines without a space)
        if k:
            if text.endswith("\\") and not text.endswith("\\\\"):
                text = text[:-1]
            elif p:
                text += " "
            else:
                text += "\n"
        text += p
    return ukmm._unquote_double(text)


class _Flow:
    """A flow collection starting at `first` (the rest of a line); more lines are taken from ls as it needs them."""

    def __init__(self, first: str, ls, line: int):
        self.s, self.i, self.ls, self.line = first, 0, ls, line

    def err(self, msg: str) -> LogError:
        return LogError(f"line {self.line}: {msg}")

    def more(self) -> bool:
        if self.ls.i >= len(self.ls.lines):
            return False
        self.s += "\n" + self.ls.lines[self.ls.i].strip()
        self.ls.i += 1
        if len(self.s) > MAX_FLOW:
            raise self.err("a [..] or {..} value runs on for too long")
        return True

    def peek(self) -> str:
        while True:
            while self.i < len(self.s) and self.s[self.i] in " \t\n":
                self.i += 1
            if self.i < len(self.s):
                return self.s[self.i]
            if not self.more():
                return ""

    def scan(self, stop) -> str:
        """Plain text from here to the first character `stop` accepts (taking more lines as needed)."""
        j = self.i
        while True:
            while j < len(self.s) and not stop(j):
                j += 1
            if j < len(self.s) or not self.more():
                break
        text, self.i = self.s[self.i:j], j
        return ukmm._fold([p.strip() for p in text.split("\n")]).strip()

    def quoted(self) -> str:
        q = self.s[self.i]
        while True:
            end = ukmm._quote_end(self.s[self.i + 1:], q)
            if end >= 0:
                break
            if not self.more():
                raise self.err("quoted text without its closing quote")
        body = self.s[self.i + 1:self.i + 1 + end]
        self.i += end + 2
        return _join_quoted(body.split("\n"), q)

    def key(self) -> str:
        if self.peek() in ("'", '"'):
            return self.quoted()

        def stop(j: int) -> bool:
            s = self.s
            return s[j] in ",}" or (s[j] == ":" and (j + 1 >= len(s) or s[j + 1] in " \n,]}"))
        return self.scan(stop)

    def node(self, depth: int):
        if depth > MAX_DEPTH:
            raise self.err("nests too deep")
        c = self.peek()
        tag = None
        if c == "!":
            j = self.i
            while j < len(self.s) and self.s[j] not in " \n,]}":
                j += 1
            tag, self.i = self.s[self.i:j], j
            c = self.peek()
        if c in ("&", "*"):
            raise self.err("anchors and aliases (& and *) aren't something BCML writes")
        if c == "":
            raise self.err("a [..] or {..} value without its closing bracket")
        if c in "[{":
            if tag is not None:
                raise self.err(f"the tag {tag} on a collection isn't something BCML writes")
            close = "]" if c == "[" else "}"
            self.i += 1
            out: list | dict = [] if c == "[" else {}
            while True:
                if self.peek() == close:
                    self.i += 1
                    return out
                if c == "[":
                    out.append(self.node(depth + 1))
                else:
                    k = self.key()
                    if self.peek() != ":":
                        raise self.err(f"expected ':' after {k!r} in a {{..}} value")
                    if k in out:
                        raise self.err(f"the key {k!r} is there twice")
                    self.i += 1
                    out[k] = None if self.peek() in (",", "}") else self.node(depth + 1)
                nxt = self.peek()
                if nxt == ",":
                    self.i += 1
                elif nxt != close:
                    raise self.err(f"expected ',' or '{close}' in a [..] or {{..}} value")
        if c in ("'", '"'):
            return _typed(self.quoted(), tag, True, self.line)
        return _typed(self.scan(lambda j: self.s[j] in ",]}"), tag, False, self.line)


def _is_item(s: str) -> bool:
    return s == "-" or s.startswith("- ")


def _map_line(s: str) -> tuple[str, str] | None:
    if s.startswith(("!", "&", "*", "[", "{", "|", ">", "? ")) or s == "?":
        return None
    return ukmm._key(s)


def _value(rest: str, ls, parent: int, depth: int, in_map: bool):
    """The value whose text starts with `rest` (after "key: " or "- "; maybe empty: then the block below)."""
    line = ls.i
    if depth > MAX_DEPTH:
        raise LogError(f"line {line}: nests too deep")
    tag = None
    if rest.startswith("!"):
        tag, _, rest = rest.partition(" ")
        rest = rest.strip()
    if rest[:1] in ("&", "*"):
        raise LogError(f"line {line}: anchors and aliases (& and *) aren't something BCML writes")
    if rest.startswith("#"):
        rest = ""
    if not rest:
        nxt = ls.peek()
        if nxt is not None and (nxt[0] > parent or (in_map and nxt[0] == parent and _is_item(nxt[1]))):
            if tag is not None:
                raise LogError(f"line {line}: the tag {tag} on a collection isn't something BCML writes")
            return _block(ls, nxt[0], depth + 1)
        return _typed("", tag, False, line) if tag else None
    if rest[0] in "[{":
        f = _Flow(rest, ls, line)
        v = f.node(depth)
        tail = f.s[f.i:].strip()
        if tail and not tail.startswith("#"):
            raise LogError(f"line {ls.i}: {tail[:30]!r} after a [..] or {{..}} value")
        if tag is not None:
            raise LogError(f"line {line}: the tag {tag} on a collection isn't something BCML writes")
        return v
    try:
        text = ukmm._scalar_text(rest, ls, parent)
    except ukmm.UkmmError as e:
        raise LogError(f"line {line}: {e}") from None
    return _typed(text, tag, rest[0] in "'\"|>", line)


def _block(ls, indent: int, depth: int):
    if depth > MAX_DEPTH:
        raise LogError(f"line {ls.i + 1}: nests too deep")
    nxt = ls.peek()
    if nxt is None:
        return None
    if _is_item(nxt[1]):
        out: list = []
        while True:
            nxt = ls.peek()
            if nxt is None or nxt[0] != indent or not _is_item(nxt[1]):
                if nxt is not None and nxt[0] > indent:
                    raise LogError(f"line {ls.i + 1} is indented more than it should be")
                return out
            s = nxt[1]
            ls.i += 1
            rest = s[1:].strip()
            if rest and (_is_item(rest) or _map_line(rest) is not None):
                # a map or list that starts on the "- " line: read the line again as its first line
                inner = indent + len(s) - len(s[1:].lstrip(" "))
                ls.i -= 1
                ls.lines[ls.i] = " " * inner + rest
                out.append(_block(ls, inner, depth + 1))
            else:
                out.append(_value(rest, ls, indent, depth + 1, False))
    out_map: dict = {}
    while True:
        nxt = ls.peek()
        if nxt is None or nxt[0] != indent:
            if nxt is not None and nxt[0] > indent:
                raise LogError(f"line {ls.i + 1} is indented more than it should be")
            return out_map
        if nxt[1].startswith("? ") or nxt[1] == "?":
            raise LogError(f"line {ls.i + 1}: complex keys ('? ') aren't something BCML writes")
        kv = _map_line(nxt[1])
        if kv is None:
            raise LogError(f"line {ls.i + 1} isn't a 'key: value' line")
        if kv[0] in out_map:
            raise LogError(f"line {ls.i + 1}: the key {kv[0]!r} is there twice")
        ls.i += 1
        out_map[kv[0]] = _value(kv[1], ls, indent, depth + 1, True)


def from_text(text: str):
    """BYML text (a BCML log) as typed values (see U32...). LogError, with the line, when it can't be read."""
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    seen = False
    for k, ln in enumerate(lines):
        if ln.startswith("%") and not seen:
            lines[k] = ""                       # (a %YAML directive)
        elif ln.startswith("--- "):
            lines[k] = ln[4:]
        elif ln.rstrip() in ("---", "...") and seen and any(x.strip() and not x.lstrip().startswith("#")
                                                            for x in lines[k + 1:]):
            raise LogError(f"line {k + 1}: a second document in one log")
        if ln.strip() and not ln.lstrip().startswith("#") and ln.rstrip() not in ("---", "..."):
            seen = True
    ls = ukmm._Lines("")
    ls.lines = lines
    nxt = ls.peek()
    if nxt is None:
        return None
    if nxt[1].startswith("? ") or nxt[1] == "?":
        raise LogError(f"line {ls.i + 1}: complex keys ('? ') aren't something BCML writes")
    try:
        if _is_item(nxt[1]) or _map_line(nxt[1]) is not None:
            v = _block(ls, nxt[0], 0)
        else:
            ls.i += 1
            v = _value(nxt[1], ls, -1, 0, False)
    except RecursionError:
        raise LogError("it nests too deep") from None
    left = ls.peek()
    if left is not None:
        raise LogError(f"line {ls.i + 1} doesn't fit where it is (check its indentation)")
    return v


_PLAIN_BAD = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029\ufeff]")


def _reads_as_text(s: str) -> bool:
    try:
        return _typed(s, None, False, 0) == s
    except LogError:
        return False


def _text_scalar(s: str) -> str:
    """s as a YAML scalar that reads back as this same text."""
    plain = (s and s == s.strip() and s[0] not in "-?:,[]{}#&*!|>'\"%@`" and not _PLAIN_BAD.search(s)
             and ": " not in s and " #" not in s and not s.endswith(":") and not any(c in s for c in ",[]{}")
             and _reads_as_text(s))
    return s if plain else json.dumps(s, ensure_ascii=False)


def _scalar_out(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return _text_scalar(v)
    if isinstance(v, U32):
        return f"!u 0x{int(v):08x}"
    if isinstance(v, I64):
        return f"!l {int(v)}"
    if isinstance(v, U64):
        return f"!ul 0x{int(v):016x}"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (float, F64)):
        if math.isnan(v):
            text = ".nan"
        elif math.isinf(v):
            text = ".inf" if v > 0 else "-.inf"
        else:
            text = repr(float(v)) if isinstance(v, F64) else "%.9g" % v
            if not any(c in text for c in ".en"):
                text += ".0"
        return f"!f64 {text}" if isinstance(v, F64) else text
    if isinstance(v, (bytes, bytearray)):
        return "!!binary " + base64.b64encode(bytes(v)).decode()
    raise TypeError(f"no BYML text for {type(v).__name__} values")


def to_text(value, width: int = 0) -> str:
    """value as BYML text in oead's style (block maps and lists; a container of at most 10 plain values in flow
    style). width > 0 wraps long flow lines after a comma, as libyaml does. For tests and for looking at logs."""
    def inline(v) -> str | None:
        if isinstance(v, (dict, list)):
            if not v:
                return "{}" if isinstance(v, dict) else "[]"
            if len(v) > 10 or any(isinstance(x, (dict, list)) for x in (v.values() if isinstance(v, dict) else v)):
                return None
            if isinstance(v, dict):
                return "{" + ", ".join(f"{_text_scalar(k)}: {_scalar_out(x)}" for k, x in v.items()) + "}"
            return "[" + ", ".join(_scalar_out(x) for x in v) + "]"
        return _scalar_out(v)

    def wrap(line: str, ind: int) -> list[str]:
        if not width or len(line) <= width:
            return [line]
        out, cur = [], ""
        for piece in re.split(r"(?<=,) ", line):
            if cur and len(cur) + 1 + len(piece) > width:
                out.append(cur)
                cur = " " * (ind + 4) + piece
            else:
                cur = f"{cur} {piece}" if cur else piece
        return out + [cur]

    def block(v, ind: int, lines: list[str]) -> None:
        pad = " " * ind
        if isinstance(v, dict):
            for k, x in v.items():
                s = inline(x)
                if s is not None:
                    lines += wrap(f"{pad}{_text_scalar(k)}: {s}", ind)
                else:
                    lines.append(f"{pad}{_text_scalar(k)}:")
                    block(x, ind if isinstance(x, list) else ind + 2, lines)
            return
        for x in v:
            s = inline(x)
            if s is not None:
                lines += wrap(f"{pad}- {s}", ind)
            else:
                sub: list[str] = []
                block(x, ind + 2, sub)
                sub[0] = f"{pad}- {sub[0][ind + 2:]}"
                lines += sub

    s = inline(value)
    if s is not None:
        return s + "\n"
    lines: list[str] = []
    block(value, 0, lines)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------- logs
# Each kind's log as read: checked and put in one shape (so a damaged log is refused before anything is merged).

def _need_map(v, what: str) -> dict:
    if not isinstance(v, dict):
        raise LogError(f"{what} isn't a map")
    return v


def _hash_key(k) -> int | None:
    """An actor hash written as text (decimal, as BCML does) or a number, as an unsigned 32-bit number."""
    if isinstance(k, bool):
        return None
    if isinstance(k, str):
        k = k.strip()
        if not re.fullmatch(r"[-+]?[0-9]+", k):
            return None
        k = int(k)
    if not isinstance(k, int) or not -(1 << 31) <= k < 1 << 32:
        return None
    return k & 0xFFFFFFFF


def _signed(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= 1 << 31 else v


def _check_actorinfo(d) -> dict:
    out = {}
    for k, e in _need_map(d, "the log's top level").items():
        h = _hash_key(k)
        if h is None:
            raise LogError(f"{k!r} isn't an actor hash")
        out[h] = _need_map(e, f"actor {k}'s entry")
    return out


def _check_gamedata(d) -> dict:
    out = {}
    for t, part in _need_map(d, "the log's top level").items():
        part = _need_map(part, f"{t}")
        extra = [k for k in part if k not in ("add", "del")]
        if extra:
            raise LogError(f"{t} has {extra[0]!r}, not just add and del")
        add = part.get("add") or {}
        if isinstance(add, list):          # (a list of whole entries: keyed by their names here)
            add = {e.get("DataName") if isinstance(e, dict) else None: e for e in add}
        for name, e in _need_map(add, f"{t}/add").items():
            if not isinstance(name, str) or not isinstance(e, dict) or e.get("DataName") != name:
                raise LogError(f"{t}/add: the entry {name!r} isn't a flag named {name!r}")
        dels = part.get("del") or []
        if not isinstance(dels, list):
            raise LogError(f"{t}/del isn't a list")
        dels = [x.get("DataName") if isinstance(x, dict) else x for x in dels]
        if not all(isinstance(x, str) for x in dels):
            raise LogError(f"{t}/del holds something that isn't a flag name")
        out[t] = {"add": dict(add), "del": dels}
    return out


def _check_savedata(d) -> dict:
    d = _need_map(d, "the log's top level")
    extra = [k for k in d if k not in ("add", "del")]
    if extra:
        raise LogError(f"it has {extra[0]!r}, not just add and del")
    add = d.get("add") or []
    if isinstance(add, dict):
        add = list(add.values())
    if not isinstance(add, list):
        raise LogError("add isn't a list")
    def hash_value(h) -> bool:
        return isinstance(h, int) and not isinstance(h, bool) and -(1 << 31) <= h < 1 << 32

    entries = []
    for e in add:
        if not (isinstance(e, dict) and isinstance(e.get("DataName"), str) and hash_value(e.get("HashValue"))):
            raise LogError(f"add holds {str(e)[:60]!r}, not an entry with DataName and HashValue")
        entries.append({**e, "HashValue": _signed(e["HashValue"])})
    dels = d.get("del") or []
    if not isinstance(dels, list):
        raise LogError("del isn't a list")
    hashes = []
    for h in dels:
        if isinstance(h, dict):
            h = h.get("HashValue")
        if not hash_value(h):
            raise LogError(f"del holds {str(h)[:40]!r}, not a HashValue")
        hashes.append(_signed(h))
    return {"add": entries, "del": hashes}


def _check_named(d, what: str, values=(dict,)) -> dict:
    out = {}
    for k, e in _need_map(d, "the log's top level").items():
        if not isinstance(e, values):
            raise LogError(f"{what} {k!r} isn't a {'map' if values == (dict,) else 'list or map'}")
        out[str(k)] = e
    return out


def _check_areadata(d) -> dict:
    out = {}
    for k, e in _need_map(d, "the log's top level").items():
        n = int(k) if isinstance(k, str) and re.fullmatch(r"\s*-?[0-9]+\s*", k) else \
            k if isinstance(k, int) and not isinstance(k, bool) else None
        if n is None:
            raise LogError(f"{k!r} isn't an area number")
        out[str(n)] = _need_map(e, f"area {k}")
    return out


_CHECKS = {
    "actorinfo.yml": _check_actorinfo,
    "gamedata.yml": _check_gamedata,
    "savedata.yml": _check_savedata,
    "eventinfo.yml": lambda d: _check_named(d, "the event"),
    "effects.yml": lambda d: _check_named(d, "the effect", (list, dict)),
    "areadata.yml": _check_areadata,
    "residents.yml": lambda d: _check_named(d, "the resident"),
}


def check_log(name: str, diff):
    """A parsed log, checked and normalized (actor hashes as numbers, save hashes signed...). LogError if bad."""
    name = name.lower()
    if name not in _CHECKS:
        raise LogError(f"{name} isn't a log this merges")
    if diff is None:
        diff = {}
    out = _CHECKS[name](diff)
    bad = _bad_value(out)
    if bad:
        raise LogError(f"{bad}: BotW's files never hold that, so it isn't merged")
    return out


def read_log(name: str, data: bytes):
    """One log file's diff (checked). LogError, with a message for the player, when it can't be read."""
    name = name.lower()
    if len(data) > MAX_LOG:
        raise LogError(f"{name} can't be read: it is bigger than any BCML log ({len(data) >> 20} MB)")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise LogError(f"{name} can't be read: it isn't UTF-8 text ({e.reason} at byte {e.start})") from None
    try:
        return check_log(name, from_text(text))
    except LogError as e:
        raise LogError(f"{name} can't be read: {e}") from None
    except (ValueError, TypeError, IndexError, RecursionError) as e:
        raise LogError(f"{name} can't be read: {e}") from None


def _packages(logs) -> list[list]:
    """logs: one item per package, each a list of its diffs (base, then options); a bare diff is a package alone."""
    return [list(p) if isinstance(p, (list, tuple)) else [p] for p in logs]


def _dict_merged(diffs: list) -> dict:
    return reduce(dict_merge, diffs, {})


def combine(name: str, logs) -> object:
    """The checked diffs of one kind as one, BCML's way: in order, an item per package (a list of its diffs: the
    base, then each option in info.json's order), packages from lower to higher priority."""
    name = name.lower()
    packages = [[check_log(name, d) for d in p] for p in _packages(logs)]
    flat = [d for p in packages for d in p]
    if name == "actorinfo.yml":
        out: dict = {}
        for pdiff in (_dict_merged(p) for p in packages):
            for h, e in pdiff.items():
                if h not in out:
                    out[h] = e
                    continue
                merged = dict_merge(out[h], e)
                a, b = out[h].get("instSize"), e.get("instSize")
                if all(isinstance(x, int) and not isinstance(x, bool) for x in (a, b)) and a > b:
                    merged["instSize"] = a          # (across mods the largest instance size wins)
                out[h] = merged
        return out
    if name == "eventinfo.yml":
        out = {}
        for p in packages:
            out.update(_dict_merged(p))             # (across mods a later event entry replaces the whole entry)
        return out
    if name == "gamedata.yml":
        types: dict = {}
        for d in flat:
            for t, part in d.items():
                cur = types.setdefault(t, {"add": {}, "del": {}})
                for n, e in part["add"].items():
                    cur["add"][n] = dict_merge(cur["add"][n], e) if n in cur["add"] else e
                    cur["del"].pop(n, None)          # (a later add of a flag undoes an earlier delete)
                for n in part["del"]:
                    cur["del"][n] = True             # (deletes are joined, where BCML keeps only the last list)
                    cur["add"].pop(n, None)
        return {t: {"add": v["add"], "del": list(v["del"])} for t, v in types.items()}
    if name == "savedata.yml":
        adds: dict = {}
        dels: dict = {}
        for d in reversed(flat):                    # (BCML's order: the latest add of a HashValue wins)
            for e in d["add"]:
                adds.setdefault(e["HashValue"], e)
            for h in d["del"]:
                dels[h] = True
        return {"add": list(adds.values()), "del": list(dels)}
    return _dict_merged(flat)


# ---------------------------------------------------------------------------------------------------- merging

def _open(stored: bytes) -> tuple[bytes, int, bool]:
    if stored[:4] == b"Yaz0":
        raw, align = wc.yaz0_decompress(stored)
        return raw, align, True
    return stored, 0, False


def _close(raw: bytes, align: int, yaz0: bool) -> bytes:
    """Stored as the game's was: Yaz0 (stored as is when big: pure-Python compression is slow) or not."""
    return ukmm.yaz0(raw, align) if yaz0 else raw


def _some(items: list, n: int = 3) -> str:
    items = [str(x) for x in items]
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def _actorinfo(stored: bytes, diff: dict) -> tuple[bytes | None, list[str]]:
    raw, align, yaz0 = _open(stored)
    root = byml_load(raw)
    if not isinstance(root, dict) or not isinstance(root.get("Actors"), list):
        raise wc.FormatError("ActorInfo has no Actors list")
    actors, hashes = root["Actors"], root.get("Hashes")
    table: dict[int, object] = {}
    if isinstance(hashes, list) and len(hashes) == len(actors) and all(isinstance(h, int) for h in hashes):
        for h, a in zip(hashes, actors):
            table[h & 0xFFFFFFFF] = a
    else:
        for a in actors:
            name = a.get("name") if isinstance(a, dict) else None
            if isinstance(name, str):
                table[zlib.crc32(_enc(name))] = a
    wrong, changed = [], False
    for h, e in diff.items():
        cur = table.get(h)
        new = dict_merge(cur, e) if isinstance(cur, dict) else e
        name = new.get("name")
        if not isinstance(name, str) or zlib.crc32(_enc(name)) != h:
            wrong.append(f"{h} ({name})" if isinstance(name, str) else str(h))
            continue
        if cur is None or not same(cur, new):
            table[h] = new
            changed = True
    notes = [f"actorinfo.yml: {len(wrong)} actor(s) skipped, their name doesn't match their hash: {_some(wrong)}"] \
        if wrong else []
    if not changed:
        return None, notes
    order = sorted(table)
    out = dict(root)
    out["Actors"] = [table[h] for h in order]
    out["Hashes"] = [h if h < 1 << 31 else U32(h) for h in order]   # (the game's: I32 below 2^31, else U32)
    return _close(byml_dump(out), align, yaz0), notes


_DATA_FILE = re.compile(r"^(?P<pre>.*?)(?P<base>[^/]+)_(?P<n>\d+)\.(?P<ext>bgdata)$")


def _root_key(data: bytes) -> str | None:
    """The single key of a BYML document's root dictionary (a game data file's type), reading only that."""
    try:
        if data[:2] != b"YB":
            return None
        keys_off, _, root = struct.unpack_from("<III", data, 4)
        if data[root] != T_DICT or int.from_bytes(data[root + 1:root + 4], "little") != 1 or data[keys_off] != T_TABLE:
            return None
        ki = int.from_bytes(data[root + 4:root + 7], "little")
        if ki >= int.from_bytes(data[keys_off + 1:keys_off + 4], "little"):
            return None
        at = keys_off + struct.unpack_from("<I", data, keys_off + 4 + 4 * ki)[0]
        return data[at:data.index(b"\0", at)].decode("utf-8", "surrogateescape")
    except (struct.error, IndexError, ValueError):
        return None


def _sarc_items(raw: bytes) -> tuple[list[tuple[str, bytes]], int]:
    s = wc.sarc_parse(raw)
    if s.order != wc.LE:
        raise wc.FormatError("a Wii U (big-endian) archive")
    if any(not n.attr >> 24 for n in s.nodes):
        raise wc.FormatError("an archive with unnamed files")
    return [(n.name, raw[s.data_offset + n.start:s.data_offset + n.end]) for n in s.nodes], s.hash_key


def _gamedata(stored: bytes, diff: dict) -> tuple[bytes | None, list[str]]:
    raw, align, yaz0 = _open(stored)
    files, key = _sarc_items(raw)
    roots = {name: _root_key(data) for name, data in files}
    data_of = dict(files)
    notes: list[str] = []
    changed: dict[str, tuple[str, list]] = {}       # file name -> (type, its entries)
    added: list[str] = []
    for t, part in diff.items():
        dels = set(part["del"])
        add = {n: e for n, e in part["add"].items() if n not in dels}   # (BCML applies deletes after adds)
        if not add and not dels:
            continue
        names = [n for n, _ in files if roots[n] == t]
        if not names:
            if add:
                notes.append(f"gamedata.yml: the game has no {t} flags, so {len(add)} of them are skipped: "
                             f"{_some(list(add))}")
            continue
        wrong = [n for n, e in add.items() if not isinstance(e.get("HashValue"), int)
                 or _signed(e["HashValue"]) != _signed(zlib.crc32(_enc(n)))]
        if wrong:
            notes.append(f"gamedata.yml: {len(wrong)} {t} flag(s) have a HashValue that isn't their name's "
                         f"(used as the mod has them): {_some(wrong)}")
        loaded = {}
        for n in names:
            doc = byml_load(data_of[n])
            if not isinstance(doc, dict) or not isinstance(doc.get(t), list):
                raise wc.FormatError(f"{n} isn't a game data file")
            loaded[n] = doc[t]
            out, dirty = [], False
            for e in doc[t]:
                dn = e.get("DataName") if isinstance(e, dict) else None
                if dn in dels:
                    dirty = True
                elif dn in add:
                    new = add.pop(dn)
                    dirty = dirty or not same(new, e)
                    out.append(new)
                else:
                    out.append(e)
            if dirty:
                changed[n] = (t, out)
        if not add:
            continue
        # new flags: after the last file of their type that isn't a revival one, then in new files after it
        spots = []
        for n in names:
            m = _DATA_FILE.match(n)
            if m and not m["base"].startswith("revival_"):
                spots.append((int(m["n"]), n, m))
        if spots:
            num, target, m = max(spots)
            pre, base = m["pre"], m["base"]
        else:
            num, target, pre, base = -1, None, names[0][:1] if names[0].startswith("/") else "", t
        cur = list(changed[target][1]) if target in changed else list(loaded[target]) if target else []
        for e in add.values():
            if target is None or len(cur) >= GAMEDATA_CHUNK:
                if target is not None:
                    changed[target] = (t, cur)
                num += 1
                target = f"{pre}{base}_{num}.bgdata"
                while target in data_of:
                    num += 1
                    target = f"{pre}{base}_{num}.bgdata"
                added.append(target)
                cur = list(changed[target][1]) if target in changed else []
            cur.append(e)
        changed[target] = (t, cur)
    if not changed:
        return None, notes
    items = []
    for name, data in files:
        if name in changed:
            t, entries = changed.pop(name)
            data = byml_dump({t: entries})
        items.append((name, data, 4))
    for name, (t, entries) in changed.items():
        items.append((name, byml_dump({t: entries}), 4))
    return _close(bnp.sarc_build(items, key), align, yaz0), notes


_SAVE_FILE = re.compile(r"^(?P<pre>.*?saveformat_)(?P<n>\d+)(?P<ext>\.bgsvdata)$")


def _savedata(stored: bytes, diff: dict) -> tuple[bytes | None, list[str]]:
    raw, align, yaz0 = _open(stored)
    files, key = _sarc_items(raw)
    game_data, others = [], []
    pre = ext = None
    for name, data in files:
        m = _SAVE_FILE.match(name)
        if not m:
            raise wc.FormatError(f"{name} isn't a save data format file")
        pre, ext = m["pre"], m["ext"]
        doc = byml_load(data)
        fl = doc.get("file_list") if isinstance(doc, dict) else None
        if not (isinstance(fl, list) and len(fl) == 2 and isinstance(fl[0], dict) and isinstance(fl[1], list)):
            raise wc.FormatError(f"{name} isn't a save data format file")
        if fl[0].get("file_name") == "game_data.sav":
            game_data.append((int(m["n"]), doc))
        else:
            others.append((int(m["n"]), data))
    if not game_data:
        raise wc.FormatError("no game_data.sav format files")
    game_data.sort(key=lambda x: x[0])
    others.sort(key=lambda x: x[0])
    first = game_data[0][1]
    entries: dict[int, dict] = {}
    for _, doc in game_data:
        for e in doc["file_list"][1]:
            h = e.get("HashValue") if isinstance(e, dict) else None
            if not isinstance(h, int) or isinstance(h, bool):
                raise wc.FormatError("a save data entry without a HashValue")
            entries[_signed(h)] = e
    before = dict(entries)
    for e in diff["add"]:
        entries[e["HashValue"]] = e
    for h in diff["del"]:
        entries.pop(h, None)
    wrong = [e["DataName"] for e in diff["add"] if _signed(zlib.crc32(_enc(e["DataName"]))) != e["HashValue"]]
    notes = [f"savedata.yml: {len(wrong)} entr{'y' if len(wrong) == 1 else 'ies'} with a HashValue that isn't "
             f"their name's (used as the mod has them): {_some(wrong)}"] if wrong else []
    if entries.keys() == before.keys() and all(same(entries[h], before[h]) for h in entries):
        return None, notes
    ordered = [entries[h] for h in sorted(entries)]   # (the game's are sorted by signed HashValue across files)
    items = []
    for i in range(0, max(len(ordered), 1), SAVEDATA_CHUNK):
        doc = {"file_list": [first["file_list"][0], ordered[i:i + SAVEDATA_CHUNK]], "save_info": first["save_info"]}
        items.append((f"{pre}{len(items)}{ext}", byml_dump(doc), 4))
    for _, data in others:                             # caption.sav and option.sav: after them, unchanged
        items.append((f"{pre}{len(items)}{ext}", data, 4))
    return _close(bnp.sarc_build(items, key), align, yaz0), notes


def _eventinfo(stored: bytes, diff: dict) -> tuple[bytes | None, list[str]]:
    raw, align, yaz0 = _open(stored)
    root = byml_load(raw)
    if not isinstance(root, dict):
        raise wc.FormatError("EventInfo isn't a dictionary")
    new = dict(root)
    for k, v in diff.items():
        new[k] = v
    if same(new, root):
        return None, []
    return _close(byml_dump(new), align, yaz0), []


def _effects(stored: bytes, diff: dict) -> tuple[bytes | None, list[str]]:
    raw, align, yaz0 = _open(stored)
    root = byml_load(raw)
    if not (isinstance(root, list) and root and isinstance(root[0], dict)):
        raise wc.FormatError("StatusEffectList isn't a list holding one dictionary")
    merged = dict_merge(root[0], diff)
    if same(merged, root[0]):
        return None, []
    return _close(byml_dump([merged] + root[1:]), align, yaz0), []


def _areadata(stored: bytes, diff: dict) -> tuple[bytes | None, list[str]]:
    raw, align, yaz0 = _open(stored)
    root = byml_load(raw)
    if not isinstance(root, list):
        raise wc.FormatError("AreaData isn't a list")
    table: dict[str, dict] = {}
    for a in root:
        n = a.get("AreaNumber") if isinstance(a, dict) else None
        if not isinstance(n, int) or isinstance(n, bool) or str(n) in table:
            raise wc.FormatError("AreaData has an area without a number of its own")
        table[str(n)] = a
    new_areas, changed = [], False
    for k, v in diff.items():
        if k not in table:
            new_areas.append(k)
            continue
        merged = dict_merge(table[k], v)
        if not same(merged, table[k]):
            table[k], changed = merged, True
    notes = [f"areadata.yml: {len(new_areas)} area(s) the game doesn't have are skipped (BCML doesn't add areas "
             f"either): {_some(new_areas)}"] if new_areas else []
    if not changed:
        return None, notes
    return _close(byml_dump([table[k] for k in sorted(table, key=int)]), align, yaz0), notes


def _residents(stored: bytes, diff: dict) -> tuple[bytes | None, list[str]]:
    raw, align, yaz0 = _open(stored)
    root = byml_load(raw)
    if not isinstance(root, list):
        raise wc.FormatError("ResidentActors isn't a list")
    table: dict[str, dict] = {}
    for a in root:
        n = a.get("name") if isinstance(a, dict) else None
        if not isinstance(n, str) or n in table:
            raise wc.FormatError("ResidentActors has an entry without a name of its own")
        table[n] = a
    for n, v in diff.items():
        table[n] = dict_merge(table[n], v) if n in table else v
    out = []
    for e in table.values():
        if e.get("remove"):
            continue
        out.append({k: x for k, x in e.items() if k != "remove"} if "remove" in e else e)
    if same(out, root):
        return None, []
    return _close(byml_dump(out), align, yaz0), []


# log name -> (the game file it changes: a RomFS path, "//" inside a pack; its merge; sized in the RSTB)
KINDS = {
    "actorinfo.yml": ("Actor/ActorInfo.product.sbyml", _actorinfo, False),
    "gamedata.yml": (f"{BOOTUP}//GameData/gamedata.ssarc", _gamedata, True),
    "savedata.yml": (f"{BOOTUP}//GameData/savedataformat.ssarc", _savedata, True),
    "eventinfo.yml": (f"{BOOTUP}//Event/EventInfo.product.sbyml", _eventinfo, True),
    "effects.yml": (f"{BOOTUP}//Ecosystem/StatusEffectList.sbyml", _effects, True),
    "areadata.yml": (f"{BOOTUP}//Ecosystem/AreaData.sbyml", _areadata, True),
    "residents.yml": (f"{BOOTUP}//Actor/ResidentActors.byml", _residents, True),
}
_GAME_ERRORS = (wc.FormatError, ValueError, TypeError, KeyError, IndexError, struct.error, RecursionError)


def merge(name: str, game: bytes, logs) -> tuple[bytes, list[str]]:
    """The game's file (`game`: its bytes as stored) with the logs of one kind merged in, and notes for what was
    left out. logs: in order, one item per package (a list of its parsed logs: the base, then its options), lower
    priority first; a bare log counts as a package alone. A log that can't be used is left out and named; when
    nothing changes, the game's own bytes come back. wc.FormatError when the game's file can't be read."""
    name = name.lower()
    notes: list[str] = []
    packages = []
    for p, pkg in enumerate(_packages(logs)):
        kept = []
        for k, d in enumerate(pkg):
            try:
                kept.append(check_log(name, d))
            except LogError as e:
                where = f"package {p + 1}" + (f", option {k}" if k else "")
                notes.append(f"{name} ({where}) not merged: {e}")
        packages.append(kept)
    diff = combine(name, packages)
    try:
        new, more = KINDS[name][1](game, diff)
    except _GAME_ERRORS as e:
        raise wc.FormatError(f"{name}: the game's file can't be merged ({e})") from None
    return (game if new is None else new), notes + more


def merge_actorinfo(game: bytes, logs) -> tuple[bytes, list[str]]:
    """Actor/ActorInfo.product.sbyml with actorinfo.yml logs merged (see merge)."""
    return merge("actorinfo.yml", game, logs)


def merge_gamedata(game: bytes, logs) -> tuple[bytes, list[str]]:
    """Bootup.pack's GameData/gamedata.ssarc with gamedata.yml logs merged (see merge)."""
    return merge("gamedata.yml", game, logs)


def merge_savedata(game: bytes, logs) -> tuple[bytes, list[str]]:
    """Bootup.pack's GameData/savedataformat.ssarc with savedata.yml logs merged (see merge)."""
    return merge("savedata.yml", game, logs)


def merge_eventinfo(game: bytes, logs) -> tuple[bytes, list[str]]:
    """Bootup.pack's Event/EventInfo.product.sbyml with eventinfo.yml logs merged (see merge)."""
    return merge("eventinfo.yml", game, logs)


def merge_effects(game: bytes, logs) -> tuple[bytes, list[str]]:
    """Bootup.pack's Ecosystem/StatusEffectList.sbyml with effects.yml logs merged (see merge)."""
    return merge("effects.yml", game, logs)


def merge_areadata(game: bytes, logs) -> tuple[bytes, list[str]]:
    """Bootup.pack's Ecosystem/AreaData.sbyml with areadata.yml logs merged (see merge)."""
    return merge("areadata.yml", game, logs)


def merge_residents(game: bytes, logs) -> tuple[bytes, list[str]]:
    """Bootup.pack's Actor/ResidentActors.byml with residents.yml logs merged (see merge)."""
    return merge("residents.yml", game, logs)


# ---------------------------------------------------------------------------------------------------- the game

_CACHE: dict[str, tuple[tuple, object]] = {}


def _cached(path: str, make):
    """make(bytes) for a game file, kept while the file stays the same (Bootup.pack is 32 MB, read once)."""
    st = os.stat(path)
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _CACHE.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    with open(path, "rb") as f:
        v = make(f.read())
    _CACHE[path] = (stamp, v)
    return v


def game_file(game: str, target: str) -> bytes | None:
    """A game file by its RomFS path ("//" inside a pack) from Savage Wind's extracted folder, or None."""
    parts = target.split("//")
    path = os.path.join(game, "romfs", *parts[0].split("/"))
    if not os.path.isfile(path):
        return None
    if len(parts) == 1:
        with open(path, "rb") as f:
            return f.read()
    blob = _cached(path, bnp.Blob)
    for name in parts[1:]:
        blob = blob.child(name) if blob is not None and blob.is_sarc() else None
    return blob.orig if blob is not None else None


def apply(name: str, diff, game: str, read=None) -> dict:
    """A combined diff (see combine) applied to the game's file from `game` (Savage Wind's extracted folder, only
    read): {"files": {target: bytes as stored}, "sizes": {size table name: size, only above the game's entry},
    "notes": [what was left out]}. No file when nothing changes. read: an optional function target -> bytes or None
    giving the copy to merge into instead of the game's (the mod's own Bootup.pack file, say); sizes still count
    from the game's copy."""
    name = name.lower()
    target, fn, sized = KINDS[name]
    what = f"{name} ({LOGS[name]})"
    done: dict = {"files": {}, "sizes": {}, "notes": []}
    try:
        data = game_file(game, target)
    except (OSError, *_GAME_ERRORS) as e:
        done["notes"].append(f"{what} not merged: the game's {target} can't be read ({e})")
        return done
    if data is None:
        done["notes"].append(f"{what} not merged: the game's {target} isn't in extracted\\romfs")
        return done
    own = read(target) if read else None
    base = data if own is None else own
    try:
        new, notes = fn(base, check_log(name, diff))
    except LogError as e:
        done["notes"].append(f"{what} not merged: {e}")
        return done
    except _GAME_ERRORS as e:
        whose = "the game's" if own is None else "the mod's"
        done["notes"].append(f"{what} not merged: {whose} {target} can't be merged ({e})")
        return done
    done["notes"] += notes
    if new is None:
        return done
    done["files"][target] = new
    if sized:
        try:
            rstb = _cached(os.path.join(game, "romfs", *bnp.rm.RSTB.split("/")), bnp.Rstb)
        except (OSError, bnp.BnpError):
            rstb = None
        size_name = bnp.canon(target.split("//")[-1], data)
        entry = rstb.get(size_name) if rstb else None
        if entry is not None:
            need = bnp.needed_size(entry, ukmm.unpacked_size(data), ukmm.unpacked_size(new), True)
            if need > entry:
                done["sizes"][size_name] = need
    return done
