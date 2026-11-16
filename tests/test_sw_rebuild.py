"""Tests for tools/sw_rebuild.py. Uses only made-up files in temp folders and fake builder scripts, never game
files, and never starts Savage Wind, swgen or zig."""
import contextlib
import errno
import hashlib
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import sw_rebuild as sw  # noqa: E402

WIN = sys.platform == "win32"
NOT_RUNNING = lambda: False  # noqa: E731
BID = bytes.fromhex("cd57b23fa4bbad65") + bytes(range(24))  # made-up id with a 1.9.0-style prefix


def fake_nso(build_id: bytes = BID, text=0x3000, rodata=0x1800, data=0x800, bss=0x400, body=b"") -> bytes:
    """A made-up NSO header (only the fields the layout reads) plus some body bytes."""
    h = bytearray(0x100)
    h[0:4] = b"NSO0"
    struct.pack_into("<III", h, 0x10, 0x100, 0, text)
    struct.pack_into("<III", h, 0x20, 0x100, text, rodata)
    struct.pack_into("<III", h, 0x30, 0x100, text + rodata, data)
    struct.pack_into("<I", h, 0x3C, bss)
    h[0x40:0x60] = build_id
    return bytes(h) + (body or b"made-up module body")


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)


def read(path):
    with open(path, "rb") as f:
        return f.read()


def text_of(path):  # (the fake builders write text files: CRLF on Windows)
    return read(path).decode().replace("\r\n", "\n")


class Commands(unittest.TestCase):
    def setUp(self):
        self.t = sw.Tools(install=r"C:\SW Folder", gen=[r"C:\SW Folder\swgen\swgen.exe"],
                          cc=[r"C:\SW Folder\zig\zig.exe"], zig=True, inc=r"C:\SW Folder\gamelib-src",
                          entry=r"C:\SW Folder\gamelib-src\gamelib_entry.c",
                          hle=r"C:\SW Folder\gamelib-src\hle_symbols.txt", implib=r"C:\SW Folder\savagewind.dll.a",
                          zig_rt=r"C:\SW Folder\zig\rt", exe=r"C:\SW Folder\savagewind.exe")

    def test_quote_arg(self):
        self.assertEqual(sw.quote_arg("plain"), "plain")
        self.assertEqual(sw.quote_arg(""), '""')
        self.assertEqual(sw.quote_arg("a b"), '"a b"')
        self.assertEqual(sw.quote_arg('-DGAMELIB_ID="abc"'), '"-DGAMELIB_ID=\\"abc\\""')
        self.assertEqual(sw.quote_arg("C:\\dir with space\\"), '"C:\\dir with space\\\\"')
        self.assertEqual(sw.quote_arg("C:\\nospace\\"), "C:\\nospace\\")
        self.assertEqual(sw.quote_arg('a\\"b'), '"a\\\\\\"b"')

    @unittest.skipUnless(WIN, "CommandLineToArgvW is Windows only")
    def test_quote_round_trip(self):
        import ctypes
        from ctypes import wintypes
        f = ctypes.windll.shell32.CommandLineToArgvW
        f.restype, f.argtypes = ctypes.POINTER(wintypes.LPWSTR), [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        args = ["plain", "", "a b", '-DGAMELIB_ID="767a"', "C:\\dir with space\\", "tab\there", 'q"uote\\',
                "back\\\\slash", "@C:\\Savage Wind\\zig\\rt\\link.rsp", "\\\\server\\share\\"]
        n = ctypes.c_int()
        p = f("prog " + sw.command_line(args), ctypes.byref(n))
        try:
            self.assertEqual([p[i] for i in range(1, n.value)], args)
        finally:
            free(ctypes.cast(p, ctypes.c_void_p))

    def test_compile_flags_zig(self):
        flags = sw.compile_flags(sw.RELEASE_CFLAGS["normal"], sw.RELEASE_ZIGFLAGS, True)
        self.assertIn("-mcpu=x86_64_v3", flags)
        self.assertNotIn("-march=x86-64-v3", flags)
        zf = sw.RELEASE_ZIGFLAGS.split()
        self.assertEqual(flags[-len(zf):], zf)  # zig's own flags last: its -O1 wins
        self.assertEqual(flags[0], "-O1")
        compat = sw.compile_flags(sw.RELEASE_CFLAGS["compat"], sw.RELEASE_ZIGFLAGS, True)
        self.assertIn("-mcpu=x86_64_v2", compat)
        plain = sw.compile_flags(sw.RELEASE_CFLAGS["normal"], sw.RELEASE_ZIGFLAGS, False)
        self.assertEqual(plain, sw.RELEASE_CFLAGS["normal"].split())

    def test_compile_argv(self):
        flags = sw.compile_flags(sw.RELEASE_CFLAGS["normal"], sw.RELEASE_ZIGFLAGS, True)
        argv = sw.compile_argv(self.t, flags, "767a39f088e81e1a", r"W\main\main_000.c", r"W\main_000.o")
        self.assertEqual(argv[:2], [r"C:\SW Folder\zig\zig.exe", "cc"])
        self.assertEqual(argv[2:2 + len(flags)], flags)
        self.assertEqual(argv[2 + len(flags):], [r"-IC:\SW Folder\gamelib-src", '-DGAMELIB_ID="767a39f088e81e1a"',
                                                 "-c", r"W\main\main_000.c", "-o", r"W\main_000.o"])

    def test_generator_argv(self):
        self.assertEqual(sw.gen_layout_argv(self.t, r"E\exefs", r"W"),
                         [r"C:\SW Folder\swgen\swgen.exe", "layout", r"E\exefs", "W"])
        main = sw.gen_module_argv(self.t, "main", r"E\exefs", r"W\layout.txt", r"W\main")
        self.assertEqual(main[1:], ["main", os.path.join(r"E\exefs", "main"), r"W\layout.txt", r"W\main"])
        sdk = sw.gen_module_argv(self.t, "sdk", r"E\exefs", r"W\layout.txt", r"W\sdk")
        self.assertEqual(sdk[-2:], ["--hle", r"C:\SW Folder\gamelib-src\hle_symbols.txt"])
        self.assertTrue(self.t.gen_no_cfg)
        self.assertFalse(sw.Tools(**{**self.t.__dict__, "gen": [sys.executable, "x.py"]}).gen_no_cfg)

    def test_link_argv(self):
        argv = sw.link_argv(self.t, r"W\swgame.dll.tmp", r"W\link.rsp")
        self.assertEqual(argv, [r"C:\SW Folder\zig\zig.exe", "lld-link",
                                "@" + os.path.join(r"C:\SW Folder\zig\rt", "link.rsp"),
                                r"-LIBPATH:C:\SW Folder\zig\rt", r"-OUT:W\swgame.dll.tmp", r"@W\link.rsp",
                                r"C:\SW Folder\savagewind.dll.a"])
        no_rt = sw.Tools(**{**self.t.__dict__, "zig_rt": None})
        self.assertEqual(sw.link_argv(no_rt, "o.tmp", "l.rsp")[1:],
                         ["cc", "-shared", "-o", "o.tmp", "@l.rsp", r"C:\SW Folder\savagewind.dll.a"])

    def test_objs_rsp(self):
        self.assertEqual(sw.objs_rsp_text([r"C:\a b\x.o", "y.o"]), '"C:/a b/x.o"\n"y.o"\n')

    def test_progress_and_tail(self):
        log = "main: 10 boundaries\nmain: progress 10/100 files 1\nmain: progress 55/100 files 4\nmain: done\n"
        self.assertEqual(sw.parse_gen_progress(log), (55, 100, 4))
        self.assertIsNone(sw.parse_gen_progress("nothing yet"))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "g.log")
            write(p, log + "Traceback\r\nboom\r\n")
            self.assertEqual(sw.log_tail(p), ["main: 10 boundaries", "main: done", "Traceback", "boom"])

    def test_exe_flags(self):
        n, c, z = sw.RELEASE_CFLAGS["normal"], sw.RELEASE_CFLAGS["compat"], sw.RELEASE_ZIGFLAGS
        blob = (b"\x00junk\x00" + c.encode() + b"\x00\x00" + n.encode() + b"\x00padding\x00" + z.encode() +
                b"\x00-O2 -fsomething\x00SAVAGEWIND_COMPAT_CPU\x00")
        self.assertEqual(sw.exe_flags(blob), {"normal": n, "compat": c, "zig": z})
        other = n.replace("-O1", "-O2")
        self.assertIsNone(sw.exe_flags(blob + other.encode() + b"\x00")["normal"])  # two different: don't guess
        self.assertEqual(sw.exe_flags(b"nothing"), {"normal": None, "compat": None, "zig": None})

    def test_pick_target(self):
        self.assertEqual(sw.pick_target("767a39f088e81e1a"), "normal")
        self.assertEqual(sw.pick_target("b4d249669cc63ebf"), "compat")
        self.assertEqual(sw.pick_target("0000000000000000", cpu_normal=lambda: False), "compat")
        self.assertEqual(sw.pick_target("0000000000000000", cpu_normal=lambda: True), "normal")
        self.assertEqual(sw.pick_target("767a39f088e81e1a", "compat"), "compat")

    def test_predict_layout(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, "main"), fake_nso(text=0x3000, rodata=0x1800, data=0x800, bss=0x400))
            write(os.path.join(d, "subsdk0"), fake_nso(bytes(32), text=0x1000, rodata=0x10, data=0x10, bss=0))
            write(os.path.join(d, "subsdk9"), fake_nso(bytes(32)))  # a mod's subsdk: left out for 1.9.0
            write(os.path.join(d, "sdk"), fake_nso(bytes(32), text=0x2000, rodata=0x100, data=0x100, bss=0x1001))
            lay = sw.predict_layout(d)
        self.assertEqual([m[0] for m in lay], ["main", "subsdk0", "sdk"])
        self.assertEqual(lay[0][1:3], (0x7000000000, 0x6000))  # data ends at 0x5000, + bss 0x400 -> page 0x6000
        self.assertEqual(lay[1][1], 0x7000006000)
        self.assertEqual(lay[1][2], 0x2000)  # 0x1000+0x10+0x10 -> 0x1020 -> 0x2000
        self.assertEqual(lay[2][1], lay[1][1] + 0x2000)
        self.assertEqual(lay[0][3], BID.hex())

    def test_image_size(self):
        h = {"text": (0, 0x3000), "rodata": (0x3000, 0x1800), "data": (0x4800, 0x800), "bss": 0x400}
        self.assertEqual(sw.image_size(h), 0x6000)

    def test_read_layout(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "layout.txt")
            write(p, "# module layout\nmain 0x7000000000 0x5000 aa\nsdk 0x7000005000 0x2000 bb\n")
            self.assertEqual(sw.read_layout(p), [("main", 0x7000000000, 0x5000, "aa"),
                                                 ("sdk", 0x7000005000, 0x2000, "bb")])


