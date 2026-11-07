#!/usr/bin/env python3
"""The names a native mod can use, each with a one-line doc, and a fast search over them (for tools/mod_studio.py).

    python tools/mod_symbols.py search <text> [--codemap DIR] [--limit N]   # try the search
    python tools/mod_symbols.py stats [--codemap DIR]                     # how many names, and how long it takes

Where the names come from (all read on this PC when the editor starts; nothing here is a copy of game files):
  native/modhost/botw.h      helpers, addresses and offsets with our own plain names; the "//:" line above each one is
                             its doc, and "(not tested in game)" marks the ones nobody checked in the game yet
  native/modhost/swmod.h     the mod API: the api-> members, the types, macros and inline helpers
  docs/addresses.md          every game address the repo documents, with its status (used in a mod, verified, ...)
  out/codemap/functions.csv  optional: the local code map tools/codemap.py makes from your own game files. Raw game
                             names, not tested. It is read on your PC only and never copied into the repo; the cache
                             below is in your user folder.

build_catalog(repo_root, codemap_dir=None) returns a list of dicts {name, kind, signature, doc, address, source}, kind
being 'helper' (botw.h), 'api' (swmod.h), 'address', 'offset' or 'codemap'. load_catalog() does the same through a
cache in %LOCALAPPDATA%\\botw-mod-loader (kept until one of the files changes). build_index(catalog) makes the search
index; search(index, text, limit=10) finds names: prefix matches first (also of each word in a name, so "stam" finds
botw_stamina_get), then substring, then fuzzy (the letters in order), the curated names before the code map's.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BOTW_H = "native/modhost/botw.h"
SWMOD_H = "native/modhost/swmod.h"
ADDRESSES_MD = "docs/addresses.md"
CODEMAP_FILE = "functions.csv"
FIELDS = ("name", "kind", "signature", "doc", "address", "source")
KINDS = ("helper", "api", "address", "offset", "codemap")
KIND_RANK = {k: i for i, k in enumerate(KINDS)}
CODEMAP_DOC = "code map: raw game names, not tested"
NOT_TESTED = "not tested in game"
CACHE_FORMAT = 1
CACHE_NAME = "mod-studio-catalog.json"
MAIN_BASE = 0x7000000000
MAX_DOC = 400

# fuzzy search over the big list (the code map): a character in fewer lines than this is followed line by line, else
# the whole list is scanned with one regex, in pieces, until the time budget runs out
RARE_LINES = 2000
FUZZY_BUDGET = 0.0025  # seconds
SUBSTRING_BUDGET = 0.0025  # seconds: the code map's substring scan stops then (a common word in a query of several)
SEARCH_BUDGET = 0.004  # seconds: substring and fuzzy together never go past this, from the start of the search
FUZZY_CHUNK = 32768    # characters per regex piece
COUNTED = "abcdefghijklmnopqrstuvwxyz0123456789_:"  # counted when the big index is made (the rest when first asked)

# docs for api members that swmod.h groups under one comment (our own words)
API_FALLBACK_DOCS = {
    "reply_str": "Add a text field to the command's answer: key = value.",
    "reply_int": "Add a whole-number field to the command's answer.",
    "reply_num": "Add a number field (with decimals) to the command's answer.",
    "reply_bool": "Add a true/false field to the command's answer.",
}


# ---------------------------------------------------------------- small helpers

def entry(name: str, kind: str, signature: str = "", doc: str = "", address: str = "", source: str = "") -> dict:
    return {"name": name, "kind": kind, "signature": signature, "doc": doc, "address": address, "source": source}


def _clean(s: str, n: int = MAX_DOC) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 3].rstrip() + "..."


def _int_value(text: str) -> int | None:
    """The first whole number in a C value (0x7001e13ed0ull, ((uint64_t)0xa4), 12), or None (floats, strings)."""
    t = text.strip()
    if t.startswith('"'):
        return None
    m = re.search(r"(?<![\w.])(?:(0[xX][0-9a-fA-F]+)[uUlL]*|(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)([uUlLfF]*))(?![\w.])", t)
    if not m:
        return None
    if m.group(1):
        return int(m.group(1), 16)
    num, suffix = m.group(2), m.group(3)
    if any(c in num for c in ".eE") or "f" in suffix.lower():
        return None  # a float
    return int(num)


def _hex(v: int) -> str:
    return f"0x{v:x}"


def _split_comment(line: str, in_block: bool) -> tuple[str, str | None, bool]:
    """(code, the // comment text or None, still inside a /* block */) for one line. Strings are respected."""
    code, i, n, q = [], 0, len(line), None
    while i < n:
        c = line[i]
        if in_block:
            j = line.find("*/", i)
            if j < 0:
                return "".join(code), None, True
            i, in_block = j + 2, False
            code.append(" ")
            continue
        if q:
            code.append(c)
            if c == "\\" and i + 1 < n:
                code.append(line[i + 1])
                i += 2
                continue
            if c == q:
                q = None
            i += 1
            continue
        if c in "\"'":
            q = c
        elif line.startswith("//", i):
            return "".join(code), line[i + 2:], False
        elif line.startswith("/*", i):
            in_block = True
            i += 2
            continue
        code.append(c)
        i += 1
    return "".join(code), None, in_block


# ---------------------------------------------------------------- C headers

_DEFINE_RE = re.compile(r"^#\s*define\s+(?P<name>[A-Za-z_]\w*)(?P<args>\([^)]*\))?\s*(?P<value>.*)$")
_TYPEDEF_FN_RE = re.compile(r"^typedef\s+(?P<ret>[^()]+?)\s*\(\s*\*\s*(?P<name>\w+)\s*\)\s*\((?P<params>.*)\)\s*;")
_TYPEDEF_FWD_RE = re.compile(r"^typedef\s+(?:struct|union|enum)\s+\w+\s+(?P<name>\w+)\s*;")
_BLOCK_START_RE = re.compile(r"^(?P<typedef>typedef\s+)?(?P<what>struct|union|enum)\b\s*(?P<tag>\w+)?\s*\{"
                             r"(?P<rest>.*)$")
_BLOCK_END_RE = re.compile(r"^\}\s*(?P<name>\w+)?\s*;")
_FUNC_RE = re.compile(
    r"^(?:(?:static|inline|extern|SWMOD_EXPORT|__inline|__forceinline)\s+)*"
    r"(?P<ret>(?:(?:const|unsigned|signed|struct|enum|union|volatile)\s+)*[A-Za-z_]\w*(?:\s*\*)*)\s*"
    r"\b(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^()]*(?:\([^()]*\)[^()]*)*)\)\s*(?P<end>[;{]|$)")
_VAR_RE = re.compile(r"^(?:(?:static|extern|const|volatile)\s+)+[\w\s\*]+?\b(?P<name>[A-Za-z_]\w*)\s*(?:=[^;]*)?;")
_MEMBER_FN_RE = re.compile(r"^(?P<ret>[^()]+?)\s*\(\s*\*\s*(?P<name>\w+)\s*\)\s*\((?P<params>.*)\)\s*;")
_MEMBER_RE = re.compile(r"^(?P<type>[\w\s\*]+?)\s*\b(?P<names>[A-Za-z_]\w*(?:\s*\[[^\]]*\])?"
                        r"(?:\s*,\s*\**\s*[A-Za-z_]\w*(?:\s*\[[^\]]*\])?)*)\s*;")
_ENUM_ITEM_RE = re.compile(r"(?P<name>[A-Za-z_]\w*)\s*(?:=\s*(?P<value>[^,]+))?\s*,?")
_KEYWORDS_NOT_NAMES = {"if", "for", "while", "switch", "return", "sizeof", "do", "else"}


class Decl:
    """One thing a header declares, with the comments just above it and at the end of its line."""
    __slots__ = ("name", "what", "signature", "value", "before", "after", "member_of", "params")

    def __init__(self, name, what, signature, value="", before=(), after=None, member_of="", params=None):
        self.name, self.what, self.signature, self.value = name, what, signature, value
        self.before, self.after, self.member_of, self.params = list(before), after, member_of, params

    def __repr__(self):
        return f"Decl({self.name!r}, {self.what!r})"


def _sig(s: str) -> str:
    """A declaration on one line, spaced the usual way: `const char *fmt, ...`, `struct Cpu *c`."""
    s = " ".join(s.split())
    s = re.sub(r"\s*(\*+)\s*", r" \1", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r"\s*,\s*", ", ", s)
    return s.strip()


def scan_header(text: str) -> list[Decl]:
    """What a C header declares at file level, and the members of its structs: #defines, functions (their bodies
    skipped), typedefs, enum values and static variables. A small reader for our own headers, not a C parser."""
    out: list[Decl] = []
    lines = text.splitlines()
    comments: list[str] = []
    in_block = False
    body = 0               # depth inside a function body (skipped)
    blocks: list[dict] = []  # open struct / enum blocks
    i = 0
    while i < len(lines):
        code, comment, in_block = _split_comment(lines[i], in_block)
        s = code.strip()
        if body:
            body += s.count("{") - s.count("}")
            body = max(body, 0)
            comments = []
            i += 1
            continue
        if not s:
            if comment is not None:
                comments.append(comment)
            elif not in_block:
                comments = []
            i += 1
            continue
        # a declaration that goes on over more lines: ( without ), or a #define ending in a backslash
        joined = 0
        while (s.count("(") > s.count(")") or s.endswith("\\")) and i + 1 < len(lines) and joined < 12:
            i += 1
            joined += 1
            more, c2, in_block = _split_comment(lines[i], in_block)
            s = s.rstrip("\\").rstrip() + " " + more.strip()
            if comment is None:
                comment = c2
        before, comments = comments, []
        if blocks:
            b = blocks[-1]
            m = _BLOCK_END_RE.match(s)
            if m or s.startswith("}"):
                blocks.pop()
                name = m.group("name") if m else None
                if b["typedef"] and name:
                    out.append(Decl(name, "type", f"{b['what']} {name}", before=b["before"], after=b["after"]))
                elif b["tag"] and not b["typedef"]:
                    out.append(Decl(b["tag"], "type", f"{b['what']} {b['tag']}", before=b["before"], after=b["after"]))
                i += 1
                continue
            if _BLOCK_START_RE.match(s):
                bs = _BLOCK_START_RE.match(s)
                blocks.append({"what": bs.group("what"), "tag": bs.group("tag"), "typedef": bool(bs.group("typedef")),
                               "before": before, "after": comment, "name": bs.group("tag") or ""})
                i += 1
                continue
            owner = b["tag"] or "?"
            if b["what"] == "enum":
                for m in _ENUM_ITEM_RE.finditer(s):
                    if m.group("name"):
                        out.append(Decl(m.group("name"), "const", m.group("name"), (m.group("value") or "").strip(),
                                        before, comment))
                i += 1
                continue
            m = _MEMBER_FN_RE.match(s)
            if m:
                out.append(Decl(m.group("name"), "member", _sig(m.group("ret")), before=before, after=comment,
                                member_of=owner, params=_sig(m.group("params"))))
            else:
                m = _MEMBER_RE.match(s)
                if m:
                    for nm in m.group("names").split(","):
                        nm = nm.strip().lstrip("*").strip()
                        base = re.match(r"[A-Za-z_]\w*", nm)
                        if base:
                            out.append(Decl(base.group(0), "member", _sig(m.group("type")), value=nm, before=before,
                                            after=comment, member_of=owner))
            i += 1
            continue
        if s.startswith("#"):
            m = _DEFINE_RE.match(s)
            if m:
                args = m.group("args")
                out.append(Decl(m.group("name"), "macro", m.group("name") + (_sig(args) if args else ""),
                                m.group("value").strip(), before, comment, params=_sig(args[1:-1]) if args else None))
            i += 1
            continue
        m = _TYPEDEF_FN_RE.match(s)
        if m:
            out.append(Decl(m.group("name"), "type", f"{_sig(m.group('ret'))} (*{m.group('name')})"
                            f"({_sig(m.group('params'))})", before=before, after=comment))
            i += 1
            continue
        m = _TYPEDEF_FWD_RE.match(s)
        if m:
            out.append(Decl(m.group("name"), "type", s.rstrip(";").strip(), before=before, after=comment))
            i += 1
            continue
        m = _BLOCK_START_RE.match(s)
        if m:
            rest = m.group("rest")
            end = _BLOCK_END_RE.search(rest.strip()) if "}" in rest else None
            if end is not None or re.search(r"\}\s*(\w+)?\s*;", rest):  # a whole struct on one line
                e = re.search(r"\}\s*(\w+)?\s*;", rest)
                name = e.group(1) if e else None
                if m.group("typedef") and name:
                    out.append(Decl(name, "type", f"{m.group('what')} {name}", before=before, after=comment))
            else:
                blocks.append({"what": m.group("what"), "tag": m.group("tag"), "typedef": bool(m.group("typedef")),
                               "before": before, "after": comment})
            i += 1
            continue
        if s.startswith("typedef"):
            i += 1
            continue
        m = _FUNC_RE.match(s)
        if m and m.group("name") not in _KEYWORDS_NOT_NAMES and m.group("ret").split()[-1] not in ("return", "else"):
            out.append(Decl(m.group("name"), "function", _sig(m.group("ret")), before=before, after=comment,
                            params=_sig(m.group("params"))))
            if m.group("end") == "{" or "{" in s:
                body = max(s.count("{") - s.count("}"), 0)
            elif m.group("end") == "" and i + 1 < len(lines) and lines[i + 1].strip().startswith("{"):
                i += 1
                body = max(lines[i].count("{") - lines[i].count("}"), 0)
            i += 1
            continue
        m = _VAR_RE.match(s)
        if m:
            out.append(Decl(m.group("name"), "var", s.rstrip(";").strip(), before=before, after=comment))
        elif s.startswith("{"):
            body = max(s.count("{") - s.count("}"), 0)
        i += 1
    return out


def _doc_lines(lines, marker: str = "") -> list[str]:
    """Comment texts (what follows //) as doc lines: with a marker (":" for botw.h), only the lines that start with
    it; without, every line, minus rulers like '----'."""
    out = []
    for c in lines:
        if c is None:
            continue
        if marker:
            if c.startswith(marker):
                out.append(c[len(marker):].strip())
            continue
        t = c.strip().lstrip("-").strip()
        if t:
            out.append(t)
    return out


def _func_signature(ret: str, name: str, params: str | None) -> str:
    sep = "" if ret.endswith("*") else " "
    return f"{ret}{sep}{name}({params if params else 'void'})".strip()


def _internal(name: str, doc: str) -> bool:
    return "__" in name or doc.startswith("(internal)") or name.startswith("_")


def _botw_kind(d: Decl, doc: str) -> tuple[str, str]:
    """(kind, address) of a botw.h constant."""
    v = _int_value(d.value)
    if v is None:
        return "helper", ""
    if v >= MAIN_BASE or "ADDR" in d.name.upper():
        return "address", _hex(v)
    if re.match(r"\s*0[xX]", d.value) or "offset" in doc.lower():
        return "offset", _hex(v)
    return "helper", ""


def parse_botw_h(text: str, source: str = BOTW_H) -> list[dict]:
    """botw.h: every name with a "//:" doc (above it or at the end of its line), plus undocumented public botw_ and
    BOTW_ names. Internal ones (botw__..., "(internal)") are left out."""
    out, seen = [], set()
    for d in scan_header(text):
        doc = _clean(" ".join(_doc_lines(d.before, ":") + _doc_lines([d.after], ":")))
        if d.member_of or d.name in seen or _internal(d.name, doc):
            continue
        if not doc and not d.name.lower().startswith("botw_"):
            continue
        if d.what == "macro" and not d.value and d.params is None:
            continue  # (an include guard or a flag)
        seen.add(d.name)
        if d.what == "function":
            out.append(entry(d.name, "helper", _func_signature(d.signature, d.name, d.params), doc, "", source))
        elif d.what == "macro" and d.params is not None:
            out.append(entry(d.name, "helper", f"{d.name}({d.params})", doc, "", source))
        elif d.what in ("macro", "const"):
            kind, addr = _botw_kind(d, doc)
            shown = addr if addr else d.value
            out.append(entry(d.name, kind, f"{d.name} = {shown}" if shown else d.name, doc, addr, source))
        elif d.what == "type":
            out.append(entry(d.name, "helper", d.signature, doc, "", source))
        elif d.what == "var":
            out.append(entry(d.name, "helper", d.signature, doc, "", source))
    return out


def parse_swmod_h(text: str, source: str = SWMOD_H) -> list[dict]:
    """swmod.h: the SwModApi members as api->name, and the types, macros and functions a mod uses. A macro defined
    twice (#ifdef __cplusplus ... #else) keeps its last definition: the C one."""
    out: dict[str, dict] = {}
    for d in scan_header(text):
        doc = _clean(" ".join(_doc_lines(d.before) + _doc_lines([d.after])))
        if d.member_of:
            if d.member_of != "SwModApi":
                continue
            name = f"api->{d.name}"
            if name in out:
                continue
            doc = doc or API_FALLBACK_DOCS.get(d.name, "")
            sep = "" if d.signature.endswith("*") else " "
            if d.params is not None:
                sig = f"{d.signature}{sep}{name}({d.params})"
            else:
                arr = d.value[len(d.name):].strip() if d.value else ""
                sig = f"{d.signature}{sep}{name}{arr}"
            out[name] = entry(name, "api", sig, doc, "", source)
            continue
        if _internal(d.name, doc):
            continue
        if d.what == "macro":
            if d.name.endswith("_H") or (not d.value and d.params is None):
                continue
            sig = f"{d.name}({d.params})" if d.params is not None else f"{d.name} = {d.value}"
            old = out.get(d.name)
            out[d.name] = entry(d.name, "api", sig, doc or (old["doc"] if old else ""), "", source)
        elif d.name in out:
            continue
        elif d.what == "function":
            out[d.name] = entry(d.name, "api", _func_signature(d.signature, d.name, d.params), doc, "", source)
        elif d.what in ("type", "const"):
            out[d.name] = entry(d.name, "api", d.signature, doc, "", source)
    return list(out.values())


# ---------------------------------------------------------------- docs/addresses.md

_STOP = {"a", "an", "the", "of", "to", "its", "it", "is", "with", "and", "for", "that", "from", "by", "in", "on", "at",
         "as", "be", "are", "this", "what", "which", "into"}
_ROW_ADDR = re.compile(r"^`0x([0-9a-fA-F]+)`$")
_ROW_OFF = re.compile(r"^`\+0x([0-9a-fA-F]+)`$")


def _slug(what: str, words: int = 5) -> str:
    head = re.split(r"\s\(|;|\.\s", what, maxsplit=1)[0]
    head = re.sub(r"['’]s\b", "", head.replace("`", " "))
    parts = [w for w in re.findall(r"[a-z0-9]+", head.lower()) if w not in _STOP]
    return "_".join(parts[:words]) or "value"


def parse_addresses_md(text: str, source: str = ADDRESSES_MD) -> list[dict]:
    """The address table: one entry per row (made-up selftest rows left out). Names are made from the row's
    description: addr_<words> for addresses, off_<words> for offsets into an object."""
    out, used = [], set()
    group = ""
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|") or re.fullmatch(r"\|[\s|:-]+\|", s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and cells[0].startswith("**") and all(not c for c in cells[1:]):
            group = cells[0].strip("*").strip()
            continue
        if len(cells) < 5:
            continue
        addr_cell, what, _where, how, status = cells[:5]
        if status.startswith("made up"):
            continue
        ma, mo = _ROW_ADDR.match(addr_cell), _ROW_OFF.match(addr_cell)
        if not (ma or mo):
            continue
        kind = "address" if ma else "offset"
        value = int((ma or mo).group(1), 16)
        base = ("addr_" if ma else "off_") + _slug(what)
        name, k = base, 2
        while name in used:
            name, k = f"{base}_{k}", k + 1
        used.add(name)
        plain_what = what.replace("`", "").rstrip()
        how_short = _clean(how.replace("`", ""), 160)
        stop = "" if plain_what.endswith((".", "?", "!")) else "."
        doc = _clean(f"{plain_what}{stop} Status: {status}" + (f" ({group})" if group else "")
                     + f". Checked: {how_short}")
        sig = f"{name} = {_hex(value)}" if ma else f"{name} = +{_hex(value)}"
        out.append(entry(name, kind, sig, doc, _hex(value), source))
    return out


def _status_of(doc: str) -> str:
    m = re.search(r"Status: ([^.(]+)", doc)
    return m.group(1).strip() if m else ""


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in _STOP}


