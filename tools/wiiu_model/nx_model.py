"""Switch BFRES 0.5.0.3 model files (milestone M2): FMDL, FSKL, FVTX, FSHP, FMAT, user data, external files, and the
embedded FSKA / FMAA animations, read into the neutral model and written back.

Layouts from out/model-work/switch-format.md sections 2-5 (read from the user's own files). Every pointer is a u64
offset from the file start listed in the relocation table (`_RLT`); strings are `_STR` entries (a pointer points at the
u16 length). Vertex and index data live in one GPU pool (all index buffers, then all vertex buffers, each 8-aligned).

The writer (`write`) places the structures in the order the game's files have them and builds the string pool,
dictionaries and relocation table itself. It rebuilt all 34 model and animation files of the V1 run byte for byte
(verify_models.py). The order, every structure 8-aligned unless noted:

    header + ResFile; FMDL array; FSKA array; FMAA array; buffer info (0x20, present even without a GPU pool);
    external file array; dictionaries (models, skeletal anims, material anims, external files)
    per model: FVTX array, FMAT array, FSHP array, user data array, FSKL, bones, bone user data arrays, matrix list,
        inverse matrices, dictionaries (bones, shapes, materials, user data), bone user data dicts and values;
        per shape: meshes, skin bones, bounds, radii (4-aligned), per mesh: submeshes, run-time buffer, index buffer
        info (0x30); per FVTX: attributes, run-time buffers, buffer info, strides (+0x20), user buffer pointers,
        attribute dictionary; the model's user data values; per material: render infos, their string arrays, then
        their numbers (4-aligned), run-time textures, sampler infos, run-time samplers, params, param data, volatile
        flags, user data array, sampler slots, texture slots, texture names, shader assign, its value arrays, its
        dictionaries, then the render info / sampler / param / user data dictionaries and the user data values
    per FSKA: bind indices, bone anims, user data array, per bone anim: base data, curves, per curve frames and keys;
        user data dictionary and values
    per FMAA: bind indices, material anim data, texture slots, run-time textures, texture names, per material:
        param infos, pattern infos, constants, curves (frames, keys)
    _STR; GPU pool at a 0x1000 boundary (all indices, then all vertex buffers); memory pool object (0x120);
    external files (BNTX 0x1000-aligned, others 0x100); _RLT (aligned like the last external file, else 8)

Empty arrays: a count of 0 usually points at the next structure (and is relocated); exceptions the game's files show
are kept as rules in the writer (all four model arrays empty -> null; a material without textures -> null texture
arrays and slots). FMAA user data is refused: where its count lives is not known.
"""
from __future__ import annotations

import re
import struct

from . import dicts, rlt, strpool
from .binio import Reader, align
from .errors import Damaged, Unsupported
from .formats import USER_DATA_CODE, USER_DATA_KINDS
from .neutral import (AnimCurve, Attribute, Bone, BoneAnim, ExtFile, MatAnim, MatAnimData, Material, Mesh, Model,
                      Param, RenderInfo, ResFile, Sampler, SamplerState, Shape, ShaderAssign, SkelAnim, Skeleton,
                      UserData, VertexBuffer)

MAGIC = b"FRES    "
VERSION = 0x00050003
PRIMITIVES = {0: "points", 1: "lines", 2: "line_strip", 3: "triangles", 4: "triangle_strip"}
PRIMITIVE_CODE = {v: k for k, v in PRIMITIVES.items()}
INDEX_FORMATS = {0: "u8", 1: "u16", 2: "u32"}
INDEX_CODE = {v: k for k, v in INDEX_FORMATS.items()}
INDEX_SIZE = {"u8": 1, "u16": 2, "u32": 4}
RENDER_INFO_KINDS = {0: "int", 1: "float", 2: "string"}
RENDER_INFO_CODE = {v: k for k, v in RENDER_INFO_KINDS.items()}

FMDL_SIZE, FSKL_SIZE, BONE_SIZE, FVTX_SIZE, ATTR_SIZE = 0x78, 0x48, 0x50, 0x60, 0x10
FSHP_SIZE, MESH_SIZE, FMAT_SIZE, RINFO_SIZE, SAMPLER_SIZE = 0x70, 0x38, 0xB8, 0x18, 0x20
PARAM_SIZE, ASSIGN_SIZE, USER_DATA_SIZE = 0x20, 0x48, 0x40
FSKA_SIZE, BONE_ANIM_SIZE, CURVE_SIZE = 0x60, 0x28, 0x30
FMAA_SIZE, MAT_ANIM_SIZE, PARAM_INFO_SIZE, PATTERN_INFO_SIZE = 0x78, 0x40, 0x18, 0x10
RT_BUFFER, RT_SAMPLER, MEMORY_POOL = 0x48, 0x78, 0x120
POOL_PROPERTY = 0x22


def is_switch_bfres(data) -> bool:
    return bytes(data[:8]) == MAGIC and bytes(data[0x0C:0x0E]) == b"\xff\xfe"


# --------------------------------------------------------------------------------------------------- curve sizes

_FRAME_SIZE = {0: 4, 1: 2, 2: 1}
_KEY_SIZE = {0: 4, 1: 2, 2: 1}
_KEY_ELEMS = {0: 4, 1: 2, 2: 1, 3: 1, 4: 1, 5: 1}  # cubic, linear, baked float, (3), step int, baked int


def curve_sizes(flags: int, keys: int) -> tuple[int, int]:
    """(frame array bytes, key array bytes) of an animation curve from its flags (frame type bits 0-1, key type bits
    2-3, curve type bits 4-6) and key count. Bool curves (types 6, 7) store one bit per key."""
    ftype, ktype, ctype = flags & 3, (flags >> 2) & 3, (flags >> 4) & 7
    if ftype not in _FRAME_SIZE or ktype not in _KEY_SIZE:
        raise Unsupported(f"animation curve flags {flags:#x} (frame/key type)")
    if ctype in (6, 7):
        return keys * _FRAME_SIZE[ftype], align(keys, 32) // 8
    if ctype not in _KEY_ELEMS:
        raise Unsupported(f"animation curve type {ctype}")
    return keys * _FRAME_SIZE[ftype], keys * _KEY_ELEMS[ctype] * _KEY_SIZE[ktype]


def base_data_size(flags: int) -> int:
    """Bytes of a bone animation's base data: scale (bit 3) 12, rotation (bit 4) 16, translation (bit 5) 12."""
    return (12 if flags & 0x08 else 0) + (16 if flags & 0x10 else 0) + (12 if flags & 0x20 else 0)


# --------------------------------------------------------------------------------------------------------- reader

_FMT_ITEM = re.compile(r"(\d*)([a-zA-Z])")


class _PtrReader(Reader):
    """Reader that records the position of every u64 field read (all pointer fields are u64; the self-check compares
    them with the relocation table)."""

    def __init__(self, data):
        super().__init__(data, "<", "Switch BFRES")
        self.fields: set[int] = set()

    def unpack(self, fmt: str, off: int, what: str = ""):
        if "Q" in fmt:
            pos = off
            for n, c in _FMT_ITEM.findall(fmt):
                n = int(n) if n else 1
                size = struct.calcsize("<" + c)
                if c == "Q":
                    self.fields.update(pos + 8 * k for k in range(n))
                pos += size * n
        return super().unpack(fmt, off, what)

    def u64(self, off: int, what: str = "") -> int:
        self.fields.add(off)
        return super().u64(off, what)


