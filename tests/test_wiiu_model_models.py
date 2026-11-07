"""Tests for tools/wiiu_model (milestone M2: models). All data is made up here in code (invented names, random or
patterned numbers): no game files."""
import copy
import random
import struct
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import wiiu_model as wm  # noqa: E402
from wiiu_model import (checks, dicts, formats, materials, nx_model, rlt, verify_models, wiiu_anims,  # noqa: E402
                        wiiu_models)
from wiiu_model.errors import Damaged, ModelError, Unsupported  # noqa: E402
from wiiu_model.neutral import (AnimCurve, Attribute, Bone, BoneAnim, ExtFile, MatAnim, MatAnimData,  # noqa: E402
                                Material, Mesh, Model, Param, RenderInfo, ResFile, Sampler, SamplerState, Shape,
                                ShaderAssign, SkelAnim, Skeleton, UserData, VertexBuffer, diff)


# --------------------------------------------------------------------------------------------- made-up models

def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def render_state_infos(mode="opaque", face="front", blend="none", write="true", alpha="false", ref=0.5):
    vals = {"gsys_render_state_mode": mode, "gsys_render_state_display_face": face,
            "gsys_render_state_blend_mode": blend, "gsys_depth_test_enable": "true", "gsys_depth_test_write": write,
            "gsys_depth_test_func": "lequal", "gsys_color_blend_rgb_src_func": "src_alpha",
            "gsys_color_blend_rgb_dst_func": "one_minus_src_alpha", "gsys_color_blend_rgb_op": "add",
            "gsys_color_blend_alpha_src_func": "one", "gsys_color_blend_alpha_dst_func": "zero",
            "gsys_color_blend_alpha_op": "add", "gsys_alpha_test_enable": alpha, "gsys_alpha_test_func": "gequal"}
    out = [RenderInfo(n, "string", (vals[n],)) for n, _ in materials.RENDER_STATE_INFOS[:14]]
    out.append(RenderInfo("gsys_color_blend_const_color", "float", (0.0, 0.25, 0.5, 1.0)))
    out.append(RenderInfo("gsys_alpha_test_value", "float", (f32(ref),)))
    return out


def sampler(name, wrap=(0, 0, 2), bias=0.0, aniso=1, border=0, filt=0x29, max_lod=13.0):
    return Sampler(name, SamplerState(*wrap, 0, border, aniso, filt, 0.0, max_lod, bias))


def params(rnd, alpha_ref=0.5):
    ps = [Param("p_uint", 8, 0, 4, 0, 0), Param("p_float", 12, 4, 4, 1, 1), Param("p_float4", 15, 8, 16, 2, 2),
          Param(materials.ALPHA_REF_PARAM, 12, 24, 4, 3, 3), Param("p_srt", 30, 28, 24, 4, 4)]
    data = bytearray(rnd.randbytes(52))
    struct.pack_into("<f", data, 24, alpha_ref)
    return ps, bytes(data)