class CacheKeys(unittest.TestCase):
    BASE = dict(name="sdk", base=0x7004000000, size=0x600000, build_id="ab" * 32, nso_sha256="11" * 32,
                gamelib_id="767a39f088e81e1a", flags=["-O1", "-mcpu=x86_64_v3"])
    IDS = dict(gen_id="g", cc_id="c", headers_id="h", hle_sha256="x")

    def key(self, **change):
        args = {**self.BASE, **self.IDS, **change}
        return sw.module_cache_key(args.pop("name"), args.pop("base"), args.pop("size"), args.pop("build_id"),
                                   args.pop("nso_sha256"), args.pop("gamelib_id"), args.pop("flags"), **args)

    def test_stable(self):
        self.assertEqual(self.key(), self.key())
        self.assertEqual(len(self.key()), 64)

    def test_every_input_counts(self):
        k = self.key()
        for change in (dict(nso_sha256="22" * 32), dict(build_id="cd" * 32), dict(gamelib_id="b4d249669cc63ebf"),
                       dict(flags=["-O1", "-mcpu=x86_64_v2"]), dict(base=0x7005000000), dict(size=0x601000),
                       dict(name="subsdk0"), dict(gen_id="g2"), dict(cc_id="c2"), dict(headers_id="h2"),
                       dict(hle_sha256="y")):
            self.assertNotEqual(self.key(**change), k, change)

    def test_hle_only_for_other_modules(self):
        self.assertEqual(self.key(name="main"), self.key(name="main", hle_sha256="other"))

    def test_hooks(self):
        plain = self.key(name="main")
        self.assertEqual(self.key(name="main", hooks=None), plain)  # (no --hooks: the key of before)
        self.assertEqual(self.key(name="main", hooks=[]), plain)
        a = self.key(name="main", hooks=[0x7000cc7410, 0x7000001000])
        self.assertNotEqual(a, plain)
        self.assertEqual(self.key(name="main", hooks=[0x7000001000, 0x7000cc7410, 0x7000001000]), a)
        self.assertNotEqual(self.key(name="main", hooks=[0x7000001000]), a)

    def test_main_change_keeps_sdk_key(self):
        sdk_before = self.key()
        main_a, main_b = self.key(name="main", nso_sha256="aa" * 32), self.key(name="main", nso_sha256="bb" * 32)
        self.assertNotEqual(main_a, main_b)
        self.assertEqual(self.key(), sdk_before)

    def test_object_cache(self):
        with tempfile.TemporaryDirectory() as d:
            c = sw.ObjectCache(os.path.join(d, "cache"), keep=2)
            keys = []
            for i in range(3):
                key = hashlib.sha256(bytes([i])).hexdigest()
                st = c.staging_dir("sdk", key)
                objs = [os.path.join(st, "sdk_000.o"), os.path.join(st, "sdk_table.o")]
                for o in objs:
                    write(o, f"obj {i} {o}")
                final = c.commit("sdk", key, st, objs, {"note": i})
                self.assertFalse(os.path.exists(st))
                self.assertEqual([os.path.basename(p) for p in final], ["sdk_000.o", "sdk_table.o"])
                os.utime(os.path.join(c.entry_dir("sdk", key), "manifest.json"), (1000 + i, 1000 + i))
                keys.append(key)
            self.assertEqual(c.lookup("sdk", keys[2]), [os.path.join(c.entry_dir("sdk", keys[2]), n)
                                                        for n in ("sdk_000.o", "sdk_table.o")])
            self.assertIsNone(c.lookup("sdk", "f" * 64))
            removed = c.prune("sdk", {keys[2][:32]})
            self.assertEqual(removed, [keys[0][:32]])  # oldest goes, two kept
            self.assertIsNone(c.lookup("sdk", keys[0]))
            self.assertIsNotNone(c.lookup("sdk", keys[1]))
            write(os.path.join(c.entry_dir("sdk", keys[1]), "sdk_000.o"), "damaged")  # size changed
            self.assertIsNone(c.lookup("sdk", keys[1]))

    def test_prune_leaves_folders_that_are_not_entries(self):
        with tempfile.TemporaryDirectory() as d:
            c = sw.ObjectCache(os.path.join(d, "cache"), keep=1)
            foreign = os.path.join(c.root, "sdk", "photos", "manifest.json")
            write(foreign, '{"key": "something else"}')
            broken = os.path.join(c.root, "sdk", "b" * 32, "manifest.json")
            write(broken, "not json")
            os.utime(foreign, (1, 1))
            os.utime(broken, (1, 1))
            keys = []
            for i in range(2):
                key = hashlib.sha256(bytes([i])).hexdigest()
                st = c.staging_dir("sdk", key)
                write(os.path.join(st, "sdk_000.o"), f"obj {i}")
                c.commit("sdk", key, st, [os.path.join(st, "sdk_000.o")], {})
                os.utime(os.path.join(c.entry_dir("sdk", key), "manifest.json"), (1000 + i, 1000 + i))
                keys.append(key)
            self.assertEqual(c.prune("sdk", {keys[1][:32]}), [keys[0][:32]])
            self.assertTrue(os.path.isfile(foreign))
            self.assertTrue(os.path.isfile(broken))

    def test_commit_when_folder_in_use(self):
        with tempfile.TemporaryDirectory() as d:
            c = sw.ObjectCache(os.path.join(d, "cache"))
            st = c.staging_dir("main", "a" * 64)
            obj = os.path.join(st, "main_000.o")
            write(obj, "obj")

            def busy(a, b):
                raise PermissionError(13, "in use")
            with mock.patch.object(sw.os, "replace", busy), mock.patch.object(sw.time, "sleep"):
                self.assertIsNone(c.commit("main", "a" * 64, st, [obj], {}, tries=3))
            self.assertTrue(os.path.isfile(obj))  # still there to link from
            self.assertIsNone(c.lookup("main", "a" * 64))


class FakeInstall:
    """A made-up Savage Wind folder whose extracted\\exefs\\main is a hard link to a made-up 'dump'."""

    def __init__(self, root: str, link: bool = True):
        self.root = root
        self.install = os.path.join(root, "Savage Wind")
        self.dump_main = os.path.join(root, "dump", "exefs", "main")
        self.P = sw.paths_for(self.install)
        self.vanilla = fake_nso(body=b"vanilla code bytes " * 50)
        write(self.dump_main, self.vanilla)
        os.makedirs(self.P["exefs"], exist_ok=True)
        if link:
            os.link(self.dump_main, self.P["main"])
        else:
            write(self.P["main"], self.vanilla)
        write(self.P["lib"], b"MZ vanilla library")
        write(self.P["lib_id"], b"767a39f088e81e1a\n")

    def patched(self, tag=b"patched"):
        p = os.path.join(self.root, f"patched-{tag.decode()}", "main")
        write(p, fake_nso(body=tag + b" code bytes " * 50))
        return p


