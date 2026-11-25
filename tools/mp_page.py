#!/usr/bin/env python3
"""Breathalyzer's Multiplayer page (Home > Multiplayer; docs/multiplayer.md): Host, Join, Who am I, Story together
and Stop, without a command line. Home network only: there is no server of ours anywhere, the host's PC is it.

What it starts, each with the launcher's own Python (sys.executable: app\\runtime\\python.exe in the player zips) and
--library last; their output is said in plain words (Details keeps it, the home folder written as %USERPROFILE%):
    Host:  tools/mp/coop_agent.py host   the session on this PC: the relay, the Join list's answers, your hour and,
                                         with Story together, your story (from your world's save);
           (the first Host on this PC waits at "Your world is open" for Start the game: Windows asks about Python
           then, and its question isn't hidden behind the game)
           then the game, through the launcher's own Play;
           then tools/mp/client.py       your friends in your game (--ghost-looks: as who they picked)
    Join:  tools/mp/coop_agent.py join   the host's hour, and the host's story when you say yes (added to your
                                         co-op save before your game starts);
           then the game through Play, then tools/mp/client.py
Play is the launcher's (LaunchController.launch): it gets your mods ready, so the ghost mod loads, and opens the
control port the tools talk to (Ghost multiplayer is a developer = true mod). Multiplayer always plays on a co-op
COPY of your save (<library>\\multiplayer\\worlds\\ or joined\\, tools/mp/worlds.py), started with
SAVAGEWIND_SAVE_DIR; your own save folder (<Savage Wind>\\save) is only read, to make the copy once. Stop, the game
closing, or the window closing end the tools through their stdin (a host tells its friends first); a tool that
doesn't end in time is killed.

MpSession is the page without widgets (tests drive it with stand-in processes, a stand-in Play and a stand-in Join
list); MpPage draws it in the launcher's window (tools/modman_gui.py LauncherView), with its Who am I page.
"""
from __future__ import annotations

import contextlib
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

GHOST_ID = "botw-mod-loader.ghost"
CHARS_ID = "botw-mod-loader.characters"            # (charsel.MOD_ID: what the Characters recipe adds)
MP_DIR = "multiplayer"     # in the mod library: keys, bans, the story list, the co-op save copies and their backups
AGENT_PY = os.path.join(TOOLS, "mp", "coop_agent.py")
CLIENT_PY = os.path.join(TOOLS, "mp", "client.py")
RELAY_PORT = 7788          # (mp.DEFAULT_RELAY_PORT)
CONTROL_PORT = 7777        # the game's control port (modman_gui.CONTROL_PORT)
STOP_WAIT = 6.0            # seconds the tools get to end cleanly after Stop; then they are killed
FIND_SECONDS = 1.0         # one Join list search (the list is kept between searches: discovery.HostTable)
QUIET_HINT_AFTER = 8.0     # the Join list found nothing for this long: say what to check
NEWS_HOLD = 6.0            # seconds news ("Ben joined your world.") stays before "who is where" comes back
LOG_LINES = 300
PREFS = "multiplayer"      # launcher.json (Settings.extra): {"name", "story", "code"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
READY = "Ready: start your game"   # (coop_agent.READY: join's story is in the co-op save)
MP_SCREENS = ("multiplayer", "who")
RESTART_WAIT = 90.0        # seconds Linkle's restart waits for the game to close itself before it gives up

STORY_ON = "ON: your friends' co-op saves get your towers, quests and freed Divine Beasts (they say yes first)."
STORY_OFF = "OFF: friends share your time of day, not your story."
COPY_NOTE = ("Home network only: both PCs on the same Wi-Fi or router. Multiplayer plays on a copy of your save; "
             "your own save stays as it is.")
# Windows Firewall: the host's Python (app\runtime\python.exe) listens on port 7788; Windows asks once, and on a Wi-Fi
# set to Public "Allow" for private networks still leaves it blocked
FIREWALL_ASK = ("Windows may ask whether Python may use the network: press Allow. Your Wi-Fi must be set to Private "
                "(Windows Settings > Network > your Wi-Fi > Private).")
FIREWALL_LINE = ("No friend yet? Windows Security > Firewall & network protection > Allow an app: tick Python for "
                 "Private. Your Wi-Fi: Private.")
NO_LIST_LINE = ("Friends' Join lists can't find you: allow Python in Windows Firewall (Windows Security > Firewall "
                "& network protection > Allow an app).")
COPY_LINE = "The game keeps running on your multiplayer copy: what you do there isn't in your own save."
OPENS_LINE = "The game still opens, on your multiplayer copy: what you do there isn't in your own save."
NAME_DEFAULT = "Player"    # never the Windows account name: the world's name goes out to the whole home network

LINK_BODY_ID = "botw-mod-loader.ghost-link-body"   # (tools/mp/ghost_link_pack.py: the Link body; Plus doesn't build it)
# Built, but not yet seen in the game since the 2026-11-13 fix (docs/charsel.md "Status"): the page says so
NOT_TRIED: frozenset = frozenset()  # all 9 spawn and play-as works in the game (commits 92c0d2f, 8de5ed9, b931c33)
# What each looks like to a friend, in plain words (charsel.Recipe.looks has the developer's notes)
LOOKS = {
    "link": "Link: a stand-in figure for now (Breathalyzer doesn't build the Link body yet).",
    "linkle": ("Your own body becomes Linkle (The Linkle Mod, one restart): your armor shows as hers and the "
               "inventory shows her. Friends need the Linkle mod too; without it they see the stand-in figure."),
    "zelda": "Her own stand, walk and run; Link's moves for the rest. The dress is stiff.",
    "hylian": "A Hateno villager: his own walk, Link's moves for the rest.",
    "sheikah": "Her own stand, walk and run; Link's moves for the rest.",
    "gerudo": "Tall. Link's moves may sink her hips a little.",
    "goron": "Sprints as a rolling ball. Climbing and gliding may look odd.",
    "rito": "Glides with real wings.",
    "zora": "Swims with Zora swim moves.",
    "bokoblin": "Its own moves, attacks too. Where you climb, glide or swim it just stands or walks.",
    "moblin": "Its own moves. Where you climb, glide or swim it just stands or walks. Big: may pop in at the edges.",
}
# Why the characters couldn't be built (charsel build --add's words on the left), in plain words
BUILD_REASONS = (
    ("isn't the game's extracted romfs", "Your game files aren't set up: open Savage Wind and press Set up game "
                                         "files."),
    ("already running", "Close Savage Wind first, then build them again."),
    ("is running", "Close Savage Wind first, then build them again."),
    ("didn't read as expected", "A game file didn't read as expected: are your game files version 1.9.0?"),
    ("no character could be built", "No character could be built from your game files."),
    ("no space left", "Your disk is full: free some space, then build them again."),
)


@dataclass
class Status:
    kind: str          # "ok", "warn", "error", "busy" or "info" (the launcher's colours)
    lines: list[str]


class PrepError(Exception):
    """Multiplayer can't start: the message is for the player."""


# ---------------------------------------------------------------- the player's choices and folders

def load_prefs(extra: dict | None) -> dict:
    """{"name", "named", "story", "code", "firewall"} from launcher.json's "multiplayer" (Settings.extra). The name:
    the one the player typed (named), else Player: never the Windows account name, which the Join list would send to
    everyone on the network (the page asks for a name at the first Host or Join). Story together starts ON (the
    friend is asked every time, and only gets things added, after a backup); no room code at first. firewall: the
    player has seen the first Host's "Windows may ask about Python" step."""
    from mp import valid_name
    from mp.security import normalize_code
    p = (extra or {}).get(PREFS)
    p = p if isinstance(p, dict) else {}
    name = valid_name(p.get("name"))
    return {"name": name or NAME_DEFAULT, "named": name is not None, "story": p.get("story") is not False,
            "code": normalize_code(p.get("code")) if isinstance(p.get("code"), str) else None,
            "firewall": p.get("firewall") is True}


def new_code() -> str:
    """A room code: 3 random words from the word list."""
    from mp.security import CODE_WORDS, WORDS
    return " ".join(secrets.choice(WORDS) for _ in range(CODE_WORDS))


def mp_folder(library) -> Path:
    return Path(library) / MP_DIR


def own_save(install) -> Path:
    """Savage Wind's own save folder (save\\ next to savagewind.exe): only ever read here."""
    return Path(install) / "save"


def story_list(mplib: Path, romfs: Path) -> Path:
    """The story list (names only) the co-op tools need, built once from this PC's own game files."""
    from mp import story
    path = Path(mplib) / "mp" / story.LIST_FILE
    with contextlib.suppress(OSError, ValueError):
        if story.StoryList.load(path).base:
            return path
    if not (Path(romfs) / "Pack" / "Bootup.pack").is_file():
        raise PrepError("Your game files aren't set up: open Savage Wind and press Set up game files.")
    try:
        story.list_from_romfs(Path(romfs)).save(path)
    except Exception as e:  # (a game file that doesn't read as expected: said, never a crash)
        raise PrepError(f"Your game files couldn't be read for the story list ({type(e).__name__}).") from None
    return path


