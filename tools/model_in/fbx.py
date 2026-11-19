"""Autodesk FBX reader (binary 7.x and ASCII 7.x) -> the neutral Scene (tools/model_in/scene.py).

Written by us from the publicly documented FBX layout; standard library only (struct, zlib). No code from the
FBX SDK or any other tool.

Binary layout: a 27-byte header ("Kaydara FBX Binary  \\0\\x1a\\0" + uint32 version), then node records. A record
is end-offset, property count, property-list length (uint32 each before version 7500, uint64 from 7500), a
1-byte name length and the name, the properties, then child records closed by an all-zero record. Property types:
Y int16, C bool, I int32, F float32, D float64, L int64, S string, R raw bytes, and arrays f d l i b (count,
encoding 0 = raw / 1 = zlib, byte length). Every count and offset is checked against the file size, so a broken
file gives one clear ReadError and never hangs.

What it reads:
- GlobalSettings: UpAxis/UpAxisSign, FrontAxis/FrontAxisSign, CoordAxis/CoordAxisSign, UnitScaleFactor (cm per
  unit) -> converted to Y-up, metres, right-handed (a left-handed file gets its winding flipped).
- Geometry: Vertices, PolygonVertexIndex (negative index ends a polygon; fan for convex, ear clipping for
  concave), LayerElementNormal / UV (every set) / Color / Material with MappingInformationType ByPolygonVertex,
  ByVertice, ByPolygon, AllSame and ReferenceInformationType Direct, IndexToDirect. One Scene mesh per material.
- Model: Lcl Translation/Rotation/Scaling, PreRotation, PostRotation, rotation/scaling pivots and offsets,
  RotationOrder (all six Euler orders; spheric is read as XYZ and noted), GeometricTranslation/Rotation/Scaling.
- Connections OO/OP: the node hierarchy, geometry -> model, material/texture/video links, skin -> clusters.
- Deformer Skin + SubDeformer Cluster: Indexes, Weights, Transform, TransformLink. Bones are rest = bind
  (rest from TransformLink), inverse bind = inverse(world rest); top 4 weights renormalised. The file stores
  Transform relative to the bone, so the mesh's bind world is TransformLink * Transform. A static mesh under a
  bone is bound rigidly to it. Meshes are baked into world space.
- Material: DiffuseColor (x DiffuseFactor), Opacity / TransparencyFactor, EmissiveColor; Texture/Video:
  RelativeFilename / FileName, embedded Content bytes -> Texture data.
What it drops (said in meta["notes"]): animations (counted in meta["animations"], not read), blend shapes,
cameras, lights, curves, edges/smoothing, InheritType other than the default.
"""

from __future__ import annotations

import base64
import math
import os
import re
import struct
import zlib

try:  # imported as a package (model_in.fbx) or with tools/model_in on sys.path
    from . import scene as _scene
except ImportError:  # pragma: no cover
    import scene as _scene  # type: ignore

MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"
MAX_DEPTH = 128
MAX_ARRAY = 1 << 28  # elements; bigger than any real model
UNPACK_MIN = 256 << 20  # bytes every file may unpack from zlib arrays (more for big files: 64x their size)
MAX_EAR_CLIP = 128  # corners; bigger concave polygons are fanned (ear clipping is cubic)

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".tga": "image/x-tga",
        ".bmp": "image/bmp", ".dds": "image/vnd-ms.dds", ".gif": "image/gif", ".tif": "image/tiff",
        ".tiff": "image/tiff", ".webp": "image/webp", ".psd": "image/vnd.adobe.photoshop"}


class FbxError(_scene.ReadError):
    """A broken or unsupported FBX file (one plain sentence)."""


# ------------------------------------------------------------------------------------------------- node tree

class Node:
    __slots__ = ("name", "props", "children")

    def __init__(self, name, props=None, children=None):
        self.name = name
        self.props = props if props is not None else []
        self.children = children if children is not None else []

    def find(self, name):
        for c in self.children:
            if c.name == name:
                return c
        return None

    def findall(self, name):
        return [c for c in self.children if c.name == name]

    def prop(self, i=0, default=None):
        return self.props[i] if i < len(self.props) else default

    def __repr__(self):  # pragma: no cover
        return "Node(%r, %d props, %d children)" % (self.name, len(self.props), len(self.children))


# ------------------------------------------------------------------------------------------------- binary

_SCALAR = {b"Y": ("<h", 2), b"C": ("<?", 1), b"I": ("<i", 4), b"F": ("<f", 4), b"D": ("<d", 8), b"L": ("<q", 8)}
_ARRAY = {b"f": ("f", 4), b"d": ("d", 8), b"l": ("q", 8), b"i": ("i", 4), b"b": ("b", 1)}


