"""The emotes (tools/emote_build.py, tools/emotes/*.json, native/examples/emotes).

Without game files (a made-up skeleton with Link's bone names, in memory):
- math: Euler XYZ round trips, continuity across +/-180 degrees, slerp, the smallest rotation between two directions;
- reach: the two-bone solve puts the wrist within 1 mm and bends the elbow toward the hint;
- curves: the fit stays within 0.002 rad, the end and delta rules, the curve head bytes, the baked size formula;
- flags: "rotation zero" is cleared on a bone that turns, a still bone gets base data only;
- loops close (the last frame is the first), mirror swaps left and right with the right signs, hand presets curl;
- eases (spline keeps moving through keys, hit overshoots), heel (the ball of the foot stays down), lag, breath, the
  rough-body clipping check, the contact sheet (JPEG); the robot holds 4 to 8 frames, the dances never stand still;
- data: every keyframe file lints clean; 32 wheel slots (4 pages) + ragdoll; unique ids and aliases; the header and mod.toml's
  generated block are what `header` writes;
- the animation list log: sorted, every name ZEmote_* after the game's last set; merged after a made-up game list and
  a Linkle-like log, it has 4 archives and a binary search finds every name;
- .bas: reads back (TypeIndex 67, FileName, Morph, the face event); a preview PNG is written.

With the user's files (skipped when they are missing): a full build to a temp folder (every pose check, read back
within 0.15 degrees; the game clips copied into our archive unchanged, daruk_flex's a real clip) and the merge with
The Linkle Mod's real log.

Native: native/examples/emotes/selftest/emotes_selftest.c built with Savage Wind's zig (skipped without it); it also
checks that sets go on Link through his own body play (the upper body follows) and the `emotedbg upper` control.
"""
import ctypes
import math
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import aamp  # noqa: E402
import bnp  # noqa: E402
import emote_build as eb  # noqa: E402

LINKLE_LOG = REPO / "out" / "emotes-lib" / "mods" / "linkle" / ".bnp" / "logs" / "aslist.aamp"


def demo():
    sk = eb.demo_skeleton()
    return sk, eb.Solver(sk, eb.demo_idle(sk))


class MathTests(unittest.TestCase):
    def test_euler_round_trip(self):
        for e in ((0.1, -0.4, 2.0), (-1.2, 0.3, -2.9), (2.5, 1.2, 0.4), (0.0, 0.0, 0.0)):
            m = eb.e2m(e)
            back = eb.m2e(m)
            m2 = eb.e2m(back)
            self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(m, m2)), (e, back))

    def test_euler_continuity(self):
        prev = None
        seq = []
        for i in range(0, 361, 10):          # z sweeps across +/-180: no 2-pi jump
            prev = eb.m2e(eb.e2m((0.0, 0.0, math.radians(170 + i * 0.1))), prev)
            seq.append(prev[2])
        self.assertTrue(all(abs(b - a) < 0.1 for a, b in zip(seq, seq[1:])), seq)

    def test_slerp(self):
        q0, q1 = eb.m2q(eb.I3), eb.m2q(eb.axis_angle((0, 1, 0), math.radians(90)))
        mid = eb.q2m(eb.slerp(q0, q1, 0.5))
        self.assertAlmostEqual(eb.angle_between_m(eb.I3, mid), 45.0, places=6)
        self.assertAlmostEqual(eb.angle_between_m(eb.q2m(eb.slerp(q0, q1, 1.0)), eb.q2m(q1)), 0.0, places=5)

    def test_rot_between(self):
        for u, v in (((1, 0, 0), (0, 1, 0)), ((0, 0, 1), (0, 0, -1)), ((0.3, -0.2, 0.9), (-0.5, 0.5, 0.1))):
            r = eb.rot_between(u, v)
            got = eb.mvec(r, eb.unit(u))
            self.assertTrue(all(abs(a - b) < 1e-9 for a, b in zip(got, eb.unit(v))))


def hard_switch_swing(u, v):
    """swing() as it was before the blend (a hard switch to the via-front path at 150 degrees), to compare with."""
    u, v = eb.unit(u), eb.unit(v)
    if eb.dot(u, v) > -0.866:
        return eb.rot_between(u, v)
    mid = eb.vsub((0.0, 0.0, 1.0), eb.vscale(u, u[2]))
    if eb.vlen(mid) < 1e-3:
        return eb.rot_between(u, v)
    mid = eb.unit(mid)
    return eb.mmul(eb.rot_between(mid, v), eb.rot_between(u, mid))


