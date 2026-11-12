"""Tests for tools/packaging/ (the player zips' runtime and starter). Made-up files in temp folders only; nothing is
compiled with zig, copied from a Python install or downloaded here."""
import hashlib
import importlib.util
import json
import marshal
import struct
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGING = REPO / "tools" / "packaging"
sys.path.insert(0, str(REPO / "tools"))
import make_release as mr  # noqa: E402


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"packaging_{name}", PACKAGING / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = load("build_starter")
br = load("build_runtime")

ICO = (struct.pack("<HHH", 0, 1, 2) + struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 32, 4, 38)
       + struct.pack("<BBBBHHII", 32, 32, 0, 0, 1, 32, 5, 42) + b"aaaa" + b"bbbbb")


def res_entries(data: bytes):
    """(type, name, data) for every entry of a .res file (numeric types and names only)."""
    out, at = [], 0
    while at < len(data):
        size, head = struct.unpack_from("<II", data, at)
        ff1, rtype, ff2, name = struct.unpack_from("<HHHH", data, at + 8)
        assert (ff1, ff2) == (0xFFFF, 0xFFFF)
        out.append((rtype, name, data[at + head:at + head + size]))
        at += head + size + (-size % 4)
    return out


class Resources(unittest.TestCase):
    def test_res_file_holds_icon_version_and_manifest(self):
        entries = res_entries(bs.make_res(ICO))
        self.assertEqual(entries[0], (0, 0, b""))  # (a .res file starts with an empty entry)
        kinds = [(t, n) for t, n, _ in entries[1:]]
        self.assertEqual(kinds, [(bs.RT_ICON, 1), (bs.RT_ICON, 2), (bs.RT_GROUP_ICON, 1), (bs.RT_VERSION, 1),
                                 (bs.RT_MANIFEST, 1)])
        self.assertEqual(entries[1][2], b"aaaa")
        self.assertEqual(entries[2][2], b"bbbbb")
        group = entries[3][2]
        self.assertEqual(struct.unpack_from("<HHH", group), (0, 1, 2))
        self.assertEqual(struct.unpack_from("<H", group, 6 + 12)[0], 1)       # first picture: icon 1
        self.assertEqual(struct.unpack_from("<H", group, 6 + 14 + 12)[0], 2)  # second: icon 2
        manifest = entries[5][2].decode()
        self.assertIn('level="asInvoker"', manifest)
        self.assertNotIn("requireAdministrator", manifest)

    def test_version_info_layout(self):
        info = bs.version_info({"ProductName": "Breathalyzer", "FileVersion": "1.0.0.0"})
        self.assertEqual(struct.unpack_from("<H", info)[0], len(info))
        self.assertEqual(struct.unpack_from("<H", info, 2)[0], 52)  # VS_FIXEDFILEINFO
        key = "VS_VERSION_INFO\0".encode("utf-16-le")
        self.assertEqual(info[6:6 + len(key)], key)
        fixed = 6 + len(key) + (-(6 + len(key)) % 4)
        self.assertEqual(struct.unpack_from("<I", info, fixed)[0], 0xFEEF04BD)
        self.assertIn("Breathalyzer\0".encode("utf-16-le"), info)
        self.assertIn("040904B0".encode("utf-16-le"), info)

    def test_bad_icons(self):
        for data in (b"", b"not an icon", struct.pack("<HHH", 0, 1, 1) + b"\0" * 16):
            with self.assertRaises(bs.StarterError):
                bs.ico_images(data)

    def test_real_icon_and_starter_source(self):
        self.assertTrue(bs.ico_images(bs.ICON.read_bytes()))
        src = bs.SOURCE.read_text(encoding="utf-8")
        self.assertIn("launcher.py", src)
        self.assertIn("modman_gui.py", src)
        self.assertNotIn("#include <windows.h>", src)  # (Savage Wind's zig has no windows.h)
        for word in ("socket", "Internet", "Reg", "http"):  # no network, no registry
            self.assertNotIn(word, src.split("*/", 1)[1])

    def test_lock_matches_this_repo(self):
        if not bs.LOCK.is_file():
            self.skipTest("the starter isn't built here")
        lock = bs.read_lock()
        self.assertEqual(lock["sources"], bs.source_hashes(), "start.c, the icon or build_starter.py changed since "
                                                              "the starter was built: run build_starter.py")


