#!/usr/bin/env python3
"""The in-game mod menu: the files the game's Mods page reads and writes, and using the player's changes.

    python tools/modmenu.py setup                  build the Mod menu mod, add it to the library, write the mod list
    python tools/modmenu.py show                   the list as the game shows it, and the changes that wait
    python tools/modmenu.py apply                  use the changes from the game now: on/off, order, then deploy
    python tools/modmenu.py undo                   throw away the changes from the game
    python tools/modmenu.py sync                   write the game's mod list again (after changes in Mod Manager)
    python tools/modmenu.py play                   apply, then start the game (python tools/modman.py run)
    python tools/modmenu.py set <mod> on|off       change what the next start uses (only while the game is closed)
    python tools/modmenu.py move <mod> <position>

<mod> is an id, a name, or a number from `show` (its place in the game's list, as the game's console commands count).
Every command takes --library <folder>, as tools/modman.py does. All but show refuse while Savage Wind runs (the
running game's changes are based on the list it read at its start). docs/mod-menu.md explains the menu.

The files that tie the game and Mod Manager together (all in the mod library):
    config.json             what is installed and on, in order (modman owns it)
    menu\\menu.txt           what the game shows; only this tool writes it; the game reads it once, at its start
    menu\\base-<serial>.txt  a copy of each menu.txt, by its serial: what the game showed when it wrote pending.txt
    menu\\pending.txt        what the player wants at the next start; the game writes it while it runs
    menu\\apply.json         only while apply runs: config.json as it was (a cut-off apply is finished next time)
The menu files are UTF-8 text, one record per line, fields split by tabs. Nothing in them can name a path or a
command: pending.txt holds only mod ids, on/off and their order. apply uses only what the player changed in the game
(pending.txt against the list it was made from), so changes made in Mod Manager meanwhile stay.
"""
from __future__ import annotations

import argparse
import base64
import errno
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import build_native as bn  # noqa: E402
import exefs_patch as ep  # noqa: E402
import modman as mm  # noqa: E402

REPO = os.path.dirname(TOOLS)
MOD_ID = "botw-mod-loader.modmenu"   # the Mod menu mod's id (its mod.toml): never listed in the game
MOD_NAME = "Mod menu"
MOD_DLL = "modmenu.dll"
MOD_SRC = os.path.join(REPO, "native", "examples", "modmenu")
MOD_SOURCES = ("modmenu.c", "opt_page_a.c", "opt_page_b.c")
MOD_OUT = os.path.join(mm.MODHOST_DIR, "modmenu-mods", "modmenu")  # the built DLL and the mod.toml setup adds
MANIFEST = f"""# The Mod menu: a Mods page in the game's Options (docs/mod-menu.md). Written by tools/modmenu.py setup.
[mod]
id = "{MOD_ID}"
name = "{MOD_NAME}"
version = "0.1.0"
authors = ["giruka"]
description = "Adds a Mods page to the game's Options: turn mods on or off and change their order."
game_versions = ["1.9.0"]   # it reads the 1.9.0 option table (and only runs with Savage Wind 0.4.1-alpha.4)
loader_api = 1

[code]
windows = "{MOD_DLL}"
"""

MENU_DIR = "menu"
MENU = "menu.txt"
PENDING = "pending.txt"
PENDING_BAD = "pending.bad.txt"     # a pending file that couldn't be read (kept for a look)
PENDING_OLD = "pending.old.txt"     # one made from a mod list with no copy left (what the player changed is unknown)
PENDING_USED = "pending.used.txt"   # the last one used
BASE = "base-{}.txt"                # menu.txt's copy by serial (native/examples/modmenu/modmenu.c reads it too)
BASE_RE = re.compile(r"base-([0-9a-f]{16})\.txt")
SERIAL_RE = re.compile(r"[0-9a-f]{16}")
KEEP_BASES = 3                      # copies kept besides the ones in use
MARKER = "apply.json"               # an apply in progress: config.json as it was before it
MENU_MAGIC = "swmodmenu"
PENDING_MAGIC = "swmodmenu-pending"
FORMAT = 1
NAME_MAX, ABOUT_MAX, EXTRA_MAX, NOTE_MAX = 28, 70, 60, 80   # (the game shows about 82 characters on its line)
MAX_LINES = 4096   # a menu file never has more lines than this (64 mods at most are shown; the rest is a bad file)
BELOW_NORMAL = 0x4000  # BELOW_NORMAL_PRIORITY_CLASS: builds stay out of the way of a running game

NOTE_DAMAGED = "The game's changes file was damaged and was not used."
NOTE_STALE = "The game's changes were made from an old mod list and were not used."
KIND_ABOUT = {"romfs": "Changes game files.", "dlc-romfs": "Changes DLC files.", "cheats": "Adds cheats.",
              "native": "Code mod.", "exefs-patch": "Patches the game code."}


class MenuError(mm.ModmanError):
    """Something the player has to fix; the message says what."""


say = mm.say


# ---------------------------------------------------------------- text the game can show

# The ASCII letters the game's option font has no glyph for (the game test), and what is shown instead
FONT_MISSING = {"{": "(", "}": ")", "|": "/", "`": "'", "^": "'", "~": "-", "%": " percent", "#": "no. ",
                "@": " at ", "$": "?"}


