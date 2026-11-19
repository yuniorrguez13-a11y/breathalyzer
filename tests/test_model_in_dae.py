"""Tests for tools/model_in/dae.py (COLLADA 1.4 / 1.5). Every model is a small made-up one written here."""
import math
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
from model_in import dae  # noqa: E402
from model_in.scene import IDENTITY, ReadError, mat_close, mat_mul, validate  # noqa: E402


def ibm_row(z):
    return "1 0 0 0 0 1 0 0 0 0 1 %g 0 0 0 1" % (-z)


# A made-up rig in centimetres, Z up: a 5-joint chain (Root, Arm, Hand, Tip, Nub, 25 cm apart) skinning a quad,
# with the scene posed differently from the bind matrices (Arm turned 30 degrees), a sword under Hand and a mirrored
# concave floor plate.
DAE14 = """<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset><contributor><author>Made Up</author><copyright>CC0</copyright></contributor>
    <unit name="centimeter" meter="0.01"/><up_axis>Z_UP</up_axis></asset>
  <library_images>
    <image id="skin-img" name="skin"><init_from>tex%20dir/skin.png</init_from></image>
    <image id="nrm-img" name="nrm"><init_from>file:///C:/models/n.png</init_from></image>
  </library_images>
  <library_effects>
    <effect id="skin-fx"><profile_COMMON>
      <newparam sid="skin-surface"><surface type="2D"><init_from>skin-img</init_from></surface></newparam>
      <newparam sid="skin-sampler"><sampler2D><source>skin-surface</source></sampler2D></newparam>
      <technique sid="common"><phong>
        <emission><color>0.1 0.2 0.3 1</color></emission>
        <diffuse><texture texture="skin-sampler" texcoord="UVMap"/></diffuse>
        <transparent opaque="A_ONE"><color>1 1 1 1</color></transparent>
        <transparency><float>0.5</float></transparency>
      </phong>
      <extra><technique profile="FCOLLADA">
        <bump><texture texture="nrm-img" texcoord="UVMap"/></bump></technique></extra>
      </technique></profile_COMMON>
      <extra><technique profile="MAX3D"><double_sided>1</double_sided></technique></extra>
    </effect>
    <effect id="plain-fx"><profile_COMMON><technique sid="common"><lambert>
      <diffuse><color>0.5 0.25 0.125 1</color></diffuse></lambert></technique></profile_COMMON></effect>
  </library_effects>
  <library_materials>
    <material id="skin-mat" name="Skin"><instance_effect url="#skin-fx"/></material>
    <material id="plain-mat" name="Plain"><instance_effect url="#plain-fx"/></material>
  </library_materials>
  <library_geometries>
    <geometry id="body" name="body"><mesh>
      <source id="body-pos"><float_array id="body-pos-a" count="12">0 0 0 10 0 0 10 0 100 0 0 100</float_array>
        <technique_common><accessor source="#body-pos-a" count="4" stride="3">
          <param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>
        </accessor></technique_common></source>
      <source id="body-nrm"><float_array id="body-nrm-a" count="3">0 -1 0</float_array>
        <technique_common><accessor source="#body-nrm-a" count="1" stride="3"/></technique_common></source>
      <source id="body-uv"><float_array id="body-uv-a" count="8">0 0 1 0 1 1 0 1</float_array>
        <technique_common><accessor source="#body-uv-a" count="4" stride="2"/></technique_common></source>
      <vertices id="body-vtx"><input semantic="POSITION" source="#body-pos"/></vertices>
      <polylist material="skinSym" count="1">
        <input semantic="VERTEX" source="#body-vtx" offset="0"/>
        <input semantic="NORMAL" source="#body-nrm" offset="1"/>
        <input semantic="TEXCOORD" source="#body-uv" offset="2" set="0"/>
        <vcount>4</vcount><p>0 0 0 1 0 1 2 0 2 3 0 3</p>
      </polylist>
      <lines count="1"><input semantic="VERTEX" source="#body-vtx" offset="0"/><p>0 1</p></lines>
    </mesh></geometry>
    <geometry id="prop" name="prop"><mesh>
      <source id="prop-pos"><float_array id="prop-pos-a" count="15">0 0 0 4 0 0 4 4 0 2 2 0 0 4 0</float_array>
        <technique_common><accessor source="#prop-pos-a" count="5" stride="3"/></technique_common></source>
      <vertices id="prop-vtx"><input semantic="POSITION" source="#prop-pos"/></vertices>
      <polygons material="plainSym" count="1"><input semantic="VERTEX" source="#prop-vtx" offset="0"/>
        <p>0 1 2 3 4</p></polygons>
    </mesh></geometry>
  </library_geometries>
  <library_controllers>
    <controller id="body-skin"><skin source="#body">
      <bind_shape_matrix>1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</bind_shape_matrix>
      <source id="joints"><Name_array id="joints-a" count="5">Root Arm Hand Tip Nub</Name_array>
        <technique_common><accessor source="#joints-a" count="5" stride="1"/></technique_common></source>
      <source id="ibm"><float_array id="ibm-a" count="80">%IBMS%</float_array>
        <technique_common><accessor source="#ibm-a" count="5" stride="16"/></technique_common></source>
      <source id="w"><float_array id="w-a" count="7">1 0.1 0.2 0.3 0.25 0.15 0.5</float_array>
        <technique_common><accessor source="#w-a" count="7" stride="1"/></technique_common></source>
      <joints><input semantic="JOINT" source="#joints"/><input semantic="INV_BIND_MATRIX" source="#ibm"/></joints>
      <vertex_weights count="4">
        <input semantic="JOINT" source="#joints" offset="0"/><input semantic="WEIGHT" source="#w" offset="1"/>
        <vcount>1 5 2 0</vcount><v>0 0  0 1 1 2 2 3 3 4 4 5  -1 6 1 6</v>
      </vertex_weights>
    </skin></controller>
  </library_controllers>
  <library_visual_scenes><visual_scene id="Scene">
    <node id="Armature" name="Armature">
      <node id="Root" sid="Root" name="Root" type="JOINT"><translate>0 0 0</translate>
        <node id="Arm" sid="Arm" name="Arm" type="JOINT"><translate>0 0 25</translate><rotate>1 0 0 30</rotate>
          <node id="Hand" sid="Hand" name="Hand" type="JOINT"><translate>0 0 25</translate>
            <node id="Sword" name="Sword"><instance_geometry url="#prop"><bind_material><technique_common>
              <instance_material symbol="plainSym" target="#plain-mat"/></technique_common></bind_material>
            </instance_geometry></node>
            <node id="Tip" sid="Tip" name="Tip" type="JOINT"><translate>0 0 25</translate>
              <node id="Nub" sid="Nub" name="Nub" type="JOINT"><translate>0 0 25</translate></node>
            </node>
          </node>
        </node>
      </node>
      <node id="Body" name="Body"><instance_controller url="#body-skin"><skeleton>#Root</skeleton>
        <bind_material><technique_common><instance_material symbol="skinSym" target="#skin-mat"/>
        </technique_common></bind_material></instance_controller></node>
    </node>
    <node id="Floor" name="Floor"><matrix>-1 0 0 10 0 1 0 0 0 0 1 0 0 0 0 1</matrix>
      <instance_geometry url="#prop"><bind_material><technique_common>
        <instance_material symbol="plainSym" target="#plain-mat"/></technique_common></bind_material>
      </instance_geometry></node>
  </visual_scene></library_visual_scenes>
  <scene><instance_visual_scene url="#Scene"/></scene>
</COLLADA>
""".replace("%IBMS%", " ".join(ibm_row(25 * j) for j in range(5)))

