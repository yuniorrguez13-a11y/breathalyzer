"""tools/model_in/fbx.py: binary (7400 and 7500 layouts, raw and zlib arrays) and ASCII FBX -> Scene.

The test files are built here from a small node tree, so nothing is downloaded and no model is shipped."""
import base64
import math
import os
import struct
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import model_in  # noqa: E402
from model_in import fbx  # noqa: E402
from model_in import scene as S  # noqa: E402

MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"


# ------------------------------------------------------------------------------------------------- writers

def L(v): return ("L", v)
def I(v): return ("I", v)  # noqa: E302,E743
def D(v): return ("D", float(v))  # noqa: E302
def Sx(v): return ("S", v)  # noqa: E302
def R(v): return ("R", v)  # noqa: E302
def Ad(v): return ("d", [float(x) for x in v])  # noqa: E302
def Ai(v): return ("i", [int(x) for x in v])  # noqa: E302


def N(name, *props, kids=None):
    return (name, list(props), kids)


def P(name, typ, *vals):
    out = [Sx(name), Sx(typ), Sx(""), Sx("A")]
    for v in vals:
        out.append(I(v) if isinstance(v, int) and typ in ("int", "enum", "bool") else
                   (Sx(v) if isinstance(v, str) else D(v)))
    return N("P", *out)


def P70(*ps):
    return N("Properties70", kids=list(ps))


def obj_name(name, cls):
    return Sx(name + "\x00\x01" + cls)


def enc_prop(p, compress):
    t, v = p
    if t in ("L", "I", "D", "C", "Y", "F"):
        fmt = {"L": "<q", "I": "<i", "D": "<d", "C": "<?", "Y": "<h", "F": "<f"}[t]
        return t.encode() + struct.pack(fmt, v)
    if t in ("S", "R"):
        b = v.encode("utf-8") if t == "S" else v
        return t.encode() + struct.pack("<I", len(b)) + b
    code = {"d": "d", "i": "i", "l": "q", "f": "f", "b": "b"}[t]
    raw = struct.pack("<%d%s" % (len(v), code), *v)
    if compress:
        data = zlib.compress(raw)
        return t.encode() + struct.pack("<III", len(v), 1, len(data)) + data
    return t.encode() + struct.pack("<III", len(v), 0, len(raw)) + raw


def write_binary(tree, version=7400, compress=False):
    wide = version >= 7500
    hs = 25 if wide else 13
    fmt = "<QQQB" if wide else "<IIIB"

    def wnode(n, off):
        name, props, kids = n
        nb = name.encode()
        pdata = b"".join(enc_prop(p, compress) for p in props)
        body = off + hs + len(nb) + len(pdata)
        sub = b""
        if kids is not None:
            for k in kids:
                sub += wnode(k, body + len(sub))
            sub += b"\0" * hs
        end = body + len(sub)
        return struct.pack(fmt, end, len(props), len(pdata), len(nb)) + nb + pdata + sub

    out = MAGIC + struct.pack("<I", version)
    for n in tree:
        out += wnode(n, len(out))
    out += b"\0" * hs + b"\xfa\xbc" * 8 + b"\0" * 4 + struct.pack("<I", version) + b"\0" * 120
    return out


def write_ascii(tree):
    lines = ["; FBX 7.4.0 project file", "; test file", ""]

    def val(p):
        t, v = p
        if t == "S":
            if "\x00\x01" in v:
                a, b = v.split("\x00\x01", 1)
                v = b + "::" + a
            return '"%s"' % v
        if t == "R":
            return ', "%s"' % base64.b64encode(v).decode()
        if t == "D":
            return repr(v)
        return str(int(v))

    def wnode(n, ind):
        name, props, kids = n
        pad = "\t" * ind
        if len(props) == 1 and props[0][0] in ("d", "i", "l", "f", "b"):
            arr = props[0][1]
            nums = ",".join(repr(x) if isinstance(x, float) else str(x) for x in arr)
            lines.append("%s%s: *%d {" % (pad, name, len(arr)))
            lines.append("%s\ta: %s" % (pad, nums))
            lines.append("%s}" % pad)
            return
        text = ", ".join(val(p) for p in props).replace(", ,", ",")
        if kids is None:
            lines.append("%s%s: %s" % (pad, name, text))
        else:
            lines.append("%s%s: %s {" % (pad, name, text) if text else "%s%s:  {" % (pad, name))
            for k in kids:
                wnode(k, ind + 1)
            lines.append("%s}" % pad)

    for n in tree:
        wnode(n, 0)
    return ("\n".join(lines) + "\n").encode("utf-8")