def vertex_buffer(rnd, nvtx, spec, skin=0):
    """spec: list of buffers, each a list of (name, format); offsets are packed, strides rounded up to 4."""
    attrs, strides, bufs = [], [], []
    for b, items in enumerate(spec):
        off = 0
        for name, fmt in items:
            attrs.append(Attribute(name, fmt, off, b))
            off += sum(formats.attr_component_sizes(fmt))
        stride = -(-off // 4) * 4
        strides.append(stride)
        bufs.append(rnd.randbytes(stride * nvtx))
    return VertexBuffer(nvtx, skin, attrs, strides, bufs)


def indices(rnd, n, nvtx, fmt="u16"):
    tc = {"u8": "B", "u16": "H", "u32": "I"}[fmt]
    return struct.pack(f"<{n}{tc}", *[rnd.randrange(nvtx) for _ in range(n)])


def bounds(rnd, n):
    return [tuple(f32(rnd.uniform(-10, 10)) for _ in range(6)) for _ in range(n)]


def make_model(rnd, name="TestBody", big=False):
    bones = [Bone("root", -1, 0, -1, 0xFFFF, 0x1001, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
             Bone("arm", 0, 1, -1, 0xFFFF, 0x00801001, (1.0, 1.0, 1.0), (0.5, 0.0, 0.0, 1.0), (1.0, 2.0, 3.0),
                  [UserData("bone_tag", "int", (7, -7))]),
             Bone("hand", 1, -1, 2, 0xFFFF, 0x01001001, (2.0, 2.0, 2.0), (0.0, 0.25, 0.0, 1.0), (0.0, 1.0, 0.0))]
    inv = struct.pack("<24f", *[f32(rnd.uniform(-2, 2)) for _ in range(24)])
    skel = Skeleton(0x1200, bones, [0, 1, 2], 2, 1, inv)
    n0 = 300 if big else 24
    vb0 = vertex_buffer(rnd, n0, [[("_p0", 0x1505), ("_n0", 0x0E02), ("_t0", 0x0B02)],
                                  [("_u0", 0x1201), ("_u1", 0x1705)], [("_i0", 0x0B03), ("_w0", 0x0B01)]], skin=2)
    vb1 = vertex_buffer(rnd, 10, [[("_p0", 0x1805), ("_c0", 0x1505), ("_u0", 0x1205), ("_x0", 0x1405),
                                   ("_y0", 0x0A01), ("_z0", 0x0201), ("_i0", 0x0903), ("_s0", 0x1202),
                                   ("_q0", 0x1203), ("_r0", 0x1504), ("_v0", 0x1803), ("_g0", 0x1905)]])
    m0a = Mesh("triangles", "u16", 36, indices(rnd, 36, n0), 0, [(0, 24), (48, 12)])
    m0b = Mesh("triangles", "u16", 12, indices(rnd, 12, n0), 0, [(0, 12)])
    sh0 = Shape("root__matA", 2, 0, 0, 0, [0, 1], 2, 0, [m0a, m0b], bounds(rnd, 5), [f32(1.5), f32(0.75)])
    m1 = Mesh("triangle_strip", "u32", 9, indices(rnd, 9, 10, "u32"), 0, [(0, 9)])
    sh1 = Shape("hand__matB", 6, 1, 2, 1, [], 0, 0, [m1], bounds(rnd, 2), [f32(2.0)])
    ps, pdata = params(rnd)
    mat_a = Material("matA", 1, [RenderInfo("test_pass", "string", ("no_setting",)),
                                 RenderInfo("test_priority", "int", (3,)), RenderInfo("test_scale", "float", (1.5,)),
                                 RenderInfo("test_empty", "string", ())] + render_state_infos(),
                     ShaderAssign("test_archive", "test_model", 0, [("_p0", "_p0"), ("_u0", "_u0")],
                                  [("_a0", "_a0"), ("_n0", "_n0")], [("opt_a", "1"), ("opt_b", "0"), ("opt_c", "x")]),
                     [sampler("_a0"), sampler("_n0", (1, 1, 2), -1.0, 4, 1)], ["TestTex_Alb", "TestTex_Nrm"],
                     ps, pdata, [1], [UserData("HideGroup", "string", ("G_A", "G_B"))])
    ps_b, pdata_b = params(rnd, 0.25)
    mat_b = Material("matB", 1, render_state_infos("translucent", "both", "color", "false", "true", 0.25),
                     ShaderAssign("test_archive", "test_model", 0, [("_p0", "_p0")], [], [("opt_a", "0")]),
                     [], [], ps_b, pdata_b, [])
    ud = [UserData("ints", "int", (1, -2, 3)), UserData("floats", "float", (0.5,)),
          UserData("raw", "bytes", b"\x01\x02\x03")]
    return Model(name, "", skel, [vb0, vb1], [sh0, sh1], [mat_a, mat_b], ud, n0 + 10)


def make_small_model(rnd, name="TestProp"):
    bones = [Bone("root", -1, -1, 0, 0xFFFF, 0x1001, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0))]
    skel = Skeleton(0x1100, bones, [0], 0, 1, b"")
    vb = vertex_buffer(rnd, 8, [[("_p0", 0x1505), ("_u0", 0x0901)]])
    sh = Shape("root__matC", 2, 0, 0, 0, [], 0, 0, [Mesh("triangles", "u16", 6, indices(rnd, 6, 8), 2, [(0, 6)])],
               bounds(rnd, 2), [f32(1.0)])
    ps, pdata = params(rnd)
    mat = Material("matC", 1, render_state_infos("mask", alpha="true"),
                   ShaderAssign("test_archive", "test_model", 1, [], [("_a0", "_a0")], []),
                   [sampler("_a0", (2, 2, 2))], ["TestTex_C"], ps, pdata, [])
    return Model(name, "", skel, [vb], [sh], [mat], [], 8)


def make_anims(rnd):
    def curve(flags, keys, ctype_elems):
        fsz, ksz = nx_model.curve_sizes(flags, keys)
        head = struct.pack("<HHI5f4x", flags, keys, 0x10, 0.0, 9.0, 1.0, 0.0, 0.0)
        return AnimCurve(head, rnd.randbytes(fsz), rnd.randbytes(ksz))

    ba0 = BoneAnim("root", struct.pack("<I4BI4x", 0x38, 3, 6, 1, 0, 0), rnd.randbytes(40), [curve(0x6, 3, 4)])
    ba1 = BoneAnim("arm", struct.pack("<I4BI4x", 0x10, 0, 0, 0, 0, 0), rnd.randbytes(16), [])
    sk = SkelAnim("Anim_A", "", struct.pack("<IiiIHH4x", 0x1200, 10, 1, 0, 2, 1), [0xFFFF, 0xFFFF], [ba0, ba1],
                  [UserData("loop", "int", (1,))])
    md = MatAnimData("matA", struct.pack("<hh3h4H6x", 0, 1, -1, -1, -1, 1, 1, 1, 2),
                     [("p_float", struct.pack("<HHHHHhI", 0, 1, 0, 0, 1, -1, 0))],
                     [("_a0", struct.pack("<HhI", 1, -1, 0xFF))], [curve(0x12, 2, 2), curve(0x4A, 2, 1)],
                     struct.pack("<If", 4, 0.5))
    ma = MatAnim("Anim_B", "", struct.pack("<IHHiIHHHH", 0, 1, 2, 15, 0, 2, 1, 0, 2), [0xFFFF], [md],
                 ["TestTex_A0", "TestTex_A1"])
    return [sk], [ma]


def make_res(seed=1, anims=True, big=False):
    rnd = random.Random(seed)
    res = ResFile("TestModel", [make_model(rnd, big=big), make_small_model(rnd)],
                  external=[ExtFile("TexInfo.txt", b"3")])
    if anims:
        res.skel_anims, res.mat_anims = make_anims(rnd)
    return res


def no_anims(res):
    c = copy.copy(res)
    c.skel_anims, c.mat_anims = [], []
    return c


def wiiu_view(res):
    """What a Wii U file carries of a Switch ResFile: the animations with a Wii U form (FSKA, FSHU, FTXP; the made-up
    material anim holds both param and pattern anims, so it has none), no TexInfo.txt (Switch only)."""
    c = copy.copy(res)
    c.skel_anims = [a for a in res.skel_anims if not wiiu_anims.unwritable(a)]
    c.mat_anims = [a for a in res.mat_anims if not wiiu_anims.unwritable(a)]
    c.external = [e for e in res.external if e.name != "TexInfo.txt"]
    return c


# ------------------------------------------------------------------------------------------------------ tests

class SwitchModelTests(unittest.TestCase):
    def test_write_read_round_trip(self):
        res = make_res()
        data = nx_model.write(res)
        back = nx_model.read(data)
        self.assertEqual(diff(res, back, "cross"), [])
        self.assertEqual(checks.switch_model_problems(data), [])
        self.assertEqual(nx_model.write(back), data)          # fixed point (string order kept)

    def test_layout_rules(self):
        data = nx_model.write(make_res())
        self.assertEqual(data[:8], b"FRES    ")
        self.assertEqual(struct.unpack_from("<I", data, 8)[0], 0x00050003)
        self.assertEqual(data[0x0E], 12)
        self.assertEqual(struct.unpack_from("<I", data, 0x1C)[0], len(data))
        bufinfo = struct.unpack_from("<Q", data, 0x90)[0]
        prop, pool_size, pool = struct.unpack_from("<IIQ", data, bufinfo)
        self.assertEqual((prop, pool % 0x1000, pool_size % 0x1000), (0x22, 0, 0))
        mp = struct.unpack_from("<Q", data, 0x88)[0]
        self.assertEqual(mp, pool + pool_size)
        # TexInfo.txt after the memory pool object, the relocation table last
        arr = struct.unpack_from("<Q", data, 0x98)[0]
        off, size = struct.unpack_from("<QI", data, arr)
        self.assertEqual((off % 0x100, data[off:off + size]), (0, b"3"))
        # the material with no samplers: texture arrays and slots null and not relocated
        _, per = rlt.parse(data, struct.unpack_from("<I", data, 0x18)[0])
        relocated = {p for s in per for p in s}
        fmdl = 0xD0
        mats = struct.unpack_from("<Q", data, fmdl + 0x40)[0]
        mat_b = mats + nx_model.FMAT_SIZE
        for field in (0x30, 0x38, 0x90, 0x98):
            self.assertEqual(struct.unpack_from("<Q", data, mat_b + field)[0], 0)
            self.assertNotIn(mat_b + field, relocated)
        self.assertNotEqual(struct.unpack_from("<Q", data, mat_b + 0x48)[0], 0)

    def test_empty_model_arrays_are_null_but_relocated(self):
        rnd = random.Random(5)
        skel = Skeleton(0x1100, [Bone("root", -1, -1, -1, 0xFFFF, 1, (1.0,) * 3, (0.0, 0.0, 0.0, 1.0), (0.0,) * 3)],
                        [], 0, 0, b"")
        res = ResFile("TestAnimOnly", [Model("Skeleton", "", skel, [], [], [], [], 0)])
        res.extra["nx.alignment_log2"] = 3
        res.skel_anims, res.mat_anims = make_anims(rnd)
        data = nx_model.write(res)
        self.assertEqual(checks.switch_model_problems(data), [])
        self.assertEqual(diff(res, nx_model.read(data), "cross"), [])
        self.assertEqual(struct.unpack_from("<4Q", data, 0xD0 + 0x28)[0], 0)
        relocated = {p for s in rlt.parse(data, struct.unpack_from("<I", data, 0x18)[0])[1] for p in s}
        self.assertIn(0xD0 + 0x28, relocated)
        self.assertEqual(struct.unpack_from("<Q", data, 0x88)[0], 0)      # no GPU pool, no memory pool

    def test_self_check_finds_a_missing_relocation(self):
        data = bytearray(nx_model.write(make_res(anims=False)))
        rlt_off = struct.unpack_from("<I", data, 0x18)[0]
        secs, per = rlt.parse(data, rlt_off)
        ptrs = sorted((p, struct.unpack_from("<Q", data, p)[0]) for s in per for p in s)
        victim = next(p for p, t in ptrs if t and p > 0x200)
        table = rlt.build(rlt_off, secs, [(p, t) for p, t in ptrs if p != victim])
        broken = bytearray(data[:rlt_off] + table)
        struct.pack_into("<I", broken, 0x1C, len(broken))
        problems = checks.switch_model_problems(bytes(broken))
        self.assertTrue(any("not relocated" in p for p in problems), problems)

    def test_refusals(self):
        res = make_res(anims=False)
        res.models[0].materials[0].textures.append("TestTex_Extra")
        with self.assertRaises(Unsupported):
            nx_model.write(res)
        res = make_res(anims=False)
        res.models[0].shapes[0].key_shapes = ["morph"]
        with self.assertRaises(Unsupported):
            nx_model.write(res)
        data = nx_model.write(make_res(anims=False))
        with self.assertRaises(ModelError):
            nx_model.read(data[:0x400])
        with self.assertRaises(Unsupported):
            nx_model.read(data[:8] + struct.pack("<I", 0x00080000) + data[12:])

    def test_curve_and_base_sizes(self):
        self.assertEqual(nx_model.curve_sizes(0x06, 3), (3, 24))       # u8 frames, s16 keys, cubic
        self.assertEqual(nx_model.curve_sizes(0x0A, 5), (5, 20))       # u8 frames, s8 keys, cubic
        self.assertEqual(nx_model.curve_sizes(0x12, 4), (4, 32))       # u8 frames, f32 keys, linear
        self.assertEqual(nx_model.curve_sizes(0x4A, 2), (2, 2))        # step int
        self.assertEqual(nx_model.curve_sizes(0x60, 40), (160, 8))     # step bool: one bit per key
        self.assertEqual(nx_model.base_data_size(0x38), 40)
        self.assertEqual(nx_model.base_data_size(0x30), 28)
        with self.assertRaises(Unsupported):
            nx_model.curve_sizes(0x03, 1)


class WiiUModelTests(unittest.TestCase):
    def test_write_read_round_trip(self):
        res = make_res()
        notes = []
        data = wiiu_models.write(res, notes=notes)
        self.assertTrue(any("animation 'Anim_B' left out" in n and "both param and texture pattern" in n
                            for n in notes))
        self.assertTrue(any("TexInfo.txt left out" in n for n in notes))
        warn = []
        back = wiiu_models.read(data, warn)
        self.assertEqual(warn, [])
        self.assertEqual(diff(wiiu_view(res), back, "cross"), [])
        self.assertEqual(wiiu_models.self_check(data), [])
        # Wii U -> Wii U keeps everything (RenderState and sampler words come back from extra)
        again = wiiu_models.write(back)
        self.assertEqual(again, data)

    def test_cross_paths(self):
        res = make_res()
        sw = nx_model.write(res)
        wu = wiiu_models.write(nx_model.read(sw))
        back = wiiu_models.read(wu)
        for m in back.models:
            materials.to_switch(m)
        sw2 = nx_model.write(back)
        self.assertEqual(checks.switch_model_problems(sw2), [])
        self.assertEqual(diff(wiiu_view(res), nx_model.read(sw2), "cross"), [])
        # the other way: Wii U -> Switch -> Wii U
        wu_res = wiiu_models.read(wu)
        sw_res = nx_model.read(nx_model.write(wu_res))
        self.assertEqual(diff(wu_res, sw_res, "cross"), [])
        self.assertEqual(diff(wu_res, wiiu_models.read(wiiu_models.write(sw_res)), "cross"), [])

    def test_big_endian_layout(self):
        res = make_res(anims=False)
        data = wiiu_models.write(res)
        self.assertEqual((data[:4], data[8:10]), (b"FRES", b"\xfe\xff"))
        self.assertEqual(struct.unpack_from(">I", data, 4)[0], 0x04050003)
        self.assertEqual(struct.unpack_from(">I", data, 0x0C)[0], len(data))
        pool = wiiu_models.string_pool(data)
        self.assertEqual(pool, sorted(pool, key=lambda s: s.encode()))
        # the u16 indices of the first mesh appear byte-swapped
        idx = res.models[0].shapes[0].meshes[0].indices
        swapped = wiiu_models.swap_words(idx, 2, "indices")
        self.assertIn(swapped, data)
        self.assertNotEqual(swapped, idx)

    def test_every_attribute_format_swaps_both_ways(self):
        for base, (lay, sizes, is_float) in formats.GX2_ATTR_BASE.items():
            for flags in ((0x800, 0x000) if is_float else (0x000, 0x100, 0x200, 0x300)):
                gx2 = base | flags
                fmt = formats.attr_format_from_gx2(gx2)
                self.assertEqual(fmt >> 8, lay)
                self.assertEqual(formats.attr_format_from_gx2(formats.attr_format_to_gx2(fmt)), fmt)
                # a vertex: a 3-byte pad, the attribute, more pad; each component reversed, the pad untouched
                a = Attribute("_t", fmt, 3, 0)
                stride = 3 + sum(sizes) + 5
                plan = wiiu_models.swap_plan([a], stride, "test")
                vtx = bytes(range(stride)) * 3
                out = wiiu_models.swap_vertices(vtx, stride, plan)
                want = bytearray(vtx)
                for v in range(3):
                    o = v * stride + 3
                    for s in sizes:
                        want[o:o + s] = vtx[o:o + s][::-1]
                        o += s
                self.assertEqual(out, bytes(want), hex(gx2))
                self.assertEqual(wiiu_models.swap_vertices(out, stride, plan), vtx)
        self.assertEqual(formats.attr_format_to_gx2(0x1505), 0x80F)
        self.assertEqual(formats.attr_format_to_gx2(0x0E02), 0x20B)
        for bad in (0x001, 0x009, 0x40A, 0x80A, 0x14):
            with self.assertRaises(Unsupported):
                formats.attr_format_from_gx2(bad)
        with self.assertRaises(Damaged):
            wiiu_models.swap_plan([Attribute("a", 0x1505, 0, 0), Attribute("b", 0x0B01, 4, 0)], 8, "test")

    def test_little_endian_index_type_is_read(self):
        res = make_res(anims=False)
        res.models[0].shapes[0].meshes[0].extra["wiiu.index_le"] = True
        data = wiiu_models.write(res)
        back = wiiu_models.read(data)
        self.assertEqual(back.models[0].shapes[0].meshes[0].indices, res.models[0].shapes[0].meshes[0].indices)

    def test_refusals(self):
        res = make_res(anims=False)
        data = bytearray(wiiu_models.write(res))
        with self.assertRaises(Unsupported):
            wiiu_models.read(bytes(data[:4]) + struct.pack(">I", 0x04040001) + bytes(data[8:]))
        with self.assertRaises(ModelError):
            wiiu_models.read(bytes(data[:0x200]))
        res.models[0].materials[0].params[1].type = wiiu_models.PARAM_TEXSRT_EX    # a TexSrtEx of 4 bytes, not 28
        with self.assertRaises(Unsupported):
            wiiu_models.read(wiiu_models.write(res))
        # key shapes: set the count byte of the first FSHP
        data = bytearray(wiiu_models.write(make_res(anims=False)))
        o = data.find(b"FSHP")
        data[o + 0x18] = 1
        with self.assertRaises(Unsupported):
            wiiu_models.read(bytes(data))

    def test_textures_and_animations_in_a_model_file_are_noted(self):
        data = bytearray(wiiu_models.write(make_res(anims=False)))
        # a count without a group is damaged
        bad = bytearray(data)
        struct.pack_into(">H", bad, 0x50 + 2 * 2, 1)
        with self.assertRaises(Damaged):
            wiiu_models.read(bytes(bad))
        # let the texture and bone visibility groups name the two models' entries: listed and warned, not read
        group = 0x20 + struct.unpack_from(">i", data, 0x20)[0]
        for k in (1, 7):
            struct.pack_into(">i", data, 0x20 + 4 * k, group - (0x20 + 4 * k))
            struct.pack_into(">H", data, 0x50 + 2 * k, 2)
        warn = []
        res = wiiu_models.read(bytes(data), warn)
        self.assertEqual(res.extra["wiiu.textures"], ["TestBody", "TestProp"])
        self.assertEqual(res.extra["wiiu.animations_dropped"], {"bone visibility anims": ["TestBody", "TestProp"]})
        self.assertTrue(any("not converted, dropped: bone visibility anims" in w for w in warn))
        self.assertTrue(any("belong in X.Tex" in w for w in warn))
        self.assertEqual(len(res.models), 2)
        # a skeletal animation group pointing at a model is damage, not a guess
        struct.pack_into(">i", data, 0x20 + 4 * 2, group - (0x20 + 4 * 2))
        struct.pack_into(">H", data, 0x50 + 2 * 2, 2)
        with self.assertRaises(Damaged):
            wiiu_models.read(bytes(data))


class MaterialTests(unittest.TestCase):
    def test_sampler_words_round_trip(self):
        rnd = random.Random(3)
        for _ in range(400):
            st = SamplerState(rnd.randrange(5), rnd.randrange(5), rnd.randrange(5), rnd.randrange(8),
                              rnd.randrange(3), rnd.choice((1, 2, 4, 8, 16)),
                              rnd.choice((1, 2)) | (rnd.choice((1, 2)) << 2) | (rnd.choice((1, 2)) << 4)
                              | rnd.choice((0, materials.FILTER_COMPARE)),
                              rnd.randrange(0, 13 * 64) / 64, rnd.randrange(0, 15 * 64) / 64,
                              rnd.randrange(-32 * 64, 32 * 64) / 64)
            words = materials.sampler_to_gx2(st)
            back = materials.sampler_from_gx2(words)
            self.assertEqual(diff(st, back, "cross"), [])
            self.assertEqual(materials.sampler_to_gx2(back), words)

    def test_known_sampler_words(self):
        # repeat / repeat / clamp, linear mag and min, point mip, LOD 0..13, bias -1
        st = SamplerState(0, 0, 2, 0, 0, 1, 0x29, 0.0, 13.0, -1.0)
        w0, w1, w2 = materials.sampler_to_gx2(st)
        self.assertEqual(w0, (2 << 6) | (1 << 9) | (1 << 12) | (1 << 17) | (2 << 22))
        self.assertEqual(w1, (13 * 64) << 10 | (0xFFF & -64) << 20)
        self.assertEqual(w2, 0x80000000)
        st2 = materials.sampler_from_gx2((0, 0, 0))                 # GX2 'no mip' becomes point on Switch
        self.assertEqual((st2.filter, st2.border, st2.max_aniso), (0x15, 1, 1))
        self.assertEqual(materials.sampler_to_gx2(st2), (0, 0, 0))  # and goes back to 'no mip'
        warn = []
        st3 = materials.sampler_from_gx2((4, 0, 0), warn)           # clamp half border: nearest, with a warning
        self.assertEqual(st3.wrap_u, 3)
        self.assertTrue(warn)
        warn = []
        materials.sampler_to_gx2(SamplerState(0, 0, 0, 0, 0, 1, 0x29, 0.0, 13.01, 0.0), warn)
        self.assertTrue(warn)
        with self.assertRaises(Unsupported):
            materials.sampler_to_gx2(SamplerState(0, 0, 0, 0, 0, 1, 0x69, 0.0, 13.0, 0.0))

    def test_render_state_round_trip(self):
        rnd = random.Random(4)
        names = materials.RENDER_STATE_NAMES
        for _ in range(300):
            v = {"gsys_render_state_mode": rnd.choice(list(materials.MODES.values())),
                 "gsys_render_state_display_face": rnd.choice(list(materials.FACES.values())),
                 "gsys_render_state_blend_mode": rnd.choice(list(materials.BLEND_MODES.values())),
                 "gsys_depth_test_enable": rnd.choice(("true", "false")),
                 "gsys_depth_test_write": rnd.choice(("true", "false")),
                 "gsys_depth_test_func": rnd.choice(list(materials.COMPARE.values())),
                 "gsys_alpha_test_enable": rnd.choice(("true", "false")),
                 "gsys_alpha_test_func": rnd.choice(list(materials.COMPARE.values()))}
            for n in names[6:12]:
                table = materials.BLEND_OPS if n.endswith("_op") else materials.BLEND_FUNCS
                v[n] = rnd.choice(list(table.values()))
            infos = [RenderInfo(n, "string", (v[n],)) for n in names[:14]]
            infos += [RenderInfo(names[14], "float", tuple(f32(rnd.random()) for _ in range(4))),
                      RenderInfo(names[15], "float", (f32(rnd.random()),))]
            rs = materials.render_state_from_infos(infos)
            self.assertEqual(materials.infos_from_render_state(rs), infos)

    def test_known_render_state(self):
        rs = materials.render_state_from_infos(render_state_infos("translucent", "both", "color", "false"))
        self.assertEqual(rs.flags, 3 | (1 << 4))
        self.assertEqual(rs.polygon_control & 3, 0)
        self.assertEqual(rs.depth_control & 0x76, 2 | (3 << 4))
        self.assertEqual(rs.alpha_control, 6)
        self.assertEqual(rs.blend_control & 0x1FFF1FFF, 4 | (5 << 8) | (1 << 16))
        self.assertEqual(materials.infos_from_render_state(rs)[1].values, ("both",))
        rs.polygon_control = 2                                         # cull back -> 'front' is drawn
        self.assertEqual(materials.infos_from_render_state(rs)[1].values, ("front",))
        rs.blend_control = (rs.blend_control & ~0x1F) | 9              # an unconfirmed blend function: warned
        warn = []
        materials.infos_from_render_state(rs, warn)
        self.assertTrue(warn)
        with self.assertRaises(Unsupported):
            materials.render_state_from_infos([RenderInfo("gsys_render_state_mode", "string", ("weird",))])

    def test_to_switch_adds_render_infos_and_alpha_ref(self):
        res = make_res(anims=False)
        orig = copy.deepcopy(res)
        data = wiiu_models.write(res, keep_render_infos=False)
        back = wiiu_models.read(data)
        mat = back.models[0].materials[0]
        self.assertFalse(any(r.name in materials.RENDER_STATE_NAMES for r in mat.render_infos))
        struct.pack_into("<f", buf := bytearray(mat.param_data), 24, 0.0)
        mat.param_data = bytes(buf)
        for m in back.models:
            materials.to_switch(m)
        self.assertEqual(diff(wiiu_view(orig), back, "cross"), [])
        # again: nothing changes once the render infos are there
        again = copy.deepcopy(back)
        for m in again.models:
            materials.to_switch(m)
        self.assertEqual(diff(back, again, "same"), [])
        # a Wii U material without the alpha ref param gets it appended, with a note
        mat = copy.deepcopy(wiiu_models.read(data).models[0].materials[0])
        mat.params = [p for p in mat.params if p.name != materials.ALPHA_REF_PARAM]
        warn = []
        materials.to_switch(Model("m", "", None, [], [], [mat], [], 0), warn)
        self.assertEqual(mat.params[-1].name, materials.ALPHA_REF_PARAM)
        self.assertEqual(materials.alpha_ref(mat), f32(0.5))
        self.assertTrue(any("added" in w for w in warn))


class ConvertTests(unittest.TestCase):
    def test_model_to_switch(self):
        res = make_res(anims=False)
        wu = wiiu_models.write(res, keep_render_infos=False)
        warn = []
        out, conv = wm.model_to_switch(wu, warn)
        self.assertEqual(checks.switch_model_problems(out), [])
        self.assertEqual(diff(wiiu_view(res), nx_model.read(out), "cross"), [])
        self.assertEqual([e.name for e in nx_model.read(out).external], [])     # TexInfo.txt: Integrate writes it
        self.assertEqual(wm.sniff(out), "switch")
        self.assertEqual(wm.read_model(wu).name, "TestModel")
        self.assertEqual(wm.write_model(conv), out)

    def test_model_stats(self):
        res = make_res(anims=False)
        vb = res.models[0].vertex_buffers[0]
        stride = vb.strides[0]
        buf = bytearray(vb.buffers[0])
        unit = (511 << 20)                                             # (0, 0, 1)
        for v in range(vb.vertex_count):
            struct.pack_into("<I", buf, stride * v + 8, unit if v else 0)
        vb.buffers[0] = bytes(buf)
        b2 = bytearray(vb.buffers[2])
        for v in range(vb.vertex_count):
            b2[8 * v:8 * v + 8] = bytes((0, 1, 0, 0, 200, 55, 0, 0)) if v else bytes((9, 0, 0, 0, 100, 0, 0, 0))
        vb.buffers[2] = bytes(b2)
        st = checks.model_stats(res)
        self.assertEqual((st["normals"], st["bad_normals"]), (vb.vertex_count + 10 * 0, 1))
        self.assertEqual(st["bad_bone_refs"], 1)
        self.assertEqual(st["bad_weights"], 1)
        self.assertEqual(st["bad_indices"], 0)
        mesh = res.models[0].shapes[0].meshes[0]
        mesh.indices = struct.pack("<H", vb.vertex_count) + mesh.indices[2:]
        self.assertEqual(checks.model_stats(res)["bad_indices"], 1)

    def test_speed(self):
        res = make_res(anims=False, big=True)
        t = time.perf_counter()
        for _ in range(3):
            wu = wiiu_models.write(res)
            back = wiiu_models.read(wu)
            nx_model.write(back)
        self.assertLess(time.perf_counter() - t, 5.0)


class SharedPieceTests(unittest.TestCase):
    """The dictionary builder and relocation encoder were made faster for big model files; both must give exactly
    what the plain rules (switch-format.md 2.4 and 2.5) give."""

    @staticmethod
    def slow_build(keys):
        def bit(key, b):
            i = b >> 3
            return 0 if i >= len(key) else (key[len(key) - 1 - i] >> (b & 7)) & 1

        def first_diff(a, b):
            for i in range(max(len(a), len(b)) * 8):
                if bit(a, i) != bit(b, i):
                    return i
            raise ValueError

        ref, left, right, kk = [-1], [0], [0], [b""]
        for key in keys:
            prev, cur = 0, left[0]
            while ref[cur] > ref[prev]:
                prev, cur = cur, (right[cur] if bit(key, ref[cur]) else left[cur])
            b = first_diff(key, kk[cur])
            prev, cur = 0, left[0]
            while ref[cur] > ref[prev] and ref[cur] < b:
                prev, cur = cur, (right[cur] if bit(key, ref[cur]) else left[cur])
            n = len(ref)
            ref.append(b)
            kk.append(key)
            if bit(key, b):
                left.append(cur)
                right.append(n)
            else:
                left.append(n)
                right.append(cur)
            if prev == 0:
                left[0] = n
            elif bit(key, ref[prev]):
                right[prev] = n
            else:
                left[prev] = n
        return list(zip(ref, left, right))

    @staticmethod
    def slow_encode(positions):
        left = set(positions)
        ents = []

        def run(p):
            k = 0
            while p + 8 * k in left and k < 255:
                k += 1
            return k

        def starts_run(p, k):
            return p in left and (p - 8) not in left and run(p) == k

        while left:
            p = min(left)
            k = run(p)
            q = None
            for cand in sorted(x for x in left if x >= p + 8 * k):
                if starts_run(cand, k):
                    q = cand
                    break
            n, skip = 1, 0
            if q is not None and (q - p) // 8 - k <= 255:
                stride = (q - p) // 8
                skip = stride - k
                n = 2
                while n < 0xFFFF and starts_run(p + n * stride * 8, k):
                    n += 1
            ents.append((p, n, k, skip))
            for j in range(n):
                for i in range(k):
                    left.discard(p + 8 * (j * (k + skip) + i))
        ents.sort()
        return ents

    def test_dictionary_builder(self):
        rnd = random.Random(8)
        for n in (1, 2, 5, 40, 300):
            keys = list({("k" * rnd.randrange(3) + "".join(rnd.choice("ab_01") for _ in range(rnd.randrange(1, 9))))
                         .encode() for _ in range(n)})
            self.assertEqual(dicts.build(keys), self.slow_build(keys))
            for i, k in enumerate(keys):
                self.assertEqual(dicts.lookup(dicts.build(keys), keys, k), i)
        with self.assertRaises(ValueError):
            dicts.build([b"ab", b"ab"])

    def test_relocation_encoder(self):
        rnd = random.Random(9)
        for _ in range(30):
            pos = set()
            base = 0
            for _ in range(rnd.randrange(1, 40)):
                base += 8 * rnd.randrange(1, 300)
                k = rnd.randrange(1, 4)
                for rep in range(rnd.randrange(1, 6)):
                    pos.update(base + 8 * (rep * (k + 3) + i) for i in range(k))
                base += 8 * 6 * 8
            self.assertEqual(rlt.encode(pos), self.slow_encode(pos))
            self.assertEqual(sorted(rlt.decode(rlt.encode(pos))), sorted(pos))


# ------------------------------------------------------------------------------------- review fixes (milestone M2)

def fix_geometry(res):
    """Valid decoded values in the skinned vertex buffer of the first model (the made-up data is random): unit
    normals, bone indices below the 3 matrices, weights summing to 255."""
    vb = res.models[0].vertex_buffers[0]
    b0 = bytearray(vb.buffers[0])
    for v in range(vb.vertex_count):
        struct.pack_into("<I", b0, vb.strides[0] * v + 8, 511 << 20)     # _n0 = (0, 0, 1)
    vb.buffers[0] = bytes(b0)
    b2 = bytearray(vb.buffers[2])
    for v in range(vb.vertex_count):
        b2[8 * v:8 * v + 8] = bytes((v % 3, (v + 1) % 3, 0, 0, 200, 55, 0, 0))
    vb.buffers[2] = bytes(b2)
    return res


def rel_target(data, pos):
    return pos + struct.unpack_from(">i", data, pos)[0]


class VerifyModelsTests(unittest.TestCase):
    def test_sample_fails_on_bad_decoded_values(self):
        bad = ResFile("Fake", [make_model(random.Random(9), big=True)])           # random vertex bytes
        s = verify_models.check_sample("bad.sbfres", wiiu_models.write(bad, keep_render_infos=False))
        self.assertFalse(s["ok"])
        self.assertTrue(any("normals off unit length" in p for p in s["decoded_problems"]), s["decoded_problems"])
        self.assertTrue(any("bone indices" in p for p in s["decoded_problems"]))
        good = fix_geometry(ResFile("Fake", [make_model(random.Random(9), big=True)]))
        g = verify_models.check_sample("good.sbfres", wiiu_models.write(good, keep_render_infos=False))
        self.assertTrue(g["ok"], g)
        txt = verify_models.text({"files": [], "samples": [s, g], "seconds": 0.0})
        self.assertIn("1/2 model files parse, convert, pass the Switch self-check and the decoded checks", txt)
        per = txt.split("\nWii U samples:\n")[1]
        self.assertIn("bad.sbfres: FAILED", per)
        self.assertIn("good.sbfres: ok", per)
        self.assertIn("normals off unit length", per)
        self.assertIn("decoded FAILS", per)

    def test_decoded_tolerance(self):
        st = {"normals": 1000, "bad_normals": 10, "indices": 50, "bad_indices": 0, "bone_refs": 20,
              "bad_bone_refs": 0, "weights": 1000, "bad_weights": 11}
        self.assertEqual(verify_models.decoded_problems(st, verify_models.SAMPLE_TOLERANCE),
                         ["11/1000 weight sets not summing to 255"])
        self.assertEqual(len(verify_models.decoded_problems(st)), 2)
        st["bad_bone_refs"] = 1
        self.assertIn("1/20 bone indices past the matrix count", verify_models.decoded_problems(st, 0.5))

    def test_v2_does_not_hide_the_missing_texinfo(self):
        res = fix_geometry(make_res())
        r = verify_models.check_file("Test.bfres", nx_model.write(res))
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["texinfo"] and r["texinfo_lost"])
        self.assertTrue(any("TexInfo.txt left out" in n for n in r["left_out"]))
        txt = verify_models.text({"files": [r], "samples": None, "seconds": 0.0})
        self.assertIn("TexInfo.txt: 1/1 files have one; a Wii U file has none, so it is missing after this round trip "
                      "in 1 of them", txt)
        self.assertIn("This covers only the values your files use: mode mask/opaque/translucent, face both/front", txt)
        # a weight set that does not sum to 255 now fails a Switch file too
        vb = res.models[0].vertex_buffers[0]
        b2 = bytearray(vb.buffers[2])
        b2[4] = 7
        vb.buffers[2] = bytes(b2)
        r = verify_models.check_file("Test.bfres", nx_model.write(res))
        self.assertFalse(r["ok"])
        self.assertEqual(r["decoded_problems"], [f"1/{vb.vertex_count} weight sets not summing to 255"])