class SwingTests(unittest.TestCase):
    """swing(): a big turn blends smoothly from the direct path to the one by way of Link's front."""

    @staticmethod
    def target(theta, azimuth):
        """theta degrees from hanging down (0, -1, 0), in the plane `azimuth` degrees from the front toward the side."""
        t, a = math.radians(theta), math.radians(azimuth)
        return (math.sin(t) * math.sin(a), -math.cos(t), math.sin(t) * math.cos(a))

    def test_no_twist_jump_for_side_overhead_targets(self):
        down = (0.0, -1.0, 0.0)
        for az in (0, 30, 45, 60, 90):
            prev, worst = None, 0.0
            for k in range(361):                                         # 90 to 179.75 degrees, 0.25 apart
                v = self.target(90 + k * 0.25, az)
                r = eb.swing(down, v)
                self.assertLess(max(abs(a - b) for a, b in zip(eb.mvec(r, down), eb.unit(v))), 1e-6)   # aims right
                if prev is not None:
                    worst = max(worst, eb.angle_between_m(prev, r))
                prev = r
            self.assertLess(worst, 2.5, az)                    # (the hard switch jumped up to 150 degrees here)
            across = eb.angle_between_m(eb.swing(down, self.target(149.9, az)), eb.swing(down, self.target(150.1, az)))
            old = eb.angle_between_m(hard_switch_swing(down, self.target(149.9, az)),
                                     hard_switch_swing(down, self.target(150.1, az)))
            self.assertLess(across, 1.0, az)
            if az >= 45:
                self.assertGreater(old, 60.0, az)              # (what the test would have caught)

    def test_small_turns_and_the_ends_are_as_before(self):
        down = (0.0, -1.0, 0.0)
        for theta in (10, 90, 130, 140, 179.9):
            for az in (0, 45, 90):
                v = self.target(theta, az)
                self.assertLess(eb.angle_between_m(eb.swing(down, v), hard_switch_swing(down, v)),
                                0.2 if theta >= 179 else 1e-5, (theta, az))

    def test_existing_emotes_solve_as_before(self):
        sk = eb.demo_skeleton()
        keyed = [d for d in eb.load_emotes() if d["kind"] == "keys"]
        self.assertGreaterEqual(len(keyed), 20)

        def solve_all():
            solver = eb.Solver(sk, eb.demo_idle(sk))
            return [eb.sample(solver, d) for d in keyed]
        now = solve_all()
        with mock.patch.object(eb, "swing", hard_switch_swing):
            before = solve_all()
        worst = 0.0
        for a, b in zip(now, before):
            for (ra, ta), (rb, tb) in zip(a.frames, b.frames):
                for x, y in zip(ra, rb):
                    worst = max(worst, eb.angle_between_m(x, y) if len(x) == 9 else
                                math.degrees(2 * math.acos(min(1.0, abs(sum(p * q for p, q in zip(x, y)))))))
        self.assertLess(worst, 0.01)


class SolveTests(unittest.TestCase):
    def setUp(self):
        self.sk, self.solver = demo()

    def wp(self, st):
        rots, trans = self.solver.solve(st)
        return eb.fk(self.sk, rots, trans)

    def test_reach_lands_within_a_millimetre(self):
        target = (-0.25, 1.2, 0.3)
        st = eb.State(reach={"Wrist_R": target})
        _, Wp = self.wp(st)
        self.assertLess(eb.vlen(eb.vsub(Wp[self.sk.idx["Wrist_R"]], target)), 0.001)

    def test_elbow_bends_toward_the_hint(self):
        target = (-0.2, 1.2, 0.25)
        for hint in ((-1, 0, 0), (0, -1, 0)):
            _, Wp = self.wp(eb.State(reach={"Wrist_R": target}, hint={"Wrist_R": hint}))
            S, E = Wp[self.sk.idx["Arm_1_R"]], Wp[self.sk.idx["Arm_2_R"]]
            mid = eb.vscale(eb.vadd(S, target), 0.5)
            self.assertGreater(eb.dot(eb.vsub(E, mid), hint), 0.02, hint)

    def test_reach_on_a_bone_follows_it(self):
        st = eb.State(reach={"Wrist_R": {"on": "Head", "at": (0.0, 0.0, 0.2)}}, turn={"Head": (25.0, 0.0, 0.0)})
        Wr, Wp = self.wp(st)
        h = self.sk.idx["Head"]
        want = eb.vadd(Wp[h], eb.mvec(Wr[h], eb.mvec(eb.mT(self.sk.rest_W[h]), (0.0, 0.0, 0.2))))
        self.assertLess(eb.vlen(eb.vsub(Wp[self.sk.idx["Wrist_R"]], want)), 0.001)

    def test_planted_feet_stay(self):
        _, Wp0 = self.wp(eb.State())
        _, Wp = self.wp(eb.State(hips=(0.05, -0.06, 0.08)))
        for a in ("Ankle_L", "Ankle_R"):
            self.assertLess(eb.vlen(eb.vsub(Wp[self.sk.idx[a]], Wp0[self.sk.idx[a]])), 0.002)

    def test_aim_points_the_bone(self):
        _, Wp = self.wp(eb.State(aim={"Arm_1_R": (0, 1, 0), "Arm_2_R": (0, 1, 0)}))
        d = eb.unit(eb.vsub(Wp[self.sk.idx["Wrist_R"]], Wp[self.sk.idx["Arm_1_R"]]))
        self.assertGreater(d[1], 0.999)

    def test_hand_presets_curl(self):
        def spread(preset, f="C"):
            _, Wp = self.wp(eb.State(hand={"R": preset}))
            return eb.vlen(eb.vsub(Wp[self.sk.idx[f"Finger_{f}_3_R"]], Wp[self.sk.idx[f"Finger_{f}_1_R"]]))
        self.assertLess(spread("fist"), spread("open") * 0.8)
        self.assertAlmostEqual(spread("point", "B"), spread("open", "B"), places=6)   # the index finger stays out
        self.assertLess(spread("point", "C"), spread("open", "C") * 0.8)

    def test_mirror(self):
        st = eb.State(aim={"Arm_1_R": (-0.5, 0.2, 0.3)}, turn={"Head": (5, 10, 15)}, hips=(0.04, 0, 0),
                      hand={"R": "fist"}, reach={"Wrist_L": {"on": "Spine_2", "at": (0.1, 0.2, 0.3)}})
        m = eb.mirror_state(st)
        self.assertEqual(m.aim, {"Arm_1_L": (0.5, 0.2, 0.3)})
        self.assertEqual(m.turn, {"Head": (5, -10, -15)})
        self.assertEqual(m.hips, (-0.04, 0, 0))
        self.assertEqual(m.hand, {"L": "fist"})
        self.assertEqual(m.reach, {"Wrist_R": {"on": "Spine_2", "at": (-0.1, 0.2, 0.3)}})


