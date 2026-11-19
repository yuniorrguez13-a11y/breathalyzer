"""glTF 2.0 (.gltf, .glb) and VRM 0.x / 1.0 (.vrm) -> neutral Scene. Pure standard library.

Written from the public Khronos glTF 2.0 spec and the public VRM 0.x / VRMC_vrm 1.0 specs.

- Buffers: the GLB BIN chunk, base64 ``data:`` URIs, and files next to the .gltf. Online (http) URIs are never
  fetched. Draco / meshopt-compressed models are refused in one sentence (re-export without compression).
- Accessors: every component type, normalized ints, byteStride, matrix column padding, sparse accessors, and
  accessors with no bufferView (zeros).
- Meshes: one Mesh per primitive; triangles, strips and fans (degenerate strip joins dropped); points/lines
  dropped with a note. Unskinned meshes are moved to world space; a mesh under a bone node is stuck to that bone.
- Skeleton: every skin joint (plus VRM humanoid nodes) and their ancestors, parents first. Skinned vertices are
  moved into the rest pose when the file's bind pose differs (``fix_bind=True``), so bind = rest.
- Weights: JOINTS_0/1 + WEIGHTS_0/1, top 4 kept and renormalised.
- Morph targets: kept in ``meta["morphs"]`` (not applied). KHR_texture_transform: kept in material extras, noted.
- VRM: humanoid map -> ``meta["humanoid"]`` {vrm bone name: bone index}; licence -> ``meta["licence"]``; MToon ->
  Material(shader="mtoon", shade_color=...); expressions / spring bone roots in ``meta["vrm"]``.
"""

from __future__ import annotations

import base64
import json
import math
import struct
from pathlib import Path
from urllib.parse import unquote

from .scene import (IDENTITY, Bone, Material, Mesh, ReadError, Scene, Texture, mat_close, mat_det3,
                    mat_dir, mat_from_trs, mat_inverse, mat_mul, mat_point, new_meta, normal_matrix, normalize,
                    read_file, skin_to_rest, smooth_normals, top4, trs_from_mat)

GLB_MAGIC = b"glTF"
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
MAX_EMPTY_ACCESSOR = 1 << 25  # numbers in an accessor with no buffer view (sparse-only): ~33M is far past any model

COMP = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
NORM_DIV = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0, 5125: 4294967295.0}
COMPRESSED = {"KHR_draco_mesh_compression": "Draco", "EXT_meshopt_compression": "meshopt",
              "KHR_meshopt_compression": "meshopt"}
IMAGE_EXTS = ("KHR_texture_basisu", "EXT_texture_webp", "EXT_texture_avif", "MSFT_texture_dds")
MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".ktx2": "image/ktx2", ".dds": "image/vnd-ms.dds", ".avif": "image/avif", ".tga": "image/x-tga"}

VRM0_USERS = {"OnlyAuthor": "author_only", "ExplicitlyLicensedPerson": "licensed_people", "Everyone": "everyone"}
VRM1_USERS = {"onlyAuthor": "author_only", "onlySeparatelyLicensedPerson": "licensed_people", "everyone": "everyone"}
VRM1_COMMERCIAL = {"personalNonProfit": "not_allowed", "personalProfit": "personal_profit",
                   "corporation": "allowed"}
VRM1_MODIFICATION = {"prohibited": "prohibited", "allowModification": "allowed",
                     "allowModificationRedistribution": "allowed_and_share"}


# ----------------------------------------------------------------------------------------------- file level

def parse_glb(data: bytes) -> tuple:
    """GLB bytes -> (json dict, BIN chunk bytes or None)."""
    if len(data) < 20 or data[:4] != GLB_MAGIC:
        raise ReadError("This isn't a GLB file (it doesn't start with 'glTF').")
    version, length = struct.unpack_from("<II", data, 4)
    if version != 2:
        raise ReadError(f"This is a glTF {version} file; only glTF 2 is supported.")
    length = min(length, len(data))
    pos, doc, binc = 12, None, None
    while pos + 8 <= length:
        clen, ctype = struct.unpack_from("<II", data, pos)
        body = data[pos + 8: pos + 8 + clen]
        if len(body) < clen:
            raise ReadError("The GLB file is cut short.")
        if ctype == CHUNK_JSON and doc is None:
            doc = _json(body)
        elif ctype == CHUNK_BIN and binc is None:
            binc = body
        pos += 8 + clen + (-clen % 4 if pos + 8 + clen < length and clen % 4 else 0)
    if doc is None:
        raise ReadError("The GLB file has no JSON part.")
    return doc, binc


def _json(raw: bytes) -> dict:
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise ReadError(f"The model's JSON is broken ({e.__class__.__name__}).") from None
    if not isinstance(doc, dict):
        raise ReadError("The model's JSON isn't an object.")
    return doc


def read(path, *, fix_bind: bool = True, scene: int | None = None) -> Scene:
    p = Path(path)
    data = read_file(p)
    fmt = "vrm" if p.suffix.lower() == ".vrm" else ("glb" if data[:4] == GLB_MAGIC else "gltf")
    return read_bytes(data, base_dir=p.parent, fmt=fmt, fix_bind=fix_bind, scene=scene)


def read_bytes(data: bytes, *, base_dir=None, fmt: str = "", fix_bind: bool = True, scene: int | None = None) -> Scene:
    if data[:4] == GLB_MAGIC:
        doc, binc = parse_glb(data)
        fmt = fmt or "glb"
    else:
        doc, binc = _json(data), None
        fmt = fmt or "gltf"
    try:
        return _Reader(doc, binc, Path(base_dir) if base_dir else None, fmt, fix_bind, scene).run()
    except ReadError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, struct.error, OSError) as e:
        raise ReadError(f"The model file is broken or unusual ({e.__class__.__name__}: {str(e)[:80]}).") from e


