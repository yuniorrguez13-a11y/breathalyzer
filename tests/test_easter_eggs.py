"""Tests for the easter eggs in the launcher (bundled-mods.toml egg = true): secret, so never listed one by one. The
mod picker has one switch, "Enable easter eggs?" (OFF at first), and Home one Easter eggs row; either turns every egg
on or off together (tools/launcher.py read_bundled, tools/modman_gui.py LaunchController and LauncherView).

A fake modman and made-up folders in a temp folder: never the real Savage Wind, mod library, settings or game. The
window tests use a hidden window (root.withdraw(), and the calls that could show it are switched off): nothing ever
shows on the screen."""
import gc
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / "tools"))
sys.path.insert(0, str(TESTS))
import launcher as la  # noqa: E402
import make_release as mr  # noqa: E402
import modman_gui as gui  # noqa: E402
import test_launcher as tl  # noqa: E402 (its made-up Savage Wind, app folder and fake modman; never its window tests)
import test_modman_gui as tm  # noqa: E402
from test_breath import hidden_root, texts  # noqa: E402

SECRET = ("Secret word", "Hidden door", "botw-mod-loader.zelda-view", "giruka.hidden-door")
NORMAL = {"id": "botw-mod-loader.modmenu", "title": "Mods page in the game", "default": "on", "kind": "code",
          "description": "Adds a Mods page to the game's Options.", "folder": "bundled-mods/modmenu", "tags": []}
EGG = {"id": "botw-mod-loader.zelda-view", "title": "Secret word", "default": "on", "kind": "code",   # (on: eggs
       "description": "A hidden easter egg for the game's console.", "folder": "bundled-mods/zelda-view",  # start OFF
       "tags": ["Experimental"], "why": "Made up.", "egg": True}                                           # anyway)
EGG_RECIPE = {"id": "giruka.hidden-door", "title": "Hidden door", "default": "off", "kind": "recipe",
              "description": "A made-up egg built from the game files.", "recipe": "castle",
              "build": [["quality.py", "toggle", "door", "on"]], "egg": True}
# A mod with a bonus built when it is first turned on (the multiplayer mod and its secret extra)
MP = {"id": "botw-mod-loader.ghost", "title": "Ghost multiplayer", "default": "off", "kind": "code",
      "description": "See a friend as a second Link.", "folder": "bundled-mods/ghost", "tags": [],
      "after_on": [["quality.py", "toggle", "bonus", "on"]], "after_on_makes": "giruka.bonus"}
BONUS = {"id": "giruka.bonus", "name": "Extra item", "enabled": True, "kinds": ["romfs"], "secret": True}


class EggModman(tl.LaunchModman):
    """The launcher's fake modman; quality.py toggle door builds the made-up recipe egg into the library."""

    def cmd_toggle(self, lever, value):
        if lever == "bonus":   # (the bonus build: a secret mod, as tools/ak47_build.py adds)
            if not any(m["id"] == BONUS["id"] for m in self.mods):
                self.mods.append(dict(BONUS))
            print("Done: the extra is built and in your library.")
            return 0
        if lever != "door":
            return super().cmd_toggle(lever, value)
        if not any(m["id"] == EGG_RECIPE["id"] for m in self.mods):
            self.mods.append({"id": EGG_RECIPE["id"], "name": EGG_RECIPE["title"], "enabled": True,
                              "kinds": ["romfs"]})
        print(f"{lever} {value}")
        return 0


class Temp(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(prefix="eggs-test-")
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)


class Bundled(Temp):
    def test_egg_flag_and_eggs_always_start_off(self):
        app = tl.make_app(self.tmp / "app", [NORMAL, EGG, dict(NORMAL, id="x.notegg", egg="yes")])
        _, mods = la.read_bundled(str(app))
        self.assertEqual([(m["id"], m["egg"], m["on"]) for m in mods],
                         [(NORMAL["id"], False, True), (EGG["id"], True, False), ("x.notegg", False, True)])

    def test_the_real_list_marks_the_secret_word_an_egg(self):
        data = tomllib.loads((TESTS.parent / "tools" / "packaging" / "bundled-mods.toml").read_text(encoding="utf-8"))
        mods = {m["id"]: m for m in data["mod"]}
        self.assertIs(mods["botw-mod-loader.zelda-view"].get("egg"), True)
        for m in mods.values():
            self.assertLessEqual(set(m), mr.BUNDLED_KEYS, m["id"])
            if m.get("egg"):
                self.assertEqual(m["default"], "off", m["id"])   # (the switch starts OFF: say so here too)


