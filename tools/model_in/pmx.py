"""PMX 2.0 / 2.1 (MikuMikuDance) reader -> the neutral Scene (tools/model_in/scene.py).

Written by us from the public PMX 2.0/2.1 format description (no code taken from any other tool).
Pure standard library.

What it reads:
- header: text encoding (UTF-16LE or UTF-8), additional-UV count, the six index sizes
- model names (JP/EN) and comments (JP/EN); the comments are where authors put their terms, so they go to
  meta["licence_text"]. PMX has no licence fields, so the model is private only until the user says otherwise.
- vertices: BDEF1, BDEF2, BDEF4, SDEF (read as BDEF2; its C/R0/R1 stay in parse()'s output only), QDEF (read as
  BDEF4); the 4 heaviest bones kept, weights renormalised
- faces, textures (paths, resolved next to the .pmx), materials (diffuse colour + texture; sphere, toon, edge,
  flags noted in meta), one Mesh per material
- bones: JP name as the bone name (the MMD name tables key on it), EN name, parent, rest = position - parent
  position (MMD bones have no rest rotation), IK chains / inherit / fixed axis noted in meta
- morphs: into meta, not applied; display frames, rigid bodies, joints and soft bodies counted as dropped

Conversion: MMD is left-handed, Y up, about 8 cm per unit, the model facing -Z. The Scene is right-handed, Y up,
metres. We scale by MMD_UNIT_M (0.08), flip Z (so the model faces +Z, like glTF) and reverse the triangle
winding so front faces stay front faces. Rotations: x and y turn the other way, z stays.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

from .scene import Bone, Material, Mesh, ReadError, Scene, Skeleton, Texture, new_meta, normalize, top4

MMD_UNIT_M = 0.08  # 1 MMD unit is about 8 cm (Miku is ~20 units = ~1.58 m)

WEIGHT_KINDS = ("BDEF1", "BDEF2", "BDEF4", "SDEF", "QDEF")
MORPH_KINDS = ("group", "vertex", "bone", "uv", "uv1", "uv2", "uv3", "uv4", "material", "flip", "impulse")
PANELS = ("hidden", "eyebrows", "eyes", "mouth", "other")
SPHERE_MODES = ("off", "multiply", "add", "sub-texture")

# material draw flags
M_DOUBLE_SIDED = 0x01
M_GROUND_SHADOW = 0x02
M_DRAW_SHADOW = 0x04
M_RECEIVE_SHADOW = 0x08
M_EDGE = 0x10
M_VERTEX_COLOUR = 0x20
M_POINTS = 0x40
M_LINES = 0x80

# bone flags
B_TAIL_IS_BONE = 0x0001
B_ROTATE = 0x0002
B_TRANSLATE = 0x0004
B_VISIBLE = 0x0008
B_ENABLED = 0x0010
B_IK = 0x0020
B_INHERIT_ROT = 0x0100
B_INHERIT_POS = 0x0200
B_FIXED_AXIS = 0x0400
B_LOCAL_AXES = 0x0800
B_AFTER_PHYSICS = 0x1000
B_EXTERNAL_PARENT = 0x2000

_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".bmp": "image/bmp",
    ".tga": "image/x-tga", ".dds": "image/vnd-ms.dds", ".gif": "image/gif",
    ".sph": "image/bmp", ".spa": "image/bmp",  # sphere maps are BMP files with another name
}


class PmxError(ReadError):
    """A PMX file we can't read, said in plain words."""


# ---------------------------------------------------------------------------------------------------------------
# byte reader


