#!/usr/bin/env python3
"""Mod settings: the [[settings]] and [[pages]] of a mod.toml (docs/mod-settings.md).

    python tools/mod_settings.py check <mod folder>                     # the problems, or what modhost will read
    python tools/mod_settings.py show [--library L] <mod id>            # the values now
    python tools/mod_settings.py set [--library L] <mod id> <key> <value>
    python tools/mod_settings.py reset [--library L] <mod id>

A mod lists settings in its mod.toml: toggles, sliders, choices (with `sets` presets), keys, texts and buttons, on its
first page or on sub-pages ([[pages]]). Rows can lock (`enable_if`) or hide (`show_if`) as other rows change; the
conditions are checked here and compiled to postfix tokens that modhost only evaluates. A setting applies `live` (the
mod's DLL is told at once), at the next game start (`restart`), or at the next deploy (`deploy`: it picks RomFS option
folders inside the mod).

Files:
    <library>\\settings\\<mod id>.json   the values the player changed (defaults are left out), written with a temp
                                          file and a rename; by modhost while the game runs, and by this tool, the
                                          Mod Manager window and `modmenu.py setting` while it doesn't
    <deploy>\\native\\settings.txt         every library mod's settings, compiled (tab text); modhost reads it at start

The deploy hooks modman calls (each import is lazy there):
    option_romfs(lib, mods)              -> [(mod, folder)]: option folders, each right after its mod's own RomFS
    deploy_values(lib, mods)             -> {mod id: {key: value}} of the `deploy` settings (launch.json, deploy hash)
    write_settings_txt(lib, mods, deploy) -> the path written (or None: no library mod has settings)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field

TOOLS = os.path.dirname(os.path.abspath(__file__))

FORMAT = 1                      # settings\<id>.json "format"
TXT_FORMAT = 1                  # settings.txt's first line: swsettings <TXT_FORMAT>
SETTINGS_DIR = "settings"       # in the library
SETTINGS_TXT = "native/settings.txt"  # in deploy\
TYPES = ("toggle", "slider", "choice", "key", "text", "button")
APPLIES = ("live", "restart", "deploy")
TYPE_CODE = {t: i for i, t in enumerate(TYPES)}       # swmod.h SWMOD_SETTING_*
APPLIES_CODE = {a: i for i, a in enumerate(APPLIES)}  # swmod.h SWMOD_APPLIES_*
SETTINGS_API = 3                # swmod.h: mod API 3 has the settings
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
VALUE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")
MOD_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
FONT_MISSING = set("{}#$%@^`|~")  # the game's Options font has no glyph for these (docs/mod-menu.md)
MAX_SETTINGS = 64
MAX_PAGES = 16
MAX_ROWS_PAGE = 30
MAX_CHOICES = 64
MAX_STEPS = 201                 # a slider is a choice row with one value per step (240 ids a row: docs/mod-menu.md)
ADVISED_STEPS = 21              # held left/right may not repeat (the game check decides): small sliders are easier
ADVISED_ROWS = 12
MAX_TOKENS = 32
MAX_DECIMALS = 6
LIMITS = {"label": 28, "description": 80, "choice label": 20, "unit": 12, "button": 16, "text": 64}
WORDS_MAX = 80                  # the description line
FOLDER_NOT = ("romfs", "content")  # option folders hold RomFS folders directly (romfs_mods' bare layout)

# Key names and their Windows virtual-key codes (the same table is in native/modhost/settings.c). Esc and Backspace
# are left out: key capture uses them.
KEYS: dict[str, int] = {**{f"F{i}": 0x6F + i for i in range(1, 25)},
                        **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
                        **{str(d): 0x30 + d for d in range(10)},
                        **{f"Num{d}": 0x60 + d for d in range(10)},
                        "Space": 0x20, "Tab": 0x09, "Enter": 0x0D, "Insert": 0x2D, "Delete": 0x2E, "Home": 0x24,
                        "End": 0x23, "PageUp": 0x21, "PageDown": 0x22, "Left": 0x25, "Up": 0x26, "Right": 0x27,
                        "Down": 0x28, "Shift": 0x10, "Ctrl": 0x11, "Alt": 0x12}
KEY_OF_VK = {v: k for k, v in KEYS.items()}
KEY_NAMES_HINT = "F1-F24, A-Z, 0-9, Num0-Num9, Space, Tab, Enter, arrows (Up, Down, Left, Right), Shift, Ctrl, Alt"


class SettingsError(Exception):
    """A value or a file can't be used; the message says why, in words a player understands."""


class CondError(Exception):
    pass


# ---------------------------------------------------------------- the schema

@dataclass
class Choice:
    value: str
    label: str
    sets: dict = field(default_factory=dict)   # other settings of the mod -> value (a preset)
    romfs: str | None = None                   # option folder (applies = "deploy"), relative to mod.toml's folder


@dataclass
class Setting:
    key: str
    type: str
    label: str
    description: str = ""
    applies: str = "restart"
    page: str | None = None
    default: object = None
    min: float = 0
    max: float = 0
    step: float = 1
    unit: str = ""
    decimals: int = 0
    choices: list[Choice] = field(default_factory=list)
    max_length: int = 32
    button: str = "Do it"
    romfs: str | None = None                   # toggle: option folder used while it is on
    show_if: str | None = None
    enable_if: str | None = None
    show_rpn: list[str] | None = None
    enable_rpn: list[str] | None = None
    show_ast: tuple | None = None
    enable_ast: tuple | None = None

    @property
    def steps(self) -> int:
        return round_half_away((self.max - self.min) / self.step) + 1 if self.type == "slider" else 0

    def values_count(self) -> int:
        return {"toggle": 2, "slider": self.steps, "choice": len(self.choices)}.get(self.type, 0)

    def choice(self, value: str) -> Choice | None:
        return next((c for c in self.choices if c.value == value), None)


@dataclass
class Page:
    key: str
    label: str
    description: str = ""


@dataclass
class Schema:
    mod_id: str
    name: str
    settings: list[Setting]
    pages: list[Page]
    native: bool = False
    loader_api: int = 1
    root: str | None = None                    # mod.toml's folder (option folders are relative to it)

    @property
    def by_key(self) -> dict[str, Setting]:
        return {s.key: s for s in self.settings}

    def get(self, key: str) -> Setting:
        s = self.by_key.get(key)
        if s is None:
            raise SettingsError(f"{self.name} has no setting {key!r} (it has: {', '.join(self.by_key) or 'none'})")
        return s

    def mods_referenced(self) -> list[str]:
        out: list[str] = []
        for s in self.settings:
            for rpn in (s.show_rpn, s.enable_rpn):
                out += [t[2:] for t in rpn or () if t.startswith("m:")]
        return list(dict.fromkeys(out))


# ---------------------------------------------------------------- the condition language
# expr := or ; or := and ('||' and)* ; and := not ('&&' not)* ; not := '!' not | cmp
# cmp := atom (('=='|'!='|'<'|'<='|'>'|'>=') atom)? ; atom := key | number | 'text' | true | false | on('id') | (expr)
# Postfix tokens: k:<key> (a setting's value, typed by its declaration), n:<number>, s:<text>, b:0/1, m:<mod id>
# (that mod is on), then ! == != < <= > >= && ||. A token list that doesn't evaluate counts as true (never hide or
# lock a setting by mistake).

