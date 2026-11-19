#!/usr/bin/env python3
"""Add picture panes and textures to the game's menu layouts (Mods page v2, docs/mod-pictures.md).

Bytes in, bytes out; nothing here reads or writes files. Used by tools/mod_pictures.py at deploy:

    Pack/Bootup.pack (SARC) > Layout/Common.sblarc (Yaz0) > SARC > blyt/*.bflyt + timg/__Combined.bntx

- `add_picture` adds a hidden picture pane to a BFLYT (a copy of a full-colour picture pane and its material, with
  its own texture name). Picture panes keep their own texture coordinates, so the Mods-page mod points a pane at one
  cell of an atlas at run time.
- `bntx_add_textures` adds textures to a BNTX without decoding the others: every texture the game has keeps its
  info and its texel bytes (formats this repo can't decode are fine), only pointers move.
- `patch_archive` changes files inside a SARC in place: files before a changed one keep their place, files after it
  move by a multiple of 0x2000, so every file keeps its alignment and its bytes.
- `yaz0_splice` makes the Yaz0 of the new archive from the old Yaz0 stream: stretches of the archive that only moved
  are copied as the game's own compressed groups, and only the changed parts are compressed (pure Python Yaz0 of
  33 MB takes minutes; this takes seconds). The result is decoded again and compared before it is used.
- `rebuild_bootup` does it all over the Bootup.pack in use and checks rule S1: every other entry of the pack is the
  same bytes as before.

Layout facts (nn::ui2d FLYT 8, BNTX 4) are from the public format notes and out/research/modmenu-v2-icons/ (the
pane copy is the HUD rupee icon's: a full-colour material that shows a texture as it is).
"""
from __future__ import annotations

import os
import struct
import sys
from array import array
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
import wiiu_convert as wc  # noqa: E402
from wiiu_model import dicts, rlt, strpool, tegra  # noqa: E402

COMMON = "Layout/Common.sblarc"
COMMON_RSTB = "Layout/Common.blarc"
BNTX_NAME = "timg/__Combined.bntx"
SHIFT = 0x2000          # files after a changed one move by a multiple of this (keeps any alignment up to 8 KiB)
YAZ0_WINDOW = 0x1000
YAZ0_MAX = 0x111

# Texture formats (BNTX codes) the atlases use
RGBA8 = 0x0B01
BC1 = 0x1A01
BC3 = 0x1C01
BLOCK = {RGBA8: (1, 4), BC1: (4, 8), BC3: (4, 16)}   # format -> (block side, bytes per block)

PANE_KINDS = ("pan1", "pic1", "txt1", "wnd1", "prt1", "bnd1")


class PatchError(Exception):
    """A layout, texture file or archive this tool doesn't know: the pictures are left out, the game file stays."""


# ================================================================================================ BFLYT

def _sections(d: bytes) -> tuple[bytearray, list[bytearray]]:
    if d[:4] != b"FLYT" or d[4:6] != b"\xff\xfe":
        raise PatchError("not a Switch layout (FLYT)")
    hsz = struct.unpack_from("<H", d, 6)[0]
    nsec = struct.unpack_from("<H", d, 0x10)[0]
    p, out = hsz, []
    for _ in range(nsec):
        if p + 8 > len(d):
            raise PatchError("layout section past the end")
        sz = struct.unpack_from("<I", d, p + 4)[0]
        if sz < 8 or p + sz > len(d):
            raise PatchError("layout section with a bad size")
        out.append(bytearray(d[p:p + sz]))
        p += sz
    return bytearray(d[:hsz]), out


def _join(head: bytearray, secs: list) -> bytes:
    body = b"".join(bytes(s) for s in secs)
    head = bytearray(head)
    struct.pack_into("<I", head, 0x0C, len(head) + len(body))
    struct.pack_into("<H", head, 0x10, len(secs))
    return bytes(head) + body


def _magic(s) -> str:
    return bytes(s[:4]).decode("latin1")


def _cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("utf-8", "replace")


def _strlist(mg: str, names: list[str]) -> bytearray:
    """txl1/fnl1: count, offsets (from the offset table), NUL-terminated names, padded to 4."""
    n = len(names)
    blob, offs = b"", []
    for nm in names:
        offs.append(4 * n + len(blob))
        blob += nm.encode() + b"\0"
    body = struct.pack("<HH", n, 0) + struct.pack(f"<{n}I", *offs) + blob
    body += bytes(-(8 + len(body)) % 4)
    return bytearray(mg.encode() + struct.pack("<I", 8 + len(body)) + body)


