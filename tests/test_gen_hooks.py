"""Tests for tools/gen_hooks.py on small made-up files shaped like Savage Wind's generator output (tools/gen.py at
0.4.1-alpha.4: chunk files main_NNN.c, main_table.c). No game files, no compiler."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import gen_hooks as gh  # noqa: E402

A, B, C, D, E = 0x7000001000, 0x7000001010, 0x7000002000, 0x7000002010, 0x7000002020

# Made up, in the generator's shape: an include, prototypes of the functions it calls, then the definitions.
# B calls A directly (bl), C jumps to A (tail call), D has the cold resume copy a jump table gives (f_<addr>_r).
CHUNK0 = """#include "cpu.h"
void f_7000001000(Cpu *c);
void f_7000002000(Cpu *c);
void f_7000001000(Cpu *c) {
    FN_ENTER(0x7000001000ull);
    c->x[0] = c->x[0] + c->x[1];
    return;
}
void f_7000001010(Cpu *c) {
    FN_ENTER(0x7000001010ull);
    c->x[30] = 0x7000001018ull; f_7000001000(c);
    c->x[30] = 0x700000101cull; c->pc = 0x7100000000ull; rt_dispatch(c);
    return;
}
"""
CHUNK1 = """#include "cpu.h"
void f_7000001000(Cpu *c);
static void f_7000002010_r(Cpu *c);
void f_7000002000(Cpu *c) {
    FN_ENTER(0x7000002000ull);
    __attribute__((musttail)) return f_7000001000(c);
}
void f_7000002010(Cpu *c) {
    FN_ENTER(0x7000002010ull);
    switch (c->x[1]) { case 0x7000002014ull: goto L_7000002014; default: break; }
    c->pc = c->x[1]; __attribute__((musttail)) return f_7000002010_r(c);
L_7000002014:;
    return;
}
__attribute__((cold, noinline, optnone)) static void f_7000002010_r(Cpu *c) {
    goto L_dispatch;
L_dispatch:
    switch (c->pc) { case 0x7000002010ull: goto L_7000002010; default: break; }
    __attribute__((musttail)) return rt_dispatch(c);
}
void f_7000002020(Cpu *c) {
    FN_ENTER(0x7000002020ull);
    return;
}
"""
TABLE = """#include "cpu.h"
void f_7000001000(Cpu *c);
void f_7000001010(Cpu *c);
void f_7000002000(Cpu *c);
void f_7000002010(Cpu *c);
void f_7000002020(Cpu *c);
const uint64_t main_fn_addrs[] = {
0x7000001000ull,
0x7000001010ull,
0x7000002000ull,
0x7000002010ull,
0x7000002020ull};
const GuestFn main_fn_ptrs[] = {
f_7000001000,
f_7000001010,
f_7000002000,
f_7000002010,
f_7000002020};
const uint64_t main_fn_count = 5;
"""


def write(path, text, crlf=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write(text.replace("\n", "\r\n") if crlf else text)


def read(path):
    with open(path, encoding="latin-1", newline="") as f:
        return f.read()


def snapshot(d):
    return {n: read(os.path.join(d, n)) for n in sorted(os.listdir(d))}


class Folder(unittest.TestCase):
    crlf = False  # (gen.py writes in text mode: CRLF on Windows)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, "main")
        write(os.path.join(self.d, "main_000.c"), CHUNK0, self.crlf)
        write(os.path.join(self.d, "main_001.c"), CHUNK1, self.crlf)
        write(os.path.join(self.d, "main_table.c"), TABLE, self.crlf)
        self.before = snapshot(self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def chunk(self, i):
        return read(os.path.join(self.d, f"main_{i:03d}.c")).replace("\r\n", "\n")


class Apply(Folder):
    def test_renames_only_the_definitions(self):
        rep = gh.apply(self.d, [A, D])
        self.assertEqual(rep.hooked, {A: "main_000.c", D: "main_001.c"})
        c0, c1 = self.chunk(0), self.chunk(1)
        self.assertIn("void f_7000001000__orig(Cpu *c) {\n    FN_ENTER(0x7000001000ull);", c0)
        self.assertIn("void f_7000001000(Cpu *c); /* gen_hooks: the wrapper is in main_hooks.c */\n"
                      "void f_7000001000__orig(Cpu *c) {", c0)
        self.assertNotIn("void f_7000001000(Cpu *c) {", c0)
        # calls keep the name, so they reach the wrapper (also B's direct call in the same file)
        self.assertIn("c->x[30] = 0x7000001018ull; f_7000001000(c);", c0)
        self.assertIn("__attribute__((musttail)) return f_7000001000(c);", c1)
        # the cold resume copy and the jump into it are left alone
        self.assertIn("static void f_7000002010_r(Cpu *c) {", c1)
        self.assertIn("return f_7000002010_r(c);", c1)
        self.assertIn("void f_7000002010__orig(Cpu *c) {", c1)
        # functions not on the list are untouched, and so is the table (it lists the wrapper's name)
        self.assertIn("void f_7000002000(Cpu *c) {", c1)
        self.assertIn("void f_7000001010(Cpu *c) {", c0)
        self.assertEqual(read(os.path.join(self.d, "main_table.c")), self.before["main_table.c"])

    def test_hooks_file(self):
        rep = gh.apply(self.d, [D, A])
        self.assertEqual(rep.hooks_c, os.path.join(self.d, "main_hooks.c"))
        text = read(rep.hooks_c)
        self.assertIn('#include "cpu.h"', text)
        self.assertIn("    {0x7000001000ull, f_7000001000__orig, 0, 0},\n    {0x7000002010ull, f_7000002010__orig, 0, 0},",
                      text)
        self.assertIn(f"__declspec(dllexport) const SwHookTable *{gh.EXPORT}(void) {{", text)
        self.assertIn(f"{{{gh.HOOK_ABI}, 2, swmod_slots, \"gen_hooks {gh.FORMAT}\"}}", text)
        wrapper = ("void f_7000002010(Cpu *c) {\n"
                   "    SwHookSlot *s = &swmod_slots[1];\n"
                   "    SwHookEntry e = __atomic_load_n(&s->entry, __ATOMIC_ACQUIRE);\n"
                   "    if (e && e(c, s)) return;\n"
                   "    __attribute__((musttail)) return f_7000002010__orig(c);\n"
                   "}\n")
        self.assertIn(wrapper, text)
        self.assertIn("void f_7000001000__orig(Cpu *c);", text)
        self.assertEqual(text.count("    if (e && e(c, s)) return;"), 2)

    def test_same_list_any_order_same_output(self):
        gh.apply(self.d, [A, D, A])
        first = snapshot(self.d)
        gh.apply(self.d, [D, A])
        self.assertEqual(snapshot(self.d), first)
        self.assertEqual(gh.hooks_c([D, A]), gh.hooks_c([A, D, D]))

    def test_idempotent(self):
        gh.apply(self.d, [A, D])
        first = snapshot(self.d)
        stamps = {n: os.stat(os.path.join(self.d, n)).st_mtime_ns for n in first}
        rep = gh.apply(self.d, [A, D])
        self.assertEqual(snapshot(self.d), first)
        self.assertEqual({n: os.stat(os.path.join(self.d, n)).st_mtime_ns for n in first}, stamps)  # not rewritten
        self.assertEqual(rep.hooked, {A: "main_000.c", D: "main_001.c"})

    def test_another_list_unhooks_the_rest(self):
        gh.apply(self.d, [A, D])
        gh.apply(self.d, [E])
        self.assertNotIn("__orig", self.chunk(0))
        self.assertIn("void f_7000002020__orig(Cpu *c) {", self.chunk(1))
        self.assertEqual(self.chunk(1).count("__orig(Cpu *c) {"), 1)
        with tempfile.TemporaryDirectory() as d2:  # the same as applying [E] to fresh files
            for n in ("main_000.c", "main_001.c", "main_table.c"):
                write(os.path.join(d2, n), self.before[n])
            gh.apply(d2, [E])
            self.assertEqual(snapshot(d2), snapshot(self.d))

    def test_remove_restores_the_files(self):
        gh.apply(self.d, [A, C, D, E])
        self.assertEqual(gh.remove(self.d), 4)
        self.assertEqual(snapshot(self.d), self.before)
        self.assertEqual(gh.remove(self.d), 0)

    def test_refuses_addresses_that_are_not_function_starts(self):
        with self.assertRaises(gh.HookError) as cm:
            gh.apply(self.d, [A, A + 4, 0x7000003000, 0x6000000000])
        msg = str(cm.exception)
        self.assertIn("0x7000001004 is not the start of a function (it is 0x4 bytes into f_7000001000)", msg)
        self.assertIn("0x7000003000 is not the start of a function (it is 0xfe0 bytes into f_7000002020)", msg)
        self.assertIn("0x6000000000 is not the start of a function", msg)
        self.assertNotIn("0x7000001000 is", msg)
        self.assertEqual(snapshot(self.d), self.before)  # nothing changed
        self.assertEqual(gh.check(self.d, [A, B]), [])

    def test_in_table_but_not_defined(self):
        write(os.path.join(self.d, "main_001.c"), CHUNK1.replace("void f_7000002020(Cpu *c) {", "void g(Cpu *c) {"))
        with self.assertRaisesRegex(gh.HookError, "f_7000002020 is in the function table, but no chunk defines it"):
            gh.apply(self.d, [E])

    def test_no_table(self):
        os.remove(os.path.join(self.d, "main_table.c"))
        with self.assertRaisesRegex(gh.HookError, "no main_table.c"):
            gh.apply(self.d, [A])
        write(os.path.join(self.d, "main_table.c"), "// not a table\n")
        with self.assertRaisesRegex(gh.HookError, "has no main_fn_addrs"):
            gh.apply(self.d, [A])

    def test_streaming_pass_matches_apply(self):
        p = gh.HookPass([D, A])
        self.assertEqual(p.chunk(os.path.join(self.d, "main_000.c")), {A})
        self.assertEqual(p.chunk(os.path.join(self.d, "main_001.c")), {D})
        rep = p.finish(self.d)
        streamed = snapshot(self.d)
        with tempfile.TemporaryDirectory() as d2:
            for n in ("main_000.c", "main_001.c", "main_table.c"):
                write(os.path.join(d2, n), self.before[n])
            gh.apply(d2, [A, D])
            self.assertEqual(snapshot(d2), streamed)
        self.assertEqual(rep.hooked, {A: "main_000.c", D: "main_001.c"})

    def test_streaming_pass_checks_the_table_at_the_end(self):
        p = gh.HookPass([A, A + 8])
        for n in ("main_000.c", "main_001.c"):
            p.chunk(os.path.join(self.d, n))
        with self.assertRaisesRegex(gh.HookError, "0x7000001008 is not the start of a function"):
            p.finish(self.d)
        self.assertFalse(os.path.exists(os.path.join(self.d, "main_hooks.c")))
        with self.assertRaisesRegex(gh.HookError, "no addresses"):
            gh.HookPass([])

    def test_table_addrs(self):
        self.assertEqual(gh.table_addrs(TABLE), [A, B, C, D, E])
        self.assertEqual(gh.table_addrs(TABLE.replace("main_", "sdk_"), "sdk"), [A, B, C, D, E])

    def test_chunk_files_in_order(self):
        for n in ("main_010.c", "main_002.c", "main_hooks.c", "sdk_000.c", "main_000.c.o", "main_1000.c"):
            write(os.path.join(self.d, n), "// x\n")
        names = [os.path.basename(p) for p in gh.chunk_files(self.d)]
        self.assertEqual(names, ["main_000.c", "main_001.c", "main_002.c", "main_010.c", "main_1000.c"])

    def test_longer_names_are_not_matched(self):
        text = "void f_70000010000(Cpu *c) {\n}\nvoid f_7000001000_x(Cpu *c) {\n}\n"
        new, found = gh.rewrite_chunk(text, [A])
        self.assertEqual((new, found), (text, set()))


class ApplyCrlf(Apply):
    crlf = True

    def test_line_ends_kept(self):
        gh.apply(self.d, [A])
        raw = read(os.path.join(self.d, "main_000.c"))
        self.assertNotIn("\n", raw.replace("\r\n", ""))  # every line still ends in CRLF
        self.assertIn("void f_7000001000(Cpu *c); /* gen_hooks: the wrapper is in main_hooks.c */\r\n"
                      "void f_7000001000__orig(Cpu *c) {\r\n", raw)


class HooksFiles(unittest.TestCase):
    def test_text(self):
        text = "# my hooks\n0x7000cc7410   # stamina setter\n\n7000001000\nmain+0x2000, extra words\nmain+0X10\n"
        self.assertEqual(gh.parse_hooks_text(text),
                         [0x7000000010, 0x7000001000, 0x7000002000, 0x7000cc7410])

    def test_json(self):
        self.assertEqual(gh.parse_hooks_text('["0x7000001000", 481036341264, "main+0x10"]'),
                         [0x7000000010, 0x7000001000, 0x7000001010])
        data = {"hooks": [{"addr": "0x7000cc7410", "name": "stamina"}, "0x7000cc7410"], "mod": "x"}
        self.assertEqual(gh.parse_hooks_text(json.dumps(data)), [0x7000cc7410])

    def test_errors_name_the_place(self):
        for text, msg in (("0x7000001000\nhello\n", "line 2: 'hello' is not an address"),
                          ('{"hooks": [{"name": "x"}]}', 'entry 1 has no "addr"'),
                          ('{"hooks": [true]}', "entry 1: True is not an address"),
                          ('{"hooks": "0x1"}', "expected a list"),
                          ("[0x10]", "not valid JSON"),
                          ("0x0\n", "line 1: '0x0' is not an address")):
            with self.assertRaises(gh.HookError, msg=text) as cm:
                gh.parse_hooks_text(text, "mine.txt")
            self.assertIn(msg, str(cm.exception))
            self.assertTrue(str(cm.exception).startswith("mine.txt: "), str(cm.exception))

    def test_files_are_merged(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.txt"), os.path.join(d, "b.json")
            with open(a, "w", encoding="utf-8-sig") as f:  # (with a BOM, as Notepad may save it)
                f.write("0x7000001000\n0x7000002000\n")
            write(b, '{"hooks": ["0x7000002000", "0x7000003000"]}')
            self.assertEqual(gh.read_hooks_files([b, a]), [0x7000001000, 0x7000002000, 0x7000003000])
            self.assertEqual(gh.read_hooks_files(a), [0x7000001000, 0x7000002000])
            with self.assertRaisesRegex(gh.HookError, "can't read the hooks file"):
                gh.read_hooks_files([os.path.join(d, "missing.txt")])

    def test_too_many(self):
        text = "\n".join(f"{0x7000000000 + 4 * i:#x}" for i in range(gh.MAX_HOOKS + 1))
        with self.assertRaisesRegex(gh.HookError, "at most"):
            gh.parse_hooks_text(text)

    def test_check_in_code(self):
        bad = gh.check_in_code([0x7000000100, 0x7000000102, 0x6fffffff00, 0x7000010000], 0x7000000000,
                               0x7000010000)
        self.assertEqual(bad, ["0x7000000102 is not a multiple of 4, so it can't be a function start",
                               "0x6fffffff00 is not in main's code (0x7000000000-0x7000010000)",
                               "0x7000010000 is not in main's code (0x7000000000-0x7000010000)"])


class Cli(Folder):
    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = gh.main(list(argv))
        return rc, out.getvalue()

    def test_show_check_apply_remove(self):
        hooks = os.path.join(self.tmp.name, "hooks.txt")
        write(hooks, "0x7000002010\n0x7000001000 # a\n")
        self.assertEqual(self.run_main("show", hooks), (0, "0x7000001000\n0x7000002010\n"))
        rc, text = self.run_main("check", self.d, hooks)
        self.assertEqual(rc, 0)
        self.assertIn("2 of 2 address(es) can be hooked", text)
        rc, text = self.run_main("apply", self.d, hooks)
        self.assertEqual(rc, 0)
        self.assertIn("Hooked 2 function(s)", text)
        self.assertIn("f_7000002010  (main_001.c)", text)
        rc, text = self.run_main("remove", self.d)
        self.assertEqual((rc, text.split(" from ")[0]), (0, "Removed 2 hook(s)"))
        self.assertEqual(snapshot(self.d), self.before)

    def test_refusal(self):
        hooks = os.path.join(self.tmp.name, "hooks.txt")
        write(hooks, "0x7000001004\n")
        rc, text = self.run_main("check", self.d, hooks)
        self.assertEqual(rc, 1)
        self.assertIn("no: 0x7000001004 is not the start of a function", text)
        rc, text = self.run_main("apply", self.d, hooks)
        self.assertEqual(rc, 2)
        self.assertTrue(text.startswith("ERROR can't hook 0x7000001004"), text)
        self.assertEqual(snapshot(self.d), self.before)

    def test_path_outside_the_code_page_when_redirected(self):
        """Redirected stdout on Windows is the ANSI code page with strict errors: an error naming a path with a letter
        it lacks ended in a UnicodeEncodeError that hid the message (made-up path)."""
        missing = os.path.join(self.tmp.name, "Łukasz", "hooks.txt")
        raw = io.BytesIO()
        out = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        with mock.patch.object(sys, "stdout", out):
            rc = gh.main(["show", missing])
            out.flush()
        self.assertEqual(rc, 2)
        text = raw.getvalue().decode("cp1252")
        self.assertTrue(text.startswith("ERROR can't read the hooks file "), text)
        self.assertIn(os.sep + "?ukasz" + os.sep, text)


L, M = 0x7000003000, 0x7000003010
# Made up, in the generator's shape: L loops back to its own start (a `b`/`cbnz` to its entry becomes a goto, gen.py
# jump()) and has the all-instruction fallback after L_dispatch; M jumps to an inner label and has a cold copy whose
# switch can land on M's start.
SELF_CHUNK = """#include "cpu.h"
static void f_7000003010_r(Cpu *c);
void f_7000003000(Cpu *c) {
    FN_ENTER(0x7000003000ull);
L_7000003000:;
    c->x[8] = (uint64_t)(0ull | c->x[0]);
    { uint64_t ea = c->x[8] + 0x8ull; uint64_t v = LD64(ea); c->x[0] = (uint64_t)(v); }
    if (c->x[0] != 0) { goto L_7000003000; }
    c->pc = c->x[9]; goto L_dispatch;
    goto L_7000003000;
L_dispatch:
    switch (c->pc) { case 0x7000003000ull: goto L_7000003000; default: break; }
    __attribute__((musttail)) return rt_dispatch(c);
}
void f_7000003010(Cpu *c) {
    FN_ENTER(0x7000003010ull);
    if (c->x[0] != 0) { goto L_7000003014; }
    c->pc = c->x[1]; __attribute__((musttail)) return f_7000003010_r(c);
L_7000003014:;
    return;
}
__attribute__((cold, noinline, optnone)) static void f_7000003010_r(Cpu *c) {
    goto L_dispatch;
L_dispatch:
    switch (c->pc) { case 0x7000003010ull: goto L_7000003010; default: break; }
    __attribute__((musttail)) return rt_dispatch(c);
}
"""
SELF_TABLE = """#include "cpu.h"
const uint64_t main_fn_addrs[] = {
0x7000003000ull,
0x7000003010ull};
"""


class SelfJumps(unittest.TestCase):
    """A function's jumps back to its own start stay inside it (they don't pass the wrapper): gen_hooks leaves them
    alone on purpose and names the hooked functions that have them."""
    crlf = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, "main")
        write(os.path.join(self.d, "main_000.c"), SELF_CHUNK, self.crlf)
        write(os.path.join(self.d, "main_table.c"), SELF_TABLE, self.crlf)

    def tearDown(self):
        self.tmp.cleanup()

    def test_count(self):
        text = SELF_CHUNK.replace("\n", "\r\n") if self.crlf else SELF_CHUNK
        self.assertEqual(gh.self_jumps(text, L), 2)  # (not the L_dispatch fallback's case)
        self.assertEqual(gh.self_jumps(text, M), 0)  # (an inner label; the cold copy is another function)
        self.assertEqual(gh.self_jumps(text, 0x7000003020), 0)  # (not defined here)
        hooked = gh.rewrite_chunk(text, [L])[0]
        self.assertEqual(gh.self_jumps(hooked, L), 2)  # (the renamed f_<addr>__orig too)

    def test_left_alone_and_reported(self):
        rep = gh.apply(self.d, [L, M])
        c0 = read(os.path.join(self.d, "main_000.c")).replace("\r\n", "\n")
        self.assertEqual(c0.count("goto L_7000003000;"), 3)  # (still gotos: not sent through the wrapper)
        self.assertNotIn("return f_7000003000(c);", c0)
        self.assertEqual(rep.self_jumps, {L: 2})
        p = gh.HookPass([L])
        p.chunk(os.path.join(self.d, "main_000.c"))
        self.assertEqual(p.finish(self.d).self_jumps, {L: 2})
        note = gh.self_jump_note(rep.self_jumps)
        self.assertEqual(note, "Note: f_7000003000 jumps back to its own start (a loop, or a call to itself). Hooks "
                               "run for the outer call only, not for those jumps (docs/hooks.md, Limits).")
        self.assertIn("f_7000003000, f_7000003010 jump back to their own start", gh.self_jump_note({M: 1, L: 2}))
        self.assertEqual(gh.self_jump_note({}), "")

    def test_cli_says_so(self):
        hooks = os.path.join(self.tmp.name, "hooks.txt")
        write(hooks, "0x7000003000\n")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(gh.main(["apply", self.d, hooks]), 0)
        self.assertIn("Note: f_7000003000 jumps back to its own start", out.getvalue())
        write(hooks, "0x7000003010\n")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(gh.main(["apply", self.d, hooks]), 0)
        self.assertNotIn("Note:", out.getvalue())


class SelfJumpsCrlf(SelfJumps):
    crlf = True


class Docs(unittest.TestCase):
    """Every gen_hooks.py command docs/hooks.md and this tool's usage lines show is one the tool accepts."""

    def test_commands_parse(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "docs", "hooks.md"), encoding="utf-8") as f:
            texts = [f.read(), gh.__doc__ or ""]
        seen = set()
        for text in texts:
            for m in re.finditer(r"gen_hooks\.py ([^`\n#]+)", text):
                words = re.sub(r"<[^>]+>(?:\.\.\.)?", "X", m.group(1)).split()
                for sub in words[0].split("|"):
                    argv = [sub] + words[1:]
                    with contextlib.redirect_stderr(io.StringIO()):
                        try:
                            gh.parser().parse_args(argv)
                        except SystemExit:
                            self.fail(f"`gen_hooks.py {m.group(1).strip()}` doesn't work: {argv} is refused")
                    seen.add(sub)
        self.assertEqual(seen, {"show", "check", "apply", "remove"})


