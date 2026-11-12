# Castle date (showcase mod, by giruka)

Beat the game, and three wooden doors stand in Princess Zelda's Room in Hyrule Castle. The right one takes you to
her Study, where Zelda greets you (our text, no voice) and gives you a choice. A door in the Study takes you back.

This is stage 1 of the plan. Later stages: the date itself (sofa, moods, her research, a photo), then a games room.

**Status (2026-11-12):** builds and installs on a real PC with the Linkle mod next to it. **Not tried in game yet.**
The door, trigger and Zelda spots are guessed from the room sizes (`castle/layout.json` says `"measured": false`);
the first game run walks them.

## Build it

The mod is built on your PC from your own game files. Nothing from the game is in this repo or in what we ship.

```
python tools/castle_build.py check         # reads your game files, changes nothing
python tools/castle_build.py build         # builds the mod and adds it to your mod library
python tools/modman.py deploy
```

- `check` lists problems: a name the castle uses that your game lacks, a placed object that would need a save
  flag, a text or animation that isn't there.
- `build` takes about 1-2 minutes and runs at low priority. Build again after you change `castle/`; the mod keeps
  its place in the load order and its on/off switch.
- `where` lists the game files it reads (all in `extracted\romfs`, only read).

| Option | What it does |
|---|---|
| `--cloth` | keeps Zelda's dress cloth (costs 3-8 fps); default: light physics, no cloth |
| `--unit dynamic` | puts the doors in the loose `E-4_Dynamic` map file instead of `E-4_Static` (in `TitleBG.pack`) |
| `--no-events` | doors and Zelda only, no door warps and no talk |
| `--no-add --out <folder>` | only writes the mod folder |
| `--library <folder>`, `--game <folder>` | another mod library or Savage Wind folder |

## What it changes

| Part | Where |
|---|---|
| Zelda (`SwZelda`) from your `Dm_Npc_Zelda_Sibyl`: a villager's "Talk" prompt, no cloth | her actor pack, actor info |
| Her talk and the two door warps (event flows written by `tools/evfl.py`) | 2 event packs, event info |
| Her name and lines | message files (all languages get the English lines) |
| 4 doors, Zelda and 2 door triggers, shown only once `GameClear` is set | map unit `E-4_Static` |
| Size entries for the new and grown files | size table |

## Works with other mods

Linkle (and any mod that ships `Pack/TitleBG.pack`) works next to it in any load order: `deploy` merges the two
`TitleBG.pack` files file by file and applies the castle's map change again. Checked offline on 2026-11-12, both
orders: all 11 of Linkle's files and the castle's objects are in the merged pack.

## Your save

- `GameClear` (set by the game when you beat it) is only read, never written.
- Stage 1 adds no new save data. No chests, pick-ups or enemies are placed (each would need a save entry).
- During a door warp saving and warping are blocked, then unblocked. That block is never saved.
- Castle story flags are never touched, and nothing is placed near the Ganon fight.

## Remove it

```
python tools/modman.py remove giruka.castle-date
python tools/modman.py deploy
```

Nothing stays in your save.

## Files

- `castle/`: our recipe (no game data): `mod.toml`, `layout.json` (where things go), `texts.json` (Zelda's lines).
- `tools/castle_build.py`: the builder. `tools/evfl.py`: our event flow reader and writer.
- Tests: `tests/test_castle_build.py`, `tests/test_evfl.py` (made-up data only).