class Controller(Temp):
    def make(self, mods=()):
        self.app = tl.make_app(self.tmp / "app", [NORMAL, EGG, EGG_RECIPE])
        self.key, self.bundled = la.read_bundled(str(self.app))
        self.eggs = [m for m in self.bundled if m["egg"]]
        self.mm = EggModman(list(mods))
        self.mm.deployed = self.mm.inputs_hash(None)
        self.spawn = tm.FakeSpawn(self.mm)
        backend = gui.LaunchBackend(None, "Z:\\SW", mm=self.mm, spawn=self.spawn, python="py", running=lambda: False,
                                    menu=False, popen=mock.Mock())
        self.ctl = gui.LaunchController(backend, clock=tm.Clock(), egg_ids={m["id"] for m in self.eggs})
        return self.ctl

    def install(self, picks):
        with mock.patch.object(gui, "TOOLS", str(self.app / "tools")):
            self.ctl.install_picked(self.bundled, picks)
            self.assertTrue(self.ctl.settle())

    def shown(self):
        return [(r.id, r.name, r.on) for r in self.ctl.shown_rows()]

    def test_home_rows_hide_the_eggs_behind_one_row(self):
        ctl = self.make()
        self.assertEqual(self.shown(), [(gui.EGG_ROW, "Easter eggs", False)])   # (the switch shows before they are in)
        self.install({NORMAL["id"]: True, EGG["id"]: False})
        # (left OFF: never put in the library, so nothing lists it, not even the game's Mods page)
        self.assertNotIn(EGG["id"], [r.id for r in ctl.listing.rows])
        self.assertNotIn(EGG_RECIPE["id"], [r.id for r in ctl.listing.rows])   # (a recipe left off isn't built)
        self.assertNotIn("bundled-mods/zelda-view", str(self.spawn.calls))
        self.assertEqual(self.shown(), [(NORMAL["id"], "Mods page in the game", True),
                                        (gui.EGG_ROW, "Easter eggs", False)])
        self.assertEqual(ctl.shown_row(gui.EGG_ROW).about, gui.EGG_LINE)
        self.assertTrue(ctl.summary_line().startswith("1 of 2 mods on."))   # (the eggs count as the one row)
        self.assertIsNone(ctl.shown_row(EGG["id"]))
        self.install({EGG["id"]: True})
        self.assertIsNone(ctl.shown_row(EGG["id"]))
        plain = gui.LaunchController(ctl.backend, clock=tm.Clock())   # (no eggs in this edition: listed as usual)
        self.assertIn(EGG["id"], [r.id for r in plain.shown_rows()])

    def test_the_names_stay_secret_while_they_go_in(self):
        ctl = self.make()
        seen = []
        real = ctl._next

        def spy():
            real()
            seen.append(list(ctl.status.lines))

        ctl._next = spy
        self.install({NORMAL["id"]: True, EGG["id"]: True, EGG_RECIPE["id"]: True})
        self.assertIn(["Adding mod 2 of 3...", "Easter eggs"], seen)
        self.assertFalse([x for lines in seen for x in lines if any(s in x for s in SECRET)], seen)
        # (Details: modman's own lines name the mod; for an egg its id and title are left out)
        self.assertFalse([x for x in ctl.details_text() if any(s in x for s in SECRET)], ctl.details_text())
        on = {r.id: r.on for r in ctl.listing.rows}
        self.assertEqual(on, {NORMAL["id"]: True, EGG["id"]: True, EGG_RECIPE["id"]: True})
        self.assertEqual(ctl.shown_row(gui.EGG_ROW).on, True)

    def test_the_switch_turns_every_egg_on_and_off(self):
        ctl = self.make()
        self.install({NORMAL["id"]: True, EGG["id"]: False})
        # on: the recipe egg isn't in the library yet, so they go in as the picker's Save puts them in
        with mock.patch.object(gui, "TOOLS", str(self.app / "tools")):
            ctl.set_eggs(self.eggs, True)
            self.assertTrue(ctl.settle())
        self.assertEqual({r.id: r.on for r in ctl.listing.rows if r.id in ctl.egg_ids},
                         {EGG["id"]: True, EGG_RECIPE["id"]: True})
        self.assertEqual(ctl.status.lines, gui.EGG_DONE[True])
        self.assertEqual(ctl.take_select(), gui.EGG_ROW)
        # off: at once, as Turn off does, nothing added or built
        n = len(self.spawn.calls)
        ctl.set_eggs(self.eggs, False)
        self.assertEqual(len(self.spawn.calls), n)
        self.assertEqual({r.id: r.on for r in ctl.listing.rows if r.id in ctl.egg_ids},
                         {EGG["id"]: False, EGG_RECIPE["id"]: False})
        self.assertEqual((ctl.status.kind, ctl.status.lines), ("ok", gui.EGG_DONE[False]))
        self.assertTrue(ctl.row(NORMAL["id"]).on)   # (the others stay as they are)
        self.assertFalse(any(s in " ".join(ctl.details_job.args) for s in SECRET))
        self.assertFalse([x for x in ctl.details_text() if any(s in x for s in SECRET)], ctl.details_text())
        # on again: all of them are there now, so at once too
        ctl.set_eggs(self.eggs, True)
        self.assertEqual(len(self.spawn.calls), n)
        self.assertTrue(ctl.shown_row(gui.EGG_ROW).on)

    def test_developer_names_stay_secret(self):
        ctl = self.make()
        self.install({NORMAL["id"]: True, EGG["id"]: True})
        ctl.dev_ids.add(EGG["id"])
        self.assertEqual(ctl.dev_mods_on(), ["Easter eggs"])


