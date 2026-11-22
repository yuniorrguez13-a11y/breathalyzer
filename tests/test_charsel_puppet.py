"""See yourself as your pick, live (ghost mod 0.9.0, native/examples/ghost/puppet.c): no game.

- The DLL's own table (which actor name each character's pack is on, its name, its aliases, the word `charsel
  playas` takes) matches tools/charsel.py, so `iam daruk` in the game spawns the pack charsel builds for Daruk.
- The selftest's "puppet" group (native/examples/ghost/selftest/ghost_selftest.c, built with Savage Wind's zig):
  Link's Move by speed (Daruk's sprint is RunGear2, his rolling ball), near names (ClimbJump -> ClimbMove), Link shown
  where the character can't mirror him (a horse, a climb with no climb clip, a cutscene), the pause menu keeps it,
  Link hidden with the game's model show/hide and hidden again when the game shows him by itself, the puppet at
  Link's place, Link's items to the puppet only without a friend's ghost, a stage change, `iam link`, `iam self off`,
  and a pack the game refuses (given up after 5 tries, "Restart the game to play as Daruk").
- 0.9.1 ragdoll: Link's ragdoll manager's bit (or a ragdoll clip) -> the puppet's animation paused and its bones
  turned as Link's (its own lengths, its extra bones following), raised so its body boxes lie on the ground fitted
  under Link; up again -> its animation and clip back; `ghost puppet ragdoll clip|off`. The body boxes
  (pup_bodies.h, charsel.py bodies) cover Link and every pack body, and body_boxes reads a made-up model right.
"""

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import charsel  # noqa: E402

GHOST = REPO / "native" / "examples" / "ghost"
PUPPET_C = GHOST / "puppet.c"
SRC = GHOST / "selftest" / "ghost_selftest.c"
OUT = REPO / "build" / "native" / "charsel-puppet-selftest"


def dll_slots() -> dict[str, tuple[str, str, str]]:
    text = PUPPET_C.read_text(encoding="utf-8")
    block = text.split("PUP_SLOTS[] = {", 1)[1].split("};", 1)[0]
    return {m[0]: (m[1], m[2], m[3]) for m in re.findall(r'\{"([^"]*)", "([^"]*)", "([^"]*)", "([^"]*)"\}', block)}


def dll_aliases() -> dict[str, str]:
    text = PUPPET_C.read_text(encoding="utf-8")
    block = text.split("PUP_ALIASES[] = {", 1)[1].split("};", 1)[0]
    return dict(re.findall(r'\{"([^"]*)", "([^"]*)"\}', block))


