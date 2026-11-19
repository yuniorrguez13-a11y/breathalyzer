"""Tests for Dragonbreath, stage 1 (tools/dragonbreath_build.py, tools/dragonbreath_model.py): the model made in code,
the recipe, and a build on a made-up game (a made-up one-handed donor sword, an ancient weapon's Guardian list, map,
terrain, flags) in temp folders. No game files. The made-up game's common parts come from tests/test_ak47.py."""
import contextlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import aamp  # noqa: E402
import ak47_build as ak  # noqa: E402
import bcml_data as bd  # noqa: E402
import bcml_texts as bt  # noqa: E402
import bnp  # noqa: E402
import dragonbreath_build as db  # noqa: E402
import dragonbreath_model as dm  # noqa: E402
import dragonbreath_stage2 as s2  # noqa: E402
import modman  # noqa: E402
import test_ak47 as t47  # noqa: E402  (the made-up game's terrain, model, textures, icon, flags)
import wiiu_convert as wc  # noqa: E402
from wiiu_model import nx_bfres, read_model  # noqa: E402

DONOR = "Weapon_Sword_T01"
GUARDIAN_SRC = "Weapon_Sword_013"
ACTOR = "Weapon_Sword_Dragonbreath"
GUARDIANS = "Enemy_Ganon,Enemy_SiteBoss_Sword,Enemy_Guardian_Mini_Baby,Enemy_Guardian_Mini_Senior"
GUARDIAN_BEAM = "Enemy_Guardian_Mini_Baby_DetachLineBeam"   # carries IsEffectiveAncientWeapon, as a Blight refight


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


# --------------------------------------------------------------------------------------------- a made-up donor

def typed_aamp(objects: dict, lists: dict | None = None) -> bytes:
    """objects: {name: {param: (type, value)}}."""
    root = aamp.PList()
    for name, params in objects.items():
        o = aamp.PObject()
        for k, (typ, v) in params.items():
            o[aamp.name_hash(k)] = aamp.Param(typ, v)
        root.objects[aamp.name_hash(name)] = o
    for name, pl in (lists or {}).items():
        root.lists[aamp.name_hash(name)] = pl
    return aamp.dump(root, "xml", 0)


def stats(power=15, life=26, sp_actor="", sp_tag="", ratio=2.0) -> bytes:
    S, I, F, B, R = aamp.STRING32, aamp.INT, aamp.F32, aamp.BOOL, aamp.STRING_REF
    com = {"IsThrowingWeapon": (B, True), "SharpWeaponPer": (F, 10.0), "SharpWeaponAddCrit": (B, True)}
    com.update({k: (I, 5) for k in db.SHARP_KEYS})
    return typed_aamp({"General": {"Life": (I, life), "IsLifeInfinite": (B, False)},
                       "Attack": {"Power": (I, power), db.POWER_TWIN: (I, power), "SpHitActor": (R, sp_actor),
                                  "SpHitTag": (R, sp_tag), "SpHitRatio": (F, ratio)},
                       "WeaponCommon": com,
                       "SmallSword": {"PodName": (S, "Weapon_Sheath_T01"), "WeaponSubType": (S, "0")}})


def pack(name: str, gp: bytes) -> bytes:
    R = aamp.STRING_REF
    link = typed_aamp({"LinkTarget": {"ActorNameJpn": (R, "x"), "ModelUser": (R, name), "GParamUser": (R, name),
                                      "ChemicalUser": (R, "Weapon_Chemical_Iron"), "PhysicsUser": (R, "Weapon_Metal")},
                       "Tags": {"Tag0": (R, "CanEquip"), "Tag1": (R, "CannotSell"), "Tag2": (R, "RevivalBloodyMoon"),
                                "Tag3": (R, "ZukanWeapon")}})
    unit = aamp.PObject()
    unit.set("UnitName", aamp.STRING64, name)
    base = aamp.PObject()
    base.set("Folder", aamp.STRING64, name)
    md0 = aamp.PList(lists={aamp.name_hash("Unit"): aamp.PList(objects={aamp.name_hash("Unit_0"): unit})},
                     objects={aamp.name_hash("Base"): base})
    ml = typed_aamp({"ControllerInfo": {"x": (aamp.F32, 1.0)}},
                    {"ModelData": aamp.PList(lists={aamp.name_hash("ModelData_0"): md0})})
    raw = bnp.sarc_build([(f"Actor/ActorLink/{name}.bxml", link, 4), (f"Actor/ModelList/{name}.bmodellist", ml, 4),
                          (f"Actor/GeneralParamList/{name}.bgparamlist", gp, 4),
                          ("Actor/Physics/Weapon_Metal.bphysics", b"physics!", 4)])
    return wc.yaz0_compress(raw)


