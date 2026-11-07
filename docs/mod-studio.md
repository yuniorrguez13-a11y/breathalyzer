# Breathalyzer Mod Studio

Mod Studio is Breathalyzer's small window for making native mods in C. You type the code, it suggests the game's
names as you type, F5 builds the mod, and F6 puts it into the running game. Big text, high contrast, and every part
works with the keyboard.

```
python tools/mod_studio.py
python tools/mod_studio.py --dark --size 22
```

- **Suggestions while you type.** Type 2 letters of a name, or `botw_`, `BOTW_` or `api->`, and a list opens right
  under the cursor, like Minecraft's command suggestions: a dark box, and the picked name on a yellow bar with black
  text and a `>` in front.
- **Plain-word docs.** The line under the editor says what the picked name is, its address, and whether it was
  checked in the game.
- **Find game value (Ctrl+F1).** Searches every name, also your local code map when you have one.
- **F5 builds** at low priority, so a running game keeps its frame rate. Errors are explained in plain words and
  their lines are marked.
- **F6 loads the mod into the game** while it runs. When the game isn't running, the mod goes into the mod
  manager's library instead, so it loads next time.

## Start

| Option | What it does |
|---|---|
| `--dir <folder>` | Where your mods' sources go. Default: `Documents\SavageWind\mods-src` in your user folder. |
| `--size <points>` | Text size, 10 to 40. Default 18. Ctrl + and Ctrl - change it in the window. |
| `--dark` | Light text on a dark background. |
| `--port <number>` | The game's control port (`SAVAGEWIND_CONTROL`). Default 7777. |
| `--install <folder>` | Your Savage Wind folder (Mod Studio builds with its zig). Default: as `build_native.py`. |
| `--library <folder>` | The mod manager's library. Default: as `modman.py`. |
| `--codemap <folder>` | Your local code map. Default: `out\codemap` in this repo, when it is there. |
| `--no-codemap` | Don't read the code map. |

Mod Studio opens the mod you had open last time.

## Make a mod

Press **Ctrl+N** (or **New mod**). Type a name: small letters, digits and `_`, starting with a letter. The name is
also the mod's console command, so names the game or modhost already use for a command (`mods`, `help`, `pos`, ...)
and names Windows keeps for itself (`con`, `nul`, `com1`, ...) are refused, with the reason. Pick what to start from,
then press Enter.

| Template | What you get |
|---|---|
| Empty | Only `swmod_init`. Start from nothing. |
| Show position | A command that says where Link is (`botw_player_pos`). |
| Stamina | A command that says Link's stamina, or sets it: `<name> 1000` (1000 = one wheel; more wheels hold more). |
| Command | A console command that adds two numbers: a start for your own commands. |

Each mod gets its own folder: `mods-src\<name>\` holds `<name>.c`, `mod.toml` and, once built, `<name>.dll`.
**Ctrl+O** opens another mod. Mod Studio saves your code when you build, switch mods or close the window. Ctrl+S saves
at any time. Ctrl+Z undoes. If the file can't be written (it is read-only, or another program holds it), your text
stays in the editor and the status line says why: another mod doesn't open, and the window's X keeps the window open
the first time (press it again to close without saving). New mod and Open wait while a build or a load runs.

## Type C with help

| Key | What it does |
|---|---|
| (typing) | The list opens by itself after 2 letters of a name, or after `botw_`, `BOTW_` or `api->`. It follows what you type. |
| Ctrl+Space | Opens the list now (with nothing typed: the helpers of `botw.h` first). |
| Up, Down | Pick a name. Page Up and Page Down jump. With big text the list shows fewer rows (as many as fit in the editor) and scrolls with the picked one. |
| Tab or Enter | Put the picked name in. A function comes with its arguments, and the first one to change is selected: just type over it. |
| Esc | Close the list. |
| Ctrl+F1 | Find game value: search every name, also the code map. It starts from the name you were typing, and the name you pick takes its place. |
| Ctrl+Tab | Leave the editor (Ctrl+Shift+Tab goes back). Tab in the editor puts in 4 spaces. |

The list doesn't open inside comments (also `/* ... */` over several lines), inside "text", on `#include` lines, in a
number (`0x7001e13ed0`), after the `->` or `.` of anything but `api`, or for words C has itself (`int`, `return`, ...).
It closes when you press F5, F6, F8, Ctrl+S, Ctrl+N, Ctrl+O or Ctrl+F1, open a dialog, click, or leave the editor, so
a later Enter never puts an old name in. Tab and Enter only put a name in while the cursor is still at the name the
list was opened for.

