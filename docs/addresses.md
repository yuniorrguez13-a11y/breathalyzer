# Game addresses (BotW 1.9.0)

Every address of the game that this repo uses or documents, in one table: the example mods, modhost, the tools, the
docs, and a few confirmed findings of the local research notes (`out\research`, never committed).

- **1.9.0 only.** These are addresses in `main` of the Switch version 1.9.0 (main build id `cd57b23fa4bbad65`).
  Another version moves almost every function and global; don't use them there.
- **Guest addresses are host addresses.** Savage Wind maps the game's memory at the same addresses on the PC, so a
  guest pointer can be read directly from C (`*(float *)(player_info + 0xa4)`), and `api->hook`, `hle_override`,
  `dispatch_lookup` and `guest_call` take these numbers as they are.
- **From a module offset to an address:** `main` starts at `0x7000000000` (`MAIN_BASE`), so an offset into `main`'s
  loaded image becomes an address by adding it: `main+0xcc7410` = `0x7000000000 + 0xcc7410` = `0x7000cc7410`. The
  hooks lists and `mod.toml` accept both forms. The offset is into the loaded image (as a disassembler shows it when
  `main` is loaded at 0), not into the compressed `main` file. The other modules (`subsdk0`, `sdk`) follow `main`
  back to back, page-rounded, so their addresses depend on `main`'s size; nothing here uses them.
- **Names** in the table are our own short descriptions. Offsets (`+0x88`) are from the start of the object the row
  names. Where it says `out/research/...`, the source is a local research note: it isn't in the repo, and only
  findings that note marks as confirmed are listed.

Status: **used in a mod** (an example mod in `native/examples` calls or reads it, and that mod worked in game; the
fourth column says what was seen of this address),
**verified** (checked in game or in the tools, but no mod uses it), **research only** (from reading the code, not
tried in game), **made up (selftest)** (not a game address: invented for `tools/build_native.py selftest`).
`tests/test_addresses.py` checks this table against the code.

