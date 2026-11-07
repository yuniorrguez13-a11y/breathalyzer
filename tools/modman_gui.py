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

try:
    import tkinter as tk
    from tkinter import filedialog, ttk
    from tkinter import font as tkfont
except ImportError:  # (the logic works without Tk; only the window needs it)
    tk = None

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
MODMAN_PY = os.path.join(TOOLS, "modman.py")

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
KEYS_HINT = "Keys: Tab, arrows, Space, Enter. Ctrl + and Ctrl - change the text size."
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

KIND_LABELS = {"romfs": "RomFS", "dlc-romfs": "DLC RomFS", "exefs-patch": "Patch", "cheats": "Cheats",
               "native": "Code"}
BUSY = {"add": "Adding the mod", "remove": "Removing the mod", "deploy": "Deploying", "check": "Checking",
        "play": "Starting the game"}
BUTTON = {"add": "Add mod", "remove": "Remove", "deploy": "Deploy", "check": "Check", "play": "Play"}
# What run prints that the status shows while the game runs.
PLAY_MARKS = ("Started Savage Wind", "Started cheats", "Warning: ", "The cheats stopped", "The game closed before",
              "Savage Wind closed")
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
    lines: list[str] = field(default_factory=list)
    dropped: int = 0           # lines no longer kept (the oldest)
    code: int | None = None
    started: float = 0.0
    ended: float | None = None
    game_started: bool = False
    proc: object = None

    @property
    def slot(self) -> str:
        return "play" if self.action == "play" else "main"

    def last_line(self) -> str:
        return next((x.strip() for x in reversed(self.lines) if x.strip()), "")


def kind_text(kinds: list[str]) -> str:
    return ", ".join(KIND_LABELS.get(k, k) for k in kinds) or "-"


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

    def listing(self) -> Listing:
        try:
            lib = self.mm.Library.load(self.root)
        except (self.mm.ModmanError, OSError, ValueError) as e:
            return Listing(self.root, [], True, str(e))
        if lib is None:
            return Listing(self.root, [], False)
        rows = [Row(k, m["id"], m.get("name") or m["id"], bool(m.get("enabled")), list(m.get("kinds") or []),
                    m.get("version")) for k, m in enumerate(lib.mods, 1)]
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

    def argv(self, args: list[str]) -> list[str]:
        return [self.python, "-u", MODMAN_PY, *args, "--library", self.root]

    def _subprocess(self, job: Job, events: queue.Queue) -> None:
        try:  # (UTF-8: a pipe would get the ANSI code page, and mod names outside it would turn into "?")
            proc = self.spawn(self.argv(job.args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
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

        with self.piped_cheats(on_line) if job.action == "play" else contextlib.nullcontext():
            code = self.call(job.args, on_line)
        events.put(("done", job, code))

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
        return not ("play" in self.jobs and action in ("deploy", "play"))

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
        head = [f"modman {' '.join(job.args)}   (library: {self.listing.root})"]
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

    def toggle(self, mod_id: str | None) -> None:
        row = self._ready("toggle", mod_id)
        if row:
            self._quick("disable" if row.on else "enable", [str(row.number)], row)

    def move(self, mod_id: str | None, delta: int) -> None:
        row = self._ready("move", mod_id)
        if not row:
            return
        pos = row.number + delta
        if not 1 <= pos <= len(self.listing.rows):
            self.status = Status("info", [f"{row.name} is already {'first' if delta < 0 else 'last'}."])
            return
        self._quick("move", [str(row.number), str(pos)], row)

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
            select = self.listing.rows[min(job.number, len(self.listing.rows)) - 1].id  # (the one now in its place)
        if select:
            self._select = select
            self.list_version += 1
        self.status = summarize(job, name)
        if job.action != "check":
            self.refresh_state()


# ---------------------------------------------------------------- the window

THEMES = {
    "light": {"bg": "#ffffff", "fg": "#000000", "button": "#e3e8ef", "active": "#c7d4e5", "line": "#6e6e6e",
              "focus": "#0045d0", "sel_bg": "#0045d0", "sel_fg": "#ffffff", "disabled": "#595959",
              "ok": "#00661a", "warn": "#8a4b00", "error": "#b00000", "busy": "#0045d0", "info": "#0045d0"},
    "dark": {"bg": "#000000", "fg": "#ffffff", "button": "#262626", "active": "#3d3d3d", "line": "#9a9a9a",
             "focus": "#ffd400", "sel_bg": "#ffd400", "sel_fg": "#000000", "disabled": "#a6a6a6",
             "ok": "#5ee07a", "warn": "#ffb347", "error": "#ff7b7b", "busy": "#ffd400", "info": "#ffd400"},
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


class View:
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

    def build(self) -> None:
        t, r, pad = self.t, self.root, PAD
        r.title(TITLE)
        set_app_icon(r)
        r.configure(bg=t["bg"])
        r.columnconfigure(0, weight=1)
        # results first
        self.status_box = box = tk.Frame(r, bg=t["bg"])
        box.grid(row=0, column=0, sticky="ew", padx=pad, pady=(pad, 10))
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
                ("check", "Check", self.on_check, False, 0),
                ("details", "Details", self.on_details, False, 12)):
            if action == "deploy":
                self.flow.items.append(None)  # (the game's buttons on a row of their own)
            gate = "move" if action == "move_down" else action
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
        self.summary.configure(text=ctl.summary_line())
        self.render_footer()
        for action, b in self.by_action.items():
            b.configure(state="normal" if ctl.can("move" if action == "move_down" else action) else "disabled")
        self.update_toggle()
        self.render_progress()
        if self.details_shown:
            self.render_details()

    def render_list(self) -> None:
        keep = self.ctl.take_select() or self.selected_id()
        self.tree.delete(*self.tree.get_children())
        rows = self.ctl.listing.rows
        for row in rows:
            self.tree.insert("", "end", iid=row.id, values=row.cells())
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

    def key(self, fn) -> str:
        fn()
        return "break"

    # --- the buttons

    def after_action(self, fn) -> None:
        fn()
        self.render()

    def on_toggle(self) -> None:
        self.after_action(lambda: self.ctl.toggle(self.selected_id()))

    def on_move(self, delta: int) -> None:
        self.after_action(lambda: self.ctl.move(self.selected_id(), delta))

    def on_deploy(self) -> None:
        if self.install_known("deploy"):
            self.after_action(self.ctl.deploy)

    def on_play(self) -> None:
        if self.install_known("play"):
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

    def ask(self, title: str, message: str, detail: str, choices: list, default: int = 0):
        """A question in the same big text. Returns the value of the button pressed; Esc or closing it gives the
        last button's."""
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
        self.label(top, self.head, text=message, wraplength=wrap).pack(anchor="w")
        if detail:
            self.label(top, text=detail, wraplength=wrap).pack(anchor="w", pady=(10, 0))
        row = tk.Frame(top, bg=t["bg"])
        row.pack(anchor="w", pady=(20, 0))
        group: list = []
        for text, value in choices:
            self.make_button(row, text, lambda v=value: done(v), group).ring.pack(side="left", padx=(0, 8))
        top.bind("<Escape>", lambda e: done(choices[-1][1]))
        top.protocol("WM_DELETE_WINDOW", lambda: done(choices[-1][1]))
        top.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - top.winfo_reqwidth()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - top.winfo_reqheight()) // 3)
        top.geometry(f"+{x}+{y}")
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
