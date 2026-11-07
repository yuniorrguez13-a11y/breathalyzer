# ghost (example native mod, BotW 1.9.0)

Spawns an actor in your game, moves it every frame and plays its Wait, Walk or Run animation by its speed: the
in-game half of the multiplayer client's `--ghost` option. The default stand-in is `Dm_Npc_Sheiker_Searcher_M_001`
(the cutscene copy of a Sheikah man), with the crate `Dm_Kibako_Contain_01` as the fallback. Full description,
launch commands, test results and limits: [docs/ghost.md](../../../docs/ghost.md).

- `ghost.c`: the whole mod. It wraps the game's per-frame job `0x7001129af8` with `hle_override` (no game-code
  rebuild, no hook slots), and spawns, moves and removes the actor with the game's own functions, called by address.
  `ghost pos` targets are smoothed to the frame rate (`smooth_*`); an actor with physics also gets its rigid bodies
  moved (`0x7001212820`). The animation (`anim_*`, `as_*`) is played by name with the game's own call
  (`0x700151e42c`), only when the choice changes.
- `selftest/ghost_selftest.c`: ghost.c's animation-name logic against a made-up actor, without the game
  (`tests/test_ghost_native.py` builds and runs it with Savage Wind's zig).
- Built by `python tools/build_native.py build` into `build\native\ghost-mods\ghost.dll`. `install` doesn't copy it;
  start the game with `SWMOD_DIR=<repo>\build\native\ghost-mods` (and `SAVAGEWIND_GAME_LIB` pointing at modhost),
  or add it with the mod manager (`mod.toml`).

Commands (developer mode): `ghost`, `ghost spawn [actor|link] [dx dy dz]`, `ghost pos x y z [yaw [speed [anim]]]`,
`ghost follow dx dy dz`, `ghost stop`, `ghost despawn`, `ghost anim auto|off|<name>`, `ghost far far_m [near_m]`,
`ghost self`. 0.5.0 adds, opt-in: the other player's animation name in `ghost pos` (played when the actor has it,
learned per actor), `ghost self` (your own Link's animation and items: plain reads, each address checked first) and
`ghost spawn link` (the Link body: `tools/mp/ghost_link_pack.py`). Tested in the game on one PC with 0.5.1
(docs/ghost.md, tenth round); not yet over two PCs.
A ghost whose place is over 60 m from Link (the game removes it at about 75 m) is not spawned until it is within
50 m again (state `far`), and one the game keeps removing is tried again less and less often.

The addresses come from read-only research on the user's own 1.9.0 `main` (out\research, never committed) and from
tests in the game. No game code is copied here.
