"""Self-checks of written files (cheap; run after every write in tests and verify).

Switch `X.Tex`: every pointer the structure has is in the relocation table and every relocated pointer is one of
them, each target lies in its section, every dictionary key is found, the size fields are right, and the file
re-reads. Wii U texture files: wiiu_bfres.self_check.
"""
from __future__ import annotations

import struct

from . import dicts, nx_bfres, nx_bntx, nx_model, rlt, strpool
from .errors import ModelError


def _rlt_problems(data, base: int, rlt_off: int, expected: set[int], what: str) -> list[str]:
    out = []
    secs, per = rlt.parse(data, rlt_off, base)
    seen = set()
    for (so, ss), positions in zip(secs, per):
        for p in positions:
            tgt = struct.unpack_from("<Q", data, base + p)[0]
            if not so <= tgt < so + ss:
                out.append(f"{what}: pointer at {p:#x} -> {tgt:#x} is outside its section {so:#x}+{ss:#x}")
            seen.add(p)
    if seen != expected:
        missing, extra = sorted(expected - seen), sorted(seen - expected)
        out.append(f"{what}: pointers not relocated {[hex(x) for x in missing[:5]]}, relocated non-pointers "
                   f"{[hex(x) for x in extra[:5]]}")
    return out


def _dict_problems(data, base: int, off: int, keys: list[str], what: str) -> list[str]:
    nodes, ptrs = dicts.read_nx_dict(memoryview(data)[base:], off)
    names = [strpool.nx_string(memoryview(data)[base:], p) for p in ptrs]
    if names != keys:
        return [f"{what}: dictionary keys {names[:3]} != {keys[:3]}"]
    enc = [k.encode("utf-8") for k in keys]
    bad = [k for i, k in enumerate(enc) if dicts.lookup(nodes, enc, k) != i]
    return [f"{what}: dictionary lookup fails for {bad[:3]}"] if bad else []


def bntx_problems(data, base: int) -> list[str]:
    d = memoryview(data)[base:]
    out = []
    size = struct.unpack_from("<I", d, 0x1C)[0]
    rlt_off = struct.unpack_from("<I", d, 0x18)[0]
    n = struct.unpack_from("<I", d, 0x24)[0]
    info_arr, brtd, dict_off, pool = struct.unpack_from("<QQQQ", d, 0x28)
    exp = {0x28, 0x30, 0x38, 0x40, dict_off + 0x10}
    names = []
    for i in range(n):
        exp.add(info_arr + 8 * i)
        exp.add(dict_off + 8 + 16 * (i + 1) + 8)
        o = struct.unpack_from("<Q", d, info_arr + 8 * i)[0]
        exp.update({o + 0x60, o + 0x68, o + 0x70, o + 0x80, o + 0x88})
        mips = struct.unpack_from("<H", d, o + 0x16)[0]
        mip_arr = struct.unpack_from("<Q", d, o + 0x70)[0]
        exp.update(mip_arr + 8 * m for m in range(mips))
        names.append(strpool.nx_string(d, struct.unpack_from("<Q", d, o + 0x60)[0]))
    out += _rlt_problems(data, base, rlt_off, exp, "BNTX")
    out += _dict_problems(data, base, dict_off, names, "BNTX texture dict")
    nsec = struct.unpack_from("<I", d, rlt_off + 8)[0]
    nent = sum(struct.unpack_from("<i", d, rlt_off + 0x10 + 0x18 * i + 0x14)[0] for i in range(nsec))
    if size != rlt_off + rlt.size(nsec, nent):
        out.append(f"BNTX size field {size:#x} does not end at the relocation table end")
    if bytes(d[brtd:brtd + 4]) != b"BRTD" or (brtd + 0x10) % 0x1000:
        out.append("BRTD block missing or texture data not 0x1000-aligned")
    return out


def switch_tex_problems(data) -> list[str]:
    """Problems of a Switch `X.Tex` / item icon file (empty list = passes)."""
    out = []
    try:
        h = nx_bfres.read_header(data)
        size = struct.unpack_from("<I", data, 0x1C)[0]
        if size != len(data):
            out.append(f"FRES size field {size:#x} != file size {len(data):#x}")
        rlt_off = struct.unpack_from("<I", data, 0x18)[0]
        exp = {0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x98, 0xA0, 0xB0}
        ext_dict = struct.unpack_from("<Q", data, 0xA0)[0]
        exp.add(ext_dict + 0x10)
        for i in range(len(h["files"])):
            exp.add(0xF0 + 0x10 * i)
            exp.add(ext_dict + 8 + 16 * (i + 1) + 8)
        out += _rlt_problems(data, 0, rlt_off, exp, "FRES")
        out += _dict_problems(data, 0, ext_dict, [n for n, _, _ in h["files"]], "external file dict")
        for _, off, fsize in h["files"]:
            if nx_bntx.is_bntx(data[off:off + 8]):
                bsize = struct.unpack_from("<I", data, off + 0x1C)[0]
                if bsize != fsize:
                    out.append(f"BNTX size {bsize:#x} != external file size {fsize:#x}")
                out += bntx_problems(data, off)
        nx_bfres.read_textures(data)
    except (ModelError, struct.error, ValueError) as e:
        out.append(f"{type(e).__name__}: {e}")
    return out


