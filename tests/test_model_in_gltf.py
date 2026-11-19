"""tools/model_in (scene.py + gltf.py): tiny made-up glTF / GLB / VRM files built here, no real models, no game.

- Scene.validate catches broken index ranges, weights, numbers, parent order; the matrix helpers round-trip.
- A skinned triangle strip with 3 bones (+ a non-joint armature parent): skeleton from the skin, joints mapped
  through the skin's joint order, normalized byte weights, a sparse position, a morph kept in meta, and the
  bind -> rest fix (vertices move when the file's bind pose differs; fix_bind=False keeps the file's).
- A textured quad: an embedded PNG, two UV sets (one normalized ushort), normalized byte normals, vertex colours,
  a node matrix applied, KHR_texture_transform kept, material fields; .gltf with external files and data URIs.
- VRM 0.x and 1.0: humanoid map, licence fields, MToon shade colour/textures, facing, thumbnails.
"""

import base64
import json
import math
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import model_in  # noqa: E402
from model_in import gltf  # noqa: E402
from model_in.scene import (IDENTITY, Bone, Material, Mesh, ReadError, Scene, Skeleton, Texture,  # noqa: E402
                            mat_close, mat_from_trs, mat_inverse, mat_mul, mat_point, normalize, top4,
                            trs_from_mat)

FMT = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


class Builder:
    """Packs a glTF document and its binary buffer, the way an exporter would."""

    def __init__(self):
        self.bin = bytearray()
        self.doc = {"asset": {"version": "2.0", "generator": "test"}, "buffers": [], "bufferViews": [],
                    "accessors": [], "nodes": [], "meshes": [], "scenes": [{"nodes": []}], "scene": 0}

    def view(self, data: bytes, stride=None) -> int:
        self.bin += b"\0" * (-len(self.bin) % 4)
        bv = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if stride:
            bv["byteStride"] = stride
        self.bin += data
        self.doc["bufferViews"].append(bv)
        return len(self.doc["bufferViews"]) - 1

    def acc(self, values, ctype, atype, normalized=False, raw=None) -> int:
        flat = [c for v in values for c in (v if isinstance(v, (tuple, list)) else (v,))]
        data = raw if raw is not None else struct.pack(f"<{len(flat)}{FMT[ctype]}", *flat)
        a = {"bufferView": self.view(data), "componentType": ctype, "type": atype,
             "count": len(flat) // NC[atype] if raw is None else len(values)}
        if normalized:
            a["normalized"] = True
        if atype == "VEC3" and ctype == 5126 and raw is None:
            a["min"] = [min(v[i] for v in values) for i in range(3)]
            a["max"] = [max(v[i] for v in values) for i in range(3)]
        self.doc["accessors"].append(a)
        return len(self.doc["accessors"]) - 1

    def node(self, **kw) -> int:
        self.doc["nodes"].append(kw)
        return len(self.doc["nodes"]) - 1

    def glb(self) -> bytes:
        doc = json.loads(json.dumps(self.doc))
        doc["buffers"] = [{"byteLength": len(self.bin)}]
        js = json.dumps(doc).encode()
        js += b" " * (-len(js) % 4)
        bn = bytes(self.bin) + b"\0" * (-len(self.bin) % 4)
        body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(bn), 0x004E4942) + bn
        return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body

    def gltf(self, uri=None) -> bytes:
        doc = json.loads(json.dumps(self.doc))
        if uri is None:
            uri = "data:application/octet-stream;base64," + base64.b64encode(bytes(self.bin)).decode()
        doc["buffers"] = [{"byteLength": len(self.bin), "uri": uri}]
        return json.dumps(doc).encode()


def png(w=2, h=2, rgba=(255, 0, 0, 255)) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\0" + bytes(rgba) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def close(a, b, places=5):
    return all(abs(x - y) < 10 ** -places for x, y in zip(a, b)) and len(a) == len(b)


ROT_Z90 = (0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5))


