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

**In Breathalyzer Plus:** Home > Multiplayer > Who am I > **Build the characters** (once, from your own game files:
the bundled Characters recipe, `charsel.py build --add`, which builds into a temp folder and puts the mod in your
mod list ON, with `assets/characters/mod.toml`: `part_of` Ghost multiplayer, so it is used only while that is on).
Then press a character: that is your pick. Each PC shows the others from its own built characters, so every player
builds them; Plus doesn't build the Link body yet, so Link (and anyone not built on a PC) is the stand-in Sheikah
figure there. The page marks Zelda, Urbosa, Daruk, Revali and Mipha **Not tried yet** until the in-game check below
passes (`mp_page.NOT_TRIED`). The steps below are the same by hand.

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
| Hylians | `linkle` | Linkle | `Dm_Npc_UMii_Hylia_BodyS_M_012`: a costume built from The Linkle Mod's files (on or off in your library) | 33 Link | 0.9 MB model + its textures |
| Hylians | `zelda` | Zelda | `Dm_Npc_Sheiker_Searcher_M_000` | 6 + 27 | 10.6 MB |
| Hylians | `hylian` | Hylian (a Hateno villager) | `Dm_Npc_UMii_Hylia_BodyS_M_001` | 6 + 27 | 11.5 MB |
| Races | `sheikah` (`paya`) | Paya (Sheikah) | `Dm_Npc_UMii_Hylia_BodyS_M_002` | 6 + 27 | 6.4 MB |
| Races | `gerudo` (`urbosa`) | Urbosa (Gerudo) | `Dm_Npc_Handmaid_B` | 6 + 27 | 13.3 MB |
| Races | `goron` (`daruk`) | Daruk (Goron) | `Dm663_Npc_Assasin_A` | 6 + 27 | 10.9 MB |
| Races | `rito` (`revali`) | Revali (Rito) | `Dm_Npc_UMii_Hylia_BodyS_M_011` | 7 + 26 | 10.6 MB |
| Races | `zora` (`mipha`) | Mipha (Zora) | `Dm_Npc_UMii_Hylia_BodyS_M_003` | 9 + 24 | 11.0 MB |
| Monsters | `bokoblin` (`boko`) | Bokoblin | `Dm663_Npc_Assasin_B` | 32 + 0 | 6.2 MB |
| Monsters | `moblin` | Moblin | `Dm663_Npc_Gerudo_S_B` | 32 + 0 | 7.8 MB |

"Loads" is what the game reads to show that body (model, textures, animation files) when no real one is nearby
(from the study's size-table read). Urbosa is the heaviest: on a slow laptop pick a lighter one.

### Own moves (2026-11-14)

- **Where the game has them, the costume plays the character's own clips** [V: the stock actors' packs]. The game's
  own Urbosa, Zelda, Mipha and Paya pick their stand and walk by their look file's personality (`Woman_Active` ->
  `Positive_*`, `Woman_Deflated` -> `Negative_*`, else `Default_*`): Zelda, Mipha and Paya are `Woman_Normal`, Urbosa
  `Woman_Active` on the men's sets (which test `Man_Active`), so all four play `Default_Wait` and `Default_Move_Walk`
  from the women's file `UMii_Common_Body_W`. That is what the costumes play. **The game has no champion-only walk
  or run** (the memories are videos), so Zelda, Urbosa and Mipha walk like the game's own Zelda, Urbosa and Mipha
  do, which is the same women's walk [V].
- **Urbosa (v4)** no longer loads the Gerudo guards' file first (v3: her stand and walk were a generic Gerudo
  guard's; the real Urbosa never loads that file [V]). 2.9 MB lighter. **v5:** the game check showed the real one
  (`Dm_Npc_Gerdo_Hero`) standing hip-shot and ours straight. Her actor's `Wait`/`Walk` are `UC_M_Wait`/`UC_M_Walk`,
  whose personality branch is `Man_Active`/`Man_Deflated`/default, and her look file says `Woman_Active` [V]; she
  plays the Active branch [G: from the hip-shot stand]: `Positive_Wait` and `Positive_Move_Walk` (women's file). Her
  run has no such branch.
- **Sprint** for Zelda, Urbosa, Mipha, Paya: their own run at 1.3x (the women's file has only a panic run, which v3
  used). Set in the animation set's own frame control (`FrameCtrl` `Rate`, as the game's sets do [V]).
