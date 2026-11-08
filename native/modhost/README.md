# modhost: native code mods for Savage Wind

`modhost.dll` lets Savage Wind 0.4.1-alpha.4 load native code mods (Windows DLLs) **without changing Savage Wind**.
Start the game with `SAVAGEWIND_GAME_LIB=<path to modhost.dll>`. Savage Wind then loads modhost instead of its game
code library, and modhost loads the real one plus your mods. How to install and launch: [docs/code-mods.md](../../docs/code-mods.md).

## Files

| File | What it is |
|---|---|
| `swmod.h` | The mod API. The only header a mod needs. |
| `modhost.c` | The host: the proxy `swgame_tables`, the mod loader, the `mods` command, and `modload`, `modunload` and `modreload` (load a mod while the game runs). |
| `settings.c` | Mod settings (API 3, [docs/mod-settings.md](../../docs/mod-settings.md)): reads `settings.txt`, keeps the values, runs watchers, saves `<library>\settings\<id>.json`, and the `modsettings` / `modset` commands. It is `#include`d by `modhost.c` (compiled alone it is empty), so modhost still builds from `modhost.c`. |
| `botw.h` | Plain-named helpers for the most basic game values (BotW 1.9.0): [docs/game-basics.md](../../docs/game-basics.md). |
| `sw_runtime.h` | Savage Wind runtime declarations the host uses (copied from Savage Wind, MIT). Mods don't use it. |
| `platform_min.h` | Hand-written Win32 / C runtime declarations (the zig that ships with Savage Wind has no `windows.h` or `stdio.h`). |
| `selftest/` | A fake runtime, fake game libraries with made-up tables (`hooked/`: made-up generated C that gets hook slots), and test mods, for `build_native.py selftest`. |

The example mods are [`native/examples/hello/`](../examples/hello/hello.c), for hooks
[`native/examples/stamina_hook/`](../examples/stamina_hook/stamina_hook.c), for `hle_override` and `guest_call`
[`native/examples/ghost/`](../examples/ghost/ghost.c), and for settings
[`native/examples/settings_demo/`](../examples/settings_demo/settings_demo.c).

## Writing a mod

```c
#include "swmod.h"

static const SwModApi *api;

static void cmd_hello(int argc, char **argv, SwModReply *r) {
    (void)argc, (void)argv;
    api->reply_str(r, "hello", "world");
}

static const SwModCommand commands[] = {
    {"hello", "", "Say hello", cmd_hello, SWMOD_CMD_PLAYER},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    api->control_register(commands, 1);
    return 0;  // 0 = ok
}
```

Build it like the example (add a `Target` to `EXAMPLES` in `tools/build_native.py`), or by hand from the Savage Wind
folder, with the zig that ships with it:

```
zig\zig.exe cc -target x86_64-windows-gnu -O2 -I<repo>\native\modhost -c mymod.c -o mymod.o
zig\zig.exe lld-link @zig\rt\link.rsp -LIBPATH:zig\rt -OUT:mymod.dll mymod.o
```

