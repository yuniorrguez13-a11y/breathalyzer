"""Tests for tools/modmenu.py (the in-game mod menu's files, and using the player's changes). Every library and mod
here is made up, in temp folders. No game is started and nothing is deployed: modman's deploy and run are replaced,
and "is Savage Wind running" is answered by the test."""
import contextlib
import errno
import io
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import modman as mm  # noqa: E402
import modmenu  # noqa: E402
from test_build_native import make_pe  # noqa: E402

MODS = [
    {"id": "better-sprint", "name": "Better Sprint", "enabled": True, "kinds": ["romfs"]},
    {"id": "map-base", "name": "Map Base", "enabled": True, "kinds": ["romfs"]},
    {"id": "better-map", "name": "Better Map", "enabled": True, "kinds": ["romfs", "cheats"]},
    {"id": modmenu.MOD_ID, "name": "Mod menu", "enabled": True, "kinds": ["native"]},
    {"id": "stamina", "name": "Stamina Hook", "enabled": False, "kinds": ["native"]},
    {"id": "wiiu-trees", "name": "Bäume Überall 木 \tfrom Wii U, with a very long name indeed", "enabled": True,
     "kinds": ["romfs"], "wiiu": {"files": 4, "converted": 3, "partly": 0, "wiiu_data": 1, "left_out": 0}},
]
TOMLS = {
    "better-sprint": 'description = "Sprint for longer."',
    "better-map": 'description = "A clearer map."\n[dependencies]\n"map-base" = "*"',
    "stamina": ('description = "Keeps stamina up."\nloader_api = 2\ngame_versions = ["1.9.0"]\n[code]\n'
                'windows = "s.dll"\nhooks = ["0x7000cc7410"]'),
}


def quiet(fn, *a, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = fn(*a, **kw)
    return rc, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "lib"
        self.lib = self.make_library(self.root)
        p = mock.patch.object(mm, "game_running", return_value=False)
        self.running = p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def make_library(self, root: Path) -> mm.Library:
        lib = mm.Library(str(root), None, [dict(m) for m in MODS])
        for m in lib.mods:
            d = root / "mods" / m["id"]
            d.mkdir(parents=True)
            if m["id"] in TOMLS:
                (d / "mod.toml").write_text(f'[mod]\nid = "{m["id"]}"\nname = "{m["name"]}"\nversion = "1.0"\n'
                                            f'{TOMLS[m["id"]]}\n', encoding="utf-8")
        lib.save()
        return lib

    def reload(self) -> mm.Library:
        return mm.Library.load(str(self.root))

    def menu_lines(self) -> list[list[str]]:
        return [ln.split("\t") for ln in Path(modmenu.menu_path(self.lib)).read_text(encoding="utf-8").splitlines()]

    def pending(self, lines: list[str], serial: str | None = None) -> Path:
        menu = modmenu.read_menu(self.lib)
        if not menu:
            modmenu.sync(self.lib)
            menu = modmenu.read_menu(self.lib)
        p = Path(modmenu.menu_path(self.lib, modmenu.PENDING))
        p.write_text(f"swmodmenu-pending\t1\nserial\t{serial or menu.serial}\n" + "".join(f"{ln}\n" for ln in lines),
                     encoding="utf-8")
        return p

    def ids(self, lib=None) -> list[tuple[str, bool]]:
        return [(m["id"], m["enabled"]) for m in (lib or self.reload()).mods]


class Text(unittest.TestCase):
    def test_ascii_text(self):
        self.assertEqual(modmenu.ascii_text("Bäume\tÜberall\n木", 40), "Baume Uberall ?")
        self.assertEqual(modmenu.ascii_text("a" * 40, 28), "a" * 25 + "...")
        self.assertEqual(modmenu.ascii_text("  two   spaces \r\n", 28), "two spaces")
        self.assertEqual(modmenu.ascii_text(None, 10), "")
        # (the game's font has no # $ % @ ^ ` { | } ~)
        self.assertEqual(modmenu.ascii_text("answers {hello: world}.", 40), "answers (hello: world).")
        self.assertEqual(modmenu.ascii_text("50% more | mod #2 @ ~night", 60), "50 percent more / mod no. 2 at -night")

    def test_ordinal(self):
        self.assertEqual([modmenu.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 64)],
                         ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "64th"])


