"""Wii U mod textures and models -> Switch files (textures: milestone M1; models, TexInfo.txt, vanilla materials and
the size table rules: Integrate, used by tools/wiiu_convert.py).

`X.Tex1` (+ `X.Tex2`) -> one Switch `X.Tex` (BFRES + BNTX, full mip chains). When a texture's mips are missing (no
Tex2, or its Tex2 entry is unusable), a texture whose level 0 is block-identical to the user's vanilla Switch texture
of the same name keeps the vanilla mips; any other texture gets one mip level and a note (design section 6.3). The
vanilla file is optional and only read for the textures that need it; a vanilla texture this reader refuses only
loses that help. Nothing is guessed: unknown formats, tile modes or inconsistent layouts raise `Unsupported` /
`Damaged`.

The BNTX swizzle field (BRTI +0x14) is not the Wii U pipe/bank swizzle. In the user's Switch files it takes the
values 0-4, grouped by texture role (Alb 0, Nrm 2, Spm / Emm / AO 3 in most). A converted texture gets the value of
its vanilla Switch twin (same name) when there is one, otherwise the role default below; the Wii U value stays in
extra (`wiiu.swizzle`). What the field does on the console is unknown: check against real samples (V3).
"""
from __future__ import annotations

from . import checks, materials, nx_bfres, nx_model, wiiu_bfres, wiiu_models
from .errors import Damaged, ModelError
from .neutral import ExtFile, ResFile, Texture, TextureSet, diff

TEXINFO = "TexInfo.txt"
# RSTB entry of a Switch `X.Tex`: decompressed size + 8208, exact on 23/23 of the user's files (switch-format.md 9)
RSTB_TEX_EXTRA = 8208
# Model RSTB entries have no known formula (open point, design section 13): the converter takes the larger of the
# vanilla entry and the new size plus the vanilla overhead, scaled by how much the model grew and by this margin
RSTB_MODEL_MARGIN = 1.5

# Role default of the BNTX swizzle field, by a name part (`_Alb`, `_Nrm`, ...). Measured on 372 textures of 23 of the
# user's Switch X.Tex files: Alb 0 in 184 of 200, Nrm 2 in 80 of 86, the rest 3 in 79 of 86 (343 of 372 in all).
SWITCH_SWIZZLE_BY_ROLE = {"Alb": 0, "Nrm": 2}
SWITCH_SWIZZLE_DEFAULT = 3


def switch_swizzle_default(name: str) -> int:
    """BNTX swizzle field for a texture with no vanilla twin: Alb 0, Nrm 2, anything else 3."""
    for part in reversed(name.replace(".", "_").split("_")):
        if part in SWITCH_SWIZZLE_BY_ROLE:
            return SWITCH_SWIZZLE_BY_ROLE[part]
    return SWITCH_SWIZZLE_DEFAULT


def _vanilla(vanilla_tex, needed: set[str], notes: list) -> tuple[dict[str, Texture], dict[str, int]]:
    """(vanilla textures among `needed`, {name: BNTX swizzle field}) of the user's vanilla `X.Tex`; empty on a
    file this reader refuses (with a note)."""
    if vanilla_tex is None:
        return {}, {}
    try:
        swizzles = nx_bfres.swizzle_fields(vanilla_tex)
        refused: list[str] = []
        textures = nx_bfres.read_textures(vanilla_tex, only=needed, refused=refused).by_name() if needed else {}
    except ModelError as e:
        notes.append(f"vanilla texture file not usable ({type(e).__name__}: {e}); no vanilla mipmaps or values used")
        return {}, {}
    if refused:
        notes.append(f"vanilla textures not usable, so their mipmaps cannot be reused: {'; '.join(refused)}")
    return textures, swizzles


