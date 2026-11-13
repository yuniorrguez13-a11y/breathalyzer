# Who am I: pick who your friends see you as

Like a ped selector: you pick a character on a wheel, and your friend sees you as that character in their game.
Tier 1, 2026-11-12. Tier 2 (2026-11-12): [you see yourself as your pick too](#see-yourself-as-your-pick), live in the
game with no restart, or [at the next start](#play-as-at-the-next-start-the-restart-way) as a fallback.

**Status (2026-11-13):** in the game, Hylian, Paya, Bokoblin and Moblin load as ghosts and as your live self;
Zelda, Urbosa, Daruk, Revali and Mipha didn't load (2026-11-12 and -13: their model lists' animation targets, see
[animation targets](#animation-targets)); rebuilt with the fix, not yet checked in the game. Daruk works as your
own body at the next start ([play as](#play-as-at-the-next-start-the-restart-way)). The wheel and the network part
are tested with made-up data only. The [in-game check](#in-game-check) has the details.

## Use it

1. **Build the characters once** (each PC builds its own, from its own game files):

   ```
   python tools\charsel.py build
   python tools\charsel.py check
   ```

   This writes a RomFS mod folder, `out\charsel-packs` (`mod.toml`, `romfs\`, `charsel.json`, `built.txt`). Add
   it to your mods like any mod (`python tools\modman.py add out\charsel-packs`, then Play or `deploy`). **Only
   characters in your game are shown:** the client and the wheel read the library copy (turned on) and keep only the
   packs that are in the deployed overlay; anything else is the Link body. It is small (9 packs, 14-19 KB each
   unpacked, about 65 KB packed in all). Never share it: your friend builds their own. `built.txt` marks it as made
   from your game's files, so Export as .breath refuses it (the Link body's folder has one too).

   `charsel build zelda` rebuilds only Zelda and keeps the others from the last build (when their recipe hasn't
   changed); `charsel build` with no names rebuilds all of them.

2. **Pick who you are.** Any of these:
   - the Multiplayer menu (`tools/mp/lobby.py`): **4  Who am I**, then the wheel. Keys 1-4, or Up/Down to move the
     big `>` and Enter. A controller works the same way (d-pad or left stick, A to pick); its B goes back, and on the
     first screen B does nothing (only the keyboard's Esc there means Play solo);
   - `python tools\charsel.py wheel`: the wheel in its own window (arrow keys or a controller's d-pad, Enter or A to
     pick, Esc or B to go back);
   - `python tools\charsel.py pick zelda`;
   - in the game: open the console (F12) and type `iam zelda`. `iam` alone lists the names, `iam next` and
     `iam back` go round the wheel. The `iam` command itself works without developer mode, but the multiplayer
     client gives it the names and reads your pick through the game's control port, which turns developer mode on.
     (The game already has a `look` command for the camera, so ours is `iam`.)

   Your pick is kept in `%LOCALAPPDATA%\Breathalyzer\charsel.json`.

3. **Play.** The multiplayer client says who you are when it joins (`--look NAME` overrides your pick, and an `iam`
   typed before the client started never wins over it; one typed while it runs is a new pick). To see your friends
   as the ones they picked, and to take `iam` from the game, add `--ghost-looks`:

   ```
   python tools\mp\client.py --relay 192.168.1.20 --name Ana --ghost-looks
   ```

   `--ghost-looks` uses the Link body for anyone whose pick isn't built on your PC, so build the Link body too
   (`python tools\mp\ghost_link_pack.py build`, [ghost](ghost.md)).

## The wheel

Three groups. Left and Right go to the next character, Up and Down to the next group. The middle shows the name in
big letters with the race ("Daruk (Goron)"), and under it the group and **Ready** (in your game: built, added and
deployed), **Build first**, or **Needs a mod**. Under the wheel: who others see, the honest "looks" line (below) and
the keys. All text is 18 pt or larger, white on dark green, blue and red (contrast 5:1 or more); on a scaled screen
(150-200%) the wheel grows with the text, up to the screen's height.

| Group | id (what you type) | Name | Built on actor name | Own clips + Link's | Loads |
|---|---|---|---|---|---|
| Hylians | `link` | Link | the Link body (`Dm_Npc_Sheiker_Searcher_M_001`) | 33 Link | 0 MB |
| Hylians | `linkle` | Linkle | the Link body, when the Linkle mod is on that PC | 33 Link | 0 MB |
| Hylians | `zelda` | Zelda | `Dm_Npc_Sheiker_Searcher_M_000` | 6 + 27 | 10.6 MB |
| Hylians | `hylian` | Hylian (a Hateno villager) | `Dm_Npc_UMii_Hylia_BodyS_M_001` | 6 + 27 | 11.5 MB |
| Races | `sheikah` (`paya`) | Paya (Sheikah) | `Dm_Npc_UMii_Hylia_BodyS_M_002` | 6 + 27 | 6.4 MB |
| Races | `gerudo` (`urbosa`) | Urbosa (Gerudo) | `Dm_Npc_Handmaid_B` | 6 + 27 | 16.2 MB |
| Races | `goron` (`daruk`) | Daruk (Goron) | `Dm663_Npc_Assasin_A` | 6 + 27 | 10.9 MB |
| Races | `rito` (`revali`) | Revali (Rito) | `Dm_Npc_UMii_Hylia_BodyS_M_011` | 7 + 26 | 10.6 MB |
| Races | `zora` (`mipha`) | Mipha (Zora) | `Dm_Npc_UMii_Hylia_BodyS_M_003` | 9 + 24 | 11.0 MB |
| Monsters | `bokoblin` (`boko`) | Bokoblin | `Dm663_Npc_Assasin_B` | 26 + 0 | 6.2 MB |
| Monsters | `moblin` | Moblin | `Dm663_Npc_Gerudo_S_B` | 26 + 0 | 7.8 MB |

"Loads" is what the game reads to show that body (model, textures, animation files) when no real one is nearby
(from the study's size-table read). Urbosa is the heaviest: on a slow laptop pick a lighter one.

## Animation targets

Zelda, Urbosa, Daruk, Revali and Mipha were never created ("the game could not create it"), in two in-game checks
(2026-11-12 and -13), on six different free names, with and without Link's clips retargeted. Their donors' model
lists (`Dm_Npc_Zelda_Sibyl`, `Dm_Npc_*_Hero`) have four animation targets of types 0, 0, 3, 2; every pack that loads
has 0, 1, 3 (Paya, the Hylian, the Link body) or only type 0 (the monsters). The game sets up a type-2 target apart
from the others, and it only ever has one together with a look file and an animation info: all 132 of its NPC packs
with a type-2 target have both, and ours give neither. So `charsel build` gives a model list with a type-2 target
the Link body's block instead (types 0, 1, 3 on `Root`, `Spine_1` and `Face_Root`: the same as Paya's own list), after
checking the body has those bones. The face keeps still. `charsel.json` says `anim_targets: "link-body"` for those,
the build says "(Link body's anim targets)", and `charsel check` refuses a pack that still has a type-2 target.

## The heap

Each free actor name has its own heap (the game's ActorInfo `instSize`: 554 KB to 1.1 MB). After the first check it
looked like the five big skeletons outgrew it (Zelda 179 bones, Urbosa 161, Mipha 151, Daruk 144, Revali 126, times
about 1,400 retargeted clips); the second check failed them the same way without that, and the animation targets
explain all of them. So the heap rule is only a precaution now [G]: `charsel build` estimates each pack's need (about
320 KB plus 4 bytes per retargeted clip and model bone) against its name's heap, and a body that would outgrow it gets
Link's animation file **without** the retarget (Link's clips only turn bones, and these bodies' main bones have
Link's names, parents and rest turns; the hip height comes from Link's clips as it is: `ghost puppet lift`). Their own
walk and run keep the retarget. `--link-retarget on` keeps Link's file retargeted on every body (to try in the game:
it fits the clips to each body), `off` drops it everywhere. Urbosa is on `Dm_Npc_Handmaid_B` (902 KB; the tall names
have 554 KB). `charsel.json` says per character `link_retarget` and `heap` (`need`, `have`, `bones`); the build says
"(not retargeted)". Not the size table: the game has entries for all their model and texture files, larger than the
files, and the four that load have no pack entry either.

## What looks right, what may not

- **Link and Linkle: right.** The Link body works in the game ([ghost](ghost.md), tenth round). On a PC with the
  Linkle mod, the Link body *is* Linkle (the mod replaces Link's model). So:
  - your friend has Linkle and you pick Linkle: they see Linkle;
  - your friend has no Linkle mod: they see Link (the log says why);
  - **wrong:** your friend has the Linkle mod and you pick Link: they see Linkle too. `build --model-copies` fixes
    this with a renamed copy of each model on its own name, but a new model file without a size-table entry may
    not load [G], so it is off until the in-game check tries it.
- **People (Zelda, Hylian, Paya, Urbosa, Daruk, Revali, Mipha):** their own stand, walk, run, sprint, crouch and
  stagger; Link's clips for everything else (attacks, bow, jump, glide, climb, swim, death), played through the game's
  own retarget, which the game already uses to play Link-skeleton clips on other people [V]. Daruk sprints as a
  rolling ball, Revali glides with a Rito flying clip, Mipha swims with Zora clips. **Not known until the game run
  [G]:** whether the hips float or sink on Link's clips (the study measured Mipha +0.24 m, Urbosa -0.35 m from
  Link's height), and whether a Goron body twists on Link's climb and glide.
- **Monsters (Bokoblin, Moblin):** their own 26 clips: walk, run, jumps, many attacks, bow, guard, hurt, a lying
  death. No climb, glide or swim pose: Link's clips on their skeletons may twist (their spine and neck start 20-60
  degrees off Link's) [G], so they are left out; the mod then shows Wait, Walk or Run by speed. `build
  --monster-link-clips` puts Link's clips in, for the game check to judge.
- **Everyone:** feet slide a little (clips play in place); skirts, capes and hair are stiff (no physics); faces don't
  move; held items need the hand bones `Weapon_R` and `Weapon_L`, which every body here has [V], but holding is only
  checked on the Link body. Big bodies (Moblin, Urbosa) keep a person-sized box, so they may pop in at the screen's
  edges; every ghost is removed at about 75 m.
- **Cutscene names vs NPC names [G]:** three characters (Zelda, Daruk, Bokoblin: one per family) sit on cutscene
  names of the same kind as today's ghost, which works. The other six sit on unused NPC names; whether an NPC-class
  actor with no NPC brain behaves is the main question of the game run. If one never loads, the client shows that
  player as the Link body for the rest of the session and says so. Moving a character to another free name:
  `charsel build --slot rito=Dm_Npc_Handmaid_A`.
- **Size table [G]:** the free names have no size-table entry for their pack. Small new files without one loaded fine
  in the game (the Link body's own animation sets); a whole pack without one is checked in the game run.

## How it works

- **A character pack** is a tiny actor pack (14-19 KB, 6-8 KB packed) built from your own files
  (`tools/charsel.py`), like the Link body (`tools/mp/ghost_link_pack.py`): the cutscene Sheikah man's actor link with
  our model list (the donor's own), our animation list (the family's animation files, then Link's, sorted names,
  blend times) and one small animation set per ghost name (the 33 of the Link body). No AI but the cutscene dummy's,
  no physics. Bodies the game puts together from a look file (the Hylian) keep that file. Each pack sits on one free
  actor name: in the game's ActorInfo, with no pack anywhere in the game (checked at build against your ActorInfo:
  class, heap of 500 KB or more, no pack). The profile matches the name's class (DemoNPC or NPC).
- **Recipes are names only** (the game's file and clip names, as `ghost_link_pack.ANIMS`). Every clip a character
  uses as its own is looked up in your files at build; a missing one or a clash with one of Link's (a family file
  that has a Link clip name too) skips that character, with the reason. `charsel.json` in the output says what was
  built where, and why anything was skipped. `charsel check` reads every pack back: one actor link on the right
  name, sorted animation names, one clip each, the cutscene dummy's AI, no physics, no attention list and no damage
  parameters (so a ghost or your puppet can never be locked on to, talked to or hit), the right profile, under 64 KB.
- **On the network** only the id travels (feature `look`, protocol version 1, [multiplayer](multiplayer.md)): the
  hello carries `"look":{"id":"zelda","v":1,"name":"Zelda"}`, a new pick goes as a `look` message (the client sends
  at most one every 2 s; the relay takes one 1.5 s after the last, for network jitter, and if it still says "too
  often" the client sends the newest pick again), and the relay keeps the latest for late joiners. Each receiver
  turns the id into a body through its **own** catalog of what is deployed (`charsel.here_catalog`, `Looks`): a raw
  actor name from the network never reaches `ghost spawn`. Anything it can't show is the Link body, with one line in
  the log: "Ghost: Ana is Zelda (shown as Link: not built here)." `v` (the recipe's version) travels for the log
  only; a rebuild keeps a pack only while its `v` matches.
- **With three or more players** the ghost shows the nearest, but it changes to another only when that one is 2 m
  nearer for 1 s, so two friends at about the same distance don't swap bodies back and forth.
- **In the game,** `iam` (ghost mod 0.8.0, [ghost](ghost.md#who-am-i-080)) keeps the word you typed; the client
  reads it from the mod's state every 2 s, tells the relay, and keeps it as your pick. The client then tells the
  mod which `iam_seq` it saw (`ghost looks <list> <pick> <seen>`), and the mod clears the word, so an old `iam` never
  comes back after a newer pick (a client started later with `--look`, or the wheel). `iam` says "Your friends see it
  in a few seconds" only while a client has asked the mod within 6 s; otherwise it says the client sends it when it
  runs. With `--no-look` the client gives no list.

## See yourself as your pick

**Live, no restart (ghost mod 0.9.0, `native/examples/ghost/puppet.c`).** Type `iam paya` in the game's console
(F12): you see yourself as Paya within a second, and your friends do too. `iam link` brings Link back. `iam self off`
keeps your friends' view but shows you Link (`iam self on` again). `iam` alone says what you see.

**In the game, 2026-11-12 (`out\showcase-lib`):**

- **Works for the bodies whose pack loads:** `iam paya`: the puppet spawned in 0.6 s, Link was hidden, it played `Wait`
  and held Link's weapon and shield. Those are Hylian, Paya, Bokoblin and Moblin (the same as the character check).
- **Daruk does not load live:** "Daruk did not load (the game could not create it): you stay Link", after 5 tries,
  and `iam` says to restart with `charsel playas daruk`. The same for Zelda, Urbosa, Revali and Mipha, on any free
  name (the character check moved Zelda and Daruk to names that work for others: still refused). The cause: a
  type-2 animation target in their model lists ([animation targets](#animation-targets)); rebuilt 2026-11-13, not yet
  checked in the game. Until
  then, **for these five, the restart way below** works (Daruk in the game, rolling sprint and all).
- **The ghost mod must be the only frame-hook mod.** With Infinite Stamina on, the ghost mod never runs (only the
  first DLL by file name that wraps the game's frame job runs, [game basics](game-basics.md)): `iam daruk` did
  nothing at all. `iam` now says so ("the ghost mod isn't running ... Infinite Stamina?"), and `modman deploy` warns
  when both are on. Infinite Stamina is off in `out\showcase-lib` for this.

How it works:

- Link is hidden with the game's own model show/hide (bit 0 of each model unit's flags: the game's own "show actor"
  sets it, its hide actions clear it). His armor and held items copy his model's draw flags every frame, so they go
  with him [V code, G in the game]. If the game shows him again by itself, he is hidden again the next frame
  (`rehides` in `ghost puppet`).
- A "self puppet" is spawned: the same character pack your friends see you as (`charsel build`, added and deployed).
  Every frame it is put exactly where Link is (place and turn) and plays the character's clip for what Link does:
  its own clips first (Daruk sprints as his rolling ball: Link's `Move` from 6.9 m/s is `RunGear2`; Revali glides
  with Rito wings; Mipha swims with Zora clips), Link's clips where it has none, and a near name for the rest of a
  family (a climb jump plays `ClimbMove`). It is shown only once it plays a clip (never a T-pose).
- The camera stays on Link (nothing moves him). What Link holds in his hands (a drawn weapon, a raised shield, an
  aimed bow) is copied into the puppet's hands (the ghost's held items) while no friend's ghost is up; the copy comes
  0.3-0.8 s after he draws it [G] and goes when he puts it away. With a friend's ghost up, the puppet's hands are
  empty. Nothing goes on the puppet's back: Link's back places on a bigger body poked out of it (Daruk's weapon over
  his head, 2026-11-13). Link's own sheathed items are hidden with him (each weapon's frame update copies its
  holder's draw flags [V code]).
- The puppet can't be locked on to, talked to or hit (no attention list, no damage, no physics in its pack). The
  green ring that shows next to it while you sprint or climb is Link's own stamina wheel, where it always is.
- **Link shows instead** while he can't be mirrored: loading, cutscenes, riding a horse, a shield surf, and a state the
  character has no clip for (a climb or swim with no climb or swim clip). The pause menu keeps what was shown. After a
  load or a death the puppet is spawned again on the new stage.
- A pack that never loads (not built, not deployed, or the game refuses it) is tried 5 times; then you stay Link and
  `iam` says: "Daruk did not load (...): you stay Link. Restart the game to play as Daruk: python tools\charsel.py
  playas daruk".
- Developer mode: `ghost puppet` (its state: `shown`, `link_hidden`, `as_want`, `as`, `why`, `rehides`...),
  `ghost puppet lift <m>` (raise or lower it, for a hip that floats or sinks), `ghost puppet retry`, `ghost puppet
  off|on`, `ghost puppet map id=Actor,...` (another actor name per character).

Not checked in the game yet [G]: moving, climbing, gliding and swimming with a live puppet, whether the game shows
Link again by itself (`rehides`), his shadow, one frame of lag, the puppet's hip height.

## Play as, at the next start (the restart way)

The fallback when the live way can't show a character: the character's body becomes **your own Link's** model, the
way The Linkle Mod does it. Built on your PC from your own files; it needs a restart.

```
python tools\charsel.py playas daruk     build it, check it, add it to your mods (last, on)
python tools\charsel.py playas           what you play as now
python tools\charsel.py playas off       back to Link from the next start (and Linkle back on)
python tools\charsel.py playas check     read the built mod back (no game)
```

Or on the Who am I wheel (the lobby's 4, or `charsel wheel`): **P** (a controller's **Y**) on a person builds it in
the background and says "Done. Restart the game to play as Daruk."

- People only: Zelda, Hylian, Paya, Urbosa, Daruk, Revali, Mipha. Their 21 main bones have Link's names, parents and
  rest turns, so Link's 1,167 clips drive them. Monsters are not offered (their spine, neck and toes start 20-60
  degrees off Link's).
- What it builds (`out\charsel-playas`, a BCML-style package: only the changed files of `TitleBG.pack`; modman fills
  the rest from your game):
  - `Model/Link.sbfres`: Link's 121 bones first, in his order, with his parents; the body's own bones after them
    (Daruk: 171 bones), each keeping its rest pose in model space; Link's 11 shapes shrunk to one point (his
    materials and hide groups stay, so armor and face code still find them); the body's shapes on top.
  - `Model/Link.Tex.sbfres`: Link's textures plus the body's.
  - `Model/Armor_Default.sbfres`: the default hair, belt and trousers shrunk to a point, so nothing pokes through.
  - The player's own animation list and sets: the character's own sprint, glide or swim (Daruk: `Nml_Move_Dash`
    -> `Common_Move_Ball`, his rolling ball). `--no-own-moves`: Link's moves only.
  - Size-table entries for every grown file (`logs/rstb.json`).
- **The Linkle Mod** also replaces Link's model: while a play-as mod is on, it is turned off (said plainly), and
  `playas off` turns it back on.
- **Armor you wear shows on top**: every armor but the default is cut for Link's body, so it floats or clips on
  another body. Take it off for the best look. The body also shows in the pause menu and in cutscenes.
- Also [G] until the game run: feet slide a little (stride is Link's), the face doesn't move, tall bodies keep Link's
  hitbox (Daruk's head goes through low ceilings), and hips may float or sink on Link's clips.
- **In the game (Daruk, 2026-11-12, `out\showcase-lib`): works.** Daruk is the player from the start: standing (front
  and back look right), and the sprint is his rolling ball. No crash, nothing in `savagewind.log`. Pictures:
  `out\shots\playas_daruk_*.png`. `playas off` was checked on the same library without the game: the library's mod
  list came back exactly as before (The Linkle Mod on, the play-as mod gone), and the deployed `TitleBG.pack` has
  Linkle's own model again (the same bytes). Not checked yet: climb, glide, swim, the pause-menu Link, cutscenes.
- Checked without the game (Daruk, 2026-11-12): the model reads back and passes the model self-check, Link's 121
  bones come first with his parents, every body bone keeps its rest pose, every skin matrix keeps its bone, the
  textures are all there, the size entries cover every file. Tests with made-up data:
  `tests/test_charsel_playas.py`; the live puppet: `tests/test_charsel_puppet.py` (and the ghost selftest).
- Never share the built folder: it is made from your game's files (`built.txt` marks it; Export as .breath refuses it).

## Never shipped or sent

Packs, models, textures, animations and ActorInfo are never in this repo and never go over the network. Each PC
builds its own from its own files; `out\` is never committed, and `check_clean.py` flags packs and models. Only ids,
versions and names travel. The Linkle body comes from the Linkle mod on that PC (its own files, read only).

## In-game check

First run 2026-11-12 (`out\research\charsel-ingame-results.txt`): 15 pass, 6 fail. Hylian, Paya, Bokoblin and
Moblin spawn, animate and cost nothing measurable (30 frames a second with and without); Zelda, Urbosa, Daruk, Revali
and Mipha never spawn (also on other free names: `charsel-ingame-results-swap.txt`), so the bot step (R1) failed on
them too; `iam` and the no-pack step pass. `python tools\mp\charsel_ingame_check.py` does steps 1-4, 6 and 8-9 in one go (it starts the game once
with its own overlay: the Link body and the characters, built into `out\charsel-check-packs`, so your own
`out\charsel-packs` is never touched; or `--no-launch` on a running game, with the characters deployed with your mods).
By hand, in a game with the ghost mod 0.8.0, the Link body and `out\charsel-packs` deployed (`modman add`, then Play),
on the test save:

1. `ghost far 0`, then for each character: `ghost spawn <actor name>` (the table above): visible within 1 s, and
   `ghost` says `state live`, `as_ok true`.
2. On each: `ghost anim Wait`, `Walk`, `Run`, `RunGear2`, `CutS1`, `BowCharge`, `DamageS`, `Dead`,
   `ParashawlGlide`, `ClimbMove`, `SwimMove` (monsters: the ones they have): each one moves, nothing T-poses, `ghost`
   shows `as` = that name. Look for floating or sinking hips on Link's clips (a side view of `Wait` next to
   `ClimbMove`), and twists on Daruk's glide and climb.
3. Walk into each: no fight, no talk prompt, nothing to pick up.
4. `frames 5` with each one spawned and without: at most 3 frames a second fewer.
5. Relay, bot and client on one PC: `python tools\mp\relay.py`, `python tools\mp\bot.py --look
   "zelda:5,goron:5,bokoblin:5"` and `python tools\mp\client.py --ghost-looks`: the ghost turns into each one
   within a second or two.
6. With that client still running: `iam` lists the names; `iam daruk` says "Your friends see it in a few seconds",
   `ghost` shows `iam_seq` one higher and the client says "Others see you as Daruk (Goron) now"; `iam ganon` is
   refused.
7. Close the client. `iam zelda` now says the client tells your friends when it runs (no promise). Start
   `python tools\mp\client.py --ghost-looks --look goron`: it stays Goron (the old `iam zelda` doesn't win), and
   `iam` then says "You are goron".
8. A free name with no pack (what a stale catalog would ask for): `ghost spawn Dm663_Npc_Gerudo_S_F`. The game keeps
   running and answering `ghost` for 15 s (no crash, no hang) and nothing shows; then `ghost despawn`. Do this last.
9. `savagewind.log`: no heap, resource or size-table line about these names.
10. Two PCs (the user's own test): each picks a character; each sees the other's pick (or the Link body where it
   isn't in their game).

What the run decides: keep the NPC-class names or move those characters (`--slot`); `--monster-link-clips` on or
off; whether `--model-copies` loads (Link and Linkle side by side); a hip fix per character if needed.
