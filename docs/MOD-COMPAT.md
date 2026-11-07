# Mod compatibility: Wii U and Switch mods on our loader

Goal: players keep the mods they already use. `tools/mod_inspect.py` checks a mod folder or `.zip` and gives each
part of it one of these verdicts (the table below says which).

```
python tools/mod_inspect.py "<mod folder or .zip>"
```

| Verdict | Meaning |
|---|---|
| works | Used as is |
| convert | Works after the loader converts it on the player's PC |
| partial | Partly works: the loader uses what it can and names the rest; not tried in the game yet |
| merge | Works after merging in UKMM (Switch mode): the loader takes UKMM's output folder, not the package |
| later | Planned, not supported yet |
| retarget | Made for another game version; needs porting to 1.9.0 |
| rewrite | Can't run on the PC build; must be remade as a native mod |

## By mod type

| Mod type | Verdict | How |
|---|---|---|
| Native mod for this loader (`mod.toml`, a DLL using `native/modhost/swmod.h`) | works | `tools/modman.py` adds and deploys it ([mod-format](mod-format.md)). Tested in game |
| Switch RomFS mod (`romfs/`, `01007EF00011E000/romfs`) | works | `tools/romfs_mods.py` fills Savage Wind's overlay folder (`SAVAGEWIND_ROMFS_OVERLAY`). Tested in game. Files in folders the game doesn't have are skipped, and the size table (RSTB) isn't fixed for you: merge with UKMM first when a mod adds or grows files |
| UKMM package, Switch or cross-platform | partial | `tools/modman.py add` builds it from the game's files ([ukmm](ukmm.md)): its options, packs, plain files, actor links, model lists, animation lists, general and other plain parameter files, and a size table (merged with other mods' at deploy). Needs UKMM installed (its zstd dictionary is read from `ukmm.exe`). Physics, animation sequences (`.bas`), texts, actor info, game data, maps and the other UKMM kinds aren't merged yet: left out, and `add` and `tools/ukmm.py info` say first how many of the package's resources that is. In the one real Switch package tried (Sheikah Run) the animation sequences, which are the mod, aren't merged; a cross-platform package holds only mergeable data, mostly of kinds not merged yet. Not tried in the game yet; merging several mods still needs UKMM (Switch mode) |
| BCML `.bnp`, Switch | works | `tools/modman.py add` installs it ([bnp](bnp.md)): its options, its packs filled from the game, every BCML 3 log merged (actor parameters, animation lists, game and save data, actor info, events, area data, residents, texts in every language, drops, shops, maps, quests) and a size table (merged with other mods' at deploy). Packs aren't merged between two packages yet: the later one's wins and *Needs attention* says what the earlier loses. A BCML 2 package's own logs (`savedata.sarc`, `deepmerge.yml`, `texts_*`) aren't merged: `partial`. The data logs were tried on made-up packages and the game's files only, the whole thing not in the game yet |
| Wii U data that can be merged (BYML, AAMP, MSBT, maps, actor params) | convert | Converts with `tools/wiiu_convert.py` (text, maps, actor data, and models below; audio not yet), on the player's PC ([wiiu-mods](wiiu-mods.md)). `tools/modman.py add` does it for you. A converted Wii U text mod was tested in game. Several mods that change the same file still need UKMM (Switch mode) to merge them. A Wii U UKMM package or packed `.bnp` is not converted: merge it in UKMM first |
| Wii U models and textures (`.sbfres` models with their animations, `X.Tex1` + `X.Tex2`, item icons) | convert | `tools/wiiu_convert.py` converts them with `tools/wiiu_model` ([wiiu-models](wiiu-models.md)); `tools/modman.py add` passes the game's own files for vanilla materials, mipmaps, `TexInfo.txt`, packs and the size table. Proven by round trips on the player's own Switch files only: no real Wii U model mod tried yet, nor any converted model in game. An `X.Tex2` without its `X.Tex1` stays Wii U data. A Wii U UKMM package with models is still "later" (UKMM keeps them as its own data) |
| Wii U audio, Havok physics, UI layouts, effects, event flows | later | Not converted yet: `tools/wiiu_convert.py` keeps them as Wii U data, which the game can't read (a mod with only these is refused by `modman add` and by `wiiu_convert.py`, which then writes nothing; its RSTB doesn't count). A Wii U UKMM package with these is "later" too: deploy it in UKMM (Wii U mode) and add that output to use the rest |
| ExeFS patch (`.ips`, `.pchtxt`) for 1.9.0 | works | `tools/exefs_patch.py`. Patches to data need no rebuild (tested in game); patches to code need `tools/sw_rebuild.py` (10-20 min) |
| ExeFS patch for another version (usually 1.6.0) | retarget | Find the same code in 1.9.0 with masked patterns, as Savage Wind's `game_sites.py` does |
| Atmosphère cheats for 1.9.0 | works (data cheats) | `tools/cheats.py` through Savage Wind's control port (tested in game). Cheats that write game code do nothing; button-combo conditions count as not pressed unless mapped to keys with `--hotkeys` |
| exlaunch / Skyline code mod (`subsdk1-9`, `main.npdm`) | rewrite | Remake as a native mod (a DLL using `native/modhost/swmod.h`; tested in game). Most BotW ones target 1.5.0 anyway |
| Cemu code patch (PowerPC `.asm`, `patches.txt`) | rewrite | Wii U code; remake as a native mod with our SDK. FPS, FOV and draw distance are already Savage Wind options |
| Cemu shader pack (`*_ps.txt`, `*_vs.txt`) | rewrite | Cemu-only. Savage Wind has its own graphics options |
| Cemu settings preset (rules.txt only) | rewrite | Not needed: Savage Wind's own options |

