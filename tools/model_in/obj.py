"""Wavefront OBJ + MTL reader -> the neutral Scene (tools/model_in/scene.py).

Written from the public OBJ/MTL description (Wavefront "Advanced Visualizer" appendix B1/B2), standard library only.

What it reads:
  * v (x y z [w] [r g b]), vt (u [v [w]]), vn, and f with v, v/vt, v//vn and v/vt/vn corners; negative indices
    count back from the last vertex read so far; `\\` line continuation.
  * polygons of any size: a fan for convex ones, ear clipping for concave ones (a fan as the last fallback).
  * o / g start a new mesh; usemtl splits a mesh further (one material per Scene mesh).
  * mtllib -> newmtl, Kd, d / Tr, map_Kd, map_Bump / bump, norm, map_Ke, Ke, map_d, with texture options
    (-bm 1.0, -o u v w, ...) skipped.
What it drops (said in scene.meta["dropped"]): l / p elements, curves and surfaces, smoothing groups.

Conversions: OBJ has no units or axis rule. By default it is read as Y-up metres (the common convention), and
`up="Z"` / `scale=...` change that. UVs are flipped to the glTF convention (v -> 1 - v). Missing normals are
computed (smooth, area weighted) and the meta says so.
"""
from __future__ import annotations

import math
import os
import shlex

from .scene import Material, Mesh, ReadError, Scene, Skeleton, Texture, new_meta, read_file, smooth_normals

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".tga": "image/x-tga",
        ".bmp": "image/bmp", ".dds": "image/vnd-ms.dds", ".gif": "image/gif", ".tif": "image/tiff",
        ".tiff": "image/tiff", ".webp": "image/webp", ".ktx2": "image/ktx2"}


def mime_of(path: str) -> str:
    return MIME.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


# ------------------------------------------------------------------------------------------------- small vectors

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-20 else (0.0, 0.0, 0.0)


def newell_normal(pts):
    """Polygon normal by Newell's method (works for concave and slightly non-planar polygons)."""
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0, z0 = pts[i]
        x1, y1, z1 = pts[(i + 1) % n]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    return (nx, ny, nz)


# ------------------------------------------------------------------------------------------------- triangulation

def _project(pts, normal):
    """Drop the largest normal axis -> 2D points that keep the polygon's winding counter-clockwise."""
    ax = max(range(3), key=lambda i: abs(normal[i]))
    u, v = [(1, 2), (2, 0), (0, 1)][ax]
    flip = normal[ax] < 0
    out = [(p[u], p[v]) for p in pts]
    if flip:
        out = [(x, -y) for x, y in out]
    return out


def _area2(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def is_convex(pts2):
    n = len(pts2)
    for i in range(n):
        if _area2(pts2[i - 1], pts2[i], pts2[(i + 1) % n]) < -1e-12:
            return False
    return True


def _inside(p, a, b, c):
    eps = -1e-12
    return _area2(a, b, p) >= eps and _area2(b, c, p) >= eps and _area2(c, a, p) >= eps


EAR_CLIP_MAX = 128  # concave faces with more corners are split as a fan (same limit as fbx.py)


def ear_clip(pts2):
    """Ear clipping of a simple counter-clockwise 2D polygon -> local index triples, or None when it fails."""
    idx = list(range(len(pts2)))
    tris = []
    guard = 0
    while len(idx) > 3:
        guard += 1
        if guard > 4 * len(pts2) * len(pts2) + 16:
            return None
        n = len(idx)
        cut = False
        for k in range(n):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % n]
            a, b, c = pts2[i0], pts2[i1], pts2[i2]
            if _area2(a, b, c) <= 1e-14:
                continue
            if any(_inside(pts2[j], a, b, c) for j in idx if j not in (i0, i1, i2) and pts2[j] not in (a, b, c)):
                continue
            tris.append((i0, i1, i2))
            del idx[k]
            cut = True
            break
        if not cut:
            # Only collinear or degenerate corners left: drop a zero-area corner, else give up.
            for k in range(n):
                if abs(_area2(pts2[idx[k - 1]], pts2[idx[k]], pts2[idx[(k + 1) % n]])) <= 1e-14:
                    del idx[k]
                    cut = True
                    break
            if not cut:
                return None
    if len(idx) == 3 and _area2(pts2[idx[0]], pts2[idx[1]], pts2[idx[2]]) > 1e-14:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