def _copy(make) -> Path:
    from mp import story
    try:
        return make()
    except story.GameRunning:
        raise PrepError("Close Savage Wind first: your save is in use.") from None
    except FileNotFoundError:
        raise PrepError("You have no save yet. Play once on your own and save: multiplayer starts from your newest "
                        "save.") from None
    except (OSError, ValueError) as e:
        raise PrepError(f"Your save couldn't be copied ({type(e).__name__}). Press Details.") from e


def prep_host(library, install, name: str) -> Path:
    """The host's world: a copy of his own newest save, made once (<library>\\multiplayer\\worlds\\<name>s_world),
    then reused. Also the story list. Returns the folder the game is started on."""
    from mp import worlds
    mplib, own = mp_folder(library), own_save(install)
    story_list(mplib, Path(install) / "extracted" / "romfs")
    return _copy(lambda: worlds.host_world(own, mplib, f"{name}'s world", protect=[own]))


def prep_join(library, install, host: dict) -> Path:
    """A friend's co-op copy for this host's world (one per host: its check words), made once from his own save.
    Also the story list. Returns the folder the game is started on."""
    from mp import worlds
    mplib, own = mp_folder(library), own_save(install)
    story_list(mplib, Path(install) / "extracted" / "romfs")
    return _copy(lambda: worlds.joined_copy(own, mplib, host["world"], check=host.get("check") or None,
                                            protect=[own]))


def characters(library, on: bool = True, link_body: bool = False, linkle_mod: bool | None = None) -> list[dict]:
    """Who am I's characters in wheel order: {"id", "name", "group", "state", "looks"}, as THIS PC shows them (each
    PC shows the others' picks from its own built characters). state: "Ready"; "Not tried yet" (built, not yet seen
    in the game: NOT_TRIED); "Build first" (the Characters mod hasn't built it here); "Stand-in" (Link: the Link body,
    which Breathalyzer doesn't build yet: link_body says it is in the mod list, ON). Linkle (2026-11-18: your own
    body, The Linkle Mod ON with one restart): "Ready" when The Linkle Mod is in the mod list (linkle_mod; None: ask
    the catalog whether it was on), "Needs a mod" without it. Read from the library copy of the Characters mod (on: it
    is ON; off, every built one says "Build first" too: the game doesn't get it)."""
    import charsel
    cat = charsel.load_catalog(Path(library) / "mods" / CHARS_ID) if on else {}
    built = cat.get("built") or {}
    linkle = bool(charsel.linkle_ready(cat)) if linkle_mod is None else linkle_mod
    out = []
    for group, ids in charsel.CATEGORIES:
        for cid in ids:
            r = charsel.RECIPES[cid]
            if r.kind == "link":
                state = "Ready" if link_body else "Stand-in"
            elif r.kind == "linkle":
                state = "Ready" if linkle else "Needs a mod"
            elif cid in built:
                state = "Not tried yet" if cid in NOT_TRIED else "Ready"
            else:
                state = "Build first"
            looks = LOOKS.get(cid) or re.sub(r"\s*\[[A-Z]\]", "", r.looks)
            if cid == "link" and link_body:
                looks = "Link's own body, without hair or clothes for now."
            out.append({"id": cid, "name": r.name.split(" (")[0], "group": group, "state": state, "looks": looks})
    return out


def build_reason(lines) -> str | None:
    """Why the Characters recipe failed, in plain words (its output: the launcher's Details), or None."""
    low = "\n".join(str(x) for x in lines or ()).lower()
    return next((say for key, say in BUILD_REASONS if key in low), None)


def mask(text: str, env: dict | None = None) -> str:
    """Text with the home folder written as %USERPROFILE% (Details: a bug report never carries the user's name)."""
    env = os.environ if env is None else env
    for home in {env.get("USERPROFILE") or "", os.path.expanduser("~")}:
        if home and len(home) > 3:
            text = re.sub(re.escape(home), "%USERPROFILE%", text, flags=re.I)
            text = re.sub(re.escape(home.replace("\\", "/")), "%USERPROFILE%", text, flags=re.I)
    return text


# Why a tool ended, in plain words (the first that matches; the tools' own words on the left)
REASONS = (
    ("too many wrong room codes", "Too many wrong room codes: wait 10 minutes, then try again."),
    ("wrong room code", "Wrong room code. Ask the host for the 3 words and try again."),
    ("needs a room code", "This world needs a room code: ask the host for the 3 words."),
    ("doesn't know the room code", "That host doesn't have the room code you typed: is it the right world?"),
    ("kicked by the host", "The host took you out of the world (for 10 minutes)."),
    ("banned by the host", "The host doesn't let you join this world."),
    ("version mismatch", "Your Breathalyzer and the host's are different versions: use the same one."),
    ("can't join this session", "The host's world doesn't take you now (full, or another version)."),
    ("bad name", "Your name isn't allowed: use 1 to 20 letters, digits or spaces."),
    ("ended the session", "The host ended the session."),
    ("bad answer from the host", "The host's PC didn't answer as Breathalyzer does: is it the right world?"),
    ("can't reach the host", "Can't reach the host's PC. Same Wi-Fi? The host allows Python in Windows Firewall "
                             "(Private networks)."),
    ("10048", "Another program uses port 7788: is Breathalyzer already hosting? Close it and try again."),
    ("address already in use", "Another program uses port 7788: is Breathalyzer already hosting? Close it and try "
                               "again."),
    ("only one usage of each socket address", "Another program uses port 7788: is Breathalyzer already hosting? "
                                              "Close it and try again."),
    ("no story list", "The story list is missing: press Host or Join again."),
)


def plain_reason(text: str) -> str | None:
    """A tool's reason in plain words, or None when it isn't one we know (then Details has it)."""
    low = (text or "").lower()
    return next((say for key, say in REASONS if key in low), None)


# ---------------------------------------------------------------- the session, without widgets

def _kill_tree(proc) -> None:
    """Stop a tool that didn't end in time, and anything it started."""
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32" and isinstance(getattr(proc, "pid", None), int):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15,
                           creationflags=NO_WINDOW)
    with contextlib.suppress(OSError):
        if proc.poll() is None:
            proc.kill()


def _decode(raw) -> str:
    if isinstance(raw, str):
        return raw.rstrip("\r\n").rsplit("\r", 1)[-1]
    return raw.rstrip(b"\r\n").rsplit(b"\r", 1)[-1].decode("utf-8", "replace")


