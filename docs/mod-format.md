# Mod format: mod.toml

A mod is a folder or `.zip`. Mods don't need a `mod.toml`: modman finds RomFS files, ExeFS patches, cheats and
native DLLs by themselves ([mod manager](mod-manager.md)). A `mod.toml` adds what modman can't guess: the mod's id
and version, the game versions and mod API it needs, the DLL that modhost loads and the game functions it hooks,
where its RomFS files are, and the other mods it needs.

`modman add` reads it, and refuses a mod whose `mod.toml` is wrong, listing each problem. `deploy` checks it again.
The file goes at the root of the mod, or inside the one folder that holds everything (as many `.zip` files have it).
Loose files next to that folder, like a `README.md`, are fine. A `mod.toml` anywhere else (inside `romfs/`, next to
other folders, or one of several in one download) stops `add` with a message saying so: move it to the mod's root, or
extract the download and add each mod's folder on its own.

```
my-mod/
  mod.toml
  bin/my_mod.dll              the native mod: a 64-bit DLL that exports swmod_init
  romfs/                      RomFS files (optional)
  01007EF00011F001/romfs/     DLC RomFS files (optional)
```

```toml
[mod]
id = "yourname.my-mod"
name = "My Mod"
version = "1.0.0"
authors = ["yourname"]
description = "One line."
game_versions = ["1.9.0"]       # checked against your game's main build id: cd57b23fa4bbad65 is 1.9.0
loader_api = 2                  # the mod API the mod needs (2 for hooks)

[code]
windows = "bin/my_mod.dll"
hooks = ["0x7000cc7410"]        # functions of main the mod hooks (api->hook)

[data]
romfs = "romfs"
dlc_romfs = "01007EF00011F001/romfs"

[dependencies]
"othername.other-mod" = ">=1.0"
```

## The keys

| Key | Needed | What it is |
|---|---|---|
| `[mod] id` | yes | The mod's id in modman (`enable`, `disable`, `remove`, dependencies) and its folder name in the library. Lowercase letters, digits, `.`, `-` and `_`, at most 64, starting with a letter or digit, like `yourname.my-mod`. Not ending in `.`, no `..`, not a Windows device name before its first `.` (`con`, `prn`, `aux`, `nul`, `com0`-`com9`, `lpt0`-`lpt9`: so not `con` or `nul.mods`), and not 16 hex digits (that reads as a build id). The library holds one mod per id. |
| `name` | yes | What `list` and the messages call the mod. One line of at most 100 characters: no line breaks (U+2028 and U+2029 included) or control characters. `add --name` picks another one (the id stays). |
| `version` | yes | Up to four numbers of at most 9 digits each, like `1.0` or `0.2.1`, with an optional suffix after `-` or `+` (`1.0.0-beta.1`). `list` shows it. |
| `authors`, `description` | no | A list of names; one line of text (no line breaks, as for `name`). |
| `game_versions` | no; yes with hooks | The game versions the mod is made for, like `["1.9.0"]`. Checked against the build id of your game's `main`. Leave it out for a mod that works with any version. |
| `loader_api` | no (1) | The mod API the mod needs: `SWMOD_API_VERSION` in [`swmod.h`](../native/modhost/swmod.h). modhost 0.2.0 has API 2. Hooks need 2. A mod that needs more than the modhost in this repo has is refused. |
| `[code] windows` | no | The mod's DLL, relative to the folder `mod.toml` is in. It must be a 64-bit DLL that exports `swmod_init`, and it is the only one modhost loads as this mod. The mod's other 64-bit DLLs without `swmod_init` go next to it as helpers, as for a mod without `mod.toml`. Another DLL that exports `swmod_init` (a debug build, say) is left out, and `add` and `deploy` say so: next to the mod's DLL, modhost would load it as a second mod. Without `[code] windows`, every DLL that exports `swmod_init` is loaded, as for a mod without `mod.toml`. |
| `[code] linux` | no | Allowed, for later. modman (Windows) doesn't use it. |
| `[code] hooks` | no | Guest addresses of the functions of `main` the mod hooks, like `"0x7000cc7410"` or `"main+0xcc7410"`. Needs `windows`, `loader_api = 2` and `game_versions`. Each must be in `main`'s code and a multiple of 4 (the rebuild checks that it is a function start). |
| `[data] romfs` | no | The folder with the mod's RomFS files. `"."` when they (`Actor/`, `Pack/`, ...) are in the folder `mod.toml` is in. |
| `[data] dlc_romfs` | no | The folder with its DLC RomFS files. |
| `[dependencies]` | no | Other mods by id (the same rules as `[mod] id`), each with `">=1.0"`, `"==1.0"` or `"*"` (any version); at most 9 digits a number. |

