"""Tests for tools/mod_studio.py: templates, the names it completes, the build commands (subprocess mocked: nothing is
compiled), the compiler's errors in plain words, loading into a made-up game over the control protocol (a fake
server on 127.0.0.1), the mod library path (a fake modman), and the window with its suggestion box (a hidden Tk
window; skipped without a display). No game files, no game, no build."""
import json
import os
import shlex
import shutil
import socketserver
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_native as bn  # noqa: E402
import mod_studio as st  # noqa: E402
import mod_symbols as ms  # noqa: E402
from test_mod_symbols import made_up_catalog, write_repo  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TC = bn.Toolchain(Path(r"C:\made-up\zig\zig.exe"), Path(r"C:\made-up\savagewind.dll.a"), Path(r"C:\made-up\zig\rt"))


def fixture_index():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cm = write_repo(root)
        return ms.build_index(ms.build_catalog(root, cm))


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mod-studio-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------- mods, templates, mod.toml

class Mods(Temp):
    def test_names(self):
        self.assertIsNone(st.check_name("speed_up2"))
        for bad in ("", "a", "Speed", "2fast", "with space", "dash-name", "x" * 32, "int"):
            self.assertIsNotNone(st.check_name(bad), bad)

    def test_new_mod_from_each_template(self):
        for key in st.TEMPLATES:
            name = "m_" + key.replace("-", "_")
            mod = st.new_mod(self.tmp, name, key)
            src = mod.source.read_text(encoding="utf-8")
            self.assertNotIn("{name}", src)
            self.assertIn(f"{name}: a native mod", src)
            self.assertIn("SWMOD_EXPORT int swmod_init", src)
            self.assertTrue(mod.manifest.is_file())
        self.assertEqual(st.list_mods(self.tmp), sorted("m_" + k.replace("-", "_") for k in st.TEMPLATES))
        with self.assertRaises(st.StudioError):
            st.new_mod(self.tmp, "m_empty", "empty")      # already there
        with self.assertRaises(st.StudioError):
            st.new_mod(self.tmp, "other", "no-such-template")
        (self.tmp / ".build").mkdir(exist_ok=True)
        self.assertNotIn(".build", st.list_mods(self.tmp))

    def test_templates_use_only_real_names(self):
        """Every botw_/BOTW_/SWMOD_/api-> name in the templates is in the repo's headers (so they compile)."""
        if not (REPO / ms.BOTW_H).is_file():
            self.skipTest("native/modhost/botw.h isn't there yet")
        known = {e["name"] for e in ms.build_catalog(REPO)}
        import re
        for key, (_, _, text) in st.TEMPLATES.items():
            for n in set(re.findall(r"\b(?:botw_\w+|BOTW_\w+|SWMOD_\w+|SwMod\w+)\b", text)):
                self.assertIn(n, known, f"{n} in template {key}")
            for n in set(re.findall(r"\bapi->(\w+)", text)):
                self.assertIn(f"api->{n}", known, f"api->{n} in template {key}")

    def test_manifest_matches_modman(self):
        import modman
        mod = st.Mod("speedy", self.tmp)
        for hooks, game in (((), False), ((), True), ((0x7000cc7410,), True)):
            man, errs = modman.parse_manifest(st.manifest_text(mod, hooks, game, "1.2.0"))
            self.assertEqual(errs, [])
            self.assertEqual((man.id, man.version, man.dll), ("studio.speedy", "1.2.0", "speedy.dll"))
            self.assertEqual(man.hooks, list(hooks))
            self.assertEqual(man.loader_api, 2 if hooks else 1)
            self.assertEqual(man.game_versions, ["1.9.0"] if game else [])

    def test_write_manifest_keeps_the_version(self):
        mod = st.new_mod(self.tmp, "keeper", "show-position")
        text = mod.manifest.read_text(encoding="utf-8").replace('version = "1.0.0"', 'version = "2.5"')
        mod.manifest.write_text(text, encoding="utf-8")
        hooks, unknown = st.write_manifest(mod, mod.source.read_text(encoding="utf-8"), {})
        self.assertEqual((hooks, unknown), ([], []))
        self.assertIn('version = "2.5"', mod.manifest.read_text(encoding="utf-8"))
        self.assertIn('game_versions = ["1.9.0"]', mod.manifest.read_text(encoding="utf-8"))  # (botw_player_pos)

    def test_find_hooks_and_uses_game(self):
        src = """
#define MY_TARGET 0x7000aa0000ull
// api->hook(0x7000bb0000, before, NULL, NULL);   (a comment: not a hook)
static void f(void) {
    api->hook(0x7000cc7410ull, before, NULL, NULL);
    api->hook(MY_TARGET, NULL, after, NULL);
    api->hook(BOTW_ADDR_THING, NULL, NULL, repl);
    api->hook(pick_one(), before, NULL, NULL);
    if (api->can_hook(0x7000dd0000)) {}
    const char *s = "api->hook(0x7000ee0000, a, b, c)";
}
"""
        hooks, unknown = st.find_hooks(src, {"BOTW_ADDR_THING": "0x7000123400"})
        self.assertEqual(hooks, [0x7000123400, 0x7000aa0000, 0x7000cc7410])
        self.assertEqual(len(unknown), 1)
        self.assertIn("line 8", unknown[0])
        self.assertTrue(st.uses_game("x = botw_player_info(api);"))
        self.assertTrue(st.uses_game("uint64_t p = 0x7001e13ed0;"))
        self.assertFalse(st.uses_game('// botw_player_info\napi->log("botw_x");\nint n = 0x10;'))


# ---------------------------------------------------------------- what gets completed

