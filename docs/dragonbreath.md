# Dragonbreath

A weapon mod by giruka for Breathalyzer: **Dragonbreath**, a lost Goron blade. Goron master smiths forged it for
Daruk before the Calamity, but it was too light and slipped from his great hands. It rivals the Master Sword and is
faster. Daruk sealed it in a diamond cave on Death Mountain, because the King accepted only the Master Sword for
Zelda's guard. An extremely slim, katana-like blade of the finest steel with a dragon engraved along it.

Status: **stages 1 and 3 of 4 built and checked offline only. Not tried in the game yet.** Stage 3 (our own fast
moves, below) is built; stage 2 (the smith, the cave, upgrades) is not. Off by default, not in the Plus zip
(`ready = false` once it has an entry).

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

## Stage 3: our own fast moves

With Dragonbreath in hand, Link's one-handed attacks are **our own clips**, keyframed by us (giruka's design, our
own keys: no other game's animations, models, names, timing or effects, nothing traced). Snap cuts out of a low
stance, a full spin with two cuts, a front-flip finisher, a dashing lunge, a five-cut flurry with twirls, a crouched
quick-draw. Every other weapon keeps the game's own moves.

**Checked offline, 2026-11-18** (Savage Wind 0.4.1-alpha.4's own files, read only; no game run): all 12 clips
solved on Link's real skeleton (idle pose from `Nml_Wait`, the sword grip from the game's own `Sword_Attack_S1`)
pass every check below with no problem; the moves package builds (`dragonbreath_build.py build --no-add --out`
made all three mods), the archive reads back within the writer's 0.15 degrees, every wrapped set keeps the game's
own nodes unchanged, and the list log merges into the game's list (`SwDragon_Animation` added, no new sets). 24 unit
tests on made-up data; the emote tests (50) and stage 1's tests (25) still pass.

| Set (game file) | Our clip | The move | Frames | Hit window | Next attack from |
|---|---|---|---|---|---|
| Combo 1 (`CutS1`) | `SwDragon_s1` | low cross cut snapped out of the right hip, then a downward flick | 14 | 2-4 | 5 |
| Combo 2 (`CutS2`) | `SwDragon_s2` | reverse rising cut, the blade rests back over the shoulder | 14 | 2-4 | 5 |
| Combo 3 (`CutS3`), part 1 | `SwDragon_s3a` | full spin, arm out: the blade sweeps across the front | 5 | 2-4 | (plays on) |
| Combo 3 (`CutS3`), part 2 | `SwDragon_s3b` | the spin ends in a second cut across the front | 14 | 2-5 | 6 |
| Finisher (`CutSF`) | `SwDragon_sf` | crouch, tucked front flip, downward cut on landing | 24 | 7-10 | 12 |
| Dash (`CutDash`) | `SwDragon_dash` | drops low, lunges on the front leg, straight thrust | 20 | 2-5 | - |
| Flurry Rush (`CutJustRush`) | `SwDragon_rush1`..`5` | cross, return, half-twirl drop cut, rising cut, wide spin | 8-14 | 2-4 / 4-6 / 4-7 | - |
| Sneakstrike (`CutNinja`) | `SwDragon_iai` | sinks low, blade back along the hip, one flat rising draw-cut, holds | 24 | 6-9 | - |

**Speed:** the 4-set combo lands **5 hits in 33 frames**; the game's one-handed combo lands 4 in about 74. That is
**2.8x the hits per second** (the goal was about 3x). The tails after the "next attack" frame are flourishes: press
attack and they are cut off; stop and they play.

**How it is made** (`tools/dragonbreath_clips.py`, clip files in `assets/dragonbreath/clips/`):

- Each clip is a JSON file in the emotes key language (`docs/emotes.md`), plus `_poses.json` (shared stances:
  `ready`, `low`, `spin_out`, `tuck`). Extra fields: `slot` (the game's set), `part` (its place when a set has more
  than one clip: they play in a row), `hits` (one window, clip frames, its side, and `at`: the point in front of
  Link the blade must sweep through), `next` (the frame the next attack may start).
