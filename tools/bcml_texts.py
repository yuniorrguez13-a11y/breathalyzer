#!/usr/bin/env python3
"""Merge three kinds of BCML 3 logs into the game's own files: texts.json (the language packs' texts), drops.json
(actor drop tables) and shop.aamp (shop data). bnp.py calls these; nothing here writes a file or runs anything, and
the game's files are only read.

    import bcml_texts as bt
    log = bt.read_log("texts.json", data, where="logs/")        # a Log: what it holds, and what it left out
    log = bt.combine("texts.json", [base, option, next_mod])    # in order: base, options, then mods low -> high
    res = bt.apply("texts.json", log, game)                     # game: Savage Wind's extracted folder
    res["files"]   # {"Pack/Bootup_USen.pack//Message/Msg_USen.product.ssarc": bytes, ...} ("//": inside a pack)
    res["sizes"]   # size table entries for the rebuilt files (never below the game's)
    res["notes"]   # what was left out, for "Needs attention"

Each kind also has one function that works on bytes alone: merge_texts(msg_archive, lang, logs),
merge_drops(bdrop, tables), merge_shop(bshop, diffs). A log that can't be read at all raises LogError; a damaged
part of one is left out and named in the notes.

The rules follow BCML 3's mergers (read for the formats and rules only; the code here is our own), with these safer
choices:
- texts: a language the mod has no texts for gets the mod's new texts from its fallback language (BCML's order: the
  same language, the same language part as "en" of "USen", any English, the first), but keeps the game's wording of
  texts the mod changed, unless the language part matches (BCML copies the changed ones too, in English); a new
  message file gets attributes when it is an EventFlowMsg file, as the game's are (BCML has it the other way round);
  a text whose controls' size fields run past its end is refused (it would break the message box).
- drops: an "UNDERRIDE_CONST" item the game's table doesn't have is left out (BCML would write the text as a number).
- shops: an item the log adds with only some of its values gets the rest from the game's own copy, else it is left
  out; a table whose items are all removed stays, empty (BCML stops with an error there).

Texts (msyt's JSON, the library BCML uses): {lang: {"<folder>/<file>.msyt": {label: {"contents": [{"text": ...} |
{"control": {"kind": ...}}], "attributes": ...}}}}. Each control becomes a text tag: 0x0E, u16 group, u16 type,
u16 size, then its values. MSBT (Switch): "MsgStdBn", FF FE, UTF-16, version 3, sections LBL1 (labels in hash slots:
hash = hash * 0x492 + byte, slot = hash % slots), ATR1 (EventFlowMsg files: an offset per text to a UTF-16
attribute; the others: none) and TXT2 (the texts), each padded to 16 bytes with 0xAB.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import struct
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aamp  # noqa: E402
import bnp  # noqa: E402
import ukmm  # noqa: E402
import wiiu_convert as wc  # noqa: E402

LOGS = {"texts.json": "texts", "drops.json": "drop tables", "shop.aamp": "shop data"}
LANG_RE = re.compile(r"[A-Z]{2}[a-z]{2}")
BOOTUP_LANG_RE = re.compile(r"Bootup_([A-Z]{2}[a-z]{2})\.pack")
UNDERRIDE = "UNDERRIDE_CONST"              # BCML's "use the game's value" in a drop table
CONTENT_PREFIXES = ("01007EF00011E000/romfs/", "content/")
DLC_PREFIXES = ("01007EF00011F001/romfs/", "aoc/0010/", "aoc/")
TITLE_PACK = "Pack/TitleBG.pack"
MAX_LOG = 64 << 20


class LogError(Exception):
    """A log that can't be used at all. The message is for the player."""


class TextError(ValueError):
    """A part of a log or a game file that can't be used (the caller names it and leaves it out)."""


@dataclass
class Log:
    """A parsed log (or several combined): `value` is what it changes, `notes` what was left out of it."""
    name: str
    value: object
    notes: list[str] = field(default_factory=list)