- **Paya:** her own shy stand (her own file). **Mipha:** a Zora woman's swim: moving, the clip lies her down in the
  water (its hips, `Skl_Root`, turned 39-73 degrees about X at its first frame, the tread clip's 0 [V]; that this
  is lying forward: [G]); still, she treads upright; her head is kept at
  Link's head height (the ground clamp's swim rule).
- **Still Link's clips** (no own clip in the game's files [V]): jump, fall, land, climb, glide (but Revali's wings),
  swim (but Mipha), crouch walk, the attacks, bow, shield, damage, death (lying: the ragdoll pose). Turns on the spot
  exist (`Default_Turn_*`) but need a turn direction the puppet doesn't read yet.
- **Bokoblin and Moblin (v2): their own swing for each of Link's, picked by his weapon and timed to his hit.** The
  self puppet now asks for `<swing>_Lsword` / `<swing>_Spear` when Link holds a two-hander or a spear (as the
  multiplayer client does for a friend's ghost; every pack's Link-clip costume gets Link's own two-hander and spear
  swings this way too). Each monster swing has a start frame and a rate so the middle of its hit lands on the middle
  of Link's (hit windows read from the game's animation sets [V frames], the look [G]): e.g. the Bokoblin's
  `Attack_Small` from frame 23 at 1.21x for Link's first sword swing. The Moblin no longer stabs with a sword. In
  water they wade (their walk, slowed: no swim clip in their files [V]).
