"""Tests for tools/font.py. Every font, sheet, archive and TrueType file here is built in memory from made-up shapes:
no game files, and nothing of the kinds check_clean blocks is written into the repo."""
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import font as F  # noqa: E402
from wiiu_model import tegra  # noqa: E402

UPEM = 1000
CAP = 700


# ------------------------------------------------------------------------------------------------ TrueType fixture

def rect(x0, y0, x1, y1):
    """A clockwise (TrueType outer) rectangle contour."""
    return [(x0, y0, True), (x0, y1, True), (x1, y1, True), (x1, y0, True)]


def make_ttf(chars: str = F.ASCII, *, notice: str = "SIL Open Font License 1.1", fstype: int = 0,
             missing: str = "") -> bytes:
    """A TrueType font: 'H' (two stems and a bar), 'l' (one stem), 'o' (quadratic curves), 'i' (a composite of 'l'),
    a space, and a box for every other character of `chars` (less `missing`)."""
    glyphs = [None, []]   # .notdef, space
    cmap = {0x20: 1}
    adv = [500, 300]
    for c in chars:
        if c == " " or c in missing:
            continue
        if c == "H":
            g = [rect(60, 0, 160, CAP), rect(440, 0, 540, CAP), rect(160, 300, 440, 400)]
        elif c == "l":
            g = [rect(60, 0, 360, 750)]
        elif c == "o":  # outer and inner rings, both of off-curve points (all quadratic)
            g = [[(50, 250, False), (50, 500, False), (300, 500, False), (300, 0, False)],
                 [(100, 250, False), (250, 0, False), (250, 450, False), (100, 450, False)]]
        elif c == "i":
            g = []  # (made a composite of 'l' below)
        elif c == "g":  # a descender
            g = [rect(60, -400, 400, 500)]
        else:
            g = [rect(60, 0, 60 + 20 * (ord(c) % 17) + 100, CAP)]
        cmap[ord(c)] = len(glyphs)
        glyphs.append(g)
        adv.append(600)
    if ord("i") in cmap and ord("l") in cmap:  # the composite: 'l' moved up 10 and right 40
        glyphs[cmap[ord("i")]] = ("comp", [(cmap[ord("l")], 40, 10)])
    glyf = bytearray()
    loca = [0]
    for g in glyphs:
        if g:
            if isinstance(g, tuple):
                glyf += struct.pack(">hhhhh", -1, 0, 0, 0, 0)
                for k, (gid, dx, dy) in enumerate(g[1]):
                    flags = 0x1 | 0x2 | (0x20 if k + 1 < len(g[1]) else 0)
                    glyf += struct.pack(">HHhh", flags, gid, dx, dy)
            else:
                pts = [p for c in g for p in c]
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                glyf += struct.pack(">hhhhh", len(g), min(xs), min(ys), max(xs), max(ys))
                end = -1
                for c in g:
                    end += len(c)
                    glyf += struct.pack(">H", end)
                glyf += struct.pack(">H", 0)
                glyf += bytes(1 if p[2] else 0 for p in pts)
                for axis in (0, 1):
                    prev = 0
                    for p in pts:
                        glyf += struct.pack(">h", p[axis] - prev)
                        prev = p[axis]
            glyf += bytes(-len(glyf) % 4)
        loca.append(len(glyf))
    codes = sorted(cmap)
    seg = len(codes) + 1
    sub = bytearray(struct.pack(">HHHHHHH", 4, 0, 0, seg * 2, 0, 0, 0))
    sub += b"".join(struct.pack(">H", c) for c in codes) + struct.pack(">H", 0xFFFF) + bytes(2)
    sub += b"".join(struct.pack(">H", c) for c in codes) + struct.pack(">H", 0xFFFF)
    sub += b"".join(struct.pack(">h", (cmap[c] - c + 0x8000) % 0x10000 - 0x8000) for c in codes) + struct.pack(">h", 1)
    sub += bytes(2 * seg)
    struct.pack_into(">H", sub, 2, len(sub))
    name_recs = [(0, "Copyright test"), (1, "Testfont"), (2, "Regular"), (13, notice)]
    strings = b""
    nrec = b""
    for nid, s in name_recs:
        raw = s.encode("utf-16-be")
        nrec += struct.pack(">6H", 3, 1, 0x409, nid, len(raw), len(strings))
        strings += raw
    tables = {
        b"head": struct.pack(">IIIIHHqqhhhhHHhhh", 0x10000, 0x10000, 0, 0x5F0F3CF5, 0, UPEM, 0, 0,
                             0, -250, 600, 800, 0, 8, 2, 1, 0),
        b"hhea": struct.pack(">IhhhHhhhhhhhhhhhH", 0x10000, 800, -250, 0, 600, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0,
                             len(glyphs)),
        b"maxp": struct.pack(">IH", 0x5000, len(glyphs)),
        b"hmtx": b"".join(struct.pack(">Hh", a, 0) for a in adv),
        b"cmap": struct.pack(">HHHHI", 0, 1, 3, 1, 12) + bytes(sub),
        b"loca": b"".join(struct.pack(">I", x) for x in loca),
        b"glyf": bytes(glyf),
        b"name": struct.pack(">HHH", 0, len(name_recs), 6 + 12 * len(name_recs)) + nrec + strings,
        b"OS/2": struct.pack(">HhHHH", 4, 500, 400, 5, fstype) + bytes(68),
    }
    out = bytearray(struct.pack(">IHHHH", 0x10000, len(tables), 0, 0, 0))
    off = 12 + 16 * len(tables)
    body = b""
    for tag in sorted(tables):
        data = tables[tag]
        out += tag + struct.pack(">III", 0, off + len(body), len(data))
        body += data + bytes(-len(data) % 4)
    return bytes(out + body)