class _Reader:
    def __init__(self, data: bytes):
        self.d = memoryview(data)
        self.p = 0
        self.enc = "utf-16-le"
        self.add_uv = 0
        self.vsize = self.tsize = self.msize = self.bsize = self.morsize = self.rsize = 4

    def need(self, n: int, what: str):
        if self.p + n > len(self.d):
            raise PmxError(f"The PMX file ends too early (while reading {what}); it may be cut short or damaged.")

    def unpack(self, fmt: struct.Struct, what: str):
        self.need(fmt.size, what)
        v = fmt.unpack_from(self.d, self.p)
        self.p += fmt.size
        return v

    def u8(self, what="a byte"):
        return self.unpack(_U8, what)[0]

    def i32(self, what="a number"):
        return self.unpack(_I32, what)[0]

    def f32(self, what="a number"):
        return self.unpack(_F32, what)[0]

    def vec(self, n: int, what="a vector"):
        return self.unpack(_VEC[n], what)

    def count(self, what: str, limit: int = 50_000_000) -> int:
        n = self.i32(what + " count")
        if n < 0 or n > limit:
            raise PmxError(f"The PMX file has a broken {what} count ({n}).")
        return n

    def text(self, what="a name") -> str:
        n = self.i32(what)
        if n < 0:
            raise PmxError(f"The PMX file has a broken text length ({n}) in {what}.")
        self.need(n, what)
        raw = bytes(self.d[self.p:self.p + n])
        self.p += n
        return raw.decode(self.enc, errors="replace").rstrip("\x00")

    def index(self, size: int, what: str) -> int:
        """A signed index (bone, texture, material, morph, rigid body); -1 means none."""
        return self.unpack(_SIDX[size], what)[0]

    def vindex(self, what="a vertex index") -> int:
        return self.unpack(_UIDX[self.vsize], what)[0]


_U8 = struct.Struct("<B")
_I32 = struct.Struct("<i")
_F32 = struct.Struct("<f")
_U16 = struct.Struct("<H")
_VEC = {n: struct.Struct("<%df" % n) for n in (2, 3, 4)}
_SIDX = {1: struct.Struct("<b"), 2: struct.Struct("<h"), 4: struct.Struct("<i")}
_UIDX = {1: struct.Struct("<B"), 2: struct.Struct("<H"), 4: struct.Struct("<i")}


# ---------------------------------------------------------------------------------------------------------------
# parse: bytes -> a plain dict in MMD's own units and axes (no conversion yet)


def parse(data: bytes) -> dict:
    """Read a PMX file into a plain dict, in MMD units and left-handed axes. read()/read_bytes() convert it."""
    data = bytes(data)
    if data[:3] == b"Pmd":
        raise PmxError("This is a PMD file (the older MMD format). Only PMX is read; "
                       "open it in PMX Editor and save it as PMX first.")
    if data[:4] != b"PMX ":
        raise PmxError("This isn't a PMX file (it doesn't start with 'PMX ').")
    r = _Reader(data)
    r.p = 4
    version = round(r.f32("the version"), 2)
    if version not in (2.0, 2.1):
        raise PmxError(f"PMX version {version} isn't supported (only 2.0 and 2.1).")
    nglob = r.u8("the header")
    if nglob < 8:
        raise PmxError(f"The PMX header is too short ({nglob} settings, 8 needed).")
    r.need(nglob, "the header")
    glob = bytes(r.d[r.p:r.p + nglob])
    r.p += nglob
    if glob[0] not in (0, 1):
        raise PmxError(f"The PMX text encoding ({glob[0]}) is unknown (0 = UTF-16, 1 = UTF-8).")
    r.enc = "utf-16-le" if glob[0] == 0 else "utf-8"
    r.add_uv = glob[1]
    if r.add_uv > 4:
        raise PmxError(f"The PMX file says it has {r.add_uv} extra UV sets (4 at most).")
    sizes = glob[2:8]
    for s, what in zip(sizes, ("vertex", "texture", "material", "bone", "morph", "rigid body")):
        if s not in (1, 2, 4):
            raise PmxError(f"The PMX {what} index size ({s}) is broken (1, 2 or 4).")
    r.vsize, r.tsize, r.msize, r.bsize, r.morsize, r.rsize = sizes

    m: dict = {
        "version": version,
        "encoding": "UTF-16LE" if glob[0] == 0 else "UTF-8",
        "additional_uvs": r.add_uv,
        "index_sizes": dict(zip(("vertex", "texture", "material", "bone", "morph", "rigid_body"), sizes)),
        "name": r.text("the model name"),
        "name_en": r.text("the English model name"),
        "comment": r.text("the model comment"),
        "comment_en": r.text("the English model comment"),
        "notes": [],
    }
    m["vertices"] = _vertices(r)
    m["faces"] = _faces(r, len(m["vertices"]["pos"]))
    m["textures"] = [r.text("a texture path") for _ in range(r.count("texture"))]
    m["materials"] = _materials(r)
    m["bones"] = _bones(r)
    m["morphs"] = _morphs(r)
    # The rest isn't used by the Scene; count it, but a cut-short tail doesn't stop the import.
    m["display_frames"] = m["rigid_bodies"] = m["joints"] = m["soft_bodies"] = 0
    try:
        m["display_frames"] = _skip_frames(r)
        m["rigid_bodies"] = _skip_rigid(r)
        m["joints"] = _skip_joints(r)
        if version >= 2.1 and r.p < len(r.d):
            m["soft_bodies"] = r.count("soft body")
    except PmxError as e:
        m["notes"].append("the physics part at the end couldn't be read (" + str(e) + ")")
    _check(m)
    return m


