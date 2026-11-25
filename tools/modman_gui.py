#!/usr/bin/env python3
"""Breathalyzer's mod manager window (tools/modman.py): big text, high contrast, everything works with the keyboard.

    python tools/modman_gui.py [--library <folder>] [--size 18] [--dark] [--port N]

It uses the same mod library as modman: --library, else the SWMOD_LIBRARY variable, else the "modloader" folder
next to the Savage Wind folder. Every button runs the modman command of the same name, so it does exactly what the
command does:

    Turn on/off, Move     modman enable/disable/move, in this process (they take a moment)
    Add mod, Remove,      `python tools/modman.py <command>` in the background, so the window keeps working during
    Deploy, Check         a deploy that rebuilds the game code (10-20 minutes)
    Play                  modman run (--port N), in a background thread of this process: the game gets this window's
                          console (or none), as it gets the console with `modman run`; the cheats' output goes to
                          Details
Deploy and Play (and Add mod, when there is no library yet) first ask for the Savage Wind folder (modman init) when
modman doesn't know one that is there.

Keys: Tab and the arrow keys move around, Space or Enter presses, Space in the list turns a mod on or off,
Ctrl+Up/Down moves it, Delete removes it (after a question), Ctrl + and Ctrl - change the text size, F5 reads the
list again.

The launcher's window is here too (LauncherView, started by tools/launcher.py: docs/launcher.md): the first-run
screens, Home with one big Play button that does everything (the game's Mods page changes, deploy, run) and the mods
with what they do, and Settings. Its More button opens this Mod Manager window.
"""
from __future__ import annotations

import argparse
import contextlib
import locale
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

TK_ERROR = ""   # why tkinter couldn't be loaded (the launcher says it)
try:
    import tkinter as tk
    from tkinter import filedialog, ttk
    from tkinter import font as tkfont
except ImportError as _e:  # (the logic works without Tk; only the window needs it)
    tk = None
    TK_ERROR = f"{type(_e).__name__}: {_e}"

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import launcher as la  # noqa: E402 (tools/launcher.py: finding and checking Savage Wind, the launcher's settings)

MODMAN_PY = os.path.join(TOOLS, "modman.py")
MODMENU_PY = os.path.join(TOOLS, "modmenu.py")
CONTROL_PORT = 7777   # the game's control port (modman.DEFAULT_PORT): open = developer mode (docs/game-basics.md)
# our mods whose commands work only in developer mode: Play turns it on while one is ON (bundled-mods.toml's
# developer = true adds more)
DEV_MODS = ("botw-mod-loader.basics-inf-stamina", "botw-mod-loader.basics-teleport-up")

TITLE = "Breathalyzer - Mods"
# The window icon, when it is there (the window works without it): .ico for Windows, .png elsewhere.
APP_ICONS = [os.path.join(os.path.dirname(TOOLS), "assets", "breathalyzer" + ext) for ext in (".ico", ".png")]
# (tools/mod_studio.py uses the same icon through set_app_icon)
DEFAULT_SIZE, MIN_SIZE, MAX_SIZE = 18, 10, 40
MIN_LIST_ROWS = 3        # the list always has room for this many mods (bigger text than leaves that is refused)
DETAILS_LINES = 4        # the smallest Details
PAD = 16
POLL_MS = 100
MAX_LINES, DROP_LINES = 50000, 10000      # output kept per command (the oldest lines go first)
REBUILD_NOTE = "If the game code must be rebuilt, this takes 10-20 minutes."
PRESS_DEPLOY = "Press Deploy to use the change."
# A mod with code added from a folder, a .zip or a DLL comes in OFF until the player's OK (modman add: needs_ok). The
# window gives that OK: Turn on asks the Careful question (BigText.code_question), then `enable --allow-code`.
CODE_OFF_LINE = "Added OFF: it has code"   # (modman cmd_add's line for it)
CODE_OK_HINT = "If you trust who made it, press Turn on: it asks for your OK."
KEYS_HINT = "Keys: Tab, arrows, Space, Enter. Ctrl + and Ctrl - change the text size."
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

KIND_LABELS = {"romfs": "RomFS", "dlc-romfs": "DLC RomFS", "exefs-patch": "Patch", "cheats": "Cheats",
               "native": "Code"}
BUSY = {"add": "Adding the mod", "remove": "Removing the mod", "deploy": "Deploying", "check": "Checking",
        "play": "Starting the game", "saves": "Copying your save", "profile": "Changing the mod set"}
BUTTON = {"add": "Add mod", "remove": "Remove", "deploy": "Deploy", "check": "Check", "play": "Play",
          "saves": "the save copy", "profile": "the mod set change"}
# The window's own QoL commands (docs/mod-manager.md "Saves, crashes and mod sets"): what the status says when one
# fails (the reason follows), and the ones whose whole answer is in Details.
QOL_FAILED = {"enable": "Not changed.", "disable": "Not changed.", "last-good": "Not changed.",
              "crash": "Nothing was turned off.", "saves": "The save wasn't changed.",
              "profile": "The mod set wasn't changed.", "picture": "The picture wasn't changed.",
              "font": "The font wasn't changed.", "settings": "The setting wasn't changed.",
              "info": "No info.", "conflicts": "No answer.", "changes": "No answer."}
NO_MODS = "--no-mods"
GATES = {"move_down": "move", "play_plain": "play"}   # the window's buttons that ask can() as another one
# What run prints that the status shows while the game runs.
PLAY_MARKS = ("Started Savage Wind", "Started cheats", "Warning: ", "The cheats stopped", "The game closed before",
              "Savage Wind closed", "Your save was backed up first", "Saving without a mod")
PLAY_WARNINGS = (("changed since the last deploy", "Your newer changes aren't in it: deploy first next time."),
                 ("code patches are not active", "Code patches are off (deployed without the rebuild)."),
                 ("hooks are not active", "Hooks are off (deployed without the rebuild)."))
# modman's messages name its commands and options; this is what to do in the window instead. (The first letter
# follows the case of what it replaces.)
PLAIN = tuple((re.compile(rx), new) for rx, new in (
    (r"Run: python tools[\\/]modman\.py deploy", "Press Deploy"),
    (r"python tools[\\/]modman\.py disable <id>", "the Turn off button"),
    (r"\bRun deploy\b", "Press Deploy"),
    (r"[Rr]un: python tools[\\/]modman\.py init <Savage Wind folder>", "Press the button again to pick it"),
    (r"[Rr]un: python tools[\\/]modman\.py init\b", "Press Add mod to start one"),
    (r"Pass your Savage Wind folder: python tools[\\/]modman\.py init <folder>", "Pick the folder that has it."),
    (r"\(run init <folder>\)", "(Deploy asks for it)"),
    (r"\(run init\)", "(Add mod makes one)"),
    (r"run init and deploy again", "press Deploy (it asks for the Savage Wind folder)"),
    (r"Pick a free one with --port\.", "Close it and press Play again, or start this window with --port <number>.")))


# ---------------------------------------------------------------- what modman prints, per thread

class ThreadOut:
    """Stands in for sys.stdout: what a thread prints inside captured() goes to that thread's sink, the rest to the
    stream it replaced (none under pythonw)."""
    encoding = "utf-8"
    errors = "replace"

    def __init__(self, real):
        self.real = real
        self.sinks: dict[int, object] = {}

    def write(self, s: str) -> int:
        sink = self.sinks.get(threading.get_ident())
        if sink is not None:
            sink(s)
        elif self.real is not None:
            try:
                self.real.write(s)
            except (OSError, ValueError):
                pass
        return len(s)

    def flush(self) -> None:
        if threading.get_ident() not in self.sinks and self.real is not None:
            try:
                self.real.flush()
            except (OSError, ValueError):
                pass

    def isatty(self) -> bool:
        return False


_ROUTER_LOCK = threading.Lock()


@contextlib.contextmanager
def captured(on_line):
    """Inside, what this thread prints goes to on_line, a line at a time (without the line end)."""
    with _ROUTER_LOCK:  # (two threads starting at once must share one router)
        out = sys.stdout
        if not isinstance(out, ThreadOut):
            out = sys.stdout = ThreadOut(out)
    buf: list[str] = []

    def sink(s: str) -> None:
        buf.append(s)
        if "\n" in s:
            *done, rest = "".join(buf).split("\n")
            buf[:] = [rest] if rest else []
            for line in done:
                on_line(line.rstrip("\r"))

    tid = threading.get_ident()
    out.sinks[tid] = sink
    try:
        yield
    finally:
        out.sinks.pop(tid, None)
        if buf:
            on_line("".join(buf).rstrip("\r"))


def decode_line(raw: bytes) -> str:
    """A line from `python tools/modman.py`: UTF-8, else the ANSI code page Python writes a pipe in. A line that
    a progress counter rewrote with \\r shows only its last state."""
    raw = raw.rstrip(b"\r\n").rsplit(b"\r", 1)[-1]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(locale.getpreferredencoding(False) or "latin-1", "replace")


# ---------------------------------------------------------------- data

@dataclass
class Row:
    number: int
    id: str
    name: str
    on: bool
    kinds: list[str]
    version: str | None = None
    about: str = ""            # one line about the mod (the launcher's list; Backend.describe)
    full: str = ""             # all of it
    secret: bool = False       # [mod] secret (an easter egg, a hidden extra's part): never listed
    needs_ok: bool = False     # it has code and came in OFF: turning it on asks first (code_question, --allow-code)
    code: tuple = ()           # its code files (modman's entry "code"), for that question

    def cells(self) -> tuple[str, str, str, str, str]:
        return (str(self.number), "On" if self.on else "Off", self.name, kind_text(self.kinds), self.version or "-")


@dataclass
class Listing:
    root: str
    rows: list[Row] = field(default_factory=list)
    exists: bool = True        # the library has a config.json
    error: str | None = None   # why it can't be read


@dataclass
class Status:
    kind: str                  # "ok", "warn", "error", "busy" or "info"
    lines: list[str]


@dataclass
class Job:
    action: str                # add, remove, deploy, check, play; enable, disable, move (quick ones)
    args: list[str]            # the modman command line, without --library
    name: str = ""             # the mod it is about
    mod_id: str = ""
    number: int = 0            # the mod's number when it started
    in_process: bool = False
    script: str = ""           # the tool it runs (default tools/modman.py)
    lines: list[str] = field(default_factory=list)
    dropped: int = 0           # lines no longer kept (the oldest)
    code: int | None = None
    started: float = 0.0
    ended: float | None = None
    game_started: bool = False
    proc: object = None
    cancelled: bool = False
    hide: tuple = ()           # words its output never shows (a secret mod's id, title, folder): see hide_words
    env: dict = field(default_factory=dict)   # play: more variables for the game (the Multiplayer page's save copy)
    show: str = ""             # the status head when the answer is in Details (info, conflicts, ...); {who}: its mod
    picture: str = ""          # info: the mod's 128 px picture (a PNG), shown at the top of Details

    @property
    def slot(self) -> str:
        return "play" if self.action == "play" else "main"

    def last_line(self) -> str:
        return next((x.strip() for x in reversed(self.lines) if x.strip()), "")


def kind_text(kinds: list[str]) -> str:
    return ", ".join(KIND_LABELS.get(k, k) for k in kinds) or "-"


SECRET_WORD = "(secret)"


def hide_words(text: str, words) -> str:
    """text with each of `words` (a secret mod's id, title, folder: any case) written as (secret)."""
    for w in sorted({w for w in words if isinstance(w, str) and len(w) > 2}, key=len, reverse=True):
        text = re.sub(re.escape(w), SECRET_WORD, text, flags=re.IGNORECASE)
    return text


def clip(s: str, n: int = 120) -> str:
    return s if len(s) <= n else s[:n - 3] + "..."


def n_of(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def plain(s: str) -> str:
    for rx, new in PLAIN:
        s = rx.sub(lambda m, new=new: new if m.group(0)[0].isupper() else new[0].lower() + new[1:], s)
    return clip(s)


# ---------------------------------------------------------------- 1-3 short lines for a result

def error_lines(text: list[str]) -> list[str]:
    """modman's error (without "Error: "), and its first reason when it lists some."""
    for i, line in enumerate(text):
        if line.startswith("Error: "):
            msg = line[len("Error: "):]
            for prefix in ("Not added: ", "Not removed: "):
                msg = msg.removeprefix(prefix)
            out = [plain(msg)]
            reasons = [x.strip()[2:] for x in text[i + 1:] if x.startswith("  - ")]
            if reasons:
                more = f" (and {len(reasons) - 1} more: see Details)" if len(reasons) > 1 else ""
                out.append(plain(reasons[0]) + more)
            return out
    return []


def failed(head: str, text: list[str]) -> Status:
    msg = error_lines(text) or ([plain(text[-1].strip())] if text else ["Something went wrong: see Details."])
    return Status("error", [head, *msg][:3])


def attention_count(text: list[str]) -> int:
    n, inside = 0, False
    for line in text:
        if line == "Needs attention:":
            inside = True
        elif inside and line.startswith("  - "):
            n += 1
        elif inside:
            inside = False
    return n


def summarize(job: Job, name: str = "") -> Status:
    """What the status area shows for a command that finished (or, for Play, one that runs)."""
    a, code = job.action, job.code
    text = [x.rstrip() for x in job.lines if x.strip()]
    ok = code == 0
    who = name or job.name or "The mod"
    if a in ("enable", "disable"):
        return Status("ok", [f"{who} is {'on' if a == 'enable' else 'off'}.", PRESS_DEPLOY]) if ok \
            else failed("Not changed.", text)
    if a == "move":
        return Status("ok", [f"{who} is now number {job.args[2]}.", PRESS_DEPLOY]) if ok \
            else failed("Not moved.", text)
    if a == "add":
        if not ok:
            return failed("Not added.", text)
        if any(x.startswith(CODE_OFF_LINE) for x in text):   # (no "Press Deploy": it is off until the player's OK)
            return Status("warn", [f"Added {who}, but it is off: it has code.",
                                   "Code runs on your PC like a program, with your rights.", CODE_OK_HINT])
        unused = [x for x in text if x.strip().startswith("not used:")]
        wiiu = any(x.strip().startswith("Left as Wii U data") for x in text)  # (a converted Wii U mod)
        if wiiu:
            more = ["Some files are still Wii U data: the game may crash. See Details."]
        elif unused:
            more = [f"{n_of(len(unused), 'part')} of it can't be used: see Details."]
        else:
            more = []
        return Status("warn" if more else "ok", [f"Added {who}.", *more, "Press Deploy to use it."])
    if a == "remove":
        if not ok:
            return failed("Not removed.", text)
        out = [f"Removed {who}.", "Only the library copy was deleted."]
        if any("Run deploy" in x for x in text):
            out.append("Press Deploy to take it out of the game.")
        return Status("ok", out)
    if a == "deploy":
        if not ok:
            return failed("Not deployed.", text)
        first = next((x for x in text if x.startswith(("Deployed ", "No mods are on"))), "")
        m = re.match(r"Deployed (\d+) mod", first)
        head = f"Deployed {n_of(int(m.group(1)), 'mod')}." if m else \
            "Deployed: no mods are on." if first else "Deploy done."
        need = attention_count(text)
        out = [head]
        if need:
            out.append(f"{n_of(need, 'thing')} need{'s' if need == 1 else ''} attention: see Details.")
        out.append("Press Play to start the game.")
        return Status("warn" if need else "ok", out)
    if a == "init":
        if not ok:
            return failed("The Savage Wind folder isn't set.", text)
        setup = [x for x in text if x.startswith("Set up the game files")]
        return Status("ok", [f"Savage Wind folder: {clip(job.args[1])}", *setup[:1]])
    if a == "check":
        fails = [x[4:].strip() for x in text if x.startswith("FAIL")]
        warns = [x[4:].strip() for x in text if x.startswith("WARN")]
        if code not in (0, 1) or (code == 1 and not fails):
            return failed("The check didn't finish.", text)
        rest = ["See Details for the rest."] if len(fails) + len(warns) > 1 else []
        if fails:
            return Status("error", [f"Check: {n_of(len(fails), 'problem')}.", plain(fails[0]), *rest])
        if warns:
            return Status("warn", [f"Check: {n_of(len(warns), 'warning')}.", plain(warns[0]), *rest])
        return Status("ok", ["Check: all good."])
    if a == "play":
        if not any(x.startswith("Started Savage Wind") for x in text):
            return Status("busy", ["Starting the game..."]) if code is None else \
                failed("The game didn't start.", text)
        closed = any(x.startswith("Savage Wind closed") for x in text)
        out, kind = ["The game closed." if closed else "Game started."], "ok"
        if NO_MODS in job.args and not closed:
            out[0] = "Game started without mods."
        if any(x.startswith("Saving without a mod") for x in text):   # (S3: fewer mods than the last start)
            out.append("Your save was backed up first: a mod is off now, see Details.")
            kind = "warn"
        elif any(x.startswith("Your save was backed up first") for x in text):
            out.append("Your save was backed up first.")
        stopped = next((i for i, x in enumerate(text) if x.startswith("The cheats stopped right away")), None)
        if stopped is not None:  # (first: it says why, from what cheats.py printed after run's "Waiting" line)
            waited = next((i for i in range(stopped - 1, -1, -1) if text[i].startswith("Waiting for the game")), None)
            why = [x.strip() for x in text[waited + 1:stopped] if x.strip()] if waited is not None else []
            out += ["The cheats stopped right away: see Details.", *[plain(x) for x in why[-1:]]]
            kind = "warn"
        for w in (x for x in text if x.startswith("Warning: ")):
            out.append(next((short for key, short in PLAY_WARNINGS if key in w), plain(w)))
            kind = "warn"
        if stopped is None and any(x.startswith("The game closed before") for x in text):
            out = ["The game closed.", "It closed before the cheats could start."]
            kind = "warn"
        elif stopped is None and not closed and any(x.startswith("Started cheats") for x in text):
            out.append("Cheats are on.")
        return Status(kind, out[:3])
    if a in QOL_FAILED:
        if not ok:
            return failed(QOL_FAILED[a], text)
        if job.show:
            return Status("ok", [job.show.format(who=who), *[plain(x.strip()) for x in text[:2]]])
        if a == "picture":
            chosen = "--clear" not in job.args and len(job.args) > 2
            return Status("ok", [f"Picture chosen for {who}: {os.path.basename(job.args[2])}." if chosen
                                 else plain(text[0]) if text else "Done.", PRESS_DEPLOY])
    return Status("ok" if ok else "error", [plain(x.strip()) for x in text[:3]] or ["Done." if ok else "Failed."])


# ---------------------------------------------------------------- modman, as the window uses it

def load_modman():
    import modman
    return modman


def console_python() -> str:
    """python.exe for the background commands, also when this window runs under pythonw.exe."""
    exe = sys.executable or "python"
    folder, base = os.path.split(exe)
    if base.lower() == "pythonw.exe" and os.path.isfile(os.path.join(folder, "python.exe")):
        return os.path.join(folder, "python.exe")
    return exe


def kill_tree(proc) -> None:
    """Stop a background command and what it started (the compilers of a rebuild)."""
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15,
                           creationflags=NO_WINDOW)
    with contextlib.suppress(OSError):
        if proc.poll() is None:
            proc.kill()


class PipedProc:
    """A started cheats.py whose output a thread reads into on_line. It ends like the process it wraps; once that
    process is done, its last lines have been passed on (before modman says anything about it)."""

    def __init__(self, proc, on_line):
        self.proc = proc
        self.reader = threading.Thread(target=self._read, args=(on_line,), daemon=True, name="cheats output")
        self.reader.start()

    def _read(self, on_line) -> None:
        try:
            for raw in self.proc.stdout:
                on_line(decode_line(raw))
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError):
                self.proc.stdout.close()

    def poll(self):
        code = self.proc.poll()
        if code is not None:
            self.reader.join(5)
        return code

    def wait(self, timeout=None):
        code = self.proc.wait(timeout)
        self.reader.join(5)
        return code

    def __getattr__(self, name):
        return getattr(self.proc, name)


class PipedCheats:
    """Stands in for the subprocess module inside modman while Play runs. modman.run starts tools/cheats.py on this
    process's console (none under pythonw), so the window would never see why the cheats stopped; this starts it
    with its output piped to on_line instead. Everything else goes to the real module unchanged."""

    def __init__(self, real, script: str, on_line):
        self.real, self.script, self.on_line = real, os.path.normcase(script), on_line

    def __getattr__(self, name):
        return getattr(self.real, name)

    def Popen(self, args, **kw):  # noqa: N802 (the name modman calls)
        if not (isinstance(args, (list, tuple)) and any(isinstance(a, str) and os.path.normcase(a) == self.script
                                                        for a in args)):
            return self.real.Popen(args, **kw)
        kw.update(stdin=self.real.DEVNULL, stdout=self.real.PIPE, stderr=self.real.STDOUT,
                  creationflags=kw.get("creationflags", 0) | NO_WINDOW)
        return PipedProc(self.real.Popen(args, **kw), self.on_line)


class Backend:
    """Runs modman commands on one library. `mm` is the modman module (a fake one in tests); `spawn` starts
    `python tools/modman.py ...` (subprocess.Popen; a fake in tests)."""

    def __init__(self, library: str | None = None, mm=None, spawn=None, python: str | None = None):
        self.mm = mm or load_modman()
        self.root = self.mm.resolve_library(library)   # (--library, else SWMOD_LIBRARY, else next to Savage Wind)
        self.spawn = spawn or subprocess.Popen
        self.python = python or console_python()
        # (config.json: Turn on/off replaces it, and Windows refuses that while another thread has it open)
        self.files = threading.Lock()
        self.describe = None   # (library, entry) -> (about, full) for each row (the launcher sets it)
        # The secret mods ([mod] secret: an easter egg, a hidden extra's parts) are left out of the list (More...,
        # Mod Manager). The launcher keeps them (marked secret): its easter-egg switch needs their state, and Home
        # leaves them out itself (LaunchController.shown_rows).
        self.keep_secret = False

    def listing(self) -> Listing:
        try:
            lib = self.mm.Library.load(self.root)
        except (self.mm.ModmanError, OSError, ValueError) as e:
            return Listing(self.root, [], True, str(e))
        if lib is None:
            return Listing(self.root, [], False)
        rows = [Row(k, m["id"], m.get("name") or m["id"], bool(m.get("enabled")), list(m.get("kinds") or []),
                    m.get("version"), secret=m.get("secret") is True, needs_ok=bool(m.get("needs_ok")),
                    code=tuple(c for c in m["code"] if isinstance(c, str)) if isinstance(m.get("code"), list) else ())
                for k, m in enumerate(lib.mods, 1)]
        if self.describe is not None:
            for row, m in zip(rows, lib.mods):
                with contextlib.suppress(Exception):   # (a mod.toml that can't be read: no text, never a crash)
                    row.about, row.full = self.describe(self.root, m)
        if not self.keep_secret:   # (the numbers stay the library's: commands use them)
            rows = [r for r in rows if not r.secret]
        return Listing(self.root, rows)

    def install_problem(self) -> str | None:
        """None when modman knows where Savage Wind is (the library's folder, or for a new library the default
        place). Else the folder where it isn't ("" when the library names none)."""
        mm = self.mm
        try:
            lib = mm.Library.load(self.root)
        except (mm.ModmanError, OSError, ValueError):
            return None  # (the list says what is wrong)
        install = lib.install if lib is not None else mm.default_install()
        if not install:
            return ""
        return None if mm.looks_like_install(install) else install

    def deploy_state(self) -> str:
        """Is the deploy there and up to date? (What `modman status` says, shorter.)"""
        mm = self.mm
        try:
            with self.files:
                lib = mm.Library.load(self.root)
                if lib is None:
                    return ""
                try:
                    launch = mm.read_launch(lib)
                except mm.ModmanError:
                    return "Not deployed yet."
            if mm.missing_paths(launch):
                return "The last deploy is broken: press Deploy."
            if mm.inputs_hash(lib) != launch.get("hash"):
                return "Changes not deployed yet: press Deploy."
            return "Deploy is up to date."
        except (mm.ModmanError, OSError, ValueError, KeyError, TypeError):
            return ""

    def call(self, args: list[str], on_line) -> int:
        """modman's main() in this thread, as `python tools/modman.py <args>` runs it. Returns its exit code."""
        with captured(on_line):
            try:
                code = self.mm.main([*args, "--library", self.root])
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 2
            except Exception as e:  # (modman catches what it expects; anything else is shown, not raised)
                on_line(f"Error: {type(e).__name__}: {e}")
                code = 2
        return code if isinstance(code, int) else 0

    def quick(self, args: list[str]) -> tuple[int, list[str]]:
        """A short command (enable, disable, move) in this thread: (exit code, its lines)."""
        lines: list[str] = []
        with self.files:
            return self.call(args, lines.append), lines

    def start(self, job: Job, events: queue.Queue) -> None:
        """Run a job in a background thread; ("line", job, text) and then ("done", job, code) arrive in events."""
        target = self._in_process if job.in_process else self._subprocess
        threading.Thread(target=target, args=(job, events), daemon=True, name=f"modman {job.action}").start()

    def argv(self, args: list[str], script: str = "") -> list[str]:
        return [self.python, "-u", script or MODMAN_PY, *args, "--library", self.root]

    def _subprocess(self, job: Job, events: queue.Queue) -> None:
        try:  # (UTF-8: a pipe would get the ANSI code page, and mod names outside it would turn into "?")
            proc = self.spawn(self.argv(job.args, job.script), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        except OSError as e:
            events.put(("line", job, f"Error: can't start {self.python}: {e}"))
            events.put(("done", job, 2))
            return
        job.proc = proc
        try:
            for raw in proc.stdout:
                events.put(("line", job, decode_line(raw)))
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError):
                proc.stdout.close()
        events.put(("done", job, proc.wait()))

    def _in_process(self, job: Job, events: queue.Queue) -> None:
        def on_line(line: str) -> None:
            events.put(("line", job, line))

        play = job.action == "play"
        with self.piped_cheats(on_line) if play else contextlib.nullcontext(), \
                self.game_env_extra(job.env) if play and job.env else contextlib.nullcontext():
            code = self.call(job.args, on_line)
        events.put(("done", job, code))

    @contextlib.contextmanager
    def game_env_extra(self, extra: dict):
        """Inside, modman.run starts the game with these variables too (only that game: this process's own
        environment doesn't change). The Multiplayer page uses it for SAVAGEWIND_SAVE_DIR, the co-op save copy."""
        real = getattr(self.mm, "game_env", None)
        if real is None or not extra:
            yield
            return
        self.mm.game_env = lambda base, launch, port: {**real(base, launch, port), **extra}
        try:
            yield
        finally:
            self.mm.game_env = real

    @contextlib.contextmanager
    def piped_cheats(self, on_line):
        """Inside, the cheats.py that modman.run starts prints to on_line (see PipedCheats)."""
        real, script = getattr(self.mm, "subprocess", None), getattr(self.mm, "CHEATS_PY", None)
        if real is None or not script:
            yield
            return
        self.mm.subprocess = PipedCheats(real, script, on_line)
        try:
            yield
        finally:
            self.mm.subprocess = real


# ---------------------------------------------------------------- what the window does, without widgets

