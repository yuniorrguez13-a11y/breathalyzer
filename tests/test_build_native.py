"""Tests for tools/build_native.py. Made-up folders and PE files only: no compiler runs, no game files."""
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import build_native as bn  # noqa: E402


# ------------------------------------------------------------------ a tiny PE32+ writer (made-up DLLs)

def make_pe(imports=None, exports=(), dll=True, ordinal_imports=None, use_lookup=True) -> bytes:
    """A minimal PE32+ file with one section holding an import table and an export table."""
    imports = imports or {}
    ordinal_imports = ordinal_imports or {}
    va = 0x1000
    sec = bytearray()

    def put(b: bytes, align: int = 8) -> int:
        while len(sec) % align:
            sec.append(0)
        rva = va + len(sec)
        sec.extend(b)
        return rva

    def cstr(s: str) -> int:
        return put(s.encode() + b"\0", 2)

    def hint_name(s: str) -> int:  # (a 2-byte hint, then the name)
        return put(b"\0\0" + s.encode() + b"\0", 2)

    dlls = list(dict.fromkeys([*imports, *ordinal_imports]))
    imp_rva = put(bytes(20 * (len(dlls) + 1)), 4)
    descs = []
    for name in dlls:
        name_rva = cstr(name)
        entries = [hint_name(f) for f in imports.get(name, [])]
        entries += [(1 << 63) | n for n in ordinal_imports.get(name, [])]
        ilt = put(b"".join(struct.pack("<Q", e) for e in entries) + bytes(8))
        descs.append((ilt if use_lookup else 0, name_rva, ilt))
    for i, (lookup, name_rva, thunk) in enumerate(descs):
        struct.pack_into("<IIIII", sec, imp_rva - va + 20 * i, lookup, 0, 0, name_rva, thunk)

    exp_rva = 0
    if exports:
        name_rvas = [cstr(e) for e in exports]
        names = put(b"".join(struct.pack("<I", r) for r in name_rvas), 4)
        ords = put(b"".join(struct.pack("<H", i) for i in range(len(exports))), 2)
        funcs = put(bytes(4 * len(exports)), 4)
        dllname = cstr("made_up.dll")
        exp_rva = put(struct.pack("<IIHHIIIIIII", 0, 0, 0, 0, dllname, 1, len(exports), len(exports), funcs, names,
                                  ords), 4)

    opt_size = 240
    pe_off = 0x80
    head = bytearray(0x200)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, pe_off)
    head[pe_off:pe_off + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", head, pe_off + 4, 0x8664, 1, 0, 0, 0, opt_size, 0x2022 if dll else 0x0022)
    opt = pe_off + 24
    struct.pack_into("<H", head, opt, 0x20B)
    struct.pack_into("<I", head, opt + 108, 16)
    struct.pack_into("<II", head, opt + 112, exp_rva, 40 if exp_rva else 0)
    struct.pack_into("<II", head, opt + 120, imp_rva if dlls else 0, 20 * (len(dlls) + 1) if dlls else 0)
    s = opt + opt_size
    head[s:s + 8] = b".data\0\0\0"
    struct.pack_into("<IIII", head, s + 8, len(sec), va, len(sec), 0x200)
    return bytes(head) + bytes(sec)


class PeParsing(unittest.TestCase):
    def test_imports_and_exports(self):
        data = make_pe({"savagewind.exe": ["control_register", "hle_override"], "KERNEL32.dll": ["LoadLibraryExW"]},
                       exports=["swgame_tables"])
        info = bn.pe_info(data)
        self.assertTrue(info.dll)
        self.assertEqual(info.machine, 0x8664)
        self.assertEqual(info.exports, ["swgame_tables"])
        self.assertEqual(info.imports["savagewind.exe"], ["control_register", "hle_override"])
        self.assertEqual(info.imports["KERNEL32.dll"], ["LoadLibraryExW"])

    def test_dll_name_match_ignores_case(self):
        info = bn.pe_info(make_pe({"SAVAGEWIND.EXE": ["guest_call"]}))
        self.assertEqual(bn.imports_from(info, "savagewind.exe"), ["guest_call"])
        self.assertEqual(bn.imports_from(info, "other.dll"), [])

    def test_ordinals_and_missing_lookup_table(self):
        info = bn.pe_info(make_pe({"a.dll": ["f"]}, ordinal_imports={"a.dll": [12]}, use_lookup=False))
        self.assertEqual(info.imports["a.dll"], ["f", "#12"])

    def test_no_tables(self):
        info = bn.pe_info(make_pe(dll=False))
        self.assertFalse(info.dll)
        self.assertEqual((info.imports, info.exports), ({}, []))

    def test_not_a_pe(self):
        with self.assertRaises(bn.PeError):
            bn.pe_info(b"not a DLL at all")
        with self.assertRaises(bn.PeError):
            bn.pe_info(b"MZ" + bytes(0x3A) + struct.pack("<I", 0x40) + b"XX\0\0")
        with self.assertRaises(bn.PeError):
            bn.pe_info(make_pe({"a.dll": ["f"]})[:0x210])  # cut off inside the tables

    def test_describe(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "modhost.dll"
            p.write_bytes(make_pe({"savagewind.exe": ["hle_override", "control_register"]}, ["swgame_tables"]))
            text = bn.describe(p, Path(d))
        self.assertEqual(text, "modhost.dll: exports swgame_tables; imports 2 function(s) from savagewind.exe "
                               "(control_register, hle_override)")

    def test_check_exports(self):
        with tempfile.TemporaryDirectory() as d:
            good, bad = Path(d) / "good.dll", Path(d) / "bad.dll"
            good.write_bytes(make_pe(exports=["swmod_init"]))
            bad.write_bytes(make_pe(exports=["something_else"]))
            bn.check_exports([(good, bn.EXAMPLES[0])])
            with self.assertRaises(bn.BuildError):
                bn.check_exports([(bad, bn.EXAMPLES[0])])


# ------------------------------------------------------------------ toolchain and commands

def fake_install(root: Path, rt: bool = True) -> Path:
    (root / "zig").mkdir(parents=True)
    (root / "zig" / "zig.exe").write_bytes(b"")
    (root / "savagewind.dll.a").write_bytes(b"!<arch>\n")
    if rt:
        (root / "zig" / "rt").mkdir()
        (root / "zig" / "rt" / "link.rsp").write_text("-DLL\n")
    return root


class Toolchain(unittest.TestCase):
    def test_finds_release_zig_and_runtime(self):
        with tempfile.TemporaryDirectory() as d:
            inst = fake_install(Path(d) / "Savage Wind")
            tc = bn.find_toolchain(inst)
            self.assertEqual(tc.zig, inst / "zig" / "zig.exe")
            self.assertEqual(tc.implib, inst / "savagewind.dll.a")
            self.assertEqual(tc.rt, inst / "zig" / "rt")

    def test_full_zig_without_prebuilt_runtime(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(bn.find_toolchain(fake_install(Path(d), rt=False)).rt)

    def test_missing_pieces(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(bn.BuildError, "No zig"):
                bn.find_toolchain(Path(d))
            (Path(d) / "zig").mkdir()
            (Path(d) / "zig" / "zig.exe").write_bytes(b"")
            with self.assertRaisesRegex(bn.BuildError, "savagewind.dll.a"):
                bn.find_toolchain(Path(d))

    def test_default_install_and_data_root(self):
        with mock.patch.dict(os.environ, {"SWMOD_INSTALL": r"D:\Games\SW", "SAVAGEWIND_DATA_ROOT": ""}):
            self.assertEqual(bn.default_install(), Path(r"D:\Games\SW"))
            self.assertEqual(bn.default_data_root(Path(r"D:\Games\SW")), Path(r"D:\Games\SW"))
        with mock.patch.dict(os.environ, {"SAVAGEWIND_DATA_ROOT": r"E:\data"}):
            self.assertEqual(bn.default_data_root(Path(r"D:\Games\SW")), Path(r"E:\data"))
        with mock.patch.dict(os.environ, {"USERPROFILE": r"C:\Users\Someone", "HOME": r"C:\Users\Someone"}):
            os.environ.pop("SWMOD_INSTALL", None)  # (restored when the patch ends)
            self.assertEqual(bn.default_install(), Path(r"C:\Users\Someone\Documents\SavageWind\Savage Wind"))


TC = bn.Toolchain(Path(r"C:\SW\zig\zig.exe"), Path(r"C:\SW\savagewind.dll.a"), Path(r"C:\SW\zig\rt"))
TC_FULL = bn.Toolchain(Path(r"C:\zig\zig.exe"), Path(r"C:\SW\savagewind.dll.a"), None)


class Commands(unittest.TestCase):
    def test_compile(self):
        cmd = bn.compile_cmd(TC, Path("a.c"), Path("a.o"), ("X=1",), (Path("inc"),))
        self.assertEqual(cmd[:2], [str(TC.zig), "cc"])
        self.assertIn("-target", cmd)
        self.assertEqual(cmd[cmd.index("-target") + 1], "x86_64-windows-gnu")
        self.assertIn(f"-I{Path('inc')}", cmd)
        self.assertIn("-DX=1", cmd)
        self.assertEqual(cmd[-4:], ["-c", "a.c", "-o", "a.o"])

    def test_link_like_the_release(self):
        cmd = bn.link_cmd(TC, [Path("a.o")], Path(r"C:\out\modhost.dll"), runtime=True)
        self.assertEqual(cmd[:2], [str(TC.zig), "lld-link"])
        self.assertIn(f"@{TC.rt / 'link.rsp'}", cmd)
        self.assertIn(f"-LIBPATH:{TC.rt}", cmd)
        self.assertIn(r"-OUT:C:\out\modhost.dll", cmd)
        self.assertEqual(cmd[-2:], ["a.o", str(TC.implib)])

    def test_mods_do_not_link_the_runtime(self):
        cmd = bn.link_cmd(TC, [Path("h.o")], Path("hello.dll"), runtime=False)
        self.assertNotIn(str(TC.implib), cmd)

    def test_link_with_a_full_zig(self):
        cmd = bn.link_cmd(TC_FULL, [Path("a.o")], Path("m.dll"), runtime=True)
        self.assertEqual(cmd[:5], [str(TC_FULL.zig), "cc", "-shared", "-target", "x86_64-windows-gnu"])
        self.assertEqual(cmd[-1], str(TC_FULL.implib))
        self.assertIn("-o", cmd)

    def test_plan(self):
        out = Path(r"C:\build\native")
        plan = bn.plan_build(TC, out, (bn.HOST, *bn.EXAMPLES))
        self.assertEqual([t.name for t, _ in plan], ["modhost", "hello"])
        host_cmds = plan[0][1]
        self.assertEqual(len(host_cmds), len(bn.HOST.sources) + 1)  # (each source, then the link)
        self.assertIn(str(out / "obj" / "modhost-modhost.o"), host_cmds[0])
        self.assertIn(f"-I{bn.HOST_SRC}", host_cmds[0])
        self.assertIn(f"-OUT:{out / 'modhost.dll'}", host_cmds[-1])
        self.assertIn(str(TC.implib), host_cmds[-1])
        self.assertIn(f"-OUT:{out / 'mods' / 'hello.dll'}", plan[1][1][1])
        self.assertNotIn(str(TC.implib), plan[1][1][1])

    def test_selftest_targets(self):
        out = Path(r"C:\b")
        names = {t.name: t for t in bn.SELFTEST}
        self.assertEqual(names["fake_runtime"].output(out), out / "selftest" / "savagewind.exe")
        self.assertIn("PROBE_FAIL", names["bad"].defines)
        self.assertFalse(any(t.runtime for t in bn.SELFTEST))  # (the fake game links against nothing real)

    def test_hot_selftest_targets(self):
        """The hot-load test mod is built three ways from one file, and copied under editor-style names."""
        out = Path(r"C:\b")
        self.assertEqual([t.defines for t in bn.SELFTEST_HOT], [(), ("HOT_V2",), ("HOT_FAIL",)])
        self.assertEqual({t.sources for t in bn.SELFTEST_HOT}, {(bn._ST / "hot_probe.c",)})
        self.assertEqual(len({t.output(out) for t in bn.SELFTEST_HOT}), 3)
        self.assertTrue(all(t.export == "swmod_init" and not t.runtime for t in bn.SELFTEST_HOT))
        self.assertEqual(bn.HOT_FILES, ("hotmod-1.dll", "hotmod-2.dll", "hotfail-1.dll"))

    def test_build_runs_the_plan(self):
        ran = []
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "native"
            built = bn.build(TC, out, (bn.HOST, *bn.EXAMPLES), runner=lambda cmd, env: ran.append((cmd, env)))
            self.assertTrue((out / "mods").is_dir())
            self.assertTrue((out / "obj").is_dir())
        self.assertEqual(built, [out / "modhost.dll", out / "mods" / "hello.dll"])
        self.assertEqual(len(ran), len(bn.HOST.sources) + 1 + 2)
        env = ran[0][1]
        self.assertEqual(env["ZIG_GLOBAL_CACHE_DIR"], str(out / "zig-cache"))
        self.assertEqual(env["ZIG_LOCAL_CACHE_DIR"], str(out / "zig-cache"))

    def test_failed_command(self):
        with self.assertRaisesRegex(bn.BuildError, "failed"):
            bn.run_cmd([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"], dict(os.environ))

    def test_failed_command_with_utf8_output(self):
        # zig prints paths as UTF-8; a name the Windows code page can't decode must still come through
        child = ("import sys; sys.stderr.buffer.write('C:/Users/\\u0441\\u0430\\u0448\\u0430/a.c:1:1: error: boom\\n'"
                 ".encode('utf-8')); sys.exit(1)")
        with self.assertRaises(bn.BuildError) as cm:
            bn.run_cmd([sys.executable, "-c", child], dict(os.environ))
        self.assertIn("C:/Users/\u0441\u0430\u0448\u0430/a.c:1:1: error: boom", str(cm.exception))

    def test_undecodable_output_is_replaced_not_fatal(self):
        child = "import sys; sys.stderr.buffer.write(b'bad \\xff\\xfe bytes'); sys.exit(2)"
        with self.assertRaisesRegex(bn.BuildError, "bad .* bytes"):
            bn.run_cmd([sys.executable, "-c", child], dict(os.environ))

    def test_settings_sources_join_when_they_are_there(self):
        """settings.c joins modhost (and its test build) once it is there; settings_demo is built but not installed."""
        self.assertEqual(bn.HOST.sources[0], bn.HOST_SRC / "modhost.c")
        self.assertEqual(bn.SETTINGS_SRC in bn.HOST.sources, bn.SETTINGS_SRC.is_file())
        self.assertEqual(bn.HOST_TEST.sources, bn.HOST.sources)
        for t in bn.SETTINGS_EXAMPLES:
            self.assertEqual((t.subdir, t.export, t.runtime), ("settings-mods", "swmod_init", False))
            self.assertNotIn(t.name, [x.name for x in bn.EXAMPLES])  # (install copies EXAMPLES only)
        swmod = (bn.HOST_SRC / "swmod.h").read_text(encoding="utf-8")
        self.assertIn(f"#define SWMOD_API_VERSION {bn.API_VERSION}\n", swmod)

    def test_sources_exist(self):
        for t in (bn.HOST, *bn.EXAMPLES, *bn.SELFTEST, *bn.HOOK_EXAMPLES, *bn.GHOST_EXAMPLES, *bn.SELFTEST_HOOKS,
                  *bn.SELFTEST_HOT, bn.HOST_TEST, *bn.SELFTEST_RACE, *bn.SETTINGS_EXAMPLES):
            for s in t.sources:
                self.assertTrue(s.is_file(), s)
        self.assertTrue((bn.HOST_SRC / "swmod.h").is_file())
        for n in (*bn.HOOKED_GEN_FILES, "hooks.txt", "entry.c"):
            self.assertTrue((bn._HK / n).is_file(), n)

    def test_hook_example_is_built_but_not_installed(self):
        out = Path(r"C:\build\native")
        plan = bn.plan_build(TC, out, bn.HOOK_EXAMPLES)
        self.assertEqual([t.name for t, _ in plan], ["stamina_hook"])
        self.assertIn(f"-OUT:{out / 'hook-mods' / 'stamina_hook.dll'}", plan[0][1][1])
        self.assertNotIn(str(TC.implib), plan[0][1][1])  # (a mod links nothing from Savage Wind)
        self.assertNotIn("stamina_hook", [t.name for t in bn.EXAMPLES])  # (install copies EXAMPLES only)

    def test_ghost_example_is_built_but_not_installed(self):
        out = Path(r"C:\build\native")
        plan = bn.plan_build(TC, out, bn.GHOST_EXAMPLES)
        self.assertEqual([t.name for t, _ in plan], ["ghost", "room"])   # (More Room sits with it: docs/room.md)
        self.assertEqual(bn.GHOST_EXAMPLES[0].export, "swmod_init")
        self.assertIn(f"-OUT:{out / 'ghost-mods' / 'ghost.dll'}", plan[0][1][1])
        self.assertIn(f"-OUT:{out / 'ghost-mods' / 'room.dll'}", plan[1][1][1])
        self.assertNotIn(str(TC.implib), plan[0][1][1])  # (a mod links nothing from Savage Wind)
        self.assertNotIn(str(TC.implib), plan[1][1][1])
        self.assertNotIn("ghost", [t.name for t in (*bn.EXAMPLES, *bn.HOOK_EXAMPLES)])
        examples = [p.name for p in bn.install_paths(Path(r"C:\made\up\root"))["examples"]]
        self.assertEqual(examples, ["hello.dll"])   # (install copies hello only)

    def test_build_command_builds_the_ghost_example_too(self):
        seen = []

        def fake_build(tc, out, targets):
            seen.extend(t.name for t in targets)
            return [t.output(out) for t in targets]

        out = Path(r"C:\made\up\native")
        with mock.patch.object(bn, "find_toolchain", return_value=TC), mock.patch.object(bn, "build", fake_build), \
                mock.patch.object(bn, "check_exports"), \
                mock.patch.object(bn, "describe", lambda p, rel=None: str(Path(p).relative_to(rel))), \
                redirect_stdout(io.StringIO()) as text:
            self.assertEqual(bn.main(["build", "--out", str(out)]), 0)
        self.assertEqual(seen, ["modhost", "hello", "stamina_hook", "ghost", "room", "basics_show_pos",
                                "basics_inf_stamina", "basics_teleport_up", *(["settings_demo"] if bn.SETTINGS_EXAMPLES else []),
                                *(t.name for t in bn.EMOTES_EXAMPLES)])
        self.assertIn(str(Path("ghost-mods") / "ghost.dll"), text.getvalue())
        self.assertIn("docs/ghost.md", text.getvalue())
        self.assertIn(str(out / "basics-mods"), text.getvalue())
        self.assertIn("docs/game-basics.md", text.getvalue())

    def test_hooked_fake_game(self):
        """The made-up generated files get hook slots from tools/gen_hooks.py (no compiler: just the C)."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            gen = bn.prepare_hooked_game(out)
            t = bn.hooked_game_target(out)
            self.assertEqual(t.output(out), out / "selftest" / "fake_swgame_hooked.dll")
            for s in t.sources:
                self.assertTrue(s.is_file(), s)
            c0 = (gen / "main_000.c").read_text(encoding="latin-1")
            c1 = (gen / "main_001.c").read_text(encoding="latin-1")
            hooks = (gen / "main_hooks.c").read_text(encoding="latin-1")
            self.assertIn("void f_7000001000__orig(Cpu *c) {", c0)
            self.assertIn("void f_7000001010(Cpu *c) {", c0)  # (calls the hooked one: not renamed)
            self.assertIn("void f_7000cc7410__orig(Cpu *c) {", c1)
            self.assertIn("static void f_7000002020_r(Cpu *c) {", c1)
            self.assertEqual(hooks.count("    if (e && e(c, s)) return;"), 4)
            bn.prepare_hooked_game(out)  # (again: the same files)
            self.assertEqual((gen / "main_000.c").read_text(encoding="latin-1"), c0)


class ModHeader(unittest.TestCase):
    """swmod.h is compiled by the selftest (C); these check the parts it can't (C++ linkage, the version macro)."""

    def setUp(self):
        self.text = (bn.HOST_SRC / "swmod.h").read_text(encoding="utf-8")

    def test_swmod_init_has_c_linkage_in_cpp(self):
        block = self.text[self.text.index('extern "C" {'):self.text.rindex("#ifdef __cplusplus")]
        self.assertIn("SWMOD_EXPORT int swmod_init(const SwModApi *api);", block)
        self.assertIn('#define SWMOD_EXPORT extern "C" __declspec(dllexport)', self.text)

    def test_swmod_exit_is_optional_and_has_c_linkage(self):
        """modhost looks swmod_exit up by name (modhost.c unload_mod): the header must name it the same way."""
        block = self.text[self.text.index('extern "C" {'):self.text.rindex("#ifdef __cplusplus")]
        self.assertIn("SWMOD_EXPORT void swmod_exit(const SwModApi *api);", block)
        self.assertIn('#define SWMOD_EXIT_NAME "swmod_exit"', self.text)
        host = (bn.HOST_SRC / "modhost.c").read_text(encoding="utf-8")
        self.assertIn("GetProcAddress(m->h, SWMOD_EXIT_NAME)", host)

    def test_api_ok_checks_the_version_only(self):
        line = next(ln for ln in self.text.splitlines() if ln.startswith("#define SWMOD_API_OK("))
        self.assertNotIn("sizeof", line)  # (a newer header's bigger struct must not refuse an older host)
        self.assertIn("#define SWMOD_HAS(api, field)", self.text)

    def test_version_2_only_adds_fields_at_the_end(self):
        """Older mods keep working: every version 1 field is where it was, the hook fields (2) come after it, then
        the settings fields (3)."""
        self.assertIn(f"#define SWMOD_API_VERSION {bn.API_VERSION}\n", self.text)
        body = self.text[self.text.index("typedef struct SwModApi {"):self.text.index("} SwModApi;")]
        fields = re.findall(r"^    [^/\n]*?\(?\*?(\w+)\)?(?:\([^;]*\))?;", body, re.M)
        v1 = ["api_version", "size", "host_version", "game_version", "mod_name", "log", "control_register",
              "reply_str", "reply_int", "reply_num", "reply_bool", "reply_text", "reply_fail", "dev_mode",
              "dispatch_lookup", "find_site", "hle_override", "guest_call", "current_cpu"]
        v2 = ["hook", "can_hook"]
        v3 = ["mod_id", "setting_int", "setting_num", "setting_str", "setting_watch", "setting_set", "settings_ui"]
        self.assertEqual(fields, v1 + v2 + v3)  # settings (v3) appended after v2, never in between


# ------------------------------------------------------------------ install planning

def fake_game(data_root: Path, game_id: str = "made-up-id\n") -> None:
    (data_root / "extracted").mkdir(parents=True)
    (data_root / "extracted" / "swgame.dll").write_bytes(b"made up")
    (data_root / "extracted" / "swgame.dll.id").write_text(game_id)


def fake_build(out: Path) -> None:
    (out / "mods").mkdir(parents=True)
    (out / "modhost.dll").write_bytes(b"host")
    (out / "mods" / "hello.dll").write_bytes(b"hello")


class Install(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data = self.root / "Savage Wind"
        self.out = self.root / "build"

    def tearDown(self):
        self._tmp.cleanup()

    def test_plan(self):
        fake_game(self.data)
        fake_build(self.out)
        steps = bn.plan_install(self.out, self.data)
        got = [(s.src, s.dst) for s in steps]
        game = self.data / "extracted"
        self.assertEqual(got, [
            (self.out / "modhost.dll", game / "modhost.dll"),
            (game / "swgame.dll.id", game / "modhost.dll.id"),
            (self.out / "mods" / "hello.dll", self.data / "mods" / "native" / "hello.dll"),
        ])
        self.assertFalse(any(s.dst.name in ("swgame.dll", "swgame.dll.id") for s in steps))

    def test_run_copies_and_never_touches_the_game_code(self):
        fake_game(self.data)
        fake_build(self.out)
        lines = bn.run_steps(bn.plan_install(self.out, self.data))
        game = self.data / "extracted"
        self.assertEqual((game / "modhost.dll").read_bytes(), b"host")
        self.assertEqual((game / "modhost.dll.id").read_text(), "made-up-id\n")
        self.assertEqual((self.data / "mods" / "native" / "hello.dll").read_bytes(), b"hello")
        self.assertEqual((game / "swgame.dll").read_bytes(), b"made up")
        self.assertEqual(len(lines), 3)

    def test_dry_run_changes_nothing(self):
        fake_game(self.data)
        fake_build(self.out)
        lines = bn.run_steps(bn.plan_install(self.out, self.data), dry_run=True)
        self.assertEqual(len(lines), 3)
        self.assertFalse((self.data / "extracted" / "modhost.dll").exists())
        self.assertFalse((self.data / "mods").exists())

    def test_examples_not_built_are_skipped(self):
        fake_game(self.data)
        self.out.mkdir()
        (self.out / "modhost.dll").write_bytes(b"host")
        self.assertEqual(len(bn.plan_install(self.out, self.data)), 2)

    def test_needs_the_game_code_and_the_build(self):
        fake_build(self.out)
        with self.assertRaisesRegex(bn.InstallError, "Set up the game files"):
            bn.plan_install(self.out, self.data)
        (self.data / "extracted").mkdir(parents=True)
        (self.data / "extracted" / "swgame.dll").write_bytes(b"x")
        with self.assertRaisesRegex(bn.InstallError, "swgame.dll.id"):
            bn.plan_install(self.out, self.data)
        (self.data / "extracted" / "swgame.dll.id").write_text("id")
        with self.assertRaisesRegex(bn.InstallError, "isn't built"):
            bn.plan_install(self.root / "empty", self.data)

    def install(self, **kw):
        steps = bn.plan_install(self.out, self.data, **kw)
        bn.run_steps(steps)
        bn.record_install(self.data, steps)
        return steps

    def test_install_records_what_it_wrote(self):
        fake_game(self.data)
        fake_build(self.out)
        self.install()
        record = bn.read_record(self.data)
        self.assertEqual(sorted(record), ["extracted/modhost.dll", "extracted/modhost.dll.id", "mods/native/hello.dll"])
        self.assertEqual(record["mods/native/hello.dll"], hashlib.sha256(b"hello").hexdigest())

    def test_uninstall_removes_only_ours(self):
        fake_game(self.data)
        fake_build(self.out)
        self.install()
        (self.data / "mods" / "native" / "someone_elses.dll").write_bytes(b"keep")
        steps, left = bn.plan_uninstall(self.data)
        self.assertTrue(all(s.src is None for s in steps))
        self.assertEqual(sorted(s.dst.name for s in steps), ["hello.dll", "modhost.dll", "modhost.dll.id"])
        self.assertEqual(left, [])
        bn.run_steps(steps)
        bn.forget_install(self.data)
        self.assertTrue((self.data / "extracted" / "swgame.dll").is_file())
        self.assertTrue((self.data / "extracted" / "swgame.dll.id").is_file())
        self.assertTrue((self.data / "mods" / "native" / "someone_elses.dll").is_file())
        self.assertFalse((self.data / "extracted" / "modhost.dll").exists())
        self.assertFalse((self.data / "extracted" / bn.RECORD).exists())
        self.assertEqual(bn.plan_uninstall(self.data), ([], []))

    def test_uninstall_keeps_a_users_mod_with_our_name(self):
        # never installed: the user's own mods\native\hello.dll stays
        fake_game(self.data)
        mine = self.data / "mods" / "native" / "hello.dll"
        mine.parent.mkdir(parents=True)
        mine.write_bytes(b"the user's own hello")
        steps, left = bn.plan_uninstall(self.data)
        self.assertEqual(steps, [])
        self.assertEqual(left, [(mine, "install didn't write it")])
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(bn.main(["uninstall", "--install", str(self.data)]), 0)
        self.assertTrue(buf.getvalue().startswith("Removed 0 file(s)."))
        self.assertIn(f"left {mine} (install didn't write it", buf.getvalue())
        self.assertEqual(mine.read_bytes(), b"the user's own hello")

    def test_uninstall_keeps_files_changed_since_install(self):
        fake_game(self.data)
        fake_build(self.out)
        self.install()
        hello = self.data / "mods" / "native" / "hello.dll"
        hello.write_bytes(b"replaced by the user")
        steps, left = bn.plan_uninstall(self.data)
        self.assertEqual(sorted(s.dst.name for s in steps), ["modhost.dll", "modhost.dll.id"])
        self.assertEqual(left, [(hello, "changed since install")])

    def test_record_cannot_point_uninstall_elsewhere(self):
        fake_game(self.data)
        game = self.data / "extracted" / "swgame.dll"
        (self.data / "extracted" / bn.RECORD).write_text(json.dumps({"files": {
            "extracted/swgame.dll": hashlib.sha256(game.read_bytes()).hexdigest(),
            "mods/native/../../extracted/swgame.dll": "x", "mods/native/x/y.dll": "x"}}))
        self.assertEqual(bn.read_record(self.data), {})
        self.assertEqual(bn.plan_uninstall(self.data), ([], []))

    def test_unreadable_record_is_ignored(self):
        fake_game(self.data)
        (self.data / "extracted" / bn.RECORD).write_text("not json")
        self.assertEqual(bn.read_record(self.data), {})
        (self.data / "extracted" / bn.RECORD).write_text('{"files": ["a list"]}')
        self.assertEqual(bn.read_record(self.data), {})

    def test_install_wont_replace_a_users_file(self):
        fake_game(self.data)
        fake_build(self.out)
        mine = self.data / "mods" / "native" / "hello.dll"
        mine.parent.mkdir(parents=True)
        mine.write_bytes(b"the user's own hello")
        with self.assertRaisesRegex(bn.InstallError, "install didn't write them") as cm:
            bn.plan_install(self.out, self.data)
        self.assertIn(str(mine), str(cm.exception))
        self.assertIn("--force", str(cm.exception))
        self.assertEqual(mine.read_bytes(), b"the user's own hello")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(bn.main(["install", "--install", str(self.data), "--out", str(self.out)]), 1)
        self.assertTrue(buf.getvalue().startswith("Error: These files are already there"))
        self.assertEqual(mine.read_bytes(), b"the user's own hello")
        self.install(force=True)
        self.assertEqual(mine.read_bytes(), b"hello")

    def test_reinstall_over_own_files_and_identical_files(self):
        fake_game(self.data)
        fake_build(self.out)
        self.install()
        (self.out / "modhost.dll").write_bytes(b"host, rebuilt")  # a new build replaces what install wrote
        self.install()
        self.assertEqual((self.data / "extracted" / "modhost.dll").read_bytes(), b"host, rebuilt")
        self.assertEqual(bn.read_record(self.data)["extracted/modhost.dll"],
                         hashlib.sha256(b"host, rebuilt").hexdigest())
        bn.forget_install(self.data)  # no record, but the files are the same as the build: fine
        bn.plan_install(self.out, self.data)

    def test_cli_install_and_uninstall(self):
        fake_game(self.data)
        fake_build(self.out)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(bn.main(["install", "--install", str(self.data), "--out", str(self.out)]), 0)
        self.assertTrue((self.data / "extracted" / bn.RECORD).is_file())
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(bn.main(["uninstall", "--install", str(self.data), "--dry-run"]), 0)
        self.assertTrue(buf.getvalue().startswith("Would remove 3 file(s)."))
        self.assertTrue((self.data / "extracted" / bn.RECORD).is_file())
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(bn.main(["uninstall", "--install", str(self.data)]), 0)
        self.assertTrue(buf.getvalue().startswith("Removed 3 file(s)."))
        self.assertFalse((self.data / "extracted" / bn.RECORD).exists())
        self.assertEqual(sorted(p.name for p in (self.data / "extracted").iterdir()), ["swgame.dll", "swgame.dll.id"])

    def test_launch_lines_quote_apostrophes(self):
        inst = Path(r"C:\Users\O'Neil\Documents\SavageWind\Savage Wind")
        lines = bn.launch_lines(inst, inst)
        self.assertEqual(lines[0], "$env:SAVAGEWIND_GAME_LIB = "
                                   r"'C:\Users\O''Neil\Documents\SavageWind\Savage Wind\extracted\modhost.dll'")
        self.assertEqual(lines[-1], r"& 'C:\Users\O''Neil\Documents\SavageWind\Savage Wind\savagewind.exe' --play")
        other = bn.launch_lines(inst, Path(r"D:\it's"))
        self.assertIn(r"$env:SAVAGEWIND_DATA_ROOT = 'D:\it''s'", other)

    def test_launch_lines_quote_curly_quotes(self):
        # PowerShell also ends a '...' string at U+2018-U+201B: each is written twice, like '
        inst = Path("C:\\Users\\O\u2019Neil\\Documents\\SavageWind\\Savage Wind")
        lines = bn.launch_lines(inst, inst)
        self.assertEqual(lines[0], "$env:SAVAGEWIND_GAME_LIB = "
                                   "'C:\\Users\\O\u2019\u2019Neil\\Documents\\SavageWind\\Savage Wind\\extracted"
                                   "\\modhost.dll'")
        self.assertEqual(lines[-1], "& 'C:\\Users\\O\u2019\u2019Neil\\Documents\\SavageWind\\Savage Wind"
                                    "\\savagewind.exe' --play")
        other = bn.launch_lines(inst, Path("D:\\\u2018a\u201ab\u201bc'd"))
        self.assertIn("$env:SAVAGEWIND_DATA_ROOT = 'D:\\\u2018\u2018a\u201a\u201ab\u201b\u201bc''d'", other)
        self.assertEqual(bn.ps_quote("\u201c\u201d\u0441\u0430\u0448\u0430"), "'\u201c\u201d\u0441\u0430\u0448\u0430'")

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell"), "needs Windows PowerShell")
    def test_ps_quote_round_trips_through_powershell(self):
        """PowerShell itself reads each quoted value back unchanged (it only assigns strings: nothing is started)."""
        values = ["C:\\Users\\O\u2019Neil\\Documents", "it's", "\u2018a\u2019b\u201ac\u201bd'", "''\u2019\u2019",
                  "C:\\\u0441\u0430\u0448\u0430 \u201cx\u201d", ""]
        script = "\n".join(f"$v = {bn.ps_quote(v)}; [Console]::Out.WriteLine('<' + (($v.ToCharArray() | "
                           "ForEach-Object { [int]$_ }) -join ',') + '>')" for v in values)
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                            base64.b64encode(script.encode("utf-16-le")).decode()],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = ["".join(chr(int(c)) for c in ln.strip()[1:-1].split(",") if c) for ln in r.stdout.splitlines()
               if ln.startswith("<")]
        self.assertEqual(got, values)

    def test_launch_lines(self):
        inst = Path(r"C:\Games\Savage Wind")
        lines = bn.launch_lines(inst, inst, 7777)
        self.assertEqual(lines[0], r"$env:SAVAGEWIND_GAME_LIB = 'C:\Games\Savage Wind\extracted\modhost.dll'")
        self.assertTrue(lines[1].startswith("$env:SAVAGEWIND_CONTROL = '7777'"))
        self.assertEqual(lines[-1], r"& 'C:\Games\Savage Wind\savagewind.exe' --play")
        self.assertFalse(any("DATA_ROOT" in ln for ln in lines))
        other = bn.launch_lines(inst, Path(r"D:\swdata"), 9000)
        self.assertIn(r"$env:SAVAGEWIND_GAME_LIB = 'D:\swdata\extracted\modhost.dll'", other)
        self.assertIn(r"$env:SAVAGEWIND_DATA_ROOT = 'D:\swdata'", other)

    def test_cli_install_dry_run(self):
        fake_game(self.data)
        fake_build(self.out)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = bn.main(["install", "--install", str(self.data), "--out", str(self.out), "--dry-run"])
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        self.assertTrue(text.startswith("Would copy 3 file(s); Savage Wind is unchanged."))
        self.assertIn("SAVAGEWIND_GAME_LIB", text)
        self.assertFalse((self.data / "extracted" / "modhost.dll").exists())

    def test_cli_errors_are_short(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = bn.main(["install", "--install", str(self.data), "--out", str(self.out)])
        self.assertEqual(rc, 1)
        self.assertTrue(buf.getvalue().startswith("Error: No game code at"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = bn.main(["build", "--install", str(self.root / "nowhere"), "--out", str(self.out)])
        self.assertEqual(rc, 1)
        self.assertIn("No zig", buf.getvalue())

    def test_cli_imports(self):
        p = self.root / "m.dll"
        p.write_bytes(make_pe({"savagewind.exe": ["guest_call"]}, ["swmod_init"]))
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(bn.main(["imports", str(p)]), 0)
        self.assertIn("exports swmod_init; imports 1 function(s) from savagewind.exe (guest_call)", buf.getvalue())


# ------------------------------------------------------------------ check (a fake game connection)

from control import ControlError  # noqa: E402 (tools/control.py)


class FakeConnection:
    def __init__(self, replies):
        self.replies = replies
        self.sent = []
        self.closed = False

    def cmd(self, *words):
        self.sent.append(words)
        r = self.replies.get(words[0])
        if isinstance(r, Exception):
            raise r
        return r

    def close(self):
        self.closed = True


class Check(unittest.TestCase):
    def run_check(self, connect):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = bn.check_game(7777, connect=connect)
        return rc, buf.getvalue()

    def test_mods_loaded(self):
        conn = FakeConnection({
            "mods": {"found": 1, "loaded": 1, "game_version": "1.2.3", "api": 1,
                     "mods.0.file": "hello.dll", "mods.0.status": "ok"},
            "hello": {"hello": "world", "mods_api": 1},
        })
        rc, text = self.run_check(lambda: conn)
        self.assertEqual(rc, 0)
        self.assertTrue(text.startswith("modhost is running: 1 of 1 mod(s) loaded (game 1.2.3, API 1)"))
        self.assertIn("hello.dll: ok", text)
        self.assertIn('hello -> {"ok": true, "data": {"hello": "world", "mods_api": 1}}', text)
        self.assertTrue(conn.closed)

    def test_game_without_modhost(self):
        conn = FakeConnection({"mods": ControlError("Unknown command: mods")})
        rc, text = self.run_check(lambda: conn)
        self.assertEqual(rc, 1)
        self.assertTrue(text.startswith("The game runs, but modhost isn't loaded"))

    def test_no_answer_is_not_reported_as_missing_modhost(self):
        for error in ("the game didn't answer in time", "the game closed the connection", "invalid reply from the game: x"):
            conn = FakeConnection({"mods": ControlError(error)})
            rc, text = self.run_check(lambda: conn)
            self.assertEqual(rc, 1)
            self.assertTrue(text.startswith(f"The game didn't answer `mods`: {error}"), text)
            self.assertNotIn("isn't loaded", text)
            self.assertNotIn("SAVAGEWIND_GAME_LIB", text)
            self.assertTrue(conn.closed)

    def test_skipped_commands_are_shown(self):
        conn = FakeConnection({
            "mods": {"found": 1, "loaded": 1, "mods.0.file": "mine.dll", "mods.0.status": "ok",
                     "mods.0.skipped": "hello"},
            "hello": {"hello": "world"},
        })
        rc, text = self.run_check(lambda: conn)
        self.assertEqual(rc, 0)
        self.assertIn("mine.dll: ok (commands not added, name taken or table full: hello)", text)

    def test_no_game(self):
        def refuse():
            raise ControlError("can't connect to the game on 127.0.0.1:7777")
        rc, text = self.run_check(refuse)
        self.assertEqual(rc, 1)
        self.assertTrue(text.startswith("No answer from the game on port 7777"))

    def test_hello_missing_is_reported_not_fatal(self):
        conn = FakeConnection({"mods": {"found": 0, "loaded": 0}, "hello": ControlError("Unknown command: hello")})
        rc, text = self.run_check(lambda: conn)
        self.assertEqual(rc, 0)
        self.assertIn('"error": "Unknown command: hello"', text)


# ------------------------------------------------------------------ selftest result checks (made-up results)

def good_result() -> dict:
    files = ["bad.dll", "hello.dll", "junk.dll", "noinit.dll", "probe.dll"]
    status = ["init failed (code 7)", "ok", "load failed (error 193)", "loaded, no swmod_init", "ok"]
    mods = {"host": "9.9.9", "found": 5, "loaded": 2, "game_version": "0.0.0-selftest"}
    for i, (f, s) in enumerate(zip(files, status)):
        mods[f"mods.{i}.file"] = f
        mods[f"mods.{i}.status"] = s
    mods["mods.1.commands"] = "hello"
    mods["mods.4.commands"] = "probe,probe_more"
    mods["mods.4.skipped"] = "help,ping,mods,hello,probe,probe_full"
    return {
        "tables": True, "same_twice": True,
        "mods": {"ok": True, "data": mods, "text": "2 of 5 native mods loaded (modhost 9.9.9, API 1)\nbad.dll: ..."},
        "hello": {"ok": True, "data": {"hello": "world", "mods_api": bn.API_VERSION}, "text": ""},
        "probe": {"ok": True, "text": "probe ok: 42 lines\nsecond line",
                  "data": {"site": 0x7000123450, "no_site": 0, "found_fn": True, "fn_x0": 42, "between_fns": True,
                           "outside": True, "mod": "probe", "game": "0.0.0-selftest", "host": "9.9.9",
                           "size_ok": True, "registered": 2, "cpu_ptr": 0x5FF000, "half": 0.5, "dev": True,
                           "cpu_null": True, "ok_smaller_host": True, "ok_older_version": False, "has_field": True,
                           "has_missing_field": False}},
        "probe fail": {"ok": False, "error": "failing on purpose: fail"},
        "#overrides": {"ok": True, "data": {"count": 1, "0.addr": 0x7000001000, "0.name": "probe_override"}},
        "#guest_calls": {"ok": True, "data": {"count": 1, "0.cpu": 0x5FF000, "0.addr": 0x7000003000}},
        "#commands": {"ok": True, "data": {"0": "help", "1": "ping", "2": "mods", "3": "modload", "4": "modunload",
                                           "5": "modreload", "6": "modsettings", "7": "modset", "8": "hello",
                                           "9": "probe", "10": "probe_more"}},
    }


GOOD_LOG = "\n".join(["[modhost] modhost 9.9.9 (mod API 1)", *bn.SELFTEST_LOG, "[modhost] mod x.dll: ok"])


class SelftestChecks(unittest.TestCase):
    def test_good_result_passes(self):
        self.assertEqual(bn.selftest_expect(good_result()), [])
        self.assertEqual(bn.selftest_expect_log(GOOD_LOG), [])

    def test_problems_are_named(self):
        r = good_result()
        r["hello"] = {"ok": False, "error": "Unknown command: hello"}
        r["#overrides"]["data"]["count"] = 0
        bad = bn.selftest_expect(r)
        self.assertEqual(len(bad), 2)
        self.assertTrue(bad[0].startswith("hello answers"))

    def test_commands_a_mod_did_not_get_are_caught(self):
        # the old host counted every name it passed on, including the ones Savage Wind refused
        r = good_result()
        r["mods"]["data"]["mods.4.commands"] = "probe,help,ping,mods,hello,probe,probe_more,probe_full"
        r["mods"]["data"]["mods.4.skipped"] = ""
        r["probe"]["data"]["registered"] = 8
        bad = bn.selftest_expect(r)
        self.assertEqual(len(bad), 3)
        self.assertTrue(bad[0].startswith("each mod lists only the commands it got"))
        self.assertTrue(bad[2].startswith("control_register returns only what was added"))

    def test_guest_call_is_checked(self):
        for calls in ({"count": 0}, {"count": 2, "0.cpu": 0x5FF000, "0.addr": 0x7000003000},
                      {"count": 1, "0.cpu": 0, "0.addr": 0x7000003000},
                      {"count": 1, "0.cpu": 0x5FF000, "0.addr": 0x7000003004}):
            r = good_result()
            r["#guest_calls"]["data"] = calls
            bad = bn.selftest_expect(r)
            self.assertEqual(len(bad), 1, calls)
            self.assertTrue(bad[0].startswith("guest_call is passed on"))

    def test_host_version_text_and_macros_are_checked(self):
        for key, value in (("host", ""), ("host", "1.0.0"), ("size_ok", False), ("ok_smaller_host", False),
                           ("has_missing_field", True)):
            r = good_result()
            r["probe"]["data"][key] = value
            self.assertEqual(len(bn.selftest_expect(r)), 1, key)
        r = good_result()
        r["probe"]["text"] = ""
        self.assertEqual(bn.selftest_expect(r), ["reply_text formats its lines ('')"])
        r = good_result()
        r["mods"]["text"] = ""
        self.assertEqual(bn.selftest_expect(r), ["`mods` prints a summary line"])

    def test_log_is_checked(self):
        log = GOOD_LOG.replace("[mod] probe: ready, 2 command(s)\n", "")
        self.assertEqual(bn.selftest_expect_log(log), ["log line: [mod] probe: ready, 2 command(s)"])
        self.assertEqual(len(bn.selftest_expect_log("")), len(bn.SELFTEST_LOG))

    def test_fallbacks(self):
        missing = {"tables": False, "#commands": {"ok": True, "data": {"0": "help", "1": "ping"}}}
        other = {"tables": True, "#commands": {"ok": True, "data": {"0": "help", "1": "ping"}}}
        self.assertEqual(bn.selftest_expect_fallback(
            missing, "[modhost] can't load the game code x (error 126)",
            other, "[modhost] that game code was built for another Savage Wind version"), [])
        mods_loaded = {"tables": True, "#commands": {"ok": True, "data": {"0": "help", "1": "ping", "2": "mods"}}}
        self.assertEqual(len(bn.selftest_expect_fallback(mods_loaded, "", mods_loaded, "")), 4)
        self.assertEqual(len(bn.selftest_expect_fallback({"tables": False}, "can't load the game code",
                                                         {"tables": True}, "another Savage Wind version")), 2)


def good_hook_results() -> tuple[dict, str, dict, str]:
    """A made-up hook run (fake game with slots) and plain run (no slots) as the selftest should see them."""
    hooked = {
        "mods": {"ok": True, "data": {"hook_slots": 4, "hooked": 3, "loaded": 2, "mods.0.file": "hook_probe.dll",
                                      "mods.1.file": "stamina_hook.dll", "mods.0.hooks": "0x7000001000,0x7000002020",
                                      "mods.1.hooks": "0x7000cc7410"}},
        "hookprobe": {"ok": True, "data": {
            "api": bn.API_VERSION, "rc_add": 0, "rc_times3": 0, "rc_taken": 3, "rc_no_slot": 2, "rc_bad_args": 5,
            "rc_late": 4, "can_hook_slot": True, "can_hook_no_slot": False, "add": 103, "add_skipped": 7,
            "direct_call": 206, "tail_jump": 104, "times3": 19, "five": 5, "no_slot_fn": 77, "setter_down": 100.0,
            "setter_up": 150.0, "before_runs": 4, "after_runs": 1, "replace_runs": 1, "regs_layout": True}},
        "stamina_hook": {"ok": True, "data": {"on": True, "calls": 2, "kept": 1}},
    }
    plain = {
        "mods": {"ok": True, "data": {"hook_slots": 0, "mods.1.status": "init failed (code 2)"}},
        "hookprobe": {"ok": True, "data": {"rc_add": 1, "rc_times3": 1, "rc_no_slot": 1, "rc_bad_args": 5,
                                           "rc_late": 4, "add": 41, "can_hook_slot": False}},
    }
    return hooked, "\n".join(bn.HOOK_LOG), plain, f"[modhost] x\n{bn.NO_SLOTS_LINE}\n"


class HookSelftestChecks(unittest.TestCase):
    def test_good_results_pass(self):
        self.assertEqual(bn.selftest_expect_hooks(*good_hook_results()), [])

    def test_a_missed_direct_call_is_caught(self):
        # (what happens without the rename: the call inside the same file runs the function, not the hook)
        hooked, log, plain, plain_log = good_hook_results()
        hooked["hookprobe"]["data"].update(direct_call=6, before_runs=3)
        bad = bn.selftest_expect_hooks(hooked, log, plain, plain_log)
        self.assertEqual(len(bad), 2)
        self.assertTrue(bad[0].startswith("a direct call from the same file reaches the hook (6)"), bad)

    def test_stamina_and_log_lines_are_checked(self):
        hooked, log, plain, plain_log = good_hook_results()
        hooked["hookprobe"]["data"]["setter_down"] = 50.0
        log = log.replace("[mod] stamina_hook: ready", "")
        bad = bn.selftest_expect_hooks(hooked, log, plain, plain_log)
        self.assertEqual(len(bad), 2, bad)
        self.assertTrue(bad[0].startswith("stamina_hook keeps the value from going down"))
        self.assertEqual(bad[1], "hook log line: [mod] stamina_hook: ready")

    def test_no_slots_line_once_per_mod(self):
        hooked, log, plain, _ = good_hook_results()
        for plain_log in ("", f"{bn.NO_SLOTS_LINE}\n{bn.NO_SLOTS_LINE}\n"):
            self.assertEqual(bn.selftest_expect_hooks(hooked, log, plain, plain_log),
                             ["without hook slots: one clear log line per mod"])


def good_hot_results() -> tuple[dict, str, dict]:
    """A made-up hot-load run (modload / modreload / modunload) and its run without hook slots, as the selftest
    should see them: replies by step name (bn.hot_steps)."""
    def okd(**data):
        return {"ok": True, "data": data}

    def fail(error):
        return {"ok": False, "error": error}

    busy = "hotmod is still in use: ... Try again; if it keeps failing, restart the game to replace this mod"
    gone = "the mod that added this command is not loaded any more (load it again with modload)"
    steps = {
        "mods_start": okd(found=1, hot=0, **{"mods.0.name": "hook_probe", "mods.0.hot": False}),
        "load": okd(name="hotmod", id=2, replaced=False, commands="hot", overrides=bn.HOT_ADDR, no_slot="",
                    hooks="0x7000002030,0x7000001000,0x7000cc7410"),
        "hot_v1": okd(version=1, mod="hotmod", add=1103, five=105),
        "call_v1": okd(x0=1001),
        "load_again": fail("a mod named hotmod is already loaded: use modreload hotmod <path> to replace it"),
        "block": okd(blocked=True),
        "unload_busy": fail(busy),
        "hot_after_busy": okd(version=1, mod="hotmod", add=1103, five=105),
        "call_after_busy": okd(x0=1001),
        "release": okd(released=True),
        "reload": okd(name="hotmod", id=3, replaced=True, commands="hot,hot2", no_slot=bn.HOT_ADDR,
                      notes=f"{bn.HOT_ADDR} has no hook slot in this game code: that function needs a rebuild ..."),
        "hot_v2": okd(version=2, mod="hotmod", add=103, five=205),
        "hot2": okd(version=2),
        "call_v2": okd(x0=2002),
        "reload_missing": fail("hotmod was not replaced, the old build still runs: there is no file C:\\x\\nosuch-9.dll"),
        "reload_junk": fail("hotmod was not replaced, the old build still runs: junk-1.dll: load failed (error 193: "
                            "not a 64-bit Windows DLL)"),
        "hot_after_bad_reload": okd(version=2, mod="hotmod"),
        "load_unquoted": fail("there is no file C:\\no such folder\\nosuch-1.dll (a path with spaces must be in double "
                              "quotes: ...)"),
        "load_fail": fail("hotfail: swmod_init failed (code 3), so it was unloaded again. hle_override "
                          f"{bn.HOT_ADDR} refused: hotmod overrides it already"),
        "hotfail": fail(f"hotfail: {gone}"),
        "hot_after_fail": okd(version=2, five=205),
        "unload_start_mod": fail("hook_probe was loaded at game start (hook_probe.dll): restart the game to replace "
                                 "this mod"),
        "load_junk": fail("junk-1.dll: load failed (error 193: not a 64-bit Windows DLL)"),
        "mods_hot": okd(found=2, hot=1, **{"mods.1.name": "hotmod", "mods.1.hot": True, "mods.1.id": 3,
                                          "mods.1.overrides": bn.HOT_ADDR}),
        "unload": okd(name="hotmod", id=3, unloaded=True),
        "hot_gone": fail(f"hot: {gone}"),
        "call_gone": okd(x0=77),
        "hookprobe": okd(add=103, five=5, times3=19),
        "overrides": okd(count=1),
        "mods_end": okd(found=1, hot=0, hooked=2),
        "unload_none": fail("no mod named nosuch is loaded (the command mods lists them)"),
    }
    plain = {
        "load": okd(name="hotmod", no_slot="0x7000002030,0x7000001000,0x7000cc7410",
                    notes="this game code has no hook slots, so no hook runs: rebuild the game code with hooks ..."),
        "hot": okd(version=1, add=41),
        "unload": okd(unloaded=True),
    }
    return steps, "\n".join(bn.HOT_LOG), plain


class HotSelftestChecks(unittest.TestCase):
    def test_good_results_pass(self):
        self.assertEqual(bn.selftest_expect_hot(*good_hot_results()), [])

    def test_steps_match_the_run(self):
        """Every step the checks read is a step of the run, and the paths are quoted for the control protocol."""
        steps, _, plain = good_hot_results()
        run_dir = Path(r"C:\made up\with space")
        self.assertEqual([k for k, _ in bn.hot_steps(run_dir)], list(steps))
        self.assertEqual([k for k, _ in bn.hot_plain_steps(run_dir)], list(plain))
        load = dict(bn.hot_steps(run_dir))["load"]
        self.assertEqual(load, 'modload "C:\\\\made up\\\\with space\\\\hotmod-1.dll"')

    def test_control_word_matches_control_py(self):
        sys.path.insert(0, str(bn.REPO / "tools"))
        import control  # noqa: E402 (tools/control.py: what a client sends)
        for word in (r"C:\a b\x.dll", 'say "hi"', r"C:\a\b"):
            self.assertEqual(bn.control_word(word), control.quote(word))

    def test_an_unload_that_should_be_refused_is_caught(self):
        steps, log, plain = good_hot_results()
        steps["unload_busy"] = {"ok": True, "data": {"name": "hotmod", "unloaded": True}}
        bad = bn.selftest_expect_hot(steps, log, plain)
        self.assertEqual(len(bad), 1, bad)
        self.assertTrue(bad[0].startswith("modunload refuses while a thread is inside the mod's hook"))

    def test_old_hooks_left_after_reload_are_caught(self):
        steps, log, plain = good_hot_results()
        steps["hot_v2"]["data"]["add"] = 1103  # (v1's before hook still ran)
        bad = bn.selftest_expect_hot(steps, log, plain)
        self.assertEqual(len(bad), 1, bad)
        self.assertTrue(bad[0].startswith("the new build runs, and the old build's hooks are gone"))

    def test_exit_after_failed_init_and_missing_log_lines_are_caught(self):
        steps, log, plain = good_hot_results()
        log = log.replace("[mod] hot: exit (v1)", "[mod] hot: exit (v9)")
        bad = bn.selftest_expect_hot(steps, log, plain)
        self.assertEqual(bad, ["hot log line: [mod] hot: exit (v1)", "swmod_exit isn't called when swmod_init failed"])

    def test_missing_rebuild_note_without_slots_is_caught(self):
        steps, log, plain = good_hot_results()
        plain["load"]["data"]["notes"] = ""
        bad = bn.selftest_expect_hot(steps, log, plain)
        self.assertEqual(len(bad), 1, bad)
        self.assertTrue(bad[0].startswith("without hook slots, modload says the hooks need a rebuild"))

    def test_reload_that_drops_the_old_build_is_caught(self):
        """modreload with a bad file must keep the old build (it checks the file before it unloads)."""
        steps, log, plain = good_hot_results()
        steps["reload_junk"] = {"ok": False, "error": "the old hotmod was unloaded, but the new one didn't load: "
                                                      "junk-1.dll: load failed (error 193)"}
        steps["hot_after_bad_reload"] = {"ok": False, "error": "hot: the mod that added this command is not loaded"}
        bad = bn.selftest_expect_hot(steps, log, plain)
        self.assertEqual(len(bad), 2, bad)
        self.assertTrue(bad[0].startswith("modreload checks the new file before it unloads the old build"))


def good_race_results() -> tuple[dict, dict, str]:
    """A made-up race run, as the selftest should see it: replies and times by step name (bn.race_steps)."""
    def okd(**data):
        return {"ok": True, "data": data}

    steps = {k: okd() for k, _ in bn.race_steps(Path(r"C:\x"))}
    steps.update({
        "block": okd(blocked=True),
        "load_other": okd(name="hotother", hooks=f"{bn.FN_FIVE},{bn.FN_ADD},{bn.FN_SETTER}"),
        "release": okd(released=True),
        "race_wait": okd(done=True, x0=5),
        "race_tail_wait": okd(done=True, x0=6),
        "mods_tail": okd(**{"mods.0.name": "race_probe", "mods.0.overrides": "", "mods.1.name": "misusetail",
                            "mods.1.overrides": ""}),
        "call_after_tail": {"ok": False, "error": f"no override for {bn.FN_ADD}"},
        "call_rearm": okd(x0=4242, name="misuse_rearm"),
        "race_rearm_wait": okd(done=True, x0=0),
        "call_after_rearm": okd(x0=5, name="misuse_rearm"),
        "overrides": okd(**{"count": 2, "0.addr": 0x7000001020, "0.name": "hot_override", "1.addr": 0x7000001000,
                            "1.name": "misuse_rearm"}),
    })
    ms = {k: 1.0 for k in steps}
    ms["race_wait"] = 300.0
    return steps, ms, "\n".join(bn.RACE_LOG)


class RaceSelftestChecks(unittest.TestCase):
    """selftest_expect_race catches what the review found: an unload that frees code a game thread is about to run,
    overrides a mod's hook adds (as a tail call, or while modunload waits), and modload waiting on a game thread."""

    def test_good_results_pass(self):
        self.assertEqual(bn.selftest_expect_race(*good_race_results()), [])

    def test_steps(self):
        steps = bn.race_steps(Path(r"C:\made up"))
        self.assertEqual(len({k for k, _ in steps}), len(steps))
        lines = dict(steps)
        self.assertEqual(lines["load"], 'modload "C:\\\\made up\\\\hotmod-1.dll"')
        self.assertTrue(lines["pause"].startswith("!pause ") and lines["unpause"] == "!pause 0")
        self.assertLess([k for k, _ in steps].index("pause"), [k for k, _ in steps].index("unload"))
        self.assertEqual(bn.HOST_TEST.defines, ("MODHOST_SELFTEST_PAUSE",))
        self.assertEqual(bn.HOST_TEST.sources, bn.HOST.sources)   # (the same modhost.c, one pause point more)
        self.assertNotEqual(bn.HOST_TEST.output(Path("C:/b")), bn.HOST.output(Path("C:/b")))

    def test_stale_hooks_after_unload_are_caught(self):
        steps, ms, log = good_race_results()
        steps["race_wait"] = {"ok": True, "data": {"done": True, "x0": 105}}   # (the freed mod's hook ran)
        bad = bn.selftest_expect_race(steps, ms, log)
        self.assertEqual(len(bad), 1, bad)
        self.assertTrue(bad[0].startswith("a thread that saw the mod's hooks just before modunload"))

    def test_overrides_from_hooks_are_caught(self):
        steps, ms, log = good_race_results()
        steps["call_after_tail"] = {"ok": True, "data": {"x0": 4242, "name": "misuse_tail"}}
        steps["call_after_rearm"] = {"ok": True, "data": {"x0": 4242}}
        log = log.replace(bn.MISUSE_REFUSED, "")
        bad = bn.selftest_expect_race(steps, ms, log)
        self.assertEqual(len(bad), 4, bad)

    def test_a_waiting_modload_is_caught(self):
        steps, ms, log = good_race_results()
        ms["load_other"] = 1510.0
        bad = bn.selftest_expect_race(steps, ms, log)
        self.assertEqual(len(bad), 1, bad)
        self.assertTrue(bad[0].startswith("modload doesn't wait for a game thread"))


class FakeGameRun(unittest.TestCase):
    """_run_fake_game with a stand-in child (no DLLs): its UTF-8 log must come through intact."""

    def test_utf8_log(self):
        child = ("import json, sys; sys.stderr.buffer.write('[modhost] mod \u0441\u0430\u0448\u0430.dll: ok\\n'"
                 ".encode('utf-8')); print(json.dumps({'argv': sys.argv[3:]}))")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(bn, "_CHILD", child):
            (Path(d) / "selftest").mkdir()
            result, log = bn._run_fake_game(Path(d), Path(d) / "game.dll", Path(d) / "mods", ("mods", "#commands"))
        self.assertEqual(result, {"argv": ["mods", "#commands"]})
        self.assertIn("[modhost] mod \u0441\u0430\u0448\u0430.dll: ok", log)

    def test_crash_is_a_build_error(self):
        child = "import sys; sys.stderr.buffer.write(b'\\xff boom'); sys.exit(5)"
        with tempfile.TemporaryDirectory() as d, mock.patch.object(bn, "_CHILD", child):
            (Path(d) / "selftest").mkdir()
            with self.assertRaisesRegex(bn.BuildError, "crashed \\(exit 5\\):\n.* boom"):
                bn._run_fake_game(Path(d), Path(d) / "game.dll", Path(d) / "mods", ())

    def test_relative_out(self):
        """--out rel-build: the child runs in rel-build/selftest, so the paths it gets must still find the files
        (relative ones would point at rel-build/selftest/rel-build/selftest/...)."""
        child = ("import json, os, sys; print(json.dumps({'cwd': os.getcwd(), 'found': [os.path.isfile(sys.argv[1]),"
                 " os.path.isfile(sys.argv[2]), os.path.isfile(os.environ['SWMOD_REAL_GAME_LIB']),"
                 " os.path.isdir(os.environ['SWMOD_DIR'])]}))")
        with tempfile.TemporaryDirectory() as d, contextlib.chdir(d), mock.patch.object(bn, "_CHILD", child):
            rel = Path("rel-build")
            (rel / "selftest" / "mods").mkdir(parents=True)
            for f in (rel / "selftest" / bn.RUNTIME_DLL, rel / bn.HOST_DLL, rel / "selftest" / "game.dll"):
                f.write_bytes(b"made-up")
            result, _ = bn._run_fake_game(rel, rel / "selftest" / "game.dll", rel / "selftest" / "mods", ())
            self.assertEqual(Path(result["cwd"]).resolve(), (rel / "selftest").resolve())
        self.assertEqual(result["found"], [True, True, True, True])

    def test_cli_makes_out_absolute(self):
        seen = []
        with tempfile.TemporaryDirectory() as d, contextlib.chdir(d), \
                mock.patch.object(bn, "find_toolchain", return_value=TC), \
                mock.patch.object(bn, "selftest", lambda tc, out: seen.append(out) or 0):
            self.assertEqual(bn.main(["selftest", "--install", d, "--out", "rel-build"]), 0)
            self.assertEqual(seen, [(Path(d) / "rel-build").resolve()])
        self.assertTrue(seen[0].is_absolute())


if __name__ == "__main__":
    unittest.main()
