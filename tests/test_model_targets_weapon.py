"""Tests for tools/model_targets/weapon.py (converter stage 1: any model -> a one-handed weapon mod). All data is
made up here: a tiny GLB and OBJ sword, and test_ak47's made-up game (its donor pack gets a stats file here).
No game files."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import aamp  # noqa: E402
import ak47_build as ak  # noqa: E402
import bnp  # noqa: E402
import test_ak47 as ta  # noqa: E402
import wiiu_convert as wc  # noqa: E402
from model_in import load  # noqa: E402
from model_in.scene import Bone, Scene, Skeleton  # noqa: E402
from model_targets import weapon as wp  # noqa: E402

DONOR = ta.DONOR


def sword_glb() -> bytes:
    """A 1 m sword along -Z: the blade (z -1..-0.2, wide along y), the handle (z -0.2..0) at the origin."""
    return ta.make_glb([("Blade", ta.box(-0.01, 0.01, -0.05, 0.05, -1.0, -0.2), (0, 0, 0), 0),
                        ("Handle", ta.box(-0.02, 0.02, -0.02, 0.02, -0.2, 0.0), (0, 0, 0), 1)],
                       [ta.png((200, 200, 210, 255)), ta.png((90, 50, 20, 255))])


def sword_obj() -> str:
    """The same kind of sword along +X (handle at the origin), edge along z, as OBJ text (no materials)."""
    lines, n = [], 0
    for x0, x1, y0, y1, z0, z1 in ((0.2, 1.2, -0.01, 0.01, -0.06, 0.06), (0.0, 0.2, -0.02, 0.02, -0.02, 0.02)):
        P, _N, _T, I = ta.box(x0, x1, y0, y1, z0, z1)
        lines += [f"v {x} {y} {z}" for x, y, z in P]
        lines += [f"f {I[t] + 1 + n} {I[t + 1] + 1 + n} {I[t + 2] + 1 + n}" for t in range(0, len(I), 3)]
        n += len(P)
    return "\n".join(["o Sword"] + lines) + "\n"


def stats_pack() -> bytes:
    """test_ak47's donor pack plus a stats file (Attack Power 10, General Life 20) named by GParamUser."""
    files = bnp.Blob(ta.donor_pack()).open()
    gp = aamp.PList()
    att, gen = aamp.PObject(), aamp.PObject()
    att.set("Power", aamp.INT, 10)
    gen.set("Life", aamp.INT, 20)
    gp.objects[aamp.name_hash("Attack")] = att
    gp.objects[aamp.name_hash("General")] = gen
    items = [(n, d, a) for n, (d, a) in files.items()]
    items.append((f"Actor/GeneralParamList/{DONOR}.bgparamlist", aamp.dump(gp, "xml", 0), 4))
    return wc.yaz0_compress(bnp.sarc_build(items))


def make_game(root: str) -> str:
    install = ta.make_game(root)
    with open(os.path.join(install, "extracted", "romfs", "Actor", "Pack", f"{DONOR}.sbactorpack"), "wb") as f:
        f.write(stats_pack())
    return install