def _read_strlist(s) -> list[str]:
    n = struct.unpack_from("<H", s, 8)[0]
    offs = struct.unpack_from(f"<{n}I", s, 12)
    return [_cstr(bytes(s[12 + o:12 + o + 128])) for o in offs]


def _mat_records(s) -> list[bytearray]:
    n = struct.unpack_from("<H", s, 8)[0]
    offs = list(struct.unpack_from(f"<{n}I", s, 12))
    ends = offs[1:] + [len(s)]
    return [bytearray(s[o:e]) for o, e in zip(offs, ends)]


def _mat_section(recs: list) -> bytearray:
    n = len(recs)
    offs, p = [], 12 + 4 * n
    for r in recs:
        offs.append(p)
        p += len(r)
    body = struct.pack("<HH", n, 0) + struct.pack(f"<{n}I", *offs) + b"".join(bytes(r) for r in recs)
    return bytearray(b"mat1" + struct.pack("<I", 8 + len(body)) + body)


def pane_names(d: bytes) -> list[str]:
    _, secs = _sections(d)
    return [_cstr(bytes(s[12:36])) for s in secs if _magic(s) in PANE_KINDS]


def _find_pane(secs, name: str) -> int:
    for i, s in enumerate(secs):
        if _magic(s) in PANE_KINDS and _cstr(bytes(s[12:36])) == name:
            return i
    raise PatchError(f"the layout has no pane {name}")


def _end_of_children(secs, i: int) -> int:
    """Index of the pae1 that closes pane i's children."""
    j = i + 1
    if j < len(secs) and _magic(secs[j]) == "usd1":
        j += 1
    if j >= len(secs) or _magic(secs[j]) != "pas1":
        raise PatchError("the parent pane has no children")
    depth = 0
    for k in range(j, len(secs)):
        m = _magic(secs[k])
        if m == "pas1":
            depth += 1
        elif m == "pae1":
            depth -= 1
            if depth == 0:
                return k
    raise PatchError("the layout's pane tree is unbalanced")


def picture_source(d: bytes, pane: str) -> tuple[bytes, bytes]:
    """(pic1 section, its material record) of a picture pane, to copy."""
    _, secs = _sections(d)
    i = _find_pane(secs, pane)
    pic = bytes(secs[i])
    if _magic(pic) != "pic1":
        raise PatchError(f"{pane} isn't a picture pane")
    mi = next((k for k, s in enumerate(secs) if _magic(s) == "mat1"), None)
    if mi is None:
        raise PatchError("the layout has no materials")
    recs = _mat_records(secs[mi])
    m = struct.unpack_from("<H", pic, 0x64)[0]
    if m >= len(recs):
        raise PatchError(f"{pane} names a material the layout doesn't have")
    return pic, bytes(recs[m])


