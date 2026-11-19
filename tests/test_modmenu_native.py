"""The Mod menu mod (native/examples/modmenu) against a made-up Savage Wind and game library: no game, no Savage Wind
process. It builds modhost, the mod and the fakes in native/examples/modmenu/selftest with the zig that ships with
Savage Wind (at below-normal priority), then drives them with build_native's fake-game harness: the fake game library
calls the fake savagewind.exe's rt_opt_* functions through its import table, as swgame.dll does.

Skipped when Savage Wind's zig or savagewind.dll.a isn't there (SWMOD_INSTALL, else Documents/SavageWind/Savage Wind).
The library it uses is made up, in a temp folder.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import build_native as bn  # noqa: E402
import modman as mm  # noqa: E402
import mod_settings as ms  # noqa: E402
import modmenu  # noqa: E402

ST = REPO / "native" / "examples" / "modmenu" / "selftest"
FAKE_RUNTIME = bn.HOST_SRC / "selftest" / "fake_runtime.c"
OUT = REPO / "build" / "native" / "modmenu-selftest"
VARIANTS = {"sw": (), "other_version": ("FAKE_OTHER_VERSION",), "other_bytes": ("FAKE_OTHER_BYTES",),
            "add_page": ("FAKE_ADD_PAGE",)}
ENV = {"FAKE_MAX_CMDS": "32", "SWMOD_MODMENU_WRITE_MS": "0"}

FIRST_PAGE = ["Fake page one", "Fake page two", "Mods", "Quit game"]
MODS_PAGE = ["Better Sprint", "Hyrule Map Fix", "Stamina Hook", "Change order", "Mod settings and info", "More",
             "How to use changes"]
TUNER_TOML = """[mod]
id = "tuner"
name = "Tuner"
version = "1.0"
authors = ["giruka"]
description = "Tunes sprint #1."
loader_api = 3

[code]
windows = "tuner.dll"

[[pages]]
key = "more"
label = "More options"
description = "A text row."

[[settings]]
key = "glow"
type = "toggle"
label = "Glow"
description = "An ON/OFF row."
default = true
applies = "live"

[[settings]]
key = "mode"
type = "choice"
label = "Sprint mode"
description = "Fast also sets the speed to 150."
default = "classic"
applies = "live"
choices = [
  { value = "classic", label = "Classic" },
  { value = "fast", label = "Fast", sets = { speed = 150 } },
  { value = "custom", label = "Custom" },
]

[[settings]]
key = "speed"
type = "slider"
label = "Sprint speed"
description = "Shown only when Sprint mode is Custom."
min = 50
max = 200
step = 10
default = 100
unit = " percent"
applies = "live"
show_if = "mode == 'custom'"

[[settings]]
key = "drain"
type = "slider"
label = "Stamina use"
description = "Locked while Sprint mode is Classic."
min = 0.5
max = 2.0
step = 0.5
default = 1.0
unit = "x"
applies = "live"
enable_if = "mode != 'classic'"

[[settings]]
key = "hotkey"
type = "key"
label = "Tuner key"
description = "Press A, then the key."
default = "F6"
applies = "live"

[[settings]]
key = "hello"
type = "button"
label = "Say hello"
button = "Say it"
description = "A button."

[[settings]]
key = "greeting"
type = "text"
label = "Greeting"
description = "Change it on your PC."
default = "Hello"
max_length = 20
applies = "restart"
page = "more"
"""

OPEN_MODS = ["g boot", "g open pause", "g sel 2", "g a", "g frame 3"]  # (the pause menu's Options, then Mods)


def targets() -> list:
    t = [bn.Target("mm_modhost", (bn.HOST_SRC / "modhost.c",), runtime=True, out_name="modhost.dll",
                   export="swgame_tables"),
         bn.Target("mm_modmenu", modmenu.mod_target().sources, runtime=False, out_name="modmenu.dll", subdir="mods",
                   export="swmod_init"),
         bn.Target("mm_fake_game", (ST / "fake_game.c",), runtime=True, out_name="fake_swgame.dll", subdir="fake",
                   export="swgame_tables")]
    for name, defines in VARIANTS.items():
        t.append(bn.Target(f"mm_sw_{name}", (FAKE_RUNTIME, ST / "fake_sw.c"), runtime=False, out_name="savagewind.exe",
                           subdir=f"{name}/selftest", defines=defines))
    return t


def make_library(root: Path) -> mm.Library:
    """Three made-up mods and the menu mod itself (hidden), with a menu.txt from tools/modmenu.py."""
    lib = mm.Library(str(root), None, [
        {"id": "better-sprint", "name": "Better Sprint", "enabled": True, "kinds": ["romfs"]},
        {"id": "map-fix", "name": "Hyrule Map Fix", "enabled": True, "kinds": ["romfs"]},
        {"id": modmenu.MOD_ID, "name": "Mod menu", "enabled": True, "kinds": ["native"]},
        {"id": "stamina", "name": "Stamina Hook", "enabled": False, "kinds": ["native"]},
    ])
    toml = {"better-sprint": 'description = "Sprint for longer."',
            "stamina": 'description = "Keeps stamina up."\nloader_api = 2\ngame_versions = ["1.9.0"]\n[code]\n'
                       'windows = "s.dll"\nhooks = ["0x7000cc7410"]'}
    for m in lib.mods:
        d = root / "mods" / m["id"]
        d.mkdir(parents=True)
        if m["id"] in toml:
            (d / "mod.toml").write_text(f'[mod]\nid = "{m["id"]}"\nname = "{m["name"]}"\nversion = "1.0"\n'
                                        f'{toml[m["id"]]}\n', encoding="utf-8")
    lib.save()
    modmenu.sync(lib)
    return lib


def plain(text: str) -> str:
    """A text as the player reads it: without the game's message tags (the fake game shows them as <font 3> ...)."""
    return re.sub(r"</?font[^>]*>|</?grey>|<tag>", "", text or "")