def _read_prop(data, pos, end, props, budget):
    """Read one property at pos into props; -> the position after it."""
    t = data[pos:pos + 1]
    pos += 1
    if t in _SCALAR:
        fmt, n = _SCALAR[t]
        if pos + n > end:
            raise FbxError("The FBX file is broken (a value is cut short at byte %d)." % pos)
        props.append(struct.unpack_from(fmt, data, pos)[0])
        return pos + n
    if t in _ARRAY:
        code, n = _ARRAY[t]
        if pos + 12 > end:
            raise FbxError("The FBX file is broken (an array header is cut short at byte %d)." % pos)
        count, enc, blen = struct.unpack_from("<III", data, pos)
        pos += 12
        if pos + blen > end:
            raise FbxError("The FBX file is broken (an array runs past the end at byte %d)." % pos)
        if count > MAX_ARRAY:
            raise FbxError("The FBX file is broken (an array claims %d values)." % count)
        raw = data[pos:pos + blen]
        want = count * n
        if enc == 1:
            budget[0] -= want
            if budget[0] < 0 or want > blen * 1100 + 1024:  # zlib can't pack better than ~1032:1
                raise FbxError("The FBX file is broken (a packed array claims %d values)." % count)
            try:  # never unpack more than the array can hold (a zip bomb stops here)
                raw = zlib.decompressobj().decompress(raw, want + 1)
            except zlib.error:
                raise FbxError("The FBX file is broken (a compressed array doesn't unpack).") from None
        elif enc != 0:
            raise FbxError("The FBX file uses an unknown array encoding (%d)." % enc)
        if len(raw) != want:
            raise FbxError("The FBX file is broken (an array holds %d bytes, %d expected)." % (len(raw), want))
        props.append(list(struct.unpack("<%d%s" % (count, code), raw)) if count else [])
        return pos + blen
    if t in (b"S", b"R"):
        if pos + 4 > end:
            raise FbxError("The FBX file is broken (a string is cut short at byte %d)." % pos)
        n = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if pos + n > end:
            raise FbxError("The FBX file is broken (a string runs past the end at byte %d)." % pos)
        raw = data[pos:pos + n]
        props.append(raw.decode("utf-8", "replace") if t == b"S" else bytes(raw))
        return pos + n
    raise FbxError("The FBX file is broken (unknown property type %r at byte %d)." % (t, pos - 1))


def parse_binary(data: bytes):
    """-> (version, root Node). Raises FbxError on anything that doesn't add up; never loops on bad offsets."""
    if len(data) < 27 or not data.startswith(MAGIC):
        raise FbxError("This isn't a binary FBX file (the header is wrong).")
    version = struct.unpack_from("<I", data, 23)[0]
    if version < 7000 or version >= 10000:
        raise FbxError("FBX version %d isn't supported (only 7.x binary files)." % version)
    head = struct.Struct("<QQQB" if version >= 7500 else "<IIIB")
    root = Node("")
    budget = [max(UNPACK_MIN, 64 * len(data))]
    # stack of (node, position of its next child, end of its children); every step moves pos forward
    stack = [(root, 27, len(data))]
    while stack:
        parent, pos, limit = stack[-1]
        if pos >= limit:
            stack.pop()
            continue
        if pos + head.size > limit:
            if len(stack) == 1:
                break  # top level: only the footer is left
            raise FbxError("The FBX file is broken (a node header is cut short at byte %d)." % pos)
        end, nprops, plen, nlen = head.unpack_from(data, pos)
        if end == 0:
            if len(stack) == 1:
                break  # the top-level list ends with a null record, then the footer
            stack.pop()  # the null record that closes a child list
            continue
        start = pos
        pos += head.size
        if end <= start or end > limit or pos + nlen > end:
            raise FbxError("The FBX file is broken (a node at byte %d points outside its space)." % start)
        if nprops > plen:  # every property takes at least one byte
            raise FbxError("The FBX file is broken (a node at byte %d has impossible sizes)." % start)
        name = data[pos:pos + nlen].decode("ascii", "replace")
        pos += nlen
        pend = pos + plen
        if pend > end:
            raise FbxError("The FBX file is broken (node %s has properties past its end)." % name)
        node = Node(name)
        for _ in range(nprops):
            if pos >= pend:
                raise FbxError("The FBX file is broken (node %s has fewer properties than it says)." % name)
            pos = _read_prop(data, pos, pend, node.props, budget)
        if pos != pend:
            raise FbxError("The FBX file is broken (node %s's properties don't fill their space)." % name)
        parent.children.append(node)
        stack[-1] = (parent, end, limit)
        if pend < end:
            if len(stack) >= MAX_DEPTH:
                raise FbxError("The FBX file is broken (nodes nest more than %d deep)." % MAX_DEPTH)
            stack.append((node, pend, end))
    return version, root


# ------------------------------------------------------------------------------------------------- ASCII

_TOKEN = re.compile(r"""
    [ \t\r\n]+
  | ;[^\n]*
  | (?P<str>"[^"]*")
  | (?P<key>[A-Za-z_][A-Za-z0-9_|.\-]*)[ \t]*:
  | (?P<count>\*[0-9]+)
  | (?P<num>[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)
  | (?P<punct>[{},])
  | (?P<word>[A-Za-z_][A-Za-z0-9_#.\-]*)
""", re.X)


def _ascii_tokens(text):
    pos, n = 0, len(text)
    out = []
    match = _TOKEN.match
    while pos < n:
        m = match(text, pos)
        if m is None:
            line = text.count("\n", 0, pos) + 1
            raise FbxError("The FBX text file is broken (can't read line %d)." % line)
        pos = m.end()
        kind = m.lastgroup
        if kind is None:
            continue
        v = m.group(kind)
        if kind == "str":
            out.append(("v", v[1:-1]))
        elif kind == "key":
            out.append(("k", v))
        elif kind == "num":
            out.append(("v", float(v) if any(c in v for c in ".eE") else int(v)))
        elif kind == "count":
            out.append(("c", int(v[1:])))
        elif kind == "punct":
            out.append((v, None))
        else:
            out.append(("v", v))
    return out


