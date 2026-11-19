"""The neutral Scene every model reader returns (glTF-shaped). Pure standard library.

Contract (all readers, tools/model_in/<format>.py, expose ``read(path, **opts) -> Scene``):

- **Space:** Y up, metres, right-handed. Readers convert (meta records what the source used).
- **Mesh** = one material's worth of triangles: ``positions`` [(x,y,z)], ``normals`` [(x,y,z)] (same length or empty),
  ``uvs`` = list of UV sets, each [(u,v)] (glTF convention: v=0 is the TOP of the image), ``colors`` [(r,g,b,a)]
  0..1 (or empty), ``joints`` [(i0,i1,i2,i3)] = indices into ``scene.skeleton.bones`` (not a per-skin list),
  ``weights`` [(w0..w3)] summing 1 (top 4 kept, renormalised; unused slots are joint 0 weight 0), ``triangles``
  [(a,b,c)] counter-clockwise front faces, 32-bit indices (no limit), ``material`` = index into
  ``scene.materials`` or None.
- **Skeleton:** ``bones`` [Bone], parents always come before children (``parent < index``, -1 = root). Each bone
  has its rest pose as local TRS (rotation is a unit quaternion x,y,z,w) and an inverse bind matrix (16 floats,
  column-major like glTF). **bind = rest**: readers move skinned vertices into the rest pose, so
  ``inverse_bind == inverse(world rest)``.
- **Material:** ``base_color`` linear RGBA, ``textures`` {slot: texture index} for slots in SLOTS,
  ``texcoords`` {slot: uv set}, ``alpha_mode`` OPAQUE/MASK/BLEND, ``double_sided``; toon sources set ``shader``
  ("mtoon", "mmd") and ``shade_color``.
- **Texture:** ``name``, ``mime``, and ``data`` (bytes) or ``path`` (a file next to the model; ``read_bytes()``).
- **meta** (dict): ``source_format``, ``units`` ("m"), ``up`` ("Y"), ``forward`` (where the model faces, "+Z" or
  "-Z"), ``licence`` (dict or None, when the format carries one), ``notes`` (plain sentences about what was
  dropped or changed, e.g. "4 morphs kept, not applied"), and format extras (``humanoid`` {vrm bone: bone index},
  ``morphs`` ...).

``validate()`` lists every problem as a short sentence (empty list = fine): index ranges, weights summing ~1,
finite numbers, parent order, unit quaternions.

Matrix helpers below are shared by every reader (column-major 4x4 as 16-tuples, like glTF).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

SLOTS = ("base", "normal", "emissive", "metallic_roughness", "occlusion", "shade")
ALPHA_MODES = ("OPAQUE", "MASK", "BLEND")
IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
WEIGHT_TOLERANCE = 1e-3


class ReadError(ValueError):
    """A model we can't read, said in one plain sentence (shown to the player as is)."""


@dataclass
class Bone:
    name: str
    parent: int = -1
    translation: tuple = (0.0, 0.0, 0.0)
    rotation: tuple = (0.0, 0.0, 0.0, 1.0)  # x, y, z, w
    scale: tuple = (1.0, 1.0, 1.0)
    inverse_bind: tuple = IDENTITY  # column-major 4x4
    extras: dict = field(default_factory=dict)

    def local_matrix(self) -> tuple:
        return mat_from_trs(self.translation, self.rotation, self.scale)


@dataclass
class Skeleton:
    bones: list = field(default_factory=list)

    def index(self, name: str) -> int:
        for i, b in enumerate(self.bones):
            if b.name == name:
                return i
        return -1

    def world_matrices(self) -> list:
        """Rest-pose world matrix of every bone (parents come first, so one pass)."""
        out = []
        for b in self.bones:
            m = b.local_matrix()
            out.append(mat_mul(out[b.parent], m) if 0 <= b.parent < len(out) else m)
        return out

    def children(self, i: int) -> list:
        return [k for k, b in enumerate(self.bones) if b.parent == i]


@dataclass
class Mesh:
    name: str = ""
    positions: list = field(default_factory=list)
    normals: list = field(default_factory=list)
    uvs: list = field(default_factory=list)  # list of UV sets
    colors: list = field(default_factory=list)
    joints: list = field(default_factory=list)
    weights: list = field(default_factory=list)
    triangles: list = field(default_factory=list)
    material: int | None = None
    extras: dict = field(default_factory=dict)

    @property
    def skinned(self) -> bool:
        return bool(self.joints)


