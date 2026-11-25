"""Tests for modman's quality-of-life parts (docs/mod-manager.md, "Saves, crashes and mod sets"): save backups
before a start (S3) and restoring them (S4), Play without mods, the crash check, "use the last mods that worked",
what changed, conflicts, mod info, pictures, turn all on/off, and mod sets with their own saves. Every install,
save, log and mod is made up in temp folders: no game is started (Popen is mocked)."""
import json
import os
import shutil
import struct
import sys
import time
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modman as mm  # noqa: E402
from test_modman import FakeProc, RunBase, mod_toml, quiet, write  # noqa: E402


def tiny_png(w: int = 2, h: int = 2, rgb=(200, 40, 40)) -> bytes:
    """A real PNG (8-bit RGB), made here."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\0" + bytes(rgb) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class QolBase(RunBase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(mm.swr, "game_running", return_value=False)   # (tools/mod_settings.py asks it)
        p.start()
        self.addCleanup(p.stop)
        self.save = self.install / "save"
        write(self.save, {"save_0/game_data.sav": "slot 0", "option.sav": "options"})
        self.backups = self.libdir / mm.SAVE_BACKUPS

    def cli(self, *args):
        return quiet(mm.main, [*args, "--library", str(self.libdir)])

    def ours(self):
        return [b for b in mm.save_backups(self.lib) if b["ours"]]

    def history(self):
        return mm.read_history(self.lib)

    def starts(self):
        return [h for h in self.history() if h["type"] == "start"]

    def log(self, text: str, when: float | None = None):
        p = self.install / mm.GAME_LOG
        p.write_text(text, encoding="utf-8")
        if when is not None:
            os.utime(p, (when, when))
        return p

    def record_start(self, mods, when: str, *, no_mods=False):
        rec = {"type": "start", "time": when, "hash": None, "profile": "Default", "pid": 1,
               "mods": [{"id": i, "name": i.capitalize(), **({"version": v} if v else {})} for i, v in mods]}
        if no_mods:
            rec = {**rec, "mods": [], "no_mods": True}
        mm.history_append(self.lib, rec)

    def record_result(self, when: str, result: str):
        mm.history_append(self.lib, {"type": "result", "start": when, "result": result})


# ---------------------------------------------------------------- 1. save backups (S3) and restore (S4)

class SaveBackups(QolBase):
    def test_first_start_backs_up_then_only_when_the_mods_change(self):
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.mod("Bravo", {"romfs/Actor/Pack/Hero.txt": "b"})
        self.deploy()
        code, out = self.run_mm()
        self.assertEqual(code, 0, out)
        self.assertIn("Your save was backed up first", out)
        first = self.ours()
        self.assertEqual(len(first), 1)
        self.assertEqual((self.backups / first[0]["name"] / "save" / "save_0" / "game_data.sav").read_text(), "slot 0")
        self.assertEqual(first[0]["reason"], "first start")
        self.assertEqual(self.starts()[-1]["save"], str(self.save))
        # the same mods again: no copy
        code, out = self.run_mm()
        self.assertNotIn("backed up", out)
        self.assertEqual(len(self.ours()), 1)
        # fewer mods, and the save changed in the game meanwhile: a copy, and the warning
        (self.save / "save_0" / "game_data.sav").write_text("slot 0, later")
        quiet(mm.main, ["disable", "bravo", "--library", str(self.libdir)])
        self.deploy()
        code, out = self.run_mm()
        self.assertIn("Your save was backed up first", out)
        self.assertIn("Saving without a mod can drop what it added to your save", out)
        self.assertEqual([b["reason"] for b in self.ours()], ["mods changed", "first start"])
        self.assertEqual(self.save.joinpath("save_0", "game_data.sav").read_text(), "slot 0, later")  # (only read)

    def test_an_unchanged_save_isnt_copied_twice(self):
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.deploy()
        self.run_mm()
        code, out = self.run_mm("--no-mods")   # (another set, but the save is the same as the newest copy)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.ours()), 1)
        code, out = self.cli("saves", "backup")
        self.assertIn("same as the backup", out)

    def test_the_save_folder_from_the_environment_is_the_one_copied(self):
        other = write(self.tmp / "test-save", {"save_0/game_data.sav": "test slot"})
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.deploy()
        code, out = self.run_mm(env={mm.ENV_SAVE: str(other)})
        self.assertEqual(code, 0, out)
        b = self.ours()[0]
        self.assertEqual(b["from"], str(other))
        self.assertEqual((Path(b["path"]) / "save_0" / "game_data.sav").read_text(), "test slot")
        self.assertEqual(FakeProc.started[0].kw["env"][mm.ENV_SAVE], str(other))   # (passed on, never cleared)

    def test_only_the_newest_five_automatic_copies_stay(self):
        lib = self.lib
        kept = mm.backup_save(lib, "by hand", keep=True)
        for k in range(7):
            (self.save / "option.sav").write_text(f"options {k}")
            time.sleep(0.002)
            self.assertFalse(mm.backup_save(lib, f"start {k}")["skipped"])
        names = [b["name"] for b in self.ours()]
        self.assertEqual(len(names), 6)
        self.assertIn(kept["name"], names)   # (one made by hand is never deleted)
        self.assertEqual(sum(1 for b in self.ours() if not b.get("kept")), mm.SAVE_KEEP)

    def test_a_failed_copy_stops_the_start(self):
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.deploy()
        with mock.patch.object(mm.shutil, "copytree", side_effect=OSError(28, "No space left on device")):
            code, out = self.run_mm()
        self.assertEqual(code, 2)
        self.assertIn("couldn't be backed up", out)
        self.assertEqual(FakeProc.started, [])
        self.assertEqual(list(self.backups.iterdir()) if self.backups.exists() else [], [])   # (no half copy left)

    def test_no_save_yet_is_fine(self):
        mm.remove_tree(str(self.save))
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.deploy()
        code, out = self.run_mm()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ours(), [])

    def test_restore_moves_the_save_aside_and_deletes_nothing(self):
        info = mm.backup_save(self.lib, "by hand", keep=True)
        (self.save / "save_0" / "game_data.sav").write_text("broken later")
        with mock.patch.object(mm, "game_running", return_value=True):
            code, out = self.cli("saves", "restore", "1")
        self.assertEqual(code, 2)
        self.assertIn("running", out)
        code, out = self.cli("saves", "restore", info["name"])
        self.assertEqual(code, 0, out)
        self.assertEqual((self.save / "save_0" / "game_data.sav").read_text(), "slot 0")
        aside = [p for p in self.install.iterdir() if p.name.startswith("save-before-restore-")]
        self.assertEqual(len(aside), 1)
        self.assertEqual((aside[0] / "save_0" / "game_data.sav").read_text(), "broken later")
        self.assertIn("nothing was deleted", out)
        # the moved-aside save and Savage Wind's own backups are listed, read-only, and can be restored from
        write(self.install / "save-backup-2026-11-07", {"save_0/game_data.sav": "savage wind's copy"})
        items = mm.save_backups(self.lib)
        theirs = {b["name"]: b for b in items if not b["ours"]}
        self.assertIn("save-backup-2026-11-07", theirs)
        self.assertIn(aside[0].name, theirs)
        code, out = self.cli("saves")
        self.assertIn("not made by modman: only read", out)
        k = [b["name"] for b in items].index("save-backup-2026-11-07") + 1
        code, out = self.cli("saves", "restore", str(k))
        self.assertEqual(code, 0, out)
        self.assertEqual((self.save / "save_0" / "game_data.sav").read_text(), "savage wind's copy")
        self.assertTrue((self.install / "save-backup-2026-11-07" / "save_0" / "game_data.sav").is_file())
        self.assertEqual(len([p for p in self.install.iterdir() if p.name.startswith("save-before-restore-")]), 2)

    def test_a_backup_by_hand_waits_until_the_game_is_closed(self):
        with mock.patch.object(mm, "game_running", return_value=True):
            code, out = self.cli("saves", "backup")
        self.assertEqual(code, 2)
        self.assertIn("Close it, then back up the save", out)
        self.assertEqual(self.ours(), [])
        code, out = self.cli("saves", "backup")
        self.assertEqual(code, 0, out)
        self.assertEqual([b["reason"] for b in self.ours()], ["by hand"])


# ---------------------------------------------------------------- 2. Play without mods

class BackupsOfThisSave(QolBase):
    def test_a_backup_of_another_save_folder_isnt_offered(self):
        """Review finding: the restore list mixed backups of other save folders (a set's own save, a test save)."""
        other = write(self.tmp / "test-save", {"save_0/game_data.sav": "another playthrough"})
        mine = mm.backup_save(self.lib, "mine", keep=True)
        theirs = mm.backup_save(self.lib, "theirs", folder=str(other), keep=True)
        self.assertEqual([b["name"] for b in mm.backups_for(self.lib) if b["ours"]], [mine["name"]])
        with self.assertRaises(mm.ModmanError):
            mm.find_backup(self.lib, theirs["name"], str(self.save))
        code, out = self.cli("saves")
        self.assertIn(mine["name"], out)
        self.assertNotIn(theirs["name"], out)


class PlayWithoutMods(QolBase):
    def test_no_deploy_needed_and_every_mod_variable_cleared(self):
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})   # (not deployed)
        code, out = self.run_mm("--no-mods", env={"SAVAGEWIND_ROMFS_OVERLAY": "C:\\stale", "SWMOD_DIR": "C:\\x"})
        self.assertEqual(code, 0, out)
        game = FakeProc.started[0]
        self.assertEqual(game.argv, [str(self.install / "savagewind.exe")])
        for gone in mm.MOD_ENV:
            self.assertNotIn(gone, game.kw["env"])
        self.assertIn("without mods", out)
        start = self.starts()[-1]
        self.assertEqual((start["no_mods"], start["mods"]), (True, []))
        self.assertEqual(self.ours()[0]["reason"], "no mods")

    def test_no_mods_uses_the_normal_save_not_the_sets_own(self):
        """Review finding: run --no-mods opened a set's own (modded) save without its mods; the launcher didn't."""
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.deploy()
        self.cli("profile", "new", "Overhaul")
        self.assertEqual(self.cli("profile", "own-save", "Overhaul", "on", "--copy")[0], 0)
        code, out = self.run_mm("--no-mods")
        self.assertEqual(code, 0, out)
        self.assertNotIn(mm.ENV_SAVE, FakeProc.started[-1].kw["env"])
        self.assertEqual(os.path.normcase(self.starts()[-1]["save"]), os.path.normcase(str(self.save)))


