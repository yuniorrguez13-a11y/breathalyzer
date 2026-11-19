# Dragonbreath

A weapon mod by giruka for Breathalyzer: **Dragonbreath**, a lost Goron blade. Goron master smiths forged it for
Daruk before the Calamity, but it was too light and slipped from his great hands. It rivals the Master Sword and is
faster. Daruk sealed it in a diamond cave on Death Mountain, because the King accepted only the Master Sword for
Zelda's guard. An extremely slim, katana-like blade of the finest steel with a dragon engraved along it.

Status: **stage 1 of 4, built and checked offline only. Not tried in the game yet.** Off by default, not in the
Plus zip (`ready = false` once it has an entry).

The full plan (4 stages, the game checks, the open questions): `out\research\dragonbreath-plan.md` (not in git).

**Checked offline, 2026-11-18** (Savage Wind 0.4.1-alpha.4's own files, read only; no game run):
`dragonbreath_build.py check` all good; a build read back (attack 40, life 1000, `IsLifeInfinite` on, not throwable,
sub-type `Dragonbreath`, Guardian ratio 0.25 with the 9 Guardian Scout names and the Guardian tags, tags as below,
plain-iron chemistry, the donor's sheath); `modman add` of both mods into a scratch library merged the actor info,
flags, texts and map with no refusal; 25 unit tests on a made-up game. Listed in `tools/packaging/bundled-mods.toml`
(not ready, off by default). The donor's 16_16 unorm UVs are written by ak47_build's shared model writer, so any
weapon mod on such a donor works. Follow-up: a new recipe mod needs `build --on` because the launcher adds no enable
step for it; that belongs in the launcher, not in each mod.

## Stage 1: the blade itself

| What | How | Checked |
|---|---|---|
| Model | Our own, made in code (`tools/dragonbreath_model.py`): a 2.2 cm to 1.5 cm wide, 6 mm thick blade with a ridge line, a 1.5 cm curve and an angled tip; a gold collar, a round guard with three claw notches, a red-wrapped handle, a gold cap. About 1,060 triangles. Same length as the donor sword, handle where the donor's is, so Link's hands and the reach stay right | offline: shape, size, faces point out, the model writer's self-check |
| Textures | Painted in code: polished blue-white steel, a wavy temper line along a bright edge, the dragon engraved on both faces (a long serpent body with scales, spines, horns, the head toward the tip, flame curls), a groove in the ridge face, red cord over black on the handle, Goron gold. Colour, normal (the engraving sinks in) and shine, in the donor's formats and sizes. **No glow and no fire**: those come only with the special mode (stage 4) | offline: the texture writer's self-check, sizes and formats equal the donor's |
| Icon | Drawn from our model, diagonal like the game's | offline |
| Actor | `Weapon_Sword_Dragonbreath`, made from the **Royal Guard's Sword** (`Weapon_Sword_047`): a plain metal one-handed sword (no glow masks, no fire chemistry). Its actor link, model list and stats file renamed to ours | offline |
| Stats | Attack **40**. Never breaks: `General.IsLifeInfinite = true` (the game's One-Hit Obliterator uses it on a sword) and life 1000 as a backstop. Can't be thrown. No random "+" versions. Sub-type `Dragonbreath` (for the fast moves in stage 3; until then every sub-type choice falls back to the normal one-handed moves). **Weak against Guardians**: the ancient weapons' own Guardian lists with a ratio of 0.25 (Ganon and the Blights left out) | offline: the stats file reads back with these numbers |
| Tags | Dropped: Hyrule Compendium, Blood Moon revival, amiibo. Added: can't be sold, never revives, never in random drops, the big "item get" fanfare. It **can** be dropped (the user's design: lose it, re-buy it) | offline |
| Texts | "Dragonbreath" and its description (every language gets the English text) | offline: modman's text merger reads it |
| Flags | `IsGet_Weapon_Sword_Dragonbreath` and the test spot's pick-up flag, both in the item mod | offline |
| Getting one to test | One lies **near the test save's start** on the Great Plateau (`x -1031, z 1812`, 3 m from where the checks stand Link). With Ghost multiplayer on (developer commands): `ghost give Weapon_Sword_Dragonbreath` puts one in front of Link | offline (spot: terrain height, nothing within 2 m, a free HashId) |

The plan's other stage 1 parts are **not done yet**: the cave north of Goron City with the chest, and the sped-up
placeholder swings (the plan's `.bas` wrap). See "Next steps".

### Two mods

- **`giruka.dragonbreath-item`** (secret, never listed): the actor, model, textures, icon, texts and flags. Once built
  it **stays on**, even while the Dragonbreath switch is off, so a save that holds the blade keeps loading.
- **`botw-mod-loader.dragonbreath`**, the switch "Dragonbreath": **off by default**. For now it holds only the test
  spot. Later stages put the cave, the Goron smith's quest and the fast moves here.

### Build it

```
python tools/dragonbreath_build.py build --on     build both, add them to your library, switch on
python tools/dragonbreath_build.py check          offline checks on your files (reads only, changes nothing)
python tools/dragonbreath_build.py preview --out blade.png   a picture of the blade (no game files needed)
python tools/dragonbreath_build.py where          the game files it is built from
python tools/dragonbreath_build.py clean          take the switch's mod out of the library (--item: the item too)
```

Then `python tools/modman.py deploy`. Without `--on` a new switch is added **off**; a rebuild keeps on/off as they
were. `build --no-add --out <folder>` only writes the two mod folders. `--optional` makes a build that can't be made
end with exit code 0; `--quiet` prints one line (both for the launcher). Build it again after a Savage Wind update.

Nothing from the game is in this repo or in a release: `tools/dragonbreath_build.py` makes everything on your PC
from your own `extracted\romfs` (only read), as BCML-style packages that `modman add` merges the normal way.

### How it is made

- **The actor**: the Royal Guard's Sword's pack with its actor link (`ModelUser`, `GParamUser`, name, tags) and
  model list renamed. Our stats file takes the place of the donor's (it is left out of our pack, so no two packs hold
  one path with different contents). Everything else (physics, sounds, effects, the one-handed moves, its sheath on
  Link's back) is the donor's, under the donor's names.
- **The stats file**: the donor's with attack, life, `IsLifeInfinite`, `IsThrowingWeapon`, the "+" chances and
  bonuses (all 0), `WeaponSubType` and `SpHitActor` / `SpHitTag` / `SpHitRatio` changed. The Guardian lists are read
  from your game's Guardian Sword (`Weapon_Sword_013`) at build time.
- **Actor info**: the donor's entry renamed (model bounds, sort key) with the same stats repeated (`attackPower`,
  `generalLife`, the "+" values), no Compendium spots.
- **The model**: the donor's model file (its `Root` bone and its one material) with our mesh; the textures in the
  donor's formats (BC1 colour, BC5 normal and shine) and sizes, with mipmaps.
- **The map**: one blade lying on the ground near the plateau start (a loose `_Dynamic` map unit), its height read
  from your terrain files, a HashId neither the game's map nor another library mod uses.

## What needs native code later

Data can't do these parts of the design; they wait for stage 4's native mod (`native/examples/dragonbreath/`):

- **Damage that drops with use down to 10, then a 10-minute recharge, then full again.** The game's Master Sword
  recharge belongs to the Master Sword's own AI, not to data. Plan: the native mod counts landed hits as "energy",
  lowers the damage through the hit function (`0x7000b952b8`, with its limits: it also runs for weapon wear, and
  chained overrides with co-op), and runs the 10-minute timer.
- **The special mode** (charged by fighting, Genshin-burst-like: stronger and faster for a while, ember and fire
  effects only then), its camera cut-in and afterimages.
- **Big characters** (play as Daruk, a Moblin...): the blade slips out of his hand after 2-3 attacks.
- **The Guardian weakness**, only if the data ratio below 1 turns out to be ignored (game check 6).

## Game check for stage 1 (about 15 minutes, not run yet)

Warn first; one game; the test save only; close with `exit`; then read the log.

1. **It loads**: the game starts and the test save loads with the mods deployed (the first new weapon actor we
   try in the game: the AK-47 is still untested too).
2. **Picked up**: walk to the blade near the start: it lies on the grass, "Take", the fanfare, it is in the pouch
   with its icon, name "Dragonbreath" and description.
3. **Looks**: equipped, slim with the engraving visible, no glow; on the back it sits in the donor's sheath. The edge
   leads the swing; if the back leads, set `"edge_side": -1` in `assets/dragonbreath/dragonbreath.json` and build
   again.
4. **Swings**: the normal one-handed moves (no new moves before stage 3), hits land on a Bokoblin, 40 attack.
5. **Durability**: after 50 hits the bar hasn't moved (`IsLifeInfinite`); if it has, the 1000 life still keeps it
   going for a long time, and the doc says which.
6. **Guardians**: against a Guardian Scout or Stalker it does clearly less damage than against a Bokoblin
   (`teleport` to one; never save after).
7. **The switch**: save; turn the Dragonbreath switch off, deploy, load: the save loads and the blade is still in the
   pouch (the item mod stays on); the test spot's blade is gone from the ground.
8. Optional, on a **throwaway copy** of the test save: deploy without the item mod too. Does the save load? (What a
   player who removes Breathalyzer gets.)

## Next steps

1. **The user looks at the preview** (`python tools/dragonbreath_build.py preview --out blade.png`) and approves the
   look or asks for changes (blade width, curve, the dragon).
2. The stage 1 game check above.
3. Stage 1, rest of the plan: the cave north of Goron City (`FldObj_CliffRedRock_Cave_A` at about 1960, 527.7,
   -3230) with a stone chest that holds the blade, in place of the test spot; the placeholder fast swings (a
   developer build only: the user ruled out sped-up stock swings for players).
4. Stage 2: the Goron smith (Rohan) tips Link about the diamond cave; the cave's monsters and diamonds; Daruk's
   first-draw scene; upgrades with rare ores (levels `_1` to `_4`, the Guardian weakness shrinking); **re-buy for 8
   diamonds** if it is lost.
5. Stage 3: our own keyframed attack clips, about 3x the Master Sword's speed (spins, flips, flurries, quick-draw).
   Our own keys only: never another game's animations, models, names or effects.
6. Stage 4: the native mod: the energy and recharge, the special mode with its effects and camera, big characters.
7. Our own scabbard (saya) model in place of the donor's sheath.

## Files

- `tools/dragonbreath_build.py` (the builder), `tools/dragonbreath_model.py` (the model and textures, in code)
- `assets/dragonbreath/`: `dragonbreath.json` (the recipe: stats, tags, texts, blade numbers, test spot),
  `mod.toml` (the switch), `item.toml` (the item)
- `tests/test_dragonbreath.py` (a made-up game; no game files)

## Credits

Idea, design and lore: giruka. Mod: giruka (Breathalyzer). The model, textures and dragon are ours, made in code.