def _some(items: list[str], n: int = 3) -> str:
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def _int(v, lo: int, hi: int, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise TextError(f"{what} isn't a whole number from {lo} to {hi}")
    return v


def _u16(v, what: str) -> bytes:
    return struct.pack("<H", _int(v, 0, 0xFFFF, what))


def _u32(v, what: str) -> bytes:
    return struct.pack("<I", _int(v, 0, 0xFFFFFFFF, what))


def _byte_list(v, what: str, n: int | None = None) -> bytes:
    if not isinstance(v, list) or (n is not None and len(v) != n):
        raise TextError(f"{what} isn't a list of {n if n is not None else 'some'} bytes")
    return bytes(_int(x, 0, 255, what) for x in v)


def _utf16(v, what: str) -> bytes:
    if not isinstance(v, str):
        raise TextError(f"{what} isn't text")
    return v.encode("utf-16-le", "surrogatepass")


def _obj(v, what: str) -> dict:
    if not isinstance(v, dict):
        raise TextError(f"{what} isn't an object")
    return v


def _one_key(v, what: str, allowed) -> tuple[str, object]:
    if not isinstance(v, dict) or len(v) != 1 or next(iter(v)) not in allowed:
        raise TextError(f"{what} isn't one of {', '.join(allowed)}")
    return next(iter(v.items()))


def _pair(v, what: str) -> tuple[object, object]:
    if not isinstance(v, list) or len(v) != 2:
        raise TextError(f"{what} isn't a [type, values] pair")
    return v[0], v[1]


# ------------------------------------------------------------------------------------------------------------ MSBT

MSBT_MAGIC = b"MsgStdBn"
LABEL_MUL = 0x492


def label_slot(label: str, slots: int) -> int:
    """The LBL1 slot a label sits in."""
    h = 0
    for b in label.encode("utf-8"):
        h = (h * LABEL_MUL + b) & 0xFFFFFFFF
    return h % slots


@dataclass
class Msbt:
    """A message file: labels and texts by text index (texts as UTF-16LE bytes, each ending with its 0)."""
    slots: int                 # LBL1 hash slots
    labels: list[str]
    attr_size: int             # ATR1 entry size: 4 (a UTF-16 attribute per text) or 0 (none)
    attrs: list[str]           # per text, when attr_size is 4
    texts: list[bytes]


def _section_spans(data: bytes) -> dict[bytes, tuple[int, int]]:
    count = struct.unpack_from("<H", data, 0x0E)[0]
    out: dict[bytes, tuple[int, int]] = {}
    p = 0x20
    for _ in range(count):
        if p + 16 > len(data):
            raise TextError("a section header runs past the end")
        magic, size = bytes(data[p:p + 4]), struct.unpack_from("<I", data, p + 4)[0]
        body, end = p + 16, p + 16 + size
        if end > len(data):
            raise TextError(f"section {magic!r} runs past the end")
        if magic not in (b"LBL1", b"ATR1", b"TXT2") or magic in out:
            raise TextError(f"section {magic!r} isn't one BotW uses")
        out[magic] = (body, end)
        p = (end + 15) & ~15
    if len(out) != 3:
        raise TextError("it lacks one of LBL1, ATR1, TXT2")
    return out


def _utf16z(data: bytes, at: int, end: int) -> str:
    q = at
    while q + 2 <= end and data[q:q + 2] != b"\0\0":
        q += 2
    if q + 2 > end:
        raise TextError("an attribute has no end")
    return data[at:q].decode("utf-16-le", "surrogatepass")


def msbt_read(data: bytes) -> Msbt:
    """A Switch MSBT as BotW's are (anything else raises TextError, and the file is kept as it is)."""
    if len(data) < 0x20 or data[:8] != MSBT_MAGIC:
        raise TextError("not an MSBT")
    if data[8:10] != b"\xff\xfe":
        raise TextError("a big-endian (Wii U) MSBT")
    if data[0x0C] != 1 or data[0x0D] != 3:
        raise TextError(f"MSBT encoding {data[0x0C]} version {data[0x0D]}, not UTF-16 version 3")
    if struct.unpack_from("<I", data, 0x12)[0] != len(data):
        raise TextError("its size field doesn't match")
    try:
        secs = _section_spans(data)
        b, e = secs[b"TXT2"]
        count = struct.unpack_from("<I", data, b)[0]
        if 4 + 4 * count > e - b:
            raise TextError("TXT2 is cut short")
        offs = struct.unpack_from(f"<{count}I", data, b + 4)
        bounds = sorted(set(offs) | {e - b})
        nxt = {a: c for a, c in zip(bounds, bounds[1:])}
        texts = []
        for o in offs:
            if not 4 + 4 * count <= o < e - b:
                raise TextError("a text offset points outside TXT2")
            t = bytes(data[b + o:b + nxt[o]])
            if len(t) % 2 or t[-2:] != b"\0\0":
                raise TextError("a text has no end")
            texts.append(t)
        b, e = secs[b"LBL1"]
        slots = struct.unpack_from("<I", data, b)[0]
        if 4 + 8 * slots > e - b or (count and not slots):
            raise TextError("LBL1 is cut short")
        labels: list[str | None] = [None] * count
        for k in range(slots):
            n, off = struct.unpack_from("<II", data, b + 4 + 8 * k)
            q = b + off
            for _ in range(n):
                if q >= e or q + 5 + data[q] > e:
                    raise TextError("a label runs past LBL1")
                ln = data[q]
                idx = struct.unpack_from("<I", data, q + 1 + ln)[0]
                if idx >= count or labels[idx] is not None:
                    raise TextError("a label points at a missing or shared text")
                labels[idx] = data[q + 1:q + 1 + ln].decode("utf-8")
                q += 5 + ln
        if any(x is None for x in labels):
            raise TextError("a text has no label")
        b, e = secs[b"ATR1"]
        acount, asize = struct.unpack_from("<II", data, b)
        if acount != count or asize not in (0, 4):
            raise TextError(f"ATR1 with {acount} entries of {asize} bytes isn't supported")
        attrs = []
        if asize == 4:
            for o in struct.unpack_from(f"<{count}I", data, b + 8):
                attrs.append(_utf16z(data, b + o, e))
    except (struct.error, UnicodeDecodeError) as ex:
        raise TextError(f"damaged MSBT ({ex})") from None
    return Msbt(slots, labels, asize, attrs, texts)


def _section(magic: bytes, body: bytes) -> bytes:
    out = magic + struct.pack("<I", len(body)) + bytes(8) + body
    return out + b"\xab" * (-len(out) % 16)


def msbt_build(m: Msbt) -> bytes:
    """The MSBT bytes, laid out as the game's are (a game file read and built again comes out the same)."""
    n = len(m.texts)
    if len(m.labels) != n or (m.attr_size == 4 and len(m.attrs) != n) or m.slots < 1:
        raise TextError("labels, attributes and texts don't line up")
    slots: list[list[int]] = [[] for _ in range(m.slots)]
    for i, lab in enumerate(m.labels):
        slots[label_slot(lab, m.slots)].append(i)
    table, recs = bytearray(struct.pack("<I", m.slots)), bytearray()
    first = 4 + 8 * m.slots
    for s in slots:
        table += struct.pack("<II", len(s), first + len(recs))
        for i in s:
            name = m.labels[i].encode("utf-8")
            if not 0 < len(name) < 256:
                raise TextError(f"label {m.labels[i][:40]!r} is empty or longer than 255 bytes")
            recs += bytes([len(name)]) + name + struct.pack("<I", i)
    if m.attr_size == 4:
        offs, strs = [], bytearray()
        for a in m.attrs:
            offs.append(8 + 4 * n + len(strs))
            strs += a.encode("utf-16-le", "surrogatepass") + b"\0\0"
        atr = struct.pack(f"<II{n}I", n, 4, *offs) + strs
    else:
        atr = struct.pack("<II", n, 0)
    offs, body = [], bytearray()
    for t in m.texts:
        offs.append(4 + 4 * n + len(body))
        body += t
    txt = struct.pack(f"<I{n}I", n, *offs) + body
    secs = _section(b"LBL1", bytes(table + recs)) + _section(b"ATR1", atr) + _section(b"TXT2", bytes(txt))
    return (MSBT_MAGIC + b"\xff\xfe\0\0\x01\x03" + struct.pack("<HHI", 3, 0, 0x20 + len(secs)) + bytes(10)
            + secs)


# ------------------------------------------------------------------------------------------- msyt controls <-> tags
# msyt's JSON for a control is serde's: {"kind": "<snake_case variant>", ...fields}. "raw" holds one group word
# (zero ... five, two_hundred_one) with that group's own shape; the friendly kinds stand for one tag each.

COLOURS = {"red": 0, "light_green1": 1, "blue": 2, "grey": 3, "light_green4": 4, "orange": 5, "light_grey": 6}
FONTS = {"normal": 0xFFFF, "hylian": 0}
PAUSES = {"short": 0, "long": 1, "longer": 2}
LOCALISATIONS = {"gender": 5, "plural": 6}
ICONS = {"l_stick_forward": 0, "l_stick_back": 1, "l_stick_left": 2, "l_stick_right": 3, "r_stick_vertical": 4,
         "r_stick_horizontal": 5, "d_pad_up": 6, "d_pad_down": 7, "d_pad_left": 8, "d_pad_right": 9, "y": 13,
         "b": 17, "l": 20, "r": 21, "plus": 23, "minus": 24, "right_arrow": 25, "left_arrow": 26, "up_arrow": 27,
         "l_stick_press": 33, "r_stick_press": 34, "gamepad": 36}
ICON_CARRY = {"a": (10, 11), "x": (12, 37, 38), "zl": (14, 15)}      # (and "unknown": any other value)
WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
GROUP_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "two_hundred_one": 201}
TWO_ONE_FIELD = {3, 4, 7, 8, 10, 13}                 # group 2 types msyt reads as one value (others: a variable)
NINE_MARK = bytes([255, 255, 0, 0] * 3)


def _sized(params: bytes) -> bytes:
    return struct.pack("<H", len(params)) + params


def _tag(group: int, kind: int, body: bytes) -> bytes:
    """A tag from what follows its type: the size field, then the values msyt keeps for it. (The size may cover
    more: msyt reads a ruby tag, 0.0, as three values and keeps the rest of it as the text after it.)"""
    return struct.pack("<HHH", 0x0E, group, kind) + body


def _check_tags(text: bytes) -> None:
    """Every tag in a text (without its closing 0) ends inside it, as the game reads them by their size field."""
    q = 0
    while q < len(text):
        if struct.unpack_from("<H", text, q)[0] != 0x0E:
            q += 2
            continue
        if q + 8 > len(text):
            raise TextError("a control is cut short")
        g, t, size = struct.unpack_from("<HHH", text, q + 2)
        q += 8 + size
        if q > len(text) or size % 2:
            raise TextError(f"control {g}.{t}: its size field runs past the text")


def _f(v: dict, key: str, what: str):
    if key not in v:
        raise TextError(f"{what} lacks {key}")
    return v[key]


def _fields16(v, what: str, keys) -> bytes:
    v = _obj(v, what)
    return b"".join(_u16(_f(v, k, what), f"{what} {k}") for k in keys)


def _icon_value(icon) -> int:
    if isinstance(icon, str):
        if icon not in ICONS:
            raise TextError(f"unknown icon {icon!r}")
        return ICONS[icon]
    k, v = _one_key(icon, "icon", (*ICON_CARRY, "unknown"))
    return _int(v, 0, 255, f"icon {k}")


def _loc_kind(v) -> int:
    if isinstance(v, str):
        if v not in LOCALISATIONS:
            raise TextError(f"unknown localisation kind {v!r}")
        return LOCALISATIONS[v]
    _, n = _one_key(v, "localisation kind", ("unknown",))
    return _int(n, 0, 0xFFFF, "localisation kind")


def _loc_body(strings) -> bytes:
    if not isinstance(strings, list):
        raise TextError("localisation options aren't a list")
    return _sized(b"".join(_sized(_utf16(s, "a localisation option")) for s in strings))


