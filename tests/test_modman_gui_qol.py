"""The Mod Manager window's quality-of-life parts (docs/mod-manager.md, "Saves, crashes and mod sets"), through its
Controller and the real tools/modman.py: Play without mods, the crash check with "Turn it off and Play", turn all
on/off, the last mods that worked, search, mod sets, mod info, pictures, settings (refused while the game runs).
Everything is made up in temp folders and no game starts (Popen is mocked). The one window test keeps its window
hidden (root.withdraw())."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modman as mm  # noqa: E402
import modman_gui as gui  # noqa: E402
from test_modman import FakeProc, mod_toml  # noqa: E402
from test_modman_qol import QolBase, tiny_png  # noqa: E402

CRASH_LOG = "[savagewind] Savage Wind\nhost backtrace (guest functions are f_<addr>):\n f_7100\n"


class GuiBase(QolBase):
    def setUp(self):
        super().setUp()
        self.backend = gui.Backend(str(self.libdir), mm=mm)
        self.backend._subprocess = self.backend._in_process   # (deploy too: in this process, with the mocks)
        self.ctl = gui.Controller(self.backend)

    def settle(self):
        self.assertTrue(self.ctl.settle(60), self.ctl.details_text())

    def head(self):
        return self.ctl.status.lines[0]

    def two_mods(self):
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.mod("Better Sprint", {"romfs/Actor/Pack/Hero.txt": "b"})
        self.ctl.refresh()
        self.settle()


class PlayWithoutMods(GuiBase):
    def test_starts_savage_wind_alone_after_a_backup(self):
        self.two_mods()
        self.ctl.play_without_mods()
        self.assertEqual(self.head(), "Starting the game without mods...")
        self.settle()
        self.assertEqual(len(FakeProc.started), 1)
        self.assertEqual(FakeProc.started[0].argv, [str(self.install / "savagewind.exe")])
        self.assertIn("Your save was backed up first.", self.ctl.status.lines, self.ctl.details_text())
        self.assertEqual(self.ours()[0]["reason"], "no mods")
        self.assertEqual([m["enabled"] for m in self.lib.mods], [True, True])   # (the mods stay as they are)

    def test_the_launchers_play_without_mods_backs_up_too(self):
        self.two_mods()
        self.record_start([("alpha", None)], "2026-11-10T10:00:00")
        backend = gui.LaunchBackend(str(self.libdir), str(self.install), mm=mm, running=lambda: False, menu=False,
                                    popen=FakeProc)
        backend.start_plain()
        self.assertEqual(FakeProc.started[0].argv, [str(self.install / "savagewind.exe")])
        self.assertEqual(self.ours()[0]["reason"], "no mods")
        self.assertTrue(backend.plain_notes[0].startswith("Your save was backed up first"))
        self.assertEqual((self.starts()[-1]["no_mods"], self.starts()[-1]["save"]),
                         (True, str(self.install / "save")))
        backend.start_plain()   # (the same set again: no second copy)
        self.assertEqual(len(self.ours()), 1)
        self.assertEqual(backend.plain_notes, [])
        mm.history_append(self.lib, {"type": "start", "time": "2026-11-11T10:00:00", "hash": None, "pid": 1,
                                     "mods": [{"id": "alpha", "name": "Alpha"}], "save": str(self.install / "save")})
        with mock.patch.object(mm, "backup_save", side_effect=OSError(28, "No space left")):
            with self.assertRaises(OSError):
                backend.start_plain()
        self.assertEqual(len(FakeProc.started), 2)   # (no copy: no game)


class CrashCheck(GuiBase):
    T1, T2 = "2026-11-10T10:00:00", "2026-11-12T10:00:00"

    def crashed(self):
        self.two_mods()
        self.record_start([("alpha", None)], self.T1)
        self.record_result(self.T1, "clean")
        self.record_start([("alpha", None), ("better-sprint", None)], self.T2)
        self.log(CRASH_LOG, mm._time_of({"time": self.T2}) + 60)

    def test_play_asks_and_turn_it_off_deploys_then_plays(self):
        self.crashed()
        lines, new = self.ctl.crash_check()
        self.assertEqual(lines, ["The game stopped with an error last time.", "New since then: Better-sprint."])
        self.assertEqual(new, ["Better Sprint"])
        self.ctl.turn_off_and_play()
        self.assertEqual(self.ctl.status.lines[:2], ["Turned off: Better Sprint.", "Deploying, then the game starts."])
        self.settle()
        self.assertEqual({m["id"]: m["enabled"] for m in self.lib.mods}, {"alpha": True, "better-sprint": False})
        self.assertEqual(len(FakeProc.started), 1, self.ctl.details_text())   # (deployed, then the game started)
        deploys = [h for h in self.history() if h["type"] == "deploy"]
        self.assertEqual([m["id"] for m in deploys[-1]["mods"] if m["enabled"]], ["alpha"])
        self.assertEqual(self.ctl.then_jobs, [])

    def test_no_crash_no_question(self):
        self.two_mods()
        self.assertEqual(self.ctl.crash_check(), ([], []))

    def test_a_failed_deploy_doesnt_start_the_game(self):
        self.crashed()
        with mock.patch.object(mm, "deploy", side_effect=mm.ModmanError("made-up deploy failure")):
            self.ctl.turn_off_and_play()
            self.settle()
        self.assertEqual(FakeProc.started, [])
        self.assertEqual(self.head(), "Not deployed.")
        self.assertEqual(self.ctl.then_jobs, [])


class TurnAllAndLastGood(GuiBase):
    def test_all_off_all_on_and_the_last_mods_that_worked(self):
        self.two_mods()
        self.ctl.set_all(False)
        self.assertEqual(self.ctl.status.lines, ["Every mod is off.", gui.PRESS_DEPLOY])
        self.assertEqual([r.on for r in self.ctl.listing.rows], [False, False])
        self.ctl.set_all(True)
        self.assertEqual([r.on for r in self.ctl.listing.rows], [True, True])
        self.ctl.use_last_good()
        self.assertEqual(self.ctl.status.kind, "error")
        self.assertEqual(self.head(), "Not changed.")
        self.record_start([("better-sprint", None)], "2026-11-10T10:00:00")
        self.record_result("2026-11-10T10:00:00", "clean")
        self.ctl.use_last_good()
        self.assertEqual(self.head(), "The mods from your last good start are on.")
        self.assertEqual([(r.id, r.on) for r in self.ctl.listing.rows], [("alpha", False), ("better-sprint", True)])

    def test_what_changed_and_conflicts_go_to_details(self):
        self.two_mods()
        self.ctl.show("changes")
        self.assertTrue(self.ctl.want_details)
        self.assertEqual(self.ctl.status.lines[:2], ["What changed since the last start:",
                                                     "No start yet: nothing to compare with."])
        self.ctl.show("conflicts")
        self.assertEqual(self.head(), "Which mod replaces which: see Details.")


class Search(GuiBase):
    def test_words_in_the_name_id_kind_or_version(self):
        self.two_mods()
        self.assertEqual([r.id for r in self.ctl.matching_rows("")], ["alpha", "better-sprint"])
        self.assertEqual([r.id for r in self.ctl.matching_rows("SPRINT")], ["better-sprint"])
        self.assertEqual([r.id for r in self.ctl.matching_rows("better romfs")], ["better-sprint"])
        self.assertEqual(self.ctl.matching_rows("nothing-like-it"), [])


class ModSets(GuiBase):
    def test_new_use_and_refused_while_playing(self):
        self.two_mods()
        self.assertEqual(self.ctl.profile_state(), ("Default", ["Default"]))
        before = self.ctl.list_version
        self.ctl.profile("new", "Hard mode")
        self.settle()
        self.assertEqual(self.ctl.profile_state(), ("Hard mode", ["Default", "Hard mode"]))
        self.assertGreater(self.ctl.list_version, before)   # (the window shows the set's name again)
        self.ctl.toggle("alpha")
        self.ctl.profile("use", "Default")
        self.settle()
        self.assertEqual(self.ctl.status.kind, "ok", self.ctl.details_text())
        self.assertEqual([r.on for r in self.ctl.listing.rows], [True, True])
        self.ctl.jobs["play"] = gui.Job("play", ["run"])   # (the game runs)
        self.ctl.profile("use", "Hard mode")
        self.assertEqual(self.ctl.status.lines, ["The game is running: close it first."])
        self.assertFalse(self.ctl.can("saves"))
        del self.ctl.jobs["play"]

    def test_a_bad_name_says_why(self):
        self.two_mods()
        self.ctl.profile("new", "no/slashes")
        self.settle()
        self.assertEqual(self.ctl.status.kind, "error")
        self.assertEqual(self.head(), "The mod set wasn't changed.")


class InfoPictureSettings(GuiBase):
    def test_info_goes_to_details(self):
        toml = mod_toml("someone.sprint", "Sprint", "1.2.0", data={"romfs": "romfs"}).replace(
            'version = "1.2.0"\n', 'version = "1.2.0"\nauthors = ["giruka"]\ndescription = "Runs faster."\n'
            'url = "https://example.com/sprint"\n')
        self.mod("Sprint", {"mod.toml": toml, "romfs/Pack/Title.txt": "s"})
        self.ctl.refresh()
        self.settle()
        self.ctl.show("info", self.ctl.listing.rows[0].id)
        self.assertEqual(self.head(), "Sprint: its info is in Details.")
        self.assertTrue(self.ctl.want_details)
        text = "\n".join(self.ctl.details_text())
        for want in ("Sprint 1.2.0", "About: Runs faster.", "Link: https://example.com/sprint"):
            self.assertIn(want, text)

    def test_choose_a_picture_and_forget_it(self):
        self.two_mods()
        png = self.tmp / "pic.png"
        png.write_bytes(tiny_png())
        self.ctl.choose_picture("alpha", str(png), "Alpha")
        self.assertEqual(self.head(), "Reading the picture...")
        self.settle()
        self.assertEqual(self.ctl.status.kind, "ok", self.ctl.details_text())
        self.assertTrue((self.libdir / mm.PICTURES_DIR / "alpha" / "chosen.png").is_file())
        self.assertEqual(self.ctl.status.lines, ["Picture chosen for Alpha: pic.png.", gui.PRESS_DEPLOY])
        bad = self.tmp / "not-a-picture.png"
        bad.write_bytes(b"not a png at all")
        self.ctl.choose_picture("alpha", str(bad), "Alpha")
        self.settle()
        self.assertEqual(self.head(), "The picture wasn't changed.")
        self.ctl.choose_picture("alpha", "", "Alpha")
        self.settle()
        self.assertFalse((self.libdir / mm.PICTURES_DIR / "alpha" / "chosen.png").exists())

    def test_settings_rows_set_and_refused_while_the_game_runs(self):
        if mm.feature("mod_settings", "rows") is None:
            self.skipTest("no tools/mod_settings.py")
        toml = mod_toml("someone.sprint", "Sprint", "1.0.0", data={"romfs": "romfs"}) + (
            '[[settings]]\nkey = "mode"\ntype = "choice"\nlabel = "Mode"\napplies = "deploy"\ndefault = "slow"\n'
            'choices = [{value = "slow", label = "Slow", romfs = "opt/slow"}, '
            '{value = "fast", label = "Fast", romfs = "opt/fast"}]\n')
        self.mod("Sprint", {"mod.toml": toml, "romfs/Pack/Title.txt": "s", "opt/slow/Pack/Title.txt": "slow",
                            "opt/fast/Pack/Title.txt": "fast"})
        self.ctl.refresh()
        self.settle()
        sid = self.ctl.listing.rows[0].id
        name, rows = self.ctl.setting_rows(sid)
        self.assertEqual((name, [(r.key, r.value_text) for r in rows]), ("Sprint", [("mode", "Slow")]))
        self.assertEqual(self.ctl.setting_rows("no-such-mod"), ("", []))
        self.ctl.set_setting(sid, "mode", "fast", "Sprint")
        self.assertEqual(self.ctl.status.lines[1], "It is used at your next start.")
        self.assertEqual(mm.read_setting_values(self.lib, "someone.sprint"), {"mode": "fast"})
        with mock.patch("sw_rebuild.game_running", return_value=True):
            self.ctl.set_setting(sid, "mode", "slow", "Sprint")
        self.assertEqual(self.ctl.status.kind, "error")
        self.assertEqual(self.head(), "The setting wasn't changed.")
        self.assertEqual(mm.read_setting_values(self.lib, "someone.sprint"), {"mode": "fast"})


class FontChooser(GuiBase):
    def test_choose_the_font_and_the_symbols_fix(self):
        if mm.feature("font", "choose") is None:
            self.skipTest("no tools/font.py")
        self.two_mods()
        cur, choices = self.ctl.fonts()
        self.assertEqual(cur, "Game font")
        self.assertIn("game", [k for k, _ in choices])
        self.ctl.use_font("game")
        self.assertEqual(self.ctl.status.kind, "ok", self.ctl.details_text())
        self.assertEqual(self.ctl.status.lines[-1], gui.PRESS_DEPLOY)
        self.ctl.use_font("no-such-font")
        self.assertEqual(self.head(), "The font wasn't changed.")
        before = self.ctl.font_symbols()
        self.ctl.set_font_symbols(not before)
        self.assertEqual(self.ctl.font_symbols(), not before)
        self.assertEqual(self.head(), f"Missing symbols fix: {'off' if before else 'on'}.")


class SavesInTheWindow(GuiBase):
    def test_back_up_now_then_put_it_back(self):
        self.two_mods()
        self.ctl.back_up_save()
        self.settle()
        self.assertEqual(self.ctl.status.kind, "ok", self.ctl.details_text())
        items = self.ctl.backups()
        self.assertEqual(items[0]["reason"], "by hand")
        (self.save / "option.sav").write_text("changed", encoding="utf-8")
        self.ctl.restore_save(items[0]["name"])
        self.settle()
        self.assertEqual((self.save / "option.sav").read_text(encoding="utf-8"), "options")
        aside = [p for p in self.save.parent.iterdir() if p.name.startswith("save-before-restore-")]
        self.assertEqual(len(aside), 1)
        self.assertEqual((aside[0] / "option.sav").read_text(encoding="utf-8"), "changed")   # (kept, not deleted)


class HiddenWindow(GuiBase):
    """The window itself, hidden (root.withdraw()). Its questions are answered by a script (no dialog opens)."""

    def open_window(self):
        if gui.tk is None:
            self.skipTest("no tkinter")
        try:
            root = gui.tk.Tk()
        except gui.tk.TclError as e:
            self.skipTest(f"no display: {e}")
        root.withdraw()
        self.view = None

        def shut():
            if self.view is not None:
                self.view.closed = True
            for after in root.tk.splitlist(root.tk.call("after", "info")):
                root.after_cancel(after)
            root.destroy()
            self.ctl.settle(10)

        self.addCleanup(shut)
        self.view = gui.View(root, self.ctl, size=16)
        root.update()
        self.root = root
        return self.view

    def script(self, *answers, texts=()):
        """view.ask and view.ask_text answer these, in order; each ask's answer must be one of its buttons."""
        answers, texts, asked = list(answers), list(texts), []

        def ask(title, message, detail, choices, default=0, columns=0):
            asked.append(message)
            want = answers.pop(0)
            self.assertIn(want, [v for _, v in choices], (message, choices))
            return want

        self.view.ask = ask
        self.view.ask_text = lambda title, message, initial="": texts.pop(0)
        return asked

    def test_search_box_and_mod_set_button(self):
        self.two_mods()
        view = self.open_window()
        self.assertEqual(view.tree.get_children(), ("alpha", "better-sprint"))
        self.assertEqual(view.set_button.cget("text"), "Mod set: Default")
        view.search.set("sprint")
        self.root.update()
        self.assertEqual(view.tree.get_children(), ("better-sprint",))
        self.assertIn("Search shows 1 of them.", view.summary.cget("text"))
        view.search.set("")
        self.root.update()
        self.assertEqual(view.tree.get_children(), ("alpha", "better-sprint"))
        for action in ("play_plain", "more"):
            self.assertEqual(str(view.by_action[action].cget("state")), "normal", action)
        self.assertTrue(self.root.bind("<Control-f>"))

    def test_more_menu(self):
        self.two_mods()
        view = self.open_window()
        self.script("all_off")
        view.on_more()
        self.assertEqual([r.on for r in self.ctl.listing.rows], [False, False])
        self.assertEqual(view.status_head.cget("text"), "Every mod is off.")
        self.script("info")
        view.on_more()
        self.assertEqual(view.status_head.cget("text"), "Alpha: its info is in Details.")
        self.assertFalse(self.ctl.want_details)   # (the window took it: Details opened, or said why it can't)
        self.assertEqual(self.ctl.details_job.picture, "")   # (a letter tile: no picture file)
        png = self.tmp / "pic.png"
        png.write_bytes(tiny_png())
        self.script("picture", "file")
        with mock.patch.object(gui.filedialog, "askopenfilename", return_value=str(png)):
            view.on_more()
        self.settle()
        self.assertTrue((self.libdir / mm.PICTURES_DIR / "alpha" / "chosen.png").is_file())
        if mm.feature("mod_pictures", "icon_path") is not None:   # (the 128 px picture tops the info)
            self.script("info")
            view.on_more()
            self.assertTrue(self.ctl.details_job.picture.endswith("icon-128.png"), self.ctl.details_job.picture)
            view.details_key = None
            view.render_details()
            self.assertEqual(len(view.details.image_names()), 1)
            self.assertTrue(view.details.get("1.0", "end").startswith("modman info alpha"))
        self.script("game_pictures", "title", "file")
        with mock.patch.object(gui.filedialog, "askopenfilename", return_value=str(png)):
            view.on_more()
        self.settle()
        self.assertTrue((self.libdir / mm.PICTURES_DIR / mm.TITLE_PICK / "chosen.png").is_file())
        self.script("saves", "backup")
        view.on_more()
        self.settle()
        self.assertEqual(self.ours()[0]["reason"], "by hand")
        asked = self.script("settings")
        view.on_more()
        self.assertEqual(view.status_head.cget("text"), "Alpha has no settings.")
        self.assertEqual(asked, ["What do you want to do?"])

    def test_settings_editor(self):
        if mm.feature("mod_settings", "rows") is None:
            self.skipTest("no tools/mod_settings.py")
        toml = mod_toml("someone.sprint", "Sprint", "1.0.0", data={"romfs": "romfs"}) + (
            '[[settings]]\nkey = "fast"\ntype = "toggle"\nlabel = "Fast sprint"\napplies = "deploy"\n'
            'default = false\nromfs = "opt/fast"\n'
            '[[settings]]\nkey = "mode"\ntype = "choice"\nlabel = "Mode"\napplies = "deploy"\ndefault = "slow"\n'
            'choices = [{value = "slow", label = "Slow", romfs = "opt/slow"}, '
            '{value = "quick", label = "Quick", romfs = "opt/quick"}]\n')
        self.mod("Sprint", {"mod.toml": toml, "romfs/Pack/Title.txt": "s", "opt/fast/Pack/Title.txt": "f",
                            "opt/slow/Pack/Title.txt": "s", "opt/quick/Pack/Title.txt": "q"})
        self.ctl.refresh()
        self.settle()
        view = self.open_window()
        sid = self.ctl.listing.rows[0].id
        _, rows = self.ctl.setting_rows(sid)
        self.script("settings", rows[0], "on")
        view.on_more()
        self.assertEqual(self.ctl.status.lines[1], "It is used at your next start.", self.ctl.details_text())
        self.script("settings", rows[1], "quick")
        view.on_more()
        self.assertEqual(mm.read_setting_values(self.lib, "someone.sprint"), {"fast": True, "mode": "quick"})
        with mock.patch.object(self.ctl, "game_running", return_value=True):
            asked = self.script("settings")
            view.on_more()
        self.assertEqual(view.status_head.cget("text"), "The game is running: close it first.")
        self.assertEqual(asked, ["What do you want to do?"])   # (no setting was offered)

    def test_mod_sets_menu(self):
        self.two_mods()
        view = self.open_window()
        self.script(("new",), texts=["Hard mode"])
        view.on_sets()
        self.settle()
        view.render()
        self.assertEqual(view.set_button.cget("text"), "Mod set: Hard mode")
        self.script(("use", "Default"))
        view.on_sets()
        self.settle()
        view.render()
        self.assertEqual(view.set_button.cget("text"), "Mod set: Default")
        self.script(("copy",), texts=["Default copy"])
        view.on_sets()
        self.settle()
        self.assertEqual(self.ctl.profile_state()[1], ["Default", "Default copy", "Hard mode"])
        self.script(("delete",), "Default copy", True)
        view.on_sets()
        self.settle()
        self.assertEqual(self.ctl.profile_state()[1], ["Default", "Hard mode"])

    def test_play_after_a_crash_asks_first(self):
        self.two_mods()
        self.record_start([("alpha", None)], "2026-11-10T10:00:00")
        self.record_result("2026-11-10T10:00:00", "clean")
        self.record_start([("alpha", None), ("better-sprint", None)], "2026-11-12T10:00:00")
        self.log(CRASH_LOG, mm._time_of({"time": "2026-11-12T10:00:00"}) + 60)
        view = self.open_window()
        asked = self.script(None)
        view.on_play()
        self.assertEqual(asked, ["The game stopped with an error last time."])
        self.assertEqual(FakeProc.started, [])   # (Cancel)
        self.script("plain")
        view.on_play()
        self.settle()
        self.assertEqual(FakeProc.started[0].argv, [str(self.install / "savagewind.exe")])


