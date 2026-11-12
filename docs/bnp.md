# BCML packages (.bnp)

`python tools/modman.py add "<file>.bnp"` installs a BCML package as a Switch mod in your library
(`tools/bnp.py` does the work). An extracted package works too: a folder, or a `.zip`, holding `info.json` and
`logs/`.

```
python tools/bnp.py info "D:\Downloads\Some Mod.bnp"
python tools/modman.py add "D:\Downloads\Some Mod.bnp"
python tools/modman.py add "D:\Downloads\Some Mod.bnp" --bnp-no-options --bnp-option "Royal Weapons"
python tools/modman.py deploy
```

`bnp.py info` shows the package's name, version, options (with their folder and which are on by default) and logs,
and reads nothing else.

## Why a .bnp can't be used as it is

A `.bnp` is a 7-Zip archive: `info.json`, the mod's files in the Switch layout (`01007EF00011E000/romfs`, the DLC in
`01007EF00011F001/romfs`), `logs/`, and `options/<folder>/` with the same layout. When BCML makes a package it takes
out of each pack (SARC) every file the mod doesn't change, and keeps some changes only as logs. A pack from a `.bnp`
on its own is missing most of its files: the game would crash on it.

## What `add` does

1. **Extracts** it with 7-Zip (`7z.exe` in Program Files or on PATH). Only on a PC without 7-Zip is Windows' own
   `tar.exe` used, which reads 7-Zip archives: never after 7-Zip failed on or refused an archive, never for a `.rar`
   and never for a packed download (below). It lists the archive first and refuses it, writing nothing, if a name
   would land outside the folder (`..`, an absolute path, a drive letter), is a name Windows treats specially (`CON`,
   `NUL`, a `:` stream, a trailing dot), starts with `-`, is a link (a junction, a RAR "copy link" too), is there
   twice, is listed with no size (a RAR entry marked "size unknown": every size check counts on the listing), or is
   password-protected (7-Zip's word on that is final: no other tool is tried). With `tar.exe`, a name beyond plain
   ASCII is refused too (it lists names in the ANSI code page and can't read them back reliably), as is a line it
   lists that can't be read: install 7-Zip for such a package. It extracts into a new work folder next to the library copy, checks
   that only the listed files came out, clears the read-only setting 7-Zip brings back, and deletes the work folder
   afterwards (`add` says so when it can't). A work folder or library copy left by an add that was cut short
   (`.adding-*`, untouched for an hour) is deleted by the next `add`. The extractor runs at a lower priority. Nothing
   in the package is run: the tool that listed the archive is the only one that reads it, a file is read by the name
   the archive spells it, after `--`, so no name is ever taken for an option. A package that lists more than 20 GB
   (more than the whole game), one file over 4 GB, or over 1000 times its own size (above 1 GB) is refused, and
   extracting needs its size plus 2 GB (or 1% of the drive, if more) free, so the drive never fills up; while the
   extractor runs, it is stopped as soon as the drive has less than half that room left (an archive writing more
   than it lists). A plain mod `.zip` gets the same checks (`modman.extract_zip`, through `bnp.check_room`).
2. **Options:** the ones the package turns on by default. `--bnp-option NAME` (its name or folder, any case; repeat
   it) adds one; for a choose-one group it replaces the group's default. `--bnp-no-options` leaves the defaults out,
   so `--bnp-no-options --bnp-option X` installs only X. A required group with nothing chosen gets its first option,
   or is refused with `--bnp-no-options`. The base files go in first, then each option in the package's order; an
   option's pack that the base also has is merged into it (the option's files win).
3. **Packs** are filled from the game's own copy (`extracted\romfs`, only read): every file the mod left out comes
   back from the game, the mod's own files win, and packs inside packs are filled the same way. This is BCML's pack
   merge for one mod. Each file keeps at least the alignment it had in its pack.
4. **`deepmerge.aamp`** (actor parameter changes: physics, actor links, model lists...) is applied to the game's
   parameter files inside the filled packs, as BCML merges it: the log's lists and objects are added or merged in,
   its values replace the game's. A pack the mod changes only through this log is copied from the game first.
   **`aslist.aamp`** (animation lists, `Actor/ASList/*.baslist`, such as the player's `Player.baslist` in
   `TitleBG.pack`'s `GameROMPlayer` actor pack) is then merged into the game's lists the way BCML does: entries are
   matched by name, not by their key. New animation archives (`AddReses`, by `Anim`) and new sequences (`ASDefines`,
   by `Name`) are added after the game's (`AddRes_N`, `ASDefine_N` numbered from the game's count); a sequence the
   game has gets the log's `Filename`. A blend rule (`CFDefines`, by its `CFPre` `Name`) the game has gets the log's
   excepts added (by value) and its posts merged by `Name` (a known post's values replaced, a new one added); a new
   rule is added. Nothing the game has is removed or moved. A list the game's pack doesn't have is named, not made up
   from the changes alone. A log entry without its name (`Anim`, `Name`, `CFPre`) is named under *Needs attention*
   and left out, never matched by its key (BCML numbers a log's keys among the logged entries, so `ASDefine_0` there
   is not the game's). A list the log doesn't change (BCML logs often repeat the game's entries) is left as it is: a
   pack only such a log touches is not copied into the mod.
5. **Size table (RSTB):** a new one, made from the game's, goes in the mod as
   `System/Resource/ResourceSizeTable.product.srsizetable`. It holds the sizes from every `rstb.json` (base and
   options; the larger one when both list a file) and sizes for the files the install rebuilt (a pack: the game's
   entry plus what it grew, packs that only `deepmerge.aamp` or `aslist.aamp` changes and packs inside packs included;
   a parameter file: the game's entry grown in proportion; an animation list: the game's entry plus what the file
   grew plus what the game allocates for each new entry, per the decomp's `resResourceASList.h`: 0xA0 per animation
   archive, 0x88 per sequence, 0x138 per blend rule, 0x98 per post, 0x28 per except). A size is never set below the
   game's.
   A size of 0 in `rstb.json` is how BCML says it couldn't work one out: that entry is removed, as BCML does. When
   none of this changes the game's table, no table is written: a size table in the overlay makes Savage Wind
   0.4.1-alpha.4 stop its own size fixes (`SAVAGEWIND_NO_HUD_LETTERS`), so the mod doesn't ship one for nothing.
6. **The data logs** are merged next, each kind's base log and its chosen options' logs combined first (BCML's
   rules, read from its source; where ours differ, it says so below), into the mod's own copy of the file when it
   has one, else the game's. The file goes back into its packs (a pack the mod doesn't ship is copied from the game,
   as for `deepmerge.aamp`), and every pack on the way gets a size like the ones above:

   | Log | File | How |
   |---|---|---|
   | `actorinfo.yml` | `Actor/ActorInfo.product.sbyml` (a whole file) | New actors added, changed keys merged; sorted by hash. No size entry (the game's table has none) |
   | `gamedata.yml` | `Bootup.pack` `GameData/gamedata.ssarc` | Flags added, replaced or deleted by name; the game's files kept, only changed ones rewritten |
   | `savedata.yml` | `Bootup.pack` `GameData/savedataformat.ssarc` | Entries added or deleted by hash, sorted; the game's revision kept |
   | `eventinfo.yml`, `effects.yml`, `areadata.yml`, `residents.yml` | `Bootup.pack` (`Event/`, `Ecosystem/`, `Actor/`) | Entries added or replaced (residents: `remove` drops one) |
   | `mainstatic.yml`, `dstatic.yml` | `Bootup.pack` `Map/MainField/Static.smubin`, `Map/CDungeon/Static.smubin` | Start points, korok spots and the like added, changed or removed |
   | `map.yml` | `TitleBG.pack` `Map/MainField/<unit>/<unit>_Static.smubin`, `Map/MainField/<unit>/<unit>_Dynamic.smubin` (a whole file) | Objects and rails added, changed or deleted by `HashId`, sorted. Statics go into `TitleBG.pack` (BCML writes them loose) |
   | `quests.yml` | `TitleBG.pack` `Quest/QuestProduct.sbquestpack` | Quests added after the one they follow, changed or deleted |
   | `drops.json`, `shop.aamp` | the `.bdrop` / `.bshop` in the actor's pack (in `TitleBG.pack` for title actors) | Drop tables as logged (`UNDERRIDE_CONST`: the game's value; an option's table merges into the base's, a later mod's replaces an earlier one's whole); shop items removed, then added or changed |
   | `texts.json` | `Pack/Bootup_XXxx.pack` `Message/Msg_XXxx.product.ssarc`, every language your game has | Texts added or changed by label. A language the mod has no texts for gets its new texts, but keeps its own wording where the mod changed one (BCML copies the English over it) |

   A log that is the same as the game's (or as the mod's own copy) changes nothing and adds no file. `add` prints a
   `logs merged:` line with each log and how many files it changed. A save data delete of a flag that a later game
   data change keeps is dropped, so the flag is still saved. `map.yml` also names an object it adds with a `HashId`
   another unit gets too, or the other half of its section (Static/Dynamic) already has (the game's `HashId`s are
   unique: an object's save flags are named by it), and a changed object your map doesn't have, which is added as new
   (usually made on a map with the DLC). Damaged or hostile files are refused, not followed: a Yaz0 file that says it
   unpacks to more than 256 MB (or more than it could), a log over 64 MB, a parameter file whose lists point at the
   same entries again, a drop table with more items than it holds.
7. **Everything else is named.** BCML 2's logs (`savedata.sarc`, `deepmerge.yml`, `texts_*`: BCML 3 refuses such
   packages too), logs this loader doesn't know, damaged logs, and the parts of a log a merger leaves out (a flag of a
   type the game lacks, a texts language your game doesn't have...) are not merged: the game's own data is used, so
   the parts of the mod that need them may not work. `add` prints each one as `needs attention:` and `deploy`
   repeats it under *Needs attention*. A DLC pack can't be filled when your game has no DLC set up: also listed.

The library copy (`modloader\mods\<id>`) is then a normal Switch mod plus `.bnp\` (never deployed: its name starts
with a dot), which keeps `info.json`, the logs, `rstb.json` (the size changes: only the ones that change the game's
table) and `bnp.json` (what was installed, its packs, the files each data log changed or names, and what wasn't
merged). A damaged `bnp.json` is named under *Needs attention* (add the mod again), never a crash.
`list` shows the package's version. To change the options, `remove` the mod and add it again.

## Several mods

- **Size tables are merged.** When two or more enabled mods change the RSTB, `deploy` makes one table from the
  game's with all their changes (the larger size of each file; a removed entry stays removed) and deploys it in the
  RSTB's place. A BCML package's changes come from its `.bnp\rstb.json`; another mod's (a UKMM output, say) from
  comparing the table it ships with the game's. Both are counted by the table's own key (a file's CRC-32, or its
  name for the few the table lists by name), so a file one mod names and another's table holds is one file, and the
  result doesn't depend on the mods' order. A package whose sizes change nothing is left out. `deploy` says which mods
  it merged; when the game's own table is missing it says the tables can't be merged, and still checks every mod.
- **Packs are merged between mods, file by file** (since 2026-11-12). When two enabled mods ship the same pack
  (any mods: BCML packages, converted Wii U mods, plain Switch mods), or a whole file a package's data logs made
  (such as `ActorInfo`), `deploy` merges them (`bnp.deploy_hook(..., files=True)`, `merge_packages`) and puts the
  merged file in the overlay over every mod's copy (`romfs_mods.add_generated`, owner `generated:merged`; its size
  entry is in the merged table). In load order, the later mod wins a file, never a whole pack. *Notes* say `<pack>: merged from <mods>`; the clash
  is not listed under *Needs attention* any more. Nearly every package with game data, texts or map logs ships
  `Bootup.pack`, `Bootup_XXxx.pack` or `TitleBG.pack`, which is where they met before: Linkle's body (11 files in
  `TitleBG.pack`) now stays next to linkle-dialogue's, in either order. A merge that fails (an error the merge
  doesn't expect) leaves each mod's own copy, the later one winning, with one line under *Needs attention*. A dry
  run merges too (in memory), so it says what the deploy does (`deploy(..., merge=False)` skips it). A pack the game
  lacks (a new actor pack two mods both add) isn't merged: the later mod's copy wins, as for any other file.
- **Merge cache:** the merged files are kept in `modloader\cache\merged-packs\` (`index.json` and one
  `<sha256>.bin` per file, shared between results). A deploy whose merge inputs are the same (each merged file's game
  copy and every mod's copy of it: path, size, time; every package's `.bnp\` folder; the game's size table,
  language packs and `ActorInfo`; these tools' own files) reads the result back, each file checked against its
  hash, instead of merging again (the Linkle set: 20-38 s merged, about 4 s read back); *Notes* say so. The 2 newest
  results are kept, files no kept result names are deleted. A dry run reads the cache but never writes it; a cache
  that can't be written is skipped.
- **A Wii U mod's whole text packs** (`Pack/Bootup_XXxx.pack`, as linkle-dialogue-fix ships them) are made a texts
  log at `add` (`text_packs_to_package`): each language's pack is compared with your game's own, label by label; the
  labels it changes or adds go into `.bnp\logs\texts.json` (system texts, `StaticMsg/ErrorMessage`,
  `StaticMsg/StaffRoll*` and `LayoutMsg/SystemWindow_00`, are left out), and the mod is installed as a BCML package
  from it. The Switch's own message files and labels the Wii U pack lacks stay, and `deploy` merges its texts with
  every other mod's in load order (the later mod wins a label). The linkle-dialogue-fix: 1551 labels in 486 message
  files for each of `USen` and `EUen`, 4 system files left out; its two byte-identical region folders are converted
  once (`wiiu_convert.region_copies`).
- **How the merge works:** `bnp.deploy_hook(..., files=True)` gives those files
  merged as BCML merges mods: the game's copy, then each mod's own files over it in order (only the ones that differ
  from the game's, packs inside packs the same way), leaving out a package's files that its logs made (and the files
  BCML ships changed and logs too, such as event info and quests, when the log can be read); then every package's
  `deepmerge.aamp`, `aslist.aamp` and data logs again, in order, onto that. So a mod that isn't a package keeps its
  own changes to the same files (a text inside `Bootup_USen.pack`, a game data flag, an event) next to the
  packages', in either order. A shop or drop table a package ships already changed (BCML keeps its log too, which
  then changes nothing at `add`) is still replayed: `bnp.json` lists every file a log names. Where a mod that isn't a
  package comes later in the order and changes a file a package's logs change, the package's logs still come last,
  so for an entry both change (the same text, flag, actor...) the package's wins, where BCML (which logs every mod)
  would keep the later mod's: *Needs attention* names each such file. A whole file such as `ActorInfo` starts from
  the last mod's copy that no package's logs made, else the game's. The merged files get size table entries, and a
  size table is made for them even when no mod ships one.
- **Sizes worked out at add:** a BCML or UKMM package this loader installed (its library copy has `.bnp\rstb.json`,
  even an empty one, or `.ukmm\ukmm.json`) is never named as a mod whose files "may need entries" in the size
  table: its entries were worked out when it was added (`romfs_mods.sizes_checked`).

## Packed downloads (.7z, .rar, archives inside)

`tools/modman.py add` also takes a download it can't use as it is: a `.rar`, a `.7z` without a BCML `info.json`
(so not a `.bnp`), or a `.zip` that holds archives (and isn't a BCML or UKMM package itself)
(`bnp.packed_download`). `bnp.unpack_download` unpacks it into `modloader\mods\.adding-<name>-<process>-<random>
.unpack\` (a name of its own, so two adds at once, the launcher and the command line, never share one), which is
removed when the add ends (or by a later add after an hour, if one was cut short):

- **Safety, at every level:** opened with 7-Zip only (Windows' `tar.exe` is never used for a download: a PC
  without 7-Zip is told to install it, or to extract the download itself), with the same checks as a `.bnp`
  (`extract()`: names, links, `-` names, sizes listed, free space), plus: each file must come out the size the
  archive lists; the archives together may list at most `MAX_MEMBERS` files and `MAX_UNPACKED` bytes; an archive
  listing over 1000 times its own size, above 1 GB, or one file over 4 GB, is refused before anything is written; at
  most 3 archives one inside the other (a 4th is refused). Nothing from the download is run.
- **What is a mod in it:** the folder's own files when `mod_inspect` finds a mod in them (archives in it are moved
  out first), and each archive in it: a `.bnp`, a UKMM package or a mod `.zip` as it is (added the usual way), any
  other one opened in turn. Wrapper folders are looked through. A package kept as it is gets the same checks (listed
  by 7-Zip, counted in the budget, the disk-filler and big-file checks), and none is kept from the 3rd level: modman
  opening it would be a 4th.
- **Game version folders:** of two or more folders side by side named for game versions (`1.6.0/`, `1.9.0/`, `For
  Switch v1.6.0`), or whose patches name a known build, only the 1.9.0 one is kept (else the newest, said); the
  others are named as left out. A mod's own version in a folder name (`Armor v1.2.0`) doesn't count.
- **One mod:** added as any other, named after its folder (or the package's own name), with the download as its
  source. **Several:** `add` stops and lists them; add the download again with `--option "<name>"` (or a part of the
  name only one has) for each one wanted. The other `--option`s go to that mod's own options.
- Bare Wii U folders (`Pack/`... in Wii U byte order, no `content/`) are moved into a `content` folder, so they are
  converted as a Wii U mod (`tools/wiiu_convert.py`).

Plain Switch mods (packed or not) get one more step at `add` (`bnp.fill_old_packs`): a `Pack/*.pack` that lacks
files your game's copy has, as a whole copy from another game version does, gets them from your game (the mod's own
files win), and `add` names the files it changes (some may be that version's rather than the mod's). Map files
(`Map/...`) and files the mod has outside the pack are never filled in: a mod leaves those out on purpose so the game
reads its own (Relics of the Past's `TitleBG.pack` has none of the game's 162 map files; its maps are in its DLC
files).

## Wii U packages

A Wii U `.bnp` (`platform: wiiu` in `info.json`, its files in `content/` and `aoc/0010/`) installs as a Switch mod
(2026-11-12; `bnp.install_wiiu`). An extracted one (a folder or `.zip` with `info.json`, and `logs/` or `options/`)
goes the same way.

1. **Put together in Wii U form:** the base's game files, then each chosen option's over them; a pack both have is
   merged (the option's files win), still in Wii U byte order. Files right in `content/` (a readme) and outside it
   (thumbnails, notes) are left out. Composing first and converting once matters: Fierce Deity has its model in one
   option and its textures in another, and the model converter pairs them only inside one mod.
2. **Converted once** by `tools/wiiu_convert.py` with your game files (models, textures, icons, packs, BYML, texts;
   event flows are the same on both consoles). A loose file that stays Wii U data (voice lines, say) is left out, so
   the game's file is used. A file inside a pack that stays Wii U data (Havok, sound, effects) gives way to your
   game's file of the same path (the pack's twin in the game, else the game's actor packs: Havok cloth in a new
   armor's pack is a copy of a vanilla one), else it is taken out of the pack and named.
3. **Logs made the Switch's:** `deepmerge.aamp` and `aslist.aamp` name `content/...` paths and key their changes by
   the CRC-32 of the path: both are rewritten for `01007EF00011E000/romfs/...` (`aoc/0010/` for the DLC's).
   `actorinfo.yml`: a new actor's instance size is a Wii U one, 2-3 times too small for the 64-bit Switch (the heap
   would be too small: crash or no spawn), so it gets the largest size of your game's actors with the same model and
   profile, else its profile's median, never less than 3 times the Wii U size; an actor of the game's never gets less
   than the game's own. `texts.json`: a choice's size field (msyt keeps it as `unknown`) is 2 more on the Wii U (a
   two-way choice: 8, the Switch's 6, as all 1,619 of the game's are), so it is set to the Switch's. `rstb.json` and
   `packs.json` are left out (Wii U sizes). The other logs are values and names, the same on both consoles, and
   merge as for a Switch package.
4. **Files the logs rebuild** (event info, game data, save data, actor info, status effects, area data, residents,
   statics, quests; texts) are taken out of the converted files: the Wii U copy is a whole file of the Wii U game's
   version, and the log is merged into your own Switch file instead.
5. **Size table:** worked out again from the Switch files, never from the Wii U package's: a file the game has grows
   its entry as a rebuilt one does; a new file (and each new file inside a new pack) gets UKMM's estimate (the game's
   files of the same kind nearest in size, see [ukmm](ukmm.md); event flows from a few of the game's event packs);
   models and textures get the model converter's sizes. A file the table lists but the game keeps somewhere else (a
   shared file such as `Actor/AIProgram/X.baiprog` changed in a new actor pack, an actor pack shipped loose that the
   game keeps only in `TitleBG.pack`) grows from the game's copy found there (`bnp.game_copy`); with no copy at all,
   to the estimate for its kind, never below its own bytes plus the game's entry.
6. Then it **installs** like a Switch package (packs filled, logs merged, size table). The library copy keeps the
   converter's report in `.bnp\wiiu-convert-report.txt`, and `bnp.json` says `"converted_from": "wiiu"`.

`add` lists what the conversion did, and under *Needs attention* each option whose models couldn't be converted
(your game's model is used, so that part of the mod is lost or only half there), the loose files left out as Wii U
data (`bnp.dropped_notes`: those that change a game file, whose part of the mod is lost, apart from new ones the game
has none of, which are missing), and the files in packs swapped for the game's (Havok, sound). For linkle-dialogue
that is 138 of its 162 files, all its voice lines.

## Limits

- Wii U packages: what `wiiu_convert.py` can't convert stays out (voice lines and other sounds, Havok without a game
  twin, effects, UI layouts; models it can't read yet). Their texts are merged; a Wii U package's texts in fewer
  languages work as any package's (see above).
- BCML 2 logs (`savedata.sarc`, `deepmerge.yml`, `texts_*`) aren't merged (named, see above).
- None of the data logs has been tried with a real package yet (the one real package here has none of them). In the
  game, a made-up package's texts, map, game data, save data and actor info logs work (2026-11-12, below); the other
  kinds (event info, effects, area data, residents, statics, quests, drops, shops) were checked on the game's own
  files and on made-up logs only.
- DLC: data logs for DLC files (DLC drop tables and shops, DLC maps) aren't merged; when your game has the DLC set
  up, the map logs are named instead of merged.
- Where the rules differ from BCML's, on purpose: game data deletes of several logs are joined (BCML keeps the
  last list) and the game's game data files are kept as they are; the save data revision is the game's; an option's
  logs merge in the package's order (BCML: no fixed order); quests: a change equal to the game's quest is no change
  (compared with the game's own list, also when merging into a mod's copy) and the later one wins; a texts language
  without the mod's own texts keeps its wording (see above); drops, shops, map additions and other entries a log
  can't place are named, not guessed. An `ActorInfo` copy whose `Hashes` don't line up with its actors (a tool
  appended one unsorted) is rebuilt from the actors' names.
- Drop tables: an option's table merging into the base's and a later mod's replacing an earlier one's whole are read
  from BCML's rules as described, not checked against BCML itself.
- Two mods (or options) adding different map objects with the same new `HashId`: the later one is kept and the clash
  is named (BCML keeps the later one too, silently).
- Rebuilt game files are bigger than the game's: big ones (game data, the language archives) are stored Yaz0, not
  compressed again, as compressing in Python is too slow. Each of the 16 language packs is rewritten when a package
  has texts (about 110 MB in the library copy).
- `aslist.aamp` only adds and changes, as BCML's log only records that: an animation list entry a mod removed stays.
- Sizes for rebuilt parameter files are an estimate (grown in proportion to the game's entry), and so are animation
  lists' (from the decomp's struct sizes, not checked in the game). For the data logs' files, the game data, save
  data and language archives and the map units follow the game's own rule exactly (checked on its files); the other
  BYML files use the pack rule (the game's entry plus what the file grew), and the main field and shrine statics,
  drop tables and shops grow in proportion. New models, sounds and other files rely on the sizes BCML wrote in
  `rstb.json`.
- Packs are rebuilt by this tool: same files, but not byte for byte what BCML or Nintendo's tools make.

## Tested

- Wii U packages: made up ones in `tests/test_wiiu_packages.py` (options merged in Wii U form, the conversion, a Wii
  U Havok file swapped for the game's twin or left out, log paths and keys, instance sizes, text choice sizes, the
  size table worked out again); and 4 real ones in a scratch library on 2026-11-12, not in the game yet (Linkle
  armor icons, Linkle dialogue with the diary and the Riju "Linkle 3" options, Fierce Deity, Girly Animation Pack:
  results in [MOD-COMPAT](MOD-COMPAT.md#wii-u-packages-2026-11-12-scratch-library-not-in-the-game-yet)).
- Made-up packages only, in `tests/test_bnp.py`: listing and extracting with 7-Zip and with `tar.exe`, refused names
  (`..`, names starting with `-`, names twice, names beyond ASCII with `tar.exe`) and links (junctions too), reading
  a file by the archive's own name with the tool that listed it, read-only files, options, packs filled (nested
  too), the parameter merge and parameter files that point many headers at the same ones, sizes for packs only
  `deepmerge.aamp` changes, the animation list merge (by name, new entries numbered after the game's, an option's log
  after the base's, a list the game lacks named, entries without a name named, a log that changes nothing leaving the
  game's pack out, sizes for the list's new entries and the packs around it), no table when nothing changes, and
  `modman add` and `deploy` with one and two packages (the merged size table, the same in any order, and without the
  game's table).
- Made-up packages and game files in `tests/test_bnp.py` (`DataLogs`), end to end: one package with all 14 data logs
  (and an option's game data and texts logs, a BCML 2 log and a damaged option log) installed into a made-up game
  that has every file they change: each file merged in its pack (`Bootup.pack`, `TitleBG.pack`, an actor pack inside
  it, a loose actor pack, two language packs) or as a whole file, the packs' other files kept, sizes for the grown
  files and their packs, the three things left out named; logs that change nothing add no file; a merged file
  whose pack nobody has is named; `deploy` naming the data logs a later package's pack replaces; and the merge
  `deploy` doesn't use yet (`files=True`): two packages sharing `Bootup.pack`, `TitleBG.pack` and an actor pack,
  each one's game data, map, quest, shop, event and parameter changes and own files kept, sizes above either's. The
  merge rules themselves: `tests/test_bcml_data.py`, `test_bcml_texts.py`, `test_bcml_maps.py`, and read-only round
  trips of the game's own files (every file these logs change: BYML, game and save data, all 16 languages' texts).
- 2026-11-07, on the game's own files (read only; scripts and output in `out\bcml-logs-check`, never in the repo): a
  made-up package with 12 small made-up logs built from the game's data (a new flag, save entry, event, resident,
  start point, map object, quest, text and drop item; a moved shrine entrance, a changed actor size and shop count)
  installed in 26 seconds: every change read back from its file, `Bootup.pack`'s other 138 files byte-identical to
  the game's, 16 language packs written, sizes above the game's (286 MB in all). Then `deploy_hook(files=True)` with
  that package and the real character mod below, which both ship `TitleBG.pack`: one `TitleBG.pack` in 15 seconds
  with the mod's 11 own files (10 byte-identical, 1 rebuilt with the same contents), its animation list, and the
  made-up map object and quest. No real package with data logs tried yet, nothing in the game.
- 2026-11-07, a real Switch package (a character and armor mod, 293 MB, 3 options) added to a scratch library from
  the user's own download, then `deploy --dry-run` and a deploy into that library: 40 seconds to add; 308 RomFS files,
  132 packs filled with 1846 files from the game, 201 parameter files merged, 382 size table entries;
  `aslist.aamp` (the player's animation list) not merged. Not tried in the game yet.
- 2026-11-07, the same package added again to a scratch folder after the fixes from review: 54 seconds at low
  priority, 392 size table entries (385 change the game's). The 5 armor packs only `deepmerge.aamp` changes
  (`Armor_011/037/074/075/076_Upper`, rebuilt from about 10 KB to 14.2-14.6 KB) now get entries above their rebuilt
  size (14752-15136; the game's are 10272-10624), and are in the packs `deploy` checks. Its 194 parameter files and
  its logs read within the new limit for parameter archives.
- 2026-11-07, the same package added to a new scratch library with the `aslist.aamp` merge (67 seconds): its one
  animation list change is the player's (`Pack/TitleBG.pack//Actor/Pack/GameROMPlayer.sbactorpack//
  Actor/ASList/Player.baslist`). The merged list has 3 animation archives instead of the game's 2 (the mod's own,
  which it ships in `TitleBG.pack`, after the game's two); its 399 sequences and 77 blend rules are the game's (the
  log repeats the game's 77 rules unchanged), and everything else in it reads back as the game's. Size table:
  `Player.baslist` 135704 -> 135872 (the proportional rule then used; the per-entry rule above gives 135928 for this
  merge, worked out, not re-installed); `GameROMPlayer` unchanged at 3083328 (the 40 bytes fit its alignment). Nothing
  is left under *Needs attention* but the usual size table note. Only `TitleBG.pack`, the size table and `.bnp\`
  differ from the earlier install. Not tried in the game yet.
- 2026-11-07, the same package added to a new scratch library with the data log merging in place (it has no data
  logs): all 320 of its files byte-identical to an install made just before the change; only `.bnp\bnp.json`
  differs (it now lists the files each data log changed: none).
- 2026-11-12, after the deploy merge fixes: `DataLogs` in `tests/test_bnp.py` adds a mod that isn't a package next
  to a package (its own English text inside a nested pack, game data flag, actor and event kept with the package's,
  in both orders), two packages each changing a different quest (both kept), and a size table made for the merged
  files alone. The same package added again to the scratch library (`out\bcml-logs-lib`, 70 seconds at low
  priority): all 321 files, `.bnp\` included, byte-identical to the 2026-11-07 copy. The real-file checks in
  `out\bcml-logs-check` again: the made-up package from the game's data installed with every change read back
  (39 seconds), and `deploy_hook(files=True)` with it and the character mod: one `TitleBG.pack` in 15 seconds, 10 of
  the mod's 11 own files byte-identical and the 11th the same inside. Not tried in the game.
- 2026-11-12, in the game (Savage Wind 0.4.1-alpha.4, a scratch library and the test save, backed up and put back;
  files in `out\bcml-ingame-1112`, never in the repo): a made-up `.bnp` with only logs (the title screen's Continue
  and the pause menu's Save in all 16 languages, one barrel in `D-6_Dynamic` next to the save's start, a new game
  data flag and its save entry, the apple's sell price) added with `modman add` next to the character mod, deployed
  and played for 10 minutes: no crash; "Continue BNP" and "Save BNP" shown; the barrel stands where it was placed
  (it isn't in the save's own picture); a save wrote the new flag (the save file 8 bytes longer, the flag's hash in
  it) and loaded back; the game's log has no size or resource errors. The sell price wasn't looked at in a shop.
