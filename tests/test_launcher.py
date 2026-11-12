"""Tests for tools/launcher.py and the launcher's window (tools/modman_gui.py: LaunchBackend, LaunchController,
LauncherView). Everything runs on made-up folders in a temp folder and a fake modman: never the real Savage Wind,
mod library, settings or game. The window tests open a see-through window (skipped without a display)."""
import contextlib
import gc
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import launcher as la  # noqa: E402
import modman_gui as gui  # noqa: E402
from test_modman_gui import MODS, ROOT, Clock, FakeModman, FakeSpawn, close, wait_for  # noqa: E402

GOOD = la.GAME_BUILD


def write(path: Path, data: bytes | str = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.write_bytes(data)
    return path


def make_install(folder: Path, version: str | None = la.SUPPORTED, set_up: bool = True, dlc: bool = False) -> Path:
    """A made-up Savage Wind folder: savagewind.exe with the version text after a commit hash (as the real one has
    it), and the set-up game files."""
    text = b"MZ\0\0" + b"\x90" * 300 + b"Version %s isn'tde0b6ac4efc1dda7" + (version or "").encode() + b"\0/\0"
    write(folder / la.EXE, text + b"\0" * 2000)
    if set_up:
        write(folder / "extracted" / "exefs" / "main", b"NSO0")
        write(folder / "extracted" / "romfs" / "Pack" / "Title.pack", b"pack")
        write(folder / "extracted" / "swgame.dll", b"MZ")
        write(folder / "extracted" / "swgame.dll.id", "0123456789abcdef\n")
    if dlc:
        (folder / "extracted" / "aoc0").mkdir(parents=True)
    return folder


def check(folder, library=None, why="picked", tmp=None, **kw):
    kw.setdefault("build_id", lambda _: GOOD)
    kw.setdefault("env", {})
    kw.setdefault("app_dir", str(tmp or folder))
    return la.check_install(str(folder), library=library, why=why, **kw)


class Temp(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(prefix="launcher-test-")
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)


# ---------------------------------------------------------------- launcher.json

class SettingsFile(Temp):
    def test_round_trip_and_unknown_keys_kept(self):
        path = str(self.tmp / "home" / la.SETTINGS_FILE)
        s, note = la.load_settings(path)
        self.assertIsNone(note)
        self.assertEqual((s.install, s.size, s.dark, s.port), (None, la.DEFAULT_SIZE, False, None))
        s.install, s.library, s.size, s.dark, s.port, s.hide_while_playing = "X:\\SW", "X:\\lib", 24, True, 8000, True
        s.extra = {"later": [1, 2]}
        la.save_settings(path, s)
        t, note = la.load_settings(path)
        self.assertIsNone(note)
        self.assertEqual((t.install, t.library, t.size, t.dark, t.port, t.hide_while_playing, t.extra),
                         ("X:\\SW", "X:\\lib", 24, True, 8000, True, {"later": [1, 2]}))
        self.assertEqual(json.loads(Path(path).read_text("utf-8"))["format"], la.FORMAT)
        self.assertEqual(os.listdir(os.path.dirname(path)), [la.SETTINGS_FILE])   # (no temp file left)

    def test_damaged_file_is_set_aside(self):
        path = write(self.tmp / la.SETTINGS_FILE, "{ not json")
        s, note = la.load_settings(str(path))
        self.assertIsNone(s.install)
        self.assertIn("start fresh", note)
        self.assertFalse(path.exists())
        self.assertTrue((self.tmp / (la.SETTINGS_FILE + ".bad")).is_file())

    def test_wrong_kinds_are_left_out(self):
        path = write(self.tmp / la.SETTINGS_FILE, json.dumps({"install": 5, "size": 99, "dark": "yes", "port": 0,
                                                              "library": "  "}))
        s, note = la.load_settings(str(path))
        self.assertIsNone(note)
        self.assertEqual((s.install, s.library, s.size, s.dark, s.port), (None, None, la.DEFAULT_SIZE, False, None))

    def test_home_dir(self):
        self.assertEqual(la.home_dir({la.ENV_HOME: str(self.tmp)}), str(self.tmp))
        self.assertEqual(la.home_dir({"LOCALAPPDATA": "Z:\\Local"}), os.path.join("Z:\\Local", "Breathalyzer"))


# ---------------------------------------------------------------- finding Savage Wind

class Finding(Temp):
    def test_wrong_level_picks_are_corrected(self):
        sw = make_install(self.tmp / "SavageWind" / "Savage Wind")
        for pick in (sw, sw / la.EXE, sw / "extracted", sw / "extracted" / "romfs" / "Pack", self.tmp / "SavageWind",
                     self.tmp):
            self.assertEqual(la.correct_pick(str(pick)), [str(sw)], pick)
        other = self.tmp / "empty"
        other.mkdir()
        self.assertEqual(la.correct_pick(str(other)), [])

    def test_two_below_are_both_offered(self):
        a = make_install(self.tmp / "Games" / "A")
        b = make_install(self.tmp / "Games" / "B")
        self.assertEqual(la.correct_pick(str(self.tmp / "Games")), [str(a), str(b)])

    def test_order_and_each_once(self):
        remembered = make_install(self.tmp / "remembered")
        from_lib = make_install(self.tmp / "from-lib")
        near = make_install(self.tmp / "near")
        docs = make_install(self.tmp / "Documents" / "SavageWind" / "Savage Wind")
        lib = self.tmp / "lib"
        write(lib / "config.json", json.dumps({"tool": "botw-mod-loader modman", "install": str(from_lib)}))
        found = la.find_installs(remembered=str(remembered), libraries=[str(lib)],
                                 near=[str(near), str(remembered)],
                                 known=la.known_places({"Documents": str(self.tmp / "Documents")}),
                                 drives=[], running=[str(near / la.EXE)])
        self.assertEqual([(f.folder, f.why) for f in found],
                         [(str(remembered), "remembered"), (str(from_lib), "your mod list"),
                          (str(near), "next to Breathalyzer"), (str(docs), "Documents")])

    def test_next_to_breathalyzer(self):
        top = self.tmp / "Documents" / "SavageWind" / "Breathalyzer-0.2.0"
        (top / "app" / "tools").mkdir(parents=True)
        sw = make_install(self.tmp / "Documents" / "SavageWind" / "Savage Wind")
        self.assertEqual(la.package_top(str(top / "app")), str(top))
        near = la.near_folders(str(top))
        self.assertEqual(near[:2], [str(top), str(top.parent)])
        self.assertIn(str(sw), near)
        self.assertNotIn(str(sw / "extracted" / "romfs"), near)   # (two levels below the parent, no deeper)
        found = la.find_installs(near=near)
        self.assertEqual([f.folder for f in found], [str(sw)])

    def test_near_is_limited(self):
        top = self.tmp / "a" / "top"
        top.mkdir(parents=True)
        for i in range(20):
            (self.tmp / "a" / f"d{i:02d}" / "x").mkdir(parents=True)
        self.assertLess(len(la.near_folders(str(top), limit=5)), len(la.near_folders(str(top))))

    def test_drive_places_are_exact(self):
        self.assertEqual(la.drive_places(["D:\\"]),
                         ["D:\\Savage Wind", "D:\\SavageWind\\Savage Wind", "D:\\Games\\Savage Wind",
                          "D:\\Games\\SavageWind\\Savage Wind"])

    def test_running_and_known_folders_answer(self):
        self.assertIsInstance(la.running_games(), list)
        self.assertEqual(set(la.known_folders()), {"Documents", "Desktop", "Downloads"})


# ---------------------------------------------------------------- checking it

class Checks(Temp):
    def test_good(self):
        sw = make_install(self.tmp / "SW", dlc=True)
        rep = check(sw, tmp=self.tmp)
        self.assertTrue(rep.ok, rep.screens)
        self.assertEqual((rep.version, rep.game_build, rep.dlc, rep.mods), (la.SUPPORTED, GOOD, True, None))
        self.assertTrue(rep.custom)   # (a made-up exe: its hash isn't an official one)
        self.assertEqual(rep.library, str(self.tmp / la.LIBRARY_NAME))
        texts = [t for _, t in rep.lines()]
        self.assertEqual(texts[0], f"Savage Wind {la.SUPPORTED} (a custom build)")
        self.assertIn("Game files: BotW 1.9.0", texts)
        self.assertIn("DLC: set up", texts)
        sha = la.exe_facts(str(sw / la.EXE))[1]
        with mock.patch.dict(la.KNOWN_EXES, {sha: la.SUPPORTED}):
            self.assertFalse(check(sw, tmp=self.tmp).custom)

    def test_version_text(self):
        for written, read in ((la.SUPPORTED, la.SUPPORTED), ("0.4.2-alpha.1", "0.4.2-alpha.1"),
                              ("0.4.1-alpha.40", "0.4.1-alpha.40"), ("1.0.0-rc.2", "1.0.0-rc.2"), (None, None)):
            sw = make_install(self.tmp / f"v-{written}", version=written, set_up=False)
            self.assertEqual(la.exe_facts(str(sw / la.EXE))[0], read, written)
        rep = check(self.tmp / "v-0.4.2-alpha.1", tmp=self.tmp)
        self.assertEqual(rep.first().key, "other_version")
        self.assertEqual(rep.first().heading, "This Savage Wind is version 0.4.2-alpha.1")
        self.assertEqual(rep.first().buttons[0], ("Open Savage Wind's download page", "download"))
        self.assertEqual(check(self.tmp / "v-None", tmp=self.tmp).first().key, "no_version")

    def test_every_problem_screen(self):
        sw = make_install(self.tmp / "SW")
        self.assertEqual(check(self.tmp / "nothing", tmp=self.tmp).first().key, "wrong_pick")
        rep = check(self.tmp / "nothing", why="remembered", tmp=self.tmp)
        self.assertEqual((rep.first().key, rep.first().heading),
                         ("moved", f"Savage Wind isn't in {self.tmp / 'nothing'} any more"))
        bare = make_install(self.tmp / "bare", set_up=False)
        sc = check(bare, tmp=self.tmp).first()
        self.assertEqual((sc.key, sc.buttons[0]), ("not_set_up", ("Open Savage Wind", "open_game")))
        self.assertIn("swgame.dll", sc.details)
        self.assertEqual(check(sw, tmp=self.tmp, build_id=lambda _: "0000000000000000").first().key, "other_game")
        self.assertEqual(check(sw, tmp=self.tmp, env={la.ENV_DATA_ROOT: "D:\\data"}).first().key, "data_moved")
        sc = check(sw, tmp=self.tmp, can_write=lambda p: False).first()
        self.assertEqual((sc.key, sc.line), ("cant_write", "Move Savage Wind out of Program Files (or another "
                                                           "protected folder)."))
        zipped = self.tmp / "Temp1_Breathalyzer.zip" / "Breathalyzer" / "app"
        self.assertEqual(check(sw, tmp=zipped).first().key, "in_zip")
        low = check(sw, tmp=self.tmp, disk_usage=lambda p: SimpleNamespace(free=2**29))
        self.assertTrue(low.ok)   # (a warning: the player may go on)
        self.assertEqual((low.first().key, low.first().blocks), ("low_space", False))
        lib = self.tmp / "lib"
        write(lib / "config.json", "{ damaged")
        sc = check(sw, library=str(lib), tmp=self.tmp).first()
        self.assertEqual((sc.key, sc.buttons[0][1]), ("list_damaged", "fresh_list"))
        backup = la.fresh_list(str(lib))
        self.assertTrue(os.path.isfile(backup) and not (lib / "config.json").exists())

    def test_existing_library(self):
        sw = make_install(self.tmp / "SW")
        lib = self.tmp / "lib"
        write(lib / "config.json", json.dumps({"tool": "botw-mod-loader modman", "install": str(sw),
                                               "mods": [{"id": "a"}, {"id": "b"}]}))
        rep = check(sw, library=str(lib), tmp=self.tmp)
        self.assertEqual(rep.mods, 2)
        self.assertIn((True, "Found your mod list: 2 mods"), rep.lines())
        write(lib / "config.json", json.dumps({"tool": "something else", "mods": []}))
        self.assertEqual(check(sw, library=str(lib), tmp=self.tmp).first().key, "list_damaged")

    def test_in_zip(self):
        self.assertTrue(la.in_zip("C:\\Users\\x\\AppData\\Local\\Temp\\Temp1_Breathalyzer.zip\\Breathalyzer"))
        self.assertFalse(la.in_zip("C:\\Users\\x\\Documents\\SavageWind\\Breathalyzer"))

    def test_frame_rate_is_only_read(self):
        sw = make_install(self.tmp / "SW", set_up=False)
        ini = write(sw / la.INI, "# settings\nframe_rate = 60\nvsync = fast\n")
        before = ini.read_bytes()
        self.assertEqual(la.frame_rate(str(sw)), "60")
        self.assertEqual(ini.read_bytes(), before)
        self.assertIsNone(la.frame_rate(str(self.tmp)))


# ---------------------------------------------------------------- the first run

class FirstRun(Temp):
    def run_first(self, settings=None, near=(), **kw):
        settings = settings or la.Settings()
        return la.FirstRun(settings, places=lambda: {"near": [str(p) for p in near]},
                           check=lambda f, lib, why: check(f, lib, why, tmp=self.tmp, **kw))

    def test_nothing_found(self):
        f = self.run_first()
        f.start()
        self.assertEqual((f.screen.key, f.screen.buttons), ("not_found", [("Choose your Savage Wind folder",
                                                                          "choose")]))
        f.pick(str(self.tmp))
        self.assertEqual(f.screen.key, "wrong_pick")

    def test_one_found_then_remembered(self):
        sw = make_install(self.tmp / "SavageWind" / "Savage Wind")
        settings = la.Settings()
        f = self.run_first(settings, near=[sw])
        f.start()
        self.assertTrue(f.ready)
        self.assertEqual(f.report.folder, str(sw))
        path = str(self.tmp / "home" / la.SETTINGS_FILE)
        f.finish(path)
        saved, _ = la.load_settings(path)
        self.assertEqual((saved.install, saved.library), (str(sw), str(self.tmp / "SavageWind" / la.LIBRARY_NAME)))
        again = self.run_first(saved)
        again.start()   # (the remembered folder: no search)
        self.assertTrue(again.ready)
        self.assertEqual(again.report.library, saved.library)

    def test_several_found(self):
        a, b = make_install(self.tmp / "A"), make_install(self.tmp / "B")
        f = self.run_first(near=[a, b])
        f.start()
        self.assertIsNone(f.screen)
        self.assertEqual([c.folder for c in f.choices], [str(a), str(b)])
        f.use(str(b))
        self.assertTrue(f.ready)
        self.assertEqual(f.report.folder, str(b))

    def test_pick_one_level_too_high(self):
        sw = make_install(self.tmp / "SavageWind" / "Savage Wind")
        f = self.run_first()
        f.start()
        f.pick(str(self.tmp / "SavageWind"))
        self.assertTrue(f.ready)
        self.assertEqual(f.report.folder, str(sw))

    def test_remembered_folder_moved(self):
        f = self.run_first(la.Settings(install=str(self.tmp / "gone")))
        f.start()
        self.assertEqual(f.screen.key, "moved")
        self.assertEqual(f.screen.buttons[0], ("Find it again", "search"))

    def test_low_space_warning(self):
        sw = make_install(self.tmp / "SW")
        f = self.run_first(near=[sw], disk_usage=lambda p: SimpleNamespace(free=2**29))
        f.start()
        self.assertEqual(f.screen.key, "low_space")
        self.assertFalse(f.ready)
        f.go_on()
        self.assertTrue(f.ready)
        f = self.run_first(la.Settings(install=str(sw)), disk_usage=lambda p: SimpleNamespace(free=2**29))
        f.start()   # (remembered: the warning goes to Home, not in the way)
        self.assertTrue(f.ready)

    def test_library_argument_wins(self):
        sw = make_install(self.tmp / "SW")
        f = la.FirstRun(la.Settings(install=str(sw), library=str(self.tmp / "old")), library=str(self.tmp / "arg"),
                        check=lambda folder, lib, why: check(folder, lib, why, tmp=self.tmp))
        f.start()
        self.assertEqual(f.report.library, str(self.tmp / "arg"))


# ---------------------------------------------------------------- what each mod is

class Descriptions(Temp):
    def test_mod_toml_and_others(self):
        lib = self.tmp / "lib"
        write(lib / "mods" / "a" / "mod.toml", '[mod]\nid = "x.a"\nname = "A"\nversion = "1.0"\nauthors = ["giruka"]\n'
                                               'description = "Stamina never runs out. Off with infstamina off."\n')
        write(lib / "mods" / "b" / "Wrap" / "mod.toml", '[mod]\nid = "x.b"\ndescription = "Wrapped one."\n')
        short, full = la.describe_mod(str(lib), {"id": "a", "kinds": ["native"], "version": "1.0"})
        self.assertEqual(short, "Stamina never runs out.")
        self.assertEqual(full, "Stamina never runs out. Off with infstamina off. Version 1.0. By giruka.")
        self.assertEqual(la.describe_mod(str(lib), {"id": "b", "kinds": ["romfs"]})[0], "Wrapped one.")
        short, _ = la.describe_mod(str(lib), {"id": "c", "kinds": ["romfs", "cheats"], "source": "D:\\dl\\Linkle.zip"})
        self.assertEqual(short, "Changes game files, Cheats. Added from a .zip file.")
        _, full = la.describe_mod(str(lib), {"id": "d", "kinds": ["romfs"], "wiiu": {"wiiu_data": 2}})
        self.assertIn("still Wii U data", full)
        write(lib / "mods" / "e" / "mod.toml", "[mod\nbroken")
        self.assertEqual(la.describe_mod(str(lib), {"id": "e", "kinds": []})[0], "Nothing the game can use.")


# ---------------------------------------------------------------- Play, step by step (a fake modman)

class LaunchModman(FakeModman):
    """FakeModman plus modmenu's apply and a run that returns once the game started (no cheats)."""

    MOD_ENV = ("SAVAGEWIND_ROMFS_OVERLAY", "SWMOD_DIR")

    def __init__(self, mods=MODS):
        super().__init__(mods)
        self.cheats = True

    def cmd_deploy(self, *opts):
        self.deploy_opts = list(opts)
        return super().cmd_deploy()

    def cmd_apply(self):
        print("Used your changes from the game (1 change).")
        self.deployed = self.inputs_hash(None)
        return 0

    def cmd_run(self, *opts):
        if self.cheats:
            return super().cmd_run(*opts)
        print("Started Savage Wind (pid 4321) with 2 mod(s).")
        return 0

    # (for the mod picker: a mod with a mod.toml gets its id from it, as modman does; mods by id or number)
    def cmd_add(self, path):
        man = la.mod_manifest(path)
        if not man.get("id"):
            return super().cmd_add(path)
        if "broken" in man["id"]:
            print(f"Error: Not added: {man['id']}'s DLL is missing.")
            return 2
        self.mods.append({"id": man["id"], "name": man.get("name", man["id"]), "enabled": True, "kinds": ["native"]})
        print(f"Added #{len(self.mods)} {man['id']} (native): {man.get('name')}")
        return 0

    def cmd_enable(self, ref, on=True):
        k = next((i for i, m in enumerate(self.mods) if m["id"] == ref), None)
        return super().cmd_enable(str(k + 1) if k is not None else ref, on)

    def cmd_disable(self, ref):
        return self.cmd_enable(ref, False)

    def cmd_toggle(self, lever, value):   # (tools/quality.py toggle: builds the effects mod into the library)
        if not any(m["id"] == "botw-mod-loader.perf-effects" for m in self.mods):
            self.mods.append({"id": "botw-mod-loader.perf-effects", "name": "Performance: effects off",
                              "enabled": True, "kinds": ["romfs"]})
        print(f"{lever} {value}")
        return 0

    def cmd_sync(self):   # (tools/modmenu.py sync)
        print("Wrote the game's mod list.")
        return 0


def make_app(folder: Path, mods: list[dict] | None = None) -> Path:
    """A made-up app\\ folder of Breathalyzer Plus: tools\\ with the tools the list names, bundled-mods\\ and
    bundled-mods.json as make_release writes it."""
    for tool in ("quality.py", "modmenu.py"):
        write(folder / "tools" / tool, "# made up\n")
    if mods is None:
        mods = [
            {"id": "botw-mod-loader.modmenu", "title": "Mods page in the game", "default": "on", "kind": "code",
             "description": "Adds a Mods page to the game's Options.", "folder": "bundled-mods/modmenu",
             "after_add": [["modmenu.py", "sync"]], "tags": []},
            {"id": "botw-mod-loader.basics-inf-stamina", "title": "Infinite Stamina", "default": "off",
             "kind": "code", "description": "Stamina never runs out.", "folder": "bundled-mods/basics-inf-stamina",
             "tags": ["Changes gameplay"]},
            {"id": "botw-mod-loader.perf-effects", "title": "Performance: effects off", "default": "off",
             "kind": "recipe", "description": "No glow, for more fps.", "recipe": "native/examples/perf_effects",
             "build": [["quality.py", "toggle", "ssao", "off"]], "tags": ["Built from your game files"]}]
    for m in mods:
        if m.get("folder") and ".." not in m["folder"] and ":" not in m["folder"]:   # (never outside the temp folder)
            write(folder / m["folder"] / "mod.toml", f'[mod]\nid = "{m["id"]}"\nname = "{m["title"]}"\n')
    write(folder / la.BUNDLED_JSON, json.dumps({"format": 1, "edition": "plus", "version": "0.2.0-beta.1",
                                                "mods": mods}))
    return folder


class Bundled(Temp):
    def test_read(self):
        app = make_app(self.tmp / "app")
        key, mods = la.read_bundled(str(app))
        self.assertEqual(key, "0.2.0-beta.1:botw-mod-loader.modmenu,botw-mod-loader.basics-inf-stamina,"
                              "botw-mod-loader.perf-effects")
        self.assertEqual([(m["id"], m["on"], m["kind"]) for m in mods],
                         [("botw-mod-loader.modmenu", True, "code"), ("botw-mod-loader.basics-inf-stamina", False,
                                                                      "code"),
                          ("botw-mod-loader.perf-effects", False, "recipe")])
        self.assertEqual(mods[0]["folder"], str(app / "bundled-mods" / "modmenu"))
        self.assertEqual(la.read_bundled(str(self.tmp / "no-app")), ("", []))

    def test_bad_entries_are_left_out(self):
        good = {"id": "x.good", "title": "Good", "default": "on", "kind": "code", "description": "Fine.",
                "folder": "bundled-mods/good"}
        bad = [dict(good, id="x.up", folder="../outside"), dict(good, id="x.abs", folder="C:/Windows"),
               dict(good, id="x.tool", after_add=[["evil.py"]]), dict(good, id="x.cmd", after_add=[["cmd.exe", "/c"]]),
               dict(good, id="x.notitle", title=""), dict(good, id="x.default", default="maybe"),
               {"id": "x.recipe", "title": "R", "default": "on", "kind": "recipe", "description": "No build."}]
        app = make_app(self.tmp / "app", [good, *bad])
        (self.tmp / "outside").mkdir()
        _, mods = la.read_bundled(str(app))
        self.assertEqual([m["id"] for m in mods], ["x.good"])


class Picker(unittest.TestCase):
    def make(self, *args, **kw):
        return Play.make(self, *args, **kw)

    def scripts(self):
        return Play.scripts(self)

    def setUp(self):
        d = tempfile.TemporaryDirectory(prefix="launcher-test-")
        self.addCleanup(d.cleanup)
        self.app = make_app(Path(d.name) / "app")
        self.key, self.mods = la.read_bundled(str(self.app))

    def test_install_what_was_picked(self):
        ctl = self.make(mods=[MODS[0]])
        picks = {"botw-mod-loader.modmenu": True, "botw-mod-loader.basics-inf-stamina": False,
                 "botw-mod-loader.perf-effects": True}
        with mock.patch.object(gui, "TOOLS", str(self.app / "tools")):
            ctl.install_picked(self.mods, picks)
        self.assertFalse(ctl.can("play"))
        self.assertEqual(ctl.status.lines[0], "Adding the mods you picked (1 of 6)...")
        self.assertTrue(ctl.settle())
        self.assertEqual(self.scripts(), [("modman.py", ["add", str(self.app / "bundled-mods" / "modmenu")]),
                                          ("modmenu.py", ["sync"]),
                                          ("modman.py", ["add", str(self.app / "bundled-mods" / "basics-inf-stamina")]),
                                          ("quality.py", ["toggle", "ssao", "off"])])
        on = {r.id: r.on for r in ctl.listing.rows}
        self.assertEqual(on, {"a": True, "botw-mod-loader.modmenu": True, "botw-mod-loader.basics-inf-stamina": False,
                              "botw-mod-loader.perf-effects": True})
        self.assertEqual(ctl.status.lines, ["Your mods are ready.", "Press Play. You can turn them on or off below."])
        self.assertTrue(ctl.can("play"))
        # (picked again: only on/off changes; a recipe that is there is turned off, not built again)
        n = len(self.spawn.calls)
        ctl.install_picked(self.mods, {**picks, "botw-mod-loader.perf-effects": False})
        self.assertTrue(ctl.settle())
        self.assertEqual(len(self.spawn.calls), n)
        self.assertFalse(ctl.row("botw-mod-loader.perf-effects").on)

    def test_a_recipe_left_off_isnt_built(self):
        ctl = self.make(mods=[])
        ctl.install_picked(self.mods, {})
        self.assertTrue(ctl.settle())
        self.assertNotIn("quality.py", [s for s, _ in self.scripts()])
        self.assertEqual([r.id for r in ctl.listing.rows], ["botw-mod-loader.modmenu",
                                                            "botw-mod-loader.basics-inf-stamina"])

    def test_one_that_cant_be_added(self):
        broken = dict(self.mods[1], id="botw-mod-loader.broken", title="Broken one")
        write(Path(broken["folder"]) / "mod.toml", '[mod]\nid = "botw-mod-loader.broken"\n')
        ctl = self.make(mods=[])
        ctl.install_picked([self.mods[0], broken], {"botw-mod-loader.broken": True})
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines, ["1 mod couldn't be added: Broken one.",
                                            "The others are ready. Details says why."])
        self.assertEqual([r.id for r in ctl.listing.rows], ["botw-mod-loader.modmenu"])


