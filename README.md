# Breathalyzer

**Breathalyzer** (`blz` for short) is a mod loader, modding SDK, tools and multiplayer for
**[Savage Wind Recomp](https://github.com/Revivedx/SavageWindRecomp)**, the native PC build of the Switch version
of *Breath of the Wild* (1.9.0). Made by giruka. Wii U and existing Switch mods
should keep working where that's technically possible, so players don't have to start over. A small multiplayer
mod is the first showcase.

**Status:** early, but working. Every tool below that touches the game was tested in the real game on 2026-11-06
and 07 with the released Savage Wind 0.4.1-alpha.4, without changing Savage Wind itself.
See [docs/PLAN.md](docs/PLAN.md).

## No game files, ever

This project ships only its own code. You need your own game, dumped from your own console. Never add game
files, keys, firmware, the C code Savage Wind generates, or links to any of them. See [NOTICE.md](NOTICE.md).

`tools/check_clean.py` runs on every push. It fails on game and RomFS files (by type, magic bytes or path), the C
code Savage Wind generates and objects built from it, folders our tools fill from the game, saves, code maps,
Savage Wind logs, screenshots from Savage Wind's `shot` command, console keys, title keys and `.tik` files, and
files over 10 MB. It can't spot links, so check those yourself.

## Quick start (players)

```
python tools/modman.py init                      # finds Savage Wind in Documents\SavageWind
python tools/modman.py add "<mod folder or .zip>"
python tools/modman.py deploy
python tools/modman.py run
```

The mod manager handles RomFS mods, ExeFS patches, cheats and native mods together. See
[mod-manager](docs/mod-manager.md). Tested in game on 2026-11-06 with all four kinds at once. Wii U mods are
converted when you add them (texts, maps and actor data, tested in game with a test text mod; models and textures,
proven on your own Switch files but not yet tried with a real Wii U model mod or in game).

## What works today

| Tool | What it does | Docs |
|---|---|---|
| `tools/modman.py` | The mod manager: add, order, deploy and start mods (uses the tools below) | [mod-manager](docs/mod-manager.md) |
| `tools/modman_gui.py` | A large-print window for the mod manager (big text, every button works with the keyboard) | [mod-manager](docs/mod-manager.md#window) |
| `tools/quality.py` | Quality presets Low, Medium, High and Original: more fps on a weak PC (grass, draw distance, shadows, effects) | [performance](docs/performance.md) |
| `tools/wiiu_convert.py` | Converts a Wii U mod to a Switch mod on your PC: texts, maps, actor data, models and textures (audio not yet). `modman add` uses it | [wiiu-mods](docs/wiiu-mods.md), [wiiu-models](docs/wiiu-models.md) |
| `tools/mod_inspect.py` | Says what kind of mod a folder or .zip is, and whether it can be used | [MOD-COMPAT](docs/MOD-COMPAT.md) |
| `tools/romfs_mods.py` | Puts Switch RomFS mods into one overlay folder for Savage Wind | [romfs-mods](docs/romfs-mods.md) |
| `tools/exefs_patch.py` | Applies ExeFS patches (`.ips`, IPS32, `.pchtxt`) to your own `main` | [exefs-patches](docs/exefs-patches.md) |
| `tools/sw_rebuild.py` | Rebuilds the game code after a code patch, without the Savage Wind window | [rebuild](docs/rebuild.md) |
| `tools/cheats.py` | Runs Atmosphere cheat files against the running game | [cheats](docs/cheats.md) |
| `tools/build_native.py` + `native/` | Native code mods (DLLs) loaded into the game by a small host | [code-mods](docs/code-mods.md) |
| `tools/gen_hooks.py` | Lets native mods hook any game function (before, after or instead), via a rebuild | [hooks](docs/hooks.md) |
| `tools/mp/` | Multiplayer proof of concept: see friends on a radar, teleport to them, and (with the ghost mod) see them as an object in your world | [multiplayer](docs/multiplayer.md), [ghost](docs/ghost.md) |
| `tools/codemap.py` | Names functions in your own `main` from its C++ type info (output stays on your PC) | [code-map](docs/code-map.md) |
| `tools/control.py` | Small client for Savage Wind's control port (used by the tools above) | |
| `tools/check_clean.py` | Makes sure no game files are in the repo | [NOTICE](NOTICE.md) |

```
python -m unittest discover -s tests                # run the tests (made-up data only)
python tools/check_clean.py                         # make sure no game files are in the repo
python tools/check_clean.py "<folder>"              # every file in a folder, even ones git ignores (a release, say)
```

`check_clean.py` exits with 0 when clean, 1 when it blocked a file, and 2 when the folder isn't one. In a folder you
name, it blocks links to other folders instead of following them. A file we made that a rule mistakes for a game file
goes in `.cleanallow` (one path per line; a line starting with `#` is a comment, and a `#` after a path is part of
the path); a listed path skips every check.

## Layout

| Folder | What's in it |
|---|---|
| `docs/` | Plan, mod compatibility, how-tos for each tool, and the [game addresses](docs/addresses.md) the mods and tools use (1.9.0) |
| `native/` | The native mod host, its mod API header (`swmod.h`), the [game basics](docs/game-basics.md) header (`botw.h`) and the example mods (hello, stamina_hook, ghost, basics_show_pos, basics_inf_stamina, basics_teleport_up), and the recipes `tools/quality.py` builds its performance mods from (perf_effects, perf_shadows) |
| `tools/` | Python tools (standard library only) |
| `tests/` | Tests built from dummy files |
| `out/`, `build/` | What the tools make on your PC (ignored by git; never commit) |
| `upstream/` | Local clone of Savage Wind's `test` branch. Ignored by git; not part of this repo |

## License

MIT (see [LICENSE](LICENSE)). It covers this project's code only, and grants nothing for Nintendo's game.