| Address | What it is | Where the repo uses it | How it was verified | Status |
|---|---|---|---|---|
| **Memory layout** | | | | |
| `0x7000000000` | Start of `main` (`MAIN_BASE`); also the cheat regions Main and Aslr | `tools/control.py`, `tools/cheats.py`, `tools/codemap.py`, `tools/gen_hooks.py`, `tools/sw_rebuild.py`, `native/modhost/swmod.h`, `docs/cheats.md`, `docs/code-map.md`, `native/modhost/botw.h`, `native/examples/modmenu/opt_page_a.c` | In game 2026-11-06 and 07: every `main` address below works at this base, and so did Main-region cheats (stamina through `[main+0x1e13ed0]+0xa4`); the modmenu mod searches `main`'s code from here for the description function (found it once, 2026-11-07) | used in a mod |
| `0x7100000000` | Alias region (`ALIAS_BASE`), the cheat region Alias | `tools/control.py`, `tools/cheats.py`, `docs/cheats.md` | Savage Wind's memory layout (its source); no tested cheat used it | research only |
| `0x7240000000` | Start of Savage Wind's heap (`HEAP_BASE`), the cheat region Heap | `tools/control.py`, `tools/cheats.py`, `docs/cheats.md`, `native/modhost/botw.h` | Savage Wind's memory layout (its source); no heap-relative cheat tested | research only |
| `0x7320000000` | End of guest memory (`GUEST_HI`): cheats refuse addresses from here on | `tools/control.py`, `native/modhost/botw.h` | Savage Wind's memory layout (its source); unit tests only | research only |
| **Player** | | | | |
| `0x7001e13ed0` | Player info pointer (u64): points at the player info block | `native/examples/ghost/ghost.c`, `tools/control.py`, `native/modhost/botw.h` | In game 2026-11-07: the ghost mod reads Link's position through it (`ghost follow` stayed 3 m ahead of Link while he ran) | used in a mod |
| `0x7001cd7bb8` | Link's actor class (its vtable): `ghost self` reads player info `+0x60` only when it starts with this | `native/examples/ghost/ghost.c` | In game 2026-11-07: `ghost self` found Link with it (`found` true) on the plateau test save, in every check run (static research: the `Player` entry's constructor stores it; out/research/player-state-plan.md) | used in a mod |
| `0x7000cc6fd0` | Get the player actor from the player info block (returns its `+0x60`) | `native/examples/ghost/ghost.c`, `docs/ghost.md`, `native/modhost/botw.h` | In game 2026-11-07: called from the ghost tick, it returned the same pointer as player info `+0x60`, 37 of 37 checks | used in a mod |
| **Pointers and offsets** | | | | |
| `+0x60` | Player info: the player actor | `native/examples/ghost/ghost.c`, `docs/ghost.md`, `native/modhost/botw.h` | In game 2026-11-07: equal to what `0x7000cc6fd0` returns, 37 of 37 checks | used in a mod |
| `+0x88` | Player info: copy of Link's position (3 floats: x, y, z) | `native/examples/ghost/ghost.c`, `native/modhost/botw.h` | In game 2026-11-07: `ghost follow` and the distance rule use it; it reads 0 0 0 on the title screen | used in a mod |
| `+0xa4` | Player info: stamina (float; 1000 = the full wheel in the tests) | `native/examples/stamina_hook/stamina_hook.c`, `tools/control.py`, `docs/hooks.md`, `native/modhost/botw.h` | In game 2026-11-06: stamina stays 1000 while sprinting with the hook (the setter's new value is compared with this one) | used in a mod |
| `+0x59` | Actor spawner: a byte; while set, spawn requests are refused | `native/examples/ghost/ghost.c` | Static research (`out/research/actor-spawn-verify.md`, confirmed); the ghost mod checks it before each request | used in a mod |
| `+0x28` | Scene object: the heap that spawned actors go on | `native/examples/ghost/ghost.c` | In game 2026-11-07: spawns with this heap work (crate, Sheikah man, Zelda) | used in a mod |
| `+0x398` | Actor: its 3x4 matrix, row by row (position at `+0x3a4`, `+0x3b4`, `+0x3c4`) | `native/examples/ghost/ghost.c`, `native/modhost/botw.h` | In game 2026-11-07: `ghost` reads back the positions and yaw (0, 90, -45) that `ghost pos` set | used in a mod |
| `+0x578` | Actor: physics pointer (0 = no physics) | `native/examples/ghost/ghost.c`, `docs/ghost.md`, `native/modhost/botw.h` | In game 2026-11-07: set on Zelda's cutscene copy, 0 on the crate | used in a mod |
| `+0x80` | Real position chain: third step, in Link's physics (u64) | `native/modhost/botw.h`, `docs/game-basics.md` | In game 2026-11-06: a cheat followed `[main+0x1e13ed0]` `+0x60`, `+0x578`, `+0x80`, `+0x410` and wrote 300.0 to `+0x1c4` 12 times a second; it held Link in the air (height about 290-300) and let him go when stopped. Savage Wind's own `teleport` tries the same chain first and checks it against `+0x88` | verified |
| `+0x410` | Real position chain: fourth step, in the object `+0x80` points at (u64) | `native/modhost/botw.h`, `docs/game-basics.md` | In game 2026-11-06: a cheat followed `[main+0x1e13ed0]` `+0x60`, `+0x578`, `+0x80`, `+0x410` and wrote 300.0 to `+0x1c4` 12 times a second; it held Link in the air (height about 290-300) and let him go when stopped. Savage Wind's own `teleport` tries the same chain first and checks it against `+0x88` | verified |
| `+0x1c0` | Real position chain: Link's real position (3 floats: x, y, z; only y was written in game) | `native/modhost/botw.h`, `docs/game-basics.md` | In game 2026-11-06: a cheat followed `[main+0x1e13ed0]` `+0x60`, `+0x578`, `+0x80`, `+0x410` and wrote 300.0 to `+0x1c4` 12 times a second; it held Link in the air (height about 290-300) and let him go when stopped. Savage Wind's own `teleport` tries the same chain first and checks it against `+0x88` | verified |
| `+0x1c4` | Real position chain: Link's height (the y of `+0x1c0`; writing it moves him) | `native/modhost/botw.h`, `docs/game-basics.md` | In game 2026-11-06: a cheat followed `[main+0x1e13ed0]` `+0x60`, `+0x578`, `+0x80`, `+0x410` and wrote 300.0 to `+0x1c4` 12 times a second; it held Link in the air (height about 290-300) and let him go when stopped. Savage Wind's own `teleport` tries the same chain first and checks it against `+0x88` | verified |
| `+0x560` | Actor: animation holder (0, or a dummy with no owner: no animations) | `native/examples/ghost/ghost.c`, `docs/ghost.md` | In game 2026-11-07: present on the Sheikah man and Zelda; the crate has none, so no animation call is made for it | used in a mod |
| `+0xd8` | Animation holder: its owner actor | `native/examples/ghost/ghost.c`, `docs/ghost.md` | In game 2026-11-07: equal to the ghost actor before every play call (`as_ok`) | used in a mod |
| `+0xb8` | Animation holder: number of slots (s32) | `native/examples/ghost/ghost.c` | In game 2026-11-07: at least 1 on the Sheikah man and Zelda (`as_ok`) | used in a mod |
| `+0xc0` | Animation holder: the slot array (0x50 bytes a slot: sub count at `+0x20`, subs at `+0x28`, 0x98 bytes each: definition `+0x0` whose name is the char* at `+0x10`, weight `+0x10`, playing `+0x18`) | `native/examples/ghost/ghost.c` | In game 2026-11-07: on Link 5 slots with 2, 2, 1, 3, 1 subs, as researched; slot 0 sub 0 named what he did (`Wait`, `Move`, `Jump`, `Land`, `Fall`, `ClimbMove`, `WallDash`, `DeadRagdoll`). `ghost self` reads it on Link | used in a mod |
| `+0xb90` | Actor: equipment holder (vtable `0x7001d349d8`; links from `+0x8`, 0x18 apart: 0 weapon, 1 shield, 2 bow; each link's data has the id at `+0x40` and the item's actor at `+0x48`; owner at `+0x98`) | `native/examples/ghost/ghost.c` | In game 2026-11-07: Link's holder passed the vtable and owner checks (`equip_ok` true). The weapon link (slot 0) read `Weapon_Sword_001` after Link took a Traveler's Sword (eleventh round, 3 runs); empty before. The shield and bow links read empty (none in the test save), so their names are not tested. `ghost self` reads Link's | used in a mod |
| `+0x64` | Actor: state byte (3 while it is being deleted) | `native/examples/ghost/ghost.c` | Partly: in game 2026-11-07 Link's sword (not being deleted) was read past this check; a deleted item was never seen, so the value 3 is static research only (out/research/player-state-plan.md). `ghost self` skips an item in that state | used in a mod |
| **Actor spawn, move and delete** | | | | |
| `0x7001e95430` | Actor spawner pointer (u64) | `native/examples/ghost/ghost.c` | In game 2026-11-07: spawns through it work (the crate was live 0.02 s after the request) | used in a mod |
| `0x7001e14cd8` | Scene object pointer (u64); its `+0x28` is the spawn heap | `native/examples/ghost/ghost.c` | In game 2026-11-07: as above | used in a mod |
| `0x7001535b10` | Request an actor spawn (async: ready a few frames later) | `native/examples/ghost/ghost.c`, `docs/ghost.md` | In game 2026-11-07: crate, Sheikah man and Zelda spawned; a request on a loading stage fails and is tried again | used in a mod |
| `0x7001535900` | Spawn an actor at once (synchronous; with its last flag set it blocks every later request: don't use) | `out/research/actor-spawn-verify.md` | Static research (confirmed); never called | research only |
| `0x700112615c` | Parameter pack: clear | `native/examples/ghost/ghost.c` | In game 2026-11-07: every spawn above sent a pack built this way (the mod also sets the same matrix with `0x700152261c`, so the pack's matrix was not tested alone) | used in a mod |
| `0x7001126168` | Parameter pack: add a value | `native/examples/ghost/ghost.c` | In game 2026-11-07: as above | used in a mod |
| `0x7001bc5540` | Parameter pack: its vtable pointer (the mod builds the pack itself) | `native/examples/ghost/ghost.c` | In game 2026-11-07: as above | used in a mod |
| `0x7001bc32c8` | String object: its vtable pointer (the mod builds the pack key with it; the animation name is passed as plain text) | `native/examples/ghost/ghost.c` | In game 2026-11-07: as for the pack | used in a mod |
| `0x700160f9e5` | The key text for the spawn matrix, in `main` (the pack keeps the pointer, so it must stay valid) | `native/examples/ghost/ghost.c` | In game 2026-11-07: as for the pack | used in a mod |
| `0x7001516814` | Spawn handle: clear | `native/examples/ghost/ghost.c` | In game 2026-11-07: every spawn above, and retries after failed requests | used in a mod |
| `0x7001516820` | Spawn handle: is the actor ready? | `native/examples/ghost/ghost.c` | In game 2026-11-07: as above | used in a mod |
| `0x7001516840` | Spawn handle: did the request fail? | `native/examples/ghost/ghost.c` | In game 2026-11-07: requests on a loading stage failed and were released | used in a mod |
| `0x7001516a5c` | Spawn handle: look at the new actor (not taken) | `native/examples/ghost/ghost.c` | In game 2026-11-07: as for every spawn | used in a mod |
| `0x7001516b74` | Spawn handle: take the new actor (wakes it) | `native/examples/ghost/ghost.c` | In game 2026-11-07: as for every spawn | used in a mod |
| `0x70015168a8` | Spawn handle: release it if the request failed | `native/examples/ghost/ghost.c` | In game 2026-11-07: as for failed requests | used in a mod |
| `0x7001516908` | Spawn handle: release (an untaken actor is deleted) | `native/examples/ghost/ghost.c` | Called for a request that times out (30 s), creates something that is not an actor, or is removed while pending; the test notes show none of these in game (failed requests use `0x70015168a8`) | used in a mod |
| `0x700056b6d0` | Actor type info (to check that a new object is an actor) | `native/examples/ghost/ghost.c` | In game 2026-11-07: every spawned actor passed the check | used in a mod |
| `0x7001517e60` | Actor link: set | `native/examples/ghost/ghost.c` | In game 2026-11-07: kept on every live ghost | used in a mod |
| `0x7001517e0c` | Actor link: is the actor still there? | `native/examples/ghost/ghost.c` | In game 2026-11-07: turned invalid on a reload and when the game removed a far ghost | used in a mod |
| `0x7001517f8c` | Actor link: reset | `native/examples/ghost/ghost.c` | In game 2026-11-07: after despawns and losses | used in a mod |
| `0x700152261c` | Set an actor's matrix | `native/examples/ghost/ghost.c` | In game 2026-11-07: set on each new actor before it is taken; the crate stood 4 m in front of Link, where the mod put it | used in a mod |
| `0x70015223f0` | Set an actor's position and rotation | `native/examples/ghost/ghost.c`, `docs/ghost.md` | In game 2026-11-07: `ghost pos` moved the crate; the yaw read back as set (0, 90, -45). Alone it did not move Zelda (an actor with physics also needs `0x7001212820`) | used in a mod |
| `0x7001212820` | Move an actor's physics bodies to a matrix | `native/examples/ghost/ghost.c`, `native/examples/ghost/README.md`, `docs/ghost.md` | In game 2026-11-07: with it Zelda stays where she is put (without it she went back to the spawn point) | used in a mod |
| `0x7001514bac` | Remove an actor (at a safe point later) | `native/examples/ghost/ghost.c` | In game 2026-11-07: the crate was gone after `ghost despawn` | used in a mod |
| **Per-frame job** | | | | |
| `0x7001129af8` | Run function of a per-frame job (the ghost mod wraps it with `hle_override`) | `native/examples/ghost/ghost.c`, `native/examples/ghost/README.md`, `docs/ghost.md`, `native/modhost/botw.h` | In game 2026-11-07: 50-60 calls a second in game (one per frame) on 3 worker threads, never two at once | used in a mod |
| `0x7001280f80` | Frame step that the per-frame job calls first (once per frame) | `out/research/actor-spawn-verify.md` | Static research (confirmed); not hooked | research only |
| `0x700151d3d0` | Frame time update (runs twice per frame while the game is paused) | `out/research/actor-spawn-verify.md` | Static research (confirmed); not hooked | research only |
| **Animation** | | | | |
| `0x700151e42c` | Play an animation set by name | `native/examples/ghost/ghost.c`, `native/examples/ghost/README.md`, `docs/ghost.md` | In game 2026-11-07: the standing, walking and running sets play on the Sheikah man and Zelda; a stress test made 17,767 calls with no crash or hang | used in a mod |
| `0x70014a0040` | Name of the animation set that plays now | `native/examples/ghost/ghost.c`, `docs/ghost.md` | In game 2026-11-07: `ghost` shows the playing set (`as`), 5 reads a second | used in a mod |
| **Equipment (`ghost self`)** | | | | |
| `0x7001d349d8` | Equipment holder class (its vtable), at actor `+0xb90` | `native/examples/ghost/ghost.c` | In game 2026-11-07: Link's holder at `+0xb90` starts with it and names Link as its owner (`equip_ok` true in every check run); static research: out/research/player-state-plan.md. `ghost self` reads the equipment only when it is there | used in a mod |
| `0x7001c3f330` | Weapon class (its vtable): `ghost self` says whether the equipped weapon is one (`weapon_class`; the name is sent either way) | `native/examples/ghost/ghost.c` | In game 2026-11-07: Link's Traveler's Sword (`Weapon_Sword_001`) starts with it (`weapon_class` true, 3 runs); static research: the class table's `Weapon` entry (out/research/player-state-plan.md) | used in a mod |
| **World and clock** | | | | |
| `+0x5c0` | World object (Savage Wind's site `world_mgr`): how many managers its list has (u32) | `native/modhost/botw.h`, `docs/game-basics.md` | In game 2026-11-07: `botw_time_of_day` (site `world_mgr`, count `+0x5c0`, list `+0x5c8`, the clock first, time `+0xb8`) read the same hour as Savage Wind's `time` at 11, 6, 7.5, 18 and 23.75 (set with `time set`) | verified |
| `+0x5c8` | World object: its manager list (u64); the first one is the clock | `native/modhost/botw.h`, `docs/game-basics.md` | In game 2026-11-07: `botw_time_of_day` (site `world_mgr`, count `+0x5c0`, list `+0x5c8`, the clock first, time `+0xb8`) read the same hour as Savage Wind's `time` at 11, 6, 7.5, 18 and 23.75 (set with `time set`) | verified |
| `+0xb8` | Clock: the time of day, 0 to 360 for 24 hours (float; the same number is another offset in the animation holder) | `native/modhost/botw.h`, `docs/game-basics.md` | In game 2026-11-07: `botw_time_of_day` (site `world_mgr`, count `+0x5c0`, list `+0x5c8`, the clock first, time `+0xb8`) read the same hour as Savage Wind's `time` at 11, 6, 7.5, 18 and 23.75 (set with `time set`) | verified |
| **Stamina** | | | | |
| `0x7000cc7410` | Stamina setter: x0 = player info, s0 = the new stamina, stored at `+0xa4` | `native/examples/stamina_hook/stamina_hook.c`, `native/examples/stamina_hook/hooks.txt`, `native/examples/stamina_hook/mod.toml`, `tools/gen_hooks.py`, `tools/modman.py`, `tools/build_native.py`, `docs/hooks.md`, `docs/mod-format.md`, `native/modhost/botw.h` | In game 2026-11-06: stamina stays 1000 while sprinting with the hook (and with an ExeFS patch that skips its store); 2026-11-07 again through `modman run` | used in a mod |
| `0x7000cc7414` | 4 bytes into the stamina setter: the docs' example of an address the rebuild refuses (not a function start) | `docs/hooks.md` | Only an example of the error message; never hooked | research only |
| **Options menu (modmenu mod)** | | | | |
| `0x7000e30550` | The game's function that writes the Options description line for the row under the cursor (Savage Wind names it `opt_two_help` in later versions); the modmenu mod finds it at run time by its first 8 instructions, so the number itself isn't in the mod | `docs/mod-menu.md` | In game 2026-11-07: the mod's search found exactly one match (its log), and calling it rewrote the line after a switch, a move and Y (out/shots/modmenu_fix_t07 to t19); Savage Wind's own research names the same address | verified |
| **Made up for the selftest (not game addresses)** | | | | |
| `0x7000001000` | Fake function that one selftest probe mod overrides (`hle_override`) and another hooks | `tools/build_native.py` | `python tools/build_native.py selftest` | made up (selftest) |
| `0x7000001020` | Fake address with no hook slot (the selftest expects the refusal) | `tools/build_native.py` | `python tools/build_native.py selftest` | made up (selftest) |
| `0x7000002020` | Fake function hooked by the selftest's probe mod | `tools/build_native.py` | `python tools/build_native.py selftest` | made up (selftest) |
| `0x7000002030` | Fake hook added too late (the selftest expects the refusal) | `tools/build_native.py` | `python tools/build_native.py selftest` | made up (selftest) |
| `0x7000003000` | Fake function the probe mod calls with `guest_call` | `tools/build_native.py` | `python tools/build_native.py selftest` | made up (selftest) |
| `0x7000123450` | Fake named site the probe mod looks up with `find_site` | `tools/build_native.py` | `python tools/build_native.py selftest` | made up (selftest) |
