# Mod menu (in the game's Options)

- **What it is:** a **Mods** page inside the game's Options, in the same font and look as the other settings. You
  see your mods, turn them on or off, change their order, and read what each one does. Each mod also has its own
  page: its info, and its settings when it has some. **More** has the mod set, the font, and turn all mods off or on.
- **Use your changes:** close the game, then run `python tools/modmenu.py play` on your PC. Play in Mod Manager does
  not use them yet (the game's **How to use changes** row says which step works). Nothing changes while the game
  runs.
- **Works with** Savage Wind 0.4.1-alpha.4 and BotW 1.9.0 only. With anything else there is no Mods button, and
  the game is as it was.
- **Tested in the real game** (2026-11-07, 3 test mods): everything on the checklist works, except 3 steps not tried
  yet. See [Game test results](#game-test-results).

## Set it up (once)

1. Build it and add it to your mod library: `python tools/modmenu.py setup`
2. Press **Deploy** in Mod Manager (or run `python tools/modman.py deploy`).
3. Start the game with **Play** in Mod Manager.

The Mod menu is itself a mod (id `botw-mod-loader.modmenu`). It shows in Mod Manager as **Mod menu**. If it ever
gives trouble, turn it off there, like any other mod.

## Use it

Open **Options** (on the title screen, or Pause > System > Options). **Mods** is just above **Quit game**.

**The Mods page**

- One **ON/OFF** row per mod, in load order. **A**, left and right, Enter, or a mouse click on ON or OFF switches it.
- The line at the bottom says what the mod does, and what changes once you use your changes: "Turns off once you use
  changes." Mods with hooks or code patches add "Next start takes 10-20 minutes." It changes as soon as you switch.
  The game shows about 80 characters, so a long "what it does" is cut short ("Adds one new text file...").
- **Y** (Restore default settings) undoes your changes: every mod goes back to how it is in the game now.
- **Change order** opens the Order page.
- **Mod settings and info** opens a list of your mods; each one opens that mod's page.
- **More** opens the More page.
- **How to use changes** says what to do: close the game, then run `python tools/modmenu.py play`. (Once Mod
  Manager's Play uses the changes, it says to press Play, and the lines say "at your next start".)
- **B** goes back to the first Options page.

**The Order page**

- One row per mod with its place: 1st, 2nd, 3rd... Left and right move the mod; the other numbers change with it.
  A mod can move only among the places the page shows. A moved mod's line says "Was 1st. Moves once you use changes."
- When two mods change the same file, the **later** one in the list is used.
- **B** goes back to the Mods page, which then shows the new order.

**The More page**

- **Mod set**: the set in use (Default until you make others in Mod Manager). Left and right pick another; it is used
  at your next start. A set keeps its mods, their settings and the font. While another set is picked, the mod rows,
  the Order page and Turn all mods off / on are refused ("Mod set Beta is picked. Change its mods once it is in use."):
  they would change the set that isn't used next. Mod changes made before the pick are not used (the Mods page then
  says so); setting values you changed stay with the set in use. When the next start can't be made ready (a failed
  deploy), the set in use stays, with its own settings and font.
- **Font**: the game's font, the easy-to-read font, or a font mod that is on. Used at your next start.
- **Turn all mods off** / **Turn all mods on**: every mod on the page, at your next start. The Mod menu stays on.
- **What changed**: what is new since your last start ("New since your last start: Better Sprint."). Mod Manager
  writes the list again right before each start, so this (and "Last start had an error") is about the last start.
- **Mods that share files** (after a deploy): which mod's files a later mod replaces.
- **Y** undoes every change here and on the Mods page.

**A mod's page** (Mods > Mod settings and info > the mod)

- The first row is the **mod's name** (its line says what it does), then **Version**, **Made by** and **Game files**
  (how many, and how many another mod later in the list replaces: files merged or made at deploy, like a size table,
  don't count). These work with any Mod Loader.
- Then its settings (with Mod Loader 0.4.1 or newer: mod API 3): ON/OFF rows, sliders and choices (left and right,
  held down they repeat), buttons (A), key rows and text rows. A sub-page is a row with **Open**.
- **Key rows**: press A, then the key within 10 seconds. The line at the bottom counts down ("Press a key now. 8 s
  left."). Backspace clears the key. Esc, F1, F10, F11 and the key left of 1 are Savage Wind's own and can't be used.
- **Text rows** show their text; change them in Mod Manager on your PC.
- A **locked** row (it needs another setting first) is grey, and its line says why: "Locked: needs Sprint mode
  Custom." Changes to it are refused.
- A row another setting turns off stays on the page, grey, with "Not used now: other settings turn it off."
- **Reset settings** (or **Y**) puts every setting of the mod back to its default, keys and texts too. It asks
  first: the line says "Press A again..." and only a second press within 4 seconds resets.
- Settings marked "Changes at your next start." wait for the next start; the others change at once.
- **B** goes back one page.

**Rows you may also see**

| Row | What it means |
|---|---|
| This list may be old | Mod Manager changed mods after the list was made, so ON/OFF here may be wrong. Your changes here still count: only what you change is used. `python tools/modmenu.py play` makes the list new. |
| Last change not used | Your last changes could not be used. The line at the bottom says why, for example "Better Map needs Map Base, which is turned off." Your mods stayed as they were. When the deploy failed, your changes still wait: fix the problem, then play again, or press **Y** to undo them. |
| No mods yet | Add mods in Mod Manager on your PC. |
| Mod list missing | Close the game, then run `python tools/modmenu.py sync` on your PC. |
| More mods in Mod Manager | The page shows the first 30 mods. Mod Manager shows all of them. |
| New since last start | What you turned on or updated since your last start. |
| Last start had an error | The game stopped with an error last time; the line says what is new since then. It never says which mod caused it. |

The Mod menu itself is not on the page (turning it off there would remove the page).

## Use your changes

Mod Manager does not read the game's changes by itself yet. Until it does, start the game with this instead of Play:

```
python tools/modmenu.py play
```

It uses your changes (on/off, order), deploys, and starts the game. Only what you changed in the game is used: a
change you made in Mod Manager in the meantime stays. If the deploy refuses (a mod needs another one that you turned
off, say), your library stays as it was, your changes still wait, and the game shows **Last change not used** with the
reason. If `play` is cut off (the window closed during a long deploy), the next `play` finishes it.

Other commands (all take `--library <folder>`, like Mod Manager):

| Command | What it does |
|---|---|
| `python tools/modmenu.py show` | The list as the game shows it, and the changes that wait. |
| `python tools/modmenu.py apply` | Use the changes now: on/off, order, then deploy. |
| `python tools/modmenu.py undo` | Throw the changes away. |
| `python tools/modmenu.py sync` | Write the game's mod list again, after you changed mods in Mod Manager. Changes from the game that wait are kept. |
| `python tools/modmenu.py set "Better Sprint" off` | Change a mod for the next start without the game (on or off). |
| `python tools/modmenu.py move "Better Sprint" 2` | Move a mod for the next start without the game. |
| `python tools/modmenu.py setup` | Build the Mod menu and add it to the library (again, after an update). |

A mod can be named by its id, its name, or its number in `show` (its place in the game's list; the game's
`modmenu_set` and `modmenu_move` count the same way). Only `show` works while the game runs: the others wait until it
is closed (the running game's changes are based on the list it read when it started).

## If something goes wrong

- **No Mods button:** `savagewind.log` has a line `[mod] modmenu: Mods page off:` and the reason. Type `modmenu` in
  the game's console (F12) to see the same.
- **The list looks old:** the Mods page starts with **This list may be old**. Close the game, then start it with
  `python tools/modmenu.py play` (or run `python tools/modmenu.py sync`).
- **The game crashes in Options:** turn **Mod menu** off in Mod Manager, deploy, and tell us.

## For modders

### Files

These files tie the game and Mod Manager together, all in the mod library (`Documents\SavageWind\modloader` unless
you chose another):

| File | Who writes it | What it holds |
|---|---|---|
| `config.json` | Mod Manager | What is installed and on, in order. |
| `menu\menu.txt` | `tools/modmenu.py` only | What the game shows. The game reads it once, when it starts. |
| `menu\base-<serial>.txt` | `tools/modmenu.py` only | A copy of each `menu.txt`, by its serial: what the game showed when it wrote `pending.txt`. The ones nothing needs are pruned. |
| `menu\pending.txt` | The game (or `modmenu.py set`/`move`/`sync` while the game is closed) | What the player wants at the next start. |
| `menu\apply.json` | `tools/modmenu.py apply` | Only while it runs: `config.json` as it was. Still there next time: the last apply was cut off, so it is finished. |

The menu files are UTF-8 text, one record per line, fields split by tabs, `\n` line ends. Lines of a kind the reader
doesn't know are skipped; a bad first line makes it drop the whole file. All are written to a temporary name and then
renamed, so a reader never sees half a file.

```
swmodmenu	1
serial	<16 hex digits, new on every write>
config	<config.json size>:<its change time in ns>
note	<"Last change not used" reason, or nothing>
use	<modman|command: who uses the changes (the game's texts follow it)>
mod	<id>	<on|off>	<flags: hidden, rebuild, wiiu>	<name>	<what it does>	<warnings>
symbols	full                       (the menu font has every ASCII symbol: tools/font.py fix-symbols)
font	<game|easy-read|mod:<id>>	<label>	[now]    (the Font row's values; "now": the font in use)
profile	<name>	[now]                (the Mod set row's values, Default first; "now": the set in use)
changed	<text>	[crash]              (What changed; "crash": the last start ended with an error)
files	<id>	<files>	<used>	<replaced>	<the mod that replaces most of them>   (from the last deploy)
about	<id>	<desc|version|authors|settings>	<text>   (a mod's info page; at most 4 per mod)
picgrid	<texture>	<w>	<h>	<cols>	<rows>   (deploy\pictures\atlas.json's sheets: icons, then backgrounds)
pic	<id or _mods-page>	<icon cell or -1>	<background cell or -1>
```

These later records came with the Mods page v2. An older Mod menu skips them. The `font` and `profile` rows show only
when their list holds the one in use. Pictures (`picgrid`, `pic`) are read but not drawn yet.

```
swmodmenu-pending	1
serial	<the menu.txt serial it was made from>
mod	<id>	<on|off>        (every listed mod, in the wanted order)
profile	<name>                 (only when the player picked another mod set)
font	<key>                    (only when the player picked another font)
```

Texts are plain ASCII (the option fonts miss many letters): names up to 28 characters, "what it does" up to 70 (from
`description` in the mod's [`mod.toml`](mod-format.md), else from its kinds), warnings up to 60 ("Needs Map Base.",
"Better Map needs this.", "Wii U mod: may look wrong."), the note up to 80. The game's description line shows about
82 characters and cuts the rest at a word, so the mod keeps each line to 80: it shortens "what it does" (at a word
end, with "..."), never the change. The font also has no ``# $ % @ ^ ` { | } ~`` (the game test showed a gap for each):
`tools/modmenu.py` keeps them in `menu.txt`, and the mod draws each one in the game's font tag 3, which has all of
ASCII (the early game check, 2026-11-14: they come out bold italic). Once `menu.txt` says `symbols full`, they are
drawn as they are. Path B gets look-alikes (`{ }` as `( )`, `|` as `/`...). pending.txt can only name mod ids, on/off,
an order, a mod set name and a font key: never a path or a command. `apply` checks the set and the font against the
library's own lists (`modman profile`, `font.py choose`) and drops any that are gone.

**Using pending.txt** (`apply`, and `play` before it starts the game): only what the player changed in the game is
used, found by comparing the file with the list it was made from (`menu.txt` or `base-<serial>.txt`, by the file's
serial). A mod whose on/off differs from that list gets the new on/off. The mods the player moved (those outside the
longest run that kept the list's order) go just after the mod before them in the wanted order. Everything else stays
as `config.json` has it now, so a change made in Mod Manager meanwhile stays. Mods that no longer exist and the Mod
menu itself are ignored. A file whose list is gone becomes `pending.old.txt` (what the player changed can't be told);
a damaged one `pending.bad.txt`; a used one `pending.used.txt`. When the deploy fails, `config.json` is put back byte
for byte (unless another program saved it meanwhile: then that save stays) and `pending.txt` stays. The deploy also
runs when the changes are in `config.json` already but the last deploy wasn't made from it. `sync` moves a waiting
`pending.txt` onto the new list. The "Last change not used" note goes once `config.json` changes. The game does the
same when it finds a `pending.txt` made from an older list: it reads that list's copy and shows the changes; without a
copy it moves the file to `pending.old.txt` (never overwrites it).

The library is `SWMOD_LIBRARY`, else three folders up from `SWMOD_DIR` (Mod Manager sets it to
`<library>\deploy\native\mods`), else the default library.

### Console commands

| Command | Who | What it does |
|---|---|---|
| `modmenu` | any player | On or off and why, the library, and per mod: `id`, `name`, `on`, `wanted`, `position`, `wanted_position`, `rebuild`, `settings`, `icon`. Also `profile`, `profile_next`, `font`, `font_next`, `symbols_full`, `settings_ui`, and the page up now (`page`, `page_mod`, `capture`). Prints e.g. "Mod menu on: 7 mods, 2 changes wait for your next start." |
| `modmenu_set <id or number> on\|off` | developer mode | Changes what the next start uses (writes pending.txt). |
| `modmenu_move <id or number> <position>` | developer mode | Moves a mod in the order for the next start. |
| `modmenu_undo` | developer mode | Undoes every change. |
| `modmenu_key <key>` | developer mode | A key for the key capture that runs now, as if pressed (`Backspace` clears, `wait` lets it run out). For scripts and the self-test. |

### How it joins the Options screen

The source is [`native/examples/modmenu`](../native/examples/modmenu/). It needs no change to Savage Wind and no
rebuild of the game code. The pages and mod info work with any modhost; the settings rows use mod API 3 (`settings_ui`).

- Savage Wind 0.4.1-alpha.4 shows its own settings as pages of the game's Options screen: the game code
  (`swgame.dll`) calls 12 `rt_opt_*` functions of `savagewind.exe` through its import table. In `swmod_init`, before
  any game thread runs, the mod puts its own functions into 11 of those import slots (`rt_opt_screen` stays) and
  forwards everything that isn't its own row to Savage Wind. So the game draws the page: font, sounds, cursor,
  scrolling and the description line are the game's.
- Two tables. While one of its pages, or Savage Wind's first page with the Mods button, is up, the game sees the mod's
  copy of Savage Wind's option entries with its own entries after them. Around every Savage Wind call that checks the
  table, Savage Wind's own table goes back in. Savage Wind's other pages keep its own table.
- The mouse: Savage Wind's pointer code calls `rt_opt_pointer_kind` inside the exe, and with the longer table in
  place it would find no row at all. The mod checks that function's first 16 bytes and replaces it with a jump to its
  own copy, which answers for both tables. So the mouse works on every row (checked in the game).
- The description line: the game writes it only when the cursor comes to a row. After a switch, a move or Y, the mod
  writes it again for the row under the cursor, with the game's own function for that (at 0x7000e30550 in 1.9.0;
  Savage Wind's later versions name it `opt_two_help` and call it the same way). The mod finds it once, when Options
  first opens, by its first 8 instructions (Savage Wind's own pattern) in the game's code: only when exactly one place
  matches. Otherwise the line changes when the cursor moves, and the log says so.
- A page change closes the screen and the title or pause menu opens it again, as Savage Wind does for its pages.
- Message ids: 90000-90004 (the Mods button and its texts), 90100 + n (the Order page's "1st"...), 91000 + 4 x row +
  part (title, line, button; 64 rows), and 92000 + 240 x slot + value for choice rows with their own value texts (32
  per page: the Mod set, the Font, a mod's sliders and choices). All stay under 99,999.
- Choice rows answer 0 to the game's decide call: the game sends left and right through it too (the early game check
  found that answering "done" froze the value).
- Key capture: while it runs the Options update is skipped, so the page takes no input. Keys are read only while the
  game's window is in front.
- Grey: titles and choice values of locked rows use the game's message colour 3. An ON/OFF row's switch itself isn't
  greyed yet (the game check found its "Disable" panes do that); the line says "Locked".
- The game's thread does no file work: a thread of the mod writes pending.txt, within about 50 ms of a change (so a
  change right before the game closes is kept).
- It turns itself off, changes nothing and logs why, unless all of these hold: the game code is for BotW 1.9.0; the
  exe holds the text `0.4.1-alpha.4`; all 11 import slots are there and still point at Savage Wind's functions;
  `rt_opt_pointer_kind` starts with the expected bytes; the game code names the Options sites (`opt_table` and three
  more, the ones Savage Wind's own Options code reads). The mod holds no game address of its own (it finds the
  description function by its bytes). It also refuses
  `modload` (the game is already running then: turn it on in Mod Manager and restart the game).
- **Later:** a small Savage Wind change, `sw_options_add_page()`, would let Savage Wind own the rows. The mod already
  uses that function when a Savage Wind has it (path B), and then touches no import slot or code. No Savage Wind has it
  yet; proposing it upstream is the owner's call.

### Limits

- Native mods load by file name, not by the order on the Order page. The order matters for RomFS files, cheats and
  patches (the later mod wins).
- The page shows the first 30 mods: the game's own pages and Savage Wind's have about 20 rows, more is untested.
- After **Y**, the rows keep a moved order on screen until you open the page again (the game sets a row's name only
  when it builds the row). Their ON/OFF and the changes are right.
- Clicking the **Back** picture at the bottom does nothing, on Savage Wind's own pages too. Press B or Esc.
- A Savage Wind update turns the page off until the mod is updated.
- Texts are English only.
- A setting that another setting hides (`show_if`) stays on the page, grey, instead of leaving it: closing and opening
  the page again works (the early game check), but the cursor would jump to the top without putting it back, and that
  needs three more game addresses in [addresses.md](addresses.md) first.
- Pictures (a mod's picture beside the list) are not drawn yet. `tools/mod_pictures.py` makes the picture sheet
  and the patched Options layout (opt-in), and `menu.txt` already says which cell is whose (`picgrid`, `pic`; the
  `modmenu` command shows each mod's `icon`). Pointing the panel at the cell needs the game's own pane functions,
  which wait for a game check.
- After **B** the cursor starts on the page's first row.

### Self-test

`python -m unittest tests.test_modmenu_native` builds modhost, the mod and a made-up Savage Wind and game library
(`native/examples/modmenu/selftest`) with the zig that ships with Savage Wind, and drives them: the Mods button above
Quit game, switching and moving mods, Y, B, the title and pause menus reopening the screen, the mouse on Savage
Wind's rows and the mod's (the fake presses rows directly, not through Savage Wind's real mouse code), a reboot,
pending.txt (also one from an older list, and quick changes right before the game closes), the stale-list row, more
mods than the page shows, path B, and each check that turns the page off. Also: the More page (a mod set and a font
picked for the next start, then used by `apply`), Mod settings and info, a mod's page with every kind of setting
(locked rows, a hidden row, a sub-page, a button, the key capture through `modmenu_key`), and settings saved to
`settings\<id>.json`.
`python -m unittest tests.test_modmenu` covers `tools/modmenu.py` with made-up libraries.

### Game test checklist

Only when you choose to; never while you play. About 15 minutes.

1. `python tools/modmenu.py setup`, then Deploy and Play in Mod Manager.
2. `savagewind.log` has `[mod] modmenu: Mods page on (N mods, path A+)` and Savage Wind's own
   `[options] Savage Wind's pages added`.
3. Pause > System > Options: **Mods** is just above **Quit game**. Every Savage Wind page still works (Video and its
   Window size list, Keyboard key capture, Language), with keys, controller and mouse.
4. Mods page: turn a mod off, B, open it again: still OFF. Y: back to ON. Order page: arrows move a mod, the other
   numbers change.
5. Mouse: click a mod's ON/OFF, click Mods, scroll a long list.
6. Title screen > Options: the same.
7. Close the game, `python tools/modmenu.py play`: the change is in, and the page shows it as it is now. The **How to
   use changes** row says to run `python tools/modmenu.py play`.
8. Language > Restart now, then Options again.
9. With no other mods on: one row "No mods yet", no crash.

`python tools/modmenu_ingame_check.py` asks the running game for the parts a script can check (step 2, and the
`modmenu` command).

### Game test results

2026-11-07, Savage Wind 0.4.1-alpha.4, BotW 1.9.0, a scratch library with Hello, Show Position, a made-up RomFS mod
(Text Note) and the Mod menu. Screenshots: `out\shots\modmenu_*.png` (first run) and `modmenu_fix_*.png` (the fixes).

| Step | Result |
|---|---|
| 2. Log line, `Mods page on (3 mods, path A+)` | PASS (after the version check fix: the real exe packs its texts without a gap) |
| 3. Mods above Quit game, pause and title menu | PASS |
| 3. Savage Wind's Video, Audio, Controls pages | PASS |
| 4. Switch, B, reopen: still OFF. Y undoes. Order page moves | PASS |
| 5. Mouse: ON/OFF, Open, order arrows, Mods button, wheel | PASS |
| Keyboard: arrows, Enter, Esc | PASS |
| Description line: fits, short words, changes at once (pad, mouse, Order page, Y) | PASS (fixed: it was cut at 82 characters and changed only when the cursor moved) |
| `{ }` and other letters the font lacks | PASS (fixed: they showed as gaps) |
| 7. Close, `modmenu.py play`: the changes are used | PASS |
| No crash | PASS |
| 3. Keyboard key capture; 8. Language > Restart now; 9. "No mods yet" | not tried yet |

## Credits

The Options plumbing follows Savage Wind Recomp's `runtime/ui/game_options.mm` (0.4.1-alpha.4, MIT License): the
option entry and row layouts, how a text is handed to the game, the reopen logic, and `rt_opt_pointer_kind`'s logic.
See [NOTICE.md](../NOTICE.md).
