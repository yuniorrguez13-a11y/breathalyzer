"""Switch BNTX 4.0.0.0 read/write (textures of `X.Tex.sbfres` and item icons), from out/model-work/switch-format.md
section 7 (layout checked on the user's own files).

Layout written (as the game's files have it):
    000 header (0x20)   020 NX header (0x38)   058 memory pool info (0x140 zeros)   198 BRTI pointer array
    _STR (8-aligned): texture names, the BNTX name, then the texture `_DIC`
    BRTI blocks: 0xA0 info + 0x100 run-time texture + 0x100 run-time view + 8 x mips mip offsets
    BRTD block header so that the data starts on a 0x1000 boundary; texture data packed (each image size aligned 0x200)
    _RLT on the next 0x1000 boundary: section 0 = [0, end of the last mip array), section 1 = [BRTD, RLT)
"""
from __future__ import annotations

import struct

from . import dicts, rlt, strpool, tegra
from .binio import Reader, align, put
from .errors import Damaged, ModelError, Unsupported
from .formats import block_info, level_dims
from .neutral import Texture, TextureSet

MAGIC = b"BNTX\0\0\0\0"
VERSION = 0x00040000
BRTI_INFO = 0xA0
BRTI_FIXED = 0x2A0
POOL_INFO = 0x140

# BRTI values every BotW texture has; a texture that differs keeps its value in extra (nx.*) so V1 can rewrite it.
DEFAULTS = {"nx.flags": 1, "nx.access": 0x20, "nx.layout2": 0x00010007, "nx.alignment": 0x200, "nx.samples": 1}


def is_bntx(data) -> bool:
    return bytes(data[:8]) == MAGIC


def read(data, name: str = "", only: set[str] | None = None, refused: list[str] | None = None) -> TextureSet:
    """Every texture of a BNTX as neutral linear levels. `only`: read just the textures with these names (the others
    are not untiled). `refused`: when given, a texture this reader refuses is skipped and noted there ('name: why')
    instead of failing the whole file."""
    r = Reader(data, "<", "BNTX")
    if r.raw(0, 8) != MAGIC:
        raise Damaged("not a BNTX file")
    ver = r.u32(8)
    if ver >> 16 != 4:
        raise Unsupported(f"BNTX version {ver:#010x} (only 4.x is read)")
    if r.u16(0x0C) != 0xFEFF:
        raise Unsupported("big-endian BNTX is not supported")
    name_off = r.u32(0x10)
    bntx_name = strpool.nx_string(r.data, name_off - 2, "BNTX name") if name_off >= 2 else ""
    if r.raw(0x20, 4) != b"NX  ":
        raise Unsupported(f"BNTX platform {r.raw(0x20, 4)!r} (only NX)")
    count = r.u32(0x24)
    info_arr, brtd = r.u64(0x28), r.u64(0x30)
    if r.raw(brtd, 4, "BRTD") != b"BRTD":
        raise Damaged("BRTD block not found")
    textures = []
    for t in range(count):
        o = r.u64(info_arr + 8 * t, "texture pointer")
        if only is not None and _brti_name(r, o) not in only:
            continue
        try:
            textures.append(_read_brti(r, o))
        except ModelError as e:
            if refused is None:
                raise
            refused.append(f"{_brti_name(r, o)}: {e}")
    ts = TextureSet(name, textures, "model")
    if bntx_name != "textures":
        ts.extra["nx.bntx_name"] = bntx_name
    if only is not None or refused:
        return ts  # a partial set is never written back; the string order only matters for writing
    order = _string_order(r)
    if order != [t.name for t in textures] + [bntx_name]:
        ts.extra["nx.string_order"] = order
    return ts


def _string_order(r: Reader) -> list[str]:
    """Strings of the `_STR` block in file order (the game's tool does not sort them; kept for byte-identical V1)."""
    st = r.u16(0x16)
    if r.raw(st, 4, "_STR") != b"_STR":
        raise Damaged("BNTX string pool not found")
    count = r.u32(st + 0x10)
    o, out = st + 0x14, []
    for _ in range(count + 1):
        s = strpool.nx_string(r.data, o, "string")
        n = len(s.encode("utf-8"))
        if out or s:
            out.append(s)
        o += 2 + n + 1
        o += o % 2
    return out


def _brti_name(r: Reader, o: int) -> str:
    if r.raw(o, 4, "BRTI") != b"BRTI":
        raise Damaged(f"texture info at {o:#x} is not a BRTI block")
    return strpool.nx_string(r.data, r.u64(o + 0x60), "texture name")


