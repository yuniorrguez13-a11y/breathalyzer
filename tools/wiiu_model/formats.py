"""Format tables for textures: the neutral (BNTX) format codes, their block sizes, and the GX2 <-> BNTX maps.

Neutral texture format = the BNTX code `(channel layout << 8) | type` (type 1 unorm, 2 snorm, 6 sRGB). GX2 surface
format values are the public GX2 enum values (wut, zlib licence). Only the formats BotW uses are listed (plus R8G8);
anything else is refused, never guessed.
"""
from __future__ import annotations

from .errors import Unsupported

# BNTX channel layout -> (block width, block height, bytes per block)
LAYOUT_BLOCK = {
    0x02: (1, 1, 1),    # R8
    0x09: (1, 1, 2),    # R8G8
    0x0B: (1, 1, 4),    # R8G8B8A8
    0x1A: (4, 4, 8),    # BC1
    0x1B: (4, 4, 16),   # BC2
    0x1C: (4, 4, 16),   # BC3
    0x1D: (4, 4, 8),    # BC4
    0x1E: (4, 4, 16),   # BC5
}
LAYOUT_NAME = {0x02: "R8", 0x09: "R8G8", 0x0B: "RGBA8", 0x1A: "BC1", 0x1B: "BC2", 0x1C: "BC3", 0x1D: "BC4",
               0x1E: "BC5"}
TYPE_NAME = {1: "unorm", 2: "snorm", 6: "srgb"}

# GX2 surface format -> neutral (BNTX) format
GX2_TO_NX = {
    0x001: 0x0201,              # R8 unorm
    0x007: 0x0901,              # R8G8 unorm
    0x01A: 0x0B01, 0x41A: 0x0B06,   # RGBA8 unorm / sRGB
    0x031: 0x1A01, 0x431: 0x1A06,   # BC1
    0x032: 0x1B01, 0x432: 0x1B06,   # BC2
    0x033: 0x1C01, 0x433: 0x1C06,   # BC3
    0x034: 0x1D01, 0x234: 0x1D02,   # BC4 unorm / snorm
    0x035: 0x1E01, 0x235: 0x1E02,   # BC5 unorm / snorm
}
NX_TO_GX2 = {v: k for k, v in GX2_TO_NX.items()}


def format_name(fmt: int) -> str:
    return f"{LAYOUT_NAME.get(fmt >> 8, hex(fmt >> 8))}_{TYPE_NAME.get(fmt & 0xFF, hex(fmt & 0xFF))}"


def block_info(fmt: int) -> tuple[int, int, int]:
    """(block width, block height, bytes per block) of a neutral format; Unsupported if unknown."""
    if fmt not in NX_TO_GX2:
        raise Unsupported(f"texture format {fmt:#06x} is not supported")
    return LAYOUT_BLOCK[fmt >> 8]


def level_dims(fmt: int, width: int, height: int, level: int) -> tuple[int, int, int]:
    """(blocks across, blocks down, bytes per block) of mip `level`."""
    bw, bh, bpb = block_info(fmt)
    w, h = max(1, width >> level), max(1, height >> level)
    return (w + bw - 1) // bw, (h + bh - 1) // bh, bpb


def level_size(fmt: int, width: int, height: int, level: int) -> int:
    """Bytes of a linear (row-major, rows packed) mip level."""
    bx, by, bpb = level_dims(fmt, width, height, level)
    return bx * by * bpb


def gx2_format(nx_fmt: int) -> int:
    try:
        return NX_TO_GX2[nx_fmt]
    except KeyError:
        raise Unsupported(f"texture format {nx_fmt:#06x} has no Wii U equivalent") from None


def nx_format(gx2_fmt: int) -> int:
    try:
        return GX2_TO_NX[gx2_fmt]
    except KeyError:
        raise Unsupported(f"GX2 surface format {gx2_fmt:#05x} is not supported") from None