def _vertices(r: _Reader) -> dict:
    n = r.count("vertex")
    pos, nrm, uv = [], [], []
    extra = [[] for _ in range(r.add_uv)]
    joints, weights, kinds = [], [], [0, 0, 0, 0, 0]
    sdef = []  # (vertex, C, R0, R1) for later, if anyone wants real SDEF
    base = struct.Struct("<8f")
    v4 = _VEC[4]
    code = {1: "b", 2: "h", 4: "i"}[r.bsize]
    i1 = struct.Struct("<" + code)
    i2 = struct.Struct("<2" + code + "f")
    i4 = struct.Struct("<4" + code + "4f")
    sdef_tail = struct.Struct("<9f")
    for v in range(n):
        x, y, z, nx, ny, nz, u, vv = r.unpack(base, "the vertices")
        pos.append((x, y, z))
        nrm.append((nx, ny, nz))
        uv.append((u, vv))
        for k in range(r.add_uv):
            extra[k].append(r.unpack(v4, "the vertices"))
        kind = r.u8("a vertex weight type")
        if kind > 4:
            raise PmxError(f"Vertex {v} has an unknown weight type ({kind}).")
        kinds[kind] += 1
        if kind == 0:
            (b,) = r.unpack(i1, "the vertex weights")
            j, w = (b, -1, -1, -1), (1.0, 0.0, 0.0, 0.0)
        elif kind in (1, 3):
            b1, b2, w1 = r.unpack(i2, "the vertex weights")
            j, w = (b1, b2, -1, -1), (w1, 1.0 - w1, 0.0, 0.0)
            if kind == 3:
                c = r.unpack(sdef_tail, "the SDEF data")
                sdef.append((v, c[0:3], c[3:6], c[6:9]))
        else:
            vals = r.unpack(i4, "the vertex weights")
            j, w = tuple(vals[:4]), tuple(vals[4:])
        r.f32("the edge scale")
        joints.append(j)
        weights.append(w)
    return {"pos": pos, "normal": nrm, "uv": uv, "extra_uv": extra, "joints": joints, "weights": weights,
            "kinds": dict(zip(WEIGHT_KINDS, kinds)), "sdef": sdef}


def _faces(r: _Reader, nverts: int) -> list:
    n = r.count("face index")
    if n % 3:
        raise PmxError(f"The PMX face list has {n} indices, which isn't a multiple of 3.")
    code = {1: "B", 2: "H", 4: "i"}[r.vsize]
    fmt = struct.Struct("<%d%s" % (n, code))
    idx = r.unpack(fmt, "the faces")
    bad = next((i for i in idx if i < 0 or i >= nverts), None)
    if bad is not None:
        raise PmxError(f"A face points at vertex {bad}, but the model has {nverts} vertices.")
    return [idx[i:i + 3] for i in range(0, n, 3)]


def _materials(r: _Reader) -> list:
    out = []
    for _ in range(r.count("material")):
        mat = {"name": r.text("a material name"), "name_en": r.text("a material name")}
        mat["diffuse"] = r.vec(4, "a material")
        mat["specular"] = r.vec(3, "a material")
        mat["specular_power"] = r.f32("a material")
        mat["ambient"] = r.vec(3, "a material")
        mat["flags"] = r.u8("a material")
        mat["edge_colour"] = r.vec(4, "a material")
        mat["edge_size"] = r.f32("a material")
        mat["texture"] = r.index(r.tsize, "a material texture")
        mat["sphere"] = r.index(r.tsize, "a material sphere texture")
        mode = r.u8("a material")
        mat["sphere_mode"] = SPHERE_MODES[mode] if mode < len(SPHERE_MODES) else str(mode)
        shared = r.u8("a material")
        if shared:
            mat["toon"] = None
            mat["toon_shared"] = r.u8("a material")  # 0..9 = toon01.bmp .. toon10.bmp
        else:
            mat["toon"] = r.index(r.tsize, "a material toon texture")
            mat["toon_shared"] = None
        mat["memo"] = r.text("a material memo")
        mat["index_count"] = r.i32("a material face count")
        out.append(mat)
    return out