def swizzle_fields(data) -> dict[str, int]:
    """{texture name: BRTI swizzle field} of a BNTX, without untiling anything."""
    r = Reader(data, "<", "BNTX")
    if r.raw(0, 8) != MAGIC:
        raise Damaged("not a BNTX file")
    count, info_arr = r.u32(0x24), r.u64(0x28)
    out = {}
    for t in range(count):
        o = r.u64(info_arr + 8 * t, "texture pointer")
        out[_brti_name(r, o)] = r.u16(o + 0x14)
    return out


def _read_brti(r: Reader, o: int) -> Texture:
    if r.raw(o, 4, "BRTI") != b"BRTI":
        raise Damaged(f"texture info at {o:#x} is not a BRTI block")
    flags, dim, tile, swz, mips = r.unpack("BBHHH", o + 0x10, "BRTI")
    samples, fmt, access, w, h, depth, arr, lay, lay2 = r.unpack("IIIiiiiii", o + 0x18, "BRTI")
    image_size, alignment = r.u32(o + 0x50), r.u32(o + 0x54)
    ch = tuple(r.raw(o + 0x58, 4))
    view_dim = r.u8(o + 0x5C)
    name = strpool.nx_string(r.data, r.u64(o + 0x60), "texture name")
    mip_arr = r.u64(o + 0x70)
    user_data = r.u64(o + 0x78)
    if dim != 2 or view_dim != 1:
        raise Unsupported(f"texture {name!r}: dimension {dim}/{view_dim} (only 2D textures are converted)")
    if depth != 1 or arr != 1:
        raise Unsupported(f"texture {name!r}: depth {depth}, array length {arr} (cube maps and arrays are refused)")
    if tile != 0:
        raise Unsupported(f"texture {name!r}: tile mode {tile} (only block-linear)")
    if samples != 1:
        raise Unsupported(f"texture {name!r}: {samples} samples")
    if user_data:
        raise Unsupported(f"texture {name!r} has user data")
    if not 1 <= mips <= 16 or w <= 0 or h <= 0:
        raise Damaged(f"texture {name!r}: size {w}x{h}, {mips} mips")
    block_info(fmt)
    bh_log2 = lay & 7
    offs = [r.u64(mip_arr + 8 * m, "mip offset") for m in range(mips)]
    lay_, total = tegra.mip_layout(fmt, w, h, mips, bh_log2)
    rel = [x - offs[0] for x in offs]
    if rel != [x[0] for x in lay_]:
        raise Damaged(f"texture {name!r}: mip offsets {rel} do not follow the block-linear layout")
    if image_size < lay_[-1][0] + lay_[-1][1]:
        raise Damaged(f"texture {name!r}: image size {image_size:#x} is too small for its mips")
    data = r.view(offs[0], image_size, "texture data")
    levels = tegra.untile_texture(data, fmt, w, h, mips, bh_log2, rel)
    tex = Texture(name, fmt, w, h, levels, ch, swz)
    tex.extra["nx.block_height_log2"] = bh_log2
    for key, val in (("nx.flags", flags), ("nx.access", access), ("nx.layout2", lay2),
                     ("nx.alignment", alignment), ("nx.samples", samples)):
        if val != DEFAULTS[key]:
            tex.extra[key] = val
    if lay & ~7:
        tex.extra["nx.layout_high"] = lay & ~7
    if image_size != align(total, alignment or 0x200):
        tex.extra["nx.image_size"] = image_size
    return tex


def block_height_log2(tex: Texture) -> int:
    if "nx.block_height_log2" in tex.extra:
        return int(tex.extra["nx.block_height_log2"])
    _, rows, _ = level_dims(tex.format, tex.width, tex.height, 0)
    return tegra.game_block_height_log2(rows)


