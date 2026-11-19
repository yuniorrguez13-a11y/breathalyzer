"""Dragonbreath stage 3 (tools/dragonbreath_clips.py) and the key-language parts it added to tools/emote_build.py
(blade, spin, a settable turn limit, the shared archive writer and list log): made-up data only, no game files."""
import copy
import importlib.util
import math
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

import aamp  # noqa: E402
import bnp  # noqa: E402
import dragonbreath_clips as dc  # noqa: E402
import emote_build as eb  # noqa: E402

H = aamp.name_hash


def ang(u, v):
    return math.degrees(math.acos(max(-1.0, min(1.0, eb.dot(eb.unit(u), eb.unit(v))))))


def cut_clip(**over):
    """A made-up one-hit clip: a flat cut from Link's right across the front to his left."""
    c = {"id": "cut", "slot": "CutS1", "part": 1, "name": "Test cut", "by": "test", "about": "a flat cut",
         "frames": 10, "next": 5, "max_turn": 85,
         "hits": [{"start": 2, "end": 4, "side": "Left", "at": [0, 1.0, 0.85]}],
         "poses": {"stand": {"hips": [0, -0.08, 0], "hand": {"R": "grip"},
                             "reach": {"Wrist_R": {"on": "Spine_2", "at": [-0.25, -0.1, 0.25]}},
                             "blade": {"Wrist_R": [-0.3, 0.5, 0.8]}}},
         "keys": [{"f": 0, "pose": "stand"},
                  {"f": 2, "pose": "stand", "ease": "linear", "reach": {"Wrist_R": {"on": "Spine_2", "at": [-0.2, -0.1, 0.32]}},
                   "blade": {"Wrist_R": [-0.6, 0.05, 0.8]}},
                  {"f": 3, "pose": "stand", "ease": "linear", "reach": {"Wrist_R": {"on": "Spine_2", "at": [0.0, -0.06, 0.34]}},
                   "blade": {"Wrist_R": [0.0, 0.15, 1.0]}},
                  {"f": 4, "pose": "stand", "ease": "linear", "reach": {"Wrist_R": {"on": "Spine_2", "at": [0.2, -0.02, 0.3]}},
                   "blade": {"Wrist_R": [0.6, 0.25, 0.76]}},
                  {"f": 10, "pose": "stand"}]}
    c.update(over)
    return c


