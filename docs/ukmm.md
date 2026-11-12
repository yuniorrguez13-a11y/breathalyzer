# UKMM packages

`python tools/modman.py add "<package>.zip"` builds a UKMM package (Switch or cross-platform) into a Switch mod in
your library (`tools/ukmm.py` does the work). A folder holding an unzipped package works too.

```
python tools/ukmm.py info "D:\Downloads\Some Mod.zip"
python tools/modman.py add "D:\Downloads\Some Mod.zip"
python tools/modman.py add "D:\Downloads\Some Mod.zip" --no-options --option "Sheikah Jump"
python tools/modman.py deploy
```

`ukmm.py info` shows the package's name, version, platform, options and what its files are (which ones this loader
can merge), and writes nothing. Its first line after the name, and `add`'s, says how many of the package's resources
are kinds this loader can't merge yet: those parts of the mod are left out. `--option` and `--no-options` are the same
switches as `--bnp-option` and `--bnp-no-options`.

Only partly supported, and not tried in the game yet: `tools/mod_inspect.py` rates these packages `partial`. In the
one real Switch package tried (Sheikah Run), the run and jump animation sequences, which are the mod, aren't merged.
A cross-platform package holds only data UKMM merges (UKMM's packer refuses plain files in one), and most of those
kinds (BYML, texts, game data, actor info, maps) aren't merged here yet.

## What works, what doesn't

- **Built:** every file the package lists. Models, textures, sounds and other plain files as they are; packs
  (actor packs, `TitleBG.pack`...) from your game's copy with the package's files added, removed or changed, nested
  packs too; and these parameter files merged into the game's: actor links (`.bxml`), model lists (`.bmodellist`),
  animation lists (`.baslist`), general parameter lists (`.bgparamlist`) and the other plain parameter files UKMM
  keeps whole (damage, awareness, LOD, UMii, demo, world info, any AAMP it has no own type for).
- **Not merged yet** (named under *Needs attention*; the game's copy is used, and a new file of that kind is left
  out of its pack): physics, animation sequences (`Actor/AS/*.bas`), recipes, shop data, AI programs and schedules,
  drop tables, life conditions, chemistry, bone control, attention clients, ragdoll files, animation info, actor
  info (so new actors get no `ActorInfo` entry), texts (`Bootup_XXxx.pack` is left out), maps, events, quests,
  tips, area data, status effects, cooking data, fonts, UI layouts and other BYML files.
- **Game data and save data flags** (`Bootup.pack//GameData/gamedata.ssarc`, `savedataformat.ssarc`): flags the
  package adds or changes are merged into the game's (game data by `tools/bcml_data.py`; save data for all three
  lists: `game_data.sav`, `caption.sav`, `option.sav`). A flag the package deletes is kept and named: UKMM diffs a
  mod against the game version its maker had, so such a delete is most often that version's difference (the
  Guardians package "deletes" `DisplaySpecialOnce`), and a flag the game reads that is gone is worse than a spare one.
- **Wii U packages** (2026-11-12): their plain files (models, textures, icons, BYML, texts, packs) are converted to
  the Switch's formats first, in memory, by `tools/wiiu_convert.py` with your game files (`X.Tex1` + `X.Tex2` become
  `X.Tex`, renamed in the manifests and pack lists too). A file that stays Wii U data (Havok, sounds, effects) gives
  way to your game's copy of the same path (the pack's twin, else the game's actor packs: Havok cloth in a new armor
  pack is a copy of a vanilla one), else it is left out. Both are under *Needs attention*, by kind: where your
  game's copy is used that part of the mod is lost; where there is none it is missing, so what uses it may fail
  (`ukmm.wiiu_lines`). Parsed resources and pack lists are the same on
  both consoles. Models the converter can't read yet (see [wiiu-models](wiiu-models.md)) are left out and named.
  UKMM stores these in its own data model; reading each kind back is its own piece of work. The merges themselves
  exist now for BCML packages (game data, save data, actor info, events, area data, status effects, residents,
  texts, drops, shops, maps, quests: `tools/bcml_data.py`, `bcml_texts.py`, `bcml_maps.py`); what UKMM packages
  still need is a reader that turns UKMM's own data for each of these kinds into their input. Animation sequences
  also need UKMM's list of animation resource type names, which is game-derived and not in this repo.
- **Refused:** a `meta.yml` with no platform or one UKMM doesn't write. A damaged package (a zstd frame cut short,
  data cut short, a pack alignment that isn't a power of two up to `0x2000`, YAML nested more than 64 deep...): a
  message, never a traceback.
- **Named, the rest installs:** a resource in a form this loader can't read (a kind it doesn't know, or data its
  decoder can't hold): the game's copy of that file is used. (Before 2026-11-12 one such resource ended the whole
  install: the Guardians package stopped at `GameData/savedataformat.sarc`, whose flags are keyed by `{name, hash}`
  maps. CBOR allows a map as a key, Python's dicts don't; such keys are now read as frozen maps.)