def strip_model(bind_rot2=ROT_Z90):
    """Armature (not a joint) -> root -> b1 -> b2 (b2 turned 90 degrees about Z); an 8-vertex strip skinned to them.

    The skin lists its joints as [b2, b1, root], so joint 0 in JOINTS_0 means b2. bind_rot2 is b2's rotation in
    the file's bind pose (the inverse bind matrices); ROT_Z90 = bind pose is the rest pose."""
    b = Builder()
    pos = [(-0.5 if i % 2 == 0 else 0.5, float(i // 2), 0.0) for i in range(8)]
    stored = list(pos)
    stored[7] = (9.0, 9.0, 9.0)  # wrong in the main data, fixed by the sparse part
    p_acc = b.acc(stored, 5126, "VEC3")
    sp_idx = b.view(struct.pack("<H", 7))
    sp_val = b.view(struct.pack("<3f", *pos[7]))
    b.doc["accessors"][p_acc]["sparse"] = {"count": 1, "indices": {"bufferView": sp_idx, "componentType": 5123},
                                           "values": {"bufferView": sp_val}}
    # joints: 0=b2, 1=b1, 2=root
    joints = [(2, 0, 0, 0), (2, 0, 0, 0), (1, 2, 0, 0), (1, 2, 0, 0), (1, 0, 0, 0), (1, 0, 0, 0),
              (0, 1, 0, 0), (0, 0, 0, 0)]
    weights = [(255, 0, 0, 0), (255, 0, 0, 0), (128, 127, 0, 0), (128, 127, 0, 0), (255, 0, 0, 0),
               (255, 0, 0, 0), (200, 55, 0, 0), (255, 0, 0, 0)]
    j_acc = b.acc(joints, 5121, "VEC4")
    w_acc = b.acc(weights, 5121, "VEC4", normalized=True)
    # morph: no bufferView, one sparse delta on vertex 1
    m_idx = b.view(struct.pack("<I", 1))
    m_val = b.view(struct.pack("<3f", 0.0, 0.5, 0.0))
    b.doc["accessors"].append({"componentType": 5126, "type": "VEC3", "count": 8,
                               "sparse": {"count": 1, "indices": {"bufferView": m_idx, "componentType": 5125},
                                          "values": {"bufferView": m_val}}})
    morph_acc = len(b.doc["accessors"]) - 1
    i_acc = b.acc(list(range(8)), 5123, "SCALAR")
    b.doc["meshes"].append({"name": "Strip", "extras": {"targetNames": ["Smile"]},
                            "primitives": [{"attributes": {"POSITION": p_acc, "JOINTS_0": j_acc, "WEIGHTS_0": w_acc},
                                            "indices": i_acc, "mode": 5, "targets": [{"POSITION": morph_acc}]}]})
    arm = b.node(name="Armature", children=[1, 4])
    root = b.node(name="root", children=[2])
    b1 = b.node(name="b1", translation=[0, 1, 0], children=[3])
    b2 = b.node(name="b2", translation=[0, 1, 0], rotation=list(ROT_Z90))
    b.node(name="StripMesh", mesh=0, skin=0, translation=[5, 5, 5])  # ignored: skinned
    b.doc["scenes"][0]["nodes"] = [arm]
    bind_world = {root: IDENTITY, b1: mat_from_trs((0, 1, 0), (0, 0, 0, 1), (1, 1, 1))}
    bind_world[b2] = mat_mul(bind_world[b1], mat_from_trs((0, 1, 0), bind_rot2, (1, 1, 1)))
    ibms = [mat_inverse(bind_world[n]) for n in (b2, b1, root)]
    ibm_acc = b.acc([list(m) for m in ibms], 5126, "MAT4")
    b.doc["skins"] = [{"joints": [b2, b1, root], "inverseBindMatrices": ibm_acc, "skeleton": root}]
    return b, pos


class SceneContract(unittest.TestCase):
    def good(self):
        sk = Skeleton([Bone("a"), Bone("b", parent=0, translation=(0, 1, 0))])
        mesh = Mesh("m", positions=[(0, 0, 0), (1, 0, 0), (0, 1, 0)], normals=[(0, 0, 1)] * 3,
                    uvs=[[(0, 0), (1, 0), (0, 1)]], joints=[(0, 1, 0, 0)] * 3, weights=[(0.5, 0.5, 0, 0)] * 3,
                    triangles=[(0, 1, 2)], material=0)
        return Scene([mesh], sk, [Material("mat", textures={"base": 0})], [Texture("t", "image/png", b"x")],
                     {"humanoid": {"hips": 0}})

    def test_good_scene_passes(self):
        self.assertEqual(self.good().validate(), [])
        self.assertIs(self.good().check().__class__, Scene)

    def test_each_problem_is_caught(self):
        cases = [
            (lambda s: s.meshes[0].triangles.append((0, 1, 3)), "triangle 1"),
            (lambda s: s.meshes[0].weights.__setitem__(1, (0.5, 0.2, 0, 0)), "sum to 0.7000"),
            (lambda s: s.meshes[0].positions.__setitem__(2, (0, float("nan"), 0)), "position 2"),
            (lambda s: s.meshes[0].joints.__setitem__(0, (0, 7, 0, 0)), "outside 0..1"),
            (lambda s: setattr(s.skeleton.bones[0], "parent", 1), "doesn't come before"),
            (lambda s: setattr(s.skeleton.bones[1], "rotation", (0, 0, 0, 2)), "unit quaternion"),
            (lambda s: setattr(s.meshes[0], "material", 3), "material 3"),
            (lambda s: s.materials[0].textures.__setitem__("normal", 5), "normal texture 5"),
            (lambda s: s.meshes[0].normals.pop(), "2 normals for 3"),
            (lambda s: s.meta["humanoid"].__setitem__("head", 9), "Humanoid bone head"),
            (lambda s: setattr(s.textures[0], "data", None), "no picture"),
        ]
        for spoil, words in cases:
            s = self.good()
            spoil(s)
            problems = s.validate()
            self.assertTrue(any(words in p for p in problems), (words, problems))
            with self.assertRaises(ReadError):
                s.check()

    def test_math_round_trips(self):
        q = normalize((0.1, 0.7, -0.2, 0.6))
        m = mat_from_trs((1, -2, 3), q, (2, 3, 4))
        t, r, s = trs_from_mat(m)
        self.assertTrue(close(t, (1, -2, 3)) and close(r, q) and close(s, (2, 3, 4)))
        self.assertTrue(mat_close(mat_mul(m, mat_inverse(m)), IDENTITY))
        self.assertTrue(close(mat_point(m, (0, 0, 0)), (1, -2, 3)))

    def test_top4(self):
        js, ws = top4([(1, 0.1), (2, 0.4), (3, 0.2), (4, 0.2), (5, 0.1)])
        self.assertEqual(js[:2], (2, 3) if js[1] == 3 else (2, 4))
        self.assertAlmostEqual(sum(ws), 1.0)
        self.assertEqual(len(js), 4)
        self.assertIsNone(top4([(1, 0.0)]))
        self.assertEqual(top4([(3, 2.0)]), ((3, 0, 0, 0), (1.0, 0.0, 0.0, 0.0)))


class SkinnedStrip(unittest.TestCase):
    def test_strip_skeleton_weights_sparse_morph(self):
        b, pos = strip_model()
        s = gltf.read_bytes(b.glb())
        self.assertEqual(s.validate(), [])
        self.assertEqual(s.meta["source_format"], "glb")
        self.assertEqual((s.meta["units"], s.meta["up"], s.meta["forward"]), ("m", "Y", "+Z"))
        names = [x.name for x in s.skeleton.bones]
        self.assertEqual(names, ["Armature", "root", "b1", "b2"])
        self.assertEqual([x.parent for x in s.skeleton.bones], [-1, 0, 1, 2])
        b2 = s.skeleton.bones[3]
        self.assertTrue(close(b2.translation, (0, 1, 0)) and close(b2.rotation, ROT_Z90))
        worlds = s.skeleton.world_matrices()
        for bone, w in zip(s.skeleton.bones, worlds):
            self.assertTrue(mat_close(mat_mul(w, bone.inverse_bind), IDENTITY), bone.name)
        self.assertTrue(close(mat_point(worlds[3], (1, 0, 0)), (0, 3, 0)))
        (m,) = s.meshes
        self.assertEqual(m.name, "Strip")
        self.assertEqual(len(m.positions), 8)
        for got, want in zip(m.positions, pos):  # bind == rest: not moved; sparse fixed vertex 7
            self.assertTrue(close(got, want), (got, want))
        # strip: even (i, i+1, i+2), odd (i, i+2, i+1)
        self.assertEqual(m.triangles, [(0, 1, 2), (1, 3, 2), (2, 3, 4), (3, 5, 4), (4, 5, 6), (5, 7, 6)])
        # skin joint 0 = b2 = bone 3, 1 = b1 = bone 2, 2 = root = bone 1
        self.assertEqual(m.joints[0], (1, 0, 0, 0))
        self.assertEqual(m.joints[2][:2], (2, 1))
        self.assertAlmostEqual(m.weights[2][0], 128 / 255, places=6)
        self.assertAlmostEqual(m.weights[2][1], 127 / 255, places=6)
        self.assertEqual(m.joints[6][:2], (3, 2))
        self.assertAlmostEqual(m.weights[6][0], 200 / 255, places=6)
        for ws in m.weights:
            self.assertAlmostEqual(sum(ws), 1.0, places=6)
        self.assertEqual(len(m.normals), 8)  # made, since the file has none
        self.assertTrue(all(close(n, (0, 0, 1)) or close(n, (0, 0, -1)) for n in m.normals))
        (morph,) = s.meta["morphs"]
        self.assertEqual((morph["mesh"], morph["name"]), (0, "Smile"))
        self.assertTrue(close(morph["positions"][1], (0, 0.5, 0)))
        self.assertTrue(close(morph["positions"][0], (0, 0, 0)))
        self.assertTrue(any("morph" in n for n in s.meta["notes"]))
        self.assertIsNone(s.meta["licence"])

    def test_bind_pose_is_moved_to_rest(self):
        b, pos = strip_model(bind_rot2=(0.0, 0.0, 0.0, 1.0))  # the file was bound with b2 straight
        s = gltf.read_bytes(b.glb())
        self.assertEqual(s.validate(), [])
        m = s.meshes[0]
        # vertex 7 is all on b2: bound straight at (0.5, 3, 0) -> b2 local (0.5, 1, 0) -> rest pose turned 90deg
        rest_b2 = mat_mul(mat_from_trs((0, 1, 0), (0, 0, 0, 1), (1, 1, 1)),
                          mat_from_trs((0, 1, 0), ROT_Z90, (1, 1, 1)))
        self.assertTrue(close(m.positions[7], mat_point(rest_b2, (0.5, 1.0, 0.0))), m.positions[7])
        self.assertTrue(close(m.positions[0], pos[0]))  # on root: unchanged
        for bone, w in zip(s.skeleton.bones, s.skeleton.world_matrices()):
            self.assertTrue(mat_close(mat_mul(w, bone.inverse_bind), IDENTITY))
        self.assertTrue(any("rest pose" in n for n in s.meta["notes"]))
        raw = gltf.read_bytes(b.glb(), fix_bind=False)
        self.assertTrue(close(raw.meshes[0].positions[7], pos[7]))
        self.assertFalse(mat_close(raw.skeleton.bones[3].inverse_bind, mat_inverse(rest_b2)))

    def test_joints_outside_the_scene_keep_parents_first(self):
        b = Builder()
        p = b.acc([(0, 0, 0), (1, 0, 0), (0, 1, 0)], 5126, "VEC3")
        j = b.acc([(0, 0, 0, 0)] * 3, 5121, "VEC4")
        w = b.acc([(1.0, 0, 0, 0)] * 3, 5126, "VEC4")
        b.doc["meshes"].append({"primitives": [{"attributes": {"POSITION": p, "JOINTS_0": j, "WEIGHTS_0": w}}]})
        b.node(name="m", mesh=0, skin=0)
        b.node(name="child", translation=[0, 1, 0])
        b.node(name="top", children=[1])
        b.doc["skins"] = [{"joints": [1, 2]}]
        b.doc["scenes"][0]["nodes"] = [0]  # the bones hang outside the scene
        s = gltf.read_bytes(b.glb())
        self.assertEqual([x.name for x in s.skeleton.bones], ["top", "child"])
        self.assertEqual(s.meshes[0].joints[0], (1, 0, 0, 0))
        self.assertEqual(s.validate(), [])

    def test_more_than_four_weights_and_fans(self):
        b = Builder()
        p = b.acc([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], 5126, "VEC3")
        j0 = b.acc([(0, 1, 2, 3)] * 4, 5121, "VEC4")
        w0 = b.acc([(0.3, 0.2, 0.1, 0.1)] * 4, 5126, "VEC4")
        j1 = b.acc([(4, 0, 0, 0)] * 4, 5121, "VEC4")  # bone 0 again counts once (0.3 + 0.2)
        w1 = b.acc([(0.1, 0.2, 0, 0)] * 4, 5126, "VEC4")
        b.doc["meshes"].append({"primitives": [{"mode": 6, "attributes": {
            "POSITION": p, "JOINTS_0": j0, "WEIGHTS_0": w0, "JOINTS_1": j1, "WEIGHTS_1": w1}},
            {"mode": 1, "attributes": {"POSITION": p}}]})
        chain = [b.node(name=f"j{i}", children=[i + 1] if i < 4 else []) for i in range(5)]
        b.doc["skins"] = [{"joints": chain}]
        b.node(name="m", mesh=0, skin=0)
        b.doc["scenes"][0]["nodes"] = [0, 5]
        s = gltf.read_bytes(b.glb())
        self.assertEqual(s.validate(), [])
        (m,) = s.meshes
        self.assertEqual(m.triangles, [(1, 2, 0), (2, 3, 0)])
        self.assertEqual(m.joints[0][0], 0)
        self.assertAlmostEqual(m.weights[0][0], 0.5 / 0.9, places=6)
        self.assertEqual(sorted(m.joints[0][1:]), [1, 2, 4] if m.joints[0][3] != 3 else [1, 2, 3])
        self.assertAlmostEqual(sum(m.weights[0]), 1.0)
        self.assertTrue(any("line" in n for n in s.meta["notes"]))
        self.assertTrue(any("top 4" in n for n in s.meta["notes"]))


class TexturedQuad(unittest.TestCase):
    PNG = png()

    def quad(self, image_by_view=True):
        b = Builder()
        p = b.acc([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], 5126, "VEC3")
        n = b.acc([(0, 0, 127)] * 4, 5120, "VEC3", normalized=True)
        uv0 = b.acc([(0, 1), (1, 1), (1, 0), (0, 0)], 5126, "VEC2")
        uv1 = b.acc([(0, 65535), (65535, 65535), (65535, 0), (0, 0)], 5123, "VEC2", normalized=True)
        col = b.acc([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)], 5121, "VEC3", normalized=True)
        idx = b.acc([0, 1, 2, 0, 2, 3], 5121, "SCALAR")
        b.doc["meshes"].append({"name": "Quad", "primitives": [{"attributes": {
            "POSITION": p, "NORMAL": n, "TEXCOORD_0": uv0, "TEXCOORD_1": uv1, "COLOR_0": col},
            "indices": idx, "material": 0}]})
        if image_by_view:
            b.doc["images"] = [{"name": "red", "bufferView": b.view(self.PNG), "mimeType": "image/png"}]
        b.doc["samplers"] = [{"wrapS": 33071, "wrapT": 33648, "magFilter": 9729}]
        b.doc["textures"] = [{"source": 0, "sampler": 0}]
        b.doc["materials"] = [{
            "name": "Paint", "pbrMetallicRoughness": {
                "baseColorFactor": [0.5, 0.6, 0.7, 0.8], "metallicFactor": 0.1, "roughnessFactor": 0.9,
                "baseColorTexture": {"index": 0, "texCoord": 1, "extensions": {"KHR_texture_transform": {
                    "offset": [0.25, 0.5], "scale": [2, 2], "rotation": 0.5}}},
                "metallicRoughnessTexture": {"index": 0}},
            "normalTexture": {"index": 0, "scale": 0.7}, "emissiveTexture": {"index": 0},
            "emissiveFactor": [1, 0.5, 0], "alphaMode": "MASK", "alphaCutoff": 0.3, "doubleSided": True,
            "extensions": {"KHR_materials_emissive_strength": {"emissiveStrength": 2.0}}}]
        b.doc["extensionsUsed"] = ["KHR_texture_transform", "KHR_materials_emissive_strength"]
        mat = list(mat_from_trs((1, 2, 3), (0, 0, 0, 1), (2, 2, 2)))
        b.node(name="QuadNode", mesh=0, matrix=mat)
        b.doc["scenes"][0]["nodes"] = [0]
        return b

    def check_quad(self, s, embedded=True):
        self.assertEqual(s.validate(), [])
        (m,) = s.meshes
        self.assertEqual(m.name, "Quad")
        self.assertEqual(m.triangles, [(0, 1, 2), (0, 2, 3)])
        self.assertTrue(close(m.positions[2], (3, 4, 3)))  # node matrix: scale 2, move (1,2,3)
        self.assertTrue(all(close(v, (0, 0, 1)) for v in m.normals))
        self.assertEqual(len(m.uvs), 2)
        self.assertTrue(close(m.uvs[0][0], (0, 1)) and close(m.uvs[1][1], (1, 1)) and close(m.uvs[1][3], (0, 0)))
        self.assertTrue(close(m.colors[0], (1, 0, 0, 1)) and close(m.colors[3], (1, 1, 1, 1)))
        self.assertEqual(m.joints, [])
        self.assertEqual(m.material, 0)
        (mat,) = s.materials
        self.assertEqual(mat.name, "Paint")
        self.assertTrue(close(mat.base_color, (0.5, 0.6, 0.7, 0.8)))
        self.assertEqual(mat.textures, {"base": 0, "metallic_roughness": 0, "normal": 0, "emissive": 0})
        self.assertEqual(mat.texcoords["base"], 1)
        self.assertEqual(mat.extras["texture_transform"]["base"],
                         {"offset": (0.25, 0.5), "rotation": 0.5, "scale": (2, 2)})
        self.assertAlmostEqual(mat.extras["normal_scale"], 0.7)
        self.assertTrue(close(mat.emissive, (2, 1, 0)))
        self.assertEqual((mat.alpha_mode, mat.alpha_cutoff, mat.double_sided), ("MASK", 0.3, True))
        self.assertAlmostEqual(mat.metallic, 0.1)
        self.assertAlmostEqual(mat.roughness, 0.9)
        self.assertEqual(mat.shader, "pbr")
        (tex,) = s.textures
        self.assertEqual(tex.mime, "image/png")
        self.assertEqual(tex.read_bytes(), self.PNG)
        self.assertEqual(tex.extras["sampler"]["wrap_s"], 33071)
        if embedded:
            self.assertIsNotNone(tex.data)
        self.assertTrue(any("KHR_texture_transform" in n for n in s.meta["notes"]))

    def test_glb_with_embedded_png(self):
        s = gltf.read_bytes(self.quad().glb())
        self.check_quad(s)
        self.assertEqual(s.textures[0].name, "red")

    def test_gltf_with_data_uris(self):
        b = self.quad(image_by_view=False)
        b.doc["images"] = [{"uri": "data:image/png;base64," + base64.b64encode(self.PNG).decode()}]
        self.check_quad(gltf.read_bytes(b.gltf()))

    def test_gltf_with_external_files(self):
        b = self.quad(image_by_view=False)
        b.doc["images"] = [{"uri": "tex%20dir/red.png"}]
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "tex dir").mkdir()
            (Path(d) / "tex dir" / "red.png").write_bytes(self.PNG)
            (Path(d) / "quad.bin").write_bytes(bytes(b.bin))
            (Path(d) / "quad.gltf").write_bytes(b.gltf(uri="quad.bin"))
            s = model_in.load(Path(d) / "quad.gltf")
            self.check_quad(s, embedded=False)
            self.assertIsNone(s.textures[0].data)
            self.assertTrue(s.textures[0].path.endswith("red.png"))
            self.assertEqual(s.meta["source_format"], "gltf")
            (Path(d) / "quad.bin").unlink()
            with self.assertRaises(ReadError):
                model_in.load(Path(d) / "quad.gltf")

    def test_online_compressed_and_unknown_are_refused(self):
        b = self.quad()
        with self.assertRaisesRegex(ReadError, "never downloads"):
            gltf.read_bytes(b.gltf(uri="https://example.invalid/q.bin"), base_dir=".")
        b.doc["extensionsRequired"] = ["KHR_draco_mesh_compression"]
        with self.assertRaisesRegex(ReadError, "Draco"):
            gltf.read_bytes(b.glb())
        with self.assertRaises(ReadError):
            gltf.read_bytes(b"glTF" + struct.pack("<II", 1, 20) + b"\0" * 8)
        with self.assertRaisesRegex(ReadError, "can't read .xyz"):
            model_in.load("thing.xyz")

    def test_mirror_flips_winding_and_padded_matrix(self):
        b = self.quad()
        b.doc["nodes"][0] = {"mesh": 0, "scale": [-1, 1, 1]}
        s = gltf.read_bytes(b.glb())
        self.assertEqual(s.meshes[0].triangles, [(0, 2, 1), (0, 3, 2)])
        self.assertTrue(close(s.meshes[0].positions[1], (-1, 0, 0)))
        # MAT2 of bytes: each 2-byte column padded to 4
        b.doc["accessors"].append({"bufferView": b.view(bytes([1, 2, 0, 0, 3, 4, 0, 0])), "componentType": 5121,
                                   "type": "MAT2", "count": 1})
        doc = dict(json.loads(json.dumps(b.doc)), buffers=[{"byteLength": len(b.bin)}])
        r = gltf._Reader(doc, bytes(b.bin), None, "glb", True, None)
        self.assertEqual(r.accessor(len(b.doc["accessors"]) - 1), [(1, 2, 3, 4)])

    def test_unlit_basisu_and_the_check_command(self):
        b = self.quad()
        b.doc["materials"][0]["extensions"] = {"KHR_materials_unlit": {}}
        b.doc["textures"] = [{"extensions": {"KHR_texture_basisu": {"source": 0}}}]
        b.doc["extensionsRequired"] = ["KHR_texture_basisu", "EXT_made_up"]
        s = gltf.read_bytes(b.glb())
        self.assertEqual(s.materials[0].shader, "unlit")
        self.assertEqual(s.textures[0].read_bytes(), self.PNG)
        self.assertTrue(any("EXT_made_up" in n for n in s.meta["notes"]))
        self.assertFalse(any("KHR_texture_basisu" in n for n in s.meta["notes"]))
        import contextlib
        import io
        from model_in import __main__ as cli
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "quad.glb"
            f.write_bytes(self.quad().glb())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main([str(f)]), 0)
                self.assertEqual(cli.main([str(Path(d) / "missing.glb")]), 1)
            self.assertIn('"triangles": 2', out.getvalue())
            self.assertIn("can't read it", out.getvalue())

    def test_broken_files_say_so_in_one_sentence(self):
        b = self.quad()
        b.doc["accessors"][0]["count"] = 400  # runs past its buffer view
        with self.assertRaisesRegex(ReadError, "runs past"):
            gltf.read_bytes(b.glb())
        b = self.quad()
        del b.doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"]
        b.doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"] = "zero"
        with self.assertRaises(ReadError):
            gltf.read_bytes(b.glb())
        b = self.quad()
        b.doc["nodes"][0]["children"] = [0]  # loops onto itself
        with self.assertRaises(ReadError):
            gltf.read_bytes(b.glb())
        with self.assertRaisesRegex(ReadError, "JSON"):
            gltf.read_bytes(b"{not json")

    def test_deep_bone_chain(self):
        b = Builder()
        n = 1500
        for i in range(n):
            b.node(name=f"hair{i}", translation=[0, 0.01, 0], children=[i + 1] if i + 1 < n else [])
        b.doc["skins"] = [{"joints": list(range(n))}]
        b.doc["scenes"][0]["nodes"] = [0]
        s = gltf.read_bytes(b.glb())
        self.assertEqual(len(s.skeleton.bones), n)
        self.assertEqual(s.skeleton.bones[-1].parent, n - 2)
        self.assertEqual(s.validate(), [])
        self.assertAlmostEqual(s.skeleton.world_matrices()[-1][13], 15.0, places=6)

    def test_unskinned_mesh_under_a_bone_sticks_to_it(self):
        b, _ = strip_model()
        q = self.quad()
        # a hat: the quad's data in a second model, hung under b2 here
        off = len(b.bin) + (-len(b.bin) % 4)
        b.bin += b"\0" * (-len(b.bin) % 4) + q.bin
        nv = len(b.doc["bufferViews"])
        na = len(b.doc["accessors"])
        for v in q.doc["bufferViews"]:
            b.doc["bufferViews"].append(dict(v, byteOffset=v["byteOffset"] + off))
        for a in q.doc["accessors"]:
            b.doc["accessors"].append(dict(a, bufferView=a["bufferView"] + nv))
        prim = q.doc["meshes"][0]["primitives"][0]
        prim = {"attributes": {k: v + na for k, v in prim["attributes"].items()}, "indices": prim["indices"] + na}
        b.doc["meshes"].append({"name": "Hat", "primitives": [prim]})
        hat = b.node(name="Hat", mesh=1, translation=[0, 0.5, 0])
        b.doc["nodes"][3]["children"] = [hat]
        s = gltf.read_bytes(b.glb())
        self.assertEqual(s.validate(), [])
        hat_mesh = [m for m in s.meshes if m.name == "Hat"][0]
        self.assertEqual(hat_mesh.joints[0], (3, 0, 0, 0))
        self.assertEqual(hat_mesh.weights[0], (1.0, 0.0, 0.0, 0.0))
        # world: b2 at (0,2,0) turned 90deg about Z, hat 0.5 up its local Y -> (-0.5, 2, 0)
        self.assertTrue(close(hat_mesh.positions[0], (-0.5, 2, 0)), hat_mesh.positions[0])


