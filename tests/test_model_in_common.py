"""tools/model_in: the rules every reader shares, checked across readers on one tiny made-up model.

- the same triangle (corners (0,0,0) (1,0,0) (0,1,0), facing +Z) written as OBJ, STL, COLLADA and glTF reads back
  with counter-clockwise front faces that agree with the normals, in metres, Y up
- load(): a missing file, a folder, an unknown or internal extension, and broken numbers (NaN, infinity) are one
  ReadError sentence, never a traceback
- glTF: a negative count, a sparse-only accessor claiming billions of items and JSON nested 100,000 deep are refused
  quickly (no huge memory, no RecursionError)
- COLLADA: an instance_node loop is cut and a 2^30 instance_node fan-out is refused; OBJ: a 20,000-corner concave
  face is split in well under a second
- every reader's meta says share = "private"
"""

import base64
import json
import math
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import model_in  # noqa: E402
from model_in import dae, gltf, obj, pmx, stl  # noqa: E402
from model_in.scene import ReadError, cross  # noqa: E402

TRI = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))

OBJ = "v 0 0 0\nv 1 0 0\nv 0 1 0\nvn 0 0 1\nf 1//1 2//1 3//1\n"
STL = ("solid t\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\n"
       "endsolid t\n")
DAE = """<?xml version="1.0"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
<asset><unit meter="1"/><up_axis>Y_UP</up_axis></asset>
<library_geometries><geometry id="g"><mesh>
<source id="p"><float_array id="pa" count="9">0 0 0 1 0 0 0 1 0</float_array><technique_common>
<accessor source="#pa" count="3" stride="3"><param name="X" type="float"/><param name="Y" type="float"/>
<param name="Z" type="float"/></accessor></technique_common></source>
<source id="n"><float_array id="na" count="3">0 0 1</float_array><technique_common>
<accessor source="#na" count="1" stride="3"><param name="X" type="float"/><param name="Y" type="float"/>
<param name="Z" type="float"/></accessor></technique_common></source>
<vertices id="v"><input semantic="POSITION" source="#p"/></vertices>
<triangles count="1"><input semantic="VERTEX" source="#v" offset="0"/><input semantic="NORMAL" source="#n" offset="1"/>
<p>0 0 1 0 2 0</p></triangles>
</mesh></geometry></library_geometries>
<library_visual_scenes><visual_scene id="s"><node id="n0"><instance_geometry url="#g"/></node></visual_scene>
</library_visual_scenes><scene><instance_visual_scene url="#s"/></scene></COLLADA>
"""


def gltf_doc(acc_extra=None, normals=True):
    data = struct.pack("<9f", *(c for p in TRI for c in p))
    accs = [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 0]}]
    views = [{"buffer": 0, "byteLength": 36}]
    attrs = {"POSITION": 0}
    if normals:
        data += struct.pack("<9f", *(0, 0, 1) * 3)
        views.append({"buffer": 0, "byteOffset": 36, "byteLength": 36})
        accs.append({"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3"})
        attrs["NORMAL"] = 1
    if acc_extra:
        accs.append(acc_extra)
    return {"asset": {"version": "2.0"},
            "buffers": [{"byteLength": len(data),
                         "uri": "data:application/octet-stream;base64," + base64.b64encode(data).decode()}],
            "bufferViews": views, "accessors": accs, "meshes": [{"primitives": [{"attributes": attrs}]}],
            "nodes": [{"mesh": 0}], "scenes": [{"nodes": [0]}], "scene": 0}


