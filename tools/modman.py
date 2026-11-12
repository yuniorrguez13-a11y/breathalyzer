#!/usr/bin/env python3
"""Manage your Savage Wind mods and start the game with them.

    python tools/modman.py init [<Savage Wind folder>]
    python tools/modman.py add <mod folder, .zip, .bnp or file> [--name NAME] [--no-convert]
                               [--option NAME ...] [--no-options]
    python tools/modman.py list
    python tools/modman.py enable <id or number>    /  disable <id or number>
    python tools/modman.py move <id or number> <position>
    python tools/modman.py remove <id or number>
    python tools/modman.py deploy [--no-rebuild] [--dry-run]
    python tools/modman.py run [--port N] [--no-cheats]
    python tools/modman.py status
    python tools/modman.py doctor
    python tools/modman.py profile [list | new <name> | use <name> | remove <name>]    (mod sets: docs/profiles.md)

Every command takes --library <folder> (or the SWMOD_LIBRARY variable). The mod library defaults to a
"modloader" folder next to the Savage Wind folder, so removing or reinstalling Savage Wind keeps your mods.
It holds config.json (the Savage Wind folder and your ordered mod list), a copy of each mod in mods\\<id>\\, and
deploy\\, which `deploy` fills from the enabled mods (later mods win) and `run` starts the game from:

    deploy\\overlay\\             RomFS files (tools/romfs_mods.py), read through SAVAGEWIND_ROMFS_OVERLAY
    deploy\\root\\exefs\\          the game's own exefs with the ExeFS patches applied (tools/exefs_patch.py);
    deploy\\root\\romfs, aocN      junctions to the game's extracted folders; started as savagewind.exe <root> --play
    deploy\\swgame.dll            game code rebuilt for code patches and hook slots (tools/sw_rebuild.py, 10-20 min)
    deploy\\native\\              modhost.dll (+ .id), the native mods and their helper DLLs in native\\mods\\, and
                                 hooks.txt: the hook addresses of the mods, for sw_rebuild --hooks
    deploy\\cheats\\<build>.txt   the enabled cheat files merged, one "// group:" per mod, run by tools/cheats.py
                                 over the control port
    deploy\\launch.json           the exact command line and environment `run` uses

A mod may have a mod.toml (docs/mod-format.md): its id, name and version, the game versions and mod API it needs,
its DLL and the functions it hooks, its RomFS folders and the mods it needs. Mods without one work as before.
A Wii U mod (content/ and/or aoc/0010/) is converted when it is added (tools/wiiu_convert.py, with the game's RomFS
for its models and textures): the library keeps the Switch copy and wiiu-convert-report.txt in mods\\<id>\\.
--no-convert refuses it instead.
A BCML package (.bnp, or one extracted with its logs/) is installed when it is added (tools/bnp.py): its default
options (or the --bnp-option ones), its packs filled from the game, its actor parameter changes and a size table.
A UKMM package (Switch or cross-platform .zip with meta.yml) is built the same way by tools/ukmm.py.
`deploy` merges the size tables of several such mods; what can't be merged is listed under "Needs attention".

modman only deletes what it wrote itself (listed in deploy\\.modman-deploy.json) and never follows junctions,
so the game's files behind deploy\\root\\romfs are never touched. The Savage Wind folder is only read.
A mod folder added from the library's drive is stored as a snapshot of its code and settings: every file under
1 MiB, every .dll/.exe/.pdb and mod.toml are copies, so rebuilding or editing the mod's own folder later doesn't change
the library. Bigger data files (RomFS assets) stay hard links to the mod's folder (no extra space): the same file, so
don't edit them in place, in the library or in the mod's folder. Some deploy files are hard links to the game's files.
Deleting such a link never changes the file's other names, not even its read-only setting.
"""
from __future__ import annotations

import argparse
import datetime
import filecmp
import hashlib
import importlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import tomllib
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import bnp  # noqa: E402
import ukmm  # noqa: E402
import build_native as bn  # noqa: E402
import cheats as ch  # noqa: E402
import exefs_patch as ep  # noqa: E402
import gen_hooks as gh  # noqa: E402
import mod_inspect as mi  # noqa: E402
import nso as nsolib  # noqa: E402
import romfs_mods as rm  # noqa: E402
import sw_rebuild as swr  # noqa: E402
import wiiu_convert as wc  # noqa: E402

REPO = os.path.dirname(TOOLS)
TOOL_ID = "botw-mod-loader modman"
FORMAT = 1
CONFIG = "config.json"
MODS = "mods"
DEPLOY = "deploy"
MARKER = ".modman-deploy.json"
LAUNCH = "launch.json"
ADDING = ".adding-"
LIBRARY_NAME = "modloader"
ENV_LIBRARY = "SWMOD_LIBRARY"
EXE = "savagewind.exe"
LIB = "swgame.dll"
MODHOST_DIR = os.path.join(REPO, "build", "native")   # where tools/build_native.py build puts modhost.dll
CHEATS_PY = os.path.join(TOOLS, "cheats.py")
DEFAULT_PORT = 7777
BUILD_190 = "cd57b23fa4bbad65"
REBUILD_TIME = "10-20 minutes"
MIN_PYTHON = (3, 12)

KINDS = ("romfs", "dlc-romfs", "exefs-patch", "cheats", "native")
KIND_NAMES = {"romfs": "RomFS files", "dlc-romfs": "DLC RomFS files", "exefs-patch": "ExeFS patches",
              "cheats": "cheats", "native": "native code mod"}
# Variables a mod setup may leave in a window; `run` clears them and sets only the ones the deploy needs.
# (SWMOD_LIBRARY too: modhost finds the library in deploy\native\settings.txt, never in a window's leftover one.)
MOD_ENV = ("SAVAGEWIND_ROMFS_OVERLAY", "SAVAGEWIND_NO_HUD_LETTERS", "SAVAGEWIND_GAME_LIB", "SAVAGEWIND_CONTROL",
           "SWMOD_REAL_GAME_LIB", "SWMOD_DIR", ENV_LIBRARY)
# Library files of the Mods page v2 parts (docs/mod-format.md, docs/profiles.md)
SETTINGS_DIR = "settings"      # <library>\settings\<id>.json: the values the player changed
PICTURES_DIR = "pictures"      # <library>\pictures\<id>\ (and _title\, _mods-page\): the player's choices, the cache
PROFILES_DIR = "profiles"      # <library>\profiles\<file>.json: named mod sets
HISTORY = "history.jsonl"      # deploy\history.jsonl: one JSON object a line (deploy, start, result)
HISTORY_KEEP = 300             # lines kept once it grows past twice that
DEFAULT_PROFILE = "Default"
TITLE_PICK, MODS_PAGE_PICK = "_title", "_mods-page"   # (not mod ids: those start with a letter or digit)
PICTURE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
FONT_EXTS = (".ttf",)          # TrueType outlines (tools/font.py draws them itself)
FONT_FOR, FONT_FOR_LATER = ("menu",), ("all",)                                  # "all": a note, used as "menu"
FONT_LANGUAGES, FONT_LANGUAGES_LATER = ("US", "EU", "JP"), ("KR", "CN", "TW", "TH")   # later ones: a note
MENU_SYMBOLS = set("{}#$%@^`|~")      # the game's menu font has none of these (until font.py fix-symbols is on)
# The Mods page v2 parts live in their own tools, imported only when used (`feature`): a tool that isn't there, or
# doesn't load, turns its part off. Called in this order: docs/mod-format.md, "What deploy does with them".
FEATURES = ("mod_settings", "font", "menu_bg", "mod_pictures")
GENERATED_OWNER = {"font": "generated:fonts", "menu_bg": "generated:title", "mod_pictures": "generated:pictures"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
RESERVED_RE = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])$")
CHEAT_RE = re.compile(r"(^|/)cheats/([0-9a-f]{16})\.txt$", re.I)  # (any case: Windows and the SD card ignore it)
CHEAT_FILE_RE = re.compile(r"[0-9a-f]{16}\.txt")
AOC_RE = re.compile(r"aoc\d+", re.I)
MAX_NATIVE = 64   # modhost loads at most this many DLLs from its mods folder (native/modhost/modhost.c MAX_MODS)
BAD_CHARS = set('<>:"|?*\\') | {chr(i) for i in range(32)}
# Folders deploy\ holds: modman's own, and the overlay romfs_mods owns (it keeps its own marker file).
OWN_TOP = ("root", "native", "cheats", "vanilla-lib", ".work", "pictures", "fonts")
ROMFS_FINDINGS = {"Switch RomFS files", "Wii U game data", "BCML mod (BNP)", "UKMM package"}
# mod_inspect findings scan_mod looks at itself (and says why when it can't use them)
HANDLED_FINDINGS = ROMFS_FINDINGS | {"ExeFS patch", "Atmosphere cheats"}
CODE_MOD_FINDING = "Switch code mod (exlaunch / Skyline)"
NATIVE_FINDING = "Native mod (this loader)"
# mod.toml (docs/mod-format.md)
MANIFEST = "mod.toml"
MANIFEST_MAX = 256 * 1024
MODHOST_API = bn.API_VERSION  # the mod API of the modhost build_native.py builds (swmod.h SWMOD_API_VERSION)
HOOKS_API = 2                 # hooks came with mod API 2
EXAMPLES_DIR = os.path.join(REPO, "native", "examples")  # its mod.toml DLLs come from MODHOST_DIR (build_native)
HOOKS_FILE = "native/hooks.txt"  # in deploy\: the hooks of the enabled mods, passed to sw_rebuild --hooks
MANIFEST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# (at most 9 digits a number: Python refuses to turn more than 4300 digits into an int, and no version needs 10)
VERSION_RE = re.compile(r"^(\d{1,9}(?:\.\d{1,9}){0,3})(?:[-+][0-9A-Za-z.-]+)?$")
GAME_VERSION_RE = re.compile(r"^\d{1,9}\.\d{1,9}\.\d{1,9}$")
REQ_RE = re.compile(r"^(>=|==)\s*(\d{1,9}(?:\.\d{1,9}){0,3})$")
MANIFEST_KEYS = {"mod": {"id", "name", "version", "authors", "description", "game_versions", "loader_api",
                         "icon", "background", "title_picture", "url", "update_url"},
                 "code": {"windows", "linux", "hooks"}, "data": {"romfs", "dlc_romfs"}, "dependencies": None,
                 "font": {"file", "for", "languages", "license"},
                 "settings": None, "pages": None}   # ([[settings]] and [[pages]]: tools/mod_settings.py checks them)
# A mod converted from Wii U when it was added: config.json's "wiiu" entry holds these counts (of its game files)
WIIU_KEYS = ("files", "converted", "partly", "wiiu_data", "left_out", "attention")


class ModmanError(Exception):
    """Something the player has to fix; the message says what."""


def say(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- small helpers

def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _abs(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))


def _inside(child: str, parent: str) -> bool:
    c, p = os.path.normcase(os.path.abspath(child)), os.path.normcase(os.path.abspath(parent))
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:  # (different drives)
        return False


def _is_link(p: str) -> bool:
    return os.path.islink(p) or os.path.isjunction(p)


def write_json(path: str, data: dict) -> None:
    """Written to a temporary file, then renamed over `path`, so a reader never sees half a file."""
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ModmanError(f"can't read {path}: {e}") from None
    if not isinstance(data, dict):
        raise ModmanError(f"{path} is damaged (not a JSON object)")
    return data


def _read_only(p: str) -> bool:
    try:
        return not os.lstat(p).st_mode & stat.S_IWRITE
    except OSError:
        return False