class Controller:
    """Runs the buttons through a Backend and keeps what the window shows: the list, the status lines, the
    progress line and the details of the latest command."""

    def __init__(self, backend: Backend, clock=time.monotonic, port: int | None = None):
        self.backend = backend
        self.clock = clock
        self.run_args = ["run", *(["--port", str(port)] if port else [])]
        self.events: queue.Queue = queue.Queue()
        self.jobs: dict[str, Job] = {}
        self.listing = backend.listing()
        self.list_version = 1
        self.details_job: Job | None = None
        self.state_text = ""
        self._select: str | None = None
        self._state_token = 0
        self._state_pending = 0
        self.then_jobs: list[Job] = []   # what starts once the running job ends well ("Turn it off and Play")
        self.want_details = False     # the answer is in Details: the window shows it
        self.status = self._first_status()

    def _first_status(self) -> Status:
        if self.listing.error:
            return Status("error", ["Can't read the mod list.", plain(self.listing.error)])
        where = self.install_problem()
        missing = (f"Savage Wind isn't in {where}." if where else "The library doesn't know the Savage Wind folder.") \
            if where is not None else ""
        if not self.listing.exists:
            if missing:
                return Status("info", ["No mod library yet.", missing, "Press Add mod: it asks for the Savage Wind "
                                                                      "folder first."])
            return Status("info", ["No mod library yet.", "Press Add mod to start one.", KEYS_HINT])
        if missing:
            return Status("warn", ["Ready.", missing, "Deploy asks for the Savage Wind folder."])
        if not self.listing.rows:
            return Status("info", ["No mods yet.", "Press Add mod to add one.", KEYS_HINT])
        return Status("info", ["Ready.", KEYS_HINT])

    # --- what the window reads

    def can(self, action: str) -> bool:
        if action in ("details", "refresh"):
            return True
        if "main" in self.jobs:
            return False
        # (the save and the mod set don't change under a running game: modman refuses too)
        return not ("play" in self.jobs and action in ("deploy", "play", "saves", "profile", "settings", "crash"))

    def row(self, mod_id: str | None) -> Row | None:
        return next((r for r in self.listing.rows if r.id == mod_id), None)

    def install_problem(self) -> str | None:
        """None when modman knows where Savage Wind is; else the folder where it isn't ("" for none). Add mod,
        Deploy and Play (and Add mod, for a new library) ask for the folder first then."""
        try:
            return self.backend.install_problem()
        except Exception:  # (then modman says what is wrong when the button runs)
            return None

    def take_select(self) -> str | None:
        """The mod the list should pick now (after an add, a move or a remove), once."""
        s, self._select = self._select, None
        return s

    def summary_line(self) -> str:
        if self.listing.error:
            return ""
        if not self.listing.exists:
            return "No mod library yet."
        rows = self.listing.rows
        on = sum(r.on for r in rows)
        text = f"{n_of(len(rows), 'mod')}, {on} on (later ones win)." if rows else "No mods yet."
        return f"{text} {self.state_text}".strip()

    def progress(self) -> list[str]:
        out = []
        job = self.jobs.get("main")
        if job:
            out.append(f"{BUSY.get(job.action, 'Working')}... {mmss(self.clock() - job.started)}")
            if job.last_line():
                out.append(clip(job.last_line(), 90))
        play = self.jobs.get("play")
        if play:
            out.append("The game is running." if play.game_started else "Starting the game...")
        return out

    def details_text(self) -> list[str]:
        job = self.details_job
        if job is None:
            return ["Nothing has run yet.", f"Library: {self.listing.root}"]
        head = [hide_words(f"modman {' '.join(job.args)}   (library: {self.listing.root})", job.hide)]
        if job.dropped:
            head.append(f"({job.dropped} earlier lines not kept)")
        tail = []
        if job.code is not None:
            took = f" after {mmss(job.ended - job.started)}" if job.ended is not None else ""
            tail.append(f"(finished{took}, exit code {job.code})")
        return head + job.lines + tail

    # --- the buttons

    def refresh(self) -> None:
        self._reload()
        self.refresh_state()

    def toggle(self, mod_id: str | None, allow_code: bool = False) -> None:
        """Turn on/off. A mod with code waiting for the player's OK (row.needs_ok) is turned on only with allow_code:
        the window asked first (BigText.code_ok_first), so `enable --allow-code`; without it nothing runs and the
        status says why (modman's own refusal names a command line the window's player never types)."""
        row = self._ready("toggle", mod_id)
        if not row:
            return
        if not row.on and row.needs_ok and not allow_code:
            self.status = Status("warn", [f"{row.name} has code: it needs your OK first.", CODE_OK_HINT])
            return
        ok = ["--allow-code"] if not row.on and row.needs_ok else []
        self._quick("disable" if row.on else "enable", [str(row.number), *ok], row)

    def move(self, mod_id: str | None, delta: int) -> None:
        row = self._ready("move", mod_id)
        if not row:
            return
        # (to the next listed mod's place: the numbers are the library's, and a secret mod between them isn't listed)
        nums = sorted(r.number for r in self.listing.rows)
        k = nums.index(row.number) + delta if row.number in nums else -1
        if not 0 <= k < len(nums):
            self.status = Status("info", [f"{row.name} is already {'first' if delta < 0 else 'last'}."])
            return
        self._quick("move", [str(row.number), str(nums[k])], row)

    def add(self, path: str) -> None:
        if not self.free("add"):
            return
        name = os.path.basename(os.path.normpath(path)) or path
        self._start(Job("add", ["add", path], name=name))
        self.status = Status("busy", [f"Adding {name}...", "A big mod takes a minute."])

    def remove(self, mod_id: str | None) -> None:
        row = self._ready("remove", mod_id)
        if row:
            self._start(Job("remove", ["remove", str(row.number)], name=row.name, mod_id=row.id, number=row.number))
            self.status = Status("busy", [f"Removing {row.name}..."])

    def deploy(self) -> None:
        if self.free("deploy"):
            self._start(Job("deploy", ["deploy"]))
            self.status = Status("busy", ["Deploying...", REBUILD_NOTE])

    def play(self) -> None:
        if self.free("play"):
            self._start(Job("play", list(self.run_args), in_process=True))
            self.status = Status("busy", ["Starting the game..."])

    def set_install(self, folder: str) -> bool:
        """modman init <folder> for this library (it makes the library when there is none). True when it worked."""
        if not self.free("add"):
            return False
        args, started = ["init", folder], self.clock()
        code, lines = self.backend.quick(args)
        self.details_job = Job("init", args, lines=lines, code=code, started=started, ended=self.clock())
        self._reload()
        self.status = summarize(self.details_job)
        self.refresh_state()
        return code == 0

    def check(self) -> None:
        if self.free("check"):
            self._start(Job("check", ["doctor"]))
            self.status = Status("busy", ["Checking..."])

    def stop(self) -> None:
        """Stop the background command (closing the window). A running game keeps running."""
        job = self.jobs.get("main")
        if job is not None:
            kill_tree(job.proc)

    # --- background work

    def poll(self) -> bool:
        """Handle what the background work sent. True when something the window shows changed."""
        changed = False
        while True:
            try:
                kind, who, value = self.events.get_nowait()
            except queue.Empty:
                return changed
            changed = True
            if kind == "line":
                self._line(who, value)
            elif kind == "done":
                self._done(who, value)
            elif kind == "state":
                self._state_pending -= 1
                if who == self._state_token:
                    self.state_text = value
            else:
                self.other_event(kind, who, value)

    def other_event(self, kind: str, who, value) -> None:
        """Events a subclass sends itself (the launcher's Play steps)."""

    def refresh_state(self) -> None:
        """Find out (in the background: it reads every enabled mod's file list) whether the deploy is up to date."""
        self._state_token += 1
        self._state_pending += 1
        token = self._state_token

        def work():
            try:
                text = self.backend.deploy_state()
            except Exception:  # (only a line of text: never worth a crash)
                text = ""
            self.events.put(("state", token, text))

        threading.Thread(target=work, daemon=True, name="modman state").start()

    def settle(self, timeout: float = 10.0) -> bool:
        """Wait until nothing runs and everything sent is handled. False on a timeout."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.poll()
            if not self.jobs and not self._state_pending and self.events.empty():
                return True
            time.sleep(0.01)
        return False

    # --- inside

    def free(self, action: str) -> bool:
        """True when the button can run now; else the status says why not."""
        if self.can(action):
            return True
        job = self.jobs.get("main")
        if job:
            self.status = Status("warn", [f"Please wait until {BUTTON.get(job.action, 'it')} is done."])
        else:
            self.status = Status("warn", ["The game is running: close it first."])
        return False

    def _ready(self, action: str, mod_id: str | None) -> Row | None:
        """The mod as the library lists it now (read again, so its number is right), or None (said why)."""
        if not self.free(action):
            return None
        self._reload()
        if not mod_id:
            self.status = Status("info", ["Pick a mod in the list first.", "Use the arrow keys or click one."])
            return None
        row = self.row(mod_id)
        if row is None:
            self.status = Status("warn", ["That mod isn't in the list any more."])
        return row

    def _reload(self, select: str | None = None) -> None:
        new = self.backend.listing()
        if new != self.listing or select:
            self.listing = new
            self.list_version += 1
        if select:
            self._select = select

    def _quick(self, action: str, refs: list[str], row: Row) -> None:
        args = [action, *refs]
        started = self.clock()
        code, lines = self.backend.quick(args)
        job = Job(action, args, name=row.name, mod_id=row.id, number=row.number, lines=lines, code=code,
                  started=started, ended=self.clock())
        self.details_job = job
        self._reload(select=row.id)
        self.status = summarize(job, row.name)
        self.refresh_state()

    def _start(self, job: Job) -> None:
        job.started = self.clock()
        self.jobs[job.slot] = job
        self.details_job = job
        self.backend.start(job, self.events)

    def _line(self, job: Job, text: str) -> None:
        if job.hide:   # (a secret mod's output: its names never reach Details)
            text = hide_words(text, job.hide)
        job.lines.append(text)
        if len(job.lines) > MAX_LINES:
            del job.lines[:DROP_LINES]
            job.dropped += DROP_LINES
        if job.action == "play" and text.startswith(PLAY_MARKS):
            job.game_started = job.game_started or text.startswith("Started Savage Wind")
            self.status = summarize(job)

    def _done(self, job: Job, code: int) -> None:
        job.code, job.ended = code, self.clock()
        if self.jobs.get(job.slot) is job:
            del self.jobs[job.slot]
        before = {r.id for r in self.listing.rows}
        self._reload()
        name, select = job.name, None
        if job.action == "add" and code == 0:
            new = [r for r in self.listing.rows if r.id not in before]
            if new:
                name, select = new[-1].name, new[-1].id
        elif job.action == "remove" and code == 0 and self.listing.rows:
            # (the one now in its place: the next listed one, else the last)
            select = next((r.id for r in self.listing.rows if r.number >= job.number), self.listing.rows[-1].id)
        if select or job.action == "profile":   # (profile: the window shows the set's name again)
            if select:
                self._select = select
            self.list_version += 1
        self.status = summarize(job, name)
        if job.action != "check":
            self.refresh_state()
        nxt = self.then_jobs.pop(0) if self.then_jobs and code == 0 and not job.cancelled else None
        if nxt is None:
            self.then_jobs = []
        elif self.free(nxt.action):
            done = self.status.lines[0] if self.status.lines else ""
            self._start(nxt)
            self.status = Status("busy", [f"{BUSY.get(nxt.action, 'Working')}...", done])

    # --- saves, crashes, mod sets and the rest (docs/mod-manager.md "Saves, crashes and mod sets")

    def library(self):
        """The library as modman reads it now (None: no library yet). Raises what modman raises."""
        with self.backend.files:
            return self.backend.mm.Library.load(self.backend.root)

    def game_running(self) -> bool:
        try:
            return bool(self.backend.mm.game_running())
        except Exception:  # (then modman refuses by itself when it matters)
            return False

    def matching_rows(self, find: str) -> list[Row]:
        """The mods the search box keeps: every word of it is in the name, id, kind or version (any case)."""
        words = (find or "").lower().split()
        if not words:
            return list(self.listing.rows)
        return [r for r in self.listing.rows
                if all(w in " ".join((r.name, r.id, kind_text(r.kinds), r.version or "")).lower() for w in words)]

    def profile_state(self) -> tuple[str, list[str]]:
        """(the mod set in use, every set's name), or ("", []) when the library can't say (no library yet)."""
        try:
            lib = self.library()
            return (lib.profile, list(self.backend.mm.profile_names(lib))) if lib is not None else ("", [])
        except Exception:  # (a broken library: the list says what is wrong; never a crash here)
            return "", []

    def crash_check(self) -> tuple[list[str], list[str]]:
        """Before Play: (modman's plain lines when the last start stopped with an error, else []; the names of the
        mods that are on now and new since the last start that ended well)."""
        mm = self.backend.mm
        try:
            lib = self.library()
            if lib is None:
                return [], []
            report = mm.check_last_start(lib)
            lines = list(mm.crash_lines(report))
            if not lines:
                return [], []
            new = {m["id"] for m in report["new"]}
            return lines, [m["name"] for m in lib.mods if m.get("enabled") and m["id"] in new]
        except Exception:
            return [], []

    def _command(self, action: str, args: list[str], *, name: str = "", show: str = "") -> Job:
        """A short command in this thread (it reads or writes only small files). Sets the status; returns its Job."""
        started = self.clock()
        code, lines = self.backend.quick(args)
        job = Job(action, args, name=name, lines=lines, code=code, started=started, ended=self.clock(), show=show)
        self.details_job = job
        self._reload()
        self.status = summarize(job, name)
        self.want_details = bool(show) and code == 0
        self.refresh_state()
        return job

    def play_without_mods(self) -> None:
        """Savage Wind on its own (run --no-mods): nothing is deployed, the mods stay as they are."""
        if self.free("play"):
            self._start(Job("play", [*self.run_args, NO_MODS], in_process=True))
            self.status = Status("busy", ["Starting the game without mods...", "Your mods stay as they are."])

    def turn_off_and_play(self) -> None:
        """After a crash: turn off the mods new since the last start that ended well, deploy, then Play."""
        if not self.free("play") or not self.free("deploy"):
            return
        job = self._command("crash", ["crash", "turn-off"])
        if job.code != 0:
            return
        said = job.last_line().split(" Run deploy")[0]
        self.then_jobs = [Job("play", list(self.run_args), in_process=True)]
        self._start(Job("deploy", ["deploy"]))
        self.status = Status("busy", [said, "Deploying, then the game starts.", REBUILD_NOTE])

    def set_all(self, on: bool) -> None:
        verb = "enable" if on else "disable"
        if not self.free(verb):
            return
        job = self._command(verb, [verb, "--all"])
        if job.code == 0:
            self.status = Status("ok", [f"Every mod is {'on' if on else 'off'}.", PRESS_DEPLOY])

    def use_last_good(self) -> None:
        """Turn on exactly the mods of the newest start that ended well (in its order)."""
        if not self.free("last-good"):
            return
        job = self._command("last-good", ["last-good", "use"])
        if job.code == 0:
            first = plain(job.lines[0].strip()) if job.lines else ""
            self.status = Status("ok", ["The mods from your last good start are on.", first, PRESS_DEPLOY])

    def show(self, what: str, mod_id: str | None = None) -> None:
        """An answer that goes to Details: info and settings (of a mod), conflicts, changes, saves, crash, fonts."""
        if not self.free(what):
            return
        if what in ("info", "settings"):
            row = self._ready(what, mod_id)
            if row is None:
                return
            heads = {"info": "{who}: its info is in Details.", "settings": "{who}'s settings are in Details."}
            job = self._command(what, [what, row.id], name=row.name, show=heads[what])
            if what == "info" and job.code == 0:
                job.picture = self.picture_file(row.id)
            return
        args, head = {"conflicts": (["conflicts"], "Which mod replaces which: see Details."),
                      "changes": (["changes"], "What changed since the last start:"),
                      "saves": (["saves"], "Your save backups are in Details."),
                      "crash": (["crash"], "How the last start ended:"),
                      "last-good": (["last-good"], "The last mods that worked:"),
                      "font": (["font"], "The fonts are in Details.")}[what]
        self._command(what, args, show=head)

    def picture_file(self, mod_id: str) -> str:
        """The mod's picture as a PNG of 128 px that Tk shows as it is (tools/mod_pictures' cache); "" when none."""
        try:
            mp, lib = self.backend.mm.feature("mod_pictures"), self.library()
            if mp is None or lib is None or not hasattr(mp, "icon_path"):
                return ""
            path = mp.icon_path(lib, mod_id, 128)
            if not path and hasattr(mp, "refresh"):   # (not made yet, or its source changed: make it now)
                mp.refresh(lib, lib.find(mod_id)[1])
                path = mp.icon_path(lib, mod_id, 128)
            return path if path and os.path.isfile(path) else ""
        except Exception:
            return ""

    def backups(self) -> list[dict]:
        """The save backups of the save folder in use, newest first (modman.backups_for: never another playthrough's),
        [] when none or no library."""
        try:
            lib = self.library()
            mm = self.backend.mm
            pick = getattr(mm, "backups_for", None) or mm.save_backups
            return list(pick(lib)) if lib is not None else []
        except Exception:
            return []

    def back_up_save(self) -> None:
        if self.free("saves"):
            self._start(Job("saves", ["saves", "backup"], in_process=True))
            self.status = Status("busy", ["Copying your save..."])

    def restore_save(self, name: str) -> None:
        """Put a backup back (only with the game closed: modman refuses otherwise). The save there now is moved
        aside, never deleted."""
        if self.free("saves"):
            self._start(Job("saves", ["saves", "restore", name], in_process=True))
            self.status = Status("busy", ["Putting the backup back...", "Your save now is kept, not deleted."])

    def profile(self, *args: str) -> None:
        """modman profile use / new / copy / rename / delete / own-save, in the background (use copies the save)."""
        if self.free("profile"):
            self._start(Job("profile", ["profile", *args], in_process=True))
            self.status = Status("busy", ["Changing the mod set..."])

    def choose_picture(self, what: str, path: str, name: str = "") -> None:
        """A picture for a mod (its id), the title screen ("title") or the Mods page ("mods-page"); "" forgets it.
        In the background: tools/mod_pictures reads the whole picture first (a few seconds for a big PNG)."""
        if not self.free("picture"):
            return
        self._start(Job("picture", ["picture", what, path] if path else ["picture", what, "--clear"],
                        name=name or what, in_process=True))
        self.status = Status("busy", ["Reading the picture..." if path else "Forgetting the picture..."])

    def fonts(self) -> tuple[str, list[tuple[str, str]]]:
        """(the font in use, [(key, words)] to choose from); ("", []) without tools/font.py or a library."""
        try:
            font, lib = self.backend.mm.feature("font"), self.library()
            if font is None or lib is None:
                return "", []
            return font.current(lib)[0].label, [(c.key, c.label) for c in font.choices(lib)]
        except Exception:
            return "", []

    def use_font(self, key: str) -> None:
        if self.free("font"):
            job = self._command("font", ["font", "use", key])
            if job.code == 0:
                self.status = Status("ok", [plain(job.last_line()).replace(" Press Deploy to use it.", ""),
                                            PRESS_DEPLOY])

    def font_symbols(self) -> bool | None:
        """Is the missing-symbols fix on (font.json)? None without tools/font.py or a library."""
        try:
            font, lib = self.backend.mm.feature("font"), self.library()
            return None if font is None or lib is None else bool(font.load_settings(lib)["fix_symbols"])
        except Exception:
            return None

    def set_font_symbols(self, on: bool) -> None:
        if self.free("font"):
            job = self._command("font", ["font", "symbols", "on" if on else "off"])
            if job.code == 0:
                self.status = Status("ok", [f"Missing symbols fix: {'on' if on else 'off'}.", PRESS_DEPLOY])

    def setting_rows(self, mod_id: str) -> tuple[str, list]:
        """(the mod's name for its settings, the rows shown now: mod_settings.Row) for the settings editor;
        ("", []) when it has none."""
        mm = self.backend.mm
        try:
            ms, lib = mm.feature("mod_settings"), self.library()
            if ms is None or lib is None:
                return "", []
            pairs = ms.library_schemas(lib)
            sch = next((s for m, s in pairs if m["id"] == mod_id), None)
            if sch is None:
                return "", []
            on = ms.mods_on(lib, lib.enabled(), pairs)
            return sch.name, [r for r in ms.rows(sch, ms.load_values(lib.root, sch), on) if r.shown]
        except Exception:
            return "", []

    def set_setting(self, mod_id: str, key: str, value: str, name: str = "") -> None:
        """One setting's new value (modman refuses while the game runs: S5)."""
        if not self.free("settings"):
            return
        job = self._command("settings", ["settings", mod_id, "set", key, str(value)], name=name)
        if job.code == 0 and job.lines:
            self.status = Status("ok", [plain(job.lines[0].strip()), "It is used at your next start."])


# ---------------------------------------------------------------- the window

THEMES = {
    "light": {"bg": "#ffffff", "fg": "#000000", "button": "#e3e8ef", "active": "#c7d4e5", "line": "#6e6e6e",
              "focus": "#0045d0", "sel_bg": "#0045d0", "sel_fg": "#ffffff", "disabled": "#595959",
              "ok": "#00661a", "warn": "#8a4b00", "error": "#b00000", "busy": "#0045d0", "info": "#0045d0",
              "play": "#00661a", "play_fg": "#ffffff"},
    "dark": {"bg": "#000000", "fg": "#ffffff", "button": "#262626", "active": "#3d3d3d", "line": "#9a9a9a",
             "focus": "#ffd400", "sel_bg": "#ffd400", "sel_fg": "#000000", "disabled": "#a6a6a6",
             "ok": "#5ee07a", "warn": "#ffb347", "error": "#ff7b7b", "busy": "#ffd400", "info": "#ffd400",
             "play": "#5ee07a", "play_fg": "#000000"},
}
COLUMNS = (("num", "#", "e"), ("on", "On", "center"), ("name", "Name", "w"), ("kind", "Kind", "w"),
           ("version", "Version", "w"))


def dpi_aware() -> None:
    """Sharp text on a scaled Windows display (else Windows stretches the window and blurs it)."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def pick_family(root, names: tuple[str, ...], fallback: str) -> str:
    have = set(tkfont.families(root))
    return next((n for n in names if n in have), tkfont.nametofont(fallback, root).actual("family"))


class Flow:
    """Buttons in rows that wrap at the window's width (big text makes them wide). None in items starts a new row."""

    def __init__(self, parent, gap: int, bg: str):
        self.frame = tk.Frame(parent, bg=bg)
        self.items: list = []
        self.gap = gap
        self.shape: list | None = None
        self.frame.bind("<Configure>", lambda e: self.reflow())

    def reflow(self, force: bool = False) -> None:
        spots, height = self.layout(self.frame.winfo_width())  # (1 before the window is shown)
        shape = [(x, y) for _, x, y in spots] + [height]
        if shape == self.shape and not force:
            return
        self.shape = shape
        for w, x, y in spots:
            w.place(x=x, y=y)
        self.frame.configure(height=height)

    def layout(self, width: int) -> tuple[list, int]:
        """Where each button goes at this width, and the height they take."""
        x = y = row_h = 0
        spots = []
        for w in self.items:
            if w is None or (x and width > 1 and x + w.winfo_reqwidth() > width):
                x, y, row_h = 0, y + row_h + self.gap if x else y, 0
            if w is None:
                continue
            rw, rh = w.winfo_reqwidth(), w.winfo_reqheight()
            spots.append((w, x, y))
            x += rw + self.gap
            row_h = max(row_h, rh)
        return spots, y + row_h

    def height_for(self, width: int) -> int:
        return self.layout(width)[1]


ASK_MIN_ROWS = 3   # a question's long text in a box that scrolls: at least this many lines of it show


def detail_rows(room_h: int, rest_h: int, line_h: int, min_rows: int = ASK_MIN_ROWS) -> int:
    """How many lines of a question's text fit in a scrolling box: what the screen has room for (room_h) once the
    rest of the question (its title, the buttons, the margins: rest_h) is there."""
    return max(min_rows, (room_h - rest_h) // max(1, line_h))


FRAME_W = 16      # a window's left and right frame on Windows (outside the size Tk is given): kept on the screen too
BELOW_MIN = 90    # the least kept below a window's inside: its title bar (+y is the top of it) and the taskbar


def place_dialog(req: tuple[int, int], room: tuple[int, int], screen: tuple[int, int],
                 parent: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """(width, height, x, y) of a question window: the size it asks for, but never more than the room the screen
    has (room: beside the title bar and the taskbar), centred on its parent window (a third down), and always all of
    it on the screen. On Windows +x+y is the top left of the window's frame, so its title bar is below y as well as
    the inside (h): all of what the room leaves (the title bar and the taskbar, more on a scaled screen) is kept
    below the window, as fit() does for the main windows, and the frame's width beside it."""
    w, h = min(req[0], room[0]), min(req[1], room[1])
    px, py, pw, ph = parent
    sw, sh = screen
    x = px + max(0, (pw - w) // 2)
    y = py + max(0, (ph - h) // 3)
    below = max(BELOW_MIN, sh - room[1])
    x = max(0, min(x, sw - w - FRAME_W))
    y = max(0, min(y, sh - h - below))
    return w, h, x, y


class BigText:
    """What the big-text windows share (the Mod Manager window, View, and the launcher, LauncherView): labels,
    buttons with a focus ring, arrow keys between buttons, questions in the same big text. The window sets self.root,
    self.t (its colours), self.font, self.bold, self.head and self.closed."""

    def label(self, parent, font=None, **kw):
        opts = {"bg": self.t["bg"], "fg": self.t["fg"], "anchor": "w", "justify": "left", **kw}
        return tk.Label(parent, font=font or self.font, **opts)

    def make_button(self, parent, text: str, command, group: list, bold: bool = False, width: int = 0, why=None):
        """A big button in a frame that turns the focus colour while the button has the keyboard (Windows draws no
        highlight ring around a Tk button, only a thin dotted line). Place it with its .ring. Tab skips it while it
        is greyed out; if it has the keyboard then (it was pressed), Space or Enter calls why() to say why not."""
        t = self.t
        ring = tk.Frame(parent, bg=t["bg"])
        b = tk.Button(ring, text=text, command=command, font=self.bold if bold else self.font, width=width,
                      bg=t["button"], fg=t["fg"], activebackground=t["active"], activeforeground=t["fg"],
                      disabledforeground=t["disabled"], relief="raised", bd=2, padx=self.size, pady=self.size // 3,
                      highlightthickness=0, cursor="hand2")
        b.pack(padx=5, pady=5)
        b.ring = ring
        b.bind("<FocusIn>", lambda e: ring.configure(bg=t["focus"]))
        b.bind("<FocusOut>", lambda e: ring.configure(bg=t["bg"]))

        def press(_event=None, enter=False):
            if str(b.cget("state")) == "disabled":
                if why is not None:
                    why()
                return "break"
            if enter:
                b.invoke()
                return "break"
            return None  # (Space: Tk's own binding presses it)

        for key in ("<Return>", "<KP_Enter>"):
            b.bind(key, lambda e: press(e, enter=True))
        b.bind("<space>", press)
        for key, step in (("<Left>", -1), ("<Up>", -1), ("<Right>", 1), ("<Down>", 1)):
            b.bind(key, lambda e, b=b, step=step, group=group: self.step_focus(group, b, step))
        group.append(b)
        return b

    def step_focus(self, group: list, here, step: int) -> str:
        """Arrow keys between buttons (skipping the ones that can't be pressed now)."""
        i = group.index(here)
        for k in range(1, len(group)):
            b = group[(i + step * k) % len(group)]
            if str(b.cget("state")) != "disabled":
                b.focus_set()
                break
        return "break"

    def one_line(self, text: str, width: int) -> str:
        """text shortened in the middle to fit width."""
        if self.font.measure(text) <= width:
            return text
        lo, hi = 0, len(text)  # (how many characters to keep)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.font.measure(text[:mid // 2] + "..." + text[len(text) - (mid - mid // 2):]) <= width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo // 2] + "..." + text[len(text) - (lo - lo // 2):]

    def key(self, fn) -> str:
        fn()
        return "break"

    def ask(self, title: str, message: str, detail: str, choices: list, default: int = 0, columns: int = 0):
        """A question in the same big text. Returns the value of the button pressed; Esc or closing it gives the
        last button's. columns: the buttons in that many columns (0: all on one row). Never taller than the screen:
        the buttons are packed first (at the bottom), and a text too long for the screen (big text on a 768-px
        laptop, a long list) goes into a box that scrolls."""
        t = self.t
        top = tk.Toplevel(self.root, bg=t["bg"], padx=24, pady=20)
        top.title(title)
        top.transient(self.root)
        top.resizable(False, False)
        result = {"value": choices[-1][1]}

        def done(value):
            result["value"] = value
            top.destroy()

        wrap = max(320, self.font.measure("0") * 36)
        self.label(top, self.head, text=message, wraplength=wrap).pack(side="top", anchor="w")
        row = tk.Frame(top, bg=t["bg"])
        row.pack(side="bottom", anchor="w", pady=(20, 0))   # (before the text: it is the text that gives way)
        group: list = []
        for k, (text, value) in enumerate(choices):
            ring = self.make_button(row, text, lambda v=value: done(v), group).ring
            if columns:
                ring.grid(row=k // columns, column=k % columns, sticky="ew", padx=(0, 8))
                ring.winfo_children()[0].pack_configure(fill="x")
            else:
                ring.pack(side="left", padx=(0, 8))
        shown = self.label(top, text=detail, wraplength=wrap) if detail else None
        if shown is not None:
            shown.pack(side="top", anchor="w", pady=(10, 0))
        top.bind("<Escape>", lambda e: done(choices[-1][1]))
        top.protocol("WM_DELETE_WINDOW", lambda: done(choices[-1][1]))
        top.update_idletasks()
        room = self.room() if hasattr(self, "room") else (int(top.winfo_screenwidth() * 0.96),
                                                          int(top.winfo_screenheight() * 0.88))
        if shown is not None and top.winfo_reqheight() > room[1]:
            rest = top.winfo_reqheight() - shown.winfo_reqheight()
            shown.destroy()
            box = tk.Frame(top, bg=t["bg"])
            box.pack(side="top", anchor="w", fill="both", expand=True, pady=(10, 0))
            lines = detail_rows(room[1], rest + 10 + 8, self.font.metrics("linespace"))
            text_box = tk.Text(box, font=self.font, bg=t["bg"], fg=t["fg"], wrap="word", height=lines,
                               width=max(20, wrap // max(1, self.font.measure("0"))), relief="flat",
                               borderwidth=0, highlightthickness=0, padx=0, pady=4, takefocus=0)
            text_box.insert("end", detail)
            text_box.configure(state="disabled")
            scroll = ttk.Scrollbar(box, orient="vertical", command=text_box.yview, style="Mods.Vertical.TScrollbar")
            text_box.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            text_box.pack(side="left", fill="both", expand=True)
            for seq in WHEEL_EVENTS[:1]:   # (the wheel scrolls it anywhere over the question)
                top.bind(seq, lambda e: text_box.yview_scroll(-1 if e.delta > 0 else 1, "units"))
            top.update_idletasks()
        w, h, x, y = place_dialog((top.winfo_reqwidth(), top.winfo_reqheight()), room,
                                  (top.winfo_screenwidth(), top.winfo_screenheight()),
                                  (self.root.winfo_rootx(), self.root.winfo_rooty(), self.root.winfo_width(),
                                   self.root.winfo_height()))
        clamped = (w, h) != (top.winfo_reqwidth(), top.winfo_reqheight())
        top.geometry(f"{w}x{h}+{x}+{y}" if clamped else f"+{x}+{y}")
        before = self.root.focus_get()

        def grab():  # (only once the window is on screen)
            try:
                if top.winfo_exists():
                    top.grab_set()
                    group[default].focus_force()
            except tk.TclError:
                with contextlib.suppress(tk.TclError):
                    top.after(50, grab)

        group[default].focus_set()
        top.after(20, grab)
        self.root.wait_window(top)
        if before is not None and not self.closed:
            with contextlib.suppress(tk.TclError):
                before.focus_set()
        return result["value"]

    def crash_question(self) -> str | None:
        """The crash check at Play, the same in the Mod Manager and the launcher: "off" (turn off what is new, then
        Play), "plain" (Play without mods), "play" (Play anyway), None (cancel). "play" right away when the last start
        didn't stop with an error."""
        lines, new = self.ctl.crash_check()
        if not lines:
            return "play"
        them = "it" if len(new) == 1 else "them"
        choices = ([(f"Turn {them} off and Play", "off")] if new else []) + [
            ("Play without mods", "plain"), ("Play anyway", "play"), ("Cancel", None)]
        return self.ask("The last start", lines[0], " ".join(lines[1:]), choices, default=0, columns=2)

    def code_question(self, row: Row) -> bool:
        """Turn on a mod with code that waits for the player's OK (added from a folder, a .zip or a DLL: modman adds
        it OFF). The same Careful words as "Add this mod?" for a .breath; Keep it off is the default. True: OK."""
        message, detail = code_ok_words(row)
        return bool(self.ask("Careful", message, detail, [("Turn it on", True), ("Keep it off", False)], default=1))

    def code_ok_first(self, mod_id: str | None) -> bool | None:
        """Before Turn on: the player's OK for a mod with code (code_question). True: OK'd (toggle with allow_code),
        False: nothing to ask (toggle as usual: off, busy, gone, no code), None: kept off (nothing runs)."""
        ctl = self.ctl
        row = ctl.row(mod_id) if ctl is not None and ctl.can("toggle") else None
        if row is None or row.on or not row.needs_ok:
            return False
        return True if self.code_question(row) else None


def code_ok_words(row: Row) -> tuple[str, str]:
    """The Careful question for turning on a mod with code (BigText.code_question): its head and its text."""
    shown = list(dict.fromkeys(os.path.basename(c.replace("\\", "/")) or c for c in row.code))
    files = ", ".join(shown[:4]) + (f" and {len(shown) - 4} more" if len(shown) > 4 else "")
    return (f"Careful: {row.name} has code" + (f" ({files})." if files else "."),
            "Code runs on your PC like a program, with your rights. Only turn it on if you trust the person who made "
            "it.")


class View(BigText):
    """The widgets. Everything they do goes through the Controller."""

    def __init__(self, root, ctl: Controller, size: int = DEFAULT_SIZE, dark: bool = False):
        self.root, self.ctl = root, ctl
        self.size = self.start_size = max(MIN_SIZE, min(MAX_SIZE, size))
        self.t = THEMES["dark" if dark else "light"]
        self.closed = False
        self.list_version = 0
        self.details_shown = False
        self.details_key = None
        self.details_count = 0
        self.progress_text = None
        self.width = 0
        self.minsize_pending = False
        self.last_dir = next((d for d in (os.path.join(os.path.expanduser("~"), "Downloads"), os.path.expanduser("~"))
                              if os.path.isdir(d)), "")
        family = pick_family(root, ("Segoe UI", "Verdana", "DejaVu Sans", "Helvetica"), "TkDefaultFont")
        mono = pick_family(root, ("Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Courier New"), "TkFixedFont")
        self.font = tkfont.Font(root, family=family, size=self.size)
        self.bold = tkfont.Font(root, family=family, size=self.size, weight="bold")
        self.head = tkfont.Font(root, family=family, size=self.size + 4, weight="bold")
        self.mono = tkfont.Font(root, family=mono, size=self.size)
        self.style = ttk.Style(root)
        self.style.theme_use("clam")  # (the theme that takes colours and sizes)
        self.buttons: list = []
        self.by_action: dict[str, object] = {}
        self.build()
        self.render()
        want = self.size
        self.size = self.start_size = self.biggest_fit(want, MIN_SIZE)
        if self.size < want:
            st = self.ctl.status
            self.ctl.status = Status(st.kind, [*st.lines[:2], f"The text is {self.size} pt: bigger doesn't fit on "
                                                               "this screen."])
            self.render()
        self.tree.focus_set()
        ctl.refresh_state()
        root.after(POLL_MS, self.tick)

    # --- building

    def build(self) -> None:
        t, r, pad = self.t, self.root, PAD
        r.title(TITLE)
        set_app_icon(r)
        r.configure(bg=t["bg"])
        r.columnconfigure(0, weight=1)
        # the mod set in use and the search box, then the results
        self.head_box = hb = tk.Frame(r, bg=t["bg"])
        hb.grid(row=0, column=0, sticky="ew", padx=pad, pady=(pad, 10))
        hb.columnconfigure(0, weight=1)
        self.top_bar = top = tk.Frame(hb, bg=t["bg"])
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.columnconfigure(2, weight=1)
        self.set_button = self.make_button(top, "Mod set: Default", self.on_sets, self.buttons, bold=True,
                                           why=lambda: self.after_action(lambda: self.ctl.free("profile")))
        self.set_button.ring.grid(row=0, column=0, sticky="w")
        self.label(top, text="Search:").grid(row=0, column=1, sticky="e", padx=(12, 6))
        self.search = tk.StringVar(r)
        self.search_box = tk.Entry(top, textvariable=self.search, font=self.font, width=4, bg=t["bg"], fg=t["fg"],
                                   insertbackground=t["fg"], relief="flat", highlightthickness=4,
                                   highlightcolor=t["focus"], highlightbackground=t["line"])
        self.search_box.grid(row=0, column=2, sticky="ew")
        self.search.trace_add("write", lambda *_: self.on_search())
        self.search_box.bind("<Escape>", lambda e: self.key(lambda: self.search.set("")))
        for seq in ("<Return>", "<KP_Enter>", "<Down>"):
            self.search_box.bind(seq, lambda e: self.key(self.tree.focus_set))
        self.status_box = box = tk.Frame(hb, bg=t["bg"])
        box.grid(row=1, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)
        self.status_bar = tk.Frame(box, width=10, bg=t["info"])
        self.status_bar.grid(row=0, column=0, rowspan=3, sticky="ns", padx=(0, 14))
        self.status_head = self.label(box, self.head)
        self.status_head.grid(row=0, column=1, sticky="ew")
        # (at least two lines high, so the list doesn't jump: apply_size sets the row's minsize; more when it wraps)
        self.status_more = self.label(box, anchor="nw")
        self.status_more.grid(row=1, column=1, sticky="new")
        self.progress = self.label(box)
        self.progress.grid(row=2, column=1, sticky="ew", pady=(6, 0))
        self.progress.grid_remove()
        # the mods
        self.list_box = tk.Frame(r, bg=t["bg"], highlightthickness=4, highlightbackground=t["line"],
                                 highlightcolor=t["focus"])
        self.list_box.grid(row=1, column=0, sticky="nsew", padx=pad)
        self.list_box.columnconfigure(0, weight=1)
        self.list_box.rowconfigure(0, weight=1)
        r.rowconfigure(1, weight=3)
        # (its height is its smallest: the window makes it bigger, see fit())
        self.tree = ttk.Treeview(self.list_box, columns=[c for c, _, _ in COLUMNS], show="headings",
                                 selectmode="browse", style="Mods.Treeview", height=MIN_LIST_ROWS)
        for col, text, anchor in COLUMNS:
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, anchor=anchor, stretch=col == "name")
        scroll = ttk.Scrollbar(self.list_box, orient="vertical", command=self.tree.yview,
                               style="Mods.Vertical.TScrollbar", takefocus=0)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<space>", lambda e: self.key(self.on_toggle))
        self.tree.bind("<Delete>", lambda e: self.key(self.on_remove))
        self.tree.bind("<Control-Up>", lambda e: self.key(lambda: self.on_move(-1)))
        self.tree.bind("<Control-Down>", lambda e: self.key(lambda: self.on_move(1)))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.update_toggle())
        self.tree.bind("<FocusIn>", self.list_focus)
        self.tree.bind("<FocusOut>", lambda e: self.list_box.configure(highlightbackground=t["line"]))
        self.tree.bind("<Configure>", lambda e: self.fit_columns())
        self.summary = self.label(r)
        self.summary.grid(row=2, column=0, sticky="ew", padx=pad, pady=(6, 12))
        # the buttons
        self.flow = Flow(r, gap=4, bg=t["bg"])
        self.flow.frame.grid(row=3, column=0, sticky="ew", padx=pad)
        for action, text, command, bold, width in (
                ("add", "Add mod", self.on_add, False, 0),
                ("toggle", "Turn off", self.on_toggle, False, 8),
                ("move", "Move up", lambda: self.on_move(-1), False, 0),
                ("move_down", "Move down", lambda: self.on_move(1), False, 0),
                ("remove", "Remove", self.on_remove, False, 0),
                ("deploy", "Deploy", self.on_deploy, True, 0),
                ("play", "Play", self.on_play, True, 0),
                ("play_plain", "Play without mods", self.on_play_plain, False, 0),
                ("check", "Check", self.on_check, False, 0),
                ("more", "More...", self.on_more, False, 0),
                ("details", "Details", self.on_details, False, 12)):
            if action == "deploy":
                self.flow.items.append(None)  # (the game's buttons on a row of their own)
            gate = GATES.get(action, action)
            b = self.make_button(self.flow.frame, text, command, self.buttons, bold, width,
                                 why=lambda gate=gate: self.after_action(lambda: self.ctl.free(gate)))
            self.flow.items.append(b.ring)
            self.by_action[action] = b
        # the full output (hidden until Details)
        self.details_box = tk.Frame(r, bg=t["bg"])
        self.details_box.columnconfigure(0, weight=1)
        self.details_box.rowconfigure(0, weight=1)
        self.details = tk.Text(self.details_box, font=self.mono, bg=t["bg"], fg=t["fg"], insertbackground=t["fg"],
                               selectbackground=t["sel_bg"], selectforeground=t["sel_fg"], wrap="word",
                               height=DETAILS_LINES,
                               relief="flat", highlightthickness=4, highlightcolor=t["focus"],
                               highlightbackground=t["line"], takefocus=1, state="disabled", padx=8, pady=6)
        dscroll = ttk.Scrollbar(self.details_box, orient="vertical", command=self.details.yview,
                                style="Mods.Vertical.TScrollbar", takefocus=0)
        self.details.configure(yscrollcommand=dscroll.set)
        self.details.grid(row=0, column=0, sticky="nsew")
        dscroll.grid(row=0, column=1, sticky="ns")
        self.footer = self.label(r)
        self.footer.grid(row=5, column=0, sticky="ew", padx=pad, pady=(4, pad))
        # keys for the whole window
        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            r.bind(seq, lambda e: self.set_size(self.size + 2))
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            r.bind(seq, lambda e: self.set_size(self.size - 2))
        r.bind("<Control-0>", lambda e: self.set_size(self.start_size))
        r.bind("<Control-MouseWheel>", lambda e: self.set_size(self.size + (2 if e.delta > 0 else -2)))
        r.bind("<F5>", lambda e: self.after_action(self.ctl.refresh))
        r.bind("<Control-f>", lambda e: self.key(self.search_box.focus_set))
        r.bind("<Configure>", self.on_resize)
        r.protocol("WM_DELETE_WINDOW", self.on_close)

    def style_tree(self) -> None:
        t, s = self.t, self.style
        line = self.font.metrics("linespace")
        s.configure("Mods.Treeview", background=t["bg"], fieldbackground=t["bg"], foreground=t["fg"], font=self.font,
                    rowheight=int(line * 1.45), borderwidth=0, bordercolor=t["bg"], lightcolor=t["bg"],
                    darkcolor=t["bg"])
        s.map("Mods.Treeview", background=[("selected", t["sel_bg"])], foreground=[("selected", t["sel_fg"])])
        s.configure("Mods.Treeview.Heading", font=self.bold, background=t["button"], foreground=t["fg"],
                    relief="flat", padding=(self.size // 2, self.size // 4), bordercolor=t["line"],
                    lightcolor=t["button"], darkcolor=t["button"])
        s.map("Mods.Treeview.Heading", background=[("active", t["active"])])
        s.configure("Mods.Vertical.TScrollbar", background=t["button"], troughcolor=t["bg"], arrowcolor=t["fg"],
                    bordercolor=t["line"], lightcolor=t["button"], darkcolor=t["button"],
                    arrowsize=max(16, int(line * 0.9)))
        s.map("Mods.Vertical.TScrollbar", background=[("active", t["active"])])
        zero = self.font.measure("0")
        widths = {"num": zero * 4, "on": self.bold.measure("Off") + zero * 3, "name": zero * 24,
                  "kind": zero * 13, "version": self.bold.measure("Version") + zero * 2}
        for col, width in widths.items():
            self.tree.column(col, width=width, minwidth=min(width, zero * 4))
        self.fit_columns()

    def fit_columns(self) -> None:
        """The Name column takes what the others leave (else bigger text pushes Version out of the list)."""
        have = self.tree.winfo_width()
        if have <= 1:
            return
        cols = [c for c, _, _ in COLUMNS]
        total = sum(int(self.tree.column(c, "width")) for c in cols)
        name = int(self.tree.column("name", "width"))
        width = max(int(self.tree.column("name", "minwidth")), name + have - total)
        if width != name:
            self.tree.column("name", width=width)

    def apply_size(self) -> None:
        for f in (self.font, self.bold, self.mono):
            f.configure(size=self.size)
        self.head.configure(size=self.size + 4)
        self.style_tree()
        for b in self.buttons:
            b.configure(padx=self.size, pady=self.size // 3)
        self.status_box.rowconfigure(1, minsize=2 * self.font.metrics("linespace"))
        self.progress_text = None  # (fit() then lays out the buttons and wraps the text again)

    def set_size(self, size: int) -> str:
        size = max(MIN_SIZE, min(MAX_SIZE, size))
        if size < self.size:
            self.size = size
            self.apply_size()
            self.fit(force=True)
        elif size > self.size:
            old = self.size
            self.size = self.biggest_fit(size, old)
            if self.size < size:
                self.tell("This is the biggest text that fits on this screen.",
                          *(["Hide Details to make it bigger."] if self.details_shown else []))
        return "break"

    def biggest_fit(self, want: int, floor: int) -> int:
        """Use the biggest text size from want down to floor that fits on the screen (floor when none does)."""
        for size in range(want, floor - 1, -1):
            self.size = size
            self.apply_size()
            if self.fit():
                return size
        self.fit(force=True)
        return floor

    # --- the window's size

    def room(self) -> tuple[int, int]:
        """The biggest window the screen has room for (beside the title bar and the taskbar)."""
        return int(self.root.winfo_screenwidth() * 0.96), int(self.root.winfo_screenheight() * 0.88)

    def fixed_height(self, width: int) -> int:
        """What the window needs at this width besides the buttons: the status lines (and room for 2 progress lines),
        the list with MIN_LIST_ROWS rows, Details when shown, the footer."""
        self.rewrap(width)
        self.root.update_idletasks()
        h = self.root.winfo_reqheight() - self.flow.frame.winfo_reqheight()
        if self.progress.winfo_manager():
            h -= self.progress.winfo_reqheight()
        return h + 2 * self.font.metrics("linespace") + 6

    def fit(self, force: bool = False) -> bool:
        """Make the window as big as the text size needs, up to the screen (it only grows), and keep it from being
        made smaller than that. False, changing nothing, when it doesn't fit on the screen (force: as big as fits)."""
        r = self.root
        room_w, room_h = self.room()
        shown = bool(r.winfo_ismapped())
        zoomed = shown and r.state() == "zoomed"
        cur_w, cur_h = (r.winfo_width(), r.winfo_height()) if shown else (0, 0)
        w = cur_w if zoomed else min(room_w, max(cur_w, self.font.measure("0") * 78))
        fit_w = min(w, room_w)  # (also maximized: what fits once it is a window again)
        fixed = self.fixed_height(fit_w)
        need = fixed + self.flow.height_for(fit_w - 2 * PAD)
        fits = need <= room_h
        if not fits and not force:
            self.rewrap()
            return False
        if not zoomed:
            h = min(room_h, max(cur_h, self.font.metrics("linespace") * 27, need))
            if (w, h) != (cur_w, cur_h):
                sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
                x, y = (r.winfo_x(), r.winfo_y()) if shown else ((sw - w) // 2, (sh - h) // 3)
                r.geometry(f"{w}x{h}+{max(0, min(x, sw - w - 16))}+{max(0, min(y, sh - h - 90))}")
        self.set_minsize(fit_w, fixed, room_h)
        self.rewrap(w)
        r.after_idle(lambda: self.flow.reflow(force=True))
        return fits

    def set_minsize(self, width: int, fixed: int, room_h: int) -> None:
        """The window can't be made smaller than what the buttons and the list need: at its narrowest the buttons
        still fit (in more rows) on the screen."""
        step, narrow = max(8, self.font.measure("0") * 2), width
        floor = min(width, max(480, self.font.measure("0") * 30))
        while narrow - step >= floor and fixed + self.flow.height_for(narrow - step - 2 * PAD) <= room_h:
            narrow -= step
        self.root.minsize(narrow, min(room_h, fixed + self.flow.height_for(width - 2 * PAD)))

    def on_resize(self, event) -> None:
        if event.widget is self.root and event.width != self.width:
            self.width = event.width
            self.rewrap()
            if not self.minsize_pending:  # (narrower: the buttons take more rows, so it needs more height)
                self.minsize_pending = True
                self.root.after_idle(self.minsize_now)

    def minsize_now(self) -> None:
        self.minsize_pending = False
        if self.closed:
            return
        room_w, room_h = self.room()
        width = min(self.root.winfo_width(), room_w)
        self.set_minsize(width, self.fixed_height(width), room_h)
        self.rewrap()

    def tell(self, *lines: str) -> None:
        self.ctl.status = Status("info", list(lines))
        self.render()

    def text_width(self, width: int | None = None) -> int:
        return max(200, (width or self.root.winfo_width()) - 80)

    def rewrap(self, width: int | None = None) -> None:
        for w in (self.status_head, self.status_more, self.progress, self.summary):
            w.configure(wraplength=self.text_width(width))
        self.render_footer(width)

    def render_footer(self, width: int | None = None) -> None:
        """The library on one line, shortened in the middle when it is too long (Details has it in full)."""
        self.footer.configure(text=self.one_line(f"Library: {self.ctl.listing.root}", self.text_width(width)))

    # --- drawing what the controller holds

    def render(self) -> None:
        if self.closed:
            return
        ctl, t = self.ctl, self.t
        if self.list_version != ctl.list_version:
            self.render_list()
        st = ctl.status
        self.status_bar.configure(bg=t.get(st.kind, t["info"]))
        self.status_head.configure(text=st.lines[0] if st.lines else "",
                                   fg=t["error"] if st.kind == "error" else t["fg"])
        self.status_more.configure(text="\n".join(st.lines[1:3]))
        found = self.search.get().strip()
        shown = f" Search shows {len(ctl.matching_rows(found))} of them." if found and ctl.listing.rows else ""
        self.summary.configure(text=ctl.summary_line() + shown)
        self.render_footer()
        for action, b in self.by_action.items():
            b.configure(state="normal" if ctl.can(GATES.get(action, action)) else "disabled")
        self.set_button.configure(state="normal" if ctl.can("profile") else "disabled")
        self.update_toggle()
        self.render_progress()
        if self.details_shown:
            self.render_details()

    def render_list(self) -> None:
        keep = self.ctl.take_select() or self.selected_id()
        self.tree.delete(*self.tree.get_children())
        rows = self.ctl.matching_rows(self.search.get())
        for row in rows:
            self.tree.insert("", "end", iid=row.id, values=row.cells())
        self.render_sets()
        ids = [row.id for row in rows]
        pick = keep if keep in ids else (ids[0] if ids else None)
        if pick:
            self.tree.selection_set(pick)
            self.tree.focus(pick)
            self.tree.see(pick)
        self.list_version = self.ctl.list_version

    def render_progress(self) -> None:
        lines = self.ctl.progress()
        text = "\n".join(lines)
        if text == self.progress_text:
            return
        self.progress_text = text  # (a line each, as fit() leaves room for 3)
        self.progress.configure(text="\n".join(self.one_line(x, self.text_width()) for x in lines))
        if text:
            self.progress.grid()
        else:
            self.progress.grid_remove()

    def render_details(self) -> None:
        job = self.ctl.details_job
        lines = self.ctl.details_text()
        key = (id(job), job.dropped if job else 0, job.code if job else None)
        at_end = self.details.yview()[1] >= 0.999
        self.details.configure(state="normal")
        if key != self.details_key:
            self.details.delete("1.0", "end")
            self.details.insert("end", "\n".join(lines))
            self.details_picture = None
            if job is not None and job.picture:   # (a mod's info: its picture, big, under the first line)
                try:
                    self.details_picture = tk.PhotoImage(master=self.root, file=job.picture)
                    self.details.insert("2.0", "\n")
                    self.details.image_create("2.0", image=self.details_picture, padx=4, pady=6)
                except tk.TclError:   # (a picture Tk can't read: the words are enough)
                    self.details_picture = None
        elif len(lines) > self.details_count:
            self.details.insert("end", "\n" + "\n".join(lines[self.details_count:]))
        self.details.configure(state="disabled")
        self.details_key, self.details_count = key, len(lines)
        if at_end:
            self.details.see("end")

    def tick(self) -> None:
        if self.closed:
            return
        try:
            if self.ctl.poll():
                self.render()
            else:
                self.render_progress()
        finally:
            if not self.closed:
                self.root.after(POLL_MS, self.tick)

    # --- the list

    def selected_id(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def list_focus(self, _event=None) -> None:
        self.list_box.configure(highlightbackground=self.t["focus"])
        if not self.tree.selection() and self.tree.get_children():
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)

    def update_toggle(self) -> None:
        row = self.ctl.row(self.selected_id())
        self.by_action["toggle"].configure(text="Turn on" if row and not row.on else "Turn off")

    # --- the buttons

    def after_action(self, fn) -> None:
        fn()
        self.render()
        if self.ctl.want_details:
            self.show_answer()

    def on_toggle(self) -> None:
        sel = self.selected_id()
        ok = self.code_ok_first(sel)   # (a mod with code waiting for the player's OK: asked first)
        if ok is not None:
            self.after_action(lambda: self.ctl.toggle(sel, allow_code=ok))

    def on_move(self, delta: int) -> None:
        self.after_action(lambda: self.ctl.move(self.selected_id(), delta))

    def on_deploy(self) -> None:
        if self.install_known("deploy"):
            self.after_action(self.ctl.deploy)

    def on_play(self) -> None:
        if not self.install_known("play"):
            return
        pick = self.crash_question()
        if pick == "off":
            self.after_action(self.ctl.turn_off_and_play)
        elif pick == "plain":
            self.after_action(self.ctl.play_without_mods)
        elif pick == "play":
            self.after_action(self.ctl.play)

    def on_check(self) -> None:
        self.after_action(self.ctl.check)

    def install_known(self, action: str) -> bool:
        """True when modman knows where Savage Wind is, after asking for the folder (modman init) if it doesn't."""
        if not self.ctl.can(action):
            self.after_action(lambda: self.ctl.free(action))
            return False
        where = self.ctl.install_problem()
        if where is None:
            return True
        if not self.ask("Savage Wind folder", "Where is Savage Wind?",
                        (f"It isn't in {where}. " if where else "") + "Pick the folder that has savagewind.exe.",
                        [("Pick the folder", True), ("Cancel", False)], default=0):
            return False
        folder = filedialog.askdirectory(parent=self.root, title="Pick the Savage Wind folder", mustexist=True,
                                         initialdir=os.path.dirname(where) if where else self.last_dir)
        if not folder:
            return False
        ok = self.ctl.set_install(os.path.normpath(folder))
        self.render()
        return ok

    def on_add(self) -> None:
        if not self.ctl.can("add"):
            return self.after_action(lambda: self.ctl.free("add"))
        if not self.ctl.listing.exists and not self.install_known("add"):  # (a new library needs it)
            return None
        what = self.ask("Add mod", "What is the mod?", "A folder, or a .zip or other file (patch, cheats, DLL).",
                        [("A folder", "folder"), (".zip or file", "file"), ("Cancel", None)], default=0)
        if what == "folder":
            path = filedialog.askdirectory(parent=self.root, title="Pick the mod folder", initialdir=self.last_dir,
                                           mustexist=True)
        elif what == "file":
            path = filedialog.askopenfilename(parent=self.root, title="Pick the mod file", initialdir=self.last_dir,
                                              filetypes=[("Mods", "*.zip *.ips *.pchtxt *.txt *.dll"),
                                                         ("All files", "*.*")])
        else:
            return None
        if path:
            path = os.path.normpath(path)
            self.last_dir = os.path.dirname(path)
            self.after_action(lambda: self.ctl.add(path))
        return None

    def on_remove(self) -> None:
        if not self.ctl.can("remove"):
            return self.after_action(lambda: self.ctl.remove(None))
        row = self.ctl.row(self.selected_id())
        if row is None:
            return self.after_action(lambda: self.ctl.remove(None))
        if self.ask("Remove mod", f"Remove {row.name}?",
                    "This deletes only the library copy. The folder or file you added it from stays.",
                    [("Remove", True), ("Cancel", False)], default=1):
            self.after_action(lambda: self.ctl.remove(row.id))
        return None

    def on_details(self) -> None:
        self.details_shown = not self.details_shown
        if self.details_shown:
            self.details_box.grid(row=4, column=0, sticky="nsew", padx=PAD, pady=(0, 6))
            self.root.rowconfigure(4, weight=2)
            if not self.fit():  # (it would leave the list or the buttons no room)
                self.details_shown = False
                self.details_box.grid_remove()
                self.root.rowconfigure(4, weight=0)
                self.fit(force=True)
                self.tell("Details doesn't fit at this text size.", "Press Ctrl - to make the text smaller first.")
                return
            self.details_key = None
            self.render_details()
        else:
            self.details_box.grid_remove()
            self.root.rowconfigure(4, weight=0)
            self.fit(force=True)
        self.by_action["details"].configure(text="Hide details" if self.details_shown else "Details")

    # --- saves, crashes, mod sets and the rest (docs/mod-manager.md "Saves, crashes and mod sets")

    def ask_text(self, title: str, message: str, initial: str = "") -> str | None:
        """A line of text in the same big text (a mod set's name, a setting's value). None when cancelled."""
        t = self.t
        top = tk.Toplevel(self.root, bg=t["bg"], padx=24, pady=20)
        top.title(title)
        top.transient(self.root)
        top.resizable(False, False)
        result = {"value": None}
        text = tk.StringVar(top, value=initial)

        def done(ok: bool):
            result["value"] = text.get().strip() if ok else None
            top.destroy()

        wrap = max(320, self.font.measure("0") * 36)
        self.label(top, self.head, text=message, wraplength=wrap).pack(anchor="w")
        entry = tk.Entry(top, textvariable=text, font=self.font, width=24, bg=t["bg"], fg=t["fg"],
                         insertbackground=t["fg"], relief="flat", highlightthickness=4, highlightcolor=t["focus"],
                         highlightbackground=t["line"])
        entry.pack(anchor="w", fill="x", pady=(14, 0))
        row = tk.Frame(top, bg=t["bg"])
        row.pack(anchor="w", pady=(20, 0))
        group: list = []
        for words, ok in (("OK", True), ("Cancel", False)):
            self.make_button(row, words, lambda ok=ok: done(ok), group).ring.pack(side="left", padx=(0, 8))
        entry.bind("<Return>", lambda e: done(True))
        top.bind("<Escape>", lambda e: done(False))
        top.protocol("WM_DELETE_WINDOW", lambda: done(False))
        entry.focus_set()
        entry.select_range(0, "end")
        with contextlib.suppress(tk.TclError):
            top.after(20, top.grab_set)
        self.root.wait_window(top)
        return result["value"] or None

    def on_search(self) -> None:
        self.list_version = -1   # (render_list again: it keeps only the mods the search box finds)
        self.render()

    def render_sets(self) -> None:
        cur, _ = self.ctl.profile_state()
        cur = cur or "Default"
        self.set_button.configure(text=f"Mod set: {cur if len(cur) <= 16 else cur[:15] + '...'}")

    def show_answer(self) -> None:
        """The answer is in Details: show Details (when it fits)."""
        self.ctl.want_details = False
        if not self.details_shown:
            self.on_details()

    def on_play_plain(self) -> None:
        if self.install_known("play"):
            self.after_action(self.ctl.play_without_mods)

    def on_more(self) -> None:
        if not self.ctl.can("more"):
            return self.after_action(lambda: self.ctl.free("more"))
        row = self.ctl.row(self.selected_id())
        mine = [(f"About {row.name}", "info"), ("Choose a picture", "picture"), (f"Settings of {row.name}",
                                                                                "settings")] if row else []
        pick = self.ask("More", "What do you want to do?", "" if row else "Pick a mod in the list for its info, "
                        "picture and settings.", [
                            *mine, ("Turn all on", "all_on"), ("Turn all off", "all_off"),
                            ("Use the last mods that worked", "last_good"), ("What changed since the last start",
                                                                              "changes"),
                            ("Which mod replaces which", "conflicts"), ("Save backups", "saves"),
                            ("Font", "font"), ("Pictures for the game", "game_pictures"), ("Cancel", None)],
                        columns=2)
        if pick in ("info", "changes", "conflicts"):
            self.after_action(lambda: self.ctl.show(pick, row.id if row else None))
        elif pick in ("all_on", "all_off"):
            self.after_action(lambda: self.ctl.set_all(pick == "all_on"))
        elif pick == "last_good":
            self.after_action(self.ctl.use_last_good)
        elif pick == "picture" and row:
            self.choose_picture(row.id, row.name)
        elif pick == "game_pictures":
            where = self.ask("Pictures", "Which picture?", "Pictures for mods are chosen with a mod picked in the "
                             "list.", [("The title screen", "title"), ("Cancel", None)], columns=1)
            # ("Behind the Mods page" comes back once the game draws pictures: docs/mod-pictures.md)
            if where:
                self.choose_picture(where, "the title screen" if where == "title" else "the Mods page")
        elif pick == "settings" and row:
            self.edit_settings(row)
        elif pick == "saves":
            self.saves_menu()
        elif pick == "font":
            self.font_menu()
        return None

    def choose_picture(self, what: str, words: str) -> None:
        how = self.ask("Picture", f"A picture for {words}", "A .png, .jpg or .webp file, up to 8 MB. Mod Manager "
                       "keeps its own copy; the mod's folder isn't changed.",
                       [("Pick a file", "file"), ("Forget my picture", "clear"), ("Cancel", None)], columns=1)
        if how == "clear":
            self.after_action(lambda: self.ctl.choose_picture(what, "", words))
            return
        if how != "file":
            return
        path = filedialog.askopenfilename(parent=self.root, title=f"Pick a picture for {words}",
                                          initialdir=self.last_dir,
                                          filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")])
        if path:
            path = os.path.normpath(path)
            self.last_dir = os.path.dirname(path)
            self.after_action(lambda: self.ctl.choose_picture(what, path, words))

    def edit_settings(self, row: Row) -> None:
        """The settings editor: pick a setting, then its value. Refused while the game runs (S5)."""
        if self.ctl.game_running():
            return self.tell("The game is running: close it first.", "Settings can't change while it runs.")
        name, rows = self.ctl.setting_rows(row.id)
        if not rows:
            return self.tell(f"{row.name} has no settings.")
        rows = [r for r in rows if r.type != "button"][:16]   # (buttons work only in the game's Mods page)
        if not rows:
            return self.tell(f"{row.name}'s settings are buttons: they work in the game's Mods page.")
        pick = self.ask("Settings", f"Settings of {name}", "Pick one to change it.",
                        [*[(f"{r.label}: {r.value_text}", r) for r in rows], ("Cancel", None)], columns=2)
        if pick is None:
            return None
        if not pick.enabled:
            return self.tell(f"{pick.label} is locked.", pick.lock_line)
        if pick.type == "toggle":
            value = self.ask("Settings", pick.label, pick.description, [("On", "on"), ("Off", "off"),
                                                                       ("Cancel", None)])
        elif pick.type == "choice":
            value = self.ask("Settings", pick.label, pick.description,
                             [*[(label, v) for v, label in pick.choices], ("Cancel", None)],
                             columns=2 if len(pick.choices) > 3 else 0)
        else:
            hint = {"slider": f"A number from {pick.min:g} to {pick.max:g}.",
                    "key": "A key's name, like F6. None: no key.",
                    "text": f"Up to {pick.max_length} letters."}.get(pick.type, "")
            value = self.ask_text("Settings", f"{pick.label}. {hint}".strip(),
                                  "" if pick.value is None else str(pick.value))
        if value is not None:
            self.after_action(lambda: self.ctl.set_setting(row.id, pick.key, value, row.name))
        return None

    def saves_menu(self) -> None:
        pick = self.ask("Save backups", "Your save", "Mod Manager copies your save before a start with other mods "
                        "than the last one. It keeps the newest 5 and never deletes your save.",
                        [("Back up my save now", "backup"), ("Put a backup back", "restore"),
                         ("Show the backups", "list"), ("Cancel", None)], columns=1)
        if pick == "backup":
            self.after_action(self.ctl.back_up_save)
        elif pick == "list":
            self.after_action(lambda: self.ctl.show("saves"))
        elif pick == "restore":
            if self.ctl.game_running():
                return self.tell("The game is running: close it first.", "A save is put back only while the game "
                                 "is closed.")
            items = self.ctl.backups()[:8]
            if not items:
                return self.tell("No save backups yet.", "A start with other mods than the last one makes one.")
            b = self.ask("Save backups", "Which backup?", "Newest first.",
                         [*[(f"{str(b.get('time') or '')[:16].replace('T', ' ')}  {b.get('reason') or ''}"[:44], b)
                            for b in items], ("Cancel", None)], columns=1)
            if b is None:
                return None
            if self.ask("Save backups", "Put this backup back?", "Your save now is moved aside (renamed, next to "
                        "it) and kept. Nothing is deleted.", [("Put it back", True), ("Cancel", False)], default=1):
                self.after_action(lambda: self.ctl.restore_save(b["name"]))
        return None

    def font_menu(self) -> None:
        cur, choices = self.ctl.fonts()
        if not choices:
            return self.tell("No font choice here.", "The font tool (tools/font.py) isn't there.")
        fix = self.ctl.font_symbols()
        extra = [] if fix is None else [(f"Fix missing symbols: turn {'off' if fix else 'on'}", ("symbols",))]
        key = self.ask("Font", "Which font?", f"In use: {cur}." + ("" if fix is None else
                                                                   f" Missing symbols fix: {'on' if fix else 'off'}."),
                       [*[(label, k) for k, label in choices[:12]], *extra, ("Cancel", None)], columns=2)
        if key == ("symbols",):
            self.after_action(lambda: self.ctl.set_font_symbols(not fix))
        elif key:
            self.after_action(lambda: self.ctl.use_font(key))
        return None

    def on_sets(self) -> None:
        """The mod set chooser: use another set, or make, copy, rename or delete one; a set's own save folder."""
        if not self.ctl.can("profile"):
            return self.after_action(lambda: self.ctl.free("profile"))
        cur, names = self.ctl.profile_state()
        if not cur:
            return self.tell("No mod library yet.", "Press Add mod to start one.")
        others = [n for n in names if n != cur][:8]
        pick = self.ask("Mod sets", f"Mod set in use: {cur}", "A mod set keeps which mods are on, their order and "
                        "their settings. Switching backs up your save first.",
                        [*[(f"Use {n}", ("use", n)) for n in others], ("New set", ("new",)),
                         ("Copy this set", ("copy",)), *([] if cur == "Default" else [
                             ("Rename this set", ("rename",)), ("Own save folder", ("own",))]),
                         *([("Delete a set", ("delete",))] if others else []), ("Cancel", None)], columns=2)
        if pick is None:
            return None
        if pick[0] == "use":
            if self.ctl.game_running():
                return self.tell("The game is running: close it first.", "The mod set changes only while it is "
                                 "closed.")
            return self.after_action(lambda: self.ctl.profile("use", pick[1]))
        if pick[0] in ("new", "copy", "rename"):
            words = {"new": "Name of the new set (it starts as the mods are now):",
                     "copy": f"Name of the copy of {cur}:", "rename": f"New name for {cur}:"}[pick[0]]
            name = self.ask_text("Mod sets", words, cur if pick[0] == "rename" else "")
            if not name:
                return None
            args = ("new", name) if pick[0] == "new" else (pick[0], cur, name)
            return self.after_action(lambda: self.ctl.profile(*args))
        if pick[0] == "own":
            on = self.ask("Mod sets", f"Own save folder for {cur}?", "With its own save folder, this set's game "
                          "doesn't touch your other saves.", [("Own folder, start with a copy of my save", "copy"),
                                                              ("Own folder, empty", "on"),
                                                              ("Use the normal save folder", "off"),
                                                              ("Cancel", None)], columns=1)
            if on:
                args = ("own-save", cur, "on", "--copy") if on == "copy" else ("own-save", cur, on)
                self.after_action(lambda: self.ctl.profile(*args))
            return None
        gone = self.ask("Mod sets", "Delete which set?", "Its mods stay in the library.",
                        [*[(n, n) for n in others], ("Cancel", None)], columns=2)
        if gone and self.ask("Mod sets", f"Delete the mod set {gone}?", "Its mods stay in the library. An own save "
                             "folder stays too.", [("Delete", True), ("Cancel", False)], default=1):
            self.after_action(lambda: self.ctl.profile("delete", gone))
        return None

    def on_close(self) -> None:
        job = self.ctl.jobs.get("main")
        if job and not self.ask("Close", f"{BUTTON.get(job.action, 'It')} is not done.",
                                "Closing now stops it." + (" Deploy again before you play." if job.action == "deploy"
                                                           else ""),
                                [("Stop and close", True), ("Keep going", False)], default=1):
            return
        self.ctl.stop()
        self.closed = True
        self.root.destroy()


# ---------------------------------------------------------------- the launcher: Play does everything

LAUNCH_BUSY = {"prepare": "Getting ready", "apply": "Using your changes from the game's Mods page",
               "undo": "Forgetting the changes from the game's Mods page",
               "deploy": "Getting your mods ready", "add": "Adding the mod", "remove": "Removing the mod",
               "check": "Checking", "tool": "Building the mod from your game files", "enable": "Turning it on",
               "disable": "Turning it off", "breath": "Checking the .breath file",
               "export": "Making the .breath file"}
REBUILD_LINE = "Rebuilding the game code"      # (what modman deploy prints before the 10-20 minute rebuild)
APPLY_FAILED = "The changes from the game were not used: "   # (modmenu apply's line when its deploy failed: the reason)
REBUILD_SAY = "Building the game code for your code mods: about 10-20 minutes, only this time."
# (modman deploy's line before it rebuilds a mod made from the game's files after a game update: modman.RECIPE_SAY)
RECIPE_LINE = "was built for another game version: rebuilding it"
RECIPE_SAY = "Rebuilding a mod made from your game's files for this game version: a minute or two."
# The Mod Manager's words, as the launcher says them (it has no Deploy button: Play deploys)
LAUNCH_WORDS = ((PRESS_DEPLOY, "Play uses the change."), ("Press Deploy to use it.", "Play uses it."),
                ("Press Deploy to take it out of the game.", "Play leaves it out."),
                ("Press Play to start the game.", "Press Play to start the game."))
# Easter eggs (bundled-mods.toml egg = true) are secret: the picker and Home never list them one by one, only one
# switch that turns every one of them on or off together (OFF at first).
EGG_ROW = "breathalyzer:easter-eggs"   # (Home's list: the switch's row; never a mod id)
EGG_TITLE = "Easter eggs"
EGG_ASK = "Enable easter eggs?"
EGG_LINE = "Secret extras. Some take longer the first time you press Play."
EGG_DONE = {True: ["Easter eggs are on.", "Play uses the change. Some take longer the first time."],
            False: ["Easter eggs are off.", "Play uses the change."]}
BONUS_SAY = "A part of a mod you turned on is built from your game files first (about 30 seconds)."


def egg_words(mods: list[dict]) -> tuple:
    """What Details never shows of these secret mods (launcher.read_bundled's): their ids (and the part after the
    first dot), titles and folders."""
    out = []
    for m in mods:
        mid = m.get("id") or ""
        folder = m.get("folder") or ""
        out += [mid, mid.split(".", 1)[-1], m.get("title") or "", folder, os.path.basename(folder.rstrip("/\\"))]
        out += [m.get("after_on_makes") or ""]
    return tuple(dict.fromkeys(w for w in out if w))
HOME_ROWS = 3          # the mod list always has room for this many
ABOUT_LINES = 2        # the selected mod's text under the list: this many lines (longer text is cut with "...")
BREATH_LINES = 4       # "Add this mod?": the description in this many lines at most (the warning and buttons fit)
WHEEL_EVENTS = ("<MouseWheel>", "<Shift-MouseWheel>", "<Button-4>", "<Button-5>")
GAME_POLL_TICKS = 25   # while the game is open, ask whether it still runs every this many ticks (2.5 s)
OPEN_POLL_TICKS = 5    # look for .breath files a second start handed over every this many ticks (0.5 s)


def wrap_text(text: str, font, width: int) -> list[str]:
    """text in lines no wider than width (as a Tk label wraps it, at spaces)."""
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            test = f"{line} {word}" if line else word
            if line and font.measure(test) > width:
                lines.append(line)
                line = word
            else:
                line = test
        lines.append(line)
    return lines


def library_sources(backend) -> list[str] | None:
    """Every mod's `source` in the library (what a received .breath package is kept for), or None when the library
    can't be read now (then nothing is removed for it)."""
    try:
        with backend.files:
            lib = backend.mm.Library.load(backend.root)
    except Exception:  # (can't tell: keep everything)
        return None
    if lib is None:
        return None
    return [m.get("source") for m in lib.mods if isinstance(m.get("source"), str)]


def unwarned_code(library: str, mod_ids: list[str], warned: list[str]) -> list[str]:
    """Code files (DLLs, programs) in these mods' library copies whose names "Add this mod?" didn't show: a package
    can unpack into more than its listing said. Paths relative to the mods folder."""
    import breath as br
    known = {br.shown_name(w).casefold() for w in warned}
    out = []
    for mod_id in mod_ids:
        root = os.path.join(library, "mods", mod_id)
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(folder, d))]
            for f in files:
                if br.is_code(f) and br.shown_name(f).casefold() not in known:
                    out.append(os.path.relpath(os.path.join(folder, f), os.path.join(library, "mods")).replace(
                        "\\", "/"))
    return sorted(out)


def cut_lines(text: str, font, width: int, n: int) -> str:
    """text in at most n lines of this width; when it is longer the last line ends in "..."."""
    lines = wrap_text(text, font, width)
    if len(lines) <= n:
        return "\n".join(lines)
    last = lines[n - 1]
    while last and font.measure(last + "...") > width:
        last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
    return "\n".join([*lines[:n - 1], last + "..."])


def report_text(lines: list[str], home: str | None = None) -> str:
    """Details as text to copy into a bug report: the home folder written as %USERPROFILE%."""
    home = home or os.environ.get("USERPROFILE") or os.path.expanduser("~")
    text = "\n".join(lines)
    if home and len(home) > 3:
        text = re.sub(re.escape(home), "%USERPROFILE%", text, flags=re.I)
    return text


def launcher_words(st: Status) -> Status:
    out = []
    for line in st.lines:
        for old, new in LAUNCH_WORDS:
            line = line.replace(old, new)
        out.append(re.sub(r"\bPress Deploy\b", "Press Play", line))
    return Status(st.kind, out)


def load_modmenu():
    """tools/modmenu.py (the game's Mods page files), or False when it doesn't load: Play then skips that step."""
    try:
        import modmenu
        return modmenu
    except Exception:  # (a tool another job is still writing must not stop the launcher)
        return False


class LaunchBackend(Backend):
    """The Backend of the launcher: one library and one Savage Wind folder, and what Play needs to know first.
    running() says whether savagewind.exe runs (launcher.game_running: no console window flashes up); menu is
    tools/modmenu.py (False: not used); popen starts the game for Play without mods. Fakes in tests."""

    def __init__(self, library: str, install: str, mm=None, spawn=None, python: str | None = None, running=None,
                 menu=None, popen=None):
        super().__init__(library, mm=mm, spawn=spawn, python=python)
        self.install = install
        self.running = running or la.game_running
        self.menu = (load_modmenu() if menu is None else menu) or None
        self.popen = popen or subprocess.Popen
        self.describe = la.describe_mod
        self.keep_secret = True   # (the easter-egg switch reads the eggs' state; Home lists none: shown_rows)

    def deploy_state(self) -> str:
        """One line under Play: is the deploy up to date (Play then starts at once)?"""
        mm = self.mm
        try:
            with self.files:
                lib = mm.Library.load(self.root)
                if lib is None:
                    return ""
                try:
                    launch = mm.read_launch(lib)
                except mm.ModmanError:
                    return "Play gets your mods ready first."
            if mm.missing_paths(launch) or mm.inputs_hash(lib) != launch.get("hash"):
                return "Play gets your changes ready first."
            return "Ready."
        except (mm.ModmanError, OSError, ValueError, KeyError, TypeError):
            return ""

    def play_facts(self) -> dict:
        """What Play has to do (a background thread runs it: it reads every enabled mod's files): running (the game
        is open), pending (changes from the game's Mods page wait), deploy (why a deploy is needed: "first",
        "broken", "changed"; None when the last one is up to date). It also writes the game's mod list again when it
        is older than the library (what `modmenu play` does), so the Mods page shows what Play starts."""
        if self.running():
            return {"running": True}
        mm = self.mm
        with self.files:
            lib = mm.Library.load(self.root)
        if lib is None:
            return {"error": f"No mod library in {self.root}."}
        facts: dict = {"running": False, "pending": False, "deploy": None}
        menu = self.menu
        if menu is not None:
            try:
                facts["pending"] = bool(menu.has_pending(lib))
                if not facts["pending"] and any(m["id"] == menu.MOD_ID for m in lib.mods):
                    now = menu.read_menu(lib)
                    if now is None or now.config != menu.config_stamp(lib):
                        menu.sync(lib)
            except Exception as e:  # (the Mods page's files: never a reason not to play)
                facts["menu_note"] = f"{type(e).__name__}: {e}"
        try:
            launch = mm.read_launch(lib)
        except mm.ModmanError:
            facts["deploy"] = "first"
        else:
            if mm.missing_paths(launch):
                facts["deploy"] = "broken"
            elif mm.inputs_hash(lib) != launch.get("hash"):
                facts["deploy"] = "changed"
        return facts

    def start_plain(self):
        """Play without mods: first a save backup when the last start had mods (S3, modman.backup_before_start; a
        failed copy raises OSError and the game doesn't start), then savagewind.exe as it is, then a start record (the
        crash check and "what changed" read it). self.plain_notes: what the backup said."""
        mm, lib, folder = self.mm, None, None
        self.plain_notes = []
        if hasattr(mm, "backup_before_start"):
            with contextlib.suppress(Exception):
                lib = mm.Library.load(self.root)
        if lib is not None:
            given = next((v for k, v in os.environ.items() if k.upper() == mm.ENV_SAVE and v), None)
            folder = os.path.abspath(os.path.join(self.install, given) if given else os.path.join(self.install,
                                                                                                    "save"))
            try:
                self.plain_notes = list(mm.backup_before_start(lib, folder, mm.launch_set(None, True)))
            except mm.ModmanError as e:
                raise OSError(str(e)) from None
        proc = la.start_plain(self.install, tuple(getattr(self.mm, "MOD_ENV", ())), self.popen)
        if lib is not None:
            pid = getattr(proc, "pid", None)
            with contextlib.suppress(Exception):
                mm.history_append(lib, {"type": "start", "hash": None, "profile": lib.profile,
                                        "pid": pid if isinstance(pid, int) else None, "mods": [], "no_mods": True,
                                        "save": folder})
        return proc

    def bonus_needed(self, mods: list[dict]) -> list[str]:
        """The ids of the bundled mods with after_on (launcher.read_bundled's) that are on, or that the game's Mods
        page turned on (its pending changes), while the library has no after_on_makes yet: their bonus is built
        before Play deploys."""
        with self.files:
            lib = self.mm.Library.load(self.root)
        if lib is None:
            return []
        have = {m["id"] for m in lib.mods}
        on = {m["id"] for m in lib.mods if m.get("enabled")}
        menu = self.menu
        if menu is not None:
            with contextlib.suppress(Exception):   # (the Mods page's files: what the library says, then)
                if menu.has_pending(lib):
                    on = {r.id for r in menu.rows(lib) if r.wanted}
        return [m["id"] for m in mods if m["id"] in on and m.get("after_on_makes") not in have]

    def enabled_ids(self) -> set[str]:
        """The ids of the mods that are ON in the library now."""
        with self.files:
            lib = self.mm.Library.load(self.root)
        return {m["id"] for m in lib.mods if m.get("enabled")} if lib is not None else set()


class LaunchController(Controller):
    """The launcher's Home without widgets. Play (launch) runs, one after the other in the background: the changes
    from the game's Mods page (modmenu apply, which deploys), else a deploy when the mods changed since the last one,
    then modman run. Each step's output is in details_text(); a step that fails stops the rest and says why in
    plain words. While the game is open, Play waits (watch() notices when it closes)."""

    def __init__(self, backend: LaunchBackend, clock=time.monotonic, port: int | None = None, dev: bool = False,
                 dev_ids=(), egg_ids=(), after_on=(), file_log: bool = False):
        self.port_arg = port                        # --port (tests, developers): always opens that control port
        self.dev = dev                              # Settings > Developer commands
        self.file_log = file_log                    # Settings > File log: the game lists the files it opens
        self.dev_ids = set(DEV_MODS) | set(dev_ids)  # mods whose commands need developer mode
        self.egg_ids = set(egg_ids)                 # the easter eggs: one Easter eggs row on Home for all of them
        # the bundled mods with after_on (launcher.read_bundled): their bonus is built when they are first turned on
        # (the picker's Save, else the next Play: Home or the game's Mods page turned them on)
        self.after_on = [m for m in after_on if m.get("after_on") and m.get("after_on_makes")]
        self.install_done: list[str] | None = None  # what the status says when the picked mods are in
        self.install_mods: list[str] = []           # while the picked mods are added: their ids, in order
        self.install_failed: Job | None = None      # the first step that failed (Details shows it)
        self.chain: list[Job] = []
        self.step: Job | None = None
        self.game_open = False
        self.offer: tuple[str, str] | None = None   # an extra button under Play: (label, action)
        self.blocked = False                        # the Mod Manager window is open
        self.rebuilding = False
        self.no_rebuild = False
        self._watching = False
        self._gone = 0
        self.first_time = False
        self.installing: list[str] | None = None   # while the picked mods are added: the ones that failed
        self.install_total = 0
        self.breath = None                          # a checked .breath (breath.Received): "Add this mod?" asks
        self.adding_breath = None                   # (job, breath.Received, the mod ids before) while modman adds it
        self.breath_stop = threading.Event()        # Cancel while a .breath is checked
        self.breath_removing = None                 # the id of a .breath's mod with code it didn't show, going out
        self.status_was: list[str] = []             # (and what the status said about it)
        self.play_env: dict = {}                    # this Play's extra game variables (launch(env=...): Multiplayer)
        super().__init__(backend, clock, port)

    def _first_status(self) -> Status:
        if self.listing.error:
            return Status("error", ["Your mod list can't be read.", plain(self.listing.error)])
        if not self.listing.rows:
            return Status("info", ["Ready to play.", "No mods yet. Press Add mod to add one, or just play."])
        return Status("info", ["Ready to play.", "Turn mods on or off below, then press Play."])

    def summary_line(self) -> str:
        if self.listing.error:
            return ""
        rows = self.shown_rows()
        text = f"{sum(r.on for r in rows)} of {n_of(len(rows), 'mod')} on." if rows else "No mods yet."
        return f"{text} {self.state_text}".strip()

    def shown_rows(self) -> list[Row]:
        """Home's list: the mods but the easter eggs and the secret ones ([mod] secret), then one Easter eggs row for
        all the eggs (ON while one of them is on), whenever there are eggs (they needn't be in the library yet: the
        switch adds them)."""
        rows = [r for r in self.listing.rows if r.id not in self.egg_ids and not r.secret]
        if self.egg_ids and not self.listing.error:
            on = any(r.on for r in self.listing.rows if r.id in self.egg_ids)
            rows.append(Row(0, EGG_ROW, EGG_TITLE, on, [], about=EGG_LINE, full=EGG_LINE))
        return rows

    def shown_row(self, row_id: str | None) -> Row | None:
        return next((r for r in self.shown_rows() if r.id == row_id), None)

    def set_eggs(self, mods: list[dict], on: bool) -> None:
        """The Easter eggs switch: every egg (launcher.read_bundled's mods with egg) on or off together. The ones in
        the library are turned on or off at once, as Turn on/off does; turned on while one isn't there yet, they are
        put in as the picker's Save does (a code mod added, a recipe built from the player's game files)."""
        if not mods or not self.free("toggle"):
            return
        self._reload()
        have = {r.id for r in self.listing.rows}
        if on and any(m["id"] not in have for m in mods):
            self.install_picked(mods, {m["id"]: True for m in mods}, done=EGG_DONE[True])
            return
        action, started, code, lines = "enable" if on else "disable", self.clock(), 0, []
        for m in mods:
            if m["id"] in have:
                c, out = self.backend.quick([action, m["id"]])
                code, lines = code or c, lines + out
        # (Details names the switch, not the mods: they are secret, so their ids and titles are left out too)
        words = egg_words(mods)
        self.details_job = Job(action, [action, f"({EGG_TITLE.lower()})"], name=EGG_TITLE,
                               lines=[hide_words(x, words) for x in lines], code=code, started=started,
                               ended=self.clock(), hide=words)
        self._reload(select=EGG_ROW)
        self.status = Status("ok", list(EGG_DONE[on])) if code == 0 else \
            launcher_words(failed("Not changed.", [x.rstrip() for x in lines if x.strip()]))
        self.refresh_state()

    def can(self, action: str) -> bool:
        if action in ("details", "refresh", "settings"):
            return True
        if self.blocked:
            return False
        job = self.jobs.get("main")
        if action == "cancel":
            return job is not None and job.action in ("prepare", "apply", "deploy", "breath") and not job.cancelled
        if job is not None:
            return False
        if action in ("play", "play_plain", "deploy", "more", "add", "export", "breath"):
            # (add: Add mod, a .breath and Export wait until the game is closed: no heavy work, no window, while
            # you play)
            return "play" not in self.jobs and not self.game_open
        return True

    def free(self, action: str) -> bool:
        if self.can(action):
            return True
        job = self.jobs.get("main")
        if self.blocked:
            self.status = Status("warn", ["Close the Mod Manager window first."])
        elif job is not None:
            self.status = Status("warn", [f"Please wait: {LAUNCH_BUSY.get(job.action, 'busy').lower()}..."])
        else:
            self.status = Status("warn", ["The game is open.", "Close it first."])
        return False

    def progress(self) -> list[str]:
        """One line under Play while something runs (the status above says more; Details has modman's output)."""
        job = self.jobs.get("main")
        if job:
            return [f"{LAUNCH_BUSY.get(job.action, 'Working')}... {mmss(self.clock() - job.started)}"]
        if "play" in self.jobs:
            return ["The game is open." if self.jobs["play"].game_started else "Starting the game..."]
        if self.game_open:
            return ["The game is open. Close it to play again with other mods."]
        return []

    # --- Play

    def launch(self, no_rebuild: bool = False, env: dict | None = None) -> None:
        """Play: find out what it takes (in the background), then do it step by step. env: more variables for the
        game this Play starts (the Multiplayer page: SAVAGEWIND_SAVE_DIR, the co-op save copy); every other Play
        starts it without them."""
        if not self.free("play"):
            return
        self.play_env = dict(env or {})
        self.offer, self.rebuilding, self.no_rebuild = None, False, no_rebuild
        job = Job("prepare", [], started=self.clock())
        self.jobs["main"] = job
        self.status = Status("busy", ["Getting ready..."])

        bonus = list(self.after_on)

        def work():
            try:
                facts = self.backend.play_facts()
                if bonus and not facts.get("running") and not facts.get("error"):
                    # (a mod turned on on Home or in the game's Mods page whose bonus isn't built yet: built first)
                    facts["bonus"] = self.backend.bonus_needed(bonus)
            except Exception as e:  # (said in the window, never a crash)
                facts = {"error": f"{type(e).__name__}: {e}"}
            self.events.put(("plan", job, facts))

        threading.Thread(target=work, daemon=True, name="launcher prepare").start()

    def other_event(self, kind: str, who, value) -> None:
        if kind == "plan":
            self._plan(who, value)
        elif kind == "game":
            self._game_state(value)
        elif kind == "breath":
            self._breath_checked(who, value)
        elif kind == "exported":
            self._exported(who, value)

    def _plan(self, job: Job, facts: dict) -> None:
        if self.jobs.get("main") is job:
            del self.jobs["main"]
        job.lines = [f"{k}: {v}" for k, v in facts.items()]
        job.code, job.ended = 0, self.clock()
        self.details_job = job
        if job.cancelled:
            self.status = Status("info", ["Stopped.", "Press Play to try again."])
            return
        if facts.get("error"):
            self.status = Status("error", ["The game didn't start.", plain(facts["error"])])
            return
        if facts.get("running"):
            self.game_open = True   # (watch() says when it closes)
            self.status = Status("error", ["Savage Wind is already open.", "Close it, then press Play."])
            return
        steps = []
        for m in self.after_on:   # (before the deploy: what they add is used at once)
            if m["id"] in (facts.get("bonus") or ()):
                steps += self.bonus_steps(m, EGG_TITLE if m.get("egg") else m["title"])
        if facts.get("pending"):   # (apply deploys; Play without code mods: that deploy doesn't rebuild either)
            steps.append(Job("apply", ["apply", *(["--no-rebuild"] if self.no_rebuild else [])], script=MODMENU_PY))
        elif self.no_rebuild:
            steps.append(Job("deploy", ["deploy", "--no-rebuild"]))
        elif facts.get("deploy") or steps:
            steps.append(Job("deploy", ["deploy"]))
        steps.append(Job("play", list(self.run_args), in_process=True))
        self.first_time = facts.get("deploy") == "first"
        self.chain = steps
        self._next()

    def dev_mods_on(self) -> list[str]:
        """The names of the mods that are ON and need developer mode (their commands), read from the library now
        (the game's Mods page may just have changed it)."""
        try:
            on = self.backend.enabled_ids()
        except Exception:  # (then the list as the window last read it)
            on = {r.id for r in self.listing.rows if r.on}
        names = {r.id: EGG_TITLE if r.id in self.egg_ids else r.name for r in self.listing.rows}   # (eggs: secret)
        return list(dict.fromkeys(names.get(i, i) for i in sorted(set(on) & self.dev_ids)))

    def play_args(self) -> list[str]:
        """modman run's arguments. Developer mode (the game's control port: SAVAGEWIND_CONTROL) is on when Settings
        says Developer commands ON, while a mod that needs it is ON, or with --port; else modman decides (the port
        only for cheats)."""
        if self.port_arg or self.dev or self.dev_mods_on():
            return ["run", "--port", str(self.port_arg or CONTROL_PORT)]
        return ["run"]

    def _next(self) -> None:
        job = self.chain.pop(0)
        self.step = job
        if job.action == "play":
            job.args = self.play_args()
            job.env = {**self.play_env, **({la.ENV_FILE_LOG: "1"} if self.file_log else {})}
        self._start(job)
        if self.installing is not None:
            n = len(self.install_mods)
            k = self.install_mods.index(job.mod_id) + 1 if job.mod_id in self.install_mods else n
            self.status = Status("busy", [f"Adding mod {k} of {n}...", job.name])
        elif job.action == "apply":
            self.status = Status("busy", ["Using your changes from the game's Mods page...",
                                          "Then the game starts."])
        elif job.action == "deploy":
            self.status = Status("busy", ["Getting your mods ready...",
                                          "The first time takes longer." if self.first_time
                                          else "Then the game starts."])
        elif job.action == "tool":   # (a bonus built before the deploy: Play's after_on step)
            self.status = Status("busy", ["Getting your mods ready...", BONUS_SAY])
        else:
            self.status = Status("busy", ["Starting the game..."])

    def _line(self, job: Job, text: str) -> None:
        super()._line(job, text)
        if job.action in ("apply", "deploy") and text.startswith(REBUILD_LINE) and not self.rebuilding:
            self.rebuilding = True
            self.status = Status("busy", ["Getting your mods ready...", REBUILD_SAY])
        elif job.action in ("apply", "deploy") and RECIPE_LINE in text and not self.rebuilding:
            self.status = Status("busy", ["Getting your mods ready...", RECIPE_SAY])   # (F-MODMAN4: not frozen)

    def _done(self, job: Job, code: int) -> None:
        super()._done(job, code)
        self.status = launcher_words(self.status)
        if self.adding_breath is not None and self.adding_breath[0] is job:
            self._breath_added(job, code)
        elif job.action == "remove" and self.breath_removing is not None and job.mod_id == self.breath_removing:
            self.breath_removing = None
            self.status = Status("error", [self.status_was[0], "It was taken out again." if code == 0 else
                                           "It couldn't be taken out: remove it with More..., then Remove.",
                                           "Only add mods from people you trust."])
        if job is not self.step:
            return
        self.step = None
        text = [x.rstrip() for x in job.lines if x.strip()]
        if self.installing is not None:
            if code != 0:
                self.installing.append(job.name)
                self.install_failed = self.install_failed or job   # (Details shows the step that failed)
                self.chain = [j for j in self.chain if j.mod_id != job.mod_id]   # (the rest of that mod's steps)
            if self.chain:
                self._next()
            else:
                self._installed()
            return
        if job.cancelled:
            self.chain = []
            self.status = Status("info", ["Stopped.", "Press Play to try again."])
            return
        if job.action == "play":
            self.chain = []
            started = any(x.startswith("Started Savage Wind") for x in text)
            if started and not any(x.startswith("Savage Wind closed") for x in text):
                self.game_open, self._gone = True, 0
            busy = next((x for x in text if "already uses port" in x), None)
            if busy:
                m = re.search(r"port (\d+)", busy)
                self.status = Status("error", [f"Another program uses port {m.group(1)}." if m else
                                               "Another program uses the game's port.",
                                               "Close it and press Play again.",
                                               *(["Or turn off Developer commands in Settings."] if self.dev else [])])
            elif any("already running" in x for x in text):
                self.game_open, self._gone = True, 0
                self.status = Status("error", ["Savage Wind is already open.", "Close it, then press Play."])
            return
        if job.action == "undo":
            self.chain = []
            if code == 0:
                self.status = Status("ok", ["The changes from the game's Mods page are forgotten.",
                                            "Press Play to play with your mods as they were."])
            else:
                self.status = launcher_words(failed("The changes from the game's Mods page couldn't be forgotten.",
                                                    text))
            return
        if code == 0 or (job.action == "tool" and self.chain):   # (a bonus that can't be built: played without it)
            if self.chain:
                self._next()
            return
        self.chain = []
        # (apply deploys too: a code mod turned on in the game's Mods page that can't be built fails it the same way.
        # Without these offers every Play would try the same changes again and stop at the same place.)
        if job.action in ("deploy", "apply") and not self.no_rebuild and any(x.startswith(REBUILD_LINE)
                                                                             for x in text):
            self.offer = ("Play without code mods", "no_rebuild")
            reason = error_lines(text)
            self.status = Status("error", ["Your code mods couldn't be built.", "You can play with the other mods: "
                                           "press Play without code mods.", *reason[:1]])
        elif job.action == "deploy":
            self.status = launcher_words(failed("Your mods couldn't be made ready.", text))
        else:
            self.offer = ("Forget the game's changes", "undo")
            note = next((x.split(": ", 1)[1] for x in text if x.startswith(APPLY_FAILED)), "")   # (modmenu's reason)
            why = [plain(note)] if note else failed("", text).lines[1:2]
            self.status = Status("error", ["Your changes from the game's Mods page couldn't be used.", *why,
                                           "Fix it and press Play, or press Forget the game's changes."])

    def cancel(self) -> None:
        """Stop getting the mods ready, or checking a .breath (Cancel). The next Play starts over."""
        job = self.jobs.get("main")
        if not self.can("cancel"):
            return
        job.cancelled = True
        if job.action == "breath":
            self.breath_stop.set()   # (the check stops between two blocks and keeps nothing)
            self.status = Status("busy", ["Stopping..."])
            return
        self.chain = []
        kill_tree(job.proc)
        self.status = Status("busy", ["Stopping..."])

    def stop(self) -> None:
        """Closing the window: a .breath being checked stops too (nothing of it is kept)."""
        self.breath_stop.set()
        super().stop()

    def turn_off_and_play(self) -> None:
        """The crash question's "Turn them off and Play": turn off the mods that are new since the last start that
        ended well (modman crash turn-off), then Play as usual (its own steps: the game's Mods page changes, the bonus
        parts, the deploy, the game)."""
        if not self.free("play"):
            return
        job = self._command("crash", ["crash", "turn-off"])
        if job.code != 0:
            return
        said = job.last_line().split(" Run deploy")[0]
        self.launch()
        self.status = Status("busy", ["Getting ready...", said])

    def forget_game_changes(self) -> None:
        """The offer after the changes from the game's Mods page couldn't be used: throw them away (modmenu undo:
        the game's pending.txt goes), so the next Play uses the mods as the library has them."""
        if not self.free("play"):
            return
        self.offer, self.chain = None, []
        job = Job("undo", ["undo"], script=MODMENU_PY)
        self.step = job
        self._start(job)
        self.status = Status("busy", ["Forgetting the changes from the game's Mods page..."])

    def play_plain(self) -> None:
        """Play without mods: savagewind.exe as it is (the mod variables cleared). Your mods stay as they are."""
        if not self.free("play_plain"):
            return
        if self.backend.running():
            self.game_open, self._gone = True, 0
            self.status = Status("error", ["Savage Wind is already open.", "Close it, then press Play."])
            return
        try:
            self.backend.start_plain()
        except OSError as e:
            self.status = Status("error", ["The game didn't start.", plain(str(e))])
            return
        self.game_open, self._gone = True, 0
        self.offer = None
        saved = any(x.startswith("Your save was backed up first") for x in getattr(self.backend, "plain_notes", []))
        self.status = Status("ok", ["Started Savage Wind without mods.", "Your save was backed up first." if saved
                                    else "Your mods are kept for next time."])

    # --- Breathalyzer Plus: the mods picked in the mod picker

    def install_picked(self, mods: list[dict], picks: dict[str, bool], done: list[str] | None = None) -> None:
        """Put the picked bundled mods (launcher.read_bundled) into the library, one step after the other in the
        background: each code mod not there yet is added (modman add, then its after_add commands), then turned on
        or off as picked; a recipe mod turned on and not there yet is built from the player's game files (its build
        commands), then turned on (a recipe never has to turn itself on). A recipe that stays off is built only when it is picked later. A mod with after_on gets its bonus
        built (those commands) when it goes in turned on and the library has no after_on_makes yet. An easter egg
        left OFF stays out of the library (so nothing lists it; the switch puts it in), and is called Easter eggs
        while it goes in, its id and title left out of Details (they are secret). done: what the status says when
        they are in."""
        if not self.free("add"):
            return
        have = {r.id for r in self.listing.rows}
        steps: list[Job] = []
        for m in mods:
            on, egg = bool(picks.get(m["id"], m["on"])), bool(m.get("egg"))
            title, hide = (EGG_TITLE, egg_words([m])) if egg else (m["title"], ())
            new = m["id"] not in have
            if egg and new and not on:
                continue
            if m["kind"] == "code" and new:
                steps.append(Job("add", ["add", m["folder"]], name=title, mod_id=m["id"], hide=hide))
            elif m["kind"] == "recipe" and new and on:
                steps += [Job("tool", cmd[1:], script=os.path.join(TOOLS, cmd[0]), name=title, mod_id=m["id"],
                              hide=hide) for cmd in m["build"]]
            if new and (m["kind"] == "code" or on):
                steps += [Job("tool", cmd[1:], script=os.path.join(TOOLS, cmd[0]), name=title, mod_id=m["id"],
                              hide=hide) for cmd in m.get("after_add") or []]
            if on and m.get("after_on") and m.get("after_on_makes") not in have:
                steps += self.bonus_steps(m, title)
            if m["kind"] == "code" or not new or on:   # (a new recipe picked on: its build adds it, this turns it on)
                # (a code mod comes in off until OK'd: --allow-code, as these are Breathalyzer's own files from
                # bundled-mods.toml and the picker's switch is the player's OK)
                ok = ["--allow-code"] if on and m["kind"] == "code" else []
                steps.append(Job("enable" if on else "disable", ["enable" if on else "disable", m["id"]] + ok,
                                 name=title, mod_id=m["id"], in_process=True, hide=hide))
        self.installing, self.install_total = [], len(steps)
        self.install_mods = list(dict.fromkeys(j.mod_id for j in steps))   # ("Adding mod 2 of 5": mods, not steps)
        self.install_failed = None
        self.install_done = done
        self.chain = steps
        if steps:
            self._next()
        else:
            self._installed()

    def bonus_steps(self, m: dict, title: str) -> list[Job]:
        """The after_on commands of a bundled mod (its bonus, built from the player's game files), as tool jobs. The
        bonus is secret: its id never shows in Details."""
        hide = egg_words([m]) if m.get("egg") else (m["after_on_makes"],)
        return [Job("tool", cmd[1:], script=os.path.join(TOOLS, cmd[0]), name=title, mod_id=m["id"], hide=hide)
                for cmd in m["after_on"]]

    def _installed(self) -> None:
        failed, self.installing = self.installing or [], None
        self._reload(select=EGG_ROW if self.install_done is EGG_DONE[True] else None)   # (the switch stays picked)
        self.refresh_state()
        if failed:
            names = list(dict.fromkeys(failed))
            job = self.install_failed
            if job is not None:   # (not whatever step ran last: that one may have worked)
                self.details_job = job
            if job is not None and any("is running" in x or "already running" in x for x in job.lines):
                self.status = Status("error", [f"Savage Wind is open, so {', '.join(names)} isn't ready yet.",
                                               "Close the game, then press Pick again in Settings and Save."])
                return
            self.status = Status("error", [f"{n_of(len(names), 'mod')} couldn't be added: {', '.join(names)}.",
                                           "The others are ready. Press Details to see why."])
        else:
            self.status = Status("ok", list(self.install_done or ["Your mods are ready.",
                                                                  "Press Play. You can turn them on or off below."]))

    # --- .breath files (tools/breath.py): add one, make one

    def open_breath(self, path: str, received: str) -> None:
        """A .breath to add (Add a .breath, or a double-click on one): checked and unpacked in the background into a
        new folder under `received`, every file's SHA-256 verified; then self.breath holds it and the window asks
        "Add this mod?". One that fails a check is refused here, in plain words."""
        if not self.free("add"):
            return
        self.drop_breath(quiet=True)
        import breath as br
        name = br.shown_name(path)
        job = Job("breath", [path], name=name, started=self.clock())
        self.jobs["main"] = job
        self.breath_stop = stop = threading.Event()
        self.status = Status("busy", [f"Checking {name}...", "Every file in it is checked before you are asked. "
                                      "Cancel stops it."])
        backend = self.backend

        def work():
            try:
                with contextlib.suppress(Exception):   # (left-over copies; a package no mod names any more)
                    br.sweep(received, in_use=library_sources(backend))
                value = br.unpack(path, os.path.join(received, br.new_name()), stop=stop.is_set)
            except Exception as e:  # (BreathError says it in plain words; anything else is shown as it is)
                value = e
            self.events.put(("breath", job, value))

        threading.Thread(target=work, daemon=True, name="launcher breath").start()

    def _breath_checked(self, job: Job, value) -> None:
        if self.jobs.get("main") is job:
            del self.jobs["main"]
        job.ended = self.clock()
        if isinstance(value, Exception):
            job.code = 1
            job.lines = [str(value)]
            self.details_job = job
            if type(value).__name__ == "Stopped":
                self.status = Status("info", ["Stopped.", f"{job.name} wasn't added. Nothing of it was kept."])
                return
            text = str(value) if type(value).__name__ == "BreathError" else f"{type(value).__name__}: {value}"
            self.status = Status("error", [f"{job.name} wasn't added.", plain(text)])
            return
        if job.cancelled:   # (Cancel came after the last block: nothing is kept all the same)
            import breath as br
            br.dropped(value)
            self.status = Status("info", ["Stopped.", f"{job.name} wasn't added. Nothing of it was kept."])
            return
        job.code = 0
        self.breath = value
        self.status = Status("info", ["Add this mod?", value.manifest.title])

    def add_breath(self) -> None:
        """Add this mod: modman add with the .breath's mod files (or its original package), under its title."""
        rec = self.breath
        if rec is None or not self.free("add"):
            return
        self.breath = None
        title = rec.manifest.title
        # (--name=<title> in one piece: a title starting with "-" isn't taken for an option)
        # (--allow-code only when the Careful box named code: the player saw it and pressed "Add this mod"; a
        # surprise DLL lands off, and _breath_added removes it)
        job = Job("add", ["add", rec.payload, f"--name={title}"] + (["--allow-code"] if rec.code or rec.maybe_code
                                                                   else []), name=title)
        self.adding_breath = (job, rec, {r.id for r in self.listing.rows})
        self._start(job)
        self.status = Status("busy", [f"Adding {title}...", "A big mod takes a minute."])

    def drop_breath(self, quiet: bool = False) -> None:
        """Not now: nothing of it is kept."""
        rec, self.breath = self.breath, None
        if rec is None:
            return
        import breath as br
        br.dropped(rec)
        if not quiet:
            self.status = Status("info", ["Not added.", f"{rec.manifest.title} wasn't added. Nothing of it was kept."])

    def _breath_added(self, job: Job, code: int) -> None:
        _, rec, before = self.adding_breath
        self.adding_breath = None
        import breath as br
        if code != 0:
            br.dropped(rec)
            return
        br.added(rec)
        # (a backstop: code in the mod's library copy that "Add this mod?" didn't name is taken out again)
        new = [r for r in self.listing.rows if r.id not in before]
        surprise = [] if rec.maybe_code else unwarned_code(self.backend.root, [r.id for r in new], rec.code)
        if surprise:
            row = new[-1]
            names = ", ".join(br.shown_name(p) for p in surprise[:3])
            self._start(Job("remove", ["remove", row.id], name=row.name, mod_id=row.id, number=row.number))
            self.status_was = [f"{row.name} wasn't added: it had code it didn't show ({names})."]
            self.status = Status("error", [*self.status_was, "It is being taken out again."])
            self.breath_removing = row.id

    def plan_export(self, mod_id: str | None, dest: str, recipes=()):
        """What Export as .breath would put in the file (breath.ExportPlan), every check made and nothing written;
        None when it can't (the status says why)."""
        row = self._ready("export", mod_id)
        if row is None:
            return None
        import breath as br
        backend = self.backend
        try:
            with backend.files:
                lib = backend.mm.Library.load(backend.root)
            entry = next((m for m in (lib.mods if lib is not None else []) if m.get("id") == row.id), None)
            if entry is None:
                raise br.BreathError(f"{row.name} isn't in your mod list any more.")
            return br.plan_export(backend.root, entry, dest, recipes=recipes,
                                  install=getattr(lib, "install", None) or backend.install)
        except Exception as e:  # (said in the window, never a crash)
            text = str(e) if isinstance(e, br.BreathError) else f"{type(e).__name__}: {e}"
            self.details_job = Job("export", [dest], name=row.name, mod_id=row.id, lines=[text], code=1,
                                   started=self.clock(), ended=self.clock())
            self.status = Status("error", ["The .breath file wasn't made.", plain(text)])
            return None

    def export_breath(self, mod_id: str | None, dest: str, recipes=(), plan=None) -> None:
        """Make a .breath file of a mod (in the background: a big mod takes a while). plan: what plan_export said
        (the window showed it and the player said yes); without it, it is planned here first."""
        row = self._ready("export", mod_id)
        if row is None:
            return
        dest = plan.dest if plan is not None else dest
        job = Job("export", [dest], name=row.name, mod_id=row.id, started=self.clock())
        self.jobs["main"] = job
        self.status = Status("busy", [f"Making {os.path.basename(dest)}...", "A big mod takes a minute."])
        backend = self.backend

        def work():
            try:
                import breath as br
                todo = plan
                if todo is None:
                    with backend.files:
                        lib = backend.mm.Library.load(backend.root)
                    entry = next((m for m in (lib.mods if lib is not None else []) if m.get("id") == mod_id), None)
                    if entry is None:
                        raise br.BreathError(f"{row.name} isn't in your mod list any more.")
                    todo = br.plan_export(backend.root, entry, dest, recipes=recipes,
                                          install=getattr(lib, "install", None) or backend.install)
                value = br.write_export(todo)
            except Exception as e:  # (said in the window, never a crash)
                value = e
            self.events.put(("exported", job, value))

        threading.Thread(target=work, daemon=True, name="launcher export").start()

    def _exported(self, job: Job, value) -> None:
        if self.jobs.get("main") is job:
            del self.jobs["main"]
        job.ended = self.clock()
        self.details_job = job
        if isinstance(value, Exception):
            job.code = 1
            text = str(value) if type(value).__name__ == "BreathError" else f"{type(value).__name__}: {value}"
            job.lines = [text]
            self.status = Status("error", ["The .breath file wasn't made.", plain(text)])
            return
        job.code = 0
        job.lines = [f"Wrote {value.path}", *value.notes]
        self.status = Status("ok", [f"Made {os.path.basename(value.path)}.",
                                    f"It is in {os.path.dirname(value.path)}. Send it to a friend: Add a .breath (or "
                                    "a double-click on it) adds the mod.", *value.notes[:1]])

    # --- while the game is open

    def watch(self) -> None:
        """Ask in the background whether the game still runs (the window calls this every few seconds)."""
        if not self.game_open or self._watching or "play" in self.jobs:
            return
        self._watching = True

        def work():
            try:
                running = bool(self.backend.running())
            except Exception:  # (then it counts as closed: Play is never stuck)
                running = False
            self.events.put(("game", None, running))

        threading.Thread(target=work, daemon=True, name="launcher game check").start()

    def _game_state(self, running: bool) -> None:
        self._watching = False
        self._gone = 0 if running else self._gone + 1
        if self.game_open and self._gone >= 2:   # (twice: a game that restarts itself isn't "closed")
            self.game_open = False
            self.status = Status("ok", ["The game closed.", "Press Play to play again."])
            self.refresh_state()

    # --- the Mod Manager's words

    def _quick(self, action: str, refs: list[str], row: Row) -> None:
        super()._quick(action, refs, row)
        self.status = launcher_words(self.status)


class LauncherView(BigText):
    """Breathalyzer as the launcher (tools/launcher.py): the first-run screens, Home (the big Play button and the mods:
    ON/OFF, title, what each does) and Settings. Everything Home does goes through a LaunchController; the first run
    goes through launcher.FirstRun."""

    def __init__(self, root, settings: la.Settings, settings_file: str, *, first_run: la.FirstRun | None = None,
                 make_backend=None, port: int | None = None, library: str | None = None, app_dir: str = la.REPO,
                 reg_base: str = la.CLASSES, running=None):
        self.root = root
        self.app_dir = app_dir
        self.reg_base = reg_base         # where the .breath file type goes (HKEY_CURRENT_USER; tests: a test key)
        self.running = running or la.game_running   # (Settings' frame rate changes only while the game is closed)
        home = os.path.dirname(os.path.abspath(settings_file))
        self.home = home
        self.window_published = False   # (launcher_main: window.json says which window this is, and if you play)
        self.hidden_for_play = False     # (minimized while you play: nothing brings it up until the game closes)
        self.open_dir = os.path.join(home, la.OPEN_DIR)       # .breath files a second start hands to this window
        self.received_dir = os.path.join(home, "received")   # .breath files unpacked for "Add this mod?"
        self.pending_breath: list[str] = []                   # .breath files waiting until Home is open
        self.breath_buttons: list = []
        self.fps_note = ""                                    # Settings' frame rate line after a change
        self.bundled_key, self.bundled = la.read_bundled(app_dir)   # (Breathalyzer Plus: our mods for the picker)
        self.eggs = [m for m in self.bundled if m.get("egg")]      # (secret: one switch for all, never listed)
        import mp_page
        self.mp = mp_page.MpPage(self)   # Home > Multiplayer: Host, Join, Who am I (tools/mp_page.py)
        self.picks: dict[str, bool] = {}
        self.pick_buttons: dict[str, object] = {}
        self.picker_buttons: list = []
        self.picker_from_settings = False
        self.settings, self.settings_file = settings, settings_file
        self.first = first_run or la.FirstRun(settings, library=library)
        self.make_backend = make_backend or (lambda lib, install: LaunchBackend(lib, install))
        self.port, self.library_arg = port, library
        self.note: str | None = None     # a notice for Home (launcher.json couldn't be read): shown when Home opens
        self.ctl: LaunchController | None = None
        self.closed = False
        self.screen = ""                 # home, setup or settings
        self.setup_mode = "first"        # first (the first run), change (Settings > Change), check (Check my setup)
        self.check_report: la.Report | None = None
        self.details_open = False
        self.manager = None              # the Mod Manager window, while it is open
        self.list_version = -1
        self.ticks = 0
        self.was_open = False
        self.wraps: list = []            # (label, share of the width) whose text wraps
        self.flows: list[Flow] = []
        self.last_dir = next((d for d in (os.path.join(os.path.expanduser("~"), "Downloads"), os.path.expanduser("~"))
                              if os.path.isdir(d)), "")
        self.size = max(MIN_SIZE, min(MAX_SIZE, settings.size))
        self.t = THEMES["dark" if settings.dark else "light"]
        family = pick_family(root, ("Segoe UI", "Verdana", "DejaVu Sans", "Helvetica"), "TkDefaultFont")
        mono = pick_family(root, ("Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Courier New"), "TkFixedFont")
        self.font = tkfont.Font(root, family=family, size=self.size)
        self.bold = tkfont.Font(root, family=family, size=self.size, weight="bold")
        self.head = tkfont.Font(root, family=family, size=self.size + 4, weight="bold")
        self.big = tkfont.Font(root, family=family, size=self.size + 10, weight="bold")
        self.play_font = tkfont.Font(root, family=family, size=self.play_size(), weight="bold")
        self.mono = tkfont.Font(root, family=mono, size=max(MIN_SIZE, self.size - 4))
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        root.title(la.WINDOW_TITLE)
        set_app_icon(root)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            root.bind(seq, lambda e: self.set_size(self.size + 2))
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            root.bind(seq, lambda e: self.set_size(self.size - 2))
        root.bind("<Control-0>", lambda e: self.set_size(la.DEFAULT_SIZE))
        root.bind("<F1>", lambda e: self.help())
        root.bind("<F5>", lambda e: self.reload())
        root.bind("<Escape>", lambda e: self.back())
        root.bind("<Configure>", self.on_resize)
        self.width = 0
        self.build()
        self.first.start()
        if self.first.ready and settings.install:
            self.open_home()
        else:
            self.show_setup()
        self.fit(shrink=True)
        root.after(POLL_MS, self.tick)

    def play_size(self) -> int:
        return max(32, min(72, self.size * 2))

    # --- building

    def wrap_label(self, parent, font=None, share: float = 1.0, **kw):
        lab = self.label(parent, font, **kw)
        self.wraps.append((lab, share))
        return lab

    def build(self) -> None:
        """Every screen's widgets (built again for another theme)."""
        t, r = self.t, self.root
        for w in r.winfo_children():
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        self.wraps, self.flows = [], []
        r.configure(bg=t["bg"])
        r.columnconfigure(0, weight=1)
        r.rowconfigure(1, weight=1)
        top = tk.Frame(r, bg=t["bg"])
        top.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 4))
        top.columnconfigure(1, weight=1)
        self.label(top, self.head, text="Breathalyzer").grid(row=0, column=0, sticky="w")
        self.sw_label = self.label(top, anchor="e")
        self.sw_label.grid(row=0, column=1, sticky="e")
        self.frames = {name: tk.Frame(r, bg=t["bg"]) for name in ("home", "setup", "settings", "picker", "breath",
                                                                     "multiplayer", "who")}
        for f in self.frames.values():
            f.columnconfigure(0, weight=1)
        self.build_home()
        self.build_setup()
        self.build_settings()
        self.build_picker()
        self.build_breath()
        self.mp.build()
        self.style_tree()
        self.list_version = -1

    def build_home(self) -> None:
        t, f = self.t, self.frames["home"]
        self.home_buttons: list = []
        self.status_box = box = tk.Frame(f, bg=t["bg"])
        box.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(6, 8))
        box.columnconfigure(1, weight=1)
        self.status_bar = tk.Frame(box, width=10, bg=t["info"])
        self.status_bar.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 14))
        self.status_head = self.wrap_label(box, self.head)
        self.status_head.grid(row=0, column=1, sticky="ew")
        self.status_more = self.wrap_label(box, anchor="nw")
        self.status_more.grid(row=1, column=1, sticky="new")
        # (Play and the smaller choices beside it share a row: Play without mods; while busy Cancel; after a failed
        # code build Play without code mods)
        row = tk.Frame(f, bg=t["bg"])
        row.grid(row=1, column=0, sticky="ew", padx=PAD)
        row.columnconfigure(0, weight=1)
        self.play = self.make_button(row, "Play", self.on_play, self.home_buttons,
                                     why=lambda: self.after_action(lambda: self.ctl.free("play")))
        self.play.configure(font=self.play_font, bg=t["play"], fg=t["play_fg"], activebackground=t["active"],
                            activeforeground=t["fg"], pady=0)   # (a Tk button adds room of its own)
        self.play.pack_configure(fill="both", expand=True)
        self.play.ring.grid(row=0, column=0, sticky="nsew")
        self.side = tk.Frame(row, bg=t["bg"])
        self.side.grid(row=0, column=1, sticky="ns")
        self.plain_button = self.make_button(self.side, "Play without mods", self.on_play_plain, self.home_buttons)
        self.offer_button = self.make_button(self.side, "", self.on_offer, self.home_buttons, bold=True)
        self.cancel_button = self.make_button(self.side, "Cancel", lambda: self.after_action(self.ctl.cancel),
                                              self.home_buttons)
        self.why_button = self.make_button(self.side, "Details", self.on_details, self.home_buttons)
        for b in (self.plain_button, self.offer_button, self.cancel_button, self.why_button):
            b.pack_configure(fill="both", expand=True)
        self.side_items: list = []
        self.line = self.wrap_label(f)
        self.line.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(6, 8))
        self.list_box = tk.Frame(f, bg=t["bg"], highlightthickness=4, highlightbackground=t["line"],
                                 highlightcolor=t["focus"])
        self.list_box.grid(row=5, column=0, sticky="nsew", padx=PAD)
        self.list_box.columnconfigure(0, weight=1)
        self.list_box.rowconfigure(0, weight=1)
        f.rowconfigure(5, weight=1)
        cols = (("on", "On/Off", "center"), ("name", "Mod", "w"), ("about", "What it does", "w"))
        self.tree = ttk.Treeview(self.list_box, columns=[c for c, _, _ in cols], show="headings", selectmode="browse",
                                 style="Home.Treeview", height=HOME_ROWS)
        for col, text, anchor in cols:
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, anchor=anchor, stretch=col == "about")
        scroll = ttk.Scrollbar(self.list_box, orient="vertical", command=self.tree.yview,
                               style="Mods.Vertical.TScrollbar", takefocus=0)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<space>", lambda e: self.key(self.on_toggle))
        self.tree.bind("<Button-1>", self.on_click)
        self.tree.bind("<Double-1>", self.on_double)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.render_about())
        self.tree.bind("<FocusIn>", self.list_focus)
        self.tree.bind("<FocusOut>", lambda e: self.list_box.configure(highlightbackground=t["line"]))
        self.about = self.wrap_label(f, anchor="nw")   # (ABOUT_LINES lines high; render_about cuts longer text)
        self.about.grid(row=6, column=0, sticky="nsew", padx=PAD, pady=(6, 8))
        self.flow = Flow(f, gap=4, bg=t["bg"])
        self.flows.append(self.flow)
        self.flow.frame.grid(row=7, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
        self.by_action = {}
        group: list = []
        for action, text, command in (
                ("toggle", "Turn off", self.on_toggle), ("export", "Export as .breath", self.on_export),
                ("multiplayer", "Multiplayer", lambda: self.mp.show()),
                ("add", "Add mod", self.on_add), ("breath", "Add a .breath", self.on_add_breath),
                ("mods_folder", "Open mods folder", self.on_open_mods),
                ("settings", "Settings", lambda: self.show_settings()), ("more", "More...", self.on_more)):
            b = self.make_button(self.flow.frame, text, command, group, width=8 if action == "toggle" else 0)
            self.flow.items.append(b.ring)
            self.by_action[action] = b
        self.flow_all = list(self.flow.items)   # (render hides Turn off and Export while there are no mods)
        self.tree.bind("<Configure>", lambda e: self.fit_cells(), add="+")

    def build_setup(self) -> None:
        t, f = self.t, self.frames["setup"]
        self.setup_head = self.wrap_label(f, self.big)
        self.setup_head.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 6))
        self.setup_line = self.wrap_label(f)
        self.setup_line.grid(row=1, column=0, sticky="ew", padx=PAD)
        self.setup_body = tk.Frame(f, bg=t["bg"])
        self.setup_body.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(12, 6))
        self.setup_body.columnconfigure(0, weight=1)
        self.setup_flow = Flow(f, gap=4, bg=t["bg"])
        self.flows.append(self.setup_flow)
        self.setup_flow.frame.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(10, 6))
        self.details_text = self.wrap_label(f, self.mono, fg=t["disabled"])
        self.details_text.grid(row=5, column=0, sticky="ew", padx=PAD, pady=(4, PAD))
        f.rowconfigure(6, weight=1)
        self.setup_buttons: list = []

    def build_settings(self) -> None:
        t, f = self.t, self.frames["settings"]
        self.settings_top = tk.Frame(f, bg=t["bg"])
        self.settings_top.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(8, 10))
        self.settings_body = tk.Frame(f, bg=t["bg"])
        self.settings_body.grid(row=1, column=0, sticky="nsew", padx=PAD)
        self.settings_body.columnconfigure(1, weight=1)
        self.settings_buttons: list = []
        self.fill_settings()

    def style_tree(self) -> None:
        t, s = self.t, self.style
        line = self.font.metrics("linespace")
        self.frames["home"].rowconfigure(6, minsize=ABOUT_LINES * line)   # (the list doesn't jump)
        self.status_box.rowconfigure(1, minsize=2 * line)
        s.configure("Home.Treeview", background=t["bg"], fieldbackground=t["bg"], foreground=t["fg"], font=self.font,
                    rowheight=int(line * 1.45), borderwidth=0, bordercolor=t["bg"], lightcolor=t["bg"],
                    darkcolor=t["bg"])
        s.map("Home.Treeview", background=[("selected", t["sel_bg"])], foreground=[("selected", t["sel_fg"])])
        s.configure("Home.Treeview.Heading", font=self.bold, background=t["button"], foreground=t["fg"],
                    relief="flat", padding=(self.size // 2, self.size // 4), bordercolor=t["line"],
                    lightcolor=t["button"], darkcolor=t["button"])
        s.map("Home.Treeview.Heading", background=[("active", t["active"])])
        s.configure("Mods.Vertical.TScrollbar", background=t["button"], troughcolor=t["bg"], arrowcolor=t["fg"],
                    bordercolor=t["line"], lightcolor=t["button"], darkcolor=t["button"],
                    arrowsize=max(16, int(line * 0.9)))
        s.map("Mods.Vertical.TScrollbar", background=[("active", t["active"])])
        zero = self.font.measure("0")
        self.tree.column("on", width=self.bold.measure("On/Off") + zero * 3, minwidth=zero * 4)
        self.tree.column("about", width=zero * 30, minwidth=zero * 8)
        self.size_columns()
        self.style_combo()

    def style_combo(self) -> None:
        """Settings' dropdowns (the frame rate): the same big text and colours as the buttons, and so is the list
        they open."""
        t, s = self.t, self.style
        line = self.font.metrics("linespace")
        s.configure("Big.TCombobox", foreground=t["fg"], fieldbackground=t["bg"], background=t["button"],
                    arrowcolor=t["fg"], bordercolor=t["line"], lightcolor=t["button"], darkcolor=t["button"],
                    arrowsize=max(16, int(line * 0.9)), padding=(self.size // 2, self.size // 5))
        s.map("Big.TCombobox", fieldbackground=[("readonly", t["bg"]), ("disabled", t["bg"])],
              foreground=[("disabled", t["disabled"]), ("readonly", t["fg"])],
              selectbackground=[("readonly", t["bg"])], selectforeground=[("readonly", t["fg"])],
              background=[("active", t["active"]), ("readonly", t["button"])],
              arrowcolor=[("disabled", t["disabled"])])
        for opt, val in (("font", self.font), ("background", t["bg"]), ("foreground", t["fg"]),
                         ("selectBackground", t["sel_bg"]), ("selectForeground", t["sel_fg"])):
            self.root.option_add(f"*TCombobox*Listbox.{opt}", val)   # (the list it opens)

    def size_columns(self) -> None:
        """The Mod column as wide as the longest name, up to 45% of the list (What it does gets the rest)."""
        zero = self.font.measure("0")
        rows = self.ctl.shown_rows() if self.ctl is not None else []
        longest = max([self.font.measure(r.name) for r in rows] + [self.bold.measure("Mod"), zero * 8])
        have = self.tree.winfo_width()
        cap = int((have if have > 1 else self.text_width()) * 0.45)
        self.tree.column("name", width=min(longest + zero * 2, cap), minwidth=zero * 8)
        self.fit_cells()

    def fit_cells(self) -> None:
        """The Mod and What it does cells cut at a word with "..." when they don't fit (Tk would cut mid-word)."""
        if self.ctl is None or not self.tree.winfo_exists():
            return
        pad = self.font.measure("0") * 2
        widths = {c: int(self.tree.column(c, "width")) - pad for c in ("name", "about")}
        for row in self.ctl.shown_rows():
            if not self.tree.exists(row.id):
                continue
            for col, text in (("name", row.name), ("about", row.about)):
                cut = cut_lines(text or "", self.font, max(widths[col], self.font.measure("0") * 4), 1)
                if self.tree.set(row.id, col) != cut:
                    self.tree.set(row.id, col, cut)

    def show_flow(self, items: list) -> None:
        """Home's row of buttons with only these (a hidden one leaves no gap)."""
        if items == self.flow.items:
            return
        for w in self.flow.items:
            if w not in items:
                w.place_forget()
        self.flow.items = items
        self.flow.shape = None
        self.flow.reflow(force=True)

    # --- screens

    def show(self, name: str) -> None:
        for key, f in self.frames.items():
            if key == name:
                f.grid(row=1, column=0, sticky="nsew")
            else:
                f.grid_remove()
        self.screen = name
        self.render()
        self.rewrap()
        if self.root.winfo_ismapped():
            self.fit()   # (a screen that needs more room than the last one makes the window bigger)

    def back(self) -> str:
        """Esc: back to Home (from Settings, the mod picker, Check my setup, or a folder change that is left); on
        "Add this mod?" it is Not now; Who am I goes back to Multiplayer."""
        if self.screen == "breath":
            self.on_breath_skip()
        elif self.screen in ("multiplayer", "who"):
            self.mp.back()
        elif self.ctl is not None and self.screen != "home":
            self.setup_mode = "first"
            self.show("home")
            self.play.focus_set()
        return "break"

    def reload(self) -> str:
        if self.screen == "home" and self.ctl is not None:
            self.after_action(self.ctl.refresh)
        elif self.screen == "setup" and self.setup_mode != "check":
            self.do("recheck")
        return "break"

    def help(self) -> str:
        lines = ["Keys: Tab and the arrow keys move, Space or Enter presses.",
                 "Space in the list turns a mod on or off. Ctrl + and Ctrl - change the text size. Esc goes back."]
        if self.ctl is not None and self.screen == "home":
            self.ctl.status = Status("info", lines)
            self.render()
        return "break"

    # --- the first run (and Check my setup, and Settings > Change)

    def setup_content(self) -> tuple[str, str, list, list, list, str]:
        """(heading, line, report lines, folder choices, buttons, details) for the setup screen."""
        f = self.first
        back = [("Back", "back")] if self.setup_mode != "first" and self.ctl is not None else []
        if self.setup_mode == "check" and self.check_report is not None:
            rep = self.check_report
            sc = rep.first()
            if sc is None:
                return ("Your setup is ready", rep.folder, rep.lines(), [], [("Back", "back")], "")
            return (sc.heading, sc.line, rep.lines(), [], [("Back", "back")] + sc.buttons, sc.details)
        if f.screen is not None:
            sc = f.screen
            return sc.heading, sc.line, f.report.lines() if f.report else [], [], sc.buttons + back, sc.details
        if f.choices:
            return ("Savage Wind is in more than one place", "Pick the one to use.", [],
                    [c.folder for c in f.choices], [("Choose another folder", "choose")] + back, "")
        if f.ready:
            rep = f.report
            return ("Found Savage Wind", rep.folder, rep.lines(), [],
                    [("Continue", "finish"), ("Choose another folder", "choose")] + back, "")
        return "Looking for Savage Wind...", "", [], [], [], ""

    def show_setup(self) -> None:
        t = self.t
        heading, line, lines, choices, buttons, details = self.setup_content()
        self.setup_head.configure(text=heading)
        self.setup_line.configure(text=line)
        for w in self.setup_body.winfo_children():
            w.destroy()
        self.wraps = [(w, s) for w, s in self.wraps if w.winfo_exists()]
        self.setup_buttons = []
        for k, (ok, text) in enumerate(lines):
            mark = self.label(self.setup_body, self.bold, text="✓" if ok else "✗",
                              fg=t["ok"] if ok else t["error"])
            mark.grid(row=k, column=0, sticky="nw", padx=(0, 12))
            self.wrap_label(self.setup_body, text=text, share=0.9).grid(row=k, column=1, sticky="w")
        self.setup_body.columnconfigure(0, weight=0)
        self.setup_body.columnconfigure(1, weight=1)
        for k, folder in enumerate(choices):
            b = self.make_button(self.setup_body, self.one_line(folder, self.room()[0] - 160),
                                 lambda p=folder: self.do("use", p), self.setup_buttons)
            b.ring.grid(row=len(lines) + k, column=0, columnspan=2, sticky="w")
        for w in self.setup_flow.frame.winfo_children():
            w.destroy()
        self.setup_flow.items = []
        main = None
        for k, (text, action) in enumerate(buttons):
            b = self.make_button(self.setup_flow.frame, text, lambda a=action: self.do(a), self.setup_buttons,
                                 bold=k == 0)
            self.setup_flow.items.append(b.ring)
            main = main or b
        self.details_open = False
        if details:
            b = self.make_button(self.setup_flow.frame, "Details", self.toggle_details, self.setup_buttons)
            self.setup_flow.items.append(b.ring)
        self.setup_details = details
        self.details_text.configure(text="")
        self.setup_flow.shape = None
        self.show("setup")
        self.root.after_idle(lambda: self.setup_flow.reflow(force=True))
        target = (self.setup_buttons[0] if choices else main)
        if target is not None:
            target.focus_set()

    def toggle_details(self) -> None:
        self.details_open = not self.details_open
        self.details_text.configure(text=self.setup_details if self.details_open else "")

    def do(self, action: str, arg: str | None = None) -> None:
        """A setup screen's button."""
        f = self.first
        rep = f.report or self.check_report
        if action == "choose":
            path = filedialog.askdirectory(parent=self.root, title="Pick your Savage Wind folder", mustexist=True,
                                           initialdir=self.settings.install or self.last_dir)
            if not path:
                return
            f.pick(os.path.normpath(path))
        elif action == "use":
            f.use(arg, "picked")
        elif action == "search":
            f.search()
        elif action == "recheck":
            folder = (rep.folder if rep else None) or self.settings.install
            if self.setup_mode == "check" and folder:
                return self.on_check()
            if folder:
                f.use(folder, "picked")
            else:
                f.search()
        elif action == "open_game":
            if rep is not None:
                la.open_path(os.path.join(rep.folder, la.EXE))
            return
        elif action == "download":
            la.open_page(la.DOWNLOAD_PAGE)
            return
        elif action == "close":
            return self.on_close()
        elif action == "continue":
            f.go_on()
        elif action == "open_library":
            if rep is not None and rep.library:
                la.open_path(la.nearest_existing(rep.library))
            return
        elif action == "fresh_list":
            if rep is None or not rep.library or not self.ask(
                    "Your mod list", "Start a fresh mod list?", "The old list is kept as a backup next to it. Your "
                    "mods stay in the mods folder; add them again to use them.", [("Start fresh", True),
                                                                                 ("Cancel", False)], default=1):
                return
            try:
                la.fresh_list(rep.library)
            except OSError as e:
                self.setup_line.configure(text=f"The old list couldn't be moved: {e}")
                return
            f.use(rep.folder, "picked")
        elif action == "finish":
            f.finish(self.settings_file)
            self.setup_mode = "first"
            self.open_home()
            return
        elif action == "back":
            self.back()
            return
        self.show_setup()

    def open_home(self) -> None:
        """Home for the Savage Wind folder the first run settled on: the mod library made (or found), Play focused."""
        rep = self.first.report
        install = rep.folder
        library = self.library_arg or rep.library or la.default_library(install)
        backend = self.make_backend(library, install)
        problem = self.ensure_library(backend, install)
        if problem:
            self.first.screen = la.Screen("library", "Your mod list couldn't be made", problem,
                                          [("Check again", "recheck"), ("Choose another folder", "choose")])
            self.show_setup()
            return
        if self.settings.library != library or self.settings.install != install:
            self.settings.install, self.settings.library = install, library
            self.save_settings()
        if self.ctl is not None:
            self.ctl.stop()
        self.ctl = LaunchController(backend, port=self.port or self.settings.port, dev=self.settings.dev,
                                    dev_ids={m["id"] for m in self.bundled if m.get("developer")},
                                    egg_ids={m["id"] for m in self.eggs},
                                    after_on=[m for m in self.bundled if m.get("after_on")],
                                    file_log=self.settings.file_log)
        warn = next((s for s in rep.screens if not s.blocks), None)
        if warn is not None:
            self.ctl.status = Status("warn", [warn.heading + ".", warn.line])
        if self.note:
            self.ctl.status = Status("warn", [self.note])
            self.note = None
        self.home_report = rep
        self.list_version = -1
        self.show("home")
        self.play.focus_set()
        self.ctl.refresh_state()
        if self.bundled and self.settings.picked != self.bundled_key:
            self.show_picker()   # (Breathalyzer Plus: pick our mods before the first Play)

    def ensure_library(self, backend: LaunchBackend, install: str) -> str | None:
        """The library knows this Savage Wind folder (modman init makes it, or points an old one here). None when it
        does, else why not."""
        mm = backend.mm
        try:
            lib = mm.Library.load(backend.root)
        except (mm.ModmanError, OSError, ValueError) as e:
            return plain(str(e))
        if lib is not None and lib.install and \
                os.path.normcase(os.path.abspath(lib.install)) == os.path.normcase(os.path.abspath(install)):
            return None
        code, lines = backend.quick(["init", install])
        if code == 0:
            return None
        return (error_lines(lines) or [plain(x) for x in lines[-1:]] or ["modman init failed."])[0]

    # --- Home

    def render(self) -> None:
        if self.closed:
            return
        rep = getattr(self, "home_report", None) or self.first.report
        if rep is not None and rep.version and rep.ok:
            self.sw_label.configure(text=f"Savage Wind {rep.version}  ✓", fg=self.t["ok"])
        else:
            self.sw_label.configure(text="")
        if self.ctl is not None and self.ctl.breath is not None and self.screen in ("home", "settings"):
            self.show_breath()   # (a .breath was checked: "Add this mod?")
            return
        if self.screen in ("multiplayer", "who"):
            self.mp.render()
            return
        if self.screen != "home" or self.ctl is None:
            return
        ctl, t = self.ctl, self.t
        if self.list_version != ctl.list_version:
            self.render_list()
        st = ctl.status
        self.status_bar.configure(bg=t.get(st.kind, t["info"]))
        self.status_head.configure(text=st.lines[0] if st.lines else "", fg=t["error"] if st.kind == "error"
                                   else t["fg"])
        self.status_more.configure(text="\n".join(st.lines[1:3]))
        self.play.configure(state="normal" if ctl.can("play") else "disabled",
                            bg=t["play"] if ctl.can("play") else t["button"])
        self.plain_button.configure(state="normal" if ctl.can("play_plain") else "disabled")
        empty = not ctl.shown_rows() and not ctl.listing.error   # (no mods: one Play, no Play without mods or Turn off)
        per_mod = {self.by_action["toggle"].ring, self.by_action["export"].ring}
        mp_ring = None if self.mp.available() else self.by_action["multiplayer"].ring   # (only with Ghost multiplayer)
        self.show_flow([r for r in self.flow_all if not (empty and r in per_mod) and r is not mp_ring])
        mp_on = self.mp.session is not None and self.mp.session.running
        if self.by_action["multiplayer"].cget("text") != ("Multiplayer: ON" if mp_on else "Multiplayer"):
            self.by_action["multiplayer"].configure(text="Multiplayer: ON" if mp_on else "Multiplayer")
            self.root.after_idle(lambda: self.flow.frame.winfo_exists() and self.flow.reflow(force=True))
        if ctl.can("cancel"):
            items = [self.cancel_button.ring]
        else:
            items = [] if empty else [self.plain_button.ring]
            if ctl.offer:
                self.offer_button.configure(text=ctl.offer[0], state="normal" if ctl.can("play") else "disabled")
                items.insert(0, self.offer_button.ring)
            if st.kind == "error" and ctl.details_job is not None:
                items.append(self.why_button.ring)
        if items != self.side_items:
            for w in self.side_items:
                w.pack_forget()
            for w in items:
                w.pack(side="left", fill="y", padx=(8, 0))
            self.side_items = items
        for action, b in self.by_action.items():
            gate = {"toggle": "toggle", "add": "add", "more": "more", "export": "add", "breath": "add"}.get(
                action, "settings")
            b.configure(state="normal" if ctl.can(gate) else "disabled")
        self.render_line()
        self.render_about()

    def render_line(self) -> None:
        lines = self.ctl.progress() if self.ctl.jobs or self.ctl.game_open else []
        self.line.configure(text="\n".join(lines) if lines else self.ctl.summary_line())

    def render_list(self) -> None:
        keep = self.ctl.take_select() or self.selected_id()
        self.tree.delete(*self.tree.get_children())
        rows = self.ctl.shown_rows()   # (the easter eggs: one Easter eggs row, never one by one)
        for row in rows:
            self.tree.insert("", "end", iid=row.id, values=("ON" if row.on else "OFF", row.name, row.about))
        ids = [r.id for r in rows]
        pick = keep if keep in ids else (ids[0] if ids else None)
        if pick:
            self.tree.selection_set(pick)
            self.tree.focus(pick)
            self.tree.see(pick)
        self.list_version = self.ctl.list_version
        self.size_columns()

    def render_about(self) -> None:
        if self.ctl is None:
            return
        row = self.ctl.shown_row(self.selected_id())
        if row is None:
            text = "Add a mod with Add mod." if not self.ctl.shown_rows() else "Pick a mod to read what it does."
        else:
            text = f"{row.name}: {row.full or row.about or kind_text(row.kinds)}"
        width = int(self.about.cget("wraplength") or 0) or self.text_width()
        self.about.configure(text=cut_lines(text, self.font, width, ABOUT_LINES))
        self.by_action["toggle"].configure(text="Turn on" if row and not row.on else "Turn off")

    def tick(self) -> None:
        if self.closed:
            return
        try:
            self.ticks += 1
            changed = False
            if self.ctl is not None:
                changed = self.ctl.poll()
                if self.ctl.game_open and self.ticks % GAME_POLL_TICKS == 0:
                    self.ctl.watch()
                playing = self.ctl.game_open or "play" in self.ctl.jobs
                if playing != self.was_open:
                    self.was_open = playing
                    if playing:
                        self.fps_note = ""   # (the game reads the new frame rate now: "next start" is over)
                    self.hide_while_playing(playing)
                    self.publish_window()
            if self.ticks % OPEN_POLL_TICKS == 0:   # (a double-click while this window is open: asked when it can)
                self.pending_breath += la.take_files(self.open_dir)
            if self.pending_breath:
                self.next_breath()
            mp_changed = self.mp.tick()   # (Multiplayer: the tools' news, and the game's state for the session)
            if self.ctl is not None:
                if changed or (mp_changed and self.screen == "home"):
                    self.render()
                elif self.screen == "home" and (self.ctl.jobs or self.ctl.game_open):
                    self.render_line()
                elif self.screen in ("multiplayer", "who") and (mp_changed or self.ctl.jobs):
                    self.mp.render()
        finally:
            if not self.closed:
                self.root.after(POLL_MS, self.tick)

    def playing(self) -> bool:
        """The game is open (or Play is starting it), or this window hid itself for it: then nothing brings the
        window up, and nothing heavy runs, until the game is closed."""
        ctl = self.ctl
        return self.hidden_for_play or (ctl is not None and (ctl.game_open or "play" in ctl.jobs))

    def hide_while_playing(self, playing: bool) -> None:
        """Settings > Hide while playing: minimized when the game starts, back when it closes. Only this brings the
        window back after it hid itself."""
        if playing:
            if self.settings.hide_while_playing:
                with contextlib.suppress(tk.TclError):
                    self.root.iconify()
                self.hidden_for_play = True
        elif self.hidden_for_play:
            self.hidden_for_play = False
            with contextlib.suppress(tk.TclError):
                self.root.deiconify()
                self.root.lift()

    def start_publishing(self) -> None:
        """launcher_main: from now on window.json says which window this is and whether you play (a second start
        brings only this window up, and never while you play)."""
        self.window_published = True
        self.publish_window()

    def publish_window(self) -> None:
        if not self.window_published:
            return
        try:
            hwnd = int(self.root.wm_frame(), 16)
        except (tk.TclError, ValueError):
            return
        la.publish_window(self.home, os.getpid(), hwnd, self.playing())

    def selected_id(self) -> str | None:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def list_focus(self, _event=None) -> None:
        self.list_box.configure(highlightbackground=self.t["focus"])
        if not self.tree.selection() and self.tree.get_children():
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)

    def on_click(self, event):
        """A click in the On/Off column turns that mod on or off."""
        row = self.tree.identify_row(event.y)
        if row and self.tree.identify_region(event.x, event.y) == "cell" and self.tree.identify_column(event.x) == "#1":
            self.tree.selection_set(row)
            self.tree.focus(row)
            self.on_toggle()
            return "break"
        return None

    def on_double(self, event):
        if self.tree.identify_column(event.x) != "#1" and self.tree.identify_row(event.y):
            self.on_toggle()
        return "break"

    def after_action(self, fn) -> None:
        fn()
        self.render()

    def on_toggle(self) -> None:
        sel = self.selected_id()
        if sel == EGG_ROW:   # (the Easter eggs switch: every egg on or off together)
            row = self.ctl.shown_row(EGG_ROW)
            on = not (row is not None and row.on)
            return self.after_action(lambda: self.ctl.set_eggs(self.eggs, on))
        ok = self.code_ok_first(sel)   # (a mod with code waiting for the player's OK: asked first)
        if ok is None:
            return None
        return self.after_action(lambda: self.ctl.toggle(sel, allow_code=ok))

    def on_play(self) -> None:
        """Play. When the last start stopped with an error, first the same question as the Mod Manager's Play (turn
        off what is new since the last start that ended well, Play without mods, Play anyway, Cancel)."""
        if self.mp_waits():
            return
        if not self.ctl.can("play"):   # (busy, or the game is open: Play says why, no question)
            return self.after_action(lambda: self.ctl.launch())
        pick = self.crash_question()
        if pick == "off":
            self.after_action(self.ctl.turn_off_and_play)
        elif pick == "plain":
            self.after_action(self.ctl.play_plain)
        elif pick == "play":
            self.after_action(lambda: self.ctl.launch())

    def on_offer(self) -> None:
        if self.ctl.offer and self.ctl.offer[1] == "no_rebuild":
            self.after_action(lambda: self.ctl.launch(no_rebuild=True))
        elif self.ctl.offer and self.ctl.offer[1] == "undo":
            self.after_action(self.ctl.forget_game_changes)

    def on_play_plain(self) -> None:
        if not self.mp_waits():
            self.after_action(self.ctl.play_plain)

    def mp_waits(self) -> bool:
        """Multiplayer is on (it starts the game itself, on the co-op copy): Play and Play without mods wait. True
        when they wait (the status says why)."""
        s = self.mp.session
        if s is None or not s.running or self.ctl is None:
            return False
        self.ctl.status = Status("warn", ["Multiplayer is on.", "It starts the game itself: press Stop on the "
                                                                "Multiplayer page first."])
        self.render()
        return True

    def on_details(self) -> None:
        """What the last step printed, in its own window, with Copy (the home folder is written as %USERPROFILE%,
        so a bug report doesn't carry the player's name)."""
        t = self.t
        text = report_text(self.ctl.details_text())
        top = tk.Toplevel(self.root, bg=t["bg"], padx=16, pady=16)
        top.title("Details")
        top.transient(self.root)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)
        box = tk.Text(top, font=self.mono, bg=t["bg"], fg=t["fg"], wrap="word", width=70, height=14,
                      highlightthickness=4, highlightcolor=t["focus"], highlightbackground=t["line"], padx=8, pady=6)
        box.insert("end", text)
        box.configure(state="disabled")
        scroll = ttk.Scrollbar(top, orient="vertical", command=box.yview, style="Mods.Vertical.TScrollbar")
        box.configure(yscrollcommand=scroll.set)
        box.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        row = tk.Frame(top, bg=t["bg"])
        row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(12, 0))
        group: list = []

        def copy():
            top.clipboard_clear()
            top.clipboard_append(text)

        for label, command in (("Copy", copy), ("Close", top.destroy)):
            self.make_button(row, label, command, group).ring.pack(side="left", padx=(0, 8))
        top.bind("<Escape>", lambda e: top.destroy())
        group[-1].focus_set()

    def on_add(self) -> None:
        if not self.ctl.can("add"):
            return self.after_action(lambda: self.ctl.free("add"))
        what = self.ask("Add mod", "What is the mod?", "A folder, or a .zip or other file (patch, cheats, DLL).",
                        [("A folder", "folder"), (".zip or file", "file"), ("Cancel", None)], default=0)
        if what == "folder":
            path = filedialog.askdirectory(parent=self.root, title="Pick the mod folder", initialdir=self.last_dir,
                                           mustexist=True)
        elif what == "file":
            path = filedialog.askopenfilename(parent=self.root, title="Pick the mod file", initialdir=self.last_dir,
                                              filetypes=[("Mods", "*.breath *.zip *.bnp *.ips *.pchtxt *.txt *.dll"),
                                                         ("All files", "*.*")])
        else:
            return None
        if path:
            path = os.path.normpath(path)
            self.last_dir = os.path.dirname(path)
            if path.lower().endswith(la.BREATH_EXT):
                self.open_breath(path)
            else:
                self.after_action(lambda: self.ctl.add(path))
        return None

    # --- .breath files: add one, make one

    def on_add_breath(self) -> None:
        if not self.ctl.can("add"):
            return self.after_action(lambda: self.ctl.free("add"))
        path = filedialog.askopenfilename(parent=self.root, title="Pick the .breath file", initialdir=self.last_dir,
                                          filetypes=[(".breath files", "*" + la.BREATH_EXT), ("All files", "*.*")])
        if path:
            path = os.path.normpath(path)
            self.last_dir = os.path.dirname(path)
            self.open_breath(path)
        return None

    def open_breath(self, path: str) -> None:
        """A .breath to add (Add a .breath, Add mod, a double-click): checked as soon as Home is open and nothing
        else runs, then "Add this mod?"."""
        self.pending_breath.append(path)
        self.next_breath()

    def next_breath(self) -> None:
        ctl = self.ctl
        if not self.pending_breath or ctl is None or self.closed or ctl.breath is not None \
                or self.screen not in ("home", "settings") or self.playing() or not ctl.can("add"):
            return   # (it waits: while you play it is never checked, unpacked or shown; it is asked after)
        path = self.pending_breath.pop(0)
        if self.screen != "home":
            self.show("home")
        with contextlib.suppress(tk.TclError):   # (a window you minimized comes back: you opened a .breath)
            state = self.root.state()
            if state == "iconic":
                self.root.deiconify()
            if state != "withdrawn":
                self.root.lift()
        self.after_action(lambda: ctl.open_breath(path, self.received_dir))

    def on_export(self) -> None:
        """Export as .breath: the selected mod in one file to share (a mod built from your game files can't be)."""
        if not self.ctl.can("add"):
            return self.after_action(lambda: self.ctl.free("add"))
        if self.selected_id() == EGG_ROW:
            self.ctl.status = Status("info", ["Easter eggs can't be exported.", "Pick another mod in the list."])
            return self.render()
        row = self.ctl.row(self.selected_id())
        if row is None:
            self.ctl.status = Status("info", ["Pick a mod in the list first.", "Then press Export as .breath."])
            return self.render()
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", row.name).strip(" .") or "mod"
        desktop = la.known_folders().get("Desktop", "")
        path = filedialog.asksaveasfilename(
            parent=self.root, title=f"Save {row.name} as a .breath file", defaultextension=la.BREATH_EXT,
            initialfile=name + la.BREATH_EXT, initialdir=desktop if os.path.isdir(desktop) else self.last_dir,
            filetypes=[(".breath files", "*" + la.BREATH_EXT)])
        if not path:
            return None
        path = os.path.normpath(path)
        recipes = {m["id"] for m in self.bundled if m.get("kind") == "recipe"}
        self.root.configure(cursor="watch")   # (what goes in is listed first: a big mod takes a moment)
        self.root.update_idletasks()
        try:
            plan = self.ctl.plan_export(row.id, path, recipes)
        finally:
            self.root.configure(cursor="")
        if plan is None:   # (the status says why)
            return self.render()
        # (what goes in, before anything is written: the files the mod was added with, nothing else of that folder)
        if not self.ask("Export as .breath", f"Make {os.path.basename(plan.dest)}?",
                        f"It will hold {plan.summary()}\n\nAnyone you send it to gets these files.",
                        [("Make it", True), ("Cancel", None)], default=0):
            self.ctl.status = Status("info", ["Not made.", "Nothing was written."])
            return self.render()
        self.after_action(lambda: self.ctl.export_breath(row.id, path, recipes, plan=plan))
        return None

    def build_breath(self) -> None:
        """The "Add this mod?" screen (show_breath fills it): the mod's title and the warnings (breath_head), then
        right under them the buttons, then the rest (breath_body), so on a small screen (768 px tall) with big text
        it is the rest that falls off the bottom, never the buttons."""
        t, f = self.t, self.frames["breath"]
        self.label(f, self.big, text="Add this mod?").grid(row=0, column=0, sticky="w", padx=PAD, pady=(8, 6))
        self.breath_head = tk.Frame(f, bg=t["bg"])
        self.breath_head.grid(row=1, column=0, sticky="ew", padx=PAD)
        self.breath_head.columnconfigure(1, weight=1)
        self.breath_flow = Flow(f, gap=4, bg=t["bg"])
        self.flows.append(self.breath_flow)
        self.breath_flow.frame.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(6, 10))
        self.breath_body = tk.Frame(f, bg=t["bg"])
        self.breath_body.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
        self.breath_body.columnconfigure(1, weight=1)
        if self.screen == "breath" and self.ctl is not None and self.ctl.breath is not None:
            self.fill_breath()   # (built again for another theme or text size while it was open)

    def show_breath(self) -> None:
        self.fill_breath()
        self.show("breath")
        if self.breath_buttons:
            self.breath_buttons[self.breath_focus].focus_set()

    def fill_breath(self) -> None:
        """The title big; for a mod with code (or that may have code) a warning in red right under it, and for code
        patches or cheats a milder one; then Add this mod and Not now (focused for a mod with code), right there;
        then who made it and its version; what it does (a few lines at most); what it is, the file."""
        t, body, rec = self.t, self.breath_head, self.ctl.breath
        import breath as br
        man = rec.manifest
        for frame in (self.breath_head, self.breath_body, self.breath_flow.frame):
            for w in frame.winfo_children():
                w.destroy()
        self.wraps = [(w, s) for w, s in self.wraps if w.winfo_exists()]
        self.breath_buttons, self.breath_flow.items = [], []
        row = 0

        def line(text: str, font=None, fg=None, pady=(0, 4)):
            nonlocal row
            lab = self.wrap_label(body, font, text=text, fg=fg or t["fg"])
            lab.grid(row=row, column=0, columnspan=2, sticky="w", pady=pady)
            row += 1
            return lab

        def names_of(paths: list[str]) -> str:
            shown = list(dict.fromkeys(br.shown_name(p) for p in paths))
            return ", ".join(shown[:4]) + (f" and {len(shown) - 4} more" if len(shown) > 4 else "")

        def warning(color: str, head: str, more: str) -> None:
            nonlocal row
            box = tk.Frame(body, bg=t["bg"])
            box.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(2, 8))
            box.columnconfigure(1, weight=1)
            tk.Frame(box, width=10, bg=color).grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 14))
            self.wrap_label(box, self.head, share=0.9, text=head, fg=color).grid(row=0, column=1, sticky="w")
            self.wrap_label(box, self.bold, share=0.9, text=more).grid(row=1, column=1, sticky="w")
            row += 1

        line(man.title, self.head, pady=(0, 6))
        code = list(dict.fromkeys(rec.code))
        trust = "Only add it if you trust the person who made it."
        if code:
            warning(t["error"], f"Careful: this mod has code ({names_of(code)}).",
                    f"Code runs on your PC like a program, with your rights. {trust}")
        elif rec.maybe_code:
            warning(t["error"], "Careful: this package may have code.",
                    f"Breathalyzer couldn't look all through it, and code runs on your PC like a program. {trust}")
        patches = list(dict.fromkeys(rec.patches))
        if patches:
            warning(t["warn"], f"This mod changes the game's code ({names_of(patches)}).",
                    f"Code patches and cheats can make the game crash or act oddly. {trust}")
        have = {r.id: r for r in self.ctl.listing.rows}
        if man.id in have:
            line(f"You already have this mod ({have[man.id].name}). To use this copy, remove yours first (More...).",
                 self.bold, t["warn"])
        body, row = self.breath_body, 0   # (the buttons come here, between the two: fill_breath_buttons)
        by = [f"By {man.author}" if man.author else "Author not given", f"Version {man.version}" if man.version
              else ""]
        line(". ".join(x for x in by if x) + ".")
        self.breath_desc = (line("", pady=(4, 8)), man.description or "No description.")
        self.cut_breath_desc()
        line(man.what())
        line(f"{br.shown_name(rec.source)}, {br.size_text(man.size)}. Not damaged. This doesn't mean it is safe: "
             "only add mods from people you trust.", fg=t["disabled"], pady=(4, 8))
        self.breath_focus = 1 if code or rec.maybe_code else 0
        self.fill_breath_buttons()

    def cut_breath_desc(self) -> None:
        """The description in BREATH_LINES lines at most, at the width it has now (a word too long for one line is
        broken, as Tk would show it): whoever made the .breath can't push the warning or the buttons off the
        screen with it."""
        lab, full = getattr(self, "breath_desc", (None, ""))
        if lab is None or not lab.winfo_exists():
            return
        width = int(lab.cget("wraplength") or 0) or self.text_width()
        paras = []
        for para in full.split("\n"):
            words = []
            for word in para.split(" "):
                while word and self.font.measure(word) > width:
                    lo, hi = 1, len(word)
                    while lo < hi:   # (the longest start of the word that fits)
                        mid = (lo + hi + 1) // 2
                        if self.font.measure(word[:mid]) <= width:
                            lo = mid
                        else:
                            hi = mid - 1
                    words.append(word[:lo])
                    word = word[lo:]
                words.append(word)
            paras.append(" ".join(words))
        lab.configure(text=cut_lines("\n".join(paras), self.font, width, BREATH_LINES))

    def fill_breath_buttons(self) -> None:
        """Add this mod and Not now, under the rest."""
        for k, (text, command) in enumerate((("Add this mod", self.on_breath_add), ("Not now", self.on_breath_skip))):
            b = self.make_button(self.breath_flow.frame, text, command, self.breath_buttons, bold=k == 0)
            self.breath_flow.items.append(b.ring)
        self.breath_flow.shape = None
        self.root.after_idle(lambda: self.breath_flow.frame.winfo_exists() and self.breath_flow.reflow(force=True))
        self.rewrap()

    def on_breath_add(self) -> None:
        if self.ctl is None:
            return
        self.ctl.add_breath()
        self.show("home")
        self.play.focus_set()
        self.render()

    def on_breath_skip(self) -> None:
        if self.ctl is None:
            return
        self.ctl.drop_breath()
        self.show("home")
        self.play.focus_set()
        self.render()

    def on_check(self) -> None:
        """Check my setup (Settings > Check): the first run's checks, on the Savage Wind folder in use."""
        install = self.ctl.backend.install if self.ctl else self.settings.install
        if not install:
            return
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            self.check_report = self.first.check(install, self.ctl.backend.root if self.ctl else None, "remembered")
        finally:
            self.root.configure(cursor="")
        self.setup_mode = "check"
        self.show_setup()

    def on_open_mods(self) -> None:
        la.open_path(la.nearest_existing(os.path.join(self.ctl.backend.root, "mods")))

    def on_open_game(self) -> None:
        la.open_path(self.ctl.backend.install)

    def on_more(self) -> None:
        """The Mod Manager window (move, remove, check, details, deploy) on the same library; Home waits meanwhile."""
        if self.manager is not None or not self.ctl.can("more"):
            return self.after_action(lambda: self.ctl.free("more"))
        top = tk.Toplevel(self.root)
        top.transient(self.root)
        self.ctl.blocked = True
        try:
            View(top, Controller(Backend(self.ctl.backend.root, mm=self.ctl.backend.mm),
                                 port=self.port or self.settings.port), size=self.size, dark=self.settings.dark)
        except Exception:
            self.ctl.blocked = False
            top.destroy()
            raise
        self.manager = top

        def gone(event):
            if event.widget is top and self.manager is top:
                self.manager = None
                self.ctl.blocked = False
                self.after_action(self.ctl.refresh)

        top.bind("<Destroy>", gone, add="+")
        self.render()
        return None

    # --- Breathalyzer Plus: the mod picker

    def build_picker(self) -> None:
        t, f = self.t, self.frames["picker"]
        self.label(f, self.big, text="Pick your mods").grid(row=0, column=0, sticky="w", padx=PAD, pady=(8, 2))
        self.wrap_label(f, text="Turn each one ON or OFF, then press Save. You can change it later on the Home "
                                "screen.").grid(row=1, column=0, sticky="ew", padx=PAD)
        # (the cards scroll when there are more than fit: the keyboard focus brings its card into view)
        holder = tk.Frame(f, bg=t["bg"])
        holder.grid(row=2, column=0, sticky="nsew", padx=PAD, pady=(12, 6))
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)
        self.picker_canvas = canvas = tk.Canvas(holder, bg=t["bg"], highlightthickness=0, height=1, takefocus=0)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview, style="Mods.Vertical.TScrollbar",
                               takefocus=0)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.picker_body = tk.Frame(canvas, bg=t["bg"])
        self.picker_body.columnconfigure(1, weight=1)
        item = canvas.create_window(0, 0, window=self.picker_body, anchor="nw")
        self.picker_body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
        for w in (canvas, self.picker_body):
            w.bind("<MouseWheel>", self.picker_wheel)
        self.picker_flow = Flow(f, gap=4, bg=t["bg"])
        self.flows.append(self.picker_flow)
        self.picker_flow.frame.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(6, PAD))
        if self.picker_buttons:   # (built again for another theme while it was open)
            self.fill_picker()

    def show_picker(self, from_settings: bool = False) -> None:
        """Our mods, each with ON/OFF, its title, what it does and its notes. A mod already in the library shows
        how it is now; the others their default. The easter eggs are never listed: one "Enable easter eggs?" switch
        for all of them (picks[EGG_ROW]), OFF unless one of them is on now."""
        rows = {r.id: r for r in self.ctl.listing.rows} if self.ctl is not None else {}
        self.picks = {m["id"]: rows[m["id"]].on if m["id"] in rows else m["on"] for m in self.bundled
                      if not m.get("egg")}
        if self.eggs:
            self.picks[EGG_ROW] = any(rows[m["id"]].on for m in self.eggs if m["id"] in rows)
        self.picker_from_settings = from_settings
        self.fill_picker()
        self.show("picker")
        if self.picker_buttons:
            self.picker_buttons[0].focus_set()

    def fill_picker(self) -> None:
        t, body = self.t, self.picker_body
        for w in body.winfo_children():
            w.destroy()
        for w in self.picker_flow.frame.winfo_children():
            w.destroy()
        self.wraps = [(w, s) for w, s in self.wraps if w.winfo_exists()]
        self.picker_buttons, self.pick_buttons, self.picker_flow.items = [], {}, []
        cards = [(m["id"], m["title"], m["description"],
                  [*m["tags"], *([f"Starts {'ON' if m['on'] else 'OFF'}: {m['why'].rstrip('.')}"]
                                 if m.get("why") else [])])   # (the tags, and why it starts on or off)
                 for m in self.bundled if not m.get("egg")]
        if self.eggs:   # (secret: one switch for every egg, never their titles)
            cards.append((EGG_ROW, EGG_ASK, EGG_LINE, []))
        for k, (key, title, text, notes) in enumerate(cards):
            b = self.make_button(body, "", lambda i=key: self.flip_pick(i), self.picker_buttons, bold=True, width=4)
            b.ring.grid(row=k, column=0, sticky="nw", pady=(0, 8))
            b.bind("<FocusIn>", lambda e, ring=b.ring: self.picker_see(ring), add="+")
            self.pick_buttons[key] = b
            self.paint_pick(key)
            card = tk.Frame(body, bg=t["bg"])
            card.grid(row=k, column=1, sticky="ew", padx=(12, 0), pady=(0, 8))
            self.label(card, self.bold, text=title).pack(anchor="w")
            self.wrap_label(card, text=text, share=0.85).pack(anchor="w")
            if notes:
                self.wrap_label(card, text=". ".join(notes) + ".", share=0.85, fg=t["disabled"]).pack(anchor="w")
        buttons = [("Save", self.on_pick_save)] + ([("Back", self.back)] if self.picker_from_settings else [])
        for k, (text, command) in enumerate(buttons):
            b = self.make_button(self.picker_flow.frame, text, command, self.picker_buttons, bold=k == 0)
            self.picker_flow.items.append(b.ring)
        self.picker_flow.shape = None
        self.root.after_idle(lambda: self.picker_flow.frame.winfo_exists() and self.picker_flow.reflow(force=True))
        self.rewrap()
        for w in body.winfo_children():
            for x in (w, *w.winfo_children()):
                x.bind("<MouseWheel>", self.picker_wheel, add="+")
        self.size_picker()

    def size_picker(self) -> None:
        """The cards ask for their whole height, but never more than the screen leaves (then they scroll)."""
        self.root.update_idletasks()
        top = self.sw_label.master.winfo_reqheight() + PAD + 4
        rest = self.frames["picker"].winfo_reqheight() - self.picker_canvas.winfo_reqheight()
        self.picker_canvas.configure(height=max(self.font.metrics("linespace") * 3,
                                                min(self.picker_body.winfo_reqheight(),
                                                    self.room()[1] - top - rest - 8)))

    def picker_see(self, widget) -> None:
        """Scroll the cards so this one shows."""
        canvas = self.picker_canvas
        total = max(1, self.picker_body.winfo_height())
        top, bottom = widget.winfo_y(), widget.winfo_y() + widget.winfo_height()
        first, last = (x * total for x in canvas.yview())
        if top < first:
            canvas.yview_moveto(top / total)
        elif bottom > last:
            canvas.yview_moveto(max(0, bottom - (last - first)) / total)

    def picker_wheel(self, event) -> str:
        self.picker_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"

    def paint_pick(self, mod_id: str) -> None:
        t, on = self.t, self.picks.get(mod_id, False)
        self.pick_buttons[mod_id].configure(text="ON" if on else "OFF", bg=t["play"] if on else t["button"],
                                            fg=t["play_fg"] if on else t["fg"])

    def flip_pick(self, mod_id: str) -> None:
        self.picks[mod_id] = not self.picks.get(mod_id, False)
        self.paint_pick(mod_id)

    def on_pick_save(self) -> None:
        """Remember the answer, go Home, and add the picked mods there (Play waits until they are in)."""
        if self.ctl is None or not self.ctl.can("add"):
            if self.ctl is not None:
                self.after_action(lambda: self.ctl.free("add"))
            return
        self.settings.picked = self.bundled_key
        self.save_settings()
        self.show("home")
        self.play.focus_set()
        picks = {k: v for k, v in self.picks.items() if k != EGG_ROW}
        picks.update({m["id"]: bool(self.picks.get(EGG_ROW)) for m in self.eggs})   # (every egg as the one switch)
        self.after_action(lambda: self.ctl.install_picked(self.bundled, picks))

    # --- Settings

    def show_settings(self) -> None:
        self.fill_settings()
        self.show("settings")
        if self.settings_buttons:
            self.settings_buttons[0].focus_set()

    def fill_settings(self) -> None:
        t, body = self.t, self.settings_body
        for w in body.winfo_children():
            w.destroy()
        self.wraps = [(w, s) for w, s in self.wraps if w.winfo_exists()]
        self.settings_buttons = []
        s = self.settings
        install = (self.ctl.backend.install if self.ctl else None) or s.install or ""
        library = (self.ctl.backend.root if self.ctl else None) or s.library or ""
        rep = getattr(self, "home_report", None)
        has_ini, fps, fps_raw, fps_problem = la.fps_info(install) if install else (False, None, None, "")
        playing = self.game_open_now()
        if not install:
            fps_text = "Not set"
        elif not has_ini:
            fps_text = "Start Savage Wind once first."
        elif fps_problem:
            fps_text = fps_problem
        elif playing:
            fps_text = "Close the game to change it."
        elif self.fps_note:
            fps_text = self.fps_note
        elif fps is None:
            fps_text = f"Your settings say {fps_raw}: pick one."
        else:
            fps_text = "Also in the game: F1, Video."
        tool = os.path.join(install, la.CONFIG_EXE) if install else ""
        sw = [("path", install)] if install else [("text", "Not set")]
        if rep is not None and rep.version:
            sw.append(("text", "Game files: OK" if rep.game_build == la.GAME_BUILD else "Game files: not 1.9.0"))
        state = la.breath_state(self.reg_base, la.breath_command(self.app_dir)[0]) if os.name == "nt" else None
        breath = {"here": ("ON: a double-click adds one", [("Unregister .breath", self.on_unregister)]),
                  "other": ("ON, for another Breathalyzer folder", [("Register .breath", self.on_register),
                                                                    ("Unregister .breath", self.on_unregister)]),
                  "taken": ("Another program opens .breath files: right-click one, Open with, Breathalyzer, Always",
                            [("Unregister .breath", self.on_unregister)]),
                  None: ("OFF: a double-click does nothing", [("Register .breath", self.on_register)])}[state]
        rows = [
            ("Savage Wind", sw, [("Change...", self.on_change_folder), ("Check", self.on_check),
                                 ("Open", lambda: la.open_path(install))]),
            ("Frame rate", [("text", fps_text)],
             [("widget", lambda parent: self.fps_box_for(parent, fps, fps_raw,
                                                         has_ini and not playing and not fps_problem))]
             + ([("Game settings", lambda: la.open_path(tool))] if os.path.isfile(tool) else [])),
            ("Mod library", [("path", library)] if library else [("text", "Not set")],
             [("Open", lambda: la.open_path(la.nearest_existing(library)))] if library else []),
            ("Text size", [("text", f"{self.size} pt")], [("Smaller", lambda: self.set_size(self.size - 2)),
                                                         ("Bigger", lambda: self.set_size(self.size + 2))]),
            ("Dark", [("text", "ON" if s.dark else "OFF")], [("Turn off" if s.dark else "Turn on", self.on_dark)]),
            ("Hide while playing", [("text", "ON" if s.hide_while_playing else "OFF")],
             [("Turn off" if s.hide_while_playing else "Turn on", self.on_hide)]),
            ("Developer commands", [("text", self.dev_text())],
             [("Turn off" if s.dev else "Turn on", self.on_dev)]),
            *([("File log", [("text", "ON: fslog.log lists the files the game opens" if s.file_log else "OFF")],
                [("Turn off" if s.file_log else "Turn on", self.on_file_log)])] if s.dev or s.file_log else []),
            ("Shortcut", [("text", "On your desktop")], [("Make one", self.on_shortcut)]),
            (".breath files", [("text", breath[0])], breath[1]),
        ]
        if self.bundled:
            rows.append(("Our mods", [("text", f"{len(self.bundled) - len(self.eggs)} come with Breathalyzer")],
                         [("Pick again", lambda: self.show_picker(from_settings=True))]))
        for w in self.settings_top.winfo_children():
            w.destroy()
        self.make_button(self.settings_top, "Back", self.back, self.settings_buttons, bold=True).ring.pack(
            side="left")
        self.label(self.settings_top, self.big, text="Settings").pack(side="left", padx=(16, 0))
        self.settings_rows = []   # (name label, value label, value parts, buttons cell)
        for k, (name, value, buttons) in enumerate(rows, 1):
            name_label = self.label(body, self.bold, text=name)
            name_label.grid(row=k, column=0, sticky="w", padx=(0, 16), pady=1)
            line = tk.Frame(body, bg=t["bg"])   # (the value and its buttons: each row's value gets what they leave)
            line.grid(row=k, column=1, sticky="ew", pady=1)
            cell = tk.Frame(line, bg=t["bg"])
            cell.pack(side="right")
            value_label = self.label(line)
            value_label.pack(side="left", fill="x")
            for text, command in buttons:
                if text == "widget":   # (the frame rate's dropdown)
                    command(cell).pack(side="left")
                    continue
                b = self.make_button(cell, text, command, self.settings_buttons)
                b.configure(pady=0)   # (a Tk button has room of its own: nine rows must fit at big text sizes)
                b.ring.pack(side="left")
            self.settings_rows.append((name_label, value_label, value, cell))
        self.layout_settings()

    def layout_settings(self, width: int | None = None) -> None:
        """The values get the width the names and the buttons leave; a long folder is shortened in the middle."""
        rows = [r for r in getattr(self, "settings_rows", []) if r[1].winfo_exists()]
        if not rows:
            return
        self.root.update_idletasks()
        names = max(n.winfo_reqwidth() for n, _, _, _ in rows) + 16
        for _, value_label, parts, cell in rows:
            room = max(self.font.measure("0") * 6, self.text_width(width) - names - cell.winfo_reqwidth() - 16)
            text = "\n".join(self.one_line(x, room) if kind == "path" else x for kind, x in parts)
            value_label.configure(text=text, wraplength=room)

    def save_settings(self) -> None:
        self.settings.size = self.size
        with contextlib.suppress(OSError):
            la.save_settings(self.settings_file, self.settings)

    def on_change_folder(self) -> None:
        if self.ctl is not None and (self.ctl.jobs or self.ctl.game_open):
            self.ask("Savage Wind", "Not now.", "Wait until Play is done and the game is closed, then change the "
                     "folder.", [("OK", None)])
            return
        path = filedialog.askdirectory(parent=self.root, title="Pick your Savage Wind folder", mustexist=True,
                                       initialdir=self.settings.install or self.last_dir)
        if not path:
            return
        self.setup_mode = "change"
        self.first.library = None
        self.first.pick(os.path.normpath(path))
        self.show_setup()

    def on_dark(self) -> None:
        self.settings.dark = not self.settings.dark
        self.save_settings()
        self.t = THEMES["dark" if self.settings.dark else "light"]
        self.build()
        self.show_settings()
        self.fit()

    def on_hide(self) -> None:
        self.settings.hide_while_playing = not self.settings.hide_while_playing
        self.save_settings()
        self.show_settings()

    def dev_text(self) -> str:
        """Settings' Developer commands value: ON, or OFF and whether a mod turns them on anyway."""
        if self.settings.dev or self.port:
            return "ON"
        mods = self.ctl.dev_mods_on() if self.ctl is not None else []
        if mods:
            return f"OFF, but ON while {', '.join(mods)} {'is' if len(mods) == 1 else 'are'} on"
        return "OFF"

    def on_dev(self) -> None:
        """Developer commands ON: Play opens the game's control port, so the commands the game keeps for developers
        (like up and infstamina) work. OFF: only while a mod that needs them is on."""
        self.settings.dev = not self.settings.dev
        self.save_settings()
        if self.ctl is not None:
            self.ctl.dev = self.settings.dev
        self.show_settings()

    def on_file_log(self) -> None:
        """File log ON: Play starts the game with SWMOD_FILE_LOG=1, and modhost writes fslog.log in the Savage Wind
        folder: every file the game opens (docs/code-mods.md, "File log"). Only a list: nothing is changed. The row
        shows while Developer commands is ON (or the file log is). OFF by default."""
        self.settings.file_log = not self.settings.file_log
        self.save_settings()
        if self.ctl is not None:
            self.ctl.file_log = self.settings.file_log
        self.show_settings()

    def on_shortcut(self) -> None:
        desktop = la.known_folders().get("Desktop", "")
        path = os.path.join(desktop, "Breathalyzer.lnk")
        try:
            target, args, workdir, icon = la.shortcut_parts()
            la.write_shortcut(path, target, args, workdir, icon, "Start Breathalyzer and play Savage Wind")
        except OSError as e:
            self.ask("Shortcut", "The shortcut couldn't be made.", str(e), [("OK", None)])
            return
        self.ask("Shortcut", "Made a shortcut on your desktop.",
                 f"{path}\n\nIt starts this copy of Breathalyzer. After you unpack a new one, make it again there.",
                 [("OK", None)])

    # --- Settings: the frame rate, .breath files

    def game_open_now(self) -> bool:
        ctl = self.ctl
        if ctl is not None and (ctl.game_open or "play" in ctl.jobs):
            return True
        try:
            return bool(self.running())
        except Exception:  # (then it counts as open: the frame rate is never written under the game)
            return True

    def fps_box_for(self, parent, value: str | None, raw: str | None, enabled: bool):
        """The frame rate's dropdown: 30, 60, 90, 120 fps or No limit (Savage Wind's own choices), showing what
        savagewind.ini says now. Greyed out while the game is open (it writes the file itself)."""
        t = self.t
        ring = tk.Frame(parent, bg=t["bg"])
        labels = [label for _, label in la.FPS_CHOICES]
        box = ttk.Combobox(ring, values=labels, state="readonly" if enabled else "disabled", font=self.font,
                           width=max(len(x) for x in labels) + 1, style="Big.TCombobox", takefocus=1)
        box.set(la.fps_label(value) if value else (raw or ""))
        box.pack(padx=5, pady=5)
        box.bind("<FocusIn>", lambda e: ring.configure(bg=t["focus"]), add="+")
        box.bind("<FocusOut>", lambda e: ring.configure(bg=t["bg"]), add="+")
        # (Settings is built again after a pick: not inside the dropdown's own event)
        box.bind("<<ComboboxSelected>>", lambda e: self.root.after_idle(self.on_fps, box.get()))
        for key in ("<Return>", "<space>", "<KP_Enter>"):   # (open the list from the keyboard too)
            box.bind(key, lambda e: (box.event_generate("<Down>"), "break")[1])
        # (the mouse wheel over a closed dropdown would pick the next frame rate and write it, unseen: it does
        # nothing here; a frame rate is written only when picked from the open list)
        for seq in WHEEL_EVENTS:
            box.bind(seq, lambda e: "break")
        self.fps_box = box
        return ring

    def on_fps(self, label: str) -> None:
        """A frame rate picked: written into savagewind.ini (only that line) while the game is closed."""
        value = next((k for k, text in la.FPS_CHOICES if text == label), None)
        install = (self.ctl.backend.install if self.ctl else None) or self.settings.install
        if value is None or not install:
            return
        try:
            changed = la.set_frame_rate(install, value, running=self.game_open_now)
        except la.FpsError as e:
            self.fps_note = ""
            self.show_settings()
            self.ask("Frame rate", "The frame rate wasn't changed.", str(e), [("OK", None)])
            return
        if changed:
            self.fps_note = f"{label}. Used from your next start."
        self.show_settings()
        with contextlib.suppress(tk.TclError, AttributeError):
            self.fps_box.focus_set()

    def on_register(self) -> None:
        """Register .breath: a double-click on a .breath file opens this Breathalyzer with it (this Windows user
        only, no admin)."""
        try:
            command, icon = la.breath_command(self.app_dir)
            la.register_breath(command, icon, self.reg_base)
        except OSError as e:
            self.ask(".breath files", "That didn't work.", str(e), [("OK", None)])
            return
        self.show_settings()
        self.ask(".breath files", "Done.", "A double-click on a .breath file now opens Breathalyzer, which asks "
                 "before it adds the mod. Only for your Windows user. Unregister .breath undoes it.", [("OK", None)])

    def on_unregister(self) -> None:
        try:
            la.unregister_breath(self.reg_base)
        except OSError as e:
            self.ask(".breath files", "That didn't work.", str(e), [("OK", None)])
            return
        self.show_settings()
        self.ask(".breath files", "Done.", "A double-click on a .breath file doesn't open Breathalyzer any more. "
                 "Add a .breath on Home still adds one.", [("OK", None)])

    def ask_text(self, title: str, message: str, detail: str, value: str) -> str | None:
        """One line of text in the same big text; None when left with Cancel or Esc."""
        t = self.t
        top = tk.Toplevel(self.root, bg=t["bg"], padx=24, pady=20)
        top.title(title)
        top.transient(self.root)
        result = {"value": None}
        wrap = max(320, self.font.measure("0") * 36)
        self.label(top, self.head, text=message, wraplength=wrap).pack(anchor="w")
        self.label(top, text=detail, wraplength=wrap).pack(anchor="w", pady=(10, 0))
        entry = tk.Entry(top, font=self.font, bg=t["bg"], fg=t["fg"], insertbackground=t["fg"], width=12,
                         highlightthickness=4, highlightcolor=t["focus"], highlightbackground=t["line"])
        entry.insert(0, value)
        entry.pack(anchor="w", pady=(12, 0))
        row = tk.Frame(top, bg=t["bg"])
        row.pack(anchor="w", pady=(16, 0))

        def done(ok):
            result["value"] = entry.get() if ok else None
            top.destroy()

        group: list = []
        for text, ok in (("Save", True), ("Cancel", False)):
            self.make_button(row, text, lambda ok=ok: done(ok), group).ring.pack(side="left", padx=(0, 8))
        entry.bind("<Return>", lambda e: done(True))
        top.bind("<Escape>", lambda e: done(False))
        top.protocol("WM_DELETE_WINDOW", lambda: done(False))
        entry.focus_set()
        entry.select_range(0, "end")
        top.after(20, lambda: top.winfo_exists() and top.grab_set())
        self.root.wait_window(top)
        return result["value"]

    # --- size

    def apply_size(self) -> None:
        for f in (self.font, self.bold):
            f.configure(size=self.size)
        self.head.configure(size=self.size + 4)
        self.big.configure(size=self.size + 10)
        self.play_font.configure(size=self.play_size())
        self.mono.configure(size=max(MIN_SIZE, self.size - 4))
        self.style_tree()
        if self.screen == "picker":
            self.size_picker()
        for group in (self.home_buttons, list(self.by_action.values()), self.setup_buttons, self.settings_buttons,
                      self.picker_buttons, self.breath_buttons, *self.mp.groups()):
            flat = group is self.settings_buttons
            for b in group:
                with contextlib.suppress(tk.TclError):
                    b.configure(padx=self.size, pady=0 if b is self.play or flat else self.size // 3)

    def set_size(self, size: int) -> str:
        size = max(MIN_SIZE, min(MAX_SIZE, size))
        if size == self.size:
            return "break"
        old, self.size = self.size, size
        self.apply_size()
        if size > old and not (self.fit() or self.fit()):   # (twice: wrapped text settles in the first)
            self.size = old
            self.apply_size()
            self.fit()
            if self.ctl is not None and self.screen == "home":
                self.ctl.status = Status("info", ["This is the biggest text that fits on this screen."])
        else:
            self.fit()
        self.save_settings()
        if self.screen == "settings":
            self.show_settings()
        self.render()
        return "break"

    def room(self) -> tuple[int, int]:
        return int(self.root.winfo_screenwidth() * 0.96), int(self.root.winfo_screenheight() * 0.88)

    def need(self, width: int) -> int:
        """The height every screen needs at this width (the list with HOME_ROWS rows)."""
        self.rewrap(width)
        self.root.update_idletasks()   # (first: a Flow lays itself out again at the width it has now)
        for fl in self.flows:
            fl.frame.configure(height=fl.height_for(width - 2 * PAD))
        if self.screen == "picker":
            self.size_picker()   # (its cards take what the screen leaves at this width)
        self.root.update_idletasks()
        top = self.sw_label.master.winfo_reqheight() + PAD + 4
        return top + max(f.winfo_reqheight() for k, f in self.frames.items()
                         if k not in ("picker", "breath", "multiplayer", "who") or self.screen == k)

    def fit(self, shrink: bool = False) -> bool:
        """Make the window as big as the text needs, up to the screen. False when it doesn't fit; with shrink, the
        text gets smaller until it does."""
        r = self.root
        room_w, room_h = self.room()
        while True:
            shown = bool(r.winfo_ismapped())
            cur_w, cur_h = (r.winfo_width(), r.winfo_height()) if shown else (0, 0)
            w = min(room_w, max(cur_w, self.font.measure("0") * 72))
            need = self.need(w)
            if need <= room_h or not shrink or self.size <= MIN_SIZE:
                break
            self.size = max(MIN_SIZE, self.size - 2)
            self.apply_size()
        h = min(room_h, max(cur_h, need, self.font.metrics("linespace") * 20))
        if (w, h) != (cur_w, cur_h) and r.state() != "zoomed":
            sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
            x, y = (r.winfo_x(), r.winfo_y()) if shown else ((sw - w) // 2, (sh - h) // 3)
            r.geometry(f"{w}x{h}+{max(0, min(x, sw - w - 16))}+{max(0, min(y, sh - h - 90))}")
        r.minsize(min(w, max(480, self.font.measure("0") * 40)), min(room_h, need))
        self.rewrap(w)
        for fl in self.flows:
            r.after_idle(lambda fl=fl: fl.frame.winfo_exists() and fl.reflow(force=True))
        return need <= room_h

    def on_resize(self, event) -> None:
        if event.widget is self.root and event.width != self.width:
            self.width = event.width
            self.rewrap()

    def text_width(self, width: int | None = None) -> int:
        return max(200, (width or self.root.winfo_width()) - 2 * PAD - 40)

    def rewrap(self, width: int | None = None) -> None:
        text_w = self.text_width(width)
        for lab, share in self.wraps:
            with contextlib.suppress(tk.TclError):
                lab.configure(wraplength=int(text_w * share))
        if self.ctl is not None and self.screen == "home":
            self.render_about()
            self.size_columns()
        if self.screen == "settings" or width is not None:
            self.layout_settings(width)
        self.cut_breath_desc()   # (the description's few lines at the new width)

    # --- closing

    def on_close(self) -> None:
        job = self.ctl.jobs.get("main") if self.ctl else None
        if job is not None and job.action in ("apply", "deploy") and not self.ask(
                "Close", "Your mods are still being made ready.", "Closing now stops it; the next Play starts over.",
                [("Stop and close", True), ("Keep going", False)], default=1):
            return
        if self.mp.session is not None and self.mp.session.running and not self.ask(
                "Close", "Multiplayer is on.", "Closing Breathalyzer stops it: your friends leave your world (or you "
                "leave theirs). The game keeps running, on your multiplayer copy.",
                [("Stop and close", True), ("Keep going", False)],
                default=1):
            return
        self.mp.close()   # (Multiplayer ends with the window: a host tells its friends)
        if self.ctl is not None:
            self.ctl.stop()
        self.save_settings()
        if self.window_published:
            la.forget_window(self.home, os.getpid())
        self.closed = True
        self.root.destroy()


def launcher_main(args) -> int:
    """tools/launcher.py: one launcher window per settings folder, "Starting..." while modman loads, then the
    first-run screens or Home."""
    if tk is None:   # (launcher.main shows it in a message box and writes launcher-error.txt: pythonw has no console)
        raise RuntimeError(f"Python's tkinter couldn't be loaded ({TK_ERROR or 'no reason given'}).")
    path = la.settings_path()
    settings, note = la.load_settings(path)
    breath_file = os.path.abspath(args.file) if getattr(args, "file", None) else None
    if la.single_instance(os.path.dirname(path)) is None:
        if breath_file:   # (a double-click on a .breath while the window is open: that window asks)
            la.pass_file(breath_file, os.path.join(os.path.dirname(path), la.OPEN_DIR))
        return 0   # (the open window came to the front)
    la.set_app_id()
    dpi_aware()
    try:
        root = tk.Tk()
    except tk.TclError as e:
        raise RuntimeError(f"Can't open a window: {e}") from e
    if args.size:
        settings.size = args.size
    if args.dark:
        settings.dark = True
    t = THEMES["dark" if settings.dark else "light"]
    root.title(la.WINDOW_TITLE)
    root.configure(bg=t["bg"])
    starting = tk.Label(root, text="Starting Breathalyzer...", font=("Segoe UI", 24), bg=t["bg"], fg=t["fg"],
                        padx=60, pady=40)
    starting.pack()
    root.update()
    mm = load_modman()   # (the slow import, while "Starting..." shows)
    if sys.platform == "win32":
        mm.game_running = la.game_running   # (modman's asks tasklist: a console window would flash up under pythonw)
    places = None
    if args.search_in:
        folders = [os.path.abspath(d) for d in args.search_in]
        places = lambda: {"near": [p for d in folders for p in (d, *la.subfolders(d))]}  # noqa: E731
    first = la.FirstRun(settings, places=places, library=args.library)
    starting.destroy()
    view = LauncherView(root, settings, path, first_run=first, port=args.port, library=args.library)
    view.start_publishing()   # (window.json: a second start brings only this window up, never while you play)
    if breath_file:   # (asked once Home is open: now, or after the first-run screens)
        view.open_breath(breath_file)
    if note:   # (on Home now, or on Home after the first-run screens: never dropped)
        view.note = note
        if view.ctl is not None:
            view.ctl.status = Status("warn", [note])
            view.note = None
            view.render()
    root.lift()
    with contextlib.suppress(tk.TclError):
        root.focus_force()
    root.mainloop()
    return 0


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


def set_app_icon(root, icons=None) -> str:
    """Gives the window Breathalyzer's icon: assets/breathalyzer.ico (on Windows), else assets/breathalyzer.png.
    Returns the file it used, or "" when there is none (or Tk can't read it): then the window keeps Tk's icon."""
    for path in APP_ICONS if icons is None else icons:
        if not os.path.isfile(path):
            continue
        try:
            if path.lower().endswith(".ico"):
                if os.name != "nt":   # (Tk reads .ico files only on Windows)
                    continue
                root.iconbitmap(default=path)
            else:
                img = tk.PhotoImage(master=root, file=path)
                root.iconphoto(True, img)
                root.app_icon = img   # (Tk drops an image Python no longer holds)
            return path
        except (tk.TclError, OSError):
            continue
    return ""


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="modman_gui.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--library", help="the mod library folder (default: SWMOD_LIBRARY, else 'modloader' next to the "
                                      "Savage Wind folder), as for modman")
    ap.add_argument("--size", type=size_arg, default=DEFAULT_SIZE,
                    help=f"text size in points, {MIN_SIZE}-{MAX_SIZE} (default {DEFAULT_SIZE}); Ctrl + and Ctrl - "
                         "change it in the window")
    ap.add_argument("--dark", action="store_true", help="light text on a dark background")
    ap.add_argument("--port", type=port_arg, help="control port for the cheats when Play starts the game, as for "
                                                  "modman run (default 7777)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if tk is None:
        print("This window needs Python's tkinter. Use tools/modman.py instead.")
        return 2
    backend = Backend(args.library)
    dpi_aware()
    try:
        root = tk.Tk()
    except tk.TclError as e:
        print(f"Can't open a window: {e}")
        return 2
    View(root, Controller(backend, port=args.port), size=args.size, dark=args.dark)
    root.lift()
    with contextlib.suppress(tk.TclError):
        root.focus_force()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
