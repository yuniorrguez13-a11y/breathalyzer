"""Wii U BFRES (3.4.0.x - 4.5.0.x, big-endian) core and FTEX textures (milestone M1).

Layouts from out/model-work/wiiu-format.md (public docs: wut GX2 structs (zlib), BfresLibrary layouts (MIT)); no real
Wii U file has been seen yet, so the reader is strict and every assumption is checked where the data allows.
Offsets are s32 relative to their own field (0 = none); a string offset points at the characters.

Textures: what an FTEX holds is decided by the FRES name, as BfresLibrary does. In `X.Tex1` an FTEX's data (0xB0) is
level 0 and its mip data offset (0xB4) is ignored (warned about when set); the mips come from the `X.Tex2` FTEX of the
same name, whose data (0xB0, mip size bytes) is the mips and whose image size is ignored. Any other name (item icons,
some tools' output): 0xB0 is the image and 0xB4 the mips; only there is the role guessed from the content. A problem
with one texture's Tex2 entry costs that texture its mips (warning), not the whole set.

After untiling, mip 1 is compared with a 2x2 average of mip 0 (bc.mip_coherence): a low value warns that the Wii U
layout may have been read wrong. It is the only check on real data until Wii U samples exist.

The Wii U writer is for tests only (Switch -> Wii U -> Switch) and shares its offsets with the reader, so the round trip
cannot catch a GX2 layout mistake; see gx2.surface_layout for its known differences from GX2.
"""
from __future__ import annotations

import struct

from . import bc, gx2, dicts, strpool
from .binio import Reader, align, put
from .errors import Damaged, ModelError, Unsupported
from .formats import block_info, channels_from_gx2, channels_to_gx2, gx2_format, level_dims, nx_format
from .neutral import Texture, TextureSet

HEADER_SIZE = 0x6C
FTEX_SIZE = 0xC0
VERSION = 0x04050003
MIN_VERSION, MAX_VERSION = 0x03040000, 0x0405FFFF
DICT_NAMES = ("models", "textures", "skeletal anims", "shader param anims", "color anims", "tex SRT anims",
              "tex pattern anims", "bone visibility anims", "material visibility anims", "shape anims",
              "scene anims", "external files")
GX2_DIM_2D = 1
GX2_USE_TEXTURE = 1
# Mip check: warn when mip 1 correlates with the 2x2 average of mip 0 below this, for textures whose mip 1 has at
# least COHERENCE_MIN_BLOCKS blocks each way. Measured on 342 of the user's Switch textures run through our GX2 tiler
# (bc.mip_coherence samples at most 16x16 blocks, about 1 ms a texture): right decodes fall below 0.5 in 2 (0.6%,
# two BC1 normal maps); wrong decodes fall below it in 79% (pipe/bank swizzle 1 instead of 0), 89% (2D read as 1D),
# 90% (1D read as 2D), 91% (swizzle 5), 92% (no untiling) and 99% (level 0 bytes read as mip 1). 0.9 would flag 57
# right ones. Smaller mips give too few blocks for a stable number.
COHERENCE_WARN = 0.5
COHERENCE_MIN_BLOCKS = 4
NO_MIPS = "only the main image (no mipmaps)"


def is_wiiu_bfres(data) -> bool:
    return bytes(data[:4]) == b"FRES" and bytes(data[8:10]) == b"\xfe\xff"


def file_kind(name: str) -> str | None:
    """'tex1' / 'tex2' from the FRES name (BfresLibrary decides by the name too); None for any other name."""
    if name.endswith(".Tex1"):
        return "tex1"
    if name.endswith(".Tex2"):
        return "tex2"
    return None


def _rel(r: Reader, pos: int) -> int:
    v = r.s32(pos)
    return pos + v if v else 0


def _str(r: Reader, off: int, what: str) -> str:
    return r.cstr(off, what) if off else ""


def read_header(data) -> dict:
    r = Reader(data, ">", "Wii U BFRES")
    if r.raw(0, 4) != b"FRES" or r.u16(8) != 0xFEFF:
        raise Damaged("not a Wii U (big-endian) BFRES")
    ver = r.u32(4)
    if not MIN_VERSION <= ver <= MAX_VERSION:
        raise Unsupported(f"Wii U BFRES version {ver:#010x} (3.4 to 4.5 are read)")
    size = r.u32(0x0C)
    if size > len(r.data):
        raise Damaged(f"file is cut short ({len(r.data):#x} of {size:#x} bytes)")
    groups = [_rel(r, 0x20 + 4 * i) for i in range(12)]
    counts = list(r.unpack("12H", 0x50))
    return {"r": r, "version": ver, "alignment": r.u32(0x10), "name": _str(r, _rel(r, 0x14), "file name"),
            "groups": groups, "counts": counts}


