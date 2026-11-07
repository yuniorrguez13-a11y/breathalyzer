"""Tests for tools/modman_gui.py. The logic runs against a fake modman (a made-up library in memory); one test runs
the real modman on a made-up library in a temp folder (add, turn off, move, remove: never deploy or run); one opens
the window with Tk and closes it (skipped without a display)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modman  # noqa: E402
import modman_gui as gui  # noqa: E402

ROOT = os.path.abspath("made-up-library")
INSTALL = os.path.abspath("made-up-savage-wind")
MODS =[{"id": "a", "name": "Alpha", "enabled": True, "kinds": ["romfs"], "version": "1.0"},
        {"id": "b", "name": "Bravo", "enabled": False, "kinds": ["cheats", "native"]},
        {"id": "c", "name": "Charlie", "enabled": True, "kinds": []}]
DEPLOY_OUT = ["Deployed 2 mod(s):", "  RomFS overlay: 3 file(s) from 1 mod(s)", "First deploy.", "Needs attention:",
              "  - Alpha: not used: an exlaunch code part", "Notes:", "  - Charlie: skipped 1 patch(es)",
              "Start the game with: python tools/modman.py run"]


class FakeModman:
    """Stands in for tools/modman.py: a library in memory, and commands that print what modman prints."""

    class ModmanError(Exception):
        pass

    def __init__(self, mods=MODS):
        self.mods = [dict(m) for m in mods]
        self.exists = True
        self.install = INSTALL      # the library's Savage Wind folder
        self.installs = {INSTALL}   # the folders that have savagewind.exe
        self.subprocess = subprocess
        self.CHEATS_PY = None       # a made-up cheats.py: run starts it as modman.run starts the real one
        self.broken = None
        self.deployed = None        # the inputs a deploy was made from
        self.deploy_error = None
        self.doctor = (0, ["OK   Savage Wind: made up", "--   modhost not built"])
        self.running = False        # the game
        self.port_busy = False      # another program has port 7777
        self.play_gate = None       # run waits on it once the game "started"
        self.calls = []
        self.Library = SimpleNamespace(load=self.load)

    def resolve_library(self, arg):
        return os.path.abspath(arg) if arg else ROOT

    def load(self, root):
        if self.broken:
            raise self.ModmanError(self.broken)
        return SimpleNamespace(mods=[dict(m) for m in self.mods], install=self.install) if self.exists else None

    def default_install(self):
        return INSTALL

    def looks_like_install(self, path):
        return path in self.installs

    def read_launch(self, lib):
        if self.deployed is None:
            raise self.ModmanError("Nothing deployed yet")
        return {"hash": self.deployed}

    def missing_paths(self, launch):
        return []

    def inputs_hash(self, lib):
        return json.dumps([(m["id"], m["enabled"]) for m in self.mods])

    def main(self, argv):
        self.calls.append(list(argv))
        assert argv[-2] == "--library", argv
        cmd, *rest = argv[:-2]
        return getattr(self, "cmd_" + cmd)(*rest)

    def cmd_enable(self, ref, on=True):
        m = self.mods[int(ref) - 1]
        m["enabled"] = on
        print(f"{m['id']} is {'on' if on else 'off'}. Run deploy to apply.")
        return 0

    def cmd_disable(self, ref):
        return self.cmd_enable(ref, False)

    def cmd_move(self, ref, pos):
        m = self.mods.pop(int(ref) - 1)
        self.mods.insert(int(pos) - 1, m)
        print(f"{m['id']} is now #{pos}. Run deploy to apply.")
        print(f"{len(self.mods)} mod(s), 2 on. Later mods win.")
        return 0

    def cmd_add(self, path):
        name = os.path.basename(path)
        if "bad" in name:
            print(f"Error: Not added: {name} has nothing Savage Wind can use.")
            print("  - no RomFS files")
            print("  - a Wii U mod")
            return 2
        mod_id = name.lower().replace(" ", "-")
        self.mods.append({"id": mod_id, "name": name, "enabled": True, "kinds": ["romfs"]})
        print(f"Added #{len(self.mods)} {mod_id} (romfs): {name}")
        print("  3 file(s) linked")
        if "part" in name:
            print("  not used: an exlaunch code part")
        print("Run deploy to use it.")
        return 0

    def cmd_remove(self, ref):
        m = self.mods.pop(int(ref) - 1)
        print(f"Removed {m['id']} ({m['name']}) from the library.")
        print("Run deploy to take it out of the game.")
        return 0

    def cmd_deploy(self):
        if self.deploy_error:
            for line in self.deploy_error:
                print(line)
            return 2
        for line in DEPLOY_OUT:
            print(line)
        self.deployed = self.inputs_hash(None)
        return 0

    def cmd_doctor(self):
        for line in self.doctor[1]:
            print(line)
        return self.doctor[0]

    def cmd_init(self, install):
        if not self.looks_like_install(install):
            print(f"Error: No savagewind.exe in {install}. Pass your Savage Wind folder: python tools/modman.py init "
                  "<folder>")
            return 2
        self.install, self.exists = install, True
        print(f"Mod library: {ROOT}")
        print(f"Savage Wind: {install}")
        print("Set up the game files in Savage Wind before you deploy.")
        print("Next: python tools/modman.py add <mod folder or .zip>")
        return 0

    def cmd_run(self, *opts):
        if self.running:
            print("Error: Savage Wind is already running. Close it first.")
            return 2
        port = opts[1] if opts[:1] == ("--port",) else "7777"
        if port == "7777" and self.port_busy:
            print("Error: Another program already uses port 7777. Pick a free one with --port.")
            return 2
        print(f"Started Savage Wind (pid 4321) with 2 mod(s). Control port {port}.")
        print("  Cheats: 2 cheat(s) from 1 mod(s), all turned on")
        print(f"Waiting for the game to open port {port} for the cheats...")
        if self.CHEATS_PY:  # (as modman.run does it)
            helper = self.subprocess.Popen([sys.executable, "-I", self.CHEATS_PY, "run", "--port", port],
                                           env=dict(os.environ))
            for _ in range(40):
                if helper.poll() is not None:
                    break
                time.sleep(0.05)
            if helper.poll() is not None:
                print(f"The cheats stopped right away (exit code {helper.returncode}); see the lines above. Savage "
                      "Wind keeps running without them.")
                return 1
            helper.kill()
            helper.wait()
        print(f"Started cheats (pid 4322) on port {port}; they stop when the game closes.")
        if self.play_gate is not None:
            self.play_gate.wait(10)
        print("Savage Wind closed.")
        return 0


class FakeProc:
    def __init__(self, lines, code, gate):
        self.lines, self.code, self.gate = lines, code, gate
        self.pid = 999
        self.returncode = None
        self.stdout = self._out()

    def _out(self):
        for i, line in enumerate(self.lines):
            if self.gate is not None and i == len(self.lines) - 1:
                self.gate.wait(10)  # (the last line comes when the test lets it)
            yield (line + "\r\n").encode("utf-8")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = self.code
        return self.code

    def kill(self):
        self.returncode = -9


class FakeSpawn:
    """Stands in for subprocess.Popen of `python -u tools/modman.py <args>`: runs the fake modman's main()."""

    def __init__(self, mm, gate=None):
        self.mm, self.gate = mm, gate
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        lines = []
        with gui.captured(lines.append):
            code = self.mm.main(argv[3:])
        return FakeProc(lines, code, self.gate)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def close(root, view=None):
    """Destroy a test window without its timers (they would fire in the next test's window)."""
    if view is not None:
        view.closed = True
    for after in root.tk.splitlist(root.tk.call("after", "info")):
        root.after_cancel(after)
    root.destroy()


