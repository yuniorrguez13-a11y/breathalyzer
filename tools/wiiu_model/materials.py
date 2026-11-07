"""Material translation between Wii U and Switch (milestone M2).

- Samplers: the three GX2 sampler words <-> nn::gfx sampler values (SamplerState). Bit positions of the GX2 words as
  in BfresLibrary's TexSampler (MIT licence) and Mesa's r600d.h (MIT): word 0 clamp X/Y/Z bits 0-2/3-5/6-8, mag filter
  9-10, min filter 12-13, z filter 15-16, mip filter 17-18, max anisotropy 19-21, border type 22-23, depth compare
  function 26-28; word 1 min LOD 0-9 and max LOD 10-19 (unsigned 4.6 fixed point), LOD bias 20-31 (signed, 6 fraction
  bits); word 2 depth compare enable bit 30 (<-> the nn::gfx comparison filter bit 0x80). Anisotropy above 1:1 is kept
  as max anisotropy. to_switch warns about both (no Switch file has them). Bits we do not translate stay in
  `extra['wiiu.sampler_words']` for a Wii U round trip.
- RenderState: a Wii U material keeps blending, culling, depth and alpha test in a RenderState (flags + GX2 registers);
  a Switch BotW material has the same as 16 `gsys_*` render infos instead. The value strings are those of BfresLibrary's
  BotW preset (MIT licence) and the user's Switch files; register bits per Mesa r600d.h.
- `to_switch`: adds the missing render infos (and sets `gsys_alpha_test_ref_value`) for materials read from Wii U files,
  turns wide-string user data into strings and drops the Wii U raw param size (all warned).
- `donor_overlay` (with the game files): a material the user's vanilla Switch model also has is built on that vanilla
  material, with the mod's values laid on top (design section 5.1); the others stay converted field by field. The
  samplers are the mod's (a sampler the mod removed stays removed, with a warning; changed wrap or filter values are
  kept).
"""
from __future__ import annotations

import copy
import struct

from .errors import Unsupported
from .neutral import Material, Model, Param, RenderInfo, RenderState, SamplerState, diff

# ------------------------------------------------------------------------------------------------------- samplers

# GX2TexClamp -> nn::gfx TextureAddressMode (0 repeat, 1 mirror, 2 clamp to edge, 3 clamp to border, 4 mirror clamp to
# edge). GX2 4 (clamp half border), 5 (mirror once half border) and 7 (mirror once border) have no exact twin.
_CLAMP_TO_NX = {0: 0, 1: 1, 2: 2, 3: 4, 6: 3}
_CLAMP_NEAR = {4: 3, 5: 4, 7: 4}
_CLAMP_TO_GX2 = {v: k for k, v in _CLAMP_TO_NX.items()}
# GX2TexBorderType (0 transparent black, 1 opaque black, 2 white, 3 register) <-> nn::gfx (0 white, 1 transparent
# black, 2 opaque black)
_BORDER_TO_NX = {0: 1, 1: 2, 2: 0}
_BORDER_TO_GX2 = {v: k for k, v in _BORDER_TO_NX.items()}
_ANISO = {0: 1, 1: 2, 2: 4, 3: 8, 4: 16}
_ANISO_GX2 = {v: k for k, v in _ANISO.items()}
# bits of word 0 / word 2 that the translation sets; the others are kept as they were (or 0 / the default)
_W0_MAPPED = 0x1FF | (3 << 9) | (3 << 12) | (3 << 17) | (7 << 19) | (3 << 22) | (7 << 26)
_W2_DEFAULT = 0x80000000           # BfresLibrary's default word 2 (bit 31 set)
_W2_COMPARE = 1 << 30
# nn::gfx filter bits above min/mag/mip: anisotropic 0x40, comparison 0x80. Nearly every sampler in the user's Switch
# files is filter 0x29 (linear min/mag, point mip), anisotropy 1, compare 0; Terrain (Pack/TitleBG.pack) also has
# 0x15, 0x19, and 0x6a (the anisotropic bit) with anisotropy 2.
FILTER_ANISO, FILTER_COMPARE = 0x40, 0x80