@dataclass
class Material:
    name: str = ""
    base_color: tuple = (1.0, 1.0, 1.0, 1.0)  # linear RGBA
    textures: dict = field(default_factory=dict)  # slot -> texture index
    texcoords: dict = field(default_factory=dict)  # slot -> uv set index (default 0)
    alpha_mode: str = "OPAQUE"
    alpha_cutoff: float = 0.5
    double_sided: bool = False
    emissive: tuple = (0.0, 0.0, 0.0)
    metallic: float = 1.0
    roughness: float = 1.0
    shader: str = "pbr"  # "pbr", "unlit", "mtoon", "mmd", ...
    shade_color: tuple | None = None  # toon shade colour, linear RGB
    extras: dict = field(default_factory=dict)


@dataclass
class Texture:
    name: str = ""
    mime: str = ""
    data: bytes | None = None
    path: str | None = None
    extras: dict = field(default_factory=dict)

    def read_bytes(self) -> bytes:
        if self.data is not None:
            return self.data
        if self.path:
            return read_file(self.path, "picture")
        raise ReadError(f"Texture {self.name!r} has no picture.")


def read_file(path, what: str = "model") -> bytes:
    """The file's bytes; a missing, locked or folder path is a ReadError in one sentence (shared by every reader)."""
    try:
        return Path(path).read_bytes()
    except OSError as e:
        why = "it isn't there" if isinstance(e, FileNotFoundError) else (
            "it's a folder" if isinstance(e, (IsADirectoryError, PermissionError)) and Path(path).is_dir()
            else (e.strerror or e.__class__.__name__).lower())
        raise ReadError(f"Can't open the {what} file {Path(path).name} ({why}).") from None


def new_meta(source_format: str, **more) -> dict:
    meta = {"source_format": source_format, "units": "m", "up": "Y", "forward": "+Z", "licence": None, "notes": [],
            "share": "private"}  # every model starts private; only the player (or a licence they confirm) changes it
    meta.update(more)
    return meta


@dataclass
class Scene:
    meshes: list = field(default_factory=list)
    skeleton: Skeleton = field(default_factory=Skeleton)
    materials: list = field(default_factory=list)
    textures: list = field(default_factory=list)
    meta: dict = field(default_factory=lambda: new_meta("unknown"))

    def note(self, text: str) -> None:
        notes = self.meta.setdefault("notes", [])
        if text not in notes:
            notes.append(text)

    def validate(self) -> list:
        return validate(self)

    def check(self) -> "Scene":
        problems = validate(self)
        if problems:
            more = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
            raise ReadError(problems[0] + more)
        return self

    def summary(self) -> dict:
        """Counts only (safe to print: no names, no licence text)."""
        return {
            "format": self.meta.get("source_format"),
            "meshes": len(self.meshes),
            "vertices": sum(len(m.positions) for m in self.meshes),
            "triangles": sum(len(m.triangles) for m in self.meshes),
            "skinned_meshes": sum(1 for m in self.meshes if m.joints),
            "bones": len(self.skeleton.bones),
            "materials": len(self.materials),
            "textures": len(self.textures),
            "humanoid_bones": len(self.meta.get("humanoid") or {}),
            "has_licence": bool(self.meta.get("licence")),
            "notes": len(self.meta.get("notes") or []),
        }


# ----------------------------------------------------------------------------------------------- validation

def _finite(values) -> bool:
    try:
        return all(math.isfinite(v) for v in values)
    except TypeError:
        return False


def _check_list(problems, where, items, width, n=None, label=""):
    if n is not None and items and len(items) != n:
        problems.append(f"{where}: {len(items)} {label} for {n} vertices.")
        return False
    for k, it in enumerate(items):
        if len(it) != width or not _finite(it):
            problems.append(f"{where}: {label} {k} is not {width} finite numbers.")
            return False
    return True


