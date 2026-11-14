"""Play as a character at the next start (tools/charsel.py playas, the restart fallback): made-up data, no game.

- rerig: a made-up body put on a made-up "Link" skeleton: Link's bones first, in his order, with his parents; every
  body bone keeps its rest pose in model space (also one whose parent changes); skin matrices keep their bone names;
  Link-only bones get Link's rest; only Euler skeletons; a name twice is refused.
- aamp_repoint: an animation set's clip name points at the character's clip, every other byte stays.
- aslist_add: the character's animation file is added after Link's (retargeted), Link's own kept.
- player_moves: Daruk's sprint (RunGear2 -> Common_Move_Ball) goes to the player's Player_Move set; a clip missing
  from the files stays Link's, with a note.
- the people only (monsters and Link are refused before any file is read); the choice file round trip; playas on and
  off against a made-up library: added last and on, The Linkle Mod turned off and back on, said plainly.
- the wheel's P (and a controller's Y): the build runs and says "Restart the game to play as Daruk".
"""
import json
import math
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import aamp  # noqa: E402
import charsel  # noqa: E402
from wiiu_model.neutral import Bone, Skeleton  # noqa: E402

EULER = 0x1000


def bone(name, parent, rot=(0.0, 0.0, 0.0), trans=(0.0, 0.0, 0.0)):
    return Bone(name, parent, -1, -1, 0xFFFF, 0, (1.0, 1.0, 1.0), (*rot, 1.0), trans)


def link_skeleton() -> Skeleton:
    bones = [bone("Root", -1), bone("Skl_Root", 0, (0, 0, math.pi / 2), (0, 0.99, 0)),
             bone("Spine_1", 1, (0.1, 0, 0), (0.1, 0, 0)), bone("Pod_B", 1, (0, 0.2, 0), (0, 0.1, 0.1)),
             bone("Weapon_R", 2, (0, 0, 0.3), (0.2, 0, 0)), bone("Belt_A_Havok", 1, (0, 0, 0), (0, -0.1, 0))]
    return Skeleton(EULER, bones, [1, 2], 2, 0, b"\0" * 48 * 2)


def body_skeleton() -> Skeleton:
    # Daruk-like: hips higher, Pod_B under the spine (another parent than Link's), two bones of its own
    bones = [bone("Root", -1), bone("Skl_Root", 0, (0, 0, math.pi / 2), (0, 1.07, 0)),
             bone("Spine_1", 1, (0.1, 0, 0), (0.15, 0, 0)), bone("Pod_B", 2, (0.3, 0, 0), (0.05, 0.2, 0)),
             bone("Weapon_R", 2, (0, 0, 0.3), (0.3, 0, 0)), bone("Goron_Back", 2, (0, 0.5, 0), (0.1, 0.1, 0)),
             bone("Goron_Hair", 5, (0.2, 0, 0), (0.05, 0, 0))]
    return Skeleton(EULER, bones, [1, 2, 3, 5, 6], 4, 1, b"\1" * 48 * 4)


def worlds(bones):
    return charsel._worlds(bones)