def parse_ascii(text: str):
    """-> (version, root Node) for an ASCII FBX file."""
    toks = _ascii_tokens(text)
    root = Node("")
    stack = [root]
    i, n = 0, len(toks)
    while i < n:
        kind, val = toks[i]
        if kind == "}":
            if len(stack) == 1:
                raise FbxError("The FBX text file is broken (a '}' closes nothing).")
            node = stack.pop()
            _fold_ascii_array(node)
            i += 1
            continue
        if kind != "k":
            raise FbxError("The FBX text file is broken (a value stands where a name should be).")
        node = Node(val)
        stack[-1].children.append(node)
        i += 1
        # values: v (, v)* ; a *N array count also counts as a value slot
        expect_value = True
        while i < n:
            kind, val = toks[i]
            if kind in ("v", "c") and expect_value:
                if kind == "v":
                    node.props.append(val)
                i += 1
                expect_value = False
            elif kind == ",":
                i += 1
                expect_value = True
            else:
                break
        if i < n and toks[i][0] == "{":
            i += 1
            if len(stack) >= MAX_DEPTH:
                raise FbxError("The FBX text file is broken (blocks nest more than %d deep)." % MAX_DEPTH)
            stack.append(node)
    if len(stack) != 1:
        raise FbxError("The FBX text file is broken (a '{' is never closed).")
    version = 7400
    hdr = root.find("FBXHeaderExtension")
    if hdr is not None and hdr.find("FBXVersion") is not None:
        version = int(hdr.find("FBXVersion").prop(0, 7400) or 7400)
    return version, root


def _fold_ascii_array(node):
    """ASCII 7.x writes arrays as `Name: *N { a: v,v,v }`: make that one list property like the binary form."""
    a = node.find("a")
    if a is not None and len(node.children) == 1 and not node.props:
        node.props = [list(a.props)]
        node.children = []


# ------------------------------------------------------------------------------------------------- helpers

def _arr(node):
    """The numbers of an array node (binary list prop, folded ASCII list, or loose ASCII values)."""
    if node is None:
        return []
    if len(node.props) == 1 and isinstance(node.props[0], list):
        return node.props[0]
    return [p for p in node.props if isinstance(p, (int, float)) and not isinstance(p, bool)]


def _clean_name(s):
    if not isinstance(s, str):
        return str(s)
    if "\x00\x01" in s:
        return s.split("\x00\x01", 1)[0]
    if "::" in s:
        return s.split("::", 1)[1]
    return s


def _props70(node):
    """{name: [values]} from Properties70 (P) or Properties60 (Property)."""
    out = {}
    if node is None:
        return out
    p70 = node.find("Properties70")
    if p70 is not None:
        for p in p70.findall("P"):
            if p.props:
                out[str(p.props[0])] = p.props[4:]
    p60 = node.find("Properties60")
    if p60 is not None:
        for p in p60.findall("Property"):
            if p.props:
                out.setdefault(str(p.props[0]), p.props[3:])
    return out


def _num(props, key, default):
    v = props.get(key)
    if v and isinstance(v[0], (int, float)):
        return float(v[0])
    return default


def _vec3(props, key, default=(0.0, 0.0, 0.0)):
    v = props.get(key)
    if v and len(v) >= 3 and all(isinstance(x, (int, float)) for x in v[:3]):
        return (float(v[0]), float(v[1]), float(v[2]))
    return default


def _translate(t):
    return (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, t[0], t[1], t[2], 1.0)


def _scale(s):
    return (s[0], 0.0, 0.0, 0.0, 0.0, s[1], 0.0, 0.0, 0.0, 0.0, s[2], 0.0, 0.0, 0.0, 0.0, 1.0)