def wait_for(ctl, cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        ctl.poll()
        if cond():
            return True
        time.sleep(0.01)
    return False


class Base(unittest.TestCase):
    def make(self, mods=MODS, gate=None, **attrs):
        self.mm = FakeModman(mods)
        for k, v in attrs.items():
            setattr(self.mm, k, v)
        self.spawn = FakeSpawn(self.mm, gate)
        self.clock = Clock()
        self.ctl = gui.Controller(gui.Backend(None, mm=self.mm, spawn=self.spawn, python="py"), clock=self.clock)
        return self.ctl

    def head(self):
        return self.ctl.status.lines[0]


class Listing(Base):
    def test_rows(self):
        ctl = self.make()
        rows = ctl.listing.rows
        self.assertEqual([r.cells() for r in rows], [("1", "On", "Alpha", "RomFS", "1.0"),
                                                      ("2", "Off", "Bravo", "Cheats, Code", "-"),
                                                      ("3", "On", "Charlie", "-", "-")])
        self.assertEqual(ctl.listing.root, ROOT)
        self.assertEqual(self.head(), "Ready.")
        self.assertEqual(ctl.summary_line(), "3 mods, 2 on (later ones win).")

    def test_no_library_yet(self):
        ctl = self.make(exists=False)
        self.assertEqual(ctl.status.lines[:2], ["No mod library yet.", "Press Add mod to start one."])
        self.assertEqual(ctl.summary_line(), "No mod library yet.")

    def test_damaged_library(self):
        ctl = self.make(broken="config.json is damaged")
        self.assertEqual(ctl.status.kind, "error")
        self.assertEqual(ctl.status.lines, ["Can't read the mod list.", "config.json is damaged"])

    def test_deploy_state(self):
        ctl = self.make()
        ctl.refresh_state()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.summary_line(), "3 mods, 2 on (later ones win). Not deployed yet.")
        ctl.deploy()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.state_text, "Deploy is up to date.")
        ctl.toggle("a")
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.state_text, "Changes not deployed yet: press Deploy.")


class QuickCommands(Base):
    def test_toggle(self):
        ctl = self.make()
        v = ctl.list_version
        ctl.toggle("a")
        self.assertEqual(self.mm.calls[-1], ["disable", "1", "--library", ROOT])
        self.assertEqual(ctl.status.lines, ["Alpha is off.", gui.PRESS_DEPLOY])
        self.assertFalse(ctl.row("a").on)
        self.assertGreater(ctl.list_version, v)
        self.assertEqual(ctl.take_select(), "a")
        self.assertIsNone(ctl.take_select())
        ctl.toggle("b")
        self.assertEqual(self.mm.calls[-1], ["enable", "2", "--library", ROOT])
        self.assertEqual(self.head(), "Bravo is on.")
        self.assertEqual(ctl.details_text()[:2], [f"modman enable 2   (library: {ROOT})",
                                                  "b is on. Run deploy to apply."])

    def test_numbers_are_read_again(self):
        """The list on disk changed since it was shown (modman in another window): the right mod still changes."""
        ctl = self.make()
        self.mm.mods.insert(0, self.mm.mods.pop())  # (now Charlie, Alpha, Bravo)
        ctl.toggle("a")
        self.assertEqual(self.mm.calls[-1], ["disable", "2", "--library", ROOT])
        self.assertEqual([r.id for r in ctl.listing.rows], ["c", "a", "b"])

    def test_gone_and_nothing_picked(self):
        ctl = self.make()
        ctl.toggle(None)
        self.assertEqual(self.head(), "Pick a mod in the list first.")
        self.mm.mods.pop(0)
        ctl.toggle("a")
        self.assertEqual(self.head(), "That mod isn't in the list any more.")
        self.assertEqual(self.mm.calls, [])

    def test_move(self):
        ctl = self.make()
        ctl.move("a", -1)
        self.assertEqual(self.head(), "Alpha is already first.")
        ctl.move("c", 1)
        self.assertEqual(self.head(), "Charlie is already last.")
        self.assertEqual(self.mm.calls, [])
        ctl.move("a", 1)
        self.assertEqual(self.mm.calls[-1], ["move", "1", "2", "--library", ROOT])
        self.assertEqual(ctl.status.lines, ["Alpha is now number 2.", gui.PRESS_DEPLOY])
        self.assertEqual([r.id for r in ctl.listing.rows], ["b", "a", "c"])
        self.assertEqual(ctl.take_select(), "a")
        ctl.move("c", -1)
        self.assertEqual(self.mm.calls[-1], ["move", "3", "2", "--library", ROOT])

    def test_unexpected_error_is_shown(self):
        ctl = self.make()
        with mock.patch.object(self.mm, "cmd_enable", side_effect=RuntimeError("boom")):
            ctl.toggle("b")
        self.assertEqual(ctl.status.kind, "error")
        self.assertEqual(ctl.status.lines, ["Not changed.", "RuntimeError: boom"])


