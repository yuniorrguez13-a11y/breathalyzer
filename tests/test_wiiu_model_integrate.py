"""Tests for the Integrate milestone of tools/wiiu_model (Wii U animations, vanilla donor materials, TexInfo.txt, size
table rules) and its use by tools/wiiu_convert.py (model and texture files of a Wii U mod, packs, --game). All data is
made up here in code (invented names, random or patterned numbers): no game files."""
import copy
import random
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
sys.path.insert(0, str(HERE))
import wiiu_convert as wc  # noqa: E402
import mod_inspect as mi  # noqa: E402
from wiiu_model import convert, materials, nx_bfres, nx_model, wiiu_anims, wiiu_bfres, wiiu_models  # noqa: E402
from wiiu_model.errors import Damaged  # noqa: E402
from wiiu_model.neutral import (AnimCurve, BoneAnim, ExtFile, MatAnim, MatAnimData, RenderInfo, ResFile,  # noqa: E402
                                SamplerState, SkelAnim, Texture, TextureSet, UserData, diff)
from test_wiiu_model_models import make_model, make_small_model  # noqa: E402
from test_wiiu_model_textures import made_up_set, smooth_chain  # noqa: E402

LE, BE = wc.LE, wc.BE


# --------------------------------------------------------------------------------------------- made-up animations

def curve(rnd, flags, keys, target=0):
    fsz, ksz = nx_model.curve_sizes(flags, keys)
    head = struct.pack("<HHI5f4x", flags, keys, target, 0.0, float(keys), 1.0, 0.25, 0.5)
    return AnimCurve(head, rnd.randbytes(fsz), rnd.randbytes(ksz))


def step_curve(textures, offset=1):
    """A texture pattern curve: u8 frames, s8 keys, step int (flags 0x4A); value = key + the integer offset."""
    n = len(textures)
    head = struct.pack("<HHIfffifI", 0x4A, n, 0, 0.0, float(n), 0.0, offset, float(n), 0)
    return AnimCurve(head, bytes(range(n)), bytes((t - offset) & 0xFF for t in textures))


def mat_head(flags, nmat, ncurve, frames, count70, npat, ntex):
    return struct.pack("<HHHHiIHHHH", flags, 0, nmat, ncurve, frames, 64, count70, npat, 0, ntex)


def made_up_anims(rnd):
    """A skeletal anim with every curve encoding, three FSHU-kind material anims (param, colour, tex SRT) and an
    FTXP-kind one (armour dye), sorted by name as the Switch keeps them."""
    c = [curve(rnd, 0x00, 3, 4), curve(rnd, 0x05, 4, 8), curve(rnd, 0x1A, 5, 0x14), curve(rnd, 0x2A, 6, 0x18),
         curve(rnd, 0x60, 40, 0)]
    bones = [BoneAnim("root", struct.pack("<IBBBBii", 0x0F000038, 3, 7, 2, 7, 0, 0), rnd.randbytes(40), c[:2]),
             BoneAnim("arm", struct.pack("<IBBBBii", 0x10, 0, 4, 3, 0, 2, 0), rnd.randbytes(16), c[2:]),
             BoneAnim("hand", struct.pack("<IBBBBii", 0, 0, 0, 0, 0, 5, 0), None, [])]
    sk = SkelAnim("Test_Walk", "", struct.pack("<IiiIHHI", 0x1204, 12, 5, 640, 3, 1, 0), [0xFFFF] * 3, bones,
                  [UserData("loop", "int", (1, 2))])

    def params(name, ncurves, nconst, first=0):
        infos = [(f"p_{name}", struct.pack("<6H4x", 0, ncurves, 0, 0, nconst, 0xFFFF))]
        consts = b"".join(struct.pack("<If", 4 * k, 0.5 + k) for k in range(nconst))
        return MatAnimData(name, struct.pack("<5h4H6x", first, -1, -1, -1, -1, 1, 0, nconst, ncurves), infos, [],
                           [curve(rnd, 0x12, 3, 4 * k) for k in range(ncurves)], consts)

    fsp = MatAnim("Test_Auto_fsp", "", mat_head(4, 2, 3, 300, 5, 0, 0), [0xFFFF, 0xFFFF],
                  [params("matA", 2, 1, 0), params("matB", 1, 1, 2)], [])
    fcl = MatAnim("Test_Color_fcl", "", mat_head(0, 1, 1, 11, 3, 0, 0), [0xFFFF], [params("matA", 1, 2)], [])
    fts = MatAnim("Test_Uv_fts", "", mat_head(0, 1, 0, 1, 1, 0, 0), [0xFFFF], [params("matB", 0, 1)], [])
    pats = [("_a0", struct.pack("<hHb3x", 0, 0xFFFF, -1))]
    ftp = MatAnim("Test_Dye_ftp", "", mat_head(0, 2, 2, 3, 0, 2, 3), [0xFFFF, 0xFFFF],
                  [MatAnimData("matA", struct.pack("<5h4H6x", -1, 0, -1, -1, -1, 0, 1, 0, 1), [], pats,
                               [step_curve([0, 1, 2])], b""),
                   MatAnimData("matB", struct.pack("<5h4H6x", -1, 1, -1, -1, -1, 0, 1, 0, 1), [], pats,
                               [step_curve([2, 1, 0])], b"")],
                  ["Test_Alb.0", "Test_Alb.1", "Test_Alb.2"])
    return [sk], sorted([fsp, fcl, fts, ftp], key=lambda a: a.name)


def anim_res(seed=5):
    rnd = random.Random(seed)
    res = ResFile("Test_Anims", [make_small_model(rnd)])
    res.skel_anims, res.mat_anims = made_up_anims(rnd)
    return res


def ftxp_offsets(data: bytes) -> tuple[int, int]:
    """(first FTXP material, its first pattern info) of a Wii U file our writer made."""
    o = data.find(b"FTXP")
    marr = o + 0x2C + struct.unpack_from(">i", data, o + 0x2C)[0]
    pinfo = marr + 0x10 + struct.unpack_from(">i", data, marr + 0x10)[0]
    return marr, pinfo