def _raw_one(sub: str, v) -> tuple[int, bytes]:
    t = WORDS.index(sub)
    what = f"raw control 1.{t}"
    v = _obj(v, what)
    if t in (0, 1, 3):
        return t, _u16(_f(v, "field_1", what), what) + _u32(_f(v, "field_2", what), what)
    if t == 2:
        return t, _u16(_f(v, "field_1", what), what)
    if t in (4, 5, 6):
        n = t - 1                                        # field_1 .. field_n are u16, then two bytes
        return t, (_fields16(v, what, [f"field_{i}" for i in range(1, n + 1)])
                   + _byte_list(_f(v, f"field_{n + 1}", what), what, 2))
    if t == 7:
        return t, _u16(_f(v, "field_1", what), what) + _byte_list(_f(v, "field_2", what), what, 2)
    if t == 8:
        marks, values = _f(v, "unknown_1", what), _f(v, "field_1", what)
        if not isinstance(marks, list) or not isinstance(values, list):
            raise TextError(f"{what} isn't in msyt's shape")
        params = (b"".join(_byte_list(x, what, 4) for x in marks) + b"".join(_u16(x, what) for x in values)
                  + _byte_list(_f(v, "field_2", what), what, 4))
        return t, _sized(params)
    if t == 9:
        strings = _f(v, "strings", what)
        if not isinstance(strings, list) or len(strings) != 4:
            raise TextError(f"{what} doesn't have 4 strings")
        params = bytearray()
        if v.get("unknown_1") is not None:
            params += _byte_list(v["unknown_1"], what, 12)
        for s in strings:
            s = _obj(s, what)
            params += _u16(_f(s, "field_1", what), what) + _sized(_utf16(_f(s, "string", what), what))
        params += _fields16(v, what, ("field_3", "field_4"))
        if v.get("unknown_2") is not None:
            params += _byte_list(v["unknown_2"], what, 12)
        params += _byte_list(_f(v, "field_6", what), what, 2)
        return t, _sized(bytes(params))
    return t, _fields16(v, what, ("field_1", "field_2")) + _byte_list(_f(v, "field_3", what), what, 2)   # 10


def _raw_control(c: dict) -> bytes:
    groups = [k for k in c if k != "kind"]
    if len(groups) != 1 or groups[0] not in GROUP_WORDS:
        raise TextError("a raw control without one known group (zero ... five, two_hundred_one)")
    g = groups[0]
    v = c[g]
    if g == "zero":
        sub, val = _one_key(v, "raw control 0", WORDS[:5])
        t = WORDS.index(sub)
        keys = ("field_1", "field_2", "field_3")[:{0: 3, 4: 1}.get(t, 2)]
        return _tag(0, t, _fields16(val, f"raw control 0.{t}", keys))
    if g == "one":
        sub, val = _one_key(v, "raw control 1", WORDS)
        t, body = _raw_one(sub, val)
        return _tag(1, t, body)
    if g == "two":
        sub, val = _one_key(v, "raw control 2", ("one_field", "variable"))
        t, val = _pair(val, "raw control 2")
        t = _int(t, 0, 0xFFFF, "raw control 2 type")
        what = f"raw control 2.{t}"
        if sub == "one_field":
            return _tag(2, t, _fields16(val, what, ("field_1",)))
        val = _obj(val, what)
        return _tag(2, t, _u16(_f(val, "field_1", what), what) + _sized(_utf16(_f(val, "string", what), what))
                    + _u16(_f(val, "field_3", what), what))
    if g == "three":
        v = _obj(v, "raw control 3")
        t = _int(_f(v, "field_1", "raw control 3"), 0, 0xFFFF, "raw control 3 type")
        return _tag(3, t, _sized(_byte_list(_f(v, "field_2", "raw control 3"), "raw control 3")))
    if g == "four":
        sub, val = _one_key(v, "raw control 4", WORDS[:4])
        t = WORDS.index(sub)
        what = f"raw control 4.{t}"
        val = _obj(val, what)
        if t in (0, 2):
            return _tag(4, t, _u16(_f(val, "field_1", what), what) + _sized(_utf16(_f(val, "string", what), what)))
        if t == 1:
            return _tag(4, t, _sized(_byte_list(_f(val, "field_1", what), what)))
        return _tag(4, t, _fields16(val, what, ("field_1",)))
    if g == "five":
        v = _obj(v, "raw control 5")
        t = _int(_f(v, "field_1", "raw control 5"), 0, 0xFFFF, "raw control 5 type")
        return _tag(5, t, _u16(_f(v, "field_2", "raw control 5"), "raw control 5"))
    sub, val = _one_key(v, "raw control 201", ("dynamic", "one_field", "localisation"))
    t, val = _pair(val, "raw control 201")
    val = _obj(val, "raw control 201")
    if sub == "localisation":
        return _tag(201, _loc_kind(t), _loc_body(_f(val, "strings", "raw control 201")))
    t = _int(t, 0, 0xFFFF, "raw control 201 type")
    what = f"raw control 201.{t}"
    if sub == "dynamic":
        return _tag(201, t, _u16(_f(val, "len", what), what) + _byte_list(_f(val, "field_2", what), what))
    return _tag(201, t, _fields16(val, what, ("field_1",)))


def encode_control(c) -> bytes:
    """One msyt control as tag bytes (little-endian). Raises TextError for anything msyt wouldn't have written."""
    c = _obj(c, "a control")
    kind = c.get("kind")
    if kind == "raw":
        return _raw_control(c)
    if kind == "set_colour":
        colour = c.get("colour")
        if colour not in COLOURS:
            raise TextError(f"unknown colour {colour!r}")
        return _tag(0, 3, _sized(struct.pack("<H", COLOURS[colour])))
    if kind == "reset_colour":
        return _tag(0, 3, _sized(b"\xff\xff"))
    if kind == "pause":
        if "frames" in c:
            return _tag(1, 0, _sized(_u32(c["frames"], "pause frames")))
        if c.get("length") not in PAUSES:
            raise TextError(f"unknown pause {c.get('length', c)!r}")
        return _tag(5, PAUSES[c["length"]], _sized(b""))
    if kind == "icon":
        return _tag(1, 7, _sized(bytes([_icon_value(_f(c, "icon", "icon")), 0xCD])))
    if kind == "variable":
        return _tag(2, _int(_f(c, "variable_kind", "variable"), 0, 0xFFFF, "variable kind"),
                    _sized(_sized(_utf16(_f(c, "name", "variable"), "variable name")) + b"\0\0"))
    if kind == "choice":
        labels = _f(c, "choice_labels", "choice")
        if not isinstance(labels, list) or not 2 <= len(labels) <= 4:
            raise TextError("a choice needs 2 to 4 labels")
        body = (_u16(_f(c, "unknown", "choice"), "choice") + b"".join(_u16(x, "choice label") for x in labels)
                + bytes([_int(_f(c, "selected_index", "choice"), 0, 255, "choice"),
                         _int(_f(c, "cancel_index", "choice"), 0, 255, "choice")]))
        return _tag(1, len(labels) + 2, body)
    if kind == "single_choice":
        return _tag(1, 10, _sized(_u16(_f(c, "label", "single choice"), "single choice") + b"\x01\xcd"))
    if kind == "sound":
        return _tag(3, 1, _sized(_byte_list(_f(c, "unknown", "sound"), "sound")))
    if kind == "sound2":
        return _tag(4, 1, _sized(_byte_list(_f(c, "unknown", "sound2"), "sound2")))
    if kind == "animation":
        return _tag(4, 2, _sized(_sized(_utf16(_f(c, "name", "animation"), "animation name"))))
    if kind == "text_size":
        return _tag(0, 2, _sized(_u16(_f(c, "percent", "text size"), "text size")))
    if kind == "auto_advance":
        return _tag(1, 3, _sized(_u32(_f(c, "frames", "auto advance"), "auto advance")))
    if kind == "localisation":
        return _tag(201, _loc_kind(_f(c, "localisation_kind", "localisation")),
                    _loc_body(_f(c, "options", "localisation")))
    if kind == "font":
        font = c.get("font_kind")
        if font not in FONTS:
            raise TextError(f"unknown font {font!r}")
        return _tag(0, 1, _sized(struct.pack("<H", FONTS[font])))
    raise TextError(f"unknown control kind {kind!r}")