Put `mymod.dll` in `<data root>\mods\native\`. Mods load in file name order. Pick a file name of your own:
`build_native.py install` won't replace a file it didn't write there (unless you add `--force`).

C++ works too: `swmod.h` declares `swmod_init` as `extern "C"`, so it is exported under its plain name. The zig that
ships with Savage Wind can't compile C++ that includes `<stddef.h>` (it lacks a C++ header), so build C++ mods with
another compiler (a full zig, clang or MSVC) or write them in C.

## What the API gives (version 3)

Version 2 added hooks and version 3 settings; mods written for an older version run unchanged.

| Field | What it does |
|---|---|
| `api_version`, `size` | The host's API version and struct size. Check with `SWMOD_API_OK(api, 1)` (or 2 for hooks, 3 for settings); for a field newer than that, `SWMOD_HAS(api, field)`. |
| `host_version`, `game_version`, `mod_name` | modhost's version, the game version the game code was built from, your file name. |
| `log(fmt, ...)` | One line in `savagewind.log`, as `[mod] ...`. |
| `control_register(cmds, n)` | Add control commands (the `SAVAGEWIND_CONTROL` socket and the in-game console). Returns how many were added. |
| `reply_str/int/num/bool/text/fail` | Answer a command. |
| `dev_mode()` | Developer mode is on (`SAVAGEWIND_CONTROL` or `SAVAGEWIND_DEV`). |
| `dispatch_lookup(addr)` | The recompiled function at a guest function entry. Works from `swmod_init` on. |
| `find_site(name)` | The address of a named site in the game code, e.g. `player_info`. |
| `hle_override(addr, fn, name)` | Replace a guest function for calls that go through the dispatcher. |
| `guest_call(cpu, addr)` | Call a guest function on a game thread. |
| `current_cpu()` | The calling game thread's `Cpu`, or NULL. |
| `hook(addr, before, after, replace)` | Version 2. Run code before, after or instead of a function of `main`, for calls from other functions (direct calls too; a function's jumps back to its own start don't run it again). Needs game code rebuilt with a hook slot for `addr`: [docs/hooks.md](../../docs/hooks.md). Returns 0 or an `SWMOD_HOOK_*` error. |
| `can_hook(addr)` | Version 2. The game code has a hook slot for `addr`. |
| `mod_id` | Version 3. The `[mod]` id of the mod.toml this DLL came with, or NULL (no settings deployed). |
| `setting_int/num/str(api, key, ...)` | Version 3. A setting's value ([docs/mod-settings.md](../../docs/mod-settings.md)); the default you pass when there is none. Any thread, no lock. `restart` and `deploy` settings give the value the game started with. |
| `setting_watch(api, fn, user)` | Version 3. `fn` runs after a `live` setting changed or a button was pressed, on the thread that changed it. From `swmod_init` only; 8 per mod. |
| `setting_set(api, key, value)` | Version 3. The mod changes its own setting (saved, shown at once; no watcher call). |
| `settings_ui()` | Version 3. The table the Mod menu uses for settings pages (`SwModSettingsUi`). |

Registers: `SWMOD_REGS(c)->x[0]`, `->sp`, and `swmod_get_s/set_s/get_d/set_d(c, n)` for `s<n>`/`d<n>`, at the
offsets of the release's `gamelib-src\cpu.h`.

Rules:

- `swmod_init` runs once, while Savage Wind loads the game code: before the game's modules are in memory and before
  any game thread starts. Register commands and overrides there.
- Load a mod while the game runs (developer mode): `modload "<path to .dll>"`, `modunload <name>`,
  `modreload <name> "<path to .dll>"`. Such a mod may export `swmod_exit(api)`, which runs before its DLL is freed.
  It can add commands and overrides only from `swmod_init` and its own commands (not from a hook or a thread of its
  own). Details and limits: [Load a mod while the game runs](../../docs/code-mods.md#load-a-mod-while-the-game-runs).
- Command tables and the strings you pass (`name` of an override too) must stay valid: make them `static`.
- A command name must be free. modhost skips (and logs) a name that Savage Wind, modhost or an earlier mod already
  has, and one Savage Wind doesn't add because its table is full (128 commands in 0.4.1-alpha.4). `mods` lists what
  each mod got and what was skipped.
- `hle_override` only catches calls through Savage Wind's dispatcher: virtual calls, function pointers and imports.
  Direct calls inside `main` are compiled to direct C calls and skip it. Use `hook` for those.
- Add hooks from `swmod_init` too. modhost logs `this game code has no hook slots; rebuild with hooks` when the
  game code wasn't built with them.
- Command handlers run on the frame thread, which is not a game thread: `current_cpu()` is NULL there.
- Guest memory is wiped when the player goes back to the start screen. Mods are not loaded again: keep no guest
  pointers across that.
- A crash in a mod is a crash of the game. Only install mods you trust: they run with your full rights.

## Credits

`sw_runtime.h` and parts of `swmod.h` mirror declarations from
[Savage Wind Recomp](https://github.com/Revivedx/SavageWindRecomp) (MIT License, Copyright (c) 2026 Revivedx and the
Savage Wind Recomp contributors; Copyright (c) 2026 Peiikai).