class Swaps(unittest.TestCase):
    def test_hard_link_safe_replace(self):
        with tempfile.TemporaryDirectory() as d:
            orig, link = os.path.join(d, "orig"), os.path.join(d, "link")
            write(orig, b"original bytes")
            os.link(orig, link)
            self.assertEqual(os.stat(orig).st_nlink, 2)
            sw.write_atomic(link, b"new bytes")
            self.assertEqual(read(orig), b"original bytes")
            self.assertEqual(read(link), b"new bytes")
            self.assertEqual(os.stat(orig).st_nlink, 1)
            self.assertEqual(sorted(os.listdir(d)), ["link", "orig"])  # no temp files left

    def test_install_patched_main_keeps_dump(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d)
            patched = fi.patched()
            msgs = sw.install_patched_main(patched, fi.install, running=NOT_RUNNING)
            self.assertIn("installed", msgs[0])
            self.assertEqual(read(fi.dump_main), fi.vanilla)  # the user's dump is untouched
            self.assertEqual(os.stat(fi.dump_main).st_nlink, 1)
            self.assertEqual(read(fi.P["main"]), read(patched))
            self.assertEqual(read(fi.P["main_backup"]), fi.vanilla)
            state = sw.load_state(fi.install)
            self.assertEqual(state["main_vanilla_sha256"], hashlib.sha256(fi.vanilla).hexdigest())
            self.assertEqual(state["main_installed_sha256"], hashlib.sha256(read(patched)).hexdigest())
            # the same again: nothing to do; another patch: the backup stays vanilla
            self.assertIn("already installed", sw.install_patched_main(patched, fi.install, running=NOT_RUNNING)[0])
            sw.install_patched_main(fi.patched(b"other"), fi.install, running=NOT_RUNNING)
            self.assertEqual(read(fi.P["main_backup"]), fi.vanilla)
            self.assertEqual(read(fi.dump_main), fi.vanilla)

    def test_restore_main(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d)
            sw.install_patched_main(fi.patched(), fi.install, running=NOT_RUNNING)
            msgs = sw.restore(fi.install, "main", running=NOT_RUNNING)
            self.assertEqual(msgs, ["main: vanilla restored"])
            self.assertEqual(read(fi.P["main"]), fi.vanilla)
            self.assertEqual(read(fi.dump_main), fi.vanilla)
            self.assertNotIn("main_installed_sha256", sw.load_state(fi.install))
            self.assertEqual(sw.restore(fi.install, "main", running=NOT_RUNNING), ["main: already vanilla"])

    def test_refusals(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d)
            with self.assertRaisesRegex(sw.RebuildError, "running"):
                sw.install_patched_main(fi.patched(), fi.install, running=lambda: True)
            with self.assertRaisesRegex(sw.RebuildError, "running"):
                sw.restore(fi.install, running=lambda: True)
            other = os.path.join(d, "other", "main")
            write(other, fake_nso(bytes(32), body=b"x"))
            with self.assertRaisesRegex(sw.RebuildError, "build id"):
                sw.install_patched_main(other, fi.install, running=NOT_RUNNING)
            with self.assertRaisesRegex(sw.RebuildError, "itself"):
                sw.install_patched_main(fi.P["main"], fi.install, running=NOT_RUNNING)
            with self.assertRaisesRegex(sw.RebuildError, "itself"):
                sw.install_patched_main(fi.dump_main, fi.install, running=NOT_RUNNING)  # (same file, hard link)
            notnso = os.path.join(d, "notnso")
            write(notnso, b"hello" * 100)
            with self.assertRaisesRegex(sw.RebuildError, "not an NSO"):
                sw.install_patched_main(notnso, fi.install, running=NOT_RUNNING)
            # a patched main is installed and its vanilla backup was lost: refuse to back up the patched one
            sw.install_patched_main(fi.patched(), fi.install, running=NOT_RUNNING)
            os.remove(fi.P["main_backup"])
            with self.assertRaisesRegex(sw.RebuildError, "backup"):
                sw.install_patched_main(fi.patched(b"again"), fi.install, running=NOT_RUNNING)
            self.assertEqual(read(fi.dump_main), fi.vanilla)

    def test_library_backup_install_restore(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d, link=False)
            built = os.path.join(d, "out", "swgame.dll.tmp")
            write(built, b"MZ rebuilt library 1")
            msgs = sw.install_library(built, fi.install, "767a39f088e81e1a", running=NOT_RUNNING)
            self.assertIn("vanilla copy saved", msgs[0])
            self.assertFalse(os.path.exists(built))
            self.assertEqual(read(fi.P["lib"]), b"MZ rebuilt library 1")
            self.assertEqual(read(fi.P["lib_id"]), b"767a39f088e81e1a\n")
            self.assertEqual(read(fi.P["lib_vanilla"]), b"MZ vanilla library")
            self.assertEqual(read(fi.P["lib_vanilla_id"]), b"767a39f088e81e1a\n")
            write(built, b"MZ rebuilt library 2")  # a second install keeps the first (vanilla) backup
            self.assertEqual(sw.install_library(built, fi.install, "767a39f088e81e1a", running=NOT_RUNNING), [])
            self.assertEqual(read(fi.P["lib_vanilla"]), b"MZ vanilla library")
            msgs = sw.restore(fi.install, "library", running=NOT_RUNNING)
            self.assertEqual(msgs, ["library: vanilla restored"])
            self.assertEqual(read(fi.P["lib"]), b"MZ vanilla library")
            self.assertFalse(os.path.exists(fi.P["lib_vanilla"]))
            self.assertEqual(sw.restore(fi.install, "library", running=NOT_RUNNING), ["library: no backup, left as it is"])

    def test_library_backup_refuses_rebuilt_without_backup(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d, link=False)
            built = os.path.join(d, "b.tmp")
            write(built, b"MZ rebuilt")
            sw.install_library(built, fi.install, "767a39f088e81e1a", running=NOT_RUNNING)
            os.remove(fi.P["lib_vanilla"])
            with self.assertRaisesRegex(sw.RebuildError, "vanilla backup is missing"):
                sw.backup_vanilla_library(fi.install)

    def test_install_hint_names_the_install(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d)
            hint = sw.install_patched_main(fi.patched(), fi.install, running=NOT_RUNNING)[-1]
            self.assertIn(f'build "{fi.install}" --install', hint)

    def test_stale_library_backup_after_new_setup(self):
        """The reviewer's case: a backup from an old setup must not outlive a new setup (other id)."""
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d, link=False)
            write(fi.P["lib"], b"MZ vanilla OLD")
            write(fi.P["lib_id"], b"1111111111111111\n")
            b1 = os.path.join(d, "b1")
            write(b1, b"MZ built 1")
            sw.install_library(b1, fi.install, "1111111111111111", running=NOT_RUNNING)
            # Savage Wind updated and set up again: new vanilla library, new id; the .vanilla files stay
            write(fi.P["lib"], b"MZ vanilla NEW")
            write(fi.P["lib_id"], b"2222222222222222\n")
            self.assertEqual(sw.library_backup_problem(fi.install), "for another Savage Wind version")
            with mock.patch.object(sw, "game_running", NOT_RUNNING), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(sw.main(["status", fi.install]), 0)
            self.assertIn("library: yes, but for another Savage Wind version", out.getvalue())
            msg = sw.backup_vanilla_library(fi.install)
            self.assertIn("saved the current library instead", msg)
            self.assertEqual(read(fi.P["lib_vanilla"]), b"MZ vanilla NEW")
            self.assertEqual(read(fi.P["lib_vanilla_id"]), b"2222222222222222\n")
            b2 = os.path.join(d, "b2")
            write(b2, b"MZ built 2")
            sw.install_library(b2, fi.install, "2222222222222222", running=NOT_RUNNING)
            self.assertEqual(sw.restore(fi.install, "library", running=NOT_RUNNING), ["library: vanilla restored"])
            self.assertEqual(read(fi.P["lib"]), b"MZ vanilla NEW")
            self.assertEqual(read(fi.P["lib_id"]), b"2222222222222222\n")

    def test_stale_library_backup_not_restored(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d, link=False)
            write(fi.P["lib_vanilla"], b"MZ vanilla OLD")
            write(fi.P["lib_vanilla_id"], b"1111111111111111\n")
            write(fi.P["lib_id"], b"2222222222222222\n")  # (the setup's library, not one this tool installed)
            msgs = sw.restore(fi.install, "library", running=NOT_RUNNING)
            self.assertEqual(len(msgs), 1)
            self.assertIn("another Savage Wind version, left as it is", msgs[0])
            self.assertEqual(read(fi.P["lib"]), b"MZ vanilla library")
            self.assertEqual(read(fi.P["lib_id"]), b"2222222222222222\n")
            self.assertTrue(os.path.isfile(fi.P["lib_vanilla"]))

    def test_library_backup_checked_against_the_exe(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d, link=False)
            built = os.path.join(d, "b")
            write(built, b"MZ rebuilt")
            sw.install_library(built, fi.install, "767a39f088e81e1a", running=NOT_RUNNING)
            exe = os.path.join(fi.install, "savagewind.exe")
            write(exe, b"MZ exe\x00767a39f088e81e1a\x00b4d249669cc63ebf\x00")
            self.assertIsNone(sw.library_backup_problem(fi.install))
            write(exe, b"MZ newer exe\x00aaaaaaaaaaaaaaaa\x00bbbbbbbbbbbbbbbb\x00")
            self.assertEqual(sw.library_backup_problem(fi.install), "for another Savage Wind version")
            # the rebuilt library is installed and the backup is stale: nothing vanilla is left to back up
            with self.assertRaisesRegex(sw.RebuildError, "set up the game files"):
                sw.backup_vanilla_library(fi.install)
            self.assertIn("left as it is", sw.restore(fi.install, "library", running=NOT_RUNNING)[0])
            self.assertEqual(read(fi.P["lib"]), b"MZ rebuilt")

    def test_library_backup_for_another_game_version(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d, link=False)
            built = os.path.join(d, "b")
            write(built, b"MZ rebuilt")
            sw.install_library(built, fi.install, "767a39f088e81e1a", running=NOT_RUNNING)
            write(fi.P["main"], fake_nso(bytes(32), body=b"another game version"))
            self.assertEqual(sw.library_backup_problem(fi.install), "for another game version")
            self.assertIn("left as it is", sw.restore(fi.install, "library", running=NOT_RUNNING)[0])

    def test_stale_library_backup_kept_when_main_is_patched(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d)
            write(fi.P["lib_vanilla"], b"MZ vanilla OLD")
            write(fi.P["lib_vanilla_id"], b"1111111111111111\n")
            sw.install_patched_main(fi.patched(), fi.install, running=NOT_RUNNING)
            # a new setup built extracted\swgame.dll (id 767a...) from the patched main: not a vanilla library
            with self.assertRaisesRegex(sw.RebuildError, "restore --only main"):
                sw.backup_vanilla_library(fi.install)
            self.assertEqual(read(fi.P["lib_vanilla"]), b"MZ vanilla OLD")

    def test_same_id_new_setup_keeps_backup(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d, link=False)
            built = os.path.join(d, "b")
            write(built, b"MZ rebuilt")
            sw.install_library(built, fi.install, "767a39f088e81e1a", running=NOT_RUNNING)
            write(fi.P["lib"], b"MZ set up again by Savage Wind (maybe from a patched main)")
            self.assertIsNone(sw.backup_vanilla_library(fi.install))
            self.assertEqual(read(fi.P["lib_vanilla"]), b"MZ vanilla library")

    def test_stale_main_backup(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d)
            sw.install_patched_main(fi.patched(), fi.install, running=NOT_RUNNING)
            sw.restore(fi.install, "main", running=NOT_RUNNING)
            # the game files are set up again from another game version
            newer = fake_nso(bytes(range(32)), body=b"another version")
            os.remove(fi.P["main"])
            write(fi.P["main"], newer)
            msgs = sw.restore(fi.install, "main", running=NOT_RUNNING)
            self.assertEqual(msgs, ["main: the backup is for another game version, left as it is"])
            self.assertEqual(read(fi.P["main"]), newer)
            patched = os.path.join(d, "p2", "main")
            write(patched, fake_nso(bytes(range(32)), body=b"another version, patched"))
            msgs = sw.install_patched_main(patched, fi.install, running=NOT_RUNNING)
            self.assertIn("saved the current main instead", msgs[1])
            self.assertEqual(read(fi.P["main_backup"]), newer)
            self.assertEqual(sw.restore(fi.install, "main", running=NOT_RUNNING), ["main: vanilla restored"])
            self.assertEqual(read(fi.P["main"]), newer)
            self.assertEqual(read(fi.dump_main), fi.vanilla)

    def test_restore_after_game_files_removed(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d)
            sw.install_patched_main(fi.patched(), fi.install, running=NOT_RUNNING)
            built = os.path.join(d, "b")
            write(built, b"MZ rebuilt")
            sw.install_library(built, fi.install, "767a39f088e81e1a", running=NOT_RUNNING)
            # Savage Wind's "Remove game files": exefs, swgame.dll and swgame.dll.id go; .vanilla files stay
            shutil.rmtree(fi.P["exefs"])
            os.remove(fi.P["lib"])
            os.remove(fi.P["lib_id"])
            msgs = sw.restore(fi.install, running=NOT_RUNNING)
            self.assertEqual(msgs, ["main: the game files aren't set up, left as it is",
                                    "library: the game files aren't set up, left as it is"])
            self.assertFalse(os.path.exists(fi.P["exefs"]))
            self.assertFalse(os.path.exists(fi.P["lib"]))

    def test_restore_everything(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d)
            sw.install_patched_main(fi.patched(), fi.install, running=NOT_RUNNING)
            built = os.path.join(d, "b")
            write(built, b"MZ rebuilt")
            sw.install_library(built, fi.install, "767a39f088e81e1a", running=NOT_RUNNING)
            with mock.patch.object(sw, "game_running", NOT_RUNNING), \
                    contextlib.redirect_stdout(io.StringIO()) as out:  # (a real game may be running on this PC)
                self.assertEqual(sw.main(["restore", fi.install]), 0)
            self.assertEqual(out.getvalue().splitlines(), ["main: vanilla restored", "library: vanilla restored"])
            self.assertEqual(read(fi.P["main"]), fi.vanilla)
            self.assertEqual(read(fi.P["lib"]), b"MZ vanilla library")
            self.assertEqual(sw.load_state(fi.install).get("library_installed_sha256"), None)
            self.assertEqual(sw.load_state(fi.install).get("main_installed_sha256"), None)

    def test_move_into_place_across_volumes(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "a"), os.path.join(d, "b")
            write(src, b"new")
            write(dst, b"old")
            real = os.replace
            calls = []

            def fake_replace(a, b):
                calls.append((a, b))
                if len(calls) == 1:
                    raise OSError(errno.EXDEV, "cross-device")
                return real(a, b)
            with mock.patch.object(sw.os, "replace", fake_replace):
                sw.move_into_place(src, dst)
            self.assertEqual(read(dst), b"new")
            self.assertFalse(os.path.exists(src))
            self.assertEqual(sorted(os.listdir(d)), ["b"])


