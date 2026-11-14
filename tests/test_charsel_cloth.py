"""Cloth and hair physics for the characters (tools/charsel.py, 2026-11-14): made-up data only, no game files.

- cloth_physics makes a donor's physics file cloth only, the way the game's armor pieces have it: no rigid body, no
  character controller, no contact info, no ragdoll; its support bones and the cloth parts asked for, numbered again.
- A pack with it: PhysicsUser is ours (SwChar_<id>), the donor's cloth and support-bone files at the paths the physics
  names; check_pack refuses anything more than cloth, another physics name, a missing file.
- The heap: the cloth counts first (fit_cloth leaves the least important parts out, never the first); a build says
  which, and `--cloth off` builds the packs as before (no physics).
- physics_bones (the bones the cloth moves, for pup_bodies.h's PUP_CLOTH) and the table puppet.c reads: one entry per
  character with cloth, never a bone named as one of Link's main bones; puppet.c's free names are charsel's.
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import aamp  # noqa: E402
from aamp import BOOL, F32, INT, STRING256, STRING_REF, PList, name_hash  # noqa: E402
import charsel  # noqa: E402
from mp import ghost_link_pack as glp  # noqa: E402
from test_charsel import actor_link, as_list, fake_romfs, model_list, obj, quiet, template_pack  # noqa: E402

GHOST = REPO / "native" / "examples" / "ghost"


def phys_file(parts, cloth="Made/Made.hkcl", support="Made/Made.bphyssb") -> bytes:
    """A made-up NPC physics file like the champions' [V layout]: a sensor rigid body, a character controller, contact
    info, a ragdoll, support bones and cloth parts."""
    hdr = obj(use_rigid_body_set_num=(INT, 1), use_ragdoll=(BOOL, True), use_cloth=(BOOL, True),
              use_support_bone=(BOOL, bool(support)), use_character_controller=(BOOL, True),
              use_contact_info=(BOOL, True), use_edge_rigid_body_num=(INT, 0), use_system_group_handler=(BOOL, True))
    objects = {name_hash("ParamSetHeader"): hdr, name_hash("Ragdoll"): obj(ragdoll_setup_file_path=(STRING256, "R.hkrg"))}
    if support:
        objects[name_hash("SupportBone")] = obj(support_bone_setup_file_path=(STRING256, support))
    cl = PList(objects={name_hash("ClothHeader"): obj(cloth_setup_file_path=(STRING256, cloth), cloth_num=(INT, len(parts))),
                        0xE216BE13: obj(X=(F32, 0.2))})
    for i, p in enumerate(parts):
        cl.objects[name_hash(f"Cloth_{i}")] = obj(name=(STRING256, p), base_bone=(STRING_REF, "Waist"), wind_drag=(F32, 5.0))
    lists = {name_hash("Cloth"): cl, name_hash("RigidBodySet"): PList(objects={1: obj(set_name=(STRING_REF, "Tgt"))}),
             name_hash("CharacterController"): PList(objects={2: obj(Y=(F32, 60.0))}),
             name_hash("RigidContactInfo"): PList(objects={3: obj(name=(STRING_REF, "Body"))})}
    return aamp.dump(PList(lists={name_hash("ParamSet"): PList(objects=objects, lists=lists)}))


def cloth_donor(actor, folder, parts, adds=(("FamilyFile", False),), cloth_size=4000, support=True, bones=()):
    """A made-up donor with physics: its cloth file holds the bone names (as a Havok file's strings) and padding."""
    files = {f"Actor/ActorLink/{actor}.bxml": actor_link(ProfileUser="NPC", AIProgramUser="Npc", ASUser="D_AS",
                                                         ModelUser="D_Model", PhysicsUser="D_Phys"),
             "Actor/ModelList/D_Model.bmodellist": model_list(folder),
             "Actor/ASList/D_AS.baslist": as_list(*adds),
             "Actor/Physics/D_Phys.bphysics": phys_file(parts, support="Made/Made.bphyssb" if support else "")}
    body = b"hk_2014\0" + b"\0".join(b.encode() for b in bones) + b"\0"
    files["Physics/Cloth/Made/Made.hkcl"] = body + b"\0" * max(0, cloth_size - len(body))
    if support:
        files["Physics/SupportBone/Made/Made.bphyssb"] = aamp.dump(PList(objects={
            5: obj(name=(STRING_REF, "Help_1")), 6: obj(name=(STRING_REF, "Arm_1_L"))}))
    return files


class ClothFile(unittest.TestCase):
    def test_recipes(self):
        with_cloth = {cid for cid, r in charsel.RECIPES.items() if r.cloth}
        # the user's ask: skirts and hair on the people who aren't Link or Linkle; the monsters' packs have cloth too
        self.assertEqual(with_cloth, {"zelda", "sheikah", "gerudo", "goron", "rito", "zora", "bokoblin", "moblin"})
        for cid in with_cloth:
            r = charsel.RECIPES[cid]
            self.assertEqual(r.kind, "pack")
            self.assertEqual(len(set(r.cloth)), len(r.cloth), cid)
            for p in r.cloth:
                self.assertTrue(re.fullmatch(r"[A-Za-z0-9_]+_Havok", p), p)   # (the game's part names)
        self.assertEqual(charsel.RECIPES["gerudo"].cloth[0], "Skart_Havok")      # (her skirt first: never left out)
        self.assertEqual(charsel.RECIPES["zora"].cloth[:3], ("Cloth_A_Havok", "Cloth_B_Havok", "Cloth_C_Havok"))

    def test_cloth_only(self):
        phys = aamp.load(phys_file(["A_Havok", "B_Havok", "C_Havok"]))
        self.assertEqual(charsel.cloth_parts(phys), ["A_Havok", "B_Havok", "C_Havok"])
        self.assertEqual(charsel.physics_paths(phys), {"cloth": "Physics/Cloth/Made/Made.hkcl",
                                                       "support": "Physics/SupportBone/Made/Made.bphyssb"})
        bad = charsel.cloth_only_problems(phys_file(["A_Havok"]))
        for word in ("use_rigid_body_set_num", "use_character_controller", "use_contact_info", "use_ragdoll",
                     "RigidBodySet", "CharacterController", "RigidContactInfo", "a ragdoll"):
            self.assertTrue(any(word in b for b in bad), word)
        data = charsel.cloth_physics(phys, ["C_Havok", "A_Havok", "B_Havok"])
        self.assertEqual(charsel.cloth_only_problems(data), [])
        ps = aamp.load(data).list("ParamSet")
        hdr = ps.object("ParamSetHeader")
        self.assertEqual({k: hdr.get_value(k) for k in ("use_rigid_body_set_num", "use_character_controller",
                                                        "use_contact_info", "use_ragdoll", "use_cloth",
                                                        "use_support_bone", "use_system_group_handler")},
                         {"use_rigid_body_set_num": 0, "use_character_controller": False, "use_contact_info": False,
                          "use_ragdoll": False, "use_cloth": True, "use_support_bone": True,
                          "use_system_group_handler": True})
        self.assertEqual(sorted(ps.lists), [name_hash("Cloth")])               # (nothing but the cloth)
        self.assertEqual(sorted(ps.objects), sorted([name_hash("ParamSetHeader"), name_hash("SupportBone")]))
        self.assertEqual(charsel.cloth_parts(aamp.load(data)), ["A_Havok", "B_Havok", "C_Havok"])   # (the file's order)
        self.assertIn(0xE216BE13, ps.list("Cloth").objects)                    # (the cloth's own settings kept)
        # a part left out: the others numbered again
        two = aamp.load(charsel.cloth_physics(phys, ["A_Havok", "C_Havok"]))
        self.assertEqual(charsel.cloth_parts(two), ["A_Havok", "C_Havok"])
        self.assertEqual(two.list("ParamSet").list("Cloth").object("ClothHeader").get_value("cloth_num"), 2)
        with self.assertRaises(charsel.CharselError):
            charsel.cloth_physics(phys, ["Nope_Havok"])
        # no support bones in the donor (the monsters'): none in ours
        mon = aamp.load(charsel.cloth_physics(aamp.load(phys_file(["A_Havok"], support="")), ["A_Havok"]))
        self.assertFalse(mon.list("ParamSet").object("ParamSetHeader").get_value("use_support_bone"))
        self.assertIsNone(mon.list("ParamSet").object("SupportBone"))

    def test_fit_cloth(self):
        files = {"Physics/Cloth/M/M.hkcl": b"\0" * 9000, "Physics/SupportBone/M/M.bphyssb": b"\0" * 1000}
        want = ["Skirt_Havok", "Hair_Havok", "Ear_Havok"]
        kept, dropped, need = charsel.fit_cloth(want, files, 3, None)
        self.assertEqual((kept, dropped, need), (want, [], int(charsel.CLOTH_HEAP_X * 10000)))
        self.assertEqual(charsel.fit_cloth(want, files, 3, 10**6)[0], want)
        kept, dropped, need = charsel.fit_cloth(want, files, 3, int(charsel.CLOTH_HEAP_X * 7000))   # (two: 7000)
        self.assertEqual((kept, dropped), (["Skirt_Havok", "Hair_Havok"], ["Ear_Havok"]))
        kept, dropped, need = charsel.fit_cloth(want, files, 3, int(charsel.CLOTH_HEAP_X * 6000))
        self.assertEqual((kept, dropped), (["Skirt_Havok"], ["Hair_Havok", "Ear_Havok"]))
        self.assertLessEqual(need, int(charsel.CLOTH_HEAP_X * 6000))
        kept, dropped, _ = charsel.fit_cloth(want, files, 3, 0)               # (never the first: the build says no cloth)
        self.assertEqual(kept, ["Skirt_Havok"])

    def test_physics_bones(self):
        files = cloth_donor("X", "Body", ["A_Havok"], bones=("Root", "Skl_Root", "Waist", "Skirt_1", "Skirt_2",
                                                             "Hair_10", "NotABone"))
        phys_files = {n: d for n, d in files.items() if n.startswith("Physics/")}
        model = ["Root", "Skl_Root", "Waist", "Skirt_1", "Skirt_2", "Hair_10", "Hair_1", "Arm_1_L", "Help_1"]
        link = ["Root", "Skl_Root", "Waist", "Arm_1_L"]
        self.assertEqual(charsel.physics_bones(phys_files, model, link), ["Hair_10", "Help_1", "Skirt_1", "Skirt_2"])


class ClothPack(unittest.TestCase):
    def make(self, physics):
        r = charsel.Recipe("madeup", "Made Up", "Races", "pack", "Npc_MadeUp", (("FamilyFile", False),),
                           (("Wait", "Own_Wait"),), cloth=("A_Havok",))
        donor = cloth_donor("Npc_MadeUp", "MadeUpBody", ["A_Havok", "B_Havok"])
        return charsel.make_pack(r, charsel.DEMO_SLOTS[0], "DemoNPC", template_pack(), donor, physics=physics(donor))

    def test_pack_with_cloth(self):
        def ours(donor):
            phys = aamp.load(donor["Actor/Physics/D_Phys.bphysics"])
            return charsel.cloth_physics(phys, ["A_Havok"]), {k: v for k, v in donor.items() if k.startswith("Physics/")}
        files = self.make(ours)
        lt = aamp.load(files[f"Actor/ActorLink/{charsel.DEMO_SLOTS[0]}.bxml"]).object("LinkTarget")
        self.assertEqual(lt.get_value("PhysicsUser"), "SwChar_madeup")
        for k in charsel.UNTOUCHABLE:
            self.assertIn(lt.get_value(k), (None, "Dummy"))                   # (still no lock-on, talk or damage)
        self.assertIn("Actor/Physics/SwChar_madeup.bphysics", files)
        self.assertIn("Physics/Cloth/Made/Made.hkcl", files)
        self.assertIn("Physics/SupportBone/Made/Made.bphyssb", files)
        self.assertNotIn("Actor/Physics/MadeUp_Phys.bphysics", files)          # (never the template's)
        data, unpacked = glp.pack_bytes(files)
        self.assertEqual(charsel.check_pack(data, charsel.DEMO_SLOTS[0]), [])
        rb = charsel.read_back(data)
        self.assertEqual(rb["physics"]["cloth"], ["A_Havok"])
        self.assertEqual(rb["physics"]["missing"], [])
        # more than cloth, a missing file, another physics name: refused
        bad = dict(files)
        bad["Actor/Physics/SwChar_madeup.bphysics"] = phys_file(["A_Havok"])
        self.assertTrue(any("CharacterController" in b for b in charsel.check_pack(glp.pack_bytes(bad)[0])))
        bad = dict(files)
        del bad["Physics/Cloth/Made/Made.hkcl"]
        self.assertTrue(any("missing" in b for b in charsel.check_pack(glp.pack_bytes(bad)[0])))
        bad = dict(files)
        al = aamp.load(bad[f"Actor/ActorLink/{charsel.DEMO_SLOTS[0]}.bxml"])
        al.object("LinkTarget").set("PhysicsUser", STRING_REF, "Npc_Zelda_Miko")
        bad[f"Actor/ActorLink/{charsel.DEMO_SLOTS[0]}.bxml"] = aamp.dump(al)
        self.assertTrue(any("physics Npc_Zelda_Miko" in b for b in charsel.check_pack(glp.pack_bytes(bad)[0])))
        # a big cloth pack is within its own limit; the same size without physics would not be
        big = dict(files)
        big["Physics/Cloth/Made/Made.hkcl"] = b"\0" * 120_000
        self.assertEqual(charsel.check_pack(glp.pack_bytes(big)[0], charsel.DEMO_SLOTS[0]), [])
        # only cloth and support-bone files go in
        with self.assertRaises(charsel.CharselError):
            self.make(lambda d: (b"x", {"Actor/AIProgram/Npc.baiprog": b"x"}))

    def test_without_physics_as_before(self):
        files = self.make(lambda d: None)
        lt = aamp.load(files[f"Actor/ActorLink/{charsel.DEMO_SLOTS[0]}.bxml"]).object("LinkTarget")
        self.assertEqual(lt.get_value("PhysicsUser"), "Dummy")
        self.assertFalse(any(n.startswith(("Physics/", "Actor/Physics/")) for n in files))
        self.assertIsNone(charsel.read_back(glp.pack_bytes(files)[0])["physics"])


class ClothBuild(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.romfs, self.out = self.root / "romfs", self.root / "out" / "charsel-packs"
        self.heap = 900_000
        patches = [mock.patch.object(charsel, "bfres_clips", lambda d: json.loads(d)),
                   mock.patch.object(charsel, "read_actorinfo",
                                     lambda romfs: {s: {"profile": p, "instSize": self.heap}
                                                    for s, p in charsel.SLOT_PROFILE.items()})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.donors = {
            "Dm_Npc_Zelda_Sibyl": cloth_donor("Dm_Npc_Zelda_Sibyl", "Npc_Zelda_Miko", list(charsel.CLOTH_ZELDA),
                                              adds=(("UMii_Common_Body_W_Animation", True),), cloth_size=30_000),
            "Dm_Npc_Goron_Hero": cloth_donor("Dm_Npc_Goron_Hero", "Npc_Goron_Hero", ["Tophair_Havok", "Beard_Havok"]),
            "Enemy_Bokoblin_Junior": cloth_donor("Enemy_Bokoblin_Junior", "Enemy_Bokoblin", [], support=False)}
        fake_romfs(self.romfs, self.donors)

    def build(self, ids, **kw):
        return charsel.build(self.romfs, self.out, ids, cache=self.root / "cache.json", say=quiet, **kw)

    def test_build_with_cloth(self):
        cat = self.build(["zelda", "goron", "bokoblin"])
        self.assertEqual(sorted(cat["built"]), ["bokoblin", "goron", "zelda"])
        z = cat["built"]["zelda"]
        self.assertEqual(z["cloth"]["parts"], ["Skirt_Havok", "Hair_C_Havok", "Necklace_Havok"])
        self.assertEqual(z["cloth"]["dropped"], [])
        self.assertEqual(z["heap"]["cloth"], z["cloth"]["bytes"])
        self.assertGreaterEqual(z["heap"]["need"], charsel.HEAP_BASE + z["cloth"]["bytes"])
        # Daruk's donor has two of his six parts: those, and the others said
        g = cat["built"]["goron"]["cloth"]
        self.assertEqual(g["parts"], ["Tophair_Havok", "Beard_Havok"])
        self.assertIn("Mustache_Havok", g["note"])
        # a donor with no cloth part: built as before, no physics, said
        self.assertEqual(cat["built"]["bokoblin"]["cloth"]["parts"], [])
        self.assertTrue(any("Bokoblin: no cloth physics" in n for n in cat["notes"]))
        self.assertEqual(charsel.check(self.out), {"zelda": [], "goron": [], "bokoblin": []})
        pack = self.out / "romfs" / "Actor" / "Pack" / f"{charsel.SLOTS['zelda']}.sbactorpack"
        rb = charsel.read_back(pack.read_bytes())
        self.assertEqual(rb["users"]["PhysicsUser"], "SwChar_zelda")
        self.assertEqual(rb["physics"]["paths"]["cloth"], "Physics/Cloth/Made/Made.hkcl")
        boko = self.out / "romfs" / "Actor" / "Pack" / f"{charsel.SLOTS['bokoblin']}.sbactorpack"
        self.assertEqual(charsel.read_back(boko.read_bytes())["users"]["PhysicsUser"], "Dummy")

    def test_small_heap_leaves_parts_out(self):
        # the heap left after the body fits the skirt's share but not all three parts (a bigger body: every free
        # name has 500 KB or more)
        base = mock.patch.object(charsel, "HEAP_BASE", 700_000)
        base.start()
        self.addCleanup(base.stop)
        files = self.donors["Dm_Npc_Zelda_Sibyl"]
        cloth = len(files["Physics/Cloth/Made/Made.hkcl"])
        support = len(files["Physics/SupportBone/Made/Made.bphyssb"])
        self.heap = charsel.HEAP_BASE + int(charsel.CLOTH_HEAP_X * (cloth / 3 + support)) + 10
        cat = self.build(["zelda"])
        z = cat["built"]["zelda"]["cloth"]
        self.assertEqual((z["parts"], z["dropped"]), (["Skirt_Havok"], ["Hair_C_Havok", "Necklace_Havok"]))
        self.assertTrue(any("left out for the heap: Hair_C_Havok, Necklace_Havok" in n for n in cat["notes"]))
        pack = self.out / "romfs" / "Actor" / "Pack" / f"{charsel.SLOTS['zelda']}.sbactorpack"
        self.assertEqual(charsel.read_back(pack.read_bytes())["physics"]["cloth"], ["Skirt_Havok"])
        # not even the skirt: no physics at all, said
        self.heap = charsel.HEAP_BASE + 10
        cat = self.build(["zelda"])
        self.assertEqual(cat["built"]["zelda"]["cloth"]["parts"], [])
        self.assertIn("heap", cat["built"]["zelda"]["cloth"]["off"])
        self.assertEqual(charsel.read_back(pack.read_bytes())["users"]["PhysicsUser"], "Dummy")

    def test_cloth_off(self):
        cat = self.build(["zelda", "goron"], cloth="off")
        self.assertEqual(cat["built"]["zelda"]["cloth"], {"off": "charsel build --cloth off", "parts": [],
                                                          "dropped": list(charsel.CLOTH_ZELDA)})
        pack = self.out / "romfs" / "Actor" / "Pack" / f"{charsel.SLOTS['goron']}.sbactorpack"
        self.assertEqual(charsel.read_back(pack.read_bytes())["users"]["PhysicsUser"], "Dummy")
        # a build of one with cloth on rebuilds the other too (never a pack of the other switch kept)
        cat = self.build(["zelda"])
        self.assertEqual(cat["kept"], [])
        self.assertNotIn("goron", cat["built"])
        cat = self.build(["goron"])
        self.assertEqual(cat["kept"], ["zelda"])
        with self.assertRaises(charsel.CharselError):
            self.build(["zelda"], cloth="maybe")


class NativeTable(unittest.TestCase):
    def test_header_cloth_table(self):
        text = (GHOST / "pup_bodies.h").read_text(encoding="utf-8")
        block = text.split("PUP_CLOTH[] = {", 1)[1].split("};", 1)[0]
        entries = {}
        for cid, body in re.findall(r'\{"([a-z]+)",((?:\s*"[^"]*")+)\}', block):
            entries[cid] = "".join(re.findall(r'"([^"]*)"', body))
        self.assertEqual(set(entries), {cid for cid, r in charsel.RECIPES.items() if r.cloth})
        for cid, names in entries.items():
            with self.subTest(cid=cid):
                self.assertTrue(names.endswith(" "))                           # (each name followed by one space)
                words = names.split()
                self.assertEqual(len(words), len(set(words)))
                self.assertFalse(set(words) & set(charsel.BODY_BONES))         # (never one of the main bones)
                self.assertGreater(len(words), 5)
        self.assertIn("{0, 0},", block)                                       # (the end entry puppet.c stops at)
        # the generator: the table after the boxes and the hands, as it was
        boxes = {"link": {"Skl_Root": [-1, 1, -1, 1, -1, 1]}}
        out = charsel.bodies_header(boxes, None, {"zelda": ["Skirt_1", "Hair_2"]})
        self.assertTrue(out.startswith(charsel.bodies_header(boxes)))
        self.assertIn('{"zelda",\n     "Skirt_1 Hair_2 "},', out)

    def test_free_names_match(self):
        text = (GHOST / "puppet.c").read_text(encoding="utf-8")
        block = text.split("PUP_FREE_NAMES[] = {", 1)[1].split("};", 1)[0]
        self.assertEqual(re.findall(r'"([^"]+)"', block), list(charsel.FREE_SLOTS))
        self.assertIn("!pup_free_name(t_status.name)", (GHOST / "ghost.c").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