# ----------------------------------------------------------------------------------------------- reader

class _Reader:
    def __init__(self, doc, binc, base_dir, fmt, fix_bind, scene_index):
        self.doc = doc
        self.binc = binc
        self.base_dir = base_dir
        self.fmt = fmt
        self.fix_bind = fix_bind
        self.scene_index = scene_index
        self._buffers = {}
        self._acc_cache = {}
        ver = str((doc.get("asset") or {}).get("version", "2.0"))
        if not ver.startswith("2"):
            raise ReadError(f"This is a glTF {ver} file; only glTF 2 is supported.")
        required = doc.get("extensionsRequired") or []
        for ext in required:
            if ext in COMPRESSED:
                raise ReadError(f"This model is {COMPRESSED[ext]}-compressed; export it again without compression.")
        ext = doc.get("extensions") or {}
        self.vrm0 = ext.get("VRM") if isinstance(ext.get("VRM"), dict) else None
        self.vrm1 = ext.get("VRMC_vrm") if isinstance(ext.get("VRMC_vrm"), dict) else None
        source = "vrm1" if self.vrm1 else ("vrm0" if self.vrm0 else fmt)
        self.out = Scene(meta=new_meta(source, forward="-Z" if (self.vrm0 and not self.vrm1) else "+Z"))
        self.out.meta["extensions_used"] = list(doc.get("extensionsUsed") or [])
        for e in required:
            if e not in COMPRESSED and e not in KNOWN_EXTS:
                self.out.note(f"The model needs the {e} extension, which isn't read; it may look wrong.")

    def _list(self, key):
        v = self.doc.get(key)
        return v if isinstance(v, list) else []

    # ---- buffers and accessors

    def buffer(self, i: int) -> bytes:
        if i in self._buffers:
            return self._buffers[i]
        bufs = self._list("buffers")
        if not 0 <= i < len(bufs):
            raise ReadError(f"The model points at buffer {i}, which doesn't exist.")
        uri = bufs[i].get("uri")
        if uri is None:
            if self.binc is None:
                raise ReadError(f"Buffer {i} has no data (no GLB binary part).")
            data = self.binc
        else:
            data = self._uri_bytes(uri, f"buffer {i}")
        need = bufs[i].get("byteLength", len(data))
        if len(data) < need:
            raise ReadError(f"Buffer {i} is cut short ({len(data)} of {need} bytes).")
        self._buffers[i] = data
        return data

    def _uri_bytes(self, uri: str, what: str) -> bytes:
        if uri.startswith("data:"):
            head, _, body = uri.partition(",")
            if head.endswith(";base64"):
                try:
                    return base64.b64decode(body, validate=False)
                except ValueError:
                    raise ReadError(f"The model's {what} has broken base64 data.") from None
            return unquote(body).encode("latin-1")
        return self._uri_path(uri, what).read_bytes()

    def _uri_path(self, uri: str, what: str) -> Path:
        if "://" in uri or uri.startswith("//"):
            raise ReadError(f"The model's {what} is online ({uri.split('://')[0]}); Breathalyzer never downloads. "
                            "Save the files next to the model.")
        if self.base_dir is None:
            raise ReadError(f"The model's {what} is a separate file, but there's no folder to find it in.")
        p = self.base_dir / unquote(uri)
        if not p.is_file():
            raise ReadError(f"The model's {what} file {unquote(uri)!r} is missing next to the model.")
        return p

    def _view(self, i: int):
        views = self._list("bufferViews")
        if not 0 <= i < len(views):
            raise ReadError(f"The model points at buffer view {i}, which doesn't exist.")
        bv = views[i]
        buf = self.buffer(bv.get("buffer", 0))
        start, length = bv.get("byteOffset", 0), bv.get("byteLength", 0)
        if not (isinstance(start, int) and isinstance(length, int) and start >= 0 and length >= 0):
            raise ReadError(f"Buffer view {i} has a broken offset or length.")
        end = start + length
        if end > len(buf):
            raise ReadError(f"Buffer view {i} runs past the end of its buffer.")
        return bv, buf, start, end

    def view_bytes(self, i: int) -> bytes:
        _, buf, start, end = self._view(i)
        return bytes(buf[start:end])

    def _read_flat(self, view_i, offset, ctype, atype, count, tight=False) -> list:
        fmt, size = COMP[ctype]
        n = NCOMP[atype]
        bv, buf, start, end = self._view(view_i)
        base = start + offset
        rows = {"MAT2": 2, "MAT3": 3, "MAT4": 4}.get(atype, 0)
        col = rows * size if rows else 0
        padded = bool(rows) and col % 4 != 0
        elem = (col + (-col % 4)) * rows if padded else n * size
        stride = elem if tight else (bv.get("byteStride") or elem)
        if not (isinstance(offset, int) and offset >= 0 and isinstance(stride, int) and stride > 0):
            raise ReadError(f"An accessor in buffer view {view_i} has a broken offset or stride.")
        if count and base + stride * (count - 1) + elem > end:
            raise ReadError(f"An accessor in buffer view {view_i} runs past its end.")
        if not padded and stride == elem:
            return list(struct.unpack_from(f"<{count * n}{fmt}", buf, base))
        out = []
        if padded:
            cfmt = f"<{rows}{fmt}"
            step = col + (-col % 4)
            for k in range(count):
                at = base + k * stride
                for c in range(rows):
                    out.extend(struct.unpack_from(cfmt, buf, at + c * step))
        else:
            efmt = f"<{n}{fmt}"
            for k in range(count):
                out.extend(struct.unpack_from(efmt, buf, base + k * stride))
        return out

    def accessor(self, i: int, *, as_float: bool = False) -> list:
        """Accessor i -> list of tuples (normalized ints become 0..1 / -1..1 floats)."""
        key = (i, as_float)
        if key in self._acc_cache:
            return self._acc_cache[key]
        accs = self._list("accessors")
        if not isinstance(i, int) or not 0 <= i < len(accs):
            raise ReadError(f"The model points at accessor {i}, which doesn't exist.")
        acc = accs[i]
        ctype, atype, count = acc.get("componentType"), acc.get("type"), acc.get("count", 0)
        if ctype not in COMP or atype not in NCOMP:
            raise ReadError(f"Accessor {i} has an unknown type ({ctype}, {atype}).")
        n = NCOMP[atype]
        if not isinstance(count, int) or count < 0:
            raise ReadError(f"Accessor {i} has a broken count ({count!r}).")
        if "bufferView" in acc:
            flat = self._read_flat(acc["bufferView"], acc.get("byteOffset", 0), ctype, atype, count)
        else:
            if count * n > MAX_EMPTY_ACCESSOR:  # no data behind it, so the file size can't bound it
                raise ReadError(f"Accessor {i} claims {count} items with no data behind them; the file is broken.")
            flat = [0.0 if ctype == 5126 else 0] * (count * n)
        sp = acc.get("sparse")
        if sp:
            scount = sp.get("count", 0)
            si, sv = sp.get("indices", {}), sp.get("values", {})
            idx = self._read_flat(si["bufferView"], si.get("byteOffset", 0), si.get("componentType", 5125),
                                  "SCALAR", scount, tight=True)
            vals = self._read_flat(sv["bufferView"], sv.get("byteOffset", 0), ctype, atype, scount, tight=True)
            for k, at in enumerate(idx):
                if not 0 <= at < count:
                    raise ReadError(f"Accessor {i}'s sparse part points outside it.")
                flat[at * n:(at + 1) * n] = vals[k * n:(k + 1) * n]
        if ctype != 5126 and (acc.get("normalized") or as_float):
            div = NORM_DIV[ctype]
            flat = [max(c / div, -1.0) for c in flat]
        elif as_float:
            flat = [float(c) for c in flat]
        out = list(zip(*[iter(flat)] * n)) if n > 1 else [(c,) for c in flat]
        self._acc_cache[key] = out
        return out

    def scalars(self, i: int) -> list:
        return [t[0] for t in self.accessor(i)]

    # ---- nodes

    def _nodes(self):
        nodes = self._list("nodes")
        parent = [-1] * len(nodes)
        for i, nd in enumerate(nodes):
            for c in nd.get("children") or []:
                if 0 <= c < len(nodes):
                    if parent[c] != -1:
                        raise ReadError(f"Node {c} has two parents; the model's node tree is broken.")
                    parent[c] = i
        local = []
        for nd in nodes:
            if "matrix" in nd and len(nd["matrix"]) == 16:
                local.append(tuple(float(x) for x in nd["matrix"]))
            else:
                local.append(mat_from_trs(nd.get("translation", (0, 0, 0)),
                                          normalize(nd.get("rotation", (0, 0, 0, 1)), (0, 0, 0, 1)),
                                          nd.get("scale", (1, 1, 1))))
        world = [None] * len(nodes)
        for i in range(len(nodes)):  # no recursion: hair chains can be deep
            chain, p = [], i
            while p >= 0 and world[p] is None:
                chain.append(p)
                if len(chain) > len(nodes):
                    raise ReadError("The model's node tree loops back on itself.")
                p = parent[p]
            for c in reversed(chain):
                world[c] = local[c] if parent[c] < 0 else mat_mul(world[parent[c]], local[c])
        self.nodes, self.parent, self.local, self.world = nodes, parent, local, world

    def _roots(self):
        scenes = self._list("scenes")
        if scenes:
            s = self.scene_index if self.scene_index is not None else self.doc.get("scene", 0)
            if not 0 <= s < len(scenes):
                raise ReadError(f"The model has no scene {s}.")
            if len(scenes) > 1:
                self.out.note(f"The model has {len(scenes)} scenes; only scene {s} was read.")
            return [r for r in scenes[s].get("nodes") or [] if 0 <= r < len(self.nodes)]
        return [i for i in range(len(self.nodes)) if self.parent[i] < 0]

    def _walk(self, roots):
        order, stack = [], list(reversed(roots))
        while stack:
            i = stack.pop()
            order.append(i)
            stack.extend(reversed([c for c in self.nodes[i].get("children") or [] if 0 <= c < len(self.nodes)]))
        return order

    # ---- main

    def run(self) -> Scene:
        self._nodes()
        order = self._walk(self._roots())
        self._textures()
        self._materials()
        self._skeleton(order)
        self._meshes(order)
        self._vrm()
        if self.out.meta.get("licence") is None:
            copyright_ = (self.doc.get("asset") or {}).get("copyright")
            if copyright_:
                self.out.meta["licence"] = {"source": "gltf", "copyright": str(copyright_), "known": False,
                                            "may_share": False}
        self.out.meta["generator"] = str((self.doc.get("asset") or {}).get("generator", ""))
        return self.out

    # ---- textures and materials

    def _textures(self):
        images = self._list("images")
        samplers = self._list("samplers")
        self.image_tex = {}
        for ti, tex in enumerate(self._list("textures")):
            src = tex.get("source")
            ext = tex.get("extensions") or {}
            for e in IMAGE_EXTS:
                if src is None and isinstance(ext.get(e), dict):
                    src = ext[e].get("source")
            t = Texture(name=tex.get("name") or f"texture_{ti}")
            if isinstance(src, int) and 0 <= src < len(images):
                self._image_into(t, images[src], src)
                self.image_tex.setdefault(src, ti)
            else:
                t.data = b""
                self.out.note(f"Texture {ti} has no picture.")
            s = tex.get("sampler")
            if isinstance(s, int) and 0 <= s < len(samplers):
                sm = samplers[s]
                t.extras["sampler"] = {"wrap_s": sm.get("wrapS", 10497), "wrap_t": sm.get("wrapT", 10497),
                                       "mag": sm.get("magFilter"), "min": sm.get("minFilter")}
            self.out.textures.append(t)

    def _image_into(self, t: Texture, img: dict, index: int):
        t.extras["image"] = index
        if img.get("name") and t.name.startswith("texture_"):
            t.name = img["name"]
        mime = img.get("mimeType", "")
        if "bufferView" in img:
            t.data = self.view_bytes(img["bufferView"])
        elif str(img.get("uri", "")).startswith("data:"):
            uri = img["uri"]
            mime = mime or uri[5:].split(";", 1)[0].split(",", 1)[0]
            t.data = self._uri_bytes(uri, f"image {index}")
        elif img.get("uri"):
            p = self._uri_path(img["uri"], f"image {index}")
            t.path = str(p)
            mime = mime or MIME_BY_EXT.get(p.suffix.lower(), "")
        else:
            t.data = b""
        if not mime:
            head = t.data[:12] if t.data else Path(t.path).read_bytes()[:12] if t.path else b""
            mime = sniff_mime(head)
        t.mime = mime

    def _texref(self, mat: Material, slot: str, ref) -> None:
        if not isinstance(ref, dict) or not isinstance(ref.get("index"), int):
            return
        if not 0 <= ref["index"] < len(self.out.textures):
            self.out.note(f"Material {mat.name!r} points at a missing texture; dropped.")
            return
        mat.textures[slot] = ref["index"]
        tc = ref.get("texCoord", 0)
        tt = (ref.get("extensions") or {}).get("KHR_texture_transform")
        if isinstance(tt, dict):
            tc = tt.get("texCoord", tc)
            mat.extras.setdefault("texture_transform", {})[slot] = {
                "offset": tuple(tt.get("offset", (0.0, 0.0))), "rotation": float(tt.get("rotation", 0.0)),
                "scale": tuple(tt.get("scale", (1.0, 1.0)))}
            self.out.note("Some textures are moved/scaled (KHR_texture_transform); kept in the material, "
                          "not applied.")
        mat.texcoords[slot] = tc
        if slot == "normal" and "scale" in ref:
            mat.extras["normal_scale"] = float(ref["scale"])
        if slot == "occlusion" and "strength" in ref:
            mat.extras["occlusion_strength"] = float(ref["strength"])

    def _materials(self):
        for mi, m in enumerate(self._list("materials")):
            mat = Material(name=m.get("name") or f"material_{mi}")
            pbr = m.get("pbrMetallicRoughness") or {}
            mat.base_color = _floats(pbr.get("baseColorFactor"), (1.0, 1.0, 1.0, 1.0))
            mat.metallic = float(pbr.get("metallicFactor", 1.0))
            mat.roughness = float(pbr.get("roughnessFactor", 1.0))
            self._texref(mat, "base", pbr.get("baseColorTexture"))
            self._texref(mat, "metallic_roughness", pbr.get("metallicRoughnessTexture"))
            self._texref(mat, "normal", m.get("normalTexture"))
            self._texref(mat, "occlusion", m.get("occlusionTexture"))
            self._texref(mat, "emissive", m.get("emissiveTexture"))
            mat.emissive = _floats(m.get("emissiveFactor"), (0.0, 0.0, 0.0))
            mode = str(m.get("alphaMode", "OPAQUE")).upper()
            mat.alpha_mode = mode if mode in ("OPAQUE", "MASK", "BLEND") else "OPAQUE"
            mat.alpha_cutoff = float(m.get("alphaCutoff", 0.5))
            mat.double_sided = bool(m.get("doubleSided", False))
            ext = m.get("extensions") or {}
            if "KHR_materials_unlit" in ext:
                mat.shader = "unlit"
            es = ext.get("KHR_materials_emissive_strength")
            if isinstance(es, dict) and "emissiveStrength" in es:
                k = float(es["emissiveStrength"])
                mat.emissive = tuple(c * k for c in mat.emissive)
            mt = ext.get("VRMC_materials_mtoon")
            if isinstance(mt, dict):
                mat.shader = "mtoon"
                mat.shade_color = _floats(mt.get("shadeColorFactor"), (1.0, 1.0, 1.0))[:3]
                self._texref(mat, "shade", mt.get("shadeMultiplyTexture"))
                mat.extras["mtoon"] = {k: v for k, v in mt.items() if not k.endswith("Texture")}
            other = sorted(k for k in ext if k not in KNOWN_EXTS)
            if other:
                mat.extras["unread_extensions"] = other
            self.out.materials.append(mat)

    # ---- skeleton

    def _skeleton(self, order):
        skins = self._list("skins")
        wanted = set()
        for sk in skins:
            wanted.update(j for j in sk.get("joints") or [] if 0 <= j < len(self.nodes))
        human_nodes = list(_vrm_human_nodes(self.vrm0, self.vrm1).values())
        wanted.update(n for n in human_nodes if isinstance(n, int) and 0 <= n < len(self.nodes))
        for j in list(wanted):  # ancestors too, so the bone tree is whole
            p = self.parent[j]
            while p >= 0 and p not in wanted:
                wanted.add(p)
                p = self.parent[p]
        reach = set(order)
        missing = wanted - reach
        # joints outside the scene's node tree: walk their own trees so parents still come first
        extra = [i for i in self._walk(sorted(i for i in missing if self.parent[i] < 0)) if i in missing]
        bone_order = [i for i in order if i in wanted] + extra
        self.node_bone = {n: b for b, n in enumerate(bone_order)}
        bones = self.out.skeleton.bones
        for n in bone_order:
            nd = self.nodes[n]
            if "matrix" in nd:
                t, r, s = trs_from_mat(self.local[n])
            else:
                t = _floats(nd.get("translation"), (0.0, 0.0, 0.0))
                r = normalize(_floats(nd.get("rotation"), (0.0, 0.0, 0.0, 1.0)), (0.0, 0.0, 0.0, 1.0))
                s = _floats(nd.get("scale"), (1.0, 1.0, 1.0))
            p = self.parent[n]
            bones.append(Bone(name=nd.get("name") or f"node_{n}", parent=self.node_bone.get(p, -1),
                              translation=t, rotation=r, scale=s, extras={"node": n}))
        # per skin: inverse binds and the rest-pose fix
        for b, n in enumerate(bone_order):
            try:
                bones[b].inverse_bind = mat_inverse(self.world[n])
            except ReadError:
                self.out.note(f"Bone {bones[b].name!r} is scaled to nothing; its inverse bind is left plain.")
        self.skin_ibm = []
        moved = False
        for si, sk in enumerate(skins):
            joints = [j for j in sk.get("joints") or []]
            if "inverseBindMatrices" in sk:
                ibms = [tuple(float(x) for x in m) for m in self.accessor(sk["inverseBindMatrices"])]
                if len(ibms) < len(joints):
                    raise ReadError(f"Skin {si} has fewer inverse bind matrices than joints.")
            else:
                ibms = [IDENTITY] * len(joints)
            self.skin_ibm.append(ibms)
            for j, ibm in zip(joints, ibms):
                b = self.node_bone.get(j)
                if b is not None and "skin" not in bones[b].extras:
                    bones[b].extras["skin"] = si
                    if not self.fix_bind:
                        bones[b].inverse_bind = ibm
                moved |= not mat_close(mat_mul(self.world[j], ibm), IDENTITY, 1e-4)
        if self.fix_bind:
            if moved:
                self.out.note("The skin's bind pose wasn't its rest pose; vertices were moved to the rest pose.")
        for b in bones:
            b.extras.pop("skin", None)

    # ---- meshes

    def _meshes(self, order):
        meshes = self._list("meshes")
        skins = self._list("skins")
        dropped_modes, extra_influence, unweighted, rigid = {}, 0, 0, 0
        morphs = []
        for n in order:
            nd = self.nodes[n]
            mi = nd.get("mesh")
            if not isinstance(mi, int) or not 0 <= mi < len(meshes):
                continue
            gm = meshes[mi]
            target_names = (gm.get("extras") or {}).get("targetNames") or []
            si = nd.get("skin")
            skin = skins[si] if isinstance(si, int) and 0 <= si < len(skins) else None
            rigid_bone = self._bone_above(n) if skin is None and self.out.skeleton.bones else None
            for pi, prim in enumerate(gm.get("primitives") or []):
                mode = prim.get("mode", 4)
                if mode not in (4, 5, 6):
                    dropped_modes[mode] = dropped_modes.get(mode, 0) + 1
                    continue
                attrs = prim.get("attributes") or {}
                if "POSITION" not in attrs:
                    continue
                name = gm.get("name") or nd.get("name") or f"mesh_{mi}"
                mesh = Mesh(name=name if len(gm.get("primitives")) == 1 else f"{name}_{pi}")
                mesh.extras.update({"node": n, "gltf_mesh": mi, "primitive": pi})
                raw_pos = self.accessor(attrs["POSITION"], as_float=True)
                nsrc = len(raw_pos)
                idx = self.scalars(prim["indices"]) if "indices" in prim else list(range(nsrc))
                tris = _triangles(idx, mode, nsrc)
                # primitives often share one vertex pool (VRoid does): keep only this one's vertices
                used = sorted({i for t in tris for i in t})
                if len(used) < nsrc:
                    remap = {v: k for k, v in enumerate(used)}
                    tris = [(remap[a], remap[b], remap[c]) for a, b, c in tris]
                    src = used
                else:
                    src = range(nsrc)

                def pick(lst, cast=None):
                    if cast:
                        return [cast(lst[v]) for v in src]
                    return [lst[v] for v in src]

                mesh.triangles = tris
                pos = pick(raw_pos, lambda p: tuple(map(float, p)))
                nv = len(pos)
                nrm = pick(self.accessor(attrs["NORMAL"], as_float=True), lambda p: tuple(map(float, p))) \
                    if "NORMAL" in attrs else []
                k = 0
                while f"TEXCOORD_{k}" in attrs:
                    mesh.uvs.append(pick(self.accessor(attrs[f"TEXCOORD_{k}"], as_float=True)))
                    k += 1
                if "COLOR_0" in attrs:
                    mesh.colors = pick(self.accessor(attrs["COLOR_0"], as_float=True),
                                       lambda c: c if len(c) == 4 else (c[0], c[1], c[2], 1.0))
                mesh.material = prim.get("material") if isinstance(prim.get("material"), int) and \
                    0 <= prim["material"] < len(self.out.materials) else None
                # morph targets (kept, not applied)
                targets = prim.get("targets") or []
                tdeltas = []
                for ti, tg in enumerate(targets):
                    d = {}
                    for key in ("POSITION", "NORMAL"):
                        if key in tg:
                            d[key] = pick(self.accessor(tg[key], as_float=True), lambda p: tuple(map(float, p)))
                    tdeltas.append(d)
                # skin
                if skin is not None:
                    jmap = [self.node_bone.get(j, 0) for j in skin.get("joints") or []]
                    sets = []
                    k = 0
                    while f"JOINTS_{k}" in attrs and f"WEIGHTS_{k}" in attrs:
                        sets.append((self.accessor(attrs[f"JOINTS_{k}"]),
                                     self.accessor(attrs[f"WEIGHTS_{k}"], as_float=True)))
                        k += 1
                    fallback = jmap[0] if jmap else 0
                    for v in src:
                        pairs = []
                        for js, ws in sets:
                            for j, w in zip(js[v], ws[v]):
                                if w > 0 and 0 <= int(j) < len(jmap):
                                    pairs.append((jmap[int(j)], float(w)))
                        if len(pairs) > 4:
                            extra_influence += 1
                        r = top4(_merge_pairs(pairs))
                        if r is None:
                            unweighted += 1
                            r = ((fallback, 0, 0, 0), (1.0, 0.0, 0.0, 0.0))
                        mesh.joints.append(r[0])
                        mesh.weights.append(r[1])
                    if self.fix_bind:
                        ibms = self.skin_ibm[si]
                        bone_mats = {}
                        for jn, ibm in zip(skin.get("joints") or [], ibms):
                            b = self.node_bone.get(jn)
                            if b is not None and b not in bone_mats:
                                bone_mats[b] = mat_mul(self.world[jn], ibm)
                        if not all(mat_close(m, IDENTITY, 1e-6) for m in bone_mats.values()):
                            full = [bone_mats.get(b, IDENTITY) for b in range(len(self.out.skeleton.bones))]
                            dsrc = [[d.get("POSITION") or [None] * nv for d in tdeltas],
                                    [d.get("NORMAL") or [None] * nv for d in tdeltas]]
                            pos, nrm, outd = skin_to_rest(pos, nrm, mesh.joints, mesh.weights, full,
                                                          dsrc[0] + dsrc[1])
                            nt = len(tdeltas)
                            for t, d in enumerate(tdeltas):
                                if "POSITION" in d:
                                    d["POSITION"] = outd[t]
                                if "NORMAL" in d:
                                    d["NORMAL"] = outd[nt + t]
                else:
                    wm = self.world[n]
                    if not mat_close(wm, IDENTITY, 1e-9):
                        nm = normal_matrix(wm)
                        pos = [mat_point(wm, p) for p in pos]
                        nrm = [normalize(mat_dir(nm, v)) for v in nrm]
                        for d in tdeltas:
                            if "POSITION" in d:
                                d["POSITION"] = [mat_dir(wm, v) for v in d["POSITION"]]
                            if "NORMAL" in d:
                                d["NORMAL"] = [mat_dir(nm, v) for v in d["NORMAL"]]
                        if mat_det3(wm) < 0:
                            mesh.triangles = [(a, c, b) for a, b, c in mesh.triangles]
                    if rigid_bone is not None:
                        mesh.joints = [(rigid_bone, 0, 0, 0)] * nv
                        mesh.weights = [(1.0, 0.0, 0.0, 0.0)] * nv
                        mesh.extras["rigid_bone"] = rigid_bone
                        rigid += 1
                mesh.positions = pos
                if not nrm:
                    nrm = smooth_normals(pos, mesh.triangles)
                    self.out.note("Some meshes had no normals; smooth ones were made.")
                mesh.normals = [normalize(v, (0.0, 1.0, 0.0)) for v in nrm]
                if tdeltas:
                    mesh_index = len(self.out.meshes)
                    for t, d in enumerate(tdeltas):
                        morphs.append({"mesh": mesh_index,
                                       "name": str(target_names[t]) if t < len(target_names) else f"morph_{t}",
                                       "positions": d.get("POSITION"), "normals": d.get("NORMAL")})
                self.out.meshes.append(mesh)
        if morphs:
            self.out.meta["morphs"] = morphs
            self.out.note(f"{len(morphs)} morphs (blend shapes) kept, not applied.")
        for mode, cnt in sorted(dropped_modes.items()):
            kind = {0: "point", 1: "line", 2: "line loop", 3: "line strip"}.get(mode, f"mode {mode}")
            self.out.note(f"{cnt} {kind} parts dropped (only triangles are used).")
        if extra_influence:
            self.out.note(f"{extra_influence} vertices had more than 4 bone weights; the top 4 were kept.")
        if unweighted:
            self.out.note(f"{unweighted} skinned vertices had no weights; they follow the skin's first bone.")
        if rigid:
            self.out.note(f"{rigid} unskinned mesh parts were stuck to the bone they hang from.")

    def _bone_above(self, n):
        p = n
        while p >= 0:
            if p in self.node_bone:
                return self.node_bone[p]
            p = self.parent[p]
        return None

    # ---- VRM

    def _vrm(self):
        if not (self.vrm0 or self.vrm1):
            return
        meta = self.out.meta
        human = {}
        for name, node in _vrm_human_nodes(self.vrm0, self.vrm1).items():
            b = self.node_bone.get(node)
            if b is not None:
                human[name] = b
        meta["humanoid"] = human
        if self.vrm1:
            meta["vrm"] = self._vrm1_info()
            meta["licence"] = vrm1_licence(self.vrm1.get("meta") or {})
            thumb = (self.vrm1.get("meta") or {}).get("thumbnailImage")
            if isinstance(thumb, int):
                meta["thumbnail_texture"] = self._texture_for_image(thumb)
        else:
            meta["vrm"] = self._vrm0_info()
            vm = self.vrm0.get("meta") or {}
            meta["licence"] = vrm0_licence(vm)
            if isinstance(vm.get("texture"), int) and 0 <= vm["texture"] < len(self.out.textures):
                meta["thumbnail_texture"] = vm["texture"]
            self._vrm0_mtoon()

    def _texture_for_image(self, image: int):
        if image in self.image_tex:
            return self.image_tex[image]
        images = self._list("images")
        if not 0 <= image < len(images):
            return None
        t = Texture(name=images[image].get("name") or f"image_{image}")
        self._image_into(t, images[image], image)
        self.out.textures.append(t)
        self.image_tex[image] = len(self.out.textures) - 1
        return self.image_tex[image]

    def _vrm1_info(self):
        v = self.vrm1
        ex = v.get("expressions") or {}
        names = list((ex.get("preset") or {}).keys()) + list((ex.get("custom") or {}).keys())
        info = {"version": "1.0", "spec_version": str(v.get("specVersion", "")), "expressions": names}
        sb = (self.doc.get("extensions") or {}).get("VRMC_springBone") or {}
        roots = []
        for spring in sb.get("springs") or []:
            js = spring.get("joints") or []
            if js and isinstance(js[0].get("node"), int) and js[0]["node"] in self.node_bone:
                roots.append(self.node_bone[js[0]["node"]])
        info["spring_bone_roots"] = roots
        return info

    def _vrm0_info(self):
        v = self.vrm0
        groups = (v.get("blendShapeMaster") or {}).get("blendShapeGroups") or []
        info = {"version": "0.x", "exporter": str(v.get("exporterVersion", "")),
                "expressions": [g.get("presetName") if g.get("presetName") not in (None, "", "unknown")
                                else g.get("name", "") for g in groups]}
        roots = []
        for g in (v.get("secondaryAnimation") or {}).get("boneGroups") or []:
            roots += [self.node_bone[b] for b in g.get("bones") or [] if b in self.node_bone]
        info["spring_bone_roots"] = roots
        return info

    def _vrm0_mtoon(self):
        props = self.vrm0.get("materialProperties") or []
        by_name = {p.get("name"): p for p in props if isinstance(p, dict)}
        for mi, mat in enumerate(self.out.materials):
            p = by_name.get(mat.name) or (props[mi] if mi < len(props) and isinstance(props[mi], dict) else None)
            if not p:
                continue
            shader = str(p.get("shader", ""))
            mat.extras["vrm0_shader"] = shader
            if shader == "VRM_USE_GLTFSHADER":
                continue
            fl = p.get("floatProperties") or {}
            vec = p.get("vectorProperties") or {}
            tex = p.get("textureProperties") or {}
            if "MToon" in shader:
                mat.shader = "mtoon"
            elif "Unlit" in shader:
                mat.shader = "unlit"
            if "_ShadeColor" in vec:
                mat.shade_color = tuple(srgb_to_linear(c) for c in _floats(vec["_ShadeColor"], (1, 1, 1, 1))[:3])
            if "base" not in mat.textures and isinstance(tex.get("_MainTex"), int):
                self._texref(mat, "base", {"index": tex["_MainTex"]})
                if "_Color" in vec:
                    c = _floats(vec["_Color"], (1, 1, 1, 1))
                    mat.base_color = tuple(srgb_to_linear(x) for x in c[:3]) + (c[3],)
            for slot, key in (("shade", "_ShadeTexture"), ("normal", "_BumpMap"), ("emissive", "_EmissionMap")):
                if slot not in mat.textures and isinstance(tex.get(key), int):
                    self._texref(mat, slot, {"index": tex[key]})
            blend = fl.get("_BlendMode")
            if blend is not None:
                mat.alpha_mode = {0: "OPAQUE", 1: "MASK"}.get(int(blend), "BLEND")
                if "_Cutoff" in fl:
                    mat.alpha_cutoff = float(fl["_Cutoff"])
            if "_CullMode" in fl:
                mat.double_sided = int(fl["_CullMode"]) == 0
            mat.extras["mtoon"] = {"floats": dict(fl), "vectors": {k: list(v) for k, v in vec.items()}}


