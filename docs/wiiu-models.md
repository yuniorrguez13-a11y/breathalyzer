# Wii U model mods

Status: `tools/wiiu_convert.py` converts the models, textures and item icons of a Wii U mod with `tools/wiiu_model`,
and `modman add` does it for you. Every step is proven by round trips of your own Switch files. No real Wii U model
mod has been tried yet, and no converted model has been seen in game: check them in game (checklist below).

- Models: a Wii U `X.sbfres` (BFRES 4.5) becomes a Switch `X.sbfres` (BFRES 0.5.0.3). That covers skeletons, vertex
  buffers, meshes and LODs, bounds, materials, user data and the animations inside the file: skeletal ones, and
  shader param, colour, tex SRT and texture pattern ones (armour dye). `X_Animation.sbfres` files convert too.
- Textures: a Wii U `X.Tex1` + `X.Tex2` become one Switch `X.Tex.sbfres`. The Tex2 is not written.
- Item icons (`.sbitemico`) convert the same way.

## How a mod is converted

```
python tools/wiiu_convert.py check "D:\mods\Armor Mod" --game "<Savage Wind>\extracted\romfs"
python tools/wiiu_convert.py convert "D:\mods\Armor Mod" --out "D:\mods\Armor Mod (Switch)" --game "<Savage Wind>\extracted\romfs"
```

- A Wii U BFRES is found by its content, loose or inside packs at any depth. A Switch BFRES is used as it is.
- Each `X.Tex1` takes the `X.Tex2` of the same name: from the same pack first, then from loose `Model/`, then from
  any other pack of the mod. The output is `X.Tex.sbfres` where the Tex1 was.
- A pack whose files are renamed (or whose Tex2 goes) is built again. Every file keeps at least its old alignment;
  an uncompressed Switch BFRES gets the 0x1000 its header asks for.
- Converted files are Yaz0-compressed again with alignment field 0, as every Switch file is.
- A file that can't be converted stays Wii U data and is listed with the reason (a variant we don't convert, or
  damaged data). Nothing is guessed: unknown formats and layouts are refused.
- Warnings (a dropped part, a texture without mipmaps, a material without a vanilla twin...) go under "Needs
  attention" in the report. `modman add` says how many there are, and `deploy` lists the mods that have them under
  "Needs attention".

## With your game files (`--game`; modman passes them)

The converter reads your own Switch files (read only) and uses them where the Wii U file alone can't say enough:

- Materials: each material is built on your vanilla material of the same name (from the model file of the same
  name: loose in `Model/`, in the same pack, or in `Pack/TitleBG.pack`). The mod's values go on top: the texture of
  each sampler, shader params of the same name and size, shader options, render infos, user data and the attribute
  assigns. The 16 `gsys_*` render-state infos come from the mod only when its RenderState differs from the vanilla
  one (the mod changed blending or culling). Samplers only the mod has are added. Mod fields your vanilla material
  lacks are dropped and listed. A material your game doesn't have is converted field by field, with a warning.
- Textures without a Tex2: a texture whose main image is unchanged from your game's keeps your game's mipmaps. Any
  other gets one mip level, with a warning (it may look grainy at a distance).
- The BNTX swizzle field comes from your texture of the same name (without it: Alb 0, Nrm 2, others 3).
- `TexInfo.txt` (the texture count of the `X.Tex` the game loads): from the mod's own `X.Tex1`, else from your
  vanilla model. Without either, none is written and a warning says so.