def _delete_name_only(p: str) -> bool:
    """Delete one name of a read-only file that has other names (hard links), without clearing the read-only
    setting: it belongs to the file, so clearing it here would clear it on the game's or the player's own copy too.
    Windows 10 1809 or newer, NTFS (FILE_DISPOSITION_IGNORE_READONLY_ATTRIBUTE). False when that isn't possible."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.SetFileInformationByHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    DELETE, SHARE_ALL, OPEN_EXISTING, OPEN_REPARSE_POINT = 0x10000, 7, 3, 0x200000
    FILE_DISPOSITION_INFO_EX = 21
    DELETE_FLAG, POSIX, IGNORE_READONLY = 0x1, 0x2, 0x10
    h = k32.CreateFileW(os.path.abspath(p), DELETE, SHARE_ALL, None, OPEN_EXISTING, OPEN_REPARSE_POINT, None)
    if h is None or h == wintypes.HANDLE(-1).value:
        return False
    try:
        for flags in (DELETE_FLAG | POSIX | IGNORE_READONLY, DELETE_FLAG | IGNORE_READONLY):
            info = wintypes.ULONG(flags)
            if k32.SetFileInformationByHandle(h, FILE_DISPOSITION_INFO_EX, ctypes.byref(info), ctypes.sizeof(info)):
                return True
        return False
    finally:
        k32.CloseHandle(h)


def _unlink(p: str) -> None:
    """Delete a file modman put somewhere. A read-only file with other names (a hard link to the game's files or to
    a mod the player added) loses only this name; its read-only setting stays as it is."""
    try:
        os.unlink(p)
        return
    except PermissionError:
        st = os.lstat(p)
        if st.st_mode & stat.S_IWRITE:
            raise  # (not read-only: something has it open)
        if st.st_nlink > 1:
            if _delete_name_only(p):
                return
            raise ModmanError(f"Can't delete {p}: it is read-only and is also the file in another place (a hard "
                              "link), so clearing read-only would change that one too. Delete it yourself.") from None
    os.chmod(p, stat.S_IWRITE)  # (modman's own copy: nothing else shares the setting)
    os.unlink(p)


def _remove_link(p: str) -> None:
    """Remove a junction or symbolic link itself; what it points to stays."""
    try:
        os.rmdir(p)
    except OSError:
        os.unlink(p)


def remove_tree(path: str) -> None:
    """Delete a folder and what is in it without following links: a junction or symbolic link inside is removed as
    a link, so the folder it points to (the game's files, say) is never touched."""
    if _is_link(path):
        _remove_link(path)
        return
    if not os.path.isdir(path):
        if os.path.lexists(path):
            _unlink(path)
        return
    with os.scandir(path) as it:
        entries = list(it)
    for e in entries:
        if e.is_junction() or e.is_symlink():
            _remove_link(e.path)
        elif e.is_dir(follow_symlinks=False):
            remove_tree(e.path)
        else:
            _unlink(e.path)
    os.rmdir(path)


def walk_files(root: str, read_only: set[str] | None = None) -> list[tuple[str, str, int]]:
    """(absolute path, "/"-separated relative path, size) of every file under root. Links and junctions are not
    followed (nor listed). `read_only`, when given, gets the relative paths of the read-only files."""
    out = []
    stack = [""]
    while stack:
        rel = stack.pop()
        with os.scandir(os.path.join(root, *rel.split("/")) if rel else root) as it:
            for e in it:
                r = f"{rel}/{e.name}" if rel else e.name
                if e.is_junction() or e.is_symlink():
                    continue
                if e.is_dir():
                    stack.append(r)
                elif e.is_file():
                    out.append((e.path, r, e.stat().st_size))
                    # (lstat, not the folder listing: Windows keeps a copy of the read-only setting with each name,
                    # and the copy kept with a hard link's other names isn't updated when it changes)
                    if read_only is not None and not os.lstat(e.path).st_mode & stat.S_IWRITE:
                        read_only.add(r)
    return sorted(out, key=lambda t: t[1])


def link_or_copy(src: str, dst: str) -> str:
    """A hard link when src and dst are on one drive that allows them, else a copy. Returns "link" or "copy".
    A read-only src is copied: a link would share its read-only setting with a file modman doesn't own."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.lexists(dst):
        _unlink(dst)
    if not _read_only(src):
        try:
            os.link(src, dst)
            return "link"
        except OSError:
            pass
    shutil.copyfile(src, dst)
    return "copy"


def make_junction(link: str, target: str) -> None:
    """A directory junction (Windows; no admin rights needed) or a symbolic link elsewhere."""
    os.makedirs(os.path.dirname(link), exist_ok=True)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(os.path.abspath(target), os.path.abspath(link))
    else:
        os.symlink(os.path.abspath(target), link, target_is_directory=True)


def _bad_part(part: str) -> bool:
    return (not part or part in (".", "..") or part[-1] in " ." or any(c in BAD_CHARS for c in part)
            or bool(RESERVED_RE.match(part.split(".", 1)[0].rstrip().lower())))


def manifest_id_ok(s: str) -> bool:
    """A mod.toml id that can also be the mod's folder name in the library (no device names, no trailing dot, not
    a build or title id, which would change what romfs_mods reads the folder's files as)."""
    return (isinstance(s, str) and bool(MANIFEST_ID_RE.match(s)) and ".." not in s and not _bad_part(s)
            and not re.fullmatch(r"[0-9a-f]{16}", s))


def good_id(s) -> bool:
    """A library id: one modman made from a name (ID_RE) or a mod.toml id."""
    return isinstance(s, str) and (bool(ID_RE.match(s)) or manifest_id_ok(s))


def _breaks_lines(c: str) -> bool:
    """A control character, or a character str.splitlines() ends a line at (U+2028, U+2029, U+0085, ...)."""
    return unicodedata.category(c) in ("Cc", "Zl", "Zp")


def one_line(s: str) -> str:
    """s with every control or line-break character made a space: safe in a one-line comment of a file another
    tool reads line by line (hooks.txt, the merged cheat file)."""
    return "".join(" " if _breaks_lines(c) else c for c in s)


def clip(s: str, n: int = 60) -> str:
    """s, shortened for a message (a mod.toml can hold a value thousands of characters long)."""
    return s if len(s) <= n else s[:n - 3] + "..."


def _gb(n: int) -> str:
    return f"{n / 2**30:.1f} GB"


def _size(n: int) -> str:
    return f"{n / 2**20:.0f} MB" if n >= 2**20 else f"{n // 1024} KB" if n >= 1024 else f"{n} bytes"


def _some(items: list[str], n: int = 5) -> str:
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def _same_bytes(a: str, b: str) -> bool:
    try:
        return filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def _read_id(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


# ---------------------------------------------------------------- the library and its config

def default_install() -> str:
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, "Documents", "SavageWind", "Savage Wind")


def library_for(install: str) -> str:
    """The default library: a "modloader" folder next to (not inside) the Savage Wind folder."""
    return os.path.join(os.path.dirname(os.path.abspath(install)), LIBRARY_NAME)


def resolve_library(arg: str | None, install_hint: str | None = None) -> str:
    if arg:
        return _abs(arg)
    if os.environ.get(ENV_LIBRARY):
        return _abs(os.environ[ENV_LIBRARY])
    return library_for(install_hint or default_install())


def looks_like_install(path: str | None) -> bool:
    return bool(path) and os.path.isfile(os.path.join(path, EXE))


class Library:
    def __init__(self, root: str, install: str | None = None, mods: list[dict] | None = None,
                 profile: str = DEFAULT_PROFILE, extra: dict | None = None):
        self.root = os.path.abspath(root)
        self.install = install
        self.mods: list[dict] = mods or []
        self.profile = profile          # the active profile (docs/profiles.md); its mod set is self.mods
        self.extra: dict = extra or {}  # config.json keys this modman doesn't know: kept as they are

    @property
    def config_path(self) -> str:
        return os.path.join(self.root, CONFIG)

    @property
    def mods_dir(self) -> str:
        return os.path.join(self.root, MODS)

    @property
    def deploy_dir(self) -> str:
        return os.path.join(self.root, DEPLOY)

    def mod_path(self, mod_id: str) -> str:
        if not good_id(mod_id):
            raise ModmanError(f"'{mod_id}' isn't a mod id modman makes")
        return os.path.join(self.mods_dir, mod_id)

    def enabled(self) -> list[dict]:
        return [m for m in self.mods if m.get("enabled")]

    @classmethod
    def load(cls, root: str) -> Library | None:
        data = read_json(os.path.join(root, CONFIG))
        if data is None:
            return None
        if data.get("tool") != TOOL_ID:
            raise ModmanError(f"{os.path.join(root, CONFIG)} wasn't written by modman")
        mods = data.get("mods", [])
        install = data.get("install")
        if not isinstance(mods, list) or (install is not None and not isinstance(install, str)):
            raise ModmanError(f"{os.path.join(root, CONFIG)} is damaged")
        seen = set()
        for m in mods:  # (ids become folder names: a hand-edited one must not point outside mods\)
            if not isinstance(m, dict) or not good_id(m.get("id")) \
                    or m["id"] in seen or not isinstance(m.get("name", ""), str):
                raise ModmanError(f"{os.path.join(root, CONFIG)} has a bad mod entry: {str(m)[:80]}")
            seen.add(m["id"])
            m["name"] = one_line(m.get("name", m["id"]))  # (names go into one-line comments of deploy files)
            if "version" in m and not (isinstance(m["version"], str) and VERSION_RE.fullmatch(m["version"])):
                del m["version"]  # (a hand-edited one modman couldn't compare)
            m["enabled"] = bool(m.get("enabled"))
            kinds = m.get("kinds")
            m["kinds"] = [k for k in kinds if k in KINDS] if isinstance(kinds, list) else []
            if "wiiu" in m:  # (converted from Wii U: keep only counts modman can print)
                w = m["wiiu"]
                if isinstance(w, dict):
                    m["wiiu"] = {k: w[k] for k in WIIU_KEYS if type(w.get(k)) is int and w[k] >= 0}
                else:
                    del m["wiiu"]
        profile = data.get("profile")
        profile = profile.strip() if isinstance(profile, str) and profile_name_ok(profile.strip()) else DEFAULT_PROFILE
        extra = {k: v for k, v in data.items() if k not in ("tool", "format", "install", "mods", "profile")}
        return cls(root, install, mods, profile, extra)

    def save(self) -> None:
        os.makedirs(self.root, exist_ok=True)
        data = {**self.extra, "tool": TOOL_ID, "format": FORMAT, "install": self.install, "mods": self.mods}
        if self.profile != DEFAULT_PROFILE:  # (a library that never used profiles keeps its config.json as it was)
            data["profile"] = self.profile
        write_json(self.config_path, data)

    def find(self, ref: str) -> tuple[int, dict]:
        ref = ref.strip()
        if ref.isdigit():
            k = int(ref)
            if 1 <= k <= len(self.mods):
                return k - 1, self.mods[k - 1]
            raise ModmanError(f"There is no mod number {k}: the list has {len(self.mods)}.")
        for i, m in enumerate(self.mods):
            if m["id"] == ref.lower():
                return i, m
        hits = [(i, m) for i, m in enumerate(self.mods) if m["name"].lower() == ref.lower()]
        if len(hits) == 1:
            return hits[0]
        raise ModmanError(f"No mod '{ref}'. Run `list` to see the ids and numbers.")

    def require_install(self) -> str:
        if not self.install:
            raise ModmanError("The library doesn't know your Savage Wind folder. Run: "
                              "python tools/modman.py init <Savage Wind folder>")
        if not looks_like_install(self.install):
            raise ModmanError(f"Savage Wind isn't in {self.install} any more (no {EXE}). Reinstall it there, or "
                              "run: python tools/modman.py init <Savage Wind folder>")
        return self.install


def open_library(args, *, create: bool = False) -> Library:
    root = resolve_library(args.library)
    lib = Library.load(root)
    if lib:
        return lib
    if not create:
        raise ModmanError(f"No mod library in {root} yet. Run: python tools/modman.py init")
    install = default_install()
    if not looks_like_install(install):
        raise ModmanError(f"Savage Wind isn't in {install}. Run: python tools/modman.py init <Savage Wind folder>")
    lib = Library(root, install)
    lib.save()
    say(f"Made a mod library in {root} for Savage Wind in {install}.")
    return lib


# ---------------------------------------------------------------- Mods page v2 parts (each in its own tool)

_FEATURE_CACHE: dict[str, object] = {}
FEATURE_ERRORS: dict[str, str] = {}   # module -> why it didn't load (a module that isn't there isn't listed)


def feature(module: str, func: str | None = None):
    """A Mods page v2 tool (tools/<module>.py), or with `func` that function of it; None when the tool isn't there,
    doesn't load or lacks the function: that part is then off. Only tools/ is searched."""
    if module not in _FEATURE_CACHE:
        mod = None
        if os.path.isfile(os.path.join(TOOLS, module + ".py")):
            try:
                mod = importlib.import_module(module)
            except Exception as e:  # (a tool another job is still writing must not stop modman)
                FEATURE_ERRORS[module] = f"{type(e).__name__}: {e}"
        _FEATURE_CACHE[module] = mod
    mod = _FEATURE_CACHE[module]
    if mod is None or func is None:
        return mod
    f = getattr(mod, func, None)
    return f if callable(f) else None


def _feature_call(module: str, func: str, *args, problems: list[str] | None = None, what: str = ""):
    """Call tools/<module>.py's func; None when it isn't there. An error in it turns that part off for this call:
    it goes into `problems` (when given) as one line, and None is returned."""
    f = feature(module, func)
    if f is None:
        return None
    try:
        return f(*args)
    except Exception as e:  # (one part failing must not stop add or deploy)
        if problems is not None:
            problems.append(f"{what or module}: off this time ({type(e).__name__}: {e})")
        return None


def settings_file(lib: "Library", mod_id: str) -> str:
    return os.path.join(lib.root, SETTINGS_DIR, f"{mod_id}.json")


def read_setting_values(lib: "Library", mod_id: str) -> dict:
    """The setting values the player changed for a mod (<library>\\settings\\<id>.json; {} when there are none).
    Only values unlike the default are kept there (docs/mod-settings.md)."""
    try:
        data = read_json(settings_file(lib, mod_id))
    except ModmanError:
        return {}
    vals = (data or {}).get("values")
    return dict(vals) if isinstance(vals, dict) and (data or {}).get("mod") == mod_id else {}


def write_setting_values(lib: "Library", mod_id: str, values: dict) -> None:
    """Write a mod's changed setting values: a temp file, then a rename (modhost reads it any time)."""
    if not good_id(mod_id) and not manifest_id_ok(mod_id):
        raise ModmanError(f"'{mod_id}' isn't a mod id")
    os.makedirs(os.path.join(lib.root, SETTINGS_DIR), exist_ok=True)
    write_json(settings_file(lib, mod_id), {"format": 1, "mod": mod_id, "values": dict(values)})


def picture_choice(lib: "Library", key: str) -> str | None:
    """The picture the player chose (<library>\\pictures\\<key>\\chosen.<png/jpg/jpeg/webp>): key is a mod id,
    "_title" (the title screen) or "_mods-page" (behind the Mods page). None when there is none."""
    folder = os.path.join(lib.root, PICTURES_DIR, key)
    for ext in PICTURE_EXTS:
        p = os.path.join(folder, "chosen" + ext)
        if os.path.isfile(p):
            return p
    return None


# ---------------------------------------------------------------- what a mod holds

@dataclass
class GameInfo:
    install: str | None
    ids: dict[str, str] | None   # module -> full build id, from extracted\exefs (None: not set up)
    layouts: dict[str, list[tuple[str, int, int]]] = field(default_factory=dict)  # module -> its segments

    @property
    def main_bid16(self) -> str:
        return (self.ids or {}).get("main", BUILD_190)[:16]


def game_info(install: str | None) -> GameInfo:
    if not install:
        return GameInfo(None, None)
    exefs = os.path.join(install, "extracted", "exefs")
    try:
        ids = ep.read_module_ids(exefs)
    except (ep.PatchError, OSError):
        ids = None
    layouts = {}
    for mod in ids or {}:
        try:
            layouts[mod] = nsolib.read_layout(os.path.join(exefs, mod))
        except (nsolib.NsoError, OSError):
            pass
    return GameInfo(install, ids, layouts)


def outside_module(p: ep.Patch, mod: str, layout: list[tuple[str, int, int]]) -> str | None:
    """Why exefs_patch's apply would refuse the patch for this module layout (it writes past the end, or between
    two segments), or None when it fits. Reads only the header layout, so it costs nothing."""
    end = max(off + size for _, off, size in layout)
    if p.end > end:
        return f"writes past the end of {mod} (0x{p.offset:x})"
    segs = [(off, off + size) for _, off, size in layout if size]
    pos = p.offset
    while pos < p.end:
        hit = next((b for a, b in segs if a <= pos < b), None)
        if hit is None:
            return f"writes at 0x{pos:x}, outside {mod}'s segments"
        pos = hit
    return None


@dataclass
class ModScan:
    kinds: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    romfs: bool = False
    patches: list[str] = field(default_factory=list)   # usable .ips/.pchtxt files
    cheats: list[str] = field(default_factory=list)    # usable cheat files
    dlls: list[str] = field(default_factory=list)      # DLLs exporting swmod_init
    helpers: list[str] = field(default_factory=list)   # other 64-bit DLLs of a native mod (deployed next to its DLLs)
    findings: list = field(default_factory=list)       # mod_inspect's findings
    manifest: Manifest | None = None                   # its mod.toml, when it has a usable one
    manifest_errors: list[str] = field(default_factory=list)  # why its mod.toml can't be used (add/deploy refuse)


def _version_name(bid: str) -> str:
    v = mi.KNOWN_BUILDS.get(bid[:16].lower())
    return f"BotW {v}" if v else "another game version"


# ---------------------------------------------------------------- mod.toml (docs/mod-format.md)

@dataclass
class Manifest:
    id: str
    name: str
    version: str
    base_rel: str = ""                       # the folder mod.toml is in, inside the mod ("" or "Wrap/")
    authors: list[str] = field(default_factory=list)
    description: str = ""
    game_versions: list[str] = field(default_factory=list)   # empty: any version
    loader_api: int = 1
    dll: str | None = None                   # [code] windows: "/"-separated, relative to mod.toml's folder
    hooks: list[int] = field(default_factory=list)            # sorted, each once
    romfs: str | None = None                 # [data] folders, like dll ("." is mod.toml's folder itself)
    dlc_romfs: str | None = None
    dependencies: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # Mods page v2 (docs/mod-format.md). Paths like dll: "/"-separated, relative to mod.toml's folder.
    icon: str | None = None                  # [mod] icon: the mod's picture
    background: str | None = None            # [mod] background: behind the Mods page while the mod is selected
    title_picture: str | None = None         # [mod] title_picture: the title-screen picture (tools/menu_bg.py)
    url: str | None = None                   # [mod] url: https only; shown in Mod Manager, never in game
    update_url: str | None = None            # [mod] update_url: kept for the update check (later)
    font: dict | None = None                 # [font]: {"file", "for", "languages", "license"} (checked here)
    settings: list[dict] = field(default_factory=list)   # [[settings]] as written (tools/mod_settings.py checks)
    pages: list[dict] = field(default_factory=list)      # [[pages]] as written
    raw: dict = field(default_factory=dict, repr=False)  # the whole parsed mod.toml (for the parts' own checks)

    def rel(self, path: str) -> str:
        """A path mod.toml names, relative to the mod's root folder."""
        return self.base_rel + path

    def folder(self, path: str) -> str:
        """A [data] folder, relative to the mod's root folder and ending in "/" ("" for the root itself)."""
        return self.base_rel if path == "." else self.base_rel + path + "/"


def version_key(v) -> tuple[int, ...]:
    """The numbers of a version, without trailing zeros (1.0 == 1.0.0); a suffix after - or + is ignored. Never
    fails: a number of more than 9 digits (only in a hand-edited config.json) ends the version there."""
    m = re.match(r"\d{1,9}(?:\.\d{1,9})*(?!\d)", v.strip()) if isinstance(v, str) else None
    nums = [int(x) for x in m.group(0).split(".")] if m else []
    while nums and nums[-1] == 0:
        nums.pop()
    return tuple(nums)


def parse_requirement(req) -> tuple[str, tuple[int, ...]] | None:
    """A [dependencies] value: "*" (any version), ">=1.0" or "==1.0". None when it isn't one of these."""
    if not isinstance(req, str):
        return None
    s = req.strip()
    if s in ("", "*"):
        return "*", ()
    m = REQ_RE.fullmatch(s)
    return (m.group(1), version_key(m.group(2))) if m else None


def satisfies(version: str | None, req: str) -> bool:
    op, want = parse_requirement(req) or ("?", ())
    if op == "*":
        return True
    if version is None or op == "?":
        return False
    have = version_key(version)
    return have >= want if op == ">=" else have == want


def parse_manifest(text: str, base_rel: str = "") -> tuple[Manifest | None, list[str]]:
    """A mod.toml's contents, checked. Returns (manifest, []) or (None, every problem found). Unknown tables and
    keys are no error (the manifest's notes say they are ignored)."""
    errs: list[str] = []
    notes: list[str] = []
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return None, [f"mod.toml isn't valid TOML: {e}"]
    except ValueError:  # (tomllib turns a number of more than 4300 digits into an int, which Python refuses)
        return None, ["mod.toml isn't valid TOML: a number in it is far too long"]
    except RecursionError:
        return None, ["mod.toml isn't valid TOML: its lists or tables are nested far too deeply"]
    for k in data:
        if k not in MANIFEST_KEYS:
            notes.append(f"mod.toml: [{k}] isn't something modman knows; ignored")
    if not isinstance(data.get("mod"), dict):
        return None, ["mod.toml has no [mod] table (with id, name and version)"]

    def table(name: str) -> dict:
        t = data.get(name, {})
        if not isinstance(t, dict):
            errs.append(f"mod.toml: {name} must be a table, written [{name}]")
            return {}
        if MANIFEST_KEYS[name] is not None:
            notes.extend(f"mod.toml: [{name}] {k} isn't something modman knows; ignored"
                         for k in t if k not in MANIFEST_KEYS[name])
        return t

    def text_of(t: dict, tname: str, key: str, required: bool = False, example: str = "...") -> str | None:
        v = t.get(key)
        if v is None:
            if required:
                errs.append(f'mod.toml: [{tname}] {key} is missing (like {key} = "{example}")')
            return None
        # (no control characters or line breaks of any kind: names go into one-line comments of files other tools
        # read line by line, and str.splitlines also breaks at U+0085, U+2028 and U+2029)
        if not isinstance(v, str) or not v.strip() or any(_breaks_lines(c) for c in v):
            errs.append(f'mod.toml: [{tname}] {key} must be one line of text in quotes, like {key} = "{example}"')
            return None
        return v.strip()

    def path_of(t: dict, tname: str, key: str, example: str, root_ok: bool = False) -> str | None:
        """A path inside the mod, "/"-separated; with root_ok, "." (mod.toml's own folder) is one too."""
        v = text_of(t, tname, key, example=example)
        if v is None:
            return None
        p = v.replace("\\", "/").strip("/")
        parts = [x for x in p.split("/") if x != "."]
        if root_ok and not parts and p.startswith(".") and not v.startswith(("/", "\\")):
            return "."
        if (v.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", v) or not parts
                or any(_bad_part(x) for x in parts)):
            errs.append(f'mod.toml: [{tname}] {key} = "{clip(v)}" must be a path inside the mod folder, like '
                        f'"{example}"'
                        + (' (or ".", the folder mod.toml is in)' if root_ok else ""))
            return None
        return "/".join(parts)

    mod, code, dat, deps = table("mod"), table("code"), table("data"), table("dependencies")
    mod_id = text_of(mod, "mod", "id", True, "yourname.my-mod")
    if mod_id is not None and not manifest_id_ok(mod_id):
        errs.append(f'mod.toml: [mod] id = "{clip(mod_id)}" can\'t be used: write it with lowercase letters, '
                    'digits, ".", "-" and "_" (at most 64, starting with a letter or digit, not ending in "."), like '
                    '"yourname.my-mod"')
        mod_id = None
    name = text_of(mod, "mod", "name", True, "My Mod")
    if name is not None and len(name) > 100:
        errs.append("mod.toml: [mod] name is longer than 100 characters")
    version = text_of(mod, "mod", "version", True, "1.0.0")
    if version is not None and not VERSION_RE.fullmatch(version):
        errs.append(f'mod.toml: [mod] version = "{clip(version)}" isn\'t a version like "1.0" or "0.2.1"')
    authors = mod.get("authors", [])
    if not isinstance(authors, list) or not all(isinstance(a, str) and not any(_breaks_lines(c) for c in a)
                                                for a in authors):
        errs.append('mod.toml: [mod] authors must be a list of names, like authors = ["yourname"]')
        authors = []
    description = text_of(mod, "mod", "description", example="One line.") or ""
    games = mod.get("game_versions", [])
    if (not isinstance(games, list) or (games == [] and "game_versions" in mod)
            or not all(isinstance(g, str) and GAME_VERSION_RE.fullmatch(g.strip()) for g in games)):
        errs.append('mod.toml: [mod] game_versions must be a list of game versions, like game_versions = ["1.9.0"]')
        games = []
    games = [g.strip() for g in games]
    api = mod.get("loader_api", 1)
    if isinstance(api, bool) or not isinstance(api, int) or api < 1:
        errs.append(f"mod.toml: [mod] loader_api must be a whole number, the mod API the mod needs (like "
                    f"loader_api = {MODHOST_API}), not {api!r}")
        api = 1

    dll = path_of(code, "code", "windows", "bin/my_mod.dll")
    if dll is not None and not dll.lower().endswith(".dll"):
        errs.append(f'mod.toml: [code] windows = "{dll}" must name a .dll file')
        dll = None
    if "linux" in code and not isinstance(code["linux"], str):
        errs.append('mod.toml: [code] linux must be a path in quotes, like linux = "bin/libmy_mod.so"')
    hooks: set[int] = set()
    raw_hooks = code.get("hooks", [])
    if not isinstance(raw_hooks, list):
        errs.append('mod.toml: [code] hooks must be a list of addresses, like hooks = ["0x7000cc7410"]')
        raw_hooks = []
    for i, a in enumerate(raw_hooks, 1):
        try:
            addr = gh.parse_address(a, f"mod.toml: [code] hooks, entry {i}: ")
        except gh.HookError as e:
            errs.append(str(e))
            continue
        if addr < gh.MAIN_BASE:
            errs.append(f"mod.toml: [code] hooks, entry {i}: {addr:#x} isn't in main (main starts at "
                        f"{gh.MAIN_BASE:#x})")
        elif addr % 4:
            errs.append(f"mod.toml: [code] hooks, entry {i}: {addr:#x} isn't a multiple of 4, so it can't be the "
                        "start of a function")
        else:
            hooks.add(addr)
    if raw_hooks and "windows" not in code:
        errs.append("mod.toml: [code] hooks need the mod's DLL: add [code] windows")
    if raw_hooks and api < HOOKS_API:
        errs.append(f"mod.toml: [code] hooks need mod API {HOOKS_API}: set [mod] loader_api = {HOOKS_API}")
    if raw_hooks and not games and "game_versions" not in mod:
        errs.append('mod.toml: [code] hooks are addresses in one game version: say which in [mod] game_versions '
                    '(like game_versions = ["1.9.0"])')

    romfs = path_of(dat, "data", "romfs", "romfs", root_ok=True)
    dlc_romfs = path_of(dat, "data", "dlc_romfs", "01007EF00011F001/romfs", root_ok=True)

    dependencies: dict[str, str] = {}
    for dep, req in deps.items():
        if not manifest_id_ok(dep):
            errs.append(f'mod.toml: [dependencies] "{clip(dep)}" isn\'t a mod id (ids look like "yourname.other-mod")')
        elif dep == mod_id:
            errs.append(f'mod.toml: [dependencies] names the mod itself ("{dep}")')
        elif parse_requirement(req) is None:
            errs.append(f'mod.toml: [dependencies] "{dep}" = {clip(repr(req))}: write ">=1.0", "==1.0" or "*" '
                        '(any version)')
        else:
            dependencies[dep] = req.strip() or "*"

    # Mods page v2: pictures, link, font, settings pages (docs/mod-format.md)
    pics: dict[str, str | None] = {}
    for key, example in (("icon", "icon.png"), ("background", "bg.png"), ("title_picture", "title.png")):
        p = path_of(mod, "mod", key, example)
        if p is not None and not p.lower().endswith(PICTURE_EXTS):
            errs.append(f'mod.toml: [mod] {key} = "{clip(p)}" must be a .png picture (.jpg or .webp work with '
                        "Pillow installed)")
            p = None
        pics[key] = p
    links: dict[str, str | None] = {}
    for key in ("url", "update_url"):
        u = text_of(mod, "mod", key, example="https://example.org/my-mod")
        if u is not None and (not u.lower().startswith("https://") or len(u) > 300 or len(u) <= len("https://")
                              or any(c.isspace() for c in u)):
            errs.append(f'mod.toml: [mod] {key} must be a web address that starts with https://, like {key} = '
                        '"https://example.org/my-mod" (at most 300 characters)')
            u = None
        links[key] = u
    font = None
    if "font" in data:
        ft = table("font")
        f_file = path_of(ft, "font", "file", "fonts/MyFont.ttf")
        if "file" not in ft:
            errs.append('mod.toml: [font] needs file, the font in the mod, like file = "fonts/MyFont.ttf"')
        elif f_file is not None and not f_file.lower().endswith(FONT_EXTS):
            errs.append(f'mod.toml: [font] file = "{clip(f_file)}" must be a .ttf font (TrueType outlines)')
            f_file = None
        f_for = ft.get("for", "menu")
        if f_for in FONT_FOR_LATER:
            notes.append(f'mod.toml: [font] for = "{f_for}" comes later: used as "menu" for now')
            f_for = "menu"
        elif f_for not in FONT_FOR:
            errs.append(f"mod.toml: [font] for = {clip(repr(f_for))}: write \"menu\" (the menus and the game's "
                        "texts)")
        langs = ft.get("languages", list(FONT_LANGUAGES))
        if not isinstance(langs, list) or not langs or not all(isinstance(x, str) for x in langs) \
                or any(x.upper() not in FONT_LANGUAGES + FONT_LANGUAGES_LATER for x in langs):
            errs.append(f"mod.toml: [font] languages must be a list of {', '.join(FONT_LANGUAGES)}, like "
                        'languages = ["US", "EU"]')
            langs = list(FONT_LANGUAGES)
        later = [x.upper() for x in langs if x.upper() in FONT_LANGUAGES_LATER]
        if later:
            notes.append(f"mod.toml: [font] languages {', '.join(later)} come later; ignored for now")
        langs = [x for x in FONT_LANGUAGES if x in {y.upper() for y in langs}]
        if not langs and not later:
            langs = list(FONT_LANGUAGES)
        elif not langs:
            errs.append(f"mod.toml: [font] languages has none of {', '.join(FONT_LANGUAGES)} yet")
        lic = text_of(ft, "font", "license", example="OFL-1.1")
        font = {"file": f_file, "for": f_for, "languages": list(langs), "license": lic}
    lists: dict[str, list[dict]] = {}
    for key in ("settings", "pages"):
        v = data.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
            errs.append(f"mod.toml: {key} must be written [[{key}]], one such table each")
            v = []
        lists[key] = v
    if errs:
        return None, errs
    # (the game's menu font lacks these symbols until font.py fix-symbols is on: warn, don't refuse)
    shown = [("[mod] name", name), ("[mod] description", description)]
    for key in ("settings", "pages"):
        for k, t in enumerate(lists[key], 1):
            shown += [(f"[[{key}]] {k} {f}", t.get(f)) for f in ("label", "description") if isinstance(t.get(f), str)]
    for where, text in shown:
        bad = sorted(MENU_SYMBOLS & set(text or ""))
        if bad:
            notes.append(f"mod.toml: {where} has {' '.join(bad)}, which the game's menus can't show yet (Mod "
                         "Manager can)")
    return Manifest(id=mod_id, name=name, version=version, base_rel=base_rel, authors=list(authors),
                    description=description, game_versions=games, loader_api=api, dll=dll, hooks=sorted(hooks),
                    romfs=romfs, dlc_romfs=dlc_romfs, dependencies=dependencies, notes=notes,
                    icon=pics["icon"], background=pics["background"], title_picture=pics["title_picture"],
                    url=links["url"], update_url=links["update_url"], font=font, settings=lists["settings"],
                    pages=lists["pages"], raw=data), []


def _manifest_files(files: list[str]) -> list[str]:
    return sorted(f for f in files if f.rsplit("/", 1)[-1].lower() == MANIFEST)


def find_manifest(rels: list[str]) -> str | None:
    """The mod.toml of a mod with these files ("/"-separated paths): at its root, or inside the one folder that
    holds everything (as mod_inspect reads a mod). Loose files next to that folder (a readme, say) are fine when it
    is the mod's only mod.toml and that folder isn't part of a layout (romfs/, a title id, Pack/, ...). None when the
    mod has no mod.toml it can use (unread_manifests then names the ones it has)."""
    src = mi.Source(list(rels))
    hit = next((f for f in src.files if f.lower() == MANIFEST), None)
    if hit is not None:
        return src.prefix + hit
    found = _manifest_files(src.files)
    if len(found) != 1:
        return None
    base = found[0][:-len(MANIFEST)]
    layout = mi.STRUCTURAL | rm.ROMFS_TOP
    if any(p.lower() in layout or rm.TID_RE.fullmatch(p.lower()) for p in base.rstrip("/").split("/")):
        return None
    if all(f.startswith(base) or "/" not in f for f in src.files):
        return src.prefix + found[0]
    return None


def unread_manifests(rels: list[str]) -> str | None:
    """Why a mod's mod.toml files can't be read (find_manifest found none it can use), or None when there is none.
    Such a mod isn't added as a mod without mod.toml: its ids, versions and hooks would go unchecked."""
    if find_manifest(rels) is not None:
        return None
    found = _manifest_files(rels)
    if not found:
        return None
    if len(found) > 1:
        return (f"it has {len(found)} mod.toml files ({_some(found, 3)}) and none at its root: it looks like several "
                "mods in one. Extract it and add each mod's folder (the one with its mod.toml) on its own")
    return (f"{found[0]} wasn't read: a mod.toml goes at the root of the mod (loose files like a readme may sit next "
            "to the folder it is in, but no other folders). Move it there, or extract the mod and add the folder "
            "mod.toml is in")


def read_manifest(root: str, rels: list[str] | None = None) -> tuple[Manifest | None, list[str], bool]:
    """(manifest, problems, whether the mod has a mod.toml) for a mod folder. Reads only."""
    if rels is None:
        rels = [r for _, r, _ in walk_files(root)]
    rel = find_manifest(rels)
    if rel is None:
        why = unread_manifests(rels)
        return (None, [why], True) if why else (None, [], False)
    path = os.path.join(root, *rel.split("/"))
    try:
        if os.path.getsize(path) > MANIFEST_MAX:
            return None, [f"mod.toml is bigger than {MANIFEST_MAX // 1024} KB"], True
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, [f"can't read mod.toml: {e.strerror or e}"], True
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, ["mod.toml isn't UTF-8 text"], True
    man, errs = parse_manifest(text, rel[:-len(MANIFEST)])
    return man, errs, True


def game_version(game: GameInfo) -> str | None:
    return mi.KNOWN_BUILDS.get(game.main_bid16)


def main_code_range(game: GameInfo) -> tuple[int, int] | None:
    """[lo, hi) of main's code in guest addresses, from the game's main (None when it isn't set up)."""
    layout = game.layouts.get("main")
    if not layout:
        return None
    _, off, size = next((s for s in layout if s[0] == "text"), layout[0])
    return gh.MAIN_BASE + off, gh.MAIN_BASE + off + size


def manifest_game_problems(man: Manifest, game: GameInfo) -> list[str]:
    """What keeps a mod.toml mod from running on this game and modhost: the game version, the mod API, and hook
    addresses that aren't in main's code."""
    out = []
    have = game_version(game) or "a version modman doesn't know"
    if man.game_versions and have not in man.game_versions:
        out.append(f"made for BotW {', '.join(man.game_versions)} ([mod] game_versions), and your game is "
                   f"{have} (build {game.main_bid16})")
    if man.loader_api > MODHOST_API:
        out.append(f"needs mod API {man.loader_api} ([mod] loader_api), and modhost has mod API {MODHOST_API}: "
                   "update botw-mod-loader and run python tools/build_native.py build")
    rng = main_code_range(game)
    if rng and man.hooks:
        out += [f"[code] hooks: {b}" for b in gh.check_in_code(man.hooks, *rng)]
    return out


def manifest_file_problems(man: Manifest, root: str, s: ModScan, real: list, romfs_error: str | None,
                           no_init: list[str] = ()) -> list[str]:
    """Where mod.toml and the mod's files disagree: a [code] windows DLL that is missing or can't be loaded, and
    [data] folders that are missing or aren't where modman finds the RomFS files (`real`: romfs_mods' entries).
    `no_init`: the mod's 64-bit DLLs without swmod_init."""
    out = []
    norm = lambda p: os.path.normcase(os.path.abspath(p))  # noqa: E731
    if man.dll:
        p = os.path.join(root, *man.rel(man.dll).split("/"))
        if not os.path.isfile(p):
            out.append(f'[code] windows = "{man.dll}": no such file in the mod')
        elif norm(p) not in {norm(x) for x in s.dlls}:
            why = ("doesn't export swmod_init" if norm(p) in {norm(x) for x in [*s.helpers, *no_init]}
                   else "isn't a 64-bit Windows DLL that exports swmod_init")
            out.append(f'[code] windows = "{man.dll}" {why}, so modhost can\'t load it')
    declared = [(k, v) for k, v in (("romfs", man.romfs), ("dlc_romfs", man.dlc_romfs)) if v]
    for key, folder in declared:
        if not os.path.isdir(os.path.join(root, *[p for p in man.folder(folder).split("/") if p])):
            out.append(f'[data] {key} = "{folder}": no such folder in the mod')
    if declared and romfs_error:
        out.append(f"[data]: {romfs_error}")
    if not declared or out:
        return out

    def under(member: str, folder: str | None) -> bool:
        return bool(folder) and member.lower().startswith(man.folder(folder).lower())

    used = set()
    for e in real:
        if under(e.member, man.dlc_romfs) and not e.dlc:
            out.append(f'[data] dlc_romfs = "{man.dlc_romfs}": modman reads the files there as base-game files. '
                       "DLC files go in a folder named 01007EF00011F001/romfs (the title id marks them as DLC)")
            return out
        key, folder = ("dlc_romfs", man.dlc_romfs) if e.dlc else ("romfs", man.romfs)
        if not under(e.member, folder):
            what = "DLC RomFS" if e.dlc else "RomFS"
            out.append(f"{e.member}: a {what} file outside the folder [data] {key} names"
                       + (f' ("{folder}")' if folder else " (it names none)"))
            return out
        used.add(key)
    for key, folder in declared:
        if key not in used:
            out.append(f'[data] {key} = "{folder}" holds no RomFS files modman can use')
    return out


def manifest_v2_problems(man: Manifest, root: str) -> tuple[list[str], list[str]]:
    """(problems, notes) of a mod.toml's Mods page v2 keys against the mod's files: the pictures and the font it
    names must be there, tools/mod_settings.py checks the [[settings]] and [[pages]] (manifest_problems(mod.toml as
    parsed, the folder mod.toml is in)), and tools/mod_pictures.py the pictures (check(manifest, mod folder)). Both
    return (problems, notes). A part whose tool isn't there isn't checked, and a note says so when the mod uses it.
    A check that fails itself is a note, never a refusal: that part is just off."""
    problems: list[str] = []
    notes: list[str] = []
    named = [(f"[mod] {k}", getattr(man, k)) for k in ("icon", "background", "title_picture")]
    named.append(("[font] file", (man.font or {}).get("file")))
    for key, rel in named:
        if rel and not os.path.isfile(os.path.join(root, *man.rel(rel).split("/"))):
            problems.append(f'{key} = "{rel}": no such file in the mod')
    if man.font is not None and feature("font") is None:
        notes.append("its font isn't used: this botw-mod-loader has no tools/font.py yet")
    checks = []
    if man.settings or man.pages:
        here = os.path.join(root, *man.base_rel.split("/")) if man.base_rel else root
        checks.append(("mod_settings", "manifest_problems", (man.raw, here), "settings pages"))
    if man.icon or man.background:
        checks.append(("mod_pictures", "check", (man, root), "pictures"))
    for module, func, args, what in checks:
        if feature(module, func) is None:
            notes.append(f"its {what} aren't used: this botw-mod-loader has no tools/{module}.py yet")
            continue
        res = _feature_call(module, func, *args, problems=notes, what=f"checking its {what}")
        if res:
            p, n = res
            problems += [str(x) for x in p]
            notes += [str(x) for x in n]
    return problems, notes


def scan_mod(root: str, game: GameInfo, name: str) -> ModScan:
    """What a stored mod folder holds that modman can use. Reads only."""
    s = ModScan()
    files = walk_files(root)
    try:
        s.findings = mi.inspect(mi.FolderSource(Path(root)))
    except (OSError, ValueError):
        s.findings = []
    s.manifest, s.manifest_errors, has_manifest = read_manifest(root, [r for _, r, _ in files])
    if s.manifest:
        s.notes += s.manifest.notes

    # RomFS (tools/romfs_mods.py decides, the same way deploy will: its plan skips what deploy skips)
    real = []
    romfs_error = None
    try:
        real = [e for m in rm.plan([root]).mods for e in m.entries if "/" in e.rel]
        if any(not e.dlc for e in real):
            s.kinds.append("romfs")
        if any(e.dlc for e in real):
            s.kinds.append("dlc-romfs")
        if real:
            s.romfs = True
            s.notes.append(f"{len(real)} RomFS file(s)")
    except rm.DeployError as e:
        msg = str(e)
        base = os.path.basename(root)
        romfs_error = name + msg[len(base):] if msg.startswith(base) else msg
        if {f.kind for f in s.findings} & ROMFS_FINDINGS:
            s.problems.append(romfs_error)

    # ExeFS patches: kept when they are for a module the player's game has
    for path, rel, _ in files:
        if not rel.lower().endswith(ep.PATCH_EXTS):
            continue
        try:
            patches = ep.load_patch_file(path)
        except (ep.PatchError, OSError) as e:
            s.problems.append(f"{rel}: {e}")
            continue
        on = [p for p in patches if p.enabled]
        if not on:
            s.problems.append(f"{rel}: every patch in it is turned off")
            continue
        if game.ids:
            fits = [p for p in on if ep.module_for(p.build_id, game.ids)]
        else:
            fits = [p for p in on if mi.KNOWN_BUILDS.get(p.build_id[:16]) == mi.TARGET_VERSION]
        if not fits:
            s.problems.append(f"{rel}: made for {_version_name(on[0].build_id)} (build {on[0].build_id[:16]}), "
                              "not your game: it needs porting")
            continue
        bad = None
        if game.ids:  # (exefs_patch would stop the whole deploy on such a patch: leave the file out instead)
            for p in fits:
                mod = ep.module_for(p.build_id, game.ids)
                why = outside_module(p, mod, game.layouts[mod]) if mod in game.layouts and p.offset >= 0 else None
                if why:
                    bad = f"{rel}: '{p.name}' {why}, so the file isn't used"
                    break
        if bad:
            s.problems.append(bad)
            continue
        s.patches.append(path)
    if s.patches:
        s.kinds.append("exefs-patch")
        s.notes.append(f"{len(s.patches)} ExeFS patch file(s)")

    # Atmosphere cheats: cheats/<first 16 digits of the build id>.txt
    count = 0
    for path, rel, size in files:
        m = CHEAT_RE.search(rel)
        if not m:
            continue
        bid = m.group(2).lower()
        if bid != game.main_bid16:
            s.problems.append(f"{rel}: cheats for {_version_name(bid)}, not your game")
            continue
        try:
            with open(path, "rb") as f:
                found, _ = ch.parse_text(f.read(4 * 2**20).decode("utf-8-sig", errors="replace"))
        except OSError as e:
            s.problems.append(f"{rel}: {e}")
            continue
        n = sum(1 for c in found if not c.master and (c.words or c.error))
        if not n:
            s.problems.append(f"{rel}: no cheats in it")
            continue
        count += n
        s.cheats.append(path)
    if s.cheats:
        s.kinds.append("cheats")
        s.notes.append(f"{count} cheat(s)")

    # Native mods: 64-bit DLLs exporting swmod_init (native/modhost/swmod.h). Their other 64-bit DLLs are helpers
    # they may need: modhost loads mods with the mod's own folder searched first, so helpers go next to them.
    # A mod.toml that names its DLL ([code] windows) makes that the mod's only one. Another DLL that exports
    # swmod_init can't go next to it as a helper: modhost runs swmod_init of every DLL in its folder, so it would
    # run as a second mod that mod.toml doesn't describe (no mod API or hook checks). It is left out.
    named = None
    if s.manifest and s.manifest.dll:
        named = os.path.normcase(os.path.abspath(os.path.join(root, *s.manifest.rel(s.manifest.dll).split("/"))))
    helpers: list[tuple[str, str]] = []
    for path, rel, size in files:
        if not rel.lower().endswith(".dll"):
            continue
        if size > 512 * 2**20:
            s.problems.append(f"{rel}: too big for a mod DLL")
            continue
        try:
            with open(path, "rb") as f:
                info = bn.pe_info(f.read())
        except (bn.PeError, OSError) as e:
            s.problems.append(f"{rel}: {e}")
            continue
        if "swmod_init" not in info.exports:
            if info.machine == 0x8664:
                helpers.append((path, rel))
            else:
                s.notes.append(f"{rel}: not a 64-bit DLL and no swmod_init: left out")
            continue
        if info.machine != 0x8664:
            s.problems.append(f"{rel}: not a 64-bit Windows DLL")
            continue
        if named and os.path.normcase(os.path.abspath(path)) != named:
            s.problems.append(f"{rel}: exports swmod_init too, and mod.toml's [code] windows names "
                              f"{s.manifest.rel(s.manifest.dll)}: left out (next to that DLL, modhost would load it "
                              "as a second mod)")
            continue
        s.dlls.append(path)
    if s.dlls:
        s.kinds.append("native")
        s.notes.append(f"{len(s.dlls)} native mod DLL(s)")
        s.helpers = [p for p, _ in helpers]
        if helpers:
            s.notes.append(f"{len(helpers)} helper DLL(s) without swmod_init ({_some([r for _, r in helpers], 3)}): "
                           "deployed next to the mod DLLs")
    else:
        s.notes += [f"{r}: no swmod_init, so it isn't loaded as a mod (a helper DLL?)" for _, r in helpers]

    # mod.toml: its files and the game must match what it says (add and deploy refuse the mod otherwise)
    if s.manifest:
        v2_problems, v2_notes = manifest_v2_problems(s.manifest, root)
        s.notes += v2_notes
        s.manifest_errors = (manifest_file_problems(s.manifest, root, s, real, romfs_error, [p for p, _ in helpers])
                             + manifest_game_problems(s.manifest, game) + v2_problems)
        if s.manifest_errors:
            s.manifest = None
        elif s.manifest.hooks:
            s.notes.append("hooks: " + ", ".join(f"{a:#x}" for a in s.manifest.hooks)
                           + f" (deploy rebuilds the game code with hook slots for them: {REBUILD_TIME} the first "
                             "time)")

    # Parts of the mod modman can't use (an exlaunch/Skyline code part, ...): say so, every time
    for f in s.findings:
        if f.verdict in mi.USABLE or f.kind in HANDLED_FINDINGS:
            continue
        if f.kind == NATIVE_FINDING:
            if not has_manifest and "native" not in s.kinds:
                s.problems.append("has a mod.toml, but no DLL in it exports swmod_init, so modhost can't load it")
        elif f.kind == CODE_MOD_FINDING:
            n = f.detail.split(":", 1)[0]
            s.problems.append(f"exlaunch / Skyline code ({n}): Savage Wind can't run Switch code mods, and the rest "
                              "of the mod may need it")
        else:
            s.problems.append(f"{f.kind}: {f.detail} ({mi.VERDICTS.get(f.verdict, f.verdict)})")
    s.problems = list(dict.fromkeys(s.problems))
    return s


# ---------------------------------------------------------------- add: storing a mod in the library

def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-") or "mod"
    if RESERVED_RE.match(s) or rm.TID_RE.fullmatch(s) or re.fullmatch(r"[0-9a-f]{16}", s):
        s = "mod-" + s  # (a title or build id as a folder name would change what the files mean)
    return s


def unique_id(base: str, lib: Library) -> str:
    taken = {m["id"] for m in lib.mods}
    if os.path.isdir(lib.mods_dir):
        taken |= {n.lower() for n in os.listdir(lib.mods_dir)}
    mod_id, k = base, 2
    while mod_id in taken:
        mod_id, k = f"{base[:44]}-{k}", k + 1
    return mod_id


STRUCTURAL = {"romfs", "exefs", "cheats", "exefs_patches", "contents", "atmosphere"}


def default_name(src: str) -> str:
    p = Path(src)
    if p.is_file():
        return p.stem
    parts = [x for x in p.parts[1:] if x]
    while parts and (parts[-1].lower() in STRUCTURAL or rm.TID_RE.fullmatch(parts[-1].lower())):
        parts.pop()
    return parts[-1] if parts else p.name or "mod"


def is_what_its_name_says(folder: str, low: str) -> bool:
    """Whether a folder named romfs, exefs or cheats holds what its name says (RomFS folders like Actor/ and Pack/,
    cheat files), rather than holding that layout itself (romfs\\romfs\\..., as 7-Zip's 'Extract to romfs\\' makes
    of a romfs.zip)."""
    try:
        with os.scandir(folder) as it:
            kids = [(e.name.lower(), e.is_dir(follow_symlinks=False)) for e in it]
    except OSError:
        return True
    dirs = {n for n, is_dir in kids if is_dir}
    if low in dirs:
        return False
    if low == "romfs":
        return bool(dirs & (rm.ROMFS_TOP | {"aoc"}))
    if low == "cheats":
        return any(not is_dir and CHEAT_FILE_RE.fullmatch(n) for n, is_dir in kids)
    return True


def context_parts(src: str) -> list[str]:
    """The folder names at the end of a mod folder's path that give its files their meaning (romfs, cheats, a title
    id: ...\\01007EF00011F001\\romfs is DLC). They are kept above the files in the library. A folder that only has
    such a name (it holds romfs\\ itself, say) gives its files no meaning, so nothing is kept for it."""
    keep: list[str] = []
    for k, part in enumerate(reversed(Path(src).parts[1:])):
        low = part.lower()
        if rm.TID_RE.fullmatch(low):
            keep.insert(0, part)
            break
        if low in ("romfs", "exefs", "cheats"):
            if k == 0 and not is_what_its_name_says(src, low):
                break
            keep.insert(0, part)
            continue
        break
    return keep


def _inspect_source(src: str) -> tuple[list, list[str]]:
    """mod_inspect's findings and the file names of a folder or .zip (or one file)."""
    if os.path.isfile(src) and not zipfile.is_zipfile(src):
        return [], [os.path.basename(src)]
    try:
        source = mi.open_source(Path(src))
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        raise ModmanError(f"Can't read {src}: {e}") from None
    try:
        return mi.inspect(source), list(source.files)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, EOFError) as e:
        raise ModmanError(f"Can't read {src}: {e}") from None
    finally:
        if isinstance(source, mi.ZipSource):
            source.zf.close()