def names(reply: dict) -> list[str]:
    d = reply.get("data", {})
    return [d.get(f"rows.{i}.title") for i in range(d.get("count", 0))]


def values(reply: dict) -> list[str]:
    d = reply.get("data", {})
    return [d.get(f"rows.{i}.value") for i in range(d.get("count", 0))]


MAIN_STEPS = [
    ("status", "modmenu"), ("iat", "g iat"), ("sw0", "sw"),
    ("boot", "g boot"), ("open", "g open pause"), ("table1", "g table"),
    ("sel_mods", "g sel 2"), ("a_mods", "g a"), ("f1", "g frame 3"), ("rows_mods", "g rows"), ("table2", "g table"),
    ("sel0", "g sel 0"), ("a0", "g a"), ("sel0b", "g sel 0"),
    ("sel2", "g sel 2"), ("a2", "g a"), ("sel2b", "g sel 2"), ("a2b", "g a"), ("status2", "modmenu"),
    ("b1", "g b"), ("f2", "g frame 3"), ("rows_first", "g rows"),
    ("sel_one", "g sel 0"), ("a_one", "g a"), ("f3", "g frame 3"), ("table_one", "g table"), ("rows_one", "g rows"),
    ("sel_t", "g sel 1"), ("a_t", "g a"), ("sw1", "sw"),
    ("b2", "g b"), ("f4", "g frame 3"), ("rows_first2", "g rows"),
    ("click_mods", "g click 2"), ("sw_click1", "sw"), ("f5", "g frame 3"), ("rows_mods2", "g rows"),
    ("click_mod1", "g click 1"), ("sw_click2", "sw"), ("rows_mods3", "g rows"),
    ("sel_order", "g sel 3"), ("a_order", "g a"), ("f6", "g frame 3"), ("rows_order", "g rows"),
    ("sel_o0", "g sel 0"), ("right", "g right"), ("f7", "g frame 4"), ("rows_order2", "g rows"),
    ("sel_o0b", "g sel 0"), ("sel_o2", "g sel 2"),
    ("status3", "modmenu"),
    ("b3", "g b"), ("f8", "g frame 3"), ("rows_mods4", "g rows"),
    ("y", "g y"), ("rows_mods5", "g rows"), ("status4", "modmenu"),
    ("markall", "sw markall"), ("f9", "g frame 8"), ("sw_markall", "sw"), ("rows_mods6", "g rows"),
    ("hold", "sw hold 1"), ("click_held", "g click 0"), ("unhold", "sw hold 0"),
    ("set", "modmenu_set map-fix off"), ("move", "modmenu_move better-sprint 2"), ("set_menu", "modmenu_set 9 on"),
    ("set_self", f"modmenu_set {modmenu.MOD_ID} off"), ("status5", "modmenu"),
    ("b4", "g b"), ("f10", "g frame 3"), ("b5", "g b"), ("f11", "g frame 4"),
    ("open_title", "g open title"), ("sel_m", "g sel 2"), ("a_m", "g a"), ("f12", "g frame 3"),
    ("rows_title", "g rows"), ("b6", "g b"), ("f13", "g frame 3"), ("rows_title_back", "g rows"),
    ("reboot", "g reboot"), ("open2", "g open pause"), ("rows_reboot", "g rows"), ("table_reboot", "g table"),
    ("text_value", "g text 90102"), ("text_unknown", "g text 95555"), ("sw_end", "sw"),
    ("wait", "!sleep 300"),
]


