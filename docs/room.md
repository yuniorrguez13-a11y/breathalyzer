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
- **Textures past the game's texture heap.** When `TextureArchiveWork` (192 MB, fixed at boot) can't give an
  allocation, More Room gives it from extra memory instead, and the game frees it as usual (below: "Textures past
  TextureArchiveWork"). The log says so in plain words ("TextureArchiveWork (192.0 MB) was full (needed 13.7 MB, had
  0.6 MB free, largest block 0.5 MB): More Room gave it from its extra memory (13.7 MB of 1.0 GB in use)").

Command (developer mode): `room` (the cap, the heaps it watched, what it raised, the extra memory, the failures, the
model check), `room watch <actor>`, `room boost <MB>|auto|off` (a fixed extra for every watched actor, for tests),
`room rescue on|off|size <MB>|<heap>` (the texture rescue on or off, the extra memory's size before it is made, or
rescue one more heap by name), `room log on|off`.

## Textures past TextureArchiveWork (2026-11-18)

The 2026-11-15 game logs had `TextureArchiveWork` (201,326,288 bytes) refuse 10-14 MB asks while the world loaded,
with costumes in the RomFS ("needed 14385352 bytes, had 639232 free"). On 2026-11-18, with More Room the only mod,
the game did the same at the first world load (14.4, 7.7, 26.5 and 14.0 MB asks) and about 15 more times while
moving around: the unmodded game runs into this heap too [V, in the game]; what it does then (wait, drop a texture,
retry) isn't known [G]. A 4K texture is 10-20 MB, a 200 MB model's textures are far past that heap, and the rule is to lift the limit,
never shrink the model.

**How the game makes it [V, from its code, static].** At boot one function builds the texture heaps, all with fixed
sizes and all through direct calls:

- `0x700131aea0` makes `FixedHeap`, 374 MB (`0x17600000`), from its parent;
- `0x700131b450` makes `TexArcWork` in it: `mov w0, #0xc000000` (192 MB) into the ExpHeap create `0x7000e9f2c8`;
- the texture system then makes `TextureArchiveWork` with size 0 ("all of the parent") inside `TexArcWork`, through
  agl's heap wrapper (`0x7001558908`, which calls the other ExpHeap create, `0x7000003f10`).

`FixedHeap` is carved up whole by fixed children (TexArcWork 192 MB, TeraSZSWork 10 MB, GameDataWork 4 MB, ...),
and the game's whole memory is Savage Wind's 3.2 GB "application memory", as on the Switch (its `kernel.c`). So the
heap can't simply be made bigger at boot: there is no room for it in the guest's memory, and none of those calls goes
through Savage Wind's dispatcher, so `hle_override` can't change the size (a hook could, but hooks need a 10-20
minute game-code rebuild for every player with the costumes).

**The fix: an extra heap for what doesn't fit [V, in the game 2026-11-18, More Room alone, a test save].** The
extra heap was made at the first failure (1.0 GB at 0x74_0000_1000, among the root heaps); in 4 minutes (the load,
then Kakariko, Hyrule Castle town and Gerudo Town by `teleport`) it gave 489 allocations, 650 MB, the biggest 61 MB,
none refused; the textures looked right in screenshots at every stop, and the game closed normally.

1. The allocations of `TextureArchiveWork` go through its vtable (sead ExpHeap `tryAlloc`, `0x700000218c`, which
   More Room already wraps). When one fails, More Room gives it from **its own heap** instead.
2. That heap is made at the first failure, never before (a game that never runs out costs nothing): More Room commits
   memory right above Savage Wind's guest address window (the first free run from 0x74_0000_0000, below 2^39, the
   Switch's address width; Windows says it is free), and the game's **own** ExpHeap code builds a
   heap there: its constructor `0x7000e9ef38` and its free list `0x7000e9ff94` (named `MoreRoomExtra`, with its own
   lock). Its size is More Room's cap from this PC (a quarter of the free RAM less 1.5 GB, at most 1 GB), but at
   least 64 MB. Its pages take RAM only once a texture is in them (until then Windows only counts them against RAM
   plus page file).
3. The game frees those blocks the way it frees any other:
   - through the heap it thinks the block came from: ExpHeap's `free` (`0x7000ea0a84`) and its three resizes
     (`0x7000ea0e00`, `0x7000ea0f78`, `0x7000ea139c`), all vtable calls, are wrapped: a block of the extra heap goes
     to the extra heap. (Without the wrapper the game's code would only refuse it: each of them first checks the
     block is inside its own heap [V, static], so a missed one leaks, it never corrupts.)
   - through `delete` (`0x7000000680`), which asks the heap lookup (`0x7000006ef0`) whose block it is: the extra heap
     is added to the game's root heap list (`0x7001e33f88`, under the lock the lookup takes, `0x7001e34020`), so the
     lookup finds it and frees it there. When the list looks different than expected, it isn't added, and the log
     says that blocks freed with `delete` stay used.
4. Textures read from guest memory work anywhere: Savage Wind's GPU address is the CPU address (`nvn/objects.c`: a
   texture's address is its memory pool's storage plus the offset, with no range check) [V, from its code].