def ascii_text(s, n: int) -> str:
    """s as one line of plain ASCII, at most n characters: accents dropped, other letters made '?', tabs and line
    breaks made spaces, runs of spaces made one, and "..." at the end when it was cut. The game's option fonts miss
    many letters, and also # $ % @ ^ ` { | } ~ (the game test showed a gap for each): those become words or look-alikes
    (FONT_MISSING). The mod treats all of it as plain text."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    out = []
    for c in s:
        if unicodedata.category(c) == "Mn":
            continue
        if c.isspace() or unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp"):
            out.append(" ")
        elif c in FONT_MISSING:
            out.append(FONT_MISSING[c])
        elif 32 <= ord(c) < 127:
            out.append(c)
        else:
            out.append("?")
    s = " ".join("".join(out).split())
    if len(s) > n:
        s = s[:max(1, n - 3)].rstrip() + "..."
    return s


def ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th, ... 11th, 12th, 13th, 21st (the Order page's values)."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------------------------------------------------------- the library's menu folder

def menu_dir(lib: mm.Library) -> str:
    return os.path.join(lib.root, MENU_DIR)


def menu_path(lib: mm.Library, name: str = MENU) -> str:
    return os.path.join(menu_dir(lib), name)


def config_stamp(lib: mm.Library) -> str:
    """config.json's size and change time: menu.txt says which config it was made from."""
    try:
        st = os.stat(lib.config_path)
    except OSError:
        return "0:0"
    return f"{st.st_size}:{st.st_mtime_ns}"


def write_text(path: str, text: str) -> None:
    """Written to a temporary name, then renamed over `path` (a reader never sees half a file)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _lines(path: str) -> list[list[str]] | None:
    """The tab-split lines of a menu file; None when it isn't there. Raises MenuError when it can't be read."""
    try:
        with open(path, "rb") as f:
            raw = f.read(1 << 20)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise MenuError(f"can't read {path}: {e.strerror or e}") from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise MenuError(f"{path} isn't UTF-8 text") from None
    lines = [ln.rstrip("\r").split("\t") for ln in text.split("\n") if ln.strip("\r")]
    if len(lines) > MAX_LINES:
        raise MenuError(f"{path} is far too long")
    return lines


def _magic_ok(line: list[str], magic: str) -> bool:
    return len(line) >= 2 and line[0] == magic and line[1].isdigit() and int(line[1]) >= 1


@dataclass
class MenuMod:
    id: str
    on: bool
    flags: set[str]
    name: str
    about: str = ""
    extra: str = ""


@dataclass
class Menu:
    serial: str = ""
    config: str = ""
    note: str = ""
    use: str = ""                  # "modman": Play in Mod Manager uses the changes; "command": modmenu.py play does
    mods: list[MenuMod] = field(default_factory=list)


def read_menu(lib: mm.Library) -> Menu | None:
    """menu.txt as written by sync; None when there is none or it can't be used (sync writes it again)."""
    return _parse_menu(menu_path(lib))


def _parse_menu(path: str) -> Menu | None:
    try:
        lines = _lines(path)
    except MenuError:
        return None
    if not lines or not _magic_ok(lines[0], MENU_MAGIC):
        return None
    menu = Menu()
    for ln in lines[1:]:
        if ln[0] == "serial" and len(ln) >= 2:
            menu.serial = ln[1]
        elif ln[0] == "config" and len(ln) >= 2:
            menu.config = ln[1]
        elif ln[0] == "note":
            menu.note = ln[1] if len(ln) >= 2 else ""
        elif ln[0] == "use" and len(ln) >= 2:
            menu.use = ln[1]
        elif ln[0] == "mod" and len(ln) >= 5 and mm.good_id(ln[1]):
            flags = {f for f in ln[3].split(",") if f}
            menu.mods.append(MenuMod(ln[1], ln[2] == "on", flags, ln[4], *(ln[5:7])))
    return menu


@dataclass
class Pending:
    serial: str = ""
    wanted: list[tuple[str, bool]] = field(default_factory=list)   # (id, on), in the wanted order


def read_pending(lib: mm.Library) -> Pending | None:
    """pending.txt: None when there is none. Raises MenuError when it is damaged (a bad first line, a line that
    can't be read, no mods)."""
    lines = _lines(menu_path(lib, PENDING))
    if lines is None:
        return None
    if not lines or not _magic_ok(lines[0], PENDING_MAGIC):
        raise MenuError("pending.txt has no swmodmenu-pending line first")
    p = Pending()
    seen = set()
    for ln in lines[1:]:
        if ln[0] == "serial" and len(ln) >= 2:
            p.serial = ln[1]
        elif ln[0] == "mod":
            if len(ln) < 3 or ln[2] not in ("on", "off") or not mm.good_id(ln[1]):
                raise MenuError(f"pending.txt has a bad mod line: {'|'.join(ln)[:60]}")
            if ln[1] not in seen:
                seen.add(ln[1])
                p.wanted.append((ln[1], ln[2] == "on"))
    if not p.wanted:
        raise MenuError("pending.txt lists no mods")
    return p


def has_pending(lib: mm.Library) -> bool:
    """apply has work to do: changes from the game wait in pending.txt, or an apply was cut off (for modman deploy /
    run: apply first)."""
    return os.path.isfile(menu_path(lib, PENDING)) or os.path.isfile(menu_path(lib, MARKER))


