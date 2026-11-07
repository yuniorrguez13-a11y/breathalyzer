"""Tests for tools/check_clean.py, with made-up dummy files in a temporary folder (nothing here comes from the game)."""
import contextlib
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import check_clean as cc  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HAVE_GIT = shutil.which("git") is not None


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def png(w, h, color=6, level=4, extra=b""):
    """A tiny made-up PNG: zlib level 2-5 gives the 78 5E header stb_image_write uses; extra goes before the IDAT."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + b"\x10" * (w * 4) for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0)) + extra
            + chunk(b"IDAT", zlib.compress(rows, level)) + chunk(b"IEND", b""))


class CheckCleanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, rel, data):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def check(self, folder=None):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            code = cc.main([str(folder or self.root)])
        return code, out.getvalue()

    def blocked(self, folder=None):
        """main()'s exit code and the paths it blocked, with their reasons."""
        code, out = self.check(folder)
        found = {}
        for ln in out.splitlines():
            if ln.startswith("BLOCKED "):
                rel, _, why = ln[len("BLOCKED "):].partition(": ")
                found[rel] = why
        return code, found

    def assertBlocked(self, rels, why=None, passes=()):
        code, found = self.blocked()
        for rel in rels:
            self.assertIn(rel, found, f"{rel} should be blocked; got {found}")
            if why:
                self.assertIn(why, found[rel])
        for rel in passes:
            self.assertNotIn(rel, found)
        self.assertEqual(code, 1 if rels else 0, found)
        return found

    def test_clean_repo_passes(self):
        self.put("src/mod.c", b"int x;\n")
        self.put("README.md", b"# hi\n")
        self.assertEqual(self.check()[0], 0)

    def test_magic_bytes_blocked(self):
        self.put("notes/thing.bin", b"SARC" + b"\0" * 16)
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("starts like a game file", out)

    def test_byml_blocked_but_plain_text_not(self):
        self.put("a.dat", b"YB\x02\x00rest")
        self.put("b.txt", b"BYE for now\n")
        code, out = self.check()
        self.assertIn("a.dat", out)
        self.assertNotIn("b.txt", out)

    def test_extension_and_folder_blocked(self):
        self.put("x/Bootup.pack", b"x")
        self.put("extracted/romfs/readme.txt", b"x")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("Bootup.pack", out)
        self.assertIn("extracted/romfs/readme.txt", out)

    def test_keys_blocked(self):
        self.put("k.txt", b"header_key" + b" = " + b"ab" * 32 + b"\n")
        code, out = self.check()
        self.assertIn("console keys", out)

    def test_allowlist(self):
        self.put("art/logo.bin", b"SARC")
        self.put("art/other.bin", b"SARC")
        self.put(".cleanallow", b"# made up for this test\n  # an indented comment\nart/logo.bin\r\n./art\\other.bin\n")
        self.assertEqual(self.check()[0], 0)

    # --- clean-1: a folder given on the command line is walked, whatever .gitignore says ---

    @unittest.skipUnless(HAVE_GIT, "needs git")
    def test_folder_ignored_by_git_is_still_checked(self):
        self.assertEqual(git(self.root, "init", "-q").returncode, 0)
        self.put(".gitignore", b"dist/\n")
        self.put("dist/Pack.bin", b"SARC" + b"\0" * 16)
        self.put("dist/sub/notes.txt", b"plain\n")
        self.assertEqual(git(self.root, "check-ignore", "-q", "dist/Pack.bin").returncode, 0)  # (git skips it)
        code, found = self.blocked(self.root / "dist")
        self.assertEqual(code, 1)
        self.assertEqual(set(found), {"Pack.bin"})
        code, out = self.check(self.root / "dist")
        self.assertIn("2 file(s) checked", out)
        code, found = self.blocked(self.root)  # (the repo's own top folder, named explicitly: walked too)
        self.assertEqual((code, set(found)), (1, {"dist/Pack.bin"}))

    def test_missing_folder_or_a_file_is_an_error(self):
        self.put("a.txt", b"x")
        self.assertEqual(self.check(self.root / "nope")[0], 2)
        self.assertEqual(self.check(self.root / "a.txt")[0], 2)

    def test_empty_folder_is_clean(self):
        code, out = self.check()
        self.assertEqual(code, 0)
        self.assertIn("Clean: 0 file(s) checked", out)

    def test_upstream_checked_in_a_folder_but_skipped_in_the_repo_without_git(self):
        self.put("upstream/x/swgame.dll", b"x")
        self.put(".git/objects/blob", b"SARC")
        self.put("src/a.c", b"int a;\n")
        self.assertBlocked(["upstream/x/swgame.dll"])  # (.git is skipped)
        with mock.patch.object(cc.subprocess, "run", side_effect=OSError("no git")):
            self.assertEqual(cc.repo_files(self.root), (["src/a.c"], []))

    def test_repo_walked_when_git_lists_nothing(self):
        self.put("dist/Pack.bin", b"SARC")
        self.put("upstream/y.bin", b"SARC")
        with mock.patch.object(cc.subprocess, "run", return_value=types.SimpleNamespace(stdout=b"")):
            self.assertEqual(cc.repo_files(self.root), (["dist/Pack.bin"], []))
        with mock.patch.object(cc.subprocess, "run", return_value=types.SimpleNamespace(stdout=b"b.txt\0a.txt\0")):
            self.assertEqual(cc.repo_files(self.root), (["a.txt", "b.txt"], []))

    def test_unreadable_folder_blocked(self):
        self.put("ok/a.txt", b"x")
        self.put("locked/b.bin", b"SARC")
        real = os.scandir

        def scandir(path):
            if Path(path).name == "locked":
                raise PermissionError(13, "Access is denied")
            return real(path)
        with mock.patch.object(cc.os, "scandir", side_effect=scandir):
            found = self.assertBlocked(["locked/"], "can't read this folder", passes=["ok/a.txt"])
        self.assertEqual(len(found), 1)

    def test_link_to_a_folder_blocked_not_followed(self):
        with tempfile.TemporaryDirectory() as other:
            Path(other, "Pack.bin").write_bytes(b"SARC")
            link = self.root / "romfs-link"
            try:
                os.symlink(other, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                try:
                    import _winapi
                    _winapi.CreateJunction(other, str(link))
                except (ImportError, AttributeError, OSError):
                    self.skipTest("can't make a folder link here")
            try:
                found = self.assertBlocked(["romfs-link/"], "a link to another folder")
                self.assertNotIn("romfs-link/Pack.bin", found)
            finally:
                (os.rmdir if os.name == "nt" else os.unlink)(link)  # (removes the link, not the folder it points to)

    # --- clean-2: C code and objects made by Savage Wind's generator, and the folders our tools fill ---

    def test_generated_c_blocked_anywhere(self):
        chunk = (b'#include "cpu.h"\nvoid f_7012345670(Cpu *c);\n'
                 b"void f_7012345680(Cpu *c) {\n    FN_ENTER(0x7012345680ull);\n    c->x[0] = 1;\n}\n")
        self.put("rb/work/main/main_000.c", chunk)
        self.put("mine/odd_name.h", chunk)
        self.put("mine/sdk_table.c", b'#include "cpu.h"\nconst uint64_t sdk_fn_addrs[] = {\n0x7099999990ull};\n'
                                     b"const GuestFn sdk_fn_ptrs[] = {\nf_7099999990};\n"
                                     b"void f_7099999990(struct Cpu *c);\n")
        self.put("mine/layout.c", b'#include "gamelib.h"\nconst GameLibModule game_modules[] = {\n};\n')
        self.put("src/hook.c", b"// hooks f_7012345670 (0x7012345670)\n"
                               b"static void before(struct Cpu *c) { (void)c; }\n")
        self.put("docs/hooks.md", b"A generated function looks like `void f_7012345670(Cpu *c)`.\n")
        self.assertBlocked(["rb/work/main/main_000.c", "mine/odd_name.h", "mine/sdk_table.c", "mine/layout.c"],
                           "generated from the game", passes=["src/hook.c", "docs/hooks.md"])

    def test_pasted_generator_output_blocked_in_any_file(self):
        many = b"".join(b"void f_70%08x(Cpu *c);\n" % (0x100 * i) for i in range(cc.GEN_FN_MANY))
        self.put("notes/chunk.txt", many)
        self.put("tests/test_gen.py", b'CHUNK = """\n' + many[: len(many) // 2] + b'"""\n')
        self.assertBlocked(["notes/chunk.txt"], "generated from the game", passes=["tests/test_gen.py"])

    def test_objects_from_generated_code_blocked(self):
        self.put("x/main_000.o", b"\x7fELF" + b"\0" * 64)
        self.put("x/subsdk0_table.obj", b"\x64\x86" + b"\0" * 64)
        self.put("y/chunk.obj", b"\x64\x86" + b"\0" * 200 + b"\0f_7012345670\0" + b"\0" * 16)
        self.put("y/libgame.a", b"!<arch>\n" + b"\0" * 32 + b"\0_f_70aa00bb10\0")
        self.put("build/hook.o", b"\x7fELF" + b"\0" * 64 + b"\0before_set_stamina\0swmod_init\0")
        self.put("build/libmods.a", b"!<arch>\n" + b"\0f_main\0")
        found = self.assertBlocked(["x/main_000.o", "x/subsdk0_table.obj", "y/chunk.obj", "y/libgame.a"],
                                   passes=["build/hook.o", "build/libmods.a"])
        self.assertIn("named like Savage Wind's generated game code", found["x/main_000.o"])
        self.assertIn("f_<address>", found["y/chunk.obj"])

    def test_folders_with_a_tool_marker_blocked(self):
        for i, mark in enumerate(cc.TOOL_MARKERS):
            self.put(f"t{i}/{mark}", b"{}\n")
            self.put(f"t{i}/deep/notes.txt", b"plain text\n")
        self.put("mine/notes.txt", b"plain text\n")
        found = self.assertBlocked([f"t{i}/deep/notes.txt" for i in range(len(cc.TOOL_MARKERS))],
                                   "a tool filled from the game", passes=["mine/notes.txt"])
        self.assertIn(cc.TOOL_MARKERS[0], found["t0/deep/notes.txt"])
        code, found = self.blocked(self.root / "t0" / "deep")  # (a folder below the marker: still blocked)
        self.assertEqual((code, list(found)), (1, ["notes.txt"]))

    # --- clean-3: RomFS files that no file type or magic used to catch ---

    def test_romfs_assets_blocked_wherever_they_are(self):
        self.put("mymod/Terrain/A/MainField.tscb", b"TSCB" + b"\0" * 32)
        self.put("x/terrain.bin", b"TSCB" + b"\0" * 32)
        self.put("mymod/Movie/Demo104_1.webm", b"\x1a\x45\xdf\xa3" + b"\0" * 32)
        self.put("clips/Demo999_0.mp4", b"\0\0\0\x18ftypmp42")
        self.put("mymod/UI/PictureBook/Animal_Test_A.jpg", b"\xff\xd8\xff\xe0" + b"\0" * 32)
        self.put("mymod/UI/Album/pict_000.jpg", b"\xff\xd8\xff\xe0" + b"\0" * 32)
        self.put("mymod/System/Version.txt", b"0.0.0\n")
        self.put("overlay2/System/RegionLangMask.txt", b"made up\n")
        self.put("docs/demo.webm", b"\x1a\x45\xdf\xa3" + b"\0" * 32)
        self.put("docs/img/logo.jpg", b"\xff\xd8\xff\xe0" + b"\0" * 32)
        self.put("notes/System/readme.txt", b"plain\n")
        self.put("docs/version.txt", b"0.0.0\n")
        found = self.assertBlocked(
            ["mymod/Terrain/A/MainField.tscb", "x/terrain.bin", "mymod/Movie/Demo104_1.webm", "clips/Demo999_0.mp4",
             "mymod/UI/PictureBook/Animal_Test_A.jpg", "mymod/UI/Album/pict_000.jpg", "mymod/System/Version.txt",
             "overlay2/System/RegionLangMask.txt"],
            passes=["docs/demo.webm", "docs/img/logo.jpg", "notes/System/readme.txt", "docs/version.txt"])
        self.assertIn("starts like a game file", found["x/terrain.bin"])
        self.assertIn("cutscene", found["mymod/Movie/Demo104_1.webm"])
        self.assertIn("RomFS", found["mymod/UI/Album/pict_000.jpg"])

    # --- clean-4: saves, code maps, Savage Wind logs and screenshots ---

    def test_saves_blocked(self):
        self.put("tests/fixtures/save_0/0/game_data.sav", b"\x30\x47\0\0" + b"\xff" * 16)
        self.put("tests/fixtures/save_0/0/caption.jpg", b"\xff\xd8\xff\xe0" + b"\0" * 16)
        self.put("tests/fixtures/save_0/tracker/trackblock00.sav", b"\0" * 16)
        self.put("loose/caption.jpg", b"\xff\xd8\xff\xe0" + b"\0" * 16)
        self.put("loose/caption.sav", b"\0" * 16)
        self.put("pics/caption.jpg", b"\xff\xd8\xff\xe0" + b"\0" * 16)
        self.put("save_notes/readme.txt", b"plain\n")
        found = self.assertBlocked(
            ["tests/fixtures/save_0/0/game_data.sav", "tests/fixtures/save_0/0/caption.jpg",
             "tests/fixtures/save_0/tracker/trackblock00.sav", "loose/caption.jpg", "loose/caption.sav"],
            passes=["pics/caption.jpg", "save_notes/readme.txt"])
        self.assertIn("save folder", found["tests/fixtures/save_0/0/caption.jpg"])
        self.assertIn(".sav", found["loose/caption.sav"])

    def test_code_maps_blocked(self):
        self.put("notes/functions.csv", b"address,name,kind,notes\r\n0x7000000000,made_up,func,\r\n")
        self.put("notes/classes.csv", b"class,typeinfo,vtables,bases,virtuals,rtti_kind,mangled\n")
        self.put("notes/summary.txt", b"\xef\xbb\xbfCode map of main, build 0011223344556677\nmade up\n")
        self.put("data/other.csv", b"address,value\n0x10,1\n")
        self.put("docs/code-map.md", b"functions.csv starts with address,name,kind,notes\n")
        self.assertBlocked(["notes/functions.csv", "notes/classes.csv", "notes/summary.txt"], "code map",
                           passes=["data/other.csv", "docs/code-map.md"])

    def test_savage_wind_logs_blocked(self):
        self.put("logs/run.txt", b"[savagewind] Savage Wind 0.0.0-made-up (commit 0000000)\n[vk] made up\n")
        self.put("logs/tail.txt", b"[vk] made up\n[gamelib]   main     made up\n")
        self.put("logs/savagewind.log", b"")
        self.put("logs/build.txt", b"[modhost] loaded 0 mods\nok\n")
        self.put("docs/log.md", b"The log starts with `[savagewind] Savage Wind <version>`.\n")
        found = self.assertBlocked(["logs/run.txt", "logs/tail.txt", "logs/savagewind.log"],
                                   passes=["logs/build.txt", "docs/log.md"])
        self.assertIn("Savage Wind log", found["logs/run.txt"])

    def test_game_screenshots_blocked(self):
        self.put("docs/img/shot.png", png(32, 18))
        self.put("docs/img/zlib6.png", png(32, 18, level=6))
        self.put("docs/img/square.png", png(32, 32))
        self.put("docs/img/rgb.png", png(32, 18, color=2))
        self.put("docs/img/srgb.png", png(32, 18, extra=b"\0\0\0\1sRGB\0\xae\xce\x1c\xe9"))
        self.assertBlocked(["docs/img/shot.png"], "screenshot of the game",
                           passes=["docs/img/zlib6.png", "docs/img/square.png", "docs/img/rgb.png",
                                   "docs/img/srgb.png"])

    @unittest.skipUnless(HAVE_GIT, "needs git")
    def test_gitignore_covers_saves_work_objects_and_logs(self):
        self.assertEqual(git(self.root, "init", "-q").returncode, 0)
        shutil.copyfile(REPO / ".gitignore", self.root / ".gitignore")
        ignored = ["tests/fixtures/save_0/0/game_data.sav", "mysaves/save_0/option.sav", "a/b/trackblock00.sav",
                   "rb/work/main/main_000.c", "rb/cache/main/x/main_000.o", "x/main_000.obj", "logs/boot1.err.log"]
        kept = ["native/examples/hello/hello.c", "docs/saves.md", "tools/work.py", "tests/fixtures/save.txt"]
        for rel in ignored + kept:
            self.put(rel, b"x")
        for rel in ignored:
            self.assertEqual(git(self.root, "check-ignore", "-q", rel).returncode, 0, rel)
        for rel in kept:
            self.assertEqual(git(self.root, "check-ignore", "-q", rel).returncode, 1, rel)

    # --- clean-5: more key formats ---

    def test_more_key_formats_blocked(self):
        hex32 = b"cd" * 16
        lines = {
            "title.txt": b"0100" + b"0" * 28 + b" = " + hex32 + b"\n",  # title.keys: rights id = title key
            "title_crlf.txt": b"0100" + b"0" * 27 + b"1=" + hex32 + b"\r\n",
            "kek.txt": b"master_kek_00 = " + hex32 + b"\n",
            "mariko.txt": b"mariko_kek = " + hex32 + b"\n",
            "tsec.txt": b"tsec_root_key_02 = " + hex32 + b"\n",
            "eticket.txt": b"eticket_rsa_kek = " + hex32 + b"\n",
            "upper.txt": b"MASTER_KEY_0A = " + hex32.upper() + b"\n",
            "cemu.txt": hex32 + b" # made-up game\n",
        }
        for name, data in lines.items():
            self.put("notes/" + name, b"made-up notes\n" + data)
        self.put("ok/code.py", b'cache_key = "' + hex32 + b'"\n')
        self.put("ok/split.txt", b"header" + b"_key =\n" + hex32 + b"\n")
        self.put("ok/prose.txt", b"compare " + hex32 + b" = " + hex32 + b" here\n")
        self.put("ok/sha.txt", b"commit " + b"ab" * 20 + b"\n" + hex32 + b"\n")
        self.put("ok/ticket.tik", b"\0" * 16)
        found = self.assertBlocked([f"notes/{n}" for n in lines] + ["ok/ticket.tik"],
                                   passes=["ok/code.py", "ok/split.txt", "ok/prose.txt", "ok/sha.txt"])
        for n in lines:
            self.assertIn("console keys", found[f"notes/{n}"])


class RepoAllowListTests(unittest.TestCase):
    def test_cleanallow_names_only_existing_made_up_files(self):
        """A stale entry would let a future file at that path skip every check."""
        entries = cc.read_allow(REPO)
        self.assertTrue(entries)
        for rel in entries:
            self.assertTrue((REPO / rel).is_file(), f".cleanallow lists {rel}, which doesn't exist")


if __name__ == "__main__":
    unittest.main()
