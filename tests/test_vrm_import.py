"""charsel import-vrm (tools/vrm_import.py): the fit of a VRM avatar onto Link's skeleton, with no game and no VRM.

- rot_between turns one direction onto another; a Fit puts the avatar's joint on Link's, turned to his bone and
  stretched to its length (the part behind the joint isn't stretched).
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