class BackgroundCommands(Base):
    def test_add(self):
        ctl = self.make()
        path = os.path.join("D:" + os.sep, "Downloads", "Better Sprint")
        ctl.add(path)
        self.assertEqual(self.head(), "Adding Better Sprint...")
        self.assertTrue(ctl.settle())
        argv, kw = self.spawn.calls[-1]
        self.assertEqual(argv, ["py", "-u", gui.MODMAN_PY, "add", path, "--library", ROOT])
        self.assertEqual((kw["stdin"], kw["stdout"], kw["stderr"]),
                         (subprocess.DEVNULL, subprocess.PIPE, subprocess.STDOUT))
        self.assertEqual(kw["env"]["PYTHONIOENCODING"], "utf-8")  # (else the pipe gets the ANSI code page)
        self.assertEqual(ctl.status.kind, "ok")
        self.assertEqual(ctl.status.lines, ["Added Better Sprint.", "Press Deploy to use it."])
        self.assertEqual(ctl.take_select(), "better-sprint")
        self.assertEqual(len(ctl.listing.rows), 4)
        self.assertIn("Run deploy to use it.", ctl.details_text())
        self.assertTrue(ctl.details_text()[-1].startswith("(finished after 0:00, exit code 0)"))

    def test_add_wiiu_mod_with_leftover_wiiu_data_warns(self):
        out = ["Converting Armor Mod from Wii U...", "Added #1 Armor Mod (romfs)",
               "From Wii U: 1 of 2 game file(s) converted.",
               "Left as Wii U data: 1 (model). The game can't read these: it may crash or look wrong where the mod "
               "uses them.", "Report: C:\\lib\\mods\\armor-mod\\wiiu-convert-report.txt"]
        st = gui.summarize(gui.Job("add", ["add", "Armor Mod"], name="Armor Mod", lines=out, code=0))
        self.assertEqual(st.kind, "warn")
        self.assertEqual(st.lines, ["Added Armor Mod.",
                                    "Some files are still Wii U data: the game may crash. See Details.",
                                    "Press Deploy to use it."])
        done = [x for x in out if not x.startswith("Left as")]  # everything converted: no warning
        st = gui.summarize(gui.Job("add", ["add", "Armor Mod"], name="Armor Mod", lines=done, code=0))
        self.assertEqual((st.kind, st.lines), ("ok", ["Added Armor Mod.", "Press Deploy to use it."]))

    def test_add_with_unused_part_and_refused(self):
        ctl = self.make()
        ctl.add("Mod with part")
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.kind, "warn")
        self.assertEqual(ctl.status.lines[1], "1 part of it can't be used: see Details.")
        ctl.add("bad.zip")
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.kind, "error")
        self.assertEqual(ctl.status.lines, ["Not added.", "bad.zip has nothing Savage Wind can use.",
                                            "no RomFS files (and 1 more: see Details)"])

    def test_remove_picks_the_next_one(self):
        ctl = self.make()
        ctl.remove("b")
        self.assertTrue(ctl.settle())
        self.assertEqual(self.spawn.calls[-1][0][3:5], ["remove", "2"])
        self.assertEqual(ctl.status.lines, ["Removed Bravo.", "Only the library copy was deleted.",
                                            "Press Deploy to take it out of the game."])
        self.assertEqual(ctl.take_select(), "c")
        ctl.remove("c")
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.take_select(), "a")

    def test_deploy_runs_in_the_background(self):
        gate = threading.Event()
        ctl = self.make(gate=gate)
        ctl.deploy()
        self.assertEqual(ctl.status.lines, ["Deploying...", gui.REBUILD_NOTE])
        self.assertTrue(wait_for(ctl, lambda: len(ctl.jobs["main"].lines) == len(DEPLOY_OUT) - 1))
        for action in ("add", "toggle", "move", "remove", "deploy", "play", "check"):
            self.assertFalse(ctl.can(action), action)
        self.assertTrue(ctl.can("details"))
        self.clock.now += 75
        self.assertEqual(ctl.progress(), ["Deploying... 1:15", "- Charlie: skipped 1 patch(es)"])
        calls = len(self.mm.calls)
        ctl.toggle("a")
        self.assertEqual(ctl.status.lines, ["Please wait until Deploy is done."])
        self.assertEqual(len(self.mm.calls), calls)
        gate.set()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.progress(), [])
        self.assertEqual(ctl.status.kind, "warn")
        self.assertEqual(ctl.status.lines, ["Deployed 2 mods.", "1 thing needs attention: see Details.",
                                            "Press Play to start the game."])
        self.assertEqual(ctl.details_text()[1:-1], DEPLOY_OUT)
        self.assertEqual(ctl.details_text()[-1], "(finished after 1:15, exit code 0)")
        self.assertTrue(ctl.can("toggle"))

    def test_deploy_refused(self):
        ctl = self.make(deploy_error=[
            "Error: Can't deploy (fix it, or turn the mod off with python tools/modman.py disable <id>):",
            "  - Alpha: needs Bravo, which is off"])
        ctl.deploy()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines, ["Not deployed.",
                                            "Can't deploy (fix it, or turn the mod off with the Turn off button):",
                                            "Alpha: needs Bravo, which is off"])

    def test_check(self):
        ctl = self.make()
        ctl.check()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines, ["Check: all good."])
        self.mm.doctor = (1, ["OK   Savage Wind: made up", "FAIL game files not set up: start Savage Wind and set "
                              "them up", "WARN 2.0 GB free on the library's drive (Z:)"])
        ctl.check()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.kind, "error")
        self.assertEqual(ctl.status.lines, ["Check: 1 problem.", "game files not set up: start Savage Wind and "
                                            "set them up", "See Details for the rest."])
        self.mm.doctor = (0, ["WARN game version unknown (build 0000)"])
        ctl.check()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines, ["Check: 1 warning.", "game version unknown (build 0000)"])

    def test_play(self):
        gate = threading.Event()
        ctl = self.make(play_gate=gate)
        ctl.play()
        self.assertTrue(wait_for(ctl, lambda: "Cheats are on." in ctl.status.lines))
        self.assertEqual(ctl.status.lines, ["Game started.", "Cheats are on."])
        self.assertEqual(ctl.progress(), ["The game is running."])
        self.assertFalse(ctl.can("deploy"))
        self.assertFalse(ctl.can("play"))
        self.assertTrue(ctl.can("toggle"))
        self.assertTrue(ctl.can("check"))
        ctl.deploy()
        self.assertEqual(ctl.status.lines, ["The game is running: close it first."])
        ctl.toggle("b")  # (in this thread, while run waits in its own)
        self.assertEqual(self.head(), "Bravo is on.")
        self.assertEqual(ctl.details_job.lines, ["b is on. Run deploy to apply."])
        gate.set()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines, ["The game closed."])
        play = next(c for c in self.mm.calls if c[0] == "run")
        self.assertEqual(play, ["run", "--library", ROOT])
        self.assertEqual(self.spawn.calls, [])  # (run is never a subprocess here: the game gets this console)
        self.assertTrue(ctl.can("play"))

    def test_play_refused(self):
        ctl = self.make(running=True)
        ctl.play()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.kind, "error")
        self.assertEqual(ctl.status.lines, ["The game didn't start.",
                                            "Savage Wind is already running. Close it first."])

    def test_cant_start_python(self):
        ctl = self.make()
        ctl.backend.spawn = mock.Mock(side_effect=OSError("not found"))
        ctl.check()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines, ["The check didn't finish.", "can't start py: not found"])

    def test_stop_kills_the_command(self):
        ctl = self.make()
        proc = mock.Mock(pid=4242)
        proc.poll.return_value = None
        ctl.jobs["main"] = gui.Job("deploy", ["deploy"], proc=proc)
        with mock.patch.object(gui.subprocess, "run") as run:
            ctl.stop()
        if sys.platform == "win32":
            self.assertEqual(run.call_args[0][0], ["taskkill", "/T", "/F", "/PID", "4242"])
        proc.kill.assert_called_once()