def read_base(lib: mm.Library, serial: str) -> list[tuple[str, bool]] | None:
    """What the game listed when it read the menu.txt with this serial: (id, on) of the listed mods, in order. From
    menu.txt when it is that one, else from its copy base-<serial>.txt. None when neither is there."""
    if not SERIAL_RE.fullmatch(serial or ""):
        return None
    for path in (menu_path(lib), menu_path(lib, BASE.format(serial))):
        menu = _parse_menu(path)
        if menu and menu.serial == serial:
            return [(m.id, m.on) for m in menu.mods if "hidden" not in m.flags]
    return None


def _pending_and_base(lib: mm.Library) -> tuple[Pending | None, list[tuple[str, bool]] | None]:
    """pending.txt and the list it was made from; (None, None) when there is none or it is damaged."""
    try:
        pending = read_pending(lib)
    except MenuError:
        return None, None
    return pending, read_base(lib, pending.serial) if pending else None


# ---------------------------------------------------------------- what the game shows for each mod

@dataclass
class Row:
    id: str
    name: str
    about: str
    extra: str
    on: bool                    # in config.json now
    wanted: bool                # at the next start (pending.txt, else as now)
    position: int | None        # 1-based among the listed mods (None: hidden)
    wanted_position: int | None
    rebuild: bool               # hooks or code patches: the next start can take 10-20 minutes
    hidden: bool                # the Mod menu mod itself, or a secret mod ([mod] secret)
    wiiu: bool

    @property
    def changed(self) -> bool:
        return not self.hidden and (self.on != self.wanted or self.position != self.wanted_position)

    @property
    def flags(self) -> str:
        return ",".join(f for f, on in (("hidden", self.hidden), ("rebuild", self.rebuild), ("wiiu", self.wiiu))
                        if on)


def _patch_files(root: str) -> list[str]:
    try:
        return [p for p, rel, _ in mm.walk_files(root) if rel.lower().endswith(ep.PATCH_EXTS)]
    except OSError:
        return []


def _has_code_patches(lib: mm.Library, root: str, kinds: list[str]) -> bool:
    """True when the mod's ExeFS patches change game code (a rebuild). Without the game's files to check against,
    a patch mod counts as one."""
    if "exefs-patch" not in kinds:
        return False
    files = _patch_files(root)
    exefs = os.path.join(lib.install or "", "extracted", "exefs")
    if not files or not os.path.isfile(os.path.join(exefs, "main")):
        return bool(files)
    try:
        return ep.check(exefs, files).needs_rebuild
    except (ep.PatchError, OSError, ValueError):
        return True


def _about(m: dict, man) -> str:
    if man and man.description.strip():
        return man.description
    parts = list(dict.fromkeys(KIND_ABOUT[k] for k in m.get("kinds", []) if k in KIND_ABOUT))
    return " ".join(parts[:2])


def _manifests(lib: mm.Library) -> dict[str, object]:
    out = {}
    for m in lib.mods:
        root = os.path.join(lib.mods_dir, m["id"])
        try:
            out[m["id"]] = mm.read_manifest(root)[0] if os.path.isdir(root) else None
        except OSError:
            out[m["id"]] = None
    return out


def describe_mods(lib: mm.Library) -> list[Row]:
    """The library's mods as the game lists them, as they are now (no pending changes)."""
    mans = _manifests(lib)
    by_manifest = {man.id: m for m in lib.mods for man in [mans[m["id"]]] if man}
    names = {m["id"]: m["name"] for m in lib.mods}
    needed_by: dict[str, list[str]] = {}
    for m in lib.mods:
        man = mans[m["id"]]
        for dep in (man.dependencies if man else {}):
            target = by_manifest.get(dep) or next((x for x in lib.mods if x["id"] == dep), None)
            if target:
                needed_by.setdefault(target["id"], []).append(m["name"])
    rows, pos = [], 0
    for m in lib.mods:
        man = mans[m["id"]]
        # (never listed: the Mod menu mod itself, and a secret mod ([mod] secret: an easter egg, a hidden extra's
        # parts), which only the launcher's switch turns on or off)
        hidden = m["id"] == MOD_ID or bool(man and man.id == MOD_ID) or m.get("secret") is True \
            or bool(man and getattr(man, "secret", False))
        root = os.path.join(lib.mods_dir, m["id"])
        rebuild = bool(man and man.hooks) or _has_code_patches(lib, root, m.get("kinds", []))
        extra = []
        for dep in (man.dependencies if man else {}):
            target = by_manifest.get(dep) or next((x for x in lib.mods if x["id"] == dep), None)
            extra.append(f"Needs {target['name'] if target else dep}.")
        extra += [f"{n} needs this." for n in needed_by.get(m["id"], [])]
        wiiu = isinstance(m.get("wiiu"), dict)
        if wiiu and m["wiiu"].get("wiiu_data"):
            extra.append("Wii U mod: may look wrong.")
        if not hidden:
            pos += 1
        rows.append(Row(m["id"], ascii_text(names[m["id"]], NAME_MAX) or m["id"][:NAME_MAX],
                        ascii_text(_about(m, man), ABOUT_MAX), ascii_text(" ".join(extra), EXTRA_MAX),
                        bool(m.get("enabled")), bool(m.get("enabled")), None if hidden else pos,
                        None if hidden else pos, rebuild, hidden, wiiu))
    return rows