TOKEN_RE = re.compile(r"\s*(?:(?P<num>-?\d+(?:\.\d+)?)|(?P<str>'[^']*')|(?P<op>==|!=|<=|>=|&&|\|\||[<>!()])"
                      r"|(?P<name>[a-z][a-z0-9_]*))")
CMP_OPS = ("==", "!=", "<", "<=", ">", ">=")
KIND = {"toggle": "b", "slider": "n", "choice": "s", "key": "s", "text": "s"}
KIND_WORD = {"b": "an ON/OFF value", "n": "a number", "s": "a text"}


def tokenize(src: str) -> list[tuple[str, str]]:
    out, i = [], 0
    while i < len(src):
        if not src[i:].strip():
            break
        m = TOKEN_RE.match(src, i)
        if not m or m.end() == i:
            raise CondError(f"can't read {src[i:]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        i = m.end()
    return out


def parse_cond(src: str, settings: dict[str, Setting], own_key: str | None = None) -> tuple[tuple, str]:
    """A condition's syntax tree and its kind (b, n or s). Raises CondError."""
    if not isinstance(src, str) or not src.strip():
        raise CondError("is empty")
    toks = tokenize(src)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else (None, None)

    def take(want=None):
        nonlocal pos
        t = peek()
        if t[0] is None or (want and t[1] != want):
            raise CondError(f"expected {want!r}" if want else "ends too early")
        pos += 1
        return t

    def atom():
        kind, v = take()
        if kind == "num":
            return ("lit", "n", float(v)), "n"
        if kind == "str":
            text = v[1:-1]
            if any(c in text for c in " \t"):
                raise CondError(f"the text {v} has a space: texts in a condition can't")
            return ("lit", "s", text), "s"
        if kind == "op" and v == "(":
            r = or_()
            take(")")
            return r
        if kind == "name":
            if v in ("true", "false"):
                return ("lit", "b", v == "true"), "b"
            if v == "on":
                take("(")
                k2, mod = take()
                if k2 != "str" or not MOD_ID_RE.match(mod[1:-1]):
                    raise CondError("on() needs a mod id in quotes, like on('giruka.hud-pack')")
                take(")")
                return ("on", mod[1:-1]), "b"
            s = settings.get(v)
            if s is None:
                raise CondError(f"there is no setting {v!r} in this mod")
            if v == own_key:
                raise CondError(f"{v!r} can't depend on itself")
            if s.type == "button":
                raise CondError(f"{v!r} is a button: it has no value to test")
            return ("key", v), KIND[s.type]
        raise CondError(f"unexpected {v!r}")

    def cmp_():
        left, lt = atom()
        kind, op = peek()
        if kind == "op" and op in CMP_OPS:
            take()
            right, rt = atom()
            if lt != rt:
                raise CondError(f"compares {KIND_WORD[lt]} with {KIND_WORD[rt]}")
            if op in ("<", "<=", ">", ">=") and lt != "n":
                raise CondError(f"{op} needs numbers (sliders)")
            for a, b in ((left, right), (right, left)):
                if a[0] == "key" and b[0] == "lit" and settings[a[1]].type == "choice":
                    if b[2] not in [c.value for c in settings[a[1]].choices]:
                        raise CondError(f"{b[2]!r} isn't a value of {a[1]!r} "
                                        f"({', '.join(c.value for c in settings[a[1]].choices)})")
                if a[0] == "key" and b[0] == "lit" and settings[a[1]].type == "key" and b[2] not in KEYS \
                        and b[2] != "":
                    raise CondError(f"{b[2]!r} isn't a key name ({KEY_NAMES_HINT})")
            return ("cmp", op, left, right), "b"
        return left, lt

    def not_():
        kind, v = peek()
        if kind == "op" and v == "!":
            take()
            r, _ = not_()
            return ("not", r), "b"
        return cmp_()

    def and_():
        r, t = not_()
        while peek() == ("op", "&&"):
            take()
            r2, _ = not_()
            r, t = ("and", r, r2), "b"
        return r, t

    def or_():
        r, t = and_()
        while peek() == ("op", "||"):
            take()
            r2, _ = and_()
            r, t = ("or", r, r2), "b"
        return r, t

    tree, kind = or_()
    if pos != len(toks):
        raise CondError(f"{toks[pos][1]!r} is left over")
    return tree, kind


def _num_text(x: float) -> str:
    """A number as settings.txt and the tokens write it: no exponent, no trailing zeros ("120", "0.1", "-2.5")."""
    if math.isclose(x, round(x), rel_tol=0, abs_tol=1e-9):
        return str(int(round(x)))
    return f"{x:.9f}".rstrip("0").rstrip(".")


def to_rpn(node: tuple) -> list[str]:
    k = node[0]
    if k == "lit":
        kind, v = node[1], node[2]
        return [f"n:{_num_text(v)}" if kind == "n" else f"b:{int(v)}" if kind == "b" else f"s:{v}"]
    if k == "key":
        return [f"k:{node[1]}"]
    if k == "on":
        return [f"m:{node[1]}"]
    if k == "not":
        return to_rpn(node[1]) + ["!"]
    if k == "cmp":
        return to_rpn(node[2]) + to_rpn(node[3]) + [node[1]]
    return to_rpn(node[1]) + to_rpn(node[2]) + ["&&" if k == "and" else "||"]


def compile_cond(src: str, settings: dict[str, Setting], own_key: str | None = None) -> tuple[list[str], set[str]]:
    """-> (postfix tokens, the keys of this mod it reads). Raises CondError."""
    tree, _ = parse_cond(src, settings, own_key)
    rpn = to_rpn(tree)
    if len(rpn) > MAX_TOKENS:
        raise CondError(f"is too long ({len(rpn)} steps; at most {MAX_TOKENS})")
    return rpn, {t[2:] for t in rpn if t.startswith("k:")}


def _equal(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def eval_rpn(rpn: list[str] | None, values: dict, settings: dict[str, Setting], mods_on: dict[str, bool]) -> bool:
    """values: key -> bool / number / str (as typed values). The same rules as native/modhost/settings.c."""
    if not rpn:
        return True
    st: list = []
    try:
        for t in rpn:
            if t[:2] == "k:":
                s = settings[t[2:]]
                v = values.get(s.key, default_of(s))
                st.append(bool(v) if s.type == "toggle" else float(v) if s.type == "slider" else str(v))
            elif t[:2] == "m:":
                st.append(bool(mods_on.get(t[2:], False)))
            elif t[:2] == "n:":
                st.append(float(t[2:]))
            elif t[:2] == "s:":
                st.append(t[2:])
            elif t[:2] == "b:":
                st.append(t[2:] == "1")
            elif t == "!":
                st.append(not _truthy(st.pop()))
            else:
                b, a = st.pop(), st.pop()
                if t in ("&&", "||"):
                    st.append(_truthy(a) and _truthy(b) if t == "&&" else _truthy(a) or _truthy(b))
                    continue
                if isinstance(a, str) != isinstance(b, str):
                    return True
                if isinstance(a, str):
                    c = (a > b) - (a < b)
                else:
                    a, b = float(a), float(b)
                    c = 0 if _equal(a, b) else (1 if a > b else -1)
                st.append({"==": c == 0, "!=": c != 0, "<": c < 0, "<=": c <= 0, ">": c > 0, ">=": c >= 0}[t])
    except (IndexError, KeyError, ValueError):
        return True
    return _truthy(st[-1]) if len(st) == 1 else True


def _truthy(v) -> bool:
    return v != "" if isinstance(v, str) else bool(v)


# ---------------------------------------------------------------- words for a locked row ("Locked: needs ...")

def _lit_words(s: Setting | None, node: tuple) -> str:
    kind, v = node[1], node[2]
    if s is not None and s.type == "choice":
        c = s.choice(v)
        return c.label if c else str(v)
    if s is not None and s.type == "toggle" or kind == "b":
        return "ON" if v else "OFF"
    if kind == "n":
        return _num_text(v) + (s.unit if s is not None else "")
    return str(v) if v != "" else "none"


FLIP = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}


def cond_words(node: tuple, settings: dict[str, Setting], names: dict[str, str] | None = None) -> str:
    """What a condition asks for, in plain words: "Sprint mode Custom and Sprint speed 100 or more"."""
    names = names or {}
    k = node[0]
    if k == "on":
        return f"{names.get(node[1], node[1])} on"
    if k == "key":
        s = settings[node[1]]
        return f"{s.label} ON" if s.type == "toggle" else f"{s.label} set" if s.type in ("key", "text") \
            else f"{s.label} not 0" if s.type == "slider" else f"{s.label} picked"
    if k == "lit":
        return _lit_words(None, node)
    if k == "not":
        inner = node[1]
        if inner[0] == "key" and settings[inner[1]].type == "toggle":
            return f"{settings[inner[1]].label} OFF"
        if inner[0] == "on":
            return f"{names.get(inner[1], inner[1])} off"
        if inner[0] == "cmp" and inner[1] in ("==", "!="):
            return cond_words(("cmp", "!=" if inner[1] == "==" else "==", inner[2], inner[3]), settings, names)
        return f"not ({cond_words(inner, settings, names)})"
    if k == "cmp":
        op, a, b = node[1], node[2], node[3]
        if a[0] != "key" and b[0] == "key":
            op, a, b = FLIP[op], b, a
        if a[0] == "key":
            s = settings[a[1]]
            if b[0] == "lit":
                val = _lit_words(s, b)
                if s.type == "toggle" and op in ("==", "!="):
                    on = bool(b[2]) == (op == "==")
                    return f"{s.label} {'ON' if on else 'OFF'}"
                return {"==": f"{s.label} {val}", "!=": f"{s.label} not {val}", "<": f"{s.label} under {val}",
                        "<=": f"{s.label} {val} or less", ">": f"{s.label} over {val}",
                        ">=": f"{s.label} {val} or more"}[op]
            other = cond_words(b, settings, names) if b[0] != "key" else settings[b[1]].label
            return {"==": f"{s.label} the same as {other}", "!=": f"{s.label} not the same as {other}",
                    "<": f"{s.label} under {other}", "<=": f"{s.label} at most {other}",
                    ">": f"{s.label} over {other}", ">=": f"{s.label} at least {other}"}[op]
        return f"{cond_words(a, settings, names)} {op} {cond_words(b, settings, names)}"
    sep = " and " if k == "and" else " or "
    return cond_words(node[1], settings, names) + sep + cond_words(node[2], settings, names)


# ---------------------------------------------------------------- checking a mod.toml

def _text_problems(what: str, s, limit: int, errs: list[str], warns: list[str], required: bool = False) -> str:
    if s is None or s == "":
        if required:
            errs.append(f"{what} is missing")
        return ""
    if not isinstance(s, str):
        errs.append(f"{what} must be text in quotes")
        return ""
    if len(s) > limit:
        errs.append(f"{what} {s!r} is {len(s)} characters; the game shows {limit}")
    bad = sorted({c for c in s if ord(c) > 0x7E or ord(c) < 0x20})
    if bad:
        errs.append(f"{what} {s!r} has {''.join(bad)!r}: the game's font shows only plain ASCII")
    missing = sorted({c for c in s if c in FONT_MISSING})
    if missing:
        warns.append(f"{what} {s!r} has {''.join(missing)}: the game's font lacks it (the Mod menu shows another "
                     "sign) unless the symbol fix is on (docs/fonts.md)")
    return s


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _decimals(*xs: float) -> int:
    d = 0
    for x in xs:
        t = _num_text(float(x))
        if "." in t:
            d = max(d, len(t.split(".")[1]))
    return min(d, MAX_DECIMALS)


def _folder_problems(where: str, folder, root: str | None, data_folders: list[str], errs: list[str]) -> str | None:
    """An option folder: a path inside the mod (relative to mod.toml's folder), outside its [data] folders, holding
    RomFS folders directly (Actor/, Pack/, ...; DLC files in aoc/0010/)."""
    if not isinstance(folder, str) or not folder.strip():
        errs.append(f"{where}: romfs must be a folder in quotes, like romfs = \"options/gold\"")
        return None
    v = folder.replace("\\", "/").strip().strip("/")
    parts = [p for p in v.split("/") if p not in ("", ".")]
    if (folder.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", folder) or not parts or ".." in parts
            or any(c in p for p in parts for c in '<>:"|?*')):
        errs.append(f'{where}: romfs = "{folder}" must be a folder inside the mod, like "options/gold"')
        return None
    rel = "/".join(parts)
    low = rel.lower()
    for d in data_folders:
        dl = d.lower().strip("/")
        if dl in ("", ".") or low == dl or low.startswith(dl + "/") or dl.startswith(low + "/"):
            errs.append(f'{where}: romfs = "{rel}" is inside (or holds) the mod\'s own [data] folder "{d}": put '
                        "option folders next to it, like options/gold")
            return None
    if any(p.lower() in FOLDER_NOT for p in parts):
        errs.append(f'{where}: romfs = "{rel}": an option folder holds the RomFS folders (Actor, Pack, ...) itself, '
                    "so its path can't have a romfs or content folder in it")
        return None
    if root is not None:
        full = os.path.join(root, *parts)
        if not os.path.isdir(full):
            errs.append(f'{where}: romfs = "{rel}": no such folder in the mod')
            return rel
        found = False
        for dirpath, dirnames, filenames in os.walk(full):
            bad = [d for d in dirnames if d.lower() in FOLDER_NOT]
            if bad:
                errs.append(f'{where}: romfs = "{rel}" has a {bad[0]} folder in it: put the RomFS folders (Actor, '
                            f'Pack, ...) in {rel} itself')
                return rel
            found = found or bool(filenames)
        if not found:
            errs.append(f'{where}: romfs = "{rel}" has no files')
    return rel


def check(data: dict, root: str | None = None, *, native: bool | None = None,
          loader_api: int | None = None, base_rel: str = "") -> tuple[Schema | None, list[str], list[str]]:
    """Check a mod.toml's [[settings]] and [[pages]] (data: the whole parsed TOML). root: the folder mod.toml is in,
    to check option folders (None: not checked). native / loader_api: from [code] windows and [mod] loader_api when
    not given. Returns (schema or None when there are none or problems, problems, warnings). Problems refuse the
    mod; warnings are only said."""
    errs: list[str] = []
    warns: list[str] = []
    if "settings" not in data and "pages" not in data:
        return None, [], []
    mod = data.get("mod") if isinstance(data.get("mod"), dict) else {}
    code = data.get("code") if isinstance(data.get("code"), dict) else {}
    dat = data.get("data") if isinstance(data.get("data"), dict) else {}
    if native is None:
        native = isinstance(code.get("windows"), str)
    if loader_api is None:
        la = mod.get("loader_api", 1)
        loader_api = la if isinstance(la, int) and not isinstance(la, bool) else 1
    data_folders = [v for v in (dat.get("romfs"), dat.get("dlc_romfs")) if isinstance(v, str)]
    raw_pages = data.get("pages", [])
    raw_sets = data.get("settings", [])
    if not isinstance(raw_pages, list) or not all(isinstance(p, dict) for p in raw_pages):
        errs.append("mod.toml: [[pages]] must be tables, written [[pages]]")
        raw_pages = []
    if not isinstance(raw_sets, list) or not all(isinstance(s, dict) for s in raw_sets):
        errs.append("mod.toml: [[settings]] must be tables, written [[settings]]")
        raw_sets = []
    if len(raw_pages) > MAX_PAGES:
        errs.append(f"mod.toml: {len(raw_pages)} [[pages]]; at most {MAX_PAGES}")
    if len(raw_sets) > MAX_SETTINGS:
        errs.append(f"mod.toml: {len(raw_sets)} [[settings]]; at most {MAX_SETTINGS}")

    pages: list[Page] = []
    for i, p in enumerate(raw_pages):
        k = p.get("key")
        where = f"mod.toml: [[pages]] {k if isinstance(k, str) else f'number {i + 1}'}"
        if not isinstance(k, str) or not KEY_RE.match(k):
            errs.append(f"{where}: key must be lowercase letters, digits and _ (up to 32, starting with a letter)")
            continue
        if any(x.key == k for x in pages):
            errs.append(f"{where}: key {k!r} is there twice")
            continue
        for x in p:
            if x not in ("key", "label", "description"):
                warns.append(f"{where}: {x} isn't something modman knows; ignored")
        label = _text_problems(f"{where} label", p.get("label"), LIMITS["label"], errs, warns, required=True)
        desc = _text_problems(f"{where} description", p.get("description"), LIMITS["description"], errs, warns)
        pages.append(Page(k, label, desc))
    page_keys = {p.key for p in pages}

    known = {"key", "type", "label", "description", "applies", "page", "default", "show_if", "enable_if", "min",
             "max", "step", "unit", "choices", "max_length", "button", "romfs"}
    sets: list[Setting] = []
    for i, r in enumerate(raw_sets):
        k = r.get("key")
        where = f"mod.toml: [[settings]] {k if isinstance(k, str) else f'number {i + 1}'}"
        if not isinstance(k, str) or not KEY_RE.match(k):
            errs.append(f"{where}: key must be lowercase letters, digits and _ (up to 32, starting with a letter)")
            continue
        if any(x.key == k for x in sets):
            errs.append(f"{where}: key {k!r} is there twice")
            continue
        for x in r:
            if x not in known:
                warns.append(f"{where}: {x} isn't something modman knows; ignored")
        ty = r.get("type")
        if ty not in TYPES:
            errs.append(f"{where}: type must be one of {', '.join(TYPES)}")
            continue
        app = r.get("applies", "live" if ty == "button" else "restart")
        if app not in APPLIES:
            errs.append(f"{where}: applies must be one of {', '.join(APPLIES)}")
            app = "restart"
        s = Setting(key=k, type=ty, applies=app,
                    label=_text_problems(f"{where} label", r.get("label"), LIMITS["label"], errs, warns, True),
                    description=_text_problems(f"{where} description", r.get("description"), LIMITS["description"],
                                               errs, warns))
        if ty == "button":
            if app != "live":
                errs.append(f"{where}: a button always applies live (the DLL is told when it is pressed)")
            if not native:
                errs.append(f"{where}: a button needs a DLL that reacts to it ([code] windows)")
        if app in ("live", "restart") and ty != "button" and not native:
            errs.append(f"{where}: applies = {app!r} needs a DLL ([code] windows) that reads it; a mod without one "
                        "uses applies = 'deploy' with option folders")
        if app == "live" and native and loader_api < SETTINGS_API:
            errs.append(f"{where}: applies = 'live' needs [mod] loader_api = {SETTINGS_API} (the DLL reads settings "
                        "through the mod API)")
        if app == "deploy" and ty in ("key", "text", "slider", "button"):
            errs.append(f"{where}: a {ty} can't pick files: use a toggle or a choice for applies = 'deploy'")
        page = r.get("page")
        if page is not None:
            if page not in page_keys:
                errs.append(f"{where}: page = {page!r}: there is no [[pages]] with that key")
            else:
                s.page = page
        d = r.get("default")
        if ty == "toggle":
            if d is not None and not isinstance(d, bool):
                errs.append(f"{where}: default must be true or false")
            s.default = bool(d) if isinstance(d, bool) else False
            if r.get("romfs") is not None:
                if app != "deploy":
                    errs.append(f"{where}: romfs (an option folder) needs applies = 'deploy'")
                s.romfs = _folder_problems(where, r["romfs"], root, data_folders, errs)
            elif app == "deploy":
                errs.append(f"{where}: applies = 'deploy' needs romfs = \"<folder used while it is on>\"")
        elif ty == "slider":
            nums = {x: r.get(x) for x in ("min", "max", "step")}
            if not all(_is_num(v) for v in nums.values()):
                errs.append(f"{where}: min, max and step must be numbers")
            elif nums["step"] <= 0 or nums["min"] >= nums["max"]:
                errs.append(f"{where}: needs min < max and step > 0")
            else:
                s.min, s.max, s.step = float(nums["min"]), float(nums["max"]), float(nums["step"])
                n = (s.max - s.min) / s.step
                if abs(n - round(n)) > 1e-6:
                    errs.append(f"{where}: max - min must be a whole number of steps")
                elif s.steps > MAX_STEPS:
                    errs.append(f"{where}: {s.steps} values; at most {MAX_STEPS} (use a bigger step)")
                elif s.steps > ADVISED_STEPS:
                    warns.append(f"{where}: {s.steps} values: holding left or right may not repeat in the game, so "
                                 f"up to {ADVISED_STEPS} values is easiest to use")
                s.decimals = _decimals(s.min, s.step)
                if d is None:
                    d = s.min
                if not _is_num(d) or not on_step(s, float(d)):
                    errs.append(f"{where}: default {d!r} isn't one of the values ({_num_text(s.min)} to "
                                f"{_num_text(s.max)} in steps of {_num_text(s.step)})")
                else:
                    s.default = typed_number(s, float(d))
            s.unit = _text_problems(f"{where} unit", r.get("unit"), LIMITS["unit"], errs, warns)
        elif ty == "choice":
            cs = r.get("choices")
            if not isinstance(cs, list) or not 2 <= len(cs) <= MAX_CHOICES:
                errs.append(f"{where}: choices must be a list of 2 to {MAX_CHOICES}, like choices = "
                            "[{ value = \"fast\", label = \"Fast\" }, ...]")
                cs = cs if isinstance(cs, list) else []
            for c in cs:
                if not isinstance(c, dict):
                    errs.append(f"{where}: each choice must be {{ value = \"...\", label = \"...\" }}")
                    continue
                v = c.get("value")
                if not isinstance(v, str) or not VALUE_RE.match(v):
                    errs.append(f"{where}: choice value {v!r}: lowercase letters, digits, . _ - (up to 32, starting "
                                "with a letter or digit)")
                    continue
                if s.choice(v):
                    errs.append(f"{where}: choice value {v!r} is there twice")
                    continue
                for x in c:
                    if x not in ("value", "label", "sets", "romfs"):
                        warns.append(f"{where}: choice {v}: {x} isn't something modman knows; ignored")
                ch = Choice(v, _text_problems(f"{where} choice {v} label", c.get("label"), LIMITS["choice label"],
                                              errs, warns, True))
                st = c.get("sets")
                if st is not None and not isinstance(st, dict):
                    errs.append(f"{where}: choice {v}: sets must be a table, like sets = {{ speed = 150 }}")
                elif st:
                    ch.sets = dict(st)
                if c.get("romfs") is not None:
                    if app != "deploy":
                        errs.append(f"{where}: choice {v} has romfs (an option folder), so applies must be 'deploy'")
                    ch.romfs = _folder_problems(f"{where} choice {v}", c["romfs"], root, data_folders, errs)
                s.choices.append(ch)
            if app == "deploy" and s.choices and not any(c.romfs for c in s.choices):
                errs.append(f"{where}: applies = 'deploy' needs romfs = \"<folder>\" on the choices that use files")
            if d is None and s.choices:
                d = s.choices[0].value
            if s.choices and not s.choice(d if isinstance(d, str) else ""):
                errs.append(f"{where}: default must be one of {', '.join(c.value for c in s.choices)}")
            s.default = d if isinstance(d, str) else ""
        elif ty == "key":
            if d is None:
                d = ""
            if not isinstance(d, str) or (d != "" and d not in KEYS):
                errs.append(f"{where}: default key {d!r} isn't a key name ({KEY_NAMES_HINT}; \"\" = none)")
                d = ""
            s.default = d
        elif ty == "text":
            ml = r.get("max_length", 32)
            if isinstance(ml, bool) or not isinstance(ml, int) or not 1 <= ml <= LIMITS["text"]:
                errs.append(f"{where}: max_length must be 1 to {LIMITS['text']}")
                ml = 32
            s.max_length = ml
            if d is None:
                d = ""
            if not isinstance(d, str) or len(d) > ml:
                errs.append(f"{where}: default must be text of at most {ml} characters")
                d = ""
            elif d:
                _text_problems(f"{where} default", d, ml, errs, warns)
            s.default = d
        elif ty == "button":
            s.button = _text_problems(f"{where} button", r.get("button"), LIMITS["button"], errs, warns) or "Do it"
            s.default = None
        if ty in ("toggle", "choice") and "unit" in r:
            warns.append(f"{where}: unit is only for sliders; ignored")
        s.show_if = r.get("show_if")
        s.enable_if = r.get("enable_if")
        for which in ("show_if", "enable_if"):
            v = r.get(which)
            if v is not None and (not isinstance(v, str) or not v.strip()):
                errs.append(f"{where}: {which} must be a condition in quotes, like {which} = \"mode == 'custom'\"")
                setattr(s, which, None)
        sets.append(s)

    by_key = {s.key: s for s in sets}
    deps: dict[str, set[str]] = {}
    for s in sets:
        where = f"mod.toml: [[settings]] {s.key}"
        for which in ("show_if", "enable_if"):
            src = getattr(s, which)
            if src is None:
                continue
            try:
                tree, kind = parse_cond(src, by_key, s.key)
                rpn = to_rpn(tree)
                if len(rpn) > MAX_TOKENS:
                    raise CondError(f"is too long ({len(rpn)} steps; at most {MAX_TOKENS})")
            except CondError as e:
                errs.append(f"{where}: {which} = \"{src}\": {e}")
                continue
            if which == "show_if":
                s.show_rpn, s.show_ast = rpn, tree
                deps[s.key] = {t[2:] for t in rpn if t.startswith("k:")}
            else:
                s.enable_rpn, s.enable_ast = rpn, tree
        for c in s.choices:
            for k2, v2 in c.sets.items():
                o = by_key.get(k2)
                if o is None or o.type in ("button", "key", "text") or k2 == s.key:
                    errs.append(f"{where}: choice {c.value} sets {k2!r}, which isn't another toggle, slider or choice "
                                "of this mod")
                    continue
                bad = coerce_problem(o, v2)
                if not bad and o.type == "slider" and not on_step(o, float(v2)):
                    bad = f"not one of its steps ({_num_text(o.min)} to {_num_text(o.max)} in steps of {_num_text(o.step)})"
                if bad:
                    errs.append(f"{where}: choice {c.value} sets {k2} = {v2!r}: {bad}")
                else:
                    c.sets[k2] = coerce(o, v2)

    def loops(k, seen):
        return any(d in seen or loops(d, seen | {d}) for d in deps.get(k, ()))
    for k in deps:
        if loops(k, {k}):
            errs.append(f"mod.toml: [[settings]] {k}: show_if goes round in a circle (a shows when b does, and b "
                        "when a does)")
    rows: dict[str | None, int] = {}
    for s in sets:
        rows[s.page] = rows.get(s.page, 0) + 1
    rows[None] = rows.get(None, 0) + len(pages)  # (each page is a button on the mod's first page)
    for p, n in rows.items():
        name = f"page {p}" if p else "the mod's first page"
        if n > MAX_ROWS_PAGE:
            errs.append(f"mod.toml: {name} has {n} rows; at most {MAX_ROWS_PAGE} (use [[pages]])")
        elif n > ADVISED_ROWS:
            warns.append(f"mod.toml: {name} has {n} rows: pages of up to {ADVISED_ROWS} rows are easiest to read")
    for p in pages:
        if not rows.get(p.key):
            warns.append(f"mod.toml: [[pages]] {p.key} has no settings")
    # Option folders sit next to the mod's own RomFS folder, which romfs_mods maps first: the mod needs one, and a
    # bare one (Actor/, Pack/, ... in a folder not named romfs) must be shallower, or the option would be taken as it.
    folders = [f for s in sets for f in [s.romfs, *(c.romfs for c in s.choices)] if f]
    if folders:
        own = dat.get("romfs")
        own_parts = [p for p in own.replace("\\", "/").split("/") if p not in ("", ".")] if isinstance(own, str) else []
        if not own_parts:
            errs.append("mod.toml: a mod with option folders (romfs = ... in [[settings]]) needs its own RomFS folder "
                        "too: [data] romfs = \"romfs\" (the files it always uses; option folders go next to it, like "
                        "options/gold)")
        elif "romfs" not in [p.lower() for p in own_parts]:
            for f in folders:
                if len(f.split("/")) <= len(own_parts):
                    errs.append(f'mod.toml: option folder "{f}" must be deeper than [data] romfs = "{own}" (like '
                                f'options/{f.split("/")[-1]}), or name the mod\'s own folder romfs')
    if errs:
        return None, errs, warns
    mod_id = mod.get("id") if isinstance(mod.get("id"), str) else ""
    name = mod.get("name") if isinstance(mod.get("name"), str) else mod_id
    return Schema(mod_id, name, sets, pages, native=native, loader_api=loader_api, root=root), [], warns


def manifest_problems(data: dict, root: str | None = None, **kw) -> tuple[list[str], list[str]]:
    """For modman's mod.toml check (add and deploy): (problems, warnings) of the [[settings]] and [[pages]]."""
    _, errs, warns = check(data, root, **kw)
    return errs, warns


# ---------------------------------------------------------------- values

def round_half_away(x: float) -> int:
    """Rounding as native/modhost/settings.c does it (st_round): halves go away from zero."""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def on_step(s: Setting, x: float) -> bool:
    if not (s.min - 1e-9 <= x <= s.max + 1e-9):
        return False
    off = (x - s.min) / s.step
    return abs(off - round(off)) <= 1e-6


def typed_number(s: Setting, x: float):
    """A slider value as stored: an int when the slider has whole steps, else a float rounded to its decimals."""
    i = min(max(round_half_away((x - s.min) / s.step), 0), s.steps - 1)
    v = s.min + i * s.step
    return int(round(v)) if s.decimals == 0 else round(v, s.decimals)


def default_of(s: Setting):
    return s.default


def coerce_problem(s: Setting, v) -> str | None:
    """Why v (a JSON / TOML value or the text a player typed) can't be a value of s; None when it can."""
    try:
        coerce(s, v)
        return None
    except SettingsError as e:
        return str(e)


def coerce(s: Setting, v):
    """v as s's typed value: toggle bool, slider number (snapped to a step), choice its value, key its name, text
    the text. Text forms are taken too ("true", "on", "120", "Fast"). Raises SettingsError."""
    if s.type == "toggle":
        if isinstance(v, bool):
            return v
        t = str(v).strip().lower()
        if t in ("true", "on", "1", "yes"):
            return True
        if t in ("false", "off", "0", "no"):
            return False
        raise SettingsError(f"{s.label} is ON or OFF, not {v!r}")
    if s.type == "slider":
        try:
            x = float(v) if not isinstance(v, bool) else float("nan")
        except (TypeError, ValueError):
            x = float("nan")
        if not math.isfinite(x) or not (s.min - 1e-9 <= x <= s.max + 1e-9):
            raise SettingsError(f"{s.label} goes from {_num_text(s.min)} to {_num_text(s.max)}, not {v!r}")
        return typed_number(s, x)
    if s.type == "choice":
        t = str(v).strip()
        c = s.choice(t) or next((c for c in s.choices if c.label.lower() == t.lower()), None)
        if c is None:
            raise SettingsError(f"{s.label} is one of: {', '.join(f'{c.value} ({c.label})' for c in s.choices)}; "
                                f"not {v!r}")
        return c.value
    if s.type == "key":
        t = str(v).strip()
        if t.lower() in ("", "none"):
            return ""
        hit = next((k for k in KEYS if k.lower() == t.lower()), None)
        if hit is None:
            raise SettingsError(f"{t!r} isn't a key name ({KEY_NAMES_HINT}; none = no key)")
        return hit
    if s.type == "text":
        if not isinstance(v, str):
            raise SettingsError(f"{s.label} is text")
        if len(v) > s.max_length:
            raise SettingsError(f"{s.label} is at most {s.max_length} characters")
        if any(ord(c) < 0x20 or ord(c) > 0x7E for c in v):
            raise SettingsError(f"{s.label}: the game's font shows only plain letters, digits and signs")
        return v
    raise SettingsError(f"{s.label} is a button: it has no value")


def value_text(s: Setting, v) -> str:
    """How the game shows a value: the choice's label, "120 percent", "ON", "F6", the text."""
    if s.type == "toggle":
        return "ON" if v else "OFF"
    if s.type == "slider":
        return f"{float(v):.{s.decimals}f}{s.unit}"
    if s.type == "choice":
        c = s.choice(v)
        return c.label if c else str(v)
    if s.type == "key":
        return v or "None"
    if s.type == "button":
        return s.button
    return str(v)


def text_form(s: Setting, v) -> str:
    """A value as settings.txt and setting_str write it: "true"/"false", "120", "0.5", "fast", "F6", the text."""
    if s.type == "toggle":
        return "true" if v else "false"
    if s.type == "slider":
        return _num_text(float(v))
    return "" if v is None else str(v)


def values_path(library: str, mod_id: str) -> str:
    if not isinstance(mod_id, str) or not MOD_ID_RE.match(mod_id) or mod_id.endswith("."):
        raise SettingsError(f"{mod_id!r} isn't a mod id")
    return os.path.join(library, SETTINGS_DIR, f"{mod_id}.json")


def read_values(library: str, mod_id: str) -> dict:
    """The raw values in <library>\\settings\\<mod id>.json ({} when there is none, or it is damaged)."""
    try:
        with open(values_path(library, mod_id), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    vals = data.get("values") if isinstance(data, dict) else None
    return dict(vals) if isinstance(vals, dict) else {}


def load_values(library: str, schema: Schema, notes: list[str] | None = None) -> dict:
    """Every setting's value now (button: left out): the saved ones over the defaults. A saved value that doesn't fit
    any more (the mod changed) is dropped; notes gets a line for it."""
    raw = read_values(library, schema.mod_id)
    out = {}
    for s in schema.settings:
        if s.type == "button":
            continue
        out[s.key] = s.default
        if s.key in raw:
            try:
                out[s.key] = coerce(s, raw[s.key])
            except SettingsError as e:
                if notes is not None:
                    notes.append(f"{schema.name}: the saved {s.label} isn't used: {e}")
    return out


def _check_game(refuse_if_running: bool) -> None:
    if not refuse_if_running:
        return
    sys.path.insert(0, TOOLS)
    import sw_rebuild as swr  # noqa: E402 (lazy: only for the check)
    if swr.game_running():
        raise SettingsError("Savage Wind is running: change settings in the game's Mod menu, or close the game first")


def save_values(library: str, schema: Schema, values: dict, *, refuse_if_running: bool = True) -> str:
    """Write <library>\\settings\\<mod id>.json with the values that differ from the defaults (keys the schema doesn't
    know stay as they were). A temp file, then a rename. Refused while Savage Wind runs (modhost writes it then)."""
    _check_game(refuse_if_running)
    path = values_path(library, schema.mod_id)
    raw = read_values(library, schema.mod_id)
    known = schema.by_key
    out = {k: v for k, v in raw.items() if k not in known}
    for s in schema.settings:
        if s.type == "button" or s.key not in values:
            continue
        v = coerce(s, values[s.key])
        if v != s.default:
            out[s.key] = v
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{schema.mod_id}.", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"format": FORMAT, "mod": schema.mod_id, "values": out}, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def state(schema: Schema, values: dict, mods_on: dict[str, bool] | None = None) -> dict[str, dict]:
    """Each setting's shown / enabled (its show_if / enable_if with these values)."""
    mods_on = mods_on or {}
    bk = schema.by_key
    return {s.key: {"shown": eval_rpn(s.show_rpn, values, bk, mods_on),
                    "enabled": eval_rpn(s.enable_rpn, values, bk, mods_on)} for s in schema.settings}


def apply_change(schema: Schema, values: dict, key: str, value, mods_on: dict[str, bool] | None = None, *,
                 check_lock: bool = True) -> tuple[dict, list[str]]:
    """values with setting `key` set to value, as the Mod menu sets it: refused when locked, and a choice also sets
    its `sets`. Returns (new values, the keys that changed). Raises SettingsError."""
    s = schema.get(key)
    if s.type == "button":
        raise SettingsError(f"{s.label} is a button: press it in the game")
    if check_lock and not eval_rpn(s.enable_rpn, values, schema.by_key, mods_on or {}):
        raise SettingsError(locked_line(schema, s))
    new = dict(values)
    new[key] = coerce(s, value)
    changed = [key] if new[key] != values.get(key) else []
    if s.type == "choice":
        c = s.choice(new[key])
        for k2, v2 in (c.sets if c else {}).items():
            if new.get(k2) != v2:
                new[k2] = v2
                changed.append(k2)
    return new, changed


def set_value(library: str, schema: Schema, key: str, value, mods_on: dict[str, bool] | None = None, *,
              refuse_if_running: bool = True) -> list[str]:
    """Change one setting in the library (as the Mod menu would) and save. Returns the keys that changed."""
    _check_game(refuse_if_running)
    vals = load_values(library, schema)
    new, changed = apply_change(schema, vals, key, value, mods_on)
    if changed:
        save_values(library, schema, new, refuse_if_running=False)
    return changed


def reset_values(library: str, schema: Schema, *, refuse_if_running: bool = True) -> str:
    """Every setting of the mod back to its default (keys the schema doesn't know stay)."""
    return save_values(library, schema, {s.key: s.default for s in schema.settings if s.type != "button"},
                       refuse_if_running=refuse_if_running)


def words_of(schema: Schema, s: Setting, names: dict[str, str] | None = None) -> str:
    """What s's enable_if needs, in words ("Sprint mode Custom")."""
    return cond_words(s.enable_ast, schema.by_key, names) if s.enable_ast is not None else ""


def locked_line(schema: Schema, s: Setting, names: dict[str, str] | None = None) -> str:
    """The description line of a locked row (at most 80 characters)."""
    if s.enable_ast is None:
        return ""
    line = f"Locked: needs {words_of(schema, s, names)}."
    return line if len(line) <= WORDS_MAX else "Locked: needs other settings changed first."


# ---------------------------------------------------------------- the library (modman) side

def _modman():
    sys.path.insert(0, TOOLS)
    import modman  # noqa: E402 (lazy: modman imports this module lazily too)
    return modman


def manifest_folder(mod_dir: str) -> str | None:
    """The folder a library mod's mod.toml is in (its root, or the one folder that holds everything), or None."""
    if os.path.isfile(os.path.join(mod_dir, "mod.toml")):
        return mod_dir
    mm = _modman()
    rel = mm.find_manifest([r for _, r, _ in mm.walk_files(mod_dir)])
    if rel is None:
        return None
    return os.path.dirname(os.path.join(mod_dir, *rel.split("/")))


def load_schema(mod_dir: str, notes: list[str] | None = None) -> Schema | None:
    """The settings of the mod in mod_dir (a library mod folder, or any mod folder), or None when it has none or its
    mod.toml can't be read or has problems (notes gets them)."""
    folder = manifest_folder(mod_dir)
    if folder is None:
        return None
    try:
        with open(os.path.join(folder, "mod.toml"), "rb") as f:
            data = tomllib.loads(f.read().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError) as e:
        if notes is not None:
            notes.append(f"{os.path.basename(mod_dir)}: mod.toml can't be read: {e}")
        return None
    schema, errs, _ = check(data, folder)
    if errs and notes is not None:
        notes += [f"{os.path.basename(mod_dir)}: {e}" for e in errs]
    if schema is not None and not schema.mod_id:
        return None
    return schema


def library_schemas(lib, notes: list[str] | None = None) -> list[tuple[dict, Schema]]:
    """(library entry, schema) of every library mod with settings, in the library's order."""
    out = []
    for m in lib.mods:
        d = os.path.join(lib.mods_dir, m["id"])
        if os.path.isdir(d):
            s = load_schema(d, notes)
            if s is not None:
                out.append((m, s))
    return out


def mods_on(lib, mods, schemas: list[tuple[dict, Schema]] | None = None) -> dict[str, bool]:
    """Mod id -> on at the next start, for on('id') in conditions: the library id of every mod, and the [mod] id of
    each mod with settings. mods: the enabled library entries."""
    on_ids = {m["id"] for m in mods}
    out = {m["id"]: m["id"] in on_ids for m in lib.mods}
    for m, sch in (library_schemas(lib) if schemas is None else schemas):
        out[sch.mod_id] = m["id"] in on_ids
    return out


def option_romfs(lib, mods) -> list[tuple[dict, str]]:
    """The option folders of the enabled mods' `deploy` settings, as they are now: [(mod, folder)], in the order
    modman puts them in the RomFS plan, each right after its mod's own RomFS (settings in mod.toml order; a later
    one wins a file). mods: the enabled library entries, in deploy order."""
    out: list[tuple[dict, str]] = []
    for m in mods:
        d = os.path.join(lib.mods_dir, m["id"])
        sch = load_schema(d) if os.path.isdir(d) else None
        if sch is None or sch.root is None:
            continue
        vals = load_values(lib.root, sch)
        for s in sch.settings:
            if s.applies != "deploy":
                continue
            folder = s.romfs if s.type == "toggle" and vals.get(s.key) else None
            if s.type == "choice":
                c = s.choice(vals.get(s.key))
                folder = c.romfs if c else None
            if folder:
                full = os.path.join(sch.root, *folder.split("/"))
                if os.path.isdir(full):
                    out.append((m, full))
    return out


def deploy_values(lib, mods) -> dict[str, dict]:
    """{mod id: {key: value}} of the enabled mods' `deploy` settings now (for launch.json and the deploy hash)."""
    out: dict[str, dict] = {}
    on = {m["id"] for m in mods}
    for m, sch in library_schemas(lib):
        if m["id"] not in on:
            continue
        vals = load_values(lib.root, sch)
        dv = {s.key: vals[s.key] for s in sch.settings if s.applies == "deploy"}
        if dv:
            out[sch.mod_id] = dv
    return out


def deploy_pending(lib, launch: dict | None) -> bool:
    """A `deploy` setting differs from what the last deploy used (launch.json "settings"): Play deploys again."""
    used = (launch or {}).get("settings")
    now = deploy_values(lib, lib.enabled())
    if not isinstance(used, dict):
        return bool(now)
    return now != used


def _clean(s) -> str:
    return str(s).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def compile_lines(schema: Schema, *, lib_id: str = "", on: bool = True, dll_names=(), deployed: dict | None = None,
                  names: dict[str, str] | None = None) -> list[str]:
    """One mod's records of settings.txt (without the swsettings / library lines). Tab separated, one record a line:
        mod     <mod id> <library id> <on|off> <name>
        dll     <mod id> <dll file name>
        page    <mod id> <key> <label> <description>
        set     <mod id> <key> <type> <applies> <page|-> <default> <min> <max> <step> <decimals> <max_length>
                <label> <description> <unit or button text>
        choice  <mod id> <key> <value> <label>
        sets    <mod id> <key> <choice value> <other key> <value>
        if      <mod id> <key> <show|enable> <postfix tokens, space separated> <words for a locked row>
        deployed <mod id> <key> <value>            (deploy settings: the value this deploy used)
    Values are in their text form (text_form)."""
    mid = schema.mod_id
    out = [f"mod\t{mid}\t{lib_id or '-'}\t{'on' if on else 'off'}\t{_clean(schema.name)}"]
    out += [f"dll\t{mid}\t{_clean(n)}" for n in dll_names]
    for p in schema.pages:
        out.append(f"page\t{mid}\t{p.key}\t{_clean(p.label)}\t{_clean(p.description)}")
    for s in schema.settings:
        d = text_form(s, s.default) if s.type != "button" else ""
        unit = s.unit if s.type == "slider" else s.button if s.type == "button" else ""
        nums = [_num_text(s.min), _num_text(s.max), _num_text(s.step)] if s.type == "slider" else ["0", "0", "1"]
        out.append("\t".join(["set", mid, s.key, s.type, s.applies, s.page or "-", _clean(d), *nums,
                              str(s.decimals), str(s.max_length if s.type == "text" else 0), _clean(s.label),
                              _clean(s.description), _clean(unit)]))
        for c in s.choices:
            out.append(f"choice\t{mid}\t{s.key}\t{c.value}\t{_clean(c.label)}")
            for k2, v2 in c.sets.items():
                out.append(f"sets\t{mid}\t{s.key}\t{c.value}\t{k2}\t{_clean(text_form(schema.by_key[k2], v2))}")
        if s.show_rpn:
            out.append(f"if\t{mid}\t{s.key}\tshow\t{' '.join(s.show_rpn)}\t-")
        if s.enable_rpn:
            out.append(f"if\t{mid}\t{s.key}\tenable\t{' '.join(s.enable_rpn)}\t{_clean(locked_line(schema, s, names))}")
        if s.applies == "deploy" and deployed is not None and s.key in deployed:
            out.append(f"deployed\t{mid}\t{s.key}\t{_clean(text_form(s, deployed[s.key]))}")
    return out


def settings_txt(lib, mods) -> str | None:
    """deploy\\native\\settings.txt's text: every library mod with settings (mods: the enabled ones), or None."""
    schemas = library_schemas(lib)
    if not schemas:
        return None
    on_ids = {m["id"] for m in mods}
    names = {m["id"]: m.get("name", m["id"]) for m in lib.mods}
    for m, sch in schemas:
        names[sch.mod_id] = m.get("name", sch.name)
    on_now = mods_on(lib, mods, schemas)
    lines = [f"swsettings\t{TXT_FORMAT}", f"library\t{_clean(os.path.abspath(lib.root))}"]
    refs: list[str] = []
    for m, sch in schemas:
        dll = None
        try:
            dll = _dll_name(sch)
        except OSError:
            pass
        vals = load_values(lib.root, sch)
        lines += compile_lines(sch, lib_id=m["id"], on=m["id"] in on_ids, dll_names=[dll] if dll else [],
                               deployed=vals, names=names)
        refs += sch.mods_referenced()
    for r in dict.fromkeys(refs):
        lines.append(f"on\t{r}\t{1 if on_now.get(r) else 0}")
    return "\n".join(lines) + "\n"


def _dll_name(sch: Schema) -> str | None:
    """The file name the mod's DLL has in deploy\\native\\mods (modman copies it by its base name)."""
    if sch.root is None:
        return None
    with open(os.path.join(sch.root, "mod.toml"), "rb") as f:
        data = tomllib.loads(f.read().decode("utf-8-sig"))
    w = (data.get("code") or {}).get("windows") if isinstance(data.get("code"), dict) else None
    return os.path.basename(w.replace("\\", "/")) if isinstance(w, str) else None


def write_settings_txt(lib, mods, deploy) -> str | None:
    """Write deploy\\native\\settings.txt (deploy: modman's DeployDir, which then lists the file, or the deploy
    folder's path). Returns its path, or None when no library mod has settings (nothing is written)."""
    text = settings_txt(lib, mods)
    if text is None:
        return None
    if hasattr(deploy, "add_file"):
        path = deploy.add_file(SETTINGS_TXT)
    else:
        path = os.path.join(str(deploy), *SETTINGS_TXT.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- command line

def _library_arg(a) -> str:
    if a.library:
        return a.library
    env = os.environ.get("SWMOD_LIBRARY")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), "Documents", "SavageWind", "modloader")


def _find_schema(library: str, ref: str) -> Schema:
    mods_dir = os.path.join(library, "mods")
    try:
        names = sorted(os.listdir(mods_dir))
    except OSError:
        raise SettingsError(f"no mod library at {library} (use --library)") from None
    for n in names:
        d = os.path.join(mods_dir, n)
        if not os.path.isdir(d):
            continue
        sch = load_schema(d)
        if sch is not None and ref in (sch.mod_id, n):
            return sch
    raise SettingsError(f"no mod {ref!r} with settings in {library}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check a mod's settings, or show and change their values.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check a mod folder's [[settings]] and show what modhost will read")
    c.add_argument("folder")
    for name in ("show", "set", "reset"):
        p = sub.add_parser(name)
        p.add_argument("--library")
        p.add_argument("mod")
        if name == "set":
            p.add_argument("key")
            p.add_argument("value")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            folder = os.path.abspath(a.folder)
            try:
                with open(os.path.join(folder, "mod.toml"), "rb") as f:
                    data = tomllib.loads(f.read().decode("utf-8-sig"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
                print(f"mod.toml can't be read: {e}")
                return 1
            sch, errs, warns = check(data, folder)
            for w in warns:
                print(f"note: {w}")
            for e in errs:
                print(f"problem: {e}")
            if errs:
                return 1
            if sch is None:
                print("This mod has no [[settings]].")
                return 0
            print("\n".join(compile_lines(sch, dll_names=[n for n in [_dll_name(sch)] if n])))
            return 0
        library = _library_arg(a)
        sch = _find_schema(library, a.mod)
        if a.cmd == "set":
            changed = set_value(library, sch, a.key, a.value)
            print(f"{sch.name}: " + (", ".join(changed) + " changed" if changed else "no change"))
        elif a.cmd == "reset":
            reset_values(library, sch)
            print(f"{sch.name}: every setting is back to its default")
        vals = load_values(library, sch)
        st = state(sch, vals)
        for s in sch.settings:
            v = value_text(s, vals.get(s.key)) if s.type != "button" else f"[{s.button}]"
            flags = ("" if st[s.key]["shown"] else " (hidden)") + \
                ("" if st[s.key]["enabled"] else f" ({locked_line(sch, s)})")
            print(f"  {s.key} = {v}{flags}")
        return 0
    except SettingsError as e:
        print(e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