def encode_contents(contents) -> bytes:
    """msyt's "contents" list as one TXT2 text (UTF-16LE, with its closing 0)."""
    if not isinstance(contents, list):
        raise TextError("contents isn't a list")
    out = bytearray()
    for c in contents:
        if not isinstance(c, dict) or len(c) != 1:
            raise TextError("a contents item isn't {\"text\": ...} or {\"control\": ...}")
        (k, v), = c.items()
        if k == "text":
            if not isinstance(v, str) or "\x0e" in v:
                raise TextError("a text item isn't plain text")
            out += _utf16(v, "text")
        elif k == "control":
            out += encode_control(v)
        else:
            raise TextError(f"unknown contents item {k!r}")
    _check_tags(bytes(out))
    return bytes(out) + b"\0\0"


class _Reader:
    def __init__(self, data: bytes, at: int):
        self.d, self.q = data, at

    def u16(self) -> int:
        if self.q + 2 > len(self.d):
            raise TextError("a control runs past its text")
        v = struct.unpack_from("<H", self.d, self.q)[0]
        self.q += 2
        return v

    def u32(self) -> int:
        return self.u16() | self.u16() << 16

    def raw(self, n: int) -> list[int]:
        if self.q + n > len(self.d):
            raise TextError("a control runs past its text")
        self.q += n
        return list(self.d[self.q - n:self.q])

    def str16(self) -> str:
        return bytes(self.raw(self.u16())).decode("utf-16-le", "surrogatepass")


def _decode_one(r: _Reader, t: int):
    if t in (0, 1, 3):
        f1, f2 = r.u16(), r.u32()
        if f1 == 4 and t == 0:
            return {"kind": "pause", "frames": f2}
        if f1 == 4 and t == 3:
            return {"kind": "auto_advance", "frames": f2}
        return {"kind": "raw", "one": {WORDS[t]: {"field_1": f1, "field_2": f2}}}
    if t == 2:
        return {"kind": "raw", "one": {"two": {"field_1": r.u16()}}}
    if t in (4, 5, 6):
        f1, labels, b = r.u16(), [r.u16() for _ in range(t - 2)], r.raw(2)
        return {"kind": "choice", "choice_labels": labels, "selected_index": b[0], "cancel_index": b[1],
                "unknown": f1}
    if t == 7:
        f1, b = r.u16(), r.raw(2)
        if f1 == 2 and b[1] == 205:
            names = {v: k for k, v in ICONS.items()}
            if b[0] in names:
                return {"kind": "icon", "icon": names[b[0]]}
            word = next((k for k, vs in ICON_CARRY.items() if b[0] in vs), "unknown")
            return {"kind": "icon", "icon": {word: b[0]}}
        return {"kind": "raw", "one": {"seven": {"field_1": f1, "field_2": b}}}
    if t == 8:
        n = r.u16()
        if n < 4:
            raise TextError("control 1.8 is too short")
        body = r.raw(n - 4)
        marks = []
        while body[:4] == [255, 255, 0, 0]:
            marks.append(body[:4])
            body = body[4:]
        values = [body[i] | body[i + 1] << 8 for i in range(0, len(body) - 1, 2)]
        return {"kind": "raw", "one": {"eight": {"unknown_1": marks, "field_1": values, "field_2": r.raw(4)}}}
    if t == 9:
        n = r.u16()
        end = r.q + n
        out: dict = {"unknown_1": None}
        if bytes(r.d[r.q:r.q + 12]) == NINE_MARK:
            out["unknown_1"] = r.raw(12)
        out["strings"] = [{"field_1": r.u16(), "string": r.str16()} for _ in range(4)]
        out["field_3"], out["field_4"] = r.u16(), r.u16()
        out["unknown_2"] = r.raw(12) if end - r.q > 12 and bytes(r.d[r.q:r.q + 12]) == NINE_MARK else None
        out["field_6"] = r.raw(2)
        return {"kind": "raw", "one": {"nine": out}}
    if t == 10:
        f1, f2, b = r.u16(), r.u16(), r.raw(2)
        if f1 == 4 and b == [1, 205]:
            return {"kind": "single_choice", "label": f2}
        return {"kind": "raw", "one": {"ten": {"field_1": f1, "field_2": f2, "field_3": b}}}
    raise TextError(f"unknown control 1.{t}")


def _decode_control(r: _Reader):
    g = r.u16()
    if g in (3, 5):                                  # (msyt reads these groups' type as their first field)
        t = r.u16()
        if g == 3:
            vals = r.raw(r.u16())
            return {"kind": "sound", "unknown": vals} if t == 1 else \
                {"kind": "raw", "three": {"field_1": t, "field_2": vals}}
        f2 = r.u16()
        if f2 == 0 and t in PAUSES.values():
            return {"kind": "pause", "length": next(k for k, v in PAUSES.items() if v == t)}
        return {"kind": "raw", "five": {"field_1": t, "field_2": f2}}
    t = r.u16()
    if g == 0:
        if t > 4:
            raise TextError(f"unknown control 0.{t}")
        vals = [r.u16() for _ in range({0: 3, 4: 1}.get(t, 2))]
        if t == 1 and vals[0] == 2 and vals[1] in FONTS.values():
            return {"kind": "font", "font_kind": next(k for k, v in FONTS.items() if v == vals[1])}
        if t == 2 and vals[0] == 2:
            return {"kind": "text_size", "percent": vals[1]}
        if t == 3 and vals[0] == 2 and vals[1] == 0xFFFF:
            return {"kind": "reset_colour"}
        if t == 3 and vals[0] == 2 and vals[1] in COLOURS.values():
            return {"kind": "set_colour", "colour": next(k for k, v in COLOURS.items() if v == vals[1])}
        return {"kind": "raw", "zero": {WORDS[t]: {f"field_{i + 1}": x for i, x in enumerate(vals)}}}
    if g == 1:
        return _decode_one(r, t)
    if g == 2:
        if t in TWO_ONE_FIELD:
            return {"kind": "raw", "two": {"one_field": [t, {"field_1": r.u16()}]}}
        f1, s, f3 = r.u16(), r.str16(), r.u16()
        if f3 == 0:
            return {"kind": "variable", "variable_kind": t, "name": s}
        return {"kind": "raw", "two": {"variable": [t, {"field_1": f1, "string": s, "field_3": f3}]}}
    if g == 4:
        if t in (0, 2):
            f1, s = r.u16(), r.str16()
            return {"kind": "animation", "name": s} if t == 2 else \
                {"kind": "raw", "four": {"zero": {"field_1": f1, "string": s}}}
        if t == 1:
            return {"kind": "sound2", "unknown": r.raw(r.u16())}
        if t == 3:
            return {"kind": "raw", "four": {"three": {"field_1": r.u16()}}}
        raise TextError(f"unknown control 4.{t}")
    if g == 201:
        if t == 0:
            n = r.u16()
            return {"kind": "raw", "two_hundred_one": {"dynamic": [0, {"len": n, "field_2": r.raw(n)}]}}
        if t <= 4:
            return {"kind": "raw", "two_hundred_one": {"one_field": [t, {"field_1": r.u16()}]}}
        if t <= 8:
            end = r.u16() + r.q
            opts = []
            while r.q < end:
                opts.append(r.str16())
            kind = next((k for k, v in LOCALISATIONS.items() if v == t), None) or {"unknown": t}
            return {"kind": "localisation", "localisation_kind": kind, "options": opts}
        raise TextError(f"unknown control 201.{t}")
    raise TextError(f"unknown control group {g}")


def decode_text(raw: bytes) -> list:
    """A TXT2 text as msyt's "contents" (the reverse of encode_contents; for tests and checks): like msyt, each tag
    is read as its known shape, and what its size field covers beyond that stays text."""
    if len(raw) % 2 or raw[-2:] != b"\0\0":
        raise TextError("a text without its closing 0")
    data = raw[:-2]
    out: list = []
    q, start = 0, 0
    while q < len(data):
        if struct.unpack_from("<H", data, q)[0] != 0x0E:
            q += 2
            continue
        if q > start:
            out.append({"text": data[start:q].decode("utf-16-le", "surrogatepass")})
        r = _Reader(data, q + 2)
        out.append({"control": _decode_control(r)})
        q = start = r.q
    if q > start:
        out.append({"text": data[start:q].decode("utf-16-le", "surrogatepass")})
    return out


# ------------------------------------------------------------------------------------------------------- texts.json

@dataclass
class Entry:
    text: bytes                # TXT2 bytes
    attr: str | None           # None: the log gives none


