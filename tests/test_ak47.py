"""Tests for tools/ak47_build.py (the AK-47 that comes with the multiplayer mod) and modman's [mod] part_of. All data is
made up here in code: a tiny glTF rifle, a made-up "claymore" (model, textures, icon, actor pack, actor info, flags,
size table), a made-up map unit and terrain, in temp folders. No game files."""
import contextlib
import io
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import tomllib
import unittest
import zlib
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import aamp  # noqa: E402
import ak47_build as ak  # noqa: E402
import bcml_data as bd  # noqa: E402
import bcml_maps as bm  # noqa: E402
import bcml_texts as bt  # noqa: E402
import bnp  # noqa: E402
import menu_bg  # noqa: E402
import modman  # noqa: E402
import wiiu_convert as wc  # noqa: E402
from wiiu_model import formats, nx_bfres, nx_model, read_model  # noqa: E402
from wiiu_model.neutral import (Attribute, Bone, ExtFile, Material, Mesh, Model, Param, RenderInfo,  # noqa: E402
                                ResFile, Sampler, SamplerState, ShaderAssign, Shape, Skeleton, Texture, TextureSet,
                                VertexBuffer)

DONOR, ACTOR = "TestSword", "TestSword_AK"
SPOT = {"unit": "D-6_Dynamic", "x": -1364.0, "z": 1755.5, "lift": 0.25, "yaw": 35.0}
PEAK = 295.5          # the made-up hill's top (m)


# --------------------------------------------------------------------------------------------- a made-up rifle (glTF)

def box(x0, x1, y0, y1, z0, z1):
    """(positions, normals, uvs, indices) of a box: 4 vertices a face, outward normals, counter-clockwise."""
    faces = [((1, 0, 0), [(x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)]),
             ((-1, 0, 0), [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)]),
             ((0, 1, 0), [(x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0)]),
             ((0, -1, 0), [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]),
             ((0, 0, 1), [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
             ((0, 0, -1), [(x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)])]
    P, N, T, I = [], [], [], []
    for n, quad in faces:
        b = len(P)
        P += quad
        N += [n] * 4
        T += [(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
        I += [b, b + 1, b + 2, b, b + 2, b + 3]
    return P, N, T, I


def quad():
    P = [(-0.05, 0.3, -1.0), (0.05, 0.3, -1.0), (0.05, 0.3, -0.8), (-0.05, 0.3, -0.8)]
    return P, [(0, 1, 0)] * 4, [(0, 0), (1, 0), (1, 1), (0, 1)], [0, 2, 1, 0, 3, 2]


def png(color, w=8, h=8) -> bytes:
    return menu_bg.png_bytes(menu_bg.Picture(w, h, bytearray(bytes(color) * (w * h))))


def make_glb(parts, images, mirror_stock=False) -> bytes:
    """parts: [(name, (P, N, T, I), translation, material index)]; materials: 0 metal (image 0), 1 wood (image 1,
    metallic 0.2), 2 metal again (image 0: shares the atlas slot)."""
    bin_ = bytearray()
    views, accs, meshes, nodes = [], [], [], []

    def add(data: bytes, ctype, typ, count, mn=None, mx=None):
        while len(bin_) % 4:
            bin_.append(0)
        views.append({"buffer": 0, "byteOffset": len(bin_), "byteLength": len(data)})
        bin_.extend(data)
        a = {"bufferView": len(views) - 1, "componentType": ctype, "type": typ, "count": count}
        if mn is not None:
            a.update(min=mn, max=mx)
        accs.append(a)
        return len(accs) - 1

    for name, (P, N, T, I), tr, mat in parts:
        p = add(struct.pack(f"<{3 * len(P)}f", *[c for v in P for c in v]), 5126, "VEC3", len(P),
                [min(v[k] for v in P) for k in range(3)], [max(v[k] for v in P) for k in range(3)])
        n = add(struct.pack(f"<{3 * len(N)}f", *[c for v in N for c in v]), 5126, "VEC3", len(N))
        t = add(struct.pack(f"<{2 * len(T)}f", *[c for v in T for c in v]), 5126, "VEC2", len(T))
        i = add(struct.pack(f"<{len(I)}H", *I), 5123, "SCALAR", len(I))
        meshes.append({"name": name, "primitives": [{"attributes": {"POSITION": p, "NORMAL": n, "TEXCOORD_0": t},
                                                     "indices": i, "material": mat}]})
        node = {"mesh": len(meshes) - 1, "name": name, "translation": list(tr)}
        if mirror_stock and name == "Stock":
            node["scale"] = [-1.0, 1.0, 1.0]
        nodes.append(node)
    imgs = []
    for data in images:
        add(data, 5121, "SCALAR", len(data))
        accs.pop()
        imgs.append({"bufferView": len(views) - 1, "mimeType": "image/png"})
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}], "nodes": nodes,
           "meshes": meshes, "accessors": accs, "bufferViews": views, "buffers": [{"byteLength": len(bin_)}],
           "images": imgs, "samplers": [{}],
           "textures": [{"source": 0, "sampler": 0}, {"source": 1, "sampler": 0}, {"source": 0, "sampler": 0}],
           "materials": [{"name": "Metal", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
                         {"name": "Wood", "pbrMetallicRoughness": {"baseColorTexture": {"index": 1},
                                                                   "metallicFactor": 0.2}},
                         {"name": "Mag", "pbrMetallicRoughness": {"baseColorTexture": {"index": 2}}}]}
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    while len(bin_) % 4:
        bin_.append(0)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(bin_), 0x004E4942) + bytes(bin_)
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def rifle(**kw) -> bytes:
    """A 3 m "rifle" along -Z: a metal body (z -2..0), a wooden stock behind it (z 0..1, lower), a metal magazine
    under the body and an open sight plate (a single quad)."""
    return make_glb([("Body", box(-0.05, 0.05, 0.0, 0.2, -2.0, 0.0), (0, 0, 0), 0),
                     ("Stock", box(-0.04, 0.04, -0.15, 0.15, -0.5, 0.5), (0, 0, 0.5), 1),
                     ("Magazine", box(-0.03, 0.03, -0.4, 0.0, -0.6, -0.4), (0, 0, 0), 2),
                     ("Sight", quad(), (0, 0, 0), 0)],
                    [png((180, 180, 190, 255)), png((150, 90, 30, 255))], **kw)


# --------------------------------------------------------------------------------------------- a made-up claymore

