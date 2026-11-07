"""Tests for tools/modman.py. Every install, mod, game file and DLL here is made up, in temp folders: no game is
started (Popen is mocked), no real rebuild runs (run_rebuild is replaced), and the real Savage Wind folder is never
touched."""
import contextlib
import dataclasses
import hashlib
import io
import json
import os
import stat
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_hooks as gh  # noqa: E402
import modman as mm  # noqa: E402
import nso  # noqa: E402
import wiiu_convert as wc  # noqa: E402
from test_build_native import make_pe  # noqa: E402
from test_nso import make_nso  # noqa: E402

BID = "cd57b23fa4bbad65"                       # (the first 8 bytes only, in a made-up module)
MAIN_ID = bytes.fromhex(BID) + bytes(24)
SDK_ID = bytes.fromhex("1122334455667788") + bytes(24)
SEGS = [(0x0, bytes(0x1000)), (0x1000, b"\x55" * 0x200), (0x2000, b"\x66" * 0x100)]
DATA_PATCH = f"@nsobid-{BID}\n@flag offset_shift 0x100\n// Data\n@enabled\n00001010 41424344\n"
DATA_PATCH_2 = f"@nsobid-{BID}\n@flag offset_shift 0x100\n// Data 2\n@enabled\n00001010 45464748\n"
CODE_PATCH = f"@nsobid-{BID}\n@flag offset_shift 0x100\n// Code\n@enabled\n00000010 1F2003D5\n"
CODE_PATCH_2 = f"@nsobid-{BID}\n@flag offset_shift 0x100\n// Code 2\n@enabled\n00000020 1F2003D5\n"
CHEATS_A = ("{Master}\n580F0000 01E13ED0\n\n[Infinite Stamina]\n04000000 00001234 00000064\n\n"
            "[Fast]\n04000000 00001238 00000001\n")
CHEATS_B = "{Master}\n580F0000 01E13ED8\n\n[Other]\n04000000 00001300 00000001\n"
CHEATS_C = "[Infinite Stamina]\n04000000 00001234 00000064\n\n[Fast]\n04000000 00001240 00000002\n"
CHEAT_PATH = f"atmosphere/contents/01007EF00011E000/cheats/{BID}.txt"
FAR_PATCH = f"@nsobid-{BID}\n@flag offset_shift 0x100\n// Far\n@enabled\n00100000 41424344\n"   # past the end
GAP_PATCH = f"@nsobid-{BID}\n@flag offset_shift 0x100\n// Gap\n@enabled\n00001500 41424344\n"   # between segments


def read_only(p) -> bool:
    return not os.stat(p).st_mode & stat.S_IWRITE


def make_pe32(**kw) -> bytes:
    """make_pe, marked as a 32-bit (i386) DLL."""
    data = bytearray(make_pe(**kw))
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    struct.pack_into("<H", data, pe + 4, 0x14C)
    return bytes(data)