def english_texts() -> bytes:
    def msbt(labels: dict) -> bytes:
        return bt.msbt_build(bt.Msbt(len(labels), list(labels), 0, [],
                                     [v.encode("utf-16-le") + b"\0\0" for v in labels.values()]))
    msg = bnp.sarc_build([("ActorType/WeaponSmallSword.msbt", msbt({f"{DONOR}_Name": "Guard sword"}), 8)])
    return bnp.sarc_build([("Message/Msg_USen.product.ssarc", wc.yaz0_compress(msg), 8)])


def make_game(root: str) -> str:
    """test_ak47's made-up game plus a one-handed donor (its model, textures and icon), an ancient weapon's stats
    and the actor info for both."""
    install = t47.make_game(root)
    rom = os.path.join(install, "extracted", "romfs")

    def put(rel, data):
        p = os.path.join(rom, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
    for rel in ("Model/{}.sbfres", "Model/{}.Tex.sbfres", "UI/StockItem/{}.sbitemico"):   # (test_ak47's donor's)
        with open(os.path.join(rom, *rel.format(t47.DONOR).split("/")), "rb") as f:
            put(rel.format(DONOR), f.read())
    put(f"Actor/Pack/{DONOR}.sbactorpack", pack(DONOR, stats()))
    put(f"Actor/Pack/{GUARDIAN_SRC}.sbactorpack",
        pack(GUARDIAN_SRC, stats(20, 17, GUARDIANS, "TeamGuardian,IsEffectiveAncientWeapon", 1.3)))
    put("Pack/Bootup_USen.pack", english_texts())
    tags = {f"tag{zlib.crc32(t.encode()):08x}": zlib.crc32(t.encode()) & 0x7FFFFFFF
            for t in ("CanEquip", "CannotSell", "RevivalBloodyMoon", "ZukanWeapon")}
    entry = {"name": DONOR, "profile": "WeaponSmallSword", "bfres": DONOR, "mainModel": DONOR, "sortKey": 200,
             "attackPower": 15, "generalLife": 26, "tags": tags, "pictureBookLiveSpot1": 21,
             "weaponCommonSharpWeaponAddAtkMax": 9, "weaponCommonSharpWeaponPer": 10.0,
             "weaponCommonSharpWeaponAddCrit": True, "homeArea": [{"name": "Somewhere"}],
             "aabbMin": {"X": -0.2, "Y": 0.0, "Z": -0.5}, "aabbMax": {"X": 0.2, "Y": 0.0, "Z": 1.5}}
    def tagged(*names):
        return {f"tag{zlib.crc32(t.encode()):08x}": bd.U32(zlib.crc32(t.encode())) for t in names}
    enemies = [{"name": "Enemy_Guardian_Mini_Baby", "profile": "Guardian", "tags": tagged("TeamGuardian")},
               {"name": "Enemy_SiteBoss_Sword_R", "profile": "SiteBoss", "tags": tagged("IsEffectiveAncientWeapon")},
               {"name": GUARDIAN_BEAM, "profile": "Guardian", "tags": tagged("IsEffectiveAncientWeapon")}]
    actors = sorted([entry, {"name": GUARDIAN_SRC, "profile": "WeaponSmallSword", "sortKey": 201},
                     {"name": t47.DONOR, "profile": "WeaponLargeSword", "sortKey": 100}] + enemies,
                    key=lambda a: zlib.crc32(a["name"].encode()))
    put("Actor/ActorInfo.product.sbyml", wc.yaz0_compress(bd.byml_dump(
        {"Actors": actors, "Hashes": [bd.U32(zlib.crc32(a["name"].encode())) for a in actors]})))
    return install


def make_recipe(root: str, **over) -> str:
    folder = os.path.join(root, "recipe")
    os.makedirs(folder, exist_ok=True)
    for n in ("mod.toml", db.ITEM_TOML):
        shutil.copy(REPO / "assets" / "dragonbreath" / n, os.path.join(folder, n))
    doc = json.loads((REPO / "assets" / "dragonbreath" / db.RECIPE_JSON).read_text(encoding="utf-8"))
    doc["donor"] = DONOR
    doc["test_spot"] = {"unit": "D-6_Dynamic", "x": t47.SPOT["x"], "z": t47.SPOT["z"], "ground": t47.PEAK,
                        "lift": 0.25, "yaw": 30.0}
    for k, v in over.items():
        doc[k] = v
    with open(os.path.join(folder, db.RECIPE_JSON), "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return folder


# --------------------------------------------------------------------------------------------- the model

SHAPE = dm.Shape(hilt=-0.2129, top=1.0512)


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = dm.make_mesh(SHAPE)

    def test_size_and_budget(self):
        lo, hi = self.m.bounds()
        self.assertAlmostEqual(lo[2], SHAPE.hilt, places=4)            # the donor's length, to the mm
        self.assertAlmostEqual(hi[2], SHAPE.top, places=4)
        self.assertLess(len(self.m.idx) // 3, 2500)                      # (the Flameblade is about this)
        self.assertEqual(len(self.m.pos), len(self.m.nrm))
        self.assertEqual(len(self.m.pos), len(self.m.tan))
        self.assertTrue(all(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 for u, v in self.m.uv))

    def test_extremely_slim(self):
        """The blade (above the collar): at most 2.2 cm wide and 6 mm thick, about 1/50 of its length."""
        blade = [p for p in self.m.pos if p[2] > SHAPE.collar_z[1] + 0.01]
        for z0 in (0.05, 0.5, 0.9):
            near = [p for p in blade if abs(p[2] - z0) < 0.03]
            w = max(p[0] for p in near) - min(p[0] for p in near)
            t = max(p[1] for p in near) - min(p[1] for p in near)
            self.assertLessEqual(w, SHAPE.base_width + 0.0005)
            self.assertLessEqual(t, SHAPE.thickness + 0.0002)
            self.assertLess(w / (SHAPE.top - SHAPE.hilt), 0.02)
        # the curve: the tip sits toward the back by about the sori
        self.assertAlmostEqual(dm.center_x(SHAPE, SHAPE.top), -SHAPE.sori, places=6)

    def test_faces_point_out(self):
        """Every triangle faces away from its part's inside, so its normal agrees with its vertices' normals, and
        the whole blade's signed volume is positive (counter-clockwise outside)."""
        P, I, N = self.m.pos, self.m.idx, self.m.nrm
        bad = 0
        for t in range(0, len(I), 3):
            a, b, c = I[t:t + 3]
            fn = ak._cross(ak._sub(P[b], P[a]), ak._sub(P[c], P[a]))
            if math.sqrt(ak._dot(fn, fn)) < 1e-12:
                continue
            avg = tuple(N[a][k] + N[b][k] + N[c][k] for k in range(3))
            bad += ak._dot(fn, avg) < 0
        self.assertEqual(bad, 0)
        vol = sum(ak._dot(P[I[t]], ak._cross(P[I[t + 1]], P[I[t + 2]])) for t in range(0, len(I), 3)) / 6
        self.assertGreater(vol, 0)

    def test_same_every_time(self):
        again = dm.make_mesh(SHAPE)
        self.assertEqual((again.pos, again.uv, again.idx), (self.m.pos, self.m.uv, self.m.idx))
        self.assertEqual(dm.texel(0.3, 0.05, SHAPE), dm.texel(0.3, 0.05, SHAPE))

    def test_edge_side(self):
        """edge_side -1 mirrors the blade (the edge then looks along -X), for a game that swings the back first."""
        flipped = dm.Shape(hilt=SHAPE.hilt, top=SHAPE.top, edge_side=-1)
        self.assertEqual([(-x, y, z) for x, y, z in dm.ring(SHAPE, 0.4)], dm.ring(flipped, 0.4))
        self.assertGreater(dm.ring(SHAPE, 0.4)[0][0], dm.ring(SHAPE, 0.4)[2][0])   # (edge on +X, back on -X)

    def test_dragon_on_both_faces(self):
        """Both main faces map onto the same texels (the far one mirrored), so the dragon shows on both."""
        hira = [dm.band_regions(k)["hira"] for k in range(dm.BANDS)]
        on_hira = [uv for uv in self.m.uv if any(r[1] <= uv[1] <= r[3] for r in hira)]
        self.assertGreater(len(on_hira), 4 * dm.BANDS * SHAPE.segments)
        # the dragon is drawn: grooves along the body, none on the last band (the tip)
        hits = sum(dm.dragon(s, y) for s in range(40, 700, 3) for y in [k / 40 for k in range(41)])
        self.assertGreater(hits, 200)
        self.assertEqual(sum(dm.dragon(s, y) for s in range(800, 1000, 5) for y in [k / 20 for k in range(21)]), 0)

    def test_textures(self):
        """Small pictures (the real ones are the donor's size): opaque colour, an engraving that sinks in, a shine
        map in 0..1, and no glow anywhere (only colour, normal and shine are made)."""
        p = dm.paint_textures(128, 128, 64, 64, 16, 16, SHAPE)
        self.assertEqual((p.albedo.w, p.albedo.h), (128, 128))
        self.assertEqual(set(p.albedo.px[3::4]), {255})
        flat = sum(1 for row in p.normal for rg in row if rg == (128, 128))
        self.assertGreater(flat, 64 * 64 // 2)                           # mostly flat...
        self.assertLess(flat, 64 * 64)                                   # ...but the dragon sinks in
        self.assertTrue(all(0.0 <= k <= 1.0 for row in p.shine for k in row))
        dark = [p.albedo.px[i:i + 3] for i in range(0, 128 * 40 * 4, 4)]  # (the first blade band's rows)
        self.assertTrue(any(max(c) < 120 for c in dark))                 # engraved lines
        self.assertTrue(any(min(c) > 220 for c in dark))                 # the bright edge

    def test_preview(self):
        pic = dm.preview(SHAPE, 400, 1)
        self.assertEqual(pic.w, 400)
        bg = bytes((36, 38, 44, 255))
        self.assertGreater(sum(1 for i in range(0, len(pic.px), 4) if bytes(pic.px[i:i + 4]) != bg), 1000)


# --------------------------------------------------------------------------------------------- the recipe

BUNDLED_ENTRY = '''[[mod]]
id = "botw-mod-loader.dragonbreath"
title = "Dragonbreath"
description = "A lost Goron blade forged for Daruk. Test stage: one lies at the plateau start."
default = "off"
why = "It adds a new weapon to the world (and later a cave, a quest and new sword moves)."
tags = ["Experimental", "Built from your game files the first time (about 1 minute)"]
ready = false
kind = "recipe"
folder = "assets/dragonbreath"
build = [["dragonbreath_build.py", "build", "--on", "--quiet"]]
'''


class RecipeTests(unittest.TestCase):
    def test_the_real_recipe(self):
        r = db.load_recipe()
        self.assertEqual((r.actor, r.donor), (ACTOR, "Weapon_Sword_047"))
        self.assertEqual(r.manifest["id"], "botw-mod-loader.dragonbreath")
        self.assertEqual(r.item_manifest["id"], "giruka.dragonbreath-item")
        self.assertEqual((r.manifest.get("part_of"), r.item_manifest.get("part_of")), (None, None))
        self.assertIs(r.item_manifest.get("secret"), True)
        self.assertEqual(r.stats["attack"], 40)                          # the user's decision
        self.assertIs(r.stats["life_infinite"], True)                    # never breaks
        self.assertIs(r.stats["throwable"], False)
        self.assertLess(r.stats["guardian_ratio"], 1.0)                  # weak against Guardians
        self.assertEqual(r.stats["subtype"], "Dragonbreath")
        self.assertIn("ZukanWeapon", r.tags["drop"])
        self.assertNotIn("NotDropFromPlayer", r.tags["add"])             # (the user: it can be dropped and lost)
        self.assertEqual(r.texts["name"], "Dragonbreath")
        self.assertIn("Daruk", r.texts["desc"])
        self.assertEqual(ak._unit_of(r.spot["x"], r.spot["z"]), "D-6")
        for line in r.texts["desc"].split("\n"):
            self.assertTrue(line.isascii())

    def test_bad_recipes(self):
        tmp = tempfile.mkdtemp(prefix="dragonbreath-recipe-")
        self.addCleanup(shutil.rmtree, tmp, True)
        for over, words in (({"tags": {"drop": ["A"], "add": ["A"]}}, "both dropped and added"),
                            ({"texts": {"name": "D", "desc": "x" * 41}}, "must fit"),
                            ({"actor": "Weapon_Lsword_X"}, "one-handed"),
                            ({"test_spot": {"unit": "A-1_Dynamic", "x": 0, "z": 0, "ground": 0, "lift": 0, "yaw": 0}},
                             "map section")):
            folder = make_recipe(os.path.join(tmp, str(len(os.listdir(tmp)))), **over)
            with self.assertRaises(db.DragonError) as cm:
                db.load_recipe(folder)
            self.assertIn(words, str(cm.exception))

    def test_future_bundled_entry_is_valid(self):
        """The packaging entry in out/research/dragonbreath-handoff.md passes make_release's checks once the files
        are tracked (a made-up project folder with only that entry)."""
        import make_release as mr
        tmp = tempfile.mkdtemp(prefix="dragonbreath-bundled-")
        self.addCleanup(shutil.rmtree, tmp, True)
        root = Path(tmp)
        (root / "tools" / "packaging").mkdir(parents=True)
        (root / "assets" / "dragonbreath").mkdir(parents=True)
        shutil.copy(REPO / "assets" / "dragonbreath" / "mod.toml", root / "assets" / "dragonbreath" / "mod.toml")
        (root / mr.BUNDLED_TOML).write_text(BUNDLED_ENTRY, encoding="utf-8")
        mods, bad = mr.read_bundled(root, {"assets/dragonbreath/mod.toml", "tools/dragonbreath_build.py"})
        self.assertEqual(bad, [])
        self.assertEqual([(m["id"], m["ready"], m["default"]) for m in mods],
                         [("botw-mod-loader.dragonbreath", False, "off")])

    def test_bundled_command_parses(self):
        """The command the packaging entry will run (out/research/dragonbreath-handoff.md) parses with the
        launcher's --library at the end."""
        import test_bundled_commands as tbc
        self.assertEqual(tbc.parse_problem("dragonbreath_build.py", ["build", "--on", "--quiet", "--library", "X:/l"]),
                         "")

    def test_no_bundled_entry_yet(self):
        """The packaging entry waits until the files are committed (a recipe's files must be ones git tracks):
        out/research/dragonbreath-handoff.md holds it. When it is added, it must stay not ready and off."""
        import tomllib
        data = tomllib.loads((REPO / "tools" / "packaging" / "bundled-mods.toml").read_text(encoding="utf-8"))
        for m in data["mod"]:
            if m["id"] == "botw-mod-loader.dragonbreath":
                self.assertIs(m["ready"], False)
                self.assertEqual(m["default"], "off")
                self.assertEqual(m["folder"], "assets/dragonbreath")


# --------------------------------------------------------------------------------------------- building

class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dragonbreath-test-")
        cls.install = make_game(cls.tmp)
        cls.recipe = db.load_recipe(make_recipe(cls.tmp))
        cls.game = ak.Game(cls.install, DONOR)
        cls.out = os.path.join(cls.tmp, "out")
        cls.calls = []

        def fake_stage2(game, st, item_dir, main_dir, *, used=None, log=None, events=True):
            """(the made-up game has no Goron City: tests/test_dragonbreath_stage2.py builds stage 2 itself)"""
            cls.calls.append({"st": st, "item": item_dir, "main": main_dir, "used": set(used), "events": events,
                              "main_logs": sorted(os.listdir(os.path.join(main_dir, "logs"))),
                              "item_logs": sorted(os.listdir(os.path.join(item_dir, "logs")))})
            return s2.Built2(notes=["stage 2 (made-up)"])
        with mock.patch.object(s2, "build_stage2", fake_stage2):
            cls.built = db.build_package(cls.game, cls.recipe, cls.out, test_spot=True)
        cls.item = os.path.join(cls.out, cls.recipe.item_manifest["id"])
        cls.main = os.path.join(cls.out, cls.recipe.manifest["id"])
        cls.rom = os.path.join(cls.item, ak.BASE_TID, "romfs")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def read(self, rel: str) -> bytes:
        with open(os.path.join(self.rom, *rel.split("/")), "rb") as f:
            data = f.read()
        return wc.yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data

    def log(self, name: str) -> str:
        with open(os.path.join(self.main if name == "map.yml" else self.item, "logs", name), encoding="utf-8") as f:
            return f.read()

    def pack_files(self) -> dict:
        return dict(bnp.Blob(self.read(f"Actor/Pack/{ACTOR}.sbactorpack")).open().items())

    def test_two_mods(self):
        self.assertEqual(sorted(os.listdir(os.path.join(self.item, "logs"))),
                         ["actorinfo.yml", "gamedata.yml", "rstb.json", "savedata.yml", "texts.json"])
        self.assertEqual(os.listdir(os.path.join(self.main, "logs")), ["map.yml"])
        self.assertFalse(os.path.exists(os.path.join(self.main, ak.BASE_TID)))   # (the switch holds no game file)
        item, _ = modman.parse_manifest(Path(self.item, "mod.toml").read_text(encoding="utf-8"))
        main, _ = modman.parse_manifest(Path(self.main, "mod.toml").read_text(encoding="utf-8"))
        self.assertEqual((item.part_of, main.part_of, item.secret), (None, None, True))
        for rel in (f"Actor/Pack/{ACTOR}.sbactorpack", f"Model/{ACTOR}.sbfres", f"Model/{ACTOR}.Tex.sbfres",
                    f"UI/StockItem/{ACTOR}.sbitemico"):
            self.assertTrue(os.path.isfile(os.path.join(self.rom, *rel.split("/"))), rel)

    def test_stats(self):
        files = self.pack_files()
        self.assertNotIn(f"Actor/GeneralParamList/{DONOR}.bgparamlist", files)   # (ours replaces it)
        got = db.read_stats(files[f"Actor/GeneralParamList/{ACTOR}.bgparamlist"][0])
        self.assertEqual((got["attack"], got["power_twin"], got["life"], got["life_infinite"]), (40, 40, 1000, True))
        self.assertEqual((got["throwable"], got["subtype"], got["pod"]), (False, "Dragonbreath", "Weapon_Sheath_T01"))
        self.assertAlmostEqual(got["sp_ratio"], 0.25, places=6)
        # no Ganon, no Blights: the tag a Blight refight carries is dropped, the Guardian that carries it named
        self.assertEqual(got["sp_actor"], f"Enemy_Guardian_Mini_Baby,Enemy_Guardian_Mini_Senior,{GUARDIAN_BEAM}")
        self.assertEqual(got["sp_tag"], "TeamGuardian")
        self.assertEqual(set(got["sharp"].values()), {0})
        self.assertEqual(got["sharp_per"], 0.0)

    def test_actor_link(self):
        files = self.pack_files()
        self.assertNotIn(f"Actor/ActorLink/{DONOR}.bxml", files)
        link = aamp.load(files[f"Actor/ActorLink/{ACTOR}.bxml"][0])
        t = link.object("LinkTarget")
        self.assertEqual((t.get_value("ModelUser"), t.get_value("GParamUser"), t.get_value("ActorNameJpn")),
                         (ACTOR, ACTOR, "Dragonbreath"))
        self.assertEqual(t.get_value("ChemicalUser"), "Weapon_Chemical_Iron")      # plain steel: no fire
        tags = [p.value for p in link.object("Tags").values()]
        self.assertEqual(tags, ["CanEquip", "CannotSell", "RevivalNone", "NotRandomSet", "ApplyHighGradeGetDemo"])
        ml = aamp.load(files[f"Actor/ModelList/{ACTOR}.bmodellist"][0])
        md0 = list(ml.list("ModelData").lists.values())[0]
        self.assertEqual(md0.object("Base").get_value("Folder"), ACTOR)

    def test_model_and_textures(self):
        res = read_model(self.read(f"Model/{ACTOR}.sbfres"))
        md = res.models[0]
        self.assertEqual((res.name, md.name), (ACTOR, ACTOR))
        self.assertEqual(md.materials[0].textures, [f"{ACTOR}_Alb", f"{ACTOR}_Nrm", f"{ACTOR}_Spm"])
        self.assertEqual(md.vertex_buffers[0].vertex_count, self.built.vertices)
        donor = {t.name.rsplit("_", 1)[1]: t for t in nx_bfres.read_textures(
            wc.yaz0_decompress(self.game.raw("tex"))[0]).textures}
        for t in nx_bfres.read_textures(self.read(f"Model/{ACTOR}.Tex.sbfres")).textures:
            d = donor[t.name.rsplit("_", 1)[1]]
            self.assertEqual((t.format, t.width, t.height, len(t.levels)), (d.format, d.width, d.height, len(d.levels)))
        icon = nx_bfres.read_textures(self.read(f"UI/StockItem/{ACTOR}.sbitemico"))
        self.assertEqual(icon.textures[0].name, ACTOR)

    def test_logs(self):
        ai = self.log("actorinfo.yml")
        for want in (f"name: {ACTOR}", "attackPower: 40", "generalLife: 1000", "weaponCommonSharpWeaponAddAtkMax: 0",
                     "pictureBookLiveSpot1: -1", "profile: WeaponSmallSword"):
            self.assertIn(want, ai)
        self.assertNotIn("homeArea", ai)
        texts = json.loads(self.log("texts.json"))["USen"][f"{db.MSG_FILE}.msyt"]
        self.assertEqual(sorted(texts), [f"{ACTOR}_Desc", f"{ACTOR}_Name", f"{ACTOR}_PictureBook"])
        self.assertEqual(texts[f"{ACTOR}_Name"]["contents"][0]["text"], "Dragonbreath")
        gd = self.log("gamedata.yml")
        self.assertIn(f"IsGet_{ACTOR}:", gd)
        self.assertIn(f"MainField_{ACTOR}_{self.built.hash_id}:", gd)     # (the test spot's: in the item mod)
        self.assertIn(f"UnitConfigName: {ACTOR}", self.log("map.yml"))
        self.assertIn(f"Actor/GeneralParamList/{ACTOR}.bgparamlist", json.loads(self.log("rstb.json")) or
                      {f"Actor/GeneralParamList/{ACTOR}.bgparamlist": 0})

    def test_test_spot_on_the_ground(self):
        x, y, z = self.built.spot
        self.assertEqual((x, z), (t47.SPOT["x"], t47.SPOT["z"]))
        self.assertAlmostEqual(y, self.built.ground + 0.25, places=3)
        self.assertLess(abs(self.built.ground - t47.PEAK), 0.5)

    def test_refuses_a_used_name(self):
        with self.assertRaises(db.DragonError):
            db.build_package(self.game, self.recipe, self.out)              # (not empty)

    def test_tempered_levels_share_the_base_model(self):
        """Stage 2's 4 tempered levels: their own pack (stats per the temper table), actor info and icon; one model
        for all five (the model list names the base blade's, which is the only model file)."""
        st = s2.load_stage2()
        want = [f"{ACTOR}_{i}" for i in range(1, 5)]
        self.assertEqual(self.built.levels, want)
        ai = bd.from_text(self.log("actorinfo.yml"))
        by_name = {e["name"]: e for e in ai.values()}
        self.assertEqual(sorted(by_name), sorted([ACTOR] + want))
        self.assertEqual(len({e["sortKey"] for e in by_name.values()}), 5)
        self.assertEqual(sorted(os.listdir(os.path.join(self.rom, "Model"))),
                         sorted([f"{ACTOR}.Tex.sbfres", f"{ACTOR}.sbfres"]))
        for i, a in enumerate(want, 1):
            files = dict(bnp.Blob(self.read(f"Actor/Pack/{a}.sbactorpack")).open().items())
            got = db.read_stats(files[f"Actor/GeneralParamList/{a}.bgparamlist"][0])
            self.assertEqual((got["attack"], got["power_twin"]), (st.levels[i]["attack"],) * 2)
            self.assertAlmostEqual(got["sp_ratio"], st.levels[i]["guardian_ratio"], places=6)
            self.assertEqual((got["life_infinite"], got["subtype"]), (True, "Dragonbreath"))
            t = aamp.load(files[f"Actor/ActorLink/{a}.bxml"][0]).object("LinkTarget")
            self.assertEqual((t.get_value("ModelUser"), t.get_value("GParamUser")), (a, a))
            self.assertEqual(t.get_value("ActorNameJpn"), st.levels[i]["name"])
            md0 = list(aamp.load(files[f"Actor/ModelList/{a}.bmodellist"][0]).list("ModelData").lists.values())[0]
            self.assertEqual(md0.object("Base").get_value("Folder"), ACTOR)
            unit = list(md0.list("Unit").objects.values())[0]
            self.assertEqual(unit.get_value("UnitName"), ACTOR)
            self.assertEqual((by_name[a]["bfres"], by_name[a]["mainModel"], by_name[a]["attackPower"]),
                             (ACTOR, ACTOR, st.levels[i]["attack"]))
            icon = nx_bfres.read_textures(self.read(f"UI/StockItem/{a}.sbitemico"))
            self.assertEqual(icon.textures[0].name, a)
        texts = json.loads(self.log("texts.json"))["USen"][f"{db.MSG_FILE}.msyt"]
        self.assertNotIn(f"{want[0]}_Name", texts)                         # (stage 2's item logs hold the levels')
        self.assertNotIn(f"IsGet_{want[0]}", self.log("gamedata.yml"))

    def test_stage2_is_built_into_both_folders(self):
        self.assertEqual(len(self.calls), 1)
        c = self.calls[0]
        self.assertEqual((c["item"], c["main"], c["events"]), (self.item, self.main, True))
        self.assertEqual(c["st"].base_actor, ACTOR)
        self.assertIn(self.built.hash_id, c["used"])                        # (the test spot's: never reused)
        self.assertIn("map.yml", c["main_logs"])                           # (after stage 1's logs are written)
        self.assertIn("actorinfo.yml", c["item_logs"])
        self.assertIn("stage 2 (made-up)", self.built.notes)

    def test_check(self):
        with quiet():
            problems = db.check(os.path.join(self.tmp, "no-library"), install=self.install,
                                recipe_dir=self.recipe.folder, stage2=False)
        self.assertEqual(problems, [])

    def test_no_test_spot_unless_asked(self):
        """The plateau test blade is developer-only: picking it up would move the quest on for a player."""
        out = os.path.join(self.tmp, "out-player")
        built = db.build_package(self.game, self.recipe, out, stage2=False)
        self.assertEqual((built.spot, built.hash_id), ((), 0))
        main = os.path.join(out, self.recipe.manifest["id"])
        self.assertFalse(os.path.exists(os.path.join(main, "logs", "map.yml")))
        with open(os.path.join(out, self.recipe.item_manifest["id"], "logs", "gamedata.yml"), encoding="utf-8") as f:
            gd = f.read()
        self.assertIn(f"IsGet_{ACTOR}:", gd)
        self.assertNotIn(f"MainField_{ACTOR}_", gd)
        self.assertEqual(len(built.levels), 4)

    def test_optional_build_never_fails(self):
        with quiet():
            code = db.main(["build", "--optional", "--quiet", "--game", os.path.join(self.tmp, "nowhere"),
                            "--library", os.path.join(self.tmp, "no-library")])
        self.assertEqual(code, 0)
        with quiet():
            code = db.main(["build", "--game", os.path.join(self.tmp, "nowhere"),
                            "--library", os.path.join(self.tmp, "no-library")])
        self.assertEqual(code, 2)


class RefusalTests(unittest.TestCase):
    def test_a_donor_with_glow_masks_is_refused(self):
        """Dragonbreath has no glow of its own (only in the special mode, later): a donor material with emission
        masks (the Flameblade's) is refused rather than half filled."""
        with self.assertRaises(db.DragonError) as cm:
            db.make_textures(SHAPE, None, {"_a0": "A", "_n0": "N", "_s0": "S", "_em0": "E"}, ACTOR)
        self.assertIn("glow", str(cm.exception))

    def test_a_stats_file_without_a_value_is_refused(self):
        recipe = db.load_recipe()
        bare = typed_aamp({"General": {"Life": (aamp.INT, 5)}})
        with self.assertRaises(db.DragonError) as cm:
            db.stats_file(bare, recipe, "A", "B")
        self.assertIn("IsLifeInfinite", str(cm.exception))


class LibraryTests(unittest.TestCase):
    """Built into a scratch library: the item mod always on, the Dragonbreath switch new and off (on with --on), a
    rebuild keeps on/off, clean takes only the switch unless asked."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dragonbreath-lib-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.install = make_game(self.tmp)
        for rel, data in (("savagewind.exe", b"MZ made-up exe"), ("extracted/swgame.dll", b"made-up game code"),
                          ("extracted/swgame.dll.id", b"test-id\n"), ("extracted/romfs/System/Version.txt", b"x")):
            path = os.path.join(self.install, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        self.recipe_dir = make_recipe(self.tmp)
        self.libdir = os.path.join(self.tmp, "lib")
        modman.Library(self.libdir, self.install).save()
        for patch in (mock.patch.object(modman, "game_running", return_value=False),
                      mock.patch.object(modman, "MODHOST_DIR", os.path.join(self.tmp, "no-modhost")),
                      mock.patch("shutil.disk_usage", return_value=t47.ROOMY)):
            patch.start()
            self.addCleanup(patch.stop)

    def lib(self):
        return modman.Library.load(self.libdir)

    def build(self, **kw):
        with quiet():       # (stage 2 off: the made-up game has no Goron City, test_dragonbreath_stage2 has one;
            #                    so the test spot gives the switch something to hold)
            return db.build(self.libdir, install=self.install, recipe_dir=self.recipe_dir, stage2=False,
                            test_spot=True, **kw)

    def test_switch_off_by_default_item_on(self):
        r = self.build()
        self.assertEqual((r["entry"]["id"], r["main_entry"]["id"]),
                         ("giruka.dragonbreath-item", "botw-mod-loader.dragonbreath"))
        on = {m["id"]: m["enabled"] for m in self.lib().mods}
        self.assertEqual(on, {"giruka.dragonbreath-item": True, "botw-mod-loader.dragonbreath": False})
        self.build(on=True)                                                 # --on: the switch on
        self.assertTrue({m["id"]: m["enabled"] for m in self.lib().mods}["botw-mod-loader.dragonbreath"])
        lib = self.lib()
        for m in lib.mods:
            m["enabled"] = m["id"] != "botw-mod-loader.dragonbreath"
        lib.save()
        self.build()                                                        # a rebuild keeps on/off
        self.assertFalse({m["id"]: m["enabled"] for m in self.lib().mods}["botw-mod-loader.dragonbreath"])
        with quiet():
            gone = db.clean(self.libdir, recipe_dir=self.recipe_dir)
        self.assertEqual(gone, ["botw-mod-loader.dragonbreath"])
        self.assertEqual([m["id"] for m in self.lib().mods], ["giruka.dragonbreath-item"])

    def test_broken_moves_leave_the_blade(self):
        clips = os.path.join(self.recipe_dir, "clips")
        os.makedirs(clips, exist_ok=True)
        with open(os.path.join(clips, "s1.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        r = self.build()                                   # no raw traceback, no DragonError: the blade is added
        self.assertIsNone(r["moves"])
        self.assertTrue(any("fast moves" in n and "not valid JSON" in n for n in r["notes"]), r["notes"])
        on = {m["id"]: m["enabled"] for m in self.lib().mods}
        self.assertEqual(on, {"giruka.dragonbreath-item": True, "botw-mod-loader.dragonbreath": False})
        self.assertTrue(any(p.startswith("clips:") for p in db.moves_problems(self.install, clips)))

    def test_add_passes_new_on_to_the_shared_add(self):
        lib = self.lib()
        with mock.patch.object(ak, "add_to_library", return_value=({"id": "x", "enabled": False}, [])) as add, \
                mock.patch.object(modman.Library, "save") as save:
            entry, _ = db.add_to_library(lib, "folder", "x", None, new_on=False)
        add.assert_called_once_with(lib, "folder", "x", None, new_on=False)
        save.assert_not_called()               # (no second save turning it off again: the shared add does it)
        self.assertFalse(entry["enabled"])


if __name__ == "__main__":
    unittest.main()