- **Named, not refused:** a package made on top of another mod (`masters` in `meta.yml`): its packs and parameter
  files list only what it changes against that mod, and are built on the plain game here, so what it relies on from
  that mod is missing. Install the two together in UKMM (Switch mode) and add its output folder.

## Why a UKMM package can't be used as it is

A UKMM package is a `.zip` with no zip compression: `meta.yml` (name, version, platform, option groups),
`manifest.yml` (the game files it changes, by their real names: `content` and `aoc` lists) and one file per changed
resource at its canonical path (`Actor/Pack/X.sbactorpack` is stored as `Actor/Pack/X.bactorpack`: every `.s` turned
into `.`; DLC files under `Aoc/0010/`). Options sit in `options/<folder>/` with their own `manifest.yml`. Each
resource is zstd-compressed CBOR of UKMM's own data: a file's bytes, a pack's file list (only what changed against
the game's), or a parsed file (for most kinds only what changed). Inside a pack UKMM stores only the files the mod
changes, and leaves out of a new pack the files it shares with the game.

## The zstd dictionary

UKMM compresses each resource with a dictionary that is built into UKMM. `tools/ukmm.py` reads it from `ukmm.exe`
(found on `PATH` or in `Program Files\ukmm\bin`; it is read, never run) and checks it against its SHA-256. Without
UKMM installed, set `UKMM_DICT` to a `ukmm.exe` or to a copy of the dictionary file (`crates/uk-mod/data/zsdic` in
UKMM's source). `python tools/ukmm.py dict` says where it was found. Packages made by UKMM versions from before the
dictionary decode without it.

Python 3.14 and later have zstd built in, and `ukmm.py` uses it. On Python 3.13 it uses `tools/zstdec.py`, a zstd
decoder in pure Python: about 5 MB of UKMM's data a second on a test PC (a 15 MB package of models took 17 seconds;
a 250 MB one takes a few minutes).

## What `add` does

1. **Reads** the package where it is (a `.zip` is never extracted). Every name in a manifest is checked as a `.bnp`'s
   are (no `..`, absolute paths, drive letters or names Windows treats specially); an option folder must be a plain
   folder name. Nothing in the package is run.
2. **Options:** the ones the package turns on by default. `--option NAME` (its name or folder, any case; repeat it)
   adds one; in a choose-one group it replaces the group's default. `--no-options` leaves the defaults out. A
   required group with nothing chosen gets its first option, or is refused with `--no-options`. Options another
   option requires come along; when that would be a second option of a choose-one group (`--option Blue` with an
   option that requires Red), it is refused: pick the one you want. The base package goes first, then each option in
   the package's order, so an option's changes win.
3. **Builds** each listed file as UKMM's deploy does for one mod: plain files from the package; a pack from the
   game's copy, its list of files changed as the package says, each file in it built the same way (a file the
   package doesn't have comes from the game's pack); a parameter file from the game's copy with the package's changes
   merged in. A pack the package doesn't store itself but whose files it changes (the player's actor pack inside
   `TitleBG.pack`, say) is rebuilt too. Files a new pack shares with the game are taken from the game's actor packs.
   A game pack that ends up with nothing of the package's in it (its only changes are kinds not merged, say) isn't
   written: the game's copy is used, and no size table ships for it (a game-only copy would also hide another mod's
   copy of that pack). The files an install writes are capped at 8 GB, and at the disk's free space less 256 MB;
   zstd output is capped as it is decoded.
4. **Compresses** files whose extension starts with `s` (not `.sarc`) as Yaz0, as the game expects: files up to 16 KB
   compressed, bigger ones stored uncompressed inside a valid Yaz0 stream (Yaz0 compression in pure Python runs at
   well under 1 MB a second; such files are about 12% bigger than the data).