def read_group(r: Reader, off: int) -> list[tuple[str, int]]:
    """[(name, absolute data offset)] of an index group, in entry order."""
    if not off:
        return []
    n = r.s32(off + 4, "index group")
    if n < 0 or n > 0xFFFF:
        raise Damaged(f"index group at {off:#x} has {n} entries")
    out = []
    for i in range(1, n + 1):
        e = off + 8 + 16 * i
        out.append((_str(r, _rel(r, e + 8), "entry name"), _rel(r, e + 12)))
    return out


class Ftex:
    """Raw FTEX fields. `kind`: 'tex1' / 'tex2' / None from the file's FRES name (see file_kind)."""

    def __init__(self, r: Reader, o: int, kind: str | None = None):
        if r.raw(o, 4, "FTEX") != b"FTEX":
            raise Damaged(f"texture at {o:#x} is not an FTEX section")
        (self.dim, self.width, self.height, self.depth, self.mips, self.format, self.aa, self.use, self.image_size,
         _, self.mip_size, _, self.tile_mode, self.swizzle, self.alignment, self.pitch) = r.unpack("16I", o + 4)
        self.mip_offsets = list(r.unpack("13I", o + 0x44))
        self.view = r.unpack("4I", o + 0x78)
        self.comp_map = r.u32(o + 0x88)
        self.regs = list(r.unpack("5I", o + 0x8C))
        self.handle = r.u32(o + 0xA0)
        self.array_length = r.u8(o + 0xA4)
        self.name = _str(r, _rel(r, o + 0xA8), "texture name")
        self.path = _str(r, _rel(r, o + 0xAC), "texture path")
        self.data_off = _rel(r, o + 0xB0)
        self.mip_data_off = _rel(r, o + 0xB4)
        self.user_data_count = r.u16(o + 0xBC)
        self.kind = kind
        self.offset = o
        self.r = r

    @property
    def role(self) -> str:
        """'full' (image + mips here, or one level only), 'tex1' (level 0 here, mips in a Tex2), 'tex2' (mips only).
        Decided by the file name; guessed from the content only for other names."""
        if self.kind == "tex1":
            return "tex1" if self.mips > 1 else "full"
        if self.kind == "tex2":
            return "tex2"
        if self.image_size == 0:
            return "tex2"
        if self.mips <= 1 or self.mip_data_off:
            return "full"
        return "tex1"

    def image(self) -> memoryview:
        if not self.image_size:
            raise Damaged(f"texture {self.name!r}: no main image (image size 0)")
        if not self.data_off:
            raise Damaged(f"texture {self.name!r}: image size {self.image_size:#x} but no data offset")
        return self.r.view(self.data_off, self.image_size, f"texture {self.name!r} image")

    def mip_data(self) -> memoryview:
        if self.role == "tex2":
            off, field = self.data_off, "data offset (0xB0)"
        elif self.role == "full":
            off, field = self.mip_data_off, "mip data offset (0xB4)"
        else:
            raise Damaged(f"texture {self.name!r}: a Tex1 entry holds no mips")
        if not self.mip_size:
            raise Damaged(f"texture {self.name!r}: {self.mips} mip levels but no mip data (mip size 0)")
        if not off:
            raise Damaged(f"texture {self.name!r}: {self.mip_size:#x} bytes of mips but no {field}")
        return self.r.view(off, self.mip_size, f"texture {self.name!r} mip data")


def read_ftex_list(data) -> tuple[dict, list[Ftex]]:
    h = read_header(data)
    r = h["r"]
    kind = file_kind(h["name"])
    ftex = [Ftex(r, off, kind) for _, off in read_group(r, h["groups"][1])]
    return h, ftex


