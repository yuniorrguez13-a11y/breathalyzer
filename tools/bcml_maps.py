#!/usr/bin/env python3
"""Merge BCML's map logs into the game's files for Savage Wind (BCML packages, tools/bnp.py):

    map.yml         map units: Pack/TitleBG.pack//Map/MainField/<U>/<U>_Static.smubin and the loose
                    Map/MainField/<U>/<U>_Dynamic.smubin (objects and rails added, changed, deleted by HashId)
    mainstatic.yml  Pack/Bootup.pack//Map/MainField/Static.smubin (start points, korok spots, markers...)
    dstatic.yml     Pack/Bootup.pack//Map/CDungeon/Static.smubin (shrine start points)
    quests.yml      Pack/TitleBG.pack//Quest/QuestProduct.sbquestpack (the quest list)

The logs are BCML's BYML text (YAML written by oead). The rules are BCML's, read from its source for the format and
the rules only; where this loader is safer than BCML it says so at the function. Nothing here runs anything from a
package, and the game's files are only read.

Two ways to use it:
- one function per log: merge_map, merge_mainstatic, merge_dstatic, merge_quests take the game's file bytes and the
  parsed logs (read_log) in order: the base log first, then each chosen option's, then the next mod's. A list inside
  that list is one package (its base and options); it only matters for dstatic.yml, which BCML merges deeply inside
  a package and entry by entry across mods. They return the merged bytes (the game's own when nothing changed) and
  notes: what was skipped, for "needs attention".
- the common interface of the BCML log modules: LOGS, LogError, read_log(name, data), combine(name, diffs),
  apply(name, diff, game) -> {"files": {target: bytes}, "sizes": {size table name: size}, "notes": [...]}.

A damaged log raises LogError (a message for the player); nothing of it is used.

It holds a small BYML reader and writer and a reader for BCML's BYML text (U32, byml_load, byml_dump,
byml_from_text, byml_same, f32). tools/byml.py (the planned shared module) is meant to replace them.
"""
from __future__ import annotations

import binascii
import base64
import math
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bnp  # noqa: E402
import ukmm  # noqa: E402
import wiiu_convert as wc  # noqa: E402

LOGS = {
    "map.yml": "map changes",
    "mainstatic.yml": "main field static actors",
    "dstatic.yml": "shrine entrances",
    "quests.yml": "quests",
}
MAX_LOG = 256 << 20                  # a log bigger than this is refused (BCML's biggest map logs are a few MB)
DEPTH = 64                           # BYML text nests about 8 deep; deeper is refused, not followed
TITLE = "Pack/TitleBG.pack"
BOOTUP = "Pack/Bootup.pack"
MAINSTATIC = BOOTUP + "//Map/MainField/Static.smubin"
DSTATIC = BOOTUP + "//Map/CDungeon/Static.smubin"
QUESTS = TITLE + "//Quest/QuestProduct.sbquestpack"
UNIT_RE = re.compile(r"([A-Za-z0-9-]{1,16})_(Static|Dynamic)")
REMOVE = "remove"                    # mainstatic.yml: an entry with this key is deleted
INDEX_ZERO = "--index_zero"          # quests.yml: "put this quest first"
PREV = "prev_quest"
UNDERRIDE = "UNDERRIDE_CONST"        # BCML's "keep the value that is there"
SECTIONS = ("Objs", "Rails")


class LogError(Exception):
    """A log that can't be used: damaged, or holding something this loader refuses. The message is for the player."""


# ---------------------------------------------------------------------------------------------------- BYML values
# int is a BYML int (I32), float a float (F32), U32 a uint; I64, U64, F64 and bytes (binary) are read and written
# but refused in the logs here: the game's map files (version 2) never use them.

class U32(int):
    """A BYML uint (type 0xD3)."""


class I64(int):
    """A BYML int64 (type 0xD4)."""


class U64(int):
    """A BYML uint64 (type 0xD5)."""


class F64(float):
    """A BYML double (type 0xD6)."""


