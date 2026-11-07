"""Cheap texture coherence checks (no full BC decode): one brightness number per block from the BC endpoint colours
(Microsoft's public BC1-BC5 format documentation), and the correlation of mip L with a 2x2 average of mip L-1. A
right untiling gives a high correlation; a wrong one (wrong swizzle, wrong tiling) gives a low one."""
from __future__ import annotations

import struct
import sys

from .formats import block_info, level_dims


def _rgb565(c: int) -> float:
    return ((c >> 11) & 31) * (255 / 31) + ((c >> 5) & 63) * (255 / 63) + (c & 31) * (255 / 31)


def block_values(level: bytes, fmt: int) -> list[float]:
    """One value per block (or pixel for uncompressed formats), row-major."""
    layout = fmt >> 8
    _, _, bpb = block_info(fmt)
    n = len(level) // bpb
    if layout == 0x1A:      # BC1: two RGB565 endpoints
        return [(_rgb565(a) + _rgb565(b)) / 6 for a, b, _ in struct.iter_unpack("<HHI", level)]
    if layout in (0x1B, 0x1C):  # BC2/BC3: colour block in the second half
        return [(_rgb565(a) + _rgb565(b)) / 6 for _, a, b, _ in struct.iter_unpack("<QHHI", level)]
    if layout in (0x1D, 0x1E):  # BC4/BC5: first channel's two endpoints
        return [(level[i * bpb] + level[i * bpb + 1]) / 2 for i in range(n)]
    return [float(level[i * bpb]) for i in range(n)]


def correlation(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return float("nan")
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if not va or not vb:
        return float("nan")
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb) ** 0.5


_K5, _K6 = 255 / 31, 255 / 63


def values_at(level: bytes, fmt: int, idx: list[int]) -> list[float]:
    """block_values (up to a constant factor) of the blocks `idx` only: the cost follows len(idx), not the level."""
    layout = fmt >> 8
    _, _, bpb = block_info(fmt)
    if layout in (0x1A, 0x1B, 0x1C):
        base = 0 if layout == 0x1A else 8
        if sys.byteorder == "little" and len(level) % 2 == 0:
            h = memoryview(level).cast("H")
            step, base = bpb // 2, base // 2
            pairs = ((h[i * step + base], h[i * step + base + 1]) for i in idx)
        else:
            pairs = (struct.unpack_from("<HH", level, i * bpb + base) for i in idx)
        return [((a >> 11) + (b >> 11) + (a & 31) + (b & 31)) * _K5 + (((a >> 5) & 63) + ((b >> 5) & 63)) * _K6
                for a, b in pairs]
    if layout in (0x1D, 0x1E):
        return [level[i * bpb] + level[i * bpb + 1] for i in idx]
    return [level[i * bpb] for i in idx]


def mip_coherence(level0: bytes, level1: bytes, fmt: int, width: int, height: int, level: int = 1,
                  max_side: int = 16) -> float:
    """Correlation of `level1` (mip `level`) with the 2x2 block average of `level0` (mip `level`-1); NaN when level
    `level` has fewer than 2x2 blocks to compare or a level is flat. At most `max_side` x `max_side` evenly spread
    blocks are compared, so the cost does not grow with the texture size."""
    bx0, by0, _ = level_dims(fmt, width, height, level - 1)
    bx1, by1, _ = level_dims(fmt, width, height, level)
    w, h = min(bx1, bx0 // 2), min(by1, by0 // 2)
    if w < 2 or h < 2:
        return float("nan")
    xs = range(0, w, -(-w // max_side))
    ys = range(0, h, -(-h // max_side))
    up = values_at(level1, fmt, [y * bx1 + x for y in ys for x in xs])
    idx0 = [r + 2 * x + d for y in ys for r in (2 * y * bx0, (2 * y + 1) * bx0) for x in xs for d in (0, 1)]
    v0 = values_at(level0, fmt, idx0)
    n = len(xs)
    down = []
    for j in range(len(ys)):
        top = v0[4 * n * j:4 * n * j + 2 * n]
        bot = v0[4 * n * j + 2 * n:4 * n * (j + 1)]
        down += [top[2 * i] + top[2 * i + 1] + bot[2 * i] + bot[2 * i + 1] for i in range(n)]
    return correlation(down, up)


def chain_coherence(levels: list[bytes], fmt: int, width: int, height: int, min_blocks: int = 2) -> list[float]:
    """mip_coherence of each pair (L-1, L), L = 1.., for the levels that have at least `min_blocks` x `min_blocks`
    blocks to compare (index 0 of the result is the pair 0/1)."""
    out = []
    for m in range(1, len(levels)):
        bx1, by1, _ = level_dims(fmt, width, height, m)
        if min(bx1, by1) < min_blocks:
            break
        out.append(mip_coherence(levels[m - 1], levels[m], fmt, width, height, m))
    return out