def rows(lib: mm.Library) -> list[Row]:
    """The mods as the game shows them, with what the player changed in the game (wanted, wanted_position: merge).
    A damaged pending.txt, or one made from a list with no copy left, counts as no changes."""
    out = describe_mods(lib)
    pending, base = _pending_and_base(lib)
    if base is None:
        return out
    hidden = {r.id for r in out if r.hidden}
    new, _ = merge(lib.mods, base, pending.wanted, hidden)
    listed = [m for m in new if m["id"] not in hidden]
    position = {m["id"]: k for k, m in enumerate(listed, 1)}
    on = {m["id"]: bool(m.get("enabled")) for m in listed}
    for r in out:
        if not r.hidden:
            r.wanted, r.wanted_position = on[r.id], position[r.id]
    return out


# ---------------------------------------------------------------- menu.txt

KEEP = object()  # sync(note=KEEP): keep the note menu.txt has, while config.json is as it was then


def modman_applies() -> bool:
    """Play in Mod Manager uses the game's changes (modman calls apply before it deploys: the request in
    docs/mod-menu.md). Until then the game tells the player to run python tools/modmenu.py play instead."""
    return callable(getattr(mm, "deploy_with_menu", None))


def menu_text(lib: mm.Library, note: str = "", serial: str | None = None) -> str:
    lines = [f"{MENU_MAGIC}\t{FORMAT}", f"serial\t{serial or secrets.token_hex(8)}", f"config\t{config_stamp(lib)}",
             f"note\t{ascii_text(note, NOTE_MAX)}", f"use\t{'modman' if modman_applies() else 'command'}"]
    for r in describe_mods(lib):
        lines.append("\t".join(("mod", r.id, "on" if r.on else "off", r.flags, r.name, r.about, r.extra)))
    return "\n".join(lines) + "\n"


def _reload(lib: mm.Library) -> None:
    """lib as config.json is on disk now (another program, such as the Mod Manager window, may have saved it)."""
    fresh = mm.Library.load(lib.root)
    if fresh:
        lib.mods, lib.install = fresh.mods, fresh.install


def _listed_now(lib: mm.Library) -> list[tuple[str, bool]]:
    return [(r.id, r.on) for r in describe_mods(lib) if not r.hidden]


def _put_pending(lib: mm.Library, serial: str, wanted: list[tuple[str, bool]]) -> str | None:
    """pending.txt with these (id, on), in this order, made from menu.txt `serial`; deleted when that is how the
    library is now. Returns its path, or None when it was deleted."""
    path = menu_path(lib, PENDING)
    if wanted == _listed_now(lib):
        if os.path.exists(path):
            os.remove(path)
        return None
    write_text(path, f"{PENDING_MAGIC}\t{FORMAT}\nserial\t{serial}\n" + "".join(
        f"mod\t{i}\t{'on' if on else 'off'}\n" for i, on in wanted))
    return path


def _prune_bases(lib: mm.Library) -> None:
    """Drop the copies of old menu.txt files that nothing needs: kept are menu.txt's, pending.txt's and the newest few
    others (a game that started while the list was written)."""
    keep = {m.serial for m in [read_menu(lib)] if m}
    pending, _ = _pending_and_base(lib)
    if pending:
        keep.add(pending.serial)
    try:
        names = [n for n in os.listdir(menu_dir(lib)) if BASE_RE.fullmatch(n) and n[5:21] not in keep]
        names.sort(key=lambda n: os.stat(menu_path(lib, n)).st_mtime_ns, reverse=True)
    except OSError:
        return
    for n in names[KEEP_BASES:]:
        try:
            os.remove(menu_path(lib, n))
        except OSError:
            pass


def sync(lib: mm.Library, note=KEEP) -> str:
    """Write menu.txt from config.json (read again first), with a new serial, and its copy base-<serial>.txt. The
    changes from the game that wait in pending.txt are moved onto the new list, so the game shows them at its next
    start and nothing the player changed is lost. note: the "Last change not used" line ("" for none; KEEP: the one
    menu.txt has, unless config.json changed since: the player changed something in Mod Manager). Refused while Savage
    Wind runs (the running game's changes are based on the list it read). Returns menu.txt's path."""
    _refuse_if_running("run sync")
    _reload(lib)
    old = read_menu(lib)
    if note is KEEP or note is None:
        note = old.note if old and old.config == config_stamp(lib) else ""
    pending, base = _pending_and_base(lib)  # (read before menu.txt is replaced: it may be pending.txt's base)
    serial = secrets.token_hex(8)
    text = menu_text(lib, note, serial)
    write_text(menu_path(lib, BASE.format(serial)), text)
    path = menu_path(lib)
    write_text(path, text)
    if base is not None:
        hidden = {r.id for r in describe_mods(lib) if r.hidden}
        new, _ = merge(lib.mods, base, pending.wanted, hidden)
        _put_pending(lib, serial, [(m["id"], bool(m.get("enabled"))) for m in new if m["id"] not in hidden])
    _prune_bases(lib)
    return path


def library_changed(lib: mm.Library) -> bool:
    """For Mod Manager, after it saved config.json: write the game's mod list again, so the Mods page shows the
    change. Does nothing while Savage Wind runs (the next play writes it). True when it wrote the list."""
    if mm.game_running():
        return False
    sync(lib)
    return True


def _ensure_menu(lib: mm.Library) -> Menu:
    menu = read_menu(lib)
    if menu is None or menu.config != config_stamp(lib):
        sync(lib)
        menu = read_menu(lib)
    return menu


# ---------------------------------------------------------------- changes while the game is closed

def _refuse_if_running(what: str) -> None:
    if mm.game_running():
        raise MenuError(f"Savage Wind is running. Close it, then {what} again.")