@unittest.skipUnless(sys.platform == "win32", "Windows only (the fakes are Windows DLLs)")
class ModMenuNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            tc = bn.find_toolchain(bn.default_install())
        except bn.BuildError as e:
            raise unittest.SkipTest(str(e))
        # (sync refuses while Savage Wind runs: these libraries are made up, so the real game doesn't matter here)
        cls.not_running = mock.patch.object(mm, "game_running", return_value=False)
        cls.not_running.start()
        cls.addClassCleanup(cls.not_running.stop)
        t = targets()
        built = bn.build(tc, OUT, t, runner=modmenu.low_priority_runner)
        bn.check_exports(zip(built, t))
        cls.tmp = tempfile.TemporaryDirectory()
        cls.lib = make_library(Path(cls.tmp.name) / "lib")
        cls.menu_before = Path(modmenu.menu_path(cls.lib)).read_text(encoding="utf-8")
        steps, cls.log = cls.run_fake("sw", [ln for _, ln in MAIN_STEPS])
        cls.s = dict(zip((k for k, _ in MAIN_STEPS), steps))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def run_fake(cls, variant: str, lines, env=None, library=None) -> tuple[list, str]:
        extra = dict(ENV, SWMOD_LIBRARY=str(library or cls.lib.root), **(env or {}))
        result, log = bn._run_fake_game(OUT / variant, OUT / "fake" / "fake_swgame.dll", OUT / "mods", list(lines),
                                        extra, host=OUT / "modhost.dll")
        return result.get("steps", []), log

    def d(self, key: str) -> dict:
        return self.s[key].get("data", {})

    # ---- path A+ on the fake 0.4.1-alpha.4

    def test_turns_on_and_swaps_eleven_slots(self):
        self.assertTrue(self.d("status")["on"], self.d("status"))
        self.assertEqual(self.d("status")["path"], "A+")
        self.assertEqual(self.d("status")["count"], 3)
        self.assertIn("[mod] modmenu: Mods page on (3 mods, path A+)", self.log)
        self.assertEqual((self.d("iat")["swapped"], self.d("iat")["screen_swapped"]), (11, False))
        self.assertTrue(self.d("sw0")["pointer_kind_jump"])

    def test_mods_button_above_quit_and_the_mods_page(self):
        self.assertEqual(names(self.s["open"]), FIRST_PAGE)
        self.assertEqual(self.d("table1")["who"], "mod")
        self.assertEqual(self.d("sel_mods")["desc"], "Turn mods on or off, and change their order.")
        self.assertEqual(self.d("a_mods")["result"], "closed")
        self.assertEqual(self.d("f1")["opened"], 1)
        self.assertEqual(names(self.s["rows_mods"]), MODS_PAGE)
        self.assertEqual(values(self.s["rows_mods"])[:3], ["ON", "ON", "OFF"])
        self.assertEqual(values(self.s["rows_mods"])[3:], ["Open", "Open", "Open", "See below"])

    def test_toggle_and_descriptions(self):
        self.assertEqual(self.d("sel0")["desc"], "Sprint for longer.")
        self.assertEqual(self.d("a0")["value"], "OFF")
        self.assertEqual(self.d("sel0b")["desc"], "Sprint for longer. Turns off once you use changes.")
        self.assertEqual(self.d("status2")["changes"], 1)
        self.assertEqual(self.d("a2")["value"], "ON")
        self.assertEqual(self.d("sel2b")["desc"],
                         "Keeps stamina up. Turns on once you use changes. Next start takes 10-20 minutes.")
        self.assertTrue(self.d("status2")["mods.2.rebuild"])
        self.assertEqual(self.d("sel_o0b")["desc"], "Was 1st. Moves once you use changes.")
        self.assertEqual(self.d("sel_o2")["desc"], "When two mods change the same file, the later one in the list is "
                                                   "used.")

    def test_the_description_line_fits(self):
        """The game test: the game shows about 82 characters of the line and cuts the rest at a word, and its font has
        no { or }. A long "what it does" is shortened, never the change; { } are drawn in the game's font tag 3 (the
        early game check: it has all of ASCII)."""
        with tempfile.TemporaryDirectory() as d:
            lib = self.library(d)
            p = Path(modmenu.menu_path(lib))
            long_about = "Keeps stamina {up} for a long time, also while you climb, swim or run."
            note = "Adds one new text file, System/modmenu-note.txt, to the game files."
            text = p.read_text(encoding="utf-8").replace("Keeps stamina up.", long_about)
            p.write_text(text.replace("Sprint for longer.", note), encoding="utf-8")
            steps, _ = self.run_fake("sw", OPEN_MODS + ["g sel 2", "g a", "g sel 2", "g sel 4", "g sel 0", "g a",
                                                        "g sel 0"], library=lib.root)
        self.assertEqual(steps[5]["data"]["desc"], long_about.replace("{", "<font 3>{</font>")
                         .replace("}", "<font 3>}</font>"))
        self.assertEqual(plain(steps[7]["data"]["desc"]),
                         "Keeps stamina... Turns on once you use changes. Next start takes 10-20 minutes.")
        self.assertEqual(steps[9]["data"]["desc"], note)
        self.assertEqual(steps[11]["data"]["desc"], "Adds one new text file... Turns off once you use changes.")
        for k in (5, 7, 8, 9, 11):
            self.assertLessEqual(len(plain(steps[k]["data"]["desc"])), 80)

    def test_savage_winds_pages_still_work(self):
        self.assertEqual(names(self.s["rows_first"]), FIRST_PAGE)  # (B on the Mods page: back to the first page)
        self.assertEqual(self.d("f3")["opened"], 1)
        self.assertEqual(self.d("table_one")["who"], "sw")  # (its other pages keep its own table)
        self.assertEqual(names(self.s["rows_one"]), ["Game text 0020", "Fake toggle"])
        self.assertEqual(self.d("a_t")["value"], "ON")
        self.assertEqual(self.d("sw1")["toggle"], 1)
        self.assertEqual(names(self.s["rows_first2"]), FIRST_PAGE)

    def test_mouse_on_every_row(self):
        self.assertEqual(self.d("sw_click1")["kinds"], "2222")  # (Savage Wind's rows and Mods, with our table in)
        self.assertEqual(names(self.s["rows_mods2"]), MODS_PAGE)
        self.assertEqual(values(self.s["rows_mods2"])[0], "OFF")  # (kept while the game runs)
        self.assertEqual(self.d("sw_click2")["kinds"], "0002222")
        self.assertEqual(values(self.s["rows_mods3"])[1], "OFF")
        self.assertEqual(values(self.s["click_held"]), values(self.s["rows_mods6"]))  # (a key capture holds the mouse)

    def test_order_page(self):
        self.assertEqual(names(self.s["rows_order"]), MODS_PAGE[:3])
        self.assertEqual(values(self.s["rows_order"]), ["1st", "2nd", "3rd"])
        self.assertEqual(self.d("right")["value"], "2nd")
        self.assertEqual(self.d("f7")["redraws"], 2)  # (the other rows' numbers change)
        self.assertEqual(values(self.s["rows_order2"]), ["2nd", "1st", "3rd"])
        st = self.d("status3")
        self.assertEqual((st["mods.0.id"], st["mods.0.position"], st["mods.0.wanted_position"]), ("map-fix", 2, 1))
        self.assertEqual(names(self.s["rows_mods4"])[:2], ["Hyrule Map Fix", "Better Sprint"])  # (in the new order)

    def test_restore_defaults_undoes_everything(self):
        self.assertEqual(names(self.s["rows_mods5"])[:2], ["Hyrule Map Fix", "Better Sprint"])  # (rows stay)
        self.assertEqual(values(self.s["rows_mods5"])[:3], ["ON", "ON", "OFF"])
        self.assertEqual(self.d("status4")["changes"], 0)

    def test_savage_winds_redraw_of_our_rows(self):
        self.assertGreaterEqual(self.d("f9")["redraws"], 5)
        self.assertEqual(self.d("sw_markall")["dirty"], 0)
        self.assertEqual(names(self.s["rows_mods6"]), names(self.s["rows_mods5"]))

    def test_commands(self):
        self.assertEqual(self.d("set")["changes"], 1)
        self.assertEqual(self.d("move")["wanted_position"], 2)
        self.assertFalse(self.s["set_menu"]["ok"])
        self.assertIn("Mod menu itself", self.s["set_self"]["error"])
        self.assertEqual(self.s["status5"]["text"], "Mod menu on: 3 mods, 2 changes wait for your next start.")

    def test_title_menu_reopens_and_a_reboot_rebuilds(self):
        self.assertEqual(self.d("f11")["opener"], 0)  # (B on the first page closes Options)
        self.assertEqual(names(self.s["rows_title"])[0], "Hyrule Map Fix")
        self.assertEqual(names(self.s["rows_title_back"]), FIRST_PAGE)
        self.assertEqual(names(self.s["rows_reboot"]), FIRST_PAGE)
        self.assertEqual(self.d("table_reboot")["who"], "mod")
        self.assertEqual(self.d("sw_end")["installs"], 2)

    def test_texts(self):
        self.assertEqual(self.d("text_value")["text"], "3rd")
        self.assertEqual(self.d("text_unknown")["text"], "")

    def test_pending_file_and_apply(self):
        text = Path(modmenu.menu_path(self.lib, modmenu.PENDING)).read_text(encoding="utf-8")
        serial = next(ln.split("\t")[1] for ln in self.menu_before.splitlines() if ln.startswith("serial\t"))
        self.assertEqual(text, f"swmodmenu-pending\t1\nserial\t{serial}\n"
                               "mod\tmap-fix\toff\nmod\tbetter-sprint\ton\nmod\tstamina\toff\n")
        lib = mm.Library.load(self.lib.root)
        rows = {r.id: r for r in modmenu.rows(lib)}
        self.assertEqual((rows["map-fix"].wanted, rows["better-sprint"].wanted_position), (False, 2))

    # ---- the gate: anything else, and nothing is touched

    def gate_off(self, variant: str, why: str, env=None):
        steps, log = self.run_fake(variant, ["modmenu", "g iat", "sw", "g boot", "g open pause", "g table"], env)
        st, iat, sw, _, rows, table = (x.get("data", {}) for x in steps)
        self.assertFalse(st["on"])
        self.assertIn(why, st["why"])
        self.assertIn(f"[mod] modmenu: Mods page off: {st['why']}", log)
        self.assertEqual(iat["swapped"], 1 if env and "FAKE_TAMPER" in env else 0)
        self.assertFalse(sw["pointer_kind_jump"])
        self.assertEqual([rows.get(f"rows.{i}.title") for i in range(rows["count"])],
                         ["Fake page one", "Fake page two", "Quit game"])
        self.assertEqual(table["who"], "sw")

    def test_off_for_another_savage_wind(self):
        self.gate_off("other_version", "isn't 0.4.1-alpha.4")

    def test_off_when_the_pointer_function_differs(self):
        self.gate_off("other_bytes", "rt_opt_pointer_kind isn't the one of 0.4.1-alpha.4")
        steps, _ = self.run_fake("other_bytes", ["sw"])
        self.assertNotEqual(steps[0]["data"]["pointer_kind"][:4], "5648")

    def test_off_for_another_game_version(self):
        self.gate_off("sw", "for BotW 1.8.2", {"FAKE_GAME_VERSION": "1.8.2"})

    def test_off_when_a_slot_was_changed(self):
        self.gate_off("sw", "rt_opt_tick was already changed", {"FAKE_TAMPER": "1"})

    def test_off_without_the_options_sites(self):
        self.gate_off("sw", "doesn't name the site opt_screen_rows", {"FAKE_NO_SITE": "1"})

    def test_refuses_a_load_while_the_game_runs(self):
        steps, log = self.run_fake("sw", ["mods", "modmenu"], {"FAKE_BOOTED": "1"})
        mods = steps[0]["data"]
        status = {mods[f"mods.{i}.file"]: mods[f"mods.{i}.status"] for i in range(mods["found"])}
        self.assertEqual(status["modmenu.dll"], "init failed (code 3)")
        self.assertIn("load it at game start", log)
        self.assertFalse(steps[1]["ok"])

    def test_missing_list(self):
        with tempfile.TemporaryDirectory() as d:
            steps, _ = self.run_fake("sw", ["modmenu", "g boot", "g open pause", "g sel 2", "g a", "g frame 3",
                                            "g sel 0"], library=d)
        self.assertEqual(steps[0]["data"]["list"], "missing")
        self.assertEqual(names(steps[5]), ["Mod list missing"])
        # (finding 15: the step that writes the list; starting from Mod Manager doesn't)
        self.assertEqual(steps[6]["data"]["desc"], "Close the game. Then on your PC run: python tools/modmenu.py sync")

    # ---- the review's findings (each on its own made-up library)

    def library(self, d: str, extra=()) -> mm.Library:
        lib = make_library(Path(d) / "lib")
        if extra:
            lib.mods += [dict(m) for m in extra]
            for m in extra:
                (Path(lib.root) / "mods" / m["id"]).mkdir(parents=True)
            lib.save()
            modmenu.sync(lib)
        return lib

    @staticmethod
    def pending_mods(lib) -> dict:
        p = Path(modmenu.menu_path(lib, modmenu.PENDING))
        return {ln.split("\t")[1]: ln.split("\t")[2] for ln in p.read_text(encoding="utf-8").splitlines()
                if ln.startswith("mod\t")} if p.exists() else {}

    def test_changes_survive_a_new_list(self):
        """Finding 2: changes from one start survive a sync before the next start, and a pending.txt made from an older
        list is moved onto the new one (through that list's copy) or kept aside, never overwritten."""
        with tempfile.TemporaryDirectory() as d:
            lib = self.library(d)
            self.run_fake("sw", OPEN_MODS + ["g sel 0", "g a", "!sleep 150"], library=lib.root)  # (Better Sprint off)
            first = Path(modmenu.menu_path(lib, modmenu.PENDING)).read_text(encoding="utf-8")
            modmenu.sync(lib)
            steps, _ = self.run_fake("sw", ["modmenu"] + OPEN_MODS + ["g rows", "g sel 2", "g a", "!sleep 150"],
                                     library=lib.root)
            self.assertEqual(steps[0]["data"]["changes"], 1)
            self.assertEqual(values(steps[6])[:3], ["OFF", "ON", "OFF"])
            self.assertEqual(self.pending_mods(lib), {"better-sprint": "off", "map-fix": "on", "stamina": "on"})
            # an older pending.txt that no sync moved (one was cut off): the game moves it itself
            Path(modmenu.menu_path(lib, modmenu.PENDING)).write_text(first, encoding="utf-8")
            steps, log = self.run_fake("sw", ["modmenu"], library=lib.root)
            self.assertEqual(steps[0]["data"]["changes"], 1)
            self.assertIn("pending.txt was made from an older mod list; its changes were moved onto this one", log)
            # without that list's copy, what the player changed can't be told: kept as pending.old.txt
            serial = next(ln.split("\t")[1] for ln in first.splitlines() if ln.startswith("serial\t"))
            os.remove(modmenu.menu_path(lib, modmenu.BASE.format(serial)))
            steps, log = self.run_fake("sw", ["modmenu"] + OPEN_MODS + ["g rows", "g sel 0", "g sel 2", "g a",
                                                                       "!sleep 150"], library=lib.root)
            self.assertEqual(Path(modmenu.menu_path(lib, modmenu.PENDING_OLD)).read_text(encoding="utf-8"), first)
            self.assertEqual(names(steps[6])[0], "Last change not used")
            self.assertEqual(steps[7]["data"]["desc"], modmenu.NOTE_STALE)
            self.assertEqual(self.pending_mods(lib), {"better-sprint": "on", "map-fix": "off", "stamina": "off"})

    def test_how_to_use_changes_says_what_works(self):
        """Finding 3: the game tells the step that works today (modmenu.py play); "press Play in Mod Manager" and
        "at your next start" only once menu.txt says Mod Manager uses the changes."""
        with tempfile.TemporaryDirectory() as d:
            lib = self.library(d)
            lines = OPEN_MODS + ["g sel 6", "g sel 0", "g a", "g sel 0", "!sleep 100"]
            steps, _ = self.run_fake("sw", lines, library=lib.root)
            self.assertEqual(steps[5]["data"]["desc"], "Close the game. Then on your PC run: python tools/modmenu.py play")
            menu = Path(modmenu.menu_path(lib))
            menu.write_text(menu.read_text(encoding="utf-8").replace("use\tcommand", "use\tmodman"), encoding="utf-8")
            if os.path.exists(modmenu.menu_path(lib, modmenu.PENDING)):
                os.remove(modmenu.menu_path(lib, modmenu.PENDING))
            steps, _ = self.run_fake("sw", lines, library=lib.root)
            self.assertEqual(steps[5]["data"]["desc"], "Close the game. Then press Play in Mod Manager.")
            self.assertEqual(steps[8]["data"]["desc"], "Sprint for longer. Turns off at your next start.")

    def test_an_old_list_says_so_on_the_page(self):
        """Finding 4: after a Mod Manager change that no sync followed, the Mods page starts with a warning row."""
        with tempfile.TemporaryDirectory() as d:
            lib = self.library(d)
            lib.mods[3]["enabled"] = True  # (Stamina Hook on in Mod Manager; menu.txt not written again)
            lib.save()
            steps, _ = self.run_fake("sw", ["modmenu"] + OPEN_MODS + ["g rows", "g sel 0"], library=lib.root)
        self.assertTrue(steps[0]["data"]["stale"])
        self.assertEqual(names(steps[6]), ["This list may be old"] + MODS_PAGE)
        self.assertEqual(values(steps[6])[0], "See below")
        self.assertEqual(steps[7]["data"]["desc"],
                         "Mod Manager changed mods after this list was made. ON/OFF may be wrong.")

    def test_a_change_right_before_the_game_closes_is_kept(self):
        """Finding 9: with the default pause between writes, two quick changes are both in pending.txt soon after
        (it was up to a second: the game closing in between lost the second)."""
        with tempfile.TemporaryDirectory() as d:
            lib = self.library(d)
            self.run_fake("sw", OPEN_MODS + ["g sel 0", "g a", "!sleep 100", "g sel 1", "g a", "!sleep 150"],
                          env={"SWMOD_MODMENU_WRITE_MS": ""}, library=lib.root)
            self.assertEqual(self.pending_mods(lib), {"better-sprint": "off", "map-fix": "off", "stamina": "off"})

    def test_order_page_keeps_mods_on_the_page(self):
        """Finding 10: with more mods than the page shows, a mod can't be moved past the last place shown (it would
        leave both pages for good)."""
        extra = [{"id": f"m{i:02d}", "name": f"Mod {i:02d}", "enabled": True, "kinds": ["romfs"]} for i in range(32)]
        with tempfile.TemporaryDirectory() as d:
            lib = self.library(d, extra)
            steps, _ = self.run_fake("sw", OPEN_MODS + ["g sel 31", "g a", "g frame 3", "g rows", "g sel 0"]
                                     + ["g right"] * 40 + ["modmenu"], library=lib.root)
        self.assertEqual(names(steps[5])[30:32], ["More mods in Mod Manager", "Change order"])
        self.assertEqual(names(steps[8])[30:], ["More mods in Mod Manager"])  # (30 Order rows)
        self.assertEqual(steps[-2]["data"]["value"], "30th")
        st = steps[-1]["data"]
        self.assertEqual((st["mods.29.id"], st["mods.29.wanted_position"]), ("better-sprint", 30))

    def test_an_id_as_long_as_modman_allows(self):
        """Finding 14: a mod whose id has 64 characters (modman allows that) is listed and can be changed."""
        long_id = "long" + "-x" * 30
        self.assertTrue(mm.good_id(long_id) and len(long_id) == 64)
        with tempfile.TemporaryDirectory() as d:
            lib = self.library(d, [{"id": long_id, "name": "Long Id", "enabled": True, "kinds": ["romfs"]}])
            steps, _ = self.run_fake("sw", ["modmenu", f"modmenu_set {long_id} off", "!sleep 150"], library=lib.root)
            self.assertEqual(steps[0]["data"]["count"], 4)
            self.assertEqual(steps[0]["data"]["mods.3.id"], long_id)
            self.assertEqual(self.pending_mods(lib)[long_id], "off")

    # ---- v2: More, Mod settings and info, a mod's pages, key capture (docs/mod-menu.md)

    def settings_library(self, d: str) -> tuple[mm.Library, Path]:
        """The made-up library plus Tuner (a mod with one of each kind of setting, and a sub-page), menu.txt, and a run
        folder whose settings.txt modhost reads (as deploy writes it). Returns (library, the run's mods folder)."""
        lib = make_library(Path(d) / "lib")
        root = Path(lib.root) / "mods" / "tuner"
        root.mkdir(parents=True)
        (root / "mod.toml").write_text(TUNER_TOML, encoding="utf-8")
        (root / "tuner.dll").write_bytes(b"")
        lib.mods.append({"id": "tuner", "name": "Tuner", "enabled": True, "kinds": ["native"], "version": "1.0"})
        lib.save()
        modmenu.sync(lib)
        run = Path(d) / "run"
        with mock.patch.object(ms, "_check_game", lambda refuse: None):
            ms.write_settings_txt(lib, lib.enabled(), str(run))
        (run / "native" / "mods").mkdir(parents=True)
        shutil.copy2(OUT / "mods" / "modmenu.dll", run / "native" / "mods" / "modmenu.dll")
        return lib, run / "native" / "mods"

    def run_mods(self, mods: Path, lines, library) -> tuple[list, str]:
        extra = dict(ENV, SWMOD_LIBRARY=str(library), SWMOD_SETTINGS_WRITE_MS="0")
        result, log = bn._run_fake_game(OUT / "sw", OUT / "fake" / "fake_swgame.dll", mods, list(lines), extra,
                                        host=OUT / "modhost.dll")
        return result.get("steps", []), log

    def test_mod_settings_pages(self):
        """Mod settings and info: a row per mod; a mod's page has its info (any modhost), its settings (mod API 3) with
        live enable_if (locked rows refuse changes, grey, "Locked: needs ..."), show_if as a grey "Not used now" row,
        a sub-page, a key capture, a button; B goes back one page; changes reach settings\\<id>.json."""
        with tempfile.TemporaryDirectory() as d:
            lib, mods = self.settings_library(d)
            lines = {}
            seq = OPEN_MODS + []
            names_ = ["list_sel", "list_a", "list_f", "list_rows", "tuner_sel", "tuner_a", "tuner_f", "tuner_rows",
                      "speed_sel", "drain_sel", "drain_right", "mode_sel", "mode_right", "mode_f", "mode_rows",
                      "drain_sel2", "drain_right2", "key_sel", "key_a", "status_cap", "key_f", "key_sel2",
                      "key_k", "key_f2", "key_rows", "key_sel3", "key_a2", "key_esc", "key_f3", "key_sel4",
                      "key_wait", "key_f4", "key_sel5", "hello_sel", "hello_a", "sub_sel", "sub_a", "sub_f",
                      "sub_rows", "b1", "bf1", "b1_rows", "status_page", "b2", "bf2", "b2_rows", "b3", "bf3",
                      "b3_rows", "sleep"]
            cmds = ["g sel 5", "g a", "g frame 3", "g rows", "g sel 3", "g a", "g frame 3", "g rows",
                    "g sel 5", "g sel 6", "g right", "g sel 4", "g right", "g frame 12", "g rows",
                    "g sel 6", "g right", "g sel 7", "g a", "modmenu", "g frame 1", "g sel 7",
                    "modmenu_key K", "g frame 4", "g rows", "g sel 7", "g a", "modmenu_key Esc", "g frame 1",
                    "g sel 7", "modmenu_key wait", "g frame 4", "g sel 7", "g sel 8", "g a", "g sel 9", "g a",
                    "g frame 3", "g rows", "g b", "g frame 3", "g rows", "modmenu", "g b", "g frame 3", "g rows",
                    "g b", "g frame 3", "g rows", "!sleep 400"]
            steps, log = self.run_mods(mods, seq + cmds, lib.root)
            s = dict(zip(names_, steps[len(seq):]))
            values_file = Path(lib.root) / "settings" / "tuner.json"
            saved = json.loads(values_file.read_text(encoding="utf-8")) if values_file.exists() else {}

        def dd(k):
            return s[k].get("data", {})

        self.assertEqual(names(s["list_rows"]), ["Better Sprint", "Hyrule Map Fix", "Stamina Hook", "Tuner"], log)
        self.assertEqual(dd("tuner_sel")["desc"], "Tunes sprint <font 3>#</font>1. 7 settings.")
        self.assertEqual([plain(t) for t in names(s["tuner_rows"])],
                         ["Tuner", "Version", "Made by", "Glow", "Sprint mode", "Sprint speed", "Stamina use",
                          "Tuner key", "Say hello", "More options", "Reset settings"])
        vals = values(s["tuner_rows"])
        self.assertEqual(vals[1:2] + vals[3:9], ["1.0", "ON", "Classic", "<grey>100 percent</grey>",
                                                 "<grey>1.0x</grey>", "F6", "Say it"])
        self.assertEqual(names(s["tuner_rows"])[6], "<grey>Stamina use</grey>")  # (locked: grey)
        self.assertEqual(dd("speed_sel")["desc"], "Not used now: other settings turn it off.")
        self.assertEqual(dd("drain_sel")["desc"], "Locked: needs Sprint mode not Classic.")
        self.assertEqual(dd("drain_right")["value"], "<grey>1.0x</grey>")  # (refused: unchanged)
        self.assertEqual(dd("mode_right")["value"], "Fast")
        self.assertGreaterEqual(dd("mode_f")["redraws"], 2)  # (the rows it unlocked and the speed it set)
        self.assertEqual(values(s["mode_rows"])[5:7], ["<grey>150 percent</grey>", "1.0x"])
        self.assertEqual(dd("drain_sel2")["desc"], "Locked while Sprint mode is Classic.")
        self.assertEqual(dd("drain_right2")["value"], "1.5x")
        # the key capture: 10 s, a countdown on the line, no input for the page meanwhile, Savage Wind's keys refused
        self.assertEqual(dd("key_a")["result"], "done")
        self.assertTrue(dd("status_cap")["capture"])
        self.assertEqual(dd("key_sel2")["desc"], "Press a key now. 10 s left. Backspace clears.")
        self.assertEqual(values(s["key_rows"])[7], "K")
        self.assertEqual(dd("key_sel3")["desc"], "Got K.")
        self.assertEqual(dd("key_sel4")["desc"], "Press a key now. 10 s left. Backspace clears. Esc is Savage Wind's.")
        self.assertEqual(dd("key_sel5")["desc"], "No key in 10 s: nothing changed.")
        self.assertEqual(dd("hello_a")["result"], "done")
        self.assertEqual(names(s["sub_rows"]), ["Greeting"])
        self.assertEqual(values(s["sub_rows"]), ["Hello"])
        self.assertEqual(names(s["b1_rows"])[0], "Tuner")  # (B: back to Tuner's page; its first row is its name)
        self.assertEqual((dd("status_page")["page"], dd("status_page")["page_mod"]), ("mod", "tuner"))
        self.assertEqual(names(s["b2_rows"])[3], "Tuner")  # (then Mod settings and info)
        self.assertEqual(names(s["b3_rows"])[:4], ["Better Sprint", "Hyrule Map Fix", "Stamina Hook", "Tuner"])
        self.assertEqual(saved.get("values", {}).get("mode"), "fast", saved)
        self.assertEqual(saved["values"].get("hotkey"), "K")
        self.assertEqual(saved["values"].get("drain"), 1.5)

    def test_info_page_without_settings(self):
        """A mod's info page needs no settings (no settings.txt here: modhost has none to show)."""
        with tempfile.TemporaryDirectory() as d:
            lib = make_library(Path(d) / "lib")
            steps, log = self.run_fake("sw", OPEN_MODS + ["g sel 4", "g a", "g frame 3", "g sel 0", "g a", "g frame 3",
                                                          "g rows", "g sel 0", "modmenu"], library=lib.root)
        k = len(OPEN_MODS)
        self.assertEqual(names(steps[k + 6]), ["Better Sprint", "Version"], log)
        self.assertEqual(values(steps[k + 6]), ["See below", "1.0"])
        self.assertEqual(steps[k + 7]["data"]["desc"], "Sprint for longer.")
        self.assertEqual(steps[k + 8]["data"]["page"], "mod")
        self.assertEqual(steps[k + 8]["data"]["page_mod"], "better-sprint")

    def test_more_page_and_picks_for_the_next_start(self):
        """More: Mod set and Font (picked here, used at the next start), Turn all mods off / on, What changed; Y undoes
        it all. pending.txt carries the picks; apply switches the set and the font."""
        with tempfile.TemporaryDirectory() as d:
            lib = make_library(Path(d) / "lib")
            mm.new_profile(lib, "Quiet")
            mm.use_profile(lib, mm.DEFAULT_PROFILE)
            modmenu.sync(lib)
            menu = Path(modmenu.menu_path(lib)).read_text(encoding="utf-8")
            more = OPEN_MODS + ["g sel 5", "g a", "g frame 3", "g rows"]
            steps, log = self.run_fake("sw", more + ["g sel 0", "g right", "g sel 0", "g sel 1", "g right", "g sel 2",
                                                    "g a", "modmenu", "g y", "modmenu", "g sel 0", "g right",
                                                    "g sel 1", "g right", "!sleep 150"], library=lib.root)
            pending = modmenu.read_pending(lib)
            calls = []
            applied = modmenu.apply(lib, deploy=lambda library: calls.append(library.profile) or {})
            font_json = Path(lib.root) / "font.json"
            chosen = json.loads(font_json.read_text(encoding="utf-8")).get("choice") if font_json.exists() else None
        self.assertIn("profile\tDefault\tnow\nprofile\tQuiet\n", menu)
        rows = steps[len(OPEN_MODS) + 3]
        self.assertEqual(names(rows), ["Mod set", "Font", "Turn all mods off", "Turn all mods on", "What changed",
                                       "How to use changes"], log)
        self.assertEqual(values(rows)[:4], ["Default", "Game font", "Turn off", "Turn on"])
        k = len(OPEN_MODS) + 4
        self.assertEqual(steps[k + 1]["data"]["value"], "Quiet")
        self.assertEqual(steps[k + 2]["data"]["desc"], "Uses the mod set Quiet once you use changes.")
        self.assertEqual(steps[k + 6]["data"]["result"], "done")
        st = steps[k + 7]["data"]
        self.assertEqual((st["profile"], st["profile_next"], st["font_next"]), ("Default", "Quiet", "easy-read"))
        # (the set and the font: Turn all mods off is refused while another set is picked, review finding)
        self.assertEqual(st["changes"], 2)
        self.assertEqual(steps[k + 9]["data"]["changes"], 0)  # (Y: all undone)
        self.assertEqual((pending.profile, pending.font), ("Quiet", "easy-read"))
        self.assertEqual(applied.status, "used", applied)
        self.assertEqual(calls, ["Quiet"])
        self.assertEqual(chosen, "easy-read")

    # ---- path B (a Savage Wind with sw_options_add_page)

    def test_path_b(self):
        with tempfile.TemporaryDirectory() as d:
            lib = make_library(Path(d) / "lib")
            steps, log = self.run_fake("add_page", ["modmenu", "g iat", "sw page", "sw page press 3",
                                                    "sw page set 0 1", "sw"], library=lib.root)
        st, iat, page, press, after, sw = (x.get("data", {}) for x in steps)
        self.assertEqual((st["on"], st["path"]), (True, "B"))
        self.assertEqual(iat["swapped"], 0)
        self.assertFalse(sw["pointer_kind_jump"])
        self.assertEqual([page[f"rows.{i}.title"] for i in range(page["count"])], MODS_PAGE)
        self.assertEqual((page["rows.0.kind"], page["rows.0.value"], page["rows.3.label"]), (0, 1, "Open"))
        self.assertEqual(press["pressed"], 1)
        self.assertEqual([press[f"rows.{i}.label"] for i in range(press["count"])], ["1st", "2nd", "3rd"])
        self.assertEqual([after[f"rows.{i}.title"] for i in range(3)], ["Hyrule Map Fix", "Better Sprint",
                                                                         "Stamina Hook"])
        self.assertIn("path B", log)


if __name__ == "__main__":
    unittest.main()