class Spawn(unittest.TestCase):
    def test_proc_logs_and_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "child.log")
            args = ["a b", '-DGAMELIB_ID="767a"', "C:\\x y\\", ""]
            code = "import json,os,sys; print(json.dumps([sys.argv[1:], os.environ.get('SW_TEST')])); sys.exit(3)"
            p = sw.Proc([sys.executable, "-c", code] + args, log, env={**os.environ, "SW_TEST": "ok"}, no_cfg=True)
            self.assertEqual(p.wait(), 3)
            got = json.loads(read(log).decode().strip().splitlines()[-1])
            self.assertEqual(got, [args, "ok"])
            if WIN:
                self.assertTrue(p.cfg_off)  # started with the CFG-off mitigation policy, like swgen.exe

    @unittest.skipUnless(WIN, "the environment block is built for CreateProcessW")
    def test_env_block_counts_utf16_units(self):
        import ctypes
        smile = "\U0001F600" * 3  # (outside the BMP: two UTF-16 units each)
        b = sw._env_block({"A": "x", "ZZZ": smile, "ZZZB": "last"})
        self.assertEqual(ctypes.wstring_at(b, ctypes.sizeof(b) // 2), f"A=x\0ZZZ={smile}\0ZZZB=last\0\0\0")

    @unittest.skipUnless(WIN, "the environment block is built for CreateProcessW")
    def test_child_gets_whole_environment(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "env.log")
            # (a small environment, so the variable checked is the last one in the block: it would be cut short)
            env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"), "SW_SMILE": "\U0001F600" * 3,
                   "ZZZZ_SW_LAST": "last value"}
            code = "import json,os; print(json.dumps([os.environ.get('SW_SMILE'), os.environ.get('ZZZZ_SW_LAST')]))"
            p = sw.Proc([sys.executable, "-c", code], log, env=env)
            self.assertEqual(p.wait(), 0)
            self.assertEqual(json.loads(read(log).decode().strip().splitlines()[-1]), [env["SW_SMILE"], "last value"])

    def test_kill(self):
        with tempfile.TemporaryDirectory() as d:
            p = sw.Proc([sys.executable, "-c", "import time; time.sleep(30)"], os.path.join(d, "k.log"))
            self.assertIsNone(p.poll())
            p.kill()
            self.assertIsNotNone(p.poll())


FAKE_SWGEN = textwrap.dedent(r'''
    import os, struct, sys
    log = os.environ.get("FAKE_GEN_LOG")
    if log:
        # The modules' generators run at once: on Windows two appends at the same moment can lose a line (it did
        # on the GitHub runner), so each takes a lock file first.
        import time
        fd = None
        for _ in range(1000):
            try:
                fd = os.open(log + ".lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except OSError:
                time.sleep(0.01)
        try:
            with open(log, "a") as f:
                f.write(sys.argv[1] + " " + " ".join(a for a in sys.argv[2:] if a == "--hle") + "\n")
        finally:
            if fd is not None:
                os.close(fd)
                os.remove(log + ".lock")
    if sys.argv[1] == "layout":
        exefs, out = sys.argv[2], sys.argv[3]
        base, lines = 0x7000000000, ["# fake layout\n"]
        for m in ["main"] + ["subsdk%d" % i for i in range(10)] + ["sdk"]:
            p = os.path.join(exefs, m)
            if not os.path.isfile(p):
                continue
            h = open(p, "rb").read(0x100)
            t, r, d = (struct.unpack_from("<III", h, o) for o in (0x10, 0x20, 0x30))
            bss = struct.unpack_from("<I", h, 0x3C)[0]
            size = (max(t[1] + t[2], r[1] + r[2], d[1] + d[2] + bss) + 0xFFF) & ~0xFFF
            lines.append("%s %#x %#x %s\n" % (m, base, size, h[0x40:0x60].hex()))
            base += size
        open(os.path.join(out, "layout.txt"), "w").write("".join(lines))
        open(os.path.join(out, "layout.c"), "w").write("// fake layout.c\n")
        sys.exit(0)
    name, nso, layout, outdir = sys.argv[1:5]
    os.makedirs(outdir, exist_ok=True)
    body = open(nso, "rb").read()
    mode = os.environ.get("FAKE_GEN_FUNCS")  # "1": one made-up function per chunk, as gen.py; "selfjump": each loops
    funcs = mode in ("1", "selfjump") and name == "main"  # back to its own start
    for i in range(3):
        text = "// %s %d %s\n" % (name, i, body[-8:].hex())
        if funcs:
            a = 0x7000000100 + 0x100 * i
            loop = "L_%x:;\n    if (c->x[0] != 0) { goto L_%x; }\n" % (a, a) if mode == "selfjump" else ""
            text += "void f_%x(Cpu *c) {\n%s    return;\n}\n" % (a, loop)
        open(os.path.join(outdir, "%s_%03d.c" % (name, i)), "w").write(text)
        print("%s: progress %d/3 files %d" % (name, i + 1, i + 1), file=sys.stderr, flush=True)
        if os.environ.get("FAKE_GEN_FAIL") == name:  # (one module's generator fails part way)
            print("Traceback: made-up generator failure in " + name, file=sys.stderr)
            sys.exit(3)
    table = "// %s table\n" % name
    if funcs:
        table += "const uint64_t main_fn_addrs[] = {\n%s};\n" % ",\n".join(
            "0x%xull" % (0x7000000100 + 0x100 * i) for i in range(3))
    open(os.path.join(outdir, name + "_table.c"), "w").write(table)
    print("%s: wrote 3 chunks" % name, file=sys.stderr)
''')

FAKE_ZIG = textwrap.dedent(r'''
    import os, sys
    a = sys.argv[1:]
    if a[0] == "cc":
        src, obj = a[a.index("-c") + 1], a[a.index("-o") + 1]
        assert any(x.startswith('-DGAMELIB_ID="') for x in a), a
        assert "-mcpu=x86_64_v3" in a, a
        fail = os.environ.get("FAKE_CC_FAIL")
        if fail and fail in os.path.basename(src):
            print(src + ": error: made-up failure", file=sys.stderr)
            print("1 error generated.", file=sys.stderr)
            sys.exit(1)
        open(obj, "w").write("OBJ[" + open(src).read().strip() + "]\n")
    elif a[0] == "lld-link":
        out = [x[5:] for x in a if x.startswith("-OUT:")][0]
        rsp = [x[1:] for x in a if x.startswith("@")][-1]
        objs = [l.strip().strip('"') for l in open(rsp) if l.strip()]
        with open(out, "w") as f:
            f.write("FAKE DLL\n" + "".join(open(o).read() for o in objs))
    else:
        sys.exit(2)
''')


def make_fake_tools(d: str, install: str) -> sw.Tools:
    """Fake swgen / zig scripts in d\\bin and the install's other build inputs (all made up)."""
    write(os.path.join(d, "bin", "fake_swgen.py"), FAKE_SWGEN)
    write(os.path.join(d, "bin", "fake_zig.py"), FAKE_ZIG)
    inc = os.path.join(install, "gamelib-src")
    for n, text in (("cpu.h", "// cpu\n"), ("gamelib.h", "// gl\n"), ("gamelib_entry.c", "// entry\n"),
                    ("hle_symbols.txt", "nn_fake\n")):
        write(os.path.join(inc, n), text)
    write(os.path.join(install, "zig", "rt", "link.rsp"), "-DLL\n")
    write(os.path.join(install, "savagewind.dll.a"), b"!<arch>\n")
    return sw.Tools(install=install, gen=[sys.executable, os.path.join(d, "bin", "fake_swgen.py")],
                    cc=[sys.executable, os.path.join(d, "bin", "fake_zig.py")], zig=True, inc=inc,
                    entry=os.path.join(inc, "gamelib_entry.c"), hle=os.path.join(inc, "hle_symbols.txt"),
                    implib=os.path.join(install, "savagewind.dll.a"), zig_rt=os.path.join(install, "zig", "rt"),
                    exe=os.path.join(install, "savagewind.exe"))


class Pipeline(unittest.TestCase):
    """The whole build with fake swgen / zig scripts: overlap, link order, the cache, failures, --install."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.d = self.tmp.name
        self.t = make_fake_tools(d, os.path.join(d, "Savage Wind"))
        self.exefs = os.path.join(d, "exefs")
        write(os.path.join(self.exefs, "main"), fake_nso(body=b"main v1."))
        write(os.path.join(self.exefs, "subsdk0"), fake_nso(bytes(32), text=0x1000, body=b"subsdk0."))
        write(os.path.join(self.exefs, "sdk"), fake_nso(bytes(32), text=0x2000, body=b"sdk v1.."))
        self.genlog = os.path.join(d, "gen_calls.log")
        self.env = mock.patch.dict(os.environ, {"FAKE_GEN_LOG": self.genlog})
        self.env.start()
        self.lines = []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def settings(self, **kw):
        base = dict(install=self.t.install, exefs=self.exefs, out=os.path.join(self.d, "out", "swgame.dll"),
                    gamelib_id="767a39f088e81e1a", target="normal",
                    flags=sw.compile_flags(sw.RELEASE_CFLAGS["normal"], sw.RELEASE_ZIGFLAGS, True), jobs=3,
                    work=os.path.join(self.d, "rebuild", "work"), cache=os.path.join(self.d, "rebuild", "cache"))
        return sw.Settings(**{**base, **kw})

    def build(self, s):
        if os.path.exists(self.genlog):
            os.remove(self.genlog)
        return sw.run_build(s, self.t, say=self.lines.append, running=NOT_RUNNING, poll_s=0.01)

    def gen_calls(self):
        """The generator runs: layout first, then the modules (they run at once: sorted, as in LOAD_ORDER)."""
        with open(self.genlog) as f:
            calls = [ln.strip() for ln in f]
        order = {n: i for i, n in enumerate(sw.LOAD_ORDER)}
        return calls[:1] + sorted(calls[1:], key=lambda c: order.get(c.split()[0], 99))

    def test_build_then_main_only_change_reuses_cache(self):
        s = self.settings()
        r = self.build(s)
        self.assertEqual(self.gen_calls(), ["layout", "main", "subsdk0 --hle", "sdk --hle"])
        self.assertEqual(r.generated, ["main", "subsdk0", "sdk"])
        dll = text_of(s.out)
        self.assertTrue(dll.startswith("FAKE DLL\nOBJ[// entry]\nOBJ[// fake layout.c]\nOBJ[// main 0 "))
        order = ["".join(ln.rstrip("]").split()[1:3]) for ln in dll.splitlines()[3:] if ln.startswith("OBJ[// ")]
        self.assertEqual(order, ["main0", "main1", "main2", "maintable", "subsdk00", "subsdk01", "subsdk02",
                                 "subsdk0table", "sdk0", "sdk1", "sdk2", "sdktable"])
        self.assertEqual(read(s.out + ".id"), b"767a39f088e81e1a\n")
        self.assertFalse(os.path.exists(s.work))  # work folder gone after a good build
        self.assertEqual(r.size, os.path.getsize(s.out))

        # main patched (same size, so the same layout): only main is generated again
        write(os.path.join(self.exefs, "main"), fake_nso(body=b"main v2."))
        r2 = self.build(s)
        self.assertEqual(self.gen_calls(), ["layout", "main"])
        self.assertEqual(r2.generated, ["main"])
        self.assertEqual(r2.cached, ["subsdk0", "sdk"])
        self.assertIn(b"6d61696e2076322e", read(s.out))  # "main v2." in the new main objects
        self.assertNotIn(b"6d61696e2076312e", read(s.out))
        self.assertEqual(read(s.out).count(b"OBJ["), 14)
        self.assertTrue(any("reusing" in ln for ln in self.lines))

        # back to v1: everything comes from the cache (two entries are kept per module)
        write(os.path.join(self.exefs, "main"), fake_nso(body=b"main v1."))
        r3 = self.build(s)
        self.assertEqual(self.gen_calls(), ["layout"])
        self.assertEqual(r3.cached, ["main", "subsdk0", "sdk"])
        self.assertEqual(text_of(s.out), dll)

    def test_no_cache(self):
        s = self.settings(cache=None)
        self.build(s)
        self.build(s)
        self.assertEqual(self.gen_calls(), ["layout", "main", "subsdk0 --hle", "sdk --hle"])
        self.assertFalse(os.path.exists(os.path.join(self.d, "rebuild", "cache")))

    def test_compile_failure_keeps_logs_and_no_cache_entry(self):
        s = self.settings()
        with mock.patch.dict(os.environ, {"FAKE_CC_FAIL": "sdk_001"}):
            with self.assertRaises(sw.StepFailed) as cm:
                self.build(s)
        self.assertIn("sdk_001.c", str(cm.exception))
        self.assertIn("made-up failure", "\n".join(sw.log_tail(cm.exception.log)))
        self.assertFalse(os.path.exists(s.out))
        left = [n for _r, _d, files in os.walk(s.work) for n in files if n != sw.WORK_MARK]
        self.assertTrue(left and all(n.endswith(".log") for n in left), left)
        self.assertTrue(os.path.isfile(os.path.join(s.work, sw.WORK_MARK)))  # (still ours for the next build)
        cache_sdk = os.path.join(s.cache, "sdk")
        self.assertEqual([n for n in os.listdir(cache_sdk)] if os.path.isdir(cache_sdk) else [], [])
        self.build(s)  # the next build cleans the old logs and works
        self.assertTrue(text_of(s.out).startswith("FAKE DLL"))

    def test_layout_failure(self):
        s = self.settings()
        os.remove(os.path.join(self.exefs, "sdk"))
        write(os.path.join(self.exefs, "sdk"), b"")  # the fake generator can't read this module's header
        with self.assertRaises(sw.StepFailed) as cm:
            self.build(s)
        self.assertIn("swgen layout", str(cm.exception))

    def test_module_generator_failure(self):
        s = self.settings()
        with mock.patch.dict(os.environ, {"FAKE_GEN_FAIL": "sdk"}):
            with self.assertRaises(sw.StepFailed) as cm:
                self.build(s)
        self.assertEqual(str(cm.exception), "swgen sdk failed (exit status 3)")
        self.assertIn("made-up generator failure in sdk", "\n".join(sw.log_tail(cm.exception.log)))
        self.assertFalse(os.path.exists(s.out))
        left = [n for _r, _d, files in os.walk(s.work) for n in files if n != sw.WORK_MARK]
        self.assertIn("gen_sdk.log", left)
        self.assertTrue(all(n.endswith(".log") for n in left), left)  # the chunks it wrote are gone
        self.assertEqual(os.listdir(os.path.join(s.cache, "sdk")), [])  # no entry, no staging folder

    def test_work_folder_not_made_by_the_tool_is_refused(self):
        root = os.path.join(self.d, "mystuff")
        notes = os.path.join(root, "work", "thesis", "notes.txt")
        write(notes, b"precious")
        s = self.settings(work=os.path.join(root, "work"), cache=os.path.join(root, "cache"))
        with self.assertRaisesRegex(sw.RebuildError, "didn't make"):
            self.build(s)
        self.assertEqual(read(notes), b"precious")
        self.assertFalse(os.path.exists(self.genlog))  # nothing was started
        self.assertFalse(os.path.exists(os.path.join(root, "cache")))

    def test_cache_folder_not_made_by_the_tool_is_refused(self):
        root = os.path.join(self.d, "mystuff")
        mine = os.path.join(root, "cache", "sdk", "a" * 32, "manifest.json")
        write(mine, '{"mine": true}')
        s = self.settings(work=os.path.join(root, "work"), cache=os.path.join(root, "cache"))
        with self.assertRaisesRegex(sw.RebuildError, "didn't make"):
            self.build(s)
        self.assertEqual(read(mine), b'{"mine": true}')

    def test_own_or_empty_work_folder_is_used(self):
        s = self.settings()
        write(os.path.join(s.work, sw.WORK_MARK), "")
        write(os.path.join(s.work, "main", "main_000.c"), "// left from an old build")
        self.build(s)
        self.assertFalse(os.path.exists(s.work))
        os.makedirs(s.work)  # an empty folder is fine too
        self.build(s)
        self.assertTrue(text_of(s.out).startswith("FAKE DLL"))

    def test_out_inside_work_or_cache_refused(self):
        for out in (os.path.join(self.d, "rebuild", "work", "swgame.dll"),
                    os.path.join(self.d, "rebuild", "cache", "x", "swgame.dll")):
            with self.assertRaisesRegex(sw.RebuildError, "cleans that folder up"):
                self.build(self.settings(out=out))
            self.assertFalse(os.path.exists(out))

    def test_game_library_never_written_without_install_mode(self):
        P = sw.paths_for(self.t.install)
        write(P["lib"], b"MZ vanilla")
        write(P["lib_id"], b"767a39f088e81e1a\n")
        for out in (P["lib"], "\\\\?\\" + P["lib"] if WIN else P["lib"],
                    os.path.join(self.t.install, "extracted", "..", "extracted", "SWGAME.DLL")):
            with self.assertRaisesRegex(sw.RebuildError, "game's own library"):
                self.build(self.settings(out=out))
        self.assertEqual(read(P["lib"]), b"MZ vanilla")
        self.assertEqual(read(P["lib_id"]), b"767a39f088e81e1a\n")
        # and the check is made again just before the result is moved into place
        s = self.settings()
        real_check = sw.check_out
        calls = []

        def check(s2):
            calls.append(1)
            if len(calls) == 2:
                s2.out = P["lib"]  # (as if the out path came to mean the game's library during the build)
            return real_check(s2)
        with mock.patch.object(sw, "check_out", check):
            with self.assertRaisesRegex(sw.RebuildError, "game's own library"):
                self.build(s)
        self.assertEqual(len(calls), 2)
        self.assertEqual(read(P["lib"]), b"MZ vanilla")

    def test_install_mode(self):
        P = sw.paths_for(self.t.install)
        write(P["main"], fake_nso())
        write(P["lib"], b"MZ vanilla")
        write(P["lib_id"], b"767a39f088e81e1a\n")
        s = self.settings(out=P["lib"], install_mode=True)
        r = self.build(s)
        self.assertEqual(r.dll, P["lib"])
        self.assertTrue(read(P["lib"]).startswith(b"FAKE DLL"))
        self.assertEqual(read(P["lib_vanilla"]), b"MZ vanilla")
        self.assertEqual(read(P["lib_vanilla_id"]), b"767a39f088e81e1a\n")
        self.assertEqual(read(P["lib_id"]), b"767a39f088e81e1a\n")
        self.assertEqual(sw.restore(self.t.install, "library", running=NOT_RUNNING), ["library: vanilla restored"])
        self.assertEqual(read(P["lib"]), b"MZ vanilla")

    def test_install_refused_when_game_started_meanwhile(self):
        P = sw.paths_for(self.t.install)
        write(P["lib"], b"MZ vanilla")
        s = self.settings(out=P["lib"], install_mode=True)
        with self.assertRaisesRegex(sw.RebuildError, "started during the build"):
            sw.run_build(s, self.t, say=self.lines.append, running=lambda: True, poll_s=0.01)
        self.assertEqual(read(P["lib"]), b"MZ vanilla")
        self.assertFalse(os.path.exists(P["lib_vanilla"]))

    def test_cache_folder_in_use_still_builds(self):
        s = self.settings()
        with mock.patch.object(sw.ObjectCache, "commit", lambda *a, **k: None):
            r = self.build(s)
        self.assertTrue(text_of(s.out).startswith("FAKE DLL"))
        self.assertEqual(text_of(s.out).count("OBJ["), 14)
        self.assertEqual(sum("couldn't be stored" in n for n in r.notes), 3)
        for mod in ("main", "subsdk0", "sdk"):  # staging folders are gone, nothing cached
            self.assertEqual(os.listdir(os.path.join(s.cache, mod)), [])

    def test_lock(self):
        s = self.settings()
        lock = sw._lock(s.work.rstrip("\\/") + ".lock")
        try:
            with self.assertRaisesRegex(sw.RebuildError, "another rebuild"):
                self.build(s)
        finally:
            lock.close()


def short_path(path: str) -> str | None:
    """The 8.3 spelling of an existing path, or None when the volume has no short names."""
    if not WIN:
        return None
    import ctypes
    from ctypes import wintypes
    f = ctypes.windll.kernel32.GetShortPathNameW
    f.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    buf = ctypes.create_unicode_buffer(32768)
    n = f(path, buf, 32768)
    return buf.value if n and buf.value.lower() != path.lower() else None


class HooksPipeline(unittest.TestCase):
    """--hooks with the fake builders: main's C gets renamed definitions and main_hooks.c before it is compiled."""
    setUp, tearDown = Pipeline.setUp, Pipeline.tearDown
    settings, build, gen_calls = Pipeline.settings, Pipeline.build, Pipeline.gen_calls

    def build_with_funcs(self, s):
        with mock.patch.dict(os.environ, {"FAKE_GEN_FUNCS": "1"}):
            return self.build(s)

    def test_hooked_build(self):
        s = self.settings(hooks=[0x7000000200])
        r = self.build_with_funcs(s)
        dll = text_of(s.out)
        self.assertIn("void f_7000000200(Cpu *c); /* gen_hooks: the wrapper is in main_hooks.c */\n"
                      "void f_7000000200__orig(Cpu *c) {", dll)
        self.assertIn("void f_7000000100(Cpu *c) {", dll)  # (not hooked: unchanged)
        self.assertIn("swmod_hooks(void)", dll)
        self.assertIn("    __attribute__((musttail)) return f_7000000200__orig(c);", dll)
        objs = [ln.split()[1:3] for ln in dll.splitlines() if ln.startswith("OBJ[")]
        self.assertEqual(objs[2:8], [["main", "0"], ["main", "1"], ["main", "2"], ["main", "table"],
                                     ["main_hooks.c:", "hook"], ["subsdk0", "0"]])  # (link order: after the table)
        self.assertEqual(len(objs), 15)  # (14 + main_hooks)
        self.assertTrue(any(ln == "main: hook slots for 1 function(s)" for ln in self.lines), self.lines)
        self.assertTrue(any(n.startswith("Hook slots: 1 function(s)") for n in r.notes), r.notes)

        # the same hooks again: main from the cache; without hooks: main generated again, no hook slots
        r2 = self.build_with_funcs(s)
        self.assertEqual(self.gen_calls(), ["layout"])
        self.assertEqual(text_of(s.out), dll)
        self.assertTrue(any(n.startswith("Hook slots:") for n in r2.notes))
        r3 = self.build_with_funcs(self.settings())
        self.assertEqual(self.gen_calls(), ["layout", "main"])
        self.assertNotIn("__orig", text_of(s.out))
        self.assertNotIn("swmod_hooks", text_of(s.out))
        self.assertFalse(any(n.startswith("Hook slots:") for n in r3.notes))

    def test_self_jump_note(self):
        """A hooked function that jumps back to its own start keeps that jump (a goto inside it), and the build says
        its hooks run for the outer call only."""
        s = self.settings(hooks=[0x7000000200])
        with mock.patch.dict(os.environ, {"FAKE_GEN_FUNCS": "selfjump"}):
            r = self.build(s)
        dll = text_of(s.out)
        self.assertIn("void f_7000000200__orig(Cpu *c) {\nL_7000000200:;\n    if (c->x[0] != 0) { goto L_7000000200; }",
                      dll)
        notes = [n for n in r.notes if n.startswith("Note: f_")]
        self.assertEqual(notes, ["Note: f_7000000200 jumps back to its own start (a loop, or a call to itself). "
                                 "Hooks run for the outer call only, not for those jumps (docs/hooks.md, Limits)."])
        r2 = self.build_with_funcs(self.settings(hooks=[0x7000000300]))  # (no self jump: no note)
        self.assertEqual(self.gen_calls(), ["layout", "main"])
        self.assertFalse(any(n.startswith("Note: f_") for n in r2.notes), r2.notes)

    def test_not_a_function_start(self):
        s = self.settings(hooks=[0x7000000100, 0x7000000104])
        with self.assertRaisesRegex(sw.RebuildError, r"^--hooks: can't hook 0x7000000104 is not the start of a "
                                                     r"function \(it is 0x4 bytes into f_7000000100\)"):
            self.build_with_funcs(s)
        self.assertFalse(os.path.exists(s.out))
        self.assertEqual(os.listdir(os.path.join(self.d, "rebuild", "cache", "main")), [])  # (nothing cached)
        self.assertTrue(os.path.isfile(os.path.join(s.work, "gen_main.log")))  # (the logs stay)

    def test_no_hooks_changes_nothing(self):
        s = self.settings()
        self.build_with_funcs(s)
        self.assertNotIn("__orig", text_of(s.out))
        self.assertEqual(text_of(s.out).count("OBJ["), 14)


class Paths(unittest.TestCase):
    def test_same_file_spellings(self):
        with tempfile.TemporaryDirectory() as d:
            lib = os.path.join(d, "Savage Wind Install", "extracted", "swgame.dll")
            write(lib, b"MZ")
            self.assertTrue(sw.same_file(lib, lib.upper()))
            self.assertTrue(sw.same_file(os.path.join(os.path.dirname(lib), "..", "extracted", "swgame.dll"), lib))
            self.assertFalse(sw.same_file(lib + ".id", lib))
            if WIN:
                self.assertTrue(sw.same_file("\\\\?\\" + lib, lib))
            short = short_path(lib)
            if short:
                self.assertTrue(sw.same_file(short, lib))
            # a file that doesn't exist yet, spelled through a short folder name
            short_dir = short_path(os.path.dirname(lib))
            if short_dir:
                self.assertTrue(sw.same_file(os.path.join(short_dir, "new.dll"),
                                             os.path.join(os.path.dirname(lib), "NEW.dll")))

    @unittest.skipUnless(WIN and hasattr(__import__("_winapi"), "CreateJunction"), "needs junctions")
    def test_same_file_through_junction(self):
        import _winapi
        with tempfile.TemporaryDirectory() as d:
            ex = os.path.join(d, "Savage Wind", "extracted")
            write(os.path.join(ex, "swgame.dll"), b"MZ")
            j = os.path.join(d, "shortcut")
            _winapi.CreateJunction(ex, j)
            try:
                self.assertTrue(sw.same_file(os.path.join(j, "swgame.dll"), os.path.join(ex, "swgame.dll")))
                self.assertTrue(sw.is_inside(os.path.join(j, "x.dll"), ex))
            finally:
                os.rmdir(j)

    def test_is_inside(self):
        with tempfile.TemporaryDirectory() as d:
            w = os.path.join(d, "rebuild", "work")
            self.assertTrue(sw.is_inside(w, w))
            self.assertTrue(sw.is_inside(os.path.join(w, "a", "swgame.dll"), w.upper()))
            self.assertFalse(sw.is_inside(w + "2", w))
            self.assertFalse(sw.is_inside(os.path.join(d, "rebuild", "swgame.dll"), w))

    def test_claim_dir(self):
        with tempfile.TemporaryDirectory() as d:
            new = os.path.join(d, "new", "work")
            sw.claim_dir(new, sw.WORK_MARK)  # made, with the marker
            self.assertTrue(os.path.isfile(os.path.join(new, sw.WORK_MARK)))
            write(os.path.join(new, "old.log"), "x")
            sw.claim_dir(new, sw.WORK_MARK)  # ours: fine with files in it
            empty = os.path.join(d, "empty")
            os.makedirs(empty)
            sw.claim_dir(empty, sw.WORK_MARK)
            self.assertTrue(os.path.isfile(os.path.join(empty, sw.WORK_MARK)))
            theirs = os.path.join(d, "theirs")
            write(os.path.join(theirs, "notes.txt"), b"precious")
            with self.assertRaisesRegex(sw.RebuildError, "didn't make"):
                sw.claim_dir(theirs, sw.WORK_MARK)
            self.assertEqual(os.listdir(theirs), ["notes.txt"])
            a_file = os.path.join(d, "afile")
            write(a_file, b"x")
            with self.assertRaisesRegex(sw.RebuildError, "isn't a folder"):
                sw.claim_dir(a_file, sw.WORK_MARK)
            with self.assertRaisesRegex(sw.RebuildError, "didn't make"):
                sw.claim_dir(theirs, sw.CACHE_MARK)

    def test_clear_dir_keeps_marker(self):
        with tempfile.TemporaryDirectory() as d:
            write(os.path.join(d, sw.WORK_MARK), "")
            write(os.path.join(d, "main", "main_000.c"), "x")
            write(os.path.join(d, "layout.txt"), "x")
            sw.clear_dir(d, sw.WORK_MARK)
            self.assertEqual(os.listdir(d), [sw.WORK_MARK])


class CliInstall:
    """A made-up Savage Wind folder with dummy builder files (never run) and a made-up exefs."""

    def __init__(self, root: str):
        self.root = root
        self.install = os.path.join(root, "Savage Wind")
        for p in ("swgen/swgen.exe", "zig/zig.exe", "gamelib-src/cpu.h", "gamelib-src/gamelib_entry.c",
                  "gamelib-src/hle_symbols.txt", "savagewind.dll.a"):
            write(os.path.join(self.install, p), b"dummy, never run")
        self.P = sw.paths_for(self.install)
        self.main = fake_nso(body=b"main v1.")
        write(self.P["main"], self.main)
        write(os.path.join(self.P["exefs"], "subsdk0"), fake_nso(bytes(32), text=0x1000, body=b"subsdk0."))
        write(os.path.join(self.P["exefs"], "sdk"), fake_nso(bytes(32), text=0x2000, body=b"sdk v1.."))
        write(self.P["lib"], b"MZ vanilla library")
        write(self.P["lib_id"], b"767a39f088e81e1a\n")
        self.work_root = os.path.join(root, "rebuild")

    def args(self, *extra):
        return sw.parser().parse_args(["build", self.install, "--work-root", self.work_root, "-j", "2", *extra])

    def exefs_copy(self, name="patched", **change) -> str:
        """A copy of extracted\\exefs with some modules replaced: change={"sdk": b"..."}."""
        d = os.path.join(self.root, name)
        for n in ("main", "subsdk0", "sdk"):
            write(os.path.join(d, n), change.get(n, read(os.path.join(self.P["exefs"], n))))
        return d


class Prepare(unittest.TestCase):
    """The checks prepare() and the build command make before anything is built or replaced."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fi = CliInstall(self.tmp.name)
        clean = {k: v for k, v in os.environ.items() if k not in ("SAVAGEWIND_GEN", "SAVAGEWIND_CC")}
        self.env = mock.patch.dict(os.environ, clean, clear=True)
        self.env.start()
        self.notrun = mock.patch.object(sw, "game_running", NOT_RUNNING)
        self.notrun.start()

    def tearDown(self):
        self.notrun.stop()
        self.env.stop()
        self.tmp.cleanup()

    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = sw.main(list(argv))
        return rc, out.getvalue()

    def test_defaults(self):
        s, t, notes = sw.prepare(self.fi.args())
        self.assertEqual(s.out, os.path.join(sw.OUT_ROOT, "swgame.dll"))
        self.assertFalse(s.install_mode)
        self.assertEqual(s.gamelib_id, "767a39f088e81e1a")
        self.assertEqual(s.work, os.path.join(self.fi.work_root, "work"))
        self.assertEqual(notes, [])
        s, t, notes = sw.prepare(self.fi.args("--install"))
        self.assertTrue(s.install_mode)
        self.assertEqual(s.out, self.fi.P["lib"])

    def test_game_library_needs_install(self):
        lib = self.fi.P["lib"]
        spellings = [lib, os.path.dirname(lib), lib.upper(), os.path.join(self.fi.install, "x", "..", "extracted")]
        if WIN:
            spellings.append("\\\\?\\" + lib)
        for sp in (short_path(lib), short_path(os.path.dirname(lib))):
            if sp:
                spellings.append(sp)
        for out in spellings:
            with self.assertRaisesRegex(sw.RebuildError, "game's own library", msg=out):
                sw.prepare(self.fi.args("--out", out))
        rc, text = self.run_main("build", self.fi.install, "--out", lib, "--work-root", self.fi.work_root)
        self.assertEqual(rc, 2)
        self.assertIn("ERROR that's the game's own library", text)
        self.assertEqual(read(lib), b"MZ vanilla library")

    def test_install_and_out_conflict(self):
        with self.assertRaisesRegex(sw.RebuildError, "can't be used together"):
            sw.prepare(self.fi.args("--install", "--out", os.path.join(self.tmp.name, "x.dll")))

    def test_out_inside_work_or_cache(self):
        for sub in ("work", os.path.join("cache", "main")):
            with self.assertRaisesRegex(sw.RebuildError, "cleans that folder up"):
                sw.prepare(self.fi.args("--out", os.path.join(self.fi.work_root, sub)))
        sw.prepare(self.fi.args("--out", os.path.join(self.fi.work_root, "swgame.dll")))  # next to them: fine

    def test_other_main(self):
        patched = self.fi.exefs_copy(main=fake_nso(body=b"main v2."))
        with self.assertRaisesRegex(sw.RebuildError, "run install-patched-main first"):
            sw.prepare(self.fi.args("--install", "--exefs", patched))
        s, t, notes = sw.prepare(self.fi.args("--exefs", patched))
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("Note: main differs from extracted\\exefs."), notes)

    def test_other_sdk_or_subsdk(self):
        for mod in ("sdk", "subsdk0"):
            patched = self.fi.exefs_copy(f"p-{mod}", **{mod: fake_nso(bytes(32), text=0x2000, body=b"patched.")})
            with self.assertRaisesRegex(sw.RebuildError, f"^{mod} in .* differs from extracted.*only a patched main"):
                sw.prepare(self.fi.args("--install", "--exefs", patched))
            s, t, notes = sw.prepare(self.fi.args("--exefs", patched))
            self.assertTrue(notes and notes[0].startswith(f"Note: {mod} differs"), notes)
        both = self.fi.exefs_copy("p-both", main=fake_nso(body=b"main v2."),
                                  sdk=fake_nso(bytes(32), text=0x2000, body=b"patched."))
        s, t, notes = sw.prepare(self.fi.args("--exefs", both))
        self.assertTrue(notes[0].startswith("Note: main, sdk differ"), notes)
        same = self.fi.exefs_copy("same")
        self.assertEqual(sw.prepare(self.fi.args("--install", "--exefs", same))[2], [])

    def test_id_from_backup(self):
        os.remove(self.fi.P["lib_id"])
        write(self.fi.P["lib_vanilla_id"], b"b4d249669cc63ebf\n")
        s, t, notes = sw.prepare(self.fi.args())  # no exe to check against: used
        self.assertEqual((s.gamelib_id, s.target), ("b4d249669cc63ebf", "compat"))
        exe = os.path.join(self.fi.install, "savagewind.exe")
        write(exe, b"MZ\x00b4d249669cc63ebf\x00767a39f088e81e1a\x00")
        self.assertEqual(sw.prepare(self.fi.args())[0].gamelib_id, "b4d249669cc63ebf")
        write(exe, b"MZ\x00aaaaaaaaaaaaaaaa\x00bbbbbbbbbbbbbbbb\x00")  # a newer Savage Wind
        with self.assertRaisesRegex(sw.RebuildError, "isn't this savagewind.exe's"):
            sw.prepare(self.fi.args())
        self.assertEqual(sw.prepare(self.fi.args("--id", "aaaaaaaaaaaaaaaa"))[0].gamelib_id, "aaaaaaaaaaaaaaaa")
        os.remove(self.fi.P["lib_vanilla_id"])
        with self.assertRaisesRegex(sw.RebuildError, "set up the game files"):
            sw.prepare(self.fi.args())

    def test_hooks_option(self):
        self.assertEqual(sw.prepare(self.fi.args())[0].hooks, [])
        a, b = os.path.join(self.tmp.name, "a.txt"), os.path.join(self.tmp.name, "b.json")
        write(a, "0x7000000100  # a\nmain+0x200\n")
        write(b, '{"hooks": ["0x7000000100", "0x7000002ffc"]}')
        s = sw.prepare(self.fi.args("--hooks", a, "--hooks", b))[0]
        self.assertEqual(s.hooks, [0x7000000100, 0x7000000200, 0x7000002ffc])
        for text, msg in (("0x7000003000\n", r"0x7000003000 is not in main's code \(0x7000000000-0x7000003000\)"),
                          ("0x7000000102\n", "0x7000000102 is not a multiple of 4"),
                          ("# nothing\n", "lists no addresses"),
                          ("zzz\n", "line 1: 'zzz' is not an address")):
            write(a, text)
            with self.assertRaisesRegex(sw.RebuildError, "^--hooks: .*" + msg):
                sw.prepare(self.fi.args("--hooks", a))
        rc, out = self.run_main("build", self.fi.install, "--work-root", self.fi.work_root, "--hooks",
                                os.path.join(self.tmp.name, "missing.txt"))
        self.assertEqual(rc, 2)
        self.assertIn("ERROR --hooks: can't read the hooks file", out)
        write(a, "0x7000000100\n")
        rc, out = self.run_main("build", self.fi.install, "--work-root", self.fi.work_root, "--hooks", a,
                                "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("hook slots for 1 function(s) of main: 0x7000000100", out)
        self.assertIn("tools/gen_hooks.py", out)

    def test_dry_run_runs_nothing(self):
        rc, text = self.run_main("build", self.fi.install, "--work-root", self.fi.work_root, "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("Commands (nothing is run):", text)
        self.assertFalse(os.path.exists(os.path.join(self.fi.work_root, "work")))

    def test_work_root_with_other_files_is_left_alone(self):
        """The reviewer's case: --work-root pointing at a folder whose work\\ holds the user's files."""
        mine = os.path.join(self.tmp.name, "mystuff")
        notes = os.path.join(mine, "work", "thesis", "notes.txt")
        write(notes, b"precious")
        rc, text = self.run_main("build", self.fi.install, "--work-root", mine, "-j", "1")
        self.assertEqual(rc, 2)
        self.assertIn("didn't make", text)
        self.assertEqual(read(notes), b"precious")
        self.assertEqual(sorted(os.listdir(os.path.join(mine, "work"))), ["thesis"])

    def test_install_backs_up_before_the_build(self):
        seen = []

        def fake_run_build(s, t):
            seen.append((read(self.fi.P["lib_vanilla"]), read(self.fi.P["lib_vanilla_id"])))
            raise sw.StepFailed("swgen layout", os.path.join(self.tmp.name, "none.log"), 1)
        with mock.patch.object(sw, "run_build", fake_run_build):
            rc, text = self.run_main("build", self.fi.install, "--install", "--work-root", self.fi.work_root)
        self.assertEqual(rc, 1)
        self.assertEqual(seen, [(b"MZ vanilla library", b"767a39f088e81e1a\n")])
        self.assertIn("library: vanilla copy saved", text)
        self.assertIn("FAILED: swgen layout failed (exit status 1)", text)
        self.assertEqual(read(self.fi.P["lib"]), b"MZ vanilla library")

    def test_install_refuses_when_backup_would_be_lost(self):
        state = {"library_installed_sha256": hashlib.sha256(b"MZ vanilla library").hexdigest()}
        sw.save_state(self.fi.install, state)  # (the installed library is a rebuilt one, and no backup exists)
        with mock.patch.object(sw, "run_build") as rb:
            rc, text = self.run_main("build", self.fi.install, "--install", "--work-root", self.fi.work_root)
        self.assertEqual(rc, 2)
        self.assertIn("vanilla backup is missing", text)
        rb.assert_not_called()

    def test_install_end_to_end_with_fake_builders(self):
        t = make_fake_tools(self.tmp.name, self.fi.install)
        with mock.patch.object(sw, "find_tools", lambda install: t):
            rc, text = self.run_main("build", self.fi.install, "--install", "--work-root", self.fi.work_root,
                                     "-j", "2")
        self.assertEqual(rc, 0, text)
        self.assertIn("library: vanilla copy saved", text)
        self.assertTrue(read(self.fi.P["lib"]).startswith(b"FAKE DLL"))
        self.assertEqual(read(self.fi.P["lib_vanilla"]), b"MZ vanilla library")
        self.assertEqual(read(self.fi.P["lib_id"]), b"767a39f088e81e1a\n")
        self.assertFalse(os.path.exists(os.path.join(self.fi.work_root, "work")))
        self.assertTrue(os.path.isfile(os.path.join(self.fi.work_root, "cache", sw.CACHE_MARK)))

    def test_hooked_build_says_to_start_through_modhost(self):
        """The reviewer's case: after --hooks the closing line named the hooked library as SAVAGEWIND_GAME_LIB, which
        starts the game with no hook running (only modhost fills in the slots)."""
        t = make_fake_tools(self.tmp.name, self.fi.install)
        hooks = os.path.join(self.tmp.name, "hooks.txt")
        write(hooks, "0x7000000200\n")
        out = os.path.join(self.tmp.name, "hooked", "swgame.dll")
        with mock.patch.object(sw, "find_tools", lambda install: t), \
                mock.patch.dict(os.environ, {"FAKE_GEN_FUNCS": "1"}):
            rc, text = self.run_main("build", self.fi.install, "--work-root", self.fi.work_root, "-j", "2",
                                     "--hooks", hooks, "--out", out)
        self.assertEqual(rc, 0, text)
        self.assertIn("Hooks only run when the game starts through modhost (docs/hooks.md).", text)
        self.assertIn(f"\n  SAVAGEWIND_GAME_LIB={sw.MODHOST_DLL}\n  SWMOD_REAL_GAME_LIB={out}\n", text)
        self.assertNotIn(f"SAVAGEWIND_GAME_LIB={out}", text)
        self.assertNotIn("To try it", text)


class Cli(unittest.TestCase):
    def settings(self, **kw):
        return sw.Settings(**{**dict(install="SW", exefs="E", out="O.dll", gamelib_id="767a39f088e81e1a",
                                     target="normal", flags=[]), **kw})

    def test_try_hint(self):
        self.assertEqual(sw.try_hint(self.settings(), "X.dll"),
                         ["To try it, start savagewind.exe with SAVAGEWIND_GAME_LIB=X.dll, or use --install."])
        self.assertEqual(sw.try_hint(self.settings(install_mode=True), "X.dll"), [])
        for installed in (False, True):
            with mock.patch.object(os.path, "isfile", lambda p: True):
                lines = sw.try_hint(self.settings(hooks=[0x7000000200], install_mode=installed), "X.dll")
            self.assertIn(f"  SAVAGEWIND_GAME_LIB={sw.MODHOST_DLL}", lines)
            self.assertEqual("  SWMOD_REAL_GAME_LIB=X.dll" in lines, not installed)  # (installed: modhost's default)
            self.assertFalse(any("GAME_LIB=X.dll" in ln and "SWMOD_REAL" not in ln for ln in lines), lines)
            self.assertFalse(any("build_native.py build" in ln for ln in lines))
        with mock.patch.object(os.path, "isfile", lambda p: False):
            lines = sw.try_hint(self.settings(hooks=[0x7000000200]), "X.dll")
        self.assertEqual(lines[-1], "modhost.dll isn't built yet: python tools/build_native.py build")

    def test_docs_hooks_commands_parse(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "docs", "hooks.md"), encoding="utf-8") as f:
            cmds = re.findall(r"sw_rebuild\.py (build[^`\n#]*)", f.read())
        self.assertTrue(cmds)
        for cmd in cmds:
            a = sw.parser().parse_args(cmd.split())
            self.assertTrue(a.hooks, cmd)
    def test_out_paths(self):
        self.assertEqual(sw._resolve_out(None), os.path.join(sw.OUT_ROOT, "swgame.dll"))
        self.assertTrue(sw._resolve_out(os.path.join("x", "my.dll")).endswith(os.path.join("x", "my.dll")))
        self.assertTrue(sw._resolve_out("folder").endswith(os.path.join("folder", "swgame.dll")))

    def test_missing_install(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(sw.RebuildError, "missing"):
                sw.find_tools(d)

    def test_cache_keep_help_matches_lru(self):
        build = sw.parser()._subparsers._group_actions[0].choices["build"]
        keep = [a for a in build._actions if "--cache-keep" in a.option_strings][0]
        self.assertIn("most recently used", keep.help)  # (prune keeps the N most recently used: test_object_cache)

    def test_fmt(self):
        self.assertEqual(sw.fmt_time(5), "5s")
        self.assertEqual(sw.fmt_time(1023), "17m 03s")
        self.assertEqual(sw.fmt_mb(3 * 2**20), "3.0 MB")

    def test_path_outside_the_code_page_when_redirected(self):
        """W3: redirected stdout on Windows is the ANSI code page with strict errors. A folder with a letter it lacks
        made the ERROR line itself raise UnicodeEncodeError, so the real message was lost (made-up folder)."""
        with tempfile.TemporaryDirectory() as d:
            missing = os.path.join(d, "Łukasz", "Savage Wind")
            raw = io.BytesIO()
            out = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
            with mock.patch.object(sys, "stdout", out):
                rc = sw.main(["status", missing])
                out.flush()
        self.assertEqual(rc, 2)
        text = raw.getvalue().decode("cp1252")
        self.assertTrue(text.startswith("ERROR no Savage Wind folder at "), text)
        self.assertIn(os.sep + "?ukasz" + os.sep, text)

    def test_lenient_stdout_left_alone(self):
        """modman calls main() in its own process after choosing its own error handler: keep it."""
        out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="backslashreplace")
        with mock.patch.object(sys, "stdout", out):
            sw._safe_stdout()
        self.assertEqual(out.errors, "backslashreplace")


GERMAN_NO_TASKS = "INFORMATION: Es werden keine Tasks mit den angegebenen Kriterien ausgeführt.\r\n"
POLISH_NO_TASKS = "INFORMACJA: Brak uruchomionych zadań spełniających określone kryteria.\r\n"


class GameRunning(unittest.TestCase):
    """W1: tasklist writes in the console (OEM) code page. On a German or Polish Windows its 'no tasks' line has
    bytes the ANSI code page can't decode (u-umlaut is 0x81 in cp850), and game_running() raised instead of
    answering, in every command that checks for the game. The wording above is made up to the same effect."""

    def with_output(self, stdout):
        done = subprocess.CompletedProcess(["tasklist"], 0, stdout=stdout, stderr=b"")
        with mock.patch.object(sw.sys, "platform", "win32"), \
                mock.patch.object(sw.subprocess, "run", return_value=done) as run:
            result = sw.game_running()
        self.assertNotIn("text", run.call_args.kwargs)  # (bytes: nothing to decode, nothing to fail)
        return result

    def test_localized_no_tasks_line(self):
        self.assertFalse(self.with_output(GERMAN_NO_TASKS.encode("cp850")))
        self.assertFalse(self.with_output(POLISH_NO_TASKS.encode("cp852")))

    def test_running_game_found_in_oem_output(self):
        line = '"savagewind.exe","29600","Console","1","5 373 484 K"\r\n'.encode("cp850")
        self.assertTrue(self.with_output(line))
        self.assertTrue(self.with_output(line.upper()))

    def test_no_output_at_all(self):
        self.assertFalse(self.with_output(None))

    @unittest.skipUnless(WIN, "tasklist is Windows only")
    def test_real_pipe_with_oem_bytes(self):
        """The reviewer's repro: a real child writes the German line in cp850 through game_running's own
        subprocess.run arguments (with text=True the reader thread died and stdout came back None)."""
        real_run = subprocess.run
        payload = GERMAN_NO_TASKS.encode("cp850")

        def child(argv, **kw):
            return real_run([sys.executable, "-c", f"import sys; sys.stdout.buffer.write({payload!r})"], **kw)

        with mock.patch.object(sw.subprocess, "run", child):
            self.assertFalse(sw.game_running())

    def test_default_check_is_looked_up_when_called(self):
        """The commands' default check is the module's game_running at call time, so a test (or modman) replacing
        it is honoured (it was bound when the module loaded, so a real running game failed these tests)."""
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d, link=False)
            with mock.patch.object(sw, "game_running", lambda: True):
                with self.assertRaisesRegex(sw.RebuildError, "is running"):
                    sw.install_patched_main(fi.patched(), fi.install)
                with self.assertRaisesRegex(sw.RebuildError, "is running"):
                    sw.restore(fi.install)
            self.assertEqual(read(fi.P["main"]), fi.vanilla)


class DefaultInstall(unittest.TestCase):
    """W4 / clean-6: the default Savage Wind folder came from this developer's own user folder, written into the
    code. It now comes from SWMOD_INSTALL, else %USERPROFILE%\\Documents\\SavageWind\\Savage Wind."""

    def env(self, **values):
        clean = {k: v for k, v in os.environ.items() if k not in ("SWMOD_INSTALL", "USERPROFILE")}
        clean.update(values)
        return mock.patch.dict(os.environ, clean, clear=True)

    def test_from_the_user_profile(self):
        someone = os.path.join(os.sep, "Users", "Someone Else")
        with self.env(USERPROFILE=someone):
            self.assertEqual(sw.default_install(),
                             os.path.join(someone, "Documents", "SavageWind", "Savage Wind"))

    def test_env_override(self):
        other = os.path.join(os.sep, "Games", "SW")
        with self.env(USERPROFILE=os.path.join(os.sep, "Users", "Someone Else"), SWMOD_INSTALL=other):
            self.assertEqual(sw.default_install(), other)
        with self.env(USERPROFILE=os.path.join(os.sep, "Users", "Someone Else"), SWMOD_INSTALL="  "):
            self.assertTrue(sw.default_install().endswith(os.path.join("SavageWind", "Savage Wind")))

    def test_parser_has_no_fixed_default(self):
        self.assertIsNone(sw.parser().parse_args(["status"]).install_dir)
        self.assertIsNone(sw.parser().parse_args(["build", "--dry-run"]).install_dir)
        self.assertEqual(sw.parser().parse_args(["status", "X"]).install_dir, "X")

    def test_status_uses_the_default(self):
        with tempfile.TemporaryDirectory() as d:
            fi = FakeInstall(d, link=False)
            with self.env(SWMOD_INSTALL=fi.install), mock.patch.object(sw, "game_running", NOT_RUNNING), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(sw.main(["status"]), 0)
            lines = out.getvalue().splitlines()
            self.assertEqual(lines[0], f"folder   {os.path.abspath(fi.install)}")
            self.assertIn("main     as set up", lines)

    def test_missing_folder_is_said(self):
        """status printed 'library missing / main missing' for a folder that isn't there, and exited 0."""
        with tempfile.TemporaryDirectory() as d:
            nobody = os.path.join(d, "Users", "nobody")
            expect = os.path.join(nobody, "Documents", "SavageWind", "Savage Wind")
            for argv in (["status"], ["build", "--dry-run"]):
                with self.env(USERPROFILE=nobody), mock.patch.object(sw, "game_running", NOT_RUNNING), \
                        contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(sw.main(argv), 2, argv)
                self.assertEqual(out.getvalue().strip(), f"ERROR no Savage Wind folder at {expect} (the default; "
                                                         "give the folder, or set SWMOD_INSTALL to it)")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(sw.main(["status", os.path.join(d, "nope")]), 2)
            self.assertEqual(out.getvalue().strip(), f"ERROR no Savage Wind folder at {os.path.join(d, 'nope')}")
            # SWMOD_INSTALL set to a missing folder: it isn't "the default", and telling the user to set it is no help
            gone = os.path.join(d, "SW")
            for argv in (["status"], ["build", "--dry-run"]):
                with self.env(SWMOD_INSTALL=gone), mock.patch.object(sw, "game_running", NOT_RUNNING), \
                        contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(sw.main(argv), 2, argv)
                self.assertEqual(out.getvalue().strip(), f"ERROR no Savage Wind folder at {gone} (from SWMOD_INSTALL)")

    def test_no_user_folder_in_shipped_code(self):
        user_path = re.compile(r"[A-Za-z]:\\{1,2}Users\\{1,2}(?!Public\b)[A-Za-z0-9]")
        tools = os.path.join(os.path.dirname(__file__), "..", "tools")
        for name in ("sw_rebuild.py", "exefs_patch.py", "mod_inspect.py", "gen_hooks.py"):
            with open(os.path.join(tools, name), encoding="utf-8") as f:
                self.assertIsNone(user_path.search(f.read()), name)


class ReadOnlyTarget(unittest.TestCase):
    """W9: Windows won't rename over a read-only file, and extracted\\exefs\\main may be a read-only hard link to
    the user's own dump (clearing read-only would clear it on the dump too). install-patched-main ended in a bare
    '[WinError 5] Access is denied'."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fi = FakeInstall(self.tmp.name)  # (main: a hard link to the made-up dump)
        os.chmod(self.fi.dump_main, stat.S_IREAD)

    def tearDown(self):
        for p in (self.fi.dump_main, self.fi.P["main"]):
            if os.path.exists(p):
                os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
        self.tmp.cleanup()

    def read_only(self, p):
        return not os.stat(p).st_mode & stat.S_IWRITE

    def test_install_patched_main(self):
        patched = self.fi.patched()
        msgs = sw.install_patched_main(patched, self.fi.install, running=NOT_RUNNING)
        self.assertIn("installed", msgs[0])
        self.assertEqual(read(self.fi.P["main"]), read(patched))
        self.assertEqual(read(self.fi.dump_main), self.fi.vanilla)  # the dump: same bytes, still read-only
        self.assertTrue(self.read_only(self.fi.dump_main))
        self.assertEqual(os.stat(self.fi.dump_main).st_nlink, 1)
        self.assertEqual(os.listdir(self.fi.P["exefs"]), ["main"])  # no .tmp or .old names left

    def test_restore_over_a_read_only_main(self):
        sw.install_patched_main(self.fi.patched(), self.fi.install, running=NOT_RUNNING)
        os.chmod(self.fi.P["main"], stat.S_IREAD)  # (its only name now)
        self.assertEqual(sw.restore(self.fi.install, "main", running=NOT_RUNNING), ["main: vanilla restored"])
        self.assertEqual(read(self.fi.P["main"]), self.fi.vanilla)
        self.assertEqual(os.listdir(self.fi.P["exefs"]), ["main"])

    @unittest.skipUnless(WIN, "only Windows refuses to rename over a read-only file")
    def test_put_back_when_only_this_name_cant_go(self):
        with mock.patch.object(sw, "_delete_name_only", lambda p: False):
            with self.assertRaisesRegex(sw.RebuildError, "read-only and is also the file in another place"):
                sw.install_patched_main(self.fi.patched(), self.fi.install, running=NOT_RUNNING)
        self.assertTrue(os.path.samefile(self.fi.P["main"], self.fi.dump_main))  # main is the dump's link again
        self.assertEqual(read(self.fi.dump_main), self.fi.vanilla)
        self.assertTrue(self.read_only(self.fi.dump_main))
        self.assertEqual(os.listdir(self.fi.P["exefs"]), ["main"])
        self.assertIsNone(sw.load_state(self.fi.install).get("main_installed_sha256"))

    def test_plain_read_only_file(self):
        p = os.path.join(self.tmp.name, "plain")
        write(p, b"old")
        os.chmod(p, stat.S_IREAD)
        sw.write_atomic(p, b"new")
        self.assertEqual(read(p), b"new")
        self.assertFalse([n for n in os.listdir(self.tmp.name) if n.startswith("plain.")])

    def test_other_permission_errors_still_raise(self):
        p = os.path.join(self.tmp.name, "busy")
        write(p, b"old")

        def busy(a, b):
            raise PermissionError(13, "in use")
        with mock.patch.object(sw.os, "replace", busy):
            with self.assertRaises(PermissionError):
                sw.write_atomic(p, b"new")
        self.assertEqual(read(p), b"old")
        self.assertFalse([n for n in os.listdir(self.tmp.name) if n.startswith("busy.")])


if __name__ == "__main__":
    unittest.main()
