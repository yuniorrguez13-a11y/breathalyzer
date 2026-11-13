"""Tests for Breathalyzer's Multiplayer page (tools/mp_page.py), what it needs from the launcher (tools/modman_gui.py:
Play with game variables, the page in the window) and the small command-line parts it uses (coop_agent and client
--stdin, coop_agent join --check and its READY line, charsel build --add).

Nothing here starts a real tool process, the game or a listener on the network: processes are stand-ins, Play runs
on the fake modman, the Join list's search is a stand-in. (One test runs coop_agent host in this process with its
port taken, on 127.0.0.1 only and without the Join list's answers, to read its real last words.) Saves, libraries
and Savage Wind folders are made up in temp folders; the launcher's own folder (BREATHALYZER_HOME: the Who am I
choice) points into a temp folder too. The window tests use a window that never shows (root.withdraw(); its
dialogs are stand-ins)."""
import asyncio
import contextlib
import gc
import io
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import charsel  # noqa: E402
import launcher as la  # noqa: E402
import modman_gui as gui  # noqa: E402
import mp_page as mpp  # noqa: E402
from mp import client, coop_agent, story  # noqa: E402
import test_launcher as tl  # noqa: E402 (its made-up Savage Wind and fake modman; never its window tests)
import test_modman_gui as tm  # noqa: E402

HOST = {"address": "192.168.1.20", "port": 7788, "world": "Ana's world", "name": "Ana", "players": 1, "max": 8,
        "code": False, "check": "apple river stone moon", "ver": "0.7.1"}