class MenuFile(Base):
    def test_sync_writes_what_the_game_shows(self):
        modmenu.sync(self.lib)
        lines = self.menu_lines()
        self.assertEqual(lines[0], ["swmodmenu", "1"])
        self.assertRegex(lines[1][1], r"^[0-9a-f]{16}$")
        self.assertEqual(lines[2][1], modmenu.config_stamp(self.lib))
        self.assertEqual(lines[3], ["note", ""])
        mods = {ln[1]: ln for ln in lines if ln[0] == "mod"}
        self.assertEqual([ln[1] for ln in lines if ln[0] == "mod"], [m["id"] for m in MODS])  # (library order)
        self.assertEqual(mods["better-sprint"], ["mod", "better-sprint", "on", "", "Better Sprint",
                                                 "Sprint for longer.", ""])
        self.assertEqual(mods[modmenu.MOD_ID][2:4], ["on", "hidden"])
        self.assertEqual(mods["stamina"][2:6], ["off", "rebuild", "Stamina Hook", "Keeps stamina up."])
        self.assertEqual(mods["better-map"][5:], ["A clearer map.", "Needs Map Base."])
        self.assertEqual(mods["map-base"][5:], ["Changes game files.", "Better Map needs this."])
        w = mods["wiiu-trees"]
        self.assertEqual(w[3], "wiiu")
        self.assertEqual(w[4], "Baume Uberall ? from Wii...")
        self.assertLessEqual(len(w[4]), 28)
        self.assertEqual(w[6], "Wii U mod: may look wrong.")
        for ln in lines:
            self.assertTrue(all(32 <= ord(c) < 127 for c in "".join(ln)), ln)

    def test_a_secret_mod_is_hidden(self):
        """[mod] secret (an easter egg, a hidden extra's part): hidden like the Mod menu mod, by its library entry or
        its mod.toml, so the game never lists it and can't turn it on or off."""
        for m in self.lib.mods:
            if m["id"] == "better-sprint":
                m["secret"] = True
        self.lib.save()
        (self.root / "mods" / "map-base" / "mod.toml").write_text(
            '[mod]\nid = "map-base"\nname = "Map Base"\nversion = "1.0"\nsecret = true\n', encoding="utf-8")
        rows = {r.id: r for r in modmenu.describe_mods(self.lib)}
        self.assertTrue(rows["better-sprint"].hidden and rows["map-base"].hidden)
        self.assertIsNone(rows["better-sprint"].position)
        modmenu.sync(self.lib)
        mods = {ln[1]: ln for ln in self.menu_lines() if ln[0] == "mod"}
        self.assertEqual((mods["better-sprint"][3], mods["map-base"][3]), ("hidden", "hidden"))
        self.assertNotIn("better-sprint", [i for i, _ in modmenu._listed_now(self.lib)])

    def test_serial_changes_and_note_is_kept(self):
        modmenu.sync(self.lib, "Better Map needs Map Base, which is off.")
        first = modmenu.read_menu(self.lib)
        modmenu.sync(self.lib)
        second = modmenu.read_menu(self.lib)
        self.assertNotEqual(first.serial, second.serial)
        self.assertEqual(second.note, "Better Map needs Map Base, which is off.")
        modmenu.sync(self.lib, "")
        self.assertEqual(modmenu.read_menu(self.lib).note, "")

    def test_reader_skips_unknown_lines_and_drops_a_bad_file(self):
        p = Path(modmenu.menu_path(self.lib))
        p.parent.mkdir(parents=True)
        p.write_text("swmodmenu\t2\nnewkind\tx\nmod\tbetter-sprint\ton\t\tBetter Sprint\n"
                     "mod\t..\\..\\evil\ton\t\tEvil\n", encoding="utf-8")
        self.assertEqual([m.id for m in modmenu.read_menu(self.lib).mods], ["better-sprint"])
        p.write_text("not a menu\nmod\tbetter-sprint\ton\t\tBetter Sprint\n", encoding="utf-8")
        self.assertIsNone(modmenu.read_menu(self.lib))

    def test_rows_without_changes(self):
        rows = modmenu.rows(self.lib)
        listed = [r for r in rows if not r.hidden]
        self.assertEqual([r.position for r in listed], [1, 2, 3, 4, 5])
        self.assertFalse(any(r.changed for r in rows))
        self.assertTrue(next(r for r in rows if r.id == "stamina").rebuild)


class Changes(Base):
    def test_set_and_move_write_pending_and_undo_deletes_it(self):
        modmenu.sync(self.lib)
        r = modmenu.set_wanted(self.lib, "stamina", True)
        self.assertTrue(r.wanted and r.changed)
        modmenu.move_wanted(self.lib, "better-sprint", 3)
        text = Path(modmenu.menu_path(self.lib, modmenu.PENDING)).read_text(encoding="utf-8")
        serial = modmenu.read_menu(self.lib).serial
        self.assertEqual(text, f"swmodmenu-pending\t1\nserial\t{serial}\nmod\tmap-base\ton\nmod\tbetter-map\ton\n"
                               "mod\tbetter-sprint\ton\nmod\tstamina\ton\nmod\twiiu-trees\ton\n")
        self.assertTrue(modmenu.undo(self.lib))
        self.assertFalse(modmenu.has_pending(self.lib))
        self.assertFalse(modmenu.undo(self.lib))

    def test_setting_it_back_deletes_pending(self):
        modmenu.set_wanted(self.lib, "map-base", False)
        self.assertTrue(modmenu.has_pending(self.lib))
        modmenu.set_wanted(self.lib, "map-base", True)
        self.assertFalse(modmenu.has_pending(self.lib))

    def test_the_menu_itself_and_bad_positions_are_refused(self):
        with self.assertRaisesRegex(modmenu.MenuError, "the menu itself"):
            modmenu.set_wanted(self.lib, modmenu.MOD_ID, False)
        with self.assertRaisesRegex(modmenu.MenuError, "1 to 5"):
            modmenu.move_wanted(self.lib, "stamina", 6)

    def test_refused_while_the_game_runs(self):
        modmenu.sync(self.lib)
        menu = Path(modmenu.menu_path(self.lib)).read_bytes()
        self.running.return_value = True
        for fn, args in ((modmenu.set_wanted, ("stamina", True)), (modmenu.move_wanted, ("stamina", 1)),
                         (modmenu.undo, ()), (modmenu.apply, ()), (modmenu.sync, ())):
            with self.assertRaisesRegex(modmenu.MenuError, "Savage Wind is running"):
                fn(self.lib, *args)
        # (sync too: the running game's changes are based on the list it read; Mod Manager's hook just waits)
        self.assertFalse(modmenu.library_changed(self.lib))
        self.assertEqual(Path(modmenu.menu_path(self.lib)).read_bytes(), menu)

    def test_rows_show_pending_changes(self):
        # (as the game writes it: every listed mod. Better Sprint moved to 4th, Stamina Hook on, Better Sprint off)
        self.pending(["mod\tmap-base\ton", "mod\tbetter-map\ton", "mod\tstamina\ton", "mod\tbetter-sprint\toff",
                      "mod\twiiu-trees\ton", "mod\tnot-there\ton"])
        rows = {r.id: r for r in modmenu.rows(self.lib)}
        self.assertTrue(rows["stamina"].wanted)
        self.assertFalse(rows["better-sprint"].wanted)
        self.assertEqual([r.id for r in sorted((r for r in rows.values() if not r.hidden),
                                               key=lambda r: r.wanted_position)],
                         ["map-base", "better-map", "stamina", "better-sprint", "wiiu-trees"])
        self.assertTrue(rows["map-base"].changed)  # (it moved up one)


