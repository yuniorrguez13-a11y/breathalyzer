"""Wii U BFRES 4.5 animations inside model files <-> the Switch animation structures of the neutral model (Integrate
milestone).

Wii U files keep skeletal anims as FSKA, and material anims in four kinds: shader param (`*_fsp`), colour (`*_fcl`)
and tex SRT (`*_fts`) anims are all FSHU (one layout, three dictionaries), texture pattern anims (`*_ftp`, armour dye)
are FTXP. The Switch keeps FSKA and puts every material anim into one FMAA list. The neutral SkelAnim / MatAnim hold the
Switch structures (nx_model), so the reader builds Switch heads from the Wii U fields:

- FSKA, bone anims, curves, base data: the same fields; numbers swap as 32-bit words (curve floats and the integer
  offset of int curves are copied as raw bits), frames and keys by their element size (f32 4, Decimal10x5 / s16 2,
  u8 / s8 1; bool keys are 32-bit words). The Wii U bone anim's u8 "begin curve" becomes the Switch s32.
- FSHU -> FMAA with param infos (6 u16 each on both), constants (8 bytes: u32 target offset, 4-byte value) and curves.
  The Switch head counts at 0x70 "param curves + constants" (checked on the user's 38 param FMAA).
- FTXP -> FMAA with pattern infos and the texture name list. The Wii U pattern info is (s8 curve, s8 sub bind); the
  Switch one (s16 curve, u16 begin constant = 0xFFFF, s8 sub bind). The Wii U base data (first texture of each pattern)
  is not stored on Switch: it is the first key of the pattern's curve (checked, warning on a difference).
- Switch FMAA lists are sorted by name (45/45 in the user's files), so the merged list is sorted.

Dropped with a warning, never guessed: bone and material visibility anims (FVIS), shape anims (FSHA), scene anims
(FSCN), constant texture patterns (no curve), and user data of material anims. None of them occur in the user's Switch
model files, so there is no Switch layout to check them against.

Layouts from BfresLibrary's Wii U loaders (MIT licence): FSKA 0x30, bone anim 0x18, curve 0x24, FSHU 0x34 (material
0x20, param info 0x10), FTXP 0x38 (material 0x1C, pattern info 8). No real Wii U animation has been seen yet (V3).
"""
from __future__ import annotations

import struct
from array import array

from .errors import Damaged, Unsupported
from .neutral import AnimCurve, BoneAnim, MatAnim, MatAnimData, SkelAnim, UserData
from .nx_model import base_data_size, curve_sizes

FSKA_SIZE, BONE_ANIM_SIZE, CURVE_SIZE = 0x30, 0x18, 0x24
FSHU_SIZE, FSHU_MAT_SIZE, PARAM_INFO_SIZE = 0x34, 0x20, 0x10
FTXP_SIZE, FTXP_MAT_SIZE, PATTERN_INFO_SIZE = 0x38, 0x1C, 0x08
TEXREF_SIZE = 8
FRAME_UNIT = {0: 4, 1: 2, 2: 1}
KEY_UNIT = {0: 4, 1: 2, 2: 1}
# Wii U dictionaries of material anims (wiiu_models.GROUP_NAMES index) by the name suffix Nintendo's tools give them
FSHU_GROUPS = {3: "_fsp", 4: "_fcl", 5: "_fts"}
FTXP_GROUP = 6
DROPPED_GROUPS = (7, 8, 9, 10)          # bone vis, material vis, shape, scene anims


def swap(data, unit: int) -> bytes:
    """Byte-swap every `unit`-byte value (unit 1: unchanged)."""
    if unit == 1 or not data:
        return bytes(data)
    if len(data) % unit:
        raise Damaged(f"{len(data)} bytes is not a whole number of {unit}-byte values")
    a = array({2: "H", 4: "I"}[unit])
    a.frombytes(bytes(data))
    a.byteswap()
    return a.tobytes()


def curve_units(flags: int) -> tuple[int, int]:
    """(frame element size, key element size) of a curve: bool curves (types 6, 7) keep 32-bit words of bits."""
    ftype, ktype, ctype = flags & 3, (flags >> 2) & 3, (flags >> 4) & 7
    if ftype not in FRAME_UNIT or ktype not in KEY_UNIT:
        raise Unsupported(f"animation curve flags {flags:#x} (frame/key type)")
    return FRAME_UNIT[ftype], 4 if ctype in (6, 7) else KEY_UNIT[ktype]


