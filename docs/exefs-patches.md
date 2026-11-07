# ExeFS patches (.ips, IPS32, .pchtxt)

`tools/exefs_patch.py` applies Switch ExeFS patches to your own game's `main` (and `sdk`, `subsdk0`), so
Savage Wind runs the patched game. It never changes your dump or the Savage Wind folder: it writes patched
copies to a folder you name.

## Check, then apply

```
python tools\exefs_patch.py check "<your game>\exefs" "<patch file or folder>"
python tools\exefs_patch.py apply "<your game>\exefs" "<output>\exefs" "<patch file or folder>"
```

`check` says, for each patch:

- `OK main: N patches`: it is for your game version and will apply.
- `SKIP ... made for another game version`: the patch is for another version (most often 1.6.0). It needs porting.
- `CLASH`: two patches write different bytes to the same place. Nothing is applied until you remove one.
- `NOTE ... must be rebuilt` or `NOTE data-only patches`: whether the game code needs rebuilding (below).

`apply` refuses an output folder that is the input `exefs` folder, however it is spelled (other case, 8.3 name,
junction, `subst` drive), and one that holds a hard link to a file in it, so your own files are never written.

Patches are matched by build id, the way Atmosphere does it: an `.ips` file is named after the module's build id
(`atmosphere/exefs_patches/<name>/<build id>.ips`), and a `.pchtxt` file names it on its `@nsobid-` line.
For 1.9.0 `main` the build id starts with `cd57b23fa4bbad65`.

## Running the patched game

Savage Wind runs the game code it compiled at setup. So:

- **Patches to data** (text, tables, constants) take effect at the next start. No rebuild.
- **Patches to code** need the game code rebuilt with `tools/sw_rebuild.py` (10-20 minutes). See [rebuild](rebuild.md).

To try patches without touching your Savage Wind folder, make a separate game folder:

```
<folder>\exefs   the output of `apply`
<folder>\romfs   a junction to <Savage Wind>\extracted\romfs   (mklink /J "<folder>\romfs" "<...>\extracted\romfs")
```

and start the game from it:

```powershell
$env:SAVAGEWIND_GAME_LIB = '<Savage Wind>\extracted\swgame.dll'   # or the rebuilt library for code patches
& '<Savage Wind>\savagewind.exe' '<folder>' --play
```

The mod manager ([mod-manager](mod-manager.md)) does all of this for you.

## Tested

On 2026-11-06 with Savage Wind 0.4.1-alpha.4:

- **Data patch:** a `.pchtxt` that changes a text the game writes to its log at start-up showed the new text in
  `savagewind.log`, with no rebuild.
- **Code patch:** a one-instruction `.pchtxt` (skip the store in the stamina setter) applied, rebuilt with
  `tools/sw_rebuild.py` (10 minutes: only `main` was rebuilt, the rest came from its cache) and run from a separate
  game folder. Sprinting for 8 seconds kept stamina at 1000; the same sprint on the unpatched game drained it
  from 1000 to 309.

## Limits

- A patch that changes an instruction Savage Wind patches itself makes the rebuild stop with an error.
- Patches for other game versions aren't ported automatically yet.
- Savage Wind never runs exlaunch / Skyline code mods (`subsdk1`...). Remake those as native mods ([code-mods](code-mods.md)).