CHEATS_FAIL = """import sys
print("Can't start: the master code 'Made up' can't run: unknown opcode F at word 1.")
sys.exit(2)
"""


class CheatsOutput(Base):
    """modman.run starts cheats.py on the console; in the window, what it prints goes to Details."""

    def test_why_the_cheats_stopped_is_in_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "cheats.py")
            Path(script).write_text(CHEATS_FAIL, encoding="utf-8")
            ctl = self.make()
            self.mm.CHEATS_PY = script
            ctl.play()
            self.assertTrue(ctl.settle(60))
        why = "Can't start: the master code 'Made up' can't run: unknown opcode F at word 1."
        self.assertEqual(ctl.status.kind, "warn")
        self.assertEqual(ctl.status.lines, ["Game started.", "The cheats stopped right away: see Details.", why])
        text = ctl.details_text()
        self.assertIn(why, text)
        stopped = next(i for i, x in enumerate(text) if x.startswith("The cheats stopped right away"))
        self.assertLess(text.index(why), stopped)  # (before modman's "see the lines above", as in a console)
        self.assertIs(self.mm.subprocess, subprocess)  # (put back once run is done)

    def test_cheats_that_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "cheats.py")
            Path(script).write_text("import time\nprint('Started: Made up', flush=True)\ntime.sleep(30)\n",
                                    encoding="utf-8")
            ctl = self.make()
            self.mm.CHEATS_PY = script
            ctl.play()
            self.assertTrue(ctl.settle(60))
        self.assertEqual(ctl.status.lines, ["The game closed."])
        self.assertIn("Started: Made up", ctl.details_text())

    def test_other_programs_are_started_as_modman_asks(self):
        real = mock.Mock()
        piped = gui.PipedCheats(real, os.path.join("Z:" + os.sep, "tools", "cheats.py"), lambda line: None)
        piped.Popen(["Z:\\game\\savagewind.exe", "--x"], cwd="Z:\\game", env={"A": "1"}, creationflags=512)
        real.Popen.assert_called_once_with(["Z:\\game\\savagewind.exe", "--x"], cwd="Z:\\game", env={"A": "1"},
                                           creationflags=512)
        self.assertIs(piped.DEVNULL, real.DEVNULL)