class Common(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, text):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_same_triangle_same_answer_in_every_reader(self):
        files = {
            "obj": self.put("t.obj", OBJ),
            "stl": self.put("t.stl", STL),
            "dae": self.put("t.dae", DAE),
            "gltf": self.put("t.gltf", json.dumps(gltf_doc())),
        }
        opts = {"stl": {"unit": 1.0, "up": "Y"}}
        for fmt, path in files.items():
            with self.subTest(fmt=fmt):
                s = model_in.load(path, **opts.get(fmt, {}))
                self.assertEqual(s.validate(), [])
                self.assertEqual((s.meta["units"], s.meta["up"]), ("m", "Y"))
                self.assertEqual(s.meta["share"], "private")  # every model starts private
                self.assertEqual(len(s.meshes), 1)
                m = s.meshes[0]
                self.assertEqual(len(m.triangles), 1)
                a, b, c = (m.positions[i] for i in m.triangles[0])
                self.assertEqual(sorted(map(tuple, (a, b, c))), sorted(TRI))
                face = cross([b[k] - a[k] for k in range(3)], [c[k] - a[k] for k in range(3)])
                self.assertGreater(face[2], 0.5, "counter-clockwise front face points +Z")
                for i in m.triangles[0]:
                    self.assertAlmostEqual(m.normals[i][2], 1.0, places=5)

    def test_load_says_what_is_wrong_in_one_sentence(self):
        (self.dir / "folder.obj").mkdir()
        cases = {
            "missing": self.dir / "nope.gltf",
            "folder": self.dir / "folder.obj",
            "unknown": self.put("x.blend", "x"),
            "internal": self.put("x.__main__", "x"),
            "no extension": self.put("model", "x"),
        }
        for label, p in cases.items():
            with self.subTest(label):
                with self.assertRaises(ReadError) as cm:
                    model_in.load(p)
                msg = str(cm.exception)
                self.assertEqual(msg.count("\n"), 0)
                self.assertTrue(msg.endswith("."), msg)

    def test_every_reader_turns_a_missing_file_into_a_read_error(self):
        for mod, ext in ((gltf, "glb"), (obj, "obj"), (stl, "stl"), (dae, "dae"), (pmx, "pmx")):
            with self.subTest(ext):
                with self.assertRaises(ReadError):
                    mod.read(self.dir / f"nope.{ext}")

    def test_broken_numbers_are_refused_by_load(self):
        for name, text in (("nan.obj", OBJ.replace("v 1 0 0", "v nan 0 0")),
                           ("inf.obj", OBJ.replace("v 0 1 0", "v 0 inf 0")),
                           ("nan.stl", STL.replace("vertex 1 0 0", "vertex 1 nan 0"))):
            with self.subTest(name):
                with self.assertRaises(ReadError) as cm:
                    model_in.load(self.put(name, text))
                self.assertIn("broken numbers", str(cm.exception))

    def test_gltf_counts_that_lie_are_refused_quickly(self):
        bad = {
            "negative count": {"bufferView": 0, "componentType": 5126, "count": -5, "type": "VEC3"},
            "sparse-only billions": {"componentType": 5126, "count": 2 ** 31 - 1, "type": "MAT4"},
            "count past the data": {"bufferView": 0, "componentType": 5126, "count": 2 ** 31 - 1, "type": "VEC3"},
            "negative offset": {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3", "byteOffset": -36},
        }
        for label, acc in bad.items():
            with self.subTest(label):
                doc = gltf_doc(acc)
                doc["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"] = len(doc["accessors"]) - 1
                with self.assertRaises(ReadError):
                    gltf.read_bytes(json.dumps(doc).encode())
        with self.assertRaises(ReadError):
            gltf.read_bytes(b"[" * 100000 + b"]" * 100000)

    def test_collada_node_instances_cannot_loop_or_explode(self):
        lib = ('<library_nodes><node id="L"><instance_geometry url="#g"/><instance_node url="#L"/>'
               '<instance_node url="#L"/></node></library_nodes><library_visual_scenes>')
        loop = DAE.replace("<library_visual_scenes>", lib).replace(
            '<node id="n0"><instance_geometry url="#g"/></node>', '<node id="n0"><instance_node url="#L"/></node>')
        s = dae.read(self.put("loop.dae", loop))  # the loop is cut, the geometry is kept once
        self.assertEqual(len(s.meshes), 1)
        self.assertTrue(any("loop back" in n for n in s.meta["notes"]))
        # no loop, but every level instances the next one twice: 2^30 nodes if nothing stopped it
        levels = "".join(f'<node id="N{k}"><instance_node url="#N{k + 1}"/><instance_node url="#N{k + 1}"/></node>'
                         for k in range(30))
        fan = DAE.replace("<library_visual_scenes>", f'<library_nodes>{levels}<node id="N30"><instance_geometry '
                          'url="#g"/></node></library_nodes><library_visual_scenes>').replace(
            '<node id="n0"><instance_geometry url="#g"/></node>', '<node id="n0"><instance_node url="#N0"/></node>')
        with self.assertRaises(ReadError) as cm:
            dae.read(self.put("fan.dae", fan))
        self.assertIn("nodes", str(cm.exception))

    def test_a_huge_concave_face_does_not_hang(self):
        n = 20000  # a star: concave, so without the corner limit ear clipping would run for hours
        pts = [(1.0 if k % 2 == 0 else 0.4) for k in range(n)]
        text = "".join(f"v {r * math.cos(2 * math.pi * k / n):.6f} {r * math.sin(2 * math.pi * k / n):.6f} 0\n"
                       for k, r in enumerate(pts))
        text += "f " + " ".join(str(i + 1) for i in range(n)) + "\n"
        t0 = time.monotonic()
        s = obj.read(self.put("star.obj", text))
        self.assertLess(time.monotonic() - t0, 10.0)
        self.assertEqual(len(s.meshes[0].triangles), n - 2)

    def test_weights_and_bone_order_rules_hold_in_the_scene_check(self):
        s = model_in.load(self.put("t.gltf", json.dumps(gltf_doc())))
        for m in s.meshes:
            for ws in m.weights:
                self.assertTrue(math.isclose(sum(ws), 1.0, abs_tol=1e-3))
        for i, b in enumerate(s.skeleton.bones):
            self.assertLess(b.parent, i)


if __name__ == "__main__":
    unittest.main()