class NullOffsetTests(unittest.TestCase):
    def test_null_counted_offsets_are_damage(self):
        res = make_res(anims=False)
        res.external.append(ExtFile("TestNote.txt", b"hello"))
        data = wiiu_models.write(res)
        wiiu_models.read(data)
        fmdl, fskl, fvtx = data.find(b"FMDL"), data.find(b"FSKL"), data.find(b"FVTX")
        fshp, fmat = data.find(b"FSHP"), data.find(b"FMAT")
        mesh = rel_target(data, fshp + 0x24)
        egroup = rel_target(data, 0x4C)
        fields = {"string pool": 0x1C, "model group entry": rel_target(data, 0x20) + 8 + 16 + 12,
                  "external file data": rel_target(data, egroup + 8 + 16 + 12),
                  "vertex buffers": fmdl + 0x10, "bones": fskl + 0x14, "matrix list": fskl + 0x18,
                  "inverse matrices": fskl + 0x1C, "attributes": fvtx + 0x10, "buffers": fvtx + 0x18,
                  "vertex data": rel_target(data, fvtx + 0x18) + 0x14, "radii": fshp + 0x1C, "meshes": fshp + 0x24,
                  "skin bones": fshp + 0x28, "bounds": fshp + 0x30, "submeshes": mesh + 0x10,
                  "index buffer": mesh + 0x14, "index data": rel_target(data, mesh + 0x14) + 0x14,
                  "texture refs": fmat + 0x28, "samplers": fmat + 0x2C, "params": fmat + 0x34,
                  "param data": fmat + 0x3C, "volatile flags": fmat + 0x44}
        for label, pos in fields.items():
            self.assertNotEqual(struct.unpack_from(">i", data, pos)[0], 0, label)
            w = bytearray(data)
            struct.pack_into(">i", w, pos, 0)
            with self.assertRaises(Damaged, msg=label):
                wiiu_models.read(bytes(w))
            with self.assertRaises(Damaged, msg=label):
                wm.model_to_switch(bytes(w))


