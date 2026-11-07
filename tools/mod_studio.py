#!/usr/bin/env python3
"""Breathalyzer Mod Studio: write a native mod in C, build it, and load it into the running game. Big text,
keyboard first.

    python tools/mod_studio.py [--dir <folder>] [--size 18] [--dark] [--port 7777]
                               [--install <Savage Wind folder>] [--library <mod library>]
                               [--codemap <folder> | --no-codemap]

New mod (Ctrl+N) starts a mod from a template (empty, show-position, stamina, command) in a folder of its own:
--dir, else %USERPROFILE%\\Documents\\SavageWind\\mods-src\\<name> (<name>.c, mod.toml, and <name>.dll once built).
Building uses the folder .build next to the mods (objects, zig's cache, the copies loaded into the game).

While you type a name (2 letters or more, or after botw_ / BOTW_ / api->), a list of matching names opens under the
cursor: Up/Down pick, Tab or Enter puts it in (a function with its arguments), Esc closes it, Ctrl+Space opens it.
The line under the editor says what the picked name is, its address, and whether it was checked in the game.
Ctrl+F1 (Find game value) searches every name, also the local code map (out\\codemap, raw game names, not tested).

F5 builds the mod into a DLL with the zig that ships with Savage Wind, at low priority so a running game keeps its
frame rate. Errors are explained in plain words and their lines marked; F8 goes to the next one.
F6 puts the mod into the game: when the game answers on its control port (--port, default 7777) the DLL is copied to a
new name and loaded with `modload` / `modreload`; when it doesn't, the mod goes into the mod manager's library
(tools/modman.py remove + add), so Deploy and Play in the mod manager load it next time. A mod that hooks a function
needs game code with a hook slot for it: Mod Studio says when a rebuild is needed, and never starts one.

Keys: F5 build, F6 load into game, F8 next error, Ctrl+S save, Ctrl+N new mod, Ctrl+O open mod, Ctrl+F1 find a game
value, Ctrl+Space names, Ctrl+Tab and Ctrl+Shift+Tab leave the editor, Ctrl + and Ctrl - change the text size.
Docs: docs/mod-studio.md.
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import difflib
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import font as tkfont
except ImportError:  # (the logic works without Tk; only the window needs it)
    tk = None

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import build_native as bn  # noqa: E402
import control  # noqa: E402
import mod_symbols as ms  # noqa: E402

REPO = Path(TOOLS).parent
TITLE = "Breathalyzer Mod Studio"
DEFAULT_SIZE, MIN_SIZE, MAX_SIZE = 18, 10, 40
PAD = 16
POLL_MS = 50
HIGHLIGHT_MS = 40          # colour the visible lines this long after the last key
POPUP_ROWS = 10
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
RESERVED_RE = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])$")  # (modman.RESERVED_RE: names Windows keeps)
VERSION_RE = re.compile(r"^(\d{1,9}(?:\.\d{1,9}){0,3})(?:[-+][0-9A-Za-z.-]+)?$")  # (modman.VERSION_RE)
# Command names modhost and Savage Wind 0.4.1-alpha.4 have already: a mod's command with one of them is skipped.
TAKEN_COMMANDS = frozenset("""
mods modload modunload modreload help ping exit cell sites fly state pos camera time shot goto teleport mem memstats
frames frametrace get set gpuprof vkprof prof hold input look press release report sample stick threads version
color_lut console batch
""".split())
STUDIO_ID = "studio."      # mod.toml ids: studio.<name>
BUILD_DIR = ".build"
HOT_KEEP = 3               # copies loaded into the game that are kept (older ones are deleted when not in use)
REBUILD_TIME = "10-20 minutes"
BELOW_NORMAL = 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS: zig and its children run below the game
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
API_RECEIVERS = ("api", "a")  # names a mod's SwModApi pointer has in the templates
GAME_VERSION = "1.9.0"     # botw.h's addresses are for this version
KEYS_HINT = ("F5 build   F6 load into game   F8 next error   Ctrl+Space names   Ctrl+F1 find game value   "
             "Ctrl+S save   Ctrl+N new   Ctrl+O open   Ctrl+Tab leaves the editor   Ctrl + and Ctrl - text size")
NAMES_LOADING = "Loading the game's names in the background: the window may pause for a moment."

C_KEYWORDS = frozenset("""
auto break case char const continue default do double else enum extern float for goto if inline int long register
restrict return short signed sizeof static struct switch typedef union unsigned void volatile while bool true false
NULL uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t size_t uintptr_t _Bool _Static_assert
""".split())


# ---------------------------------------------------------------- templates

HEAD = """// {name}: a native mod for Savage Wind, made with Mod Studio (tools/mod_studio.py).
// F5 builds it, F6 loads it into the running game. Type botw_ or api-> to see what you can use,
// Ctrl+Space shows the list, Ctrl+F1 searches every game value (docs: native/modhost/botw.h).
"""

TEMPLATES: dict[str, tuple[str, str, str]] = {
    "empty": ("Empty", "Only swmod_init: start from nothing.", HEAD + """#include "swmod.h"
#include "botw.h"

static const SwModApi *api;  // the mod API: everything the mod does in the game goes through it

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;  // a modhost older than this mod
    api = a;
    api->log("{name}: ready (game %s)", api->game_version);
    return 0;
}
"""),
    "show-position": ("Show position", "A command that says where Link is.", HEAD + """#include "swmod.h"
#include "botw.h"

static const SwModApi *api;

// `{name}` in the game's console (or over the control port): where Link is.
static void cmd_{name}(int argc, char **argv, SwModReply *r) {
    (void)argc;
    (void)argv;
    float pos[3];
    if (!botw_player_pos(api, pos)) {
        api->reply_fail(r, "Link isn't in the world yet (load a save first)");
        return;
    }
    api->reply_num(r, "x", pos[0]);
    api->reply_num(r, "y", pos[1]);
    api->reply_num(r, "z", pos[2]);
    api->reply_text(r, "Link is at x %.1f, y %.1f (height), z %.1f", pos[0], pos[1], pos[2]);
}

static const SwModCommand commands[] = {
    {"{name}", "", "Where Link is (x y z in metres)", cmd_{name}, SWMOD_CMD_PLAYER},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    if (!botw_version_ok(api)) {
        api->log("{name}: made for game version %s, this is %s", BOTW_GAME_VERSION, api->game_version);
        return 2;
    }
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("{name}: ready, type {name} in the console");
    return 0;
}
"""),
    "stamina": ("Stamina", "A command that shows or sets Link's stamina.", HEAD + """#include <stdlib.h>

#include "swmod.h"
#include "botw.h"

static const SwModApi *api;

// `{name}` says Link's stamina; `{name} 1000` sets it to 1000 (BOTW_STAMINA_WHEEL: one wheel; more wheels hold more).
static void cmd_{name}(int argc, char **argv, SwModReply *r) {
    if (argc > 1 && !botw_stamina_set(api, (float)atof(argv[1]))) {
        api->reply_fail(r, "can't set stamina now: Link isn't in the world, or the value isn't a number");
        return;
    }
    float now = botw_stamina_get(api);
    if (now < 0) {
        api->reply_fail(r, "Link isn't in the world yet (load a save first)");
        return;
    }
    api->reply_num(r, "stamina", now);
    api->reply_text(r, "stamina is %.0f (%.1f wheels)", now, now / BOTW_STAMINA_WHEEL);
}

static const SwModCommand commands[] = {
    {"{name}", "[value]", "Show Link's stamina, or set it (1000 = one wheel)", cmd_{name}, 0},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    if (!botw_version_ok(api)) {
        api->log("{name}: made for game version %s, this is %s", BOTW_GAME_VERSION, api->game_version);
        return 2;
    }
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("{name}: ready, type {name} in the console");
    return 0;
}
"""),
    "command": ("Command", "A console command with two numbers: a start for your own.", HEAD + """#include <stdlib.h>

#include "swmod.h"
#include "botw.h"

static const SwModApi *api;

// `{name} <a> <b>` adds two numbers: a command with arguments to start from.
static void cmd_{name}(int argc, char **argv, SwModReply *r) {
    if (argc < 3) {
        api->reply_fail(r, "usage: {name} <a> <b>");
        return;
    }
    double x = atof(argv[1]), y = atof(argv[2]);
    api->reply_num(r, "sum", x + y);
    api->reply_text(r, "%g + %g = %g", x, y, x + y);
}

static const SwModCommand commands[] = {
    {"{name}", "<a> <b>", "Add two numbers (Mod Studio example)", cmd_{name}, SWMOD_CMD_PLAYER},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("{name}: ready, type {name} 1 2 in the console");
    return 0;
}
"""),
}


class StudioError(Exception):
    """Something the player has to fix; the message says what."""


# ---------------------------------------------------------------- mods on disk

def default_mods_dir() -> Path:
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return Path(home) / "Documents" / "SavageWind" / "mods-src"


@dataclass
class Mod:
    name: str
    root: Path            # the folder that holds every mod (--dir)

    @property
    def folder(self) -> Path:
        return self.root / self.name

    @property
    def source(self) -> Path:
        return self.folder / f"{self.name}.c"

    @property
    def manifest(self) -> Path:
        return self.folder / "mod.toml"

    @property
    def dll(self) -> Path:
        return self.folder / f"{self.name}.dll"

    @property
    def build_dir(self) -> Path:
        return self.root / BUILD_DIR / self.name

    @property
    def mod_id(self) -> str:
        return STUDIO_ID + self.name


def check_name(name: str) -> str | None:
    """Why a mod name can't be used, or None. The name is also its file name and its console command."""
    if not name:
        return "Type a name first."
    if not NAME_RE.match(name):
        return ("Use 2 to 31 small letters, digits and _ , starting with a letter (it becomes the file name and the "
                "console command).")
    if name in C_KEYWORDS:
        return f"{name} is a word C uses itself: pick another name."
    if RESERVED_RE.match(name):
        return f"Windows keeps the name {name} for itself (no file can have it): pick another name."
    if name in TAKEN_COMMANDS:
        return (f"The game or modhost already has a command called {name}, so your mod's command would be skipped: "
                "pick another name.")
    return None


def list_mods(root: Path) -> list[str]:
    try:
        names = [p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
                 and (p / f"{p.name}.c").is_file()]
    except OSError:
        return []
    return sorted(names)


def new_mod(root: Path, name: str, template: str) -> Mod:
    why = check_name(name)
    if why:
        raise StudioError(why)
    if template not in TEMPLATES:
        raise StudioError(f"No template {template}.")
    mod = Mod(name, root)
    if mod.folder.exists():
        raise StudioError(f"{mod.folder} is already there: open it with Ctrl+O, or pick another name.")
    mod.folder.mkdir(parents=True)
    mod.source.write_text(TEMPLATES[template][2].replace("{name}", name), encoding="utf-8", newline="\n")
    write_manifest(mod, mod.source.read_text(encoding="utf-8"), {})
    return mod


# ---------------------------------------------------------------- what the source uses

