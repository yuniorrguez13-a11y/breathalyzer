# Code map

`tools/codemap.py` names functions in **your own** copy of BotW 1.9.0's `main`. It uses only data inside that
file, so this project never ships a list of Nintendo addresses: each player makes their own.

**Never share or commit the output.** It is made from your game files. Keep it in `out/`, which git ignores.
The tool won't write anywhere else inside this repo, however the path is written (`\\?\C:\...`, a share, a
volume path).

## Make one

```
python tools/codemap.py "<your dump>\exefs\main"
```

It takes a few seconds. Output goes to `out/codemap/` (change it with `--out DIR`):

| File | What's in it |
|---|---|
| `functions.csv` | `address, name, kind, notes`, one row per address and kind, sorted by address |
| `classes.csv` | `class, typeinfo, vtables, bases, virtuals, rtti_kind, mangled` |
| `summary.txt` | the counts |

The tool prints counts only, never code.

## What the names mean

- **Addresses** are guest addresses: `main` starts at `0x7000000000`, as in Savage Wind and on the Switch.
- **`Class::vf_5`** is the function in slot 5 of `Class`'s vtable. Slots count from 0, starting at vtable + 0x10.
  An object's vptr (its first 8 bytes) points at vtable + 0x10.
- **`Class::vf_2@0x10`** is slot 2 of the vtable for the base-class part at offset 0x10 (multiple inheritance).
- **`vt_7001234560`** is a class without RTTI, named after its vtable's address. Most game code (ksys, uking, sead)
  was built without RTTI, so most classes look like this. Only the SDK's own libraries (`nn::`) carry real names.
  These count only where vtables live (the read-only data before `.dynamic`) and only when code uses them. Other
  tables of function pointers are left out: the startup and shutdown lists (`.init_array`, `.fini_array`) and
  structs in writable data.
- **One function, many slots:** a function used by several vtables (inherited, or identical code merged) gets one
  row. The name is the base-most class's slot; `notes` lists the other names (up to 50, then "+N more").

| kind | Where the name comes from |
|---|---|
| `vfunc` | a vtable slot, as above |
| `export` | `main`'s own dynamic symbols (demangled; the mangled name is in `notes`) |
| `import` | a function `main` calls in the SDK, at its PLT stub (the address `main`'s code calls) |
| `site` | a Savage Wind site name from its 0.4.1-alpha.4 `tools/game_sites.py` (only for a 1.9.0 `main`) |

Site rows are read from the local clone in `upstream/SavageWindRecomp`. Only the 1.9.0 table is used, and only
when `main`'s build id starts with `cd57b23fa4bbad65`; any other `main` (1.8.2 too) gets no site rows. `notes` says
what the site is: a function entry (`call`), an instruction (`code`; "here also a function entry" when a function
is known to start there), or a data address (`ref`, `got`). Use `--no-sites` to leave them out, or `--sites FILE`
to read another copy of `game_sites.py`. The summary then credits that file instead of the release.

## Why vtables matter for mods

In Savage Wind, calls through a vtable go through its dispatcher (`rt_dispatch`), so a mod can wrap a `vfunc` at
runtime with `hle_override`. Direct calls (`bl`) inside `main` skip the dispatcher and can't be wrapped that way.
Only `vfunc` rows are reached through vtables; the startup constructors in `.init_array` are not, and the map does
not list them as `vfunc`.