class FakeMenu:
    MOD_ID = "botw-mod-loader.modmenu"

    def __init__(self, pending=False):
        self.pending = pending
        self.synced = 0
        self.config = "old"

    def has_pending(self, lib):
        return self.pending

    def read_menu(self, lib):
        return SimpleNamespace(config=self.config)

    def config_stamp(self, lib):
        return "new"

    def sync(self, lib):
        self.synced += 1
        self.config = "new"


class Play(unittest.TestCase):
    def make(self, mods=MODS, gate=None, running=False, menu=False, deployed=True, **attrs):
        self.mm = LaunchModman(mods)
        for k, v in attrs.items():
            setattr(self.mm, k, v)
        if deployed:
            self.mm.deployed = self.mm.inputs_hash(None)
        self.spawn = FakeSpawn(self.mm, gate)
        self.running = [running]
        self.popen = mock.Mock()
        backend = gui.LaunchBackend(None, "Z:\\SW", mm=self.mm, spawn=self.spawn, python="py",
                                    running=lambda: self.running[0], menu=menu, popen=self.popen)
        self.ctl = gui.LaunchController(backend, clock=Clock())
        return self.ctl

    def scripts(self):
        return [(os.path.basename(argv[2]), argv[3:-2]) for argv, _ in self.spawn.calls]

    def runs(self):
        """The modman commands run in this process (the others go through spawn)."""
        return [c for c in self.mm.calls if c[0] == "run"]

    def test_up_to_date_starts_at_once(self):
        ctl = self.make()
        self.assertEqual(ctl.summary_line(), "2 of 3 mods on.")
        ctl.refresh_state()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.summary_line(), "2 of 3 mods on. Ready.")
        ctl.launch()
        self.assertEqual(ctl.status.lines, ["Getting ready..."])
        self.assertFalse(ctl.can("play"))
        self.assertTrue(ctl.settle())
        self.assertEqual(self.scripts(), [])
        self.assertEqual(self.mm.calls, [["run", "--library", ROOT]])
        self.assertEqual(ctl.status.lines[0], "The game closed.")   # (with cheats: run waits for the game)
        self.assertTrue(ctl.can("play"))

    def test_changed_mods_are_deployed_first(self):
        gate = threading.Event()
        ctl = self.make(deployed=False, gate=gate)
        ctl.refresh_state()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.state_text, "Play gets your mods ready first.")
        ctl.launch()
        self.assertTrue(wait_for(ctl, lambda: ctl.jobs.get("main") and ctl.jobs["main"].action == "deploy"))
        self.assertEqual(ctl.status.lines, ["Getting your mods ready...", "The first time takes longer."])
        self.assertEqual(ctl.progress(), ["Getting your mods ready... 0:00"])
        self.assertTrue(ctl.can("cancel"))
        self.assertFalse(ctl.can("toggle"))
        gate.set()
        self.assertTrue(ctl.settle())
        self.assertEqual(self.scripts(), [("modman.py", ["deploy"])])
        self.assertEqual(self.mm.calls[-1], ["run", "--library", ROOT])
        self.assertEqual(ctl.status.lines[0], "The game closed.")
        ctl.toggle("a")
        self.assertEqual(ctl.status.lines, ["Alpha is off.", "Play uses the change."])
        ctl.refresh_state()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.state_text, "Play gets your changes ready first.")

    def test_changes_from_the_games_mods_page(self):
        menu = FakeMenu(pending=True)
        ctl = self.make(menu=menu, deployed=False)
        ctl.launch()
        self.assertTrue(ctl.settle())
        self.assertEqual(self.scripts(), [("modmenu.py", ["apply"])])   # (apply deploys: no second deploy)
        self.assertEqual(self.mm.calls[-1][0], "run")
        self.assertEqual(menu.synced, 0)

    def test_old_mods_page_list_is_written_again(self):
        menu = FakeMenu()
        mods = [*MODS, {"id": FakeMenu.MOD_ID, "name": "Mod menu", "enabled": True, "kinds": ["native"]}]
        ctl = self.make(mods=mods, menu=menu)
        ctl.launch()
        self.assertTrue(ctl.settle())
        self.assertEqual(menu.synced, 1)
        self.assertEqual(self.mm.calls[-1][0], "run")

    def test_game_already_open(self):
        ctl = self.make(running=True)
        ctl.launch()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines, ["Savage Wind is already open.", "Close it, then press Play."])
        self.assertEqual(self.mm.calls, [])
        self.assertFalse(ctl.can("play"))
        self.assertFalse(ctl.can("play_plain"))
        self.running[0] = False
        for _ in range(2):   # (closed only when two checks in a row say so)
            ctl.watch()
            self.assertTrue(wait_for(ctl, lambda: not ctl._watching))
        self.assertEqual(ctl.status.lines, ["The game closed.", "Press Play to play again."])
        self.assertTrue(ctl.can("play"))

    def test_game_without_cheats_is_watched(self):
        ctl = self.make()
        self.mm.cheats = False
        self.running[0] = True
        ctl.launch()
        self.assertTrue(ctl.settle())
        self.assertTrue(ctl.game_open)
        self.assertEqual(ctl.progress(), ["The game is open. Close it to play again with other mods."])
        ctl.watch()
        self.assertTrue(wait_for(ctl, lambda: not ctl._watching))
        self.assertTrue(ctl.game_open)
        self.running[0] = False
        for _ in range(2):
            ctl.watch()
            self.assertTrue(wait_for(ctl, lambda: not ctl._watching))
        self.assertFalse(ctl.game_open)

    def test_code_mods_that_cant_be_built(self):
        ctl = self.make(deployed=False, deploy_error=[
            "Rebuilding the game code with 1 hook mod. This takes 10-20 minutes.",
            "Error: sw_rebuild failed: the compiler stopped (exit code 3)"])
        ctl.launch()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines[0], "Your code mods couldn't be built.")
        self.assertEqual(ctl.offer, ("Play without code mods", "no_rebuild"))
        self.assertEqual(self.runs(), [])
        self.mm.deploy_error = None
        ctl.launch(no_rebuild=True)
        self.assertTrue(ctl.settle())
        self.assertEqual(self.scripts()[-1], ("modman.py", ["deploy", "--no-rebuild"]))
        self.assertEqual(self.mm.calls[-1][0], "run")
        self.assertIsNone(ctl.offer)

    def test_other_deploy_failure(self):
        ctl = self.make(deployed=False, deploy_error=["Error: Can't deploy: Better Map needs Base Map (turn it on)"])
        ctl.launch()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines, ["Your mods couldn't be made ready.",
                                            "Can't deploy: Better Map needs Base Map (turn it on)"])
        self.assertIsNone(ctl.offer)

    def test_cancel(self):
        gate = threading.Event()
        ctl = self.make(deployed=False, gate=gate)
        ctl.launch()
        self.assertTrue(wait_for(ctl, lambda: ctl.jobs.get("main") and ctl.jobs["main"].action == "deploy"
                                 and ctl.jobs["main"].proc is not None))
        self.assertTrue(ctl.can("cancel"))
        with mock.patch.object(gui, "kill_tree") as kill:
            ctl.cancel()
        kill.assert_called_once()
        gate.set()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines, ["Stopped.", "Press Play to try again."])
        self.assertEqual(self.runs(), [])

    def test_port_in_use(self):
        ctl = self.make(port_busy=True)
        ctl.launch()
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines, ["Another program uses port 7777.",
                                            "Close it and press Play again, or pick another port in Settings."])

    def test_play_without_mods(self):
        ctl = self.make()
        with mock.patch.dict(os.environ, {"SAVAGEWIND_ROMFS_OVERLAY": "Z:\\o", "SWMOD_DIR": "Z:\\n",
                                          "SAVAGEWIND_SAVE_DIR": "Z:\\save"}):
            ctl.play_plain()
        argv, kw = self.popen.call_args
        self.assertEqual(argv[0], [os.path.join("Z:\\SW", la.EXE)])
        self.assertEqual(kw["cwd"], "Z:\\SW")
        self.assertNotIn("SAVAGEWIND_ROMFS_OVERLAY", kw["env"])
        self.assertNotIn("SWMOD_DIR", kw["env"])
        self.assertEqual(kw["env"]["SAVAGEWIND_SAVE_DIR"], "Z:\\save")   # (not a mod variable: kept)
        self.assertEqual(ctl.status.lines[0], "Started Savage Wind without mods.")
        self.assertTrue(ctl.game_open)
        self.assertFalse(ctl.can("play"))

    def test_mod_rows_say_what_they_do(self):
        ctl = self.make()
        self.assertEqual([r.about for r in ctl.listing.rows], ["Changes game files.", "Cheats, Code mod.",
                                                               "Nothing the game can use."])