def merge_addresses(core: list[dict], table: list[dict]) -> list[dict]:
    """botw.h already names some table rows: add the row's status to that name's doc instead of listing the value
    twice. Offsets are only merged when the two descriptions share a word (the same offset means other things in
    other objects)."""
    by_addr = {e["address"]: e for e in core if e["kind"] == "address" and e["address"]}
    offs = [e for e in core if e["kind"] == "offset" and e["address"]]
    out = []
    for row in table:
        what = _words(row["doc"].split(". ")[0])
        hit = by_addr.get(row["address"]) if row["kind"] == "address" else next(
            (e for e in offs if e["address"] == row["address"] and _words(e["doc"]) & what), None)
        if hit is None:
            out.append(row)
            continue
        st = _status_of(row["doc"])
        if st and "docs/addresses.md" not in hit["doc"]:
            hit["doc"] = _clean(f"{hit['doc']} (docs/addresses.md: {st})")
    return out


# ---------------------------------------------------------------- the local code map

def parse_codemap(path: Path, source: str = "") -> list[dict]:
    """functions.csv of tools/codemap.py: address,name,kind,notes. Raw game names, not tested."""
    out = []
    src = source or str(path)
    with open(path, encoding="utf-8-sig", newline="", errors="replace") as f:
        rows = csv.reader(f)
        head = next(rows, None)
        if not head or [h.strip().lower() for h in head[:2]] != ["address", "name"]:
            return out
        for row in rows:
            if len(row) < 2 or not row[1]:
                continue
            addr = row[0].strip().lower()
            what = row[2].strip() if len(row) > 2 else ""
            notes = row[3].strip() if len(row) > 3 else ""
            doc = CODEMAP_DOC + (f" ({what})" if what else "") + (f": {_clean(notes, 160)}" if notes else "")
            out.append({"name": row[1], "kind": "codemap", "signature": "", "doc": doc, "address": addr,
                        "source": src})
    return out