def _bones(r: _Reader) -> list:
    out = []
    bs = r.bsize
    for _ in range(r.count("bone")):
        b = {"name": r.text("a bone name"), "name_en": r.text("a bone name")}
        b["position"] = r.vec(3, "a bone")
        b["parent"] = r.index(bs, "a bone parent")
        b["layer"] = r.i32("a bone")
        flags = b["flags"] = r.unpack(_U16, "a bone")[0]
        if flags & B_TAIL_IS_BONE:
            b["tail_bone"] = r.index(bs, "a bone tail")
        else:
            b["tail_offset"] = r.vec(3, "a bone tail")
        if flags & (B_INHERIT_ROT | B_INHERIT_POS):
            b["inherit"] = {"bone": r.index(bs, "a bone"), "weight": r.f32("a bone"),
                            "rotation": bool(flags & B_INHERIT_ROT), "position": bool(flags & B_INHERIT_POS)}
        if flags & B_FIXED_AXIS:
            b["fixed_axis"] = r.vec(3, "a bone axis")
        if flags & B_LOCAL_AXES:
            b["local_x"] = r.vec(3, "a bone axis")
            b["local_z"] = r.vec(3, "a bone axis")
        if flags & B_EXTERNAL_PARENT:
            b["external_parent"] = r.i32("a bone")
        if flags & B_IK:
            ik = {"target": r.index(bs, "an IK target"), "loops": r.i32("an IK chain"),
                  "limit_angle": r.f32("an IK chain"), "links": []}
            for _ in range(r.count("IK link", 10_000)):
                link = {"bone": r.index(bs, "an IK link")}
                if r.u8("an IK link"):
                    link["min"] = r.vec(3, "an IK limit")
                    link["max"] = r.vec(3, "an IK limit")
                ik["links"].append(link)
            b["ik"] = ik
        out.append(b)
    return out


def _morphs(r: _Reader) -> list:
    out = []
    v4, v3 = _VEC[4], _VEC[3]
    mat_tail = struct.Struct("<B4f3ff3f4ff4f4f4f")
    for _ in range(r.count("morph")):
        mo = {"name": r.text("a morph name"), "name_en": r.text("a morph name")}
        panel = r.u8("a morph")
        kind = r.u8("a morph")
        mo["panel"] = PANELS[panel] if panel < len(PANELS) else str(panel)
        if kind >= len(MORPH_KINDS):
            raise PmxError(f"Morph '{mo['name']}' has an unknown type ({kind}).")
        mo["kind"] = MORPH_KINDS[kind]
        offs = []
        for _ in range(r.count("morph offset")):
            if kind in (0, 9):  # group, flip
                offs.append((r.index(r.morsize, "a morph"), r.f32("a morph")))
            elif kind == 1:  # vertex
                offs.append((r.vindex("a morph"), r.unpack(v3, "a morph")))
            elif kind == 2:  # bone: translation, rotation quaternion (x, y, z, w)
                offs.append((r.index(r.bsize, "a morph"), r.unpack(v3, "a morph"), r.unpack(v4, "a morph")))
            elif kind in (3, 4, 5, 6, 7):  # uv, extra uv 1..4
                offs.append((r.vindex("a morph"), r.unpack(v4, "a morph")))
            elif kind == 8:  # material
                mi = r.index(r.msize, "a morph")
                t = r.unpack(mat_tail, "a morph")
                offs.append({"material": mi, "op": "multiply" if t[0] == 0 else "add",
                             "diffuse": t[1:5], "specular": t[5:8], "specular_power": t[8],
                             "ambient": t[9:12], "edge_colour": t[12:16], "edge_size": t[16],
                             "texture_tint": t[17:21], "sphere_tint": t[21:25], "toon_tint": t[25:29]})
            else:  # impulse: rigid body, local flag, velocity, torque
                offs.append((r.index(r.rsize, "a morph"), r.u8("a morph"), r.unpack(v3, "a morph"),
                             r.unpack(v3, "a morph")))
        mo["offsets"] = offs
        out.append(mo)
    return out