class WhatToDoInTheWindow(Base):
    """modman's messages name commands and options; the window says what to do in it instead."""

    def test_plain(self):
        self.assertEqual(gui.plain("Another program already uses port 7777. Pick a free one with --port."),
                         "Another program already uses port 7777. Close it and press Play again, or start this "
                         "window with --port <number>.")
        self.assertEqual(gui.plain("Savage Wind isn't in Z:\\SW any more (no savagewind.exe). Reinstall it there, "
                                   "or run: python tools/modman.py init <Savage Wind folder>"),
                         "Savage Wind isn't in Z:\\SW any more (no savagewind.exe). Reinstall it there, or press the "
                         "button again to pick it")
        self.assertEqual(gui.plain("No mod library in Z:\\lib yet. Run: python tools/modman.py init"),
                         "No mod library in Z:\\lib yet. Press Add mod to start one")
        self.assertEqual(gui.plain("Savage Wind not found in Z:\\SW (run init <folder>)"),
                         "Savage Wind not found in Z:\\SW (Deploy asks for it)")
        for text in ("Savage Wind isn't in Z:\\SW. Run: python tools/modman.py init <Savage Wind folder>",
                     "No savagewind.exe in Z:\\x. Pass your Savage Wind folder: python tools/modman.py init <folder>",
                     "no mod library in Z:\\lib yet (run init)", "Z:\\SW\\savagewind.exe not found: run init and "
                     "deploy again."):
            self.assertNotIn("modman.py", gui.plain(text), text)
            self.assertNotIn("run init", gui.plain(text), text)

    def test_port_in_use(self):
        ctl = self.make(port_busy=True)
        ctl.play()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines, ["The game didn't start.", "Another program already uses port 7777. Close "
                                            "it and press Play again, or start this window with --port <number>."])
        ctl = gui.Controller(ctl.backend, port=8000)
        ctl.play()
        self.assertTrue(ctl.settle())
        self.assertEqual(self.mm.calls[-1], ["run", "--port", "8000", "--library", ROOT])
        self.assertEqual(ctl.status.lines[0], "The game closed.")
        self.assertEqual(gui.parser().parse_args(["--port", "8000"]).port, 8000)
        for bad in ("0", "65536", "x"):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                gui.parser().parse_args(["--port", bad])

    def test_first_run_with_savage_wind_elsewhere(self):
        ctl = self.make(exists=False, installs=set())
        self.assertEqual(ctl.install_problem(), INSTALL)
        self.assertEqual(ctl.status.lines, ["No mod library yet.", f"Savage Wind isn't in {INSTALL}.",
                                            "Press Add mod: it asks for the Savage Wind folder first."])
        other = os.path.abspath("made-up-games-folder")
        self.assertFalse(ctl.set_install(other))
        self.assertEqual(ctl.status.lines, ["The Savage Wind folder isn't set.",
                                            f"No savagewind.exe in {other}. Pick the folder that has it."])
        self.mm.installs.add(other)
        self.assertTrue(ctl.set_install(other))
        self.assertEqual(self.mm.calls[-1], ["init", other, "--library", ROOT])
        self.assertEqual(ctl.status.lines, [f"Savage Wind folder: {other}",
                                            "Set up the game files in Savage Wind before you deploy."])
        self.assertTrue(ctl.listing.exists)
        self.assertIsNone(ctl.install_problem())

    def test_savage_wind_moved(self):
        ctl = self.make(installs=set())
        self.assertEqual(ctl.status.kind, "warn")
        self.assertEqual(ctl.status.lines, ["Ready.", f"Savage Wind isn't in {INSTALL}.",
                                            "Deploy asks for the Savage Wind folder."])

    def test_buttons_ask_for_the_folder(self):
        if gui.tk is None:
            self.skipTest("no tkinter")
        try:
            root = gui.tk.Tk()
        except gui.tk.TclError as e:
            self.skipTest(f"no display: {e}")
        view = None
        try:
            root.withdraw()
            ctl = self.make(exists=False, installs=set())
            view = gui.View(root, ctl)
            other = os.path.abspath("made-up-games-folder")
            mod = os.path.abspath("Better Sprint")
            self.mm.installs.add(other)
            with mock.patch.object(view, "ask", side_effect=[True, "folder"]) as ask, \
                    mock.patch.object(gui.filedialog, "askdirectory", side_effect=[other, mod]):
                view.on_add()
            self.assertEqual(ask.call_args_list[0][0][1], "Where is Savage Wind?")
            self.assertEqual(self.mm.calls[0], ["init", other, "--library", ROOT])
            self.assertTrue(ctl.settle())
            self.assertEqual(self.spawn.calls[-1][0][3:5], ["add", mod])
            self.assertEqual(ctl.status.lines[0], "Added Better Sprint.")
            # (the folder is gone again: Deploy asks, and Cancel deploys nothing)
            self.mm.installs.clear()
            with mock.patch.object(view, "ask", return_value=False):
                view.on_deploy()
            self.assertEqual(len(self.spawn.calls), 1)
            self.assertNotIn("main", ctl.jobs)
        finally:
            close(root, view)


class Summaries(unittest.TestCase):
    def job(self, action, code, lines, args=None):
        return gui.Job(action, args or [action], lines=lines, code=code)

    def test_play_warnings(self):
        st = gui.summarize(self.job("play", 0, [
            "Warning: your mods or settings changed since the last deploy; this starts the last deployed set. Run "
            "deploy to use the changes.", "Started Savage Wind (pid 1) without mods."]))
        self.assertEqual(st.kind, "warn")
        self.assertEqual(st.lines, ["Game started.", "Your newer changes aren't in it: deploy first next time."])
        st = gui.summarize(self.job("play", 1, ["Started Savage Wind (pid 1) with 1 mod(s). Control port 7777.",
                                                "The cheats stopped right away (exit code 2); see the lines above."]))
        self.assertEqual(st.lines, ["Game started.", "The cheats stopped right away: see Details."])
        st = gui.summarize(self.job("play", 1, ["Started Savage Wind (pid 1) with 1 mod(s).",
                                                "The game closed before it opened its control port; cheats not "
                                                "started."]))
        self.assertEqual(st.lines, ["The game closed.", "It closed before the cheats could start."])
        self.assertEqual(gui.summarize(self.job("play", None, [])).lines, ["Starting the game..."])

    def test_deploy_without_mods(self):
        st = gui.summarize(self.job("deploy", 0, ["No mods are on: the game will start without mods.", "First deploy.",
                                                  "Start the game with: python tools/modman.py run"]))
        self.assertEqual(st.lines, ["Deployed: no mods are on.", "Press Play to start the game."])

    def test_failure_without_error_line(self):
        st = gui.summarize(self.job("deploy", 1, ["Traceback (most recent call last):", "ZeroDivisionError: x"]))
        self.assertEqual(st.lines, ["Not deployed.", "ZeroDivisionError: x"])
        self.assertEqual(gui.summarize(self.job("deploy", 2, [])).lines,
                         ["Not deployed.", "Something went wrong: see Details."])

    def test_plain_and_clip(self):
        self.assertEqual(gui.plain("Nothing deployed yet. Run: python tools/modman.py deploy"),
                         "Nothing deployed yet. Press Deploy")
        self.assertEqual(len(gui.plain("x" * 500)), 120)
        self.assertEqual(gui.mmss(59), "0:59")
        self.assertEqual(gui.mmss(3725), "1:02:05")