def donor_model() -> bytes:
    bones = [Bone("Root", -1, -1, -1, 0xFFFF, 0xF001001, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0))]
    skel = Skeleton(0x1100, bones, [], 0, 0, b"")
    pos = [(-0.2, 0.0, -0.5), (0.2, 0.0, -0.5), (0.2, 0.0, 1.5), (-0.2, 0.0, 1.5)]
    b0 = b"".join(struct.pack("<4e", *p, 1.0) for p in pos)
    b1 = b"".join(struct.pack("<I4b2e", (1 << 30) | (511 << 10), 127, 0, 0, 127, 0.0, 0.0) for _ in pos)
    b2 = b"".join(struct.pack("<4b", 0, 0, 127, 127) for _ in pos)
    attrs = [Attribute("_p0", 0x1505, 0, 0), Attribute("_n0", 0x0E02, 0, 1), Attribute("_t0", 0x0B02, 4, 1),
             Attribute("_u0", 0x1205, 8, 1), Attribute("_b0", 0x0B02, 0, 2)]
    vb = VertexBuffer(4, 0, attrs, [8, 12, 4], [b0, b1, b2])
    box_ = (0.0, 0.0, 0.5, 0.2, 0.01, 1.0)
    mesh = Mesh("triangles", "u16", 6, struct.pack("<6H", 0, 1, 2, 0, 2, 3), 0, [(0, 6)])
    sh = Shape("Blade1__Mt_Test", 6, 0, 0, 0, [], 0, 0, [mesh], [box_, box_], [1.02])
    st = SamplerState(0, 0, 2, 0, 0, 1, 0x29, 0.0, 13.0, 0.0)
    mat = Material("Mt_Test", 1, [RenderInfo("gsys_pass", "string", ("no_setting",))],
                   ShaderAssign("uking_mat", "uking_mat", 0, [("_p0", "_p0"), ("_u0", "_u0")],
                                [("_a0", "_a0"), ("_s0", "_s0"), ("_n0", "_n0")], [("uking_enable_normalmap", "1")]),
                   [Sampler("_a0", st), Sampler("_n0", st), Sampler("_s0", st)],
                   [f"{DONOR}_Alb", f"{DONOR}_Nrm", f"{DONOR}_Spm"],
                   [Param("uk_bounding_radius", 12, 0, 4, 0, 0)], struct.pack("<f", 1.0), [])
    model = Model(DONOR, "", skel, [vb], [sh], [mat], [], 4)
    return nx_model.write(ResFile(DONOR, [model], external=[ExtFile("TexInfo.txt", b"3")]))


def chain(fmt, w, h, n, fill=b"\x11"):
    return [bytes(fill * formats.level_size(fmt, w, h, k)) for k in range(n)]