def first_int_key(c: AnimCurve) -> int | None:
    """First value of a step / baked int curve (types 4, 5): key + the integer offset; None for other curves."""
    flags, n, _t, _s, _e, _sc, offset = struct.unpack_from("<HHIIIIi", c.head)
    if (flags >> 4) & 7 not in (4, 5) or not n:
        return None
    code = {0: "<i", 1: "<h", 2: "<b"}[(flags >> 2) & 3]
    return struct.unpack_from(code, c.keys)[0] + offset


def _sorted_names(anims: list) -> list:
    return sorted(anims, key=lambda a: a.name.encode("utf-8"))


# --------------------------------------------------------------------------------------------------------- reader

def read_curves(rd, arr: int, n: int, what: str) -> list[AnimCurve]:
    r = rd.r
    out = []
    for i in range(n):
        c = arr + CURVE_SIZE * i
        r.need(c, CURVE_SIZE, f"{what} curve")
        flags, keys, target, *rest = r.unpack("HHI5I", c, what)
        fsz, ksz = curve_sizes(flags, keys)
        funit, kunit = curve_units(flags)
        fa = rd.need_rel(c + 0x1C, keys, f"{what} curve frames")
        ka = rd.need_rel(c + 0x20, keys, f"{what} curve keys")
        frames = swap(r.raw(fa, fsz, "curve frames"), funit) if fsz else b""
        keyb = swap(r.raw(ka, ksz, "curve keys"), kunit) if ksz else b""
        out.append(AnimCurve(struct.pack("<HHI5I4x", flags, keys, target, *rest), frames, keyb))
    return out


def _anim_user_data(rd, off: int, n: int, what: str) -> list[UserData]:
    uds = rd.user_data(off, n, what)
    for u in uds:
        if u.kind == "wstring":
            rd.warn(f"{what}: wide-string user data {u.name!r} becomes a plain string")
            u.kind = "string"
    return uds


def _check_name(rd, pos: int, name: str, what: str) -> None:
    got = rd.s(pos, f"{what} name")
    if got != name:
        raise Damaged(f"{what} dictionary key {name!r} != its name {got!r}")


def read_fska(rd, o: int, name: str) -> SkelAnim:
    r = rd.r
    what = f"skeletal animation {name!r}"
    if r.raw(o, 4, "FSKA") != b"FSKA":
        raise Damaged(f"{what} at {o:#x} is not an FSKA")
    r.need(o, FSKA_SIZE, "FSKA")
    _check_name(rd, o + 4, name, what)
    path = rd.s(o + 8, "animation path")
    flags, frames, nbone, nud, ncurve, baked = r.unpack("IiHHiI", o + 0x0C)
    barr = rd.need_rel(o + 0x20, nbone, f"{what} bone anims")
    if rd.rel(o + 0x24):
        rd.warn(f"{what}: bind skeleton offset is set; ignored (bound at run time)")
    bind = rd.need_rel(o + 0x28, nbone, f"{what} bind indices")
    binds = list(r.unpack(f"{nbone}H", bind)) if nbone else []
    bones, total = [], 0
    for i in range(nbone):
        b = barr + BONE_ANIM_SIZE * i
        r.need(b, BONE_ANIM_SIZE, "bone anim")
        bflags = r.u32(b)
        bname = rd.s(b + 4, "bone anim name")
        br, bt, nc, bbt, bc = r.unpack("5B", b + 8)
        carr = rd.need_rel(b + 0x10, nc, f"{what} bone {bname!r} curves")
        base_off = rd.rel(b + 0x14)
        base = swap(r.raw(base_off, base_data_size(bflags), "bone anim base data"), 4) if base_off else None
        curves = read_curves(rd, carr, nc, f"{what} bone {bname!r}")
        bones.append(BoneAnim(bname, struct.pack("<IBBBBii", bflags, br, bt, nc, bbt, bc, 0), base, curves))
        total += nc
    if total != ncurve:
        rd.warn(f"{what}: header curve count {ncurve} but the bone anims have {total} (the Switch file gets {total})")
    ud = _anim_user_data(rd, rd.rel(o + 0x2C), nud, what)
    head = struct.pack("<IiiIHHI", flags, frames, total, baked, nbone, len(ud), 0)
    return SkelAnim(name, path, head, binds, bones, ud)