class Output(unittest.TestCase):
    def test_threads_get_their_own_lines(self):
        outside = io.StringIO()
        got = {"a": [], "b": []}
        both = threading.Barrier(2)

        def work(key):
            with gui.captured(got[key].append):
                both.wait()
                for i in range(50):
                    print(f"{key}{i}", end="" if i % 2 else "\n", flush=True)
                print("tail", end="")

        with contextlib.redirect_stdout(outside):
            threads = [threading.Thread(target=work, args=(k,)) for k in got]
            for t in threads:
                t.start()
            print("main thread")
            for t in threads:
                t.join()
        self.assertEqual(outside.getvalue(), "main thread\n")
        for key, lines in got.items():
            self.assertEqual(len(lines), 26)
            self.assertEqual(lines[0], f"{key}0")
            self.assertEqual(lines[1], f"{key}1{key}2")
            self.assertEqual(lines[-1], f"{key}49tail")

    def test_decode_line(self):
        self.assertEqual(gui.decode_line(b"caf\xc3\xa9\r\n"), "café")
        self.assertEqual(gui.decode_line(b"10%\r20%\r30%\n"), "30%")
        self.assertTrue(gui.decode_line(b"caf\xe9\n").startswith("caf"))  # (the ANSI code page: no error)


class CommandLine(unittest.TestCase):
    def test_options(self):
        a = gui.parser().parse_args(["--size", "24", "--dark", "--library", "x"])
        self.assertEqual((a.size, a.dark, a.library), (24, True, "x"))
        self.assertEqual(gui.parser().parse_args([]).size, gui.DEFAULT_SIZE)
        for bad in ("9", "41", "big"):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                gui.parser().parse_args(["--size", bad])

    def test_library_as_modman_picks_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            with mock.patch.dict(os.environ, {"USERPROFILE": home}):
                os.environ.pop(modman.ENV_LIBRARY, None)
                self.assertEqual(gui.Backend(None, mm=modman).root,
                                 os.path.join(home, "Documents", "SavageWind", "modloader"))
                os.environ[modman.ENV_LIBRARY] = os.path.join(tmp, "env-lib")
                self.assertEqual(gui.Backend(None, mm=modman).root, os.path.join(tmp, "env-lib"))
                self.assertEqual(gui.Backend(os.path.join(tmp, "arg-lib"), mm=modman).root,
                                 os.path.join(tmp, "arg-lib"))

    def test_console_python(self):
        with mock.patch.object(gui.sys, "executable", os.path.join("Z:" + os.sep, "Py", "pythonw.exe")), \
                mock.patch.object(gui.os.path, "isfile", return_value=True):
            self.assertEqual(gui.console_python(), os.path.join("Z:" + os.sep, "Py", "python.exe"))


class RealModman(unittest.TestCase):
    """The real tools/modman.py on a made-up library and Savage Wind folder in a temp folder."""

    def test_add_toggle_move_remove(self):
        from test_modman import fake_install, write
        with tempfile.TemporaryDirectory(prefix="modman-gui-test-") as tmp:
            tmp = Path(tmp)
            install = fake_install(tmp / "SavageWind" / "Savage Wind")
            libdir = tmp / "SavageWind" / "modloader"
            modman.Library(str(libdir), str(install)).save()
            sprint = write(tmp / "dl" / "Better Sprint", {"romfs/Actor/Pack/Hero.txt": "mod hero"})
            title = write(tmp / "dl" / "Title Mod", {"romfs/Pack/Title.txt": "mod title"})
            ctl = gui.Controller(gui.Backend(str(libdir), mm=modman))
            ctl.add(str(sprint))
            self.assertTrue(ctl.settle(120))
            self.assertEqual(ctl.status.lines, ["Added Better Sprint.", "Press Deploy to use it."], ctl.details_text())
            ctl.add(str(title))
            self.assertTrue(ctl.settle(120))
            self.assertEqual([(r.id, r.on) for r in ctl.listing.rows], [("better-sprint", True), ("title-mod", True)])
            self.assertEqual(ctl.state_text, "Not deployed yet.")
            ctl.toggle("better-sprint")
            self.assertEqual(ctl.status.lines, ["Better Sprint is off.", gui.PRESS_DEPLOY])
            ctl.move("title-mod", -1)
            self.assertEqual(ctl.status.lines[0], "Title Mod is now number 1.")
            ctl.remove("better-sprint")
            self.assertTrue(ctl.settle(120))
            self.assertEqual(ctl.status.lines[:2], ["Removed Better Sprint.", "Only the library copy was deleted."])
            lib = modman.Library.load(str(libdir))
            self.assertEqual([m["id"] for m in lib.mods], ["title-mod"])
            self.assertFalse((libdir / "mods" / "better-sprint").exists())
            self.assertTrue((sprint / "romfs" / "Actor" / "Pack" / "Hero.txt").is_file())  # (the source stays)

    def test_names_outside_the_code_page(self):
        """What `python tools/modman.py` prints reaches Details as it is, not as "?" (a pipe gets the ANSI code page
        unless told otherwise)."""
        from test_modman import fake_install, write
        name = "日本語 Mod Łódź"
        with tempfile.TemporaryDirectory(prefix="modman-gui-test-") as tmp:
            tmp = Path(tmp)
            install = fake_install(tmp / "SavageWind" / "Savage Wind")
            libdir = tmp / "SavageWind" / "modloader"
            modman.Library(str(libdir), str(install)).save()
            src = write(tmp / "dl" / name, {"romfs/Pack/Title.txt": "mod title"})
            ctl = gui.Controller(gui.Backend(str(libdir), mm=modman))
            ctl.add(str(src))
            self.assertTrue(ctl.settle(120))
            self.assertEqual(ctl.status.lines[0], f"Added {name}.", ctl.details_text())
            self.assertTrue(any(x.endswith(f": {name}") for x in ctl.details_text()), ctl.details_text())
            ctl.remove(ctl.listing.rows[0].id)
            self.assertTrue(ctl.settle(120))
            self.assertTrue(any(f"({name}) from the library." in x for x in ctl.details_text()), ctl.details_text())


