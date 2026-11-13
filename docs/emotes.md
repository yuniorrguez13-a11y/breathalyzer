# Emotes

Link can dance, wave, give a thumbs up or down, and go flying like a ragdoll. 24 emotes on 3 pages, plus the
ragdoll. A mod by giruka.

**Status:** experimental, **not working in the game yet**. The animations are built and checked offline (every pose,
and a read-back of the file). In the game (Game check 2026-11-13, round 2) the emote now stays on Link's body slot for
the whole clip and moving cancels it, but on screen Link still stood idle. The cause is found (the emote went on his
legs only, see How it works) and a fix is built, not yet tried in the game. The ragdoll was only a lying pose (it
looked like a crouch); version 0.2.0 makes it a real ragdoll (Link is thrown forward and tumbles), built from a
study of the game's code, not yet tried in the game. The Emotes mod starts **off**, because it changes a game file
(Link's animation list).

## Turn it on

1. Build the mod and add it (developers, from this repo):

   ```
   python tools/build_native.py build
   python tools/modman.py add native/examples/emotes
   python tools/emote_build.py build
   ```

   `emote_build.py build` makes the animations on your PC from your own game files and adds them to your mod
   library as **Emote animations** (`giruka.emotes-anims`). That package is part of the Emotes mod: it is hidden and
   is used only while the Emotes mod is on.
2. Turn **Emotes** on, then press Play (or `python tools/modman.py deploy`).

It works together with The Linkle Mod: both add an animation archive to Link's animation list, and the list merge
keeps both.

## Play an emote

| How | What |
|---|---|
| Keys **5 6 7 8 9 0** (or the numpad's) | 5 thumbs up, 6 thumbs down, 7 six seven, 8 in love, 9 floss, 0 ragdoll |
| Console: `emote` | the numbered list |
| Console: `emote floss`, `emote 9`, `emote 67`, `emote thrust` | play one (by name, number, nickname or the start of its name) |
| Console: `emote stop` | stop; moving, jumping or attacking stops it too |
| Console: `emote ragdoll`, `emote ragdoll on` / `off` | the ragdoll (`off`: get up sooner) |
| Mods page: **Emote keys** | choose which emote each number key plays |
| Mods page: **Play an emote** | press Play, close the menu, and Link does it |

- Link has to **stand still** with nothing in his hands to start one, and not during a talk or a cutscene. Walk,
  jump or attack and he stops. Press the same key again to stop.
- Short emotes (wave, thumbs up...) go back to standing by themselves. Dances keep going until you move.
- **Ragdoll (key 0):** Link is thrown forward, the way he faces, about 3 m, and lands limp on the ground, tumbling
  like after a bomb blast. No hearts are lost. He gets up by himself after a moment; press 0 again to get up sooner.
  Link has to stand still on the ground (not riding, swimming or in a cutscene); a weapon in his hand is fine. One
  ragdoll every 2 seconds. Watch out near cliffs and water: the game can't tell the mod where an edge is, and the
  tumble is the game's own, so a fall off a cliff is a real fall.
- Other players see a lying-down pose while you ragdoll. If the real ragdoll can't start (another game version, say),
  Link lies down in that pose instead.
- If the animations aren't built, the wave, thumbs up, photo pose and victory use the game's own moves; the others
  say "the emote animations are missing: turn on Emotes (animations) in the launcher, then Deploy".

## The list

30 frames = 1 second. "Ours" means our own keyframes in `tools/emotes/`; "game" means one of Link's own moves,
named by its name.

**Page 1: Hello**

| # | Emote | Nicknames | What Link does |
|---|---|---|---|
| 1 | Wave | hi, hello | right hand high over his head, four big swings (ours) |
| 2 | Thumbs up | like, nice | arm forward, thumb up, a firm nod (ours) |
| 3 | Thumbs down | dislike, boo | thumb down, a slow head shake, shoulders drop (ours) |
| 4 | Clap | applause | claps in front of his chest twice a second (ours, loops) |
| 5 | Point | you | "You!": points forward, the other hand on his hip (ours) |
| 6 | Shrug | idk, dunno | palms up, shoulders up, head tilt (ours) |
| 7 | Facepalm | ugh, smh | hand to his face, head drops into it, slow shake (ours) |
| 8 | Bow | thanks | a polite bow, hand on his chest (ours) |

**Page 2: Dances** (all loop until you move)

| # | Emote | Nicknames | What Link does |
|---|---|---|---|
| 1 | Floss | flossing | straight arms swing side to side, one in front and one behind, hips the other way |
| 2 | Six seven | 67, 6 7 | the 6-7 meme: palms up, hands bob up and down in turn like scales |
| 3 | In love | thrust, so in love | the "I'm so in love with you" trend: hands behind his head, hip thrusts on the beat |
| 4 | Robot | beep | stiff 90-degree arm poses that snap, head snaps left and right |
| 5 | Moonwalk | mj | on the spot: heel pops, the other foot slides back, swap |
| 6 | Air guitar | guitar, rock | wide stance, strums, head-bangs |
| 7 | Korok hop | yahaha, hop | happy hops with his arms flung up |
| 8 | Hestu shake | maracas | fists high shaking maracas, side-step shuffle |

**Page 3: Hyrule**

| # | Emote | Nicknames | What Link does |
|---|---|---|---|
| 1 | Salute | guard | a Hylian guard's salute: heels together, fist to his heart, chin up (ours) |
| 2 | Revali smug | revali, smug | arms crossed, head back and turned away, a foot taps (ours, loops) |
| 3 | Daruk flex | flex | Link's own muscle-man pose (game) |
| 4 | Urbosa snap | snap | hand on his hip, the other raised, a finger snap (ours) |
| 5 | Mipha wave | shy | a small shy wave near his chin, blushing (ours) |
| 6 | Photo pose | photo, pose | one hand up high, the game's selfie pose without the Slate (game, loops) |
| 7 | Snack time | eat, apple | a hearty bite (game) |
| 8 | Victory | cheer, yay | his cooking celebration, with his voice (game) |

**Ragdoll** (key 0, `emote ragdoll`, nicknames flop, limp): Link is thrown forward and ragdolls to the ground, then
gets up by himself (press again to get up sooner). Other players see a lying-down pose.

## Make your own emote

Each emote is one small JSON file in `tools/emotes/`. Copy one (say `wave.json`), give it a new `id` (the file is
`<id>.json`), change the keys, and check it:

```
python tools/emote_build.py lint                         the data (no game files needed)
python tools/emote_build.py preview my_dance --out my_dance.png    front and side stick figures
python tools/emote_build.py check                        every pose check, and a test write (writes nothing)
python tools/emote_build.py build                        build and add to your library
```

Then `emote my_dance` plays it in the game. A new id works in the console without rebuilding the DLL; to put it on
the number keys and the Play page, developers run `python tools/emote_build.py header` and rebuild the DLL.

**Axes:** Link's own. **x** points to Link's **left**, **y** up, **z** forward, in metres from the ground between
his feet. Angles are in degrees. Bones use Link's names (`Arm_1_R` upper arm, `Arm_2_R` forearm, `Wrist_R`,
`Spine_1`, `Head`, `Leg_1_L`, `Ankle_L`, `Finger_B_1_R`...).

| Field | Meaning |
|---|---|
| `id`, `name`, `aliases` | the console name, the wheel name (2 lines of 12 letters at most), nicknames |
| `page`, `slot` | `hello`, `dances` or `hyrule`, slot 1 to 8 (or `special`) |
| `by`, `about` | who made it, what it looks like (one line) |
| `kind` | `keys` (yours), `game_clip` + `"clip"`, or `game_set` + `"set"` (the game's own move) |
| `frames`, `loop` | length (30 = 1 s, at most 255); a loop's last frame is its first |
| `face` | `smile`, `blush`, `smug`, `frown`, `serious`, `surprise`, `battle`, `selfie`, `none` |
| `poses` | named poses that keys reuse |
| `keys` | the keyframes: `{"f": frame, ...}`; what a key says stays until a later key changes it |
| `expect` | checks on the solved pose, such as `{"f": 12, "above": ["Wrist_R", "Head"]}` |
| `contact` | hands that may touch the body (`Wrist_R`, `Wrist_L`) |

In a key:

| Field | Meaning |
|---|---|
| `pose`, `mirror` | start from a named pose; `mirror: true` swaps left and right |
| `ease` | `smooth` (default), `linear`, `snap` (holds, then jumps in 2 frames: robot moves), `hold` |
| `aim {bone: [x,y,z]}` | point an arm or leg bone that way |
| `palm {Wrist_R: [x,y,z]}` | which way the palm faces |
| `reach {Wrist_R: [x,y,z]}` | put the hand there (the elbow bends by itself); `{"on": "Head", "at": [x,y,z]}` follows a bone |
| `step {Ankle_L: [x,y,z]}` | move a foot from where it stands (lift it, slide it) |
| `elbow {R: [x,y,z]}`, `knee {L: ...}` | which way the elbow or knee points |
| `hand {R: preset}` | `open`, `relaxed`, `fist`, `thumb_up`, `point`, `peace`, `snap`, `snapped`, `claw`, `grip` |
| `turn {bone: [x,y,z]}` | degrees about Link's axes: head nod `[15,0,0]`, shake `[0,15,0]`, tilt `[0,0,15]`; on `Spine_1` the whole upper body |
| `local {bone: [x,y,z]}` | degrees in the bone's own frame (fine finger work) |
| `rest [bones]` | the skeleton's own rest pose for these bones (`Finger_*_R` works) |
| `hips [x,y,z]` | move the whole body; the feet stay planted, so the knees bend |
| `feet` | `planted` (default) or `free` |
| `reset`, `free [bones]` | everything back to standing, or only these bones |

Expect checks: `above`, `below`, `left_of`, `right_of`, `in_front`, `behind` (`[bone, bone]`), `near` and `far`
(`[bone, bone, centimetres]`), `thumb` (`["R", "up"]`), `feet_on_ground` (`true`). `check` also catches joints that
jump, hands inside the body, elbows and knees bending the wrong way and planted feet that slide.

Our keyframes are our own work. Don't copy animations from other games or paid libraries (Mixamo and the like):
nothing that can't be shared goes in `tools/emotes/`.

## How it works (developers)

- `tools/emote_build.py` solves the keys on Link's skeleton (read from your `Player_Animation`; the idle pose is the
  first frame of `Nml_Wait`), fits curves (within 0.11 degrees), writes the archive `SwEmote_Animation` with
  `tools/wiiu_model`, one animation set `Player_ZEmote_<id>.bas` per emote, the list log `logs/aslist.aamp` (names
  `ZEmote_<id>`, sorted after the game's last name, so the game's binary search finds them) and `logs/rstb.json`, and
  an `emotes.json` record of what it built. Nothing from the game is in the repo.
- Game clips (`kind: game_clip`, such as the flex) are copied from your own `Player_Animation` into our archive under
  our name (`SwEmote_<id>`). Another mod may replace `Player_Animation`: The Linkle Mod's has 1-frame empty stand-ins
  for the random idles (`Random_Nml_Wait_Kinnikujiman` and two more), so a set naming the game's clip ended at once.
- `native/examples/emotes/emotes.c` wraps Link's own per-frame update (`0x7000cd1ee8`), plays the set with Link's own
  body play (`0x7000cc7edc`, as the game plays Wait or Move) only while Link is in `PlayerWait`, lets go when the game
  takes the body slot back, and plays `Wait` when a one-shot ends. `emote state` answers the fields multiplayer will
  use. Developer checks: `emotedbg slot`, `emotedbg play <set>`, `emotedbg upper <set>|-`, `emotedbg path
  body|generic`, `emotedbg ai`, `emotedbg rd`, `emotedbg mask off|calc|always`, `emotedbg ragdoll throw|slip|pose`,
  `emotedbg throw [speed height push roll]`.
- **The ragdoll (0.2.0):** the game's own reaction to a blast, `PlayerLaunch` (the `PlayerNormal` child named
  "uchiage", launch), entered the way the game's own message code enters it: `0x7000cae770(PlayerNormal, key, params)`
  (the key is the game's own text at `0x7001656008`; the child is entered at once). Its enter `0x7000c8279c` reads its
  settings through pointers in the action: `+0x20` InitSpeed (metres per frame), `+0x28` JumpHeight, `+0x30`
  AddLinearImpulse, `+0x38` AddRollImpulse, `+0x48` Damage (the player's own `Player_Link.baiprog`: 1.0, 10, 50, 30,
  8 = two hearts) and `+0x50` BasePos, the spot he flies away from (the direction from Link `+0x1770`/`+0x1778`, his
  position). For the switch call only, the mod points those at its own arc (0.15 = 4.5 m/s, 1 m up, push 20 as a big
  hit's `PlayerLargeDamage`, roll 30), a zero damage, and a spot 10 m behind Link (his facing: his matrix's z axis,
  actor `+0x398`); then the game's pointers go back. After `NoRagdollTime` (12 frames) the game itself switches to its
  slip-and-tumble ragdoll `PlayerSlippingDown` with the push along the flight and the roll tipping him head first
  (`0x7000cabdfc` -> `0x7000cae8e0` -> `0x70011e778c`), and stands him up when the ragdoll's still timers reach their
  limits. The slip-fall's own speed damage points at the zero until he is up. A second press copies the limits into
  the timers (manager `*(Link+0x1870)` `+0x138`/`+0x13c` <- `+0x1dc`/`+0x1e0`). `emotedbg rd` answers the state
  (`th`: asked, flying, down), the game's launch values, the arc, the direction, the cooldown and why the pose was
  used instead. `emotedbg ragdoll slip` is the first physics try (the slip-fall in place, no push), `pose` the clip
  only; `emotedbg throw 0.2 1.5` changes the arc. Not tried in the game yet: the flight, the landing pose, the get-up
  press.
- **Why the model stood idle (round 2):** Link's model has two skeletal animation targets (`Player_Link.bmodellist`
  `AnmTarget_0` and `_1`): slot 0 of his animation holder moves the lower body, slot 1 the upper body (from `Spine_1`
  down, without the face). His body play puts a set on slot 0 and flags him (`+0xc48` bit `0x1000`); later that
  frame his animation step (`0x7000ccc7f0`) puts the same set on slot 1 too. The generic play the ghost uses (its
  puppets have one target) sets no flag, so the emote ran on the legs only (it even ended on time) while the upper
  body kept standing. Upper-body sets such as `TalkHello` the game plays on slot 1's second layer (`0x7000cc80c8`)
  and stops with `0x7000cc8224`; `emotedbg upper` plays one that way, as a control.