class MpSession:
    """Host or Join, step by step in the background; poll() (the window's tick) takes what happened and keeps
    `status` (what the page says), `mode`, `players` and the Join list's `hosts` up to date.

    mode: idle; prep (the co-op copy and the story list); agent (coop_agent started: host waits for "Hosting",
    join for READY); open (host, the first time: waits for go()); game (the launcher's Play runs, the client is
    started); live (the game is open); stopping.
    start_game(env) is the launcher's Play with these game variables; game_update(starting, open) tells the session
    what Play and the game are doing. spawn is subprocess.Popen (tests: a stand-in); search is discovery.search."""

    def __init__(self, library: str, install: str, *, python: str | None = None, spawn=None,
                 game_port: int = CONTROL_PORT, start_game=None, cancel_game=None, clock=time.monotonic, search=None,
                 host_prep=prep_host, join_prep=prep_join, kill=_kill_tree, send_iam=None, send_exit=None):
        self.library, self.install = str(library), str(install)
        self.send_exit = send_exit or self._send_exit   # (Linkle's restart: the game closes itself)
        self.python = python or sys.executable
        self.spawn = spawn or subprocess.Popen
        self.game_port = game_port
        self.start_game = start_game
        self.cancel_game = cancel_game      # Stop before the game opened: Play's Cancel (True: it won't open)
        self.clock = clock
        self.search = search
        self.host_prep, self.join_prep, self.kill = host_prep, join_prep, kill
        self.send_iam = send_iam or self._send_iam
        self.events: queue.Queue = queue.Queue()
        self.procs: dict[str, object] = {}
        self.mode = "idle"
        self.role: str | None = None        # "host" or "join"
        self.name = ""
        self.code: str | None = None
        self.story = True                   # host: Story together
        self.share = True                   # join: said yes to the host's story
        self.target: dict | None = None     # join: the Join list's row
        self.world: Path | None = None      # the co-op save folder the game runs on
        self.check = ""                     # host: this world's check words
        self.players: list[str] = []        # friends in the world now
        self.ever_joined = False            # host: a friend reached this world (then no firewall line)
        self.no_list = False                # host: the Join list's answers couldn't start (firewall)
        self.pause = False                  # host: wait at "Your world is open" for Start the game (the 1st time)
        self.game_gone = False              # the game closed or didn't start (Stop says nothing about it)
        self.look_note = ""                 # Who am I: whether the game took the pick (`iam`'s answer)
        self.exit_error: str | None = None  # Linkle's restart: why `exit` couldn't reach the game (send_exit)
        self.where = ""                     # the client's last "who is where" line
        self.news_at = -1e9                 # when something last happened (news): kept NEWS_HOLD seconds
        self.ending: Status | None = None   # why the session ended (a tool's last words)
        self.reason: Status | None = None   # what to say once it has stopped
        self.status = Status("info", ["Play with friends on your home network.",
                                      "Press Host to open your world, or Join to go into a friend's."])
        self.log: deque[str] = deque(maxlen=LOG_LINES)
        self.hosts: list[dict] = []
        self.finding_since: float | None = None
        self._finder: threading.Event | None = None
        self._token = 0
        self.version = 0                    # goes up when something the page shows changed

    @property
    def mp_library(self) -> Path:
        return mp_folder(self.library)

    @property
    def running(self) -> bool:
        return self.mode != "idle"

    def why_not(self, enabled: set, game_open: bool, busy: str | None = None) -> str | None:
        """Why Host or Join can't start now (plain words), or None."""
        if self.running:
            return "Multiplayer is on now: press Stop first."
        if GHOST_ID not in enabled:
            return "Turn on Ghost multiplayer first."
        if game_open:
            return "Close Savage Wind first: Host and Join start it for you."
        if busy:
            return f"Please wait: {busy}."
        return None

    def say(self, kind: str, *lines: str) -> None:
        self.status = Status(kind, [x for x in lines if x])
        self.version += 1

    def news(self, kind: str, *lines: str) -> None:
        """Something happened (a friend joined, the line dropped...): said, and kept NEWS_HOLD seconds before the
        next "who is where" line puts the usual one back."""
        self.say(kind, *lines)
        self.news_at = self.clock()

    # --- Host and Join

    def _begin(self, role: str, name: str, code: str | None) -> int:
        self.find(False)
        self._token += 1
        self.mode, self.role, self.name, self.code = "prep", role, name, code
        self.world, self.check, self.players, self.where = None, "", [], ""
        self.ever_joined = self.no_list = self.pause = self.game_gone = False
        self.ending = self.reason = None
        self.news_at = -1e9
        self.log.clear()
        return self._token

    def host(self, name: str, story: bool = True, code: str | None = None, pause: bool = False) -> None:
        """Host: the world copy, then coop_agent host, then (once it hosts) the game and the client. pause (the first
        Host on this PC): once the world is open, wait for go() (Start the game) so Windows' question about Python
        isn't hidden behind the game."""
        token = self._begin("host", name, code)
        self.story, self.pause = story, pause
        self.say("busy", "Getting your world ready...", "Your world is a copy of your save: your own save stays as "
                                                         "it is.")
        self._prep(token, lambda: self.host_prep(self.library, self.install, name))

    def join(self, host: dict, name: str, share: bool = True, code: str | None = None) -> None:
        """Join a host from the Join list: the co-op copy, then coop_agent join, then (once the host's story is in
        the copy) the game and the client."""
        token = self._begin("join", name, code)
        self.target, self.share = dict(host), share
        self.say("busy", f"Joining {host['world']}...", "Getting your co-op save ready: your own save stays as it is.")
        self._prep(token, lambda: self.join_prep(self.library, self.install, host))

    def _prep(self, token: int, work) -> None:
        def run():
            try:
                res = work()
            except PrepError as e:
                res = e
            except Exception as e:  # (said in plain words; Details has the rest)
                self.log.append(f"prep: {type(e).__name__}: {e}")
                res = PrepError(f"Multiplayer couldn't get ready ({type(e).__name__}). Press Details.")
            self.events.put(("prep", token, res))

        threading.Thread(target=run, daemon=True, name="mp prep").start()

    def _prepped(self, token: int, res) -> None:
        if token != self._token or self.mode != "prep":
            return
        if isinstance(res, Exception):
            self.mode = "idle"
            if res.__cause__ is not None:
                self.log.append(f"prep: {type(res.__cause__).__name__}: {res.__cause__}")
            self.say("error", "Multiplayer didn't start.", str(res))
            return
        self.world = Path(res)
        port = ["--game-port", str(self.game_port)]
        code = ["--code", self.code] if self.code else []
        if self.role == "host":
            args = ["host", "--name", self.name, *port, "--stdin", *code,
                    *(["--story-from", str(self.world)] if self.story else [])]
            self.say("busy", "Opening your world...", FIREWALL_ASK)
        else:
            h = self.target
            args = ["join", "--name", self.name, "--address", h["address"], "--port", str(h["port"]),
                    "--save-dir", str(self.world), "--yes" if self.share else "--no", *port, "--stdin", *code,
                    *(["--check", h["check"]] if h.get("check") else [])]
            self.say("busy", f"Joining {h['world']}...", f"Getting {h['name']}'s story first." if self.share
                     else "")
        self.mode = "agent"
        self._start("agent", AGENT_PY, args, self.mp_library)

    def _start(self, role: str, script: str, args: list[str], library) -> bool:
        argv = [self.python, "-u", script, *args, "--library", str(library)]
        self.log.append(f"{role}: started")
        try:
            proc = self.spawn(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              creationflags=NO_WINDOW, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        except OSError as e:
            self.log.append(f"{role}: can't start: {e}")
            self.stop(Status("error", ["Multiplayer didn't start.", "Breathalyzer's Python couldn't be started: "
                                       "unpack the zip again."]))
            return False
        self.procs[role] = proc
        threading.Thread(target=self._read, args=(role, proc), daemon=True, name=f"mp {role}").start()
        return True

    def _read(self, role: str, proc) -> None:
        try:
            for raw in proc.stdout:
                self.events.put(("line", role, proc, _decode(raw)))
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError, ValueError, AttributeError):
                proc.stdout.close()
        try:
            code = proc.wait()
        except Exception:  # (a stand-in that can't wait: counts as ended)
            code = None
        self.events.put(("exit", role, proc, code))

    def go(self) -> None:
        """Start the game (the first Host waits for it at "Your world is open")."""
        if self.mode == "open":
            self._go_game()

    def _opened(self) -> None:
        """The host's world is open: the game at once, or (pause) first the page's Start the game."""
        if not self.pause:
            return self._go_game()
        self.mode = "open"
        self.say("ok", "Your world is open.", FIREWALL_ASK, NO_LIST_LINE if self.no_list else "",
                 "Then press Start the game.")

    def _go_game(self) -> None:
        """The session is up (host: hosting; join: the story is in the copy): the game through Play, and the
        client, which waits for the game by itself."""
        self.mode = "game"
        self.say("busy", "Starting the game...", "Load your world with Continue.",
                 NO_LIST_LINE if self.role == "host" and self.no_list else "")
        try:
            if self.start_game is None:
                raise RuntimeError("no Play here")
            self.start_game({"SAVAGEWIND_SAVE_DIR": str(self.world)})
        except Exception as e:  # (said, and everything stops: never a crash)
            self.log.append(f"play: {type(e).__name__}: {e}")
            self.stop(Status("error", ["The game didn't start.", "Press Details."]))
            return
        relay = f"127.0.0.1:{RELAY_PORT}" if self.role == "host" else \
            f"{self.target['address']}:{self.target['port']}"
        args = ["--relay", relay, "--name", self.name, "--no-ui", "--ghost-looks", "--game-port", str(self.game_port),
                "--stdin", *(["--code", self.code] if self.code else [])]
        self._start("client", CLIENT_PY, args, self.library)

    def live_status(self) -> None:
        friends = ", ".join(self.players)
        if self.role == "host":
            # (until a friend has reached this world: what to check in Windows, on the page the whole time)
            help_line = "" if self.ever_joined else NO_LIST_LINE if self.no_list else FIREWALL_LINE
            self.say("ok", f"You're hosting {self.name}'s world.",
                     f"Friends here: {friends}." if friends else
                     "Friends on your home network press Join and pick it." +
                     (f" Room code: {self.code}." if self.code else ""),
                     help_line,
                     self.where if friends and self.where else (f"Check words: {self.check}." if self.check else ""))
        else:
            h = self.target or {}
            self.say("ok", f"You're in {h.get('world', 'the world')}.", self.where or "Load your world with Continue.")

    def game_update(self, starting: bool, game_open: bool, why: list[str] | None = None) -> bool:
        """What Play and the game are doing (the window's tick): the session goes live when the game is open, and
        stops when the game didn't start or closed. why: the launcher's own words when Play failed. True when
        something changed."""
        if self.mode == "game" and not starting:
            if game_open:
                self.mode = "live"
                self.live_status()
            else:
                self.game_gone = True
                why = [x for x in (why or []) if x][:2]
                self.stop(Status("error", ["The game didn't start.", *why, "Multiplayer stopped."] if why else
                                 ["The game didn't start.", "Home says why. Multiplayer stopped."]))
            return True
        if self.mode == "live" and not starting and not game_open:
            self.game_gone = True
            self.stop(Status("info", ["The game closed.", "Multiplayer stopped."]))
            return True
        return False

    # --- what the tools say

    def poll(self) -> bool:
        """Take what happened in the background. True when the page should be drawn again."""
        changed = False
        while True:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                break
            changed = True
            kind = ev[0]
            if kind == "hosts":
                if self._finder is not None:
                    self.hosts = ev[1]
            elif kind == "prep":
                self._prepped(ev[1], ev[2])
            elif kind == "line":
                self._line(*ev[1:])
            elif kind == "exit":
                self._exit(*ev[1:])
            elif kind == "stopped":
                if ev[1] == self._token and self.mode == "stopping":
                    self.mode = "idle"
                    self.status = self.reason or Status("info", ["Multiplayer stopped."])
            elif kind == "game_exit":
                if ev[1]:
                    self.log.append(f"exit: {ev[1]}")
                    self.exit_error = ev[1]
            elif kind == "iam":
                cid, err = ev[1], ev[2]
                if err:
                    self.log.append(f"iam: {err}")
                self.look_note = ("Your game didn't take it now: it is used from your next Host or Join." if err
                                  else "In your game now.")
                if self.status.lines and self.status.lines[0].startswith("You picked"):   # (on_pick's words)
                    self.status = Status("warn" if err else "ok", [self.status.lines[0], self.look_note])
        if changed:
            self.version += 1
        return changed

    def _line(self, role: str, proc, text: str) -> None:
        mine = self.procs.get(role) is proc
        self.log.append(f"{role}: {text}")
        if not mine or not text.strip():
            return
        (self._agent_line if role == "agent" else self._client_line)(text.strip())

    def _agent_line(self, text: str) -> None:
        if self.role == "host":
            m = re.match(r"Hosting .* on port \d+\. Check words: ([a-z ]+?)(?:\. Room code: .*)?$", text)
            if m:
                self.check = m.group(1)
                if self.mode == "agent":
                    self._opened()
                return
            m = re.match(r"(.{1,20}) joined( \(older Breathalyzer.*)?$", text)
            if m:
                self.ever_joined = True
                if m.group(1) not in self.players:
                    self.players.append(m.group(1))
                self.news("ok", f"{m.group(1)} joined your world.",
                         "Their Breathalyzer is older: they see you, nothing else." if m.group(2) else
                         f"Friends here: {', '.join(self.players)}.")
                return
            m = re.match(r"(.{1,20}) left$", text)
            if m:
                with contextlib.suppress(ValueError):
                    self.players.remove(m.group(1))
                self.news("info", f"{m.group(1)} left your world.",
                         f"Friends here: {', '.join(self.players)}." if self.players else "")
                return
            if text.startswith("No friend has reached you yet"):
                self.news("warn", "No friend has reached you yet.", FIREWALL_LINE.split("? ", 1)[-1])
            elif text.startswith("Join lists can't find this host"):
                self.no_list = True   # (kept in the status lines until a friend has come in: _go_game, live_status)
                self.news("warn", "Friends' Join list can't find you.", "Allow Python in Windows Firewall: Windows "
                          "Security > Firewall & network protection > Allow an app.")
            elif text.startswith("Lost your own place in the session"):
                self.ending = Status("error", ["Your world stopped.", "Press Host to open it again."])
            elif text.startswith("Stopped:"):
                self.ending = Status("error", ["Your world couldn't open.",
                                               plain_reason(text) or "Press Details to see why."])
            return
        host = (self.target or {}).get("name") or "the host"
        if text.startswith(READY):
            if self.mode == "agent":
                self._go_game()
        elif "'s story added:" in text or text.startswith("Your co-op save already has"):
            self.news("ok", f"{host}'s story is in your co-op save." if "added" in text
                     else f"Your co-op save has {host}'s story already.",
                     "Nothing was taken away. A backup was made first." if "added" in text else "",
                     "Starting the game..." if self.mode in ("agent", "game") else "")
        elif "isn't sharing story" in text:
            self.news("info", f"{host} doesn't share story.", "You share the time of day.")
        elif "check words don't match" in text:
            self.news("warn", "This host's check words don't match the Join list.", "Story is off for this "
                             "session.")
        elif text.startswith("Your game uses the co-op save now"):
            self.news("info", f"{host}'s new story is added at your next start.")
        elif text.startswith("The host's Breathalyzer is older"):
            self.news("warn", f"{host}'s Breathalyzer is older.", "You see each other, nothing else is shared.")
        elif text.startswith("Back in "):
            self.news("ok", f"Back in {host}'s world.")
        elif text.startswith("Lost the line to "):
            self.news("warn", f"Lost {host}'s world.", "Trying again...")
        elif text.startswith(("Gave up:", "Couldn't join:", "Stopped:", "The host closed your connection:")) \
                or text.endswith("ended the session"):
            why = plain_reason(text)
            ended = "ended the session" in text
            self.ending = Status("info" if ended else "error",
                                 [f"{host} ended the session." if ended else "You're out of the world.",
                                  "" if ended else why or "Press Details to see why."])
        elif text.startswith("Story not added"):
            self.news("warn", f"{host}'s story wasn't added.", "Press Details to see why.")

    def _client_line(self, text: str) -> None:
        m = re.match(r"Ghost: showing (.{1,20}) in your game\.$", text)
        if m:
            self.news("ok", f"You see {m.group(1)} in your game.", self.where)
            return
        if text.startswith("Ghost: the game has no `ghost` command"):
            # (Ghost was ON on Home, so: it was turned off on the game's Mods page (Play used that), its DLL didn't
            # start, or the mods weren't made ready. Other frame mods, like Infinite Stamina, no longer stop it.)
            self.ending = Status("error", ["Ghost multiplayer didn't run in your game.",
                                           "Turn it on in the game's Mods page too, then press Host or Join again "
                                           "(Details says more)."])
            self.stop(self.ending)
        elif text.startswith(("The relay said no:", "Not trying again:")):
            why = plain_reason(text)
            if why or text.startswith("Not trying again:"):
                self.ending = Status("error", ["You're out of the world.", why or "Press Details to see why."])
                if text.startswith("Not trying again:"):
                    self.stop(self.ending)
        elif text.startswith("Can't reach the relay") and self.role == "join":
            self.news("warn", f"Can't reach {(self.target or {}).get('world', 'the world')} yet.", "Trying again...")
        elif text.startswith("Nobody else in room") or re.match(r"[^:|]{1,26}: (\d+ m |no position yet)", text):
            self.where = "No friend in the game yet." if text.startswith("Nobody") else \
                text if len(text) <= 160 else text[:157] + "..."
            if self.mode == "live" and self.clock() - self.news_at >= NEWS_HOLD:   # (news stays a moment first)
                self.live_status()

    def _exit(self, role: str, proc, code) -> None:
        self.log.append(f"{role}: ended (code {code})")
        if self.procs.get(role) is not proc:
            return
        del self.procs[role]
        if self.mode in ("idle", "stopping"):
            return
        why = self._why_from_log(role) or "Press Details to see why."   # (a tool that ended without its last words)
        if role == "agent":
            head = "Your world couldn't open." if self.role == "host" and self.mode == "agent" else \
                "Multiplayer stopped."
            self.stop(self.ending or Status("error", [head, why]))
        else:
            self.stop(self.ending or Status("error", ["Your friends' figures stopped.", why]))

    def _why_from_log(self, role: str) -> str | None:
        """The newest of this tool's lines that says why in plain words (plain_reason), or None."""
        for line in reversed(self.log):
            if line.startswith(f"{role}: "):
                why = plain_reason(line[len(role) + 2:])
                if why:
                    return why
        return None

    # --- Stop

    def _game_after_stop(self) -> str:
        """What the game does once multiplayer stops, said with the reason: it keeps running (or still opens) on the
        co-op copy; "" when it closed, never started, or Play was cancelled before it opened (cancel_game)."""
        if self.game_gone:
            return ""
        if self.mode == "live":
            return COPY_LINE
        if self.mode == "game":
            cancelled = False
            if self.cancel_game is not None:
                try:
                    cancelled = bool(self.cancel_game())
                except Exception as e:  # (said as "still opens": never a crash)
                    self.log.append(f"play: can't cancel: {type(e).__name__}: {e}")
            return "" if cancelled else OPENS_LINE
        return ""

    def stop(self, reason: Status | None = None) -> None:
        """End the session: the tools are told through their stdin (the client takes your friends' figures out of
        your game; a host tells its friends), and killed if they haven't ended after STOP_WAIT seconds. A game
        that is open keeps running (on the co-op copy: said); Play still getting the mods ready is cancelled."""
        self.find(False)
        if self.mode == "idle":
            return
        if self.mode == "stopping":
            self.reason = reason or self.reason
            return
        game = self._game_after_stop()
        procs = list(self.procs.items())
        self.procs.clear()
        self._token += 1
        token = self._token
        reason = reason or Status("info", ["Multiplayer stopped."])
        lines = [x for x in reason.lines if x]
        if game and game not in lines:
            lines = [*lines[:3], game]
        self.reason = Status(reason.kind, lines)
        if not procs:
            self.mode = "idle"
            self.status = self.reason
            self.version += 1
            return
        self.mode = "stopping"
        self.say("busy", "Stopping...")
        kill, clock = self.kill, self.clock

        def work():
            for role, p in sorted(procs, key=lambda rp: rp[0] != "client"):   # (the client first: ghosts go)
                with contextlib.suppress(OSError, ValueError, AttributeError):
                    p.stdin.write(b"end\n" if role == "agent" else b"stop\n")
                    p.stdin.flush()
                with contextlib.suppress(OSError, ValueError, AttributeError):
                    p.stdin.close()
            end = clock() + STOP_WAIT
            for _role, p in procs:
                with contextlib.suppress(Exception):
                    p.wait(timeout=max(0.1, end - clock()))
                if p.poll() is None:
                    kill(p)
            self.events.put(("stopped", token))

        threading.Thread(target=work, daemon=True, name="mp stop").start()

    def close(self) -> None:
        """The window closes: tell the tools to end (they also end when this process's pipes close)."""
        self.find(False)
        for role, p in list(self.procs.items()):
            with contextlib.suppress(OSError, ValueError, AttributeError):
                p.stdin.write(b"end\n" if role == "agent" else b"stop\n")
                p.stdin.close()
        self.procs.clear()
        self.mode = "idle"

    def details(self) -> str:
        return mask("\n".join(self.log)) or "Nothing yet."

    # --- the Join list

    def find(self, on: bool) -> None:
        """Look for worlds on the home network (every second, in the background) while the Join list is open."""
        if on and self._finder is None:
            stop = threading.Event()
            self._finder, self.hosts, self.finding_since = stop, [], self.clock()
            search = self.search
            if search is None:
                from mp import discovery
                table = discovery.HostTable()
                search = lambda seconds: discovery.search(seconds, table=table)  # noqa: E731

            def loop():
                last = None
                while not stop.is_set():
                    began = time.monotonic()
                    try:
                        found = list(search(FIND_SECONDS))
                    except OSError as e:
                        self.log.append(f"join list: {e}")
                        found = None
                    if found is not None and found != last and not stop.is_set():
                        self.events.put(("hosts", found))   # (only a change: the page is drawn again for it)
                        last = found
                    stop.wait(max(0.0, FIND_SECONDS - (time.monotonic() - began)))   # (at most once a second)

            threading.Thread(target=loop, daemon=True, name="mp join list").start()
        elif not on and self._finder is not None:
            self._finder.set()
            self._finder, self.finding_since = None, None

    @property
    def finding(self) -> bool:
        return self._finder is not None

    # --- Who am I

    def pick_look(self, cid: str) -> str:
        """Your pick: kept for the multiplayer client (charsel's choice, which it reads when it starts), and while
        your game is open also told to it (`iam`, the ghost mod's live view). look_note says which, once the game
        answered ("In your game now." only then). Returns the character's short name."""
        import charsel
        if charsel.find_id(cid) != cid:
            raise ValueError(f"no character {cid!r}")
        charsel.save_choice(cid)
        if self.mode in ("game", "live"):
            self.look_note = "Telling your game..."
            threading.Thread(target=lambda: self.events.put(("iam", cid, self.send_iam(cid))), daemon=True,
                             name="mp iam").start()
        else:
            self.look_note = "It is used from your next Host or Join."
        self.version += 1
        return charsel.RECIPES[cid].name.split(" (")[0]

    def _send_iam(self, cid: str) -> str:
        """`iam <id>` to the game's control port. Returns "" or why it didn't go (the client also picks up the
        choice at its next start)."""
        try:
            from control import Control
            c = Control(self.game_port, timeout=3.0)
            try:
                c.cmd("iam", cid)
            finally:
                c.close()
            return ""
        except Exception as e:  # (the game busy or loading: the choice is kept all the same)
            return f"{type(e).__name__}: {e}"

    def _send_exit(self) -> str:
        """`exit` to the game's control port: the game closes itself (Linkle's restart). "" or why it didn't go (the
        port is only open while a developer mod is on: Ghost multiplayer is one). Only a failed CONNECT is "didn't
        go" (review C4): once the command is out, the game closing drops the socket, or a load screen keeps the
        answer past the timeout, and the restart waits for it to close (relaunch_tick gives up after RESTART_WAIT)."""
        try:
            from control import Control
            c = Control(self.game_port, timeout=3.0)
        except Exception as e:
            return f"{type(e).__name__}: {e}"
        try:
            c.cmd("exit")
        except Exception as e:  # (sent: an answer that doesn't come is the game closing)
            self.log.append(f"exit sent, no answer: {type(e).__name__}: {e}")
        finally:
            with contextlib.suppress(Exception):
                c.close()
        return ""