class Merge(unittest.TestCase):
    MODS = [{"id": i, "name": i.upper(), "enabled": True} for i in ("a", "b", "menu", "c", "d")]
    BASE = [("a", True), ("b", True), ("c", True), ("d", True)]  # (what the game showed)

    def test_merge_rules(self):
        new, changes = modmenu.merge(self.MODS, self.BASE, [("a", True), ("c", True), ("d", True), ("x", False),
                                                            ("b", False), ("menu", False), ("d", False)], {"menu"})
        self.assertEqual([m["id"] for m in new], ["a", "c", "menu", "d", "b"])  # (b moved last; menu keeps its slot)
        self.assertEqual([m["enabled"] for m in new], [True, True, True, True, False])
        self.assertEqual(changes, ["B: off", "B: 4th in the order"])
        self.assertTrue(all(m["enabled"] for m in self.MODS))  # (the old list is untouched)

    def test_only_what_the_player_changed_is_used(self):
        """Finding 1: Mod Manager changes made after the game read its list stay (on/off and order)."""
        mods = [dict(m) for m in self.MODS]
        mods[0]["enabled"] = False                             # (Mod Manager: A off)
        mods = [mods[4], mods[0], mods[1], mods[2], mods[3]]   # (Mod Manager: D first)
        # the game showed the old list; the player only moved C to the top
        new, changes = modmenu.merge(mods, self.BASE, [("c", True), ("a", True), ("b", True), ("d", True)], {"menu"})
        self.assertEqual([(m["id"], m["enabled"]) for m in new],
                         [("c", True), ("d", True), ("a", False), ("menu", True), ("b", True)])
        self.assertEqual(changes, ["C: 1st in the order"])
        # turning a mod back to what the game showed is no change from the game
        new, changes = modmenu.merge(mods, self.BASE, [("a", True), ("b", True), ("c", True), ("d", True)], {"menu"})
        self.assertEqual((new, changes), (mods, []))


