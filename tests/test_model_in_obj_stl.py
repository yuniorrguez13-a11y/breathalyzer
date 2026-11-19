"""Tests for tools/model_in/obj.py (OBJ + MTL) and tools/model_in/stl.py (binary + ASCII STL).

Every model here is a small made-up one written in the test: no downloaded models."""
import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
from model_in import obj, stl  # noqa: E402
from model_in.scene import ReadError, validate  # noqa: E402


def area2(pts, tri):
    a, b, c = (pts[i] for i in tri)
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


CUBE_OBJ = """# a made-up cube, two groups and two materials
mtllib cube.mtl
v -1 -1 -1
v 1 -1 -1
v 1 1 -1
v -1 1 -1
v -1 -1 1
v 1 -1 1
v 1 1 1
v -1 1 1
vt 0 0
vt 1 0
vt 1 1
vt 0 1
vn 0 0 1
vn 0 0 -1
vn 1 0 0
vn -1 0 0
vn 0 1 0
vn 0 -1 0
o Box
usemtl red
f 5/1/1 6/2/1 7/3/1 8/4/1
f -5/1/2 -6/4/2 -7/3/2 -8/2/2
usemtl skin
f 2/1/3 3/2/3 7/3/3 6/4/3
f 1/1/4 5/2/4 8/3/4 4/4/4
g Lid
usemtl skin
f 4//5 8//5 7//5 3//5
f 1//6 2//6 6//6 5//6
"""

CUBE_MTL = """newmtl red
Kd 0.8 0.1 0.1
d 0.5
map_Kd -s 1 1 1 -o 0 0 0 tex/red albedo.png
newmtl skin
Kd 1 0.9 0.8
map_Bump -bm 0.5 skin_n.png
norm skin_nrm.png
map_Ke glow.tga
map_d mask.png
"""


class ObjTriangulation(unittest.TestCase):
    def test_convex_quad_is_a_fan(self):
        self.assertEqual(obj.triangulate([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]), [(0, 1, 2), (0, 2, 3)])

    def test_concave_polygon_uses_ear_clipping(self):
        # an L shape (concave at corner 2), counter-clockwise in the XY plane; a fan from corner 0 would make
        # the triangle (0, 2, 3), which lies outside the L and turns the wrong way
        pts = [(2, 0, 0), (2, 1, 0), (1, 1, 0), (1, 2, 0), (0, 2, 0), (0, 0, 0)]
        self.assertLess(area2(pts, (0, 2, 3)), 0)
        tris = obj.triangulate(pts)
        self.assertEqual(len(tris), 4)
        for t in tris:
            self.assertGreater(area2(pts, t), 0, "every triangle keeps the polygon's winding")
        self.assertAlmostEqual(sum(area2(pts, t) for t in tris) / 2, 3.0)

    def test_concave_polygon_facing_down(self):
        pts = [(0, 0, 0), (0, 2, 0), (1, 2, 0), (1, 1, 0), (2, 1, 0), (2, 0, 0)]  # clockwise seen from +Z
        tris = obj.triangulate(pts)
        self.assertEqual(len(tris), 4)
        for t in tris:
            self.assertLess(area2(pts, t), 0)
        self.assertAlmostEqual(-sum(area2(pts, t) for t in tris) / 2, 3.0)

    def test_mtl_texture_options_are_skipped(self):
        self.assertEqual(obj.tex_file("-bm 0.3 -o 0.1 0.2 n.png"), "n.png")
        self.assertEqual(obj.tex_file("-s 2 2 my file.png"), "my file.png")
        self.assertEqual(obj.tex_file("-clamp on a.jpg"), "a.jpg")


class ObjReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "cube.obj").write_text(CUBE_OBJ, encoding="utf-8")
        (self.dir / "cube.mtl").write_text(CUBE_MTL, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_cube_meshes_materials_textures(self):
        sc = obj.read(self.dir / "cube.obj")
        self.assertEqual(validate(sc), [])
        self.assertEqual([m.name for m in sc.meshes], ["Box.red", "Box.skin", "Lid"])
        self.assertEqual(sum(len(m.triangles) for m in sc.meshes), 12)
        red, skin = sc.materials
        self.assertEqual(red.name, "red")
        self.assertEqual(red.alpha_mode, "BLEND")
        self.assertAlmostEqual(red.base_color[3], 0.5)
        self.assertAlmostEqual(red.base_color[0], 0.8)
        self.assertEqual(sc.textures[red.textures["base"]].path, str(self.dir / "tex" / "red albedo.png"))
        self.assertEqual(sc.textures[red.textures["base"]].mime, "image/png")
        self.assertEqual(Path(sc.textures[skin.textures["normal"]].path).name, "skin_nrm.png")  # norm beats bump
        self.assertEqual(Path(sc.textures[skin.textures["emissive"]].path).name, "glow.tga")
        self.assertEqual(skin.alpha_mode, "MASK")
        self.assertEqual(sc.meshes[0].material, 0)
        self.assertEqual(sc.meshes[2].material, 1)
        self.assertEqual(sc.meta["source_format"], "obj")
        self.assertEqual((sc.meta["units"], sc.meta["up"]), ("m", "Y"))
        self.assertEqual(len(sc.skeleton.bones), 0)

    def test_negative_indices_uv_flip_and_winding(self):
        sc = obj.read(self.dir / "cube.obj")
        front = sc.meshes[0]
        # the +Z face: vt (0,0) -> glTF (0,1)
        i = front.positions.index((-1.0, -1.0, 1.0))
        self.assertEqual(front.uvs[0][i], (0.0, 1.0))
        # negative indices -5..-8 are vertices 4..1: the back face at z = -1
        self.assertIn((1.0, 1.0, -1.0), front.positions)
        for a, b, c in front.triangles:
            pa, pb, pc = front.positions[a], front.positions[b], front.positions[c]
            u = [pb[k] - pa[k] for k in range(3)]
            v = [pc[k] - pa[k] for k in range(3)]
            n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            fn = front.normals[a]
            self.assertGreater(sum(x * y for x, y in zip(n, fn)), 0, "counter-clockwise front faces")

    def test_lid_has_no_uvs_and_shared_vertices(self):
        lid = obj.read(self.dir / "cube.obj").meshes[2]
        self.assertEqual(lid.uvs, [])
        self.assertEqual(len(lid.positions), 8)  # two quads with their own normals, 4 corners each
        self.assertEqual(len(lid.triangles), 4)

    def test_missing_normals_are_computed_and_noted(self):
        text = "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\nl 1 2\n"
        sc = obj.parse(text)
        self.assertEqual(validate(sc), [])
        m = sc.meshes[0]
        for n in m.normals:
            self.assertAlmostEqual(n[2], 1.0)
        self.assertIsNone(m.material)
        notes = " ".join(sc.meta["notes"])
        self.assertIn("normals were computed", notes)
        self.assertIn("lines/points dropped", notes)

    def test_z_up_and_scale(self):
        sc = obj.parse("v 0 0 100\nv 100 0 100\nv 0 100 100\nf 1 2 3\n", up="Z", scale=0.01)
        pos = sc.meshes[0].positions
        self.assertEqual(pos[0], (0.0, 1.0, -0.0))
        self.assertEqual(pos[2], (0.0, 1.0, -1.0))
        self.assertTrue(any("Z up" in n for n in sc.meta["notes"]))

    def test_vertex_colours_continuation_and_missing_material(self):
        text = "v 0 0 0 1 0 0\nv 1 0 0 0 1 0\n v 0 1 0 0 0 1\nusemtl ghost\nf 1 \\\n 2 3\n"
        sc = obj.parse(text)
        self.assertEqual(validate(sc), [])
        self.assertEqual(sc.meshes[0].colors[0], (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(sc.materials[0].name, "ghost")
        self.assertTrue(any("not defined" in n for n in sc.meta["notes"]))

    def test_in_memory_mtl_and_big_index(self):
        n = 70000  # past 16-bit indices: no limit
        lines = ["mtllib a b.mtl"]
        lines += ["v %d 0 0" % i for i in range(n)]
        lines += ["v 0 1 0", "usemtl m", "f 1 %d %d" % (n, n + 1)]
        sc = obj.parse("\n".join(lines), mtl_text={"a b.mtl": "newmtl m\nKd 0 0 1\n"})
        self.assertEqual(sc.materials[0].base_color, (0.0, 0.0, 1.0, 1.0))
        self.assertEqual((sc.materials[0].metallic, sc.materials[0].roughness), (0.0, 1.0))
        sc = obj.parse("mtllib m\nv 0 0 0\nv 1 0 0\nv 0 1 0\nusemtl a\nf 1 2 3\nusemtl b\nf 1 2 3\n",
                       mtl_text={"m": "newmtl a\nNs 98\nnewmtl b\nPr 0.25\nPm 1\nNs 5\n"})
        a, b = sc.materials
        self.assertAlmostEqual(a.roughness, math.sqrt(0.02))
        self.assertEqual((b.metallic, b.roughness), (1.0, 0.25))
        self.assertEqual(len(sc.meshes[0].positions), 3)

    def test_broken_files_say_one_plain_sentence(self):
        with self.assertRaisesRegex(ReadError, "only has 1"):
            obj.parse("v 0 0 0\nf 1 2 3\n")
        with self.assertRaisesRegex(ReadError, r"line 2\)"):
            obj.parse("v 0 0 0\nv 1 nope 0\n")
        with self.assertRaisesRegex(ReadError, "no faces"):
            obj.parse("v 0 0 0\n")
        with self.assertRaisesRegex(ReadError, "no triangles"):
            stl.parse(b"solid empty\nendsolid empty\n")
        with self.assertRaisesRegex(ReadError, "too short"):
            stl.parse(b"\x00\x01")
        with self.assertRaisesRegex(ReadError, "can't read"):
            stl.parse(b"solid x\nfacet normal 0 0 1\nvertex 1 2\nendfacet\nendsolid\n")

    def test_broken_mtl_is_a_note(self):
        sc = obj.parse("mtllib m.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nusemtl a\nf 1 2 3\n",
                       mtl_text={"m.mtl": "newmtl a\nKd one two three\n"})
        self.assertEqual(validate(sc), [])
        self.assertTrue(any("broken line" in n for n in sc.meta["notes"]))

    def test_load_by_extension(self):
        import model_in
        sc = model_in.load(self.dir / "cube.obj")
        self.assertEqual(len(sc.meshes), 3)


def binary_stl(tris, header=b"made up", normals=None):
    out = bytearray(header.ljust(80, b"\0"))
    out += struct.pack("<I", len(tris))
    for k, t in enumerate(tris):
        n = normals[k] if normals else (0.0, 0.0, 0.0)
        out += struct.pack("<12fH", *n, *t[0], *t[1], *t[2], 0)
    return bytes(out)


# a made-up tetrahedron in millimetres, Z up, faces counter-clockwise from outside
TET = [(0, 0, 0), (10, 0, 0), (0, 10, 0), (0, 0, 10)]
TET_FACES = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]


def tet_tris():
    return [tuple(TET[i] for i in f) for f in TET_FACES]


class StlReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def check_tet(self, sc):
        self.assertEqual(validate(sc), [])
        m = sc.meshes[0]
        self.assertEqual(len(m.triangles), 4)
        self.assertEqual(len(m.positions), 12)  # flat: corners only shared inside one face
        self.assertEqual(len({p for p in m.positions}), 4)  # 4 positions
        # mm, Z up -> m, Y up: (0,0,10) mm becomes (0, 0.01, 0) m
        self.assertIn((0.0, 0.01, 0.0), [tuple(round(c, 9) + 0.0 for c in p) for p in m.positions])
        # outward flat normals (the centroid is inside)
        cx = [sum(p[k] for p in m.positions) / len(m.positions) for k in range(3)]
        for a, b, c in m.triangles:
            na = m.normals[a]
            self.assertEqual(na, m.normals[b])
            self.assertEqual(na, m.normals[c])
            self.assertAlmostEqual(math.sqrt(sum(x * x for x in na)), 1.0)
            out = [m.positions[a][k] - cx[k] for k in range(3)]
            self.assertGreater(sum(x * y for x, y in zip(na, out)), 0)

    def test_binary(self):
        p = self.dir / "tet.stl"
        p.write_bytes(binary_stl(tet_tris()))
        sc = stl.read(p)
        self.check_tet(sc)
        self.assertEqual(sc.meta["stl_kind"], "binary")
        self.assertEqual(sc.meshes[0].name, "tet")

    def test_binary_whose_header_says_solid(self):
        p = self.dir / "tricky.stl"
        p.write_bytes(binary_stl(tet_tris(), header=b"solid exported by a made-up tool"))
        sc = stl.read(p)
        self.check_tet(sc)
        self.assertEqual(sc.meta["stl_kind"], "binary")

    def test_ascii_two_solids(self):
        def solid(name, shift):
            out = ["solid %s" % name]
            for t in tet_tris():
                out += ["  facet normal 0 0 0", "    outer loop"]
                out += ["      vertex %g %g %g" % (p[0] + shift, p[1], p[2]) for p in t]
                out += ["    endloop", "  endfacet"]
            out.append("endsolid %s" % name)
            return out
        p = self.dir / "two.stl"
        p.write_text("\n".join(solid("first", 0) + solid("second", 100)), encoding="ascii")
        sc = stl.read(p)
        self.assertEqual(sc.meta["stl_kind"], "ascii")
        self.assertEqual([m.name for m in sc.meshes], ["first", "second"])
        self.check_tet(sc)

    def test_coplanar_faces_share_vertices_and_smooth_option(self):
        quad = [((0, 0, 0), (1, 0, 0), (1, 1, 0)), ((0, 0, 0), (1, 1, 0), (0, 1, 0))]
        sc = stl.parse(binary_stl(quad), unit=1.0, up="Y")
        self.assertEqual(len(sc.meshes[0].positions), 4)
        sc = stl.parse(binary_stl(tet_tris()), smooth=True)
        self.assertEqual(validate(sc), [])
        self.assertEqual(len(sc.meshes[0].positions), 4)

    def test_degenerate_face_keeps_stored_normal(self):
        tris = tet_tris() + [((0, 0, 0), (1, 0, 0), (2, 0, 0))]
        sc = stl.parse(binary_stl(tris, normals=[(0, 0, 0)] * 4 + [(0, 0, 1)]))
        self.assertEqual(len(sc.meshes[0].triangles), 5)
        self.assertTrue(any("zero-area" in n for n in sc.meta["notes"]))
        self.assertEqual(validate(sc), [])


if __name__ == "__main__":
    unittest.main()
