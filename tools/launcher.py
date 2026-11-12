#!/usr/bin/env python3
"""Breathalyzer, the launcher: install it, point it at Savage Wind once, and press Play.

    python tools/launcher.py [--library <folder>] [--size N] [--dark] [--port N] [<file.breath>]

The first start finds Savage Wind (or asks for its folder), checks it (version, game files, room to write) and finds
or makes your mod list. After that the window (tools/modman_gui.py, LauncherView) opens on Home: one big Play button
and your mods with their titles, what they do, and ON/OFF. Play does everything: it uses the changes from the game's
Mods page, gets your mods ready (modman deploy) when they changed, and starts the game (modman run). A .breath file
(tools/breath.py) given on the command line, as a double-click on one gives it, is offered for adding ("Add this
mod?"); with the window already open, it goes to that window. docs/launcher.md explains it all.

What the launcher remembers (the Savage Wind folder, the mod library and the window's choices; nothing else) is in
%LOCALAPPDATA%\\Breathalyzer\\launcher.json; the BREATHALYZER_HOME variable names another folder (tests use it).
It never goes online: no update check, nothing sent anywhere. In the Savage Wind folder it changes one thing, only
when the player picks it in Settings and the game is closed: savagewind.ini's frame_rate line (the file as it was is
kept once as savagewind.ini.breathalyzer-backup).

This file holds the parts without a window (each tested on its own in tests/test_launcher.py): the settings file,
finding and checking Savage Wind, the first-run steps, the mod descriptions, the running-game check, one window only,
desktop shortcuts, the frame rate, and the .breath file type (this Windows user only: HKEY_CURRENT_USER, set and
removed with Settings' buttons).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TOOLS)     # (in the player zips this is the app\ folder)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

APP_NAME = "Breathalyzer"
APP_ID = "giruka.Breathalyzer"    # Windows groups the launcher's windows on the taskbar by this
WINDOW_TITLE = "Breathalyzer"     # (a second start finds the open window by window.json, not by this)
SETTINGS_FILE = "launcher.json"
ENV_HOME = "BREATHALYZER_HOME"
FORMAT = 1
EXE = "savagewind.exe"
CONFIG_EXE = "Savage Wind Configuration.exe"
INI = "savagewind.ini"
SUPPORTED = "0.4.1-alpha.4"       # the Savage Wind this Breathalyzer works with
# SHA-256 of savagewind.exe -> its version. The version string in the exe decides; a known hash makes it exact (an
# unknown one with the right string is "a custom build": allowed, with a note). Add each official release here.
KNOWN_EXES = {"90bdae43315a1f8f348296f1c9dd8144421f027582f62a371c8ee62ce62f2804": "0.4.1-alpha.4"}
GAME_BUILD = "cd57b23fa4bbad65"   # BotW 1.9.0's main build id (modman.BUILD_190)
DOWNLOAD_PAGE = "https://github.com/Revivedx/SavageWindRecomp/releases"
LIBRARY_NAME = "modloader"        # (modman.LIBRARY_NAME: the library goes next to the Savage Wind folder)
ENV_LIBRARY = "SWMOD_LIBRARY"
ENV_DATA_ROOT = "SAVAGEWIND_DATA_ROOT"
MIN_FREE = 2 * 2**30              # code mods need about this much: the rebuilt game code and its cache
MAX_LOOK = 300                    # folders looked into next to Breathalyzer, at most
DEFAULT_SIZE, MIN_SIZE, MAX_SIZE = 20, 10, 40
# The version in the exe's text (it sits right after a commit hash: "...dda70.4.1-alpha.4"): found by its
# "-alpha.N" end, then read back from there with a one-digit major.
ANCHOR_RE = re.compile(rb"-(?:alpha|beta|rc)\.\d{1,3}(?![0-9])")
VERSION_RE = re.compile(rb"(\d\.\d{1,3}\.\d{1,3}-(?:alpha|beta|rc)\.\d{1,3})$")
MAX_EXE = 512 * 2**20             # (a bigger savagewind.exe isn't read)
SKIP_DIRS = {"windows", "program files", "program files (x86)", "programdata", "appdata", "$recycle.bin",
             "system volume information", "node_modules", "__pycache__", ".git"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------- the settings file

@dataclass
class Settings:
    install: str | None = None        # the Savage Wind folder
    library: str | None = None        # the mod library
    size: int = DEFAULT_SIZE          # text size, points
    dark: bool = False
    port: int | None = None           # the control port (None: modman's 7777; only --port sets another)
    hide_while_playing: bool = False
    picked: str = ""                  # the bundled mods list the player answered in the mod picker (read_bundled)
    dev: bool = False                 # Settings > Developer commands: Play opens the control port (developer mode)
    extra: dict = field(default_factory=dict)   # keys a newer launcher wrote: kept as they are

    KEYS = ("install", "library", "size", "dark", "port", "hide_while_playing", "picked", "dev")


def home_dir(env: dict | None = None) -> str:
    """Where launcher.json goes: BREATHALYZER_HOME, else %LOCALAPPDATA%\\Breathalyzer (it survives updates of
    Breathalyzer: a new version is a new folder)."""
    env = os.environ if env is None else env
    if env.get(ENV_HOME):
        return os.path.abspath(env[ENV_HOME])
    base = env.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, APP_NAME)


def settings_path(env: dict | None = None) -> str:
    return os.path.join(home_dir(env), SETTINGS_FILE)


def load_settings(path: str) -> tuple[Settings, str | None]:
    """(the settings, a note when the file couldn't be read: it is then set aside as launcher.json.bad and the
    launcher starts as on the first run). Values of the wrong kind are left out."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return Settings(), None
    except (OSError, ValueError) as e:
        data = e
    if not isinstance(data, dict):
        bad = path + ".bad"
        with contextlib.suppress(OSError):
            os.replace(path, bad)
        return Settings(), f"Your launcher settings couldn't be read, so they start fresh (the old file: {bad})."
    s = Settings()
    for key in ("install", "library", "picked"):
        if isinstance(data.get(key), str) and data[key].strip():
            setattr(s, key, data[key])
    if type(data.get("size")) is int and MIN_SIZE <= data["size"] <= MAX_SIZE:
        s.size = data["size"]
    for key in ("dark", "hide_while_playing", "dev"):
        if type(data.get(key)) is bool:
            setattr(s, key, data[key])
    if type(data.get("port")) is int and 1 <= data["port"] <= 65535:
        s.port = data["port"]
    s.extra = {k: v for k, v in data.items() if k not in Settings.KEYS and k != "format"}
    return s, None