class WiiUAnimationTests(unittest.TestCase):
    def test_switch_wiiu_switch_round_trip(self):
        res = anim_res()
        sw = nx_model.write(res)
        self.assertEqual(nx_model.read(sw).mat_anims[0].name, "Test_Auto_fsp")
        notes = []
        wu = wiiu_models.write(nx_model.read(sw), notes=notes)
        self.assertEqual(notes, [])
        self.assertEqual(wiiu_models.self_check(wu), [])
        # one dictionary per Wii U kind: skeletal 1, shader param 1, colour 1, tex SRT 1, tex pattern 1
        self.assertEqual(struct.unpack_from(">12H", wu, 0x50)[:9], (1, 0, 1, 1, 1, 1, 1, 0, 0))
        for magic in (b"FSKA", b"FSHU", b"FTXP"):
            self.assertIn(magic, wu)
        # big-endian: the FSKA frame count, the first constant
        o = wu.find(b"FSKA")
        self.assertEqual(struct.unpack_from(">i", wu, o + 0x10)[0], 12)
        warn = []
        back = wiiu_models.read(wu, warn)
        self.assertEqual(warn, [])
        self.assertEqual(diff(res, back, "cross"), [])
        sw2 = nx_model.write(back)
        self.assertEqual(diff(res, nx_model.read(sw2), "cross"), [])
        self.assertEqual(wm_problems(sw2), [])
        # Wii U -> Wii U is a fixed point
        self.assertEqual(wiiu_models.write(back), wu)

    def test_material_anims_are_merged_and_sorted(self):
        res = anim_res()
        res.mat_anims = list(reversed(res.mat_anims))     # the Wii U file keeps them in four dictionaries
        back = wiiu_models.read(wiiu_models.write(res))
        self.assertEqual([a.name for a in back.mat_anims], sorted(a.name for a in res.mat_anims))
        # the Switch counts: param curves + constants at 0x70, patterns at 0x72, textures at 0x76
        by = {a.name: a for a in back.mat_anims}
        self.assertEqual(struct.unpack_from("<H", by["Test_Auto_fsp"].head, 16)[0], 5)
        self.assertEqual(struct.unpack_from("<HH", by["Test_Dye_ftp"].head, 18), (2, 0))
        self.assertEqual(struct.unpack_from("<H", by["Test_Dye_ftp"].head, 22)[0], 3)

    def test_curve_element_swaps(self):
        rnd = random.Random(2)
        for flags, unit_f, unit_k in ((0x00, 4, 4), (0x05, 2, 2), (0x1A, 1, 1), (0x60, 4, 4), (0x4A, 1, 1)):
            self.assertEqual(wiiu_anims.curve_units(flags), (unit_f, unit_k), hex(flags))
        c = curve(rnd, 0x05, 4)
        self.assertEqual(wiiu_anims.swap(c.frames, 2)[:2], c.frames[1::-1])
        self.assertEqual(wiiu_anims.first_int_key(step_curve([2, 0, 1], 5)), 2)
        self.assertIsNone(wiiu_anims.first_int_key(c))

    def test_constant_pattern_and_bad_base_data_are_warned(self):
        wu = bytearray(wiiu_models.write(anim_res()))
        marr, pinfo = ftxp_offsets(wu)
        bda = marr + 0x18 + struct.unpack_from(">i", wu, marr + 0x18)[0]
        struct.pack_into(">H", wu, bda, 7)                  # base texture 7, but the curve starts at 0
        warn = []
        back = wiiu_models.read(bytes(wu), warn)
        self.assertTrue(any("starts with texture 7 but its curve with 0" in w for w in warn), warn)
        wu[pinfo] = 0xFF                                    # no curve: a constant pattern
        warn = []
        back = wiiu_models.read(bytes(wu), warn)
        self.assertTrue(any("constant texture pattern(s) dropped" in w and "matA/_a0" in w for w in warn), warn)
        ftp = next(a for a in back.mat_anims if a.name == "Test_Dye_ftp")
        self.assertEqual([len(m.pattern_infos) for m in ftp.materials], [0, 1])
        self.assertEqual(wm_problems(nx_model.write(back)), [])
        wu[pinfo] = 5                                       # curve 5 of 1: damage
        with self.assertRaises(Damaged):
            wiiu_models.read(bytes(wu))

    def test_what_has_no_wiiu_form(self):
        res = anim_res()
        both = copy.deepcopy(res.mat_anims[0])
        both.materials[0].pattern_infos = [("_a0", struct.pack("<hHb3x", 0, 0xFFFF, -1))]
        self.assertIn("both param and texture pattern", wiiu_anims.unwritable(both))
        far = copy.deepcopy(res.skel_anims[0])
        far.bone_anims[2].head = struct.pack("<IBBBBii", 0, 0, 0, 0, 0, 300, 0)
        self.assertIn("u8", wiiu_anims.unwritable(far))
        const = copy.deepcopy(next(a for a in res.mat_anims if a.name.endswith("_ftp")))
        const.materials[0].pattern_infos = [("_a0", struct.pack("<hHb3x", -1, 0, -1))]
        self.assertIn("constant", wiiu_anims.unwritable(const))
        self.assertEqual(wiiu_anims.wiiu_group(res.mat_anims[1]), 4)       # Test_Color_fcl
        self.assertEqual(wiiu_anims.wiiu_group(res.mat_anims[3]), 5)       # Test_Uv_fts


def wm_problems(data):
    from wiiu_model import checks
    return checks.switch_model_problems(data)


# ------------------------------------------------------------------------------------------ donor and model file