class Secret(Temp):
    """[mod] secret mods (an egg's, a bonus's parts) are never listed; a mod's bonus (after_on) is built only once
    the mod is turned on."""

    def make(self, mods=()):
        self.app = tl.make_app(self.tmp / "app", [NORMAL, MP])
        self.key, self.bundled = la.read_bundled(str(self.app))
        self.mm = EggModman(list(mods))
        self.mm.deployed = self.mm.inputs_hash(None)
        self.spawn = tm.FakeSpawn(self.mm)
        backend = gui.LaunchBackend(None, "Z:\\SW", mm=self.mm, spawn=self.spawn, python="py", running=lambda: False,
                                    menu=False, popen=mock.Mock())
        self.ctl = gui.LaunchController(backend, clock=tm.Clock(), after_on=self.bundled)
        return self.ctl

    def bonus_runs(self):
        return [argv for argv, _ in self.spawn.calls if "bonus" in argv]

    def test_read_bundled_keeps_after_on(self):
        self.make()
        mp = next(m for m in self.bundled if m["id"] == MP["id"])
        self.assertEqual((mp["after_on"], mp["after_on_makes"]), (MP["after_on"], MP["after_on_makes"]))
        app = tl.make_app(self.tmp / "app2", [dict(MP, after_on_makes="")])   # (no id it adds: left out)
        self.assertEqual(la.read_bundled(str(app))[1], [])

    def test_secret_rows_are_never_listed(self):
        ctl = self.make([{"id": "a", "name": "Alpha", "enabled": True, "kinds": ["romfs"]}, dict(BONUS)])
        self.assertEqual([r.id for r in ctl.listing.rows], ["a", BONUS["id"]])   # (the launcher keeps them...)
        self.assertTrue(ctl.row(BONUS["id"]).secret)
        self.assertEqual([r.id for r in ctl.shown_rows()], ["a"])                 # (... Home lists none)
        self.assertTrue(ctl.summary_line().startswith("1 of 1 mod on."), ctl.summary_line())
        more = gui.Backend(None, mm=self.mm).listing()                            # (More... and Mod Manager)
        self.assertEqual([r.id for r in more.rows], ["a"])

    def test_more_moves_past_a_secret_mod(self):
        mods = [{"id": "a", "name": "Alpha", "enabled": True, "kinds": ["romfs"]}, dict(BONUS),
                {"id": "b", "name": "Beta", "enabled": True, "kinds": ["romfs"]}]
        self.make(mods)
        ctl = gui.Controller(gui.Backend(None, mm=self.mm), clock=tm.Clock())
        self.assertEqual([(r.id, r.number) for r in ctl.listing.rows], [("a", 1), ("b", 3)])
        ctl.move("b", -1)
        self.assertEqual([m["id"] for m in self.mm.mods], ["b", "a", BONUS["id"]])
        ctl.move("b", -1)
        self.assertEqual(ctl.status.lines[0], "Beta is already first.")

    def test_bonus_waits_until_the_mod_is_turned_on(self):
        ctl = self.make()
        self.assertEqual(self.mm.calls, [])
        with mock.patch.object(gui, "TOOLS", str(self.app / "tools")):
            ctl.install_picked(self.bundled, {NORMAL["id"]: True, MP["id"]: False})
            self.assertTrue(ctl.settle())
        self.assertEqual(self.bonus_runs(), [])                     # (multiplayer left off: never built)
        self.assertNotIn(BONUS["id"], [m["id"] for m in self.mm.mods])
        with mock.patch.object(gui, "TOOLS", str(self.app / "tools")):
            ctl.install_picked(self.bundled, {NORMAL["id"]: True, MP["id"]: True})   # (Pick again: ON)
            self.assertTrue(ctl.settle())
        self.assertEqual(len(self.bonus_runs()), 1)
        self.assertEqual({m["id"]: m["enabled"] for m in self.mm.mods if m["id"] in (MP["id"], BONUS["id"])},
                         {MP["id"]: True, BONUS["id"]: True})
        self.assertEqual([r.id for r in ctl.shown_rows()], [NORMAL["id"], MP["id"]])
        self.assertFalse([x for x in ctl.details_text() if BONUS["id"] in x], ctl.details_text())
        with mock.patch.object(gui, "TOOLS", str(self.app / "tools")):   # (there now: never built again)
            ctl.install_picked(self.bundled, {NORMAL["id"]: True, MP["id"]: True})
            self.assertTrue(ctl.settle())
        self.assertEqual(len(self.bonus_runs()), 1)

    def test_play_builds_the_bonus_of_a_mod_turned_on_on_home(self):
        ctl = self.make()
        with mock.patch.object(gui, "TOOLS", str(self.app / "tools")):
            ctl.install_picked(self.bundled, {NORMAL["id"]: True, MP["id"]: False})
            self.assertTrue(ctl.settle())
        self.mm.deployed = self.mm.inputs_hash(None)
        ctl.toggle(MP["id"])                                        # (Home: Turn on)
        self.assertTrue(ctl.row(MP["id"]).on)
        self.assertEqual(self.bonus_runs(), [])
        seen = []
        real = ctl._next

        def spy():
            real()
            seen.append(list(ctl.status.lines))

        ctl._next = spy
        with mock.patch.object(gui, "TOOLS", str(self.app / "tools")):
            ctl.launch()
            self.assertTrue(ctl.settle())
        self.assertEqual(len(self.bonus_runs()), 1)
        scripts = [os.path.basename(argv[2]) + " " + argv[3] for argv, _ in self.spawn.calls][-2:]
        self.assertEqual(scripts, ["quality.py toggle", "modman.py deploy"])   # (the bonus first, then the deploy)
        self.assertIn(["Getting your mods ready...", gui.BONUS_SAY], seen)
        self.assertIn(BONUS["id"], [m["id"] for m in self.mm.mods])
        self.assertEqual(self.mm.calls[-1][0], "run")
        # the next Play: built already, nothing more
        n = len(self.spawn.calls)
        with mock.patch.object(gui, "TOOLS", str(self.app / "tools")):
            ctl.launch()
            self.assertTrue(ctl.settle())
        self.assertEqual(len(self.bonus_runs()), 1)
        self.assertFalse([c for c, _ in self.spawn.calls[n:] if "bonus" in c])

    def test_a_bonus_that_fails_never_stops_play(self):
        ctl = self.make([dict(id=MP["id"], name=MP["title"], enabled=True, kinds=["native"])])
        real = self.mm.cmd_toggle
        self.mm.cmd_toggle = lambda lever, value: 1 if lever == "bonus" else real(lever, value)
        with mock.patch.object(gui, "TOOLS", str(self.app / "tools")):
            ctl.launch()
            self.assertTrue(ctl.settle())
        self.assertEqual(self.mm.calls[-1][0], "run")              # (played without it)