def add_picture(d: bytes, name: str, src_pic: bytes, src_mat: bytes, tex: str, pos: tuple, size: tuple,
                visible: bool = False, after: str | None = None, parent: str = "RootPane") -> bytes:
    """The layout with a new picture pane `name`: a copy of `src_pic` with a copy of `src_mat` whose one texture is
    `tex`, at `pos` (x, y) with `size` (w, h), texture coordinates over the whole texture. `after`: right after that
    pane (and its children); else the last child of `parent`. A pane of that name already there is an error."""
    if len(name.encode()) > 23 or len(tex.encode()) > 120:
        raise ValueError("pane or texture name too long")
    head, secs = _sections(d)
    if any(_magic(s) in PANE_KINDS and _cstr(bytes(s[12:36])) == name for s in secs):
        raise PatchError(f"the layout has a pane {name} already")
    ti = next((i for i, s in enumerate(secs) if _magic(s) == "txl1"), None)
    texs = _read_strlist(secs[ti]) if ti is not None else []
    if tex not in texs:
        texs.append(tex)
    tex_index = texs.index(tex)
    if ti is None:   # (txl1 goes before fnl1/mat1)
        ti = next((i for i, s in enumerate(secs) if _magic(s) in ("fnl1", "mat1")), None)
        if ti is None:
            raise PatchError("the layout has no materials")
        secs.insert(ti, _strlist("txl1", texs))
    else:
        secs[ti] = _strlist("txl1", texs)
    mi = next((i for i, s in enumerate(secs) if _magic(s) == "mat1"), None)
    if mi is None:
        raise PatchError("the layout has no materials")
    recs = _mat_records(secs[mi])
    m = bytearray(src_mat)
    if len(m) < 0x30 or struct.unpack_from("<I", m, 0x1C)[0] & 3 != 1:
        raise PatchError("the source material must have exactly one texture")
    m[0:0x1C] = name.encode().ljust(0x1C, b"\0")
    struct.pack_into("<h", m, 0x2C, tex_index)   # texmap 0: texture index (FLYT 8 layout)
    recs.append(m)
    secs[mi] = _mat_section(recs)
    pic = bytearray(src_pic)
    if len(pic) < 0x68 + 32 * pic[0x66]:
        raise PatchError("the source picture pane is too short")
    pic[12:36] = name.encode().ljust(24, b"\0")
    pic[8] = (pic[8] | 1) if visible else (pic[8] & ~1 & 0xFF)
    struct.pack_into("<3f", pic, 44, float(pos[0]), float(pos[1]), 0.0)
    struct.pack_into("<3f", pic, 56, 0.0, 0.0, 0.0)      # (no rotation, and scale 1: the rupee icon is drawn at 0.67)
    struct.pack_into("<2f", pic, 68, 1.0, 1.0)
    struct.pack_into("<2f", pic, 76, float(size[0]), float(size[1]))
    pic[10] = 255                                          # (fully opaque pane)
    struct.pack_into("<H", pic, 0x64, len(recs) - 1)
    for k in range(pic[0x66]):   # whole texture: TL TR BL BR
        struct.pack_into("<8f", pic, 0x68 + 32 * k, 0, 0, 1, 0, 0, 1, 1, 1)
    if after:
        i = _find_pane(secs, after)
        at = i + 1
        if at < len(secs) and _magic(secs[at]) == "usd1":
            at += 1
        if at < len(secs) and _magic(secs[at]) == "pas1":
            at = _end_of_children(secs, i) + 1
    else:
        at = _end_of_children(secs, _find_pane(secs, parent))
    secs.insert(at, pic)
    return _join(head, secs)


def picture_panes(d: bytes) -> dict[str, dict]:
    """{name: {"texture", "pos", "size", "visible"}} of the picture panes (for checks and tests)."""
    _, secs = _sections(d)
    ti = next((i for i, s in enumerate(secs) if _magic(s) == "txl1"), None)
    texs = _read_strlist(secs[ti]) if ti is not None else []
    mi = next((i for i, s in enumerate(secs) if _magic(s) == "mat1"), None)
    recs = _mat_records(secs[mi]) if mi is not None else []
    out = {}
    for s in secs:
        if _magic(s) != "pic1":
            continue
        m = struct.unpack_from("<H", s, 0x64)[0]
        t = struct.unpack_from("<h", recs[m], 0x2C)[0] if m < len(recs) and len(recs[m]) >= 0x2E else -1
        out[_cstr(bytes(s[12:36]))] = {"texture": texs[t] if 0 <= t < len(texs) else None,
                                       "pos": struct.unpack_from("<2f", s, 44),
                                       "size": struct.unpack_from("<2f", s, 76),
                                       "scale": struct.unpack_from("<2f", s, 68), "visible": bool(s[8] & 1)}
    return out


# ================================================================================================ BNTX

@dataclass
class NewTexture:
    name: str
    fmt: int            # RGBA8, BC1 or BC3
    w: int
    h: int
    data: bytes         # linear: RGBA8 rows, or BC blocks row by row
    channels: tuple = (2, 3, 4, 5)


def texture_names(bntx: bytes) -> list[str]:
    _check_bntx(bntx)
    count, info_arr = struct.unpack_from("<IQ", bntx, 0x24)
    return [strpool.nx_string(bntx, struct.unpack_from("<Q", bntx, struct.unpack_from("<Q", bntx, info_arr + 8 * i)[0]
                                                                         + 0x60)[0], "texture name")
            for i in range(count)]


def _check_bntx(b: bytes) -> None:
    if bytes(b[:8]) != b"BNTX\0\0\0\0" or bytes(b[0x20:0x24]) != b"NX  " \
            or struct.unpack_from("<H", b, 0x0C)[0] != 0xFEFF:
        raise PatchError("not a Switch texture file (BNTX)")


