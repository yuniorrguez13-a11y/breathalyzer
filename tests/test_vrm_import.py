"""charsel import-vrm (tools/vrm_import.py): the fit of a VRM avatar onto Link's skeleton, with no game and no VRM.

- rot_between turns one direction onto another; a Fit puts the avatar's joint on Link's, turned to his bone and
  stretched to its length (the part behind the joint isn't stretched).
- own_fits / own_skeleton (2026-11-15): the avatar keeps her own lengths (her arms close to her chest), turned to
  Link's rest pose, feet on his ankle height, the skeleton resting at her joints; as_mask, game_albedo.
- quantize keeps the 4 heaviest bones, weights summing 255.
- read_vrm: a VRM 1.0 humanoid as is, a VRM 0.x one with its thumbs moved a step and flipped to face +Z, and a plain
  glTF refused; node_links sends hair and skirt bones to their nearest humanoid ancestor.
"""

import json
import struct
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import charsel  # noqa: E402
import vrm_import as vi  # noqa: E402


def glb(doc: dict) -> bytes:
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    return b"glTF" + struct.pack("<II", 2, 12 + 8 + len(js)) + struct.pack("<II", len(js), 0x4E4F534A) + js


HUMAN = ("hips", "head", "leftUpperArm", "rightUpperArm", "leftUpperLeg", "rightUpperLeg")