def vrm_model(version):
    b = Builder()
    p = b.acc([(0, 0, 0), (1, 0, 0), (0, 1, 0)], 5126, "VEC3")
    b.doc["meshes"].append({"name": "Body", "primitives": [{"attributes": {"POSITION": p}, "material": 0}]})
    b.doc["images"] = [{"name": "thumb", "bufferView": b.view(png(1, 1)), "mimeType": "image/png"},
                       {"name": "skin", "bufferView": b.view(png(2, 2, (200, 150, 120, 255))),
                        "mimeType": "image/png"}]
    b.doc["textures"] = [{"source": 1}, {"source": 0}]
    hips = b.node(name="J_Hips", children=[1])
    head = b.node(name="J_Head", translation=[0, 0.6, 0])
    b.node(name="Body", mesh=0)
    b.doc["scenes"][0]["nodes"] = [hips, 2]
    if version == 0:
        b.doc["materials"] = [{"name": "Body_mat", "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]}}]
        b.doc["extensionsUsed"] = ["VRM"]
        b.doc["extensions"] = {"VRM": {
            "exporterVersion": "UniVRM-0.99", "specVersion": "0.0",
            "meta": {"title": "Test Girl", "version": "1", "author": "giruka", "contactInformation": "",
                     "reference": "", "texture": 1, "allowedUserName": "Everyone",
                     "violentUssageName": "Disallow", "sexualUssageName": "Disallow",
                     "commercialUssageName": "Allow", "otherPermissionUrl": "", "licenseName": "CC_BY",
                     "otherLicenseUrl": ""},
            "humanoid": {"humanBones": [{"bone": "hips", "node": hips}, {"bone": "head", "node": head}]},
            "materialProperties": [{"name": "Body_mat", "shader": "VRM/MToon",
                                    "floatProperties": {"_BlendMode": 1, "_Cutoff": 0.4, "_CullMode": 0},
                                    "vectorProperties": {"_Color": [1, 0.5, 0.5, 1], "_ShadeColor": [0.5, 0.5, 1, 1]},
                                    "textureProperties": {"_MainTex": 0, "_ShadeTexture": 0}}],
            "blendShapeMaster": {"blendShapeGroups": [{"name": "Joy", "presetName": "joy"},
                                                      {"name": "Wink2", "presetName": "unknown"}]},
            "secondaryAnimation": {"boneGroups": [{"bones": [head]}]}}}
    else:
        b.doc["materials"] = [{"name": "Body_mat", "pbrMetallicRoughness": {
            "baseColorFactor": [0.9, 0.8, 0.7, 1], "baseColorTexture": {"index": 0}},
            "extensions": {"VRMC_materials_mtoon": {"specVersion": "1.0", "shadeColorFactor": [0.5, 0.4, 0.3],
                                                    "shadeMultiplyTexture": {"index": 0},
                                                    "shadingToonyFactor": 0.9}}}]
        b.doc["extensionsUsed"] = ["VRMC_vrm", "VRMC_materials_mtoon", "VRMC_springBone"]
        b.doc["extensions"] = {
            "VRMC_vrm": {"specVersion": "1.0",
                         "meta": {"name": "Test Girl", "version": "1", "authors": ["giruka", "friend"],
                                  "licenseUrl": "https://vrm.dev/licenses/1.0/", "thumbnailImage": 0,
                                  "avatarPermission": "everyone", "commercialUsage": "personalProfit",
                                  "allowRedistribution": True, "modification": "allowModification",
                                  "creditNotation": "unnecessary"},
                         "humanoid": {"humanBones": {"hips": {"node": hips}, "head": {"node": head}}},
                         "expressions": {"preset": {"happy": {}}, "custom": {"wink2": {}}}},
            "VRMC_springBone": {"springs": [{"joints": [{"node": head}]}]}}
    return b


class Vrm(unittest.TestCase):
    def test_vrm0(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "girl.vrm"
            f.write_bytes(vrm_model(0).glb())
            s = model_in.load(f)
        self.assertEqual(s.validate(), [])
        self.assertEqual(s.meta["source_format"], "vrm0")
        self.assertEqual(s.meta["forward"], "-Z")
        self.assertEqual([b.name for b in s.skeleton.bones], ["J_Hips", "J_Head"])  # humanoid nodes are bones
        self.assertEqual(s.meta["humanoid"], {"hips": 0, "head": 1})
        lic = s.meta["licence"]
        self.assertEqual((lic["source"], lic["title"], lic["authors"]), ("vrm0", "Test Girl", ["giruka"]))
        self.assertEqual((lic["allowed_users"], lic["commercial"], lic["licence_name"]),
                         ("everyone", "allowed", "CC_BY"))
        self.assertEqual((lic["redistribution"], lic["modification"], lic["credit"], lic["may_share"]),
                         (True, "allowed_and_share", "required", True))
        self.assertFalse(lic["violent_ok"])
        self.assertEqual(s.meta["thumbnail_texture"], 1)
        self.assertEqual(s.meta["vrm"]["expressions"], ["joy", "Wink2"])
        self.assertEqual(s.meta["vrm"]["spring_bone_roots"], [1])
        (mat,) = s.materials
        self.assertEqual(mat.shader, "mtoon")
        self.assertTrue(close(mat.shade_color, (0.214041, 0.214041, 1.0)))  # sRGB 0.5 -> linear
        self.assertTrue(close(mat.base_color, (1.0, 0.214041, 0.214041, 1.0)))
        self.assertEqual(mat.textures, {"base": 0, "shade": 0})
        self.assertEqual((mat.alpha_mode, mat.alpha_cutoff, mat.double_sided), ("MASK", 0.4, True))
        self.assertEqual(s.textures[0].name, "skin")
        # a "no redistribution" licence is never shareable
        b = vrm_model(0)
        b.doc["extensions"]["VRM"]["meta"]["licenseName"] = "Redistribution_Prohibited"
        lic = gltf.read_bytes(b.glb()).meta["licence"]
        self.assertEqual((lic["redistribution"], lic["may_share"]), (False, False))
        b.doc["extensions"]["VRM"]["meta"]["licenseName"] = "CC_BY_NC_ND"
        lic = gltf.read_bytes(b.glb()).meta["licence"]
        self.assertEqual((lic["modification"], lic["commercial"], lic["may_share"]),
                         ("prohibited", "not_allowed", False))

    def test_vrm1(self):
        s = gltf.read_bytes(vrm_model(1).glb(), fmt="vrm")
        self.assertEqual(s.validate(), [])
        self.assertEqual((s.meta["source_format"], s.meta["forward"]), ("vrm1", "+Z"))
        self.assertEqual(s.meta["humanoid"], {"hips": 0, "head": 1})
        lic = s.meta["licence"]
        self.assertEqual((lic["source"], lic["title"], lic["authors"]), ("vrm1", "Test Girl", ["giruka", "friend"]))
        self.assertEqual((lic["allowed_users"], lic["commercial"], lic["redistribution"], lic["modification"]),
                         ("everyone", "personal_profit", True, "allowed"))
        self.assertEqual((lic["credit"], lic["licence_name"], lic["may_share"]),
                         ("unnecessary", "VRM Public License 1.0", False))
        self.assertEqual(lic["licence_url"], "https://vrm.dev/licenses/1.0/")
        self.assertEqual(s.meta["thumbnail_texture"], 1)
        self.assertEqual(s.meta["vrm"]["expressions"], ["happy", "wink2"])
        self.assertEqual(s.meta["vrm"]["spring_bone_roots"], [1])
        (mat,) = s.materials
        self.assertEqual(mat.shader, "mtoon")
        self.assertTrue(close(mat.shade_color, (0.5, 0.4, 0.3)))
        self.assertTrue(close(mat.base_color, (0.9, 0.8, 0.7, 1)))
        self.assertEqual(mat.textures, {"base": 0, "shade": 0})
        self.assertAlmostEqual(mat.extras["mtoon"]["shadingToonyFactor"], 0.9)
        # spec defaults when the meta leaves things out: private
        b = vrm_model(1)
        b.doc["extensions"]["VRMC_vrm"]["meta"] = {"name": "x", "authors": ["y"], "licenseUrl": "u"}
        lic = gltf.read_bytes(b.glb()).meta["licence"]
        self.assertEqual((lic["allowed_users"], lic["commercial"], lic["redistribution"], lic["modification"],
                          lic["may_share"]), ("author_only", "not_allowed", False, "prohibited", False))
        # a VRM 1.0 thumbnail no texture uses gets a texture of its own
        b = vrm_model(1)
        b.doc["textures"] = [{"source": 1}]
        s = gltf.read_bytes(b.glb())
        self.assertEqual(s.meta["thumbnail_texture"], 1)
        self.assertEqual(s.textures[1].name, "thumb")
        self.assertEqual(s.validate(), [])

    def test_plain_gltf_copyright_is_not_a_licence_to_share(self):
        b = TexturedQuad().quad()
        b.doc["asset"]["copyright"] = "(c) someone"
        lic = gltf.read_bytes(b.glb()).meta["licence"]
        self.assertEqual((lic["source"], lic["copyright"], lic["may_share"]), ("gltf", "(c) someone", False))


if __name__ == "__main__":
    unittest.main()