def triangulate(pts3):
    """Local index triples for a polygon given by its 3D corner points (same winding as the input)."""
    n = len(pts3)
    if n < 3:
        return []
    if n == 3:
        return [(0, 1, 2)]
    normal = newell_normal(pts3)
    if _dot(normal, normal) < 1e-30:
        return [(0, i, i + 1) for i in range(1, n - 1)]
    pts2 = _project(pts3, normal)
    if n > EAR_CLIP_MAX or is_convex(pts2):  # ear clipping is ~n^3: a 30,000-corner face would take hours
        return [(0, i, i + 1) for i in range(1, n - 1)]
    tris = ear_clip(pts2)
    if not tris:
        return [(0, i, i + 1) for i in range(1, n - 1)]
    return tris


def axis_fix(up: str):
    """Function turning a point of the given up axis into Y-up right-handed coordinates."""
    up = (up or "Y").upper().replace("_UP", "")
    if up == "Z":
        return lambda p: (p[0], p[2], -p[1])
    if up == "X":
        return lambda p: (-p[1], p[0], p[2])
    return lambda p: (p[0], p[1], p[2])


# ------------------------------------------------------------------------------------------------- MTL

_TEX_OPT_ARGS = {"-blendu": 1, "-blendv": 1, "-bm": 1, "-boost": 1, "-cc": 1, "-clamp": 1, "-imfchan": 1,
                 "-mm": 2, "-o": 3, "-s": 3, "-t": 3, "-texres": 1, "-type": 1}