- **Linkle:** `iam linkle` without a Linkle costume built says so ("add The Linkle Mod to your mods (it can stay
  off) and build the characters again. You stay Link.").

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
- **Everyone:** feet slide a little (clips play in place); skirts, capes and hair swing with the character's own cloth
  physics since 2026-11-14 ([cloth and hair](#cloth-and-hair-2026-11-14), not yet tried in the game); faces don't
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

- **A character pack** is a small actor pack (14-19 KB unpacked, 45-140 KB with cloth) built from your own files
  (`tools/charsel.py`), like the Link body (`tools/mp/ghost_link_pack.py`): the cutscene Sheikah man's actor link with
  our model list (the donor's own), our animation list (the family's animation files, then Link's, sorted names,
  blend times) and one small animation set per ghost name (the 33 of the Link body). No AI but the cutscene dummy's,
  and cloth-only physics ([cloth and hair](#cloth-and-hair-2026-11-14)). Bodies the game puts together from a look
  file (the Hylian) keep that file. Each pack sits on one free
  actor name: in the game's ActorInfo, with no pack anywhere in the game (checked at build against your ActorInfo:
  class, heap of 500 KB or more, no pack). The profile matches the name's class (DemoNPC or NPC).
- **Recipes are names only** (the game's file and clip names, as `ghost_link_pack.ANIMS`). Every clip a character
  uses as its own is looked up in your files at build; a missing one or a clash with one of Link's (a family file
  that has a Link clip name too) skips that character, with the reason. `charsel.json` in the output says what was
  built where, and why anything was skipped. `charsel check` reads every pack back: one actor link on the right
  name, sorted animation names, one clip each, the cutscene dummy's AI, no physics but cloth (no rigid body, no
  character controller, no contact info, no ragdoll), no attention list and no damage parameters (so a ghost or your
  puppet can never be locked on to, talked to, hit or pushed against), the right profile, under 64 KB (256 KB with
  cloth).
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
  aimed bow) is copied into the puppet's hands (the ghost's held items) while no friend's ghost is up, and what he
  carries on his back onto its back ([held items](#held-items-the-ground-the-hands)). With a friend's ghost up, the
  puppet's hands are empty. Link's own items are hidden with him (each weapon's frame update copies its holder's draw
  flags [V code]).
- The puppet can't be locked on to, talked to or hit (no attention list, no damage, no physics but cloth in its
  pack). The green ring that shows next to it while you sprint or climb is Link's own stamina wheel, where it always is.
- **Link shows instead** while he can't be mirrored: loading, cutscenes, riding a horse, a shield surf. A state the
  character has no clip for no longer shows Link (0.9.3): it stands or walks there (a glide: it falls), and swimming
  its head is kept at Link's head height. The pause menu keeps what was shown. After a load or a death the puppet is
  spawned again on the new stage.
- A pack that never loads (not built, not deployed, or the game refuses it) is tried 5 times; then you stay Link and
  `iam` says: "Daruk did not load (...): you stay Link. Restart the game to play as Daruk: python tools\charsel.py
  playas daruk".
- Developer mode: `ghost puppet` (its state: `shown`, `link_hidden`, `as_want`, `as`, `why`, `rehides`...),
  `ghost puppet lift <m>` (raise or lower it, for a hip that floats or sinks), `ghost puppet retry`, `ghost puppet
  off|on`, `ghost puppet map id=Actor,...` (another actor name per character).

Not checked in the game yet [G]: moving, climbing, gliding and swimming with a live puppet, whether the game shows
Link again by itself (`rehides`), his shadow, one frame of lag, the puppet's hip height.

### Ragdoll

**Ghost mod 0.9.1.** When Link ragdolls (a bomb, a Guardian beam, a big hit, a tumble down a steep slope, his death),
the costume falls with him and lies on the ground, not through it. It never skips the ragdoll. Seen in the game
(2026-11-13): a bomb threw Link as Daruk down a hillside, and most of Daruk went through the ground. Daruk's torso is
about 2 m wide, Link's is 0.4 m, and the costume was standing at Link's place while he lay down.

- **Detected** by Link's ragdoll manager (`*(Link+0x1870)`): bit 13 (`0x2000`) of its flags at `+0x110` is set while
  the ragdoll runs. The ragdoll's start (`0x70011e704c`) sets it, its end (`0x70011e56ac`) clears it, and the game's own
  starts (`0x70011e7880`, `0x70011e7908`) read it as "already on" [S]. Every ragdoll goes through that start: the slip
  and tumble (`PlayerSlippingDown`, which a blast or Guardian hit switches to after `PlayerSuperBlow` or
  `PlayerLaunch`), the death ragdoll (`PlayerDead`) and lava (`PlayerHell`). A second source, in case the offset is
  wrong in this game version: Link's clip is `SlippingDown...`, `Dead...`, `DeadRagdoll` or `Rolldown...`.
- **His pose, bone by bone:** the costume's animation is paused with the game's own switch (bit 3 of byte `+0x34` of
  each model unit's animation at unit `+0x188`; the game clears that bit itself to pose a model by hand, at
  `0x7000596438` [S]). Then every frame each of its bones with one of Link's names (Daruk: 94 of 144) gets that bone's
  turn from Link's skeleton, its world matrices, the pose the physics made. The bone keeps its own length, the
  character's own bones (belly, beard, wings) follow their parents, and its hips go where Link's are. That is what a
  Link clip of that exact pose would do. The new bone transforms are written as the game's own set-local writes them
  (unit `vf_9`, `0x7000f95fa8`: the skeleton's locals at `+0x18`, 0x60 bytes a bone).
- **On the ground:** a ground plane is fitted under Link's own body, through the lowest corner of each of his body
  boxes. Then the costume is raised (or lowered, for a thinner body like Mipha) until its lowest box corner lies on it.
  It goes up at once and comes down gently (0.2 s). The body boxes are each body's flesh around 16 main bones, in the
  bone's own frame, measured from the game's own models by `python tools\charsel.py bodies` and written to
  `native/examples/ghost/pup_bodies.h` (numbers only). The Hylian is made of Mii-style parts, so it uses Link's boxes
  scaled to its spine.
- **Getting up:** the pose is followed until Link is up again (spine within 35 degrees of straight up, hips 0.65 m
  over the ground, 4 s at most). Then the costume's animation runs again and it plays its clip for what Link does.
- **If his pose can't be followed** (a skeleton or the animation can't be read, or fewer than 8 bones have Link's
  names), the costume plays its own lying clip (`Dead`) on that ground, under Link's hips, and says why
  (`ragdoll_why`).
- Developer mode: `ghost puppet ragdoll pose|clip|off` (pose by default; off is the 0.9.0 way: its clips at his
  place), `ghost puppet girth <x>` (the body boxes times x, 0.3 to 3, if it floats or sinks). `ghost puppet` answers
  `ragdoll` (no, pose, clip), `ragdoll_src` (flag, anim, getup), `ragdoll_bones`, `ragdoll_lift`, `ragdoll_flags`,
  `ragdoll_mgr`, `ragdolls`, `ragdoll_why`.
- This is the live costume (`iam daruk`). The restart way (`charsel playas daruk`) has Daruk's body on Link's own
  skeleton. There the game's physics moves the body directly, Link-sized, so it can still sink [G]. To see Daruk
  ragdoll properly, use the live costume.
- Tests without the game: the ghost selftest (`rd_*` rows: detection, the pose on made-up skeletons, the ground on
  flat and sloped bodies, the lift, getting up, the clip, `ragdoll off`, removal mid-ragdoll) and
  `tests/test_charsel_puppet.py` (the body boxes for every body, `body_boxes` on a made-up model).

Not checked in the game yet [G], all of it: that the bit and the animation switch work this way in a running game,
one frame of lag behind Link's pose, how the ground fit looks on a hillside, and whether the death screen counts as
"no real position" (then Link shows, as in a cutscene).

**Round 3 (2026-11-13, after the user played Daruk; still ghost 0.9.1, the 0.9.2 number waits for the in-game check
tool).** He said: "he just rolled around like a physics-less ball", then "the bomb ragdoll just makes him walk, I can
see him walking while falling through a hill".

- **Why the pose never showed:** the game puts a unit's clip into its bone locals before the unit's own step
  (`0x7001051914`: the unit's animation list, then unit `vf_79` `0x7000f969ac`, then its world matrices) [S]. These
  costumes' clips don't go through the `+0x188` animation at all: in the user's game, Paya's and the Hylian's units
  have none there, yet they play Wait. So pausing paused nothing, the clip overwrote the pose, and the costume showed
  its last clip (a walk, or Daruk's rolling ball) placed at Link's tumbling matrix. Now every posed unit gets the whole
  pose again right after its own step, before its world matrices are made (`vf_79` wrapped with `hle_override`, all
  bones). No clip can show. Zelda's animation of another class needs nothing more.
- **The walk on the hill:** after a tumble on a steep hill Link gets up into `Slide`, `SlideBrake` and `SlideLand`.
  No pack has these, so the costume walked by his speed at his feet, half in the hill. The pose is now followed
  through every lying, sliding and knock-down clip (`Slide...`, `DamageL...`, `LandDamage...`, `SwimDead`). The ground
  takes the slope of his motion while he moves, so the costume stays over the hill. Standing, its feet are on the
  ground and the rest of it is at most 0.15 m under it. He is "up" only when standing.
- **The retarget:** his turn from his own rest is put on the costume's own rest (each skeleton's rest read from its
  resource), and the bones not named as his go to their rest (hair, beard, cloth). Daruk's 21 main bones rest exactly
  as Link's (measured from the model files), so his body was already turned right. The face bones were 10-53 degrees
  off, Zora's ears 112, and the monsters' necks 40-82.
- **Loose limbs:** each arm and leg swings behind the body's motion: a damped spring, at most 52 degrees off
  (`ghost puppet limp 0..1`, 0.5 by default, 0 = exactly Link's pose).
- **Daruk's sprint ball rolls:** his sprint clip is a still curled pose, and the game rolls the body itself. While it
  plays, the costume turns about the axis across his motion at his speed divided by the ball's radius (measured each
  frame from its body boxes as the clip poses them). Its lowest point stays on the ground (the slope comes from his
  motion), it faces the way he goes, the spin starts and stops gently, and it comes back upright before its next clip
  (`ghost puppet ball off|on`).
- **Link no longer flashes on a fast slide:** he shows only for the game's own states (no world, an event running,
  no body of his own), each held 0.15 s. The old 1 m check of his real position against the player info's copy is
  only measured now (`real_gap`, `gap_frames`).
- **A ragdoll of its own isn't possible:** no Goron or Champion pack has one (`Physics/Ragdoll/*.hkrg`). Only Link's
  pack and the monsters' have one (Bokoblin, Moblin, Lizalfos, Lynel, Hinox), each for its own skeleton.
- `ghost puppet` also answers `ragdoll_anim` (how each posed unit is held), `ragdoll_after_step`,
  `ragdoll_after_writes`, `ragdoll_rest`, `ragdoll_up`, `ragdoll_want`, `limp`, `busy`, `event`, `real_gap`,
  `real_gap_max`, `gap_frames`, `ball` (no, rolling, upright, off), `ball_radius`, `ball_spin`, `ball_angle` and
  `balls`. Selftest rows: `rd_detect_slides`, `rd_rest_retarget`, `rd_rest_refused`, `rd_motion_slope`, `rd_limp`,
  `ball_shape_place`, `rd_after_step`, `rd_slide_followed`, `ball_rolls`, `rd_why_cleared`, `short_doubt_keeps`.
- Not checked in the game yet [G]: all of round 3.

### Held items, the ground, the hands

**2026-11-14 (still ghost 0.9.1 by number; not yet checked in the game [G]).** The user played every character
(2026-11-13): weapons missing or flickering in most hands, Urbosa's sword on her back and her shield nowhere, Daruk's
blades clipped at his wrist, Urbosa sinking while attacking, the Moblin turning into Link to swim.

- **Why the weapons failed:** the copy sat on the item's own bone follow, and the game picks that place by the
  holder's class (`ghost-items-game-attach.md`, slot 153). An NPC-class holder is asked "stowed?" by its own holder
  object, so the copy goes to `Pod_A` (Urbosa's lower back) or nowhere. The NPC-class slots are the Hylian, Paya,
  Mipha, Revali, Urbosa and the Moblin [V ActorInfo]; Zelda, Daruk and the Bokoblin are DemoNPC (never stowed, no
  offsets), which is why Zelda held them [S + the user's report]. Every body has `Weapon_R` and `Weapon_L`, turned as
  Link's [V models]: the bones were never missing.
- **Now:** every copy the puppet holds is on our own bone follow, never the game's: the body's `Weapon_R` (the shield
  and the bow `Weapon_L`) with the item's own "in the hand" offsets (Link's), or its back (`Spine_2`, where Link's
  `Pod_A` sits on his, out of each body's own back box) with the item's own back offsets. When the game puts its own
  follow back, ours goes back the next frame (`items.N.grip_sets` counts it). Sheathed items now show on the costume's
  back. A name Link's equipment read loses for under 0.4 s keeps its copy (the flicker was a delete and a new copy
  0.3-0.8 s later).
- **Per body** (`PUP_GRIPS` in `puppet.c`): Daruk's grip moves 24 cm to his finger roots (his `Weapon_R` sits mid-palm,
  every other body's at its finger roots [V models]) and his items are 1.8 times Link's; the Moblin 2.0, the
  Bokoblin 1.3; the rest 1 [G sizes]. `ghost puppet grip <size> [x y z cm]` tries others live, `grip -` goes back.
- **Hands:** a hand holding a copy closes as Link's does (his finger turns), and while he guards with the shield in
  hand (a parry, or the `...Attention`/`...Atn...` stance) his whole left arm is copied: the shield goes up. Written
  after each unit's own step, those bones only (`ghost puppet hands off|on`; `hands`, `hand_bones`).
- **Ground clamp, every state:** its lowest foot goes as high as Link's lowest foot (his skeleton, both bodies' feet
  boxes): attacks, landings, crouching, slopes, the air. Swimming, its head goes to his head's height
  (`ghost puppet clamp off|on`; `clamp` feet/head, `clamp_m`, `clamp_want_m`).
- **Daruk:** the ball keeps rolling through a sprint dip of up to 0.35 s (a frame hitch popped it upright). The
  camera inside his body is not fixed: no camera address is known in this repo, so nothing safe can move it yet.
- **Moblin:** his limb chains all carry Link's names (46 bones) [V model], so the round-3 pose fix covers his
  ragdoll; swimming now wades (stands, head at Link's head height) instead of showing Link.
- **Death:** an event or his body gone keeps the costume while it follows his ragdoll or he plays a death clip.
- Selftest rows: `grip_place`, `items_kept`, `guard_detect`, `hands_overlay`, `death_keeps_costume`, `clamp_step`,
  `ball_dip_hold`, `missing_clip_no_link`, `items_grip`, `clamp_world`.

### Round 5 (the round-4 game check, 2026-11-14; ghost still 0.9.1 by number; not yet checked in the game)

- **Back items lay sideways** (Urbosa's spear across her hips, the sword sticking out at Paya's and Revali's
  shoulders): that was the game's own NPC sheath place, not ours [V numbers: Urbosa's `Pod_A` rests 28 cm under her
  `Spine_2`, at 1.26 m; a spear's `NPCHoldTransOffset` is (0, 0, -0.85) with no turn, a sword's all 0]. Our back
  place (Link's `Pod_A` place on the body's `Spine_2`, every body's `Spine_2` turned as Link's [V], with the item's own
  `PlayerHold` offsets: Link's) was sheathed over. Now each NPC holder slot that holds one of our copies keeps its "on
  the back" byte at 0 ("in hand", as a DemoNPC holder always is), so the item's AI never sheathes it (`holder`,
  `holder_writes` in `ghost puppet`; `items.N.place` back/grip). `ghost puppet back x y z` (cm, `Spine_2`'s frame)
  tries a per-body move, `back -` goes back; the table's `back` is 0 for every body [G].
- **The arrow floated next to her head:** Link's own nocked arrow stays at his hidden hand. An equipment slot from 3
  up holding an actor named `...Arrow` is now hidden while the costume shows and shown again once shot or when Link
  shows (`arrow`, `arrow_hidden`) [G: which slot holds it].
- **Paya's sword pointed straight up in front of her chest:** her own stand holds her hands at her chest. For Paya and
  Urbosa (hand on hip in her new stand), the arm of a hand holding a drawn copy now takes Link's turns (`hands`
  shows `arm_r`/`arm_l`).
- **Daruk's ball over a drop:** Link counts as sprinting while the sprint is picked by speed, or while he Moves and
  his stamina falls (only a sprint drains it on foot), whatever his speed; the ball ends only after 0.3 s out of it
  (`sprint_since`, `stamina`).
- **Game Over for a minute spent the 5 tries:** no request while the game is busy (an event such as Game Over, a
  load, no body); a try that fails while it is busy isn't counted (`busy_waits`); a pack given up by the game's doing
  that spawned before comes back on a new stage or a new Link, after a Continue (`comebacks`).
- **The Bokoblin's swing came 0.16 s late:** the costume blended 6 frames into a swing (Link's own attack nodes have
  Morph 0 [V]) and starts a frame or two after him. Attack sets now start at once (node Morph 0, and the puppet asks
  with no blend) and the monsters' swings start 0.05 s further into their clips (Bokoblin `Attack_Small` from frame
  25, Moblin's first swing from 18) [G: the remaining lag].