def textures_to_switch(tex1, tex2=None, vanilla_tex=None, notes: list | None = None) -> tuple[bytes, TextureSet]:
    """(Switch `X.Tex` / item icon bytes, neutral set) from decompressed Wii U Tex1 (+ Tex2). `vanilla_tex`: the
    user's decompressed vanilla Switch `X.Tex` (optional, read-only), used for missing mips and the BNTX swizzle
    field."""
    notes = [] if notes is None else notes
    ts: TextureSet = wiiu_bfres.read_textures(tex1, tex2, notes)  # Unsupported when the file has no textures
    needed = {t.name for t in ts.textures if t.extra.get("wiiu.mips_missing")}
    vanilla, swizzles = _vanilla(vanilla_tex, needed, notes)
    kept, single = [], []
    for t in ts.textures:
        t.swizzle_hint = swizzles.get(t.name, switch_swizzle_default(t.name))
        if not t.extra.get("wiiu.mips_missing"):
            continue
        v = vanilla.get(t.name)
        if (v is not None and v.format == t.format and (v.width, v.height) == (t.width, t.height)
                and v.levels[0] == t.levels[0]):
            t.levels = list(v.levels)
            kept.append(t.name)
            note = t.extra.get("wiiu.mips_missing_note")
            if note in notes:
                notes.remove(note)
        else:
            single.append(t.name)
    if kept:
        notes.append(f"vanilla mipmaps kept for {len(kept)} unchanged texture(s): {', '.join(kept)}")
    if single:
        notes.append(f"no mipmaps for {len(single)} texture(s) (they may look grainy at a distance): "
                     f"{', '.join(single)}")
    out = nx_bfres.write_textures(ts)
    problems = checks.switch_tex_problems(out)
    if problems:
        raise Damaged(f"the converted textures fail the Switch self-check: {problems[:3]}")
    return out, ts


# ------------------------------------------------------------------------------------------ models (Integrate)

def wiiu_texture_names(tex1) -> list[str]:
    """Names of the textures in a Wii U texture BFRES (Tex1, Tex2 or single file), from its header only."""
    h = wiiu_bfres.read_header(tex1)
    return [n for n, _ in wiiu_bfres.read_group(h["r"], h["groups"][1])]


def switch_texture_names(tex) -> list[str]:
    """Names of the textures in a Switch `X.Tex` BFRES (from the BRTI headers only)."""
    return list(nx_bfres.swizzle_fields(tex))


def wiiu_counts(data) -> list[int]:
    """The 12 group counts of a Wii U BFRES header: models, textures, 9 animation kinds, external files."""
    return wiiu_bfres.read_header(data)["counts"]