def save_settings(path: str, s: Settings) -> None:
    """Write launcher.json: a temp file, then a rename (a crash never leaves half a file)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {**s.extra, "format": FORMAT, **{k: getattr(s, k) for k in Settings.KEYS}}
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- finding Savage Wind (fixed places only)

def has_exe(folder: str | None) -> bool:
    return bool(folder) and os.path.isfile(os.path.join(folder, EXE))


def subfolders(folder: str) -> list[str]:
    """The folders right inside `folder`, by name; never links or junctions (they can point anywhere: a library's
    deploy\\root\\romfs points at the game's files), never system folders. [] when it can't be read."""
    out = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                try:
                    if e.name.lower() in SKIP_DIRS or e.name.startswith(("$", ".")) or e.is_symlink() \
                            or (hasattr(e, "is_junction") and e.is_junction()) or not e.is_dir():
                        continue
                except OSError:
                    continue
                out.append(e.path)
    except OSError:
        return []
    return sorted(out, key=str.lower)


def correct_pick(path: str) -> list[str]:
    """The Savage Wind folder(s) a folder the player picked means. The folder itself when it has savagewind.exe; the
    folder of a picked file (savagewind.exe itself); the Savage Wind folder above a folder inside it (`extracted`,
    `extracted\\romfs`, `save`...: one to four levels too low); the folders one or two levels below that have it
    (one or two levels too high: `SavageWind`, `Documents`). [] when none."""
    p = os.path.abspath(path)
    if os.path.isfile(p):
        p = os.path.dirname(p)
    if has_exe(p):
        return [p]
    up = p
    for _ in range(4):
        parent = os.path.dirname(up)
        if parent == up:
            break
        up = parent
        if has_exe(up):
            return [up]
    found = []
    for child in subfolders(p)[:MAX_LOOK]:
        if has_exe(child):
            found.append(child)
            continue
        found += [g for g in subfolders(child)[:60] if has_exe(g)]
        if len(found) >= 10:
            break
    return found[:10]


@dataclass
class Found:
    folder: str
    why: str    # remembered, library, next to Breathalyzer, Documents/Desktop/Downloads, drive, running


def package_top(app_dir: str = REPO) -> str:
    """The folder the player unpacked: Breathalyzer-<v>\\ (app\\ is inside it); the repo itself from source."""
    return os.path.dirname(app_dir) if os.path.basename(app_dir).lower() == "app" else app_dir


def near_folders(top: str, limit: int = MAX_LOOK) -> list[str]:
    """Next to Breathalyzer: its own folder, the folder it is in, and that folder's subfolders and theirs (depth 2,
    nearest first, at most `limit` folders looked into)."""
    parent = os.path.dirname(top)
    out = [top] + ([parent] if parent and parent != top else [])
    if not parent or parent == top:
        return out
    level, looked = [parent], 0
    for _ in range(2):
        nxt = []
        for folder in level:
            if looked >= limit:
                return out
            looked += 1
            for sub in subfolders(folder):
                if os.path.normcase(sub) != os.path.normcase(top):
                    out.append(sub)
                    nxt.append(sub)
        level = nxt
    return out


def library_installs(libraries: list[str]) -> list[str]:
    """The Savage Wind folders mod libraries made earlier (with the command line) name: their config.json's
    `install`. Only read."""
    out = []
    for lib in libraries:
        try:
            with open(os.path.join(lib, "config.json"), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("install"), str):
            out.append(data["install"])
    return out


def known_places(folders: dict[str, str]) -> list[tuple[str, str]]:
    """(folder, why) to try in Documents, Desktop and Downloads: `Savage Wind`, `SavageWind\\Savage Wind`, and any
    `SavageWind*`/`Savage Wind*` folder (and its `Savage Wind`), one level only."""
    out = []
    for why, base in folders.items():
        if not base or not os.path.isdir(base):
            continue
        out += [(os.path.join(base, "Savage Wind"), why), (os.path.join(base, "SavageWind", "Savage Wind"), why)]
        for sub in subfolders(base):
            name = os.path.basename(sub).lower().replace(" ", "")
            if name.startswith("savagewind"):
                out += [(sub, why), (os.path.join(sub, "Savage Wind"), why)]
    return out


def drive_places(drives: list[str]) -> list[str]:
    """Exact folders on each fixed drive (never a scan of the drive)."""
    out = []
    for d in drives:
        root = d if d.endswith(("\\", "/")) else d + os.sep
        out += [os.path.join(root, *parts) for parts in (("Savage Wind",), ("SavageWind", "Savage Wind"),
                                                         ("Games", "Savage Wind"),
                                                         ("Games", "SavageWind", "Savage Wind"))]
    return out


def find_installs(remembered: str | None = None, libraries: list[str] = (), near: list[str] = (),
                  known: list[tuple[str, str]] = (), drives: list[str] = (), running: list[str] = ()) -> list[Found]:
    """Every Savage Wind folder in the fixed places, in this order, each once: the remembered one, the ones mod
    libraries name, next to Breathalyzer, Documents/Desktop/Downloads, the fixed drives' usual folders, and a running
    savagewind.exe's own folder. Only those exact folders are looked at."""
    seen, out = set(), []

    def add(folder: str | None, why: str) -> None:
        if not folder:
            return
        key = os.path.normcase(os.path.abspath(folder))
        if key not in seen and has_exe(folder):
            seen.add(key)
            out.append(Found(os.path.abspath(folder), why))

    add(remembered, "remembered")
    for f in library_installs(list(libraries)):
        add(f, "your mod list")
    for f in near:
        add(f, "next to Breathalyzer")
    for f, why in known:
        add(f, why)
    for f in drives:
        add(f, "drive")
    for exe in running:
        add(os.path.dirname(exe), "running")
    return out


def default_places(app_dir: str = REPO, env: dict | None = None) -> dict:
    """The arguments of find_installs for this PC (no disk scans)."""
    env = os.environ if env is None else env
    known = known_folders()
    libs = []
    if env.get(ENV_LIBRARY):
        libs.append(env[ENV_LIBRARY])
    if known.get("Documents"):
        libs.append(os.path.join(known["Documents"], "SavageWind", LIBRARY_NAME))
    return {"libraries": libs, "near": near_folders(package_top(app_dir)), "known": known_places(known),
            "drives": drive_places(fixed_drives()), "running": [p for _, p in running_games() if p]}


# ---------------------------------------------------------------- Windows parts (ctypes; nothing to install)

def _win():
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    return ctypes, wintypes


def known_folders() -> dict[str, str]:
    """Documents, Desktop and Downloads where Windows has them (moved by OneDrive too), else in the home folder."""
    home = os.path.expanduser("~")
    out = {name: os.path.join(home, name) for name in ("Documents", "Desktop", "Downloads")}
    w = _win()
    if w is None:
        return out
    ctypes, wintypes = w
    ids = {"Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}", "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
           "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}"}

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    try:
        shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
        for name, text in ids.items():
            g = GUID()
            if ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g)) != 0:
                continue
            p = ctypes.c_wchar_p()
            if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0 and p.value:
                out[name] = p.value
            ole32.CoTaskMemFree(p)
    except (AttributeError, OSError):
        pass
    return out


def fixed_drives() -> list[str]:
    w = _win()
    if w is None:
        return []
    ctypes, _ = w
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return [f"{chr(65 + i)}:\\" for i in range(26) if mask >> i & 1
                and ctypes.windll.kernel32.GetDriveTypeW(f"{chr(65 + i)}:\\") == 3]   # DRIVE_FIXED
    except (AttributeError, OSError):
        return []


def running_games(name: str = EXE) -> list[tuple[int, str]]:
    """(pid, exe path or "") of each running savagewind.exe. Through the process list (Toolhelp), not tasklist: no
    console window flashes up under pythonw, and it costs a millisecond."""
    w = _win()
    if w is None:
        return []
    ctypes, wintypes = w

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(Entry))
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                             ctypes.POINTER(wintypes.DWORD))
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    snap = k.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    out = []
    try:
        e = Entry()
        e.dwSize = ctypes.sizeof(Entry)
        ok = k.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == name.lower():
                path = ""
                h = k.OpenProcess(0x1000, False, e.th32ProcessID)   # PROCESS_QUERY_LIMITED_INFORMATION
                if h:
                    buf = ctypes.create_unicode_buffer(1024)
                    n = wintypes.DWORD(len(buf))
                    if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                        path = buf.value
                    k.CloseHandle(h)
                out.append((int(e.th32ProcessID), path))
            ok = k.Process32NextW(snap, ctypes.byref(e))
    finally:
        k.CloseHandle(snap)
    return out


def game_running() -> bool:
    return bool(running_games())


def single_instance(key: str):
    """One launcher window per settings folder (key): a named mutex. Returns its handle (keep it until the end), or
    None when another launcher holds it: that one's window is then brought to the front (bring_to_front: only that
    window, and never while the game is open). Off Windows: always a handle."""
    w = _win()
    if w is None:
        return object()
    ctypes, wintypes = w
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.restype = wintypes.HANDLE
    k.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    name = "Local\\Breathalyzer-" + hashlib.sha1(os.path.normcase(key).encode("utf-8")).hexdigest()[:16]
    h = k.CreateMutexW(None, False, name)
    if h and ctypes.get_last_error() == 183:   # ERROR_ALREADY_EXISTS
        k.CloseHandle(wintypes.HANDLE(h))
        bring_to_front(key)
        return None
    return h or object()


