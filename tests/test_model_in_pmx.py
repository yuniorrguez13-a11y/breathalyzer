"""tools/model_in/pmx.py: the PMX 2.0/2.1 (MikuMikuDance) reader, on tiny PMX files built here (no real model).

- the main model: two bones (UTF-16 Japanese + English names), a quad, one material with a texture and a shared
  toon, a vertex morph and a bone morph, comments with terms, display frame + rigid body + joint at the end
- every field checked: metres (x 0.08), Z flipped, winding reversed, bone rest = position - parent position,
  inverse bind = minus the world position, BDEF1/BDEF2/BDEF4/SDEF weights, the licence text in meta
- UTF-8 + 4-byte indices, PMX 2.1 QDEF, IK chains (limits mirrored), plain-words errors, a cut-short physics tail
"""

import math
import struct
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from model_in import pmx  # noqa: E402

S = pmx.MMD_UNIT_M


# ---------------------------------------------------------------------------------------------------------------
# a small PMX writer, for the tests only


class W:
    def __init__(self, enc=0, sizes=(1, 1, 1, 2, 1, 1), add_uv=0, version=2.0):
        self.enc, self.sizes, self.add_uv, self.version = enc, sizes, add_uv, version
        self.vsize, self.tsize, self.msize, self.bsize, self.morsize, self.rsize = sizes

    def text(self, s):
        b = s.encode("utf-16-le" if self.enc == 0 else "utf-8")
        return struct.pack("<i", len(b)) + b

    @staticmethod
    def idx(i, size):
        return struct.pack("<" + {1: "b", 2: "h", 4: "i"}[size], i)

    def vidx(self, i):
        return struct.pack("<" + {1: "B", 2: "H", 4: "i"}[self.vsize], i)

    def header(self, name, name_en, comment, comment_en):
        return (b"PMX " + struct.pack("<fB", self.version, 8) + bytes([self.enc, self.add_uv, *self.sizes])
                + self.text(name) + self.text(name_en) + self.text(comment) + self.text(comment_en))

    def vertex(self, pos, nrm, uv, kind, bones, ws, extra=(), sdef=None):
        b = struct.pack("<8f", *pos, *nrm, *uv)
        for e in extra:
            b += struct.pack("<4f", *e)
        b += bytes([kind])
        if kind == 0:
            b += self.idx(bones[0], self.bsize)
        elif kind in (1, 3):
            b += self.idx(bones[0], self.bsize) + self.idx(bones[1], self.bsize) + struct.pack("<f", ws[0])
            if kind == 3:
                b += struct.pack("<9f", *sdef)
        else:
            b += b"".join(self.idx(x, self.bsize) for x in bones) + struct.pack("<4f", *ws)
        return b + struct.pack("<f", 1.0)

    def material(self, name, name_en, diffuse, flags, tex, count, sphere=-1, sphere_mode=0, toon=("shared", 3),
                 memo=""):
        b = self.text(name) + self.text(name_en) + struct.pack("<4f3ff3f", *diffuse, 0.1, 0.2, 0.3, 5.0,
                                                               0.4, 0.5, 0.6)
        b += bytes([flags]) + struct.pack("<4ff", 0, 0, 0, 1, 1.0)
        b += self.idx(tex, self.tsize) + self.idx(sphere, self.tsize) + bytes([sphere_mode])
        if toon[0] == "shared":
            b += bytes([1, toon[1]])
        else:
            b += bytes([0]) + self.idx(toon[1], self.tsize)
        return b + self.text(memo) + struct.pack("<i", count)

    def bone(self, name, name_en, pos, parent, flags=0x001E, tail=(0, 1, 0), ik=None, inherit=None,
             fixed_axis=None, local_axes=None, external=None):
        if ik:
            flags |= 0x0020
        if inherit:
            flags |= 0x0100
        if fixed_axis:
            flags |= 0x0400
        if local_axes:
            flags |= 0x0800
        if external is not None:
            flags |= 0x2000
        b = self.text(name) + self.text(name_en) + struct.pack("<3f", *pos) + self.idx(parent, self.bsize)
        b += struct.pack("<iH", 0, flags)
        b += self.idx(tail, self.bsize) if flags & 1 else struct.pack("<3f", *tail)
        if inherit:
            b += self.idx(inherit[0], self.bsize) + struct.pack("<f", inherit[1])
        if fixed_axis:
            b += struct.pack("<3f", *fixed_axis)
        if local_axes:
            b += struct.pack("<6f", *local_axes[0], *local_axes[1])
        if external is not None:
            b += struct.pack("<i", external)
        if ik:
            target, loops, limit, links = ik
            b += self.idx(target, self.bsize) + struct.pack("<if", loops, limit) + struct.pack("<i", len(links))
            for link in links:
                b += self.idx(link[0], self.bsize)
                if len(link) > 1:
                    b += bytes([1]) + struct.pack("<6f", *link[1], *link[2])
                else:
                    b += bytes([0])
        return b

    def morph_vertex(self, name, name_en, panel, offsets):
        b = self.text(name) + self.text(name_en) + bytes([panel, 1]) + struct.pack("<i", len(offsets))
        for v, d in offsets:
            b += self.vidx(v) + struct.pack("<3f", *d)
        return b

    def morph_raw(self, name, kind, items):
        """A morph whose offsets are given as ready bytes (one per offset)."""
        return self.text(name) + self.text(name) + bytes([4, kind]) + struct.pack("<i", len(items)) + b"".join(items)

    def morph_bone(self, name, name_en, panel, offsets):
        b = self.text(name) + self.text(name_en) + bytes([panel, 2]) + struct.pack("<i", len(offsets))
        for bi, t, q in offsets:
            b += self.idx(bi, self.bsize) + struct.pack("<3f4f", *t, *q)
        return b

    def tail(self, frames=True, rigid=True, joint=True):
        b = b""
        if frames:
            b += struct.pack("<i", 1) + self.text("Root") + self.text("Root") + bytes([1]) + struct.pack("<i", 1)
            b += bytes([0]) + self.idx(0, self.bsize)
        else:
            b += struct.pack("<i", 0)
        if rigid:
            b += struct.pack("<i", 1) + self.text("剛体") + self.text("body") + self.idx(0, self.bsize)
            b += struct.pack("<BHB3f3f3f5fB", 0, 0xFFFF, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1, .5, .5, 0, .5, 0)
        else:
            b += struct.pack("<i", 0)
        if joint:
            b += struct.pack("<i", 1) + self.text("J") + self.text("J") + bytes([0])
            b += self.idx(0, self.rsize) + self.idx(0, self.rsize) + struct.pack("<24f", *([0.0] * 24))
        else:
            b += struct.pack("<i", 0)
        return b