class Table(unittest.TestCase):
    def test_every_pack_character_on_charsels_actor_name(self):
        slots = dll_slots()
        packs = [r.id for r in charsel.RECIPES.values() if r.kind == "pack"]
        # (2026-11-18: no Linkle SELF-puppet: you are Link's own body with The Linkle Mod on; her costume stays in
        # charsel's table for the friends' view, review C1)
        self.assertNotIn("linkle", slots)
        self.assertIn(charsel.SLOTS["linkle"], charsel.FREE_SLOTS)
        self.assertEqual(sorted(set(slots) - {"custom"}), sorted(packs))
        import vrm_import
        for cid, (actor, name, playas) in slots.items():
            if cid == vrm_import.IMPORT_ID:     # (charsel import-vrm's costume, on Link's skeleton)
                self.assertEqual((actor, name, playas), (vrm_import.IMPORT_SLOT, "Custom", ""))
                self.assertIn(actor, charsel.FREE_SLOTS)
                continue
            with self.subTest(cid=cid):
                r = charsel.RECIPES[cid]
                self.assertEqual(actor, charsel.SLOTS[cid])
                self.assertEqual(name, r.name.split(" (")[0])
                want = charsel.playas_word(cid) if cid in charsel.PLAYAS_PEOPLE else ""
                self.assertEqual(playas, want)
                if playas:
                    self.assertEqual(charsel.find_id(playas), cid)   # (`charsel playas <word>` takes it)

    def test_aliases_match(self):
        on = set(dll_slots())
        import vrm_import
        want = {a: r.id for r in charsel.RECIPES.values() if r.kind == "pack" or r.id in on for a in r.aliases}
        want.update({a: vrm_import.IMPORT_ID for a in vrm_import.IMPORT_ALIASES})
        self.assertEqual(dll_aliases(), want)

    def test_weapon_swing_names(self):
        # the held weapon's swing names the DLL asks for: the Link body's (every pack has them), and the monsters'
        # extra ones (charsel EXTRA_NAMES, in both monster recipes)
        from mp import ghost_link_pack as glp
        text = PUPPET_C.read_text(encoding="utf-8")
        names = lambda key: re.findall(r'"([A-Za-z0-9_]+)"', text.split(f"{key}[] = {{", 1)[1].split("};", 1)[0])  # noqa
        ghost = {n for n, _c, _f in glp.ANIMS}
        self.assertTrue(set(names("PUP_SWINGS")) <= ghost)
        self.assertEqual(tuple(names("PUP_SWINGS_OWN")), charsel.EXTRA_NAMES)
        for cid in ("bokoblin", "moblin"):
            self.assertTrue(set(charsel.EXTRA_NAMES) <= set(dict(charsel.RECIPES[cid].own)), cid)
            self.assertIn(f'"{cid}"', text.split("pup_own_swings(const char *id)", 1)[1].split("}", 1)[0])

    def test_versions(self):
        toml = (GHOST / "mod.toml").read_text(encoding="utf-8")
        ver = re.search(r'^version = "([^"]+)"', toml, re.M)[1]
        self.assertEqual(ver, "0.9.1")
        self.assertIn(f'#define GHOST_VERSION "{ver}"', (GHOST / "ghost.c").read_text(encoding="utf-8"))
        self.assertIn('#include "puppet.c"', (GHOST / "ghost.c").read_text(encoding="utf-8"))

    def test_body_boxes_cover_every_pack_body(self):
        text = (GHOST / "pup_bodies.h").read_text(encoding="utf-8")
        bones = re.findall(r'"([A-Za-z_0-9]+)"', text.split("PUP_BODY_BONES[PUP_NBODY_BONES] = {", 1)[1].split("};", 1)[0])
        self.assertEqual(tuple(bones), charsel.BODY_BONES)
        block = text.split("PUP_BODIES[] = {", 1)[1]
        ids = re.findall(r'\{"([a-z]+)", \{', block)
        self.assertEqual(ids[0], "link")      # (puppet.c's fallback is the first entry)
        packs = {r.id for r in charsel.RECIPES.values() if r.kind == "pack"}
        self.assertEqual(set(ids) - {"link"}, packs - {"hylian"})   # (the Hylian: Mii-style parts, Link's boxes)
        for box in re.findall(r"\{(-?\d+), (-?\d+), (-?\d+), (-?\d+), (-?\d+), (-?\d+)\}", block):
            lo_hi = [int(v) for v in box]
            for k in range(0, 6, 2):
                self.assertLessEqual(lo_hi[k], 0)
                self.assertGreaterEqual(lo_hi[k + 1], 0)
                self.assertLess(lo_hi[k + 1] - lo_hi[k], 400)   # (cm: no box over 4 m)

    def test_body_boxes_of_a_made_up_model(self):
        import struct
        import types
        from wiiu_model.neutral import Attribute, Bone, Skeleton, VertexBuffer
        euler = charsel.EULER_ROT
        bones = [Bone("Root", -1, -1, -1, 0xFFFF, 0, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
                 Bone("Skl_Root", 0, 0, -1, 0xFFFF, 0, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
                 Bone("Spine_1", 1, 1, -1, 0xFFFF, 0, (1.0, 1.0, 1.0), (0.0, 0.0, 1.5707963, 1.0), (0.0, 0.2, 0.0)),
                 Bone("Belly", 2, 2, -1, 0xFFFF, 0, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.3))]
        sk = Skeleton(euler, bones, [1, 2, 3], 3, 0, b"\0" * 48 * 3)
        # vertices in model space, each on one bone (index into the matrix list, weight 1): two on Skl_Root, one on
        # Spine_1 (turned 90 degrees about z: its own x is the model's y) and one on Belly (counts for Spine_1)
        verts = [((0.3, 1.0, 0.0), 0), ((-0.2, 0.9, 0.1), 0), ((0.0, 1.6, 0.0), 1), ((0.0, 1.2, 0.5), 2)]
        pos = b"".join(struct.pack("<3f", *p) for p, _ in verts)
        idx = b"".join(struct.pack("<2B", i, 0) for _, i in verts)
        wgt = b"".join(struct.pack("<2B", 255, 0) for _ in verts)
        vb = VertexBuffer(4, 2, [Attribute("_p0", (0x18 << 8) | 5, 0, 0), Attribute("_i0", (0x09 << 8) | 3, 0, 1),
                                  Attribute("_w0", (0x09 << 8) | 1, 0, 2)], [12, 2, 2], [pos, idx, wgt])
        shape = types.SimpleNamespace(vertex_buffer=0, skin_count=2, bone=0)
        model = types.SimpleNamespace(skeleton=sk, shapes=[shape], vertex_buffers=[vb])
        got = charsel.body_boxes(model, trim=0.0)
        self.assertEqual(got["Skl_Root"], [-20, 30, -10, 0, 0, 10])
        # Spine_1 sits at y 1.2, turned: model y = its x; the Spine_1 vertex 0.4 up (x+ 40), Belly's 0.5 forward
        self.assertEqual(got["Spine_1"], [0, 40, 0, 0, 0, 50])
        self.assertNotIn("Head", got)
        with self.assertRaises(charsel.CharselError):
            charsel.body_boxes(types.SimpleNamespace(skeleton=Skeleton(0, bones, [], 0, 0, b""), shapes=[],
                                                     vertex_buffers=[]))

    def test_hand_bones_and_header(self):
        import types
        from wiiu_model.neutral import Bone, Skeleton
        z = (1.0, 1.0, 1.0)
        bones = [Bone("Root", -1, -1, -1, 0xFFFF, 0, z, (0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
                 Bone("Wrist_R", 0, -1, -1, 0xFFFF, 0, z, (0.0, 0.0, 0.0, 1.0), (-0.3, 1.2, 0.0)),
                 Bone("Weapon_R", 1, -1, -1, 0xFFFF, 0, z, (1.5707963, 0.0, 0.0, 1.0), (-0.107, 0.0, -0.028)),
                 Bone("Finger_A_1_R", 1, -1, -1, 0xFFFF, 0, z, (0.0, 0.0, 0.0, 1.0), (-0.05, 0.0, 0.0)),
                 Bone("Finger_A_2_R", 3, -1, -1, 0xFFFF, 0, z, (0.0, 0.0, 0.0, 1.0), (-0.03, 0.0, 0.0))]
        model = types.SimpleNamespace(skeleton=Skeleton(charsel.EULER_ROT, bones, [], 0, 0, b""))
        h = charsel.hand_bones(model)
        self.assertEqual(h["bones"]["Weapon_R"], {"parent": "Wrist_R", "place": [-10.7, 0.0, -2.8],
                                                  "turn": [90.0, 0.0, 0.0]})
        self.assertNotIn("Weapon_L", h["bones"])
        self.assertEqual(h["fingers"], {"L": [], "R": ["Finger_A_1_R", "Finger_A_2_R"]})
        self.assertEqual(h["hip"], 0.0)                                # (no Skl_Root in this one)
        text = charsel.hands_header({"link": h})
        self.assertIn("#ifdef PUP_WANT_HANDS", text)                  # (compiled only where it is read)
        self.assertNotRegex(text, r"[^.\d]\d+f")                     # (every float is a C float: 0.0f, never 0f)
        self.assertIn('{"link", {0, 1, 0, 1, 0, 0, 0, 0},', text)
        # the header keeps the body boxes as they were and only adds the hands after them
        boxes = {"link": {"Skl_Root": [-1, 1, -1, 1, -1, 1]}}
        self.assertTrue(charsel.bodies_header(boxes, {"link": h}).startswith(charsel.bodies_header(boxes)))

    def test_merge_parts(self):
        # a body (Root, Skl_Root, Head; smooth matrices on Skl_Root and Head, a rigid one on Root) and a hair part
        # (Root, Head and its own Hair_1; smooth matrices on Head and Hair_1): one model, one skeleton
        import struct
        import types
        from wiiu_model.neutral import Attribute, Bone, Skeleton, VertexBuffer
        z, r0 = (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0)

        def skel(bones, m2b, smooth):
            w = charsel._worlds(bones)
            ib = b"".join(struct.pack("<12f", *(v for row in charsel._inv(w[b])[:3] for v in row))
                          for b in m2b[:smooth])
            return Skeleton(charsel.EULER_ROT, bones, m2b, smooth, len(m2b) - smooth, ib)

        def vb(indices):
            idx = b"".join(struct.pack("<2B", *i) for i in indices)
            pos = b"".join(struct.pack("<3f", 0.0, 1.0, 0.0) for _ in indices)
            return VertexBuffer(len(indices), 2, [Attribute("_p0", (0x18 << 8) | 5, 0, 0),
                                                  Attribute("_i0", (0x09 << 8) | 3, 0, 1)], [12, 2], [pos, idx])

        def shape(name, mat, skin):
            return types.SimpleNamespace(name=name, material=mat, bone=0, vertex_buffer=0, skin_bones=[],
                                         skin_count=skin)

        body_bones = [Bone("Root", -1, -1, 2, 0xFFFF, 0, z, r0, (0.0, 0.0, 0.0)),
                      Bone("Skl_Root", 0, 0, -1, 0xFFFF, 0, z, r0, (0.0, 1.0, 0.0)),
                      Bone("Head", 1, 1, -1, 0xFFFF, 0, z, r0, (0.0, 0.5, 0.0))]
        body = types.SimpleNamespace(name="Link", skeleton=skel(body_bones, [1, 2, 0], 2),
                                     vertex_buffers=[vb([(0, 1), (2, 2)])], shapes=[shape("Skin", 0, 2)],
                                     materials=[types.SimpleNamespace(name="Mt_Skin", textures=["Skin_Alb"])],
                                     total_vertex_count=2)
        hair_bones = [Bone("Root", -1, -1, -1, 0xFFFF, 0, z, r0, (0.0, 0.0, 0.0)),
                      Bone("Head", 0, 0, -1, 0xFFFF, 0, z, r0, (0.0, 1.5, 0.0)),
                      Bone("Hair_1", 1, 1, -1, 0xFFFF, 0, z, r0, (0.0, 0.1, -0.1))]
        hair = types.SimpleNamespace(name="Armor_Default_Head", skeleton=skel(hair_bones, [1, 2], 2),
                                     vertex_buffers=[vb([(0, 1), (1, 1)])], shapes=[shape("Hair", 0, 2)],
                                     materials=[types.SimpleNamespace(name="Mt_Skin", textures=["Hair_Alb"])])
        m, info = charsel.merge_parts(body, [hair], {"Hair_Alb": "SwBody_Hair_Alb"})
        names = [b.name for b in m.skeleton.bones]
        self.assertEqual(names, ["Root", "Skl_Root", "Head", "Hair_1"])
        self.assertEqual(m.skeleton.matrix_to_bone, [1, 2, 3, 0])          # smooth: body's, the new one; then rigid
        self.assertEqual((m.skeleton.smooth_count, m.skeleton.rigid_count), (3, 1))
        self.assertEqual((m.skeleton.bones[3].smooth_index, m.skeleton.bones[0].rigid_index), (2, 3))
        idx = lambda v: list(struct.iter_unpack("<2B", v.buffers[1]))    # noqa: E731
        self.assertEqual(idx(m.vertex_buffers[0]), [(0, 1), (3, 3)])         # (the body's rigid matrix moved up)
        self.assertEqual(idx(m.vertex_buffers[1]), [(1, 2), (2, 2)])         # (Head: the body's; Hair_1: the new one)
        world = charsel._worlds(m.skeleton.bones)
        self.assertLess(charsel._gap(world[3], charsel._worlds(hair_bones)[2]), 1e-6)   # (Hair_1 rests where it did)
        ib = struct.unpack_from("<12f", m.skeleton.inverse_matrices, 48 * 2)
        self.assertAlmostEqual(ib[7], -1.6, places=5)                       # (its bind: its rest, y 1.6)
        self.assertEqual([x.name for x in m.materials], ["Mt_Skin", "SwPart0_Mt_Skin"])
        self.assertEqual(m.materials[1].textures, ["SwBody_Hair_Alb"])
        self.assertEqual((m.shapes[1].material, m.shapes[1].vertex_buffer), (1, 1))
        self.assertEqual((info["new_bones"], info["shapes"], info["left_out"]), (1, 1, []))
        # a part whose bind pose isn't its rest is refused
        bad = types.SimpleNamespace(**dict(vars(hair), skeleton=Skeleton(charsel.EULER_ROT, hair_bones, [1, 2], 2, 0,
                                                                         b"\0" * 96)))
        with self.assertRaises(charsel.CharselError):
            charsel.merge_parts(body, [bad])

    def test_hint_words(self):
        self.assertIn("Restart the game to play as Daruk", charsel.playas_hint("goron"))
        self.assertIn("charsel.py playas daruk", charsel.playas_hint("goron"))
        self.assertIn("iam daruk", charsel.playas_hint("goron"))
        self.assertNotIn("Restart", charsel.playas_hint("bokoblin"))
        self.assertEqual(charsel.playas_hint("link"), "")


@unittest.skipUnless(sys.platform == "win32", "Windows only (a Windows DLL)")
class Selftest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import build_native as bn
        import modmenu
        try:
            tc = bn.find_toolchain(bn.default_install())
        except bn.BuildError as e:
            raise unittest.SkipTest(str(e))
        t = bn.Target("ghost_selftest", (SRC,), runtime=False, out_name="ghost_selftest.dll", export="ghost_selftest")
        built = bn.build(tc, OUT, [t], runner=modmenu.low_priority_runner)
        # (run in a process of its own: the made-up world sits at fixed addresses, and test_ghost_native's copy of
        # the same selftest may hold them in this one)
        import subprocess
        code = ("import ctypes,sys; d=ctypes.CDLL(sys.argv[1]); b=ctypes.create_string_buffer(1<<18); "
                "d.ghost_selftest.argtypes=(ctypes.c_char_p,ctypes.c_int); d.ghost_selftest(b,len(b)); "
                "sys.stdout.write(b.value.decode('utf-8','replace'))")
        out = subprocess.run([sys.executable, "-I", "-c", code, str(built[0])], capture_output=True, text=True,
                             timeout=300)
        cls.rows = {}
        for line in out.stdout.splitlines():
            group, name, status, detail = (line.split(" ", 3) + ["", "", "", ""])[:4]
            cls.rows[(group, name)] = (status, detail)

    def test_puppet(self):
        names = ["move_by_speed", "near_names", "missing_clip_no_link", "ids", "sentences", "spawns_and_swaps",
                 "at_link", "sprint_and_walk", "rehide", "climb_near_name", "horse_shows_link", "cutscene_shows_link",
                 "pause_keeps", "items_to_puppet", "items_grip", "stage_change", "iam_link_back", "self_off",
                 "refused_gives_up", "no_tick_says_why",
                 # 0.9.5 (the round-4 game check): the copies never sheathed by the game, Link's arrow hidden with him,
                 # no tries spent while the game is busy (Game Over) and the costume back after a Continue
                 "holder_in_hand", "arrow_hidden", "busy_no_tries", "gameover_comeback", "refused_stays_failed",
                 # 0.9.6: a cloth pack's physics never moved by the physics call unless asked; a warp counted
                 "physmtx_warp",
                 # 0.9.10: the costume stops with Link and walks as soon as he moves
                 "stop_with_link"]
        if ("puppet", "world") in self.rows:      # (the made-up globals' page was taken: the pure part only)
            names = names[:5]
        # 0.9.3 (pure): held copies per body, kept names, the guard, the hands, death, the ground clamp, the ball's dip
        names += ["grip_place", "items_kept", "guard_detect", "hands_overlay", "death_keeps_costume", "clamp_step",
                  "ball_dip_hold"]
        # 0.9.5 (pure): the back place's tweak and the arm flag, a whole arm, an attack at once, the arrow's name
        names += ["back_tweak", "arm_overlay", "attack_morph", "arrow_name"]
        # 0.9.6 (pure): the bones each body's own physics moves are never written by the pose or the hands; warps
        names += ["cloth_lookup", "cloth_left_alone", "warp_step"]
        # 0.9.10 (pure): Link's own stand is the costume's at once (it walked on ~0.8 s); his "now" speed
        names += ["stand_at_once", "now_speed"]
        # 0.9.1: the costume falls with Link's ragdoll (his pose, on the ground), pure and in the made-up world
        names += ["rd_detect", "rd_order_refuses_loop", "rd_pose", "rd_ground", "rd_lift", "rd_upright", "rd_bodies"]
        if ("puppet", "rd_world") not in self.rows:
            names += ["rd_follow", "rd_getup", "rd_clip_and_off", "rd_removed", "ball_rolls", "clamp_world",
                      "ball_drop_hold"]
        for name in names:
            with self.subTest(check=name):
                status, detail = self.rows.get(("puppet", name), ("MISSING", "not run"))
                self.assertEqual(status, "PASS", detail)

    def test_nothing_failed(self):
        bad = {k: v for k, v in self.rows.items() if v[0] == "FAIL"}
        self.assertEqual(bad, {})


if __name__ == "__main__":
    unittest.main()