- **Linkle did not load:** her new model file had no size-table entry while another mod ships a table [G: the cause].
  The characters mod now ships its own table (`romfs/System/Resource/ResourceSizeTable.product.srsizetable`: the
  game's, plus an entry for each pack, each new file in a pack, and her model and textures; listed in `charsel.json`
  `sizes`); `modman deploy` merges it with the other mods' tables. Her model is sized from Link's (his entry is 1.58x
  his file [V]): 884,592 bytes -> 1,393,696. Not `logs/rstb.json`: modman refuses a mod folder with `logs/` as an
  unmerged BCML package. Her slot's heap (1,004,792 bytes) is like the other NPC slots that load [V ActorInfo].
- Selftest rows: `back_tweak`, `arm_overlay`, `attack_morph`, `arrow_name`, `holder_in_hand`, `arrow_hidden`,
  `busy_no_tries`, `gameover_comeback`, `refused_stays_failed`, `ball_drop_hold`.

**Big bodies and knockback (designed, not built) [G].** A big body (Daruk, the Moblin) could shrug off small knocks
(a bomb, a small hit) and still take the damage. Big hits (a Guardian beam, a big explosion, a Lynel) and death would
still ragdoll. The game decides the knock-back before any ragdoll. The damage reaction picks the blow action
(`PlayerSuperBlow`, `PlayerLaunch`, and `PlayerBarrierBlow` for barriers). That action flies Link and then, after
`NoRagdollTime` (10 or 12 frames in `Player_Link.baiprog`), switches to the slip and tumble through `0x7000cae8e0`
-> `0x70011e778c`. Skipping only the ragdoll still leaves the blow's flight, and the emotes study warns that driving
the ragdoll by hand splits the body from Link's capsule. So the hook has to sit where the damage reaction is chosen,
with the hit's strength known there. That place isn't found yet. The emotes mod also wraps Link's update
(`0x7000cd1ee8`, only the first wrap runs). So nothing is hooked: size-based knockback waits for a study of the
damage reaction and for modhost to chain wraps.