EIGHT = [{"id": f"m{i}", "name": f"Made-up mod {i}", "enabled": True, "kinds": ["romfs"], "version": "1.0"}
         for i in range(1, 9)]


class Layout(Base):
    """The window on screen (see-through, so nothing shows): text that wraps, bigger text, Tab."""

    def window(self, size=18, mods=EIGHT, gate=None):
        if gui.tk is None:
            self.skipTest("no tkinter")
        try:
            root = gui.tk.Tk()
        except gui.tk.TclError as e:
            self.skipTest(f"no display: {e}")
        root.attributes("-alpha", 0.0)
        try:
            view = gui.View(root, self.make(mods, gate=gate), size=size)
        except BaseException:
            close(root)
            raise
        self.addCleanup(close, root, view)
        root.update()
        return root, view

    def full_rows(self, view):
        bottom = view.tree.winfo_height()
        boxes = [view.tree.bbox(k) for k in view.tree.get_children()]
        return sum(1 for b in boxes if b and b[1] + b[3] <= bottom)

    def check_fits(self, root, view, rows=gui.MIN_LIST_ROWS):
        where = f"{view.size} pt, {root.wm_geometry()}, details {view.details_shown}"
        bottom = root.winfo_rooty() + root.winfo_height()
        for action, b in view.by_action.items():
            self.assertLessEqual(b.ring.winfo_rooty() + b.ring.winfo_height(), bottom, f"{action}: {where}")
            self.assertLessEqual(b.ring.winfo_x() + b.ring.winfo_width(), view.flow.frame.winfo_width(),
                                 f"{action}: {where}")
        self.assertGreaterEqual(self.full_rows(view), rows, where)
        self.assertLessEqual(sum(int(view.tree.column(c, "width")) for c, _, _ in gui.COLUMNS),
                             view.tree.winfo_width(), where)  # (Version isn't cut off)
        self.assertLessEqual(root.winfo_width(), root.winfo_screenwidth(), where)
        self.assertLessEqual(root.winfo_height(), root.winfo_screenheight(), where)

    def test_status_lines_are_never_cut(self):
        root, view = self.window()
        line = view.font.metrics("linespace")
        self.assertGreaterEqual(view.status_box.grid_bbox(1, 1)[3], 2 * line)  # (two lines high when shorter)
        lines = ["Check: 2 problems.", "Savage Wind not found in C:\\Users\\Someone With A Long Name\\Documents\\"
                 "SavageWind\\Savage Wind (Deploy asks for it), and more words so it wraps",
                 "See Details for the rest."]
        self.ctl.status = gui.Status("error", lines)
        view.render()
        root.update()
        probe = gui.tk.Label(root, font=view.font, text="\n".join(lines[1:]), justify="left",
                             wraplength=view.status_more.cget("wraplength"))
        self.assertGreater(probe.winfo_reqheight(), 3 * line)  # (it does wrap)
        self.assertGreaterEqual(view.status_more.winfo_height(), probe.winfo_reqheight())
        view.set_size(24)
        root.update()
        self.assertEqual(int(view.status_box.grid_rowconfigure(1)["minsize"]), 2 * view.font.metrics("linespace"))

    def test_bigger_text_keeps_the_list_and_the_buttons(self):
        root, view = self.window()
        self.check_fits(root, view)
        for size in range(20, gui.MAX_SIZE + 2, 2):  # (Ctrl +)
            view.set_size(size)
            root.update()
            self.check_fits(root, view)
        if view.size < gui.MAX_SIZE:
            self.assertEqual(self.head(), "This is the biggest text that fits on this screen.")
        biggest = view.size
        view.on_details()
        root.update()
        if view.details_shown:
            self.check_fits(root, view)
        else:
            self.assertEqual(self.head(), "Details doesn't fit at this text size.")
        view.set_size(view.start_size)  # (Ctrl 0)
        if not view.details_shown:
            view.on_details()
        root.update()
        self.assertTrue(view.details_shown)
        self.check_fits(root, view)
        view.set_size(gui.MAX_SIZE)
        root.update()
        self.check_fits(root, view)
        self.assertLessEqual(view.size, biggest)

    def test_big_start_size(self):
        root, view = self.window(size=gui.MAX_SIZE)
        self.check_fits(root, view)
        if view.size < gui.MAX_SIZE:
            self.assertEqual(self.ctl.status.lines[-1], f"The text is {view.size} pt: bigger doesn't fit on this "
                                                        "screen.")

    def test_while_deploying(self):
        gate = threading.Event()
        root, view = self.window(size=gui.MAX_SIZE, gate=gate)
        try:
            view.on_deploy()
            self.assertTrue(wait_for(self.ctl, lambda: self.ctl.jobs["main"].lines))
            view.render()
            root.update()
            self.assertTrue(view.progress.winfo_ismapped())
            self.check_fits(root, view, rows=2)
        finally:
            gate.set()
            self.ctl.settle()

    def test_tab_skips_greyed_out_buttons(self):
        gate = threading.Event()
        root, view = self.window(gate=gate)
        try:
            deploy = view.by_action["deploy"]
            deploy.focus_force()
            root.update()
            deploy.event_generate("<Return>")  # (pressed with the keyboard: it keeps the focus, greyed out)
            root.update()
            self.assertEqual(self.ctl.status.lines[0], "Deploying...")
            self.assertEqual(str(deploy.cget("state")), "disabled")
            stops, w = [], view.tree
            for _ in range(30):
                w = w.tk_focusNext()
                if w is None or w is view.tree:
                    break
                stops.append(w)
            names = {b: a for a, b in view.by_action.items()}
            self.assertEqual([names[w] for w in stops if w in names], ["details"])
            for key in ("<Return>", "<space>"):
                self.ctl.status = gui.Status("busy", ["Deploying..."])
                deploy.focus_force()
                root.update()
                deploy.event_generate(key)
                root.update()
                self.assertEqual(self.ctl.status.lines, ["Please wait until Deploy is done."], key)
        finally:
            gate.set()
            self.ctl.settle()