def strip_comments(src: str) -> str:
    """The code without comments and strings (they're blanked out, line breaks kept)."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            seg = src[i:n if j < 0 else j + 2]
            out.append("\n" * seg.count("\n"))
            i = n if j < 0 else j + 2
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out.append(c + c)
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


HOOK_CALL_RE = re.compile(r"\bhook\s*\(")
DEFINE_NUM_RE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)\s+\(?\s*(?:\(\s*u?int64_t\s*\))?\s*(0[xX][0-9a-fA-F]+)",
                           re.M)


def first_argument(code: str, i: int) -> str:
    """The first argument of a call whose ( is just before code[i] (nested brackets kept together)."""
    depth = 0
    for j in range(i, len(code)):
        c = code[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return code[i:j]
            depth -= 1
        elif c == "," and depth == 0:
            return code[i:j]
    return code[i:]


def find_hooks(src: str, names: dict[str, str] | None = None) -> tuple[list[int], list[str]]:
    """The addresses the mod hooks (api->hook(<address>, ...)): written as a number, a #define of the mod, or a
    BOTW_ADDR_ name. Returns (addresses, the hook calls whose address can't be worked out)."""
    code = strip_comments(src)
    local = {m.group(1): m.group(2) for m in DEFINE_NUM_RE.finditer(code)}
    found, unknown = [], []
    for m in HOOK_CALL_RE.finditer(code):
        arg = " ".join(first_argument(code, m.end()).split())
        value = None
        num = re.fullmatch(r"(0[xX][0-9a-fA-F]+)[uUlL]*", arg)
        if num:
            value = num.group(1)
        elif arg in local:
            value = local[arg]
        elif names and names.get(arg):
            value = names[arg]
        if value is None:
            line = code.count("\n", 0, m.start()) + 1
            unknown.append(f"line {line}: hook({arg}, ...)")
            continue
        a = int(value, 16)
        if a not in found:
            found.append(a)
    return sorted(found), unknown


GAME_NAME_RE = re.compile(r"\b(?:botw_[a-z]\w*|BOTW_\w+)\b|(?<![\w.])0[xX]7[0-3][0-9a-fA-F]{8}(?![\w.])")


def uses_game(src: str) -> bool:
    """True when the code uses botw.h's names or a game address (they are for one game version)."""
    return bool(GAME_NAME_RE.search(strip_comments(src)))


def read_version(text: str) -> str:
    m = re.search(r'^\s*version\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else "1.0.0"


def manifest_text(mod: Mod, hooks=(), game: bool = False, version: str = "1.0.0") -> str:
    """mod.toml for the mod manager (docs/mod-format.md)."""
    lines = [f"# {mod.name}: made with Mod Studio (tools/mod_studio.py). Mod Studio writes this file each time it",
             "# builds the mod; change the version here, the rest follows the code.",
             "", "[mod]", f'id = "{mod.mod_id}"', f'name = "{mod.name}"', f'version = "{version}"',
             'description = "Made with Mod Studio."',
             f"loader_api = {2 if hooks else 1}"]
    if game or hooks:
        lines.append(f'game_versions = ["{GAME_VERSION}"]   # its game addresses are for this version')
    lines += ["", "[code]", f'windows = "{mod.name}.dll"']
    if hooks:
        lines.append("hooks = [" + ", ".join(f'"{a:#x}"' for a in hooks) + "]")
    return "\n".join(lines) + "\n"


def manifest_version_problem(mod: Mod) -> str | None:
    """Why the mod manager would refuse the version in the mod's mod.toml (it wants 1.0, 1.2.3 or 1.0-beta), or
    None."""
    try:
        version = read_version(mod.manifest.read_text(encoding="utf-8"))
    except OSError:
        return None
    if VERSION_RE.match(version):
        return None
    return (f'The version in mod.toml ("{version}") isn\'t like 1.0 or 1.2.3, so the mod manager won\'t take the '
            f"mod. Change it in {mod.manifest} (1.1-beta is fine).")


def botw_names() -> dict[str, str]:
    """BOTW_ADDR_ names -> addresses, straight from botw.h (for when the editor's names aren't loaded yet)."""
    try:
        text = (REPO / ms.BOTW_H).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return {e["name"]: e["address"] for e in ms.parse_botw_h(text) if e["kind"] == "address"}


def write_manifest(mod: Mod, src: str, names: dict[str, str] | None = None) -> tuple[list[int], list[str]]:
    """Write mod.toml from what the code uses. Returns (hook addresses, hook calls whose address isn't clear)."""
    hooks, unknown = find_hooks(src, names if names else botw_names())
    try:
        version = read_version(mod.manifest.read_text(encoding="utf-8"))
    except OSError:
        version = "1.0.0"
    text = manifest_text(mod, hooks, uses_game(src), version)
    try:
        old = mod.manifest.read_text(encoding="utf-8")
    except OSError:
        old = None
    if old != text:
        mod.manifest.write_text(text, encoding="utf-8", newline="\n")
    return hooks, unknown


# ---------------------------------------------------------------- what the editor completes

TOKEN_RE = re.compile(r"(?:(?P<recv>[A-Za-z_]\w*)->)?(?P<ident>[A-Za-z_]\w*)?$")


@dataclass
class Token:
    text: str         # what gets replaced
    ident: str        # the name part
    recv: str | None  # "api" when it follows api-> (or a->)
    blocked: bool = False  # no names here: inside a number (0x7001e13ed0) or a member of another struct (info->stam)


def token_before(line: str) -> Token:
    """The name being typed at the end of `line` (the line up to the cursor)."""
    m = TOKEN_RE.search(line)
    recv, ident = m.group("recv"), m.group("ident") or ""
    before = line[:m.start()]
    if recv and recv not in API_RECEIVERS:
        return Token("", "", None, True)  # (info->stam: a member of something that isn't the api)
    if not recv and (before.endswith(("->", ".")) or (before[-1:].isdigit() and ident)):
        return Token("", "", None, True)  # (p->x, s.x, or the x7001e13ed0 of 0x7001e13ed0)
    text = (f"{recv}->" if recv else "") + ident
    return Token(text, ident, recv)


def in_comment_or_string(line: str) -> bool:
    code, comment, block = ms._split_comment(line, False)
    if comment is not None or block:
        return True
    quotes = 0
    i = 0
    while i < len(code):
        if code[i] == "\\":
            i += 2
            continue
        if code[i] == '"':
            quotes += 1
        i += 1
    return quotes % 2 == 1


def should_open(tok: Token, line: str) -> bool:
    """Open the list by itself? After api-> (or a->), after 2+ letters of a name; not in comments, strings, an
    #include line, a number, a member of another struct, or for a word C has itself."""
    if tok.blocked:
        return False
    if in_comment_or_string(line) or re.match(r"\s*#\s*(include|pragma|if|ifdef|ifndef|endif|else)\b", line):
        return False
    if tok.recv:
        return True
    return len(tok.ident) >= 2 and tok.ident not in C_KEYWORDS


def popup_query(tok: Token, auto: bool) -> tuple[str, set | None]:
    """(search text, kinds) for the list. While typing on, the code map joins in from 3 letters."""
    if tok.recv:
        return f"api->{tok.ident}", {"api"}
    if auto and len(tok.ident) < 3:
        return tok.ident, {"helper", "api", "address", "offset"}
    return tok.ident, None


def suggestions(index, tok: Token, auto: bool, limit: int = POPUP_ROWS) -> list[dict]:
    q, kinds = popup_query(tok, auto)
    items = ms.search(index, q, limit, kinds=kinds)
    if auto and len(items) == 1 and items[0]["name"] == tok.text and ms.call_params(items[0]) is None:
        return []  # (already typed in full: nothing to add)
    return items


def doc_lines(e: dict) -> tuple[str, str]:
    """(first line: what to type, second: what it is, its address, how far to trust it)."""
    sig = e["signature"] or ms.display_text(e)
    parts = [e["doc"]] if e["doc"] else []
    if e["address"] and e["kind"] in ("address", "offset", "codemap"):
        parts.append(("offset +" if e["kind"] == "offset" else "address ") + e["address"])
    parts.append(ms.trust_text(e))
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return sig, "  |  ".join(out)


# ---------------------------------------------------------------- building

@dataclass
class Problem:
    line: int          # in the mod's source (0: none, e.g. a link error)
    col: int
    level: str         # "error" or "warning"
    message: str       # the compiler's words
    plain: str         # ours
    file: str = ""


@dataclass
class BuildResult:
    ok: bool
    dll: Path | None
    problems: list[Problem] = field(default_factory=list)
    seconds: float = 0.0
    output: str = ""
    hooks: list[int] = field(default_factory=list)

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.level == "error"]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if p.level == "warning"]


PLAIN = [
    (r"use of undeclared identifier '(\w+)'", "{0} isn't known here. Check the spelling, or press Ctrl+Space to pick "
                                              "a name."),
    (r"call to undeclared function '(\w+)'", "There is no function called {0}. Check the spelling, or press "
                                             "Ctrl+Space to pick one."),
    (r"implicit declaration of function '(\w+)'", "There is no function called {0}. Check the spelling, or press "
                                                  "Ctrl+Space to pick one."),
    (r"unknown type name '(\w+)'", "{0} isn't a type C knows. Check the spelling (int, float, bool, uint64_t ...)."),
    (r"expected ';'", "A ; is missing (at the end of this line or the one before)."),
    (r"expected '\)'", "A ) is missing."),
    (r"expected '\}'", "A } is missing: every { needs its }."),
    (r"extraneous closing brace", "There is one } too many."),
    (r"expected expression", "Something is missing here: a value or a name."),
    (r"expected identifier", "A name is missing here."),
    (r"too few arguments to function call, expected (\d+), have (\d+)",
     "This call gives {1} value(s), and the function needs {0}."),
    (r"too many arguments to function call, expected (\d+), have (\d+)",
     "This call gives {1} value(s), and the function takes only {0}."),
    (r"no member named '(\w+)' in '(?:struct )?([^']+)'", "{1} has no {0}. Type api-> to pick from the list."),
    (r"redefinition of '(\w+)'", "{0} is made twice: rename one of them."),
    (r"'([^']+)' file not found", "The file {0} isn't there (#include)."),
    (r"undefined symbol: (\S+)", "{0} is used but never written anywhere. Check the spelling."),
    (r"unused variable '(\w+)'", "{0} is never used (that's allowed)."),
    (r"unused parameter '(\w+)'", "{0} is never used: write (void){0}; to say that's on purpose."),
    (r"unused function '(\w+)'", "{0} is never called."),
    (r"control reaches end of non-void function", "The function can reach its end without return: add a return."),
    (r"non-void function '(\w+)' should return a value", "{0} must return a value: write return <value>;"),
    (r"missing terminating '?\"'? character", "A text in \"...\" isn't closed."),
    (r"incompatible pointer to integer|incompatible integer to pointer",
     "A pointer and a number are mixed up here (a missing * or &?)."),
    (r"incompatible pointer types", "A pointer of the wrong type is used here."),
    (r"format specifies type '([^']+)' but the argument has type '([^']+)'",
     "The % code in the text expects {0}, and the value is {1}."),
    (r"assigning to '([^']+)' from incompatible type '([^']+)'", "A {1} can't be put into a {0}."),
    (r"passing '([^']+)' to parameter of incompatible type '([^']+)'", "This gives a {0} where a {1} is needed."),
    (r"use of undeclared label", "That label isn't there."),
    (r"invalid operands to binary expression", "These two values can't be used with this operator."),
    (r"called object type '([^']+)' is not a function", "That isn't a function, so it can't be called with ()."),
]
PLAIN_RE = [(re.compile(rx), text) for rx, text in PLAIN]
DIAG_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+): (?P<level>fatal error|error|warning): "
                     r"(?P<msg>.*?)(?: \[-W[\w-]+\])?$", re.M)
LINK_RE = re.compile(r"^(?:(?:lld-link|ld\.lld|ld|zig)[^:\n]*: )?(?P<level>error|warning): (?P<msg>.+)$", re.M)


def plain_message(msg: str) -> str:
    for rx, text in PLAIN_RE:
        m = rx.search(msg)
        if m:
            return text.format(*m.groups())
    return msg[:1].upper() + msg[1:]


INCLUDED_RE = re.compile(r"^In file included from (?P<file>.+?):(?P<line>\d+)[:,]?\s*$")
NOTE_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+): note: (?P<msg>.*)$")