# ------------------------------------------------------------------------------------------------- test scenes

PNG = b"\x89PNG\r\n\x1a\n" + b"fake picture bytes"


def header(version=7400):
    return N("FBXHeaderExtension", kids=[N("FBXHeaderVersion", I(1003)), N("FBXVersion", I(version))])


def global_settings(up=1, up_s=1, front=2, front_s=1, coord=0, coord_s=1, unit=1.0):
    return N("GlobalSettings", kids=[N("Version", I(1000)), P70(
        P("UpAxis", "int", up), P("UpAxisSign", "int", up_s), P("FrontAxis", "int", front),
        P("FrontAxisSign", "int", front_s), P("CoordAxis", "int", coord), P("CoordAxisSign", "int", coord_s),
        P("UnitScaleFactor", "double", float(unit)))])


def mat16(t=(0.0, 0.0, 0.0)):
    return Ad([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, t[0], t[1], t[2], 1])


def layer(kind, values_key, values, mapping="ByPolygonVertex", ref="Direct", index_key=None, index=None):
    kids = [N("Version", I(101)), N("Name", Sx("")), N("MappingInformationType", Sx(mapping)),
            N("ReferenceInformationType", Sx(ref)), N(values_key, Ad(values))]
    if index_key:
        kids.append(N(index_key, Ai(index)))
    return N(kind, I(0), kids=kids)


def skinned_quad(version=7400):
    """A 1 x 2 m quad (Y up, metres) skinned to Hips -> Spine, with UVs, a material and an embedded texture."""
    geom = N("Geometry", L(100), obj_name("Quad", "Geometry"), Sx("Mesh"), kids=[
        N("Vertices", Ad([0, 0, 0, 1, 0, 0, 1, 2, 0, 0, 2, 0])),
        N("PolygonVertexIndex", Ai([0, 1, 2, -4])),
        layer("LayerElementNormal", "Normals", [0, 0, 1] * 4),
        layer("LayerElementUV", "UV", [0, 0, 1, 0, 1, 1, 0, 1], ref="IndexToDirect", index_key="UVIndex",
              index=[0, 1, 2, 3]),
        N("LayerElementMaterial", I(0), kids=[N("MappingInformationType", Sx("AllSame")),
                                              N("ReferenceInformationType", Sx("IndexToDirect")),
                                              N("Materials", Ai([0]))]),
    ])
    objects = N("Objects", kids=[
        geom,
        N("Model", L(200), obj_name("QuadMesh", "Model"), Sx("Mesh"), kids=[N("Version", I(232)), P70()]),
        N("Model", L(300), obj_name("Hips", "Model"), Sx("LimbNode"), kids=[P70()]),
        N("Model", L(301), obj_name("Spine", "Model"), Sx("LimbNode"), kids=[
            P70(P("Lcl Translation", "Lcl Translation", 0.0, 1.0, 0.0))]),
        N("Deformer", L(400), obj_name("Skin", "Deformer"), Sx("Skin")),
        N("Deformer", L(401), obj_name("Hips", "SubDeformer"), Sx("Cluster"), kids=[
            N("Indexes", Ai([0, 1, 2, 3])), N("Weights", Ad([1.0, 1.0, 1.0, 0.25])),
            N("Transform", mat16()), N("TransformLink", mat16())]),
        N("Deformer", L(402), obj_name("Spine", "SubDeformer"), Sx("Cluster"), kids=[
            N("Indexes", Ai([2, 3])), N("Weights", Ad([1.0, 0.75])),
            # Transform = TransformLink^-1 * mesh world at bind (the mesh sits at the origin)
            N("Transform", mat16((0.0, -1.0, 0.0))), N("TransformLink", mat16((0.0, 1.0, 0.0)))]),
        N("Material", L(500), obj_name("Skin", "Material"), Sx(""), kids=[P70(
            P("DiffuseColor", "Color", 1.0, 0.5, 0.25), P("Opacity", "double", 1.0))]),
        N("Texture", L(600), obj_name("Tex", "Texture"), Sx(""), kids=[
            N("FileName", Sx("C:/nowhere/tex.png")), N("RelativeFilename", Sx("tex.png"))]),
        N("Video", L(700), obj_name("Tex", "Video"), Sx("Clip"), kids=[
            N("RelativeFilename", Sx("tex.png")), N("Content", R(PNG))]),
        N("AnimationStack", L(800), obj_name("Take 001", "AnimStack"), Sx("")),
        N("AnimationCurve", L(801), obj_name("", "AnimCurve"), Sx("")),
    ])
    conns = N("Connections", kids=[
        N("C", Sx("OO"), L(200), L(0)), N("C", Sx("OO"), L(300), L(0)), N("C", Sx("OO"), L(301), L(300)),
        N("C", Sx("OO"), L(100), L(200)), N("C", Sx("OO"), L(500), L(200)),
        # the Spine cluster comes first, so the mesh's bind world is read through a non-identity bone
        N("C", Sx("OO"), L(400), L(100)), N("C", Sx("OO"), L(402), L(400)), N("C", Sx("OO"), L(401), L(400)),
        N("C", Sx("OO"), L(300), L(401)), N("C", Sx("OO"), L(301), L(402)),
        N("C", Sx("OP"), L(600), L(500), Sx("DiffuseColor")), N("C", Sx("OO"), L(700), L(600)),
    ])
    return [header(version), global_settings(unit=100.0), objects, conns]


