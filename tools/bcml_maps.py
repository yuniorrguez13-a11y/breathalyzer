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

Its BYML types, reader and writer are tools/bcml_data.py's (U32, byml_load, byml_dump, byml_same here wrap them),
and so is its reader for BCML's BYML text (byml_from_text).
"""
from __future__ import annotations

import math
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bcml_data as bd  # noqa: E402
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
# but refused in the logs here: the game's map files (version 2) never use them. The types, the reader and the
# writer are tools/bcml_data.py's (one copy for every BCML log module).

U32, I64, U64, F64 = bd.U32, bd.I64, bd.U64, bd.F64


def f32(x: float) -> float:
    """x rounded to a 32-bit float (too big: infinity)."""
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def unyaz0(data: bytes) -> bytes:
    return wc.yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data


def byml_load(data: bytes):
    """A Switch (little-endian) BYML document, Yaz0 or not, as typed values."""
    data = unyaz0(data)
    if data[:2] != b"YB":
        raise wc.FormatError("not a Switch (little-endian) BYML document")
    return bd.byml_load(data)


def byml_dump(root, share: bool = True) -> bytes:
    """A Switch BYML document (version 2, little-endian) of typed values. Key and string tables are sorted and
    unique; each container comes after its parent, every node 4-aligned, 8-byte values 8-aligned. share: equal
    containers are written once (as in the game's map files; its quest list shares none). ValueError for what BYML
    can't hold."""
    try:
        return bd.byml_dump(root, share=share)
    except TypeError as e:
        raise ValueError(str(e)) from None


byml_same = bd.same      # deep equality that minds the BYML type (int vs U32 vs bool...); floats as 32-bit floats


# ---------------------------------------------------------------------------------------------------- BYML text
# What oead.byml.to_text writes (libyaml), read by tools/bcml_data.py's reader (the one copy for every BCML log
# module): typed scalars and tags, flow collections libyaml wraps over lines, quoted and folded strings; anchors and
# aliases refused.


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


def byml_from_text(text: str):
    """BCML's BYML text (YAML written by oead) as typed values. Raises LogError when it can't be read."""
    try:
        return bd.from_text(text)
    except bd.LogError as e:
        raise LogError(str(e)) from None
    except (ValueError, TypeError, IndexError, RecursionError) as e:
        raise LogError(f"it can't be read ({e})") from None


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