class UserDataTests(unittest.TestCase):
    def test_one_numbering_bytes_are_4_on_switch(self):
        self.assertIs(nx_model.USER_DATA_KINDS, formats.USER_DATA_KINDS)
        res = make_res(anims=False)                      # the first model has bytes user data
        for sw in (nx_model.write(res), wm.model_to_switch(wiiu_models.write(res))[0]):
            spans = []
            nx_model.read(sw, spans)
            types = sorted({sw[o + 0x14] for o, _, label in spans if label == "user data"})
            self.assertEqual(types, [0, 1, 2, 4])
        spans = []
        sw = nx_model.write(res)
        nx_model.read(sw, spans)
        o = next(o for o, _, label in spans if label == "user data" and sw[o + 0x14] == 4)
        bad = bytearray(sw)
        bad[o + 0x14] = 3                                  # a wide string in BfresLibrary's numbering: not seen
        with self.assertRaises(Unsupported):
            nx_model.read(bytes(bad))

    def test_wide_strings(self):
        res = make_res(anims=False)
        res.models[0].user_data.append(UserData("wide", "wstring", ("abc", "Zé", "")))
        data = wiiu_models.write(res)
        self.assertIn("abc".encode("utf-16-be") + b"\0\0", data)
        self.assertEqual(wiiu_models.self_check(data), [])
        back = wiiu_models.read(data)
        self.assertEqual(back.models[0].user_data[-1], UserData("wide", "wstring", ("abc", "Zé", "")))
        with self.assertRaises(Unsupported):
            nx_model.write(back)                                       # never silently a string
        warn = []
        out, _ = wm.model_to_switch(data, warn)
        self.assertTrue(any("wide-string user data 'wide' became a string" in w for w in warn), warn)
        ud = nx_model.read(out).models[0].user_data[-1]
        self.assertEqual((ud.kind, ud.values), ("string", ("abc", "Zé", "")))
        bad = data.replace("abc".encode("utf-16-be"), b"\xd8\x00" + "bc".encode("utf-16-be"))   # lone surrogate
        with self.assertRaises(Unsupported):
            wiiu_models.read(bad)


