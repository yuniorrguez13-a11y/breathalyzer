"""The costume looks of 2026-11-15 (tools/charsel.py), with no game files:

(The Linkle costume v3 is gone, 2026-11-18: picking Linkle turns The Linkle Mod on as Link's own body.)
- Skin with an NPC's material: the player's skin materials take Zelda's, pointed at their own textures by role, and
  their shapes get a second texture coordinate.
- Zelda's legs: a donor's leg skin moved onto her leg bones (stretched to her lengths), feet left out, helper bones
  counted as their parents; her model list repointed at her own copy.
"""

import struct
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import charsel  # noqa: E402

NS = types.SimpleNamespace
Z, R0 = (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0)


HIDE_GROUP = "HideMaterialGroup"   # (a material's hide groups, as the game's user data names them)


def ud(*groups):
    return [NS(name=HIDE_GROUP, values=tuple(groups))] if groups else []


def mat(name, *groups, textures=(), samplers=(), hint="npc"):
    return NS(name=name, user_data=ud(*groups), textures=list(textures),
              samplers=[NS(name=s) for s in samplers], render_infos=[NS(name="gsys_priority_hint", values=(hint,))])


class NpcSkin(unittest.TestCase):
    def test_roles_and_uv1(self):
        from wiiu_model.neutral import Attribute, VertexBuffer
        tmpl = mat("Mt_Skin", samplers=list(charsel.NPC_SKIN_ROLES), textures=["Z"] * 5)
        uv = struct.pack("<2H", 1, 2) + struct.pack("<2H", 3, 4)
        vb = VertexBuffer(2, 1, [Attribute("_p0", 0x1805, 0, 0), Attribute("_u0", 0x1201, 12, 0)], [16],
                          [bytes(12) + uv[:4] + bytes(12) + uv[4:]])
        skin = mat("Mt_Upper_Skin", "G_Upper", hint="player", samplers=("_a0", "_n0", "_s0", "_ao0", "_gn0", "_gn1"),
                   textures=("Skin_Alb", "Skin_Nrm", "Skin_Spm", "Skin_AO", "Skin_Damage", "Skin_Red"))
        face = mat("Mt_Face", hint="player", samplers=("_a0", "_n0", "_s0"), textures=("Head_Alb", "Head_Nrm", "H_Spm"))
        hair = mat("Mt_Hair", samplers=("_a0",), textures=("Hair_Alb",))
        m = NS(materials=[skin, face, hair], shapes=[NS(material=0, vertex_buffer=0)], vertex_buffers=[vb])
        done = charsel.npc_skin_materials(m, tmpl)
        self.assertEqual(done, ["Mt_Upper_Skin", "Mt_Face"])                         # (the hair is an NPC's already)
        self.assertEqual(m.materials[0].textures, ["Skin_Alb", "Skin_Damage", "Skin_Nrm", "Skin_Spm", "Skin_AO"])
        self.assertEqual(m.materials[1].textures, ["Head_Alb", charsel.FLAT_DAMAGE, "Head_Nrm", "H_Spm",
                                                   charsel.FLAT_AO])
        self.assertEqual((m.materials[0].name, [u.values for u in m.materials[0].user_data]),
                         ("Mt_Upper_Skin", [("G_Upper",)]))                           # (its name and groups kept)
        self.assertIs(m.materials[2], hair)
        self.assertEqual([a.name for a in vb.attributes], ["_p0", "_u0", "_u1"])
        self.assertEqual(vb.buffers[1], uv)                                           # (_u1 = _u0)
        self.assertFalse(charsel.ensure_uv1(vb))                                      # (once)

    def test_flat_textures(self):
        bc1 = charsel.flat_texture(NS(format=0x1a06, extra={}, name="", width=0, height=0, levels=[]), "W", 255)
        self.assertEqual((bc1.width, bc1.levels), (4, [struct.pack("<HHI", 0xFFFF, 0xFFFF, 0)]))
        bc4 = charsel.flat_texture(NS(format=0x1d01, extra={}, name="", width=0, height=0, levels=[]), "A", 200)
        self.assertEqual(bc4.levels, [bytes((200, 200)) + bytes(6)])
        with self.assertRaises(charsel.CharselError):
            charsel.flat_texture(NS(format=0x1e01, extra={}), "N", 1)