def _fixed(value: float, bits: int, signed: bool, what: str, warnings: list | None) -> int:
    q = round(value * 64)
    lo, hi = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
    if q / 64 != value or not lo <= q <= hi:
        q = min(hi, max(lo, q))
        if warnings is not None:
            warnings.append(f"{what}: {value} is not a multiple of 1/64 in range; written as {q / 64}")
    return q & ((1 << bits) - 1)


def sampler_from_gx2(words, warnings: list | None = None, what: str = "sampler") -> SamplerState:
    """nn::gfx sampler values from the three GX2 sampler words."""
    w0, w1, w2 = words
    wraps = []
    for shift in (0, 3, 6):
        c = (w0 >> shift) & 7
        if c in _CLAMP_TO_NX:
            wraps.append(_CLAMP_TO_NX[c])
        else:
            wraps.append(_CLAMP_NEAR[c])
            if warnings is not None:
                warnings.append(f"{what}: GX2 clamp mode {c} has no Switch twin; the nearest is used")
    mag, mn, mip = (w0 >> 9) & 3, (w0 >> 12) & 3, (w0 >> 17) & 3
    if mag > 1 or mn > 1 or mip > 2:
        raise Unsupported(f"{what}: GX2 filter values {mag}/{mn}/{mip}")
    # nn::gfx filter: mip bits 0-1, mag bits 2-3, min bits 4-5 (1 point, 2 linear). GX2 'no mip' becomes point.
    filt = (max(mip, 1)) | ((mag + 1) << 2) | ((mn + 1) << 4)
    aniso = (w0 >> 19) & 7
    if aniso not in _ANISO:
        raise Unsupported(f"{what}: GX2 anisotropy ratio {aniso}")
    border = (w0 >> 22) & 3
    if border not in _BORDER_TO_NX:
        if warnings is not None:
            warnings.append(f"{what}: GX2 border colour from a register; white is used")
        border_nx = 0
    else:
        border_nx = _BORDER_TO_NX[border]
    compare = (w0 >> 26) & 7
    if w2 & _W2_COMPARE:
        filt |= FILTER_COMPARE                           # warned by to_switch (sampler_notes)
    min_lod = (w1 & 0x3FF) / 64
    max_lod = ((w1 >> 10) & 0x3FF) / 64
    bias = (w1 >> 20) & 0xFFF
    bias = (bias - 0x1000 if bias & 0x800 else bias) / 64
    st = SamplerState(*wraps, compare, border_nx, _ANISO[aniso], filt, min_lod, max_lod, bias)
    st.extra["wiiu.sampler_words"] = (w0, w1, w2)
    return st