def _skip_frames(r: _Reader) -> int:
    n = r.count("display frame")
    for _ in range(n):
        r.text("a display frame")
        r.text("a display frame")
        r.u8("a display frame")
        for _ in range(r.count("display frame item")):
            if r.u8("a display frame item") == 0:
                r.index(r.bsize, "a display frame item")
            else:
                r.index(r.morsize, "a display frame item")
    return n


_RIGID = struct.Struct("<BHB3f3f3f5fB")
_JOINT_TAIL = struct.Struct("<24f")  # position, rotation, 4 limits, 2 springs


def _skip_rigid(r: _Reader) -> int:
    n = r.count("rigid body")
    for _ in range(n):
        r.text("a rigid body")
        r.text("a rigid body")
        r.index(r.bsize, "a rigid body")
        r.unpack(_RIGID, "a rigid body")
    return n


def _skip_joints(r: _Reader) -> int:
    n = r.count("joint")
    for _ in range(n):
        r.text("a joint")
        r.text("a joint")
        r.u8("a joint")
        r.index(r.rsize, "a joint")
        r.index(r.rsize, "a joint")
        r.unpack(_JOINT_TAIL, "a joint")
    return n


def _check(m: dict) -> None:
    nb, nt, nv = len(m["bones"]), len(m["textures"]), len(m["vertices"]["pos"])
    for mat in m["materials"]:
        if mat["index_count"] < 0 or mat["index_count"] % 3:
            raise PmxError(f"Material '{mat['name']}' has a broken face count ({mat['index_count']}).")
    # the materials share the face list in order; a mismatch is tolerated (extra faces dropped, short ones cut)
    total, have = sum(mat["index_count"] for mat in m["materials"]) // 3, len(m["faces"])
    if total < have:
        m["notes"].append(_plural(have - total, "face belongs", "faces belong") + " to no material; dropped")
    elif total > have:
        m["notes"].append(f"the materials ask for {_plural(total - have, 'face', 'faces')} more than the model "
                          "has; cut short")
        for key in ("texture", "sphere", "toon"):
            t = mat.get(key)
            if t is not None and t >= nt:
                m["notes"].append(f"material '{mat['name']}' points at missing texture {t}; ignored")
                mat[key] = -1
    for i, b in enumerate(m["bones"]):
        if b["parent"] >= nb or b["parent"] == i:
            m["notes"].append(f"bone '{b['name']}' has a broken parent ({b['parent']}); made a root")
            b["parent"] = -1
    # a parent loop would hang anything that walks the tree: cut it
    for i, b in enumerate(m["bones"]):
        seen, p = {i}, b["parent"]
        while p >= 0:
            if p in seen:
                m["notes"].append(f"bone '{b['name']}' is in a parent loop; made a root")
                b["parent"] = -1
                break
            seen.add(p)
            p = m["bones"][p]["parent"]
    bad = 0
    for j in m["vertices"]["joints"]:
        if any(x >= nb for x in j):
            bad += 1
    if bad:
        m["notes"].append(f"{bad} vertices point at bones that don't exist; those weights dropped")
    if nv and not nb:
        m["notes"].append("the model has no bones")


# ---------------------------------------------------------------------------------------------------------------
# convert: the parsed dict -> Scene (Y up, right-handed, metres, facing +Z)