- **Why the game put Wait back:** while Link stands, his action `PlayerWait` runs each frame (calc `0x7000c9d0bc`,
  before his update). Its walk/run code runs his standing animation code `0x7000ce0c44`, and that plays `Wait` on the
  body slot whenever the slot's name isn't `Wait` (or one of the game's own standing sets: out of breath, cold...). The
  mod also wraps that calc: while an emote plays, the slot's name reads `Wait` during that one call, and the emote's
  own name again right after. Everything else (the mod, multiplayer) sees the emote's name.
- Link's AI: his `RootAi` is at Link `+0x558`; below it `PlayerRoot`, then `PlayerNormal`, whose current child is the
  action (`PlayerWait`, `PlayerMove`...). An AI node's current child index is its u16 at `+0x1c` (the game's
  getCurrentChild `0x70015377d0`); `+0x1e` is the one before it (0.1.0 read `+0x1e` by mistake). `PlayerNormal` has
  about 170 children (0.1.0 looked at the first 64 only, so it never found the slip-fall).
- Tests: `python -m unittest tests.test_emote_build` (the math, the data, the list merge, a full build with your
  files, and the DLL's own self-test).
- Next: the emote wheel (hold a button, needs the in-game overlay), controller play, and emotes your friends see in
  multiplayer.
