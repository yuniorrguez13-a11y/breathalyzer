"""Tests for tools/romfs_mods.py. Every mod and "game" here is made-up files in temp folders: no game files."""
import contextlib
import errno
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import romfs_mods as rm  # noqa: E402

RSTB = "System/Resource/ResourceSizeTable.product.srsizetable"


def make(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)
    return root


def make_zip(path: Path, files: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
    return path


def set_zip_method(path: Path, method: int) -> None:
    """Mark every member of a stored .zip as using `method` (as a Deflate64 .zip from Windows would)."""
    data = bytearray(path.read_bytes())
    for sig, off in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
        i = data.find(sig)
        while i >= 0:
            data[i + off:i + off + 2] = method.to_bytes(2, "little")
            i = data.find(sig, i + 4)
    path.write_bytes(bytes(data))


def run_cli(*args):
    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
        code = rm.main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


class MapPaths(unittest.TestCase):
    def targets(self, paths, **kw):
        entries, skipped, layouts = rm.map_paths(paths, **kw)
        return {e.member: e.target for e in entries}, dict(skipped), layouts

    def test_romfs_folder(self):
        t, _, layouts = self.targets(["romfs/Actor/Pack/A.txt"])
        self.assertEqual(t, {"romfs/Actor/Pack/A.txt": "Actor/Pack/A.txt"})
        self.assertEqual(layouts, ["romfs/"])

    def test_title_id_and_atmosphere(self):
        t, _, layouts = self.targets(["01007EF00011E000/romfs/Pack/B.txt",
                                      "atmosphere/contents/01007ef00011e000/romfs/UI/C.txt"])
        self.assertEqual(sorted(t.values()), ["Pack/B.txt", "UI/C.txt"])
        self.assertEqual(layouts, ["atmosphere/contents", "title-id folder"])

    def test_dlc_title_id_goes_to_aoc_0010(self):
        t, _, _ = self.targets(["01007EF00011F001/romfs/Map/MainField/D.txt", "01007EF00011E000/romfs/Pack/E.txt"])
        self.assertEqual(t["01007EF00011F001/romfs/Map/MainField/D.txt"], "aoc/0010/Map/MainField/D.txt")
        self.assertEqual(t["01007EF00011E000/romfs/Pack/E.txt"], "Pack/E.txt")
        t, _, _ = self.targets(["Wrap/01007EF00011E000/romfs/Pack/E.txt"])
        self.assertEqual(t["Wrap/01007EF00011E000/romfs/Pack/E.txt"], "Pack/E.txt")

    def test_base_and_dlc_trees_of_one_mod(self):
        for paths in (["01007EF00011E000/MyMod/romfs/Pack/A.txt", "01007EF00011F001/MyMod/romfs/Map/B.txt"],
                      ["atmosphere/contents/01007EF00011E000/romfs/Pack/A.txt",
                       "atmosphere/contents/01007EF00011F001/romfs/Map/B.txt"],
                      ["romfs/Pack/A.txt", "atmosphere/contents/01007EF00011E000/romfs/Pack/A.txt"]):
            with self.subTest(paths=paths):
                t, _, _ = self.targets(paths)
                self.assertEqual(len(t), 2)

    def test_several_romfs_trees_refused(self):
        # option folders, and an emulator's per-game folder holding several mods
        for paths, names in ((["30fps/romfs/Pack/Bootup.pack", "60fps/romfs/Pack/Bootup.pack"], "30fps/romfs, 60fps/romfs"),
                             (["ModA/romfs/Pack/A.txt", "ModB/romfs/Actor/B.txt"], "ModA/romfs, ModB/romfs"),
                             (["romfs/Pack/A.txt", "Optional/romfs/Pack/A.txt"], "Optional/romfs, romfs")):
            with self.subTest(paths=paths), self.assertRaises(rm.DeployError) as cm:
                rm.map_paths(paths)
            self.assertIn(names, str(cm.exception))
        rm.map_paths(["romfs/Pack/A.txt", "Extras/romfs/readme.txt"])  # (a file at a tree's root doesn't count)

    def test_bare_aoc_alone_is_wiiu(self):
        for paths in (["aoc/0010/Pack/AocMainField.pack"], ["Wrap/aoc/0010/Map/MainField/A-1/A.txt", "Wrap/x.txt"]):
            with self.subTest(paths=paths), self.assertRaises(rm.DeployError) as cm:
                rm.map_paths(paths)
            self.assertIn("Wii U", str(cm.exception))
        t, _, _ = self.targets(["aoc/0010/Map/G.txt"], context=("c:", "mods", "01007ef00011e000", "x"))
        self.assertEqual(t, {"aoc/0010/Map/G.txt": "aoc/0010/Map/G.txt"})
        t, _, _ = self.targets(["romfs/aoc/0010/Map/G.txt"])
        self.assertEqual(t, {"romfs/aoc/0010/Map/G.txt": "aoc/0010/Map/G.txt"})

    def test_title_id_above_the_mod_folder(self):
        t, _, _ = self.targets(["romfs/Map/F.txt"], context=("c:", "mods", "01007ef00011f001"))
        self.assertEqual(t["romfs/Map/F.txt"], "aoc/0010/Map/F.txt")
        t, _, _ = self.targets(["Map/F.txt"], context=("c:", "x", "01007ef00011f001", "romfs"))
        self.assertEqual(t["Map/F.txt"], "aoc/0010/Map/F.txt")

    def test_bare_tree_and_wrapper(self):
        t, s, layouts = self.targets(["MyMod/Actor/Pack/A.txt", "MyMod/System/B.txt", "MyMod/readme.txt",
                                      "MyMod/screens/UI/shot.png", "notes.txt"])
        self.assertEqual(t["MyMod/Actor/Pack/A.txt"], "Actor/Pack/A.txt")
        self.assertEqual(t["MyMod/readme.txt"], "readme.txt")  # (dropped later: not in a RomFS folder)
        self.assertEqual(s["MyMod/screens/UI/shot.png"], rm.R_UNKNOWN_TOP)
        self.assertEqual(s["notes.txt"], rm.R_OUTSIDE)
        self.assertEqual(layouts, ["RomFS folders"])

    def test_image_and_3d_editor_files_are_left_out(self):
        # (Shrek Hinox ships its texture work next to the models: the game reads none of it)
        t, s, _ = self.targets(["shrek/Model/Enemy_Giant.sbfres", "shrek/Model/1_FTEX/Body_Alb.dds",
                                "shrek/Model/1_FTEX/Body_Alb.gtx"])
        self.assertEqual(list(t), ["shrek/Model/Enemy_Giant.sbfres"])
        self.assertEqual(s["shrek/Model/1_FTEX/Body_Alb.dds"], rm.R_EDITING)
        self.assertEqual(s["shrek/Model/1_FTEX/Body_Alb.gtx"], rm.R_EDITING)
        t, s, _ = self.targets(["romfs/UI/Album/pict_000.jpg", "romfs/UI/a.PNG"])
        self.assertEqual((list(t), s), (["romfs/UI/Album/pict_000.jpg"], {"romfs/UI/a.PNG": rm.R_EDITING}))

    def test_overlay_style_tree_with_dlc(self):
        t, _, _ = self.targets(["aoc/0010/Map/G.txt", "Pack/H.txt"])
        self.assertEqual(t, {"aoc/0010/Map/G.txt": "aoc/0010/Map/G.txt", "Pack/H.txt": "Pack/H.txt"})

    def test_skips(self):
        t, s, _ = self.targets(["romfs/Actor/.DS_Store", "__MACOSX/romfs/Actor/x", "romfs/Actor/Thumbs.db",
                                "exefs/main.ips", "0100000000010000/romfs/Actor/x.txt",
                                "romfs/aoc/0011/Map/y.txt", "romfs/Actor/CON.txt", "romfs/Actor/ok.txt"])
        self.assertEqual(list(t), ["romfs/Actor/ok.txt"])
        self.assertEqual(s["romfs/Actor/.DS_Store"], rm.R_JUNK)
        self.assertEqual(s["__MACOSX/romfs/Actor/x"], rm.R_JUNK)
        self.assertEqual(s["exefs/main.ips"], rm.R_OUTSIDE)
        self.assertEqual(s["0100000000010000/romfs/Actor/x.txt"], rm.R_OTHER_GAME)
        self.assertEqual(s["romfs/aoc/0011/Map/y.txt"], rm.R_AOC)
        self.assertEqual(s["romfs/Actor/CON.txt"], rm.R_BAD_NAME)

    def test_wiiu_refused(self):
        with self.assertRaises(rm.DeployError):
            rm.map_paths(["content/Actor/Pack/A.txt", "aoc/0010/Map/B.txt"])

    def test_wiiu_refusals_point_to_wiiu_convert(self):
        for paths in (["content/Actor/Pack/A.txt", "aoc/0010/Map/B.txt"], ["Wrap/content/Pack/A.txt"],
                      ["aoc/0010/Pack/AocMainField.pack"]):
            with self.subTest(paths=paths), self.assertRaises(rm.WiiUModError) as cm:
                rm.map_paths(paths)
            msg = str(cm.exception)
            self.assertIn("python tools/wiiu_convert.py convert <mod> --out <new folder>", msg)
            self.assertIn("python tools/modman.py add converts it for you", msg)
            self.assertNotIn("UKMM", msg)
        self.assertTrue(issubclass(rm.WiiUModError, rm.DeployError))  # (callers catching DeployError still do)

    def test_several_trees_refused(self):
        with self.assertRaises(rm.DeployError):
            rm.map_paths(["ModA/Actor/A.txt", "ModB/Pack/B.txt"])


class Deploy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / "overlay"

    def tearDown(self):
        self.tmp.cleanup()

    def mod(self, name, files):
        return str(make(self.root / "mods" / name, files))

    def game(self, files=None, dlc=None):
        g = make(self.root / "game" / "romfs", files or {"Actor/Pack/A.txt": "game", "Pack/Boot.txt": "game",
                                                         "System/Version.txt": "1"})
        if dlc is not None:
            make(self.root / "game" / "aoc1", dlc)
        return str(g)

    def deploy(self, mods, **kw):
        return rm.deploy(mods, str(self.out), **kw)

    def test_folder_mod_is_hard_linked(self):
        a = self.mod("A", {"romfs/Actor/Pack/A.txt": "mod a"})
        man = self.deploy([a])
        dest = self.out / "Actor/Pack/A.txt"
        self.assertEqual(dest.read_text(), "mod a")
        self.assertTrue(os.path.samefile(dest, Path(a) / "romfs/Actor/Pack/A.txt"))
        self.assertEqual(man["files"]["Actor/Pack/A.txt"]["how"], "link")
        self.assertTrue(man["complete"])
        on_disk = json.loads((self.out / rm.MARKER).read_text(encoding="utf-8"))
        self.assertEqual(on_disk, man)

    def test_copy_when_link_fails(self):
        a = self.mod("A", {"romfs/Actor/Pack/A.txt": "mod a"})
        with mock.patch("os.link", side_effect=OSError(errno.EXDEV, "different drive")):
            man = self.deploy([a])
        dest = self.out / "Actor/Pack/A.txt"
        self.assertEqual(dest.read_text(), "mod a")
        self.assertFalse(os.path.samefile(dest, Path(a) / "romfs/Actor/Pack/A.txt"))
        self.assertEqual(man["files"]["Actor/Pack/A.txt"]["how"], "copy")

    def test_read_only_source_is_copied_not_linked(self):
        a = self.mod("A", {"romfs/Actor/Pack/A.txt": "mod a"})
        src = Path(a) / "romfs/Actor/Pack/A.txt"
        os.chmod(src, stat.S_IREAD)
        try:
            man = self.deploy([a])
            dest = self.out / "Actor/Pack/A.txt"
            self.assertFalse(os.path.samefile(dest, src))
            self.assertEqual(man["files"]["Actor/Pack/A.txt"]["how"], "copy")
            self.deploy([a])  # a second deploy clears the overlay; the mod's file keeps its read-only setting
            self.assertFalse(os.stat(src).st_mode & stat.S_IWRITE)
        finally:
            os.chmod(src, stat.S_IWRITE | stat.S_IREAD)

    @unittest.skipUnless(os.name == "nt", "the read-only setting is shared by hard links on Windows")
    def test_clearing_keeps_read_only_of_a_linked_file(self):
        a = self.mod("A", {"romfs/Actor/Pack/A.txt": "mod a"})
        src = Path(a) / "romfs/Actor/Pack/A.txt"
        self.deploy([a])
        dest = self.out / "Actor/Pack/A.txt"
        self.assertTrue(os.path.samefile(dest, src))
        os.chmod(src, stat.S_IREAD)  # the player made their file read-only after the deploy
        try:
            self.deploy([a])
            self.assertFalse(os.stat(src).st_mode & stat.S_IWRITE)
            self.assertEqual(src.read_text(), "mod a")
        finally:
            os.chmod(src, stat.S_IWRITE | stat.S_IREAD)

    def test_copy_option(self):
        a = self.mod("A", {"romfs/Actor/Pack/A.txt": "mod a"})
        man = self.deploy([a], copy=True)
        self.assertEqual(man["files"]["Actor/Pack/A.txt"]["how"], "copy")

    def test_zip_is_extracted_and_dlc_remapped(self):
        z = make_zip(self.root / "mods" / "Z.zip", {"Z/01007EF00011E000/romfs/Pack/P.txt": "zip base",
                                                     "Z/01007EF00011F001/romfs/Map/MainField/M.txt": "zip dlc"})
        man = self.deploy([str(z)])
        self.assertEqual((self.out / "Pack/P.txt").read_text(), "zip base")
        self.assertEqual((self.out / "aoc/0010/Map/MainField/M.txt").read_text(), "zip dlc")
        self.assertEqual({f["how"] for f in man["files"].values()}, {"copy"})
        self.assertTrue(man["files"]["aoc/0010/Map/MainField/M.txt"]["dlc"])
        self.assertEqual(man["mods"][0]["dlc_files"], 1)

    def test_conflicts_last_mod_wins(self):
        a = self.mod("A", {"romfs/Actor/Pack/A.txt": "a", "romfs/Actor/Pack/OnlyA.txt": "a"})
        b = self.mod("B", {"romfs/actor/pack/a.txt": "b"})  # same file on Windows: case doesn't matter
        man = self.deploy([a, b])
        self.assertEqual((self.out / "Actor/Pack/A.txt").read_text(), "b")
        self.assertEqual(man["conflicts"], [{"path": "Actor/Pack/a.txt", "mods": ["A", "B"], "winner": "B"}])
        self.assertEqual([(m["files"], m["deployed"]) for m in man["mods"]], [(2, 1), (1, 1)])
        self.assertEqual(sorted(os.listdir(self.out / "Actor")), ["Pack"])  # one folder, spelled as mod A did

    def test_refuses_foreign_folder_and_wipes_own(self):
        make(self.out, {"precious.txt": "keep"})
        a = self.mod("A", {"romfs/Actor/Pack/A.txt": "a"})
        with self.assertRaises(rm.DeployError):
            self.deploy([a])
        self.assertEqual((self.out / "precious.txt").read_text(), "keep")
        self.assertFalse((self.out / rm.MARKER).exists())

        (self.out / "precious.txt").unlink()
        self.deploy([a])
        b = self.mod("B", {"romfs/Pack/B.txt": "b"})
        self.deploy([b])
        self.assertEqual(sorted(os.listdir(self.out)), [rm.MARKER, "Pack"])
        self.assertEqual((Path(a) / "romfs/Actor/Pack/A.txt").read_text(), "a")  # removing the link kept the mod
        self.assertEqual(sorted(os.listdir(self.root)), ["mods", "overlay"])  # (no ~new or ~old left)

    def test_redeploy_refuses_files_it_did_not_write(self):
        a = self.mod("A", {"romfs/Pack/A.txt": "a"})
        self.deploy([a])
        make(self.out, {"My new mod/romfs/Pack/Mine.txt": "only copy", "notes.txt": "mine", "Pack/Extra.txt": "x"})
        (self.out / "Empty").mkdir()
        with self.assertRaises(rm.DeployError) as cm:
            self.deploy([self.mod("B", {"romfs/Pack/B.txt": "b"})])
        for name in ("My new mod/", "notes.txt", "Pack/Extra.txt"):
            self.assertIn(name, str(cm.exception))
        self.assertEqual((self.out / "My new mod/romfs/Pack/Mine.txt").read_text(), "only copy")
        self.assertEqual((self.out / "notes.txt").read_text(), "mine")
        self.assertTrue((self.out / "Empty").is_dir())
        self.assertEqual((self.out / "Pack/A.txt").read_text(), "a")  # the old overlay still works
        self.assertEqual(rm.env_for(str(self.out)), {rm.ENV_OVERLAY: str(self.out)})

    def test_marker_paths_outside_the_folder_are_ignored(self):
        victim = make(self.root, {"victim.txt": "keep"}) / "victim.txt"
        make(self.out, {"Pack/A.txt": "a", rm.MARKER: json.dumps(
            {"tool": rm.TOOL_ID, "complete": True, "files": {"../victim.txt": {}, "Pack/A.txt": {}, "C:/x": {}}})})
        self.deploy([self.mod("B", {"romfs/Pack/B.txt": "b"})])
        self.assertEqual(victim.read_text(), "keep")
        self.assertFalse((self.out / "Pack/A.txt").exists())

    def test_failed_redeploy_keeps_the_old_overlay(self):
        a = self.mod("A", {"romfs/Pack/A.txt": "a", f"romfs/{RSTB}": "t"})
        self.deploy([a])
        before = (self.out / rm.MARKER).read_bytes()
        z = make_zip(self.root / "mods" / "Bad.zip", {"romfs/Pack/New.txt": "hello world"})
        data = bytearray(z.read_bytes())
        data[data.find(b"hello world")] ^= 1  # (stored data no longer matches its CRC)
        z.write_bytes(bytes(data))
        with self.assertRaises(rm.DeployError) as cm:
            self.deploy([a, str(z)])
        self.assertIn("damaged", str(cm.exception))
        self.assertEqual((self.out / rm.MARKER).read_bytes(), before)
        self.assertEqual((self.out / "Pack/A.txt").read_text(), "a")
        self.assertFalse((self.out / "Pack/New.txt").exists())
        self.assertEqual(sorted(os.listdir(self.root)), ["mods", "overlay"])
        self.assertEqual(rm.env_for(str(self.out)), {rm.ENV_OVERLAY: str(self.out), rm.ENV_NO_HUD: "1"})

    def test_swap_refused_while_files_are_open(self):
        a = self.mod("A", {"romfs/Pack/A.txt": "a"})
        self.deploy([a])
        real = os.rename

        def rename(src, dst):
            if str(dst).endswith(rm.ASIDE):
                raise PermissionError(13, "Access is denied")
            return real(src, dst)
        with mock.patch.object(rm.os, "rename", rename), self.assertRaises(rm.DeployError) as cm:
            self.deploy([self.mod("B", {"romfs/Pack/B.txt": "b"})])
        self.assertIn("close Savage Wind", str(cm.exception))
        self.assertEqual(sorted(os.listdir(self.out)), [rm.MARKER, "Pack"])
        self.assertEqual(os.listdir(self.out / "Pack"), ["A.txt"])
        self.assertFalse(Path(str(self.out) + rm.STAGE).exists())

    def test_cut_short_deploy_is_cleaned_up(self):
        a = self.mod("A", {"romfs/Pack/A.txt": "a", "romfs/Pack/B.txt": "b", "romfs/UI/C.txt": "c"})
        real = rm._place

        def place(e, dest, *args):
            if dest.endswith("C.txt"):
                raise OSError(28, "No space left on device")
            return real(e, dest, *args)
        with mock.patch.object(rm, "_place", place), self.assertRaises(OSError):
            self.deploy([a])
        marker = json.loads((self.out / rm.MARKER).read_text(encoding="utf-8"))
        self.assertFalse(marker["complete"])
        self.assertEqual(sorted(marker["files"]), ["Pack/A.txt", "Pack/B.txt", "UI/C.txt"])
        with self.assertRaises(rm.DeployError):
            rm.env_for(str(self.out))
        self.assertEqual(run_cli("env", "--out", self.out)[0], 2)

        b = self.mod("B", {"romfs/Actor/D.txt": "d"})
        self.deploy([b])
        self.assertEqual(sorted(os.listdir(self.out)), [rm.MARKER, "Actor"])

    def test_leftover_new_and_old_folders(self):
        a = self.mod("A", {"romfs/Pack/A.txt": "a"})
        stage, aside = Path(str(self.out) + rm.STAGE), Path(str(self.out) + rm.ASIDE)
        for f in (stage, aside):
            make(f, {"Pack/A.txt": "x", rm.MARKER: json.dumps({"tool": rm.TOOL_ID, "files": {"Pack/A.txt": {}}})})
        self.deploy([a])
        self.assertFalse(stage.exists() or aside.exists())

        make(stage, {"mine.txt": "keep"})
        with self.assertRaises(rm.DeployError) as cm:
            self.deploy([a])
        self.assertIn("in the way", str(cm.exception))
        self.assertEqual((stage / "mine.txt").read_text(), "keep")

    def test_refuses_marker_from_someone_else(self):
        make(self.out, {rm.MARKER: json.dumps({"tool": "other"})})
        with self.assertRaises(rm.DeployError):
            self.deploy([self.mod("A", {"romfs/Pack/A.txt": "a"})])

    def test_refuses_overlap(self):
        a = self.mod("A", {"romfs/Pack/A.txt": "a"})
        with self.assertRaises(rm.DeployError):
            rm.deploy([a], str(Path(a) / "overlay"))
        with self.assertRaises(rm.DeployError):
            rm.deploy([a], str(self.root / "mods"))

    def test_same_manifest_twice(self):
        a = self.mod("A", {"romfs/Pack/A.txt": "a", "01007EF00011F001/romfs/Map/B.txt": "b"})
        self.deploy([a])
        first = (self.out / rm.MARKER).read_bytes()
        self.deploy([a])
        self.assertEqual((self.out / rm.MARKER).read_bytes(), first)

    def test_dry_run_writes_nothing(self):
        a = self.mod("A", {"romfs/Pack/A.txt": "a"})
        man = self.deploy([a], dry_run=True)
        self.assertTrue(man["dry_run"])
        self.assertFalse(self.out.exists())

    def test_rstb_flag_and_env(self):
        a = self.mod("A", {"romfs/Pack/A.txt": "a"})
        man = self.deploy([a])
        self.assertFalse(man["rstb"]["shipped"])
        self.assertTrue(any("No mod ships an RSTB" in w for w in man["warnings"]))
        self.assertEqual(rm.env_for(str(self.out), man), {rm.ENV_OVERLAY: str(self.out)})

        r = self.mod("R", {f"romfs/{RSTB}": "table"})
        man = self.deploy([a, r])
        self.assertEqual(man["rstb"], {"shipped": True, "from": "R", "mods": ["R"]})
        env = rm.env_for(str(self.out))  # (read back from the marker file)
        self.assertEqual(env, {rm.ENV_OVERLAY: str(self.out), rm.ENV_NO_HUD: "1"})
        self.assertTrue(any("RSTB comes from R only" in w for w in man["warnings"]))

    def test_packages_whose_sizes_were_checked_are_never_named_as_needing_entries(self):
        """A BCML or UKMM package tools/bnp.py or tools/ukmm.py installed had its sizes worked out at add (an empty
        .bnp/rstb.json: none needed): not named as a mod whose files may need entries, merged table or not."""
        a = self.mod("A", {"romfs/Pack/A.txt": "a"})
        b = self.mod("B", {"romfs/Pack/B.txt": "b", ".bnp/rstb.json": "{}"})
        u = self.mod("U", {"romfs/Pack/U.txt": "u", ".ukmm/ukmm.json": "{}"})
        r = self.mod("R", {f"romfs/{RSTB}": "table"})
        man = self.deploy([a, b, u, r])
        w = next(w for w in man["warnings"] if "RSTB comes from R only" in w)
        self.assertIn("files from A may need entries", w)
        self.assertNotIn("B", w.split("files from", 1)[1])
        self.assertNotIn("U", w.split("files from", 1)[1].split("may need")[0])
        merged = rm.MergedRstb(b"merged", ["R"])
        p = rm.plan([a, b, u, r], None, merged)
        w = next(w for w in rm.manifest_for(p, str(self.out), complete=False)["warnings"] if "no size changes" in w)
        self.assertIn("no size changes from A;", w)

    def test_dlc_marks_never_at_the_root(self):
        a = self.mod("A", {"romfs/System/AocVersion.txt": "x", "romfs/Pack/AocMainField.pack": "x",
                           "romfs/Pack/A.txt": "a", "01007EF00011F001/romfs/System/AocVersion.txt": "x"})
        man = self.deploy([a])
        self.assertFalse((self.out / "System/AocVersion.txt").exists())
        self.assertFalse((self.out / "Pack/AocMainField.pack").exists())
        self.assertTrue((self.out / "aoc/0010/System/AocVersion.txt").exists())
        self.assertEqual(man["mods"][0]["skipped"], {rm.R_DLC_MARK: 2})

    def test_dlc_movie_also_where_the_player_looks(self):
        a = self.mod("A", {"01007EF00011F001/romfs/Movie/Ballad.mp4": "film"})
        man = self.deploy([a])
        self.assertEqual((self.out / "aoc/0010/Movie/Ballad.mp4").read_text(), "film")
        self.assertEqual((self.out / "Movie/Ballad.mp4").read_text(), "film")
        self.assertTrue(man["files"]["Movie/Ballad.mp4"]["dlc_movie_copy"])
        self.assertEqual(man["mods"][0]["deployed"], 1)

    def test_game_check(self):
        g = self.game(dlc={"Map/MainField/M.txt": "dlc"})
        a = self.mod("A", {"romfs/actor/pack/a.txt": "longer than the game's",
                           "romfs/Pack/New.txt": "n", "romfs/NewDir/X.txt": "x", "romfs/root.txt": "r",
                           "01007EF00011F001/romfs/Map/MainField/M.txt": "d",
                           "01007EF00011F001/romfs/Map/Other/O.txt": "o"})
        man = self.deploy([a], game_romfs=str(Path(g).parent))  # (the extracted folder works too)
        self.assertTrue((self.out / "Actor/Pack/A.txt").exists())
        self.assertIn("Actor/Pack/A.txt", man["files"])  # spelled as the game spells it
        gc = man["game_check"]
        self.assertEqual((gc["replaced"], gc["new"]), (2, 1))
        self.assertCountEqual(gc["outside_game_dirs"], ["NewDir/X.txt", "aoc/0010/Map/Other/O.txt"])
        self.assertEqual(gc["bigger_than_game"], ["Actor/Pack/A.txt"])
        self.assertEqual(man["mods"][0]["skipped"], {rm.R_ROOT: 1})
        self.assertTrue(any("folders the game's RomFS doesn't have" in w for w in man["warnings"]))
        self.assertTrue(any("1 file(s) are bigger" in w for w in man["warnings"]))

    def test_game_without_dlc(self):
        g = self.game()
        a = self.mod("A", {"01007EF00011F001/romfs/Map/M.txt": "d"})
        man = self.deploy([a], game_romfs=g)
        self.assertEqual(man["game_check"]["dlc_unused"], ["aoc/0010/Map/M.txt"])

    def test_bare_tree_uses_game_folders(self):
        g = self.game({"Pack/A.txt": "g", "Odd/B.txt": "g"})
        a = self.mod("A", {"Odd/B.txt": "m", "Pack/A.txt": "m"})
        man = self.deploy([a], game_romfs=g)
        self.assertEqual(sorted(man["files"]), ["Odd/B.txt", "Pack/A.txt"])

    def test_not_enough_space(self):
        z = make_zip(self.root / "mods" / "Z.zip", {"romfs/Pack/P.txt": "x" * 1000})
        with mock.patch.object(rm.shutil, "disk_usage", return_value=mock.Mock(free=10)):
            with self.assertRaises(rm.DeployError):
                self.deploy([str(z)])
        self.assertFalse(self.out.exists())

    def test_refused_mods(self):
        ukmm = make_zip(self.root / "u.zip", {"meta.yml": "platform: !Specific Switch\n", "manifest.yml": "x\n"})
        bnp = self.mod("bnp", {"info.json": "{}", "logs/packs.json": "{}", "01007EF00011E000/romfs/Pack/A.txt": "a"})
        nothing = self.mod("empty", {"readme.txt": "hi"})
        for m in (str(ukmm), bnp, nothing, str(self.root / "missing")):
            with self.subTest(m=m), self.assertRaises(rm.DeployError):
                self.deploy([m])
        self.assertFalse(self.out.exists())

    def test_option_folders_refused(self):
        z = make_zip(self.root / "mods" / "FPS Mod.zip", {
            "30fps/romfs/Pack/Bootup.pack": "thirty", "30fps/romfs/Actor/Pack/Only30.txt": "x",
            "60fps/romfs/Pack/Bootup.pack": "sixty", "60fps/romfs/Actor/Pack/Only60.txt": "y"})
        with self.assertRaises(rm.DeployError) as cm:
            self.deploy([str(z)])
        self.assertIn("30fps/romfs, 60fps/romfs", str(cm.exception))
        self.assertFalse(self.out.exists())

    def test_same_file_in_two_layouts_of_one_mod(self):
        same = {"romfs/Pack/A.txt": "a", "atmosphere/contents/01007EF00011E000/romfs/Pack/A.txt": "a"}
        for m in (self.mod("Same", same), str(make_zip(self.root / "mods" / "Same.zip", same))):
            with self.subTest(m=m):
                man = self.deploy([m])
                self.assertEqual(list(man["files"]), ["Pack/A.txt"])
                self.assertEqual(man["conflicts"], [])
                self.assertEqual(man["mods"][0]["skipped"], {rm.R_SAME: 1})
        differ = {"romfs/Pack/A.txt": "a", "atmosphere/contents/01007EF00011E000/romfs/Pack/A.txt": "b"}
        for m in (self.mod("Differ", differ), str(make_zip(self.root / "mods" / "Differ.zip", differ))):
            with self.subTest(m=m), self.assertRaises(rm.DeployError) as cm:
                self.deploy([m])
            self.assertIn("two different files for Pack/A.txt", str(cm.exception))

    def test_wiiu_dlc_only_mods_refused(self):
        rules = "[Definition]\ntitleIds = 00050000101C9400,00050000101C9500,00050000101C9300\nname = tweak\n"
        pack = make_zip(self.root / "mods" / "WiiU tweak.zip",
                        {"rules.txt": rules, "aoc/0010/Map/MainField/A-1/A-1_Dynamic.txt": "big-endian"})
        plain = self.mod("WiiU DLC only", {"aoc/0010/Pack/AocMainField.txt": "be"})
        for m, why in ((str(pack), "Cemu graphic pack"), (plain, "Wii U")):
            with self.subTest(m=m), self.assertRaises(rm.DeployError) as cm:
                self.deploy([m])
            self.assertIn(why, str(cm.exception))
        self.assertFalse(self.out.exists())

    def test_wiiu_mods_are_sent_to_wiiu_convert_and_packages_to_ukmm(self):
        rules = "[Definition]\ntitleIds = 00050000101C9400\nname = made up\n"
        wiiu = {
            "content": self.mod("WiiU content", {"content/Actor/Pack/A.txt": "be", "readme.txt": "hi"}),
            "dlc only": self.mod("WiiU dlc", {"aoc/0010/Map/MainField/A.txt": "be"}),
            "cemu pack": str(make_zip(self.root / "mods" / "Cemu.zip",
                                      {"Pack/rules.txt": rules, "Pack/content/System/A.txt": "be"})),
        }
        for what, m in wiiu.items():
            with self.subTest(what=what), self.assertRaises(rm.WiiUModError) as cm:
                rm.load_mod(m)
            msg = str(cm.exception)
            self.assertTrue(msg.startswith(Path(m).name), msg)  # (the mod's name comes first, as before)
            self.assertIn("python tools/wiiu_convert.py convert <mod> --out <new folder>", msg)
            self.assertNotIn("UKMM", msg)
            code, _, err = run_cli("deploy", "--out", self.out, m)
            self.assertEqual(code, 2)
            self.assertIn("wiiu_convert.py", err)
        # a Cemu pack with no game files can't be converted: not a WiiUModError, and no UKMM either
        settings = make_zip(self.root / "mods" / "Settings.zip", {"Pack/rules.txt": rules, "Pack/patches.txt": "[x]"})
        with self.assertRaises(rm.DeployError) as cm:
            rm.load_mod(str(settings))
        self.assertNotIsInstance(cm.exception, rm.WiiUModError)
        self.assertIn("without game files", str(cm.exception))
        # UKMM stays the advice for packed packages, and for merging
        ukmm = make_zip(self.root / "u.zip", {"meta.yml": "platform: !Specific Switch\n", "manifest.yml": "x\n"})
        bnp = self.mod("bnp", {"info.json": "{}", "logs/packs.json": "{}", "01007EF00011E000/romfs/Pack/A.txt": "a"})
        for m in (str(ukmm), bnp):
            with self.subTest(m=m), self.assertRaises(rm.DeployError) as cm:
                rm.load_mod(m)
            self.assertNotIsInstance(cm.exception, rm.WiiUModError)
            self.assertIn("UKMM", str(cm.exception))
        man = self.deploy([self.mod("A", {"romfs/Pack/A.txt": "a"})], dry_run=True)
        self.assertTrue(any("Merge the mods with UKMM" in w for w in man["warnings"]))
        self.assertFalse(self.out.exists())

    def test_cemu_pack_for_another_game_is_not_sent_to_wiiu_convert(self):
        # a rules.txt without BotW's title id: no conversion can make it usable, with or without game files
        rules = "[Definition]\ntitleIds = 000500001010EC00\nname = made up\n"
        packs = {"with game files": self.mod("Other", {"rules.txt": rules, "content/Pack/Title.txt": "be"}),
                 "settings only": str(make_zip(self.root / "mods" / "Other.zip", {"Pack/rules.txt": rules}))}
        for what, m in packs.items():
            with self.subTest(what=what), self.assertRaises(rm.OtherGameError) as cm:
                rm.load_mod(m)
            msg = str(cm.exception)
            self.assertIn("made for another game", msg)
            self.assertNotIn("wiiu_convert", msg)
            self.assertNotIn("modman.py add", msg)
            code, _, err = run_cli("deploy", "--out", self.out, m)
            self.assertEqual(code, 2)
            self.assertIn("made for another game", err)
        self.assertFalse(self.out.exists())

    def test_wiiu_mod_wiiu_convert_cant_convert_is_not_sent_to_it(self):
        # only audio (and the RSTB): wiiu_convert keeps it as Wii U data, and modman add refuses such a mod (models
        # convert now: tools/wiiu_model)
        rstb = "content/System/Resource/ResourceSizeTable.product.srsizetable"
        mods = {"content": self.mod("Audio", {"content/Sound/A.bfstm": "be", rstb: "be"}),
                "dlc only": self.mod("Dlc Audio", {"aoc/0010/Sound/A.bfstm": "be"}),
                "cemu pack": str(make_zip(self.root / "mods" / "CemuModels.zip",
                                          {"Pack/rules.txt": "[Definition]\ntitleIds = 00050000101C9400\n",
                                           "Pack/content/Sound/A.bfstm": "be"}))}
        for what, m in mods.items():
            with self.subTest(what=what), self.assertRaises(rm.WiiUModError) as cm:
                rm.load_mod(m)
            msg = str(cm.exception)
            self.assertIn("tools/wiiu_convert.py can't convert yet", msg)
            self.assertNotIn("converts it for you", msg)

    def test_zip_compression_python_cant_read(self):
        good = self.mod("Good", {"romfs/Pack/Good.txt": "ok"})
        self.deploy([good])
        before = (self.out / rm.MARKER).read_bytes()
        z = make_zip(self.root / "mods" / "d64.zip", {"romfs/Pack/New.txt": "hello"})
        set_zip_method(z, 9)  # Deflate64
        code, _, err = run_cli("deploy", "--out", self.out, good, z)
        self.assertEqual(code, 2)
        self.assertIn("Deflate64", err)
        self.assertIn("7-Zip", err)
        self.assertEqual((self.out / rm.MARKER).read_bytes(), before)
        self.assertEqual(sorted(os.listdir(self.out / "Pack")), ["Good.txt"])

    def test_drive_without_hard_links(self):
        a = self.mod("A", {"romfs/Pack/A.txt": b"x" * 100000})
        no_links = mock.patch("os.link", side_effect=OSError(errno.EPERM, "no hard links on exFAT"))
        with no_links, mock.patch.object(rm.shutil, "disk_usage", return_value=mock.Mock(free=10)) as du:
            with self.assertRaises(rm.DeployError) as cm:
                self.deploy([a])
        self.assertTrue(du.called)
        self.assertIn("Not enough free space", str(cm.exception))
        self.assertFalse(self.out.exists())
        with no_links:
            man = self.deploy([a], dry_run=True)
        self.assertEqual(man["files"]["Pack/A.txt"]["how"], "copy")  # (known before any file is placed)

    def test_link_falls_back_to_copy_only_if_it_fits(self):
        a = self.mod("A", {"romfs/Pack/A.txt": b"x" * 100000})
        with mock.patch.object(rm, "_links_work", return_value=True), \
                mock.patch("os.link", side_effect=OSError(errno.EPERM, "no")), \
                mock.patch.object(rm.shutil, "disk_usage", return_value=mock.Mock(free=10)):
            with self.assertRaises(rm.DeployError) as cm:
                self.deploy([a])
        self.assertIn("Not enough free space", str(cm.exception))
        self.assertFalse((self.out / "Pack/A.txt").exists())

    def test_short_on_space_replaces_in_place(self):
        z = make_zip(self.root / "mods" / "Z.zip", {"romfs/Pack/P.txt": "x" * 3000})
        self.deploy([str(z)])
        # 3000 bytes to copy and 1000 free: only fits once the old copy (3000 bytes) is deleted
        with mock.patch.object(rm.shutil, "disk_usage", return_value=mock.Mock(free=1000)), \
                mock.patch.object(rm, "_deploy_staged", side_effect=AssertionError("staged")):
            man = self.deploy([str(z)])
        self.assertTrue(man["complete"])
        self.assertEqual((self.out / "Pack/P.txt").read_text(), "x" * 3000)
        with mock.patch.object(rm.shutil, "disk_usage", return_value=mock.Mock(free=10)):
            with self.assertRaises(rm.DeployError):
                self.deploy([str(z), str(make_zip(self.root / "mods" / "Y.zip", {"romfs/Pack/Q.txt": "y" * 3000}))])
        self.assertEqual(sorted(os.listdir(self.out / "Pack")), ["P.txt"])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return run_cli(*args)

    def test_deploy_and_env(self):
        a = make(self.root / "A", {"romfs/Pack/A.txt": "a", f"romfs/{RSTB}": "t"})
        b = make(self.root / "B.d", {"romfs/Pack/A.txt": "b"})
        out = self.root / "ov"
        code, text, _ = self.run_cli("deploy", "--out", out, a, b)
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("Deployed 2 mod(s): 2 file(s)"))
        self.assertIn("Pack/A.txt: A, B.d -> B.d", text)
        self.assertIn(f"{rm.ENV_NO_HUD}=1", text)
        code, text, _ = self.run_cli("env", "--out", out, "--format", "ps")
        self.assertEqual(code, 0)
        self.assertEqual(text.splitlines(), [f"$env:{rm.ENV_OVERLAY} = '{out}'", f"$env:{rm.ENV_NO_HUD} = '1'"])
        code, text, _ = self.run_cli("env", "--out", out, "--format", "cmd")
        self.assertEqual(text.splitlines()[0], f'set "{rm.ENV_OVERLAY}={out}"')

    def test_json_and_errors(self):
        a = make(self.root / "A", {"romfs/Pack/A.txt": "a"})
        code, text, _ = self.run_cli("deploy", "--out", self.root / "ov", "--dry-run", "--json", a)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["files"]["Pack/A.txt"]["mod"], "A")
        code, _, err = self.run_cli("env", "--out", self.root / "ov")
        self.assertEqual(code, 2)
        self.assertIn("deploy", err)

    def test_env_clears_hud_letters_without_an_rstb(self):
        out = self.root / "ov"
        r = make(self.root / "R", {"romfs/Pack/A.txt": "a", f"romfs/{RSTB}": "t"})
        self.assertEqual(self.run_cli("deploy", "--out", out, r)[0], 0)
        self.assertEqual(self.run_cli("env", "--out", out, "--format", "ps")[1].splitlines()[1],
                         f"$env:{rm.ENV_NO_HUD} = '1'")
        a = make(self.root / "A", {"romfs/Pack/A.txt": "a"})
        self.assertEqual(self.run_cli("deploy", "--out", out, a)[0], 0)
        lines = {f: self.run_cli("env", "--out", out, "--format", f)[1].splitlines() for f in ("ps", "cmd", "plain")}
        self.assertEqual(lines["ps"], [f"$env:{rm.ENV_OVERLAY} = '{out}'",
                                       f"Remove-Item Env:{rm.ENV_NO_HUD} -ErrorAction SilentlyContinue"])
        self.assertEqual(lines["cmd"], [f'set "{rm.ENV_OVERLAY}={out}"', f'set "{rm.ENV_NO_HUD}="'])
        self.assertEqual(lines["plain"], [f"{rm.ENV_OVERLAY}={out}"])  # (an empty value would still count as set)

    def test_json_is_ascii_and_round_trips(self):
        mod = make(self.root / "\u039c\u03bf\u03bd mod", {"romfs/Pack/A.txt": "a"})
        code, text, _ = self.run_cli("deploy", "--json", "--out", self.root / "\u03a9 ov", mod)
        self.assertEqual(code, 0)
        self.assertTrue(text.isascii())
        man = json.loads(text)
        self.assertEqual(man["mods"][0]["path"], str(mod))
        self.assertEqual(man["env"][rm.ENV_OVERLAY], str(self.root / "\u03a9 ov"))

    def test_env_lines_keep_every_letter(self):
        v = "C:\\Jos\u00e9's \u2019\U0001F600 %x%"
        ps = rm._env_line("N", v, "ps")
        self.assertTrue(ps.isascii())
        self.assertEqual(ps, "$env:N = 'C:\\Jos' + [char]0x00E9 + '''s ' + [char]0x2019 + [char]0xD83D + "
                             "[char]0xDE00 + ' %x%'")
        self.assertEqual(rm._env_line("N", "\u03a9", "ps"), "$env:N = '' + [char]0x03A9")
        self.assertEqual(rm._env_line("N", "", "ps"), "$env:N = ''")
        self.assertEqual(rm.env_lines({rm.ENV_OVERLAY: v, rm.ENV_NO_HUD: "1"}, "cmd"),
                         ["chcp 65001 >nul", f'set "{rm.ENV_OVERLAY}=C:\\Jos\u00e9\'s \u2019\U0001F600 %%x%%"',
                          f'set "{rm.ENV_NO_HUD}=1"'])
        self.assertEqual(rm.env_lines({rm.ENV_OVERLAY: "C:\\a"}, "cmd")[0], f'set "{rm.ENV_OVERLAY}=C:\\a"')

    def test_env_output_is_utf8_when_piped(self):
        mod = make(self.root / "A", {"romfs/Pack/A.txt": "a"})
        out = self.root / "\u03a9 ov"
        self.assertEqual(self.run_cli("deploy", "--out", out, mod)[0], 0)
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        tool = Path(__file__).resolve().parent.parent / "tools" / "romfs_mods.py"
        r = subprocess.run([sys.executable, str(tool), "env", "--out", str(out)], capture_output=True, env=env,
                           timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.decode("utf-8").strip(), f"{rm.ENV_OVERLAY}={out}")

    def test_unexpected_errors_are_reported(self):
        a = make(self.root / "A", {"romfs/Pack/A.txt": "a"})
        with mock.patch.object(rm, "deploy", side_effect=NotImplementedError("That compression method is not supported")):
            code, _, err = self.run_cli("deploy", "--out", self.root / "ov", a)
        self.assertEqual(code, 2)
        self.assertIn("not supported", err)