# ---------------------------------------------------------------- 3. the crash check, 3b. the last mods that worked

class CrashCheck(QolBase):
    T1, T2, T3 = "2026-11-10T10:00:00", "2026-11-11T10:00:00", "2026-11-12T10:00:00"

    def at(self, t: str, plus: float = 60) -> float:
        return mm._time_of({"time": t}) + plus

    def three_starts(self, last_log: str):
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.mod("Better Sprint", {"romfs/Actor/Pack/Hero.txt": "b"})
        self.record_start([("alpha", "1.0")], self.T1)
        self.record_result(self.T1, "clean")
        self.record_start([("alpha", "1.0")], self.T2)
        self.record_result(self.T2, "unknown")
        self.record_start([("alpha", "1.1"), ("better-sprint", None)], self.T3)
        self.log(last_log, self.at(self.T3))

    def test_a_crash_names_whats_new_since_the_last_good_start(self):
        self.three_starts("[savagewind] Savage Wind\nboom\nhost backtrace (guest functions are f_<addr>):\n f_7100\n")
        rep = mm.check_last_start(self.lib)
        self.assertEqual(rep["result"], "crash")
        self.assertEqual(rep["since"], self.T1)
        self.assertEqual([m["name"] for m in rep["new"]], ["Alpha", "Better-sprint"])   # (a new version counts)
        self.assertEqual(mm.crash_lines(rep), ["The game stopped with an error last time.",
                                               "New since then: Alpha, Better-sprint."])
        results = [h for h in self.history() if h["type"] == "result" and h["start"] == self.T3]
        self.assertEqual([r["result"] for r in results], ["crash"])
        mm.check_last_start(self.lib)   # (read once: no second record)
        self.assertEqual(len([h for h in self.history() if h["type"] == "result" and h["start"] == self.T3]), 1)
        code, out = self.cli("crash")
        self.assertIn("New since then", out)

    def test_turn_it_off(self):
        self.three_starts("host backtrace (guest functions are f_x\n")
        code, out = self.cli("crash", "turn-off")
        self.assertEqual(code, 0, out)
        self.assertEqual({m["id"]: m["enabled"] for m in self.lib.mods}, {"alpha": False, "better-sprint": False})

    def test_run_says_it_and_still_starts(self):
        self.three_starts("host backtrace (guest functions are f_x\n")
        self.deploy()
        code, out = self.run_mm()
        self.assertEqual(code, 0, out)
        self.assertIn("Note: The game stopped with an error last time.", out)
        self.assertEqual(len(FakeProc.started), 1)

    def test_clean_unknown_and_a_log_older_than_the_start(self):
        self.three_starts("[vk] t=1\nwindow closed\n")
        self.assertEqual(mm.check_last_start(self.lib)["result"], "clean")
        self.record_start([("alpha", "1.1")], "2026-11-13T10:00:00")
        self.log("[vk] frame 10\n", self.at("2026-11-13T10:00:00"))   # (closed through the control port)
        self.assertEqual(mm.check_last_start(self.lib)["result"], "unknown")
        self.record_start([("alpha", "1.1")], "2026-11-14T10:00:00")
        self.log("host backtrace (guest functions are f_x\n", self.at("2026-11-14T10:00:00", -600))
        self.assertEqual(mm.check_last_start(self.lib)["result"], "unknown")   # (an older run's log)
        with mock.patch.object(mm, "game_running", return_value=True):
            self.record_start([("alpha", "1.1")], "2026-11-15T10:00:00")
            self.assertIsNone(mm.check_last_start(self.lib))   # (still running: nothing written)
        self.assertNotIn("2026-11-15T10:00:00", [h.get("start") for h in self.history()])

    def test_the_last_mods_that_worked(self):
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.mod("Bravo", {"romfs/Actor/Pack/Hero.txt": "b"})
        self.mod("Charlie", {"romfs/System/Version.txt": "c"})
        self.assertIsNone(mm.last_good(self.lib))
        self.record_start([("charlie", None), ("alpha", None)], self.T1)
        self.record_result(self.T1, "clean")
        self.record_start([("bravo", None)], self.T2)
        self.record_result(self.T2, "crash")
        self.record_start([], self.T3, no_mods=True)
        self.record_result(self.T3, "clean")
        good = mm.last_good(self.lib)
        self.assertEqual(good["start"]["time"], self.T1)   # (not the crash, not the start without mods)
        code, out = self.cli("last-good", "use")
        self.assertEqual(code, 0, out)
        lib = self.lib
        self.assertEqual([(m["id"], m["enabled"]) for m in lib.mods],
                         [("charlie", True), ("bravo", False), ("alpha", True)])