def _msbt_name(fname) -> str:
    if not isinstance(fname, str):
        raise TextError("a file name isn't text")
    n = fname.replace("\\", "/").lstrip("/")
    if n.lower().endswith(".msyt"):
        n = n[:-5] + ".msbt"
    parts = n.split("/")
    if not n.lower().endswith(".msbt") or any(p in ("", ".", "..") for p in parts) or ":" in n:
        raise TextError(f"{fname!r} isn't a message file name")
    return n


def _entry(label, entry) -> Entry:
    if not isinstance(label, str) or not 0 < len(label.encode("utf-8")) < 256 or "\0" in label:
        raise TextError("a label isn't 1 to 255 bytes of text")
    entry = _obj(entry, "a text")
    attr = entry.get("attributes")
    if attr is not None and not isinstance(attr, str):
        raise TextError("its attributes aren't text")
    return Entry(encode_contents(entry.get("contents")), attr)


def read_texts_log(data: bytes, where: str = "texts.json") -> Log:
    """BCML 3's logs/texts.json: {lang: {msbt name: {label: Entry}}}. A text that can't be encoded is left out."""
    if len(data) > MAX_LOG:
        raise LogError(f"{where} is too big to be a texts log.")
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        raise LogError(f"{where} can't be read ({e}): the mod's texts are left out.") from None
    if not isinstance(raw, dict) or not all(isinstance(v, dict) for v in raw.values()):
        raise LogError(f"{where} isn't a BCML 3 texts log (language -> files): the mod's texts are left out.")
    value: dict[str, dict[str, dict[str, Entry]]] = {}
    bad: list[str] = []
    for lang, files in raw.items():
        if not LANG_RE.fullmatch(lang):
            bad.append(f"{lang!r}: not a language like USen")
            continue
        for fname, labels in files.items():
            try:
                name = _msbt_name(fname)
                if not isinstance(labels, dict):
                    raise TextError("its labels aren't an object")
            except TextError as e:
                bad.append(f"{lang} {fname}: {e}")
                continue
            for label, entry in labels.items():
                try:
                    value.setdefault(lang, {}).setdefault(name, {})[label] = _entry(label, entry)
                except (TextError, RecursionError) as e:
                    bad.append(f"{lang} {name} {str(label)[:40]}: {e}")
    notes = [f"{where}: {len(bad)} part(s) can't be used and are left out ({_some(bad, 2)})"] if bad else []
    return Log("texts.json", value, notes)


def pick_language(lang: str, langs: list[str]) -> tuple[str | None, bool]:
    """(the log language a game language takes its texts from, whether its changed texts count too): BCML's order
    (the same language, the same language part, any English, the first); changed texts only from the first two."""
    if lang in langs:
        return lang, True
    for x in langs:
        if x[2:4] == lang[2:4]:
            return x, True
    for x in langs:
        if x[2:4] == "en":
            return x, False
    return (langs[0], False) if langs else (None, False)


def _texts_values(logs) -> list[dict]:
    out = []
    for x in logs:
        v = x.value if isinstance(x, Log) else x
        out.extend(v if isinstance(v, list) else [v])
    return out


def merge_texts(msg: bytes, lang: str, logs: list, stats: dict | None = None) -> tuple[bytes, list[str]]:
    """The game's Message/Msg_<lang>.product.ssarc (Yaz0 or not) with the logs' texts for that language, in order
    (later wins per label). Returns (the archive, Yaz0 again when it was: the same object when nothing changed;
    notes). stats gets "kept" (changed texts kept as the game's: no text in this language), "sources" (log
    languages used), "old_len"/"new_len" (the archive's size before and after, uncompressed)."""
    stats = {} if stats is None else stats
    stats.update(kept=0, sources=set())
    notes: list[str] = []
    yaz0 = msg[:4] == b"Yaz0"
    try:
        raw, align = bnp.unyaz0(msg) if yaz0 else (msg, 0)
        arc = bnp.Blob(raw)
        files = arc.open()
    except (wc.FormatError, struct.error, IndexError) as e:
        raise TextError(f"the game's message archive can't be read ({e})") from None
    stats["old_len"] = stats["new_len"] = len(raw)
    parsed: dict[str, Msbt | None] = {}
    bad_files: list[str] = []

    def game_msbt(name: str) -> Msbt | None:
        if name not in parsed:
            try:
                parsed[name] = msbt_read(files[name][0])
            except TextError as e:
                parsed[name] = None
                bad_files.append(f"{name} ({e})")
        return parsed[name]

    wanted: dict[str, dict[str, Entry]] = {}
    for log in _texts_values(logs):
        src, full = pick_language(lang, list(log))
        if src is None:
            continue
        stats["sources"].add(src)
        for name, labels in log[src].items():
            have = game_msbt(name) if name in files else None
            game_labels = set(have.labels) if have is not None else set()
            if name in files and have is None:
                continue
            for label, e in labels.items():
                if not full and label in game_labels:
                    stats["kept"] += 1
                    continue
                prev = wanted.setdefault(name, {}).get(label)
                if e.attr is None and prev is not None and prev.attr is not None:
                    e = Entry(e.text, prev.attr)
                wanted[name][label] = e
    dropped_attrs = 0
    changed = 0
    for name, labels in wanted.items():
        if name in files:
            m = copy.deepcopy(game_msbt(name))
            index = {x: i for i, x in enumerate(m.labels)}
            for label, e in labels.items():
                i = index.get(label)
                if i is None:
                    index[label] = len(m.labels)
                    m.labels.append(label)
                    m.texts.append(e.text)
                    if m.attr_size == 4:
                        m.attrs.append(e.attr or "")
                    dropped_attrs += bool(e.attr) and m.attr_size != 4
                    continue
                m.texts[i] = e.text
                if e.attr is not None and m.attr_size == 4:
                    m.attrs[i] = e.attr
                dropped_attrs += bool(e.attr) and m.attr_size != 4
            align_of = files[name][1]
        else:
            size = 4 if name.startswith("EventFlowMsg/") or any(e.attr for e in labels.values()) else 0
            m = Msbt(max(1, len(labels)), list(labels), size,
                     [e.attr or "" for e in labels.values()] if size else [], [e.text for e in labels.values()])
            align_of = 8
        try:
            new = msbt_build(m)
        except TextError as e:
            bad_files.append(f"{name} ({e})")
            continue
        if name not in files or new != files[name][0]:
            arc.set(name, new, align_of)
            changed += 1
    if bad_files:
        notes.append(f"texts.json: {len(bad_files)} message file(s) can't be rebuilt and keep the game's texts "
                     f"({_some(bad_files, 2)})")
    if dropped_attrs:
        notes.append(f"texts.json: {dropped_attrs} text attribute(s) left out: their message files have none")
    if not changed:
        return msg, notes
    new_raw = arc.build_raw()
    stats["new_len"] = len(new_raw)
    return (ukmm.yaz0(new_raw, align) if yaz0 else new_raw), notes


# ------------------------------------------------------------------------------------------------------ paths, game

def norm_path(path) -> tuple[list[str], bool]:
    """A log's file path ("<pack>//<inner>//<file>", BCML's content prefix allowed) as its "//" parts and whether it
    is a DLC file. Raises TextError for a path that isn't a safe RomFS path."""
    if not isinstance(path, str) or not path:
        raise TextError("a file path isn't text")
    p = path.replace("\\", "/")
    dlc = False
    low = p.lower()
    for pre in CONTENT_PREFIXES:
        if low.startswith(pre.lower()):
            p = p[len(pre):]
            break
    else:
        for pre in DLC_PREFIXES:
            if low.startswith(pre.lower()):
                p, dlc = p[len(pre):], True
                break
    parts = p.split("//")
    for part in parts:
        bits = part.split("/")
        if not part or part.startswith("/") or any(b in ("", ".", "..") for b in bits) or ":" in part:
            raise TextError(f"{path!r} isn't a safe game file path")
    return parts, dlc