def bntx_add_textures(bntx: bytes, new: list[NewTexture]) -> tuple[bytes, list[tuple[int, int, int]]]:
    """(the BNTX with `new` textures added after the others, moved runs [(old offset, new offset, length)]). Every
    texture already there keeps its info block and texel bytes; the texel block moves as a whole (the run)."""
    b = bytes(bntx)
    _check_bntx(b)
    ver = struct.unpack_from("<I", b, 8)[0]
    if ver >> 16 != 4:
        raise PatchError(f"texture file version {ver:#x} isn't 4.x")
    name_ptr = struct.unpack_from("<I", b, 0x10)[0]
    bntx_name = strpool.nx_string(b, name_ptr - 2, "BNTX name") if name_ptr >= 2 else ""
    count, info_arr, brtd, dict_off, pool = struct.unpack_from("<IQQQQ", b, 0x24)
    if bytes(b[brtd:brtd + 4]) != b"BRTD":
        raise PatchError("texture file without its texel block")
    olds = []
    names = []
    lo_data, hi_data = None, 0
    for i in range(count):
        o = struct.unpack_from("<Q", b, info_arr + 8 * i)[0]
        if bytes(b[o:o + 4]) != b"BRTI":
            raise PatchError("texture file with a bad texture block")
        mips = struct.unpack_from("<H", b, o + 0x16)[0]
        image_size = struct.unpack_from("<I", b, o + 0x50)[0]
        name_p, parent, mip_arr, user, tex_p, view_p, slot, udict = struct.unpack_from("<8Q", b, o + 0x60)
        if user or udict or slot or not 1 <= mips <= 16:
            raise PatchError("texture file with texture user data (unknown kind)")
        offs = list(struct.unpack_from(f"<{mips}Q", b, mip_arr))
        if offs[0] < brtd + 0x10 or offs[0] + image_size > len(b):
            raise PatchError("texture data outside the texel block")
        lo_data = offs[0] if lo_data is None else min(lo_data, offs[0])
        hi_data = max(hi_data, offs[0] + image_size)
        name = strpool.nx_string(b, name_p, "texture name")
        names.append(name)
        olds.append((bytes(b[o + 0x10:o + 0x60]), bytes(b[o + 0xA0:o + 0x2A0]), offs, name))
    taken = set(names)
    for t in new:
        if t.name in taken:
            raise PatchError(f"the texture file has a texture {t.name} already")
        taken.add(t.name)
        if t.fmt not in BLOCK:
            raise ValueError(f"texture format {t.fmt:#x}")
    data_start = brtd + 0x10
    old_data = b[data_start:hi_data] if count else b""
    # the order of the strings as stored (kept, so the pool reads the same), then the new names
    order = _str_order(b)
    images = []
    for t in new:
        side, bpb = BLOCK[t.fmt]
        bx, by = -(-t.w // side), -(-t.h // side)
        if len(t.data) != bx * by * bpb:
            raise ValueError(f"texture {t.name}: {len(t.data)} bytes, expected {bx * by * bpb}")
        bh = tegra.game_block_height_log2(by)
        img = tegra.tile_texture([t.data], t.fmt, t.w, t.h, bh)
        img += bytes(-len(img) % 0x200)
        images.append((img, bh))

    n = count + len(new)
    all_names = names + [t.name for t in new]
    ptrs: list[tuple[int, int]] = []
    info_new = 0x58 + 0x140
    str_off = _al(info_new + 8 * n, 8)
    body, soffs = strpool.nx_pool([x for x in order if x in set(all_names) | {bntx_name}]
                                  + [x for x in all_names + [bntx_name] if x not in order])
    str_end = str_off + 0x10 + len(body)
    dict_new = _al(str_end, 8)
    brti_off = []
    pos = dict_new + dicts.nx_dict_size(n)
    mip_counts = [len(x[2]) for x in olds] + [1] * len(new)
    for k in range(n):
        pos = _al(pos, 8)
        brti_off.append(pos)
        pos += 0x2A0 + 8 * mip_counts[k]
    sec0_end = pos
    brtd_new = _al(pos + 0x10, 0x1000) - 0x10
    data_new = brtd_new + 0x10
    new_at = _al(data_new + len(old_data), 0x200)
    total = new_at + sum(len(i) for i, _ in images)
    rlt_off = _al(total, 0x1000)
    buf = bytearray(rlt_off)

    def s(key: str) -> int:
        return str_off + 0x10 + soffs[key]

    buf[0:0x20] = b[0:0x20]
    struct.pack_into("<I", buf, 0x10, s(bntx_name) + 2)
    struct.pack_into("<H", buf, 0x16, str_off)
    struct.pack_into("<I", buf, 0x18, rlt_off)
    buf[0x20:0x58] = b[0x20:0x58]
    struct.pack_into("<IQQQQ", buf, 0x24, n, info_new, brtd_new, dict_new, 0x58)
    ptrs += [(0x28, info_new), (0x30, brtd_new), (0x38, dict_new), (0x40, 0x58)]
    if pool != 0x58:
        raise PatchError("texture file with its memory pool info elsewhere")
    buf[0x58:0x58 + 0x140] = b[0x58:0x58 + 0x140]
    for i, o in enumerate(brti_off):
        struct.pack_into("<Q", buf, info_new + 8 * i, o)
        ptrs.append((info_new + 8 * i, o))
    first = brti_off[0] if n else brtd_new
    struct.pack_into("<4sIII", buf, str_off, b"_STR", first - str_off, first - str_off, 0)
    buf[str_off + 0x10:str_end] = body
    key_ptrs = [s(x) for x in all_names]
    buf[dict_new:dict_new + dicts.nx_dict_size(n)] = dicts.nx_dict(b"_DIC", all_names, key_ptrs, s(""))
    ptrs.append((dict_new + 0x10, s("")))
    ptrs += [(dict_new + 8 + 16 * (i + 1) + 8, p) for i, p in enumerate(key_ptrs)]
    shift = data_new - data_start
    cur = new_at
    for k in range(n):
        o = brti_off[k]
        nxt = (brti_off[k + 1] if k + 1 < n else brtd_new) - o
        struct.pack_into("<4sIII", buf, o, b"BRTI", nxt, nxt, 0)
        mip_arr = o + 0x2A0
        if k < count:
            fixed, runtime, offs, name = olds[k]
            buf[o + 0x10:o + 0x60] = fixed
            buf[o + 0xA0:o + 0x2A0] = runtime
            mips = [x + shift for x in offs]
        else:
            t = new[k - count]
            img, bh = images[k - count]
            name = t.name
            struct.pack_into("<BBHHH", buf, o + 0x10, 1, 2, 0, 0, 1)
            struct.pack_into("<IIIiiiiii", buf, o + 0x18, 1, t.fmt, 0x20, t.w, t.h, 1, 1, bh, 0x00010007)
            struct.pack_into("<II4BB", buf, o + 0x50, len(img), 0x200, *t.channels, 1)
            mips = [cur]
            buf[cur:cur + len(img)] = img
            cur += len(img)
        struct.pack_into("<8Q", buf, o + 0x60, s(name), 0x20, mip_arr, 0, o + 0xA0, o + 0x1A0, 0, 0)
        ptrs += [(o + 0x60, s(name)), (o + 0x68, 0x20), (o + 0x70, mip_arr), (o + 0x80, o + 0xA0),
                 (o + 0x88, o + 0x1A0)]
        for m, v in enumerate(mips):
            struct.pack_into("<Q", buf, mip_arr + 8 * m, v)
            ptrs.append((mip_arr + 8 * m, v))
    buf[data_new:data_new + len(old_data)] = old_data
    struct.pack_into("<4sIII", buf, brtd_new, b"BRTD", 0, rlt_off - brtd_new, 0)
    buf += rlt.build(rlt_off, [(0, sec0_end), (brtd_new, rlt_off - brtd_new)], ptrs)
    struct.pack_into("<I", buf, 0x1C, len(buf))
    runs = [(data_start, data_new, len(old_data))] if old_data else []
    return bytes(buf), runs


def _al(n: int, a: int) -> int:
    return -(-n // a) * a


def _str_order(b: bytes) -> list[str]:
    st = struct.unpack_from("<H", b, 0x16)[0]
    if bytes(b[st:st + 4]) != b"_STR":
        raise PatchError("texture file without its name list")
    count = struct.unpack_from("<I", b, st + 0x10)[0]
    o, out = st + 0x14, []
    for _ in range(count + 1):
        x = strpool.nx_string(b, o, "string")
        n = len(x.encode("utf-8"))
        if out or x:
            out.append(x)
        o += 2 + n + 1
        o += o % 2
    return out


# ================================================================================================ SARC in place

def patch_archive(data: bytes, changes: dict[str, bytes]) -> tuple[bytes, list[tuple[int, int, int]], dict]:
    """(new SARC, runs [(old offset, new offset, length)] of bytes that only moved, {name: (new start, end)}) with
    the named files' bytes replaced. Names, order and the name table stay; a file after a changed one moves by a
    multiple of 0x2000. Every other file keeps its bytes (and its alignment)."""
    b = bytes(data)
    try:
        s = wc.sarc_parse(b)
    except wc.FormatError as e:
        raise PatchError(f"archive: {e}") from None
    if s.order != wc.LE:
        raise PatchError("archive isn't a Switch (little-endian) one")
    names = [nd.name for nd in s.nodes]
    missing = [n for n in changes if n not in names]
    if missing:
        raise PatchError(f"archive has no {missing[0]}")
    doff = s.data_offset
    nodes = sorted(enumerate(s.nodes), key=lambda x: (x[1].start, x[0]))
    out = bytearray(b[:doff])
    runs: list[tuple[int, int, int]] = []
    if s.sfnt:   # (the name table: same bytes, same place)
        runs.append((s.sfnt, s.sfnt, doff - s.sfnt))
    new_pos: dict[int, tuple[int, int]] = {}
    shift = 0
    run: list | None = None   # [old offset, new offset] of the stretch of unchanged files being copied
    for k, (i, nd) in enumerate(nodes):
        old_a, old_b = doff + nd.start, doff + nd.end
        nxt = doff + nodes[k + 1][1].start if k + 1 < len(nodes) else old_b
        a = old_a + shift
        if len(out) > a:
            raise PatchError("archive files overlap")
        out += bytes(a - len(out))
        if nd.name in changes:
            if run is not None:
                runs.append((run[0], run[1], old_a - run[0]))
                run = None
            new = changes[nd.name]
            out += new
            while nxt + shift < a + len(new):
                shift += SHIFT
            new_pos[i] = (a - doff, a - doff + len(new))
        else:
            if run is None:
                run = [old_a, a]
            out += b[old_a:max(nxt, old_b)]
            new_pos[i] = (a - doff, a - doff + (old_b - old_a))
    if run is not None:
        runs.append((run[0], run[1], len(out) - run[1]))
    for i, (a, e) in new_pos.items():
        struct.pack_into("<II", out, s.sfat + 0xC + 16 * i + 8, a, e)
    struct.pack_into("<I", out, 8, len(out))
    starts = {s.nodes[i].name: v for i, v in new_pos.items()}
    return bytes(out), [r for r in runs if r[2] > 0], starts


def archive_check(old: bytes, new: bytes, changed: set[str]) -> list[str]:
    """Names of files that differ between two SARCs other than `changed` (and missing or added ones)."""
    a = dict(wc.sarc_files(old))
    bb = dict(wc.sarc_files(new))
    bad = sorted(set(a) ^ set(bb))
    bad += [n for n in a if n in bb and n not in changed and a[n] != bb[n]]
    return bad


# ================================================================================================ Yaz0

def yaz0_index(comp: bytes) -> tuple[bytes, int, array, array]:
    """(decompressed bytes, header alignment, compressed offset of each group, output offset of each group)."""
    if len(comp) < 16 or comp[:4] != b"Yaz0":
        raise PatchError("not Yaz0")
    size, align = struct.unpack_from(">II", comp, 4)
    out = bytearray()
    src = comp
    i = 16
    gsrc, gout = array("Q"), array("Q")
    gs, go = gsrc.append, gout.append
    try:
        while len(out) < size:
            gs(i)
            go(len(out))
            code = src[i]
            i += 1
            if code == 0xFF and size - len(out) >= 8:
                out += src[i:i + 8]
                i += 8
                continue
            for bit in (0x80, 0x40, 0x20, 0x10, 8, 4, 2, 1):
                if code & bit:
                    out.append(src[i])
                    i += 1
                else:
                    b1, b2 = src[i], src[i + 1]
                    i += 2
                    dist = ((b1 & 0x0F) << 8 | b2) + 1
                    n = b1 >> 4
                    if n:
                        n += 2
                    else:
                        n = src[i] + 0x12
                        i += 1
                    st = len(out) - dist
                    if st < 0:
                        raise PatchError("Yaz0 data refers to bytes before its start")
                    if dist >= n:
                        out += out[st:st + n]
                    else:
                        out += (bytes(out[st:]) * (n // dist + 1))[:n]
                if len(out) >= size:
                    break
    except IndexError:
        raise PatchError("Yaz0 data is cut short") from None
    del out[size:]
    return bytes(out), align, gsrc, gout


def _tokens(data: bytes, i: int, end: int) -> list:
    """Greedy LZ tokens for data[i:end] (matches may reach back before i): ints are literals, tuples (len, dist)."""
    find = data.rfind
    toks = []
    ap = toks.append
    while i < end:
        best = where = 0
        if i and end - i >= 3:
            lo = i - YAZ0_WINDOW if i > YAZ0_WINDOW else 0
            pos = find(data[i:i + 3], lo, i + 2)
            if pos >= 0:
                top = min(YAZ0_MAX, end - i)
                good, bad, where, probe = 3, top + 1, pos, 4
                while good < top:
                    sz = min(probe, top)
                    pos = find(data[i:i + sz], lo, i - 1 + sz)
                    if pos < 0:
                        bad = sz
                        break
                    good, where = sz, pos
                    probe *= 2
                while bad - good > 1:
                    mid = (good + bad) // 2
                    pos = find(data[i:i + mid], lo, i - 1 + mid)
                    if pos >= 0:
                        good, where = mid, pos
                    else:
                        bad = mid
                best = good
        if best >= 3:
            ap((best, i - where))
            i += best
        else:
            ap(data[i])
            i += 1
    return toks


def _fill_to_groups(toks: list) -> bool:
    """Make the token count a multiple of 8 by splitting matches into a shorter match plus literals (same bytes).
    False when it can't (too few long matches)."""
    need = -len(toks) % 8
    k = len(toks) - 1
    while need and k >= 0:
        t = toks[k]
        if isinstance(t, tuple) and t[0] > 3:
            ln, dist = t
            take = min(need, ln - 3)
            toks[k:k + 1] = [(ln - take, dist)] + [("lit",)] * take   # (literals: the bytes at their place)
            need -= take
        k -= 1
    return need == 0


def _emit(toks: list, data: bytes, start: int, out: bytearray) -> None:
    """Write tokens (for data from `start`) as Yaz0 groups."""
    pos = start
    for g in range(0, len(toks), 8):
        flag_at = len(out)
        out.append(0)
        flags = 0
        for bit, t in zip((0x80, 0x40, 0x20, 0x10, 8, 4, 2, 1), toks[g:g + 8]):
            if isinstance(t, int):
                flags |= bit
                out.append(t)
                pos += 1
            elif t[0] == "lit":
                flags |= bit
                out.append(data[pos])
                pos += 1
            else:
                ln, dist = t
                d = dist - 1
                if ln >= 0x12:
                    out += bytes((d >> 8, d & 0xFF, ln - 0x12))
                else:
                    out += bytes((((ln - 2) << 4) | (d >> 8), d & 0xFF))
                pos += ln
        out[flag_at] = flags


def yaz0_splice(comp: bytes, index: tuple, new: bytes, runs: list[tuple[int, int, int]], align: int) -> bytes:
    """Yaz0 of `new`, reusing the groups of the old stream `comp` (`index` = yaz0_index's group offsets) over the runs
    (old offset, new offset, length) where `new` holds the old bytes moved."""
    gsrc, gout = index
    out = bytearray(b"Yaz0" + struct.pack(">II", len(new), align) + bytes(4))
    pos = 0
    ngroups = len(gout)
    for old_a, new_a, ln in sorted(runs, key=lambda r: r[1]):
        if new_a < pos or ln < 2 * YAZ0_WINDOW:
            continue
        g = bisect_left(gout, old_a + YAZ0_WINDOW)
        h = bisect_right(gout, old_a + ln) - 1
        h = min(h, ngroups - 1)          # (never the last group: it may be cut short)
        done = False
        while g < h:
            target = new_a + gout[g] - old_a
            toks = _tokens(new, pos, target)
            if _fill_to_groups(toks):
                _emit(toks, new, pos, out)
                out += comp[gsrc[g]:gsrc[h]]
                pos = new_a + gout[h] - old_a
                done = True
                break
            g += 1
        if not done:
            continue
    toks = _tokens(new, pos, len(new))
    _emit(toks, new, pos, out)
    return bytes(out)


# ================================================================================================ Bootup.pack

@dataclass
class Rebuilt:
    pack: bytes             # the new Pack/Bootup.pack
    common_size: int        # Layout/Common.blarc unpacked (its size-table entry must hold it)
    old_common_size: int


def open_common(pack: bytes) -> tuple[bytes, int, tuple, bytes]:
    """(Common archive unpacked, its Yaz0 alignment, group index, the compressed entry) from a Bootup.pack."""
    try:
        files = dict(wc.sarc_files(pack))
    except wc.FormatError as e:
        raise PatchError(f"Pack/Bootup.pack: {e}") from None
    comp = files.get(COMMON)
    if comp is None:
        raise PatchError(f"Pack/Bootup.pack has no {COMMON}")
    comp = bytes(comp)
    if comp[:4] == b"Yaz0":
        raw, align, gs, go = yaz0_index(comp)
        return raw, align, (gs, go), comp
    return comp, -1, (array("Q"), array("Q")), comp


def rebuild_bootup(pack: bytes, patch) -> Rebuilt:
    """Bootup.pack with Common.sblarc changed by `patch(archive) -> (new archive, runs)`. Checks rule S1: every
    other entry of the pack has the same bytes as in `pack`, and the new archive decodes back exactly."""
    raw, align, index, comp = open_common(pack)
    new_raw, runs = patch(raw)
    if align >= 0:
        new_comp = yaz0_splice(comp, index, new_raw, runs, align)
        back, _ = wc.yaz0_decompress(new_comp)
        if back != new_raw:
            raise PatchError("self-check failed: the new layout archive doesn't decode back the same")
    else:
        new_comp = new_raw
    new_pack, _, _ = patch_archive(pack, {COMMON: new_comp})
    bad = archive_check(pack, new_pack, {COMMON})
    if bad:
        raise PatchError(f"self-check failed: {bad[0]} would change in Pack/Bootup.pack")
    if dict(wc.sarc_files(new_pack))[COMMON] != new_comp:
        raise PatchError("self-check failed: the layout archive isn't in the new pack as made")
    return Rebuilt(new_pack, len(new_raw), len(raw))


def patch_common(raw: bytes, layouts: dict[str, list[tuple]], textures: list[NewTexture]):
    """(new Common archive, runs): `layouts` = {"blyt/X.bflyt": [(name, tex, pos, size, after), ...]} (picture panes
    copied from the HUD rupee icon), `textures` added to timg/__Combined.bntx."""
    files = dict(wc.sarc_files(raw))
    src_layout = files.get("blyt/Rupee_00.bflyt")
    if src_layout is None or BNTX_NAME not in files:
        raise PatchError("the game's layout archive isn't one this tool knows (no Rupee_00 layout or no textures)")
    src_pic, src_mat = picture_source(bytes(src_layout), "P_Icon_00")
    changes: dict[str, bytes] = {}
    for lay, adds in layouts.items():
        d = files.get(lay)
        if d is None:
            raise PatchError(f"the game's layout archive has no {lay}")
        d = bytes(d)
        for name, tex, pos, size, after in adds:
            d = add_picture(d, name, src_pic, src_mat, tex, pos, size, visible=False, after=after)
        changes[lay] = d
    bntx_runs: list = []
    if textures:
        nb, bntx_runs = bntx_add_textures(bytes(files[BNTX_NAME]), textures)
        changes[BNTX_NAME] = nb
    new, runs, starts = patch_archive(raw, changes)
    if bntx_runs:
        s = wc.sarc_parse(raw)
        old_bntx = next(s.data_offset + nd.start for nd in s.nodes if nd.name == BNTX_NAME)
        new_bntx = s.data_offset + starts[BNTX_NAME][0]
        runs += [(old_bntx + a, new_bntx + b, ln) for a, b, ln in bntx_runs]
    for a, b, ln in runs:   # (the runs are only hints: each one is checked here)
        if raw[a:a + ln] != new[b:b + ln]:
            raise PatchError("internal: a moved stretch of the archive isn't the same bytes")
    return new, runs