# ---------------------------------------------------------------- 6. what changed, 4. conflicts, 5. all on/off

class ChangesConflictsAll(QolBase):
    def test_what_changed_since_the_last_start(self):
        self.assertEqual(mm.changes_since(self.lib), ["No start yet: nothing to compare with."])
        self.mod("Alpha", {"mod.toml": mod_toml("someone.alpha", "Alpha", "1.0.0"), "romfs/Pack/Title.txt": "a"})
        self.mod("Bravo", {"romfs/Actor/Pack/Hero.txt": "b"})
        self.deploy()
        self.run_mm()
        self.assertEqual(mm.changes_since(self.lib), ["Nothing changed since the last start."])
        lib = self.lib
        lib.mods[0]["version"] = "1.1.0"
        lib.mods[1]["enabled"] = False
        lib.mods.append({"id": "charlie", "name": "Charlie", "enabled": True, "kinds": []})
        lib.save()
        self.assertEqual(mm.changes_since(self.lib), ["New: Charlie.", "Not on any more: Bravo.",
                                                      "Updated: Alpha 1.0.0 -> 1.1.0."])
        code, out = self.cli("changes")
        self.assertIn("New: Charlie.", out)

    def test_conflicts_in_plain_words(self):
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a", "romfs/System/Version.txt": "a"})
        self.mod("Bravo", {"romfs/Pack/Title.txt": "b", "romfs/System/Version.txt": "b"})
        self.mod("Charlie", {"romfs/Actor/Pack/Hero.txt": "c"})
        code, out = self.cli("conflicts")
        self.assertIn("Nothing deployed yet", out)
        self.deploy()
        self.assertEqual(mm.conflict_lines(self.lib), ["Bravo replaces 2 of Alpha's files (Bravo is later in the list)."])
        self.assertEqual(mm.conflict_lines(self.lib, "charlie"), [])
        self.assertEqual(len(mm.conflict_lines(self.lib, "alpha")), 1)
        code, out = self.cli("conflicts", "bravo")
        self.assertIn("Bravo replaces 2 of Alpha's files", out)

    def test_turn_all_off_and_on_skips_hidden_mods(self):
        for n in ("Alpha", "Bravo"):
            self.mod(n, {"romfs/Pack/Title.txt": n})
        lib = self.lib
        lib.mods.append({"id": "egg", "name": "Egg", "enabled": True, "kinds": [], "secret": True})
        lib.save()
        code, out = self.cli("disable", "--all")
        self.assertEqual(code, 0, out)
        self.assertIn("Turned off 2 mod(s)", out)
        self.assertEqual([m["enabled"] for m in self.lib.mods], [False, False, True])
        self.cli("enable", "--all")
        self.assertEqual([m["enabled"] for m in self.lib.mods], [True, True, True])
        code, out = self.cli("enable")
        self.assertEqual(code, 2)
        self.assertIn("--all", out)

    def test_turn_all_off_and_last_good_leave_the_mod_menu_on(self):
        """Review finding: Turn all off (and Use the last mods that worked) turned the Mod menu off too."""
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        d = self.libdir / "mods" / "modmenu-copy"
        d.mkdir(parents=True, exist_ok=True)
        lib = self.lib
        lib.mods.append({"id": "modmenu-copy", "name": "Mod menu", "enabled": True, "kinds": ["native"]})
        lib.save()
        (d / "mod.toml").write_text(f'[mod]\nid = "{mm.MODMENU_ID}"\nname = "Mod menu"\nversion = "1.0"\n',
                                    encoding="utf-8")
        code, out = self.cli("disable", "--all")
        self.assertEqual(code, 0, out)
        self.assertEqual({m["name"]: m["enabled"] for m in self.lib.mods}, {"Alpha": False, "Mod menu": True})
        self.assertTrue(mm.is_modmenu(self.lib, self.lib.mods[1]))
        self.assertFalse(mm.is_modmenu(self.lib, self.lib.mods[0]))

    def test_list_find(self):
        for n in ("Better Sprint", "Linkle"):
            self.mod(n, {"romfs/Pack/Title.txt": n})
        code, out = quiet(mm.main, ["list", "--find", "sprint", "--library", str(self.libdir)])
        self.assertIn("Better Sprint", out)
        self.assertNotIn("Linkle", out)


