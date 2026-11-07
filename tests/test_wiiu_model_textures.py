"""Tests for tools/wiiu_model (milestone M1: textures). All data is made up here in code (random or patterned
blocks, invented texture names): no game files."""
import os
import random
import struct
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import wiiu_model as wm  # noqa: E402
from wiiu_model import (bc, checks, convert, dicts, formats, gx2, nx_bfres, nx_bntx, rlt, tegra,  # noqa: E402
                        wiiu_bfres)
from wiiu_model.errors import Damaged, Unsupported  # noqa: E402
from wiiu_model.neutral import Texture, TextureSet, describe, diff  # noqa: E402

RND = random.Random(1234)
BC1, BC1_SRGB, BC3, BC4, BC5, RGBA8, R8 = 0x1A01, 0x1A06, 0x1C01, 0x1D01, 0x1E01, 0x0B06, 0x0201


def chain(fmt, w, h, mips=None, seed=0):
    rnd = random.Random(seed)
    if mips is None:
        mips = max(w, h).bit_length()
    return [rnd.randbytes(formats.level_size(fmt, w, h, m)) for m in range(mips)]


def smooth_chain(fmt, w, h, mips=None, seed=0):
    """Made-up levels for the reader's mip check: the per-block value bc.mip_coherence reads (BC1-3 colour endpoints,
    BC4/5 first endpoints, else the first byte) is random at the smallest level and copied with +-1 noise to the
    four children at each larger level, so every mip is a smaller copy of the one above and a misplaced block shows.
    Every other byte is random."""
    rnd = random.Random(seed)
    if mips is None:
        mips = max(w, h).bit_length()
    layout = fmt >> 8
    bpb = formats.block_info(fmt)[2]
    out, vals, pdims = [], None, None
    for m in reversed(range(mips)):
        bx, by, _ = formats.level_dims(fmt, w, h, m)
        if vals is None:
            vals = [rnd.randrange(32) for _ in range(bx * by)]
        else:
            pbx, pby = pdims
            vals = [min(31, max(0, vals[min(y // 2, pby - 1) * pbx + min(x // 2, pbx - 1)] + rnd.randrange(-1, 2)))
                    for y in range(by) for x in range(bx)]
        pdims = (bx, by)
        lv = bytearray(rnd.randbytes(bx * by * bpb))
        for i, v in enumerate(vals):
            o = i * bpb
            if layout in (0x1A, 0x1B, 0x1C):
                c = (v << 11) | ((2 * v) << 5) | v
                struct.pack_into("<HH", lv, o + (0 if layout == 0x1A else 8), c, c)
            elif layout in (0x1D, 0x1E):
                lv[o] = lv[o + 1] = v * 8
            else:
                lv[o] = v * 8
        out.append(bytes(lv))
    return out[::-1]


def made_up_set(name="Test_Model"):
    t = [
        Texture("Test_Alb", BC1_SRGB, 128, 64, smooth_chain(BC1_SRGB, 128, 64, seed=1)),
        Texture("Test_Nrm", BC5, 64, 64, smooth_chain(BC5, 64, 64, seed=2), channels=(2, 3, 1, 1), swizzle_hint=2),
        Texture("Test_Spm", BC4, 96, 40, smooth_chain(BC4, 96, 40, 4, seed=3), swizzle_hint=3),
        Texture("Test_Emm", BC3, 36, 20, smooth_chain(BC3, 36, 20, seed=4), channels=(2, 2, 2, 2), swizzle_hint=7),
        Texture("Test_Mask", R8, 30, 18, smooth_chain(R8, 30, 18, 3, seed=5)),
        Texture("Test_Icon", RGBA8, 40, 24, smooth_chain(RGBA8, 40, 24, 1, seed=6)),
    ]
    return TextureSet(name, t, "model")


class TilerTests(unittest.TestCase):
    SIZES = [(1, 1), (2, 3), (5, 4), (7, 9), (8, 8), (9, 16), (15, 17), (17, 15), (31, 33), (33, 32),
             (64, 63), (65, 20), (100, 3), (127, 9), (129, 5)]

    def test_gx2_fast_matches_reference(self):
        for bpp in (8, 16, 32, 64, 128):
            for mode in (1, 2, 4, 5, 6):
                for i, (w, h) in enumerate(self.SIZES):
                    sw = (i + bpp + mode) % 8
                    pa, ha, _ = gx2.alignments(mode, bpp)
                    pitch, ph = gx2.roundup(w, pa), gx2.roundup(h, ha)
                    size = pitch * ph * bpp // 8
                    lin = RND.randbytes(w * h * bpp // 8)
                    tiled = gx2.tile(lin, w, h, bpp, pitch, mode, sw, size)
                    with self.subTest(bpp=bpp, mode=mode, w=w, h=h, sw=sw):
                        if w * h <= 300:
                            self.assertEqual(tiled, gx2.tile_ref(lin, w, h, bpp, pitch, mode, sw, size))
                            self.assertEqual(gx2.untile_ref(tiled, w, h, bpp, pitch, mode, sw), lin)
                        self.assertEqual(gx2.untile(tiled, w, h, bpp, pitch, mode, sw), lin)

    def test_gx2_all_swizzles_and_large(self):
        for sw in range(8):
            for bpp in (64, 128):
                w, h = 300, 40
                pitch = gx2.roundup(w, 32)
                size = pitch * gx2.roundup(h, 16) * bpp // 8
                lin = RND.randbytes(w * h * bpp // 8)
                t = gx2.tile(lin, w, h, bpp, pitch, 4, sw, size)
                self.assertEqual(gx2.untile(t, w, h, bpp, pitch, 4, sw), lin)
        w, h = 1000, 7
        lin = RND.randbytes(w * h * 8)
        pitch = gx2.roundup(w, 32)
        t = gx2.tile(lin, w, h, 64, pitch, 4, 3, pitch * 16 * 8)
        self.assertEqual(gx2.untile(t, w, h, 64, pitch, 4, 3), lin)

    def test_gx2_macro_tiling_is_a_bijection(self):
        for bpp in (8, 32, 64, 128):
            for mode in (2, 4, 5, 6):
                pa, ha, _ = gx2.alignments(mode, bpp)
                pitch, height = pa * 2, ha * 2
                bpe = bpp // 8
                addrs = {gx2.ref_addr(x, y, bpp, pitch, mode, 6) // bpe for y in range(height) for x in range(pitch)}
                self.assertEqual(addrs, set(range(pitch * height)), (bpp, mode))

    def test_gx2_address_formula_pins(self):
        # Hand-derived from the addrlib formula (2 pipes, 4 banks): 64 bpp, 2D thin1, pitch 64.
        a = lambda x, y, sw=0: gx2.ref_addr(x, y, 64, 64, 4, sw)  # noqa: E731
        self.assertEqual(a(0, 0), 0)
        self.assertEqual(a(1, 0), 8)          # x0 is element-index bit 0
        self.assertEqual(a(0, 1), 16)         # y0 is element-index bit 1
        self.assertEqual(a(8, 0), 0x300)      # x3 -> pipe 1, bank bit 0
        self.assertEqual(a(0, 8), 0x100)      # y3 -> pipe 1
        self.assertEqual(a(32, 0), 0x1000)    # next macro tile: 4096 bytes / 8, bits above 7 moved up 3
        self.assertEqual(a(0, 0, 5), 0x500)   # pipe/bank swizzle 5 -> pipe 1, bank 2
        self.assertEqual(gx2.ref_addr(9, 0, 64, 64, 2, 0), 512 + 8)  # 1D: second micro tile, element 1

    def test_tegra_fast_matches_reference(self):
        for wb in (1, 3, 8, 16, 24, 40, 64, 72, 136, 200):
            for rows in (1, 2, 7, 8, 9, 17, 33):
                for bh in (1, 2, 4, 8, 16):
                    lin = RND.randbytes(wb * rows)
                    t = tegra.tile(lin, wb, rows, bh)
                    with self.subTest(wb=wb, rows=rows, bh=bh):
                        if wb * rows <= 600:
                            self.assertEqual(t, tegra.tile_ref(lin, wb, rows, bh))
                        self.assertEqual(tegra.untile(t, wb, rows, bh), lin)
                        self.assertEqual(len(t), tegra.level_size(wb, rows, bh))

    def test_tegra_address_pins(self):
        self.assertEqual(tegra.ref_addr(16, 0, 64, 1), 32)
        self.assertEqual(tegra.ref_addr(0, 1, 64, 1), 16)
        self.assertEqual(tegra.ref_addr(32, 0, 64, 1), 256)
        self.assertEqual(tegra.ref_addr(0, 2, 64, 1), 64)
        self.assertEqual(tegra.ref_addr(0, 8, 64, 2), 512)
        self.assertEqual(tegra.ref_addr(64, 0, 128, 2), 1024)
        self.assertEqual(tegra.ref_addr(0, 16, 128, 2), 2048)


class LayoutTests(unittest.TestCase):
    def test_tegra_mip_layout_by_hand(self):
        # 128x128 BC1, block height 4 GOBs: level 0 is 256 bytes x 32 rows = 4x4 GOBs (8192 bytes); level 1 drops
        # to bh 2 (16 rows) and starts at 8192; levels 2.. are one GOB each, 512-aligned.
        lay, size = tegra.mip_layout(BC1, 128, 128, 8, 2)
        self.assertEqual([o for o, _, _ in lay], [0, 8192, 10240, 10752, 11264, 11776, 12288, 12800])
        self.assertEqual([b for _, _, b in lay], [4, 2, 1, 1, 1, 1, 1, 1])
        self.assertEqual(size, 13312)

    def test_tegra_game_block_height(self):
        self.assertEqual([tegra.game_block_height_log2(r) for r in (1, 8, 9, 16, 17, 64, 65, 128, 129, 4096)],
                         [0, 0, 1, 1, 2, 3, 4, 4, 4, 4])

    def test_gx2_first_1d_level_and_pitch(self):
        # Surface parameters of public Cemu captures (wiiu_swizzle test fixture names): size, format, pitch,
        # swizzle bits 16+.
        for fmt, w, h, mips, pitch, first in ((BC1, 256, 256, 8, 64, 2), (BC3, 1024, 256, 11, 256, 3),
                                              (BC1, 64, 64, 2, 32, 1), (BC1, 1024, 1024, 1, 256, 13)):
            lay = gx2.surface_layout(fmt, w, h, mips)
            self.assertEqual((lay.pitch, lay.first_1d_level), (pitch, first), (fmt, w, h))

    def test_gx2_layout_is_consistent(self):
        for fmt in (BC1, BC3, BC4, BC5, RGBA8, R8):
            for w, h in ((2048, 2048), (1024, 512), (100, 60), (33, 17), (4, 4), (1, 1), (256, 8)):
                mips = max(w, h).bit_length()
                lay = gx2.surface_layout(fmt, w, h, mips)
                self.assertEqual(lay.alignment, lay.levels[0].base_align)
                if mips > 1:
                    self.assertGreaterEqual(lay.mip_offsets[0], lay.image_size)
                    self.assertEqual(lay.mip_offsets[0] % lay.levels[1].base_align, 0)
                end = 0
                for m in range(1, mips):
                    start = 0 if m == 1 else lay.mip_offsets[m - 1]
                    self.assertGreaterEqual(start, end)
                    self.assertEqual(start % lay.levels[m].base_align, 0)
                    end = start + lay.levels[m].size
                self.assertEqual(end, lay.mip_size)
                modes = [lv.mode for lv in lay.levels]
                self.assertEqual(modes, sorted(modes, reverse=True))  # 2D levels never come back after 1D

    def test_mip_slices(self):
        self.assertEqual(gx2.mip_slices(100, 4, [999, 40, 70] + [0] * 10), [(0, 40), (40, 70), (70, 100)])
        self.assertEqual(gx2.mip_slices(50, 2, [0] * 13), [(0, 50)])
        with self.assertRaises(Damaged):
            gx2.mip_slices(50, 3, [0, 80] + [0] * 11)


class DictAndRelocTests(unittest.TestCase):
    def test_dict_lookup_every_key(self):
        sets = [["a"], ["abc", "abd", "ab", "abcd"], [f"tex_{i:03d}" for i in range(200)],
                ["_a0", "_n0", "_s0", "_a1", "x", "xx", "xxx"], [f"name{i}" for i in range(1, 30)]]
        for keys in sets:
            enc = [k.encode() for k in keys]
            nodes = dicts.build(enc)
            self.assertEqual(len(nodes), len(keys) + 1)
            for i, k in enumerate(enc):
                self.assertEqual(dicts.lookup(nodes, enc, k), i)
            self.assertEqual(dicts.lookup(nodes, enc, b"missing!"), -1)

    def test_dict_duplicate_key_refused(self):
        with self.assertRaises(ValueError):
            dicts.build([b"a", b"a"])

    def test_rlt_round_trip_and_strides(self):
        pos = {0x20, 0x30, 0x40, 0x50, 0x98, 0xA0, 0xB0, 0x110, 0x120} | {0x1000 + 0x5C * 8 * j + 8 * k
                                                                        for j in range(5) for k in range(3)}
        ents = rlt.encode(pos)
        self.assertEqual(sorted(rlt.decode(ents)), sorted(pos))
        self.assertIn((0x1000, 5, 3, 0x5C - 3), ents)
        self.assertIn((0x20, 4, 1, 1), ents)
        blob = rlt.build(0x2000, [(0, 0x200), (0x1000, 0x1000)], [(p, 8) for p in pos if p < 0x1000]
                         + [(p, 0x1008) for p in pos if p >= 0x1000])
        buf = bytes(0x2000) + blob
        secs, per = rlt.parse(buf, 0x2000)
        self.assertEqual(secs, [(0, 0x200), (0x1000, 0x1000)])
        self.assertEqual(sorted(per[0] + per[1]), sorted(pos))


class SwitchTests(unittest.TestCase):
    def test_switch_write_read(self):
        ts = made_up_set()
        data = wm.write_textures_switch(ts)
        self.assertEqual(wm.sniff(data), "switch")
        self.assertEqual(checks.switch_tex_problems(data), [])
        back = wm.read_textures_switch(data)
        self.assertEqual(back.name, ts.name)
        self.assertEqual(back.kind, "model")
        self.assertEqual(diff(ts, back, "cross"), [])
        self.assertEqual(wm.write_textures_switch(back), data)  # deterministic, extras reproduce the file

    def test_bntx_layout_rules(self):
        ts = made_up_set()
        data = wm.write_textures_switch(ts)
        h = nx_bfres.read_header(data)
        self.assertEqual(h["name"], "Test_Model.Tex")
        self.assertEqual([(n, o) for n, o, _ in h["files"]], [("textures.bntx", 0x1000)])
        self.assertEqual(struct.unpack_from("<I", data, 0x18)[0] % 0x1000, 0)
        b = data[0x1000:]
        brtd = struct.unpack_from("<Q", b, 0x30)[0]
        self.assertEqual((brtd + 0x10) % 0x1000, 0)
        self.assertEqual(struct.unpack_from("<I", b, 0x18)[0] % 0x1000, 0)

    def test_string_order_kept(self):
        ts = made_up_set()
        ts.extra["nx.string_order"] = ["Test_Spm", "textures"] + [t.name for t in ts.textures if t.name != "Test_Spm"]
        data = wm.write_textures_switch(ts)
        back = wm.read_textures_switch(data)
        self.assertEqual(back.extra["nx.string_order"], ts.extra["nx.string_order"])
        self.assertEqual(wm.write_textures_switch(back), data)
        self.assertEqual(checks.switch_tex_problems(data), [])

    def test_block_height_extra(self):
        ts = made_up_set()
        ts.textures[0].extra["nx.block_height_log2"] = 0
        back = wm.read_textures_switch(wm.write_textures_switch(ts))
        self.assertEqual(back.textures[0].extra["nx.block_height_log2"], 0)
        self.assertEqual(back.textures[0].levels, ts.textures[0].levels)

    def test_item_icon(self):
        ts = TextureSet("Made_Up_Icon.bitemico", [Texture("Made_Up_Icon", RGBA8, 72, 72, chain(RGBA8, 72, 72, 1))],
                        "single")
        data = wm.write_textures_switch(ts)
        self.assertEqual(nx_bfres.read_header(data)["name"], "Made_Up_Icon.bitemico")
        back = wm.read_textures_switch(data)
        self.assertEqual(back.kind, "single")
        self.assertEqual(diff(ts, back, "cross"), [])

    def test_self_check_finds_problems(self):
        data = bytearray(wm.write_textures_switch(made_up_set()))
        b = 0x1000
        o = b + struct.unpack_from("<Q", data, b + struct.unpack_from("<Q", data, b + 0x28)[0])[0]
        struct.pack_into("<Q", data, o + 0x80, 0x7FFFFF)  # run-time texture pointer outside its section
        self.assertTrue(any("outside its section" in p for p in checks.switch_tex_problems(bytes(data))))
        short = bytearray(wm.write_textures_switch(made_up_set()))
        struct.pack_into("<I", short, 0x1C, len(short) + 8)
        self.assertTrue(any("size field" in p for p in checks.switch_tex_problems(bytes(short))))

    def test_verify_check_file_on_made_up_set(self):
        from wiiu_model import verify
        r = verify.check_file("made_up.Tex", wm.write_textures_switch(made_up_set()))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["mip_check"], {"checked": 4, "low": []})
        self.assertEqual(r["wiiu_warnings"], [])
        self.assertTrue(r["v1_identical"] and r["v2_identical_to_original"])
        self.assertIn("1/1 files pass", verify.text({"files": [r], "speed": {}, "samples": False}))

    def test_switch_refusals(self):
        ts = made_up_set()
        data = bytearray(wm.write_textures_switch(ts))
        b = 0x1000
        o = b + struct.unpack_from("<Q", data, b + struct.unpack_from("<Q", data, b + 0x28)[0])[0]
        cube = bytearray(data)
        cube[o + 0x5C] = 3  # view dimension cube
        with self.assertRaises(Unsupported):
            wm.read_textures_switch(bytes(cube))
        fmt = bytearray(data)
        struct.pack_into("<I", fmt, o + 0x1C, 0x2001)  # BC7
        with self.assertRaises(Unsupported):
            wm.read_textures_switch(bytes(fmt))
        with self.assertRaises(Damaged):
            wm.read_textures_switch(bytes(data[:0x1100]))


class WiiUTests(unittest.TestCase):
    def test_wiiu_write_read(self):
        ts = made_up_set()
        tex1, tex2 = wm.write_textures_wiiu(ts)
        self.assertEqual(wm.sniff(tex1), "wiiu")
        self.assertEqual(wiiu_bfres.self_check(tex1), [])
        self.assertEqual(wiiu_bfres.self_check(tex2), [])
        warn = []
        back = wm.read_textures_wiiu(tex1, tex2, warn)
        self.assertEqual(warn, [])
        self.assertEqual(back.name, "Test_Model")
        self.assertEqual(diff(ts, back, "cross"), [])
        self.assertEqual(back.textures[0].extra["wiiu.tile_mode"], 4)
        h = wiiu_bfres.read_header(tex1)
        self.assertEqual((h["name"], h["version"]), ("Test_Model.Tex1", 0x04050003))
        self.assertEqual(wiiu_bfres.read_header(tex2)["name"], "Test_Model.Tex2")

    def test_tex1_tex2_split(self):
        ts = made_up_set()
        tex1, tex2 = wm.write_textures_wiiu(ts)
        _, f1 = wiiu_bfres.read_ftex_list(tex1)
        _, f2 = wiiu_bfres.read_ftex_list(tex2)
        self.assertEqual({f.role for f in f1}, {"tex1", "full"})
        self.assertEqual([f.name for f in f2], [t.name for t in ts.textures if t.mips > 1])
        self.assertTrue(all(f.role == "tex2" and f.image_size == 0 for f in f2))
        for f in f1:
            self.assertEqual(f.swizzle >> 16, gx2.surface_layout(formats.nx_format(f.format), f.width, f.height,
                                                                 f.mips).first_1d_level)

    def test_cross_paths(self):
        ts = made_up_set()
        sw = wm.write_textures_switch(ts)
        t1, t2 = wm.write_textures_wiiu(wm.read_textures_switch(sw))
        sw2 = wm.write_textures_switch(wm.read_textures_wiiu(t1, t2))
        self.assertEqual(checks.switch_tex_problems(sw2), [])
        self.assertEqual(diff(ts, wm.read_textures_switch(sw2), "cross"), [])
        self.assertEqual(sw2, sw)  # nothing Switch-only in the made-up set, so the files match byte for byte

    def test_single_file_icon(self):
        ts = TextureSet("Made_Up_Icon", [Texture("Made_Up_Icon", BC1, 64, 64, chain(BC1, 64, 64))], "single")
        f, none = wm.write_textures_wiiu(ts)
        self.assertIsNone(none)
        _, ft = wiiu_bfres.read_ftex_list(f)
        self.assertEqual(ft[0].role, "full")
        back = wm.read_textures_wiiu(f)
        self.assertEqual(back.kind, "single")
        self.assertEqual(diff(ts, back, "cross"), [])

    def test_tex2_missing_gives_one_mip(self):
        ts = made_up_set()
        tex1, _ = wm.write_textures_wiiu(ts)
        warn = []
        back = wm.read_textures_wiiu(tex1, None, warn)
        self.assertEqual([t.mips for t in back.textures], [1] * len(ts.textures))
        self.assertTrue(any("Tex2 data missing" in w for w in warn))
        for a, b in zip(ts.textures, back.textures):
            self.assertEqual(a.levels[0], b.levels[0])

    def test_tex2_only_refused(self):
        _, tex2 = wm.write_textures_wiiu(made_up_set())
        with self.assertRaises(Unsupported):
            wm.read_textures_wiiu(tex2)

    def test_wiiu_refusals(self):
        ts = TextureSet("T", [Texture("T_Alb", BC1, 64, 64, chain(BC1, 64, 64))], "model")
        tex1, tex2 = wm.write_textures_wiiu(ts)
        _, (f,) = wiiu_bfres.read_ftex_list(tex1)
        o = tex1.index(b"FTEX")
        for off, val, err in ((0x04, 3, Unsupported),      # cube map
                              (0x18, 0x01F, Unsupported),   # unknown GX2 format
                              (0x34, 7, Unsupported),       # 2D thick
                              (0x1C, 2, Unsupported)):      # 4x MSAA  (a wrong first 1D level: a warning now)
            bad = bytearray(tex1)
            struct.pack_into(">I", bad, o + off, val)
            with self.subTest(off=off), self.assertRaises(err):
                wm.read_textures_wiiu(bytes(bad), tex2)
        with self.assertRaises(Damaged):
            wm.read_textures_wiiu(tex1[:len(tex1) - 64], tex2)

    def test_mip_swizzle_choice(self):
        # Smooth made-up BC1 image (endpoints follow a gradient), mips = 2x2 averages, so a right untiling of
        # level 1 correlates with level 0 and a wrong one does not.
        w = h = 256

        def level(m):
            bx, by = (w >> m) // 4, (h >> m) // 4
            out = bytearray()
            for y in range(by):
                for x in range(bx):
                    v = int(31 * (0.5 + 0.5 * __import__("math").sin((x << m) / 9 + (y << m) / 13)))
                    c = (v << 11) | ((2 * v) << 5) | v
                    out += struct.pack("<HHI", c, c, 0)
            return bytes(out)

        tex = Texture("Smooth", BC1, w, h, [level(m) for m in range(4)], swizzle_hint=1)
        ts = TextureSet("S", [tex], "model")
        e = wiiu_bfres.texture_entry(tex)
        tex1 = wiiu_bfres._write_file("S.Tex1", [e], "tex1", 0x2000)
        for real in (6, 1):
            lay, sw, _, mip = gx2.tile_texture(tex.levels, BC1, w, h, real)
            e2 = dict(e, swizzle=(6 << 8) | (lay.first_1d_level << 16), mip=mip)
            tex2 = wiiu_bfres._write_file("S.Tex2", [e2], "tex2", 0x2000)
            warn = []
            back = wm.read_textures_wiiu(tex1, tex2, warn)
            with self.subTest(real=real):
                self.assertEqual(back.textures[0].levels, tex.levels)
                self.assertEqual(back.textures[0].extra["wiiu.mip_swizzle_choice"], "Tex2" if real == 6 else "Tex1")
                self.assertTrue(any("swizzles differ" in x for x in warn))
        self.assertGreater(bc.mip_coherence(tex.levels[0], tex.levels[1], BC1, w, h), 0.9)
        del ts


class ConvertTests(unittest.TestCase):
    def test_convert_with_and_without_vanilla_mips(self):
        ts = made_up_set()
        tex1, tex2 = wm.write_textures_wiiu(ts)
        notes = []
        out, conv = convert.textures_to_switch(tex1, tex2, None, notes)
        self.assertEqual(notes, [])
        want = made_up_set()
        for t in want.textures:   # the BNTX swizzle field follows the texture's role (no vanilla file here)
            t.swizzle_hint = convert.switch_swizzle_default(t.name)
        self.assertEqual(diff(want, wm.read_textures_switch(out), "cross"), [])
        # Tex2 missing: Test_Alb unchanged from "vanilla" keeps its mips, Test_Nrm changed in the mod gets one mip.
        vanilla = made_up_set()
        vanilla.textures[1].levels[0] = bytes(len(vanilla.textures[1].levels[0]))
        notes = []
        out, conv = convert.textures_to_switch(tex1, None, wm.write_textures_switch(vanilla), notes)
        got = {t.name: t for t in wm.read_textures_switch(out).textures}
        self.assertEqual(got["Test_Alb"].levels, ts.textures[0].levels)
        self.assertEqual(got["Test_Nrm"].mips, 1)
        self.assertTrue(any("vanilla mipmaps kept" in n for n in notes))
        self.assertTrue(any("no mipmaps" in n and "Test_Nrm" in n for n in notes))
        self.assertFalse(any(n.startswith("Test_Alb:") for n in notes), notes)   # its 'no mipmaps' warning is dropped
        self.assertTrue(any(n.startswith("Test_Nrm: Tex2 data missing") for n in notes), notes)
        self.assertEqual(checks.switch_tex_problems(out), [])


class WiiUGuardTests(unittest.TestCase):
    """Regression tests of the review fixes of milestone M1 (made-up data; each names the review case it covers)."""

    @staticmethod
    def one(fmt=BC1, w=256, h=256, hint=5, seed=1):
        tex = Texture("A", fmt, w, h, smooth_chain(fmt, w, h, seed=seed), swizzle_hint=hint)
        ts = TextureSet("T", [tex], "model")
        tex1, tex2 = wm.write_textures_wiiu(ts)
        return ts, tex1, tex2

    @staticmethod
    def at(data, i=0):
        return wiiu_bfres.read_ftex_list(data)[1][i].offset

    @staticmethod
    def single(tex):
        return wm.write_textures_wiiu(TextureSet("Made_Up_Icon", [tex], "single"))[0]

    # finding 1: the Tex1/Tex2 role comes from the FRES name (BfresLibrary), not from the FTEX content
    def test_role_from_file_name(self):
        self.assertEqual([wiiu_bfres.file_kind(n) for n in ("A.Tex1", "A.Tex2", "A", "A.Tex", "A.Tex1.x")],
                         ["tex1", "tex2", None, None, None])
        ts, tex1, tex2 = self.one()
        self.assertEqual([(f.kind, f.role) for f in wiiu_bfres.read_ftex_list(tex1)[1]], [("tex1", "tex1")])
        self.assertEqual([(f.kind, f.role) for f in wiiu_bfres.read_ftex_list(tex2)[1]], [("tex2", "tex2")])
        with self.assertRaises(Unsupported):
            wm.read_textures_wiiu(tex2)            # a Tex2 given as the main file
        with self.assertRaises(Unsupported):
            wm.read_textures_wiiu(tex1, tex1)      # a Tex1 given as the mipmap file
        icon = self.single(ts.textures[0])         # other names: 0xB0 image, 0xB4 mips (guessed from the content)
        self.assertEqual([(f.kind, f.role) for f in wiiu_bfres.read_ftex_list(icon)[1]], [(None, "full")])
        self.assertEqual(wm.read_textures_wiiu(icon).textures[0].levels, ts.textures[0].levels)

    def test_tex1_mip_data_offset_is_ignored(self):
        # review X6: Tex1's 0xB4 points at its own image. Before: 8 garbage mips taken from Tex1, Tex2 ignored, no
        # warning.
        ts, tex1, tex2 = self.one()
        o = self.at(tex1)
        bad = bytearray(tex1)
        image = o + 0xB0 + struct.unpack_from(">i", bad, o + 0xB0)[0]
        struct.pack_into(">i", bad, o + 0xB4, image - (o + 0xB4))
        warn = []
        back = wm.read_textures_wiiu(bytes(bad), tex2, warn)
        self.assertEqual(back.textures[0].levels, ts.textures[0].levels)
        self.assertTrue(any("A: Tex1 entry has a mip data offset; ignored" in w for w in warn), warn)
        self.assertTrue(any("mip data offset" in p for p in wiiu_bfres.self_check(bytes(bad))))

    def test_tex2_image_size_is_ignored(self):
        # Before: a Tex2 FTEX with a non-zero image size was skipped ('has its own main image') and every mip lost.
        ts, tex1, tex2 = self.one()
        bad = bytearray(tex2)
        struct.pack_into(">I", bad, self.at(tex2) + 0x24, 0x8000)
        warn = []
        back = wm.read_textures_wiiu(tex1, bytes(bad), warn)
        self.assertEqual(back.textures[0].levels, ts.textures[0].levels)
        self.assertTrue(any("A: Tex2 entry has an image size (0x8000); ignored" in w for w in warn), warn)

    # finding 2: a null (0) offset is 'missing', never 'the start of the file'
    def test_null_tex2_data_offset_means_mips_missing(self):
        # review X1: Tex2's 0xB0 is 0 and its data is at 0xB4. Before: mips decoded from the FRES header, no warning.
        ts, tex1, tex2 = self.one()
        o = self.at(tex2)
        bad = bytearray(tex2)
        data = o + 0xB0 + struct.unpack_from(">i", bad, o + 0xB0)[0]
        struct.pack_into(">ii", bad, o + 0xB0, 0, data - (o + 0xB4))
        warn = []
        t = wm.read_textures_wiiu(tex1, bytes(bad), warn).textures[0]
        self.assertEqual(t.levels, ts.textures[0].levels[:1])
        self.assertEqual(t.extra["wiiu.mips_missing"], 9)
        self.assertTrue(any("no data offset (0xB0)" in w and w.endswith(wiiu_bfres.NO_MIPS) for w in warn), warn)

    def test_null_image_offset_is_damaged(self):
        ts, tex1, tex2 = self.one()
        for data, other in ((tex1, tex2), (self.single(ts.textures[0]), None)):
            bad = bytearray(data)
            struct.pack_into(">i", bad, self.at(data) + 0xB0, 0)
            with self.subTest(other=other is not None), self.assertRaises(Damaged):
                wm.read_textures_wiiu(bytes(bad), other)

    # finding 3: the mip check runs on every texture and shows in the convert notes
    def test_mip_check_on_every_texture(self):
        # Mips tiled with pipe/bank swizzle 5 while Tex1 and Tex2 both say 1 (so no swizzle choice is made). Before:
        # decoded wrong with no warning.
        tex = Texture("A", BC1, 256, 256, smooth_chain(BC1, 256, 256, seed=3), swizzle_hint=1)
        e = wiiu_bfres.texture_entry(tex)
        mip5 = gx2.tile_texture(tex.levels, BC1, 256, 256, 5)[3]
        tex1 = wiiu_bfres._write_file("T.Tex1", [e], "tex1", 0x2000)
        tex2 = wiiu_bfres._write_file("T.Tex2", [dict(e, mip=mip5)], "tex2", 0x2000)
        notes = []
        _, conv = convert.textures_to_switch(tex1, tex2, None, notes)
        self.assertLess(conv.textures[0].extra["wiiu.mip_coherence"], wiiu_bfres.COHERENCE_WARN)
        self.assertTrue(any(n.startswith("A: mip 1 does not look like a smaller copy of mip 0") for n in notes), notes)
        warn = []
        good = wm.read_textures_wiiu(*wm.write_textures_wiiu(TextureSet("T", [tex], "model")), warn)
        self.assertGreater(good.textures[0].extra["wiiu.mip_coherence"], 0.9)
        self.assertEqual(warn, [])
        # too few blocks at mip 1 (here 2x2): the number is noted but never warned about
        small = Texture("S", BC1, 16, 16, chain(BC1, 16, 16, seed=8))
        warn = []
        back = wm.read_textures_wiiu(*wm.write_textures_wiiu(TextureSet("T", [small], "model")), warn)
        self.assertIn("wiiu.mip_coherence", back.textures[0].extra)
        self.assertEqual(warn, [])

    def test_mip_check_measure(self):
        # The sampled check (at most 16x16 blocks) is the full correlation of block_values when the mip is small
        # enough, stays close to it on a large one, and costs about the same for every size.
        def full(l0, l1, fmt, w, h):
            bx0, by0, _ = formats.level_dims(fmt, w, h, 0)
            bx1, by1, _ = formats.level_dims(fmt, w, h, 1)
            v0, v1 = bc.block_values(l0, fmt), bc.block_values(l1, fmt)
            cw, ch = min(bx1, bx0 // 2), min(by1, by0 // 2)
            down = [v0[2 * y * bx0 + 2 * x] + v0[2 * y * bx0 + 2 * x + 1] + v0[(2 * y + 1) * bx0 + 2 * x]
                    + v0[(2 * y + 1) * bx0 + 2 * x + 1] for y in range(ch) for x in range(cw)]
            return bc.correlation(down, [v1[y * bx1 + x] for y in range(ch) for x in range(cw)])

        for fmt in (BC1, BC3, BC4, BC5, RGBA8, R8):
            for w, h in ((120, 72), (36, 20)):
                lv = smooth_chain(fmt, w, h, 2, seed=fmt)
                lv[1] = chain(fmt, w, h, 2, seed=1)[1] if w == 36 else lv[1]   # one unrelated pair
                with self.subTest(fmt=hex(fmt), w=w):
                    self.assertAlmostEqual(bc.mip_coherence(lv[0], lv[1], fmt, w, h, max_side=10 ** 6),
                                           full(lv[0], lv[1], fmt, w, h), places=9)
        lv = smooth_chain(BC1, 1024, 512, 2, seed=7)
        self.assertAlmostEqual(bc.mip_coherence(lv[0], lv[1], BC1, 1024, 512),
                               full(lv[0], lv[1], BC1, 1024, 512), delta=0.05)

    # finding 4: swizzle bits 16+ are a warning when reading (the untiler never uses them), an error in self_check
    def test_first_1d_level_field_is_a_warning(self):
        # review R2: bits 16+ = 13 on a texture whose mips drop to 1D at mip 2. Before: the whole file was refused.
        ts, tex1, tex2 = self.one()
        o = self.at(tex1)
        bad = bytearray(tex1)
        sw = struct.unpack_from(">I", bad, o + 0x38)[0]
        struct.pack_into(">I", bad, o + 0x38, (13 << 16) | (sw & 0xFFFF))
        warn = []
        back = wm.read_textures_wiiu(bytes(bad), tex2, warn)
        self.assertEqual(back.textures[0].levels, ts.textures[0].levels)
        self.assertTrue(any("the swizzle field says mip 13" in w and "gives 2" in w for w in warn), warn)
        self.assertTrue(any("swizzle says mip 13" in p for p in wiiu_bfres.self_check(bytes(bad))))

    # finding 5: mip offsets must increase
    def test_mip_offsets_must_increase(self):
        offs = [0x8000, 0x2000, 0x2800, 0x2A00, 0x2C00, 0x2E00, 0x3000, 0x3200] + [0] * 5
        self.assertEqual(gx2.mip_slices(0x3400, 9, offs)[:3], [(0, 0x2000), (0x2000, 0x2800), (0x2800, 0x2A00)])
        for i, v in ((1, 0), (2, 0), (3, 0x2000), (7, 0x3000)):
            bad = list(offs)
            bad[i] = v
            with self.subTest(i=i), self.assertRaises(Damaged):
                gx2.mip_slices(0x3400, 9, bad)
        # review X2: offsets[2] = 0 in Tex1 and Tex2. Before: mip 3 read mip 1's bytes, no warning. Now that texture
        # loses its mips with a warning; in a single file it is Damaged.
        ts, tex1, tex2 = self.one()
        holed = []
        for data in (tex1, tex2, self.single(ts.textures[0])):
            b = bytearray(data)
            o = self.at(data)
            offs2 = list(struct.unpack_from(">13I", b, o + 0x44))
            offs2[2] = 0
            struct.pack_into(">13I", b, o + 0x44, *offs2)
            holed.append(bytes(b))
        warn = []
        back = wm.read_textures_wiiu(holed[0], holed[1], warn)
        self.assertEqual(back.textures[0].levels, ts.textures[0].levels[:1])
        self.assertTrue(any("out of order" in w for w in warn), warn)
        with self.assertRaises(Damaged):
            wm.read_textures_wiiu(holed[2])

    # finding 6: the user's vanilla file only helps; a texture (or file) this reader refuses does not stop the mod
    def test_vanilla_problems_do_not_stop_the_conversion(self):
        ts = made_up_set()
        tex1, _ = wm.write_textures_wiiu(ts)
        van = made_up_set()
        van.textures.append(Texture("Z_Array", BC1, 64, 64, smooth_chain(BC1, 64, 64, seed=9)))
        vb = bytearray(wm.write_textures_switch(van))
        b = 0x1000
        arr = struct.unpack_from("<Q", vb, b + 0x28)[0]
        for i in (1, len(van.textures) - 1):          # Test_Nrm (needed) and Z_Array (not needed): array length 2
            struct.pack_into("<i", vb, b + struct.unpack_from("<Q", vb, b + arr + 8 * i)[0] + 0x30, 2)
        with self.assertRaises(Unsupported):
            wm.read_textures_switch(bytes(vb))        # (review R1: this used to refuse the whole conversion)
        notes = []
        out, _ = convert.textures_to_switch(tex1, None, bytes(vb), notes)
        got = wm.read_textures_switch(out).by_name()
        self.assertEqual(got["Test_Alb"].levels, ts.textures[0].levels)   # vanilla mips still used
        self.assertEqual(got["Test_Nrm"].mips, 1)
        self.assertTrue(any("vanilla textures not usable" in n and "Test_Nrm" in n and "Z_Array" not in n
                            for n in notes), notes)
        notes = []
        out, _ = convert.textures_to_switch(tex1, None, bytes(vb[:0x1100]), notes)   # a cut-short vanilla file
        self.assertTrue(any(n.startswith("vanilla texture file not usable") for n in notes), notes)
        self.assertEqual(checks.switch_tex_problems(out), [])

    # finding 7: one stale Tex2 entry costs only that texture its mips
    def test_stale_tex2_entry(self):
        # review X4: Tex1 has A 256 and B 128, the Tex2 is from a set where B is 64. Before: Damaged, A lost too.
        a = Texture("A", BC1, 256, 256, smooth_chain(BC1, 256, 256, seed=1))
        b128 = Texture("B", BC1, 128, 128, smooth_chain(BC1, 128, 128, seed=2))
        b64 = Texture("B", BC1, 64, 64, smooth_chain(BC1, 64, 64, seed=2))
        ta1, _ = wm.write_textures_wiiu(TextureSet("T", [a, b128], "model"))
        _, tb2 = wm.write_textures_wiiu(TextureSet("T", [a, b64], "model"))
        warn = []
        back = wm.read_textures_wiiu(ta1, tb2, warn).by_name()
        self.assertEqual(back["A"].levels, a.levels)
        self.assertEqual(back["B"].levels, b128.levels[:1])
        self.assertEqual(back["B"].extra["wiiu.mips_missing"], 8)
        self.assertTrue(any(w.startswith("B: Tex2 entry not usable (Tex2 entry describes a different texture (64x64")
                            for w in warn), warn)

    # finding 8: a texture file with no textures is refused
    def test_empty_texture_file(self):
        # review X5: before, a valid-looking Switch X.Tex with 0 textures was written.
        empty = wiiu_bfres._write_file("E.Tex1", [], "tex1", 0x1000)
        with self.assertRaises(Unsupported):
            wm.read_textures_wiiu(empty)
        with self.assertRaises(Unsupported):
            convert.textures_to_switch(empty)

    # finding 9: the Tex1/Tex2 swizzle comparison only when it can matter, without untiling level 0 again
    def test_mip_swizzle_comparison_only_when_it_matters(self):
        tex = Texture("A", BC1, 64, 64, smooth_chain(BC1, 64, 64, seed=4), swizzle_hint=1)
        self.assertEqual(gx2.surface_layout(BC1, 64, 64, 7).first_1d_level, 1)   # every mip is 1D-tiled
        e = wiiu_bfres.texture_entry(tex)
        tex1 = wiiu_bfres._write_file("S.Tex1", [e], "tex1", 0x2000)
        tex2 = wiiu_bfres._write_file("S.Tex2", [dict(e, swizzle=(6 << 8) | (1 << 16))], "tex2", 0x2000)
        warn = []
        back = wm.read_textures_wiiu(tex1, tex2, warn)
        self.assertEqual(back.textures[0].levels, tex.levels)
        self.assertEqual(warn, [])
        self.assertNotIn("wiiu.mip_swizzle_choice", back.textures[0].extra)
        self.assertEqual((back.textures[0].extra["wiiu.mip_swizzle"] >> 8) & 7, 6)
        # Flat data: both swizzles decode the same, so no warning; level 0 is untiled once.
        flat = Texture("F", BC1, 256, 256, [bytes(formats.level_size(BC1, 256, 256, m)) for m in range(9)])
        e = wiiu_bfres.texture_entry(flat)
        tex1 = wiiu_bfres._write_file("F.Tex1", [e], "tex1", 0x2000)
        tex2 = wiiu_bfres._write_file("F.Tex2", [dict(e, swizzle=(6 << 8) | (2 << 16))], "tex2", 0x2000)
        warn = []
        with mock.patch.object(gx2, "untile_texture", wraps=gx2.untile_texture) as spy:
            back = wm.read_textures_wiiu(tex1, tex2, warn)
        self.assertEqual(back.textures[0].levels, flat.levels)
        self.assertEqual(warn, [])
        self.assertEqual([c.kwargs.get("first", 0) for c in spy.call_args_list], [0, 1, 1])

    # finding 10: the writer is test-only; the reader follows the file's offsets, including GX2's padding
    def test_reader_takes_gx2_padded_mip_offsets(self):
        # review X3: GX2 puts (swizzle & 0xFFFF) bytes before the first 1D level when it is above 1; our writer does
        # not (gx2.surface_layout says so).
        tex = Texture("A", BC1, 256, 256, smooth_chain(BC1, 256, 256, seed=5), swizzle_hint=5)
        lay = gx2.surface_layout(BC1, 256, 256, 9)
        e = wiiu_bfres.texture_entry(tex)
        pad, first = e["swizzle"] & 0xFFFF, lay.first_1d_level
        self.assertEqual((pad, first), (0x500, 2))
        cut = lay.mip_offsets[first - 1]
        mip = e["mip"][:cut] + bytes(pad) + e["mip"][cut:]
        offs = [x + pad if i >= first - 1 and x else x for i, x in enumerate(lay.mip_offsets)]
        self.assertNotEqual(offs, e["mip_offsets"])
        e.update(mip=mip, mip_size=len(mip), mip_offsets=offs)
        tex1 = wiiu_bfres._write_file("T.Tex1", [e], "tex1", 0x2000)
        tex2 = wiiu_bfres._write_file("T.Tex2", [e], "tex2", 0x2000)
        warn = []
        back = wm.read_textures_wiiu(tex1, tex2, warn)
        self.assertEqual(back.textures[0].levels, tex.levels)
        self.assertEqual(warn, [])
        self.assertIn("for tests only", wiiu_bfres.write_textures.__doc__.lower())

    # finding 11: tile mode 16 (linear special) with mips is refused (its mip layout is not known)
    def test_linear_special_with_mips_refused(self):
        many = Texture("L", RGBA8, 100, 60, smooth_chain(RGBA8, 100, 60, 3), extra={"wiiu.tile_mode": 16})
        with self.assertRaises(Unsupported):
            wm.read_textures_wiiu(*wm.write_textures_wiiu(TextureSet("T", [many], "model")))
        with self.assertRaises(Unsupported):
            gx2.untile_texture(RGBA8, 100, 60, 3, gx2.LINEAR_SPECIAL, 0, 100, b"", b"", [0] * 13)
        one = Texture("L", RGBA8, 100, 60, smooth_chain(RGBA8, 100, 60, 1), extra={"wiiu.tile_mode": 16})
        t1, t2 = wm.write_textures_wiiu(TextureSet("T", [one], "model"))
        self.assertIsNone(t2)
        self.assertEqual(wm.read_textures_wiiu(t1).textures[0].levels, one.levels)

    # finding 12: the Switch BNTX swizzle field comes from the vanilla twin or the texture's role
    def test_switch_swizzle_field(self):
        self.assertEqual([convert.switch_swizzle_default(n) for n in
                          ("Made_Up_Hood_Alb.14", "Made_Up_Belt_A_Nrm", "Made_Up_Hair_Spm", "Made_Up_X_f00", "Nrm")],
                         [0, 2, 3, 3, 2])
        ts = made_up_set()
        tex1, tex2 = wm.write_textures_wiiu(ts)
        out, conv = convert.textures_to_switch(tex1, tex2)
        self.assertEqual({t.name: t.swizzle_hint for t in wm.read_textures_switch(out).textures},
                         {"Test_Alb": 0, "Test_Nrm": 2, "Test_Spm": 3, "Test_Emm": 3, "Test_Mask": 3, "Test_Icon": 3})
        self.assertEqual((conv.by_name()["Test_Emm"].extra["wiiu.swizzle"] >> 8) & 7, 7)  # Wii U value kept in extra
        van = made_up_set()
        van.textures[3].swizzle_hint = 1                # Test_Emm's vanilla twin
        out, _ = convert.textures_to_switch(tex1, tex2, wm.write_textures_switch(van))
        got = {t.name: t.swizzle_hint for t in wm.read_textures_switch(out).textures}
        self.assertEqual((got["Test_Emm"], got["Test_Mask"]), (1, 0))


class NeutralTests(unittest.TestCase):
    def test_describe_and_diff(self):
        a, b = made_up_set(), made_up_set()
        b.textures[2].extra["nx.block_height_log2"] = 3
        b.textures[2].extra["wiiu.pitch"] = 32
        self.assertEqual(diff(a, b, "cross"), [])
        self.assertEqual(describe(a, "cross"), describe(b, "cross"))
        self.assertTrue(diff(a, b, "same"))
        self.assertEqual(describe(a, "same", "nx"), describe(made_up_set(), "same", "nx"))
        lv = bytearray(b.textures[0].levels[1])
        lv[5] ^= 1
        b.textures[0].levels[1] = bytes(lv)
        self.assertEqual(diff(a, b, "cross"), ["textures[0].levels[1] @0x5 (sizes 1024/1024)"])

    def test_texture_check(self):
        t = Texture("x", BC1, 16, 16, [bytes(128)])
        t.check()
        with self.assertRaises(ValueError):
            Texture("x", BC1, 16, 16, [bytes(127)]).check()

    def test_formats(self):
        for gx, nx in formats.GX2_TO_NX.items():
            self.assertEqual(formats.gx2_format(formats.nx_format(gx)), gx)
            self.assertIn(nx >> 8, formats.LAYOUT_BLOCK)
        with self.assertRaises(Unsupported):
            formats.nx_format(0x01F)
        for ch in ((2, 3, 4, 5), (2, 3, 1, 1), (0, 1, 5, 2)):
            self.assertEqual(formats.channels_from_gx2(formats.channels_to_gx2(ch)), ch)


class SpeedTests(unittest.TestCase):
    def test_tilers_are_practical(self):
        lv = chain(BC1, 512, 512, 1)
        gx2._index.cache_clear()
        tegra._index.cache_clear()
        t0 = time.perf_counter()
        lay, sw, img, mip = gx2.tile_texture(lv, BC1, 512, 512, 0)
        back = gx2.untile_texture(BC1, 512, 512, 1, 4, sw, lay.pitch, img, None, lay.mip_offsets)
        nx = tegra.tile_texture(lv, BC1, 512, 512, 4)
        self.assertEqual(tegra.untile_texture(nx, BC1, 512, 512, 1, 4), lv)
        self.assertEqual(back, lv)
        self.assertLess(time.perf_counter() - t0, 2.0)  # 4 x 128 KB; ~0.01 s on the dev machine


if __name__ == "__main__":
    unittest.main()
