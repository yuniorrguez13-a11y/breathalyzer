# Mod manager (modman)

`tools/modman.py` keeps your mods in one library, puts together the ones you turn on, and starts Savage Wind
with them. It never changes anything in the Savage Wind folder, and never changes the files you add mods from
(not even their read-only setting).

## How to use it

1. **Set up the game files** in Savage Wind once (start it and press *Set up game files*).
2. **Make the library:** `python tools/modman.py init`. It finds Savage Wind in `Documents\SavageWind\Savage Wind`;
   pass your folder if it's somewhere else. The library goes next to it, in `Documents\SavageWind\modloader`, so
   removing or reinstalling Savage Wind keeps your mods.
3. **Add mods**, one folder or `.zip` at a time: `python tools/modman.py add "D:\Downloads\Better Sprint.zip"`.
   It says what it found in the mod, or why it can't use it. Lines starting `not used:` are parts of the mod it
   leaves out (say an exlaunch code part, or a patch for another game version); `deploy` repeats them.
   `--name "My name"` picks the name. A mod with a [`mod.toml`](mod-format.md) gets its id, name and version from
   it, and is refused (with the reasons) when the file is wrong or the mod doesn't fit your game or modhost.
   A Wii U mod is converted to a Switch mod as it is added: see [Wii U mods](#wii-u-mods). A BCML package (`.bnp`)
   is installed as it is added: see [BCML packages](#bcml-packages-bnp); so is a UKMM package: see
   [UKMM packages](#ukmm-packages).
4. **Check the order:** `python tools/modman.py list`. It shows each mod's id, version (from its `mod.toml`, else
   `-`), kinds and name, and `(from Wii U)` for a converted Wii U mod. When two mods change the same file, the
   **later** one wins.
   Use `move <id> <position>`, `disable <id>` and `enable <id>` (an id or the number from `list`).
5. **Deploy:** `python tools/modman.py deploy`. Read the *Needs attention* lines. If a mod has code patches, or a
   native mod hooks game functions, this rebuilds the game code, which takes **10-20 minutes** (only the first
   time, or when the patches or the hooks change). `--dry-run` shows what would happen, including whether the last
   rebuild is kept; `--no-rebuild` skips the rebuild (code patches that changed then do nothing, and hooks do
   nothing: the hook mods still load).
6. **Play:** `python tools/modman.py run`. It starts the game, and the cheats once the game is up.
7. After you add, remove, turn on or off, or move a mod, **deploy again**. `run` and `status` warn you if you forgot.
   They also notice when the library was moved or the DLC was set up since; `run` refuses to start a deploy whose
   files are gone.

`python tools/modman.py status` shows what is deployed; `python tools/modman.py doctor` checks your setup.
`python tools/modman.py remove <id>` deletes a mod's copy from the library (never the file you added it from).

## Window

`python tools/modman_gui.py` opens a window for the same library: big text, dark on light, and every button works
with the keyboard. `python tools/modman_gui.py --size 24 --dark` makes the text bigger (10-40, default 18) and
light on black. `--library <folder>` picks another library, as for modman; `--port <number>` is the cheats' port
for **Play** (as `run --port`). `pythonw tools/modman_gui.py` opens it without a console window; **Play** still
works, as `savagewind.exe` is a Windows program, not a console one.

- The top lines show the latest result. **Details** shows everything the command printed, also what the cheats
  printed (why they stopped, say).
- Each button runs the modman command: **Add mod** (`add`: a folder, or a `.zip` or other file), **Turn on/off**
  (`enable`/`disable`), **Move up/down** (`move`), **Remove** (`remove`: it asks first, and deletes only the
  library copy), **Deploy** (`deploy`), **Play** (`run`), **Check** (`doctor`).
- When modman doesn't know where Savage Wind is, **Deploy** and **Play** (and **Add mod** for a new library) ask for
  the Savage Wind folder first (`init`).
- **Deploy** runs in the background. A rebuild still takes 10-20 minutes; the window shows the time and the last
  step, and the other buttons wait (Tab skips them). While a game started with **Play** runs with cheats,
  **Deploy** and **Play** wait too.
- Bigger text makes the window bigger, up to the screen. The text gets only as big as still leaves room for every
  button and 3 mods in the list; the window says so when it stops there. **Details** opens only when it fits too.
- Keys: Tab moves between the list and the buttons, the arrow keys move in the list and between the buttons, Space
  or Enter presses a button. In the list, Space turns the mod on or off, Ctrl+Up/Down moves it, Delete removes it.
  Ctrl + and Ctrl - change the text size (Ctrl 0: the start size), F5 reads the list again, Esc cancels a question.
- Closing the window stops a running add, remove, deploy or check (it asks first). A game started with **Play**
  keeps running.

## What each kind of mod needs

| Kind | Looks like | How it is used | What you need |
|---|---|---|---|
| `romfs` | `romfs/...`, `01007EF00011E000/romfs/...`, or folders like `Actor/`, `Pack/` | Savage Wind reads them instead of the game's files (`SAVAGEWIND_ROMFS_OVERLAY`) | Nothing. Files are not merged: see below |
| `dlc-romfs` | `01007EF00011F001/romfs/...` | Same, for the DLC | The DLC set up in Savage Wind |
| `exefs-patch` | `.ips` (named after the build id) or `.pchtxt` (`@nsobid-` line) for your game | Applied to a copy of the game's code files; the game starts from that copy | Data patches: nothing. Code patches: the 10-20 minute rebuild that `deploy` runs |
| `cheats` | `cheats/cd57b23fa4bbad65.txt` (Atmosphere format, BotW 1.9.0; `Cheats/` or `.TXT` in any case too) | Merged into one file, one `// group: <mod name>` section per mod, each with its own master code; `run` starts `tools/cheats.py` on the game's control port (7777) | Only cheats that change values work; code-patch cheats do nothing |
| `native` | A 64-bit `.dll` that exports `swmod_init` (with a `mod.toml` that has `[code] windows`: only the one it names; another DLL with `swmod_init` is left out, as modhost would load it as a second mod) | Loaded by modhost when the game starts. The mod's other 64-bit DLLs (helpers it needs) go next to it | `python tools/build_native.py build` once. Hooks (`[code] hooks` in `mod.toml`): the 10-20 minute rebuild that `deploy` runs |

A mod can have several kinds (say RomFS files plus cheats). A mod's `toggles.txt` next to its cheat file decides
which of its cheats run; without one, all of them run.

## mod.toml, hooks and the examples

A `mod.toml` ([format](mod-format.md)) gives a mod an id, a version, the game versions and mod API it needs, its
DLL, the functions it hooks, its RomFS folders and the mods it needs. Mods without one work as before. It goes at the
mod's root, or in the one folder that holds the mod (a readme next to that folder is fine); `add` refuses a mod with
a `mod.toml` anywhere else, or with several, rather than add it without the checks.

- **Hooks.** `deploy` puts the hooks of all enabled native mods into one list, `deploy\native\hooks.txt`, and
  rebuilds the game code with hook slots for them, even when no mod has code patches. It says which mod asked for
  which hook. The game then starts through modhost with `SWMOD_REAL_GAME_LIB` set to that rebuilt library
  (`deploy\swgame.dll`). The rebuild is kept while the game code files and the hooks stay the same.
- **Refused deploys.** `deploy` refuses (and keeps your last deploy) when an enabled mod's `mod.toml` no longer fits:
  another game version, a mod API newer than modhost's, a hook outside the game code, a missing DLL, or a
  dependency that isn't in the library, is off, or has the wrong version (dependencies are found by the id in each
  library mod's `mod.toml`, also for mods that are off). The message names the mod and what to do.
- **The examples** in `native/examples` have a `mod.toml`. Build them, then add them:

  ```
  python tools/build_native.py build
  python tools/modman.py add native/examples/hello
  python tools/modman.py add native/examples/stamina_hook
  ```

  Their DLLs come from `build\native`, where the build puts them (only for folders inside `native/examples`; other
  mods carry their own DLL). After you rebuild an example, `remove` it and add it again.

**Can't be used:** mods for another game version (they need porting), Cemu code patches, shader packs and settings
presets, Cemu graphic packs for another game, Wii U mods with only models, textures, audio, layouts, effects or event
flows (not converted yet), UKMM packages and Wii U BCML packages (install them
in UKMM and add its output folder), exlaunch/Skyline code mods, and ExeFS patches that write outside the game's code
files. `add` refuses a mod with nothing usable and says why. When only a part can't be used (a RomFS mod with an
exlaunch part, say), the rest is added and the part is listed as `not used`: the rest may not work without it.

## Wii U mods

`add` converts a Wii U mod to a Switch mod on your PC, with [`tools/wiiu_convert.py`](wiiu-mods.md). A Wii U mod is
a folder or `.zip` with `content/` (game files) and/or `aoc/0010/` (DLC files), also a Cemu graphic pack with game
files, or its `content` folder on its own. The library keeps only the Switch copy.

- `add` prints up to 4 lines: how many game files were converted, how many are left as Wii U data, what was left
  out, and where the report is.
- **Converted:** texts, maps, actor data and the packs that hold them. **Left as Wii U data** (kept unchanged):
  models, textures, audio, Havok physics, layouts, effects, event flows. The game can't read those: it may crash or
  look wrong where the mod uses them. `deploy` says so under *Needs attention*.
- **Left out:** the mod's RSTB (its sizes are Wii U sizes) and files that aren't game data (readme, `rules.txt`).
- The full report is `wiiu-convert-report.txt` in the mod's library folder (`modloader\mods\<id>`).
- `list` shows `(from Wii U)` after the name, with the number of files not converted. `status` names the converted
  mods that are on.
- A mod where nothing could be converted (only models, say) is refused, and nothing is added.
- A folder you converted yourself with `wiiu_convert.py convert` counts as a converted Wii U mod: `add` reads the
  `wiiu-convert-report.txt` in it, so `list` and `deploy` warn the same way. Adding the Wii U mod itself is simpler.
- A Cemu graphic pack made for another game (its `rules.txt` has no BotW title id) is refused.
- A password-protected `.zip`, or one packed in a way Python can't read, is refused: extract it with 7-Zip, then add
  the folder.
- A big mod takes a while: packed files are compressed again (about 1.5 seconds per MB on a test PC).
- `python tools/modman.py add "<mod folder or .zip>" --no-convert` refuses a Wii U mod instead, as before.
- Several Wii U mods that change the same file aren't merged: the later one wins, as for Switch mods.
- Tested in game with a Wii U text mod made for the test (see [wiiu-mods](wiiu-mods.md)); no real Wii U mod tried yet.

## BCML packages (.bnp)

`add` installs a BCML package: a `.bnp`, or one extracted with its `logs/` (a folder or `.zip`). The details are in
[bnp](bnp.md).

- `python tools/bnp.py info "<file>.bnp"` lists its options. `add` installs the ones it turns on by default;
  `--bnp-option NAME` adds one (repeat it), `--bnp-no-options` leaves the defaults out.
- It needs 7-Zip, or Windows' own `tar.exe` (there on Windows 10 and 11), to open a `.bnp`. Names that would land
  outside the mod folder, names starting with `-`, links and password-protected packages are refused before anything
  is written. `tar.exe` can't read names beyond plain ASCII reliably: such a package asks for 7-Zip.
- BCML packages hold only the files a mod changes inside each pack: `add` fills the packs back from your game's
  files, applies the actor parameter changes (`deepmerge.aamp`) and makes a size table (RSTB) for the mod when its
  sizes change the game's.
- Logs it can't merge (animation lists, game data, texts, maps...) are printed as `needs attention:` and repeated by
  `deploy`: the game's own data is used for those parts.
- `list` shows the package's version. To change the options, `remove` it and add it again.

## UKMM packages

`add` builds a UKMM package (a Switch or cross-platform `.zip` with `meta.yml`, or a folder holding one's files) from
your game's files. The details are in [ukmm](ukmm.md).

- `python tools/ukmm.py info "<file>.zip"` lists its options and what can be merged. `add` installs the default
  options; `--option NAME` adds one (repeat it), `--no-options` leaves the defaults out.
- It needs UKMM's zstd dictionary, which it reads from an installed `ukmm.exe` (or set `UKMM_DICT`).
- Packs, plain files and the common actor parameter files are built; other kinds (physics, animation sequences,
  texts, actor info...) are left out: `add` says first how many of the package's resources that is, prints them as
  `needs attention:`, and `deploy` repeats them. So a package often only partly works, and none has been tried in
  the game yet. A Wii U package is refused.

## Good to know

- **No merging,** except the size table. When two mods ship the same file (often `Pack/Bootup.pack`), only the
  later one's is used, and `deploy` lists the clash. Mods that add files or make them bigger may need a matching RSTB
  (the game's file-size table). When two or more mods change the RSTB (BCML packages, or mods that ship one),
  `deploy` merges their changes into one table (the larger size wins). Other files aren't merged: merge such mods
  with UKMM (Switch mode) first and add UKMM's output folder as one mod.
- **Two ExeFS patches that change the same bytes** stop the deploy: turn one off.
- **Cheats from several mods** are all kept, each mod's in its own [group](cheats.md#groups) with its own master
  code. `tools/cheats.py` starts each group from fresh registers, so a mod's cheats work as they would in its own
  file. A mod whose master code can't run (a bad word or an unknown opcode) is left out, and *Needs attention* says
  so. A cheat is kept once when two mods have the same name, the same code and the same master code; otherwise the
  later one is renamed `<name> (<mod name>)`.
- **Disk space.** The library keeps its own copy of every file under 1 MiB, every `.dll`, `.exe` and `.pdb`, and
  `mod.toml`, so rebuilding a mod's DLL or editing its `mod.toml` in the folder you added it from doesn't change the
  library (`remove` the mod and add it again to take the new one). Data files of 1 MiB or more (RomFS assets) from a
  mod folder on the library's drive are hard links when the drive has them (no extra space): they are still the same
  file as the original, so don't edit them in place, neither in the library nor in the mod's folder. Read-only
  files, files from another drive and every file on a drive without hard links (FAT32, exFAT) are copied. A `.zip`
  is extracted. A folder named `romfs` that holds another `romfs` folder (as *Extract to romfs\\* makes) is fine.
- **The deploy folder** (`modloader\deploy`) belongs to modman. It deletes only what it wrote there, and refuses to
  deploy when it finds files it didn't write in the folders it writes to (`overlay`, `root`, `native`, `cheats`,
  `vanilla-lib`, `.work`). Other files in `deploy` are left alone. `deploy\root\romfs` is a link to the game's own
  files; modman never deletes through it.
- **A refused deploy keeps the last one.** Every check runs before anything is deleted, so a mod that can't be
  deployed leaves your last working deploy in place. One exception: when the game code rebuild itself fails, the
  last deploy is gone; `deploy --no-rebuild` then gives you everything except the code patches and the hooks.
- **`run`** refuses while Savage Wind is already running. It clears mod settings left in your window from earlier
  manual setups and sets only what the deploy needs. `--no-cheats` starts without the cheats; `--port 8000` picks
  another control port (1-65535). If the cheats stop right away, `run` says so. The control port lets any program on
  this PC change the game's memory while it is on.
- **Saves** are the same as when you start Savage Wind normally.
- **Another library folder:** add `--library <folder>` to every command, or set `SWMOD_LIBRARY`.

## What is tested

In the real game:

- 2026-11-06, each piece `modman` uses on its own: the RomFS overlay, a data patch started from a patched copy of
  the code files, a rebuilt game code library, modhost, cheats over the control port, and a library rebuilt with
  `sw_rebuild --hooks` loaded through modhost (`SWMOD_REAL_GAME_LIB`) with the stamina_hook example.
- 2026-11-06: `deploy` and `run` with RomFS files, a data patch, cheats and a native mod together.
- 2026-11-07: hello, stamina_hook and ghost added through their `mod.toml`. `deploy` made the combined hooks list
  and rebuilt the game code with `--hooks` (9 seconds, as sw_rebuild had the objects cached). `run`: all three mods
  loaded, `hook_slots` 1, `hooked` 1, and stamina stayed at 1000 while sprinting.

Tested with made-up files only (`tests/test_modman.py`, the rebuild itself replaced): the dependency checks,
keeping the last rebuild on a later deploy, `--no-rebuild`, cheats from several mods (one group per mod), and Wii U
mods converted by `add`. BCML packages: made-up ones in `tests/test_bnp.py`, and one real package added and
deployed into a scratch library (not in the game yet; see [bnp](bnp.md#tested)).
Helper DLLs next to a native mod follow modhost's documented DLL search (the mod's own folder first); that hasn't
been tried in the game either.