class SettingsTests(unittest.TestCase):
    def test_defaults_from_the_name(self):
        s = wp.Settings(name="My Katana!", attack=30, donor=DONOR)
        self.assertEqual(s.actor, "Weapon_Sword_MyKatana")
        self.assertEqual(s.mod_id, "weapon.my-katana")
        self.assertTrue(s.desc)

    def test_bad_values_are_one_sentence(self):
        for kw in ({"attack": 0}, {"attack": 5000}, {"name": ""}, {"donor": "bad name"}, {"length": 9.0},
                   {"durability": 0}):
            args = {"name": "X", "attack": 10, "donor": DONOR, **kw}
            with self.assertRaises(wp.WeaponError):
                wp.Settings(**args)


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.glb = os.path.join(self.tmp.name, "sword.glb")
        Path(self.glb).write_bytes(sword_glb())
        self.obj = os.path.join(self.tmp.name, "sword.obj")
        Path(self.obj).write_text(sword_obj(), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_glb_mesh_and_atlas(self):
        m = wp.scene_mesh(load(self.glb))
        self.assertEqual(len(m.slots), 2)              # two pictures -> two atlas slots
        self.assertEqual(len(m.idx) // 3, 24)          # two closed boxes, no back faces
        self.assertTrue(all(0 <= u <= 1 and 0 <= v <= 1 for u, v in m.uv))

    def test_orientation_glb(self):
        m = wp.scene_mesh(load(self.glb))
        self.assertEqual(wp.orientation(m, None), ("-z", "+y"))
        w = ak.to_weapon(m, "-z", "+y", (-0.5, 0.0), 2.0)
        lo, hi = w.bounds()
        self.assertAlmostEqual(lo[2], -0.5, 4)
        self.assertAlmostEqual(hi[2], 1.5, 4)
        handle = [w.pos[i][2] for i, p in enumerate(m.pos) if p[2] > -0.2 + 1e-6]
        self.assertTrue(handle and max(handle) < 0.0)   # the handle ends up in the donor's hilt (below 0)

    def test_orientation_obj_and_pick(self):
        sc = load(self.obj)
        m = wp.scene_mesh(sc)
        self.assertEqual(wp.orientation(m, None), ("+x", "+z"))
        with self.assertRaises(wp.WeaponError):
            wp.scene_mesh(sc, ["NoSuchMesh"])

    def test_grip_bone_sets_the_end(self):
        m = wp.scene_mesh(load(self.glb))
        sc = Scene(skeleton=Skeleton([Bone("Sword_Grip", translation=(0.0, 0.0, -1.0))]))
        self.assertEqual(wp.grip_point(sc), (0.0, 0.0, -1.0))
        self.assertEqual(wp.orientation(m, wp.grip_point(sc)), ("+z", "+y"))


class LicenceTests(unittest.TestCase):
    def test_share_rules(self):
        sc = Scene()
        self.assertIsNotNone(wp.share_problem(sc))                      # private by default
        sc.meta["share"] = "shareable"
        self.assertIsNone(wp.share_problem(sc))
        sc.meta["licence"] = {"may_share": False}
        self.assertIsNotNone(wp.share_problem(sc))                      # the licence wins over the player's mark
        sc.meta["licence"] = {"may_share": True}
        sc.meta["share"] = "private"
        self.assertIsNone(wp.share_problem(sc))


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.tmp.name
        cls.install = make_game(root)
        cls.glb = os.path.join(root, "sword.glb")
        Path(cls.glb).write_bytes(sword_glb())
        cls.obj = os.path.join(root, "sword.obj")
        Path(cls.obj).write_text(sword_obj(), encoding="utf-8")
        cls.s = wp.Settings(name="Test Blade", attack=42, donor=DONOR, desc="Made up.", durability=33)
        cls.out = os.path.join(root, "out")
        cls.res = wp.build(cls.glb, cls.s, cls.out, install=cls.install, share=os.path.join(root, "share"))
        cls.romfs = os.path.join(cls.res.folder, ak.BASE_TID, "romfs")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def log(self, name):
        return Path(self.res.folder, "logs", name).read_text(encoding="utf-8")

    def test_files(self):
        a = self.s.actor
        for rel in (f"Model/{a}.sbfres", f"Model/{a}.Tex.sbfres", f"UI/StockItem/{a}.sbitemico",
                    f"Actor/Pack/{a}.sbactorpack"):
            self.assertTrue(os.path.isfile(os.path.join(self.romfs, *rel.split("/"))), rel)
        for name in ("mod.toml", "info.json", ak.OUT_FILE):
            self.assertTrue(os.path.isfile(os.path.join(self.res.folder, name)), name)
        self.assertEqual(self.res.triangles, 24)

    def test_model_reads_back(self):
        raw = ak._unyaz0(Path(self.romfs, "Model", f"{self.s.actor}.sbfres").read_bytes())
        res = ak.read_model(raw)
        self.assertEqual(res.models[0].name, self.s.actor)
        self.assertAlmostEqual(ak.donor_length(res), 2.0, 2)   # the donor's length by default

    def test_stats_in_the_pack(self):
        files = bnp.Blob(Path(self.romfs, "Actor", "Pack", f"{self.s.actor}.sbactorpack").read_bytes()).open()
        gp = f"Actor/GeneralParamList/{self.s.actor}.bgparamlist"
        self.assertIn(gp, files)
        self.assertNotIn(f"Actor/GeneralParamList/{DONOR}.bgparamlist", files)
        root = aamp.load(files[gp][0])
        self.assertEqual(root.object("Attack").get_value("Power"), 42)
        self.assertEqual(root.object("General").get_value("Life"), 33)
        link = aamp.load(files[f"Actor/ActorLink/{self.s.actor}.bxml"][0]).object("LinkTarget")
        self.assertEqual(link.get_value("ModelUser"), self.s.actor)
        self.assertEqual(link.get_value("GParamUser"), self.s.actor)

    def test_logs(self):
        texts = json.loads(self.log("texts.json"))["USen"]["ActorType/WeaponLargeSword.msyt"]
        self.assertEqual(texts[f"{self.s.actor}_Name"]["contents"][0]["text"], "Test Blade")
        self.assertIn("attackPower: 42", self.log("actorinfo.yml"))
        self.assertIn(f"IsGet_{self.s.actor}", self.log("gamedata.yml"))
        self.assertIn(f"Model/{self.s.actor}.bfres", json.loads(self.log("rstb.json")))
        self.assertIn(str(zlib.crc32(self.s.actor.encode())), self.log("actorinfo.yml"))

    def test_private_model_is_not_shared(self):
        self.assertEqual(self.res.shared, "")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "share")))
        self.assertTrue(any("built for you only" in n for n in self.res.notes))

    def test_no_game_files_in_a_recipe(self):
        sc = load(self.glb)
        sc.meta["share"] = "shareable"
        folder = wp.write_recipe(sc, self.glb, self.s, os.path.join(self.tmp.name, "recipe"))
        self.assertEqual(sorted(os.listdir(folder)), ["recipe.json", "sword.glb"])
        doc = json.loads(Path(folder, "recipe.json").read_text(encoding="utf-8"))
        self.assertEqual((doc["target"], doc["stats"]["attack"]), ("weapon", 42))

    def test_refuses_used_name_and_full_folder(self):
        sc = load(self.glb)
        game = wp.find_game(self.install, DONOR)
        with self.assertRaises(wp.WeaponError):
            wp.build_package(game, sc, wp.Settings(name="X", attack=1, donor=DONOR, actor=DONOR),
                             os.path.join(self.tmp.name, "o2"))
        with self.assertRaises(wp.WeaponError):
            wp.build_package(game, sc, self.s, self.out)

    def test_command_line_obj(self):
        out = os.path.join(self.tmp.name, "cli")
        with contextlib.redirect_stdout(io.StringIO()) as so:
            code = wp.main(["build", self.obj, "--name", "Obj Sword", "--attack", "25", "--donor", DONOR,
                            "--out", out, "--game", self.install, "--length", "1.2"])
        self.assertEqual(code, 0, so.getvalue())
        self.assertTrue(os.path.isfile(os.path.join(out, "weapon.obj-sword", ak.BASE_TID, "romfs", "Model",
                                                    "Weapon_Sword_ObjSword.sbfres")))
        with contextlib.redirect_stderr(io.StringIO()) as se:
            code = wp.main(["build", os.path.join(self.tmp.name, "nope.obj"), "--name", "X", "--attack", "5",
                            "--donor", DONOR, "--out", os.path.join(self.tmp.name, "cli2"), "--game", self.install])
        self.assertEqual(code, 1)
        self.assertIn("Can't build the weapon", se.getvalue())


if __name__ == "__main__":
    unittest.main()