5. Not inside the guest window: the first try (an unused gap in it) was refused in the game, because the guest maps
   its own memory into the window's gaps (the time service's shared memory sat at 0x70_baf2_2000), and Savage Wind's
   kernel, which hands those addresses out, can't know about ours [V, in the game 2026-11-18].
6. The graphics-memory warning (a quarter of the graphics memory) is said once when the extra heap holds more than
   that.

Only `TextureArchiveWork` is rescued by default; `room rescue <heap>` adds another by name (for tests).
`TeraSZSWork` (the terrain streaming cache) and the screenshot heap fail and retry in a normal game, so they aren't.

**What it means for memory [V, in the game]:** not one of those 489 blocks was freed while moving across the map
(no free reached the extra heap, its use only grew). So the texture system keeps what it got and lets go only when
an allocation fails [G: the likely reading]: with the rescue its cache grows past 192 MB into the extra memory, up to
the cap (1 GB on a PC with RAM to spare), and then works as before. `room rescue size <MB>` (before the first world
load: the size is fixed when the heap is made) sets a smaller extra memory for tests.

**Full [V, in the game 2026-11-18, `room rescue size 64`]:** the 64 MB filled at the second place (71 allocations),
then 18 more asks were refused as before the rescue (the game's own handling), still no block freed, and the game
ran on and closed normally.

**Not known yet [G]:** whether the game would ever free those blocks (it never did in either run: either it keeps
those textures, or it frees them some way that doesn't reach ExpHeap's free; a leak would look the same), and so
whether a long session keeps the whole extra memory; whether agl's heap compaction (it allocates a 30 MB work buffer
from the same heap and moves blocks) leaves the extra heap's blocks alone.

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

The texture rescue: ExpHeap free `0x7000ea0a84` and resizes `0x7000ea0e00`, `0x7000ea0f78`, `0x7000ea139c`, its
constructor `0x7000e9ef38` and free list `0x7000e9ff94`, the root heap list `0x7001e33f88` and its lock
`0x7001e34020` (lock / unlock `0x70015a0520` / `0x70015a0530`), the string class's vtable `0x7001d66260`, the extra
memory from 0x74_0000_0000 up. Research only: `0x700131aea0`, `0x700131b450`, `0x7000e9f2c8`, `0x7000003f10`,
`0x7001558908`, `0x7000000680`, `0x7000006ef0` (above).

## Not done yet

- The texture rescue with a big texture mod (a 4K costume, a 200 MB model) and with the extra memory full: only the
  unmodded game's own textures were tried.
- A native rescue for an actor heap whose overflow heap is full (a bigger overflow heap of our own). Not needed by
  today's costumes.
- Two big costumes at once (multiplayer ghosts) and a 200 MB model: not tried.