def validate(scene: Scene) -> list:
    problems: list = []
    nb = len(scene.skeleton.bones)
    nm = len(scene.materials)
    nt = len(scene.textures)

    for i, b in enumerate(scene.skeleton.bones):
        w = f"Bone {i} ({b.name})"
        if not (b.parent == -1 or 0 <= b.parent < i):
            problems.append(f"{w}: its parent {b.parent} doesn't come before it.")
        if len(b.translation) != 3 or not _finite(b.translation):
            problems.append(f"{w}: bad translation.")
        if len(b.scale) != 3 or not _finite(b.scale):
            problems.append(f"{w}: bad scale.")
        if len(b.rotation) != 4 or not _finite(b.rotation):
            problems.append(f"{w}: bad rotation.")
        elif abs(math.sqrt(sum(c * c for c in b.rotation)) - 1.0) > 1e-3:
            problems.append(f"{w}: its rotation isn't a unit quaternion.")
        if len(b.inverse_bind) != 16 or not _finite(b.inverse_bind):
            problems.append(f"{w}: bad inverse bind matrix.")

    for mi, m in enumerate(scene.meshes):
        w = f"Mesh {mi} ({m.name})"
        n = len(m.positions)
        _check_list(problems, w, m.positions, 3, None, "position")
        _check_list(problems, w, m.normals, 3, n, "normals" if len(m.normals) != n else "normal")
        for s, uv in enumerate(m.uvs):
            _check_list(problems, f"{w} UV set {s}", uv, 2, n, "UVs" if len(uv) != n else "UV")
        _check_list(problems, w, m.colors, 4, n, "colours" if len(m.colors) != n else "colour")
        if bool(m.joints) != bool(m.weights):
            problems.append(f"{w}: has joints without weights or weights without joints.")
        if m.joints:
            if len(m.joints) != n or len(m.weights) != n:
                problems.append(f"{w}: {len(m.joints)} joints / {len(m.weights)} weights for {n} vertices.")
            elif not nb:
                problems.append(f"{w}: is skinned but the scene has no bones.")
            else:
                for k, (js, ws) in enumerate(zip(m.joints, m.weights)):
                    if len(js) != 4 or len(ws) != 4:
                        problems.append(f"{w}: vertex {k} doesn't have 4 joints and 4 weights.")
                        break
                    if any((not isinstance(j, int)) or j < 0 or j >= nb for j in js):
                        problems.append(f"{w}: vertex {k} uses a bone outside 0..{nb - 1}.")
                        break
                    if not _finite(ws) or any(x < 0 for x in ws):
                        problems.append(f"{w}: vertex {k} has a negative or broken weight.")
                        break
                    if abs(sum(ws) - 1.0) > WEIGHT_TOLERANCE:
                        problems.append(f"{w}: vertex {k} weights sum to {sum(ws):.4f}, not 1.")
                        break
        for k, t in enumerate(m.triangles):
            if len(t) != 3 or any((not isinstance(v, int)) or v < 0 or v >= n for v in t):
                problems.append(f"{w}: triangle {k} points outside its {n} vertices.")
                break
        if m.material is not None and not (isinstance(m.material, int) and 0 <= m.material < nm):
            problems.append(f"{w}: material {m.material} doesn't exist.")

    for i, mat in enumerate(scene.materials):
        w = f"Material {i} ({mat.name})"
        if len(mat.base_color) != 4 or not _finite(mat.base_color):
            problems.append(f"{w}: bad base colour.")
        if mat.alpha_mode not in ALPHA_MODES:
            problems.append(f"{w}: unknown alpha mode {mat.alpha_mode!r}.")
        if mat.shade_color is not None and (len(mat.shade_color) != 3 or not _finite(mat.shade_color)):
            problems.append(f"{w}: bad shade colour.")
        for slot, t in mat.textures.items():
            if slot not in SLOTS:
                problems.append(f"{w}: unknown texture slot {slot!r}.")
            if not (isinstance(t, int) and 0 <= t < nt):
                problems.append(f"{w}: {slot} texture {t} doesn't exist.")

    for i, t in enumerate(scene.textures):
        if t.data is None and not t.path:
            problems.append(f"Texture {i} ({t.name}) has no picture.")

    for name, b in (scene.meta.get("humanoid") or {}).items():
        if not (isinstance(b, int) and 0 <= b < nb):
            problems.append(f"Humanoid bone {name} points at bone {b}, which doesn't exist.")
    return problems


