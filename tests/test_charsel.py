"""Tests for Who am I (tools/charsel.py): the characters, their packs, the wheel and your choice. Made-up data only:
no game files, no window on the screen (the wheel's window is drawn into a withdrawn Tk root)."""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import aamp  # noqa: E402
from aamp import INT, STRING64, STRING_REF, PList, PObject, name_hash  # noqa: E402
import charsel  # noqa: E402
from mp import ghost_link_pack as glp  # noqa: E402
from mp import lobby  # noqa: E402
from wiiu_convert import sarc_build, yaz0_compress  # noqa: E402

GHOST = {n for n, _c, _f in glp.ANIMS}


def quiet(*_a, **_k):
    pass


def obj(**params):
    o = PObject()
    for k, (t, v) in params.items():
        o.set(k, t, v)
    return o


def actor_link(**users):
    base = {k: "Dummy" for k in ("ProfileUser", "AIProgramUser", "ASUser", "ModelUser", "PhysicsUser", "UMiiUser",
                                 "AnimationInfo", "GParamUser")}
    base.update(users)
    return aamp.dump(PList(objects={name_hash("LinkTarget"): obj(**{k: (STRING_REF, v) for k, v in base.items()})}))


def model_list(folder=None):
    lists = {name_hash("AnmTarget"): PList(lists={name_hash("AnmTarget_0"): PList(
        objects={name_hash("Base"): obj(NumASSlot=(INT, 1))})})}
    if folder:
        md0 = PList(objects={name_hash("Base"): obj(Folder=(STRING64, folder))},
                    lists={name_hash("Unit"): PList(objects={name_hash("Unit_0"): obj(UnitName=(STRING64, folder))})})
        lists[name_hash("ModelData")] = PList(lists={name_hash("ModelData_0"): md0})
    else:
        lists[name_hash("ModelData")] = PList()
    return aamp.dump(PList(lists=lists))


LINK_BODY_TARGETS = ((0, ("Face_Root",)), (1, ("Root", "Face_Root")), (3, ("Root", "Spine_1", "Face_Root")))
HERO_TARGETS = LINK_BODY_TARGETS[:1] + ((0, ("Root", "Face_Root")),) + LINK_BODY_TARGETS[2:] + ((2, ()),)


def target_list(targets, folder=None):
    """A model list with animation targets ((TargetType, partial bones) each), like the game's."""
    at = PList()
    for i, (tt, bones) in enumerate(targets):
        part = PList(objects={name_hash(f"Partial_{k}"): obj(Bone=(STRING64, b)) for k, b in enumerate(bones)})
        at.lists[name_hash(f"AnmTarget_{i}")] = PList(
            objects={name_hash("Base"): obj(NumASSlot=(INT, 1), TargetType=(INT, tt))},
            lists={name_hash("Partial"): part} if bones else {})
    root = aamp.load(model_list(folder))
    root.lists[name_hash("AnmTarget")] = at
    return aamp.dump(root)


def as_list(*adds):
    add = PList()
    for i, (anim, rt) in enumerate(adds):
        o = obj(Anim=(STRING64, anim))
        if rt:
            o.set("RetargetModel", STRING64, "Skeleton")
        add.objects[name_hash(f"AddRes_{i}")] = o
    return aamp.dump(PList(lists={name_hash("AddReses"): add, name_hash("ASDefines"): PList()}))


def template_pack():
    """The made-up cutscene stand-in: its actor link, AI program, model list, animation list (with a retarget entry)."""
    return {f"Actor/ActorLink/{charsel.TEMPLATE}.bxml": actor_link(
                ProfileUser="DemoNPC", AIProgramUser="DemoDummy", ASUser="MadeUp_AS", ModelUser="MadeUp_Model",
                PhysicsUser="MadeUp_Phys", UMiiUser="MadeUp_Mii"),
            "Actor/AIProgram/DemoDummy.baiprog": aamp.dump(PList(objects={1: obj(X=(INT, 1))})),
            "Actor/ModelList/MadeUp_Model.bmodellist": target_list(LINK_BODY_TARGETS),
            "Actor/ASList/MadeUp_AS.baslist": as_list(("MadeUp_Body", True)),
            "Actor/Physics/MadeUp_Phys.bphysics": b"made up", "Actor/UMii/MadeUp_Mii.bumii": b"made up"}


def donor_pack(actor, folder="MadeUpBody", umii=None, adds=(("FamilyFile", False),), targets=None):
    files = {f"Actor/ActorLink/{actor}.bxml": actor_link(ProfileUser="NPC", AIProgramUser="Npc", ASUser="D_AS",
                                                         ModelUser="D_Model", UMiiUser=umii or "Dummy"),
             "Actor/ModelList/D_Model.bmodellist": (target_list(targets, None if umii else folder) if targets
                                                    else model_list(None if umii else folder)),
             "Actor/ASList/D_AS.baslist": as_list(*adds)}
    if umii:
        files[f"Actor/UMii/{umii}.bumii"] = b"made-up look"
    return files


def pause_pack():
    return {f"Actor/ActorLink/{glp.DONOR_LINK}.bxml": actor_link(ModelUser="P_Model", ASUser="P_AS"),
            "Actor/ModelList/P_Model.bmodellist": model_list("Link"),
            "Actor/ASList/P_AS.baslist": as_list((charsel.PLAYER_ANIM, False))}


TEST_RECIPE = charsel.Recipe("madeup", "Made Up", "Races", "pack", "Npc_MadeUp",
                             (("FamilyFile", True), (charsel.PLAYER_ANIM, True)),
                             (("Wait", "Own_Wait"), ("Walk", "Own_Walk"), ("Run", "Own_Run")))
MONSTER = charsel.Recipe("madeup", "Made Up", "Monsters", "pack", "Npc_MadeUp", (("FamilyFile", False),),
                         (("Wait", "W"), ("CutS1", "A")), rest="speed")


