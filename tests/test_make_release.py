"""Tests for tools/make_release.py. Made-up files in temp folders only: tiny fake DLLs, fake game-file headers and
made-up user names. No game files, and the real repo is never zipped here."""
import contextlib
import gzip
import hashlib
import io
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import make_release as mr  # noqa: E402

HAVE_GIT = shutil.which("git") is not None
LOGIN = "alicefake"            # made-up login name for the leak checks
EMAIL = "alice.fake@example.invalid"
TERMS = [LOGIN, EMAIL]


def make_dll(exports=("swmod_init",), machine=0x8664, dll=True, extra=b"") -> bytes:
    """A minimal made-up PE32+ file: one section holding an export table (names only), then extra bytes."""
    va, roff, pe, opt_size = 0x1000, 0x200, 0x80, 240
    sec = bytearray(40 + 4 * len(exports))  # export directory + its name pointer array
    for i, n in enumerate(exports):
        struct.pack_into("<I", sec, 40 + 4 * i, va + len(sec))
        sec += n.encode() + b"\0"
    struct.pack_into("<I", sec, 24, len(exports))
    struct.pack_into("<I", sec, 32, va + 40)
    sec += extra
    hdr = bytearray(roff)
    hdr[0:2] = b"MZ"
    struct.pack_into("<I", hdr, 0x3C, pe)
    hdr[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", hdr, pe + 4, machine, 1, 0, 0, 0, opt_size, 0x2022 if dll else 0x0022)
    opt = pe + 24
    struct.pack_into("<H", hdr, opt, 0x20B)
    struct.pack_into("<I", hdr, opt + 108, 16)
    struct.pack_into("<II", hdr, opt + 112, va if exports else 0, len(sec))
    s = opt + opt_size
    hdr[s:s + 8] = b".edata\0\0"
    struct.pack_into("<IIII", hdr, s + 8, len(sec), va, len(sec), roff)
    return bytes(hdr) + bytes(sec)


HOST_DLL = make_dll(("swgame_tables",), extra=b"[modhost] made-up log text\0")
# Made-up secrets in the shapes the tool looks for, built from pieces so this file itself holds none.
TOKEN = "gh" + "p_" + "A1b2C3d4E5" * 3 + "123456"
DASHES = "-" * 5
PRIVATE_KEY = f"{DASHES}BEGIN OPENSSH PRIVATE KEY{DASHES}\nmadeup\n{DASHES}END OPENSSH PRIVATE KEY{DASHES}\n"
AWS_KEY = "AK" + "IA" + "MADEUPEXAMPLE123"


def zip_of(files) -> bytes:
    """A made-up zip file holding these files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def dir_link(link: Path, target: Path) -> None:
    """A junction on Windows (no admin rights needed), a symbolic link elsewhere."""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=True)
    else:
        os.symlink(target, link, target_is_directory=True)


class Base(unittest.TestCase):
    def setUp(self):
        if not HAVE_GIT:
            self.skipTest("git isn't installed (make_release needs it)")
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "repo"
        self.out = base / "release"
        self.build = self.root / "build" / "native"
        self.sw = base / "SavageWindMadeUp"  # stands in for the Savage Wind folder: nothing is in it
        self.root.mkdir()
        self.git("init", "-q")
        # (this PC's own global ignore list must not change what the tests see)
        self.git("config", "core.excludesFile", str(base / "no-global-excludes"))
        self.project()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *a):
        subprocess.run(["git", "-C", str(self.root), *a], capture_output=True, check=True)

    def put(self, rel, data=b"made up\n"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)
        return p

    def project(self):
        """A tiny made-up project in the shape of this repo, plus things that must stay out."""
        self.put("README.md", "# made-up project\n")
        self.put("LICENSE", "MIT (made up)\n")
        self.put("NOTICE.md", "notice\n")
        self.put(".gitignore", "out/\nbuild/\n__pycache__/\n")
        self.put("tools/tool.py", "print('hi')\n")
        self.put("tools/mp/sub.py", "x = 1\n")
        self.put("native/examples/hello/hello.c", "int swmod_init(void) { return 0; }\n")
        self.put("docs/guide.md", "how to\n")
        self.put("tests/test_tool.py", "pass\n")
        self.put(".github/workflows/checks.yml", "name: checks\n")
        # never in a release:
        self.put("notes.txt", "my own notes\n")
        self.put("out/codemap/summary.txt", "tool output\n")
        self.put("build/native/obj/hello.o", b"\0obj")
        self.put("upstream/README.md", "someone else's repo\n")
        self.put("tools/__pycache__/tool.cpython-313.pyc", b"\0pyc")
        self.put("tools/stale.pyc", b"\0pyc")

    def plan(self, **kw):
        kw.setdefault("terms", TERMS)
        kw.setdefault("build_dir", self.build)
        kw.setdefault("forbidden", [self.sw])
        return mr.plan_release(self.root, kw.pop("version", "1.2.3"), **kw)

    def run_main(self, *args, **kw):
        kw.setdefault("terms", TERMS)
        kw.setdefault("build_dir", self.build)
        kw.setdefault("forbidden", [self.sw])
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = mr.main(["--out", str(self.out), *args], root=self.root, **kw)
        return code, out.getvalue() + err.getvalue()

    def zip_path(self, version="1.2.3"):
        return self.out / f"breathalyzer-{version}.zip"


class WhatGoesIn(Base):
    def test_only_the_allow_list(self):
        rels = [i.rel for i in self.plan().items]
        self.assertEqual(rels, sorted([".github/workflows/checks.yml", ".gitignore", "LICENSE", "NOTICE.md",
                                       "README.md", "docs/guide.md", "native/examples/hello/hello.c",
                                       "tests/test_tool.py", "tools/mp/sub.py", "tools/tool.py"]))

    def test_blocked_file_refuses_everything(self):
        self.put("docs/pack.sarc", b"made-up")                 # a game file type
        self.put("tools/data.bin", b"SARC" + b"\0" * 20)       # starts like a game file
        self.put("tests/keys.txt", "master_key_00 = " + "ab" * 16 + "\n")  # made-up key line
        plan = self.plan()
        refused = dict(plan.refused)
        self.assertEqual(sorted(refused), ["docs/pack.sarc", "tests/keys.txt", "tools/data.bin"])
        self.assertIn("game file type", refused["docs/pack.sarc"])
        self.assertIn("console keys", refused["tests/keys.txt"])
        code, out = self.run_main("--version", "1.2.3")
        self.assertEqual(code, 1)
        self.assertIn("REFUSED docs/pack.sarc", out)
        self.assertFalse(self.out.exists(), "nothing may be written when a file is blocked")

    def test_leaks_are_refused(self):
        self.put("docs/a.md", f"see C:\\Users\\{LOGIN.capitalize()}\\Documents\n")
        self.put("docs/b.md", f"mail {EMAIL.upper()}\n")
        self.put("docs/c.md", f"{LOGIN}s and x{LOGIN} are other words\n")  # not the whole word: fine
        self.put("tools/d.bin", b"\0\0" + f"\\{LOGIN}\\".encode("utf-16-le"))  # UTF-16, in a binary file
        refused = dict(self.plan().refused)
        self.assertEqual(sorted(refused), ["docs/a.md", "docs/b.md", "tools/d.bin"])
        self.assertIn("leak", refused["docs/a.md"])

    def test_size_limit(self):
        self.put(".cleanallow", "docs/big.md\n")  # (skips check_clean's own 10 MB rule, not ours)
        self.put("docs/big.md", b"x" * 2048)
        with mock.patch.object(mr, "MAX_FILE_BYTES", 1024):
            refused = dict(self.plan().refused)
        self.assertIn("bigger than", refused.get("docs/big.md", ""))

    def test_cleanallow_skips_check_clean_but_not_leak_checks(self):
        gen = "".join(f"void f_{0x7100000000 + i:010x}(Cpu *c) {{}}\n" for i in range(3))
        self.put("native/selftest/main_000.c", gen)
        self.assertIn("native/selftest/main_000.c", dict(self.plan().refused))
        self.put(".cleanallow", "# made up\nnative/selftest/main_000.c\n")
        self.assertNotIn("native/selftest/main_000.c", dict(self.plan().refused))
        self.put("native/selftest/main_000.c", gen + f"// {LOGIN}\n")
        self.assertIn("leak", dict(self.plan().refused).get("native/selftest/main_000.c", ""))

    @unittest.skipUnless(HAVE_GIT, "git isn't installed")
    def test_git_ignored_files_stay_out_and_tracked_files_go_in(self):
        def git(*a):
            subprocess.run(["git", "-C", str(self.root), *a], capture_output=True, check=True)
        git("init", "-q")
        self.put(".gitignore", "out/\nbuild/\n__pycache__/\n*.log\n")
        self.put("tools/run.log", "a log\n")
        self.put("extra/thing.txt", "tracked on purpose\n")
        git("add", "extra/thing.txt")
        git("add", "-f", "out/codemap/summary.txt")  # tracked by mistake: still never goes in
        rels = [i.rel for i in self.plan().items]
        self.assertIn("extra/thing.txt", rels)
        self.assertNotIn("tools/run.log", rels)
        self.assertNotIn("notes.txt", rels)
        self.assertFalse([r for r in rels if r.startswith("out/")])

    def test_bad_versions(self):
        for v in ("../x", "a/b", "a\\b", "", "-x", "1..2", "x" * 70):
            self.assertFalse(mr.valid_version(v), v)
            code, out = self.run_main("--version", v)
            self.assertEqual(code, 2, v)
        self.assertTrue(mr.valid_version("0.1.0-dev+win64"))


class Binaries(Base):
    def setUp(self):
        super().setUp()
        self.put("build/native/modhost.dll", HOST_DLL)
        self.put("build/native/mods/hello.dll", make_dll())
        # self-test DLLs and a stray game library: never picked
        self.put("build/native/selftest/fake_swgame.dll", make_dll(("swgame_tables",)))
        self.put("build/native/mods/swgame.dll", make_dll(("swgame_tables",)))

    def test_only_built_targets_and_only_with_the_option(self):
        self.assertFalse([i for i in self.plan().items if i.rel.startswith("build/")])
        plan = self.plan(with_binaries=True)
        self.assertEqual(sorted(plan.binaries()), ["build/native/modhost.dll", "build/native/mods/hello.dll"])
        self.assertEqual(plan.refused, [])
        self.assertIn("build/native/hook-mods/stamina_hook.dll", plan.not_built)

    def test_dll_checks(self):
        cases = {
            b"not a dll at all": "not a DLL we built",
            make_dll(("something_else",)): "doesn't export swmod_init",
            make_dll(machine=0x14C): "not a 64-bit",
            make_dll(dll=False): "a program, not a DLL",
            make_dll(("swmod_init", "f_7100cc7410")): "game functions",
        }
        for data, why in cases.items():
            self.put("build/native/mods/hello.dll", data)
            refused = dict(self.plan(with_binaries=True).refused)
            self.assertIn(why, refused.get("build/native/mods/hello.dll", ""), why)

    def test_game_library_copied_over_modhost_is_refused(self):
        self.put("build/native/modhost.dll", make_dll(("swgame_tables",)))  # right export, but not ours
        refused = dict(self.plan(with_binaries=True).refused)
        self.assertIn("isn't our modhost.dll", refused.get("build/native/modhost.dll", ""))

    def test_never_swgame_or_the_savage_wind_folder(self):
        sw_dll = self.sw / "extracted" / "swgame.dll"
        sw_dll.parent.mkdir(parents=True)
        sw_dll.write_bytes(make_dll(("swgame_tables",)))
        why = mr.binary_problems(sw_dll, self.build, [self.sw])
        self.assertTrue(any("game's code library" in w for w in why))
        self.assertTrue(any("Savage Wind folder" in w for w in why))
        self.assertTrue(any("not inside the build folder" in w for w in why))
        # a build folder that is itself inside the Savage Wind folder
        refused = dict(self.plan(with_binaries=True, forbidden=[self.root]).refused)
        self.assertIn("Savage Wind folder", refused.get("build/native/mods/hello.dll", ""))


class TheZip(Base):
    def setUp(self):
        super().setUp()
        self.put("build/native/modhost.dll", HOST_DLL)
        self.put("build/native/mods/hello.dll", make_dll())

    def test_writes_zip_and_sums(self):
        code, out = self.run_main("--version", "1.2.3", "--with-binaries")
        self.assertEqual(code, 0, out)
        zp = self.zip_path()
        with zipfile.ZipFile(zp) as zf:
            names = zf.namelist()
            self.assertEqual(names, sorted(names))
            self.assertTrue(all(n.startswith("breathalyzer-1.2.3/") for n in names))
            self.assertIn("breathalyzer-1.2.3/build/native/modhost.dll", names)
            self.assertIn("breathalyzer-1.2.3/SHA256SUMS.txt", names)
            self.assertTrue(all(i.date_time == mr.FIXED_DATE for i in zf.infolist()))
            sums = zf.read("breathalyzer-1.2.3/SHA256SUMS.txt").decode()
            for line in sums.splitlines():
                sha, rel = line.split("  ")
                self.assertEqual(hashlib.sha256(zf.read("breathalyzer-1.2.3/" + rel)).hexdigest(), sha)
            self.assertEqual(len(sums.splitlines()), len(names) - 1)
        outer = (self.out / "breathalyzer-1.2.3.SHA256SUMS.txt").read_text()
        self.assertIn(f"{hashlib.sha256(zp.read_bytes()).hexdigest()}  breathalyzer-1.2.3.zip\n", outer)
        self.assertIn("  breathalyzer-1.2.3/tools/tool.py\n", outer)
        self.assertFalse(list(self.out.glob("*.tmp")))

    def test_byte_identical(self):
        self.assertEqual(self.run_main("--version", "1.2.3", "--with-binaries")[0], 0)
        first = self.zip_path().read_bytes()
        for p in self.root.rglob("*"):  # new dates on every file: same zip
            if p.is_file():
                os.utime(p, (1_000_000_000, 1_000_000_000))
        self.assertEqual(self.run_main("--version", "1.2.3", "--with-binaries")[0], 0)
        self.assertEqual(self.zip_path().read_bytes(), first)
        self.put("docs/guide.md", "how to, changed\n")
        self.assertEqual(self.run_main("--version", "1.2.3", "--with-binaries")[0], 0)
        self.assertNotEqual(self.zip_path().read_bytes(), first)

    def test_dry_run_writes_nothing(self):
        code, out = self.run_main("--version", "1.2.3", "--dry-run", "--with-binaries")
        self.assertEqual(code, 0, out)
        self.assertIn("tools/tool.py", out)
        self.assertIn("build/native/modhost.dll", out)
        self.assertIn("not built, left out: build/native/hook-mods/stamina_hook.dll", out)
        self.assertNotIn("notes.txt", out)
        self.assertFalse(self.out.exists())
        self.put("docs/x.sarc", b"made up")
        code, out = self.run_main("--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("REFUSED docs/x.sarc", out)

    def test_failed_check_after_writing_removes_the_zip(self):
        with mock.patch.object(mr, "verify_zip", return_value=[("x", "made-up problem")]):
            code, out = self.run_main("--version", "1.2.3")
        self.assertEqual(code, 1)
        self.assertIn("made-up problem", out)
        self.assertFalse(list(self.out.iterdir()))


class VerifyZip(Base):
    """verify_zip on hand-made bad zips: what it must catch after writing."""

    def setUp(self):
        super().setUp()
        self.p = self.plan()
        self.files = mr.entries(self.p)
        self.allowed = set()

    def problems(self, files, raw=None):
        zp = self.out / "test.zip"
        self.out.mkdir(exist_ok=True)
        zp.write_bytes(raw if raw is not None else mr.zip_bytes(files))
        return dict(mr.verify_zip(zp, self.p, self.allowed, TERMS))

    @staticmethod
    def stored_zip(files) -> bytes:
        """The files uncompressed, so a test can change bytes in place."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            for name in sorted(files):
                zf.writestr(name, files[name])
        return buf.getvalue()

    def test_clean_zip_passes(self):
        self.assertEqual(self.problems(self.files), {})

    def test_bad_names(self):
        f = self.p.folder
        for name, why in ((f"/abs/{f}/x.txt", "absolute"), (f"C:/x/{f}.txt", "absolute"),
                          (f"{f}/../evil.txt", "'..'"),
                          ("other/x.txt", "not inside"), (f"{f}/out/x.txt", "never go in"),
                          (f"{f}/tools/__pycache__/x.pyc", "never go in"), (f"{f}/extra.txt", "not planned"),
                          (f"{f}/Build/native/mods/other.dll", "never go in"),  # (Build/ is build/ on Windows)
                          (f"{f}/tools/__PyCache__/x.py", "never go in"), (f"{f}/Upstream/x.txt", "never go in"),
                          (f"{f}/docs/notes-{LOGIN}.md", "its name holds")):
            got = self.problems({**self.files, name: b"x"})
            self.assertIn(why, got.get(name, ""), name)
        # a backslash: zipfile itself would store it as /, so change the stored name's bytes
        raw = self.stored_zip({**self.files, f"{f}/tools#x.py": b"x"}).replace(b"tools#x.py", b"tools\\x.py")
        self.assertIn("backslash", self.problems(None, raw).get(f"{f}/tools\\x.py", ""))

    def test_changed_content_leaks_and_game_files(self):
        f = self.p.folder
        got = self.problems({**self.files, f"{f}/docs/guide.md": f"by {LOGIN}\n".encode()})
        self.assertIn("leak", got[f"{f}/docs/guide.md"])
        self.assertIn("content differs", got[f"{f}/docs/guide.md"])
        self.p.items.append(mr.Item("docs/pic.bin", b"Yaz0" + b"\0" * 12))  # planned, but a game file
        got = self.problems(mr.entries(self.p))
        self.assertIn("starts like a game file", got.get(f"{f}/docs/pic.bin", ""))
        # planned, but an archive holding a game file, or a file holding a (made-up) token
        self.p.items.append(mr.Item("tests/x.zip", zip_of({"Pack/a.sarc": b"made up"})))
        self.p.items.append(mr.Item("docs/t.md", f"token {TOKEN}\n".encode()))
        got = self.problems(mr.entries(self.p))
        self.assertIn("game file type", got.get(f"{f}/tests/x.zip", ""))
        self.assertIn("access token", got.get(f"{f}/docs/t.md", ""))

    def test_missing_file_and_wrong_sums(self):
        f = self.p.folder
        files = dict(self.files)
        del files[f"{f}/tools/tool.py"]
        self.assertIn("missing", self.problems(files).get(f"{f}/tools/tool.py", ""))
        files = {**self.files, f"{f}/SHA256SUMS.txt": b"0" * 64 + b"  tools/tool.py\n"}
        self.assertIn(f"{f}/SHA256SUMS.txt", self.problems(files))

    def test_size_limit_and_damaged_zip(self):
        with mock.patch.object(mr, "MAX_FILE_BYTES", 4):
            got = self.problems(self.files)
        self.assertTrue(any("bigger than" in w for w in got.values()))
        raw = bytearray(self.stored_zip(self.files))
        raw[raw.find(b"made-up project")] ^= 0x20  # (one changed byte: the CRC no longer matches)
        self.assertIn("damaged", self.problems(None, bytes(raw)).get(f"{self.p.folder}/README.md", ""))
        raw = bytearray(mr.zip_bytes(self.files))
        at = raw.find(b"README.md") + len("README.md")  # (the deflate stream right after the local header)
        raw[at:at + 8] = b"\xff" * 8
        self.assertTrue(any("damaged" in w for w in self.problems(None, bytes(raw)).values()))
        self.assertIn("can't open", next(iter(self.problems(None, b"not a zip").values())))


class Links(Base):
    def test_junction_in_a_folder_above_is_refused(self):
        outside = Path(self.tmp.name) / "outside"  # stands in for the game dump: a junction would pull it in
        (outside / "sub").mkdir(parents=True)
        (outside / "main.elf").write_bytes(b"\x7fELF made up")
        (outside / "sub" / "x.txt").write_text("made up\n")
        dir_link(self.root / "tools" / "junc", outside)
        plan = self.plan()
        self.assertFalse([i.rel for i in plan.items if i.rel.startswith("tools/junc")])
        refused = dict(plan.refused)
        self.assertTrue(any(r.startswith("tools/junc") and "link" in w for r, w in refused.items()), refused)
        code, out = self.run_main("--version", "1.2.3")
        self.assertEqual(code, 1, out)
        self.assertFalse(self.out.exists())

    def test_link_problems(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "a.txt").write_text("made up\n")
        dir_link(self.root / "docs" / "l", outside)
        self.assertIn("link or junction", mr.link_problems(self.root, "docs/l/a.txt")[0])
        self.assertEqual(mr.link_problems(self.root, "docs/guide.md"), [])


class Archives(Base):
    def test_files_inside_zip_and_gzip_are_checked(self):
        self.put("tests/fixtures/game.zip", zip_of({"Pack/TitleBG.pack": b"SARC" + b"\0" * 100}))  # made-up header
        self.put("tests/fixtures/keys.zip", zip_of({"prod.keys": "master_key_00 = " + "ab" * 16 + "\n"}))
        self.put("tests/fixtures/leak.zip", zip_of({"note.txt": f"C:/Users/{LOGIN}/Documents"}))
        self.put("tests/fixtures/token.zip", zip_of({"cfg.txt": f"token = {TOKEN}\n"}))
        self.put("docs/assets/blob.gz", gzip.compress(b"SARC" + b"\0" * 100 + f" {LOGIN} ".encode()))
        self.put("docs/assets/noext.bin", zip_of({"a/b.sarc": b"made up"}))  # a zip by its first bytes only
        self.put("tests/fixtures/nested.zip", zip_of({"inner.zip": zip_of({"x.txt": b"made up"})}))
        self.put("docs/assets/x.7z", b"7z\xbc\xaf\x27\x1c" + b"\0" * 20)  # made-up 7z header: can't be opened
        self.put("docs/assets/y.rar", b"made up")                         # by its type only
        self.put("tests/fixtures/clean.zip", zip_of({"mod/readme.txt": b"made up\n"}))
        self.put("tests/fixtures/clean.txt.gz", gzip.compress(b"made up\n"))
        plan = self.plan()
        refused = dict(plan.refused)
        self.assertEqual(sorted(refused), sorted([
            "tests/fixtures/game.zip", "tests/fixtures/keys.zip", "tests/fixtures/leak.zip", "tests/fixtures/token.zip",
            "docs/assets/blob.gz", "docs/assets/noext.bin", "tests/fixtures/nested.zip", "docs/assets/x.7z",
            "docs/assets/y.rar"]))
        self.assertIn("starts like a game file", refused["tests/fixtures/game.zip"])
        self.assertIn("console keys", refused["tests/fixtures/keys.zip"])
        self.assertIn("leak", refused["tests/fixtures/leak.zip"])
        self.assertIn("access token", refused["tests/fixtures/token.zip"])
        self.assertIn("starts like a game file", refused["docs/assets/blob.gz"])  # (every reason, not just one)
        self.assertIn("leak", refused["docs/assets/blob.gz"])
        self.assertIn("game file type", refused["docs/assets/noext.bin"])
        self.assertIn("inside a zip", refused["tests/fixtures/nested.zip"])
        self.assertIn("can't be checked", refused["docs/assets/x.7z"])
        self.assertIn("can't be checked", refused["docs/assets/y.rar"])
        rels = [i.rel for i in plan.items]
        self.assertIn("tests/fixtures/clean.zip", rels)
        self.assertIn("tests/fixtures/clean.txt.gz", rels)
        code, out = self.run_main("--version", "1.2.3")
        self.assertEqual(code, 1, out)
        self.assertFalse(self.out.exists())

    def test_cleanallow_skips_check_clean_inside_but_never_leaks(self):
        self.put("tests/fixtures/mod.zip", zip_of({"content/Pack/x.sarc": b"made up"}))
        self.assertIn("tests/fixtures/mod.zip", dict(self.plan().refused))
        self.put(".cleanallow", "tests/fixtures/mod.zip\n")
        self.assertEqual(self.plan().refused, [])
        self.put("tests/fixtures/mod.zip", zip_of({"content/Pack/x.sarc": b"made up", "n.txt": f"by {LOGIN}"}))
        self.assertIn("leak", dict(self.plan().refused).get("tests/fixtures/mod.zip", ""))
        self.put("tests/fixtures/mod.7z", b"7z\xbc\xaf\x27\x1c" + b"\0" * 20)
        self.put(".cleanallow", "tests/fixtures/mod.7z\n")
        self.assertIn("can't be checked", dict(self.plan().refused).get("tests/fixtures/mod.7z", ""))


class NeedsGit(Base):
    def test_git_failing_refuses(self):
        self.put("tools/extracted-1.6.0/main.elf", b"\x7fELF made up")  # .gitignore would keep this out
        real_run = subprocess.run

        def dubious(args, **kw):
            if args[0] == "git":
                return subprocess.CompletedProcess(args, 128, b"", b"fatal: detected dubious ownership in X\n")
            return real_run(args, **kw)
        with mock.patch.object(mr.subprocess, "run", side_effect=dubious):
            code, out = self.run_main("--dry-run")
        self.assertEqual(code, 1, out)
        self.assertIn("git is needed", out)
        self.assertIn("dubious ownership", out)
        with mock.patch.object(mr.subprocess, "run", side_effect=FileNotFoundError(2, "made-up: no git")):
            code, out = self.run_main("--version", "1.2.3")
        self.assertEqual(code, 1, out)
        self.assertIn("git is needed", out)
        self.assertFalse(self.out.exists())

    def test_not_a_git_repo_refuses(self):
        shutil.rmtree(self.root / ".git")
        if subprocess.run(["git", "-C", str(self.root), "rev-parse"], capture_output=True).returncode == 0:
            self.skipTest("the temp folder is inside a git repo here")
        self.put("tools/extracted-1.6.0/main.elf", b"\x7fELF made up")
        code, out = self.run_main("--version", "1.2.3")
        self.assertEqual(code, 1, out)
        self.assertIn("git is needed", out)
        self.assertFalse(self.out.exists())


class LetterCase(Base):
    def test_never_and_allow_list_ignore_letter_case(self):
        for rel in ("Build/native/mods/other.dll", "Upstream/x.txt", "Dist/pkg.txt", "OUT/x.txt",
                    "tools/__PyCache__/x.py", "tools/X.PYC"):
            self.assertTrue(mr.never(rel), rel)
        self.assertTrue(mr.allowed_root("Tools/x.py"))
        self.assertTrue(mr.allowed_root("readme.md"))

    def test_tracked_files_in_other_letter_case_stay_out(self):
        for d in ("build", "upstream", "tools/__pycache__"):  # (on Windows Build/ would land in build/)
            shutil.rmtree(self.root / d)
        paths = ("Upstream/notes.txt", "Build/native/mods/other.dll", "Dist/pkg.txt", "tools/__PyCache__/x.txt")
        for rel in paths:
            self.put(rel, b"made up\n")
        self.git("add", "-f", *paths)
        rels = [i.rel for i in self.plan().items]
        self.assertFalse([r for r in rels if r.lower().startswith(("upstream/", "build/", "dist/"))
                          or "__pycache__" in r.lower()], rels)


class FileNames(Base):
    def test_non_ascii_name(self):
        name = "docs/caf\u00e9.md"
        self.put(name, "made up\n")
        code, out = self.run_main("--version", "1.2.3", "--dry-run")
        self.assertEqual(code, 0, out)
        code, out = self.run_main("--version", "1.2.3")
        self.assertEqual(code, 0, out)  # (it crashed writing SHA256SUMS.txt as ASCII)
        with zipfile.ZipFile(self.zip_path()) as zf:
            self.assertIn(f"breathalyzer-1.2.3/{name}", zf.namelist())
            self.assertIn(f"  {name}\n", zf.read("breathalyzer-1.2.3/SHA256SUMS.txt").decode("utf-8"))
        outer = (self.out / "breathalyzer-1.2.3.SHA256SUMS.txt").read_text(encoding="utf-8")
        self.assertIn(f"  breathalyzer-1.2.3/{name}\n", outer)

    def test_user_name_in_a_file_name(self):
        self.put(f"docs/notes-{LOGIN}.md", "made up\n")
        code, out = self.run_main("--version", "1.2.3", "--dry-run")
        self.assertEqual(code, 1, out)
        self.assertIn(f"REFUSED docs/notes-{LOGIN}.md: its name holds", out)
        code, out = self.run_main("--version", "1.2.3")
        self.assertEqual(code, 1, out)
        self.assertIn(f"REFUSED docs/notes-{LOGIN}.md", out)
        self.assertNotIn(mr.SUMS, out)  # (it blamed SHA256SUMS.txt)


class LeakTerms(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.users = Path(os.path.abspath(os.sep)) / "Users"  # C:\Users on Windows, /Users elsewhere

    def tearDown(self):
        self.tmp.cleanup()

    def terms(self, login, short=None, temp="", git_name=(), git_mail=()):
        def git(root, *args):
            return list(git_name) if args[-1] == "user.name" else list(git_mail) if args[-1] == "user.email" else []
        env = {"USERNAME": login, "USER": "", "TEMP": temp, "TMP": temp, "USERPROFILE": "", "LOCALAPPDATA": "",
               "APPDATA": ""}
        with mock.patch.object(mr.getpass, "getuser", return_value=login), \
                mock.patch.object(mr.Path, "home", return_value=self.users / login), \
                mock.patch.dict(os.environ, env), mock.patch.object(mr, "_git_lines", side_effect=git), \
                mock.patch.object(mr, "_short_path", side_effect=lambda p: short or p):
            return mr.leak_terms(self.root)

    def spellings(self, path: Path):
        s = str(path)
        return {s, s.replace("\\", "/"), s.replace("\\", "\\\\"), "/c" + s.split(":", 1)[-1].replace("\\", "/")}

    def test_short_and_common_logins_still_catch_the_home_path(self):
        for login in ("jo", "dev"):  # (too short or too common to look for as a word)
            terms = self.terms(login)
            self.assertNotIn(login, terms)
            for text in self.spellings(self.users / login / "Documents"):
                self.assertTrue(mr.leak_problems(text.encode(), terms), (login, text))
            self.assertFalse(mr.leak_problems(str(self.users / f"{login}e" / "x").encode(), terms), login)

    def test_short_8_3_name_of_the_home_folder(self):
        short = self.users / "ALEXAN~1"
        terms = self.terms("alexanderfake", short=str(short))
        self.assertTrue(mr.leak_problems(str(short / "AppData" / "Local" / "Temp" / "x").encode(), terms))
        # also when only TEMP spells it that way
        terms = self.terms("alexanderfake", temp=str(short / "AppData" / "Local" / "Temp"))
        self.assertTrue(mr.leak_problems(str(short / "AppData" / "x").encode(), terms))

    def test_git_name_unless_license_holds_it(self):
        terms = self.terms("alicefake", git_name=["Alice Fake"], git_mail=[EMAIL])
        self.assertIn("Alice Fake", terms)
        self.assertIn(EMAIL, terms)
        self.assertTrue(mr.leak_problems(b"made by alice fake\n", terms))
        (self.root / "LICENSE").write_text("MIT License\n\nCopyright (c) 2026 Alice Fake\n")
        self.assertNotIn("Alice Fake", self.terms("alicefake", git_name=["Alice Fake"]))


class WriteErrors(Base):
    def sums_path(self):
        return self.out / "breathalyzer-1.2.3.SHA256SUMS.txt"

    def first_release(self):
        self.assertEqual(self.run_main("--version", "1.2.3")[0], 0)
        self.put("docs/new.md", "made-up change\n")
        return self.zip_path().read_bytes(), self.sums_path().read_bytes()

    def test_locked_old_zip(self):
        old_zip, old_sums = self.first_release()
        with mock.patch.object(mr.os, "replace", side_effect=PermissionError(13, "Access is denied")):
            code, out = self.run_main("--version", "1.2.3")
        self.assertEqual(code, mr.EXIT_WRITE_FAILED, out)
        self.assertIn("open in another program", out)
        self.assertNotIn("Traceback", out)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [self.sums_path().name, self.zip_path().name])
        self.assertEqual(self.zip_path().read_bytes(), old_zip)
        self.assertEqual(self.sums_path().read_bytes(), old_sums)

    @unittest.skipUnless(os.name == "nt", "only Windows locks a file that is open")
    def test_really_locked_old_zip(self):
        old_zip, old_sums = self.first_release()
        with open(self.zip_path(), "rb"):  # like the zip open in Explorer, 7-Zip or an upload
            code, out = self.run_main("--version", "1.2.3")
        self.assertEqual(code, mr.EXIT_WRITE_FAILED, out)
        self.assertFalse(list(self.out.glob("*.tmp")))
        self.assertEqual((self.zip_path().read_bytes(), self.sums_path().read_bytes()), (old_zip, old_sums))

    def test_sums_file_locked_after_the_zip(self):
        old_zip, _ = self.first_release()
        real_replace = os.replace
        calls = []

        def second_fails(a, b):
            calls.append(b)
            if len(calls) == 2:
                raise PermissionError(13, "Access is denied")
            return real_replace(a, b)
        with mock.patch.object(mr.os, "replace", side_effect=second_fails):
            code, out = self.run_main("--version", "1.2.3")
        self.assertEqual(code, mr.EXIT_WRITE_FAILED, out)
        self.assertIn("not the SHA-256 list", out)
        self.assertNotEqual(self.zip_path().read_bytes(), old_zip)
        # no list next to the zip that describes the old zip, and no .tmp files
        self.assertEqual([p.name for p in self.out.iterdir()], [self.zip_path().name])

    def test_folder_that_cant_be_made(self):
        blocker = Path(self.tmp.name) / "a-file"
        blocker.write_text("made up\n")
        code, out = self.run_main("--version", "1.2.3", "--out", str(blocker / "sub"))
        self.assertEqual(code, mr.EXIT_WRITE_FAILED, out)
        self.assertIn("can't write in", out)


class OutFolder(Base):
    def test_out_inside_the_project_is_refused(self):
        code, out = self.run_main("--version", "1.2.3", "--out", str(self.root / "docs" / "rel"))
        self.assertEqual(code, 2, out)
        self.assertIn("inside the project", out)
        self.assertFalse((self.root / "docs" / "rel").exists())
        code, out = self.run_main("--version", "1.2.3", "--out", str(self.root / "out" / "rel"))
        self.assertEqual(code, 0, out)

    def test_earlier_releases_are_never_packed(self):
        for name in ("breathalyzer", "botw-mod-loader"):   # (botw-mod-loader: the name before the rename)
            self.put(f"docs/rel/{name}-0.9.SHA256SUMS.txt", "0" * 64 + f"  {name}-0.9.zip\n")
            self.put(f"docs/rel/{name}-0.9.zip", zip_of({f"{name}-0.9/README.md": b"made up\n"}))
        refused = dict(self.plan().refused)
        for name in ("breathalyzer", "botw-mod-loader"):
            self.assertIn("never packed again", refused.get(f"docs/rel/{name}-0.9.SHA256SUMS.txt", ""))
            self.assertIn("never packed again", refused.get(f"docs/rel/{name}-0.9.zip", ""))


class HardLinks(Base):
    def setUp(self):
        super().setUp()
        self.put("build/native/modhost.dll", HOST_DLL)
        self.dll = self.put("build/native/mods/hello.dll", make_dll())

    def link(self, src: Path, dst: Path):
        try:
            os.link(src, dst)
        except OSError as e:
            self.skipTest(f"no hard links here: {e}")

    def test_hard_link_to_the_savage_wind_folder(self):
        src = self.sw / "mods" / "thirdparty.dll"  # a made-up DLL that lives in the "Savage Wind" folder
        src.parent.mkdir(parents=True)
        src.write_bytes(make_dll())
        self.dll.unlink()
        self.link(src, self.dll)
        refused = dict(self.plan(with_binaries=True).refused)
        self.assertIn("hard link", refused.get("build/native/mods/hello.dll", ""))

    @unittest.skipUnless(os.name == "nt", "only Windows can list a file's other names")
    def test_hard_link_elsewhere_is_fine(self):
        other = Path(self.tmp.name) / "library" / "hello.dll"  # like modman's library holding a link to it
        other.parent.mkdir()
        self.link(self.dll, other)
        plan = self.plan(with_binaries=True)
        self.assertEqual(plan.refused, [])
        self.assertIn("build/native/mods/hello.dll", plan.binaries())


class Secrets(Base):
    def test_secrets_are_refused(self):
        self.put("tools/.env", "TOKEN=made-up\n")
        self.put("tests/id_ed25519", "made up\n")
        self.put("docs/cert.pem", "made up\n")
        self.put("docs/notes.md", f"token {TOKEN}\n")
        self.put("tests/key.txt", PRIVATE_KEY)
        self.put("tools/cfg.py", f"KEY = '{AWS_KEY}'\n")
        self.put("docs/ok.md", "scikit-learn and sk-learn are fine, so is a lone ghp_ prefix\n")
        self.put("docs/.env.example", "TOKEN=\n")
        refused = dict(self.plan().refused)
        self.assertEqual(sorted(refused), sorted(["tools/.env", "tests/id_ed25519", "docs/cert.pem", "docs/notes.md",
                                                  "tests/key.txt", "tools/cfg.py"]))
        self.assertIn("keys or passwords", refused["tools/.env"])
        self.assertIn("private key or an access token", refused["tests/key.txt"])


class Docs(unittest.TestCase):
    def test_doc_commands_parse(self):
        text = (REPO / "docs" / "release.md").read_text(encoding="utf-8")
        cmds = re.findall(r"^python tools[\\/]make_release\.py(.*)$", text, re.M)
        self.assertTrue(cmds)
        for args in cmds:
            a = mr.parser().parse_args(shlex.split(args.split("#")[0]))
            self.assertTrue(mr.valid_version(a.version), args)
        flat = " ".join(text.split())
        for fact in ("SHA256SUMS.txt", "20 MB", "byte-identical", "swgame.dll", "check_clean", "git is needed",
                     "same Python version", "zlib-ng", "3: the files couldn't be written", "hard link", "junction",
                     "access tokens", "zip and gzip"):
            self.assertIn(fact, flat)
        # (a zip is only byte-identical on the same Python: 3.14 on Windows compresses with zlib-ng)
        self.assertIn("same Python version", " ".join(mr.__doc__.split()))
        self.assertNotIn("The same files always give a byte-identical zip", flat)


if __name__ == "__main__":
    unittest.main()