class Rerig(unittest.TestCase):
    def test_link_first_body_kept(self):
        link, body = link_skeleton(), body_skeleton()
        rig = charsel.rerig(link, body)
        names = [b.name for b in rig["bones"]]
        lnames = [b.name for b in link.bones]
        self.assertEqual(names[:len(lnames)], lnames)                      # Link's bones first, in his order
        self.assertEqual(names[len(lnames):], ["Goron_Back", "Goron_Hair"])
        for i, lb in enumerate(link.bones):                                # with Link's parents
            nb = rig["bones"][i]
            self.assertEqual(names[nb.parent] if nb.parent >= 0 else None,
                             lnames[lb.parent] if lb.parent >= 0 else None)
        self.assertEqual(rig["reparented"], ["Pod_B"])
        self.assertEqual(rig["link_only"], ["Belt_A_Havok"])
        bw, nw = worlds(body.bones), worlds(rig["bones"])                  # every body bone keeps its rest pose
        for i, b in enumerate(body.bones):
            self.assertLess(charsel._gap(nw[names.index(b.name)], bw[i]), charsel.REST_TOL, b.name)
        bnames = [b.name for b in body.bones]                              # skin matrices keep their bone names
        self.assertEqual([names[k] for k in rig["matrix_to_bone"]], [bnames[k] for k in body.matrix_to_bone])
        smooth = {i for i, b in enumerate(rig["bones"]) if b.smooth_index >= 0}
        rigid = [b.rigid_index for b in rig["bones"] if b.rigid_index >= 0]
        self.assertEqual(len(smooth), 4)
        self.assertEqual(rigid, [4])
        belt = rig["bones"][names.index("Belt_A_Havok")]                   # a Link-only bone: Link's rest
        self.assertEqual(belt.translation, link.bones[5].translation)

    def test_refusals(self):
        link, body = link_skeleton(), body_skeleton()
        quat = Skeleton(0, body.bones, body.matrix_to_bone, 4, 1, body.inverse_matrices)
        with self.assertRaises(charsel.CharselError):
            charsel.rerig(link, quat)
        twice = Skeleton(EULER, body.bones + [bone("Spine_1", 0)], body.matrix_to_bone, 4, 1, body.inverse_matrices)
        with self.assertRaises(charsel.CharselError):
            charsel.rerig(link, twice)


def made_up_as() -> bytes:
    root = aamp.PList()
    elem = aamp.PObject()
    elem.set("TypeIndex", aamp.INT, 0)
    elem.set("FileName", aamp.STRING64, "Nml_Move_Dash")
    other = aamp.PObject()
    other.set("FileName", aamp.STRING64, "Nml_Move_Walk")
    lst = aamp.PList()
    lst.objects[aamp.name_hash("Parameters")] = elem
    lst.objects[aamp.name_hash("Other")] = other
    root.lists[aamp.name_hash("Elements")] = lst
    return aamp.dump(root)


class AnimationFiles(unittest.TestCase):
    def test_repoint(self):
        old = made_up_as()
        new, n = charsel.aamp_repoint(old, {"Nml_Move_Dash": "Common_Move_Ball"})
        self.assertEqual(n, 1)
        root = aamp.load(new)
        objs = root.list("Elements").objects
        self.assertEqual(objs[aamp.name_hash("Parameters")].get_value("FileName"), "Common_Move_Ball")
        self.assertEqual(objs[aamp.name_hash("Other")].get_value("FileName"), "Nml_Move_Walk")
        same, n0 = charsel.aamp_repoint(old, {"Not_There": "X"})
        self.assertEqual((same, n0), (old, 0))
        with self.assertRaises(charsel.CharselError):
            charsel.aamp_repoint(b"NOPE" + bytes(60), {})

    def test_aslist_add(self):
        root = aamp.PList()
        add = aamp.PList()
        o = aamp.PObject()
        o.set("Anim", aamp.STRING64, charsel.PLAYER_ANIM)
        add.objects[aamp.name_hash("AddRes_0")] = o
        root.lists[aamp.name_hash("AddReses")] = add
        out = aamp.load(charsel.aslist_add(aamp.dump(root), [("UMii_Goron_BodyC_M_Animation", True)], True))
        adds = {x.get_value("Anim"): x.get_value("RetargetModel") for x in out.list("AddReses").objects.values()}
        self.assertEqual(adds, {charsel.PLAYER_ANIM: "Skeleton", "UMii_Goron_BodyC_M_Animation": "Skeleton"})
        again = aamp.load(charsel.aslist_add(aamp.dump(out), [("UMii_Goron_BodyC_M_Animation", True)]))
        self.assertEqual(len(again.list("AddReses").objects), 2)          # (no second copy)

    def test_player_moves_daruk(self):
        r = charsel.RECIPES["goron"]
        clips = {"UMii_Goron_BodyC_M_Animation": ["Common_Move_Ball", "Default_Wait"]}
        edits, where, notes = charsel.player_moves(r, clips.get)
        self.assertEqual(edits, {"Actor/AS/Player_Move.bas": {"Nml_Move_Dash": "Common_Move_Ball"}})
        self.assertEqual(where, {"Common_Move_Ball": ("UMii_Goron_BodyC_M_Animation", True)})
        self.assertEqual(notes, [])
        edits, where, notes = charsel.player_moves(r, {}.get)              # (not in the files: Link's sprint stays)
        self.assertEqual((edits, where), ({}, {}))
        self.assertIn("Link's move stays", notes[0])

    def test_player_moves_mipha_swims(self):
        r = charsel.RECIPES["zora"]
        # (a Zora WOMAN's swim since v4: UMii_Zora_BodyC_W, not the men's file)
        self.assertIn(("UMii_Zora_BodyC_W_Animation", True), r.anims)
        self.assertNotIn("UMii_Zora_BodyC_M_Animation", dict(r.anims))
        clips = {"UMii_Zora_BodyC_W_Animation": ["Common_Swim_Wait", "Common_Move_Swim_Walk", "Common_Move_Swim_Run"]}
        edits, where, _ = charsel.player_moves(r, clips.get)
        self.assertEqual(edits["Actor/AS/Player_SwimMove.bas"],
                         {"Swim_Wait": "Common_Swim_Wait", "Swim_Move_Slow_F": "Common_Move_Swim_Walk",
                          "Swim_Move_Fast_F": "Common_Move_Swim_Run"})