class Fit(unittest.TestCase):
    def test_rot_between(self):
        for a, b in (((1.0, 0, 0), (0, 1.0, 0)), ((0, 0, 1.0), (0, 0, -1.0)), ((0, 1.0, 0), (0, 1.0, 0))):
            got = vi._apply(vi.rot_between(a, b), a)
            for c in range(3):
                self.assertAlmostEqual(got[c], b[c], places=6)

    def test_fit_moves_turns_and_stretches(self):
        # avatar arm: shoulder at x 0.1, elbow at 0.3 (0.2 long); Link's: 0.2 and 0.6 (0.4 long), both along +x
        fits = vi.make_fits({"leftUpperArm": (0.1, 1.4, 0.0), "leftLowerArm": (0.3, 1.4, 0.0)},
                            {"Arm_1_L": (0.2, 1.3, 0.0), "Arm_2_L": (0.6, 1.3, 0.0)})
        f = fits["Arm_1_L"]
        self.assertAlmostEqual(f.k, 2.0)
        p = f.point((0.2, 1.45, 0.0))            # half way down the avatar's arm, 5 cm above it
        for got, want in zip(p, (0.4, 1.35, 0.0)):
            self.assertAlmostEqual(got, want, places=6)
        p = f.point((0.05, 1.4, 0.0))            # behind the shoulder: moved, not stretched
        self.assertAlmostEqual(p[0], 0.15, places=6)
        self.assertAlmostEqual(fits["Arm_2_L"].k, 1.0)      # (no avatar hand: moved only)
        for got, want in zip(fits["Arm_2_L"].point((0.35, 1.4, 0.0)), (0.65, 1.3, 0.0)):
            self.assertAlmostEqual(got, want, places=6)

    def test_quantize(self):
        ix, w = vi.quantize({3: 0.5, 7: 0.25, 9: 0.125, 1: 0.0625, 2: 0.0625})
        self.assertEqual(ix[:3], [3, 7, 9])
        self.assertEqual(sum(w), 255)
        ix, w = vi.quantize({5: 1.0})
        self.assertEqual((ix, w), ([5, 5, 5, 5], [255, 0, 0, 0]))

    def link_like(self):
        """A small Link-like skeleton in his T-pose: Root > Skl_Root (hips 1.0) > Waist, Spine_1 > Spine_2 >
        Clavicle_L > Arm_1_L > Arm_2_L > Wrist_L, Waist > Leg_1_L > Leg_2_L > Ankle_L (+x, then down)."""
        from wiiu_model.neutral import Bone
        z, r0 = (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0)
        rows = [("Root", -1, (0, 0, 0)), ("Skl_Root", 0, (0, 1.0, 0)), ("Waist", 1, (0, 0, 0)),
                ("Spine_1", 1, (0, 0.1, 0)), ("Spine_2", 3, (0, 0.2, 0)), ("Clavicle_L", 4, (0.03, 0.2, 0)),
                ("Arm_1_L", 5, (0.15, 0, 0)), ("Arm_2_L", 6, (0.25, 0, 0)), ("Wrist_L", 7, (0.25, 0, 0)),
                ("Leg_1_L", 2, (0.1, -0.05, 0)), ("Leg_2_L", 9, (0, -0.42, 0)), ("Ankle_L", 10, (0, -0.4, 0))]
        return [Bone(n, p, -1, -1, 0xFFFF, 0, z, r0, t) for n, p, t in rows]

    def test_own_lengths_2026_11_15(self):
        # "why are her arms so far away from her chest": the avatar keeps her own collarbone and arm lengths
        # (half Link's here), only turned to his rest pose; her feet go on his ankle height
        bones = self.link_like()
        vj = {"hips": (0.0, 0.95, 0.0), "spine": (0.0, 1.05, 0.0), "chest": (0.0, 1.25, 0.0),
              "leftShoulder": (0.02, 1.43, 0.0), "leftUpperArm": (0.08, 1.43, 0.0),     # (collarbone 6 cm: Link 15)
              "leftLowerArm": (0.08, 1.23, 0.0), "leftHand": (0.08, 1.03, 0.0),         # (her arms hang down: A-pose)
              "leftUpperLeg": (0.08, 0.9, 0.0), "leftLowerLeg": (0.08, 0.5, 0.0), "leftFoot": (0.08, 0.1, 0.0)}
        fits, joints, info = vi.own_fits(vj, bones)
        self.assertTrue(info["own_lengths"])
        self.assertAlmostEqual(joints["Arm_1_L"][0] - joints["Clavicle_L"][0], 0.06, places=6)  # her own 6 cm
        self.assertAlmostEqual(joints["Arm_2_L"][0] - joints["Arm_1_L"][0], 0.2, places=6)   # her arm, turned to +x
        self.assertAlmostEqual(joints["Arm_2_L"][1], joints["Arm_1_L"][1], places=6)
        lw = charsel._worlds(bones)
        self.assertAlmostEqual(joints["Ankle_L"][1], lw[11][1][3], places=6)       # feet on Link's ankle height
        self.assertAlmostEqual(info["feet_shift_m"], lw[11][1][3] - 0.1, places=3)
        # a point on her forearm, 5 cm toward the hand: turned with the arm about her own elbow, not stretched
        p = fits["Arm_2_L"].point((0.08, 1.18, 0.0))
        self.assertAlmostEqual(p[0] - joints["Arm_2_L"][0], 0.05, places=6)
        # the skeleton rests at those joints with Link's turns; its inverse bind matrices match
        import struct
        from wiiu_model.neutral import Skeleton
        sk = Skeleton(charsel.EULER_ROT, bones, list(range(len(bones))), len(bones), 0,
                      b"\0" * (48 * len(bones)))
        new = vi.own_skeleton(sk, joints)
        w = charsel._worlds(new.bones)
        names = [b.name for b in new.bones]
        for n, j in joints.items():
            for c in range(3):
                self.assertAlmostEqual(w[names.index(n)][c][3], j[c], places=6)
        self.assertEqual(new.bones[1].translation, bones[1].translation)             # (Skl_Root: Link's)
        ib = struct.unpack_from("<12f", new.inverse_matrices, 48 * names.index("Wrist_L"))
        self.assertAlmostEqual(ib[3], -joints["Wrist_L"][0], places=5)
        self.assertEqual(sk.bones[6].translation, (0.15, 0, 0))                     # (Link's own left as it was)

    def test_zelda_skin_fed_by_our_meshes(self):
        """2026-11-18 (`iam custom` showed only her head with Zelda's Mt_Skin): the zelda-fed variant drops the shader
        attribute our buffers lack (`All`) and turns the vertex position modify off as Link's material has it."""
        import types
        sh = lambda at, op: types.SimpleNamespace(attribs=at, options=op)    # noqa: E731
        zelda = types.SimpleNamespace(shader=sh([("_p0", "_p0"), ("_u1", "_u1"), ("_u2", "All")],
                                                [("uking_vtx_position_modify_calc_type", "8"),
                                                 ("uking_vtx_position_modify_A", "300"), ("uking_enable_ao", "1")]))
        link = types.SimpleNamespace(shader=sh([], [("uking_vtx_position_modify_calc_type", "0"),
                                                    ("uking_vtx_position_modify_A", "100"), ("uking_enable_ao", "0")]))
        m = vi.fed_by_our_meshes(zelda, link)
        self.assertEqual(m.shader.attribs, [("_p0", "_p0"), ("_u1", "_u1")])
        self.assertEqual(m.shader.options, [("uking_vtx_position_modify_calc_type", "0"),
                                            ("uking_vtx_position_modify_A", "100"), ("uking_enable_ao", "1")])
        self.assertEqual(zelda.shader.attribs[-1], ("_u2", "All"))                  # (a copy)
        self.assertEqual(vi.SKIN_MATERIAL, "link")        # (the fallback until the game check says otherwise)

    def test_mask_material_and_albedo(self):
        import types
        ri = lambda n, v: types.SimpleNamespace(name=n, values=v)    # noqa: E731
        mat = types.SimpleNamespace(render_infos=[ri("gsys_render_state_mode", ("opaque",)),
                                                  ri("gsys_alpha_test_enable", ("false",)), ri("gsys_pass", ("x",))])
        m = vi.as_mask(mat)
        self.assertEqual([r.values for r in m.render_infos], [("mask",), ("true",), ("x",)])
        self.assertEqual(mat.render_infos[0].values, ("opaque",))                   # (a copy)
        import menu_bg
        Image = menu_bg.pillow()
        if Image is None:
            self.skipTest("no Pillow")
        img = vi.game_albedo(Image.new("RGBA", (4, 4), (248, 230, 216, 200)), 0.5)
        self.assertEqual(img.getpixel((0, 0)), (124, 115, 108, 200))               # colour scaled, alpha kept

    def test_every_link_bone_is_links(self):
        # (names only: Link's skeleton as tests/test_charsel_puppet.py's made-up models name it, and his real one has
        # each of these; checked against the game by charsel import-vrm itself)
        for lb in set(vi.BONES.values()) | set(vi.LINK_NEXT.values()):
            self.assertRegex(lb, r"^(Waist|Spine_[12]|Neck|Head|Clavicle_[LR]|Arm_[12]_[LR]|Wrist_[LR]|"
                                 r"Finger_[A-E]_[123]_[LR]|Leg_[12]_[LR]|Ankle_[LR]|Toe_[LR])$")