def sampler_to_gx2(st: SamplerState, warnings: list | None = None, what: str = "sampler") -> tuple[int, int, int]:
    """The three GX2 sampler words for nn::gfx sampler values. Kept words (`wiiu.sampler_words`) give the bits that
    are not translated."""
    keep = st.extra.get("wiiu.sampler_words")
    w0 = keep[0] & ~_W0_MAPPED if keep else 0
    w2 = keep[2] if keep else _W2_DEFAULT
    for shift, wrap in zip((0, 3, 6), (st.wrap_u, st.wrap_v, st.wrap_w)):
        if wrap not in _CLAMP_TO_GX2:
            raise Unsupported(f"{what}: wrap mode {wrap}")
        w0 |= _CLAMP_TO_GX2[wrap] << shift
    # the anisotropic bit: GX2 says that with its anisotropy ratio (your Terrain model's anisotropic samplers are
    # filter 0x6a with anisotropy 2)
    aniso_bit = FILTER_ANISO if st.max_aniso > 1 else 0
    if st.filter & ~(0x3F | FILTER_COMPARE | aniso_bit):
        raise Unsupported(f"{what}: nn::gfx filter {st.filter:#x} (anisotropic / minimum / maximum bits)")
    w2 = (w2 | _W2_COMPARE) if st.filter & FILTER_COMPARE else (w2 & ~_W2_COMPARE)
    mip, mag, mn = st.filter & 3, (st.filter >> 2) & 3, (st.filter >> 4) & 3
    if mag not in (1, 2) or mn not in (1, 2) or mip not in (1, 2):
        raise Unsupported(f"{what}: nn::gfx filter {st.filter:#x}")
    if keep and (keep[0] >> 17) & 3 == 0 and mip == 1:
        mip = 0                                          # the Wii U file said 'no mip'; keep it
    w0 |= ((mag - 1) << 9) | ((mn - 1) << 12) | (mip << 17)
    if st.max_aniso not in _ANISO_GX2:
        raise Unsupported(f"{what}: max anisotropy {st.max_aniso}")
    w0 |= _ANISO_GX2[st.max_aniso] << 19
    if st.border not in _BORDER_TO_GX2:
        raise Unsupported(f"{what}: border colour {st.border}")
    border = _BORDER_TO_GX2[st.border]
    if keep and (keep[0] >> 22) & 3 == 3 and st.border == 0:
        border = 3
    w0 |= border << 22
    if not 0 <= st.compare <= 7:
        raise Unsupported(f"{what}: compare function {st.compare}")
    w0 |= st.compare << 26
    w1 = (_fixed(st.min_lod, 10, False, f"{what} min LOD", warnings)
          | _fixed(st.max_lod, 10, False, f"{what} max LOD", warnings) << 10
          | _fixed(st.lod_bias, 12, True, f"{what} LOD bias", warnings) << 20)
    return w0 & 0xFFFFFFFF, w1 & 0xFFFFFFFF, w2 & 0xFFFFFFFF


# -------------------------------------------------------------------------------------------------- render state

RENDER_STATE_INFOS = (
    ("gsys_render_state_mode", "string"), ("gsys_render_state_display_face", "string"),
    ("gsys_render_state_blend_mode", "string"), ("gsys_depth_test_enable", "string"),
    ("gsys_depth_test_write", "string"), ("gsys_depth_test_func", "string"),
    ("gsys_color_blend_rgb_src_func", "string"), ("gsys_color_blend_rgb_dst_func", "string"),
    ("gsys_color_blend_rgb_op", "string"), ("gsys_color_blend_alpha_src_func", "string"),
    ("gsys_color_blend_alpha_dst_func", "string"), ("gsys_color_blend_alpha_op", "string"),
    ("gsys_alpha_test_enable", "string"), ("gsys_alpha_test_func", "string"),
    ("gsys_color_blend_const_color", "float"), ("gsys_alpha_test_value", "float"),
)
RENDER_STATE_NAMES = tuple(n for n, _ in RENDER_STATE_INFOS)
ALPHA_REF_PARAM = "gsys_alpha_test_ref_value"
PARAM_FLOAT = 12

MODES = {0: "custom", 1: "opaque", 2: "mask", 3: "translucent"}
BLEND_MODES = {0: "none", 1: "color", 2: "logic"}
# GX2CompareFunction (= r600 compare function) -> string
COMPARE = {0: "never", 1: "less", 2: "equal", 3: "lequal", 4: "greater", 5: "noequal", 6: "gequal", 7: "always"}
# GX2BlendFunction (= r600 BLEND_*) -> string. The first eight are BfresLibrary's BotW preset; the others follow its
# naming and are not confirmed by any file (a warning says so when one is used).
BLEND_FUNCS = {0: "zero", 1: "one", 2: "src_color", 4: "src_alpha", 5: "one_minus_src_alpha", 6: "dst_alpha",
               13: "const_color", 19: "const_alpha"}
BLEND_FUNCS_UNCONFIRMED = {3: "one_minus_src_color", 7: "one_minus_dst_alpha", 8: "dst_color",
                           9: "one_minus_dst_color", 10: "src_alpha_saturate", 14: "one_minus_const_color",
                           20: "one_minus_const_alpha"}