- **Shared tool fixes, made at the root** (`tools/emote_build.py`, every clip can use them; the emote tests pass):
  `blade` (point the held weapon: the hand turns so `Weapon_R` points there, kept near the forearm's line;
  optional `edge`), a fixed grip (`Solver(fixed=...)`: the game's own `Weapon_R` sword grip on every frame), `spin`
  (turn the whole solved pose about the hips: spins and flips; a spin goes on the hip's children and a flip on the
  hip bone so the Euler curves never sit at the 90-degree middle angle), `max_turn` (the per-frame joint limit,
  settable for fast moves), big arm swings (over 150 degrees) now turn by way of the front (before, an arm raised
  straight up got a twist that flipped with a millimetre of difference, so a clip could snap it round in one frame),
  the knee check follows the hips' facing (it was fixed to +z, wrong in a spin), and the archive writer and the list
  log are shared functions (`write_anim_archive`, `anim_list_log`) that the emotes and Dragonbreath both use.
- **The archive** `SwDragon_Animation` (12 clips, about 190 KB; read back within 0.11 degrees) is added to Link's list (`AddRes`, the same merge as
  the emotes and The Linkle Mod). **No new sets**: each game set (`Player_CutS1.bas`...) gets a weapon sub-type
  selector on top (`TypeIndex 97`, the node the game uses for the Katana and the Master Sword): `Dragonbreath` plays
  our clip node (or a sequence node with our parts), `default` the game's own tree, whose nodes stay byte for byte
  (only moved down one place). Our nodes are copies of the game's own one-handed node in that file: every event
  kept, moved onto our frames (the hit window and the next-input frame exactly, the rest in step), rate 1.0, blend 2
  frames. The flurry's sequence keeps the game's child count (it repeats the last cut once, as the game's does).
- **Package:** `giruka.dragonbreath-moves` (hidden, `part_of` the Dragonbreath switch: used only while it is on).
  `dragonbreath_build.py build` makes it with the other two; `clean` takes it out with the switch's mod. With the
  switch off, the sets are the game's and the blade swings the normal one-handed moves.

**The checks** (`python tools/dragonbreath_clips.py check`, also part of `dragonbreath_build.py check`; on every
frame of every clip):

| Check | Limit |
|---|---|
| The emotes' pose checks | no joint jumps beyond the clip's `max_turn` (85 here, 90 for the flip; deliberate snaps marked `hit` skip it), no hand in the body, elbows at most 155 degrees, knees never backward (now facing-aware), planted feet stay put |
| Joint limits | knees at most 150 degrees, the wrist at most 80 degrees off the forearm's line, the neck at most 75 degrees off the idle pose |
| Feet | ankles and toes never more than 2 cm below the floor; on the ground on the last frame |
| The blade | never through the body's middle line (7 cm); inside the hit window it sweeps within 25 cm of its hit point (ours: 2 to 16 cm) and turns at most 50 degrees a frame (ours: 8 to 50) so a fast window can't skip past a target |
| The sets | the game's nodes unchanged, the hit window at least 3 frames, the next attack after the window |

Pictures: `python tools/dragonbreath_clips.py preview` writes one PNG per clip to `out\research\dragonbreath-clips\`
(every frame from the front and from Link's right, the blade in orange with its edge in green, the hit window's
frames on yellow, the hit point as a green cross). `--demo` uses the made-up skeleton (no game files).

**Not done in stage 3** (next steps): the draw flourish on `WeaponEquipOn` (an upper-body set; its tree needs a
look first), the `_Advance` twins (the game picks a "moving" clip by speed under a `T16` node; ours plays the same
clip standing or moving), the charged attack (`CutCharge`: stage 4a), and our own clips for the jump, fall and
horse attacks (they keep the game's moves).

### Game check for stage 3 (not run yet; about 20 minutes)

Warn first; one game; the test save only; close with `exit`; then read the log. Deploy with the switch on
(`dragonbreath_build.py build --on`, `modman.py deploy`).

1. **It loads**, and the other one-handed swords still swing the game's moves (the Traveler's Sword first).
2. **Each clip plays from its input** with Dragonbreath: the 4-hit combo, the dash attack (sprint + attack), a
   Flurry Rush (perfect dodge), a sneakstrike (crouch behind a Bokoblin). Note any that plays the game's move
   instead (the selector didn't pick `Dragonbreath`) or freezes Link in place (the clip didn't load).
3. **Hits land** on a Bokoblin on every window: count hits per combo (5 wanted: the spin's two cuts both count),
   the flip finisher's landing cut, the dash, all 5 flurry cuts.
4. **Speed:** time the 4-set combo (video or the log): about 33 frames wanted. If the game holds the next attack
   longer (its own minimum gap between combo steps), raise `next` in the clip files and note the real speed here.
5. **The combo's third set** (`CutS3`, two parts in a sequence node copied from the Flurry Rush's): both parts
   play (spin, then the second cut). If only the first plays, or the set ends early: merge `s3a` and `s3b` into one
   clip with one longer window (one hit instead of two).
6. **Looks:** the sword sits in the hand as in the game's moves (the grip is the game's); no arm or wrist snapping;
   the flip doesn't sink into slopes or float; the spin doesn't clip the shield. The blade's edge: if the back leads
   the cuts, that is the model's `edge_side` (stage 1 check 3).
7. **The dash moves Link:** ours has no root motion (the game's dash moves the `Root` bone about 2.3 m). If Link
   lunges on the spot, add root motion (a field in the key language) and build again.
8. **Shield and guard** still work between attacks; a parry; with The Linkle Mod and the Emotes mod on too (all
   three add an archive to Link's list and the list merge keeps them all: checked offline; Emotes and the moves
   both put files into Link's player pack, which deploy merges file by file: not checked offline).
9. **Switch off:** deploy with the Dragonbreath switch off: the blade swings the game's normal one-handed moves.

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
- **Friends see the game's moves** (stage 3): a ghost sends the set's name (`CutS1`...) and the friend's game plays
  its own clip for it, so friends see normal one-handed swings at our timing. Showing ours needs the ghost to send
  the held weapon's sub-type too (a ghost change, with its owner).
- **Fast moves past what data allows**, only if the stage 3 game check shows them: a minimum gap the game's AI
  keeps between combo steps (then the combo can't reach 2.8x by data), or a dash that needs root motion the AI
  ignores.

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
   -3230) with a stone chest that holds the blade, in place of the test spot. (The sped-up stock swings are no
   longer needed: stage 3's own clips came first.)
4. Stage 2: the Goron smith (Rohan) tips Link about the diamond cave; the cave's monsters and diamonds; Daruk's
   first-draw scene; upgrades with rare ores (levels `_1` to `_4`, the Guardian weakness shrinking); **re-buy for 8
   diamonds** if it is lost.
5. Stage 3 is built (above): **the user looks at the clip pictures** (`out\research\dragonbreath-clips\*.png`,
   `python tools/dragonbreath_clips.py preview`), then the stage 3 game check. Left for later: the draw flourish
   (`WeaponEquipOn`), the `_Advance` twins, root motion for the dash if the game check needs it, our own jump and
   fall attacks. `docs/emotes.md` should list the new key fields (`blade`, `spin`, `max_turn`; described in
   `tools/emote_build.py` next to its field list) for other clip makers.
6. Stage 4: the native mod: the energy and recharge, the special mode with its effects and camera, big characters.
7. Our own scabbard (saya) model in place of the donor's sheath.

## Files

- `tools/dragonbreath_build.py` (the builder), `tools/dragonbreath_model.py` (the model and textures, in code)
- `tools/dragonbreath_clips.py` (stage 3: the clips, their checks and pictures, the wrapped sets, the moves package)
- `assets/dragonbreath/`: `dragonbreath.json` (the recipe: stats, tags, texts, blade numbers, test spot),
  `mod.toml` (the switch), `item.toml` (the item), `clips/` (our attack keyframes: one file per clip, `_poses.json`)
- `tests/test_dragonbreath.py` (a made-up game; no game files), `tests/test_dragonbreath_clips.py` (made-up clips,
  sets and the made-up skeleton)

## Credits

Idea, design and lore: giruka. Mod: giruka (Breathalyzer). The model, textures and dragon are ours, made in code.
