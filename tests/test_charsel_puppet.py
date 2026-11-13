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
        self.assertEqual(sorted(slots), sorted(packs))
        for cid, (actor, name, playas) in slots.items():
            with self.subTest(cid=cid):
                r = charsel.RECIPES[cid]
                self.assertEqual(actor, charsel.SLOTS[cid])
                self.assertEqual(name, r.name.split(" (")[0])
                want = charsel.playas_word(cid) if cid in charsel.PLAYAS_PEOPLE else ""
                self.assertEqual(playas, want)
                if playas:
                    self.assertEqual(charsel.find_id(playas), cid)   # (`charsel playas <word>` takes it)

    def test_aliases_match(self):
        want = {a: r.id for r in charsel.RECIPES.values() if r.kind == "pack" for a in r.aliases}
        self.assertEqual(dll_aliases(), want)

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
        names = ["move_by_speed", "near_names", "unmirrored_shows_link", "ids", "sentences", "spawns_and_swaps",
                 "at_link", "sprint_and_walk", "rehide", "climb_near_name", "horse_shows_link", "cutscene_shows_link",
                 "pause_keeps", "items_to_puppet", "stage_change", "iam_link_back", "self_off", "refused_gives_up",
                 "no_tick_says_why"]
        if ("puppet", "world") in self.rows:      # (the made-up globals' page was taken: the pure part only)
            names = names[:5]
        # 0.9.1: the costume falls with Link's ragdoll (his pose, on the ground), pure and in the made-up world
        names += ["rd_detect", "rd_order_refuses_loop", "rd_pose", "rd_ground", "rd_lift", "rd_upright", "rd_bodies"]
        if ("puppet", "rd_world") not in self.rows:
            names += ["rd_follow", "rd_getup", "rd_clip_and_off", "rd_removed"]
        for name in names:
            with self.subTest(check=name):
                status, detail = self.rows.get(("puppet", name), ("MISSING", "not run"))
                self.assertEqual(status, "PASS", detail)

    def test_nothing_failed(self):
        bad = {k: v for k, v in self.rows.items() if v[0] == "FAIL"}
        self.assertEqual(bad, {})


if __name__ == "__main__":
    unittest.main()