def _row(lib: mm.Library, ref: str, rs: list[Row]) -> Row:
    """The listed mod `ref` names: an id, a name, or a number from `show` (its place in the game's list, as the game's
    modmenu_set and modmenu_move count). The Mod menu itself is refused."""
    listed = sorted((r for r in rs if not r.hidden), key=lambda r: r.wanted_position)
    if ref.strip().isdigit():
        k = int(ref)
        if 1 <= k <= len(listed):
            return listed[k - 1]
        raise MenuError(f"There is no mod number {k}: the game's list has {len(listed)}. "
                        "See them with: python tools/modmenu.py show")
    _, m = lib.find(ref)
    hit = next(r for r in rs if r.id == m["id"])
    if hit.hidden:
        raise MenuError(f"{m['name']} is the menu itself: turn it on or off in Mod Manager.")
    return hit


def write_pending(lib: mm.Library, wanted: list[tuple[str, bool]]) -> str | None:
    """Write pending.txt with these (id, on) in this order, or delete it when that is how the library is now.
    Returns its path, or None when it was deleted."""
    if wanted == _listed_now(lib):
        return _put_pending(lib, "", wanted)
    menu = _ensure_menu(lib)
    return _put_pending(lib, menu.serial, wanted)


def _wanted_list(rs: list[Row]) -> list[tuple[str, bool]]:
    listed = sorted((r for r in rs if not r.hidden), key=lambda r: r.wanted_position)
    return [(r.id, r.wanted) for r in listed]


def set_wanted(lib: mm.Library, ref: str, on: bool) -> Row:
    """Turn a mod on or off for the next start (pending.txt). Refused while Savage Wind runs."""
    _refuse_if_running("change it")
    rs = rows(lib)
    me = _row(lib, ref, rs)
    me.wanted = on
    write_pending(lib, _wanted_list(rs))
    return next(r for r in rows(lib) if r.id == me.id)


def move_wanted(lib: mm.Library, ref: str, position: int) -> Row:
    """Move a mod to a 1-based position among the listed mods, for the next start (pending.txt)."""
    _refuse_if_running("move it")
    rs = rows(lib)
    me = _row(lib, ref, rs)
    order = sorted((r for r in rs if not r.hidden), key=lambda r: r.wanted_position)
    if not 1 <= position <= len(order):
        raise MenuError(f"Position must be 1 to {len(order)}.")
    order.remove(me)
    order.insert(position - 1, me)
    write_pending(lib, [(r.id, r.wanted) for r in order])
    return next(r for r in rows(lib) if r.id == me.id)


def undo(lib: mm.Library) -> bool:
    """Throw away the changes from the game. True when there were some."""
    _refuse_if_running("undo")
    path = menu_path(lib, PENDING)
    if not os.path.exists(path):
        return False
    os.remove(path)
    return True


# ---------------------------------------------------------------- apply: use the changes at the next start

@dataclass
class Applied:
    status: str                    # nothing, same, used, resumed, failed, damaged, stale
    changes: list[str] = field(default_factory=list)
    note: str = ""
    launch: dict | None = None

    @property
    def text(self) -> str:
        n = len(self.changes)
        return {"nothing": "No changes from the game.",
                "same": "The changes from the game match the library already.",
                "used": f"Used {n} change{'s' if n != 1 else ''} from the game.",
                "resumed": "Finished using the changes from the game (the last try was cut off).",
                "failed": f"The changes from the game were not used: {self.note}",
                "damaged": NOTE_DAMAGED, "stale": NOTE_STALE}[self.status]


def _set_aside(lib: mm.Library, name: str) -> None:
    src = menu_path(lib, PENDING)
    if os.path.exists(src):
        os.replace(src, menu_path(lib, name))


def _moved(base_order: list[str], wanted_order: list[str]) -> set[str]:
    """The mods the player moved: those outside the longest run of wanted_order that kept base_order's order (the
    first such run found, as native/examples/modmenu/modmenu.c rebase picks it). Both lists hold the same ids."""
    at = {i: k for k, i in enumerate(base_order)}
    seq = [at[i] for i in wanted_order]
    best, prev, top = [1] * len(seq), [-1] * len(seq), -1
    for j in range(len(seq)):
        for k in range(j):
            if seq[k] < seq[j] and best[k] + 1 > best[j]:
                best[j], prev[j] = best[k] + 1, k
        if top < 0 or best[j] > best[top]:
            top = j
    keep = set()
    while top >= 0:
        keep.add(wanted_order[top])
        top = prev[top]
    return {i for i in wanted_order if i not in keep}


def merge(mods: list[dict], base: list[tuple[str, bool]], wanted: list[tuple[str, bool]],
          hidden: set[str]) -> tuple[list[dict], list[str]]:
    """The library's mod list with what the player changed in the game, and only that. base: what the game showed,
    (id, on) of its listed mods in order (read_base); wanted: what pending.txt asks for. A mod whose on/off in wanted
    differs from base gets that on/off. The mods the player moved (those outside the longest run that kept base's
    order) go just after the mod before them in wanted (first, when none is). Everything else stays as `mods` has it,
    so a change made in Mod Manager after the game read its list stays. Ids that aren't in the library or in base, and
    hidden ones, are ignored. Returns (new list of new dicts, what changed)."""
    new = [dict(m) for m in mods]
    by_id = {m["id"]: m for m in new}
    was = dict(base)
    want, seen = [], set()
    for i, on in wanted:
        if i in by_id and i in was and i not in hidden and i not in seen:
            seen.add(i)
            want.append((i, on))
    changes = []
    for i, on in want:
        m = by_id[i]
        if on != was[i] and bool(m.get("enabled")) != on:
            m["enabled"] = on
            changes.append(f"{m['name']}: {'on' if on else 'off'}")
    order = [i for i, _ in want]
    moved = _moved(list(dict.fromkeys(i for i, _ in base if i in seen)), order)
    if moved:
        slots = [k for k, m in enumerate(new) if m["id"] not in hidden]
        before = [new[k]["id"] for k in slots]
        after = [i for i in before if i not in moved]
        for k, i in enumerate(order):
            if i in moved:
                after.insert(after.index(order[k - 1]) + 1 if k else 0, i)
        for slot, i in zip(slots, after):
            new[slot] = by_id[i]
        changes += [f"{by_id[i]['name']}: {ordinal(after.index(i) + 1)} in the order" for i in order
                    if i in moved and after.index(i) != before.index(i)]
    return new, changes