# ---------------------------------------------------------------- the catalog

def default_codemap_dir(repo_root: Path | str = REPO) -> Path:
    return Path(repo_root) / "out" / "codemap"


def _rel_or_abs(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def build_catalog(repo_root: Path | str = REPO, codemap_dir: Path | str | None = None) -> list[dict]:
    """Every name: botw.h, swmod.h, docs/addresses.md, then (when codemap_dir is given and has functions.csv) the code
    map. A missing file is skipped."""
    root = Path(repo_root)

    def text(rel: str) -> str:
        try:
            return (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    botw = parse_botw_h(text(BOTW_H))
    api = parse_swmod_h(text(SWMOD_H))
    table = merge_addresses(botw, parse_addresses_md(text(ADDRESSES_MD)))
    catalog = botw + api + table
    if codemap_dir is not None:
        f = Path(codemap_dir) / CODEMAP_FILE
        if f.is_file():
            try:
                catalog += parse_codemap(f, _rel_or_abs(f, root))
            except (OSError, csv.Error):
                pass
    return catalog


def source_files(repo_root: Path | str, codemap_dir: Path | str | None) -> list[Path]:
    root = Path(repo_root)
    files = [root / BOTW_H, root / SWMOD_H, root / ADDRESSES_MD, Path(__file__).resolve()]
    if codemap_dir is not None:
        files.append(Path(codemap_dir) / CODEMAP_FILE)
    return files


def stamp(files) -> list:
    out = []
    for p in files:
        try:
            st = os.stat(p)
            out.append([str(p), st.st_mtime_ns, st.st_size])
        except OSError:
            out.append([str(p), None, None])
    return out


def default_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "botw-mod-loader"


def load_catalog(repo_root: Path | str = REPO, codemap_dir: Path | str | None = None,
                 cache_dir: Path | str | None = None) -> tuple[list[dict], bool]:
    """build_catalog through a cache file (JSON) keyed by the source files' times and sizes. Returns (catalog, True
    when it came from the cache). A cache that can't be read or written is ignored."""
    key = stamp(source_files(repo_root, codemap_dir))
    path = Path(cache_dir or default_cache_dir()) / CACHE_NAME
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("format") == CACHE_FORMAT and data.get("stamp") == key:
            return [dict(zip(FIELDS, row)) for row in data["rows"]], True
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    catalog = build_catalog(repo_root, codemap_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"format": CACHE_FORMAT, "stamp": key, "rows": [[e[k] for k in FIELDS] for e in catalog]}, f,
                      separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        pass
    return catalog, False


# ---------------------------------------------------------------- the index and the search

_WORD_START = re.compile(r"(?:(?<=[_>:.\s])|(?<=[a-z0-9])(?=[A-Z])|(?<=[a-zA-Z])(?=[0-9]))[A-Za-z0-9]")


def _keys(name: str, address: str, small: bool) -> set[str]:
    low = name.lower()
    keys = {low}
    if small:
        for m in _WORD_START.finditer(name):
            keys.add(low[m.start():])
    else:
        i = low.find("::")
        while i >= 0:
            keys.add(low[i + 2:])
            i = low.find("::", i + 2)
    if address:
        keys.add(address.lower())
    return keys


def _fuzzy_pattern(s: str) -> str:
    """The letters of s in order, each the first one after the one before (possessive: no backtracking)."""
    parts = []
    for c in s:
        e = re.escape(c)
        parts.append(f"[^\\n{e}]*+{e}")
    return "".join(parts)


class _Tier:
    """One list of entries: the curated names (small: sorted and scored in full) or the code map (big: read in
    order, stopping as soon as there are enough)."""

    def __init__(self, entries: list[dict], ids: list[int], small: bool):
        self.small = small
        self.ids = ids
        pairs = []
        for i in ids:
            e = entries[i]
            pairs += [(k, i) for k in _keys(e["name"], e["address"], small)]
        pairs.sort()
        self.keys = [k for k, _ in pairs]
        self.key_ids = [i for _, i in pairs]
        self.lows = [entries[i]["name"].lower() for i in ids]
        self.hay = "\n" + "\n".join(self.lows) + "\n"
        starts, p = [], 1
        for s in self.lows:
            starts.append(p)
            p += len(s) + 1
        self.starts = starts
        self.addrs = [entries[i]["address"].lower() for i in ids]
        self.docs = [(entries[i]["name"] + " " + entries[i]["doc"]).lower() for i in ids] if small else None
        self._counts: dict[str, int] = {} if small else {c: self.hay.count(c) for c in COUNTED}

    def count(self, c: str) -> int:
        n = self._counts.get(c)
        if n is None:
            n = self._counts[c] = self.hay.count(c)
        return n

    def prefix(self, q: str):
        keys, kid = self.keys, self.key_ids
        j = bisect.bisect_left(keys, q)
        while j < len(keys) and keys[j].startswith(q):
            yield kid[j]
            j += 1

    def rarity(self, w: str) -> int:
        """About how many lines have w: the count of its rarest character (cheap, from the counts kept)."""
        return min((self.count(c) for c in set(w)), default=0)

    def substring(self, words: list[str], deadline: float = float("inf")):
        if self.small:
            for k, low in enumerate(self.lows):
                if all(w in low for w in words) or (len(words) == 1 and words[0] in self.addrs[k]):
                    yield k
            return
        # Scan for the rarest word (not the longest: "vf_ zzz" must look for zzz, not visit every vf_ line), and stop
        # at the deadline: a common word that other words then reject can mean a visit to every line.
        w = min(words, key=lambda x: (self.rarity(x), -len(x)))
        if self.rarity(w) == 0:
            return
        rest = [x for x in words if x is not w]
        hay, starts, lows = self.hay, self.starts, self.lows
        pos, n = 0, 0
        while True:
            i = hay.find(w, pos)
            if i < 0:
                break
            k = bisect.bisect_right(starts, i) - 1
            end = starts[k] + len(lows[k])
            if all(x in lows[k] for x in rest):
                yield k
            pos = end + 1
            n += 1
            if not n % 128 and time.perf_counter() > deadline:
                return

    def fuzzy(self, s: str, deadline: float):
        """Line numbers (in this tier) whose name has the letters of s in order."""
        if self.small:
            rx = re.compile(_fuzzy_pattern(s))
            for k, low in enumerate(self.lows):
                if rx.search(low):
                    yield k
            return
        if any(self.count(c) == 0 for c in set(s)):
            return
        hay, starts, lows = self.hay, self.starts, self.lows
        rare = min(set(s), key=self.count)
        line_rx = re.compile(_fuzzy_pattern(s))
        if self.count(rare) <= RARE_LINES:
            pos, n = 0, 0
            while True:
                i = hay.find(rare, pos)
                if i < 0:
                    return
                k = bisect.bisect_right(starts, i) - 1
                end = starts[k] + len(lows[k])
                if line_rx.match(hay, starts[k], end):
                    yield k
                pos = end + 1
                n += 1
                if not n % 128 and time.perf_counter() > deadline:
                    return
        rx = re.compile("\n" + _fuzzy_pattern(s))
        pos, size = 0, len(hay)
        while pos < size - 1:
            stop = hay.find("\n", min(size - 1, pos + FUZZY_CHUNK) + 1)
            stop = size if stop < 0 else stop + 1
            while True:
                m = rx.search(hay, pos, stop)
                if not m:
                    break
                k = bisect.bisect_right(starts, m.start() + 1) - 1
                yield k
                pos = starts[k] + len(lows[k])
            pos = stop - 1
            if time.perf_counter() > deadline:
                return


class Index:
    """What search() needs: the entries, a sorted key list per tier, and the names joined for substring scans."""

    def __init__(self, catalog: list[dict]):
        self.entries = catalog
        core = [i for i, e in enumerate(catalog) if e["kind"] != "codemap"]
        code = [i for i, e in enumerate(catalog) if e["kind"] == "codemap"]
        self.tiers = [_Tier(catalog, core, True), _Tier(catalog, code, False)]
        self.by_name: dict[str, dict] = {}
        for e in catalog:
            self.by_name.setdefault(e["name"], e)
        self.default_order = sorted(core, key=lambda i: (KIND_RANK.get(catalog[i]["kind"], 9), catalog[i]["name"]))
        self.codemap_count = len(code)

    def __len__(self):
        return len(self.entries)


def build_index(catalog: list[dict]) -> Index:
    return Index(catalog)


def _rank(e: dict, q: str, raw: str) -> tuple:
    low = e["name"].lower()
    return (low != q, not low.startswith(q), not e["name"].startswith(raw), KIND_RANK.get(e["kind"], 9), len(low), low)


def search(index: Index, text: str, limit: int = 10, kinds=None, docs: bool = False) -> list[dict]:
    """Up to `limit` entries for text: prefix matches (of the name or of a word in it, or of the address), then
    substring, then fuzzy (the letters in order); within each, the curated names (botw.h, swmod.h, addresses.md)
    before the code map. kinds: only these kinds. docs: last, curated entries whose doc has every word."""
    raw = "_".join(text.split())
    q = raw.lower()
    out: list[dict] = []
    if limit <= 0 or index is None:
        return out
    seen: set[int] = set()
    entries = index.entries
    kinds = set(kinds) if kinds else None
    tiers = index.tiers if (kinds is None or "codemap" in kinds) else index.tiers[:1]

    def take(i: int) -> bool:
        """Add entry i; True when the list is full."""
        if i in seen:
            return False
        e = entries[i]
        if kinds is not None and e["kind"] not in kinds:
            return False
        seen.add(i)
        out.append(e)
        return len(out) >= limit

    if not q:
        for i in index.default_order:
            if take(i):
                break
        return out
    words = text.lower().split()
    start = time.perf_counter()
    stop = start + SEARCH_BUDGET
    # 1. prefix (bisect on the sorted keys)
    for t in tiers:
        if t.small:
            hits = set(t.prefix(q))
            for i in sorted(hits, key=lambda i: _rank(entries[i], q, raw)):
                if take(i):
                    return out
        else:
            for i in t.prefix(q):
                if take(i):
                    return out
    # 2. substring
    for t in tiers:
        found = t.substring(words, min(time.perf_counter() + SUBSTRING_BUDGET, stop))
        if t.small:
            found = sorted(found, key=lambda k: (min((t.lows[k].find(w) for w in words), default=0),
                                                 KIND_RANK.get(entries[t.ids[k]]["kind"], 9), len(t.lows[k])))
        for k in found:
            if take(t.ids[k]):
                return out
    # 3. fuzzy
    s = "".join(words)
    if len(s) >= 2:
        for t in tiers:
            found = t.fuzzy(s, min(time.perf_counter() + FUZZY_BUDGET, stop))
            if t.small:
                rx = re.compile(_fuzzy_pattern(s))

                def spread(k, rx=rx, t=t):
                    m = rx.search(t.lows[k])
                    return (m.end() - m.start() - len(s), len(t.lows[k])) if m else (99, 99)
                found = sorted(found, key=spread)
            for k in found:
                if take(t.ids[k]):
                    return out
    # 4. docs (curated names only)
    if docs:
        t = index.tiers[0]
        for k, d in enumerate(t.docs or []):
            if all(w in d for w in words) and take(t.ids[k]):
                return out
    return out


# ---------------------------------------------------------------- what the editor shows and inserts

def is_verified(e: dict) -> bool:
    """False for the code map, for "(not tested in game)" helpers and for addresses that are research only (a table
    row's own "Status: research only", or the "(docs/addresses.md: research only)" merged into a botw.h name)."""
    if e["kind"] == "codemap":
        return False
    d = e["doc"].lower()
    return NOT_TESTED not in d and "status: research only" not in d and "addresses.md: research only" not in d


def trust_text(e: dict) -> str:
    """One short phrase: how far to trust the name."""
    if e["kind"] == "codemap":
        return CODEMAP_DOC
    if e["kind"] == "api":
        return "mod API (swmod.h)"
    if e["source"].endswith("addresses.md"):
        st = _status_of(e["doc"])
        return f"docs/addresses.md: {st}" if st else "docs/addresses.md"
    if "addresses.md: research only" in e["doc"].lower():  # (a botw.h name whose table row is research only)
        return "docs/addresses.md: research only"
    return "checked in game" if is_verified(e) else NOT_TESTED


def call_params(e: dict) -> list[str] | None:
    """The argument names when the entry is something you call (a function, a function-like macro or an api->
    function pointer), else None."""
    if e["kind"] not in ("helper", "api"):
        return None
    name, sig = e["name"], e["signature"]
    m = re.search(re.escape(name) + r"\s*\((.*)\)\s*$", sig)
    if not m:
        return None
    inner = m.group(1).strip()
    if inner in ("", "void"):
        return []
    parts, depth, cur = [], 0, []
    for c in inner:
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    names = []
    for p in parts:
        p = p.strip()
        if p == "...":
            names.append("...")
            continue
        fp = re.search(r"\(\s*\*\s*(\w+)\s*\)", p)
        if fp:
            names.append(fp.group(1))
            continue
        p = re.sub(r"\[[^\]]*\]", "", p)
        ids = re.findall(r"[A-Za-z_]\w*", p)
        names.append(ids[-1] if ids else f"arg{len(names) + 1}")
    return names


def display_text(e: dict, width: int = 72) -> str:
    """One line for a suggestion list."""
    params = call_params(e)
    if params is not None:
        s = f"{e['name']}({', '.join(params)})"
    elif e["kind"] in ("address", "offset", "codemap") and e["address"]:
        s = f"{e['name']}  {e['address']}"
    else:
        s = e["name"]
    if e["kind"] == "codemap":
        s += "  (code map)"
    return s if len(s) <= width else s[:width - 3] + "..."


def insertion(e: dict, receiver: str = "api") -> tuple[str, int, int]:
    """(text to put in the code, start, end of the part to select afterwards). A function gets its argument names,
    with the first one that isn't `api` selected so typing replaces it; a documented address or offset its number;
    a code map name its address with the name in a comment."""
    kind, name = e["kind"], e["name"]
    if kind == "codemap":
        label = name.replace("*/", "* /")
        text = f"{e['address']}ull /* {label} (code map, not tested) */"
        return text, len(text), len(text)
    if e["source"].endswith("addresses.md") and e["address"]:
        text = e["address"] + ("ull" if kind == "address" else "")
        return text, len(text), len(text)
    if name.startswith("api->") and receiver != "api":
        name = f"{receiver}->{name[5:]}"
    params = call_params(e)
    if params is None:
        return name, len(name), len(name)
    text = f"{name}({', '.join(params)})"
    pos = len(name) + 1
    for p in params:
        if p not in ("api", "..."):
            return text, pos, pos + len(p)
        pos += len(p) + 2
    return text, len(text), len(text)


# ---------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Search the names a native mod can use (botw.h, swmod.h, "
                                             "docs/addresses.md and the local code map).")
    ap.add_argument("action", choices=("search", "stats"))
    ap.add_argument("text", nargs="*", help="search: what to look for")
    ap.add_argument("--codemap", type=Path, default=None, help="code map folder (default: out/codemap when there)")
    ap.add_argument("--no-codemap", action="store_true", help="leave the code map out")
    ap.add_argument("--limit", type=int, default=10)
    a = ap.parse_args(argv)
    cm = None if a.no_codemap else (a.codemap or default_codemap_dir())
    t0 = time.perf_counter()
    catalog, cached = load_catalog(REPO, cm)
    t1 = time.perf_counter()
    index = build_index(catalog)
    t2 = time.perf_counter()
    if a.action == "stats":
        by = {}
        for e in catalog:
            by[e["kind"]] = by.get(e["kind"], 0) + 1
        print(f"{len(catalog)} names: " + ", ".join(f"{by.get(k, 0)} {k}" for k in KINDS))
        print(f"Read in {1000 * (t1 - t0):.0f} ms ({'cache' if cached else 'files'}), index in "
              f"{1000 * (t2 - t1):.0f} ms.")
        return 0
    text = " ".join(a.text)
    t3 = time.perf_counter()
    hits = search(index, text, a.limit, docs=True)
    t4 = time.perf_counter()
    for e in hits:
        print(f"{display_text(e, 90)}")
        doc = e["doc"][len(CODEMAP_DOC):].lstrip(" :") if e["doc"].startswith(CODEMAP_DOC) else e["doc"]
        print(f"    {e['kind']}, {trust_text(e)}" + (f": {_clean(doc, 150)}" if doc else ""))
    print(f"{len(hits)} match(es) in {1000 * (t4 - t3):.2f} ms.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