class RecipePickedOn(unittest.TestCase):
    """A new recipe mod picked on is built, then turned on by the window: its build never has to turn it on (the
    Dragonbreath build's old --on). The fixture is test_launcher's mod picker (Picker), borrowed, not inherited."""
    PERF = "botw-mod-loader.perf-effects"

    def setUp(self):
        import test_launcher as tl
        self.tl = tl
        tl.Picker.setUp(self)

    def make(self, *args, **kw):
        return self.tl.Play.make(self, *args, **kw)

    def test_a_new_recipe_picked_on_is_turned_on(self):
        ctl = self.make(mods=[])
        fake = self.mm

        def toggle(lever, value):   # (the recipe's build adds its mod OFF, as Dragonbreath's does without --on)
            if not any(m["id"] == self.PERF for m in fake.mods):
                fake.mods.append({"id": self.PERF, "name": "Performance: effects off", "enabled": False,
                                  "kinds": ["romfs"]})
            return 0

        fake.cmd_toggle = toggle
        with mock.patch.object(gui, "TOOLS", str(self.app / "tools")):
            ctl.install_picked([m for m in self.mods if m["id"] == self.PERF], {self.PERF: True})
        self.assertTrue(ctl.settle())
        self.assertTrue(ctl.row(self.PERF).on)
        self.assertEqual(ctl.status.lines, ["Your mods are ready.", "Press Play. You can turn them on or off below."])

    def test_a_new_recipe_left_off_gets_no_switch_step(self):
        ctl = self.make(mods=[])
        ctl.install_picked([m for m in self.mods if m["id"] == self.PERF], {self.PERF: False})
        self.assertTrue(ctl.settle())
        self.assertEqual([r.id for r in ctl.listing.rows], [])
        self.assertEqual(ctl.status.kind, "ok")


if __name__ == "__main__":
    import unittest
    unittest.main()