class ZeldaLegs(unittest.TestCase):
    def skel(self, rows):
        from wiiu_model.neutral import Bone, Skeleton
        bones = [Bone(n, p, -1, -1, 0xFFFF, 0, Z, R0, t) for n, p, t in rows]
        smooth = list(range(len(bones)))
        for i, b in enumerate(bones):
            b.smooth_index = i
        w = charsel._worlds(bones)
        ib = b"".join(struct.pack("<12f", *(v for row in charsel._inv(w[b])[:3] for v in row)) for b in smooth)
        return Skeleton(charsel.EULER_ROT, bones, smooth, len(smooth), 0, ib)

    def test_fit_legs(self):
        from wiiu_model.neutral import Attribute, Mesh, VertexBuffer
        # donor: knee at 0.5, ankle at 0.1, a knee helper under Leg_1; base: knee at 0.6 (a longer shin)
        donor_sk = self.skel([("Root", -1, (0, 0, 0)), ("Leg_1_L", 0, (0.1, 0.9, 0)), ("Knee_L", 1, (0, -0.4, 0)),
                              ("Leg_2_L", 1, (0, -0.4, 0)), ("Ankle_L", 3, (0, -0.4, 0)), ("Toe_L", 4, (0, -0.1, 0.1))])
        base_sk = self.skel([("Root", -1, (0, 0, 0)), ("Leg_1_L", 0, (0.1, 1.0, 0)), ("Leg_2_L", 1, (0, -0.4, 0)),
                             ("Ankle_L", 2, (0, -0.5, 0)), ("Toe_L", 3, (0, -0.1, 0.1))])

        def vert(p, bone):
            return struct.pack("<3f", *p) + struct.pack("<I", (511 << 20) | (1 << 30)) + struct.pack("<2H", 0, 0) \
                + bytes((bone, 0, 0, 0)) + bytes((255, 0, 0, 0))
        verts = [((0.1, 0.3, 0.05), 3), ((0.12, 0.3, 0.05), 3), ((0.1, 0.32, 0.05), 3),      # mid-shin
                 ((0.1, 0.55, 0.05), 2), ((0.12, 0.55, 0.05), 2), ((0.1, 0.57, 0.05), 2),     # on the knee helper
                 ((0.1, 0.0, 0.1), 5), ((0.12, 0.0, 0.1), 5), ((0.1, 0.02, 0.1), 3)]          # a toe triangle
        raw = b"".join(vert(p, b) for p, b in verts)
        attrs = [Attribute("_p0", 0x1805, 0, 0), Attribute("_n0", 0x0E02, 12, 0), Attribute("_u0", 0x1201, 16, 0),
                 Attribute("_i0", 0x0B03, 20, 0), Attribute("_w0", 0x0B01, 24, 0)]
        vb = VertexBuffer(len(verts), 1, attrs, [28], [raw])
        mesh = Mesh("triangles", "u16", 9, struct.pack("<9H", *range(9)), 0, [(0, 9)])
        donor = NS(name="Donor", skeleton=donor_sk, materials=[NS(name="Mt_Lower_Skin")], vertex_buffers=[vb],
                   shapes=[NS(material=0, vertex_buffer=0, skin_count=1, meshes=[mesh])])
        base = NS(skeleton=base_sk)
        pos, nrm, uv, infl, tris, info = charsel.fit_legs(donor, base)
        self.assertEqual(info["triangles"], 2)                       # (the toe triangle left out)
        self.assertAlmostEqual(info["stretch"]["Leg_2_L"], 1.25)       # (shin 0.4 -> 0.5)
        self.assertAlmostEqual(pos[0][1], 0.6 - 0.2 * 1.25, places=5)  # half way down her longer shin
        self.assertAlmostEqual(pos[0][2], 0.05, places=5)              # (across the bone: as it was)
        self.assertAlmostEqual(pos[3][1], 0.65, places=5)              # (knee helper -> Leg_1: 5 cm above her knee)
        self.assertEqual(infl[0], ([3 - 1, 2, 2, 2], [255, 0, 0, 0]))  # base's Leg_2_L matrix (index 2)
        self.assertEqual(infl[3][0][0], 1)                             # base's Leg_1_L matrix
        self.assertAlmostEqual(nrm[0][2], 1.0, places=2)

    def test_repoint_model_list(self):
        import aamp
        from aamp import STRING64, PList, PObject, name_hash
        base, unit = PObject(), PObject()
        base.set("Folder", STRING64, "Npc_Zelda_Miko")
        unit.set("UnitName", STRING64, "Npc_Zelda_Miko")
        unit.set("BindBone", STRING64, "")
        md0 = PList(lists={name_hash("Unit"): PList(objects={name_hash("Unit_0"): unit})},
                    objects={name_hash("Base"): base})
        root = PList(lists={name_hash("ModelData"): PList(lists={name_hash("ModelData_0"): md0})})
        data, n = charsel.repoint_model_list(aamp.dump(root), "Npc_Zelda_Miko", "SwChar_zelda")
        self.assertEqual(n, 2)
        md = aamp.load(data).list("ModelData").list("ModelData_0")
        self.assertEqual(md.object("Base").get_value("Folder"), "SwChar_zelda")
        self.assertEqual(md.list("Unit").object("Unit_0").get_value("UnitName"), "SwChar_zelda")
        self.assertEqual(charsel.repoint_model_list(data, "Other", "X")[1], 0)


if __name__ == "__main__":
    unittest.main()