## First check on the user's own mods (2026-11-06)

- 26 UKMM Wii U mods: 4 can be merged (merge: install them in UKMM, Switch mode); 22 need model or texture
  conversion (mostly character and armor models). Neither converter takes a UKMM package itself: deploy it in UKMM
  (Wii U mode), then add UKMM's output folder, whose models `wiiu_convert.py` converts now.
- 15 Cemu packs under "Mods": 14 are code patches or shaders (rewrite); 1 is UKMM's deployed output (Wii U data,
  convert).

## Rules that keep this clean

- All conversion runs on the player's PC, from their own files. We never ship converted game files.
- Converted output stays in the player's data folder and is never uploaded or shared.
- We read other authors' mods but never bundle them (upstream PR #439 was closed for exactly this).

## Build order

1. ~~Switch RomFS and UKMM/BCML Switch mods~~ done (overlay deploy). BCML `.bnp` packages: installed by
   `tools/modman.py add` ([bnp](bnp.md)), every BCML 3 log merged; next: merging two packages' packs at deploy.
2. ~~ExeFS patches for 1.9.0~~ done; next: retargeting older (1.6.0) patches with masked patterns.
3. ~~Cheats~~ done for data cheats.
4. ~~Native code mods~~ done (host + API 2 hooks, API 3 settings pages being built: [mod-settings](mod-settings.md)).
   Hooks go before, after or instead of any function of `main`, via a rebuild.
5. ~~Wii U mergeable data~~ done: `tools/wiiu_convert.py` converts text, maps and actor data on the player's PC, and
   `tools/modman.py add` converts a Wii U mod by itself. Tested in game with a Wii U text mod made for the test
   (see [wiiu-mods](wiiu-mods.md)); no real Wii U mod tried yet.
6. Wii U asset conversion: ~~models, textures, their animations~~ done in `tools/wiiu_model`, proven on round trips
   of the player's own Switch files, not yet on a real Wii U model mod or in game ([wiiu-models](wiiu-models.md));
   next: audio, and the formats `wiiu_convert.py` keeps as Wii U data for now (UI layouts, event flows, effects).