def _config_bytes(lib: mm.Library) -> bytes:
    with open(lib.config_path, "rb") as f:
        return f.read()


def _read_marker(lib: mm.Library) -> bytes | None:
    """config.json as it was before an apply that was cut off (apply.json); None when no apply was cut off."""
    try:
        with open(menu_path(lib, MARKER), encoding="utf-8") as f:
            data = json.load(f)
        return base64.b64decode(data["before"], validate=True)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        return b""  # (damaged: the apply was cut off, but how config.json was is unknown)


def _write_marker(lib: mm.Library, before: bytes) -> None:
    write_text(menu_path(lib, MARKER), json.dumps({"tool": "modmenu", "before": base64.b64encode(before).decode()}))


def _clear_marker(lib: mm.Library) -> None:
    try:
        os.remove(menu_path(lib, MARKER))
    except FileNotFoundError:
        pass


def _restore_config(lib: mm.Library, old: bytes, saved: bytes) -> bool:
    """Put config.json back to `old`, unless another program (the Mod Manager window) saved it after this apply did
    (it isn't `saved` any more): then its change stays. True when it was put back."""
    try:
        now = _config_bytes(lib)
    except OSError:
        now = None
    if now != saved or not old:
        _reload(lib)
        return False
    tmp = f"{lib.config_path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "wb") as f:
            f.write(old)
        os.replace(tmp, lib.config_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    _reload(lib)
    return True


def _deploy_out_of_date(lib: mm.Library) -> bool:
    """The last deploy wasn't made from the library as it is now (modman run would warn and start the old one)."""
    try:
        return mm.inputs_hash(lib) != mm.read_launch(lib).get("hash")
    except (mm.ModmanError, OSError, ValueError):
        return False


def _first_line(e: BaseException) -> str:
    text = str(e).strip()
    lines = [ln.strip(" -") for ln in text.splitlines() if ln.strip(" -")]
    # (modman's refusals put the reason on the second line: "Can't deploy (...):" then "  - Better Map needs ...")
    pick = lines[1] if len(lines) > 1 and lines[0].endswith(":") else (lines[0] if lines else "The deploy failed")
    pick = re.sub(r"\s*(:\s*python\s+tools\S*|\(deploy refuses until then\)).*$", "", pick).rstrip(" .:")
    return pick[:1].upper() + pick[1:] + "."


def _mod_names(lib: mm.Library) -> dict[str, str]:
    """Every id a message may name (library ids and mod.toml ids) -> the mod's name."""
    names = {m["id"]: m["name"] for m in lib.mods}
    for i, man in _manifests(lib).items():
        if man and man.id:
            names.setdefault(man.id, names[i])
    return names


def _reason(e: BaseException, lib: mm.Library) -> str:
    """Why the deploy failed, in plain words for the game's "Last change not used" line: mod names, not ids; no
    command lines; no Python error text."""
    if isinstance(e, OSError):
        code = getattr(e, "winerror", None)
        if code in (32, 33) or e.errno in (errno.EACCES, errno.EBUSY, errno.EPIPE):
            return "A file was in use. Close other programs, then try again."
        if code == 112 or e.errno == errno.ENOSPC:
            return "The disk is full."
        if e.errno == errno.ENOENT:
            return "A file was missing. Check your mods in Mod Manager."
        return "A file couldn't be read or written. Try again."
    names = _mod_names(lib)
    text = re.sub(r"\b(needs|Needs) ([a-z0-9](?:[a-z0-9._-]*[a-z0-9_-])?)",
                  lambda m: f"{m.group(1)} {names.get(m.group(2), m.group(2))}", _first_line(e))
    return ascii_text(text, NOTE_MAX)


def apply(lib: mm.Library, deploy=None) -> Applied:
    """Use what the player changed in the game: merge pending.txt (against the list it was made from: read_base) into
    config.json, deploy, then write menu.txt. Changes made in Mod Manager meanwhile stay. When the deploy fails,
    config.json goes back to how it was (unless another program saved it meanwhile), pending.txt stays (the changes
    still wait: try again, or undo them), and the reason becomes menu.txt's note (the game shows it as "Last change
    not used"). An apply that was cut off (apply.json is there: config.json may hold changes that were never
    deployed) is finished: it deploys again. deploy(lib) defaults to modman's deploy. Refused while Savage Wind runs."""
    _refuse_if_running("press Play")
    deploy = deploy or (lambda library: mm.deploy(library))
    before = _read_marker(lib)
    if not os.path.isfile(menu_path(lib, PENDING)) and before is None:
        return Applied("nothing")
    _reload(lib)
    pending, base, status, note = None, None, "", ""
    if os.path.isfile(menu_path(lib, PENDING)):
        try:
            pending = read_pending(lib)
        except MenuError:
            _set_aside(lib, PENDING_BAD)
            status, note = "damaged", NOTE_DAMAGED
        else:
            base = read_base(lib, pending.serial)
            if base is None:
                _set_aside(lib, PENDING_OLD)
                pending, status, note = None, "stale", NOTE_STALE
    if pending is None and before is None:
        sync(lib, note)
        return Applied(status, note=note)
    hidden = {r.id for r in describe_mods(lib) if r.hidden}
    new, changes = merge(lib.mods, base, pending.wanted, hidden) if pending else (lib.mods, [])
    if not changes and before is None and not _deploy_out_of_date(lib):
        os.remove(menu_path(lib, PENDING))
        sync(lib, "")
        return Applied("same")
    old = before if before is not None else _config_bytes(lib)
    _write_marker(lib, old)  # (before config.json changes: a cut-off apply is finished next time)
    if changes:
        lib.mods = new
        lib.save()
    saved = _config_bytes(lib)
    try:
        launch = deploy(lib)
    except (mm.ModmanError, OSError, ValueError) as e:
        _restore_config(lib, old, saved)
        _clear_marker(lib)
        note = _reason(e, lib)
        sync(lib, note)  # (pending.txt stays, moved onto the list as it is again)
        return Applied("failed", changes, note)
    except BaseException:
        _restore_config(lib, old, saved)
        _clear_marker(lib)
        raise
    if pending:
        _set_aside(lib, PENDING_USED)
    _clear_marker(lib)
    sync(lib, note)
    done = "used" if changes else "resumed" if before is not None else "same"
    return Applied(done, changes, note, launch)


# ---------------------------------------------------------------- setup: build the mod and add it

def mod_target(out: Path | None = None) -> bn.Target:
    sources = tuple(Path(MOD_SRC) / s for s in MOD_SOURCES)
    return bn.Target("modmenu", sources, runtime=False, subdir="modmenu-mods/modmenu", export="swmod_init")


def low_priority_runner(cmd: list[str], env: dict) -> None:
    """build_native's runner, at below-normal priority (the game may be running)."""
    flags = BELOW_NORMAL if os.name == "nt" else 0
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", env=env, creationflags=flags)
    if r.returncode:
        tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-20:])
        raise bn.BuildError(f"{Path(cmd[0]).name} {cmd[1]} failed:\n{tail}")