# ---------------------------------------------------------------- shortcuts and one window

@unittest.skipUnless(sys.platform == "win32", "Windows only")
class WindowsParts(Temp):
    def test_shortcut_round_trip(self):
        path = str(self.tmp / "Breathalyzer.lnk")
        target, args, workdir, icon = la.shortcut_parts()
        la.write_shortcut(path, target, args, workdir, icon, "Start Breathalyzer")
        got = la.read_shortcut(path)
        self.assertEqual(os.path.normcase(got["target"]), os.path.normcase(target))
        self.assertEqual((got["args"], got["workdir"], got["description"]), (args, workdir, "Start Breathalyzer"))
        self.assertTrue(args.startswith("-I ") and args.endswith('launcher.py"'))
        self.assertTrue(target.lower().endswith(("pythonw.exe", "python.exe")))

    def test_one_window_only(self):
        key = str(self.tmp / "settings-home")
        first = la.single_instance(key)
        self.assertIsNotNone(first)
        with mock.patch.object(la, "bring_to_front", return_value=False) as front:
            self.assertIsNone(la.single_instance(key))
        front.assert_called_once_with(la.WINDOW_TITLE)
        self.assertIsNotNone(la.single_instance(key + "-other"))


# ---------------------------------------------------------------- the window

class Window(Temp):
    """The launcher window, see-through (nothing shows): the first-run screens, Home, Settings, bigger text."""

    def open(self, settings=None, near=(), mods=MODS, size=None, app_dir=la.REPO):
        if gui.tk is None:
            self.skipTest("no tkinter")
        try:
            root = gui.tk.Tk()
        except gui.tk.TclError as e:
            self.skipTest(f"no display: {e}")
        root.attributes("-alpha", 0.0)
        self.mm = LaunchModman(mods)
        self.mm.deployed = self.mm.inputs_hash(None)
        self.spawn = FakeSpawn(self.mm)
        settings = settings or la.Settings()
        if size:
            settings.size = size
        first = la.FirstRun(settings, places=lambda: {"near": [str(p) for p in near]},
                            check=lambda f, lib, why: check(f, lib or str(self.tmp / "lib"), why, tmp=self.tmp))

        def backend(lib, install):
            self.mm.installs.add(install)
            return gui.LaunchBackend(lib, install, mm=self.mm, spawn=self.spawn, python="py", running=lambda: False,
                                     menu=False, popen=mock.Mock())

        try:
            view = gui.LauncherView(root, settings, str(self.tmp / "home" / la.SETTINGS_FILE), first_run=first,
                                    make_backend=backend, app_dir=str(app_dir))
        except BaseException:
            close(root)
            raise
        # (then the window's objects are freed here, in the main thread: a Tk freed by a garbage collection that
        # happens to run in a background thread stops the whole process with "Tcl_AsyncDelete")
        self.addCleanup(gc.collect)
        self.addCleanup(close, root, view)
        root.update()
        return root, view

    def visible(self, view, widget):
        return widget.winfo_ismapped() and widget.winfo_rooty() + widget.winfo_height() <= \
            view.root.winfo_rooty() + view.root.winfo_height()

    def test_first_run_to_home(self):
        sw = make_install(self.tmp / "SavageWind" / "Savage Wind")
        root, view = self.open(near=[sw])
        self.assertEqual(view.screen, "setup")
        self.assertEqual(view.setup_head.cget("text"), "Found Savage Wind")
        self.assertEqual(view.setup_line.cget("text"), str(sw))
        self.assertEqual(root.focus_lastfor(), view.setup_buttons[0])
        self.assertEqual(view.setup_buttons[0].cget("text"), "Continue")
        view.setup_buttons[0].invoke()
        root.update()
        self.assertEqual(view.screen, "home")
        self.assertEqual(self.mm.calls[0], ["init", str(sw), "--library", str(self.tmp / "lib")])
        saved, _ = la.load_settings(view.settings_file)
        self.assertEqual(saved.install, str(sw))
        self.assertEqual(root.focus_lastfor(), view.play)
        self.assertEqual(view.sw_label.cget("text"), f"Savage Wind {la.SUPPORTED}  \u2713")
        rows = [view.tree.item(i, "values") for i in view.tree.get_children()]
        self.assertEqual([r[:2] for r in rows], [("ON", "Alpha"), ("OFF", "Bravo"), ("ON", "Charlie")])
        self.assertEqual(rows[0][2], "Changes game files.")
        self.assertTrue(view.about.cget("text").startswith("Alpha: "))
        for w in (view.play, view.tree, *view.by_action.values()):
            self.assertTrue(self.visible(view, w), w)
        # Space in the list turns the mod off; the text says Play uses it
        view.tree.focus_set()
        view.on_toggle()
        root.update()
        self.assertEqual(view.tree.item("a", "values")[0], "OFF")
        self.assertEqual(view.status_more.cget("text"), "Play uses the change.")
        view.play.invoke()
        self.assertTrue(view.ctl.settle())
        self.assertEqual(self.mm.calls[-1][0], "run")
        self.assertEqual(self.spawn.calls[-1][0][3], "deploy")

    def test_nothing_found_screen(self):
        root, view = self.open()
        self.assertEqual(view.setup_head.cget("text"), "Where is Savage Wind?")
        self.assertEqual(view.setup_buttons[0].cget("text"), "Choose your Savage Wind folder")
        sw = make_install(self.tmp / "Games" / "SavageWind" / "Savage Wind")
        with mock.patch.object(gui.filedialog, "askdirectory", return_value=str(self.tmp / "Games")):
            view.setup_buttons[0].invoke()
        root.update()
        self.assertEqual(view.setup_head.cget("text"), "Found Savage Wind")
        self.assertEqual(view.first.report.folder, str(sw))

    def test_settings_and_back(self):
        sw = make_install(self.tmp / "SW")
        root, view = self.open(settings=la.Settings(install=str(sw)))
        self.assertEqual(view.screen, "home")   # (remembered: straight to Home)
        view.by_action["settings"].invoke()
        root.update()
        self.assertEqual(view.screen, "settings")
        texts = [b.cget("text") for b in view.settings_buttons]
        self.assertEqual(texts[0], "Back")
        for want in ("Change...", "Smaller", "Bigger", "Make one"):
            self.assertIn(want, texts)
        view.back()
        root.update()
        self.assertEqual(view.screen, "home")
        view.on_check()
        root.update()
        self.assertEqual(view.setup_head.cget("text"), "Your setup is ready")
        view.back()
        self.assertEqual(view.screen, "home")

    def test_bigger_text_still_fits(self):
        sw = make_install(self.tmp / "SW")
        root, view = self.open(settings=la.Settings(install=str(sw)))
        for size in range(view.size + 2, la.MAX_SIZE + 2, 2):
            view.set_size(size)
            root.update()
            self.assertLessEqual(root.winfo_height(), root.winfo_screenheight())
            for w in (view.play, *view.by_action.values()):
                self.assertTrue(self.visible(view, w), f"{w.cget('text')} at {view.size} pt")
        self.assertGreater(view.size, la.DEFAULT_SIZE)
        saved, _ = la.load_settings(view.settings_file)
        self.assertEqual(saved.size, view.size)

    def test_more_opens_the_mod_manager(self):
        sw = make_install(self.tmp / "SW")
        root, view = self.open(settings=la.Settings(install=str(sw)))
        view.by_action["more"].invoke()
        root.update()
        self.assertIsNotNone(view.manager)
        self.assertTrue(view.ctl.blocked)
        self.assertFalse(view.ctl.can("play"))
        self.assertEqual(view.manager.title(), gui.TITLE)
        view.manager.destroy()
        root.update()
        self.assertIsNone(view.manager)
        self.assertTrue(view.ctl.can("play"))

    def test_details_after_a_failure(self):
        sw = make_install(self.tmp / "SW")
        root, view = self.open(settings=la.Settings(install=str(sw)))
        self.mm.deployed = None
        self.mm.deploy_error = ["Error: Can't deploy: a file is in use"]
        self.assertFalse(view.why_button.ring.winfo_ismapped())
        view.play.invoke()
        self.assertTrue(view.ctl.settle())
        view.render()
        root.update()
        self.assertEqual(view.status_head.cget("text"), "Your mods couldn't be made ready.")
        self.assertTrue(view.why_button.ring.winfo_ismapped())
        before = set(root.winfo_children())
        view.why_button.invoke()
        root.update()
        new = [w for w in root.winfo_children() if w not in before]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0].title(), "Details")
        new[0].destroy()

    def test_picker_before_the_first_play(self):
        sw = make_install(self.tmp / "SW")
        app = make_app(self.tmp / "app")
        root, view = self.open(near=[sw], mods=[], app_dir=app)
        view.setup_buttons[0].invoke()   # (Continue)
        root.update()
        self.assertEqual(view.screen, "picker")
        self.assertEqual([b.cget("text") for b in view.pick_buttons.values()], ["ON", "OFF", "OFF"])
        self.assertEqual(root.focus_lastfor(), view.picker_buttons[0])
        view.pick_buttons["botw-mod-loader.basics-inf-stamina"].invoke()
        self.assertEqual(view.pick_buttons["botw-mod-loader.basics-inf-stamina"].cget("text"), "ON")
        save = next(b for b in view.picker_buttons if b.cget("text") == "Save")
        save.invoke()
        self.assertTrue(view.ctl.settle())
        view.render()
        root.update()
        self.assertEqual(view.screen, "home")
        self.assertEqual([view.tree.item(i, "values")[:2] for i in view.tree.get_children()],
                         [("ON", "Mods page in the game"), ("ON", "Infinite Stamina")])
        saved, _ = la.load_settings(view.settings_file)
        self.assertEqual(saved.picked, view.bundled_key)
        view.show_settings()
        self.assertIn("Pick again", [b.cget("text") for b in view.settings_buttons])

    def test_report_text_hides_the_home_folder(self):
        text = gui.report_text(["Error: can't read C:\\Users\\Someone\\Documents\\x", "ok"], home="C:\\Users\\Someone")
        self.assertEqual(text, "Error: can't read %USERPROFILE%\\Documents\\x\nok")

    def test_dark(self):
        sw = make_install(self.tmp / "SW")
        root, view = self.open(settings=la.Settings(install=str(sw)))
        view.show_settings()
        view.on_dark()
        root.update()
        self.assertEqual(view.t, gui.THEMES["dark"])
        self.assertEqual(view.screen, "settings")
        view.back()
        root.update()
        self.assertEqual(view.play.cget("bg"), gui.THEMES["dark"]["play"])


class CommandLine(unittest.TestCase):
    def test_options(self):
        a = la.parser().parse_args(["--size", "24", "--dark", "--port", "8000", "--search-in", "x"])
        self.assertEqual((a.size, a.dark, a.port, a.search_in), (24, True, 8000, ["x"]))
        for bad in (["--size", "9"], ["--port", "0"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                la.parser().parse_args(bad)


if __name__ == "__main__":
    unittest.main()