def _rot_axis(axis, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    if axis == 0:
        return (1.0, 0.0, 0.0, 0.0, 0.0, c, s, 0.0, 0.0, -s, c, 0.0, 0.0, 0.0, 0.0, 1.0)
    if axis == 1:
        return (c, 0.0, -s, 0.0, 0.0, 1.0, 0.0, 0.0, s, 0.0, c, 0.0, 0.0, 0.0, 0.0, 1.0)
    return (c, s, 0.0, 0.0, -s, c, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


# FBX RotationOrder enum: 0 XYZ, 1 XZY, 2 YZX, 3 YXZ, 4 ZXY, 5 ZYX, 6 spheric XYZ. "XYZ" = X applied first.
_ORDERS = ((0, 1, 2), (0, 2, 1), (1, 2, 0), (1, 0, 2), (2, 0, 1), (2, 1, 0))


def euler_matrix(deg, order=0):
    m = _scene.IDENTITY
    for axis in _ORDERS[order if 0 <= order < 6 else 0]:
        if deg[axis]:
            m = _scene.mat_mul(_rot_axis(axis, deg[axis]), m)
    return m


def _inv(m):
    return _scene.mat_inverse(m)


def local_matrix(props, notes=None):
    """FBX node local transform: T * Roff * Rp * Rpre * R * Rpost^-1 * Rp^-1 * Soff * Sp * S * Sp^-1."""
    mm = _scene.mat_mul
    order = int(_num(props, "RotationOrder", 0.0))
    if order == 6 and notes is not None:
        notes.add("A spheric rotation order was read as XYZ.")
    t = _translate(_vec3(props, "Lcl Translation"))
    r = euler_matrix(_vec3(props, "Lcl Rotation"), order)
    s = _scale(_vec3(props, "Lcl Scaling", (1.0, 1.0, 1.0)))
    pre = euler_matrix(_vec3(props, "PreRotation"))
    post = euler_matrix(_vec3(props, "PostRotation"))
    roff = _translate(_vec3(props, "RotationOffset"))
    rp = _translate(_vec3(props, "RotationPivot"))
    soff = _translate(_vec3(props, "ScalingOffset"))
    sp = _translate(_vec3(props, "ScalingPivot"))
    rp_inv = _translate(tuple(-x for x in _vec3(props, "RotationPivot")))
    sp_inv = _translate(tuple(-x for x in _vec3(props, "ScalingPivot")))
    post_inv = _inv(post)
    m = t
    for x in (roff, rp, pre, r, post_inv, rp_inv, soff, sp, s, sp_inv):
        m = mm(m, x)
    return m


def geometric_matrix(props):
    gt = _vec3(props, "GeometricTranslation")
    gr = _vec3(props, "GeometricRotation")
    gs = _vec3(props, "GeometricScaling", (1.0, 1.0, 1.0))
    return _scene.mat_mul(_scene.mat_mul(_translate(gt), euler_matrix(gr)), _scale(gs))


def _fbx_matrix(node):
    v = _arr(node)
    if len(v) != 16:
        return None
    return tuple(float(x) for x in v)


def axis_conversion(gs):
    """GlobalSettings -> (3x3 basis as a column-major 4x4 without scale, unit scale to metres, info dict)."""
    p = _props70(gs)
    up = int(_num(p, "UpAxis", 1.0))
    up_s = _num(p, "UpAxisSign", 1.0)
    front = int(_num(p, "FrontAxis", 2.0))
    front_s = _num(p, "FrontAxisSign", 1.0)
    coord = int(_num(p, "CoordAxis", 0.0))
    coord_s = _num(p, "CoordAxisSign", 1.0)
    unit = _num(p, "UnitScaleFactor", 1.0)
    if sorted((up, front, coord)) != [0, 1, 2]:
        up, up_s, front, front_s, coord, coord_s = 1, 1.0, 2, 1.0, 0, 1.0
    if not (math.isfinite(unit) and unit > 0):
        unit = 1.0
    sgn = lambda v: -1.0 if v < 0 else 1.0  # noqa: E731
    # rows of the conversion: scene X <- file right, scene Y <- file up, scene Z <- file front
    rows = [[0.0, 0.0, 0.0] for _ in range(3)]
    rows[0][coord] = sgn(coord_s)
    rows[1][up] = sgn(up_s)
    rows[2][front] = sgn(front_s)
    m = (rows[0][0], rows[1][0], rows[2][0], 0.0,
         rows[0][1], rows[1][1], rows[2][1], 0.0,
         rows[0][2], rows[1][2], rows[2][2], 0.0,
         0.0, 0.0, 0.0, 1.0)
    names = "XYZ"
    info = {"up": ("-" if up_s < 0 else "+") + names[up], "front": ("-" if front_s < 0 else "+") + names[front],
            "right": ("-" if coord_s < 0 else "+") + names[coord], "unit_cm": unit}
    return m, unit / 100.0, info


# ------------------------------------------------------------------------------------------------- triangulation

def _newell(pts):
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0, z0 = pts[i]
        x1, y1, z1 = pts[(i + 1) % n]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    return (nx, ny, nz)


def triangulate(pts):
    """Polygon corners (3D) -> [(i, j, k)] keeping the polygon's winding. Fan if convex, ear clipping if not."""
    n = len(pts)
    if n < 3:
        return []
    if n == 3:
        return [(0, 1, 2)]
    nrm = _newell(pts)
    ax = max(range(3), key=lambda k: abs(nrm[k]))
    if abs(nrm[ax]) < 1e-30:
        return [(0, i, i + 1) for i in range(1, n - 1)]
    u, v = [(1, 2), (2, 0), (0, 1)][ax]
    flip = nrm[ax] < 0
    p2 = [(p[u], p[v]) for p in pts]
    if flip:  # make the projected polygon counter-clockwise
        p2 = [(x, -y) for x, y in p2]

    def cross2(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    if n > MAX_EAR_CLIP or all(cross2(p2[i - 1], p2[i], p2[(i + 1) % n]) >= 0 for i in range(n)):
        return [(0, i, i + 1) for i in range(1, n - 1)]
    idx = list(range(n))
    tris = []
    guard = 0
    while len(idx) > 3 and guard < n * n:
        guard += 1
        m = len(idx)
        for k in range(m):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % m]
            if cross2(p2[a], p2[b], p2[c]) <= 0:
                continue
            inside = False
            for q in idx:
                if q in (a, b, c):
                    continue
                if (cross2(p2[a], p2[b], p2[q]) >= 0 and cross2(p2[b], p2[c], p2[q]) >= 0
                        and cross2(p2[c], p2[a], p2[q]) >= 0):
                    inside = True
                    break
            if not inside:
                tris.append((a, b, c))
                idx.pop(k)
                break
        else:
            break
    if len(idx) == 3:
        tris.append(tuple(idx))
    elif len(idx) > 3:  # self-intersecting: fan the rest
        tris += [(idx[0], idx[i], idx[i + 1]) for i in range(1, len(idx) - 1)]
    return tris


# ------------------------------------------------------------------------------------------------- layers

def _layer_el(el, values_key, index_key, width, poly_of_pv, pv_cp, n_cp, notes, label):
    mapping = str((el.find("MappingInformationType") or Node("", ["ByPolygonVertex"])).prop(0, "ByPolygonVertex"))
    ref = str((el.find("ReferenceInformationType") or Node("", ["Direct"])).prop(0, "Direct"))
    flat = _arr(el.find(values_key))
    vals = [tuple(float(x) for x in flat[i:i + width]) for i in range(0, len(flat) - width + 1, width)]
    if not vals:
        return None
    index = None
    if ref in ("IndexToDirect", "Index"):
        index = _arr(el.find(index_key))
    elif ref != "Direct":
        notes.add("A %s layer uses reference mode %s; it was skipped." % (label, ref))
        return None
    n_pv = len(pv_cp)

    def pick(k):
        if index is not None:
            if k >= len(index):
                return None
            k = index[k]
        if 0 <= k < len(vals):
            return vals[k]
        return None

    if mapping == "ByPolygonVertex":
        out = [pick(k) for k in range(n_pv)]
    elif mapping in ("ByVertice", "ByVertex", "ByControlPoint"):
        out = [pick(cp) for cp in pv_cp]
    elif mapping == "ByPolygon":
        out = [pick(poly_of_pv[k]) for k in range(n_pv)]
    elif mapping == "AllSame":
        v = pick(0)
        out = [v] * n_pv
    else:
        notes.add("A %s layer uses mapping %s; it was skipped." % (label, mapping))
        return None
    if any(v is None for v in out):
        notes.add("A %s layer points outside its values; it was skipped." % label)
        return None
    return out


def _material_layer(geom, n_poly):
    el = geom.find("LayerElementMaterial")
    if el is None:
        return None
    mapping = str((el.find("MappingInformationType") or Node("", ["AllSame"])).prop(0, "AllSame"))
    ids = [int(x) for x in _arr(el.find("Materials"))]
    if not ids:
        return None
    if mapping == "AllSame":
        return [ids[0]] * n_poly
    if len(ids) < n_poly:
        return [ids[0]] * n_poly
    return ids[:n_poly]


# ------------------------------------------------------------------------------------------------- scene build

class _Notes:
    def __init__(self):
        self.items = []

    def add(self, text):
        if text not in self.items:
            self.items.append(text)


def read(path, **opts):
    with open(path, "rb") as f:
        data = f.read()
    return parse(data, base_dir=os.path.dirname(os.path.abspath(path)), name=os.path.basename(path), **opts)


def parse(data: bytes, base_dir: str = ".", name: str = "model.fbx", **_opts):
    if data.startswith(MAGIC[:20]):
        version, root = parse_binary(data)
        kind = "fbx-binary"
    else:
        if data.startswith(b"Kaydara"):
            raise FbxError("This isn't a binary FBX file (the header is wrong).")
        if not re.search(rb"(FBXHeaderExtension|Objects|Definitions)[ \t]*:", data[:65536]):
            raise FbxError("This isn't an FBX file (no FBX header found).")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        version, root = parse_ascii(text.lstrip("\ufeff"))
        kind = "fbx-ascii"
    return build_scene(root, version, kind, base_dir)


def build_scene(root, version, kind, base_dir="."):
    S = _scene
    notes = _Notes()
    if version < 7000:
        raise FbxError("FBX %d files (before 7.0) aren't supported; re-export as FBX 2011 or newer." % version)
    conv, unit, axis_info = axis_conversion(root.find("GlobalSettings"))
    conv_det = S.mat_det3(conv)
    objects = root.find("Objects")
    if objects is None:
        raise FbxError("The FBX file has no Objects section.")
    objs = {}
    for o in objects.children:
        oid = o.prop(0)
        if isinstance(oid, (int, float)) and not isinstance(oid, bool):
            objs[int(oid)] = o
        elif isinstance(oid, str):  # 6.x-style names as ids
            objs[oid] = o
    # connections: child -> [(parent, prop)], parent -> [(child, prop)]
    up, down = {}, {}
    conns = root.find("Connections")
    for c in (conns.children if conns is not None else []):
        if c.name not in ("C", "Connect") or len(c.props) < 3:
            continue
        kind_c = str(c.props[0])
        a, b = c.props[1], c.props[2]
        a = int(a) if isinstance(a, (int, float)) else a
        b = int(b) if isinstance(b, (int, float)) else b
        prop = str(c.props[3]) if kind_c == "OP" and len(c.props) > 3 else None
        up.setdefault(a, []).append((b, prop))
        down.setdefault(b, []).append((a, prop))

    def kids(oid, cls=None, sub=None):
        out = []
        for cid, prop in down.get(oid, []):
            o = objs.get(cid)
            if o is None or (cls and o.name != cls) or (sub and o.prop(2) != sub):
                continue
            out.append((cid, o, prop))
        return out

    def parents(oid, cls=None):
        out = []
        for pid, prop in up.get(oid, []):
            o = objs.get(pid)
            if o is not None and (cls is None or o.name == cls):
                out.append((pid, o, prop))
        return out

    # ---- model hierarchy (file space)
    models = {oid: o for oid, o in objs.items() if o.name == "Model"}
    mparent = {}
    for oid in models:
        ps = [pid for pid, o, _ in parents(oid, "Model")]
        mparent[oid] = ps[0] if ps else None
    local_cache, world_cache = {}, {}
    mprops = {oid: _props70(o) for oid, o in models.items()}
    for oid, p in mprops.items():
        it = int(_num(p, "InheritType", 1.0))
        if it not in (0, 1):  # RrSs and RSrs only differ with non-uniform scale; Rrs (2) is read as RSrs
            notes.add("A node uses inherit type %d; it was read as the default." % it)

    for oid in models:  # a parent loop would hang every walk up the tree: refuse it once, here
        seen, p = {oid}, mparent.get(oid)
        while p is not None:
            if p in seen:
                raise FbxError("The FBX file is broken (its node parents go round in a loop).")
            seen.add(p)
            p = mparent.get(p)

    def local(oid):
        if oid not in local_cache:
            local_cache[oid] = local_matrix(mprops[oid], notes)
        return local_cache[oid]

    def world(oid):
        chain = []
        p = oid
        while p is not None and p not in world_cache:
            chain.append(p)
            p = mparent.get(p)
        m = world_cache[p] if p is not None else None
        for q in reversed(chain):
            m = local(q) if m is None else S.mat_mul(m, local(q))
            world_cache[q] = m
        return world_cache[oid]

    # conversion of file-space matrices into scene space: C * G * C^-1 with C = unit * basis
    cmat = tuple(x * unit for x in conv[:12]) + (0.0, 0.0, 0.0, 1.0)
    cinv = S.mat_inverse(cmat)

    def to_scene(m):
        return S.mat_mul(S.mat_mul(cmat, m), cinv)

    # ---- materials and textures
    textures, tex_index = [], {}

    def texture_of(tex_id, tnode):
        if tex_id in tex_index:
            return tex_index[tex_id]
        tname = _clean_name(tnode.prop(1, "texture"))
        rel = tnode.find("RelativeFilename") or tnode.find("RelativeFileName")
        absn = tnode.find("FileName") or tnode.find("Filename")
        data = None
        for vid, vnode, _ in kids(tex_id, "Video"):
            content = vnode.find("Content")
            if content is not None:
                blob = _content_bytes(content)
                if blob:
                    data = blob
            if rel is None:
                rel = vnode.find("RelativeFilename")
            if absn is None:
                absn = vnode.find("Filename") or vnode.find("FileName")
        relp = str(rel.prop(0, "")) if rel is not None else ""
        absp = str(absn.prop(0, "")) if absn is not None else ""
        path = _resolve(base_dir, relp, absp)
        label = relp or absp or tname
        if data is None and path is None:
            notes.add("A texture file wasn't found next to the model: %s." % os.path.basename(
                label.replace("\\", "/")))
            path = os.path.join(base_dir, os.path.basename(label.replace("\\", "/")) or tname)
        tex_index[tex_id] = len(textures)
        textures.append(S.Texture(name=os.path.splitext(os.path.basename(label.replace("\\", "/")))[0] or tname,
                                  mime=MIME.get(os.path.splitext(label)[1].lower(), "application/octet-stream"),
                                  data=data, path=None if data is not None else path))
        return tex_index[tex_id]

    materials, mat_index = [], {}

    def material_of(mat_id):
        if mat_id in mat_index:
            return mat_index[mat_id]
        mnode = objs[mat_id]
        p = _props70(mnode)
        dc = _vec3(p, "DiffuseColor", _vec3(p, "Diffuse", (0.8, 0.8, 0.8)))
        df = _num(p, "DiffuseFactor", 1.0)
        if "Opacity" in p:
            alpha = _num(p, "Opacity", 1.0)
        elif "TransparencyFactor" in p and "TransparentColor" in p:
            tc = _vec3(p, "TransparentColor", (0.0, 0.0, 0.0))
            alpha = 1.0 - _num(p, "TransparencyFactor", 0.0) * (sum(tc) / 3.0)
        else:
            alpha = 1.0
        alpha = max(0.0, min(1.0, alpha))
        ec = _vec3(p, "EmissiveColor", (0.0, 0.0, 0.0))
        ef = _num(p, "EmissiveFactor", 1.0)
        mat = S.Material(name=_clean_name(mnode.prop(1, "material")),
                         base_color=(dc[0] * df, dc[1] * df, dc[2] * df, alpha),
                         emissive=(ec[0] * ef, ec[1] * ef, ec[2] * ef),
                         alpha_mode="BLEND" if alpha < 1.0 else "OPAQUE")
        slots = {"DiffuseColor": "base", "Diffuse": "base", "Maya|baseColor": "base", "base_color": "base",
                 "NormalMap": "normal", "Bump": "normal", "Maya|normalCamera": "normal",
                 "EmissiveColor": "emissive", "Emissive": "emissive"}
        for tid, tnode, prop in kids(mat_id, "Texture"):
            slot = slots.get(prop or "DiffuseColor")
            if prop in ("TransparentColor", "TransparencyFactor"):
                # a cut-out map: the Scene has no opacity slot, so the colour texture's alpha is used instead
                if mat.alpha_mode == "OPAQUE":
                    mat.alpha_mode = "MASK"
                notes.add("Opacity textures weren't read; those materials use their colour texture's alpha (MASK).")
                continue
            if slot is None:
                notes.add("A material texture on %s was skipped." % (prop or "?"))
                continue
            mat.textures.setdefault(slot, texture_of(tid, tnode))
        mat_index[mat_id] = len(materials)
        materials.append(mat)
        return mat_index[mat_id]

    # ---- skins: geometry id -> [(bone model id, indexes, weights, transform, link)]
    skins = {}
    for gid, g in objs.items():
        if g.name != "Geometry":
            continue
        for sid, snode, _ in kids(gid, "Deformer"):
            if snode.prop(2) != "Skin":
                if snode.prop(2) == "BlendShape":
                    notes.add("Blend shapes (morphs) were dropped.")
                continue
            for cid, cnode, _ in kids(sid, "Deformer"):
                if cnode.prop(2) != "Cluster":
                    continue
                links = [mid for mid, mo, _ in kids(cid, "Model")]
                if not links:
                    continue
                idx = [int(x) for x in _arr(cnode.find("Indexes"))]
                wts = [float(x) for x in _arr(cnode.find("Weights"))]
                if len(idx) != len(wts):
                    notes.add("A skin cluster has %d indexes and %d weights; the extra ones were dropped."
                              % (len(idx), len(wts)))
                skins.setdefault(gid, []).append((links[0], idx, wts, _fbx_matrix(cnode.find("Transform")),
                                                  _fbx_matrix(cnode.find("TransformLink"))))

    # ---- skeleton: every cluster link, every limb node, and their ancestors
    link_world = {}
    for clist in skins.values():
        for bid, _i, _w, _t, link in clist:
            if link is not None and bid not in link_world:
                link_world[bid] = link
    bone_ids = set(link_world)
    for oid, o in models.items():
        if o.prop(2) in ("LimbNode", "Limb", "Root", "Skeleton"):
            bone_ids.add(oid)
    for oid in list(bone_ids):
        p = mparent.get(oid)
        while p is not None and p not in bone_ids:
            bone_ids.add(p)
            p = mparent.get(p)
    # parents before children: walk each bone's chain up to the first placed one, then add it top-down
    bone_order, placed = [], set()
    for oid in sorted(bone_ids, key=lambda k: str(k)):
        chain, p = [], oid
        while p is not None and p in bone_ids and p not in placed:
            chain.append(p)
            p = mparent.get(p)
        for q in reversed(chain):
            placed.add(q)
            bone_order.append(q)
    bone_index = {oid: i for i, oid in enumerate(bone_order)}
    bone_world_scene = {}
    for oid in bone_order:
        fw = link_world.get(oid)
        if fw is None:
            par = mparent.get(oid)
            # a bone without a cluster keeps its bind-pose parent: world = parent bind world * own local
            fw = S.mat_mul(link_world[par], local(oid)) if par in link_world else world(oid)
            link_world[oid] = fw
        bone_world_scene[oid] = to_scene(fw)
    bones = []
    for oid in bone_order:
        par = mparent.get(oid)
        pi = bone_index.get(par, -1) if par is not None else -1
        wm = bone_world_scene[oid]
        loc = wm if pi < 0 else S.mat_mul(S.mat_inverse(bone_world_scene[par]), wm)
        t, r, s = S.trs_from_mat(loc)
        bones.append(S.Bone(name=_clean_name(models[oid].prop(1, "bone")), parent=pi, translation=t, rotation=r,
                            scale=s, inverse_bind=S.mat_inverse(wm)))

    # ---- meshes
    meshes = []
    for mid, mnode in models.items():
        geoms = kids(mid, "Geometry")
        if not geoms:
            continue
        mat_ids = [cid for cid, _o, _p in kids(mid, "Material")]
        geo_m = geometric_matrix(mprops[mid])
        for gid, gnode, _ in geoms:
            if gnode.prop(2) not in (None, "Mesh"):
                notes.add("A %s geometry was dropped (only meshes are read)." % gnode.prop(2))
                continue
            clusters = skins.get(gid, [])
            # the file stores Transform relative to the bone (TransformLink^-1 * mesh world at bind), so the
            # mesh's bind world is TransformLink * Transform
            bind = None
            for _b, _i, _w, tr, link in clusters:
                if tr is not None and link is not None:
                    bind = S.mat_mul(link, tr)
                    break
            rigid = None
            if bind is None:
                # a static mesh under a bone (a sword in a hand) is bound rigidly to that bone, placed where it
                # sits relative to the bone in the bind pose
                p = mparent.get(mid)
                while p is not None and p not in bone_index:
                    p = mparent.get(p)
                if p is not None:
                    rigid = bone_index[p]
                    bind = S.mat_mul(S.mat_mul(link_world[p], S.mat_inverse(world(p))), world(mid))
                    notes.add("Static meshes under a bone were bound rigidly to it.")
            # vertices are in the geometry's own file-space frame: scene = C * world * geometric * v
            mesh_world = S.mat_mul(bind if bind is not None else world(mid), geo_m)
            meshes += _build_mesh(gnode, _clean_name(mnode.prop(1, "mesh")), S.mat_mul(cmat, mesh_world),
                                  clusters, bone_index, [material_of(m) for m in mat_ids], notes, rigid)

    # ---- animations and other dropped things
    stacks = [_clean_name(o.prop(1, "")) for o in objs.values() if o.name == "AnimationStack"]
    curves = sum(1 for o in objs.values() if o.name == "AnimationCurve")
    attrs = {}
    for o in objs.values():
        if o.name == "NodeAttribute" and o.prop(2) in ("Camera", "Light"):
            attrs[o.prop(2)] = attrs.get(o.prop(2), 0) + 1
        if o.name == "Geometry" and o.prop(2) in ("NurbsCurve", "Line", "Nurbs", "NurbsSurface", "Shape"):
            if o.prop(2) != "Shape":
                attrs[o.prop(2)] = attrs.get(o.prop(2), 0) + 1
    meta = S.new_meta(kind, fbx_version=version, axes=axis_info,
                      animations={"stacks": stacks, "curves": curves})
    if curves or stacks:
        notes.add("%d animation take(s) with %d curves were found and not read." % (len(stacks), curves))
    for k, n in attrs.items():
        notes.add("%d %s object(s) were dropped." % (n, k))
    if conv_det < 0:
        notes.add("The file is left-handed; it was mirrored to right-handed and the winding flipped.")
    if (axis_info["up"], axis_info["front"]) != ("+Y", "+Z"):
        notes.add("Axes converted from %s up / %s front to +Y up / +Z front." % (axis_info["up"],
                                                                               axis_info["front"]))
    if abs(unit - 0.01) > 1e-12:
        notes.add("Units converted: 1 file unit = %g m." % unit)
    else:
        notes.add("Units converted from centimetres to metres.")
    meta["notes"] = notes.items
    return S.Scene(meshes=meshes, skeleton=S.Skeleton(bones=bones), materials=materials, textures=textures,
                   meta=meta)


def _content_bytes(node):
    v = node.props
    if v and isinstance(v[0], (bytes, bytearray)):
        return bytes(v[0])
    text = "".join(x for x in v if isinstance(x, str))
    if not text:
        return None
    try:
        return base64.b64decode(text, validate=False)
    except (ValueError, TypeError):
        return None


def _resolve(base_dir, rel, absp):
    cands = []
    if rel:
        cands.append(os.path.join(base_dir, rel.replace("\\", os.sep).replace("/", os.sep)))
    if absp:
        cands.append(absp)
        cands.append(os.path.join(base_dir, os.path.basename(absp.replace("\\", "/"))))
    if rel:
        cands.append(os.path.join(base_dir, os.path.basename(rel.replace("\\", "/"))))
    for c in cands:
        if c and os.path.isfile(c):
            return os.path.normpath(c)
    return None


def _build_mesh(g, name, mesh_world, clusters, bone_index, mat_list, notes, rigid=None):
    S = _scene
    flat = _arr(g.find("Vertices"))
    cps = [(float(flat[i]), float(flat[i + 1]), float(flat[i + 2])) for i in range(0, len(flat) - 2, 3)]
    pvi = [int(x) for x in _arr(g.find("PolygonVertexIndex"))]
    n_cp = len(cps)
    if not cps or not pvi:
        return []
    # polygons
    polys, cur = [], []
    pv_cp = []
    for k, v in enumerate(pvi):
        end = v < 0
        cp = ~v if end else v
        if not 0 <= cp < n_cp:
            raise FbxError("The FBX file is broken (mesh %s points at vertex %d of %d)." % (name, cp, n_cp))
        cur.append(k)
        pv_cp.append(cp)
        if end:
            polys.append(cur)
            cur = []
    if cur:
        polys.append(cur)
        notes.add("A mesh's last polygon wasn't closed; it was closed.")
    poly_of_pv = [0] * len(pvi)
    for pi_, poly in enumerate(polys):
        for k in poly:
            poly_of_pv[k] = pi_
    n_poly = len(polys)
    normals = None
    uv_sets = []
    colors = None
    for el in g.findall("LayerElementNormal")[:1]:
        normals = _layer_el(el, "Normals", "NormalsIndex", 3, poly_of_pv, pv_cp, n_cp, notes, "normal")
    for el in g.findall("LayerElementUV"):
        uv = _layer_el(el, "UV", "UVIndex", 2, poly_of_pv, pv_cp, n_cp, notes, "UV")
        if uv is not None:
            uv_sets.append(uv)
    for el in g.findall("LayerElementColor")[:1]:
        colors = _layer_el(el, "Colors", "ColorIndex", 4, poly_of_pv, pv_cp, n_cp, notes, "colour")
    if len(g.findall("LayerElementColor")) > 1:
        notes.add("Only the first vertex colour set was kept.")
    mat_of_poly = _material_layer(g, n_poly)
    # skin per control point
    skin = None
    if clusters:
        per = [[] for _ in range(n_cp)]
        for bid, idx, wts, _t, _l in clusters:
            bi = bone_index.get(bid)
            if bi is None:
                continue
            for i, w in zip(idx, wts):
                if 0 <= i < n_cp and w > 0:
                    per[i].append((bi, w))
        skin = []
        unweighted = 0
        for lst in per:
            t4 = S.top4(lst)
            if t4 is None:
                unweighted += 1
                t4 = ((0, 0, 0, 0), (1.0, 0.0, 0.0, 0.0))
            skin.append(t4)
        if unweighted:
            notes.add("%d skinned vertices had no weights; they follow the first bone." % unweighted)
    elif rigid is not None:
        skin = [((rigid, 0, 0, 0), (1.0, 0.0, 0.0, 0.0))] * n_cp
    # transform
    pos_w = [S.mat_point(mesh_world, p) for p in cps]
    nmat = S.normal_matrix(mesh_world)
    flip = S.mat_det3(mesh_world) < 0
    if normals is not None:
        normals = [S.normalize(S.mat_dir(nmat, n), (0.0, 1.0, 0.0)) for n in normals]
    # split by material
    groups = {}
    for pi_, poly in enumerate(polys):
        mi = mat_of_poly[pi_] if mat_of_poly else 0
        if not 0 <= mi < max(1, len(mat_list)):
            mi = 0
        groups.setdefault(mi, []).append(pi_)
    out = []
    for mi in sorted(groups):
        index, positions, nrm, uvs, cols, joints, weights, tris = {}, [], [], [[] for _ in uv_sets], [], [], [], []
        for pi_ in groups[mi]:
            poly = polys[pi_]
            corners = []
            for k in poly:
                cp = pv_cp[k]
                key = (cp, normals[k] if normals else None, tuple(u[k] for u in uv_sets), colors[k] if colors else None)
                vi = index.get(key)
                if vi is None:
                    vi = index[key] = len(positions)
                    positions.append(pos_w[cp])
                    if normals:
                        nrm.append(normals[k])
                    for s, u in enumerate(uv_sets):
                        uvs[s].append((u[k][0], 1.0 - u[k][1]))
                    if colors:
                        c = colors[k]
                        cols.append((c[0], c[1], c[2], c[3]))
                    if skin is not None:
                        joints.append(skin[cp][0])
                        weights.append(skin[cp][1])
                corners.append(vi)
            pts = [positions[v] for v in corners]
            for a, b, c in triangulate(pts):
                t = (corners[a], corners[b], corners[c])
                if t[0] == t[1] or t[1] == t[2] or t[0] == t[2]:
                    continue
                tris.append((t[0], t[2], t[1]) if flip else t)
        if not tris:
            continue
        if not nrm:
            nrm = S.smooth_normals(positions, tris)
            notes.add("Normals were computed for meshes that had none.")
        mat = mat_list[mi] if mat_list and mi < len(mat_list) else None
        mname = name if len(groups) == 1 else "%s.%d" % (name, mi)
        out.append(S.Mesh(name=mname, positions=positions, normals=nrm, uvs=uvs, colors=cols, joints=joints,
                          weights=weights, triangles=tris, material=mat))
    return out


def main(argv=None):  # pragma: no cover - a quick look from the command line
    import json
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: fbx.py model.fbx")
        return 2
    try:
        sc = read(args[0])
    except _scene.ReadError as e:
        print(str(e))
        return 1
    print(json.dumps(sc.summary(), indent=1))
    for n in sc.meta.get("notes", []):
        print("-", n)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