def build_mod(install: str | None = None, out: str | None = None) -> str:
    """Compile modmenu.dll with the zig that ships with Savage Wind; returns the folder holding it and mod.toml."""
    tc = bn.find_toolchain(Path(install) if install else bn.default_install())
    out_dir = Path(out or mm.MODHOST_DIR)
    t = mod_target()
    built = bn.build(tc, out_dir, [t], runner=low_priority_runner)
    bn.check_exports(zip(built, [t]))
    folder = built[0].parent
    (folder / "mod.toml").write_text(MANIFEST, encoding="utf-8")
    return str(folder)


def add_to_library(lib: mm.Library, folder: str) -> tuple[dict, bool]:
    """Add the built mod to the library, in place of an older copy (same position, same on/off). Returns (entry,
    whether it replaced one)."""
    old = next(((i, m) for i, m in enumerate(lib.mods) if m["id"] == MOD_ID), None)
    if old:  # (check the new copy first: once the old one is removed, a refusal would leave none)
        scan = mm.scan_mod(folder, mm.game_info(lib.install), MOD_NAME)
        if scan.manifest_errors or "native" not in scan.kinds:
            raise MenuError("The new Mod menu build can't be added, so the old one stays:"
                            + "".join(f"\n  - {e}" for e in scan.manifest_errors or scan.problems or ["no DLL"]))
        mm.remove_mod(lib, MOD_ID)
    entry, _, _ = mm.add_mod(lib, folder)
    if old:
        i, m = old
        lib.mods.insert(i, lib.mods.pop())
        lib.mods[i]["enabled"] = bool(m.get("enabled"))
        lib.save()
    return entry, bool(old)


# ---------------------------------------------------------------- command line

def open_lib(args) -> mm.Library:
    root = mm.resolve_library(args.library)
    lib = mm.Library.load(root)
    if not lib:
        raise MenuError(f"No mod library in {root} yet. Run: python tools/modman.py init")
    return lib


def changes_line(rs: list[Row]) -> str:
    listed = [r for r in rs if not r.hidden]
    n = sum(1 for r in listed if r.changed)
    head = f"{len(listed)} mod{'s' if len(listed) != 1 else ''}"
    if not n:
        return f"{head}, no changes wait."
    return f"{head}, {n} change{'s wait' if n != 1 else ' waits'} for your next start."