KNOWN_EXTS = {"KHR_materials_unlit", "KHR_texture_transform", "KHR_materials_emissive_strength",
              "KHR_mesh_quantization", "VRM", "VRMC_vrm", "VRMC_materials_mtoon", "VRMC_springBone",
              "VRMC_node_constraint", "VRMC_springBone_extended_collider", *IMAGE_EXTS}


# ----------------------------------------------------------------------------------------------- helpers

def _floats(v, default) -> tuple:
    try:
        out = tuple(float(x) for x in v)
    except (TypeError, ValueError):
        return tuple(default)
    return out if len(out) >= len(default) and all(math.isfinite(x) for x in out) else tuple(default)


def _merge_pairs(pairs):
    """The same bone twice (JOINTS_0 and JOINTS_1) counts once, weights added."""
    acc = {}
    for j, w in pairs:
        acc[j] = acc.get(j, 0.0) + w
    return list(acc.items())


def _triangles(idx, mode, nv) -> list:
    if any(not 0 <= i < nv for i in idx):
        raise ReadError("A mesh's indices point past its vertices.")
    if mode == 4:
        it = iter(idx)
        return list(zip(it, it, it))
    tris = []
    if mode == 5:
        for i in range(len(idx) - 2):
            t = (idx[i], idx[i + 1], idx[i + 2]) if i % 2 == 0 else (idx[i], idx[i + 2], idx[i + 1])
            if len(set(t)) == 3:
                tris.append(t)
    else:  # fan
        for i in range(1, len(idx) - 1):
            t = (idx[i], idx[i + 1], idx[0])
            if len(set(t)) == 3:
                tris.append(t)
    return tris