def _check_ftex(f: Ftex) -> None:
    if f.dim != GX2_DIM_2D:
        raise Unsupported(f"texture {f.name!r}: GX2 dimension {f.dim} (only 2D; cube maps, arrays, 3D refused)")
    if f.depth not in (0, 1):
        raise Unsupported(f"texture {f.name!r}: depth/array length {f.depth}")
    if f.aa != 0:
        raise Unsupported(f"texture {f.name!r}: multisampled (AA mode {f.aa})")
    if not 1 <= f.mips <= gx2.MAX_MIPS or f.width <= 0 or f.height <= 0:
        raise Damaged(f"texture {f.name!r}: {f.width}x{f.height}, {f.mips} mips")
    nx_format(f.format)
    if f.tile_mode not in gx2.SUPPORTED_MODES:
        raise Unsupported(f"texture {f.name!r}: GX2 tile mode {f.tile_mode} is not supported")
    if f.tile_mode == gx2.LINEAR_SPECIAL and f.mips > 1:
        raise Unsupported(f"texture {f.name!r}: GX2 tile mode 16 (linear special) with {f.mips} mip levels; how GX2 "
                          f"lays out its mips is not known (no sample)")


def _check_layout(f: Ftex, fmt: int, warnings: list, strict: bool = False) -> None:
    """Level 0 must fit its data. Swizzle bits 16+ should equal the first 1D level our addrlib rule computes; the
    untiler never reads that field (it computes each level's mode), so a difference is a warning, and an error only
    when `strict` (self_check of our own writer's output)."""
    levels = [gx2.surface_level(fmt, f.width, f.height, m, f.tile_mode) for m in range(f.mips)]
    if f.tile_mode in gx2.MACRO and (strict or f.mips > 1):
        want = gx2.first_1d_level(levels)
        got = (f.swizzle >> 16) & 0xFF
        if got != want:
            if strict:
                raise Damaged(f"texture {f.name!r}: swizzle says mip {got} is the first 1D-tiled level, the GX2 rule "
                              f"gives {want}")
            warnings.append(f"{f.name}: the swizzle field says mip {got} is the first 1D-tiled level, the GX2 rule "
                            f"gives {want}; the mips are read with the rule (the field is not used)")
    if f.pitch < levels[0].width:
        raise Damaged(f"texture {f.name!r}: pitch {f.pitch} < width {levels[0].width} elements")
    if f.role != "tex2":
        _, _, bpe = block_info(fmt)
        lv0 = f.pitch * levels[0].padded_height * bpe
        if f.image_size < lv0:
            raise Damaged(f"texture {f.name!r}: level 0 needs {lv0:#x} bytes, image size is {f.image_size:#x}")


def _check_mips(f: Ftex, fmt: int, mip_len: int, offsets: list[int]) -> None:
    """Each mip slice (increasing offsets, gx2.mip_slices) holds at least its level's size."""
    _, _, bpe = block_info(fmt)
    for m, (a, b) in enumerate(gx2.mip_slices(mip_len, f.mips, offsets), 1):
        lv = gx2.surface_level(fmt, f.width, f.height, m, f.tile_mode)
        _, lpitch = gx2.level_pitch(fmt, f.width, f.height, m, f.tile_mode, f.pitch)
        need = lpitch * lv.padded_height * bpe
        if b - a < need:
            raise Damaged(f"texture {f.name!r}: mip {m} has {b - a:#x} bytes, its level size is {need:#x}")


def _tex2_mips(f: Ftex, src: Ftex, fmt: int, warnings: list):
    """(mip data, offsets, mip swizzle or None) from the Tex2 entry `src` of Tex1 entry `f`; ModelError if unusable."""
    if (src.width, src.height, src.mips, src.format) != (f.width, f.height, f.mips, f.format):
        raise Damaged(f"Tex2 entry describes a different texture ({src.width}x{src.height}, {src.mips} mips, format "
                      f"{src.format:#x}; Tex1 says {f.width}x{f.height}, {f.mips} mips, format {f.format:#x})")
    data = src.mip_data()
    offsets = f.mip_offsets
    if (src.tile_mode, src.pitch) != (f.tile_mode, f.pitch):
        warnings.append(f"{f.name}: Tex2 says tile mode {src.tile_mode} / pitch {src.pitch}, Tex1 "
                        f"{f.tile_mode} / {f.pitch}; Tex1's are used")
    if f.mip_size and src.mip_size != f.mip_size:
        warnings.append(f"{f.name}: Tex1 says {f.mip_size:#x} bytes of mips, Tex2 has {src.mip_size:#x}")
    if src.mip_offsets[1:] != f.mip_offsets[1:]:
        warnings.append(f"{f.name}: Tex1 and Tex2 mip offsets differ; Tex2's are used")
        offsets = src.mip_offsets
    _check_mips(f, fmt, len(data), offsets)
    mip_swizzle = src.swizzle if (src.swizzle >> 8) & 7 != (f.swizzle >> 8) & 7 else None
    return data, offsets, mip_swizzle


