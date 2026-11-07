# RomFS mods with Savage Wind

`tools/romfs_mods.py` puts your Switch RomFS mods into one folder, and Savage Wind reads that folder
in place of the game's own files when you start it with `SAVAGEWIND_ROMFS_OVERLAY=<folder>`.
This works with the released 0.4.1-alpha.4: no rebuild needed.

## Quick start

```powershell
# 1. Fill the overlay folder. Mods are listed lowest priority first: later mods win.
python tools\romfs_mods.py deploy --out "D:\SavageWind\mods\overlay" `
    --game-romfs "D:\SavageWind\Savage Wind\extracted" `
    "D:\mods\Better Sprint" "D:\mods\Linkle Outfit.zip"

# 2. Start the game with the variables it prints (close the game before you deploy again).
python tools\romfs_mods.py env --out "D:\SavageWind\mods\overlay" --format ps | Invoke-Expression
& "D:\SavageWind\Savage Wind\savagewind.exe"
```

`env` prints `NAME=value` lines by default, `--format ps` for PowerShell and `--format cmd` for a
`.cmd` file (write that file from cmd.exe: `python tools\romfs_mods.py env --out <folder> --format cmd > mods.cmd`;
PowerShell's `>` writes a format cmd.exe can't run). The `ps` and `cmd` lines also clear
`SAVAGEWIND_NO_HUD_LETTERS` when the mods don't need it, so a window you used before doesn't keep it.
Paths with any letters work in both. Starting `Savage Wind Configuration.exe` from the same window works
too: it passes the variables on.

To turn mods off in that window:

```powershell
Remove-Item Env:SAVAGEWIND_ROMFS_OVERLAY, Env:SAVAGEWIND_NO_HUD_LETTERS -ErrorAction SilentlyContinue
```

In cmd.exe: `set "SAVAGEWIND_ROMFS_OVERLAY="` and `set "SAVAGEWIND_NO_HUD_LETTERS="`.

`deploy` options:

| Option | What it does |
|---|---|
| `--game-romfs <folder>` | The game's `extracted\romfs` (or `extracted`). Checks every path against the game. Recommended. |
| `--dry-run` | Shows what would happen. Writes nothing. |
| `--copy` | Copies files instead of hard-linking them. |
| `--json` | Prints the full report as JSON (plain ASCII: other letters are `\u` escapes). |

## What a mod can look like

Each mod is a folder or a `.zip`. One wrapper folder around everything is fine.