def count(n):
    return struct.pack("<i", n)


SDEF_C = (0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0)


def main_model(w=None, tail=True):
    """Two bones, a quad (4 vertices, 2 triangles), one material, a vertex morph and a bone morph."""
    w = w or W()
    b = w.header("テスト", "Test", "改変OK・再配布禁止", "Edits OK, no redistribution")
    b += count(4)
    b += w.vertex((-1, 0, 0), (0, 0, -1), (0, 1), 0, (0,), (1.0,))
    b += w.vertex((1, 0, 0), (0, 0, -1), (1, 1), 1, (0, 1), (0.25,))
    b += w.vertex((1, 2, 3), (0, 0, -1), (1, 0), 2, (0, 1, -1, -1), (2.0, 2.0, 0.0, 0.0))
    b += w.vertex((-1, 2, 0), (0, 0, -1), (0, 0), 3, (1, 0), (0.75,), sdef=SDEF_C)
    b += count(6) + b"".join(w.vidx(i) for i in (0, 3, 2, 0, 2, 1))  # clockwise seen from -Z (MMD front)
    b += count(1) + w.text("tex\\肌.png")
    b += count(1) + w.material("肌", "skin", (1.0, 0.5, 0.25, 1.0), 0x01 | 0x10, 0, 6, memo="メモ")
    b += count(2)
    b += w.bone("センター", "center", (0, 0, 0), -1)
    b += w.bone("頭", "head", (0, 10, 2), 0)
    b += count(2)
    b += w.morph_vertex("あ", "a", 3, [(2, (0, 1, 1))])
    b += w.morph_bone("うなずき", "nod", 4, [(1, (0, 0, 1), (0.1, 0.2, 0.3, 0.9))])
    if tail:
        b += w.tail()
    return b