class ReadVrm(unittest.TestCase):
    def nodes(self):
        # 0 hips (its children: head, skirt, arms, legs), 1 head (its child: hair), 8 a thumb (on a leg: names only)
        n = [{"name": "hips", "children": [1, 2, 4, 5, 6, 7]}, {"name": "head", "children": [3]},
             {"name": "skirt"}, {"name": "hair"}, {"name": "lua"}, {"name": "rua"}, {"name": "lul"},
             {"name": "rul", "children": [8]}, {"name": "rthumb", "translation": [0.5, 1.0, 0.2]}]
        return n

    def test_vrm1(self):
        hb = {b: {"node": i} for b, i in zip(HUMAN, (0, 1, 4, 5, 6, 7))}
        g, got, flip = vi.read_vrm(glb({"asset": {"version": "2.0"}, "nodes": self.nodes(),
                                        "extensions": {"VRMC_vrm": {"humanoid": {"humanBones": hb}}}}))
        self.assertFalse(flip)
        self.assertEqual(got["head"], 1)
        links = vi.node_links(g, got)
        self.assertEqual((links[3], links[2], links[1]), ("Head", "Waist", "Head"))   # hair -> head, skirt -> hips

    def test_vrm0_thumbs_and_facing(self):
        hb = [{"bone": b, "node": i} for b, i in zip(HUMAN, (0, 1, 4, 5, 6, 7))]
        hb.append({"bone": "rightThumbProximal", "node": 8})
        _g, got, flip = vi.read_vrm(glb({"asset": {"version": "2.0"}, "nodes": self.nodes(),
                                         "extensions": {"VRM": {"humanoid": {"humanBones": hb}}}}))
        self.assertTrue(flip)
        self.assertEqual(got["rightThumbMetacarpal"], 8)
        self.assertEqual(vi.BONES["rightThumbMetacarpal"], "Finger_A_1_R")

    def test_not_a_vrm(self):
        with self.assertRaises(charsel.CharselError):
            vi.read_vrm(glb({"asset": {"version": "2.0"}, "nodes": self.nodes()}))
        with self.assertRaises(charsel.CharselError):      # (a humanoid with no head)
            vi.read_vrm(glb({"asset": {"version": "2.0"}, "nodes": self.nodes(), "extensions": {
                "VRMC_vrm": {"humanoid": {"humanBones": {"hips": {"node": 0}}}}}}))


if __name__ == "__main__":
    unittest.main()