def zup_cm(**axes):
    """A 1 x 2 m wall in a Z-up centimetre file (3ds Max style): faces -Y, sits 50 cm up, no normals."""
    gs = dict(up=2, up_s=1, front=1, front_s=-1, coord=0, coord_s=1, unit=1.0)
    gs.update(axes)
    objects = N("Objects", kids=[
        N("Geometry", L(10), obj_name("Wall", "Geometry"), Sx("Mesh"), kids=[
            N("Vertices", Ad([0, 0, 0, 100, 0, 0, 100, 0, 200, 0, 0, 200])),
            N("PolygonVertexIndex", Ai([0, 1, 2, -4]))]),
        N("Model", L(20), obj_name("Wall", "Model"), Sx("Mesh"), kids=[
            P70(P("Lcl Translation", "Lcl Translation", 0.0, 0.0, 50.0))]),
    ])
    conns = N("Connections", kids=[N("C", Sx("OO"), L(20), L(0)), N("C", Sx("OO"), L(10), L(20))])
    return [header(), global_settings(**gs), objects, conns]


def tri_normal(m, t):
    a, b, c = (m.positions[i] for i in t)
    return S.normalize(S.cross(tuple(b[k] - a[k] for k in range(3)), tuple(c[k] - a[k] for k in range(3))))


def close(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b)) and len(a) == len(b)


# ------------------------------------------------------------------------------------------------- tests