def close(a, b, eps=1e-5):
    return all(abs(x - y) <= eps for x, y in zip(a, b)) and len(a) == len(b)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


class MainModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sc = pmx.read_bytes(main_model())

    def test_valid(self):
        self.assertEqual(self.sc.validate(), [])
        self.assertEqual(self.sc.summary()["format"], "pmx")

    def test_meta_and_licence(self):
        meta = self.sc.meta
        self.assertEqual(meta["source_format"], "pmx")
        self.assertEqual((meta["units"], meta["up"], meta["forward"]), ("m", "Y", "+Z"))
        self.assertEqual(meta["version"], 2.0)
        self.assertEqual(meta["encoding"], "UTF-16LE")
        self.assertEqual((meta["model_name"], meta["model_name_en"]), ("テスト", "Test"))
        self.assertEqual(meta["licence_text"], "改変OK・再配布禁止\n\nEdits OK, no redistribution")
        self.assertIsNone(meta["licence"])  # PMX carries no licence fields
        self.assertEqual(meta["share"], "private")
        self.assertEqual(meta["scale"], 0.08)
        self.assertIn("8 cm", meta["source_units"])
        self.assertIn("left-handed", meta["source_axes"])
        notes = " ".join(meta["notes"])
        for bit in ("8 cm", "licence_text", "1 SDEF vertex read as BDEF2", "2 morphs kept",
                    "1 display frame, 1 rigid body, 1 physics joint", "toon"):
            self.assertIn(bit, notes)

    def test_bones(self):
        bones = self.sc.skeleton.bones
        self.assertEqual([b.name for b in bones], ["センター", "頭"])
        self.assertEqual([b.extras["name_en"] for b in bones], ["center", "head"])
        self.assertEqual([b.parent for b in bones], [-1, 0])
        self.assertTrue(close(bones[0].translation, (0, 0, 0)))
        self.assertTrue(close(bones[1].translation, (0, 10 * S, -2 * S)))  # metres, Z flipped
        for b in bones:
            self.assertEqual(b.rotation, (0.0, 0.0, 0.0, 1.0))
            self.assertEqual(b.scale, (1.0, 1.0, 1.0))
        ib = bones[1].inverse_bind
        self.assertTrue(close(ib, (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -10 * S, 2 * S, 1)))
        # bind = rest: world rest * inverse bind = identity
        from model_in.scene import IDENTITY, mat_close, mat_mul
        for wm, b in zip(self.sc.skeleton.world_matrices(), bones):
            self.assertTrue(mat_close(mat_mul(wm, b.inverse_bind), IDENTITY))
        self.assertTrue(close(bones[1].extras["tail_offset"], (0, S, 0)))
        self.assertEqual(bones[1].extras["pmx_index"], 1)
        self.assertEqual(self.sc.meta["ik"], [])

    def test_mesh(self):
        self.assertEqual(len(self.sc.meshes), 1)
        m = self.sc.meshes[0]
        self.assertEqual((m.name, m.material), ("肌", 0))
        want = [(-S, 0, 0), (S, 0, 0), (S, 2 * S, -3 * S), (-S, 2 * S, 0)]
        for p, q in zip(m.positions, want):
            self.assertTrue(close(p, q), (p, q))
        for n in m.normals:
            self.assertTrue(close(n, (0, 0, 1)))  # faced -Z in MMD, +Z now
        self.assertEqual(m.uvs, [[(0, 1), (1, 1), (1, 0), (0, 0)]])  # same v-down convention, unchanged
        self.assertEqual(m.colors, [])
        self.assertEqual(m.triangles, [(2, 3, 0), (1, 2, 0)])  # winding reversed
        for a, b, c in m.triangles:  # counter-clockwise faces agree with the normals
            face = cross(sub(m.positions[b], m.positions[a]), sub(m.positions[c], m.positions[a]))
            self.assertGreater(face[2], 0)
        self.assertEqual(m.extras["pmx_vertices"], [0, 1, 2, 3])

    def test_weights(self):
        m = self.sc.meshes[0]
        # BDEF1, BDEF2 (0.25 on bone 0), BDEF4 (2, 2 -> 0.5, 0.5), SDEF (0.75 on bone 1) as BDEF2
        want = [({0: 1.0}), ({0: 0.25, 1: 0.75}), ({0: 0.5, 1: 0.5}), ({1: 0.75, 0: 0.25})]
        for js, ws, wd in zip(m.joints, m.weights, want):
            got = {}
            for j, w in zip(js, ws):
                if w:
                    got[j] = got.get(j, 0) + w
            self.assertEqual(set(got), set(wd))
            for j in wd:
                self.assertAlmostEqual(got[j], wd[j], places=6)
            self.assertAlmostEqual(sum(ws), 1.0, places=6)
            self.assertEqual(len(js), 4)

    def test_material_and_texture(self):
        (mat,) = self.sc.materials
        self.assertEqual(mat.name, "肌")
        self.assertTrue(close(mat.base_color, (1.0, lin(0.5), lin(0.25), 1.0)))  # sRGB diffuse -> linear
        self.assertEqual(mat.textures, {"base": 0})
        self.assertEqual(mat.alpha_mode, "OPAQUE")
        self.assertTrue(mat.double_sided)
        self.assertEqual(mat.shader, "mmd")
        ex = mat.extras
        self.assertEqual(ex["name_en"], "skin")
        self.assertEqual(ex["diffuse"], (1.0, 0.5, 0.25, 1.0))
        self.assertTrue(ex["edge"])
        self.assertEqual(ex["toon_shared"], "toon04.bmp")
        self.assertIsNone(ex["toon"])
        self.assertIsNone(ex["sphere"])
        self.assertEqual(ex["sphere_mode"], "off")
        self.assertEqual(ex["memo"], "メモ")
        self.assertAlmostEqual(ex["specular_power"], 5.0)
        (tex,) = self.sc.textures
        self.assertEqual((tex.name, tex.mime, tex.path, tex.data), ("肌", "image/png", "tex/肌.png", None))
        self.assertEqual(tex.extras["pmx_path"], "tex\\肌.png")

    def test_morphs(self):
        a, nod = self.sc.meta["morphs"]
        self.assertEqual((a["name"], a["name_en"], a["panel"], a["kind"]), ("あ", "a", "mouth", "vertex"))
        ((local, d),) = a["meshes"][0]
        self.assertEqual(local, 2)
        self.assertTrue(close(d, (0, S, -S)))
        self.assertEqual((nod["name"], nod["kind"], nod["panel"]), ("うなずき", "bone", "other"))
        ((bone, t, q),) = nod["bones"]
        self.assertEqual(bone, 1)
        self.assertTrue(close(t, (0, 0, -S)))
        n = math.sqrt(0.1 ** 2 + 0.2 ** 2 + 0.3 ** 2 + 0.9 ** 2)
        self.assertTrue(close(q, (-0.1 / n, -0.2 / n, 0.3 / n, 0.9 / n), 1e-6))  # x, y turn the other way

    def test_scale_option(self):
        sc = pmx.read_bytes(main_model(), scale=1.0)
        self.assertTrue(close(sc.skeleton.bones[1].translation, (0, 10, -2)))
        self.assertEqual(sc.meta["scale"], 1.0)