class KeyLanguage(unittest.TestCase):
    """The emote solver's new fields, on the made-up skeleton with a weapon bone."""

    @classmethod
    def setUpClass(cls):
        cls.rig = dc.demo_rig()

    def solve(self, fields):
        st = eb.State()
        eb.apply_fields(st, fields, self.rig.skel, "test")
        rots, trans = self.rig.solver.solve(st)
        return eb.fk(self.rig.skel, rots, trans), rots

    def test_blade_points_where_asked(self):
        for d in ((0, 0, 1), (-0.6, 0.1, 0.8), (0.5, 0.7, 0.3), (0, -0.6, 0.8)):
            (Wr, Wp), _ = self.solve({"reach": {"Wrist_R": [-0.2, 1.0, 0.3]}, "blade": {"Wrist_R": list(d)}})
            g, t, _e = eb.weapon_line(self.rig.skel, Wr, Wp, "Wrist_R", 1.0)
            self.assertLess(ang(eb.vsub(t, g), d), 0.5, d)

    def test_blade_edge(self):
        (Wr, Wp), _ = self.solve({"reach": {"Wrist_R": [-0.2, 1.0, 0.3]},
                                  "blade": {"Wrist_R": {"dir": [0, 0, 1], "edge": [0, -1, 0]}}})
        g, t, e = eb.weapon_line(self.rig.skel, Wr, Wp, "Wrist_R", 1.0)
        self.assertLess(ang(eb.vsub(t, g), (0, 0, 1)), 0.5)
        self.assertLess(ang(e, (0, -1, 0)), 0.5)

    def test_blade_without_edge_keeps_the_wrist_near_the_forearm(self):
        (Wr, Wp), _ = self.solve({"reach": {"Wrist_R": [-0.2, 1.0, 0.35]}, "blade": {"Wrist_R": [-0.3, 0.6, 0.75]}})
        sk = self.rig.skel
        P = lambda n: Wp[sk.idx[n]]  # noqa: E731
        bend = ang(eb.vsub(P("Wrist_R"), P("Arm_2_R")), eb.vsub(P("Finger_C_1_R"), P("Wrist_R")))
        self.assertLess(bend, 80)

    def test_blade_bad_values(self):
        for bad in ({"Wrist_R": [0, 0, 0]}, {"Ankle_R": [0, 0, 1]}, {"Wrist_R": {"dir": [0, 0, 1], "edge": [0, 0, 2]}},
                    {"Wrist_R": {"edge": [1, 0, 0]}}):
            with self.assertRaises(eb.EmoteError):
                eb.apply_fields(eb.State(), {"blade": bad}, None, "t")

    def test_blade_mirrors(self):
        st = eb.State()
        eb.apply_fields(st, {"blade": {"Wrist_R": [-0.5, 0.2, 0.8]}}, None, "t")
        m = eb.mirror_state(st)
        self.assertIn("Wrist_L", m.blade)
        self.assertAlmostEqual(m.blade["Wrist_L"][0][0], -st.blade["Wrist_R"][0][0])

    def test_fixed_grip_is_kept(self):
        _, rots = self.solve({"reach": {"Wrist_R": [-0.2, 1.0, 0.3]}})
        i = self.rig.skel.idx["Weapon_R"]
        self.assertLess(eb.angle_between_m(rots[i], self.rig.fixed["Weapon_R"][0]), 1e-3)

    def test_spin_turns_the_solved_pose_as_one_piece(self):
        base = {"reach": {"Wrist_R": {"on": "Spine_2", "at": [-0.3, 0.0, 0.2]}}, "feet": "free"}
        (Wr0, Wp0), rots0 = self.solve(base)
        (Wr1, Wp1), rots1 = self.solve({**base, "spin": [0, 90, 0]})
        sk = self.rig.skel
        hip = Wp0[sk.idx["Skl_Root"]]
        R = eb.deg_m((0, 90, 0))
        for n in ("Wrist_R", "Head", "Ankle_L"):
            want = eb.vadd(hip, eb.mvec(R, eb.vsub(Wp0[sk.idx[n]], hip)))
            self.assertLess(eb.vlen(eb.vsub(want, Wp1[sk.idx[n]])), 1e-3, n)
        # a turn about the vertical goes on the hip's children (no 90-degree middle Euler angle on the hip bone)
        r = sk.idx["Skl_Root"]
        self.assertLess(eb.angle_between_m(rots0[r], rots1[r]), 1e-6)
        # a flip goes on the hip bone
        _, rots2 = self.solve({**base, "spin": [90, 0, 0]})
        self.assertGreater(eb.angle_between_m(rots0[r], rots2[r]), 89)

    def test_spin_limits(self):
        with self.assertRaises(eb.EmoteError):
            eb.apply_fields(eb.State(), {"spin": [0, 200, 0]}, None, "t")
        st = eb.State()
        eb.apply_fields(st, {"spin": [0, 120, 30]}, None, "t")
        self.assertEqual(eb.mirror_state(st).spin, (0.0, -120.0, -30.0))

    def test_swing_is_steady_overhead(self):
        a = eb.swing((0, -1, 0), (0.001, 1, 0.0))
        b = eb.swing((0, -1, 0), (-0.001, 1, 0.0))
        self.assertLess(eb.angle_between_m(a, b), 2.0)
        self.assertLess(ang(eb.mvec(a, (0, -1, 0)), (0.001, 1, 0)), 0.1)
        small = eb.swing((0, -1, 0), (0, -0.9, 0.3))
        self.assertLess(eb.angle_between_m(small, eb.rot_between((0, -1, 0), (0, -0.9, 0.3))), 1e-9)

    def test_max_turn(self):
        d = dict(cut_clip())
        spec = dc.emote_spec(d)
        self.assertEqual(eb.lint_one(spec), [])
        spec["max_turn"] = 200
        self.assertTrue(any("max_turn" in p for p in eb.lint_one(spec)))
        spec["max_turn"] = 10
        self.assertTrue(any("max_turn" in p for p in eb.lint_one(spec)))

    def test_list_log_archive_only(self):
        pio = eb.anim_list_log("SwTest_Animation")
        back = bnp.aamp_read(bnp.aamp_write(pio))
        body = next(iter(back.root.lists.values()))
        anims, names = eb.list_names(body)
        self.assertEqual((anims, names), (["SwTest_Animation"], []))
        anims, names = eb.list_names(next(iter(eb.aslist_log([{"id": "wave"}]).root.lists.values())))
        self.assertEqual((anims, names), ([eb.ARCHIVE], ["ZEmote_wave"]))