def texinfo(res: ResFile) -> int | None:
    """The TexInfo.txt number of a Switch model ResFile (None when it has none or it is not a number)."""
    for e in res.external:
        if e.name == TEXINFO:
            try:
                return int(bytes(e.data).decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                return None
    return None


def set_texinfo(res: ResFile, count: int | None) -> None:
    """TexInfo.txt = count, where the file has it (else first); None removes it."""
    at = next((i for i, e in enumerate(res.external) if e.name == TEXINFO), None)
    if count is None:
        if at is not None:
            del res.external[at]
        return
    ext = ExtFile(TEXINFO, str(count).encode("ascii"))
    if at is None:
        res.external.insert(0, ext)
    else:
        res.external[at] = ext


def size_count(res: ResFile) -> int:
    """Shapes + materials + bones of a model file: what the model RSTB rule scales with."""
    return sum(len(m.shapes) + len(m.materials) + len(m.skeleton.bones) for m in res.models)


def model_file_to_switch(data, notes: list, tex_count: int | None = None,
                         donor: ResFile | None = None) -> tuple[bytes, ResFile, dict]:
    """(Switch model bytes, neutral ResFile, info) from a decompressed Wii U model / animation BFRES:
    read_wiiu_model, then model_to_switch.

    - Materials get the Switch render infos from the RenderState (materials.to_switch). With `donor` (the user's
      vanilla Switch file of the same name, read with nx_model), a material the vanilla model of the same name also
      has is built on the vanilla material with the mod's values on top (materials.donor_overlay).
    - `tex_count`: the texture count of the X.Tex the game will load, written as TexInfo.txt (None: none written).
    - Animations convert (wiiu_anims); kinds without a checked Switch layout are dropped with a warning.
    The output passes the Switch self-check or `Damaged` is raised. info: donor_materials, alone_materials, models,
    skel_anims, mat_anims."""
    res = read_wiiu_model(data, notes)
    out, info = model_to_switch(res, notes, tex_count, donor)
    return out, res, info


def read_wiiu_model(data, notes: list) -> ResFile:
    """A decompressed Wii U model / animation BFRES as a neutral ResFile, materials prepared for the Switch."""
    res = wiiu_models.read(data, notes)
    for m in res.models:
        materials.to_switch(m, notes)
    return res


def is_model_file(res: ResFile) -> bool:
    """A model file (vertex data) rather than an animation file (X_Animation: a skeleton and animations)."""
    return any(m.vertex_buffers for m in res.models)


def needs_donor(res: ResFile) -> bool:
    """Whether the vanilla twin helps: a model file, or any file with materials. An animation file only needs the
    vanilla file's size (the size table)."""
    return is_model_file(res) or any(m.materials for m in res.models)


def same_as_vanilla(res, vanilla) -> bool:
    """Whether a converted neutral ResFile / TextureSet holds the same values as the user's vanilla one (every value
    the Switch file stores; platform extras aside). The converter then writes the game's own bytes."""
    return diff(vanilla, res, "cross", 1) == []


def model_to_switch(res: ResFile, notes: list, tex_count: int | None = None,
                    donor: ResFile | None = None) -> tuple[bytes, dict]:
    """(Switch bytes, info) of a ResFile from read_wiiu_model (changed in place: donor materials, TexInfo.txt)."""
    info = {"donor_materials": 0, "alone_materials": 0, "models": len(res.models),
            "skel_anims": len(res.skel_anims), "mat_anims": len(res.mat_anims)}
    if donor is not None:
        by = {m.name: m for m in donor.models}
        for m in res.models:
            d = by.get(m.name)
            if d is None:
                info["alone_materials"] += len(m.materials)
                continue
            based, alone = materials.donor_overlay(m, d, notes)
            info["donor_materials"] += based
            info["alone_materials"] += alone
    else:
        info["alone_materials"] = sum(len(m.materials) for m in res.models)
    set_texinfo(res, tex_count)
    out = nx_model.write(res)
    problems = checks.switch_model_problems(out)
    if problems:
        raise Damaged(f"the converted model fails the Switch self-check: {problems[:3]}")
    return out, info


def with_texinfo(vanilla: bytes, count: int) -> bytes:
    """The user's vanilla Switch model file (decompressed) written again with TexInfo.txt = count (nx_model gives the
    same bytes for an unchanged file)."""
    res = nx_model.read(vanilla)
    set_texinfo(res, count)
    out = nx_model.write(res)
    problems = checks.switch_model_problems(out)
    if problems:
        raise Damaged(f"the model with the new TexInfo.txt fails the Switch self-check: {problems[:3]}")
    return out


def rstb_tex(size: int) -> int:
    return size + RSTB_TEX_EXTRA


def rstb_model(new_size: int, new_count: int, vanilla_entry: int, vanilla_size: int, vanilla_count: int) -> int:
    """RSTB entry for a converted model with a vanilla twin: the vanilla entry when the new file is no bigger and has
    no more shapes+materials+bones; else the larger of the vanilla entry and the new size plus the vanilla overhead
    (entry - size) scaled by max(1, new / vanilla count) and RSTB_MODEL_MARGIN."""
    if new_size <= vanilla_size and new_count <= vanilla_count:
        return vanilla_entry
    overhead = max(0, vanilla_entry - vanilla_size)
    grow = max(1.0, new_count / vanilla_count) if vanilla_count else 1.0
    return max(vanilla_entry, new_size + int(-(-overhead * grow * RSTB_MODEL_MARGIN // 1)))
