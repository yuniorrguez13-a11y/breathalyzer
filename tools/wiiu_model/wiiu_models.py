"""Wii U BFRES 4.5 model files (milestone M2): FMDL, FSKL, FVTX, FSHP, FMAT (RenderState, samplers, params, shader
assign), user data and external files, read into the neutral model; and a writer for tests (Switch -> Wii U -> Switch).
Embedded animations (FSKA, FSHU, FTXP) are read and written by wiiu_anims (Integrate milestone).

Layouts from out/model-work/wiiu-format.md (BfresLibrary's Wii U loaders, MIT licence; wut's GX2 enums, zlib licence).
No real Wii U model file has been seen yet, so the reader is strict: unknown formats, key shapes, bounding node trees
and versions other than 4.5.x are refused or warned about, never guessed. Offsets are s32 relative to their own field
(0 = none; a null offset of something with a count above 0 is damage, see need_rel); a string offset points at the
characters (the u32 length is 4 bytes before). Dictionaries ("index groups") are read in entry order; their trie is not
used. String-valued dictionaries (shader assign) point their data offset straight at the string characters
(BfresLibrary's ResString, Wii U branch).

Byte order: every number becomes little-endian in the neutral model. Vertex components are swapped by their own size
(8-bit none, 16-bit 2 bytes, 32-bit 4 bytes, 10_10_10_2 as one 32-bit word); indices of the big-endian GX2 index types
(4, 9) are swapped, the little-endian ones (0, 1) are not; param data swaps as 32-bit words (every param type is made
of 4-byte units).

The writer exists only for tests: it is shaped like the reader, so a round trip through it proves our two directions
agree, not that real Wii U files look like this.
"""
from __future__ import annotations

import struct
from array import array

from . import dicts, materials, wiiu_anims
from .binio import Reader, align
from .errors import Damaged, Unsupported
from .formats import (GX2_INDEX, GX2_INDEX_CODE, GX2_PRIMITIVE, GX2_PRIMITIVE_CODE, USER_DATA_CODE, USER_DATA_KINDS,
                      attr_component_sizes, attr_format_from_gx2, attr_format_to_gx2)
from .neutral import (Attribute, Bone, ExtFile, Material, Mesh, Model, Param, RenderInfo, RenderState, ResFile, Sampler,
                      Shape, ShaderAssign, SkelAnim, Skeleton, UserData, VertexBuffer)

VERSION = 0x04050003
MODEL_MIN_VERSION = 0x04050000
HEADER_SIZE = 0x6C
GROUP_NAMES = ("models", "textures", "skeletal anims", "shader param anims", "color anims", "tex SRT anims",
               "tex pattern anims", "bone visibility anims", "material visibility anims", "shape anims",
               "scene anims", "external files")
FMDL_SIZE, FSKL_SIZE, BONE_SIZE, FVTX_SIZE, ATTR_SIZE, BUFFER_SIZE = 0x30, 0x24, 0x40, 0x20, 0x0C, 0x18
FSHP_SIZE, MESH_SIZE, FMAT_SIZE, RSTATE_SIZE, ASSIGN_SIZE, SAMPLER_SIZE, PARAM_SIZE = 0x38, 0x1C, 0x4C, 0x30, 0x1C, \
    0x18, 0x14
RENDER_INFO_KINDS = {0: "int", 1: "float", 2: "string"}
RENDER_INFO_CODE = {v: k for k, v in RENDER_INFO_KINDS.items()}
PARAM_TEXSRT, PARAM_TEXSRT_EX = 30, 31
TEXSRT_SIZE, TEXSRT_EX_SIZE = 24, 28
DATA_ALIGN = 0x40
SWITCH_ONLY_EXTERNAL = ("TexInfo.txt",)     # Switch model files only; never in a Wii U file


def is_wiiu_bfres(data) -> bool:
    return bytes(data[:4]) == b"FRES" and bytes(data[8:10]) == b"\xfe\xff"


def swap_plan(attrs: list, stride: int, what: str) -> list[tuple[int, int]]:
    """[(byte offset in the vertex, component size)] of the multi-byte components of the attributes of one buffer.
    Components must lie inside the stride and not overlap."""
    plan, used = [], bytearray(stride)
    for a in attrs:
        off = a.offset
        for size in attr_component_sizes(a.format):
            if off + size > stride:
                raise Damaged(f"{what}: attribute {a.name!r} runs past the vertex stride {stride}")
            if any(used[off:off + size]):
                raise Damaged(f"{what}: attribute {a.name!r} overlaps another attribute")
            used[off:off + size] = b"\1" * size
            if size > 1:
                plan.append((off, size))
            off += size
    return plan


def swap_vertices(data, stride: int, plan) -> bytes:
    """Byte-swap each component of every vertex: one extended-slice copy per byte column."""
    if not plan:
        return bytes(data)
    src = memoryview(bytes(data))
    out = bytearray(src)
    for off, size in plan:
        for k in range(size):
            out[off + k::stride] = src[off + size - 1 - k::stride]
    return bytes(out)


def swap_words(data, size: int, what: str) -> bytes:
    if len(data) % size:
        raise Damaged(f"{what}: {len(data)} bytes is not a whole number of {size}-byte values")
    a = array({2: "H", 4: "I"}[size])
    a.frombytes(bytes(data))
    a.byteswap()
    return a.tobytes()


# --------------------------------------------------------------------------------------------------------- reader

