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

    def test_packed_files_are_left_out(self):
        self.mod("m", {"romfs/a.bin": b"a", "extras.zip": b"PK"})
        res = br.export(str(self.lib), {"id": "m", "name": "M"}, str(self.out / "m.breath"))
        self.assertEqual([f.path for f in res.manifest.files], ["mod/romfs/a.bin"])
        self.assertTrue(any("extras.zip" in n for n in res.notes))


# ---------------------------------------------------------------- the launcher: add one, make one

class BreathModman(tl.LaunchModman):
    """The fake modman, with add --name (what "Add this mod" runs)."""

    def __init__(self, mods=tm.MODS):
        super().__init__(mods)
        self.added = []

    def cmd_add(self, path, *opts):
        name = opts[1] if opts[:1] == ("--name",) else os.path.basename(path)
        self.added.append((path, name, os.path.exists(path)))
        mod_id = name.lower().replace(" ", "-")
        self.mods.append({"id": mod_id, "name": name, "enabled": True, "kinds": ["romfs"]})
        print(f"Added #{len(self.mods)} {mod_id} (romfs): {name}")
        return 0


class Controller(Temp):
    def make(self, mods=tm.MODS, lib=None):
        self.mm = BreathModman(mods)
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
        self.assertIn("A made-up mod.", shown)
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

    def test_export_button(self):
        root, view = self.open()
        lib = Path(view.ctl.backend.root)
        self.assertTrue(br._inside(str(lib), str(self.tmp)), lib)   # (never a real library)
        write(lib / "mods" / "a" / "romfs" / "a.bin", b"alpha")
        view.tree.selection_set("a")
        dest = self.tmp / "Desktop" / "Alpha.breath"
        with mock.patch.object(gui.filedialog, "asksaveasfilename", return_value=str(dest)) as save:
            view.on_export()
        self.assertEqual(save.call_args.kwargs["initialfile"], "Alpha.breath")
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
