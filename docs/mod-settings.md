# Mod settings pages

A mod can have its own settings page: on/off rows, sliders, choices, keys, texts and buttons, on its first page or on
sub-pages. The player changes them in the game (Options > Mods > Mod settings and info) or in Mod Manager on the PC.
Rows can lock or hide while other rows change. The rules are written in `mod.toml`; a mod with a DLL also reads and
reacts in C (mod API 3, [`native/modhost/swmod.h`](../native/modhost/swmod.h)). There is no script language for now
(Lua may come later).

What works now (checked without the game, see [What is verified](#what-is-verified)):

- Checking `[[settings]]` and `[[pages]]` at `add` and `deploy` (`tools/mod_settings.py`).
- Values saved per mod in `<library>\settings\<mod id>.json`, by the game and by the PC tools.
- modhost's settings engine (mod API 3): getters, watchers, `setting_set`, the table the Mod menu uses, and the
  console commands `modsettings` and `modset`.
- Option folders: a setting can pick RomFS files at the next deploy.
- The example [`native/examples/settings_demo`](../native/examples/settings_demo/mod.toml) has one of every kind.

The game's Mod menu pages for settings are built separately ([mod menu](mod-menu.md)).

## A small example

```toml
[mod]
id = "giruka.better-sprint"
name = "Better Sprint"
version = "1.0.0"
loader_api = 3                  # the DLL reads its settings through mod API 3

[code]
windows = "better_sprint.dll"

[[pages]]
key = "advanced"
label = "Advanced"
description = "Fine tuning."

[[settings]]
key = "mode"
type = "choice"
label = "Sprint mode"
description = "Classic: the game's own. Custom: pick the speed below."
default = "classic"
applies = "live"
choices = [
  { value = "classic", label = "Classic" },
  { value = "fast", label = "Fast", sets = { speed = 150 } },
  { value = "custom", label = "Custom" },
]

[[settings]]
key = "speed"
type = "slider"
label = "Sprint speed"
min = 50
max = 200
step = 10
default = 100
unit = " percent"
applies = "live"
show_if = "mode == 'custom'"
```

Check a mod folder before you add it: `python tools/mod_settings.py check <mod folder>`. It lists the problems, or
prints what modhost will read.

## Kinds of rows

| `type` | In the game | Value | Keys of its own |
|---|---|---|---|
| `toggle` | ON/OFF | `true` / `false` | `default` (false); `romfs`, `dlc_romfs` (with `applies = "deploy"`: the folder used while it is ON) |
| `slider` | a row with arrows, one value per step ("120 percent") | a number | `min`, `max`, `step`, `default` (min), `unit` (12 characters). At most 201 values |
| `choice` | a row with arrows | the choice's `value` | `choices` (2 to 64), `default` (the first) |
| `key` | a button showing the key ("F6"); A, then press a key | a key name | `default` ("" = none) |
| `text` | shown, not changed: "Change it in Mod Manager on your PC" | text | `default` (""), `max_length` (1 to 64, default 32) |
| `button` | a button with its own text | (how often it was pressed) | `button` (16 characters). Needs a DLL; always `live` |

A choice is `{ value = "fast", label = "Fast" }`, plus optional `sets` (a preset), `romfs` and `dlc_romfs` (option
folders). Values are lowercase letters, digits, `.`, `_` and `-`, up to 32. Labels are up to 20 characters.

Key names: `F1`-`F24`, `A`-`Z`, `0`-`9`, `Num0`-`Num9`, `Space`, `Tab`, `Enter`, `Insert`, `Delete`, `Home`, `End`,
`PageUp`, `PageDown`, `Up`, `Down`, `Left`, `Right`, `Shift`, `Ctrl`, `Alt`. (Esc and Backspace are not keys you can
pick: key capture uses them.)

## Keys every setting has

| Key | Needed | What it is |
|---|---|---|
| `key` | yes | Its name: lowercase letters, digits and `_`, up to 32, starting with a letter. Unique in the mod. |
| `type` | yes | `toggle`, `slider`, `choice`, `key`, `text` or `button`. |
| `label` | yes | The row's title: up to 28 characters, plain ASCII. |
| `description` | no | The line under the page: up to 80 characters. |
| `applies` | no | `live`, `restart` (the default) or `deploy`: see below. |
| `page` | no | The `key` of a `[[pages]]` entry; without it, the row is on the mod's first page. |
| `show_if` | no | A condition: the row shows only while it is true. |
| `enable_if` | no | A condition: the row is locked while it is false. |

A `[[pages]]` entry has `key`, `label` and `description`. Each page is a button on the mod's first page.

The game's menu font has no `{ } # $ % @ ^ ` | ~`. A label or description with one of them is only a warning: the
Mod menu shows another sign for it until the symbol fix is on ([fonts](fonts.md)).

## When a change takes effect

| `applies` | When | Needs |
|---|---|---|
| `live` | At once: the mod's DLL is told (its watchers run) | `[code] windows` and `loader_api = 3` |
| `restart` | At the next game start. Until then the DLL still gets the value the game started with | `[code] windows` |
| `deploy` | At the next deploy (Play does it): the setting picks option folders | `romfs` / `dlc_romfs` folders |

The game's description line says "Changes at your next start." for a `restart` or `deploy` setting that was changed.

## Conditions: rows that lock or hide

`show_if` and `enable_if` are small conditions over the mod's own settings:

```
mode == 'custom'
numbers && speed >= 100
!numbers || on('giruka.hud-pack')
(mode == 'fast' || mode == 'custom') && drain < 1.5
```

- A toggle is true or false, a slider is a number, a choice is its value (`'custom'`), a key is its name (`'F6'`,
  `''` for none), a text is its text.
- `==`, `!=`, `<`, `<=`, `>`, `>=`, `&&`, `||`, `!` and brackets. `<` and the others compare numbers only.
- `on('mod.id')`: that mod is on (its `[mod]` id, or its id in the library).
- Texts in quotes can't have spaces. A choice is compared only with its own values, and a setting never with itself.
  A button has no value to test. At most 32 steps.
- `show_if` can't go round in a circle (a shows when b does, and b when a does).

Python checks every condition at `add` and turns it into a short list of steps; modhost only evaluates those steps.
If a list can't be evaluated (it never should), the row counts as shown and unlocked, so a setting is never hidden
by mistake.

A locked row says why, in words made from its condition: "Locked: needs Sprint mode Custom." The row's value can't be
changed while it is locked (the DLL's own `setting_set` still can).

A hidden row: the Mod menu builds the page again when a `show_if` changes, if the game allows it (the early game
check decides). Otherwise the row is greyed until the page is opened again. Put the row that controls others first.

## Presets: `sets`

A choice can set other settings of the mod when it is picked:

```toml
choices = [{ value = "fast", label = "Fast", sets = { speed = 150, numbers = true } }]
```

Only toggles, sliders and choices of the same mod; each value must be one the setting has. The changed rows redraw at
once. The DLL's watcher then gets `key = NULL` (several changed).

## Option folders (`applies = "deploy"`)

A `deploy` toggle or choice picks folders inside the mod. Each one holds RomFS folders directly (`Actor/`, `Pack/`,
`UI/`, ...), with no `romfs/` or title-id folder in it. `dlc_romfs` is the same for DLC files.

```
my-mod/
  mod.toml
  romfs/...              the mod's own files ([data] romfs = "romfs")
  options/gold/UI/...    used while "Wheel look" is Gold
  options/gold-dlc/...   its DLC files (dlc_romfs)
```

- Deploy puts a chosen folder right after the mod's own files, so it replaces them, and mods later in the list still
  win over it.
- The mod needs its own `[data] romfs` folder too, and the option folders sit next to it (not inside it).
- The values a deploy used are kept in `launch.json` (`"settings"`). When the player changes one, Play deploys again.

## Reading settings in a DLL (mod API 3)

```c
#include "swmod.h"

static const SwModApi *api;
static volatile bool g_speed_changed;

static void on_change(const SwModApi *self, const char *key, void *user) {
    (void)self, (void)user;
    if (!key || !strcmp(key, "speed")) g_speed_changed = true;   // set a flag; do the work in your hook
}

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    if (SWMOD_HAS(a, setting_watch) && a->mod_id) a->setting_watch(a, on_change, NULL);
    return 0;
}

// in a hook or command:
double speed = SWMOD_HAS(api, setting_num) ? api->setting_num(api, "speed", 100) : 100;
```

| Field | What it does |
|---|---|
| `mod_id` | The `[mod]` id this DLL came with, or NULL (no settings deployed). |
| `setting_int(api, key, def)` | Toggle 0/1, slider its value (rounded), choice its index, key its Windows virtual-key code (0 none), button how often it was pressed. `def` when there is no such setting. |
| `setting_num(api, key, def)` | The same as a number (slider steps below 1 stay as they are). |
| `setting_str(api, key, buf, cap)` | As text: choice its value ("fast"), key its name, text the text, toggle "true"/"false". Returns the full length, -1 for none. |
| `setting_watch(api, fn, user)` | Call `fn(api, key, user)` after a `live` setting changed or a button was pressed. From `swmod_init` only, 8 per mod. |
| `setting_set(api, key, value)` | The mod changes its own setting (a counter, a preset worked out in code): checked, saved, shown at once. A choice's `sets` apply. Watchers are not called for it. |
| `settings_ui()` | The table a settings screen uses (the Mod menu): `SwModSettingsUi` in `swmod.h`. |

Rules:

- The getters work on any thread, at any time, without a lock: call them where you use the value.
- For `restart` and `deploy` settings they return the value the game started with.
- A watcher runs on the thread that made the change: the game's thread inside the Options screen (or the frame thread
  for `modset`). Keep it short: set a flag, then do the work in your hook or command. No file work, no waits.
- On an older modhost (`SWMOD_HAS(api, setting_int)` is false), or without settings.txt (`mod_id` is NULL), use your
  defaults. A mod built for API 1 or 2 runs unchanged.
- A mod loaded with `modload` gets its settings too (by its file name); its watchers are taken out at `modunload`.
- `setting_set` and `set_index` return `SWMOD_SET_OK`, `SWMOD_SET_NO_SETTING`, `SWMOD_SET_BAD_VALUE`,
  `SWMOD_SET_LOCKED` (the Mod menu only) or `SWMOD_SET_READ_ONLY` (a text or a button in the Mod menu).

## Changing settings on the PC

```
python tools/mod_settings.py show better-sprint             # the values now (hidden and locked rows marked)
python tools/mod_settings.py set better-sprint speed 120
python tools/mod_settings.py set better-sprint mode Fast     # a choice's value or its label
python tools/mod_settings.py reset better-sprint             # every setting back to its default
```

`--library <folder>` picks the library (default: `SWMOD_LIBRARY`, else Documents\SavageWind\modloader). The mod is its
`[mod]` id or its id in the library. A locked row is refused with the same words the game shows. Changes are refused
while Savage Wind runs: the game writes the file then. Mod Manager's window has a settings editor too
(being built, [mod manager](mod-manager.md)); a text setting is changed there.

Mod sets ([profiles](profiles.md)) keep each set's settings: switching sets puts that set's values in place.

## Files

| File | Written by | Read by |
|---|---|---|
| `<library>\settings\<mod id>.json` | modhost while the game runs; `tools/mod_settings.py`, Mod Manager and `modmenu.py setting` while it doesn't | modhost, deploy, Mod Manager |
| `deploy\native\settings.txt` | deploy (`tools/mod_settings.py`) | modhost at game start |

A values file holds only what differs from the defaults, so a mod update's new default reaches players who never
changed it:

```json
{"format": 1, "mod": "giruka.better-sprint", "values": {"mode": "custom", "speed": 120}}
```

Both writers use a temp file and a rename, so the file is never half written. modhost writes from its own thread,
which starts with the first change (about 30 ms later), and keeps keys it doesn't know as they were. A change made
just before the game closes is saved as it closes (modhost 0.4.1). A value that doesn't fit the mod any more (the
mod changed) is ignored, and its default is used.

`settings.txt` is tab text, one record a line, made at deploy from every library mod that has settings (the format
is in `tools/mod_settings.py`, `compile_lines`). modhost finds it next to its mods folder (`SWMOD_SETTINGS` to point
elsewhere) and the library from its `library` line. Without it, every mod uses its defaults.

## Console commands

| Command | Who | What it does |
|---|---|---|
| `modsettings` | any player | The mods with settings. |
| `modsettings <mod>` | any player | One mod's settings: value, hidden, locked, and "at your next start". |
| `modset <mod> <key> <value>` | developer mode | Change a setting as the Mod menu does (locks and presets apply, watchers run). A button: no value. `modset <mod> reset`: all defaults. |

## Advice

- Sliders of up to 21 values are easiest: holding left or right may not repeat in the game (`add` warns above 21).
- Pages of up to 12 rows are easiest to read (`add` warns above 12; 30 is the most).
- Say in the description what a row does, in plain words. The game adds "Locked: needs ..." and "Changes at your next
  start." itself.

## Limits

- At most 64 settings and 16 pages a mod, and 30 rows a page (each page is a row on the first page).
- Text can't be typed in the game's Options (Savage Wind has no text box there): text settings are changed on the PC.
- Key capture takes keyboard keys only.
- `.bnp` and UKMM package options stay fixed when the package is added ([bnp](bnp.md)).

## What is verified

Checked on this PC without the game (`tests/test_mod_settings.py`):

- The schema accepts every kind of row and refuses the problems above, with the line that says why.
- Mod sets: switching sets puts each set's values back, and the next deploy follows them (option folders, "at your
  next start", settings.txt). Option folders go into the deploy plan right after their mod.
- 35 conditions with 8 sets of values: Python and modhost give the same answer in all 280 cases (a choice value "2"
  stays a text; 0.5 + 5 x 0.1 equals 1).
- modhost against a fake game: settings.txt is read, the getters give the start value for `restart` and `deploy`
  settings, watchers run once per Mod-menu change and not for `setting_set`, `setting_watch` is refused after
  `swmod_init`, locked rows are refused with their words, `sets` apply, two threads read values while a third changes
  them, no writer thread runs before the first change, the writer keeps unknown keys, Python reads what modhost wrote,
  a mod loaded with `modload` loses its watcher at `modunload`, a mod without settings.txt gets its defaults, and a
  change made just before the game closes is still saved.

Not verified yet (needs the game): the Mod menu's pages, key capture, held left/right on a slider, how a locked
ON/OFF row looks, and rebuilding a page for `show_if`.

## Later

`setting_choices` (lists a DLL fills at run time), `.bnp` and UKMM options as settings, controller buttons for key
rows, and maybe a script language (Lua).