# GX2BlendCombine (= r600 COMB_*) -> string; 'add', 'min', 'max' are BfresLibrary's, the subtractions are not confirmed
BLEND_OPS = {0: "add", 2: "min", 3: "max"}
BLEND_OPS_UNCONFIRMED = {1: "sub", 4: "rev_sub"}
# (cull front, cull back) -> the faces that are drawn
FACES = {(False, False): "both", (False, True): "front", (True, False): "back", (True, True): "none"}
# The only mode, face, blend mode and compare strings the user's Switch files show (210 materials). Others follow the
# same naming but are not confirmed: a warning says so when one is written.
SEEN_ON_SWITCH = {"gsys_render_state_mode": {"opaque", "mask", "translucent"},
                  "gsys_render_state_display_face": {"front", "both"},
                  "gsys_render_state_blend_mode": {"none", "color"},
                  "gsys_depth_test_func": {"lequal", "gequal"}, "gsys_alpha_test_func": {"lequal", "gequal"}}


def _rev(*tables) -> dict:
    out = {}
    for t in tables:
        out.update({v: k for k, v in t.items()})
    return out


def _bool(b) -> str:
    return "true" if b else "false"


def infos_from_render_state(rs: RenderState, warnings: list | None = None, what: str = "material") -> list:
    """The 16 render infos for a Wii U RenderState, in the Switch order."""
    def blend(v: int) -> str:
        if v in BLEND_FUNCS:
            return BLEND_FUNCS[v]
        if v in BLEND_FUNCS_UNCONFIRMED:
            if warnings is not None:
                warnings.append(f"{what}: blend function {BLEND_FUNCS_UNCONFIRMED[v]!r} is not confirmed on Switch")
            return BLEND_FUNCS_UNCONFIRMED[v]
        raise Unsupported(f"{what}: GX2 blend function {v}")

    def op(v: int) -> str:
        if v in BLEND_OPS:
            return BLEND_OPS[v]
        if v in BLEND_OPS_UNCONFIRMED:
            if warnings is not None:
                warnings.append(f"{what}: blend operation {BLEND_OPS_UNCONFIRMED[v]!r} is not confirmed on Switch")
            return BLEND_OPS_UNCONFIRMED[v]
        raise Unsupported(f"{what}: GX2 blend combine {v}")

    bc, dc, ac, pc = rs.blend_control, rs.depth_control, rs.alpha_control, rs.polygon_control
    blend_mode = (rs.flags >> 4) & 3
    if blend_mode not in BLEND_MODES:
        raise Unsupported(f"{what}: RenderState blend mode {blend_mode}")
    vals = [
        MODES[rs.flags & 3], FACES[bool(pc & 1), bool(pc & 2)], BLEND_MODES[blend_mode],
        _bool(dc & 2), _bool(dc & 4), COMPARE[(dc >> 4) & 7],
        blend(bc & 0x1F), blend((bc >> 8) & 0x1F), op((bc >> 5) & 7),
        blend((bc >> 16) & 0x1F), blend((bc >> 24) & 0x1F), op((bc >> 21) & 7),
        _bool(ac & 8), COMPARE[ac & 7],
    ]
    if warnings is not None:
        for (n, _), v in zip(RENDER_STATE_INFOS, vals):
            if n in SEEN_ON_SWITCH and v not in SEEN_ON_SWITCH[n]:
                warnings.append(f"{what}: {n} {v!r} is in no Switch file we checked (name not confirmed)")
    out = [RenderInfo(n, "string", (v,)) for (n, _), v in zip(RENDER_STATE_INFOS, vals)]
    out.append(RenderInfo("gsys_color_blend_const_color", "float", tuple(rs.blend_color)))
    out.append(RenderInfo("gsys_alpha_test_value", "float", (rs.alpha_ref,)))
    return out


