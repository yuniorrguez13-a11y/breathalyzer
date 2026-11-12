"""Tests for tools/breath.py (the .breath file: one mod in one file) and the launcher's parts that use it: "Add a
.breath" / "Add this mod?" and "Export as .breath" (LaunchController), and the "Add this mod?" screen and Settings'
frame rate and .breath rows (LauncherView).

Every .breath here is made up in a temp folder. The window tests use a hidden window (root.withdraw(), and the calls
that could show it are switched off): nothing ever shows on the screen. Never the real Savage Wind, mod library,
settings or registry (the file type goes under a made-up HKEY_CURRENT_USER test key that is deleted afterwards)."""
import contextlib
import gc
import hashlib
import io
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import breath as br  # noqa: E402
import launcher as la  # noqa: E402
import modman_gui as gui  # noqa: E402
import test_launcher as tl  # noqa: E402 (its made-up Savage Wind and fake modman; never its window tests)
import test_modman_gui as tm  # noqa: E402

DLL = b"MZ" + b"\0" * 62   # (a made-up DLL: only its name makes it code)


def write(path: Path, data: bytes | str = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


def manifest(files: dict[str, bytes], **kw) -> dict:
    code = sorted(p for p in files if br.is_code(p))
    m = {"format": br.FORMAT, "format_version": br.FORMAT_VERSION, "id": "giruka.test-mod", "title": "Test Mod",
         "version": "1.0", "author": "giruka", "description": "A made-up mod.", "kind": "mod", "package": "",
         "has_code": bool(code), "code": code,
         "files": [{"path": p, "size": len(d), "sha256": hashlib.sha256(d).hexdigest()} for p, d in files.items()],
         "made_with": "tests"}
    m.update(kw)
    return m


def make_breath(path: Path, files: dict[str, bytes], man: dict | None = None, raw: bool = False) -> Path:
    """A made-up .breath: the files and breath.json (man, or the right one for the files)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p, d in files.items():
            zf.writestr(p, d)
        if not raw:
            zf.writestr(br.MANIFEST, json.dumps(manifest(files) if man is None else man))
    return path


GOOD = {"mod/romfs/Actor/Pack/Thing.sbactorpack": b"a made-up actor" * 10, "mod/mod.toml": b'[mod]\nid = "x"\n'}


class Temp(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(prefix="breath-test-")
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)

    def assertRefused(self, path, *words, unpack=True):
        """Refused with plain words; nothing unpacked."""
        dest = self.tmp / "unpacked"
        with self.assertRaises(br.BreathError) as cm:
            br.unpack(str(path), str(dest)) if unpack else br.peek(str(path))
        for w in words:
            self.assertIn(w, str(cm.exception))
        self.assertFalse(dest.exists())
        return str(cm.exception)


# ---------------------------------------------------------------- reading one: every check

class Checks(Temp):
    def test_good_one_unpacks_and_every_hash_is_checked(self):
        path = make_breath(self.tmp / "Test Mod.breath", GOOD)
        man = br.peek(str(path))
        self.assertEqual((man.id, man.title, man.version, man.author, man.kind, man.has_code),
                         ("giruka.test-mod", "Test Mod", "1.0", "giruka", "mod", False))
        rec = br.unpack(str(path), str(self.tmp / "in"))
        self.assertEqual(rec.payload, str(self.tmp / "in" / "mod"))
        for p, d in GOOD.items():
            self.assertEqual((self.tmp / "in" / p).read_bytes(), d)
        self.assertTrue((self.tmp / "in" / br.WAITING).is_file())
        self.assertEqual(rec.code, [])
        br.added(rec)   # (a mod's own files: the library has its copy now)
        self.assertFalse((self.tmp / "in").exists())

    def test_changed_file_is_refused_and_nothing_kept(self):
        files = dict(GOOD)
        bad = dict(GOOD)
        bad["mod/mod.toml"] = b'[mod]\nid = "y"\n'   # (same size, other bytes)
        path = make_breath(self.tmp / "x.breath", bad, man=manifest(files))
        br.peek(str(path))   # (sizes match: only the SHA-256 tells)
        msg = self.assertRefused(path, "doesn't match its SHA-256", "Ask for a new copy")
        self.assertIn("mod.toml", msg)

    def test_list_must_match_what_is_there(self):
        extra = make_breath(self.tmp / "extra.breath", {**GOOD, "mod/romfs/sneaky.bin": b"x"}, man=manifest(GOOD))
        self.assertRefused(extra, "doesn't name", unpack=False)
        missing = make_breath(self.tmp / "missing.breath", {"mod/mod.toml": GOOD["mod/mod.toml"]},
                              man=manifest(GOOD))
        self.assertRefused(missing, "misses files", unpack=False)
        sizes = manifest(GOOD)
        sizes["files"][0]["size"] += 1
        self.assertRefused(make_breath(self.tmp / "size.breath", GOOD, man=sizes), "another size", unpack=False)

    def test_code_must_be_declared(self):
        files = {**GOOD, "mod/hooks.dll": DLL}
        hidden = manifest(files, has_code=False, code=[])
        self.assertRefused(make_breath(self.tmp / "hidden.breath", files, man=hidden), "says it has no code",
                           "hooks.dll", unpack=False)
        wrong = manifest(files, code=[])
        self.assertRefused(make_breath(self.tmp / "wrong.breath", files, man=wrong), "code files", unpack=False)
        rec = br.unpack(str(make_breath(self.tmp / "ok.breath", files)), str(self.tmp / "in"))
        self.assertTrue(rec.manifest.has_code)
        self.assertEqual(rec.code, ["mod/hooks.dll"])
        self.assertEqual(rec.manifest.what(), "Files for the game, with code.")

    def test_unsafe_names_are_refused(self):
        for i, name in enumerate(("mod/../evil.txt", "../evil.txt", "C:/Windows/evil.dll", "/abs/evil.txt",
                                  "mod/CON.txt", "mod/a:b.txt", "mod/x/../../y.txt", "-mod/x.txt")):
            files = {name: b"x"}
            path = make_breath(self.tmp / f"name{i}.breath", files)
            self.assertRefused(path, "Not added", unpack=False)

    def test_links_are_refused(self):
        path = self.tmp / "link.breath"
        man = manifest({"mod/link": b"C:/Windows"})
        with zipfile.ZipFile(path, "w") as zf:
            info = zipfile.ZipInfo("mod/link")
            info.external_attr = 0o120777 << 16
            zf.writestr(info, "C:/Windows")
            zf.writestr(br.MANIFEST, json.dumps(man))
        self.assertRefused(path, "link", unpack=False)

    def test_packed_files_inside_are_refused(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as zf:
            zf.writestr("a.txt", "x")
        files = {**GOOD, "mod/more.zip": inner.getvalue()}
        self.assertRefused(make_breath(self.tmp / "nested.breath", files), "packed file", unpack=False)
        # (a package .breath holds exactly one package, never packed files inside a package folder)
        pk = {"package/Mod/romfs/x.bin": b"x", "package/Mod/inner.7z": b"7z"}
        man = manifest(pk, kind="package", package="wiiu")
        self.assertRefused(make_breath(self.tmp / "pk.breath", pk, man=man), "packed file", unpack=False)
        two = {"package/a.bnp": b"x", "package/b.bnp": b"y"}
        man = manifest(two, kind="package", package="bnp")
        self.assertRefused(make_breath(self.tmp / "two.breath", two, man=man), "more than one package", unpack=False)

    def test_zip_bombs_and_sizes(self):
        zeros = {"mod/romfs/zeros.bin": b"\0" * 200_000}
        path = make_breath(self.tmp / "bomb.breath", zeros)
        with mock.patch.object(br, "MAX_RATIO", 1):
            self.assertRefused(path, "sizes that can't be true", unpack=False)
        with mock.patch.object(br, "MAX_UNPACKED", 100_000):
            self.assertRefused(path, "more than the whole game", unpack=False)
        with mock.patch.object(br, "MAX_MEMBER", 100_000):
            self.assertRefused(path, "bigger than any game file", unpack=False)
        many = {f"mod/romfs/{i}.bin": b"x" for i in range(5)}
        with mock.patch.object(br, "MAX_MEMBERS", 3):
            self.assertRefused(make_breath(self.tmp / "many.breath", many), "too many", unpack=False)
        with mock.patch.object(br, "free_space", return_value=1000):
            self.assertRefused(make_breath(self.tmp / "ok.breath", GOOD), "Not enough free space")

    def test_overlapping_members_are_refused(self):
        """Two members sharing the same bytes (how the worst zip bombs are made)."""
        path = make_breath(self.tmp / "overlap.breath", {"mod/a.bin": b"aaaa", "mod/b.bin": b"bbbb"})
        data = bytearray(path.read_bytes())
        with zipfile.ZipFile(path) as zf:
            start = zf.start_dir
        second = data.index(b"PK\x01\x02", data.index(b"PK\x01\x02", start) + 4)
        data[second + 42:second + 46] = (0).to_bytes(4, "little")   # (b's header is a's)
        path.write_bytes(bytes(data))
        self.assertRefused(path, "overlap", unpack=False)

    def test_not_a_breath(self):
        self.assertRefused(write(self.tmp / "junk.breath", b"not a zip at all" * 10), "isn't a .breath file")
        self.assertRefused(make_breath(self.tmp / "plain.breath", GOOD, raw=True), "no breath.json")
        self.assertRefused(make_breath(self.tmp / "other.breath", GOOD, man={"format": "something"}),
                           "isn't a .breath file made by Breathalyzer")
        newer = manifest(GOOD, format_version=br.FORMAT_VERSION + 1)
        self.assertRefused(make_breath(self.tmp / "newer.breath", GOOD, man=newer), "newer Breathalyzer")
        for bad in ({"id": "../x"}, {"id": "Has Caps"}, {"title": ""}, {"kind": "theme"},
                    {"kind": "package", "package": ""}, {"has_code": "no"}, {"files": []}):
            m = {**manifest(GOOD), **bad}
            self.assertRefused(make_breath(self.tmp / "bad.breath", GOOD, man=m), "Not added", unpack=False)
        self.assertRefused(self.tmp / "gone.breath", "isn't there any more", unpack=False)

    def test_texts_are_cleaned(self):
        m = manifest(GOOD, title="Evil\u202egpj.exe\x07 mod" + "!" * 200, author="a\nb",
                     description="line one\x00\n\n\n\nline two\u200b")
        man = br.peek(str(make_breath(self.tmp / "t.breath", GOOD, man=m)))
        self.assertNotIn("\u202e", man.title)
        self.assertTrue(man.title.startswith("Evil gpj.exe mod"))
        self.assertLessEqual(len(man.title), br.TEXT_MAX["title"])
        self.assertEqual(man.author, "a b")
        self.assertEqual(man.description, "line one\n\nline two")

    def test_package_with_code_inside_is_named(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as zf:
            zf.writestr("exefs/hooks.dll", DLL)
            zf.writestr("romfs/x.bin", "x")
        pk = {"package/Cool.zip": inner.getvalue()}
        man = manifest(pk, kind="package", package="download")
        rec = br.unpack(str(make_breath(self.tmp / "pk.breath", pk, man=man)), str(self.tmp / "in"))
        self.assertEqual(rec.payload, str(self.tmp / "in" / "package" / "Cool.zip"))
        self.assertEqual(rec.code, ["package/Cool.zip/exefs/hooks.dll"])
        self.assertIn("as it came", rec.manifest.what())
        br.added(rec)   # (a package stays: it is the original the library entry names)
        self.assertTrue(Path(rec.payload).is_file())
        self.assertFalse((self.tmp / "in" / br.WAITING).exists())

    def test_sweep_removes_left_over_folders(self):
        received = self.tmp / "received"
        old, new, kept = received / "old", received / "new", received / "kept"
        for d in (old, new):
            write(d / br.WAITING, "x")
            write(d / "mod" / "a.bin", "x")
        write(kept / "package" / "a.bnp", "x")   # (an added package: no .waiting)
        past = time.time() - br.STALE_AFTER - 60
        os.utime(old / br.WAITING, (past, past))
        self.assertEqual(br.sweep(str(received)), [])
        self.assertEqual(sorted(os.listdir(received)), ["kept", "new"])

    def test_names_windows_sees_alike_or_that_hide_what_they_are(self):
        sigma = {"mod/\u03c3.txt": b"a", "mod/\u03c2.txt": b"b"}   # (Greek sigma and final sigma: one name to Windows)
        self.assertRefused(make_breath(self.tmp / "sigma.breath", sigma), "Windows sees as the same name",
                           unpack=False)
        self.assertRefused(make_breath(self.tmp / "com.breath", {"mod/COM\u00b9.txt": b"x"}), "can't use safely",
                           unpack=False)
        flipped = {"mod/photo\u202egnp.dll": DLL}   # (shows as "photolld.png")
        self.assertRefused(make_breath(self.tmp / "flip.breath", flipped), "hidden marks", unpack=False)

    def test_a_file_and_a_folder_with_one_name(self):
        both = {"mod/romfs/x": b"a file", "mod/romfs/x/y.bin": b"in a folder"}
        msg = self.assertRefused(make_breath(self.tmp / "both.breath", both), "a file and a folder with the same name")
        self.assertNotIn("Errno", msg)

    def test_a_long_list_of_files_is_refused_before_it_is_read(self):
        many = {f"mod/romfs/{i}.bin": b"x" for i in range(6)}
        path = make_breath(self.tmp / "many.breath", many)
        with mock.patch.object(br, "MAX_MEMBERS", 3), \
                mock.patch.object(br.zipfile, "ZipFile", side_effect=AssertionError("read the whole list")):
            self.assertRefused(path, "holds 7 files: too many", unpack=False)
        with mock.patch.object(br, "MAX_CD", 100), \
                mock.patch.object(br.zipfile, "ZipFile", side_effect=AssertionError("read the whole list")):
            self.assertRefused(path, "list of files is far too big", unpack=False)

    def test_zip64_counts_are_read_from_the_end_records(self):
        """A Zip64 file says its real counts in its own record: those are what is checked."""
        rec_at = 4
        zip64 = (b"PK\x06\x06" + (44).to_bytes(8, "little") + b"\x2d\x00\x2d\x00" + bytes(8)
                 + (10 ** 6).to_bytes(8, "little") + (10 ** 6).to_bytes(8, "little") + (10 ** 9).to_bytes(8, "little")
                 + bytes(8))
        locator = b"PK\x06\x07" + bytes(4) + rec_at.to_bytes(8, "little") + (1).to_bytes(4, "little")
        end = b"PK\x05\x06" + bytes(4) + b"\xff\xff\xff\xff" + b"\xff\xff\xff\xff" + b"\xff\xff\xff\xff" + bytes(2)
        data = b"junk" + zip64 + locator + end
        self.assertEqual(br.zip_counts(io.BytesIO(data), len(data)), (10 ** 6, 10 ** 9))
        self.assertRefused(write(self.tmp / "z64.breath", data), "holds 1000000 files", unpack=False)

    def test_package_type_must_fit_the_file(self):
        seven = b"7z\xbc\xaf\x27\x1c" + bytes(26)
        for i, (files, package, words) in enumerate((
                ({"package/x.7z": seven}, "ukmm", "is a 7z file"),
                ({"package/x.7z": b"PK\x03\x04" + bytes(26)}, "download", "is a zip file"),
                ({"package/x.rar": b"not packed at all"}, "download", "no packed file at all"),
                ({"package/Mod/romfs/a.bin": b"a"}, "download", "holds a folder"))):
            man = manifest(files, kind="package", package=package)
            self.assertRefused(make_breath(self.tmp / f"t{i}.breath", files, man=man), *words.split("|"),
                               unpack=False)
        inner = io.BytesIO()   # (says BCML, but has no info.json)
        with zipfile.ZipFile(inner, "w") as zf:
            zf.writestr("content/Actor/a.bin", "a")
        files = {"package/Cool.bnp": inner.getvalue()}
        man = manifest(files, kind="package", package="bnp")
        self.assertRefused(make_breath(self.tmp / "nobnp.breath", files, man=man), "has no BCML info.json")

    def test_code_anywhere_in_a_package_is_found(self):
        def zipped(entries: dict) -> bytes:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for n, d in entries.items():
                    zf.writestr(n, d)
            return buf.getvalue()

        nested = zipped({"romfs/x.bin": "x", "extras/inner.zip": zipped({"exefs/hooks.dll": DLL})})
        files = {"package/Cool.zip": nested}
        man = manifest(files, kind="package", package="download")
        rec = br.unpack(str(make_breath(self.tmp / "n.breath", files, man=man)), str(self.tmp / "n"))
        self.assertEqual(rec.code, ["package/Cool.zip/extras/inner.zip/exefs/hooks.dll"])
        self.assertFalse(rec.maybe_code)
        files = {"package/Cool.zip": zipped({"romfs/x.bin": "x", "more.7z": b"7z\xbc\xaf\x27\x1c" + bytes(26)})}
        man = manifest(files, kind="package", package="download")
        rec = br.unpack(str(make_breath(self.tmp / "m.breath", files, man=man)), str(self.tmp / "m"))
        self.assertEqual(rec.code, [])
        self.assertTrue(rec.maybe_code)   # (a .7z inside isn't looked into: "this package may have code")
        # (a .7z package: listed with bnp's own listing, nothing unpacked)
        files = {"package/Cool.7z": b"7z\xbc\xaf\x27\x1c" + bytes(26)}
        man = manifest(files, kind="package", package="download")
        path = make_breath(self.tmp / "s.breath", files, man=man)
        listing = [br.bnp.Member("Cool", 0, True), br.bnp.Member("Cool/exefs/hooks.dll", 64, False),
                   br.bnp.Member("Cool/exefs/fix.pchtxt", 10, False)]
        with mock.patch.object(br.bnp, "list_archive", return_value=(listing, "7z")):
            rec = br.unpack(str(path), str(self.tmp / "s"))
        self.assertEqual(rec.code, ["package/Cool.7z/Cool/exefs/hooks.dll"])
        self.assertEqual(rec.patches, ["package/Cool.7z/Cool/exefs/fix.pchtxt"])
        with mock.patch.object(br.bnp, "list_archive", side_effect=br.bnp.BnpError("no 7-Zip")):
            rec = br.unpack(str(path), str(self.tmp / "s2"))
        self.assertTrue(rec.maybe_code)

    def test_code_patches_and_cheats_are_named(self):
        files = {**GOOD, "mod/exefs/fix.pchtxt": b"@nsobid-0\n", "mod/cheats/0123456789ABCDEF.txt": b"[x]\n"}
        rec = br.unpack(str(make_breath(self.tmp / "p.breath", files)), str(self.tmp / "p"))
        self.assertFalse(rec.manifest.has_code)
        self.assertEqual(sorted(rec.patches), ["mod/cheats/0123456789ABCDEF.txt", "mod/exefs/fix.pchtxt"])

    def test_cancel_keeps_nothing(self):
        dest = self.tmp / "in"
        with self.assertRaises(br.Stopped):
            br.unpack(str(make_breath(self.tmp / "x.breath", GOOD)), str(dest), stop=lambda: True)
        self.assertFalse(dest.exists())

    def test_sweep_removes_packages_no_mod_names_any_more(self):
        received = self.tmp / "received"
        used, gone = received / "used", received / "gone"
        for d in (used, gone):
            write(d / "package" / "a.bnp", "x")
            past = time.time() - 3600
            os.utime(d, (past, past))
        in_use = [str(used / "package" / "a.bnp"), "C:\\Elsewhere\\mod"]
        self.assertEqual(br.sweep(str(received)), [])   # (without the library's list: nothing of that kind goes)
        self.assertEqual(sorted(os.listdir(received)), ["gone", "used"])
        self.assertEqual(br.sweep(str(received), in_use=in_use), [])
        self.assertEqual(os.listdir(received), ["used"])

    def test_hidden_marks_in_texts(self):
        m = manifest(GOOD, title="Co\u200dol\U000E0041 Mod\u00ad", author="gi\u2060ruka\u061c")
        man = br.peek(str(make_breath(self.tmp / "t.breath", GOOD, man=m)))
        self.assertEqual((man.title, man.author), ("Co ol Mod", "gi ruka"))
        self.assertEqual(br.shown_name("mod/a\u202eb.dll"), "a b.dll")
        self.assertEqual(len(br.shown_name("x" * 500)), br.MAX_SHOWN_NAME)

    def test_command_line(self):
        path = make_breath(self.tmp / "x.breath", {**GOOD, "mod/hooks.dll": DLL})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(br.main(["info", str(path)]), 0)
        self.assertIn("Test Mod 1.0 (giruka.test-mod)", out.getvalue())
        self.assertIn("Code: mod/hooks.dll", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(br.main(["info", str(write(self.tmp / "bad.breath", "x"))]), 1)
        self.assertIn("Error: Not added", out.getvalue())


# ---------------------------------------------------------------- making one

class Export(Temp):
    def setUp(self):
        super().setUp()
        self.lib = self.tmp / "lib"
        self.out = self.tmp / "Desktop"

    def mod(self, mod_id: str, files: dict[str, bytes | str]) -> Path:
        folder = self.lib / "mods" / mod_id
        for p, d in files.items():
            write(folder / p, d)
        return folder

    def test_own_mod_round_trip(self):
        self.mod("giruka.cool", {"mod.toml": '[mod]\nid = "giruka.cool"\nname = "Cool"\nversion = "2.1"\n'
                                             'description = "Makes things cool."\nauthors = ["giruka"]\n',
                                 "romfs/Actor/a.bin": b"a" * 5000, "hooks.dll": DLL, ".modman/state": "x"})
        entry = {"id": "giruka.cool", "name": "Cool", "enabled": True, "kinds": ["romfs", "native"]}
        res = br.export(str(self.lib), entry, str(self.out / "Cool"))
        self.assertEqual(res.path, str(self.out / "Cool.breath"))
        man = res.manifest
        self.assertEqual((man.id, man.title, man.version, man.author, man.description, man.kind, man.has_code),
                         ("giruka.cool", "Cool", "2.1", "giruka", "Makes things cool.", "mod", True))
        self.assertEqual(man.code, ["mod/hooks.dll"])
        with zipfile.ZipFile(res.path) as zf:
            self.assertEqual(sorted(zf.namelist()), ["breath.json", "mod/hooks.dll", "mod/mod.toml",
                                                     "mod/romfs/Actor/a.bin"])   # (no .modman: never dot folders)
        rec = br.unpack(res.path, str(self.tmp / "in"))
        self.assertEqual((Path(rec.payload) / "romfs" / "Actor" / "a.bin").read_bytes(), b"a" * 5000)
        self.assertEqual(br.peek(res.path).to_json(), rec.manifest.to_json())

    def test_package_mod_exports_its_original_package(self):
        self.mod("cool-bnp", {"romfs/Pack/Merged.pack": b"merged with the player's own game files",
                              ".bnp/info.json": json.dumps({"desc": "A BCML mod.", "author": "Someone"})})
        src = self.tmp / "Downloads" / "Cool Mod.bnp"
        with zipfile.ZipFile(write(src, b""), "w") as zf:
            zf.writestr("info.json", "{}")
            zf.writestr("content/Actor/a.bin", "a")
        entry = {"id": "cool-bnp", "name": "Cool Mod", "enabled": True, "kinds": ["romfs"], "source": str(src),
                 "bnp": {"options": []}, "version": "3"}
        res = br.export(str(self.lib), entry, str(self.out / "Cool Mod.breath"))
        with zipfile.ZipFile(res.path) as zf:
            self.assertEqual(sorted(zf.namelist()), ["breath.json", "package/Cool Mod.bnp"])
        man = res.manifest
        self.assertEqual((man.kind, man.package, man.description, man.author, man.version),
                         ("package", "bnp", "A BCML mod.", "Someone", "3"))
        rec = br.unpack(res.path, str(self.tmp / "in"))
        self.assertEqual(Path(rec.payload).read_bytes(), src.read_bytes())
        src.unlink()
        with self.assertRaises(br.BreathError) as cm:
            br.export(str(self.lib), entry, str(self.out / "again.breath"))
        self.assertIn("isn't there any more", str(cm.exception))

    def test_plain_mod_exports_the_files_it_came_from(self):
        """The library's copy can hold files filled in from the player's game (a whole Pack/*.pack): the mod's own
        files are exported, and without them such a copy is refused."""
        self.mod("titles", {"romfs/Pack/TitleBG.pack": b"filled from the game", "romfs/a.bin": b"a"})
        src = self.tmp / "Downloads" / "Titles"
        write(src / "romfs" / "Pack" / "TitleBG.pack", b"only the mod's own")
        write(src / "romfs" / "a.bin", b"a")
        entry = {"id": "titles", "name": "Titles", "enabled": True, "kinds": ["romfs"], "source": str(src)}
        res = br.export(str(self.lib), entry, str(self.out / "Titles.breath"))
        with zipfile.ZipFile(res.path) as zf:
            self.assertEqual(zf.read("mod/romfs/Pack/TitleBG.pack"), b"only the mod's own")
        shutil.rmtree(src)
        with self.assertRaises(br.BreathError) as cm:
            br.export(str(self.lib), entry, str(self.out / "Titles2.breath"))
        self.assertIn("may hold files from your game", str(cm.exception))
        self.assertFalse((self.out / "Titles2.breath").exists())
        # (without a whole pack, the library's copy is the mod's own files)
        self.mod("plain", {"romfs/Actor/b.bin": b"b"})
        entry = {"id": "plain", "name": "Plain", "enabled": True, "kinds": ["romfs"], "source": str(src)}
        res = br.export(str(self.lib), entry, str(self.out / "Plain.breath"))
        self.assertEqual([f.path for f in res.manifest.files], ["mod/romfs/Actor/b.bin"])

    def test_plain_mod_from_a_download(self):
        self.mod("dl", {"romfs/a.bin": b"a"})
        src = self.tmp / "Downloads" / "Mod.zip"
        with zipfile.ZipFile(write(src, b""), "w") as zf:
            zf.writestr("Mod/romfs/a.bin", "a")
        entry = {"id": "dl", "name": "Downloaded", "enabled": True, "kinds": ["romfs"], "source": str(src)}
        res = br.export(str(self.lib), entry, str(self.out / "dl.breath"))
        self.assertEqual((res.manifest.kind, res.manifest.package), ("package", "download"))
        self.assertEqual([f.path for f in res.manifest.files], ["package/Mod.zip"])

    def test_mods_made_from_the_game_are_never_exported(self):
        self.mod("giruka.effects", {"romfs/a.bin": b"from the game"})
        entry = {"id": "giruka.effects", "name": "Effects off", "enabled": True, "kinds": ["romfs"]}
        with self.assertRaises(br.BreathError) as cm:
            br.export(str(self.lib), entry, str(self.out / "e.breath"), recipes={"giruka.effects"})
        self.assertIn("made from your own game files", str(cm.exception))
        self.mod("built", {"romfs/a.bin": b"x", "built.txt": "built by quality.py"})
        with self.assertRaises(br.BreathError):
            br.export(str(self.lib), {"id": "built", "name": "Built"}, str(self.out / "b.breath"))
        self.assertFalse(self.out.exists())

    def test_where_it_may_go(self):
        self.mod("m", {"romfs/a.bin": b"a"})
        with self.assertRaises(br.BreathError) as cm:
            br.export(str(self.lib), {"id": "m", "name": "M"}, str(self.lib / "mods" / "m.breath"))
        self.assertIn("somewhere else", str(cm.exception))
        with self.assertRaises(br.BreathError):
            br.export(str(self.lib), {"id": "gone", "name": "Gone"}, str(self.out / "g.breath"))
        with self.assertRaises(br.BreathError):
            br.export(str(self.lib), {"id": "../m", "name": "Odd"}, str(self.out / "o.breath"))

    def test_plain_mod_exports_only_what_was_added(self):
        """Files put in the folder a mod was added from since (or anything else in it) never go in: only the library
        copy's files, each from that folder while it is there. What goes in is listed before anything is written."""
        self.mod("m", {"romfs/Actor/a.bin": b"library copy"})
        src = self.tmp / "Downloads"
        write(src / "romfs" / "Actor" / "a.bin", b"the mod's own")
        write(src / "secret.txt", b"private")
        write(src / "Photos" / "me.jpg", b"private")
        entry = {"id": "m", "name": "M", "enabled": True, "kinds": ["romfs"], "source": str(src)}
        plan = br.plan_export(str(self.lib), entry, str(self.out / "M.breath"))
        self.assertFalse(self.out.exists())   # (nothing written yet)
        self.assertEqual(plan.summary(), "1 file, 1 KB: romfs.")
        res = br.write_export(plan)
        with zipfile.ZipFile(res.path) as zf:
            self.assertEqual(sorted(zf.namelist()), ["breath.json", "mod/romfs/Actor/a.bin"])
            self.assertEqual(zf.read("mod/romfs/Actor/a.bin"), b"the mod's own")
        # (added from a romfs folder itself: modman put "romfs" above its files)
        self.mod("r", {"romfs/Actor/b.bin": b"library copy"})
        write(self.tmp / "Mod" / "romfs" / "Actor" / "b.bin", b"the mod's own b")
        entry = {"id": "r", "name": "R", "enabled": True, "kinds": ["romfs"], "source": str(self.tmp / "Mod" / "romfs")}
        res = br.export(str(self.lib), entry, str(self.out / "R.breath"))
        with zipfile.ZipFile(res.path) as zf:
            self.assertEqual(zf.read("mod/romfs/Actor/b.bin"), b"the mod's own b")

    def test_where_windows_wont_save_it(self):
        self.mod("m", {"romfs/a.bin": b"a"})
        with mock.patch.object(br.os, "replace", side_effect=PermissionError(13, "Access is denied")):
            with self.assertRaises(br.BreathError) as cm:
                br.export(str(self.lib), {"id": "m", "name": "M"}, str(self.out / "m.breath"))
        self.assertIn("pick another folder, like your Desktop", str(cm.exception))
        self.assertEqual([p.name for p in self.out.iterdir()], [])   # (no half-written file left)

    def test_packed_files_are_left_out(self):
        self.mod("m", {"romfs/a.bin": b"a", "extras.zip": b"PK"})
        res = br.export(str(self.lib), {"id": "m", "name": "M"}, str(self.out / "m.breath"))
        self.assertEqual([f.path for f in res.manifest.files], ["mod/romfs/a.bin"])
        self.assertTrue(any("extras.zip" in n for n in res.notes))


# ---------------------------------------------------------------- the launcher: add one, make one

class BreathModman(tl.LaunchModman):
    """The fake modman, with add --name (what "Add this mod" runs)."""

    def __init__(self, mods=tm.MODS, library=None):
        super().__init__(mods)
        self.added = []
        self.removed = []
        self.library = library

    def cmd_add(self, path, *opts):
        named = [o.split("=", 1)[1] for o in opts if o.startswith("--name=")]
        assert "--name" not in opts, opts   # (always --name=<title> in one piece: a title can start with "-")
        name = named[0] if named else os.path.basename(path)
        self.added.append((path, name, os.path.exists(path)))
        mod_id = name.lower().replace(" ", "-")
        self.mods.append({"id": mod_id, "name": name, "enabled": True, "kinds": ["romfs"], "source": path})
        if self.library is not None and os.path.isdir(path):   # (the library copy, as modman makes it)
            shutil.copytree(path, os.path.join(self.library, "mods", mod_id))
        print(f"Added #{len(self.mods)} {mod_id} (romfs): {name}")
        return 0

    def cmd_remove(self, ref):
        k = next((i for i, m in enumerate(self.mods) if m["id"] == ref), None)
        self.removed.append(ref)
        return super().cmd_remove(str(k + 1) if k is not None else ref)


class Controller(Temp):
    def make(self, mods=tm.MODS, lib=None):
        self.mm = BreathModman(mods, library=lib)
        self.mm.deployed = self.mm.inputs_hash(None)
        self.spawn = tm.FakeSpawn(self.mm)
        backend = gui.LaunchBackend(lib, "Z:\\SW", mm=self.mm, spawn=self.spawn, python="py",
                                    running=lambda: False, menu=False, popen=mock.Mock())
        self.ctl = gui.LaunchController(backend, clock=tm.Clock())
        self.ctl.refresh_state()
        self.assertTrue(self.ctl.settle())
        return self.ctl

    def test_add_this_mod(self):
        ctl = self.make()
        received = self.tmp / "received"
        ctl.open_breath(str(make_breath(self.tmp / "Test Mod.breath", GOOD)), str(received))
        self.assertEqual(ctl.status.lines[0], "Checking Test Mod.breath...")
        self.assertFalse(ctl.can("add"))
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines, ["Add this mod?", "Test Mod"])
        self.assertEqual(ctl.breath.manifest.title, "Test Mod")
        ctl.add_breath()
        self.assertTrue(ctl.settle())
        self.assertEqual(len(self.mm.added), 1)
        path, name, existed = self.mm.added[0]
        self.assertEqual((os.path.basename(path), name, existed), ("mod", "Test Mod", True))
        self.assertEqual(os.listdir(received), [])   # (added: the unpacked copy is gone)
        self.assertEqual(ctl.listing.rows[-1].name, "Test Mod")

    def test_not_now_keeps_nothing(self):
        ctl = self.make()
        received = self.tmp / "received"
        ctl.open_breath(str(make_breath(self.tmp / "Test Mod.breath", GOOD)), str(received))
        self.assertTrue(ctl.settle())
        ctl.drop_breath()
        self.assertEqual(ctl.status.lines[0], "Not added.")
        self.assertEqual(os.listdir(received), [])
        self.assertEqual(self.mm.added, [])

    def test_a_bad_one_is_refused_in_plain_words(self):
        ctl = self.make()
        bad = dict(GOOD)
        bad["mod/mod.toml"] = b'[mod]\nid = "z"\n'
        ctl.open_breath(str(make_breath(self.tmp / "Bad.breath", bad, man=manifest(GOOD))), str(self.tmp / "r"))
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.kind, "error")
        self.assertEqual(ctl.status.lines[0], "Bad.breath wasn't added.")
        self.assertIn("SHA-256", ctl.status.lines[1])
        self.assertIsNone(ctl.breath)
        self.assertEqual(os.listdir(self.tmp / "r"), [])
        self.assertTrue(ctl.can("add"))

    def test_a_title_starting_with_a_dash(self):
        ctl = self.make()
        man = manifest(GOOD, title="-rf Mod")
        ctl.open_breath(str(make_breath(self.tmp / "dash.breath", GOOD, man=man)), str(self.tmp / "r"))
        self.assertTrue(ctl.settle())
        ctl.add_breath()
        self.assertTrue(ctl.settle())
        self.assertEqual([n for _, n, _ in self.mm.added], ["-rf Mod"])
        self.assertEqual(ctl.status.kind, "ok")

    def test_cancel_while_checking(self):
        ctl = self.make()

        def slow(path, dest, spare=None, stop=None):
            end = time.monotonic() + 5
            while not stop() and time.monotonic() < end:
                time.sleep(0.01)
            raise br.Stopped(br.STOPPED)

        with mock.patch.object(br, "unpack", side_effect=slow):
            ctl.open_breath(str(make_breath(self.tmp / "Big.breath", GOOD)), str(self.tmp / "r"))
            self.assertIn("Cancel stops it.", ctl.status.lines[1])
            self.assertTrue(ctl.can("cancel"))
            ctl.cancel()
            self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.lines, ["Stopped.", "Big.breath wasn't added. Nothing of it was kept."])
        self.assertIsNone(ctl.breath)

    def test_nothing_is_added_or_made_while_the_game_is_open(self):
        ctl = self.make()
        ctl.game_open = True
        for action in ("add", "export", "breath"):
            self.assertFalse(ctl.can(action), action)
        ctl.open_breath(str(make_breath(self.tmp / "x.breath", GOOD)), str(self.tmp / "r"))
        self.assertEqual(ctl.status.lines, ["The game is open.", "Close it first."])
        self.assertNotIn("main", ctl.jobs)
        self.assertFalse((self.tmp / "r").exists())

    def test_code_it_did_not_show_is_taken_out_again(self):
        """A backstop: a DLL in the mod's library copy that "Add this mod?" didn't name (a package can unpack into
        more than its listing said) is taken out again at once."""
        lib = self.tmp / "lib"
        (lib / "mods").mkdir(parents=True)
        ctl = self.make(lib=str(lib))
        ctl.open_breath(str(make_breath(self.tmp / "Test Mod.breath", GOOD)), str(self.tmp / "r"))
        self.assertTrue(ctl.settle())
        write(Path(ctl.breath.payload) / "exefs" / "sneaky.dll", DLL)   # (as if modman found more inside)
        ctl.add_breath()
        self.assertTrue(ctl.settle())
        self.assertEqual(self.mm.removed, ["test-mod"])
        self.assertNotIn("test-mod", [r.id for r in ctl.listing.rows])
        self.assertEqual(ctl.status.kind, "error")
        self.assertEqual(ctl.status.lines[:2], ["Test Mod wasn't added: it had code it didn't show (sneaky.dll).",
                                                "It was taken out again."])

    def test_export(self):
        lib = self.tmp / "lib"
        write(lib / "mods" / "a" / "romfs" / "a.bin", b"alpha")
        ctl = self.make(lib=str(lib))
        dest = self.tmp / "Desktop" / "Alpha.breath"
        ctl.export_breath("a", str(dest))
        self.assertTrue(ctl.settle())
        self.assertEqual(ctl.status.kind, "ok")
        self.assertEqual(ctl.status.lines[0], "Made Alpha.breath.")
        self.assertEqual(br.peek(str(dest)).title, "Alpha")
        ctl.export_breath("a", str(lib / "inside.breath"))
        self.assertTrue(ctl.settle())
        self.assertEqual((ctl.status.kind, ctl.status.lines[0]), ("error", "The .breath file wasn't made."))


# ---------------------------------------------------------------- the window, hidden

def hidden_root():
    """A Tk root that never shows: withdrawn before anything can draw it, see-through, and the calls that would
    bring it up (deiconify, lift, focus_force) do nothing."""
    if gui.tk is None:
        raise unittest.SkipTest("no tkinter")
    try:
        root = gui.tk.Tk()
    except gui.tk.TclError as e:
        raise unittest.SkipTest(f"no display: {e}")
    root.withdraw()
    with contextlib.suppress(gui.tk.TclError):
        root.attributes("-alpha", 0.0)
    for name in ("deiconify", "wm_deiconify", "lift", "tkraise", "focus_force", "iconify", "wm_iconify"):
        setattr(root, name, lambda *a, **k: None)
    return root


def texts(widget) -> list[str]:
    out = []
    for w in widget.winfo_children():
        with contextlib.suppress(gui.tk.TclError, AttributeError):
            t = w.cget("text")
            if t:
                out.append(str(t))
        out += texts(w)
    return out


class HiddenWindow(Temp):
    """LauncherView in a window that never shows."""

    def open(self, ini: str | None = "frame_rate = 30\n", running=False):
        root = hidden_root()
        self.sw = tl.make_install(self.tmp / "SW")
        if ini is not None:
            write(self.sw / la.INI, ini)
        self.mm = BreathModman()
        self.mm.deployed = self.mm.inputs_hash(None)
        self.spawn = tm.FakeSpawn(self.mm)
        self.running = [running]
        self.base = f"Software\\BreathalyzerTest-{secrets.token_hex(4)}\\Classes"
        self.addCleanup(delete_test_key, self.base)

        def backend(lib, install):
            self.mm.installs.add(install)
            return gui.LaunchBackend(lib, install, mm=self.mm, spawn=self.spawn, python="py", running=lambda: False,
                                     menu=False, popen=mock.Mock())

        settings = la.Settings(install=str(self.sw))
        first = la.FirstRun(settings, places=lambda: {"near": []},
                            check=lambda f, lib, why: tl.check(f, lib or str(self.tmp / "lib"), why, tmp=self.tmp))
        try:
            view = gui.LauncherView(root, settings, str(self.tmp / "home" / la.SETTINGS_FILE), first_run=first,
                                    make_backend=backend, reg_base=self.base, running=lambda: self.running[0])
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

    def test_frame_rate_dropdown(self):
        crlf = "# Savage Wind settings\r\nfullscreen = 0\r\nframe_rate = 30\r\nvsync = fast\r\n"
        root, view = self.open(ini=crlf)
        view.show_settings()
        root.update()
        self.assertEqual(view.fps_box.get(), "30 fps")
        self.assertEqual(list(view.fps_box.cget("values")), ["30 fps", "60 fps", "90 fps", "120 fps", "No limit"])
        self.assertEqual(str(view.fps_box.cget("state")), "readonly")
        view.on_fps("No limit")
        root.update()
        ini = self.sw / la.INI
        self.assertEqual(ini.read_bytes(), crlf.replace("frame_rate = 30", "frame_rate = off").encode())
        self.assertEqual((self.sw / la.INI_BACKUP).read_bytes(), crlf.encode())
        self.assertEqual(view.fps_box.get(), "No limit")
        self.assertIn("No limit. Used from your next start.", texts(view.frames["settings"]))
        self.assertEqual(root.state(), "withdrawn")

    def test_frame_rate_waits_for_the_game_to_close(self):
        root, view = self.open(running=True)
        view.show_settings()
        root.update()
        self.assertEqual(str(view.fps_box.cget("state")), "disabled")
        self.assertIn("Close the game to change it.", texts(view.frames["settings"]))
        view.on_fps("60 fps")
        self.assertEqual((self.sw / la.INI).read_text(), "frame_rate = 30\n")
        self.assertEqual(view.ask.call_args[0][1], "The frame rate wasn't changed.")
        self.assertFalse((self.sw / la.INI_BACKUP).exists())

    def test_frame_rate_without_settings_file(self):
        root, view = self.open(ini=None)
        view.show_settings()
        root.update()
        self.assertIn("Start Savage Wind once first.", texts(view.frames["settings"]))
        self.assertEqual(str(view.fps_box.cget("state")), "disabled")

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_register_and_unregister_buttons(self):
        before = real_breath_type()
        root, view = self.open()
        view.show_settings()
        self.assertIn("Register .breath", [b.cget("text") for b in view.settings_buttons])
        with mock.patch.object(la, "shell_changed") as changed:
            view.on_register()
            self.assertEqual(la.breath_state(self.base, la.breath_command(view.app_dir)[0]), "here")
            self.assertIn("Unregister .breath", [b.cget("text") for b in view.settings_buttons])
            self.assertEqual(view.ask.call_args[0][1], "Done.")
            view.on_unregister()
            self.assertIsNone(la.breath_state(self.base))
            self.assertIn("Register .breath", [b.cget("text") for b in view.settings_buttons])
        changed.assert_not_called()   # (only the real key tells Explorer)
        self.assertEqual(real_breath_type(), before)   # (the real file types are never touched by tests)

    def test_add_this_mod_screen_warns_about_code(self):
        root, view = self.open()
        files = {**GOOD, "mod/hooks.dll": DLL}
        path = make_breath(self.tmp / "Code Mod.breath", files, man=manifest(files, title="Code Mod"))
        view.open_breath(str(path))
        self.assertTrue(view.ctl.settle())
        view.render()
        root.update()
        self.assertEqual(view.screen, "breath")
        shown = texts(view.frames["breath"])
        self.assertIn("Add this mod?", shown)
        self.assertIn("Code Mod", shown)
        self.assertIn("By giruka. Version 1.0.", shown)
        self.assertIn("A made-up mod.", [t.replace("\n", " ") for t in shown])   # (cut into lines at its width)
        self.assertIn("Careful: this mod has code (hooks.dll).", shown)
        self.assertEqual([b.cget("text") for b in view.breath_buttons], ["Add this mod", "Not now"])
        self.assertEqual(view.breath_focus, 1)   # (Not now first, for a mod with code)
        view.back()   # (Esc: Not now)
        root.update()
        self.assertEqual(view.screen, "home")
        self.assertEqual(os.listdir(view.received_dir), [])
        self.assertEqual(self.mm.added, [])
        self.assertEqual(root.state(), "withdrawn")

    def test_add_this_mod_adds_it(self):
        root, view = self.open()
        view.open_breath(str(make_breath(self.tmp / "Test Mod.breath", GOOD)))
        self.assertTrue(view.ctl.settle())
        view.render()
        self.assertEqual(view.screen, "breath")
        self.assertNotIn("Careful", " ".join(texts(view.frames["breath"])))
        self.assertEqual(view.breath_focus, 0)
        view.breath_buttons[0].invoke()
        self.assertTrue(view.ctl.settle())
        view.render()
        root.update()
        self.assertEqual(view.screen, "home")
        self.assertEqual([n for _, n, _ in self.mm.added], ["Test Mod"])
        self.assertEqual(view.ctl.listing.rows[-1].name, "Test Mod")
        self.assertEqual(len(view.tree.get_children()), 4)

    def test_a_double_click_while_open_is_handed_over(self):
        root, view = self.open()
        path = make_breath(self.tmp / "Test Mod.breath", GOOD)
        la.pass_file(str(path), view.open_dir)   # (what a second start does)
        for _ in range(gui.OPEN_POLL_TICKS * 2):
            if not os.listdir(view.open_dir):
                break
            view.tick()
        self.assertTrue(view.ctl.settle())
        view.render()
        self.assertEqual(view.screen, "breath")
        self.assertEqual(os.listdir(view.open_dir), [])
        self.assertEqual(root.state(), "withdrawn")

    def test_a_breath_waits_while_you_play(self):
        """Hidden while you play (minimized): a .breath handed over then is never checked, unpacked or shown, and
        the window never comes up for it; once the game closes the window comes back and asks."""
        root, view = self.open()
        view.settings.hide_while_playing = True
        for name in ("deiconify", "lift", "iconify", "focus_force"):
            setattr(root, name, mock.Mock())
        view.ctl.watch = mock.Mock()   # (the game "runs" until the test says it closed)
        view.ctl.game_open = True
        view.tick()
        root.iconify.assert_called_once()
        self.assertTrue(view.hidden_for_play)
        path = make_breath(self.tmp / "Test Mod.breath", GOOD)
        with mock.patch.object(root, "state", return_value="iconic"):
            la.pass_file(str(path), view.open_dir)   # (a double-click while you play)
            for _ in range(gui.OPEN_POLL_TICKS * 3):
                view.tick()
            view.open_breath(str(path))   # (or Add a .breath, had it been pressed before)
            self.assertEqual(view.pending_breath, [str(path), str(path)])
            self.assertNotIn("main", view.ctl.jobs)
            self.assertFalse(os.path.isdir(view.received_dir) and os.listdir(view.received_dir))
            root.deiconify.assert_not_called()
            root.lift.assert_not_called()
            root.focus_force.assert_not_called()
        view.ctl.game_open = False   # (the game closed: the window comes back, then asks)
        view.tick()
        root.deiconify.assert_called_once()
        self.assertTrue(view.ctl.settle())
        view.render()
        self.assertEqual(view.screen, "breath")

    def test_the_mouse_wheel_never_changes_the_frame_rate(self):
        root, view = self.open(ini="frame_rate = 30\n")
        view.show_settings()
        root.update()
        box = view.fps_box
        self.assertEqual(box.bindtags()[0], str(box))   # (its own bindings first: they stop the dropdown's)
        for seq in gui.WHEEL_EVENTS:
            self.assertIn("break", box.bind(seq), seq)
        for delta in (-120, 120, -120):
            box.event_generate("<MouseWheel>", delta=delta, when="now")
        root.update()
        self.assertEqual((self.sw / la.INI).read_text(), "frame_rate = 30\n")
        self.assertFalse((self.sw / la.INI_BACKUP).exists())

    def test_settings_file_in_another_format_is_left_alone(self):
        root, view = self.open(ini=None)
        data = "frame_rate = 30\r\n".encode("utf-16")
        write(self.sw / la.INI, data)
        view.show_settings()
        root.update()
        self.assertIn(la.INI_OTHER_FORMAT, texts(view.frames["settings"]))
        self.assertEqual(str(view.fps_box.cget("state")), "disabled")
        view.on_fps("60 fps")
        self.assertEqual((self.sw / la.INI).read_bytes(), data)
        self.assertEqual(view.ask.call_args[0][2], la.INI_OTHER_FORMAT)

    def test_a_long_description_never_hides_the_warning_or_the_buttons(self):
        root, view = self.open()
        view.set_size(gui.MAX_SIZE)   # (the biggest text that fits on this screen)
        files = {**GOOD, "mod/hooks.dll": DLL, "mod/exefs/fix.pchtxt": b"@nsobid-0\n"}
        long = ("A" * 300 + " " + "Lots of words. " * 200)[:2000]
        path = make_breath(self.tmp / "Long.breath", files, man=manifest(files, title="Long", description=long))
        view.open_breath(str(path))
        self.assertTrue(view.ctl.settle())
        view.render()
        root.update()
        self.assertEqual(view.screen, "breath")
        body = view.breath_body
        row_of = {}
        for w in body.winfo_children():
            for text in [str(w.cget("text"))] if isinstance(w, gui.tk.Label) else texts(w):
                row_of[text] = int(w.grid_info()["row"])
        careful = next(r for t, r in row_of.items() if t.startswith("Careful: this mod has code (hooks.dll)"))
        patches = next(r for t, r in row_of.items() if t.startswith("This mod changes the game's code (fix.pchtxt)"))
        desc_label, full = view.breath_desc
        self.assertEqual(full, long)
        self.assertLess(careful, int(desc_label.grid_info()["row"]))   # (right under the title)
        self.assertLess(patches, int(desc_label.grid_info()["row"]))
        shown = desc_label.cget("text")
        self.assertLessEqual(len(shown.split("\n")), gui.BREATH_LINES)
        self.assertTrue(shown.endswith("..."))
        room_w, room_h = view.room()
        width = min(room_w, view.font.measure("0") * 72)
        self.assertLessEqual(view.need(width), room_h, f"{view.size} pt")   # (all of it fits: warning and buttons)
        self.assertEqual([b.cget("text") for b in view.breath_buttons], ["Add this mod", "Not now"])
        self.assertIn("Not damaged. This doesn't mean it is safe: only add mods from people you trust.",
                      " ".join(texts(view.frames["breath"])))

    def test_a_package_that_may_have_code_warns(self):
        root, view = self.open()
        files = {"package/Cool.7z": b"7z\xbc\xaf\x27\x1c" + bytes(26)}
        path = make_breath(self.tmp / "Cool.breath", files, man=manifest(files, kind="package", package="download"))
        with mock.patch.object(br.bnp, "list_archive", side_effect=br.bnp.BnpError("no 7-Zip")):
            view.open_breath(str(path))
            self.assertTrue(view.ctl.settle())
        view.render()
        self.assertIn("Careful: this package may have code.", texts(view.frames["breath"]))
        self.assertEqual(view.breath_focus, 1)   # (Not now first)

    def test_export_button(self):
        root, view = self.open()
        lib = Path(view.ctl.backend.root)
        self.assertTrue(br._inside(str(lib), str(self.tmp)), lib)   # (never a real library)
        write(lib / "mods" / "a" / "romfs" / "a.bin", b"alpha")
        view.tree.selection_set("a")
        dest = self.tmp / "Desktop" / "Alpha.breath"
        with mock.patch.object(gui.filedialog, "asksaveasfilename", return_value=str(dest)) as save:
            view.on_export()   # (asks first, showing what goes in: Cancel writes nothing)
        self.assertEqual(save.call_args.kwargs["initialfile"], "Alpha.breath")
        self.assertEqual(view.ask.call_args[0][1], "Make Alpha.breath?")
        self.assertIn("1 file, 1 KB: romfs.", view.ask.call_args[0][2])
        self.assertTrue(view.ctl.settle())
        self.assertFalse(dest.exists())
        self.assertEqual(view.ctl.status.lines[0], "Not made.")
        view.ask.return_value = True   # (Make it)
        with mock.patch.object(gui.filedialog, "asksaveasfilename", return_value=str(dest)):
            view.on_export()
        self.assertTrue(view.ctl.settle())
        self.assertEqual(br.peek(str(dest)).title, "Alpha")
        self.assertIn("Export as .breath", [b.cget("text") for b in view.by_action.values()])
        self.assertIn("Add a .breath", [b.cget("text") for b in view.by_action.values()])


def delete_test_key(base: str) -> None:
    """Remove the made-up HKEY_CURRENT_USER\\Software\\BreathalyzerTest-<x> key a test used."""
    if sys.platform != "win32":
        return
    import winreg
    top = base.split("\\Classes")[0]
    assert top.startswith("Software\\BreathalyzerTest-"), top
    with contextlib.suppress(OSError):
        la._delete_tree(winreg, winreg.HKEY_CURRENT_USER, top)


def real_breath_type():
    """What the real HKEY_CURRENT_USER\\Software\\Classes\\.breath says (only read)."""
    if sys.platform != "win32":
        return None
    import winreg
    return la._read_default(winreg, winreg.HKEY_CURRENT_USER, f"{la.CLASSES}\\{la.BREATH_EXT}")


if __name__ == "__main__":
    unittest.main()