class _Read:
    def __init__(self, data, warnings: list, cover: bool):
        self.r = Reader(data, ">", "Wii U BFRES", cover)
        self.warnings = warnings
        self.group_size_notes = 0

    def rel(self, pos: int) -> int:
        v = self.r.s32(pos)
        if not v:
            return 0
        t = pos + v
        if not 0 <= t < self.r.size:
            raise Damaged(f"offset at {pos:#x} points outside the file ({t:#x})")
        return t

    def need_rel(self, pos: int, count: int, what: str) -> int:
        """Target of the offset of something counted (an array, a data block): a null offset with a count above 0
        is damage, never "the start of the file"."""
        t = self.rel(pos)
        if count > 0 and not t:
            raise Damaged(f"{what}: count {count} but the offset at {pos:#x} is null")
        return t

    def s(self, pos: int, what: str = "string") -> str:
        t = self.rel(pos)
        return self.r.cstr(t, what) if t else ""

    def group(self, off: int, what: str, null_data_ok: bool = False) -> list[tuple[str, int]]:
        """[(name, absolute data offset)] of an index group, in entry order. An entry with a null data offset is
        damage unless `null_data_ok` (string-valued groups)."""
        if not off:
            return []
        r = self.r
        size, n = r.u32(off, what), r.s32(off + 4, what)
        if n < 0 or n > 0xFFFF:
            raise Damaged(f"{what}: index group at {off:#x} has {n} entries")
        if size != 8 + 16 * (n + 1):
            self.group_size_notes += 1
        r.need(off, 8 + 16 * (n + 1), what)
        out = []
        for i in range(1, n + 1):
            e = off + 8 + 16 * i
            out.append((self.s(e + 8, f"{what} name"),
                        self.rel(e + 12) if null_data_ok else self.need_rel(e + 12, 1, f"{what} entry {i}")))
        return out

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    # ---------------------------------------------------------------- file

    def file(self) -> ResFile:
        r = self.r
        if r.raw(0, 4) != b"FRES" or r.u16(8) != 0xFEFF:
            raise Damaged("not a Wii U (big-endian) BFRES")
        ver = r.u32(4)
        if not MODEL_MIN_VERSION <= ver <= 0x0405FFFF:
            raise Unsupported(f"Wii U BFRES version {ver:#010x}: only 4.5 (BotW) model files are converted")
        size = r.u32(0x0C)
        if size > r.size:
            raise Damaged(f"file is cut short ({r.size:#x} of {size:#x} bytes)")
        r.need(0, HEADER_SIZE, "header")
        pool_size, pool = r.u32(0x18), self.need_rel(0x1C, r.u32(0x18), "string pool")
        if pool:
            r.need(pool, pool_size, "string pool")
        res = ResFile(self.s(0x14, "file name"), [])
        res.extra["wiiu.alignment"] = r.u32(0x10)
        res.extra["wiiu.version"] = ver
        if r.u32(0x68):
            self.warn("file user pointer is set; ignored")
        groups = [self.rel(0x20 + 4 * i) for i in range(12)]
        counts = r.unpack("12H", 0x50)
        entries = [self.group(g, GROUP_NAMES[i]) if counts[i] or g else [] for i, g in enumerate(groups)]
        for i, e in enumerate(entries):
            if len(e) != counts[i]:
                raise Damaged(f"{GROUP_NAMES[i]}: {len(e)} entries, the header says {counts[i]}")
        if entries[1]:
            res.extra["wiiu.textures"] = [n for n, _ in entries[1]]
            self.warn(f"{len(entries[1])} texture(s) inside the model file ({', '.join(n for n, _ in entries[1][:5])}"
                      f"{'...' if len(entries[1]) > 5 else ''}); they belong in X.Tex")
        res.models = [self.model(off, name) for name, off in entries[0]]
        res.skel_anims, res.mat_anims, dropped = wiiu_anims.read_all(self, entries, GROUP_NAMES)
        if dropped:
            res.extra["wiiu.animations_dropped"] = dropped
        if self.group_size_notes:
            self.warn(f"{self.group_size_notes} index group(s) with an unexpected size field (read anyway)")
        for name, off in entries[11]:
            r.need(off, 8, "external file")
            dsize = r.u32(off + 4)
            data_off = self.need_rel(off, dsize, f"external file {name!r}")
            if name.endswith(".bfsha"):
                self.warn(f"external file {name!r} (a Wii U shader binary) dropped")
                r.need(data_off, dsize, name)
                continue
            res.external.append(ExtFile(name, r.raw(data_off, dsize, f"external file {name!r}")))
        return res

    def user_data(self, dict_off: int, count: int, what: str) -> list[UserData]:
        ents = self.group(dict_off, f"{what} user data")
        if len(ents) != count:
            raise Damaged(f"{what}: {len(ents)} user data entries, the count says {count}")
        out = []
        r = self.r
        for name, o in ents:
            r.need(o, 8, "user data")
            n, kind = r.u16(o + 4), r.u8(o + 6)
            if kind not in USER_DATA_KINDS:
                raise Unsupported(f"{what}: user data {name!r} has type {kind}")
            k = USER_DATA_KINDS[kind]
            if k == "int":
                vals = r.unpack(f"{n}i", o + 8) if n else ()
            elif k == "float":
                vals = r.unpack(f"{n}f", o + 8) if n else ()
            elif k == "string":
                vals = tuple(self.s(o + 8 + 4 * j, "user data string") for j in range(n))
            elif k == "wstring":
                vals = tuple(self.wstr(o + 8 + 4 * j) for j in range(n))
            else:
                vals = r.raw(o + 8, n, "user data bytes")
            got = self.s(o, "user data name")
            if got != name:
                raise Damaged(f"{what}: user data dictionary key {name!r} != entry name {got!r}")
            out.append(UserData(name, k, vals))
        return out

    def wstr(self, pos: int) -> str:
        """Wide string: 16-bit units up to a 0 unit, big-endian like the rest of the file (UTF-16BE; no Wii U file
        with one has been seen, so materials.to_switch warns when it converts one)."""
        t = self.rel(pos)
        if not t:
            return ""
        d = self.r.data
        end = t
        while end + 1 < len(d) and (d[end] or d[end + 1]):
            end += 2
        self.r.need(t, end + 2 - t, "wide string")
        try:
            return bytes(d[t:end]).decode("utf-16-be")
        except UnicodeDecodeError:
            raise Unsupported(f"wide string at {t:#x} is not valid UTF-16 (big-endian)") from None

    # ---------------------------------------------------------------- model

    def model(self, o: int, name: str) -> Model:
        r = self.r
        if r.raw(o, 4, "FMDL") != b"FMDL":
            raise Damaged(f"model {name!r} at {o:#x} is not an FMDL")
        r.need(o, FMDL_SIZE, "FMDL")
        got = self.s(o + 4, "model name")
        if got != name:
            raise Damaged(f"model dictionary key {name!r} != FMDL name {got!r}")
        path = self.s(o + 8, "model path")
        nv, ns, nm, nu = r.unpack("4H", o + 0x20)
        total = r.u32(o + 0x28)
        if r.u32(o + 0x2C):
            self.warn(f"model {name!r}: user pointer set; ignored")
        skel = self.skeleton(self.rel(o + 0x0C), name)
        varr = self.need_rel(o + 0x10, nv, f"model {name!r} vertex buffers")
        vbufs = [self.fvtx(varr + FVTX_SIZE * i, name, i) for i in range(nv)]
        shapes = [self.fshp(off, sname, name, i, nv) for i, (sname, off) in
                  enumerate(self.group(self.rel(o + 0x14), f"model {name!r} shapes"))]
        mats = [self.fmat(off, mname, name, i) for i, (mname, off) in
                enumerate(self.group(self.rel(o + 0x18), f"model {name!r} materials"))]
        if (len(shapes), len(mats)) != (ns, nm):
            raise Damaged(f"model {name!r}: {len(shapes)} shapes / {len(mats)} materials, counts say {ns} / {nm}")
        ud = self.user_data(self.rel(o + 0x1C), nu, f"model {name!r}")
        return Model(name, path, skel, vbufs, shapes, mats, ud, total)

    def skeleton(self, o: int, model: str) -> Skeleton:
        r = self.r
        if not o or r.raw(o, 4, "FSKL") != b"FSKL":
            raise Damaged(f"model {model!r}: skeleton missing or not an FSKL")
        r.need(o, FSKL_SIZE, "FSKL")
        flags = r.u32(o + 4)
        nb, ns, nr = r.unpack("3H", o + 8)
        what = f"model {model!r} skeleton"
        bdict = self.rel(o + 0x10)
        barr = self.need_rel(o + 0x14, nb, f"{what} bones")
        mtob = self.need_rel(o + 0x18, ns + nr, f"{what} matrix list")
        inv = self.need_rel(o + 0x1C, ns, f"{what} inverse matrices")
        if r.u32(o + 0x20):
            self.warn(f"model {model!r}: skeleton user pointer set; ignored")
        bones = []
        for i in range(nb):
            b = barr + BONE_SIZE * i
            r.need(b, BONE_SIZE, "bone")
            bname = self.s(b, "bone name")
            idx, parent, smooth, rigid, bb, nud, bflags = r.unpack("HhhhHHI", b + 4)
            if idx != i:
                raise Damaged(f"model {model!r}: bone {bname!r} has index {idx}, expected {i}")
            vals = r.unpack("10f", b + 0x14)
            bones.append(Bone(bname, parent, smooth, rigid, bb, bflags, vals[0:3], vals[3:7], vals[7:10],
                              self.user_data(self.rel(b + 0x3C), nud, f"bone {bname!r}")))
        keys = [n for n, _ in self.group(bdict, f"model {model!r} bones")]
        if keys != [b.name for b in bones]:
            raise Damaged(f"model {model!r}: bone dictionary does not match the bone array")
        mlist = list(r.unpack(f"{ns + nr}H", mtob, "matrix list")) if ns + nr else []
        invm = swap_words(r.raw(inv, 48 * ns, "inverse matrices"), 4, "inverse matrices") if ns else b""
        for b in bones:
            if b.smooth_index >= ns or b.rigid_index >= ns + nr:
                raise Damaged(f"model {model!r}: bone {b.name!r} matrix index out of range")
        return Skeleton(flags, bones, mlist, ns, nr, invm)

    def fvtx(self, o: int, model: str, i: int) -> VertexBuffer:
        r = self.r
        if r.raw(o, 4, "FVTX") != b"FVTX":
            raise Damaged(f"model {model!r}: vertex buffer {i} at {o:#x} is not an FVTX")
        r.need(o, FVTX_SIZE, "FVTX")
        na, nb, idx, nvtx, skin = r.unpack("BBHIB", o + 4)
        what = f"model {model!r} vertex buffer {i}"
        aarr, adict = self.need_rel(o + 0x10, na, f"{what} attributes"), self.rel(o + 0x14)
        barr = self.need_rel(o + 0x18, nb, f"{what} buffers")
        attrs = []
        for k in range(na):
            a = aarr + ATTR_SIZE * k
            r.need(a, ATTR_SIZE, "attribute")
            aname = self.s(a, "attribute name")
            buf, off, gx2 = r.u8(a + 4), r.u16(a + 6), r.u32(a + 8)
            if buf >= nb:
                raise Damaged(f"{what}: attribute {aname!r} uses buffer {buf} of {nb}")
            attrs.append(Attribute(aname, attr_format_from_gx2(gx2), off, buf))
        if [n for n, _ in self.group(adict, f"{what} attributes")] != [a.name for a in attrs]:
            raise Damaged(f"{what}: attribute dictionary does not match the attribute array")
        strides, bufs = [], []
        vb_extra = {}
        for k in range(nb):
            b = barr + BUFFER_SIZE * k
            r.need(b, BUFFER_SIZE, "buffer")
            _, size, _, stride, buffering, _ = r.unpack("IIIHHI", b)
            need = stride * nvtx
            data_off = self.need_rel(b + 0x14, need, f"{what} buffer {k} data")
            if size < need:
                raise Damaged(f"{what}: buffer {k} has {size} bytes, stride {stride} x {nvtx} vertices need {need}")
            if size > need:
                self.warn(f"{what}: buffer {k} has {size - need} bytes more than its vertices; ignored")
            if buffering > 1:
                self.warn(f"{what}: buffer {k} has {buffering} copies; the first is used")
                vb_extra["wiiu.buffering"] = buffering
            raw = r.view(data_off, need, f"{what} buffer {k}")
            plan = swap_plan([a for a in attrs if a.buffer == k], stride, what)
            bufs.append(swap_vertices(raw, stride, plan))
            strides.append(stride)
        vb = VertexBuffer(nvtx, skin, attrs, strides, bufs)
        vb.extra.update(vb_extra)
        if idx != i:
            self.warn(f"{what}: section index {idx}")
        return vb

    def fshp(self, o: int, name: str, model: str, i: int, nvbuf: int) -> Shape:
        r = self.r
        if r.raw(o, 4, "FSHP") != b"FSHP":
            raise Damaged(f"model {model!r}: shape {name!r} is not an FSHP")
        r.need(o, FSHP_SIZE, "FSHP")
        if self.s(o + 4, "shape name") != name:
            raise Damaged(f"model {model!r}: shape dictionary key {name!r} != FSHP name")
        flags = r.u32(o + 8)
        idx, mat, bone, vbi, nsk = r.unpack("5H", o + 0x0C)
        skin, nmesh, nks, nta = r.unpack("4B", o + 0x16)
        nnodes = r.u16(o + 0x1A)
        if nks:
            raise Unsupported(f"shape {name!r} has {nks} key shapes (morphs); not converted")
        if vbi >= nvbuf:
            raise Damaged(f"shape {name!r}: vertex buffer {vbi} of {nvbuf}")
        if idx != i:
            self.warn(f"shape {name!r}: section index {idx}, dictionary position {i}")
        what = f"shape {name!r}"
        rad = self.need_rel(o + 0x1C, nmesh, f"{what} radii")
        radii = list(r.unpack(f"{nmesh}f", rad, "radii")) if nmesh else []
        marr = self.need_rel(o + 0x24, nmesh, f"{what} meshes")
        meshes = [self.mesh(marr + MESH_SIZE * k, name) for k in range(nmesh)]
        skarr = self.need_rel(o + 0x28, nsk, f"{what} skin bones")
        skin_bones = list(r.unpack(f"{nsk}H", skarr, "skin bones")) if nsk else []
        nbox = sum(len(m.submeshes) + 1 for m in meshes)
        sh_extra = {}
        if nnodes:
            nodes = self.need_rel(o + 0x30, nnodes, f"{what} bounding nodes")
            bbox = self.need_rel(o + 0x34, nbox, f"{what} bounds")
            r.need(nodes, 12 * nnodes, "bounding nodes")
            if nnodes != nbox:
                raise Unsupported(f"shape {name!r}: {nnodes} bounding nodes for {nbox} meshes + submeshes")
            self.warn(f"shape {name!r}: bounding node tree dropped (the Switch keeps only the boxes)")
            r.need(self.need_rel(o + 0x38, nnodes, f"{what} bounding indices"), 2 * nnodes, "bounding indices")
            sh_extra["wiiu.bounding_nodes"] = nnodes
        else:
            bbox = self.need_rel(o + 0x30, nbox, f"{what} bounds")
        bounds = [r.unpack("6f", bbox + 24 * k) for k in range(nbox)]
        sh = Shape(name, flags, mat, bone, vbi, skin_bones, skin, nta, meshes, bounds, radii)
        sh.extra.update(sh_extra)
        return sh

    def mesh(self, o: int, shape: str) -> Mesh:
        r = self.r
        r.need(o, MESH_SIZE, "mesh")
        prim, ifmt, count, nsub = r.unpack("IIIH", o)
        if prim not in GX2_PRIMITIVE:
            raise Unsupported(f"shape {shape!r}: GX2 primitive mode {prim} (fans, quads and adjacency are refused)")
        if ifmt not in GX2_INDEX:
            raise Unsupported(f"shape {shape!r}: GX2 index format {ifmt}")
        fmt, big = GX2_INDEX[ifmt]
        size = count * (2 if fmt == "u16" else 4)
        sub = self.need_rel(o + 0x10, nsub, f"shape {shape!r} submeshes")
        subs = [r.unpack("II", sub + 8 * k) for k in range(nsub)]
        for a, n in subs:
            if a + n * (size // max(count, 1)) > size:
                raise Damaged(f"shape {shape!r}: submesh {a:#x}+{n} past the {count} indices")
        b = self.need_rel(o + 0x14, 1, f"shape {shape!r} index buffer")
        r.need(b, BUFFER_SIZE, "index buffer")
        _, bsize, _, stride, _, _ = r.unpack("IIIHHI", b)
        if bsize < size:
            raise Damaged(f"shape {shape!r}: index buffer has {bsize} bytes for {count} {fmt} indices")
        raw = r.raw(self.need_rel(b + 0x14, size, f"shape {shape!r} index data"), size, "indices")
        data = swap_words(raw, 2 if fmt == "u16" else 4, "indices") if big else raw
        m = Mesh(GX2_PRIMITIVE[prim], fmt, count, data, r.u32(o + 0x18), subs)
        if not big:
            m.extra["wiiu.index_le"] = True
        if stride != (2 if fmt == "u16" else 4):
            m.extra["wiiu.index_stride"] = stride
        return m

    # ---------------------------------------------------------------- material

    def fmat(self, o: int, name: str, model: str, i: int) -> Material:
        r = self.r
        if r.raw(o, 4, "FMAT") != b"FMAT":
            raise Damaged(f"model {model!r}: material {name!r} is not an FMAT")
        r.need(o, FMAT_SIZE, "FMAT")
        if self.s(o + 4, "material name") != name:
            raise Damaged(f"model {model!r}: material dictionary key {name!r} != FMAT name")
        what = f"material {name!r}"
        flags = r.u32(o + 8)
        idx, nri, nsamp, ntex, nparam, nvol, dsize, rawsize, nud = r.unpack("HHBBHHHHH", o + 0x0C)
        if idx != i:
            self.warn(f"{what}: section index {idx}, dictionary position {i}")
        infos = []
        for iname, a in self.group(self.rel(o + 0x1C), f"{what} render infos"):
            r.need(a, 8, "render info")
            cnt, kind = r.u16(a), r.u8(a + 2)
            if kind not in RENDER_INFO_KINDS:
                raise Unsupported(f"{what}: render info {iname!r} has type {kind}")
            k = RENDER_INFO_KINDS[kind]
            if k == "string":
                vals = tuple(self.s(a + 8 + 4 * j, "render info value") for j in range(cnt))
            else:
                vals = r.unpack(f"{cnt}{'i' if k == 'int' else 'f'}", a + 8) if cnt else ()
            if self.s(a + 4, "render info name") != iname:
                raise Damaged(f"{what}: render info dictionary key {iname!r} != entry name")
            infos.append(RenderInfo(iname, k, tuple(vals)))
        if len(infos) != nri:
            raise Damaged(f"{what}: {len(infos)} render infos, the count says {nri}")
        rs_off = self.rel(o + 0x20)
        rs = None
        if rs_off:
            v = r.unpack("IIIIfIII4f", rs_off, "render state")
            rs = RenderState(*v[:8], tuple(v[8:12]))
        shader = self.assign(self.rel(o + 0x24), what)
        tref = self.need_rel(o + 0x28, ntex, f"{what} texture refs")
        textures = []
        for k in range(ntex):
            r.need(tref + 8 * k, 8, "texture ref")
            textures.append(self.s(tref + 8 * k, "texture name"))
            if r.s32(tref + 8 * k + 4):
                self.rel(tref + 8 * k + 4)       # an FTEX of this file; textures are bound by name on Switch
        sarr = self.need_rel(o + 0x2C, nsamp, f"{what} samplers")
        samplers = []
        for k in range(nsamp):
            a = sarr + SAMPLER_SIZE * k
            r.need(a, SAMPLER_SIZE, "sampler")
            sname = self.s(a + 0x10, "sampler name")
            st = materials.sampler_from_gx2(r.unpack("3I", a), self.warnings, f"{what} sampler {sname!r}")
            samplers.append(Sampler(sname, st))
        if [n for n, _ in self.group(self.rel(o + 0x30), f"{what} samplers")] != [s.name for s in samplers]:
            raise Damaged(f"{what}: sampler dictionary does not match the sampler array")
        parr = self.need_rel(o + 0x34, nparam, f"{what} params")
        params = []
        for k in range(nparam):
            a = parr + PARAM_SIZE * k
            r.need(a, PARAM_SIZE, "shader param")
            typ, size, off, uni, _cb, dep1, dep2 = r.unpack("BBHiIHH", a)
            pname = self.s(a + 0x10, "param name")
            if off + size > dsize:
                raise Damaged(f"{what}: shader param {pname!r} at {off}+{size} is past the param data ({dsize})")
            p = Param(pname, typ, off, size, dep1, dep2)
            if uni != -1:
                p.extra["uniform"] = uni
            params.append(p)
        if [n for n, _ in self.group(self.rel(o + 0x38), f"{what} params")] != [p.name for p in params]:
            raise Damaged(f"{what}: shader param dictionary does not match the param array")
        pdata = (swap_words(r.raw(self.need_rel(o + 0x3C, dsize, f"{what} param data"), dsize, "param data"), 4,
                            f"{what} param data") if dsize else b"")
        if any(p.type == PARAM_TEXSRT_EX for p in params):
            pdata = self.texsrt_ex(params, pdata, what)
        ud = self.user_data(self.rel(o + 0x40), nud, what)
        vol_off = self.need_rel(o + 0x44, nvol, f"{what} volatile flags")
        volatile = []
        if vol_off and nparam:
            vb = r.raw(vol_off, (nparam + 7) // 8, "volatile flags")
            volatile = [k for k in range(nparam) if (vb[k >> 3] >> (k & 7)) & 1]
        if len(volatile) != nvol:
            self.warn(f"{what}: {len(volatile)} volatile flags set, the count says {nvol}")
        mat = Material(name, flags, infos, shader, samplers, textures, params, pdata, volatile, ud)
        if rs is not None:
            mat.extra["wiiu.render_state"] = rs
        if rawsize:
            mat.extra["wiiu.raw_param_size"] = rawsize
        return mat

    def texsrt_ex(self, params: list, pdata: bytes, what: str) -> bytes:
        """TexSrtEx params (31: a TexSrt and a 4-byte run-time matrix pointer, 28 bytes) become TexSrt (30, 24
        bytes), as every Switch material has them: the pointer is dropped and the params after it move down 4 bytes.
        Returns the new param data and changes `params` in place."""
        cuts = []
        for p in params:
            if p.type == PARAM_TEXSRT_EX:
                if p.size != TEXSRT_EX_SIZE:
                    raise Unsupported(f"{what}: TexSrtEx param {p.name!r} has size {p.size}, not {TEXSRT_EX_SIZE}")
                cuts.append((p.offset + TEXSRT_SIZE, p))
        cuts.sort(key=lambda c: c[0])
        for c, owner in cuts:
            for q in params:
                if q is not owner and q.offset < c + 4 and q.offset + q.size > c:
                    raise Damaged(f"{what}: shader param {q.name!r} overlaps the pointer of TexSrtEx {owner.name!r}")
        out, pos = bytearray(), 0
        for c, _ in cuts:
            out += pdata[pos:c]
            pos = c + 4
        out += pdata[pos:]
        for q in params:
            q.offset -= 4 * sum(1 for c, _ in cuts if c + 4 <= q.offset)
        for _, p in cuts:
            p.type, p.size = PARAM_TEXSRT, TEXSRT_SIZE
        self.warn(f"{what}: {len(cuts)} TexSrtEx param(s) ({', '.join(p.name for _, p in cuts[:4])}) became TexSrt "
                  f"as on the Switch (run-time matrix pointer dropped)")
        return bytes(out)

    def assign(self, o: int, what: str) -> ShaderAssign:
        r = self.r
        if not o:
            raise Damaged(f"{what}: no shader assign")
        r.need(o, ASSIGN_SIZE, "shader assign")
        rev, na, ns, no = r.unpack("IBBH", o + 8)
        out = []
        for k, (n, label) in enumerate(((na, "attribute assign"), (ns, "sampler assign"), (no, "shader option"))):
            ents = self.group(self.rel(o + 0x10 + 4 * k), f"{what} {label}", null_data_ok=True)
            if len(ents) != n:
                raise Damaged(f"{what}: {len(ents)} {label} entries, the count says {n}")
            out.append([(key, r.cstr(off, label) if off else "") for key, off in ents])
        return ShaderAssign(self.s(o, "shader archive"), self.s(o + 4, "shading model"), rev, *out)


def read(data, warnings: list | None = None, cover: bool = False) -> ResFile:
    """Neutral ResFile of a decompressed Wii U model BFRES (4.5). Warnings (dropped or doubtful parts) go to
    `warnings`. Materials keep their RenderState in extra['wiiu.render_state']; call materials.to_switch on each model
    before writing it for the Switch."""
    return _Read(data, [] if warnings is None else warnings, cover).file()


def uncovered(data) -> list[tuple[int, int]]:
    """Non-zero byte ranges that the reader never touched (used by the Wii U self-check and on real samples)."""
    rd = _Read(data, [], True)
    rd.file()
    return rd.r.uncovered()


def string_pool(data) -> list[str]:
    """Strings of the pool in file order (u32 length, characters, NUL, 4-byte padding)."""
    r = Reader(data, ">", "Wii U BFRES")
    size, pos = r.u32(0x18), r.s32(0x1C)
    o = 0x1C + pos if pos else 0
    end, out = o + size, []
    while o + 4 <= end:
        n = r.u32(o)
        out.append(bytes(r.raw(o + 4, n)).decode("utf-8", "replace"))
        o = align(o + 4 + n + 1, 4)
    return out


def self_check(data) -> list[str]:
    """Problems of a Wii U model file (empty = passes): it re-reads without warnings, every offset is inside the file,
    every non-zero byte is read by someone (coverage), the string pool is sorted, the size field is right."""
    out = []
    try:
        warn: list[str] = []
        rd = _Read(data, warn, True)
        rd.file()
        out += [f"warning: {w}" for w in warn]
        unc = rd.r.uncovered()
        if unc:
            out.append(f"bytes nobody reads: {[(hex(a), hex(b)) for a, b in unc[:4]]}")
        pool = string_pool(data)
        if pool != sorted(pool, key=lambda x: x.encode("utf-8")):
            out.append("string pool is not sorted")
        if struct.unpack_from(">I", data, 0x0C)[0] != len(data):
            out.append("file size field is wrong")
    except (Damaged, Unsupported, struct.error, ValueError) as e:
        out.append(f"{type(e).__name__}: {e}")
    return out


# --------------------------------------------------------------------------------------------------------- writer

class _Write:
    """Wii U file layout (tests only): header, groups and structures in model order, the sorted string pool, then the
    data blocks (vertex and index buffers, external files) each aligned to 0x40."""

    def __init__(self, res: ResFile, keep_render_infos: bool, notes: list):
        self.res = res
        self.keep = keep_render_infos
        self.notes = notes
        self.buf = bytearray()
        self.rels: dict[int, int] = {}
        self.srels: list[tuple[int, str]] = []
        self.blocks: list[tuple[int, bytes]] = []   # (field position, data) placed after the strings

    def alloc(self, n: int, a: int = 4) -> int:
        self.buf.extend(bytes(-len(self.buf) % a))
        off = len(self.buf)
        self.buf.extend(bytes(n))
        return off

    def put(self, fmt: str, off: int, *vals) -> None:
        struct.pack_into(">" + fmt, self.buf, off, *vals)

    def rel(self, pos: int, target: int) -> None:
        if target:
            self.rels[pos] = target

    def rs(self, pos: int, s: str) -> None:
        self.srels.append((pos, s))

    def data(self, pos: int, data: bytes) -> None:
        self.blocks.append((pos, bytes(data)))

    def group(self, entries: list[tuple[str, int]]) -> int:
        """Index group of (name, data offset) entries; returns its offset."""
        n = len(entries)
        off = self.alloc(8 + 16 * (n + 1))
        self.put("Ii", off, 8 + 16 * (n + 1), n)
        nodes = dicts.build([k.encode("utf-8") for k, _ in entries])
        for i, (ref, left, right) in enumerate(nodes):
            e = off + 8 + 16 * i
            self.put("IHH", e, ref & 0xFFFFFFFF, left, right)
            if i:
                self.rs(e + 8, entries[i - 1][0])
                self.rel(e + 12, entries[i - 1][1])
        return off

    def string_group(self, pairs: list[tuple[str, str]]) -> int:
        """Index group whose values are strings (shader assign): the data offset points at the string."""
        n = len(pairs)
        off = self.alloc(8 + 16 * (n + 1))
        self.put("Ii", off, 8 + 16 * (n + 1), n)
        nodes = dicts.build([k.encode("utf-8") for k, _ in pairs])
        for i, (ref, left, right) in enumerate(nodes):
            e = off + 8 + 16 * i
            self.put("IHH", e, ref & 0xFFFFFFFF, left, right)
            if i:
                self.rs(e + 8, pairs[i - 1][0])
                self.rs(e + 12, pairs[i - 1][1])
        return off

    # ---------------------------------------------------------------- file

    def file(self) -> bytes:
        res = self.res
        self.alloc(HEADER_SIZE)
        self.buf[0:4] = b"FRES"
        fmdl = [self.alloc(FMDL_SIZE) for _ in res.models]
        mgroup = self.group([(m.name, o) for m, o in zip(res.models, fmdl)]) if res.models else 0
        for m, o in zip(res.models, fmdl):
            self.model(o, m)
        anim_groups: dict[int, list] = {}
        for a in res.skel_anims + res.mat_anims:
            why = wiiu_anims.unwritable(a)
            if why:
                self.notes.append(f"animation {a.name!r} left out of the Wii U file: it {why}")
                continue
            if isinstance(a, SkelAnim):
                anim_groups.setdefault(2, []).append((a.name, wiiu_anims.write_fska(self, a)))
            else:
                g = wiiu_anims.wiiu_group(a)
                write = wiiu_anims.write_ftxp if g == wiiu_anims.FTXP_GROUP else wiiu_anims.write_fshu
                anim_groups.setdefault(g, []).append((a.name, write(self, a)))
        for g, ents in anim_groups.items():
            self.rel(0x20 + 4 * g, self.group(ents))
        ext = []
        for e in res.external:
            if e.name in SWITCH_ONLY_EXTERNAL:
                self.notes.append(f"{e.name} left out of the Wii U file (Switch model files only)")
                continue
            o = self.alloc(8)
            self.put("I", o + 4, len(e.data))
            self.data(o, e.data)
            ext.append((e.name, o))
        egroup = self.group(ext) if ext else 0
        self.rs(0x14, res.name)
        self.rel(0x20, mgroup)
        self.rel(0x4C, egroup)
        counts = [len(res.models)] + [len(anim_groups.get(g, ())) for g in range(1, 11)] + [len(ext)]
        self.put("12H", 0x50, *counts)
        # string pool (sorted, u32 length + chars + NUL, 4-aligned; an offset points at the characters)
        strings = sorted({s for _, s in self.srels}, key=lambda x: x.encode("utf-8"))
        pool = self.alloc(0)
        soff = {}
        for s in strings:
            b = s.encode("utf-8")
            o = self.alloc(4 + len(b) + 1)
            self.put("I", o, len(b))
            self.buf[o + 4:o + 4 + len(b)] = b
            soff[s] = o + 4
        self.alloc(0)
        self.put("I", 0x18, len(self.buf) - pool)
        self.rel(0x1C, pool)
        for pos, s in self.srels:
            self.rels[pos] = soff[s]
        for pos, d in self.blocks:
            o = self.alloc(len(d), DATA_ALIGN)
            self.buf[o:o + len(d)] = d
            self.rel(pos, o)
        for pos, tgt in self.rels.items():
            self.put("i", pos, tgt - pos)
        self.put("IHHI", 4, self.res.extra.get("wiiu.version", VERSION), 0xFEFF, 0x10, len(self.buf))
        self.put("I", 0x10, self.res.extra.get("wiiu.alignment", 0x2000))
        return bytes(self.buf)

    def user_data(self, uds: list[UserData]) -> int:
        if not uds:
            return 0
        offs = []
        for u in uds:
            kind = u.kind
            if kind not in USER_DATA_CODE:
                raise Unsupported(f"user data {u.name!r}: type {kind!r} is not written")
            n = len(u.values)
            size = n if kind == "bytes" else 4 * n
            o = self.alloc(8 + size)
            self.rs(o, u.name)
            self.put("HB", o + 4, n, USER_DATA_CODE[kind])
            if kind == "int":
                self.put(f"{n}i", o + 8, *u.values)
            elif kind == "float":
                self.put(f"{n}f", o + 8, *u.values)
            elif kind == "string":
                for j, v in enumerate(u.values):
                    self.rs(o + 8 + 4 * j, v)
            elif kind == "wstring":                     # UTF-16BE characters and a 0 unit, as the reader takes them
                for j, v in enumerate(u.values):
                    enc = v.encode("utf-16-be")
                    w = self.alloc(len(enc) + 2)
                    self.buf[w:w + len(enc)] = enc
                    self.rel(o + 8 + 4 * j, w)
            else:
                self.buf[o + 8:o + 8 + n] = bytes(u.values)
            offs.append((u.name, o))
        return self.group(offs)

    # ---------------------------------------------------------------- model

    def model(self, o: int, m: Model) -> None:
        self.buf[o:o + 4] = b"FMDL"
        self.rs(o + 4, m.name)
        self.rs(o + 8, m.path)
        self.put("4HI", o + 0x20, len(m.vertex_buffers), len(m.shapes), len(m.materials), len(m.user_data),
                 m.total_vertex_count)
        self.rel(o + 0x0C, self.skeleton(m.skeleton, m.name))
        varr = self.alloc(FVTX_SIZE * len(m.vertex_buffers))
        if m.vertex_buffers:
            self.rel(o + 0x10, varr)
        for i, vb in enumerate(m.vertex_buffers):
            self.fvtx(varr + FVTX_SIZE * i, vb, i)
        shapes = [(s.name, self.fshp(s, i, varr)) for i, s in enumerate(m.shapes)]
        self.rel(o + 0x14, self.group(shapes) if shapes else 0)
        mats = [(x.name, self.fmat(x, i)) for i, x in enumerate(m.materials)]
        self.rel(o + 0x18, self.group(mats) if mats else 0)
        self.rel(o + 0x1C, self.user_data(m.user_data))

    def skeleton(self, sk: Skeleton, model: str) -> int:
        o = self.alloc(FSKL_SIZE)
        self.buf[o:o + 4] = b"FSKL"
        self.put("I3H", o + 4, sk.flags, len(sk.bones), sk.smooth_count, sk.rigid_count)
        barr = self.alloc(BONE_SIZE * len(sk.bones))
        for i, b in enumerate(sk.bones):
            bo = barr + BONE_SIZE * i
            self.rs(bo, b.name)
            self.put("HhhhHHI", bo + 4, i, b.parent, b.smooth_index, b.rigid_index, b.billboard_index,
                     len(b.user_data), b.flags)
            self.put("10f", bo + 0x14, *b.scale, *b.rotation, *b.translation)
        for i, b in enumerate(sk.bones):
            self.rel(barr + BONE_SIZE * i + 0x3C, self.user_data(b.user_data))
        self.rel(o + 0x10, self.group([(b.name, barr + BONE_SIZE * i) for i, b in enumerate(sk.bones)]))
        self.rel(o + 0x14, barr)
        n = sk.smooth_count + sk.rigid_count
        if n:
            mt = self.alloc(2 * n)
            self.put(f"{n}H", mt, *sk.matrix_to_bone)
            self.rel(o + 0x18, mt)
        if sk.smooth_count:
            inv = self.alloc(len(sk.inverse_matrices))
            self.buf[inv:inv + len(sk.inverse_matrices)] = swap_words(sk.inverse_matrices, 4, "inverse matrices")
            self.rel(o + 0x1C, inv)
        return o

    def fvtx(self, o: int, vb: VertexBuffer, i: int) -> None:
        self.buf[o:o + 4] = b"FVTX"
        nb = len(vb.buffers)
        self.put("BBHIB", o + 4, len(vb.attributes), nb, i, vb.vertex_count, vb.skin_count)
        aarr = self.alloc(ATTR_SIZE * len(vb.attributes))
        for k, a in enumerate(vb.attributes):
            ao = aarr + ATTR_SIZE * k
            self.rs(ao, a.name)
            self.put("BxHI", ao + 4, a.buffer, a.offset, attr_format_to_gx2(a.format))
        self.rel(o + 0x10, aarr)
        self.rel(o + 0x14, self.group([(a.name, aarr + ATTR_SIZE * k) for k, a in enumerate(vb.attributes)]))
        barr = self.alloc(BUFFER_SIZE * nb)
        self.rel(o + 0x18, barr)
        for k, (stride, data) in enumerate(zip(vb.strides, vb.buffers)):
            b = barr + BUFFER_SIZE * k
            self.put("IIIHHI", b, 0, len(data), 0, stride, 1, 0)
            plan = swap_plan([a for a in vb.attributes if a.buffer == k], stride, f"vertex buffer {i}")
            self.data(b + 0x14, swap_vertices(data, stride, plan))

    def fshp(self, sh: Shape, i: int, varr: int) -> int:
        if sh.key_shapes:
            raise Unsupported(f"shape {sh.name!r} has key shapes")
        o = self.alloc(FSHP_SIZE)
        self.buf[o:o + 4] = b"FSHP"
        self.rs(o + 4, sh.name)
        self.put("I5H4BH", o + 8, sh.flags, i, sh.material, sh.bone, sh.vertex_buffer, len(sh.skin_bones),
                 sh.skin_count, len(sh.meshes), 0, sh.target_attrib_count, 0)
        rad = self.alloc(4 * len(sh.radii))
        self.put(f"{len(sh.radii)}f", rad, *sh.radii)
        self.rel(o + 0x1C, rad)
        self.rel(o + 0x20, varr + FVTX_SIZE * sh.vertex_buffer)
        marr = self.alloc(MESH_SIZE * len(sh.meshes))
        self.rel(o + 0x24, marr)
        for k, mesh in enumerate(sh.meshes):
            self.mesh(marr + MESH_SIZE * k, mesh, sh.name)
        if sh.skin_bones:
            sk = self.alloc(2 * len(sh.skin_bones))
            self.put(f"{len(sh.skin_bones)}H", sk, *sh.skin_bones)
            self.rel(o + 0x28, sk)
        bb = self.alloc(24 * len(sh.bounds))
        for k, box in enumerate(sh.bounds):
            self.put("6f", bb + 24 * k, *box)
        self.rel(o + 0x30, bb)
        return o

    def mesh(self, o: int, mesh: Mesh, shape: str) -> None:
        if mesh.primitive not in GX2_PRIMITIVE_CODE or mesh.index_format not in GX2_INDEX_CODE:
            raise Unsupported(f"shape {shape!r}: {mesh.primitive} / {mesh.index_format} indices have no GX2 form")
        le = mesh.extra.get("wiiu.index_le", False)
        code = GX2_INDEX_CODE[mesh.index_format] if not le else (0 if mesh.index_format == "u16" else 1)
        isz = 2 if mesh.index_format == "u16" else 4
        self.put("IIIH", o, GX2_PRIMITIVE_CODE[mesh.primitive], code, mesh.index_count, len(mesh.submeshes))
        self.put("I", o + 0x18, mesh.first_vertex)
        sub = self.alloc(8 * len(mesh.submeshes))
        for j, (a, n) in enumerate(mesh.submeshes):
            self.put("II", sub + 8 * j, a, n)
        self.rel(o + 0x10, sub)
        b = self.alloc(BUFFER_SIZE)
        self.put("IIIHHI", b, 0, len(mesh.indices), 0, mesh.extra.get("wiiu.index_stride", isz), 1, 0)
        self.rel(o + 0x14, b)
        self.data(b + 0x14, mesh.indices if le else swap_words(mesh.indices, isz, "indices"))

    def fmat(self, mat: Material, i: int) -> int:
        what = f"material {mat.name!r}"
        o = self.alloc(FMAT_SIZE)
        self.buf[o:o + 4] = b"FMAT"
        self.rs(o + 4, mat.name)
        rs = mat.extra.get("wiiu.render_state")
        if rs is None:
            rs = materials.render_state_from_infos(mat.render_infos, self.notes, what)
        infos = list(mat.render_infos)
        if not self.keep:
            infos = [r for r in infos if r.name not in materials.RENDER_STATE_NAMES]
        npar = len(mat.params)
        self.put("IHHBBHHHHH", o + 8, mat.flags, i, len(infos), len(mat.samplers), len(mat.textures), npar,
                 len(mat.volatile), len(mat.param_data), mat.extra.get("wiiu.raw_param_size", 0), len(mat.user_data))
        ri = []
        for info in infos:
            n = len(info.values)
            a = self.alloc(8 + 4 * n)
            self.put("HB", a, n, RENDER_INFO_CODE[info.kind])
            self.rs(a + 4, info.name)
            if info.kind == "string":
                for j, v in enumerate(info.values):
                    self.rs(a + 8 + 4 * j, v)
            elif n:
                self.put(f"{n}{'i' if info.kind == 'int' else 'f'}", a + 8, *info.values)
            ri.append((info.name, a))
        self.rel(o + 0x1C, self.group(ri) if ri else 0)
        st = self.alloc(RSTATE_SIZE)
        self.put("IIIIfIII4f", st, rs.flags, rs.polygon_control, rs.depth_control, rs.alpha_control, rs.alpha_ref,
                 rs.color_control, rs.blend_target, rs.blend_control, *rs.blend_color)
        self.rel(o + 0x20, st)
        self.rel(o + 0x24, self.assign(mat.shader))
        if mat.textures:
            tr = self.alloc(8 * len(mat.textures))
            for k, t in enumerate(mat.textures):
                self.rs(tr + 8 * k, t)
            self.rel(o + 0x28, tr)
        if mat.samplers:
            sa = self.alloc(SAMPLER_SIZE * len(mat.samplers))
            for k, s in enumerate(mat.samplers):
                a = sa + SAMPLER_SIZE * k
                self.put("3I", a, *materials.sampler_to_gx2(s.state, self.notes, f"{what} sampler {s.name!r}"))
                self.rs(a + 0x10, s.name)
                self.put("B", a + 0x14, k)
            self.rel(o + 0x2C, sa)
            self.rel(o + 0x30, self.group([(s.name, sa + SAMPLER_SIZE * k) for k, s in enumerate(mat.samplers)]))
        if mat.params:
            pa = self.alloc(PARAM_SIZE * npar)
            for k, p in enumerate(mat.params):
                a = pa + PARAM_SIZE * k
                self.put("BBHiIHH", a, p.type, p.size, p.offset, p.extra.get("uniform", -1), 0, p.depended, p.depend)
                self.rs(a + 0x10, p.name)
            self.rel(o + 0x34, pa)
            self.rel(o + 0x38, self.group([(p.name, pa + PARAM_SIZE * k) for k, p in enumerate(mat.params)]))
        if mat.param_data:
            pd = self.alloc(len(mat.param_data))
            self.buf[pd:pd + len(mat.param_data)] = swap_words(mat.param_data, 4, f"{what} param data")
            self.rel(o + 0x3C, pd)
        self.rel(o + 0x40, self.user_data(mat.user_data))
        if npar:
            vf = self.alloc((npar + 7) // 8)
            for k in mat.volatile:
                self.buf[vf + (k >> 3)] |= 1 << (k & 7)
            self.rel(o + 0x44, vf)
        return o

    def assign(self, sa: ShaderAssign) -> int:
        o = self.alloc(ASSIGN_SIZE)
        self.rs(o, sa.archive)
        self.rs(o + 4, sa.model)
        self.put("IBBH", o + 8, sa.revision, len(sa.attribs), len(sa.samplers), len(sa.options))
        for k, pairs in enumerate((sa.attribs, sa.samplers, sa.options)):
            if pairs:
                self.rel(o + 0x10 + 4 * k, self.string_group(pairs))
        return o


def write(res: ResFile, keep_render_infos: bool = True, notes: list | None = None) -> bytes:
    """Wii U model BFRES for a neutral ResFile (FOR TESTS ONLY: Switch -> Wii U -> Switch). Animations are written as
    FSKA / FSHU / FTXP (wiiu_anims); one without a Wii U form and the Switch-only TexInfo.txt are left out (noted). Materials get a RenderState made from their render infos (or the one
    read from a Wii U file); `keep_render_infos=False` also drops the 16 render infos it replaces, as a real Wii U
    material has them."""
    return _Write(res, keep_render_infos, [] if notes is None else notes).file()
