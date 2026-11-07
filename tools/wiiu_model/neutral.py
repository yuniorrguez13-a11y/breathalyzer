"""Neutral in-memory model shared by the Switch and Wii U readers/writers (textures part: milestone M1).

Conventions (design section 3): texture levels are linear blocks, row-major, rows packed tightly
(`ceil(w/bw) * bytes_per_block` per row); level L is max(1, w >> L) x max(1, h >> L) pixels. Formats are BNTX codes,
channels BNTX selectors (0 zero, 1 one, 2 R, 3 G, 4 B, 5 A). Platform-only values live in `extra` under `nx.` or
`wiiu.` keys; a writer reads only its own prefix.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, fields, is_dataclass

from .formats import level_size


@dataclass
class Texture:
    name: str
    format: int
    width: int
    height: int
    levels: list[bytes]
    channels: tuple[int, int, int, int] = (2, 3, 4, 5)
    swizzle_hint: int = 0
    dim: str = "2d"
    depth: int = 1
    array: int = 1
    extra: dict = field(default_factory=dict)

    @property
    def mips(self) -> int:
        return len(self.levels)

    def check(self) -> None:
        """Level count and sizes match the format and size (raises ValueError)."""
        if not self.levels:
            raise ValueError(f"texture {self.name!r} has no levels")
        for m, lv in enumerate(self.levels):
            want = level_size(self.format, self.width, self.height, m)
            if len(lv) != want:
                raise ValueError(f"texture {self.name!r} level {m}: {len(lv)} bytes, expected {want}")


@dataclass
class TextureSet:
    name: str                       # 'X' for X.Tex / X.Tex1, or the item icon name
    textures: list[Texture]
    kind: str = "model"             # 'model' (X.Tex) or 'single' (item icon)
    extra: dict = field(default_factory=dict)

    def by_name(self) -> dict[str, Texture]:
        return {t.name: t for t in self.textures}


def _keep(key: str, mode: str, platform: str | None) -> bool:
    if key.startswith(("nx.", "wiiu.")):
        if mode == "cross":
            return False
        return platform is None or key.startswith(platform + ".")
    return True


def describe(obj, mode: str = "cross", platform: str | None = None):
    """Canonical tree of plain values. `cross` leaves out every nx./wiiu. extra; `same` keeps them (or only the ones
    of `platform`). Bytes become (length, sha1) so trees stay small and comparable."""
    if is_dataclass(obj):
        out = {}
        for f in fields(obj):
            v = getattr(obj, f.name)
            if f.name == "extra":
                v = {k: x for k, x in sorted(v.items()) if _keep(k, mode, platform)}
            out[f.name] = describe(v, mode, platform)
        return out
    if isinstance(obj, (bytes, bytearray, memoryview)):
        b = bytes(obj)
        return ("bytes", len(b), hashlib.sha1(b).hexdigest())
    if isinstance(obj, dict):
        return {k: describe(v, mode, platform) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [describe(v, mode, platform) for v in obj]
    return obj


def diff(a, b, mode: str = "cross", limit: int = 10, platform: str | None = None) -> list[str]:
    """First `limit` paths where two neutral objects differ, e.g. `textures[3].levels[2] @0x1a0`."""
    out: list[str] = []

    def walk(x, y, path):
        if len(out) >= limit:
            return
        if is_dataclass(x) and is_dataclass(y) and type(x) is type(y):
            for f in fields(x):
                vx, vy = getattr(x, f.name), getattr(y, f.name)
                if f.name == "extra":
                    vx = {k: v for k, v in vx.items() if _keep(k, mode, platform)}
                    vy = {k: v for k, v in vy.items() if _keep(k, mode, platform)}
                walk(vx, vy, f"{path}.{f.name}" if path else f.name)
            return
        if isinstance(x, (bytes, bytearray, memoryview)) and isinstance(y, (bytes, bytearray, memoryview)):
            bx, by = bytes(x), bytes(y)
            if bx != by:
                n = min(len(bx), len(by))
                first = next((i for i in range(n) if bx[i] != by[i]), n)
                out.append(f"{path} @{first:#x} (sizes {len(bx)}/{len(by)})")
            return
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y), key=str):
                if k not in x or k not in y:
                    out.append(f"{path}[{k!r}] only in {'second' if k not in x else 'first'}")
                else:
                    walk(x[k], y[k], f"{path}[{k!r}]")
            return
        if isinstance(x, (list, tuple)) and isinstance(y, (list, tuple)):
            if len(x) != len(y):
                out.append(f"{path} length {len(x)} != {len(y)}")
            for i, (vx, vy) in enumerate(zip(x, y)):
                walk(vx, vy, f"{path}[{i}]")
            return
        if x != y:
            out.append(f"{path}: {x!r} != {y!r}")

    walk(a, b, "")
    return out[:limit]


# ------------------------------------------------------------------------------------------------ models (milestone M2)
# Conventions (design section 3): numbers inside `bytes` fields (vertex buffers, indices, inverse matrices, param data)
# are little-endian; lists keep file order (dictionary order = array order); offsets, string pools, dictionaries,
# relocation data, run-time areas and padding are not stored (writers make them). f32 values are Python floats (an
# f32 -> float -> f32 trip is exact). Attribute formats are nn::gfx codes `(channel layout << 8) | type`; primitive
# and index formats are strings; sampler states use nn::gfx values (materials.py translates GX2 words).


@dataclass
class UserData:
    name: str
    kind: str                       # 'int', 'float', 'string', 'wstring' (Wii U only), 'bytes'
    values: object                  # tuple of int / float / str, or bytes
    extra: dict = field(default_factory=dict)


@dataclass
class Bone:
    name: str
    parent: int                     # -1 = root
    smooth_index: int               # -1 = none
    rigid_index: int                # -1 = none
    billboard_index: int            # 0xFFFF = none
    flags: int
    scale: tuple
    rotation: tuple                 # 4 floats (Euler XYZ + 1.0, or a quaternion; FSKL flags say which)
    translation: tuple
    user_data: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)


@dataclass
class Skeleton:
    flags: int
    bones: list
    matrix_to_bone: list            # bone index per matrix: smooth matrices first, then rigid
    smooth_count: int
    rigid_count: int
    inverse_matrices: bytes         # 12 f32 (3x4) per smooth matrix
    extra: dict = field(default_factory=dict)


@dataclass
class Attribute:
    name: str
    format: int                     # nn::gfx attribute format
    offset: int                     # byte offset in the vertex
    buffer: int                     # buffer index


@dataclass
class VertexBuffer:
    vertex_count: int
    skin_count: int                 # max influences per vertex
    attributes: list
    strides: list                   # per buffer
    buffers: list                   # bytes per buffer (stride x vertex count, little-endian components)
    extra: dict = field(default_factory=dict)


@dataclass
class Mesh:
    primitive: str                  # 'points', 'lines', 'line_strip', 'triangles', 'triangle_strip'
    index_format: str               # 'u8', 'u16', 'u32'
    index_count: int
    indices: bytes                  # little-endian
    first_vertex: int
    submeshes: list                 # [(byte offset, index count)]
    extra: dict = field(default_factory=dict)


@dataclass
class Shape:
    name: str
    flags: int
    material: int
    bone: int
    vertex_buffer: int
    skin_bones: list
    skin_count: int
    target_attrib_count: int
    meshes: list
    bounds: list                    # one (cx, cy, cz, ex, ey, ez) per mesh and per submesh, file order
    radii: list                     # one per mesh
    key_shapes: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)


@dataclass
class RenderInfo:
    name: str
    kind: str                       # 'int', 'float', 'string'
    values: tuple


@dataclass
class ShaderAssign:
    archive: str
    model: str
    revision: int
    attribs: list                   # [(shader attribute, model attribute)]
    samplers: list                  # [(shader sampler, material sampler)]
    options: list                   # [(option, value)]
    extra: dict = field(default_factory=dict)


@dataclass
class SamplerState:
    """nn::gfx sampler values. wrap: 0 repeat, 1 mirror, 2 clamp to edge, 3 clamp to border, 4 mirror clamp to edge.
    filter: mip bits 0-1, mag bits 2-3, min bits 4-5 (1 point, 2 linear). compare: comparison function. border:
    0 white, 1 transparent black, 2 opaque black."""
    wrap_u: int
    wrap_v: int
    wrap_w: int
    compare: int
    border: int
    max_aniso: int
    filter: int
    min_lod: float
    max_lod: float
    lod_bias: float
    extra: dict = field(default_factory=dict)


@dataclass
class Sampler:
    name: str
    state: SamplerState


@dataclass
class Param:
    name: str
    type: int                       # same numbering on both consoles (8 uint, 12-15 float1-4, 30 TexSrt ...)
    offset: int
    size: int
    depended: int
    depend: int
    extra: dict = field(default_factory=dict)


@dataclass
class Material:
    name: str
    flags: int
    render_infos: list
    shader: ShaderAssign
    samplers: list
    textures: list                  # texture names; texture i goes with sampler i
    params: list
    param_data: bytes               # little-endian (u32 words)
    volatile: list                  # indices of volatile params
    user_data: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # 'wiiu.render_state': RenderState of a Wii U file


@dataclass
class RenderState:
    """Wii U FMAT RenderState: flags (mode bits 0-1, blend mode bits 4-5) and raw GX2 registers."""
    flags: int
    polygon_control: int
    depth_control: int
    alpha_control: int
    alpha_ref: float
    color_control: int
    blend_target: int
    blend_control: int
    blend_color: tuple


@dataclass
class Model:
    name: str
    path: str
    skeleton: Skeleton
    vertex_buffers: list
    shapes: list
    materials: list
    user_data: list
    total_vertex_count: int
    extra: dict = field(default_factory=dict)


@dataclass
class ExtFile:
    name: str
    data: bytes


@dataclass
class ResFile:
    name: str
    models: list
    skel_anims: list = field(default_factory=list)   # Switch FSKA (SkelAnim); not converted to Wii U
    mat_anims: list = field(default_factory=list)    # Switch FMAA (MatAnim)
    external: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)


# Switch animations (FSKA / FMAA), kept as the Switch structures: `head` fields are the structure's little-endian
# non-pointer bytes (flags, counts, frame numbers), curves keep their frame and key arrays as bytes. Wii U
# animations are not converted (design section 5.4); these exist for the Switch round trip and for copying vanilla
# animations. The writer sets the counts in the heads from the lists.

@dataclass
class AnimCurve:
    head: bytes                     # curve bytes 0x10..0x30: u16 flags, u16 key count, u32 target, 5 f32, pad
    frames: bytes
    keys: bytes


@dataclass
class BoneAnim:
    name: str
    head: bytes                     # bytes 0x18..0x28: u32 flags, begin rotate/translate, curve count, ...
    base: object                    # base data bytes, or None
    curves: list


@dataclass
class SkelAnim:
    name: str
    path: str
    head: bytes                     # FSKA bytes 0x48..0x60: flags, frames, curve count, baked size, counts
    bind_indices: list
    bone_anims: list
    user_data: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)


@dataclass
class MatAnimData:
    name: str
    head: bytes                     # bytes 0x28..0x40: first curve indices, counts
    param_infos: list               # [(param name, bytes 0x8..0x18)]
    pattern_infos: list             # [(sampler name, bytes 0x8..0x10)]
    curves: list
    constants: bytes                # 8 bytes each


@dataclass
class MatAnim:
    name: str
    path: str
    head: bytes                     # FMAA bytes 0x60..0x78: flags, counts, frames, baked size
    bind_indices: list
    materials: list
    textures: list                  # texture names (texture pattern animations)
    user_data: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)