class Runtime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, rel, data=b"x = 1\n"):
        p = self.base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def test_stdlib_leaves_out_tests_pip_and_site_packages(self):
        for rel in ("Lib/os.py", "Lib/json/__init__.py", "Lib/tkinter/ttk.py", "Lib/test/test_os.py",
                    "Lib/site-packages/pkg/__init__.py", "Lib/idlelib/x.py", "Lib/ensurepip/__init__.py",
                    "Lib/__pycache__/os.cpython-313.pyc", "Lib/json/__pycache__/x.pyc", "Lib/README.txt"):
            self.put(rel)
        names = [n for n, _ in br.stdlib_modules(self.base / "Lib")]
        self.assertEqual(names, ["json/__init__.py", "os.py", "tkinter/ttk.py"])

    def test_pyc_names_no_folder_of_this_pc(self):
        data = br.pyc(b"def f():\n    return 1\n", "json/__init__.py")
        self.assertEqual(data[:4], importlib.util.MAGIC_NUMBER)
        self.assertEqual(int.from_bytes(data[4:8], "little"), 1)  # hash-based, unchecked: no file dates
        code = marshal.loads(data[16:])
        self.assertEqual(code.co_filename, "json/__init__.py")
        self.assertEqual(br.pyc(b"x = 1\n", "a.py"), br.pyc(b"x = 1\n", "a.py"))
        self.put("Lib/a.py", b"x = 1\n")
        self.put("Lib/b/c.py", b"y = 2\n")
        z1 = br.stdlib_zip(self.base / "Lib")
        self.assertEqual(z1, br.stdlib_zip(self.base / "Lib"))  # (the same files, the same zip)
        self.assertNotIn(str(self.base).encode(), z1)

    def test_tcl_files_skip_time_zones_demos_and_libs(self):
        for rel in ("tcl/tcl8.6/init.tcl", "tcl/tcl8.6/tzdata/Europe/Paris", "tcl/tk8.6/tk.tcl",
                    "tcl/tk8.6/demos/x.tcl", "tcl/tk8.6/images/logo.gif", "tcl/tcl8/8.5/msgcat-1.6.1.tm",
                    "tcl/tcl86t.lib", "tcl/nmake/x.vc", "tcl/tcl8.6/x.lib"):
            self.put(rel)
        names = [n for n, _ in br.tcl_files(self.base / "tcl")]
        self.assertEqual(sorted(names), ["tcl/tcl8.6/init.tcl", "tcl/tcl8/8.5/msgcat-1.6.1.tm", "tcl/tk8.6/tk.tcl"])

    def test_signers(self):
        psf = "CN=Python Software Foundation, O=Python Software Foundation, L=Beaverton, S=Oregon, C=US"
        ms = "CN=Microsoft Windows Software Compatibility Publisher, O=Microsoft Corporation, L=Redmond, C=US"
        sigs = {"python.exe": ("Valid", psf), "vcruntime140.dll": ("Valid", ms), "tk86t.dll": ("NotSigned", ""),
                "evil.dll": ("Valid", "CN=Someone, O=Someone Else"), "fake.pyd": ("Valid", "CN=x, O=Python Software "
                                                                               "Foundation Fan Club")}
        bad = " ".join(br.signer_problems(sigs, sorted(sigs) + ["gone.pyd"]))
        for name in ("tk86t.dll", "evil.dll", "fake.pyd", "gone.pyd"):
            self.assertIn(name, bad)
        for name in ("python.exe", "vcruntime140.dll"):
            self.assertNotIn(name, bad)
        self.assertEqual(br.signer_name(psf), "Python Software Foundation")

    def test_pth_keeps_it_apart(self):
        self.assertNotIn("import site", br.PTH_LINES)
        self.assertEqual(mr.runtime_shape_problems({"python.exe": b"", "pythonw.exe": b"", "LICENSE.txt": b"",
                                                    "python313.zip": b"",
                                                    "python313._pth": "\n".join(br.PTH_LINES).encode()})
                         .__len__(), 2)  # (only the two made-up empty exes, which aren't Windows programs)

    def test_lock_round_trip(self):
        rt = self.base / "runtime"
        self.put("runtime/python.exe", b"made up")
        self.put("runtime/tcl/tcl8.6/init.tcl", b"# made up\n")
        lock = br.make_lock(rt, {"python.exe": "Python Software Foundation"}, "OK made up")
        self.assertEqual(sorted(lock["files"]), ["python.exe", "tcl/tcl8.6/init.tcl"])
        self.assertEqual(lock["files"]["python.exe"]["sha256"], hashlib.sha256(b"made up").hexdigest())
        self.assertNotIn(str(self.base), json.dumps(lock))
        self.assertEqual(lock["licences"], {})  # (only the licence files that are there)
        files, bad = mr.runtime_files(rt, lock)
        self.assertEqual(bad, [])
        self.put("runtime/python.exe", b"changed")
        _, bad = mr.runtime_files(rt, lock)
        self.assertIn("differs", dict(bad)["python.exe"])

    def test_real_lock_has_no_paths_of_this_pc(self):
        if not br.LOCK.is_file():
            self.skipTest("the runtime isn't built here")
        text = br.LOCK.read_text(encoding="utf-8")
        lock = json.loads(text)
        self.assertEqual(lock["format"], 1)
        self.assertEqual(mr.leak_problems(text.encode(), mr.leak_terms(REPO)), [])
        for need in ("python.exe", "pythonw.exe", "LICENSE.txt", "_tkinter.pyd", "tcl/tk8.6/license.terms"):
            self.assertIn(need, lock["files"])
        self.assertFalse([n for n in lock["files"] if "site-packages" in n or "/test/" in n or "tzdata" in n])
        for name in lock["files"]:
            if name.endswith((".exe", ".dll", ".pyd")):
                self.assertIn(lock["signed"].get(name), ("Python Software Foundation",
                                                         "Microsoft Windows Software Compatibility Publisher"), name)


if __name__ == "__main__":
    unittest.main()