class Apply(Base):
    def setUp(self):
        super().setUp()
        modmenu.sync(self.lib)
        self.deployed = []

    def deploy_ok(self, lib):
        self.deployed.append(self.ids(mm.Library.load(lib.root)))
        return {"mods": []}

    def test_nothing_waits(self):
        self.assertEqual(modmenu.apply(self.lib, self.deploy_ok).status, "nothing")
        self.assertEqual(self.deployed, [])

    GAME_LIST = ["better-sprint", "map-base", "better-map", "stamina", "wiiu-trees"]  # (the listed mods, in order)

    def game_pending(self, on: dict, order=None, serial=None) -> Path:
        """pending.txt as the game writes it: every listed mod, in the wanted order, on/off as given (else as now)."""
        now = {m["id"]: m["enabled"] for m in self.reload().mods}
        return self.pending([f"mod\t{i}\t{'on' if on.get(i, now[i]) else 'off'}" for i in order or self.GAME_LIST],
                            serial)

    def refuse(self, lib):
        raise mm.ModmanError("Can't deploy (fix it, or turn the mod off with python tools/modman.py disable <id>):\n"
                             "  - Better Map needs map-base, which is turned off: python tools/modman.py enable "
                             "map-base")

    def test_changes_are_used_then_deployed(self):
        before = modmenu.read_menu(self.lib).serial
        self.game_pending({"stamina": True, "wiiu-trees": False},
                          ["stamina", "map-base", "better-map", "wiiu-trees", "better-sprint"])
        a = modmenu.apply(self.lib, self.deploy_ok)
        self.assertEqual(a.status, "used")
        want = [("stamina", True), ("map-base", True), ("better-map", True), (modmenu.MOD_ID, True),
                ("wiiu-trees", False), ("better-sprint", True)]
        self.assertEqual(self.ids(), want)
        self.assertEqual(self.deployed, [want])  # (config.json was saved before the deploy)
        self.assertFalse(modmenu.has_pending(self.lib))
        self.assertTrue(os.path.isfile(modmenu.menu_path(self.lib, modmenu.PENDING_USED)))
        self.assertFalse(os.path.isfile(modmenu.menu_path(self.lib, modmenu.MARKER)))
        menu = modmenu.read_menu(self.lib)
        self.assertNotEqual(menu.serial, before)
        self.assertEqual(menu.config, modmenu.config_stamp(self.lib))
        self.assertEqual([m.id for m in menu.mods][0], "stamina")
        self.assertEqual(len(a.changes), 4)
        self.assertEqual(a.changes[0], "Stamina Hook: on")
        self.assertEqual(a.changes[2:], ["Stamina Hook: 1st in the order", "Better Sprint: 5th in the order"])

    def test_a_failed_deploy_puts_the_library_back(self):
        config = Path(self.lib.config_path).read_bytes()
        self.game_pending({"map-base": False})
        a = modmenu.apply(self.lib, self.refuse)
        self.assertEqual(a.status, "failed")
        self.assertEqual(Path(self.lib.config_path).read_bytes(), config)
        self.assertTrue(next(m for m in self.lib.mods if m["id"] == "map-base")["enabled"])
        # (no command line in the game, and the mod's name, not its id)
        self.assertEqual(a.note, "Better Map needs Map Base, which is turned off.")
        self.assertEqual(modmenu.read_menu(self.lib).note, a.note)  # (the game shows it: "Last change not used")

    def test_a_failed_deploy_keeps_the_changes_waiting(self):
        """Finding 5: a failed deploy (here a file in use) keeps pending.txt, moved onto the new list, so the game
        still shows the changes and the next play tries again; the note is plain words."""
        self.game_pending({"map-base": False, "better-map": False, "stamina": True})

        def busy(lib):
            raise OSError(32, "The process cannot access the file because it is being used by another process")
        a = modmenu.apply(self.lib, busy)
        self.assertEqual((a.status, len(a.changes)), ("failed", 3))
        self.assertEqual(a.note, "A file was in use. Close other programs, then try again.")
        self.assertTrue(modmenu.has_pending(self.lib))
        self.assertEqual(modmenu.read_pending(self.lib).serial, modmenu.read_menu(self.lib).serial)
        self.assertEqual(sum(r.changed for r in modmenu.rows(self.reload())), 3)
        win = OSError(None, "The process cannot access the file", None, 32)  # (as Windows raises it)
        self.assertEqual(modmenu._reason(win, self.lib), a.note)
        self.assertEqual(modmenu._reason(OSError(errno.ENOSPC, "No space"), self.lib), "The disk is full.")
        a = modmenu.apply(self.reload(), self.deploy_ok)  # (the next play: it works now)
        self.assertEqual((a.status, len(a.changes)), ("used", 3))
        self.assertEqual(modmenu.read_menu(self.lib).note, "")

    def test_a_crash_in_the_deploy_puts_the_library_back_too(self):
        config = Path(self.lib.config_path).read_bytes()
        self.game_pending({"map-base": False})
        with self.assertRaises(KeyboardInterrupt):
            modmenu.apply(self.lib, mock.Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(Path(self.lib.config_path).read_bytes(), config)
        self.assertFalse(os.path.isfile(modmenu.menu_path(self.lib, modmenu.MARKER)))

    def test_damaged_pending(self):
        p = self.pending([])
        p.write_text("nonsense\n", encoding="utf-8")
        a = modmenu.apply(self.lib, self.deploy_ok)
        self.assertEqual((a.status, self.deployed), ("damaged", []))
        self.assertTrue(os.path.isfile(modmenu.menu_path(self.lib, modmenu.PENDING_BAD)))
        self.assertEqual(modmenu.read_menu(self.lib).note, modmenu.NOTE_DAMAGED)
        self.pending(["mod\tstamina\tmaybe"])
        self.assertEqual(modmenu.apply(self.lib, self.deploy_ok).status, "damaged")

    def test_mod_manager_change_before_the_game_wrote_stays(self):
        """Finding 1 (sim1): Stamina Hook turned on in Mod Manager; the game, started from the older list, showed it
        OFF and the player only turned Better Sprint off. Stamina Hook stays on."""
        lib = self.reload()
        lib.mods[4]["enabled"] = True
        lib.save()
        old = time.time() - 50
        os.utime(lib.config_path, (old, old))
        self.game_pending({"better-sprint": False, "stamina": False})
        a = modmenu.apply(self.reload(), self.deploy_ok)
        self.assertEqual((a.status, a.changes), ("used", ["Better Sprint: off"]))
        self.assertTrue(dict(self.ids())["stamina"])

    def test_mod_manager_changes_win_over_older_game_changes(self):
        """Finding 1 (sim2): an older game change (Map Base off), a newer Mod Manager change (Stamina Hook on), then
        sync as the docs say: both stay."""
        p = self.game_pending({"map-base": False})
        old = time.time() - 100
        os.utime(p, (old, old))
        lib = self.reload()
        lib.mods[4]["enabled"] = True
        lib.save()
        modmenu.sync(lib)
        a = modmenu.apply(self.reload(), self.deploy_ok)
        self.assertEqual((a.status, a.changes), ("used", ["Map Base: off"]))
        self.assertEqual({i: on for i, on in self.ids() if i in ("map-base", "stamina")},
                         {"map-base": False, "stamina": True})

    def test_pending_from_an_unknown_list_is_set_aside(self):
        """Finding 1: apply checks pending.txt's serial; with no copy of that list, what the player changed is unknown."""
        self.game_pending({"map-base": False}, serial="0123456789abcdef")
        a = modmenu.apply(self.lib, self.deploy_ok)
        self.assertEqual((a.status, self.deployed), ("stale", []))
        self.assertTrue(os.path.isfile(modmenu.menu_path(self.lib, modmenu.PENDING_OLD)))
        self.assertEqual(modmenu.read_menu(self.lib).note, modmenu.NOTE_STALE)
        self.assertTrue(dict(self.ids())["map-base"])

    def test_sync_moves_the_games_changes_onto_the_new_list(self):
        """Finding 2: sync (after a Mod Manager change) keeps the game's changes: pending.txt gets the new serial,
        with only what the player changed, so the game shows them at its next start."""
        first = modmenu.read_menu(self.lib).serial
        self.game_pending({"better-sprint": False})
        lib = self.reload()
        lib.mods[4]["enabled"] = True
        lib.save()
        modmenu.sync(lib)
        menu = modmenu.read_menu(lib)
        self.assertNotEqual(menu.serial, first)
        pending = modmenu.read_pending(lib)
        self.assertEqual(pending.serial, menu.serial)
        self.assertEqual(dict(pending.wanted), {"better-sprint": False, "map-base": True, "better-map": True,
                                                "stamina": True, "wiiu-trees": True})
        self.assertTrue(os.path.isfile(modmenu.menu_path(lib, modmenu.BASE.format(menu.serial))))
        # (an old copy is kept while a pending.txt may still name it: the game may have written one in between)
        self.assertEqual(modmenu.read_base(lib, first)[0], ("better-sprint", True))
        for _ in range(modmenu.KEEP_BASES + 2):
            modmenu.sync(lib)
        bases = [n for n in os.listdir(modmenu.menu_dir(lib)) if modmenu.BASE_RE.fullmatch(n)]
        self.assertEqual(len(bases), modmenu.KEEP_BASES + 1)  # (the copies of old lists are pruned)

    def test_a_cut_off_apply_is_finished(self):
        """Finding 7 (sim3): apply stopped after config.json was saved, before the deploy ended (the console was
        closed: no except block ran). The next play deploys; it doesn't call the changes stale."""
        self.game_pending({"map-base": False})
        killed = mock.Mock(side_effect=KeyboardInterrupt)
        with mock.patch.object(modmenu, "_restore_config"), mock.patch.object(modmenu, "_clear_marker"), \
                self.assertRaises(KeyboardInterrupt):
            modmenu.apply(self.lib, killed)
        self.assertFalse(dict(self.ids())["map-base"])  # (saved, never deployed)
        self.assertTrue(modmenu.has_pending(self.lib))
        a = modmenu.apply(self.reload(), self.deploy_ok)
        self.assertEqual(a.status, "resumed")
        self.assertEqual(len(self.deployed), 1)
        self.assertFalse(dict(self.deployed[0])["map-base"])
        self.assertFalse(modmenu.has_pending(self.lib))
        # (and when that deploy fails too, config.json goes back to how it was before the first try)
        config = Path(self.lib.config_path).read_bytes()
        self.game_pending({"stamina": True})
        with mock.patch.object(modmenu, "_restore_config"), mock.patch.object(modmenu, "_clear_marker"), \
                self.assertRaises(KeyboardInterrupt):
            modmenu.apply(self.reload(), killed)
        self.assertEqual(modmenu.apply(self.reload(), self.refuse).status, "failed")
        self.assertEqual(Path(self.lib.config_path).read_bytes(), config)

    def test_an_old_deploy_is_redone_when_the_changes_are_in_already(self):
        """Finding 7: the changes match config.json, but the last deploy wasn't made from it: deploy."""
        self.game_pending({"map-base": False})
        lib = self.reload()
        lib.mods[1]["enabled"] = False  # (config.json has it already)
        lib.save()
        with mock.patch.object(modmenu, "_deploy_out_of_date", return_value=True):
            a = modmenu.apply(self.reload(), self.deploy_ok)
        self.assertEqual((a.status, len(self.deployed)), ("same", 1))

    def test_a_mod_manager_save_during_the_deploy_stays(self):
        """Finding 8: the Mod Manager window saves config.json while the deploy runs. A failed deploy doesn't put
        the old config.json over that save, and after a good one menu.txt is made from what is on disk."""
        self.game_pending({"map-base": False})

        def window_saves(k, then):
            def deploy(lib):
                other = mm.Library.load(lib.root)
                other.mods[k]["enabled"] = False  # (a mod turned off in the window)
                other.save()
                return then(lib)
            return deploy
        a = modmenu.apply(self.lib, window_saves(0, self.refuse))
        self.assertEqual(a.status, "failed")
        self.assertFalse(dict(self.ids())["better-sprint"])
        self.game_pending({"stamina": True})
        a = modmenu.apply(self.reload(), window_saves(5, self.deploy_ok))
        self.assertEqual(a.status, "used")
        menu = modmenu.read_menu(self.lib)
        self.assertEqual(menu.config, modmenu.config_stamp(self.lib))
        self.assertEqual({m.id: m.on for m in menu.mods}, dict(self.ids()))

    def test_the_note_goes_once_the_library_changes(self):
        """Finding 6 (sim_note): "Last change not used" is dropped once config.json changed (the player fixed it in
        Mod Manager), by play and by sync; it stays while nothing changed."""
        self.game_pending({"map-base": False})
        self.assertEqual(modmenu.apply(self.lib, self.refuse).status, "failed")
        modmenu.sync(self.reload())
        self.assertTrue(modmenu.read_menu(self.lib).note)
        modmenu.undo(self.lib)
        time.sleep(0.02)
        lib = self.reload()
        lib.mods[4]["enabled"] = True
        lib.save()
        with mock.patch.object(mm, "run", return_value=0):
            rc, _ = quiet(modmenu.main, ["play", "--library", str(self.root)])
        self.assertEqual((rc, modmenu.read_menu(self.lib).note), (0, ""))

    def test_same_as_now(self):
        self.pending(["mod\tstamina\toff"])
        self.assertEqual(modmenu.apply(self.lib, self.deploy_ok).status, "same")
        self.assertFalse(modmenu.has_pending(self.lib))
        self.assertEqual(self.deployed, [])

    def test_hidden_and_unknown_ids_change_nothing(self):
        self.pending([f"mod\t{modmenu.MOD_ID}\toff", "mod\tgone\ton"])
        self.assertEqual(modmenu.apply(self.lib, self.deploy_ok).status, "same")
        self.assertTrue(next(m for m in self.reload().mods if m["id"] == modmenu.MOD_ID)["enabled"])

    def test_pending_written_by_the_game(self):
        """The exact bytes the mod writes (native/examples/modmenu/modmenu.c pending_text): LF only, every listed mod."""
        serial = modmenu.read_menu(self.lib).serial
        Path(modmenu.menu_path(self.lib, modmenu.PENDING)).write_bytes(
            f"swmodmenu-pending\t1\nserial\t{serial}\nmod\tmap-base\ton\nmod\tbetter-sprint\ton\n"
            "mod\tbetter-map\toff\nmod\tstamina\toff\nmod\twiiu-trees\ton\n".encode())
        self.assertEqual(modmenu.apply(self.lib, self.deploy_ok).status, "used")
        self.assertEqual([i for i, _ in self.ids()][:3], ["map-base", "better-sprint", "better-map"])


class Setup(Base):
    def fake_build(self) -> str:
        d = Path(self.tmp.name) / "built"
        d.mkdir(exist_ok=True)
        (d / modmenu.MOD_DLL).write_bytes(make_pe(exports=["swmod_init"]))
        (d / "mod.toml").write_text(modmenu.MANIFEST, encoding="utf-8")
        return str(d)

    def test_replaces_the_old_copy_in_place(self):
        self.lib.mods[3]["enabled"] = False
        self.lib.save()
        entry, replaced = modmenu.add_to_library(self.lib, self.fake_build())
        self.assertTrue(replaced)
        self.assertEqual(entry["name"], "Mod menu")
        lib = self.reload()
        self.assertEqual(lib.mods[3]["id"], modmenu.MOD_ID)
        self.assertFalse(lib.mods[3]["enabled"])
        self.assertTrue((self.root / "mods" / modmenu.MOD_ID / modmenu.MOD_DLL).is_file())

    def test_first_add_and_a_bad_build_keeps_the_old_one(self):
        lib = mm.Library(str(Path(self.tmp.name) / "fresh"), None, [])
        lib.save()
        entry, replaced = modmenu.add_to_library(lib, self.fake_build())
        self.assertEqual((entry["id"], replaced), (modmenu.MOD_ID, False))
        bad = Path(self.tmp.name) / "bad"
        bad.mkdir()
        (bad / "mod.toml").write_text(modmenu.MANIFEST, encoding="utf-8")  # (no DLL)
        with self.assertRaisesRegex(modmenu.MenuError, "old one stays"):
            modmenu.add_to_library(lib, str(bad))
        self.assertEqual([m["id"] for m in mm.Library.load(lib.root).mods], [modmenu.MOD_ID])

    def test_setup_command(self):
        with mock.patch.object(modmenu, "build_mod", return_value=self.fake_build()):
            rc, out = quiet(modmenu.main, ["setup", "--library", str(self.root)])
        self.assertEqual(rc, 0)
        self.assertEqual(out.splitlines()[-1], "Mod menu updated (#4, on). Now press Deploy in Mod Manager.")
        self.assertIsNotNone(modmenu.read_menu(self.reload()))

    def test_manifest_is_what_modman_reads(self):
        man, errs = mm.parse_manifest(modmenu.MANIFEST)
        self.assertEqual(errs, [])
        self.assertEqual((man.id, man.name, man.dll, man.game_versions), (modmenu.MOD_ID, "Mod menu", "modmenu.dll",
                                                                          ["1.9.0"]))


class CommandLine(Base):
    def run_cli(self, *args) -> tuple[int, list[str]]:
        rc, out = quiet(modmenu.main, [*args, "--library", str(self.root)])
        return rc, out.splitlines()

    def test_show(self):
        modmenu.sync(self.lib)
        modmenu.set_wanted(self.lib, "stamina", True)
        rc, lines = self.run_cli("show")
        self.assertEqual(rc, 0)
        self.assertEqual(lines[0], "5 mods, 1 change waits for your next start.")
        self.assertIn("   4  off -> on  Stamina Hook  next start: 10-20 minutes", lines)
        self.assertEqual(lines[-1], "Use them: python tools/modmenu.py play (or apply). Throw them away: "
                                    "python tools/modmenu.py undo")

    def test_set_move_undo(self):
        self.assertEqual(self.run_cli("set", "stamina", "on")[1], ["Stamina Hook: on at your next start."])
        self.assertEqual(self.run_cli("move", "2", "1")[1], ["Map Base: 1st at your next start."])
        self.assertEqual(self.run_cli("undo")[1], ["Threw away the changes from the game."])
        rc, lines = self.run_cli("set", "6", "off")
        self.assertEqual((rc, lines[0][:7]), (2, "Error: "))

    def test_numbers_are_the_games(self):
        """Finding 13: a number is the mod's place in the game's list (show's numbers, and the game's modmenu_set /
        modmenu_move), not Mod Manager's number (which counts the hidden Mod menu)."""
        self.assertEqual(self.run_cli("set", "4", "on")[1], ["Stamina Hook: on at your next start."])  # (Mod Manager: #5)
        self.assertEqual(self.run_cli("move", "5", "1")[1], ["Baume Uberall ? from Wii...: 1st at your next start."])
        rc, lines = self.run_cli("show")
        self.assertIn("   1  on         Baume Uberall ? from Wii... (was 5)", lines)
        self.assertEqual(self.run_cli("set", "1", "off")[1], ["Baume Uberall ? from Wii...: off at your next start."])
        rc, lines = self.run_cli("move", "6", "1")
        self.assertEqual(rc, 2)
        self.assertIn("the game's list has 5", lines[0])
        sub = next(a for a in modmenu.parser()._actions if a.dest == "cmd")
        for cmd in ("set", "move"):
            self.assertIn("number from show", next(a.help for a in sub.choices[cmd]._actions if a.dest == "mod"))

    def test_apply_and_play(self):
        modmenu.set_wanted(self.lib, "stamina", True)
        with mock.patch.object(mm, "deploy", return_value={"mods": [], "parts": {}, "attention": [], "notes": []}), \
                mock.patch.object(mm, "format_deploy", return_value="Deployed."), \
                mock.patch.object(mm, "run", return_value=0) as run:
            rc, lines = self.run_cli("play")
        self.assertEqual(rc, 0)
        self.assertEqual(lines[:2], ["Used 1 change from the game.", "  Stamina Hook: on"])
        run.assert_called_once()
        self.assertTrue(self.reload().mods[4]["enabled"])

    def test_sync_and_setup_wait_while_the_game_runs(self):
        """Findings 2 and 16: sync and setup refuse while the game runs (its changes need the list it read)."""
        rc, lines = self.run_cli("sync")
        self.assertEqual(rc, 0)
        self.assertTrue(lines[0].startswith("Wrote the game's mod list (5 mods): "))
        self.running.return_value = True
        menu = Path(modmenu.menu_path(self.lib)).read_bytes()
        build = mock.Mock()
        with mock.patch.object(modmenu, "build_mod", build):
            for cmd in ("sync", "setup"):
                rc, lines = self.run_cli(cmd)
                self.assertEqual((rc, lines[0][:33]), (2, "Error: Savage Wind is running. Cl"), cmd)
        build.assert_not_called()
        self.assertEqual(Path(modmenu.menu_path(self.lib)).read_bytes(), menu)


class InGameCheck(Base):
    """tools/modmenu_ingame_check.py on made-up logs and answers (it never reaches a game here: ask_game is
    replaced)."""

    LOG = ("[modhost] modhost 0.3.0 (mod API 2)\n[mod] modmenu: Mods page off: old\n"
           "[modhost] modhost 0.3.0 (mod API 2)\n[mod] modmenu: Mods page on (5 mods, path A+)\n"
           "[modhost] mod modmenu.dll: ok, commands: modmenu,modmenu_set,modmenu_move,modmenu_undo\n"
           "[options] Savage Wind's pages added to the Options screen (108 entries)\n")

    def test_log(self):
        import modmenu_ingame_check as ic
        res = ic.check_log(ic.last_start(self.LOG))
        self.assertEqual([k for k, _ in res], ["PASS", "PASS"])
        self.assertIn("5 mods, path A+", res[0][1])
        res = ic.check_log(ic.last_start("[modhost] modhost 0.3.0\n[modhost] mod modmenu.dll: init failed (code 3)\n"
                                         "[mod] modmenu: Mods page off: this Savage Wind isn't 0.4.1-alpha.4\n"))
        self.assertEqual([k for k, _ in res], ["FAIL", "FAIL", "INFO"])

    def test_game_answer(self):
        import modmenu_ingame_check as ic
        modmenu.sync(self.lib)
        menu = modmenu.read_menu(self.lib)
        listed = [m.id for m in menu.mods if "hidden" not in m.flags]
        reply = {"on": True, "path": "A+", "count": len(listed), "changes": 0, "list": "ok",
                 **{f"mods.{i}.id": x for i, x in enumerate(reversed(listed))}}
        self.assertEqual([k for k, _ in ic.check_game(reply, menu)], ["PASS", "PASS"])
        reply["count"] = 2
        self.assertEqual([k for k, _ in ic.check_game(reply, menu)], ["PASS", "FAIL"])
        self.assertEqual(ic.check_game({"on": False, "why": "x"}, menu), [("FAIL", "The game says the Mod menu is "
                                                                                   "off: x")])

    def test_main_reads_only(self):
        import modmenu_ingame_check as ic
        inst = Path(self.tmp.name) / "inst"
        inst.mkdir()
        (inst / "savagewind.log").write_text(self.LOG, encoding="utf-8")
        modmenu.sync(self.lib)
        env = {k: v for k, v in os.environ.items() if k != "SAVAGEWIND_DATA_ROOT"}
        self.enterContext(mock.patch.dict(os.environ, env, clear=True))
        with mock.patch.object(ic, "ask_game", return_value=(None, "No answer.")):
            rc, out = quiet(ic.main, ["--library", str(self.root), "--install", str(inst)])
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("Mod menu check: nothing failed (3 checks)."))
        with mock.patch.object(ic, "ask_game", return_value=(None, "No answer.")):
            rc, _ = quiet(ic.main, ["--library", str(self.root), "--install", str(Path(self.tmp.name) / "none")])
        self.assertEqual(rc, 2)