# The library keeps its own copy of these mod files, never a hard link to the mod's folder: a rebuilt DLL or an
# edited mod.toml there must not change the mod that was added (`list` would show one version, mod.toml another).
COPY_BELOW = 2**20                      # every file smaller than this (1 MiB)
COPY_ALWAYS = (".dll", ".exe", ".pdb")  # code, any size; and MANIFEST (mod.toml), any size


def keep_own_copy(rel: str, size: int) -> bool:
    """Whether the library stores its own copy of a mod file (rel: its "/"-separated path in the mod, size: its
    bytes) rather than a hard link. Only bigger data files (RomFS assets) are linked."""
    name = rel.rsplit("/", 1)[-1].lower()
    return size < COPY_BELOW or name.endswith(COPY_ALWAYS) or name == MANIFEST


def store_folder(src: str, dest: str) -> tuple[int, int, list[str]]:
    """Put a mod folder's files in dest. Returns (linked, copied, notes). Links and junctions inside the mod are left
    out. Files keep_own_copy names (under 1 MiB, .dll/.exe/.pdb, mod.toml) are copied, so the library is a snapshot of
    the mod's code and settings. Bigger data files are hard links on the same drive (no extra space; still the same
    file as the mod's own, so neither may be edited in place) and copies otherwise. Read-only files are copied: a
    hard link shares the read-only setting, and deleting the link later must not change the player's own file."""
    read_only: set[str] = set()
    files = walk_files(src, read_only)
    prefix = context_parts(src)
    notes: list[str] = []
    os.makedirs(dest, exist_ok=True)
    same = os.stat(src).st_dev == os.stat(dest).st_dev
    plan = [(path, rel, size, same and rel not in read_only and not keep_own_copy(rel, size))
            for path, rel, size in files]
    left = sum(size for _, _, size, link in plan if not link)  # (bytes still to copy)
    if left:
        free = shutil.disk_usage(dest).free
        if left > free:
            raise ModmanError(f"Not enough free space: {_size(left)} to copy, {_size(free)} free.")
    linked = copied = 0
    for path, rel, size, link in plan:
        dst = os.path.join(dest, *prefix, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if link:
            try:
                os.link(path, dst)
                linked += 1
                continue
            except OSError:  # (a drive without hard links, or a file with too many names): a copy, if it fits
                free = shutil.disk_usage(dest).free
                if left + size > free:
                    raise ModmanError(f"Not enough free space: {_size(left + size)} to copy, {_size(free)} "
                                      "free.") from None
        else:
            left -= size
        shutil.copyfile(path, dst)
        copied += 1
    return linked, copied, notes


def extract_zip(src: str, dest: str) -> tuple[int, list[str]]:
    """Extract a .zip into dest. Refuses a member that would land outside dest (absolute paths, drive letters,
    ".."), password-protected members and compression methods Python can't read, before writing anything."""
    notes: list[str] = []
    name = os.path.basename(src)
    with zipfile.ZipFile(src) as zf:
        plan: list[tuple[zipfile.ZipInfo, list[str]]] = []
        seen: dict[str, zipfile.ZipInfo] = {}
        skipped_names = skipped_links = 0
        for info in zf.infolist():
            raw = info.filename.replace("\\", "/")
            if raw.endswith("/"):
                continue
            parts = raw.split("/")
            if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or ".." in parts:
                raise ModmanError(f"Not added: {name} holds a file with an unsafe path ({info.filename!r}) that "
                                  "would be written outside the mod folder.")
            parts = [p for p in parts if p not in ("", ".")]
            if not parts:
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                skipped_links += 1
                continue
            if any(_bad_part(p) for p in parts):
                skipped_names += 1
                continue
            check_zip_member(info, name)
            key = "/".join(parts).lower()
            if key in seen:
                if (seen[key].CRC, seen[key].file_size) == (info.CRC, info.file_size):
                    continue
                raise ModmanError(f"Not added: {name} has two different files named {'/'.join(parts)}.")
            seen[key] = info
            plan.append((info, parts))
        need, free = sum(i.file_size for i, _ in plan), shutil.disk_usage(_existing(dest)).free
        if need > free:
            raise ModmanError(f"Not enough free space to extract {name}: {_size(need)} needed, {_size(free)} free.")
        os.makedirs(dest, exist_ok=True)
        for info, parts in plan:
            dst = os.path.join(dest, *parts)
            if not _inside(dst, dest):  # (belt and braces: the checks above already make this impossible)
                raise ModmanError(f"Not added: {name} holds an unsafe path ({info.filename!r}).")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                with zf.open(info) as fsrc, open(dst, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst, 1 << 20)
            except (zipfile.BadZipFile, OSError, EOFError, RuntimeError, NotImplementedError, zlib.error) as e:
                raise ModmanError(f"Not added: can't extract {info.filename} from {name} ({e}).") from None
        if skipped_names:
            notes.append(f"left out {skipped_names} file(s) whose names Windows can't use")
        if skipped_links:
            notes.append(f"left out {skipped_links} link(s)")
        return len(plan), notes


ZIP_READS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA)