DAE15 = """<?xml version="1.0"?>
<COLLADA xmlns="http://www.collada.org/2008/03/COLLADASchema" version="1.5.0">
  <asset><unit meter="1"/><up_axis>Y_UP</up_axis></asset>
  <library_images>
    <image id="img1"><init_from><ref>pics/a.png</ref></init_from></image>
    <image id="img2" name="inside"><init_from><hex format="PNG">89504E470D0A1A0A</hex></init_from></image>
  </library_images>
  <library_effects><effect id="fx"><profile_COMMON>
    <newparam sid="samp"><sampler2D><instance_image url="#img1"/></sampler2D></newparam>
    <technique sid="t"><blinn><diffuse><texture texture="samp" texcoord="UV0"/></diffuse>
      <emission><texture texture="img2" texcoord="UV0"/></emission></blinn></technique>
  </profile_COMMON></effect></library_effects>
  <library_materials><material id="m"><instance_effect url="#fx"/></material></library_materials>
  <library_geometries><geometry id="tri"><mesh>
    <source id="p"><float_array id="pa" count="9">0 0 0 1 0 0 0 1 0</float_array>
      <technique_common><accessor source="#pa" count="3" stride="3"/></technique_common></source>
    <source id="n"><float_array id="na" count="3">0 0 1</float_array>
      <technique_common><accessor source="#na" count="1" stride="3"/></technique_common></source>
    <source id="c"><float_array id="ca" count="9">1 0 0 0 1 0 0 0 1</float_array>
      <technique_common><accessor source="#ca" count="3" stride="3"/></technique_common></source>
    <source id="t0"><float_array id="t0a" count="6">0 0 1 0 0 1</float_array>
      <technique_common><accessor source="#t0a" count="3" stride="2"/></technique_common></source>
    <source id="t1"><float_array id="t1a" count="6">0 0.25 0.5 0.25 0 0.75</float_array>
      <technique_common><accessor source="#t1a" count="3" stride="2"/></technique_common></source>
    <vertices id="v"><input semantic="POSITION" source="#p"/><input semantic="COLOR" source="#c"/></vertices>
    <triangles material="mm" count="1">
      <input semantic="VERTEX" source="#v" offset="0"/><input semantic="NORMAL" source="#n" offset="1"/>
      <input semantic="TEXCOORD" source="#t1" offset="2" set="1"/>
      <input semantic="TEXCOORD" source="#t0" offset="2" set="0"/>
      <p>0 0 0 1 0 1 2 0 2</p></triangles>
  </mesh></geometry></library_geometries>
  <library_visual_scenes><visual_scene id="vs">
    <node name="Moved"><translate>1 2 3</translate><scale>2 2 2</scale>
      <instance_geometry url="#tri"><bind_material><technique_common>
        <instance_material symbol="mm" target="#m"/></technique_common></bind_material></instance_geometry></node>
  </visual_scene></library_visual_scenes>
  <scene><instance_visual_scene url="#vs"/></scene>
</COLLADA>
"""


