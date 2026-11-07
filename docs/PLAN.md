# Breathalyzer plan

Base: [Savage Wind Recomp](https://github.com/Revivedx/SavageWindRecomp), a native PC build of Switch BotW 1.9.0
(MIT, alpha, built on LAS Recomp). Mods are on its roadmap ("Beta releases will include DLC + Mods support", #124).
Its open PR #430 adds data mods through BCML. Code mods don't exist yet.

## Phases

| # | Phase | Effort (part-time) | Status (2026-11-07) |
|---|---|---|---|
| 0 | Setup: dump, play, rebuild game code | 1-2 weeks | Done: game set up and playing; headless rebuild works. Building Savage Wind itself from source not needed yet |
| 1 | Code map: names for 1.9.0 functions, struct layouts, docs | first 200 functions in 3-6 weeks | Tool done: 64k code addresses named from the game's own C++ type info, on the player's PC. Human-readable names for key systems still to do |
| 2 | Mod loader + SDK: hooks, DLL mods, `mod.toml`, existing mods ([MOD-COMPAT](MOD-COMPAT.md)) | 2-4 months | Works in game: RomFS overlay, ExeFS patches (data and code), cheats, native DLL mods, hooks on any game function (via a rebuild), and a mod manager (command line and a large-print window) that deploys and starts them all. Wii U data mods convert on the PC (text, maps, actor data; a converted Wii U text mod tested in game) |
| 3 | Multiplayer showcase: "ghost Link" for 2-4 players | 1-3 months | Works as a first version: relay, client, radar, and a native mod that shows each other player as a stand-in character that follows and turns with them and plays the game's own stand, walk and run animations (tested in game, 17k animation switches without a crash). Tested on two real PCs over home Wi-Fi (2026-11-07). Next: a Link-like model, turn and jump poses |
| 4 | Release: code only, clean checks, mirrors | 2-4 weeks | Clean check and release zip tool done (`tools/make_release.py`: allow-list, leak and secret checks, same zip every time). Not released |

## Key facts

- **How mods get in without changing Savage Wind** (all tested in game): `SAVAGEWIND_ROMFS_OVERLAY` for files,
  a patched `exefs` in a separate game folder for ExeFS patches, `SAVAGEWIND_GAME_LIB` for our native mod host
  (and for rebuilt game code), and `SAVAGEWIND_CONTROL` for cheats and multiplayer. Details are in each
  tool's doc, e.g. [code-mods](code-mods.md) and [romfs-mods](romfs-mods.md).
- **Hooks work without changing Savage Wind** (tested in game). The game's ARM code is recompiled to native code, so
  exlaunch-style runtime hooks don't apply. Instead `tools/gen_hooks.py` adds hook slots to the generated game code
  and `tools/sw_rebuild.py --hooks` rebuilds our own copy of the library; modhost fills the slots
  ([hooks](hooks.md)). Sending a hook layer upstream (PRs against `test`) is optional future work: talk to the
  maintainer (Revivedx) first so we don't duplicate their mod work.
- **The code map comes from [zeldaret/botw](https://github.com/zeldaret/botw), but for a different version.** It
  names about 97k of 113k functions, for 1.5.0; Savage Wind runs 1.9.0. Port names with Ghidra plus patterns,
  starting with what mods need: player, actors, world manager, events, game flags, input, per-frame update.
  zeldaret/botw has no license: link to it, don't copy from it, ask before reusing.
- **Multiplayer:** ENet or GameNetworkingSockets plus a small relay server, with 20–30 updates a second and a jitter
  buffer. Remote players are puppet actors with their AI off. Test solo with a bot client that replays recorded
  movement; a real test needs a friend's PC with their own dump.
- **What sets us apart:** every existing BotW multiplayer mod (BOTWM, BreathM, PrivCord co-op) runs on Cemu and
  pokes memory from outside. Ours runs inside the native PC build, is open source, and is clean.

## Disk (C: had 36 GB free on 2026-11-06, with the dump already on it)

| Need | Size | Where |
|---|---|---|
| Your game dump (1.9.0 ExeFS + merged RomFS, DLC optional) | 15.2 GB (+1.4 GB DLC) | C: (Documents). Fits; an external drive is still better |
| Savage Wind data (the release setup copies the dump here) | ~20 GB free needed | C: for now; `SAVAGEWIND_DATA_ROOT` moves it |
| Source build: the dump as `extracted/` next to the program | read in place, no copy | Move the dump there instead of copying it |
| MSYS2 build tools | ~3–5 GB (estimate) | C: (`C:\msys64`) |
| Repo, build output | ~1.5 GB | Anywhere |

## Release rules

Code only. No game files, keys, generated code, or converted or bundled assets. Use a neutral name (no "Zelda"),
take no money (no Patreon or early access), mirror the repo, and expect YouTube claims on gameplay videos.
Background: SM64 port DMCA (2020), BotW MP claims (2023), Yuzu settlement (2024), Ryujinx stop (2024).
