"""Switch BFRES 0.5.0.3 core (milestone M1): the texture container `X.Tex` / item icon, a BFRES with no models and one
external file `textures.bntx`. Layout from out/model-work/switch-format.md sections 2-3 (fixed in the game's files):

    000 header + ResFile (to 0xD0)   0D0 unreferenced buffer info (0x22, zeros)   0F0 external file array
    100 external file dict           128 _STR (strings `textures.bntx`, the file name)   1000 BNTX
    _RLT on the next 0x1000 boundary after the BNTX (5 sections: 0 = [0, end of strings), 4 = [0x1000, RLT))
"""
from __future__ import annotations

import struct

from . import dicts, nx_bntx, rlt, strpool
from .binio import Reader, align, put
from .errors import Damaged, Unsupported
from .neutral import TextureSet

MAGIC = b"FRES    "
VERSION = 0x00050003


def is_switch_bfres(data) -> bool:
    return bytes(data[:8]) == MAGIC and bytes(data[0x0C:0x0E]) == b"\xff\xfe"


def read_header(data) -> dict:
    r = Reader(data, "<", "Switch BFRES")
    if r.raw(0, 8) != MAGIC:
        raise Damaged("not a Switch BFRES (magic)")
    if r.u16(0x0C) != 0xFEFF:
        raise Unsupported("not a little-endian (Switch) BFRES")
    ver = r.u32(8)
    if ver != VERSION:
        raise Unsupported(f"Switch BFRES version {ver:#010x} (only 0.5.0.3)")
    name = strpool.nx_string(r.data, r.u32(0x10) - 2, "file name")
    counts = r.unpack("7H", 0xBC)
    ext_arr, ext_dict = r.u64(0x98), r.u64(0xA0)
    files = []
    if counts[6]:
        _, key_ptrs = dicts.read_nx_dict(r.data, ext_dict)
        for i in range(counts[6]):
            off, size = r.unpack("QI", ext_arr + 0x10 * i, "external file")
            files.append((strpool.nx_string(r.data, key_ptrs[i], "external file name"), off, size))
    return {"name": name, "counts": counts, "files": files, "align_log2": r.u8(0x0E)}


def _bntx_slice(data) -> tuple[dict, memoryview]:
    h = read_header(data)
    if any(h["counts"][:6]):
        raise Unsupported(f"{h['name']!r} holds models or animations; it is not a texture file")
    bntx = [(n, o, s) for n, o, s in h["files"] if nx_bntx.is_bntx(data[o:o + 8])]
    if len(bntx) != 1 or len(h["files"]) != 1:
        raise Unsupported(f"{h['name']!r}: expected one external file holding a BNTX, found "
                          f"{[n for n, _, _ in h['files']]}")
    _, off, size = bntx[0]
    if off + size > len(data):
        raise Damaged("textures.bntx runs past the end of the file")
    return h, memoryview(data)[off:off + size]


def swizzle_fields(data) -> dict[str, int]:
    """{texture name: BRTI swizzle field} of a Switch `X.Tex` (decompressed), without untiling anything."""
    return nx_bntx.swizzle_fields(_bntx_slice(data)[1])


def read_textures(data, only: set[str] | None = None, refused: list[str] | None = None) -> TextureSet:
    """TextureSet of a Switch `X.Tex` file or item icon (decompressed). `only` / `refused`: see nx_bntx.read (a
    partial, tolerant read, e.g. of the user's vanilla file when only a few textures are needed)."""
    if only is not None or refused is not None:
        h, bntx = _bntx_slice(data)
        name = h["name"]
        kind = "model" if name.endswith(".Tex") else "single"
        ts = nx_bntx.read(bntx, name[:-4] if kind == "model" else name, only, refused)
        ts.kind = kind
        return ts
    h = read_header(data)
    if any(h["counts"][:6]):
        raise Unsupported(f"{h['name']!r} holds models or animations; it is not a texture file")
    bntx = [(n, o, s) for n, o, s in h["files"] if nx_bntx.is_bntx(data[o:o + 8])]
    if len(bntx) != 1 or len(h["files"]) != 1:
        raise Unsupported(f"{h['name']!r}: expected one external file holding a BNTX, found "
                          f"{[n for n, _, _ in h['files']]}")
    fname, off, size = bntx[0]
    if off + size > len(data):
        raise Damaged("textures.bntx runs past the end of the file")
    name = h["name"]
    kind = "model" if name.endswith(".Tex") else "single"
    ts = nx_bntx.read(memoryview(data)[off:off + size], name[:-4] if kind == "model" else name)
    ts.kind = kind
    if fname != "textures.bntx":
        ts.extra["nx.bntx_file"] = fname
    return ts


def write_tex_container(name: str, bntx: bytes, file_name: str = "textures.bntx") -> bytes:
    """A BFRES named `name` (e.g. `X.Tex`) holding one external file (the BNTX)."""
    str_off = 0x128
    body, soffs = strpool.nx_pool([file_name, name])
    str_end = str_off + 0x10 + len(body)
    if str_end > 0x1000:
        raise ValueError("file name too long for the .Tex layout")
    bntx_off = 0x1000
    rlt_off = align(bntx_off + len(bntx), 0x1000)
    buf = bytearray(rlt_off)
    s = lambda key: str_off + 0x10 + soffs[key]  # noqa: E731
    ptrs = []
    buf[0:8] = MAGIC
    put(buf, "<IHBBIHHI", 8, VERSION, 0xFEFF, 12, 0, s(name) + 2, 0, str_off, rlt_off)
    put(buf, "<Q", 0x20, s(name))
    ptrs.append((0x20, s(name)))
    for k in range(6):  # model, skeletal, material, bone visibility, shape, scene anim dicts: null but relocated
        ptrs.append((0x30 + 0x10 * k, 0))
    put(buf, "<QQ", 0x98, 0xF0, 0x100)
    ptrs += [(0x98, 0xF0), (0xA0, 0x100)]
    put(buf, "<QI", 0xB0, str_off + 0x14, str_end - str_off)
    ptrs.append((0xB0, str_off + 0x14))
    put(buf, "<7H", 0xBC, 0, 0, 0, 0, 0, 0, 1)
    put(buf, "<I", 0xD0, 0x22)  # unreferenced buffer info, as in the game's .Tex files
    put(buf, "<QII", 0xF0, bntx_off, len(bntx), 0)
    ptrs.append((0xF0, bntx_off))
    buf[0x100:0x128] = dicts.nx_dict(b"\0\0\0\0", [file_name], [s(file_name)], s(""))
    ptrs += [(0x110, s("")), (0x120, s(file_name))]
    put(buf, "<4sIII", str_off, b"_STR", 0, rlt_off - str_off, 0)
    buf[str_off + 0x10:str_end] = body
    buf[bntx_off:bntx_off + len(bntx)] = bntx
    secs = [(0, str_end), (str_end, 0), (str_end, 0), (str_end, 0), (bntx_off, rlt_off - bntx_off)]
    buf += rlt.build(rlt_off, secs, ptrs)
    struct.pack_into("<I", buf, 0x1C, len(buf))
    return bytes(buf)


def write_textures(ts: TextureSet) -> bytes:
    """Switch `X.Tex` file (kind 'model', FRES name `X.Tex`) or item icon (kind 'single', FRES name = set name)."""
    name = ts.name + ".Tex" if ts.kind == "model" else ts.name
    return write_tex_container(name, nx_bntx.write(ts), ts.extra.get("nx.bntx_file", "textures.bntx"))