# ---------------------------------------------------------------- 7. mod info, pictures

class InfoAndPictures(QolBase):
    def test_info_from_mod_toml(self):
        self.mod("Base", {"mod.toml": mod_toml("someone.base", "Base", "1.0.0"), "romfs/Pack/Title.txt": "x"})
        toml = mod_toml("someone.sprint", "Better Sprint", "1.2.0", deps={"someone.base": ">=1.0"}).replace(
            'version = "1.2.0"\n', 'version = "1.2.0"\nauthors = ["giruka"]\ndescription = "Sprint for longer."\n'
            'url = "https://example.com/sprint"\nicon = "icon.png"\n')
        self.mod("Better Sprint", {"mod.toml": toml, "icon.png": tiny_png(), "romfs/Pack/Title.txt": "y"})
        info = mm.mod_info(self.lib, "2")
        self.assertEqual((info.name, info.version, info.authors, info.description, info.url),
                         ("Better Sprint", "1.2.0", ["giruka"], "Sprint for longer.", "https://example.com/sprint"))
        self.assertEqual(info.needs, ["someone.base >=1.0"])
        self.assertTrue(info.picture.endswith("icon.png"))
        self.assertEqual(info.picture_from, "the mod")
        self.assertEqual(mm.mod_info(self.lib, "1").needed_by, ["Better Sprint"])
        code, out = self.cli("info", "2")
        self.assertIn("By: giruka", out)
        self.assertIn("Link: https://example.com/sprint", out)

    def test_a_bnp_web_image_is_never_used(self):
        self.mod("Pack", {"romfs/Pack/Title.txt": "x", ".bnp/info.json": json.dumps(
            {"name": "Pack", "desc": "From BCML.", "url": "https://gamebanana.com/mods/1",
             "image": "https://images.gamebanana.com/x.png"})})
        info = mm.mod_info(self.lib, "1")
        self.assertEqual((info.description, info.url, info.picture), ("From BCML.", "https://gamebanana.com/mods/1",
                                                                       None))
        self.assertEqual(info.picture_from, "a letter tile")

    def test_choose_a_picture(self):
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        pic = write(self.tmp / "pics", {"a.png": tiny_png(), "fake.png": b"not a png", "b.jpg": b"\xff\xd8\xff\xe0jp"})
        code, out = self.cli("picture", "alpha", str(pic / "fake.png"))
        self.assertEqual(code, 2)
        self.assertIn("isn't a PNG picture", out)
        code, out = self.cli("picture", "alpha", str(pic / "a.png"))
        self.assertEqual(code, 0, out)
        chosen = self.libdir / "pictures" / "alpha" / "chosen.png"
        self.assertEqual(chosen.read_bytes(), tiny_png())
        self.assertEqual(mm.mod_info(self.lib, "alpha").picture_from, "your choice")
        code, out = self.cli("picture", "alpha", str(pic / "b.jpg"))
        if mm.feature("mod_pictures", "choose") is not None:   # (it reads the whole picture: a header isn't enough)
            self.assertEqual(code, 2, out)
            self.assertIn("can't be used", out)
            self.assertEqual(chosen.read_bytes(), tiny_png())   # (the earlier choice stays)
        else:
            self.assertFalse(chosen.exists())   # (one choice at a time)
            self.assertTrue((self.libdir / "pictures" / "alpha" / "chosen.jpg").is_file())
        self.cli("picture", "title", str(pic / "a.png"))
        self.assertEqual(mm.picture_choice(self.lib, mm.TITLE_PICK), str(self.libdir / "pictures" / "_title" /
                                                                          "chosen.png"))
        code, out = self.cli("picture", "alpha", "--clear")
        self.assertIn("Forgot the picture", out)
        self.assertIsNone(mm.picture_choice(self.lib, "alpha"))
        with mock.patch.object(mm, "PICTURE_MAX", 10):
            code, out = self.cli("picture", "mods-page", str(pic / "a.png"))
        self.assertIn("at most 8 MB", out)