Paths use `/` (or `\`) and must stay inside the mod folder. Tables and keys modman doesn't know are ignored (`add`
says so: look for typos). A mod with a `mod.toml` that is only `[mod]` and `[data]` is fine.

## RomFS folders

modman finds RomFS files the same way with or without a `mod.toml` (`romfs/`, `01007EF00011E000/romfs/`, folders
like `Actor/` and `Pack/`; DLC files in `01007EF00011F001/romfs/`). `[data]` says where they are, and modman checks
that it agrees: each folder must be there and hold RomFS files, and every RomFS file must be in the folder named for
its kind. DLC files are only read as DLC in a folder named `01007EF00011F001/romfs` (the title id says so), so use
that for `dlc_romfs`. A mod holds one RomFS tree: `romfs = "romfs"` with `dlc_romfs = "dlc/romfs"` is refused.

## Hooks

`deploy` collects the hooks of all enabled native mods into one list, `deploy\native\hooks.txt`, and rebuilds the
game code with hook slots for them (`tools/sw_rebuild.py --hooks`, 10-20 minutes), also when no mod has code patches.
The rebuilt library is kept while the game code files (with the ExeFS patches) and the set of hooks stay the same.
`deploy` lists which mod asked for which hook. With `deploy --no-rebuild` the mods still load, but their hooks do
nothing, and modhost logs `this game code has no hook slots`. How hooks work: [hooks](hooks.md).

## Dependencies

`deploy` refuses until each dependency of an enabled mod is in the library, turned on, and has a version that
matches. modman looks a dependency up by the id in each library mod's `mod.toml` (also for mods that are off, and
for a `mod.toml` put into a library folder by hand), and a mod without a `mod.toml` by its id in the library.
Versions compare by their numbers (`1.0` is `1.0.0`; a suffix is ignored). A mod without a `mod.toml` has
no version, so it only meets `"*"`. `add` says when a dependency isn't met yet. Dependencies don't change the load
order: modhost loads the DLLs in file name order.

## What add and deploy check

- `add` refuses a `mod.toml` that isn't valid TOML or breaks the rules above (it lists every problem), one that
  isn't where modman reads it (see the top), a DLL that is missing or can't be loaded as a mod, a game version or
  mod API that doesn't fit, a hook outside `main`'s code, and an id the library already has, as another mod's id in
  the library or in its `mod.toml` (remove the old one first).
- `deploy` runs the same checks again, on the library copy and your game as they are now, plus the dependencies.
  When one fails it stops before anything is deleted, so your last deploy stays. Fix the mod, or turn it off with
  `python tools/modman.py disable <id>`.
- modhost itself doesn't read `mod.toml`: modman checks it before the game starts.

## The examples in this repo

[`native/examples/hello`](../native/examples/hello/mod.toml) and
[`native/examples/stamina_hook`](../native/examples/stamina_hook/mod.toml) have a `mod.toml`. Their DLLs aren't in
those folders: `python tools/build_native.py build` builds them into `build\native` (`mods\hello.dll`,
`hook-mods\stamina_hook.dll`). So for a mod folder inside `native/examples`, when the DLL `[code] windows` names
isn't in the folder, `modman add` copies it from `build\native` into the library. The library keeps that copy: after
you rebuild an example, `remove` it and add it again. Any other mod must carry its DLL itself.

```
python tools/build_native.py build
python tools/modman.py add native/examples/hello
python tools/modman.py add native/examples/stamina_hook
python tools/modman.py deploy       # rebuilds the game code with a hook slot for 0x7000cc7410
python tools/modman.py run
```

## Not done yet

The mod API has logging, control commands, `hle_override`, `guest_call` and (API 2) hooks before, after or instead
of any function of `main`. Planned: events (each frame, save loaded, actor created), calling game functions by their
code map name, menus with Dear ImGui, and mods offering functions to other mods.