def donor_textures() -> bytes:
    spm = []
    for k in range(4):   # BC5 blocks with R = 200, G = 100 everywhere
        size = formats.level_size(0x1E01, 8, 8, k)
        spm.append((bytes((200, 200)) + bytes(6) + bytes((100, 100)) + bytes(6)) * (size // 16))
    ts = TextureSet(DONOR, [Texture(f"{DONOR}_Alb", 0x1A06, 16, 16, chain(0x1A06, 16, 16, 5)),
                            Texture(f"{DONOR}_Nrm", 0x1E01, 8, 8, chain(0x1E01, 8, 8, 4), channels=(2, 3, 1, 1),
                                    swizzle_hint=2),
                            Texture(f"{DONOR}_Spm", 0x1E01, 8, 8, spm, channels=(2, 3, 3, 3), swizzle_hint=3)],
                    kind="model")
    return nx_bfres.write_textures(ts)


def donor_icon() -> bytes:
    return nx_bfres.write_textures(TextureSet(f"{DONOR}.bitemico", [Texture(DONOR, 0x0B06, 16, 16, [bytes(1024)])],
                                              kind="single"))


def aamp_file(objects: dict, lists: dict | None = None) -> bytes:
    root = aamp.PList()
    for name, params in objects.items():
        o = aamp.PObject()
        for k, v in params.items():
            o.set(k, aamp.STRING_REF if isinstance(v, str) else aamp.F32, v)
        root.objects[aamp.name_hash(name)] = o
    for name, pl in (lists or {}).items():
        root.lists[aamp.name_hash(name)] = pl
    return aamp.dump(root, "xml", 0)


def donor_pack() -> bytes:
    link = aamp_file({"LinkTarget": {"ActorNameJpn": "x", "ModelUser": DONOR, "GParamUser": DONOR,
                                     "PhysicsUser": DONOR},
                      "Tags": {"Tag0": "CanEquip", "Tag1": "AmiiboTreasure", "Tag2": "RevivalBloodyMoon",
                               "Tag3": "ZukanWeapon"}})
    unit = aamp.PObject()
    unit.set("UnitName", aamp.STRING64, DONOR)
    unit.set("BindBone", aamp.STRING64, "")
    units = aamp.PList(objects={aamp.name_hash("Unit_0"): unit})
    base = aamp.PObject()
    base.set("Folder", aamp.STRING64, DONOR)
    md0 = aamp.PList(lists={aamp.name_hash("Unit"): units}, objects={aamp.name_hash("Base"): base})
    ml = aamp_file({"ControllerInfo": {"x": 1.0}},
                   {"ModelData": aamp.PList(lists={aamp.name_hash("ModelData_0"): md0})})
    raw = bnp.sarc_build([(f"Actor/ActorLink/{DONOR}.bxml", link, 4), (f"Actor/ModelList/{DONOR}.bmodellist", ml, 4),
                          (f"Actor/Physics/{DONOR}.bphysics", b"physics!", 4)])
    return wc.yaz0_compress(raw)


def tscb(cx, cz, size, name) -> bytes:
    head = b"TSCB" + struct.pack(">III", 0x0A000000, 1, 0) + struct.pack(">ff", 500.0, 800.0)
    head += struct.pack(">IIII", 0, 1, 0, 0) + struct.pack(">fI", 32.0, 8)
    head += struct.pack(">I", 4)                         # 0x30: the area table is 4 bytes on
    head += struct.pack(">I", 4)                         # 0x34: the one area is 4 bytes on, at 0x38
    area = struct.pack(">7f", cx / 500, cz / 500, size / 500, 0.3, 0.4, 0.0, 0.0)
    area += struct.pack(">II", 1, 0x10) + struct.pack(">III", 0, 0, 0)   # (the name: 0x10 past o + 0x20)
    return head + area + name.encode() + b"\0"


def hill_map(cx, cz, size) -> bytes:
    vals = []
    for j in range(256):
        for i in range(256):
            x = cx - size / 2 + i * size / 255
            z = cz - size / 2 + j * size / 255
            h = PEAK - 0.02 * math.hypot(x - SPOT["x"], z - SPOT["z"]) ** 1.5
            vals.append(round(h / 800 * 65535))
    return struct.pack("<65536H", *vals)


def flag(name: str, reset: int, once: bool) -> dict:
    return {"DataName": name, "DeleteRev": -1, "HashValue": ak._signed(zlib.crc32(name.encode())), "InitValue": 0,
            "IsEventAssociated": False, "IsOneTrigger": once, "IsProgramReadable": True, "IsProgramWritable": True,
            "IsSave": True, "MaxValue": True, "MinValue": False, "ResetType": reset}


def make_game(root: str) -> str:
    """A made-up Savage Wind folder (extracted\\romfs) with everything the builder reads. Returns the install."""
    rom = os.path.join(root, "game", "extracted", "romfs")

    def put(rel, data):
        p = os.path.join(rom, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    model, tex, icon, pack = donor_model(), donor_textures(), donor_icon(), donor_pack()
    put(f"Model/{DONOR}.sbfres", wc.yaz0_compress(model))
    put(f"Model/{DONOR}.Tex.sbfres", wc.yaz0_compress(tex))
    put(f"UI/StockItem/{DONOR}.sbitemico", wc.yaz0_compress(icon))
    put(f"Actor/Pack/{DONOR}.sbactorpack", pack)
    tags = {f"tag{zlib.crc32(t.encode()):08x}": zlib.crc32(t.encode()) & 0x7FFFFFFF
            for t in ("CanEquip", "AmiiboTreasure", "RevivalBloodyMoon", "ZukanWeapon")}
    entry = {"name": DONOR, "profile": "WeaponLargeSword", "bfres": DONOR, "mainModel": DONOR, "sortKey": 100,
             "instSize": 1000, "attackPower": 10, "tags": tags,
             "aabbMin": {"X": -0.2, "Y": 0.0, "Z": -0.5}, "aabbMax": {"X": 0.2, "Y": 0.0, "Z": 1.5}}
    other = {"name": "Other", "profile": "WeaponLargeSword", "sortKey": 101}
    actors = sorted([entry, other], key=lambda a: zlib.crc32(a["name"].encode()))
    put("Actor/ActorInfo.product.sbyml", wc.yaz0_compress(bd.byml_dump(
        {"Actors": actors, "Hashes": [bd.U32(zlib.crc32(a["name"].encode())) for a in actors]})))
    gd = bnp.sarc_build([("/bool_data_0.bgdata", bd.byml_dump({"bool_data": [
        flag(f"IsGet_{DONOR}", 0, True), flag(f"MainField_{DONOR}_123", 1, False)]}), 4)])
    put("Pack/Bootup.pack", bnp.sarc_build([("GameData/gamedata.ssarc", wc.yaz0_compress(gd), 4)]))
    put("Pack/TitleBG.pack", bnp.sarc_build([("Map/MainField/D-6/D-6_Static.smubin", wc.yaz0_compress(bm.byml_dump(
        {"Objs": [{"HashId": bm.U32(5), "SRTHash": 0, "Translate": [0.0, 0.0, 0.0], "UnitConfigName": "Rock"}],
         "Rails": []})), 4)]))
    put("Map/MainField/D-6/D-6_Dynamic.smubin", wc.yaz0_compress(bm.byml_dump(
        {"LocationPosX": -1500.0, "LocationPosZ": 1500.0, "LocationSize": 1000.0, "Objs": [
            {"HashId": bm.U32(7), "SRTHash": 0, "Translate": [-1300.0, 280.0, 1700.0], "UnitConfigName": "Tree"}],
         "Rails": []})))
    sizes = {f"Model/{DONOR}.bfres": 2 * len(model), f"Model/{DONOR}.Tex.bfres": len(tex) + 4096,
             f"UI/StockItem/{DONOR}.bitemico": len(icon) + 4096, f"Actor/Pack/{DONOR}.bactorpack": 8000,
             f"Actor/ActorLink/{DONOR}.bxml": 3000, f"Actor/ModelList/{DONOR}.bmodellist": 3000}
    put("System/Resource/ResourceSizeTable.product.srsizetable", wc.yaz0_compress(wc.rstb_build(
        [(zlib.crc32(k.encode()), v) for k, v in sizes.items()], [])))
    put("Terrain/A/MainField.tscb", tscb(SPOT["x"], SPOT["z"], 62.5, "5800000000"))
    put("Terrain/A/MainField/5800000000.hght.sstera", wc.yaz0_compress(bnp.sarc_build(
        [("5800000000.hght", hill_map(SPOT["x"], SPOT["z"], 62.5), 4)])))
    return os.path.join(root, "game")


def make_recipe(root: str, **model) -> str:
    folder = os.path.join(root, "recipe")
    os.makedirs(folder)
    with open(os.path.join(folder, "rifle.glb"), "wb") as f:
        f.write(rifle())
    shutil.copy(REPO / "assets" / "ak47" / "mod.toml", os.path.join(folder, "mod.toml"))
    shutil.copy(REPO / "assets" / "ak47" / ak.SPOT_TOML, os.path.join(folder, ak.SPOT_TOML))
    doc = {"format": 1, "actor": ACTOR, "donor": DONOR,
           "model": {"file": "rifle.glb", "forward": "-z", "up": "+y", "flip": False, "length": 1.0, **model},
           "drop_tags": ["AmiiboTreasure", "ZukanWeapon"],
           "texts": {"name": "AK-47", "desc": "Made up."},
           "spot": {**SPOT, "ground": PEAK}}
    with open(os.path.join(folder, "ak47.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return folder


# --------------------------------------------------------------------------------------------- tests

REAL_LOAD = ak.load_recipe


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


class RecipeTests(unittest.TestCase):
    def test_the_real_recipe(self):
        r = ak.load_recipe()
        self.assertEqual((r.manifest["id"], r.spot_manifest["id"]), ("giruka.ak47", "giruka.ak47-spot"))
        self.assertNotIn("part_of", r.manifest)                  # (the item stays on: saves that hold it load)
        self.assertEqual(r.spot_manifest["part_of"], ak.MP_ID)   # (where it lies: only with multiplayer on)
        self.assertEqual(r.actor, "Weapon_Lsword_AK47")
        self.assertEqual(ak._unit_of(r.spot["x"], r.spot["z"]) + "_Dynamic", r.spot["unit"])
        ak.model_check(r)   # (the shipped .glb is the one the recipe names)
        for name, part_of in (("mod.toml", None), (ak.SPOT_TOML, ak.MP_ID)):
            man, errs = modman.parse_manifest((REPO / "assets" / "ak47" / name).read_text(encoding="utf-8"))
            self.assertEqual(errs, [])
            self.assertEqual((man.part_of, man.secret), (part_of, True))
            self.assertFalse(man.notes)
            self.assertLessEqual(len(man.description), 80)
            # (neutral: what a library entry says never gives the extra away)
            text = (man.name + " " + man.description).lower()
            self.assertFalse([w for w in ("ak-47", "ak47", "rifle", "gun", "weapon") if w in text], text)

    def test_unit_of(self):
        self.assertEqual(ak._unit_of(-1364, 1755.5), "D-6")
        self.assertEqual(ak._unit_of(-5000, -4000), "A-1")
        self.assertEqual(ak._unit_of(4999, 3999), "J-8")

    def test_bundled_entry_builds_it_when_the_multiplayer_mod_is_turned_on(self):
        data = tomllib.loads((REPO / "tools" / "packaging" / "bundled-mods.toml").read_text(encoding="utf-8"))
        ghost = next(m for m in data["mod"] if m["id"] == ak.MP_ID)
        self.assertNotIn("after_add", ghost)   # (never built for a player who never turns multiplayer on)
        self.assertEqual(ghost["after_on"], [["ak47_build.py", "build", "--optional", "--quiet"]])
        self.assertEqual(ghost["after_on_makes"], ak.load_recipe().manifest["id"])
        self.assertTrue((REPO / "tools" / "ak47_build.py").is_file())

    def test_the_shipped_model_has_our_own_wood(self):
        glb = ak.Glb((REPO / "assets" / "ak47" / "AK-47.glb").read_bytes())
        names = [im.get("name") for im in glb.doc["images"]]
        self.assertEqual(names[0], "image (8)")                  # (the DoD photo: public domain)
        self.assertTrue(names[1].startswith("wood (procedural"), names)
        credits = (REPO / "assets" / "ak47" / "MODEL-CREDITS.txt").read_text(encoding="utf-8")
        self.assertIn(ak.load_recipe().model["sha256"], credits)
        self.assertIn("DM-ST-89-01131", credits)

    def test_library_after_the_command(self):
        # (the launcher puts --library last)
        with tempfile.TemporaryDirectory() as d, quiet():
            code = ak.main(["where", "--library", d, "--game", d])
        self.assertEqual(code, 1)   # (nothing there: every file MISSING, but the arguments were taken)


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.glb = ak.Glb(rifle(), "rifle.glb")

    def test_gather(self):
        m = ak.gather(self.glb, 64)
        self.assertEqual(len(m.slots), 2)                       # (Metal and Mag share their image)
        self.assertEqual([round(s[1], 2) for s in m.slots], [1.0, 0.2])
        self.assertTrue(any("Sight" in n and "back faces" in n for n in m.notes))
        self.assertEqual(len(m.idx), (3 * 12 + 2 + 2) * 3)      # 3 boxes, the quad and its back
        self.assertTrue(all(0 <= u <= 1 and 0 <= v <= 1 for u, v in m.uv))
        # stock UVs in the second band (lower half), body UVs in the first
        stock = [m.uv[i] for i, p in enumerate(m.pos) if p[2] > 0.05]
        self.assertTrue(all(v >= 0.5 for _, v in stock))

    def test_weapon_space(self):
        m = ak.to_weapon(ak.gather(self.glb, 64), "-z", "+y", (-0.5, 0.0), 2.0)
        lo, hi = m.bounds()
        self.assertAlmostEqual(lo[2], -0.5, places=5)
        self.assertAlmostEqual(hi[2], 1.5, places=5)            # the muzzle where the blade's tip is
        grip = [p for p in m.pos if -0.5 <= p[2] <= 0.0]          # the stock, centred on the Z axis
        self.assertAlmostEqual(min(p[0] for p in grip) + max(p[0] for p in grip), 0.0, places=5)
        self.assertAlmostEqual(min(p[1] for p in grip) + max(p[1] for p in grip), 0.0, places=5)
        mag = [p for p in m.pos if p[0] < -0.15]                  # "up" is +X: the magazine hangs to -X
        self.assertTrue(mag)
        self.assertLess(max(p[0] for p in m.pos), 0.2 + 1e-6)
        # normals turned with it: the body's top face (+Y in the model) points +X now
        self.assertTrue(any(abs(n[0] - 1) < 1e-6 for n in m.nrm))
        for t, n in zip(m.tan, m.nrm):
            self.assertAlmostEqual(sum(a * b for a, b in zip(t[:3], n)), 0.0, places=5)
            self.assertIn(t[3], (-1.0, 1.0))

    def test_winding_faces_out(self):
        m = ak.to_weapon(ak.gather(self.glb, 64), "-z", "+y", (-0.5, 0.0), 2.0)
        lo, hi = m.bounds()
        c = [(lo[k] + hi[k]) / 2 for k in range(3)]
        out = 0
        for t in range(0, 36 * 3, 3):   # (the first three boxes: closed)
            a, b, d = (m.pos[i] for i in m.idx[t:t + 3])
            fn = ak._cross(ak._sub(b, a), ak._sub(d, a))
            out += ak._dot(fn, ak._sub(a, c)) > 0
        self.assertGreater(out, 36 * 0.8)

    def test_mirrored_node_keeps_faces_out(self):
        m = ak.gather(ak.Glb(rifle(mirror_stock=True), "r"), 64)
        stock = [t for t in range(0, len(m.idx), 3) if all(m.pos[i][2] > 0.01 for i in m.idx[t:t + 3])]
        for t in stock:
            a, b, d = (m.pos[i] for i in m.idx[t:t + 3])
            fn = ak._cross(ak._sub(b, a), ak._sub(d, a))
            self.assertGreater(ak._dot(fn, m.nrm[m.idx[t]]), 0)

    def test_flip(self):
        m = ak.to_weapon(ak.gather(self.glb, 64), "-z", "+y", (-0.5, 0.0), 2.0, flip=True)
        self.assertTrue([p for p in m.pos if p[0] > 0.15])     # the magazine on the other side

    def test_bad_files(self):
        with self.assertRaises(ak.AkError):
            ak.Glb(b"not a glb at all", "x")
        doc = json.dumps({"asset": {"version": "2.0"}, "extensionsRequired": ["KHR_draco_mesh_compression"]}).encode()
        doc += b" " * (-len(doc) % 4)
        data = b"glTF" + struct.pack("<II", 2, 20 + len(doc)) + struct.pack("<II", len(doc), 0x4E4F534A) + doc
        with self.assertRaisesRegex(ak.AkError, "draco"):
            ak.Glb(data, "x")


class SharedStepTests(unittest.TestCase):
    """ak47_build's steps other weapons share (Dragonbreath): their names in its messages, the UV encoder's range."""

    def setUp(self):
        self.m = ak.to_weapon(ak.gather(ak.Glb(rifle(), "rifle.glb"), 64), "-z", "+y", (-0.5, 0.0), 2.0)

    def test_unorm_uvs_outside_0_1_are_an_error_not_clamped(self):
        size, enc = ak.ENCODERS[0x1201]
        self.assertEqual(enc((0.0, 1.0)), struct.pack("<2H", 0, 65535))
        for bad in ((1.5, 0.2), (0.2, -0.25)):
            with self.assertRaisesRegex(ValueError, "outside 0-1"):
                enc(bad)
        self.m.uv[0] = (1.5, 0.0)          # a tiling UV, written through a 16_16 unorm attribute
        with mock.patch.dict(ak.ENCODERS, {0x1205: ak.ENCODERS[0x1201]}):
            with self.assertRaisesRegex(ak.AkError, r"the Blade model can't be written: vertex 0's _u0: .*outside "
                                                    r"0-1.*the donor's model keeps it as 16-bit 0-1 values"):
                ak.build_model(donor_model(), self.m, "Weapon_Test", {}, donor="the donor's", weapon="the Blade")
        self.m.uv[0] = (1.0, 0.0)
        with mock.patch.dict(ak.ENCODERS, {0x1205: ak.ENCODERS[0x1201]}):
            ak.build_model(donor_model(), self.m, "Weapon_Test", {})     # inside 0-1: fine

    def test_names_in_messages(self):
        with self.assertRaisesRegex(ak.AkError, "^the claymore's model can't be read"):
            ak.build_model(b"junk", self.m, "Weapon_Test", {})
        with self.assertRaisesRegex(ak.AkError, "^the donor's model can't be read"):
            ak.build_model(b"junk", self.m, "Weapon_Test", {}, donor="the donor's", weapon="the Blade")
        with self.assertRaisesRegex(ak.AkError, "^the donor's icon can't be read"):
            ak.build_icon(b"junk", self.m, None, "Weapon_Test", donor="the donor's")
        with self.assertRaisesRegex(ak.AkError, "^the AK-47's gamedata.yml would be refused"):
            ak.check_logs({"gamedata.yml": "bool_data: [not, a, map"})
        with self.assertRaisesRegex(ak.AkError, "^Dragonbreath's gamedata.yml would be refused"):
            ak.check_logs({"gamedata.yml": "bool_data: [not, a, map"}, weapon="Dragonbreath's")


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="ak47-test-")
        cls.install = make_game(cls.tmp)
        cls.recipe = ak.load_recipe(make_recipe(cls.tmp))
        cls.game = ak.Game(cls.install, DONOR)
        cls.out = os.path.join(cls.tmp, "out")
        cls.built = ak.build_package(cls.game, cls.recipe, cls.out)
        cls.item = os.path.join(cls.out, cls.recipe.manifest["id"])
        cls.spot = os.path.join(cls.out, cls.recipe.spot_manifest["id"])
        cls.rom = os.path.join(cls.item, ak.BASE_TID, "romfs")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def read(self, rel: str) -> bytes:
        with open(os.path.join(self.rom, *rel.split("/")), "rb") as f:
            data = f.read()
        return wc.yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data

    def log(self, name: str) -> str:
        """A log of the item mod; map.yml: the spot mod's (it has nothing else)."""
        with open(os.path.join(self.spot if name == "map.yml" else self.item, "logs", name), encoding="utf-8") as f:
            return f.read()

    def test_two_mods(self):
        self.assertEqual((self.built.folder, self.built.spot_folder), (self.item, self.spot))
        self.assertEqual(sorted(os.listdir(os.path.join(self.item, "logs"))),
                         ["actorinfo.yml", "gamedata.yml", "rstb.json", "savedata.yml", "texts.json"])
        self.assertEqual(sorted(os.listdir(self.spot)), [ak.OUT_FILE, "info.json", "logs", "mod.toml"])
        self.assertEqual(os.listdir(os.path.join(self.spot, "logs")), ["map.yml"])
        item, _ = modman.parse_manifest(Path(self.item, "mod.toml").read_text(encoding="utf-8"))
        spot, _ = modman.parse_manifest(Path(self.spot, "mod.toml").read_text(encoding="utf-8"))
        self.assertEqual((item.part_of, spot.part_of, item.secret, spot.secret), (None, ak.MP_ID, True, True))
        # (where it lies: only in the spot mod's BUILT.md, never in the item's)
        self.assertNotIn("1364", Path(self.item, ak.OUT_FILE).read_text(encoding="utf-8"))
        self.assertIn("x=-1364.00", Path(self.spot, ak.OUT_FILE).read_text(encoding="utf-8"))

    def test_model(self):
        res = read_model(self.read(f"Model/{ACTOR}.sbfres"))
        md = res.models[0]
        self.assertEqual((res.name, md.name), (ACTOR, ACTOR))
        self.assertEqual(md.skeleton.bones[0].name, "Root")
        self.assertEqual(md.materials[0].name, "Mt_Test")               # (the claymore's material, kept)
        self.assertEqual(md.materials[0].textures, [f"{ACTOR}_Alb", f"{ACTOR}_Nrm", f"{ACTOR}_Spm"])
        vb = md.vertex_buffers[0]
        self.assertEqual(vb.vertex_count, self.built.vertices)
        z = [struct.unpack_from("<4e", vb.buffers[0], 8 * i)[2] for i in range(vb.vertex_count)]
        self.assertAlmostEqual(min(z), -0.5, places=2)
        self.assertAlmostEqual(max(z), 1.5, places=2)
        self.assertEqual({struct.unpack_from("<4e", vb.buffers[0], 8 * i)[3] for i in range(vb.vertex_count)}, {1.0})

    def test_textures(self):
        ts = nx_bfres.read_textures(self.read(f"Model/{ACTOR}.Tex.sbfres"))
        got = {t.name: t for t in ts.textures}
        self.assertEqual(set(got), {f"{ACTOR}_Alb", f"{ACTOR}_Nrm", f"{ACTOR}_Spm"})
        alb = got[f"{ACTOR}_Alb"]
        self.assertEqual((alb.format, alb.width, len(alb.levels)), (0x1A06, 16, 5))
        pic = menu_bg.decode_texture(alb.format, alb.width, alb.height, alb.levels[0], alb.channels)
        top, bottom = pic.px[8 * 16 * 0 + 32:8 * 16 * 0 + 35], pic.px[(12 * 16 + 8) * 4:(12 * 16 + 8) * 4 + 3]
        self.assertGreater(top[2], top[0] - 10)                          # metal band: grey
        self.assertGreater(bottom[0], bottom[2] + 60)                    # wood band: brown
        spm = got[f"{ACTOR}_Spm"]
        r = menu_bg.decode_bc4(spm.levels[0][:8], 4, 4)                  # top block: metal
        r2 = menu_bg.decode_bc4(spm.levels[0][-16:-8], 4, 4)             # bottom block: wood
        self.assertEqual(r[0], 200)
        self.assertLess(r2[0], 200 * 0.6)
        nrm = got[f"{ACTOR}_Nrm"]
        self.assertEqual(menu_bg.decode_bc4(nrm.levels[0][:8], 4, 4)[0], 128)

    def test_icon(self):
        ts = nx_bfres.read_textures(self.read(f"UI/StockItem/{ACTOR}.sbitemico"))
        t = ts.textures[0]
        self.assertEqual((t.name, t.format, t.width, t.height), (ACTOR, 0x0B06, 16, 16))
        alpha = t.levels[0][3::4]
        self.assertGreater(sum(a > 128 for a in alpha), 8)
        self.assertEqual(alpha[0], 0)                                    # the corners stay clear
        self.assertGreater(alpha[7 * 16 + 7] + alpha[8 * 16 + 8], 0)     # the diagonal is drawn

    def test_actor_pack(self):
        with open(os.path.join(self.rom, "Actor", "Pack", f"{ACTOR}.sbactorpack"), "rb") as f:
            files = bnp.Blob(f.read()).open()
        self.assertEqual(sorted(files), [f"Actor/ActorLink/{ACTOR}.bxml", f"Actor/ModelList/{ACTOR}.bmodellist",
                                         f"Actor/Physics/{DONOR}.bphysics"])
        link = aamp.load(files[f"Actor/ActorLink/{ACTOR}.bxml"][0])
        self.assertEqual(link.object("LinkTarget").get_value("ModelUser"), ACTOR)
        self.assertEqual(link.object("LinkTarget").get_value("PhysicsUser"), DONOR)
        self.assertEqual([p.value for p in link.object("Tags").values()], ["CanEquip", "RevivalBloodyMoon"])
        ml = aamp.load(files[f"Actor/ModelList/{ACTOR}.bmodellist"][0])
        md0 = ml.list("ModelData").list("ModelData_0")
        self.assertEqual(md0.object("Base").get_value("Folder"), ACTOR)
        self.assertEqual(md0.list("Unit").object("Unit_0").get_value("UnitName"), ACTOR)

    def test_logs(self):
        ai = bd.read_log("actorinfo.yml", self.log("actorinfo.yml").encode())
        e = ai[zlib.crc32(ACTOR.encode())]
        self.assertEqual((e["name"], e["bfres"], e["mainModel"], e["sortKey"]), (ACTOR, ACTOR, ACTOR, 102))
        want = {f"tag{zlib.crc32(t.encode()):08x}" for t in ("CanEquip", "RevivalBloodyMoon")}
        self.assertEqual(set(e["tags"]), want)
        self.assertAlmostEqual(e["aabbMax"]["Z"], 1.5, places=3)
        gd = bd.read_log("gamedata.yml", self.log("gamedata.yml").encode())["bool_data"]["add"]
        placed = f"MainField_{ACTOR}_{self.built.hash_id}"
        self.assertEqual(set(gd), {f"IsGet_{ACTOR}", placed})
        self.assertEqual((gd[placed]["ResetType"], gd[f"IsGet_{ACTOR}"]["IsOneTrigger"]), (1, True))
        sd = bd.read_log("savedata.yml", self.log("savedata.yml").encode())
        self.assertEqual({x["DataName"] for x in sd["add"]}, set(gd))
        texts = json.loads(self.log("texts.json"))["USen"]["ActorType/WeaponLargeSword.msyt"]
        self.assertEqual(texts[f"{ACTOR}_Name"]["contents"][0]["text"], "AK-47")
        rstb = json.loads(self.log("rstb.json"))
        for name in (f"Model/{ACTOR}.bfres", f"Model/{ACTOR}.Tex.bfres", f"UI/StockItem/{ACTOR}.bitemico",
                     f"Actor/Pack/{ACTOR}.bactorpack", f"Actor/ActorLink/{ACTOR}.bxml"):
            self.assertIn(name, rstb)

    def test_placed_on_the_hilltop(self):
        self.assertAlmostEqual(self.built.ground, PEAK, delta=0.05)
        self.assertAlmostEqual(self.built.spot[1], PEAK + SPOT["lift"], delta=0.05)
        self.assertNotIn(self.built.hash_id, {5, 7})
        # the map log merges into the made-up unit
        game = os.path.join(self.install, "extracted")
        diff = bm.combine("map.yml", [bm.read_log("map.yml", self.log("map.yml").encode())])
        res = bm.apply("map.yml", diff, game)
        self.assertEqual(res["notes"], [])
        unit = bm.byml_load(res["files"]["Map/MainField/D-6/D-6_Dynamic.smubin"])
        ours = [o for o in unit["Objs"] if o["UnitConfigName"] == ACTOR]
        self.assertEqual(len(ours), 1)
        self.assertFalse(ours[0]["!Parameters"]["IsFixedPlace"])
        self.assertEqual(ours[0]["!Parameters"]["LevelSensorMode"], 0)

    def test_actorinfo_merges(self):
        game = os.path.join(self.install, "extracted")
        diff = bd.combine("actorinfo.yml", [bd.read_log("actorinfo.yml", self.log("actorinfo.yml").encode())])
        res = bd.apply("actorinfo.yml", diff, game)
        data = res["files"]["Actor/ActorInfo.product.sbyml"]
        root = bd.byml_load(wc.yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data)
        self.assertIn(ACTOR, [a["name"] for a in root["Actors"]])
        self.assertEqual(root["Hashes"], sorted(root["Hashes"]))

    def test_terrain(self):
        t = ak.Terrain(os.path.join(self.install, "extracted", "romfs"))
        h, size = t.height(SPOT["x"], SPOT["z"])
        self.assertAlmostEqual(h, PEAK, delta=0.05)
        self.assertAlmostEqual(size, 62.5)
        self.assertLess(t.height(SPOT["x"] + 20, SPOT["z"])[0], h - 1)
        self.assertLess(ak.ground_spread(t, SPOT["x"], SPOT["z"]), ak.MAX_SLOPE_SPREAD)
        with self.assertRaises(ak.AkError):
            t.height(0.0, 0.0)

    def test_refuses_a_moved_hill(self):
        r = ak.load_recipe(self.recipe.folder)
        r.spot["ground"] = PEAK + 10
        with self.assertRaisesRegex(ak.AkError, "isn't where it should be"):
            ak.build_package(self.game, r, os.path.join(self.tmp, "out2"))

    def test_refuses_a_used_name(self):
        r = ak.load_recipe(self.recipe.folder)
        r.actor = "Other"
        with self.assertRaisesRegex(ak.AkError, "already has an actor"):
            ak.build_package(self.game, r, os.path.join(self.tmp, "out3"))

    def test_check(self):
        lib = os.path.join(self.tmp, "nolib")
        problems = ak.check(lib, install=self.install, recipe_dir=self.recipe.folder)
        # (the made-up game has no English texts: that one is expected)
        self.assertEqual([p for p in problems if "English" not in p], [])

    def test_check_looks_at_the_static_unit_too(self):
        """A tree in D-6_Static (TitleBG.pack) 2 m from the spot, a rock 3.5 m away: the tree is named, with the
        same radius as the docs (3 m)."""
        with tempfile.TemporaryDirectory(prefix="ak47-static-") as d:
            install = make_game(d)
            tbg = os.path.join(install, "extracted", "romfs", "Pack", "TitleBG.pack")
            objs = [{"HashId": bm.U32(5), "SRTHash": 0, "Translate": [SPOT["x"] + 2.0, PEAK, SPOT["z"]],
                     "UnitConfigName": "TreeNear"},
                    {"HashId": bm.U32(6), "SRTHash": 0, "Translate": [SPOT["x"], PEAK, SPOT["z"] + 3.5],
                     "UnitConfigName": "RockFar"}]
            with open(tbg, "wb") as f:
                f.write(bnp.sarc_build([("Map/MainField/D-6/D-6_Static.smubin", wc.yaz0_compress(bm.byml_dump(
                    {"Objs": objs, "Rails": []})), 4)]))
            problems = ak.check(os.path.join(d, "nolib"), install=install, recipe_dir=self.recipe.folder)
        near = [p for p in problems if "stands within" in p]
        self.assertEqual(near, [f"TreeNear (D-6_Static) stands within {ak.NEAR_RADIUS:g} m of the spot"])
        self.assertEqual(ak.NEAR_RADIUS, 3.0)
        self.assertIn("within 3 m", (REPO / "docs" / "easter-eggs.md").read_text(encoding="utf-8"))

    def test_optional_build_never_fails(self):
        with tempfile.TemporaryDirectory() as d, quiet():
            code = ak.main(["build", "--optional", "--no-add", "--out", os.path.join(d, "o"), "--game", d])
        self.assertEqual(code, 0)
        with tempfile.TemporaryDirectory() as d, quiet():
            code = ak.main(["build", "--no-add", "--out", os.path.join(d, "o"), "--game", d])
        self.assertEqual(code, 2)

    def test_optional_build_survives_a_damaged_donor_model(self):
        """The claymore's model damaged (a Yaz0 header over garbage): not an AkError (the decompressor's or the model
        reader's own error). --optional still ends with 0 and a short traceback; without it the error is raised."""
        with tempfile.TemporaryDirectory(prefix="ak47-bad-") as d:
            install = make_game(d)
            model = os.path.join(install, "extracted", "romfs", "Model", f"{DONOR}.sbfres")
            with open(model, "wb") as f:
                f.write(b"Yaz0" + struct.pack(">I", 1 << 20) + bytes(8) + bytes([0xFF]) + bytes(40))
            args = ["build", "--no-add", "--game", install, "--library", os.path.join(d, "nolib")]
            with mock.patch.object(ak, "load_recipe", lambda folder=None: REAL_LOAD(self.recipe.folder)):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = ak.main([*args, "--optional", "--quiet", "--out", os.path.join(d, "o1")])
                self.assertEqual(code, 0)
                self.assertIn("Traceback", err.getvalue())
                self.assertIn("The extra wasn't built (it is optional)", out.getvalue())
                with quiet(), self.assertRaises(Exception) as cm:
                    ak.main([*args, "--out", os.path.join(d, "o2")])
                self.assertNotIsInstance(cm.exception, ak.AkError)

    def test_quiet_and_verbose(self):
        """--quiet names nothing and never says where it lies; only --verbose says where."""
        for flag, where in (("--quiet", False), (None, False), ("--verbose", True)):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory(prefix="ak47-say-") as d, \
                    mock.patch.object(ak, "load_recipe", lambda folder=None: REAL_LOAD(self.recipe.folder)):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = ak.main(["build", "--no-add", "--out", os.path.join(d, "o"), "--game", self.install,
                                    "--library", os.path.join(d, "nolib")] + ([flag] if flag else []))
                text = out.getvalue() + err.getvalue()
                self.assertEqual(code, 0, text)
                self.assertEqual("x=-1364" in text, where, text)
                if flag == "--quiet":
                    self.assertTrue(text.startswith("Done: the extra is built"), text)
                    self.assertNotIn(ACTOR.lower(), text.lower())
                    self.assertNotIn("ak-47", text.lower())


ROOMY = type("Usage", (), {"total": 1 << 40, "used": 0, "free": 1 << 40})()


def english_texts() -> bytes:
    """A made-up Pack/Bootup_USen.pack: the weapon texts and the NPC texts, one label each."""
    def msbt(labels: dict) -> bytes:
        return bt.msbt_build(bt.Msbt(len(labels), list(labels), 0, [],
                                     [v.encode("utf-16-le") + b"\0\0" for v in labels.values()]))
    msg = bnp.sarc_build([("ActorType/NPC.msbt", msbt({"Npc_A_Name": "A"}), 8),
                          ("ActorType/WeaponLargeSword.msbt", msbt({f"{DONOR}_Name": "Claymore"}), 8)])
    return bnp.sarc_build([("Message/Msg_USen.product.ssarc", wc.yaz0_compress(msg), 8)])


class LibraryTests(unittest.TestCase):
    """The AK-47 built into a scratch library (made-up game, made-up multiplayer and castle mods): its two secret
    mods, the HashId kept clear of other mods' map changes, and deploy merging the files it shares with the castle."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ak47-lib-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.install = make_game(self.tmp)
        for rel, data in (("savagewind.exe", b"MZ made-up exe"), ("extracted/swgame.dll", b"made-up game code"),
                          ("extracted/swgame.dll.id", b"test-id\n"), ("extracted/romfs/System/Version.txt", b"x"),
                          ("extracted/romfs/Pack/Bootup_USen.pack", english_texts())):
            path = os.path.join(self.install, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        self.recipe = ak.load_recipe(make_recipe(self.tmp))
        self.libdir = os.path.join(self.tmp, "lib")
        modman.Library(self.libdir, self.install).save()
        for patch in (mock.patch.object(modman, "game_running", return_value=False),
                      mock.patch.object(modman, "MODHOST_DIR", os.path.join(self.tmp, "no-modhost")),
                      mock.patch("shutil.disk_usage", return_value=ROOMY)):   # (the test PC's drive may be full)
            patch.start()
            self.addCleanup(patch.stop)

    @property
    def lib(self):
        return modman.Library.load(self.libdir)

    def add(self, name: str, files: dict):
        src = os.path.join(self.tmp, "src", name)
        for rel, data in files.items():
            path = os.path.join(src, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data.encode() if isinstance(data, str) else data)
        with quiet():
            return modman.add_mod(self.lib, src)[0]

    def add_ghost(self):
        return self.add("ghost", {"mod.toml": f'[mod]\nid = "{ak.MP_ID}"\nname = "Ghost multiplayer"\n'
                                              'version = "0.1.0"\n[data]\nromfs = "romfs"\n',
                                  "romfs/System/GhostTest.txt": "made up"})

    def add_castle(self):
        """A made-up castle date: a BCML package that changes the actor info and the flags too (SwZelda)."""
        entry = {"name": "SwZelda", "profile": "NPC", "bfres": "SwZelda", "mainModel": "SwZelda", "sortKey": 900}
        flag_ = ak.flag_entry("SwCastle_Door", None)
        return self.add("castle", {
            "info.json": json.dumps({"name": "Castle date", "version": "0.1.0", "desc": "Made up.",
                                     "platform": "switch", "options": {}}),
            "mod.toml": '[mod]\nid = "giruka.castle-date"\nname = "Castle date"\nversion = "0.1.0"\n',
            "logs/actorinfo.yml": bd.to_text({str(zlib.crc32(b"SwZelda")): entry}),
            "logs/gamedata.yml": bd.to_text({"bool_data": {"add": {"SwCastle_Door": flag_}, "del": []}}),
            "logs/savedata.yml": bd.to_text({"add": [{"DataName": "SwCastle_Door",
                                                      "HashValue": flag_["HashValue"]}], "del": []}),
            "logs/texts.json": json.dumps({"USen": {"ActorType/NPC.msyt": {
                "SwZelda_Name": {"contents": [{"text": "Zelda"}]}}}})})

    def build(self):
        with quiet():
            return ak.build(self.libdir, install=self.install, recipe_dir=self.recipe.folder)

    def test_two_secret_mods_the_item_stays_on(self):
        self.add_ghost()
        r = self.build()
        mods = {m["id"]: m for m in self.lib.mods}
        item, spot = mods["giruka.ak47"], mods["giruka.ak47-spot"]
        self.assertEqual((item.get("part_of"), spot.get("part_of")), (None, ak.MP_ID))
        self.assertEqual((item.get("secret"), spot.get("secret")), (True, True))
        self.assertEqual((r["entry"]["id"], r["spot_entry"]["id"]), ("giruka.ak47", "giruka.ak47-spot"))
        lib = self.lib
        for m in lib.mods:
            if m["id"] == ak.MP_ID:
                m["enabled"] = False
        # multiplayer off: the item stays (a save that holds it loads), where it lies goes
        self.assertEqual([m["id"] for m in lib.enabled()], ["giruka.ak47"])
        for m in lib.mods:
            m["enabled"] = True
        self.assertEqual([m["id"] for m in lib.enabled()], [ak.MP_ID, "giruka.ak47", "giruka.ak47-spot"])
        # built again: both replaced, their places and on/off kept
        lib.mods[1]["enabled"] = False
        lib.save()
        self.build()
        self.assertEqual([(m["id"], m["enabled"]) for m in self.lib.mods],
                         [(ak.MP_ID, True), ("giruka.ak47", False), ("giruka.ak47-spot", True)])

    def test_add_new_on(self):
        src = os.path.join(self.tmp, "src", "plain")
        os.makedirs(os.path.join(src, "romfs", "System"))
        with open(os.path.join(src, "mod.toml"), "w", encoding="utf-8") as f:
            f.write('[mod]\nid = "giruka.plain"\nname = "Plain"\nversion = "0.1.0"\n[data]\nromfs = "romfs"\n')
        with open(os.path.join(src, "romfs", "System", "PlainTest.txt"), "w", encoding="utf-8") as f:
            f.write("made up")
        with quiet():
            e, _ = ak.add_to_library(self.lib, src, "giruka.plain", new_on=False)
        self.assertFalse(e["enabled"])                                      # a new one: off, as asked
        self.assertEqual([(m["id"], m["enabled"]) for m in self.lib.mods], [("giruka.plain", False)])
        lib = self.lib
        lib.mods[0]["enabled"] = True
        lib.save()
        with quiet():
            e, _ = ak.add_to_library(self.lib, src, "giruka.plain", new_on=False)
        self.assertTrue(e["enabled"])                                       # an old one keeps its on/off
        self.assertTrue(self.lib.mods[0]["enabled"])

    def test_the_hash_id_keeps_clear_of_other_mods(self):
        first = zlib.crc32(b"giruka.ak47/hilltop")   # (what a build picks when nothing else uses it)
        obj = {"HashId": bm.U32(first), "SRTHash": 0, "Translate": [0.0, 0.0, 0.0], "UnitConfigName": "Rock"}
        self.add("other", {"info.json": json.dumps({"name": "Other map mod", "version": "1", "platform": "switch"}),
                           "logs/map.yml": bd.to_text({"D-6_Dynamic": {"Objs": {"add": [obj]}}})})
        r = self.build()
        self.assertNotEqual(r["built"].hash_id, first)
        self.assertEqual([p for p in ak.check(self.libdir, install=self.install, recipe_dir=self.recipe.folder)
                          if "HashId" in p], [])
        # a mod added later that uses the AK-47's HashId: check names it
        obj = dict(obj, HashId=bm.U32(r["built"].hash_id))
        self.add("later", {"info.json": json.dumps({"name": "Later map mod", "version": "1", "platform": "switch"}),
                           "logs/map.yml": bd.to_text({"D-6_Dynamic": {"Objs": {"add": [obj]}}})})
        problems = ak.check(self.libdir, install=self.install, recipe_dir=self.recipe.folder)
        self.assertIn(f"HashId {r['built'].hash_id} of the spot is added by Later map mod too: build it again",
                      problems)

    def labels(self, pack: bytes) -> set:
        """The English text labels in a Bootup_USen.pack."""
        msg = bnp.Blob(pack).child("Message/Msg_USen.product.ssarc")
        return {lab for name in msg.open() for lab in bt.msbt_read(msg.child(name).raw).labels}

    def merged(self, files: dict) -> tuple[set, set]:
        """(actor names, bool flag names) in the merged ActorInfo and Bootup.pack."""
        def unyaz(d):
            return wc.yaz0_decompress(d)[0] if d[:4] == b"Yaz0" else d
        ai = bd.byml_load(unyaz(files["Actor/ActorInfo.product.sbyml"]))
        actors = {a["name"] for a in ai["Actors"]}
        self.assertEqual(ai["Hashes"], sorted(ai["Hashes"]))
        gd = bnp.Blob(files["Pack/Bootup.pack"]).child("GameData/gamedata.ssarc")
        flags = set()
        for name in gd.open():
            root = bd.byml_load(gd.child(name).raw)
            flags |= {r["DataName"] for rows in root.values() for r in rows if isinstance(r, dict)}
        return actors, flags

    def test_deploy_merges_the_files_it_shares_with_the_castle(self):
        """The finding: the AK-47 and the castle date both change ActorInfo and Bootup.pack. Deploy (and its dry run)
        merges them in either order: both actors and both mods' flags stay."""
        for castle_first in (False, True):
            with self.subTest(castle_first=castle_first):
                shutil.rmtree(self.libdir)
                modman.Library(self.libdir, self.install).save()
                if castle_first:
                    self.add_castle()
                self.add_ghost()
                self.build()
                if not castle_first:
                    self.add_castle()
                want_actors = {DONOR, "Other", ACTOR, "SwZelda"}
                want_flags = {f"IsGet_{ACTOR}", "SwCastle_Door", f"IsGet_{DONOR}"}
                hooks = []
                real = bnp.deploy_hook

                def spy(*a, **kw):
                    hooks.append(real(*a, **kw))
                    return hooks[-1]
                with mock.patch.object(bnp, "deploy_hook", spy), quiet():
                    launch = modman.deploy(self.lib, dry_run=True)
                self.assertTrue(launch["dry_run"])
                self.assertFalse([a for a in launch["attention"] if "replaces" in a or "several mods" in a],
                                 launch["attention"])
                self.assertTrue(any(n.startswith("Actor/ActorInfo.product.sbyml: merged from") for n in
                                    launch["notes"]), launch["notes"])
                actors, flags = self.merged(hooks[-1].files)
                self.assertLessEqual(want_actors, actors)
                self.assertLessEqual(want_flags, flags)
                self.assertLessEqual({f"{ACTOR}_Name", "SwZelda_Name", "Npc_A_Name"},
                                     self.labels(hooks[-1].files["Pack/Bootup_USen.pack"]))
                # the real deploy: the same in the overlay
                with quiet():
                    launch = modman.deploy(self.lib)
                overlay = launch["parts"]["overlay"]["path"]
                files = {}
                for rel in ("Actor/ActorInfo.product.sbyml", "Pack/Bootup.pack", "Pack/Bootup_USen.pack"):
                    with open(os.path.join(overlay, *rel.split("/")), "rb") as f:
                        files[rel] = f.read()
                actors, flags = self.merged(files)
                self.assertLessEqual(want_actors, actors)
                self.assertLessEqual(want_flags, flags)
                self.assertLessEqual({f"{ACTOR}_Name", "SwZelda_Name"}, self.labels(files["Pack/Bootup_USen.pack"]))
                self.assertTrue(os.path.isfile(os.path.join(overlay, "Map", "MainField", "D-6", "D-6_Dynamic.smubin")))


class PartOfTests(unittest.TestCase):
    """modman's [mod] part_of: a mod used only while another one is on."""

    def manifest(self, extra: str):
        return modman.parse_manifest('[mod]\nid = "me.child"\nname = "Child"\nversion = "1.0"\n' + extra)

    def test_parse(self):
        man, errs = self.manifest('part_of = "me.parent"\n')
        self.assertEqual((errs, man.part_of, man.notes), ([], "me.parent", []))
        self.assertIsNone(self.manifest("")[0].part_of)
        self.assertTrue(self.manifest('part_of = "Not An Id"\n')[1])
        self.assertTrue(self.manifest('part_of = "me.child"\n')[1])

    def test_secret(self):
        man, errs = self.manifest("secret = true\n")
        self.assertEqual((errs, man.secret, man.notes), ([], True, []))
        self.assertFalse(self.manifest("")[0].secret)
        self.assertIn("secret must be true or false", " ".join(self.manifest('secret = "yes"\n')[1]))

    def test_enabled(self):
        mods = [{"id": "me.parent", "name": "P", "enabled": True, "kinds": []},
                {"id": "me.child", "name": "C", "enabled": True, "kinds": [], "part_of": "me.parent"},
                {"id": "me.plain", "name": "X", "enabled": True, "kinds": []}]
        lib = modman.Library(tempfile.gettempdir(), None, mods)
        self.assertEqual([m["id"] for m in lib.enabled()], ["me.parent", "me.child", "me.plain"])
        mods[0]["enabled"] = False
        self.assertEqual([m["id"] for m in lib.enabled()], ["me.plain"])
        mods[0]["enabled"] = True
        mods[1]["enabled"] = False
        self.assertEqual([m["id"] for m in lib.enabled()], ["me.parent", "me.plain"])
        del mods[0]
        mods[0]["enabled"] = True
        self.assertEqual([m["id"] for m in lib.enabled()], ["me.plain"])   # (its main mod is gone)


if __name__ == "__main__":
    unittest.main()