class WindowSmoke(Base):
    def test_window_opens_and_closes(self):
        if gui.tk is None:
            self.skipTest("no tkinter")
        for dark in (False, True):
            with self.subTest(dark=dark):
                try:
                    root = gui.tk.Tk()
                except gui.tk.TclError as e:
                    self.skipTest(f"no display: {e}")
                view = None
                try:
                    root.withdraw()
                    ctl = self.make()
                    view = gui.View(root, ctl, size=20, dark=dark)
                    root.update()
                    self.assertEqual(view.tree.get_children(), ("a", "b", "c"))
                    self.assertEqual(view.tree.selection(), ("a",))
                    self.assertEqual(view.by_action["toggle"].cget("text"), "Turn off")
                    view.on_toggle()
                    root.update()
                    self.assertEqual(view.tree.item("a", "values")[1], "Off")
                    self.assertEqual(view.by_action["toggle"].cget("text"), "Turn on")
                    self.assertEqual(view.status_head.cget("text"), "Alpha is off.")
                    # (keys only reach a window that has the focus, and this one stays hidden: check that the
                    # keys are bound, then do what they do)
                    for seq in ("<space>", "<Delete>", "<Control-Up>", "<Control-Down>"):
                        self.assertIn("break", view.tree.bind(seq), seq)
                    for seq in ("<Control-plus>", "<Control-minus>", "<Control-0>", "<F5>"):
                        self.assertTrue(root.bind(seq), seq)
                    for seq in ("<Return>", "<Left>", "<Right>", "<FocusIn>"):
                        self.assertTrue(view.by_action["deploy"].bind(seq), seq)
                    view.on_toggle()
                    view.on_move(1)
                    root.update()
                    self.assertEqual(view.status_head.cget("text"), "Alpha is now number 2.")
                    self.assertEqual(view.tree.get_children(), ("b", "a", "c"))
                    self.assertEqual(view.tree.selection(), ("a",))
                    view.set_size(22)
                    root.update()
                    self.assertEqual(view.font.cget("size"), 22)
                    view.set_size(99)  # (the biggest that fits on this screen, up to MAX_SIZE)
                    root.update()
                    self.assertTrue(22 <= view.font.cget("size") <= gui.MAX_SIZE)
                    if view.font.cget("size") < gui.MAX_SIZE:
                        self.assertEqual(view.status_head.cget("text"),
                                         "This is the biggest text that fits on this screen.")
                    view.set_size(view.start_size)  # (Ctrl 0)
                    self.assertEqual(view.font.cget("size"), 20)
                    view.on_details()
                    root.update()
                    self.assertIn("modman move 1 2", view.details.get("1.0", "end"))
                    ctl.deploy()
                    self.assertTrue(ctl.settle())
                    view.render()
                    self.assertEqual(view.status_head.cget("text"), "Deployed 2 mods.")
                    self.assertEqual(root.title(), gui.TITLE)
                finally:
                    close(root, view)


class AppIcon(unittest.TestCase):
    """The window takes Breathalyzer's icon from assets/ when it is there, and works without it."""

    class FakeRoot:
        def __init__(self, bad_ico=False):
            self.calls, self.bad_ico = [], bad_ico

        def iconbitmap(self, default=None):
            self.calls.append(("ico", default))
            if self.bad_ico:
                raise gui.tk.TclError("made-up unreadable icon")

        def iconphoto(self, default, image):
            self.calls.append(("png", image))

    def setUp(self):
        if gui.tk is None:
            self.skipTest("no tkinter")
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.ico, self.png = (os.path.join(d.name, "breathalyzer" + ext) for ext in (".ico", ".png"))

    def test_title_and_where_the_icon_is(self):
        self.assertTrue(gui.TITLE.startswith("Breathalyzer"))
        repo = os.path.dirname(gui.TOOLS)
        self.assertEqual(gui.APP_ICONS, [os.path.join(repo, "assets", "breathalyzer.ico"),
                                         os.path.join(repo, "assets", "breathalyzer.png")])

    def test_no_icon_is_fine(self):
        root = self.FakeRoot()
        self.assertEqual(gui.set_app_icon(root, [self.ico, self.png]), "")
        self.assertEqual(root.calls, [])

    def test_ico_on_windows_else_png(self):
        for path in (self.ico, self.png):
            Path(path).write_bytes(b"made up")
        with mock.patch.object(gui.tk, "PhotoImage", side_effect=lambda **kw: ("image", kw["file"])):
            if os.name == "nt":
                root = self.FakeRoot()
                self.assertEqual(gui.set_app_icon(root, [self.ico, self.png]), self.ico)
                self.assertEqual(root.calls, [("ico", self.ico)])
            root = self.FakeRoot(bad_ico=True)   # (an .ico Tk can't read, or not Windows: the .png)
            self.assertEqual(gui.set_app_icon(root, [self.ico, self.png]), self.png)
            self.assertEqual(root.calls[-1], ("png", ("image", self.png)))
            self.assertEqual(root.app_icon, ("image", self.png))


if __name__ == "__main__":
    unittest.main()