_MODE_CODE = _rev(MODES)
_BLEND_MODE_CODE = _rev(BLEND_MODES)
_COMPARE_CODE = _rev(COMPARE)
_BLEND_CODE = _rev(BLEND_FUNCS, BLEND_FUNCS_UNCONFIRMED)
_OP_CODE = _rev(BLEND_OPS, BLEND_OPS_UNCONFIRMED)
_FACE_CODE = _rev(FACES)
_BOOL = {"true": True, "false": False}
DEFAULT_INFOS = {"gsys_render_state_mode": "opaque", "gsys_render_state_display_face": "front",
                 "gsys_render_state_blend_mode": "none", "gsys_depth_test_enable": "true",
                 "gsys_depth_test_write": "true", "gsys_depth_test_func": "lequal",
                 "gsys_color_blend_rgb_src_func": "src_alpha", "gsys_color_blend_rgb_dst_func": "one_minus_src_alpha",
                 "gsys_color_blend_rgb_op": "add", "gsys_color_blend_alpha_src_func": "one",
                 "gsys_color_blend_alpha_dst_func": "zero", "gsys_color_blend_alpha_op": "add",
                 "gsys_alpha_test_enable": "false", "gsys_alpha_test_func": "gequal",
                 "gsys_color_blend_const_color": (0.0, 0.0, 0.0, 0.0), "gsys_alpha_test_value": 0.5}


def render_state_from_infos(infos: list, warnings: list | None = None, what: str = "material") -> RenderState:
    """A Wii U RenderState from the 16 render infos (a missing or empty one takes the BotW default, with a warning)."""
    by = {r.name: r.values for r in infos}

    def get(name: str):
        v = by.get(name)
        if not v:
            if warnings is not None:
                warnings.append(f"{what}: render info {name} missing or empty; default used for the RenderState")
            return DEFAULT_INFOS[name]
        return v[0] if name != "gsys_color_blend_const_color" else tuple(v)

    def code(table: dict, name: str):
        v = get(name)
        if v not in table:
            raise Unsupported(f"{what}: render info {name} = {v!r} has no GX2 value")
        return table[v]

    flags = code(_MODE_CODE, "gsys_render_state_mode") | (code(_BLEND_MODE_CODE, "gsys_render_state_blend_mode") << 4)
    cull_front, cull_back = code(_FACE_CODE, "gsys_render_state_display_face")
    polygon = int(cull_front) | (int(cull_back) << 1)
    depth = ((code(_BOOL, "gsys_depth_test_enable") << 1) | (code(_BOOL, "gsys_depth_test_write") << 2)
             | (code(_COMPARE_CODE, "gsys_depth_test_func") << 4))
    alpha = code(_COMPARE_CODE, "gsys_alpha_test_func") | (code(_BOOL, "gsys_alpha_test_enable") << 3)
    blend = (code(_BLEND_CODE, "gsys_color_blend_rgb_src_func") | (code(_OP_CODE, "gsys_color_blend_rgb_op") << 5)
             | (code(_BLEND_CODE, "gsys_color_blend_rgb_dst_func") << 8)
             | (code(_BLEND_CODE, "gsys_color_blend_alpha_src_func") << 16)
             | (code(_OP_CODE, "gsys_color_blend_alpha_op") << 21)
             | (code(_BLEND_CODE, "gsys_color_blend_alpha_dst_func") << 24) | (1 << 29))
    color = tuple(float(x) for x in get("gsys_color_blend_const_color"))
    if len(color) != 4:
        raise Unsupported(f"{what}: gsys_color_blend_const_color has {len(color)} values")
    ref = float(get("gsys_alpha_test_value"))
    # CB_COLOR_CONTROL: blend enable for target 0 (bit 8) when blending, ROP3 0xCC (copy)
    color_control = (0xCC << 16) | (0x100 if flags >> 4 == 1 else 0)
    return RenderState(flags, polygon, depth, alpha, ref, color_control, 0, blend, color)