def f32(x: float) -> float:
    """x rounded to a 32-bit float (too big: infinity)."""
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def _typed(v):
    """wc.byml_load's values with their types: ("uint", n) -> U32(n), a float's bits -> float..."""
    if isinstance(v, dict):
        return {k: _typed(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_typed(x) for x in v]
    if isinstance(v, tuple):
        kind, raw = v
        if kind == "int":
            return raw - (1 << 32) if raw & 0x80000000 else raw
        if kind == "uint":
            return U32(raw)
        if kind == "float":
            return struct.unpack("<f", struct.pack("<I", raw))[0]
        if kind == "int64":
            return I64(raw - (1 << 64) if raw >> 63 else raw)
        if kind == "uint64":
            return U64(raw)
        if kind == "double":
            return F64(struct.unpack("<d", struct.pack("<Q", raw))[0])
        raise wc.FormatError(f"BYML value type {kind}")
    return v


def unyaz0(data: bytes) -> bytes:
    return wc.yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data


def byml_load(data: bytes):
    """A Switch (little-endian) BYML document, Yaz0 or not, as typed values."""
    data = unyaz0(data)
    if wc.byml_order(data) != wc.LE:
        raise wc.FormatError("not a Switch (little-endian) BYML document")
    try:
        return _typed(wc.byml_load(data))
    except (IndexError, KeyError, ValueError, struct.error, RecursionError, UnicodeDecodeError) as e:
        raise wc.FormatError(f"damaged BYML ({type(e).__name__})") from None


def _kind(v) -> int:
    if isinstance(v, bool):
        return wc.BYML_BOOL
    if isinstance(v, str):
        return wc.BYML_STRING
    if isinstance(v, dict):
        return wc.BYML_DICT
    if isinstance(v, list):
        return wc.BYML_ARRAY
    if v is None:
        return wc.BYML_NULL
    if isinstance(v, (bytes, bytearray)):
        return wc.BYML_BINARY
    for cls, t in ((U32, wc.BYML_UINT), (I64, wc.BYML_INT64), (U64, wc.BYML_UINT64), (F64, wc.BYML_DOUBLE)):
        if isinstance(v, cls):
            return t
    if isinstance(v, int):
        return wc.BYML_INT
    if isinstance(v, float):
        return wc.BYML_FLOAT
    raise ValueError(f"{type(v).__name__} can't go into a BYML document")


_RANGE = {wc.BYML_INT: (-(1 << 31), 1 << 31, "<i"), wc.BYML_UINT: (0, 1 << 32, "<I"),
          wc.BYML_INT64: (-(1 << 63), 1 << 63, "<q"), wc.BYML_UINT64: (0, 1 << 64, "<Q")}


def byml_dump(root, share: bool = True) -> bytes:
    """A Switch BYML document (version 2, little-endian) of typed values. Key and string tables are sorted and
    unique; each container comes after its parent, every node 4-aligned, 8-byte values 8-aligned. share: equal
    containers are written once (as in the game's map files; its quest list shares none)."""
    if not isinstance(root, (dict, list)):
        raise ValueError("a BYML document's root is a dictionary or an array")
    keys: set[str] = set()
    strings: set[str] = set()
    stack = [root]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            for k, x in v.items():
                if not isinstance(k, str):
                    raise ValueError("BYML dictionary keys are strings")
                keys.add(k)
                stack.append(x)
        elif isinstance(v, list):
            stack.extend(v)
        elif isinstance(v, str):
            strings.add(v)
    key_list = sorted(keys, key=lambda s: s.encode())
    str_list = sorted(strings, key=lambda s: s.encode())
    kidx = {k: i for i, k in enumerate(key_list)}
    sidx = {s: i for i, s in enumerate(str_list)}
    buf = bytearray(16)
    shapes: dict[tuple, int] = {}       # a container's contents -> a number (equal containers: equal numbers)
    shape_of: dict[int, int] = {}       # id() of a container in root -> its number
    written: dict[int, int] = {}        # a number -> where that container was written

    def shape(v) -> int:
        """Equal containers are written once, as the game's own files do (they share a third of their maps)."""
        n = shape_of.get(id(v))
        if n is None:
            items = sorted(v.items()) if isinstance(v, dict) else enumerate(v)
            key = (isinstance(v, dict),) + tuple(
                (k, _kind(x), shape(x) if isinstance(x, (dict, list)) else
                 struct.pack("<d", x) if isinstance(x, float) else x) for k, x in items)
            n = shape_of[id(v)] = shapes.setdefault(key, len(shapes))
        return n

    def pad(a: int) -> None:
        buf.extend(bytes(-len(buf) % a))

    def table(items: list[str]) -> int:
        if not items:
            return 0
        pad(4)
        off = len(buf)
        enc = [s.encode() + b"\0" for s in items]
        offs, pos = [], 4 + 4 * (len(enc) + 1)
        for e in enc:
            offs.append(pos)
            pos += len(e)
        offs.append(pos)
        buf.extend(bytes([wc.BYML_STRINGS]) + len(enc).to_bytes(3, "little"))
        buf.extend(struct.pack(f"<{len(offs)}I", *offs))
        buf.extend(b"".join(enc))
        return off

    def inline(t: int, v) -> int:
        if t == wc.BYML_STRING:
            return sidx[v]
        if t == wc.BYML_BOOL:
            return int(v)
        if t == wc.BYML_NULL:
            return 0
        if t == wc.BYML_FLOAT:
            return struct.unpack("<I", struct.pack("<f", f32(v)))[0]
        lo, hi, fmt = _RANGE[t]
        if not lo <= v < hi:
            raise ValueError(f"{v} doesn't fit a BYML {wc.BYML_NAMES[t]}")
        return struct.unpack("<I", struct.pack(fmt, v))[0]

    def container(v) -> int:
        same_as = shape(v) if share else id(v)
        if same_as in written:
            return written[same_as]
        pad(4)
        off = len(buf)
        if share:
            written[same_as] = off
        n = len(v)
        if n >= 1 << 24:
            raise ValueError("a BYML container with too many items")
        if isinstance(v, list):
            items = v
            types = [_kind(x) for x in items]
            buf.extend(bytes([wc.BYML_ARRAY]) + n.to_bytes(3, "little") + bytes(types))
            pad(4)
            slots = [len(buf) + 4 * k for k in range(n)]
            buf.extend(bytes(4 * n))
        else:
            pairs = sorted(v.items(), key=lambda kv: kidx[kv[0]])
            items = [x for _, x in pairs]
            types = [_kind(x) for x in items]
            buf.extend(bytes([wc.BYML_DICT]) + n.to_bytes(3, "little"))
            slots = []
            for (k, _), t in zip(pairs, types):
                buf.extend(kidx[k].to_bytes(3, "little") + bytes([t]))
                slots.append(len(buf))
                buf.extend(bytes(4))
        for x, t, at in zip(items, types, slots):
            if t in (wc.BYML_ARRAY, wc.BYML_DICT):
                value = container(x)
            elif t in wc.BYML_LONG:
                pad(8)
                value = len(buf)
                if t == wc.BYML_DOUBLE:
                    buf.extend(struct.pack("<d", x))
                else:
                    lo, hi, fmt = _RANGE[t]
                    if not lo <= x < hi:
                        raise ValueError(f"{x} doesn't fit a BYML {wc.BYML_NAMES[t]}")
                    buf.extend(struct.pack(fmt, x))
            elif t == wc.BYML_BINARY:
                pad(4)
                value = len(buf)
                buf.extend(struct.pack("<I", len(x)) + bytes(x))
            else:
                value = inline(t, x)
            struct.pack_into("<I", buf, at, value)
        return off

    key_off = table(key_list)
    str_off = table(str_list)
    root_off = container(root)
    pad(4)
    struct.pack_into("<2sHIII", buf, 0, b"YB", 2, key_off, str_off, root_off)
    return bytes(buf)


def byml_same(a, b) -> bool:
    """Deep equality that minds the BYML type (int vs U32 vs bool...); floats compare as 32-bit floats."""
    stack = [(a, b)]
    while stack:
        x, y = stack.pop()
        if type(x) is not type(y):
            return False
        if isinstance(x, dict):
            if x.keys() != y.keys():
                return False
            stack.extend((x[k], y[k]) for k in x)
        elif isinstance(x, list):
            if len(x) != len(y):
                return False
            stack.extend(zip(x, y))
        elif isinstance(x, F64):
            if struct.pack("<d", x) != struct.pack("<d", y):
                return False
        elif isinstance(x, float):
            if struct.pack("<f", f32(x)) != struct.pack("<f", f32(y)):
                return False
        elif x != y:
            return False
    return True


# ---------------------------------------------------------------------------------------------------- BYML text
# What oead.byml.to_text writes (libyaml): block maps and lists, flow [..] and {..} of scalars that libyaml wraps over
# lines at 80 columns, quoted and folded strings, the tags !u (U32, hex), !l (I64), !ul (U64), !f64 (F64) and
# !!binary. Untagged: true/false, null, an integer is an int (0x hex, a leading 0 octal), a number with "." or an
# exponent a float, anything else a string; quoted text is always a string. Anchors and aliases are refused (oead
# never writes them). Scalar and key reading come from tools/ukmm.py's YAML subset.

_NULLS = {"", "~", "null", "Null", "NULL"}
_TRUE = {"true", "True", "TRUE"}
_FALSE = {"false", "False", "FALSE"}
_INT_RE = re.compile(r"[-+]?(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)")
_FLOAT_RE = re.compile(r"[-+]?(?:(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?|\d+[eE][-+]?\d+|\.(?:inf|Inf|INF))"
                       r"|\.(?:nan|NaN|NAN)")


def _int(text: str) -> int:
    """An integer as C's strtoull with base 0 reads it (0x hex, a leading 0 octal, else decimal), with a sign."""
    t = text.strip()
    neg = t[:1] == "-"
    if t[:1] in ("-", "+"):
        t = t[1:]
    if t[:2] in ("0x", "0X"):
        v = int(t[2:], 16)
    elif len(t) > 1 and t[0] == "0":
        v = int(t[1:], 8)
    else:
        v = int(t, 10)
    return -v if neg else v


def _float(text: str) -> float:
    t = text.strip()
    low = t.lower()
    if low.lstrip("+-") == ".inf":
        return -math.inf if low.startswith("-") else math.inf
    if low == ".nan":
        return math.nan
    return float(t)


def _plain(text: str):
    """An untagged plain scalar's value."""
    if text in _NULLS:
        return None
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    if _INT_RE.fullmatch(text):
        v = _int(text)
        for cls, lo, hi in ((int, -(1 << 31), 1 << 31), (U32, 0, 1 << 32), (I64, -(1 << 63), 1 << 63),
                            (U64, 0, 1 << 64)):
            if lo <= v < hi:
                return cls(v)
        raise LogError(f"the number {text} is too big")
    if _FLOAT_RE.fullmatch(text):
        return f32(_float(text))
    return text


def _tagged(tag: str, text: str, plain: bool):
    try:
        if tag in ("!u", "!l", "!ul", "!!int"):
            v = _int(text)
            cls, lo, hi = {"!u": (U32, 0, 1 << 32), "!l": (I64, -(1 << 63), 1 << 63), "!ul": (U64, 0, 1 << 64),
                           "!!int": (int, -(1 << 31), 1 << 31)}[tag]
            if not lo <= v < hi:
                raise ValueError
            return cls(v)
        if tag == "!f64":
            return F64(_float(text))
        if tag == "!!float":
            return f32(_float(text))
        if tag == "!!str":
            return text
        if tag == "!!bool" and text in _TRUE | _FALSE:
            return text in _TRUE
        if tag == "!!null" and (text in _NULLS or not plain):
            return None
        if tag in ("!!binary", "!<tag:yaml.org,2002:binary>"):
            return base64.b64decode("".join(text.split()), validate=True)
    except (ValueError, binascii.Error):
        raise LogError(f"the value {text!r} doesn't fit its tag {tag}") from None
    raise LogError(f"the tag {tag} on {text!r} isn't one BCML writes")


def _dash(s: str) -> bool:
    return s == "-" or s.startswith("- ")


def _keyish(s: str) -> bool:
    return s[:1] not in ("-", "[", "{", "!", "&", "*", "|", ">", "?", "#") and ukmm._key(s) is not None


class _Text:
    """One BYML text document, read line by line (block style) with flow collections read by _Flow."""

    def __init__(self, text: str):
        self.ls = ukmm._Lines(text)

    def fail(self, what: str):
        raise LogError(f"line {self.ls.i + 1}: {what}")

    def doc(self):
        nxt = self.ls.peek()
        if nxt is None:
            return None
        ind, s = nxt
        if s.startswith("? ") or s == "?":
            self.fail("complex keys aren't used by BCML")
        if _dash(s) or _keyish(s):
            v = self.block(ind, 0)
        else:
            self.ls.i += 1
            v = self.node(s, -1, 0, True)
        if self.ls.peek() is not None:
            self.fail("text after the end of the document")
        return v

    def block(self, indent: int, depth: int):
        if depth > DEPTH:
            raise LogError("the log nests too deep")
        if _dash(self.ls.peek()[1]):
            out: list = []
            while True:
                nxt = self.ls.peek()
                if nxt is None or nxt[0] != indent or not _dash(nxt[1]):
                    break
                rest = nxt[1][1:].strip()
                if rest and (_dash(rest) or _keyish(rest)):
                    # "- key: value" or "- - item": a map or list that starts on the dash's line
                    after = self.ls.lines[self.ls.i][indent + 1:]
                    inner = indent + 1 + len(after) - len(after.lstrip(" "))
                    self.ls.lines[self.ls.i] = " " * inner + rest
                    out.append(self.block(inner, depth + 1))
                else:
                    self.ls.i += 1
                    out.append(self.node(rest, indent, depth + 1, True))
        else:
            out = {}
            while True:
                nxt = self.ls.peek()
                if nxt is None or nxt[0] != indent:
                    break
                s = nxt[1]
                if s.startswith("? "):
                    self.fail("complex keys aren't used by BCML")
                kv = ukmm._key(s) if _keyish(s) else None
                if kv is None:
                    self.fail("isn't a 'key: value' line")
                self.ls.i += 1
                if kv[0] in out:
                    self.fail(f"the key {kv[0]!r} is there twice")
                out[kv[0]] = self.node(kv[1], indent, depth + 1, False)
        nxt = self.ls.peek()
        if nxt is not None and nxt[0] > indent:
            self.fail("is indented more than it should be")
        return out

    def node(self, first: str, parent: int, depth: int, in_list: bool):
        """A value whose text starts with `first` (the rest of a line already read), maybe the block below it."""
        if depth > DEPTH:
            raise LogError("the log nests too deep")
        tag = None
        if first.startswith("!"):
            tag, _, first = first.partition(" ")
            first = first.strip()
        if first[:1] in ("&", "*"):
            self.fail("anchors and aliases (& *) aren't used by BCML")
        if not first or first.startswith("#"):
            nxt = self.ls.peek()
            if nxt is not None and (nxt[0] > parent or (not in_list and nxt[0] == parent and _dash(nxt[1]))):
                if tag not in (None, "!!map", "!!seq"):
                    self.fail(f"the tag {tag} on a list or map")
                return self.block(nxt[0], depth + 1)
            return _tagged(tag, "", True) if tag else None
        if first[0] in "[{":
            if tag not in (None, "!!map", "!!seq"):
                self.fail(f"the tag {tag} on a list or map")
            f = _Flow(self, first)
            v = f.value(depth)
            f.end()
            return v
        try:
            text = ukmm._scalar_text(first, self.ls, parent)
        except ukmm.UkmmError as e:
            raise LogError(f"line {self.ls.i + 1}: {e}") from None
        quoted = first[0] in ("'", '"', "|", ">")
        if tag:
            return _tagged(tag, text, not quoted)
        return text if quoted else _plain(text)


class _Flow:
    """A flow collection ([...] or {...}) of BYML text, which may run over several lines."""

    def __init__(self, owner: _Text, first: str):
        self.o = owner
        self.line = first
        self.i = 0

    def more(self) -> bool:
        ls = self.o.ls
        if ls.i >= len(ls.lines):
            return False
        self.line = ls.lines[ls.i]
        ls.i += 1
        self.i = 0
        return True

    def peek(self) -> str:
        """The next character that isn't a space or in a comment, reading more lines when needed."""
        while True:
            line = self.line
            while self.i < len(line) and line[self.i] in " \t":
                self.i += 1
            if self.i < len(line) and line[self.i] != "#":
                return line[self.i]
            if not self.more():
                self.o.fail("a [ or { without its closing bracket")

    def end(self) -> None:
        """Only a comment may follow the collection on its last line."""
        rest = self.line[self.i:].strip()
        if rest and not rest.startswith("#"):
            self.o.fail(f"text after a closing bracket: {rest[:20]!r}")

    def value(self, depth: int):
        if depth > DEPTH:
            raise LogError("the log nests too deep")
        c = self.peek()
        tag = None
        if c == "!":
            j = self.i
            while j < len(self.line) and self.line[j] not in " ,[]{}":
                j += 1
            tag, self.i = self.line[self.i:j], j
            c = self.peek()
        if c in "&*":
            self.o.fail("anchors and aliases (& *) aren't used by BCML")
        if c in "[{":
            if tag not in (None, "!!map", "!!seq"):
                self.o.fail(f"the tag {tag} on a list or map")
            close = "]" if c == "[" else "}"
            self.i += 1
            out: list | dict = [] if c == "[" else {}
            while True:
                c = self.peek()
                if c == close:
                    self.i += 1
                    return out
                if close == "]":
                    out.append(self.value(depth + 1))
                else:
                    key = self.scalar(True)[0]
                    if self.peek() != ":":
                        self.o.fail("a key without ':' in a { } map")
                    self.i += 1
                    if key in out:
                        self.o.fail(f"the key {key!r} is there twice")
                    out[key] = None if self.peek() in ",}" else self.value(depth + 1)
                c = self.peek()
                if c == ",":
                    self.i += 1
                elif c != close:
                    self.o.fail(f"expected ',' or '{close}', found {c!r}")
        if c in ",]}":
            return _tagged(tag, "", True) if tag else None
        text, quoted = self.scalar(False)
        if tag:
            return _tagged(tag, text, not quoted)
        return text if quoted else _plain(text)

    def scalar(self, key: bool) -> tuple[str, bool]:
        """A quoted or plain scalar (its text, quoted?), folded over line breaks as YAML does."""
        q = self.line[self.i]
        parts: list[str] = []
        if q in ("'", '"'):
            self.i += 1
            while True:
                end = ukmm._quote_end(self.line[self.i:], q)
                if end >= 0:
                    parts.append(self.line[self.i:self.i + end])
                    self.i += end + 1
                    break
                parts.append(self.line[self.i:])
                if not self.more():
                    self.o.fail("quoted text without its closing quote")
            if len(parts) > 1:                               # line breaks fold: the spaces around them go
                parts = [parts[0].rstrip()] + [p.strip() for p in parts[1:-1]] + [parts[-1].lstrip()]
            if q == "'":
                return ukmm._fold([p.replace("''", "'") for p in parts]), True
            text = ""
            for k, p in enumerate(parts):
                if k:
                    if text.endswith("\\") and not text.endswith("\\\\"):
                        text = text[:-1]
                    else:
                        text += " " if p else "\n"
                text += p
            return ukmm._unquote_double(text), True
        while True:
            line, j = self.line, self.i
            while j < len(line):
                ch = line[j]
                if ch in ",[]{}" or (ch == "#" and j and line[j - 1] in " \t"):
                    break
                if key and ch == ":" and (j + 1 >= len(line) or line[j + 1] in " ,]}"):
                    break
                j += 1
            parts.append(line[self.i:j].strip())
            self.i = j
            if j < len(line) and line[j] != "#":
                break
            if not self.more():
                self.o.fail("a [ or { without its closing bracket")
            c = self.peek()
            if c in ",[]{}" or (key and c == ":"):
                break
        return ukmm._fold(parts), False


def byml_from_text(text: str):
    """BCML's BYML text (YAML written by oead) as typed values. Raises LogError when it can't be read."""
    try:
        return _Text(text).doc()
    except RecursionError:
        raise LogError("the log nests too deep") from None


# ---------------------------------------------------------------------------------------------------- helpers

def dict_merge(a: dict, b: dict) -> dict:
    """BCML's util.dict_merge(a, b, overwrite_lists=True), without changing a: maps merge key by key, anything else
    (lists too) from b replaces a's; b's "UNDERRIDE_CONST" keeps a's value."""
    out = dict(a)
    for k, v in b.items():
        old = out.get(k)
        if isinstance(old, dict) and isinstance(v, dict):
            out[k] = dict_merge(old, v)
        elif not (isinstance(v, str) and v == UNDERRIDE and k in out):
            out[k] = v
    return out


def _packages(logs) -> list[list]:
    """logs as packages: a list in the list is one package (base, options), anything else one package of one log."""
    if not isinstance(logs, (list, tuple)):
        raise TypeError("logs: a list of parsed logs")
    return [list(d) if isinstance(d, (list, tuple)) else [d] for d in logs]


def _flat(logs) -> list:
    return [d for p in _packages(logs) for d in p]


_REFUSED = ((I64, "int64"), (U64, "uint64"), (F64, "double"), (bytes, "binary"))


def _check_types(v, where: str) -> None:
    """Refuse the value types the game's version 2 map files never use."""
    stack = [v]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
        else:
            for cls, name in _REFUSED:
                if isinstance(x, cls):
                    raise LogError(f"{where} holds a {name} value, which the game's map files never use")


def _hid(obj) -> int | None:
    """An object's HashId as a u32, or None."""
    h = obj.get("HashId") if isinstance(obj, dict) else None
    if isinstance(h, int) and not isinstance(h, bool):
        return h & 0xFFFFFFFF
    return None


def _encode(root, like: bytes, share: bool = True) -> bytes:
    """root as BYML, Yaz0-compressed (stored when big) when `like` (the file it replaces) was."""
    raw = byml_dump(root, share)
    if like[:4] == b"Yaz0":
        return ukmm.yaz0(raw, struct.unpack_from(">I", like, 8)[0])
    return raw


def _load_game(data: bytes, what: str):
    try:
        return byml_load(data)
    except wc.FormatError as e:
        raise LogError(f"the game's {what} can't be read ({e})") from None


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _some(items, n: int = 3) -> str:
    items = [str(x) for x in items]
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


# ---------------------------------------------------------------------------------------------------- map.yml
# {"A-1_Static": {"Objs": {"add": [obj], "mod": {"<HashId>": obj}, "del": [!u HashId]}, "Rails": {same}}}; an older
# log has {add, mod, del} for the objects only. Objects are whole. BCML's own merge (Rust merge_maps): changes ("mod")
# replace the object with that HashId (one the game lacks is added), deletions remove, additions are appended unless
# the game's map has their HashId (even one deleted now), then all sorted by HashId as u32.

def _map_section(s, where: str) -> dict:
    if s is None:
        s = {}
    if not isinstance(s, dict) or not set(s) <= {"add", "mod", "del"}:
        raise LogError(f"{where} isn't an add/mod/del list")
    add, mod, dele = s.get("add") or [], s.get("mod") or {}, s.get("del") or []
    if not isinstance(add, list) or not all(_hid(o) is not None for o in add):
        raise LogError(f"{where}: an added object has no HashId")
    if not isinstance(mod, dict):
        raise LogError(f"{where}: 'mod' isn't a map of HashIds")
    mods = {}
    for k, o in mod.items():
        try:
            h = _int(k)
            if not -(1 << 31) <= h < 1 << 32:
                raise ValueError
        except ValueError:
            raise LogError(f"{where}: {k!r} isn't a HashId") from None
        if not isinstance(o, dict):
            raise LogError(f"{where}: the change of {k} isn't an object")
        mods[h & 0xFFFFFFFF] = o
    if not isinstance(dele, list) or not all(isinstance(h, int) and not isinstance(h, bool) for h in dele):
        raise LogError(f"{where}: 'del' isn't a list of HashIds")
    return {"add": add, "mod": mods, "del": [h & 0xFFFFFFFF for h in dele]}


def _map_read(root, where: str) -> dict:
    if root is None:
        return {}
    if not isinstance(root, dict):
        raise LogError(f"{where} isn't a list of map units")
    out = {}
    for unit, u in root.items():
        if not UNIT_RE.fullmatch(unit):
            raise LogError(f"{where}: {unit!r} isn't a map unit name like A-1_Static")
        if not isinstance(u, dict):
            raise LogError(f"{where}: {unit} isn't a map change")
        if "Objs" not in u and "Rails" not in u and set(u) <= {"add", "mod", "del"}:
            u = {"Objs": u}                                  # an older BCML log: objects only
        if not set(u) <= set(SECTIONS):
            raise LogError(f"{where}: {unit} has {_some(sorted(set(u) - set(SECTIONS)))}, not Objs and Rails")
        out[unit] = {sec: _map_section(u.get(sec), f"{where}: {unit} {sec}") for sec in SECTIONS}
    return out


def _map_combine(logs) -> dict:
    """BCML's consolidate: per unit and section, a later change wins, deletions add up, additions by HashId (the
    later one wins)."""
    out: dict = {}
    for diff in _flat(logs):
        for unit, u in diff.items():
            dst = out.setdefault(unit, {sec: {"add": {}, "mod": {}, "del": {}} for sec in SECTIONS})
            for sec in SECTIONS:
                s, d = u.get(sec) or {}, dst[sec]
                d["mod"].update(s.get("mod") or {})
                d["del"].update(dict.fromkeys(s.get("del") or ()))
                for obj in s.get("add") or ():
                    h = _hid(obj)
                    d["add"].pop(h, None)
                    d["add"][h] = obj
    return {unit: {sec: {"add": list(d["add"].values()), "mod": d["mod"], "del": list(d["del"])}
                   for sec, d in u.items()} for unit, u in out.items()}


def _sort_key(obj) -> int:
    h = _hid(obj)
    return 0 if h is None else h


def _merge_section(stock: list, s: dict) -> tuple[list, list[int]]:
    """One section (Objs or Rails) of a unit with its combined change; also the HashIds of additions skipped."""
    game_ids = [_hid(o) for o in stock]
    known = set(game_ids) - {None}
    rows = list(zip(game_ids, stock))
    where = {}
    for i, h in enumerate(game_ids):
        if h is not None:
            where.setdefault(h, i)
    orphans = []
    first = {h: stock[i] for h, i in where.items()}
    for h, obj in s["mod"].items():
        i = where.get(h)
        if i is None:
            orphans.append(obj)                              # a change of an object the game lacks: an addition
        else:
            rows[i] = (h, obj)
    dels = set(s["del"])
    kept = [obj for h, obj in rows if h is None or h not in dels]
    skipped, added = [], set()
    for obj in list(s["add"]) + orphans:
        h = _hid(obj)
        if h in known:
            if not byml_same(first.get(h), obj):            # (the same object again is no change)
                skipped.append(h)
        elif h not in added:
            added.add(h)
            kept.append(obj)
    kept.sort(key=_sort_key)
    return kept, skipped


def _merge_unit(root, d: dict, unit: str) -> tuple[object, list[str]]:
    if not isinstance(root, dict):
        return root, [f"map.yml: the game's {unit} isn't a map unit, so its changes were skipped"]
    out, notes = dict(root), []
    for sec in SECTIONS:
        s = d.get(sec) or {}
        if not (s.get("add") or s.get("mod") or s.get("del")):
            continue
        stock = root.get(sec)
        if not isinstance(stock, list):
            notes.append(f"map.yml: the game's {unit} has no {sec} list, so its {sec} changes were skipped")
            continue
        out[sec], skipped = _merge_section(stock, s)
        if skipped:
            what = "added object" if sec == "Objs" else "added rail"
            notes.append(f"map.yml: {unit}: {_plural(len(skipped), what)} skipped: the "
                         f"game's map already has the HashId ({_some(f'0x{h:08x}' for h in skipped)})")
    return out, notes


def map_units(diff) -> list[str]:
    """The units ("A-1_Static"...) parsed logs or a combined map change touch."""
    if isinstance(diff, dict):
        return sorted(diff)
    return sorted({u for d in _flat(diff) for u in d})


def map_target(unit: str) -> str:
    """Where a unit's file is: statics in TitleBG.pack (written there, not loose as BCML does), dynamics loose."""
    m = UNIT_RE.fullmatch(unit)
    if not m:
        raise LogError(f"{unit!r} isn't a map unit name")
    rel = f"Map/MainField/{m.group(1)}/{unit}.smubin"
    return f"{TITLE}//{rel}" if m.group(2) == "Static" else rel


def merge_map(units: dict[str, bytes], logs) -> tuple[dict[str, bytes], list[str]]:
    """units: the game's files of the units the logs change ("A-1_Static" -> its .smubin, Yaz0 or not; map_units
    lists them). Returns the units that changed (new bytes, compressed as the game's were) and notes."""
    return _merge_map(units, _map_combine(logs))


def _merge_map(units: dict[str, bytes], diff: dict) -> tuple[dict[str, bytes], list[str]]:
    out, notes = {}, []
    for unit in sorted(diff):
        if not any(s.get(k) for s in diff[unit].values() for k in ("add", "mod", "del")):
            continue
        data = units.get(unit)
        if data is None:
            notes.append(f"map.yml: your game has no map unit {unit}, so its changes were skipped")
            continue
        root = _load_game(data, unit)
        merged, n = _merge_unit(root, diff[unit], unit)
        notes += n
        if not byml_same(root, merged):
            out[unit] = _encode(merged, data)
    return out, notes


# ---------------------------------------------------------------------------------------------------- mainstatic.yml
# {category: {ID: entry}}: new and changed entries whole; a deleted one is the game's entry plus a "remove" key. The
# ID is ceil(X), ceil(Y), ceil(Z) of Translate and the value of the first key (in BYML's sorted order) whose name
# holds "name", all written together with no separator. BCML merges the entries into the game's by ID (dict_merge),
# drops those with "remove", appends new ones and ignores categories the game lacks.

def static_id(entry) -> str | None:
    """BCML's ID of a mainstatic entry, or None when it has no usable Translate."""
    if not isinstance(entry, dict):
        return None
    t = entry.get("Translate")
    try:
        xyz = "".join(str(math.ceil(t[a])) for a in "XYZ")
    except (TypeError, KeyError, ValueError, OverflowError):
        return None
    name = next((entry[k] for k in sorted(entry, key=lambda s: s.encode()) if "name" in k.lower()), "")
    return xyz + (name if isinstance(name, str) else str(name))


def _cats_read(root, where: str, nested: bool) -> dict:
    if root is None:
        return {}
    if not isinstance(root, dict):
        raise LogError(f"{where} isn't a map of entries")
    for k, v in root.items():
        if nested:
            if v is not None and not isinstance(v, dict):
                raise LogError(f"{where}: {k} isn't a map of entries")
            for kk, e in (v or {}).items():
                if not isinstance(e, dict):
                    raise LogError(f"{where}: {k} {kk} isn't an entry")
        elif not isinstance(v, dict):
            raise LogError(f"{where}: {k} isn't an entry")
    return {k: dict(v or {}) for k, v in root.items()} if nested else root


def _mainstatic_combine(logs) -> dict:
    out: dict = {}
    for diff in _flat(logs):
        out = dict_merge(out, diff)
    return out


def merge_mainstatic(game: bytes, logs) -> tuple[bytes, list[str]]:
    """game: Map/MainField/Static.smubin (from Bootup.pack). Returns the merged file and notes."""
    return _merge_bytes(game, "Map/MainField/Static.smubin", _mainstatic_apply, _mainstatic_combine(logs))


def _merge_bytes(game: bytes, what: str, fn, diff, share: bool = True) -> tuple[bytes, list[str]]:
    root = _load_game(game, what)
    merged, notes = fn(root, diff)
    return (game if byml_same(root, merged) else _encode(merged, game, share)), notes


def _mainstatic_apply(root, diff: dict) -> tuple[object, list[str]]:
    """Safer than BCML where the game has several entries with one ID (11 in NonAutoGenArea, 1 in
    StaticGrudgeLocation): BCML keeps one of them and loses the other; here all stay, and a change or deletion picks
    the entry it equals (a deletion) or the last one (as BCML's merge would), named in the notes."""
    if not isinstance(root, dict):
        return root, ["mainstatic.yml: the game's Static.smubin isn't a map of categories, so it was skipped"]
    out, notes = dict(root), []
    extra = [c for c in diff if c not in root]
    if extra:
        notes.append(f"mainstatic.yml: {_plural(len(extra), 'category', 'categories')} the game doesn't have "
                     f"ignored, as BCML does ({_some(extra)})")
    for cat, entries in diff.items():
        if cat not in root or not entries:
            continue
        stock = root[cat]
        if not isinstance(stock, list):
            notes.append(f"mainstatic.yml: the game's {cat} isn't a list, so its changes were skipped")
            continue
        ids: dict[str, list[int]] = {}
        for i, e in enumerate(stock):
            k = static_id(e)
            if k is not None:
                ids.setdefault(k, []).append(i)
        rows, gone, new, unsure = list(stock), set(), [], []
        for key, item in entries.items():
            at = ids.get(key)
            removing = REMOVE in item
            if not at:
                if not removing:
                    new.append(item)
                continue
            i = at[-1]
            if len(at) > 1:
                plain = {k: v for k, v in item.items() if k != REMOVE}
                same = [j for j in at if byml_same(stock[j], plain)]
                if same and not removing:
                    continue                                 # one of them already is this entry
                if same:
                    i = same[0]
                else:
                    unsure.append(key)
            if removing:
                gone.add(i)
            else:
                rows[i] = dict_merge(rows[i], item) if isinstance(rows[i], dict) else item
        out[cat] = [e for i, e in enumerate(rows) if i not in gone and not (isinstance(e, dict) and REMOVE in e)]
        out[cat] += [e for e in new if REMOVE not in e]
        if unsure:
            notes.append(f"mainstatic.yml: {cat}: the game has several entries with the ID {_some(unsure, 2)}; the "
                         "change or removal went to the last of them")
    return out, notes


# ---------------------------------------------------------------------------------------------------- dstatic.yml
# {"<Map>___<PosName>": entry}: a new start point whole, a changed one only its Rotate and/or Translate. BCML: inside
# a package dict_merge; across mods a later mod's entry replaces an earlier one's whole; applied to StartPos: a
# matching (Map, PosName) gets the entry's keys, others are appended. A key without "___" means its Entrance_1.

def _dstatic_combine(logs) -> dict:
    out: dict = {}
    for pkg in _packages(logs):
        merged: dict = {}
        for diff in pkg:
            merged = dict_merge(merged, diff)
        out.update(merged)
    return out


def merge_dstatic(game: bytes, logs) -> tuple[bytes, list[str]]:
    """game: Map/CDungeon/Static.smubin (from Bootup.pack). Returns the merged file and notes."""
    return _merge_bytes(game, "Map/CDungeon/Static.smubin", _dstatic_apply, _dstatic_combine(logs))


def _dstatic_apply(root, diff: dict) -> tuple[object, list[str]]:
    """Safer than BCML: a new start point without a Map and PosName (which BCML would add as it is) is skipped."""
    if not diff:
        return root, []
    stock = root.get("StartPos") if isinstance(root, dict) else None
    if not isinstance(stock, list):
        return root, ["dstatic.yml: the game's CDungeon/Static.smubin has no StartPos list, so it was skipped"]
    rows = list(stock)
    where: dict[str, int] = {}
    for i, e in enumerate(rows):
        if isinstance(e, dict):
            where.setdefault(f"{e.get('Map')}___{e.get('PosName')}", i)
    bad = []
    for key, item in diff.items():
        k = key if "___" in key else key + "___Entrance_1"
        i = where.get(k)
        if i is not None:
            rows[i] = {**rows[i], **item}
        elif isinstance(item.get("Map"), str) and isinstance(item.get("PosName"), str):
            where[k] = len(rows)
            rows.append(item)
        else:
            bad.append(key)
    notes = [f"dstatic.yml: {_plural(len(bad), 'new shrine entrance')} without a Map and PosName skipped "
             f"({_some(bad)})"] if bad else []
    return {**root, "StartPos": rows}, notes


# ---------------------------------------------------------------------------------------------------- quests.yml
# {add: [quest + prev_quest], mod: {Name: quest + prev_quest}, del: [Name]}. prev_quest is the quest before it in
# the mod's list ("--index_zero": first). BCML sets prev_quest before comparing, so "mod" lists nearly every quest.

def _quest(q, where: str) -> tuple[dict, str | None]:
    if not isinstance(q, dict) or not isinstance(q.get("Name"), str):
        raise LogError(f"{where}: a quest without a Name")
    prev = q.get(PREV)
    if prev is not None and not isinstance(prev, str):
        raise LogError(f"{where}: {q['Name']}'s prev_quest isn't a quest name")
    return {k: v for k, v in q.items() if k != PREV}, prev


def _quests_read(root, where: str) -> dict:
    if root is None:
        root = {}
    if not isinstance(root, dict) or not set(root) <= {"add", "mod", "del"}:
        raise LogError(f"{where} isn't an add/mod/del list of quests")
    add, mod, dele = root.get("add") or [], root.get("mod") or {}, root.get("del") or []
    if not isinstance(add, list) or not isinstance(mod, dict) or not isinstance(dele, list):
        raise LogError(f"{where}: add, mod or del has the wrong shape")
    if not all(isinstance(n, str) for n in dele):
        raise LogError(f"{where}: 'del' isn't a list of quest names")
    return {"add": [_quest(q, f"{where} add") for q in add],
            "mod": {str(k): _quest(q, f"{where} mod") for k, q in mod.items()}, "del": list(dele)}


def merge_quests(game: bytes, logs) -> tuple[bytes, list[str]]:
    """game: Quest/QuestProduct.sbquestpack (from TitleBG.pack). Returns the merged file and notes."""
    return _merge_bytes(game, "QuestProduct.sbquestpack", _quests_apply, _flat(logs), share=False)


def _quests_apply(root, diffs: list) -> tuple[object, list[str]]:
    """Safer than BCML: a "mod" entry equal to the game's quest is no change (BCML's prev_quest makes every quest look
    changed, so one mod's log would undo another's edits); a later change wins (BCML: the earliest); positions are
    looked up again after every step; an addition of a quest the list already has is skipped (BCML adds it twice);
    prev_quest never reaches the game's file."""
    if not isinstance(root, list) or not all(isinstance(q, dict) and isinstance(q.get("Name"), str) for q in root):
        return root, ["quests.yml: the game's quest list can't be read, so it was skipped"]
    game = {}
    for q in root:
        game.setdefault(q["Name"], q)
    mods: dict[str, tuple[dict, str | None]] = {}
    adds: dict[str, tuple[dict, str | None]] = {}
    dels: dict[str, None] = {}
    for d in diffs:
        for name, (q, prev) in d["mod"].items():
            if name in game and byml_same(q, game[name]):
                continue
            mods[name] = (q, prev)
        for q, prev in d["add"]:
            adds[q["Name"]] = (q, prev)
        dels.update(dict.fromkeys(d["del"]))
    rows = list(root)
    for name, (q, prev) in mods.items():
        i = next((i for i, r in enumerate(rows) if r["Name"] == name), None)
        if i is None:
            adds.setdefault(name, (q, prev))
        else:
            rows[i] = q
    rows = [r for r in rows if r["Name"] not in dels]
    names = [r["Name"] for r in rows]
    pending, twice = list(adds.items()), []
    while pending:
        later = []
        for name, (q, prev) in pending:
            if name in names:
                if not byml_same(rows[names.index(name)], q):    # (the same quest again is no change)
                    twice.append(name)
            elif prev == INDEX_ZERO:
                rows.insert(0, q)
                names.insert(0, name)
            elif prev in names:
                i = names.index(prev) + 1
                rows.insert(i, q)
                names.insert(i, name)
            elif prev is not None and prev in adds and prev not in twice:
                later.append((name, (q, prev)))              # after another new quest: once that one is in
            else:
                rows.append(q)
                names.append(name)
        if len(later) == len(pending):                       # a loop of prev_quests: at the end, in order
            for name, (q, _) in later:
                rows.append(q)
                names.append(name)
            later = []
        pending = later
    notes = [f"quests.yml: {_plural(len(twice), 'added quest')} skipped: the quest list already has "
             f"{'it' if len(twice) == 1 else 'them'} ({_some(twice)})"] if twice else []
    return rows, notes


# ---------------------------------------------------------------------------------------------------- interface

def read_log(name: str, data: bytes):
    """One log file -> its change (what combine and the merge_* functions take). LogError when it can't be used."""
    name = name.lower()
    if name not in LOGS:
        raise LogError(f"{name} isn't a log this module merges")
    if len(data) > MAX_LOG:
        raise LogError(f"{name} is too big ({len(data) >> 20} MB)")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise LogError(f"{name} isn't UTF-8 text") from None
    try:
        root = byml_from_text(text)
    except LogError as e:
        raise LogError(f"{name} can't be read: {e}") from None
    _check_types(root, name)
    if name == "map.yml":
        return _map_read(root, name)
    if name == "mainstatic.yml":
        return _cats_read(root, name, True)
    if name == "dstatic.yml":
        return _cats_read(root, name, False)
    return _quests_read(root, name)


def combine(name: str, diffs: list):
    """Changes in order (base, options in info.json's order, then mods from low to high priority) as one. A list
    inside `diffs` is one package's base and options."""
    name = name.lower()
    if name == "map.yml":
        return _map_combine(diffs)
    if name == "mainstatic.yml":
        return _mainstatic_combine(diffs)
    if name == "dstatic.yml":
        return _dstatic_combine(diffs)
    if name == "quests.yml":
        return _flat(diffs)                  # merged against the game's list, which apply has
    raise LogError(f"{name} isn't a log this module merges")


_RSTB_CACHE: dict[str, object] = {}


def _game_rstb(game: str):
    p = os.path.join(game, "romfs", *bnp.rm.RSTB.split("/"))
    try:
        stamp = (p, os.path.getmtime(p))
    except OSError:
        return None
    if stamp not in _RSTB_CACHE:
        _RSTB_CACHE.clear()
        _RSTB_CACHE[stamp] = bnp.Rstb(bnp._read(p))
    return _RSTB_CACHE[stamp]


class _GameFiles:
    """The game's files by target ("Pack/TitleBG.pack//Map/..." or a loose RomFS path), packs read once."""

    def __init__(self, game: str):
        self.romfs = os.path.join(game, "romfs")
        self.packs: dict[str, tuple[bytes, object] | None] = {}

    def get(self, target: str) -> bytes | None:
        outer, _, inner = target.partition("//")
        path = os.path.join(self.romfs, *outer.split("/"))
        if not inner:
            return bnp._read(path) if os.path.isfile(path) else None
        if outer not in self.packs:
            self.packs[outer] = None
            if os.path.isfile(path):
                raw = unyaz0(bnp._read(path))
                try:
                    self.packs[outer] = (raw, wc.sarc_parse(raw))
                except wc.FormatError:
                    raise LogError(f"the game's {outer} can't be read") from None
        pack = self.packs[outer]
        if pack is None:
            return None
        raw, s = pack
        node = next((n for n in s.nodes if n.name == inner), None)
        return raw[s.data_offset + node.start:s.data_offset + node.end] if node else None


def apply(name: str, diff, game: str, read=None) -> dict:
    """A combined change applied to the game's files. game: Savage Wind's extracted folder (only read). read: an
    optional function target -> bytes or None giving the copy to merge into instead of the game's (a mod's own
    TitleBG.pack file, say). Returns {"files": {target: bytes}, "sizes": {size table name: size}, "notes": [...]}:
    files only for what changed, Yaz0 as the game's are; sizes for the size table, never below the game's entry."""
    name = name.lower()
    if name not in LOGS:
        raise LogError(f"{name} isn't a log this module merges")
    gf = _GameFiles(game)
    rstb = _game_rstb(game)
    files: dict[str, bytes] = {}
    sizes: dict[str, int] = {}
    notes: list[str] = []
    empty = not diff if name != "map.yml" else not any(
        any(s.get(k) for s in u.values() for k in ("add", "mod", "del")) for u in diff.values())
    if empty:
        return {"files": files, "sizes": sizes, "notes": notes}
    if name != "quests.yml" and bnp._aoc_dirs(game):
        notes.append(f"{name} ({LOGS[name]}) not merged: your game has the DLC, and DLC maps aren't merged yet")
        return {"files": files, "sizes": sizes, "notes": notes}

    def base(target: str) -> tuple[bytes | None, bytes | None]:
        vanilla = gf.get(target)
        own = read(target) if read else None
        return (own if own is not None else vanilla), vanilla

    def put(target: str, new: bytes, vanilla: bytes | None, pack: bool) -> None:
        files[target] = new
        if rstb is None or vanilla is None:
            return
        inner = target.split("//")[-1]
        key = bnp.canon(inner)
        g = rstb.get(key)
        if g is not None:
            need = bnp.needed_size(g, ukmm.unpacked_size(vanilla), ukmm.unpacked_size(new), pack)
            if need != g:
                sizes[key] = need

    if name == "map.yml":
        units, vanillas = {}, {}
        for unit in diff:
            units[unit], vanillas[unit] = base(map_target(unit))
        out, notes = _merge_map({u: d for u, d in units.items() if d is not None}, diff)
        for unit, data in out.items():
            put(map_target(unit), data, vanillas[unit], True)
    else:
        target, fn, pack = {"mainstatic.yml": (MAINSTATIC, _mainstatic_apply, False),
                            "dstatic.yml": (DSTATIC, _dstatic_apply, False),
                            "quests.yml": (QUESTS, _quests_apply, True)}[name]
        data, vanilla = base(target)
        if data is None:
            notes.append(f"{name} ({LOGS[name]}) not merged: your game has no {target}")
        else:
            new, notes = _merge_bytes(data, target.split("//")[-1], fn, diff, share=name != "quests.yml")
            if new is not data:
                put(target, new, vanilla, pack)
    if files and rstb is None:
        notes.append(f"{name}: the game's size table wasn't found, so no sizes were set for the merged files")
    return {"files": files, "sizes": sizes, "notes": notes}