# The open launcher window says which window it is (its process and window handle) and whether the game is open, in
# this file in its settings folder: a second start brings only that window up, and never while the game is open.
WINDOW_FILE = "window.json"


def publish_window(folder: str, pid: int, hwnd: int, playing: bool) -> None:
    path = os.path.join(folder, WINDOW_FILE)
    tmp = f"{path}.{pid}.tmp"
    with contextlib.suppress(OSError):
        os.makedirs(folder, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"pid": int(pid), "hwnd": int(hwnd), "playing": bool(playing)}, f)
        os.replace(tmp, path)


def forget_window(folder: str, pid: int) -> None:
    """The window closes: its line goes (only its own: a newer launcher's stays)."""
    info = read_window(folder)
    if info is not None and info["pid"] == pid:
        with contextlib.suppress(OSError):
            os.remove(os.path.join(folder, WINDOW_FILE))


def read_window(folder: str) -> dict | None:
    try:
        with open(os.path.join(folder, WINDOW_FILE), encoding="utf-8") as f:
            info = json.loads(f.read(4096))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or type(info.get("pid")) is not int or type(info.get("hwnd")) is not int \
            or info["hwnd"] <= 0:
        return None
    return {"pid": info["pid"], "hwnd": info["hwnd"], "playing": info.get("playing") is not False}


def window_owner(hwnd: int) -> tuple[int, str] | None:
    """(the process that owns this window, its window class), or None when there is no such window."""
    w = _win()
    if w is None:
        return None
    ctypes, wintypes = w
    u = ctypes.windll.user32
    h = wintypes.HWND(hwnd)
    if not u.IsWindow(h):
        return None
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(h, ctypes.byref(pid))
    buf = ctypes.create_unicode_buffer(64)
    u.GetClassNameW(h, buf, 64)
    return pid.value, buf.value


def show_window(hwnd: int) -> bool:
    """Restore a minimized window and put it in front."""
    w = _win()
    if w is None:
        return False
    ctypes, wintypes = w
    u = ctypes.windll.user32
    h = wintypes.HWND(hwnd)
    if u.IsIconic(h):
        u.ShowWindow(h, 9)   # SW_RESTORE
    return bool(u.SetForegroundWindow(h))


def bring_to_front(key: str, running=None) -> bool:
    """A second start: the open launcher window of this settings folder to the front. Only the window its
    window.json names (owned by that process, a Tk window: never another window called Breathalyzer), and not at
    all while the game is open (the window stays hidden while you play; a .breath handed over waits for after)."""
    info = read_window(key)
    if info is None or info["playing"]:
        return False
    try:
        if (running or game_running)():
            return False
    except Exception:  # (can't tell: then the game may be open, and the window stays where it is)
        return False
    owner = window_owner(info["hwnd"])
    if owner is None or owner != (info["pid"], "TkTopLevel"):
        return False
    return show_window(info["hwnd"])


def set_app_id(app_id: str = APP_ID) -> None:
    """The launcher's own taskbar button (not Python's)."""
    w = _win()
    if w is not None:
        with contextlib.suppress(AttributeError, OSError):
            w[0].windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)


# ---------------------------------------------------------------- checking a Savage Wind folder

@dataclass
class Screen:
    """One plain screen: a big heading, one line, buttons (the first is the main one), technical text under
    Details. A screen that doesn't block is a warning: the player may go on."""
    key: str
    heading: str
    line: str
    buttons: list[tuple[str, str]]    # (label, action)
    details: str = ""
    blocks: bool = True


# key: (heading, line, buttons, blocks). {folder}, {version}, {drive} and {port} are filled in.
SCREENS = {
    "not_found": ("Where is Savage Wind?", "Pick the folder that has savagewind.exe in it.",
                  [("Choose your Savage Wind folder", "choose")], True),
    "wrong_pick": ("That's not the Savage Wind folder", "There's no savagewind.exe in {folder}.",
                   [("Choose again", "choose")], True),
    "other_version": ("This Savage Wind is version {version}",
                      f"Breathalyzer works with Savage Wind {SUPPORTED} only.",
                      [("Open Savage Wind's download page", "download"), ("Choose another folder", "choose")], True),
    "no_version": ("This Savage Wind's version can't be read",
                   f"Breathalyzer works with Savage Wind {SUPPORTED} only.",
                   [("Open Savage Wind's download page", "download"), ("Choose another folder", "choose")], True),
    "not_set_up": ("Set up the game files first",
                   "Open Savage Wind and press Set up game files (once, about 10 minutes). Come back after.",
                   [("Open Savage Wind", "open_game"), ("Check again", "recheck")], True),
    "other_game": ("Your game files are not 1.9.0", "Breathalyzer needs BotW 1.9.0 game files.",
                   [("Open Savage Wind", "open_game"), ("Check again", "recheck")], True),
    "data_moved": ("Your game files are in another folder", "Breathalyzer can't use a moved data folder yet.",
                   [("Close", "close")], True),
    "moved": ("Savage Wind isn't in {folder} any more", "Did you move it?",
              [("Find it again", "search"), ("Choose your Savage Wind folder", "choose")], True),
    "cant_write": ("Breathalyzer can't save here",
                   "Move {folder} out of Program Files (or another protected folder).",
                   [("Choose another folder", "choose")], True),
    "in_zip": ("Unpack Breathalyzer first", "Right-click the zip, Extract All, then start it from the new folder.",
               [("Close", "close")], True),
    "low_space": ("Not enough free space", "Code mods need about 2 GB free on {drive}.",
                  [("Go on anyway", "continue"), ("Open the mods folder", "open_library")], False),
    "list_damaged": ("Your mod list can't be read", "Your mods are still in the mods folder.",
                     [("Start a fresh list", "fresh_list"), ("Open the mods folder", "open_library")], True),
    "game_open": ("Savage Wind is already open", "Close it, then press Play.", [("OK", "close")], True),
}


def screen(key: str, details: str = "", **values) -> Screen:
    heading, line, buttons, blocks = SCREENS[key]
    return Screen(key, heading.format(**values), line.format(**values), list(buttons), details, blocks)


@dataclass
class Report:
    folder: str
    version: str | None = None        # "0.4.1-alpha.4"
    custom: bool = False              # the version string is right but the exe isn't one we know
    game_build: str | None = None     # main's build id (16 hex digits)
    dlc: bool = False
    free: int | None = None           # bytes free on the library's drive
    library: str | None = None
    mods: int | None = None           # mods in an existing library (None: none there yet)
    screens: list[Screen] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(s.blocks for s in self.screens)

    def first(self) -> Screen | None:
        """The screen to show: the first that blocks, else the first warning."""
        return next((s for s in self.screens if s.blocks), None) or (self.screens[0] if self.screens else None)

    def lines(self) -> list[tuple[bool, str]]:
        """(ok, text) a line each, for the "Found Savage Wind" and "Check my setup" screens."""
        out = []
        if self.version:
            out.append((True, f"Savage Wind {self.version}" + (" (a custom build)" if self.custom else "")))
        keys = {s.key for s in self.screens}
        if self.version and not keys & {"not_set_up", "data_moved"}:
            out.append((self.game_build == GAME_BUILD, "Game files: BotW 1.9.0" if self.game_build == GAME_BUILD
                        else "Game files: not 1.9.0"))
            out.append((True, "DLC: set up" if self.dlc else "DLC: not set up (that's fine)"))
        if self.free is not None:
            out.append((self.free >= MIN_FREE, f"{self.free / 2**30:.1f} GB free for mods"))
        if self.mods is not None:
            out.append((True, f"Found your mod list: {self.mods} mod{'s' if self.mods != 1 else ''}"))
        elif self.library and "list_damaged" not in keys:
            out.append((True, "A new mod list will be made next to Savage Wind"))
        return out