def _texture(f: Ftex, mip_src: Ftex | None, warnings: list) -> Texture:
    _check_ftex(f)
    fmt = nx_format(f.format)
    _check_layout(f, fmt, warnings)
    image = bytes(f.image())
    mip_data, mip_swizzle, offsets, missing = None, None, f.mip_offsets, None
    if f.mips > 1:
        if f.role == "full":
            mip_data = f.mip_data()
            _check_mips(f, fmt, len(mip_data), offsets)
        elif mip_src is None:
            missing = "Tex2 data missing"
        else:
            try:
                mip_data, offsets, mip_swizzle = _tex2_mips(f, mip_src, fmt, warnings)
            except ModelError as e:
                missing = f"Tex2 entry not usable ({e})"

    def untile_mips(data: bytes, sw: int | None) -> list[bytes]:
        return gx2.untile_texture(fmt, f.width, f.height, f.mips, f.tile_mode, f.swizzle, f.pitch, None, data,
                                  offsets, sw, first=1)

    levels = gx2.untile_texture(fmt, f.width, f.height, f.mips, f.tile_mode, f.swizzle, f.pitch, image, None, offsets)
    choice = None
    if mip_data is not None:
        mip_bytes = bytes(mip_data)
        levels += untile_mips(mip_bytes, None)
        # A different Tex2 swizzle matters only when mip 1 is macro-tiled (1D tiling has no pipe/bank swizzle).
        if mip_swizzle is not None and gx2.surface_level(fmt, f.width, f.height, 1, f.tile_mode).mode in gx2.MACRO:
            alt = untile_mips(mip_bytes, mip_swizzle)
            if alt != levels[1:]:
                choice = _choose_mip_swizzle(fmt, f.width, f.height, levels, alt)
                levels = choice[1]
                warnings.append(f"{f.name}: Tex1/Tex2 swizzles differ ({(f.swizzle >> 8) & 7}/"
                                f"{(mip_swizzle >> 8) & 7}); mips untiled with the {choice[0]} one (mip check "
                                f"{choice[2]:.2f} vs {choice[3]:.2f})")
    tex = Texture(f.name, fmt, f.width, f.height, levels, channels_from_gx2(f.comp_map), (f.swizzle >> 8) & 7)
    if choice is not None:
        tex.extra["wiiu.mip_swizzle_choice"] = choice[0]
    if len(levels) > 1:
        coh = choice[2] if choice is not None else bc.mip_coherence(levels[0], levels[1], fmt, f.width, f.height)
        if coh == coh:
            tex.extra["wiiu.mip_coherence"] = round(coh, 3)
            bx, by, _ = level_dims(fmt, f.width, f.height, 1)
            if min(bx, by) >= COHERENCE_MIN_BLOCKS and coh < COHERENCE_WARN:
                warnings.append(f"{f.name}: mip 1 does not look like a smaller copy of mip 0 (mip check {coh:.2f}, "
                                f"below {COHERENCE_WARN}); the Wii U data may have been read wrong")
    if missing is not None:
        note = f"{f.name}: {missing}; {NO_MIPS}"
        tex.extra["wiiu.mips_missing"] = f.mips
        tex.extra["wiiu.mips_missing_note"] = note
        warnings.append(note)
    if f.user_data_count:
        warnings.append(f"{f.name}: texture user data ({f.user_data_count} entries) dropped")
    tex.extra.update({"wiiu.tile_mode": f.tile_mode, "wiiu.swizzle": f.swizzle, "wiiu.pitch": f.pitch,
                      "wiiu.alignment": f.alignment, "wiiu.mip_offsets": list(offsets), "wiiu.use": f.use})
    if f.path:
        tex.extra["wiiu.path"] = f.path
    if any(f.regs):
        tex.extra["wiiu.regs"] = list(f.regs)
    if mip_swizzle is not None:
        tex.extra["wiiu.mip_swizzle"] = mip_swizzle
    return tex


def _choose_mip_swizzle(fmt: int, width: int, height: int, levels: list[bytes], alt_mips: list[bytes]):
    """(choice, levels, its mip check, the other's): keep the mips (Tex1's swizzle in `levels`, Tex2's in `alt_mips`)
    whose mip 1 matches mip 0 better."""
    s1 = bc.mip_coherence(levels[0], levels[1], fmt, width, height)
    s2 = bc.mip_coherence(levels[0], alt_mips[0], fmt, width, height)
    if s2 == s2 and (s1 != s1 or s2 >= s1):  # NaN-safe: prefer Tex2's on a tie (BfresPlatformConverter does)
        return "Tex2", levels[:1] + alt_mips, s2, s1
    return "Tex1", levels, s1, s2