def parse_problems(output: str, source: Path) -> list[Problem]:
    """The compiler's and linker's errors and warnings, with line numbers in the mod's source. An error inside a
    header is put on the mod's own line that caused it: a macro of the mod the compiler expanded there ("note:
    expanded from macro"), else the mod's #include line ("In file included from")."""
    out, seen = [], set()
    src_name = os.path.normcase(str(source))

    def is_mine(f: str) -> bool:
        f = f.strip()
        return os.path.normcase(f) == src_name or os.path.basename(f).lower() == source.name.lower()

    lines = output.splitlines()
    included = 0  # the mod's #include line that the headers in the latest "In file included from" chain came from
    for i, text in enumerate(lines):
        inc = INCLUDED_RE.match(text)
        if inc:
            if is_mine(inc.group("file")):
                included = int(inc.group("line"))
            continue
        m = DIAG_RE.match(text)
        if not m:
            continue
        level = "error" if "error" in m.group("level") else "warning"
        f = m.group("file").strip()
        mine = is_mine(f)
        line = int(m.group("line")) if mine else 0
        if not mine:
            for nxt in lines[i + 1:i + 40]:
                if DIAG_RE.match(nxt) or INCLUDED_RE.match(nxt):
                    break
                note = NOTE_RE.match(nxt)
                if note and is_mine(note.group("file")) and "expanded from macro" in note.group("msg"):
                    line = int(note.group("line"))
                    break
            line = line or included
        msg = m.group("msg").strip()
        key = (line, level, msg)
        if key in seen:
            continue
        seen.add(key)
        plain = plain_message(msg)
        if not mine:
            plain = f"{os.path.basename(f)} line {m.group('line')}: {plain}"
            if line:
                plain += f" It comes from your line {line}."
        out.append(Problem(line, int(m.group("col")) if mine else 0, level, msg, plain, f))
    for m in LINK_RE.finditer(output):
        msg = m.group("msg").strip()
        key = (0, m.group("level"), msg)
        if key not in seen:
            seen.add(key)
            out.append(Problem(0, 0, m.group("level"), msg, plain_message(msg)))
    return out


UNKNOWN_NAME_RE = re.compile(r"undeclared (?:identifier|function) '(\w+)'|unknown type name '(\w+)'|"
                             r"implicit declaration of function '(\w+)'|undefined symbol: (\w+)")
NO_MEMBER_RE = re.compile(r"no member named '(\w+)' in '(?:struct )?SwModApi'")


def add_did_you_mean(problems: list[Problem], index) -> None:
    """A misspelled name: add the closest documented one ("Did you mean botw_stamina_get?")."""
    if index is None:
        return
    curated = [index.entries[i]["name"] for i in index.tiers[0].ids]
    plain_names = [n for n in curated if not n.startswith("api->")]
    members = [n[5:] for n in curated if n.startswith("api->")]
    for p in problems:
        m, pool, prefix = UNKNOWN_NAME_RE.search(p.message), plain_names, ""
        if not m:
            m, pool, prefix = NO_MEMBER_RE.search(p.message), members, "api->"
        if not m:
            continue
        name = next(g for g in m.groups() if g)
        hit = difflib.get_close_matches(name, pool, n=1, cutoff=0.7)
        if hit and hit[0] != name:
            p.plain += f" Did you mean {prefix}{hit[0]}?"


def place_link_errors(problems: list[Problem], src: str) -> None:
    """A link error names a symbol, not a line: point it at the symbol's first use in the source."""
    code = strip_comments(src)
    for p in problems:
        m = re.search(r"undefined symbol: (\S+)", p.message)
        if p.line or not m:
            continue
        sym = m.group(1)
        if sym.startswith("__imp_"):
            sym = sym[len("__imp_"):]
        hit = re.search(rf"\b{re.escape(sym)}\b", code)
        if hit:
            p.line = code.count("\n", 0, hit.start()) + 1


def run_low(cmd: list[str], env: dict, timeout: float = 300) -> subprocess.CompletedProcess:
    """Run a build step below normal priority (zig's own children inherit it), with no console window."""
    flags = (BELOW_NORMAL | NO_WINDOW) if sys.platform == "win32" else 0
    return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", env=env,
                          creationflags=flags, timeout=timeout)


def compile_commands(tc: bn.Toolchain, mod: Mod) -> tuple[list[list[str]], Path]:
    """The two steps (compile, link) with build_native.py's zig, flags and include folder; and the DLL they make."""
    obj = mod.build_dir / "obj" / f"{mod.name}.o"
    out = mod.build_dir / f"{mod.name}.dll"
    return [bn.compile_cmd(tc, mod.source, obj, (), (bn.HOST_SRC,)), bn.link_cmd(tc, [obj], out, False)], out


def build_mod(mod: Mod, tc: bn.Toolchain, names: dict[str, str] | None = None, runner=run_low,
              check_export: bool = True, index=None) -> BuildResult:
    """Compile and link the mod into <build>/<name>.dll, then copy it next to the source and write mod.toml.
    index: the names, for "Did you mean ...?" after a misspelled one."""
    t0 = time.monotonic()
    src = mod.source.read_text(encoding="utf-8", errors="replace")
    hooks, unknown = write_manifest(mod, src, names)
    cmds, out = compile_commands(tc, mod)
    (mod.build_dir / "obj").mkdir(parents=True, exist_ok=True)
    env = bn.build_env(mod.root / BUILD_DIR)
    problems: list[Problem] = []
    for u in unknown:
        line = int(re.match(r"line (\d+)", u).group(1))
        problems.append(Problem(line, 0, "warning", u, "Mod Studio can't tell which function this hook is for: write "
                                "its address or a BOTW_ADDR_ name there, so the mod manager can give it a hook slot."))
    bad_version = manifest_version_problem(mod)
    if bad_version:
        problems.append(Problem(0, 0, "warning", "mod.toml version", bad_version))
    text = ""
    for cmd in cmds:
        try:
            r = runner(cmd, env)
        except (OSError, subprocess.SubprocessError) as e:
            problems.append(Problem(0, 0, "error", str(e), f"The compiler didn't run: {e}"))
            return BuildResult(False, None, problems, time.monotonic() - t0, text, hooks)
        step = (r.stdout or "") + (r.stderr or "")
        text += step
        problems += parse_problems(step, mod.source)
        if r.returncode:
            if not any(p.level == "error" for p in problems):
                last = next((x.strip() for x in reversed(step.splitlines()) if x.strip()), f"exit code {r.returncode}")
                problems.append(Problem(0, 0, "error", last, plain_message(last)))
            place_link_errors(problems, src)
            add_did_you_mean(problems, index)
            return BuildResult(False, None, problems, time.monotonic() - t0, text, hooks)
    if check_export:
        try:
            exports = bn.pe_info(out.read_bytes()).exports
        except (OSError, bn.PeError) as e:
            exports = []
            problems.append(Problem(0, 0, "error", str(e), f"The DLL can't be read: {e}"))
        if "swmod_init" not in exports:
            if not any(p.level == "error" for p in problems):
                problems.append(Problem(0, 0, "error", "no swmod_init export",
                                        "The mod has no swmod_init: keep the line SWMOD_EXPORT int swmod_init(...)."))
            return BuildResult(False, None, problems, time.monotonic() - t0, text, hooks)
    try:
        shutil.copyfile(out, mod.dll)
    except OSError as e:
        problems.append(Problem(0, 0, "warning", str(e), f"Built, but {mod.dll.name} couldn't be copied next to the "
                                                         f"source ({e.strerror or e})."))
    return BuildResult(True, out, problems, time.monotonic() - t0, text, hooks)


# ---------------------------------------------------------------- into the game

@dataclass
class Outcome:
    kind: str                 # ok, warn, error, info
    lines: list[str]
    loaded_name: str | None = None   # the name modhost gave the mod (for the next modreload)


def hot_copy(dll: Path, mod: Mod) -> Path:
    """Copy the DLL to <build>/hot/<name>-<n>.dll, a name the game hasn't loaded yet; delete old copies that are
    no longer in use (the game keeps a loaded one locked, so that one stays)."""
    d = mod.build_dir / "hot"
    d.mkdir(parents=True, exist_ok=True)
    rx = re.compile(rf"{re.escape(mod.name)}-(\d+)\.dll", re.I)
    have = sorted((int(m.group(1)), p) for p in d.iterdir() if (m := rx.fullmatch(p.name)))
    n = (have[-1][0] if have else 0) + 1
    dst = d / f"{mod.name}-{n}.dll"
    shutil.copyfile(dll, dst)
    for _, old in have[:-HOT_KEEP] if len(have) > HOT_KEEP else []:
        with contextlib.suppress(OSError):
            old.unlink()
    return dst


class GameLink(control.Control):
    """tools/control.py's connection, returning the game's whole reply ({"ok": ..., "data"/"name"/"error": ...})."""

    def request(self, *words) -> dict:
        self._send(control.request_line([str(w) for w in words]))
        line = self._read_line()
        try:
            reply = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise control.ControlError(f"invalid reply from the game: {e}") from None
        if not isinstance(reply, dict):
            raise control.ControlError("invalid reply from the game: not a JSON object")
        return reply


