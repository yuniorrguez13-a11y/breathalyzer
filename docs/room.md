# More Room (native/examples/room)

A native mod (mod API 2, no hook slots) that lifts the game's memory limits for modded actors at run time, so a big
costume or model gets the memory it needs instead of being shrunk, and says in plain words why something didn't load.
Part of multiplayer (`part_of` the ghost mod: on whenever the costumes are). Install:
`python tools/build_native.py build`, then `python tools/modman.py add native/examples/room` (room.dll is built into
`build\native\ghost-mods`).

## What it does

- **Heap failures in plain words.** It wraps the game's two heap allocators (sead ExpHeap and the actor heap, a
  "DualHeap": its own block, then an overflow heap) through `hle_override`. Every allocation that fails is logged
  once: which heap, its size, what was asked and what it had ("actor heap "Dm_Npc_Handmaid_A" (931336 bytes) needed
  2000000 bytes, had 1234 free (largest block 1000); its overflow ..."). At most 200 lines a game.
- **Actor heaps that grow.** The game sizes an actor's heap from ActorInfo's `instSize` plus a per-class extra (a
  virtual call from `0x700152a0d8`). For a watched actor (every actor whose pack is in the deployed RomFS: the modded
  ones) the extra grows to what that actor needed the last time its heap ran out (from the game's own failure numbers,
  plus half again), so the next try (the costume tries 5 times) has the room. Never for the game's own actors. Only
  for actor classes that use the base extra-size function (NPC and DemoNPC, all the costume slots, and about 35 more);
  a class with its own (Link's, about 40 more) is only logged.
- **The cap, from this PC.** One actor heap may grow by at most a quarter of the RAM free at start, less 1.5 GB kept
  for Windows and the game, never past 1 GB, never under 16 MB (so the iGPU laptop still gets what a costume needs).
  The graphics memory is read too (DXGI: the biggest adapter's dedicated memory, or half its shared memory on
  integrated graphics); a mod's textures may take a quarter of it before the plain-words warning.
- **Model files named as their files.** At start it reads every `Model\*.sbfres` of the deployed RomFS (up to 512)
  and logs any whose name inside differs from its file name: the game can't create an actor that uses it (below).

Command (developer mode): `room` (the cap, the heaps it watched, what it raised, the failures, the model check),
`room watch <actor>`, `room boost <MB>|auto|off` (a fixed extra for every watched actor, for tests), `room log on|off`.

## The Linkle / Custom bug (2026-11-15)

"Linkle / Custom did not load (the game could not create it)". Both are full models on Link's skeleton
(`Model/SwChar_linkle.sbfres`, `Model/SwChar_yuniar.sbfres`) in an NPC slot.

- **Not memory [V, in the game]:** with More Room watching, Linkle's actor heap (1,009,160 bytes) took 12 small
  allocations (the biggest 168 bytes), none failed, and nothing overflowed; no heap in the game failed at its
  creation. With 64 MB more actor heap (`room boost 64`: the heaps really were 68,118,024 bytes) both still failed.
- **The cause [V, in the game]:** the costume model files were Link's model file with new shapes, still named "Link"
  inside (the file's name, its one model's name, and "Link.Tex" for the textures), and the model list's unit was
  "Link". Every model file of the game is named as its file. The same two costumes with their files renamed
  `SwChar_linkle` / `SwChar_yuniar` (file, model, textures, unit) were created at once (0.17 s and 0.33 s), Urbosa
  still loads. Which of the two the game checks (a clash with the player's own loaded "Link", or the name against the
  file) isn't known [G].
- **The fix:** `tools/charsel.py` names every model file of ours as its file (`own_names`, the model list's unit too),
  repairs a kept VRM import in place on the next `charsel build`, and `charsel check` refuses a pack whose unit isn't
  its folder; `tools/vrm_import.py` names the imported model as its file. More Room's model check flags any mod's
  model file named wrong.

## Addresses (1.9.0, docs/addresses.md)

`0x700000218c` ExpHeap tryAlloc, `0x7001402e38` the actor heap's tryAlloc, `0x700152a0d8` the actor heap's creation
(its extra-size call returns to `0x700152a168`; the base extra-size function `0x700056c048`, the one the costume
classes use), `0x700108f698` ActorInfo instSize, `0x7001402b58` the actor heap create. Heap: name `+0x28`, size `+0x40`, parent `+0x48`; actor heap: overflow heap
`+0x110`, bytes in it `+0x118`; actor: name `+0x10`, its heap `+0x5b8`.

## Not done yet

- A native rescue for an actor heap whose overflow heap is full (a bigger overflow heap of our own) and for textures
  past the guest's texture work heap (`TextureArchiveWork`, 192 MB: it refused 10-14 MB asks while the world loaded,
  a normal retry or a real cap for 4K textures [G]). Neither was needed by today's costumes.
- Two big costumes at once (multiplayer ghosts) and a 200 MB model: not tried.