class Recipes(unittest.TestCase):
    def test_every_recipe_is_well_formed(self):
        on_wheel = [i for _, ids in charsel.CATEGORIES for i in ids]
        self.assertEqual(sorted(on_wheel), sorted(charsel.RECIPES))           # each once on the wheel
        self.assertEqual(len(on_wheel), len(set(on_wheel)))
        words = set()
        for cid, r in charsel.RECIPES.items():
            self.assertEqual(r.id, cid)
            self.assertTrue(charsel.valid_id(cid), cid)
            self.assertLessEqual(len(r.name), 20, r.name)
            self.assertIn(r.cat, dict(charsel.CATEGORIES))
            self.assertIn(cid, dict(charsel.CATEGORIES)[r.cat])
            for n, clip in r.own:
                self.assertIn(n, GHOST | set(charsel.EXTRA_NAMES), f"{cid}: {n}")   # only ghost names
                self.assertTrue(re.fullmatch(r"[A-Za-z0-9_]{1,63}", clip), clip)
            self.assertEqual(len(dict(r.own)), len(r.own), cid)
            for w in (cid,) + r.aliases:
                self.assertNotIn(w, words, w)                                 # one word, one character
                self.assertTrue(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,23}", w), w)   # (what iam.c takes)
                words.add(w)
            if r.kind == "pack":
                self.assertTrue(r.donor and r.anims and r.looks)
                self.assertNotIn(r.donor, charsel.FREE_SLOTS)
                self.assertIn(r.rest, ("link", "speed"))
                self.assertIn(cid, charsel.SLOTS)

    def test_slots(self):
        self.assertEqual(len(set(charsel.SLOTS.values())), len(charsel.SLOTS))   # one name, one character
        self.assertTrue(set(charsel.SLOTS.values()) <= set(charsel.FREE_SLOTS))
        self.assertNotIn(charsel.LINK_ACTOR, charsel.FREE_SLOTS)                # the Link body's: never ours
        self.assertEqual(charsel.LINK_ACTOR, glp.LINK_ACTOR)
        self.assertEqual(set(charsel.SLOT_PROFILE), set(charsel.FREE_SLOTS))
        for cid in ("zelda", "goron", "bokoblin"):                              # one of each family on a cutscene name
            self.assertIn(charsel.SLOTS[cid], charsel.DEMO_SLOTS)
        self.assertIn(charsel.SLOTS["moblin"], charsel.TALL_SLOTS)                # the Moblin on the tall box
        self.assertNotIn(charsel.SLOTS["gerudo"], charsel.TALL_SLOTS)            # Urbosa: its heap is too small

    def test_fit_heap(self):
        # (the first in-game check: Paya, 97 bones, loaded on a 1.1 MB name with Link's file retargeted; Zelda, 179
        # bones, didn't on 831 KB or 1.1 MB)
        files = [("UMii_Common_Body_W_Animation", True), (charsel.PLAYER_ANIM, True)]
        count = {"UMii_Common_Body_W_Animation": 254, charsel.PLAYER_ANIM: 1167}.get
        self.assertEqual(charsel.fit_heap(files, 97, 1_099_000, count)[0], files)
        for heap in (831_520, 1_123_576):
            out, need = charsel.fit_heap(files, 179, heap, count)
            self.assertEqual(out, [("UMii_Common_Body_W_Animation", True), (charsel.PLAYER_ANIM, False)])
            self.assertLessEqual(need, heap)
        self.assertEqual(charsel.fit_heap(files, None, 831_520, count), (files, None))   # unknown: as before
        self.assertEqual(charsel.fit_heap([("Enemy_Bokoblin_Animation", False)], 75, 554_232, count)[1],
                         charsel.HEAP_BASE)

    def test_own_moves_2026_11_14(self):
        # Paya: her own shy stand from her own file (not retargeted: made for her skeleton, as her game pack has it)
        paya = charsel.RECIPES["sheikah"]
        self.assertEqual(dict(paya.own)["Wait"], "Npc_Shiekah_Heir_Talk_Shy_Wait")
        self.assertEqual(paya.anims[0], ("Npc_Shiekah_Heir_Animation", False))
        self.assertEqual(paya.v, 4)                       # (v4: her hair's cloth, 2026-11-14)
        # Mipha: a Zora woman's swim; the Zora men's file (and its man's Default_Wait) is gone
        mipha = charsel.RECIPES["zora"]
        self.assertEqual([f for f, _ in mipha.anims],
                         ["UMii_Common_Body_W_Animation", "UMii_Zora_BodyC_W_Animation", charsel.PLAYER_ANIM])
        self.assertEqual(mipha.v, 6)                      # (v6: her sash and fins' cloth)
        # Urbosa: no Gerudo guards' file any more (the real Urbosa never loads it): her actor's women's file first
        urbosa = charsel.RECIPES["gerudo"]
        self.assertEqual([f for f, _ in urbosa.anims], ["UMii_Common_Body_W_Animation", charsel.PLAYER_ANIM])
        # v5 (the round-4 game check): her actor's hip-shot stand and walk, the Active branch of its sets
        self.assertEqual((dict(urbosa.own)["Wait"], dict(urbosa.own)["Walk"]), ("Positive_Wait", "Positive_Move_Walk"))
        self.assertEqual(urbosa.v, 6)                     # (v6: her skirt and hair's cloth)
        # the women's sprint: their run, faster (not the panic run)
        for cid in ("zelda", "sheikah", "gerudo", "zora"):
            r = charsel.RECIPES[cid]
            self.assertEqual(dict(r.own)["RunGear2"], "Common_Move_Run", cid)
            self.assertEqual(charsel.frame_ctrl(r)["RunGear2"], (charsel.SPRINT_RATE, None, None), cid)
        self.assertEqual(charsel.RECIPES["linkle"].v, 4)   # (v4, 2026-11-18: no costume, her body is Link's)
        self.assertTrue(charsel.RECIPES["zelda"].legs)      # (v6: legs under the dress)
        self.assertEqual(charsel.RECIPES["zelda"].v, 6)
        # the resolve: Paya's Wait comes from her own file, the rest of her own from the women's
        clips = {"Npc_Shiekah_Heir_Animation": {"Npc_Shiekah_Heir_Talk_Shy_Wait"},
                 "UMii_Common_Body_W_Animation": {c for _n, c in charsel.UMII_W}}
        res = charsel.resolve(paya, lambda f: None if f == charsel.PLAYER_ANIM else clips.get(f, set()))
        self.assertEqual((res["missing"], res["clash"]), ([], []))
        self.assertEqual(res["own"]["Wait"], "Npc_Shiekah_Heir_Talk_Shy_Wait")

    def test_monster_swings_2026_11_14(self):
        # each swing for each weapon, timed to Link's hit (rates in 0.8..1.6 by the research's rule), and wading
        for cid in ("bokoblin", "moblin"):
            r = charsel.RECIPES[cid]
            own, ctrl = dict(r.own), charsel.frame_ctrl(r)
            for n in ("CutS1", "CutS2", "CutS3", "CutSF", "CutDash", "CutS1_Lsword", "CutSF_Lsword") + \
                    charsel.EXTRA_NAMES:
                self.assertIn(n, own, f"{cid} {n}")
                self.assertIn(n, ctrl, f"{cid} {n}")
                rate, start, end = ctrl[n]
                self.assertTrue(0.8 <= rate <= 1.6 and start >= 0 and (end is None or end > start), f"{cid} {n}")
            self.assertEqual((own["SwimWait"], own["SwimMove"]), ("Wait", "Walk"))
        moblin = dict(charsel.RECIPES["moblin"].own)                            # no stab for a sword any more
        self.assertNotEqual(moblin["CutS1"], moblin["CutS1_Spear"])
        # 0.9.5: every swing starts SWING_LEAD_S further into its clip (the costume starts a frame or two after Link;
        # the Bokoblin's hit came 0.16 s late in the round-4 check), the sprint's frame control untouched
        boko = charsel.frame_ctrl(charsel.RECIPES["bokoblin"])
        self.assertEqual(boko["CutS1"], (1.21, 25.0, None))                    # (23 + 0.05 s x 30 x 1.21)
        self.assertEqual(charsel.frame_ctrl(charsel.RECIPES["moblin"])["CutS1"], (0.8, 18.0, 30))
        self.assertEqual(boko["SwimMove"], (0.6, None, None))
        self.assertEqual((charsel.RECIPES["bokoblin"].v, charsel.RECIPES["moblin"].v), (4, 4))   # (v4: their cloth)
        bad = charsel.Recipe("bad", "Bad", "Monsters", "pack", "X", (), (("Wait", "W"),), rest="speed",
                             ctrl=(("CutS1", 1.0, 0, None),))
        with self.assertRaises(charsel.CharselError):                           # frame control for a name it lacks
            charsel.frame_ctrl(bad)

    def test_frame_ctrl_set(self):
        # the game's own frame control on the clip node (Extend > FrameCtrl > Rate/StartFrame/EndFrame)
        bas = charsel.as_file("Attack_Small", 1.21, 23, None)
        el = aamp.load(bas).list("Elements").list("Element0")
        self.assertEqual(el.object("Parameters").get_value("FileName"), "Attack_Small")
        self.assertEqual(el.object("Parameters").get_value("TypeIndex"), 67)
        rate, start, end = charsel.as_ctrl(bas)
        self.assertAlmostEqual(rate, 1.21, places=5)
        self.assertEqual((start, end), (23.0, None))
        self.assertEqual(charsel.as_file("Wait"), glp.as_file("Wait"))         # no control: as before
        self.assertEqual(charsel.as_ctrl(glp.as_file("Wait")), (None, None, None))
        # 0.9.5: an attack set starts at once (its node's Morph 0, as Link's own attack nodes)
        for bas in (charsel.as_file("Sword_Attack_S1", morph=0.0), charsel.as_file("Attack_Small", 1.21, 25, None, 0.0)):
            p = aamp.load(bas).list("Elements").list("Element0").object("Parameters")
            self.assertEqual((p.get_value("Morph"), p.get_value("TypeIndex")), (0.0, 67))
        self.assertAlmostEqual(charsel.as_ctrl(charsel.as_file("Attack_Small", 1.21, 25, None, 0.0))[0], 1.21, places=5)

    def test_find_id(self):
        cases = {"zelda": "zelda", "ZELDA": "zelda", " Daruk ": "goron", "paya": "sheikah", "Urbosa": "gerudo",
                 "boko": "bokoblin", "Paya (Sheikah)": "sheikah", "mipha": "zora", "hateno_villager": None,
                 "villager": "hylian", "ganon": None, "": None, None: None, 3: None, "link": "link"}
        for text, want in cases.items():
            self.assertEqual(charsel.find_id(text), want, text)

    def test_look_message_and_display(self):
        self.assertEqual(charsel.look_message("goron"), {"id": "goron", "v": 4, "name": "Daruk (Goron)"})
        self.assertEqual(charsel.display({"id": "goron"}), "Daruk (Goron)")
        self.assertEqual(charsel.display({"id": "newer-one", "name": "Ganon"}), "Ganon")
        self.assertEqual(charsel.display(None), "Link")


class Clips(unittest.TestCase):
    def test_clip_map(self):
        m = charsel.clip_map(TEST_RECIPE)
        self.assertEqual(set(m), GHOST)                                          # Link's clips fill the rest
        self.assertEqual(m["Wait"], "Own_Wait")
        self.assertEqual(m["ClimbMove"], dict((n, c) for n, c, _ in glp.ANIMS)["ClimbMove"])
        self.assertEqual(charsel.clip_map(MONSTER), {"Wait": "W", "CutS1": "A"})  # speed: own clips only
        self.assertEqual(set(charsel.clip_map(MONSTER, "link")), GHOST)          # --monster-link-clips
        bad = charsel.Recipe("bad", "Bad", "Races", "pack", "X", (), (("NotAGhostName", "Clip"),))
        with self.assertRaises(charsel.CharselError):
            charsel.clip_map(bad)

    def test_anim_files(self):
        self.assertEqual(charsel.anim_files(MONSTER), [("FamilyFile", False)])
        self.assertEqual(charsel.anim_files(MONSTER, "link"), [("FamilyFile", False), (charsel.PLAYER_ANIM, True)])
        self.assertEqual(charsel.anim_files(TEST_RECIPE, "speed"), [("FamilyFile", True)])

    def test_resolve(self):
        fam = {"Own_Wait", "Own_Walk", "Own_Run"}
        res = charsel.resolve(TEST_RECIPE, lambda f: None if f == charsel.PLAYER_ANIM else fam)
        self.assertEqual((len(res["own"]), len(res["link"]), res["missing"], res["clash"]), (3, 30, [], []))
        # an own clip must be in the family's files (Link's file is trusted for Link's 33 clips only)
        res = charsel.resolve(TEST_RECIPE, lambda f: None if f == charsel.PLAYER_ANIM else {"Own_Wait"})
        self.assertEqual(sorted(res["missing"]), ["Run->Own_Run", "Walk->Own_Walk"])
        res = charsel.resolve(MONSTER, lambda f: {"W"})
        self.assertEqual(res["missing"], ["CutS1->A"])
        # a Link clip name that the family file also has: the family's would play first [G]: a clash
        climb = dict((n, c) for n, c, _ in glp.ANIMS)["ClimbMove"]
        res = charsel.resolve(TEST_RECIPE, lambda f: None if f == charsel.PLAYER_ANIM else fam | {climb})
        self.assertEqual(res["clash"], [f"{climb} is found first in FamilyFile"])