The line under the editor shows the picked name: first what to type (its arguments), then what it is, its address,
and how far you can trust it:

| It says | Meaning |
|---|---|
| checked in game | A `botw.h` helper or address built only on values that were checked in the game. |
| not tested in game | A `botw.h` helper or value nobody has checked in the game yet (shown in the warning colour). |
| mod API (swmod.h) | Part of the mod API: `api->...`, `SWMOD_...`, `SwMod...`. |
| docs/addresses.md: used in a mod / verified / research only | A game address from the [address table](addresses.md), with its status there. A `botw.h` name whose table row is research only says so too (warning colour). |
| code map: raw game names, not tested | A name from your local code map. These come straight from the game files and nobody checked them. |

Where the names come from:

- `native/modhost/botw.h`: the basic game values with our own plain names, like `botw_player_pos`,
  `botw_stamina_get`, `botw_stamina_set` and `BOTW_ADDR_PLAYER_INFO`. Its docs: [game-basics.md](game-basics.md).
- `native/modhost/swmod.h`: the mod API ([code-mods.md](code-mods.md), [hooks.md](hooks.md)).
- `docs/addresses.md`: every game address the repo documents. Rows that `botw.h` already names show up under the
  `botw.h` name. The others get names like `addr_request_actor_spawn` and `off_actor_spawner_byte`; putting one in
  writes its number.
- Your local code map (optional): `out\codemap\functions.csv`, made by `python tools/codemap.py <path to main>` from
  your own game files ([code-map.md](code-map.md)). Putting one in writes its address with the name in a comment.
  It stays on your PC: never share it or put it in the repo.

To try the search without the window: `python tools/mod_symbols.py search stamina`.

## Build (F5)

F5 saves and builds the mod into a DLL with the zig that ships with Savage Wind, with the same flags as
`tools/build_native.py`. The compiler runs at below-normal priority, so a running game keeps its frame rate. One mod
takes about 0.6 s.

- **When it works:** the status line says so, `<name>.dll` is in the mod's folder, and `mod.toml` is written for the
  mod manager.
- **When it fails:** the status line says what is wrong in plain words, with the line number (for a misspelled name
  also "Did you mean ...?"). The lines with errors are marked red, warnings yellow, and the cursor jumps to the first
  one. F8 goes to the next one. An error inside a header that your code caused (a `#define` of yours that clashes with
  a name in it, say) is put on your line: the `#define`, else the `#include`.

`mod.toml` follows the code: Mod Studio writes it at each build. It names the DLL, sets `game_versions = ["1.9.0"]`
when the code uses `botw.h` names or game addresses, and lists the functions the mod hooks (`api->hook(...)` with a
number, a `#define` of the mod, or a `BOTW_ADDR_` name). Change only the version there, like `1.1` or `1.1-beta`: the
build warns when the mod manager wouldn't take it (`1.1 beta`, with a space, is refused).

## Load into the game (F6)

F6 builds first when the code changed, then:

