"""The Mod menu mod (native/examples/modmenu) against a made-up Savage Wind and game library: no game, no Savage Wind
process. It builds modhost, the mod and the fakes in native/examples/modmenu/selftest with the zig that ships with
Savage Wind (at below-normal priority), then drives them with build_native's fake-game harness: the fake game library
calls the fake savagewind.exe's rt_opt_* functions through its import table, as swgame.dll does.

Skipped when Savage Wind's zig or savagewind.dll.a isn't there (SWMOD_INSTALL, else Documents/SavageWind/Savage Wind).
The library it uses is made up, in a temp folder.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import build_native as bn  # noqa: E402
import modman as mm  # noqa: E402
import modmenu  # noqa: E402

ST = REPO / "native" / "examples" / "modmenu" / "selftest"
FAKE_RUNTIME = bn.HOST_SRC / "selftest" / "fake_runtime.c"
OUT = REPO / "build" / "native" / "modmenu-selftest"
VARIANTS = {"sw": (), "other_version": ("FAKE_OTHER_VERSION",), "other_bytes": ("FAKE_OTHER_BYTES",),
            "add_page": ("FAKE_ADD_PAGE",)}
ENV = {"FAKE_MAX_CMDS": "32", "SWMOD_MODMENU_WRITE_MS": "0"}

FIRST_PAGE = ["Fake page one", "Fake page two", "Mods", "Quit game"]
MODS_PAGE = ["Better Sprint", "Hyrule Map Fix", "Stamina Hook", "Change order", "How to use changes"]
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
        self.assertEqual(values(self.s["rows_mods"])[3:], ["Open", "See below"])

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
        self.assertEqual(self.d("sel_o2")["desc"], "Later mods win when two mods change the same file.")

    def test_the_description_line_fits(self):
        """The game test: the game shows about 82 characters of the line and cuts the rest at a word, and its font has
        no { or }. A long "what it does" is shortened, never the change; { } become ( )."""
        with tempfile.TemporaryDirectory() as d:
            lib = self.library(d)
            p = Path(modmenu.menu_path(lib))
            long_about = "Keeps stamina {up} for a long time, also while you climb, swim or run."
            note = "Adds one new text file, System/modmenu-note.txt, to the game files."
            text = p.read_text(encoding="utf-8").replace("Keeps stamina up.", long_about)
            p.write_text(text.replace("Sprint for longer.", note), encoding="utf-8")
            steps, _ = self.run_fake("sw", OPEN_MODS + ["g sel 2", "g a", "g sel 2", "g sel 4", "g sel 0", "g a",
                                                        "g sel 0"], library=lib.root)
        self.assertEqual(steps[5]["data"]["desc"], long_about.replace("{", "(").replace("}", ")"))
        self.assertEqual(steps[7]["data"]["desc"],
                         "Keeps stamina... Turns on once you use changes. Next start takes 10-20 minutes.")
        self.assertEqual(steps[9]["data"]["desc"], note)
        self.assertEqual(steps[11]["data"]["desc"], "Adds one new text file... Turns off once you use changes.")
        for k in (5, 7, 8, 9, 11):
            self.assertLessEqual(len(steps[k]["data"]["desc"]), 80)

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
        self.assertEqual(self.d("sw_click2")["kinds"], "00022")
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
            lines = OPEN_MODS + ["g sel 4", "g sel 0", "g a", "g sel 0", "!sleep 100"]
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