class HiddenWindow(Temp):
    """LauncherView in a window that never shows: the picker and Home with eggs."""

    def open(self, mods=()):
        root = hidden_root()
        self.app = tl.make_app(self.tmp / "app", [NORMAL, EGG, EGG_RECIPE])
        sw = tl.make_install(self.tmp / "SW")
        self.mm = EggModman(list(mods))
        self.mm.deployed = self.mm.inputs_hash(None)
        self.spawn = tm.FakeSpawn(self.mm)

        def backend(lib, install):
            self.mm.installs.add(install)
            return gui.LaunchBackend(lib, install, mm=self.mm, spawn=self.spawn, python="py", running=lambda: False,
                                     menu=False, popen=mock.Mock())

        settings = la.Settings(install=str(sw))
        first = la.FirstRun(settings, places=lambda: {"near": []},
                            check=lambda f, lib, why: tl.check(f, lib or str(self.tmp / "lib"), why, tmp=self.tmp))
        try:
            view = gui.LauncherView(root, settings, str(self.tmp / "home" / la.SETTINGS_FILE), first_run=first,
                                    make_backend=backend, app_dir=str(self.app))
        except BaseException:
            tm.close(root)
            raise
        view.ask = mock.Mock(return_value=None)        # (a dialog would be a new window: never in tests)
        view.ask_text = mock.Mock(return_value=None)
        self.addCleanup(gc.collect)
        self.addCleanup(tm.close, root, view)
        root.update()
        self.assertEqual(root.state(), "withdrawn")
        return root, view

    def save(self, root, view):
        save = next(b for b in view.picker_buttons if b.cget("text") == "Save")
        with mock.patch.object(gui, "TOOLS", str(self.app / "tools")):
            save.invoke()
            self.assertTrue(view.ctl.settle())
        view.render()
        root.update()

    def home(self, view):
        """Home's list: (ON/OFF, the row's full name; the hidden window is narrow, so the cells are cut)."""
        names = {r.id: r.name for r in view.ctl.shown_rows()}
        return [(view.tree.item(i, "values")[0], names[i]) for i in view.tree.get_children()]

    def test_picker_has_one_switch_off_at_first(self):
        root, view = self.open()
        self.assertEqual(view.screen, "picker")   # (Breathalyzer Plus: the picker before the first Play)
        self.assertEqual(list(view.pick_buttons), [NORMAL["id"], gui.EGG_ROW])
        self.assertEqual(view.pick_buttons[gui.EGG_ROW].cget("text"), "OFF")
        words = texts(view.frames["picker"])
        self.assertIn("Enable easter eggs?", words)
        self.assertIn("Secret extras. Some take longer the first time you press Play.", words)
        self.assertFalse([w for w in words if any(s in w for s in SECRET)], words)
        self.save(root, view)   # (left OFF: neither egg goes in, so nothing lists them)
        self.assertEqual(view.screen, "home")
        self.assertEqual({r.id: r.on for r in view.ctl.listing.rows}, {NORMAL["id"]: True})
        self.assertEqual(self.home(view), [("ON", "Mods page in the game"), ("OFF", "Easter eggs")])
        self.assertEqual(root.state(), "withdrawn")

    def test_switch_on_enables_every_egg_and_home_shows_one_row(self):
        root, view = self.open()
        view.pick_buttons[gui.EGG_ROW].invoke()
        self.assertEqual(view.pick_buttons[gui.EGG_ROW].cget("text"), "ON")
        self.save(root, view)
        self.assertEqual({r.id: r.on for r in view.ctl.listing.rows},
                         {NORMAL["id"]: True, EGG["id"]: True, EGG_RECIPE["id"]: True})
        self.assertEqual(self.home(view), [("ON", "Mods page in the game"), ("ON", "Easter eggs")])
        self.assertFalse([w for w in texts(view.frames["home"]) if any(s in w for s in SECRET)])
        # Home's Easter eggs row: Space turns every egg off, and on again
        view.tree.selection_set(gui.EGG_ROW)
        view.render_about()
        self.assertTrue(view.about.cget("text").startswith("Easter eggs:"), view.about.cget("text"))
        self.assertEqual(view.by_action["toggle"].cget("text"), "Turn off")
        view.on_toggle()
        root.update()
        self.assertEqual({r.id: r.on for r in view.ctl.listing.rows if r.id in view.ctl.egg_ids},
                         {EGG["id"]: False, EGG_RECIPE["id"]: False})
        self.assertEqual(self.home(view), [("ON", "Mods page in the game"), ("OFF", "Easter eggs")])
        self.assertEqual(view.selected_id(), gui.EGG_ROW)
        self.assertEqual(view.status_head.cget("text"), "Easter eggs are off.")
        view.on_toggle()
        root.update()
        self.assertEqual(self.home(view)[-1], ("ON", "Easter eggs"))
        # never exported; the picker again shows the switch ON (an egg is on), Settings counts only the others
        view.on_export()
        self.assertEqual(view.ctl.status.lines[0], "Easter eggs can't be exported.")
        view.show_settings()
        self.assertIn("1 come with Breathalyzer", texts(view.frames["settings"]))
        view.show_picker(from_settings=True)
        self.assertEqual(view.pick_buttons[gui.EGG_ROW].cget("text"), "ON")
        self.assertEqual(root.state(), "withdrawn")


if __name__ == "__main__":
    unittest.main()