def _sarc_index(path: str) -> dict[str, tuple[int, int]]:
    """The files of an uncompressed SARC on disk (name -> (start, end) in the file), reading only its header."""
    try:
        with open(path, "rb") as f:
            head = f.read(0x14)
            if len(head) < 0x14 or head[:4] != b"SARC" or head[6:8] != b"\xff\xfe":
                return {}
            doff = struct.unpack_from("<I", head, 0x0C)[0]
            if doff > 64 << 20:
                return {}
            f.seek(0)
            hdr = f.read(doff)
        count = struct.unpack_from("<H", hdr, 0x1A)[0]
        names_at = 0x20 + 16 * count + 8
        out = {}
        for k in range(count):
            _h, attr, start, end = struct.unpack_from("<IIII", hdr, 0x20 + 16 * k)
            if attr >> 24:
                at = names_at + (attr & 0xFFFFFF) * 4
                stop = hdr.find(b"\0", at)
                out[hdr[at:stop].decode("utf-8", "replace")] = (doff + start, doff + end)
        return out
    except (OSError, struct.error):
        return {}


class _Game:
    """The game's files a log names, read on demand (TitleBG.pack's actors by seeking, not loading the pack)."""

    def __init__(self, game: str):
        self.romfs = os.path.join(game, "romfs")
        self._title: dict[str, tuple[int, int]] | None = None
        self.rstb = bnp.load_game_rstb(game)

    def title(self) -> dict[str, tuple[int, int]]:
        if self._title is None:
            self._title = _sarc_index(os.path.join(self.romfs, *TITLE_PACK.split("/")))
        return self._title

    def top(self, rel: str) -> tuple[bytes | None, list[str]]:
        """(bytes of a RomFS file or None, where its changes go). An actor pack TitleBG.pack holds is used from
        there, as BCML does (the game loads the resident pack's copy)."""
        if rel.startswith("Actor/Pack/") and rel in self.title():
            a, b = self.title()[rel]
            with open(os.path.join(self.romfs, *TITLE_PACK.split("/")), "rb") as f:
                f.seek(a)
                return f.read(b - a), [TITLE_PACK, rel]
        p = os.path.join(self.romfs, *rel.split("/"))
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read(), [rel]
        return None, [rel]

    def nested(self, parts: list[str]) -> tuple[bytes | None, str]:
        """(the game's bytes of a file inside packs or None, the target path for the integrator)."""
        data, where = self.top(parts[0])
        target = "//".join(where + parts[1:])
        blob = bnp.Blob(data) if data is not None else None
        for name in parts[1:]:
            if blob is None or not blob.is_sarc():
                return None, target
            blob = blob.child(name)
        if blob is None:
            return None, target
        return blob.orig, target

    def size(self, inner: str, old: bytes | None, old_len: int, new_len: int) -> dict[str, int]:
        if old is None or self.rstb is None:
            return {}
        name = bnp.canon(inner, old)
        g = self.rstb.get(name)
        return {} if g is None else {name: bnp.needed_size(g, old_len, new_len, False)}


# ------------------------------------------------------------------------------------------------------- drops.json

DROP_KEYS = (("repeat_num_min", "RepeatNumMin"), ("repeat_num_max", "RepeatNumMax"),
             ("approach_type", "ApproachType"), ("occurrence_speed_type", "OccurrenceSpeedType"))
I32 = (-(1 << 31), (1 << 31) - 1)


def _whole(v, what: str) -> int:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return _int(v, *I32, what)


def _probability(v, what: str):
    if v == UNDERRIDE:
        return v
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 3.4e38:
        raise TextError(f"{what} isn't a number")
    return float(v)


def _drop_table(name, tab) -> dict:
    if not isinstance(name, str) or not name:
        raise TextError("a table without a name")
    tab = _obj(tab, f"table {name}")
    out = {k: _whole(tab[k], f"{name} {k}") for k, _ in DROP_KEYS if k in tab and tab[k] != UNDERRIDE}
    items = tab.get("items")
    if not isinstance(items, dict):
        raise TextError(f"table {name} has no items list")
    out["items"] = {}
    for item, p in items.items():
        if not item:
            raise TextError(f"table {name} has an item without a name")
        out["items"][item] = _probability(p, f"{name} {item}")
    return out


def read_drops_log(data: bytes, where: str = "drops.json") -> Log:
    """BCML 3's logs/drops.json: {"<actor pack>//Actor/DropTable/<name>.bdrop": {table: {...}}}, keyed here by the
    path's parts joined with "//" (prefix taken off). A damaged file entry is left out whole."""
    if len(data) > MAX_LOG:
        raise LogError(f"{where} is too big to be a drops log.")
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        raise LogError(f"{where} can't be read ({e}): the mod's drop table changes are left out.") from None
    if not isinstance(raw, dict):
        raise LogError(f"{where} isn't a BCML drops log: the mod's drop table changes are left out.")
    value: dict[str, dict] = {}
    bad, dlc = [], []
    for path, tables in raw.items():
        try:
            parts, is_dlc = norm_path(path)
            if is_dlc:
                dlc.append(parts[-1])
                continue
            if not isinstance(tables, dict) or not tables:
                raise TextError("no tables")
            value["//".join(parts)] = {t: _drop_table(t, tab) for t, tab in tables.items()}
        except TextError as e:
            bad.append(f"{str(path).split('//')[-1]}: {e}")
    notes = []
    if bad:
        notes.append(f"{where}: {len(bad)} drop table file(s) damaged and left out, the game's are used "
                     f"({_some(bad, 2)})")
    if dlc:
        notes.append(f"{where}: {len(dlc)} DLC drop table file(s) not merged ({_some(dlc, 2)}): DLC files aren't "
                     "supported here")
    return Log("drops.json", value, notes)


def _read_drop(data: bytes) -> tuple[dict, str, int]:
    """A .bdrop as {table: {key: int, "items": {name: float}}} in the game's order, its type name, pio version."""
    try:
        root = aamp.load(data)
        header = root.objects[aamp.name_hash("Header")]
        tables = {}
        for i in range(1, header.get_value("TableNum", 0) + 1):
            name = header.get_value(f"Table{i:02}")
            obj = root.objects[aamp.name_hash(name)]
            t = {k: obj.get_value(p) for k, p in DROP_KEYS}
            t["items"] = {}
            columns = obj.get_value("ColumnNum", 0)
            if not isinstance(columns, int) or not 0 <= columns <= len(obj) // 2:
                # (each column is two parameters: a count the object can't hold is damaged, not looped over)
                raise TextError(f"table {name} says it has {columns} items")
            for j in range(1, columns + 1):
                t["items"][obj.get_value(f"ItemName{j:02}")] = obj.get_value(f"ItemProbability{j:02}")
            if None in t.values() or None in t["items"] or None in t["items"].values():
                raise TextError(f"table {name} lacks a value")
            tables[name] = t
    except (aamp.AampError, KeyError, TypeError, AttributeError, struct.error) as e:
        raise TextError(f"the game's drop table can't be read ({e})") from None
    return tables, aamp.type_name(data) or "xml", struct.unpack_from("<I", data, 0x10)[0]


def merge_drops(game: bytes | None, logs: list[dict]) -> tuple[bytes, list[str]]:
    """A .bdrop with one file's logged tables, in order (a later table wins whole), as BCML merges them: only the
    logged tables stay, each with its logged items (the game's order, then new ones); "UNDERRIDE_CONST" takes the
    game's value. game: the game's .bdrop, or None when the game has none (the log alone)."""
    tables: dict[str, dict] = {}
    for d in logs:
        tables.update(d)
    gt, type_name, version = _read_drop(game) if game else ({}, "xml", 0)
    notes: list[str] = []
    order = [t for t in gt if t in tables] + [t for t in tables if t not in gt]
    root = aamp.PList()
    header = aamp.PObject()
    root.objects[aamp.name_hash("Header")] = header
    under, incomplete, kept = [], [], []
    for t in order:
        g, log = gt.get(t), tables[t]
        fields = {k: (log[k] if k in log else g[k] if g else None) for k, _ in DROP_KEYS}
        if None in fields.values():
            incomplete.append(t)
            continue
        gi = g["items"] if g else {}
        items = {n: (p if log["items"][n] == UNDERRIDE else log["items"][n]) for n, p in gi.items()
                 if n in log["items"]}
        for n, p in log["items"].items():
            if n in gi:
                continue
            if p == UNDERRIDE:
                under.append(f"{t} {n}")
                continue
            items[n] = p
        obj = aamp.PObject()
        for k, pname in DROP_KEYS:
            obj.set(pname, aamp.INT, fields[k])
        obj.set("ColumnNum", aamp.INT, len(items))
        for j, (n, p) in enumerate(items.items(), 1):
            obj.set(f"ItemName{j:02}", aamp.STRING64, n)
            obj.set(f"ItemProbability{j:02}", aamp.F32, float(p))
        kept.append(t)
        root.objects[aamp.name_hash(t)] = obj
    header.set("TableNum", aamp.INT, len(kept))
    for i, t in enumerate(kept, 1):
        header.set(f"Table{i:02}", aamp.STRING64, t)
    if under:
        notes.append(f"{len(under)} drop(s) say \"use the game's value\" for an item the game's table lacks and are "
                     f"left out ({_some(under, 2)})")
    if incomplete:
        notes.append(f"{len(incomplete)} new drop table(s) lack their drop counts and are left out "
                     f"({_some(incomplete, 2)})")
    try:
        return aamp.dump(root, type_name, version), notes
    except aamp.AampError as e:
        raise TextError(str(e)) from None