def exe_facts(path: str) -> tuple[str | None, str]:
    """(the Savage Wind version written in the exe, the exe's SHA-256), in one read. The exe has no version info,
    but its "about" text holds the version. When several versions are in it, the supported one wins, else the first."""
    if os.path.getsize(path) > MAX_EXE:
        return None, ""
    with open(path, "rb") as f:
        data = f.read()
    if re.search(re.escape(SUPPORTED.encode("ascii")) + rb"(?![0-9])", data):
        return SUPPORTED, hashlib.sha256(data).hexdigest()
    found = []
    for m in ANCHOR_RE.finditer(data):
        v = VERSION_RE.search(data[max(0, m.start() - 12):m.end()])
        if v:
            found.append(v.group(1).decode("ascii"))
    return (found[0] if found else None), hashlib.sha256(data).hexdigest()


def nearest_existing(path: str) -> str:
    p = os.path.abspath(path)
    while not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def writable(folder: str) -> bool:
    """A test file can be made (and is removed again) in the folder, or in the nearest folder above it that exists."""
    try:
        fd, probe = tempfile.mkstemp(prefix=".breathalyzer-test-", dir=nearest_existing(folder))
    except OSError:
        return False
    os.close(fd)
    with contextlib.suppress(OSError):
        os.remove(probe)
    return True


def in_zip(path: str) -> bool:
    """Started from inside a zip (Windows shows a zip as a folder and runs from a temp copy named after it)."""
    return any(p.lower().endswith(".zip") for p in re.split(r"[\\/]+", os.path.abspath(path)))


def default_library(install: str, env: dict | None = None) -> str:
    """modman's default: SWMOD_LIBRARY, else a "modloader" folder next to (not inside) the Savage Wind folder."""
    env = os.environ if env is None else env
    if env.get(ENV_LIBRARY):
        return os.path.abspath(env[ENV_LIBRARY])
    return os.path.join(os.path.dirname(os.path.abspath(install)), LIBRARY_NAME)


def read_build_id(install: str) -> str | None:
    """main's build id (16 hex digits) from extracted\\exefs, as modman's game_info reads it; None when it can't."""
    try:
        import exefs_patch as ep
        ids = ep.read_module_ids(os.path.join(install, "extracted", "exefs"))
    except Exception:  # (any reason: the check then says the game files aren't right)
        return None
    main = (ids or {}).get("main")
    return main[:16].lower() if isinstance(main, str) else None


def library_state(root: str) -> tuple[int | None, str | None]:
    """(how many mods an existing library has (None: no library there), why it can't be read (None: it can))."""
    path = os.path.join(root, "config.json")
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        return None, f"{path}: {e}"
    mods = data.get("mods") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("tool") != "botw-mod-loader modman" or not isinstance(mods, list):
        return None, f"{path} wasn't written by Breathalyzer, or is damaged"
    return len(mods), None


def check_install(folder: str, *, library: str | None = None, app_dir: str = REPO, env: dict | None = None,
                  why: str = "picked", build_id=read_build_id, disk_usage=shutil.disk_usage,
                  can_write=writable) -> Report:
    """Check a Savage Wind folder (only reads it; a test file goes next to it and into Breathalyzer's folder and is
    removed). why: "picked" (the player chose it), "remembered" (launcher.json named it) or "found"."""
    env = os.environ if env is None else env
    rep = Report(os.path.abspath(folder))
    add = rep.screens.append
    if in_zip(app_dir):
        add(screen("in_zip", f"Breathalyzer runs from {app_dir}"))
        return rep
    if not has_exe(folder):
        add(screen("moved", folder=folder) if why == "remembered" else screen("wrong_pick", folder=folder))
        return rep
    exe = os.path.join(folder, EXE)
    try:
        rep.version, sha = exe_facts(exe)
    except OSError as e:
        add(screen("no_version", f"Can't read {exe}: {e}"))
        return rep
    if rep.version is None:
        add(screen("no_version", f"No version text in {exe} (SHA-256 {sha})"))
        return rep
    if rep.version != SUPPORTED:
        add(screen("other_version", f"{exe}: {rep.version} (SHA-256 {sha})", version=rep.version))
        return rep
    rep.custom = KNOWN_EXES.get(sha) != SUPPORTED
    if env.get(ENV_DATA_ROOT):
        add(screen("data_moved", f"{ENV_DATA_ROOT} is set: {env[ENV_DATA_ROOT]}"))
        return rep
    ex = os.path.join(folder, "extracted")
    need = [os.path.join(ex, "exefs", "main"), os.path.join(ex, "swgame.dll"), os.path.join(ex, "swgame.dll.id")]
    missing = [p for p in need if not os.path.isfile(p)] + \
        ([os.path.join(ex, "romfs")] if not os.path.isdir(os.path.join(ex, "romfs")) else [])
    if missing:
        add(screen("not_set_up", "Missing: " + ", ".join(missing)))
        return rep
    rep.game_build = build_id(folder)
    if rep.game_build != GAME_BUILD:
        add(screen("other_game", f"main's build id: {rep.game_build or 'unreadable'} (1.9.0 is {GAME_BUILD})"))
    with contextlib.suppress(OSError), os.scandir(ex) as it:
        rep.dlc = any(e.is_dir() and re.fullmatch(r"aoc\d*", e.name, re.I) for e in it)
    rep.library = library or default_library(folder, env)
    for place in (rep.library, app_dir):
        if not can_write(place):
            add(screen("cant_write", f"Can't make a file in {nearest_existing(place)}",
                       folder="Savage Wind" if place == rep.library else "Breathalyzer"))
            return rep
    with contextlib.suppress(OSError):
        probe = nearest_existing(rep.library)
        rep.free = disk_usage(probe).free
        if rep.free < MIN_FREE:
            drive = os.path.splitdrive(probe)[0] or probe
            add(screen("low_space", f"{rep.free / 2**30:.1f} GB free on {drive}", drive=drive))
    rep.mods, damaged = library_state(rep.library)
    if damaged:
        add(screen("list_damaged", damaged))
    return rep


def fresh_list(library: str) -> str:
    """Set a damaged config.json aside (config.json.bad-<time>) so a new list can be made; the mods stay. Returns
    the backup's path."""
    src = os.path.join(library, "config.json")
    dst = f"{src}.bad-{time.strftime('%Y%m%d-%H%M%S')}"
    os.replace(src, dst)
    return dst


# ---------------------------------------------------------------- the first run, without a window

class FirstRun:
    """Find Savage Wind, check it, pick the mod library; the window shows `self.screen` (a problem), the folders in
    `self.choices` (several found) or `self.report` (all good: "Found Savage Wind"). finish() remembers the choice.

    places() -> the find_installs arguments; check(folder, library, why) -> Report. library: the mod library to use
    (--library), else the remembered one for the remembered folder, else the one next to the folder."""

    def __init__(self, settings: Settings, places=None, check=None, env: dict | None = None,
                 library: str | None = None):
        self.settings = settings
        self.library = library
        self.places = places or default_places
        self.check = check or (lambda folder, library, why: check_install(folder, library=library, why=why,
                                                                         env=self.env))
        self.env = os.environ if env is None else env
        self.screen: Screen | None = None
        self.choices: list[Found] = []
        self.report: Report | None = None

    def start(self) -> None:
        """The remembered folder when there is one (a problem with it is shown), else a search."""
        s = self.settings
        if s.install:
            self.use(s.install, "remembered")
        else:
            self.search()

    def search(self) -> None:
        found = find_installs(**self.places())
        self.reset()
        if not found:
            self.screen = screen("not_found")
        elif len(found) == 1:
            self.use(found[0].folder, "found")
        else:
            self.choices = found

    def pick(self, path: str) -> None:
        """The folder the player chose (corrected when it is a level too high or too low)."""
        hits = correct_pick(path)
        self.reset()
        if not hits:
            self.screen = screen("wrong_pick", folder=path)
        elif len(hits) == 1:
            self.use(hits[0], "picked")
        else:
            self.choices = [Found(h, "picked") for h in hits]

    def use(self, folder: str, why: str = "picked") -> None:
        self.reset()
        same = bool(self.settings.install) and \
            os.path.normcase(os.path.abspath(folder)) == os.path.normcase(os.path.abspath(self.settings.install))
        lib = self.library or (self.settings.library if same else None)
        rep = self.check(folder, lib, why)
        self.report = rep
        first = rep.first()
        if first is not None and (first.blocks or why != "remembered"):
            self.screen = first

    def go_on(self) -> None:
        """Go on past a warning (low space)."""
        if self.screen is not None and not self.screen.blocks:
            self.screen = None

    def reset(self) -> None:
        self.screen, self.choices, self.report = None, [], None

    @property
    def ready(self) -> bool:
        return self.report is not None and self.report.ok and self.screen is None

    def finish(self, path: str) -> Settings:
        """Remember the Savage Wind folder and the library (launcher.json)."""
        assert self.ready
        self.settings.install, self.settings.library = self.report.folder, self.report.library
        save_settings(path, self.settings)
        return self.settings