class DonorTests(unittest.TestCase):
    def test_material_on_the_vanilla_one(self):
        rnd = random.Random(3)
        donor = make_model(rnd).materials[0]
        donor.shader.options.append(("opt_vanilla_only", "1"))
        mod = copy.deepcopy(donor)
        mod.shader.options = [o for o in mod.shader.options if o[0] != "opt_vanilla_only"]
        mod.shader.options[0] = ("opt_a", "9")
        mod.shader.options.append(("opt_wiiu_only", "1"))
        mod.textures[0] = "Mod_Alb"
        p = next(x for x in mod.params if x.name == "p_float")
        data = bytearray(mod.param_data)
        struct.pack_into("<f", data, p.offset, 42.0)
        mod.param_data = bytes(data)
        mod.render_infos.append(RenderInfo("wiiu_only_info", "int", (1,)))
        mod.samplers.append(copy.deepcopy(mod.samplers[0]))
        mod.samplers[-1].name = "_e0"
        mod.textures.append("Mod_Emm")
        mod.shader.samplers.append(("_e0", "_e0"))
        out, changed, dropped = materials.donor_material(mod, donor)
        self.assertEqual(out.textures, ["Mod_Alb", "TestTex_Nrm", "Mod_Emm"])
        self.assertIn(("opt_vanilla_only", "1"), out.shader.options)     # the vanilla value stays
        self.assertEqual(dict(out.shader.options)["opt_a"], "9")
        self.assertEqual(struct.unpack_from("<f", out.param_data, p.offset)[0], 42.0)
        self.assertIn(("_e0", "_e0"), out.shader.samplers)
        self.assertEqual(sorted(dropped), ["option opt_wiiu_only", "render info wiiu_only_info"])
        self.assertIn("sampler _e0 added", changed)
        self.assertEqual([r.name for r in out.render_infos], [r.name for r in donor.render_infos])
        # the 16 render-state infos only come from the mod when its RenderState differs: then all of them
        mod2 = copy.deepcopy(donor)
        for r in mod2.render_infos:
            if r.name == "gsys_render_state_mode":
                r.values = ("translucent",)
        out2, changed2, _ = materials.donor_material(mod2, donor)
        self.assertEqual(next(r.values for r in out2.render_infos if r.name == "gsys_render_state_mode"),
                         ("translucent",))
        out3, changed3, _ = materials.donor_material(copy.deepcopy(donor), donor)
        self.assertEqual((diff(out3, donor, "cross"), changed3), ([], []))

    def test_model_file_to_switch(self):
        rnd = random.Random(4)
        res = ResFile("TestBody", [make_model(rnd)], external=[ExtFile("TexInfo.txt", b"3")])
        wu = wiiu_models.write(res, keep_render_infos=False)     # as a real Wii U file: RenderState only
        notes = []
        out, back, info = convert.model_file_to_switch(wu, notes, tex_count=7)
        self.assertEqual(convert.texinfo(nx_model.read(out)), 7)
        self.assertEqual(info["alone_materials"], 2)
        donor = nx_model.read(nx_model.write(res))
        donor.models[0].materials[1].shader.options.append(("opt_vanilla_only", "x"))
        out, back, info = convert.model_file_to_switch(wu, notes, tex_count=None, donor=donor)
        self.assertEqual((info["donor_materials"], info["alone_materials"]), (2, 0))
        got = nx_model.read(out)
        self.assertIsNone(convert.texinfo(got))
        self.assertIn(("opt_vanilla_only", "x"), got.models[0].materials[1].shader.options)
        self.assertEqual(diff(res.models[0].materials[0], got.models[0].materials[0], "cross"), [])

    def test_rstb_rules(self):
        self.assertEqual(convert.rstb_tex(1000), 9208)
        self.assertEqual(convert.rstb_model(900, 10, 5000, 1000, 10), 5000)          # no bigger: vanilla entry
        self.assertEqual(convert.rstb_model(2000, 10, 5000, 1000, 10), 2000 + 6000)  # overhead 4000 x 1.5
        self.assertEqual(convert.rstb_model(2000, 20, 5000, 1000, 10), 2000 + 12000)  # twice the parts
        self.assertEqual(convert.rstb_model(1100, 10, 100000, 1000, 10), 1100 + 148500)
        self.assertEqual(wc.resource_path("romfs", "Model/A.Tex.sbfres"), "Model/A.Tex.bfres")
        self.assertEqual(wc.resource_path(wc.DLC_OUT, "Model/A.sbfres"), "Aoc/0010/Model/A.bfres")


# ------------------------------------------------------------------------------------------ the mod converter

def tiny_byml(order, key="Hp", value=7):
    def u24(n):
        return n.to_bytes(3, "big" if order == BE else "little")
    name = key.encode() + b"\0"
    table = b"\xc2" + u24(1) + struct.pack(order + "II", 12, 12 + len(name)) + name
    table += bytes(-len(table) % 4)
    root = 0x10 + len(table)
    return ((b"BY" if order == BE else b"YB") + struct.pack(order + "HIII", 2, 0x10, 0, root) + table
            + b"\xc1" + u24(1) + u24(0) + b"\xd1" + struct.pack(order + "i", value))


