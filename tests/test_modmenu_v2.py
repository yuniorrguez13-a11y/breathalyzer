"""Tests for the Mods page v2 parts of tools/modmenu.py: menu.txt's later records (symbols, font, profile, changed,
files, about) and the More page's picks in pending.txt (a mod set and a font, used by apply). Made-up libraries in temp
folders; nothing is deployed and no game is started."""
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import modman as mm  # noqa: E402
import modmenu  # noqa: E402
import romfs_mods as rm  # noqa: E402
from test_modmenu import Base  # noqa: E402


class Records(Base):
    def kinds(self, kind: str) -> list[list[str]]:
        modmenu.sync(self.lib)
        return [ln for ln in self.menu_lines() if ln[0] == kind]

    def test_mod_sets_default_first_and_the_one_in_use(self):
        self.assertEqual(self.kinds("profile"), [["profile", "Default", "now"]])
        mm.new_profile(self.lib, "Quiet")
        self.assertEqual(self.kinds("profile"), [["profile", "Default"], ["profile", "Quiet", "now"]])

    def test_fonts_and_symbols(self):
        fonts = self.kinds("font")
        self.assertEqual(fonts[0], ["font", "game", "Game font", "now"])
        self.assertIn(["font", "easy-read", "Easy-read"], fonts)
        self.assertEqual(self.kinds("symbols"), [])
        with mock.patch.object(mm, "read_launch", return_value={"made": {"fonts": {"symbols_full": True}}}):
            self.assertEqual(self.kinds("symbols"), [["symbols", "full"]])

    def test_symbols_are_kept_for_the_game(self):
        toml = Path(self.lib.mods_dir) / "better-sprint" / "mod.toml"
        toml.write_text(toml.read_text(encoding="utf-8").replace("Sprint for longer.", "Sprint {50%} longer."),
                        encoding="utf-8")
        mod = next(ln for ln in self.kinds("mod") if ln[1] == "better-sprint")
        self.assertEqual(mod[5], "Sprint {50%} longer.")
        self.assertEqual(modmenu.ascii_text("50% {x}", 40), "50 percent (x)")  # (other users: look-alikes as before)

    def test_about_lines(self):
        about = [ln[1:] for ln in self.kinds("about")]
        self.assertIn(["better-sprint", "version", "1.0"], about)
        self.assertNotIn("wiiu-trees", {a[0] for a in about})  # (no mod.toml, no version: no info)
        self.assertNotIn(modmenu.MOD_ID, {a[0] for a in about})

    def test_what_changed_since_the_last_start(self):
        mods = [{"id": "better-sprint", "enabled": True, "version": "1.0"},
                {"id": "map-base", "enabled": True, "version": "1.0"}]
        mm.history_append(self.lib, {"type": "deploy", "hash": "h1", "mods": mods})
        mm.history_append(self.lib, {"type": "start", "hash": "h1", "pid": 1})
        self.assertEqual(self.kinds("changed"),
                         [["changed", "New since your last start: Better Map, Baume Uberall ? from Wii...."]])
        mm.history_append(self.lib, {"type": "result", "hash": "h1", "result": "crash"})
        line = self.kinds("changed")[0]
        self.assertEqual(line[2], "crash")
        self.assertTrue(line[1].startswith("The game stopped with an error last time. New since then: Better Map"))
        self.assertLessEqual(len(line[1]), modmenu.NOTE_MAX)

    def test_no_start_yet_no_line(self):
        self.assertEqual(self.kinds("changed"), [])

    def test_files_from_the_last_deploy(self):
        overlay = Path(self.lib.deploy_dir) / "overlay"
        overlay.mkdir(parents=True)
        md = Path(self.lib.mods_dir)
        man = {"mods": [{"name": "Better Sprint", "path": str(md / "better-sprint" / "romfs"), "files": 3,
                         "deployed": 1},
                        {"name": "Better Map", "path": str(md / "better-map"), "files": 2, "deployed": 2}],
               "conflicts": [{"path": "a", "mods": ["Better Sprint", "Better Map"], "winner": "Better Map"},
                             {"path": "b", "mods": ["Better Sprint", "Better Map"], "winner": "Better Map"}]}
        (overlay / rm.MARKER).write_text(json.dumps(man), encoding="utf-8")
        self.assertEqual([ln[1:] for ln in self.kinds("files")],
                         [["better-sprint", "3", "1", "2", "Better Map"], ["better-map", "2", "2", "0", ""]])

    def test_before_start_writes_the_list_again(self):
        """Review finding: "New since your last start" went stale and the crash line never reached the game. modman
        run now calls before_start right before each start (only with the Mod menu on and the game closed)."""
        on = modmenu.menu_mod_on(self.lib)
        with mock.patch.object(modmenu, "sync") as s:
            self.assertEqual(modmenu.before_start(self.lib), on)
        self.assertEqual(s.called, on)
        with mock.patch.object(mm, "game_running", return_value=True), mock.patch.object(modmenu, "sync") as s:
            self.assertFalse(modmenu.before_start(self.lib))
        s.assert_not_called()
        for m in self.lib.mods:
            m["enabled"] = False
        with mock.patch.object(modmenu, "sync") as s:
            self.assertFalse(modmenu.before_start(self.lib))
        s.assert_not_called()

    def test_files_made_at_deploy_are_no_conflict(self):
        """Review finding: a size table rebuilt at deploy (generated:pictures) counted as "replaced by a later mod"."""
        overlay = Path(self.lib.deploy_dir) / "overlay"
        overlay.mkdir(parents=True)
        md = Path(self.lib.mods_dir)
        man = {"mods": [{"name": "Better Sprint", "path": str(md / "better-sprint"), "files": 3, "deployed": 2},
                        {"name": "Better Map", "path": str(md / "better-map"), "files": 2, "deployed": 1}],
               "files": {"System/Resource/ResourceSizeTable.product.srsizetable": {"mod": "generated:pictures"},
                         "Pack/Bootup.pack": {"mod": mm.MERGED_OWNER}},
               "conflicts": [{"path": "System/Resource/ResourceSizeTable.product.srsizetable",
                              "mods": ["Better Sprint", "Better Map"], "winner": "Better Map"},
                             {"path": "Pack/Bootup.pack", "mods": ["Better Sprint", "Better Map"],
                              "winner": "Better Map"}]}
        (overlay / rm.MARKER).write_text(json.dumps(man), encoding="utf-8")
        self.assertEqual([ln[1:] for ln in self.kinds("files")],
                         [["better-sprint", "3", "2", "0", ""], ["better-map", "2", "1", "0", ""]])

    def test_pictures_from_the_atlas(self):
        self.assertEqual(self.kinds("picgrid"), [])
        pics = Path(self.lib.deploy_dir) / "pictures"
        pics.mkdir(parents=True)
        atlas = {"format": 1, "grids": [{"tex": "BzModIcons_00^l", "w": 512, "h": 512, "cols": 4, "rows": 4,
                                         "pane": "P_ModPanel_00"},
                                        {"tex": "BzModBg_00^l", "w": 1280, "h": 720, "cols": 4, "rows": 4}],
                 "cells": {"better-sprint": {"icon": 0, "bg": -1}, "_mods-page": {"icon": -1, "bg": 0},
                           modmenu.MOD_ID: {"icon": 1, "bg": -1}, "gone": {"icon": 2, "bg": -1}}}
        (pics / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
        self.assertEqual([ln[1:] for ln in self.kinds("picgrid")],
                         [["BzModIcons_00^l", "512", "512", "4", "4"], ["BzModBg_00^l", "1280", "720", "4", "4"]])
        self.assertEqual([ln[1:] for ln in self.kinds("pic")], [["better-sprint", "0", "-1"], ["_mods-page", "-1", "0"]])

    def test_a_failing_part_leaves_only_its_rows_out(self):
        with mock.patch.object(mm, "profile_names", side_effect=RuntimeError("boom")):
            modmenu.sync(self.lib)
        kinds = {ln[0] for ln in self.menu_lines()}
        self.assertNotIn("profile", kinds)
        self.assertIn("mod", kinds)
        self.assertIn("font", kinds)


class Ids(unittest.TestCase):
    """The plan's message id layout (section 4): the tested ids stay, rows and choice values get blocks of their own,
    all under 99,999, and the Mods page's most rows fit."""

    def test_layout(self):
        src = (REPO / "native" / "examples" / "modmenu" / "opt_page_a.c").read_text(encoding="utf-8")
        head = (REPO / "native" / "examples" / "modmenu" / "opt_page.h").read_text(encoding="utf-8")

        def num(text, name):
            return int(re.search(rf"#define {name} (\d+)", text).group(1))
        self.assertEqual([num(src, n) for n in ("MSG_MODS", "MSG_VALUE", "MSG_ROW", "MSG_SLOT", "MSG_HI")],
                         [90000, 90100, 91000, 92000, 99999])
        rows, slots, values = num(head, "MM_PAGE_ROWS"), num(head, "MM_SLOTS"), num(head, "MM_SLOT_VALUES")
        self.assertLessEqual(91000 + 4 * rows, 92000)
        self.assertLessEqual(92000 + values * slots - 1, 99999)
        self.assertGreaterEqual(values, 201)  # (a slider of 201 steps)
        self.assertGreaterEqual(rows, num(head, "MM_PAGE_MODS") + 7)  # (mods, stale, note, more, 4 buttons)


class Picks(Base):
    def setUp(self):
        super().setUp()
        mm.new_profile(self.lib, "Quiet")
        mm.use_profile(self.lib, mm.DEFAULT_PROFILE)
        modmenu.sync(self.lib)
        self.listed = [ln for ln in self.menu_lines() if ln[0] == "mod" and "hidden" not in ln[3]]
        self.deployed = []

    def mod_lines(self, changes=None) -> list[str]:
        changes = changes or {}
        return [f"mod\t{ln[1]}\t{changes.get(ln[1], ln[2])}" for ln in self.listed]

    def test_pending_reads_the_picks(self):
        self.pending(self.mod_lines() + ["profile\tQuiet", "font\teasy-read"])
        p = modmenu.read_pending(self.lib)
        self.assertEqual((p.profile, p.font), ("Quiet", "easy-read"))
        self.pending(self.mod_lines() + ["profile\t../x", "font\tC:\\evil"])
        p = modmenu.read_pending(self.lib)
        self.assertEqual((p.profile, p.font), ("", ""))

    def test_sync_keeps_the_picks(self):
        self.pending(self.mod_lines() + ["profile\tQuiet", "font\teasy-read"])
        modmenu.sync(self.lib)
        p = modmenu.read_pending(self.lib)
        self.assertEqual((p.profile, p.font), ("Quiet", "easy-read"))
        lines = modmenu.show_lines(self.lib)
        self.assertIn("Mod set at your next start: Quiet", lines)
        self.assertIn("Font at your next start: easy-read", lines)

    def test_a_pick_of_whats_in_use_is_dropped(self):
        self.pending(self.mod_lines() + ["profile\tDefault", "font\tgame"])
        modmenu.sync(self.lib)
        self.assertFalse(os.path.exists(modmenu.menu_path(self.lib, modmenu.PENDING)))

    def test_apply_switches_and_never_puts_the_changes_in_the_old_set(self):
        """Review finding: mod changes made on the list of the set in use, with another set picked, went quietly to
        the old set. Now they are not used, and the note says so."""
        self.pending(self.mod_lines({"better-sprint": "off"}) + ["profile\tQuiet", "font\teasy-read"])
        a = modmenu.apply(self.lib, lambda lib: self.deployed.append(lib.profile) or {})
        self.assertEqual(a.status, "used", a)
        self.assertIn("Mod set: Quiet", a.changes)
        self.assertIn("Font: easy-read", a.changes)
        self.assertEqual(a.note, "Mod changes not used: the mod set is now Quiet. Change its mods now.")
        self.assertEqual(self.deployed, ["Quiet"])
        self.assertEqual(self.reload().profile, "Quiet")
        default = mm.read_profile(self.lib, mm.DEFAULT_PROFILE)
        self.assertTrue(next(m for m in default["mods"] if m["id"] == "better-sprint")["enabled"])
        font = json.loads((self.root / "font.json").read_text(encoding="utf-8"))
        self.assertEqual(font["choice"], "easy-read")
        self.assertFalse(os.path.exists(modmenu.menu_path(self.lib, modmenu.PENDING)))

    def test_a_refused_switch_is_said_and_the_rest_is_used(self):
        self.pending(self.mod_lines({"better-sprint": "off"}) + ["profile\tQuiet"])
        with mock.patch.object(mm, "use_profile", side_effect=mm.ModmanError("No mod set 'Quiet'.")):
            a = modmenu.apply(self.lib, lambda lib: self.deployed.append(lib.profile) or {})
        self.assertEqual(a.note, "No mod set 'Quiet'.")
        self.assertNotIn("Mod set: Quiet", a.changes)
        self.assertEqual(self.deployed, [mm.DEFAULT_PROFILE])
        self.assertFalse(next(m for m in self.reload().mods if m["id"] == "better-sprint")["enabled"])

    def test_a_failed_deploy_puts_the_old_sets_values_back(self):
        """Review finding: after a failed deploy the old set was in use again with the picked set's settings and
        font."""
        sdir = self.root / mm.SETTINGS_DIR
        sdir.mkdir(exist_ok=True)
        (sdir / "better-sprint.json").write_text('{"speed": 3}', encoding="utf-8")
        font_file = self.root / "font.json"
        font_before = font_file.read_bytes() if font_file.exists() else None
        self.pending(self.mod_lines() + ["profile\tQuiet", "font\teasy-read"])

        def fail(lib):
            raise mm.ModmanError("Can't deploy: the disk is full.")
        a = modmenu.apply(self.lib, fail)
        self.assertEqual(a.status, "failed", a)
        self.assertEqual(self.reload().profile, mm.DEFAULT_PROFILE)
        self.assertEqual((sdir / "better-sprint.json").read_text(encoding="utf-8"), '{"speed": 3}')
        self.assertEqual(font_file.read_bytes() if font_file.exists() else None, font_before)
        self.assertTrue(os.path.exists(modmenu.menu_path(self.lib, modmenu.PENDING)))

    def test_a_switch_cut_off_partway_leaves_the_set_in_use_as_it_was(self):
        """Second review #1: with no mod changes waiting, a switch that failed after writing some of the picked
        set's values deployed the picked set's mod list (in memory) with the mixed values."""
        sdir = self.root / mm.SETTINGS_DIR
        sdir.mkdir(exist_ok=True)
        (sdir / "better-sprint.json").write_text('{"speed": 3}', encoding="utf-8")
        mods_before = [dict(m) for m in self.lib.mods]
        self.pending(self.mod_lines() + ["profile\tQuiet"])

        def half_switch(lib, name):
            (sdir / "better-sprint.json").write_text('{"speed": 9}', encoding="utf-8")
            (sdir / "picked-only.json").write_text('{"x": 1}', encoding="utf-8")
            for m in lib.mods:
                m["enabled"] = not m.get("enabled", True)
            lib.profile = name
            raise OSError(28, "No space left on device")
        with mock.patch.object(mm, "use_profile", side_effect=half_switch):
            a = modmenu.apply(self.lib, lambda lib: self.deployed.append((lib.profile, [dict(m) for m in lib.mods]))
                              or {})
        self.assertNotIn("Mod set: Quiet", a.changes)
        self.assertEqual(self.deployed, [(mm.DEFAULT_PROFILE, mods_before)])
        self.assertEqual((sdir / "better-sprint.json").read_text(encoding="utf-8"), '{"speed": 3}')
        self.assertFalse((sdir / "picked-only.json").exists())
        self.assertEqual(self.reload().profile, mm.DEFAULT_PROFILE)


if __name__ == "__main__":
    unittest.main()