class ParamTests(unittest.TestCase):
    def test_texsrt_ex_becomes_texsrt(self):
        res = make_res(anims=False)
        mat = res.models[0].materials[1]        # p_uint 0+4, p_float 4+4, p_float4 8+16, alpha ref 24+4, p_srt 28+24
        srt = mat.params[4]
        srt.type, srt.size = wiiu_models.PARAM_TEXSRT_EX, 28
        mat.params.append(Param("p_after", 12, 56, 4, 5, 5))
        data = mat.param_data[:52] + b"\x11\x22\x33\x44" + struct.pack("<f", 2.5)
        mat.param_data = data
        warn = []
        back = wiiu_models.read(wiiu_models.write(res), warn)
        bm = back.models[0].materials[1]
        self.assertEqual([(p.type, p.offset, p.size) for p in bm.params[3:]], [(12, 24, 4), (30, 28, 24), (12, 52, 4)])
        self.assertEqual(bm.param_data, data[:52] + data[56:])
        self.assertTrue(any("TexSrtEx" in w and "p_srt" in w for w in warn), warn)
        out, _ = wm.model_to_switch(wiiu_models.write(res))
        self.assertEqual(checks.switch_model_problems(out), [])
        self.assertEqual(nx_model.read(out).models[0].materials[1].param_data, data[:52] + data[56:])
        srt.size = 24                                                   # a TexSrtEx without the pointer: refused
        with self.assertRaises(Unsupported):
            wiiu_models.read(wiiu_models.write(res))

    def test_raw_param_size_is_not_carried_to_switch(self):
        res = make_res(anims=False)
        res.models[0].materials[0].extra["wiiu.raw_param_size"] = 0x40
        wu = wiiu_models.write(res, keep_render_infos=False)
        self.assertEqual(wiiu_models.read(wu).models[0].materials[0].extra["wiiu.raw_param_size"], 0x40)
        warn = []
        out, _ = wm.model_to_switch(wu, warn)
        self.assertNotIn("nx.raw_param_size", nx_model.read(out).models[0].materials[0].extra)
        self.assertTrue(any("raw param size" in w for w in warn), warn)
        # a Switch value stays Switch -> Switch and does not reach the Wii U file
        res = make_res(anims=False)
        res.models[0].materials[0].extra["nx.raw_param_size"] = 8
        sw_res = nx_model.read(nx_model.write(res))
        self.assertEqual(sw_res.models[0].materials[0].extra["nx.raw_param_size"], 8)
        wu_mat = wiiu_models.read(wiiu_models.write(sw_res)).models[0].materials[0]
        self.assertNotIn("wiiu.raw_param_size", wu_mat.extra)