def write_tree(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def model_res(name, seed):
    rnd = random.Random(seed)
    m = make_model(rnd, name) if name == "TestBody" else make_small_model(rnd, name)
    return ResFile(name, [m], skel_anims=[], mat_anims=[])


def tex_set(name, seed=1, mips=True):
    ts = made_up_set(name)
    ts.textures = ts.textures[:3]
    if not mips:
        for t in ts.textures:
            t.levels = t.levels[:1]
    return ts


def plain(data):
    return wc.yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data


class ModConverterTests(unittest.TestCase):
    """wiiu_convert.py with Wii U model and texture files (made-up mod, made-up game folder)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        body_tex1, body_tex2 = wiiu_bfres.write_textures(tex_set("TestBody"))
        prop_tex1, prop_tex2 = wiiu_bfres.write_textures(tex_set("TestProp"))
        single1, _ = wiiu_bfres.write_textures(tex_set("Single"))
        _, lone2 = wiiu_bfres.write_textures(tex_set("Lone"))
        self.body = model_res("TestBody", 1)
        self.prop = model_res("TestProp", 2)
        pack = wc.sarc_build([("Model/TestProp.sbfres", wiiu_models.write(self.prop, keep_render_infos=False)),
                              ("Model/TestProp.Tex1.sbfres", prop_tex1), ("Model/TestProp.Tex2.sbfres", prop_tex2),
                              ("Actor/X.byml", tiny_byml(BE))], BE)
        self.mod = write_tree(self.tmp / "Model Mod", {
            "content/Model/TestBody.sbfres": wc.yaz0_compress(wiiu_models.write(self.body, keep_render_infos=False)),
            "content/Model/TestBody.Tex1.sbfres": body_tex1,
            "content/Model/TestBody.Tex2.sbfres": body_tex2,
            "content/Model/Single.Tex1.sbfres": single1,
            "content/Model/Lone.Tex2.sbfres": lone2,
            "content/Pack/TitleBG.pack": pack,
            # a Wii U size table (left out; with --game the game's one is written in its place)
            "content/" + wc.RSTB_PATH: b"RSTB" + struct.pack(">IIII", 1, 0, 0x1234, 0x100),
        })

    def make_game(self) -> Path:
        """A made-up Switch RomFS: vanilla TestBody (with an option the mod lacks), Single.Tex with the same main
        images and full mipmaps, a TitleBG.pack with TestProp and one more file, and a size table."""
        vanilla = copy.deepcopy(self.body)
        vanilla.models[0].materials[0].shader.options.append(("opt_vanilla_only", "1"))
        vanilla.external = [ExtFile("TexInfo.txt", b"3")]
        single = tex_set("Single")
        prop_sw = copy.deepcopy(self.prop)
        prop_sw.external = [ExtFile("TexInfo.txt", b"3")]
        mat = prop_sw.models[0].materials[0]                     # (the mod changed a param of this one)
        mat.param_data = struct.pack("<f", 9.5) * 2 + mat.param_data[8:]
        pack = wc.sarc_build([("Model/TestProp.sbfres", nx_model.write(prop_sw)),
                              ("Model/TestProp.Tex.sbfres", nx_bfres.write_textures(tex_set("TestProp"))),
                              ("Actor/X.byml", tiny_byml(LE)), ("Actor/OnlyInTheGame.byml", tiny_byml(LE, "G", 3))],
                             LE)
        self.vanilla_body = nx_model.write(vanilla)
        crc = [(zlib.crc32(b"Model/TestBody.bfres"), len(self.vanilla_body) + 50000),
               (zlib.crc32(b"Model/Single.Tex.bfres"), 10)]
        rstb = wc.yaz0_compress(wc.rstb_build(crc, []))
        return write_tree(self.tmp / "game" / "romfs", {
            "Model/TestBody.sbfres": wc.yaz0_compress(self.vanilla_body),
            "Model/Single.Tex.sbfres": nx_bfres.write_textures(single),
            "Pack/TitleBG.pack": pack,
            wc.RSTB_PATH: rstb,
        })

    def test_convert_without_the_game(self):
        out = self.tmp / "out"
        rep = wc.convert_mod(str(self.mod), str(out))
        self.assertEqual(rep.verdict(), "converted in part")              # (the lone Tex2 stays Wii U data)
        files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
        self.assertEqual(files, ["romfs/Model/Lone.Tex2.sbfres", "romfs/Model/Single.Tex.sbfres",
                                 "romfs/Model/TestBody.Tex.sbfres", "romfs/Model/TestBody.sbfres",
                                 "romfs/Pack/TitleBG.pack", wc.REPORT])
        # the model: every value as written, TexInfo.txt = the Tex1's texture count; Yaz0 alignment field 0
        raw = (out / "romfs/Model/TestBody.sbfres").read_bytes()
        self.assertEqual(struct.unpack_from(">I", raw, 8)[0], 0)
        got = nx_model.read(plain(raw))
        want = copy.deepcopy(self.body)
        want.external = [ExtFile("TexInfo.txt", b"3")]
        self.assertEqual(diff(want, got, "cross"), [])
        # the textures: joined Tex1 + Tex2, every mip
        ts = nx_bfres.read_textures((out / "romfs/Model/TestBody.Tex.sbfres").read_bytes())
        orig = tex_set("TestBody")
        self.assertEqual([t.levels for t in ts.textures], [t.levels for t in orig.textures])
        # no Tex2 and no game: one mip level, with a warning under "Needs attention"
        single = nx_bfres.read_textures((out / "romfs/Model/Single.Tex.sbfres").read_bytes())
        self.assertEqual({t.mips for t in single.textures}, {1})
        text = rep.text()
        self.assertIn("Needs attention (", text)
        self.assertIn("content/Model/Single.Tex1.sbfres: texture", text)
        self.assertIn("no mipmaps for 3 texture(s)", text)
        self.assertIn("content/Model/Lone.Tex2.sbfres: texture - mipmaps only (Lone.Tex2)", text)
        self.assertIn("without your game files (--game)", text)
        # the pack: renamed, the Tex2 gone, the BFRES entries 0x1000-aligned, the BYML converted
        pack = (out / "romfs/Pack/TitleBG.pack").read_bytes()
        s = wc.sarc_parse(pack)
        self.assertEqual(s.order, LE)
        names = {n.name: (s.data_offset + n.start, pack[s.data_offset + n.start:s.data_offset + n.end])
                 for n in s.nodes}
        self.assertEqual(sorted(names), ["Actor/X.byml", "Model/TestProp.Tex.sbfres", "Model/TestProp.sbfres"])
        for n in ("Model/TestProp.Tex.sbfres", "Model/TestProp.sbfres"):
            self.assertEqual(names[n][0] % 0x1000, 0, n)
        self.assertEqual(names["Actor/X.byml"][1], tiny_byml(LE))
        self.assertEqual(convert.texinfo(nx_model.read(names["Model/TestProp.sbfres"][1])), 3)
        # report rows: the joined Tex2 counts as converted and is not written
        by = {f["path"]: f for f in rep.files}
        self.assertEqual(by["content/Model/TestBody.Tex2.sbfres"]["status"], wc.CONVERTED)
        self.assertEqual(by["content/Model/TestBody.Tex1.sbfres"]["target"], "romfs/Model/TestBody.Tex.sbfres")
        self.assertEqual(wc.report_counts(text), {**rep.counts(), "verdict": rep.verdict()})
        self.assertEqual(rep.counts()["attention"], len(rep.attention()))

    def test_convert_with_the_game(self):
        game = self.make_game()
        out = self.tmp / "out"
        rep = wc.convert_mod(str(self.mod), str(out), game=str(game))
        # materials on the vanilla ones (the option only the game's model has is there)
        got = nx_model.read(plain((out / "romfs/Model/TestBody.sbfres").read_bytes()))
        self.assertIn(("opt_vanilla_only", "1"), got.models[0].materials[0].shader.options)
        # Single.Tex1 without a Tex2: main images unchanged from the game's, so the game's mipmaps are kept; the
        # result holds the game's values, so the game's own file is written (and its size table entry stays)
        single = nx_bfres.read_textures((out / "romfs/Model/Single.Tex.sbfres").read_bytes())
        self.assertEqual([t.levels for t in single.textures], [t.levels for t in tex_set("Single").textures])
        self.assertEqual((out / "romfs/Model/Single.Tex.sbfres").read_bytes(),
                         (game / "Model/Single.Tex.sbfres").read_bytes())
        # the pack holds converted models: the file only the game's pack has is filled in
        pack = (out / "romfs/Pack/TitleBG.pack").read_bytes()
        names = {n.name for n in wc.sarc_parse(pack).nodes}
        self.assertIn("Actor/OnlyInTheGame.byml", names)
        text = rep.text()
        self.assertIn("pack filled: content/Pack/TitleBG.pack: 1 file(s) from your game's Pack/TitleBG.pack", text)
        # the size table: X.Tex entries = size + 8208; the model's vanilla entry is big enough and stays; Single.Tex is
        # the game's own file, so its entry stays too
        rstb = (out / "romfs" / wc.RSTB_PATH).read_bytes()
        crcs, _ = wc.rstb_load(plain(rstb))
        table = dict(crcs)
        self.assertEqual(table[zlib.crc32(b"Model/Single.Tex.bfres")], 10)
        # TestBody, TestProp.Tex and Single.Tex hold the game's values: written as the game's own files
        self.assertIn("3 file(s) came out the same as your game's", text)
        self.assertEqual((out / "romfs/Model/TestBody.sbfres").read_bytes(),
                         (game / "Model/TestBody.sbfres").read_bytes())
        self.assertEqual(table[zlib.crc32(b"Model/TestBody.bfres")], len(self.vanilla_body) + 50000)
        self.assertEqual(table[zlib.crc32(b"Model/TestBody.Tex.bfres")],
                         len(plain((out / "romfs/Model/TestBody.Tex.sbfres").read_bytes())) + 8208)
        self.assertIn("size table: romfs/System/Resource/ResourceSizeTable.product.srsizetable", text)
        self.assertIn("no entry for 1 new model file(s) (Model/TestProp.bfres)", text)

    def test_vanilla_twin_in_the_same_pack(self):
        """A model in a pack other than TitleBG.pack (a dungeon pack, say) finds its vanilla twin in the game's pack of
        the same path; the converted BFRES is 0x1000-aligned even when no file name changes."""
        prop_wu = wiiu_models.write(self.prop, keep_render_infos=False)
        mod = write_tree(self.tmp / "Dungeon Mod", {
            "content/Pack/Dungeon001.pack": wc.sarc_build([("Map/A.byml", tiny_byml(BE)),
                                                           ("Model/TestProp.sbfres", prop_wu)], BE)})
        vanilla = copy.deepcopy(self.prop)
        vanilla.models[0].materials[0].shader.options.append(("opt_dungeon", "1"))
        vanilla.external = [ExtFile("TexInfo.txt", b"5")]
        game = write_tree(self.tmp / "game2" / "romfs", {
            "Model/Readme.txt": b"made up",
            "Pack/Dungeon001.pack": wc.sarc_build([("Map/A.byml", tiny_byml(LE)),
                                                   ("Model/TestProp.sbfres", nx_model.write(vanilla))], LE)})
        out = self.tmp / "out2"
        rep = wc.convert_mod(str(mod), str(out), game=str(game))
        self.assertEqual(rep.verdict(), "everything converted")
        pack = (out / "romfs/Pack/Dungeon001.pack").read_bytes()
        s = wc.sarc_parse(pack)
        at = {n.name: s.data_offset + n.start for n in s.nodes}
        self.assertEqual(at["Model/TestProp.sbfres"] % 0x1000, 0)
        got = nx_model.read(dict(wc.sarc_files(pack))["Model/TestProp.sbfres"])
        self.assertIn(("opt_dungeon", "1"), got.models[0].materials[0].shader.options)
        self.assertEqual(convert.texinfo(got), 5)        # (the mod has no Tex1: the vanilla model's TexInfo.txt)

    def test_check_names_what_converts(self):
        rep = wc.check_mod(str(self.mod))
        st = {f["path"].rsplit("/", 1)[-1]: (f["status"], f["kind"]) for f in rep.files}
        self.assertEqual(st["TestBody.sbfres"], (wc.CONVERTED, "model"))
        self.assertEqual(st["TestBody.Tex1.sbfres"], (wc.CONVERTED, "texture"))
        self.assertEqual(st["Lone.Tex2.sbfres"], (wc.CANT, "texture"))
        self.assertEqual(rep.exit_code(), 1)

    def test_switch_and_damaged_bfres(self):
        conv = wc.Converter(LE)
        sw = nx_model.write(model_res("TestProp", 3))
        self.assertEqual(conv.file(sw, "Model/A.sbfres").status, wc.ALREADY)
        bad = bytearray(wiiu_models.write(model_res("TestProp", 3)))
        bad[0x60:0x80] = b"\x7f" * 0x20
        r = conv.file(bytes(bad), "Model/B.sbfres")
        self.assertIn(r.status, (wc.BROKEN, wc.CANT))
        self.assertEqual(r.data, bytes(bad))                  # kept as it was
        old = wiiu_models.write(model_res("TestProp", 3))
        old = old[:4] + struct.pack(">I", 0x03040001) + old[8:]
        self.assertEqual(conv.file(old, "Model/C.sbfres").status, wc.CANT)

    def test_renamed_file_never_overwrites(self):
        tex1, tex2 = wiiu_bfres.write_textures(tex_set("Dup"))
        mod = write_tree(self.tmp / "Dup Mod", {"content/Model/Dup.Tex1.sbfres": tex1,
                                                 "content/Model/Dup.Tex.sbfres": nx_bfres.write_textures(tex_set("Dup"))})
        rep = wc.check_mod(str(mod))
        by = {f["path"].rsplit("/", 1)[-1]: f for f in rep.files}
        self.assertEqual(by["Dup.Tex1.sbfres"]["status"], wc.BROKEN)
        self.assertIn("the mod also has romfs/Model/Dup.Tex.sbfres", by["Dup.Tex1.sbfres"]["detail"])


# ------------------------------------------------------------------------------- review fixes (Integrate milestone)

def named_tex(names, name="TestBody"):
    """made_up_set with the textures renamed (as many textures as names)."""
    ts = made_up_set(name)
    ts.textures = ts.textures[:len(names)]
    for t, n in zip(ts.textures, names):
        t.name = n
    return ts


def bad_tex1(tex1: bytes) -> bytes:
    """A Tex1 whose names read but whose first texture has an unknown GX2 format: it can't be converted."""
    out = bytearray(tex1)
    struct.pack_into(">I", out, out.find(b"FTEX") + 0x18, 0x3F)
    return bytes(out)


def anim_file(name="Test_Animation", seed=5):
    """An animation file: a skeleton-only model (no vertex data, no materials) and animations."""
    res = anim_res(seed)
    m = res.models[0]
    res.models[0] = type(m)(m.name, m.path, m.skeleton, [], [], [], [], 0)
    res.name = name
    return res


def body_game(tmp: Path, extra: dict | None = None, mat_tex=("TestTex_Alb", "TestTex_Nrm")) -> Path:
    """A made-up game: TestBody (TexInfo = the texture count) and a TestBody.Tex holding mat_tex."""
    vanilla = model_res("TestBody", 1)
    vanilla.external = [ExtFile("TexInfo.txt", str(len(mat_tex)).encode())]
    return write_tree(tmp / "game" / "romfs", {"Model/TestBody.sbfres": wc.yaz0_compress(nx_model.write(vanilla)),
                                               "Model/TestBody.Tex.sbfres": nx_bfres.write_textures(named_tex(mat_tex)),
                                               **(extra or {})})


def warnings_of(rep):
    return [w for n in rep.attention() for w in n.warnings]


def refused_model(name, seed):
    """A Wii U model of a BFRES version the converter refuses."""
    wu = wiiu_models.write(model_res(name, seed))
    return wu[:4] + struct.pack(">I", 0x03040001) + wu[8:]


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    # 1: the missing-texture check and TexInfo.txt look at the X.Tex that ships
    def test_missing_textures_when_the_mods_x_tex_replaces_the_games(self):
        game = body_game(self.tmp)
        body = wiiu_models.write(model_res("TestBody", 1), keep_render_infos=False)
        t1, t2 = wiiu_bfres.write_textures(named_tex(["Other_Alb", "Other_Nrm"]))
        mod = write_tree(self.tmp / "m1", {"content/Model/TestBody.sbfres": body,
                                           "content/Model/TestBody.Tex1.sbfres": t1,
                                           "content/Model/TestBody.Tex2.sbfres": t2})
        w = warnings_of(wc.check_mod(str(mod), str(game)))
        self.assertTrue(any("2 texture name(s) are in no X.Tex the game loads" in x
                            and "TestTex_Alb, TestTex_Nrm" in x for x in w), w)
        # the mod's X.Tex1 can't be converted: your game's X.Tex ships, so its names count and give TexInfo.txt
        mod = write_tree(self.tmp / "m2", {"content/Model/TestBody.sbfres": body,
                                           "content/Model/TestBody.Tex1.sbfres": bad_tex1(t1)})
        rep = wc.check_mod(str(mod), str(game))
        by = {f["path"].rsplit("/", 1)[-1]: f for f in rep.files}
        self.assertEqual(by["TestBody.Tex1.sbfres"]["status"], wc.CANT)
        self.assertIn("TexInfo.txt 2 (your game's TestBody.Tex: the mod's TestBody.Tex1.sbfres can't be converted)",
                      by["TestBody.sbfres"]["detail"])
        self.assertFalse(any("texture name(s)" in x for x in warnings_of(rep)))
        # without the game files: no TexInfo.txt from a Tex1 that doesn't ship
        rep = wc.check_mod(str(mod))
        self.assertTrue(any("TexInfo.txt not written: the mod's TestBody.Tex1.sbfres can't be converted" in x
                            for x in warnings_of(rep)))
        # a name your vanilla model uses that no X.Tex of your game holds (terrain textures, say) is not flagged
        game2 = body_game(self.tmp / "g2", mat_tex=("TestTex_Alb",))
        t1, t2 = wiiu_bfres.write_textures(named_tex(["TestTex_Alb"]))
        mod = write_tree(self.tmp / "m3", {"content/Model/TestBody.sbfres": body,
                                           "content/Model/TestBody.Tex1.sbfres": t1,
                                           "content/Model/TestBody.Tex2.sbfres": t2})
        self.assertFalse(any("texture name(s)" in x for x in warnings_of(wc.check_mod(str(mod), str(game2)))))

    # 2: the samplers are the mod's
    def test_donor_material_honours_removed_samplers_and_changed_states(self):
        donor = make_model(random.Random(3)).materials[0]          # samplers _a0, _n0
        donor.samplers[0].state.extra["nx.tail"] = b"\1" * 12
        mod = copy.deepcopy(donor)
        mod.samplers, mod.textures = mod.samplers[:1], mod.textures[:1]
        mod.shader.samplers = [p for p in mod.shader.samplers if p[1] != "_n0"]
        out, changed, dropped = materials.donor_material(mod, donor)
        self.assertEqual(([s.name for s in out.samplers], out.textures), (["_a0"], ["TestTex_Alb"]))
        self.assertNotIn(("_n0", "_n0"), out.shader.samplers)
        self.assertIn("sampler _n0 removed", changed)
        self.assertEqual(out.samplers[0].state.extra, {"nx.tail": b"\1" * 12})   # unchanged: the vanilla sampler
        # the mod's sampler read from GX2 words: the same values keep the vanilla sampler, a new wrap mode is kept
        st = materials.sampler_from_gx2(materials.sampler_to_gx2(donor.samplers[0].state))
        mod.samplers[0].state = st
        out, changed, _ = materials.donor_material(mod, donor)
        self.assertEqual(out.samplers[0].state, donor.samplers[0].state)
        st = copy.deepcopy(st)
        st.wrap_u = 2
        st.extra.clear()
        mod.samplers[0].state = materials.sampler_from_gx2(materials.sampler_to_gx2(st))
        out, changed, _ = materials.donor_material(mod, donor)
        self.assertEqual(out.samplers[0].state.wrap_u, 2)
        self.assertIn("sampler _a0 values", changed)
        # an anisotropic sampler (filter 0x6a, anisotropy 2: GX2 says it with its ratio): the same values keep yours, and
        # the warning about the missing anisotropic bit goes
        aniso = SamplerState(0, 0, 0, 0, 0, 2, 0x6A, 0.0, 13.0, 0.0)
        st = materials.sampler_from_gx2(materials.sampler_to_gx2(aniso))
        self.assertEqual((st.filter, st.max_aniso), (0x2A, 2))
        self.assertTrue(materials.same_sampler_state(st, aniso))
        donor_m = make_model(random.Random(3))
        donor_m.materials[0].samplers[0].state = aniso
        mod_m = copy.deepcopy(donor_m)
        mod_m.materials[0].samplers[0].state = st
        warn = materials.sampler_notes(st, "material 'matA' sampler '_a0'")
        self.assertEqual(len(warn), 1)
        materials.donor_overlay(mod_m, donor_m, warn)
        self.assertEqual((mod_m.materials[0].samplers[0].state, warn), (aniso, []))
        # the mod's order
        mod = copy.deepcopy(donor)
        mod.samplers.reverse()
        mod.textures.reverse()
        out, _, _ = materials.donor_material(mod, donor)
        self.assertEqual(([s.name for s in out.samplers], out.textures),
                         (["_n0", "_a0"], ["TestTex_Nrm", "TestTex_Alb"]))
        # through the converter: the removed sampler stays removed, a warning says so, the X.Tex has what is sampled
        game = body_game(self.tmp)
        res = model_res("TestBody", 1)
        for mat in res.models[0].materials:
            keep = [i for i, smp in enumerate(mat.samplers) if smp.name != "_n0"]
            mat.samplers, mat.textures = [mat.samplers[i] for i in keep], [mat.textures[i] for i in keep]
            mat.shader.samplers = [p for p in mat.shader.samplers if p[1] != "_n0"]
        t1, t2 = wiiu_bfres.write_textures(named_tex(["TestTex_Alb"]))
        wu = wiiu_models.write(res, keep_render_infos=False)
        src = write_tree(self.tmp / "m", {"content/Model/TestBody.sbfres": wu,
                                          "content/Model/TestBody.Tex1.sbfres": t1,
                                          "content/Model/TestBody.Tex2.sbfres": t2})
        out_dir = self.tmp / "out"
        rep = wc.convert_mod(str(src), str(out_dir), game=str(game))
        got = nx_model.read(plain((out_dir / "romfs/Model/TestBody.sbfres").read_bytes()))
        self.assertEqual(got.models[0].materials[0].textures, ["TestTex_Alb"])
        self.assertEqual(convert.texinfo(got), 1)
        w = warnings_of(rep)
        self.assertTrue(any("the mod's lacks were left out: _n0" in x for x in w), w)
        self.assertFalse(any("texture name(s)" in x for x in w), w)

    # 3: a model the converter refuses doesn't stay as Wii U bytes where the Switch game loads it
    def test_refused_models_give_way_to_the_games_files(self):
        a, b = model_res("TestA", 1), model_res("TestB", 2)
        t1, t2 = wiiu_bfres.write_textures(tex_set("TestD"))
        mod = write_tree(self.tmp / "mod", {
            "content/Pack/TitleBG.pack": wc.sarc_build(
                [("Model/TestA.sbfres", wiiu_models.write(a)), ("Model/TestB.sbfres", refused_model("TestB", 2)),
                 ("Model/TestC.sbfres", refused_model("TestC", 3)), ("Model/TestD.Tex1.sbfres", bad_tex1(t1)),
                 ("Model/TestD.Tex2.sbfres", t2)], BE),
            "content/Model/TestE.sbfres": wc.yaz0_compress(refused_model("TestE", 4)),
            "content/Model/TestF.sbfres": refused_model("TestF", 5)})
        game_b = wc.yaz0_compress(nx_model.write(b))
        game = write_tree(self.tmp / "game" / "romfs", {
            "Model/TestE.sbfres": nx_model.write(model_res("TestE", 4)),
            "Pack/TitleBG.pack": wc.sarc_build(
                [("Model/TestA.sbfres", nx_model.write(a)), ("Model/TestB.sbfres", game_b),
                 ("Model/TestD.Tex.sbfres", nx_bfres.write_textures(tex_set("TestD")))], LE)})
        out = self.tmp / "out"
        rep = wc.convert_mod(str(mod), str(out), game=str(game))
        files = dict(wc.sarc_files((out / "romfs/Pack/TitleBG.pack").read_bytes()))
        self.assertEqual(files["Model/TestB.sbfres"], game_b)                   # your game's file in its place
        self.assertNotIn("Model/TestC.sbfres", files)                           # no twin: left out of the pack
        self.assertNotIn("Model/TestD.Tex1.sbfres", files)                      # the Switch never loads Tex1/Tex2
        self.assertNotIn("Model/TestD.Tex2.sbfres", files)
        self.assertIn("Model/TestD.Tex.sbfres", files)                          # filled from your game's pack
        self.assertFalse(any(d[8:10] == b"\xfe\xff" for d in files.values()))  # no Wii U BFRES left in the pack
        self.assertFalse((out / "romfs/Model/TestE.sbfres").exists())           # loose, your game has it: left out
        self.assertTrue((out / "romfs/Model/TestF.sbfres").exists())            # loose, no twin: kept and listed
        by = {f["path"]: f["status"] for f in rep.files}
        self.assertEqual((by["content/Model/TestE.sbfres"], by["content/Model/TestF.sbfres"]), (wc.REPLACED, wc.CANT))
        self.assertEqual(rep.verdict(), "converted in part")
        self.assertEqual([n.path for n in rep.problems()], ["content/Model/TestF.sbfres"])
        att = {n.path.rsplit("/", 1)[-1] for n in rep.attention()}
        self.assertTrue({"TestB.sbfres", "TestC.sbfres", "TestD.Tex1.sbfres", "TestE.sbfres"} <= att, att)
        text = rep.text()
        self.assertIn("your game's file", text)
        self.assertEqual(wc.report_counts(text), {**rep.counts(), "verdict": rep.verdict()})

    # 4: files that come out as the game's are the game's bytes (nothing compressed); an animation file's vanilla twin
    # is not read as a model
    def test_files_equal_to_the_games_are_its_own_bytes(self):
        prop = model_res("TestProp", 2)
        prop.external = [ExtFile("TexInfo.txt", b"3")]
        anim = anim_file()
        pack_game = wc.sarc_build(
            [("Model/TestProp.sbfres", wc.yaz0_compress(nx_model.write(prop))),
             ("Model/TestProp.Tex.sbfres", wc.yaz0_compress(nx_bfres.write_textures(tex_set("TestProp")))),
             ("Actor/X.byml", wc.yaz0_compress(tiny_byml(LE)))], LE)
        game = write_tree(self.tmp / "game" / "romfs", {
            "Model/Test_Animation.sbfres": wc.yaz0_compress(nx_model.write(anim)), "Pack/TitleBG.pack": pack_game})
        t1, t2 = wiiu_bfres.write_textures(tex_set("TestProp"))
        mod = write_tree(self.tmp / "mod", {
            "content/Model/Test_Animation.sbfres": wc.yaz0_store(wiiu_models.write(anim, keep_render_infos=False)),
            "content/Pack/TitleBG.pack": wc.sarc_build([
                ("Model/TestProp.sbfres", wc.yaz0_store(wiiu_models.write(prop, keep_render_infos=False))),
                ("Model/TestProp.Tex1.sbfres", wc.yaz0_store(t1)), ("Model/TestProp.Tex2.sbfres", wc.yaz0_store(t2)),
                ("Actor/X.byml", wc.yaz0_store(tiny_byml(BE)))], BE)})
        job = wc.ModelJob(wc.GameFiles(str(game)))
        out = {}
        with mock.patch.object(wc, "yaz0_compress", side_effect=AssertionError("compressed")):
            rep = wc._run(str(mod), wc.Converter(LE, models=job), collect=out)
        self.assertEqual(out["romfs/Model/Test_Animation.sbfres"], (game / "Model/Test_Animation.sbfres").read_bytes())
        self.assertEqual(dict(wc.sarc_files(out["romfs/Pack/TitleBG.pack"])), dict(wc.sarc_files(pack_game)))
        self.assertNotIn(("model/test_animation.sbfres", None), job.game._donors)   # only its size was read
        self.assertEqual(rep.models["same"], 4)
        self.assertIn("4 file(s) came out the same as your game's", rep.text())
        self.assertEqual(rep.attention(), [])          # (notes on converting don't apply to the game's own files)
        self.assertEqual(rep.verdict(), "everything converted")
        # a changed animation file is converted and compressed as before
        anim2 = anim_file(seed=6)
        mod2 = write_tree(self.tmp / "mod2", {"content/Model/Test_Animation.sbfres":
                                              wc.yaz0_store(wiiu_models.write(anim2, keep_render_infos=False))})
        out = {}
        rep = wc._run(str(mod2), wc.Converter(LE, models=wc.ModelJob(wc.GameFiles(str(game)))), collect=out)
        new = out["romfs/Model/Test_Animation.sbfres"]
        self.assertEqual(diff(anim2, nx_model.read(plain(new)), "cross"), [])
        self.assertEqual(rep.models["same"], 0)

    # 5: a texture-only mod with another texture count than your model's TexInfo.txt
    def test_texture_only_mod_rewrites_the_models_texinfo(self):
        prop_game = model_res("TestProp", 2)
        prop_game.external = [ExtFile("TexInfo.txt", b"2")]
        game = body_game(self.tmp, {"Pack/TitleBG.pack": wc.sarc_build([
            ("Model/TestProp.sbfres", nx_model.write(prop_game)),
            ("Model/TestProp.Tex.sbfres", nx_bfres.write_textures(named_tex(["A", "B"], "TestProp")))], LE)})
        t1, t2 = wiiu_bfres.write_textures(named_tex(["TestTex_Alb", "TestTex_Nrm", "TestTex_Extra"]))
        p1, p2 = wiiu_bfres.write_textures(named_tex(["A", "B", "C"], "TestProp"))
        mod = write_tree(self.tmp / "mod", {
            "content/Model/TestBody.Tex1.sbfres": t1, "content/Model/TestBody.Tex2.sbfres": t2,
            "content/Pack/TitleBG.pack": wc.sarc_build([("Model/TestProp.Tex1.sbfres", p1),
                                                        ("Model/TestProp.Tex2.sbfres", p2)], BE)})
        out = self.tmp / "out"
        rep = wc.convert_mod(str(mod), str(out), game=str(game))
        loose = nx_model.read(plain((out / "romfs/Model/TestBody.sbfres").read_bytes()))
        want = nx_model.read(plain((game / "Model/TestBody.sbfres").read_bytes()))
        want.external = [ExtFile("TexInfo.txt", b"3")]
        self.assertEqual(diff(want, loose, "cross"), [])
        pack = dict(wc.sarc_files((out / "romfs/Pack/TitleBG.pack").read_bytes()))
        self.assertEqual(convert.texinfo(nx_model.read(plain(pack["Model/TestProp.sbfres"]))), 3)
        text = rep.text()
        self.assertIn("written again: romfs/Model/TestBody.sbfres: your game's TestBody model (TexInfo.txt 2 -> 3",
                      text)
        self.assertIn("written again: content/Pack/TitleBG.pack//Model/TestProp.sbfres", text)
        # a model your game keeps where the mod's X.Tex isn't: a warning only
        mod = write_tree(self.tmp / "mod2", {"content/Model/TestProp.Tex1.sbfres": p1,
                                             "content/Model/TestProp.Tex2.sbfres": p2})
        rep = wc.check_mod(str(mod), str(game))
        self.assertTrue(any("TexInfo.txt 2 but this TestProp.Tex has 3 textures" in x for x in warnings_of(rep)))

    # 6: verify's scenario B sees the mod's changes (and fails when they are lost)
    def test_verify_b_checks_the_mods_changes(self):
        game = body_game(self.tmp)
        files = wc.GameFiles(str(game))
        row = wc._verify_model(files, "models with X.Tex1 + X.Tex2", "Model/TestBody.sbfres")
        self.assertEqual(row["problems"], [])
        self.assertEqual(row["b_changed"], ["matA/p_float", "TestTex_Alb main image"])
        self.assertEqual(row["b_mips"], {"TestTex_Alb": 1})

        def lost(mod, donor):                       # a converter that drops the mod's values
            return copy.deepcopy(donor), [], []
        with mock.patch.object(materials, "donor_material", lost):
            row = wc._verify_model(files, "models with X.Tex1 + X.Tex2", "Model/TestBody.sbfres")
        self.assertTrue(any(p.startswith("B model") for p in row["problems"]), row["problems"])
        # the whole-pack group on a made-up TitleBG.pack: every file comes back as the game's own bytes
        prop = model_res("TestProp", 2)
        prop.external = [ExtFile("TexInfo.txt", b"3")]
        write_tree(game, {"Pack/TitleBG.pack": wc.sarc_build([
            ("Model/TestProp.sbfres", wc.yaz0_compress(nx_model.write(prop))),
            ("Model/TestProp.Tex.sbfres", nx_bfres.write_textures(tex_set("TestProp"))),
            ("Actor/X.byml", tiny_byml(LE))], LE)})
        row = wc._verify_titlebg(wc.GameFiles(str(game)), say=lambda s: None)
        self.assertEqual(row["problems"], [])
        self.assertEqual((row["bfres"], row["wiiu_files"], row["vanilla_bytes"]), (2, 3, 2))


class InspectTests(unittest.TestCase):
    def test_wiiu_models_convert(self):
        low = ["content/model/a.sbfres", "content/model/a.tex1.sbfres", "content/model/a.tex2.sbfres",
               "content/model/b.tex1.sbfres", "content/model/c.tex2.sbfres", "content/sound/s.bfstm"]
        models, no_mips, lone = mi.wiiu_model_files(low)
        self.assertEqual((len(models), no_mips, lone),
                         (5, ["content/model/b.tex1.sbfres"], ["content/model/c.tex2.sbfres"]))
        f = mi._wiiu_data(low, [])
        self.assertEqual(f.verdict, mi.CONVERT)
        self.assertIn("4 model/texture file(s)", f.detail)
        self.assertIn("1 X.Tex1 without its X.Tex2", f.detail)
        self.assertIn("incl. 1 X.Tex2 without its X.Tex1", f.detail)
        self.assertEqual(mi.WIIU_MODEL_EXT, wc.MODEL_EXT)
        only_lone = mi._wiiu_data(["content/model/c.tex2.sbfres"], [])
        self.assertEqual(only_lone.verdict, mi.LATER)


if __name__ == "__main__":
    unittest.main()