def check_finite(scene: Scene) -> Scene:
    """ReadError if any vertex or bone number is NaN or infinite (a broken file); load() runs this on every model."""
    for mi, m in enumerate(scene.meshes):
        for label, items in (("positions", m.positions), ("normals", m.normals), ("colours", m.colors),
                             ("weights", m.weights), *((f"UVs", uv) for uv in m.uvs)):
            for it in items:
                if not _finite(it):
                    raise ReadError(f"The model has broken numbers (not a number or infinite) in the {label} "
                                    f"of part {mi} ({m.name}).")
    for b in scene.skeleton.bones:
        if not (_finite(b.translation) and _finite(b.rotation) and _finite(b.scale) and _finite(b.inverse_bind)):
            raise ReadError(f"The model has broken numbers (not a number or infinite) in bone {b.name!r}.")
    return scene


# ----------------------------------------------------------------------------------------------- skin helpers

def top4(pairs) -> tuple:
    """[(joint, weight), ...] -> ((j0..j3), (w0..w3)): the 4 heaviest, renormalised; None if all weights are 0."""
    pairs = sorted(((w, j) for j, w in pairs if w > 0.0), key=lambda p: -p[0])[:4]
    total = sum(w for w, _ in pairs)
    if total <= 0.0:
        return None
    pairs += [(0.0, 0)] * (4 - len(pairs))
    return tuple(j for _, j in pairs), tuple(w / total for w, _ in pairs)


def skin_to_rest(positions, normals, joints, weights, bone_mats, deltas=()):
    """Move skinned vertices by their blended bone matrices (bone_mats[j] = rest world_j * inverse_bind_j).

    Returns (positions, normals, [deltas...]); normals and morph deltas turn with the blended linear part."""
    out_p, out_n = [], []
    out_d = [[] for _ in deltas]
    for k, p in enumerate(positions):
        js, ws = joints[k], weights[k]
        m = [0.0] * 16
        for j, wt in zip(js, ws):
            if wt:
                bm = bone_mats[j]
                for c in range(16):
                    m[c] += wt * bm[c]
        out_p.append(mat_point(m, p))
        if normals:
            out_n.append(normalize(mat_dir(m, normals[k])))
        for d, src in zip(out_d, deltas):
            d.append(mat_dir(m, src[k]) if src[k] else src[k])
    return out_p, out_n, out_d


def smooth_normals(positions, triangles) -> list:
    """Area-weighted vertex normals, for models that come without any."""
    acc = [[0.0, 0.0, 0.0] for _ in positions]
    for a, b, c in triangles:
        pa, pb, pc = positions[a], positions[b], positions[c]
        u = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        v = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        n = cross(u, v)
        for i in (a, b, c):
            acc[i][0] += n[0]
            acc[i][1] += n[1]
            acc[i][2] += n[2]
    return [normalize(n, (0.0, 1.0, 0.0)) for n in acc]


# ----------------------------------------------------------------------------------------------- math

def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def normalize(v, fallback=(0.0, 0.0, 0.0)):
    n = math.sqrt(sum(c * c for c in v))
    if n <= 1e-20 or not math.isfinite(n):
        return tuple(fallback)
    return tuple(c / n for c in v)


def mat_mul(a, b) -> tuple:
    """a * b, both column-major 4x4."""
    out = [0.0] * 16
    for col in range(4):
        b0, b1, b2, b3 = b[col * 4], b[col * 4 + 1], b[col * 4 + 2], b[col * 4 + 3]
        for row in range(4):
            out[col * 4 + row] = a[row] * b0 + a[4 + row] * b1 + a[8 + row] * b2 + a[12 + row] * b3
    return tuple(out)


def mat_point(m, p) -> tuple:
    x, y, z = p
    return (m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14])


def mat_dir(m, v) -> tuple:
    x, y, z = v
    return (m[0] * x + m[4] * y + m[8] * z,
            m[1] * x + m[5] * y + m[9] * z,
            m[2] * x + m[6] * y + m[10] * z)


def mat_det3(m) -> float:
    return (m[0] * (m[5] * m[10] - m[9] * m[6]) - m[4] * (m[1] * m[10] - m[9] * m[2])
            + m[8] * (m[1] * m[6] - m[5] * m[2]))