class Variants(unittest.TestCase):
    def test_utf8_and_4_byte_indices(self):
        a = pmx.read_bytes(main_model())
        b = pmx.read_bytes(main_model(W(enc=1, sizes=(4, 4, 4, 4, 4, 4))))
        self.assertEqual(b.meta["encoding"], "UTF-8")
        self.assertEqual(b.validate(), [])
        self.assertEqual([x.name for x in b.skeleton.bones], [x.name for x in a.skeleton.bones])
        self.assertEqual(b.meta["licence_text"], a.meta["licence_text"])
        self.assertEqual(b.meshes[0].positions, a.meshes[0].positions)
        self.assertEqual(b.meshes[0].triangles, a.meshes[0].triangles)
        self.assertEqual(b.meshes[0].weights, a.meshes[0].weights)
        self.assertEqual(b.meta["morphs"], a.meta["morphs"])

    def test_pmx21_qdef_and_extra_uv(self):
        w = W(version=2.1, add_uv=1, sizes=(2, 1, 1, 1, 1, 1))
        b = w.header("q", "q", "", "")
        b += count(3)
        b += w.vertex((0, 0, 0), (0, 1, 0), (0, 0), 4, (0, 1, 2, -1), (1, 1, 2, 0), extra=[(1, 2, 3, 4)])
        b += w.vertex((1, 0, 0), (0, 1, 0), (1, 0), 0, (2,), (1,), extra=[(5, 6, 7, 8)])
        b += w.vertex((0, 0, 1), (0, 1, 0), (0, 1), 0, (1,), (1,), extra=[(9, 9, 9, 9)])
        b += count(3) + b"".join(w.vidx(i) for i in (0, 1, 2))
        b += count(0)
        b += count(1) + w.material("m", "m", (1, 1, 1, 0.5), 0, -1, 3, toon=("own", -1))
        b += count(3)
        for i, name in enumerate(("a", "b", "c")):
            b += w.bone(name, name, (i, 0, 0), i - 1)
        b += count(0) + w.tail(False, False, False) + count(0)  # 2.1: no soft bodies
        sc = pmx.read_bytes(b)
        self.assertEqual(sc.validate(), [])
        self.assertEqual(sc.meta["version"], 2.1)
        m = sc.meshes[0]
        self.assertEqual(len(m.uvs), 2)
        self.assertEqual(m.uvs[1], [(1, 2), (5, 6), (9, 9)])
        self.assertEqual(m.extras["pmx_extra_uv"][0][0], (1, 2, 3, 4))
        got = dict((j, w_) for j, w_ in zip(m.joints[0], m.weights[0]) if w_)
        self.assertEqual(set(got), {0, 1, 2})
        self.assertAlmostEqual(got[2], 0.5)
        self.assertAlmostEqual(got[0], 0.25)
        mat = sc.materials[0]
        self.assertEqual((mat.alpha_mode, mat.textures, mat.double_sided), ("BLEND", {}, False))
        self.assertAlmostEqual(mat.base_color[3], 0.5)
        notes = " ".join(sc.meta["notes"])
        self.assertIn("1 QDEF vertex read as BDEF4", notes)
        self.assertIn("1 extra UV set kept", notes)
        self.assertEqual(m.colors, [])  # no vertex-colour flag

    def test_ik_and_parent_after_child(self):
        w = W()
        b = w.header("ik", "ik", "", "")
        b += count(1) + w.vertex((0, 0, 0), (0, 1, 0), (0, 0), 0, (0,), (1,))
        b += count(0) + count(0) + count(0)
        b += count(3)
        b += w.bone("ひざ", "knee", (0, 5, 0), 2)  # its parent comes after it
        b += w.bone("足IK", "leg IK", (0, 0, 1), -1, ik=(0, 40, 2.0, [(0, (-3.0, -0.1, -0.2), (-0.5, 0.1, 0.3)),
                                                                     (2,)]))
        b += w.bone("足", "leg", (0, 10, 0), -1, inherit=(1, 0.5))
        b += count(0) + w.tail(False, False, False)
        sc = pmx.read_bytes(b)
        self.assertEqual(sc.validate(), [])
        bones = sc.skeleton.bones
        self.assertEqual([x.name for x in bones], ["足", "ひざ", "足IK"])
        self.assertEqual([x.parent for x in bones], [-1, 0, -1])
        self.assertEqual(sc.meta["pmx_bone_order"], [2, 0, 1])
        self.assertTrue(close(bones[1].translation, (0, -5 * S, 0)))
        self.assertEqual(bones[0].extras["inherit"], {"bone": 2, "weight": 0.5, "rotation": True,
                                                      "position": False})
        (chain,) = sc.meta["ik"]
        self.assertEqual((chain["bone"], chain["target"], chain["loops"]), (2, 1, 40))
        self.assertAlmostEqual(chain["limit_angle"], 2.0)
        knee, leg = chain["links"]
        self.assertEqual(knee["bone"], 1)
        self.assertTrue(close(knee["min"], (0.5, -0.1, -0.2)))  # x, y mirrored, z kept
        self.assertTrue(close(knee["max"], (3.0, 0.1, 0.3)))
        self.assertEqual(leg, {"bone": 0})
        self.assertTrue(bones[2].extras["ik"])
        self.assertEqual(sc.meshes, [])  # no faces, no mesh
        self.assertIn("1 IK chain kept", " ".join(sc.meta["notes"]))

    def test_bone_extras_and_every_morph_kind(self):
        w = W(version=2.1, add_uv=1, sizes=(1, 1, 1, 1, 1, 1))
        b = w.header("all", "all", "", "")
        b += count(3)
        for p in ((0, 0, 0), (1, 0, 0), (0, 1, 0)):
            b += w.vertex(p, (0, 0, -1), (0, 0), 0, (0,), (1,), extra=[(0.5, 2.0, -1, 1)])
        b += count(3) + b"".join(w.vidx(i) for i in (0, 2, 1))
        b += count(0) + count(1) + w.material("m", "m", (1, 1, 1, 1), 0x20, -1, 3)  # 2.1 vertex colour flag
        b += count(2)
        b += w.bone("root", "root", (0, 0, 0), -1, flags=0x001F, tail=1, fixed_axis=(0, 0, 1))
        b += w.bone("arm", "arm", (1, 0, 0), 0, local_axes=((1, 0, 0), (0, 0, 1)), external=7)
        mat = struct.pack("<B4f3ff3f4ff4f4f4f", 1, *range(1, 29))
        b += count(6)
        b += w.morph_raw("group", 0, [w.idx(1, 1) + struct.pack("<f", 0.5)])
        b += w.morph_raw("uv", 3, [w.vidx(2) + struct.pack("<4f", 0.5, 0.25, 0, 0)])
        b += w.morph_raw("uv1", 4, [w.vidx(0) + struct.pack("<4f", 1, 1, 1, 1)])
        b += w.morph_raw("mat", 8, [w.idx(-1, 1) + mat])
        b += w.morph_raw("flip", 9, [w.idx(0, 1) + struct.pack("<f", 1.0)])
        b += w.morph_raw("impulse", 10, [w.idx(0, 1) + bytes([1]) + struct.pack("<6f", 1, 2, 3, 4, 5, 6)])
        b += w.tail() + count(0)
        sc = pmx.read_bytes(b)
        self.assertEqual(sc.validate(), [])
        root, arm = sc.skeleton.bones
        self.assertEqual(root.extras["tail_bone"], 1)
        self.assertNotIn("tail_offset", root.extras)
        self.assertTrue(close(root.extras["fixed_axis"], (0, 0, -1)))
        self.assertTrue(close(arm.extras["local_x"], (1, 0, 0)))
        self.assertTrue(close(arm.extras["local_z"], (0, 0, -1)))
        self.assertEqual(arm.extras["external_parent"], 7)
        mo = {m["name"]: m for m in sc.meta["morphs"]}
        self.assertEqual([m["kind"] for m in sc.meta["morphs"]],
                         ["group", "uv", "uv1", "material", "flip", "impulse"])
        self.assertEqual(mo["group"]["morphs"], [(1, 0.5)])
        self.assertEqual(mo["uv"]["meshes"], {0: [(2, (0.5, 0.25, 0.0, 0.0))]})
        self.assertEqual(mo["uv1"]["meshes"], {0: [(0, (1.0, 1.0, 1.0, 1.0))]})
        (mm,) = mo["mat"]["materials"]
        self.assertEqual((mm["material"], mm["op"], mm["diffuse"], mm["toon_tint"]),
                         (-1, "add", (1, 2, 3, 4), (25, 26, 27, 28)))
        self.assertEqual(mo["flip"]["morphs"], [(0, 1.0)])
        self.assertEqual(mo["impulse"]["impulses"], 1)
        notes = " ".join(sc.meta["notes"])
        self.assertIn("6 morphs kept", notes)
        self.assertIn("1 display frame, 1 rigid body, 1 physics joint", notes)
        self.assertNotIn("physics part", notes)
        self.assertEqual(sc.meshes[0].colors, [(0.5, 1.0, 0.0, 1.0)] * 3)  # extra UV 1, clamped
        self.assertIn("Vertex colours taken from extra UV 1", notes)

    def test_no_bones(self):
        w = W()
        b = w.header("n", "n", "", "")
        b += count(3)
        for p in ((0, 0, 0), (1, 0, 0), (0, 1, 0)):
            b += w.vertex(p, (0, 0, -1), (0, 0), 0, (-1,), (1,))
        b += count(3) + b"".join(w.vidx(i) for i in (0, 1, 2))
        b += count(0) + count(1) + w.material("m", "m", (1, 1, 1, 1), 0, -1, 3)
        b += count(0) + count(0) + w.tail(False, False, False)
        sc = pmx.read_bytes(b)
        self.assertEqual(sc.validate(), [])
        self.assertFalse(sc.meshes[0].skinned)
        self.assertIn("The model has no bones.", sc.meta["notes"])

    def test_cut_short_physics_still_reads(self):
        data = main_model()
        sc = pmx.read_bytes(data[:-20])  # the joint at the very end is cut
        self.assertEqual(sc.validate(), [])
        self.assertEqual(len(sc.meshes[0].positions), 4)
        self.assertTrue(any("physics part" in n for n in sc.meta["notes"]))

    def test_read_file_and_load(self):
        import tempfile
        import model_in
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tiny.pmx"
            p.write_bytes(main_model())
            sc = model_in.load(p)
            self.assertEqual(sc.meta["source_format"], "pmx")
            self.assertEqual(Path(sc.textures[0].path), Path(d) / "tex" / "肌.png")
            self.assertIn("1 texture file isn't next to the model.", sc.meta["notes"])
            (Path(d) / "tex").mkdir()
            (Path(d) / "tex" / "肌.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            sc = pmx.read(p)
            self.assertFalse(any("next to the model" in n for n in sc.meta["notes"]))
            self.assertEqual(sc.textures[0].read_bytes(), b"\x89PNG\r\n\x1a\n")


class Errors(unittest.TestCase):
    def bad(self, data, words):
        with self.assertRaises(pmx.PmxError) as cm:
            pmx.read_bytes(data)
        self.assertIn(words, str(cm.exception))
        from model_in.scene import ReadError
        self.assertIsInstance(cm.exception, ReadError)

    def test_plain_word_errors(self):
        good = main_model()
        self.bad(b"OBJ stuff", "isn't a PMX file")
        self.bad(b"Pmd" + good[3:], "PMD")
        self.bad(good[:4] + struct.pack("<f", 3.0) + good[8:], "version 3.0")
        self.bad(good[:60], "ends too early")
        self.bad(good[:9] + bytes([7]) + good[10:], "encoding")
        self.bad(good[:11] + bytes([3]) + good[12:], "vertex index size")

    def test_face_out_of_range(self):
        w = W()
        b = w.header("x", "x", "", "") + count(1) + w.vertex((0, 0, 0), (0, 1, 0), (0, 0), 0, (0,), (1,))
        b += count(3) + b"".join(w.vidx(i) for i in (0, 0, 5))
        self.bad(b, "vertex 5")

    def test_material_face_count_mismatch_is_tolerated(self):
        w = W()
        b = w.header("x", "x", "", "") + count(3)
        for p in ((0, 0, 0), (1, 0, 0), (0, 1, 0)):
            b += w.vertex(p, (0, 0, -1), (0, 0), 0, (0,), (1,))
        b += count(6) + b"".join(w.vidx(i) for i in (0, 2, 1, 0, 1, 2))
        b += count(0) + count(1) + w.material("m", "m", (1, 1, 1, 1), 0, -1, 3)  # covers 1 of the 2 faces
        b += count(1) + w.bone("a", "a", (0, 0, 0), -1) + count(0) + w.tail(False, False, False)
        sc = pmx.read_bytes(b)
        self.assertEqual(sc.validate(), [])
        self.assertEqual(sc.meshes[0].triangles, [(1, 2, 0)])
        self.assertIn("1 face belongs to no material; dropped.", sc.meta["notes"])
        bad = b.replace(w.material("m", "m", (1, 1, 1, 1), 0, -1, 3), w.material("m", "m", (1, 1, 1, 1), 0, -1, 4))
        self.bad(bad, "broken face count")

    def test_missing_file(self):
        with self.assertRaises(pmx.PmxError):
            pmx.read(REPO / "out" / "no-such-model.pmx")


if __name__ == "__main__":
    unittest.main()