class Docs(unittest.TestCase):
    """docs/mod-menu.md: every command it shows parses, and the facts it states match the code."""

    def test_commands_parse(self):
        import test_docs as td
        text = (REPO / "docs" / "mod-menu.md").read_text(encoding="utf-8")
        cmds = td.doc_commands(text)
        self.assertTrue(any("modmenu.py setup" in c for c in cmds))
        for c in cmds:
            m = td.CMD_RE.match(c)
            for argv in td.argv_variants(m.group(2) or ""):
                td.parse(m.group(1).replace("\\", "/").replace("/", "."), argv)

    def test_facts(self):
        text = " ".join((REPO / "docs" / "mod-menu.md").read_text(encoding="utf-8").split())
        self.assertIn(f"`{modmenu.MOD_ID}`", text)
        self.assertIn("0.4.1-alpha.4", text)
        src = (REPO / "native" / "examples" / "modmenu" / "opt_page.h").read_text(encoding="utf-8")
        page = int(re.search(r"#define MM_PAGE_MODS (\d+)", src).group(1))
        self.assertIn(f"first {page} mods", text)
        line = int(re.search(r"#define MM_TEXT_MAX (\d+)", src).group(1)) - 1
        self.assertIn(f"keeps each line to {line}", text)  # (the game test: the game shows about 82 characters)
        self.assertEqual(modmenu.NOTE_MAX, line)
        self.assertIn("Tested in the real game", text)
        self.assertIn("### Game test results", raw := (REPO / "docs" / "mod-menu.md").read_text(encoding="utf-8"))
        self.assertIn("not tried yet", raw)

    def test_says_what_works_today(self):
        """Findings 3, 15 and 16: the docs tell the real step (modmenu.py play while Mod Manager's Play doesn't use the
        changes), the real fix for a missing list (sync), and that the mouse was checked in the game."""
        raw = (REPO / "docs" / "mod-menu.md").read_text(encoding="utf-8")
        intro = " ".join(raw.split("## Set it up")[0].split())
        self.assertFalse(modmenu.modman_applies())  # (when modman does, these texts change with it)
        self.assertIn("`python tools/modmenu.py play`", intro)
        self.assertIn("Play in Mod Manager does not use them yet", intro)
        text = " ".join(raw.split())
        self.assertNotIn("then press **Play** in Mod Manager", text)
        self.assertNotIn("Start the game from Mod Manager", text)
        missing = next(ln for ln in raw.splitlines() if ln.startswith("| Mod list missing"))
        self.assertIn("python tools/modmenu.py sync", missing)
        self.assertIn("| This list may be old |", raw)
        self.assertIn("the mouse works on every row (checked in the game)", text)
        self.assertNotIn("checked against a made-up Savage Wind only", text)
        self.assertIn("Only `show` works while the game runs", text)


