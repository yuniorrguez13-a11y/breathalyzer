"""Tegra X1 block-linear texture layout (Switch BNTX), written from the layout notes in out/model-work/switch-format.md
section 7.3 (checked on 372/372 textures of the user's own files: every mip offset and image size reproduced).

A GOB is 64 bytes x 8 rows (512 bytes). A layout block is 1 GOB wide and `bh` GOBs tall. Within a GOB, 16-byte runs
are contiguous. The address of byte column x, block row y splits into an x part and a y part:
    addr = fx(x) + gy(y)
    fx(x) = (x // 64) * 512*bh + ((x % 64) // 32) * 256 + ((x % 32) // 16) * 32 + x % 16
    gy(y) = (y // (8*bh)) * 512*bh*wg + ((y % (8*bh)) // 8) * 512 + ((y % 8) // 2) * 64 + (y % 2) * 16
so the index list is one add per unit (see perm.py).
"""
from __future__ import annotations

from functools import lru_cache
from array import array

from . import perm
from .errors import Damaged
from .formats import level_dims

GOB_BYTES = 512
MAX_BH_LOG2 = 5          # nn::gfx allows block heights 1..32 GOBs; BotW uses 1..16


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def align(n: int, a: int) -> int:
    return -(-n // a) * a


def game_block_height_log2(rows: int) -> int:
    """Block height (log2, in GOBs) BotW's converter picks for a level-0 height of `rows` block rows: the next power
    of two of ceil(rows / 8), at most 16 (370/372 textures in the sample; 2 use one step less)."""
    g = 1
    while g < 16 and g * 8 < rows:
        g *= 2
    return g.bit_length() - 1


def level_block_height(bh: int, rows: int) -> int:
    """Block height of a mip level: the texture's, halved while the level is at most half a block tall."""
    while bh > 1 and rows <= bh * 8 // 2:
        bh //= 2
    return bh


def level_size(width_bytes: int, rows: int, bh: int) -> int:
    return ceil_div(width_bytes, 64) * ceil_div(rows, 8 * bh) * bh * GOB_BYTES


def mip_layout(fmt: int, width: int, height: int, mips: int, bh_log2: int) -> tuple[list[tuple[int, int, int]], int]:
    """[(offset, size, block height)] per mip and the image size (all mips, aligned to 0x200)."""
    if not 0 <= bh_log2 <= MAX_BH_LOG2:
        raise Damaged(f"block height 2^{bh_log2} GOBs is out of range")
    out, off = [], 0
    for m in range(mips):
        bx, by, bpb = level_dims(fmt, width, height, m)
        bh = level_block_height(1 << bh_log2, by)
        size = level_size(bx * bpb, by, bh)
        off = align(off, GOB_BYTES * bh)
        out.append((off, size, bh))
        off += size
    return out, align(off, 0x200)


def ref_addr(x: int, y: int, width_bytes: int, bh: int) -> int:
    """Reference (slow) address of byte column x, block row y."""
    wg = ceil_div(width_bytes, 64)
    return ((y // (8 * bh)) * GOB_BYTES * bh * wg + (x // 64) * GOB_BYTES * bh + ((y % (8 * bh)) // 8) * GOB_BYTES
            + ((x % 64) // 32) * 256 + ((y % 8) // 2) * 64 + ((x % 32) // 16) * 32 + (y % 2) * 16 + (x % 16))


def _unit(width_bytes: int) -> int:
    for u in (8, 4, 2):
        if width_bytes % u == 0:
            return u
    return 1


@lru_cache(maxsize=48)
def _index(width_bytes: int, rows: int, bh: int, unit: int) -> array:
    wg = ceil_div(width_bytes, 64)
    gb = GOB_BYTES * bh
    fx = [((x >> 6) * gb + ((x >> 5) & 1) * 256 + ((x >> 4) & 1) * 32 + (x & 15)) // unit
          for x in range(0, width_bytes, unit)]
    gy = [((y // (8 * bh)) * gb * wg + ((y % (8 * bh)) >> 3) * GOB_BYTES + ((y & 7) >> 1) * 64 + (y & 1) * 16) // unit
          for y in range(rows)]
    return array("I", [a + b for b in gy for a in fx])


def untile(data, width_bytes: int, rows: int, bh: int) -> bytes:
    """Linear rows (width_bytes each, packed) from one block-linear level."""
    u = _unit(width_bytes)
    return perm.gather(data, _index(width_bytes, rows, bh, u), u, 1, "block-linear level")


def tile(linear, width_bytes: int, rows: int, bh: int) -> bytes:
    """One block-linear level (zero padding) from linear rows."""
    if len(linear) != width_bytes * rows:
        raise ValueError(f"linear level is {len(linear)} bytes, expected {width_bytes * rows}")
    u = _unit(width_bytes)
    return perm.scatter(linear, _index(width_bytes, rows, bh, u), u, 1, level_size(width_bytes, rows, bh),
                        "block-linear level")


def untile_ref(data, width_bytes: int, rows: int, bh: int) -> bytes:
    return b"".join(bytes(data[ref_addr(x, y, width_bytes, bh)] for x in range(width_bytes)) for y in range(rows))


def tile_ref(linear, width_bytes: int, rows: int, bh: int) -> bytes:
    out = bytearray(level_size(width_bytes, rows, bh))
    for y in range(rows):
        for x in range(width_bytes):
            out[ref_addr(x, y, width_bytes, bh)] = linear[y * width_bytes + x]
    return bytes(out)


def untile_texture(data, fmt: int, width: int, height: int, mips: int, bh_log2: int,
                   offsets: list[int] | None = None) -> list[bytes]:
    """Every mip level of a block-linear texture as linear blocks. `offsets` (relative to the texture data) default to
    the computed layout."""
    lay, _ = mip_layout(fmt, width, height, mips, bh_log2)
    out = []
    for m, (off, size, bh) in enumerate(lay):
        if offsets is not None:
            off = offsets[m]
        bx, by, bpb = level_dims(fmt, width, height, m)
        if off + size > len(data):
            raise Damaged(f"mip {m} ({size} bytes at {off:#x}) runs past the texture data ({len(data):#x})")
        out.append(untile(memoryview(data)[off:off + size], bx * bpb, by, bh))
    return out


def tile_texture(levels: list[bytes], fmt: int, width: int, height: int, bh_log2: int) -> bytes:
    """The block-linear image (all mips, padded to the image size) from linear levels."""
    lay, image_size = mip_layout(fmt, width, height, len(levels), bh_log2)
    out = bytearray(image_size)
    for m, ((off, size, bh), lin) in enumerate(zip(lay, levels)):
        bx, by, bpb = level_dims(fmt, width, height, m)
        out[off:off + size] = tile(lin, bx * bpb, by, bh)
    return bytes(out)