class _Read:
    def __init__(self, data, spans: list | None = None):
        self.r = _PtrReader(data)
        self.d = self.r.data
        self.spans = spans
        self.dicts: list[tuple[list, list[str]]] = []

    def span(self, off: int, size: int, label: str) -> None:
        if self.spans is not None and size:
            self.spans.append((off, size, label))

    def s(self, ptr: int, what: str = "string") -> str:
        return strpool.nx_string(self.d, ptr, what) if ptr else ""

    def sp(self, off: int, what: str = "string") -> str:
        return self.s(self.r.u64(off, what), what)

    def names(self, dict_off: int, count: int, what: str) -> list[str]:
        if not count:
            return []
        if not dict_off:
            raise Damaged(f"{what}: {count} entries but no dictionary")
        nodes, ptrs = dicts.read_nx_dict(self.d, dict_off)
        if len(ptrs) != count:
            raise Damaged(f"{what}: dictionary has {len(ptrs)} entries, the count says {count}")
        self.span(dict_off, dicts.nx_dict_size(count), f"dict {what}")
        self.r.fields.update(dict_off + 16 + 16 * i for i in range(count + 1))
        keys = [self.s(p, what) for p in ptrs]
        self.dicts.append((nodes, keys))
        return keys

    def check_names(self, got: list[str], dict_off: int, what: str) -> None:
        keys = self.names(dict_off, len(got), what)
        if keys != got:
            raise Damaged(f"{what}: dictionary keys {keys[:3]} do not match the array {got[:3]}")

    def user_data(self, arr: int, dict_off: int, count: int, what: str) -> list[UserData]:
        out = []
        for i in range(count):
            o = arr + USER_DATA_SIZE * i
            self.span(o, USER_DATA_SIZE, "user data")
            name = self.sp(o, "user data name")
            data, n, kind = self.r.u64(o + 8), self.r.u32(o + 0x10), self.r.u8(o + 0x14)
            if kind not in USER_DATA_KINDS or USER_DATA_KINDS[kind] == "wstring":
                raise Unsupported(f"{what}: user data {name!r} has type {kind} (type 3, a wide string in "
                                  f"BfresLibrary's numbering, is in no Switch file we checked)")
            k = USER_DATA_KINDS[kind]
            if k == "int":
                vals = self.r.unpack(f"{n}i", data, "user data") if n else ()
                self.span(data, 4 * n, "user data values")
            elif k == "float":
                vals = self.r.unpack(f"{n}f", data, "user data") if n else ()
                self.span(data, 4 * n, "user data values")
            elif k == "string":
                vals = tuple(self.sp(data + 8 * j, "user data string") for j in range(n))
                self.span(data, 8 * n, "user data values")
            else:
                vals = self.r.raw(data, n, "user data") if n else b""
                self.span(data, n, "user data values")
            ud = UserData(name, k, vals)
            if bytes(self.d[o + 0x15:o + USER_DATA_SIZE]).strip(b"\0"):
                ud.extra["nx.tail"] = bytes(self.d[o + 0x15:o + USER_DATA_SIZE])
            out.append(ud)
        self.check_names([u.name for u in out], dict_off, f"{what} user data")
        return out

    # ---------------------------------------------------------------- file

    def file(self) -> ResFile:
        r = self.r
        if r.raw(0, 8) != MAGIC:
            raise Damaged("not a Switch BFRES (magic)")
        if r.u16(0x0C) != 0xFEFF:
            raise Unsupported("not a little-endian (Switch) BFRES")
        ver = r.u32(8)
        if ver != VERSION:
            raise Unsupported(f"Switch BFRES version {ver:#010x} (only 0.5.0.3)")
        self.span(0, 0xD0, "FRES")
        r.unpack("18Q", 0x20)                               # every pointer field of the header (for the self-check)
        counts = r.unpack("7H", 0xBC)
        if any(counts[3:6]):
            raise Unsupported("bone visibility, shape or scene animations are not supported")
        res = ResFile(self.sp(0x20, "file name"), [])
        res.extra["nx.alignment_log2"] = r.u8(0x0E)
        if r.u64(0xA8):
            raise Unsupported("FRES user pointer is set")
        bufinfo = r.u64(0x90)
        self.pool = None
        if bufinfo:
            self.span(bufinfo, 0x20, "buffer info")
            prop, size, off = r.unpack("IIQ", bufinfo, "buffer info")
            if prop != POOL_PROPERTY:
                res.extra["nx.pool_property"] = prop
            self.pool = (off, size)
        mp = r.u64(0x88)
        if mp:
            self.span(mp, MEMORY_POOL, "memory pool")
        self.index_spans, self.vertex_spans = [], []
        res.models = [self.model(r.u64(0x28) + FMDL_SIZE * i, i) for i in range(counts[0])]
        self.check_names([m.name for m in res.models], r.u64(0x30), "model")
        res.skel_anims = [self.fska(r.u64(0x38) + FSKA_SIZE * i) for i in range(counts[1])]
        self.check_names([a.name for a in res.skel_anims], r.u64(0x40), "skeletal animation")
        res.mat_anims = [self.fmaa(r.u64(0x48) + FMAA_SIZE * i) for i in range(counts[2])]
        self.check_names([a.name for a in res.mat_anims], r.u64(0x50), "material animation")
        names = self.names(r.u64(0xA0), counts[6], "external file")
        for i, name in enumerate(names):
            o = r.u64(0x98) + 0x10 * i
            self.span(o, 0x10, "external file entry")
            off, size = r.unpack("QI", o, "external file")
            res.external.append(ExtFile(name, r.raw(off, size, f"external file {name!r}")))
            self.span(off, size, f"external file {name}")
        st = r.u64(0xB0) - 0x14
        if r.raw(st, 4, "_STR") != b"_STR":
            raise Damaged("string pool not found")
        res.extra["nx.string_order"] = self.string_order(st)
        self.span(st, r.u32(0xB8), "_STR")
        return res

    def string_order(self, st: int) -> list[str]:
        count = self.r.u32(st + 0x10)
        o, out = st + 0x14, []
        for i in range(count + 1):
            s = strpool.nx_string(self.d, o, "string")
            if i:
                out.append(s)
            o += 2 + len(s.encode("utf-8")) + 1
            o += o & 1
        return out

    # ---------------------------------------------------------------- model

    def model(self, o: int, idx: int) -> Model:
        r = self.r
        if r.raw(o, 4, "FMDL") != b"FMDL":
            raise Damaged(f"model {idx} at {o:#x} is not an FMDL")
        self.span(o, FMDL_SIZE, "FMDL")
        name, path = self.sp(o + 0x10, "model name"), self.sp(o + 0x18, "model path")
        r.unpack("10Q", o + 0x10)                           # every pointer field (for the self-check)
        nv, ns, nm, nu = r.unpack("4H", o + 0x68)
        total = r.u32(o + 0x70)
        if r.u64(o + 0x60):
            raise Unsupported(f"model {name!r}: user pointer is set")
        skel = self.skeleton(r.u64(o + 0x20), name)
        vbufs = [self.fvtx(r.u64(o + 0x28) + FVTX_SIZE * i, name) for i in range(nv)]
        mats = [self.fmat(r.u64(o + 0x40) + FMAT_SIZE * i, name) for i in range(nm)]
        shapes = [self.fshp(r.u64(o + 0x30) + FSHP_SIZE * i, name, vbufs) for i in range(ns)]
        self.check_names([s.name for s in shapes], r.u64(o + 0x38), f"model {name!r} shape")
        self.check_names([m.name for m in mats], r.u64(o + 0x48), f"model {name!r} material")
        ud_arr = r.u64(o + 0x50)
        ud = self.user_data(ud_arr, r.u64(o + 0x58), nu, f"model {name!r}")
        return Model(name, path, skel, vbufs, shapes, mats, ud, total)

    def skeleton(self, o: int, model: str) -> Skeleton:
        r = self.r
        if r.raw(o, 4, "FSKL") != b"FSKL":
            raise Damaged(f"model {model!r}: skeleton is not an FSKL")
        self.span(o, FSKL_SIZE, "FSKL")
        bdict, barr, mtob, inv = r.unpack("4Q", o + 0x10)
        if r.u64(o + 0x30):
            raise Unsupported(f"model {model!r}: skeleton user pointer is set")
        flags = r.u32(o + 0x38)
        nb, ns, nr = r.unpack("3H", o + 0x3C)
        bones = []
        for i in range(nb):
            b = barr + BONE_SIZE * i
            self.span(b, BONE_SIZE, "bone")
            name = self.sp(b, "bone name")
            ud_arr, ud_dict = r.u64(b + 8), r.u64(b + 0x10)
            index, parent, smooth, rigid, bb, nud = r.unpack("HhhhHH", b + 0x18)
            if index != i:
                raise Damaged(f"model {model!r}: bone {name!r} has index {index}, expected {i}")
            bflags = r.u32(b + 0x24)
            vals = r.unpack("10f", b + 0x28)
            bone = Bone(name, parent, smooth, rigid, bb, bflags, vals[0:3], vals[3:7], vals[7:10],
                        self.user_data(ud_arr, ud_dict, nud, f"bone {name!r}"))
            bones.append(bone)
        self.check_names([b.name for b in bones], bdict, f"model {model!r} bone")
        mlist = list(r.unpack(f"{ns + nr}H", mtob, "matrix list")) if ns + nr else []
        self.span(mtob, 2 * (ns + nr), "matrix to bone")
        invm = r.raw(inv, 48 * ns, "inverse matrices") if ns else b""
        self.span(inv, 48 * ns, "inverse matrices")
        sk = Skeleton(flags, bones, mlist, ns, nr, invm)
        if not mtob or not inv:
            sk.extra["nx.null_matrix_ptrs"] = (not mtob, not inv)
        return sk

    def pool_data(self, off: int, size: int, what: str) -> bytes:
        if self.pool is None:
            raise Damaged(f"{what}: the file has no GPU buffer pool")
        base, psize = self.pool
        if off + size > psize:
            raise Damaged(f"{what}: {off:#x}+{size:#x} is outside the GPU pool ({psize:#x})")
        return self.r.raw(base + off, size, what)

    def fvtx(self, o: int, model: str) -> VertexBuffer:
        r = self.r
        if r.raw(o, 4, "FVTX") != b"FVTX":
            raise Damaged(f"model {model!r}: vertex buffer at {o:#x} is not an FVTX")
        self.span(o, FVTX_SIZE, "FVTX")
        aarr, adict, _mp, rtb, ubuf, binfo, bstride = r.unpack("7Q", o + 0x10)
        if r.u64(o + 0x48):
            raise Unsupported(f"model {model!r}: vertex buffer user pointer is set")
        first = r.u32(o + 0x50)
        na, nb, _, nvtx, skin = r.unpack("BBHIB", o + 0x54)
        attrs = []
        for i in range(na):
            a = aarr + ATTR_SIZE * i
            self.span(a, ATTR_SIZE, "attribute")
            name = self.sp(a, "attribute name")
            fmt, off, buf = r.unpack("IHH", a + 8)
            attrs.append(Attribute(name, fmt, off, buf))
        self.check_names([a.name for a in attrs], adict, f"model {model!r} attribute")
        self.span(rtb, RT_BUFFER * nb, "runtime vertex buffers")
        self.span(binfo, 0x10 * nb, "vertex buffer info")
        self.span(bstride, 0x10 * nb, "vertex buffer strides")
        self.span(ubuf, 8 * nb, "user buffer pointers")
        strides, bufs, pos = [], [], first
        for k in range(nb):
            size, bflag = r.unpack("II", binfo + 0x10 * k)
            stride = r.u32(bstride + 0x10 * k)
            if bflag:
                raise Unsupported(f"model {model!r}: vertex buffer flags {bflag:#x}")
            if size != stride * nvtx:
                raise Damaged(f"model {model!r}: vertex buffer {k} has {size} bytes, stride {stride} x {nvtx}")
            pos = align(pos, 8)
            bufs.append(self.pool_data(pos, size, f"model {model!r} vertex buffer {k}"))
            self.vertex_spans.append((pos, size))
            pos += size
            strides.append(stride)
        return VertexBuffer(nvtx, skin, attrs, strides, bufs)

    def fshp(self, o: int, model: str, vbufs: list[VertexBuffer]) -> Shape:
        r = self.r
        if r.raw(o, 4, "FSHP") != b"FSHP":
            raise Damaged(f"model {model!r}: shape at {o:#x} is not an FSHP")
        self.span(o, FSHP_SIZE, "FSHP")
        name = self.sp(o + 0x10, "shape name")
        _vtx, marr, skarr, _ksarr, _ksdict, bbox, rad = r.unpack("7Q", o + 0x18)
        if r.u64(o + 0x50):
            raise Unsupported(f"shape {name!r}: user pointer is set")
        flags = r.u32(o + 0x58)
        _, mat, bone, vbi, nsk = r.unpack("5H", o + 0x5C)
        skin, nmesh, nks, nta = r.unpack("4B", o + 0x66)
        if nks:
            raise Unsupported(f"shape {name!r} has key shapes (morphs)")
        meshes = []
        for m in range(nmesh):
            mo = o_ = marr + MESH_SIZE * m
            self.span(mo, MESH_SIZE, "mesh")
            sub, _mp, rtb, binfo = r.unpack("4Q", o_)
            ioff, prim, ifmt, nidx, first, nsub = r.unpack("IIIIIH", mo + 0x20)
            if prim not in PRIMITIVES:
                raise Unsupported(f"shape {name!r}: primitive type {prim}")
            if ifmt not in INDEX_FORMATS:
                raise Unsupported(f"shape {name!r}: index format {ifmt}")
            fmt = INDEX_FORMATS[ifmt]
            size, bflag = r.unpack("II", binfo)
            if size != nidx * INDEX_SIZE[fmt] or bflag:
                raise Damaged(f"shape {name!r}: index buffer size {size} for {nidx} {fmt} indices")
            self.span(rtb, RT_BUFFER, "runtime index buffer")
            self.span(binfo, 0x10, "index buffer info")
            subs = [r.unpack("II", sub + 8 * s) for s in range(nsub)]
            self.span(sub, 8 * nsub, "submeshes")
            data = self.pool_data(ioff, size, f"shape {name!r} indices")
            self.index_spans.append((ioff, size))
            meshes.append(Mesh(PRIMITIVES[prim], fmt, nidx, data, first, subs))
        nbox = sum(len(m.submeshes) + 1 for m in meshes)
        bounds = [r.unpack("6f", bbox + 24 * k) for k in range(nbox)]
        self.span(bbox, 24 * nbox, "bounds")
        radii = list(r.unpack(f"{nmesh}f", rad)) if nmesh else []
        self.span(rad, 4 * nmesh, "radii")
        skin_bones = list(r.unpack(f"{nsk}H", skarr)) if nsk else []
        self.span(skarr, 2 * nsk, "skin bones")
        sh = Shape(name, flags, mat, bone, vbi, skin_bones, skin, nta, meshes, bounds, radii)
        if not nsk and not skarr:
            sh.extra["nx.null_skin_bones"] = True
        if vbi >= len(vbufs):
            raise Damaged(f"shape {name!r}: vertex buffer {vbi} of {len(vbufs)}")
        return sh

    def fmat(self, o: int, model: str) -> Material:
        r = self.r
        if r.raw(o, 4, "FMAT") != b"FMAT":
            raise Damaged(f"model {model!r}: material at {o:#x} is not an FMAT")
        self.span(o, FMAT_SIZE, "FMAT")
        name = self.sp(o + 0x10, "material name")
        (ria, rid, sa, rtt, tna, rts, sia, sid, spa, spd, spdata, uda, udd, volf, userp, sslot,
         tslot) = r.unpack("17Q", o + 0x18)
        if userp:
            raise Unsupported(f"material {name!r}: user pointer is set")
        flags, _, nri, nsamp, ntex, nparam, nvol, dsize, rawsize, nud = r.unpack("IHHBBHHHHH", o + 0xA0)
        infos = []
        for i in range(nri):
            a = ria + RINFO_SIZE * i
            self.span(a, RINFO_SIZE, "render info")
            rn = self.sp(a, "render info name")
            data, cnt, kind = r.unpack("QHB", a + 8)
            if kind not in RENDER_INFO_KINDS:
                raise Unsupported(f"material {name!r}: render info {rn!r} has type {kind}")
            k = RENDER_INFO_KINDS[kind]
            if k == "string":
                vals = tuple(self.sp(data + 8 * j, "render info value") for j in range(cnt))
                self.span(data, 8 * cnt, "render info values")
            else:
                vals = r.unpack(f"{cnt}{'i' if k == 'int' else 'f'}", data) if cnt else ()
                self.span(data, 4 * cnt, "render info values")
            infos.append(RenderInfo(rn, k, tuple(vals)))
        self.check_names([x.name for x in infos], rid, f"material {name!r} render info")
        shader = self.assign(sa, name)
        samplers = []
        for i in range(nsamp):
            a = sia + SAMPLER_SIZE * i
            self.span(a, SAMPLER_SIZE, "sampler info")
            wu, wv, ww, cmp_, border, aniso, filt = r.unpack("6BH", a)
            mn, mx, bias = r.unpack("3f", a + 8)
            st = SamplerState(wu, wv, ww, cmp_, border, aniso, filt, mn, mx, bias)
            tail = r.raw(a + 0x14, 12)
            if tail.strip(b"\0"):
                st.extra["nx.tail"] = tail
            samplers.append(Sampler("", st))
        snames = self.names(sid, nsamp, f"material {name!r} sampler")
        for s, n in zip(samplers, snames):
            s.name = n
        textures = [self.sp(tna + 8 * i, "texture name") for i in range(ntex)]
        self.span(tna, 8 * ntex, "texture names")
        self.span(rtt, 8 * ntex, "runtime textures")
        self.span(rts, RT_SAMPLER * nsamp, "runtime samplers")
        params = []
        for i in range(nparam):
            a = spa + PARAM_SIZE * i
            self.span(a, PARAM_SIZE, "shader param")
            cb = r.u64(a)
            pn = self.sp(a + 8, "param name")
            typ, size, off, uni, dep1, dep2, tail = r.unpack("BBHiHHI", a + 0x10)
            p = Param(pn, typ, off, size, dep1, dep2)
            if uni != -1:
                p.extra["uniform"] = uni
            if cb or tail:
                raise Unsupported(f"material {name!r}: shader param {pn!r} has a callback or unknown data")
            params.append(p)
        self.check_names([p.name for p in params], spd, f"material {name!r} param")
        pdata = r.raw(spdata, dsize, "param data") if dsize else b""
        self.span(spdata, dsize, "param data")
        vbytes = r.raw(volf, align(nparam, 64) // 8, "volatile flags") if volf else b""
        self.span(volf, len(vbytes), "volatile flags")
        volatile = [i for i in range(nparam) if vbytes and (vbytes[i >> 3] >> (i & 7)) & 1]
        if len(volatile) != nvol:
            raise Damaged(f"material {name!r}: {len(volatile)} volatile flags set, the count says {nvol}")
        ud = self.user_data(uda, udd, nud, f"material {name!r}")
        for arr, n, what in ((sslot, nsamp, "sampler slots"), (tslot, ntex, "texture slots")):
            if arr:
                vals = r.unpack(f"{n}q", arr) if n else ()
                self.span(arr, 8 * n, what)
                if any(v != -1 for v in vals):
                    raise Unsupported(f"material {name!r}: {what} are set")
        mat = Material(name, flags, infos, shader, samplers, textures, params, pdata, volatile, ud)
        if rawsize:
            mat.extra["nx.raw_param_size"] = rawsize
        if not nud and not uda:
            mat.extra["nx.null_user_data"] = True
        return mat

    def assign(self, o: int, mat: str) -> ShaderAssign:
        r = self.r
        self.span(o, ASSIGN_SIZE, "shader assign")
        archive, model = self.sp(o, "shader archive"), self.sp(o + 8, "shading model")
        aa, ad, sa, sd, oa, od = r.unpack("6Q", o + 0x10)
        rev, na, ns, no = r.unpack("IBBH", o + 0x40)
        out = []
        for arr, dct, n, what in ((aa, ad, na, "attribute assign"), (sa, sd, ns, "sampler assign"),
                                  (oa, od, no, "shader option")):
            keys = self.names(dct, n, f"material {mat!r} {what}")
            vals = [self.sp(arr + 8 * i, what) for i in range(n)]
            self.span(arr, 8 * n, what + " values")
            out.append(list(zip(keys, vals)))
        return ShaderAssign(archive, model, rev, *out)

    # ---------------------------------------------------------------- animations

    def curves(self, arr: int, n: int) -> list[AnimCurve]:
        r = self.r
        out = []
        for i in range(n):
            c = arr + CURVE_SIZE * i
            self.span(c, CURVE_SIZE, "curve")
            fa, ka = r.unpack("QQ", c)
            flags, keys = r.unpack("HH", c + 0x10)
            fsz, ksz = curve_sizes(flags, keys)
            out.append(AnimCurve(r.raw(c + 0x10, 0x20), r.raw(fa, fsz, "curve frames"), r.raw(ka, ksz, "curve keys")))
            self.span(fa, fsz, "curve frames")
            self.span(ka, ksz, "curve keys")
        return out

    def fska(self, o: int) -> SkelAnim:
        r = self.r
        if r.raw(o, 4, "FSKA") != b"FSKA":
            raise Damaged(f"skeletal animation at {o:#x} is not an FSKA")
        self.span(o, FSKA_SIZE, "FSKA")
        name, path = self.sp(o + 0x10, "animation name"), self.sp(o + 0x18, "animation path")
        bskel, bind, barr, uda, udd = r.unpack("5Q", o + 0x20)
        if bskel:
            raise Unsupported(f"skeletal animation {name!r}: bind skeleton is set")
        head = r.raw(o + 0x48, 0x18)
        nbone, nud = r.unpack("HH", o + 0x58)
        binds = list(r.unpack(f"{nbone}H", bind)) if nbone else []
        self.span(bind, 2 * nbone, "bind indices")
        anims = []
        for i in range(nbone):
            b = barr + BONE_ANIM_SIZE * i
            self.span(b, BONE_ANIM_SIZE, "bone anim")
            bn = self.sp(b, "bone anim name")
            carr, base = r.unpack("QQ", b + 8)
            bhead = r.raw(b + 0x18, 0x10)
            flags, ncurve = r.u32(b + 0x18), r.u8(b + 0x1E)
            bsize = base_data_size(flags)
            bdata = r.raw(base, bsize, "bone anim base data") if base else None
            self.span(base, bsize, "bone anim base")
            anims.append(BoneAnim(bn, bhead, bdata, self.curves(carr, ncurve)))
        ud = self.user_data(uda, udd, nud, f"skeletal animation {name!r}")
        return SkelAnim(name, path, head, binds, anims, ud)

    def fmaa(self, o: int) -> MatAnim:
        r = self.r
        if r.raw(o, 4, "FMAA") != b"FMAA":
            raise Damaged(f"material animation at {o:#x} is not an FMAA")
        self.span(o, FMAA_SIZE, "FMAA")
        name, path = self.sp(o + 0x10, "animation name"), self.sp(o + 0x18, "animation path")
        bmodel, bind, mdat, rtt, tna, uda, _udd, tslot = r.unpack("8Q", o + 0x20)
        if bmodel:
            raise Unsupported(f"material animation {name!r}: bind model is set")
        head = r.raw(o + 0x60, 0x18)
        nmat, ntex = r.u16(o + 0x64), r.u16(o + 0x76)
        if uda:
            # where an FMAA keeps its user data count is not known (no sample has user data): refuse, never guess
            raise Unsupported(f"material animation {name!r} has user data")
        binds = list(r.unpack(f"{nmat}H", bind)) if nmat else []
        self.span(bind, 2 * nmat, "bind indices")
        mats = []
        for i in range(nmat):
            m = mdat + MAT_ANIM_SIZE * i
            self.span(m, MAT_ANIM_SIZE, "mat anim data")
            mn = self.sp(m, "material anim name")
            pinfo, tinfo, carr, cst = r.unpack("4Q", m + 8)
            mhead = r.raw(m + 0x28, 0x18)
            npi, ntpi, ncst, ncv = r.unpack("4H", m + 0x32)
            pis = []
            for k in range(npi):
                a = pinfo + PARAM_INFO_SIZE * k
                self.span(a, PARAM_INFO_SIZE, "param anim info")
                pis.append((self.sp(a, "param anim name"), r.raw(a + 8, PARAM_INFO_SIZE - 8)))
            tps = []
            for k in range(ntpi):
                a = tinfo + PATTERN_INFO_SIZE * k
                self.span(a, PATTERN_INFO_SIZE, "pattern anim info")
                tps.append((self.sp(a, "pattern anim name"), r.raw(a + 8, PATTERN_INFO_SIZE - 8)))
            consts = r.raw(cst, 8 * ncst, "anim constants") if ncst else b""
            self.span(cst, 8 * ncst, "anim constants")
            mats.append(MatAnimData(mn, mhead, pis, tps, self.curves(carr, ncv), consts))
        textures = [self.sp(tna + 8 * i, "texture name") for i in range(ntex)] if tna else []
        self.span(tna, 8 * ntex, "anim texture names")
        self.span(rtt, 8 * ntex, "anim runtime textures")
        if tslot:
            self.span(tslot, 8 * ntex, "anim texture slots")
        return MatAnim(name, path, head, binds, mats, textures)


def read(data, spans: list | None = None) -> ResFile:
    """Neutral ResFile of a decompressed Switch model / animation BFRES. Texture files (`X.Tex`) hold no models: use
    nx_bfres.read_textures for them. `spans` (optional) collects (offset, size, label) of every structure read."""
    return _Read(data, spans).file()


def read_checked(data):
    """(ResFile, pointer field positions read, [(dict nodes, keys)]) for the self-check."""
    rd = _Read(data)
    res = rd.file()
    return res, rd.r.fields, rd.dicts


# --------------------------------------------------------------------------------------------------------- writer

class _Write:
    """Places structures in the game's order. `p(pos, target)` records a relocated pointer (target 0 = a null pointer
    that the game still relocates); string pointers are filled once the pool is placed."""

    def __init__(self, res: ResFile):
        self.res = res
        self.buf = bytearray()
        self.ptrs: dict[int, int] = {}
        self.sptrs: list[tuple[int, str]] = []
        self.uses: dict[str, int] = {}
        self.blocks: list[tuple[int, bytes]] = []
        self.mempool_ptrs: list[int] = []

    # ---------------------------------------------------------------- primitives

    def alloc(self, n: int, a: int = 8) -> int:
        pad = -len(self.buf) % a
        if pad:
            self.buf.extend(bytes(pad))
        off = len(self.buf)
        self.buf.extend(bytes(n))
        return off

    def here(self, a: int = 8) -> int:
        return len(self.buf) + (-len(self.buf) % a)

    def put(self, fmt: str, off: int, *vals) -> None:
        struct.pack_into("<" + fmt, self.buf, off, *vals)

    def raw(self, off: int, data) -> None:
        self.buf[off:off + len(data)] = data

    def p(self, pos: int, target: int) -> None:
        self.ptrs[pos] = target

    def ps(self, pos: int, s: str) -> None:
        self.sptrs.append((pos, s))
        self.uses[s] = self.uses.get(s, 0) + 1

    def block(self, off: int, magic: bytes, chained: bool = True) -> None:
        self.buf[off:off + 4] = magic
        if chained:
            self.blocks.append((off, magic))

    def dict_(self, keys: list[str]) -> int:
        n = len(keys)
        off = self.alloc(dicts.nx_dict_size(n))
        nodes = dicts.build([k.encode("utf-8") for k in keys])
        self.put("i", off + 4, n)
        for i, (ref, left, right) in enumerate(nodes):
            e = off + 8 + 16 * i
            self.put("iHH", e, ref, left, right)
            self.ps(e + 8, "" if i == 0 else keys[i - 1])
        return off

    def strings_array(self, off: int, values) -> None:
        for i, v in enumerate(values):
            self.ps(off + 8 * i, v)

    # ---------------------------------------------------------------- file

    def pool(self) -> bytes:
        """GPU pool contents in the game's order: all index buffers (model, shape, mesh order), then all vertex buffers
        (model, FVTX, buffer order), each 8-aligned. Records the offsets for the meshes and FVTXs."""
        pool = bytearray()
        self.index_offs, self.vertex_offs = {}, {}
        for mi, m in enumerate(self.res.models):
            for si, sh in enumerate(m.shapes):
                for ki, mesh in enumerate(sh.meshes):
                    pool.extend(bytes(-len(pool) % 8))
                    self.index_offs[mi, si, ki] = len(pool)
                    pool += mesh.indices
        pool.extend(bytes(-len(pool) % 8))
        self.first_vertex = len(pool)
        for mi, m in enumerate(self.res.models):
            for vi, vb in enumerate(m.vertex_buffers):
                pool.extend(bytes(-len(pool) % 8))
                self.vertex_offs[mi, vi] = len(pool)
                for data in vb.buffers:
                    pool.extend(bytes(-len(pool) % 8))
                    pool += data
        return bytes(pool)

    def file(self) -> bytes:
        res = self.res
        nm, nsa, nma, nx_ = len(res.models), len(res.skel_anims), len(res.mat_anims), len(res.external)
        self.alloc(0xD0)
        self.buf[0:8] = MAGIC
        models = self.alloc(FMDL_SIZE * nm)
        fska = self.alloc(FSKA_SIZE * nsa)
        fmaa = self.alloc(FMAA_SIZE * nma)
        bufinfo = self.alloc(0x20)
        self.put("I", bufinfo, res.extra.get("nx.pool_property", POOL_PROPERTY))
        ext = self.alloc(0x10 * nx_)
        self.ps(0x20, res.name)
        for k, (arr, n) in enumerate(((models, nm), (fska, nsa), (fmaa, nma))):
            if n:
                self.p(0x28 + 0x10 * k, arr)
        names = ([m.name for m in res.models], [a.name for a in res.skel_anims], [a.name for a in res.mat_anims])
        for k, keys in enumerate(names):
            self.p(0x30 + 0x10 * k, self.dict_(keys) if keys else 0)
        for k in range(3):                                   # bone visibility, shape, scene anims: null dicts
            self.p(0x60 + 0x10 * k, 0)
        if nx_:
            self.p(0x98, ext)
        self.p(0xA0, self.dict_([e.name for e in res.external]) if nx_ else 0)
        self.put("7H", 0xBC, nm, nsa, nma, 0, 0, 0, nx_)
        pool = self.pool()
        for i, m in enumerate(res.models):
            self.model(models + FMDL_SIZE * i, m, i)
        for i, a in enumerate(res.skel_anims):
            self.fska(fska + FSKA_SIZE * i, a)
        for i, a in enumerate(res.mat_anims):
            self.fmaa(fmaa + FMAA_SIZE * i, a)
        # string pool: the read file's order, then new strings by use count (most used first)
        order = [s for s in res.extra.get("nx.string_order", []) if s in self.uses]
        known = set(order)
        rest = sorted((s for s in self.uses if s not in known and s), key=lambda s: -self.uses[s])
        body, soffs = strpool.nx_pool(order + rest)
        st = self.alloc(0x10 + len(body))
        self.block(st, b"_STR")
        self.raw(st + 0x10, body)
        str_end = st + 0x10 + len(body)
        self.p(0xB0, st + 0x14)
        self.put("I", 0xB8, str_end - st)
        for pos, s in self.sptrs:
            self.p(pos, st + 0x10 + soffs[s])
        sec0 = str_end
        secs = [(0, sec0)]
        if pool:
            pool_off = align(sec0, 0x1000)
            pool_size = align(len(pool), 0x1000)
            self.buf.extend(bytes(pool_off - len(self.buf)))
            self.buf += pool + bytes(pool_size - len(pool))
            self.put("IQ", bufinfo + 4, pool_size, pool_off)
            self.p(bufinfo + 8, pool_off)
            self.p(0x90, bufinfo)
            mp = self.alloc(MEMORY_POOL)
            self.p(0x88, mp)
            for pos in self.mempool_ptrs:
                self.p(pos, mp)
            secs += [(pool_off, self.first_vertex), (pool_off + self.first_vertex, pool_size - self.first_vertex),
                     (mp, MEMORY_POOL)]
        else:
            secs += [(sec0, 0)] * 3
        empty_ext = secs[-1][0] + secs[-1][1]
        ext_start, last_align = None, 8
        for i, e in enumerate(res.external):
            a = 0x1000 if bytes(e.data[:4]) == b"BNTX" else 0x100
            off = self.alloc(len(e.data), a)
            self.raw(off, e.data)
            self.put("QI", ext + 0x10 * i, off, len(e.data))
            self.p(ext + 0x10 * i, off)
            ext_start = off if ext_start is None else ext_start
            last_align = a
        rlt_off = align(len(self.buf), last_align)
        self.buf.extend(bytes(rlt_off - len(self.buf)))
        secs.append((ext_start, rlt_off - ext_start) if ext_start is not None else (empty_ext, 0))
        # block chain (blocks in file order; the last runs to the relocation table), header
        self.blocks.sort()
        for (o, _), (nxt, _) in zip(self.blocks, self.blocks[1:]):
            self.put("II", o + 4, nxt - o, nxt - o)
        if self.blocks:
            last = self.blocks[-1][0]
            self.put("II", last + 4, 0, rlt_off - last)
        # alignment: 2^12 with GPU data or external files, else 2^3 (animation files; 28/28 of the user's files)
        self.put("IHBBIHHI", 8, VERSION, 0xFEFF, res.extra.get("nx.alignment_log2", 12 if pool or nx_ else 3), 0,
                 self.ptrs[0x20] + 2, 0, self.blocks[0][0] if self.blocks else 0, rlt_off)
        for pos, tgt in self.ptrs.items():
            self.put("Q", pos, tgt)
        self.buf += rlt.build(rlt_off, secs, sorted(self.ptrs.items()))
        self.put("I", 0x1C, len(self.buf))
        return bytes(self.buf)

    # ---------------------------------------------------------------- model

    def user_data_array(self, uds: list[UserData]) -> int:
        return self.alloc(USER_DATA_SIZE * len(uds)) if uds else self.here()

    def user_data_values(self, arr: int, uds: list[UserData]) -> None:
        for i, u in enumerate(uds):
            o = arr + USER_DATA_SIZE * i
            self.ps(o, u.name)
            kind = u.kind
            if kind not in USER_DATA_CODE or kind == "wstring":
                raise Unsupported(f"user data {u.name!r}: type {u.kind!r} is not written for the Switch (wide "
                                  f"strings become strings in materials.to_switch, with a warning)")
            vals = u.values
            n = len(vals)
            self.put("IB", o + 0x10, n, USER_DATA_CODE[kind])
            if "nx.tail" in u.extra:
                self.raw(o + 0x15, u.extra["nx.tail"])
            if not n:
                continue
            if kind == "string":
                d = self.alloc(8 * n)
                self.strings_array(d, vals)
            elif kind == "bytes":
                d = self.alloc(n)
                self.raw(d, bytes(vals))
            else:
                d = self.alloc(4 * n)
                self.put(f"{n}{'i' if kind == 'int' else 'f'}", d, *vals)
            self.p(o + 8, d)

    def model(self, o: int, m: Model, mi: int) -> None:
        self.block(o, b"FMDL")
        sk = m.skeleton
        nv, ns, nmat, nud = len(m.vertex_buffers), len(m.shapes), len(m.materials), len(m.user_data)
        self.ps(o + 0x10, m.name)
        self.ps(o + 0x18, m.path)
        self.put("4HI", o + 0x68, nv, ns, nmat, nud, m.total_vertex_count)
        vtx = self.alloc(FVTX_SIZE * nv)
        mats = self.alloc(FMAT_SIZE * nmat)
        shp = self.alloc(FSHP_SIZE * ns)
        uda = self.user_data_array(m.user_data)
        # the four arrays are one block; when all are empty the game writes null pointers (still relocated)
        empty = not (nv or ns or nmat or nud)
        self.p(o + 0x28, 0 if empty else vtx)
        self.p(o + 0x30, 0 if empty else shp)
        self.p(o + 0x40, 0 if empty else mats)
        self.p(o + 0x50, 0 if empty else uda)
        fskl = self.alloc(FSKL_SIZE)
        self.p(o + 0x20, fskl)
        self.block(fskl, b"FSKL")
        bones = self.alloc(BONE_SIZE * len(sk.bones))
        bone_uds = []
        for i, b in enumerate(sk.bones):
            bo = bones + BONE_SIZE * i
            self.ps(bo, b.name)
            self.put("HhhhHHI", bo + 0x18, i, b.parent, b.smooth_index, b.rigid_index, b.billboard_index,
                     len(b.user_data), b.flags)
            self.put("10f", bo + 0x28, *b.scale, *b.rotation, *b.translation)
            if not b.user_data:
                self.p(bo + 8, 0)
                self.p(bo + 0x10, 0)
        for i, b in enumerate(sk.bones):
            if b.user_data:
                bo = bones + BONE_SIZE * i
                arr = self.alloc(USER_DATA_SIZE * len(b.user_data))
                self.p(bo + 8, arr)
                bone_uds.append((bo, b.user_data, arr))
        nmtx = sk.smooth_count + sk.rigid_count
        if len(sk.matrix_to_bone) != nmtx:
            raise Damaged(f"model {m.name!r}: {len(sk.matrix_to_bone)} matrices, counts say {nmtx}")
        if len(sk.inverse_matrices) != 48 * sk.smooth_count:
            raise Damaged(f"model {m.name!r}: inverse matrix bytes do not match {sk.smooth_count} smooth matrices")
        mtob = self.alloc(2 * nmtx)
        self.put(f"{nmtx}H", mtob, *sk.matrix_to_bone)
        inv = self.alloc(len(sk.inverse_matrices))
        self.raw(inv, sk.inverse_matrices)
        null_mtob, null_inv = sk.extra.get("nx.null_matrix_ptrs", (False, False))
        if nmtx or not null_mtob:
            self.p(fskl + 0x20, mtob)
        if sk.smooth_count or not null_inv:
            self.p(fskl + 0x28, inv)
        self.p(fskl + 0x18, bones)
        self.put("I3H", fskl + 0x38, sk.flags, len(sk.bones), sk.smooth_count, sk.rigid_count)
        self.p(fskl + 0x10, self.dict_([b.name for b in sk.bones]))
        self.p(o + 0x38, self.dict_([s.name for s in m.shapes]) if ns else 0)
        self.p(o + 0x48, self.dict_([x.name for x in m.materials]) if nmat else 0)
        self.p(o + 0x58, self.dict_([u.name for u in m.user_data]) if nud else 0)
        for bo, uds, arr in bone_uds:
            self.p(bo + 0x10, self.dict_([u.name for u in uds]))
            self.user_data_values(arr, uds)
        for si, sh in enumerate(m.shapes):
            self.fshp(shp + FSHP_SIZE * si, sh, si, mi, vtx)
        for vi, vb in enumerate(m.vertex_buffers):
            self.fvtx(vtx + FVTX_SIZE * vi, vb, vi, mi)
        self.user_data_values(uda, m.user_data)
        for k, mat in enumerate(m.materials):
            self.fmat(mats + FMAT_SIZE * k, mat, k)

    def fshp(self, o: int, sh: Shape, si: int, mi: int, vtx: int) -> None:
        self.block(o, b"FSHP", chained=False)
        if sh.key_shapes:
            raise Unsupported(f"shape {sh.name!r} has key shapes")
        self.ps(o + 0x10, sh.name)
        self.p(o + 0x18, vtx + FVTX_SIZE * sh.vertex_buffer)
        self.put("I5H4B", o + 0x58, sh.flags, si, sh.material, sh.bone, sh.vertex_buffer, len(sh.skin_bones),
                 sh.skin_count, len(sh.meshes), 0, sh.target_attrib_count)
        meshes = self.alloc(MESH_SIZE * len(sh.meshes))
        self.p(o + 0x20, meshes)
        sk = self.alloc(2 * len(sh.skin_bones))
        self.put(f"{len(sh.skin_bones)}H", sk, *sh.skin_bones)
        if sh.skin_bones or not sh.extra.get("nx.null_skin_bones"):
            self.p(o + 0x28, sk)
        nbox = sum(len(m.submeshes) + 1 for m in sh.meshes)
        if len(sh.bounds) != nbox or len(sh.radii) != len(sh.meshes):
            raise Damaged(f"shape {sh.name!r}: {len(sh.bounds)} bounds / {len(sh.radii)} radii for "
                          f"{len(sh.meshes)} meshes")
        bb = self.alloc(24 * nbox)
        for k, box in enumerate(sh.bounds):
            self.put("6f", bb + 24 * k, *box)
        rad = self.alloc(4 * len(sh.radii), 4)
        self.put(f"{len(sh.radii)}f", rad, *sh.radii)
        self.p(o + 0x40, bb)
        self.p(o + 0x48, rad)
        for k, mesh in enumerate(sh.meshes):
            mo = meshes + MESH_SIZE * k
            if mesh.primitive not in PRIMITIVE_CODE or mesh.index_format not in INDEX_CODE:
                raise Unsupported(f"shape {sh.name!r}: {mesh.primitive} / {mesh.index_format}")
            size = len(mesh.indices)
            if size != mesh.index_count * INDEX_SIZE[mesh.index_format]:
                raise Damaged(f"shape {sh.name!r}: {size} index bytes for {mesh.index_count} indices")
            sub = self.alloc(8 * len(mesh.submeshes))
            for j, (a, b) in enumerate(mesh.submeshes):
                self.put("II", sub + 8 * j, a, b)
            rtb = self.alloc(RT_BUFFER)
            info = self.alloc(0x30)
            self.put("I", info, size)
            self.p(mo, sub)
            self.mempool_ptrs.append(mo + 8)
            self.p(mo + 0x10, rtb)
            self.p(mo + 0x18, info)
            self.put("IIIIIH", mo + 0x20, self.index_offs[mi, si, k], PRIMITIVE_CODE[mesh.primitive],
                     INDEX_CODE[mesh.index_format], mesh.index_count, mesh.first_vertex, len(mesh.submeshes))

    def fvtx(self, o: int, vb: VertexBuffer, vi: int, mi: int) -> None:
        self.block(o, b"FVTX", chained=False)
        nb = len(vb.buffers)
        attrs = self.alloc(ATTR_SIZE * len(vb.attributes))
        for k, a in enumerate(vb.attributes):
            ao = attrs + ATTR_SIZE * k
            self.ps(ao, a.name)
            self.put("IHH", ao + 8, a.format, a.offset, a.buffer)
        rtb = self.alloc(RT_BUFFER * nb)
        info = self.alloc(0x10 * nb)
        strides = self.alloc(0x10 * nb + 0x20)
        ubuf = self.alloc(8 * nb)
        for k, (stride, data) in enumerate(zip(vb.strides, vb.buffers)):
            if len(data) != stride * vb.vertex_count:
                raise Damaged(f"vertex buffer {vi} buffer {k}: {len(data)} bytes, stride {stride} x "
                              f"{vb.vertex_count}")
            self.put("I", info + 0x10 * k, len(data))
            self.put("I", strides + 0x10 * k, stride)
        self.p(o + 0x10, attrs)
        self.p(o + 0x18, self.dict_([a.name for a in vb.attributes]))
        self.mempool_ptrs.append(o + 0x20)
        self.p(o + 0x28, rtb)
        self.p(o + 0x30, ubuf)
        self.p(o + 0x38, info)
        self.p(o + 0x40, strides)
        self.put("IBBHIB", o + 0x50, self.vertex_offs[mi, vi], len(vb.attributes), nb, vi,
                 vb.vertex_count, vb.skin_count)

    def fmat(self, o: int, mat: Material, idx: int) -> None:
        self.block(o, b"FMAT")
        self.ps(o + 0x10, mat.name)
        ns, nt, npar, nud = len(mat.samplers), len(mat.textures), len(mat.params), len(mat.user_data)
        if ns != nt:
            raise Unsupported(f"material {mat.name!r}: {ns} samplers but {nt} textures (Switch needs one each)")
        ri = self.alloc(RINFO_SIZE * len(mat.render_infos))
        self.p(o + 0x18, ri)
        later = []
        for k, info in enumerate(mat.render_infos):
            a = ri + RINFO_SIZE * k
            self.ps(a, info.name)
            self.put("HB", a + 0x10, len(info.values), RENDER_INFO_CODE[info.kind])
            if info.values:
                if info.kind == "string":
                    d = self.alloc(8 * len(info.values))
                    self.strings_array(d, info.values)
                    self.p(a + 8, d)
                else:
                    later.append((a, info))
        for a, info in later:
            n = len(info.values)
            d = self.alloc(4 * n, 4)
            self.put(f"{n}{'i' if info.kind == 'int' else 'f'}", d, *info.values)
            self.p(a + 8, d)
        rtt = self.alloc(8 * nt)
        sinfo = self.alloc(SAMPLER_SIZE * ns)
        for k, s in enumerate(mat.samplers):
            st = s.state
            a = sinfo + SAMPLER_SIZE * k
            self.put("6BH3f", a, st.wrap_u, st.wrap_v, st.wrap_w, st.compare, st.border, st.max_aniso, st.filter,
                     st.min_lod, st.max_lod, st.lod_bias)
            if "nx.tail" in st.extra:
                self.raw(a + 0x14, st.extra["nx.tail"])
        rts = self.alloc(RT_SAMPLER * ns)
        par = self.alloc(PARAM_SIZE * npar)
        for k, p in enumerate(mat.params):
            a = par + PARAM_SIZE * k
            self.ps(a + 8, p.name)
            self.put("BBHiHH", a + 0x10, p.type, p.size, p.offset, p.extra.get("uniform", -1), p.depended, p.depend)
        pdata = self.alloc(len(mat.param_data))
        self.raw(pdata, mat.param_data)
        vol = self.alloc(align(npar, 64) // 8)
        for i in mat.volatile:
            self.buf[vol + (i >> 3)] |= 1 << (i & 7)
        uda = self.user_data_array(mat.user_data)
        sslot = self.alloc(8 * ns)
        tslot = self.alloc(8 * nt)
        self.put(f"{ns}q", sslot, *([-1] * ns))
        self.put(f"{nt}q", tslot, *([-1] * nt))
        tna = self.alloc(8 * nt)
        self.strings_array(tna, mat.textures)
        sa, _ = self.assign(mat.shader)
        self.p(o + 0x28, sa)
        # with no textures / samplers: texture arrays and slots are null (not relocated), the sampler arrays point at
        # the next structure (as the game's Mt_*_Shadow materials have them)
        for pos, tgt, n in ((0x30, rtt, nt), (0x38, tna, nt), (0x40, rts, 1), (0x48, sinfo, 1), (0x58, par, 1),
                            (0x68, pdata, 1), (0x80, vol, 1), (0x90, sslot, ns), (0x98, tslot, nt)):
            if n:
                self.p(o + pos, tgt)
        self.p(o + 0x70, 0 if mat.extra.get("nx.null_user_data") and not nud else uda)
        self.assign_dicts(sa, mat.shader)
        self.p(o + 0x20, self.dict_([x.name for x in mat.render_infos]) if mat.render_infos else 0)
        self.p(o + 0x50, self.dict_([s.name for s in mat.samplers]) if ns else 0)
        self.p(o + 0x60, self.dict_([p.name for p in mat.params]) if npar else 0)
        self.p(o + 0x78, self.dict_([u.name for u in mat.user_data]) if nud else 0)
        self.user_data_values(uda, mat.user_data)
        self.put("IHHBBHHHHH", o + 0xA0, mat.flags, idx, len(mat.render_infos), ns, nt, npar, len(mat.volatile),
                 len(mat.param_data), mat.extra.get("nx.raw_param_size", 0), nud)

    def assign(self, sa: ShaderAssign) -> tuple[int, list[int]]:
        o = self.alloc(ASSIGN_SIZE)
        self.ps(o, sa.archive)
        self.ps(o + 8, sa.model)
        self.put("IBBH", o + 0x40, sa.revision, len(sa.attribs), len(sa.samplers), len(sa.options))
        arrays = []
        for k, pairs in enumerate((sa.attribs, sa.samplers, sa.options)):
            arr = self.alloc(8 * len(pairs))
            self.strings_array(arr, [v for _, v in pairs])
            self.p(o + 0x10 + 0x10 * k, arr)
            arrays.append(arr)
        return o, arrays

    def assign_dicts(self, o: int, sa: ShaderAssign) -> None:
        for k, pairs in enumerate((sa.attribs, sa.samplers, sa.options)):
            self.p(o + 0x18 + 0x10 * k, self.dict_([kk for kk, _ in pairs]) if pairs else 0)

    # ---------------------------------------------------------------- animations

    def curves(self, cs: list[AnimCurve]) -> int:
        arr = self.alloc(CURVE_SIZE * len(cs))
        for k, c in enumerate(cs):
            a = arr + CURVE_SIZE * k
            self.raw(a + 0x10, c.head)
            flags, keys = struct.unpack_from("<HH", c.head)
            if curve_sizes(flags, keys) != (len(c.frames), len(c.keys)):
                raise Damaged(f"animation curve: {len(c.frames)} frame / {len(c.keys)} key bytes do not match its "
                              f"flags {flags:#x} and {keys} keys")
            fa = self.alloc(len(c.frames))
            self.raw(fa, c.frames)
            ka = self.alloc(len(c.keys))
            self.raw(ka, c.keys)
            self.p(a, fa)
            self.p(a + 8, ka)
        return arr

    def fska(self, o: int, a: SkelAnim) -> None:
        self.block(o, b"FSKA")
        self.ps(o + 0x10, a.name)
        self.ps(o + 0x18, a.path)
        self.raw(o + 0x48, a.head)
        # counts follow the lists (the head may come from another file)
        self.put("i", o + 0x50, sum(len(b.curves) for b in a.bone_anims))
        self.put("HH", o + 0x58, len(a.bone_anims), len(a.user_data))
        if len(a.bind_indices) != len(a.bone_anims):
            raise Damaged(f"skeletal animation {a.name!r}: {len(a.bind_indices)} bind indices for "
                          f"{len(a.bone_anims)} bone animations")
        bind = self.alloc(2 * len(a.bind_indices))
        self.put(f"{len(a.bind_indices)}H", bind, *a.bind_indices)
        barr = self.alloc(BONE_ANIM_SIZE * len(a.bone_anims))
        uda = self.user_data_array(a.user_data)
        self.p(o + 0x28, bind)
        self.p(o + 0x30, barr)
        self.p(o + 0x38, uda)
        for k, b in enumerate(a.bone_anims):
            bo = barr + BONE_ANIM_SIZE * k
            self.ps(bo, b.name)
            self.raw(bo + 0x18, b.head)
            self.put("B", bo + 0x1E, len(b.curves))
            if b.base is not None and len(b.base) != base_data_size(struct.unpack_from("<I", b.head)[0]):
                raise Damaged(f"bone animation {b.name!r}: base data does not match its flags")
            if b.base is not None:
                d = self.alloc(len(b.base))
                self.raw(d, b.base)
                self.p(bo + 0x10, d)
            else:
                self.p(bo + 0x10, 0)
            if b.curves:
                self.p(bo + 8, self.curves(b.curves))
        self.p(o + 0x40, self.dict_([u.name for u in a.user_data]) if a.user_data else 0)
        self.user_data_values(uda, a.user_data)

    def fmaa(self, o: int, a: MatAnim) -> None:
        self.block(o, b"FMAA", chained=False)
        self.ps(o + 0x10, a.name)
        self.ps(o + 0x18, a.path)
        self.raw(o + 0x60, a.head)
        self.put("HH", o + 0x64, len(a.materials), sum(len(m.curves) for m in a.materials))
        self.put("H", o + 0x72, sum(len(m.pattern_infos) for m in a.materials))
        self.put("H", o + 0x76, len(a.textures))
        if len(a.bind_indices) != len(a.materials):
            raise Damaged(f"material animation {a.name!r}: {len(a.bind_indices)} bind indices for "
                          f"{len(a.materials)} materials")
        bind = self.alloc(2 * len(a.bind_indices))
        self.put(f"{len(a.bind_indices)}H", bind, *a.bind_indices)
        mdat = self.alloc(MAT_ANIM_SIZE * len(a.materials))
        self.p(o + 0x28, bind)
        self.p(o + 0x30, mdat)
        nt = len(a.textures)
        if nt:
            tslot = self.alloc(8 * nt)
            self.put(f"{nt}q", tslot, *([-1] * nt))
            self.p(o + 0x58, tslot)
        rtt = self.alloc(8 * nt)
        self.p(o + 0x38, rtt)
        if nt:
            tna = self.alloc(8 * nt)
            self.strings_array(tna, a.textures)
            self.p(o + 0x40, tna)
        if a.user_data:
            raise Unsupported(f"material animation {a.name!r}: user data is not written (layout not known)")
        for k, md in enumerate(a.materials):
            mo = mdat + MAT_ANIM_SIZE * k
            self.ps(mo, md.name)
            self.raw(mo + 0x28, md.head)
            if len(md.constants) % 8:
                raise Damaged(f"material animation {a.name!r}: constants are 8 bytes each")
            self.put("4H", mo + 0x32, len(md.param_infos), len(md.pattern_infos), len(md.constants) // 8,
                     len(md.curves))
            if md.param_infos:
                pi = self.alloc(PARAM_INFO_SIZE * len(md.param_infos))
                for j, (n, raw) in enumerate(md.param_infos):
                    self.ps(pi + PARAM_INFO_SIZE * j, n)
                    self.raw(pi + PARAM_INFO_SIZE * j + 8, raw)
                self.p(mo + 8, pi)
            if md.pattern_infos:
                ti = self.alloc(PATTERN_INFO_SIZE * len(md.pattern_infos))
                for j, (n, raw) in enumerate(md.pattern_infos):
                    self.ps(ti + PATTERN_INFO_SIZE * j, n)
                    self.raw(ti + PATTERN_INFO_SIZE * j + 8, raw)
                self.p(mo + 0x10, ti)
            if md.constants:
                c = self.alloc(len(md.constants))
                self.raw(c, md.constants)
                self.p(mo + 0x20, c)
            if md.curves:
                self.p(mo + 0x18, self.curves(md.curves))
        self.p(o + 0x50, 0)


def write(res: ResFile) -> bytes:
    """Switch BFRES 0.5.0.3 bytes of a neutral ResFile (models, Switch animations, external files)."""
    return _Write(res).file()