class SkinnedQuad(unittest.TestCase):
    def check(self, sc):
        self.assertEqual(sc.validate(), [])
        self.assertEqual([b.name for b in sc.skeleton.bones], ["Hips", "Spine"])
        self.assertEqual([b.parent for b in sc.skeleton.bones], [-1, 0])
        spine = sc.skeleton.bones[1]
        self.assertTrue(close(spine.translation, (0.0, 1.0, 0.0)))
        self.assertTrue(close(spine.rotation, (0.0, 0.0, 0.0, 1.0)))
        self.assertTrue(close(spine.inverse_bind, S.mat_from_trs((0, -1, 0), (0, 0, 0, 1), (1, 1, 1))))
        self.assertEqual(len(sc.meshes), 1)
        m = sc.meshes[0]
        self.assertEqual(len(m.positions), 4)
        self.assertEqual(len(m.triangles), 2)
        ys = sorted(p[1] for p in m.positions)
        self.assertAlmostEqual(ys[-1], 2.0)
        for t in m.triangles:  # counter-clockwise seen from the normal side
            self.assertGreater(S.cross(*[tuple(m.positions[t[i]][k] - m.positions[t[0]][k] for k in range(3))
                                         for i in (1, 2)])[2], 0)
        self.assertEqual(len(m.uvs), 1)
        top = [i for i, p in enumerate(m.positions) if p[1] > 1.5]
        for i in top:  # FBX v=1 at the top -> glTF v=0 at the top
            self.assertAlmostEqual(m.uvs[0][i][1], 0.0)
        for js, ws in zip(m.joints, m.weights):
            self.assertAlmostEqual(sum(ws), 1.0)
        v3 = next(i for i, p in enumerate(m.positions) if close(p, (0.0, 2.0, 0.0)))
        self.assertEqual(m.joints[v3][0], 1)
        self.assertAlmostEqual(m.weights[v3][0], 0.75)
        v2 = next(i for i, p in enumerate(m.positions) if close(p, (1.0, 2.0, 0.0)))
        self.assertAlmostEqual(m.weights[v2][0], 0.5)
        mat = sc.materials[m.material]
        self.assertTrue(close(mat.base_color, (1.0, 0.5, 0.25, 1.0)))
        self.assertEqual(mat.alpha_mode, "OPAQUE")
        tex = sc.textures[mat.textures["base"]]
        self.assertEqual(tex.read_bytes(), PNG)
        self.assertEqual(tex.mime, "image/png")
        self.assertEqual(sc.meta["animations"]["stacks"], ["Take 001"])
        self.assertTrue(any("animation" in n for n in sc.meta["notes"]))
        self.assertEqual((sc.meta["units"], sc.meta["up"]), ("m", "Y"))
        # bind = rest: skinning with the rest pose leaves every vertex where it is
        worlds = sc.skeleton.world_matrices()
        mats = [S.mat_mul(w, b.inverse_bind) for w, b in zip(worlds, sc.skeleton.bones)]
        moved, _, _ = S.skin_to_rest(m.positions, [], m.joints, m.weights, mats)
        for a, b in zip(moved, m.positions):
            self.assertTrue(close(a, b))

    def test_binary_layouts(self):
        for version in (7400, 7500):
            for compress in (False, True):
                with self.subTest(version=version, zlib=compress):
                    data = write_binary(skinned_quad(version), version, compress)
                    sc = fbx.parse(data)
                    self.assertEqual(sc.meta["source_format"], "fbx-binary")
                    self.assertEqual(sc.meta["fbx_version"], version)
                    self.check(sc)

    def test_ascii(self):
        data = write_ascii(skinned_quad())
        sc = fbx.parse(data)
        self.assertEqual(sc.meta["source_format"], "fbx-ascii")
        self.check(sc)
        self.check(fbx.parse(bytes([0xEF, 0xBB, 0xBF]) + data.replace(b"\n", b"\r\n")))  # BOM + Windows line ends

    def test_static_mesh_under_a_bone_is_bound_rigidly(self):
        tree = skinned_quad()
        objects, conns = tree[2][2], tree[3][2]
        objects += [N("Geometry", L(900), obj_name("Blade", "Geometry"), Sx("Mesh"), kids=[
                        N("Vertices", Ad([0, 0, 0, 0.1, 0, 0, 0, 0.5, 0])), N("PolygonVertexIndex", Ai([0, 1, -3]))]),
                    N("Model", L(901), obj_name("Blade", "Model"), Sx("Mesh"), kids=[
                        P70(P("Lcl Translation", "Lcl Translation", 0.0, 0.5, 0.0))])]
        conns += [N("C", Sx("OO"), L(901), L(301)), N("C", Sx("OO"), L(900), L(901))]
        sc = fbx.parse(write_binary(tree, 7500, True))
        self.assertEqual(sc.validate(), [])
        blade = next(m for m in sc.meshes if m.name == "Blade")
        self.assertEqual(set(blade.joints), {(1, 0, 0, 0)})
        self.assertAlmostEqual(min(p[1] for p in blade.positions), 1.5)  # Spine at 1 m + 0.5 m

    def test_opacity_map_makes_a_cutout(self):
        tree = skinned_quad()
        tree[3][2].append(N("C", Sx("OP"), L(600), L(500), Sx("TransparentColor")))
        sc = fbx.parse(write_binary(tree))
        mat = sc.materials[0]
        self.assertEqual(mat.alpha_mode, "MASK")
        self.assertIn("base", mat.textures)
        self.assertTrue(any("Opacity textures" in n for n in sc.meta["notes"]))

    def test_old_fbx6_is_refused(self):
        text = b'FBXHeaderExtension:  {\n FBXVersion: 6100\n}\nObjects:  {\n}\n'
        with self.assertRaises(S.ReadError) as cm:
            fbx.parse(text)
        self.assertIn("re-export", str(cm.exception))

    def test_read_from_disk_via_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "quad.fbx")
            with open(p, "wb") as f:
                f.write(write_binary(skinned_quad(7500), 7500, True))
            self.check(model_in.load(p))


