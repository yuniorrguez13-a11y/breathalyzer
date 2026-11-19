"""COLLADA (.dae, 1.4 and 1.5) reader -> the neutral Scene (tools/model_in/scene.py).

Written from the public Khronos COLLADA 1.4.1 / 1.5.0 specifications with xml.etree, standard library only.

What it reads:
  * <asset> unit and up_axis -> metres, Y up, right-handed.
  * geometry: <triangles>, <polylist>, <polygons> (holes dropped), <trifans>, <tristrips>; inputs VERTEX
    (POSITION/NORMAL/... inside <vertices>), NORMAL, TEXCOORD (by set), COLOR; polygons are triangulated
    (fan when convex, ear clipping when concave).
  * materials: <instance_effect> -> profile_COMMON phong/blinn/lambert/constant: diffuse colour or texture,
    emission, transparency (alpha mode), bump/normal textures from <extra>, double_sided from <extra>;
    textures through newparam sampler2D/surface (1.4), <instance_image> (1.5) or a direct image id; images from
    a file (<init_from>, URI decoded) or embedded <hex>.
  * the visual scene: <node> transforms (matrix, translate, rotate, scale; lookat and skew dropped),
    <instance_geometry>, <instance_controller>, <instance_node>, <bind_material>.
  * <skin> controllers: bind_shape_matrix, JOINT names (sid, id or name), INV_BIND_MATRIX, vertex_weights ->
    one skeleton for the whole file; each vertex keeps its top 4 influences, renormalised.
Static meshes are baked into world space; a static mesh under a joint node is bound rigidly to that bone.
The rest pose is the bind pose (taken from the inverse bind matrices when the skin has them).
UVs are flipped to the glTF convention (v -> 1 - v).
"""
from __future__ import annotations

import binascii
import math
import os
import urllib.parse
import xml.etree.ElementTree as ET

from .obj import mime_of, triangulate
from .scene import Bone, Material, Mesh, ReadError, Scene, Skeleton, Texture, new_meta, read_file, smooth_normals

IDENT = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


# ------------------------------------------------------------------------------------------------- 4x4 maths

def mat(vals):
    v = [float(x) for x in vals]
    if len(v) < 16:
        v += [0.0] * (16 - len(v))
    return [v[0:4], v[4:8], v[8:12], v[12:16]]