class Packs(unittest.TestCase):
    def make(self, recipe=TEST_RECIPE, slot=charsel.DEMO_SLOTS[0], profile="DemoNPC", donor=None, rest=None):
        donor = donor or donor_pack(recipe.donor)
        return charsel.make_pack(recipe, slot, profile, template_pack(), donor, rest)

    def test_pack_files(self):
        files = self.make()
        lt = aamp.load(files[f"Actor/ActorLink/{charsel.DEMO_SLOTS[0]}.bxml"]).object("LinkTarget")
        self.assertEqual({k: lt.get_value(k) for k in ("ModelUser", "ASUser", "PhysicsUser", "AnimationInfo",
                                                       "UMiiUser", "AIProgramUser", "ProfileUser")},
                         {"ModelUser": "SwChar_madeup", "ASUser": "SwChar_madeup", "PhysicsUser": "Dummy",
                          "AnimationInfo": "Dummy", "UMiiUser": "Dummy", "AIProgramUser": "DemoDummy",
                          "ProfileUser": "DemoNPC"})
        self.assertIn("Actor/AIProgram/DemoDummy.baiprog", files)
        self.assertNotIn("Actor/Physics/MadeUp_Phys.bphysics", files)          # (no physics, no look of the template)
        self.assertNotIn("Actor/UMii/MadeUp_Mii.bumii", files)
        self.assertEqual(files["Actor/ModelList/SwChar_madeup.bmodellist"], model_list("MadeUpBody"))   # the donor's
        asl = aamp.load(files["Actor/ASList/SwChar_madeup.baslist"])
        names = [o.get_value("Name") for o in asl.list("ASDefines").objects.values()]
        self.assertEqual(names, sorted(GHOST, key=str.encode))                 # sorted by bytes: binary-searched
        adds = [(o.get_value("Anim"), o.get_value("RetargetModel")) for o in asl.list("AddReses").objects.values()]
        # the donor's own list doesn't retarget its own file: neither do we; Link's file is retargeted
        self.assertEqual(adds, [("FamilyFile", None), (charsel.PLAYER_ANIM, "Skeleton")])
        self.assertEqual(len(asl.list("CFDefines").lists), len(GHOST))          # blend frames, as the Link body's
        for n in GHOST:
            el = aamp.load(files[f"Actor/AS/SwChar_madeup_{n}.bas"]).list("Elements").list("Element0")
            self.assertEqual(el.object("Parameters").get_value("FileName"), charsel.clip_map(TEST_RECIPE)[n])

    def test_look_file_body(self):
        donor = donor_pack("Npc_MadeUp", umii="MadeUpLook")
        files = self.make(donor=donor, slot=charsel.NPC_SLOTS[0], profile="NPC")
        lt = aamp.load(files[f"Actor/ActorLink/{charsel.NPC_SLOTS[0]}.bxml"]).object("LinkTarget")
        self.assertEqual((lt.get_value("UMiiUser"), lt.get_value("ProfileUser")), ("MadeUpLook", "NPC"))
        self.assertEqual(files["Actor/UMii/MadeUpLook.bumii"], b"made-up look")
        donor.pop("Actor/UMii/MadeUpLook.bumii")
        with self.assertRaises(charsel.CharselError):                           # no model data and no look file
            self.make(donor=donor)

    def test_refusals(self):
        with self.assertRaises(charsel.CharselError):
            self.make(slot=charsel.LINK_ACTOR)                                 # the Link body's name: never
        with self.assertRaises(charsel.CharselError):
            self.make(slot="Enemy_Lynel_Junior")                               # only the free names
        t = template_pack()
        t[f"Actor/ActorLink/{charsel.TEMPLATE}.bxml"] = actor_link(ModelUser="SwChar_zelda", ASUser="MadeUp_AS")
        with self.assertRaises(charsel.CharselError):                           # a template that is ours already
            charsel.make_pack(TEST_RECIPE, charsel.DEMO_SLOTS[0], "DemoNPC", t, donor_pack("Npc_MadeUp"))
        with self.assertRaises(charsel.CharselError):
            charsel.make_pack(charsel.RECIPES["link"], charsel.DEMO_SLOTS[0], "DemoNPC", template_pack(), {})

    def test_type2_targets_take_the_link_bodys(self):
        # (2026-11-13: every hero list, targets 0 0 3 2, was never created; Paya's 0 1 3 was)
        donor = donor_pack("Npc_MadeUp", targets=HERO_TARGETS)
        files = self.make(donor=donor)
        ml = aamp.load(files["Actor/ModelList/SwChar_madeup.bmodellist"])
        self.assertEqual(charsel.target_types(ml), [0, 1, 3])
        self.assertEqual(charsel.target_bones(ml), {"Root", "Spine_1", "Face_Root"})
        self.assertEqual(ml.list("ModelData").list("ModelData_0").object("Base").get_value("Folder"), "MadeUpBody")
        data = glp.pack_bytes(files)[0]
        self.assertEqual(charsel.check_pack(data, charsel.DEMO_SLOTS[0]), [])
        self.assertEqual(charsel.read_back(data)["targets"], [0, 1, 3])
        bones = ["Root", "Spine_1", "Face_Root", "Arm_1_L"]
        files = charsel.make_pack(TEST_RECIPE, charsel.DEMO_SLOTS[0], "DemoNPC", template_pack(), donor, None, None,
                                  bones)
        self.assertEqual(charsel.target_types(aamp.load(files["Actor/ModelList/SwChar_madeup.bmodellist"])), [0, 1, 3])
        with self.assertRaises(charsel.CharselError) as e:                      # a body without a bone they name
            charsel.make_pack(TEST_RECIPE, charsel.DEMO_SLOTS[0], "DemoNPC", template_pack(), donor, None, None,
                              ["Root", "Spine_1"])
        self.assertIn("Face_Root", str(e.exception))
        t = template_pack()                                                     # nothing to copy: refused
        t["Actor/ModelList/MadeUp_Model.bmodellist"] = target_list(HERO_TARGETS)
        with self.assertRaises(charsel.CharselError):
            charsel.make_pack(TEST_RECIPE, charsel.DEMO_SLOTS[0], "DemoNPC", t, donor)
        # lists without a type-2 target stay byte for byte (Paya's, the monsters')
        for targets in (LINK_BODY_TARGETS, ((0, ("Root",)), (0, ("Root",)))):
            ml = target_list(targets, "MadeUpBody")
            self.assertEqual(charsel.body_targets(ml, template_pack()), (ml, False))
        # a pack that still has one is refused by the check
        hero = charsel._pack_files("madeup", charsel.DEMO_SLOTS[0], "DemoNPC", template_pack(),
                                   target_list(HERO_TARGETS, "MadeUpBody"), None, [("FamilyFile", True)],
                                   charsel.clip_map(TEST_RECIPE))
        self.assertTrue(any("type-2" in b for b in charsel.check_pack(glp.pack_bytes(hero)[0])))

    def test_read_back_and_check(self):
        data, unpacked = glp.pack_bytes(self.make())
        self.assertEqual(charsel.check_pack(data, charsel.DEMO_SLOTS[0]), [])
        rb = charsel.read_back(data)
        self.assertEqual((rb["slot"], rb["unpacked"], len(rb["names"])), (charsel.DEMO_SLOTS[0], unpacked, len(GHOST)))
        self.assertLess(unpacked, charsel.PACK_LIMIT)
        self.assertIn("expected", charsel.check_pack(data, charsel.DEMO_SLOTS[1])[0])
        # an unsorted list is caught
        files = self.make()
        asl = aamp.load(files["Actor/ASList/SwChar_madeup.baslist"])
        objs = list(asl.list("ASDefines").objects.items())
        asl.list("ASDefines").objects = dict(reversed(objs))
        files["Actor/ASList/SwChar_madeup.baslist"] = aamp.dump(asl)
        self.assertTrue(any("sorted" in b for b in charsel.check_pack(glp.pack_bytes(files)[0])))
        # a profile that doesn't match the name's class is caught
        files = self.make(profile="NPC")
        self.assertTrue(any("profile" in b for b in charsel.check_pack(glp.pack_bytes(files)[0])))
        self.assertTrue(charsel.check_pack(b"not a pack"))

    def test_never_locked_on_talked_to_or_hit(self):
        # (the self puppet and the ghost: no attention list (lock-on, talk) and no damage, as the template's Dummy)
        def check(**users):
            t = template_pack()
            t[f"Actor/ActorLink/{charsel.TEMPLATE}.bxml"] = actor_link(
                AIProgramUser="DemoDummy", ASUser="MadeUp_AS", ModelUser="MadeUp_Model", **users)
            files = charsel.make_pack(TEST_RECIPE, charsel.DEMO_SLOTS[0], "DemoNPC", t, donor_pack("Npc_MadeUp"))
            return charsel.check_pack(glp.pack_bytes(files)[0], charsel.DEMO_SLOTS[0])
        self.assertEqual(check(AttentionUser="Dummy", DamageParamUser="Dummy"), [])
        for user, value in (("AttentionUser", "NpcStandard"), ("DamageParamUser", "Npc")):
            with self.subTest(user=user):
                bad = check(**{user: value})
                self.assertTrue(any(user in b and "locked on" in b for b in bad), bad)

    def test_model_copy(self):
        files = charsel.make_copy_pack("linkle", charsel.NPC_SLOTS[4], "NPC", template_pack(), pause_pack())
        ml = aamp.load(files["Actor/ModelList/SwChar_linkle.bmodellist"])
        self.assertEqual(ml.list("ModelData").list("ModelData_0").object("Base").get_value("Folder"), "SwChar_linkle")
        data = glp.pack_bytes(files)[0]
        self.assertEqual(charsel.check_pack(data, charsel.NPC_SLOTS[4]), [])
        rb = charsel.read_back(data)
        self.assertEqual(rb["adds"], [(charsel.PLAYER_ANIM, None)])             # Link's own skeleton: no retarget
        self.assertEqual(rb["clips"], {n: c for n, c, _ in glp.ANIMS})
        with self.assertRaises(charsel.CharselError):
            charsel.make_copy_pack("zelda", charsel.NPC_SLOTS[4], "NPC", template_pack(), pause_pack())