class Completion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = fixture_index()

    def test_token(self):
        t = st.token_before("    x = botw_th")
        self.assertEqual((t.text, t.ident, t.recv), ("botw_th", "botw_th", None))
        t = st.token_before("    api->lo")
        self.assertEqual((t.text, t.ident, t.recv), ("api->lo", "lo", "api"))
        t = st.token_before("    a->")
        self.assertEqual((t.text, t.ident, t.recv), ("a->", "", "a"))
        t = st.token_before("    p->ma")
        self.assertEqual((t.text, t.recv, t.blocked), ("", None, True))   # (a member of something else: no names)
        self.assertEqual(st.token_before("x = 1 + ").text, "")

    def test_no_names_in_numbers_or_other_members(self):
        """Review: Enter at the end of 0x7001e13ed0 gave 0BOTW_ADDR_PLAYER_INFO, and info->stam gave
        info->botw_stamina_get(api)."""
        for line in ("#define MY_ADDR 0x7001e13ed0", "    x = 0x1c", "    info->stam", "    s.stam", "    f = 3.5f",
                     "    y = 1e5"):
            t = st.token_before(line)
            self.assertTrue(t.blocked, line)
            self.assertEqual(t.text, "", line)
            self.assertFalse(st.should_open(t, line), line)
        for line, text in (("    api->lo", "api->lo"), ("    a->lo", "a->lo"), ("    x2 = botw_st", "botw_st"),
                           ("    f(0, bo", "bo")):
            t = st.token_before(line)
            self.assertEqual((t.text, t.blocked), (text, False), line)

    def test_names_that_clash(self):
        """Review: nul gave a wrong message, con/com1 made a mod.toml modman refuses, mods/help/pos clash with
        commands the game or modhost has."""
        for name in ("nul", "con", "aux", "prn", "com1", "lpt9"):
            self.assertIn("Windows keeps the name", st.check_name(name), name)
        for name in ("mods", "modload", "modreload", "help", "pos", "ping", "teleport", "state"):
            self.assertIn("already has a command", st.check_name(name), name)
        self.assertIsNone(st.check_name("console_x"))
        import modman
        self.assertEqual(st.RESERVED_RE.pattern, modman.RESERVED_RE.pattern)
        self.assertEqual(st.VERSION_RE.pattern, modman.VERSION_RE.pattern)

    def test_when_the_list_opens(self):
        ok = [("  botw_", "botw_"), ("  api->", "api->"), ("  BOTW_", "BOTW_"), ("  x = th", "th")]
        for line, _ in ok:
            self.assertTrue(st.should_open(st.token_before(line), line), line)
        for line in ("  // botw_th", '  api->log("botw_th', "  t", "  return", "  int", '#include "bo',
                     "  /* botw_t"):
            self.assertFalse(st.should_open(st.token_before(line), line), line)

    def test_suggestions(self):
        names = [e["name"] for e in st.suggestions(self.index, st.token_before("  api->"), True)]
        self.assertTrue(names and all(n.startswith("api->") for n in names))
        names = [e["name"] for e in st.suggestions(self.index, st.token_before("  botw_v"), True)]
        self.assertEqual(names[0], "botw_value_set")
        # while typing, the code map joins in from 3 letters only
        self.assertFalse([e for e in st.suggestions(self.index, st.token_before("  th"), True)
                          if e["kind"] == "codemap"])
        self.assertTrue([e for e in st.suggestions(self.index, st.token_before("  thing_s"), True)
                         if e["kind"] == "codemap"])
        # a name typed in full that isn't called: nothing left to add
        self.assertEqual(st.suggestions(self.index, st.token_before("  BOTW_ADDR_THING"), True), [])
        sig, text = st.doc_lines(self.index.by_name["botw_thing_move"])
        self.assertIn("botw_thing_move(const SwModApi *api", sig)
        self.assertIn("not tested in game", text)
        sig, text = st.doc_lines(self.index.by_name["thing_site"])
        self.assertIn("address 0x7000200020", text)
        self.assertIn(ms.CODEMAP_DOC, text)


# ---------------------------------------------------------------- building (subprocess mocked)

CLANG_OUT = r"""{src}:11:12: error: expected ';' after expression
   11 |     api = a
      |            ^
{src}:12:5: error: call to undeclared function 'botw_valeu_set'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]
   12 |     botw_valeu_set(api, 3);
{src}:14:9: warning: unused variable 'spare' [-Wunused-variable]
C:\made-up\native\modhost\botw.h:40:1: error: unknown type name 'oops'
3 errors generated.
"""
LINK_OUT = """lld-link: error: undefined symbol: helper_i_forgot
>>> referenced by C:\\made-up\\x.o:(swmod_init)
lld-link: warning: something harmless
"""


class Build(Temp):
    def test_commands(self):
        mod = st.Mod("speedy", self.tmp)
        cmds, out = st.compile_commands(TC, mod)
        obj = mod.build_dir / "obj" / "speedy.o"
        self.assertEqual(cmds[0], [str(TC.zig), "cc", *bn.CFLAGS, f"-I{bn.HOST_SRC}", "-c", str(mod.source), "-o",
                                   str(obj)])
        self.assertEqual(cmds[1][:2], [str(TC.zig), "lld-link"])
        self.assertIn(f"-OUT:{out}", cmds[1])
        self.assertIn(str(obj), cmds[1])
        self.assertNotIn(str(TC.implib), cmds[1])   # (a mod doesn't link against savagewind.exe)
        self.assertEqual(out, self.tmp / ".build" / "speedy" / "speedy.dll")

    def test_low_priority_and_no_window(self):
        with mock.patch.object(st.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            st.run_low(["zig", "cc"], {"A": "1"})
        kw = run.call_args.kwargs
        if sys.platform == "win32":
            self.assertTrue(kw["creationflags"] & st.BELOW_NORMAL)
            self.assertTrue(kw["creationflags"] & st.NO_WINDOW)
        self.assertEqual(kw["env"], {"A": "1"})
        self.assertTrue(kw["capture_output"])

    def test_build_ok(self):
        mod = st.new_mod(self.tmp, "speedy", "stamina")
        seen = []

        def runner(cmd, env):
            seen.append((cmd, env))
            if cmd[1] == "lld-link":
                out = next(a[len("-OUT:"):] for a in cmd if a.startswith("-OUT:"))
                Path(out).write_bytes(b"MZ made-up dll")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        r = st.build_mod(mod, TC, {}, runner=runner, check_export=False)
        self.assertTrue(r.ok, r.problems)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0][1]["ZIG_GLOBAL_CACHE_DIR"], str(self.tmp / ".build" / "zig-cache"))
        self.assertEqual(mod.dll.read_bytes(), b"MZ made-up dll")   # copied next to the source
        self.assertEqual(r.dll, mod.build_dir / "speedy.dll")
        self.assertIn('windows = "speedy.dll"', mod.manifest.read_text(encoding="utf-8"))

    def test_build_errors_in_plain_words(self):
        mod = st.new_mod(self.tmp, "speedy", "empty")
        out = CLANG_OUT.format(src=mod.source)

        def runner(cmd, env):
            return subprocess.CompletedProcess(cmd, 1, "", out)

        r = st.build_mod(mod, TC, {}, runner=runner, check_export=False, index=fixture_index())
        self.assertFalse(r.ok)
        lines = [(p.line, p.level) for p in r.problems]
        self.assertEqual(lines, [(11, "error"), (12, "error"), (14, "warning"), (0, "error")])
        self.assertEqual(r.problems[0].plain, "A ; is missing (at the end of this line or the one before).")
        self.assertIn("There is no function called botw_valeu_set", r.problems[1].plain)
        self.assertIn("Did you mean botw_value_set?", r.problems[1].plain)
        self.assertEqual(r.problems[2].plain, "spare is never used (that's allowed).")
        self.assertTrue(r.problems[3].plain.startswith("botw.h line 40: oops isn't a type C knows"))
        self.assertEqual(len(r.errors), 3)

    def test_errors_in_a_header_point_at_the_mods_line(self):
        """Review: '#define strcmp 1' before the includes gave 'string.h line 37: A name is missing here.' with no
        line marked and F8 saying 'No errors marked'."""
        src = Path(r"C:\made up\badmacro\badmacro.c")
        out = rf"""In file included from {src}:5:
In file included from C:\repo\native\modhost/swmod.h:31:
C:\zig\lib\libc\include\any-windows-any/string.h:37:15: error: expected identifier or '('
   37 |   int __cdecl strcmp(const char *_Str1,const char *_Str2);
      |               ^
{src}:4:16: note: expanded from macro 'strcmp'
    4 | #define strcmp 1
      |                ^
In file included from {src}:6:
C:\repo\native\modhost/botw.h:103:46: error: called object type 'int' is not a function or function pointer
  103 |     return api && api->game_version && strcmp(api->game_version, BOTW_GAME_VERSION) == 0;
C:\repo\native\modhost/botw.h:110:1: warning: something else in the same header
2 errors generated.
"""
        probs = st.parse_problems(out, src)
        self.assertEqual([(p.line, p.level) for p in probs], [(4, "error"), (6, "error"), (6, "warning")])
        self.assertTrue(probs[0].plain.startswith("string.h line 37: A name is missing here."))
        self.assertIn("It comes from your line 4.", probs[0].plain)
        self.assertIn("It comes from your line 6.", probs[1].plain)

    def test_link_errors_point_at_the_line(self):
        mod = st.new_mod(self.tmp, "speedy", "empty")
        src = mod.source.read_text(encoding="utf-8").replace("api = a;", "api = a;\n    helper_i_forgot();")
        mod.source.write_text(src, encoding="utf-8")

        def runner(cmd, env):
            return subprocess.CompletedProcess(cmd, 1 if cmd[1] == "lld-link" else 0, "", LINK_OUT if cmd[1] ==
                                               "lld-link" else "")

        r = st.build_mod(mod, TC, {}, runner=runner, check_export=False)
        self.assertFalse(r.ok)
        err = r.errors[0]
        self.assertEqual(err.line, src.splitlines().index("    helper_i_forgot();") + 1)
        self.assertIn("helper_i_forgot is used but never written", err.plain)
        self.assertEqual([p.level for p in r.problems], ["error", "warning"])

    def test_unclear_hook_is_a_warning(self):
        mod = st.new_mod(self.tmp, "speedy", "empty")
        src = mod.source.read_text(encoding="utf-8").replace("api = a;", "api = a;\n    api->hook(where(), 0, 0, 0);")
        mod.source.write_text(src, encoding="utf-8")
        r = st.build_mod(mod, TC, {}, runner=lambda c, e: subprocess.CompletedProcess(c, 0, "", ""),
                         check_export=False)
        self.assertTrue(r.ok)
        self.assertEqual(r.warnings[0].line, src.splitlines().index("    api->hook(where(), 0, 0, 0);") + 1)
        self.assertIn("can't tell which function", r.warnings[0].plain)