class Clips(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.rig = dc.demo_rig()

    def test_lint_good(self):
        self.assertEqual(dc.lint([cut_clip()]), [])

    def test_lint_problems(self):
        cases = [
            (cut_clip(hits=[{"start": 2, "end": 3, "side": "Left", "at": [0, 1, 1]}]), "at least 3"),
            (cut_clip(next=3), "next"),
            (cut_clip(slot="CutNope"), "slot"),
            (cut_clip(hits=[]), "exactly one"),
            (cut_clip(hits=[{"start": 2, "end": 4, "side": "Sideways", "at": [0, 1, 1]}]), "side"),
            (cut_clip(id="Bad Id"), "id"),
            (cut_clip(extra=1), "unknown field"),
        ]
        for c, word in cases:
            self.assertTrue(any(word in p for p in dc.lint([c])), (word, dc.lint([c])))
        two = [cut_clip(id="a", slot="CutS3", part=1), cut_clip(id="b", slot="CutS3", part=3)]
        self.assertTrue(any("parts" in p for p in dc.lint(two)))

    def test_lint_next_on_the_last_part_only(self):
        first = cut_clip(id="a", slot="CutS3", part=1)
        del first["next"]                                # (part 1 plays to its end: its next isn't used)
        self.assertEqual(dc.lint([first, cut_clip(id="b", slot="CutS3", part=2)]), [])
        self.assertTrue(any("b: next" in p for p in dc.lint([first, cut_clip(id="b", slot="CutS3", part=2, next=3)])))

    def test_check_a_good_cut(self):
        c = cut_clip()
        rep = dc.check_clip(self.rig, c, dc.solve(self.rig, c))
        self.assertEqual(rep.problems, [])
        self.assertLess(rep.hit_cm, dc.HIT_CM)
        self.assertLess(rep.tip_deg, dc.TIP_DEG)

    def test_check_finds_a_miss(self):
        c = cut_clip(hits=[{"start": 2, "end": 4, "side": "Left", "at": [0, 0.3, 2.5]}])
        rep = dc.check_clip(self.rig, c, dc.solve(self.rig, c))
        self.assertTrue(any("from its hit point" in p for p in rep.problems), rep.problems)

    def test_check_finds_a_foot_in_the_floor(self):
        c = cut_clip()
        c["keys"] = copy.deepcopy(c["keys"])
        c["keys"][2]["step"] = {"Ankle_L": [0, -0.08, 0]}
        rep = dc.check_clip(self.rig, c, dc.solve(self.rig, c))
        self.assertTrue(any("below the floor" in p for p in rep.problems), rep.problems)

    def test_check_finds_a_too_fast_window(self):
        c = cut_clip()
        c["keys"] = [k for k in copy.deepcopy(c["keys"]) if k["f"] != 3]
        c["keys"][2]["blade"] = {"Wrist_R": [0.95, 0.1, -0.3]}
        rep = dc.check_clip(self.rig, c, dc.solve(self.rig, c))
        self.assertTrue(any("in one frame" in p for p in rep.problems), rep.problems)

    def test_combo_speed(self):
        clips = [cut_clip(id="a", slot="CutS1", next=5), cut_clip(id="b", slot="CutS2", next=5),
                 cut_clip(id="c", slot="CutS3", part=1, frames=6, next=6),
                 cut_clip(id="d", slot="CutS3", part=2, next=6), cut_clip(id="e", slot="CutSF", next=10)]
        sp = dc.combo_speed(clips)
        self.assertEqual((sp["frames"], sp["hits"]), (5 + 5 + 6 + 6 + 10, 5))
        self.assertAlmostEqual(sp["hits_per_second_x"], (5 / 32) / (4 / 74.0))

    def test_frame_map(self):
        f = dc.frame_map([(0, 0), (0, 0), (21, 14), (0, 2), (4, 4), (17, 5)])
        self.assertEqual(f(0), 0)
        self.assertAlmostEqual(f(4), 4)
        self.assertAlmostEqual(f(17), 5)
        self.assertAlmostEqual(f(21), 14)
        self.assertAlmostEqual(f(19), 9.5)

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow isn't installed (the pictures are optional)")
    def test_picture(self):
        c = cut_clip()
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "cut.png")
            dc.render_clip(self.rig, c, dc.solve(self.rig, c), p)
            with open(p, "rb") as fh:
                self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")

    def test_our_clip_files(self):
        clips = dc.load_clips()
        self.assertEqual(dc.lint(clips), [])
        self.assertGreaterEqual(len(clips), 12)
        self.assertEqual({c["slot"] for c in clips}, set(dc.SLOTS))
        self.assertGreater(dc.combo_speed(clips)["hits_per_second_x"], 2.5)
        for c in clips:   # every one solves on the made-up skeleton, its blade inside the window near its point
            rep = dc.check_clip(self.rig, c, dc.solve(self.rig, c))
            self.assertLess(rep.hit_cm, 30.0, c["id"])