class Choice(unittest.TestCase):
    def test_people_only(self):
        for cid in ("bokoblin", "moblin", "link", "linkle"):
            with self.subTest(cid=cid), self.assertRaises(charsel.CharselError):
                charsel.body_of(Path("Z:/no/romfs"), charsel.RECIPES[cid])   # (refused before any file is read)
        self.assertEqual(set(charsel.PLAYAS_PEOPLE),
                         {r.id for r in charsel.RECIPES.values() if r.kind == "pack" and r.cat != "Monsters"})

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "charsel.json"
            path.write_text(json.dumps({"format": 1, "look": "goron"}), encoding="utf-8")
            self.assertEqual(charsel.load_playas(path), {})
            charsel.save_playas({"id": "goron", "library": "L", "turned_off": ["the-linkle-mod"]}, path)
            self.assertEqual(charsel.load_playas(path), {"id": "goron", "library": "L",
                                                         "turned_off": ["the-linkle-mod"]})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["look"], "goron")   # (the pick stays)
            charsel.save_playas(None, path)
            self.assertEqual(charsel.load_playas(path), {})
            path.write_text(json.dumps({"playas": {"id": "ganon", "library": "L"}}), encoding="utf-8")
            self.assertEqual(charsel.load_playas(path), {})                # (not a character: nothing)


class FakeLib:
    def __init__(self, root: Path):
        self.root, self.mods_dir = str(root), str(root / "mods")
        self.mods = [{"id": "the-linkle-mod", "name": "The Linkle Mod", "enabled": True},
                     {"id": "botw-mod-loader.ghost", "name": "Ghost", "enabled": True}]
        (root / "mods" / "the-linkle-mod" / "romfs" / "Pack").mkdir(parents=True)   # (Linkle: a body for Link)
        self.saves = 0

    def save(self):
        self.saves += 1