def sniff_mime(head: bytes) -> str:
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"\xabKTX 20"):
        return "image/ktx2"
    if head.startswith(b"DDS "):
        return "image/vnd-ms.dds"
    return "application/octet-stream"


def srgb_to_linear(c: float) -> float:
    c = float(c)
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _vrm_human_nodes(vrm0, vrm1) -> dict:
    out = {}
    if vrm1:
        for name, b in ((vrm1.get("humanoid") or {}).get("humanBones") or {}).items():
            if isinstance(b, dict) and isinstance(b.get("node"), int):
                out[name] = b["node"]
    elif vrm0:
        for b in (vrm0.get("humanoid") or {}).get("humanBones") or []:
            if isinstance(b, dict) and isinstance(b.get("node"), int) and b.get("bone"):
                out[b["bone"]] = b["node"]
    return out


def vrm0_licence(m: dict) -> dict:
    """VRM 0.x meta -> our licence dict. Unknown or 'Other' licences are treated as not shareable."""
    name = str(m.get("licenseName", "") or "")
    cc = name.startswith("CC")
    redistribution = False if name == "Redistribution_Prohibited" else (True if cc else None)
    modification = ("prohibited" if "ND" in name.split("_") else "allowed_and_share") if cc else None
    credit = "unnecessary" if name == "CC0" else ("required" if name.startswith("CC_BY") else None)
    commercial = {"Allow": "allowed", "Disallow": "not_allowed"}.get(m.get("commercialUssageName"))
    if cc and "NC" in name.split("_"):
        commercial = "not_allowed"
    return {
        "source": "vrm0", "known": bool(name) and name != "Other",
        "title": str(m.get("title", "") or ""), "authors": [str(m["author"])] if m.get("author") else [],
        "version": str(m.get("version", "") or ""), "contact": str(m.get("contactInformation", "") or ""),
        "reference": str(m.get("reference", "") or ""),
        "allowed_users": VRM0_USERS.get(m.get("allowedUserName")),
        "violent_ok": m.get("violentUssageName") == "Allow", "sexual_ok": m.get("sexualUssageName") == "Allow",
        "commercial": commercial, "licence_name": name, "licence_url": str(m.get("otherLicenseUrl", "") or ""),
        "other_permission_url": str(m.get("otherPermissionUrl", "") or ""),
        "redistribution": redistribution, "modification": modification, "credit": credit,
        "may_share": bool(redistribution) and modification == "allowed_and_share",
        "raw": {k: v for k, v in m.items() if k != "texture"},
    }