def fake_set(names=("Sword_Attack_S1",), seq=False) -> bytes:
    """A made-up attack set: a weapon-type selector over the given clip nodes (in a sequence node when seq)."""
    def clip(name):
        e = aamp.PList()
        p = aamp.PObject()
        p.set("TypeIndex", aamp.INT, 67)
        p.set("FileName", aamp.STRING64, name)
        p.set("Morph", aamp.F32, 4.0)
        e.objects[H("Parameters")] = p
        trig = aamp.PList()
        for i, (t, fr, v) in enumerate(((0, -2.0, "AttackSmall"), (7, 0.0, "FaceCut"), (4, 17.0, ""))):
            o = aamp.PObject()
            o.set("TypeIndex", aamp.INT, t)
            o.set("Frame", aamp.F32, fr)
            o.set("Value", aamp.STRING_REF, v)
            trig.objects[H(f"Event{i}")] = o
        hold = aamp.PList()
        for i, (t, s, en, v) in enumerate(((3, 0.0, 4.0, "Left"), (2, 18.0, -1.0, ""), (48, 0.0, 5.0, ""))):
            o = aamp.PObject()
            o.set("TypeIndex", aamp.INT, t)
            o.set("StartFrame", aamp.F32, s)
            o.set("EndFrame", aamp.F32, en)
            o.set("Value", aamp.STRING32, v)
            hold.objects[H(f"Event{i}")] = o
        fc = aamp.PList(objects={H("FrameCtrl0"): aamp.PObject({H("Rate"): aamp.Param(aamp.F32, 1.15)})})
        e.lists[H("Extend")] = aamp.PList(lists={H("TriggerEvents"): trig, H("HoldEvents"): hold, H("FrameCtrl"): fc})
        return e
    els = []
    root = aamp.PList()
    rp = aamp.PObject()
    rp.set("TypeIndex", aamp.INT, 98)
    root.objects[H("Parameters")] = rp
    els.append(root)
    kids = list(range(2, 2 + len(names))) if seq else list(range(1, 1 + len(names)))
    if seq:
        s = aamp.PList()
        sp = aamp.PObject()
        sp.set("TypeIndex", aamp.INT, 11)
        s.objects[H("Parameters")] = sp
        s.objects[H("Children")] = aamp.PObject({H(f"Child{i}"): aamp.Param(aamp.INT, k)
                                                 for i, k in enumerate(kids + [kids[-1]])})
        s.lists[H("Extend")] = aamp.PList(lists={H("IntArray"): aamp.PList(objects={H("IntArray0"): aamp.PObject(
            {H(f"Value{i}"): aamp.Param(aamp.INT, i + 1) for i in range(len(kids) + 1)})})})
        els.append(s)
        root.objects[H("Children")] = aamp.PObject({H("Child0"): aamp.Param(aamp.INT, 1)})
    else:
        root.objects[H("Children")] = aamp.PObject({H(f"Child{i}"): aamp.Param(aamp.INT, k)
                                                    for i, k in enumerate(kids)})
    els += [clip(n) for n in names]
    top = aamp.PList(lists={H("Elements"): aamp.PList(lists={H(f"Element{i}"): e for i, e in enumerate(els)})})
    return aamp.dump(top)