# ---------------------------------------------------------------- Breathalyzer Plus: our mods, for the mod picker

BUNDLED_JSON = "bundled-mods.json"    # app\bundled-mods.json (tools/make_release.py --edition plus writes it)
TOOL_RE = re.compile(r"[a-z][a-z0-9_]*\.py")


def tool_command(cmd, tools: str = TOOLS) -> bool:
    """A command of the list: one of our own tools (tools\\<name>.py, there) and its arguments."""
    return isinstance(cmd, list) and bool(cmd) and all(isinstance(x, str) for x in cmd) \
        and bool(TOOL_RE.fullmatch(cmd[0])) and os.path.isfile(os.path.join(tools, cmd[0]))


def app_folder(rel, app_dir: str = REPO) -> str | None:
    """A folder the list names, inside app\\ ("/"-separated, relative); None when it isn't one."""
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or ":" in rel:
        return None
    parts = rel.split("/")
    if any(x in ("", ".", "..") for x in parts):
        return None
    path = os.path.join(app_dir, *parts)
    return path if os.path.isdir(path) else None


def read_bundled(app_dir: str = REPO) -> tuple[str, list[dict]]:
    """(a key for this list, our mods) from app\\bundled-mods.json: ("", []) without one (Breathalyzer without Plus)
    or when it can't be used. A mod that names a folder outside app\\ or a command that isn't one of our tools is
    left out. Each mod: id, title, description, on (its default), why, tags, kind ("code": folder; "recipe": build,
    the commands that build it from the player's game files), after_add, after_on and after_on_makes (commands run
    when the mod is turned on while the library has no after_on_makes yet: the mod's bonus, built only for players
    who use the mod), egg (an easter egg: secret, so the picker and Home never list it, only one switch for all of
    them; it always starts OFF)."""
    try:
        with open(os.path.join(app_dir, BUNDLED_JSON), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return "", []
    if not isinstance(data, dict) or data.get("format") != 1 or not isinstance(data.get("mods"), list):
        return "", []
    tools = os.path.join(app_dir, "tools")
    out = []
    for m in data["mods"]:
        if not isinstance(m, dict) or not all(isinstance(m.get(k), str) and m[k].strip()
                                               for k in ("id", "title", "description")):
            continue
        if m.get("default") not in ("on", "off") or m.get("kind") not in ("code", "recipe"):
            continue
        after = m.get("after_add") or []
        build = m.get("build") or []
        on_cmds = m.get("after_on") or []
        makes = m.get("after_on_makes")
        if not isinstance(after, list) or not isinstance(build, list) or not isinstance(on_cmds, list) \
                or not all(tool_command(c, tools) for c in [*after, *build, *on_cmds]):
            continue
        if on_cmds and not (isinstance(makes, str) and makes.strip()):
            continue   # (after_on needs the id it adds: else it would run at every start)
        egg = m.get("egg") is True
        mod = {"id": m["id"], "title": m["title"], "description": m["description"],
               "on": m["default"] == "on" and not egg,
               "why": m.get("why") if isinstance(m.get("why"), str) else "", "kind": m["kind"],
               "tags": [t for t in m.get("tags") or [] if isinstance(t, str)], "after_add": after,
               "experimental": m.get("experimental") is True, "developer": m.get("developer") is True, "egg": egg}
        if on_cmds:
            mod["after_on"], mod["after_on_makes"] = on_cmds, makes
        if m["kind"] == "code":
            mod["folder"] = app_folder(m.get("folder"), app_dir)
            if mod["folder"] is None:
                continue
        else:
            if not build:
                continue
            mod["build"] = build
        out.append(mod)
    if not out:
        return "", []
    version = data.get("version") if isinstance(data.get("version"), str) else ""
    return f"{version}:" + ",".join(m["id"] for m in out), out


# ---------------------------------------------------------------- what each mod is, for the list

KIND_PLAIN = {"romfs": "Changes game files", "dlc-romfs": "Changes DLC files", "exefs-patch": "Patches the game code",
              "cheats": "Cheats", "native": "Code mod"}


def mod_manifest(mod_folder: str) -> dict:
    """The [mod] table of a mod's mod.toml (at its root, or in the one folder it holds); {} when it has none."""
    paths = [os.path.join(mod_folder, "mod.toml")]
    subs = subfolders(mod_folder) if os.path.isdir(mod_folder) else []
    if len(subs) == 1:
        paths.append(os.path.join(subs[0], "mod.toml"))
    for p in paths:
        try:
            if os.path.getsize(p) > 256 * 1024:
                continue
            with open(p, "rb") as f:
                data = tomllib.loads(f.read().decode("utf-8-sig"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        mod = data.get("mod")
        return mod if isinstance(mod, dict) else {}
    return {}


def first_sentence(text: str) -> str:
    text = " ".join(text.split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return m.group(1) if m else text


def describe_mod(library: str, entry: dict) -> tuple[str, str]:
    """(one line for the list, the whole text for under it) for a library entry: its mod.toml's description, else
    what it holds and where it came from."""
    man = mod_manifest(os.path.join(library, "mods", str(entry.get("id", ""))))
    desc = man.get("description") if isinstance(man.get("description"), str) else ""
    parts = []
    if desc.strip():
        full = " ".join(desc.split())
        short = first_sentence(full)
    else:
        kinds = [KIND_PLAIN[k] for k in entry.get("kinds") or [] if k in KIND_PLAIN]
        short = (", ".join(kinds) or "Nothing the game can use") + "."
        full = short
    src = entry.get("source") if isinstance(entry.get("source"), str) else ""
    if isinstance(entry.get("wiiu"), dict):
        parts.append("Converted from a Wii U mod" + (": some files are still Wii U data, it may look wrong."
                                                    if entry["wiiu"].get("wiiu_data") else "."))
    elif entry.get("bnp"):
        parts.append("Installed from a BCML package.")
    elif entry.get("ukmm"):
        parts.append("Installed from a UKMM package.")
    elif src and not desc.strip():
        ext = os.path.splitext(src)[1].lower()
        parts.append(f"Added from a {ext} file." if ext and not os.path.isdir(src) else "Added from a folder.")
        if short.endswith("."):
            short = f"{short} {parts[-1]}"
    if entry.get("version"):
        parts.append(f"Version {entry['version']}.")
    authors = man.get("authors")
    if isinstance(authors, list) and all(isinstance(a, str) for a in authors) and authors:
        parts.append("By " + ", ".join(authors) + ".")
    if entry.get("problems"):
        parts.append("Some parts of it can't be used: More > Check says which.")
    return short, " ".join([full, *[p for p in parts if p not in short]])


# ---------------------------------------------------------------- what a Savage Wind folder says about itself

def frame_rate(install: str) -> str | None:
    """savagewind.ini's frame_rate as it is written there (None: no file, or no such line)."""
    try:
        with open(os.path.join(install, INI), "rb") as f:
            data = f.read(MAX_INI + 1)
    except OSError:
        return None
    found = [m.group("value").decode("utf-8", "replace").strip() for m in FPS_LINE.finditer(data)]
    found = [v for v in found if v]
    return found[-1] if found else None


# ---------------------------------------------------------------- the frame rate (Settings > Frame rate)

FPS_KEY = "frame_rate"
# Savage Wind's own choices: its README says "60 fps by default, or 30, 90, 120 or no limit"; the game writes
# "frame_rate = off" for no limit, and its set command takes 0 ("unlimited") for it too.
FPS_CHOICES = (("30", "30 fps"), ("60", "60 fps"), ("90", "90 fps"), ("120", "120 fps"), ("off", "No limit"))
FPS_DEFAULT = "60"
FPS_OFF = ("off", "unlimited", "0")
INI_BACKUP = INI + ".breathalyzer-backup"   # the file as it was before Breathalyzer first changed it (made once)
MAX_INI = 1 << 20
FPS_LINE = re.compile(rb"(?m)^(?P<head>(?:\xef\xbb\xbf)?[ \t]*frame_rate[ \t]*=[ \t]*)(?P<value>[^\r\n]*?)"
                      rb"(?P<tail>[ \t]*\r?)$")


class FpsError(Exception):
    """The frame rate can't be changed now. The message is for the player."""


def fps_value(raw: str | None) -> str | None:
    """One of FPS_CHOICES' values for what savagewind.ini says (None: something else)."""
    v = (raw or "").strip().lower()
    if v in FPS_OFF:
        return "off"
    return v if v in {k for k, _ in FPS_CHOICES} else None


def fps_label(value: str | None) -> str:
    return dict(FPS_CHOICES).get(value or "", "")


INI_OTHER_FORMAT = ("Savage Wind's settings file is in a format Breathalyzer doesn't change: use F1, Video in the "
                    "game.")


def ini_format_problem(data: bytes) -> str | None:
    """Why Breathalyzer leaves this savagewind.ini alone, or None: one saved as UTF-16 ("Unicode" in Notepad), or
    with NUL bytes, would be damaged by a plain-text line (and the game may then reset every setting)."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in data:
        return INI_OTHER_FORMAT
    return None


def fps_info(install: str) -> tuple[bool, str | None, str | None, str]:
    """(savagewind.ini is there, the frame rate's value from FPS_CHOICES or None, what the file says, why it can't
    be read or changed: "" when it can). A file without the line means the game's default, 60."""
    path = os.path.join(install, INI)
    if not os.path.isfile(path):
        return False, None, None, ""
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_INI + 1)
    except OSError as e:
        return True, None, None, f"Savage Wind's settings file can't be read: {e.strerror or e}."
    problem = ini_format_problem(data) or ("Savage Wind's settings file is far too big: it may be damaged."
                                           if len(data) > MAX_INI else "")
    if problem:
        return True, None, None, problem
    found = [m.group("value").decode("utf-8", "replace").strip() for m in FPS_LINE.finditer(data)]
    found = [v for v in found if v]
    raw = found[-1] if found else None
    return True, fps_value(raw) if raw is not None else FPS_DEFAULT, raw, ""


def read_fps(install: str) -> tuple[bool, str | None, str | None]:
    """(savagewind.ini is there, the frame rate's value from FPS_CHOICES or None, what the file says). A file
    without the line means the game's default, 60; one that can't be read or is in another format gives
    (True, None, None): fps_info says why."""
    return fps_info(install)[:3]


def set_frame_rate(install: str, value: str, running=None) -> bool:
    """Write the frame rate into savagewind.ini: only the frame_rate line changes (every other byte stays as it is;
    a file without the line gets it at the end). Only while Savage Wind is closed (it writes the file itself), and
    only in a plain-text file (never one saved as UTF-16). The file as it was is kept once, as
    savagewind.ini.breathalyzer-backup. True when the file changed."""
    if value not in {k for k, _ in FPS_CHOICES}:
        raise FpsError(f"{value!r} isn't a frame rate Savage Wind has.")
    running = running or game_running
    path = os.path.join(install, INI)
    if running():
        raise FpsError("Close Savage Wind first: it writes its settings file when it closes.")
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_INI + 1)
    except FileNotFoundError:
        raise FpsError("Start Savage Wind once first: it makes its settings file then.") from None
    except OSError as e:
        raise FpsError(f"Savage Wind's settings file can't be read: {e.strerror or e}.") from None
    if len(data) > MAX_INI:
        raise FpsError("Savage Wind's settings file is far too big: it may be damaged.")
    problem = ini_format_problem(data)
    if problem:
        raise FpsError(problem)
    new_value = value.encode("ascii")
    if FPS_LINE.search(data):
        new = FPS_LINE.sub(lambda m: m.group("head") + new_value + m.group("tail"), data)
    else:
        nl = b"\r\n" if b"\r\n" in data else b"\n"
        new = data + (nl if data and not data.endswith(b"\n") else b"") + FPS_KEY.encode() + b" = " + new_value + nl
    if new == data:
        return False
    backup = os.path.join(install, INI_BACKUP)
    tmp = f"{path}.breathalyzer-{os.getpid()}.tmp"
    try:
        if not os.path.exists(backup):
            shutil.copy2(path, backup)
        with open(tmp, "wb") as f:
            f.write(new)
        if running():   # (started meanwhile: never write under it)
            raise FpsError("Close Savage Wind first: it writes its settings file when it closes.")
        os.replace(tmp, path)
    except OSError as e:
        raise FpsError(f"Savage Wind's settings file can't be changed: {e.strerror or e}.") from None
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)
    return True


def game_env(base: dict, clear: tuple[str, ...]) -> dict:
    """The environment for Savage Wind without mods: this one without the mod variables a deploy sets."""
    gone = {k.upper() for k in clear}
    return {k: v for k, v in base.items() if k.upper() not in gone}


def start_plain(install: str, clear: tuple[str, ...], popen=subprocess.Popen):
    """Start savagewind.exe as it is, without mods (the mod variables cleared). Returns the process."""
    exe = os.path.join(install, EXE)
    return popen([exe], cwd=install, env=game_env(dict(os.environ), clear),
                 creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def open_path(path: str) -> bool:
    """Open a folder in Explorer, or a program (Savage Wind Configuration.exe), as a double-click would."""
    if not path or not os.path.exists(path):
        return False
    try:
        if sys.platform == "win32":
            os.startfile(path)   # noqa: S606 (a folder or a program the player asked to open)
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except OSError:
        return False


def open_page(url: str) -> bool:
    """A web page in the player's browser (the launcher itself never goes online)."""
    import webbrowser
    return webbrowser.open(url)


# ---------------------------------------------------------------- a desktop shortcut (.lnk), written here

ANSI = "mbcs" if sys.platform == "win32" else "latin-1"
LNK_CLSID = bytes.fromhex("0114020000000000c000000000000046")
CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
IID_SHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
IID_PERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"


def write_shortcut(path: str, target: str, args: str = "", workdir: str = "", icon: str = "",
                   description: str = "") -> None:
    """A Windows shortcut (.lnk) to `target`, made by Windows' own shell-link object in this process (ctypes): no
    PowerShell, no script host, nothing started. Raises OSError when it can't."""
    w = _win()
    if w is None:
        raise OSError("shortcuts are made on Windows only")
    ctypes, wintypes = w
    ole32 = ctypes.windll.ole32

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    def guid(text: str) -> GUID:
        g = GUID()
        if ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g)) != 0:
            raise OSError(f"bad GUID {text}")
        return g

    def call(obj, index: int, argtypes: tuple, *args, restype=ctypes.HRESULT):
        """Method `index` of a COM object's table (an HRESULT that is an error raises OSError)."""
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])(obj, *args)

    started = ole32.CoInitializeEx(None, 2) in (0, 1)   # COINIT_APARTMENTTHREADED (S_FALSE: already on)
    link, store = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        clsid, iid = guid(CLSID_SHELL_LINK), guid(IID_SHELL_LINK_W)
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(link))
        if hr != 0 or not link:
            raise OSError(f"can't make a shortcut (error {hr & 0xFFFFFFFF:#x})")
        s = ctypes.c_wchar_p
        call(link, 20, (s,), os.path.abspath(target))           # SetPath
        call(link, 11, (s,), args)                               # SetArguments
        call(link, 9, (s,), workdir or os.path.dirname(target))  # SetWorkingDirectory
        if icon:
            call(link, 17, (s, ctypes.c_int), icon, 0)          # SetIconLocation
        call(link, 7, (s,), description)                         # SetDescription
        pf = guid(IID_PERSIST_FILE)
        call(link, 0, (ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)), ctypes.byref(pf),
             ctypes.byref(store))                                # QueryInterface(IPersistFile)
        call(store, 6, (s, wintypes.BOOL), os.path.abspath(path), True)   # Save
    finally:
        for obj in (store, link):
            if obj:
                call(obj, 2, (), restype=ctypes.c_ulong)         # Release
        if started:
            ole32.CoUninitialize()