def write(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return root


def make_zip(path: Path, files: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
    return path


def mark_zip(path: Path, *, flag: int = 0, method: int | None = None) -> Path:
    """Set a flag bit (1: password-protected) or the compression method (9: Deflate64) of every member of a stored
    .zip, in its local and central headers. The data stays as it was."""
    data = bytearray(path.read_bytes())
    for sig, at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = data.find(sig)
        while i >= 0:
            data[i + at] |= flag
            if method is not None:
                data[i + at + 2:i + at + 4] = method.to_bytes(2, "little")
            i = data.find(sig, i + 4)
    path.write_bytes(bytes(data))
    return path


def fake_install(inst: Path) -> Path:
    return write(inst, {
        "savagewind.exe": b"MZ made-up exe\0test-id\0",
        "extracted/exefs/main": make_nso(MAIN_ID, SEGS),
        "extracted/exefs/sdk": make_nso(SDK_ID, SEGS),
        "extracted/exefs/main.npdm": b"made-up npdm",
        "extracted/romfs/Actor/Pack/Hero.txt": "game actor",
        "extracted/romfs/Pack/Title.txt": "game title",
        "extracted/romfs/System/Version.txt": "made up",
        "extracted/aoc1/Map/MainField/Dlc.txt": "game dlc",
        "extracted/swgame.dll": b"made-up game code",
        "extracted/swgame.dll.id": "test-id\n",
    })


def quiet(fn, *a, **kw):
    """Run fn with its printing captured: (result, text)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **kw)
    return r, buf.getvalue()


class FakeProc:
    started: list = []

    def __init__(self, argv, **kw):
        self.argv, self.kw = list(argv), kw
        self.pid = 4000 + len(FakeProc.started)
        self.returncode = None
        self.terminated = False
        FakeProc.started.append(self)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 1

    def kill(self):
        self.terminate()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="modman-test-"))
        self.addCleanup(lambda: mm.remove_tree(str(self.tmp)))  # (never follows the junctions deploy makes)
        self.install = fake_install(self.tmp / "SavageWind" / "Savage Wind")
        self.libdir = self.tmp / "SavageWind" / "modloader"
        self.lib = mm.Library(str(self.libdir), str(self.install))
        self.lib.save()
        self.dl = self.tmp / "downloads"
        self.host_dir = self.tmp / "build-native"
        self.rebuilds = []
        self.rebuild_hooks = []   # (per rebuild: the addresses of its --hooks file, or None without one)
        patches = [mock.patch.object(mm, "game_running", return_value=False),
                   mock.patch.object(mm, "run_rebuild", side_effect=self.fake_rebuild),
                   mock.patch.object(mm, "MODHOST_DIR", str(self.host_dir)),
                   mock.patch.dict(os.environ, {"USERPROFILE": str(self.tmp / "home")})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop(mm.ENV_LIBRARY, None)
        self.extracted = self.install / "extracted"
        self.deploy_dir = self.libdir / "deploy"

    def fake_rebuild(self, install, exefs, out, hooks=None):
        self.rebuilds.append((install, exefs, out))
        self.rebuild_hooks.append(gh.read_hooks_files(hooks) if hooks else None)
        Path(out).write_bytes(b"made-up rebuilt game code")
        Path(out + ".id").write_text("test-id\n")
        return 0

    @property
    def lib(self):  # (read config.json each time: CLI calls in a test change it)
        return mm.Library.load(str(self.libdir))

    @lib.setter
    def lib(self, value):
        value.save()

    def mod(self, name, files, *, zip_it=False, **kw):
        src = make_zip(self.dl / f"{name}.zip", files) if zip_it else write(self.dl / name, files)
        entry, scan, notes = quiet(mm.add_mod, self.lib, str(src), **kw)[0]
        return entry

    def deploy(self, **kw):
        launch, out = quiet(mm.deploy, self.lib, **kw)
        return launch, out

    def build_host(self):
        write(self.host_dir, {"modhost.dll": make_pe(exports=("swgame_tables",))})


# ---------------------------------------------------------------- library, config, add

class LibraryPlace(Base):
    def test_default_library_is_next_to_the_install(self):
        self.assertEqual(mm.library_for(str(self.install)), str(self.libdir))
        self.assertEqual(mm.resolve_library(None, str(self.install)), str(self.libdir))
        with mock.patch.dict(os.environ, {mm.ENV_LIBRARY: str(self.tmp / "elsewhere")}):
            self.assertEqual(mm.resolve_library(None, str(self.install)), str(self.tmp / "elsewhere"))
            self.assertEqual(mm.resolve_library(str(self.tmp / "arg")), str(self.tmp / "arg"))

    def test_init_detects_the_install_and_keeps_mods(self):
        home_install = fake_install(self.tmp / "home" / "Documents" / "SavageWind" / "Savage Wind")
        code, out = quiet(mm.main, ["init"])
        self.assertEqual(code, 0, out)
        lib = mm.Library.load(str(home_install.parent / "modloader"))
        self.assertEqual(lib.install, str(home_install))
        # --library before the command is kept (argparse sub-parser defaults don't reset it)
        code, out = quiet(mm.main, ["--library", str(self.libdir), "list"])
        self.assertIn("No mods yet", out)

    def test_bad_ids_in_config_are_refused(self):
        cfg = json.loads((self.libdir / "config.json").read_text())
        cfg["mods"] = [{"id": "../outside", "name": "x", "enabled": True, "kinds": []}]
        (self.libdir / "config.json").write_text(json.dumps(cfg))
        with self.assertRaises(mm.ModmanError):
            mm.Library.load(str(self.libdir))


class Add(Base):
    def test_romfs_folder_is_stored(self):
        e = self.mod("Better Sprint", {"romfs/Actor/Pack/Hero.txt": "mod hero"})
        self.assertEqual((e["id"], e["kinds"], e["enabled"]), ("better-sprint", ["romfs"], True))
        stored = self.libdir / "mods" / "better-sprint" / "romfs" / "Actor" / "Pack" / "Hero.txt"
        self.assertEqual(stored.read_text(), "mod hero")  # (a small file: the library's own copy, see LibrarySnapshot)
        self.assertFalse(os.path.samefile(stored, self.dl / "Better Sprint" / "romfs" / "Actor" / "Pack" / "Hero.txt"))
        e2 = self.mod("Better Sprint", {"romfs/Pack/Title.txt": "x"})
        self.assertEqual(e2["id"], "better-sprint-2")
        self.assertEqual([m["id"] for m in mm.Library.load(str(self.libdir)).mods], ["better-sprint", "better-sprint-2"])

    def test_zip_with_dlc(self):
        e = self.mod("Dlc Mod", {"Wrap/01007EF00011F001/romfs/Map/MainField/New.txt": "dlc",
                                 "Wrap/01007EF00011E000/romfs/Pack/Extra.txt": "base"}, zip_it=True)
        self.assertEqual(e["kinds"], ["romfs", "dlc-romfs"])

    def test_folder_name_keeps_its_meaning(self):
        # ...\01007EF00011F001\romfs passed directly: DLC files, kept under that title id in the library
        src = write(self.dl / "Thing" / "01007EF00011F001" / "romfs", {"Map/MainField/New.txt": "dlc"})
        e, _, _ = quiet(mm.add_mod, self.lib, str(src))[0]
        self.assertEqual((e["name"], e["kinds"]), ("Thing", ["dlc-romfs"]))
        self.assertTrue((self.libdir / "mods" / "thing" / "01007EF00011F001" / "romfs" / "Map").is_dir())
        self.assertEqual(mm.slug("01007EF00011F001"), "mod-01007ef00011f001")

    def test_zip_slip_is_refused_and_nothing_is_written(self):
        for evil in ("../evil.txt", "romfs/../../evil.txt", "/abs/evil.txt", "C:/evil.txt"):
            with self.subTest(evil=evil):
                z = make_zip(self.dl / "evil.zip", {"romfs/Pack/A.txt": "a", evil: "gotcha"})
                with self.assertRaises(mm.ModmanError) as cm:
                    quiet(mm.add_mod, self.lib, str(z))
                self.assertIn("unsafe path", str(cm.exception))
                self.assertFalse((self.tmp / "evil.txt").exists())
                self.assertFalse((self.libdir / "evil.txt").exists())
                self.assertEqual(os.listdir(self.libdir / "mods"), [])
        self.assertEqual(mm.Library.load(str(self.libdir)).mods, [])

    def test_mods_that_cant_be_used_are_refused_with_the_reason(self):
        cases = {
            "cemu": ({"Pack/rules.txt": "[Definition]\ntitleIds = 00050000101C9400\nname = x\n",
                      "Pack/patches.txt": "[x]\n"}, "Cemu"),
            "old cheats": ({"atmosphere/contents/01007EF00011E000/cheats/de0b6ac4efc1dda7.txt":
                            "[X]\n04000000 00000010 00000001\n"}, "1.8.2"),
            "old patch": ({"exefs/p.pchtxt": "@nsobid-0123456789abcdef\n@enabled\n00000110 00\n"}, "porting"),
            "helper dll": ({"x.dll": make_pe(exports=("something_else",))}, "nothing Savage Wind can use"),
            "readme": ({"readme.md": "hi"}, "doesn't look like a BotW mod"),
        }
        for name, (files, why) in cases.items():
            with self.subTest(name=name), self.assertRaises(mm.ModmanError) as cm:
                self.mod(name, files)
            self.assertIn(why, str(cm.exception))
        self.assertEqual(mm.Library.load(str(self.libdir)).mods, [])
        self.assertEqual([n for n in os.listdir(self.libdir / "mods")], [])  # (no staging folder left)

    def test_kinds_of_patches_cheats_and_native(self):
        self.assertEqual(self.mod("Data", {"exefs/data.pchtxt": DATA_PATCH})["kinds"], ["exefs-patch"])
        self.assertEqual(self.mod("Cheats", {CHEAT_PATH: CHEATS_A})["kinds"], ["cheats"])
        self.assertEqual(self.mod("Native", {"hello.dll": make_pe(exports=("swmod_init",))})["kinds"], ["native"])
        mixed = self.mod("Mixed", {"romfs/Pack/Title.txt": "t", "exefs/d.pchtxt": DATA_PATCH,
                                   "exefs/old.pchtxt": "@nsobid-0123456789abcdef\n@enabled\n00000110 00\n"})
        self.assertEqual(mixed["kinds"], ["romfs", "exefs-patch"])
        self.assertTrue(any("old.pchtxt" in p for p in mixed["problems"]))

    def test_single_files(self):
        p = write(self.dl, {"one.pchtxt": DATA_PATCH, f"{BID}.txt": CHEATS_A, "notes.txt": "x"})
        e, _, _ = quiet(mm.add_mod, self.lib, str(p / "one.pchtxt"))[0]
        self.assertEqual(e["kinds"], ["exefs-patch"])
        e, _, _ = quiet(mm.add_mod, self.lib, str(p / f"{BID}.txt"))[0]
        self.assertEqual(e["kinds"], ["cheats"])
        with self.assertRaises(mm.ModmanError):
            quiet(mm.add_mod, self.lib, str(p / "notes.txt"))

    def test_game_files_and_library_are_not_mods(self):
        for src in (self.extracted / "romfs", self.libdir):
            with self.subTest(src=src), self.assertRaises(mm.ModmanError):
                quiet(mm.add_mod, self.lib, str(src))


class ListOrderRemove(Base):
    def test_enable_disable_move(self):
        for n in ("A", "B", "C"):
            self.mod(n, {"romfs/Pack/Title.txt": n})
        lib = str(self.libdir)
        quiet(mm.main, ["disable", "2", "--library", lib])
        quiet(mm.main, ["move", "c", "1", "--library", lib])
        code, out = quiet(mm.main, ["list", "--library", lib])
        self.assertEqual(code, 0)
        loaded = mm.Library.load(lib)
        self.assertEqual([(m["id"], m["enabled"]) for m in loaded.mods], [("c", True), ("a", True), ("b", False)])
        self.assertLess(out.index(" c "), out.index(" a "))
        quiet(mm.main, ["enable", "b", "--library", lib])
        self.assertTrue(mm.Library.load(lib).mods[2]["enabled"])
        code, out = quiet(mm.main, ["enable", "9", "--library", lib])
        self.assertEqual(code, 2)

    def test_remove_deletes_only_inside_the_library(self):
        self.mod("A", {"romfs/Pack/Title.txt": "a"})
        precious = write(self.tmp / "precious", {"keep.txt": "mine"})
        mm.make_junction(str(self.libdir / "mods" / "a" / "link"), str(precious))
        quiet(mm.main, ["remove", "a", "--library", str(self.libdir)])
        self.assertFalse((self.libdir / "mods" / "a").exists())
        self.assertEqual((precious / "keep.txt").read_text(), "mine")
        self.assertTrue((self.dl / "A" / "romfs" / "Pack" / "Title.txt").is_file())  # (the source stays)
        self.assertEqual(mm.Library.load(str(self.libdir)).mods, [])


# ---------------------------------------------------------------- deploy

class Deploy(Base):
    def launch_file(self):
        return json.loads((self.deploy_dir / "launch.json").read_text(encoding="utf-8"))

    def test_romfs_later_mod_wins_and_order_matters(self):
        self.mod("A", {"romfs/Pack/Title.txt": "A title", "romfs/Actor/Pack/Hero.txt": "A hero"})
        self.mod("B", {"romfs/Pack/Title.txt": "B title"})
        launch, out = self.deploy()
        self.assertEqual((self.deploy_dir / "overlay" / "Pack" / "Title.txt").read_text(), "B title")
        self.assertEqual((self.deploy_dir / "overlay" / "Actor" / "Pack" / "Hero.txt").read_text(), "A hero")
        self.assertEqual(launch["argv"], [str(self.install / "savagewind.exe")])
        self.assertEqual(launch["env"], {"SAVAGEWIND_ROMFS_OVERLAY": str(self.deploy_dir / "overlay")})
        self.assertEqual(launch["cwd"], str(self.install))
        self.assertIn("later mod wins", " ".join(launch["attention"]))
        self.assertEqual(self.launch_file()["env"], launch["env"])
        quiet(mm.main, ["move", "b", "1", "--library", str(self.libdir)])
        launch, out = self.deploy()
        self.assertEqual((self.deploy_dir / "overlay" / "Pack" / "Title.txt").read_text(), "A title")
        self.assertIn("order changed", mm.format_deploy(launch))
        self.assertEqual(self.rebuilds, [])

    def test_data_patch_needs_no_rebuild(self):
        self.mod("Data", {"exefs/data.pchtxt": DATA_PATCH})
        vanilla = (self.extracted / "exefs" / "main").read_bytes()
        launch, _ = self.deploy()
        root = self.deploy_dir / "root"
        self.assertEqual(self.rebuilds, [])
        self.assertEqual(launch["argv"], [str(self.install / "savagewind.exe"), str(root), "--play"])
        self.assertEqual(launch["env"], {"SAVAGEWIND_GAME_LIB": str(self.extracted / "swgame.dll")})
        self.assertEqual(launch["parts"]["exefs"]["rebuild"], "none")
        self.assertEqual(nso.load(str(root / "exefs" / "main")).read(0x1010, 4), b"ABCD")
        self.assertEqual((self.extracted / "exefs" / "main").read_bytes(), vanilla)  # (the game's own: untouched)
        self.assertTrue((root / "exefs" / "sdk").is_file() and (root / "exefs" / "main.npdm").is_file())
        self.assertTrue(os.path.isjunction(root / "romfs"))
        self.assertTrue(os.path.samefile(root / "romfs", self.extracted / "romfs"))
        self.assertTrue(os.path.samefile(root / "aoc1", self.extracted / "aoc1"))

    def test_code_patch_rebuilds_once(self):
        self.mod("Code", {"exefs/code.pchtxt": CODE_PATCH})
        launch, out = self.deploy()
        out_lib = str(self.deploy_dir / "swgame.dll")
        self.assertEqual(self.rebuilds, [(str(self.install), str(self.deploy_dir / "root" / "exefs"), out_lib)])
        self.assertIn("10-20 minutes", out)
        self.assertEqual(launch["env"], {"SAVAGEWIND_GAME_LIB": out_lib})
        self.assertEqual(launch["parts"]["exefs"]["rebuild"], "build")
        launch, _ = self.deploy()  # same patches: the rebuilt game code is kept
        self.assertEqual(len(self.rebuilds), 1)
        self.assertEqual((launch["parts"]["exefs"]["rebuild"], launch["env"]["SAVAGEWIND_GAME_LIB"]), ("reuse", out_lib))
        self.mod("Code 2", {"exefs/code2.pchtxt": CODE_PATCH_2})
        self.deploy()
        self.assertEqual(len(self.rebuilds), 2)
        quiet(mm.main, ["disable", "code", "--library", str(self.libdir)])
        quiet(mm.main, ["disable", "code-2", "--library", str(self.libdir)])
        launch, _ = self.deploy()  # no code patches any more: the rebuilt library goes
        self.assertFalse(Path(out_lib).exists())
        self.assertEqual(launch["env"], {})

    def test_no_rebuild_leaves_code_patches_pending(self):
        self.mod("Code", {"exefs/code.pchtxt": CODE_PATCH})
        launch, _ = self.deploy(no_rebuild=True)
        self.assertEqual(self.rebuilds, [])
        self.assertEqual(launch["parts"]["exefs"]["rebuild"], "pending")
        self.assertEqual(launch["env"], {"SAVAGEWIND_GAME_LIB": str(self.extracted / "swgame.dll")})
        self.assertIn("--no-rebuild", " ".join(launch["attention"]))

    def test_rebuild_failure_stops_the_deploy(self):
        self.mod("Code", {"exefs/code.pchtxt": CODE_PATCH})
        with mock.patch.object(mm, "run_rebuild", return_value=1), self.assertRaises(mm.ModmanError):
            self.deploy()
        self.assertFalse((self.deploy_dir / "launch.json").exists())
        with self.assertRaises(mm.ModmanError):
            mm.read_launch(self.lib)

    def test_native_mods_through_modhost(self):
        self.build_host()
        self.mod("Hello", {"hello.dll": make_pe(exports=("swmod_init",))})
        launch, _ = self.deploy()
        nat = self.deploy_dir / "native"
        self.assertEqual(launch["env"], {"SAVAGEWIND_GAME_LIB": str(nat / "modhost.dll"),
                                         "SWMOD_REAL_GAME_LIB": str(self.extracted / "swgame.dll"),
                                         "SWMOD_DIR": str(nat / "mods")})
        self.assertEqual((nat / "modhost.dll.id").read_text(), "test-id\n")
        self.assertTrue((nat / "mods" / "hello.dll").is_file())
        self.assertEqual(launch["argv"], [str(self.install / "savagewind.exe")])

    def test_native_and_code_patch_use_the_rebuilt_library(self):
        self.build_host()
        self.mod("Hello", {"hello.dll": make_pe(exports=("swmod_init",))})
        self.mod("Code", {"exefs/code.pchtxt": CODE_PATCH})
        launch, _ = self.deploy()
        self.assertEqual(launch["env"]["SWMOD_REAL_GAME_LIB"], str(self.deploy_dir / "swgame.dll"))
        self.assertEqual(launch["env"]["SAVAGEWIND_GAME_LIB"], str(self.deploy_dir / "native" / "modhost.dll"))
        self.assertEqual(launch["argv"][1:], [str(self.deploy_dir / "root"), "--play"])

    def test_native_without_modhost_says_what_to_do(self):
        self.mod("Hello", {"hello.dll": make_pe(exports=("swmod_init",))})
        launch, _ = self.deploy()
        self.assertEqual(launch["env"], {})
        self.assertIn("build_native.py build", " ".join(launch["attention"]))

    def test_cheats_are_merged(self):
        self.mod("A", {CHEAT_PATH: CHEATS_A})
        self.mod("C", {CHEAT_PATH: CHEATS_C})
        launch, _ = self.deploy()
        text = (self.deploy_dir / "cheats" / f"{BID}.txt").read_text(encoding="utf-8")
        import cheats as ch
        found, _ = ch.parse_text(text)
        # (C's "Infinite Stamina" has the same code as A's, but A's runs after A's master code and C's doesn't)
        self.assertEqual([c.name for c in found if not c.master],
                         ["Infinite Stamina", "Fast", "Infinite Stamina (C)", "Fast (C)"])
        self.assertEqual([(c.group_name, c.master) for c in found],
                         [("A", True), ("A", False), ("A", False), ("C", False), ("C", False)])
        self.assertIn("C: cheat 'Infinite Stamina' renamed 'Infinite Stamina (C)' (another mod has the same one "
                      "with another master code)", launch["notes"])
        self.assertEqual(launch["cheats"]["args"], ["--all"])
        self.assertEqual(launch["cheats"]["count"], 4)
        self.assertEqual(launch["env"], {"SAVAGEWIND_CONTROL": "7777"})

    def test_cheats_with_another_master_code_get_their_own_group(self):
        self.mod("A", {CHEAT_PATH: CHEATS_A})
        self.mod("B", {CHEAT_PATH: CHEATS_B})
        launch, _ = self.deploy()
        text = (self.deploy_dir / "cheats" / f"{BID}.txt").read_text(encoding="utf-8")
        import cheats as ch
        found, _ = ch.parse_text(text)
        self.assertEqual([(c.group_name, c.name, c.error) for c in found if c.master],
                         [("A", "Master", None), ("B", "Master", None)])
        self.assertEqual([c.name for c in found if c.group_name == "B"], ["Master", "Other"])
        self.assertEqual(launch["attention"], [])
        self.assertEqual(launch["cheats"]["count"], 3)

    def test_cheat_toggles_choose_what_runs(self):
        folder = CHEAT_PATH.rsplit("/", 1)[0]
        self.mod("A", {CHEAT_PATH: CHEATS_A, f"{folder}/toggles.txt": "[Fast]\ntrue\n[Infinite Stamina]\nfalse\n"})
        launch, _ = self.deploy()
        self.assertEqual(launch["cheats"]["args"], ["--enable=Fast"])
        self.assertIn("1 turned on", " ".join(mm.describe_parts(launch)))

    def test_nothing_enabled(self):
        self.mod("A", {"romfs/Pack/Title.txt": "a"})
        self.deploy()
        quiet(mm.main, ["disable", "a", "--library", str(self.libdir)])
        launch, out = self.deploy()
        self.assertEqual((launch["argv"], launch["env"]), ([str(self.install / "savagewind.exe")], {}))
        self.assertFalse((self.deploy_dir / "overlay").exists())
        self.assertIn("- A", mm.format_deploy(launch))

    def test_all_kinds_together(self):
        self.build_host()
        self.mod("Romfs", {"romfs/Pack/Title.txt": "t"})
        self.mod("Data", {"exefs/data.pchtxt": DATA_PATCH})
        self.mod("Cheats", {CHEAT_PATH: CHEATS_A})
        self.mod("Hello", {"hello.dll": make_pe(exports=("swmod_init",))})
        launch, _ = self.deploy()
        self.assertEqual(set(launch["env"]), {"SAVAGEWIND_ROMFS_OVERLAY", "SAVAGEWIND_GAME_LIB", "SWMOD_REAL_GAME_LIB",
                                              "SWMOD_DIR", "SAVAGEWIND_CONTROL"})
        self.assertEqual(launch["env"]["SWMOD_REAL_GAME_LIB"], str(self.extracted / "swgame.dll"))
        self.assertEqual(len(mm.describe_parts(launch)), 4)
        # (the mod folder is named "Romfs" and holds romfs/: the file must land where the game looks for it)
        self.assertEqual((self.deploy_dir / "overlay" / "Pack" / "Title.txt").read_text(), "t")
        self.assertEqual(launch["parts"]["overlay"]["replaced"], 1)
        self.assertNotIn("doesn't have", " ".join(launch["attention"]))

    def test_romfs_and_code_patch(self):
        self.mod("Romfs", {"romfs/Pack/Title.txt": "t"})
        self.mod("Code", {"exefs/code.pchtxt": CODE_PATCH})
        launch, _ = self.deploy()
        self.assertEqual(launch["env"], {"SAVAGEWIND_ROMFS_OVERLAY": str(self.deploy_dir / "overlay"),
                                         "SAVAGEWIND_GAME_LIB": str(self.deploy_dir / "swgame.dll")})
        self.assertEqual(launch["argv"][1:], [str(self.deploy_dir / "root"), "--play"])
        self.assertEqual((self.deploy_dir / "overlay" / "Pack" / "Title.txt").read_text(), "t")
        launch, _ = self.deploy(dry_run=True)  # (same patches: a dry run works out that the rebuild is kept)
        self.assertEqual(launch["parts"]["exefs"]["rebuild"], "reuse")
        self.assertEqual(launch["env"]["SAVAGEWIND_GAME_LIB"], str(self.deploy_dir / "swgame.dll"))
        self.assertNotIn("10-20 minutes", mm.format_deploy(launch))
        self.assertEqual(len(self.rebuilds), 1)

    def test_exefs_clash_is_refused(self):
        self.mod("One", {"exefs/a.pchtxt": DATA_PATCH})
        self.mod("Two", {"exefs/b.pchtxt": DATA_PATCH_2})
        with self.assertRaises(mm.ModmanError) as cm:
            self.deploy()
        self.assertIn("One", str(cm.exception))
        self.assertIn("Two", str(cm.exception))
        self.assertFalse(self.deploy_dir.exists())  # (refused before anything was written)

    def test_dry_run_writes_nothing(self):
        self.mod("Romfs", {"romfs/Pack/Title.txt": "t"})
        self.mod("Code", {"exefs/code.pchtxt": CODE_PATCH})
        launch, _ = self.deploy(dry_run=True)
        self.assertTrue(launch["dry_run"])
        self.assertEqual(launch["parts"]["exefs"]["rebuild"], "build")
        self.assertIn("10-20 minutes", mm.format_deploy(launch))
        self.assertFalse(self.deploy_dir.exists())
        self.assertEqual(self.rebuilds, [])

    def test_refused_while_the_game_runs(self):
        with mock.patch.object(mm, "game_running", return_value=True), self.assertRaises(mm.ModmanError):
            self.deploy()

    def test_patched_main_in_the_game_files_uses_the_vanilla_backup(self):
        vanilla = (self.extracted / "exefs" / "main").read_bytes()
        patched = make_nso(MAIN_ID, [(0x0, b"\x11" * 0x1000), SEGS[1], SEGS[2]])
        write(self.install, {"extracted/exefs/main": patched, "mods/.vanilla/exefs/main": vanilla,
                             "mods/.vanilla/state.json": json.dumps(
                                 {"main_installed_sha256": hashlib.sha256(patched).hexdigest()})})
        self.mod("Data", {"exefs/data.pchtxt": DATA_PATCH})
        launch, _ = self.deploy()
        main = nso.load(str(self.deploy_dir / "root" / "exefs" / "main"))
        self.assertEqual(main.read(0x0, 16), bytes(16))      # vanilla code
        self.assertEqual(main.read(0x1010, 4), b"ABCD")      # with the patch
        self.assertFalse((self.deploy_dir / ".work").exists())
        self.assertEqual((self.extracted / "exefs" / "main").read_bytes(), patched)

    def test_rebuilt_library_in_the_game_files_uses_the_vanilla_copy(self):
        write(self.install, {"extracted/swgame.dll.vanilla": b"vanilla game code",
                             "extracted/swgame.dll.vanilla.id": "test-id\n",
                             "mods/.vanilla/state.json": json.dumps({"library_installed_sha256": hashlib.sha256(
                                 b"made-up game code").hexdigest()})})
        self.mod("Data", {"exefs/data.pchtxt": DATA_PATCH})
        launch, _ = self.deploy()
        lib = self.deploy_dir / "vanilla-lib" / "swgame.dll"
        self.assertEqual(launch["env"], {"SAVAGEWIND_GAME_LIB": str(lib)})
        self.assertEqual(lib.read_bytes(), b"vanilla game code")


class DeploySafety(Base):
    def test_repeat_keeps_the_game_files_behind_junctions(self):
        self.mod("Data", {"exefs/data.pchtxt": DATA_PATCH})
        for _ in range(2):
            self.deploy()
        self.assertTrue((self.deploy_dir / "root" / "exefs" / "main").is_file())
        quiet(mm.main, ["disable", "data", "--library", str(self.libdir)])
        self.deploy()
        self.assertFalse((self.deploy_dir / "root").exists())
        for rel in ("romfs/Pack/Title.txt", "romfs/Actor/Pack/Hero.txt", "aoc1/Map/MainField/Dlc.txt", "exefs/main"):
            self.assertTrue((self.extracted / rel).is_file(), rel)

    def test_foreign_files_stop_a_redeploy_and_stay(self):
        self.mod("Data", {"exefs/data.pchtxt": DATA_PATCH})
        self.deploy()
        write(self.deploy_dir, {"root/exefs/subsdk1": b"someone else's", "native/x.txt": "mine"})
        with self.assertRaises(mm.ModmanError) as cm:
            self.deploy()
        self.assertIn("subsdk1", str(cm.exception))
        self.assertTrue((self.deploy_dir / "root" / "exefs" / "subsdk1").is_file())
        self.assertTrue((self.deploy_dir / "launch.json").is_file())  # (refused before anything was deleted)

    def test_a_folder_modman_didnt_make_is_refused(self):
        write(self.deploy_dir, {"notes.txt": "mine"})
        self.mod("A", {"romfs/Pack/Title.txt": "a"})
        with self.assertRaises(mm.ModmanError):
            self.deploy()
        self.assertEqual((self.deploy_dir / "notes.txt").read_text(), "mine")

    def test_marker_paths_outside_the_folder_are_ignored(self):
        self.mod("A", {"romfs/Pack/Title.txt": "a"})
        self.deploy()
        outside = write(self.libdir, {"outside.txt": "keep"}) / "outside.txt"
        marker = self.deploy_dir / mm.MARKER
        data = json.loads(marker.read_text())
        data["files"] += ["../outside.txt", "..\\outside.txt", str(outside)]
        data["links"] = ["../mods"]
        marker.write_text(json.dumps(data))
        self.deploy()
        self.assertEqual(outside.read_text(), "keep")
        self.assertTrue((self.libdir / "mods" / "a").is_dir())

    def test_foreign_file_in_the_overlay_is_refused_before_any_write(self):
        self.mod("A", {"romfs/Pack/Title.txt": "a"})
        self.mod("Data", {"exefs/data.pchtxt": DATA_PATCH})
        self.deploy()
        write(self.deploy_dir, {"overlay/Pack/Mine.txt": "mine"})
        with self.assertRaises(mm.ModmanError) as cm:
            self.deploy()
        self.assertIn("Mine.txt", str(cm.exception))
        self.assertTrue((self.deploy_dir / "launch.json").is_file())
        self.assertTrue((self.deploy_dir / "root" / "exefs" / "main").is_file())

    def test_top_level_files_of_others_are_left_alone(self):
        self.mod("A", {"romfs/Pack/Title.txt": "a"})
        self.deploy()
        write(self.deploy_dir, {"my notes.txt": "mine"})
        self.deploy()
        self.assertEqual((self.deploy_dir / "my notes.txt").read_text(), "mine")


# ---------------------------------------------------------------- run, status, doctor

class RunBase(Base):
    def setUp(self):
        super().setUp()
        FakeProc.started = []
        p = mock.patch.object(mm.subprocess, "Popen", side_effect=FakeProc)
        p.start()
        self.addCleanup(p.stop)

    def run_mm(self, *args, env=None, port_open=(False,)):
        with mock.patch.dict(os.environ, env or {}), mock.patch.object(mm, "port_open", side_effect=list(port_open)), \
                mock.patch.object(mm.time, "sleep"):
            return quiet(mm.main, ["run", "--library", str(self.libdir), *args])


class Run(RunBase):
    def test_env_and_argv(self):
        self.mod("Romfs", {"romfs/Pack/Title.txt": "t"})
        self.mod("Data", {"exefs/data.pchtxt": DATA_PATCH})
        self.deploy()
        (code, out) = self.run_mm(env={"SAVAGEWIND_GAME_LIB": "C:\\stale.dll", "SWMOD_DIR": "C:\\stale",
                                       "SAVAGEWIND_NO_HUD_LETTERS": "1", "SAVAGEWIND_SAVE_DIR": "C:\\my-test-saves"})
        self.assertEqual(code, 0, out)
        self.assertEqual(len(FakeProc.started), 1)
        game = FakeProc.started[0]
        self.assertEqual(game.argv, [str(self.install / "savagewind.exe"), str(self.deploy_dir / "root"), "--play"])
        self.assertEqual(game.kw["cwd"], str(self.install))
        env = game.kw["env"]
        self.assertEqual(env["SAVAGEWIND_GAME_LIB"], str(self.extracted / "swgame.dll"))
        self.assertEqual(env["SAVAGEWIND_ROMFS_OVERLAY"], str(self.deploy_dir / "overlay"))
        for gone in ("SWMOD_DIR", "SAVAGEWIND_NO_HUD_LETTERS", "SAVAGEWIND_CONTROL", "SWMOD_REAL_GAME_LIB"):
            self.assertNotIn(gone, env)
        self.assertEqual(env["SAVAGEWIND_SAVE_DIR"], "C:\\my-test-saves")
        self.assertIn("Started Savage Wind (pid", out)
        self.assertNotIn("Warning", out)

    def test_cheats_start_after_the_port_opens(self):
        self.mod("Cheats", {CHEAT_PATH: CHEATS_A})
        self.deploy()
        code, out = self.run_mm(port_open=(False, False, True))
        self.assertEqual(code, 0, out)
        game, helper = FakeProc.started
        self.assertEqual(game.kw["env"]["SAVAGEWIND_CONTROL"], "7777")
        file = str(self.deploy_dir / "cheats" / f"{BID}.txt")
        self.assertEqual(helper.argv[:10], [sys.executable, "-I", mm.CHEATS_PY, "run", file, "--port", "7777",
                                            "--wait", "30", "--all"])
        self.assertEqual(helper.argv[10:], ["--main", str(self.extracted / "exefs" / "main")])
        self.assertNotIn("SAVAGEWIND_CONTROL", helper.kw["env"])
        self.assertTrue(helper.terminated or helper.returncode is not None)
        self.assertIn("Started cheats (pid", out)

    def test_other_port_and_no_cheats(self):
        self.mod("Cheats", {CHEAT_PATH: CHEATS_A})
        self.deploy()
        code, out = self.run_mm("--port", "8123", port_open=(False, True))
        self.assertEqual(FakeProc.started[0].kw["env"]["SAVAGEWIND_CONTROL"], "8123")
        self.assertIn("8123", FakeProc.started[1].argv)
        FakeProc.started = []
        code, out = self.run_mm("--no-cheats")
        self.assertEqual(len(FakeProc.started), 1)
        self.assertNotIn("SAVAGEWIND_CONTROL", FakeProc.started[0].kw["env"])

    def test_refusals(self):
        code, out = self.run_mm()
        self.assertEqual(code, 2)
        self.assertIn("deploy", out)
        self.mod("Cheats", {CHEAT_PATH: CHEATS_A})
        self.deploy()
        with mock.patch.object(mm, "game_running", return_value=True):
            code, out = self.run_mm()
        self.assertIn("already running", out)
        code, out = self.run_mm(port_open=(True,))
        self.assertIn("port 7777", out)
        self.assertEqual(FakeProc.started, [])

    def test_stale_deploy_is_warned_about(self):
        self.mod("A", {"romfs/Pack/Title.txt": "a"})
        self.mod("B", {"romfs/Pack/Title.txt": "b"})
        self.deploy()
        code, out = quiet(mm.main, ["status", "--library", str(self.libdir)])
        self.assertIn("up to date", out)
        quiet(mm.main, ["disable", "b", "--library", str(self.libdir)])
        code, out = quiet(mm.main, ["status", "--library", str(self.libdir)])
        self.assertIn("OUT OF DATE", out)
        code, out = self.run_mm()
        self.assertIn("changed since the last deploy", out)
        self.assertEqual(len(FakeProc.started), 1)  # (still starts the last deployed set)
        self.deploy()
        code, out = quiet(mm.main, ["status", "--library", str(self.libdir)])
        self.assertIn("up to date", out)


class Doctor(Base):
    def test_doctor_on_a_fake_install(self):
        code, out = quiet(mm.main, ["doctor", "--library", str(self.libdir)])
        self.assertIn(f"OK   Savage Wind: {self.install}", out)
        self.assertIn("OK   game files set up (game code id test-id)", out)
        self.assertIn("modhost not built", out)
        self.assertIn("Python", out)
        self.assertEqual(code, 0, out)


# ---------------------------------------------------------------- regressions from the review

class FolderNamedLikeItsLayout(Base):
    """A mod folder named romfs (or cheats) that holds romfs\\ (cheats\\) itself, as 'Extract to romfs\\' makes."""

    def test_romfs_folder_holding_romfs_lands_where_the_game_looks(self):
        src = write(self.dl / "romfs", {"romfs/Actor/Pack/Hero.txt": "mod hero"})
        e, _, _ = quiet(mm.add_mod, self.lib, str(src), name="Hero Mod")[0]
        stored = self.libdir / "mods" / e["id"]
        self.assertEqual([r for _, r, _ in mm.walk_files(str(stored))], ["romfs/Actor/Pack/Hero.txt"])
        launch, _ = self.deploy()
        self.assertEqual((self.deploy_dir / "overlay" / "Actor" / "Pack" / "Hero.txt").read_text(), "mod hero")
        self.assertFalse((self.deploy_dir / "overlay" / "romfs").exists())
        self.assertEqual(launch["parts"]["overlay"]["replaced"], 1)
        self.assertNotIn("doesn't have", " ".join(launch["attention"]))

    def test_romfs_folder_that_is_a_romfs_keeps_its_name(self):
        src = write(self.dl / "Some Mod" / "Romfs", {"Actor/Pack/Hero.txt": "mod hero"})
        quiet(mm.add_mod, self.lib, str(src))
        self.assertTrue((self.libdir / "mods" / "some-mod" / "Romfs" / "Actor" / "Pack" / "Hero.txt").is_file())
        self.deploy()
        self.assertEqual((self.deploy_dir / "overlay" / "Actor" / "Pack" / "Hero.txt").read_text(), "mod hero")

    def test_cheats_folder_with_or_without_its_own_cheats_folder(self):
        for name, files in (("A", {f"cheats/{BID}.txt": CHEATS_A}), ("B", {f"{BID}.txt": CHEATS_A})):
            with self.subTest(name=name):
                src = write(self.dl / name / "cheats", files)
                e, _, _ = quiet(mm.add_mod, self.lib, str(src), name=name)[0]
                self.assertEqual(e["kinds"], ["cheats"])
                self.assertTrue((self.libdir / "mods" / e["id"] / "cheats" / f"{BID}.txt").is_file())


class DeployKeepsTheLastOneWhenRefused(Base):
    """Every check that can refuse a deploy runs before the last deploy is deleted."""

    def good_deploy(self):
        self.mod("Good", {"romfs/Pack/Title.txt": "good", "exefs/data.pchtxt": DATA_PATCH})
        self.deploy()
        self.assertTrue((self.deploy_dir / "launch.json").is_file())

    def assert_last_deploy_kept(self):
        self.assertTrue((self.deploy_dir / "launch.json").is_file())
        self.assertTrue((self.deploy_dir / "root" / "exefs" / "main").is_file())
        self.assertEqual((self.deploy_dir / "overlay" / "Pack" / "Title.txt").read_text(), "good")
        mm.read_launch(self.lib)  # (run and status still see it)

    def test_mod_with_only_a_dlc_marker_is_refused_at_add(self):
        with self.assertRaises(mm.ModmanError) as cm:
            self.mod("Aoc Marker", {"romfs/System/AocVersion.txt": "x"})
        self.assertIn("no RomFS files to deploy", str(cm.exception))

    def test_stored_mod_that_no_longer_works_is_left_out_with_a_note(self):
        self.good_deploy()
        self.mod("Marker", {"romfs/Pack/Title.txt": "m"})
        stored = self.libdir / "mods" / "marker"
        os.remove(stored / "romfs" / "Pack" / "Title.txt")
        write(stored, {"romfs/System/AocVersion.txt": "x"})
        launch, _ = self.deploy()
        self.assertIn("Marker: ", " ".join(launch["attention"]))
        self.assertIn("no RomFS files to deploy", " ".join(launch["attention"]))
        self.assertEqual((self.deploy_dir / "overlay" / "Pack" / "Title.txt").read_text(), "good")

    def test_romfs_refusal_comes_before_any_delete(self):
        self.good_deploy()
        write(self.deploy_dir, {"overlay~new/mine.txt": "someone else's"})
        with self.assertRaises(mm.ModmanError) as cm:
            self.deploy()
        self.assertIn("RomFS overlay", str(cm.exception))
        self.assert_last_deploy_kept()
        self.assertEqual((self.deploy_dir / "overlay~new" / "mine.txt").read_text(), "someone else's")

    def test_patch_past_the_end_or_between_segments_is_refused_at_add(self):
        for name, text, why in (("Far", FAR_PATCH, "past the end of main"),
                                ("Gap", GAP_PATCH, "outside main's segments")):
            with self.subTest(name=name), self.assertRaises(mm.ModmanError) as cm:
                self.mod(name, {"exefs/p.pchtxt": text})
            self.assertIn(why, str(cm.exception))

    def test_bad_patch_found_only_at_deploy_keeps_the_last_deploy(self):
        self.good_deploy()
        real = mm.game_info
        # (as if add couldn't read the module layout, so only deploy's own check is left to catch it)
        with mock.patch.object(mm, "game_info", lambda inst: dataclasses.replace(real(inst), layouts={})):
            self.mod("Far", {"exefs/far.pchtxt": FAR_PATCH})
            with self.assertRaises(mm.ModmanError) as cm:
                self.deploy()
        self.assertIn("Far: far.pchtxt: Far writes past the end of main", str(cm.exception))
        self.assert_last_deploy_kept()

    def test_mixed_patch_file_with_one_bad_patch(self):
        e = self.mod("Mixed", {"exefs/ok.pchtxt": DATA_PATCH, "exefs/far.pchtxt": FAR_PATCH})
        self.assertEqual(e["kinds"], ["exefs-patch"])
        self.assertTrue(any("far.pchtxt" in p and "isn't used" in p for p in e["problems"]))
        launch, _ = self.deploy()
        self.assertEqual(launch["parts"]["exefs"]["files"], 1)
        self.assertIn("far.pchtxt", " ".join(launch["attention"]))


class LibrarySnapshot(Base):
    """The library keeps its own copy of a mod's small files, DLLs and mod.toml: rebuilding or editing the folder the
    mod was added from changes nothing in it. Only big data files (1 MiB or more) are hard links on the same drive."""
    BIG = b"B" * (mm.COPY_BELOW + 1)

    def test_which_files_get_their_own_copy(self):
        small, big = mm.COPY_BELOW - 1, mm.COPY_BELOW
        for rel, size, own in (("romfs/Pack/Title.txt", small, True), ("romfs/Model/Big.bin", big, False),
                               ("bin/ghost.dll", big, True), ("bin/Ghost.DLL", big, True), ("tool.exe", big, True),
                               ("bin/ghost.pdb", big, True), ("mod.toml", big, True), ("Wrap/Mod.TOML", big, True),
                               ("romfs/mod.toml.bak", big, False), ("romfs/x.dll.txt", big, False)):
            self.assertEqual(mm.keep_own_copy(rel, size), own, rel)

    def test_rebuilt_dll_and_edited_mod_toml_dont_change_the_library(self):
        src = write(self.dl / "Ghost", {"mod.toml": mod_toml("someone.ghost", "Ghost", "1.0.0", dll="bin/ghost.dll"),
                                        "bin/ghost.dll": make_pe(exports=("swmod_init",)), "readme.txt": "small"})
        (e, _, notes), _ = quiet(mm.add_mod, self.lib, str(src))
        self.assertEqual((e["id"], e["version"]), ("someone.ghost", "1.0.0"))
        self.assertIn("3 file(s) copied", notes)
        lib = self.libdir / "mods" / "someone.ghost"
        rels = ("mod.toml", "bin/ghost.dll", "readme.txt")
        for r in rels:
            self.assertFalse(os.path.samefile(lib / r, src / r), r)
        before = {r: (lib / r).read_bytes() for r in rels}
        # (written in place, as a rebuild or an editor does: a hard link would show the new bytes)
        (src / "bin" / "ghost.dll").write_bytes(make_pe(exports=("swmod_init",)) + b"rebuilt")
        (src / "mod.toml").write_text(mod_toml("someone.ghost", "Ghost", "2.0.0", dll="bin/ghost.dll"))
        (src / "readme.txt").write_text("changed")
        self.assertEqual({r: (lib / r).read_bytes() for r in rels}, before)
        self.assertEqual(mm.read_manifest(str(lib))[0].version, "1.0.0")
        code, out = quiet(mm.main, ["list", "--library", str(self.libdir)])
        self.assertIn("someone.ghost  1.0.0  native  Ghost", out)

    def test_a_dll_added_on_its_own_is_a_copy(self):
        dll = write(self.dl, {"loose.dll": make_pe(exports=("swmod_init",))}) / "loose.dll"
        e = quiet(mm.add_mod, self.lib, str(dll))[0][0]
        stored = self.libdir / "mods" / e["id"] / "loose.dll"
        self.assertFalse(os.path.samefile(stored, dll))
        dll.write_bytes(b"rebuilt")
        self.assertNotEqual(stored.read_bytes(), b"rebuilt")

    def test_big_data_file_is_still_a_link_on_the_same_drive(self):
        src = write(self.dl / "Big Data", {"romfs/Data/Big.bin": self.BIG, "romfs/Pack/Title.txt": "small"})
        (e, _, notes), _ = quiet(mm.add_mod, self.lib, str(src))
        self.assertIn("2 file(s) (1 linked, 1 copied)", notes)
        lib = self.libdir / "mods" / e["id"] / "romfs"
        self.assertTrue(os.path.samefile(lib / "Data" / "Big.bin", src / "romfs" / "Data" / "Big.bin"))
        self.assertFalse(os.path.samefile(lib / "Pack" / "Title.txt", src / "romfs" / "Pack" / "Title.txt"))

    def test_big_dll_and_read_only_big_file_are_copies(self):
        src = write(self.dl / "Sized", {"bin/helper.dll": self.BIG, "romfs/Data/Big.bin": self.BIG,
                                        "romfs/Data/Locked.bin": self.BIG})
        os.chmod(src / "romfs" / "Data" / "Locked.bin", stat.S_IREAD)
        dest = self.tmp / "stored"
        self.assertEqual(mm.store_folder(str(src), str(dest))[:2], (1, 2))
        self.assertFalse(os.path.samefile(dest / "bin" / "helper.dll", src / "bin" / "helper.dll"))
        locked = ("romfs", "Data", "Locked.bin")
        self.assertFalse(os.path.samefile(dest.joinpath(*locked), src.joinpath(*locked)))
        self.assertTrue(os.path.samefile(dest / "romfs" / "Data" / "Big.bin", src / "romfs" / "Data" / "Big.bin"))

    def test_free_space_counts_the_copies_only(self):
        src = write(self.dl / "Sized", {"bin/helper.dll": self.BIG, "romfs/Data/Big.bin": self.BIG,
                                        "romfs/Pack/Title.txt": "small"})
        copies = len(self.BIG) + len("small")  # (the DLL and the small file; Big.bin is a link)
        with mock.patch.object(mm.shutil, "disk_usage", return_value=mock.Mock(free=copies - 1)):
            with self.assertRaises(mm.ModmanError) as cm:
                mm.store_folder(str(src), str(self.tmp / "short"))
        self.assertIn("Not enough free space", str(cm.exception))
        with mock.patch.object(mm.shutil, "disk_usage", return_value=mock.Mock(free=copies)):
            self.assertEqual(mm.store_folder(str(src), str(self.tmp / "fits"))[:2], (1, 2))

    def test_a_link_that_fails_is_copied_if_it_fits(self):
        src = write(self.dl / "No Links", {"romfs/Data/Big.bin": self.BIG, "romfs/Pack/Title.txt": "small"})
        need = len(self.BIG) + len("small")
        no_links = mock.patch("os.link", side_effect=OSError("no hard links on this drive"))
        with no_links, mock.patch.object(mm.shutil, "disk_usage", return_value=mock.Mock(free=need - 1)):
            with self.assertRaises(mm.ModmanError) as cm:
                mm.store_folder(str(src), str(self.tmp / "short"))
        self.assertIn("Not enough free space", str(cm.exception))
        with no_links, mock.patch.object(mm.shutil, "disk_usage", return_value=mock.Mock(free=need)):
            self.assertEqual(mm.store_folder(str(src), str(self.tmp / "fits"))[:2], (0, 2))

    def test_a_loose_file_copy_checks_free_space(self):
        """store_file refuses a copy that doesn't fit with store_folder's message, not a raw OS error."""
        files = write(self.dl, {"loose.dll": self.BIG, f"{mm.BUILD_190}.txt": "[Hearts]\n04000000 00001000 00000001\n"})
        for name in ("loose.dll", f"{mm.BUILD_190}.txt"):
            src, size = files / name, (files / name).stat().st_size
            with mock.patch.object(mm.shutil, "disk_usage", return_value=mock.Mock(free=size - 1)):
                with self.assertRaises(mm.ModmanError) as cm:
                    mm.store_file(str(src), str(self.tmp / "short"))
            self.assertIn("Not enough free space", str(cm.exception))
            self.assertIn(f"{mm._size(size - 1)} free", str(cm.exception))
            self.assertEqual([p for p in (self.tmp / "short").rglob("*") if p.is_file()], [])
            with mock.patch.object(mm.shutil, "disk_usage", return_value=mock.Mock(free=size)):
                self.assertEqual(mm.store_file(str(src), str(self.tmp / "fits")), "copy")

    def test_a_loose_big_patch_link_needs_no_free_space(self):
        src = write(self.dl, {"big.ips": self.BIG}) / "big.ips"
        with mock.patch.object(mm.shutil, "disk_usage", return_value=mock.Mock(free=0)):
            self.assertEqual(mm.store_file(str(src), str(self.tmp / "linked")), "link")

    def test_a_loose_big_patch_whose_link_fails_checks_free_space(self):
        src = write(self.dl, {"big.ips": self.BIG}) / "big.ips"
        dest = self.tmp / "nolinks"
        with mock.patch.object(mm.os, "link", side_effect=OSError("no hard links here")):
            with mock.patch.object(mm.shutil, "disk_usage", return_value=mock.Mock(free=0)):
                with self.assertRaisesRegex(mm.ModmanError, "Not enough free space"):
                    mm.store_file(str(src), str(dest))
            self.assertFalse((dest / "big.ips").exists())
            self.assertEqual(mm.store_file(str(src), str(dest)), "copy")
        self.assertEqual((dest / "big.ips").read_bytes(), src.read_bytes())


class ReadOnlyFilesKeepTheirSetting(Base):
    """Deleting modman's hard links never clears read-only on the game's files or the player's own mod files."""

    @unittest.skipUnless(os.name == "nt", "the read-only attribute only blocks deletes on Windows")
    def test_unlink_of_a_read_only_hard_link(self):
        a = write(self.tmp, {"a.txt": "x"}) / "a.txt"
        b = self.tmp / "b.txt"
        os.link(a, b)
        os.chmod(a, stat.S_IREAD)
        mm._unlink(str(b))
        self.assertFalse(b.exists())
        self.assertTrue(read_only(a))
        mm._unlink(str(a))  # (the last name: modman's own, so it may clear it)
        self.assertFalse(a.exists())

    def test_deploy_and_remove(self):
        vanilla = (self.extracted / "exefs" / "main").read_bytes()
        patched = make_nso(MAIN_ID, [(0x0, b"\x11" * 0x1000), SEGS[1], SEGS[2]])
        write(self.install, {"extracted/exefs/main": patched, "mods/.vanilla/exefs/main": vanilla,
                             "mods/.vanilla/state.json": json.dumps(
                                 {"main_installed_sha256": hashlib.sha256(patched).hexdigest()}),
                             "extracted/swgame.dll.vanilla": b"vanilla game code",
                             "extracted/swgame.dll.vanilla.id": "test-id\n"})
        state = json.loads((self.install / "mods/.vanilla/state.json").read_text())
        state["library_installed_sha256"] = hashlib.sha256(b"made-up game code").hexdigest()
        (self.install / "mods/.vanilla/state.json").write_text(json.dumps(state))
        game = [self.extracted / "exefs" / "sdk", self.extracted / "exefs" / "main.npdm",
                self.install / "mods" / ".vanilla" / "exefs" / "main", self.extracted / "swgame.dll.vanilla"]
        for p in game:
            os.chmod(p, stat.S_IREAD)
        # (Title.txt is big enough to be a hard link in the library: small files are copies)
        src = write(self.dl / "Data", {"exefs/data.pchtxt": DATA_PATCH, "romfs/Pack/Title.txt": "t" * mm.COPY_BELOW})
        own = [src / "exefs" / "data.pchtxt", src / "romfs" / "Pack" / "Title.txt"]
        os.chmod(own[0], stat.S_IREAD)
        quiet(mm.add_mod, self.lib, str(src))
        self.assertTrue(os.path.samefile(self.libdir / "mods" / "data" / "romfs" / "Pack" / "Title.txt", own[1]))
        launch, _ = self.deploy()
        self.assertEqual(launch["env"]["SAVAGEWIND_GAME_LIB"], str(self.deploy_dir / "vanilla-lib" / "swgame.dll"))
        os.chmod(own[1], stat.S_IREAD)  # (marked read-only after it was added: the library has a link to it)
        self.deploy()
        quiet(mm.main, ["disable", "data", "--library", str(self.libdir)])
        self.deploy()  # (deletes root\exefs, vanilla-lib and the overlay)
        quiet(mm.main, ["remove", "data", "--library", str(self.libdir)])
        self.assertFalse((self.libdir / "mods" / "data").exists())
        self.assertEqual([read_only(p) for p in game + own], [True] * (len(game) + len(own)))
        self.assertEqual(own[0].read_text(), DATA_PATCH)


class PartlyUsableMods(Base):
    def test_exlaunch_part_is_named_at_add_and_deploy(self):
        src = write(self.dl / "Big Overhaul", {"atmosphere/contents/01007EF00011E000/romfs/Pack/Title.txt": "t",
                                               "atmosphere/contents/01007EF00011E000/exefs/subsdk9": b"\0" * 64,
                                               "atmosphere/contents/01007EF00011E000/exefs/main.npdm": b"META"})
        code, out = quiet(mm.main, ["add", str(src), "--library", str(self.libdir)])
        self.assertEqual(code, 0, out)
        self.assertIn("not used: exlaunch / Skyline code (2 file(s))", out)
        self.assertEqual(self.lib.mods[0]["kinds"], ["romfs"])
        self.assertTrue(any("exlaunch" in p for p in self.lib.mods[0]["problems"]))
        launch, _ = self.deploy()
        self.assertIn("Big Overhaul: exlaunch / Skyline code", "\n".join(launch["attention"]))
        self.assertIn("exlaunch", mm.format_deploy(launch))

    def test_code_mod_alone_is_refused_with_the_reason(self):
        with self.assertRaises(mm.ModmanError) as cm:
            self.mod("Only Code", {"exefs/subsdk9": b"\0" * 64, "exefs/main.npdm": b"META"})
        # (the reason is mod_inspect's: "Switch code mod (exlaunch / Skyline): ... remake it as a native mod")
        self.assertIn("exlaunch / Skyline", str(cm.exception))
        self.assertIn("native mod", str(cm.exception))
        self.assertEqual(str(cm.exception).count("exlaunch"), 1)


def tiny_byml(order: str, key: str = "Hp", value: int = 7) -> bytes:
    """A made-up BYML v2 document {key: value}, in Wii U (wc.BE) or Switch (wc.LE) byte order."""
    def u24(n: int) -> bytes:
        return n.to_bytes(3, "big" if order == wc.BE else "little")
    name = key.encode() + b"\0"
    table = b"\xc2" + u24(1) + struct.pack(order + "II", 12, 12 + len(name)) + name
    table += bytes(-len(table) % 4)
    root = 0x10 + len(table)
    return ((b"BY" if order == wc.BE else b"YB") + struct.pack(order + "HIII", 2, 0x10, 0, root) + table
            + b"\xc1" + u24(1) + u24(0) + b"\xd1" + struct.pack(order + "i", value))


MADE_UP_MODEL = b"FRES    " + bytes(0x40)
WIIU_RSTB = b"RSTB" + struct.pack(">IIII", 1, 0, 0x1234, 0x100)   # one made-up entry, Wii U byte order
CEMU_RULES = "[Definition]\ntitleIds = 00050000101C9400\nname = Made up\n"


def wiiu_files(prefix: str = "") -> dict:
    """A made-up Wii U mod: a Yaz0 actor pack with a BYML inside, a BYML, a model, the RSTB, a DLC BYML, a readme."""
    pack = wc.yaz0_compress(wc.sarc_build([("Actor/A.byml", tiny_byml(wc.BE))], wc.BE))
    return {f"{prefix}{k}": v for k, v in {
        "content/Actor/Pack/Hero.sbactorpack": pack,
        "content/Pack/Title.byml": tiny_byml(wc.BE, "T", 1),
        "content/Model/Armor.sbfres": MADE_UP_MODEL,
        "content/System/Resource/ResourceSizeTable.product.srsizetable": WIIU_RSTB,
        "aoc/0010/Map/MainField/Dlc.byml": tiny_byml(wc.BE, "D", 2),
        "readme.txt": "made up",
    }.items()}


class WiiUMods(Base):
    """modman add converts Wii U mods with tools/wiiu_convert.py (made-up files only)."""

    def add_cli(self, *args):
        return quiet(mm.main, ["add", *map(str, args), "--library", str(self.libdir)])

    def test_add_converts_into_the_library(self):
        src = write(self.dl / "Armor Mod", wiiu_files())
        code, out = self.add_cli(src)
        self.assertEqual(code, 0, out)
        lines = [x.strip() for x in out.splitlines()]
        self.assertEqual(lines[0], "Converting Armor Mod from Wii U (a big mod takes a while)...")
        self.assertEqual(lines[1], "Added #1 armor-mod (romfs, dlc-romfs): Armor Mod")
        report = self.libdir / "mods" / "armor-mod" / wc.REPORT
        self.assertEqual(lines[2:6], [
            "From Wii U: 3 of 5 game file(s) converted.",
            "Left as Wii U data: 1 (1 model/texture). The game can't read these: it may crash or look wrong where "
            "the mod uses them.",
            "Left out: 2 (the RSTB, readme.txt).",
            f"Report: {report}"])
        # the library holds the Switch copy; the download keeps its Wii U files
        mod = self.libdir / "mods" / "armor-mod"
        self.assertEqual((mod / "romfs/Pack/Title.byml").read_bytes(), tiny_byml(wc.LE, "T", 1))
        self.assertEqual((mod / "01007EF00011F001/romfs/Map/MainField/Dlc.byml").read_bytes(),
                         tiny_byml(wc.LE, "D", 2))
        raw, _ = wc.yaz0_decompress((mod / "romfs/Actor/Pack/Hero.sbactorpack").read_bytes())
        self.assertEqual(wc.sarc_files(raw), [("Actor/A.byml", tiny_byml(wc.LE))])
        self.assertEqual((mod / "romfs/Model/Armor.sbfres").read_bytes(), MADE_UP_MODEL)  # (kept as Wii U data)
        self.assertFalse((mod / "romfs/System/Resource").exists())  # (the Wii U RSTB is left out)
        self.assertFalse((mod / "readme.txt").exists())
        self.assertEqual((src / "content/Pack/Title.byml").read_bytes(), tiny_byml(wc.BE, "T", 1))
        # the report is wiiu_convert's, and names the mod's own library folder
        text = report.read_text(encoding="utf-8")
        self.assertIn(f"Switch mod written to: {mod} (", text)
        self.assertIn("content/Model/Armor.sbfres: model/texture", text)
        self.assertNotIn(mm.ADDING, text)
        self.assertEqual(os.listdir(self.libdir / "mods"), ["armor-mod"])  # (no staging folder left)
        e = self.lib.mods[0]
        self.assertEqual(e["kinds"], ["romfs", "dlc-romfs"])
        self.assertEqual(e["wiiu"], {"files": 5, "converted": 3, "partly": 0, "wiiu_data": 1, "left_out": 2,
                                     "attention": 0})

    def test_add_converts_models_with_the_game_files(self):
        """A Wii U model mod: modman passes the game's RomFS to the model converter, and the files converted with
        warnings are counted in config.json and listed by deploy (made-up model, texture and game folder)."""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_wiiu_model_integrate import model_res, tex_set
        from wiiu_model import wiiu_bfres, wiiu_models
        write(self.install, {"extracted/romfs/Model/Readme.txt": "made up"})
        self.assertEqual(mm.game_romfs(self.lib), str(self.install / "extracted" / "romfs"))
        tex1, _ = wiiu_bfres.write_textures(tex_set("TestProp"))
        src = write(self.dl / "Prop Mod", {
            "content/Model/TestProp.sbfres": wiiu_models.write(model_res("TestProp", 2), keep_render_infos=False),
            "content/Model/TestProp.Tex1.sbfres": tex1})
        code, out = self.add_cli(src)
        self.assertEqual(code, 0, out)
        self.assertIn("From Wii U: all 2 game file(s) converted.", out)
        # (the textures have no Tex2 and no vanilla twin; the model's material names a texture no X.Tex has)
        self.assertIn("Models and textures converted with warnings: 2", out)
        e = self.lib.mods[0]
        self.assertEqual(e["wiiu"]["attention"], 2)
        mod = self.libdir / "mods" / e["id"]
        self.assertTrue((mod / "romfs/Model/TestProp.Tex.sbfres").is_file())
        self.assertFalse((mod / "romfs/Model/TestProp.Tex1.sbfres").exists())
        self.assertIn(f"with your game files from {self.install / 'extracted' / 'romfs'}",
                      (mod / wc.REPORT).read_text(encoding="utf-8"))
        launch, _ = self.deploy()
        self.assertIn(f"{e['name']}: 2 model/texture file(s) were converted from Wii U with warnings",
                      "\n".join(launch["attention"]))

    def test_zip_cemu_pack_with_a_code_patch(self):
        z = make_zip(self.dl / "Pack.zip", {"My Pack/rules.txt": CEMU_RULES, "My Pack/patch_fps.asm": "[x]\n",
                                            "My Pack/content/Pack/Title.byml": tiny_byml(wc.BE)})
        code, out = self.add_cli(z)
        self.assertEqual(code, 0, out)
        self.assertIn("From Wii U: all 1 game file(s) converted.", out)
        self.assertIn("Left out: 2 (patch_fps.asm, rules.txt).", out)
        self.assertIn("not used: Cemu code patch (PowerPC)", out)  # (that part of the pack isn't in the library)
        self.assertNotIn("Left as Wii U data", out)
        e = self.lib.mods[0]
        self.assertEqual((e["id"], e["kinds"], e["wiiu"]["wiiu_data"]), ("pack", ["romfs"], 0))
        self.assertTrue(any("Cemu code patch" in p for p in e["problems"]))
        self.assertEqual((self.libdir / "mods/pack/romfs/Pack/Title.byml").read_bytes(), tiny_byml(wc.LE))
        os.remove(z)  # (the .zip was closed again)

    def test_content_folder_on_its_own(self):
        src = write(self.dl / "Shield Mod" / "content", {"Pack/Title.byml": tiny_byml(wc.BE)})
        with self.assertRaises(mm.ModmanError) as cm:  # (--no-convert: as before)
            quiet(mm.add_mod, self.lib, str(src), convert=False)
        self.assertIn("doesn't look like a BotW mod", str(cm.exception))
        e, _, notes = quiet(mm.add_mod, self.lib, str(src))[0]
        self.assertEqual((e["id"], e["name"], e["kinds"]), ("shield-mod", "Shield Mod", ["romfs"]))
        self.assertEqual(notes[0], "From Wii U: all 1 game file(s) converted.")
        self.assertEqual((self.libdir / "mods/shield-mod/romfs/Pack/Title.byml").read_bytes(), tiny_byml(wc.LE))

    def test_nothing_convertible_is_refused_and_nothing_added(self):
        cases = {"Only Models": ({"content/Model/Armor.sbfres": MADE_UP_MODEL},
                                 ["nothing in Only Models could be converted from Wii U",
                                  "Can't be converted yet: 1 (1 model/texture)"]),
                 "Only RSTB": ({"content/System/Resource/ResourceSizeTable.product.srsizetable": WIIU_RSTB},
                               ["nothing in Only RSTB could be converted", "Left out: 1 (the RSTB)"]),
                 "Two Options": ({"A/content/Pack/Title.byml": tiny_byml(wc.BE),
                                  "B/content/Pack/Title.byml": tiny_byml(wc.BE, "T", 2)},
                                 ["Two Options holds several mods or options"])}
        for name, (files, want) in cases.items():
            with self.subTest(name=name):
                code, out = self.add_cli(write(self.dl / name, files))
                self.assertEqual(code, 2, out)
                for w in want:
                    self.assertIn(w, out)
                self.assertIn("Not added", out)
        self.assertEqual(self.lib.mods, [])
        self.assertEqual(os.listdir(self.libdir / "mods"), [])

    def test_folder_from_wiiu_convert_keeps_the_wiiu_warnings(self):
        """wiiu_convert convert, then add its output (the route wiiu-mods.md showed): the library entry lost
        '(from Wii U)' and deploy's 'still Wii U data' line, and a folder with only a Wii U model in it was added
        (made-up mods)."""
        src = write(self.dl / "Armor Mod", wiiu_files())
        out = self.dl / "Armor Mod (Switch)"
        self.assertEqual(wc.convert_mod(str(src), str(out)).verdict(), "converted in part")
        code, text = self.add_cli(out)
        self.assertEqual(code, 0, text)
        self.assertIn("From Wii U (converted by tools/wiiu_convert.py): 3 of 5 game file(s) converted.", text)
        self.assertIn("Left as Wii U data: 1. The game can't read these", text)
        e = self.lib.mods[0]
        self.assertEqual(e["wiiu"], {"files": 5, "converted": 3, "partly": 0, "wiiu_data": 1, "left_out": 2,
                                     "attention": 0})
        self.assertIn(f"Report: {self.libdir / 'mods' / e['id'] / wc.REPORT}", text)
        self.assertTrue((self.libdir / "mods" / e["id"] / wc.REPORT).is_file())
        self.assertEqual(mm.wiiu_short(e), "from Wii U, 1 file(s) not converted")
        launch, _ = self.deploy()
        self.assertIn(f"{e['name']}: 1 file(s) are still Wii U data", "\n".join(launch["attention"]))
        # a folder an older convert left with nothing usable in it (only the model) is refused
        old = write(self.dl / "Model Out", {"romfs/Model/Armor.sbfres": MADE_UP_MODEL})
        only = write(self.dl / "Only Models", {"content/Model/Armor.sbfres": MADE_UP_MODEL})
        (old / wc.REPORT).write_text(wc.check_mod(str(only)).text(), encoding="utf-8")
        code, text = self.add_cli(old)
        self.assertEqual(code, 2, text)
        self.assertIn("nothing in Model Out could be converted from Wii U", text)
        self.assertEqual(len(self.lib.mods), 1)
        # convert itself now writes nothing for such a mod
        self.assertEqual(wc.convert_mod(str(only), str(self.dl / "New Out")).verdict(), "nothing could be converted")
        self.assertFalse((self.dl / "New Out").exists())

    def test_no_convert_keeps_the_old_refusal(self):
        src = write(self.dl / "Armor Mod", wiiu_files())
        code, out = self.add_cli(src, "--no-convert")
        self.assertEqual(code, 2, out)
        self.assertIn("is a Wii U mod (content/ folder)", out)
        self.assertIn("python tools/wiiu_convert.py convert <mod> --out <new folder>", out)
        self.assertNotIn("UKMM", out)
        self.assertEqual(self.lib.mods, [])
        self.assertEqual(os.listdir(self.libdir / "mods"), [])

    def test_what_is_not_converted(self):
        # a Switch overlay folder (bare aoc/0010 next to Pack/) is a Switch mod; a Wii U UKMM package goes to UKMM
        e = self.mod("Overlay", {"Pack/Title.txt": "t", "aoc/0010/Map/MainField/New.txt": "d"})
        self.assertEqual(e["kinds"], ["romfs", "dlc-romfs"])
        self.assertNotIn("wiiu", e)
        with self.assertRaises(mm.ModmanError) as cm:
            self.mod("Ukmm WiiU", {"meta.yml": "platform: !Specific Wii U\n", "manifest.yml": "- Actor/A.byml\n"})
        self.assertIn("install it in UKMM", str(cm.exception))

    def test_list_status_and_deploy_show_it(self):
        self.mod("Armor Mod", wiiu_files())
        self.mod("Sprint", {"romfs/Pack/Title.txt": "sprint"})
        lib = str(self.libdir)
        _, out = quiet(mm.main, ["list", "--library", lib])
        rows = {ln.split()[2]: ln for ln in out.splitlines() if ln.startswith("   ")}
        self.assertTrue(rows["armor-mod"].endswith("Armor Mod  (from Wii U, 1 file(s) not converted)"), out)
        self.assertNotIn("Wii U", rows["sprint"])
        _, out = quiet(mm.main, ["status", "--library", lib])
        self.assertIn("Converted from Wii U: armor-mod (1 file(s) not converted)", out)
        launch, _ = self.deploy()
        overlay = self.deploy_dir / "overlay"
        self.assertEqual((overlay / "Pack/Title.byml").read_bytes(), tiny_byml(wc.LE, "T", 1))
        self.assertEqual((overlay / "aoc/0010/Map/MainField/Dlc.byml").read_bytes(), tiny_byml(wc.LE, "D", 2))
        self.assertFalse((overlay / wc.REPORT).exists())
        self.assertIn("Armor Mod: 1 file(s) are still Wii U data", "\n".join(launch["attention"]))
        quiet(mm.main, ["disable", "armor-mod", "--library", lib])
        _, out = quiet(mm.main, ["status", "--library", lib])
        self.assertNotIn("Converted from Wii U", out)  # (only mods that are on)
        self.assertEqual(mm.wiiu_short(self.lib.mods[0]), "from Wii U, 1 file(s) not converted")
        self.assertEqual(mm.wiiu_short(self.lib.mods[1]), "")

    def test_hand_edited_wiiu_entries(self):
        cfg = json.loads((self.libdir / "config.json").read_text())
        cfg["mods"] = [{"id": "a", "name": "A", "enabled": True, "kinds": ["romfs"], "wiiu": "yes"},
                       {"id": "b", "name": "B", "enabled": True, "kinds": ["romfs"],
                        "wiiu": {"files": 3, "wiiu_data": -1, "converted": True, "left_out": "2", "other": 1}}]
        (self.libdir / "config.json").write_text(json.dumps(cfg))
        a, b = self.lib.mods
        self.assertNotIn("wiiu", a)
        self.assertEqual(b["wiiu"], {"files": 3})
        self.assertEqual(mm.wiiu_short(b), "from Wii U")

    def assert_nothing_added(self):
        self.assertEqual(self.lib.mods, [])
        mods = self.libdir / "mods"
        self.assertEqual(os.listdir(mods) if mods.exists() else [], [])

    def test_zip_python_cant_read_is_refused_as_with_no_convert(self):
        # (it crashed with a RuntimeError, or named a file inside in a garbled sentence)
        cases = {"Locked": ({"flag": 1}, "Locked.zip is password-protected. Extract it yourself, then add the folder."),
                 "Big Mod": ({"method": 9}, "Big Mod.zip uses a compression Python can't read. Extract it with "
                                            "7-Zip, then add the folder.")}
        for name, (how, want) in cases.items():
            z = mark_zip(make_zip(self.dl / f"{name}.zip", {"content/Pack/Title.byml": tiny_byml(wc.BE)}), **how)
            for extra in ((), ("--no-convert",)):
                with self.subTest(name=name, extra=extra):
                    code, out = self.add_cli(z, *extra)
                    self.assertEqual(code, 2, out)
                    self.assertEqual(out.strip(), f"Error: Not added: {want}")
        self.assert_nothing_added()

    def test_damaged_zip_is_refused_without_a_crash(self):
        z = self.dl / "Damaged.zip"
        z.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("content/Pack/Title.byml", tiny_byml(wc.BE) * 50)
        data = bytearray(z.read_bytes())
        start = 30 + len("content/Pack/Title.byml")  # (the packed data of the first member: no extra field)
        data[start:start + 2] = b"\xff\xff"            # (a deflate block type that doesn't exist)
        z.write_bytes(bytes(data))
        code, out = self.add_cli(z)
        self.assertEqual(code, 2, out)
        self.assertIn("Error: Not added: Damaged.zip is damaged (", out)
        self.assertIn("extract it with 7-Zip and add the folder", out)
        code, out = self.add_cli(z, "--no-convert")  # (it crashed here too, in extract_zip)
        self.assertEqual(code, 2, out)
        self.assertIn("Error: Not added: can't extract content/Pack/Title.byml from Damaged.zip (", out)
        self.assert_nothing_added()

    def test_cemu_pack_for_another_game_is_refused(self):
        rules = "[Definition]\ntitleIds = 000500001010EC00\nname = Made up\n"
        src = write(self.dl / "Other Game Pack", {"rules.txt": rules, "content/Pack/Title.byml": tiny_byml(wc.BE)})
        for extra in ((), ("--no-convert",)):
            with self.subTest(extra=extra):
                code, out = self.add_cli(src, *extra)
                self.assertEqual(code, 2, out)
                self.assertEqual(out.strip(), "Error: Not added: Other Game Pack is a Cemu graphic pack made for "
                                              "another game, not BotW (its rules.txt has no BotW title id).")
        self.assert_nothing_added()

    def test_no_convert_names_add_only_for_what_add_converts(self):
        rstb = "content/System/Resource/ResourceSizeTable.product.srsizetable"
        # (audio: a kind wiiu_convert keeps as Wii U data; models convert now, see test_wiiu_model_integrate)
        src = write(self.dl / "Only Audio", {"content/Sound/Stream/Armor.bfstm": b"FSTM" + bytes(20), rstb: WIIU_RSTB})
        self.assertEqual([f.verdict for f in mm.mi.inspect(mm.mi.open_source(src))], [mm.mi.LATER])
        code, out = self.add_cli(src, "--no-convert")
        self.assertEqual(code, 2, out)
        self.assertIn("tools/wiiu_convert.py can't convert yet", out)
        self.assertNotIn("converts it for you", out)
        code, out = self.add_cli(src)
        self.assertEqual(code, 2, out)
        self.assertIn("nothing in Only Audio could be converted from Wii U", out)
        code, out = self.add_cli(write(self.dl / "Text Mod", {"content/Pack/Title.byml": tiny_byml(wc.BE)}),
                                 "--no-convert")
        self.assertIn("python tools/modman.py add converts it for you", out)
        self.assert_nothing_added()

    def test_dlc_only_mod_and_its_aoc_folder_on_its_own(self):
        dlc = {"Map/MainField/Dlc.byml": tiny_byml(wc.BE, "D", 2)}
        cases = [(write(self.dl / "Dlc Mod", {f"aoc/0010/{k}": v for k, v in dlc.items()}), "dlc-mod", "Dlc Mod"),
                 (write(self.dl / "Dlc Two" / "aoc" / "0010", dlc), "dlc-two", "Dlc Two")]
        for src, mod_id, name in cases:
            with self.subTest(src=src):
                e, _, notes = quiet(mm.add_mod, self.lib, str(src))[0]
                self.assertEqual((e["id"], e["name"], e["kinds"]), (mod_id, name, ["dlc-romfs"]))
                self.assertEqual(e["wiiu"]["converted"], 1)
                self.assertEqual(notes[0], "From Wii U: all 1 game file(s) converted.")
                mod = self.libdir / "mods" / mod_id
                self.assertEqual((mod / "01007EF00011F001/romfs/Map/MainField/Dlc.byml").read_bytes(),
                                 tiny_byml(wc.LE, "D", 2))
                self.assertFalse((mod / "romfs").exists())

    def test_the_doc_says_how_many_lines_add_prints(self):
        rep = wc.check_mod(str(write(self.dl / "Armor Mod", wiiu_files())))
        self.assertEqual(len(mm.wiiu_summary(rep, "report.txt")), 4)  # (converted, Wii U data, left out, report)
        doc = " ".join((REPO / "docs" / "mod-manager.md").read_text(encoding="utf-8").split())
        self.assertIn("`add` prints up to 4 lines", doc)


class HelperDlls(Base):
    def test_helper_dlls_go_next_to_the_mod_dll(self):
        self.build_host()
        self.mod("Fancy", {"fancy.dll": make_pe(exports=("swmod_init",)), "libs/fancyutil.dll": make_pe(exports=("f",)),
                           "old32.dll": make_pe32(exports=("g",))})
        self.mod("Romfs Only", {"romfs/Pack/Title.txt": "t", "stray.dll": make_pe(exports=("h",))})
        launch, _ = self.deploy()
        self.assertEqual(sorted(os.listdir(self.deploy_dir / "native" / "mods")), ["fancy.dll", "fancyutil.dll"])
        self.assertEqual((launch["parts"]["native"]["mods"], launch["parts"]["native"]["helpers"]),
                         (["fancy.dll"], ["fancyutil.dll"]))
        self.assertIn("with 1 helper DLL(s)", " ".join(mm.describe_parts(launch)))

    def test_same_helper_in_two_mods(self):
        self.build_host()
        self.mod("One", {"one.dll": make_pe(exports=("swmod_init",)), "util.dll": make_pe(exports=("a",))})
        self.mod("Two", {"two.dll": make_pe(exports=("swmod_init",)), "util.dll": make_pe(exports=("a",))})
        launch, _ = self.deploy()
        self.assertNotIn("util.dll", " ".join(launch["attention"]))  # (the same file: nothing to say)
        self.mod("Three", {"three.dll": make_pe(exports=("swmod_init",)), "util.dll": make_pe(exports=("b",))})
        launch, _ = self.deploy()
        self.assertIn("both have util.dll: Three's is used", " ".join(launch["attention"]))


class StaleDeploys(RunBase):
    def test_moved_library_is_caught_by_status_and_run(self):
        self.mod("Romfs", {"romfs/Pack/Title.txt": "t"})
        self.mod("Data", {"exefs/data.pchtxt": DATA_PATCH})
        self.deploy()
        moved = self.libdir.parent / "modloader-moved"
        os.rename(self.libdir, moved)
        code, out = quiet(mm.main, ["status", "--library", str(moved)])
        self.assertIn("BROKEN", out)
        self.assertNotIn("up to date", out)
        code, out = quiet(mm.main, ["run", "--library", str(moved)])
        self.assertEqual(code, 2, out)
        self.assertIn("Run: python tools/modman.py deploy", out)
        self.assertEqual(FakeProc.started, [])
        h_moved = mm.inputs_hash(mm.Library.load(str(moved)))  # (the same files, in another place)
        os.rename(moved, self.libdir)
        self.assertNotEqual(h_moved, mm.inputs_hash(self.lib))
        code, out = quiet(mm.main, ["status", "--library", str(self.libdir)])
        self.assertIn("up to date", out)

    def test_dlc_set_up_after_the_deploy(self):
        self.mod("Data", {"exefs/data.pchtxt": DATA_PATCH})
        self.deploy()
        write(self.install, {"extracted/aoc2/Map/MainField/Dlc2.txt": "dlc 2"})
        code, out = quiet(mm.main, ["status", "--library", str(self.libdir)])
        self.assertIn("OUT OF DATE", out)
        self.deploy()
        self.assertEqual(sorted(os.listdir(self.deploy_dir / "root")), ["aoc1", "aoc2", "exefs", "romfs"])
        code, out = quiet(mm.main, ["status", "--library", str(self.libdir)])
        self.assertIn("up to date", out)


class CheatNames(RunBase):
    def test_names_starting_with_a_dash_reach_cheats_py(self):
        import cheats as ch
        folder = CHEAT_PATH.rsplit("/", 1)[0]
        text = ("[-1 Heart]\n04000000 00001234 00000001\n\n[-Fast]\n04000000 00001238 00000001\n\n"
                "[--all]\n04000000 0000123C 00000001\n\n[Speed=2]\n04000000 00001240 00000002\n\n"
                "[Off]\n04000000 00001244 00000001\n")
        toggles = "[-Fast]\ntrue\n[-1 Heart]\ntrue\n[--all]\ntrue\n[Speed=2]\ntrue\n[Off]\nfalse\n"
        self.mod("Odd", {CHEAT_PATH: text, f"{folder}/toggles.txt": toggles})
        launch, _ = self.deploy()
        chosen = []
        real_select = ch.select

        def spy(*a, **kw):
            picked = real_select(*a, **kw)
            chosen.extend(c.name for c in picked)
            return picked

        argv = ["run", launch["cheats"]["file"], "--port", "7777", *launch["cheats"]["args"]]
        with mock.patch.object(ch, "select", side_effect=spy), \
                mock.patch.object(ch, "Control", side_effect=ch.ControlError("no game here")):
            code, out = quiet(ch.main, argv)  # (a SystemExit here would be argparse refusing modman's arguments)
        self.assertEqual(code, 1, out)
        self.assertEqual(chosen, ["-1 Heart", "-Fast", "--all", "Speed=2"])

    def test_cheats_that_stop_at_once_are_reported(self):
        self.mod("Cheats", {CHEAT_PATH: CHEATS_A})
        self.deploy()

        def proc(argv, **kw):
            p = FakeProc(argv, **kw)
            if mm.CHEATS_PY in argv:
                p.returncode = 2
            return p

        with mock.patch.object(mm.subprocess, "Popen", side_effect=proc):
            code, out = self.run_mm(port_open=(False, True))
        self.assertEqual(code, 1, out)
        self.assertIn("The cheats stopped right away (exit code 2)", out)
        self.assertNotIn("Started cheats", out)

    def test_port_must_be_a_real_port(self):
        self.mod("Cheats", {CHEAT_PATH: CHEATS_A})
        self.deploy()
        for bad in ("70000", "0", "-5", "abc"):
            with self.subTest(port=bad), contextlib.redirect_stderr(io.StringIO()) as err, \
                    self.assertRaises(SystemExit):
                self.run_mm("--port", bad)
            self.assertIn("--port", err.getvalue())
        self.assertEqual(FakeProc.started, [])
        with self.assertRaises(mm.ModmanError):
            mm.run(self.lib, port=70000)


class DryRunTellsTheTruth(Base):
    def test_no_rebuild_dry_run_after_a_rebuild(self):
        self.mod("Code", {"exefs/code.pchtxt": CODE_PATCH})
        self.deploy()
        out_lib = str(self.deploy_dir / "swgame.dll")
        launch, _ = self.deploy(dry_run=True, no_rebuild=True)  # (same patches: kept, even with --no-rebuild)
        self.assertEqual((launch["parts"]["exefs"]["rebuild"], launch["env"]["SAVAGEWIND_GAME_LIB"]), ("reuse", out_lib))
        self.mod("Code 2", {"exefs/code2.pchtxt": CODE_PATCH_2})
        dry, _ = self.deploy(dry_run=True, no_rebuild=True)
        self.assertEqual(dry["parts"]["exefs"]["rebuild"], "pending")
        self.assertEqual(dry["env"]["SAVAGEWIND_GAME_LIB"], str(self.extracted / "swgame.dll"))
        self.assertNotIn("need the game code rebuilt", mm.format_deploy(dry))  # (--no-rebuild never rebuilds)
        self.assertEqual(self.deploy(dry_run=True)[0]["parts"]["exefs"]["rebuild"], "build")
        real, _ = self.deploy(no_rebuild=True)
        self.assertEqual((real["parts"]["exefs"]["rebuild"], real["env"]), (dry["parts"]["exefs"]["rebuild"], dry["env"]))
        self.assertEqual(len(self.rebuilds), 1)


# ---------------------------------------------------------------- mod.toml

REPO = Path(__file__).resolve().parent.parent
H1, H2 = 0x7000000400, 0x7000000800    # function starts inside the made-up main's code (0x7000000000-0x7000001000)


def mod_toml(mod_id, name="Test Mod", version="1.0.0", *, dll=None, hooks=None, api=None, games=None, deps=None,
             data=None, extra="") -> str:
    """A mod.toml with these values (only the given ones)."""
    lines = ["[mod]", f'id = "{mod_id}"', f'name = "{name}"', f'version = "{version}"']
    if games is not None:
        lines.append(f"game_versions = {json.dumps(games)}")
    if api is not None:
        lines.append(f"loader_api = {api}")
    if dll or hooks is not None:
        lines.append("[code]")
        if dll:
            lines.append(f'windows = "{dll}"')
        if hooks is not None:
            lines.append(f"hooks = {json.dumps(hooks)}")
    if data:
        lines += ["[data]"] + [f'{k} = "{v}"' for k, v in data.items()]
    if deps:
        lines += ["[dependencies]"] + [f'"{k}" = "{v}"' for k, v in deps.items()]
    return "\n".join(lines) + "\n" + extra


def hook_toml(mod_id, hooks, dll, **kw) -> str:
    return mod_toml(mod_id, dll=dll, hooks=[f"{a:#x}" for a in hooks], api=2, games=["1.9.0"], **kw)


FULL_TOML = """
[mod]
id = "someone.full-mod"
name = "Full Mod"
version = "1.2.0-beta.1"
authors = ["someone", "someone else"]
description = "One line."
game_versions = ["1.9.0"]
loader_api = 2

[code]
windows = "bin\\\\full.dll"
linux = "bin/libfull.so"
hooks = ["0x7000cc7410", "main+0xcc7410", "0x7000000400"]

[data]
romfs = "./romfs/"
dlc_romfs = "01007EF00011F001/romfs"

[dependencies]
"someone.base" = ">=1.0"
"someone.exact" = "== 2.1"
"someone.any" = "*"
"""


class ManifestParsing(unittest.TestCase):
    def errors(self, text):
        man, errs = mm.parse_manifest(text)
        self.assertIsNone(man, text)
        return "\n".join(errs)

    def test_full_manifest(self):
        man, errs = mm.parse_manifest(FULL_TOML, "Wrap/")
        self.assertEqual(errs, [])
        self.assertEqual((man.id, man.name, man.version, man.authors, man.description),
                         ("someone.full-mod", "Full Mod", "1.2.0-beta.1", ["someone", "someone else"], "One line."))
        self.assertEqual((man.game_versions, man.loader_api, man.dll), (["1.9.0"], 2, "bin/full.dll"))
        self.assertEqual(man.hooks, [H1, 0x7000cc7410])  # (sorted, each once: main+0xcc7410 is the same address)
        self.assertEqual((man.romfs, man.dlc_romfs), ("romfs", "01007EF00011F001/romfs"))
        self.assertEqual(man.dependencies, {"someone.base": ">=1.0", "someone.exact": "== 2.1", "someone.any": "*"})
        self.assertEqual(man.rel(man.dll), "Wrap/bin/full.dll")
        self.assertEqual(man.notes, [])

    def test_minimal_manifest_and_defaults(self):
        man, errs = mm.parse_manifest(mod_toml("a.b"))
        self.assertEqual(errs, [])
        self.assertEqual((man.loader_api, man.game_versions, man.dll, man.hooks, man.dependencies),
                         (1, [], None, [], {}))

    def test_unknown_keys_are_notes_not_errors(self):
        man, errs = mm.parse_manifest(mod_toml("a.b", extra="[mod.extra]\n[other]\nx = 1\n"))
        self.assertEqual(errs, [])
        man, errs = mm.parse_manifest(mod_toml("a.b").replace('version = "1.0.0"', 'version = "1.0.0"\nlicense = "MIT"')
                                      + "[dependancies]\n")
        self.assertEqual(errs, [])
        self.assertEqual(man.notes, ["mod.toml: [dependancies] isn't something modman knows; ignored",
                                     "mod.toml: [mod] license isn't something modman knows; ignored"])

    def test_each_problem_has_its_own_message(self):
        cases = {
            "bad toml": ("[mod\nid = 1", "isn't valid TOML"),
            "no mod table": ('[code]\nwindows = "a.dll"\n', "has no [mod] table"),
            "mod not a table": ('mod = "x"\n', "has no [mod] table"),
            "no id": ('[mod]\nname = "x"\nversion = "1"\n', "[mod] id is missing"),
            "upper id": (mod_toml("Some.Mod"), '[mod] id = "Some.Mod" can\'t be used'),
            "id with space": (mod_toml("a b"), "can't be used"),
            "id ending in a dot": (mod_toml("a.b."), "can't be used"),
            "device name id": (mod_toml("con.mod"), "can't be used"),
            "build id as id": (mod_toml("cd57b23fa4bbad65"), "can't be used"),
            "no name": ('[mod]\nid = "a.b"\nversion = "1"\n', "[mod] name is missing"),
            "name not text": ('[mod]\nid = "a.b"\nname = 5\nversion = "1"\n', "[mod] name must be one line of text"),
            "no version": ('[mod]\nid = "a.b"\nname = "x"\n', "[mod] version is missing"),
            "bad version": (mod_toml("a.b", version="one"), '[mod] version = "one" isn\'t a version'),
            "authors": (mod_toml("a.b").replace("[mod]", '[mod]\nauthors = "me"'), "[mod] authors must be a list"),
            "game_versions text": (mod_toml("a.b").replace("[mod]", '[mod]\ngame_versions = "1.9.0"'),
                                   "[mod] game_versions must be a list"),
            "game_versions empty": (mod_toml("a.b", games=[]), "[mod] game_versions must be a list"),
            "game_versions bad": (mod_toml("a.b", games=["v1.9"]), "[mod] game_versions must be a list"),
            "loader_api text": (mod_toml("a.b", api='"2"'), "[mod] loader_api must be a whole number"),
            "loader_api zero": (mod_toml("a.b", api=0), "[mod] loader_api must be a whole number"),
            "dll not a dll": (mod_toml("a.b", dll="mod.so"), '[code] windows = "mod.so" must name a .dll'),
            "dll outside": (mod_toml("a.b", dll="../x.dll"), "must be a path inside the mod folder"),
            "dll absolute": (mod_toml("a.b", dll="C:/x.dll"), "must be a path inside the mod folder"),
            "hooks not a list": (mod_toml("a.b", dll="a.dll", api=2, games=["1.9.0"]).replace(
                'windows = "a.dll"', 'windows = "a.dll"\nhooks = "0x7000000400"'), "[code] hooks must be a list"),
            "hook not an address": (hook_toml("a.b", [], "a.dll").replace("hooks = []", 'hooks = ["stamina"]'),
                                    "entry 1: 'stamina' is not an address"),
            "hook not aligned": (hook_toml("a.b", [H1 + 2], "a.dll"), "isn't a multiple of 4"),
            "hook below main": (hook_toml("a.b", [0x1000], "a.dll"), "isn't in main"),
            "hooks without dll": (mod_toml("a.b", hooks=["0x7000000400"], api=2, games=["1.9.0"]),
                                  "[code] hooks need the mod's DLL"),
            "hooks with api 1": (mod_toml("a.b", dll="a.dll", hooks=["0x7000000400"], games=["1.9.0"]),
                                 "[code] hooks need mod API 2: set [mod] loader_api = 2"),
            "hooks without game_versions": (mod_toml("a.b", dll="a.dll", hooks=["0x7000000400"], api=2),
                                            "say which in [mod] game_versions"),
            "data outside": (mod_toml("a.b", data={"romfs": "../romfs"}), "[data] romfs"),
            "dependency id": (mod_toml("a.b", deps={"Not An Id": ">=1"}), "isn't a mod id"),
            "dependency rule": (mod_toml("a.b", deps={"c.d": "~1.0"}), '"c.d" = \'~1.0\': write ">=1.0"'),
            "dependency on itself": (mod_toml("a.b", deps={"a.b": ">=1"}), "names the mod itself"),
        }
        for name, (text, want) in cases.items():
            with self.subTest(name=name):
                self.assertIn(want, self.errors(text))

    def test_every_problem_is_listed_at_once(self):
        text = mod_toml("Bad Id", version="x", dll="a.so")
        self.assertEqual(len(mm.parse_manifest(text)[1]), 3)

    def test_versions_and_requirements(self):
        yes = [("1.0", ">=1.0"), ("1.0.0", "==1"), ("1.10", ">=1.2"), ("2.0.1-beta", ">=2.0"), ("0.1", "*"),
               (None, "*"), ("3", ">= 2.9.9")]
        no = [("0.9", ">=1.0"), ("1.0.1", "==1.0"), ("1.2", ">=1.10"), (None, ">=1.0"), (None, "==1.0")]
        for v, req in yes:
            with self.subTest(v=v, req=req):
                self.assertTrue(mm.satisfies(v, req))
        for v, req in no:
            with self.subTest(v=v, req=req):
                self.assertFalse(mm.satisfies(v, req))
        for bad in ("1.0", "~1", ">1", "<=2", ">=x", 5):
            self.assertIsNone(mm.parse_requirement(bad), bad)

    def test_found_at_the_root_or_in_one_wrapping_folder(self):
        self.assertEqual(mm.find_manifest(["mod.toml", "a.dll"]), "mod.toml")
        self.assertEqual(mm.find_manifest(["Wrap/Mod.TOML", "Wrap/bin/a.dll"]), "Wrap/Mod.TOML")
        self.assertIsNone(mm.find_manifest(["a/mod.toml", "b/x.dll"]))       # (not at the root of the mod)
        self.assertIsNone(mm.find_manifest(["romfs/mod.toml", "romfs/Pack/x"]))  # (romfs/ is the layout, not a wrapper)
        self.assertIsNone(mm.find_manifest(["romfs/Pack/x"]))

    def test_the_repo_examples(self):
        hello, errs, found = mm.read_manifest(str(REPO / "native" / "examples" / "hello"))
        self.assertEqual((errs, found), ([], True))
        self.assertEqual((hello.id, hello.dll, hello.hooks, hello.loader_api, hello.game_versions),
                         ("botw-mod-loader.hello", "hello.dll", [], 1, []))
        stamina, errs, _ = mm.read_manifest(str(REPO / "native" / "examples" / "stamina_hook"))
        self.assertEqual(errs, [])
        self.assertEqual((stamina.id, stamina.dll, stamina.loader_api, stamina.game_versions),
                         ("botw-mod-loader.stamina-hook", "stamina_hook.dll", 2, ["1.9.0"]))
        self.assertEqual(stamina.hooks, [0x7000cc7410])
        hooks_txt = REPO / "native" / "examples" / "stamina_hook" / "hooks.txt"
        self.assertEqual(stamina.hooks, gh.read_hooks_files(hooks_txt))
        # (the DLL names are the ones build_native.py builds, which is where modman takes them from)
        import build_native as bn
        built = {t.filename: t.subdir for t in (*bn.EXAMPLES, *bn.HOOK_EXAMPLES)}
        self.assertEqual((built.get("hello.dll"), built.get("stamina_hook.dll")), ("mods", "hook-mods"))

    def test_run_rebuild_passes_the_hooks_file(self):
        with mock.patch.object(mm.swr, "main", return_value=0) as main:
            mm.run_rebuild("I", "E", "O", "H")
            mm.run_rebuild("I", "E", "O")
        self.assertEqual([c.args[0] for c in main.call_args_list],
                         [["build", "I", "--exefs", "E", "--out", "O", "--hooks", "H"],
                          ["build", "I", "--exefs", "E", "--out", "O"]])

    def test_hooks_file_is_what_sw_rebuild_reads(self):
        text = mm.hooks_text({H1: ["A", "B"], 0x7000cc7410: ["C"]})
        self.assertEqual(gh.parse_hooks_text(text), [H1, 0x7000cc7410])
        self.assertIn("0x7000000400   # A, B", text)

    def test_rebuild_key_covers_the_hook_set(self):
        self.assertEqual(mm._with_hooks("k", []), "k")  # (a build without hooks keeps its old key)
        self.assertEqual(mm._with_hooks("k", [H2, H1]), mm._with_hooks("k", [H1, H2, H1]))
        self.assertNotEqual(mm._with_hooks("k", [H1]), mm._with_hooks("k", [H1, H2]))
        self.assertNotEqual(mm._with_hooks("k", [H1]), mm._with_hooks("other", [H1]))

    def test_modhost_api_is_swmod_h(self):
        header = (REPO / "native" / "modhost" / "swmod.h").read_text(encoding="utf-8")
        self.assertIn(f"#define SWMOD_API_VERSION {mm.MODHOST_API}\n", header)

    def test_manifest_ids_in_the_library(self):
        for good in ("a.b", "botw-mod-loader.stamina-hook", "x_y.z-1", "plain-slug"):
            self.assertTrue(mm.good_id(good), good)
        for bad in ("../a", "a..b", "a.", ".a", "con.x", "A.b", "a/b", "a\\b", "", None, "x" * 65):
            self.assertFalse(mm.good_id(bad), bad)


class ManifestAdd(Base):
    def native(self, name, toml, files=None, **kw):
        return self.mod(name, {"mod.toml": toml, "bin/mod.dll": make_pe(exports=("swmod_init",)), **(files or {})},
                        **kw)

    def refused(self, name, files, **kw):
        before = (self.lib.mods, sorted(os.listdir(self.libdir / "mods")) if (self.libdir / "mods").is_dir() else [])
        with self.assertRaises(mm.ModmanError) as cm:
            self.mod(name, files, **kw)
        self.assertEqual((self.lib.mods, sorted(os.listdir(self.libdir / "mods"))), before)  # (nothing left behind)
        return str(cm.exception)

    def test_id_name_and_version_come_from_mod_toml(self):
        e = self.native("Some Download", mod_toml("someone.better-sprint", "Better Sprint", "2.1", dll="bin/mod.dll"))
        self.assertEqual((e["id"], e["name"], e["version"], e["kinds"]),
                         ("someone.better-sprint", "Better Sprint", "2.1", ["native"]))
        self.assertTrue((self.libdir / "mods" / "someone.better-sprint" / "bin" / "mod.dll").is_file())
        self.assertEqual(self.lib.mods[0]["id"], "someone.better-sprint")  # (config.json takes the id)
        code, out = quiet(mm.main, ["list", "--library", str(self.libdir)])
        self.assertIn("someone.better-sprint  2.1  native  Better Sprint", out)
        self.assertEqual(self.lib.find("someone.better-sprint")[0], 0)

    def test_name_option_keeps_the_id(self):
        src = write(self.dl / "X", {"mod.toml": mod_toml("someone.x", "From Toml", dll="m.dll"),
                                    "m.dll": make_pe(exports=("swmod_init",))})
        e = quiet(mm.add_mod, self.lib, str(src), name="My Name")[0][0]
        self.assertEqual((e["id"], e["name"]), ("someone.x", "My Name"))

    def test_zip_with_a_wrapping_folder(self):
        e = self.mod("Zipped", {"Zipped Mod/mod.toml": mod_toml("someone.zipped", dll="bin/z.dll"),
                                "Zipped Mod/bin/z.dll": make_pe(exports=("swmod_init",))}, zip_it=True)
        self.assertEqual((e["id"], e["kinds"]), ("someone.zipped", ["native"]))

    def test_cli_add_says_what_it_read(self):
        src = write(self.dl / "Cli", {"mod.toml": hook_toml("someone.cli", [H1], "bin/mod.dll"),
                                      "bin/mod.dll": make_pe(exports=("swmod_init",))})
        code, out = quiet(mm.main, ["add", str(src), "--library", str(self.libdir)])
        self.assertEqual(code, 0, out)
        self.assertIn("Added #1 someone.cli 1.0.0 (native): Test Mod (from its mod.toml)", out)
        self.assertIn("hooks: 0x7000000400 (deploy rebuilds the game code with hook slots", out)

    def test_bad_mod_toml_refuses_the_mod(self):
        out = self.refused("Bad", {"mod.toml": mod_toml("Bad Id", version="x"),
                                   "bin/mod.dll": make_pe(exports=("swmod_init",))})
        self.assertIn("Not added: Bad's mod.toml can't be used:", out)
        self.assertIn('[mod] id = "Bad Id"', out)
        self.assertIn('[mod] version = "x"', out)

    def test_dll_problems(self):
        out = self.refused("Missing", {"mod.toml": mod_toml("a.missing", dll="bin/gone.dll"),
                                       "bin/mod.dll": make_pe(exports=("swmod_init",))})
        self.assertIn('[code] windows = "bin/gone.dll": no such file in the mod', out)
        out = self.refused("Helper", {"mod.toml": mod_toml("a.helper", dll="bin/util.dll"),
                                      "bin/util.dll": make_pe(exports=("f",)),
                                      "bin/mod.dll": make_pe(exports=("swmod_init",))})
        self.assertIn("doesn't export swmod_init, so modhost can't load it", out)
        out = self.refused("Old", {"mod.toml": mod_toml("a.old", dll="old.dll"),
                                   "old.dll": make_pe32(exports=("swmod_init",))})
        self.assertIn("isn't a 64-bit Windows DLL that exports swmod_init", out)

    def test_game_version_and_mod_api_are_checked(self):
        out = self.refused("Old Game", {"mod.toml": mod_toml("a.old-game", games=["1.8.2"], dll="m.dll"),
                                        "m.dll": make_pe(exports=("swmod_init",))})
        self.assertIn("made for BotW 1.8.2 ([mod] game_versions), and your game is 1.9.0 (build cd57b23fa4bbad65)", out)
        out = self.refused("Future", {"mod.toml": mod_toml("a.future", api=mm.MODHOST_API + 1, dll="m.dll"),
                                      "m.dll": make_pe(exports=("swmod_init",))})
        self.assertIn(f"needs mod API {mm.MODHOST_API + 1} ([mod] loader_api), and modhost has mod API "
                      f"{mm.MODHOST_API}", out)
        e = self.mod("Both", {"mod.toml": mod_toml("a.both", games=["1.8.2", "1.9.0"], api=mm.MODHOST_API,
                                                   dll="m.dll"), "m.dll": make_pe(exports=("swmod_init",))})
        self.assertEqual(e["id"], "a.both")

    def test_hook_outside_mains_code_is_refused(self):
        out = self.refused("Far Hook", {"mod.toml": hook_toml("a.far", [0x7000cc7410], "m.dll"),
                                        "m.dll": make_pe(exports=("swmod_init",))})
        self.assertIn("[code] hooks: 0x7000cc7410 is not in main's code (0x7000000000-0x7000001000)", out)

    def test_the_same_id_twice_is_refused(self):
        self.native("One", mod_toml("someone.same", "One", "1.0", dll="bin/mod.dll"))
        with self.assertRaises(mm.ModmanError) as cm:
            self.native("Two", mod_toml("someone.same", "Two", "1.1", dll="bin/mod.dll"))
        self.assertIn("already has someone.same (One 1.0)", str(cm.exception))
        self.assertIn("python tools/modman.py remove someone.same", str(cm.exception))
        self.assertEqual(sorted(os.listdir(self.libdir / "mods")), ["someone.same"])
        write(self.libdir / "mods" / "someone.left", {"x.txt": "left over"})
        with self.assertRaises(mm.ModmanError) as cm:
            self.native("Three", mod_toml("someone.left", dll="bin/mod.dll"))
        self.assertIn("already exists, and the library doesn't list it", str(cm.exception))

    def test_data_folders(self):
        e = self.mod("Data", {"mod.toml": mod_toml("a.data", data={"romfs": "romfs",
                                                                  "dlc_romfs": "01007EF00011F001/romfs"}),
                              "romfs/Pack/Title.txt": "t", "01007EF00011F001/romfs/Map/MainField/New.txt": "d"})
        self.assertEqual(e["kinds"], ["romfs", "dlc-romfs"])
        self.assertNotIn("problems", e)  # (no "has a mod.toml, but no DLL" for a data-only mod.toml)
        e = self.mod("Nested", {"mod.toml": mod_toml("a.nested", data={"romfs": "files/romfs"}),
                                "files/romfs/Pack/Title.txt": "t"})
        self.assertEqual(e["kinds"], ["romfs"])
        out = self.refused("Gone", {"mod.toml": mod_toml("a.gone", data={"romfs": "romfs", "dlc_romfs": "dlc"}),
                                    "romfs/Pack/Title.txt": "t"})
        self.assertIn('[data] dlc_romfs = "dlc": no such folder in the mod', out)
        out = self.refused("Dlc Folder", {"mod.toml": mod_toml("a.dlc", data={"dlc_romfs": "dlc"}),
                                          "dlc/Map/MainField/New.txt": "d"})
        self.assertIn("reads the files there as base-game files", out)
        out = self.refused("Two Trees", {"mod.toml": mod_toml("a.trees", data={"romfs": "romfs",
                                                                              "dlc_romfs": "dlc/romfs"}),
                                         "romfs/Pack/Title.txt": "t", "dlc/romfs/Map/MainField/New.txt": "d"})
        self.assertIn("[data]: Two Trees holds several RomFS trees", out)
        out = self.refused("Elsewhere", {"mod.toml": mod_toml("a.elsewhere", data={"romfs": "files"}),
                                         "files/readme.txt": "x", "romfs/Pack/Title.txt": "t"})
        self.assertIn("romfs/Pack/Title.txt: a RomFS file outside the folder [data] romfs names", out)

    def test_dependencies_are_noted_at_add(self):
        self.native("Base", mod_toml("someone.base", version="0.9", dll="bin/mod.dll"))
        _, scan, notes = quiet(mm.add_mod, self.lib, str(write(self.dl / "Top", {
            "mod.toml": mod_toml("someone.top", dll="bin/mod.dll", deps={"someone.base": ">=1.0",
                                                                         "someone.other": "*"}),
            "bin/mod.dll": make_pe(exports=("swmod_init",))})))[0]
        self.assertIn("needs someone.base >=1.0, and the library has version 0.9 (deploy refuses until then)", notes)
        self.assertIn("needs someone.other, which isn't in the library: add it (deploy refuses until then)", notes)
        self.assertEqual(self.lib.mods[1]["id"], "someone.top")  # (added all the same)

    def test_mods_without_mod_toml_are_unchanged(self):
        e = self.mod("Hello", {"hello.dll": make_pe(exports=("swmod_init",)), "romfs/Pack/Title.txt": "t"})
        self.assertEqual((e["id"], e["name"], e["kinds"]), ("hello", "Hello", ["romfs", "native"]))
        self.assertNotIn("version", e)
        self.assertEqual(set(e), {"id", "name", "enabled", "kinds", "source", "added"})
        code, out = quiet(mm.main, ["list", "--library", str(self.libdir)])
        self.assertIn("hello  -  romfs, native  Hello", out)


class ExampleMods(Base):
    """native/examples/<x>: added straight from the repo, with the DLL build_native.py built (made up here)."""

    def setUp(self):
        super().setUp()
        real = mm.game_info  # (the made-up main is too small for the real stamina address: no layout check)
        p = mock.patch.object(mm, "game_info", lambda inst: dataclasses.replace(real(inst), layouts={}))
        p.start()
        self.addCleanup(p.stop)

    def build_examples(self):
        self.build_host()
        write(self.host_dir, {"mods/hello.dll": make_pe(exports=("swmod_init",)),
                              "hook-mods/stamina_hook.dll": make_pe(exports=("swmod_init", "stamina"))})

    def test_add_and_deploy_both_examples(self):
        self.build_examples()
        for x in ("hello", "stamina_hook"):
            code, out = quiet(mm.main, ["add", str(REPO / "native" / "examples" / x), "--library", str(self.libdir)])
            self.assertEqual(code, 0, out)
            self.assertIn("taken from", out)
        self.assertEqual([(m["id"], m["version"], m["kinds"]) for m in self.lib.mods],
                         [("botw-mod-loader.hello", "1.0.0", ["native"]),
                          ("botw-mod-loader.stamina-hook", "1.0.0", ["native"])])
        stored = self.libdir / "mods" / "botw-mod-loader.stamina-hook" / "stamina_hook.dll"
        self.assertEqual(stored.read_bytes(), (self.host_dir / "hook-mods" / "stamina_hook.dll").read_bytes())
        self.assertFalse((REPO / "native" / "examples" / "stamina_hook" / "stamina_hook.dll").exists())
        launch, out = self.deploy()
        self.assertEqual(self.rebuild_hooks, [[0x7000cc7410]])
        self.assertEqual(self.rebuilds, [(str(self.install), str(self.extracted / "exefs"),
                                          str(self.deploy_dir / "swgame.dll"))])
        self.assertEqual(sorted(os.listdir(self.deploy_dir / "native" / "mods")), ["hello.dll", "stamina_hook.dll"])
        self.assertEqual(launch["env"]["SWMOD_REAL_GAME_LIB"], str(self.deploy_dir / "swgame.dll"))
        self.assertEqual(launch["parts"]["hooks"]["by_addr"], {"0x7000cc7410": ["Stamina Hook (example)"]})

    def test_example_not_built_yet(self):
        with self.assertRaises(mm.ModmanError) as cm:
            quiet(mm.add_mod, self.lib, str(REPO / "native" / "examples" / "stamina_hook"))
        self.assertIn("stamina_hook.dll isn't built yet. Run python tools/build_native.py build", str(cm.exception))
        self.assertEqual(self.lib.mods, [])

    def test_only_repo_examples_take_the_built_dll(self):
        self.build_examples()
        src = write(self.dl / "Copy", {"mod.toml": mod_toml("someone.copy", dll="hello.dll")})
        with self.assertRaises(mm.ModmanError) as cm:
            quiet(mm.add_mod, self.lib, str(src))
        self.assertIn('[code] windows = "hello.dll": no such file in the mod', str(cm.exception))
        with mock.patch.object(mm, "EXAMPLES_DIR", str(self.dl)):
            e, _, notes = quiet(mm.add_mod, self.lib, str(src))[0]
        self.assertEqual(e["kinds"], ["native"])
        self.assertTrue(any("taken from" in n for n in notes))


class Hooks(RunBase):
    def hook_mod(self, name, mod_id, hooks, **kw):
        dll = f"{mod_id.split('.')[-1]}.dll"
        return self.mod(name, {"mod.toml": hook_toml(mod_id, hooks, dll, name=name, **kw),
                               dll: make_pe(exports=("swmod_init",))})

    def hooks_file(self):
        return gh.read_hooks_files(self.deploy_dir / "native" / "hooks.txt")

    def test_union_of_hooks_rebuilds_without_code_patches(self):
        self.build_host()
        self.hook_mod("A", "test.a", [H1])
        self.hook_mod("B", "test.b", [H2, H1])
        self.mod("Plain", {"plain.dll": make_pe(exports=("swmod_init",))})
        launch, out = self.deploy()
        out_lib = str(self.deploy_dir / "swgame.dll")
        self.assertEqual(self.rebuilds, [(str(self.install), str(self.extracted / "exefs"), out_lib)])
        self.assertEqual(self.rebuild_hooks, [[H1, H2]])
        self.assertEqual(self.hooks_file(), [H1, H2])
        self.assertIn("hook slots for 2 function(s)", out)
        self.assertEqual(launch["argv"], [str(self.install / "savagewind.exe")])  # (no ExeFS patch: no alt root)
        nat = self.deploy_dir / "native"
        self.assertEqual(launch["env"], {"SAVAGEWIND_GAME_LIB": str(nat / "modhost.dll"),
                                         "SWMOD_REAL_GAME_LIB": out_lib, "SWMOD_DIR": str(nat / "mods")})
        self.assertEqual((nat / "modhost.dll.id").read_text(), "test-id\n")
        k = launch["parts"]["hooks"]
        self.assertEqual((k["by_addr"], k["mods"], k["rebuild"], k["library"]),
                         ({"0x7000000400": ["A", "B"], "0x7000000800": ["B"]}, ["A", "B"], "build", out_lib))
        text = mm.format_deploy(launch)
        self.assertIn("Hooks: 2 function(s) for 2 mod(s) (game code rebuilt with hook slots)", text)
        self.assertIn("0x7000000400: A, B", text)
        self.assertIn("0x7000000800: B", text)
        self.assertNotIn("exefs", launch["parts"])
        self.assertIn("# A, B", (nat / "hooks.txt").read_text())

    def test_rebuilt_library_is_kept_while_exefs_and_hooks_are_the_same(self):
        self.build_host()
        self.hook_mod("A", "test.a", [H1])
        self.deploy()
        launch, _ = self.deploy()
        self.assertEqual(len(self.rebuilds), 1)
        self.assertEqual(launch["parts"]["hooks"]["rebuild"], "reuse")
        self.assertEqual(launch["env"]["SWMOD_REAL_GAME_LIB"], str(self.deploy_dir / "swgame.dll"))
        dry, _ = self.deploy(dry_run=True)
        self.assertEqual(dry["parts"]["hooks"]["rebuild"], "reuse")
        self.assertNotIn("10-20 minutes", mm.format_deploy(dry))
        self.mod("Romfs", {"romfs/Pack/Title.txt": "t"})  # (no effect on the game code)
        self.deploy()
        self.assertEqual(len(self.rebuilds), 1)
        self.hook_mod("B", "test.b", [H2])               # (another hook set)
        self.assertEqual(self.deploy(dry_run=True)[0]["parts"]["hooks"]["rebuild"], "build")
        self.deploy()
        self.assertEqual(self.rebuild_hooks, [[H1], [H1, H2]])
        quiet(mm.main, ["disable", "test.b", "--library", str(self.libdir)])
        self.deploy()
        self.assertEqual(self.rebuild_hooks, [[H1], [H1, H2], [H1]])
        quiet(mm.main, ["disable", "test.a", "--library", str(self.libdir)])
        launch, _ = self.deploy()  # (no hooks, no code patches: the rebuilt library goes)
        self.assertEqual(len(self.rebuilds), 3)
        self.assertFalse((self.deploy_dir / "swgame.dll").exists())
        self.assertFalse((self.deploy_dir / "native" / "hooks.txt").exists())
        self.assertNotIn("hooks", launch["parts"])

    def test_hooks_and_code_patches_share_one_rebuild(self):
        self.build_host()
        self.mod("Code", {"exefs/code.pchtxt": CODE_PATCH})
        self.deploy()
        self.assertEqual(self.rebuild_hooks, [None])
        self.hook_mod("A", "test.a", [H1])
        launch, _ = self.deploy()  # (the code patches are the same, the hook set isn't: rebuilt)
        root_exefs = str(self.deploy_dir / "root" / "exefs")
        self.assertEqual([r[1] for r in self.rebuilds], [root_exefs, root_exefs])
        self.assertEqual(self.rebuild_hooks, [None, [H1]])
        self.assertEqual((launch["parts"]["exefs"]["rebuild"], launch["parts"]["hooks"]["rebuild"]), ("build", "build"))
        self.assertEqual(launch["env"]["SWMOD_REAL_GAME_LIB"], str(self.deploy_dir / "swgame.dll"))
        self.assertEqual(launch["argv"][1:], [str(self.deploy_dir / "root"), "--play"])
        self.mod("Code 2", {"exefs/code2.pchtxt": CODE_PATCH_2})
        self.deploy()  # (other patches, same hooks: rebuilt)
        self.assertEqual(self.rebuild_hooks, [None, [H1], [H1]])
        quiet(mm.main, ["disable", "test.a", "--library", str(self.libdir)])
        launch, _ = self.deploy()  # (back to code patches only)
        self.assertEqual(self.rebuild_hooks, [None, [H1], [H1], None])
        self.assertNotIn("hooks", launch["parts"])

    def test_hooks_with_a_data_patch_build_from_the_patched_exefs(self):
        self.build_host()
        self.mod("Data", {"exefs/data.pchtxt": DATA_PATCH})
        self.hook_mod("A", "test.a", [H1])
        launch, _ = self.deploy()
        self.assertEqual(self.rebuilds[0][1], str(self.deploy_dir / "root" / "exefs"))
        self.assertEqual((launch["parts"]["exefs"]["rebuild"], launch["parts"]["hooks"]["rebuild"]), ("none", "build"))
        self.assertEqual(launch["env"]["SWMOD_REAL_GAME_LIB"], str(self.deploy_dir / "swgame.dll"))
        self.assertIn("data only, no rebuild needed", " ".join(mm.describe_parts(launch)))
        launch, _ = self.deploy(dry_run=True)
        self.assertEqual(launch["parts"]["hooks"]["rebuild"], "reuse")
        self.assertEqual(len(self.rebuilds), 1)

    def test_no_rebuild_leaves_the_hooks_inactive(self):
        self.build_host()
        self.hook_mod("A", "test.a", [H1])
        launch, out = self.deploy(no_rebuild=True)
        self.assertEqual(self.rebuilds, [])
        self.assertEqual(launch["parts"]["hooks"]["rebuild"], "pending")
        self.assertEqual(launch["env"]["SWMOD_REAL_GAME_LIB"], str(self.extracted / "swgame.dll"))
        warn = " ".join(launch["attention"])
        self.assertIn("The hooks of A do nothing", warn)
        self.assertIn("--no-rebuild", warn)
        self.assertIn("this game code has no hook slots", warn)
        self.assertEqual(self.hooks_file(), [H1])  # (written all the same, for a rebuild by hand)
        self.assertIn("NOT active", " ".join(mm.describe_parts(launch)))
        code, out = self.run_mm()
        self.assertEqual(code, 0, out)
        self.assertIn("Warning: the hooks are not active", out)
        self.assertIn("no hook slots", out)
        self.deploy()  # (without --no-rebuild: now it is built)
        self.assertEqual(self.rebuild_hooks, [[H1]])
        # deploy --help said it skips the rebuild only "for code patches"
        deploy = next(a for a in mm.parser()._subparsers._group_actions[0].choices["deploy"]._actions
                      if "--no-rebuild" in a.option_strings)
        self.assertIn("hooks", deploy.help)

    def test_dry_run_says_a_rebuild_is_coming(self):
        self.build_host()
        self.hook_mod("A", "test.a", [H1])
        launch, _ = self.deploy(dry_run=True)
        self.assertEqual(launch["parts"]["hooks"]["rebuild"], "build")
        self.assertIn("The hooks need the game code rebuilt with hook slots: that takes 10-20 minutes",
                      mm.format_deploy(launch))
        self.assertEqual(launch["env"]["SWMOD_REAL_GAME_LIB"], str(self.deploy_dir / "swgame.dll"))
        self.assertFalse(self.deploy_dir.exists())
        self.assertEqual(self.rebuilds, [])

    def test_hooks_need_modhost(self):
        self.hook_mod("A", "test.a", [H1])
        launch, _ = self.deploy()
        self.assertEqual(self.rebuilds, [])
        self.assertNotIn("hooks", launch["parts"])
        self.assertIn("build_native.py build", " ".join(launch["attention"]))

    def test_disabled_hook_mods_ask_for_nothing(self):
        self.build_host()
        self.hook_mod("A", "test.a", [H1])
        self.hook_mod("B", "test.b", [H2])
        quiet(mm.main, ["disable", "test.b", "--library", str(self.libdir)])
        self.deploy()
        self.assertEqual(self.rebuild_hooks, [[H1]])

    def test_failed_rebuild_names_the_hooks(self):
        self.build_host()
        self.hook_mod("A", "test.a", [H1])
        with mock.patch.object(mm, "run_rebuild", return_value=1), self.assertRaises(mm.ModmanError) as cm:
            self.deploy()
        self.assertIn("everything except the hooks", str(cm.exception))

    def test_status_and_run_show_the_hooks(self):
        self.build_host()
        self.hook_mod("A", "test.a", [H1])
        self.deploy()
        code, out = quiet(mm.main, ["status", "--library", str(self.libdir)])
        self.assertIn("up to date", out)
        self.assertIn("Hooks: 1 function(s) for 1 mod(s)", out)
        code, out = self.run_mm()
        self.assertEqual(code, 0, out)
        self.assertEqual(FakeProc.started[0].kw["env"]["SWMOD_REAL_GAME_LIB"], str(self.deploy_dir / "swgame.dll"))
        self.assertNotIn("Warning", out)


class ManifestRefusals(Base):
    """deploy refuses mod.toml mods it can't run, before anything is deleted."""

    def native(self, name, toml):
        src = write(self.dl / name, {"mod.toml": toml, "m.dll": make_pe(exports=("swmod_init",))})
        return quiet(mm.add_mod, self.lib, str(src), name=name)[0][0]

    def good_deploy(self):
        self.mod("Good", {"romfs/Pack/Title.txt": "good"})
        self.deploy()

    def refused(self, *want):
        with self.assertRaises(mm.ModmanError) as cm:
            self.deploy()
        msg = str(cm.exception)
        self.assertIn("Can't deploy", msg)
        for w in want:
            self.assertIn(w, msg)
        self.assertEqual((self.deploy_dir / "overlay" / "Pack" / "Title.txt").read_text(), "good")
        mm.read_launch(self.lib)  # (the last deploy is still there)
        return msg

    def test_dependencies(self):
        self.good_deploy()
        self.build_host()
        self.native("Top", mod_toml("test.top", dll="m.dll", deps={"test.base": ">=1.0"}))
        self.refused("Top needs test.base >=1.0, which isn't in the library: add it")
        self.native("Base", mod_toml("test.base", "Base", "0.9", dll="m.dll"))
        self.refused("Top needs test.base >=1.0, and the library has version 0.9")
        quiet(mm.main, ["disable", "test.base", "--library", str(self.libdir)])
        self.refused("which is turned off: python tools/modman.py enable test.base")
        quiet(mm.main, ["remove", "test.base", "--library", str(self.libdir)])
        self.native("Base", mod_toml("test.base", "Base", "1.0.0", dll="m.dll"))
        launch, _ = self.deploy()
        self.assertEqual([m["id"] for m in launch["mods"]], ["good", "test.top", "test.base"])
        self.assertEqual(launch["mods"][1]["version"], "1.0.0")

    def test_exact_and_any_version(self):
        self.good_deploy()
        self.native("Exact", mod_toml("test.exact", dll="m.dll", deps={"good": "==1.0"}))
        self.refused("needs good ==1.0, and the library has a mod without a mod.toml (no version)")
        quiet(mm.main, ["remove", "test.exact", "--library", str(self.libdir)])
        self.native("Any", mod_toml("test.any", dll="m.dll", deps={"good": "*"}))
        self.deploy()  # (a mod without a mod.toml meets "*")

    def test_mod_api_newer_than_modhost(self):
        self.good_deploy()
        future = mm.MODHOST_API + 1
        with mock.patch.object(mm, "MODHOST_API", future):  # (added while modhost had that API)
            self.native("Future", mod_toml("test.future", api=future, dll="m.dll"))
        self.refused(f"Future: needs mod API {future} ([mod] loader_api), and modhost has mod API "
                     f"{mm.MODHOST_API}: update botw-mod-loader")

    def test_game_version_changed_since_add(self):
        self.native("For 190", mod_toml("test.for190", games=["1.9.0"], dll="m.dll"))
        self.good_deploy()
        old_id = bytes.fromhex("de0b6ac4efc1dda7") + bytes(24)
        write(self.install, {"extracted/exefs/main": make_nso(old_id, SEGS)})
        self.refused("For 190: made for BotW 1.9.0 ([mod] game_versions), and your game is 1.8.2 (build "
                     "de0b6ac4efc1dda7)")

    def test_hook_address_outside_main_is_caught_at_deploy(self):
        self.good_deploy()
        self.build_host()
        real = mm.game_info
        with mock.patch.object(mm, "game_info", lambda inst: dataclasses.replace(real(inst), layouts={})):
            self.native("Far", hook_toml("test.far", [0x7000cc7410], "m.dll"))
        self.refused("Far: [code] hooks: 0x7000cc7410 is not in main's code")
        self.assertEqual(self.rebuilds, [])

    def test_broken_mod_toml_in_the_library(self):
        self.native("Fine", mod_toml("test.fine", dll="m.dll"))
        self.good_deploy()
        (self.libdir / "mods" / "test.fine" / "m.dll").unlink()
        self.refused('Fine: [code] windows = "m.dll": no such file in the mod')

    def test_disabled_mods_are_not_checked(self):
        self.native("Top", mod_toml("test.top", dll="m.dll", deps={"test.base": ">=1.0"}))
        quiet(mm.main, ["disable", "test.top", "--library", str(self.libdir)])
        self.deploy()

    def test_dry_run_key_matches_the_real_one_with_a_vanilla_backup(self):
        vanilla = (self.extracted / "exefs" / "main").read_bytes()
        patched = make_nso(MAIN_ID, [(0x0, b"\x11" * 0x1000), SEGS[1], SEGS[2]])
        write(self.install, {"extracted/exefs/main": patched, "mods/.vanilla/exefs/main": vanilla,
                             "mods/.vanilla/state.json": json.dumps(
                                 {"main_installed_sha256": hashlib.sha256(patched).hexdigest()})})
        self.mod("Code", {"exefs/code.pchtxt": CODE_PATCH, "exefs/data.pchtxt": DATA_PATCH})
        self.deploy()
        launch, _ = self.deploy(dry_run=True)
        self.assertEqual(launch["parts"]["exefs"]["rebuild"], "reuse")
        launch, _ = self.deploy()
        self.assertEqual(launch["parts"]["exefs"]["rebuild"], "reuse")
        self.assertEqual(len(self.rebuilds), 1)


class ManifestEdgeCases(Base):
    """Regression tests from the review of the mod.toml support."""

    DLL = make_pe(exports=("swmod_init",))

    def refused_add(self, name, files, **kw):
        before = (self.lib.mods, sorted(os.listdir(self.libdir / "mods")) if (self.libdir / "mods").is_dir() else [])
        with self.assertRaises(mm.ModmanError) as cm:
            self.mod(name, files, **kw)
        self.assertEqual((self.lib.mods, sorted(os.listdir(self.libdir / "mods"))), before)  # (nothing left behind)
        return str(cm.exception)

    def test_only_the_dll_mod_toml_names_is_loaded(self):
        self.build_host()
        e = self.mod("Two", {"mod.toml": hook_toml("a.two", [H1], "bin/main.dll"), "bin/main.dll": self.DLL,
                             "bin/debug/main_debug.dll": self.DLL, "bin/util.dll": make_pe(exports=("f",))})
        self.assertEqual(e["kinds"], ["native"])
        self.assertIn("bin/debug/main_debug.dll: exports swmod_init too, and mod.toml's [code] windows names "
                      "bin/main.dll: left out", " ".join(e["problems"]))
        launch, _ = self.deploy()
        nat = launch["parts"]["native"]
        self.assertEqual((nat["mods"], nat["helpers"]), (["main.dll"], ["util.dll"]))
        self.assertEqual(sorted(os.listdir(self.deploy_dir / "native" / "mods")), ["main.dll", "util.dll"])
        self.assertIn("main_debug.dll", " ".join(launch["attention"]))
        self.assertEqual(self.rebuild_hooks, [[H1]])
        # (without [code] windows, or without a mod.toml, every DLL that exports swmod_init is a mod, as before)
        self.mod("Data Toml", {"mod.toml": mod_toml("a.data-toml"), "x.dll": self.DLL, "y.dll": self.DLL})
        self.mod("Plain", {"p.dll": self.DLL, "q.dll": self.DLL})
        launch, _ = self.deploy()
        self.assertEqual(launch["parts"]["native"]["mods"], ["main.dll", "p.dll", "q.dll", "x.dll", "y.dll"])

    def test_mod_toml_in_a_folder_next_to_a_readme(self):
        self.build_host()
        e = self.mod("w", {"README.md": "read me", "MyMod/mod.toml": hook_toml("a.wrapped", [H1], "my.dll"),
                           "MyMod/my.dll": self.DLL}, zip_it=True)
        self.assertEqual((e["id"], e["version"]), ("a.wrapped", "1.0.0"))
        launch, _ = self.deploy()
        self.assertEqual(launch["parts"]["hooks"]["by_addr"], {"0x7000000400": ["Test Mod"]})
        self.assertEqual(self.rebuild_hooks, [[H1]])
        e = self.mod("Inner", {"Outer/readme.txt": "x", "Outer/Inner/mod.toml": mod_toml("a.inner", data={
            "romfs": "romfs"}), "Outer/Inner/romfs/Pack/Title.txt": "t"}, zip_it=True)
        self.assertEqual((e["id"], e["kinds"]), ("a.inner", ["romfs"]))
        self.assertEqual(mm.find_manifest(["README.md", "MyMod/mod.toml", "MyMod/x.dll"]), "MyMod/mod.toml")
        self.assertIsNone(mm.find_manifest(["readme.txt", "romfs/mod.toml", "romfs/Pack/x"]))
        self.assertIsNone(mm.find_manifest(["readme.txt", "Pack/mod.toml", "Pack/x"]))

    def test_mod_toml_that_cant_be_the_mods_own_is_refused(self):
        out = self.refused_add("Apart", {"Code/mod.toml": hook_toml("a.apart", [H1], "my.dll"), "Code/my.dll": self.DLL,
                                         "Other/x.dll": self.DLL}, zip_it=True)
        self.assertIn("Code/mod.toml wasn't read: a mod.toml goes at the root of the mod", out)
        out = self.refused_add("Pack", {"A/mod.toml": mod_toml("a.a", dll="a.dll"), "A/a.dll": self.DLL,
                                        "B/mod.toml": mod_toml("a.b", dll="b.dll"), "B/b.dll": self.DLL})
        self.assertIn("it has 2 mod.toml files (A/mod.toml, B/mod.toml) and none at its root", out)
        out = self.refused_add("In Romfs", {"romfs/mod.toml": mod_toml("a.r"), "romfs/Pack/Title.txt": "t"})
        self.assertIn("romfs/mod.toml wasn't read", out)
        # (a mod already in the library with such a layout is refused at deploy, before anything is deleted)
        self.mod("Good", {"romfs/Pack/Title.txt": "good"})
        self.deploy()
        write(self.libdir / "mods" / "good", {"Extra/mod.toml": mod_toml("a.extra")})
        with self.assertRaises(mm.ModmanError) as cm:
            self.deploy()
        self.assertIn("Extra/mod.toml wasn't read", str(cm.exception))
        self.assertEqual((self.deploy_dir / "overlay" / "Pack" / "Title.txt").read_text(), "good")

    def test_huge_numbers_and_deep_nesting_are_refused_not_a_crash(self):
        head = '[mod]\nid = "a.huge"\nname = "H"\nversion = "1.0"\n'
        cases = {
            "loader_api": (head + "loader_api = " + "9" * 5000 + "\n", "a number in it is far too long"),
            "hook": (head + "[code]\nhooks = [" + "7" * 5000 + "]\n", "a number in it is far too long"),
            "nesting": (head + "x = " + "[" * 50000 + "]" * 50000 + "\n", "nested far too deeply"),
            "version": (head.replace('"1.0"', '"' + "1" * 5000 + '"'), "isn't a version like"),
            "requirement": (head + '[dependencies]\n"x.y" = ">=' + "1" * 5000 + '"\n', 'write ">=1.0"'),
            "game version": (head + 'game_versions = ["1.' + "9" * 5000 + '.0"]\n', "game_versions must be a list"),
        }
        for name, (text, want) in cases.items():
            with self.subTest(name=name):
                man, errs = mm.parse_manifest(text)
                self.assertIsNone(man)
                self.assertIn(want, "\n".join(errs))
                self.assertLess(len("\n".join(errs)), 1000)  # (long values are cut short in the message)
        src = write(self.dl / "Huge", {"mod.toml": cases["loader_api"][0], "m.dll": self.DLL})
        code, out = quiet(mm.main, ["add", str(src), "--library", str(self.libdir)])
        self.assertEqual(code, 2, out)
        self.assertIn("Not added: Huge's mod.toml can't be used", out)
        self.assertEqual(os.listdir(self.libdir / "mods"), [])
        self.assertEqual(mm.version_key("1" * 5000), ())  # (a hand-edited config.json: no crash)
        self.assertFalse(mm.satisfies("1" * 5000, ">=1.0"))
        cfg = json.loads((self.libdir / "config.json").read_text(encoding="utf-8"))
        cfg["mods"] = [{"id": "x", "name": "X", "enabled": True, "kinds": [], "version": "2" * 5000},
                       {"id": "y", "name": "Y", "enabled": True, "kinds": [], "version": 3}]
        (self.libdir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual([m.get("version") for m in self.lib.mods], [None, None])

    def test_line_breaks_in_names_never_reach_hooks_txt(self):
        for ch in ("\u2028", "\u2029", "\x85", "\x1c", "\t", "\x7f"):
            with self.subTest(ch=repr(ch)):
                text = hook_toml("a.evil", [H1], "m.dll").replace('name = "Test Mod"',
                                                                  f'name = "Evil\\u{ord(ch):04x}x"')
                man, errs = mm.parse_manifest(text)
                self.assertIsNone(man)
                self.assertIn("[mod] name must be one line of text", "\n".join(errs))
        text = mod_toml("a.b").replace("[mod]", '[mod]\ndescription = "a\\u2028b"\nauthors = ["x\\u2029y"]')
        self.assertEqual(len(mm.parse_manifest(text)[1]), 2)
        self.assertEqual(gh.parse_hooks_text(mm.hooks_text({H1: ["Evil\u20280x7000000804", "B\r\n0x7000000808"]})),
                         [H1])
        self.build_host()  # (a name from --name, or a hand-edited config.json, is made one line)
        src = write(self.dl / "Evil", {"mod.toml": hook_toml("a.evil", [H1], "m.dll"), "m.dll": self.DLL})
        e = quiet(mm.add_mod, self.lib, str(src), name="Evil\u20280x7000000804")[0][0]
        self.assertEqual(e["name"], "Evil 0x7000000804")
        lib = self.lib
        lib.mods[0]["name"] = "Evil\u20290x7000000808"
        lib.save()
        self.assertEqual(self.lib.mods[0]["name"], "Evil 0x7000000808")
        launch, _ = self.deploy()
        self.assertEqual(self.rebuild_hooks, [[H1]])
        self.assertEqual(list(launch["parts"]["hooks"]["by_addr"]), ["0x7000000400"])

    def test_romfs_folders_at_the_mods_root(self):
        e = self.mod("Root Data", {"mod.toml": mod_toml("a.root-data", data={"romfs": "."}), "Pack/Title.txt": "t",
                                   "readme.txt": "x"})
        self.assertEqual(e["kinds"], ["romfs"])
        launch, _ = self.deploy()
        self.assertEqual((self.deploy_dir / "overlay" / "Pack" / "Title.txt").read_text(), "t")
        e = self.mod("Wrapped", {"Wrap/mod.toml": mod_toml("a.wrapped-data", data={"romfs": "./"}),
                                 "Wrap/Actor/Pack/Hero.txt": "h"}, zip_it=True)
        self.assertEqual(e["kinds"], ["romfs"])
        self.assertEqual(mm.parse_manifest(mod_toml("a.b", data={"romfs": "."}))[0].romfs, ".")
        for bad in ("..", "/", "C:/"):
            with self.subTest(bad=bad):
                errs = "\n".join(mm.parse_manifest(mod_toml("a.b", data={"romfs": bad}))[1])
                self.assertIn('must be a path inside the mod folder, like "romfs" (or ".", the folder mod.toml is in)',
                              errs)
        errs = "\n".join(mm.parse_manifest(mod_toml("a.b", dll="."))[1])  # (only [data] folders can be ".")
        self.assertIn('[code] windows = "." must be a path inside the mod folder, like "bin/my_mod.dll"', errs)
        self.assertNotIn('(or "."', errs)
        out = self.refused_add("Root Dlc", {"mod.toml": mod_toml("a.root-dlc", data={"dlc_romfs": "."}),
                                            "Map/MainField/New.txt": "d"})
        self.assertIn("reads the files there as base-game files", out)

    def test_dependency_on_a_mod_that_is_off_is_found_by_its_mod_toml_id(self):
        self.build_host()
        e = self.mod("Base Lib", {"base.dll": self.DLL})  # (added without a mod.toml: library id from its name)
        self.assertEqual(e["id"], "base-lib")
        write(self.libdir / "mods" / "base-lib", {"mod.toml": mod_toml("someone.base", version="1.0")})
        quiet(mm.main, ["disable", "base-lib", "--library", str(self.libdir)])
        _, _, notes = quiet(mm.add_mod, self.lib, str(write(self.dl / "Top", {
            "mod.toml": mod_toml("someone.top", dll="t.dll", deps={"someone.base": ">=1.0"}), "t.dll": self.DLL})))[0]
        want = "needs someone.base >=1.0, which is turned off: python tools/modman.py enable base-lib"
        self.assertIn(f"{want} (deploy refuses until then)", notes)
        with self.assertRaises(mm.ModmanError) as cm:
            self.deploy()
        self.assertIn(f"Test Mod {want}", str(cm.exception))
        with self.assertRaises(mm.ModmanError) as cm:  # (and it can't be added a second time)
            self.mod("Base Again", {"mod.toml": mod_toml("someone.base", version="1.1", dll="b.dll"),
                                    "b.dll": self.DLL})
        self.assertIn("already has someone.base (Base Lib 1.0, library id base-lib)", str(cm.exception))
        self.assertIn("python tools/modman.py remove base-lib", str(cm.exception))
        quiet(mm.main, ["enable", "base-lib", "--library", str(self.libdir)])
        launch, _ = self.deploy()
        self.assertEqual(launch["parts"]["native"]["mods"], ["base.dll", "t.dll"])

    def test_dependency_version_comes_from_the_files(self):
        e = self.mod("Base", {"base.dll": self.DLL})  # (config.json has no version for it)
        write(self.libdir / "mods" / e["id"], {"mod.toml": mod_toml("base", version="2.0")})
        _, _, notes = quiet(mm.add_mod, self.lib, str(write(self.dl / "Top", {
            "mod.toml": mod_toml("someone.top", dll="t.dll", deps={"base": ">=1.0"}), "t.dll": self.DLL})))[0]
        self.assertEqual([n for n in notes if "needs" in n], [])


# ---------------------------------------------------------------- cheats: one group per mod in the merged file

class CheatGroups(unittest.TestCase):
    """merge_cheats puts each mod's cheats in a group of their own (a "// group: <mod>" line), with the mod's own
    master code, and cheats.py starts each group from fresh registers: no mod sees another mod's registers."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="modman-cheats-"))
        self.addCleanup(lambda: mm.remove_tree(str(self.tmp)))

    def merge(self, *mods, toggles=None):
        items = []
        for k, (name, text) in enumerate(mods):
            p = write(self.tmp / str(k), {f"{BID}.txt": text, **({"toggles.txt": toggles[name]}
                                                                  if toggles and name in toggles else {})})
            items.append((name, str(p / f"{BID}.txt")))
        return mm.merge_cheats(items)

    def run_tick(self, merged, names=None):
        """One cheats.py tick of the merged file on made-up memory (--all, or --enable names)."""
        import cheats as ch
        path = self.tmp / "merged" / f"{BID}.txt"
        write(path.parent, {path.name: merged.text})
        cf = ch.load(path)
        mem = ch.FakeMemory()
        ch.CheatVM(mem, say=lambda s: None).tick(ch.select(cf, names, names is None))
        return mem

    def test_a_later_mod_doesnt_see_the_registers_an_earlier_one_left(self):
        a = "[A: max arrows]\n40000000 00000000 00000100\n04000000 00200000 000003E7\n"
        b = "[B: 20 hearts]\n04000000 00123450 00000050\n"
        mem = self.run_tick(self.merge(("Mod A", a), ("Mod B", b)))
        main = mm.ch.MAIN_BASE
        self.assertEqual(mem.get(main + 0x200100), 0x3E7)
        self.assertEqual((mem.get(main + 0x123450), mem.get(main + 0x123550)), (0x50, 0))

    def test_a_later_mods_master_runs_only_for_its_own_cheats(self):
        c = "[C: hearts]\n04000000 00123450 00000050\n"
        d = "{D master}\n40000000 00000000 00000200\n\n[D: thing]\n04000000 00300000 00000001\n"
        merged = self.merge(("Mod C", c), ("Mod D", d))
        self.assertEqual((merged.mods, merged.attention), (["Mod C", "Mod D"], []))
        mem = self.run_tick(merged)
        main = mm.ch.MAIN_BASE
        self.assertEqual((mem.get(main + 0x123450), mem.get(main + 0x123650)), (0x50, 0))
        self.assertEqual(mem.get(main + 0x300200), 1)

    def test_each_mod_keeps_its_own_copy_of_the_same_master(self):
        master = "{Master}\n40000000 00000000 00000010\n\n"          # r0 = 0x10
        a = master + "[X]\n40000000 00000000 00000500\n04000000 00001000 00000001\n"   # r0 = 0x500 afterwards
        b = master + "[Y]\n04000000 00002000 00000002\n"
        merged = self.merge(("A", a), ("B", b))
        self.assertEqual(merged.text.count("{Master}"), 2)
        mem = self.run_tick(merged)
        main = mm.ch.MAIN_BASE
        self.assertEqual(mem.get(main + 0x1500), 1)
        self.assertEqual((mem.get(main + 0x2010), mem.get(main + 0x2500)), (2, 0))   # (B's r0 is B's master's)
        mem = self.run_tick(merged, ["Y"])                               # (and A's group doesn't run at all)
        self.assertEqual((mem.get(main + 0x1500), mem.get(main + 0x2010)), (0, 2))

    def test_the_merged_file_reads_like_cheat_files_one_after_another(self):
        merged = self.merge(("Mod A", CHEATS_A), ("Mod  B\n", CHEATS_B))
        lines = merged.text.splitlines()
        self.assertEqual(lines[0], "// Made by modman (botw-mod-loader) from your enabled cheat mods: Mod A, Mod B")
        body = lines[lines.index("// group: Mod A"):]
        self.assertEqual(body, ["// group: Mod A", "{Master}", "580F0000 01E13ED0", "",
                                "[Infinite Stamina]", "04000000 00001234 00000064", "",
                                "[Fast]", "04000000 00001238 00000001", "",
                                "// group: Mod B", "{Master}", "580F0000 01E13ED8", "",
                                "[Other]", "04000000 00001300 00000001", ""])
        import cheats as ch
        found, warnings = ch.parse_text(merged.text)
        self.assertEqual((warnings, [c.error for c in found]), ([], [None] * 5))

    def test_identical_cheats_with_the_same_master_are_kept_once(self):
        merged = self.merge(("A", CHEATS_A), ("A again", CHEATS_A))
        self.assertEqual((merged.names, merged.mods, merged.count), (["Infinite Stamina", "Fast"], ["A"], 2))
        self.assertEqual([ln for ln in merged.text.splitlines() if ln.startswith("// group:")], ["// group: A"])

    def test_a_mod_whose_master_cant_run_is_left_out(self):
        bad = "{Master}\n40010000 00000000 00000100\nB0000000\n\n[Set flag]\n04010000 00000200 00000001\n"
        for order in ((("Bad", bad), ("A", CHEATS_A)), (("A", CHEATS_A), ("Bad", bad))):
            with self.subTest(first=order[0][0]):
                merged = self.merge(*order)
                self.assertEqual(merged.mods, ["A"])
                self.assertNotIn("Set flag", merged.text)
                self.assertEqual(len(merged.attention), 1)
                self.assertIn("Bad: its cheats were left out. Its master code can't run (unknown opcode B",
                              merged.attention[0])

    def test_group_lines_in_a_mods_own_file_are_not_copied(self):
        already = "// group: Old\n[P]\n04000000 00000010 00000001\n// group: Older\n[Q]\n04000000 00000020 00000001\n"
        merged = self.merge(("Mine", already))
        self.assertEqual([ln for ln in merged.text.splitlines() if ln.startswith("// group:")], ["// group: Mine"])
        self.assertEqual(merged.names, ["P", "Q"])

    def test_toggles_still_choose_per_mod(self):
        merged = self.merge(("A", CHEATS_A), ("B", CHEATS_B), toggles={"A": "[Fast]\ntrue\n"})
        self.assertEqual(merged.enable, ["Fast", "Other"])
        mem = self.run_tick(merged, merged.enable)
        main = mm.ch.MAIN_BASE
        self.assertEqual((mem.get(main + 0x1234), mem.get(main + 0x1238), mem.get(main + 0x1300)), (0, 1, 1))


class CheatFolderInAnyCase(Base):
    """Cheats/, CHEATS/ and .TXT work on Windows and on the Switch's SD card, so modman takes them too."""

    def test_cheats_folder_and_file_name_in_any_case(self):
        for k, rel in enumerate(("Cheats/cd57b23fa4bbad65.txt", "cheats/cd57b23fa4bbad65.TXT",
                                 "atmosphere/contents/01007EF00011E000/CHEATS/CD57B23FA4BBAD65.TXT")):
            with self.subTest(rel=rel):
                e = self.mod(f"Case {k}", {rel: f"[Cheat {k}]\n04000000 00001234 0000000{k}\n"})
                self.assertEqual((e["kinds"], e.get("problems", [])), (["cheats"], []))
        launch, _ = self.deploy()
        self.assertEqual(launch["cheats"]["count"], 3)
        text = (self.deploy_dir / "cheats" / f"{BID}.txt").read_text(encoding="utf-8")
        for k in range(3):
            self.assertIn(f"[Cheat {k}]", text)

    def test_cheats_next_to_romfs_files_are_kept(self):
        e = self.mod("Both", {"romfs/Pack/Title.txt": "t", "Cheats/cd57b23fa4bbad65.txt": CHEATS_A})
        self.assertEqual(e["kinds"], ["romfs", "cheats"])

    def test_a_cheats_folder_passed_itself(self):
        src = write(self.dl / "Pack" / "Cheats", {f"{BID}.txt": CHEATS_A})
        e, _, _ = quiet(mm.add_mod, self.lib, str(src))[0]
        self.assertEqual(e["kinds"], ["cheats"])

    def test_a_lone_cheat_file_in_upper_case(self):
        p = write(self.dl, {BID.upper() + ".TXT": CHEATS_A})
        e, _, _ = quiet(mm.add_mod, self.lib, str(p / (BID.upper() + ".TXT")))[0]
        self.assertEqual(e["kinds"], ["cheats"])


if __name__ == "__main__":
    unittest.main()