class Abi(unittest.TestCase):
    """The slot table modhost reads must match what gen_hooks writes."""

    def test_modhost_agrees(self):
        src = os.path.join(os.path.dirname(__file__), "..", "native", "modhost", "modhost.c")
        with open(src, encoding="utf-8") as f:
            text = f.read()
        for want in (f"#define SWHOOK_ABI {gh.HOOK_ABI}\n", f'#define SWHOOK_EXPORT "{gh.EXPORT}"\n'):
            self.assertTrue(want in text, f"modhost.c should have: {want}")
        generated = gh.hooks_c([A])
        for pattern in (r"struct SwHookSlot \{(.*?)\};", r"typedef struct \{([^{}]*?)\} SwHookTable;"):
            fields = [self.fields(re.search(pattern, t, re.S).group(1)) for t in (text, generated)]
            self.assertEqual(fields[0], fields[1], pattern)
            self.assertGreaterEqual(len(fields[0]), 4)

    @staticmethod
    def fields(body):
        """The member declarations of a struct body, comments and spacing dropped (struct Cpu * = Cpu *)."""
        body = re.sub(r"//[^\n]*", "", body).replace("struct Cpu", "Cpu")
        return [" ".join(d.split()) for d in body.split(";") if d.strip()]


if __name__ == "__main__":
    unittest.main()