def read_shortcut(path: str) -> dict:
    """A shortcut's target, arguments, start-in folder, icon and tooltip, read from the file (MS-SHLLINK)."""
    with open(path, "rb") as f:
        data = f.read()
    size, clsid, flags = struct.unpack_from("<I16sI", data, 0)
    if size != 0x4C or clsid != LNK_CLSID:
        raise ValueError("not a shortcut")
    pos, out = 0x4C, {}
    if flags & 0x1:   # (an id list: skipped)
        pos += 2 + struct.unpack_from("<H", data, pos)[0]
    if flags & 0x2:
        total, head, _, _, base_off = struct.unpack_from("<IIIII", data, pos)
        if head >= 0x24:
            start = end = pos + struct.unpack_from("<I", data, pos + 28)[0]
            while data[end:end + 2] not in (b"\0\0", b""):
                end += 2
            out["target"] = data[start:end].decode("utf-16-le")
        else:
            out["target"] = data[pos + base_off:data.index(b"\0", pos + base_off)].decode(ANSI, "replace")
        pos += total
    wide = 2 if flags & 0x80 else 1   # (IsUnicode)
    for bit, key in ((0x4, "description"), (0x8, "relative"), (0x10, "workdir"), (0x20, "args"), (0x40, "icon")):
        if flags & bit:
            n = struct.unpack_from("<H", data, pos)[0] * wide
            out[key] = data[pos + 2:pos + 2 + n].decode("utf-16-le" if wide == 2 else ANSI, "replace")
            pos += 2 + n
    return out