def write(ts: TextureSet) -> bytes:
    """BNTX bytes for a texture set (every texture, full mip chains as given)."""
    texs = ts.textures
    n = len(texs)
    bntx_name = ts.extra.get("nx.bntx_name", "textures")
    for t in texs:
        t.check()
        if t.dim != "2d" or t.depth != 1 or t.array != 1:
            raise Unsupported(f"texture {t.name!r}: only 2D textures can be written")
    # images first (sizes needed for the layout)
    images = []
    for t in texs:
        bh = block_height_log2(t)
        img = tegra.tile_texture(t.levels, t.format, t.width, t.height, bh)
        alignment = t.extra.get("nx.alignment", 0x200)
        size = t.extra.get("nx.image_size", align(len(img), alignment))
        if size < len(img):
            raise ValueError(f"texture {t.name!r}: image size {size:#x} is smaller than its data")
        images.append((img + bytes(size - len(img)), bh))

    ptrs: list[tuple[int, int]] = []
    info_arr = 0x58 + POOL_INFO
    str_off = align(info_arr + 8 * n, 8)
    wanted = [t.name for t in texs] + [bntx_name]
    order = [x for x in ts.extra.get("nx.string_order", []) if x in wanted]
    body, soffs = strpool.nx_pool(order + [x for x in wanted if x not in order])
    str_end = str_off + 0x10 + len(body)
    dict_off = align(str_end, 8)
    brti_off = []
    pos = dict_off + dicts.nx_dict_size(n)
    for t in texs:
        pos = align(pos, 8)
        brti_off.append(pos)
        pos += BRTI_FIXED + 8 * t.mips
    sec0_end = pos
    brtd = align(pos + 0x10, 0x1000) - 0x10
    data_off = brtd + 0x10
    total_data = sum(len(img) for img, _ in images)
    rlt_off = align(data_off + total_data, 0x1000)

    buf = bytearray(rlt_off)
    s = lambda key: str_off + 0x10 + soffs[key]  # noqa: E731  (pointer to a string's u16 length)
    # header
    buf[0:8] = MAGIC
    put(buf, "<IHBBIHHI", 8, VERSION, 0xFEFF, 12, 0x40, s(bntx_name) + 2, 0, str_off, rlt_off)
    # NX header
    buf[0x20:0x24] = b"NX  "
    put(buf, "<IQQQQQII", 0x24, n, info_arr, brtd, dict_off, 0x58, 0, 0, 0)
    ptrs += [(0x28, info_arr), (0x30, brtd), (0x38, dict_off), (0x40, 0x58)]
    for i, o in enumerate(brti_off):
        put(buf, "<Q", info_arr + 8 * i, o)
        ptrs.append((info_arr + 8 * i, o))
    # strings + dict
    put(buf, "<4sIII", str_off, b"_STR", brti_off[0] - str_off if n else 0, (brti_off[0] if n else brtd) - str_off, 0)
    buf[str_off + 0x10:str_end] = body
    key_ptrs = [s(t.name) for t in texs]
    buf[dict_off:dict_off + dicts.nx_dict_size(n)] = dicts.nx_dict(b"_DIC", [t.name for t in texs], key_ptrs, s(""))
    ptrs.append((dict_off + 0x10, s("")))
    ptrs += [(dict_off + 8 + 16 * (i + 1) + 8, p) for i, p in enumerate(key_ptrs)]
    # BRTI blocks + data
    cur = data_off
    for i, (t, (img, bh)) in enumerate(zip(texs, images)):
        o = brti_off[i]
        nxt = (brti_off[i + 1] if i + 1 < n else brtd) - o
        lay_, _ = tegra.mip_layout(t.format, t.width, t.height, t.mips, bh)
        put(buf, "<4sIII", o, b"BRTI", nxt, nxt, 0)
        put(buf, "<BBHHH", o + 0x10, t.extra.get("nx.flags", 1), 2, 0, t.swizzle_hint, t.mips)
        put(buf, "<IIIiiiiii", o + 0x18, t.extra.get("nx.samples", 1), t.format, t.extra.get("nx.access", 0x20),
            t.width, t.height, 1, 1, bh | t.extra.get("nx.layout_high", 0), t.extra.get("nx.layout2", 0x00010007))
        put(buf, "<II4BB", o + 0x50, len(img), t.extra.get("nx.alignment", 0x200), *t.channels, 1)
        mip_arr = o + BRTI_FIXED
        put(buf, "<QQQQQQQQ", o + 0x60, s(t.name), 0x20, mip_arr, 0, o + BRTI_INFO, o + BRTI_INFO + 0x100, 0, 0)
        ptrs += [(o + 0x60, s(t.name)), (o + 0x68, 0x20), (o + 0x70, mip_arr), (o + 0x80, o + BRTI_INFO),
                 (o + 0x88, o + BRTI_INFO + 0x100)]
        for m, (moff, _, _) in enumerate(lay_):
            put(buf, "<Q", mip_arr + 8 * m, cur + moff)
            ptrs.append((mip_arr + 8 * m, cur + moff))
        buf[cur:cur + len(img)] = img
        cur += len(img)
    put(buf, "<4sIII", brtd, b"BRTD", 0, rlt_off - brtd, 0)
    table = rlt.build(rlt_off, [(0, sec0_end), (brtd, rlt_off - brtd)], ptrs)
    buf += table
    struct.pack_into("<I", buf, 0x1C, len(buf))
    return bytes(buf)