def to_switch(model: Model, warnings: list | None = None) -> None:
    """Prepare a model read from a Wii U file for the Switch (call it before nx_model.write):
    - the render infos the Switch keeps instead of the RenderState are added when missing (at the end, in the Switch
      order), and `gsys_alpha_test_ref_value` takes the alpha reference. Materials that already have all 16 (a Switch
      file, or a Wii U file written from one) are not changed;
    - wide-string user data (model, bones, materials) becomes string user data, with a warning: the Switch files show
      no wide strings, and type 3 means something else in older notes;
    - a Wii U raw param size is not carried over (the Switch writer writes 0, as every Switch file has), noted once per
      model;
    - samplers read from GX2 words with a comparison filter or anisotropy above 1:1 are warned about (sampler_notes)."""
    _wide_strings(model, warnings)
    raw = sum(1 for mat in model.materials if mat.extra.get("wiiu.raw_param_size"))
    if warnings is not None:
        if raw:
            warnings.append(f"model {model.name!r}: {raw} material(s) have a Wii U raw param size; the Switch file "
                            f"gets 0, as every Switch file we checked has")
        for mat in model.materials:
            for s in mat.samplers:
                if "wiiu.sampler_words" in s.state.extra:
                    warnings += sampler_notes(s.state, f"material {mat.name!r} sampler {s.name!r}")
    for mat in model.materials:
        rs = mat.extra.get("wiiu.render_state")
        if rs is None:
            continue
        have = {r.name for r in mat.render_infos}
        missing = [n for n in RENDER_STATE_NAMES if n not in have]
        if not missing:
            continue
        made = {r.name: r for r in infos_from_render_state(rs, warnings, f"material {mat.name!r}")}
        mat.render_infos = list(mat.render_infos) + [made[n] for n in missing]
        if "gsys_alpha_test_value" in missing:
            set_alpha_ref(mat, rs.alpha_ref, warnings)


def sampler_notes(st: SamplerState, what: str = "sampler") -> list[str]:
    """Warnings for sampler values no Switch file we checked has: a comparison filter, anisotropy above 1:1 (kept as
    max anisotropy, without the nn::gfx anisotropic filter bit)."""
    out = []
    if st.filter & FILTER_COMPARE:
        out.append(f"{what}: depth compare sampler (function {st.compare}); the nn::gfx comparison filter bit is set, "
                   f"which no Switch file we checked has")
    if st.max_aniso > 1 and not st.filter & FILTER_ANISO:
        out.append(f"{what}: anisotropy {st.max_aniso}:1 is kept as max anisotropy without the nn::gfx anisotropic "
                   f"filter bit (the anisotropic samplers we checked, Terrain's, have it: filter 0x6a)")
    return out


def _wide_strings(model: Model, warnings: list | None) -> None:
    groups = [(f"model {model.name!r}", model.user_data)]
    if model.skeleton is not None:
        groups += [(f"bone {b.name!r}", b.user_data) for b in model.skeleton.bones]
    groups += [(f"material {m.name!r}", m.user_data) for m in model.materials]
    for where, uds in groups:
        for u in uds:
            if u.kind == "wstring":
                u.kind = "string"
                if warnings is not None:
                    warnings.append(f"{where}: wide-string user data {u.name!r} became a string (read as UTF-16 "
                                    f"big-endian, not confirmed on a real file)")


def set_alpha_ref(mat: Material, value: float, warnings: list | None = None) -> None:
    """Set shader param `gsys_alpha_test_ref_value` (float) to `value`; append it when the material lacks it."""
    for p in mat.params:
        if p.name == ALPHA_REF_PARAM:
            if p.type != PARAM_FLOAT or p.size != 4:
                if warnings is not None:
                    warnings.append(f"material {mat.name!r}: {ALPHA_REF_PARAM} has type {p.type}; not changed")
                return
            data = bytearray(mat.param_data)
            struct.pack_into("<f", data, p.offset, value)
            mat.param_data = bytes(data)
            return
    off = len(mat.param_data)
    idx = len(mat.params)
    mat.params = list(mat.params) + [Param(ALPHA_REF_PARAM, PARAM_FLOAT, off, 4, idx, idx)]
    mat.param_data = bytes(mat.param_data) + struct.pack("<f", value)
    if warnings is not None:
        warnings.append(f"material {mat.name!r}: shader param {ALPHA_REF_PARAM} added (the Wii U material lacks it)")


