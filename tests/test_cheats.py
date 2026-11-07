"""Tests for tools/cheats.py. Only made-up cheats and a fake game server; no game files."""
import contextlib
import io
import json
import math
import socketserver
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import cheats as ch  # noqa: E402
import control as control_mod  # noqa: E402
from control import Control, ControlError  # noqa: E402

MAIN, HEAP, ALIAS = ch.MAIN_BASE, ch.HEAP_BASE, ch.ALIAS_BASE


def words(text: str) -> list[int]:
    return [int(t, 16) for t in text.split()]


def cheat(code: str, name="test", master=False) -> ch.Cheat:
    c = ch.Cheat(name, master=master, words=words(code))
    c.ops = ch.decode(c.words)
    return c


def run(code: str, mem=None, ticks=1, vm=None, **kw):
    """Run one cheat on FakeMemory; returns (vm, mem, messages)."""
    mem = mem or ch.FakeMemory()
    said = []
    vm = vm or ch.CheatVM(mem, say=said.append, **kw)
    c = cheat(code)
    for _ in range(ticks):
        vm.tick([c])
    return vm, mem, said


def f32(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


def f64(x):
    return struct.unpack("<Q", struct.pack("<d", x))[0]


# ---------------------------------------------------------------------------------------------------------------

class Parsing(unittest.TestCase):
    def test_sections_master_and_words(self):
        text = ("{Master}\r\n580F0000 01234567\r\n\r\n[Infinite thing]\r\n04000000 00001000 0000002A\r\n"
                "[Two words per line] 04000000 00001000\r\n0000002B\r\n")
        cheats, warnings = ch.parse_text(text)
        self.assertEqual([c.name for c in cheats], ["Master", "Infinite thing", "Two words per line"])
        self.assertTrue(cheats[0].master)
        self.assertEqual(cheats[1].words, [0x04000000, 0x1000, 0x2A])
        self.assertEqual(cheats[2].words, [0x04000000, 0x1000, 0x2B])
        self.assertEqual(warnings, [])
        self.assertEqual(cheats[1].line, 4)

    def test_nested_brackets_and_bom(self):
        cheats, _ = ch.parse_text("﻿[Hearts [v2]]\n04000000 00000000 00000001\n")
        self.assertEqual(cheats[0].name, "Hearts [v2]")
        self.assertEqual(len(cheats[0].ops), 1)

    def test_name_ends_at_its_own_bracket(self):
        cheats, warnings = ch.parse_text("[Infinite Hearts] // needs [Master]\n04000000 00000000 00000001\n"
                                         "{Master [x]} # note {y}\n40010000 00000000 00000100\n"
                                         "[Hearts [v2]] // see [Notes]\n")
        self.assertEqual([c.name for c in cheats], ["Infinite Hearts", "Master [x]", "Hearts [v2]"])
        self.assertEqual(warnings, [])
        cheats, warnings = ch.parse_text("[A] [B]\n[C] needs [M]\n[Odd [v2]\n")
        self.assertEqual([c.name for c in cheats], ["A", "B", "C", "Odd [v2"])   # unbalanced: last bracket
        self.assertEqual(len(warnings), 1)
        self.assertIn("needs [M]", warnings[0])

    def test_comments_silent_and_junk_warned(self):
        cheats, warnings = ch.parse_text("# about\n// more\n[A]\n04000000 00000000 00000001 // trailing\n"
                                         "made by someone\n")
        self.assertEqual(cheats[0].words, [0x04000000, 0, 1])
        self.assertEqual(len(warnings), 1)
        self.assertIn("line 5", warnings[0])

    def test_code_before_any_name(self):
        cheats, warnings = ch.parse_text("04000000 00000000 00000001\n[A]\n")
        self.assertEqual(cheats[0].words, [])
        self.assertIn("before any", warnings[0])

    def test_bad_word_refuses_the_cheat(self):
        cheats, _ = ch.parse_text("[A]\n04000000 0000000 00000001\n[B]\n04000000 00000000 00000001\n")
        self.assertEqual(cheats[0].status, "refused")
        self.assertIn("8-digit", cheats[0].error)
        self.assertEqual(cheats[1].status, "ok")

    def test_second_master_refused(self):
        cheats, _ = ch.parse_text("{M1}\n20000000\n{M2}\n20000000\n")
        self.assertIsNone(cheats[0].error)
        self.assertIn("second master", cheats[1].error)

    def test_empty_sections(self):
        cheats, _ = ch.parse_text("[Header 1.9.0]\n[A]\n04000000 00000000 00000001\n[--section--]\n")
        self.assertEqual([c.status for c in cheats], ["empty", "ok", "empty"])

    def test_missing_close_bracket(self):
        _, warnings = ch.parse_text("[Oops\n")
        self.assertIn("without", warnings[0])

    def test_toggles(self):
        t = ch.parse_toggles("[A]\ntrue\n[B]\nfalse\n[C]\non\n")
        self.assertEqual(t, {"A": True, "B": False, "C": True})


class FileChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_build_id_190_no_warning(self):
        p = self.put(f"atmosphere/contents/{ch.TITLE_ID}/cheats/{ch.BUILD_190}.txt", "[A]\n20000000\n")
        cf = ch.load(p)
        self.assertEqual(cf.warnings, [])
        self.assertEqual(cf.build_id, ch.BUILD_190)

    def test_build_id_other_warns(self):
        p = self.put("cheats/DE0B6AC4EFC1DDA7.txt", "[A]\n20000000\n")
        cf = ch.load(p)
        self.assertTrue(any("not 1.9.0" in w for w in cf.warnings))
        p = self.put("cheats/0123456789abcdef.txt", "[A]\n20000000\n")
        self.assertTrue(any("another game version" in w for w in ch.load(p).warnings))

    def test_plain_name_no_warning(self):
        p = self.put("my cheats.txt", "[A]\n20000000\n")
        self.assertEqual(ch.load(p).warnings, [])

    def test_wrong_title_folder(self):
        p = self.put(f"contents/0100000000010000/cheats/{ch.BUILD_190}.txt", "[A]\n20000000\n")
        self.assertTrue(any("title" in w for w in ch.load(p).warnings))

    def test_toggles_file_enables(self):
        p = self.put("c/x.txt", "{M}\n20000000\n[A]\n20000000\n[B]\n20000000\n")
        self.put("c/toggles.txt", "[A]\ntrue\n[B]\nfalse\n")
        cf = ch.load(p)
        self.assertEqual([c.enabled for c in cf.cheats], [True, True, False])
        self.assertEqual([c.name for c in ch.select(cf, None, False)], ["M", "A"])

    def test_select(self):
        p = self.put("x.txt", "{M}\n20000000\n[Moon jump]\n20000000\n[Moon walk]\n20000000\n[Speed]\n20000000\n"
                              "[Broken]\nB0000000\n")
        cf = ch.load(p)
        self.assertEqual([c.name for c in ch.select(cf, ["speed"], False)], ["M", "Speed"])
        self.assertEqual([c.name for c in ch.select(cf, ["2"], False)], ["M", "Moon walk"])
        self.assertEqual([c.name for c in ch.select(cf, None, True)], ["M", "Moon jump", "Moon walk", "Speed"])
        with self.assertRaisesRegex(ValueError, "matches 2"):
            ch.select(cf, ["moon"], False)
        with self.assertRaisesRegex(ValueError, "no cheat named"):
            ch.select(cf, ["fly"], False)
        self.assertEqual([c.name for c in ch.select(cf, None, False)], ["M"])
        skipped = []
        self.assertEqual([c.name for c in ch.select(cf, ["broken", "speed"], False, skipped)], ["M", "Speed"])
        self.assertEqual([c.name for c in skipped], ["Broken"])

    def test_toggles_match_name_with_trailing_comment(self):
        p = self.put("c/x.txt", "[Infinite Hearts] // needs [Master]\n04000000 00000000 00000001\n")
        self.put("c/toggles.txt", "[Infinite Hearts]\ntrue\n")
        self.assertEqual([c.name for c in ch.select(ch.load(p), None, False)], ["Infinite Hearts"])

    def test_select_keeps_file_order(self):
        p = self.put("order.txt", "{M}\n40020000 00000000 00000000\n"
                                  "[A sets r1]\n40010000 00000000 00000100\n"
                                  "[B uses r1]\n04010000 00000200 00000001\n")
        cf = ch.load(p)
        for names in (["2", "1"], ["B uses", "A sets"], ["1", "2"]):
            with self.subTest(names=names):
                chosen = ch.select(cf, names, False)
                self.assertEqual([c.name for c in chosen], ["M", "A sets r1", "B uses r1"])
                mem = ch.FakeMemory()
                ch.CheatVM(mem, say=lambda s: None).tick(chosen)
                self.assertEqual((mem.get(MAIN + 0x200), mem.get(MAIN + 0x300)), (0, 1))

    def test_select_refuses_broken_master(self):
        p = self.put("bad.txt", "{Master}\n40010000 00000000 00000100\nB0000000\n"
                                "[Set flag]\n04010000 00000200 00000001\n")
        cf = ch.load(p)
        for names, everything in ((None, True), (["set flag"], False), (None, False)):
            with self.assertRaisesRegex(ValueError, r"master code 'Master' can't run: unknown opcode B.*--force"):
                ch.select(cf, names, everything)
        skipped = []
        self.assertEqual([c.name for c in ch.select(cf, None, True, skipped, force=True)], ["Set flag"])
        self.assertEqual([c.name for c in skipped], ["Master"])

    def test_second_master_reported(self):
        p = self.put("two.txt", "{M1}\n40010000 00000000 00000100\n{M2}\n40020000 00000000 00000200\n"
                                "[A]\n04000000 00000000 00000001\n")
        cf = ch.load(p)
        self.assertEqual([m.name for m in cf.masters], ["M1", "M2"])
        with self.assertRaisesRegex(ValueError, "'M2' can't run: a second master code"):
            ch.select(cf, None, True)
        skipped = []
        self.assertEqual([c.name for c in ch.select(cf, None, True, skipped, force=True)], ["M1", "A"])
        self.assertEqual([c.name for c in skipped], ["M2"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ch.main(["list", str(p)])
        text = out.getvalue()
        self.assertIn("plus 2 master codes (only one is allowed)", text)
        self.assertRegex(text, r"M2\s+no\s+refused")
        self.assertIn("can't run: a second master code; only one is allowed", text)

    def test_empty_second_master_only_skipped(self):
        p = self.put("two.txt", "{M1}\n40010000 00000000 00000100\n{M2}\n[A]\n04000000 00000000 00000001\n")
        skipped = []
        self.assertEqual([c.name for c in ch.select(ch.load(p), None, True, skipped)], ["M1", "A"])
        self.assertEqual([c.name for c in skipped], ["M2"])

    def test_empty_master_is_no_master(self):
        p = self.put("empty.txt", "{Master Code}\n\n[Infinite hearts]\n04000000 00000000 00000001\n")
        skipped = []
        self.assertEqual([c.name for c in ch.select(ch.load(p), None, True, skipped)], ["Infinite hearts"])
        self.assertEqual(skipped, [])

    def test_main_text_range_and_code_patch_detection(self):
        head = bytearray(0x100)
        head[:4] = b"NSO0"
        struct.pack_into("<III", head, 0x10, 0x100, 0, 0x1000)   # .text at main+0 .. main+0x1000
        nso_path = self.root / "exefs" / "main"
        nso_path.parent.mkdir()
        nso_path.write_bytes(bytes(head))
        rng = ch.main_text_range(self.root / "exefs")
        self.assertEqual(rng, (MAIN, MAIN + 0x1000))
        p = self.put("x.txt", "[Code]\n04000000 00000100 D503201F\n[Data]\n04000000 00002000 00000001\n"
                              "[Mixed]\n04000000 00000100 D503201F\n04000000 00002000 00000001\n")
        cf = ch.load(p, main_text=rng)
        self.assertEqual([c.status for c in cf.cheats], ["refused", "ok", "partial"])
        self.assertIn("recompiled", cf.cheats[0].error)
        # the VM skips code writes at run time too
        mem = ch.FakeMemory()
        said = []
        ch.CheatVM(mem, say=said.append, main_text=rng).tick([cf.cheats[2]])
        self.assertEqual(mem.get(MAIN + 0x100), 0)
        self.assertEqual(mem.get(MAIN + 0x2000), 1)
        self.assertTrue(any("game code" in s for s in said))

    def test_list_output(self):
        p = self.put(f"{ch.BUILD_190}.txt", "{Master}\n20000000\n[Good]\n04000000 00000000 00000001\n"
                                            "[Keys]\n80000100\n04000000 00000000 00000001\n20000000\n"
                                            "[Bad]\nB0000000\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ch.main(["list", str(p)]), 0)
        text = out.getvalue()
        self.assertTrue(text.startswith(f"{ch.BUILD_190}.txt for 1.9.0: 3 cheat(s), 2 can run (1 partly), 1 can't"))
        self.assertIn("needs keys ZL", text)
        self.assertIn("unknown opcode B", text)
        self.assertIn("always", text)


class Decoding(unittest.TestCase):
    def test_lengths(self):
        cases = {
            "04000000 00000000 00000001": 1, "08000000 00000000 00000000 00000001": 1,
            "14050000 00000000 00000001": 1, "20000000": 1, "21000000": 1, "30000000 00000003": 1, "31000000": 1,
            "40000000 00000000 00000001": 1, "54000000 00000000": 1, "64000000 00000000 00000001": 1,
            "74000000 00000001": 1, "94000000": 1, "94000100 00000001": 1, "98000100 00000000 00000001": 1,
            "A4000000": 1, "A4000200 00000010": 1, "C0450400 00000001": 1, "C0850400 00000000 00000001": 1,
            "C0450500": 1, "C0450000 00000010": 1, "C1000010": 1, "C210FFFF": 1, "C3000801": 1,
            "C4000000 00000000 00000001": 1, "FF000000": 1, "FF100000": 1, "FFF40400": 1, "FFF40000 00000010": 1,
            "80000001": 1,
        }
        for code, count in cases.items():
            with self.subTest(code=code):
                self.assertEqual(len(ch.decode(words(code))), count)

    def test_opcode_numbers(self):
        self.assertEqual(ch.opcode_of(0xC0000000), 0xC0)
        self.assertEqual(ch.opcode_of(0xFFF00000), 0xFFF)
        self.assertEqual(ch.opcode_of(0x9F000000), 0x9)

    def test_refusals(self):
        bad = {
            "B0000000": "unknown opcode B", "C5000000": "unknown opcode C5", "D0000000": "unknown opcode D",
            "FF200000": "unknown opcode FF2", "F0000000": "unknown opcode F00",
            "04000000 00000000": "cut short", "08000000 00000000 00000001": "cut short",
            "03000000 00000000 00000001": "width 3", "04500000 00000000 00000001": "memory type 5",
            "14070000 00000000 00000001": "comparison 7", "22000000": "block end type",
            "32000000": "loop type", "54004000 00000000": "load type", "92A00000": "float math",
            "94E00000": "math type 14", "94000200": "operand type", "A4000600": "offset type",
            "C0450600": "operand type", "C1000040": "save/restore type", "FFF40500": "operand type",
            "74005000 00000000": "legacy math", "14052000 00000000 00000001": "operand type 2",
        }
        for code, msg in bad.items():
            with self.subTest(code=code):
                with self.assertRaises(ch.DecodeError) as cm:
                    ch.decode(words(code))
                self.assertIn(msg, str(cm.exception))

    def test_refused_cheat_in_file(self):
        cheats, _ = ch.parse_text("[X]\n04000000 00000000 00000001\nB0000000\n")
        self.assertEqual(cheats[0].status, "refused")
        self.assertIn("word 4", cheats[0].error)


class StaticStores(unittest.TestCase):
    def test_widths_and_regions(self):
        _, mem, _ = run("01000000 00000010 000001FF\n"            # u8 main+0x10 (cut to 0xFF)
                        "02100000 00000020 0001BEEF\n"            # u16 heap+0x20
                        "04200000 00000030 DEADBEEF\n"            # u32 alias+0x30
                        "08300000 00000040 01234567 89ABCDEF\n"   # u64 aslr (= main)+0x40
                        "04400070 00010000 00000007")             # u32 absolute 0x7000010000
        self.assertEqual(mem.get(MAIN + 0x10, 2), 0xFF)
        self.assertEqual(mem.get(HEAP + 0x20, 4), 0xBEEF)
        self.assertEqual(mem.get(ALIAS + 0x30, 4), 0xDEADBEEF)
        self.assertEqual(mem.get(MAIN + 0x40, 8), 0x0123456789ABCDEF)
        self.assertEqual(mem.get(0x7000010000, 4), 7)

    def test_40_bit_offset_and_offset_register(self):
        _, mem, _ = run("40030000 00000000 00000100\n"     # r3 = 0x100
                        "04030001 00000000 00000005")      # main + 0x100000000 + r3
        self.assertEqual(mem.get(MAIN + 0x100000100), 5)


class Conditionals(unittest.TestCase):
    def setUp(self):
        self.mem = ch.FakeMemory()
        self.mem.put(MAIN + 0x100, 4, 10)

    def cond(self, c, v):
        code = f"140{c}0000 00000100 {v:08X}\n04000000 00000200 00000001\n20000000"
        _, mem, _ = run(code, self.mem)
        hit = mem.get(MAIN + 0x200)
        mem.put(MAIN + 0x200, 4, 0)
        return bool(hit)

    def test_comparisons(self):
        table = [(1, 9, True), (1, 10, False), (2, 10, True), (2, 11, False), (3, 11, True), (3, 10, False),
                 (4, 10, True), (4, 9, False), (5, 10, True), (5, 11, False), (6, 11, True), (6, 10, False)]
        for c, v, want in table:
            with self.subTest(c=c, v=v):
                self.assertEqual(self.cond(c, v), want)

    def test_compare_masks_to_width(self):
        self.mem.put(MAIN + 0x300, 4, 0xFFFFFF05)
        _, mem, _ = run("11050000 00000300 00000105\n04000000 00000200 00000001\n20000000", self.mem)
        self.assertEqual(mem.get(MAIN + 0x200), 1)

    def test_64_bit_compare(self):
        self.mem.put(MAIN + 0x300, 8, 0x1122334455667788)
        _, mem, _ = run("18050000 00000300 11223344 55667788\n04000000 00000200 00000001\n20000000", self.mem)
        self.assertEqual(mem.get(MAIN + 0x200), 1)

    def test_offset_register_form(self):
        self.mem.put(MAIN + 0x500, 4, 3)
        _, mem, _ = run("40020000 00000000 00000400\n14051200 00000100 00000003\n04000000 00000200 00000001\n"
                        "20000000", self.mem)
        self.assertEqual(mem.get(MAIN + 0x200), 1)

    def test_else(self):
        code = ("14050000 00000100 {v}\n04000000 00000200 00000001\n21000000\n04000000 00000204 00000001\n"
                "20000000\n04000000 00000208 00000001")
        _, mem, _ = run(code.format(v="0000000A"), self.mem)
        self.assertEqual((mem.get(MAIN + 0x200), mem.get(MAIN + 0x204), mem.get(MAIN + 0x208)), (1, 0, 1))
        _, mem, _ = run(code.format(v="0000000B"), ch.FakeMemory())
        self.assertEqual((mem.get(MAIN + 0x200), mem.get(MAIN + 0x204), mem.get(MAIN + 0x208)), (0, 1, 1))

    def test_nested(self):
        # if (false) { if (true) { w1 } w2 } else { if (true) { w3 } else { w4 } } w5
        code = ("14050000 00000100 00000063\n"
                "14050000 00000100 0000000A\n04000000 00000201 00000001\n20000000\n04000000 00000202 00000001\n"
                "21000000\n"
                "14050000 00000100 0000000A\n04000000 00000203 00000001\n21000000\n04000000 00000204 00000001\n"
                "20000000\n"
                "20000000\n04000000 00000205 00000001")
        _, mem, _ = run(code, self.mem)
        self.assertEqual([mem.get(MAIN + 0x200 + k, 1) for k in range(1, 6)], [0, 0, 1, 0, 1])

    def test_missing_end_skips_to_end_of_cheat(self):
        _, mem, _ = run("14050000 00000100 00000063\n04000000 00000200 00000001", self.mem)
        self.assertEqual(mem.get(MAIN + 0x200), 0)

    def test_stray_end_is_harmless(self):
        _, mem, _ = run("20000000\n04000000 00000200 00000001", self.mem)
        self.assertEqual(mem.get(MAIN + 0x200), 1)


class Loops(unittest.TestCase):
    def test_loop_writes_array(self):
        # r1 = heap+0x1000; loop r0 x4 { [r1] = 7, r1 += 4 }
        _, mem, _ = run("40010000 00000072 40001000\n30000000 00000004\n64011000 00000000 00000007\n31000000")
        self.assertEqual([mem.get(HEAP + 0x1000 + 4 * k) for k in range(5)], [7, 7, 7, 7, 0])

    def test_nested_loops(self):
        # r2 counts total iterations: outer 3 x inner 5
        vm, _, _ = run("30000000 00000003\n30010000 00000005\n94022100 00000001\n31010000\n31000000")
        self.assertEqual(vm.reg[2], 15)

    def test_zero_count_is_endless_and_stopped(self):
        vm, _, said = run("30000000 00000000\n31000000", max_steps=1000)
        self.assertTrue(any("endless" in s for s in said))
        self.assertEqual(len(vm.stopped), 1)

    def test_end_without_start(self):
        vm, _, said = run("31000000")
        self.assertTrue(any("without a loop start" in s for s in said))

    def test_stopped_cheat_does_not_block_others(self):
        mem = ch.FakeMemory()
        vm = ch.CheatVM(mem, say=lambda s: None, max_steps=100)
        bad, good = cheat("30000000 00000000\n31000000", "bad"), cheat("04000000 00000000 00000009", "good")
        vm.tick([bad, good])
        vm.tick([bad, good])
        self.assertEqual(mem.get(MAIN), 9)
        self.assertIn(id(bad), vm.stopped)


class Registers(unittest.TestCase):
    def test_load_static(self):
        vm, _, _ = run("400F0000 01234567 89ABCDEF")
        self.assertEqual(vm.reg[15], 0x0123456789ABCDEF)

    def test_load_from_memory_all_forms(self):
        mem = ch.FakeMemory()
        mem.put(MAIN + 0x10, 8, HEAP + 0x100)        # pointer
        mem.put(HEAP + 0x108, 4, 0x11223344)
        mem.put(HEAP + 0x200, 2, 0xBEEF)
        mem.put(HEAP + 0x330, 1, 0x7F)
        vm, _, _ = run("58000000 00000010\n"          # r0 = [main+0x10]            (kind 0)
                       "54001000 00000008\n"          # r0 = [r0+8]                 (kind 1)
                       "40020000 00000072 40000000\n"  # r2 = heap
                       "52032200 00000200\n"          # r3 = u16 [r2+0x200]         (kind 2)
                       "40040000 00000000 00000030\n"  # r4 = 0x30
                       "51153400 00000300",           # r5 = u8 [heap + r4 + 0x300] (kind 3)
                       mem)
        self.assertEqual(vm.reg[0], 0x11223344)
        self.assertEqual(vm.reg[3], 0xBEEF)
        self.assertEqual(vm.reg[5], 0x7F)

    def test_store_static_to_register_address(self):
        _, mem, _ = run("40000000 00000072 40000000\n40010000 00000000 00000010\n"
                        "68001110 11223344 55667788\n"   # [r0 + r1] = u64, r0 += 8
                        "62000000 00000000 0001ABCD")    # [r0] = u16 (cut)
        self.assertEqual(mem.get(HEAP + 0x10, 8), 0x1122334455667788)
        self.assertEqual(mem.get(HEAP + 8, 4), 0xABCD)

    def test_legacy_arithmetic(self):
        cases = [(0, 5, 3, 8), (1, 5, 3, 2), (2, 5, 3, 15), (3, 1, 4, 16), (4, 0x80, 3, 0x10)]
        for c, start, v, want in cases:
            with self.subTest(c=c):
                vm, _, _ = run(f"40000000 00000000 {start:08X}\n7400{c}000 {v:08X}")
                self.assertEqual(vm.reg[0], want)
        vm, _, _ = run("40000000 00000000 000000FF\n71000000 00000001")   # width 1 wraps
        self.assertEqual(vm.reg[0], 0)

    def test_arithmetic_integer(self):
        a, b = 0xF0F0, 0x0FF3
        table = [(0, a + b), (1, a - b), (2, a * b), (3, (a << (b & 63)) & ch.M64), (4, a >> (b & 63)),
                 (5, a & b), (6, a | b), (7, ~a & ch.M64), (8, a ^ b), (9, a)]
        for c, want in table:
            with self.subTest(c=c):
                vm, _, _ = run(f"40010000 00000000 {a:08X}\n40020000 00000000 {b:08X}\n98{c:X}01020")
                self.assertEqual(vm.reg[0], want & ch.M64)
                vm, _, _ = run(f"40010000 00000000 {a:08X}\n98{c:X}01100 00000000 {b:08X}")
                self.assertEqual(vm.reg[0], want & ch.M64)

    def test_arithmetic_width_and_wrap(self):
        vm, _, _ = run("40010000 00000000 0000FFFF\n92001100 00000001")   # u16 add wraps
        self.assertEqual(vm.reg[0], 0)
        vm, _, _ = run("40010000 00000000 00000001\n98101100 00000000 00000002")  # 1-2 wraps to 2^64-1
        self.assertEqual(vm.reg[0], ch.M64)
        vm, _, _ = run("40010000 00000000 00000001\n98301100 00000000 00000041")  # shift 65 = shift 1
        self.assertEqual(vm.reg[0], 2)

    def test_float32(self):
        table = [(0xA, 1.5, 2.25, 3.75), (0xB, 1.5, 2.25, -0.75), (0xC, 1.5, 2.0, 3.0), (0xD, 3.0, 2.0, 1.5)]
        for c, x, y, want in table:
            with self.subTest(c=c):
                vm, _, _ = run(f"40010000 00000000 {f32(x):08X}\n94{c:X}01100 {f32(y):08X}")
                self.assertEqual(vm.reg[0], f32(want))

    def test_float_edge_cases(self):
        vm, _, _ = run(f"40010000 00000000 {f32(1.0):08X}\n94D01100 00000000")
        self.assertEqual(vm.reg[0], 0x7F800000)                        # 1/0 = inf
        vm, _, _ = run(f"40010000 00000000 {f32(3e38):08X}\n94C01100 {f32(10.0):08X}")
        self.assertEqual(vm.reg[0], 0x7F800000)                        # overflow = inf
        vm, _, _ = run("40010000 00000000 00000000\n94D01100 00000000")
        self.assertTrue(math.isnan(ch._f32(vm.reg[0])))                 # 0/0 = nan

    def test_float64(self):
        a, b = f64(10.5), f64(0.25)
        vm, _, _ = run(f"40010000 {a >> 32:08X} {a & 0xFFFFFFFF:08X}\n98C01100 {b >> 32:08X} {b & 0xFFFFFFFF:08X}")
        self.assertEqual(vm.reg[0], f64(2.625))

    def test_store_register_all_offset_types(self):
        setup = "40010000 00000000 0000002A\n40020000 00000072 40000100\n40030000 00000000 00000010\n"
        cases = [
            ("A4120000", HEAP + 0x100),                       # 0: [r2]
            ("A4120130", HEAP + 0x110),                       # 1: [r2 + r3]
            ("A4120200 00000020", HEAP + 0x120),              # 2: [r2 + 0x20]
            ("A4130310", HEAP + 0x10),                        # 3: heap + r3
            ("A4100400 00000040", MAIN + 0x40),               # 4: main + 0x40
            ("A4130510 00000040", HEAP + 0x50),               # 5: heap + r3 + 0x40
        ]
        for code, addr in cases:
            with self.subTest(code=code):
                _, mem, _ = run(setup + code)
                self.assertEqual(mem.get(addr), 0x2A)

    def test_store_register_increment(self):
        vm, mem, _ = run("40010000 00000000 00000005\n40020000 00000072 40000000\nA2121000\nA2121000")
        self.assertEqual((mem.get(HEAP, 2), mem.get(HEAP + 2, 2)), (5, 5))
        self.assertEqual(vm.reg[2], HEAP + 4)

    def test_register_conditional_all_operands(self):
        mem = ch.FakeMemory()
        mem.put(MAIN + 0x40, 4, 7)
        mem.put(HEAP + 0x10, 4, 7)
        setup = ("40000000 00000000 00000007\n40010000 00000000 00000040\n40020000 00000072 40000000\n"
                 "40030000 00000000 00000010\n40040000 00000000 00000007\n")
        operands = ["C0450000 00000040", "C0450101", "C0450220 00000010", "C0450323", "C0450400 00000007",
                    "C0450540"]
        store = "\n040F0000 00000300 00000001\n20000000"   # offset register r15 (opcode 0 always adds one)
        for operand in operands:
            with self.subTest(operand=operand):
                _, out, _ = run(setup + operand + store, mem)
                self.assertEqual(out.get(MAIN + 0x300), 1)
                mem.put(MAIN + 0x300, 4, 0)
                no = operand.replace("C045", "C046")    # != is false
                _, out, _ = run(setup + no + store, mem)
                self.assertEqual(out.get(MAIN + 0x300), 0)

    def test_store_static_adds_offset_register(self):
        _, mem, _ = run("40000000 00000000 00000007\n04000000 00000300 00000001")
        self.assertEqual((mem.get(MAIN + 0x300), mem.get(MAIN + 0x307)), (0, 1))

    def test_register_conditional_64_bit_value(self):
        _, mem, _ = run("40000000 00000001 00000002\nC0850400 00000001 00000002\n040F0000 00000300 00000001\n"
                        "20000000")
        self.assertEqual(mem.get(MAIN + 0x300), 1)

    def test_save_restore(self):
        vm, _, _ = run("40010000 00000000 00000011\n"
                       "C1000110\n"     # save: saved[0] = r1
                       "C1010030\n"     # clear r1
                       "C1020000\n"     # restore: r2 = saved[0]
                       "C1040020\n"     # clear saved[4] (no-op)
                       "C1050020")      # clear saved[5]
        self.assertEqual((vm.reg[1], vm.reg[2], vm.saved[0]), (0, 0x11, 0x11))
        vm, _, _ = run("40000000 00000000 00000001\n40010000 00000000 00000002\n"
                       "C2100003\nC2300003\nC2000001\nC2200001\nC2000003")
        self.assertEqual((vm.reg[0], vm.reg[1]), (0, 2))

    def test_static_registers_read_and_write(self):
        mem = ch.FakeMemory()
        vm = ch.CheatVM(mem, say=lambda s: None)
        vm.static[0x05] = 99
        writer = cheat("C3000052\n94002100 00000001\nC3000800", "w")    # r2 = static[5]; r0 = r2+1; static[0x80] = r0
        vm.tick([writer])
        self.assertEqual(vm.static[0x80], 100)
        self.assertEqual(vm.reg[2], 99)

    def test_static_registers_persist_between_ticks(self):
        mem = ch.FakeMemory()
        vm = ch.CheatVM(mem, say=lambda s: None)
        vm.static[0x05] = 99                                            # set by the host before tick 1
        writer = cheat("40000000 00000000 0000002A\nC3000800", "w")     # static[0x80] = 42
        reader = cheat("C3000052\nA4200400 00000040", "r")              # [main+0x40] = static[5]
        vm.tick([writer])
        vm.tick([reader])                                               # tick 2 doesn't touch static[0x80]
        self.assertEqual(vm.static[0x80], 42)
        self.assertEqual(mem.get(MAIN + 0x40), 99)
        self.assertEqual(vm.static[0x05], 99)

    def test_registers_shared_within_tick_and_reset_between(self):
        mem = ch.FakeMemory()
        vm = ch.CheatVM(mem, say=lambda s: None)
        master = cheat("40050000 00000000 00000077", "m", master=True)
        user = cheat("A4500400 00000010\n94055100 00000001", "u")   # [main+0x10] = r5, r5 += 1
        vm.tick([user, master])     # master runs first even if listed later
        self.assertEqual(mem.get(MAIN + 0x10), 0x77)
        vm.tick([user])             # registers start at 0 again
        self.assertEqual(mem.get(MAIN + 0x10), 0)

    def test_master_stopped_stops_the_others(self):
        mem = ch.FakeMemory()
        said = []
        vm = ch.CheatVM(mem, say=said.append, max_steps=50)
        master = cheat("40010000 00000000 00000100\n30000000 00000000\n31000000", "m", master=True)  # r1, then hang
        user = cheat("04010000 00000200 00000001", "u")                 # [main + 0x200 + r1] = 1
        vm.tick([master, user])
        vm.tick([master, user])
        self.assertEqual((mem.get(MAIN + 0x200), mem.get(MAIN + 0x300)), (0, 0))
        self.assertIn(id(master), vm.stopped)
        self.assertIn(id(user), vm.stopped)
        self.assertEqual(sum("stopped too" in s for s in said), 1)

    def test_refused_master_blocks_the_others(self):
        mem = ch.FakeMemory()
        vm = ch.CheatVM(mem, say=lambda s: None)
        master = ch.Cheat("m", master=True, error="unknown opcode B at word 4")
        user = cheat("04010000 00000200 00000001", "u")
        vm.tick([master, user])
        self.assertEqual(mem.get(MAIN + 0x200), 0)
        self.assertIn(id(user), vm.stopped)

    def test_empty_master_does_not_block(self):
        mem = ch.FakeMemory()
        vm = ch.CheatVM(mem, say=lambda s: None)
        vm.tick([ch.Cheat("m", master=True), cheat("04000000 00000200 00000001", "u")])
        self.assertEqual(mem.get(MAIN + 0x200), 1)
        self.assertEqual(vm.stopped, {})


class KeysPauseAndLog(unittest.TestCase):
    def test_keypress_not_pressed_without_hotkeys(self):
        _, mem, _ = run("80000101\n04000000 00000000 00000001\n21000000\n04000000 00000004 00000001\n20000000")
        self.assertEqual((mem.get(MAIN), mem.get(MAIN + 4)), (0, 1))

    def test_keypress_with_hotkeys(self):
        down = set()
        keys = ch.Hotkeys(ch.parse_hotkeys("ZL=SHIFT, A=J"), reader=lambda vk: vk in down)
        mem = ch.FakeMemory()
        vm = ch.CheatVM(mem, say=lambda s: None, hotkeys=keys)
        c = cheat("80000101\n94000100 00000001\nA4000400 00000000\n20000000")   # r0 = 1 if ZL+A
        down.update({0x10})
        vm.tick([c])
        self.assertEqual(mem.get(MAIN), 0)
        down.update({ord("J")})
        vm.tick([c])
        self.assertEqual(mem.get(MAIN), 1)

    def test_extended_keypress_once_per_press(self):
        down = set()
        keys = ch.Hotkeys({0x1: 0x70}, reader=lambda vk: vk in down)
        mem = ch.FakeMemory()
        vm = ch.CheatVM(mem, say=lambda s: None, hotkeys=keys)
        once = cheat("C4000000 00000000 00000001\n58000000 00000000\n94000100 00000001\nA4000400 00000000\n"
                     "20000000")   # counter at main+0 += 1 per press
        hold = cheat("C4100000 00000000 00000001\n58000000 00000008\n94000100 00000001\nA4000400 00000008\n"
                     "20000000")
        for pressed in (True, True, True, False, True):
            down.clear()
            if pressed:
                down.add(0x70)
            vm.tick([once, hold])
        self.assertEqual(mem.get(MAIN), 2)       # two separate presses
        self.assertEqual(mem.get(MAIN + 8), 4)   # every tick it was held

    def test_parse_hotkeys_errors(self):
        for bad in ("ZL", "Q=SHIFT", "A=NOPE"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ch.parse_hotkeys(bad)
        self.assertEqual(ch.parse_hotkeys("+=F1;dup=0x70"), {0x400: 0x70, 0x2000: 0x70})

    def test_partial_status(self):
        cheats, _ = ch.parse_text("[K]\n80000001\n20000000\n")
        ch.analyse(cheats[0])
        self.assertEqual(cheats[0].status, "partial")
        ch.analyse(cheats[0], hotkeys=ch.Hotkeys({0x1: 0x41}, reader=lambda vk: False))
        self.assertEqual(cheats[0].status, "ok")

    def test_pause_resume_are_no_ops(self):
        _, mem, _ = run("FF000000\n04000000 00000000 00000001\nFF100000")
        self.assertEqual(mem.get(MAIN), 1)
        cheats, _ = ch.parse_text("[P]\nFF000000\nFF100000\n")
        ch.analyse(cheats[0])
        self.assertEqual(cheats[0].status, "ok")
        self.assertTrue(any("pause" in n for n in cheats[0].notes))

    def test_debug_log_prints_on_change(self):
        mem = ch.FakeMemory()
        mem.put(MAIN + 0x20, 4, 5)
        said = []
        vm = ch.CheatVM(mem, say=said.append)
        c = cheat("FFF43000 00000020\nFFF41100\nFFF42210 00000020\nFFF44310\nFFF45400\n40040000 00000000 00000009",
                  "log")
        vm.tick([c])
        vm.tick([c])
        logs = [s for s in said if "log" in s]
        # 5 logs on the first tick (r1/r3 are 0 so some read main+0 = 0), none repeated on the second
        self.assertEqual(len(logs), 5)
        self.assertIn("log 3 = 0x5 (5)", logs[0])


class MemoryErrors(unittest.TestCase):
    def test_unmapped_read_gives_zero_and_warns_once(self):
        mem = ch.FakeMemory(regions=[(MAIN, MAIN + 0x1000)])
        said = []
        vm = ch.CheatVM(mem, say=said.append)
        c = cheat("40000000 00000000 00000000\n54001000 00000010\nA4000400 00000100")
        for _ in range(3):
            vm.tick([c])
        self.assertEqual(sum("can't read" in s for s in said), 1)
        self.assertEqual(mem.get(MAIN + 0x100), 0)
        self.assertEqual(vm.stopped, {})

    def test_unmapped_write_dropped(self):
        mem = ch.FakeMemory(regions=[(MAIN, MAIN + 0x1000)])
        said = []
        vm = ch.CheatVM(mem, say=said.append)
        vm.tick([cheat("04100000 00000000 00000001\n04000000 00000000 00000002")])
        self.assertEqual(mem.get(MAIN), 2)
        self.assertTrue(any("can't write" in s for s in said))


# ---------------------------------------------------------------------------------------------------------------
# A fake game: speaks the control protocol (one line in, one JSON line out) for ping and mem read/write.

class FakeGame:
    TYPES = {"u8": 1, "u16": 2, "u32": 4, "u64": 8}

    def __init__(self, regions=((MAIN, MAIN + 0x10000), (HEAP, HEAP + 0x10000)), close_after=None):
        self.mem = bytearray()
        self.data: dict[int, int] = {}
        self.regions = list(regions)
        self.lines: list[str] = []
        self.close_after = close_after
        game = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                for raw in self.rfile:
                    line = raw.decode().strip()
                    game.lines.append(line)
                    if game.close_after is not None and len(game.lines) >= game.close_after:
                        return
                    self.wfile.write((json.dumps(game.answer(line.split())) + "\n").encode())

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02},
                                       daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def ok(self, a, n):
        return any(lo <= a and a + n <= hi for lo, hi in self.regions)

    def get(self, a, n):
        return int.from_bytes(bytes(self.data.get(a + k, 0) for k in range(n)), "little")

    def put(self, a, n, v):
        for k, b in enumerate(v.to_bytes(n, "little")):
            self.data[a + k] = b

    def answer(self, argv):
        if argv == ["ping"]:
            return {"ok": True, "data": {"pong": True}}
        if len(argv) >= 4 and argv[:2] == ["mem", "read"]:
            a, n = int(argv[2], 0), int(argv[3])
            kind = argv[4] if len(argv) > 4 else "hex"
            if not self.ok(a, n):
                return {"ok": False, "error": "Address range not accessible"}
            if kind == "hex":
                return {"ok": True, "data": {"addr": hex(a), "bytes": n,
                                             "hex": bytes(self.data.get(a + k, 0) for k in range(n)).hex()}}
            w = self.TYPES[kind]
            return {"ok": True, "data": {"addr": hex(a), "bytes": n,
                                         **{f"value.{i}": str(self.get(a + i * w, w)) for i in range(n // w)}}}
        if len(argv) == 5 and argv[:2] == ["mem", "write"]:
            a, w, v = int(argv[2], 0), self.TYPES[argv[3]], int(argv[4], 0)
            if v >= 1 << (8 * w):
                return {"ok": False, "error": "mem: use ..."}
            if not self.ok(a, w):
                return {"ok": False, "error": "Address range not accessible"}
            self.put(a, w, v)
            return {"ok": True, "data": {"addr": hex(a), "bytes": w, "value": str(v)}}
        return {"ok": False, "error": f"unknown command {argv[:1]}"}


class ControlMemoryTests(unittest.TestCase):
    def setUp(self):
        self.game = FakeGame()
        self.control = Control(self.game.port, timeout=5)
        self.mem = ch.ControlMemory(self.control)

    def tearDown(self):
        self.control.close()
        self.game.stop()

    def test_reads_all_widths_little_endian(self):
        self.game.put(MAIN + 0x10, 8, 0x0102030405060708)
        self.assertEqual(self.mem.read(MAIN + 0x10, 1), 0x08)
        self.assertEqual(self.mem.read(MAIN + 0x10, 2), 0x0708)
        self.assertEqual(self.mem.read(MAIN + 0x10, 4), 0x05060708)
        self.assertEqual(self.mem.read(MAIN + 0x10, 8), 0x0102030405060708)
        self.assertEqual(self.mem.round_trips, 1)     # one 256-byte block served all four

    def test_read_across_blocks(self):
        self.game.put(MAIN + 0xFC, 8, 0x1122334455667788)
        self.assertEqual(self.mem.read(MAIN + 0xFC, 8), 0x1122334455667788)
        self.assertEqual(self.mem.round_trips, 2)

    def test_writes_go_in_one_batch_and_reads_see_them(self):
        self.game.put(MAIN + 0x20, 4, 0xAAAAAAAA)
        self.mem.write(MAIN, 4, 0x11)
        self.mem.write(MAIN + 0x8, 8, 0x2233445566778899)
        self.mem.write(MAIN + 0x21, 1, 0x55)
        self.mem.write(MAIN, 4, 0x12)                          # same place again: sent once, last value
        self.assertEqual(self.mem.read(MAIN, 4), 0x12)          # pending write, no round trip
        self.assertEqual(self.mem.round_trips, 0)
        self.assertEqual(self.mem.read(MAIN + 0x20, 4), 0xAAAA55AA)   # game bytes + pending byte
        self.assertEqual(self.game.get(MAIN, 4), 0)             # nothing sent before flush
        before = len(self.game.lines)
        self.assertEqual(self.mem.flush(), [])
        self.assertEqual(self.mem.batches, 1)
        writes = [ln for ln in self.game.lines[before:] if ln.startswith("mem write")]
        self.assertEqual(len(writes), 3)
        self.assertEqual(self.game.get(MAIN, 4), 0x12)
        self.assertEqual(self.game.get(MAIN + 8, 8), 0x2233445566778899)
        self.assertEqual(self.game.get(MAIN + 0x20, 4), 0xAAAA55AA)

    def test_cache_cleared_each_tick(self):
        self.assertEqual(self.mem.read(MAIN, 4), 0)
        self.game.put(MAIN, 4, 9)
        self.assertEqual(self.mem.read(MAIN, 4), 0)            # same tick: snapshot
        self.mem.flush()
        self.assertEqual(self.mem.read(MAIN, 4), 9)

    def test_unmapped_and_outside(self):
        with self.assertRaises(ch.MemoryAccessError):
            self.mem.read(ALIAS, 4)
        trips = self.mem.round_trips
        with self.assertRaises(ch.MemoryAccessError):
            self.mem.read(0x10, 4)                              # outside the guest window: no round trip
        self.assertEqual(self.mem.round_trips, trips)
        with self.assertRaises(ch.MemoryAccessError):
            self.mem.write(0x10, 4, 1)

    def test_failed_write_reported(self):
        self.mem.tag = "x"
        self.mem.write(ALIAS + 4, 4, 1)
        self.mem.write(MAIN, 4, 2)
        failures = self.mem.flush()
        self.assertEqual([(t, a) for t, a, _ in failures], [("x", ALIAS + 4)])
        self.assertEqual(self.game.get(MAIN, 4), 2)

    def test_more_writes_than_one_batch(self):
        for k in range(ch.BATCH_MAX + 6):
            self.mem.write(HEAP + 4 * k, 4, k)
        self.mem.flush()
        self.assertEqual(self.mem.batches, 2)
        self.assertEqual(self.game.get(HEAP + 4 * (ch.BATCH_MAX + 5), 4), ch.BATCH_MAX + 5)

    def test_vm_end_to_end(self):
        self.game.put(MAIN + 0x100, 8, HEAP + 0x40)     # pointer in main
        self.game.put(HEAP + 0x48, 4, 3)
        c = cheat("58000000 00000100\n"                  # r0 = [main+0x100]
                  "C0433400 00000005\n"                  # if r3 < 5 ... (r3 = 0) yes
                  "54012000 00000008\n"                  # r1 = [r0+8]
                  "94011100 00000001\n"                  # r1 += 1
                  "A4100200 00000008\n"                  # [r0+8] = r1
                  "20000000")
        vm = ch.CheatVM(self.mem, say=lambda s: None)
        vm.tick([c])
        self.assertEqual(self.game.get(HEAP + 0x48, 4), 4)
        vm.tick([c])
        self.assertEqual(self.game.get(HEAP + 0x48, 4), 5)

    def test_game_closing_raises_gone(self):
        self.game.close_after = len(self.game.lines) + 1
        with self.assertRaises(ch.GameGone):
            self.mem.read(MAIN, 4)


class RunCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / f"{ch.BUILD_190}.txt"
        self.path.write_text("{Master}\n40050000 00000000 00000003\n"
                             "[Set value]\nA4500400 00000010\n"
                             "[Count up]\n58000000 00000020\n94000100 00000001\nA4000400 00000020\n"
                             "[Keys]\n80000001\n04000000 00000030 00000001\n20000000\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                code = ch.main(list(args))
            except KeyboardInterrupt:
                self.fail("Ctrl+C escaped main(): " + out.getvalue())
        return code, out.getvalue()

    def test_once(self):
        game = FakeGame()
        try:
            code, out = self.cli("run", str(self.path), "--port", str(game.port), "--all", "--once", "--timeout", "5")
        finally:
            game.stop()
        self.assertEqual(code, 0, out)
        self.assertEqual(game.get(MAIN + 0x10, 4), 3)
        self.assertEqual(game.get(MAIN + 0x20, 4), 1)
        self.assertEqual(game.get(MAIN + 0x30, 4), 0)
        self.assertEqual(out.count("Started:"), 4)
        self.assertIn("Started: Master [master]", out)
        self.assertIn("needs keys A", out)

    def test_runs_until_the_game_closes(self):
        game = FakeGame(close_after=12)
        try:
            code, out = self.cli("run", str(self.path), "--port", str(game.port), "--enable", "count up",
                                 "--hz", "500", "--timeout", "5")
        finally:
            game.stop()
        self.assertEqual(code, 0, out)
        self.assertIn("closed the connection", out)
        self.assertGreaterEqual(game.get(MAIN + 0x20, 4), 2)

    def test_no_game(self):
        game = FakeGame()
        port = game.port
        game.stop()
        code, out = self.cli("run", str(self.path), "--port", str(port), "--all", "--timeout", "1")
        self.assertEqual(code, 1)
        self.assertIn("SAVAGEWIND_CONTROL", out)

    def test_nothing_enabled(self):
        self.path.write_text("[A]\n04000000 00000000 00000001\n", encoding="utf-8")
        code, out = self.cli("run", str(self.path), "--port", "1")
        self.assertEqual(code, 1)
        self.assertIn("--enable", out)

    def test_bad_name(self):
        code, out = self.cli("run", str(self.path), "--port", "1", "--enable", "nope")
        self.assertEqual(code, 2)
        self.assertIn("no cheat named", out)

    def run_once(self, text, *args):
        """Write `text`, run it for one tick against a FakeGame; returns (code, output, game)."""
        self.path.write_text(text, encoding="utf-8")
        game = FakeGame()
        try:
            code, out = self.cli("run", str(self.path), "--port", str(game.port), "--once", "--timeout", "5", *args)
        finally:
            game.stop()
        return code, out, game

    def test_enable_order_does_not_change_run_order(self):
        code, out, game = self.run_once("[A sets r1]\n40010000 00000000 00000100\n"
                                        "[B uses r1]\n04010000 00000200 00000001\n", "--enable", "2", "1")
        self.assertEqual(code, 0, out)
        self.assertEqual((game.get(MAIN + 0x200, 4), game.get(MAIN + 0x300, 4)), (0, 1))
        self.assertLess(out.index("Started: A sets r1"), out.index("Started: B uses r1"))

    def test_broken_master_refuses_to_start(self):
        text = "{Master}\n40010000 00000000 00000100\nB0000000\n[Set flag]\n04010000 00000200 00000001\n"
        code, out, game = self.run_once(text, "--all")
        self.assertEqual(code, 2, out)
        self.assertIn("Can't start: the master code 'Master' can't run", out)
        self.assertIn("--force", out)
        self.assertEqual(game.lines, [])                   # never connected
        code, out, game = self.run_once(text, "--all", "--force")
        self.assertEqual(code, 0, out)
        self.assertIn("Skipped: Master (can't run: unknown opcode B", out)
        self.assertEqual(game.get(MAIN + 0x200, 4), 1)     # what --force asks for: runs without the master's r1

    def test_master_endless_loop_stops_everything(self):
        self.path.write_text("{Master}\n40010000 00000000 00000100\n30000000 00000000\n31000000\n"
                             "[Set flag]\n04010000 00000200 00000001\n", encoding="utf-8")
        game = FakeGame(close_after=40)    # ends the run if the cheats wrongly keep going
        try:
            code, out = self.cli("run", str(self.path), "--port", str(game.port), "--all", "--hz", "500",
                                 "--timeout", "5")
        finally:
            game.stop()
        self.assertEqual(code, 1, out)
        self.assertIn("Master: stopped", out)
        self.assertIn("1 other cheat(s) stopped too", out)
        self.assertIn("All cheats stopped.", out)
        self.assertEqual((game.get(MAIN + 0x200, 4), game.get(MAIN + 0x300, 4)), (0, 0))

    def test_only_master_is_not_enough(self):
        self.path.write_text("{Master}\n40010000 00000000 00000100\n[Infinite hearts]\n04000000 00000000 00000001\n",
                             encoding="utf-8")
        code, out = self.cli("run", str(self.path), "--port", "1", "--once")
        self.assertEqual(code, 1, out)
        self.assertIn("No cheats are on: only the master code would run. Use --enable", out)
        self.assertNotIn("Started:", out)

    def test_nothing_runnable_message_by_case(self):
        self.path.write_text("[Broken]\nB0000000\n[Good]\n04000000 00000000 00000001\n", encoding="utf-8")
        code, out = self.cli("run", str(self.path), "--port", "1", "--enable", "Broken")
        self.assertEqual(code, 1)
        self.assertIn("Skipped: Broken (can't run: unknown opcode B", out)
        self.assertIn("Nothing to run: none of the chosen cheats can run.", out)
        self.assertNotIn("Use --enable", out)
        self.path.write_text("{M}\n40010000 00000000 00000100\n", encoding="utf-8")
        code, out = self.cli("run", str(self.path), "--port", "1", "--all")
        self.assertEqual(code, 1)
        self.assertIn("Nothing to run: the file has no cheats besides the master code.", out)

    def test_empty_master_runs_quietly(self):
        code, out, game = self.run_once("{Master Code}\n\n[Infinite hearts]\n04000000 00000000 00000001\n", "--all")
        self.assertEqual(code, 0, out)
        self.assertNotIn("Skipped", out)
        self.assertNotIn("None", out)
        self.assertNotIn("Master Code", out)
        self.assertIn("Started: Infinite hearts", out)
        self.assertEqual(game.get(MAIN, 4), 1)

    def test_second_master_in_run(self):
        text = ("{M1}\n40010000 00000000 00000100\n{M2}\n40020000 00000000 00000200\n"
                "[A]\n04010000 00000000 00000001\n")
        code, out, _ = self.run_once(text, "--all")
        self.assertEqual(code, 2, out)
        self.assertIn("'M2' can't run", out)
        code, out, game = self.run_once(text, "--all", "--force")
        self.assertEqual(code, 0, out)
        self.assertIn("Skipped: M2 (can't run: a second master code; only one is allowed)", out)
        self.assertEqual(game.get(MAIN + 0x100, 4), 1)     # M1's r1 still reaches A

    # Ctrl+C before the cheats start, and closing the connection when the ping fails

    def fake_control(self, exc):
        made = []

        class FakeControl:
            def __init__(self, *a, **kw):
                self.closed = False
                made.append(self)

            def cmd(self, *words):
                raise exc

            def close(self):
                self.closed = True

        return FakeControl, made

    def test_ctrl_c_while_connecting(self):
        with mock.patch.object(ch, "Control", side_effect=KeyboardInterrupt):
            code, out = self.cli("run", str(self.path), "--all", "--port", "1", "--wait", "5")
        self.assertEqual((code, out.strip().splitlines()[-1]), (0, "Stopped."))

    def test_ctrl_c_during_wait_retry(self):
        def interrupted(_):
            raise KeyboardInterrupt
        game = FakeGame()
        port = game.port
        game.stop()
        fake_time = types.SimpleNamespace(monotonic=time.monotonic, sleep=interrupted)
        with mock.patch.object(control_mod, "time", fake_time):
            code, out = self.cli("run", str(self.path), "--all", "--port", str(port), "--wait", "5",
                                 "--timeout", "1")
        self.assertEqual((code, out.strip().splitlines()[-1]), (0, "Stopped."))

    def test_ctrl_c_during_ping_closes(self):
        cls, made = self.fake_control(KeyboardInterrupt())
        with mock.patch.object(ch, "Control", cls):
            code, out = self.cli("run", str(self.path), "--all", "--port", "1")
        self.assertEqual(code, 0)
        self.assertIn("Stopped.", out)
        self.assertTrue(made[0].closed)

    def test_failed_ping_closes(self):
        cls, made = self.fake_control(ControlError("the game didn't answer in time"))
        with mock.patch.object(ch, "Control", cls):
            code, out = self.cli("run", str(self.path), "--all", "--port", "1")
        self.assertEqual(code, 1)
        self.assertIn("Can't reach the game", out)
        self.assertTrue(made[0].closed)


# ---------------------------------------------------------------------------------------------------------------
# Groups ("// group: <name>" lines, as modman writes its merged file): each group runs as its own cheat file

GROUPED = ("// group: Mod A\n{Master A}\n40000000 00000000 00000010\n"           # r0 = 0x10
           "[A: arrows]\n40000000 00000000 00000100\n04000000 00200000 000003E7\n"   # r0 = 0x100; [main+0x200100]
           "[A: uses r0]\n04000000 00210000 00000001\n"                         # [main+0x210000 + r0]
           "// group: Mod B\n"
           "[B: hearts]\n04000000 00123450 00000050\n")                          # [main+0x123450 + r0]


class Groups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / f"{ch.BUILD_190}.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, text):
        self.path.write_text(text, encoding="utf-8")
        return ch.load(self.path)

    def tick(self, chosen, vm=None):
        mem = ch.FakeMemory()
        said = []
        vm = vm or ch.CheatVM(mem, say=said.append)
        vm.tick(chosen)
        return vm.mem, said

    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ch.main(list(args))
        return code, out.getvalue()

    def test_group_lines_split_the_file(self):
        cheats, warnings = ch.parse_text(GROUPED + "// GROUP:   Mod C  \n00000000\n[C]\n20000000\n")
        self.assertEqual([(c.name, c.group, c.group_name) for c in cheats],
                         [("Master A", 1, "Mod A"), ("A: arrows", 1, "Mod A"), ("A: uses r0", 1, "Mod A"),
                          ("B: hearts", 2, "Mod B"), ("C", 3, "Mod C")])
        # (a group line ends the cheat before it: code right after it belongs to no cheat)
        self.assertEqual(warnings, ["line 13: code before any [cheat name], ignored"])
        self.assertEqual(cheats[3].words, [0x04000000, 0x00123450, 0x00000050])
        plain, _ = ch.parse_text("// group of cheats, a plain comment\n[X]\n20000000\n// from Mod A\n")
        self.assertEqual([(c.group, c.group_name) for c in plain], [(0, "")])
        self.assertFalse(self.load("[X]\n20000000\n").grouped)
        self.assertTrue(self.load(GROUPED).grouped)

    def test_one_master_per_group(self):
        cheats, _ = ch.parse_text("// group: A\n{M}\n40000000 00000000 00000010\n{M2}\n20000000\n"
                                  "// group: B\n{M}\n40000000 00000000 00000020\n")
        self.assertEqual([c.error for c in cheats],
                         [None, "a second master code in its group; only one is allowed per group", None])

    def test_each_group_starts_from_fresh_registers(self):
        cf = self.load(GROUPED)
        mem, _ = self.tick(ch.select(cf, None, True))
        self.assertEqual(mem.get(MAIN + 0x200100), 0x3E7)    # within a group the registers carry over...
        self.assertEqual(mem.get(MAIN + 0x210100), 1)
        self.assertEqual(mem.get(MAIN + 0x123450), 0x50)     # ...but group B starts from r0 = 0
        self.assertEqual(mem.get(MAIN + 0x123550), 0)
        # without the group lines it is one program, as before: B sees A's r0
        mem, _ = self.tick(ch.select(self.load(GROUPED.replace("// group:", "// from")), None, True))
        self.assertEqual((mem.get(MAIN + 0x123450), mem.get(MAIN + 0x123550)), (0, 0x50))

    def test_saved_registers_start_at_0_in_each_group(self):
        save = "40010000 00000000 00000005\nC1010110\n"        # r1 = 5; saved[1] = r1
        restore = "C1010100\nA4100400 00000040\n"             # r1 = saved[1]; [main+0x40] = r1
        text = f"[Save]\n{save}// group: other\n[Restore]\n{restore}"
        mem, _ = self.tick(ch.select(self.load("// group: one\n" + text), None, True))
        self.assertIn(("w", MAIN + 0x40, 4, 0), mem.log)
        mem, _ = self.tick(ch.select(self.load(text.replace("// group: other\n", "")), None, True))
        self.assertEqual(mem.get(MAIN + 0x40), 5)

    def test_static_registers_belong_to_their_group(self):
        cf = self.load("// group: one\n[W]\n40000000 00000000 0000002A\nC3000800\n"    # static[0x80] = 42
                       "// group: two\n[W2]\n40000000 00000000 00000007\nC3000810\n")   # static[0x81] = 7
        vm = ch.CheatVM(ch.FakeMemory(), say=lambda s: None)
        chosen = ch.select(cf, None, True)
        vm.tick(chosen)
        vm.tick(chosen)
        self.assertEqual((vm._statics[1][0x80], vm._statics[1][0x81]), (42, 0))
        self.assertEqual((vm._statics[2][0x80], vm._statics[2][0x81]), (0, 7))
        self.assertEqual(vm.static, [0] * 256)                # (group 0's, the only ones without group lines)

    def test_a_master_that_stops_stops_only_its_group(self):
        cf = self.load("// group: Loop\n{M}\n40010000 00000000 00000100\n30000000 00000000\n31000000\n"
                       "[Set flag]\n04010000 00000200 00000001\n"
                       "// group: Fine\n[Other]\n04000000 00000400 00000001\n")
        mem = ch.FakeMemory()
        said = []
        vm = ch.CheatVM(mem, say=said.append, max_steps=50)
        chosen = ch.select(cf, None, True)
        vm.tick(chosen)
        vm.tick(chosen)
        self.assertEqual((mem.get(MAIN + 0x200), mem.get(MAIN + 0x300)), (0, 0))
        self.assertEqual(mem.get(MAIN + 0x400), 1)
        self.assertNotIn(id(chosen[-1]), vm.stopped)
        self.assertIn("1 other cheat(s) stopped too: they depend on the master code (M) of group Loop.", said)

    def test_select_runs_a_groups_master_only_with_its_cheats(self):
        text = ("// group: A\n{MA}\n40000000 00000000 00000010\n[X]\n04000000 00001000 00000001\n"
                "// group: B\n{MB}\n40000000 00000000 00000020\n[Y]\n04000000 00002000 00000001\n")
        cf = self.load(text)
        self.assertEqual([c.name for c in ch.select(cf, None, True)], ["MA", "X", "MB", "Y"])
        self.assertEqual([c.name for c in ch.select(cf, ["Y", "X"], False)], ["MA", "X", "MB", "Y"])
        self.assertEqual([c.name for c in ch.select(cf, ["Y"], False)], ["MB", "Y"])
        self.assertEqual(ch.select(cf, None, False), [])      # (nothing on in a toggles.txt: no master runs alone)
        mem, _ = self.tick(ch.select(cf, ["Y"], False))
        self.assertEqual((mem.get(MAIN + 0x1010), mem.get(MAIN + 0x2020)), (0, 1))

    def test_a_master_that_cant_run_skips_only_its_group(self):
        cf = self.load("// group: Bad\n{M}\n40010000 00000000 00000100\nB0000000\n"
                       "[Set flag]\n04010000 00000200 00000001\n"
                       "// group: Good\n[Other]\n04000000 00000400 00000001\n")
        skipped = []
        self.assertEqual([c.name for c in ch.select(cf, None, True, skipped)], ["Other"])   # (no ValueError)
        self.assertEqual([c.name for c in skipped], ["M", "Set flag"])
        skipped = []
        self.assertEqual([c.name for c in ch.select(cf, None, True, skipped, force=True)], ["Set flag", "Other"])
        self.assertEqual([c.name for c in skipped], ["M"])
        game = FakeGame()
        try:
            code, out = self.cli("run", str(self.path), "--port", str(game.port), "--all", "--once", "--timeout", "5")
        finally:
            game.stop()
        self.assertEqual(code, 0, out)
        self.assertIn("Skipped: M (can't run: unknown opcode B", out)
        self.assertIn("Skipped: Set flag (the master code of its group can't run, group Bad)", out)
        self.assertIn("Started: Other", out)
        self.assertEqual((game.get(MAIN + 0x200, 4), game.get(MAIN + 0x400, 4)), (0, 1))

    def test_run_names_the_group_of_each_master(self):
        self.load(GROUPED)
        game = FakeGame(regions=((MAIN, MAIN + 0x300000),))
        try:
            code, out = self.cli("run", str(self.path), "--port", str(game.port), "--all", "--once", "--timeout", "5")
        finally:
            game.stop()
        self.assertEqual(code, 0, out)
        self.assertIn("Started: Master A [master of group Mod A]", out)
        self.assertEqual((game.get(MAIN + 0x123450, 4), game.get(MAIN + 0x123550, 4)), (0x50, 0))

    def test_list_shows_the_groups(self):
        self.load(GROUPED)
        code, out = self.cli("list", str(self.path))
        self.assertEqual(code, 0, out)
        self.assertIn("3 cheat(s), 3 can run, in 2 groups, 1 master code(s).", out)
        self.assertLess(out.index("group: Mod A"), out.index("Master A"))
        self.assertLess(out.index("A: uses r0"), out.index("group: Mod B"))
        self.assertLess(out.index("group: Mod B"), out.index("B: hearts"))
        self.assertRegex(out, r"M\s+no\s+ok\s+4\s+Master A")          # (no cheat of its group is on)
        self.assertRegex(out, r"3\s+no\s+ok\s+0\s+B: hearts")        # (numbers go on across the groups)

    def test_no_group_lines_list_unchanged(self):
        self.load("{M}\n40000000 00000000 00000010\n[X]\n04000000 00001000 00000001\n")
        code, out = self.cli("list", str(self.path))
        self.assertIn("1 cheat(s), 1 can run, plus a master code.", out)
        self.assertNotIn("group", out)
        self.assertRegex(out, r"M\s+always\s+ok")


if __name__ == "__main__":
    unittest.main()