def _mat_anim_common(rd, o: int, name: str, nmat: int, nud: int, ud_pos: int, what: str):
    r = rd.r
    if rd.rel(o + 0x24):
        rd.warn(f"{what}: bind model offset is set; ignored (bound at run time)")
    bind = rd.need_rel(o + 0x28, nmat, f"{what} bind indices")
    binds = list(r.unpack(f"{nmat}H", bind)) if nmat else []
    marr = rd.need_rel(o + 0x2C, nmat, f"{what} materials")
    ud = rd.user_data(rd.rel(ud_pos), nud, what)
    if ud:
        rd.warn(f"{what}: user data ({', '.join(u.name for u in ud)}) dropped: no Switch material animation in "
                f"your files has user data, so where it goes is not known")
    return binds, marr


def read_fshu(rd, o: int, name: str) -> MatAnim:
    r = rd.r
    what = f"material animation {name!r}"
    if r.raw(o, 4, "FSHU") != b"FSHU":
        raise Damaged(f"{what} at {o:#x} is not an FSHU")
    r.need(o, FSHU_SIZE, "FSHU")
    _check_name(rd, o + 4, name, what)
    path = rd.s(o + 8, "animation path")
    flags, frames, nmat, nud, _nparam, _ncurve, baked = r.unpack("IiHHiiI", o + 0x0C)
    if flags >> 16:
        rd.warn(f"{what}: flag bits {flags >> 16 << 16:#x} have no place on the Switch; dropped")
    binds, marr = _mat_anim_common(rd, o, name, nmat, nud, o + 0x30, what)
    mats = []
    for i in range(nmat):
        m = marr + FSHU_MAT_SIZE * i
        r.need(m, FSHU_MAT_SIZE, "FSHU material")
        npi, ncv, ncst = r.unpack("HHH", m)
        curve_idx, _info_idx = r.unpack("ii", m + 8)
        mname = rd.s(m + 0x10, "material anim name")
        mw = f"{what} material {mname!r}"
        pia = rd.need_rel(m + 0x14, npi, f"{mw} param infos")
        ca = rd.need_rel(m + 0x18, ncv, f"{mw} curves")
        csa = rd.need_rel(m + 0x1C, ncst, f"{mw} constants")
        infos = []
        for k in range(npi):
            a = pia + PARAM_INFO_SIZE * k
            vals = r.unpack("6H", a, "param anim info")
            infos.append((rd.s(a + 0x0C, "param anim name"), struct.pack("<6H4x", *vals)))
        consts = swap(r.raw(csa, 8 * ncst, "anim constants"), 4) if ncst else b""
        curves = read_curves(rd, ca, ncv, mw)
        if not -0x8000 <= curve_idx < 0x8000:
            raise Unsupported(f"{mw}: first curve index {curve_idx} does not fit the Switch's 16 bits")
        mats.append(MatAnimData(mname, struct.pack("<5h4H6x", curve_idx, -1, -1, -1, -1, npi, 0, ncst, ncv),
                                infos, [], curves, consts))
    ncurves = sum(len(m.curves) for m in mats)
    count = ncurves + sum(len(m.constants) // 8 for m in mats)
    head = struct.pack("<HHHHiIHHHH", flags & 0xFFFF, 0, nmat, ncurves, frames, baked, count, 0, 0, 0)
    return MatAnim(name, path, head, binds, mats, [])


def _texture_names(rd, off: int, n: int, what: str) -> list[str]:
    """Keys of the FTXP texture dictionary. Its values should be texture refs (name offset, texture offset); they are
    only checked (a warning if not), the keys are what the Switch keeps."""
    ents = rd.group(off, f"{what} textures", null_data_ok=True)
    if len(ents) != n:
        raise Damaged(f"{what}: {len(ents)} texture names, the count says {n}")
    odd = 0
    for key, data in ents:
        if not data:
            continue
        try:
            rd.r.need(data, TEXREF_SIZE, "texture ref")
            if rd.s(data, "texture ref name") != key or rd.r.u32(data + 4):
                odd += 1
        except Damaged:
            odd += 1
    if odd:
        rd.warn(f"{what}: {odd} texture dictionary value(s) are not (name, 0) texture refs; only the names are used")
    return [k for k, _ in ents]


def read_ftxp(rd, o: int, name: str) -> MatAnim:
    r = rd.r
    what = f"texture pattern animation {name!r}"
    if r.raw(o, 4, "FTXP") != b"FTXP":
        raise Damaged(f"{what} at {o:#x} is not an FTXP")
    r.need(o, FTXP_SIZE, "FTXP")
    _check_name(rd, o + 4, name, what)
    path = rd.s(o + 8, "animation path")
    flags, nud, frames, ntex, nmat, _npat, _ncurve, baked = r.unpack("HHiHHiiI", o + 0x0C)
    binds, marr = _mat_anim_common(rd, o, name, nmat, nud, o + 0x34, what)
    textures = _texture_names(rd, rd.rel(o + 0x30), ntex, what)
    mats, dropped = [], []
    for i in range(nmat):
        m = marr + FTXP_MAT_SIZE * i
        r.need(m, FTXP_MAT_SIZE, "FTXP material")
        ntp, ncv = r.unpack("HH", m)
        curve_idx, _info_idx = r.unpack("ii", m + 4)
        mname = rd.s(m + 0x0C, "material anim name")
        mw = f"{what} material {mname!r}"
        tpa = rd.need_rel(m + 0x10, ntp, f"{mw} pattern infos")
        ca = rd.need_rel(m + 0x14, ncv, f"{mw} curves")
        bda = rd.need_rel(m + 0x18, ntp, f"{mw} base data")
        base = r.unpack(f"{ntp}H", bda, "pattern base data") if ntp else ()
        curves = read_curves(rd, ca, ncv, mw)
        pats = []
        for k in range(ntp):
            a = tpa + PATTERN_INFO_SIZE * k
            ci, sub = r.unpack("bb", a, "pattern anim info")
            pname = rd.s(a + 4, "pattern anim name")
            if ci < 0:
                dropped.append(f"{mname}/{pname} (texture {base[k]})")
                continue
            if ci >= ncv:
                raise Damaged(f"{mw}: pattern {pname!r} uses curve {ci} of {ncv}")
            first = first_int_key(curves[ci])
            if first is not None and first != base[k]:
                rd.warn(f"{mw}: pattern {pname!r} starts with texture {base[k]} but its curve with {first}; the Switch "
                        f"keeps only the curve")
            if first is not None and not 0 <= first < max(1, ntex):
                rd.warn(f"{mw}: pattern {pname!r} names texture {first} of {ntex}")
            pats.append((pname, struct.pack("<hHb3x", ci, 0xFFFF, sub)))
        if not -0x8000 <= curve_idx < 0x8000:
            raise Unsupported(f"{mw}: first curve index {curve_idx} does not fit the Switch's 16 bits")
        mats.append(MatAnimData(mname, struct.pack("<5h4H6x", -1, curve_idx, -1, -1, -1, 0, len(pats), 0, ncv),
                                [], pats, curves, b""))
    if dropped:
        rd.warn(f"{what}: constant texture pattern(s) dropped (no curve; the Switch layout of a constant pattern is "
                f"not confirmed): {', '.join(dropped[:4])}{'...' if len(dropped) > 4 else ''}")
    ncurves = sum(len(m.curves) for m in mats)
    npats = sum(len(m.pattern_infos) for m in mats)
    head = struct.pack("<HHHHiIHHHH", flags, 0, nmat, ncurves, frames, baked, 0, npats, 0, len(textures))
    return MatAnim(name, path, head, binds, mats, textures)


def read_all(rd, entries: list, group_names) -> tuple[list[SkelAnim], list[MatAnim], dict]:
    """(skeletal anims in file order, material anims merged and sorted by name, {group: [names dropped]})."""
    skel = [read_fska(rd, off, n) for n, off in entries[2]]
    mats: list[MatAnim] = []
    for g in FSHU_GROUPS:
        mats += [read_fshu(rd, off, n) for n, off in entries[g]]
    mats += [read_ftxp(rd, off, n) for n, off in entries[FTXP_GROUP]]
    seen, merged = set(), []
    for a in _sorted_names(mats):
        if a.name in seen:
            rd.warn(f"two material animations named {a.name!r}: the Switch keeps one name once; the second dropped")
            continue
        seen.add(a.name)
        merged.append(a)
    dropped = {group_names[i]: [n for n, _ in entries[i]] for i in DROPPED_GROUPS if entries[i]}
    if dropped:
        rd.warn("not converted, dropped: " + "; ".join(
            f"{k}: {', '.join(v[:4])}{'...' if len(v) > 4 else ''}" for k, v in dropped.items())
            + " (no Switch model file of yours has these, so their Switch layout cannot be checked)")
    return skel, merged, dropped


# ------------------------------------------------------------------------------------------- writer (tests only)

def unwritable(a) -> str | None:
    """Why a Switch animation has no Wii U form in our test writer (None: it has one)."""
    if isinstance(a, SkelAnim):
        for b in a.bone_anims:
            bc = struct.unpack_from("<i", b.head, 8)[0]
            if not 0 <= bc <= 255:
                return f"bone anim {b.name!r} begins at curve {bc} (a Wii U u8)"
        return None
    params = any(m.param_infos for m in a.materials)
    pats = any(m.pattern_infos for m in a.materials)
    if params and pats:
        return "holds both param and texture pattern anims (two Wii U animations)"
    if a.user_data:
        return "has user data"
    if struct.unpack_from("<H", a.head, 0x14)[0]:
        return "has visibility anims"
    for m in a.materials:
        if struct.unpack_from("<3h", m.head, 4) != (-1, -1, -1):
            return f"material {m.name!r} has visibility curves or constants"
        for n, raw in m.pattern_infos:
            ci, bc, _sb = struct.unpack("<hHb3x", raw)
            if bc != 0xFFFF or not 0 <= ci <= 127:
                return f"pattern {m.name}/{n} is constant or uses curve {ci}"
    return None


def wiiu_group(a: MatAnim) -> int:
    """Wii U dictionary of a writable Switch material anim: FTXP for patterns, else FSHU by name suffix."""
    if any(m.pattern_infos for m in a.materials):
        return FTXP_GROUP
    for g, suffix in FSHU_GROUPS.items():
        if a.name.endswith(suffix):
            return g
    return 3


def write_curves(w, curves: list[AnimCurve]) -> int:
    arr = w.alloc(CURVE_SIZE * len(curves))
    for k, c in enumerate(curves):
        o = arr + CURVE_SIZE * k
        flags, keys, target, *rest = struct.unpack("<HHI5I4x", c.head)
        if curve_sizes(flags, keys) != (len(c.frames), len(c.keys)):
            raise Damaged(f"animation curve: frame / key bytes do not match flags {flags:#x} and {keys} keys")
        funit, kunit = curve_units(flags)
        w.put("HHI5I", o, flags, keys, target, *rest)
        fa = w.alloc(len(c.frames))
        w.buf[fa:fa + len(c.frames)] = swap(c.frames, funit)
        ka = w.alloc(len(c.keys))
        w.buf[ka:ka + len(c.keys)] = swap(c.keys, kunit)
        w.rel(o + 0x1C, fa)
        w.rel(o + 0x20, ka)
    return arr


def _u16_array(w, values) -> int:
    o = w.alloc(2 * len(values))
    if values:
        w.put(f"{len(values)}H", o, *values)
    return o


def write_fska(w, a: SkelAnim) -> int:
    o = w.alloc(FSKA_SIZE)
    w.buf[o:o + 4] = b"FSKA"
    w.rs(o + 4, a.name)
    w.rs(o + 8, a.path)
    flags, frames, _nc, baked, _nb, _nu, _pad = struct.unpack("<IiiIHHI", a.head)
    n = len(a.bone_anims)
    w.put("IiHHiI", o + 0x0C, flags, frames, n, len(a.user_data), sum(len(b.curves) for b in a.bone_anims), baked)
    barr = w.alloc(BONE_ANIM_SIZE * n)
    w.rel(o + 0x20, barr)
    w.rel(o + 0x28, _u16_array(w, a.bind_indices))
    for k, b in enumerate(a.bone_anims):
        bo = barr + BONE_ANIM_SIZE * k
        bflags, br, bt, _nc, bbt, bc, _p = struct.unpack("<IBBBBii", b.head)
        w.put("I", bo, bflags)
        w.rs(bo + 4, b.name)
        w.put("5B", bo + 8, br, bt, len(b.curves), bbt, bc)
        if b.curves:
            w.rel(bo + 0x10, write_curves(w, b.curves))
        if b.base is not None:
            d = w.alloc(len(b.base))
            w.buf[d:d + len(b.base)] = swap(b.base, 4)
            w.rel(bo + 0x14, d)
    w.rel(o + 0x2C, w.user_data(a.user_data))
    return o


def write_fshu(w, a: MatAnim) -> int:
    o = w.alloc(FSHU_SIZE)
    w.buf[o:o + 4] = b"FSHU"
    w.rs(o + 4, a.name)
    w.rs(o + 8, a.path)
    flags, _ud, _nm, _nc, frames, baked = struct.unpack_from("<HHHHiI", a.head)
    w.put("IiHHiiI", o + 0x0C, flags, frames, len(a.materials), 0, sum(len(m.param_infos) for m in a.materials),
          sum(len(m.curves) for m in a.materials), baked)
    w.rel(o + 0x28, _u16_array(w, a.bind_indices))
    marr = w.alloc(FSHU_MAT_SIZE * len(a.materials))
    w.rel(o + 0x2C, marr)
    info = 0
    for k, m in enumerate(a.materials):
        mo = marr + FSHU_MAT_SIZE * k
        nconst = len(m.constants) // 8
        w.put("HHHxxii", mo, len(m.param_infos), len(m.curves), nconst, struct.unpack_from("<h", m.head)[0], info)
        info += len(m.param_infos)
        w.rs(mo + 0x10, m.name)
        pa = w.alloc(PARAM_INFO_SIZE * len(m.param_infos))
        for j, (n, raw) in enumerate(m.param_infos):
            w.put("6H", pa + PARAM_INFO_SIZE * j, *struct.unpack("<6H4x", raw))
            w.rs(pa + PARAM_INFO_SIZE * j + 0x0C, n)
        w.rel(mo + 0x14, pa)
        w.rel(mo + 0x18, write_curves(w, m.curves))
        c = w.alloc(len(m.constants))
        w.buf[c:c + len(m.constants)] = swap(m.constants, 4)
        w.rel(mo + 0x1C, c)
    return o


def write_ftxp(w, a: MatAnim) -> int:
    o = w.alloc(FTXP_SIZE)
    w.buf[o:o + 4] = b"FTXP"
    w.rs(o + 4, a.name)
    w.rs(o + 8, a.path)
    flags, _ud, _nm, _nc, frames, baked = struct.unpack_from("<HHHHiI", a.head)
    w.put("HHiHHiiI", o + 0x0C, flags, 0, frames, len(a.textures), len(a.materials),
          sum(len(m.pattern_infos) for m in a.materials), sum(len(m.curves) for m in a.materials), baked)
    w.rel(o + 0x28, _u16_array(w, a.bind_indices))
    marr = w.alloc(FTXP_MAT_SIZE * len(a.materials))
    w.rel(o + 0x2C, marr)
    refs = []
    for t in a.textures:
        ref = w.alloc(TEXREF_SIZE)
        w.rs(ref, t)
        refs.append((t, ref))
    if refs:
        w.rel(o + 0x30, w.group(refs))
    info = 0
    for k, m in enumerate(a.materials):
        mo = marr + FTXP_MAT_SIZE * k
        w.put("HHii", mo, len(m.pattern_infos), len(m.curves), struct.unpack_from("<h", m.head, 2)[0], info)
        info += len(m.pattern_infos)
        w.rs(mo + 0x0C, m.name)
        pa = w.alloc(PATTERN_INFO_SIZE * len(m.pattern_infos))
        base = []
        for j, (n, raw) in enumerate(m.pattern_infos):
            ci, _bc, sub = struct.unpack("<hHb3x", raw)
            w.put("bb", pa + PATTERN_INFO_SIZE * j, ci, sub)
            w.rs(pa + PATTERN_INFO_SIZE * j + 4, n)
            first = first_int_key(m.curves[ci]) if 0 <= ci < len(m.curves) else None
            base.append((first or 0) & 0xFFFF)
        w.rel(mo + 0x10, pa)
        w.rel(mo + 0x14, write_curves(w, m.curves))
        w.rel(mo + 0x18, _u16_array(w, base))
    return o