class Axes(unittest.TestCase):
    def test_zup_cm_becomes_yup_metres(self):
        for writer in (lambda t: write_binary(t, 7400, True), write_ascii):
            sc = fbx.parse(writer(zup_cm()))
            self.assertEqual(sc.validate(), [])
            m = sc.meshes[0]
            xs = [p[0] for p in m.positions]
            ys = [p[1] for p in m.positions]
            zs = [p[2] for p in m.positions]
            self.assertAlmostEqual(min(ys), 0.5)
            self.assertAlmostEqual(max(ys), 2.5)
            self.assertAlmostEqual(max(xs), 1.0)
            self.assertTrue(all(abs(z) < 1e-9 for z in zs))
            for t in m.triangles:  # it faced -Y (front) in the file: it faces +Z now
                self.assertTrue(close(tri_normal(m, t), (0.0, 0.0, 1.0)))
            for n in m.normals:
                self.assertTrue(close(n, (0.0, 0.0, 1.0)))
            self.assertEqual(sc.meta["axes"]["up"], "+Z")
            self.assertTrue(any("centimetres" in n for n in sc.meta["notes"]))

    def test_left_handed_file_keeps_front_faces(self):
        sc = fbx.parse(write_binary(zup_cm(coord_s=-1)))
        self.assertEqual(sc.validate(), [])
        m = sc.meshes[0]
        self.assertLess(max(p[0] for p in m.positions), 1e-9)  # mirrored in X
        for t, n in zip(m.triangles, m.normals):
            self.assertTrue(close(tri_normal(m, t), (0.0, 0.0, 1.0)))
        self.assertTrue(any("left-handed" in n for n in sc.meta["notes"]))


class Transforms(unittest.TestCase):
    def props(self, **kw):
        # run through the binary writer and parser so the props have their real file types
        data = write_binary([N("Model", kids=[P70(*model_props_nodes(**kw))])])
        return fbx._props70(fbx.parse_binary(data)[1].children[0])

    def test_xyz_order_applies_x_first(self):
        m = fbx.local_matrix(self.props(r=(90, 0, 90)))
        # X first: +Y -> +Z; then Z: +Z stays +Z
        self.assertTrue(close(S.mat_dir(m, (0, 1, 0)), (0, 0, 1)))
        m2 = fbx.local_matrix(self.props(r=(90, 0, 90), order=5))  # ZYX: Z first: +Y -> -X; X keeps it
        self.assertTrue(close(S.mat_dir(m2, (0, 1, 0)), (-1, 0, 0)))

    def test_prerotation_and_translation(self):
        m = fbx.local_matrix(self.props(t=(1, 2, 3), pre=(0, 0, 90)))
        self.assertTrue(close(S.mat_point(m, (1, 0, 0)), (1, 3, 3)))
        m = fbx.local_matrix(self.props(s=(2, 2, 2), r=(0, 90, 0)))
        self.assertTrue(close(S.mat_point(m, (1, 0, 0)), (0, 0, -2)))


def model_props_nodes(**kw):
    out = []
    for k, v in kw.items():
        if k == "order":
            out.append(P("RotationOrder", "enum", v))
            continue
        name = {"t": "Lcl Translation", "r": "Lcl Rotation", "s": "Lcl Scaling", "pre": "PreRotation",
                "post": "PostRotation"}[k]
        out.append(P(name, name, *[float(x) for x in v]))
    return out


