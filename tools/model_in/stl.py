"""STL reader (binary and ASCII) -> the neutral Scene (tools/model_in/scene.py).

Written from the public STL description (3D Systems, "StereoLithography Interface Specification", 1989), standard
library only.

  * Binary: 80-byte header, uint32 triangle count, then 50 bytes per triangle (normal, 3 corners, uint16 attribute).
    A file is binary when its size is exactly 84 + 50 * count (even when the header starts with "solid").
  * ASCII: solid / facet normal / outer loop / vertex x3 / endloop / endfacet / endsolid; several solids become
    several meshes.
  * Normals are flat. The face normal comes from the corner winding (counter-clockwise = outside, as the spec says);
    the stored normal is only used for a degenerate face.
  * Vertices are merged by position: by default corners that share a position *and* a face normal are one vertex
    (flat shading, but coplanar neighbours share vertices). `smooth=True` merges by position only and averages
    the normals (soft shading).
  * STL has no units or axis rule. The default is the 3D-printing convention: millimetres, Z up; `unit` (metres
    per file unit) and `up` change it. The meta says what was assumed.
Dropped: the per-face colour bits (two vendors use them in opposite ways).
"""
from __future__ import annotations

import math
import os
import struct

from .obj import axis_fix
from .scene import Mesh, ReadError, Scene, Skeleton, new_meta, read_file, smooth_normals


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / n, v[1] / n, v[2] / n) if n > 1e-20 else None


def is_binary(data: bytes) -> bool:
    if len(data) < 84:
        return False
    count = struct.unpack_from("<I", data, 80)[0]
    if len(data) == 84 + 50 * count:
        return True
    head = data[:512].lstrip()
    if head[:5].lower() == b"solid":
        try:
            head.decode("ascii")
            return False
        except UnicodeDecodeError:
            return True
    return True


def _binary_solids(data: bytes):
    count = struct.unpack_from("<I", data, 80)[0]
    avail = (len(data) - 84) // 50
    if count > avail:
        count = avail
    name = data[:80].split(b"\0", 1)[0].decode("latin-1").strip()
    if name.lower().startswith("solid"):
        name = name[5:].strip()
    faces = []
    unpack = struct.Struct("<12f").unpack_from
    off = 84
    for _ in range(count):
        f = unpack(data, off)
        faces.append(((f[0], f[1], f[2]), (f[3], f[4], f[5]), (f[6], f[7], f[8]), (f[9], f[10], f[11])))
        off += 50
    return [(name or "stl", faces)]


def _ascii_solids(text: str):
    solids = []
    name = None
    faces = None
    normal = (0.0, 0.0, 0.0)
    corners = []
    for raw in text.splitlines():
        toks = raw.split()
        if not toks:
            continue
        k = toks[0].lower()
        if k == "solid":
            name = " ".join(toks[1:]) or "stl"
            faces = []
            solids.append((name, faces))
        elif k == "facet":
            if faces is None:
                faces = []
                solids.append(("stl", faces))
            nums = toks[2:5] if len(toks) >= 5 and toks[1].lower() == "normal" else []
            normal = tuple(float(x) for x in nums) if len(nums) == 3 else (0.0, 0.0, 0.0)
            corners = []
        elif k == "vertex":
            corners.append((float(toks[1]), float(toks[2]), float(toks[3])))
        elif k == "endfacet":
            if len(corners) >= 3:
                # a facet "loop" may in theory hold more than 3 corners: fan it
                for i in range(1, len(corners) - 1):
                    faces.append((normal, corners[0], corners[i], corners[i + 1]))
            corners = []
    return solids


def read(path, unit: float = 0.001, up: str = "Z", smooth: bool = False) -> Scene:
    data = read_file(path, "STL")
    return parse(data, unit=unit, up=up, smooth=smooth, name=os.path.splitext(os.path.basename(str(path)))[0])


def parse(data: bytes, unit: float = 0.001, up: str = "Z", smooth: bool = False, name: str = "stl") -> Scene:
    binary = is_binary(data)
    if len(data) < 84 and data.lstrip()[:5].lower() != b"solid":
        raise ReadError("This STL file is too short to hold a model.")
    if binary:
        solids = [(name, _binary_solids(data)[0][1])]  # the 80-byte header is usually tool text: use the file name
    else:
        try:
            solids = _ascii_solids(data.decode("latin-1"))
        except (ValueError, IndexError):
            raise ReadError("This text STL file has a line it can't read.") from None
    fix = axis_fix(up)
    meshes = []
    degenerate = 0
    for sname, faces in solids:
        pos, normals, tris, index = [], [], [], {}
        for fn, a, b, c in faces:
            a, b, c = (fix((p[0] * unit, p[1] * unit, p[2] * unit)) for p in (a, b, c))
            n = _unit(_cross((b[0] - a[0], b[1] - a[1], b[2] - a[2]), (c[0] - a[0], c[1] - a[1], c[2] - a[2])))
            if n is None:
                degenerate += 1
                n = _unit(fix(fn)) or (0.0, 1.0, 0.0)
            nk = (round(n[0], 5), round(n[1], 5), round(n[2], 5))
            tri = []
            for p in (a, b, c):
                pk = (round(p[0], 9), round(p[1], 9), round(p[2], 9))
                key = pk if smooth else (pk, nk)
                i = index.get(key)
                if i is None:
                    i = index[key] = len(pos)
                    pos.append(p)
                    normals.append(n)
                tri.append(i)
            if tri[0] != tri[1] and tri[1] != tri[2] and tri[0] != tri[2]:
                tris.append(tuple(tri))
        if smooth:
            normals = smooth_normals(pos, tris)
        if tris:
            meshes.append(Mesh(name=sname or name, positions=pos, normals=normals, uvs=[], colors=[], joints=[],
                               weights=[], triangles=tris, material=None))
    if not meshes:
        raise ReadError("This STL file has no triangles, so there is nothing to import.")
    meta = new_meta("stl", stl_kind="binary" if binary else "ascii",
                    source_units="%g m per unit (assumed)" % unit, source_up=up.upper()[:1] or "Z",
                    share="private")
    scene = Scene(meshes=meshes, skeleton=Skeleton(), materials=[], textures=[], meta=meta)
    scene.note("STL has no units: read as %g m per unit%s." % (
        unit, " (millimetres, the 3D-printing habit)" if abs(unit - 0.001) < 1e-12 else ""))
    if not up.upper().startswith("Y"):
        scene.note("Turned from %s up to Y up." % up.upper()[:1])
    if degenerate:
        scene.note("%d zero-area faces kept their stored normal." % degenerate)
    return scene