def alpha_ref(mat: Material):
    """Value of `gsys_alpha_test_ref_value` (or None)."""
    for p in mat.params:
        if p.name == ALPHA_REF_PARAM and p.size == 4 and p.offset + 4 <= len(mat.param_data):
            return struct.unpack_from("<f", mat.param_data, p.offset)[0]
    return None


# ------------------------------------------------------------------------------------------- vanilla donor (M3)

def _gx2_round_trip(st: SamplerState) -> SamplerState | None:
    """The sampler values a Wii U file can hold for `st` (through the three GX2 words and back); None when GX2 has no
    form for them."""
    try:
        plain = copy.deepcopy(st)
        plain.extra.pop("wiiu.sampler_words", None)
        return sampler_from_gx2(sampler_to_gx2(plain))
    except Unsupported:
        return None


def same_sampler_state(mod: SamplerState, vanilla: SamplerState) -> bool:
    """Whether the mod's sampler values (read from GX2 words) are the vanilla ones as a Wii U file holds them: wrap
    modes, filter, anisotropy, border, compare function and LOD (in 1/64 steps)."""
    a, b = _gx2_round_trip(vanilla), _gx2_round_trip(mod)
    if a is None or b is None:
        return diff(vanilla, mod, "cross", 1) == []
    return diff(a, b, "cross", 1) == []