def fake_romfs(root: Path, donors: dict[str, dict], loose: tuple = ()) -> None:
    """A made-up romfs: Actor/Pack holds the template and the donors (and any `loose` free names, to be refused);
    Model/<file>.sbfres holds made-up clip lists (bfres_clips is patched to read them as JSON)."""
    packs = root / "Actor" / "Pack"
    packs.mkdir(parents=True)
    pack = lambda files: yaz0_compress(sarc_build(sorted(files.items())))   # noqa: E731
    (packs / f"{charsel.TEMPLATE}.sbactorpack").write_bytes(pack(template_pack()))
    for actor, files in donors.items():
        (packs / f"{actor}.sbactorpack").write_bytes(pack(files))
    for name in loose:
        (packs / f"{name}.sbactorpack").write_bytes(pack(template_pack()))
    (root / "Model").mkdir()
    clips = {"UMii_Common_Body_W_Animation": ["Default_Wait", "Default_Move_Walk", "Common_Move_Run",
                                              "Common_Move_Escape", "Common_Crouch", "Common_Stagger_F_L"],
             "UMii_Goron_BodyC_M_Animation": ["Default_Wait", "Default_Move_Walk", "Common_Move_Run",
                                              "Common_Move_Ball", "Common_Crouch", "Common_Stagger_F_L"],
             "Enemy_Bokoblin_Animation": sorted({c for _, c in charsel.BOKO})}
    for name, names in clips.items():
        (root / "Model" / f"{name}.sbfres").write_text(json.dumps(names), encoding="utf-8")


