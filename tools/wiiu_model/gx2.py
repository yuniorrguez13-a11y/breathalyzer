"""Wii U GX2 surface tiling (R600/R700 addrlib with the Wii U constants), written in our own code.

Logic ported (formulas, not code) from AMD addrlib as adapted for R600/R700 by decaf-emu/addrlib
(Copyright (c) 2014 Advanced Micro Devices, Inc., MIT licence) and from wiiu_swizzle by ScanMountGoat
(Copyright (c) 2024 SMG, MIT licence: Wii U constants taken from Cemu captures, mip slicing, per-level pitch rule).
See NOTICE.md.

Wii U constants: 2 pipes, 4 banks, 256-byte pipe interleave, 2048-byte row, 256-byte swap, 2048-byte split. Work is
in elements: a 4x4 block for BC formats, a pixel otherwise. Only thin tile modes are handled: 1 linear aligned,
2 1D thin1, 4/5/6 2D thin1/thin2/thin4, 12 3D thin1 (+ 16 linear special for reading). Thick and bank-swapped modes
are refused (no sample to check them against).

The macro-tiled address splits into an x part and a y part (see out/model-work/wiiu-format.md section 5): the pixel
index bits from x and y are disjoint, pipe/bank = (x term) ^ (y term) ^ swizzle, and the macro offset only changes
every macro tile. So an index list costs one add per element; perm.py does the copy.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
from functools import lru_cache

from . import perm
from .errors import Damaged, Unsupported
from .formats import block_info

PIPES, BANKS = 2, 4
PIPE_INTERLEAVE = 256

LINEAR_GENERAL, LINEAR_ALIGNED, TILED_1D_THIN1 = 0, 1, 2
TILED_2D_THIN1, TILED_2D_THIN2, TILED_2D_THIN4 = 4, 5, 6
TILED_3D_THIN1, LINEAR_SPECIAL = 12, 16
MACRO = {TILED_2D_THIN1: (32, 16), TILED_2D_THIN2: (16, 32), TILED_2D_THIN4: (8, 64), TILED_3D_THIN1: (32, 16)}
SUPPORTED_MODES = {LINEAR_ALIGNED, TILED_1D_THIN1, *MACRO, LINEAR_SPECIAL}
NO_1D_LEVEL = 13            # swizzle bits 16+ when no level drops to 1D tiling
MAX_MIPS = 14               # 1 + 13 mip offsets

# Element index inside an 8x8 micro tile (thin, displayable), bit order from bit 0 up.
_MICRO_ORDER = {
    8: ("x0", "x1", "x2", "y1", "y0", "y2"),
    16: ("x0", "x1", "x2", "y0", "y1", "y2"),
    32: ("x0", "x1", "y0", "x2", "y1", "y2"),
    64: ("x0", "y0", "x1", "x2", "y1", "y2"),
    128: ("y0", "x0", "x1", "x2", "y1", "y2"),
}


def _micro_tables(bpp: int) -> tuple[list[int], list[int]]:
    order = _MICRO_ORDER[bpp]
    px, py = [0] * 8, [0] * 8
    for v in range(8):
        for pos, name in enumerate(order):
            if (v >> int(name[1])) & 1:
                if name[0] == "x":
                    px[v] |= 1 << pos
                else:
                    py[v] |= 1 << pos
    return px, py


MICRO = {bpp: _micro_tables(bpp) for bpp in _MICRO_ORDER}


def pixel_index(x: int, y: int, bpp: int) -> int:
    px, py = MICRO[bpp]
    return px[x & 7] | py[y & 7]


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def roundup(n: int, a: int) -> int:
    return -(-n // a) * a


def next_pow2(n: int) -> int:
    return 1 if n <= 1 else 1 << (n - 1).bit_length()


def _check_mode(mode: int) -> None:
    if mode not in SUPPORTED_MODES:
        raise Unsupported(f"GX2 tile mode {mode} is not supported (thin modes 1, 2, 4, 5, 6, 12, 16 only)")


# ------------------------------------------------------------------------------------------------ surface sizes

@dataclass(frozen=True)
class Level:
    mode: int          # tile mode of this level (2D levels drop to 1D when small)
    width: int         # elements
    height: int        # elements
    pitch: int         # elements (padded)
    padded_height: int
    size: int          # bytes
    base_align: int


def level_mode(mode: int, bpp: int, w_el: int, h_el: int, level: int) -> int:
    """Tile mode addrlib picks for a mip level (level 0 keeps the surface mode)."""
    if level == 0 or mode not in MACRO:
        return mode
    w, h = next_pow2(w_el), next_pow2(h_el)
    micro_bytes = bpp * 64 // 8
    waf = PIPE_INTERLEAVE // micro_bytes if micro_bytes <= PIPE_INTERLEAVE else 1
    mw, mh = MACRO[mode]
    if w < waf * mw or h < mh:
        return TILED_1D_THIN1
    return mode


def alignments(mode: int, bpp: int) -> tuple[int, int, int]:
    """(pitch align, height align, base align) in elements/elements/bytes."""
    if mode in (LINEAR_GENERAL, LINEAR_SPECIAL):
        return 1, 1, 1
    if mode == LINEAR_ALIGNED:
        return max(64, 2048 // bpp), 1, 256
    if mode == TILED_1D_THIN1:
        return max(8, 256 // bpp), 8, 256
    mw, mh = MACRO[mode]
    pitch_align = max(mw, mw * (256 // bpp // 8))
    macro_bytes = bpp * mw * mh // 8
    base = max(2 * macro_bytes, mh * pitch_align * bpp // 8)
    if (base // macro_bytes) % 2:
        base += macro_bytes
    return pitch_align, mh, base


def surface_level(fmt: int, width: int, height: int, level: int, mode: int) -> Level:
    """Size and alignment of one mip level, computed the way addrlib does for GX2 (one sample, one slice)."""
    _check_mode(mode)
    bw, bh, bpe = block_info(fmt)
    bpp = bpe * 8
    w_el = ceil_div(max(1, width >> level), bw)
    h_el = ceil_div(max(1, height >> level), bh)
    lmode = level_mode(mode, bpp, w_el, h_el, level)
    w, h = (next_pow2(w_el), next_pow2(h_el)) if level else (w_el, h_el)
    pa, ha, base = alignments(lmode, bpp)
    pitch, ph = roundup(w, pa), roundup(h, ha)
    if level == 0 and lmode in MACRO and bpp >= 32:
        # R6xx dual pitch rule (addrlib IsDualPitchAlignNeeded): an odd number of macro tiles across (> 1) gets one
        # more. Matches the Cemu-captured fixtures of wiiu_swizzle (pitch 32, 64 and 256 stay as they are).
        mw, mh = MACRO[lmode]
        if ((pitch - 1) // mw) & 1 == 0 and (pitch > mw or (((ph - 1) // mh) & 1 == 0 and ph > mh)):
            pitch += mw
    return Level(lmode, w_el, h_el, pitch, ph, pitch * ph * bpe, base)


@dataclass
class Layout:
    """GX2 surface fields our writer uses for a 2D texture."""
    levels: list[Level]
    image_size: int
    mip_size: int
    alignment: int
    pitch: int
    mip_offsets: list[int]    # 13 values as in GX2Surface
    first_1d_level: int


def first_1d_level(levels: list[Level]) -> int:
    if levels[0].mode not in MACRO:
        return 0
    for i, lv in enumerate(levels):
        if lv.mode not in MACRO:
            return i
    return NO_1D_LEVEL


def surface_layout(fmt: int, width: int, height: int, mips: int, mode: int = TILED_2D_THIN1) -> Layout:
    """Layout our Wii U writer uses. The writer exists for tests only (Switch -> Wii U -> Switch); reading never uses
    these offsets (it takes the file's). Known or suspected differences from GX2, to confirm on the first real
    Tex1/Tex2 sample before our Wii U output is used as evidence of anything:
    - GX2 puts (swizzle & 0xFFFF) bytes of padding before the first 1D-tiled mip level when that level is above 1;
      this layout does not (the reader handles the padded offsets: tested).
    - `alignments` adds one macro tile to the base alignment when (base / macro tile bytes) is odd (addrlib's
      IsDualBaseAlignNeeded form); wiiu_swizzle adds it when that number is even."""
    if not 1 <= mips <= MAX_MIPS:
        raise Unsupported(f"{mips} mip levels (GX2 allows 1..{MAX_MIPS})")
    levels = [surface_level(fmt, width, height, m, mode) for m in range(mips)]
    offsets = [0] * 13
    off = 0
    for m in range(1, mips):
        lv = levels[m]
        if m == 1:
            offsets[0] = roundup(levels[0].size, lv.base_align)
        else:
            off = roundup(off, lv.base_align)
            offsets[m - 1] = off
        off += lv.size
    return Layout(levels, levels[0].size, off, levels[0].base_align, levels[0].pitch, offsets, first_1d_level(levels))


def level_pitch(fmt: int, width: int, height: int, level: int, mode: int, file_pitch: int) -> tuple[int, int]:
    """(tile mode, pitch) to read mip `level` of a real file: level 0 uses the file's pitch; other levels the computed
    pitch, never more than the file's (wiiu_swizzle's rule, checked against Cemu)."""
    lv = surface_level(fmt, width, height, level, mode)
    if level == 0:
        return lv.mode, file_pitch
    return lv.mode, min(lv.pitch, file_pitch) if file_pitch else lv.pitch


def mip_slices(mip_data_len: int, mips: int, offsets: list[int]) -> list[tuple[int, int]]:
    """(start, end) of levels 1.. inside the mip data: level 1 at 0, level L >= 2 at offsets[L-1]; a level ends where
    the next starts, the last one at the end of the mip data. Starts must increase (0 is only valid as the unused
    offset after the last level), otherwise two levels would read the same bytes: Damaged."""
    if mips > MAX_MIPS:
        raise Damaged(f"{mips} mip levels (GX2 allows 1..{MAX_MIPS})")
    starts = [0] + [offsets[m - 1] for m in range(2, mips)]
    for m in range(1, len(starts)):
        if starts[m] <= starts[m - 1]:
            raise Damaged(f"mip {m + 1} starts at {starts[m]:#x}, not after mip {m} ({starts[m - 1]:#x}); the mip "
                          f"offsets are out of order")
    out = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else mip_data_len
        if not 0 <= start <= end <= mip_data_len:
            raise Damaged(f"mip {i + 1} spans {start:#x}..{end:#x}, mip data is {mip_data_len:#x} bytes")
        out.append((start, end))
    return out


# ------------------------------------------------------------------------------------------------ addresses

def _f(t: int) -> int:
    """Insert the 3 pipe/bank bits above bit 7."""
    return (t & 0xFF) | ((t & ~0xFF) << 3)


def ref_addr(x: int, y: int, bpp: int, pitch: int, mode: int, swizzle: int) -> int:
    """Reference (slow) byte address of element (x, y), slice 0, one sample. `swizzle` = pipe/bank swizzle 0..7."""
    bpe = bpp // 8
    if mode in (LINEAR_GENERAL, LINEAR_ALIGNED, LINEAR_SPECIAL):
        return (y * pitch + x) * bpe
    if mode == TILED_1D_THIN1:
        return 64 * bpe * (x // 8 + (y // 8) * (pitch // 8)) + pixel_index(x, y, bpp) * bpe
    _check_mode(mode)
    mw, mh = MACRO[mode]
    elem = pixel_index(x, y, bpp) * bpe
    pipe = ((x >> 3) ^ (y >> 3)) & 1
    bank = (((y >> 5) ^ (x >> 3)) & 1) | ((((y >> 4) ^ (x >> 4)) & 1) << 1)
    bp = ((pipe + PIPES * bank) ^ swizzle) & 7
    pipe, bank = bp & 1, bp >> 1
    macro_bytes = bpe * mw * mh
    macro_off = macro_bytes * (x // mw + (pitch // mw) * (y // mh))
    total = elem + (macro_off >> 3)
    return (bank << 9) | (pipe << 8) | _f(total)


def _unit(bpe: int) -> tuple[int, int, int]:
    unit = min(bpe, 8)
    return unit, bpe // unit, unit.bit_length() - 1


@lru_cache(maxsize=48)
def _index(w: int, h: int, bpp: int, pitch: int, mode: int, swizzle: int) -> array:
    bpe = bpp // 8
    unit, _, sh = _unit(bpe)
    if mode in (LINEAR_GENERAL, LINEAR_ALIGNED, LINEAR_SPECIAL):
        ax = [x * bpe >> sh for x in range(w)]
        cy = [y * pitch * bpe >> sh for y in range(h)]
        return array("I", [a + c for c in cy for a in ax])
    px, py = MICRO[bpp]
    if mode == TILED_1D_THIN1:
        mbytes = 64 * bpe
        ax = [(mbytes * (x >> 3) + px[x & 7] * bpe) >> sh for x in range(w)]
        pt = pitch // 8
        cy = [(mbytes * (y >> 3) * pt + py[y & 7] * bpe) >> sh for y in range(h)]
        return array("I", [a + c for c in cy for a in ax])
    mw, mh = MACRO[mode]
    per_row = pitch // mw
    mb8 = bpe * mw * mh >> 3
    fex = [_f(px[x & 7] * bpe) for x in range(w)]
    bxv = [((x >> 3) & 1) | (((x >> 3) & 1) << 1) | (((x >> 4) & 1) << 2) for x in range(w)]
    mtx = [x // mw for x in range(w)]
    n_mx = ceil_div(w, mw)
    tables: dict[tuple[int, int], list[int]] = {}
    rows = []
    for y in range(h):
        k = (((y >> 3) & 1) | (((y >> 5) & 1) << 1) | (((y >> 4) & 1) << 2)) ^ swizzle
        my = y // mh
        t = tables.get((k, my))
        if t is None:
            if tables and next(iter(tables))[1] != my:
                tables.clear()
            fm = [_f((mx + my * per_row) * mb8) for mx in range(n_mx)]
            t = [(((b ^ k) << 8) + e + fm[m]) >> sh for b, e, m in zip(bxv, fex, mtx)]
            tables[(k, my)] = t
        rows.append((t, _f(py[y & 7] * bpe) >> sh))
    return array("I", [a + c for t, c in rows for a in t])


def _geometry_check(w: int, h: int, pitch: int, mode: int) -> None:
    _check_mode(mode)
    if pitch < w:
        raise Damaged(f"pitch {pitch} is smaller than the level width {w}")
    if mode in MACRO and pitch % MACRO[mode][0]:
        raise Damaged(f"pitch {pitch} is not a multiple of the macro tile width {MACRO[mode][0]}")
    if mode == TILED_1D_THIN1 and pitch % 8:
        raise Damaged(f"pitch {pitch} is not a multiple of 8 (1D tiling)")


def untile(data, w: int, h: int, bpp: int, pitch: int, mode: int, swizzle: int) -> bytes:
    """Linear elements (row-major, packed) of one level. `swizzle` = pipe/bank swizzle (0..7)."""
    _geometry_check(w, h, pitch, mode)
    unit, lanes, _ = _unit(bpp // 8)
    return perm.gather(data, _index(w, h, bpp, pitch, mode, swizzle & 7), unit, lanes, "GX2 level")


def tile(linear, w: int, h: int, bpp: int, pitch: int, mode: int, swizzle: int, size: int) -> bytes:
    """One tiled level of `size` bytes (zero padding) from linear elements."""
    _geometry_check(w, h, pitch, mode)
    bpe = bpp // 8
    if len(linear) != w * h * bpe:
        raise ValueError(f"linear level is {len(linear)} bytes, expected {w * h * bpe}")
    unit, lanes, _ = _unit(bpe)
    return perm.scatter(linear, _index(w, h, bpp, pitch, mode, swizzle & 7), unit, lanes, size, "GX2 level")


def untile_ref(data, w: int, h: int, bpp: int, pitch: int, mode: int, swizzle: int) -> bytes:
    bpe = bpp // 8
    out = bytearray()
    for y in range(h):
        for x in range(w):
            a = ref_addr(x, y, bpp, pitch, mode, swizzle & 7)
            out += data[a:a + bpe]
    return bytes(out)


def tile_ref(linear, w: int, h: int, bpp: int, pitch: int, mode: int, swizzle: int, size: int) -> bytes:
    bpe = bpp // 8
    out = bytearray(size)
    for y in range(h):
        for x in range(w):
            a = ref_addr(x, y, bpp, pitch, mode, swizzle & 7)
            i = (y * w + x) * bpe
            out[a:a + bpe] = linear[i:i + bpe]
    return bytes(out)


# ------------------------------------------------------------------------------------------------ whole textures

def untile_texture(fmt: int, width: int, height: int, mips: int, mode: int, swizzle: int, pitch: int,
                   image: bytes | None, mip_data: bytes | None, mip_offsets: list[int],
                   mip_swizzle: int | None = None, first: int = 0) -> list[bytes]:
    """Linear levels `first`.. of a GX2 texture. `image` = level 0 data (unused when `first` > 0), `mip_data` =
    levels 1.. (None: level 0 only). `swizzle` is the whole GX2 swizzle field (bits 8-10 used); `mip_swizzle` the
    field used for levels >= 1."""
    _check_mode(mode)
    if mode == LINEAR_SPECIAL and mips > 1 and mip_data is not None:
        raise Unsupported("GX2 tile mode 16 (linear special) with mipmaps: the mip layout is not known (no sample)")
    bw, bh, bpe = block_info(fmt)
    bpp = bpe * 8
    levels = []
    n = mips if mip_data is not None else 1
    slices = [(0, len(image) if image is not None else 0)] + (
        mip_slices(len(mip_data), mips, mip_offsets) if n > 1 else [])
    for m in range(first, n):
        lmode, lpitch = level_pitch(fmt, width, height, m, mode, pitch)
        w_el = ceil_div(max(1, width >> m), bw)
        h_el = ceil_div(max(1, height >> m), bh)
        src = image if m == 0 else mip_data
        start, end = slices[m]
        sw = swizzle if m == 0 or mip_swizzle is None else mip_swizzle
        levels.append(untile(memoryview(src)[start:end], w_el, h_el, bpp, lpitch, lmode, (sw >> 8) & 7))
    return levels


def tile_texture(levels: list[bytes], fmt: int, width: int, height: int, swizzle_hint: int,
                 mode: int = TILED_2D_THIN1) -> tuple[Layout, int, bytes, bytes]:
    """(layout, swizzle field, image, mip data) of a GX2 texture from linear levels."""
    lay = surface_layout(fmt, width, height, len(levels), mode)
    swizzle = ((swizzle_hint & 7) << 8) | (lay.first_1d_level << 16)
    bw, bh, bpe = block_info(fmt)
    bpp = bpe * 8
    image = b""
    mip = bytearray(lay.mip_size)
    for m, lin in enumerate(levels):
        lv = lay.levels[m]
        data = tile(lin, lv.width, lv.height, bpp, lv.pitch, lv.mode, swizzle_hint & 7, lv.size)
        if m == 0:
            image = data
        else:
            start = 0 if m == 1 else lay.mip_offsets[m - 1]
            mip[start:start + lv.size] = data
    return lay, swizzle, image, bytes(mip)