def mul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def inv(m):
    a = [row[:] + [1.0 if r == c else 0.0 for c in range(4)] for r, row in enumerate(m)]
    for c in range(4):
        piv = max(range(c, 4), key=lambda r: abs(a[r][c]))
        if abs(a[piv][c]) < 1e-15:
            return [row[:] for row in IDENT]
        a[c], a[piv] = a[piv], a[c]
        p = a[c][c]
        a[c] = [x / p for x in a[c]]
        for r in range(4):
            if r != c and a[r][c] != 0.0:
                f = a[r][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
    return [row[4:] for row in a]


def translate(x, y, z):
    return [[1.0, 0.0, 0.0, x], [0.0, 1.0, 0.0, y], [0.0, 0.0, 1.0, z], [0.0, 0.0, 0.0, 1.0]]


def scale_m(x, y, z):
    return [[x, 0.0, 0.0, 0.0], [0.0, y, 0.0, 0.0], [0.0, 0.0, z, 0.0], [0.0, 0.0, 0.0, 1.0]]


def rotate(ax, ay, az, deg):
    n = math.sqrt(ax * ax + ay * ay + az * az)
    if n < 1e-20:
        return [row[:] for row in IDENT]
    x, y, z = ax / n, ay / n, az / n
    a = math.radians(deg)
    c, s, t = math.cos(a), math.sin(a), 1.0 - math.cos(a)
    return [[t * x * x + c, t * x * y - s * z, t * x * z + s * y, 0.0],
            [t * x * y + s * z, t * y * y + c, t * y * z - s * x, 0.0],
            [t * x * z - s * y, t * y * z + s * x, t * z * z + c, 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def point(m, p):
    return (m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + m[0][3],
            m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + m[1][3],
            m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + m[2][3])


def det3(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def normal_matrix(m):
    i = inv(m)
    return [[i[c][r] for c in range(3)] for r in range(3)]  # transpose of the inverse (3x3)


def _n3(nm, n):
    v = (nm[0][0] * n[0] + nm[0][1] * n[1] + nm[0][2] * n[2], nm[1][0] * n[0] + nm[1][1] * n[1] + nm[1][2] * n[2],
         nm[2][0] * n[0] + nm[2][1] * n[1] + nm[2][2] * n[2])
    ln = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / ln, v[1] / ln, v[2] / ln) if ln > 1e-20 else (0.0, 1.0, 0.0)


def quat_from(r):
    """Unit quaternion (x, y, z, w) from a 3x3 rotation (rows)."""
    tr = r[0][0] + r[1][1] + r[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = ((r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s, 0.25 * s)
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2
        q = (0.25 * s, (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s, (r[2][1] - r[1][2]) / s)
    elif r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2
        q = ((r[0][1] + r[1][0]) / s, 0.25 * s, (r[1][2] + r[2][1]) / s, (r[0][2] - r[2][0]) / s)
    else:
        s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2
        q = ((r[0][2] + r[2][0]) / s, (r[1][2] + r[2][1]) / s, 0.25 * s, (r[1][0] - r[0][1]) / s)
    n = math.sqrt(sum(x * x for x in q)) or 1.0
    q = tuple(x / n for x in q)
    return q if q[3] >= 0 else tuple(-x for x in q)


def decompose(m):
    """4x4 -> (translation, rotation quaternion xyzw, scale)."""
    t = (m[0][3], m[1][3], m[2][3])
    cols = [(m[0][c], m[1][c], m[2][c]) for c in range(3)]
    s = [math.sqrt(sum(x * x for x in col)) for col in cols]
    if det3(m) < 0:
        s[0] = -s[0]
    r = [[cols[c][rr] / s[c] if abs(s[c]) > 1e-20 else (1.0 if rr == c else 0.0) for c in range(3)]
         for rr in range(3)]
    return t, quat_from(r), (s[0], s[1], s[2])


def col_major(m):
    """Row-list 4x4 -> 16-tuple column-major (the Scene's matrix layout, like glTF)."""
    return tuple(float(m[r][c]) for c in range(4) for r in range(4))


def axis_matrix(up: str):
    up = (up or "Y_UP").upper()
    if up.startswith("Z"):
        return [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, -1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
    if up.startswith("X"):
        return [[0.0, -1.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
    return [row[:] for row in IDENT]


# ------------------------------------------------------------------------------------------------- XML helpers

def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _floats(text):
    return [float(x) for x in (text or "").split()]


def _ints(text):
    return [int(x) for x in (text or "").split()]


def _ref(url):
    return url[1:] if url and url.startswith("#") else url


class _Doc:
    def __init__(self, root, base_dir):
        self.root = root
        self.base_dir = base_dir
        self.ids = {}
        for el in root.iter():
            i = el.get("id")
            if i is not None and i not in self.ids:
                self.ids[i] = el
        self._sources = {}
        self.dropped = {}
        self.warnings = []

    def drop(self, what, n=1):
        self.dropped[what] = self.dropped.get(what, 0) + n

    def source(self, sid):
        """<source> -> list of tuples (stride items each), or list of names for Name/IDREF arrays."""
        sid = _ref(sid)
        if sid in self._sources:
            return self._sources[sid]
        el = self.ids.get(sid)
        out = []
        if el is not None and el.tag == "source":
            acc = el.find("technique_common/accessor")
            stride = int(acc.get("stride", "1")) if acc is not None else 1
            offset = int(acc.get("offset", "0")) if acc is not None else 0
            count = int(acc.get("count", "0")) if acc is not None else None
            arr = None
            for tag in ("float_array", "Name_array", "IDREF_array", "int_array", "SIDREF_array", "bool_array"):
                arr = el.find(tag)
                if arr is not None:
                    break
            if arr is not None:
                if arr.tag in ("float_array", "int_array"):
                    vals = _floats(arr.text)
                else:
                    vals = (arr.text or "").split()
                if stride == 1 and arr.tag not in ("float_array", "int_array"):
                    out = vals[offset:offset + count if count is not None else None]
                else:
                    n = count if count is not None else (len(vals) - offset) // stride
                    out = [tuple(vals[offset + i * stride: offset + (i + 1) * stride]) for i in range(n)]
        self._sources[sid] = out
        return out


# ------------------------------------------------------------------------------------------------- geometry

def _geometry(doc, geom):
    """<geometry> -> list of primitives in COLLADA space:
    dict(symbol, positions, normals, uvs[set][...], colors, tris, pos_index)."""
    mesh = geom.find("mesh")
    if mesh is None:
        doc.drop("non-mesh geometry (spline/brep)")
        return []
    verts = mesh.find("vertices")
    vert_inputs = {}
    if verts is not None:
        for inp in verts.findall("input"):
            vert_inputs[inp.get("semantic")] = doc.source(inp.get("source"))
    prims = []
    for prim in mesh:
        tag = prim.tag
        if tag in ("lines", "linestrips"):
            doc.drop("lines")
            continue
        if tag not in ("triangles", "polylist", "polygons", "trifans", "tristrips"):
            continue
        inputs = prim.findall("input")
        if not inputs:
            continue
        stride = max(int(i.get("offset", "0")) for i in inputs) + 1
        # polygons as lists of corners (each corner = tuple of indices, one per offset)
        polys = []
        if tag == "triangles":
            p = _ints(prim.findtext("p"))
            c = [tuple(p[i:i + stride]) for i in range(0, len(p) - stride + 1, stride)]
            polys = [c[i:i + 3] for i in range(0, len(c) - 2, 3)]
        elif tag == "polylist":
            p = _ints(prim.findtext("p"))
            at = 0
            for n in _ints(prim.findtext("vcount")):
                polys.append([tuple(p[(at + k) * stride:(at + k + 1) * stride]) for k in range(n)])
                at += n
        elif tag == "polygons":
            for pe in prim.findall("p"):
                p = _ints(pe.text)
                polys.append([tuple(p[i:i + stride]) for i in range(0, len(p) - stride + 1, stride)])
            for ph in prim.findall("ph"):
                p = _ints(ph.findtext("p"))
                polys.append([tuple(p[i:i + stride]) for i in range(0, len(p) - stride + 1, stride)])
                if ph.find("h") is not None:
                    doc.drop("polygon holes")
        else:
            for pe in prim.findall("p"):
                p = _ints(pe.text)
                c = [tuple(p[i:i + stride]) for i in range(0, len(p) - stride + 1, stride)]
                for i in range(1, len(c) - 1):
                    if tag == "trifans":
                        polys.append([c[0], c[i], c[i + 1]])
                    else:
                        j = i - 1
                        polys.append([c[j], c[j + 1], c[j + 2]] if j % 2 == 0 else [c[j + 1], c[j], c[j + 2]])
        # which offset feeds which attribute
        pos_off = None
        attr = []  # (kind, set, offset, data)
        own = {(i.get("semantic"), int(i.get("set", "0"))) for i in inputs}
        for inp in inputs:
            sem = inp.get("semantic")
            off = int(inp.get("offset", "0"))
            if sem == "VERTEX":
                pos_off = off
                for vsem, data in vert_inputs.items():
                    if vsem in ("NORMAL", "TEXCOORD", "COLOR") and (vsem, 0) not in own:
                        attr.append((vsem, 0, off, data))
            elif sem in ("NORMAL", "TEXCOORD", "COLOR"):
                attr.append((sem, int(inp.get("set", "0")), off, doc.source(inp.get("source"))))
        # one normal and one colour set (the lowest set), every UV set once
        keep, seen = [], set()
        for a in sorted(attr, key=lambda a: (a[0], a[1])):
            k = (a[0], a[1]) if a[0] == "TEXCOORD" else a[0]
            if k in seen:
                if a[0] == "COLOR":
                    doc.drop("extra vertex colour sets")
                continue
            seen.add(k)
            keep.append(a)
        attr = keep
        positions_src = vert_inputs.get("POSITION")
        if pos_off is None or not positions_src:
            continue
        uv_sets = sorted({s for k, s, _, _ in attr if k == "TEXCOORD"})
        key_map, keys, tris = {}, [], []
        for poly in polys:
            if len(poly) < 3:
                continue
            local = []
            for corner in poly:
                li = key_map.get(corner)
                if li is None:
                    li = key_map[corner] = len(keys)
                    keys.append(corner)
                local.append(li)
            pts = [tuple(positions_src[keys[li][pos_off]][:3]) for li in local]
            for a, b, c in triangulate(pts):
                tris.append((local[a], local[b], local[c]))
        out = {"symbol": prim.get("material"), "positions": [], "normals": [], "uvs": [[] for _ in uv_sets],
               "colors": [], "tris": tris, "pos_index": []}
        has_n = any(k == "NORMAL" for k, _, _, _ in attr)
        has_c = any(k == "COLOR" for k, _, _, _ in attr)
        for key in keys:
            pi = key[pos_off]
            out["pos_index"].append(pi)
            out["positions"].append(tuple(positions_src[pi][:3]))
            for kind, st, off, data in attr:
                idx = key[off]
                v = data[idx] if idx < len(data) else ()
                if kind == "NORMAL":
                    out["normals"].append(tuple(v[:3]) if len(v) >= 3 else (0.0, 1.0, 0.0))
                elif kind == "TEXCOORD":
                    u = v[0] if v else 0.0
                    w = v[1] if len(v) > 1 else 0.0
                    out["uvs"][uv_sets.index(st)].append((u, 1.0 - w))
                elif kind == "COLOR":
                    col = tuple(v[:4]) + (1.0,) * (4 - len(v[:4]))
                    out["colors"].append(col if len(v) >= 3 else (1.0, 1.0, 1.0, 1.0))
        if not has_n:
            out["normals"] = None
        if not has_c:
            out["colors"] = []
        if tris:
            prims.append(out)
    return prims


# ------------------------------------------------------------------------------------------------- materials

class _Mats:
    def __init__(self, doc):
        self.doc = doc
        self.materials = []
        self.textures = []
        self._mat_index = {}
        self._tex_index = {}

    def image(self, image_id):
        doc = self.doc
        img = doc.ids.get(image_id)
        if img is None or img.tag != "image":
            return None
        hexel = img.find(".//hex")
        if hexel is not None and (hexel.text or "").strip():
            key = "hex:" + image_id
            if key not in self._tex_index:
                fmt = (hexel.get("format") or "png").lower()
                try:
                    data = binascii.unhexlify("".join(hexel.text.split()))
                except (binascii.Error, ValueError):
                    doc.warnings.append("bad embedded image: %s" % image_id)
                    return None
                self._tex_index[key] = len(self.textures)
                self.textures.append(Texture(name=img.get("name") or image_id, mime=mime_of("x." + fmt),
                                             data=data, path=None))
            return self._tex_index[key]
        ref = img.findtext("init_from/ref") or img.findtext("init_from")
        if not ref or not ref.strip():
            return None
        ref = ref.strip()
        if ref.lower().startswith("file:"):
            ref = ref[5:]
            while ref.startswith("//"):
                ref = ref[1:]
            if len(ref) > 2 and ref[0] == "/" and ref[2] == ":":
                ref = ref[1:]  # /C:/...
        ref = urllib.parse.unquote(ref)
        full = os.path.normpath(ref if os.path.isabs(ref) else os.path.join(doc.base_dir, ref))
        if full not in self._tex_index:
            self._tex_index[full] = len(self.textures)
            self.textures.append(Texture(name=img.get("name") or image_id, mime=mime_of(full), data=None,
                                         path=full))
        return self._tex_index[full]

    def _texture(self, effect, tex_el):
        if tex_el is None:
            return None
        name = tex_el.get("texture")
        params = {p.get("sid"): p for p in effect.iter("newparam") if p.get("sid")}
        seen = 0
        while name in params and seen < 4:
            seen += 1
            p = params[name]
            samp = p.find("sampler2D")
            surf = p.find("surface")
            if samp is not None:
                inst = samp.find("instance_image")
                if inst is not None:
                    return self.image(_ref(inst.get("url")))
                name = (samp.findtext("source") or "").strip()
            elif surf is not None:
                name = (surf.findtext("init_from/ref") or surf.findtext("init_from") or "").strip()
                break
            else:
                break
        return self.image(name)

    def material(self, mat_id):
        if mat_id in self._mat_index:
            return self._mat_index[mat_id]
        doc = self.doc
        mel = doc.ids.get(mat_id)
        if mel is None or mel.tag != "material":
            doc.warnings.append("material not found: %s" % mat_id)
            self._mat_index[mat_id] = None
            return None
        inst = mel.find("instance_effect")
        effect = doc.ids.get(_ref(inst.get("url"))) if inst is not None else None
        base_color = (1.0, 1.0, 1.0, 1.0)
        base_tex = normal_tex = emis_tex = None
        emissive = (0.0, 0.0, 0.0)
        alpha_mode, double = "OPAQUE", False
        if effect is not None:
            tech = None
            prof = effect.find("profile_COMMON")
            if prof is not None:
                for t in prof.findall("technique"):
                    for kind in ("phong", "blinn", "lambert", "constant"):
                        tech = t.find(kind)
                        if tech is not None:
                            break
                    if tech is not None:
                        break
            if tech is not None:
                diff = tech.find("diffuse")
                if diff is not None:
                    col = diff.find("color")
                    if col is not None:
                        c = _floats(col.text) + [1.0] * 4
                        base_color = (c[0], c[1], c[2], c[3])
                    base_tex = self._texture(effect, diff.find("texture"))
                em = tech.find("emission")
                if em is not None:
                    col = em.find("color")
                    if col is not None:
                        c = _floats(col.text) + [0.0] * 3
                        emissive = (c[0], c[1], c[2])
                    emis_tex = self._texture(effect, em.find("texture"))
                alpha = base_color[3]
                tr = tech.find("transparent")
                tv = tech.find("transparency/float")
                factor = float(tv.text) if tv is not None and (tv.text or "").strip() else 1.0
                if tr is not None and tr.find("texture") is not None:
                    alpha_mode = "MASK"
                elif tr is not None or tv is not None:
                    opaque = tr.get("opaque", "A_ONE") if tr is not None else "A_ONE"
                    c = _floats(tr.findtext("color")) + [1.0] * 4 if tr is not None else [1.0] * 4
                    if opaque.startswith("RGB"):
                        lum = 0.212671 * c[0] + 0.715160 * c[1] + 0.072169 * c[2]
                        a = 1.0 - factor * lum if opaque == "RGB_ZERO" else factor * lum
                    else:
                        a = factor * c[3] if opaque == "A_ONE" else 1.0 - factor * c[3]
                    if a <= 0.0:  # some exporters write the factor inverted; never import an invisible material
                        doc.warnings.append("material %s: transparency 0 read as opaque" % mat_id)
                        a = 1.0
                    alpha = alpha * min(1.0, a)
                    if alpha < 0.999:
                        alpha_mode = "BLEND"
                base_color = (base_color[0], base_color[1], base_color[2], alpha)
            for bump in effect.iter("bump"):
                normal_tex = self._texture(effect, bump.find("texture"))
                if normal_tex is not None:
                    break
            for el in list(effect.iter("double_sided")) + list(mel.iter("double_sided")):
                if (el.text or "").strip().lower() in ("1", "true"):
                    double = True
        idx = len(self.materials)
        slots = {k: v for k, v in (("base", base_tex), ("normal", normal_tex), ("emissive", emis_tex))
                 if v is not None}
        if emis_tex is not None and emissive == (0.0, 0.0, 0.0):
            emissive = (1.0, 1.0, 1.0)
        self.materials.append(Material(name=mel.get("name") or mat_id, base_color=base_color, textures=slots,
                                       alpha_mode=alpha_mode, double_sided=double, emissive=emissive,
                                       metallic=0.0))
        self._mat_index[mat_id] = idx
        return idx


# ------------------------------------------------------------------------------------------------- scene graph

def _node_matrix(doc, node):
    m = [row[:] for row in IDENT]
    for el in node:
        tag = el.tag
        if tag == "matrix":
            m = mul(m, mat(_floats(el.text)))
        elif tag == "translate":
            v = _floats(el.text) + [0.0] * 3
            m = mul(m, translate(v[0], v[1], v[2]))
        elif tag == "rotate":
            v = _floats(el.text) + [0.0] * 4
            m = mul(m, rotate(v[0], v[1], v[2], v[3]))
        elif tag == "scale":
            v = _floats(el.text) + [1.0] * 3
            m = mul(m, scale_m(v[0], v[1], v[2]))
        elif tag in ("lookat", "skew"):
            doc.drop("node %s transforms" % tag)
    return m


def _bind_map(inst):
    out = {}
    for im in inst.iter("instance_material"):
        out[im.get("symbol")] = _ref(im.get("target"))
    return out


def read(path, scale: float = 1.0) -> Scene:
    path = str(path)
    data = read_file(path, "COLLADA")
    return parse(data, base_dir=os.path.dirname(os.path.abspath(path)), scale=scale)


def parse(data, base_dir: str = ".", scale: float = 1.0) -> Scene:
    """COLLADA bytes -> Scene; a broken file raises ReadError with one plain sentence."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ReadError("This .dae file is damaged: its XML can't be read.") from None
    if not (isinstance(root.tag, str) and root.tag.split("}")[-1] == "COLLADA"):
        raise ReadError("This .dae file isn't a COLLADA model.")
    try:
        scene = _parse(root, base_dir, scale)
    except ReadError:
        raise
    except (ValueError, IndexError, KeyError, AttributeError, TypeError, ZeroDivisionError):
        raise ReadError("This COLLADA file has broken numbers or indices, so it can't be read.") from None
    if not scene.meshes:
        raise ReadError("This COLLADA file has no meshes, so there is nothing to import.")
    return scene


MAX_NODES = 50_000  # far past any real character or prop (big rigs have a few thousand)


def _parse(root, base_dir, scale):
    version = root.get("version", "")
    _strip_ns(root)
    doc = _Doc(root, base_dir)
    unit = 1.0
    unit_el = root.find("asset/unit")
    if unit_el is not None and unit_el.get("meter"):
        try:
            unit = float(unit_el.get("meter"))
        except ValueError:
            pass
    up = (root.findtext("asset/up_axis") or "Y_UP").strip().upper()

    # --- walk the visual scene
    nodes = []  # dict(el, parent, local, world)
    instances = []  # dict(kind, url, world, bind, node, skeleton)

    def walk(el, parent, parent_world, depth=0, path=frozenset()):
        if depth > 256:
            doc.drop("nodes nested deeper than 256 levels")
            return
        if len(nodes) >= MAX_NODES:  # instance_node can multiply nodes; a broken or hostile file must not hang
            raise ReadError("This COLLADA file's scene grows past %d nodes, so it is probably broken." % MAX_NODES)
        local = _node_matrix(doc, el)
        world = mul(parent_world, local)
        me = len(nodes)
        nodes.append({"el": el, "parent": parent, "local": local, "world": world})
        for child in el:
            if child.tag == "instance_geometry":
                instances.append({"kind": "geom", "url": _ref(child.get("url")), "world": world,
                                  "bind": _bind_map(child), "node": me})
            elif child.tag == "instance_controller":
                instances.append({"kind": "ctrl", "url": _ref(child.get("url")), "world": world,
                                  "bind": _bind_map(child), "node": me,
                                  "skeleton": [_ref(s.text.strip()) for s in child.findall("skeleton") if s.text]})
            elif child.tag == "instance_node":
                target = doc.ids.get(_ref(child.get("url")))
                if target is not None and id(target) in path:
                    doc.drop("node instances that loop back on themselves")
                elif target is not None:
                    walk(target, me, world, depth + 1, path | {id(target)})
            elif child.tag == "node":
                walk(child, me, world, depth + 1, path)

    vs = None
    inst_vs = root.find("scene/instance_visual_scene")
    if inst_vs is not None:
        vs = doc.ids.get(_ref(inst_vs.get("url")))
    if vs is None:
        vs = root.find("library_visual_scenes/visual_scene")
    if vs is not None:
        for n in vs.findall("node"):
            walk(n, None, IDENT, 0)
    else:
        # no scene: show every geometry once, untransformed
        for g in root.iter("geometry"):
            instances.append({"kind": "geom", "url": g.get("id"), "world": IDENT, "bind": {}, "node": None})

    kids = {}
    for j, n in enumerate(nodes):
        kids.setdefault(n["parent"], []).append(j)
    _subtrees = {}

    def subtree(i):
        if i not in _subtrees:
            out, stack = set(), [i]
            while stack:
                k = stack.pop()
                out.add(k)
                stack.extend(kids.get(k, ()))
            _subtrees[i] = out
        return _subtrees[i]

    _allowed = {}

    def allowed_under(roots):
        key = tuple(roots)
        if key not in _allowed:
            allowed = set()
            for r in roots:
                for i, n in enumerate(nodes):
                    if n["el"].get("id") == r:
                        allowed |= subtree(i)
            _allowed[key] = sorted(allowed)
        return _allowed[key]

    def find_joint(name, roots):
        """A skin's joint name -> node index: a sid under the <skeleton> roots first, then any sid, id or name."""
        tries = []
        if roots and allowed_under(roots):
            tries.append(allowed_under(roots))
        tries.append(range(len(nodes)))
        for cands in tries:
            for attr in ("sid", "id", "name"):
                for i in cands:
                    if nodes[i]["el"].get(attr) == name:
                        return i
        return None

    # --- skins
    skins = []
    for inst in instances:
        if inst["kind"] != "ctrl":
            continue
        ctrl = doc.ids.get(inst["url"])
        skin = ctrl.find("skin") if ctrl is not None else None
        if skin is None:
            if ctrl is not None and ctrl.find("morph") is not None:
                doc.drop("morph controllers")
                base = _ref(ctrl.find("morph").get("source"))
                instances.append({"kind": "geom", "url": base, "world": inst["world"], "bind": inst["bind"],
                                  "node": inst["node"]})
            continue
        geom_url = _ref(skin.get("source"))
        src_el = doc.ids.get(geom_url)
        if src_el is not None and src_el.tag == "controller":  # skin over a morph: use the morph's base
            m = src_el.find("morph")
            doc.drop("morph controllers")
            geom_url = _ref(m.get("source")) if m is not None else geom_url
        bsm = mat(_floats(skin.findtext("bind_shape_matrix"))) if skin.find("bind_shape_matrix") is not None \
            else [row[:] for row in IDENT]
        jnames, ibms = [], []
        for inp in skin.findall("joints/input"):
            if inp.get("semantic") == "JOINT":
                jnames = list(doc.source(inp.get("source")))
            elif inp.get("semantic") == "INV_BIND_MATRIX":
                ibms = [mat(t) for t in doc.source(inp.get("source"))]
        jnodes = []
        for name in jnames:
            j = find_joint(name if isinstance(name, str) else name[0], inst.get("skeleton"))
            if j is None:
                doc.warnings.append("joint not found: %s" % name)
            jnodes.append(j)
        vw = skin.find("vertex_weights")
        influences = []
        if vw is not None:
            j_off = w_off = 0
            wsrc = []
            stride = 1
            for inp in vw.findall("input"):
                off = int(inp.get("offset", "0"))
                stride = max(stride, off + 1)
                if inp.get("semantic") == "JOINT":
                    j_off = off
                elif inp.get("semantic") == "WEIGHT":
                    w_off = off
                    wsrc = [t[0] if isinstance(t, tuple) else float(t) for t in doc.source(inp.get("source"))]
            v = _ints(vw.findtext("v"))
            at = 0
            for n in _ints(vw.findtext("vcount")):
                lst = []
                for k in range(n):
                    base = (at + k) * stride
                    lst.append((v[base + j_off], wsrc[v[base + w_off]] if v[base + w_off] < len(wsrc) else 0.0))
                influences.append(lst)
                at += n
        skins.append({"inst": inst, "geom": geom_url, "bsm": bsm, "jnodes": jnodes, "ibms": ibms,
                      "influences": influences})

    # --- the skeleton: every joint node (type="JOINT" or used by a skin)
    joint_set = {i for i, n in enumerate(nodes) if n["el"].get("type") == "JOINT"}
    for s in skins:
        joint_set |= {j for j in s["jnodes"] if j is not None}
    bone_of = {}
    order = [i for i in range(len(nodes)) if i in joint_set]  # walk order: parents before children
    for i in order:
        bone_of[i] = len(bone_of)
    bind_world = {}
    ibm_of = {}
    differ = 0
    for s in skins:
        for k, j in enumerate(s["jnodes"]):
            if j is not None and k < len(s["ibms"]):
                if j in ibm_of:
                    if any(abs(a - b) > 1e-4 for ra, rb in zip(ibm_of[j], s["ibms"][k]) for a, b in zip(ra, rb)):
                        doc.warnings.append("joint %s has two different bind matrices; the first is kept"
                                            % nodes[j]["el"].get("name"))
                    continue
                ibm_of[j] = s["ibms"][k]

    def bone_parent(i):
        p = nodes[i]["parent"]
        while p is not None and p not in bone_of:
            p = nodes[p]["parent"]
        return p

    for i in order:
        if i in ibm_of:
            bw = inv(ibm_of[i])
            if any(abs(a - b) > 1e-3 for ra, rb in zip(bw, nodes[i]["world"]) for a, b in zip(ra, rb)):
                differ += 1
        else:
            bp = bone_parent(i)
            if bp is not None:
                # the bone keeps its offset from its parent bone, measured in the node tree
                bw = mul(bind_world[bp], mul(inv(nodes[bp]["world"]), nodes[i]["world"]))
            else:
                bw = nodes[i]["world"]
        bind_world[i] = bw

    # --- conversion to metres, Y up
    s_total = unit * scale
    A = mul(scale_m(s_total, s_total, s_total), axis_matrix(up))
    A_inv = inv(A)
    C = axis_matrix(up)

    def conv_m(m):
        return mul(A, mul(m, A_inv))

    def conv_p(p):
        return point(A, p)

    def conv_n(n):
        return _n3([row[:3] for row in C[:3]], n)

    skeleton = Skeleton()
    if order:
        bones = []
        for i in order:
            bp = bone_parent(i)
            bw = conv_m(bind_world[i])
            local = mul(inv(conv_m(bind_world[bp])), bw) if bp is not None else bw
            t, r, sc = decompose(local)
            el = nodes[i]["el"]
            bones.append(Bone(name=el.get("name") or el.get("sid") or el.get("id") or "bone%d" % len(bones),
                              parent=bone_of[bp] if bp is not None else -1, translation=t, rotation=r, scale=sc,
                              inverse_bind=col_major(inv(bw))))
        skeleton = Skeleton(bones=bones)

    # --- meshes
    mats = _Mats(doc)
    geo_cache = {}
    meshes = []
    skin_by_inst = {id(s["inst"]): s for s in skins}

    def geom_prims(url):
        if url not in geo_cache:
            g = doc.ids.get(url)
            geo_cache[url] = _geometry(doc, g) if g is not None and g.tag == "geometry" else []
            if g is None:
                doc.warnings.append("geometry not found: %s" % url)
        return geo_cache[url]

    def nearest_bone(ni):
        while ni is not None and ni not in bone_of:
            ni = nodes[ni]["parent"]
        return ni

    for inst in instances:
        skin = skin_by_inst.get(id(inst))
        if inst["kind"] == "ctrl" and skin is None:
            continue
        url = skin["geom"] if skin else inst["url"]
        prims = geom_prims(url)
        rigid = None if skin else (nearest_bone(inst["node"]) if inst["node"] is not None else None)
        xf = skin["bsm"] if skin else inst["world"]
        if rigid is not None:  # keep its offset from the bone, with the bone in its rest (bind) pose
            xf = mul(bind_world[rigid], mul(inv(nodes[rigid]["world"]), inst["world"]))
        nm = normal_matrix(xf)
        flip = det3(xf) < 0
        node_el = nodes[inst["node"]]["el"] if inst["node"] is not None else None
        g_el = doc.ids.get(url)
        base_name = (node_el.get("name") if node_el is not None else None) or \
                    (g_el.get("name") if g_el is not None else None) or url or "mesh"
        for k, pr in enumerate(prims):
            pos = [conv_p(point(xf, p)) for p in pr["positions"]]
            tris = [(a, c, b) for a, b, c in pr["tris"]] if flip else list(pr["tris"])
            if pr["normals"] is not None:
                nrm = [conv_n(_n3(nm, n)) for n in pr["normals"]]
            else:
                nrm = smooth_normals(pos, tris)
            joints, weights = [], []
            if skin:
                for pi in pr["pos_index"]:
                    infl = skin["influences"][pi] if pi < len(skin["influences"]) else []
                    lst = []
                    for ji, w in infl:
                        if ji < 0 or ji >= len(skin["jnodes"]) or skin["jnodes"][ji] is None or w <= 0:
                            continue
                        lst.append((bone_of[skin["jnodes"][ji]], w))
                    merged = {}
                    for b, w in lst:
                        merged[b] = merged.get(b, 0.0) + w
                    lst = sorted(merged.items(), key=lambda t: -t[1])
                    if len(lst) > 4:
                        doc.drop("weights past the top 4")
                    lst = lst[:4]
                    tot = sum(w for _, w in lst)
                    if tot <= 0:
                        first = next((j for j in skin["jnodes"] if j is not None), None)
                        lst = [(bone_of[first] if first is not None else 0, 1.0)]
                        tot = 1.0
                        doc.drop("unweighted vertices (bound to the first joint)")
                    lst = [(b, w / tot) for b, w in lst] + [(0, 0.0)] * (4 - len(lst))
                    joints.append(tuple(b for b, _ in lst))
                    weights.append(tuple(w for _, w in lst))
            elif rigid is not None:
                joints = [(bone_of[rigid], 0, 0, 0)] * len(pos)
                weights = [(1.0, 0.0, 0.0, 0.0)] * len(pos)
            sym = pr["symbol"]
            mat_id = inst["bind"].get(sym, sym) if sym else None
            mi = mats.material(mat_id) if mat_id else None
            name = base_name if len(prims) == 1 else "%s.%s" % (base_name, sym or k)
            meshes.append(Mesh(name=name, positions=pos, normals=nrm, uvs=pr["uvs"], colors=pr["colors"],
                               joints=joints, weights=weights, triangles=tris, material=mi))

    lic = {}
    tool = ""
    for contrib in root.findall("asset/contributor"):
        for tag in ("author", "copyright", "source_data"):
            v = contrib.findtext(tag)
            if v and v.strip() and tag not in lic:
                lic[tag] = v.strip()
        tool = tool or (contrib.findtext("authoring_tool") or "").strip()
    if lic:  # free text, like glTF's asset.copyright: not a licence we understand, so never "may share"
        lic.update({"source": "dae", "known": False, "may_share": False})
    meta = new_meta("dae", collada_version=version, source_units="%g m per unit" % unit, source_up=up[:1],
                    licence=lic or None, share="private", generator=tool)
    scene = Scene(meshes=meshes, skeleton=skeleton, materials=mats.materials, textures=mats.textures, meta=meta)
    if unit * scale != 1.0:
        scene.note("Scaled by %g to metres." % (unit * scale))
    if not up.startswith("Y"):
        scene.note("Turned from %s up to Y up." % up[:1])
    if root.find("library_animations/animation") is not None:
        scene.note("Animations were not imported.")
    if differ:
        scene.note("%d bones: the rest pose was taken from the bind matrices (the scene pose differed)." % differ)
    for what, n in doc.dropped.items():
        scene.note("%d %s dropped." % (n, what))
    for w in doc.warnings:
        scene.note(w[0].upper() + w[1:] + ".")
    return scene