def read_textures(tex1, tex2=None, warnings: list | None = None) -> TextureSet:
    warnings = [] if warnings is None else warnings
    h1, f1 = read_ftex_list(tex1)
    k1 = file_kind(h1["name"])
    if k1 == "tex2" or (f1 and all(f.role == "tex2" for f in f1)):
        raise Unsupported("this file holds only mipmaps (Tex2); the main image is in Tex1")
    if not f1:
        raise Unsupported(f"no textures in this file ({h1['name'] or 'unnamed'})")
    if k1 == "tex1":
        for f in f1:
            if f.mip_data_off:
                warnings.append(f"{f.name}: Tex1 entry has a mip data offset; ignored (a Tex1 holds level 0 only)")
    mips_by_name: dict[str, Ftex] = {}
    if tex2 is not None:
        h2, f2 = read_ftex_list(tex2)
        if file_kind(h2["name"]) == "tex1":
            raise Unsupported(f"the mipmap file given is {h2['name']}, a Tex1; the mipmaps are in the Tex2")
        for f in f2:
            if f.role != "tex2":
                warnings.append(f"{f.name}: Tex2 entry has its own main image; ignored")
                continue
            if f.image_size:
                warnings.append(f"{f.name}: Tex2 entry has an image size ({f.image_size:#x}); ignored (a Tex2 holds "
                                f"the mips only)")
            mips_by_name[f.name] = f
        extra = set(mips_by_name) - {f.name for f in f1}
        if extra:
            warnings.append(f"Tex2 textures without a Tex1 entry ignored: {sorted(extra)}")
    name = h1["name"]
    kind = "single"
    for suffix in (".Tex1", ".Tex2", ".Tex"):
        if name.endswith(suffix):
            name, kind = name[:-len(suffix)], "model"
            break
    if tex2 is not None:
        kind = "model"
    textures = [_texture(f, mips_by_name.get(f.name), warnings) for f in f1]
    ts = TextureSet(name, textures, kind)
    ts.extra["wiiu.alignment"] = h1["alignment"]
    ts.extra["wiiu.version"] = h1["version"]
    return ts


# ------------------------------------------------------------------------------------------------ writer (tests)

def _write_file(fres_name: str, entries: list[dict], role: str, file_align: int) -> bytes:
    """One Wii U texture BFRES. entries: dicts with the FTEX fields + 'image'/'mip' bytes. role: 'tex1', 'tex2' or
    'full'."""
    n = len(entries)
    group_off = HEADER_SIZE
    ftex0 = align(group_off + dicts.wiiu_group_size(n), 0x20)
    str_base = ftex0 + FTEX_SIZE * n
    strings = [fres_name] + [e["name"] for e in entries] + [e["path"] for e in entries if e["path"]]
    pool, soffs = strpool.wiiu_pool(strings, str_base)
    pos = str_base + len(pool)
    blocks = []                        # (offset, bytes)
    data_offs, mip_offs = [], []
    for e in entries:
        a = max(e["alignment"], 0x200)
        d_main = e["image"] if role in ("tex1", "full") else e["mip"]
        pos = align(pos, a)
        data_offs.append(pos)
        blocks.append((pos, d_main))
        pos += len(d_main)
        if role == "full" and e["mip"]:
            pos = align(pos, a)
            mip_offs.append(pos)
            blocks.append((pos, e["mip"]))
            pos += len(e["mip"])
        else:
            mip_offs.append(0)
    size = pos
    buf = bytearray(size)
    put(buf, ">4sIHHII", 0, b"FRES", VERSION, 0xFEFF, 0x10, size, file_align)
    put(buf, ">iIi", 0x14, soffs[fres_name] - 0x14, len(pool), str_base - 0x1C)
    put(buf, ">i", 0x24, group_off - 0x24)
    put(buf, ">12H", 0x50, *[n if i == 1 else 0 for i in range(12)])
    ftex_offs = [ftex0 + FTEX_SIZE * i for i in range(n)]
    buf[group_off:group_off + dicts.wiiu_group_size(n)] = dicts.wiiu_group(
        group_off, [e["name"] for e in entries], [soffs[e["name"]] for e in entries], ftex_offs)
    for i, e in enumerate(entries):
        o = ftex_offs[i]
        image_size = e["image_size"] if role != "tex2" else 0
        put(buf, ">4s16I", o, b"FTEX", GX2_DIM_2D, e["width"], e["height"], 1, e["mips"], e["format"], 0,
            e["use"], image_size, 0, e["mip_size"], 0, e["tile_mode"], e["swizzle"], e["alignment"], e["pitch"])
        put(buf, ">13I", o + 0x44, *e["mip_offsets"])
        put(buf, ">4I", o + 0x78, 0, e["mips"], 0, 1)
        put(buf, ">I5III", o + 0x88, e["comp_map"], *e["regs"], 0, 1 << 24)
        put(buf, ">i", o + 0xA8, soffs[e["name"]] - (o + 0xA8))
        if e["path"]:
            put(buf, ">i", o + 0xAC, soffs[e["path"]] - (o + 0xAC))
        put(buf, ">ii", o + 0xB0, data_offs[i] - (o + 0xB0), mip_offs[i] - (o + 0xB4) if mip_offs[i] else 0)
    buf[str_base:str_base + len(pool)] = pool
    for off, b in blocks:
        buf[off:off + len(b)] = b
    return bytes(buf)