def vrm1_licence(m: dict) -> dict:
    """VRMC_vrm 1.0 meta -> our licence dict (spec defaults: no redistribution, no modification)."""
    redistribution = bool(m.get("allowRedistribution", False))
    modification = VRM1_MODIFICATION.get(m.get("modification", "prohibited"), "prohibited")
    return {
        "source": "vrm1", "known": True,
        "title": str(m.get("name", "") or ""), "authors": [str(a) for a in m.get("authors") or []],
        "version": str(m.get("version", "") or ""), "contact": str(m.get("contactInformation", "") or ""),
        "reference": ", ".join(str(r) for r in m.get("references") or []),
        "copyright": str(m.get("copyrightInformation", "") or ""),
        "third_party": str(m.get("thirdPartyLicenses", "") or ""),
        "allowed_users": VRM1_USERS.get(m.get("avatarPermission", "onlyAuthor"), "author_only"),
        "violent_ok": bool(m.get("allowExcessivelyViolentUsage", False)),
        "sexual_ok": bool(m.get("allowExcessivelySexualUsage", False)),
        "political_ok": bool(m.get("allowPoliticalOrReligiousUsage", False)),
        "antisocial_ok": bool(m.get("allowAntisocialOrHateUsage", False)),
        "commercial": VRM1_COMMERCIAL.get(m.get("commercialUsage", "personalNonProfit"), "not_allowed"),
        "licence_name": "VRM Public License 1.0" if "vrm.dev/licenses/1.0" in str(m.get("licenseUrl", "")) else "",
        "licence_url": str(m.get("licenseUrl", "") or ""), "other_url": str(m.get("otherLicenseUrl", "") or ""),
        "redistribution": redistribution, "modification": modification,
        "credit": m.get("creditNotation", "required"),
        "may_share": redistribution and modification == "allowed_and_share",
        "raw": {k: v for k, v in m.items() if k != "thumbnailImage"},
    }