def check_zip_member(info: zipfile.ZipInfo, name: str) -> None:
    """Refuse a .zip (named `name`) whose member Python can't read: password-protected, or a compression such as
    Deflate64 (Windows uses it for big files)."""
    if info.flag_bits & 1:
        raise ModmanError(f"Not added: {name} is password-protected. Extract it yourself, then add the folder.")
    if info.compress_type not in ZIP_READS:
        raise ModmanError(f"Not added: {name} uses a compression Python can't read. Extract it with 7-Zip, then add "
                          "the folder.")


def check_zip(src: str) -> None:
    """extract_zip's checks for a .zip modman reads without extracting it (a Wii U mod wiiu_convert reads)."""
    name = os.path.basename(src)
    try:
        with zipfile.ZipFile(src) as zf:
            for info in zf.infolist():
                if not info.filename.endswith("/"):
                    check_zip_member(info, name)
    except (OSError, zipfile.BadZipFile) as e:
        raise ModmanError(f"Not added: can't read {name} ({e}).") from None


def _existing(path: str) -> str:
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def store_file(src: str, dest: str) -> str:
    """One patch, cheat or DLL file passed on its own: placed where scan_mod looks for it. Returns "link" or "copy":
    a copy (as store_folder makes) unless it is a data file of 1 MiB or more."""
    base = os.path.basename(src)
    low = base.lower()
    if low.endswith(".txt"):
        if not re.fullmatch(r"[0-9a-f]{16}\.txt", low):
            raise ModmanError(f"Not added: a cheat file must be named after the game's build id, like "
                              f"{BUILD_190}.txt (BotW 1.9.0).")
        sub = ["cheats"]
    elif low.endswith((".ips", ".pchtxt", ".dll")):
        sub = []
    else:
        raise ModmanError(f"Not added: {base} isn't a mod folder, a .zip, or a .ips, .pchtxt, cheat .txt or .dll file.")
    dst = os.path.join(dest, *sub, base)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    size = os.path.getsize(src)
    if not keep_own_copy(base, size) and not _read_only(src):
        try:
            os.link(src, dst)
            return "link"
        except OSError:
            pass  # (another drive, or one without hard links: copied below, if it fits)
    free = shutil.disk_usage(os.path.dirname(dst)).free
    if size > free:
        raise ModmanError(f"Not enough free space: {_size(size)} to copy, {_size(free)} free.")
    shutil.copyfile(src, dst)
    return "copy"


def example_dll(src: str, stage: str) -> str | None:
    """The mod.toml of an example in this repo (native/examples/<x>) names its DLL by file name only, and
    `build_native.py build` puts that DLL in build\\native. When it isn't in the mod folder itself, it is copied from
    there into the library copy. Returns a note, or None when there was nothing to do."""
    if not _inside(src, EXAMPLES_DIR):
        return None
    man, _, _ = read_manifest(stage)
    if not man or not man.dll:
        return None
    dst = os.path.join(stage, *man.rel(man.dll).split("/"))
    if os.path.isfile(dst):
        return None
    base = os.path.basename(dst).lower()
    examples = (*bn.EXAMPLES, *bn.HOOK_EXAMPLES, *getattr(bn, "GHOST_EXAMPLES", ()))
    target = next((t for t in examples if t.filename.lower() == base), None)
    if target is None:
        return None
    built = str(target.output(Path(MODHOST_DIR)))
    if not os.path.isfile(built):
        raise ModmanError(f"Not added: {target.filename} isn't built yet. Run python tools/build_native.py build, "
                          "then add the example again.")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(built, dst)  # (a copy: the next build replaces the built one, the library keeps this one)
    return f"{man.dll}: taken from {built} (built by build_native.py; add the example again after a rebuild)"


def library_ids(lib: Library, scans: list[tuple[dict, str, ModScan]] = ()) -> dict[str, tuple[dict, str | None]]:
    """What a dependency can name -> (the library entry, its mod version), for every mod in the library, on or off,
    as its files say now: the id in its mod.toml, and its id in the library (the only id of a mod without one; the
    two differ for a mod.toml put into a library folder by hand). `scans`: mods already scanned (their mod.toml
    isn't read again). A mod.toml id wins over a library id, and a mod that is on over one that is off."""
    scanned = {m["id"]: s for m, _, s in scans}
    manifests: dict[str, Manifest | None] = {}
    for m in lib.mods:
        if m["id"] in scanned:
            manifests[m["id"]] = scanned[m["id"]].manifest
            continue
        root = os.path.join(lib.mods_dir, m["id"])
        try:
            manifests[m["id"]] = read_manifest(root)[0] if os.path.isdir(root) else None
        except OSError:
            manifests[m["id"]] = None
    out: dict[str, tuple[dict, str | None]] = {}
    for m in lib.mods:  # (a mod.toml that can't be used now: the version add recorded, for the messages)
        man = manifests[m["id"]]
        out[m["id"]] = (m, man.version if man else m.get("version"))
    for on in (False, True):
        for m in lib.mods:
            man = manifests[m["id"]]
            if man and bool(m.get("enabled")) == on:
                out[man.id] = (m, man.version)
    return out


def dependency_problem(dep: str, req: str, entry: dict | None, version: str | None) -> str | None:
    """Why a dependency isn't met by this library entry (None when it is). `version` is the entry's mod version."""
    want = "" if req == "*" else f" {req}"
    if entry is None:
        return f"needs {dep}{want}, which isn't in the library: add it"
    if not entry.get("enabled"):
        return f"needs {dep}{want}, which is turned off: python tools/modman.py enable {entry['id']}"
    if not satisfies(version, req):
        have = f"version {version}" if version else "a mod without a mod.toml (no version)"
        return f"needs {dep}{want}, and the library has {have}"
    return None


# ---------------------------------------------------------------- Wii U mods: converted when added

def _wiiu_folder(src: str) -> bool:
    """A Wii U mod's content (or aoc/0010) folder passed on its own: it holds RomFS folders (Actor/, Pack/...), and
    its name says it is Wii U data."""
    p = Path(src)
    if not p.is_dir():
        return False
    low = p.name.lower()
    if low != "content" and not (low == "0010" and p.parent.name.lower() == "aoc"):
        return False
    try:
        with os.scandir(p) as it:
            dirs = {e.name.lower() for e in it if e.is_dir(follow_symlinks=False)}
    except OSError:
        return False
    return bool(dirs & rm.ROMFS_TOP) and "romfs" not in dirs


def is_wiiu_mod(src: str, findings: list) -> bool:
    """Whether src is a Wii U mod tools/wiiu_convert.py can turn into a Switch one: one romfs_mods refuses as Wii U
    (content/, only aoc/0010/, a Cemu graphic pack with game files), or its content folder on its own. A Switch
    overlay folder (bare aoc/0010/ next to Actor/, Pack/...) is not one. Reads only."""
    if _wiiu_folder(src):
        return True
    if not any(f.platform == "wiiu" for f in findings):
        return False
    try:
        mod = rm.load_mod(src)
    except rm.WiiUModError:
        return True
    except (rm.DeployError, OSError, ValueError, RuntimeError, zipfile.BadZipFile, EOFError):
        return False
    if mod.zip:
        mod.zip.close()
    return False


def other_game_pack(src: str, findings: list) -> bool:
    """Whether src is a Cemu graphic pack made for another Wii U game: no rules.txt in it names BotW's title id
    (romfs_mods decides, as deploy would). Reads only."""
    if not any(f.kind == "Cemu graphic pack" for f in findings):  # (mod_inspect's finding for such a rules.txt)
        return False
    try:
        mod = rm.load_mod(src)
    except rm.OtherGameError:
        return True
    except (rm.DeployError, OSError, ValueError, RuntimeError, zipfile.BadZipFile, EOFError, zlib.error):
        return False
    if mod.zip:
        mod.zip.close()
    return False


def _wiiu_name(src: str) -> str:
    """The mod's name for a Wii U mod passed as its content (or aoc/0010) folder: the folder above it."""
    p = Path(src)
    if p.is_dir() and p.name.lower() == "content":
        return p.parent.name or p.name
    if p.is_dir() and p.name == "0010" and p.parent.name.lower() == "aoc":
        return p.parent.parent.name or p.name
    return default_name(src)


def wiiu_counts(rep) -> dict[str, int]:
    """What happened to a converted mod's game files (the "wiiu" entry in config.json)."""
    return rep.counts()


def converted_report(src: str) -> dict | None:
    """The counts and verdict in the wiiu-convert-report.txt that `wiiu_convert.py convert` leaves in its output
    folder, when src is such a folder (None otherwise): modman then treats it as a Wii U mod it converted itself."""
    path = os.path.join(src, wc.REPORT)
    if not os.path.isdir(src) or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return wc.report_counts(f.read(1 << 20))
    except OSError:
        return None


def wiiu_summary(rep, report: str | None = None) -> list[str]:
    """Up to 4 short lines: converted, left as Wii U data, left out, and where the report is (when it was kept)."""
    c = wiiu_counts(rep)
    part = f" ({c['partly']} of them only in part)" if c["partly"] else ""
    if c["converted"] and c["converted"] == c["files"]:
        out = [f"From Wii U: all {c['files']} game file(s) converted{part}."]
    else:
        out = [f"From Wii U: {c['converted']} of {c['files']} game file(s) converted{part}."]
    probs = rep.problems()
    if probs:
        kinds = f"{len(probs)} ({_count_text([p.kind for p in probs])})"
        out.append(f"Left as Wii U data: {kinds}. The game can't read these: it may crash or look wrong where the mod "
                   "uses them." if report else f"Can't be converted yet: {kinds}.")
    gone = ["the RSTB" if f["kind"] == "RSTB" else f["path"].rsplit("/", 1)[-1]
            for f in rep.files if f["status"] == wc.LEFT_OUT]
    gone += [p.rsplit("/", 1)[-1] for p in rep.unsafe + rep.other]
    if gone:
        out.append(f"Left out: {len(gone)} ({_some(gone, 3)}).")
    if c.get("attention"):
        out.append(f"Models and textures converted with warnings: {c['attention']} (under \"Needs attention\" in the "
                   "report; not proven on real Wii U files yet: check them in game).")
    if report:
        out.append(f"Report: {report}")
    return out


def converted_summary(c: dict, report: str) -> list[str]:
    """wiiu_summary's lines for a folder wiiu_convert.py convert wrote (from its report's counts)."""
    if c["converted"] and c["converted"] == c["files"]:
        out = [f"From Wii U (converted by tools/wiiu_convert.py): all {c['files']} game file(s) converted."]
    else:
        out = [f"From Wii U (converted by tools/wiiu_convert.py): {c['converted']} of {c['files']} game file(s) "
               "converted."]
    if c["wiiu_data"]:
        out.append(f"Left as Wii U data: {c['wiiu_data']}. The game can't read these: it may crash or look wrong "
                   "where the mod uses them.")
    if c.get("attention"):
        out.append(f"Models and textures converted with warnings: {c['attention']} (see \"Needs attention\" in the "
                   "report).")
    out.append(f"Report: {report}")
    return out