## Cloth and hair (2026-11-14)

The user: "if there's no skirt or hair physics you can't do the Shorts" (the non-Link characters). Not yet tried in
the game. Research: out/research/charsel-anims-physics.md section 3.

- **The pack:** `PhysicsUser` is ours (`SwChar_<id>`): `Actor/Physics/SwChar_<id>.bphysics` is the stock actor's own
  physics file made **cloth only**, the way every armor piece of the game has it [V: `Armor_001_Upper` and others]:
  `use_rigid_body_set_num 0`, `use_character_controller False`, `use_contact_info False`, `use_ragdoll False`; the
  rigid bodies (its `Tgt` sensor capsule), the character controller (a 60 kg capsule on `EntityNPC` that would push
  Link), the contact info and the monsters' ragdoll are gone; its support bones and cloth parts stay as they are. The
  stock actor's `Physics/Cloth/<x>.hkcl` and `Physics/SupportBone/<x>.bphyssb` are copied from your own game into the
  pack at build time, at the paths the physics file names; the game's size table already has entries for them [V].
  `AttentionUser` and `DamageParamUser` stay Dummy: no lock-on, no talk, no damage. `charsel check` refuses a pack
  whose physics is more than cloth.
- **Per character** (the stock actor's own parts [V: its physics file], most important first; `charsel.json`
  `built.<id>.cloth`):

  | Character | Cloth parts | Heap estimate: need / slot's heap (cloth part) |
  |---|---|---|
  | Urbosa | skirt `Skart`, hair `Hair_B`, `Hair_A`, waist `Accs_D`, `Earring` | 661,314 / 902,392 (177,738) |
  | Zelda | `Skirt`, hair `Hair_C`, `Necklace` | 654,672 / 831,520 (152,808) |
  | Mipha | sash `Cloth_A/B/C`, `Head_Fin`, `Ear`, `Leg_Fin`, `Arm_Fin_R`, `Parts_B`, `Parts_D` | 684,274 / 1,127,672 (161,934) |
  | Revali | `ScarfA`, `ScarfB`, `Braid`, `ShoulderPlume` | 578,540 / 1,012,984 (77,100) |
  | Daruk | `Tophair`, `Beard`, `Mustache`, `Cloths`, `ArmFur`, `Seta` | 544,316 / 1,028,128 (121,788) |
  | Paya | `Hair_02`, `Hair_Side` | 916,090 / 1,099,000 (44,742) |
  | Bokoblin | `Apron`, `Ear_L`, `Ear_R`, `Necklace` | 384,728 / 1,007,648 (64,728) |
  | Moblin | loincloth `Cloth_A/B/C` | 367,736 / 554,232 (47,736) |

  (all `_Havok`). The Hylian (a Mii-style body), Link and Linkle have none here.