class CurveTests(unittest.TestCase):
    def test_fit_within_tolerance(self):
        vals = [0.8 * math.sin(i / 7.0) + 0.1 * math.cos(i / 3.0) for i in range(61)]
        c = eb.make_curve(0x20, vals)
        self.assertLess(len(c.frames), 61)
        self.assertEqual(c.frames[0], 0.0)
        self.assertEqual(c.frames[-1], 60.0)
        for x in range(61):
            self.assertLessEqual(abs(eb.rows_eval(c.frames, c.rows, float(x)) - vals[x]), eb.TOL_RAD + 1e-9)
        self.assertAlmostEqual(c.delta, vals[-1] - vals[0])
        self.assertEqual(c.rows[-1][1:], (0.0, 0.0, 0.0))

    def test_head_bytes(self):
        c = eb.make_curve(0x24, [0.0, 0.5, 1.0, 0.5, 0.0])
        f, k, tg, s, e, sc, off, d = struct.unpack_from("<HHIfffff", c.head())
        self.assertEqual((f, k, tg, s, e, sc, off), (2, len(c.frames), 0x24, 0.0, 4.0, 1.0, 0.0))
        self.assertEqual(len(c.head()), 0x20)
        fr, rows = eb.read_curve(c.head(), c.frame_bytes(), c.key_bytes())
        self.assertEqual(fr, c.frames)
        self.assertAlmostEqual(eb.rows_eval(fr, rows, 2.0), 1.0, places=5)

    def test_baked_size(self):
        cs = [eb.make_curve(0x20, [0.0, 1.0] * 5 + [0.0]), eb.make_curve(0x24, [0.0, 0.1, 0.2])]
        want = sum(((4 * (math.ceil(c.frames[-1] - c.frames[0]) + 8) + 7) // 8) * 8 for c in cs)
        self.assertEqual(eb.baked_size(cs), want)


class ClipTests(unittest.TestCase):
    def setUp(self):
        self.sk, self.solver = demo()
        self.emotes = {d["id"]: d for d in eb.load_emotes()}

    def test_flags(self):
        clip = eb.sample(self.solver, self.emotes["wave"])
        cd = eb.build_clip(self.sk, eb.plain_template(self.sk), clip, "SwEmote_wave")
        tr = {t.name: t for t in cd.tracks}
        self.assertTrue(tr["Arm_2_R"].curves)
        self.assertFalse(tr["Arm_2_R"].flags & eb.FLAG_ROT_ZERO)
        self.assertTrue(tr["Arm_2_R"].flags & 0xE00)
        self.assertFalse(tr["Finger_B_2_L"].curves)  # still: base data only
        self.assertEqual(struct.unpack_from("<I", cd.head())[0], 0x1200)
        self.assertEqual(struct.unpack_from("<i", cd.head(), 4)[0], self.emotes["wave"]["frames"])
        firsts = [struct.unpack_from("<i", t.head, 8)[0] for t in cd.tracks]
        counts = [len(t.curves) for t in cd.tracks]
        self.assertEqual(firsts, [sum(counts[:i]) for i in range(len(counts))])

    def test_loops_close(self):
        for i in ("floss", "six_seven", "in_love", "robot", "moonwalk", "air_guitar", "hestu_shake", "revali_smug"):
            clip = eb.sample(self.solver, self.emotes[i])
            a, b = clip.frames[0], clip.frames[-1]
            worst = max(eb.angle_between_m(x, y) for x, y in zip(a[0], b[0]))
            self.assertLess(worst, 1e-4, i)
            cd = eb.build_clip(self.sk, eb.plain_template(self.sk), clip, "x")
            self.assertEqual(struct.unpack_from("<I", cd.head())[0], 0x1204, i)

    def test_floss_arms_and_hips_swing_opposite(self):
        clip = eb.sample(self.solver, self.emotes["floss"])
        ix = self.sk.idx
        half = self.emotes["floss"]["frames"] // 2
        for fr, side in ((0, 1), (half, -1)):
            _, Wp = eb.fk(self.sk, *clip.frames[fr])
            self.assertGreater(Wp[ix["Wrist_R"]][0] * side, 0)          # both hands to one side
            self.assertGreater(Wp[ix["Wrist_L"]][0] * side, 0)
            self.assertLess(Wp[ix["Skl_Root"]][0] * side, 0)            # the hips to the other
        _, Wp0 = eb.fk(self.sk, *clip.frames[0])
        _, Wp12 = eb.fk(self.sk, *clip.frames[half])
        self.assertGreater(Wp0[ix["Wrist_R"]][2], Wp0[ix["Wrist_L"]][2])     # right arm in front, left behind
        self.assertGreater(Wp12[ix["Wrist_L"]][2], Wp12[ix["Wrist_R"]][2])   # then swapped

    def test_six_seven_hands_alternate(self):
        clip = eb.sample(self.solver, self.emotes["six_seven"])
        ix = self.sk.idx
        _, a = eb.fk(self.sk, *clip.frames[0])
        _, b = eb.fk(self.sk, *clip.frames[10])
        self.assertGreater(a[ix["Wrist_R"]][1] - a[ix["Wrist_L"]][1], 0.1)
        self.assertGreater(b[ix["Wrist_L"]][1] - b[ix["Wrist_R"]][1], 0.1)

    def test_in_love_thrusts(self):
        clip = eb.sample(self.solver, self.emotes["in_love"])
        ix = self.sk.idx
        z = [eb.fk(self.sk, *clip.frames[f])[1][ix["Skl_Root"]][2] for f in (0, 6)]
        self.assertGreater(z[1] - z[0], 0.12)

    def test_every_emote_solves_on_any_skeleton(self):
        for d in self.emotes.values():
            if d["kind"] == "keys":
                clip = eb.sample(self.solver, d)
                self.assertEqual(len(clip.frames), d["frames"] + 1, d["id"])

    def wp(self, st):
        rots, trans = self.solver.solve(st)
        return eb.fk(self.sk, rots, trans)

    def test_spline_keeps_moving_through_keys(self):
        spec = {"id": "t", "frames": 30, "loop": False, "ease": "spline",
                "keys": [{"f": 0}, {"f": 10, "aim": {"Arm_1_R": [-0.6, 0.0, 0.8]}},
                         {"f": 20, "aim": {"Arm_1_R": [-0.6, 0.6, 0.5]}}, {"f": 30, "reset": True}]}
        i = self.sk.idx["Arm_1_R"]

        def speed(sp):
            clip = eb.sample(self.solver, sp)
            return [eb.angle_between_m(a[0][i], b[0][i]) for a, b in zip(clip.frames, clip.frames[1:])]
        spl, smo = speed(spec), speed(dict(spec, ease="smooth"))
        self.assertGreater(spl[9] + spl[10], 2.0)                 # no stop on the middle key
        self.assertLess(smo[9] + smo[10], spl[9] + spl[10])       # smooth stops there
        self.assertLess(spl[0], 0.5 * max(spl[:10]))               # it starts from rest (no jump to full speed)
        stops = dict(spec, keys=[{"f": 0}, {"f": 10, "aim": {"Arm_1_R": [-0.6, 0.0, 0.8]}},
                                 {"f": 20, "aim": {"Arm_1_R": [-0.6, 0.6, 0.5]}, "ease": "smooth"},
                                 {"f": 30, "reset": True}])
        two = speed(stops)[:10]                                   # spline between two stops: eases, not linear
        self.assertLess(two[0], 0.5 * max(two))
        self.assertLess(two[9], 0.5 * max(two))

    def test_hit_overshoots_and_settles(self):
        vals = [eb.ease_t("hit", u / 10, 0, 10) for u in range(11)]
        self.assertGreater(max(vals), 1.02)
        self.assertEqual(vals[-1], 1.0)
        self.assertEqual(eb.ease_t("in", 0.5, 0, 10), 0.25)
        self.assertEqual(eb.ease_t("out", 0.5, 0, 10), 0.75)

    def test_heel_keeps_the_ball_of_the_foot_down(self):
        _, Wp0 = self.wp(eb.State())
        _, Wp = self.wp(eb.State(heel={"L": 35.0}, step={"Ankle_L": (0.0, 0.0, 0.05)}))
        toe0, toe = Wp0[self.sk.idx["Toe_L"]], Wp[self.sk.idx["Toe_L"]]
        self.assertLess(abs(toe[1] - toe0[1]), 0.002)              # the ball stays on the ground
        self.assertLess(abs(toe[2] - toe0[2] - 0.05), 0.002)       # where the step puts it
        self.assertGreater(Wp[self.sk.idx["Ankle_L"]][1] - Wp0[self.sk.idx["Ankle_L"]][1], 0.04)   # the heel is up
        self.assertEqual(eb.mirror_state(eb.State(heel={"L": 20.0})).heel, {"R": 20.0})

    def test_lag_delays_a_bone(self):
        spec = {"id": "t", "frames": 20, "loop": True, "ease": "spline",
                "keys": [{"f": 0}, {"f": 10, "turn": {"Head": [0, 30, 0]}}]}
        base = eb.sample(self.solver, spec)
        lagged = eb.sample(self.solver, dict(spec, lag={"Head": 3}))
        i = self.sk.idx["Head"]
        for fr in range(21):
            self.assertLess(eb.angle_between_m(lagged.frames[fr][0][i], base.frames[(fr - 3) % 20][0][i]), 1e-6)

    def test_breath_moves_the_chest_and_closes_a_loop(self):
        spec = {"id": "t", "frames": 40, "loop": True, "breath": [2.0, 40], "keys": [{"f": 0}]}
        clip = eb.sample(self.solver, spec)
        i = self.sk.idx["Spine_2"]
        self.assertGreater(eb.angle_between_m(clip.frames[0][0][i], clip.frames[10][0][i]), 0.5)
        self.assertLess(eb.angle_between_m(clip.frames[0][0][i], clip.frames[40][0][i]), 1e-6)

    def test_collisions_find_a_hand_in_the_chest(self):
        _, Wp0 = self.wp(eb.State())
        self.assertEqual(eb.collisions(self.sk, *self.wp(eb.State())), [])
        inside = eb.vadd(Wp0[self.sk.idx["Spine_2"]], (0.0, 0.08, 0.0))
        Wr, Wp = self.wp(eb.State(reach={"Wrist_R": inside}))
        hits = eb.collisions(self.sk, Wr, Wp)
        self.assertTrue(any("right" in h[0] and h[1] > 0.03 for h in hits), hits)
        allowed = eb.collisions(self.sk, Wr, Wp, contact=("Wrist_R",))
        self.assertFalse([h for h in allowed if "hand" in h[0] or "fingers" in h[0]])

    def test_preview_sheet(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow isn't installed")
        clip = eb.sample(self.solver, self.emotes["robot"])
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "robot.jpg")
            res = eb.render_sheet(self.sk, clip, p)
            with open(p, "rb") as f:
                self.assertEqual(f.read(2), bytes([0xFF, 0xD8]))
        self.assertGreaterEqual(res["stats"]["still"][0], 4)   # the robot holds dead still after each hit
        self.assertLessEqual(res["stats"]["still"][0], 8)

    def test_dances_never_stand_still(self):
        for i in ("moonwalk", "air_guitar", "floss", "hestu_shake", "wave", "clap"):
            st = eb.motion_stats(self.sk, eb.sample(self.solver, self.emotes[i]))
            self.assertLessEqual(st["still"][0], 3, i)

    def test_preview_png(self):
        clip = eb.sample(self.solver, self.emotes["wave"])
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "wave.png")
            eb.write_preview(self.sk, clip, p)
            with open(p, "rb") as f:
                self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")


class DataTests(unittest.TestCase):
    def setUp(self):
        self.emotes = eb.load_emotes()

    def test_lint_clean(self):
        self.assertEqual(eb.lint_all(self.emotes), [])

    def test_the_list(self):
        pages = [d for d in self.emotes if d["page"] in eb.PAGES]
        self.assertEqual(len(pages), 32)
        self.assertEqual(len({(d["page"], d["slot"]) for d in pages}), 32)
        self.assertEqual([d["id"] for d in self.emotes if d["page"] == eb.SPECIAL], ["ragdoll"])
        ids = {d["id"] for d in self.emotes}
        for want in ("thumbs_up", "thumbs_down", "ragdoll", "six_seven", "in_love", "floss"):
            self.assertIn(want, ids)
        for d in self.emotes:
            self.assertLessEqual(len(eb.SET_PREFIX + d["id"]), eb.MAX_SET_NAME)
            self.assertLessEqual(d.get("frames", 0), 255)
            self.assertTrue(d["by"])

    def test_lint_catches_mistakes(self):
        bad = dict(self.emotes[0], keys=[{"f": 0}, {"f": 5, "aim": {"Arm_9_R": [0, 1, 0]}}], _file="wave.json")
        self.assertTrue(eb.lint_all([bad]))
        bad2 = dict(self.emotes[0], name="A really very long emote name here", _file="wave.json")
        self.assertTrue(eb.lint_one(bad2))
        clip = {"id": "c", "name": "C", "page": "poses", "slot": 1, "by": "giruka", "about": "A test.",
                "kind": "game_clip", "clip": "Nml_Wait", "loop": False, "_file": "c.json"}
        self.assertFalse(eb.lint_one(dict(clip, start=10, end=40)))
        self.assertTrue(eb.lint_one(dict(clip, start=40, end=10)))     # end comes after start

    def test_header_and_mod_toml_in_sync(self):
        with open(eb.HEADER, encoding="utf-8") as f:
            self.assertEqual(f.read(), eb.header_text(self.emotes), "run: python tools/emote_build.py header")
        with open(eb.MOD_TOML, encoding="utf-8") as f:
            cur = f.read()
        self.assertEqual(cur, eb.mod_toml_text(cur, self.emotes), "run: python tools/emote_build.py header")

    def test_mod_toml(self):
        import tomllib
        with open(eb.MOD_TOML, "rb") as f:
            d = tomllib.load(f)
        self.assertEqual(d["mod"]["authors"], ["giruka"])
        self.assertEqual(d["mod"]["id"], eb.MOD_ID)
        keys = [s["key"] for s in d["settings"]]
        self.assertEqual(len(keys), len(set(keys)))
        for s in d["settings"]:
            self.assertLessEqual(len(s["label"]), 28, s["key"])
            self.assertLessEqual(len(s["description"]), 80, s["key"])


class LogTests(unittest.TestCase):
    def setUp(self):
        self.emotes = eb.load_emotes()

    def test_sorted_names(self):
        pio = eb.aslist_log(self.emotes)
        body = pio.root.lists[zlib.crc32(eb.ASLIST_PATH.encode())]
        anims, names = eb.list_names(body)
        self.assertEqual(anims, [eb.ARCHIVE])
        self.assertEqual(names, sorted(names, key=lambda s: s.encode()))
        self.assertTrue(all(n.startswith(eb.SET_PREFIX) for n in names))
        self.assertGreater(names[0].encode(), eb.LAST_GAME_SET.encode())
        self.assertEqual(bnp.file_table(bnp.aamp_read(bnp.aamp_write(pio))), [eb.ASLIST_PATH])

    def test_merge_with_a_linkle_like_log(self):
        def obj(**kv):
            return bnp.PObj({zlib.crc32(k.encode()): (8, v.encode()) for k, v in kv.items()})
        game_names = ["Attack", "Run", "TalkHello", "Wait", "WeaponThrowCharge"]
        game = bnp.PList(lists={
            bnp.ADD_RESES: bnp.PList(objs={zlib.crc32(b"AddRes_0"): obj(Anim="Player_Animation"),
                                           zlib.crc32(b"AddRes_1"): obj(Anim="Motorcycle_Animation")}),
            bnp.AS_DEFINES: bnp.PList(objs={zlib.crc32(f"ASDefine_{i}".encode()): obj(Name=n, Filename="Player_" + n)
                                            for i, n in enumerate(game_names)})})
        linkle = bnp.PList(lists={bnp.ADD_RESES: bnp.PList(objs={zlib.crc32(b"AddRes_2"): obj(
            Anim="Linkle_Extra_Animation")})})
        bnp.aslist_merge(game, linkle, [])
        ours = eb.aslist_log(self.emotes).root.lists[zlib.crc32(eb.ASLIST_PATH.encode())]
        bad = []
        bnp.aslist_merge(game, ours, bad)
        anims, names = eb.list_names(game)
        self.assertEqual(anims, ["Player_Animation", "Motorcycle_Animation", "Linkle_Extra_Animation", eb.ARCHIVE])
        self.assertEqual(bad, [])
        for n in game_names + [eb.SET_PREFIX + d["id"] for d in self.emotes]:
            self.assertTrue(eb.bsearch(names, n), n)

    def test_bas_reads_back(self):
        data = eb.make_bas("SwEmote_floss", 6, "FaceSmile")
        got = eb.read_bas(data)
        self.assertEqual(got, {"TypeIndex": 67, "FileName": "SwEmote_floss", "Morph": 6.0, "faces": ["FaceSmile"]})
        self.assertEqual(eb.read_bas(eb.make_bas("Rolldown_U", 8, None))["faces"], [])
        self.assertEqual(aamp.load(data).list("Elements").list("Element0").object("Parameters").get_value("TypeIndex"),
                         67)


def _game():
    try:
        return eb.find_game(None)
    except eb.EmoteError:
        return None


@unittest.skipUnless(_game() is not None, "the user's game files aren't here")
class WithGameFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        eb._low_priority()
        cls.game = _game()
        cls.emotes = eb.load_emotes()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.built = eb.build_package(cls.game, cls.emotes, cls.tmp.name)   # strict: every pose check passes

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_built(self):
        b = self.built
        self.assertEqual(b.clips, sum(1 for d in self.emotes if d["kind"] in ("keys", "game_clip")))
        self.assertEqual(b.copied, {d["id"]: d["clip"] for d in self.emotes if d["kind"] == "game_clip"
                                    and not d.get("start")})
        self.assertEqual(b.sets, len(self.emotes))
        self.assertLessEqual(b.readback_deg, eb.READBACK_DEG)
        for name in ("mod.toml", "info.json", "emotes.json", "logs/aslist.aamp", "logs/rstb.json",
                     f"{eb.BASE_TID}/romfs/Pack/TitleBG.pack"):
            self.assertTrue(os.path.isfile(os.path.join(b.folder, *name.split("/"))), name)

    def test_pack_holds_only_ours(self):
        with open(os.path.join(self.built.folder, eb.BASE_TID, "romfs", "Pack", "TitleBG.pack"), "rb") as f:
            tb = bnp.Blob(f.read())
        self.assertEqual(sorted(tb.open()), sorted([f"Model/{eb.ARCHIVE}.sbfres", eb.PLAYER_PACK]))
        names = sorted(tb.child(eb.PLAYER_PACK).open())
        self.assertEqual(names, sorted(f"Actor/AS/{eb.BAS_PREFIX}{d['id']}.bas" for d in self.emotes))
        wave = eb.read_bas(tb.child(eb.PLAYER_PACK).child(f"Actor/AS/{eb.BAS_PREFIX}wave.bas").raw)
        self.assertEqual((wave["TypeIndex"], wave["FileName"]), (67, "SwEmote_wave"))

    def test_game_clips_are_copied(self):
        # Game check r2: The Linkle Mod's Player_Animation has a 1-frame empty Random_Nml_Wait_Kinnikujiman, so a set
        # naming the game clip ended at once. Our archive carries a copy of the player's own clip under our name.
        from wiiu_model import nx_model
        with open(os.path.join(self.built.folder, eb.BASE_TID, "romfs", "Pack", "TitleBG.pack"), "rb") as f:
            tb = bnp.Blob(f.read())
        ours = {a.name: a for a in nx_model.read(tb.child(f"Model/{eb.ARCHIVE}.sbfres").raw).skel_anims}
        game = {a.name: a for a in self.game.anims().skel_anims}
        for d in self.emotes:
            if d["kind"] != "game_clip" or d.get("start"):
                continue
            with self.subTest(emote=d["id"]):
                bas = eb.read_bas(tb.child(eb.PLAYER_PACK).child(f"Actor/AS/{eb.BAS_PREFIX}{d['id']}.bas").raw)
                self.assertEqual(bas["FileName"], eb.CLIP_PREFIX + d["id"])
                a, g = ours[eb.CLIP_PREFIX + d["id"]], game[d["clip"]]
                self.assertEqual((a.head, a.bone_anims), (g.head, g.bone_anims))
        flex = struct.unpack_from("<i", ours[eb.CLIP_PREFIX + "daruk_flex"].head, 4)[0]
        self.assertGreater(flex, 1, "daruk_flex must be the real clip, not a 1-frame stand-in")
        rec = {s["id"]: s for s in self.built.record["sets"]}
        self.assertEqual(rec["daruk_flex"]["from_clip"], "Random_Nml_Wait_Kinnikujiman")
        # victory: the game clip from frame 112 on (no look down at the cooking pot), written as our own clip
        vic = struct.unpack_from("<i", ours[eb.CLIP_PREFIX + "victory"].head, 4)[0]
        self.assertEqual(rec["victory"]["from_frame"], 112)
        self.assertEqual(vic, rec["victory"]["frames"])
        # ... up to frame 151, the landing (the clip's last second looks down at the pot again)
        self.assertEqual(rec["victory"]["to_frame"], 151)
        self.assertEqual(vic, 151 - 112)
        self.assertLess(vic, struct.unpack_from("<i", game["Demo_Get_Cooking_VeryGood"].head, 4)[0])

    @unittest.skipUnless(LINKLE_LOG.is_file(), "The Linkle Mod's log isn't in out/emotes-lib")
    def test_merge_with_linkle(self):
        with open(LINKLE_LOG, "rb") as f:
            lk = bnp.aamp_read(f.read())
        anims, names = eb.list_names(eb.merged_list(self.game, self.emotes, [lk]))
        self.assertEqual(anims[-2:], ["Linkle_Extra_Animation", eb.ARCHIVE])
        self.assertEqual(len(anims), 4)
        self.assertEqual(names, sorted(names, key=lambda s: s.encode()))
        for d in self.emotes:
            self.assertTrue(eb.bsearch(names, eb.SET_PREFIX + d["id"]))
        self.assertTrue(eb.bsearch(names, "Wait"))


@unittest.skipUnless(sys.platform == "win32", "Windows only (a Windows DLL)")
class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import build_native as bn
        import modmenu
        try:
            tc = bn.find_toolchain(bn.default_install())
        except bn.BuildError as e:
            raise unittest.SkipTest(str(e))
        src = REPO / "native" / "examples" / "emotes" / "selftest" / "emotes_selftest.c"
        t = bn.Target("emotes_selftest", (src,), runtime=False, out_name="emotes_selftest.dll", export="emotes_selftest")
        built = bn.build(tc, REPO / "build" / "native" / "emotes-selftest", [t], runner=modmenu.low_priority_runner)
        bn.check_exports(zip(built, [t]))
        dll = ctypes.CDLL(str(built[0]))
        buf = ctypes.create_string_buffer(16384)
        dll.emotes_selftest.argtypes = (ctypes.c_char_p, ctypes.c_int)
        dll.emotes_selftest.restype = ctypes.c_int
        cls.fails = dll.emotes_selftest(buf, len(buf))
        cls.report = buf.value.decode("utf-8", "replace")
        cls.rows = {}
        for line in cls.report.splitlines():
            group, name, status, detail = (line.split(" ", 3) + ["", "", "", ""])[:4]
            cls.rows[(group, name)] = (status, detail)

    def group(self, group, names):
        for n in names:
            with self.subTest(check=f"{group} {n}"):
                status, detail = self.rows.get((group, n), ("MISSING", "not run"))
                self.assertEqual(status, "PASS", detail)

    def test_parse(self):
        self.group("parse", ["id", "alias_67", "alias_thrust", "number", "prefix", "community", "two_words"])

    def test_playerwait(self):
        # the game's standing code (PlayerWait's calc) puts Wait back unless slot 0 reads Wait: the mask keeps ours
        self.group("pw", ["replay_blocked", "name_restored", "finish_releases", "game_set_in_calc_wins",
                          "took_when_off", "always_mode", "always_stop_unmasks", "standing_from_pw",
                          "moving_refused"])

    def test_machine(self):
        self.group("ai", ["found", "scan_fallback", "event_above_root", "current_is_1c"])
        self.group("machine", ["start", "playing", "finished_then_wait", "refused_moving", "loop_playing",
                               "loop_keeps", "game_took_over", "stop", "spam_guard", "community",
                               "load_drops_request"])
        self.group("missing", ["fallback", "refused", "why_whole", "late_set_to_wait", "game_talk_untouched",
                               "call_fails"])
        self.group("revert", ["said", "hold_replays"])
        self.group("noai", ["after_load_refused", "standing_starts", "moving_refused", "event_refused",
                            "lift_refused"])
        self.group("guard", ["event_refused", "weapon_refused", "weapon_on_back_ok"])

    def test_path(self):
        # Game check r2: slot 0 held our set but the model stood idle. Link's own body play flags him so his animation
        # step puts the set on slot 1 sub 0 (the upper body); the generic play (A/B) leaves the upper body on Wait
        self.group("path", ["body_play", "upper_body_seen", "wait_on_both", "generic_legs_only", "upper_dbg_plays",
                            "upper_dbg_runs", "upper_dbg_stops", "upper_dbg_stop_cmd"])

    def test_names(self):
        # the game keeps our char*: slot 0 must read back names that live outside the game's memory
        self.group("names", ["after_same_tick", "slot0_reads_ours", "interned", "kept_ptr_read"])

    def test_ragdoll(self):
        # the real ragdoll: the game's launch (PlayerLaunch) entered with our arc, aimed forward, no damage; the pose
        # for A/B and as the fallback
        self.group("ragdoll", ["pose_on", "pose_off", "throw_not_walking", "throw_not_in_event", "throw_on",
                               "weapon_in_hand_ok", "throw_arc_no_damage", "throw_forward", "throw_pointers_back",
                               "state_shows_pose", "throw_down_no_damage", "no_hold", "getup_press", "up_restores",
                               "cooldown", "after_cooldown", "not_taken_pose", "exit_restores", "no_launch_pose",
                               "bad_settings_pose", "slip_mode", "slip_up_restores", "load_forgets"])

    def test_state(self):
        self.group("state", ["fields", "no_id_at_boot"])

    def test_wheel(self):
        # the emote wheel (wheel.c) through a made-up overlay host: hold G / D-pad Down, point, let go to play
        self.group("wheel", ["slices", "pages", "extras_page", "no_world_no_wheel", "hold_opens", "mouse_points",
                             "release_plays", "toast_names", "tap_then_digit", "keys_quiet", "cmd_opens",
                             "page_turns", "keys_point_enter_plays", "draws", "esc_closes", "pad_hold",
                             "pad_b_closes", "zero_ragdoll", "menu_closes", "idle_nothing", "toast_why",
                             "state_field"])

    def test_nothing_else_failed(self):
        self.assertEqual(self.fails, 0, self.report)


if __name__ == "__main__":
    unittest.main()