# ---------------------------------------------------------------- into a made-up game

class FakeGame:
    """The made-up game's hot-load commands, answering as native/modhost/modhost.c does: a copy <name>-<n>.dll keeps
    the name <name>; `modreload` of a name that isn't loaded just loads; a mod loaded at game start can't be
    replaced; the fields are in "data"."""

    def __init__(self):
        self.mods: dict[str, dict] = {}   # name -> {"file", "hot"}
        self.next_id = 1
        self.hook_slots = 4
        self.hot = True            # False: an older modhost without modload
        self.strict = False        # True: modreload of a name that isn't loaded fails (no such mod)
        self.refuse_unload = False
        self.no_slot = ""
        self.lines: list[list[str]] = []

    def answer(self, words):
        self.lines.append(words)
        cmd = words[0] if words else ""
        if cmd == "mods":
            data = {"found": len(self.mods), "loaded": len(self.mods), "hook_slots": self.hook_slots}
            for i, (n, m) in enumerate(self.mods.items()):
                data.update({f"mods.{i}.name": n, f"mods.{i}.file": m["file"], f"mods.{i}.hot": m["hot"]})
            return {"ok": True, "data": data, "text": ""}
        if cmd in ("modload", "modreload") and not self.hot:
            return {"ok": False, "error": f"Unknown command: {cmd}"}
        if cmd == "modreload":
            name, path = words[1], words[2]
            old = self.mods.get(name)
            if old and not old["hot"]:
                return {"ok": False, "error": f"{name} was loaded at game start ({old['file']}): restart the game "
                                              "to replace this mod"}
            if not old and self.strict:
                return {"ok": False, "error": f"no mod named {name} is loaded"}
            if old and self.refuse_unload:
                return {"ok": False, "error": f"{name} was not replaced: a game thread still runs one of its hooks"}
            self.mods.pop(name, None)
            return self.load(path, name, replaced=old is not None)
        if cmd == "modload":
            name = st.hot_base(Path(words[1]).name)
            if name in self.mods:
                return {"ok": False, "error": f"a mod named {name} is already loaded: use modreload {name} <path>"}
            return self.load(words[1], name)
        return {"ok": False, "error": f"Unknown command: {cmd}"}

    def load(self, path, name, replaced=False):
        if not os.path.isfile(path):
            return {"ok": False, "error": f"there is no file {path}"}
        self.mods[name] = {"file": Path(path).name, "hot": True}
        self.next_id += 1
        return {"ok": True, "data": {"name": name, "id": self.next_id - 1, "file": Path(path).name, "path": path,
                                     "replaced": replaced, "no_slot": self.no_slot, "notes": ""},
                "text": f"{name} {'reloaded' if replaced else 'loaded'}"}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for raw in self.rfile:
            words = shlex.split(raw.decode("utf-8"), posix=True)
            if words == ["drop"]:
                return
            self.wfile.write((json.dumps(self.server.game.answer(words)) + "\n").encode())


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class Load(Temp):
    def setUp(self):
        super().setUp()
        self.game = FakeGame()
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.game = self.game
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.mod = st.new_mod(self.tmp, "speedy", "show-position")
        self.dll = self.tmp / "built.dll"
        self.dll.write_bytes(b"MZ made-up")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def into(self, known=None, hooks=()):
        return st.into_game(self.mod, self.dll, self.port, known, hooks, mm=mock.Mock(side_effect=AssertionError))

    def test_load_then_reload(self):
        out = self.into()
        self.assertEqual(out.kind, "ok", out.lines)
        self.assertEqual(out.lines[0], "Loaded speedy into the game (mod id 1).")
        self.assertEqual(out.loaded_name, "speedy")
        self.assertEqual(self.game.lines[-1][0], "modload")
        first = Path(self.game.lines[-1][1])
        self.assertEqual(first, self.mod.build_dir / "hot" / "speedy-1.dll")   # a copy with a new name
        out = self.into(out.loaded_name)
        self.assertEqual(out.lines[0], "Reloaded speedy into the game (mod id 2).")
        self.assertEqual(self.game.lines[-1][:2], ["modreload", "speedy"])
        self.assertEqual(Path(self.game.lines[-1][2]).name, "speedy-2.dll")
        self.assertEqual(self.game.mods, {"speedy": {"file": "speedy-2.dll", "hot": True}})

    def test_finds_a_copy_from_an_earlier_session(self):
        self.game.mods["speedy"] = {"file": "speedy-7.dll", "hot": True}   # (Mod Studio was closed and opened)
        out = self.into()
        self.assertEqual(self.game.lines[-1][:2], ["modreload", "speedy"])
        self.assertTrue(out.lines[0].startswith("Reloaded"))
        self.assertEqual(len(self.game.mods), 1)   # (never a second copy)

    def test_loaded_at_game_start(self):
        self.game.mods["speedy"] = {"file": "speedy.dll", "hot": False}   # (from the mod library)
        out = self.into()
        self.assertEqual(out.kind, "error")
        self.assertIn("restart the game to replace this mod", out.lines[0])
        self.assertIn("mod library when the game started", out.lines[1])

    def test_known_name_after_a_game_restart(self):
        out = self.into("speedy")      # (loaded before the game was restarted: modreload just loads it)
        self.assertEqual([w[0] for w in self.game.lines], ["mods", "modreload"])
        self.assertTrue(out.lines[0].startswith("Loaded"), out.lines)
        self.game.mods.clear()
        self.game.lines.clear()
        self.game.strict = True        # (a modhost that refuses an unknown name: then modload)
        out = self.into("speedy")
        self.assertEqual([w[0] for w in self.game.lines], ["mods", "modreload", "modload"])
        self.assertTrue(out.lines[0].startswith("Loaded"))

    def test_refusal_is_shown(self):
        self.into()
        self.game.refuse_unload = True
        out = self.into("speedy")
        self.assertEqual(out.kind, "error")
        self.assertIn("was not replaced", out.lines[0])

    def test_no_slot_for_its_hook(self):
        self.game.no_slot = "0x7000cc7410"
        out = self.into(hooks=[0x7000cc7410])
        self.assertEqual(out.kind, "warn")
        self.assertTrue(any("No hook slot for 0x7000cc7410" in x for x in out.lines))

    def test_names_in_a_mods_reply(self):
        self.assertEqual(st.hot_base("speedy-12.dll"), "speedy")
        self.assertEqual(st.hot_base("speedy.dll"), "speedy")
        self.assertEqual(st.hot_base("-3.dll"), "-3")
        data = {"mods.0.name": "other", "mods.0.hot": True, "mods.1.name": "Speedy", "mods.1.hot": False}
        self.assertEqual(st.loaded_entry(data, "speedy"), {"name": "Speedy", "hot": False})
        self.assertEqual(st.loaded_entry({"mods.0.file": "speedy-4.dll"}, "speedy")["name"], "speedy")  # (no names)
        self.assertIsNone(st.loaded_entry({"mods.0.name": "speedy_two"}, "speedy"))

    def test_old_modhost(self):
        self.game.hot = False
        out = self.into()
        self.assertEqual(out.kind, "error")
        self.assertIn("no modload command", out.lines[0])

    def test_hook_without_slots(self):
        self.game.hook_slots = 0
        out = self.into(hooks=[0x7000cc7410])
        self.assertEqual(out.kind, "warn")
        self.assertTrue(any("no hook slots" in x for x in out.lines))
        self.assertTrue(any("Deploy" in x for x in out.lines))

    def test_hot_copies_are_tidied(self):
        for _ in range(st.HOT_KEEP + 3):
            st.hot_copy(self.dll, self.mod)
        left = sorted(p.name for p in (self.mod.build_dir / "hot").iterdir())
        self.assertEqual(len(left), st.HOT_KEEP + 1)
        self.assertIn(f"speedy-{st.HOT_KEEP + 3}.dll", left)

    def test_game_stops_answering(self):
        class Dropper(st.GameLink):
            def request(self, *words):
                self._send(b"drop\n")   # (the fake game closes the connection, as a game that quits does)
                return super().request(*words)

        out = st.into_game(self.mod, self.dll, self.port, link=Dropper, mm=mock.Mock())
        self.assertEqual(out.kind, "error")
        self.assertIn("stopped answering", out.lines[0])


