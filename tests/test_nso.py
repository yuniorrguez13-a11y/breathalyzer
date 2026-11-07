"""Tests for tools/nso.py. Uses only made-up bytes, never game files."""
import hashlib
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import nso  # noqa: E402


def make_nso(build_id: bytes, segs: list[tuple[int, bytes]], *, compress=True, hashed=True) -> bytes:
    """A small NSO with the given (memory offset, bytes) for text, rodata and data."""
    hdr = bytearray(nso.HEADER_SIZE)
    hdr[0:4] = nso.MAGIC
    hdr[0x40:0x40 + len(build_id)] = build_id
    flags = 0
    for i in range(3):
        if hashed:
            flags |= nso.FLAG_HASHED[i]
        if compress:
            flags |= nso.FLAG_COMPRESSED[i]
    struct.pack_into("<I", hdr, 0x0C, flags)
    segments = [nso.Segment(name, off, data, compress, hashed) for name, (off, data) in zip(nso.SEGMENTS, segs)]
    return nso.Nso(bytes(hdr), segments, b"\0", 0x40).to_bytes()


BID = bytes.fromhex("aabbccddeeff0011") + bytes(24)
SEGS = [(0x0, bytes(range(256)) * 16), (0x1000, b"rodata!" * 100), (0x2000, b"\x01\x02" * 64)]


class Lz4(unittest.TestCase):
    def test_overlapping_match(self):
        block = bytes([0x35]) + b"abc" + b"\x03\x00" + bytes([0x30]) + b"XYZ"
        self.assertEqual(nso.lz4_decompress(block, 15), b"abcabcabcabcXYZ")

    def test_long_match_length(self):
        # literals "ab", match offset 2 with length 15 + 10 + 4 = 29, then 1 literal
        block = bytes([0x2F]) + b"ab" + b"\x02\x00" + bytes([10]) + bytes([0x10]) + b"!"
        out = nso.lz4_decompress(block, 2 + 29 + 1)
        self.assertEqual(out, (b"ab" * 16)[:31] + b"!")

    def test_store_round_trip(self):
        for n in (0, 1, 14, 15, 16, 269, 270, 271, 300, 5000):
            data = bytes((i * 7) & 0xFF for i in range(n))
            self.assertEqual(nso.lz4_decompress(nso.lz4_store(data), n), data, n)

    def test_bad_blocks(self):
        with self.assertRaises(nso.NsoError):
            nso.lz4_decompress(bytes([0x10]) + b"a" + b"\x00\x00", 10)   # offset 0
        with self.assertRaises(nso.NsoError):
            nso.lz4_decompress(bytes([0x10]) + b"a" + b"\x05\x00", 10)   # before the start
        with self.assertRaises(nso.NsoError):
            nso.lz4_decompress(bytes([0x50]) + b"ab", 5)                 # literals past the end
        with self.assertRaises(nso.NsoError):
            nso.lz4_decompress(bytes([0x10]) + b"a", 3)                  # wrong size


class NsoFile(unittest.TestCase):
    def test_round_trip(self):
        for compress in (True, False):
            raw = make_nso(BID, SEGS, compress=compress)
            n = nso.parse(raw)
            self.assertEqual(n.build_id, BID)
            self.assertEqual(n.build_id_hex[:16], "aabbccddeeff0011")
            for seg, (off, data) in zip(n.segments, SEGS):
                self.assertEqual((seg.mem_offset, seg.data, seg.compressed), (off, data, compress))
            self.assertEqual(nso.parse(n.to_bytes()).segments[1].data, SEGS[1][1])

    def test_hash_checked(self):
        raw = bytearray(make_nso(BID, SEGS, compress=False))
        text_off = struct.unpack_from("<I", raw, 0x10)[0]
        raw[text_off] ^= 0xFF
        with self.assertRaises(nso.NsoError):
            nso.parse(bytes(raw))
        nso.parse(bytes(raw), check_hashes=False)  # allowed when asked

    def test_write_and_read_across_segments(self):
        n = nso.parse(make_nso(BID, SEGS))
        n.write(0x10, b"\xAA\xBB")
        self.assertEqual(n.read(0x10, 2), b"\xAA\xBB")
        # text ends exactly where rodata starts (0x1000), so a write can cross from one into the other
        n.write(0x0FFF, b"\x11\x22")
        self.assertEqual(n.read(0x0FFF, 2), b"\x11\x22")
        self.assertEqual(n.segment("rodata").data[0], 0x22)
        # but not into the gap between rodata's end and data's start
        with self.assertRaises(nso.NsoError):
            n.write(0x1000 + len(SEGS[1][1]), b"\x00")  # gap between rodata end and data start

    def test_rewrite_keeps_hashes_valid(self):
        n = nso.parse(make_nso(BID, SEGS))
        n.write(0x20, b"\x00" * 8)
        out = n.to_bytes()
        again = nso.parse(out)  # would raise if a hash were stale
        self.assertEqual(again.read(0x20, 8), b"\x00" * 8)
        self.assertEqual(hashlib.sha256(again.segments[0].data).digest(), out[0xA0:0xC0])

    def test_stored_output(self):
        n = nso.parse(make_nso(BID, SEGS))
        out = n.to_bytes(compress=False)
        flags = struct.unpack_from("<I", out, 0x0C)[0]
        self.assertEqual(flags & 0b111, 0)
        self.assertEqual(nso.parse(out).segments[2].data, SEGS[2][1])

    def test_not_an_nso(self):
        with self.assertRaises(nso.NsoError):
            nso.parse(b"NOPE" + bytes(0x100))


if __name__ == "__main__":
    unittest.main()
