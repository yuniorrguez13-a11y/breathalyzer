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

1. **Extracts** it with 7-Zip (`7z.exe` in Program Files or on PATH), else with Windows' own `tar.exe`, which reads
   7-Zip archives. It lists the archive first and refuses it, writing nothing, if a name would land outside the
   folder (`..`, an absolute path, a drive letter), is a name Windows treats specially (`CON`, `NUL`, a `:` stream, a
   trailing dot), starts with `-`, is a link (a junction too), is there twice, or is password-protected. With
   `tar.exe`, a name beyond plain ASCII is refused too (it lists names in the ANSI code page and can't read them back
   reliably): install 7-Zip for such a package. It extracts into a new work folder next to the library copy, checks
   that only the listed files came out, clears the read-only setting 7-Zip brings back, and deletes the work folder
   afterwards (`add` says so when it can't). A work folder or library copy left by an add that was cut short
   (`.adding-*`, untouched for an hour) is deleted by the next `add`. The extractor runs at a lower priority. Nothing
   in the package is run: the tool that listed the archive is the only one that reads it, a file is read by the name
   the archive spells it, after `--`, so no name is ever taken for an option.
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
6. **Everything else is named.** `gamedata.yml`, `savedata.yml`, texts, map changes, `actorinfo.yml` and any log
   this loader doesn't know are not merged: the game's own data is used, so the parts of the mod that need them may
   not work. `add` prints each one as `needs attention:` and `deploy` repeats it under
   *Needs attention*. A DLC pack can't be filled when your game has no DLC set up: also listed.

The library copy (`modloader\mods\<id>`) is then a normal Switch mod plus `.bnp\` (never deployed: its name starts
with a dot), which keeps `info.json`, the logs, `rstb.json` (the size changes: only the ones that change the game's
table) and `bnp.json` (what was installed, its packs, and what wasn't merged). `list` shows the package's version.
To change the options, `remove` the mod and add it again.

## Several mods

- **Size tables are merged.** When two or more enabled mods change the RSTB, `deploy` makes one table from the
  game's with all their changes (the larger size of each file; a removed entry stays removed) and deploys it in the
  RSTB's place. A BCML package's changes come from its `.bnp\rstb.json`; another mod's (a UKMM output, say) from
  comparing the table it ships with the game's. Both are counted by the table's own key (a file's CRC-32, or its
  name for the few the table lists by name), so a file one mod names and another's table holds is one file, and the
  result doesn't depend on the mods' order. A package whose sizes change nothing is left out. `deploy` says which mods
  it merged; when the game's own table is missing it says the tables can't be merged, and still checks every mod.
- **Packs are not merged between mods.** When a later mod ships a pack a BCML package also has, the later one wins
  as for any file, and *Needs attention* says that package's changes in it are lost. Merge such mods in UKMM
  (Switch mode) and add its output instead.

## Limits

- Wii U packages are refused (install them in UKMM, Switch mode, and add its output).
- Game data, save data, texts, maps, actor info, drops, shops and other logs aren't merged (named, see above). A mod
  that relies on them may only partly work.
- `aslist.aamp` only adds and changes, as BCML's log only records that: an animation list entry a mod removed stays.
- Sizes for rebuilt parameter files are an estimate (grown in proportion to the game's entry), and so are animation
  lists' (from the decomp's struct sizes, not checked in the game). New models, sounds and other files rely on the
  sizes BCML wrote in `rstb.json`.
- Packs are rebuilt by this tool: same files, but not byte for byte what BCML or Nintendo's tools make.

## Tested

- Made-up packages only, in `tests/test_bnp.py`: listing and extracting with 7-Zip and with `tar.exe`, refused names
  (`..`, names starting with `-`, names twice, names beyond ASCII with `tar.exe`) and links (junctions too), reading
  a file by the archive's own name with the tool that listed it, read-only files, options, packs filled (nested
  too), the parameter merge and parameter files that point many headers at the same ones, sizes for packs only
  `deepmerge.aamp` changes, the animation list merge (by name, new entries numbered after the game's, an option's log
  after the base's, a list the game lacks named, entries without a name named, a log that changes nothing leaving the
  game's pack out, sizes for the list's new entries and the packs around it), no table when nothing changes, and
  `modman add` and `deploy` with one and two packages (the merged size table, the same in any order, and without the
  game's table).
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