class _Silent(socketserver.StreamRequestHandler):
    def handle(self):
        self.rfile.readline()
        time.sleep(1.5)   # (accepts, reads, never answers)


class _Garbage(socketserver.StreamRequestHandler):
    def handle(self):
        self.rfile.readline()
        self.wfile.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")


class OtherProgramOnThePort(Temp):
    """Review: a program that listens on the port and stays silent made F6 wait 15 s; one that answers garbage
    failed at once; and in both cases the mod never went into the library."""

    def serve(self, handler):
        server = _Server(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def setUp(self):
        super().setUp()
        self.mod = st.new_mod(self.tmp, "speedy", "show-position")
        self.dll = self.tmp / "built.dll"
        self.dll.write_bytes(b"MZ made-up")

    def test_silent_program(self):
        port = self.serve(_Silent)
        real = st.load_running
        fast = lambda *a, **k: real(*a, **k, first_timeout=0.3)  # noqa: E731
        t = time.monotonic()
        with mock.patch.object(st, "load_running", fast):
            out = st.into_game(self.mod, self.dll, port, mm=mock.Mock(side_effect=AssertionError))
        self.assertLess(time.monotonic() - t, 1.2)
        self.assertEqual(out.kind, "error")
        self.assertIn(f"Nothing answered on port {port}", out.lines[0])
        self.assertTrue(any("another port" in x for x in out.lines))

    def test_garbage_answer_goes_to_the_library(self):
        port = self.serve(_Garbage)
        mm = FakeModman(self.tmp / "lib")
        out = st.into_game(self.mod, self.dll, port, running=lambda: False, mm=mm)
        self.assertEqual(out.kind, "warn", out.lines)
        self.assertIn("went into the mod library", out.lines[1])
        self.assertIn(f"Something else answers on port {port}", out.lines[0])
        self.assertEqual([m["id"] for m in mm.lib.mods], ["studio.speedy"])


class FakeModman:
    """Stands in for tools/modman.py's library functions. Like modman, add_mod refuses a mod.toml that
    modman.parse_manifest refuses (a version like "1.1 beta"), copies the mod into <lib>/mods/<id>, and remove_mod
    deletes that folder. fail_adds: the next add_mod calls whose source folder name is in it fail."""

    class ModmanError(Exception):
        pass

    MARKER = ".modman-deploy.json"

    def __init__(self, root: Path, mods=(), exists=True, install_ok=True):
        import modman
        self.root, self.calls, self.install_ok = root, [], install_ok
        self.parse_manifest = modman.parse_manifest
        self.fail_adds: set[str] = set()
        fake = self

        class Lib:
            def __init__(s, root, install=None, mods=None):
                s.root, s.install, s.mods = str(root), install, list(mods or [])
                s.deploy_dir = os.path.join(str(root), "deploy")

            def save(s):
                fake.calls.append(("save", [m["id"] for m in s.mods]))

            def mod_path(s, mod_id):
                return os.path.join(s.root, "mods", mod_id)

            @classmethod
            def load(cls, root):
                return fake.lib

        self.Library = Lib
        self.lib = Lib(root, "C:/made-up/Savage Wind", [dict(m) for m in mods]) if exists else None
        for m in mods:   # (each listed mod has its folder in the library)
            d = Path(self.lib.mod_path(m["id"])) if self.lib else None
            if d:
                d.mkdir(parents=True, exist_ok=True)
                (d / "mod.toml").write_text(f'[mod]\nid = "{m["id"]}"\nname = "{m["name"]}"\nversion = "1.0"\n\n'
                                            f'[code]\nwindows = "x.dll"\n', encoding="utf-8")
                (d / "x.dll").write_bytes(b"MZ old")

    def resolve_library(self, arg):
        return str(self.root)

    def default_install(self):
        return "C:/made-up/Savage Wind"

    def looks_like_install(self, p):
        return self.install_ok

    def read_json(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return None

    def remove_mod(self, lib, ref):
        self.calls.append(("remove", ref))
        i = next(i for i, m in enumerate(lib.mods) if m["id"] == ref)
        shutil.rmtree(lib.mod_path(ref), ignore_errors=True)
        return lib.mods.pop(i)

    def add_mod(self, lib, src, name=None):
        files = sorted(os.listdir(src))
        self.calls.append(("add", files))
        if Path(src).name in self.fail_adds:
            self.fail_adds.discard(Path(src).name)
            raise self.ModmanError(f"Not added: {Path(src).name} (made-up failure)")
        man, errs = self.parse_manifest(Path(src, "mod.toml").read_text(encoding="utf-8"))
        if errs:
            raise self.ModmanError(f"Not added: {name}'s mod.toml can't be used:" + "".join(f"\n  - {e}" for e in errs))
        shutil.copytree(src, lib.mod_path(man.id))
        entry = {"id": man.id, "name": name or man.name, "enabled": True, "kinds": ["native"]}
        lib.mods.append(entry)
        lib.save()
        return entry, None, []

    def game_running(self):
        return False


class Library(Temp):
    def setUp(self):
        super().setUp()
        self.mod = st.new_mod(self.tmp, "speedy", "show-position")
        self.dll = self.tmp / "built.dll"
        self.dll.write_bytes(b"MZ made-up")

    def test_new_in_library(self):
        mm = FakeModman(self.tmp / "lib", [{"id": "other", "name": "Other", "enabled": True}])
        out = st.into_game(self.mod, self.dll, 1, answers=lambda p: False, running=lambda: False, mm=mm)
        self.assertEqual(out.kind, "ok", out.lines)
        self.assertIn("went into the mod library", out.lines[0])
        self.assertIn("Deploy, then Play", out.lines[1])
        self.assertEqual(mm.calls[0], ("add", ["mod.toml", "speedy.dll"]))   # only the mod's two files
        self.assertEqual([m["id"] for m in mm.lib.mods], ["other", "studio.speedy"])

    def test_replaced_in_place(self):
        mods = [{"id": "a", "name": "A", "enabled": True}, {"id": "studio.speedy", "name": "speedy", "enabled": False},
                {"id": "b", "name": "B", "enabled": True}]
        mm = FakeModman(self.tmp / "lib", mods)
        out = st.into_game(self.mod, self.dll, 1, answers=lambda p: False, running=lambda: False, mm=mm)
        self.assertEqual([c[0] for c in mm.calls[:2]], ["remove", "add"])
        self.assertEqual([m["id"] for m in mm.lib.mods], ["a", "studio.speedy", "b"])   # same place
        self.assertFalse(mm.lib.mods[1]["enabled"])                                     # still off
        self.assertIn("replaced", out.lines[0])
        self.assertTrue(any("turned off" in x for x in out.lines))

    def test_hook_slot_from_last_deploy(self):
        mm = FakeModman(self.tmp / "lib")
        os.makedirs(mm.lib.deploy_dir)
        marker = Path(mm.lib.deploy_dir) / mm.MARKER
        out = st.put_in_library(self.mod, self.dll, hooks=[0x7000cc7410], mm=mm)
        self.assertEqual(out.kind, "warn")
        self.assertIn(st.REBUILD_LINE, out.lines)
        marker.write_text(json.dumps({"rebuild": {"hooks": ["0x7000cc7410"]}}), encoding="utf-8")
        out = st.put_in_library(self.mod, self.dll, hooks=[0x7000cc7410], mm=mm)
        self.assertEqual(out.kind, "ok")
        self.assertTrue(any("already has a hook slot" in x for x in out.lines))

    def test_no_library_and_no_game(self):
        mm = FakeModman(self.tmp / "lib", exists=False, install_ok=False)
        out = st.put_in_library(self.mod, self.dll, mm=mm)
        self.assertEqual(out.kind, "error")
        self.assertIn("no mod library", out.lines[0])

    def test_running_without_port(self):
        mm = FakeModman(self.tmp / "lib")
        out = st.into_game(self.mod, self.dll, 7777, answers=lambda p: False, running=lambda: True, mm=mm)
        self.assertEqual(out.kind, "warn")
        self.assertIn("doesn't answer on port 7777", out.lines[0])
        self.assertIn("--port 7777", out.lines[0])

    def library_ids(self, mm):
        return [(m["id"], m["enabled"]) for m in mm.lib.mods]

    def test_bad_version_leaves_the_library_alone(self):
        """Review: after version = "1.1 beta" the second F6 removed the mod from the library, then failed."""
        mods = [{"id": "a", "name": "A", "enabled": True}, {"id": "studio.speedy", "name": "speedy", "enabled": False}]
        mm = FakeModman(self.tmp / "lib", mods)
        text = self.mod.manifest.read_text(encoding="utf-8").replace('version = "1.0.0"', 'version = "1.1 beta"')
        self.mod.manifest.write_text(text, encoding="utf-8")
        out = st.put_in_library(self.mod, self.dll, mm=mm)
        self.assertEqual(out.kind, "error")
        self.assertIn("library wasn't changed", out.lines[0])
        self.assertTrue(any("version" in x for x in out.lines[1:]), out.lines)
        self.assertEqual(self.library_ids(mm), [("a", True), ("studio.speedy", False)])
        self.assertEqual([c[0] for c in mm.calls], [])   # (nothing removed, nothing added)
        self.assertIn("isn't like 1.0", st.manifest_version_problem(self.mod))
        r = st.build_mod(self.mod, TC, {}, runner=lambda c, e: subprocess.CompletedProcess(c, 0, "", ""),
                         check_export=False)
        self.assertTrue(any("mod manager won't take" in p.plain for p in r.warnings))   # (said at build time too)

    def test_refused_new_copy_puts_the_old_one_back(self):
        """If add_mod fails after the old copy was removed, the old copy goes back, at its place and on or off."""
        mods = [{"id": "a", "name": "A", "enabled": True}, {"id": "studio.speedy", "name": "speedy", "enabled": False},
                {"id": "b", "name": "B", "enabled": True}]
        mm = FakeModman(self.tmp / "lib", mods)
        mm.fail_adds.add("package")
        out = st.put_in_library(self.mod, self.dll, mm=mm)
        self.assertEqual(out.kind, "error")
        self.assertIn("still has the old copy", out.lines[1])
        self.assertEqual(self.library_ids(mm), [("a", True), ("studio.speedy", False), ("b", True)])
        self.assertEqual((Path(mm.lib.mod_path("studio.speedy")) / "x.dll").read_bytes(), b"MZ old")


# ---------------------------------------------------------------- the window (hidden)

def key(ch: str) -> SimpleNamespace:
    names = {"_": "underscore", ">": "greater", "-": "minus", " ": "space", "(": "parenleft", ";": "semicolon"}
    return SimpleNamespace(keysym=names.get(ch, ch), char=ch, state=0)


class Window(Temp):
    @classmethod
    def setUpClass(cls):
        if st.tk is None:
            raise unittest.SkipTest("no tkinter")
        cls.small = fixture_index()

    def setUp(self):
        super().setUp()
        try:
            self.root = st.tk.Tk()
        except st.tk.TclError as e:
            self.skipTest(f"no display: {e}")
        self.root.withdraw()

    def tearDown(self):
        for v in getattr(self, "views", []):
            v.shutdown()
        with_root = getattr(self, "root", None)
        if with_root is not None:
            with_root.destroy()
        super().tearDown()

    def view(self, index=None, template="empty"):
        cfg = st.Config(root=self.tmp, settings=None, codemap=None, install=self.tmp / "no-savage-wind")
        v = st.View(self.root, cfg, index=index or self.small, load_names=False, open_last=False, hidden=True)
        self.views = [*getattr(self, "views", []), v]
        v.open_mod(st.new_mod(self.tmp, "testmod", template))
        v.editor.mark_set("insert", "end-1c")
        v.editor.insert("insert", "\nvoid f(void) {\n    ")
        return v

    def type(self, v, text):
        times = []
        for ch in text:
            t = time.perf_counter()
            v.editor.insert("insert", ch)
            v.after_key(key(ch))
            times.append((time.perf_counter() - t) * 1000)
        return times

    def line(self, v):
        return v.editor.get("insert linestart", "insert lineend")

    def test_title_names_the_product_and_the_mod(self):
        v = self.view()
        self.assertTrue(st.TITLE.startswith("Breathalyzer"))
        self.assertTrue(self.root.title().startswith(f"{st.TITLE} - testmod"), self.root.title())

    def test_box_opens_filters_and_puts_in(self):
        v = self.view()
        self.type(v, "bo")
        self.assertTrue(v.popup.visible)
        self.type(v, "tw_v")
        self.assertEqual(v.popup.items[0]["name"], "botw_value_set")
        self.assertEqual(v.popup.labels[0].cget("bg"), st.POPUP["sel_bg"])  # the picked one is a yellow bar
        self.assertIn("botw_value_set(const SwModApi *api, float value)", v.doc_sig.cget("text"))
        v.on_tab()
        self.assertFalse(v.popup.visible)
        self.assertEqual(self.line(v).strip(), "botw_value_set(api, value)")
        self.assertEqual(v.editor.get("sel.first", "sel.last"), "value")   # typing replaces the argument
        v.editor.mark_set("insert", "insert lineend")
        v.editor.insert("insert", ";\n    ")
        self.type(v, "api->re")
        self.assertEqual(v.pick_row(0), "break")   # a click on a row puts that name in
        self.assertEqual(self.line(v).strip(), "api->reply_str(r, key, value)")

    def test_arrows_enter_and_escape(self):
        v = self.view()
        self.type(v, "botw_th")
        items = [e["name"] for e in v.popup.items]
        self.assertGreaterEqual(len(items), 2)
        self.assertEqual(v.popup_key(1), "break")
        self.assertEqual(v.popup.sel, 1)
        self.assertEqual((v.popup.labels[1].cget("bg"), v.popup.labels[1].cget("fg")),
                         (st.POPUP["sel_bg"], st.POPUP["sel_fg"]))
        self.assertTrue(v.popup.labels[1].cget("text").startswith(st.MARK))
        self.assertEqual((v.popup.labels[0].cget("bg"), v.popup.labels[0].cget("fg")), (st.POPUP["bg"], st.POPUP["fg"]))
        self.assertFalse(v.popup.labels[0].cget("text").startswith(st.MARK))
        self.assertIn(items[1], v.doc_sig.cget("text"))
        v.on_return()
        self.assertTrue(self.line(v).strip().startswith(items[1]))
        v.editor.tag_remove("sel", "1.0", "end")
        v.editor.mark_set("insert", "insert lineend")
        self.type(v, ";")
        v.on_return()   # no box: a new line, indented like this one
        self.assertEqual(self.line(v), "    ")
        self.type(v, "botw_t")
        self.assertEqual(v.on_escape(), "break")
        self.assertFalse(v.popup.visible)
        self.assertIsNone(v.on_escape())

    def test_api_arrow_and_ctrl_space(self):
        v = self.view()
        self.type(v, "api->")
        self.assertTrue(v.popup.visible)
        self.assertTrue(all(e["name"].startswith("api->") for e in v.popup.items))
        self.type(v, "lo")
        self.assertEqual(v.popup.items[0]["name"], "api->log")
        v.on_tab()
        self.assertEqual(self.line(v).strip(), "api->log(fmt, ...)")
        v.editor.insert("insert lineend", ";\n    ")
        v.editor.mark_set("insert", "insert lineend")
        v.refresh_popup(auto=False)   # Ctrl+Space on nothing: the curated names
        self.assertTrue(v.popup.visible)
        self.assertNotIn("codemap", {e["kind"] for e in v.popup.items})

    def test_no_box_in_comments_strings_or_keywords(self):
        v = self.view()
        for text in ("// botw_th", '"botw_th', "return", "x = 1 "):
            v.editor.insert("insert", "\n    ")
            self.type(v, text)
            self.assertFalse(v.popup.visible, text)

    def test_real_key_events(self):
        """The bindings themselves: the box opens after Tk put the letters in."""
        v = self.view()
        tags = v.editor.bindtags()
        self.assertLess(tags.index("Text"), tags.index("StudioAfter"))   # (after Tk put the letter in)
        self.assertTrue(self.root.bind_class("StudioAfter", "<KeyPress>"))
        for seq in ("<Tab>", "<Return>", "<Escape>", "<Up>", "<Down>", "<Control-space>", "<Control-o>", "<F5>",
                    "<Control-F1>"):
            self.assertTrue(v.editor.bind(seq), seq)   # (before Tk's own keys: Ctrl+O would put in a line break)
        self.assertIn("break", v.editor.bind("<Control-o>"))
        for ch in "botw_":
            v.editor.event_generate("<KeyPress>", keysym=key(ch).keysym)
        self.root.update()
        if not self.line(v).endswith("botw_"):
            self.skipTest("this Tk doesn't deliver generated keys to a hidden window")
        self.assertTrue(v.popup.visible)
        v.editor.event_generate("<KeyPress>", keysym="Down")
        v.editor.event_generate("<KeyPress>", keysym="Tab")
        self.root.update()
        self.assertFalse(v.popup.visible)
        self.assertTrue(self.line(v).strip().startswith("botw_"))
        self.assertNotIn("\t", self.line(v))

    def test_colours(self):
        v = self.view(template="show-position")
        v.highlight_visible()
        ed = v.editor
        self.assertIn("comment", ed.tag_names("1.0"))
        line = next(i for i, x in enumerate(ed.get("1.0", "end").splitlines(), 1) if x.startswith("static void"))
        self.assertIn("keyword", ed.tag_names(f"{line}.0"))
        inc = next(i for i, x in enumerate(ed.get("1.0", "end").splitlines(), 1) if x.startswith("#include"))
        self.assertIn("preproc", ed.tag_names(f"{inc}.0"))
        self.assertIn("string", ed.tag_names(f"{inc}.10"))

    def test_build_errors_are_marked(self):
        v = self.view()
        probs = [st.Problem(3, 1, "error", "expected ';'", "A ; is missing."),
                 st.Problem(5, 1, "warning", "unused variable 'x'", "x is never used."),
                 st.Problem(0, 0, "error", "undefined symbol: q", "q is used but never written anywhere.")]
        v.build_done(st.BuildResult(False, None, probs))
        self.assertEqual(v.status_text[0], "Build failed: 2 errors.")
        self.assertTrue(v.status_text[1].startswith("Line 3: A ; is missing."))
        self.assertEqual(v.editor.index("insert").split(".")[0], "3")
        self.assertIn("problem_error", v.editor.tag_names("3.0"))
        self.assertIn("problem_warning", v.editor.tag_names("5.0"))
        v.next_problem()
        self.assertEqual(v.editor.index("insert").split(".")[0], "5")
        v.next_problem()
        self.assertEqual(v.editor.index("insert").split(".")[0], "3")

    def test_build_without_compiler_says_so(self):
        v = self.view()
        v.on_build()
        self.assertEqual(v.status_text[0], "Can't build: no compiler.")
        self.assertIn("No zig", v.status_text[1])

    def test_load_result_is_shown(self):
        v = self.view()
        v.load_done(st.Outcome("ok", ["Loaded testmod into the game (mod id 4).", "Its commands work now."],
                               loaded_name="testmod-1"))
        self.assertEqual(v.loaded_name, "testmod-1")
        self.assertEqual(v.status_text[0], "Loaded testmod into the game (mod id 4).")

    def test_new_mod_dialog(self):
        v = self.view()
        v.on_new()
        d = v.new_dialog
        d["name"].insert(0, "Bad Name")
        d["create"]()
        self.assertIn("small letters", d["message"].cget("text"))
        d["name"].delete(0, "end")
        d["name"].insert(0, "second")
        v.list_step(d["list"], 1)   # (Down in the name box picks the next template)
        d["create"]()
        self.assertEqual(v.mod.name, "second")
        self.assertIn("botw_player_pos", v.editor.get("1.0", "end"))   # show-position

    def test_find_game_value(self):
        v = self.view()
        v.on_find()
        f = v.find_dialog
        f["box"].insert(0, "widget")
        f["run"]()
        names = [e["name"] for e in f["found"]]
        self.assertIn("made::up::Widget::vf_1", names)
        k = names.index("made::up::Widget::vf_1")
        self.assertEqual(f["list"].itemcget(k, "fg"), v.t["warn"])     # code map rows stand out
        f["list"].selection_clear(0, "end")
        f["list"].selection_set(k)
        f["put"]()
        self.assertIn("0x7000200000ull /* made::up::Widget::vf_1 (code map, not tested) */", self.line(v))

    def test_names_load_in_the_background(self):
        cm = write_repo(self.tmp / "fixture")
        cfg = st.Config(root=self.tmp, settings=None, codemap=cm, cache=self.tmp / "cache")
        v = st.View(self.root, cfg, load_names=True, open_last=False, hidden=True)
        self.views = [v]
        self.assertIn(st.NAMES_LOADING, v.footer.cget("text"))   # (the window may pause meanwhile: it says so)
        deadline = time.monotonic() + 20
        while (not v.full_index or v.footer.cget("text") != st.KEYS_HINT) and time.monotonic() < deadline:
            v.tick()
            time.sleep(0.02)
        self.assertTrue(v.full_index)
        self.assertEqual(v.footer.cget("text"), st.KEYS_HINT)
        self.assertEqual(v.index.codemap_count, 3)
        self.assertIn("api->log", v.index.by_name)          # the repo's own swmod.h
        self.assertIn("3 raw game names", v.names_note)
        self.assertTrue((self.tmp / "cache" / ms.CACHE_NAME).is_file())

    def test_doc_for_the_name_under_the_cursor(self):
        v = self.view()
        v.editor.insert("insert", "botw_thing_move(api, 1, 2, 3);")
        v.editor.mark_set("insert", "insert linestart + 8 chars")
        v.doc_at_cursor()
        self.assertIn("botw_thing_move", v.doc_sig.cget("text"))
        self.assertIn("not tested in game", v.doc_text.cget("text"))
        self.assertEqual(v.doc_sig.cget("fg"), v.t["warn"])   # (not tested: shown in the warning colour)

    def test_open_dialog(self):
        v = self.view()   # (it has unsaved text: opening another mod saves it first)
        st.new_mod(self.tmp, "another", "command")
        v.on_open()
        o = v.open_dialog
        self.assertEqual(o["names"], ["another", "testmod"])
        self.assertEqual(o["list"].curselection(), (1,))   # the open mod is picked
        v.list_step(o["list"], 1)
        o["go"]()
        self.assertEqual(v.mod.name, "another")
        self.assertIn("void f(void)", (self.tmp / "testmod" / "testmod.c").read_text(encoding="utf-8"))
        self.assertIn("adds two numbers", v.editor.get("1.0", "end"))

    def test_text_size(self):
        v = self.view()
        v.set_size(24)
        self.assertEqual(v.mono.cget("size"), 24)
        v.set_size(99)
        self.assertEqual(v.size, st.MAX_SIZE)

    # --- regression tests for the review of 2026-11-07

    def test_stale_box_never_changes_another_line(self):
        """Review: the box stayed open after F5 (and F6, F8, Ctrl+S, Ctrl+F1, the dialogs); after a failed build the
        cursor jumped to the error's line and the next Enter wrote the old name into that line."""
        v = self.view()
        first = v.editor.get("1.0", "1.end")
        self.type(v, "botw_th")
        self.assertTrue(v.popup.visible)
        v.build_done(st.BuildResult(False, None, [st.Problem(1, 1, "error", "expected ';'", "A ; is missing.")]))
        self.assertFalse(v.popup.visible)                     # (goto_line closed it)
        self.assertEqual(v.editor.index("insert").split(".")[0], "1")
        v.on_return()
        self.assertEqual(v.editor.get("1.0", "1.end"), first)  # a plain new line: no name in the comment
        # the box open, then the cursor moved without a key: Enter doesn't put the name anywhere
        v.editor.mark_set("insert", "end-1c")
        v.editor.insert("insert", "\n    ")
        self.type(v, "botw_th")
        v.editor.mark_set("insert", "2.0")
        self.assertFalse(v.accept())
        self.assertFalse(v.popup.visible)
        self.assertNotIn("botw_thing(", v.editor.get("1.0", "end"))
        # the buttons and dialogs close it too (the keys F5, F6, F8, Ctrl+S, Ctrl+N, Ctrl+O and Ctrl+F1 call
        # hide_popup before their function, and so does the editor losing the keyboard)
        for leave in (v.on_build, v.on_load, v.on_find, v.on_new, v.on_open):
            v.editor.mark_set("insert", "end-1c")
            v.editor.insert("insert", "\n    ")
            self.type(v, "botw_th")
            self.assertTrue(v.popup.visible)
            leave()
            self.assertFalse(v.popup.visible, leave.__name__)
            for d in (getattr(v, "find_dialog", None), getattr(v, "new_dialog", None), getattr(v, "open_dialog", None)):
                if d:
                    d["window"].destroy()
            v.find_dialog = v.new_dialog = v.open_dialog = None
        self.assertIn("hide_popup", v.editor.bind("<FocusOut>"))

    def test_find_puts_the_name_in_place_of_the_typed_one(self):
        """Review: 'stam' + Find game value gave 'stambotw_stamina_get(api)', and a later Enter added the name again."""
        v = self.view()
        self.type(v, "botw_th")
        self.assertTrue(v.popup.visible)
        v.on_find()
        self.assertFalse(v.popup.visible)
        f = v.find_dialog
        self.assertEqual(f["box"].get(), "botw_th")
        names = [e["name"] for e in f["found"]]
        f["list"].selection_clear(0, "end")
        f["list"].selection_set(names.index("botw_thing"))
        f["put"]()
        self.assertEqual(self.line(v).strip(), "botw_thing(api)")
        v.on_return()   # no box any more: just a new line
        self.assertEqual(self.line(v), "    ")
        self.assertEqual(v.editor.get("1.0", "end").count("botw_thing"), 1)

    def test_failed_save_keeps_the_text(self):
        """Review: when the source couldn't be written, opening another mod or closing the window threw the text
        away."""
        import stat
        v = self.view()
        st.new_mod(self.tmp, "beta", "command")
        src = v.mod.source
        os.chmod(src, stat.S_IREAD)
        self.addCleanup(os.chmod, src, stat.S_IREAD | stat.S_IWRITE)
        self.assertFalse(v.open_mod(st.Mod("beta", self.tmp)))
        self.assertEqual(v.mod.name, "testmod")
        self.assertIn("void f(void)", v.editor.get("1.0", "end"))
        self.assertEqual(v.status_text[0], "Not saved.")
        self.assertTrue(any("wasn't opened" in x for x in v.status_text))
        v.on_close()                      # the first X: the window stays
        self.assertFalse(v.closed)
        self.assertIn("window stays open", v.status_text[0])
        v.on_close()                      # the second X closes it without saving
        self.assertTrue(v.closed)
        self.root = None                  # (destroyed)

    def test_no_new_or_open_while_busy_and_results_stay_with_their_mod(self):
        """Review: opening mod beta during a build of alpha loaded alpha's DLL into the game as beta, and marked
        alpha's errors in beta."""
        v = self.view()
        alpha = v.mod
        st.new_mod(self.tmp, "beta", "command")
        v.busy = "Building"
        v.on_open()
        v.on_new()
        self.assertIsNone(getattr(v, "open_dialog", None))
        self.assertIsNone(getattr(v, "new_dialog", None))
        self.assertFalse(v.open_mod(st.Mod("beta", self.tmp)))
        self.assertEqual(v.mod.name, "testmod")
        v.busy = ""
        self.assertTrue(v.open_mod(st.Mod("beta", self.tmp)))   # (as if the build had been started before)
        dll = self.tmp / "alpha.dll"
        dll.write_bytes(b"MZ alpha")
        seen = []

        def fake_into_game(mod, dll, *a, **k):
            seen.append((mod.name, dll.read_bytes()))
            return st.Outcome("ok", ["Loaded."])

        v.in_thread = lambda work, done: done(work())
        with mock.patch.object(st, "into_game", fake_into_game):
            v.build_done(st.BuildResult(False, None, [st.Problem(3, 1, "error", "x", "A ; is missing.")]), None, alpha)
            self.assertEqual(v.problems, [])                 # beta isn't marked with alpha's errors
            self.assertIn("Another mod is open", v.status_text[1])
            v.build_done(st.BuildResult(True, dll, []), lambda r, m=alpha: v.load(r.dll, r.hooks, m), alpha)
        self.assertEqual(seen, [("testmod", b"MZ alpha")])  # alpha's DLL goes in as alpha
        self.assertEqual(v.mod.name, "beta")

    def test_no_box_in_block_comments_and_numbers(self):
        v = self.view()
        v.editor.insert("insert", "/* a note\n    ")
        self.type(v, "botw_th")
        self.assertFalse(v.popup.visible)                    # (the /* started on the line before)
        v.editor.insert("insert", " */\n    ")
        self.type(v, "botw_th")
        self.assertTrue(v.popup.visible)
        v.hide_popup()
        v.editor.insert("insert", ";\n    x = 0")
        self.type(v, "x7001e13ed0")
        self.assertFalse(v.popup.visible)
        v.refresh_popup(auto=False)                          # Ctrl+Space in a number: no box either
        self.assertFalse(v.popup.visible)
        self.assertEqual(v.status_text[0], "No names fit here.")
        v.on_return()
        self.assertIn("x = 0x7001e13ed0\n", v.editor.get("1.0", "end"))

    def test_picked_row_stands_out(self):
        """Review: the picked row differed from the others only by text colour, 1.24:1."""
        def lum(c):
            rgb = [int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            rgb = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in rgb]
            return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]

        def ratio(a, b):
            la, lb = sorted((lum(a), lum(b)), reverse=True)
            return (la + 0.05) / (lb + 0.05)

        p = st.POPUP
        self.assertGreaterEqual(ratio(p["sel_fg"], p["sel_bg"]), 7)   # the picked row's text
        self.assertGreaterEqual(ratio(p["fg"], p["bg"]), 7)           # the other rows' text
        self.assertGreaterEqual(ratio(p["sel_bg"], p["bg"]), 7)       # the picked row against the others
        v = self.view()
        self.type(v, "botw_")
        labs = v.popup.labels
        self.assertTrue(labs[0].cget("text").startswith(st.MARK))
        self.assertTrue(labs[1].cget("text").startswith(st.NO_MARK))

    def test_box_fits_in_the_editor_and_scrolls(self):
        """Review: at 24 pt the 10-row box was cut off below the editor, and Down moved the pick out of sight."""
        v = self.view()
        v.set_size(24)
        self.type(v, "botw_")
        n = len(v.popup.items)
        self.assertGreaterEqual(n, 6)
        lh = v.mono.metrics("linespace")
        v.editor.winfo_x = v.editor.winfo_y = lambda: 0
        v.edit_box.winfo_width = lambda: 4000
        for room, line_y, rows, above in ((20 * lh, 12 * lh, min(n, 10), True), (8 * lh, 2 * lh, 4, False)):
            v.edit_box.winfo_height = lambda room=room: room
            v.editor.bbox = lambda *a, y=line_y: (10, y, 10, lh)
            v.popup.set(v.popup.items)
            x, y = v.popup_spot(v.tok)
            w, h = v.popup.size()
            self.assertEqual(v.popup.shown(), rows, room)
            self.assertTrue(0 <= y and y + h <= room, (y, h, room))
            self.assertEqual(y < line_y, above)
        v.popup.fit(3)
        for _ in range(4):
            v.popup_key(1)
        self.assertEqual((v.popup.sel, v.popup.top), (4, 2))           # the pick stays in sight
        self.assertEqual(v.popup.labels[2].cget("text"), st.MARK + v.popup.texts[4])
        self.assertEqual(v.popup.labels[3].winfo_manager(), "")         # (only 3 rows shown)
        want = v.popup.items[2]["name"]
        v.pick_row(0)                                                   # the row on screen: item 2
        self.assertTrue(self.line(v).strip().startswith(want))

    def test_keystroke_speed_with_70000_names(self):
        big = ms.build_index(made_up_catalog())
        v = self.view(index=big)
        times, popup = [], []
        for word in ("botw_stamina_set", "api->spawn_call", "camera", "nn_hndl", "vt_70010", "shieldplayer"):
            v.editor.insert("insert", "\n    ")
            times += self.type(v, word)[1:]
            popup.append(v.last_popup_ms)
        p90 = sorted(times)[int(len(times) * 0.9)]
        print(f"\n  keystroke to box, 70000 names: median {statistics.median(times):.2f} ms, 90% under "
              f"{p90:.2f} ms, slowest {max(times):.2f} ms", file=sys.stderr)
        self.assertLess(statistics.median(times), 15.0)
        self.assertLess(p90, 15.0)


if __name__ == "__main__":
    unittest.main()