class Sources(unittest.TestCase):
    """Checks on the C sources that no self-test run can show."""
    MOD = REPO / "native" / "examples" / "modmenu"

    def test_modhost_header_is_modhosts_own(self):
        """Finding 12: the Win32 calls only the Mod menu needs live in its own win_min.h; modhost's shared
        platform_min.h doesn't declare them (other code declares some of them with other types)."""
        shared = (REPO / "native" / "modhost" / "platform_min.h").read_text(encoding="utf-8")
        for name in ("VirtualQuery", "VirtualProtect", "CreateThread", "WaitForSingleObject", "CloseHandle",
                     "CreateFileW", "SwMemInfo", "SwSrwLock"):
            self.assertNotIn(name, shared)
        self.assertIn("VirtualQuery", (self.MOD / "win_min.h").read_text(encoding="utf-8"))
        self.assertIn('#include "win_min.h"', (self.MOD / "opt_page.h").read_text(encoding="utf-8"))

    def test_a_dirty_row_is_checked_before_it_is_read(self):
        """Finding 11: w_tick checks a remembered row is still on the screen before it reads it (a row of a closed
        screen may be freed)."""
        src = (self.MOD / "opt_page_a.c").read_text(encoding="utf-8")
        loop = src[src.index("while (g_ndirty > 0)"):]
        loop = loop[:loop.index("return 0;")]
        self.assertLess(loop.index("on_screen(screen, d)"), loop.index("my_row(d)"))

    def test_ids_as_long_as_modman_allows(self):
        """Finding 14: the mod keeps ids as long as modman's MANIFEST_ID_RE allows (64 characters)."""
        head = (self.MOD / "opt_page.h").read_text(encoding="utf-8")
        cap = int(re.search(r"#define MM_ID_MAX (\d+)", head).group(1))
        longest = int(re.search(r"\{0,(\d+)\}", mm.MANIFEST_ID_RE.pattern).group(1)) + 1
        self.assertGreater(cap, longest)
        self.assertIn("n >= MM_ID_MAX)", (self.MOD / "modmenu.c").read_text(encoding="utf-8"))


class UseLine(Base):
    def test_menu_says_who_uses_the_changes(self):
        """Finding 3: menu.txt says whether Play in Mod Manager uses the changes; the game's texts follow it."""
        modmenu.sync(self.lib)
        self.assertEqual(modmenu.read_menu(self.lib).use, "command")
        with mock.patch.object(mm, "deploy_with_menu", create=True, new=lambda lib, **kw: {}):
            modmenu.sync(self.lib)
        self.assertEqual(modmenu.read_menu(self.lib).use, "modman")


if __name__ == "__main__":
    unittest.main()