# ---------------------------------------------------------------- rule S2: size-table entries for files made at deploy

def _table(entries: dict, names: dict | None = None) -> bytes:
    """A made-up Switch size table (Yaz0, as the game ships it), built in memory."""
    import zlib
    import wiiu_convert as wc
    crcs = [(zlib.crc32(k.encode()) & 0xFFFFFFFF, v) for k, v in entries.items()]
    raw = wc.rstb_build(crcs, [(k.encode(), v) for k, v in (names or {}).items()])
    return wc.yaz0_compress(raw, 0)


def _sizes(data: bytes) -> dict:
    import wiiu_convert as wc
    crcs, named = wc.rstb_load(wc.yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data)
    out = {c: v for c, v in crcs}
    out.update({n.split(b"\0", 1)[0].decode(): v for n, v in named})
    return out


class SizeTableMerge(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def crc(self, name):
        import zlib
        return zlib.crc32(name.encode()) & 0xFFFFFFFF

    def game(self, table: bytes | None):
        files = {"romfs/Pack/Bootup.pack": b"game pack", "romfs/Actor/Pack/A.txt": "a"}
        if table is not None:
            files["romfs/" + RSTB] = table
        make(self.tmp / "extracted", files)
        return rm.GameIndex.scan(str(self.tmp / "extracted"))

    def test_merges_into_the_mods_table_like_linkle(self):
        g = self.game(_table({"Layout/Common.blarc": 1000, "Actor/Pack/A.bactorpack": 50}))
        linkle = make(self.tmp / "linkle", {"romfs/" + RSTB: _table({"Layout/Common.blarc": 1000, "Actor/Pack/A.bactorpack":
                                                                      90, "Model/Linkle.bfres": 777}),
                                            "romfs/Actor/Pack/A.txt": "linkle"})
        p = rm.plan([str(linkle)], g)
        rm.add_generated(p, [("Pack/Bootup.pack", b"made", ("Layout/Common.blarc", 5000))], "generated:pictures")
        w = p.winner(RSTB)
        self.assertEqual(w.mod, "generated:pictures")
        got = _sizes(w.read())
        self.assertEqual(got[self.crc("Model/Linkle.bfres")], 777)       # (the mod's own entries stay)
        self.assertEqual(got[self.crc("Actor/Pack/A.bactorpack")], 90)
        self.assertEqual(got[self.crc("Layout/Common.blarc")], 5000)
        self.assertEqual(p.generated[w.target]["built_on"], "linkle")
        man = rm.manifest_for(p, str(self.tmp / "ov"), complete=True, dry_run=True)
        self.assertTrue(man["rstb"]["shipped"])
        self.assertEqual(man["rstb"]["added"], {"Layout/Common.blarc": 5000})
        self.assertIn(rm.ENV_NO_HUD, man["env"])
        self.assertTrue(any("linkle's size table with them added" in x for x in man["warnings"]), man["warnings"])

    def test_only_raises_entries(self):
        g = self.game(_table({"Layout/Common.blarc": 9000}))
        p = rm.plan([], g)
        rm.add_generated(p, [("Pack/Bootup.pack", b"made", ("Layout/Common.blarc", 5000))], "generated:pictures")
        self.assertIsNone(p.winner(RSTB).mod)           # (the game's table fits: no table shipped)
        self.assertEqual(p.winner("Pack/Bootup.pack").read(), b"made")

    def test_game_table_when_no_mod_ships_one(self):
        g = self.game(_table({"Layout/Common.blarc": 1000}, names={"Long/Name.bxml": 4}))
        p = rm.plan([], g)
        rm.add_generated(p, [("Pack/Bootup.pack", b"made", ("Layout/Common.blarc", 5000)),
                             ("Pack/Other.pack", b"x", ("Long/Name.bxml", 8))], "generated:pictures")
        got = _sizes(p.winner(RSTB).read())
        self.assertEqual((got[self.crc("Layout/Common.blarc")], got["Long/Name.bxml"]), (5000, 8))
        self.assertIsNone(p.generated[p.winner(RSTB).target]["built_on"])
        out = self.tmp / "ov"
        man = rm.deploy([], str(out), plan_=p)
        self.assertTrue((out / RSTB).is_file())
        self.assertEqual(man["files"][RSTB]["mod"], "generated:pictures")
        self.assertTrue(any("the game's size table" in x for x in man["warnings"]))

    def test_two_makers_share_one_table(self):
        g = self.game(_table({"Layout/Common.blarc": 1000, "Font/Font_US.bfarc": 10}))
        p = rm.plan([], g)
        rm.add_generated(p, [("Font/Font_US.sbfarc", b"font", ("Font/Font_US.bfarc", 20))], "generated:fonts")
        rm.add_generated(p, [("Pack/Bootup.pack", b"made", ("Layout/Common.blarc", 5000))], "generated:pictures")
        got = _sizes(p.winner(RSTB).read())
        self.assertEqual((got[self.crc("Font/Font_US.bfarc")], got[self.crc("Layout/Common.blarc")]), (20, 5000))
        g2 = p.generated[p.winner(RSTB).target]
        self.assertIsNone(g2["built_on"])
        self.assertEqual(g2["sizes"], {"Font/Font_US.bfarc": 20, "Layout/Common.blarc": 5000})

    def test_no_table_known_leaves_the_file_out(self):
        p = rm.plan([])
        taken = rm.add_generated(p, [("Pack/Bootup.pack", b"made", ("Layout/Common.blarc", 5000))],
                                 "generated:pictures")
        self.assertEqual(taken, [])
        self.assertIsNone(p.winner("Pack/Bootup.pack"))
        self.assertTrue(any("needs a bigger size-table" in w for w in p.warnings))

    def test_a_damaged_table_leaves_the_file_out(self):
        g = self.game(b"not a size table")
        p = rm.plan([], g)
        rm.add_generated(p, [("Pack/Bootup.pack", b"made", ("Layout/Common.blarc", 5000))], "generated:pictures")
        self.assertIsNone(p.winner("Pack/Bootup.pack").mod)
        self.assertTrue(any("can't be read" in w for w in p.warnings), p.warnings)

    def test_rstb_name(self):
        self.assertEqual(rm.rstb_name("Layout/Common.sblarc"), "Layout/Common.blarc")
        self.assertEqual(rm.rstb_name("Pack/Bootup.pack"), "Pack/Bootup.pack")
        self.assertEqual(rm.rstb_name("Font/Font_US.sbfarc"), "Font/Font_US.bfarc")


if __name__ == "__main__":
    unittest.main()