class Sets(unittest.TestCase):

    def test_wrap_one_clip(self):
        src = fake_set()
        out = dc.wrap_bas(src, "CutS1", [cut_clip()], {"Sword_Attack_S1": 21.0})
        self.assertTrue(dc.untouched(src, out))
        t = dc.tree(out)
        self.assertTrue(t[0].startswith("#0 T97 Dragonbreath,default"), t)
        self.assertIn("T67 SwDragon_cut hit=[2,4]", "\n".join(t))
        self.assertIn("T98", "\n".join(t))          # the game's tree is the default branch
        els = list(aamp.load(out).list("Elements").lists.values())
        ours = els[-1]
        self.assertEqual(ours.object("Parameters").get_value("FileName"), "SwDragon_cut")
        self.assertEqual(ours.object("Parameters").get_value("Morph"), dc.MORPH)
        trig = {o.get_value("TypeIndex"): o.get_value("Frame") for o in dc._events(ours, "TriggerEvents")}
        self.assertEqual(trig[4], 5.0)               # next input on our frame
        self.assertEqual(trig[0], -2.0)              # "before the start" stays
        hold = {o.get_value("TypeIndex"): (o.get_value("StartFrame"), o.get_value("EndFrame"))
                for o in dc._events(ours, "HoldEvents")}
        self.assertEqual(hold[3], (2.0, 4.0))
        self.assertEqual(hold[2][1], -1.0)
        self.assertTrue(5.0 <= hold[2][0] <= 10.0)
        rate = next(iter(ours.list("Extend").list("FrameCtrl").objects.values())).get_value("Rate")
        self.assertAlmostEqual(rate, 1.0)
        self.assertEqual(aamp.dump(aamp.load(out)), out)

    def test_wrap_parts_in_a_sequence(self):
        names = [f"Sword_Attack_Just_Rush{i}" for i in (1, 2)]
        src = fake_set(names, seq=True)
        parts = [cut_clip(id="r1", slot="CutJustRush", part=1), cut_clip(id="r2", slot="CutJustRush", part=2)]
        out = dc.wrap_bas(src, "CutJustRush", parts, {})
        self.assertTrue(dc.untouched(src, out))
        els = list(aamp.load(out).list("Elements").lists.values())
        seq = els[len(els) - 3]
        self.assertEqual(seq.object("Parameters").get_value("TypeIndex"), 11)
        kids = [p.value for p in seq.object("Children").values()]
        self.assertEqual(kids, [len(els) - 2, len(els) - 1, len(els) - 1])   # the game's count: the last repeated
        self.assertEqual([e.object("Parameters").get_value("FileName") for e in els[-2:]],
                         ["SwDragon_r1", "SwDragon_r2"])
        # only the last part keeps the next-attack trigger (TypeIndex 4): a buffered press can't skip part 2
        trig = [[o.get_value("TypeIndex") for o in dc._events(e, "TriggerEvents")] for e in els[-2:]]
        self.assertEqual(trig, [[0, 7], [0, 7, 4]])
        self.assertEqual(aamp.dump(aamp.load(out)), out)

    def test_cut_s3_part_1_has_no_next_trigger(self):
        src = fake_set(("Sword_Attack_S3",))
        parts = [cut_clip(id="s3b", slot="CutS3", part=2), cut_clip(id="s3a", slot="CutS3", part=1)]
        out = dc.wrap_bas(src, "CutS3", parts, {}, seq_template=aamp.load(fake_set(
            ["Sword_Attack_Just_Rush1"], seq=True)).list("Elements").lists[H("Element1")])
        els = list(aamp.load(out).list("Elements").lists.values())
        self.assertEqual([e.object("Parameters").get_value("FileName") for e in els[-2:]],
                         ["SwDragon_s3a", "SwDragon_s3b"])            # (in part order, whatever the list's order)
        has4 = [any(o.get_value("TypeIndex") == 4 for o in dc._events(e, "TriggerEvents")) for e in els[-2:]]
        self.assertEqual(has4, [False, True])

    def test_flurry_rush_sequence_found_by_its_child(self):
        els = list(aamp.load(fake_set(["Sword_Attack_Just_Rush1", "Sword_Attack_Just_Rush2"], seq=True))
                   .list("Elements").lists.values())
        decoy = copy.deepcopy(els[1])                     # another sequence, first in the set, over another clip
        decoy.objects[H("Children")] = aamp.PObject({H("Child0"): aamp.Param(aamp.INT, len(els) + 1)})
        other = copy.deepcopy(els[2])
        other.object("Parameters")[H("FileName")] = aamp.Param(aamp.STRING64, "Sword_Attack_Other")
        got = dc._parent_sequence([els[0], decoy] + els[1:] + [other], "Sword_Attack_Just_Rush1")
        self.assertIs(got, els[1])
        self.assertIsNone(dc._parent_sequence([els[0]] + els[2:], "Sword_Attack_Just_Rush1"))
        # as in the game's 0.4.1 file: the clip in a speed selector (T16) under the sequence, other sequences first
        speed = aamp.PList()
        speed.objects[H("Parameters")] = aamp.PObject({H("TypeIndex"): aamp.Param(aamp.INT, 16)})
        speed.objects[H("Children")] = aamp.PObject({H("Child0"): aamp.Param(aamp.INT, 4)})
        seq = copy.deepcopy(els[1])
        seq.objects[H("Children")] = aamp.PObject({H("Child0"): aamp.Param(aamp.INT, 3)})
        rush1 = els[2]
        lst = [els[0], decoy, seq, speed, rush1, other]
        decoy.objects[H("Children")] = aamp.PObject({H("Child0"): aamp.Param(aamp.INT, 5)})
        self.assertIs(dc._parent_sequence(lst, "Sword_Attack_Just_Rush1"), seq)

    def test_wrap_needs_the_game_node(self):
        with self.assertRaises(dc.ClipError):
            dc.wrap_bas(fake_set(("Something_Else",)), "CutS1", [cut_clip()], {})


if __name__ == "__main__":
    unittest.main()