# -------------------------------------------------------------------------------------------------------- shop.aamp

SHOP_KEYS = (("ItemName", aamp.STRING64), ("ItemNum", aamp.INT), ("ItemAdjustPrice", aamp.INT),
             ("ItemLookGetFlg", aamp.BOOL), ("ItemAmount", aamp.INT))
FILENAMES, ADDITIONS, REMOVALS, TABLE_NAMES = (aamp.name_hash(n) for n in
                                               ("Filenames", "Additions", "Removals", "TableNames"))


def read_shop_log(data: bytes, where: str = "shop.aamp") -> Log:
    """BCML 3's logs/shop.aamp (binary AAMP): {"<path parts joined by //>": the file's PList (Additions,
    Removals)}."""
    if data[:4] != b"AAMP":
        text = data[:200].lstrip().lower()
        why = "a text AAMP: only BCML's binary logs are read" if text.startswith((b"!io", b"version", b"param_root",
                                                                                  b"type")) else "not an AAMP file"
        raise LogError(f"{where} can't be read ({why}): the mod's shop changes are left out.")
    try:
        root = aamp.load(data)
    except (aamp.AampError, struct.error, UnicodeDecodeError, IndexError) as e:
        raise LogError(f"{where} can't be read ({e}): the mod's shop changes are left out.") from None
    names = root.objects.get(FILENAMES)
    if names is None:
        raise LogError(f"{where} has no file list (Filenames): the mod's shop changes are left out.")
    value: dict[str, aamp.PList] = {}
    bad, dlc = [], []
    for p in names.values():
        if p.type not in aamp.STRING_TYPES or not isinstance(p.value, str):
            continue
        try:
            parts, is_dlc = norm_path(p.value)
        except TextError as e:
            bad.append(str(e))
            continue
        if is_dlc:
            dlc.append(parts[-1])
            continue
        diff = root.lists.get(aamp.name_hash(p.value))
        if diff is None:
            bad.append(f"{parts[-1]}: no changes for it")
            continue
        key = "//".join(parts)
        if key in value:
            _merge_plist(value[key], diff)
        else:
            value[key] = diff
    notes = []
    if bad:
        notes.append(f"{where}: {len(bad)} shop file(s) damaged and left out, the game's are used "
                     f"({_some(bad, 2)})")
    if dlc:
        notes.append(f"{where}: {len(dlc)} DLC shop file(s) not merged ({_some(dlc, 2)}): DLC files aren't "
                     "supported here")
    return Log("shop.aamp", value, notes)


def _merge_plist(a: aamp.PList, b: aamp.PList) -> None:
    """BCML's merge_plists: lists and objects are unions; an object both have gets b's parameters over a's."""
    for k, sub in b.lists.items():
        if k in a.lists:
            _merge_plist(a.lists[k], sub)
        else:
            a.lists[k] = copy.deepcopy(sub)
    for k, obj in b.objects.items():
        if k in a.objects:
            a.objects[k].update(copy.deepcopy(obj))
        else:
            a.objects[k] = copy.deepcopy(obj)


def _shop_param(key: str, ptype: int, p: aamp.Param) -> aamp.Param:
    v = p.value
    if ptype == aamp.STRING64:
        if p.type not in aamp.STRING_TYPES or not isinstance(v, str) or not 0 < len(v.encode()) < 64:
            raise TextError(f"{key} isn't a name")
    elif ptype == aamp.BOOL:
        if p.type not in (aamp.BOOL, aamp.INT, aamp.U32) or isinstance(v, (str, float, list)):
            raise TextError(f"{key} isn't true or false")
        v = bool(v)
    else:
        if p.type not in (aamp.INT, aamp.U32) or isinstance(v, bool) or not isinstance(v, int) \
                or not I32[0] <= v <= I32[1]:
            raise TextError(f"{key} isn't a whole number")
    return aamp.Param(ptype, v)


def _read_shop(data: bytes) -> tuple[list[list], str, int]:
    """A .bshop as [[table name, {crc32(item name): {key: Param}}]] in the game's order, type name, pio version."""
    try:
        root = aamp.load(data)
        header = root.objects[aamp.name_hash("Header")]
        tables = []
        for p in header.values():
            if p.type not in aamp.STRING_TYPES:
                continue
            obj = root.objects[aamp.name_hash(p.value)]
            items: dict[int, dict] = {}
            j = 1
            while aamp.name_hash(f"ItemName{j:03}") in obj:
                item = {k: _shop_param(k, t, obj[aamp.name_hash(f"{k}{j:03}")]) for k, t in SHOP_KEYS}
                items.setdefault(aamp.name_hash(item["ItemName"].value), item)
                j += 1
            tables.append([p.value, items])
    except (aamp.AampError, KeyError, struct.error) as e:
        raise TextError(f"the game's shop file can't be read ({e})") from None
    return tables, aamp.type_name(data) or "xml", struct.unpack_from("<I", data, 0x10)[0]


def merge_shop(game: bytes | None, diffs: list) -> tuple[bytes, list[str]]:
    """A .bshop with one file's logged changes, in order (BCML: unions, a later item's values win): removals first,
    then additions (an existing item gets the given values, a new one goes last); ItemSort and ColumnNum renumbered.
    game: the game's .bshop, or None when the game has none."""
    diff = aamp.PList()
    for d in diffs:
        _merge_plist(diff, d)
    tables, type_name, version = _read_shop(game) if game else ([], "xml", 0)
    gitems = {(aamp.name_hash(n), ih): copy.deepcopy(it) for n, items in tables for ih, it in items.items()}
    by_hash = {aamp.name_hash(t[0]): t for t in tables}
    notes, bad, emptied = [], [], []
    rem = diff.lists.get(REMOVALS)
    if rem is not None:
        for th, tl in rem.lists.items():
            t = by_hash.get(th)
            if t is None:
                continue
            for ih in tl.objects:
                t[1].pop(ih, None)
            if not t[1]:
                emptied.append(t[0])
    add = diff.lists.get(ADDITIONS)
    if add is not None:
        names = {}
        for k, p in (add.objects.get(TABLE_NAMES) or {}).items():
            if isinstance(p.value, str) and p.value:
                names[k] = p.value
        for th, tl in add.lists.items():
            t = by_hash.get(th)
            if t is None:
                if th not in names:
                    bad.append(f"a new table without a name (0x{th:08x})")
                    continue
                t = by_hash[th] = [names[th], {}]
                tables.append(t)
            for ih, obj in tl.objects.items():
                try:
                    given = {k: _shop_param(k, pt, obj[aamp.name_hash(k)]) for k, pt in SHOP_KEYS
                             if aamp.name_hash(k) in obj}
                except TextError as e:
                    bad.append(f"{t[0]}: {e}")
                    continue
                if ih in t[1]:
                    t[1][ih].update(given)
                    continue
                item = {**gitems.get((th, ih), {}), **given}
                if len(item) != len(SHOP_KEYS):
                    bad.append(f"{t[0]}: a new item without all its values "
                               f"({given['ItemName'].value if 'ItemName' in given else f'0x{ih:08x}'})")
                    continue
                t[1][ih] = item
    root = aamp.PList()
    header = aamp.PObject()
    root.objects[aamp.name_hash("Header")] = header
    header.set("TableNum", aamp.INT, len(tables))
    for i, (name, items) in enumerate(tables, 1):
        header.set(f"Table{i:02}", aamp.STRING64, name)
        obj = aamp.PObject()
        obj.set("ColumnNum", aamp.INT, len(items))
        for j, item in enumerate(items.values(), 1):
            obj.set(f"ItemSort{j:03}", aamp.INT, j - 1)
            for k, _ in SHOP_KEYS:
                obj[aamp.name_hash(f"{k}{j:03}")] = item[k]
        root.objects[aamp.name_hash(name)] = obj
    still_empty = [n for n in emptied if not by_hash[aamp.name_hash(n)][1]]
    if bad:
        notes.append(f"{len(bad)} shop change(s) left out ({_some(bad, 2)})")
    if still_empty:
        notes.append(f"shop table(s) {_some(still_empty, 2)} lost all their items and are kept empty")
    try:
        return aamp.dump(root, type_name, version), notes
    except aamp.AampError as e:
        raise TextError(str(e)) from None