def show_lines(lib: mm.Library) -> list[str]:
    rs = rows(lib)
    menu = read_menu(lib)
    out = [changes_line(rs)]
    for r in sorted((r for r in rs if not r.hidden), key=lambda r: r.wanted_position):
        state = ("on " if r.on else "off") + (f" -> {'on' if r.wanted else 'off'}" if r.on != r.wanted else "")
        moved = f" (was {r.position})" if r.position != r.wanted_position else ""
        slow = "  next start: 10-20 minutes" if r.rebuild and r.changed else ""
        out.append(f"  {r.wanted_position:>2}  {state:<10} {r.name}{moved}{slow}")
    if menu and menu.note:
        out.append(f"Last change not used: {menu.note}")
    _, base = _pending_and_base(lib)
    if os.path.isfile(menu_path(lib, PENDING)) and base is None:
        out.append("The game's changes can't be read (damaged, or made from an old mod list): play or apply sets "
                   "them aside.")
    if os.path.isfile(menu_path(lib, MARKER)):
        out.append("The last use of the game's changes was cut off: python tools/modmenu.py play finishes it.")
    if not menu:
        out.append("The game has no mod list yet: python tools/modmenu.py sync")
    elif menu.config != config_stamp(lib):
        out.append("The game's mod list is older than the library: python tools/modmenu.py sync")
    if any(r.changed for r in rs):
        out.append("Use them: python tools/modmenu.py play (or apply). Throw them away: python tools/modmenu.py undo")
    return out


def cmd_setup(args) -> int:
    lib = open_lib(args)
    _refuse_if_running("run setup")  # (it changes the library, and the running game's changes need the list it read)
    say("Building the Mod menu mod (low priority)...")
    folder = build_mod(args.install, args.out)
    entry, replaced = add_to_library(lib, folder)
    sync(lib)
    k = next(i for i, m in enumerate(lib.mods, 1) if m["id"] == MOD_ID)
    state = "on" if lib.mods[k - 1]["enabled"] else "OFF (turn it on in Mod Manager)"
    say(f"Mod menu {'updated' if replaced else 'added'} (#{k}, {state}). Now press Deploy in Mod Manager.")
    return 0


def cmd_show(args) -> int:
    for line in show_lines(open_lib(args)):
        say(line)
    return 0


def _say_applied(a: Applied) -> None:
    say(a.text)
    for c in a.changes:
        say(f"  {c}")
    if a.status == "failed":
        say("The library is as it was. Your changes from the game still wait: fix the problem and play again, or "
            "undo them (Y on the game's Mods page, or python tools/modmenu.py undo). The game shows the reason on its "
            "Mods page.")


def cmd_apply(args) -> int:
    a = apply(open_lib(args))
    _say_applied(a)
    if a.launch:
        say(mm.format_deploy(a.launch))
    return 1 if a.status in ("failed", "damaged") else 0


def cmd_undo(args) -> int:
    lib = open_lib(args)
    say("Threw away the changes from the game." if undo(lib) else "No changes from the game.")
    return 0


def cmd_sync(args) -> int:
    lib = open_lib(args)
    path = sync(lib)
    say(f"Wrote the game's mod list ({len([r for r in describe_mods(lib) if not r.hidden])} mods): {path}")
    return 0


def cmd_play(args) -> int:
    lib = open_lib(args)
    a = apply(lib)
    if a.status != "nothing":
        _say_applied(a)
    else:
        _ensure_menu(lib)
    return mm.run(lib, port=args.port)


def cmd_set(args) -> int:
    r = set_wanted(open_lib(args), args.mod, args.state == "on")
    say(f"{r.name}: {'on' if r.wanted else 'off'} at your next start." if r.changed
        else f"{r.name}: no change ({'on' if r.on else 'off'}).")
    return 0


def cmd_move(args) -> int:
    r = move_wanted(open_lib(args), args.mod, args.position)
    say(f"{r.name}: {ordinal(r.wanted_position)} at your next start.")
    return 0


def parser() -> argparse.ArgumentParser:
    lib_help = "the mod library folder (as for modman.py)"
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--library", help=lib_help, default=argparse.SUPPRESS)
    ap = argparse.ArgumentParser(prog="modmenu.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--library", help=lib_help)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup", parents=[common], help="build the Mod menu mod and add it to the library")
    p.add_argument("--install", help="your Savage Wind folder (its zig builds the mod)")
    p.add_argument("--out", help="build folder (default: build/native)")
    sub.add_parser("show", parents=[common], help="the list as the game shows it, and the changes that wait")
    sub.add_parser("apply", parents=[common], help="use the changes from the game now (then deploy)")
    sub.add_parser("undo", parents=[common], help="throw away the changes from the game")
    sub.add_parser("sync", parents=[common], help="write the game's mod list again (with the game closed)")
    p = sub.add_parser("play", parents=[common], help="apply, then start the game")
    p.add_argument("--port", type=mm.port_arg, help="control port for cheats (as modman.py run)")
    mod_help = "id, name, or number from show (its place in the game's list, as in the game)"
    p = sub.add_parser("set", parents=[common], help="turn a mod on or off for the next start")
    p.add_argument("mod", help=mod_help)
    p.add_argument("state", choices=("on", "off"))
    p = sub.add_parser("move", parents=[common], help="move a mod for the next start (later mods win)")
    p.add_argument("mod", help=mod_help)
    p.add_argument("position", type=int)
    return ap


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    args = parser().parse_args(argv)
    if not hasattr(args, "library"):
        args.library = None
    cmds = {"setup": cmd_setup, "show": cmd_show, "apply": cmd_apply, "undo": cmd_undo, "sync": cmd_sync,
            "play": cmd_play, "set": cmd_set, "move": cmd_move}
    try:
        return cmds[args.cmd](args)
    except (mm.ModmanError, bn.BuildError) as e:
        say(f"Error: {e}")
        return 2
    except OSError as e:
        say(f"Error: {e}")
        return 2
    except KeyboardInterrupt:
        say("Stopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