- **The game runs and answers on its control port:** Mod Studio copies the DLL to a new name in `.build\<name>\hot\`
  (the game keeps a loaded DLL locked; modhost still calls `<name>-3.dll` just `<name>`) and loads it with the
  `modload` command; the next F6 replaces it with `modreload`. The status line shows the game's answer. Your mod's
  commands work right away. If the game refuses ("restart the game to replace this mod"), close the game and press
  F6 again. That also happens when the mod came from the mod library when the game started: turn it off in the mod
  manager if you want to load it with F6 while you play.
- **The game isn't running:** the mod goes into the mod manager's library (it replaces the copy that was there, at
  the same place in the list, on or off as before). In the mod manager press **Deploy**, then **Play**: it loads with
  the game. Mod Studio checks the new copy's `mod.toml` first: if the mod manager would refuse it, the library stays
  as it was. If the mod manager still refuses the new copy, the old one is put back.

Tried in the real game on 2026-11-07: a new mod from the Show position template built in 1.4 s while the game ran,
F6 loaded it (`modload`) and its command answered Link's position at once. After a change to the code, F6 replaced it
(`modreload`), and the new build answered. F6 from a fresh start (no name remembered) found the loaded mod with `mods`
and replaced it too.

To load mods while you play, the game needs its control port and a modhost with the `modload` command. Start it with
`python tools/modman.py run --port 7777`, or start the mod manager with `python tools/modman_gui.py --port 7777` and
press Play. If the game runs without the port, Mod Studio says so and puts the mod into the library.

If another program listens on the port: when it doesn't answer within 2 seconds, Mod Studio says so (wait if the game
is loading, or use another `--port`); when it answers something that isn't the game, the mod goes into the library
and Mod Studio says to use another port. A build or load that ends after you opened another mod still goes to the mod
it was for.

**Hooks.** A mod that hooks a game function needs game code built with a hook slot for it ([hooks.md](hooks.md)).
Mod Studio checks: while the game runs, whether its game code has hook slots; otherwise, whether the last deploy
built one for that function. When a slot is missing it says a rebuild is needed: press Deploy in the mod manager
(10-20 minutes). Mod Studio never starts a rebuild itself; do it when you aren't playing.

## Speed

Measured on this laptop (the tests print them):

| What | Time |
|---|---|
| A search over 70,000 names (made up, shaped like a real code map), one word or several | median 0.6 ms, slowest 3.8 ms |
| A search over the real code map (65,000 names), several words with a common one (`vf_ qj`) | about 1 ms (35 ms before the fix) |
| Key press to suggestion list, with 70,000 names | median 0.8 ms, 9 in 10 under 4 ms, slowest 4.8 ms |
| Reading the names (in the background) | about 0.1 s from the cache, plus 0.2-0.3 s for the index; 0.6 s the first time |
| Building one mod | about 0.6 s |

While the names load, the key line at the bottom says so: the window can pause for up to about a tenth of a second
then, once. The search never takes more than about 4 ms: it stops looking in the code map then, so a rare query may
show fewer code map names.

The search tries name starts first (also the start of each word in a name, so `stam` finds `botw_stamina_get`),
then names that contain the text (with several words, it looks for the rarest one first), then names that have its
letters in order (`plyrpos` finds `botw_player_pos`). The documented names come before the code map's.

## Files

| Where | What |
|---|---|
| `mods-src\<name>\` | Your mod: `<name>.c`, `mod.toml`, `<name>.dll`. |
| `mods-src\.build\` | Build files: objects, zig's cache, the copies loaded into the game, and the copy for the mod manager. Safe to delete. |
| `%LOCALAPPDATA%\botw-mod-loader\mod-studio-catalog.json` | The names, cached until one of their files changes. It holds the code map's names when you have one: it stays on your PC. |
| `%LOCALAPPDATA%\botw-mod-loader\mod-studio.json` | The mod you had open last. |

## Tests

```
python -m unittest tests.test_mod_symbols tests.test_mod_studio
```

They use made-up headers, a made-up address table and code map, a made-up game on 127.0.0.1 for F6 and a made-up
mod manager library. Nothing is compiled (the compiler is replaced) and no game files are read. The window test
runs in a hidden window and is skipped without a display.