| Layout | Goes to |
|---|---|
| `romfs/...` | the overlay root |
| `01007EF00011E000/romfs/...` (BCML and UKMM output), or `01007EF00011E000/<mod>/romfs/...` (emulator mod folders) | the overlay root |
| `atmosphere/contents/01007EF00011E000/romfs/...` | the overlay root |
| `01007EF00011F001/romfs/...` (DLC) | `aoc\0010\` in the overlay |
| bare RomFS folders: `Actor/`, `Pack/`, `System/`, `UI/`, `Map/`... | the overlay root |
| bare `aoc/0010/...` next to bare RomFS folders (an overlay folder) | `aoc\0010\` in the overlay |

A mod must hold one RomFS tree (its base-game and DLC parts count as one). A download with choices,
such as `30fps/romfs` and `60fps/romfs`, or an emulator folder holding several mods, is refused:
pass the folder you want. When the same file is in two layouts of one mod (`romfs/` and
`atmosphere/contents/.../romfs/`), it's used once if both copies are the same, and refused if not.

Not deployed: `exefs/` patches (use `tools/exefs_patch.py`), cheats, code mods, Wii U mods (a
`content/` folder, a Cemu graphic pack with `rules.txt`, or only an `aoc/0010/` folder), and packed
UKMM or BCML packages (`.bnp`, or a folder with `logs/`). Convert a Wii U mod on your PC first, then
pass the new folder: `python tools/wiiu_convert.py convert "<mod>" --out "<new folder>"`
([wiiu-mods](wiiu-mods.md)). With `modman`, add the Wii U mod itself instead: `modman add` converts
it and keeps its warnings. A Wii U mod with only models, textures, audio, layouts, effects or event
flows can't be converted yet, and a Cemu graphic pack made for another game (no BotW title id in its
`rules.txt`) can't be used: both tools refuse them. For a BCML package, use `modman add`, which installs it
([bnp](bnp.md)). For a UKMM package, install it in UKMM (Switch mode), deploy there, and pass UKMM's output folder to
this tool. Switch DLC files go in
`01007EF00011F001/romfs/`.

A `.zip` packed with Deflate64, PPMd or Zstandard (Windows does this for big files) is refused before
anything is written: extract it with 7-Zip and pass the folder.

## What it doesn't do: merging

This tool does not merge files. When two mods ship the same file (often `Pack/Bootup.pack` or
`Actor/ActorInfo.product.sbyml`), the later mod's copy is used and the report lists the conflict.
It also does not fix the RSTB (`System/Resource/ResourceSizeTable.product.srsizetable`), the table of
file sizes the game checks. (`modman deploy` does one thing more: when two or more mods change the RSTB,
it merges their changes into one table, the larger size winning, and deploys that one. See [bnp](bnp.md).)

For several mods that touch the same files, or any mod that adds or grows files, use
[UKMM](https://github.com/NiceneNerd/ukmm) in Switch mode: install the mods there, deploy, and give
this tool UKMM's output folder as the only mod. UKMM merges packs and texts and writes a matching RSTB.

## Reading the report

The first line is the result. After it come the files each mod gave, the conflicts, and warnings:

- **Ships an RSTB → start with `SAVAGEWIND_NO_HUD_LETTERS=1`.** In 0.4.1-alpha.4, when the overlay has
  an RSTB, Savage Wind stops making its own RSTB changes (issue #505, fixed on the test branch after
  that release). Its HUD button-letter change would then make `Layout/Common.blarc` too big, so it must
  be turned off. `env` adds the variable for you. Also keep the voice language the same as the text
  language: grown voice files lose their RSTB fix too.
- **No mod ships an RSTB.** Replaced files that are bigger than the game's, and new files, may fail to
  load. Merge with UKMM first.
- **Folders the game's RomFS doesn't have.** Savage Wind only adds new files to folders the game
  already has. Files elsewhere are skipped (the log says `directory not in the game's RomFS, skipped`).
- **DLC files but no DLC set up.** Savage Wind only reads `aoc\0010\` when the game has an `aocN`
  folder next to `extracted\romfs`.
- **`System/AocVersion.txt` or `Pack/AocMainField.pack` skipped.** These are DLC files. In the game's
  place they would turn on Savage Wind's fix for a RomFS with the DLC merged in, so the tool drops them.

## Good to know

- **Files are served exactly as they are.** Savage Wind doesn't compress or decompress anything, so
  mods must hold final Switch files (Yaz0 `.s*` files, little-endian data), which Switch mods do.
- **Hard links.** When a mod folder and the overlay are on the same drive, the overlay holds hard links:
  no extra space used. Don't edit files inside the overlay, because that also edits the mod. Files from
  a `.zip`, from another drive, or on a drive without hard links (FAT32, exFAT) are copied, after a
  free-space check. Extract big `.zip` mods to the overlay's drive to save space.
- **The tool owns the overlay folder.** It writes `.mod-deploy.json` there (the game ignores names
  starting with a dot), with the list of files it put there. It only ever deletes those files. It
  refuses a folder that holds anything else (and names it), a folder that isn't empty and has no such
  file, and a folder that overlaps a mod or the game. Keep your own files and mods outside it.
- **A redeploy keeps the old overlay until the new one is ready.** It builds the new one next to it
  (`<folder>~new`), then swaps them (the old one goes to `<folder>~old` and is deleted). If anything
  fails, the old overlay stays as it was. Close Savage Wind first: Windows can't swap a folder while
  the game has files in it open. With too little free space for both, the old overlay's files are
  deleted first instead.
- **Folder name spelling.** With `--game-romfs`, folder and file names are spelled as the game spells
  them (Savage Wind matches new files' folders case-sensitively).
- **DLC movies** are also placed at `Movie\...` in the overlay, where Savage Wind's movie player looks.
- **Check it worked** in `savagewind.log` next to the exe: `[fs] overlay: N romfs files replaced` (N also
  counts 3 or 4 of Savage Wind's own changes) and `[fs] overlay: K new romfs files`.
- **Turn mods off** by starting the game without `SAVAGEWIND_ROMFS_OVERLAY` (see the commands above).