def mat_inverse(m) -> tuple:
    """Inverse of an affine column-major 4x4 (last row 0 0 0 1)."""
    det = mat_det3(m)
    if abs(det) < 1e-30 or not math.isfinite(det):
        raise ReadError("A transform in the model squashes everything flat (it has no inverse).")
    a, b, c = m[0], m[4], m[8]
    d, e, f = m[1], m[5], m[9]
    g, h, i = m[2], m[6], m[10]
    inv = [(e * i - f * h) / det, -(b * i - c * h) / det, (b * f - c * e) / det,
           -(d * i - f * g) / det, (a * i - c * g) / det, -(a * f - c * d) / det,
           (d * h - e * g) / det, -(a * h - b * g) / det, (a * e - b * d) / det]  # row-major 3x3
    tx, ty, tz = m[12], m[13], m[14]
    rx = -(inv[0] * tx + inv[1] * ty + inv[2] * tz)
    ry = -(inv[3] * tx + inv[4] * ty + inv[5] * tz)
    rz = -(inv[6] * tx + inv[7] * ty + inv[8] * tz)
    return (inv[0], inv[3], inv[6], 0.0, inv[1], inv[4], inv[7], 0.0, inv[2], inv[5], inv[8], 0.0, rx, ry, rz, 1.0)


def normal_matrix(m) -> tuple:
    """Inverse-transpose of the linear part, as a column-major 4x4 (for normals)."""
    inv = mat_inverse(m)
    return (inv[0], inv[4], inv[8], 0.0, inv[1], inv[5], inv[9], 0.0, inv[2], inv[6], inv[10], 0.0,
            0.0, 0.0, 0.0, 1.0)


def mat_from_trs(t, r, s) -> tuple:
    x, y, z, w = r
    sx, sy, sz = s
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz, wx, wy, wz = x * y, x * z, y * z, w * x, w * y, w * z
    return ((1 - 2 * (yy + zz)) * sx, (2 * (xy + wz)) * sx, (2 * (xz - wy)) * sx, 0.0,
            (2 * (xy - wz)) * sy, (1 - 2 * (xx + zz)) * sy, (2 * (yz + wx)) * sy, 0.0,
            (2 * (xz + wy)) * sz, (2 * (yz - wx)) * sz, (1 - 2 * (xx + yy)) * sz, 0.0,
            float(t[0]), float(t[1]), float(t[2]), 1.0)


def quat_from_rot(m) -> tuple:
    """Unit quaternion (x,y,z,w) from the (orthonormal) linear part of a column-major 4x4."""
    m00, m10, m20 = m[0], m[1], m[2]
    m01, m11, m21 = m[4], m[5], m[6]
    m02, m12, m22 = m[8], m[9], m[10]
    tr = m00 + m11 + m22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = ((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        q = (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        q = ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2
        q = ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)
    q = normalize(q, (0.0, 0.0, 0.0, 1.0))
    return q if q[3] >= 0 else tuple(-c for c in q)


def trs_from_mat(m) -> tuple:
    """Split an affine column-major 4x4 into (translation, rotation quaternion, scale). A mirror goes in scale x."""
    t = (m[12], m[13], m[14])
    sx = math.sqrt(m[0] ** 2 + m[1] ** 2 + m[2] ** 2)
    sy = math.sqrt(m[4] ** 2 + m[5] ** 2 + m[6] ** 2)
    sz = math.sqrt(m[8] ** 2 + m[9] ** 2 + m[10] ** 2)
    if mat_det3(m) < 0:
        sx = -sx
    if min(abs(sx), abs(sy), abs(sz)) < 1e-30:
        return t, (0.0, 0.0, 0.0, 1.0), (sx, sy, sz)
    rot = (m[0] / sx, m[1] / sx, m[2] / sx, 0.0, m[4] / sy, m[5] / sy, m[6] / sy, 0.0,
           m[8] / sz, m[9] / sz, m[10] / sz, 0.0, 0.0, 0.0, 0.0, 1.0)
    return t, quat_from_rot(rot), (sx, sy, sz)


def mat_close(a, b, tol=1e-5) -> bool:
    return all(abs(x - y) <= tol * max(1.0, abs(x), abs(y)) for x, y in zip(a, b))