# ---------------------------------------------------------------- 8. mod sets: copy, rename, delete, own saves

class ProfilesQol(QolBase):
    def test_copy_rename_delete(self):
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.assertEqual(self.cli("profile", "new", "Hard")[0], 0)
        self.assertEqual(self.cli("profile", "copy", "Hard", "Hard 2")[0], 0)
        self.assertEqual(self.lib.profile, "Hard")   # (a copy isn't used by itself)
        code, out = self.cli("profile", "rename", "Hard", "Very hard")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.lib.profile, "Very hard")
        self.assertEqual(mm.profile_names(self.lib), ["Default", "Hard 2", "Very hard"])
        self.assertFalse((self.libdir / "profiles" / "hard.json").exists())
        code, out = self.cli("profile", "rename", "Default", "Other")
        self.assertEqual(code, 2)
        self.cli("profile", "use", "Default")
        code, out = self.cli("profile", "delete", "Hard 2")
        self.assertEqual(code, 0, out)
        self.assertEqual(mm.profile_names(self.lib), ["Default", "Very hard"])

    def test_own_save_folder(self):
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.deploy()
        self.cli("profile", "new", "Overhaul")
        code, out = self.cli("profile", "own-save", "Default", "on")
        self.assertEqual(code, 2)
        code, out = self.cli("profile", "own-save", "Overhaul", "on", "--copy")
        self.assertEqual(code, 0, out)
        own = self.libdir / "profiles" / "overhaul" / "save"
        self.assertEqual((own / "save_0" / "game_data.sav").read_text(), "slot 0")
        # leaving the set and coming back keeps its own save (the set's file is written on the way out)
        self.cli("profile", "use", "Default")
        self.cli("profile", "use", "Overhaul")
        self.assertEqual(mm.profile_own_save(self.lib), str(own))
        self.deploy()
        code, out = self.run_mm()
        self.assertEqual(FakeProc.started[-1].kw["env"][mm.ENV_SAVE], str(own))
        self.assertEqual(self.starts()[-1]["save"], str(own))
        # a save folder given from outside wins (a test save, the co-op copy)
        other = write(self.tmp / "test-save", {"x.sav": "x"})
        self.run_mm(env={mm.ENV_SAVE: str(other)})
        self.assertEqual(FakeProc.started[-1].kw["env"][mm.ENV_SAVE], str(other))
        # rename moves the own folder with the set; off and delete keep it
        self.cli("profile", "rename", "Overhaul", "Big overhaul")
        own2 = self.libdir / "profiles" / "big-overhaul" / "save"
        self.assertTrue((own2 / "save_0" / "game_data.sav").is_file())
        code, out = self.cli("profile", "own-save", "Big overhaul", "off")
        self.assertIn("stays in", out)
        self.assertIsNone(mm.profile_own_save(self.lib))
        self.cli("profile", "use", "Default")
        code, out = self.cli("profile", "delete", "Big overhaul")
        self.assertTrue((own2 / "save_0" / "game_data.sav").is_file())

    def test_own_save_copy_is_all_or_nothing(self):
        # S3.11: a copy cut off partway (disk full) left half a save in the set's own folder, and own-save was on;
        # the next own-save on --copy then skipped the copy (the folder wasn't empty) and the game loaded that half.
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.deploy()
        self.cli("profile", "new", "Overhaul")
        self.cli("profile", "use", "Overhaul")
        own = self.libdir / "profiles" / "overhaul" / "save"
        real = shutil.copytree

        def half(src, dst, *a, **kw):   # (one file gets there, then the disk is full)
            os.makedirs(os.path.join(dst, "save_0"))
            shutil.copy2(os.path.join(src, "option.sav"), dst)
            raise OSError(28, "No space left on device")
        with mock.patch.object(mm.shutil, "copytree", side_effect=half):
            code, out = self.cli("profile", "own-save", "Overhaul", "on", "--copy")
        self.assertEqual(code, 2, out)
        self.assertIn("Nothing was changed", out)
        self.assertFalse(own.exists() and any(own.iterdir()), list(own.iterdir()) if own.exists() else None)
        self.assertFalse(Path(str(own) + mm.COPYING).exists())
        self.assertIsNone(mm.profile_own_save(mm.Library.load(str(self.libdir))))   # (the set as it was)
        # a cut-off copy's leftovers (the PC turned off) are thrown away; the next try copies the whole save
        write(Path(str(own) + mm.COPYING), {"save_0/old.sav": "half"})
        own.mkdir(parents=True, exist_ok=True)   # (an empty own folder: copied into)
        with mock.patch.object(mm.shutil, "copytree", side_effect=real):
            code, out = self.cli("profile", "own-save", "Overhaul", "on", "--copy")
        self.assertEqual(code, 0, out)
        self.assertEqual((own / "save_0" / "game_data.sav").read_text(), "slot 0")
        self.assertEqual((own / "option.sav").read_text(), "options")
        self.assertFalse((own / "save_0" / "old.sav").exists())
        self.assertFalse(Path(str(own) + mm.COPYING).exists())
        self.assertEqual(mm.profile_own_save(mm.Library.load(str(self.libdir))), str(own))
        # on again with --copy: the own save stays as it is (never copied over)
        (own / "option.sav").write_text("played")
        code, out = self.cli("profile", "own-save", "Overhaul", "on", "--copy")
        self.assertEqual(code, 0, out)
        self.assertIn("Not copied", out)
        self.assertEqual((own / "option.sav").read_text(), "played")

    def test_own_save_copy_waits_for_windows_to_let_go(self):
        # Review of S3.11: Windows Defender or the search indexer still reads the files just copied, and renaming the
        # folder is refused for a moment (access denied): tried again, not "free some space or close the program".
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.deploy()
        self.cli("profile", "new", "Overhaul")
        own = self.libdir / "profiles" / "overhaul" / "save"
        real = os.replace
        refused = []

        def busy(times):
            def replace(a, b):
                if str(a).endswith(mm.COPYING) and len(refused) < times:
                    refused.append(a)
                    raise PermissionError(13, "Access is denied")
                return real(a, b)
            return replace
        own.mkdir(parents=True)   # (an empty own folder, as own-save on without --copy leaves it)
        with mock.patch.object(mm.os, "replace", side_effect=busy(1)), mock.patch.object(mm.time, "sleep") as nap:
            code, out = self.cli("profile", "own-save", "Overhaul", "on", "--copy")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(refused), 1)
        nap.assert_called_once_with(mm.RENAME_WAIT)
        self.assertEqual((own / "save_0" / "game_data.sav").read_text(), "slot 0")
        self.assertFalse(Path(str(own) + mm.COPYING).exists())
        self.assertEqual(mm.profile_own_save(mm.Library.load(str(self.libdir))), str(own))
        # still refused after every try: plain words, the empty own folder is there again, no .copying left
        self.cli("profile", "new", "Second")
        own2 = self.libdir / "profiles" / "second" / "save"
        own2.mkdir(parents=True)
        refused.clear()
        with mock.patch.object(mm.os, "replace", side_effect=busy(99)), mock.patch.object(mm.time, "sleep") as nap:
            code, out = self.cli("profile", "own-save", "Second", "on", "--copy")
        self.assertEqual(code, 2, out)
        self.assertEqual(len(refused), mm.RENAME_TRIES)
        self.assertEqual(nap.call_count, mm.RENAME_TRIES - 1)
        self.assertIn("Windows is still checking", out)
        self.assertIn("try again in a moment", out)
        self.assertNotIn("free some space", out)
        self.assertTrue(own2.is_dir() and not any(own2.iterdir()))   # (as it was: nothing was changed)
        self.assertFalse(Path(str(own2) + mm.COPYING).exists())
        self.assertIsNone(mm.profile_own_save(mm.Library.load(str(self.libdir)), "Second"))

    def test_switching_backs_up_the_save_first(self):
        self.mod("Alpha", {"romfs/Pack/Title.txt": "a"})
        self.cli("profile", "new", "Other")
        code, out = self.cli("profile", "use", "Default")
        self.assertEqual(code, 0, out)
        self.assertIn("Your save was backed up first", out)
        self.assertEqual(self.ours()[0]["reason"], "before Default")


