# Rebuilding the game code (sw_rebuild)

`tools/sw_rebuild.py` rebuilds Savage Wind's game code library (`swgame.dll`) from the command line. It does
the same steps as pressing **Set up game files** in Savage Wind 0.4.1-alpha.4, using the install's own
`swgen\swgen.exe` and `zig\zig.exe`. Use it after patching `exefs/main` (for example with `tools/exefs_patch.py`),
because code changes only take effect once the library is rebuilt.

Close Savage Wind first. A full build took 19 minutes with `-j 6` on a 12-thread laptop and produced the same
size of library as the GUI. The next time only `main` changes, the build reuses the cached `sdk` and `subsdk0`
objects. The cache uses about 330 MB for one build of all three modules; each extra cached `main` adds about
240 MB (up to `--cache-keep`, default 2, so about 565 MB).

## Apply a code mod

```
python tools/exefs_patch.py apply "<SW>\extracted\exefs" out\patched <mod patches>
python tools/sw_rebuild.py install-patched-main out\patched\main "<SW>"
python tools/sw_rebuild.py build "<SW>" --install
```

`<SW>` is your Savage Wind folder. `build` and `status` can leave it out: they then use `%SWMOD_INSTALL%` when it is
set, else `%USERPROFILE%\Documents\SavageWind\Savage Wind`. When that folder doesn't exist they stop with
`ERROR no Savage Wind folder at <path>`. `install-patched-main` and `restore` always need it.

- `install-patched-main` copies the vanilla `main` to `<SW>\mods\.vanilla\exefs\main`. It then writes the
  patched one next to it and renames it over `extracted\exefs\main`. The old file is never opened for writing, so
  if it is a hard link to your own dump, the dump stays unchanged. This also works when `extracted\exefs\main` is
  read-only: the dump keeps its bytes and its read-only setting. (On Windows older than 10 1809, or a drive that
  isn't NTFS, a read-only hard link can't be replaced that way; the command then changes nothing and says to make
  the file writable. The same goes for `restore`.)
- `build --install` copies the vanilla library to `extracted\swgame.dll.vanilla` (and `.vanilla.id`) the first
  time. It then renames the new library into place and writes `swgame.dll.id`.
- Only a patched `main` can be installed. If your `--exefs` folder also has a changed `sdk` or `subsdk0`,
  `build --install` refuses, because the game would run the new code on the old data.

## Undo

```
python tools/sw_rebuild.py restore "<SW>"                 # vanilla main and library
python tools/sw_rebuild.py restore "<SW>" --only library   # just the library
python tools/sw_rebuild.py status "<SW>"                  # what is installed now
```

`status` starts with a `folder   <path>` line naming the Savage Wind folder it looked at.

Savage Wind's "Remove game files" and a new setup leave the backups behind, so a backup can belong to an older
Savage Wind or game version. `restore` leaves such a backup alone and says so, and it never puts files back after
the game files were removed. The next `build --install` replaces an old library backup with the library the new
setup made. If a patched `main` is installed at that point, it refuses instead: run `restore --only main`, set up
the game files again, then build.

## Build without replacing anything

```
python tools/sw_rebuild.py build                       # writes out\rebuild\swgame.dll (+ .id)
python tools/sw_rebuild.py build --exefs out\patched   # from another exefs folder
```

To try a library from `out\rebuild`, set `SAVAGEWIND_GAME_LIB` to its path before starting `savagewind.exe`.
The game reads data from `extracted\exefs`, so its modules must be the ones the library was built from
(`install-patched-main` for `main`). Otherwise code and data won't match; the tool prints a note naming the
modules that differ.

## Options

| Option | What it does |
|---|---|
| `-j N` | compilers at once (default: CPU count; use fewer to keep the PC usable) |
| `--out PATH` | output `.dll` or folder (never the game's own library, however it is spelled: use `--install`; and not inside the work or cache folder) |
| `--no-cache` | don't reuse or store compiled modules |
| `--cache-keep N` | builds kept per module in `out\rebuild\cache`: the N most recently used (default 2). To go back to vanilla, use `restore`, which needs no build. |
| `--work-root DIR` | where the `work` and `cache` folders go (default `out\rebuild`). A `work` or `cache` folder there that sw_rebuild didn't make is refused, never emptied. They hold generated game code: keep them out of the repo. If they do end up in it, git ignores folders named `work` and `cache`, and `tools/check_clean.py` blocks every file in a folder that has a `.sw_rebuild-work` or `.sw_rebuild-cache` marker. |
| `--hooks FILE` | add hook slots for the game functions listed in FILE, so native mods can hook them (see [hooks](hooks.md)); can be repeated |
| `--low-priority` | run the builders at below-normal priority |
| `--keep-work` | keep the generated C in `out\rebuild\work` |
| `--dry-run` | print the exact commands and stop |
| `--id`, `--target` | override the GAMELIB_ID or CPU target (normally read from `extracted\swgame.dll.id`) |

## How it matches the release

- **Commands.** `swgen layout`, then `swgen <module> <nso> layout.txt <dir>` for each module (plus `--hle gamelib-src\hle_symbols.txt` for every module except main).
  Each file is compiled with `zig cc <the exe's GAMELIB_CFLAGS, -march as -mcpu> <GAMELIB_ZIGFLAGS> -Igamelib-src -DGAMELIB_ID="<id>"`.
  The link is `zig lld-link @zig\rt\link.rsp -LIBPATH:zig\rt -OUT:... @objs.rsp savagewind.dll.a`.
  The flags are read from `savagewind.exe` and match 0.4.1-alpha.4.
- **Control Flow Guard.** `swgen.exe` is started with Control Flow Guard turned off (the "CFG: off" process
  mitigation policy), as Savage Wind does. On some Windows 11 builds swgen crashes without it (exit status
  0xC0000409).
- **Cleanup.** The zig caches go in the work folder, and every child process ends if the tool is stopped. The tool
  only empties folders it made (they hold a `.sw_rebuild-work` or `.sw_rebuild-cache` marker file).
- **Cache.** A module's objects are reused when nothing they are built from has changed: its bytes, build id and
  address, the id, the flags, `swgen`, `zig`, the headers and the HLE list.
- **Failed builds.** If a build fails, the tool prints the end of the failing log. The logs stay in
  `out\rebuild\work`.