def close(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def tri_normal(m, t):
    a, b, c = (m.positions[i] for i in t)
    u = [b[k] - a[k] for k in range(3)]
    v = [c[k] - a[k] for k in range(3)]
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


class Dae14(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "rig.dae"
        cls.path.write_text(DAE14, encoding="utf-8")
        cls.sc = dae.read(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_valid(self):
        self.assertEqual(validate(self.sc), [])
        self.assertEqual(self.sc.meta["source_format"], "dae")
        self.assertEqual((self.sc.meta["units"], self.sc.meta["up"]), ("m", "Y"))
        self.assertEqual(self.sc.meta["licence"], {"author": "Made Up", "copyright": "CC0", "source": "dae",
                                                   "known": False, "may_share": False})
        self.assertEqual(self.sc.meta["share"], "private")

    def test_skeleton_rest_is_bind(self):
        sk = self.sc.skeleton
        self.assertEqual([b.name for b in sk.bones], ["Root", "Arm", "Hand", "Tip", "Nub"])
        self.assertEqual([b.parent for b in sk.bones], [-1, 0, 1, 2, 3])
        worlds = sk.world_matrices()
        for j, (b, w) in enumerate(zip(sk.bones, worlds)):
            # 25 cm per joint along Z up -> 0.25 m along Y, no turn (the scene's 30 degree pose is not the rest)
            self.assertTrue(close(w[12:15], (0.0, 0.25 * j, 0.0)), (b.name, w[12:15]))
            self.assertTrue(close(b.rotation, (0.0, 0.0, 0.0, 1.0)), b.rotation)
            self.assertTrue(mat_close(mat_mul(w, b.inverse_bind), IDENTITY), b.name)
        self.assertTrue(any("bind matrices" in n for n in self.sc.meta["notes"]))

    def test_skinned_quad(self):
        body = next(m for m in self.sc.meshes if m.name == "Body")
        self.assertEqual(len(body.triangles), 2)
        self.assertEqual(len(body.positions), 4)
        top = [i for i, p in enumerate(body.positions) if abs(p[1] - 1.0) < 1e-9]
        self.assertEqual(len(top), 2, "100 cm Z up -> 1 m Y up")
        for t in body.triangles:
            self.assertGreater(tri_normal(body, t)[2], 0, "the quad faces +Z after the turn")
        for n in body.normals:
            self.assertTrue(close(n, (0.0, 0.0, 1.0)))
        i0 = body.positions.index((0.0, 0.0, 0.0))
        self.assertEqual(body.uvs[0][i0], (0.0, 1.0))
        # weights: vertex order follows the corners 0..3
        self.assertEqual(body.joints[0], (0, 0, 0, 0))
        self.assertEqual(body.weights[0], (1.0, 0.0, 0.0, 0.0))
        self.assertEqual(body.joints[1], (2, 3, 1, 4))  # Hand .3, Tip .25, Arm .2, Nub .15; Root .1 cut
        self.assertTrue(close(body.weights[1], (0.3 / 0.9, 0.25 / 0.9, 0.2 / 0.9, 0.15 / 0.9)))
        self.assertEqual(body.joints[2][0], 1)  # the -1 (bind shape) influence is skipped
        self.assertAlmostEqual(body.weights[2][0], 1.0)
        self.assertEqual(body.joints[3], (0, 0, 0, 0))
        notes = " ".join(self.sc.meta["notes"])
        self.assertIn("weights past the top 4", notes)
        self.assertIn("unweighted vertices", notes)
        self.assertIn("lines dropped", notes)

    def test_material_and_textures(self):
        body = next(m for m in self.sc.meshes if m.name == "Body")
        mat = self.sc.materials[body.material]
        self.assertEqual(mat.name, "Skin")
        self.assertEqual(mat.alpha_mode, "BLEND")
        self.assertAlmostEqual(mat.base_color[3], 0.5)
        self.assertTrue(mat.double_sided)
        self.assertTrue(close(mat.emissive, (0.1, 0.2, 0.3)))
        base = self.sc.textures[mat.textures["base"]]
        self.assertEqual(Path(base.path), self.path.parent / "tex dir" / "skin.png")
        self.assertEqual(base.mime, "image/png")
        self.assertEqual(Path(self.sc.textures[mat.textures["normal"]].path).name, "n.png")
        plain = self.sc.materials[next(m for m in self.sc.meshes if m.name == "Floor").material]
        self.assertEqual(plain.name, "Plain")
        self.assertEqual(plain.textures, {})
        self.assertTrue(close(plain.base_color, (0.5, 0.25, 0.125, 1.0)))
        self.assertEqual(len(self.sc.materials), 2, "one material per id, shared by meshes")

    def test_mirrored_concave_floor(self):
        floor = next(m for m in self.sc.meshes if m.name == "Floor")
        self.assertEqual(len(floor.triangles), 3)
        area = 0.0
        for t in floor.triangles:
            n = tri_normal(floor, t)
            self.assertGreater(n[1], 0, "a mirror keeps the faces pointing up (winding flipped back)")
            area += n[1] / 2
        self.assertAlmostEqual(area, (16 - 4) * 1e-4)  # 4x4 cm minus the 2 cm notch, in m^2
        xs = sorted(p[0] for p in floor.positions)
        self.assertAlmostEqual(xs[0], 0.06)
        self.assertAlmostEqual(xs[-1], 0.10)
        self.assertEqual(floor.joints, [])
        for n in floor.normals:
            self.assertTrue(close(n, (0.0, 1.0, 0.0)))

    def test_static_mesh_under_a_joint_is_rigid(self):
        sword = next(m for m in self.sc.meshes if m.name == "Sword")
        self.assertEqual(set(sword.joints), {(2, 0, 0, 0)})
        self.assertEqual(set(sword.weights), {(1.0, 0.0, 0.0, 0.0)})
        # it follows Hand into the rest pose (0.5 m up), not the 30 degree pose the file was saved in
        for p in sword.positions:
            self.assertAlmostEqual(p[1], 0.5)


class Dae15(unittest.TestCase):
    def test_15_basics(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "tri.dae"
            p.write_text(DAE15, encoding="utf-8")
            sc = dae.read(p)
            self.assertEqual(validate(sc), [])
            self.assertEqual(sc.meta["collada_version"], "1.5.0")
            m = sc.meshes[0]
            self.assertEqual(m.name, "Moved")
            self.assertTrue(close(m.positions[1], (3.0, 2.0, 3.0)))
            self.assertEqual(m.colors[0], (1.0, 0.0, 0.0, 1.0))
            self.assertEqual(len(m.uvs), 2)
            self.assertEqual(m.uvs[0][1], (1.0, 1.0))
            self.assertEqual(m.uvs[1][0], (0.0, 0.75))
            mat = sc.materials[m.material]
            self.assertEqual(Path(sc.textures[mat.textures["base"]].path), Path(tmp) / "pics" / "a.png")
            emis = sc.textures[mat.textures["emissive"]]
            self.assertEqual(emis.data, b"\x89PNG\r\n\x1a\n")
            self.assertEqual(emis.mime, "image/png")
            self.assertEqual(emis.name, "inside")
            self.assertEqual(len(sc.skeleton.bones), 0)

    def test_no_scene_and_x_up(self):
        doc = DAE15.replace("<up_axis>Y_UP</up_axis>", "<up_axis>X_UP</up_axis>")
        doc = doc[:doc.index("<library_visual_scenes>")] + "</COLLADA>"
        sc = dae.parse(doc.encode())
        self.assertEqual(validate(sc), [])
        m = sc.meshes[0]
        # X up: (1,0,0) is up -> (0,1,0); (0,1,0) is "left" -> (-1,0,0)
        self.assertTrue(close(m.positions[1], (0.0, 1.0, 0.0)))
        self.assertTrue(close(m.positions[2], (-1.0, 0.0, 0.0)))
        for t in m.triangles:
            n = tri_normal(m, t)
            self.assertTrue(close([x / math.sqrt(sum(c * c for c in n)) for x in n], m.normals[0]))

    def test_instance_node_reuses_a_library_node(self):
        doc = DAE15.replace("<library_visual_scenes>", """<library_nodes><node id="lib" name="Twin">
            <instance_geometry url="#tri"/></node></library_nodes><library_visual_scenes>""")
        doc = doc.replace("</visual_scene>", """<node name="A"><translate>10 0 0</translate>
            <instance_node url="#lib"/></node><node name="B"><instance_node url="#lib"/></node></visual_scene>""")
        sc = dae.parse(doc.encode())
        self.assertEqual(validate(sc), [])
        twins = [m for m in sc.meshes if m.name == "Twin"]
        self.assertEqual(len(twins), 2)
        self.assertEqual(sorted(m.positions[1][0] for m in twins), [1.0, 11.0])
        self.assertIsNone(twins[0].material)

    def test_broken_files_say_one_plain_sentence(self):
        with self.assertRaisesRegex(ReadError, "damaged"):
            dae.parse(b"<COLLADA><asset>")
        with self.assertRaisesRegex(ReadError, "isn't a COLLADA"):
            dae.parse(b"<svg/>")
        with self.assertRaisesRegex(ReadError, "no meshes"):
            dae.parse(b'<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1"/>')
        with self.assertRaisesRegex(ReadError, "broken numbers"):
            dae.parse(DAE15.replace("0 0 0 1 0 0 0 1 0</float_array>", "0 0 zero</float_array>").encode())

    def test_load_by_extension(self):
        import model_in
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "tri.dae"
            p.write_text(DAE15, encoding="utf-8")
            self.assertEqual(len(model_in.load(p).meshes), 1)


# Made up the way many exporters write it: IDREF joints, matrix transforms, a bind shape offset, no <skeleton>,
# and strips, fans and a polygon with a hole.
DAE_IDREF = """<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset><unit meter="1"/><up_axis>Z_UP</up_axis></asset>
  <library_animations><animation id="a"/></library_animations>
  <library_geometries><geometry id="g" name="strip"><mesh>
    <source id="p"><float_array id="pa" count="18">0 0 0 1 0 0 0 0 1 1 0 1 0 0 2 1 0 2</float_array>
      <technique_common><accessor source="#pa" count="6" stride="3"/></technique_common></source>
    <vertices id="v"><input semantic="POSITION" source="#p"/></vertices>
    <tristrips count="1"><input semantic="VERTEX" source="#v" offset="0"/><p>0 1 2 3 4 5</p></tristrips>
    <trifans count="1"><input semantic="VERTEX" source="#v" offset="0"/><p>0 1 3 2</p></trifans>
    <polygons count="1"><input semantic="VERTEX" source="#v" offset="0"/>
      <ph><p>0 1 5 4</p><h>2 3 3</h></ph></polygons>
  </mesh></geometry></library_geometries>
  <library_controllers><controller id="c"><skin source="#g">
    <bind_shape_matrix>1 0 0 0 0 1 0 0 0 0 1 1 0 0 0 1</bind_shape_matrix>
    <source id="jn"><IDREF_array id="jna" count="2">Bone_A Bone_B</IDREF_array>
      <technique_common><accessor source="#jna" count="2"/></technique_common></source>
    <source id="ib"><float_array id="iba" count="32">1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1
      1 0 0 0 0 1 0 0 0 0 1 -2 0 0 0 1</float_array>
      <technique_common><accessor source="#iba" count="2" stride="16"/></technique_common></source>
    <source id="w"><float_array id="wa" count="1">1</float_array>
      <technique_common><accessor source="#wa" count="1"/></technique_common></source>
    <joints><input semantic="JOINT" source="#jn"/><input semantic="INV_BIND_MATRIX" source="#ib"/></joints>
    <vertex_weights count="6"><input semantic="JOINT" source="#jn" offset="0"/>
      <input semantic="WEIGHT" source="#w" offset="1"/>
      <vcount>1 1 1 1 1 1</vcount><v>0 0 0 0 0 0 0 0 1 0 1 0</v></vertex_weights>
  </skin></controller></library_controllers>
  <library_visual_scenes><visual_scene id="s">
    <node id="Armature" name="Armature"><matrix>1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</matrix>
      <node id="Bone_A" name="A" sid="A" type="JOINT"><matrix sid="transform">1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</matrix>
        <node id="Bone_B" name="B" sid="B" type="JOINT"><matrix sid="transform">1 0 0 0 0 1 0 0 0 0 1 2 0 0 0 1</matrix>
        </node></node></node>
    <node id="Mesh" name="Mesh"><matrix>1 0 0 5 0 1 0 0 0 0 1 0 0 0 0 1</matrix>
      <instance_controller url="#c"/></node>
  </visual_scene></library_visual_scenes>
  <scene><instance_visual_scene url="#s"/></scene>
</COLLADA>
"""


class DaeExporterStyle(unittest.TestCase):
    def test_idref_joints_bind_shape_and_strips(self):
        sc = dae.parse(DAE_IDREF.encode())
        self.assertEqual(validate(sc), [])
        self.assertEqual([b.name for b in sc.skeleton.bones], ["A", "B"])
        self.assertTrue(close(sc.skeleton.world_matrices()[1][12:15], (0.0, 2.0, 0.0)))
        self.assertEqual(len(sc.meshes), 3)
        strip, fan, holed = sc.meshes
        self.assertEqual(len(strip.triangles), 4)
        self.assertEqual(len(fan.triangles), 2)
        self.assertEqual(len(holed.triangles), 2)
        for m in sc.meshes:
            for t in m.triangles:
                self.assertGreater(tri_normal(m, t)[2], 0, "strip and fan corners keep one winding")
        # bind shape lifts the strip 1 m; the node's own offset (x = 5) is ignored for a skin, as the spec says
        ys = sorted({round(p[1], 9) for p in strip.positions})
        self.assertEqual(ys, [1.0, 2.0, 3.0])
        self.assertEqual(max(p[0] for p in strip.positions), 1.0)
        top = [k for k, p in enumerate(strip.positions) if abs(p[1] - 3.0) < 1e-9]
        self.assertEqual({strip.joints[k][0] for k in top}, {1})
        notes = " ".join(sc.meta["notes"])
        self.assertIn("polygon holes dropped", notes)
        self.assertIn("Animations were not imported", notes)


if __name__ == "__main__":
    unittest.main()
