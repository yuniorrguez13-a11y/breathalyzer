"""Wii U -> Switch model/texture converter for BotW mods (pure Python, no third-party packages).

Milestone M1 (textures): GX2 (Wii U FTEX in X.Tex1/X.Tex2 BFRES 4.5) and Tegra block-linear (Switch BNTX in X.Tex
BFRES 0.5) read and write, both ways, through a neutral linear TextureSet.
Milestone M2 (models): Switch BFRES 0.5 model files (FMDL, FSKL, FVTX, FSHP, FMAT, user data, external files and the
embedded FSKA/FMAA animations) and Wii U BFRES 4.5 model files (RenderState and GX2 sampler words translated by
materials.py) read and write through a neutral ResFile.
Milestone M3 (Integrate): Wii U animations (FSKA, FSHU, FTXP; wiiu_anims.py), materials built on the user's vanilla
ones (materials.donor_overlay), TexInfo.txt and the size table rules (convert.py); tools/wiiu_convert.py uses them for
the model, texture and item icon files of a Wii U mod.
Design and format notes: out/model-work/design.md, switch-format.md, wiiu-format.md (work files, not in the repo).

This package never imports other tools; tools/wiiu_convert.py calls into it.
"""
from __future__ import annotations

from .errors import Damaged, ModelError, Unsupported
from .neutral import ResFile, Texture, TextureSet, describe, diff
from . import checks, materials, nx_bfres, nx_bntx, nx_model, wiiu_bfres, wiiu_models


def sniff(data) -> str | None:
    """'switch' / 'wiiu' for a (decompressed) BFRES, 'bntx' for a bare BNTX, else None."""
    head = bytes(data[:16])
    if head[:8] == nx_bfres.MAGIC and head[0x0C:0x0E] == b"\xff\xfe":
        return "switch"
    if head[:4] == b"FRES" and head[8:10] == b"\xfe\xff":
        return "wiiu"
    if head[:8] == nx_bntx.MAGIC:
        return "bntx"
    return None


def read_textures_switch(data) -> TextureSet:
    """Neutral TextureSet from a Switch `X.Tex` BFRES or item icon (decompressed)."""
    return nx_bfres.read_textures(data)


def write_textures_switch(ts: TextureSet) -> bytes:
    return nx_bfres.write_textures(ts)


def read_textures_wiiu(tex1, tex2=None, warnings: list | None = None) -> TextureSet:
    """Neutral TextureSet from Wii U `X.Tex1` (+ `X.Tex2`) or a single-file texture BFRES (decompressed)."""
    return wiiu_bfres.read_textures(tex1, tex2, warnings)


def write_textures_wiiu(ts: TextureSet) -> tuple[bytes, bytes | None]:
    """(Tex1, Tex2 or None) Wii U files for a texture set (Switch -> Wii U exists only for tests)."""
    return wiiu_bfres.write_textures(ts)


def read_model(data, warnings: list | None = None) -> ResFile:
    """Neutral ResFile of a decompressed Switch or Wii U model BFRES (by its byte order)."""
    kind = sniff(data)
    if kind == "switch":
        return nx_model.read(data)
    if kind == "wiiu":
        return wiiu_models.read(data, warnings)
    raise Unsupported("not a BFRES file")


def write_model(res: ResFile, platform: str = "switch") -> bytes:
    """Switch BFRES 0.5.0.3 bytes ('switch'), or Wii U 4.5 bytes ('wiiu', for tests only)."""
    if platform == "switch":
        return nx_model.write(res)
    if platform == "wiiu":
        return wiiu_models.write(res)
    raise ValueError(f"unknown platform {platform!r}")


def model_to_switch(data, warnings: list | None = None) -> tuple[bytes, ResFile]:
    """(Switch model bytes, neutral ResFile) from a decompressed Wii U model BFRES, converted field by field: the
    materials get the render infos the Switch keeps instead of the RenderState (materials.to_switch), animations
    convert (wiiu_anims), textures inside the model file are dropped (a warning says so). The output passes the Switch
    self-check or `Damaged` is raised; nothing unchecked is returned. No TexInfo.txt is written: use
    convert.model_file_to_switch for that and for vanilla materials (tools/wiiu_convert.py does)."""
    warnings = [] if warnings is None else warnings
    res = wiiu_models.read(data, warnings)
    for m in res.models:
        materials.to_switch(m, warnings)
    out = nx_model.write(res)
    problems = checks.switch_model_problems(out)
    if problems:
        raise Damaged(f"the converted model fails the Switch self-check: {problems[:3]}")
    return out, res


__all__ = ["ModelError", "Unsupported", "Damaged", "Texture", "TextureSet", "ResFile", "describe", "diff", "sniff",
           "read_textures_switch", "write_textures_switch", "read_textures_wiiu", "write_textures_wiiu",
           "read_model", "write_model", "model_to_switch"]