# ---------------------------------------------------------------- the font and settings commands

class FontAndSettingsCommands(QolBase):
    def test_font(self):
        if mm.feature("font", "choose") is None:
            self.skipTest("no tools/font.py")
        code, out = self.cli("font")
        self.assertEqual(code, 0, out)
        self.assertIn("Font in use: Game font", out)
        code, out = self.cli("font", "use", "game")
        self.assertEqual(code, 0, out)
        code, out = self.cli("font", "use", "no-such-font")
        self.assertEqual(code, 2)
        self.assertIn("No font mod", out)
        self.cli("font", "symbols", "on")
        self.assertTrue(mm.feature("font").load_settings(str(self.libdir))["fix_symbols"])

    def test_settings(self):
        if mm.feature("mod_settings", "rows") is None:
            self.skipTest("no tools/mod_settings.py")
        code, out = self.cli("settings")
        self.assertIn("No mod in the library has settings", out)
        toml = mod_toml("someone.sprint", "Sprint", "1.0.0", data={"romfs": "romfs"}) + (
            '[[settings]]\nkey = "mode"\ntype = "choice"\nlabel = "Mode"\napplies = "deploy"\ndefault = "slow"\n'
            'choices = [{value = "slow", label = "Slow", romfs = "opt/slow"}, '
            '{value = "fast", label = "Fast", romfs = "opt/fast"}]\n')
        self.mod("Sprint", {"mod.toml": toml, "romfs/Pack/Title.txt": "s", "opt/slow/Pack/Title.txt": "slow",
                            "opt/fast/Pack/Title.txt": "fast"})
        code, out = self.cli("settings", "sprint", "set", "mode", "fast")
        self.assertEqual(code, 0, out)
        self.assertIn("Mode: Fast", out)
        self.assertEqual(mm.read_setting_values(self.lib, "someone.sprint"), {"mode": "fast"})
        with mock.patch("sw_rebuild.game_running", return_value=True):
            code, out = self.cli("settings", "sprint", "reset")
        self.assertEqual(code, 2)
        self.assertIn("running", out)