class SamplerAndRenderStateTests(unittest.TestCase):
    def test_compare_and_anisotropy(self):
        w0 = (2 << 6) | (1 << 9) | (1 << 12) | (1 << 17) | (2 << 19) | (3 << 26)    # 4:1, compare function LEQUAL
        st = materials.sampler_from_gx2((w0, (13 * 64) << 10, 0x80000000 | (1 << 30)))
        self.assertEqual((st.filter, st.max_aniso, st.compare), (0x29 | materials.FILTER_COMPARE, 4, 3))
        self.assertEqual(materials.sampler_to_gx2(st)[2], 0x80000000 | (1 << 30))
        self.assertEqual(len(materials.sampler_notes(st)), 2)
        plain = materials.sampler_from_gx2((w0 & ~(7 << 19), (13 * 64) << 10, 0x80000000))
        self.assertEqual((plain.filter, materials.sampler_notes(plain)), (0x29, []))
        st2 = SamplerState(0, 0, 2, 3, 0, 1, 0x29 | materials.FILTER_COMPARE, 0.0, 13.0, 0.0)    # no kept words
        self.assertEqual(materials.sampler_to_gx2(st2)[2], 0x80000000 | (1 << 30))
        back = wiiu_models.read(wiiu_models.write(make_res(anims=False)))     # sampler _n0 has anisotropy 4
        warn = []
        materials.to_switch(back.models[0], warn)
        self.assertTrue(any("sampler '_n0': anisotropy 4:1" in w for w in warn), warn)

    def test_unseen_render_state_values_are_warned(self):
        warn = []
        materials.infos_from_render_state(materials.render_state_from_infos(render_state_infos()), warn)
        self.assertEqual(warn, [])
        cases = [({"face": "back"}, None, "'back'"), ({"face": "none"}, None, "'none'"),
                 ({"mode": "custom"}, None, "'custom'"), ({"blend": "logic"}, None, "'logic'"),
                 ({}, "never", "'never'"), ({}, "noequal", "'noequal'")]
        for kw, depth, word in cases:
            infos = render_state_infos(**kw)
            if depth:
                infos[5] = RenderInfo("gsys_depth_test_func", "string", (depth,))
            warn = []
            materials.infos_from_render_state(materials.render_state_from_infos(infos), warn)
            self.assertEqual(len(warn), 1, (kw, depth, warn))
            self.assertIn(word, warn[0])


if __name__ == "__main__":
    unittest.main()