class Layers(unittest.TestCase):
    def mesh(self, layers, verts=None, pvi=None, mats=1):
        verts = verts or [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 2, 0, 0]
        pvi = pvi or [0, 1, -3, 0, 2, -4, 1, 4, -3]
        objects = [N("Geometry", L(1), obj_name("G", "Geometry"), Sx("Mesh"),
                     kids=[N("Vertices", Ad(verts)), N("PolygonVertexIndex", Ai(pvi))] + layers),
                   N("Model", L(2), obj_name("M", "Model"), Sx("Mesh"))]
        conns = [N("C", Sx("OO"), L(2), L(0)), N("C", Sx("OO"), L(1), L(2))]
        for k in range(mats):
            objects.append(N("Material", L(10 + k), obj_name("mat%d" % k, "Material"), Sx(""), kids=[P70(
                P("DiffuseColor", "Color", 0.1 * k, 0.2, 0.3), P("Opacity", "double", 0.5))]))
            conns.append(N("C", Sx("OO"), L(10 + k), L(2)))
        tree = [header(), global_settings(unit=100.0), N("Objects", kids=objects), N("Connections", kids=conns)]
        sc = fbx.parse(write_binary(tree, 7500, True))
        self.assertEqual(sc.validate(), [])
        return sc

    def test_mapping_modes(self):
        sc = self.mesh([
            layer("LayerElementNormal", "Normals", [0, 0, 1, 0, 0, -1], mapping="ByVertice", ref="IndexToDirect",
                  index_key="NormalsIndex", index=[0, 0, 0, 0, 0]),
            layer("LayerElementColor", "Colors", [1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1], mapping="ByPolygon"),
            layer("LayerElementUV", "UV", [0.25, 0.5], mapping="AllSame"),
        ])
        m = sc.meshes[0]
        self.assertTrue(all(close(n, (0, 0, 1)) for n in m.normals))
        self.assertEqual(sorted(set(m.colors)), [(0.0, 0.0, 1.0, 1.0), (0.0, 1.0, 0.0, 1.0), (1.0, 0.0, 0.0, 1.0)])
        self.assertTrue(all(close(uv, (0.25, 0.5)) for uv in m.uvs[0]))
        self.assertEqual(len(m.triangles), 3)
        self.assertEqual(sc.materials[0].alpha_mode, "BLEND")

    def test_material_per_polygon_splits_meshes(self):
        lay = N("LayerElementMaterial", I(0), kids=[N("MappingInformationType", Sx("ByPolygon")),
                                                    N("ReferenceInformationType", Sx("IndexToDirect")),
                                                    N("Materials", Ai([0, 1, 1]))])
        sc = self.mesh([lay], mats=2)
        self.assertEqual(len(sc.meshes), 2)
        self.assertEqual(sorted(m.material for m in sc.meshes), [0, 1])
        self.assertEqual(sorted(len(m.triangles) for m in sc.meshes), [1, 2])
        self.assertTrue(any("computed" in n for n in sc.meta["notes"]))

    def test_concave_polygon_ear_clipping(self):
        # an L shape (6 corners, concave at (1,1)) as one polygon
        verts = [0, 0, 0, 2, 0, 0, 2, 1, 0, 1, 1, 0, 1, 2, 0, 0, 2, 0]
        sc = self.mesh([], verts=verts, pvi=[0, 1, 2, 3, 4, -6], mats=0)
        m = sc.meshes[0]
        self.assertEqual(len(m.triangles), 4)
        area = 0.0
        for t in m.triangles:
            a, b, c = (m.positions[i] for i in t)
            z = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            self.assertGreater(z, 0)  # every piece keeps the polygon's counter-clockwise winding
            area += z / 2
        self.assertAlmostEqual(area, 3.0)

    def test_triangulate_function(self):
        self.assertEqual(fbx.triangulate([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]), [(0, 1, 2), (0, 2, 3)])
        self.assertEqual(fbx.triangulate([(0, 0, 0), (1, 0, 0)]), [])


