# Hooks for native mods

A native mod (a DLL that [modhost](code-mods.md) loads) can run its own code **before**, **after** or **instead of**
any recompiled function of the game's `main`. That includes functions the game calls directly, which
`hle_override` can't catch. Savage Wind itself is not changed. You only rebuild the game code with **hook slots**
for the functions your mods hook.

## Try it: infinite stamina

The example mod [`native/examples/stamina_hook`](../native/examples/stamina_hook/stamina_hook.c) hooks the stamina
setter of BotW 1.9.0 and keeps stamina from going down.

```
python tools/build_native.py build
python tools/sw_rebuild.py build --hooks native\examples\stamina_hook\hooks.txt --out out\rebuild-hooks -j 6
```

The rebuild regenerates and recompiles `main` only (about 10 to 15 minutes; `sdk` and `subsdk0` come from the cache
when an earlier build made them). Hooks only run when modhost loads the hooked library, so don't start the game with
`SAVAGEWIND_GAME_LIB` pointing at the library itself. Start it in PowerShell, from the repo folder:

```
$env:SAVAGEWIND_GAME_LIB = "$PWD\build\native\modhost.dll"
$env:SWMOD_REAL_GAME_LIB = "$PWD\out\rebuild-hooks\swgame.dll"
$env:SWMOD_DIR = "$PWD\build\native\hook-mods"
$env:SAVAGEWIND_CONTROL = '7777'
& "$env:USERPROFILE\Documents\SavageWind\Savage Wind\savagewind.exe" --play
```

`savagewind.log` should show `[modhost] game code has 1 hook slot` and
`[modhost] mod stamina_hook.dll: ok, commands: stamina_hook, hooks: 0x7000cc7410`. Sprint: the stamina wheel
stays full. The command `stamina_hook` (over the control socket) says how often the setter ran and how often the hook
kept the old value; `stamina_hook off` and `stamina_hook on` switch it. `mods` now reports `"host":"0.3.0","api":2`,
`hook_slots` and `hooked` (functions with a hook), and each mod's `hooks`.

The library in `out\rebuild-hooks` was built from `extracted\exefs`, so it matches the data the game loads. Without
the three `SWMOD_`/`SAVAGEWIND_GAME_LIB` variables the game runs as usual.

## Writing a hook

```c
#include "swmod.h"

static bool before_set_stamina(struct Cpu *c) {
    float next = swmod_get_s(c, 0);                    // the first float argument (s0)
    if (next < 50.0f) swmod_set_s(c, 0, 50.0f);        // change it
    return false;                                      // true = skip the game's function
}

SWMOD_EXPORT int swmod_init(const SwModApi *api) {
    if (!SWMOD_API_OK(api, 2)) return 1;               // hooks need mod API 2
    return api->hook(0x7000cc7410, before_set_stamina, NULL, NULL);  // 0 = ok
}
```

`api->hook(addr, before, after, replace)` takes any of the three (NULL for the rest):

| Hook | Runs | Notes |
|---|---|---|
| `bool before(Cpu *c)` | before the function | Arguments are in `x0`-`x7` and `v0`-`v7`. Return `true` to skip the function (and the after hooks); then put its result in `x0` or `v0` yourself. |
| `void after(Cpu *c)` | after it returned | The result is in `x0` (or `v0` for a float). |
| `void replace(Cpu *c, orig)` | instead of it | `orig(c)` runs the game's function; call it as often as you like, or not at all. |

- Call `hook` from `swmod_init`. Later calls fail with `SWMOD_HOOK_LATE`.
- Several mods can hook the same function. Their before and after hooks run in mod load order (file names). Only
  one mod can replace a function (`SWMOD_HOOK_TAKEN` for the next).
- `api->can_hook(addr)` says whether the game code has a slot for `addr`.
- Errors: `SWMOD_HOOK_NO_SLOTS` (the game code was built without hooks; the log says
  `this game code has no hook slots; rebuild with hooks`), `SWMOD_HOOK_NO_SLOT` (no slot for that address: add it
  to the list and rebuild), `SWMOD_HOOK_BAD_ARGS`, `SWMOD_HOOK_FULL` (8 before and 8 after hooks per function,
  256 hooked functions). modhost logs each one.
