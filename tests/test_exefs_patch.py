"""Tests for tools/exefs_patch.py. Uses only made-up modules and patches, never game files."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.dirname(__file__))
import exefs_patch as ep  # noqa: E402
import nso  # noqa: E402
from test_nso import make_nso  # noqa: E402

MAIN_ID = bytes.fromhex("aabbccddeeff0011") + bytes(24)
SDK_ID = bytes.fromhex("1122334455667788") + bytes(24)
SEGS = [(0x0, bytes(0x1000)), (0x1000, b"\x55" * 0x200), (0x2000, b"\x66" * 0x100)]


def ips(records, *, ips32=False):
    out = bytearray(b"IPS32" if ips32 else b"PATCH")
    for off, payload in records:
        out += off.to_bytes(4 if ips32 else 3, "big")
        if isinstance(payload, tuple):  # RLE
            count, value = payload
            out += b"\x00\x00" + count.to_bytes(2, "big") + bytes([value])
        else:
            out += len(payload).to_bytes(2, "big") + payload
    out += b"EEOF" if ips32 else b"EOF"
    return bytes(out)


class Parsing(unittest.TestCase):
    def test_ips_records_and_rle(self):
        ps = ep.parse_ips(ips([(0x110, b"\x1f\x20\x03\xd5"), (0x200, (5, 0xAB))]), source="x.ips", build_id="aa")
        self.assertEqual([(p.offset, p.data) for p in ps], [(0x10, b"\x1f\x20\x03\xd5"), (0x100, b"\xab" * 5)])

    def test_ips32(self):
        ps = ep.parse_ips(ips([(0x1000100, b"\x01")], ips32=True), source="x.ips", build_id="aa")
        self.assertEqual((ps[0].offset, ps[0].data), (0x1000000, b"\x01"))

    def test_ips_errors(self):
        with self.assertRaises(ep.PatchError):
            ep.parse_ips(b"NOTIPS", source="x.ips", build_id="aa")
        with self.assertRaises(ep.PatchError):
            ep.parse_ips(b"PATCH\x00\x01\x10\x00\x10\x01", source="x.ips", build_id="aa")  # truncated, no EOF

    def test_build_id_from_name(self):
        self.assertEqual(ep.build_id_from_name("AABBCCDDEEFF0011000000000000000000000000.ips"), "aabbccddeeff0011")
        with self.assertRaises(ep.PatchError):
            ep.build_id_from_name("main.ips")

    def test_pchtxt(self):
        text = """@nsobid-AABBCCDDEEFF00110000000000000000
@flag print_values
@flag offset_shift 0x100

// Infinite thing
@enabled
00000010 1F2003D5
00000020 "hi\\n"   // a string value