class Build(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.romfs, self.out = self.root / "romfs", self.root / "out" / "charsel-packs"
        patches = [mock.patch.object(charsel, "bfres_clips", lambda d: json.loads(d)),
                   mock.patch.object(charsel, "read_actorinfo",
                                     lambda romfs: {s: {"profile": p, "instSize": 900_000}
                                                    for s, p in charsel.SLOT_PROFILE.items()})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def build(self, ids, donors, **kw):
        fake_romfs(self.romfs, donors, kw.pop("loose", ()))
        return charsel.build(self.romfs, self.out, ids, cache=self.root / "cache.json", say=quiet, **kw)

    def test_build_check_and_rebuild(self):
        donors = {"Dm_Npc_Zelda_Sibyl": donor_pack("Dm_Npc_Zelda_Sibyl", "Npc_Zelda_Miko",
                                                   adds=(("UMii_Common_Body_W_Animation", True),)),
                  "Dm_Npc_Goron_Hero": donor_pack("Dm_Npc_Goron_Hero", "Npc_Goron_Hero"),
                  "Enemy_Bokoblin_Junior": donor_pack("Enemy_Bokoblin_Junior", "Enemy_Bokoblin")}
        cat = self.build(["zelda", "goron", "boko", "link"], donors)
        self.assertEqual(sorted(cat["built"]), ["bokoblin", "goron", "zelda"])  # (link: the Link body, no pack)
        self.assertEqual(cat["built"]["zelda"]["slot"], charsel.SLOTS["zelda"])
        self.assertEqual((cat["built"]["bokoblin"]["own"], cat["built"]["bokoblin"]["link"]), (len(charsel.BOKO), 0))
        self.assertEqual(cat["built"]["goron"]["own"] + cat["built"]["goron"]["link"], len(GHOST))
        self.assertTrue((self.out / "mod.toml").is_file())
        self.assertIn(charsel.MOD_ID, (self.out / "mod.toml").read_text(encoding="utf-8"))
        self.assertEqual(charsel.load_catalog(self.out)["built"].keys(), cat["built"].keys())
        self.assertEqual(charsel.check(self.out), {"zelda": [], "goron": [], "bokoblin": []})
        self.assertTrue((self.root / "cache.json").is_file())                  # clip names only
        self.assertTrue((self.out / "built.txt").is_file())                     # (made from the game's files)
        # a rebuild of one character keeps the others; a moved one leaves its old name; nothing else is touched
        mine = self.out / "romfs" / "Actor" / "Pack" / "Readme_of_mine.txt"
        mine.write_text("mine", encoding="utf-8")
        cat = charsel.build(self.romfs, self.out, ["zelda"], cache=self.root / "cache.json", say=quiet,
                            slots={"zelda": "Dm_Npc_Handmaid_B"})
        left = sorted(p.name for p in (self.out / "romfs" / "Actor" / "Pack").iterdir())
        self.assertEqual(left, sorted(["Readme_of_mine.txt", "Dm_Npc_Handmaid_B.sbactorpack",
                                       f"{charsel.SLOTS['goron']}.sbactorpack",
                                       f"{charsel.SLOTS['bokoblin']}.sbactorpack"]))
        self.assertEqual(sorted(cat["built"]), ["bokoblin", "goron", "zelda"])
        self.assertEqual(cat["kept"], ["bokoblin", "goron"])
        self.assertEqual(cat["built"]["zelda"]["slot"], "Dm_Npc_Handmaid_B")
        self.assertEqual(charsel.check(self.out), {"zelda": [], "goron": [], "bokoblin": []})
        # a kept one's name can't be taken by another without rebuilding it too
        with self.assertRaises(charsel.CharselError) as e:
            charsel.build(self.romfs, self.out, ["rito"], say=quiet, slots={"rito": charsel.SLOTS["goron"]})
        self.assertIn("goron", str(e.exception))
        # a kept pack from an older recipe version is rebuilt, not kept
        data = json.loads((self.out / "charsel.json").read_text(encoding="utf-8"))
        data["built"]["goron"]["v"] = 0
        (self.out / "charsel.json").write_text(json.dumps(data), encoding="utf-8")
        cat = charsel.build(self.romfs, self.out, ["zelda"], cache=self.root / "cache.json", say=quiet)
        self.assertEqual(sorted(cat["built"]), ["bokoblin", "zelda"])
        self.assertFalse((self.out / "romfs" / "Actor" / "Pack" / f"{charsel.SLOTS['goron']}.sbactorpack").exists())
        # a build of all of them starts over
        cat = charsel.build(self.romfs, self.out, None, cache=self.root / "cache.json", say=quiet)
        self.assertEqual(cat["kept"], [])

    def test_hero_lists_and_link_retarget(self):
        donors = {"Dm_Npc_Zelda_Sibyl": donor_pack("Dm_Npc_Zelda_Sibyl", "Npc_Zelda_Miko", targets=HERO_TARGETS,
                                                   adds=(("UMii_Common_Body_W_Animation", True),))}
        pack = self.out / "romfs" / "Actor" / "Pack" / f"{charsel.SLOTS['zelda']}.sbactorpack"
        cat = self.build(["zelda"], donors)
        self.assertEqual(cat["built"]["zelda"]["anim_targets"], "link-body")
        self.assertEqual(cat["built"]["zelda"]["v"], charsel.RECIPES["zelda"].v)
        rb = charsel.read_back(pack.read_bytes())
        self.assertEqual(rb["targets"], [0, 1, 3])
        self.assertEqual(rb["adds"], [("UMii_Common_Body_W_Animation", "Skeleton"), (charsel.PLAYER_ANIM, "Skeleton")])
        self.assertEqual(charsel.check(self.out), {"zelda": []})
        for how, want in (("off", None), ("on", "Skeleton")):
            charsel.build(self.romfs, self.out, ["zelda"], cache=self.root / "cache.json", say=quiet,
                          link_retarget=how)
            self.assertEqual(charsel.read_back(pack.read_bytes())["adds"][-1], (charsel.PLAYER_ANIM, want), how)
        with self.assertRaises(charsel.CharselError):
            charsel.build(self.romfs, self.out, ["zelda"], say=quiet, link_retarget="maybe")
        with contextlib.redirect_stdout(io.StringIO()):                     # (an empty library of its own)
            self.assertEqual(charsel.main(["build", "zelda", "--romfs", str(self.romfs), "--out", str(self.out),
                                           "--library", str(self.root), "--cache", str(self.root / "cache.json"),
                                           "--link-retarget", "off"]), 0)
        self.assertEqual(charsel.read_back(pack.read_bytes())["adds"][-1], (charsel.PLAYER_ANIM, None))

    def test_never_exported_as_a_breath(self):
        import breath
        donors = {"Dm_Npc_Goron_Hero": donor_pack("Dm_Npc_Goron_Hero", "Npc_Goron_Hero")}
        self.build(["goron"], donors)
        lib = self.root / "lib"
        shutil.copytree(self.out, lib / "mods" / charsel.MOD_ID)              # (as modman add stores it)
        self.assertTrue(breath.game_made(str(lib), {"id": charsel.MOD_ID}))
        # the Link body's folder too
        link_out = self.root / "link-body"
        with mock.patch.object(glp, "build_pack", lambda a, b, slots="sheikah": {"Actor/X.bxml": b"made up"}), \
                mock.patch.object(glp, "link_target", lambda files, actor: (None, type("L", (), {
                    "get_value": staticmethod(lambda k: "Dummy")})())), \
                mock.patch.object(glp, "read_pack", lambda path: {}), \
                mock.patch.object(glp, "find_pack", lambda romfs, actor: romfs / "x"):
            glp.build(self.romfs, link_out, say=quiet)
        shutil.copytree(link_out, lib / "mods" / "botw-mod-loader.ghost-link-body")
        self.assertTrue(breath.game_made(str(lib), {"id": "botw-mod-loader.ghost-link-body"}))

    def test_size_table_2026_11_14(self):
        """0.9.5: the characters mod ships a size table: the game's own with an entry for each pack of ours and each
        file of ours inside it (sized from the game's file of that kind nearest in size), the game's own files left
        alone; a model file of ours (Link's copy) from Link's (his entry over his size), its textures as play-as sizes
        Link's. No game table: no table, said."""
        import struct
        import zlib
        import bnp
        donors = {"Dm_Npc_Goron_Hero": donor_pack("Dm_Npc_Goron_Hero", "Npc_Goron_Hero")}
        fake_romfs(self.romfs, donors)
        tpl = (self.romfs / "Actor" / "Pack" / f"{charsel.TEMPLATE}.sbactorpack").read_bytes()
        files = template_pack()
        link_model, link_tex = b"L" * 1000, b"T" * 2000
        (self.romfs / "Model" / "Link.sbfres").write_bytes(link_model)
        (self.romfs / "Model" / "Link.Tex.sbfres").write_bytes(link_tex)
        game = {f"Actor/Pack/{charsel.TEMPLATE}.bactorpack": len(charsel._unyaz(tpl)) + 512,
                "Model/Link.bfres": 1576, "Model/Link.Tex.bfres": 2000 + 8208}
        for n, d in files.items():
            game[n] = len(d) * 3 + 100
        raw = b"RSTB" + struct.pack("<II", len(game), 0) + b"".join(
            struct.pack("<II", c, v) for c, v in sorted((zlib.crc32(n.encode()), v) for n, v in game.items()))
        table = self.romfs / "System" / "Resource" / "ResourceSizeTable.product.srsizetable"
        table.parent.mkdir(parents=True)
        table.write_bytes(raw)
        slot = charsel.SLOTS["goron"]
        # default (2026-11-14 game check: pack entries broke every costume): no entries for the packs
        cat = charsel.build(self.romfs, self.out, ["goron"], cache=self.root / "cache.json", say=quiet)
        self.assertNotIn(f"Actor/Pack/{slot}.bactorpack", cat["sizes"]["entries"])
        with mock.patch.object(charsel, "SIZE_PACKS", True):
            cat = charsel.build(self.romfs, self.out, ["goron"], cache=None, say=quiet)
        sizes = cat["sizes"]["entries"]
        self.assertIn(f"Actor/Pack/{slot}.bactorpack", sizes)
        self.assertIn("Actor/ASList/SwChar_goron.baslist", sizes)
        self.assertIn("Actor/AS/SwChar_goron_CutS1.bas", sizes)
        self.assertNotIn("Actor/AIProgram/DemoDummy.baiprog", sizes)            # (the game's own, copied as it is)
        pack = (self.out / "romfs" / "Actor" / "Pack" / f"{slot}.sbactorpack").read_bytes()
        import ukmm
        self.assertEqual(sizes[f"Actor/Pack/{slot}.bactorpack"],
                         ukmm.estimate_entry(len(charsel._unyaz(tpl)), game[f"Actor/Pack/{charsel.TEMPLATE}.bactorpack"],
                                             len(charsel._unyaz(pack)), True))
        shipped = bnp.Rstb((self.out / "romfs" / charsel.RSTB_REL).read_bytes())
        for n, v in sizes.items():
            self.assertEqual(shipped.get(n), v, n)
        self.assertEqual(shipped.get("Model/Link.bfres"), 1576)               # (the game's entries kept)
        self.assertEqual(charsel.check(self.out), {"goron": []})
        # a model file of ours (Link's own copy, --model-copies; 2026-11-18: no Linkle costume any more), sized from
        # Link's
        pm = {"id": "hero", "name": "Hero Skin", "version": "1", "fp": "f" * 16, "linkle": False}
        with mock.patch.object(charsel, "player_model_mod", lambda lib, romfs=None: pm):
            fake_romfs(self.root / "romfs3", {glp.DONOR_LINK: pause_pack(), **donors})
            r3 = self.root / "romfs3"
            for n, d in (("Link.sbfres", link_model), ("Link.Tex.sbfres", link_tex)):   # (Yaz0, as the game's)
                (r3 / "Model" / n).write_bytes(yaz0_compress(d))
            (r3 / "System" / "Resource").mkdir(parents=True)
            shutil.copy(table, r3 / "System" / "Resource" / table.name)
            cat = charsel.build(r3, self.out, ["link", "goron"], library=self.root, model_copies=True, cache=None,
                                say=quiet)
        sizes = cat["sizes"]
        self.assertEqual(sizes["model_ratio"], 1.576)
        self.assertEqual(sizes["entries"]["Model/SwChar_link.bfres"], bnp.round32(1000 * 1576 // 1000 + 1))
        self.assertEqual(sizes["entries"]["Model/SwChar_link.Tex.bfres"],
                         max(ukmm.estimate_entry(2000, 2000 + 8208, 2000, False), bnp.round32(2000) + 8208))
        self.assertEqual(charsel.check(self.out), {"goron": [], "link": []})
        # a table that lost an entry is named by check; no game table: no table shipped, and said
        t = bnp.Rstb((self.out / "romfs" / charsel.RSTB_REL).read_bytes())
        t.apply({"Model/SwChar_link.bfres": 0})
        (self.out / "romfs" / charsel.RSTB_REL).write_bytes(t.to_bytes())
        self.assertIn("sizes", charsel.check(self.out))
        table.unlink()
        cat = charsel.build(self.romfs, self.out, ["goron"], cache=self.root / "cache.json", say=quiet)
        self.assertFalse((self.out / "romfs" / charsel.RSTB_REL).exists())
        self.assertFalse(cat["sizes"]["table"])
        self.assertTrue(any("size table" in n for n in cat["notes"]))

    def test_skips_say_why(self):
        donors = {"Dm_Npc_Goron_Hero": donor_pack("Dm_Npc_Goron_Hero", "Npc_Goron_Hero")}
        cat = self.build(["zelda", "goron", "bokoblin", "linkle"], donors, loose=(charsel.SLOTS["goron"],))
        self.assertIn("No pack for Dm_Npc_Zelda_Sibyl", cat["skipped"]["zelda"])   # no donor
        self.assertIn("isn't free", cat["skipped"]["goron"])                    # the name has a pack in the game
        self.assertIn("No pack for Enemy_Bokoblin_Junior", cat["skipped"]["bokoblin"])
        self.assertIn("no Linkle mod", cat["skipped"]["linkle"])   # (her costume, friends' view: needs the mod)
        self.assertEqual(cat["built"], {})

    def test_missing_clip_skips(self):
        donors = {"Dm_Npc_Zelda_Sibyl": donor_pack("Dm_Npc_Zelda_Sibyl", "Npc_Zelda_Miko")}
        fake_romfs(self.romfs, donors)
        (self.romfs / "Model" / "UMii_Common_Body_W_Animation.sbfres").write_text('["Default_Wait"]', encoding="utf-8")
        cat = charsel.build(self.romfs, self.out, ["zelda"], cache=None, say=quiet)
        self.assertIn("clips missing", cat["skipped"]["zelda"])

    def test_refusals(self):
        fake_romfs(self.romfs, {})
        with self.assertRaises(charsel.CharselError):
            charsel.build(self.romfs, self.romfs / "inside", ["zelda"], say=quiet)   # never inside the game's files
        with self.assertRaises(charsel.CharselError):
            charsel.build(self.romfs, self.out, ["ganon"], say=quiet)
        with self.assertRaises(charsel.CharselError):
            charsel.build(self.romfs, self.out, ["zelda", "goron"], say=quiet,
                          slots={"goron": charsel.SLOTS["zelda"]})              # two on one name
        # Link's and Linkle's own names count only with --model-copies
        charsel.build(self.romfs, self.out, ["rito", "link"], say=quiet, slots={"rito": charsel.SLOTS["link"]})
        with self.assertRaises(charsel.CharselError):
            charsel.build(self.romfs, self.out, ["rito", "link"], say=quiet, slots={"rito": charsel.SLOTS["link"]},
                          model_copies=True)
        with self.assertRaises(charsel.CharselError):
            charsel.build(self.root / "nothing", self.out, say=quiet)
        with self.assertRaises(charsel.CharselError):
            charsel.parse_slots(["zelda=Enemy_Lynel_Junior"])
        self.assertEqual(charsel.parse_slots(["Zelda=Dm_Npc_Handmaid_A"]), {"zelda": "Dm_Npc_Handmaid_A"})

    def test_linkle_from_the_library(self):
        lib = self.root / "lib"
        title = lib / "mods" / "the-linkle-mod" / "01007EF00011E000" / "romfs" / "Pack" / "TitleBG.pack"
        title.parent.mkdir(parents=True)
        title.write_bytes(sarc_build([("Model/Link.sbfres", b"made-up Linkle model"),
                                      ("Model/Link.Tex.sbfres", b"made-up Linkle textures")]))
        other = lib / "mods" / "off-mod" / "romfs" / "Model"
        other.mkdir(parents=True)
        (other / "Link.sbfres").write_bytes(b"disabled")
        (lib / "config.json").write_text(json.dumps({"mods": [
            {"id": "the-linkle-mod", "name": "The Linkle Mod", "enabled": True, "version": "3.0.1"},
            {"id": "off-mod", "name": "Off", "enabled": False}]}), encoding="utf-8")
        pm = charsel.player_model_mod(lib)
        self.assertEqual((pm["id"], pm["linkle"], len(pm["fp"])), ("the-linkle-mod", True, 16))
        cat = self.build(["linkle"], {}, library=lib)                           # (no pause-menu Link here)
        self.assertEqual(cat["linkle"]["how"], "link-body")                    # the Link body *is* Linkle there
        self.assertIn(glp.DONOR_LINK, cat["skipped"]["linkle"])                 # her costume needs it: said
        # her costume, for the FRIENDS' view (review C1 2026-11-18: only the self-puppet went): the mod's own model
        # (made up here, so no hair merged: said) on its own free name
        tmp = self.root / "romfs2"
        fake_romfs(tmp, {glp.DONOR_LINK: pause_pack()})
        cat = charsel.build(tmp, self.out, ["linkle"], library=lib, cache=None, say=quiet)
        self.assertEqual(cat["built"]["linkle"]["slot"], charsel.SLOTS["linkle"])
        self.assertEqual((cat["linkle"]["how"], cat["linkle"]["mod_on"]), ("link-body", True))
        self.assertEqual(cat["linkle"]["look"]["how"], "body only")
        self.assertTrue(any("hair and clothes" in n for n in cat["notes"]))
        self.assertEqual((self.out / "romfs" / "Model" / "SwChar_linkle.sbfres").read_bytes(), b"made-up Linkle model")
        self.assertTrue(charsel.linkle_ready(cat))
        self.assertEqual(charsel.Looks(cat).actor({"id": "linkle"}), (charsel.SLOTS["linkle"], ""))
        # the mod OFF: said as off, Linkle isn't the Link body here
        (lib / "config.json").write_text(json.dumps({"mods": [
            {"id": "the-linkle-mod", "name": "The Linkle Mod", "enabled": False, "version": "3.0.1"}]}),
            encoding="utf-8")
        self.assertIsNone(charsel.player_model_mod(lib))
        self.assertEqual(charsel.linkle_mod(lib)["enabled"], False)
        cat = charsel.build(tmp, self.out, ["linkle"], library=lib, cache=None, say=quiet)
        self.assertEqual((cat["linkle"]["how"], cat["linkle"]["mod"], cat["linkle"]["mod_on"]),
                         ("off", "The Linkle Mod", False))
        self.assertIn("linkle", cat["built"])                                  # (still built from its files)
        self.assertTrue(charsel.linkle_ready(cat))
        self.assertEqual(charsel.Looks(cat).actor({"id": "linkle"}), (charsel.SLOTS["linkle"], ""))
        # no costume built (an empty catalog) and the mod off: the Link body, said
        self.assertEqual(charsel.Looks({"linkle": {"how": "off"}}).actor({"id": "linkle"}),
                         (charsel.LINK_ACTOR, "the Linkle mod isn't installed here"))
        # another player-model mod: noted, not Linkle
        (lib / "config.json").write_text(json.dumps({"mods": [{"id": "off-mod", "name": "Hero Skin",
                                                               "enabled": True}]}), encoding="utf-8")
        cat = charsel.build(tmp, self.out, ["link"], library=lib, cache=None, say=quiet)
        self.assertEqual(cat["linkle"]["how"], "missing")
        self.assertTrue(any("Hero Skin" in n for n in cat["notes"]))
        self.assertIsNone(charsel.linkle_mod(lib))
        cat = charsel.build(tmp, self.out, ["linkle"], library=lib, cache=None, say=quiet)
        self.assertIn("no Linkle mod in your mod library (on or off)", cat["skipped"]["linkle"])
        self.assertFalse(charsel.linkle_ready(cat))


class LinkleBody(unittest.TestCase):
    """2026-11-18, stage 1 of out/research/linkle-body-swap-plan.md: picking Linkle turns The Linkle Mod ON as Link's
    own body (and the play-as mod OFF: it would win over her); anyone else turns it OFF."""

    MODS = [{"id": "the-linkle-mod", "name": "The Linkle Mod", "enabled": False},
            {"id": "giruka.ak47", "name": "Extra item", "enabled": True},
            {"id": charsel.PLAYAS_ID, "name": "Play as Daruk", "enabled": True}]

    @staticmethod
    def body_mod(lib, mod_id="the-linkle-mod"):
        """A mod folder in the library that replaces Link's model (what makes a "linkle" mod The Linkle Mod)."""
        f = Path(lib) / "mods" / mod_id / "romfs" / "Model"
        f.mkdir(parents=True, exist_ok=True)
        (f / "Link.sbfres").write_bytes(b"Linkle")

    def test_plan(self):
        with tempfile.TemporaryDirectory(prefix="charsel-plan-") as lib:
            self.body_mod(lib)
            on = charsel.linkle_plan(self.MODS, True, lib)
            self.assertEqual((on["mod"]["id"], on["enable"], on["disable"]), ("the-linkle-mod", ["the-linkle-mod"],
                                                                             [charsel.PLAYAS_ID]))
            self.assertEqual(charsel.linkle_plan(self.MODS, False, lib),
                             {"mod": self.MODS[0], "enable": [], "disable": []})
            lit = [dict(m, enabled=True) for m in self.MODS[:2]]
            self.assertEqual(charsel.linkle_plan(lit, False, lib)["disable"], ["the-linkle-mod"])
            self.assertEqual(charsel.linkle_plan(lit, True, lib)["enable"], [])      # (already on: nothing to do)
            self.assertIsNone(charsel.linkle_plan(self.MODS[1:], True, lib)["mod"])
            # review C2: a "linkle" add-on that doesn't replace Link's model isn't her, on or off, before or after her
            hair = {"id": "linkle-hair", "name": "Linkle Hair Recolor", "enabled": True}
            for mods in ([dict(self.MODS[0]), hair], [hair, dict(self.MODS[0])]):
                on = charsel.linkle_plan(mods, True, lib)
                self.assertEqual((on["mod"]["id"], on["enable"], on["disable"]),
                                 ("the-linkle-mod", ["the-linkle-mod"], []))
                self.assertEqual(charsel.linkle_plan([dict(m, enabled=True) for m in mods], False, lib)["disable"],
                                 ["the-linkle-mod"])
            self.assertIsNone(charsel.linkle_plan([hair], True, lib)["mod"])        # (only the add-on: not Ready)
            self.assertEqual(charsel.linkle_plan([{"id": "x", "name": "linkle hair"}, {"name": "no id"}], True,
                                                 lib)["enable"], [])
            self.assertTrue(charsel.is_linkle_mod("the-linkle-mod", "", lib))
            self.assertFalse(charsel.is_linkle_mod("linkle-hair", "Linkle Hair Recolor", lib))
            self.assertFalse(charsel.is_linkle_mod("the-linkle-mod", "", None))

    def test_who_am_i_undoes_only_its_own(self):
        """Review C3: picking anyone else turns off only a Linkle Mod Who am I turned on (not one you turned on on
        Home); C7: what it switched leaves the play-as state's turned_off (a later playas off can't turn it back on)."""
        with tempfile.TemporaryDirectory(prefix="charsel-own-") as d:
            self.body_mod(d)
            st = Path(d) / "state.json"
            charsel.save_playas({"id": "goron", "library": d, "turned_off": ["the-linkle-mod", "x"]}, st)
            yours = [dict(self.MODS[0], enabled=True)]
            off = charsel.linkle_plan(yours, False, d, owned=charsel.linkle_owned(st))
            self.assertEqual(off["disable"], [])                          # (you turned it on: left on)
            on = charsel.linkle_plan(self.MODS, True, d, owned=charsel.linkle_owned(st))
            charsel.linkle_switched(on, True, st)
            self.assertEqual(charsel.linkle_owned(st), ["the-linkle-mod"])
            self.assertEqual(charsel.load_playas(st)["turned_off"], ["x"])
            off = charsel.linkle_plan(yours, False, d, owned=charsel.linkle_owned(st))
            self.assertEqual(off["disable"], ["the-linkle-mod"])          # (its own: turned off again)
            charsel.linkle_switched(off, False, st)
            self.assertEqual(charsel.linkle_owned(st), [])

    def test_a_game_identical_link_model_doesnt_count(self):
        """(out\\showcase-lib: Emote animations ships TitleBG.pack filled from the game, Link's model included; the
        merge keeps The Linkle Mod's, so with the game's files it isn't "the mod that replaces Link's model")"""
        with tempfile.TemporaryDirectory(prefix="charsel-pm-") as d:
            root = Path(d)
            romfs = root / "romfs"
            (romfs / "Model").mkdir(parents=True)
            (romfs / "Model" / "Link.sbfres").write_bytes(b"the game's Link")
            lib = root / "lib"
            for mid, data in (("the-linkle-mod", b"Linkle"), ("emotes", b"the game's Link")):
                f = lib / "mods" / mid / "romfs" / "Model"
                f.mkdir(parents=True)
                (f / "Link.sbfres").write_bytes(data)
            (lib / "config.json").write_text(json.dumps({"mods": [
                {"id": "the-linkle-mod", "name": "The Linkle Mod", "enabled": True},
                {"id": "emotes", "name": "Emote animations", "enabled": True}]}), encoding="utf-8")
            self.assertEqual(charsel.player_model_mod(lib)["id"], "emotes")
            pm = charsel.player_model_mod(lib, romfs=romfs)
            self.assertEqual((pm["id"], pm["linkle"]), ("the-linkle-mod", True))

    def test_in_the_library(self):
        import modman as mm
        with tempfile.TemporaryDirectory(prefix="charsel-linkle-") as d:
            lib = mm.Library(d, None, [dict(m) for m in self.MODS])
            lib.save()
            self.body_mod(d)
            res = charsel.linkle_body(True, library=d)
            self.assertEqual(res, {"changed": ["The Linkle Mod", "Play as Daruk"], "mod": "The Linkle Mod"})
            got = {m["id"]: m["enabled"] for m in mm.Library.load(d).mods}
            self.assertEqual(got, {"the-linkle-mod": True, "giruka.ak47": True, charsel.PLAYAS_ID: False})
            self.assertEqual(charsel.linkle_body(True, library=d)["changed"], [])
            self.assertEqual(charsel.linkle_body(False, library=d)["changed"], ["The Linkle Mod"])
            self.assertFalse(mm.Library.load(d).mods[0]["enabled"])
            lib = mm.Library(d, None, [dict(self.MODS[1])])
            lib.save()
            with self.assertRaises(charsel.CharselError) as e:
                charsel.linkle_body(True, library=d)
            self.assertIn("Add it first", str(e.exception))


class LooksHere(unittest.TestCase):
    def catalog(self, **extra):
        cat = {"built": {"zelda": {"slot": charsel.SLOTS["zelda"]}, "goron": {"slot": charsel.SLOTS["goron"]},
                         "fake": {"slot": "Dm_Npc_Zelda_Sibyl"}, "bokoblin": {"slot": "Not_A_Free_Name"}}}
        cat.update(extra)
        return cat

    def test_actor_for_a_look(self):
        lk = charsel.Looks(self.catalog())
        self.assertEqual(lk.actor({"id": "zelda"}), (charsel.SLOTS["zelda"], ""))
        self.assertEqual(lk.actor({"id": "built:goron"}), (charsel.SLOTS["goron"], ""))
        self.assertEqual(lk.actor(None), (charsel.LINK_ACTOR, ""))                 # an older client: the Link body
        self.assertEqual(lk.actor({"id": "link"}), (charsel.LINK_ACTOR, ""))
        self.assertEqual(lk.actor({"id": "rito"}), (charsel.LINK_ACTOR, "not built here"))
        self.assertEqual(lk.actor({"id": "bokoblin"}), (charsel.LINK_ACTOR, "not built here"))   # (a bad slot: dropped)
        self.assertEqual(lk.actor({"id": "ganon"})[0], charsel.LINK_ACTOR)
        self.assertIn("unknown", lk.actor({"id": "ganon"})[1])
        self.assertEqual(lk.actor({"id": "linkle"}), (charsel.LINK_ACTOR, "the Linkle mod isn't installed here"))
        lk2 = charsel.Looks(self.catalog(linkle={"how": "link-body"}))
        self.assertEqual(lk2.actor({"id": "linkle"}), (charsel.LINK_ACTOR, ""))
        # never a raw name from the network
        for evil in ({"id": "Dm_Npc_Zelda_Sibyl"}, {"id": "fake"}, {"id": charsel.SLOTS["zelda"]}):
            self.assertEqual(lk.actor(evil)[0], charsel.LINK_ACTOR, evil)

    def test_failed_slot_falls_back(self):
        lk = charsel.Looks(self.catalog())
        self.assertTrue(lk.mark_failed(charsel.SLOTS["zelda"]))
        self.assertFalse(lk.mark_failed(charsel.SLOTS["zelda"]))                 # (once)
        self.assertFalse(lk.mark_failed(charsel.LINK_ACTOR))                     # the fallback is never dropped
        self.assertEqual(lk.actor({"id": "zelda"}), (charsel.LINK_ACTOR, "didn't load in this game"))

    def test_only_what_the_game_really_gets(self):
        """The looks come from the enabled library copy, and only for packs in the deployed overlay."""
        with tempfile.TemporaryDirectory() as d:
            lib = Path(d)
            self.assertEqual(charsel.deployed_catalog(lib), {})                   # (no library)
            mod = lib / "mods" / charsel.MOD_ID
            mod.mkdir(parents=True)
            (mod / "charsel.json").write_text(json.dumps(dict(self.catalog(), mod=charsel.MOD_ID)), encoding="utf-8")
            cfg = {"mods": [{"id": charsel.MOD_ID, "name": "Characters", "enabled": True}]}
            (lib / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
            pack = lib / "deploy" / "overlay" / "Actor" / "Pack" / f"{charsel.SLOTS['zelda']}.sbactorpack"
            pack.parent.mkdir(parents=True)
            pack.write_bytes(b"made up")
            cat = charsel.deployed_catalog(lib)
            self.assertEqual(sorted(cat["built"]), ["zelda"])                    # goron: built, not deployed
            self.assertIn("goron", cat["not_deployed"])
            lk = charsel.Looks(cat)
            self.assertEqual(lk.actor({"id": "zelda"}), (charsel.SLOTS["zelda"], ""))
            self.assertEqual(lk.actor({"id": "goron"}), (charsel.LINK_ACTOR, "not built here"))
            self.assertEqual(charsel.here_catalog(None, lib)[1], str(mod))
            # turned off in the library: nothing (every look is the Link body)
            cfg["mods"][0]["enabled"] = False
            (lib / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
            self.assertEqual(charsel.deployed_catalog(lib), {})
            got, where = charsel.here_catalog(None, lib)
            self.assertEqual(got, {})
            self.assertIn("none in your game", where)
            # a folder you name is taken as it is
            self.assertIn("goron", charsel.here_catalog(mod, lib)[0]["built"])

    def test_catalog_reading(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(charsel.load_catalog(d), {})
            Path(d, "charsel.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(charsel.load_catalog(d), {})
            Path(d, "charsel.json").write_text(json.dumps(self.catalog()), encoding="utf-8")
            self.assertIn("zelda", charsel.load_catalog(d)["built"])
            self.assertIn("zelda", charsel.load_catalog(Path(d, "charsel.json"))["built"])
        self.assertEqual(charsel.load_catalog(None), {})


class Wheel(unittest.TestCase):
    def test_keys(self):
        w = charsel.Wheel("link")
        order = charsel.order()
        self.assertEqual(w.cid, "link")
        w.key("left")
        self.assertEqual(w.cid, order[-1])                                      # round the wheel
        w.key("right")
        w.key("right")
        self.assertEqual(w.cid, order[1])
        w.key("down")
        self.assertEqual(w.cid, "sheikah")                                      # the next group's first
        w.key("down")
        self.assertEqual(w.cid, "bokoblin")
        w.key("down")
        self.assertEqual(w.cid, "link")
        w.key("up")
        self.assertEqual(w.cid, "bokoblin")
        self.assertIsNone(w.key("x"))
        self.assertEqual(w.key("enter"), "bokoblin")
        self.assertEqual(charsel.Wheel("goron").key("esc"), "")
        self.assertEqual(charsel.Wheel("nobody").cid, "link")

    def test_lines_and_slices(self):
        w = charsel.Wheel("goron", built={"goron": {}})
        lines = w.lines()
        self.assertEqual(lines[0], "Who am I?")
        self.assertIn("[RACES]", lines[1])
        self.assertIn("Daruk (Goron)", lines[2])
        self.assertIn("Ready", lines[2])
        self.assertIn("Others see you as: Daruk.", lines[3])
        sl = w.slices()
        self.assertEqual(len(sl), len(charsel.RECIPES))
        self.assertAlmostEqual(sum(s["extent"] for s in sl), 360.0)
        self.assertEqual([s["id"] for s in sl if s["on"]], ["goron"])
        self.assertEqual(charsel.Wheel("zelda", built={}).status(), "Build first")
        self.assertEqual(charsel.Wheel("linkle").status(), "Needs a mod")
        w = charsel.Wheel("linkle")
        w.linkle = True
        self.assertEqual(w.status(), "Ready")
        self.assertEqual(charsel.Wheel("zelda").status(), "")                   # (nothing known about the build)

    def test_pad(self):
        self.assertEqual(charsel.pad_keys(0, charsel.XI_A), ["enter"])
        self.assertEqual(charsel.pad_keys(charsel.XI_A, charsel.XI_A), [])      # held: once
        self.assertEqual(charsel.pad_keys(0, charsel.pad_buttons(0, lx=-30000)), ["left"])
        self.assertEqual(charsel.pad_keys(0, charsel.pad_buttons(0, ly=30000)), ["up"])
        self.assertEqual(charsel.pad_buttons(0, lx=1000, ly=-1000), 0)          # inside the dead zone
        self.assertEqual(charsel.pad_keys(0, charsel.XI_B | charsel.XI_RIGHT), ["right", "esc"])

    def test_window_draws_and_takes_keys(self):
        try:
            import tkinter as tk
            root = tk.Tk()
        except Exception as e:   # (no Tk or no display)
            self.skipTest(f"no Tk: {e}")
        root.withdraw()          # (never on the screen)
        try:
            got = []
            win = charsel.WheelWindow(root, charsel.Wheel("link"), size=300, font=20, on_done=got.append)
            c = win.canvas
            name = lambda: c.itemcget(c.find_withtag("name")[0], "text")   # noqa: E731
            self.assertEqual(name(), "Link")
            self.assertEqual(len(c.find_withtag("slice")), len(charsel.RECIPES))
            win.press("down")
            self.assertEqual(name(), "Paya (Sheikah)")                           # (the race too)
            self.assertIn("Others see you as: Paya", c.itemcget(c.find_withtag("who")[0], "text"))
            self.assertEqual(c.itemcget(c.find_withtag("looks")[0], "text"), charsel.RECIPES["sheikah"].looks)
            for tag in ("label", "cat", "who", "looks", "hint"):                # (big text: never under MIN_PT)
                for item in c.find_withtag(tag):
                    size = int(re.search(r"(?<!\w)(\d+)(?!\w)", str(c.itemcget(item, "font"))).group(1))
                    self.assertGreaterEqual(size, charsel.MIN_PT, tag)
            win._on_key(type("E", (), {"keysym": "Return"}))
            self.assertEqual(got, ["sheikah"])
            lit = [i for i in c.find_withtag("slice") if c.itemcget(i, "fill") == "#ffd43b"]
            self.assertEqual(len(lit), 1)
        finally:
            root.destroy()


class Choice(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = mock.patch.dict(os.environ, {charsel.ENV_HOME: tmp.name})
        p.start()
        self.addCleanup(p.stop)
        self.home = Path(tmp.name)

    def test_round_trip(self):
        self.assertEqual(charsel.state_path(), self.home.resolve() / "charsel.json")
        self.assertEqual(charsel.load_choice(), {"look": "link", "packs": None})
        charsel.state_path().write_text(json.dumps({"other": 1}), encoding="utf-8")
        charsel.save_choice("goron", packs="D:\\packs")
        self.assertEqual(charsel.load_choice(), {"look": "goron", "packs": "D:\\packs"})
        self.assertEqual(json.loads(charsel.state_path().read_text(encoding="utf-8"))["other"], 1)   # kept
        with self.assertRaises(charsel.CharselError):
            charsel.save_choice("daruk")                                       # ids only, never an alias or a name
        charsel.state_path().write_text(json.dumps({"look": "Enemy_Lynel"}), encoding="utf-8")
        self.assertEqual(charsel.load_choice()["look"], "link")
        charsel.state_path().write_text("[1, 2", encoding="utf-8")
        self.assertEqual(charsel.load_choice()["look"], "link")

    def test_cli_pick_and_where(self):
        with mock.patch("builtins.print"):
            self.assertEqual(charsel.main(["pick", "Daruk"]), 0)
            self.assertEqual(charsel.main(["pick", "ganon"]), 1)
            self.assertEqual(charsel.main(["where"]), 0)
        self.assertEqual(charsel.load_choice()["look"], "goron")


ANA = {"name": "Ana", "world": "Ana's world", "players": 2, "max": 8, "code": True, "port": 7788, "ver": "0.7.0",
       "check": "apple river stone moon", "address": "192.168.1.20"}


class LobbyWheel(unittest.TestCase):
    def test_room_code_after_who_am_i(self):
        """Who am I and the room code's word wheels are two things: opening one never breaks the other."""
        from mp import security
        lb = lobby.Lobby("link")
        lb.key("4")
        lb.key("esc")
        lb.key("2")
        lb.set_hosts([ANA])
        lb.key("enter")
        self.assertEqual(lb.screen, "code")
        lb.key("right")
        lb.key("down")
        self.assertEqual(lb.code(), f"{security.WORDS[0]} {security.WORDS[1]} {security.WORDS[0]}")
        self.assertIn(security.WORDS[1].upper(), lb.lines()[1])                 # (the lit word)
        lb.key("enter")
        self.assertEqual(lb.key("enter")["code"], lb.code())

    def test_controller_and_cursor(self):
        """Up/Down and Enter reach every line (a controller has no 1-4); its B never starts Play solo."""
        lb = lobby.Lobby("link")
        self.assertTrue(lb.lines()[1].startswith(" > 1"))
        self.assertIsNone(lb.key("back"))                                       # B on the first screen: nothing
        self.assertEqual(lb.screen, "main")
        lb.key("down")
        lb.key("down")
        lb.key("down")
        self.assertTrue(lb.lines()[4].startswith(" > 4"))
        lb.key("enter")
        self.assertEqual(lb.screen, "who")
        lb.key("right")
        lb.key("back")                                                          # B in the wheel: back, unchanged
        self.assertEqual((lb.screen, lb.look), ("main", "link"))
        lb.key("up")
        lb.key("up")
        self.assertIsNone(lb.key("enter"))                                      # Join a world
        self.assertEqual(lb.screen, "join")
        lb.key("back")
        self.assertEqual(lb.screen, "main")
        lb.key("down")
        self.assertEqual(lb.key("enter"), {"mode": "solo"})                     # Play solo only when picked
        self.assertEqual(lobby.KEYS["BackSpace"], "back")
        for k in ("KP_Enter", "space"):
            self.assertEqual(lobby.KEYS[k], "enter")

    def test_who_am_i_in_the_menu(self):
        lb = lobby.Lobby("link", built={"zelda": {}})
        self.assertIn("Who am I          Link", lb.lines()[4])
        lb.key("4")
        self.assertEqual(lb.screen, "who")
        self.assertEqual(lb.lines()[0], "Who am I?")
        lb.key("right")
        lb.key("right")
        self.assertIsNone(lb.key("enter"))                                      # (no plan: back to the menu)
        self.assertEqual((lb.look, lb.screen), ("zelda", "main"))
        self.assertIn("Who am I          Zelda", lb.lines()[4])
        lb.key("4")
        lb.key("down")
        lb.key("esc")                                                           # Esc: unchanged
        self.assertEqual((lb.look, lb.screen), ("zelda", "main"))
        self.assertEqual(lb.key("1"), {"mode": "host"})                         # the plan is as before

    def test_window_swaps_in_the_drawn_wheel(self):
        try:
            import tkinter as tk
            root = tk.Tk()
        except Exception as e:   # (no Tk or no display)
            self.skipTest(f"no Tk: {e}")
        root.withdraw()          # (never on the screen)
        try:
            plans = []
            win = lobby.LobbyWindow(root, lobby.Lobby("link"), on_plan=plans.append)
            self.assertIn("Who am I", win.label.cget("text"))
            win.press("4")
            self.assertIsNotNone(win.win)                                       # the wheel, drawn
            c = win.win.canvas
            win.press("right")
            self.assertEqual(c.itemcget(c.find_withtag("name")[0], "text"), "Linkle")
            win.press("enter")
            self.assertIsNone(win.win)                                          # back to the text menu
            self.assertIn("Who am I          Linkle", win.label.cget("text"))
            win._on_key(type("E", (), {"keysym": "3"}))
            self.assertEqual(plans, [{"mode": "solo"}])
        finally:
            root.destroy()


class IamList(unittest.TestCase):
    def test_client_list_fits_iam(self):
        from mp import client
        text = client.iam_list()
        self.assertLess(len(text), 900)
        entries = text.split(",")
        self.assertEqual([e.split("/")[0] for e in entries], charsel.order())
        for e in entries:
            for w in e.split("/"):
                self.assertTrue(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,23}", w), w)   # (iam.c's iam_word_ok)
        self.assertLessEqual(len(entries), 48)                                     # (IAM_MAX)
        self.assertTrue(all(len(e) < 96 for e in entries))                         # (IAM_ENTRY)


if __name__ == "__main__":
    unittest.main()