def donor_material(mod: Material, donor: Material) -> tuple[Material, list[str], list[str]]:
    """A Switch material built on the user's vanilla material of the same name (design section 5.1), with the mod's
    values laid on top. Returns (material, what the mod changed, mod fields the vanilla material lacks: dropped).

    Samplers are the mod's, in the mod's order, each with the mod's texture: a sampler the vanilla material also has
    keeps the vanilla values when the mod's are the same as a Wii U file holds them (same_sampler_state), else takes
    the mod's (the mod changed wrap or filter). Vanilla samplers the mod lacks are left out ("sampler X removed" in
    the changes). Sampler assigns: the vanilla ones of the samplers kept, then the mod's for the others.
    Also taken from the mod: flags; shader param values of the same name, type and size; shader option values of the
    same name; render infos of the same name and type (the 16 render-state ones only when the mod's RenderState gives
    other values than the vanilla ones: then all 16, the mod changed blending or culling); user data of the same name
    and type; the attribute assigns (they describe the mod's own vertex data). Everything else is the vanilla
    material's."""
    out = copy.deepcopy(donor)
    changed, dropped = [], []
    out.flags = mod.flags
    # samplers and textures: the mod's set and order
    vanilla = {s.name: (s, donor.textures[i] if i < len(donor.textures) else None)
               for i, s in enumerate(donor.samplers)}
    samplers, textures = [], []
    for i, s in enumerate(mod.samplers):
        v, vtex = vanilla.get(s.name, (None, None))
        t = mod.textures[i] if i < len(mod.textures) else vtex
        if t is None:
            dropped.append(f"sampler {s.name} (no texture)")
            continue
        if v is None:
            samplers.append(copy.deepcopy(s))
            changed.append(f"sampler {s.name} added")
        elif same_sampler_state(s.state, v.state):
            samplers.append(copy.deepcopy(v))
        else:
            samplers.append(copy.deepcopy(s))
            changed.append(f"sampler {s.name} values")
        textures.append(t)
        if v is not None and t != vtex:
            changed.append(f"texture {s.name}={t}")
    kept = {s.name for s in samplers}
    changed += [f"sampler {s.name} removed" for s in donor.samplers if s.name not in kept]
    out.samplers, out.textures = samplers, textures
    pairs = [p for p in donor.shader.samplers if p[1] in kept]
    assigned = {p[0] for p in pairs}
    pairs += [p for p in mod.shader.samplers if p[1] in kept and p[1] not in vanilla and p[0] not in assigned]
    out.shader.samplers = pairs
    # shader params
    data = bytearray(out.param_data)
    by = {p.name: p for p in out.params}
    for p in mod.params:
        d = by.get(p.name)
        if d is None or (d.type, d.size) != (p.type, p.size) or p.offset + p.size > len(mod.param_data):
            dropped.append(f"param {p.name}")
            continue
        new = mod.param_data[p.offset:p.offset + p.size]
        if data[d.offset:d.offset + d.size] != new:
            data[d.offset:d.offset + d.size] = new
            changed.append(f"param {p.name}")
    out.param_data = bytes(data)
    # shader options
    opts = dict(mod.shader.options)
    known = {k for k, _ in out.shader.options}
    for i, (k, v) in enumerate(out.shader.options):
        if k in opts and opts[k] != v:
            out.shader.options[i] = (k, opts[k])
            changed.append(f"option {k}={opts[k]}")
    dropped += [f"option {k}" for k in opts if k not in known]
    if out.shader.attribs != mod.shader.attribs:
        out.shader.attribs = list(mod.shader.attribs)
        changed.append("attribute assigns")
    # render infos
    mod_ri = {r.name: r for r in mod.render_infos}
    rs_differs = any(r.name in RENDER_STATE_NAMES and r.name in mod_ri and mod_ri[r.name].kind == r.kind
                     and mod_ri[r.name].values != r.values for r in out.render_infos)
    for i, r in enumerate(out.render_infos):
        m = mod_ri.get(r.name)
        if m is None or m.kind != r.kind or m.values == r.values:
            continue
        if r.name in RENDER_STATE_NAMES and not rs_differs:
            continue
        out.render_infos[i] = copy.deepcopy(m)
        changed.append(f"render info {r.name}")
    have = {r.name for r in out.render_infos}
    dropped += [f"render info {n}" for n in mod_ri if n not in have]
    # user data
    uds = {u.name: u for u in mod.user_data}
    have = {u.name for u in out.user_data}
    for i, u in enumerate(out.user_data):
        m = uds.get(u.name)
        if m is not None and m.kind == u.kind and m.values != u.values:
            out.user_data[i] = copy.deepcopy(m)
            changed.append(f"user data {u.name}")
    dropped += [f"user data {n}" for n in uds if n not in have]
    return out, changed, dropped


def donor_overlay(model: Model, donor: Model, warnings: list) -> tuple[int, int]:
    """Replace each material of `model` (read from a Wii U file, after to_switch) that `donor` (the user's vanilla
    Switch model of the same name) also has by donor_material. Returns (materials built on the vanilla ones, materials
    converted field by field). Fields the vanilla material lacks are listed in a warning."""
    by = {m.name: m for m in donor.materials}
    based = alone = 0
    for i, mat in enumerate(model.materials):
        d = by.get(mat.name)
        if d is None:
            alone += 1
            continue
        model.materials[i], changed, dropped = donor_material(mat, d)
        based += 1
        for smp in model.materials[i].samplers:        # your sampler kept: the notes on the mod's values don't apply
            if "wiiu.sampler_words" not in smp.state.extra:
                prefix = f"material {mat.name!r} sampler {smp.name!r}: "
                warnings[:] = [w for w in warnings if not w.startswith(prefix)]
        if dropped:
            warnings.append(f"model {model.name!r} material {mat.name!r}: not in your vanilla material, dropped: "
                            f"{', '.join(dropped[:6])}{'...' if len(dropped) > 6 else ''}")
        removed = [c.split()[1] for c in changed if c.startswith("sampler ") and c.endswith(" removed")]
        if removed:
            warnings.append(f"model {model.name!r} material {mat.name!r}: sampler(s) your vanilla material has and "
                            f"the mod's lacks were left out: {', '.join(removed)} (if that part looks wrong in game, "
                            f"the Wii U and Switch materials may differ here)")
    return based, alone
