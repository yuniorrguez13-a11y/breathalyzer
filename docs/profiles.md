# Mod sets (profiles)

A mod set is a named list of your mods: which are on, in which order, the options you picked for BCML and UKMM
packages, and the settings you changed. Switch between sets to play with other mods, without adding or removing any.

```
python tools/modman.py profile                    # the sets, and the one in use
python tools/modman.py profile new "Hard mode"    # a new set, made from the mods as they are now; it is in use
python tools/modman.py profile use Default        # switch; then deploy
python tools/modman.py profile remove "Hard mode"
```

- **Default** is the set you had before you made any. A library that never used sets stays as it was.
- The set in use is what `list`, `enable`, `disable` and `move` change. Switching keeps it as it is, then puts the
  other set's order, on/off and settings in place. Run `deploy` after a switch.
- A mod added while another set was in use is off in this set, at the end of the list. A mod a set names that was
  removed from the library is left out (the switch says so).
- Options of a BCML or UKMM package are fixed when it is added. When a set had other options, the switch says so: to
  change them, remove the mod and add it again with `--option`.
- Switching is refused while Savage Wind runs (the game may be writing the settings).
- Names: up to 40 letters, digits, spaces, `.`, `-` and `_`, starting with a letter or digit. Case doesn't matter.

Not yet: a set with its own saves (`SAVAGEWIND_SAVE_DIR`), a set picked in the game, and sets in the Mod Manager
window.

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
 "settings": {"someone.better-sprint": {"speed": 150}}}
```

`mods` is in load order (later mods win). `settings` holds the values as `settings\<id>.json` does: only those
unlike the default.

## History

`deploy\history.jsonl` keeps one JSON object a line, oldest first. Deploys keep the file; past 600 lines only the
newest 300 stay.

| `"type"` | Written by | Fields |
|---|---|---|
| `deploy` | `deploy` | `time`, `hash` (as launch.json's), `profile`, `mods` (every library mod in order: `id`, `name`, `enabled`, `kinds`, `version`), `settings` (the deploy settings used) |
| `start` | `run` | `time`, `hash` of the deploy started, `profile`, `pid` |
| `result` | the crash check (being built) | `time`, `hash`, `start` (the start's time), `result`: `clean`, `crash` or `unknown` |

Readers skip lines and types they don't know.
