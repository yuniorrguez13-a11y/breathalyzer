# Wii U mods

`tools/wiiu_convert.py` turns a Wii U BotW mod into a Switch RomFS mod: data files where the two versions differ
only in byte order and folder layout, and models, textures and item icons ([wiiu-models](wiiu-models.md)). With
`modman` you don't need to run it: `modman add` on the Wii U mod converts it for you, with your game files for the
models. Everything runs on your PC, from the mod you have; nothing is uploaded.

**UKMM (Switch mode) is still the best route** when you use several mods that change the same files (say two mods
with their own `Bootup.pack`), or when a mod adds files or makes them bigger (UKMM writes a matching RSTB). This tool
converts one mod at a time and merges nothing.

## How to use it

With the mod manager, add the Wii U mod itself. It converts the mod into the library, refuses one where nothing
converts, and `list` and `deploy` keep warning about files that stay Wii U data
([mod-manager](mod-manager.md#wii-u-mods)):

```
python tools/modman.py add "D:\mods\Armor Mod"
```

To look first, or to use the mod with `tools/romfs_mods.py`, run the converter yourself:

```
python tools/wiiu_convert.py check "D:\mods\Armor Mod"
python tools/wiiu_convert.py convert "D:\mods\Armor Mod" --out "D:\mods\Armor Mod (Switch)" --game "<Savage Wind>\extracted\romfs"
```

- `check` lists what converts and what doesn't, and writes nothing (`--json` for JSON).
- `--game` (both commands) points at your game's RomFS, which is only read. Models then get your vanilla materials,
  `TexInfo.txt` and size table entries, textures without a Tex2 keep your game's mipmaps where unchanged, and packs
  holding models get the files your game's pack has ([wiiu-models](wiiu-models.md)). `modman add` passes it.
- `convert` writes the Switch mod into a new (or empty) folder: `romfs/` for the game, `01007EF00011F001/romfs/`
  for the DLC, and `wiiu-convert-report.txt` (the same text `check` prints). When nothing in the mod can be
  converted, it writes nothing. `modman add` on that folder reads the report, so it gives the same warnings as
  adding the Wii U mod.
- `--drop-unconverted` leaves out whole files that stay Wii U data, instead of copying them unchanged (a pack
  with only some such files inside is still written). A Wii U model in the Switch game can crash it; without the
  file, the game uses its own one if the mod only replaced it.
- Exit code: 0 everything converted, 1 some files stay Wii U data or were left out, 2 the mod can't be used: nothing
  in it can be converted (only audio, say, or only the RSTB, which is always left out), or the message says why.
- Files converted with warnings (models and textures) are listed under "Needs attention" in the report: they were
  converted, but check them in game.

A mod is a folder or a `.zip` with `content/` (game files) and/or `aoc/0010/` (DLC files), also inside one wrapper
folder or a Cemu graphic pack (`<pack>/rules.txt` + `<pack>/content/`). You can also pass the `content` folder
itself. Files outside those folders (readme, `rules.txt`, preview pictures) are left out. A download with several
options (two packs with their own `content/`) is refused: convert the one you want. Switch mods, UKMM packages,
packed BCML packages (`info.json` with `logs/`) and Cemu graphic packs made for another game (no BotW title id in
`rules.txt`) are refused too (Switch mods need no conversion; install the packages in UKMM). A game file whose name
Windows can't use, or that could lead out of the output folder (`..`, `C:`, a `\`, a trailing dot or space,
`con`...), is left out and listed in the report: nothing is written outside `--out`, and no file is written over.

## What converts

| Format | Files | What happens |
|---|---|---|
| Yaz0 | every `.s*` file | Unpacked, the content converted, packed again (same alignment field). The Yaz0 header is big-endian on both consoles |
| SARC | `.pack`, `.sbactorpack`, `.ssarc`, `.sbeventpack`... | Byte order mark, header, SFAT and SFNT swapped; names, name hashes and file order kept. Files inside are converted too, at any depth. If a file inside changes size, the files are laid out again in the same order, each at least as aligned as before |
| BYML | maps (`.smubin`), actor info, game data, `.byml`/`.sbyml`, `.baniminfo`... | `BY` (Wii U) to `YB` (Switch), version kept (BotW: 2). All node types: dictionaries, arrays, string tables, strings, bool, int, uint, float, int64, uint64, double, binary data, null. Shared nodes are swapped once |
| MSBT | texts (`Msg_*.product.ssarc` in `Bootup_<lang>.pack`) | Byte order mark, header, LBL1 (labels), ATR1 (attribute strings), TXT2 (text and tags), and TSY1/NLI1 if present |
| AAMP | actor parameters (`.bxml`, `.bphysics`, `.bgparamlist`...) | Nothing to do: AAMP is little-endian on both consoles. Its header flags say so (bit 0), and all 102,260 AAMP files in the 7,211 actor packs of the Switch game have flags 3. A big-endian one is reported. Used as is |
| BFRES models | `Model/X.sbfres`, `X_Animation.sbfres` (loose or in packs) | Rebuilt as Switch BFRES 0.5.0.3 by `tools/wiiu_model`: skeleton, vertex data, meshes, materials (on your vanilla ones with `--game`), user data, skeletal and material animations, `TexInfo.txt` ([wiiu-models](wiiu-models.md)) |
| BFRES textures | `Model/X.Tex1.sbfres` + `X.Tex2.sbfres`, `UI/StockItem/*.sbitemico` | Untiled from the Wii U GPU layout and tiled for the Switch: Tex1 + Tex2 become one `X.Tex.sbfres` (the Tex2 is not written; in a pack, the pack is built again with the new names) |
| Text | `.txt`, `.xml`, `.json`, `.yml`... | Used as is |
| Empty files | any | Used as is (an empty file is the same on both consoles) |

Text tags: most tag values are 16-bit numbers or strings and swap the same way. The few tags with 32-bit or 8-bit
values were found by reading every tag in the Switch game's texts (16 languages). A tag the game's own text never
uses is converted as 16-bit values and named in the report: check those lines in game.

## What doesn't convert, and why

These files are kept unchanged (or left out with `--drop-unconverted`) and listed in the report. The game can't read
them: where the mod uses them it may crash or look wrong.

| Kind | Why |
|---|---|
| Model parts the converter refuses (key shapes, cube maps, unknown formats...), an `X.Tex2` without its `X.Tex1`, and other texture formats (BNTX, GTX, BFLIM, map textures) | See [wiiu-models](wiiu-models.md). The report gives the reason for each file |
| Audio (`.bfstm`, `.bars`, `.bfwav`, `.bfsar`...) | Encoded differently for each console |
| Havok physics (`.hk*`, `.shk*`, also inside actor packs) | Built for each console |
| Shaders, effects (`.ptcl`, `.sesetlist`), fonts, movies | Built for each console's GPU or video player |
| UI layouts (`.bflyt`, `.bflan`), event flows (`.bfevfl`), ELink/SLink, PrOD (`.blwp`), terrain, ecosystem | Byte order differs, but this tool doesn't convert them yet |
| RSTB (`ResourceSizeTable.product.srsizetable`) | Left out. The byte order converts, but its sizes are Wii U sizes and the Switch's differ (64-bit). The game's own RSTB is used: merge with UKMM if the mod adds or grows files. With `--game`, converted models and textures that need more get your game's RSTB with their entries raised |
| Unknown formats | Kept as they are |

A pack holding only files that can't be converted (a layout archive, say) is kept whole. A pack with some of each
is converted, and the files inside that couldn't be are listed (`pack//file inside`).

## Proof that nothing is lost

The Wii U files aren't needed for this: `verify` converts your own Switch files to Wii U byte order and back, and
checks two things for each one. The result must be the same as the original, and the Wii U copy must read the same
(every BYML value, label, text, tag, size entry and file name inside, compared one by one).

```
python tools/wiiu_convert.py verify "<Savage Wind>\extracted\romfs" --out out\wiiu-roundtrip
```

It only reads the game folder and writes a report (`wiiu-roundtrip-report.txt` and `.json`) to `--out`. Keep that out
of the repo (`out\` is ignored). It takes about 7 minutes (26 at low priority while the game ran); `--count` sets
how many actor packs and maps it samples
(default 20). It also runs model groups (half of `--count` models with their textures, an animation file, two item
icons, and Link inside `TitleBG.pack`), which go through the whole mod converter with and without your game files.
`--groups models` runs only those (seconds); `--groups data` only the others. On 2026-11-07: 8 of 8 model samples
passed ([wiiu-models](wiiu-models.md#what-is-checked)).

Run on 2026-11-07 on the user's own game (1.9.0): 63 files, **no failures**.

| Sample | Files |
|---|---|
| Actor packs (Yaz0 SARC with AAMP, BYML and Havok inside) | 20 |
| `Bootup_<lang>.pack`, all 16 languages (SARC, Yaz0 SARC, MSBT) | 16 |
| Dungeon packs (SARC with Yaz0 maps, actor packs, models, Havok) | 4 |
| `Bootup.pack` (31 MB: game data, more packs, layouts, sound) | 1 |
| Map files (`*_Dynamic.smubin`) | 20 |
| `ActorInfo.product.sbyml` | 1 |
| RSTB | 1 |

Inside them, 17,535 MSBT files (about 1,100 per language), 99 BYML documents (1.37 million values: dictionaries,
arrays, strings, ints, uints, floats, bools), 67 SARC packs and the RSTB went to Wii U byte order and back; 510
AAMP files were kept as they are. Every text tag had a known layout. All 63 files came back the same once
unpacked, and every Wii U copy read the same as the original. BotW's files have no int64, uint64, double or
binary BYML values: the tests cover those with made-up files.

Yaz0 files come back with other compressed bytes than Nintendo's (our encoder is a different one), so packs that
hold Yaz0 files are laid out again. That is the only difference: unpacked, every file is byte-identical.

The round trip proves the converters lose nothing and are each other's inverse. It can't prove that a Wii U file
has the same layout as the Switch one in every detail; that comes from the formats themselves (and for text tags,
from the game's texts, above).

## Tested in game

On 2026-11-07 with Savage Wind 0.4.1-alpha.4. No real Wii U mod was at hand, so the test made one from the user's
own game:

1. In a copy of `Pack/Bootup_USen.pack`, the title menu's "Continue" (`LayoutMsg/Title_00.msbt`, label `0000`)
   was changed to "WIIUMOD!", the same length, so no size table entry changes.
2. That copy was turned into Wii U form (the `verify` code path; SARC and MSBT byte order marks `FE FF`) and laid
   out as `content/Pack/Bootup_USen.pack`.
3. `convert` said "everything converted"; unpacked, the result matched the Switch copy.
4. Deployed with `tools/romfs_mods.py`: the title menu showed "WIIUMOD!" and the save loaded and played normally.

The pack came out 52 KB bigger (our Yaz0 encoder) and still loaded; this pack has no size table entry.
Still not tried: a real Wii U mod, and maps or actor data in game.