- A material texture that is in no `X.Tex` the game loads (the model's own, or `Link.Tex` for armour) gets a warning:
  that part would render without it.
- A pack holding converted models (`TitleBG.pack`, say) gets the files your game's pack has and the mod's lacks.
- Size table: your game's RSTB is written with the entries of the converted files raised. `X.Tex`: its size + 8208
  (exact on 23/23 of your files). Models: your vanilla entry when the new file is no bigger and has no more parts;
  else the new size plus 1.5 x the vanilla overhead, scaled by how much the model grew (a guess: no formula is known).
  A new model your game has no entry for gets none, with a note: if it crashes on load, merge with UKMM.

Without `--game`: materials field by field, one mip level for textures without a Tex2, `TexInfo.txt` only from the
mod's own `X.Tex1`, no pack filling and no size table. The report says so.

## Animations

- Wii U FSKA becomes a Switch FSKA. Wii U FSHU (shader param `*_fsp`, colour `*_fcl`, tex SRT `*_fts`) and FTXP
  (texture pattern `*_ftp`, armour dye) become Switch FMAA, sorted by name as your files have them (45 of 45).
- Curves keep their encoding; numbers swap by their own size (f32 4, Decimal10x5 and s16 2, u8 and s8 1, bool keys
  as 32-bit words). Integer curve offsets are copied as raw bits.
- A Wii U texture pattern stores its first texture apart; the Switch takes it from the curve. A difference is a
  warning.
- Dropped with a warning: bone and material visibility anims, shape anims, scene anims, texture patterns without a
  curve, and user data of material anims. None of your Switch model files has any of these, so their Switch layout
  can't be checked.

## What is checked

On your own Switch files, read only, at low priority while you played (2026-11-07):

- The mod converter (`wiiu_convert.py verify --groups models --count 8`): 8 of 8 samples pass. Each sample went
  from your Switch file to our Wii U writer (materials with a RenderState and without the 16 render infos, as on Wii
  U), then through `wiiu_convert.py` back to the Switch, and was compared value by value.
  - 4 models with their `X.Tex1` + `X.Tex2` (7 materials, 14 textures). Without your game files: every model value
    equal, `TexInfo.txt` from the Tex1, every texture's blocks equal at every mip. 2 textures get another swizzle
    field from the role rule (no vanilla twin to copy it from). With your game files and the Tex1 only: every
    material equal, and every texture keeps your game's mipmaps, so the blocks are equal again.
  - The size table rules on them: no entry needed raising (each `X.Tex` at size + 8208 is within your game's entry).
  - 1 animation file (36 skeletal, 23 material animations): every value equal.
  - 2 item icons: byte-identical.
  - Link in `Pack/TitleBG.pack`: the pack was rebuilt with `Link.Tex.sbfres` in place of `Link.Tex1`/`Link.Tex2`,
    both files equal value by value and 0x1000-aligned, and the 408 other files filled from your game's pack (410
    names, the same as your game's).
- Animations through our Wii U writer and back (`wiiu_model`): all 33 skeletal and 45 material animations of the 10
  sampled files that have them come back equal (FSKA, FSHU for `*_fsp`/`*_fcl`/`*_fts`, FTXP for `*_ftp`), and the
  Wii U files pass their self-check.
- Models (34 files, 74 models, 251 materials, 1555 bones): Switch -> Switch is byte-identical; Switch -> Wii U ->
  Switch gives every value back; materials written as a real Wii U file has them get all 16 render infos back
  (251/251).
- Textures (26 files, 375 textures, 3233 mip levels): Switch -> Switch is byte-identical; Switch -> Wii U -> Switch
  gives identical blocks at every mip.

The round trips use our own Wii U writer, which is shaped like our reader. So they cannot catch a mistake in how we
read real Wii U files. The decoded checks (normals, indices, bone indices, weights; for textures "mip 1 looks like a
smaller mip 0") are the ones that also work on real data.

## Not converted

- Refused (kept as Wii U data, with the reason): key shapes (morphs), cube maps, texture arrays, 3D and multisampled
  textures, unknown vertex or texture formats, thick or bank-swapped tile modes, files older than BFRES 4.5.
- An `X.Tex2` without its `X.Tex1`: kept (the main image is missing).
- Dropped with a warning: `.bfsha` shader files and textures stored inside a model file (they belong in `X.Tex`).
- Other model and texture formats (BNTX, GTX, BFLIM, map textures) stay Wii U data.

## In-game checklist (for you)

The converter never starts the game. When you try a converted model mod:

1. `python tools/modman.py add "<the Wii U mod>"`, then read "Needs attention" in its `wiiu-convert-report.txt`.
2. Deploy and start. Load a save where the model shows (wear the armour, meet the character).
3. Check: no crash when the model loads, also after travelling to another region; no missing (black, white or
   checkered) textures; nothing grainy at a distance; body parts hidden right under armour; dye still works at the
   Kochi Dye Shop (texture pattern animations); cloth, hair and face animations move.
4. If it crashes on load: turn the mod off (`python tools/modman.py disable <id>`) and note which file the report
   lists. A missing size table entry (new model, no vanilla twin) is the first suspect: merge the mod with UKMM.

## To confirm on the first real Wii U files

Models:

1. Index groups: string-valued dictionaries (shader assign) point straight at the string, as BfresLibrary reads them.
2. The RenderState blend mode is in flags bits 4-5. BfresLibrary masks those bits without shifting them.
3. Volatile flags are one bit per param, lowest bit first.
4. Bone flags, FSKL flags, FSHP flags (0x4) and the total vertex count mean the same on both consoles. Compare a mod
   model with its vanilla Switch twin.
5. 10_10_10_2 normals have the same bit order on both consoles. The decoded check (unit length) settles it.
6. A Wii U material has the same options, params and render info names as the Switch one, apart from the 16. Until
   this is checked, `--game` builds each material on your vanilla one.
7. TexSrtEx is 28 bytes with the pointer last, and BotW's Wii U materials use TexSrt or TexSrtEx.
8. User data types 3 and 4 on Switch, and the byte order of Wii U wide strings.
9. What the raw param size means on Wii U. We write 0 on Switch.

Animations:

10. FSKA, FSHU and FTXP layouts (BfresLibrary's Wii U loaders) and their baked sizes mean the same on both consoles.
11. FTXP texture dictionary values are (name, texture) pairs; the reader uses only the names and warns otherwise.
12. A pattern's first texture equals its curve's first key (warned when not).

Textures:

1. `X.Tex1`: mip data offset is 0. `X.Tex2`: image size is 0, data at 0xB0, mip size equals the data length.
2. GX2 puts (swizzle & 0xFFFF) bytes of padding before the first 1D-tiled mip when that mip is above 1. Our reader
   handles it; our test writer does not add it.
3. The base alignment rule (odd or even macro-tile count) of our test writer.
4. Swizzle bits 16+ match our first-1D-mip rule (a difference is only a warning).
5. Whether `Tex1` and `Tex2` swizzle fields differ, and which one the mips use.
6. The mip check stays above 0.5 on real Wii U textures.
7. The Switch swizzle field our converted textures get works in game.
8. Where Link's `Tex2` lives on Wii U (in `TitleBG.pack` or loose): the pairing handles both.

Size table: the model rule is a guess until a converted model is tried in game.

## How Wii U model files are read

- Only version 4.5 (BotW) is read. Big-endian numbers become little-endian. Vertex components are swapped by their
  own size (10_10_10_2 normals as one 32-bit word). Indices are swapped for the big-endian GX2 index types only.
  Shader param data is swapped as 32-bit words.
- Vertex formats come from GX2's base type and flags (wut's enum values). Formats we do not know are refused.
- The RenderState becomes the 16 `gsys_*` render infos the Switch uses, using BfresLibrary's BotW strings. They are
  added at the end, only when missing. `gsys_alpha_test_ref_value` gets the alpha reference. A blend function or
  operation that is not in BfresLibrary's list gets its name by the same pattern, with a warning.
- Mode, face, blend mode and compare values that none of your Switch files use (for example `back`, `none`,
  `custom`, `logic`, `never`) are written with a warning, because their names are not confirmed.
- GX2 sampler words become nn::gfx sampler values. GX2 clamp modes 4, 5 and 7 have no Switch twin: the nearest is
  used, with a warning. "No mip filter" becomes "point". A depth compare sampler gets the nn::gfx comparison bit
  (0x80), and anisotropy above 1:1 is kept as max anisotropy without the anisotropic bit. Both get a warning, because
  every sampler in your files is filter 0x29 with anisotropy 1.
- TexSrtEx params (a TexSrt plus a 4-byte run-time pointer) become TexSrt, as on the Switch. The pointer is dropped,
  later params move down 4 bytes, and a warning says so.
- User data uses one numbering on both consoles, BfresLibrary's: 0 int, 1 float, 2 string, 3 wide string, 4 bytes.
  Bytes are written as type 4 on Switch. Your files only show types 0-2, so 3 and 4 are not confirmed on Switch.
  Wide strings are read as UTF-16 big-endian and become strings, with a warning. A Switch file with type 3 is refused.
- The Wii U raw param size is not carried over. The Switch file gets 0, as all your files have, with a note.
- A null offset with a count above 0 (a damaged file) is refused. It is never read as "the start of the file".
- A Switch file with no GPU data and no external file (an animation file) gets alignment 2^3, others 2^12 (28 of 28
  of your files).

## How Wii U texture files are read

- The file name decides what each texture entry holds, as in BfresLibrary. A `X.Tex1` entry holds mip 0, a `X.Tex2`
  entry holds the mips, other files (item icons) hold both.
- A missing offset (0) is never read as "the start of the file". Mip offsets must increase.
- A bad `Tex2` entry costs only that texture its mips, with a note.
- Refused: cube maps, arrays, 3D, multisampled textures, unknown formats, thick or bank-swapped tile modes, and tile
  mode 16 with mips.

## Developer commands

    python tools/wiiu_model inspect FILE
    python tools/wiiu_model roundtrip FILE
    python tools/wiiu_model convert TEX1 [TEX2] -o OUT [--vanilla X.Tex.sbfres]
    python tools/wiiu_model verify --dec DIR [--romfs ROMFS] [--icons N] --out REPORT.txt [--json R.json]
    python tools/wiiu_model model-inspect FILE
    python tools/wiiu_model model-roundtrip FILE
    python tools/wiiu_model model-convert FILE -o OUT
    python tools/wiiu_model verify-models --dec DIR [--romfs ROMFS] [--extra N] [--samples DIR] --out R.txt
                                          [--json R.json]
    python tools/wiiu_convert.py verify "<Savage Wind>\extracted\romfs" --out out\wiiu-roundtrip --groups models

Inputs may be Yaz0-compressed. Game files and samples are only read. `verify-models --samples` parses, checks and
converts every Wii U model it finds under that folder (loose, or inside SARC packs). The report prints the decoded
numbers for each sample, and the command exits with 1 when a file or a sample fails.