- **The heap [G]:** the cloth is counted first: 1.5 bytes of heap per byte of the cloth and support-bone files (no
  game number exists; the game's size table gives these files about 2x and 4.6x their size, but that is the resource
  heap). Then Link's file loses its retarget if needed (as before), then the least important parts are left out, never
  the first; if even that one doesn't fit, the character has no cloth. Today every character fits with all its parts.
  A part left out is [G]: every one of the game's 508 cloth physics files lists every part of its cloth file [V].
- **`charsel build --cloth off`** builds the packs as before 2026-11-14 (no physics): the fallback if a cloth pack
  doesn't load or costs too many frames.
- **The puppet (ghost 0.9.6 by comment, still 0.9.1 by number):** the bones each body's physics moves (`PUP_CLOTH` in
  `pup_bodies.h`, written by `charsel.py bodies`: the names in its cloth and support-bone files that are bones of its
  model, less those named as Link's, which are the anchors; 15-72 per body) are never written by the ragdoll pose,
  the after-step write or the hands, so the pose and the cloth don't fight (`cloth_bones` in `ghost puppet`). The
  ground clamp and the ball only move the whole costume.
- **The physics call:** the puppet's physics is not moved with `PHYS_SET_MTX` (it has no rigid body), and the ghost
  skips it for any actor on charsel's free names. `ghost puppet physmtx warp` calls it once after each warp, `on` every
  frame (the old way), `off` (the default) never: for the game check if the cloth stays behind.
- **Warps:** a new pick, a stage change, a fast travel and a respawn spawn a new puppet, so its cloth starts fresh.
  A jump of more than 6 m in one frame on the same stage (a teleport command) is counted (`warps`); no cloth reset is
  known, so a skirt may whip once there [G].
- **Not known until the game run [G]:** whether an NPC-class actor runs with physics and no rigid body at all (none of
  the game's NPC or DemoNPC actors does: the Great Fairies keep a `Tgt` capsule on `SensorNoHit`, the next thing to
  try); the frame cost (an older note: 3-8 frames a second for Zelda's dress); whether the cloth follows without the
  physics call.

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