def switch_model_problems(data) -> list[str]:
    """Problems of a Switch model / animation BFRES (empty list = passes): the file re-reads; every non-null pointer
    field the reader followed is in the relocation table and every relocated position is a pointer field; each
    relocated pointer targets its own section; every dictionary key is found by the trie lookup; the size fields are
    right; and writing the re-read file gives the same bytes (the writer is a fixed point)."""
    out = []
    try:
        res, fields, dlist = nx_model.read_checked(data)
        size = struct.unpack_from("<I", data, 0x1C)[0]
        if size != len(data):
            out.append(f"FRES size field {size:#x} != file size {len(data):#x}")
        rlt_off = struct.unpack_from("<I", data, 0x18)[0]
        secs, per = rlt.parse(data, rlt_off)
        relocated = set()
        for (so, ss), positions in zip(secs, per):
            for p in positions:
                tgt = struct.unpack_from("<Q", data, p)[0]
                if tgt and not so <= tgt < so + ss:
                    out.append(f"pointer at {p:#x} -> {tgt:#x} is outside its section {so:#x}+{ss:#x}")
                relocated.add(p)
        nonnull = {p for p in fields if struct.unpack_from("<Q", data, p)[0]}
        missing = nonnull - relocated
        if missing:
            out.append(f"pointers not relocated: {[hex(x) for x in sorted(missing)[:5]]}")
        stray = relocated - fields
        if stray:
            out.append(f"relocated positions that are no pointer field: {[hex(x) for x in sorted(stray)[:5]]}")
        nsec = struct.unpack_from("<I", data, rlt_off + 8)[0]
        nent = sum(struct.unpack_from("<i", data, rlt_off + 0x10 + 0x18 * i + 0x14)[0] for i in range(nsec))
        if len(data) != rlt_off + rlt.size(nsec, nent):
            out.append("the relocation table is not the end of the file")
        for nodes, keys in dlist:
            enc = [k.encode("utf-8") for k in keys]
            bad = [k for i, k in enumerate(enc) if dicts.lookup(nodes, enc, k) != i]
            if bad:
                out.append(f"dictionary lookup fails for {bad[:3]}")
        if nx_model.write(res) != bytes(data):
            out.append("writing the re-read file gives different bytes")
    except (ModelError, struct.error, ValueError) as e:
        out.append(f"{type(e).__name__}: {e}")
    return out


def _snorm10(x: int) -> float:
    return max((x - 1024 if x & 0x200 else x) / 511, -1.0)


def model_stats(res) -> dict:
    """Decoded checks of the geometry of a neutral ResFile (what a wrong byte order or layout breaks): normals
    (`_n*` R10G10B10A2 snorm) of unit length within 1%, indices below the vertex count, bone indices (`_i*` uint, the
    first `skin count` of them) below the skeleton's matrix count, weights (`_w0` unorm, up to 4 influences) summing
    to 255. Returns counts and the first few failures."""
    st = {"normals": 0, "bad_normals": 0, "indices": 0, "bad_indices": 0, "bone_refs": 0, "bad_bone_refs": 0,
          "weights": 0, "bad_weights": 0, "problems": []}

    def problem(msg: str) -> None:
        if len(st["problems"]) < 5:
            st["problems"].append(msg)

    for m in res.models:
        nmtx = m.skeleton.smooth_count + m.skeleton.rigid_count
        for vi, vb in enumerate(m.vertex_buffers):
            n = vb.vertex_count
            for a in vb.attributes:
                stride, data = vb.strides[a.buffer], vb.buffers[a.buffer]
                if a.name.startswith("_n") and a.format == 0x0E02:
                    bad = 0
                    for v in range(n):
                        w = struct.unpack_from("<I", data, stride * v + a.offset)[0]
                        x, y, z = _snorm10(w & 0x3FF), _snorm10((w >> 10) & 0x3FF), _snorm10((w >> 20) & 0x3FF)
                        if abs((x * x + y * y + z * z) ** 0.5 - 1) > 0.01:
                            bad += 1
                    st["normals"] += n
                    st["bad_normals"] += bad
                    if bad:
                        problem(f"{m.name} vertex buffer {vi}: {bad} of {n} normals not unit length")
                elif a.name.startswith("_i") and (a.format & 0xFF) == 3 and vb.skin_count:
                    k = {0x02: 1, 0x09: 2, 0x0B: 4}.get(a.format >> 8, 0)
                    first = 4 * int(a.name[2:]) if a.name[2:].isdigit() else 0
                    use = max(0, min(k, vb.skin_count - first))
                    bad = 0
                    for v in range(n):
                        o = stride * v + a.offset
                        bad += sum(1 for x in data[o:o + use] if x >= nmtx)
                    st["bone_refs"] += n * use
                    st["bad_bone_refs"] += bad
                    if bad:
                        problem(f"{m.name} vertex buffer {vi}: {bad} bone indices >= {nmtx} matrices")
                elif a.name == "_w0" and a.format in (0x0B01, 0x0901) and 0 < vb.skin_count <= 4:
                    use = min(4 if a.format == 0x0B01 else 2, vb.skin_count)
                    bad = 0
                    for v in range(n):
                        o = stride * v + a.offset
                        if sum(data[o:o + use]) != 255:
                            bad += 1
                    st["weights"] += n
                    st["bad_weights"] += bad
                    if bad:
                        problem(f"{m.name} vertex buffer {vi}: {bad} of {n} weight sets do not sum to 255")
        for sh in m.shapes:
            count = m.vertex_buffers[sh.vertex_buffer].vertex_count
            for mesh in sh.meshes:
                tc = {"u8": "B", "u16": "H", "u32": "I"}[mesh.index_format]
                idx = struct.unpack(f"<{mesh.index_count}{tc}", mesh.indices) if mesh.index_count else ()
                st["indices"] += len(idx)
                bad = sum(1 for x in idx if x + mesh.first_vertex >= count)
                st["bad_indices"] += bad
                if bad:
                    problem(f"{m.name} shape {sh.name}: {bad} indices >= {count} vertices")
    return st