// Off by default
@disabled
00000030 00000000
@stop
00000040 FF
"""
        ps = ep.parse_pchtxt(text, source="mod.pchtxt")
        self.assertEqual(len(ps), 3)
        self.assertEqual((ps[0].name, ps[0].offset, ps[0].data, ps[0].enabled), ("Infinite thing", 0x10, bytes.fromhex("1F2003D5"), True))
        self.assertEqual(ps[1].data, b"hi\n")
        self.assertEqual((ps[2].name, ps[2].enabled), ("Off by default", False))
        self.assertEqual(ps[0].build_id, "aabbccddeeff0011")

    def test_pchtxt_without_shift_counts_the_header(self):
        ps = ep.parse_pchtxt("@nsobid-aabbccddeeff0011\n@enabled\n00000110 00\n", source="m.pchtxt")
        self.assertEqual(ps[0].offset, 0x10)

    def test_pchtxt_errors(self):
        with self.assertRaises(ep.PatchError):
            ep.parse_pchtxt("@enabled\n0010 00\n", source="m.pchtxt")  # no nsobid
        with self.assertRaises(ep.PatchError):
            ep.parse_pchtxt("@nsobid-aa\n@enabled\n0010 0\n", source="m.pchtxt")  # half a byte

    def test_conflicts(self):
        a = ep.Patch("a", "a", "x", 0x10, b"\x01\x02\x03\x04")
        b = ep.Patch("b", "b", "x", 0x12, b"\x03\x04")   # same bytes where they overlap: fine
        c = ep.Patch("c", "c", "x", 0x13, b"\xff")       # clashes with a and with b
        d = ep.Patch("d", "d", "x", 0x20, b"\xff")       # elsewhere
        self.assertEqual([(x.name, y.name) for x, y in ep.find_conflicts([a, b, c, d])], [("a", "c"), ("b", "c")])
        self.assertEqual(ep.find_conflicts([a, b, d]), [])


class Applying(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.exefs = os.path.join(root, "exefs")
        os.makedirs(self.exefs)
        with open(os.path.join(self.exefs, "main"), "wb") as fh:
            fh.write(make_nso(MAIN_ID, SEGS))
        with open(os.path.join(self.exefs, "sdk"), "wb") as fh:
            fh.write(make_nso(SDK_ID, SEGS))
        with open(os.path.join(self.exefs, "main.npdm"), "wb") as fh:
            fh.write(b"META")
        self.patches = os.path.join(root, "patches")
        os.makedirs(self.patches)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, data):
        p = os.path.join(self.patches, name)
        with open(p, "wb" if isinstance(data, bytes) else "w") as fh:
            fh.write(data)
        return p

    def test_check_sorts_patches(self):
        self.put("AABBCCDDEEFF0011.ips", ips([(0x110, b"\x01\x02")]))
        self.put("1122334455667788.ips", ips([(0x1100, b"\x03")]))
        self.put("DEADBEEFDEADBEEF.ips", ips([(0x110, b"\x04")]))
        self.put("x.pchtxt", "@nsobid-aabbccddeeff0011\n@flag offset_shift 0x100\n@disabled\n0010 05\n")
        rep = ep.check(self.exefs, [self.patches])
        self.assertEqual(sorted(rep.targets), ["main", "sdk"])
        self.assertEqual(len(rep.wrong_version), 1)
        self.assertEqual(len(rep.disabled), 1)
        self.assertTrue(rep.ok)

    def test_apply_writes_patched_copy_and_leaves_original(self):
        self.put("AABBCCDDEEFF0011.ips", ips([(0x110, b"\xde\xad"), (0x1100, (4, 0x77))]))
        out = os.path.join(self.tmp.name, "out")
        rep, notes = ep.apply(self.exefs, out, [self.patches])
        self.assertTrue(rep.ok, rep.errors)
        patched = nso.load(os.path.join(out, "main"))
        self.assertEqual(patched.read(0x10, 2), b"\xde\xad")
        self.assertEqual(patched.read(0x1000, 4), b"\x77" * 4)
        self.assertEqual(patched.build_id, MAIN_ID)  # same build id, so Savage Wind still knows the version
        self.assertEqual(nso.load(os.path.join(self.exefs, "main")).read(0x10, 2), b"\x00\x00")
        self.assertTrue(os.path.isfile(os.path.join(out, "main.npdm")))
        self.assertTrue(os.path.isfile(os.path.join(out, "sdk")))

    def test_conflict_stops_apply(self):
        self.put("AABBCCDDEEFF0011.ips", ips([(0x110, b"\x01")]))
        self.put("b.pchtxt", "@nsobid-AABBCCDDEEFF0011\n@flag offset_shift 0x100\n// Other\n@enabled\n0010 02\n")
        out = os.path.join(self.tmp.name, "out")
        rep, notes = ep.apply(self.exefs, out, [self.patches])
        self.assertFalse(rep.ok)
        self.assertEqual(len(rep.conflicts), 1)
        self.assertFalse(os.path.exists(out))

    def test_out_of_range_reported(self):
        self.put("AABBCCDDEEFF0011.ips", ips([(0x100 + 0x1500, b"\x01")]))  # in the gap after rodata
        rep, _ = ep.apply(self.exefs, os.path.join(self.tmp.name, "out"), [self.patches])
        self.assertEqual(len(rep.errors), 1)

    def test_code_vs_data_patches(self):
        self.put("AABBCCDDEEFF0011.ips", ips([(0x100 + 0x1000, b"\x01")]))   # rodata only
        rep = ep.check(self.exefs, [self.patches])
        self.assertFalse(rep.needs_rebuild)
        self.put("AABBCCDDEEFF0011.ips", ips([(0x100 + 0x1000, b"\x01"), (0x100 + 0xFFF, b"\x02\x03")]))  # crosses into text
        rep = ep.check(self.exefs, [self.patches])
        self.assertTrue(rep.needs_rebuild)
        self.assertEqual(rep.code_patches["main"], 1)

    def test_refuses_in_place(self):
        self.put("AABBCCDDEEFF0011.ips", ips([(0x110, b"\x01")]))
        with self.assertRaises(ep.PatchError):
            ep.apply(self.exefs, self.exefs, [self.patches])

    def test_cli(self):
        self.put("AABBCCDDEEFF0011.ips", ips([(0x110, b"\x01")]))
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ep.main(["check", self.exefs, self.patches])
        self.assertEqual(code, 0)
        self.assertIn("OK  main: 1 patches", buf.getvalue())

    def test_cli_names_outside_the_code_page_when_redirected(self):
        """Redirected stdout on Windows is the ANSI code page with strict errors: a patch file named in Chinese made
        `check` end in a UnicodeEncodeError instead of printing its report (made-up patch name)."""
        self.put("补丁.pchtxt", "@nsobid-aabbccddeeff0011\n@flag offset_shift 0x100\n@enabled\n0010 05\n")
        raw = io.BytesIO()
        out = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        with mock.patch.object(sys, "stdout", out):
            code = ep.main(["check", self.exefs, self.patches])
            out.flush()
        self.assertEqual(code, 0)
        self.assertIn("OK  main: 1 patches from ??.pchtxt", raw.getvalue().decode("cp1252"))


class SameFolderSpelledOtherwise(unittest.TestCase):
    """apply must never write into the exefs folder it reads, however the output folder is spelled (W6)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.exefs = os.path.join(root, "Exefs")  # (only main: every file is a patch target)
        os.makedirs(self.exefs)
        self.main = os.path.join(self.exefs, "main")
        with open(self.main, "wb") as fh:
            fh.write(make_nso(MAIN_ID, SEGS))
        self.patches = os.path.join(root, "patches")
        os.makedirs(self.patches)
        with open(os.path.join(self.patches, "AABBCCDDEEFF0011.ips"), "wb") as fh:
            fh.write(ips([(0x110, b"\xde\xad")]))

    def tearDown(self):
        self.tmp.cleanup()

    def original(self):
        with open(self.main, "rb") as fh:
            return fh.read()

    def assertRefused(self, out):
        before = self.original()
        with self.assertRaises(ep.PatchError):
            ep.apply(self.exefs, out, [self.patches])
        self.assertEqual(self.original(), before)  # the user's main is untouched
        self.assertEqual(os.listdir(self.exefs), ["main"])

    @unittest.skipUnless(os.path.normcase("A") == os.path.normcase("a"), "case-insensitive file names only")
    def test_other_case(self):
        self.assertRefused(os.path.join(self.tmp.name, "EXEFS"))

    def test_trailing_separator_and_dots(self):
        self.assertRefused(self.exefs + os.sep)
        self.assertRefused(os.path.join(self.exefs, "..", "Exefs"))

    @unittest.skipUnless(sys.platform == "win32", "8.3 short names are Windows only")
    def test_short_name(self):
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(self.exefs, buf, len(buf))
        if not n or os.path.normcase(buf.value) == os.path.normcase(self.exefs):
            self.skipTest("this volume makes no 8.3 names")
        self.assertRefused(buf.value)

    def test_out_holds_a_hard_link_to_the_module(self):
        out = os.path.join(self.tmp.name, "out")
        os.makedirs(out)
        try:
            os.link(self.main, os.path.join(out, "main"))
        except OSError:
            self.skipTest("no hard links here")
        self.assertRefused(out)

    def test_a_new_folder_still_works(self):
        out = os.path.join(self.tmp.name, "out")
        rep, notes = ep.apply(self.exefs, out, [self.patches])
        self.assertTrue(rep.ok, rep.errors)
        self.assertEqual(nso.load(os.path.join(out, "main")).read(0x10, 2), b"\xde\xad")
        self.assertEqual(nso.load(self.main).read(0x10, 2), b"\x00\x00")


if __name__ == "__main__":
    unittest.main()