def _is_num(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def tex_file(args: str) -> str:
    """The file name of a map_* statement, after skipping its options."""
    toks = args.split()
    i = 0
    while i < len(toks) and toks[i].startswith("-") and toks[i].lower() in _TEX_OPT_ARGS:
        want = _TEX_OPT_ARGS[toks[i].lower()]
        i += 1
        if want == 3:  # up to three numbers
            got = 0
            while i < len(toks) and got < 3 and _is_num(toks[i]) and i < len(toks) - 1:
                i += 1
                got += 1
        else:
            i += min(want, max(0, len(toks) - 1 - i))
    name = " ".join(toks[i:]).strip()
    if len(name) >= 2 and name[0] == name[-1] == '"':
        name = name[1:-1]
    return name


def read_mtl(text: str):
    """MTL text -> {name: dict(Kd, d, Ke, maps{slot: file})} in file order."""
    mats = {}
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, rest = line.partition(" ")
        key = key.lower()
        rest = rest.strip()
        if key == "newmtl":
            cur = mats.setdefault(rest or "material%d" % len(mats),
                                  {"Kd": None, "d": 1.0, "Ke": None, "Pr": None, "Pm": None, "Ns": None, "maps": {}})
            continue
        if cur is None:
            continue
        vals = rest.split()
        if key == "kd" and len(vals) >= 3:
            cur["Kd"] = tuple(float(x) for x in vals[:3])
        elif key == "kd" and len(vals) == 1:
            cur["Kd"] = (float(vals[0]),) * 3
        elif key == "ke" and len(vals) >= 3:
            cur["Ke"] = tuple(float(x) for x in vals[:3])
        elif key in ("pr", "pm", "ns") and vals:
            cur[key.capitalize()] = float(vals[0])
        elif key == "d" and vals:
            cur["d"] = float(vals[-1])
        elif key == "tr" and vals:
            cur["d"] = 1.0 - float(vals[-1])
        elif key == "map_kd":
            cur["maps"]["base"] = tex_file(rest)
        elif key in ("norm", "map_kn"):  # PBR extension: a real normal map wins over a bump map
            cur["maps"]["normal"] = tex_file(rest)
        elif key in ("map_bump", "bump"):
            cur["maps"].setdefault("normal", tex_file(rest))
        elif key == "map_ke":
            cur["maps"]["emissive"] = tex_file(rest)
        elif key == "map_d":
            cur["maps"]["alpha"] = tex_file(rest)
    return mats


# ------------------------------------------------------------------------------------------------- OBJ

def _idx(tok: str, count: int):
    if not tok:
        return None
    i = int(tok)
    if i < 0:
        i = count + i
    else:
        i -= 1
    if i < 0 or i >= count:
        raise ReadError("A face points at element %s, but the file only has %d so far." % (tok, count))
    return i


def _logical_lines(text: str):
    buf = ""
    for raw in text.splitlines():
        if raw.endswith("\\"):
            buf += raw[:-1] + " "
            continue
        yield buf + raw
        buf = ""
    if buf:
        yield buf


def _read_text(path):
    data = read_file(path, "OBJ")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1", "replace")


def read(path, up: str = "Y", scale: float = 1.0, mtl_text: dict | None = None) -> Scene:
    """Read an .obj file. `mtl_text` maps mtl file names to their text (tests / in-memory use)."""
    path = str(path)
    return parse(_read_text(path), base_dir=os.path.dirname(os.path.abspath(path)), up=up, scale=scale,
                 mtl_text=mtl_text, name=os.path.splitext(os.path.basename(path))[0])


def parse(text: str, base_dir: str = ".", up: str = "Y", scale: float = 1.0, mtl_text: dict | None = None,
          name: str = "obj") -> Scene:
    """OBJ text -> Scene; a broken file raises ReadError with one plain sentence."""
    scene = _parse(text, base_dir, up, scale, mtl_text, name)
    if not scene.meshes:
        raise ReadError("The OBJ file has no faces, so there is nothing to import.")
    return scene


def _parse(text, base_dir, up, scale, mtl_text, name):
    fix = axis_fix(up)
    vs, vcs, vts, vns = [], [], [], []
    dropped = {}
    warnings = []
    mtl_defs = {}
    groups = {}  # (group, material) -> dict(corners map, positions...)
    order = []
    group = name
    material = None

    def bucket():
        key = (group, material)
        b = groups.get(key)
        if b is None:
            b = groups[key] = {"map": {}, "corners": [], "tris": []}
            order.append(key)
        return b

    lineno = 0
    try:
        for lineno, line in enumerate(_logical_lines(text), 1):
            line = line.strip()
            if not line or line[0] == "#":
                continue
            key, _, rest = line.partition(" ")
            rest = rest.strip()
            if key == "v":
                p = rest.split()
                vs.append(fix((float(p[0]) * scale, float(p[1]) * scale, float(p[2]) * scale)))
                if len(p) == 6:  # x y z r g b (common colour extension)
                    vcs.append(tuple(float(x) for x in p[3:6]) + (1.0,))
                elif len(p) >= 7:  # x y z w r g b
                    vcs.append(tuple(float(x) for x in p[4:7]) + (1.0,))
                else:
                    vcs.append(None)
            elif key == "vt":
                p = rest.split()
                u = float(p[0])
                v = float(p[1]) if len(p) > 1 else 0.0
                vts.append((u, 1.0 - v))
            elif key == "vn":
                p = rest.split()
                vns.append(_norm(fix((float(p[0]), float(p[1]), float(p[2])))))
            elif key == "f":
                b = bucket()
                corners = []
                for tok in rest.split():
                    parts = tok.split("/")
                    vi = _idx(parts[0], len(vs))
                    ti = _idx(parts[1], len(vts)) if len(parts) > 1 else None
                    ni = _idx(parts[2], len(vns)) if len(parts) > 2 else None
                    ck = (vi, ti, ni)
                    li = b["map"].get(ck)
                    if li is None:
                        li = b["map"][ck] = len(b["corners"])
                        b["corners"].append(ck)
                    corners.append(li)
                if len(corners) < 3:
                    dropped["faces with fewer than 3 corners"] = dropped.get("faces with fewer than 3 corners", 0) + 1
                    continue
                pts = [vs[b["corners"][c][0]] for c in corners]
                for t in triangulate(pts):
                    b["tris"].append((corners[t[0]], corners[t[1]], corners[t[2]]))
            elif key in ("o", "g"):
                group = rest or name
            elif key == "usemtl":
                material = rest or None
            elif key == "mtllib":
                for fname in _mtl_names(rest, base_dir, mtl_text):
                    txt = None
                    if mtl_text and fname in mtl_text:
                        txt = mtl_text[fname]
                    else:
                        fp = os.path.join(base_dir, fname)
                        if os.path.isfile(fp):
                            txt = _read_text(fp)
                    if txt is None:
                        warnings.append("material file not found: %s" % fname)
                        continue
                    try:
                        defs = read_mtl(txt)
                    except (ValueError, IndexError):
                        warnings.append("material file has a broken line, its materials were skipped: %s" % fname)
                        continue
                    mtl_defs.update(defs)
            elif key in ("l", "p"):
                dropped["lines/points"] = dropped.get("lines/points", 0) + 1
            elif key in ("curv", "curv2", "surf", "cstype", "deg", "bmat", "step", "parm", "trim", "hole", "con"):
                dropped["curves/surfaces"] = dropped.get("curves/surfaces", 0) + 1
            # s, vp, lod, etc. ignored on purpose
    except ReadError:
        raise
    except (ValueError, IndexError):
        raise ReadError("The OBJ file has a line it can't read (line %d)." % lineno) from None

    # materials and textures
    textures, tex_index = [], {}

    def tex(fname):
        if not fname:
            return None
        full = os.path.normpath(os.path.join(base_dir, fname.replace("\\", os.sep)))
        if full not in tex_index:
            tex_index[full] = len(textures)
            textures.append(Texture(name=os.path.splitext(os.path.basename(full))[0], mime=mime_of(full),
                                    data=None, path=full))
        return tex_index[full]

    used = []
    for (_, mname) in order:
        if mname is not None and mname not in used:
            used.append(mname)
    materials, mat_index = [], {}
    for mname in used:
        m = mtl_defs.get(mname)
        if m is None:
            warnings.append("material not defined: %s" % mname)
            m = {"Kd": None, "d": 1.0, "Ke": None, "Pr": None, "Pm": None, "Ns": None, "maps": {}}
        kd = m["Kd"] or (1.0, 1.0, 1.0)
        alpha = max(0.0, min(1.0, m["d"]))
        mode = "BLEND" if alpha < 1.0 else ("MASK" if "alpha" in m["maps"] else "OPAQUE")
        mat_index[mname] = len(materials)
        slots = {}
        for slot in ("base", "normal", "emissive"):
            t = tex(m["maps"].get(slot))
            if t is not None:
                slots[slot] = t
        emissive = m["Ke"] or ((1.0, 1.0, 1.0) if "emissive" in slots else (0.0, 0.0, 0.0))
        if m["Pr"] is not None:
            rough = m["Pr"]
        elif m["Ns"] is not None:  # Phong shininess -> roughness, the usual sqrt(2 / (Ns + 2)) rule of thumb
            rough = math.sqrt(2.0 / (max(0.0, m["Ns"]) + 2.0))
        else:
            rough = 1.0
        materials.append(Material(name=mname, base_color=(kd[0], kd[1], kd[2], alpha), textures=slots,
                                  alpha_mode=mode, double_sided=False, emissive=emissive,
                                  metallic=max(0.0, min(1.0, m["Pm"] if m["Pm"] is not None else 0.0)),
                                  roughness=max(0.0, min(1.0, rough))))

    meshes = []
    multi = {}
    for g, _ in order:
        multi[g] = multi.get(g, 0) + 1
    computed = 0
    for key in order:
        g, mname = key
        b = groups[key]
        if not b["tris"]:
            continue
        corners = b["corners"]
        pos = [vs[c[0]] for c in corners]
        has_uv = any(c[1] is not None for c in corners)
        has_n = all(c[2] is not None for c in corners)
        uvs = [[vts[c[1]] if c[1] is not None else (0.0, 0.0) for c in corners]] if has_uv else []
        if has_n:
            normals = [vns[c[2]] for c in corners]
        else:
            normals = smooth_normals(pos, b["tris"])
            computed += 1
        colors = []
        if any(vcs[c[0]] is not None for c in corners):
            colors = [vcs[c[0]] or (1.0, 1.0, 1.0, 1.0) for c in corners]
        mesh_name = g if multi[g] == 1 or mname is None else "%s.%s" % (g, mname)
        meshes.append(Mesh(name=mesh_name, positions=pos, normals=normals, uvs=uvs, colors=colors, joints=[],
                           weights=[], triangles=list(b["tris"]),
                           material=mat_index.get(mname) if mname is not None else None))

    meta = new_meta("obj", source_units="unknown (OBJ has no units), scaled by %g" % scale,
                    source_up=up.upper()[:1] or "Y", share="private")
    scene = Scene(meshes=meshes, skeleton=Skeleton(), materials=materials, textures=textures, meta=meta)
    scene.note("OBJ has no units: read as metres%s." % ("" if scale == 1.0 else " after scaling by %g" % scale))
    if not up.upper().startswith("Y"):
        scene.note("Turned from %s up to Y up." % up.upper()[:1])
    if computed:
        scene.note("%d mesh(es) had no normals; smooth normals were computed." % computed)
    for what, n in dropped.items():
        scene.note("%d %s dropped." % (n, what))
    for w in warnings:
        scene.note(w[0].upper() + w[1:] + ".")
    return scene


def _mtl_names(rest, base_dir, mtl_text):
    """mtllib may list several files, and a single file name may contain spaces."""
    whole = rest.strip()
    if (mtl_text and whole in mtl_text) or os.path.isfile(os.path.join(base_dir, whole)):
        return [whole]
    try:
        return shlex.split(whole, posix=True)
    except ValueError:
        return whole.split()