# ---------------------------------------------------------------- the page in the launcher's window

class MpPage:
    """Home > Multiplayer and its Who am I page, in the launcher's window (LauncherView: its fonts, colours,
    buttons and questions). Everything it does goes through an MpSession and the launcher's LaunchController."""

    def __init__(self, view):
        self.view = view
        self.session: MpSession | None = None
        self.buttons: list = []       # the Multiplayer page's buttons (the window sizes their padding)
        self.who_buttons: list = []
        self.join_open = False
        self.watch_ctl: str | None = None   # a launcher step this page started: "ghost" (turning it on) or "chars"
        self.ctl_failed = False       # that step failed: Details shows the launcher's (what the step printed)
        self.who_error: Status | None = None   # Who am I: why the characters couldn't be built
        self.quiet = False            # the Join list has found nothing for QUIET_HINT_AFTER seconds
        self.shape = None
        self.who_shape = None
        self.focus_text, self.focus_index = "", -1
        self.relaunch: dict | None = None   # Linkle's restart: the game was told to close; then started again
        self.body_note = ""                  # Who am I: what picking Linkle (or leaving her) did to your own body

    def gui(self):
        import modman_gui
        return modman_gui

    # --- what the launcher has

    def bundled(self, mod_id: str) -> dict | None:
        return next((m for m in self.view.bundled if m["id"] == mod_id), None)

    def row(self, mod_id: str):
        ctl = self.view.ctl
        return next((r for r in ctl.listing.rows if r.id == mod_id), None) if ctl is not None else None

    def available(self) -> bool:
        """Home shows Multiplayer when Ghost multiplayer came with Breathalyzer, or is in the mod list."""
        return self.bundled(GHOST_ID) is not None or self.row(GHOST_ID) is not None

    def enabled(self) -> set:
        ctl = self.view.ctl
        return {r.id for r in ctl.listing.rows if r.on} if ctl is not None else set()

    def prefs(self) -> dict:
        return load_prefs(self.view.settings.extra)

    def save_prefs(self, **kw) -> None:
        p = self.prefs()
        p.update(kw)
        named = p["named"] or "name" in kw   # (the default name is never kept: the first Host or Join asks)
        self.view.settings.extra[PREFS] = {**({"name": p["name"]} if named else {}), "story": bool(p["story"]),
                                           **({"code": p["code"]} if p.get("code") else {}),
                                           **({"firewall": True} if p.get("firewall") else {})}
        self.view.save_settings()

    def ensure(self) -> MpSession | None:
        """The session for the mod list and Savage Wind folder in use (a running one is kept)."""
        ctl = self.view.ctl
        if ctl is None:
            return self.session
        lib, install = ctl.backend.root, ctl.backend.install
        s = self.session
        if s is None or (not s.running and (s.library, s.install) != (str(lib), str(install))):
            g = self.gui()
            self.session = MpSession(lib, install, python=g.console_python(),
                                     game_port=ctl.port_arg or g.CONTROL_PORT,
                                     start_game=lambda env: self.view.ctl.launch(env=env),
                                     cancel_game=self.cancel_play)
        return self.session

    def cancel_play(self) -> bool:
        """Stop while Play still gets the mods ready: Play's Cancel. True when the game won't open now."""
        ctl = self.view.ctl
        if ctl is None or ctl.game_open or "play" in ctl.jobs:
            return False
        if ctl.can("cancel"):
            ctl.cancel()
            return True
        return not ctl.jobs.get("main")   # (a bonus being built can't be cancelled: then the game still opens)

    def groups(self) -> list[list]:
        return [self.buttons, self.who_buttons]

    # --- building

    def build(self) -> None:
        """Both pages' frames (built again for another theme)."""
        g, v, t = self.gui(), self.view, self.view.t
        tk = g.tk
        self.buttons, self.who_buttons, self.shape, self.who_shape = [], [], None, None
        for name, title in (("multiplayer", "Multiplayer"), ("who", "Who am I?")):
            f = v.frames[name]
            top = tk.Frame(f, bg=t["bg"])
            top.grid(row=0, column=0, sticky="ew", padx=g.PAD, pady=(8, 6))
            group = self.buttons if name == "multiplayer" else self.who_buttons
            v.make_button(top, "Back", self.back, group, bold=True).ring.pack(side="left")
            v.label(top, v.big, text=title).pack(side="left", padx=(16, 0))
            box = tk.Frame(f, bg=t["bg"])
            box.grid(row=1, column=0, sticky="ew", padx=g.PAD, pady=(4, 8))
            box.columnconfigure(1, weight=1)
            bar = tk.Frame(box, width=10, bg=t["info"])
            bar.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 14))
            head = v.wrap_label(box, v.head)
            head.grid(row=0, column=1, sticky="ew")
            more = v.wrap_label(box, anchor="nw")
            more.grid(row=1, column=1, sticky="new")
            body = tk.Frame(f, bg=t["bg"])
            body.grid(row=2, column=0, sticky="ew", padx=g.PAD)
            body.columnconfigure(1, weight=1)
            flow = g.Flow(f, gap=4, bg=t["bg"])
            v.flows.append(flow)
            flow.frame.grid(row=3, column=0, sticky="ew", padx=g.PAD, pady=(10, 4))
            note = v.wrap_label(f, fg=t["disabled"])
            note.grid(row=4, column=0, sticky="ew", padx=g.PAD, pady=(6, g.PAD))
            setattr(self, f"{name}_parts", {"bar": bar, "head": head, "more": more, "body": body, "flow": flow,
                                           "note": note, "fixed": len(group)})

    # --- screens

    def show(self) -> None:
        """Home > Multiplayer."""
        if self.ensure() is None:
            return
        self.shape = None
        self.view.show("multiplayer")
        self.focus_first(self.buttons)

    def show_who(self) -> None:
        self.who_shape = None
        self.view.show("who")
        self.focus_first(self.who_buttons)

    def focus_first(self, group: list) -> None:
        pick = next((b for b in group[1:] if str(b.cget("state")) != "disabled"), None) or (group[0] if group
                                                                                              else None)
        if pick is not None:
            with contextlib.suppress(Exception):
                pick.focus_set()

    def back(self) -> None:
        """Back (Esc): Who am I goes back to Multiplayer; the Join list closes; Multiplayer goes Home."""
        v = self.view
        if v.screen == "who":
            self.show()
        elif self.join_open:
            self.join_open = False
            if self.session is not None:
                self.session.find(False)
            self.render()
        else:
            v.show("home")
            with contextlib.suppress(Exception):
                v.play.focus_set()

    def tick(self) -> bool:
        """The window's tick: the session's news and the game's state. True when this page should be drawn
        again."""
        s, ctl = self.session, self.view.ctl
        if s is None:
            return False
        changed = s.poll()
        if ctl is not None:
            play = ctl.jobs.get("play")   # (with cheats, Play's run lasts as long as the game: open once it started)
            started = play is not None and play.game_started
            changed = s.game_update(bool(ctl.jobs.get("main")) or (play is not None and not started),
                                    ctl.game_open or started,
                                    why=ctl.status.lines if ctl.status.kind == "error" else None) or changed
            if self.watch_ctl and not ctl.jobs.get("main"):
                self.step_done(self.watch_ctl)
                changed = True
            changed = self.relaunch_tick() or changed
        quiet = self.quiet_now()   # (nothing else changes while the Join list finds nothing: the hint needs this)
        if quiet != self.quiet:
            self.quiet, changed = quiet, True
        return changed

    def quiet_now(self) -> bool:
        s = self.session
        return bool(self.join_open and s is not None and not s.hosts and s.finding_since is not None
                    and s.clock() - s.finding_since >= QUIET_HINT_AFTER)

    def step_done(self, what: str) -> None:
        """The launcher step this page started has ended: its words on the page (a failure in plain words, with
        Details: the launcher's, what the step printed)."""
        s, ctl = self.session, self.view.ctl
        self.watch_ctl = None
        st = ctl.status
        self.ctl_failed = st.kind == "error"
        if not self.ctl_failed:
            self.who_error = None
            s.say(st.kind if st.kind != "busy" else "info", *st.lines[:3])
            return
        if any("Savage Wind is open" in x for x in st.lines):   # (the launcher's own words say what to do)
            lines = list(st.lines[:3])
        elif what == "chars":
            why = build_reason(ctl.details_text())
            lines = ["The characters couldn't be built.", *([why] if why else []), "Press Details to see why."]
        else:
            lines = ["Ghost multiplayer couldn't be turned on.", "Press Details to see why."]
        if what == "chars":
            self.who_error = Status("error", lines)
        s.say("error", *lines)

    def close(self) -> None:
        if self.session is not None:
            self.session.close()

    # --- drawing

    def status_now(self) -> Status:
        """The page's status: the launcher's while it works for this page (with its progress), else the session's."""
        s, ctl = self.session, self.view.ctl
        job = ctl.jobs.get("main") if ctl is not None else None
        if job is not None and (self.watch_ctl or not s.running):
            return Status("busy", [*(ctl.status.lines[:2] or ["Working..."]), *ctl.progress()[:1]])
        if job is not None and s.mode == "game":   # (Play gets the mods ready first: its progress, not a hang)
            return Status("busy", [*(ctl.status.lines[:2] or ["Getting ready..."]), *ctl.progress()[:1],
                                   "Then load your world with Continue."])
        return s.status

    def paint_status(self, parts: dict, st: Status) -> None:
        t = self.view.t
        parts["bar"].configure(bg=t.get(st.kind, t["info"]))
        parts["head"].configure(text=st.lines[0] if st.lines else "", fg=t["error"] if st.kind == "error" else t["fg"])
        parts["more"].configure(text="\n".join(st.lines[1:4]))

    def render(self) -> None:
        v = self.view
        if v.closed or v.ctl is None or self.session is None:
            return
        if v.screen == "who":
            return self.render_who()
        if v.screen != "multiplayer":
            return
        parts = self.multiplayer_parts
        self.paint_status(parts, self.status_now())
        s, ctl, p = self.session, v.ctl, self.prefs()
        busy = bool(ctl.jobs.get("main"))
        ghost = self.row(GHOST_ID)
        hosts = tuple((h["address"], h["port"], h["world"], h["name"], h["players"], h["max"], bool(h["code"]))
                      for h in s.hosts) if self.join_open else ()
        quiet = self.quiet_now()
        shape = (s.mode, self.join_open, bool(ghost and ghost.on), ghost is not None, busy, ctl.game_open,
                 p["name"], p["story"], p["code"], self.look_name(), hosts, quiet, bool(s.log), self.ctl_failed,
                 v.size)
        if shape != self.shape:
            self.shape = shape
            self.fill(parts, s, p, ghost, busy, quiet)

    def look_name(self) -> str:
        import charsel
        cid = charsel.load_choice()["look"]
        return charsel.RECIPES[cid].name.split(" (")[0] if cid in charsel.RECIPES else "Link"

    def clear(self, parts: dict, group: list) -> None:
        focus = self.view.root.focus_get()
        self.focus_text, self.focus_index = "", -1
        with contextlib.suppress(Exception):
            if focus in group:
                self.focus_text, self.focus_index = focus.cget("text"), group.index(focus)
        del group[parts["fixed"]:]
        for w in parts["body"].winfo_children():
            w.destroy()
        for w in parts["flow"].frame.winfo_children():
            w.destroy()
        parts["flow"].items = []
        self.view.wraps = [(w, sh) for w, sh in self.view.wraps if w.winfo_exists()]
        self.view.flows = [f for f in self.view.flows if f.frame.winfo_exists()]   # (Who am I's group rows)

    def refocus(self, group: list) -> None:
        """The keyboard stays on the same button after the page is drawn again (by its text, else its place)."""
        if self.focus_text:
            b = next((b for b in group if b.cget("text") == self.focus_text), None)
            if b is None and 0 <= self.focus_index < len(group):
                b = group[self.focus_index]
            if b is not None:
                with contextlib.suppress(Exception):
                    b.focus_set()

    def fill(self, parts: dict, s: MpSession, p: dict, ghost, busy: bool, quiet: bool) -> None:
        g, v, t = self.gui(), self.view, self.view.t
        tk = g.tk
        group = self.buttons
        self.clear(parts, group)
        body = parts["body"]
        on = bool(ghost and ghost.on)
        idle = not s.running

        def row(k: int, name: str, value: str, buttons) -> None:
            v.label(body, v.bold, text=name).grid(row=k, column=0, sticky="nw", padx=(0, 16), pady=2)
            line = tk.Frame(body, bg=t["bg"])
            line.grid(row=k, column=1, sticky="ew", pady=2)
            cell = tk.Frame(line, bg=t["bg"])
            cell.pack(side="right", anchor="n")
            v.wrap_label(line, text=value, share=0.5).pack(side="left", fill="x", anchor="w")
            for text, command, enabled in buttons:
                b = v.make_button(cell, text, command, group)
                b.configure(pady=0, state="normal" if enabled else "disabled")
                b.ring.pack(side="left")

        if self.join_open:
            v.label(body, v.bold, text="Pick a world:").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
            for k, h in enumerate(s.hosts, 1):
                text = f"{h['world']}  -  {h['players']} of {h['max']} players" + ("  -  room code" if h["code"]
                                                                                    else "")
                b = v.make_button(body, text, lambda h=h: self.on_join_pick(h), group, bold=True)
                b.ring.grid(row=k, column=0, columnspan=2, sticky="w")
            if not s.hosts:
                v.wrap_label(body, text="Looking for worlds on your home network..." + (
                    "\nNothing yet? Both PCs on the same Wi-Fi, set to Private. On both PCs, Windows Firewall "
                    "allows Python: Windows Security > Firewall & network protection > Allow an app." if quiet
                    else "")).grid(row=1, column=0, columnspan=2, sticky="w")
            else:
                v.wrap_label(body, text="Check words (they must match the host's screen): " + "; ".join(
                    f"{h['world']}: {h['check']}" for h in s.hosts if h.get("check")), fg=t["disabled"]).grid(
                    row=len(s.hosts) + 1, column=0, columnspan=2, sticky="w", pady=(6, 0))
            b = v.make_button(parts["flow"].frame, "Back", self.back, group)
            parts["flow"].items.append(b.ring)
        else:
            have = ghost is not None or self.bundled(GHOST_ID) is not None
            row(0, "Ghost multiplayer", "ON" if on else "OFF" if have else "Not in this Breathalyzer",
                [("Turn off" if on else "Turn on", self.on_ghost, idle and not busy and have)])
            row(1, "Your name", p["name"], [("Change", self.on_name, idle)])
            row(2, "Who am I", self.look_name(), [("Change", self.show_who, True)])
            row(3, "Story together", STORY_ON if p["story"] else STORY_OFF,
                [("Turn off" if p["story"] else "Turn on", self.on_story, idle)])
            row(4, "Room code", f"{p['code']}: friends type it to join." if p["code"] else
                "OFF: anyone on your home network can join.",
                [("New one" if p["code"] else "Make one", self.on_code, idle)]
                + ([("Turn off", lambda: self.on_code(off=True), idle)] if p["code"] else []))
            flow = parts["flow"]
            if idle:
                for text, command in (("Host", self.on_host), ("Join", self.on_join)):
                    b = v.make_button(flow.frame, text, command, group, bold=True)
                    b.configure(font=v.head, state="normal" if on and not busy else "disabled",
                                bg=t["play"] if on and not busy else t["button"],
                                fg=t["play_fg"] if on and not busy else t["fg"])
                    flow.items.append(b.ring)
            else:
                if s.mode == "open":   # (the first Host: Windows' question about Python first, then the game)
                    b = v.make_button(flow.frame, "Start the game", self.on_start_game, group, bold=True)
                    wait = busy or v.ctl.game_open   # (Play can't start now: the status says why after Host)
                    b.configure(font=v.head, state="disabled" if wait else "normal",
                                bg=t["button"] if wait else t["play"], fg=t["fg"] if wait else t["play_fg"])
                    flow.items.append(b.ring)
                b = v.make_button(flow.frame, "Stop", self.on_stop, group, bold=True)
                b.configure(font=v.head, state="normal" if s.mode != "stopping" else "disabled")
                flow.items.append(b.ring)
            if s.log or self.ctl_failed:
                flow.items.append(v.make_button(flow.frame, "Details", self.on_details, group).ring)
        parts["note"].configure(text=COPY_NOTE)
        parts["flow"].shape = None
        v.root.after_idle(lambda: parts["flow"].frame.winfo_exists() and parts["flow"].reflow(force=True))
        v.rewrap()
        self.refocus(group)

    def render_who(self) -> None:
        v = self.view
        parts = self.who_parts
        ctl = v.ctl
        busy = bool(ctl.jobs.get("main"))
        chars_row = self.row(CHARS_ID)
        body_row = self.row(LINK_BODY_ID)
        chars = characters(ctl.backend.root, on=bool(chars_row and chars_row.on),
                           link_body=bool(body_row and body_row.on), linkle_mod=self.linkle_row() is not None)
        import charsel
        pick = charsel.load_choice()["look"]
        st = self.status_now()
        r = next((c for c in chars if c["id"] == pick), chars[0])
        if self.watch_ctl and busy:
            pass   # (building: the launcher's words and progress)
        elif self.who_error is not None:
            st = self.who_error
        else:
            # (what friends see comes from THEIR PC's built characters: each PC shows the others from its own)
            seen = {"Stand-in": "Friends see a stand-in figure for now: Breathalyzer doesn't build the Link body yet.",
                    "Needs a mod": "Add The Linkle Mod to your mods to be Linkle yourself (Home > Add mod).",
                    "Not tried yet": "Not tried in the game yet: friends may see a stand-in figure instead."}.get(
                r["state"], "Friends see it if their PC built the characters too (each PC builds its own).")
            if r["id"] == "linkle" and r["state"] == "Ready":
                seen = "Friends see Linkle when their PC has The Linkle Mod in its mods too (on or off)."
            st = Status("ok" if r["state"] == "Ready" else "info",
                        [f"You picked {r['name']}.", seen,
                         r["looks"] if r["state"] in ("Ready", "Not tried yet", "Build first") else "",
                         self.body_note, self.session.look_note])
            st.lines = [x for x in st.lines if x]
        self.paint_status(parts, st)
        shape = (pick, tuple(c["state"] for c in chars), busy, chars_row is not None,
                 bool(chars_row and chars_row.on), self.who_error is not None, v.size)
        if shape == self.who_shape:
            return
        self.who_shape = shape
        g, t = self.gui(), v.t
        tk = g.tk
        group = self.who_buttons
        self.clear(parts, group)
        body = parts["body"]
        k = 0
        for group_name in dict.fromkeys(c["group"] for c in chars):
            v.label(body, v.bold, text=group_name).grid(row=k, column=0, sticky="w", pady=(6, 0))
            fl = g.Flow(body, gap=4, bg=t["bg"])
            fl.frame.grid(row=k + 1, column=0, sticky="ew")
            body.columnconfigure(0, weight=1)
            for c in (c for c in chars if c["group"] == group_name):
                b = v.make_button(fl.frame, f"{c['name']}\n{c['state']}", lambda cid=c["id"]: self.on_pick(cid),
                                  group, bold=c["id"] == pick)
                if c["id"] == pick:
                    b.configure(bg=t["play"], fg=t["play_fg"])
                fl.items.append(b.ring)
            v.root.after_idle(lambda fl=fl: fl.frame.winfo_exists() and fl.reflow(force=True))
            v.flows.append(fl)
            k += 2
        cm = self.bundled(CHARS_ID)
        built = sum(1 for c in chars if c["state"] in ("Ready", "Not tried yet") and c["id"] not in ("link", "linkle"))
        flow = parts["flow"]
        if chars_row is not None and not chars_row.on:
            note = "The characters are built but turned off: you see your friends as stand-in figures."
            b = v.make_button(flow.frame, "Turn them on", lambda: self.on_chars(turn_on=True), group, bold=True)
            b.configure(state="disabled" if busy else "normal")
            flow.items.append(b.ring)
        elif chars_row is not None:
            note = (f"{built} characters are built on this PC: you see your friends as who they picked. Your pick is "
                    "used from your next Host or Join (at once when your game is open).")
        elif cm is not None:
            note = ("Build the characters once on this PC (a few minutes, from your own game files): then you see "
                    "your friends as who they picked. They see your pick if they built them too.")
            b = v.make_button(flow.frame, "Build the characters", self.on_chars, group, bold=True)
            b.configure(state="disabled" if busy else "normal")
            flow.items.append(b.ring)
        else:
            note = "The characters don't come with this Breathalyzer: you see your friends as stand-in figures."
        if self.who_error is not None and ctl.details_job is not None:   # (why the build failed: its output)
            flow.items.append(v.make_button(flow.frame, "Details", v.on_details, group).ring)
        parts["note"].configure(text=note)
        flow.shape = None
        v.root.after_idle(lambda: flow.frame.winfo_exists() and flow.reflow(force=True))
        v.rewrap()
        self.refocus(group)

    # --- buttons

    def after(self) -> None:
        self.shape = self.who_shape = None
        self.view.render()

    def on_ghost(self) -> None:
        """Ghost multiplayer ON/OFF: as the mod picker does it (added first when it isn't in the list yet; its
        surprise is built the first time it goes on)."""
        ctl, ghost, gm = self.view.ctl, self.row(GHOST_ID), self.bundled(GHOST_ID)
        if ghost is not None and (ghost.on or gm is None):
            ctl.toggle(GHOST_ID)
            self.session.say(ctl.status.kind, *ctl.status.lines[:1],
                             "Press Host or Join." if not ghost.on else "")
        elif gm is not None:
            self.watch_ctl, self.ctl_failed = "ghost", False
            ctl.install_picked([gm], {GHOST_ID: True}, done=["Ghost multiplayer is on.", "Press Host or Join."])
        self.after()

    def on_chars(self, turn_on: bool = False) -> None:
        """Build the characters (the Characters recipe: tools/charsel.py build --add, from your own game files),
        or turn them on again."""
        ctl, cm = self.view.ctl, self.bundled(CHARS_ID)
        if turn_on and self.row(CHARS_ID) is not None:
            ctl.toggle(CHARS_ID)
        elif cm is not None:
            self.watch_ctl, self.ctl_failed, self.who_error = "chars", False, None
            ctl.install_picked([cm], {CHARS_ID: True}, done=["The characters are built.",
                                                             "You see your friends as who they picked."])
        self.after()

    def on_name(self) -> None:
        self.ask_name()
        self.after()

    def ask_name(self) -> str | None:
        """Your name in multiplayer (kept). None when it was cancelled or can't be used (the status says why)."""
        from mp import valid_name
        v = self.view
        got = v.ask_text("Your name", "Your name in multiplayer", "1 to 20 letters, digits or spaces. Everyone on "
                         "your home network sees it (your world is called by it).", self.prefs()["name"])
        if got is None:
            return None
        name = valid_name(got)
        if name is None:
            self.session.say("error", "That name can't be used.", "Use 1 to 20 letters, digits or spaces.")
            return None
        self.save_prefs(name=name)
        self.session.say("ok", f"Your name: {name}.")
        return name

    def named(self) -> bool:
        """The first Host or Join asks for a name (never the Windows account name). False: not now."""
        return self.prefs()["named"] or self.ask_name() is not None

    def on_story(self) -> None:
        self.save_prefs(story=not self.prefs()["story"])
        self.after()

    def on_code(self, off: bool = False) -> None:
        self.save_prefs(code=None if off else new_code())
        code = self.prefs()["code"]
        self.session.say("ok", f"Room code: {code}." if code else "Room code off.",
                         "Tell your friends the 3 words: they type them when they join." if code else
                         "Anyone on your home network can join.")
        self.after()

    def why(self) -> str | None:
        ctl = self.view.ctl
        job = ctl.jobs.get("main")
        busy = self.gui().LAUNCH_BUSY.get(job.action, "busy").lower() if job else None
        return self.session.why_not(self.enabled(), ctl.game_open or "play" in ctl.jobs, busy)

    def on_host(self) -> None:
        why = self.why()
        if why:
            self.session.say("warn", why)
        elif self.named():
            p = self.prefs()
            self.ctl_failed = False
            self.session.host(p["name"], p["story"], p["code"], pause=not p["firewall"])
        self.after()

    def on_start_game(self) -> None:
        """The first Host waited at "Your world is open" (Windows' question about Python): now the game."""
        self.save_prefs(firewall=True)
        self.session.go()
        self.after()

    def on_join(self) -> None:
        why = self.why()
        if why:
            self.session.say("warn", why)
        elif self.named():
            self.join_open, self.ctl_failed, self.quiet = True, False, False
            self.session.find(True)
            self.session.say("info", "Join a world.", "Worlds on your home network show here by themselves.",
                             "If Windows asks whether Python may use the network: press Allow.")
        self.after()

    def on_join_pick(self, h: dict) -> None:
        """A world picked in the Join list: its room code (when it needs one), the story question, then Join."""
        from mp.security import normalize_code
        from mp.story import story_question
        v, s = self.view, self.session
        code = None
        if h.get("code"):
            got = v.ask_text("Room code", f"{h['world']} needs a room code.", f"Type the 3 words {h['name']} "
                             "told you.", "")
            if got is None:
                return
            code = normalize_code(got)
            if code is None:
                s.say("error", "That isn't a room code.", "A room code is 3 words: ask the host again.")
                return self.after()
        q = story_question(h["name"])
        share = v.ask("Story together", q[0], "\n".join(q[1:4]), [("Yes, share", True), ("No", False)], default=0)
        if share is None:   # (the question closed without an answer: not joined)
            return
        why = self.why()
        if why:
            s.say("warn", why)
            return self.after()
        self.join_open = False
        s.join(h, self.prefs()["name"], bool(share), code)
        self.after()

    def on_stop(self) -> None:
        if self.session is not None:
            self.session.stop()
        self.after()

    def on_details(self) -> None:
        v, s = self.view, self.session
        if self.ctl_failed and not s.running:   # (turning Ghost on, or building the characters, failed)
            return v.on_details()
        text = s.details()
        lines = text.splitlines()
        v.ask("Multiplayer", "What the multiplayer tools said", "\n".join(lines[-14:]), [("OK", None)])

    def on_pick(self, cid: str) -> None:
        try:
            name = self.session.pick_look(cid)
        except (OSError, ValueError) as e:
            self.session.say("error", "Your pick wasn't kept.", type(e).__name__)
            return self.after()
        self.who_error = None
        self.body_note = self.linkle_switch(cid == "linkle")
        self.session.say("ok", f"You picked {name}.", self.body_note, self.session.look_note)
        self.who_shape = None
        self.render_who()

    # --- Linkle: your own body (out/research/linkle-body-swap-plan.md, stage 1)

    def linkle_row(self):
        """The Linkle Mod's row in the mod list (the last one), or None."""
        import charsel
        ctl = self.view.ctl
        rows = ([r for r in ctl.listing.rows if charsel.is_linkle_mod(r.id, r.name, ctl.backend.root)]
                if ctl is not None else [])
        return rows[-1] if rows else None

    def linkle_switch(self, want: bool) -> str:
        """Picking Linkle turns The Linkle Mod ON as Link's own body (every armor piece hers, the inventory shows her),
        and the play-as mod off (it would win over her body); picking anyone else turns it OFF again. Through the
        launcher's own mod switch (Home's Turn on/off); the game gets it at its next start (Play, Host and Join deploy
        first), or now with a restart when it is open. Returns what to say ("": nothing changed)."""
        import charsel
        ctl = self.view.ctl
        if ctl is None:
            return ""
        plan = charsel.linkle_plan([{"id": r.id, "name": r.name, "enabled": r.on} for r in ctl.listing.rows], want,
                                   ctl.backend.root, owned=charsel.linkle_owned())   # (review C3: only its own)
        if want and plan["mod"] is None:
            return "Your friends see Linkle. " + charsel.LINKLE_ADD
        todo = plan["enable"] + plan["disable"]
        if not todo:
            return "Linkle's body is on." if want else ""
        for mod_id in todo:
            ctl.toggle(mod_id)
        rows = {r.id: r.on for r in ctl.listing.rows}
        if any(rows.get(i) is not True for i in plan["enable"]) or any(rows.get(i) is not False for i in plan["disable"]):
            return ("The Linkle Mod couldn't be turned " + ("on" if want else "off") + ": "
                    + " ".join(ctl.status.lines[:2]))
        try:
            charsel.linkle_switched(plan, want)     # (C3: what Who am I turned on; C7: the play-as state forgets it)
        except OSError as e:
            self.session.log.append(f"linkle state: {e}")
        what = ("Linkle's body is on (The Linkle Mod): your armor shows as hers, the inventory shows her." if want
                else "The Linkle Mod is off again: you are Link's own body.")
        return self.restart_for(what)

    def restart_for(self, what: str) -> str:
        """The body changes at the game's next start: closed, the next Play, Host or Join starts it so (they deploy
        first); open, a restart now when you say yes (the game closes itself, then starts again the way it ran)."""
        ctl, s = self.view.ctl, self.session
        if not (ctl.game_open or "play" in ctl.jobs):
            return what + (" The game starts with it now." if s.running else
                           " Press Host or Join (or Play on Home): the game starts with it.")
        friends = "\nYour friends are disconnected, and join again after the restart." if s.running else ""
        yes = self.view.ask("Who am I", "Restart the game now?", what + "\nIt shows after a restart. What you "
                            "haven't saved in the game is lost." + friends, [("Restart now", True), ("Later", False)],
                            default=1)    # (review C5: Enter is Later: a restart drops friends and unsaved play)
        if not yes:
            return what + " It shows from the game's next start."
        # (review C4: the restart is set once the command goes; `exit` is sent off the UI thread, and only a game
        # that can't be reached cancels it, through poll's "game_exit" and relaunch_tick)
        self.relaunch = {"role": s.role if s.running else None, "until": s.clock() + RESTART_WAIT}
        s.exit_error = None
        threading.Thread(target=lambda: s.events.put(("game_exit", s.send_exit())), daemon=True,
                         name="mp exit").start()
        return what + " Restarting the game..."

    def relaunch_tick(self) -> bool:
        """Linkle's restart, once the game has closed: started again the way it ran (Play; a host hosts again; a
        friend's Join list opens). Not closed in RESTART_WAIT: given up, said. True when something changed."""
        r, ctl, s = self.relaunch, self.view.ctl, self.session
        if r is None or ctl is None or s is None:
            return False
        if s.exit_error:          # (the game couldn't be reached: nothing closes, nothing restarts)
            s.exit_error, self.relaunch = None, None
            self.body_note = "The game didn't answer: close it yourself, then start it again."
            s.say("warn", self.body_note)
            return True
        if ctl.game_open or "play" in ctl.jobs or ctl.jobs.get("main") or s.running:
            if s.clock() > r["until"]:
                self.relaunch = None
                self.body_note = "The game didn't close: close it yourself, then start it again."
                s.say("warn", self.body_note)
                return True
            return False
        self.relaunch = None
        if r["role"] == "host":
            self.on_host()
        elif r["role"] == "join":
            self.on_join()
        else:
            ctl.launch()
        self.body_note = ""
        return True