class Library(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lib = FakeLib(self.root)
        self.state = self.root / "charsel.json"
        lib = self.lib

        def add_mod(lib_, src, *a, **k):
            lib_.mods.append({"id": charsel.PLAYAS_ID, "name": "Play as Daruk (Who am I)", "enabled": False})
            return lib_.mods[-1], None, []

        def remove_mod(lib_, mid):
            lib_.mods[:] = [m for m in lib_.mods if m["id"] != mid]

        self.mm = types.SimpleNamespace(add_mod=add_mod, remove_mod=remove_mod, ModmanError=RuntimeError)
        self.patches = [mock.patch.dict(sys.modules, {"modman": self.mm}),
                        mock.patch.object(charsel, "_open_library", lambda library: lib),
                        mock.patch.object(charsel, "build_playas", lambda cid, *a, **k: {
                            "id": cid, "name": charsel.RECIPES[cid].name, "own_moves": {"RunGear2": "Common_Move_Ball"},
                            "notes": []}),
                        mock.patch.object(charsel, "check_playas", lambda *a, **k: []),
                        mock.patch.object(charsel, "_link_model_in",
                                          lambda folder: ("x", "y") if Path(folder).name == "the-linkle-mod" else None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_on_then_off(self):
        info = charsel.playas_on("daruk", library=self.root, state=self.state, say=lambda s: None)
        self.assertTrue(info["added"])
        self.assertEqual(info["turned_off"], ["The Linkle Mod"])
        self.assertEqual(self.lib.mods[-1]["id"], charsel.PLAYAS_ID)          # last (it holds Link's model), on
        self.assertTrue(self.lib.mods[-1]["enabled"])
        self.assertFalse(self.lib.mods[0]["enabled"])                          # Linkle off while it is on
        self.assertTrue(self.lib.mods[1]["enabled"])                           # (the others untouched)
        self.assertEqual(charsel.load_playas(self.state)["turned_off"], ["the-linkle-mod"])
        again = charsel.playas_on("zelda", library=self.root, state=self.state, say=lambda s: None)
        self.assertEqual(sum(m["id"] == charsel.PLAYAS_ID for m in self.lib.mods), 1)   # (replaced, not twice)
        self.assertEqual(again["turned_off"], ["The Linkle Mod"])                # (still off: said again)
        self.assertEqual(charsel.load_playas(self.state)["turned_off"], ["the-linkle-mod"])   # (still remembered)
        res = charsel.playas_off(library=self.root, state=self.state)
        self.assertEqual(res, {"removed": True, "turned_on": ["The Linkle Mod"]})
        self.assertTrue(self.lib.mods[0]["enabled"])
        self.assertFalse(any(m["id"] == charsel.PLAYAS_ID for m in self.lib.mods))
        self.assertEqual(charsel.load_playas(self.state), {})

    def test_command_words(self):
        out = []
        with mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))):
            code = charsel.main(["playas", "daruk", "--library", str(self.root), "--state", str(self.state)])
        text = "\n".join(out)
        self.assertEqual(code, 0)
        self.assertIn("Restart the game to play as Daruk", text)
        self.assertIn("The Linkle Mod is off while you play as Daruk", text)
        self.assertIn("Armor you wear shows on top", text)
        out.clear()
        with mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))):
            code = charsel.main(["playas", "off", "--library", str(self.root), "--state", str(self.state)])
        self.assertEqual(code, 0)
        self.assertIn("Back to Link at the next start", "\n".join(out))
        self.assertIn("The Linkle Mod: on again", "\n".join(out))
        with mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))):
            self.assertEqual(charsel.main(["playas", "ganon", "--state", str(self.state)]), 1)

    def test_monster_refused(self):
        with self.assertRaises(charsel.CharselError):
            charsel.playas_on("link", library=self.root, state=self.state)


class WheelOffer(unittest.TestCase):
    def test_p_builds_and_says_restart(self):
        w = charsel.Wheel("goron")
        self.assertIn("P: restart as Daruk", w.lines()[-1])
        self.assertIn("You see yourself as Daruk too", w.lines()[3])
        w.playas_run = lambda cid, say=None: {"turned_off": ["The Linkle Mod"]}
        w.start_playas("goron", wait=True)
        self.assertIn("Restart the game to play as Daruk", w.playas_note)
        self.assertIn("The Linkle Mod is off", w.playas_note)
        self.assertIn(w.playas_note, w.lines()[-2])
        self.assertFalse(w.playas_busy)

    def test_p_key_and_failures(self):
        w = charsel.Wheel("goron")

        def fails(cid, say=None):
            raise charsel.CharselError("your TitleBG.pack has no Model/Link.sbfres")
        w.playas_run = fails
        self.assertIsNone(w.key("playas"))                                    # (stays on the wheel)
        import threading
        for t in threading.enumerate():
            if t.name == "charsel-playas":
                t.join(5)
        self.assertIn("Play as Daruk: your TitleBG.pack", w.playas_note)
        b = charsel.Wheel("bokoblin")
        b.start_playas("bokoblin", wait=True)
        self.assertIn("can't be played as at a restart", b.playas_note)
        self.assertNotIn("P: restart", b.lines()[-1])
        self.assertEqual(charsel.TK_KEYS["p"], "playas")
        self.assertIn((charsel.XI_Y, "playas"), charsel.PAD_KEYS)


if __name__ == "__main__":
    unittest.main()