- Registers: `SWMOD_REGS(c)->x[n]`, `->sp`, and `swmod_get_s/set_s/get_d/set_d(c, n)` for float registers. Guest
  pointers are host pointers, so `x0 + 0xa4` can be read directly.
- Mods built for API 1 keep working unchanged on this modhost.

## Declaring the hooks: the hooks list

The game code only has slots for the addresses in the hooks list given to the rebuild. Ship one with your mod:

```
# hooks.txt: one address per line; # starts a comment
0x7000cc7410        # stamina setter (1.9.0)
main+0xcc7410       # the same, as an offset into main
```

JSON works too: `["0x7000cc7410"]` or `{"hooks": [{"addr": "0x7000cc7410", "name": "stamina setter"}]}`.
`--hooks` can be given several times; the rebuild hooks the union. Addresses are guest addresses of `main` for the
game version you build for.

## How it works

Savage Wind's generator turns each guest function into a C function `f_<addr>(Cpu *c)` and compiles a direct call
or jump as a plain C call by that name. Its function table, which the dispatcher uses for indirect calls, lists the
same name. With `--hooks`, `tools/gen_hooks.py` edits the generated C before it is compiled:

1. It checks every address against the function table. An address that isn't the start of a recompiled function is
   refused, and the build stops with a message such as
   `0x7000cc7414 is not the start of a function (it is 0x4 bytes into f_7000cc7410)`.
2. It renames each hooked definition to `f_<addr>__orig`.
3. It writes `main_hooks.c`: the slot table, the export `swmod_hooks()`, and for each function a new `f_<addr>` that
   calls the slot's hook (if a mod set one) and otherwise jumps straight to `f_<addr>__orig`.

So every call from another function ends up in the wrapper: direct calls from any file, tail jumps, and the
dispatcher. (A function's jumps back to its own start are the exception: see Limits.) modhost reads the slots through
`swmod_hooks()` right after it loads the game code, before any game code runs. A function nobody hooks costs one load
and one test per call.

The hooks list is part of the rebuild's cache key. A build without `--hooks` is exactly as before. For the generated
files themselves: `python tools/gen_hooks.py check|apply <folder> <hooks file>` and
`python tools/gen_hooks.py remove <folder>`.

## Limits

- Only whole functions of `main` can be hooked, at their start. A point inside a function can't be. Code the
  compiler or the game's own build put inline into another function is part of that function, so it isn't hooked
  either.
- A function that jumps back to its own start (a loop that begins at its first instruction, or a call to itself as
  its last step) does that inside its own code, not through the wrapper. Its hooks run once, for the outer call:
  before hooks don't see the arguments of the later rounds, and `replace` covers only the outer call. When the
  rebuild generates `main`, it names such functions (`Note: f_... jumps back to its own start`). In 1.9.0, 34 of
  125,144 functions do this; the
  stamina setter doesn't.
- Each new hook address needs a rebuild of `main` (cached afterwards). A hooked library is built from one exact
  `main`, so code patches (IPS/pchtxt) and hooks are built together, from the same `--exefs`.
- Hooks run on game threads, often many times per frame. Keep them short, and don't call the hooked function
  (through `guest_call` or the dispatcher) from its own before or after hook: that runs the hook again. Inside
  `replace`, call `orig` instead.
- Change only argument and result registers. The game expects `x19`-`x29`, `sp` and `v8`-`v15` unchanged after a
  call.
- A mod loaded when the game starts adds its hooks once, from `swmod_init`, and they stay: to switch one off, keep
  a flag in your mod (like `stamina_hook off`). Its hooks and commands stay even when its `swmod_init` then fails.
  A mod loaded with `modload` while the game runs is different: `modunload` takes its hooks out again, and so does
  a failed `swmod_init`. After and replace hooks on a function that waits for a frame can keep `modunload` from
  ever finishing; restart the game then. See [Load a mod while the game runs](code-mods.md#load-a-mod-while-the-game-runs).
- `after` and `replace` hooks add a stack frame where the game used a tail jump. The game threads' stacks are large,
  so this only matters for a function the game reaches in a very long chain of tail jumps.
- Addresses are for one game version. The example is for 1.9.0 and refuses other versions.
- The game code is rebuilt from your own game files and stays under `out\`. Never commit it.