# ------------------------------------------------------------------------------------------------ BFFNT fixture

def make_bffnt(fmt=F.FMT_BC4, *, cw=8, ch=10, cols=14, rows=11, baseline=8, glyphs=None, blocks=None,
               sw=128, sh=128) -> bytes:
    """A one-sheet bitmap font. `glyphs`: {index: (pixel rows top first, or None, Glyph)}. `blocks`: CMAP blocks as
    font.Bffnt holds them."""
    glyphs = glyphs or {}
    grid = [[0] * sw for _ in range(sh)]
    for gi, (pix, _) in glyphs.items():
        if pix:
            r, c = divmod(gi, cols)
            x0, y0 = (cw + 1) * c + 1, (ch + 1) * r + 1
            for dy, row in enumerate(pix):
                for dx, v in enumerate(row):
                    grid[y0 + dy][x0 + dx] = v
    raw = grid[::-1]  # stored bottom row first
    if fmt == F.FMT_BC4:
        lin = bytearray()
        for by in range(sh // 4):
            for bx in range(sw // 4):
                lin += F.bc4_encode([raw[by * 4 + t // 4][bx * 4 + t % 4] for t in range(16)])
        wb, brows = sw // 4 * 8, sh // 4
    else:
        lin = bytearray(v for row in raw for v in row)
        wb, brows = sw, sh
    tiled = tegra.tile(bytes(lin), wb, brows, 1)
    bntx = bytearray(0x100)
    bntx[0:4] = b"BNTX"
    struct.pack_into("<Q", bntx, 0x28, 0x40)
    struct.pack_into("<Q", bntx, 0x40, 0x50)
    o = 0x50
    bntx[o:o + 4] = b"BRTI"
    struct.pack_into("<BBHHH", bntx, o + 0x10, 1, 2, 0, 0, 1)
    struct.pack_into("<IIIiiiiII", bntx, o + 0x18, 1, F.BNTX_FMT[fmt], 0x20, sw, sh, 1, 1, 0, 0)
    struct.pack_into("<II", bntx, o + 0x50, len(tiled), 0x200)
    struct.pack_into("<Q", bntx, o + 0x70, 0xD0)
    struct.pack_into("<Q", bntx, 0xD0, 0x100)
    bntx += tiled
    img = 0x200
    tglp = bytearray(b"TGLP" + bytes(4) + struct.pack("<BBBBIHHHHHHI", cw, ch, 1, cw, len(bntx), baseline, fmt,
                                                        cols, rows, sw, sh, img))
    tglp += bytes(img - 0x34 - len(tglp)) + bntx
    tglp += bytes(-len(tglp) % 4)
    struct.pack_into("<I", tglp, 4, len(tglp))
    n = max(glyphs) + 1 if glyphs else 1
    cwdh = bytearray(b"CWDH" + bytes(4) + struct.pack("<HHI", 0, n - 1, 0))
    for i in range(n):
        cwdh += struct.pack("<bBB", *(glyphs[i][1] if i in glyphs else (0, 0, 0)))
    cwdh += bytes(-len(cwdh) % 4)
    struct.pack_into("<I", cwdh, 4, len(cwdh))
    head_len = 0x14 + 0x20 + len(tglp)
    secs = []
    at = head_len + len(cwdh)
    for b in blocks or []:
        sec = bytearray(b"CMAP" + bytes(4) + struct.pack("<IIHHI", b["first"], b["last"], b["method"], 0, 0))
        if b["method"] == 0:
            sec += struct.pack("<H", b["index"])
        elif b["method"] == 1:
            sec += struct.pack(f"<{len(b['table'])}H", *b["table"])
        else:
            sec += struct.pack("<I", len(b["pairs"])) + b"".join(struct.pack("<II", *p) for p in b["pairs"])
        sec += bytes(-len(sec) % 4)
        struct.pack_into("<I", sec, 4, len(sec))
        secs.append((at, sec))
        at += len(sec)
    for i, (st, sec) in enumerate(secs):
        struct.pack_into("<I", sec, 20, secs[i + 1][0] + 8 if i + 1 < len(secs) else 0)
    finf = b"FINF" + struct.pack("<I", 0x20) + struct.pack("<BBBBHHbBBB", 1, ch, cw, baseline, ch, 0, 0, cw, cw, 1) \
        + struct.pack("<III", 0x34 + 8, head_len + 8, secs[0][0] + 8 if secs else 0)
    body = finf + bytes(tglp) + bytes(cwdh) + b"".join(bytes(s) for _, s in secs)
    head = b"FFNT" + b"\xff\xfe" + struct.pack("<HIIHH", 0x14, 0x04010000, 0x14 + len(body), 3 + len(secs), 0)
    return head + body


def bar(cw, ch, x0, x1, y0, y1, v=255):
    return [[v if x0 <= x < x1 and y0 <= y < y1 else 0 for x in range(cw)] for y in range(ch)]


def game_like(fmt=F.FMT_BC4, *, extra_scan=(), lacks="{}#$%@^`|~", kana=True) -> bytes:
    """A small 'game' font: every printable ASCII but `lacks` in a table block 0x20-0x7E (holes 0xFFFF), 'H' and 'l'
    with real stems, a kana in a direct block, and a scan block over everything."""
    glyphs = {0: (None, (3, 0, 3))}
    table = [0xFFFF] * 95
    gi = 1
    v = 255 if fmt == F.FMT_BC4 else 255
    for c in F.ASCII[1:]:
        if c in lacks:
            continue
        if c == "H":
            pix = [[v if (x < 1 or 5 <= x < 6 or y == 5) and 2 <= y < 8 else 0 for x in range(8)] for y in range(10)]
        elif c == "l":
            pix = bar(8, 10, 0, 1, 1, 8, v)
        else:
            pix = bar(8, 10, 0, 2, 2, 8, v)
        glyphs[gi] = (pix, (1, 6 if c == "H" else 2, 7))
        table[ord(c) - 0x20] = gi
        gi += 1
    table[0] = 0
    blocks = [{"first": 0x20, "last": 0x7E, "method": 1, "table": table}]
    if kana:
        glyphs[gi] = (bar(8, 10, 2, 6, 2, 6, v), (0, 8, 9))
        blocks.insert(0, {"first": 0x3042, "last": 0x3042, "method": 0, "index": gi})
        gi += 1
    pairs = sorted(extra_scan)
    blocks.append({"first": 0, "last": 0xFFFF, "method": 2, "pairs": pairs})
    return make_bffnt(fmt, glyphs=glyphs, blocks=blocks)


# ------------------------------------------------------------------------------------------------ SARC fixture

def sarc_hash(name: str) -> int:
    h = 0
    for c in name.encode():
        h = (h * 0x65 + c) & 0xFFFFFFFF
    return h


def make_sarc(files: list[tuple[str, bytes]], align: int = 0x100) -> bytes:
    files = sorted(files, key=lambda f: sarc_hash(f[0]))
    names = b""
    offs = []
    for n, _ in files:
        offs.append(len(names) // 4)
        names += n.encode() + b"\0"
        names += bytes(-len(names) % 4)
    head_len = 0x14 + 0x0C + 16 * len(files) + 8 + len(names)
    data_off = -(-head_len // align) * align
    data = bytearray()
    nodes = b""
    for (n, d), o in zip(files, offs):
        data += bytes(-len(data) % align)
        nodes += struct.pack("<IIII", sarc_hash(n), 0x01000000 | o, len(data), len(data) + len(d))
        data += d
    sfat = b"SFAT" + struct.pack("<HHI", 0x0C, len(files), 0x65) + nodes
    sfnt = b"SFNT" + struct.pack("<HH", 8, 0) + names
    head = b"SARC" + struct.pack("<H", 0x14) + b"\xff\xfe" + struct.pack("<IIHH", data_off + len(data), data_off,
                                                                          0x100, 0)
    return head + sfat + sfnt + bytes(data_off - head_len) + bytes(data)


def archive(**kw) -> bytes:
    """A Font_xx-like archive: files 0x1000-aligned as in the game's, so a few added bytes fit the padding."""
    return make_sarc([("Ancient_00.bffnt", b"ancient bytes"), ("Caption_00.bffnt", game_like(**kw)),
                      ("NormalS_00.bffnt", game_like(F.FMT_R8, lacks="")), ("Normal_00.bffnt", game_like(lacks="")),
                      ("External_00.bffnt", b"icons, never touched")], align=0x1000)


def tt(**kw) -> F.TrueType:
    return F.TrueType(make_ttf(**kw), "test.ttf")


# ================================================================================================= tests

class Yaz0AndSarc(unittest.TestCase):
    def test_stored_round_trip(self):
        data = bytes(range(256)) * 3 + b"tail"
        z = F.yaz0_stored(data, 0x1000)
        self.assertEqual(F.yaz0_decode(z), (data, 0x1000))

    def test_back_references(self):
        # "ab" stored, then a copy of 6 from distance 2 (overlapping) and a long copy (n = 0x12 + 1)
        body = bytes([0b11000000]) + b"ab" + bytes([0x40, 0x01]) + bytes([0x00, 0x07, 0x01])
        z = b"Yaz0" + struct.pack(">II", 27, 0) + bytes(4) + body
        self.assertEqual(F.yaz0_decode(z)[0], b"abababab" + b"abababababababababa")

    def test_cut_short_and_too_big(self):
        z = F.yaz0_stored(b"x" * 100)
        with self.assertRaises(F.FontError):
            F.yaz0_decode(z[:40])
        with self.assertRaises(F.FontError):
            F.yaz0_decode(b"Yaz0" + struct.pack(">II", F.MAX_ARCHIVE + 1, 0) + bytes(8))

    def test_rebuild_in_place_keeps_size_and_other_bytes(self):
        arc = make_sarc([("a.bin", b"A" * 10), ("b.bin", b"B" * 300), ("c.bin", b"C" * 5)])
        s = F.Sarc(arc)
        out = s.rebuild({"a.bin": b"Z" * 200})
        self.assertEqual(len(out), len(arc))
        t = F.Sarc(out)
        self.assertEqual(t.get("a.bin"), b"Z" * 200)
        self.assertEqual(t.get("b.bin"), b"B" * 300)
        self.assertEqual(t.get("c.bin"), b"C" * 5)

    def test_rebuild_grows_by_whole_alignment_steps(self):
        arc = make_sarc([("a.bin", b"A" * 10), ("b.bin", b"B" * 300), ("c.bin", b"C" * 5)])
        s = F.Sarc(arc)
        first = s.files[0][0]
        out = s.rebuild({first: b"Z" * 700})
        t = F.Sarc(out)
        self.assertEqual(t.get(first), b"Z" * 700)
        for name, _, start, _ in t.files:
            self.assertEqual((t.data_off + start) % 0x100, 0, name)
            if name != first:
                self.assertEqual(t.get(name), s.get(name))
        slot = s.files[1][2] - s.files[0][2]
        grew = len(out) - len(arc)
        self.assertEqual(grew % 0x100, 0)
        self.assertTrue(700 - slot <= grew < 700 - slot + 0x100)

    def test_refuses_big_endian(self):
        arc = bytearray(make_sarc([("a", b"x")]))
        arc[6:8] = b"\xfe\xff"
        with self.assertRaises(F.FontError):
            F.Sarc(bytes(arc))


class Bc4(unittest.TestCase):
    def test_decode_known_block(self):
        # e0 200 > e1 100: index 0 -> 200, 1 -> 100, 2 -> (6*200+100)/7 = 186 (rounded)
        bits = 0 | 1 << 3 | 2 << 6
        vals = F.bc4_decode(bytes((200, 100)) + bits.to_bytes(6, "little"))
        self.assertEqual(vals[:4], [200, 100, 186, 200])

    def test_six_value_mode_has_zero_and_255(self):
        bits = 6 | 7 << 3
        self.assertEqual(F.bc4_decode(bytes((10, 20)) + bits.to_bytes(6, "little"))[:2], [0, 255])

    def test_encode_exact_for_flat_and_extremes(self):
        for px in ([0] * 16, [77] * 16, [0, 255] * 8, [0] * 8 + [255] * 8):
            self.assertEqual(F.bc4_decode(F.bc4_encode(px)), px)

    def test_encode_keeps_zero_exact_next_to_coverage(self):
        px = [0, 31, 63, 255] * 4
        back = F.bc4_decode(F.bc4_encode(px))
        self.assertEqual([b for p, b in zip(px, back) if p == 0], [0] * 4)
        self.assertLessEqual(max(abs(a - b) for a, b in zip(px, back)), 8)


class TrueTypeReading(unittest.TestCase):
    def test_cmap_advance_and_cap(self):
        t = tt()
        self.assertTrue(t.has(ord("H")))
        self.assertFalse(t.has(0x3042))
        self.assertEqual(t.advance(t.cmap[ord("H")]), 600)
        self.assertEqual(t.cap_height(), CAP)

    def test_composite_moves_the_part(self):
        t = tt()
        lx = [x for c in t.contours(t.cmap[ord("l")]) for x, _, _ in c]
        ix = [x for c in t.contours(t.cmap[ord("i")]) for x, _, _ in c]
        self.assertEqual(min(ix) - min(lx), 40)

    def test_refuses_cff_and_collections(self):
        for head in (b"OTTO", b"ttcf"):
            with self.assertRaises(F.FontError):
                F.TrueType(head + bytes(100))

    def test_license(self):
        self.assertTrue(F.license_info(tt())["free"])
        info = F.license_info(tt(notice="All rights reserved", fstype=2))
        self.assertFalse(info["free"])
        self.assertEqual(len(info["warnings"]), 2)


class Drawing(unittest.TestCase):
    def test_raster_square_is_exact(self):
        cov = F.raster([[(1, 1), (3, 1), (3, 3), (1, 3)]], 4, 4)
        self.assertEqual([[round(v, 6) for v in r] for r in cov],
                         [[0, 0, 0, 0], [0, 1, 1, 0], [0, 1, 1, 0], [0, 0, 0, 0]])

    def test_raster_half_pixels_and_area(self):
        cov = F.raster([[(0.5, 0.5), (2.5, 0.5), (2.5, 1.5), (0.5, 1.5)]], 3, 3)
        self.assertAlmostEqual(sum(map(sum, cov)), 2.0, 6)
        self.assertAlmostEqual(cov[0][0], 0.25, 6)
        self.assertAlmostEqual(cov[1][1], 0.5, 6)

    def test_raster_clips_outside(self):
        cov = F.raster([[(-5, -5), (10, -5), (10, 10), (-5, 10)]], 3, 2)
        self.assertEqual([[round(v, 9) for v in r] for r in cov], [[1.0] * 3] * 2)

    def test_offset_bolder_and_thinner(self):
        sq = [[(0, 0), (0, 4), (4, 4), (4, 0)]]  # clockwise, y up: TrueType's outer direction
        self.assertAlmostEqual(abs(F._area(F.offset_polys(sq, 1)[0])), 36, 6)
        self.assertAlmostEqual(abs(F._area(F.offset_polys(sq, -1)[0])), 4, 6)

    def test_quadratic_contour_is_flattened(self):
        t = tt()
        polys = [F._flatten(c, 0.02) for c in t.contours(t.cmap[ord("o")])]
        self.assertTrue(all(len(p) > 8 for p in polys))


class BffntReading(unittest.TestCase):
    def test_codes_follow_first_matching_block(self):
        # 'A' is a hole in the table block: a later scan entry for it must not count (the table's range wins)
        bf = F.Bffnt(game_like(lacks="A", extra_scan=[(0x41, 0), (0x2603, 1)]))
        have = bf.codes()
        self.assertNotIn(0x41, have)
        self.assertIn(0x2603, have)
        self.assertIn(0x3042, have)
        self.assertEqual(bf.lacks("AB"), "A")

    def test_measure(self):
        bf = F.Bffnt(game_like())
        m = bf.measure()
        self.assertAlmostEqual(m["cap"], 6, 1)
        self.assertAlmostEqual(m["stem"], 1, 1)

    def test_outlined_fill(self):
        bf = F.Bffnt(game_like(F.FMT_R8, lacks=""))
        self.assertTrue(bf.outlined)
        self.assertEqual((bf.fill(128), bf.fill(255)), (0.0, 1.0))

    def test_refuses_other_sheet_formats(self):
        data = bytearray(game_like())
        struct.pack_into("<H", data, 0x34 + 8 + 10, 5)
        with self.assertRaises(F.FontError):
            F.Bffnt(bytes(data))


def parsed(arc: bytes, name: str) -> F.Bffnt:
    return F.Bffnt(F.Sarc(arc).get(name), name)


class FixSymbols(unittest.TestCase):
    def setUp(self):
        self.fira = tt()
        self.arc = archive()

    def build(self, arc=None, **kw):
        return F.build(arc or self.arc, F.Recipe(fix=self.fira, **kw))

    def test_adds_every_missing_ascii_and_nothing_else_changes(self):
        out, rep = self.build()
        self.assertEqual(rep["fonts"]["Caption_00"]["fixed"], "#$%@^`{|}~")
        self.assertTrue(rep["symbols_full"])
        a, b = parsed(self.arc, "Caption_00.bffnt"), parsed(out, "Caption_00.bffnt")
        self.assertEqual(b.lacks(), "")
        for c, gi in a.codes().items():  # the font's own glyphs: same cell texels, same widths, same mapping
            self.assertEqual(b.codes()[c], gi)
            self.assertEqual(b.read_cell(gi), a.read_cell(gi))
            self.assertEqual(b.widths[gi], a.widths[gi])
        old, new = F.Sarc(self.arc), F.Sarc(out)
        for name in ("Ancient_00.bffnt", "External_00.bffnt", "NormalS_00.bffnt", "Normal_00.bffnt"):
            self.assertEqual(new.get(name), old.get(name))

    def test_new_glyphs_have_ink_from_column_0_and_fit(self):
        out, _ = self.build()
        b = parsed(out, "Caption_00.bffnt")
        gi = b.codes()[ord("#")]
        cell = b.read_cell(gi)
        self.assertTrue(any(r[0] for r in cell))
        g = b.widths[gi]
        self.assertTrue(0 < g.width <= b.cw and g.advance > 0)

    def test_table_holes_patched_in_place(self):
        out, _ = self.build()
        b = parsed(out, "Caption_00.bffnt")
        self.assertEqual([x["method"] for x in b.blocks], [0, 1, 2])
        self.assertEqual(len(b.blocks[2]["pairs"]), 0)

    def test_codes_outside_tables_go_into_the_scan_block(self):
        # the table starts at '(' (as the game's Japanese caption font does): '"#$%&' go into the scan block, sorted
        font = make_bffnt(glyphs={0: (None, (3, 0, 3)), 1: (bar(8, 10, 0, 1, 1, 8), (1, 2, 7)),
                                  2: (bar(8, 10, 0, 6, 2, 8), (1, 6, 7))},
                          blocks=[{"first": 0x28, "last": 0x7E, "method": 1,
                                   "table": [1 if c in (ord("l"),) else 2 if c == ord("H") else 0xFFFF
                                             for c in range(0x28, 0x7F)]},
                                  {"first": 0, "last": 0xFFFF, "method": 2, "pairs": [(0x20, 0), (0x21, 1)]}])
        arc = make_sarc([("Caption_00.bffnt", font)])
        out, rep = self.build(arc)
        b = parsed(out, "Caption_00.bffnt")
        self.assertEqual(b.lacks(), "")
        pairs = b.blocks[1]["pairs"]
        self.assertEqual(pairs, sorted(pairs))
        self.assertEqual({c for c, _ in pairs}, {0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27})

    def test_code_no_block_covers_gets_a_new_scan_block(self):
        font = make_bffnt(glyphs={0: (None, (3, 0, 3)), 1: (bar(8, 10, 0, 1, 1, 8), (1, 2, 7))},
                          blocks=[{"first": 0x6C, "last": 0x6C, "method": 0, "index": 1}])
        out, _ = self.build(make_sarc([("Caption_00.bffnt", font)]))
        b = parsed(out, "Caption_00.bffnt")
        self.assertEqual(b.lacks(), "")
        self.assertEqual(b.blocks[-1]["method"], 2)

    def test_cells_sharing_a_block_with_ink_are_skipped(self):
        # the last glyph's ink touches the next cell's 4x4 blocks: that cell is not used
        pix = bar(8, 10, 0, 8, 0, 10)
        font = make_bffnt(glyphs={0: (None, (3, 0, 3)), 1: (pix, (0, 8, 9))},
                          blocks=[{"first": 0x20, "last": 0x21, "method": 0, "index": 0}])
        bf = F.Bffnt(font)
        self.assertFalse(bf.cell_is_free(2, set()))
        out, _ = self.build(make_sarc([("Caption_00.bffnt", font)]))
        b = parsed(out, "Caption_00.bffnt")
        self.assertNotIn(2, b.codes().values())
        self.assertEqual(b.read_cell(1), F.Bffnt(font).read_cell(1))

    def test_same_archive_size_when_padding_absorbs_it(self):
        packed = F.yaz0_stored(self.arc, 0x80)
        out, rep = F.build_archive(packed, F.Recipe(fix=self.fira), "US")
        self.assertIsNone(rep["rstb"])
        self.assertEqual(F.yaz0_decode(out)[1], 0x80)
        self.assertEqual(len(F.yaz0_decode(out)[0]), len(self.arc))

    def test_a_grown_archive_gets_a_size_entry(self):
        arc = make_sarc([("Caption_00.bffnt", game_like())], align=4)
        out, rep = F.build_archive(arc, F.Recipe(fix=self.fira), "EU")
        n = len(F.yaz0_decode(out)[0])
        self.assertGreater(n, len(arc))
        self.assertEqual(rep["rstb"], ["Font/Font_EU.bfarc", (n + 31) // 32 * 32 + 8192])

    def test_nothing_to_do(self):
        arc = make_sarc([("Caption_00.bffnt", game_like(lacks=""))])
        out, rep = self.build(arc)
        self.assertIsNone(out)

    def test_external_is_never_edited(self):
        out, _ = F.build(self.arc, F.Recipe(fix=self.fira, fonts=F.MENU_FONTS + ("External_00",)))
        self.assertEqual(F.Sarc(out).get("External_00.bffnt"), b"icons, never touched")

    def test_outlined_font_gets_the_ring(self):
        arc = make_sarc([("NormalS_00.bffnt", game_like(F.FMT_R8, lacks="~"))])
        out, _ = F.build(arc, F.Recipe(fix=self.fira, fonts=("NormalS_00",)))
        b = parsed(out, "NormalS_00.bffnt")
        vals = {v for r in b.read_cell(b.codes()[ord("~")]) for v in r}
        self.assertIn(128, vals)            # the dark ring
        self.assertTrue(max(vals) > 160)    # the letter inside it


class Restyle(unittest.TestCase):
    def test_redraws_what_the_font_has_and_keeps_the_rest(self):
        arc = archive()
        out, rep = F.build(arc, F.Recipe(font=tt(), font_key="easy-read"))
        a, b = parsed(arc, "Caption_00.bffnt"), parsed(out, "Caption_00.bffnt")
        kana = a.codes()[0x3042]
        self.assertEqual(b.read_cell(kana), a.read_cell(kana))         # the font has no kana: the game's stays
        self.assertNotEqual(b.read_cell(a.codes()[ord("o")]), a.read_cell(a.codes()[ord("o")]))
        self.assertEqual(b.lacks(), "")                                # its own '{' etc. were added
        self.assertTrue(rep["symbols_full"])
        self.assertGreater(rep["fonts"]["Normal_00"]["redrawn"], 90)

    def test_font_lacking_symbols_plus_fix(self):
        arc = archive()
        out, rep = F.build(arc, F.Recipe(font=tt(missing="{}"), fix=tt()))
        cap = rep["fonts"]["Caption_00"]
        self.assertEqual(cap["fixed"], "{}")
        self.assertEqual(parsed(out, "Caption_00.bffnt").lacks(), "")

    def test_without_fix_symbols_full_is_false(self):
        _, rep = F.build(archive(), F.Recipe(font=tt(missing="~")))
        self.assertEqual(rep["fonts"]["Caption_00"]["lacks"], "~")
        self.assertFalse(rep["symbols_full"])

    def test_descender_is_squashed_into_the_cell(self):
        bf = F.Bffnt(game_like())
        st = F.style_for(bf, tt(), False)
        d = F.draw(bf, tt(), ord("g"), st)
        self.assertFalse(d.clipped)
        self.assertTrue(d.squeezed)

    def test_stems_matched_for_fix(self):
        bf = F.Bffnt(game_like())
        st = F.style_for(bf, tt(), True)
        self.assertGreater(st.thin, 0)


# ------------------------------------------------------------------------------------------------ library side

class Lib(SimpleNamespace):
    @property
    def mods_dir(self):
        return os.path.join(self.root, "mods")


def font_mod(root: str, mid: str, *, enabled=True, toml_extra="", file_ok=True) -> dict:
    d = os.path.join(root, "mods", mid)
    os.makedirs(os.path.join(d, "fonts"), exist_ok=True)
    with open(os.path.join(d, "mod.toml"), "w", encoding="utf-8") as f:
        f.write(f'[mod]\nid = "{mid}"\nname = "Font {mid}"\nversion = "1.0"\n\n[font]\nfile = "fonts/F.ttf"\n'
                + toml_extra)
    if file_ok:
        with open(os.path.join(d, "fonts", "F.ttf"), "wb") as f:
            f.write(make_ttf())
    return {"id": mid, "name": f"Font {mid}", "enabled": enabled}


class Settings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = Lib(root=self.tmp.name, install=None, mods=[])

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_and_save(self):
        self.assertEqual(F.load_settings(self.lib), {"choice": "auto", "fix_symbols": F.FIX_SYMBOLS_DEFAULT})
        F.save_settings(self.lib, choice="easy-read", fix_symbols=True)
        self.assertEqual(F.load_settings(self.lib), {"choice": "easy-read", "fix_symbols": True})
        with open(os.path.join(self.tmp.name, F.SETTINGS), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["tool"], F.TOOL_ID)

    def test_damaged_file_gives_defaults(self):
        with open(os.path.join(self.tmp.name, F.SETTINGS), "w") as f:
            f.write("{nope")
        self.assertEqual(F.load_settings(self.lib)["choice"], "auto")

    def test_auto_picks_the_last_font_mod_that_is_on(self):
        self.lib.mods = [font_mod(self.tmp.name, "a"), font_mod(self.tmp.name, "b"),
                         font_mod(self.tmp.name, "c", enabled=False)]
        pick, notes = F.current(self.lib)
        self.assertEqual(pick.key, "mod:b")
        keys = [c.key for c in F.choices(self.lib)]
        self.assertEqual(keys, ["game", "easy-read", "mod:a", "mod:b"])

    def test_player_choice_wins_and_falls_back(self):
        self.lib.mods = [font_mod(self.tmp.name, "a"), font_mod(self.tmp.name, "b")]
        F.save_settings(self.lib, choice="mod:a")
        self.assertEqual(F.current(self.lib)[0].key, "mod:a")
        self.lib.mods[0]["enabled"] = False
        pick, notes = F.current(self.lib)
        self.assertEqual(pick.key, "mod:b")
        self.assertTrue(notes)
        F.save_settings(self.lib, choice="game")
        self.assertEqual(F.current(self.lib)[0].label, F.GAME_FONT)

    def test_broken_font_mod_is_not_offered(self):
        self.lib.mods = [font_mod(self.tmp.name, "a", file_ok=False)]
        self.assertEqual([c.key for c in F.choices(self.lib)], ["game", "easy-read"])
        self.assertTrue(F.font_mods(self.lib)[0].problems)

    def test_check_font_table(self):
        clean, probs, notes = F.check_font_table({"file": "fonts/A.ttf", "for": "all", "languages": ["us", "KR"],
                                                  "x": 1})
        self.assertEqual(probs, [])
        self.assertEqual(clean["languages"], ["US"])
        self.assertEqual(len(notes), 3)
        for bad in ({}, {"file": "../x.ttf"}, {"file": "C:/x.ttf"}, {"file": "a.png"}, {"file": "a.ttf", "for": "x"},
                    {"file": "a.ttf", "languages": ["ZZ"]}, {"file": "a.ttf", "license": 3}):
            self.assertTrue(F.check_font_table(bad)[1], bad)
        self.assertTrue(F.check_font_table({"file": "a.ttf"}, files={"b.ttf"})[1])
        self.assertFalse(F.check_font_table({"file": "a.ttf"}, files={"Wrap/a.ttf"}, base_rel="Wrap/")[1])

    def test_labels_are_short_ascii(self):
        self.assertEqual(F._ascii_label("Caf\u00e9 font with a very long name indeed"), "Caf? font with a very lon...")


class Deploy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.game = os.path.join(root, "game", "romfs")
        os.makedirs(os.path.join(self.game, "Font"))
        for lang in F.LANGS:
            with open(os.path.join(self.game, "Font", f"Font_{lang}.sbfarc"), "wb") as f:
                f.write(F.yaz0_stored(archive(), 0x80))
        self.lib = Lib(root=os.path.join(root, "lib"), install=None, mods=[])
        os.makedirs(self.lib.root)
        self.fira = os.path.join(root, "fira.ttf")
        with open(self.fira, "wb") as f:
            f.write(make_ttf())
        self.p = mock.patch.object(F, "fira_path", lambda file, install=None: self.fira)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_nothing_chosen_nothing_built(self):
        gen = F.deploy_files(self.lib, None, game_romfs=self.game)
        self.assertEqual(list(gen), [])
        self.assertFalse(gen.symbols_full)
        self.assertEqual(gen.font, F.GAME_FONT)

    def test_fix_symbols_for_every_language(self):
        F.save_settings(self.lib, fix_symbols=True)
        gen = F.deploy_files(self.lib, None, game_romfs=self.game)
        self.assertEqual([g.path for g in gen], [f"Font/Font_{x}.sbfarc" for x in F.LANGS])
        self.assertTrue(gen.symbols_full)
        self.assertTrue(all(g.rstb is None for g in gen))
        self.assertEqual(gen.owner, "generated:fonts")
        more = gen + [F.GenFile("Pack/Title.pack", b"", None)]  # (other jobs' files join the list)
        self.assertEqual(len(more), 4)

    def test_second_deploy_reads_the_cache(self):
        F.save_settings(self.lib, choice="easy-read", fix_symbols=True)
        first = F.deploy_files(self.lib, None, game_romfs=self.game)
        with mock.patch.object(F, "build_archive", side_effect=AssertionError("built again")):
            second = F.deploy_files(self.lib, None, game_romfs=self.game)
        self.assertEqual([g.data for g in first], [g.data for g in second])
        self.assertEqual(second.font, F.EASY_READ)
        self.assertTrue(second.symbols_full)

    def test_builds_over_the_winning_mod_archive(self):
        F.save_settings(self.lib, fix_symbols=True)
        mod_file = os.path.join(self.tmp.name, "mod_Font_US.sbfarc")
        with open(mod_file, "wb") as f:
            f.write(F.yaz0_stored(archive(lacks="~"), 0x80))
        entry = SimpleNamespace(data=None, src=mod_file, zname="")
        plan = SimpleNamespace(winners={"Font/Font_US.sbfarc": (0, entry)}, mods=[SimpleNamespace(name="Big font")])
        gen = F.deploy_files(self.lib, plan, game_romfs=self.game)
        us = next(g for g in gen if g.path.endswith("_US.sbfarc"))
        b = F.Bffnt(F.Sarc(F.yaz0_decode(us.data)[0]).get("Caption_00.bffnt"))
        self.assertEqual(b.lacks(), "")
        self.assertEqual(len(F.Bffnt(game_like(lacks="~")).codes()) + 1, len(b.codes()))

    def test_a_font_mod_for_some_languages(self):
        self.lib.mods = [font_mod(self.lib.root, "f", toml_extra='languages = ["US"]\n')]
        gen = F.deploy_files(self.lib, None, game_romfs=self.game)
        self.assertEqual([g.path for g in gen], ["Font/Font_US.sbfarc"])
        self.assertEqual(gen.font_key, "mod:f")
        self.assertFalse(gen.symbols_full)

    def test_missing_archive_is_named(self):
        os.remove(os.path.join(self.game, "Font", "Font_JP.sbfarc"))
        F.save_settings(self.lib, fix_symbols=True)
        gen = F.deploy_files(self.lib, None, game_romfs=self.game)
        self.assertEqual(len(gen), 2)
        self.assertFalse(gen.symbols_full)
        self.assertTrue(any("Font_JP" in a for a in gen.attention))


class CommandLine(unittest.TestCase):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = F.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_build_and_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "Font_US.sbfarc")
            with open(src, "wb") as f:
                f.write(F.yaz0_stored(archive()))
            fira = os.path.join(tmp, "fira.ttf")
            with open(fira, "wb") as f:
                f.write(make_ttf())
            with mock.patch.object(F, "fira_path", lambda file, install=None: fira):
                code, out, _ = self.run_main(["build", src, "--out", os.path.join(tmp, "o.sbfarc"), "--fix-symbols"])
            self.assertEqual(code, 0, out)
            self.assertIn("no size-table entry needed", out)
            png = os.path.join(tmp, "p.png")
            code, _, _ = self.run_main(["preview", os.path.join(tmp, "o.sbfarc"), "--out", png, "--text", "a{b}"])
            self.assertEqual(code, 0)
            with open(png, "rb") as f:
                data = f.read()
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(zlib.crc32(data[12:29]) & 0xFFFFFFFF, struct.unpack(">I", data[29:33])[0])

    def test_build_refuses_external_and_nothing_to_do(self):
        code, _, err = self.run_main(["build", "x.sbfarc", "--out", "y", "--fix-symbols", "--fonts", "External_00"])
        self.assertEqual(code, 1)
        code, _, err = self.run_main(["build", __file__, "--out", "y"])
        self.assertEqual(code, 1)
        self.assertIn("nothing to do", err)

    def test_build_refuses_the_game_folder(self):
        with self.assertRaises(F.FontError):
            F._guard_out(os.path.join("C:\\SW", "extracted", "romfs", "Font", "x"), "C:\\SW")

    def test_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "t.ttf")
            with open(p, "wb") as f:
                f.write(make_ttf(missing="~"))
            code, out, _ = self.run_main(["check", p])
        self.assertEqual(code, 0)
        self.assertIn("lacks ~", out)


class BundledFira(unittest.TestCase):
    def test_bundled_copies_draw_all_of_ascii(self):
        for name in (F.FIRA_MEDIUM, F.FIRA_SEMIBOLD):
            t = F.load_truetype(os.path.join(F.ASSETS, name))
            self.assertEqual([c for c in F.ASCII if not t.has(ord(c)) and c != " "], [])
            self.assertTrue(F.license_info(t)["free"])
        self.assertTrue(os.path.isfile(os.path.join(F.ASSETS, F.FIRA_LICENSE)))


if __name__ == "__main__":
    unittest.main()