def texture_entry(t: Texture) -> dict:
    """GX2 surface fields + tiled data for one neutral texture."""
    t.check()
    if t.dim != "2d" or t.depth != 1 or t.array != 1:
        raise Unsupported(f"texture {t.name!r}: only 2D textures can be written")
    mode = int(t.extra.get("wiiu.tile_mode", gx2.TILED_2D_THIN1))
    lay, swizzle, image, mip = gx2.tile_texture(t.levels, t.format, t.width, t.height, t.swizzle_hint, mode)
    return {"name": t.name, "path": t.extra.get("wiiu.path", ""), "width": t.width, "height": t.height,
            "mips": t.mips, "format": gx2_format(t.format), "use": t.extra.get("wiiu.use", GX2_USE_TEXTURE),
            "image_size": lay.image_size, "mip_size": lay.mip_size, "tile_mode": mode, "swizzle": swizzle,
            "alignment": lay.alignment, "pitch": lay.pitch, "mip_offsets": lay.mip_offsets,
            "comp_map": channels_to_gx2(t.channels), "regs": list(t.extra.get("wiiu.regs", [0] * 5)),
            "image": image, "mip": mip}


def write_textures(ts: TextureSet) -> tuple[bytes, bytes | None]:
    """(Tex1, Tex2 or None) for kind 'model'; (single file, None) for kind 'single'. For tests only: the layout is
    not confirmed against GX2 (gx2.surface_layout), so this output is no evidence of what real Wii U files hold."""
    entries = [texture_entry(t) for t in ts.textures]
    file_align = max([0x1000] + [e["alignment"] for e in entries])
    if ts.kind == "single":
        return _write_file(ts.name, entries, "full", file_align), None
    tex1 = _write_file(ts.name + ".Tex1", entries, "tex1", file_align)
    with_mips = [e for e in entries if e["mips"] > 1]
    tex2 = _write_file(ts.name + ".Tex2", with_mips, "tex2", file_align) if with_mips else None
    return tex1, tex2


def self_check(data) -> list[str]:
    """Self-check of a Wii U texture file from our (test-only) writer: every offset in range, swizzle bits 16+ = the
    computed first 1D level (strict, unlike reading), mip slices increasing and large enough. Returns problems
    (empty = fine)."""
    problems = []
    try:
        h, ftex = read_ftex_list(data)
        r = h["r"]
        names = [f.name for f in ftex]
        for f in ftex:
            try:
                _check_ftex(f)
                fmt = nx_format(f.format)
                _check_layout(f, fmt, [], strict=True)
                if f.role in ("tex1", "full"):
                    f.image()
                if f.role in ("tex2", "full") and f.mips > 1:
                    _check_mips(f, fmt, len(f.mip_data()), f.mip_offsets)
                elif f.mips > 1 and f.mip_size:
                    _check_mips(f, fmt, f.mip_size, f.mip_offsets)
                if f.role == "tex1" and f.mip_data_off:
                    problems.append(f"texture {f.name!r}: Tex1 entry has a mip data offset")
            except (Damaged, Unsupported) as e:
                problems.append(str(e))
        if len(set(names)) != len(names):
            problems.append("duplicate texture names")
        if r.u32(0x0C) != len(r.data):
            problems.append(f"file size field {r.u32(0x0C):#x} != {len(r.data):#x}")
    except (Damaged, Unsupported, struct.error) as e:
        problems.append(str(e))
    return problems
