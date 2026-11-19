# Mod sets (profiles)

A mod set is a named list of your mods: which are on, in which order, the options you picked for BCML and UKMM
packages, the settings you changed and the font you picked ([fonts](fonts.md)). Switch between sets to play with
other mods, without adding or removing any.

```
python tools/modman.py profile                    # the sets, and the one in use
python tools/modman.py profile new "Hard mode"    # a new set, made from the mods as they are now; it is in use
python tools/modman.py profile use Default        # switch; then deploy
python tools/modman.py profile remove "Hard mode"
python tools/modman.py profile copy Default "Hard mode"     # a copy; it uses the normal saves until own-save is on
python tools/modman.py profile rename "Hard mode" Speedrun
python tools/modman.py profile own-save Speedrun on --copy  # its own save folder, started from the save in use
```

- **Default** is the set you had before you made any. A library that never used sets stays as it was.
- The set in use is what `list`, `enable`, `disable` and `move` change. Switching keeps it as it is, then puts the
  other set's order, on/off, settings and font in place. Run `deploy` after a switch.
- A mod added while another set was in use is off in this set, at the end of the list. A mod a set names that was
  removed from the library is left out (the switch says so).
- Options of a BCML or UKMM package are fixed when it is added. When a set had other options, the switch says so: to
  change them, remove the mod and add it again with `--option`.
- Switching is refused while Savage Wind runs (the game may be writing the settings).
- Names: up to 40 letters, digits, spaces, `.`, `-` and `_`, starting with a letter or digit. Case doesn't matter.

Added with the Mods page v2 (2026-11-18, commit 65003e3): `copy`, `rename`, `delete` and `own-save <name> on|off`
(the set's own save folder, `<library>\profiles\<file name>\save`), a save backup before a start with a different
set, mod sets in the Mod Manager window, and the set chooser on the game's Mods page (*More*; checked in the game
that night).

## Files

| File | What it holds |
|---|---|
| `config.json` | The mod list of the set in use. `"profile": "<name>"` names the set, left out for Default |
| `profiles\<name>.json` | Each set, named in lowercase with `-` for other characters (`Hard mode`: `hard-mode.json`) |

A set's file:

```json
{"tool": "botw-mod-loader modman", "format": 1, "kind": "profile", "name": "Hard mode", "saved": "2026-11-07T17:00:00",
 "mods": [{"id": "better-sprint", "enabled": true}, {"id": "linkle", "enabled": false}],
 "options": {"linkle": ["Linkle with hood"]},
 "settings": {"someone.better-sprint": {"speed": 150}},
 "font": {"choice": "easy-read", "fix_symbols": true}}
```

`mods` is in load order (later mods win). `settings` holds the values as `settings\<id>.json` does: only those
unlike the default. `font` holds the font settings as `font.json` does ([fonts](fonts.md)): the font picked
(`auto`, `game`, `easy-read` or `mod:<id>`) and the symbols fix. It is there only when the font tool is; a set without
it leaves the font as it is. When a set picks a font mod that is off in it, the switch says so and the game's font
choice goes back to automatic.

## History

`deploy\history.jsonl` keeps one JSON object a line, oldest first. Deploys keep the file; past 600 lines only the
newest 300 stay.

| `"type"` | Written by | Fields |
|---|---|---|
| `deploy` | `deploy` | `time`, `hash` (as launch.json's), `profile`, `mods` (every library mod in order: `id`, `name`, `enabled`, `kinds`, `version`), `settings` (the deploy settings used) |
| `start` | `run` | `time`, `hash` of the deploy started, `profile`, `pid` |
| `result` | the crash check (being built) | `time`, `hash`, `start` (the start's time), `result`: `clean`, `crash` or `unknown` |

Readers skip lines and types they don't know.