# --------------------------------------------------------------------------------------------- the module contract

def read_log(name: str, data: bytes, where: str = "") -> Log:
    """One log file (name: its file name in logs/, where: e.g. "options/x/logs/") -> its Log. Raises LogError."""
    n = name.lower()
    if n == "texts.json":
        return read_texts_log(data, where + name)
    if n == "drops.json":
        return read_drops_log(data, where + name)
    if n == "shop.aamp":
        return read_shop_log(data, where + name)
    raise LogError(f"{where}{name} isn't a log this merger knows.")


def combine(name: str, logs: list) -> Log:
    """Several logs of one kind as one, in order: a package's base, its chosen options, then the next package. A list
    inside `logs` is one package's logs (base, options), as the other BCML log modules take them."""
    n = name.lower()
    packages = [list(item) if isinstance(item, (list, tuple)) else [item] for item in logs]
    logs = [x for p in packages for x in p]
    notes = [x for log in logs for x in log.notes]
    if n == "texts.json":
        return Log(n, _texts_values(logs), notes)        # (each log picks its own language per game language)
    if n == "drops.json":
        # (inside one package an option's table merges into the base's, items added or changed, as BCML merges a
        # package's diffs; across mods a later table replaces an earlier one whole)
        out: dict[str, dict] = {}
        for p in packages:
            mine: dict[str, dict] = {}
            for log in p:
                for path, tables in log.value.items():
                    have = mine.setdefault(path, {})
                    for t, tab in tables.items():
                        old = have.get(t)
                        have[t] = tab if old is None else {**old, **tab, "items": {**old["items"], **tab["items"]}}
            for path, tables in mine.items():
                out.setdefault(path, {}).update(tables)
        return Log(n, out, notes)
    if n == "shop.aamp":
        merged: dict[str, aamp.PList] = {}
        for log in logs:
            for path, pl in log.value.items():
                if path not in merged:
                    merged[path] = aamp.PList()
                _merge_plist(merged[path], pl)
        return Log(n, merged, notes)
    raise LogError(f"{name} isn't a log this merger knows.")


def _result() -> dict:
    # (targets: every file the log names, changed or not: a package that ships the file already changed keeps the log,
    # which then changes nothing at install; deploy still has to merge that file between mods)
    return {"files": {}, "sizes": {}, "notes": [], "targets": []}


def _apply_texts(log: Log, game: str, read=None) -> dict:
    res = _result()
    res["notes"] += log.notes
    logs = [v for v in _texts_values([log]) if v]
    if not logs:
        return res
    pack_dir = os.path.join(game, "romfs", "Pack")
    try:
        langs = sorted(m.group(1) for f in os.listdir(pack_dir) if (m := BOOTUP_LANG_RE.fullmatch(f)))
    except OSError:
        langs = []
    if not langs:
        res["notes"].append("texts.json not merged: the game's language packs (Pack/Bootup_XXxx.pack) aren't in "
                            "extracted\\romfs")
        return res
    rstb = bnp.load_game_rstb(game)
    used: set[str] = set()
    kept: list[str] = []
    seen_notes: set[str] = set()
    for lang in langs:
        inner = f"Message/Msg_{lang}.product.ssarc"
        stats: dict = {}
        try:
            with open(os.path.join(pack_dir, f"Bootup_{lang}.pack"), "rb") as f:
                pack = bnp.Blob(f.read())
            entry = pack.open().get(inner)
            if entry is None:
                raise TextError(f"its pack has no {inner}")
            vanilla = entry[0]
            res["targets"].append(f"Pack/Bootup_{lang}.pack//{inner}")
            own = read(f"Pack/Bootup_{lang}.pack//{inner}") if read else None
            old = vanilla if own is None else own
            new, notes = merge_texts(old, lang, logs, stats)
            stats["old_len"] = ukmm.unpacked_size(vanilla)    # (sizes count from the game's copy)
        except (TextError, wc.FormatError, struct.error, IndexError, OSError) as e:
            res["notes"].append(f"texts.json: the {lang} texts can't be merged ({e}); that language keeps the "
                                "game's texts")
            continue
        for x in notes:
            if x not in seen_notes:
                seen_notes.add(x)
                res["notes"].append(x)
        used |= stats.get("sources", set())
        if stats.get("kept"):
            kept.append(lang)
        if new is not old:
            res["files"][f"Pack/Bootup_{lang}.pack//{inner}"] = new
            size_name = f"Message/Msg_{lang}.product.sarc"
            g = rstb.get(size_name) if rstb else None
            if g is not None:
                res["sizes"][size_name] = bnp.needed_size(g, stats["old_len"], stats["new_len"], True)
    if kept:
        res["notes"].append(f"texts.json: the mod has no texts in {len(kept)} of your game's languages "
                            f"({_some(kept, 4)}): they get its new texts but keep the game's wording where the mod "
                            "changed it")
    unused = sorted({x for v in logs for x in v} - used)
    if unused:
        res["notes"].append(f"texts.json: the mod's {_some(unused)} texts aren't used: your game has no such "
                            "language")
    return res


def _apply_aamp(log: Log, game: str, kind: str, read=None) -> dict:
    res = _result()
    res["notes"] += log.notes
    if not log.value:
        return res
    g = _Game(game)
    merge = merge_drops if kind == "drops.json" else merge_shop
    what = "drop table" if kind == "drops.json" else "shop"
    failed = []
    for path, diff in log.value.items():
        parts = path.split("//")
        try:
            vanilla, target = g.nested(parts)
            res["targets"].append(target)   # (named even when merging changes nothing: see bnp.merge_logs)
            own = read(target) if read else None
            old = vanilla if own is None else own
            plain = old
            if old is not None and old[:4] == b"Yaz0":
                plain = bnp.unyaz0(old)[0]
            new, notes = merge(plain, [diff])
        except (TextError, wc.FormatError, struct.error, IndexError, OSError) as e:
            failed.append(f"{parts[-1]}: {e}")
            continue
        res["notes"] += [f"{kind} ({parts[-1]}): {x}" for x in notes]
        if plain is not None and new == plain:
            continue
        out = ukmm.yaz0(new, struct.unpack_from(">I", old, 8)[0]) if old is not None and old[:4] == b"Yaz0" else new
        res["files"][target] = out
        res["sizes"].update(g.size(parts[-1], vanilla, ukmm.unpacked_size(vanilla) if vanilla is not None else 0,
                                   len(new)))
    if failed:
        res["notes"].append(f"{kind}: {len(failed)} {what} file(s) not merged, the game's are used "
                            f"({_some(failed, 2)})")
    return res


def apply(name: str, log: Log, game: str, read=None) -> dict:
    """A combined Log applied to the game's files (game: Savage Wind's extracted folder, only read):
    {"files": {target: bytes}, "sizes": {size table name: size}, "notes": [what was left out]}. A target is a RomFS
    path with "//" inside packs; bytes are final (Yaz0 when the game's file is). The packs around a target are the
    integrator's to rebuild and size. read: an optional function target -> bytes or None giving the copy to merge
    into instead of the game's (the mod's own); sizes still count from the game's copy."""
    n = name.lower()
    if n == "texts.json":
        return _apply_texts(log, game, read)
    if n in ("drops.json", "shop.aamp"):
        return _apply_aamp(log, game, n, read)
    raise LogError(f"{name} isn't a log this merger knows.")