def game_answers(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def reply_field(reply: dict, key: str):
    if key in reply:
        return reply[key]
    data = reply.get("data")
    return data.get(key) if isinstance(data, dict) else None


def hot_base(file: str) -> str:
    """The name modhost gives a DLL loaded while the game runs: the file name without .dll and without the -<n> of
    the copy (speedy-3.dll is speedy), so a mod keeps its name across modreload."""
    base = file[:-4] if file.lower().endswith(".dll") else file
    return re.sub(r"(?<=.)-\d+$", "", base)


def loaded_entry(data: dict, name: str) -> dict | None:
    """This mod in a `mods` reply ({"name", "hot"}), or None. mods.<i>.name is the name; an older modhost without
    names gives the file only."""
    rows: dict[str, dict] = {}
    for k, v in (data or {}).items():
        m = re.fullmatch(r"mods\.(\d+)\.(name|file|hot)", k)
        if m:
            rows.setdefault(m.group(1), {})[m.group(2)] = v
    for row in rows.values():
        n = row.get("name") if isinstance(row.get("name"), str) else (
            hot_base(row["file"]) if isinstance(row.get("file"), str) else None)
        if n and n.lower() == name.lower():
            return {"name": n, "hot": row.get("hot")}
    return None


NOT_LOADED_RE = re.compile(r"no (such )?mod|not loaded|isn't loaded|unknown mod|not found|no hot", re.I)
REBUILD_LINE = (f"Its hook needs a hook slot in the game code: press Deploy in the mod manager to rebuild it "
                f"({REBUILD_TIME}). Do that when you aren't playing.")


class PortProblem(Exception):
    """Something listens on the control port, but it didn't answer `mods` like the game. silent: no answer at all."""

    def __init__(self, message: str, silent: bool):
        super().__init__(message)
        self.silent = silent


FIRST_ANSWER_S = 2.0  # how long the first request may take (the game answers once per frame)


def load_running(mod: Mod, dll: Path, port: int, known: str | None = None, hooks=(), link=GameLink,
                 timeout: float = 15.0, first_timeout: float = FIRST_ANSWER_S) -> Outcome:
    """The game answers: copy the DLL to a fresh name and modreload (or modload) it. PortProblem when what answers on
    the port isn't the game."""
    lines: list[str] = []
    with link(port, timeout=timeout) as g:
        with contextlib.suppress(AttributeError, OSError):
            g.sock.settimeout(first_timeout)
        try:
            mods = g.request("mods")
        except control.ControlError as e:
            if "in time" in str(e) or "invalid reply" in str(e):
                raise PortProblem(str(e), "in time" in str(e)) from None
            raise  # (the connection closed: the game quit or restarted)
        if not isinstance(mods, dict) or not isinstance(mods.get("ok"), bool) or (
                mods.get("ok") and not isinstance(mods.get("data"), dict)) or (
                not mods.get("ok") and not isinstance(mods.get("error"), str)):
            raise PortProblem("its answer isn't the game's", False)
        with contextlib.suppress(AttributeError, OSError):
            g.sock.settimeout(timeout)
        if not mods.get("ok"):
            err = str(mods.get("error", ""))
            if err.startswith("Unknown command"):
                return Outcome("error", ["The game runs without modhost, so it can't take native mods.",
                                         "Close it, then press F6 to put the mod in the library, and start the game "
                                         "with Play in the mod manager."])
            mods = {"data": {}}
        data = mods.get("data") if isinstance(mods.get("data"), dict) else {}
        no_slots = bool(hooks and data and int(data.get("hook_slots", 0) or 0) == 0)
        if no_slots:
            lines.append("This game code has no hook slots, so the hook does nothing yet. " + REBUILD_LINE)
        there = loaded_entry(data, mod.name)
        target = known or (there["name"] if there else None)
        copy = hot_copy(dll, mod)
        if target:
            reply = g.request("modreload", target, str(copy))
            if not reply.get("ok") and NOT_LOADED_RE.search(str(reply.get("error", ""))):
                target = None
                reply = g.request("modload", str(copy))
        else:
            reply = g.request("modload", str(copy))
    if reply.get("ok"):
        name = reply_field(reply, "name") or hot_base(copy.name)
        mid = reply_field(reply, "id")
        replaced = reply_field(reply, "replaced")
        verb = "Reloaded" if (replaced if isinstance(replaced, bool) else target) else "Loaded"
        head = f"{verb} {mod.name} into the game" + (f" (mod id {mid})." if mid is not None else ".")
        no_slot = reply_field(reply, "no_slot")
        if no_slot and not no_slots:
            lines.append(f"No hook slot for {no_slot} in this game code, so that hook does nothing. "
                         + REBUILD_LINE)
        notes = [x.strip() for x in str(reply_field(reply, "notes") or "").splitlines() if x.strip()]
        lines += notes[:2]
        return Outcome("warn" if lines else "ok", [head, *lines, "Its commands work now: try them in the console."],
                       loaded_name=str(name))
    err = str(reply.get("error", "no reason given"))
    if err.startswith("Unknown command"):
        return Outcome("error", ["This modhost can't load mods while the game runs (no modload command).",
                                 "Update modhost (python tools/build_native.py build) and restart the game. Until "
                                 "then: close the game and press F6, so the mod loads next time."])
    if "game start" in err:
        lines.insert(0, "It came from the mod library when the game started. Close the game and press F6 (that "
                        "updates the library), or turn it off in the mod manager to load it with F6 while you play.")
    return Outcome("error", [f"The game didn't take {mod.name}: {err}", *lines])


def make_package(mod: Mod, dll: Path) -> Path:
    """<build>/package: mod.toml and the DLL only, for the mod manager (it copies the whole folder)."""
    pkg = mod.build_dir / "package"
    pkg.mkdir(parents=True, exist_ok=True)
    for p in pkg.iterdir():
        if p.is_file() and p.name not in ("mod.toml", f"{mod.name}.dll"):
            with contextlib.suppress(OSError):
                p.unlink()
    shutil.copyfile(mod.manifest, pkg / "mod.toml")
    shutil.copyfile(dll, pkg / f"{mod.name}.dll")
    return pkg


def load_modman():
    import modman  # noqa: E402 (heavy: only when a mod goes into the library)
    return modman


def library_hook_note(mm, lib, hooks) -> str | None:
    """Whether the game code the last deploy made has hook slots for these addresses."""
    if not hooks:
        return None
    try:
        marker = mm.read_json(os.path.join(lib.deploy_dir, mm.MARKER)) or {}
    except Exception:  # noqa: BLE001 (a damaged marker: then no slots are known)
        marker = {}
    built = set()
    for a in (marker.get("rebuild") or {}).get("hooks", []) or []:
        with contextlib.suppress(TypeError, ValueError):
            built.add(int(str(a), 16))
    if all(a in built for a in hooks):
        return "The game code from the last deploy already has a hook slot for its hook."
    return REBUILD_LINE


def keep_library_copy(lib, mod: Mod) -> Path | None:
    """A copy of the mod's folder in the library (<build>/library-old), so it can go back if the new one is refused.
    None when there is no folder to copy."""
    try:
        src = Path(lib.mod_path(mod.mod_id))
    except Exception:  # noqa: BLE001 (a library without mod_path: nothing to keep)
        return None
    if not src.is_dir():
        return None
    keep = mod.build_dir / "library-old"
    shutil.rmtree(keep, ignore_errors=True)
    shutil.copytree(src, keep)
    return keep


def place(lib, entry: dict, i: int, old: dict) -> None:
    """Put entry where the old entry was in the list, on or off as it was."""
    lib.mods.remove(entry)
    lib.mods.insert(min(i, len(lib.mods)), entry)
    entry["enabled"] = bool(old.get("enabled", True))
    lib.save()


def restore_library_copy(mm, lib, mod: Mod, old: tuple[int, dict], keep: Path | None) -> bool:
    """The new copy was refused after the old one was removed: add the old one back, at its place. True when done."""
    if keep is None:
        return False
    try:
        entry, _scan, _notes = mm.add_mod(lib, str(keep), old[1].get("name") or mod.name)
        place(lib, entry, *old)
        return True
    except (mm.ModmanError, OSError):
        return False


def put_in_library(mod: Mod, dll: Path, library: str | None = None, hooks=(), mm=None) -> Outcome:
    """The game isn't running: put the mod into the mod manager's library (remove the old copy, add the new one,
    at the same place in the list and on or off as before)."""
    mm = mm or load_modman()
    try:
        root = mm.resolve_library(library)
        lib = mm.Library.load(root)
        if lib is None:
            install = mm.default_install()
            if not mm.looks_like_install(install):
                return Outcome("error", ["There's no mod library yet, and Savage Wind isn't in its usual folder.",
                                         "Open the mod manager (python tools/modman_gui.py) and add a mod once, "
                                         "then press F6 again."])
            lib = mm.Library(root, install)
            lib.save()
        pkg = make_package(mod, dll)
        # Check the new copy before the old one goes: what the mod manager would refuse (a version like "1.1 beta")
        # must leave the library as it was.
        _man, errs = mm.parse_manifest((pkg / "mod.toml").read_text(encoding="utf-8"))
        if errs:
            return Outcome("error", [f"The mod manager wouldn't take {mod.name}, so the library wasn't changed.",
                                     *[f"{e} (in {mod.manifest})" for e in errs[:2]]])
        old = next(((i, m) for i, m in enumerate(lib.mods) if m["id"] == mod.mod_id), None)
        keep = None
        if old:
            keep = keep_library_copy(lib, mod)
            mm.remove_mod(lib, mod.mod_id)
        try:
            entry, _scan, _notes = mm.add_mod(lib, str(pkg), mod.name)
        except mm.ModmanError as e:
            if not old:
                raise
            put_back = restore_library_copy(mm, lib, mod, old, keep)
            return Outcome("error", [f"The mod manager didn't take the new {mod.name}: {str(e).splitlines()[0]}",
                                     "The library still has the old copy, on or off as before." if put_back else
                                     f"The old copy couldn't be put back: add {mod.name} again with F6 once the "
                                     "problem is fixed."])
        if old:
            place(lib, entry, *old)
    except mm.ModmanError as e:
        return Outcome("error", [f"The mod manager didn't take {mod.name}.", *str(e).splitlines()[:3]])
    except OSError as e:
        return Outcome("error", [f"{mod.name} couldn't go into the mod library: {e.strerror or e}"])
    lines = [f"The game isn't running, so {mod.name} went into the mod library"
             + (" (it was there: replaced)." if old else "."),
             "In the mod manager press Deploy, then Play: it loads with the game."]
    if old and not entry["enabled"]:
        lines.append("It is turned off in the library: turn it on first.")
    note = library_hook_note(mm, lib, hooks)
    if note:
        lines.append(note)
    return Outcome("warn" if note == REBUILD_LINE else "ok", lines)


def into_game(mod: Mod, dll: Path, port: int, known: str | None = None, hooks=(), library: str | None = None,
              answers=game_answers, running=None, link=GameLink, mm=None) -> Outcome:
    """F6: load into the running game, else put into the library."""
    note = None
    if answers(port):
        try:
            return load_running(mod, dll, port, known, hooks, link)
        except PortProblem as e:
            if e.silent:
                return Outcome("error", [f"Nothing answered on port {port} in {FIRST_ANSWER_S:.0f} seconds.",
                                         "If the game is loading or minimised, wait a moment and press F6 again.",
                                         f"If another program uses port {port}, start the game with another port "
                                         f"(python tools/modman.py run --port <number>) and Mod Studio with the "
                                         "same --port."])
            note = (f"Something else answers on port {port} ({e}): it isn't the game. Start the game with another "
                    f"port (python tools/modman.py run --port <number>) and Mod Studio with the same --port.")
        except control.ControlError as e:
            return Outcome("error", [f"The game stopped answering: {e}",
                                     "Commands only run while the game shows frames (not while it loads or is "
                                     "minimised). Try F6 again."])
        except OSError as e:
            return Outcome("error", [f"The DLL couldn't be copied for the game: {e.strerror or e}"])
    out = put_in_library(mod, dll, library, hooks, mm)
    if note:
        lead = "The game isn't running, so "
        if out.lines and out.lines[0].startswith(lead):  # (it may run: on another port)
            out.lines[0] = "So " + out.lines[0][len(lead):]
        out.lines.insert(1 if out.kind == "error" else 0, note)
        out.kind = "warn" if out.kind == "ok" else out.kind
        return out
    try:
        is_running = running() if running else (mm or load_modman()).game_running()
    except Exception:  # noqa: BLE001 (tasklist missing: then don't say it)
        is_running = False
    if is_running:
        out.lines[0:0] = [f"Savage Wind runs, but doesn't answer on port {port}: start it with a control port to load "
                          f"mods while you play (python tools/modman.py run --port {port})."]
        out.kind = "warn" if out.kind == "ok" else out.kind
    return out


# ---------------------------------------------------------------- settings (per user, a convenience only)

def settings_path() -> Path:
    return ms.default_cache_dir() / "mod-studio.json"


def read_settings(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_settings(path: Path | None, data: dict) -> None:
    if path is None:
        return
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------- the window

SYNTAX = {
    "light": {"keyword": "#0033b3", "string": "#a33b00", "comment": "#1a6b1a", "number": "#8b008b",
              "preproc": "#6b4c00", "error_bg": "#ffd0d0", "warn_bg": "#fff0b3", "gutter_bg": "#ececec",
              "gutter_fg": "#4d4d4d"},
    "dark": {"keyword": "#82b1ff", "string": "#ffb86b", "comment": "#7ee787", "number": "#ff9df5",
             "preproc": "#ffd479", "error_bg": "#5c0000", "warn_bg": "#4d3d00", "gutter_bg": "#1a1a1a",
             "gutter_fg": "#bdbdbd"},
}
# Minecraft-style suggestions: a dark box with light grey names; the picked one is a yellow bar with black text and a
# "> " in front, so it stands out by more than its colour (the same in both themes)
POPUP = {"bg": "#000000", "fg": "#e0e0e0", "sel_bg": "#ffff55", "sel_fg": "#000000", "border": "#6e6e6e"}
POPUP_MIN_ROWS = 3   # fewer rows than this never fit: the box goes where most of it shows
MARK, NO_MARK = "> ", "  "
HL_RE = re.compile(r"(?P<comment>//[^\n]*|/\*.*?(?:\*/|\Z))"
                   r"|(?P<string>\"(?:\\.|[^\"\\\n])*\"?|'(?:\\.|[^'\\\n])*'?)"
                   r"|(?P<preproc>^[ \t]*#[ \t]*\w+)"
                   r"|(?P<number>(?<![\w.])(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)[uUlLfF]*(?![\w.]))"
                   r"|(?P<keyword>\b(?:" + "|".join(sorted(C_KEYWORDS - {"NULL"}, key=len, reverse=True)) + r")\b)",
                   re.S | re.M)
HL_TAGS = ("comment", "string", "preproc", "number", "keyword")


class Popup:
    """The suggestion box under the cursor. It shows `rows` names at a time (as many as fit in the editor) and
    scrolls with the picked one."""

    def __init__(self, parent, font):
        self.font = font
        self.frame = tk.Frame(parent, bg=POPUP["bg"], highlightthickness=2, highlightbackground=POPUP["border"],
                              highlightcolor=POPUP["border"], bd=0)
        self.labels = []
        for i in range(POPUP_ROWS):
            lab = tk.Label(self.frame, font=font, bg=POPUP["bg"], fg=POPUP["fg"], anchor="w", justify="left",
                           padx=10, pady=0, bd=0)
            lab.grid(row=i, column=0, sticky="ew")
            self.labels.append(lab)
        self.items: list[dict] = []
        self.texts: list[str] = []
        self.sel = 0
        self.top = 0          # the first item shown
        self.rows = POPUP_ROWS
        self.visible = False

    def set(self, items: list[dict]) -> None:
        self.items = items
        self.sel = self.top = 0
        self.rows = POPUP_ROWS
        self.texts = [ms.display_text(e) for e in items]
        self.render()

    def fit(self, rows: int) -> None:
        """Show at most this many rows (the rest scroll into view with Up and Down)."""
        self.rows = max(1, min(POPUP_ROWS, rows))
        self.top = max(0, min(self.top, self.sel, len(self.items) - self.rows))
        if self.sel >= self.top + self.rows:
            self.top = self.sel - self.rows + 1
        self.render()

    def shown(self) -> int:
        return min(self.rows, len(self.items))

    def render(self) -> None:
        for i, lab in enumerate(self.labels):
            k = self.top + i
            if i < self.shown():
                picked = k == self.sel
                lab.configure(text=(MARK if picked else NO_MARK) + self.texts[k],
                              fg=POPUP["sel_fg"] if picked else POPUP["fg"],
                              bg=POPUP["sel_bg"] if picked else POPUP["bg"])
                lab.grid()
            else:
                lab.grid_remove()

    def move(self, step: int) -> dict | None:
        if not self.items:
            return None
        self.sel = (self.sel + step) % len(self.items)
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + self.rows:
            self.top = self.sel - self.rows + 1
        self.render()
        return self.items[self.sel]

    def size(self) -> tuple[int, int]:
        chars = max((len(t) for t in self.texts), default=10) + len(MARK)
        w = self.font.measure("0") * chars + 24
        h = self.font.metrics("linespace") * self.shown() + 8
        return w, h

    def show(self, x: int, y: int) -> None:
        self.frame.place(x=x, y=y)
        self.frame.lift()
        self.visible = True

    def hide(self) -> None:
        if self.visible:
            self.frame.place_forget()
        self.visible = False
        self.items = []


@dataclass
class Config:
    root: Path = field(default_factory=default_mods_dir)
    port: int = control.DEFAULT_PORT
    install: Path | None = None
    library: str | None = None
    codemap: Path | None = None   # None: no code map
    settings: Path | None = field(default_factory=settings_path)  # the last mod opened (None: not remembered)
    cache: Path | None = None     # the names cache's folder (None: %LOCALAPPDATA%\botw-mod-loader)


class View:
    """The window: status line, buttons, the editor with its suggestion box, the docs line."""

    def __init__(self, root, cfg: Config, size: int = DEFAULT_SIZE, dark: bool = False, index=None,
                 load_names: bool = True, open_last: bool = True, hidden: bool = False):
        from modman_gui import THEMES, Flow, pick_family  # (the mod manager window's colours and button rows)
        self.root, self.cfg = root, cfg
        self.hidden = hidden  # (tests: dialogs stay off the screen too)
        self.find_dialog = None
        self.theme = "dark" if dark else "light"
        self.t = dict(THEMES[self.theme], **SYNTAX[self.theme])
        self.Flow = Flow
        self.size = self.start_size = max(MIN_SIZE, min(MAX_SIZE, size))
        family = pick_family(root, ("Segoe UI", "Verdana", "DejaVu Sans", "Helvetica"), "TkDefaultFont")
        mono = pick_family(root, ("Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Courier New"), "TkFixedFont")
        self.font = tkfont.Font(root, family=family, size=self.size)
        self.bold = tkfont.Font(root, family=family, size=self.size, weight="bold")
        self.head = tkfont.Font(root, family=family, size=self.size + 4, weight="bold")
        self.mono = tkfont.Font(root, family=mono, size=self.size)
        self.mono_bold = tkfont.Font(root, family=mono, size=self.size, weight="bold")
        self.index = index if index is not None else ms.build_index([])
        self.full_index = index is not None and index.codemap_count > 0
        self.names_note = ""
        self.mod: Mod | None = None
        self.loaded_name: str | None = None   # what modhost calls the mod we loaded (for modreload)
        self.problems: list[Problem] = []
        self.problem_at = -1
        self.busy = ""
        self.events: queue.Queue = queue.Queue()
        self.tok: Token | None = None
        self.tok_at = ""           # where self.tok starts in the editor
        self.close_armed = False  # the window's X was pressed once while the text couldn't be saved
        self.last_popup_ms = 0.0
        self.hl_job = None
        self.doc_job = None
        self.tick_job = None
        self.closed = False
        self.buttons: list = []
        self.build()
        self.set_status("info", TITLE, ["Press Ctrl+N for a new mod, or Ctrl+O to open one."])
        if load_names:
            self.load_names()
        if open_last:
            last = read_settings(cfg.settings).get("last")
            if isinstance(last, str) and last in list_mods(cfg.root):
                self.open_mod(Mod(last, cfg.root))
        self.editor.focus_set()
        self.tick_job = root.after(POLL_MS, self.tick)

    def shutdown(self) -> None:
        """Stop the timers (before the window goes)."""
        self.closed = True
        for job in (self.tick_job, self.hl_job, self.doc_job):
            if job:
                with contextlib.suppress(tk.TclError):
                    self.root.after_cancel(job)

    # --- building the window

    def label(self, parent, font=None, **kw):
        opts = {"bg": self.t["bg"], "fg": self.t["fg"], "anchor": "w", "justify": "left", **kw}
        return tk.Label(parent, font=font or self.font, **opts)

    def make_button(self, parent, text: str, command, bold: bool = False, group: list | None = None):
        """A big button in a frame that turns the focus colour while it has the keyboard. The arrow keys move between
        the buttons of its group."""
        t = self.t
        group = self.buttons if group is None else group
        ring = tk.Frame(parent, bg=t["bg"])
        b = tk.Button(ring, text=text, command=command, font=self.bold if bold else self.font, bg=t["button"],
                      fg=t["fg"], activebackground=t["active"], activeforeground=t["fg"],
                      disabledforeground=t["disabled"], relief="raised", bd=2, padx=self.size, pady=self.size // 3,
                      highlightthickness=0, cursor="hand2")
        b.pack(padx=5, pady=5)
        b.ring = ring
        b.bind("<FocusIn>", lambda e: ring.configure(bg=t["focus"]))
        b.bind("<FocusOut>", lambda e: ring.configure(bg=t["bg"]))
        for key in ("<Return>", "<KP_Enter>"):
            b.bind(key, lambda e: (b.invoke(), "break")[1])
        for key, step in (("<Left>", -1), ("<Up>", -1), ("<Right>", 1), ("<Down>", 1)):
            b.bind(key, lambda e, b=b, step=step, group=group: self.step_focus(group, b, step))
        group.append(b)
        return b

    @staticmethod
    def step_focus(group: list, here, step: int) -> str:
        i = group.index(here)
        group[(i + step) % len(group)].focus_set()
        return "break"

    def build(self) -> None:
        from modman_gui import set_app_icon   # (Breathalyzer's icon when assets/ has it, as on the mod manager)
        t, r = self.t, self.root
        r.title(TITLE)
        set_app_icon(r)
        r.configure(bg=t["bg"])
        r.columnconfigure(0, weight=1)
        # results first
        box = tk.Frame(r, bg=t["bg"])
        box.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 8))
        box.columnconfigure(1, weight=1)
        self.status_bar = tk.Frame(box, width=10, bg=t["info"])
        self.status_bar.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 14))
        self.status_head = self.label(box, self.head)
        self.status_head.grid(row=0, column=1, sticky="ew")
        self.status_more = self.label(box, anchor="nw")
        self.status_more.grid(row=1, column=1, sticky="new")
        self.status_box = box
        # buttons
        self.flow = self.Flow(r, gap=4, bg=t["bg"])
        self.flow.frame.grid(row=1, column=0, sticky="ew", padx=PAD)
        for text, cmd, bold in (("New mod", self.on_new, False), ("Open", self.on_open, False),
                                ("Save", self.on_save, False), ("Build (F5)", self.on_build, True),
                                ("Load into game (F6)", self.on_load, True), ("Find game value", self.on_find, False)):
            b = self.make_button(self.flow.frame, text, cmd, bold)
            self.flow.items.append(b.ring)
        self.where = self.label(r, fg=t["fg"])
        self.where.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(4, 4))
        # the editor
        edit = tk.Frame(r, bg=t["bg"])
        edit.grid(row=3, column=0, sticky="nsew", padx=PAD)
        r.rowconfigure(3, weight=1)
        edit.columnconfigure(1, weight=1)
        edit.rowconfigure(0, weight=1)
        self.edit_box = edit
        self.gutter = tk.Canvas(edit, width=40, bg=t["gutter_bg"], highlightthickness=0, takefocus=0, bd=0)
        self.gutter.grid(row=0, column=0, sticky="ns")
        self.editor = tk.Text(edit, font=self.mono, undo=True, maxundo=-1, autoseparators=True, wrap="none",
                              bg=t["bg"], fg=t["fg"], insertbackground=t["fg"], insertwidth=3,
                              selectbackground=t["sel_bg"], selectforeground=t["sel_fg"],
                              inactiveselectbackground=t["active"],
                              highlightthickness=4, highlightcolor=t["focus"], highlightbackground=t["line"],
                              relief="flat", padx=8, pady=6, height=12)
        self.editor.grid(row=0, column=1, sticky="nsew")
        ys = tk.Scrollbar(edit, orient="vertical", command=self.editor.yview, takefocus=0, width=max(16, self.size))
        ys.grid(row=0, column=2, sticky="ns")
        xs = tk.Scrollbar(edit, orient="horizontal", command=self.editor.xview, takefocus=0, width=max(16, self.size))
        xs.grid(row=1, column=1, sticky="ew")
        self.editor.configure(yscrollcommand=lambda a, b: (ys.set(a, b), self.on_scroll()),
                              xscrollcommand=xs.set)
        for tag in HL_TAGS:
            self.editor.tag_configure(tag, foreground=t[tag])
        self.editor.tag_configure("keyword", font=self.mono_bold)
        self.editor.tag_configure("problem_error", background=t["error_bg"])
        self.editor.tag_configure("problem_warning", background=t["warn_bg"])
        self.editor.tag_raise("sel")
        self.popup = Popup(edit, self.mono)
        # what the picked name is
        docs = tk.Frame(r, bg=t["bg"])
        docs.grid(row=4, column=0, sticky="ew", padx=PAD, pady=(8, 0))
        docs.columnconfigure(0, weight=1)
        self.doc_sig = self.label(docs, self.mono_bold)
        self.doc_sig.grid(row=0, column=0, sticky="ew")
        self.doc_text = self.label(docs)
        self.doc_text.grid(row=1, column=0, sticky="ew")
        self.footer = self.label(r, fg=t["fg"])
        self.footer.configure(text=KEYS_HINT)
        self.footer.grid(row=5, column=0, sticky="ew", padx=PAD, pady=(6, PAD))
        # keys
        ed = self.editor
        ed.bindtags((str(ed), "Text", "StudioAfter", str(r), "all"))
        ed.bind("<Up>", lambda e: self.popup_key(-1))
        ed.bind("<Down>", lambda e: self.popup_key(1))
        ed.bind("<Prior>", lambda e: self.popup_key(-POPUP_ROWS + 1) if self.popup.visible else None)
        ed.bind("<Next>", lambda e: self.popup_key(POPUP_ROWS - 1) if self.popup.visible else None)
        ed.bind("<Tab>", self.on_tab)
        ed.bind("<Shift-Tab>", lambda e: (self.leave_editor(-1), "break")[1])
        ed.bind("<Return>", self.on_return)
        ed.bind("<KP_Enter>", self.on_return)
        ed.bind("<Escape>", self.on_escape)
        ed.bind("<Control-space>", lambda e: (self.refresh_popup(auto=False), "break")[1])
        ed.bind("<Button-1>", self.hide_popup)
        ed.bind("<FocusOut>", self.hide_popup)  # (a dialog, another window: Enter mustn't put in a stale name later)
        r.bind_class("StudioAfter", "<KeyPress>", self.after_key)
        r.bind_class("StudioAfter", "<ButtonRelease-1>", lambda e: self.schedule_doc())
        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            r.bind(seq, lambda e: self.set_size(self.size + 2))
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            r.bind(seq, lambda e: self.set_size(self.size - 2))
        r.bind("<Control-0>", lambda e: self.set_size(self.start_size))
        # the window's keys, also on the editor itself: there they must stop before Tk's own Text keys (Ctrl+O would
        # put in a line break)
        for seqs, fn in ((("<F5>",), self.on_build), (("<F6>",), self.on_load), (("<F8>",), self.next_problem),
                         (("<Control-s>", "<Control-S>"), self.on_save), (("<Control-n>", "<Control-N>"), self.on_new),
                         (("<Control-o>", "<Control-O>"), self.on_open), (("<Control-F1>",), self.on_find)):
            for seq in seqs:  # (each closes the suggestion box first: a later Enter must not put in a stale name)
                r.bind(seq, lambda e, fn=fn: (self.hide_popup(), fn(), "break")[2])
                ed.bind(seq, lambda e, fn=fn: (self.hide_popup(), fn(), "break")[2])
        for i, lab in enumerate(self.popup.labels):
            lab.bind("<Button-1>", lambda e, i=i: self.pick_row(i))
        r.bind("<Configure>", lambda e: self.on_resize() if e.widget is r else None)
        r.protocol("WM_DELETE_WINDOW", self.on_close)
        self.apply_size()

    def leave_editor(self, step: int) -> None:
        w = self.editor.tk_focusNext() if step > 0 else self.editor.tk_focusPrev()
        if w is not None:
            w.focus_set()

    # --- sizes

    def apply_size(self) -> None:
        for f in (self.font, self.bold, self.mono, self.mono_bold):
            f.configure(size=self.size)
        self.head.configure(size=self.size + 4)
        for b in self.buttons:
            b.configure(padx=self.size, pady=self.size // 3)
        self.editor.configure(tabs=(self.mono.measure("0") * 4,))
        self.status_box.rowconfigure(1, minsize=2 * self.font.metrics("linespace"))
        self.on_resize()
        self.draw_gutter()

    def set_size(self, size: int) -> str:
        size = max(MIN_SIZE, min(MAX_SIZE, size))
        if size != self.size:
            self.size = size
            self.apply_size()
        return "break"

    def on_resize(self) -> None:
        w = max(200, self.root.winfo_width() - 2 * PAD - 30)
        for lab in (self.status_more, self.doc_text, self.footer, self.where):
            lab.configure(wraplength=w)
        self.status_head.configure(wraplength=w)

    # --- status and docs line

    def set_status(self, kind: str, head: str, lines=()) -> None:
        self.status_bar.configure(bg=self.t.get(kind, self.t["info"]))
        self.status_head.configure(text=head, fg=self.t["fg"] if kind in ("ok", "info") else self.t.get(kind,
                                                                                                         self.t["fg"]))
        self.status_more.configure(text="\n".join(lines))
        self.status_text = [head, *lines]

    def show_doc(self, e: dict | None) -> None:
        if e is None:
            self.doc_sig.configure(text="")
            self.doc_text.configure(text="")
            return
        sig, text = doc_lines(e)
        self.doc_sig.configure(text=sig, fg=self.t["warn"] if not ms.is_verified(e) and e["kind"] != "api"
                               else self.t["fg"])
        self.doc_text.configure(text=text)

    def schedule_doc(self) -> None:
        if self.doc_job:
            self.root.after_cancel(self.doc_job)
        self.doc_job = self.root.after(60, self.doc_at_cursor)

    def doc_at_cursor(self) -> None:
        """No list open: say what the name under the cursor is (when it is a known one)."""
        self.doc_job = None
        if self.popup.visible:
            return
        line = self.editor.get("insert linestart", "insert lineend")
        col = int(self.editor.index("insert").split(".")[1])
        for m in re.finditer(r"(?:(?:api|a)->)?[A-Za-z_]\w*", line):
            if m.start() <= col <= m.end():
                word = m.group(0)
                if word.startswith("a->"):
                    word = "api->" + word[3:]
                e = self.index.by_name.get(word)
                if e:
                    self.show_doc(e)
                return

    # --- the names (in the background)

    def load_names(self) -> None:
        cfg = self.cfg

        def work():
            try:
                core = ms.build_index(ms.build_catalog(REPO))
                self.events.put(("index", core, False, ""))
                if cfg.codemap is not None:
                    if (Path(cfg.codemap) / ms.CODEMAP_FILE).is_file():
                        cat, _ = ms.load_catalog(REPO, cfg.codemap, cfg.cache)
                        full = ms.build_index(cat)
                        self.events.put(("index", full, True, ""))
                    else:
                        self.events.put(("index", core, False, f"No code map in {cfg.codemap}: Find game value "
                                                               "searches the documented names only."))
            except Exception as e:  # noqa: BLE001 (a broken file must not stop the editor)
                self.events.put(("index", None, False, f"The names couldn't be read: {e}"))
            self.events.put(("names_done",))

        # (reading the code map holds Python for a few tenths of a second at most: the window can pause meanwhile)
        self.footer.configure(text=KEYS_HINT + "\n" + NAMES_LOADING)
        threading.Thread(target=work, daemon=True, name="mod-studio-names").start()

    def set_index(self, index, full: bool, note: str) -> None:
        if index is not None:
            self.index = index
            self.full_index = full
        self.names_note = note
        if full:
            self.names_note = f"Code map loaded: {index.codemap_count} raw game names (not tested)."

    # --- the event queue (work done in threads lands here)

    def tick(self) -> None:
        if self.closed:
            return
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "index":
                    self.set_index(*ev[1:])
                elif ev[0] == "names_done":
                    self.footer.configure(text=KEYS_HINT)
                elif ev[0] == "call":
                    ev[1]()
        except queue.Empty:
            pass
        self.tick_job = self.root.after(POLL_MS, self.tick)

    def in_thread(self, work, done) -> None:
        def run():
            try:
                result = work()
            except Exception as e:  # noqa: BLE001 (shown in the status line, never a crash)
                result = e
            self.events.put(("call", lambda: done(result)))
        threading.Thread(target=run, daemon=True, name="mod-studio-job").start()

    # --- typing

    def after_key(self, e) -> None:
        """Runs after the Text widget handled a key (the character is in already)."""
        ks = e.keysym
        if ks in ("Left", "Right", "Home", "End", "Up", "Down", "Prior", "Next") or (e.state & 0x4 and not e.char):
            self.hide_popup()
            self.schedule_doc()
        elif ks in ("BackSpace", "Delete") or (e.char and (e.char.isalnum() or e.char in "_>")):
            if self.popup.visible or ks not in ("BackSpace", "Delete"):
                self.refresh_popup(auto=True)
        elif e.char:
            self.hide_popup()
        if e.char or ks in ("BackSpace", "Delete", "Return"):
            self.schedule_highlight()
            self.update_title()

    def in_block_comment(self) -> bool:
        """The cursor is inside a /* ... */ that started on an earlier line (the line itself is checked by
        in_comment_or_string)."""
        ed = self.editor
        op = ed.search("/*", "insert linestart", backwards=True, stopindex="1.0")
        if not op or in_comment_or_string(ed.get(f"{op} linestart", op)):
            return False  # (none, or a /* inside a // comment or a text: not the start of a comment)
        return not ed.search("*/", "insert linestart", backwards=True, stopindex=op)

    def refresh_popup(self, auto: bool) -> None:
        t0 = time.perf_counter()
        line = self.editor.get("insert linestart", "insert")
        tok = token_before(line)
        if tok.blocked or (auto and (not should_open(tok, line) or self.in_block_comment())):
            self.hide_popup()
            if not auto:
                self.set_status("info", "No names fit here.", ["The cursor is in a number, or after the -> or . of "
                                                               "something that isn't api."])
            return
        items = suggestions(self.index, tok, auto)
        if not items:
            self.hide_popup()
            if not auto:
                self.set_status("info", "No names match " + (tok.text or "here") + ".",
                                ["Ctrl+F1 searches every game value, also the code map."])
            return
        self.tok = tok
        self.tok_at = self.editor.index(f"insert - {len(tok.text)} chars")
        self.popup.set(items)
        x, y = self.popup_spot(tok)
        self.popup.show(x, y)
        self.show_doc(items[0])
        self.last_popup_ms = (time.perf_counter() - t0) * 1000

    def popup_spot(self, tok: Token) -> tuple[int, int]:
        """Right under the start of the name being typed, or above it when there's more room there. The box shows only
        as many rows as fit in the editor (at least POPUP_MIN_ROWS), so with big text no row is cut off."""
        ed = self.editor
        line_h = self.mono.metrics("linespace")
        bbox = ed.bbox(f"insert - {len(tok.text)} chars") or ed.bbox("insert")
        bx, by, _, bh = bbox if bbox else (0, 0, 0, line_h)
        x0, y0 = ed.winfo_x(), ed.winfo_y()
        x, y = x0 + bx, y0 + by + bh + 2
        room_w, room_h = self.edit_box.winfo_width(), self.edit_box.winfo_height()
        above = False
        if room_h > 1:
            want = min(len(self.popup.items), POPUP_ROWS)
            fit_below = max(0, (room_h - y - 8) // line_h)
            fit_above = max(0, (y0 + by - 2 - 8) // line_h)
            above = fit_below < want and fit_above > fit_below
            self.popup.fit(max(POPUP_MIN_ROWS, min(want, fit_above if above else fit_below)))
        w, h = self.popup.size()
        if above:
            y = max(0, y0 + by - h - 2)
        if room_w > 1 and x + w > room_w:
            x = max(0, room_w - w)
        return x, y

    def popup_key(self, step: int):
        if not self.popup.visible:
            return None
        e = self.popup.move(step)
        self.show_doc(e)
        return "break"

    def pick_row(self, i: int) -> str:
        """A click on a row of the list puts that name in (i: the row on screen)."""
        k = self.popup.top + i
        if self.popup.visible and k < len(self.popup.items):
            self.popup.move(k - self.popup.sel)
            self.accept()
            self.editor.focus_set()
        return "break"

    def hide_popup(self, _e=None) -> None:
        self.popup.hide()
        self.tok = None

    def accept(self) -> bool:
        """Put the picked name in place of the name being typed. False (and the box closes) when the cursor isn't at
        that name any more: then nothing is changed."""
        if not self.popup.visible or not self.popup.items or self.tok is None:
            self.hide_popup()
            return False
        e = self.popup.items[self.popup.sel]
        tok = self.tok
        ed = self.editor
        line = ed.get("insert linestart", "insert")
        cur = token_before(line)  # (the name as it is now)
        start = ed.index(f"insert - {len(cur.text)} chars")
        if cur.text != tok.text or start != self.tok_at:
            self.hide_popup()
            return False
        self.hide_popup()
        text, s0, s1 = ms.insertion(e, receiver=(cur.recv or tok.recv or "api"))
        ed.edit_separator()
        ed.delete(start, "insert")
        ed.insert(start, text)
        ed.tag_remove("sel", "1.0", "end")
        ed.mark_set("insert", f"{start} + {s0} chars")
        if s1 > s0:
            ed.tag_add("sel", f"{start} + {s0} chars", f"{start} + {s1} chars")
        ed.edit_separator()
        self.show_doc(e)
        self.schedule_highlight()
        self.update_title()
        return True

    def on_tab(self, _e=None):
        if not (self.popup.visible and self.accept()):
            self.editor.insert("insert", "    ")
        return "break"

    def on_return(self, _e=None):
        if self.popup.visible and self.accept():
            return "break"
        ed = self.editor
        line = ed.get("insert linestart", "insert")
        indent = re.match(r"[ \t]*", line).group(0)
        if line.rstrip().endswith("{"):
            indent += "    "
        ed.edit_separator()
        if ed.tag_ranges("sel"):
            ed.delete("sel.first", "sel.last")
        ed.insert("insert", "\n" + indent)
        ed.see("insert")
        self.schedule_highlight()
        self.update_title()
        return "break"

    def on_escape(self, _e=None):
        if self.popup.visible:
            self.hide_popup()
            return "break"
        return None

    # --- colours and line numbers (only the visible lines, a moment after the last key)

    def schedule_highlight(self) -> None:
        if self.hl_job:
            self.root.after_cancel(self.hl_job)
        self.hl_job = self.root.after(HIGHLIGHT_MS, self.highlight_visible)

    def on_scroll(self) -> None:
        self.schedule_highlight()

    def visible_lines(self) -> tuple[int, int]:
        ed = self.editor
        first = int(ed.index("@0,0").split(".")[0])
        if not ed.winfo_ismapped():  # (not on screen yet: the first screenful)
            return first, first + 80
        last = int(ed.index(f"@0,{max(1, ed.winfo_height())}").split(".")[0])
        return first, max(first, last)

    def highlight_visible(self) -> None:
        self.hl_job = None
        if self.closed:
            return
        ed = self.editor
        first, last = self.visible_lines()
        last = min(last + 1, int(ed.index("end-1c").split(".")[0]))
        start = f"{first}.0"
        # a /* comment that starts above the visible lines
        op = ed.search("/*", start, backwards=True, stopindex="1.0")
        if op:
            cl = ed.search("*/", start, backwards=True, stopindex="1.0")
            if not cl or ed.compare(cl, "<", op):
                start = op
        text = ed.get(start, f"{last}.end")
        line0, col0 = (int(x) for x in ed.index(start).split("."))
        for tag in HL_TAGS:
            ed.tag_remove(tag, start, f"{last}.end")
        starts = [0]
        for m in re.finditer("\n", text):
            starts.append(m.end())

        def idx(off: int) -> str:
            k = bisect.bisect_right(starts, off) - 1
            col = off - starts[k] + (col0 if k == 0 else 0)
            return f"{line0 + k}.{col}"

        for m in HL_RE.finditer(text):
            ed.tag_add(m.lastgroup, idx(m.start()), idx(m.end()))
        self.draw_gutter()

    def draw_gutter(self) -> None:
        ed, g = self.editor, self.gutter
        g.delete("all")
        n_lines = int(ed.index("end-1c").split(".")[0])
        digits = max(3, len(str(n_lines)))
        width = self.mono.measure("0") * digits + 16
        if int(g.cget("width")) != width:
            g.configure(width=width)
        bad = {p.line: p.level for p in self.problems if p.line}
        first, last = self.visible_lines()
        for ln in range(first, min(last, n_lines) + 1):
            info = ed.dlineinfo(f"{ln}.0")
            if not info:
                continue
            y = info[1] + ed.winfo_y() - g.winfo_y()
            level = bad.get(ln)
            color = self.t["error"] if level == "error" else self.t["warn"] if level else self.t["gutter_fg"]
            g.create_text(width - 8, y, anchor="ne", text=str(ln), fill=color,
                          font=self.mono_bold if level else self.mono)

    # --- files

    def update_title(self) -> None:
        name = self.mod.name if self.mod else "no mod"
        star = " *" if self.mod and self.editor.edit_modified() else ""
        self.root.title(f"{TITLE} - {name}{star}")

    def open_mod(self, mod: Mod) -> bool:
        """Open mod in the editor. The open mod's changes are saved first; when that fails nothing else happens (the
        text stays in the editor) and it returns False."""
        self.hide_popup()
        if self.busy:
            self.set_status("busy", f"{self.busy}...", [f"Wait until it's done, then open {mod.name}."])
            return False
        if self.mod and self.editor.edit_modified() and not self.save():
            self.status_text.append(f"{mod.name} wasn't opened, so your text is still here. Fix that, or copy the "
                                    "text out, then open it again.")
            self.status_more.configure(text="\n".join(self.status_text[1:]))
            return False
        try:
            text = mod.source.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            self.set_status("error", f"Can't open {mod.name}.", [str(e)])
            return False
        self.mod = mod
        self.loaded_name = None
        self.problems = []
        ed = self.editor
        ed.delete("1.0", "end")
        ed.insert("1.0", text)
        ed.edit_reset()
        ed.edit_modified(False)
        ed.mark_set("insert", "1.0")
        ed.see("insert")
        self.where.configure(text=f"Mod: {mod.name}    Folder: {mod.folder}")
        self.update_title()
        self.highlight_visible()
        write_settings(self.cfg.settings, {**read_settings(self.cfg.settings), "last": mod.name})
        self.set_status("ok", f"Opened {mod.name}.", ["F5 builds it, F6 loads it into the game."])
        return True

    def save(self) -> bool:
        if not self.mod:
            return False
        text = self.editor.get("1.0", "end-1c")
        try:
            self.mod.source.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8", newline="\n")
        except OSError as e:
            self.set_status("error", "Not saved.", [f"{self.mod.source}: {e.strerror or e}"])
            return False
        self.editor.edit_modified(False)
        self.update_title()
        return True

    def on_save(self) -> None:
        if not self.mod:
            self.set_status("info", "Nothing to save yet.", ["Press Ctrl+N for a new mod, or Ctrl+O to open one."])
        elif self.save():
            self.set_status("ok", f"Saved {self.mod.source.name}.", [])

    def on_close(self) -> None:
        """The window's X: save first. When that fails the window stays open (the text is still in it); pressing X
        again closes it without saving."""
        if self.mod and self.editor.edit_modified() and not self.save() and not self.close_armed:
            self.close_armed = True
            self.set_status("error", "Not saved, so the window stays open.",
                            [*self.status_text[1:2], "Fix that or copy your text out. Press the window's X again to "
                                                     "close it and lose the changes."])
            return
        self.shutdown()
        self.root.destroy()

    # --- dialogs (big text, keyboard)

    def dialog(self, title: str):
        t = self.t
        self.hide_popup()
        d = tk.Toplevel(self.root, bg=t["bg"])
        if self.hidden:
            d.withdraw()
        d.title(title)
        d.transient(self.root)
        d.configure(padx=PAD, pady=PAD)
        d.columnconfigure(0, weight=1)
        d.bind("<Escape>", lambda e: self.close_dialog(d))
        d.protocol("WM_DELETE_WINDOW", lambda: self.close_dialog(d))
        return d

    def close_dialog(self, d) -> None:
        d.destroy()
        self.editor.focus_set()

    def listbox(self, parent, rows: int):
        t = self.t
        lb = tk.Listbox(parent, font=self.font, height=rows, bg=t["bg"], fg=t["fg"], selectbackground=t["sel_bg"],
                        selectforeground=t["sel_fg"], highlightthickness=4, highlightcolor=t["focus"],
                        highlightbackground=t["line"], activestyle="none", exportselection=False, relief="flat")
        return lb

    def entry(self, parent):
        t = self.t
        return tk.Entry(parent, font=self.mono, bg=t["bg"], fg=t["fg"], insertbackground=t["fg"], insertwidth=3,
                        highlightthickness=4, highlightcolor=t["focus"], highlightbackground=t["line"],
                        relief="flat")

    @staticmethod
    def list_step(lb, step: int) -> str:
        n = lb.size()
        if n:
            cur = lb.curselection()
            i = (cur[0] + step) % n if cur else 0
            lb.selection_clear(0, "end")
            lb.selection_set(i)
            lb.activate(i)
            lb.see(i)
            lb.event_generate("<<ListboxSelect>>")
        return "break"

    def refuse_while_busy(self) -> bool:
        """True (and says so) while a build or a load runs: its result belongs to the mod that is open."""
        if self.busy:
            self.set_status("busy", f"{self.busy}...", ["Wait until it's done: it is for the mod that is open now."])
            return True
        return False

    def on_new(self) -> None:
        if self.refuse_while_busy():
            return
        d = self.dialog("New mod")
        self.label(d, self.head, text="New mod").grid(row=0, column=0, sticky="w")
        self.label(d, text="Name (small letters, digits and _): it is also the console command.").grid(
            row=1, column=0, sticky="w", pady=(8, 2))
        name = self.entry(d)
        name.grid(row=2, column=0, sticky="ew")
        self.label(d, text="Start from:").grid(row=3, column=0, sticky="w", pady=(12, 2))
        keys = list(TEMPLATES)
        lb = self.listbox(d, len(keys))
        for k in keys:
            lb.insert("end", f"{TEMPLATES[k][0]}: {TEMPLATES[k][1]}")
        lb.selection_set(0)
        lb.grid(row=4, column=0, sticky="ew")
        msg = self.label(d, fg=self.t["error"], wraplength=self.font.measure("0") * 50)
        msg.grid(row=5, column=0, sticky="w", pady=(8, 0))

        def create(_e=None):
            nm = name.get().strip().lower()
            sel = lb.curselection()
            try:
                mod = new_mod(self.cfg.root, nm, keys[sel[0] if sel else 0])
            except (StudioError, OSError) as e:
                msg.configure(text=str(e))
                name.focus_set()
                return "break"
            self.close_dialog(d)
            if self.open_mod(mod):
                self.set_status("ok", f"Made {mod.name}.", [f"In {mod.folder}. F5 builds it, F6 loads it into the "
                                                            "game."])
            else:
                self.status_text.append(f"{mod.name} was made in {mod.folder}: open it with Ctrl+O later.")
                self.status_more.configure(text="\n".join(self.status_text[1:]))
            return "break"

        row = tk.Frame(d, bg=self.t["bg"])
        row.grid(row=6, column=0, sticky="w", pady=(12, 0))
        group: list = []
        self.make_button(row, "Create", create, True, group).ring.pack(side="left")
        self.make_button(row, "Cancel", lambda: self.close_dialog(d), False, group).ring.pack(side="left")
        for w in (name, lb):
            w.bind("<Return>", create)
        name.bind("<Up>", lambda e: self.list_step(lb, -1))
        name.bind("<Down>", lambda e: self.list_step(lb, 1))
        name.focus_set()
        self.new_dialog = {"window": d, "name": name, "list": lb, "create": create, "message": msg}

    def on_open(self) -> None:
        if self.refuse_while_busy():
            return
        names = list_mods(self.cfg.root)
        if not names:
            self.set_status("info", "No mods yet.", [f"Press Ctrl+N to make one (in {self.cfg.root})."])
            return
        d = self.dialog("Open mod")
        self.label(d, self.head, text="Open mod").grid(row=0, column=0, sticky="w")
        self.label(d, text=f"In {self.cfg.root}").grid(row=1, column=0, sticky="w", pady=(4, 8))
        lb = self.listbox(d, min(12, max(3, len(names))))
        for n in names:
            lb.insert("end", n)
        cur = names.index(self.mod.name) if self.mod and self.mod.name in names else 0
        lb.selection_set(cur)
        lb.activate(cur)
        lb.grid(row=2, column=0, sticky="ew")

        def go(_e=None):
            sel = lb.curselection()
            if sel:
                self.close_dialog(d)
                self.open_mod(Mod(names[sel[0]], self.cfg.root))
            return "break"

        lb.bind("<Return>", go)
        lb.bind("<Double-Button-1>", go)
        lb.focus_set()
        self.open_dialog = {"window": d, "list": lb, "go": go, "names": names}

    def on_find(self) -> None:
        """Find game value: search every name, the code map too. It starts from the name being typed, and the name
        picked takes that name's place."""
        tok = token_before(self.editor.get("insert linestart", "insert"))
        tok_at = self.editor.index(f"insert - {len(tok.text)} chars")
        d = self.dialog("Find game value")
        self.label(d, self.head, text="Find game value").grid(row=0, column=0, sticky="w")
        note = self.names_note or ("The code map is still loading..." if self.cfg.codemap is not None
                                   and not self.full_index else "")
        self.label(d, text="Type words (like player position or stamina). Enter puts the picked one in your code."
                   + (f"\n{note}" if note else "")).grid(row=1, column=0, sticky="w", pady=(4, 8))
        box = self.entry(d)
        box.grid(row=2, column=0, sticky="ew")
        lb = self.listbox(d, 12)
        lb.configure(font=self.mono, width=70)
        lb.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        d.rowconfigure(3, weight=1)
        sig = self.label(d, self.mono_bold, wraplength=self.font.measure("0") * 60)
        sig.grid(row=4, column=0, sticky="w", pady=(8, 0))
        info = self.label(d, wraplength=self.font.measure("0") * 60)
        info.grid(row=5, column=0, sticky="w")
        found: list[dict] = []

        def run(_e=None):
            found[:] = ms.search(self.index, box.get(), 60, docs=True)
            lb.delete(0, "end")
            for i, e in enumerate(found):
                lb.insert("end", ms.display_text(e, 80))
                if e["kind"] == "codemap":
                    lb.itemconfigure(i, fg=self.t["warn"])
            if found:
                lb.selection_set(0)
                lb.activate(0)
            show()

        def show(_e=None):
            sel = lb.curselection()
            if sel and sel[0] < len(found):
                s, txt = doc_lines(found[sel[0]])
                sig.configure(text=s)
                info.configure(text=txt)
            else:
                sig.configure(text="" if box.get().strip() else "")
                info.configure(text="Nothing found." if box.get().strip() else "")

        def put(_e=None):
            sel = lb.curselection()
            if sel and sel[0] < len(found):
                e = found[sel[0]]
                text, s0, s1 = ms.insertion(e, receiver=tok.recv or "api")
                self.close_dialog(d)
                self.hide_popup()
                ed = self.editor
                ed.edit_separator()
                ed.tag_remove("sel", "1.0", "end")
                start = ed.index("insert")
                # the half-typed name the search started from goes: the found name takes its place
                if tok.ident and ed.index(f"insert - {len(tok.text)} chars") == tok_at \
                        and ed.get(tok_at, "insert") == tok.text:
                    ed.delete(tok_at, "insert")
                    start = tok_at
                ed.insert(start, text)
                ed.mark_set("insert", f"{start} + {s0} chars")
                if s1 > s0:
                    ed.tag_add("sel", f"{start} + {s0} chars", f"{start} + {s1} chars")
                ed.edit_separator()
                self.show_doc(e)
                self.schedule_highlight()
                self.update_title()
            return "break"

        box.bind("<KeyRelease>", lambda e: run() if e.keysym not in ("Up", "Down", "Return", "Escape") else None)
        box.bind("<Up>", lambda e: self.list_step(lb, -1))
        box.bind("<Down>", lambda e: self.list_step(lb, 1))
        box.bind("<Return>", put)
        lb.bind("<Return>", put)
        lb.bind("<Double-Button-1>", put)
        lb.bind("<<ListboxSelect>>", show)
        if tok.ident:
            box.insert(0, tok.ident)
            run()
        box.focus_set()
        self.find_dialog = {"window": d, "box": box, "list": lb, "found": found, "run": run, "put": put,
                            "sig": sig, "info": info}

    # --- build and load

    def toolchain(self) -> bn.Toolchain:
        return bn.find_toolchain(self.cfg.install or bn.default_install())

    def names_map(self) -> dict[str, str]:
        return {e["name"]: e["address"] for e in self.index.entries if e["kind"] == "address" and e["address"]}

    def mark_problems(self) -> None:
        ed = self.editor
        for tag in ("problem_error", "problem_warning"):
            ed.tag_remove(tag, "1.0", "end")
        for p in self.problems:
            if p.line:
                ed.tag_add(f"problem_{p.level}", f"{p.line}.0", f"{p.line}.end + 1 chars")
        self.draw_gutter()

    def on_build(self, then=None) -> None:
        self.hide_popup()
        if self.busy:
            self.set_status("busy", f"{self.busy}...", ["Wait until it's done."])
            return
        if not self.mod:
            self.set_status("info", "Make or open a mod first.", ["Ctrl+N makes one, Ctrl+O opens one."])
            return
        if not self.save():
            return
        try:
            tc = self.toolchain()
        except bn.BuildError as e:
            self.set_status("error", "Can't build: no compiler.", [str(e), "Start Mod Studio with --install <your "
                                                                         "Savage Wind folder>."])
            return
        mod, names, index = self.mod, self.names_map(), self.index
        self.busy = "Building"
        self.problems = []
        self.mark_problems()
        self.set_status("busy", f"Building {mod.name}...", ["At low priority, so the game keeps its frame rate."])
        self.in_thread(lambda: build_mod(mod, tc, names, index=index), lambda r: self.build_done(r, then, mod))

    def build_done(self, r, then=None, mod: Mod | None = None) -> None:
        """A build ended. mod: the mod that was built (its errors are marked only while it is the open one)."""
        self.busy = ""
        if isinstance(r, Exception):
            self.set_status("error", "The build stopped.", [str(r)])
            return
        if mod is not None and (self.mod is None or mod.folder != self.mod.folder):
            self.set_status("ok" if r.ok else "error", f"{mod.name}: build {'done' if r.ok else 'failed'}.",
                            [f"Another mod is open now. Open {mod.name} again to see its errors."
                             if not r.ok else f"{r.dll.name} in {r.seconds:.1f} s."])
            if r.ok and then:
                then(r)
            return
        self.problems = r.problems
        self.problem_at = -1
        self.mark_problems()
        if not r.ok:
            errs = r.errors
            first = next((p for p in errs if p.line), errs[0] if errs else None)
            more = f" (and {len(errs) - 1} more: F8 shows the next)" if len(errs) > 1 else ""
            line = f"Line {first.line}: " if first and first.line else ""
            self.set_status("error", f"Build failed: {len(errs)} error{'s' if len(errs) != 1 else ''}.",
                            [line + (first.plain if first else "see the compiler's words") + more])
            if first and first.line:
                self.goto_line(first.line)
                self.problem_at = self.problems.index(first)
            return
        warn = r.warnings
        lines = [f"{r.dll.name} in {r.seconds:.1f} s." + (" Press F6 to load it into the game." if not then else "")]
        if warn:
            w = warn[0]
            lines.append(f"{len(warn)} warning{'s' if len(warn) != 1 else ''}: "
                         + (f"line {w.line}: " if w.line else "") + w.plain + (" F8 shows them." if len(warn) > 1
                                                                                else ""))
        self.set_status("warn" if warn else "ok", f"Built {(mod or self.mod).name}.", lines)
        self.last_build = r
        if then:
            then(r)

    def goto_line(self, line: int) -> None:
        ed = self.editor
        self.hide_popup()
        ed.mark_set("insert", f"{line}.0 lineend")
        ed.see("insert")
        ed.focus_set()

    def next_problem(self) -> None:
        withline = [p for p in self.problems if p.line]
        if not withline:
            self.set_status("info", "No errors marked.", ["F5 builds the mod."])
            return
        self.problem_at = (self.problem_at + 1) % len(self.problems)
        while not self.problems[self.problem_at].line:
            self.problem_at = (self.problem_at + 1) % len(self.problems)
        p = self.problems[self.problem_at]
        self.goto_line(p.line)
        n = withline.index(p) + 1
        self.set_status("error" if p.level == "error" else "warn", f"Line {p.line}: {p.plain}",
                        [f"{p.level.capitalize()} {n} of {len(withline)}. The compiler said: {p.message}"])

    def needs_build(self) -> bool:
        if not self.mod or self.editor.edit_modified():
            return True
        try:
            return self.mod.source.stat().st_mtime_ns > self.mod.dll.stat().st_mtime_ns
        except OSError:
            return True

    def on_load(self) -> None:
        self.hide_popup()
        if self.busy:
            self.set_status("busy", f"{self.busy}...", ["Wait until it's done."])
            return
        if not self.mod:
            self.set_status("info", "Make or open a mod first.", ["Ctrl+N makes one, Ctrl+O opens one."])
            return
        if self.needs_build():
            # (the mod is taken now: the result goes to this mod, whatever is open when the build ends)
            self.on_build(then=lambda r, m=self.mod: self.load(r.dll, r.hooks, m))
            return
        src = self.mod.source.read_text(encoding="utf-8", errors="replace")
        hooks, _ = find_hooks(src, self.names_map() or botw_names())
        self.load(self.mod.dll, hooks)

    def load(self, dll: Path, hooks, mod: Mod | None = None) -> None:
        mod, cfg = mod or self.mod, self.cfg
        known = self.loaded_name if mod is self.mod else None
        self.busy = "Loading"
        self.set_status("busy", f"Putting {mod.name} into the game...", [f"Asking the game on port {cfg.port}."])
        self.in_thread(lambda: into_game(mod, dll, cfg.port, known, hooks, cfg.library),
                       lambda out: self.load_done(out, mod))

    def load_done(self, out, mod: Mod | None = None) -> None:
        self.busy = ""
        if isinstance(out, Exception):
            self.set_status("error", "Loading stopped.", [str(out)])
            return
        if out.loaded_name and (mod is None or mod is self.mod):
            self.loaded_name = out.loaded_name
        self.set_status(out.kind, out.lines[0], out.lines[1:4])


# ---------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    from modman_gui import dpi_aware, port_arg, size_arg
    ap = argparse.ArgumentParser(prog="mod_studio.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", type=Path, default=None,
                    help="the folder for your mods' sources (default: Documents\\SavageWind\\mods-src)")
    ap.add_argument("--size", type=size_arg, default=DEFAULT_SIZE, help=f"text size in points (default {DEFAULT_SIZE})")
    ap.add_argument("--dark", action="store_true", help="light text on a dark background")
    ap.add_argument("--port", type=port_arg, default=control.DEFAULT_PORT,
                    help="the game's control port (SAVAGEWIND_CONTROL, default 7777)")
    ap.add_argument("--install", type=Path, default=None, help="your Savage Wind folder (for its zig)")
    ap.add_argument("--library", default=None, help="the mod manager's library (as for modman)")
    ap.add_argument("--codemap", type=Path, default=None,
                    help="the local code map folder (default: out\\codemap in this repo, when it is there)")
    ap.add_argument("--no-codemap", action="store_true", help="don't read the code map")
    a = ap.parse_args(argv)
    if tk is None:
        print(f"{TITLE} needs Python's tkinter.")
        return 2
    cfg = Config(root=(a.dir or default_mods_dir()).resolve(), port=a.port, install=a.install, library=a.library,
                 codemap=None if a.no_codemap else (a.codemap or ms.default_codemap_dir(REPO)))
    try:
        cfg.root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"Can't make {cfg.root}: {e}")
        return 1
    dpi_aware()
    try:
        root = tk.Tk()
    except tk.TclError as e:
        print(f"Can't open a window: {e}")
        return 2
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{int(sw * 0.8)}x{int(sh * 0.85)}+{int(sw * 0.1)}+{int(sh * 0.04)}")
    View(root, cfg, size=a.size, dark=a.dark)
    root.lift()
    with contextlib.suppress(tk.TclError):
        root.focus_force()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