STARTER = "Start Breathalyzer.exe"     # next to app\ in the player zips (tools/packaging/build_starter.py)


def shortcut_parts(app_dir: str = REPO, python: str | None = None) -> tuple[str, str, str, str]:
    """(target, arguments, start-in folder, icon) of a shortcut that starts this launcher. In the player zips:
    Start Breathalyzer.exe, the starter the player knows (never pythonw.exe straight: antivirus programs flag that
    pattern). From the source: the python next to this one (pythonw.exe: no console window), -I, tools\\launcher.py."""
    top = package_top(app_dir)
    starter = os.path.join(top, STARTER)
    if os.path.basename(app_dir).lower() == "app" and os.path.isfile(starter):
        return starter, "", top, starter
    exe = python or sys.executable or "pythonw.exe"
    folder = os.path.dirname(exe)
    w = os.path.join(folder, "pythonw.exe")
    target = w if os.path.isfile(w) else exe
    icon = os.path.join(app_dir, "assets", "breathalyzer.ico")
    return target, f'-I "{os.path.join(app_dir, "tools", "launcher.py")}"', app_dir, icon if os.path.isfile(icon) \
        else ""


# ---------------------------------------------------------------- .breath files: a double-click adds the mod

BREATH_EXT = ".breath"
BREATH_PROGID = "Breathalyzer.Mod"
BREATH_TYPE_NAME = "Breathalyzer mod"
CLASSES = r"Software\Classes"     # under HKEY_CURRENT_USER: this Windows user only, no admin (tests use another key)
OPEN_DIR = "open"                 # in home_dir(): .breath files a second start passes to the open window
MAX_OPEN_REQUEST = 4096
OPEN_REQUEST_AGE = 120            # seconds: a handed-over .breath older than this is left over (dropped)


def breath_command(app_dir: str = REPO, python: str | None = None) -> tuple[str, str]:
    """(the command a double-click on a .breath runs, its icon): Start Breathalyzer.exe "<file>" in the player zips,
    else this Python with tools\\launcher.py."""
    target, args, _, icon = shortcut_parts(app_dir, python)
    cmd = f'"{target}"' + (f" {args}" if args else "") + ' "%1"'
    return cmd, f'"{icon or target}",0'


def _winreg():
    if sys.platform != "win32":
        raise OSError("file types are set on Windows only")
    import winreg
    return winreg


def _delete_tree(reg, root, path: str) -> None:
    """Delete a registry key and everything under it (only keys Breathalyzer made)."""
    try:
        with reg.OpenKey(root, path, 0, reg.KEY_READ | reg.KEY_WRITE) as k:
            while True:
                try:
                    sub = reg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(reg, root, f"{path}\\{sub}")
    except FileNotFoundError:
        return
    reg.DeleteKey(root, path)


def _read_default(reg, root, path: str) -> str | None:
    try:
        with reg.OpenKey(root, path) as k:
            value, _ = reg.QueryValueEx(k, "")
            return value if isinstance(value, str) else None
    except OSError:
        return None


# Where Explorer keeps the program the player picked with "Always use this app" (it wins over Software\Classes)
FILE_EXTS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"


def breath_choice(choice_base: str = FILE_EXTS) -> str | None:
    """The program Explorer opens .breath files with because the player picked it ("Always use this app"): its
    ProgId, or None when there is no such choice. Only reads."""
    try:
        reg = _winreg()
    except OSError:
        return None
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, f"{choice_base}\\{BREATH_EXT}\\UserChoice") as k:
            value, _ = reg.QueryValueEx(k, "ProgId")
    except OSError:
        return None
    return value if isinstance(value, str) and value else None


def breath_state(base: str = CLASSES, command: str | None = None, choice_base: str | None = None) -> str | None:
    """Whether a double-click on a .breath opens Breathalyzer: "here" (this copy), "other" (another Breathalyzer
    folder, an older one), "taken" (registered, but the player picked another program for .breath files, which
    wins), None (not set, or set to another program). choice_base: where Explorer's choice is (tests: under their
    own key; default: the real one for the real Software\\Classes, else <base's parent>\\FileExts). Only reads."""
    try:
        reg = _winreg()
    except OSError:
        return None
    root = reg.HKEY_CURRENT_USER
    if _read_default(reg, root, f"{base}\\{BREATH_EXT}") != BREATH_PROGID:
        return None
    have = _read_default(reg, root, f"{base}\\{BREATH_PROGID}\\shell\\open\\command")
    if not have:
        return None
    if choice_base is None:
        choice_base = FILE_EXTS if base == CLASSES else base.rsplit("\\", 1)[0] + "\\FileExts"
    picked = breath_choice(choice_base)
    if picked is not None and picked != BREATH_PROGID:
        return "taken"
    want = command if command is not None else breath_command()[0]
    return "here" if os.path.normcase(have) == os.path.normcase(want) else "other"