5. **Size table (RSTB):** a new one, made from the game's, goes in the mod when the install grows an entry the
   game's table has (a pack's entry grows by what the pack grew, another file's in proportion) or adds new files.
   A new file's entry is estimated from your game's files of the same kind (extension; textures apart from other
   models) nearest to it in size: in its folder for a file at the top, in the game's pack around it (else the game's
   actor packs) for a file in a pack. A pack gets its bytes plus a game pack's overhead; another file the largest of
   the 8 nearest files' estimates (their entry grown in proportion, or their overhead kept when one is more than 4
   times bigger or smaller), plus a quarter. A new file of a kind your game has none of gets no entry and is named
   (it may fail to load: [romfs-mods](romfs-mods.md#reading-the-report)). BCML packages carry their own sizes for
   new files; UKMM sizes them at deploy. A size is never set below the game's, and no table is written when nothing
   changes (see [bnp](bnp.md) for why).
6. **Names** what it couldn't merge: `add` prints it as `needs attention:` and `deploy` repeats it.

The library copy (`modloader\mods\<id>`) is then a normal Switch mod plus `.ukmm\ukmm.json` (never deployed: what was
installed, its packs, its size changes and what wasn't merged). `list` shows the package's version. To change the
options, `remove` the mod and add it again.

## Several mods

As for BCML packages: `deploy` merges the size tables of the mods that change it (a UKMM package's by comparing the
table it ships with the game's). Packs aren't merged between mods: when two mods ship the same pack, the later one's
is used. Merge such mods in UKMM (Switch mode) and add its output folder instead.

## Limits

- The file kinds listed above aren't merged.
- Texts (`Pack/Bootup_XXxx.pack`) are left out.
- A DLC pack can't be filled when your game has no DLC set up (named).
- Sizes for grown and new files are estimates (as for BCML packages); a new file of a kind the game doesn't have
  gets none (named).
- Packs and parameter files are rebuilt by this tool: the same files, but not byte for byte what UKMM writes.
- Not tried in the game yet.
- A package made on top of another mod (`masters`) is built on the plain game (named).
- Wii U packages: as for any Wii U mod, sounds, Havok, effects and UI layouts don't convert (the game's copies are
  used), and new actors need the actor info kind, which isn't merged yet, so a Wii U package that adds armors
  installs but its new armors don't exist in the game yet.
- Game and save data: a flag the package deletes is kept (named); new `revival_*` flags go into the plain lists (as
  with BCML's logs).

## Tested

- Made-up packages only, in `tests/test_ukmm.py`: zstd frames made by Python 3.14's zstd from made-up text (Huffman
  and FSE tables, a dictionary trained on made-up text) and frames made by hand, damaged frames, CBOR and byte
  arrays, `meta.yml` and manifests, options, packs (files added, removed, nested, shared with the game, missing),
  every merged kind, Yaz0, the size table, `modman add` (options from the command line too), `deploy` and `info`.
- Made-up packages in `tests/test_wiiu_packages.py`: maps as map keys, save data (all three lists) and game data
  flags merged, deletes kept and named, one unreadable resource named while the rest installs, a Wii U package's
  BYML converted and its Havok swapped for the game's twin (or left out), `X.Tex1`/`X.Tex2` names.
- 2026-11-12, scratch library (`out\wiiu-bnp-lib`), not in the game: Guardians of the Kingdom (Switch) now installs
  (it stopped at its save data before): 108 files, 107 parameter files merged; its save and game data flags are
  all in your game already (its one "delete" kept), and 5 resources of kinds not merged yet are named (3 drop
  tables, 1 AI program, 1 UI layout). Bokoblin Slayer (Wii U) installs: 82 of 93 plain files convert (164
  textures), 4 Havok files come from the game, but its 7 armor models can't be converted yet and its actor info,
  recipes, physics and texts aren't merged, so its new armors don't exist in the game yet.
- 2026-11-07, the user's own 26 Wii U UKMM packages (read only, in UKMM's own folder): `info` reads each; every one
  of the 4,167 parameter files of the kinds above loads, 453 of them merge with the game's own copy and are written
  back as valid parameter files. The pure-Python decoder gives the same bytes as Python 3.14's zstd for all 16,090
  resources (4.1 GB decoded, with UKMM's dictionary read from the installed `ukmm.exe`).
- 2026-11-07, one real Switch package from GameBanana (Sheikah Run, 397 KB, https://gamebanana.com/mods/699479,
  downloaded for this test) added to a scratch library with its option, then `deploy --dry-run`: 6 seconds; 22 game
  files (20 armor packs, `TitleBG.pack` with the player's actor pack rebuilt, a new model), 22 parameter files merged
  (the armor's general parameters; the player's animation list gets the mod's new animation archive), 19 size table
  entries. Its 3 animation sequences (`.bas`, the run and jump themselves) aren't merged (named), so the mod only
  partly works. Not tried in the game.
- 2026-11-07, after the review fixes: the same package again (7 seconds): 3 of its 16 resources named first as not
  merged; 20 size table entries, one of them estimated for the new `Model/Sheikah.sbfres` (1,298,688 for 635,464
  bytes of model, about 2 times its size). The new-file estimate checked against the game's own size table, each
  file left out in turn and estimated from the rest (up to 400 files of each of 18 kinds: models, textures, actor
  packs, effects and 14 kinds of files in actor packs): packs exact; the others a median 1.25 to 1.33 times the
  game's entry, under it for 2 of 400 models (0.92 of it at worst) and 1 of 400 animation lists (0.99), for none of
  the rest. Not tried in the game.
