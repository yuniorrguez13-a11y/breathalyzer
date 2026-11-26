# Breathalyzer

**Breathalyzer** (`blz` for short) is a mod loader, modding SDK, tools and multiplayer for
**[Savage Wind Recomp](https://github.com/Revivedx/SavageWindRecomp)**, the native PC build of the Switch version
of *Breath of the Wild* (1.9.0). Made by giruka. Wii U and existing Switch mods
should keep working where that's technically possible, so players don't have to start over. Multiplayer on your
home network is being built; it is not in a download yet.

**Status:** early. The early version 0.3.0-early.2 is out: the launcher, the mod manager and a few basic mods.
Multiplayer, playing the story together, playing as other characters and emotes are **not** in it.
The tools under "What works today" were tried in the real game (from 2026-11-06) with Savage Wind 0.4.1-alpha.4,
without changing Savage Wind itself, except where a row says they were not. See [docs/PLAN.md](docs/PLAN.md).

## No game files, ever

This project ships only its own code. You need your own game, dumped from your own console. Never add game
files, keys, firmware, the C code Savage Wind generates, or links to any of them. See [NOTICE.md](NOTICE.md).

`tools/check_clean.py` runs on every push. It fails on game and RomFS files (by type, magic bytes or path), the C
code Savage Wind generates and objects built from it, folders our tools fill from the game, saves, code maps,
Savage Wind logs, screenshots from Savage Wind's `shot` command, console keys, title keys and `.tik` files, and
files over 10 MB. It can't spot links, so check those yourself.

## Download and play (no Python needed)

Breathalyzer is also the launcher for Savage Wind. There are two downloads:

- **Breathalyzer** (`breathalyzer-<version>.zip`): the launcher and mod manager with no bundled mods. With an empty
  mod list it plays the plain game; mods you add yourself are played too (both downloads share your mod list).
- **Breathalyzer Plus** (`breathalyzer-plus-<version>.zip`): the same, plus our own mods. Before your first Play
  you pick which ones are on; each has a title and a short description, and mods that change the game start off.
  In 0.3.0-early.2 these are the basic mods only: the Mods page in the game, Show Position, Infinite Stamina,
  Teleport Up and a performance mod. No multiplayer yet.

Unpack the zip, open **Start Breathalyzer**, show it your Savage Wind folder once, and press **Play**. It brings its
own Python, so you install nothing. Neither zip has game files or mods made by other people, and Breathalyzer never
goes online by itself. See [launcher](docs/launcher.md); how the zips are made: [release](docs/release.md).

From the repo, the same window: `python tools/launcher.py`. It finds Savage Wind (or asks once), checks it, and
opens Home: a big **Play** that gets your mods ready and starts the game, and your mods with ON/OFF and what each
does.

## Quick start (command line)

```
python tools/modman.py init                      # finds Savage Wind in Documents\SavageWind
python tools/modman.py add "<mod folder or .zip>"
python tools/modman.py deploy
python tools/modman.py run
```

The mod manager handles RomFS mods, ExeFS patches, cheats and native mods together. See
[mod-manager](docs/mod-manager.md). Tested in game on 2026-11-06 with all four kinds at once. Wii U mods are
converted when you add them (texts, maps and actor data: tested in game with a test text mod; models and textures:
checked on your own Switch files, but not tried in the game yet, or with a real Wii U model mod).

## What works today

| Tool | What it does | Docs |
|---|---|---|
| `tools/modman.py` | The mod manager: add, order, deploy and start mods (uses the tools below), and mod sets you switch between (`profile`) | [mod-manager](docs/mod-manager.md), [profiles](docs/profiles.md) |
| `tools/launcher.py` | The launcher: finds and checks Savage Wind, one big Play button (deploys when needed, then starts the game), your mods with what each does, Settings, the Plus mod picker | [launcher](docs/launcher.md) |
| `tools/modman_gui.py` | A large-print window for the mod manager (big text, every button works with the keyboard) | [mod-manager](docs/mod-manager.md#window) |
| `tools/quality.py` | Quality presets Low, Medium, High and Original: more fps on a weak PC (grass, draw distance, shadows, effects) | [performance](docs/performance.md) |
| `tools/menu_bg.py` | Your own picture on the game's main menu (tried in game with version 1.0.0; 1.1.0, the readable-words fix, not tried in game yet; a mod's `[mod] title_picture` is being built) | [menu-background](docs/menu-background.md) |
| `tools/wiiu_convert.py` | Converts a Wii U mod to a Switch mod on your PC: texts, maps, actor data, models and textures (models and textures not tried in the game yet; audio not yet). `modman add` uses it | [wiiu-mods](docs/wiiu-mods.md), [wiiu-models](docs/wiiu-models.md) |
| `tools/mod_inspect.py` | Says what kind of mod a folder or .zip is, and whether it can be used | [MOD-COMPAT](docs/MOD-COMPAT.md) |
| `tools/romfs_mods.py` | Puts Switch RomFS mods into one overlay folder for Savage Wind | [romfs-mods](docs/romfs-mods.md) |
| `tools/exefs_patch.py` | Applies ExeFS patches (`.ips`, IPS32, `.pchtxt`) to your own `main` | [exefs-patches](docs/exefs-patches.md) |
| `tools/sw_rebuild.py` | Rebuilds the game code after a code patch, without the Savage Wind window | [rebuild](docs/rebuild.md) |
| `tools/cheats.py` | Runs Atmosphere cheat files against the running game | [cheats](docs/cheats.md) |
| `tools/build_native.py` + `native/` | Native code mods (DLLs) loaded into the game by a small host | [code-mods](docs/code-mods.md) |
| `tools/gen_hooks.py` | Lets native mods hook any game function (before, after or instead), via a rebuild | [hooks](docs/hooks.md) |
| `tools/mp/` | Multiplayer on your home network (LAN), **being built, not in a download yet**: host or join from the launcher, see your friend as Link (or as the character they pick) with their moves and the items they carry, and play the story together. Tried on one PC only, with a test bot as the friend; only an early version ran on two PCs (2026-11-07). Story together: you get the host's story when you join; while you play, only towers and memories are shared | [multiplayer](docs/multiplayer.md), [ghost](docs/ghost.md) |
| `tools/codemap.py` | Names functions in your own `main` from its C++ type info (output stays on your PC) | [code-map](docs/code-map.md) |
| `tools/control.py` | Small client for Savage Wind's control port (used by the tools above) | |
| `tools/check_clean.py` | Makes sure no game files are in the repo | [NOTICE](NOTICE.md) |

Being built (Mods page v2). Not finished, not in a download, and not tried in the game yet:

| Tool | What it will do | Docs |
|---|---|---|
| `tools/mod_settings.py` | Settings pages for mods (on/off, sliders, choices, keys, buttons, sub-pages) in the game and in Mod Manager | [mod-settings](docs/mod-settings.md) |
| `tools/font.py` | Fixes the symbols the menu font lacks, an easy-to-read font, and font mods | [fonts](docs/fonts.md) |
| `tools/mod_pictures.py`, `tools/layout_patch.py` | Mod pictures in Mod Manager and (opt-in) on the Mods page | [mod-pictures](docs/mod-pictures.md) |

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
| `native/` | The native mod host, its mod API header (`swmod.h`), the [game basics](docs/game-basics.md) header (`botw.h`) and the example mods (hello, stamina_hook, ghost, room, basics_show_pos, basics_inf_stamina, basics_teleport_up, settings_demo, emotes), and the recipes `tools/quality.py` builds its performance mods from (perf_effects, perf_shadows) |
| `tools/` | Python tools (standard library only; Pillow, when installed, lets the menu picture tool read JPG, WEBP, BMP and GIF) |
| `tests/` | Tests built from dummy files |
| `site/` | The project's web page: static `index.html`, CSS and a small script, no trackers (design: [site/DESIGN.md](site/DESIGN.md)) |
| `out/`, `build/` | What the tools make on your PC (ignored by git; never commit) |
| `upstream/` | Local clone of Savage Wind's `test` branch. Ignored by git; not part of this repo |

## License

MIT (see [LICENSE](LICENSE)). It covers this project's code only, and grants nothing for Nintendo's game.