def read(path, scale: float = MMD_UNIT_M, **_opts) -> Scene:
    """Read a .pmx file into a Scene. Texture paths are resolved next to the file."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise PmxError(f"Can't open the PMX file ({e.strerror or e}).") from None
    return read_bytes(data, base_dir=path.parent, scale=scale)


def read_bytes(data: bytes, base_dir=None, scale: float = MMD_UNIT_M) -> Scene:
    return to_scene(parse(data), base_dir=base_dir, scale=scale)


def _pos(v, s):
    return (v[0] * s + 0.0, v[1] * s + 0.0, -v[2] * s + 0.0)  # + 0.0: no -0.0


def _dir(v):
    return (v[0] + 0.0, v[1] + 0.0, -v[2] + 0.0)


def _quat(q):
    # mirroring Z: a turn about x or y goes the other way, about z stays
    return normalize((-q[0], -q[1], q[2], q[3]), (0.0, 0.0, 0.0, 1.0))


def _lin(c: float) -> float:
    c = min(max(c, 0.0), 1.0)
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _bone_order(bones: list) -> list:
    """PMX order, except that a parent always comes before its children (the Scene needs that)."""
    order, placed = [], set()
    for i in range(len(bones)):
        chain, j = [], i
        while j >= 0 and j not in placed:
            chain.append(j)
            j = bones[j]["parent"]
        for k in reversed(chain):
            order.append(k)
            placed.add(k)
    return order


def _texture(raw: str, base_dir) -> Texture:
    rel = raw.replace("\\", "/").strip()
    name = rel.rsplit("/", 1)[-1]
    stem, ext = os.path.splitext(name)
    path = str(Path(base_dir) / rel) if base_dir is not None else rel
    return Texture(name=stem, mime=_MIME.get(ext.lower(), "application/octet-stream"), path=path,
                   extras={"pmx_path": raw})


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def to_scene(m: dict, base_dir=None, scale: float = MMD_UNIT_M) -> Scene:
    s = float(scale)
    meta = new_meta("pmx")
    licence_text = m["comment"].replace("\r\n", "\n").strip()
    en = m["comment_en"].replace("\r\n", "\n").strip()
    if en and en != licence_text:
        licence_text = (licence_text + "\n\n" + en).strip()
    meta.update({
        "version": m["version"], "encoding": m["encoding"],
        "model_name": m["name"], "model_name_en": m["name_en"],
        "licence": None,  # PMX has no licence fields: private only until the user says it may be shared
        "licence_text": licence_text,
        "share": "private",
        "source_units": f"MMD units, 1 unit ~ {s * 100:g} cm (scaled by {s:g} to metres)",
        "scale": s,
        "source_axes": "left-handed, Y up, model facing -Z (Z flipped, triangle winding reversed)",
        "forward": "+Z",
    })
    scene = Scene(meta=meta)
    scene.note(f"Scaled from MMD units to metres (1 unit ~ {s * 100:g} cm) and Z flipped to right-handed.")
    scene.note("PMX has no licence fields: the author's comment is in licence_text; "
               "the model stays private until you say it may be shared.")

    # bones -------------------------------------------------------------------------------------------------------
    pb = m["bones"]
    order = _bone_order(pb)
    new = {old: k for k, old in enumerate(order)}
    nb = len(order)
    if order != list(range(nb)):
        meta["pmx_bone_order"] = order
        scene.note("Bones reordered so every parent comes before its children (pmx_bone_order).")

    def bi(old):
        return new.get(old, -1) if old is not None and old >= 0 else -1

    world = [_pos(b["position"], s) for b in pb]
    ik = []
    for old in order:
        b = pb[old]
        par = b["parent"]
        w = world[old]
        local = (w[0] - world[par][0], w[1] - world[par][1], w[2] - world[par][2]) if par >= 0 else w
        ex = {"name_en": b["name_en"], "pmx_index": old, "flags": b["flags"], "layer": b["layer"],
              "after_physics": bool(b["flags"] & B_AFTER_PHYSICS), "visible": bool(b["flags"] & B_VISIBLE)}
        if "tail_bone" in b:
            ex["tail_bone"] = bi(b["tail_bone"])
        else:
            ex["tail_offset"] = _pos(b["tail_offset"], s)
        if "inherit" in b:
            ex["inherit"] = dict(b["inherit"], bone=bi(b["inherit"]["bone"]))
        if "fixed_axis" in b:
            ex["fixed_axis"] = normalize(_dir(b["fixed_axis"]))
        if "local_x" in b:
            ex["local_x"] = normalize(_dir(b["local_x"]))
            ex["local_z"] = normalize(_dir(b["local_z"]))
        if "external_parent" in b:
            ex["external_parent"] = b["external_parent"]
        if "ik" in b:
            links = []
            for link in b["ik"]["links"]:
                li = {"bone": bi(link["bone"])}
                if "min" in link:  # angle limits (radians), mirrored: x and y swap sign and min/max
                    lo, hi = link["min"], link["max"]
                    li["min"] = (-hi[0], -hi[1], lo[2])
                    li["max"] = (-lo[0], -lo[1], hi[2])
                links.append(li)
            ik.append({"bone": new[old], "target": bi(b["ik"]["target"]), "loops": b["ik"]["loops"],
                       "limit_angle": b["ik"]["limit_angle"], "links": links})
            ex["ik"] = True
        scene.skeleton.bones.append(Bone(
            name=b["name"], parent=bi(par), translation=local,
            inverse_bind=(1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, -w[0], -w[1], -w[2], 1.0),
            extras=ex))
    meta["ik"] = ik
    if ik:
        scene.note(f"{_plural(len(ik), 'IK chain', 'IK chains')} kept in meta (not solved).")

    # textures ----------------------------------------------------------------------------------------------------
    scene.textures = [_texture(t, base_dir) for t in m["textures"]]
    if base_dir is not None:
        missing = sum(1 for t in scene.textures if not Path(t.path).is_file())
        if missing:
            scene.note(f"{_plural(missing, 'texture file isn', 'texture files aren')}'t next to the model.")

    # materials ---------------------------------------------------------------------------------------------------
    for mat in m["materials"]:
        d = mat["diffuse"]
        tex = {}
        if mat["texture"] >= 0:
            tex["base"] = mat["texture"]
        scene.materials.append(Material(
            name=mat["name"],
            base_color=(_lin(d[0]), _lin(d[1]), _lin(d[2]), min(max(d[3], 0.0), 1.0)),
            textures=tex,
            alpha_mode="BLEND" if d[3] < 1.0 else "OPAQUE",
            double_sided=bool(mat["flags"] & M_DOUBLE_SIDED),
            metallic=0.0, roughness=1.0, shader="mmd",
            extras={"name_en": mat["name_en"], "diffuse": d, "specular": mat["specular"],
                    "specular_power": mat["specular_power"], "ambient": mat["ambient"], "flags": mat["flags"],
                    "edge": bool(mat["flags"] & M_EDGE), "edge_colour": mat["edge_colour"],
                    "edge_size": mat["edge_size"],
                    "sphere": mat["sphere"] if mat["sphere"] >= 0 else None, "sphere_mode": mat["sphere_mode"],
                    "toon": mat["toon"] if mat["toon"] is not None and mat["toon"] >= 0 else None,
                    "toon_shared": (None if mat["toon_shared"] is None
                                    else "toon%02d.bmp" % (mat["toon_shared"] + 1)),
                    "memo": mat["memo"]}))
    if any(x.extras["sphere"] is not None or x.extras["toon"] is not None or x.extras["toon_shared"]
           for x in scene.materials):
        scene.note("Sphere and toon textures noted in material extras (not used as PBR slots).")
    if any(x["flags"] & (M_POINTS | M_LINES) for x in m["materials"]):
        scene.note("Point/line drawing on some materials ignored (drawn as triangles).")

    # meshes: one per material ------------------------------------------------------------------------------------
    v = m["vertices"]
    nv = len(v["pos"])
    pos = [_pos(p, s) for p in v["pos"]]
    nrm = [normalize(_dir(n), (0.0, 1.0, 0.0)) for n in v["normal"]]
    skin_j, skin_w = [], []
    unweighted = 0
    if nb:
        for js, ws in zip(v["joints"], v["weights"]):
            acc = {}
            for j, w in zip(js, ws):
                if 0 <= j < nb and w > 0.0:
                    acc[new[j]] = acc.get(new[j], 0.0) + w
            t = top4(acc.items())
            if t is None:
                unweighted += 1
                t = ((0, 0, 0, 0), (1.0, 0.0, 0.0, 0.0))
            skin_j.append(t[0])
            skin_w.append(t[1])
    if unweighted:
        scene.note(f"{_plural(unweighted, 'vertex', 'vertices')} had no usable weight; put on the first bone.")
    kinds = v["kinds"]
    if kinds["SDEF"]:
        scene.note(f"{_plural(kinds['SDEF'], 'SDEF vertex', 'SDEF vertices')} read as BDEF2 (plain 2-bone blend).")
    if kinds["QDEF"]:
        scene.note(f"{_plural(kinds['QDEF'], 'QDEF vertex', 'QDEF vertices')} read as BDEF4 (plain 4-bone blend).")
    if m["additional_uvs"]:
        scene.note(f"{_plural(m['additional_uvs'], 'extra UV set', 'extra UV sets')} kept as UV sets 1+ "
                   "(x, y only; full values in mesh extras).")

    where = [[] for _ in range(nv)]  # PMX vertex -> [(mesh, local vertex)], for morphs
    start = 0
    for mi, mat in enumerate(m["materials"]):
        faces = m["faces"][start // 3:(start + mat["index_count"]) // 3]
        start += mat["index_count"]
        if not faces:
            continue
        src = sorted({x for f in faces for x in f})  # the vertices this material uses, in PMX order
        remap = {x: k for k, x in enumerate(src)}
        # reversed: clockwise front faces (MMD) -> counter-clockwise (Scene)
        tris = [(remap[c], remap[b], remap[a]) for a, b, c in faces]
        mesh_index = len(scene.meshes)
        for k, x in enumerate(src):
            where[x].append((mesh_index, k))
        mesh = Mesh(name=mat["name"], positions=[pos[x] for x in src], normals=[nrm[x] for x in src],
                    uvs=[[tuple(v["uv"][x]) for x in src]], triangles=tris, material=mi,
                    extras={"pmx_vertices": src})
        for extra in v["extra_uv"]:
            mesh.uvs.append([(extra[x][0], extra[x][1]) for x in src])
        if v["extra_uv"]:
            mesh.extras["pmx_extra_uv"] = [[extra[x] for x in src] for extra in v["extra_uv"]]
            if mat["flags"] & M_VERTEX_COLOUR:  # PMX 2.1: extra UV 1 holds the vertex colour (RGBA)
                mesh.colors = [tuple(min(max(c, 0.0), 1.0) for c in v["extra_uv"][0][x]) for x in src]
                scene.note("Vertex colours taken from extra UV 1 (the PMX 2.1 vertex-colour flag).")
        if nb:
            mesh.joints = [skin_j[x] for x in src]
            mesh.weights = [skin_w[x] for x in src]
        scene.meshes.append(mesh)

    # morphs: kept in meta, not applied ---------------------------------------------------------------------------
    morphs = []
    for mo in m["morphs"]:
        out = {"name": mo["name"], "name_en": mo["name_en"], "panel": mo["panel"], "kind": mo["kind"]}
        k = mo["kind"]
        if k == "vertex" or k.startswith("uv"):
            per = {}
            for vi, d in mo["offsets"]:
                val = _pos(d, s) if k == "vertex" else tuple(d)
                for mesh_i, local in (where[vi] if 0 <= vi < nv else ()):
                    per.setdefault(mesh_i, []).append((local, val))
            out["meshes"] = per
        elif k == "bone":
            out["bones"] = [(bi(b), _pos(t, s), _quat(q)) for b, t, q in mo["offsets"]]
        elif k in ("group", "flip"):
            out["morphs"] = [(i, w) for i, w in mo["offsets"]]
        elif k == "material":
            out["materials"] = mo["offsets"]
        else:  # impulse: rigid bodies aren't imported
            out["impulses"] = len(mo["offsets"])
        morphs.append(out)
    meta["morphs"] = morphs
    if morphs:
        scene.note(f"{_plural(len(morphs), 'morph', 'morphs')} kept in meta, not applied.")

    dropped = [_plural(m[key], one, many) for key, one, many in (
        ("display_frames", "display frame", "display frames"), ("rigid_bodies", "rigid body", "rigid bodies"),
        ("joints", "physics joint", "physics joints"), ("soft_bodies", "soft body", "soft bodies")) if m[key]]
    if dropped:
        scene.note("Dropped (physics and MMD panels aren't imported): " + ", ".join(dropped) + ".")
    for n in m["notes"]:
        scene.note(n[0].upper() + n[1:] + ".")
    return scene