# Component selectors. BNTX: 0 zero, 1 one, 2 R, 3 G, 4 B, 5 A. GX2: 0 R, 1 G, 2 B, 3 A, 4 zero, 5 one.
_GX2_TO_NX_CH = {0: 2, 1: 3, 2: 4, 3: 5, 4: 0, 5: 1}
_NX_TO_GX2_CH = {v: k for k, v in _GX2_TO_NX_CH.items()}


def channels_from_gx2(comp_map: int) -> tuple[int, int, int, int]:
    sel = ((comp_map >> 24) & 0xFF, (comp_map >> 16) & 0xFF, (comp_map >> 8) & 0xFF, comp_map & 0xFF)
    try:
        return tuple(_GX2_TO_NX_CH[s] for s in sel)
    except KeyError:
        raise Unsupported(f"GX2 component map {comp_map:#010x} is not valid") from None


def channels_to_gx2(ch) -> int:
    try:
        r, g, b, a = (_NX_TO_GX2_CH[c] for c in ch)
    except KeyError:
        raise Unsupported(f"channel selectors {tuple(ch)} are not valid") from None
    return (r << 24) | (g << 16) | (b << 8) | a


# ------------------------------------------------------------------------------------------- models (milestone M2)
# Vertex attribute formats. Neutral = the nn::gfx code `(channel layout << 8) | type` (type 1 unorm, 2 snorm, 3 uint,
# 4 sint, 5 float). GX2AttribFormat = base type | flags (0x100 integer, 0x200 signed, 0x800 float); the base types and
# flags are the public GX2 enum values (wut, zlib licence). The table is built from those two orthogonal parts; only
# combinations whose meaning is clear are listed, anything else is refused, never guessed.

# GX2 base type -> (nn::gfx channel layout, component sizes in bytes, float base type)
GX2_ATTR_BASE = {
    0x00: (0x02, (1,), False),            # 8
    0x02: (0x0A, (2,), False),            # 16
    0x03: (0x0A, (2,), True),             # 16 float
    0x04: (0x09, (1, 1), False),          # 8_8
    0x05: (0x14, (4,), False),            # 32
    0x06: (0x14, (4,), True),             # 32 float
    0x07: (0x12, (2, 2), False),          # 16_16
    0x08: (0x12, (2, 2), True),           # 16_16 float
    0x0A: (0x0B, (1, 1, 1, 1), False),    # 8_8_8_8
    0x0B: (0x0E, (4,), False),            # 10_10_10_2, swapped as one 32-bit word
    0x0C: (0x17, (4, 4), False),          # 32_32
    0x0D: (0x17, (4, 4), True),           # 32_32 float
    0x0E: (0x15, (2, 2, 2, 2), False),    # 16_16_16_16
    0x0F: (0x15, (2, 2, 2, 2), True),     # 16_16_16_16 float
    0x10: (0x18, (4, 4, 4), False),       # 32_32_32
    0x11: (0x18, (4, 4, 4), True),        # 32_32_32 float
    0x12: (0x19, (4, 4, 4, 4), False),    # 32_32_32_32
    0x13: (0x19, (4, 4, 4, 4), True),     # 32_32_32_32 float
}
_LAYOUT_TO_GX2 = {}
for _base, (_lay, _sizes, _flt) in GX2_ATTR_BASE.items():
    _LAYOUT_TO_GX2[_lay, _flt] = _base
ATTR_TYPE_NAME = {1: "unorm", 2: "snorm", 3: "uint", 4: "sint", 5: "float"}
_GX2_ATTR_FLAGS = {0x000: 1, 0x200: 2, 0x100: 3, 0x300: 4}     # unorm, snorm, uint, sint
_NX_ATTR_FLAGS = {v: k for k, v in _GX2_ATTR_FLAGS.items()}
ATTR_LAYOUT_SIZES = {lay: sizes for lay, sizes, _ in GX2_ATTR_BASE.values()}


