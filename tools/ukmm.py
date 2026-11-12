#!/usr/bin/env python3
"""Install UKMM mod packages (.zip with meta.yml and manifest.yml) as Switch mods for Savage Wind.
`tools/modman.py add` uses this.

    python tools/ukmm.py info <package .zip or folder>      name, version, platform, options, files, what can be used
    python tools/ukmm.py dict [ukmm.exe or zsdic file]       where the zstd dictionary comes from

A UKMM package (written by UKMM's own code, which we only read about: github.com/NiceneNerd/UKMM) is a .zip with no
zip compression: meta.yml (name, version, platform, option groups), manifest.yml (the game files it changes:
"content" and "aoc" lists, real names), and one file per changed resource at its canonical path ("Actor/Pack/X.
bactorpack": the ".s" of compressed names dropped, DLC files under "Aoc/0010/"). Options sit in options/<path>/ with
their own manifest.yml. Each resource is zstd-compressed (with a dictionary UKMM carries inside ukmm.exe) CBOR of one
of:
  Binary     the file's bytes (models, textures, sounds...): used as they are;
  Sarc       a pack's list of files (added, removed; the rest come from the game's copy): rebuilt here, every file
             in it built the same way, nested packs too;
  Mergeable  a parsed file, as a diff against the game's (UKMM's own data model, dozens of types). This module
             applies the ones listed in MERGEABLE (actor parameter files: actor links, model lists, animation lists,
             general parameters and other plain parameter files). The others are named ("needs attention"): the
             game's copy is used, or the file is missing when the game has none.

Installing one here: checks the platform (Switch, cross-platform, or Wii U: a Wii U package's plain files are
converted to the Switch's formats first, see WiiuBinaries), picks options (the package's defaults, or the player's),
builds every listed file from the game's own files (extracted\\romfs, only read) and the package's resources (a game
pack nothing of the package's gets into is left as the game's; game data and save data flags merged too), writes a
size table (RSTB) when that changes the game's (grown entries for the files that got bigger; for new files, entries
estimated from the game's files of the same kind nearest in size; a new file nothing can be estimated from is named),
and keeps .ukmm/ukmm.json (what was installed and what wasn't merged) in the library copy. Nothing in the package is
run; names from it are checked; zstd output, nesting, alignments and the install's total size are capped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import struct
import sys
import zipfile
import zlib
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bnp  # noqa: E402
import romfs_mods as rm  # noqa: E402
import wiiu_convert as wc  # noqa: E402
import zstdec  # noqa: E402

TOOL_ID = "botw-mod-loader ukmm"
META = ".ukmm"                        # in the library copy: names starting with '.' are never deployed
BASE_TID, DLC_TID = bnp.BASE_TID, bnp.DLC_TID
AOC = "Aoc/0010/"
MAX_META = 1 << 20
MAX_RESOURCE = 1 << 30                # one decompressed resource
MAX_INSTALL = 8 << 30                 # every file one package's install writes (the game's whole RomFS is ~15 GB)
SPARE_SPACE = 256 << 20               # free disk space an install leaves at least
KIN_FILES = 6000                      # game files looked at in one folder to size a new file of their kind
KIN_NEAR = 8                          # a new file's entry: the largest estimate from this many game files
KIN_MARGIN = 1.25                     # and this much more than that (not for packs, sized exactly): checked
                                      # against the game's own table, leaving each file out in turn, it was
                                      # under the game's entry for 2 of 400 models (0.92 of it at worst), 1 of
                                      # 400 animation lists (0.99) and none of 16 other kinds
MAX_ALIGN = 0x2000                    # the biggest alignment of a file in a pack (Wii U textures; Switch headers ask
                                      # for up to 1 << 13 too)
# UKMM's zstd dictionary (crates/uk-mod/data/zsdic, built into ukmm.exe): found by these, never shipped here
DICT_ID = 0x795F475D
DICT_SIZE = 112640
DICT_SHA256 = "e0410c5721898d834aa172c82b850285b9d08a25134d29356be80e91c75d85fe"
DICT_HEAD = struct.pack("<II", zstdec.DICT_MAGIC, DICT_ID)
DICT_URL = ("https://github.com/NiceneNerd/UKMM/raw/0fdf3ff6f0e66774db2a0fc2a10906a226b2562e/"
            "crates/uk-mod/data/zsdic")
YAZ0_SQUEEZE = 16 << 10               # files up to this are Yaz0-compressed; bigger ones get a stored Yaz0 stream


class UkmmError(Exception):
    """A package that can't be installed. The message is for the player."""


# ---------------------------------------------------------------------------------------------------- YAML (subset)
# meta.yml and manifest.yml as UKMM writes them (serde_yaml): block maps and lists, plain and quoted scalars, block
# scalars (|, >), empty flow collections and tags (!Specific Switch). Not a general YAML reader.

class _Lines:
    def __init__(self, text: str):
        self.lines = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "  ").split("\n")
        self.i = 0

    def peek(self) -> tuple[int, str] | None:
        """(indent, text) of the next line with content, skipping blank lines and comments."""
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            s = raw.strip()
            if not s or s.startswith("#") or s in ("---", "..."):
                self.i += 1
                continue
            return len(raw) - len(raw.lstrip(" ")), s
        return None


def _unquote_double(s: str) -> str:
    out, i = [], 0
    esc = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "/": "/", " ": " ", "a": "\a",
           "b": "\b", "e": "\x1b", "f": "\f", "v": "\v", "N": "\x85", "_": "\xa0", "L": " ", "P": " "}
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n in esc:
                out.append(esc[n])
                i += 2
                continue
            width = {"x": 2, "u": 4, "U": 8}.get(n)
            if width:
                try:
                    out.append(chr(int(s[i + 2:i + 2 + width], 16)))
                    i += 2 + width
                    continue
                except ValueError:
                    pass
        out.append(c)
        i += 1
    return "".join(out)


def _fold(parts: list[str]) -> str:
    """Multi-line flow scalar lines joined as YAML folds them: a line break is a space, an empty line a newline."""
    out = ""
    pending = 0
    for k, p in enumerate(parts):
        if not p:
            pending += 1
            continue
        if k and out:
            out += "\n" * pending if pending else " "
        elif pending and not out:
            out += "\n" * pending
        out += p
        pending = 0
    return out


def _scalar_text(first: str, ls: _Lines, parent: int) -> str:
    """A scalar starting on the current line (`first`, after "key: " or "- "), with its continuation lines."""
    if first[:1] in ("|", ">"):
        m = re.fullmatch(r"([|>])([+-]?)(\d?)([+-]?)\s*(#.*)?", first)
        if not m:
            raise UkmmError(f"YAML block scalar {first!r} isn't understood")
        style, chomp = m.group(1), m.group(2) or m.group(4)
        explicit = int(m.group(3)) if m.group(3) else 0
        lines: list[str] = []
        indent = parent + explicit if explicit else None
        while ls.i < len(ls.lines):
            raw = ls.lines[ls.i]
            if raw.strip():
                ind = len(raw) - len(raw.lstrip(" "))
                if indent is None:
                    if ind <= parent:
                        break
                    indent = ind
                if ind < indent:
                    break
                lines.append(raw[indent:])
            else:
                lines.append("")
            ls.i += 1
        trailing = 0
        while lines and not lines[-1]:
            lines.pop()
            trailing += 1
        if style == "|":
            text = "\n".join(lines)
        else:
            text = ""
            for k, ln in enumerate(lines):
                if k:
                    text += "\n" if (not ln or ln.startswith(" ") or not lines[k - 1]
                                     or lines[k - 1].startswith(" ")) else " "
                text += ln
        if chomp == "-" or not lines:
            return text
        return text + ("\n" * (trailing + 1) if chomp == "+" else "\n")
    if first[:1] in ("'", '"'):
        q = first[0]
        body = first[1:]
        parts: list[str] = []
        while True:
            end = _quote_end(body, q)
            if end >= 0:
                parts.append(body[:end] if not parts else body[:end].strip())
                break
            parts.append(body.rstrip() if not parts else body.strip())
            if ls.i >= len(ls.lines):
                raise UkmmError("YAML quoted text without its closing quote")
            body = ls.lines[ls.i]
            ls.i += 1
        if q == "'":
            return _fold([p.replace("''", "'") for p in parts])
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
        return _unquote_double(text)
    parts = [first.split(" #", 1)[0].rstrip()]
    while True:
        nxt = ls.peek()
        if nxt is None or nxt[0] <= parent:
            break
        ind, s = nxt
        if s.startswith("- ") or s == "-" or re.match(r"^[^'\"\s][^:]*:(\s|$)", s):
            break
        parts.append(s.split(" #", 1)[0].rstrip())
        ls.i += 1
    return _fold(parts)


def _quote_end(s: str, q: str) -> int:
    i = 0
    while i < len(s):
        if q == "'" and s[i] == "'":
            if s[i + 1:i + 2] == "'":
                i += 2
                continue
            return i
        if q == '"':
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == '"':
                return i
        i += 1
    return -1


def _flow(s: str):
    """[], {}, or a simple one-line [a, b] list."""
    s = s.strip()
    if s in ("[]", "{}"):
        return [] if s == "[]" else {}
    if s.startswith("[") and s.endswith("]"):
        return [_plain(x.strip()) for x in s[1:-1].split(",") if x.strip()]
    raise UkmmError(f"YAML flow value {s!r} isn't understood")


def _plain(s: str):
    if s in ("null", "~", "Null", "NULL", ""):
        return None
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if s[:1] in ("'", '"'):
        ls = _Lines("")
        return _scalar_text(s, ls, -1)
    return s


def _value(first: str, ls: _Lines, parent: int, depth: int = 0):
    """A value whose text starts on the current line (maybe empty: then it is the block below)."""
    tag = None
    if first.startswith("!"):
        tag, _, first = first[1:].partition(" ")
        first = first.strip()
    if not first:
        nxt = ls.peek()
        if nxt is not None and (nxt[0] > parent or (nxt[0] == parent and (nxt[1].startswith("- ")
                                                                          or nxt[1] == "-"))):
            v = _block(ls, nxt[0], depth + 1)
        else:
            v = None
    elif first[:1] in ("[", "{"):
        v = _flow(first)
    elif first[:1] in ("|", ">", "'", '"'):
        v = _scalar_text(first, ls, parent)
    else:
        text = _scalar_text(first, ls, parent)
        v = _plain(text) if text in _SPECIAL else text
    return {tag: v} if tag else v


_SPECIAL = {"null", "Null", "NULL", "~", "true", "True", "TRUE", "false", "False", "FALSE"}


def _key(s: str) -> tuple[str, str] | None:
    """("key", rest) for a "key: rest" line."""
    if s[:1] in ("'", '"'):
        end = _quote_end(s[1:], s[0])
        if end < 0:
            return None
        key = s[1:end + 1]
        key = key.replace("''", "'") if s[0] == "'" else _unquote_double(key)
        rest = s[end + 2:]
        if not rest.startswith(":"):
            return None
        return key, rest[1:].strip()
    m = re.match(r"^([^:#]+?|\S+?):(?:\s+(.*)|)$", s)
    if not m or s.startswith(("- ", "[", "{")):
        return None
    return m.group(1).strip(), (m.group(2) or "").strip()


YAML_DEPTH = 64                       # meta.yml and manifests nest 4 deep; more is refused, not followed


def _block(ls: _Lines, indent: int, depth: int = 0):
    if depth > YAML_DEPTH:
        raise UkmmError("YAML nests too deep")
    nxt = ls.peek()
    if nxt is None:
        return None
    if nxt[1].startswith("- ") or nxt[1] == "-":
        out: list = []
        while True:
            nxt = ls.peek()
            if nxt is None or nxt[0] != indent or not (nxt[1].startswith("- ") or nxt[1] == "-"):
                break
            ls.i += 1
            rest = nxt[1][1:].strip()
            if rest and not rest.startswith(("!", "[", "{", "|", ">")) and _key(rest) is not None:
                # a map that starts on the "- " line: its keys sit 2 columns in (the line is read again as one)
                inner = indent + 2
                ls.i -= 1
                ls.lines[ls.i] = " " * inner + rest
                out.append(_block(ls, inner, depth + 1))
            else:
                out.append(_value(rest, ls, indent, depth))
        return out
    out_map: dict = {}
    while True:
        nxt = ls.peek()
        if nxt is None or nxt[0] != indent:
            if nxt is not None and nxt[0] > indent:
                raise UkmmError(f"YAML line {ls.i + 1} is indented more than it should be")
            break
        kv = _key(nxt[1])
        if kv is None:
            raise UkmmError(f"YAML line {ls.i + 1} isn't a 'key: value' line")
        ls.i += 1
        out_map[kv[0]] = _value(kv[1], ls, indent, depth)
    return out_map


def yaml_load(text: str):
    """The YAML subset UKMM writes (see above), as dicts, lists, strings, None and booleans."""
    ls = _Lines(text)
    nxt = ls.peek()
    if nxt is None:
        return None
    return _block(ls, nxt[0])


# ---------------------------------------------------------------------------------------------------- CBOR
# What minicbor-ser writes for serde data: structs as maps keyed by field names, enums as {"Variant": value} (unit
# variants as their name), None as null, Vec<u8> as an array of small unsigned ints.

class _Break:
    pass


_BREAK = _Break()


class FrozenMap(dict):
    """A CBOR map used as a map key (UKMM keys save data flags by {name, hash}): it reads as the map, and can be a key
    because it can't change. Its values are frozen too (lists as tuples, maps as FrozenMaps)."""

    def __hash__(self):
        return hash(frozenset(self.items()))

    def _read_only(self, *a, **k):
        raise TypeError("a map used as a key can't change")

    __setitem__ = __delitem__ = update = pop = popitem = clear = setdefault = _read_only