class Broken(unittest.TestCase):
    def fails(self, data, word=None):
        t0 = time.monotonic()
        with self.assertRaises(S.ReadError) as cm:
            fbx.parse(data)
        self.assertLess(time.monotonic() - t0, 5.0)
        msg = str(cm.exception)
        self.assertNotIn("\n", msg)
        if word:
            self.assertIn(word, msg)
        return msg

    def test_truncated_everywhere(self):
        data = write_binary(skinned_quad(7500), 7500, True)
        for cut in range(28, len(data) - 200, 37):
            with self.subTest(cut=cut):
                try:
                    fbx.parse(data[:cut])
                except S.ReadError as e:
                    self.assertNotIn("\n", str(e))

    def test_offsets_past_the_file(self):
        data = bytearray(write_binary(skinned_quad(7400)))
        struct.pack_into("<I", data, 27, 0x7FFFFFF0)  # first node's end offset
        self.fails(bytes(data), "broken")

    def test_array_count_lies(self):
        bad = N("Objects", kids=[N("Geometry", L(1), Sx("g"), Sx("Mesh"), kids=[N("Vertices", Ad([0.0] * 3))])])
        data = bytearray(write_binary([bad], 7400, False))
        i = data.index(b"Vertices") + len("Vertices") + 1
        struct.pack_into("<I", data, i, 1 << 27)  # count far above what the bytes hold
        self.fails(bytes(data), "array")

    def test_zlib_bomb_is_capped(self):
        # 3 doubles claimed, 64 MB of zeros packed: unpacking stops at 25 bytes and the sizes don't match
        bomb = zlib.compress(b"\0" * (64 << 20))
        self.fails(_with_array_payload(bomb, 3), "array")
        self.fails(_with_array_payload(b"not zlib at all", 3), "compressed")
        # a tiny file claiming a 1 GB array is refused before anything is unpacked
        self.fails(_with_array_payload(zlib.compress(b"\0" * (1 << 20)), 1 << 27), "packed array")
        ok = fbx.parse_binary(_with_array_payload(zlib.compress(struct.pack("<3d", 1, 2, 3)), 3))[1]
        self.assertEqual(ok.children[0].children[0].props[0], [1.0, 2.0, 3.0])

    def test_unknown_property_type(self):
        data = bytearray(write_binary([N("Objects", Sx("x"))]))
        data[data.index(b"Objects") + 7] = ord("Q")
        self.fails(bytes(data), "unknown property")

    def test_not_fbx(self):
        self.fails(b"hello world, no model here", "isn't an FBX")
        self.fails(b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", 6100), "version")

    def test_ascii_unclosed(self):
        self.fails(b"FBXHeaderExtension:  {\n FBXVersion: 7400\nObjects:  {\n", "never closed")

    def test_parent_loop(self):
        objects = N("Objects", kids=[N("Model", L(1), Sx("a"), Sx("Null")), N("Model", L(2), Sx("b"), Sx("Null"))])
        conns = N("Connections", kids=[N("C", Sx("OO"), L(1), L(2)), N("C", Sx("OO"), L(2), L(1))])
        self.fails(write_binary([header(), objects, conns]), "loop")

    def test_bad_vertex_index(self):
        objects = N("Objects", kids=[
            N("Geometry", L(1), Sx("g"), Sx("Mesh"), kids=[N("Vertices", Ad([0, 0, 0] * 3)),
                                                         N("PolygonVertexIndex", Ai([0, 1, -100]))]),
            N("Model", L(2), Sx("m"), Sx("Mesh"))])
        conns = N("Connections", kids=[N("C", Sx("OO"), L(1), L(2)), N("C", Sx("OO"), L(2), L(0))])
        self.fails(write_binary([header(), objects, conns]), "vertex")


def _with_array_payload(payload, count):
    """A binary FBX whose only array claims `count` doubles but carries `payload` as its zlib data."""
    name = b"Vertices"
    prop = b"d" + struct.pack("<III", count, 1, len(payload)) + payload
    hs = 13
    inner_off = 27 + hs + len(b"Objects")
    inner_end = inner_off + hs + len(name) + len(prop)
    inner = struct.pack("<IIIB", inner_end, 1, len(prop), len(name)) + name + prop
    outer_end = inner_end + hs
    outer = struct.pack("<IIIB", outer_end, 0, 0, len(b"Objects")) + b"Objects" + inner + b"\0" * hs
    return MAGIC + struct.pack("<I", 7400) + outer + b"\0" * hs + b"\0" * 64


if __name__ == "__main__":
    unittest.main()