def write(path: Path, data: bytes | str = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


class Pipe:
    """A stand-in for a tool's stdin: what the page writes, and whether it closed it."""

    def __init__(self, proc):
        self.proc, self.data, self.closed = proc, b"", False

    def write(self, b: bytes) -> int:
        if self.closed:
            raise ValueError("closed")
        self.data += b
        return len(b)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        if self.proc.ends_on_stdin:
            self.proc.end(0)


class FakeTool:
    """A stand-in for a started tool: the test says what it prints (say) and when it ends (end); it ends by itself
    when its stdin closes (as --stdin does), unless ends_on_stdin is off (a tool that hangs)."""

    def __init__(self, argv, kw):
        self.argv, self.kw = list(argv), kw
        self.lines: "list[bytes | None]" = []
        self.cond = threading.Condition()
        self.returncode = None
        self.killed = False
        self.ends_on_stdin = True
        self.stdin = Pipe(self)
        self.stdout = self._out()
        self.pid = None

    def _out(self):
        while True:
            with self.cond:
                while not self.lines:
                    self.cond.wait(5)
                line = self.lines.pop(0)
            if line is None:
                return
            yield line

    def say(self, *texts: str) -> None:
        with self.cond:
            self.lines += [(t + "\r\n").encode("utf-8") for t in texts]
            self.cond.notify_all()

    def end(self, code=0) -> None:
        with self.cond:
            if self.returncode is None:
                self.returncode = code
                self.lines.append(None)
                self.cond.notify_all()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        end = time.monotonic() + (timeout if timeout is not None else 5)
        while self.returncode is None and time.monotonic() < end:
            time.sleep(0.01)
        if self.returncode is None:
            raise TimeoutError
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.end(-9)

    @property
    def script(self) -> str:
        return os.path.basename(self.argv[2])


class Spawn:
    def __init__(self):
        self.tools: list[FakeTool] = []

    def __call__(self, argv, **kw):
        t = FakeTool(argv, kw)
        self.tools.append(t)
        return t

    def get(self, script: str) -> FakeTool | None:
        return next((t for t in reversed(self.tools) if t.script == script), None)


def settle(s: mpp.MpSession, cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s.poll()
        if cond():
            return True
        time.sleep(0.01)
    return False


class Temp(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(prefix="mp-page-test-")
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)
        env = mock.patch.dict(os.environ, {"BREATHALYZER_HOME": str(self.tmp / "home")})
        env.start()
        self.addCleanup(env.stop)


class Session(Temp):
    def make(self, **kw):
        self.spawn = Spawn()
        self.games: list[dict] = []
        self.world = self.tmp / "lib" / "multiplayer" / "worlds" / "Anas_world"
        self.copy = self.tmp / "lib" / "multiplayer" / "joined" / "Anas_world-apple_river_stone_moon"
        kw.setdefault("host_prep", lambda lib, install, name: self.world)
        kw.setdefault("join_prep", lambda lib, install, host: self.copy)
        self.s = mpp.MpSession(self.tmp / "lib", self.tmp / "SW", python="py", spawn=self.spawn,
                               start_game=self.games.append, **kw)
        return self.s

    def test_host_step_by_step(self):
        s = self.make()
        self.assertIsNone(s.why_not({mpp.GHOST_ID}, False))
        s.host("Ana", story=True)
        self.assertEqual(s.mode, "prep")
        self.assertEqual(s.status.lines[0], "Getting your world ready...")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        agent = self.spawn.get("coop_agent.py")
        self.assertEqual(agent.argv, ["py", "-u", mpp.AGENT_PY, "host", "--name", "Ana", "--game-port", "7777",
                                      "--stdin", "--story-from", str(self.world),
                                      "--library", str(self.tmp / "lib" / "multiplayer")])   # (--library last)
        self.assertEqual(agent.kw["creationflags"], mpp.NO_WINDOW)
        self.assertEqual(self.games, [])                           # (no game before the world is open)
        agent.say("Hosting Ana's world on port 7788. Check words: apple river stone moon")
        self.assertTrue(settle(s, lambda: s.mode == "game"))
        self.assertEqual(self.games, [{"SAVAGEWIND_SAVE_DIR": str(self.world)}])   # (the copy, never his save)
        cl = self.spawn.get("client.py")
        self.assertEqual(cl.argv[:3], ["py", "-u", mpp.CLIENT_PY])
        self.assertEqual(cl.argv[3:], ["--relay", "127.0.0.1:7788", "--name", "Ana", "--no-ui", "--ghost-looks",
                                       "--game-port", "7777", "--stdin", "--library", str(self.tmp / "lib")])
        self.assertFalse(s.game_update(starting=True, game_open=False))   # (Play still getting the mods ready)
        self.assertTrue(s.game_update(starting=False, game_open=True))
        self.assertEqual(s.mode, "live")
        self.assertEqual(s.status.lines[0], "You're hosting Ana's world.")
        self.assertIn("Check words: apple river stone moon.", s.status.lines)
        self.assertIn(mpp.FIREWALL_LINE, s.status.lines)              # (until a friend has come in)
        agent.say("Ben joined")
        self.assertTrue(settle(s, lambda: s.players == ["Ben"]))     # (two tools: their lines come in any order)
        cl.say("Ghost: showing Ben in your game.", "Ben: 12 m NE, 2 m higher")
        self.assertTrue(settle(s, lambda: s.where.startswith("Ben: 12 m")))
        self.assertEqual(s.status.lines, ["You see Ben in your game."])   # (news stays a moment)
        with mock.patch.object(mpp, "NEWS_HOLD", 0.0):
            cl.say("Ben: 11 m NE, 2 m higher")
            self.assertTrue(settle(s, lambda: s.where.startswith("Ben: 11 m")))
        self.assertEqual(s.status.lines, ["You're hosting Ana's world.", "Friends here: Ben.",
                                          "Ben: 11 m NE, 2 m higher"])
        agent.say("Ben left")
        self.assertTrue(settle(s, lambda: s.status.lines[0] == "Ben left your world."))
        self.assertEqual(s.players, [])
        s.stop()
        self.assertEqual(s.status.lines, ["Stopping..."])
        self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertEqual(s.status.lines, ["Multiplayer stopped.", mpp.COPY_LINE])   # (on the co-op copy: said)
        self.assertEqual(agent.stdin.data, b"end\n")              # (a host tells its friends: coop_agent --stdin)
        self.assertEqual(cl.stdin.data, b"stop\n")
        self.assertTrue(agent.stdin.closed and cl.stdin.closed)
        self.assertFalse(agent.killed or cl.killed)
        self.assertIn("agent: Hosting Ana's world on port 7788. Check words: apple river stone moon", s.details())

    def test_host_without_story_and_with_a_room_code(self):
        s = self.make()
        s.host("Ana", story=False, code="apple river stone")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        argv = self.spawn.get("coop_agent.py").argv
        self.assertNotIn("--story-from", argv)
        self.assertEqual(argv[argv.index("--code") + 1], "apple river stone")
        self.spawn.get("coop_agent.py").say("Hosting Ana's world on port 7788. Check words: a b c d. Room code: "
                                            "apple river stone")
        self.assertTrue(settle(s, lambda: s.mode == "game"))
        cl = self.spawn.get("client.py").argv
        self.assertEqual(cl[cl.index("--code") + 1], "apple river stone")

    def test_join_step_by_step(self):
        s = self.make()
        s.join(dict(HOST, code=True), "Ben", share=True, code="apple river stone")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        agent = self.spawn.get("coop_agent.py")
        self.assertEqual(agent.argv[3:], ["join", "--name", "Ben", "--address", "192.168.1.20", "--port", "7788",
                                          "--save-dir", str(self.copy), "--yes", "--game-port", "7777", "--stdin",
                                          "--code", "apple river stone", "--check", "apple river stone moon",
                                          "--library", str(self.tmp / "lib" / "multiplayer")])
        agent.say(r"Ana's story added: 3 towers, 1 Divine Beasts. (backup: C:\Users\Someone\lib\backups\x)")
        self.assertTrue(settle(s, lambda: "story" in s.status.lines[0]))
        self.assertEqual(s.status.lines[:2], ["Ana's story is in your co-op save.",
                                              "Nothing was taken away. A backup was made first."])
        self.assertNotIn("Someone", " ".join(s.status.lines))        # (never a path with a user name)
        self.assertEqual(self.games, [])                               # (the game waits for READY)
        agent.say(f"{coop_agent.READY} with SAVAGEWIND_SAVE_DIR={self.copy} and press Continue.")
        self.assertTrue(settle(s, lambda: s.mode == "game"))
        self.assertEqual(self.games, [{"SAVAGEWIND_SAVE_DIR": str(self.copy)}])
        cl = self.spawn.get("client.py").argv
        self.assertEqual(cl[cl.index("--relay") + 1], "192.168.1.20:7788")
        s.game_update(False, True)
        self.assertEqual(s.status.lines[0], "You're in Ana's world.")
        agent.say("Lost the line to Ana (no answer); trying again...")
        self.assertTrue(settle(s, lambda: s.status.lines == ["Lost Ana's world.", "Trying again..."]))
        agent.say("Back in Ana's world.")
        self.assertTrue(settle(s, lambda: s.status.lines == ["Back in Ana's world."]))
        agent.say("Ana ended the session")
        agent.end(0)
        self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertEqual(s.status.lines, ["Ana ended the session.", mpp.COPY_LINE])
        self.assertEqual(self.spawn.get("client.py").stdin.data, b"stop\n")   # (its ghost taken out of the game)

    def test_join_without_story(self):
        s = self.make()
        s.join(HOST, "Ben", share=False)
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        argv = self.spawn.get("coop_agent.py").argv
        self.assertIn("--no", argv)
        self.assertNotIn("--code", argv)

    def test_a_wrong_room_code_in_plain_words(self):
        s = self.make()
        s.join(dict(HOST, code=True), "Ben", code="apple river stone")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        agent = self.spawn.get("coop_agent.py")
        agent.say("Couldn't join: wrong room code")
        agent.end(1)
        self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertEqual(s.status.kind, "error")
        self.assertEqual(s.status.lines, ["You're out of the world.",
                                          "Wrong room code. Ask the host for the 3 words and try again."])
        self.assertEqual(self.games, [])                               # (the game never started)
        self.assertIsNone(self.spawn.get("client.py"))

    def real_host_output_with_its_port_taken(self) -> tuple[int, str]:
        """What `coop_agent host` really prints when its port is taken (run here, in this process: the relay on
        127.0.0.1 and no Join-list answers, so nothing listens on the network)."""
        import socket
        taken = socket.socket()
        self.addCleanup(taken.close)
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        lib = self.tmp / "agent-lib"
        write(lib / "mp" / story.LIST_FILE, "base MapTower_01\n")
        real = coop_agent.host_session

        def loopback(*a, **k):
            return real(*a, **{**k, "relay_host": "127.0.0.1", "discovery_port": None})

        out = io.StringIO()
        with mock.patch.object(coop_agent, "host_session", loopback), mock.patch.object(sys, "stdin", io.StringIO()), \
                contextlib.redirect_stdout(out):
            code = coop_agent.main(["host", "--name", "Ana", "--port", str(port), "--library", str(lib)])
        return code, out.getvalue()

    def test_a_host_that_cant_open_its_port(self):
        code, said = self.real_host_output_with_its_port_taken()
        self.assertEqual(code, 1)
        self.assertIn("Stopped: couldn't open port", said)          # (its last words, no traceback)
        self.assertNotIn("Traceback", said)
        s = self.make()
        s.host("Ana")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        agent = self.spawn.get("coop_agent.py")
        agent.say(*said.splitlines())
        agent.end(code)
        self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertEqual(s.status.lines, ["Your world couldn't open.", "Another program uses port 7788: is "
                                          "Breathalyzer already hosting? Close it and try again."])

    def test_a_tool_that_ends_without_last_words(self):
        s = self.make()
        s.host("Ana")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        agent = self.spawn.get("coop_agent.py")
        agent.say("Traceback (most recent call last):", r'  File "C:\Users\Someone\app\tools\mp\relay.py", line 9',
                  "OSError: [Errno 10048] error while attempting to bind on address ('0.0.0.0', 7788): only one "
                  "usage of each socket address (protocol/network address/port) is normally permitted")
        agent.end(1)
        self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertEqual(s.status.lines, ["Your world couldn't open.", "Another program uses port 7788: is "
                                          "Breathalyzer already hosting? Close it and try again."])
        shown = " ".join(s.status.lines)
        self.assertNotIn("Traceback", shown)
        self.assertNotIn("Someone", shown)
        with mock.patch.dict(os.environ, {"USERPROFILE": r"C:\Users\Someone"}):
            self.assertIn(r"%USERPROFILE%\app\tools", s.details())       # (Details: no user name either)
            self.assertNotIn("Someone", s.details())

    def test_first_host_waits_for_start_the_game(self):
        s = self.make()
        s.host("Ana", pause=True)
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        agent = self.spawn.get("coop_agent.py")
        self.assertIn(mpp.FIREWALL_ASK, s.status.lines)                 # ("Opening your world...")
        agent.say("Join lists can't find this host ([WinError 10013] access denied); friends can still type your "
                  "address.", "Hosting Ana's world on port 7788. Check words: a b c d")
        self.assertTrue(settle(s, lambda: s.mode == "open"))
        self.assertEqual(self.games, [])                                # (Windows' question first, not the game)
        self.assertEqual(s.status.lines, ["Your world is open.", mpp.FIREWALL_ASK, mpp.NO_LIST_LINE,
                                          "Then press Start the game."])
        s.go()
        self.assertEqual(s.mode, "game")
        self.assertEqual(self.games, [{"SAVAGEWIND_SAVE_DIR": str(self.world)}])
        self.assertIn(mpp.NO_LIST_LINE, s.status.lines)                 # (kept, not gone in 0.1 s)
        s.game_update(False, True)
        self.assertIn(mpp.NO_LIST_LINE, s.status.lines)                 # (until a friend has come in)
        agent.say("Ben joined")
        self.assertTrue(settle(s, lambda: s.players == ["Ben"]))
        s.live_status()
        self.assertNotIn(mpp.NO_LIST_LINE, s.status.lines)
        self.assertNotIn(mpp.FIREWALL_LINE, s.status.lines)
        s.stop()

    def test_stop_while_play_gets_ready(self):
        cancels = []
        s = self.make(cancel_game=lambda: cancels.append(1) or True)
        s.host("Ana")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        self.spawn.get("coop_agent.py").say("Hosting Ana's world on port 7788. Check words: a b c d")
        self.assertTrue(settle(s, lambda: s.mode == "game"))
        s.stop()
        self.assertEqual(cancels, [1])                                  # (Play's Cancel: the game doesn't open)
        self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertEqual(s.status.lines, ["Multiplayer stopped."])
        s = self.make(cancel_game=lambda: False)                        # (too late: Play is starting the game)
        s.host("Ana")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        self.spawn.get("coop_agent.py").say("Hosting Ana's world on port 7788. Check words: a b c d")
        self.assertTrue(settle(s, lambda: s.mode == "game"))
        s.stop()
        self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertEqual(s.status.lines, ["Multiplayer stopped.", mpp.OPENS_LINE])

    def test_the_game_didnt_start_says_the_launchers_words(self):
        s = self.make()
        s.host("Ana")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        self.spawn.get("coop_agent.py").say("Hosting Ana's world on port 7788. Check words: a b c d")
        self.assertTrue(settle(s, lambda: s.mode == "game"))
        s.game_update(False, False, why=["Your mods couldn't be made ready.", "A file is in use."])
        self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertEqual(s.status.lines, ["The game didn't start.", "Your mods couldn't be made ready.",
                                          "A file is in use.", "Multiplayer stopped."])

    def test_an_unknown_reason_is_not_shown_raw(self):
        s = self.make()
        s.host("Ana")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        agent = self.spawn.get("coop_agent.py")
        agent.say(r"Stopped: something odd in C:\Users\Someone\x")
        agent.end(1)
        self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertEqual(s.status.lines, ["Your world couldn't open.", "Press Details to see why."])

    def test_the_game_didnt_start_or_closed(self):
        s = self.make()
        s.host("Ana")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        self.spawn.get("coop_agent.py").say("Hosting Ana's world on port 7788. Check words: a b c d")
        self.assertTrue(settle(s, lambda: s.mode == "game"))
        self.assertTrue(s.game_update(starting=False, game_open=False))   # (Play ended, no game)
        self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertEqual(s.status.lines, ["The game didn't start.", "Home says why. Multiplayer stopped."])
        self.assertTrue(all(t.stdin.closed for t in self.spawn.tools))
        s.host("Ana")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        self.spawn.get("coop_agent.py").say("Hosting Ana's world on port 7788. Check words: a b c d")
        self.assertTrue(settle(s, lambda: s.mode == "game"))
        s.game_update(False, True)
        self.assertEqual(s.mode, "live")
        s.game_update(False, False)
        self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertEqual(s.status.lines, ["The game closed.", "Multiplayer stopped."])

    def test_a_tool_that_doesnt_end_is_killed(self):
        s = self.make()
        s.host("Ana")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        agent = self.spawn.get("coop_agent.py")
        agent.ends_on_stdin = False
        with mock.patch.object(mpp, "STOP_WAIT", 0.2):
            s.stop()
            self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertTrue(agent.killed)

    def test_the_client_finds_no_ghost_mod(self):
        s = self.make()
        s.host("Ana")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        self.spawn.get("coop_agent.py").say("Hosting Ana's world on port 7788. Check words: a b c d")
        self.assertTrue(settle(s, lambda: s.mode == "game"))
        self.spawn.get("client.py").say("Ghost: the game has no `ghost` command. Start it with modhost and the ghost "
                                        "mod (docs/ghost.md); showing the radar only.")
        self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertEqual(s.status.lines[:2], ["Ghost multiplayer didn't run in your game.",
                                              "Turn off Infinite Stamina (on Home or in the game's Mods page), then "
                                              "press Host or Join again."])

    def test_prep_failure_and_stop_while_preparing(self):
        def no_save(*a):
            raise mpp.PrepError("You have no save yet.")
        s = self.make(host_prep=no_save)
        s.host("Ana")
        self.assertTrue(settle(s, lambda: s.mode == "idle"))
        self.assertEqual(s.status.lines, ["Multiplayer didn't start.", "You have no save yet."])
        gate = threading.Event()
        s = self.make(host_prep=lambda *a: gate.wait(5) and self.world)
        s.host("Ana")
        s.stop()
        self.assertEqual(s.mode, "idle")
        gate.set()
        time.sleep(0.05)
        s.poll()
        self.assertEqual(s.mode, "idle")            # (the late copy starts nothing)
        self.assertEqual(self.spawn.tools, [])

    def test_why_not(self):
        s = self.make()
        self.assertEqual(s.why_not(set(), False), "Turn on Ghost multiplayer first.")
        self.assertIn("Infinite Stamina", s.why_not({mpp.GHOST_ID, mpp.STAMINA_ID}, False))
        self.assertIn("Close Savage Wind first", s.why_not({mpp.GHOST_ID}, True))
        self.assertEqual(s.why_not({mpp.GHOST_ID}, False, "getting your mods ready"),
                         "Please wait: getting your mods ready.")
        s.host("Ana")
        self.assertIn("press Stop first", s.why_not({mpp.GHOST_ID}, False))
        s.stop()

    def test_join_list(self):
        calls = []

        def search(seconds):
            calls.append(seconds)
            time.sleep(0.01)
            return [HOST]

        s = self.make(search=search)
        s.find(True)
        self.assertTrue(s.finding)
        self.assertTrue(settle(s, lambda: s.hosts == [HOST]))
        s.find(False)
        self.assertFalse(s.finding)
        s.hosts = []
        time.sleep(0.05)
        s.poll()
        self.assertEqual(s.hosts, [])               # (closed: late answers are dropped)
        self.assertEqual(calls[0], mpp.FIND_SECONDS)

    def test_who_am_i_is_kept_and_told_to_the_game(self):
        told, answer = [], [""]
        s = self.make(send_iam=lambda cid: told.append(cid) or answer[0])
        self.assertEqual(s.pick_look("zelda"), "Zelda")
        self.assertEqual(charsel.load_choice()["look"], "zelda")   # (what client.py reads when it starts)
        self.assertEqual(told, [])                                  # (no game: nothing sent)
        self.assertEqual(s.look_note, "It is used from your next Host or Join.")
        s.host("Ana")
        self.assertTrue(settle(s, lambda: s.mode == "agent"))
        self.spawn.get("coop_agent.py").say("Hosting Ana's world on port 7788. Check words: a b c d")
        self.assertTrue(settle(s, lambda: s.mode == "game"))
        s.pick_look("goron")
        self.assertEqual(s.look_note, "Telling your game...")       # (never "in your game" before it answered)
        self.assertTrue(settle(s, lambda: told == ["goron"] and s.look_note == "In your game now."))
        answer[0] = "ConnectionRefusedError: the game isn't open yet"
        s.pick_look("rito")
        self.assertTrue(settle(s, lambda: s.look_note.startswith("Your game didn't take it now")))
        self.assertIn("iam: ConnectionRefusedError", s.details())
        with self.assertRaises(ValueError):
            s.pick_look("ganon")
        s.stop()


class Prep(Temp):
    def install(self) -> Path:
        sw = tl.make_install(self.tmp / "SW")
        write(sw / "extracted" / "romfs" / "Pack" / "Bootup.pack", b"made up")
        write(sw / "save" / "save_0" / "0" / story.SAVE_NAME, b"\x30\x47\0\0" + b"\xff" * 60)
        return sw

    def test_host_world_and_story_list(self):
        sw = self.install()
        lib = self.tmp / "lib"
        before = (sw / "save" / "save_0" / "0" / story.SAVE_NAME).read_bytes()
        with mock.patch.object(story, "list_from_romfs", return_value=story.StoryList(["MapTower_01"])) as lst, \
                mock.patch.object(story, "_running_processes", return_value=[]):
            world = mpp.prep_host(lib, sw, "Ana")
            again = mpp.prep_host(lib, sw, "Ana")
        self.assertEqual(world, lib / "multiplayer" / "worlds" / "Anas_world")
        self.assertEqual(again, world)                                   # (made once, then used again)
        self.assertEqual(lst.call_count, 1)                              # (the story list too)
        self.assertEqual(story.StoryList.load(lib / "multiplayer" / "mp" / story.LIST_FILE).base, ["MapTower_01"])
        self.assertTrue((world / story.COPY_MARK).is_file())
        self.assertEqual((world / "save_0" / "0" / story.SAVE_NAME).read_bytes(), before)
        self.assertEqual((sw / "save" / "save_0" / "0" / story.SAVE_NAME).read_bytes(), before)   # (only read)

    def test_join_copy_is_per_host(self):
        sw = self.install()
        with mock.patch.object(story, "list_from_romfs", return_value=story.StoryList(["MapTower_01"])), \
                mock.patch.object(story, "_running_processes", return_value=[]):
            a = mpp.prep_join(self.tmp / "lib", sw, HOST)
            b = mpp.prep_join(self.tmp / "lib", sw, dict(HOST, check="bear cake dove fern"))
        self.assertEqual(a.parent, self.tmp / "lib" / "multiplayer" / "joined")
        self.assertNotEqual(a, b)

    def test_plain_reasons(self):
        sw = self.install()
        with mock.patch.object(story, "list_from_romfs", return_value=story.StoryList(["MapTower_01"])), \
                mock.patch.object(story, "_running_processes", return_value=["savagewind.exe"]):
            with self.assertRaisesRegex(mpp.PrepError, "Close Savage Wind first"):
                mpp.prep_host(self.tmp / "lib", sw, "Ana")
        (sw / "save" / "save_0" / "0" / story.SAVE_NAME).unlink()
        with mock.patch.object(story, "_running_processes", return_value=[]):
            with self.assertRaisesRegex(mpp.PrepError, "You have no save yet"):
                mpp.prep_host(self.tmp / "lib", sw, "Ana")
        (sw / "extracted" / "romfs" / "Pack" / "Bootup.pack").unlink()
        with self.assertRaisesRegex(mpp.PrepError, "Set up game files"):
            mpp.prep_host(self.tmp / "lib2", sw, "Ana")


class Small(Temp):
    def test_prefs(self):
        with mock.patch.dict(os.environ, {"USERNAME": "Ana Maria"}):
            p = mpp.load_prefs({})
        self.assertEqual(p, {"name": "Player", "named": False, "story": True, "code": None, "firewall": False})
        p = mpp.load_prefs({"multiplayer": {"name": "x" * 40, "story": False, "code": "Apple  river-stone"}})
        self.assertEqual(p, {"name": "Player", "named": False, "story": False, "code": "apple river stone",
                             "firewall": False})
        p = mpp.load_prefs({"multiplayer": {"name": "Ana", "firewall": True}})
        self.assertEqual((p["name"], p["named"], p["firewall"]), ("Ana", True, True))
        self.assertEqual(mpp.load_prefs({"multiplayer": {"code": "not a code"}})["code"], None)
        from mp.security import normalize_code
        code = mpp.new_code()
        self.assertEqual(normalize_code(code), code)
        self.assertEqual(len(code.split()), 3)

    def test_characters(self):
        lib = self.tmp / "lib"
        self.assertEqual({c["state"] for c in mpp.characters(lib) if c["id"] not in ("link", "linkle")},
                         {"Build first"})
        write(lib / "mods" / mpp.CHARS_ID / charsel.CATALOG, json.dumps(
            {"built": {"zelda": {"slot": charsel.SLOTS["zelda"]}, "hylian": {"slot": charsel.SLOTS["hylian"]}},
             "linkle": {"how": "missing"}}))
        chars = {c["id"]: c for c in mpp.characters(lib)}
        self.assertEqual(list(chars), charsel.order())
        self.assertEqual(len(chars), 11)                                 # (9 characters, Link and Linkle)
        self.assertEqual((chars["link"]["state"], chars["hylian"]["state"], chars["zelda"]["state"],
                          chars["goron"]["state"], chars["linkle"]["state"]),
                         ("Stand-in", "Ready", "Ready", "Build first", "Needs a mod"))
        self.assertEqual(mpp.characters(lib, link_body=True)[0]["state"], "Ready")   # (the Link body in the list)
        self.assertEqual(chars["goron"]["name"], "Daruk")
        self.assertEqual(mpp.characters(lib, on=False)[2]["state"], "Build first")   # (turned off: not in the game)
        for c in chars.values():                                         # (player words, no developer markers)
            self.assertNotRegex(c["looks"], r"\[[A-Z]\]|armor actors|MB\b")
        self.assertEqual(mpp.NOT_TRIED, frozenset())

    def test_build_reason(self):
        self.assertEqual(mpp.build_reason([r"charsel: Z:\SW\extracted\romfs isn't the game's extracted romfs (no "
                                           r"Actor\Pack)"]),
                         "Your game files aren't set up: open Savage Wind and press Set up game files.")
        self.assertIsNone(mpp.build_reason(["Traceback (most recent call last):"]))

    def test_plain_reason(self):
        self.assertEqual(mpp.plain_reason("too many wrong room codes"),
                         "Too many wrong room codes: wait 10 minutes, then try again.")
        self.assertIsNone(mpp.plain_reason("Traceback (most recent call last):"))


class PageParts(Temp):
    """MpPage's parts that need only the launcher's controller (a stand-in here)."""

    def page(self, **ctl):
        c = types.SimpleNamespace(game_open=False, jobs={}, can=lambda action: False, cancel=mock.Mock(),
                                  status=gui.Status("info", ["Ready."]), progress=lambda: [])
        c.__dict__.update(ctl)
        p = mpp.MpPage(types.SimpleNamespace(ctl=c))
        p.session = mpp.MpSession(self.tmp / "lib", self.tmp / "SW", python="py", spawn=Spawn())
        return p, c

    def test_stop_cancels_play_only_before_the_game(self):
        p, c = self.page(jobs={"main": object()}, can=lambda action: action == "cancel")
        self.assertTrue(p.cancel_play())                         # (getting the mods ready: Play's Cancel)
        c.cancel.assert_called_once_with()
        p, c = self.page(jobs={"play": object()})
        self.assertFalse(p.cancel_play())                        # (the game is being started: it still opens)
        c.cancel.assert_not_called()
        p, c = self.page(game_open=True)
        self.assertFalse(p.cancel_play())

    def test_play_progress_while_the_game_gets_ready(self):
        p, c = self.page(jobs={"main": object()}, status=gui.Status("busy", ["Getting your mods ready...",
                                                                             "Then the game starts."]),
                         progress=lambda: ["Getting ready... 0:12"])
        p.session.mode = "game"
        self.assertEqual(p.status_now().lines, ["Getting your mods ready...", "Then the game starts.",
                                                "Getting ready... 0:12", "Then load your world with Continue."])
        c.jobs = {}
        p.session.say("busy", "Starting the game...")
        self.assertEqual(p.status_now().lines, ["Starting the game..."])


# ---------------------------------------------------------------- what the page needs from the launcher

class PlayWithEnv(Temp):
    def test_play_passes_game_variables_only_to_that_game(self):
        class Modman(tl.LaunchModman):
            def game_env(self, base, launch, port):
                return {"FROM": "modman"}

            def cmd_run(self, *opts):
                self.envs.append(self.game_env({}, {}, 7777))
                return super().cmd_run(*opts)

        mm = Modman()
        mm.cheats, mm.envs = False, []
        mm.deployed = mm.inputs_hash(None)
        backend = gui.LaunchBackend(None, "Z:\\SW", mm=mm, spawn=tm.FakeSpawn(mm), python="py",
                                    running=lambda: False, menu=False, popen=mock.Mock())
        ctl = gui.LaunchController(backend, clock=tm.Clock())
        ctl.launch(env={"SAVAGEWIND_SAVE_DIR": "Z:\\copy"})
        self.assertTrue(ctl.settle())
        self.assertEqual(mm.envs, [{"FROM": "modman", "SAVAGEWIND_SAVE_DIR": "Z:\\copy"}])
        self.assertNotIn("SAVAGEWIND_SAVE_DIR", os.environ)                 # (this process's own stays as it was)
        self.assertEqual(mm.game_env({}, {}, None), {"FROM": "modman"})    # (put back)
        ctl.game_open = False
        ctl.launch()                                                        # (the next plain Play: without it)
        self.assertTrue(ctl.settle())
        self.assertEqual(mm.envs[-1], {"FROM": "modman"})


def hidden_root():
    """A Tk root that never shows (as tests/test_breath.py makes it)."""
    if gui.tk is None:
        raise unittest.SkipTest("no tkinter")
    try:
        root = gui.tk.Tk()
    except gui.tk.TclError as e:
        raise unittest.SkipTest(f"no display: {e}")
    root.withdraw()
    with contextlib.suppress(gui.tk.TclError):
        root.attributes("-alpha", 0.0)
    for name in ("deiconify", "wm_deiconify", "lift", "tkraise", "focus_force", "iconify", "wm_iconify"):
        setattr(root, name, lambda *a, **k: None)
    return root


def texts(widget) -> list[str]:
    out = []
    for w in widget.winfo_children():
        with contextlib.suppress(gui.tk.TclError, AttributeError):
            t = w.cget("text")
            if t:
                out.append(str(t))
        out += texts(w)
    return out


def buttons(widget) -> dict:
    out = {}
    for w in widget.winfo_children():
        if isinstance(w, gui.tk.Button):
            out[str(w.cget("text"))] = w
        out.update(buttons(w))
    return out


class MpModman(tl.LaunchModman):
    def __init__(self, mods):
        super().__init__(mods)
        self.cheats, self.envs = False, []

    def game_env(self, base, launch, port):
        return {}

    def cmd_run(self, *opts):
        self.envs.append((list(opts), self.game_env({}, {}, 7777)))
        return super().cmd_run(*opts)

    def cmd_build(self, *opts):   # (tools/charsel.py build --add: the Characters recipe)
        self.mods.append({"id": mpp.CHARS_ID, "name": "Characters for multiplayer", "enabled": True,
                          "kinds": ["romfs"]})
        print("Built 9 characters.")
        return 0


class Window(Temp):
    """The page in LauncherView, in a window that never shows."""

    def open(self, ghost_bundled=True):
        root = hidden_root()
        self.sw = tl.make_install(self.tmp / "SW")
        app = self.tmp / "app"
        mods = [{"id": mpp.GHOST_ID, "title": "Ghost multiplayer", "default": "off", "kind": "code",
                 "description": "See a friend in your world.", "folder": "bundled-mods/ghost", "developer": True,
                 "tags": ["Experimental"]},
                {"id": mpp.CHARS_ID, "title": "Characters for multiplayer", "default": "off", "kind": "recipe",
                 "description": "Your friends see you as Zelda.", "build": [["charsel.py", "build", "--add"]],
                 "tags": ["Experimental"]}] if ghost_bundled else []
        tl.make_app(app, mods + [{"id": "botw-mod-loader.basics-show-pos", "title": "Show Position",
                                  "default": "on", "kind": "code", "description": "Where Link is.",
                                  "folder": "bundled-mods/show-pos"}])
        write(app / "tools" / "charsel.py", "# made up\n")
        self.mm = MpModman([])
        self.mm.deployed = self.mm.inputs_hash(None)
        self.spawn = tm.FakeSpawn(self.mm)

        def backend(lib, install):
            self.mm.installs.add(install)
            return gui.LaunchBackend(lib, install, mm=self.mm, spawn=self.spawn, python="py", running=lambda: False,
                                     menu=False, popen=mock.Mock())

        key, _ = la.read_bundled(str(app))
        settings = la.Settings(install=str(self.sw), picked=key)
        first = la.FirstRun(settings, places=lambda: {"near": []},
                            check=lambda f, lib, why: tl.check(f, lib or str(self.tmp / "lib"), why, tmp=self.tmp))
        try:
            view = gui.LauncherView(root, settings, str(self.tmp / "home" / la.SETTINGS_FILE), first_run=first,
                                    make_backend=backend, app_dir=str(app),
                                    reg_base="Software\\BreathalyzerTest-none\\Classes", running=lambda: False)
        except BaseException:
            tm.close(root)
            raise
        view.ask = mock.Mock(return_value=None)        # (a dialog would be a new window: never in tests)
        view.ask_text = mock.Mock(return_value=None)
        self.addCleanup(gc.collect)
        self.addCleanup(tm.close, root, view)
        self.addCleanup(lambda: view.mp.session and view.mp.session.close())
        root.update()
        self.assertEqual(root.state(), "withdrawn")
        return root, view

    def pump(self, root, view, cond, timeout=5.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            view.ctl.poll()
            if view.mp.tick():
                view.render()
            root.update()
            if cond():
                return True
            time.sleep(0.01)
        return False

    def test_home_has_multiplayer_only_with_ghost(self):
        root, view = self.open(ghost_bundled=False)
        self.assertNotIn(view.by_action["multiplayer"].ring, view.flow.items)
        root2, view2 = self.open()
        self.assertIn(view2.by_action["multiplayer"].ring, view2.flow.items)

    def test_turn_on_host_and_stop(self):
        root, view = self.open()
        view.by_action["multiplayer"].invoke()
        root.update()
        self.assertEqual(view.screen, "multiplayer")
        page = view.frames["multiplayer"]
        shown = texts(page)
        for want in ("Ghost multiplayer", "OFF", "Your name", "Who am I", "Link", "Story together", mpp.STORY_ON,
                     "Room code", mpp.COPY_NOTE):
            self.assertIn(want, shown)
        b = buttons(page)
        self.assertEqual(str(b["Host"].cget("state")), "disabled")      # (Ghost multiplayer is off)
        b["Turn on"].invoke()                                              # (as the picker does: added, then on)
        self.assertTrue(self.pump(root, view, lambda: "Host" in buttons(page)
                                  and str(buttons(page)["Host"].cget("state")) == "normal"))
        self.assertTrue(any(r.id == mpp.GHOST_ID and r.on for r in view.ctl.listing.rows))
        self.assertIn("Ghost multiplayer is on.", texts(page))
        # (the session's tools and the copy are stand-ins; Play is the launcher's own, on the fake modman)
        spawn, world = Spawn(), self.tmp / "lib" / "multiplayer" / "worlds" / "Anas_world"
        ctl = view.ctl
        view.mp.session = mpp.MpSession(ctl.backend.root, ctl.backend.install, python="py", spawn=spawn,
                                        start_game=lambda env: view.ctl.launch(env=env),
                                        host_prep=lambda *a: world)
        view.mp.render()
        view.ask_text.return_value = "Ana"          # (the first Host asks for a name: never the Windows account's)
        buttons(page)["Host"].invoke()
        self.assertEqual(view.ask_text.call_args[0][0], "Your name")
        self.assertTrue(view.mp.prefs()["named"])
        self.assertTrue(self.pump(root, view, lambda: spawn.get("coop_agent.py") is not None))
        self.assertIn("Stop", buttons(page))
        spawn.get("coop_agent.py").say("Hosting Ana's world on port 7788. Check words: apple river stone moon")
        # (the first Host on this PC: Windows' question about Python comes before the game, which would hide it)
        self.assertTrue(self.pump(root, view, lambda: "Start the game" in buttons(page)))
        self.assertEqual(self.mm.envs, [])
        self.assertIn(mpp.FIREWALL_ASK, "\n".join(texts(page)))
        buttons(page)["Start the game"].invoke()
        self.assertTrue(view.mp.prefs()["firewall"])                       # (once: the next Host doesn't wait)
        self.assertTrue(self.pump(root, view, lambda: view.mp.session.mode == "live"))
        self.assertEqual(self.mm.envs[-1], (["--port", "7777"], {"SAVAGEWIND_SAVE_DIR": str(world)}))
        self.assertIn("You're hosting Ana's world.", texts(page))
        self.assertTrue(json.loads((self.tmp / "home" / la.SETTINGS_FILE).read_text(encoding="utf-8"))
                        ["multiplayer"]["name"] == "Ana")
        buttons(page)["Stop"].invoke()
        self.assertTrue(self.pump(root, view, lambda: view.mp.session.mode == "idle"))
        self.assertIn("Multiplayer stopped.", texts(page))
        self.assertIn("Host", buttons(page))
        view.back()
        self.assertEqual(view.screen, "home")
        self.assertEqual(root.state(), "withdrawn")

    def test_join_list_and_story_question(self):
        root, view = self.open()
        view.mp.show()
        page = view.frames["multiplayer"]
        buttons(page)["Turn on"].invoke()
        self.assertTrue(self.pump(root, view, lambda: "Join" in buttons(page)
                                  and str(buttons(page)["Join"].cget("state")) == "normal"))
        spawn, copy = Spawn(), self.tmp / "lib" / "multiplayer" / "joined" / "x"
        ctl = view.ctl
        view.mp.session = mpp.MpSession(ctl.backend.root, ctl.backend.install, python="py", spawn=spawn,
                                        start_game=lambda env: view.ctl.launch(env=env),
                                        join_prep=lambda *a: copy, search=lambda s: [dict(HOST, code=True)])
        view.mp.save_prefs(name="Ben")
        view.mp.render()
        buttons(page)["Join"].invoke()
        world = "Ana's world  -  1 of 8 players  -  room code"
        self.assertTrue(self.pump(root, view, lambda: world in buttons(page)))
        self.assertIn("Check words (they must match the host's screen): Ana's world: apple river stone moon",
                      texts(page))
        view.ask_text.return_value = "Apple River Stone"
        view.ask.return_value = False                                     # ("No": time only)
        buttons(page)[world].invoke()
        q = view.ask.call_args[0]
        self.assertEqual(q[1], "Share story progress with Ana?")
        self.assertTrue(self.pump(root, view, lambda: spawn.get("coop_agent.py") is not None))
        argv = spawn.get("coop_agent.py").argv
        self.assertIn("--no", argv)
        self.assertEqual(argv[argv.index("--code") + 1], "apple river stone")
        self.assertFalse(view.mp.session.finding)                          # (the Join list stopped looking)
        view.mp.session.stop()
        self.assertTrue(self.pump(root, view, lambda: view.mp.session.mode == "idle"))

    def test_join_list_says_what_to_check_when_it_finds_nothing(self):
        root, view = self.open()
        view.mp.show()
        page = view.frames["multiplayer"]
        buttons(page)["Turn on"].invoke()
        self.assertTrue(self.pump(root, view, lambda: "Join" in buttons(page)
                                  and str(buttons(page)["Join"].cget("state")) == "normal"))
        ctl = view.ctl
        view.mp.session = mpp.MpSession(ctl.backend.root, ctl.backend.install, python="py", spawn=Spawn(),
                                        search=lambda s: time.sleep(0.02) or [])
        view.mp.save_prefs(name="Ben")
        view.mp.render()
        buttons(page)["Join"].invoke()
        self.assertTrue(view.mp.session.finding)
        hint = lambda: [x for x in texts(page) if "Nothing yet?" in x]   # noqa: E731
        with mock.patch.object(mpp, "QUIET_HINT_AFTER", 0.3):
            end = time.monotonic() + 5
            while time.monotonic() < end and not hint():
                root.update()      # (only the window's own tick, LauncherView.tick: nothing else changes meanwhile)
                time.sleep(0.02)
        self.assertTrue(hint())
        self.assertIn("On both PCs, Windows Firewall allows Python", hint()[0])   # (the joiner's own PC too)
        view.back()
        self.assertFalse(view.mp.session.finding)

    def test_who_am_i(self):
        root, view = self.open()
        view.mp.show()
        view.mp.show_who()
        root.update()
        page = view.frames["who"]
        b = buttons(page)
        names = [k.split("\n")[0] for k in b if "\n" in k]
        self.assertEqual(names, ["Link", "Linkle", "Zelda", "Hylian", "Paya", "Urbosa", "Daruk", "Revali", "Mipha",
                                 "Bokoblin", "Moblin"])
        self.assertIn("Build the characters", b)
        self.assertIn("Link\nStand-in", b)                         # (Plus doesn't build the Link body yet)
        shown = "\n".join(texts(page))
        self.assertIn("You picked Link.", shown)
        self.assertIn("Friends see a stand-in figure for now", shown)
        self.assertNotIn("see you as Link", shown)                 # (never promised)
        b["Zelda\nBuild first"].invoke()
        root.update()
        self.assertGreater(view.need(900), 0)          # (the window can size itself: no row of the old page left)
        view.apply_size()                              # (and its text size: only buttons that are there)
        self.assertEqual(charsel.load_choice()["look"], "zelda")
        shown = "\n".join(texts(page))
        self.assertIn("You picked Zelda.", shown)
        # (what friends see comes from THEIR built characters: never "your friends see you as" from this PC)
        self.assertIn("Friends see it if their PC built the characters too (each PC builds its own).", shown)
        self.assertIn("It is used from your next Host or Join.", shown)
        self.assertNotIn("[G]", shown)
        buttons(page)["Build the characters"].invoke()                    # (the recipe: charsel.py build --add)
        self.assertTrue(self.pump(root, view, lambda: self.spawn.calls))
        argv = self.spawn.calls[-1][0]
        self.assertEqual(os.path.basename(argv[2]), "charsel.py")
        self.assertEqual(argv[3:], ["build", "--add", "--library", view.ctl.backend.root])
        view.back()
        self.assertEqual(view.screen, "multiplayer")
        self.assertEqual(root.state(), "withdrawn")

    def test_who_am_i_says_why_a_build_failed(self):
        root, view = self.open()

        def cmd_build(*opts):   # (charsel's real words when the game files aren't set up)
            print(r"charsel: Z:\SW\extracted\romfs isn't the game's extracted romfs (no Actor\Pack)")
            return 1

        self.mm.cmd_build = cmd_build
        view.mp.show()
        view.mp.show_who()
        root.update()
        page = view.frames["who"]
        buttons(page)["Build the characters"].invoke()
        self.assertTrue(self.pump(root, view, lambda: view.mp.who_error is not None and "Details" in buttons(page)))
        shown = "\n".join(texts(page))
        self.assertIn("The characters couldn't be built.", shown)
        self.assertIn("Your game files aren't set up: open Savage Wind and press Set up game files.", shown)
        self.assertNotIn("The others are ready", shown)            # (one mod: the launcher's words for many)
        self.assertNotIn("Actor\\Pack", shown)
        self.assertIn("Details", buttons(page))                     # (never pressed here: it opens a window)
        self.assertEqual(root.state(), "withdrawn")


# ---------------------------------------------------------------- the command-line parts the page uses

class Glue(Temp):
    def test_client_stops_when_stdin_says_so(self):
        stopped = threading.Event()
        runner = types.SimpleNamespace(_stop=None, stop=stopped.set)
        t = client.stop_on_stdin(runner, io.StringIO("stop\n"))
        time.sleep(0.1)
        self.assertFalse(stopped.is_set())            # (run() hasn't started: it waits for it)
        runner._stop = object()
        t.join(2)
        self.assertTrue(stopped.is_set())
        stopped.clear()
        runner._stop = object()
        client.stop_on_stdin(runner, io.StringIO("")).join(2)    # (the pipe closed: stop too)
        self.assertTrue(stopped.is_set())

    def test_client_parses_the_pages_arguments(self):
        seen = {}

        class Stop(BaseException):
            pass

        def runner(*a, **k):
            seen.update(k)
            raise Stop

        with mock.patch.object(client, "Runner", runner), mock.patch.object(charsel, "here_catalog",
                                                                            return_value=({}, "x")) as cat, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(Stop):
                client.main(["--relay", "127.0.0.1:7788", "--name", "Ana", "--no-ui", "--ghost-looks",
                             "--game-port", "7777", "--stdin", "--library", str(self.tmp / "lib")])
        self.assertEqual(cat.call_args, mock.call(None, library=str(self.tmp / "lib")))   # (--library's catalog)

    def agent_args(self):
        return types.SimpleNamespace(library=self.tmp / "mp", name="Ben", dlc=False, code=None, port=7788,
                                     share=True, same_pc=False, save_dir=self.tmp / "copy", stdin=True,
                                     game_port=None, list=None, friends_story=False)

    def test_join_says_ready_and_ends_on_stdin(self):
        got = {}

        async def fake_join(library, name, address, save_dir, sl, **kw):
            got.update(kw, address=address)
            kw["before_game"]()
            await kw["stop"].wait()
            return {"error": None}

        out = io.StringIO()
        with mock.patch.object(coop_agent, "join_session", fake_join), \
                mock.patch.object(sys, "stdin", io.StringIO("end\n")), contextlib.redirect_stdout(out):
            res = asyncio.run(asyncio.wait_for(coop_agent._run_join(self.agent_args(), None, None, "10.0.0.2",
                                                                    "apple river stone moon"), 5))
        self.assertEqual(res, {"error": None})
        self.assertEqual(got["check"], "apple river stone moon")
        self.assertEqual(got["address"], "10.0.0.2")
        self.assertTrue(out.getvalue().startswith(coop_agent.READY))

    def test_host_ends_when_its_pipe_closes(self):
        async def fake_host(library, name, game, sl, *, commands, stop, **kw):
            while True:
                text = await commands.get()
                if coop_agent.host_command(text, mock.Mock(), stop) == "Ending the session.":
                    return {"ended": True}

        with mock.patch.object(coop_agent, "host_session", fake_host), \
                mock.patch.object(sys, "stdin", io.StringIO("")), contextlib.redirect_stdout(io.StringIO()):
            res = asyncio.run(asyncio.wait_for(coop_agent._run_host(self.agent_args(), None, None), 5))
        self.assertEqual(res, {"ended": True})

    def test_agent_takes_the_pages_arguments(self):
        lst = write(self.tmp / "mp" / "mp" / story.LIST_FILE, "base MapTower_01\n")
        seen = {}

        def run(coro):
            seen["code"] = coro.cr_code.co_name
            coro.close()
            return {"error": None}

        with mock.patch.object(coop_agent.asyncio, "run", run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(coop_agent.main(["join", "--name", "Ben", "--address", "10.0.0.2", "--port", "7788",
                                              "--save-dir", str(self.tmp / "copy"), "--yes", "--game-port", "7777",
                                              "--stdin", "--check", "apple river stone moon",
                                              "--library", str(self.tmp / "mp")]), 0)
            self.assertEqual(seen["code"], "_run_join")
            self.assertEqual(coop_agent.main(["host", "--name", "Ana", "--game-port", "7777", "--stdin",
                                              "--story-from", str(self.tmp / "world"),
                                              "--library", str(self.tmp / "mp")]), 0)
            self.assertEqual(seen["code"], "_run_host")
        self.assertTrue(lst.is_file())


class CharactersRecipe(Temp):
    """charsel build --add: the bundled Characters recipe (the launcher puts --library last)."""

    def setUp(self):
        super().setUp()
        root = self.tmp / "lib"
        (root / "mods").mkdir(parents=True)
        lib = types.SimpleNamespace(root=str(root), mods_dir=str(root / "mods"), install=str(self.tmp / "SW"),
                                    mods=[{"id": "a", "name": "A", "enabled": True},
                                          {"id": mpp.GHOST_ID, "name": "Ghost", "enabled": True}], saves=0)
        lib.save = lambda: setattr(lib, "saves", lib.saves + 1)
        self.lib, self.added = lib, []

        def add_mod(lib_, src, *a, **k):
            self.added.append((Path(src) / "mod.toml").read_text(encoding="utf-8"))
            lib_.mods.append({"id": charsel.MOD_ID, "name": "Characters", "enabled": False})
            return lib_.mods[-1], None, []

        def remove_mod(lib_, mid):
            lib_.mods[:] = [m for m in lib_.mods if m["id"] != mid]

        def build(romfs, out, ids, **kw):
            self.built = (Path(romfs), kw)
            write(Path(out) / "mod.toml", charsel.MOD_TOML)
            return {"built": {"zelda": {}, "goron": {}}, "skipped": {"moblin": "no"}, "kept": [], "notes": [],
                    "linkle": {"how": "missing"}}

        mm = types.SimpleNamespace(add_mod=add_mod, remove_mod=remove_mod, ModmanError=RuntimeError)
        for p in (mock.patch.dict(sys.modules, {"modman": mm}),
                  mock.patch.object(charsel, "_open_library", lambda library: lib),
                  mock.patch.object(charsel, "build", build), mock.patch.object(charsel, "_low_priority", lambda: 0)):
            p.start()
            self.addCleanup(p.stop)

    def test_build_add(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(charsel.main(["build", "--add", "--library", self.lib.root]), 0)
        romfs, kw = self.built
        self.assertEqual(romfs, self.tmp / "SW" / "extracted" / "romfs")   # (the library's own Savage Wind)
        self.assertEqual(kw["library"], self.lib.root)
        self.assertIn('part_of = "botw-mod-loader.ghost"', self.added[0])  # (assets/characters/mod.toml)
        self.assertEqual([m["id"] for m in self.lib.mods], ["a", mpp.GHOST_ID, charsel.MOD_ID])
        self.assertTrue(self.lib.mods[-1]["enabled"])
        self.assertIn("Built 2 characters: Zelda, Daruk. Not built: moblin.", out.getvalue())
        self.lib.mods[-1]["enabled"] = False                                # (turned off, then built again)
        self.lib.mods.insert(0, self.lib.mods.pop())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(charsel.main(["build", "--add", "--library", self.lib.root]), 0)
        self.assertEqual([m["id"] for m in self.lib.mods], [charsel.MOD_ID, "a", mpp.GHOST_ID])   # (its place kept)
        self.assertFalse(self.lib.mods[0]["enabled"])                                              # (and its OFF)

    def test_the_recipe_toml_matches_the_list(self):
        import tomllib
        man = tomllib.loads(charsel.RECIPE_TOML.read_text(encoding="utf-8"))["mod"]
        self.assertEqual(man["id"], charsel.MOD_ID)
        self.assertEqual(man["part_of"], mpp.GHOST_ID)
        bundled = tomllib.loads((Path(mpp.TOOLS) / "packaging" / "bundled-mods.toml").read_text(encoding="utf-8"))
        chars = next(m for m in bundled["mod"] if m["id"] == charsel.MOD_ID)
        self.assertEqual((chars["kind"], chars["folder"], chars["build"]),
                         ("recipe", "assets/characters", [["charsel.py", "build", "--add"]]))
        self.assertEqual(chars["description"], man["description"])


if __name__ == "__main__":
    unittest.main()