def _count_text(items: list[str]) -> str:
    """How many of each, most first: 2 model/texture, 1 audio."""
    counts: dict[str, int] = {}
    for x in items:
        counts[x] = counts.get(x, 0) + 1
    return ", ".join(f"{v} {k}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))


def wiiu_short(m: dict) -> str:
    """A library entry's Wii U state for list and status: "" (not from Wii U), "from Wii U", or with the count of
    files that stayed Wii U data."""
    w = m.get("wiiu")
    if not isinstance(w, dict):
        return ""
    n = w.get("wiiu_data", 0)
    return f"from Wii U, {n} file(s) not converted" if n else "from Wii U"


def game_romfs(lib: "Library") -> str | None:
    """The game's own RomFS (Savage Wind's extracted\\romfs), when the library knows the install and it is there: the
    Wii U model converter reads vanilla materials, mipmaps and the size table from it (read only)."""
    if not lib.install:
        return None
    romfs = os.path.join(lib.install, "extracted", "romfs")
    return romfs if os.path.isdir(os.path.join(romfs, "Model")) else None


def convert_wiiu(src: str, stage: str, name: str, game: str | None = None):
    """Convert a Wii U mod into stage (a new folder) with tools/wiiu_convert.py (with the game's RomFS for models and
    textures, when given). Returns its report. Refuses (and stage is gone) when nothing could be converted or
    wiiu_convert can't use the mod."""
    try:
        rep = wc.convert_mod(src, stage, game=game)
    except wc.ConvertError as e:
        raise ModmanError(f"Not added: {e}" if e.whole else f"Not added: {name} {e}") from None
    except zlib.error as e:  # (a .zip with damaged packed data: wiiu_convert reads it without extracting it)
        raise ModmanError(f"Not added: {os.path.basename(src)} is damaged ({e}). Download it again, or extract it "
                          "with 7-Zip and add the folder.") from None
    if rep.verdict() in wc.NOTHING:
        raise ModmanError(f"Not added: nothing in {name} could be converted from Wii U."
                          + "".join(f"\n  - {x}" for x in wiiu_summary(rep)[1:]))
    return rep


def add_download(lib: Library, src: str, name: str | None = None, *, convert: bool = True,
                 bnp_options: list[str] = (), bnp_no_options: bool = False) -> tuple[dict, ModScan, list[str]]:
    """add_mod for a download it can't use as it is (bnp.packed_download: a .rar, a .7z that isn't a .bnp, a .zip
    holding archives): unpacked into a work folder in the library (tools/bnp.py unpack_download: every name and
    size checked, at most 3 archives deep, nothing run), the mod in it added, the work folder removed. Several mods
    in it: --option names the one to add."""
    os.makedirs(lib.mods_dir, exist_ok=True)
    work = os.path.join(lib.mods_dir, ADDING + slug(default_name(src))[:24] + ".unpack")
    if os.path.lexists(work):
        remove_tree(work)  # (left by an add that was cut short; the name is modman's own)
    try:
        try:
            dl = bnp.unpack_download(src, work, say=say)
            found, picks = dl.pick(list(bnp_options))
        except bnp.BnpError as e:
            raise ModmanError(str(e)) from None
        entry, scan, notes = add_mod(lib, found.path, name, convert=convert, bnp_options=picks,
                                     bnp_no_options=bnp_no_options, download=src)
    finally:
        if os.path.lexists(work):
            remove_tree(work)
    left = [f"left out of the download: {n}" for n in dl.notes]
    return entry, scan, [f"unpacked from {os.path.basename(src)}" + (f": {found.label}" if len(dl.found) > 1 else "")
                         ] + left + notes


def add_mod(lib: Library, src: str, name: str | None = None, *, convert: bool = True,
            bnp_options: list[str] = (), bnp_no_options: bool = False,
            download: str | None = None) -> tuple[dict, ModScan, list[str]]:
    """Inspect, store and record a mod. Returns (its config entry, its scan, notes). A mod with a mod.toml gets its
    id, name and version from it (--name still picks the name). A Wii U mod is converted into the library (its
    notes start with what was converted), unless convert is False: then it is refused as before. A packed download
    goes through add_download (`download`: the one src was unpacked from, by add_download only)."""
    src = _abs(src)
    if not os.path.exists(src):
        raise ModmanError(f"{src} not found.")
    if not download and (_inside(src, lib.root) or _inside(lib.root, src)):
        raise ModmanError("That folder is (or holds) the mod library itself: add the mod's own folder or .zip.")
    if lib.install and (_inside(src, os.path.join(lib.install, "extracted")) or _inside(lib.install, src)):
        raise ModmanError("That is Savage Wind's own folder or game files, not a mod.")
    given = one_line(name or "").strip()
    magic = None if download else bnp.archive_magic(src)
    packed = magic in ("7z", "rar") and bnp.packed_download(src)  # (before inspecting: a .rar can look like a .zip)
    findings, names = ([], []) if packed else _inspect_source(src)
    if packed or (magic == "zip" and bnp.packed_download(src, findings, names)):
        return add_download(lib, src, name, convert=convert, bnp_options=bnp_options, bnp_no_options=bnp_no_options)
    pkg = bnp.package_kind(src, findings, names)  # (a BCML package: installed by tools/bnp.py)
    upkg = None if pkg else ukmm.package_kind(src, findings, names)  # (a UKMM package: tools/ukmm.py)
    try:
        info = bnp.peek(src, pkg) if pkg else ukmm.peek(src) if upkg else None
    except (bnp.BnpError, ukmm.UkmmError) as e:
        raise ModmanError(str(e)) from None
    wiiu = convert and not pkg and not upkg and is_wiiu_mod(src, findings)
    name = given or one_line(_wiiu_name(src) if wiiu else (info.name if info and info.name else default_name(src))
                             ).strip() or "mod"
    bare = next((f for f in findings if f.kind == mi.BARE_WIIU and not wiiu), None)
    if bare:  # (bare Wii U folders: romfs_mods would deploy them as Switch files)
        if convert:
            raise ModmanError(f"Not added: {name} has {bare.detail}.")
        findings = [f for f in findings if f is not bare]   # (--no-convert: refused below, as before)
    if other_game_pack(src, findings):
        raise ModmanError(f"Not added: {name} is a Cemu graphic pack made for another game, not BotW (its rules.txt "
                          "has no BotW title id).")
    converted = None if wiiu else converted_report(src)  # (a folder wiiu_convert.py convert wrote)
    if converted and converted["verdict"] in wc.NOTHING:
        raise ModmanError(f"Not added: nothing in {name} could be converted from Wii U (its {wc.REPORT} says so).")
    blocked = {mi.REWRITE, mi.RETARGET}
    if findings and all(f.verdict in blocked for f in findings):
        reasons = "\n".join(f"  - {f.kind}: {f.detail} ({mi.VERDICTS[f.verdict]})" for f in findings)
        raise ModmanError(f"Not added: {name} can't be used with Savage Wind.\n{reasons}")
    loose = any(n.lower().endswith((".dll", ".ips", ".pchtxt", ".txt")) for n in names)
    if not wiiu and not pkg and not findings and not loose:
        raise ModmanError(f"Not added: {name} doesn't look like a BotW mod (no RomFS files, patches, cheats or "
                          "DLLs). If it's packed in a .7z or .rar, extract it first.")
    game = game_info(lib.install)
    mod_id = unique_id(slug(name), lib)
    os.makedirs(lib.mods_dir, exist_ok=True)
    stuck = bnp.sweep_stale(lib.mods_dir, remove_tree)  # (any add's work folders left by one that was cut short)
    stage = os.path.join(lib.mods_dir, ADDING + mod_id)
    if os.path.lexists(stage):
        remove_tree(stage)  # (left by an add that was cut short; the name is modman's own)
    notes: list[str] = [f"{p} (left by an add that was cut short) couldn't be removed: delete it yourself"
                        for p in stuck]
    rep = None
    try:
        if pkg:
            try:
                res = bnp.add(src, pkg, stage, os.path.join(lib.require_install(), "extracted"), info,
                              picks=list(bnp_options), no_defaults=bnp_no_options, unzip=extract_zip, say=say)
            except bnp.BnpError as e:
                raise ModmanError(str(e)) from None
            notes += res.summary()
        elif upkg:
            try:
                res = ukmm.add(src, upkg, stage, os.path.join(lib.require_install(), "extracted"), info,
                               picks=list(bnp_options), no_defaults=bnp_no_options, say=say)
            except ukmm.UkmmError as e:
                raise ModmanError(str(e)) from None
            notes += res.summary()
        elif wiiu:
            if os.path.isfile(src):
                check_zip(src)  # (password, compression Python can't read: refused as any .zip is)
            say(f"Converting {name} from Wii U (a big mod takes a while)...")
            rep = convert_wiiu(src, stage, name, game_romfs(lib))
        elif os.path.isdir(src):
            linked, copied, more = store_folder(src, stage)
            notes += more
            notes.append(f"{linked + copied} file(s) " + ("linked" if not copied else
                                                         "copied" if not linked else f"({linked} linked, {copied} copied)"))
        elif zipfile.is_zipfile(src):
            n, more = extract_zip(src, stage)
            notes += more
            notes.append(f"{n} file(s) extracted")
        else:
            os.makedirs(stage, exist_ok=True)
            store_file(src, stage)
        if not (pkg or upkg or wiiu):  # (a Pack/*.pack copied whole from another game version: filled from yours)
            notes += bnp.fill_old_packs(stage, game_romfs(lib))
        note = example_dll(src, stage)
        if note:
            notes.append(note)
        scan = scan_mod(stage, game, name)
        if scan.manifest_errors:
            raise ModmanError(f"Not added: {name}'s mod.toml can't be used:"
                              + "".join(f"\n  - {e}" for e in scan.manifest_errors))
        man = scan.manifest
        if man:
            name = given or man.name
            known = library_ids(lib)  # (mod.toml ids too: a mod added under another library id has one)
            if man.id in known:
                old, version = known[man.id]
                what = " ".join(x for x in (old["name"], str(version or "")) if x)
                also = "" if old["id"] == man.id else f", library id {old['id']}"
                raise ModmanError(f"Not added: the library already has {man.id} ({what}{also}). To put this one in "
                                  f"its place: python tools/modman.py remove {old['id']}, then add it again.")
            if man.id in {n.lower() for n in os.listdir(lib.mods_dir)}:
                raise ModmanError(f"Not added: {os.path.join(lib.mods_dir, man.id)} already exists, and the library "
                                  "doesn't list it. Move that folder away, then add the mod again.")
            mod_id = man.id
            for dep, req in man.dependencies.items():
                hit, version = known.get(dep, (None, None))
                why = dependency_problem(dep, req, hit, version)
                if why:
                    notes.append(f"{why} (deploy refuses until then)")
        if rep:
            # (the library copy holds only the converted game files: name the other parts modman can't use here)
            scan.problems = list(dict.fromkeys(scan.problems + [
                f"{f.kind}: {f.detail} ({mi.VERDICTS.get(f.verdict, f.verdict)})" for f in findings
                if f.verdict not in mi.USABLE and f.kind not in HANDLED_FINDINGS]))
        if not scan.kinds:
            reasons = scan.problems  # (scan_mod also lists the parts mod_inspect found that modman can't use)
            raise ModmanError(f"Not added: {name} has nothing Savage Wind can use."
                              + "".join(f"\n  - {r}" for r in reasons or ["no RomFS files, patches, cheats or "
                                                                          "native mod DLLs for your game"]))
        final = os.path.join(lib.mods_dir, mod_id)
        if rep:
            rep.out = final  # (the report names the folder the mod ends up in, not the staging one)
            with open(os.path.join(stage, wc.REPORT), "w", encoding="utf-8") as f:
                f.write(rep.text())
            notes[:0] = wiiu_summary(rep, os.path.join(final, wc.REPORT))
        elif converted:
            notes[:0] = converted_summary(converted, os.path.join(final, wc.REPORT))
        os.rename(stage, final)
    except BaseException:
        if os.path.lexists(stage):
            remove_tree(stage)
        raise
    entry = {"id": mod_id, "name": name, "enabled": True, "kinds": scan.kinds, "source": download or src,
             "added": _now()}
    if man:
        entry["version"] = man.version
    elif pkg or upkg:
        entry["bnp" if pkg else "ukmm"] = {"options": [o.name for o in res.chosen]}
        if info.version:
            entry["version"] = info.version
    if rep:
        entry["wiiu"] = wiiu_counts(rep)
    elif converted:
        entry["wiiu"] = {k: converted[k] for k in WIIU_KEYS}
    if scan.problems:
        entry["problems"] = scan.problems
    lib.mods.append(entry)
    lib.save()
    notes += add_hooks(lib, entry, os.path.join(lib.mods_dir, mod_id), man)
    return entry, scan, notes


def add_hooks(lib: Library, entry: dict, folder: str, man: Manifest | None) -> list[str]:
    """After a mod is in the library: each Mods page v2 tool's on_add(lib, entry, folder, manifest) runs (the
    pictures cache, say), in FEATURES order. Returns their notes. A tool that fails never undoes the add."""
    notes: list[str] = []
    for module in FEATURES:
        res = _feature_call(module, "on_add", lib, entry, folder, man, problems=notes)
        notes += [str(n) for n in (res or [])]
    return notes


def remove_mod(lib: Library, ref: str) -> dict:
    i, m = lib.find(ref)
    path = lib.mod_path(m["id"])
    if not _inside(path, lib.mods_dir) or os.path.normcase(path) == os.path.normcase(lib.mods_dir):
        raise ModmanError(f"Refusing to delete {path}: it isn't a mod folder inside the library.")
    if os.path.lexists(path):
        remove_tree(path)
    del lib.mods[i]
    lib.save()
    return m


# ---------------------------------------------------------------- cheats: merging the enabled cheat files

@dataclass
class CheatMerge:
    text: str = ""
    count: int = 0
    names: list[str] = field(default_factory=list)
    enable: list[str] | None = None        # None: every cheat (--all)
    mods: list[str] = field(default_factory=list)
    attention: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _sections(text: str) -> list[list[str]]:
    """A cheat file's sections: a [name] or {master} line and the lines after it, as written. A "// group:" line
    (in a file that was merged already) ends a section and is dropped: the merge makes its own groups."""
    out: list[list[str]] = []
    cur = None
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if ch.GROUP_RE.fullmatch(line):
            cur = None
        elif line[:1] in ("[", "{") and ch._name_end(line) > 0:
            cur = [line]
            out.append(cur)
        elif cur is not None and line:
            cur.append(line)
    return out


def _section_name(header: str) -> str:
    return header[1:ch._name_end(header)].strip()


def _section_code(lines: list[str]):
    """What a section runs, to tell identical sections apart: its words (or its lines when it can't be parsed)."""
    found, _ = ch.parse_text("\n".join(lines))
    if len(found) == 1 and not found[0].error:
        return tuple(found[0].words)
    return tuple(" ".join(ln.split()).upper() for ln in lines[1:])


def merge_cheats(items: list[tuple[str, str]]) -> CheatMerge:
    """Merge (mod name, cheat file) pairs in order into one cheat file for tools/cheats.py. Each mod's cheats go
    in their own group, under a "// group: <mod name>" line, with that mod's own master code: cheats.py starts every
    group from fresh registers, so each mod's cheats run as its file would on its own. Identical cheats (same name,
    code and master code) are kept once; a different cheat with a name already taken gets "(<mod name>)" added. A
    mod whose master code can't run is left out, because its cheats depend on it. A mod with a toggles.txt next to
    its file contributes only the cheats it turns on (else all of them)."""
    r = CheatMerge()
    body: list[str] = []
    taken: dict[str, tuple] = {}    # cheat name (lower case) -> (its master's code, its code)
    explicit = False
    enable: list[str] = []
    for mod, path in items:
        try:
            with open(path, "rb") as f:
                text = f.read().decode("utf-8-sig", errors="replace")
        except OSError as e:
            r.attention.append(f"{mod}: can't read {os.path.basename(path)} ({e})")
            continue
        sections = _sections(text)
        masters = [s for s in sections if s[0].startswith("{")]
        mine = next((s for s in masters if len(s) > 1 and _section_code(s)), None)
        if len(masters) > 1:
            r.notes.append(f"{mod}: {os.path.basename(path)} has {len(masters)} master codes; only the first one "
                           "with code is used")
        mcode = None
        if mine is not None:
            found, _ = ch.parse_text("\n".join(mine))
            bad = next((c.error for c in found if c.error), None)
            if bad:
                r.attention.append(f"{mod}: its cheats were left out. Its master code can't run ({bad}), and its "
                                   "cheats depend on it.")
                continue
            mcode = _section_code(mine)
        toggles_path = os.path.join(os.path.dirname(path), "toggles.txt")
        toggles = None
        if os.path.isfile(toggles_path):
            with open(toggles_path, "rb") as f:
                toggles = ch.parse_toggles(f.read().decode("utf-8-sig", errors="replace"))
            explicit = True
        added: list[str] = []
        label = re.sub(r"[\[\]{}]", "", mod)  # (brackets in a mod's name would end the cheat's name early)
        for s in sections:
            if s[0].startswith("{"):
                continue
            if not (len(s) > 1 or s[0][ch._name_end(s[0]) + 1:].strip()):
                continue  # (a name with no code)
            orig = name = _section_name(s[0])
            code = (mcode, _section_code(s))
            key = name.lower()
            if key in taken:
                if taken[key] == code:
                    continue  # (the same cheat, with the same master code, from two mods: once is enough)
                new, k = f"{name} ({label})", 2
                while new.lower() in taken:
                    new, k = f"{name} ({label} {k})", k + 1
                why = "a different one" if taken[key][1] != code[1] else "the same one with another master code"
                r.notes.append(f"{mod}: cheat '{name}' renamed '{new}' (another mod has {why})")
                end = ch._name_end(s[0])
                s = [s[0][0] + new + s[0][end] + s[0][end + 1:]] + s[1:]
                name, key = new, new.lower()
            taken[key] = code
            added += [*s, ""]
            r.names.append(name)
            r.count += 1
            if toggles is None or toggles.get(orig, False):
                enable.append(name)
        if added:
            r.mods.append(mod)
            body += [f"// group: {' '.join(mod.split())}", *(mine + [""] if mine else []), *added]
    head = ["// Made by modman (botw-mod-loader) from your enabled cheat mods: "
            + ", ".join(" ".join(m.split()) for m in r.mods),
            "// Deploy again to remake it; edits here are lost.",
            "// Each '// group:' line starts one mod's cheats, with its own master code. tools/cheats.py runs each",
            "// group as if it were that mod's own cheat file, from fresh registers.", ""]
    r.text = "\n".join(head + body) + "\n"
    r.enable = enable if explicit else None
    return r


# ---------------------------------------------------------------- deploy

def game_running() -> bool:
    return swr.game_running()


def run_rebuild(install: str, exefs: str, out: str, hooks: str | None = None) -> int:
    """tools/sw_rebuild.py build, into `out` (+ .id), with hook slots for the addresses in the file `hooks` when
    given. Returns its exit code."""
    argv = ["build", install, "--exefs", exefs, "--out", out]
    if hooks:
        argv += ["--hooks", hooks]
    return swr.main(argv)


def vanilla_main(install: str) -> str | None:
    """sw_rebuild's backup of the vanilla main when it installed a patched one in extracted\\exefs (else None)."""
    P = swr.paths_for(install)
    sha = swr.load_state(install).get("main_installed_sha256")
    if not sha or not os.path.isfile(P["main"]) or swr.file_sha256(P["main"]) != sha:
        return None
    backup = P["main_backup"]
    if not os.path.isfile(backup):
        raise ModmanError("A patched main is installed in Savage Wind's game files (by sw_rebuild) and its vanilla "
                          "backup is missing. Set up the game files in Savage Wind again.")
    if swr.nso_header(backup)["build_id"] != swr.nso_header(P["main"])["build_id"]:
        raise ModmanError(f"The vanilla backup {backup} is for another game version. Run: "
                          f'python tools/sw_rebuild.py restore "{install}"')
    return backup


def vanilla_library(install: str) -> str | None:
    """sw_rebuild's backup of the vanilla game code when it installed a rebuilt one in extracted\\ (else None)."""
    P = swr.paths_for(install)
    state = swr.load_state(install)
    sha = state.get("library_installed_sha256")
    if not sha or not os.path.isfile(P["lib"]) or swr.file_sha256(P["lib"]) != sha:
        return None
    if not os.path.isfile(P["lib_vanilla"]) or swr.library_backup_problem(install, state):
        raise ModmanError("The game code in extracted\\ was rebuilt by sw_rebuild, and there is no usable vanilla "
                          f'copy. Run: python tools/sw_rebuild.py restore "{install}" (or set up the game files '
                          "again).")
    return P["lib_vanilla"]


def _stat_key(path: str) -> list:
    try:
        st = os.stat(path)
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return ["missing"]


def game_folders(extracted: str) -> list[str]:
    """The romfs and aocN folders of the game files (lowercase), which deploy links or checks against."""
    try:
        names = os.listdir(extracted)
    except OSError:
        return []
    return sorted(n.lower() for n in names if (n.lower() == "romfs" or AOC_RE.fullmatch(n))
                  and os.path.isdir(os.path.join(extracted, n)))


def inputs_hash(lib: Library) -> str:
    """What a deploy is made from: the library's place (launch.json holds full paths into it), the Savage Wind folder
    and its game files (with the DLC folders set up), the enabled mods in order and their files (names, sizes,
    times), and modhost when a native mod is on. `run` and `status` compare it."""
    h = hashlib.sha256()
    install = lib.install or ""
    enabled = lib.enabled()
    h.update(json.dumps({"v": FORMAT, "install": os.path.normcase(os.path.abspath(install)) if install else "",
                         "library": os.path.normcase(os.path.abspath(lib.root)),
                         "mods": [m["id"] for m in enabled]}).encode())
    ex = os.path.join(install, "extracted")
    h.update(json.dumps(game_folders(ex)).encode())
    for p in (os.path.join(install, EXE), os.path.join(ex, "exefs", "main"), os.path.join(ex, LIB),
              os.path.join(ex, LIB + ".id")):
        h.update(json.dumps(_stat_key(p)).encode())
    rels: dict[str, list[str]] = {}
    for m in enabled:
        root = os.path.join(lib.mods_dir, m["id"])
        h.update(f"\0{m['id']}\0".encode())
        try:
            for path, rel, size in walk_files(root):
                h.update(json.dumps([rel, *_stat_key(path)]).encode())
                rels.setdefault(m["id"], []).append(rel)
        except OSError:
            h.update(b"missing")
    if any("native" in m.get("kinds", []) for m in enabled):
        h.update(json.dumps(_stat_key(os.path.join(MODHOST_DIR, "modhost.dll"))).encode())
    values = deploy_setting_values(lib, rels)  # (only when a mod has applies = "deploy" settings: else as before)
    if values:
        h.update(json.dumps({"deploy_settings": values}, sort_keys=True).encode())
    return h.hexdigest()


def deploy_setting_values(lib: Library, rels: dict[str, list[str]] | None = None) -> dict:
    """The values of the enabled mods' `applies = "deploy"` settings that deploy uses ({mod id: {key: value}}), from
    tools/mod_settings.py's deploy_values(lib, mods) (mods: the enabled library entries). {} without that tool, or
    when no enabled mod has a mod.toml (`rels`: the files of each enabled mod, when known: saves a look)."""
    if feature("mod_settings", "deploy_values") is None:
        return {}
    mods = lib.enabled()
    if rels is not None and not any(_manifest_files(rels.get(m["id"], [])) for m in mods):
        return {}
    res = _feature_call("mod_settings", "deploy_values", lib, mods)
    return {str(k): dict(v) for k, v in (res or {}).items() if isinstance(v, dict) and v} \
        if isinstance(res, dict) else {}


def _with_hooks(key: str, hooks) -> str:
    """A rebuild key that also covers the hook set (a build without hooks keeps its key as before)."""
    if not hooks:
        return key
    addrs = [f"{a:#x}" for a in sorted(set(hooks))]
    return hashlib.sha256(json.dumps({"exefs": key, "hooks": addrs, "format": gh.FORMAT}).encode()).hexdigest()


def rebuild_key(exefs: str, gamelib_id: str | None, hooks=None) -> str:
    """What a rebuilt game code library is made from: every file of the exefs, the game code id, and the hook set."""
    h = hashlib.sha256((gamelib_id or "").encode())
    for n in sorted(os.listdir(exefs)):
        p = os.path.join(exefs, n)
        if os.path.isfile(p):
            h.update(f"{n}:{swr.file_sha256(p)};".encode())
    return _with_hooks(h.hexdigest(), hooks)


def planned_rebuild_key(rep: ep.Report, exefs: str, main_vanilla: str | None, gamelib_id: str | None,
                        hooks=None) -> str:
    """rebuild_key of the patched exefs deploy would write, worked out in memory for a dry run: the same patches
    applied the same way as exefs_patch.apply, without writing a file."""
    h = hashlib.sha256((gamelib_id or "").encode())
    for n in sorted(os.listdir(exefs)):
        p = os.path.join(exefs, n)
        if not os.path.isfile(p):
            continue
        src = main_vanilla if n == "main" and main_vanilla else p
        if n in rep.targets:
            module = nsolib.load(src)
            for patch in rep.targets[n]:
                if module.read(patch.offset, len(patch.data)) != patch.data:
                    module.write(patch.offset, patch.data)
            digest = hashlib.sha256(module.to_bytes()).hexdigest()
        else:
            digest = swr.file_sha256(src)
        h.update(f"{n}:{digest};".encode())
    return _with_hooks(h.hexdigest(), hooks)


def reusable_rebuild(old: dict | None, out_lib: str, key: str) -> dict | None:
    """The last deploy's rebuild record when its game code was built from exactly this patched exefs and is still
    there unchanged (else None)."""
    rb = (old or {}).get("rebuild") or {}
    files = (old or {}).get("files") or []
    if (rb.get("key") == key and LIB in files and _stat_key(out_lib) == rb.get("stat")
            and os.path.isfile(out_lib + ".id")):
        return rb
    return None


def _safe_rel(rel) -> bool:
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or ":" in rel or "\\" in rel:
        return False
    return all(p not in ("", ".", "..") for p in rel.split("/"))


class DeployDir:
    """library\\deploy\\. Every file and junction modman puts there is listed in its marker file before it is made,
    and a later deploy deletes only those (junctions as links, never what they point to)."""

    def __init__(self, path: str):
        self.path = path
        self.old: dict | None = None
        self.files: set[str] = set()
        self.links: set[str] = set()
        self.info: dict = {}

    def abs(self, rel: str) -> str:
        return os.path.join(self.path, *rel.split("/"))

    @property
    def marker(self) -> str:
        return os.path.join(self.path, MARKER)

    def read_launch(self) -> dict | None:
        try:
            data = read_json(os.path.join(self.path, LAUNCH))
        except ModmanError:
            return None
        return data if data and data.get("tool") == TOOL_ID else None

    def claim(self) -> None:
        """Check the folder is modman's (or new, or empty) and holds nothing else where modman writes. Reads only."""
        if not os.path.lexists(self.path):
            return
        if _is_link(self.path) or not os.path.isdir(self.path):
            raise ModmanError(f"{self.path} isn't a plain folder: move it away, then deploy again.")
        old = read_json(self.marker)
        if old is None:
            if [n for n in os.listdir(self.path) if n != HISTORY]:  # (history.jsonl stays across deploys)
                raise ModmanError(f"{self.path} isn't empty and modman didn't make it. Move what is in it, then "
                                  "deploy again.")
            return
        if old.get("tool") != TOOL_ID:
            raise ModmanError(f"{self.marker} wasn't written by modman.")
        self.old = old
        files = {r for r in old.get("files", []) if _safe_rel(r)}
        links = {r for r in old.get("links", []) if _safe_rel(r)}
        foreign = []
        for top in OWN_TOP:
            foreign += self._foreign(top, files, links)
        if foreign:
            raise ModmanError(f"{self.path} has files modman didn't put there: {_some(foreign)}. Move them out of "
                              "the folder, then deploy again.")

    def _foreign(self, rel: str, files: set[str], links: set[str]) -> list[str]:
        p = self.abs(rel)
        if not os.path.lexists(p):
            return []
        if _is_link(p):
            return [] if rel in links else [rel]
        if not os.path.isdir(p):
            return [] if rel in files else [rel]
        out = []
        with os.scandir(p) as it:
            for e in it:
                out += self._foreign(f"{rel}/{e.name}", files, links)
        return out

    def clear(self, keep: set[str] = frozenset()) -> None:
        """Delete what the last deploy wrote (except `keep`), then the folders that leaves empty."""
        old = self.old or {}
        files = {r for r in old.get("files", []) if _safe_rel(r)}
        links = {r for r in old.get("links", []) if _safe_rel(r)}
        self.files, self.links = set(files), set(links)
        self.save()  # (complete: false while deleting; everything still listed if this is cut short)
        self.files = {r for r in files if r in keep and os.path.isfile(self.abs(r)) and not _is_link(self.abs(r))}
        self.info = {k: v for k, v in old.items() if k == "rebuild"} if self.files else {}
        self.links = set()
        dirs: set[str] = set()
        for rel in links:
            p = self.abs(rel)
            if _is_link(p):
                _remove_link(p)
            dirs.update(rel.rsplit("/", k)[0] for k in range(1, rel.count("/") + 1))
        for rel in files - self.files:
            p = self.abs(rel)
            if _is_link(p):
                _remove_link(p)
            elif os.path.isfile(p):
                _unlink(p)
            dirs.update(rel.rsplit("/", k)[0] for k in range(1, rel.count("/") + 1))
        for d in sorted(dirs, key=lambda x: -x.count("/")):
            try:
                os.rmdir(self.abs(d))
            except OSError:
                pass  # (gone, or not empty)
        self.save()

    def add_file(self, rel: str) -> str:
        self.files.add(rel)
        self.save()
        p = self.abs(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def drop_file(self, rel: str) -> None:
        p = self.abs(rel)
        if rel in self.files and os.path.isfile(p) and not _is_link(p):
            _unlink(p)
        self.files.discard(rel)
        self.save()

    def add_link(self, rel: str, target: str) -> None:
        self.links.add(rel)
        self.save()
        make_junction(self.abs(rel), target)

    def save(self, complete: bool = False) -> None:
        os.makedirs(self.path, exist_ok=True)
        write_json(self.marker, {"tool": TOOL_ID, "format": FORMAT, "complete": complete, **self.info,
                                 "files": sorted(self.files), "links": sorted(self.links)})


def unshare_read_only(folder: str, tmp: str) -> int:
    """romfs_mods clears read-only on an overlay file it deletes. An overlay file is a hard link to a library file,
    which can be a hard link to the player's own mod file: clearing it there would change the player's file. So
    before romfs_mods runs, each read-only overlay file with other names becomes a private copy (same bytes), whose
    setting is its own. `tmp` is a scratch name on the same drive. Returns how many files were copied."""
    if not os.path.isdir(folder) or _is_link(folder):
        return 0
    ro: set[str] = set()
    files = walk_files(folder, ro)
    n = 0
    for path, rel, _ in files:
        if rel not in ro or os.lstat(path).st_nlink <= 1:
            continue
        if os.path.lexists(tmp):
            _unlink(tmp)
        shutil.copyfile(path, tmp)
        _unlink(path)  # (this name only; the player's file keeps its setting)
        os.replace(tmp, path)
        n += 1
    return n


def history_path(lib: Library) -> str:
    return os.path.join(lib.deploy_dir, HISTORY)


def history_append(lib: Library, record: dict) -> None:
    """Add one record to deploy\\history.jsonl (docs/profiles.md, "History"): {"type": "deploy" | "start" |
    "result", "time": ..., ...}, one JSON object a line. The file isn't in deploy's marker, so deploys keep it; past
    2 x HISTORY_KEEP lines only the newest HISTORY_KEEP stay."""
    path = history_path(lib)
    os.makedirs(lib.deploy_dir, exist_ok=True)
    line = json.dumps({"time": _now(), **record}, ensure_ascii=False, separators=(",", ":"))
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")
    try:
        if os.path.getsize(path) > 4096 * HISTORY_KEEP:  # (cheap test first: read it only when it may be long)
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            if len(lines) > 2 * HISTORY_KEEP:
                tmp = f"{path}.tmp-{os.getpid()}"
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    f.write("\n".join(lines[-HISTORY_KEEP:]) + "\n")
                os.replace(tmp, path)
    except OSError:
        pass  # (trimming can wait for the next record)


def read_history(lib: Library) -> list[dict]:
    """deploy\\history.jsonl's records, oldest first. Lines that aren't JSON objects are skipped."""
    out = []
    try:
        with open(history_path(lib), encoding="utf-8") as f:
            for ln in f:
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("type"), str):
                    out.append(rec)
    except OSError:
        return []
    return out


def _drop_overlay(path: str) -> None:
    """Remove an overlay folder romfs_mods made (it refuses one holding anything else)."""
    if not os.path.lexists(path):
        return
    man = rm._owned(path)
    rm._remove_folder(path, rm._listed(man))


def _mod_of(path: str, lib: Library, names: dict[str, str]) -> str:
    try:
        first = os.path.relpath(path, lib.mods_dir).split(os.sep)[0]
    except ValueError:
        return "?"
    return names.get(first, first)


def manifest_refusals(lib: Library, scans: list[tuple[dict, str, ModScan]]) -> list[str]:
    """Why the enabled mods with a mod.toml can't be deployed: a mod.toml that doesn't match the mod or the game
    (game_versions, loader_api, hooks), or dependencies that aren't in the library, are turned off or have the wrong
    version. Empty when they all can."""
    out = [f"{m['name']}: {e}" for m, _, s in scans for e in s.manifest_errors]
    if not any(s.manifest and s.manifest.dependencies for _, _, s in scans):
        return out
    found = library_ids(lib, scans)  # (the mods that are off too, by their mod.toml id)
    for m, _, s in scans:
        for dep, req in (s.manifest.dependencies.items() if s.manifest else ()):
            entry, version = found.get(dep, (None, None))
            why = dependency_problem(dep, req, entry, version)
            if why:
                out.append(f"{m['name']} {why}")
    return out


def collect_hooks(scans: list[tuple[dict, str, ModScan]], native: dict) -> dict[int, list[str]]:
    """The hook addresses the enabled native mods' mod.toml files ask for -> the names of the mods asking."""
    deployed = {os.path.normcase(p) for _, p, _ in native.values()}
    out: dict[int, list[str]] = {}
    for m, _, s in scans:
        if s.manifest and s.manifest.hooks and any(os.path.normcase(p) in deployed for p in s.dlls):
            for a in s.manifest.hooks:
                out.setdefault(a, []).append(m["name"])
    return dict(sorted(out.items()))


def hooks_text(hooks: dict[int, list[str]]) -> str:
    """The combined hooks list for sw_rebuild --hooks (tools/gen_hooks.py's text format)."""
    lines = ["# Hook slots for the enabled native mods. Made by modman; deploy again to remake it.",
             "# Rebuilt by hand: python tools/sw_rebuild.py build --hooks <this file> (see docs/hooks.md)"]
    # (names on one line whatever they hold: gen_hooks reads the file with splitlines, which also breaks at U+2028
    # and the like, and text after such a break would be read as another hook address)
    lines += [f"{a:#x}   # {one_line(', '.join(names))}" for a, names in hooks.items()]
    return "\n".join(lines) + "\n"


def deploy(lib: Library, *, no_rebuild: bool = False, dry_run: bool = False) -> dict:
    """Build library\\deploy\\ from the enabled mods, in order (later mods win). Returns launch.json's contents
    (with "dry_run": true and nothing written for a dry run)."""
    install = lib.require_install()
    if not dry_run and game_running():
        raise ModmanError("Savage Wind is running. Close it, then deploy again.")
    game = game_info(install)
    extracted = os.path.join(install, "extracted")
    exefs_dir = os.path.join(extracted, "exefs")
    names = {m["id"]: m["name"] for m in lib.mods}
    attention: list[str] = []
    notes: list[str] = []
    scans: list[tuple[dict, str, ModScan]] = []
    for m in lib.enabled():
        root = os.path.join(lib.mods_dir, m["id"])
        if not os.path.isdir(root):
            attention.append(f"{m['name']}: its folder is missing from the library; remove it and add it again")
            continue
        s = scan_mod(root, game, m["name"])
        attention += [f"{m['name']}: {p}"
                      for p in s.problems + bnp.stored_problems(root) + ukmm.stored_problems(root)]
        left = (m.get("wiiu") or {}).get("wiiu_data")
        if left:
            attention.append(f"{m['name']}: {left} file(s) are still Wii U data (converted from Wii U; see "
                             f"{wc.REPORT} in mods\\{m['id']}): the game may crash or look wrong where the mod uses "
                             "them")
        warned = (m.get("wiiu") or {}).get("attention")
        if warned:
            attention.append(f"{m['name']}: {warned} model/texture file(s) were converted from Wii U with warnings "
                             f"(see \"Needs attention\" in {wc.REPORT} in mods\\{m['id']}): check them in game")
        scans.append((m, root, s))
    refuse = manifest_refusals(lib, scans)
    if refuse:
        raise ModmanError("Can't deploy (fix it, or turn the mod off with python tools/modman.py disable <id>):\n"
                          + "\n".join(f"  - {r}" for r in refuse))
    h = inputs_hash(lib)
    d = DeployDir(lib.deploy_dir)
    old_launch = d.read_launch()
    d.claim()
    overlay = os.path.join(d.path, "overlay")
    try:
        rm._owned(overlay)  # (romfs_mods refuses an overlay holding files it didn't put there: ask before any write)
    except rm.DeployError as e:
        raise ModmanError(f"RomFS overlay: {e}") from None

    # --- what each part will be (reads only) ---
    romfs_paths = [root for _, root, s in scans if s.romfs]
    # (each mod's chosen option folders, right after its own RomFS files: tools/mod_settings.py option_romfs)
    sources = romfs_sources(scans, option_layers(lib, scans, attention))
    if sources and not os.path.isdir(os.path.join(extracted, "romfs")):
        raise ModmanError("Savage Wind's game files aren't set up (no extracted\\romfs). Set them up in Savage Wind.")
    deploy_values = deploy_setting_values(lib)

    patch_files = [p for _, _, s in scans for p in s.patches]
    rep = None
    if patch_files:
        if not os.path.isfile(os.path.join(exefs_dir, "main")):
            raise ModmanError("Savage Wind's game files aren't set up (no extracted\\exefs\\main).")
        try:
            rep = ep.check(exefs_dir, patch_files)
        except (ep.PatchError, OSError) as e:
            raise ModmanError(f"ExeFS patches: {e}") from None
        if rep.errors:
            raise ModmanError("ExeFS patches can't be applied:\n" + "\n".join(f"  - {e}" for e in rep.errors))
        if rep.conflicts:
            lines = []
            for a, b in rep.conflicts[:10]:
                where = next((mod for mod, ps in rep.targets.items() if a in ps), "a module")
                lines.append(f"  - {_mod_of(a.source, lib, names)} ({a.label()}) and {_mod_of(b.source, lib, names)} "
                             f"({b.label()}) change {where} at 0x{max(a.offset, b.offset):x} differently")
            raise ModmanError("ExeFS patches clash; turn one of each pair off, then deploy again:\n" + "\n".join(lines))
        by_file: dict[str, int] = {}
        for p in rep.wrong_version:
            by_file[p.source] = by_file.get(p.source, 0) + 1
        for src, n in by_file.items():
            notes.append(f"{_mod_of(src, lib, names)}: skipped {n} patch(es) in {os.path.basename(src)} made for "
                         "another game version")
        for p in rep.header_writes:
            attention.append(f"{_mod_of(p.source, lib, names)}: {p.label()} writes into the NSO header; skipped")
        if not rep.targets:
            rep = None
    code_patches = bool(rep and rep.needs_rebuild)

    cheat_items = [(m["name"], p) for m, _, s in scans for p in s.cheats]
    merged = merge_cheats(cheat_items) if cheat_items else None
    if merged:
        attention += merged.attention
        notes += merged.notes
        if merged.enable is not None and not merged.enable:
            notes.append("No cheat is turned on in the mods' toggles.txt files: cheats won't run.")
        if not merged.count:
            merged = None

    # Native mods: their mod DLLs and helper DLLs, one folder for all (later mods win a name)
    host = os.path.join(MODHOST_DIR, "modhost.dll")
    native: dict[str, tuple[dict, str, bool]] = {}   # lowercase file name -> (mod, file, is a helper)
    if any(s.dlls for _, _, s in scans) and not os.path.isfile(host):
        attention.append("Native mods need modhost.dll, which isn't built yet: run python tools/build_native.py "
                         "build, then deploy again. Native mods are left out until then.")
    elif any(s.dlls for _, _, s in scans):
        if not os.path.isfile(os.path.join(extracted, LIB + ".id")):
            raise ModmanError("Savage Wind's game files aren't set up (no extracted\\swgame.dll.id).")
        for m, _, s in scans:
            for p in s.dlls + s.helpers:
                n = os.path.basename(p).lower()
                prev = native.get(n)
                if prev and not _same_bytes(prev[1], p):
                    attention.append(f"{prev[0]['name']} and {m['name']} both have {os.path.basename(p)}: "
                                     f"{m['name']}'s is used")
                native[n] = (m, p, p in s.helpers)
        if len(native) > MAX_NATIVE:
            attention.append(f"{len(native)} native mod and helper DLLs, but modhost loads at most {MAX_NATIVE}: "
                             "the ones last in name order are left out. Turn some native mods off.")

    # Hooks: the union of what the deployed native mods' mod.toml files ask for, built into the game code
    hooks = collect_hooks(scans, native)
    hook_addrs = list(hooks)
    if len(hooks) > gh.MAX_HOOKS:
        raise ModmanError(f"The enabled mods ask for {len(hooks)} hooks, and the game code can have at most "
                          f"{gh.MAX_HOOKS}. Turn some hook mods off.")
    if hooks and not rep and not os.path.isfile(os.path.join(exefs_dir, "main")):
        raise ModmanError("Savage Wind's game files aren't set up (no extracted\\exefs\\main, which the hook slots "
                          "are built from).")
    need_rebuild = code_patches or bool(hooks)

    exe = os.path.join(install, EXE)
    lib_vanilla = vanilla_library(install) if rep else None
    main_vanilla = vanilla_main(install) if rep else None
    if main_vanilla:
        notes.append("A patched main is installed in the game files (sw_rebuild): the ExeFS patches go on the "
                     "vanilla copy instead.")
    root_dir = os.path.join(d.path, "root")
    out_lib = os.path.join(d.path, LIB)
    gamelib_id = _read_id(os.path.join(extracted, LIB + ".id"))

    # --- everything that can still refuse the deploy, before anything is deleted ---
    gindex = None
    pre = None
    rstb = None
    if romfs_paths:  # (BCML packages: one size table for every mod that changes it; packs a later mod replaces)
        hook = bnp.deploy_hook([(m["name"], root) for m, root, s in scans if s.romfs], extracted)
        attention += hook.attention
        notes += hook.notes
        rstb = hook.rstb
    if sources:
        try:  # (romfs_mods' plan against the game: what its real deploy would refuse, it refuses here)
            gindex = rm.GameIndex.scan(extracted)
            pre = rm.deploy(sources, overlay, gindex, dry_run=True, rstb=rstb)
        except (rm.DeployError, zipfile.BadZipFile) as e:
            raise ModmanError(f"RomFS overlay: {e}") from None
    makers = [m for m in GENERATED_OWNER if feature(m, "deploy_files")]  # (fonts, title picture, pictures)
    if rep:
        bad = []
        for mod, ps in rep.targets.items():
            src = main_vanilla if mod == "main" and main_vanilla else os.path.join(exefs_dir, mod)
            try:
                layout = nsolib.read_layout(src)
            except (nsolib.NsoError, OSError) as e:
                bad.append(f"{mod}: {e}")
                continue
            bad += [f"{_mod_of(p.source, lib, names)}: {p.label()} {why}" for p in ps
                    if (why := outside_module(p, mod, layout))]
        if bad:
            raise ModmanError("ExeFS patches can't be applied (turn the mod off, then deploy again):\n"
                              + "\n".join(f"  - {e}" for e in bad))

    if dry_run:
        rebuild = "none"
        game_lib = None
        if rep:
            game_lib = os.path.join(d.path, "vanilla-lib", LIB) if lib_vanilla else os.path.join(extracted, LIB)
        if need_rebuild:
            kept = None
            if (d.old or {}).get("rebuild") and os.path.isfile(out_lib):
                try:  # (the patched exefs worked out in memory, to tell a reuse from a rebuild)
                    key = (planned_rebuild_key(rep, exefs_dir, main_vanilla, gamelib_id, hook_addrs) if rep
                           else rebuild_key(exefs_dir, gamelib_id, hook_addrs))
                except (nsolib.NsoError, OSError) as e:
                    raise ModmanError(f"{'ExeFS patches' if rep else 'The game code files'}: {e}") from None
                kept = reusable_rebuild(d.old, out_lib, key)
            rebuild = "reuse" if kept else "pending" if no_rebuild else "build"
            if rebuild != "pending":
                game_lib = out_lib
        launch = _launch(lib, h, exe, pre, rep, root_dir, game_lib, rebuild if code_patches else "none", native, d,
                         merged, game, attention, notes, patch_files, hooks=hooks,
                         hook_rebuild=rebuild, hooks_file=d.abs(HOOKS_FILE), settings=deploy_values)
        launch["dry_run"] = True
        return launch

    # --- writes ---
    for folder in (overlay, overlay + rm.STAGE, overlay + rm.ASIDE):
        unshare_read_only(folder, os.path.join(d.path, ".modman-unshare.tmp"))
    d.clear(keep={LIB, LIB + ".id"})
    man = None
    p = None
    made: dict = {}
    try:
        if sources or makers:  # (the plan, then the files made at deploy over it: fonts, title picture, pictures)
            if gindex is None and os.path.isdir(os.path.join(extracted, "romfs")):
                gindex = rm.GameIndex.scan(extracted)
            p = rm.plan(sources, gindex, rstb)
            for rel, data in run_makers(lib, p, scans, makers, attention, notes, made):
                with open(d.add_file(rel), "wb") as f:
                    f.write(data)
        if p is not None and p.winners:
            man = rm.deploy(sources, overlay, gindex, rstb=rstb, plan_=p)
        else:
            _drop_overlay(overlay)
    except (rm.DeployError, zipfile.BadZipFile) as e:
        raise ModmanError(f"RomFS overlay: {e}") from None
    finally:
        for m in (p.mods if p is not None else ()):
            if m.zip:
                m.zip.close()

    game_lib = None
    rebuild = "none"
    rebuild_src = exefs_dir  # (what the game runs: the game's exefs, or deploy\root\exefs with the patches)
    if rep:
        src = exefs_dir
        if main_vanilla:
            src = d.abs(".work/vanilla-exefs")
            for n in sorted(os.listdir(exefs_dir)):
                if os.path.isfile(os.path.join(exefs_dir, n)):
                    link_or_copy(main_vanilla if n == "main" else os.path.join(exefs_dir, n),
                                 d.add_file(f".work/vanilla-exefs/{n}"))
        out_exefs = os.path.join(root_dir, "exefs")
        if os.path.isdir(out_exefs) and os.listdir(out_exefs):  # (cleared above; never write over a leftover file)
            raise ModmanError(f"{out_exefs} isn't empty: move what is in it, then deploy again.")
        for n in sorted(os.listdir(src)):
            if os.path.isfile(os.path.join(src, n)):
                d.add_file(f"root/exefs/{n}")
        try:
            rep2, _ = ep.apply(src, out_exefs, patch_files)
        except (ep.PatchError, OSError) as e:
            raise ModmanError(f"ExeFS patches: {e}") from None
        if not rep2.ok:
            raise ModmanError("ExeFS patches can't be applied:\n" + "\n".join(f"  - {e}" for e in rep2.errors))
        for rel in [f for f in d.files if f.startswith(".work/")]:
            d.drop_file(rel)
        for rel in (".work/vanilla-exefs", ".work"):
            try:
                os.rmdir(d.abs(rel))
            except OSError:
                pass
        d.add_link("root/romfs", os.path.join(extracted, "romfs"))
        for n in sorted(os.listdir(extracted)):
            if AOC_RE.fullmatch(n) and os.path.isdir(os.path.join(extracted, n)):
                d.add_link(f"root/{n}", os.path.join(extracted, n))
        game_lib = os.path.join(extracted, LIB)
        if lib_vanilla:
            game_lib = d.add_file(f"vanilla-lib/{LIB}")
            link_or_copy(lib_vanilla, game_lib)
            if os.path.isfile(lib_vanilla + ".id"):
                link_or_copy(lib_vanilla + ".id", d.add_file(f"vanilla-lib/{LIB}.id"))
            notes.append("The game code in extracted\\ was rebuilt by sw_rebuild: using its vanilla copy.")
        rebuild_src = out_exefs
    hooks_file = None
    if hooks:
        hooks_file = d.add_file(HOOKS_FILE)
        with open(hooks_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(hooks_text(hooks))
    if need_rebuild:
        # (kept while the exefs it was built from, the game code id and the hook set are all the same)
        key = rebuild_key(rebuild_src, gamelib_id, hook_addrs)
        old_rb = reusable_rebuild(d.old, out_lib, key) if LIB in d.files else None
        if old_rb:
            rebuild, game_lib = "reuse", out_lib
            d.info["rebuild"] = old_rb
        elif no_rebuild:
            rebuild = "pending"
        else:
            d.drop_file(LIB)
            d.drop_file(LIB + ".id")
            d.info.pop("rebuild", None)
            d.add_file(LIB)
            d.add_file(LIB + ".id")
            what = " and ".join(x for x in ("the code patches" if code_patches else "",
                                            f"hook slots for {len(hooks)} function(s)" if hooks else "") if x)
            if hooks:
                aside = (" (Skip what sw_rebuild says about " + ("extracted\\exefs and " if rep else "")
                         + "starting the game: modman run does that.)")
            else:
                aside = (" (sw_rebuild's note about extracted\\exefs doesn't apply: modman starts the game from "
                         "deploy\\root.)" if rep else "")
            say(f"Rebuilding the game code with {what}. This takes {REBUILD_TIME}.{aside}")
            rc = run_rebuild(install, rebuild_src, out_lib, hooks_file) if hooks_file \
                else run_rebuild(install, rebuild_src, out_lib)
            if rc != 0 or not os.path.isfile(out_lib):
                left = " and ".join(x for x in ("the code patches" if code_patches else "",
                                                "the hooks" if hooks else "") if x)
                raise ModmanError("The game code rebuild failed (see above). Deploy with --no-rebuild to play "
                                  f"with everything except {left}.")
            d.info["rebuild"] = {"key": key, "stat": _stat_key(out_lib), "hooks": [f"{a:#x}" for a in hook_addrs]}
            rebuild, game_lib = "build", out_lib
    if rebuild not in ("reuse", "build"):
        d.drop_file(LIB)
        d.drop_file(LIB + ".id")
        d.info.pop("rebuild", None)

    if native:
        real = game_lib or os.path.join(extracted, LIB)
        shutil.copyfile(host, d.add_file("native/modhost.dll"))
        id_src = real + ".id" if os.path.isfile(real + ".id") else os.path.join(extracted, LIB + ".id")
        shutil.copyfile(id_src, d.add_file("native/modhost.dll.id"))
        for _, p, _ in native.values():
            shutil.copyfile(p, d.add_file(f"native/mods/{os.path.basename(p)}"))

    if merged:
        with open(d.add_file(f"cheats/{game.main_bid16}.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(merged.text)

    if native:  # (the settings pages: read by modhost at game start; mods: the enabled library entries)
        on = [m for m, _, _ in scans]
        if feature("mod_settings", "write_settings_txt"):  # (it writes deploy\native\settings.txt through d)
            _feature_call("mod_settings", "write_settings_txt", lib, on, d, problems=attention, what="Mod settings")
        elif feature("mod_settings", "settings_txt"):
            txt = _feature_call("mod_settings", "settings_txt", lib, on, problems=attention, what="Mod settings")
            if txt:
                with open(d.add_file("native/settings.txt"), "w", encoding="utf-8", newline="\n") as f:
                    f.write(txt)

    launch = _launch(lib, h, exe, man, rep, root_dir, game_lib, rebuild if code_patches else "none", native, d,
                     merged, game, attention, notes, patch_files, hooks=hooks, hook_rebuild=rebuild,
                     hooks_file=hooks_file, settings=deploy_values)
    if made:  # (what the makers say about what they made: font.py's symbols_full and font name, for modmenu.sync)
        launch["made"] = made
    write_json(d.add_file(LAUNCH), launch)
    d.save(complete=True)
    history_append(lib, {"type": "deploy", "hash": h, "profile": lib.profile,
                         "mods": [{"id": m["id"], "name": m["name"], "enabled": bool(m.get("enabled")),
                                   "kinds": m.get("kinds", []),
                                   **({"version": m["version"]} if m.get("version") else {})} for m in lib.mods],
                         "settings": deploy_values})
    launch["previous"] = old_launch
    return launch


def option_layers(lib: Library, scans: list[tuple[dict, str, ModScan]], attention: list[str]
                  ) -> list[tuple[str, str, bool]]:
    """The option folders the enabled mods' `applies = "deploy"` settings chose, as (mod id, folder, dlc), from
    tools/mod_settings.py's option_romfs(lib, mods): mods are the enabled library entries in deploy order, and it
    returns (entry or mod id, folder) or (entry or mod id, folder, dlc) items, in order. A folder outside its mod's
    library folder is left out."""
    if not any(s.manifest and s.manifest.settings for _, _, s in scans) \
            or feature("mod_settings", "option_romfs") is None:
        return []
    roots = {m["id"]: root for m, root, _ in scans}
    out = []
    for item in _feature_call("mod_settings", "option_romfs", lib, [m for m, _, _ in scans], problems=attention,
                              what="Mod settings option folders") or []:
        try:
            who, folder, *rest = item
            mod_id = who["id"] if isinstance(who, dict) else str(who)
            folder = str(folder)
        except (TypeError, ValueError, KeyError):
            attention.append(f"Mod settings: an option folder came as {clip(repr(item))}: left out")
            continue
        root = roots.get(mod_id)
        if not root or not _inside(folder, root) or not os.path.isdir(folder) or _is_link(folder):
            attention.append(f"{mod_id}: option folder {folder} isn't a folder inside the mod: left out")
            continue
        out.append((mod_id, os.path.abspath(folder), bool(rest and rest[0])))
    return out


def romfs_sources(scans: list[tuple[dict, str, ModScan]], layers: list[tuple[str, str, bool]]) -> list:
    """What romfs_mods deploys, in order: each mod's own RomFS folder, then its option folders (rm.Layer)."""
    out: list = []
    for m, root, s in scans:
        if s.romfs:
            out.append(root)
        out += [rm.Layer(folder, os.path.basename(root), dlc) for mod_id, folder, dlc in layers if mod_id == m["id"]]
    return out


def title_picture_choice(lib: Library, scans: list[tuple[dict, str, ModScan]]) -> str | None:
    """The title-screen picture: the player's choice (<library>\\pictures\\_title\\chosen.*), else the last enabled
    mod's [mod] title_picture. None: the game's own."""
    chosen = picture_choice(lib, TITLE_PICK)
    if chosen:
        return chosen
    for m, root, s in reversed(scans):
        if s.manifest and s.manifest.title_picture:
            return os.path.join(root, *s.manifest.rel(s.manifest.title_picture).split("/"))
    return None


DEPLOY_FILE_TOPS = ("pictures", "fonts")   # where a maker's files for deploy\ may go (deploy\pictures\atlas.json)


def run_makers(lib: Library, plan: "rm.Plan", scans: list[tuple[dict, str, ModScan]], makers: list[str],
               attention: list[str], notes: list[str], made: dict | None = None) -> list[tuple[str, bytes]]:
    """Run each maker's deploy_files over the plan, in order: tools/font.py (lib, plan), tools/menu_bg.py (lib, plan,
    title picture) and tools/mod_pictures.py (lib, plan) last, so it builds on the final winning files. Each returns
    a list of (RomFS path, bytes, size-table entry or None) items, which go into the overlay as "generated:<what>"
    files (romfs_mods.add_generated; the entry is (name in the size table, size)); ("deploy/<pictures or
    fonts>/...", bytes, None) for a file in deploy\\; and strings, which are notes. The list may carry `notes` and
    `attention` lists, and other plain values (font.py: symbols_full, font), which go into `made`["<what>"] and
    launch.json's "made" (modmenu.sync reads them). A maker that fails is off for this deploy (one line under Needs
    attention). Returns the deploy\\ files as (path in deploy\\, bytes)."""
    out: list[tuple[str, bytes]] = []
    for module in makers:
        owner = GENERATED_OWNER[module]
        what = owner.split(":")[1].capitalize()
        args = (lib, plan, title_picture_choice(lib, scans)) if module == "menu_bg" else (lib, plan)
        res = _feature_call(module, "deploy_files", *args, problems=attention, what=what)
        extra = dict(getattr(res, "__dict__", None) or {})
        notes += [f"{what}: {n}" for n in extra.pop("notes", None) or []]
        attention += [f"{what}: {a}" for a in extra.pop("attention", None) or []]
        if made is not None and extra:
            made[owner.split(":")[1]] = {k: v for k, v in extra.items()
                                         if isinstance(v, (str, int, float, bool)) or v is None}
        files = []
        for item in res or []:
            if isinstance(item, str):
                notes.append(f"{what}: {item}")
                continue
            try:
                path, data, size = item
            except (TypeError, ValueError):
                attention.append(f"{what}: gave something that isn't (path, bytes, size): left out")
                continue
            rel = str(path).replace("\\", "/")
            if rel.startswith("deploy/"):
                rel = rel[len("deploy/"):]
                if not _safe_rel(rel) or rel.split("/")[0] not in DEPLOY_FILE_TOPS or len(rel.split("/")) < 2 \
                        or not isinstance(data, (bytes, bytearray)):
                    attention.append(f"{what}: deploy\\{rel} isn't a place in deploy\\pictures or deploy\\fonts: "
                                     "left out")
                    continue
                out.append((rel, bytes(data)))
            else:
                files.append((path, data, size))
        if files:
            try:
                rm.add_generated(plan, files, owner)
            except ValueError as e:
                attention.append(f"{what}: off this time ({e})")
    return out


def _launch(lib, h, exe, man, rep, root_dir, game_lib, rebuild, native, d, merged, game, attention, notes,
            patch_files, *, hooks=None, hook_rebuild="none", hooks_file=None, settings=None) -> dict:
    """launch.json: the exact command line and environment for `run`, and what is deployed. `rebuild` is the state
    of the game code for the code patches, `hook_rebuild` for the hooks (the same library holds both). `settings`:
    the `applies = "deploy"` setting values used ({mod id: {key: value}})."""
    install = lib.install
    env: dict[str, str] = {}
    argv = [exe]
    parts: dict = {}
    if man:
        env.update(man["env"])
        g = man.get("game_check") or {}
        made = man.get("generated") or {}
        parts["overlay"] = {"path": man["overlay"], "files": len(man["files"]),
                            "mods": len([m for m in man["mods"] if not m["name"].startswith(rm.GENERATED)]),
                            "replaced": g.get("replaced"), "new": g.get("new")}
        if made:  # (files made at deploy: fonts, the title picture, pictures)
            parts["overlay"]["generated"] = {k: len(v) for k, v in made.items()}
        attention += man["warnings"]
        conflicts = man["conflicts"]
        if conflicts:
            attention.append(f"{len(conflicts)} RomFS file(s) are in several mods; the later mod wins: "
                             + _some([f"{c['path']} ({' -> '.join(c['mods'])})" for c in conflicts], 3))
        notes += [n for n in man["notes"] if not n.startswith("Paths not checked")]
        for m in man["mods"]:
            notes += [f"{m['name']}: {n}" for n in m["notes"] if "not deployed by this tool" not in n]
    if rep:
        argv = [exe, root_dir, "--play"]
        code = sum(rep.code_patches.values())
        parts["exefs"] = {"root": root_dir, "files": len(patch_files), "modules": sorted(rep.targets),
                          "patches": sum(len(ps) for ps in rep.targets.values()), "code_patches": code,
                          "rebuild": rebuild, "library": game_lib}
        if rebuild == "pending":
            attention.append(f"{code} ExeFS patch(es) change game code and do nothing until the game code is rebuilt: "
                             f"deploy without --no-rebuild (takes {REBUILD_TIME}).")
    if native:
        host = d.abs("native/modhost.dll")
        env["SAVAGEWIND_GAME_LIB"] = host
        env["SWMOD_REAL_GAME_LIB"] = game_lib or os.path.join(install, "extracted", LIB)
        env["SWMOD_DIR"] = d.abs("native/mods")
        parts["native"] = {"host": host,
                           "mods": sorted(os.path.basename(p) for _, p, helper in native.values() if not helper),
                           "helpers": sorted(os.path.basename(p) for _, p, helper in native.values() if helper),
                           "real_lib": env["SWMOD_REAL_GAME_LIB"]}
    elif game_lib:
        env["SAVAGEWIND_GAME_LIB"] = game_lib
    if hooks:
        mods = list(dict.fromkeys(n for names in hooks.values() for n in names))
        parts["hooks"] = {"file": hooks_file, "count": len(hooks), "mods": mods,
                          "by_addr": {f"{a:#x}": names for a, names in hooks.items()},
                          "rebuild": hook_rebuild, "library": game_lib}
        if hook_rebuild == "pending":
            attention.append(f"The hooks of {', '.join(mods)} do nothing: the game code wasn't rebuilt with hook "
                             "slots (--no-rebuild). The mods still load, and modhost logs 'this game code has no hook "
                             f"slots'. Deploy without --no-rebuild to rebuild it (takes {REBUILD_TIME}).")
    cheats = None
    if merged:
        # (one --enable=NAME per cheat: argparse takes it literally, even a name that starts with '-')
        args = ["--all"] if merged.enable is None else [f"--enable={n}" for n in merged.enable]
        if args:
            env["SAVAGEWIND_CONTROL"] = str(DEFAULT_PORT)
        cheats = {"file": d.abs(f"cheats/{game.main_bid16}.txt"), "count": merged.count, "mods": merged.mods,
                  "args": args, "main": os.path.join(install, "extracted", "exefs", "main")}
    return {"tool": TOOL_ID, "format": FORMAT, "complete": True, "deployed": _now(), "hash": h,
            "install": install, "cwd": install, "argv": argv, "env": env, "clear_env": list(MOD_ENV),
            "mods": [{"id": m["id"], "name": m["name"], "kinds": m.get("kinds", []),
                      **({"version": m["version"]} if m.get("version") else {})} for m in lib.enabled()],
            "parts": parts, "cheats": cheats, "attention": list(dict.fromkeys(attention)),
            "notes": list(dict.fromkeys(notes)),
            **({"settings": settings} if settings else {}),
            **({"profile": lib.profile} if lib.profile != DEFAULT_PROFILE else {})}


def describe_parts(launch: dict) -> list[str]:
    p = launch.get("parts") or {}
    out = []
    if p.get("overlay"):
        o = p["overlay"]
        out.append(f"RomFS overlay: {o['files']} file(s) from {o['mods']} mod(s)")
    if p.get("exefs"):
        e = p["exefs"]
        built = "game code to be rebuilt" if launch.get("dry_run") else "game code rebuilt"
        what = {"none": "data only, no rebuild needed", "build": f"code patches, {built}",
                "reuse": "code patches, rebuilt game code kept from last time",
                "pending": "code patches NOT active yet (rebuild pending)"}.get(e["rebuild"], e["rebuild"])
        out.append(f"ExeFS patches: {e['patches']} patch(es) in {e['files']} file(s) for "
                   f"{', '.join(e['modules'])} ({what})")
    if p.get("native"):
        helpers = len(p["native"].get("helpers") or [])
        out.append(f"Native mods: {len(p['native']['mods'])} DLL(s) through modhost"
                   + (f", with {helpers} helper DLL(s) next to them" if helpers else ""))
    if p.get("hooks"):
        k = p["hooks"]
        built = "game code to be rebuilt" if launch.get("dry_run") else "game code rebuilt"
        what = {"build": f"{built} with hook slots", "reuse": "game code with hook slots kept from last time",
                "pending": "NOT active: no hook slots, rebuild pending"}.get(k["rebuild"], k["rebuild"])
        out.append(f"Hooks: {k['count']} function(s) for {len(k['mods'])} mod(s) ({what})")
        out += [f"  {a}: {', '.join(names)}" for a, names in list(k["by_addr"].items())[:10]]
        if len(k["by_addr"]) > 10:
            out.append(f"  and {len(k['by_addr']) - 10} more (all in {k.get('file')})")
    c = launch.get("cheats")
    if c:
        on = ("all" if c["args"] == ["--all"] else str(len([a for a in c["args"] if a != "--enable"])) if c["args"]
              else "none")
        out.append(f"Cheats: {c['count']} cheat(s) from {len(c['mods'])} mod(s), {on} turned on")
    return out


def format_deploy(launch: dict) -> str:
    dry = launch.get("dry_run")
    n = len(launch["mods"])
    lines = []
    if not n:
        lines.append("No mods are on: the game will start without mods." if not dry
                     else "No mods are on: deploy would clear the deploy folder.")
    else:
        lines.append(("Would deploy" if dry else "Deployed") + f" {n} mod(s):")
    lines += [f"  {x}" for x in describe_parts(launch)]
    prev = launch.get("previous")
    if not dry and prev is not None:
        old_ids = [m.get("id") for m in prev.get("mods", [])]
        new_ids = [m["id"] for m in launch["mods"]]
        added = [m["name"] for m in launch["mods"] if m["id"] not in old_ids]
        gone = [m.get("name", m.get("id")) for m in prev.get("mods", []) if m.get("id") not in new_ids]
        moved = [i for i in old_ids if i in new_ids] != [i for i in new_ids if i in old_ids]
        bits = [f"+ {x}" for x in added] + [f"- {x}" for x in gone] + (["order changed"] if moved else [])
        lines.append("Changed since the last deploy: " + ", ".join(bits) if bits
                     else "Same mods as the last deploy.")
    elif not dry:
        lines.append("First deploy.")
    e = (launch.get("parts") or {}).get("exefs")
    k = (launch.get("parts") or {}).get("hooks")
    if dry and e and e["rebuild"] == "build":
        lines.append(f"The code patches need the game code rebuilt: that takes {REBUILD_TIME}.")
    elif dry and k and k["rebuild"] == "build":
        lines.append(f"The hooks need the game code rebuilt with hook slots: that takes {REBUILD_TIME}.")
    if launch["attention"]:
        lines.append("Needs attention:")
        lines += [f"  - {a}" for a in launch["attention"]]
    if launch["notes"]:
        lines.append("Notes:")
        lines += [f"  - {a}" for a in launch["notes"]]
    if dry:
        lines.append("Nothing was written (dry run).")
    else:
        lines.append("Start the game with: python tools/modman.py run")
    return "\n".join(lines)


# ---------------------------------------------------------------- run

def port_open(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port(port: int, game, poll: float = 0.5) -> bool:
    """Wait until the game's control port answers; False when the game ends first."""
    while game.poll() is None:
        if port_open(port):
            return True
        time.sleep(poll)
    return False


def game_env(base: dict, launch: dict, port: int | None) -> dict:
    """The game's environment: this one without leftover mod variables, plus the deploy's."""
    clear = {k.upper() for k in launch.get("clear_env", MOD_ENV)} | set(MOD_ENV)
    env = {k: v for k, v in base.items() if k.upper() not in clear}
    env.update({k: v for k, v in launch["env"].items() if k != "SAVAGEWIND_CONTROL"})
    if port:
        env["SAVAGEWIND_CONTROL"] = str(port)
    return env


def read_launch(lib: Library) -> dict:
    data = DeployDir(lib.deploy_dir).read_launch()
    marker = read_json(os.path.join(lib.deploy_dir, MARKER)) if os.path.isdir(lib.deploy_dir) else None
    if not data or not marker or not marker.get("complete"):
        raise ModmanError("Nothing deployed yet (or the last deploy didn't finish). Run: python tools/modman.py deploy")
    return data


def missing_paths(launch: dict) -> list[str]:
    """The files and folders launch.json starts the game with that aren't there (any more): after the library or
    Savage Wind was moved, say."""
    want = []
    argv = launch.get("argv") or []
    root = argv[1] if len(argv) > 1 and isinstance(argv[1], str) and not argv[1].startswith("-") else None
    if root:  # (the alt root, then its code files and the game's romfs behind its junction)
        want.append(root)
    env = launch.get("env") or {}
    want += [env[k] for k in ("SAVAGEWIND_ROMFS_OVERLAY", "SAVAGEWIND_GAME_LIB", "SWMOD_REAL_GAME_LIB", "SWMOD_DIR")
             if env.get(k)]
    c = launch.get("cheats") or {}
    if c.get("file"):
        want.append(c["file"])
    gone = [str(p) for p in want if not isinstance(p, str) or not os.path.exists(p)]
    if root and root not in gone:
        gone += [p for p in (os.path.join(root, "exefs", "main"), os.path.join(root, "romfs")) if not os.path.exists(p)]
    return gone


def _gone_text(gone: list[str]) -> str:
    return f"{_some(gone, 2)} {'is' if len(gone) == 1 else 'are'}n't there any more"


def stop(proc, grace: float = 5.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()


def run(lib: Library, *, port: int | None = None, no_cheats: bool = False) -> int:
    if port is not None and not 1 <= port <= 65535:
        raise ModmanError(f"{port} isn't a port: use 1 to 65535.")
    launch = read_launch(lib)
    exe, cwd = launch["argv"][0], launch["cwd"]
    if not os.path.isfile(exe):
        raise ModmanError(f"{exe} not found: run init and deploy again.")
    gone = missing_paths(launch)
    if gone:
        raise ModmanError(f"The last deploy can't start: {_gone_text(gone)} (was the library or Savage Wind moved?). "
                          "Run: python tools/modman.py deploy")
    if inputs_hash(lib) != launch.get("hash"):
        say("Warning: your mods or settings changed since the last deploy; this starts the last deployed set. "
            "Run deploy to use the changes.")
    e = (launch.get("parts") or {}).get("exefs")
    if e and e.get("rebuild") == "pending":
        say("Warning: the code patches are not active (deployed with --no-rebuild).")
    k = (launch.get("parts") or {}).get("hooks")
    if k and k.get("rebuild") == "pending":
        say("Warning: the hooks are not active (deployed with --no-rebuild): modhost will log 'this game code has no "
            "hook slots'.")
    if game_running():
        raise ModmanError("Savage Wind is already running. Close it first.")
    cheats = launch.get("cheats") if not no_cheats else None
    if cheats and not cheats.get("args"):
        cheats = None
    want_port = port or (DEFAULT_PORT if cheats else None)
    if want_port and port_open(want_port):
        raise ModmanError(f"Another program already uses port {want_port}. Pick a free one with --port.")
    env = game_env(dict(os.environ), launch, want_port)
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)  # (Ctrl+C here stops the cheats, not the game)
    game = subprocess.Popen(launch["argv"], cwd=cwd, env=env, creationflags=flags)
    pid = getattr(game, "pid", None)
    try:  # (the crash check and "what changed" read it: docs/profiles.md, "History")
        history_append(lib, {"type": "start", "hash": launch.get("hash") if isinstance(launch.get("hash"), str)
                             else None, "profile": lib.profile, "pid": pid if isinstance(pid, int) else None})
    except OSError:
        pass
    what = describe_parts(launch)
    mods = len(launch.get("mods", []))
    say(f"Started Savage Wind (pid {game.pid}) " + (f"with {mods} mod(s)." if mods else "without mods.")
        + (f" Control port {want_port}." if want_port else ""))
    for w in what:
        if not w.startswith("Cheats") or cheats:
            say(f"  {w}")
    if not cheats:
        return 0
    helper = None
    try:
        say(f"Waiting for the game to open port {want_port} for the cheats...")
        if not wait_for_port(want_port, game):
            say("The game closed before it opened its control port; cheats not started.")
            return 1
        argv = [sys.executable, "-I", CHEATS_PY, "run", cheats["file"], "--port", str(want_port), "--wait", "30",
                *cheats["args"]]
        if cheats.get("main") and os.path.isfile(cheats["main"]):
            argv += ["--main", cheats["main"]]
        cenv = {k: v for k, v in os.environ.items() if k.upper() not in set(MOD_ENV)}
        helper = subprocess.Popen(argv, env=cenv)
        for _ in range(4):  # (a cheat file or name cheats.py refuses ends it at once: say so instead of waiting)
            if helper.poll() is not None:
                break
            time.sleep(0.5)
        if helper.poll() is not None:
            say(f"The cheats stopped right away (exit code {helper.returncode}); see the lines above. Savage Wind "
                "keeps running without them.")
            return 1
        say(f"Started cheats (pid {helper.pid}) on port {want_port}; they stop when the game closes.")
        game.wait()
        say("Savage Wind closed.")
        return 0
    except KeyboardInterrupt:
        say("Stopped the cheats. Savage Wind keeps running.")
        return 0
    finally:
        stop(helper)


# ---------------------------------------------------------------- profiles: named mod sets (docs/profiles.md)

PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")


def profile_name_ok(name) -> bool:
    """A profile name: 1-40 letters, digits, spaces, ".", "-" and "_", starting with a letter or digit."""
    return isinstance(name, str) and bool(PROFILE_NAME_RE.fullmatch(name)) and not name.endswith((" ", "."))


def profile_file(lib: Library, name: str) -> str:
    """<library>\\profiles\\<name in lowercase, "-" for the rest>.json"""
    stem = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "profile"
    if RESERVED_RE.match(stem.split(".")[0]):
        stem = "profile-" + stem
    return os.path.join(lib.root, PROFILES_DIR, stem + ".json")


def read_profile(lib: Library, name: str) -> dict | None:
    """A profile's file, checked; None when there is none."""
    path = profile_file(lib, name)
    data = read_json(path)
    if data is None:
        return None
    if data.get("tool") != TOOL_ID or data.get("kind") != "profile" or not isinstance(data.get("mods"), list) \
            or not profile_name_ok(data.get("name")):
        raise ModmanError(f"{path} isn't a mod set modman wrote")
    return data


def profile_names(lib: Library) -> list[str]:
    """The profiles' names: Default first (it is there even before it has a file), then the others by name."""
    names = set()
    folder = os.path.join(lib.root, PROFILES_DIR)
    if os.path.isdir(folder):
        for n in sorted(os.listdir(folder)):
            if n.lower().endswith(".json"):
                try:
                    data = read_json(os.path.join(folder, n))
                except ModmanError:
                    continue
                if data and data.get("kind") == "profile" and profile_name_ok(data.get("name")) \
                        and os.path.normcase(profile_file(lib, data["name"])) == os.path.normcase(os.path.join(folder, n)):
                    names.add(data["name"])
    names.add(lib.profile)
    others = sorted((n for n in names if n.lower() != DEFAULT_PROFILE.lower()), key=str.lower)
    return [DEFAULT_PROFILE, *others]


def find_profile(lib: Library, name: str) -> str:
    want = (name or "").strip()
    hit = next((n for n in profile_names(lib) if n.lower() == want.lower()), None)
    if hit is None:
        raise ModmanError(f"No mod set '{want}'. These are there: {', '.join(profile_names(lib))}.")
    return hit


def profile_snapshot(lib: Library, name: str) -> dict:
    """The library's mod set as a profile: the mods in order, on or off, the options chosen for .bnp and UKMM
    packages, and the setting values the player changed (settings\\<id>.json)."""
    options = {}
    for m in lib.mods:
        pkg = m.get("bnp") or m.get("ukmm")
        if isinstance(pkg, dict) and isinstance(pkg.get("options"), list):
            options[m["id"]] = [str(o) for o in pkg["options"]]
    settings = {m["id"]: v for m in lib.mods if (v := read_setting_values(lib, m["id"]))}
    return {"tool": TOOL_ID, "format": 1, "kind": "profile", "name": name, "saved": _now(),
            "mods": [{"id": m["id"], "enabled": bool(m.get("enabled"))} for m in lib.mods],
            "options": options, "settings": settings}


def save_profile(lib: Library, name: str) -> str:
    """Write the library's mod set as profile `name`. Returns the file."""
    if not profile_name_ok(name):
        raise ModmanError(f"'{name}' can't be a mod set name: use up to 40 letters, digits, spaces, '.', '-' and '_'.")
    path = profile_file(lib, name)
    old = read_json(path) if os.path.isfile(path) else None
    if old and isinstance(old.get("name"), str) and old["name"].lower() != name.lower():
        raise ModmanError(f"'{name}' would share a file with the mod set '{old['name']}': pick another name.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_json(path, profile_snapshot(lib, name))
    return path


def apply_profile(lib: Library, data: dict) -> list[str]:
    """Make the library's mod set the profile's: order, on/off and setting values. Mods added since the profile was
    saved go last, turned off; mods it names that the library no longer has are skipped. Returns notes."""
    notes: list[str] = []
    rows = [x for x in data.get("mods", []) if isinstance(x, dict) and isinstance(x.get("id"), str)]
    on = {x["id"]: bool(x.get("enabled")) for x in rows}
    by_id = {m["id"]: m for m in lib.mods}
    new = [by_id[x["id"]] for x in rows if x["id"] in by_id]
    added = [m for m in lib.mods if m["id"] not in on]
    for m in new:
        m["enabled"] = on[m["id"]]
    for m in added:
        m["enabled"] = False
    gone = [x["id"] for x in rows if x["id"] not in by_id]
    if added:
        notes.append(f"Off in this set (added after it was saved): {_some([m['name'] for m in added])}.")
    if gone:
        notes.append(f"Not in the library any more, so left out: {_some(gone)}.")
    lib.mods = new + added
    want = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    for m in lib.mods:
        target = want.get(m["id"]) if isinstance(want.get(m["id"]), dict) else {}
        if read_setting_values(lib, m["id"]) != target and (target or os.path.isfile(settings_file(lib, m["id"]))):
            write_setting_values(lib, m["id"], target)
    opts = data.get("options") if isinstance(data.get("options"), dict) else {}
    for m in lib.mods:
        pkg = m.get("bnp") or m.get("ukmm")
        had = opts.get(m["id"])
        if isinstance(pkg, dict) and isinstance(had, list) and sorted(map(str, had)) != sorted(map(str, pkg.get(
                "options") or [])):
            notes.append(f"{m['name']} was added with other options than this set had. To change them: remove it and "
                         "add it again with --option.")
    return notes


def use_profile(lib: Library, name: str) -> list[str]:
    """Switch to profile `name`: the current set is kept in its own profile first. Returns notes."""
    if game_running():
        raise ModmanError("Savage Wind is running. Close it, then switch the mod set.")
    target = find_profile(lib, name)
    if target == lib.profile:
        return [f"'{target}' is the mod set in use already."]
    data = read_profile(lib, target)
    if data is None:
        raise ModmanError(f"The mod set '{target}' has no file ({profile_file(lib, target)}).")
    save_profile(lib, lib.profile)
    notes = apply_profile(lib, data)
    lib.profile = data["name"]
    lib.save()
    return notes


def new_profile(lib: Library, name: str) -> str:
    """Start a new profile from the current mod set and use it (the current one is kept as it is)."""
    name = (name or "").strip()
    if not profile_name_ok(name):
        raise ModmanError(f"'{name}' can't be a mod set name: use up to 40 letters, digits, spaces, '.', '-' and '_'.")
    if any(n.lower() == name.lower() for n in profile_names(lib)) or os.path.isfile(profile_file(lib, name)):
        raise ModmanError(f"There is a mod set called '{name}' already (or one with the same file name).")
    if game_running():
        raise ModmanError("Savage Wind is running. Close it, then make the mod set.")
    save_profile(lib, lib.profile)
    path = save_profile(lib, name)
    lib.profile = name
    lib.save()
    return path


def remove_profile(lib: Library, name: str) -> str:
    """Delete a profile's file (not Default's, not the one in use). Its mods stay in the library."""
    target = find_profile(lib, name)
    if target.lower() == DEFAULT_PROFILE.lower():
        raise ModmanError("The Default mod set can't be removed.")
    if target == lib.profile:
        raise ModmanError(f"'{target}' is in use: switch to another mod set first.")
    path = profile_file(lib, target)
    if os.path.isfile(path):
        os.remove(path)
    return path


# ---------------------------------------------------------------- commands

def cmd_init(args) -> int:
    install = _abs(args.install) if args.install else default_install()
    if not looks_like_install(install):
        raise ModmanError(f"No {EXE} in {install}. Pass your Savage Wind folder: python tools/modman.py init <folder>")
    root = resolve_library(args.library, install)
    lib = Library.load(root) or Library(root)
    lib.install = install
    lib.save()
    say(f"Mod library: {root}")
    say(f"Savage Wind: {install}")
    if _inside(root, install):
        say("Warning: the library is inside the Savage Wind folder, so removing Savage Wind would delete your mods.")
    if not os.path.isfile(os.path.join(install, "extracted", LIB)):
        say("Set up the game files in Savage Wind before you deploy.")
    if not args.library and not os.environ.get(ENV_LIBRARY) and \
            os.path.normcase(root) != os.path.normcase(resolve_library(None)):
        say(f'This isn\'t the default place: add --library "{root}" to later commands (or set {ENV_LIBRARY}).')
    say("Next: python tools/modman.py add <mod folder or .zip>")
    return 0


def cmd_add(args) -> int:
    lib = open_library(args, create=True)
    entry, scan, notes = add_mod(lib, args.path, args.name, convert=not args.no_convert,
                                 bnp_options=args.bnp_option, bnp_no_options=args.bnp_no_options)
    version = f" {entry['version']}" if entry.get("version") else ""
    say(f"Added #{len(lib.mods)} {entry['id']}{version} ({', '.join(entry['kinds'])}): {entry['name']}"
        + (" (from its mod.toml)" if scan.manifest else ""))
    packaged = entry.get("wiiu") or entry.get("bnp") or entry.get("ukmm")
    for n in (notes + scan.notes) if packaged else (scan.notes + notes):
        say(f"  {n}")
    for p in scan.problems:
        say(f"  not used: {p}")
    say("Run deploy to use it.")
    return 0


def cmd_list(args) -> int:
    root = resolve_library(args.library)
    lib = Library.load(root)
    if not lib or not lib.mods:
        say(f"No mods yet (library: {root}). Add one: python tools/modman.py add <mod folder or .zip>")
        return 0
    say(f"{len(lib.mods)} mod(s), {len(lib.enabled())} on. Later mods win.")
    w = max(len(m["id"]) for m in lib.mods)
    kw = max(len(", ".join(m["kinds"]) or "-") for m in lib.mods)
    vw = max(len(str(m.get("version") or "-")) for m in lib.mods)
    for k, m in enumerate(lib.mods, 1):
        wiiu = wiiu_short(m)
        say(f"  {k:>2}  {'on ' if m['enabled'] else 'off'}  {m['id']:<{w}}  {str(m.get('version') or '-'):<{vw}}  "
            f"{', '.join(m['kinds']) or '-':<{kw}}  {m['name']}" + (f"  ({wiiu})" if wiiu else ""))
    return 0


def cmd_enable(args, on: bool) -> int:
    lib = open_library(args)
    _, m = lib.find(args.mod)
    m["enabled"] = on
    lib.save()
    say(f"{m['id']} is {'on' if on else 'off'}. Run deploy to apply.")
    return 0


def cmd_move(args) -> int:
    lib = open_library(args)
    i, m = lib.find(args.mod)
    if not 1 <= args.position <= len(lib.mods):
        raise ModmanError(f"Position must be 1 to {len(lib.mods)}.")
    lib.mods.insert(args.position - 1, lib.mods.pop(i))
    lib.save()
    say(f"{m['id']} is now #{args.position}. Run deploy to apply.")
    return cmd_list(args)


def cmd_remove(args) -> int:
    lib = open_library(args)
    m = remove_mod(lib, args.mod)
    say(f"Removed {m['id']} ({m['name']}) from the library.")
    if os.path.isfile(os.path.join(lib.deploy_dir, LAUNCH)):
        say("Run deploy to take it out of the game.")
    return 0


def cmd_deploy(args) -> int:
    lib = open_library(args)
    launch = deploy(lib, no_rebuild=args.no_rebuild, dry_run=args.dry_run)
    say(format_deploy(launch))
    return 0


def cmd_run(args) -> int:
    return run(open_library(args), port=args.port, no_cheats=args.no_cheats)


def cmd_status(args) -> int:
    root = resolve_library(args.library)
    lib = Library.load(root)
    if not lib:
        say(f"No mod library in {root} yet. Run: python tools/modman.py init")
        return 0
    say(f"Library: {root} ({len(lib.mods)} mod(s), {len(lib.enabled())} on)")
    if lib.profile != DEFAULT_PROFILE:
        say(f"Mod set: {lib.profile}")
    say(f"Savage Wind: {lib.install or 'not set (run init)'}")
    wiiu = [m["id"] + (f" ({m['wiiu']['wiiu_data']} file(s) not converted)" if m["wiiu"].get("wiiu_data") else "")
            for m in lib.enabled() if isinstance(m.get("wiiu"), dict)]
    if wiiu:
        say(f"Converted from Wii U: {_some(wiiu, 5)}")
    try:
        launch = read_launch(lib)
    except ModmanError as e:
        say(str(e))
        return 0
    fresh = inputs_hash(lib) == launch.get("hash")
    gone = missing_paths(launch)
    say(f"Deployed {launch.get('deployed', '?')}: " + (
        f"BROKEN ({_gone_text(gone)}). Run deploy." if gone
        else "up to date." if fresh else "OUT OF DATE (mods or settings changed since). Run deploy."))
    for x in describe_parts(launch) or ["no mods"]:
        say(f"  {x}")
    for a in launch.get("attention", []):
        say(f"  ! {a}")
    say("Start: python tools/modman.py run")
    return 0


def cmd_profile(args) -> int:
    lib = open_library(args)
    if args.action == "list":
        names = profile_names(lib)
        say(f"{len(names)} mod set(s); in use: {lib.profile}.")
        for n in names:
            say(f"  {'*' if n == lib.profile else ' '} {n}")
        say("Switch: python tools/modman.py profile use <name>")
        return 0
    if not args.name:
        raise ModmanError(f"Say which mod set: python tools/modman.py profile {args.action} <name>")
    if args.action == "new":
        new_profile(lib, args.name)
        say(f"Made the mod set '{lib.profile}' from the mods as they are now, and it is in use. Changes you make now "
            "go into it.")
        return 0
    if args.action == "use":
        notes = use_profile(lib, args.name)
        say(f"Mod set in use: {lib.profile}. {len(lib.enabled())} of {len(lib.mods)} mod(s) on.")
        for n in notes:
            say(f"  {n}")
        say("Run deploy to use it.")
        return 0
    name = find_profile(lib, args.name)
    remove_profile(lib, name)
    say(f"Removed the mod set '{name}'. Its mods stay in the library.")
    return 0


def cmd_doctor(args) -> int:
    root = resolve_library(args.library)
    try:
        lib = Library.load(root)
    except ModmanError as e:
        say(f"FAIL {e}")
        lib = None
    install = (lib.install if lib else None) or default_install()
    bad = 0

    def line(ok, msg, level="FAIL"):
        nonlocal bad
        if not ok and level == "FAIL":
            bad += 1
        say(f"{'OK  ' if ok else level.ljust(4)} {msg}")

    line(looks_like_install(install), f"Savage Wind: {install}" if looks_like_install(install)
         else f"Savage Wind not found in {install} (run init <folder>)")
    ex = os.path.join(install, "extracted")
    gid = _read_id(os.path.join(ex, LIB + ".id"))
    ready = os.path.isfile(os.path.join(ex, LIB)) and gid and os.path.isfile(os.path.join(ex, "exefs", "main")) \
        and os.path.isdir(os.path.join(ex, "romfs"))
    line(bool(ready), f"game files set up (game code id {gid})" if ready
         else "game files not set up: start Savage Wind and set them up")
    if ready:
        g = game_info(install)
        bid = g.main_bid16
        line(bid == BUILD_190, f"game version {mi.KNOWN_BUILDS.get(bid, 'unknown')} (build {bid})", "WARN")
    line(bool(lib), f"mod library: {root}" if lib else f"no mod library in {root} yet (run init)", "WARN")
    probe = _existing(root)
    free = shutil.disk_usage(probe).free
    line(free >= 5 * 2**30, f"{_gb(free)} free on the library's drive ({os.path.splitdrive(probe)[0] or probe})",
         "WARN")
    py = sys.version_info
    line(py[:2] >= MIN_PYTHON, f"Python {py.major}.{py.minor}.{py.micro}"
         + ("" if py[:2] >= MIN_PYTHON else f" (needs {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer)"))
    host = os.path.join(MODHOST_DIR, "modhost.dll")
    line(os.path.isfile(host), "modhost built (native mods can be used)" if os.path.isfile(host)
         else "modhost not built: native mods need python tools/build_native.py build", "--  ")
    if looks_like_install(install):
        state = swr.load_state(install)
        if state.get("main_installed_sha256") or state.get("library_installed_sha256"):
            say("NOTE sw_rebuild installed a patched main or rebuilt game code in the game files; modman uses the "
                "vanilla copies for ExeFS patches.")
    line(not game_running(), "Savage Wind isn't running" if not game_running()
         else "Savage Wind is running (close it before deploy)", "NOTE")
    return 1 if bad else 0


def port_arg(text: str) -> int:
    """A TCP port for --port: 1 to 65535, as Savage Wind takes for SAVAGEWIND_CONTROL."""
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' isn't a port number") from None
    if not 1 <= n <= 65535:
        raise argparse.ArgumentTypeError(f"{n} isn't a port: use 1 to 65535")
    return n


def parser() -> argparse.ArgumentParser:
    lib_help = f"the mod library folder (default: {ENV_LIBRARY}, else 'modloader' next to the Savage Wind folder)"
    top = argparse.ArgumentParser(add_help=False)
    top.add_argument("--library", help=lib_help)
    common = argparse.ArgumentParser(add_help=False)  # (SUPPRESS: given before the command, it isn't reset after)
    common.add_argument("--library", help=lib_help, default=argparse.SUPPRESS)
    ap = argparse.ArgumentParser(prog="modman.py", description=__doc__.split("\n\n")[0], parents=[top])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", parents=[common], help="set the Savage Wind folder and make the library")
    p.add_argument("install", nargs="?", help="your Savage Wind folder (default: Documents\\SavageWind\\Savage Wind)")
    p = sub.add_parser("add", parents=[common], help="add a mod folder, .zip, .bnp, patch, cheat file or DLL")
    p.add_argument("path")
    p.add_argument("--name", help="a name for the mod (default: the folder or file name)")
    p.add_argument("--no-convert", action="store_true",
                   help="refuse a Wii U mod instead of converting it to a Switch mod (tools/wiiu_convert.py)")
    p.add_argument("--bnp-option", "--option", action="append", default=[], metavar="NAME",
                   help="a BCML or UKMM package's option to install, by name or folder (repeat for more; "
                        "tools/bnp.py info or tools/ukmm.py info lists them)")
    p.add_argument("--bnp-no-options", "--no-options", action="store_true",
                   help="leave out the options a BCML or UKMM package turns on by default (--option ones still go "
                        "in)")
    sub.add_parser("list", parents=[common], help="show the mods in order")
    for name in ("enable", "disable"):
        p = sub.add_parser(name, parents=[common], help=f"turn a mod {'on' if name == 'enable' else 'off'}")
        p.add_argument("mod", help="id or number from list")
    p = sub.add_parser("move", parents=[common], help="move a mod to a position (later mods win)")
    p.add_argument("mod", help="id or number from list")
    p.add_argument("position", type=int)
    p = sub.add_parser("remove", parents=[common], help="delete a mod from the library")
    p.add_argument("mod", help="id or number from list")
    p = sub.add_parser("deploy", parents=[common], help="build the deploy folder from the enabled mods")
    p.add_argument("--no-rebuild", action="store_true",
                   help="don't rebuild the game code (code patches and hooks then do nothing)")
    p.add_argument("--dry-run", action="store_true", help="say what would happen; write nothing")
    p = sub.add_parser("run", parents=[common], help="start Savage Wind with the deployed mods")
    p.add_argument("--port", type=port_arg, help=f"control port for cheats, 1-65535 (default {DEFAULT_PORT})")
    p.add_argument("--no-cheats", action="store_true", help="start without the cheats")
    sub.add_parser("status", parents=[common], help="what is deployed, and whether it is up to date")
    sub.add_parser("doctor", parents=[common], help="check that everything needed is there")
    p = sub.add_parser("profile", parents=[common],
                       help="mod sets you switch between: list, new <name>, use <name>, remove <name>")
    p.add_argument("action", nargs="?", default="list", choices=("list", "new", "use", "remove"))
    p.add_argument("name", nargs="?", help="the mod set's name (up to 40 letters, digits, spaces, '.', '-', '_')")
    return ap


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    args = parser().parse_args(argv)
    cmds = {"init": cmd_init, "add": cmd_add, "list": cmd_list, "enable": lambda a: cmd_enable(a, True),
            "disable": lambda a: cmd_enable(a, False), "move": cmd_move, "remove": cmd_remove,
            "deploy": cmd_deploy, "run": cmd_run, "status": cmd_status, "doctor": cmd_doctor,
            "profile": cmd_profile}
    try:
        return cmds[args.cmd](args)
    except ModmanError as e:
        say(f"Error: {e}")
        return 2
    except (OSError, zipfile.BadZipFile, rm.DeployError, ep.PatchError, swr.RebuildError, bnp.BnpError,
            ukmm.UkmmError) as e:
        say(f"Error: {e}")
        return 2
    except KeyboardInterrupt:
        say("Stopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