def frozen(v):
    """A CBOR value as a map key: lists become tuples and maps FrozenMaps, all the way down."""
    if isinstance(v, dict):
        return FrozenMap((frozen(k), frozen(x)) for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return tuple(frozen(x) for x in v)
    return v


def cbor_decode(data: bytes, pos: int = 0, depth: int = 0):
    """(value, next position) of the CBOR item at pos."""
    if depth > 200:
        raise UkmmError("resource data nests too deep")
    if pos >= len(data):
        raise UkmmError("resource data cut short")
    ib = data[pos]
    major, ai = ib >> 5, ib & 31
    pos += 1
    if ai < 24:
        arg = ai
    elif ai < 28:
        n = 1 << (ai - 24)
        if pos + n > len(data):
            raise UkmmError("resource data cut short")
        arg = int.from_bytes(data[pos:pos + n], "big")
        if major == 7 and ai >= 25:
            raw = data[pos:pos + n]
            pos += n
            return (struct.unpack(">e", raw)[0] if n == 2 else struct.unpack(">f", raw)[0] if n == 4
                    else struct.unpack(">d", raw)[0]), pos
        pos += n
    elif ai == 31:
        arg = None
    else:
        raise UkmmError("resource data isn't valid CBOR")
    if major == 0:
        return arg, pos
    if major == 1:
        return -1 - arg, pos
    if major in (2, 3):
        if arg is None:
            chunks = []
            while data[pos] != 0xFF:
                c, pos = cbor_decode(data, pos, depth + 1)
                chunks.append(c)
            pos += 1
            out = b"".join(c.encode() if isinstance(c, str) else c for c in chunks)
        else:
            if pos + arg > len(data):
                raise UkmmError("resource data cut short")
            out = data[pos:pos + arg]
            pos += arg
        return (out.decode("utf-8", "replace") if major == 3 else bytes(out)), pos
    if major == 4:
        items = []
        if arg is None:
            while True:
                v, pos = cbor_decode(data, pos, depth + 1)
                if v is _BREAK:
                    break
                items.append(v)
        else:
            if arg > len(data) - pos:
                raise UkmmError("resource data cut short")
            for _ in range(arg):
                v, pos = cbor_decode(data, pos, depth + 1)
                items.append(v)
        return items, pos
    if major == 5:
        out: dict = {}
        k = 0
        while arg is None or k < arg:
            key, pos = cbor_decode(data, pos, depth + 1)
            if key is _BREAK:
                if arg is None:
                    break
                raise UkmmError("resource data isn't valid CBOR")
            v, pos = cbor_decode(data, pos, depth + 1)
            if isinstance(key, (list, dict)):
                key = frozen(key)   # (a list or map as a key: UKMM's save data flags are keyed by {name, hash})
            out[key] = v
            k += 1
        return out, pos
    if major == 6:
        return cbor_decode(data, pos, depth + 1)
    if ai == 31:
        return _BREAK, pos
    return {20: False, 21: True, 22: None, 23: None}.get(arg), pos


def u8_array(data: bytes, pos: int, to_end: bool = False) -> tuple[bytes, int]:
    """A CBOR array of bytes (serde's Vec<u8>: each element a uint, 0x18-prefixed from 24 up), decoded in C-speed
    steps: the pairs "18 18" are the value 24, the other 0x18 bytes are prefixes. to_end: the array must end where
    the data does."""
    ib = data[pos]
    if ib >> 5 != 4:
        raise UkmmError("resource bytes aren't an array")
    ai = ib & 31
    pos += 1
    if ai < 24:
        n = ai
    elif ai < 28:
        k = 1 << (ai - 24)
        n = int.from_bytes(data[pos:pos + k], "big")
        pos += k
    else:
        raise UkmmError("resource bytes have no length")
    seg = data[pos:] if to_end else data[pos:pos + 2 * n]
    parts = seg.split(b"\x18\x18")
    vals = b"\x18".join([p.replace(b"\x18", b"") for p in parts]) if len(parts) > 1 else seg.replace(b"\x18", b"")
    if len(vals) < n:
        raise UkmmError("resource bytes cut short")
    head = vals[:n]
    used = n + n - len(head.translate(None, _HIGH))
    if to_end and used != len(seg):
        raise UkmmError("resource bytes aren't what UKMM writes")
    return head, pos + used


_HIGH = bytes(range(24, 256))


# ---------------------------------------------------------------------------------------------------- dictionary

def dictionary_from(path: str) -> zstdec.Dictionary | None:
    """UKMM's dictionary from a zsdic file or from ukmm.exe (found by its header, checked by its SHA-256)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            data = f.read(min(size, 256 << 20))
    except OSError:
        return None
    at = 0 if data[:8] == DICT_HEAD else data.find(DICT_HEAD)
    while at >= 0:
        blob = data[at:at + DICT_SIZE]
        if len(blob) == DICT_SIZE and hashlib.sha256(blob).hexdigest() == DICT_SHA256:
            return zstdec.Dictionary(blob)
        at = data.find(DICT_HEAD, at + 1)
    return None


def dictionary_places(extra: list[str] = ()) -> list[str]:
    """Where UKMM's dictionary is looked for, in order: the given paths, UKMM_DICT, ukmm on PATH, the usual
    install folders."""
    out = list(extra)
    if os.environ.get("UKMM_DICT"):
        out.append(os.environ["UKMM_DICT"])
    for name in ("ukmm.exe", "ukmm"):
        w = shutil.which(name)
        if w:
            out.append(w)
    for env, sub in (("ProgramW6432", r"ukmm\bin\ukmm.exe"), ("ProgramFiles", r"ukmm\bin\ukmm.exe"),
                     ("ProgramFiles", r"ukmm\ukmm.exe"), ("LOCALAPPDATA", r"Programs\ukmm\ukmm.exe"),
                     ("LOCALAPPDATA", r"ukmm\zsdic")):
        base = os.environ.get(env)
        if base:
            out.append(os.path.join(base, sub))
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def find_dictionary(extra: list[str] = ()) -> tuple[zstdec.Dictionary | None, str | None]:
    for p in dictionary_places(extra):
        if os.path.isfile(p):
            d = dictionary_from(p)
            if d is not None:
                return d, p
    return None, None


def _no_dictionary() -> UkmmError:
    return UkmmError("This UKMM package is compressed with UKMM's dictionary, which is built into UKMM: install UKMM "
                     "(github.com/NiceneNerd/UKMM, its ukmm.exe is read, never run), or save its dictionary file "
                     f"({DICT_URL}) and set UKMM_DICT to it, then add the package again.")


# ---------------------------------------------------------------------------------------------------- the package

def canonical(path: str) -> str:
    """A resource's canonical path, as UKMM names it: Switch and Wii U prefixes taken off, DLC files under Aoc/0010/,
    every ".s" turned into "." (Actor/Pack/X.sbactorpack -> Actor/Pack/X.bactorpack)."""
    c = path.replace("\\", "/")
    for k, v in (("Content/", ""), ("content/", ""), ("atmosphere/titles/", ""), ("atmosphere/contents/", ""),
                 ("01007EF00011E000/romfs/", ""), ("01007ef00011e000/romfs/", ""),
                 ("01007EF00011E001/romfs", "Aoc/0010"), ("01007EF00011E002/romfs", "Aoc/0010"),
                 ("01007EF00011F001/romfs", "Aoc/0010"), ("01007EF00011F002/romfs", "Aoc/0010"),
                 ("01007ef00011e001/romfs", "Aoc/0010"), ("01007ef00011e002/romfs", "Aoc/0010"),
                 ("01007ef00011f001/romfs", "Aoc/0010"), ("01007ef00011f002/romfs", "Aoc/0010"),
                 ("romfs/", ""), ("aoc/content", "Aoc"), ("aoc", "Aoc")):
        if c.startswith(k):
            c = v + c[len(k):]
    return c.replace(".s", ".")


@dataclass
class Option:
    name: str
    path: str
    desc: str = ""
    group: str = ""
    exclusive: bool = False     # a choose-one group
    required: bool = False      # the group needs a choice
    default: bool = False
    requires: list[str] = field(default_factory=list)


@dataclass
class Meta:
    name: str
    version: str
    author: str
    platform: str               # "switch", "wiiu" or "any"
    options: list[Option]
    raw: dict
    api: str = ""
    category: str = ""
    description: str = ""
    prefix: str = ""            # the folder inside the zip or folder that holds meta.yml ("" at the top)
    masters: list[str] = field(default_factory=list)   # mods the package was made on top of (UKMM diffs against them)

    def summary(self) -> list[str]:
        plat = {"switch": "Switch", "wiiu": "Wii U", "any": "any platform"}.get(self.platform, self.platform)
        out = [f"{self.name}" + (f" {self.version}" if self.version else "") + f" ({plat}, UKMM {self.api or '?'})"]
        if self.author:
            out.append(f"  by {self.author}")
        if not self.options:
            out.append("  no options")
        for o in self.options:
            where = f"one of '{o.group}'" if o.exclusive else f"option of '{o.group}'"
            out.append(f"  {where}: {o.name} (folder {o.path or '(none)'})" + (" [default]" if o.default else ""))
        if self.masters:
            out.append(f"  {masters_note(self.masters)}")
        return out


def masters_note(masters: list[str]) -> str:
    return (f"made on top of {_some([repr(m) for m in masters], 3)} (its 'masters'): its packs and parameter files "
            "list only what it changes against that mod, and this loader builds them on the plain game, so what it "
            "relies on from that mod is missing. Install them together in UKMM (Switch mode) and add its output "
            "folder instead")


def _masters(value) -> list[str]:
    """meta.yml's masters: a map of mod names (or a list), empty for most packages."""
    if isinstance(value, dict):
        return [str(k) for k in value]
    if isinstance(value, list):
        return [str(x if not isinstance(x, dict) else x.get("name") or next(iter(x), "?")) for x in value if x]
    return [str(value)] if value not in (None, "", {}, []) else []


def _platform(value) -> str:
    """meta.yml's platform: !Specific Switch, !Specific Wii U, Universal (or the older {Specific: ...} form)."""
    if isinstance(value, dict) and len(value) == 1:
        k, v = next(iter(value.items()))
        if str(k).lower() == "specific":
            v = str(v).lower()
            return "wiiu" if "wii" in v or v == "big" else "switch" if v in ("switch", "little") else v
        value = k
    v = str(value or "").lower()
    if v in ("universal", "agnostic", "any"):
        return "any"
    return "wiiu" if "wii" in v else "switch" if "switch" in v else v


def _opt_path(value, where: str) -> str:
    p = str(value if value is not None else "").replace("\\", "/").strip("/")
    if p and (any(part in ("", ".", "..") for part in p.split("/")) or any(rm._bad_name(x) for x in p.split("/"))
              or re.match(r"^[A-Za-z]:", p)):
        raise UkmmError(f"{where} names an option folder that isn't a plain folder name ({value!r}).")
    return p


def parse_meta(text: str, where: str = "meta.yml") -> Meta:
    try:
        raw = yaml_load(text)
    except UkmmError as e:
        raise UkmmError(f"{where} can't be read ({e}).") from None
    if not isinstance(raw, dict):
        raise UkmmError(f"{where} isn't a UKMM meta file.")
    opts: list[Option] = []
    for g in raw.get("option_groups") or []:
        if not isinstance(g, dict):
            continue
        kind = str(g.get("type") or "")
        if not kind and len(g) == 1:                       # (an older layout: {Exclusive: {...}})
            kind, g = next(iter(g.items()))
            if not isinstance(g, dict):
                continue
        exclusive = kind.lower().startswith("excl")
        gname = str(g.get("name") or "options")
        defaults = {_opt_path(g.get("default"), where)} if exclusive and g.get("default") is not None else {
            _opt_path(d, where) for d in (g.get("defaults") or []) if d is not None}
        for o in g.get("options") or []:
            if not isinstance(o, dict):
                continue
            path = _opt_path(o.get("path"), where)
            opts.append(Option(str(o.get("name") or path or "option"), path, str(o.get("description") or ""),
                               gname, exclusive, bool(g.get("required")), path in defaults,
                               [_opt_path(r, where) for r in (o.get("requires") or []) if r]))
    return Meta(str(raw.get("name") or "").strip(), str(raw.get("version") or "").strip(),
                str(raw.get("author") or "").strip(), _platform(raw.get("platform")), opts, raw,
                str(raw.get("api") or ""), str(raw.get("category") or ""), str(raw.get("description") or ""),
                masters=_masters(raw.get("masters")))


def parse_manifest(text: str, where: str = "manifest.yml") -> tuple[list[str], list[str]]:
    """(content files, DLC files): real names relative to the RomFS and the DLC's RomFS."""
    try:
        raw = yaml_load(text)
    except UkmmError as e:
        raise UkmmError(f"{where} can't be read ({e}).") from None
    if raw is None:
        return [], []
    if not isinstance(raw, dict):
        raise UkmmError(f"{where} isn't a UKMM manifest.")
    out = []
    for key in ("content", "aoc"):
        files = []
        for f in raw.get(key) or []:
            if not isinstance(f, str) or not f.strip():
                continue
            try:
                parts = bnp.check_name(f.strip(), where)
            except bnp.BnpError as e:
                raise UkmmError(str(e)) from None
            files.append("/".join(parts))
        out.append(files)
    return out[0], out[1]


def choose(meta: Meta, picks: list[str] = (), no_defaults: bool = False) -> list[Option]:
    """The options to install, in the package's order: its defaults (none with no_defaults) plus the picked ones (by
    name or folder, any case). Picking one of a choose-one group replaces its default; a required group gets its
    first option when nothing in it is chosen (refused with no_defaults); options an option requires come along
    (refused when that would make two of a choose-one group)."""
    chosen = [] if no_defaults else [o for o in meta.options if o.default]
    for pick in picks:
        want = pick.strip().casefold()
        hit = next((o for o in meta.options if want in (o.name.casefold(), o.path.casefold())), None)
        if hit is None:
            names = ", ".join(f"'{o.name}'" for o in meta.options) or "none"
            raise UkmmError(f"{meta.name or 'The package'} has no option '{pick}'. Its options: {names}.")
        if hit.exclusive:
            chosen = [o for o in chosen if not (o.exclusive and o.group == hit.group)]
        if hit not in chosen:
            chosen.append(hit)
    groups: dict[str, bool] = {}
    for o in meta.options:
        groups.setdefault(o.group, o.required)
    for g, required in groups.items():
        if required and not any(o.group == g for o in chosen):
            if no_defaults:
                raise UkmmError(f"{meta.name or 'The package'} needs an option of '{g}': pick it with --option.")
            chosen.append(next(o for o in meta.options if o.group == g))
    by_path = {o.path.casefold(): o for o in meta.options}
    todo = list(chosen)
    while todo:
        need = todo.pop()
        for r in need.requires:
            o = by_path.get(r.casefold())
            if o is not None and o not in chosen:
                other = next((x for x in chosen if o.exclusive and x.exclusive and x.group == o.group), None)
                if other is not None:     # (it would install two options of a choose-one group)
                    raise UkmmError(f"{meta.name or 'The package'}: '{need.name}' needs '{o.name}', but '{other.name}' "
                                    f"is chosen in '{o.group}', where only one can be: pick '{o.name}' with "
                                    f"--option, or leave '{need.name}' out.")
                chosen.append(o)
                todo.append(o)
    return [o for o in meta.options if o in chosen]


class Package:
    """A UKMM package: a .zip (read in place, nothing extracted) or a folder holding one's files."""

    def __init__(self, src: str):
        self.src = src
        self.zip: zipfile.ZipFile | None = None
        self.prefix = ""
        if os.path.isdir(src):
            self.files = {rel: path for rel, path in bnp._walk(src).items()}
        else:
            try:
                self.zip = zipfile.ZipFile(src)
            except (zipfile.BadZipFile, OSError) as e:
                raise UkmmError(f"{os.path.basename(src)} can't be read as a .zip ({e}).") from None
            self.files = {}
            for info in self.zip.infolist():
                if info.is_dir():
                    continue
                if info.flag_bits & 1:
                    raise UkmmError(f"{os.path.basename(src)} is password-protected: not added.")
                self.files[info.filename.replace("\\", "/")] = info
        low = {k.lower(): k for k in self.files}
        if "meta.yml" not in low:
            tops = {k.split("/", 1)[0] for k in self.files if "/" in k}
            if len(tops) == 1 and not any("/" not in k for k in self.files):
                top = tops.pop()
                if f"{top.lower()}/meta.yml" in low:
                    self.prefix = top + "/"
        self.low = low

    def close(self) -> None:
        if self.zip is not None:
            self.zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def name(self, rel: str) -> str | None:
        """The member's name as stored, for a path relative to the package (exact first, then any case)."""
        full = self.prefix + rel
        if full in self.files:
            return full
        return self.low.get(full.lower())

    def has(self, rel: str) -> bool:
        return self.name(rel) is not None

    def read(self, rel: str, limit: int = MAX_RESOURCE) -> bytes:
        n = self.name(rel)
        if n is None:
            raise UkmmError(f"{os.path.basename(self.src)} has no {rel}.")
        entry = self.files[n]
        if self.zip is not None:
            if entry.file_size > limit:
                raise UkmmError(f"{rel} in {os.path.basename(self.src)} is too big.")
            try:
                with self.zip.open(entry) as f:
                    data = f.read(limit + 1)
            except (zipfile.BadZipFile, NotImplementedError, zlib.error, OSError) as e:
                raise UkmmError(f"{rel} can't be read from {os.path.basename(self.src)} ({e}).") from None
        else:
            size = os.path.getsize(entry)
            if size > limit:
                raise UkmmError(f"{rel} in {os.path.basename(self.src)} is too big.")
            with open(entry, "rb") as f:
                data = f.read(size + 1)       # (not limit + 1: a buffer that big is made up front)
        if len(data) > limit:
            raise UkmmError(f"{rel} in {os.path.basename(self.src)} is too big.")
        return data

    def size(self) -> int:
        """The bytes of every member (as stored)."""
        if self.zip is not None:
            return sum(i.file_size for i in self.files.values())
        total = 0
        for path in self.files.values():
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
        return total

    def head(self, rel: str, n: int) -> bytes:
        """The first n bytes of a member (enough to tell what a resource is)."""
        entry = self.files[self.name(rel)]
        try:
            if self.zip is not None:
                with self.zip.open(entry) as f:
                    return f.read(n)
            with open(entry, "rb") as f:
                return f.read(n)
        except (zipfile.BadZipFile, NotImplementedError, zlib.error, OSError) as e:
            raise UkmmError(f"{rel} can't be read from {os.path.basename(self.src)} ({e}).") from None

    def text(self, rel: str) -> str:
        return self.read(rel, MAX_META).decode("utf-8-sig", "replace")


def is_package(names: list[str]) -> bool:
    low = {n.replace("\\", "/").lower() for n in names}
    if "meta.yml" in low and "manifest.yml" in low:
        return True
    tops = {n.split("/", 1)[0] for n in low if "/" in n}
    return len(tops) == 1 and any(n.endswith("/meta.yml") and n.count("/") == 1 for n in low) and any(
        n.endswith("/manifest.yml") and n.count("/") == 1 for n in low)


def package_kind(src: str, findings: list = (), names: list[str] = ()) -> str | None:
    """What modman add installs with this module: "zip" or "folder" (a UKMM package: mod_inspect's UKMM finding, or
    meta.yml and manifest.yml at its top), else None. Reads only."""
    found = any(getattr(f, "kind", "") == "UKMM package" for f in findings)
    if not found:
        if names:
            found = is_package(list(names))
        elif os.path.isdir(src):
            found = os.path.isfile(os.path.join(src, "meta.yml")) and os.path.isfile(os.path.join(src, "manifest.yml"))
        elif zipfile.is_zipfile(src):
            try:
                with zipfile.ZipFile(src) as z:
                    found = is_package(z.namelist())
            except (zipfile.BadZipFile, OSError):
                found = False
    if not found:
        return None
    return "folder" if os.path.isdir(src) else "zip" if os.path.isfile(src) and zipfile.is_zipfile(src) else None


def peek(src: str) -> Meta:
    """The package's meta.yml (nothing is extracted)."""
    with Package(src) as pkg:
        if not pkg.has("meta.yml"):
            raise UkmmError(f"Not added: {os.path.basename(src)} has no UKMM meta.yml.")
        meta = parse_meta(pkg.text("meta.yml"), f"{os.path.basename(src)}: meta.yml")
        meta.prefix = pkg.prefix
        return meta


# ---------------------------------------------------------------------------------------------------- resources

@dataclass
class Res:
    kind: str                   # "binary", "sarc" or "merge"
    data: bytes = b""           # binary: the file's bytes (never Yaz0)
    files: dict | None = None   # sarc: file name -> removed?
    align: int = 0              # sarc: its smallest file alignment
    mtype: str = ""             # merge: UKMM's type name
    value: object = None        # merge: the CBOR value


def dict_id(raw: bytes) -> int:
    """The zstd dictionary a stored resource's frame names (0: none, or not a frame; a damaged frame header is
    found out when the resource is read)."""
    if raw[:4] != struct.pack("<I", zstdec.MAGIC):
        return 0
    try:
        return zstdec.frame_info(raw)[0]
    except zstdec.ZstdError:
        return 0


def load_resource(raw: bytes, dictionary: zstdec.Dictionary | None, where: str) -> Res:
    """One stored resource: zstd undone (UKMM's dictionary when the frame names it), its CBOR read."""
    if raw[:4] == struct.pack("<I", zstdec.MAGIC):
        try:
            did, _ = zstdec.frame_info(raw)
            if did and (dictionary is None or dictionary.id != did):
                raise _no_dictionary()
            data = zstdec.decompress(raw, dictionary if did else None, MAX_RESOURCE)
        except zstdec.ZstdError as e:
            raise UkmmError(f"{where} can't be decompressed ({e}): the package may be damaged.") from None
    else:
        data = raw                       # (a package unzipped by UKMM's own "unzip": already decompressed)
    try:
        if not data or data[0] != 0xA1:
            raise UkmmError("not a UKMM resource")
        key, p = cbor_decode(data, 1)
        if key == "Binary":
            blob, _ = u8_array(data, p, to_end=True)
            return Res("binary", data=blob)
        value, end = cbor_decode(data, p)
        if end != len(data):
            raise UkmmError("data after the resource")
        if key == "Sarc" and isinstance(value, dict):
            files = value.get("files") or {}
            if not isinstance(files, dict):
                raise UkmmError("a pack without its file list")
            align = value.get("alignment") or 0
            if not isinstance(align, int) or align < 0 or align > MAX_ALIGN or align & (align - 1):
                raise UkmmError(f"a pack whose files are aligned to {align!r} bytes (a power of two up to "
                                f"{MAX_ALIGN:#x} is expected)")
            return Res("sarc", files={str(k): bool(v) for k, v in files.items()}, align=align)
        if key == "Mergeable" and isinstance(value, dict) and len(value) == 1:
            mtype, inner = next(iter(value.items()))
            if mtype == "BinaryOverride":   # (UKMM couldn't parse it: stored as it was, overriding)
                if not isinstance(inner, list) or not inner:
                    raise UkmmError("an override without its bytes")
                return Res("binary", data=bytes(inner[0]))
            return Res("merge", mtype=str(mtype), value=inner)
        raise UnknownResource(f"unknown resource kind {key!r}")
    except UnknownResource as e:
        raise UnknownResource(f"{where} can't be read ({e}): made by a UKMM version this loader doesn't know?") \
            from None
    except UkmmError as e:
        if "dictionary" in str(e):
            raise
        raise UkmmError(f"{where} can't be read ({e}): made by a UKMM version this loader doesn't know?") from None
    except (IndexError, ValueError, TypeError, struct.error) as e:
        raise UnknownResource(f"{where} can't be read ({e}): made by a UKMM version this loader doesn't know?") \
            from None


class UnknownResource(UkmmError):
    """A resource in a form this loader doesn't read (a kind it doesn't know, or data its decoder can't hold): named
    and left out, the rest of the package installs. Damaged data (cut short, out-of-range values, a damaged zstd
    frame) is a plain UkmmError: the whole package is refused."""


def resource_kind(raw: bytes, dictionary: zstdec.Dictionary | None) -> str:
    """"binary", "sarc", "merge:<type>" or "unreadable", from the start of a stored resource only."""
    try:
        if raw[:4] == struct.pack("<I", zstdec.MAGIC):
            did, _ = zstdec.frame_info(raw)
            head = zstdec.decompress_head(raw, dictionary if did else None, 160)
        else:
            head = raw[:160]
        if head[:1] != b"\xa1":
            return "unreadable"
        key, p = cbor_decode(head, 1)
        if key == "Mergeable" and head[p:p + 1] == b"\xa1":
            mtype, _ = cbor_decode(head, p + 1)
            return "binary" if mtype == "BinaryOverride" else f"merge:{mtype}"
        return {"Binary": "binary", "Sarc": "sarc"}.get(key, "unreadable")
    except (zstdec.ZstdError, UkmmError, IndexError, ValueError, struct.error):
        return "unreadable"


# ------------------------------------------------------------------------------------- parameter files (AAMP)
# UKMM keeps parameter files as roead's ParameterIO (serde form: objects and lists keyed by name hashes, parameters
# as {"Type": value}) or as its own structs built from them. They are turned into bnp's AAMP tree here, merged the way
# UKMM merges them, and written with bnp's AAMP writer.

AAMP_TYPES = {"Bool": 0, "F32": 1, "I32": 2, "Vec2": 3, "Vec3": 4, "Vec4": 5, "Color": 6, "String32": 7,
              "String64": 8, "Curve1": 9, "Curve2": 10, "Curve3": 11, "Curve4": 12, "BufferInt": 13,
              "BufferF32": 14, "String256": 15, "Quat": 16, "U32": 17, "BufferU32": 18, "BufferBinary": 19,
              "StringRef": 20}
VEC_FIELDS = {3: "xy", 4: "xyz", 5: "xyzt", 6: "rgba", 16: "abcd"}
STRING_TYPES = {7, 8, 15, 20}


def h(name: str) -> int:
    return zlib.crc32(name.encode())


def param_from(v) -> tuple[int, bytes]:
    """A parameter from its serde form ({"F32": 1.0}) to bnp's (type, value bytes)."""
    if not isinstance(v, dict) or len(v) != 1:
        raise UkmmError(f"a parameter UKMM wrote in a form this loader doesn't know ({v!r:.60})")
    name, x = next(iter(v.items()))
    t = AAMP_TYPES.get(name)
    if t is None:
        raise UkmmError(f"parameter type {name!r} isn't known")
    if t == 0:
        return t, struct.pack("<I", 1 if x else 0)
    if t == 1:
        return t, struct.pack("<f", x)
    if t == 2:
        return t, struct.pack("<i", x)
    if t == 17:
        return t, struct.pack("<I", x)
    if t in VEC_FIELDS:
        return t, struct.pack(f"<{len(VEC_FIELDS[t])}f", *(x[c] for c in VEC_FIELDS[t]))
    if t in STRING_TYPES:
        return t, str(x).encode()
    if 9 <= t <= 12:
        if not isinstance(x, list):
            raise UkmmError(f"a {name} parameter that isn't a list")
        return t, b"".join(struct.pack("<II30f", c["a"], c["b"], *c["floats"]) for c in x)
    if t == 13:
        return t, struct.pack(f"<{len(x)}i", *x)
    if t == 14:
        return t, struct.pack(f"<{len(x)}f", *x)
    if t == 18:
        return t, struct.pack(f"<{len(x)}I", *x)
    if isinstance(x, (bytes, bytearray)):
        return t, bytes(x)
    if isinstance(x, list):
        return t, bytes(x)                # (serde's Vec<u8>; a value over 255 is a ValueError: can't be merged)
    if isinstance(x, str):
        return t, x.encode()
    raise UkmmError(f"a {name} parameter that isn't bytes ({type(x).__name__})")


def pobj_from(v) -> bnp.PObj:
    if not isinstance(v, dict):
        raise UkmmError("a parameter object UKMM wrote in a form this loader doesn't know")
    return bnp.PObj({int(k): param_from(p) for k, p in v.items()})


def plist_from(v) -> bnp.PList:
    if not isinstance(v, dict):
        raise UkmmError("a parameter list UKMM wrote in a form this loader doesn't know")
    return bnp.PList({int(k): plist_from(x) for k, x in (v.get("lists") or {}).items()},
                     {int(k): pobj_from(x) for k, x in (v.get("objects") or {}).items()})


def pio_from(v) -> bnp.Pio:
    if not isinstance(v, dict) or "param_root" not in v:
        raise UkmmError("a parameter file UKMM wrote in a form this loader doesn't know")
    return bnp.Pio(int(v.get("version") or 0), 3, str(v.get("data_type") or "xml").encode() + b"\0",
                   bnp.PARAM_ROOT, plist_from(v["param_root"]))


def _text(p: tuple[int, bytes] | None) -> str | None:
    return p[1].decode("utf-8", "replace") if p and p[0] in STRING_TYPES else None


def _str_param(t: int, s: str) -> tuple[int, bytes]:
    return t, s.encode()


def _delete_map(v) -> dict:
    """UKMM's DeleteMap in serde form ({key: [value, removed]}) -> {key: (value, removed)}, in order."""
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise UkmmError("a keyed list UKMM wrote in a form this loader doesn't know")
    out = {}
    for k, pair in v.items():
        if not isinstance(pair, list) or len(pair) != 2:
            raise UkmmError("a keyed list entry UKMM wrote in a form this loader doesn't know")
        out[k] = (pair[0], bool(pair[1]))
    return out


def merge_delete_map(base: dict, diff: dict) -> dict:
    """UKMM's DeleteMap merge: the base's entries, then the diff's (replacing or added, in order), removed ones
    dropped. Values are (value, removed)."""
    out = dict(base)
    for k, v in diff.items():
        out[k] = v
    return {k: (v, False) for k, (v, removed) in out.items() if not removed}


def merge_delete_set(base: dict, diff: dict) -> dict:
    """UKMM's DeleteSet merge: the base's items the diff doesn't name, then the diff's, removed ones dropped."""
    out = {k: False for k, removed in base.items() if not removed and k not in diff}
    out.update(diff)
    return {k: False for k, removed in out.items() if not removed}


def merge_delete_vec(base: list, diff: list) -> list:
    """UKMM's DeleteVec merge: the base's items the diff doesn't remove, then each of the diff's kept items not there
    yet inserted at its own index. Items are (value, removed)."""
    removed = [v for v, r in diff if r]
    out = [v for v, r in base if not r and not any(v == x for x in removed)]
    for idx, v in enumerate(x for x, r in diff if not r):
        if not any(v == y for y in out):
            out.insert(idx, v)
    return [(v, False) for v in out]


class AampFile:
    """A plain parameter file (GenericAamp, GeneralParamList, UMii, DamageParam...): deep merge, as BCML does."""

    def parse(self, data: bytes):
        return bnp.aamp_read(data)

    def load(self, v):
        return pio_from(v)

    def merge(self, base, diff):
        bnp.aamp_merge(base.root, diff.root)
        return base

    def write(self, model) -> bytes:
        return bnp.aamp_write(model)


def _pio(objs: dict | None = None, lists: dict | None = None) -> bnp.Pio:
    return bnp.Pio(0, 3, b"xml\0", bnp.PARAM_ROOT, bnp.PList(dict(lists or {}), dict(objs or {})))


class ActorLinkFile:
    """Actor links (.bxml): link targets merged by name, tags added and removed."""
    FIT_TAGS = 1115720914

    def parse(self, data: bytes):
        root = bnp.aamp_read(data).root
        target = root.objs.get(h("LinkTarget"))
        if target is None:
            raise UkmmError("an actor link without link targets")
        tags = root.objs.get(h("Tags"))
        fit = root.objs.get(self.FIT_TAGS)
        return {"targets": dict(target.params),
                "tags": None if tags is None else {_text(p): False for p in tags.params.values() if _text(p)},
                "fit_tags": None if fit is None else {_text(p): False for p in fit.params.values() if _text(p)}}

    def load(self, v):
        tags, fit = v.get("tags"), v.get("fit_tags")
        return {"targets": dict(pobj_from(v.get("targets") or {}).params),
                "tags": None if tags is None else {str(k): bool(x) for k, x in tags.items()},
                "fit_tags": None if fit is None else {str(k): bool(x) for k, x in fit.items()}}

    def merge(self, base, diff):
        out = {"targets": {**base["targets"], **diff["targets"]}}
        for key in ("tags", "fit_tags"):
            b, d = base[key], diff[key]
            out[key] = merge_delete_set(b, d) if b is not None and d is not None else b if d is None else d
        return out

    def write(self, m) -> bytes:
        objs = {h("LinkTarget"): bnp.PObj(dict(m["targets"]))}
        for key, crc in (("tags", h("Tags")), ("fit_tags", self.FIT_TAGS)):
            if m[key] is not None:
                live = [t for t, removed in m[key].items() if not removed]
                objs[crc] = bnp.PObj({h(f"Tag{i}"): _str_param(20, t) for i, t in enumerate(live)})
        return bnp.aamp_write(_pio(objs))


class ASListFile:
    """Animation lists (.baslist): animation archives (AddReses) and sequences (ASDefines) added or replaced by name,
    blend rules (CFDefines) by their CFPre name, Common replaced."""

    def parse(self, data: bytes):
        root = bnp.aamp_read(data).root
        adds = root.lists.get(h("AddReses"))
        defs = root.lists.get(h("ASDefines"))
        if adds is None or defs is None:
            raise UkmmError("an animation list without AddReses or ASDefines")
        add_reses = {}
        for obj in adds.objs.values():
            anim = _text(obj.params.get(h("Anim")))
            if anim is None:
                raise UkmmError("an animation list entry without its Anim")
            model = obj.params.get(h("RetargetModel"))
            nc = obj.params.get(h("RetargetNoCorrect"))
            add_reses[anim] = ({"anim": anim, "retarget_model": _text(model),
                                "retarget_nocorrect": None if nc is None else bool(struct.unpack("<I", nc[1])[0])},
                               False)
        as_defines = {}
        for obj in defs.objs.values():
            name, fn = _text(obj.params.get(h("Name"))), _text(obj.params.get(h("Filename")))
            if name is None or fn is None:
                raise UkmmError("an animation list ASDefine without its Name or Filename")
            as_defines[name] = (fn, False)
        cf = root.lists.get(h("CFDefines"))
        cf_defines = None
        if cf is not None:
            cf_defines = {}
            for pl in cf.lists.values():
                pre = pl.objs.get(h("CFPre"))
                name = _text(pre.params.get(h("Name"))) if pre else None
                if name is None:
                    raise UkmmError("an animation list CFDefine without its CFPre name")
                cf_defines[name] = (pl, False)
        common = root.objs.get(h("Common"))
        return {"common": common, "add_reses": add_reses, "as_defines": as_defines, "cf_defines": cf_defines}

    def load(self, v):
        common = v.get("common")
        cf = v.get("cf_defines")
        return {"common": None if common is None else pobj_from(common),
                "add_reses": {str(k): (dict(x), r) for k, (x, r) in _delete_map(v.get("add_reses")).items()},
                "as_defines": {str(k): (str(x), r) for k, (x, r) in _delete_map(v.get("as_defines")).items()},
                "cf_defines": None if cf is None else {str(k): (plist_from(x), r)
                                                       for k, (x, r) in _delete_map(cf).items()}}

    def merge(self, base, diff):
        cf_b, cf_d = base["cf_defines"], diff["cf_defines"]
        return {"common": diff["common"] if diff["common"] is not None else base["common"],
                "add_reses": merge_delete_map(base["add_reses"], diff["add_reses"]),
                "as_defines": merge_delete_map(base["as_defines"], diff["as_defines"]),
                "cf_defines": cf_b if cf_d is None else merge_delete_map(cf_b, cf_d) if cf_b is not None else cf_d}

    def write(self, m) -> bytes:
        adds = {}
        for i, (res, _) in enumerate(x for x in m["add_reses"].values() if not x[1]):
            params = {h("Anim"): _str_param(8, res["anim"])}
            if res.get("retarget_model") is not None:
                params[h("RetargetModel")] = _str_param(8, res["retarget_model"])
            if res.get("retarget_nocorrect") is not None:
                params[h("RetargetNoCorrect")] = (0, struct.pack("<I", 1 if res["retarget_nocorrect"] else 0))
            adds[h(f"AddRes_{i}")] = bnp.PObj(params)
        defs = {h(f"ASDefine_{i}"): bnp.PObj({h("Name"): _str_param(8, name), h("Filename"): _str_param(8, fn)})
                for i, (name, (fn, _)) in enumerate((k, x) for k, x in m["as_defines"].items() if not x[1])}
        lists = {h("AddReses"): bnp.PList({}, adds), h("ASDefines"): bnp.PList({}, defs)}
        if m["cf_defines"] is not None:
            lists[h("CFDefines")] = bnp.PList({h(f"CFDefine_{i}"): pl for i, (pl, _) in
                                               enumerate(x for x in m["cf_defines"].values() if not x[1])}, {})
        objs = {h("Common"): m["common"]} if m["common"] is not None else {}
        return bnp.aamp_write(_pio(objs, lists))


class ModelListFile:
    """Model lists (.bmodellist): controller and attention parameters merged, model data by index (units added and
    removed by name), animation targets by index, locators added and removed."""

    def parse(self, data: bytes):
        root = bnp.aamp_read(data).root
        ci, att = root.objs.get(h("ControllerInfo")), root.objs.get(h("Attention"))
        md, at = root.lists.get(h("ModelData")), root.lists.get(h("AnmTarget"))
        if ci is None or att is None or md is None or at is None:
            raise UkmmError("a model list without ControllerInfo, Attention, ModelData or AnmTarget")
        model_data = {}
        for i, pl in enumerate(md.lists.values()):
            base = pl.objs.get(h("Base"))
            folder = _text(base.params.get(h("Folder"))) if base else None
            if folder is None:
                raise UkmmError("model data without its Folder")
            units = {}
            ul = pl.lists.get(h("Unit"))
            for obj in (ul.objs.values() if ul else []):
                name, bone = _text(obj.params.get(h("UnitName"))), _text(obj.params.get(h("BindBone")))
                if name is None or bone is None:
                    raise UkmmError("a model unit without its UnitName or BindBone")
                units[name] = (bone, False)
            model_data[i] = {"folder": folder, "units": units}
        locators = []
        i = 0
        while h(f"Locator_{i}") in root.objs:
            locators.append((root.objs[h(f"Locator_{i}")], False))
            i += 1
        return {"controller_info": dict(ci.params), "attention": dict(att.params), "model_data": model_data,
                "anm_target": dict(enumerate(at.lists.values())), "locators": locators}

    def load(self, v):
        model_data = {}
        for k, d in (v.get("model_data") or {}).items():
            model_data[int(k)] = {"folder": str(d.get("folder") or ""),
                                  "units": {str(n): (str(b), r) for n, (b, r) in _delete_map(d.get("units")).items()}}
        locs = []
        for pair in v.get("locators") or []:
            if not isinstance(pair, list) or len(pair) != 2:
                raise UkmmError("a model list locator UKMM wrote in a form this loader doesn't know")
            locs.append((pobj_from(pair[0]), bool(pair[1])))
        return {"controller_info": dict(pobj_from(v.get("controller_info") or {}).params),
                "attention": dict(pobj_from(v.get("attention") or {}).params), "model_data": model_data,
                "anm_target": {int(k): plist_from(x) for k, x in (v.get("anm_target") or {}).items()},
                "locators": locs}

    def merge(self, base, diff):
        md = {}
        for i in sorted(set(base["model_data"]) | set(diff["model_data"])):
            b, d = base["model_data"].get(i), diff["model_data"].get(i)
            md[i] = {"folder": d["folder"], "units": merge_delete_map(b["units"], d["units"])} if b and d else (d or b)
        return {"controller_info": {**base["controller_info"], **diff["controller_info"]},
                "attention": {**base["attention"], **diff["attention"]}, "model_data": md,
                "anm_target": {**base["anm_target"], **diff["anm_target"]},
                "locators": merge_delete_vec(base["locators"], diff["locators"])}

    def write(self, m) -> bytes:
        objs = {h("ControllerInfo"): bnp.PObj(dict(m["controller_info"])),
                h("Attention"): bnp.PObj(dict(m["attention"]))}
        for i, (obj, _) in enumerate(x for x in m["locators"] if not x[1]):
            objs[h(f"Locator_{i}")] = obj
        md = {}
        for i in sorted(m["model_data"]):
            d = m["model_data"][i]
            pl = bnp.PList({}, {h("Base"): bnp.PObj({h("Folder"): _str_param(8, d["folder"])})})
            live = [(n, b) for n, (b, r) in d["units"].items() if not r]
            if live:
                pl.lists[h("Unit")] = bnp.PList({}, {h(f"Unit_{k}"): bnp.PObj({h("UnitName"): _str_param(8, n),
                                                                             h("BindBone"): _str_param(8, b)})
                                                     for k, (n, b) in enumerate(live)})
            md[h(f"ModelData_{i}")] = pl
        at = {h(f"AnmTarget_{i}"): m["anm_target"][i] for i in sorted(m["anm_target"])}
        return bnp.aamp_write(_pio(objs, {h("ModelData"): bnp.PList(md, {}), h("AnmTarget"): bnp.PList(at, {})}))


# ------------------------------------------------------------------------------------- game data and save data
# UKMM's GameDataPack (Bootup.pack//GameData/gamedata.ssarc) and SaveDataPack (//GameData/savedataformat.ssarc) diffs:
# flags added or changed are merged into the game's files; a flag the diff deletes is kept and named (UKMM diffs a
# mod against the game version its maker had, so a delete of a game flag is most often that version's difference,
# and a missing flag the game's code reads is worse than a spare one).

BYML_TAGS = {"Bool": bool, "I32": int, "Float": float, "String": str}


def byml_from(v, depth: int = 0):
    """roead's Byml in serde form ({"I32": 0}, {"Array": [...]}, "Null"...) -> tools/bcml_data.py's BYML values."""
    import bcml_data
    if depth > 64:
        raise UkmmError("BYML data nests too deep")
    if v is None or v == "Null":
        return None
    if not isinstance(v, dict) or len(v) != 1:
        raise UkmmError("BYML data UKMM wrote in a form this loader doesn't know")
    tag, x = next(iter(v.items()))
    if tag in BYML_TAGS and isinstance(x, BYML_TAGS[tag]) and not (tag != "Bool" and isinstance(x, bool)):
        return bcml_data.f32(x) if tag == "Float" else x
    if tag == "Float" and isinstance(x, int) and not isinstance(x, bool):
        return bcml_data.f32(float(x))
    if tag in ("U32", "I64", "U64") and isinstance(x, int) and not isinstance(x, bool):
        return {"U32": bcml_data.U32, "I64": bcml_data.I64, "U64": bcml_data.U64}[tag](x)
    if tag == "Double" and isinstance(x, (int, float)) and not isinstance(x, bool):
        return bcml_data.F64(x)
    if tag == "Array" and isinstance(x, list):
        return [byml_from(i, depth + 1) for i in x]
    if tag in ("Map", "HashMap", "ValueHashMap") and isinstance(x, dict):
        return {str(k): byml_from(i, depth + 1) for k, i in x.items()}
    if tag in ("BinaryData", "FileData") and isinstance(x, (list, bytes)):
        return bytes(x)
    raise UkmmError(f"BYML data of a kind this loader doesn't know ({tag})")


FLAG_FIELDS = (("category", "Category"), ("data_name", "DataName"), ("delete_rev", "DeleteRev"),
               ("hash_value", "HashValue"), ("init_value", "InitValue"), ("is_event_associated", "IsEventAssociated"),
               ("is_one_trigger", "IsOneTrigger"), ("is_program_readable", "IsProgramReadable"),
               ("is_program_writable", "IsProgramWritable"), ("is_save", "IsSave"), ("max_value", "MaxValue"),
               ("min_value", "MinValue"), ("reset_type", "ResetType"))
BYML_FIELDS = {"init_value", "max_value", "min_value"}


class GameDataFile:
    """Game data flags (UKMM's GameDataPack): turned into a gamedata.yml change and merged by tools/bcml_data.py."""

    def __init__(self):
        self.notes: list[str] = []

    def parse(self, data: bytes):
        return data

    def load(self, v):
        if not isinstance(v, dict):
            raise UkmmError("game data UKMM wrote in a form this loader doesn't know")
        diff: dict = {}
        kept: list[str] = []
        for part in v.values():
            if not isinstance(part, dict):
                raise UkmmError("game data UKMM wrote in a form this loader doesn't know")
            t = str(part.get("data_type") or "")
            for name, (flag, removed) in _delete_map(part.get("flags")).items():
                if removed:
                    kept.append(str(name))
                    continue
                if not isinstance(flag, dict):
                    raise UkmmError("a game data flag UKMM wrote in a form this loader doesn't know")
                entry = {}
                for src, dst in FLAG_FIELDS:
                    if src not in flag or (src == "category" and flag[src] is None):
                        continue
                    entry[dst] = byml_from(flag[src]) if src in BYML_FIELDS else flag[src]
                diff.setdefault(t, {"add": {}, "del": []})["add"][str(name)] = entry
        if kept:
            self.notes.append(f"game data: {len(kept)} flag(s) the mod deletes kept ({_some(sorted(kept), 3)}): "
                              "most likely they differ only in the game version it was made on")
        return diff

    def merge(self, base, diff):
        import bcml_data
        if not diff:
            return base
        out, notes = bcml_data.merge("gamedata.yml", base, [[diff]])
        self.notes += notes
        return out

    def write(self, model) -> bytes:
        return model


SAVE_FILE = re.compile(r"^(?P<pre>.*?saveformat_)(?P<n>\d+)(?P<ext>\.bgsvdata)$")
SAVE_ORDER = ("game_data.sav", "caption.sav", "option.sav")
SAVE_CHUNK = 8192


class SaveDataFile:
    """Save data flags (UKMM's SaveDataPack): the flags of game_data.sav, caption.sav and option.sav, each added to
    the game's list of that file when missing (UKMM's merge, without its deletes: see above)."""

    def __init__(self):
        self.notes: list[str] = []

    def parse(self, data: bytes):
        import bcml_data
        s = wc.sarc_parse(data)
        if s.order != wc.LE or any(not n.attr >> 24 for n in s.nodes):
            raise UkmmError("the game's save data list isn't a Switch pack with named files")
        files: dict[str, dict] = {}
        pre = ext = None
        for n in sorted(s.nodes, key=lambda n: int(SAVE_FILE.match(n.name)["n"]) if SAVE_FILE.match(n.name) else 0):
            m = SAVE_FILE.match(n.name)
            if not m:
                raise UkmmError(f"{n.name} isn't a save data format file")
            pre, ext = m["pre"], m["ext"]
            doc = bcml_data.byml_load(data[s.data_offset + n.start:s.data_offset + n.end])
            fl = doc.get("file_list") if isinstance(doc, dict) else None
            if not (isinstance(fl, list) and len(fl) == 2 and isinstance(fl[0], dict) and isinstance(fl[1], list)):
                raise UkmmError(f"{n.name} isn't a save data format file")
            f = files.setdefault(str(fl[0].get("file_name")), {"doc": doc, "flags": {}})
            for e in fl[1]:
                hv = e.get("HashValue") if isinstance(e, dict) else None
                if not isinstance(hv, int) or isinstance(hv, bool):
                    raise UkmmError("a save data entry without a HashValue")
                f["flags"][bcml_data._signed(hv)] = e
        return {"files": files, "pre": pre or "/saveformat_", "ext": ext or ".bgsvdata", "key": s.hash_key,
                "changed": False}

    def load(self, v):
        if not isinstance(v, dict):
            raise UkmmError("save data UKMM wrote in a form this loader doesn't know")
        out: dict[str, dict] = {}
        for fname, part in v.items():
            flags = part.get("flags") if isinstance(part, dict) else None
            if flags is None:
                continue
            if not isinstance(flags, dict):
                raise UkmmError("save data flags UKMM wrote in a form this loader doesn't know")
            add, dels = {}, []
            for flag, removed in flags.items():
                if not (isinstance(flag, dict) and isinstance(flag.get("name"), str) and isinstance(flag.get("hash"), int)):
                    raise UkmmError("a save data flag UKMM wrote in a form this loader doesn't know")
                if removed:
                    dels.append(flag["name"])
                else:
                    add[flag["hash"]] = flag["name"]
            out[str(fname)] = {"add": add, "del": dels}
        return out

    def merge(self, base, diff):
        import bcml_data
        kept = []
        for fname, part in diff.items():
            kept += part["del"]
            f = base["files"].get(fname)
            if f is None:
                if part["add"]:
                    self.notes.append(f"save data: your game has no {fname} list, so {len(part['add'])} flag(s) for "
                                      "it are left out")
                continue
            for hv, name in part["add"].items():
                hv = bcml_data._signed(hv)
                if hv not in f["flags"]:
                    f["flags"][hv] = {"DataName": name, "HashValue": hv}
                    base["changed"] = True
        if kept:
            self.notes.append(f"save data: {len(kept)} flag(s) the mod deletes kept ({_some(sorted(kept), 3)}): most "
                              "likely they differ only in the game version it was made on")
        return base

    def write(self, m) -> bytes:
        import bcml_data
        if not m["changed"]:
            raise _Unchanged()
        items = []
        names = [n for n in SAVE_ORDER if n in m["files"]] + [n for n in m["files"] if n not in SAVE_ORDER]
        for fname in names:
            f = m["files"][fname]
            ordered = [f["flags"][hv] for hv in sorted(f["flags"])]
            for i in range(0, max(len(ordered), 1), SAVE_CHUNK):
                doc = dict(f["doc"])
                doc["file_list"] = [f["doc"]["file_list"][0], ordered[i:i + SAVE_CHUNK]]
                items.append((f"{m['pre']}{len(items)}{m['ext']}", bcml_data.byml_dump(doc), 4))
        return bnp.sarc_build(items, m["key"])


class _Unchanged(Exception):
    """A merge that left the game's file as it was: its own copy is used."""


_AAMP = AampFile()
MERGEABLE = {"GenericAamp": _AAMP, "GeneralParamList": _AAMP, "UMii": _AAMP, "DamageParam": _AAMP,
             "Awareness": _AAMP, "Lod": _AAMP, "Demo": _AAMP, "WorldInfo": _AAMP,
             "ActorLink": ActorLinkFile(), "ASList": ASListFile(), "ModelList": ModelListFile(),
             "GameDataPack": GameDataFile(), "SaveDataPack": SaveDataFile()}
KIND_NAMES = {"Physics": "physics", "AnimSeq": "animation sequences", "Recipe": "recipes", "ShopData": "shop data",
              "AIProgram": "AI programs", "AISchedule": "AI schedules", "DropTable": "drop tables",
              "LifeCondition": "life conditions", "Chemical": "chemistry", "BoneControl": "bone control",
              "AttClient": "attention clients", "AttClientList": "attention client lists",
              "RagdollBlendWeight": "ragdoll blend weights", "RagdollConfig": "ragdoll configs",
              "RagdollConfigList": "ragdoll config lists", "AnimationInfo": "animation info",
              "ActorInfo": "actor info", "GameDataPack": "game data flags", "SaveDataPack": "save data flags",
              "MessagePack": "texts", "MapUnit": "map units", "MainStatic": "main field static actors",
              "Static": "static actors", "Location": "locations", "LazyTraverseList": "lazy traverse lists",
              "EventInfo": "event info", "ResidentActors": "resident actors", "ResidentEvents": "resident events",
              "QuestProduct": "quests", "Tips": "tips", "AreaData": "area data", "LevelSensor": "level sensor",
              "StatusEffectList": "status effects", "CookData": "cooking data", "ChemicalRes": "chemistry",
              "ShopGameDataInfo": "shop game data", "BarslistInfo": "sound lists", "FontArchive": "fonts",
              "LayoutArchive": "UI layouts", "GenericByml": "BYML data"}


# ---------------------------------------------------------------------------------------------------- Yaz0, packs

def compressed_name(name: str) -> bool:
    """UKMM's (roead's) rule: a file whose extension starts with "s" is Yaz0-compressed, except .sarc."""
    base = name.rsplit("/", 1)[-1]
    ext = base.rsplit(".", 1)[-1] if "." in base else ""
    return ext.startswith("s") and ext != "sarc"


def yaz0_stored(data: bytes, align: int = 0) -> bytes:
    """A Yaz0 stream with every byte stored as is (valid Yaz0, made in C-speed slices)."""
    n = len(data)
    groups = (n + 7) // 8
    padded = bytes(data) + bytes(groups * 8 - n)
    body = bytearray(groups * 9)
    body[0::9] = b"\xff" * groups
    for j in range(8):
        body[j + 1::9] = padded[j::8]
    cut = groups * 9 - (groups * 8 - n)
    return b"Yaz0" + struct.pack(">II", n, align) + bytes(4) + bytes(body[:cut])


def yaz0(data: bytes, align: int = 0) -> bytes:
    """Yaz0: really compressed when small (pure Python compresses well under 1 MB/s), stored as is when big."""
    return wc.yaz0_compress(data, align) if len(data) <= YAZ0_SQUEEZE else yaz0_stored(data, align)


def new_alignment(name: str, data: bytes, min_align: int) -> int:
    """The alignment of a file added to a pack, picked as UKMM's pack writer (roead) does: the pack's smallest, a few
    file types' own, and what a Switch binary header asks for."""
    base = name.rsplit("/", 1)[-1]
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
    a = max(min_align, 4)
    a = max(a, {"ksky": 8, "bksky": 8, "gtx": 0x2000, "sharcb": 0x1000, "sharc": 0x1000, "baglmf": 0x80,
                "bffnt": 0x1000}.get(ext, 1))
    if len(data) > 0x20 and data[:4] != b"Yaz0" and data[0xC:0xE] == b"\xff\xfe":
        size = struct.unpack_from("<I", data, 0x1C)[0]
        if size == len(data) and data[0xE] < 14:
            a = max(a, 1 << data[0xE])
    return a


def sarc_entries(data: bytes) -> list[tuple[str, int]]:
    """The (name, stored size) of each file in a pack (Yaz0 or not), reading only its header."""
    head = bnp._yaz0_head(data, 0x14) if data[:4] == b"Yaz0" else data[:0x14]
    if head[:4] != b"SARC" or len(head) < 0x14:
        return []
    doff = struct.unpack_from("<I", head, 0x0C)[0]
    if doff > 1 << 24:
        return []
    raw = bytes(bnp._yaz0_head(data, doff) if data[:4] == b"Yaz0" else data[:doff])
    if raw[6:8] != b"\xff\xfe" or raw[0x14:0x18] != b"SFAT":
        return []
    count = struct.unpack_from("<H", raw, 0x1A)[0]
    sfnt = 0x20 + 16 * count
    if raw[sfnt:sfnt + 4] != b"SFNT":
        return []
    out = []
    for k in range(count):
        _, attr, start, end = struct.unpack_from("<IIII", raw, 0x20 + 16 * k)
        if attr >> 24:
            at = sfnt + 8 + (attr & 0xFFFFFF) * 4
            stop = raw.find(b"\0", at, doff)
            if stop < 0:
                return out
            out.append((raw[at:stop].decode("utf-8", "replace"), max(0, end - start)))
    return out


def sarc_names(data: bytes) -> list[str]:
    """The file names of a pack (Yaz0 or not), reading only its header."""
    return [n for n, _ in sarc_entries(data)]


def unpacked_size(data: bytes) -> int:
    """A file's size once Yaz0 is undone (from the Yaz0 header alone)."""
    return struct.unpack_from(">I", data, 4)[0] if data[:4] == b"Yaz0" and len(data) >= 16 else len(data)


def estimate_entry(ref_size: int, ref_entry: int, new_len: int, pack: bool) -> int:
    """A size table entry for a new file of new_len bytes (unpacked), from a game file of the same kind (ref_size
    bytes, entry ref_entry): a pack needs its bytes plus the same overhead; another file the larger of that and its
    entry grown in proportion."""
    over = bnp.round32(new_len) + ref_entry - bnp.round32(ref_size)
    if pack or ref_size <= 0:
        return max(over, bnp.round32(new_len))
    return max(over, bnp.round32(math.ceil(ref_entry * new_len / ref_size)))


def _unyaz0(data: bytes) -> bytes:
    return wc.yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data


# ---------------------------------------------------------------------------------------------------- installing

@dataclass
class Layer:
    label: str                  # "" for the base, else "options/<path>"
    prefix: str                 # where its files sit in the package
    content: list[str] = field(default_factory=list)
    aoc: list[str] = field(default_factory=list)


@dataclass
class Result:
    meta: Meta
    chosen: list[Option]
    files: int = 0              # game files written
    from_game: int = 0          # files inside packs taken from the game's copies
    built: int = 0              # resources built from the package (binary, packs, merged)
    merged: int = 0             # parameter files merged
    rstb_entries: int = 0
    rstb_changed: int = 0
    rstb_new: int = 0           # entries estimated for new files
    rstb_ok: bool = False
    resources: int = 0          # the package's resources for the chosen options
    unmerged: dict[str, int] = field(default_factory=dict)   # of them, kinds not merged: UKMM type -> count
    notes: list[str] = field(default_factory=list)
    not_merged: list[str] = field(default_factory=list)
    packs: list[str] = field(default_factory=list)

    def summary(self) -> list[str]:
        m = self.meta
        opts = ", ".join(o.name for o in self.chosen) or "none"
        n = f"{self.rstb_entries} entr{'y' if self.rstb_entries == 1 else 'ies'}" + (
            f" ({self.rstb_new} estimated for new files)" if self.rstb_new else "")
        out = [f"UKMM package {m.name or '?'}" + (f" {m.version}" if m.version else "") + f": options {opts}"
               + (f" (of {len(m.options)})" if m.options else "")]
        if self.unmerged:
            out.append(unmerged_share(sum(self.unmerged.values()), self.resources, self.unmerged))
        out += [f"{self.files} game file(s) written; {self.built} resource(s) from the package; {self.merged} "
                f"parameter file(s) merged; {self.from_game} file(s) in packs taken from your game",
                f"size table: {n}, {self.rstb_changed} changed from the game's" if self.rstb_changed else
                f"size table: {n}, none of them bigger than the game's, so the game's is used" if self.rstb_ok else
                f"size table: {n}, not made (see below)" if self.rstb_entries else
                "size table: no changes (the game's is used)"]
        out += self.notes
        out += [f"needs attention: {x}" for x in self.not_merged]
        return out


def unmerged_share(n: int, total: int, kinds: dict[str, int]) -> str:
    """The line `info` and `add` put first: how much of the package is kinds this loader doesn't merge yet."""
    what = ", ".join(f"{v} {KIND_NAMES.get(k, k)}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
    return (f"{n} of {max(total, n)} resource(s) are kinds this loader can't merge yet ({what}): the parts of the mod "
            "they make are left out")


class Sizer:
    """Size table entries for files the game doesn't have, estimated from the game's files of the same kind nearest
    in size (estimate), and where the game's actor packs hold each file (look). tools/bnp.py sizes the new files of a
    converted Wii U package with it too."""

    def __init__(self, game: str, rstb: bnp.Rstb | None, say=print):
        self.romfs = os.path.join(game, "romfs")
        self.aoc_dirs = bnp._aoc_dirs(game)
        self.rstb, self.say = rstb, say
        self.index: dict[str, tuple[str, list[str]]] | None = None
        self._kin: dict[tuple[bool, str], list[tuple[int, str]]] = {}  # (DLC?, kind) -> (size, canonical name) of
        #                                     the files in the game's actor packs (filled with the index)
        self._folders: dict[tuple, list[tuple[int, int]]] = {}         # a game folder's files of a kind: (size, entry)
        self.siblings: list[dict[str, list[tuple[int, str]]]] = []     # the game packs being rebuilt: kind -> files

    def look(self) -> dict[str, tuple[str, list[str]]]:
        """Canonical name -> (game file, the pack names on the way) of every file in the game's actor packs (and the
        actor packs in TitleBG.pack), read once from their headers."""
        if self.index is None:
            self.say("  looking through the game's actor packs (once)...")
            self.index = {}
            for aoc, roots in ((False, [self.romfs]), (True, self.aoc_dirs)):
                for root in roots:
                    folder = os.path.join(root, "Actor", "Pack")
                    try:
                        names = sorted(os.listdir(folder))
                    except OSError:
                        continue
                    for n in names:
                        p = os.path.join(folder, n)
                        try:
                            with open(p, "rb") as f:
                                head = f.read(1 << 16)
                        except OSError:
                            continue
                        for inner, size in sarc_entries(head):
                            self._note(canonical((AOC if aoc else "") + inner), (p, [inner]), inner, size, aoc)
            tbg = os.path.join(self.romfs, "Pack", "TitleBG.pack")
            if os.path.isfile(tbg):
                try:
                    blob = bnp.Blob(bnp._read(tbg))
                    for n, (content, _) in blob.open().items():
                        if n.startswith("Actor/Pack/") and isinstance(content, (bytes, bytearray)):
                            for inner, size in sarc_entries(content):
                                self._note(canonical(inner), (tbg, [n, inner]), inner, size, False)
                except (wc.FormatError, struct.error, IndexError):
                    pass
        return self.index

    def _note(self, key: str, where: tuple[str, list[str]], inner: str, size: int, aoc: bool) -> None:
        if key in self.index:
            return
        self.index[key] = where
        if not compressed_name(inner):
            self._kin.setdefault((aoc, _kind(inner)), []).append((size, inner))

    def kin(self, name: str, aoc: bool, depth: int) -> list[tuple[int, int]]:
        """(size unpacked, size table entry) of game files of the same kind as `name` (by extension; textures and
        other models apart): for a file in a pack, the other files of the game's pack being rebuilt around it,
        else those in the game's actor packs; for a file at the top, the files of its game folder."""
        rstb, kind = self.rstb, _kind(name)
        if depth:
            def entries(files: list[tuple[int, str]]) -> list[tuple[int, int]]:
                got = [(size, rstb.get(n) if rstb.get(n) is not None or not aoc else rstb.get(bnp.DLC_CANON + n))
                       for size, n in files]
                return [(size, e) for size, e in got if e is not None]
            for sib in reversed(self.siblings):
                got = entries(sib.get(kind, []))
                if got:
                    return got
            self.look()
            return entries(self._kin.get((aoc, kind), []))
        folder = name.rpartition("/")[0]
        key = (aoc, folder.lower(), kind)
        if key not in self._folders:
            out = []
            for root in (self.aoc_dirs if aoc else [self.romfs]):
                d = os.path.join(root, *folder.split("/")) if folder else root
                try:
                    names = sorted(e.name for e in os.scandir(d) if e.is_file())
                except OSError:
                    continue
                for n in names:
                    if _kind(n) != kind:
                        continue
                    if len(out) >= KIN_FILES:
                        break
                    try:
                        with open(os.path.join(d, n), "rb") as f:
                            head = f.read(16)
                            if head[:4] != b"Yaz0" or len(head) < 16:
                                f.seek(0, 2)
                                head = b"raw!" + struct.pack(">I", f.tell())
                    except OSError:
                        continue
                    rel = f"{folder}/{n}" if folder else n
                    entry = rstb.get((bnp.DLC_CANON if aoc else "") + bnp.canon(rel, head))
                    if entry is not None:
                        out.append((struct.unpack_from(">I", head, 4)[0], entry))
            self._folders[key] = out
        return self._folders[key]

    def estimate(self, name: str, new_len: int, pack: bool, aoc: bool, depth: int) -> int | None:
        """A size table entry for a file the game doesn't have (new_len bytes unpacked; depth 0 at the top, else
        inside packs): the largest estimate from the KIN_NEAR game files of its kind nearest to it in size (see
        estimate_entry; only those within 4 times its size count in proportion), plus KIN_MARGIN (not for a pack,
        sized exactly). None when there is no table, or nothing of its kind to go by."""
        if self.rstb is None:
            return None
        refs = self.kin(name, aoc, depth)
        if not refs:
            return None
        far = lambda r: abs(math.log2(max(r[0], 1) / max(new_len, 1)))  # noqa: E731
        near = sorted(refs, key=far)[:KIN_NEAR]
        # (a file 4 times bigger or smaller isn't sized in proportion: its overhead is a different share of it)
        need = max(estimate_entry(size, entry, new_len, pack or far((size, entry)) > 2) for size, entry in near)
        return need if pack else bnp.round32(math.ceil(need * KIN_MARGIN))


class Builder:
    """Builds the package's files from the game's own and its resources, as UKMM's deploy does for one mod."""

    def __init__(self, pkg: Package, layers: list[Layer], game: str, dictionary, res: Result, say=print):
        self.pkg, self.layers, self.game, self.dictionary, self.res, self.say = pkg, layers, game, dictionary, res, say
        self.romfs = os.path.join(game, "romfs")
        self.aoc_dirs = bnp._aoc_dirs(game)
        self.game_rstb = bnp.load_game_rstb(game)
        self.entries: dict[str, int] = {}
        self.unsupported: dict[str, list[str]] = {}    # type -> files
        self.missing: list[str] = []
        self.broken: list[str] = []
        self.left_out: set[str] = set()     # files already named (a kind not merged, a merge that failed)
        self.unmerged: dict[str, tuple[str, int]] = {}  # canonical path -> (UKMM type, the package's resources for
        #                                     it) of a kind not merged
        self.new_sized: set[str] = set()    # size table entries estimated for new files
        self.unsized: list[str] = []        # new files no entry could be estimated for
        self.sizer = Sizer(game, self.game_rstb, say)
        self.used_dict = False
        self._dict_looked = False
        self.wiiu: WiiuBinaries | None = None   # a Wii U package's binary resources, converted (prepare_wiiu)
        self.wiiu_lost: dict[str, str] = {}     # where -> kind: Wii U data that can't be converted, the game's copy used
        self.wiiu_gone: dict[str, str] = {}     # where -> kind: the same with no game copy: left out
        self.wanted: set[str] = set()       # canonical paths of the resources the chosen layers hold
        self.resources = 0                  # and how many resources that is
        for name in pkg.files:
            rel = name[len(pkg.prefix):]
            if not name.startswith(pkg.prefix) or rel.endswith(".yml") or rel.startswith("thumb."):
                continue
            if not rel.startswith("options/"):
                self.wanted.add(rel)
                self.resources += 1
            for layer in layers:
                if layer.prefix and rel.startswith(layer.prefix):
                    self.wanted.add(rel[len(layer.prefix):])
                    self.resources += 1

    def touches(self, stored: bytes, aoc: bool, depth: int = 0) -> bool:
        """Whether a game pack holds a file the package changes (its own list, then the packs in it)."""
        names = sarc_names(stored)
        pre = AOC if aoc else ""
        if any(canonical(pre + n) in self.wanted for n in names):
            return True
        nested = [n for n in names if bnp.is_packish(n)]
        if not nested or depth >= 2:
            return False
        try:
            files = bnp.Blob(stored).open()
        except (wc.FormatError, struct.error, IndexError):
            return False
        for n in nested:
            e = files.get(n)
            if e is not None and self.touches(e[0].data() if isinstance(e[0], bnp.Blob) else e[0], aoc, depth + 1):
                return True
        return False

    # -- the package's versions of a resource (base first, then each chosen option)
    def versions(self, canon: str, as_stored: bool = False) -> list[Res]:
        """The package's versions of a resource. One that can't be read is named ("needs attention") and left out,
        so the rest of the package still installs; only a missing zstd dictionary ends the install. A Wii U
        package's binary resources come converted (unless as_stored)."""
        out = []
        for layer in self.layers:
            rel = layer.prefix + canon
            if self.pkg.has(rel):
                raw = self.pkg.read(rel)
                if dict_id(raw):
                    self.used_dict = True
                    if self.dictionary is None and not self._dict_looked:
                        self._dict_looked = True               # (looked for once, only when a resource needs it)
                        self.dictionary, _ = find_dictionary()
                where = f"{layer.label + '/' if layer.label else ''}{canon}"
                try:
                    out.append(load_resource(raw, self.dictionary, where))
                except UnknownResource as e:    # (damaged data, or no dictionary: the whole package is refused)
                    self.broken.append(f"{str(e).rstrip('.?')}; left out, the game's copy is used")
                    self.left_out.add(where)
        if self.wiiu is not None and not as_stored:
            out = self.wiiu.versions(canon, out)
        return out

    # -- the game's files
    def game_file(self, rel: str, aoc: bool) -> bytes | None:
        for root in (self.aoc_dirs if aoc else [self.romfs]):
            p = os.path.join(root, *rel.split("/"))
            if os.path.isfile(p):
                return bnp._read(p)
        return None

    def elsewhere(self, canon: str) -> bytes | None:
        """A game file that isn't in the pack the mod puts it in, found by its canonical name in the game's actor
        packs (UKMM leaves out of its packages the files a new pack shares with the game)."""
        hit = self.sizer.look().get(canon)
        if hit is None:
            return None
        path, chain = hit
        try:
            blob = bnp.Blob(bnp._read(path))
            for name in chain[:-1]:
                blob = blob.child(name)
                if blob is None or not blob.is_sarc():
                    return None
            entry = blob.open().get(chain[-1])
        except (OSError, wc.FormatError, struct.error, IndexError):
            return None
        if entry is None:
            return None
        return entry[0].data() if isinstance(entry[0], bnp.Blob) else entry[0]

    # -- size table entries for new files: estimated from the game's files most like them (Sizer)
    def kin(self, name: str, aoc: bool, depth: int) -> list[tuple[int, int]]:
        return self.sizer.kin(name, aoc, depth)

    def new_size(self, names: list[str], name: str, new_len: int, pack: bool, aoc: bool, depth: int,
                 where: str) -> None:
        """A size table entry for a file the game doesn't have (Sizer.estimate). Named when the game has nothing of
        its kind to go by."""
        if self.game_rstb is None or any(self.game_rstb.get(n) is not None for n in names):
            return                                   # (no table to add to, or the game's lists it already)
        need = self.sizer.estimate(name, new_len, pack, aoc, depth)
        if need is None:
            self.unsized.append(where)
            return
        for n in names:
            self.entries[n] = max(need, self.entries.get(n, 0))
            self.new_sized.add(n)

    # -- size table entries for rebuilt files the game's table lists
    def size(self, names: list[str], old_len: int, new_len: int, pack: bool) -> None:
        if self.game_rstb is None:
            return
        for name in names:
            g = self.game_rstb.get(name)
            if g is not None:
                need = bnp.needed_size(g, old_len, new_len, pack)
                if need > g:
                    self.entries[name] = max(need, self.entries.get(name, 0))
                return

    def build(self, canon: str, name: str, aoc: bool, vanilla: bytes | None, where: str, depth: int = 0
              ) -> bytes | None:
        """A file's bytes as stored (Yaz0 when its name says so), or None when neither the package nor the game has
        it. vanilla: the game's copy as stored."""
        if depth > 16:
            raise UkmmError(f"{where}: packs nest too deep")
        vers = self.versions(canon)
        if not vers:
            if vanilla is None and depth:
                vanilla = self.elsewhere(canon)
                if vanilla is not None:
                    self.res.from_game += 1
            if self.wiiu is not None and canon in self.wiiu.failed:
                kind = self.wiiu.failed[canon].split(":", 1)[0] or "file"
                if vanilla is None:
                    self.left_out.add(where)    # (Wii U data that can't be converted, and the game has no copy: named)
                    self.wiiu_gone[where] = kind
                else:
                    self.wiiu_lost[where] = kind
            if vanilla is None or not bnp.is_packish(name) or not self.touches(vanilla, aoc):
                return vanilla
            # (the game's pack, with files inside it the package changes: UKMM rebuilds it from its own list)
            vers = [Res("sarc", files={}, align=0)]
            own = False
        else:
            own = True
        raw_v = None
        if vanilla is not None:
            try:
                raw_v = _unyaz0(vanilla)
            except (wc.FormatError, IndexError, struct.error):
                raw_v = None
        last = vers[-1].kind
        if last == "binary":
            raw = vers[-1].data
        elif last == "sarc":
            raw = self.build_sarc(canon, aoc, raw_v, [v for v in vers if v.kind == "sarc"], where, depth)
            if raw is raw_v:
                return vanilla              # (nothing of the package's got into the game's pack: its copy is used)
        else:
            raw = self.merge(canon, raw_v, [v for v in vers if v.kind == "merge"], where)
            if raw is None:
                return vanilla
        if own:
            self.res.built += 1
        align = struct.unpack_from(">I", vanilla, 8)[0] if vanilla is not None and vanilla[:4] == b"Yaz0" else 0
        stored = yaz0(raw, align) if compressed_name(name) else raw
        names = [bnp.canon(canonical_rel(name), stored)]
        if aoc:
            names = ([bnp.DLC_CANON + names[0]] if depth == 0 else [names[0], bnp.DLC_CANON + names[0]])
        if raw_v is not None and len(raw) > len(raw_v):
            self.size(names, len(raw_v), len(raw), last == "sarc")
        elif vanilla is None:
            self.new_size(names, name, len(raw), last == "sarc", aoc, depth, where)
        return stored

    def build_sarc(self, canon: str, aoc: bool, raw_v: bytes | None, vers: list[Res], where: str, depth: int
                   ) -> bytes:
        game_pack = None
        if raw_v is not None and raw_v[:4] == b"SARC":
            try:
                game_pack = bnp.Blob(raw_v)
                game_files = game_pack.open()
            except (wc.FormatError, struct.error, IndexError) as e:
                self.broken.append(f"{where}: the game's copy can't be read ({e})")
                game_pack, game_files = None, {}
        else:
            game_files = {}
        names: dict[str, bool] = {n: False for n in game_files}
        for v in vers:
            for n, removed in v.files.items():
                names[n] = removed
        if self.wiiu is not None:
            names = self.wiiu.pack_names(names, aoc)
        min_align = vers[0].align if game_pack is None else (vers[0].align or 4)
        items = []
        same = game_pack is not None                  # (nothing of the package's in it so far)
        from_game = self.res.from_game
        sib: dict[str, list[tuple[int, str]]] = {}    # (its files by kind: new files in it are sized from them)
        for n, (content, _) in game_files.items():
            data = content.data() if isinstance(content, bnp.Blob) else content
            sib.setdefault(_kind(n), []).append((unpacked_size(data), bnp.canon(n, data)))
        self.sizer.siblings.append(sib)
        try:
            for n in sorted(k for k, removed in names.items() if not removed):
                entry = game_files.get(n)
                g = None
                if entry is not None:
                    g = entry[0].data() if isinstance(entry[0], bnp.Blob) else entry[0]
                child = canonical((AOC if aoc else "") + n)
                data = self.build(child, n, aoc, g, f"{where}//{n}", depth + 1)
                if data is None:
                    if f"{where}//{n}" not in self.left_out:
                        self.missing.append(f"{where}//{n}")
                    continue
                if data is g:
                    self.res.from_game += 1
                else:
                    same = False
                items.append((n, data, entry[1] if entry is not None else new_alignment(n, data, min_align)))
        finally:
            self.sizer.siblings.pop()
        if same and len(items) == len(game_files):
            self.res.from_game = from_game
            return raw_v                              # (the game's own pack: build() uses its copy)
        return bnp.sarc_build(items, game_pack.key if game_pack is not None else 0x65)

    def merge(self, canon: str, raw_v: bytes | None, vers: list[Res], where: str) -> bytes | None:
        mtype = vers[-1].mtype
        handler = MERGEABLE.get(mtype)
        if handler is None:
            self.unsupported.setdefault(mtype, []).append(where)
            self.left_out.add(where)
            self.unmerged[canon] = (mtype, len(vers))
            return None
        notes = getattr(handler, "notes", None)
        try:
            if raw_v is None and isinstance(handler, (GameDataFile, SaveDataFile)):
                raise UkmmError("your game has no copy to add the flags to")
            model = handler.parse(raw_v) if raw_v is not None else None
            for v in vers:
                if v.mtype != mtype:
                    continue
                diff = handler.load(v.value)
                model = diff if model is None else handler.merge(model, diff)
            out = handler.write(model)
        except _Unchanged:
            return None
        except (UkmmError, wc.FormatError, struct.error, KeyError, TypeError, ValueError, AttributeError) as e:
            self.broken.append(f"{where} ({KIND_NAMES.get(mtype, mtype)}): can't be merged ({e})")
            self.left_out.add(where)
            return None
        finally:
            if notes:
                self.res.notes += [f"{where}: {n}" for n in notes]
                notes.clear()
        if raw_v is not None and out == raw_v:
            return None                     # (the merge changed nothing: the game's copy is used)
        self.res.merged += 1
        return out


def wiiu_lines(lost: dict[str, str], gone: dict[str, str]) -> list[str]:
    """Needs attention lines for a Wii U package's files that stay Wii U data (where -> kind): those whose game copy
    is used (that part of the mod is lost), and those the game has none of (left out: what uses them may fail)."""
    def count(by: dict[str, str]) -> str:
        c: dict[str, int] = {}
        for k in by.values():
            c[k] = c.get(k, 0) + 1
        return ", ".join(f"{v} {k}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))

    def names(by: dict[str, str]) -> str:
        return _some([w.split("//")[-1].rsplit("/", 1)[-1] for w in sorted(by)], 2)

    out = []
    if lost:
        out.append(f"{len(lost)} file(s) are Wii U data that can't be converted ({count(lost)}): your game's are used "
                   f"({names(lost)}), so that part of the mod is lost")
    if gone:
        out.append(f"{len(gone)} new file(s) are Wii U data that can't be converted and your game has none "
                   f"({count(gone)}; {names(gone)}): left out, so what uses them may fail")
    return out


TEX1_RE = re.compile(r"\.Tex1(\.s?bfres)$", re.I)
TEX2_RE = re.compile(r"\.Tex2(\.s?bfres)$", re.I)


class WiiuBinaries:
    """A Wii U package's binary resources (models, textures, icons, BYML, MSBT, packs...) converted to the Switch's
    formats by tools/wiiu_convert.py, in memory, with the game's files (vanilla materials, mipmaps, TexInfo.txt):
    X.Tex1 + X.Tex2 become X.Tex (renamed in the manifests and pack lists). A file that can't be converted (Havok,
    sounds...) gives way to the game's copy of the same path (the pack's twin, else the game's actor packs: Havok
    cloth in a new armor pack is a copy of a vanilla one), else it is left out and named. Parsed resources (parameter
    files, game data...) and pack lists are the same on both consoles and are used as they are."""

    def __init__(self, builder: Builder):
        self.b = builder
        self.converted: dict[str, bytes] = {}    # canonical path -> Switch bytes (never Yaz0)
        self.renamed: dict[str, str] = {}        # canonical path of an X.Tex1 -> its X.Tex's
        self.dropped: set[str] = set()           # X.Tex2 (joined into X.Tex)
        self.failed: dict[str, str] = {}         # canonical path -> why the game's copy is used (or none)
        self.extras: dict[str, bytes] = {}       # real name -> your vanilla model written again (a new TexInfo.txt)
        self.made: set[str] = set()              # canonical paths only the conversion has (X.Tex, those models)
        self.sizes: dict[str, int] = {}          # size table entries the model converter worked out
        self.lines: list[str] = []               # what happened, for the notes

    @staticmethod
    def member(canon: str) -> str:
        return ("aoc/0010/" + canon[len(AOC):]) if canon.startswith(AOC) else "content/" + canon

    @staticmethod
    def canon_of(target: str) -> str:
        if target.startswith(wc.DLC_OUT + "/"):
            return AOC + canonical(target[len(wc.DLC_OUT) + 1:])
        return canonical(target[len(wc.BASE_OUT) + 1:] if target.startswith(wc.BASE_OUT + "/") else target)

    def prepare(self, real: dict[str, str], say=print) -> None:
        """Convert every binary resource of the chosen layers (the latest layer's version of each). real: canonical
        path -> real name of the manifests' files (DLC ones under Aoc/0010/), so models keep their .sbfres names."""
        files: dict[str, bytes] = {}
        source: dict[str, str] = {}
        for canon in sorted(self.b.wanted):
            vers = self.b.versions(canon, as_stored=True)
            if vers and vers[-1].kind == "binary":
                name = self.member(real.get(canon, canon))
                files[name] = vers[-1].data
                source[name] = canon
        if not files:
            return
        say(f"  converting {len(files)} Wii U file(s) to the Switch's formats...")
        romfs = os.path.join(self.b.game, "romfs")
        try:
            game = wc.GameFiles(romfs) if os.path.isdir(os.path.join(romfs, "Model")) else None
        except wc.ConvertError:
            game = None
        out: dict[str, bytes] = {}
        src = wc.MemSource(files)
        try:
            rep = wc._run("Wii U package", wc.Converter(wc.LE, models=wc.ModelJob(game)),
                          opened=(src, wc.plan_layout(src.files)), collect=out)
        except wc.ConvertError as e:
            raise UkmmError(f"Not added: its Wii U files can't be converted ({e}).") from None
        targets = {f["target"] for f in rep.files}
        bad: dict[str, list[str]] = {}
        for f in rep.files:
            canon = source[f["path"]]
            target = f["target"]
            new = self.canon_of(target)
            if target in out and f["status"] not in (wc.CANT, wc.BROKEN):
                self.converted[new] = out[target]
                if new != canon:
                    self.renamed[canon] = new
            elif TEX2_RE.search(canon) and f["status"] in wc.OK:
                self.dropped.add(canon)        # (joined into its X.Tex)
            else:
                self.failed[canon] = f"{f['kind']}" + (f": {f['detail']}" if f["detail"] else "")
                bad.setdefault(f["kind"], []).append(canon)
        for target, data in out.items():       # (your vanilla models written again with a new TexInfo.txt)
            if target not in targets and target.startswith(wc.BASE_OUT + "/"):
                self.extras[target[len(wc.BASE_OUT) + 1:]] = data
                self.converted.setdefault(self.canon_of(target), data)
                self.made.add(self.canon_of(target))
        self.made |= set(self.renamed.values())
        self.b.wanted |= self.made
        st = rep.by_status()
        self.lines.append(f"Wii U package: {len(files)} binary file(s) converted for the Switch ("
                          + ", ".join(f"{wc.LABEL[s]} {st[s]}" for s in wc.STATUS_ORDER if st[s]) + ")")
        self.lines += [f"  {x}" for x in rep.model_lines() if not x.startswith("size table:")]  # (sized below)
        if bad:
            self.lines.append("Wii U data that can't be converted (" + "; ".join(
                f"{k}: {_some(v, 2)}" for k, v in sorted(bad.items())) + "): your game's copy of each is used where it "
                "has one, the others are left out")
        att = rep.attention()
        if att:
            self.lines.append(f"{len(att)} converted model/texture file(s) to check in game ("
                              + _some([n.path.split('//')[-1].split('/', 1)[-1] for n in att], 3) + ")")
        r = (rep.models or {}).get("rstb") or {}
        self.sizes = dict(r.get("sizes") or {})

    def versions(self, canon: str, vers: list[Res]) -> list[Res]:
        if not vers:
            data = self.converted.get(canon) if canon in self.made else None
            return [Res("binary", data=data)] if data is not None else []
        if vers[-1].kind != "binary":
            return vers
        if canon in self.converted:
            return [*vers[:-1], Res("binary", data=self.converted[canon])]
        return []                             # (can't be converted, or joined into an X.Tex: the game's copy)

    def real_name(self, aoc: bool, rel: str) -> str | None:
        """A manifest file's real name on the Switch (X.Tex1 -> X.Tex), or None for one left out (X.Tex2)."""
        canon = (AOC if aoc else "") + canonical(rel)
        if canon in self.dropped:
            return None
        return TEX1_RE.sub(r".Tex\1", rel) if canon in self.renamed else rel

    def pack_names(self, names: dict[str, bool], aoc: bool) -> dict[str, bool]:
        out = {}
        for n, removed in names.items():
            new = self.real_name(aoc, n)
            if new is not None:
                out[new] = removed and out.get(new, True)
        return out


def canonical_rel(name: str) -> str:
    return name.replace("\\", "/")


def _kind(name: str) -> str:
    """What a file is, for sizing new ones: its extension without the Yaz0 "s" (.sbfres and .bfres are one kind),
    textures (Foo.Tex.bfres, Foo.Tex1.bfres) apart from models."""
    base = name.rsplit("/", 1)[-1].lower()
    ext = base.rsplit(".", 1)[-1] if "." in base else ""
    if len(ext) > 1 and ext[0] == "s" and ext != "sarc":
        ext = ext[1:]
    return ext + (":tex" if ext == "bfres" and ".tex" in base else "")


def _layers(pkg: Package, chosen: list[Option], meta: Meta) -> list[Layer]:
    out = [Layer("", "")]
    for o in chosen:
        if not o.path:
            continue                      # (a label in the option list: it holds nothing)
        out.append(Layer(f"options/{o.path}", f"options/{o.path}/"))
    for layer in out:
        rel = layer.prefix + "manifest.yml"
        if not pkg.has(rel):
            if layer.label:
                raise UkmmError(f"Not added: {meta.name}'s option folder {layer.label} has no manifest.yml.")
            raise UkmmError(f"Not added: {meta.name} has no manifest.yml.")
        layer.content, layer.aoc = parse_manifest(pkg.text(rel), f"{layer.label or meta.name}: manifest.yml")
    return out


def install(src: str, stage: str, game: str, *, meta: Meta, picks: list[str] = (), no_defaults: bool = False,
            dictionary: zstdec.Dictionary | None = None, say=print) -> Result:
    """Build the package's game files into stage (a Switch-layout mod: 01007EF00011E000/romfs/..., the DLC's under
    01007EF00011F001/romfs/...) from the game (`game`: Savage Wind's extracted folder, only read)."""
    name = meta.name or os.path.basename(src)
    if meta.platform not in ("switch", "any", "wiiu"):
        raise UkmmError(f"Not added: {name}'s meta.yml names a platform this loader doesn't know "
                        f"({meta.platform or 'none'}).")
    if not os.path.isdir(os.path.join(game, "romfs")):
        raise UkmmError("Savage Wind's game files aren't set up (no extracted\\romfs), and a UKMM package is built "
                        "from them. Set them up in Savage Wind first.")
    chosen = choose(meta, picks, no_defaults)
    res = Result(meta, chosen)
    with Package(src) as pkg:
        layers = _layers(pkg, chosen, meta)
        free = _free_space(stage)
        size = pkg.size() // 2                # (the least it writes: stored data takes at most 2 bytes a byte)
        if free is not None and size + SPARE_SPACE > free:
            raise UkmmError(f"Not added: not enough free space to build {name} ({(size + SPARE_SPACE) >> 20} MB "
                            f"needed at least, {free >> 20} MB free).")
        budget = MAX_INSTALL if free is None else min(MAX_INSTALL, free - SPARE_SPACE)
        written = 0
        b = Builder(pkg, layers, game, dictionary, res, say)
        res.resources = b.resources
        todo: dict[tuple[bool, str], str] = {}
        for layer in layers:
            for aoc, files in ((False, layer.content), (True, layer.aoc)):
                for rel in files:
                    todo.setdefault((aoc, rel.lower()), rel)
        texts = [rel for (aoc, low), rel in todo.items()
                 if not aoc and re.fullmatch(r"pack/bootup_[a-z]{4}\.pack", low)]
        if texts:
            res.not_merged.append(f"texts ({_some(texts, 2)}) not merged: UKMM builds them from the game's language "
                                  "pack, which this loader doesn't do yet; the game's texts are used")
        jobs = [(aoc, rel) for (aoc, _), rel in todo.items() if rel not in texts or aoc]
        if meta.platform == "wiiu":
            # (a Wii U package: its binary files converted first, X.Tex1 + X.Tex2 named X.Tex as the Switch's)
            b.wiiu = WiiuBinaries(b)
            b.wiiu.prepare({(AOC if aoc else "") + canonical(rel): (AOC if aoc else "") + rel for aoc, rel in jobs},
                           say)
            renamed = {}
            for aoc, rel in jobs:
                new = b.wiiu.real_name(aoc, rel)
                if new is not None:
                    renamed.setdefault((aoc, new.lower()), (aoc, new))
            jobs = list(renamed.values())
            for real_name, data in sorted(b.wiiu.extras.items()):
                if (False, real_name.lower()) not in renamed:
                    jobs.append((False, real_name))
            res.notes += b.wiiu.lines
        os.makedirs(stage, exist_ok=True)
        step = max(1, len(jobs) // 5)
        for k, (aoc, rel) in enumerate(jobs):
            if len(jobs) > 20 and k % step == 0:
                say(f"  building files: {k}/{len(jobs)}")
            canon = (AOC if aoc else "") + canonical(rel)
            vanilla = b.game_file(rel, aoc)
            where = ("aoc/0010/" if aoc else "") + rel
            data = b.build(canon, rel, aoc, vanilla, where)
            if data is None:
                if where not in b.left_out:
                    b.missing.append(where)
                continue
            if data is vanilla:
                continue                          # (nothing of the package's in it after all)
            written += len(data)
            if written > budget:
                raise UkmmError(f"Not added: {name} builds more than " + (
                    f"{MAX_INSTALL >> 30} GB of files, which no real mod does: the package may be damaged."
                    if budget == MAX_INSTALL else
                    f"the {(budget + SPARE_SPACE) >> 20} MB free on the disk (with {SPARE_SPACE >> 20} MB left "
                    "spare): free some space and add it again."))
            dest = os.path.join(stage, DLC_TID if aoc else BASE_TID, "romfs", *rel.split("/"))
            bnp._write_new(dest, data)
            res.files += 1
            if bnp.is_packish(rel):
                res.packs.append(("aoc/0010/" if aoc else "") + rel)
        # what couldn't be built
        if meta.masters:
            res.not_merged.append(masters_note(meta.masters))
        for mtype, count in b.unmerged.values():
            res.unmerged[mtype] = res.unmerged.get(mtype, 0) + count
        for mtype, files in sorted(b.unsupported.items()):
            res.not_merged.append(f"{len(files)} {KIND_NAMES.get(mtype, mtype)} file(s) ({_some(files, 2)}) not "
                                  f"merged: this loader can't read UKMM's {mtype} data yet; the game's copies are "
                                  "used, and new ones are left out (UKMM can merge them)")
        res.not_merged += list(dict.fromkeys(b.broken))
        if b.wiiu is not None:
            for k, v in b.wiiu.sizes.items():   # (the model converter's sizes for converted models and textures)
                b.entries[k] = max(v, b.entries.get(k, 0))
        res.not_merged += wiiu_lines(b.wiiu_lost, b.wiiu_gone)
        if b.missing:
            res.not_merged.append(f"{len(b.missing)} file(s) neither the package nor your game has "
                                  f"({_some(b.missing, 2)}): left out, so the parts of the mod that use them may not "
                                  "work")
        if b.unsized and b.game_rstb is not None:
            res.not_merged.append(f"{len(b.unsized)} new file(s) got no size table entry ({_some(b.unsized, 2)}): "
                                  "your game has no file of their kind to estimate one from, so they may fail to "
                                  "load")
        if not b.aoc_dirs and any(layer.aoc for layer in layers):
            res.not_merged.append("the package changes DLC files and your game has no DLC set up: DLC packs hold "
                                  "only the mod's own files")
        if b.used_dict:
            res.notes.append("decompressed with UKMM's dictionary")
    # the size table: the game's, with the grown entries and the new files' estimated ones
    res.rstb_entries = len(b.entries)
    res.rstb_new = len(b.new_sized)
    shipped = os.path.join(stage, *bnp.RSTB_REL.split("/"))
    if b.entries:
        if b.game_rstb is None:
            res.not_merged.append("the size table (RSTB) can't be made: the game's own isn't in extracted\\romfs, so "
                                  "bigger files may fail to load")
        else:
            entries = b.game_rstb.changes(b.entries)
            res.rstb_changed, res.rstb_ok = b.game_rstb.apply(entries), True
            if res.rstb_changed:
                table = b.game_rstb.to_bytes()
                if written + len(table) > budget:
                    raise UkmmError(f"Not added: not enough free space to build {name}.")
                bnp._write_new(shipped, table)
    meta_dir = os.path.join(stage, META)
    write_json(os.path.join(meta_dir, "ukmm.json"), {
        "tool": TOOL_ID, "name": meta.name, "version": meta.version, "platform": meta.platform, "api": meta.api,
        "options": [o.name for o in chosen], "option_paths": [o.path for o in chosen], "packs": res.packs,
        "files": res.files, "built": res.built, "merged": res.merged, "from_game": res.from_game,
        "rstb": {k: v for k, v in sorted(b.entries.items())}, "rstb_new": sorted(b.new_sized),
        "unmerged": res.unmerged, "resources": res.resources, "masters": meta.masters,
        "not_merged": res.not_merged, "notes": res.notes})
    return res


def _free_space(path: str) -> int | None:
    """Free bytes on the disk that holds path (or its nearest existing folder); None when it can't be told."""
    parent = os.path.abspath(path)
    while not os.path.isdir(parent) and os.path.dirname(parent) != parent:
        parent = os.path.dirname(parent)
    try:
        return shutil.disk_usage(parent).free
    except OSError:
        return None


def _some(items: list[str], n: int = 3) -> str:
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")


def add(src: str, kind: str, stage: str, game: str, meta: Meta, *, picks: list[str] = (), no_defaults: bool = False,
        say=print) -> Result:
    """modman add's way in: the package is read where it is (a .zip is never extracted), stage gets the mod."""
    choose(meta, picks, no_defaults)  # (an unknown option is refused before anything is read)
    what = os.path.basename(src)
    say(f"Building {what} as a Switch mod (options, packs filled from your game, size table)...")
    try:
        return install(src, stage, game, meta=meta, picks=picks, no_defaults=no_defaults, say=say)
    except OSError as e:
        raise UkmmError(f"Not added: installing {what} failed ({e}).") from None
    except DAMAGED as e:
        raise UkmmError(f"Not added: {what} can't be read ({type(e).__name__}: {str(e)[:200]}): the package may "
                        "be damaged, or made by a UKMM version this loader doesn't know.") from None


# what a damaged package can still raise deep in the parsers (refused cleanly, never a traceback)
DAMAGED = (zstdec.ZstdError, ValueError, KeyError, IndexError, TypeError, struct.error, RecursionError,
           MemoryError, wc.FormatError, bnp.BnpError)


# ---------------------------------------------------------------------------------------------------- the library

def stored(root: str) -> dict | None:
    """The ukmm.json a library copy keeps (None for a mod that wasn't a UKMM package)."""
    try:
        with open(os.path.join(root, META, "ukmm.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("tool") == TOOL_ID else None


def stored_problems(root: str) -> list[str]:
    """What the package's install couldn't merge (modman lists it under "Needs attention"); a damaged copy's list
    (not a list) is named, not a crash."""
    info = stored(root) or {}
    v = info.get("not_merged") or []
    if not isinstance(v, list):
        return [f"its library copy's {META}/ukmm.json is damaged (not_merged): remove the mod and add it again"]
    return [x for x in v if isinstance(x, str)]


# ---------------------------------------------------------------------------------------------------- command line

def describe(src: str) -> list[str]:
    """`info`: what the package holds and what this loader can build from it (reads only)."""
    meta = peek(src)
    out = meta.summary()
    with Package(src) as pkg:
        layers = [Layer("", "")] + [Layer(f"options/{o.path}", f"options/{o.path}/") for o in meta.options if o.path]
        counts: dict[str, int] = {}
        dictionary, where = None, None
        dict_needed = False
        for layer in layers:
            rel = layer.prefix + "manifest.yml"
            if not pkg.has(rel):
                continue
            c, a = parse_manifest(pkg.text(rel), rel)
            out.append(f"  {layer.label or 'base'}: {len(c)} file(s)" + (f", {len(a)} DLC file(s)" if a else ""))
        for name in pkg.files:
            rel = name[len(pkg.prefix):]
            if rel.endswith(".yml") or rel.startswith("thumb."):
                continue
            raw = pkg.head(rel, 160 << 10)   # (a zstd block is at most 128 KB)
            if dict_id(raw):
                dict_needed = True
                if dictionary is None:
                    dictionary, where = find_dictionary()
                    if dictionary is None:
                        break
            key = resource_kind(raw, dictionary)
            if key.startswith("merge:"):
                key = ("merged: " if key[6:] in MERGEABLE else "not merged: ") + key[6:]
            counts[key] = counts.get(key, 0) + 1
        if dict_needed and dictionary is None:
            out.append("  its resources need UKMM's dictionary, which wasn't found (see: python tools/ukmm.py dict)")
        elif counts:
            out.append("  resources: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
            if where:
                out.append(f"  (UKMM's dictionary read from {where})")
            unmerged = {k[12:]: v for k, v in counts.items() if k.startswith("not merged: ")}
            if unmerged:                      # (said first: how much of the mod won't be there)
                out.insert(1, "  " + unmerged_share(sum(unmerged.values()), sum(counts.values()), unmerged)
                           + " (all options counted)")
    return out


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info", help="show a package's name, version, options and resources (reads only)")
    p.add_argument("package", help="a UKMM package (.zip) or a folder holding one's files")
    p = sub.add_parser("dict", help="say where UKMM's zstd dictionary is found (reads only)")
    p.add_argument("path", nargs="?", help="a ukmm.exe or zsdic file to check")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "dict":
            if args.path:
                d = dictionary_from(args.path)
                print(f"found in {args.path}" if d else f"not in {args.path}")
                return 0 if d else 1
            d, where = find_dictionary()
            if d is None:
                print("UKMM's dictionary wasn't found. Looked in:\n  " + "\n  ".join(dictionary_places()))
                print(str(_no_dictionary()))
                return 1
            print(f"found in {where} (id {d.id}, {DICT_SIZE} bytes)")
            return 0
        src = os.path.abspath(args.package)
        if not os.path.exists(src):
            raise UkmmError(f"{args.package} not found.")
        if package_kind(src) is None:
            raise UkmmError(f"{args.package} isn't a UKMM package (a .zip or folder with meta.yml and manifest.yml).")
        print("\n".join(describe(src)))
        print("Add it: python tools/modman.py add <package> [--option NAME] [--no-options]")
        return 0
    except (UkmmError, OSError, zipfile.BadZipFile) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except DAMAGED as e:
        print(f"error: {args.package} can't be read ({type(e).__name__}: {str(e)[:200]}): the package may be "
              "damaged.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