def register_breath(command: str, icon: str, base: str = CLASSES) -> None:
    """Make a double-click on a .breath file run `command` (it ends in "%1"), for this Windows user only
    (HKEY_CURRENT_USER\\Software\\Classes: no admin). Raises OSError when it can't."""
    reg = _winreg()
    root = reg.HKEY_CURRENT_USER
    with reg.CreateKeyEx(root, f"{base}\\{BREATH_EXT}", 0, reg.KEY_WRITE) as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, BREATH_PROGID)
    with reg.CreateKeyEx(root, f"{base}\\{BREATH_EXT}\\OpenWithProgids", 0, reg.KEY_WRITE) as k:
        reg.SetValueEx(k, BREATH_PROGID, 0, reg.REG_SZ, "")
    with reg.CreateKeyEx(root, f"{base}\\{BREATH_PROGID}", 0, reg.KEY_WRITE) as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, BREATH_TYPE_NAME)
    with reg.CreateKeyEx(root, f"{base}\\{BREATH_PROGID}\\DefaultIcon", 0, reg.KEY_WRITE) as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, icon)
    with reg.CreateKeyEx(root, f"{base}\\{BREATH_PROGID}\\shell\\open\\command", 0, reg.KEY_WRITE) as k:
        reg.SetValueEx(k, "", 0, reg.REG_SZ, command)
    if base == CLASSES:
        shell_changed()


def unregister_breath(base: str = CLASSES) -> bool:
    """Undo register_breath: Breathalyzer's file type goes, and .breath too when it names Breathalyzer (a .breath
    another program took over is left to it). True when something was removed."""
    reg = _winreg()
    root = reg.HKEY_CURRENT_USER
    ext = f"{base}\\{BREATH_EXT}"
    gone = _read_default(reg, root, f"{base}\\{BREATH_PROGID}") is not None
    _delete_tree(reg, root, f"{base}\\{BREATH_PROGID}")
    with contextlib.suppress(OSError):
        with reg.OpenKey(root, f"{ext}\\OpenWithProgids", 0, reg.KEY_WRITE) as k:
            reg.DeleteValue(k, BREATH_PROGID)
            gone = True
    with contextlib.suppress(OSError):
        with reg.OpenKey(root, f"{ext}\\OpenWithProgids", 0, reg.KEY_READ) as k:
            empty = reg.QueryInfoKey(k)[:2] == (0, 0)
        if empty:
            reg.DeleteKey(root, f"{ext}\\OpenWithProgids")
    if _read_default(reg, root, ext) == BREATH_PROGID:
        with reg.OpenKey(root, ext, 0, reg.KEY_WRITE) as k:
            reg.DeleteValue(k, "")
        gone = True
    with contextlib.suppress(OSError):
        with reg.OpenKey(root, ext, 0, reg.KEY_READ) as k:
            empty = reg.QueryInfoKey(k)[:2] == (0, 0)
        if empty:
            reg.DeleteKey(root, ext)
    if base == CLASSES:
        shell_changed()
    return gone


def shell_changed() -> None:
    """Tell Explorer that file types changed (the .breath icon shows at once)."""
    w = _win()
    if w is not None:
        with contextlib.suppress(AttributeError, OSError):
            w[0].windll.shell32.SHChangeNotify(0x08000000, 0, None, None)   # SHCNE_ASSOCCHANGED, SHCNF_IDLIST


def pass_file(path: str, folder: str) -> str:
    """Hand a .breath file to the open launcher window (a second start, from a double-click): a small request file
    in `folder` that the window picks up. Returns its path."""
    os.makedirs(folder, exist_ok=True)
    name = f"{time.time_ns()}-{os.getpid()}.txt"
    tmp = os.path.join(folder, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(os.path.abspath(path))
    os.replace(tmp, os.path.join(folder, name))
    return os.path.join(folder, name)


def take_files(folder: str, now: float | None = None) -> list[str]:
    """The .breath files passed with pass_file, oldest first; each request is removed once read. One older than
    OPEN_REQUEST_AGE (left over: the window had closed meanwhile) is dropped, never asked about later."""
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".txt"))
    except OSError:
        return []
    now = time.time() if now is None else now
    out = []
    for n in names:
        p = os.path.join(folder, n)
        try:
            fresh = now - os.path.getmtime(p) <= OPEN_REQUEST_AGE
            with open(p, encoding="utf-8") as f:
                text = f.read(MAX_OPEN_REQUEST + 1).strip()
        except (OSError, UnicodeDecodeError):
            fresh, text = False, ""
        with contextlib.suppress(OSError):
            os.remove(p)
        if fresh and text and len(text) <= MAX_OPEN_REQUEST and "\n" not in text \
                and text.lower().endswith(BREATH_EXT):
            out.append(text)
    return out


# ---------------------------------------------------------------- command line

def size_arg(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' isn't a number") from None
    if not MIN_SIZE <= n <= MAX_SIZE:
        raise argparse.ArgumentTypeError(f"use {MIN_SIZE} to {MAX_SIZE}")
    return n


def port_arg(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' isn't a port number") from None
    if not 1 <= n <= 65535:
        raise argparse.ArgumentTypeError(f"{n} isn't a port: use 1 to 65535")
    return n


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="launcher.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--library", help="the mod library folder (default: the one the first run picked, else "
                                      "'modloader' next to the Savage Wind folder)")
    ap.add_argument("--size", type=size_arg, help=f"text size in points, {MIN_SIZE}-{MAX_SIZE} (default: the last "
                                                  f"one, else {DEFAULT_SIZE})")
    ap.add_argument("--dark", action="store_true", help="light text on a dark background")
    ap.add_argument("--port", type=port_arg, help="control port for the cheats (default 7777)")
    ap.add_argument("--search-in", action="append", metavar="FOLDER",
                    help="look for Savage Wind only in these folders and one level below (for tests)")
    ap.add_argument("file", nargs="?", help="a .breath file to add (what a double-click on one passes)")
    return ap


ERROR_FILE = "launcher-error.txt"     # in home_dir(): what went wrong when the launcher couldn't start
START_FAILED = ("Breathalyzer couldn't start.", "Unpack the zip again, or allow Breathalyzer in your antivirus.",
                "What went wrong is in this file:")


def write_error(text: str, env: dict | None = None) -> str:
    """Write what went wrong to launcher-error.txt (home_dir); returns its path, or "" when it can't be written."""
    path = os.path.join(home_dir(env), ERROR_FILE)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  Breathalyzer couldn't start (Python {sys.version.split()[0]},"
                    f" {REPO})\n\n{text}")
        return path
    except OSError:
        return ""


def message_box(heading: str, text: str) -> bool:
    """A Windows message box (user32, through ctypes: it needs no Tk). Under pythonw it is the only way a player sees
    why the launcher didn't start. False when there is none (not Windows)."""
    if sys.platform != "win32":
        print(f"{heading}\n{text}", file=sys.stderr)
        return False
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, f"{heading}\n\n{text}", APP_NAME, 0x10 | 0x10000)  # (error, on top)
        return True
    except Exception:  # (nothing more can be done)
        return False


def clean_tcl_env(env: dict | None = None) -> list[str]:
    """Drop TCL_LIBRARY and TK_LIBRARY (another program may leave them set: Tk would then load that program's Tcl and
    fail). Must run before tkinter is imported. Returns the names it dropped."""
    env = os.environ if env is None else env
    gone = [k for k in ("TCL_LIBRARY", "TK_LIBRARY") if k in env]
    for k in gone:
        del env[k]
    return gone


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    clean_tcl_env()
    try:
        import modman_gui as gui   # (the window; it imports modman, which takes a moment)
        return gui.launcher_main(args)
    except Exception:  # (pythonw has no console: without this a failed start shows nothing at all)
        import traceback
        path = write_error(traceback.format_exc())
        message_box(START_FAILED[0], START_FAILED[1] + (f"\n\n{START_FAILED[2]}\n{path}" if path else ""))
        return 3


if __name__ == "__main__":
    sys.modules.setdefault("launcher", sys.modules[__name__])   # (the window imports it by name: one copy)
    sys.exit(main())