def attr_format_from_gx2(code: int) -> int:
    """nn::gfx attribute format of a GX2AttribFormat; Unsupported if the code is not one we understand."""
    base, flags = code & 0xFF, code & ~0xFF
    if base not in GX2_ATTR_BASE:
        raise Unsupported(f"GX2 vertex attribute format {code:#05x} is not supported")
    lay, _, is_float = GX2_ATTR_BASE[base]
    if is_float:
        if flags not in (0x000, 0x800):
            raise Unsupported(f"GX2 vertex attribute format {code:#05x} (float with flags {flags:#x})")
        return (lay << 8) | 5
    if flags not in _GX2_ATTR_FLAGS:
        raise Unsupported(f"GX2 vertex attribute format {code:#05x} (flags {flags:#x})")
    return (lay << 8) | _GX2_ATTR_FLAGS[flags]


def attr_format_to_gx2(fmt: int) -> int:
    """GX2AttribFormat for an nn::gfx attribute format (floats with the 0x800 flag, as Nintendo's files have them)."""
    lay, typ = fmt >> 8, fmt & 0xFF
    is_float = typ == 5
    base = _LAYOUT_TO_GX2.get((lay, is_float))
    if base is None or (not is_float and typ not in _NX_ATTR_FLAGS):
        raise Unsupported(f"vertex attribute format {fmt:#06x} has no GX2 equivalent")
    return base | (0x800 if is_float else _NX_ATTR_FLAGS[typ])


def attr_component_sizes(fmt: int) -> tuple:
    """Byte sizes of the components of an nn::gfx attribute format (what a byte swap must reverse)."""
    lay = fmt >> 8
    if lay not in ATTR_LAYOUT_SIZES or (fmt & 0xFF) not in ATTR_TYPE_NAME:
        raise Unsupported(f"vertex attribute format {fmt:#06x} is not supported")
    return ATTR_LAYOUT_SIZES[lay]


def attr_format_name(fmt: int) -> str:
    names = {0x02: "R8", 0x09: "R8G8", 0x0A: "R16", 0x0B: "R8G8B8A8", 0x0E: "R10G10B10A2", 0x12: "R16G16",
             0x14: "R32", 0x15: "R16G16B16A16", 0x17: "R32G32", 0x18: "R32G32B32", 0x19: "R32G32B32A32"}
    return f"{names.get(fmt >> 8, hex(fmt >> 8))}_{ATTR_TYPE_NAME.get(fmt & 0xFF, hex(fmt & 0xFF))}"


# GX2 index type: 0 U16 little-endian, 1 U32 little-endian, 4 U16 big-endian, 9 U32 big-endian (wut).
GX2_INDEX = {0: ("u16", False), 1: ("u32", False), 4: ("u16", True), 9: ("u32", True)}
GX2_INDEX_CODE = {"u16": 4, "u32": 9}
# GX2 primitive mode (wut) <-> neutral name. Fans, quads, rects, loops and adjacency modes are refused.
GX2_PRIMITIVE = {1: "points", 2: "lines", 3: "line_strip", 4: "triangles", 6: "triangle_strip"}
GX2_PRIMITIVE_CODE = {v: k for k, v in GX2_PRIMITIVE.items()}
# User data types: one numbering for both consoles, BfresLibrary's UserDataType (MIT licence), which its Wii U and
# Switch loaders share: 0 int, 1 float, 2 string, 3 wide string, 4 bytes. The user's Switch files only show 0-2, so
# 3 and 4 are unconfirmed on Switch (an older note had Switch 3 = bytes). Hence: Switch bytes are written as 4; Wii U
# wide strings become strings (materials.to_switch warns); a Switch file with type 3 is refused (nx_model).
USER_DATA_KINDS = {0: "int", 1: "float", 2: "string", 3: "wstring", 4: "bytes"}
USER_DATA_CODE = {v: k for k, v in USER_DATA_KINDS.items()}
