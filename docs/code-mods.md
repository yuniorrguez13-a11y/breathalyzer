# Native code mods (proof of concept)

Native code mods are Windows DLLs that run inside Savage Wind. `modhost.dll` loads them into the **released** Savage
Wind 0.4.1-alpha.4 without changing it: no patched exe, no rebuilt game code. It was tested in the real game on
2026-11-06 and 07. See [What is verified](#what-is-verified).

## Quick start

```
python tools/build_native.py build      # modhost.dll and the example mods, into build\native\ (hello;
                                        # stamina_hook and ghost are built too, but not installed)
python tools/build_native.py install    # copy them into your Savage Wind folder (add --dry-run to only look)
```

`install` prints how to start the game. In PowerShell:

```
$env:SAVAGEWIND_GAME_LIB = 'C:\...\Savage Wind\extracted\modhost.dll'
$env:SAVAGEWIND_CONTROL = '7777'        # optional, for the check below
& 'C:\...\Savage Wind\savagewind.exe' --play
```

Then, while the game runs:

```
python tools/build_native.py check      # asks the game which mods loaded, and runs `hello`
```

It should print `modhost is running: 1 of 1 mod(s) loaded` and `hello -> {"ok": true, "data": {"hello": "world",
"mods_api": 2}}`. `savagewind.log` (next to the exe) has `[modhost]` lines, one per mod.

Start the game without `SAVAGEWIND_GAME_LIB` and it runs as usual, without mods. `python tools/build_native.py
uninstall` removes the files `install` copied, if nobody changed them since.

`build` uses the zig and `savagewind.dll.a` that ship with Savage Wind. Use `--install <folder>` (or set
`SWMOD_INSTALL`) if Savage Wind is not in `Documents\SavageWind\Savage Wind`, and `--data-root <folder>` if you start
it with `SAVAGEWIND_DATA_ROOT`.

## What `install` copies

| From | To |
|---|---|
| `build\native\modhost.dll` | `<data root>\extracted\modhost.dll` (next to `swgame.dll`, which is not touched) |
| `<data root>\extracted\swgame.dll.id` | `<data root>\extracted\modhost.dll.id` |
| `build\native\mods\hello.dll` | `<data root>\mods\native\hello.dll` |

The data root is the Savage Wind folder unless you use `SAVAGEWIND_DATA_ROOT`.

`install` notes each file it writes, with its SHA-256, in `<data root>\extracted\modhost.install.json`. It won't
replace a file it didn't write (say your own `mods\native\hello.dll`) unless you add `--force`. `uninstall` removes
only the files in that record that are unchanged, and lists the ones it left.

## How it works

Savage Wind compiles the game code into `extracted\swgame.dll` when you set up your game files. At startup it loads
that DLL and calls its one export, `swgame_tables()`, which returns the function tables of the recompiled game
(Savage Wind `runtime/gamelib.c`). The environment variable `SAVAGEWIND_GAME_LIB` makes it load another DLL instead.
modhost is such a DLL:

1. Savage Wind calls `modhost.dll`'s `swgame_tables()`. This happens once per run, on the main thread, after the
   control commands are set up and the working directory is the data root, and before the game's modules are loaded
   or any game thread runs (`runtime/main.c` `boot`).
2. modhost loads the real `extracted\swgame.dll` (or `SWMOD_REAL_GAME_LIB`) and calls its `swgame_tables()`.
3. It adds the control commands `mods`, `modload`, `modunload` and `modreload`, then loads every `mods\native\*.dll`
   (or the folder in `SWMOD_DIR`) in file name order and calls each one's `swmod_init(api)`. It logs one line per mod.
4. It returns the real tables unchanged. Savage Wind checks their version id as usual and runs the real game code.

The `.id` file: Savage Wind's start check reads `<library>.id` to see whether the game code matches the app
(`gamelib_present`). For a `SAVAGEWIND_GAME_LIB` library it skips that file when loading and lets the tables' own id
decide. `install` still copies `swgame.dll.id` to `modhost.dll.id` so the start check passes the same way.

If the real game code can't be loaded, modhost returns nothing and loads no mods. Savage Wind then falls back to
`extracted\swgame.dll` by itself, as if modhost weren't there. If the game code was built for another Savage Wind
version, modhost loads no mods and Savage Wind refuses it as it always would.

The mod API is [`native/modhost/swmod.h`](../native/modhost/swmod.h): logging, control commands, finding recompiled
functions and named sites, `hle_override` and `guest_call`. Mods link against nothing from Savage Wind. Only modhost
uses Savage Wind's exports, so only modhost has to be rebuilt for a new Savage Wind version. See
[`native/modhost/README.md`](../native/modhost/README.md) for writing a mod. The game addresses the example mods
use (1.9.0 only), with how each one was verified, are listed in [addresses](addresses.md).

## The `mods` command

`mods` lists what modhost loaded. It is player safe, so it also works in the in-game console (F12) without developer
mode. Over the socket:

```
{"ok":true,"data":{"host":"0.4.1","api":3,"game_version":"1.9.0","game_lib":"...\\extracted\\swgame.dll",
 "dir":"...\\mods\\native","found":1,"loaded":1,"hot":0,"hook_slots":0,"hooked":0,"mods.0.name":"hello",
 "mods.0.id":1,"mods.0.file":"hello.dll","mods.0.hot":false,"mods.0.ok":true,"mods.0.status":"ok",
 "mods.0.commands":"hello","mods.0.skipped":"","mods.0.hooks":"","mods.0.overrides":""}}
```

`hook_slots` is how many functions the game code has hook slots for (0 without a [hooks](hooks.md) rebuild),
`hooked` how many of them a mod hooked, and each mod's `hooks` the addresses it hooked. `commands` are the commands
the mod got. `skipped` are the ones it asked for but didn't get: the name was taken (by
Savage Wind, modhost or an earlier mod) or Savage Wind's command table was full. modhost finds the taken names by
running Savage Wind's own `help` (through `control_exec_line`) before and after each one. `overrides` are the
addresses it overrode with `hle_override`. `hot` (top level) counts the mods loaded while the game runs, and a mod's
`hot` is `true` for one of them (see [Load a mod while the game runs](#load-a-mod-while-the-game-runs)). `id` numbers
the mods in load order; a mod loaded again gets a new one.

A status is `ok`, `init failed (code N)`, `load failed (error N)` (a Windows error code; 193 = not a 64-bit DLL) or
`loaded, no swmod_init`.

A mod loaded at game start whose `swmod_init` fails leaves nothing running: its hooks are taken out again, and its
`hle_override` calls (held back until `swmod_init` returns 0) never reach the game. Its DLL stays loaded.

`guest_call` to address 0 (a site or pattern that wasn't found) is refused with a log line naming the mod; the call
does nothing. Savage Wind would stop the game with FATAL.

## Load a mod while the game runs

You can load a mod into the running game, change its code, and load the new build, without a restart. This needs
developer mode: start the game with `SAVAGEWIND_CONTROL` set (see Quick start). Three developer commands do it:

| Command | What it does |
|---|---|
| `modload <path to .dll>` | Loads the mod and runs its `swmod_init`. |
| `modunload <name>` | Takes out the mod's commands, hooks and overrides, runs its `swmod_exit` if it has one, and frees the DLL. |
| `modreload <name> <path to .dll>` | Checks the new file first (it is there, is a 64-bit DLL with `swmod_init`, isn't loaded by another mod; the mod's own file may be loaded again). Then `modunload <name>`, and loads the new file under the same name. If the new file is bad, the old build keeps running. If no mod has that name, it only loads. |

Put a path with spaces in double quotes: `modload "C:\my mods\mymod-1.dll"`. `tools/control.py` and Mod Studio do
that for you.

With [`tools/control.py`](../tools/control.py), from the `tools` folder:

```
from control import Control
with Control(7777) as c:
    print(c.cmd("modload", r"C:\mymod\build\mymod-1.dll"))
    print(c.cmd("modreload", "mymod", r"C:\mymod\build\mymod-2.dll"))
    print(c.cmd("modunload", "mymod"))
```

`modload` and `modreload` answer with the mod's `name`, `id`, `file`, `path`, `replaced` (an old build was unloaded
first), `commands`, `skipped`, `hooks`, `no_slot`, `overrides` and `notes`. `notes` says in plain sentences what didn't
work, one line each. When something fails, the reply is `{"ok":false,"error":"..."}` with the reason.

**Names.** A mod's name is its file name without `.dll` and without a `-<number>` ending: `mymod-3.dll` loads as
`mymod`. Windows locks a DLL file while it is loaded, so copy each new build to a new file name (`mymod-1.dll`,
`mymod-2.dll`, ...) and load that. The name stays `mymod`. Loading a file that is already loaded is refused.

**Hooks need hook slots.** A hook only runs if the game code was rebuilt with a slot for that function (see
[hooks](hooks.md)). Without one, the mod still loads, `no_slot` lists the addresses, and `notes` says that the
function needs a rebuild of the game code.

**What unloading does.**

- Hooks are taken out. modhost then waits until no game thread still runs one of the mod's hooks or overrides. It
  waits up to 1 second (set `SWMOD_UNLOAD_WAIT_MS` to change it). If a thread is still inside, `modunload` puts
  everything back and refuses with "...restart the game to replace this mod". The mod keeps working. Try again a
  moment later.
- Commands: Savage Wind can't remove a command, so the name stays in `help`. While the mod is unloaded it answers
  "not loaded any more". Loading the mod again brings it back.
- `hle_override`: Savage Wind can't remove an override either. modhost puts in its own stand-in, once per address.
  After the unload it runs the game's own function again.
- Then modhost calls the mod's `swmod_exit(api)`, if it exports one, and frees the DLL.
- If `swmod_init` fails (returns nonzero), modhost unloads the mod again at once and says why. `swmod_exit` is not
  called then. If a game thread is still in one of its new hooks, it can't be freed yet: its hooks, overrides and
  commands are switched off anyway (status `init failed (code N), switched off, still loaded`); `modunload` frees it
  later.
- A thread that saw the mod's hooks just before the unload, and gets to them only after it, runs the game's own
  function: it never reaches the freed DLL.

**Add things from `swmod_init` or a command only.** A mod loaded with `modload` can add commands and overrides
(`control_register`, `hle_override`) only from its `swmod_init` and its own commands. From a hook, a thread of its own
or `swmod_exit` they are refused, with a `[modhost] ... refused` line in `savagewind.log`: they would stay in Savage
Wind after the DLL is freed. Hooks (`api->hook`) only work from `swmod_init`, as always.
- Mods loaded at game start (from `mods\native`) are never unloaded. `modunload` says "restart the game to replace
  this mod".

`mods` lists the loaded mods with `"hot": true`.

**swmod_exit.** Optional. Stop any thread your mod started, and wait for it. No code of the mod may run after
`swmod_exit` returns.

```
SWMOD_EXPORT void swmod_exit(const SwModApi *api) {
    // stop your threads, free what you allocated, undo memory you changed
}
```

**Limits.**

- Tried in the real game with mods that add commands (2026-11-07, see [What is verified](#what-is-verified)). Hooks
  and `hle_override` from a mod loaded while the game runs have only run in the selftest so far.
- An `hle_override` from a mod loaded while the game runs only reaches game threads that haven't called that address
  yet, because each thread remembers where a call goes. Hooks don't have this problem. An address that a mod loaded at
  game start overrides can't be overridden again.
- Game memory the mod changed stays changed after the unload. Undo it in `swmod_exit` if you need to.
- If a game thread leaves a hooked function abnormally (the game goes back to the start screen, or the thread ends
  inside it), that hook can stay "in use". Unloading that mod is then refused until you restart the game.
- modhost waits on the thread that also shows the frames. An `after` or `replace` hook on a function that waits for
  the next frame (a frame-level job) is "in use" for as long as modhost waits, so that mod can't be unloaded: restart
  the game to replace it. `before` hooks don't have this problem, and loading never waits.
- Per game run: at most 64 mods in all, 64 command names and 32 overridden addresses from mods loaded while it runs.
- A command keeps the "player safe" flag it had when its name was first added. Developer mode runs every command
  anyway.

## What is verified

In the real game (Savage Wind 0.4.1-alpha.4, BotW 1.9.0):

- 2026-11-06: the release `savagewind.exe` loads `modhost.dll` through `SAVAGEWIND_GAME_LIB`, modhost loads the real
  `swgame.dll` and `mods\native\hello.dll`, `savagewind.log` has the `[modhost]` lines, `mods` and `hello` answer
  over the control socket, and the game plays normally.
- 2026-11-06: modhost 0.2.0 (API 2) with the [hooks](hooks.md) example: `mods` showed `hook_slots` 1 and `hooked` 1.
- 2026-11-07: the [ghost](ghost.md) mod uses `dispatch_lookup`, `hle_override` (on a per-frame job of the game) and
  `guest_call` on the real game code.
- 2026-11-07: `modman run` with three native mods (hello, stamina_hook, ghost): all three loaded.
- 2026-11-07: `modload`, `modreload` and `modunload` while the game ran (modhost 0.3.0, test save on the Great
  Plateau). `modload` loaded the [basics](game-basics.md) examples show position and teleport up: `where` answered the
  same x y z as `pos`, and `up 10` moved Link from height 249.0 to 259.0 (he fell back down). `modreload` put a changed
  build of show position in place (`replaced: true`; the new build's answer and log line). After `modunload`, `up`
  said "not loaded any more", and the same file loaded again. `mods` listed the loaded mods with `hot: true`.
  Refused, with the reason: `modunload` of a mod loaded at game start, `modload` of a second build under a name that
  is loaded, a missing file and a file that isn't a DLL. Mod Studio's build and F6 did the same for a new mod
  ([mod studio](mod-studio.md)). No crash in two runs; the game closed normally with `exit`.
- 2026-11-07: `find_site` on the real game code: a mod loaded with `modload` found the sites `world_mgr` and
  `pause_state` and read the hour and the pause state through them (`botw_time_of_day`, `botw_paused` in
  [game basics](game-basics.md)).

Checked on this PC without the game:

- `build` compiles and links `modhost.dll` and `hello.dll` with the zig in the 0.4.1-alpha.4 release (zig 0.17.0),
  with no warnings. `modhost.dll` exports `swgame_tables` and imports 14 functions from `savagewind.exe`:
  `control_console_free`, `control_enabled`, `control_exec_line`, `control_fail`, `control_field_bool`,
  `control_field_int`, `control_field_num`, `control_field_str`, `control_register`, `control_text`, `gamelib_id`,
  `guest_call`, `hle_override`, `kernel_current_cpu`. All 14 are in the release exe's export table. `hello.dll`
  imports nothing from Savage Wind.
- A C++ mod that includes `swmod.h` exports plain `swmod_init` (built with stand-in `<stddef.h>` headers, since the
  shipped zig can't compile C++ that includes the real one).
- `modhost.dll` and `savagewind.exe` use the same C runtime (UCRT), so modhost's log lines go to the stream that
  Savage Wind redirects into `savagewind.log`.
- The loading order, the `SAVAGEWIND_GAME_LIB` path, the `.id` handling and the fallback were read from the release
  source (commit 76e61af: `runtime/gamelib.c`, `runtime/main.c`, `runtime/paths.c`).
- `python tools/build_native.py selftest` builds a fake `savagewind.exe` (a DLL with the same exports), a fake game
  library with made-up tables and test mods, and drives modhost through them with Python. It checks: the real tables
  are returned, mods load in name order, `hello` answers, and a test mod calls every API function and the fake
  runtime gets the right thing (log lines, reply fields and text, `guest_call`'s Cpu and address, `hle_override`,
  `host_version`, the version macros). It also checks that taken command names and a full command table are skipped
  and not counted, that broken mods (init fails, not a DLL, no `swmod_init`, a bad command entry) are reported
  without stopping the others, and the two fallbacks.
- The same selftest loads a test mod while the fake game "runs" (2026-11-07, modhost 0.3.0). `modload` loads it, and
  its command, its hooks (after those of a mod loaded at game start) and its override run. While a thread of the mod
  waits inside its hook, `modunload` refuses, and the mod keeps working. `modreload` puts a changed build in its place:
  the old hooks are gone, a new command works, the override calls the new code, and the old build's `swmod_exit` ran.
  A mod whose `swmod_init` fails is unloaded again, without `swmod_exit`. A mod loaded at game start can't be
  unloaded. After `modunload`, the mod's command says it is gone, and the game's own function runs again. On game
  code without hook slots, `modload` says that the hooks need a rebuild.
- The unit tests (`tests/test_build_native.py`) cover the build commands, install and uninstall with the install
  record (a user's own file is never replaced or removed), the PE reader, the selftest checks and `check`,
  with made-up files only.

Not verified yet (needs the real game):

- `mods` and `hello` in the in-game console (F12).
- A command name that is already taken, against the real `help` (its output format was read from the release
  `runtime/control.c`).
- Going back to the start screen and playing again with modhost loaded.
- Hooks and overrides of a mod loaded with `modload` in the real game (only the selftest so far).

## Limits

- `hle_override` only catches calls that go through Savage Wind's dispatcher (virtual calls, function pointers,
  imports). Direct calls inside `main` can't be hooked this way.
- There is no per-frame or "game ready" callback yet. Mods act from `swmod_init`, from their commands, or from
  overridden functions.
- modhost uses Savage Wind internals that are not a stable API. It is built for 0.4.1-alpha.4; check
  `native/modhost/sw_runtime.h` against the source of any other version before using it there.
- A mod runs with your full rights, and a crash in a mod crashes the game. Only install mods you trust.

## Hooks

Since mod API 2, mods can also hook any game function (before, after or instead of it), including ones
called directly. That needs game code rebuilt with hook slots. See [hooks](hooks.md). Tested in game on 2026-11-06.

## Settings

Since mod API 3 (modhost 0.4.0), a mod can have a settings page: it lists `[[settings]]` in its `mod.toml`, and the
player changes them in the game's Mod menu or in Mod Manager. The DLL reads them with `api->setting_int`,
`setting_num` and `setting_str` (any thread, no lock), and `api->setting_watch` tells it when a `live` setting
changed. Everything about them is in [mod settings](mod-settings.md); the example is
[`native/examples/settings_demo`](../native/examples/settings_demo/settings_demo.c).

- Mods built for API 1 or 2 run unchanged; a mod that wants settings checks `SWMOD_HAS(api, setting_int)` and uses
  its defaults on an older modhost.
- modhost reads `deploy\native\settings.txt` (next to its mods folder; `SWMOD_SETTINGS` to point elsewhere) before any
  mod starts, and finds each DLL's settings by its file name. Without the file, every mod gets its defaults and
  `api->mod_id` is NULL.
- Two more commands: `modsettings [<mod>]` (any player) lists the settings, and `modset <mod> <key> [<value>]`
  (developer mode) changes one as the Mod menu does.
- Checked without the game (`tests/test_mod_settings.py`, the fake game of `build_native.py selftest`): the getters,
  watchers, locks, presets, the values file, a mod loaded with `modload`, and Python and C agreeing on 280 condition
  cases. In the game (2026-11-18, Mods page v2, commit 65003e3): the settings pages and every row type, live; see
  [mod settings](mod-settings.md#what-is-verified) for what is still open.

## File log

modhost can list every file the game opens, one line each, in `fslog.log`. It is for finding out which files a change
needs (the first step of the Linkle live swap). It only writes the list: every open goes on to the game as it came,
and nothing is redirected. **OFF by default.**

- **Turn it on:** Breathalyzer, Settings, File log: Turn on. The row is there while Developer commands is ON (or the
  file log is). It works from the next Play, when at least one code mod is ON (modhost only runs then; the game's Mods
  page is one). Without Breathalyzer, set `SWMOD_FILE_LOG=1` before `modman run`, or before you start the game with
  modhost.
- **The file:** `fslog.log` in the Savage Wind folder, next to `savagewind.log`. The game before keeps its list as
  `fslog.prev.log`.
- **In the game (F12):** `fslog` shows the counts, `fslog off` pauses the list, `fslog on` goes on, and
  `fslog mark <words>` adds a line of your own (`fslog mark fast travel`), so a moment is easy to find later. The
  command is only there while the file log is on. Any player can use it.
- **What it wraps:** the four file opens of the game's file devices (sead `doOpen_`: `0x7000e8fae0`, `0x7000e92690`,
  `0x700115cba0`, `0x7000eaba98`, in [addresses](addresses.md); `BOTW_ADDR_FILE_OPEN` to `_4` in `botw.h`), with
  `hle_override`, after the mods loaded. A mod that overrides one of them at game start keeps it (the log skips that
  one and says so); a mod loaded with `modload` can't override them while the log is on. It needs a `modhost.dll`
  built after 2026-11-21 (`python tools/build_native.py build`, then Play).

A line, tab separated: `time thread fn mode result ms device path note`. `time` is seconds since the log started;
`fn` the end of the open's address (`e8fae0`); `mode` w3; `result` `ok` (the device came back), `fail` (0) or
`ret=<hex>`; `ms` how long the open took; `device` the device's class (its vtable); `path` the path in x2, cut at 255
bytes, `(no path)` or `(unreadable)`. Lines that start with `#` are notes (start, pause, marks, limits, the end).

Limits, so a storm of opens can't fill the disk or slow the game:

- The same open (function, mode, result and path) gets one line a second. The others in that second become one
  `+N more until <time>` line, with the time of the first, written when the second is over.
- 1000 lines a second at most (`SWMOD_FILE_LOG_RATE`); one note counts the rest. (Threads can finish out of order:
  a long open that started in an earlier second counts in the newer one, so the count never goes back.)
- 20 MB in all (`SWMOD_FILE_LOG_MAX`, in bytes), then a note says it stopped.
- Game threads only add lines to memory; a thread of modhost writes them to the file every 250 ms. What is left is
  written when the game closes, and `fslog` writes it at once.

Checked without the game: `python tools/build_native.py selftest` wraps the four opens of its fake game and checks
the lines, the repeats, the pause, the marks, both limits, paths that can't be read (also pointers into memory Windows
says can't be read: not committed, no access, a guard page, and a path that runs into such a page, cut with `...`) and
the previous log. A second run checks the overlap rules: a mod that overrides one of the four at game start keeps it,
and `modload` is refused one the log wraps. Not run in the game yet.
