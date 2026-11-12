# Who am I: pick who your friends see you as

Like a ped selector: you pick a character on a wheel, and your friend sees you as that character in their game.
You still play as Link (or Linkle). Tier 1, 2026-11-12.

**Status: built and checked without the game. Nothing here has run in the game yet.** The bodies, the wheel and the
network part are done and tested with made-up data. The [in-game check](#in-game-check) decides what really works.

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
| Races | `gerudo` (`urbosa`) | Urbosa (Gerudo) | `Dm663_Npc_Gerudo_S_A` | 6 + 27 | 16.2 MB |
| Races | `goron` (`daruk`) | Daruk (Goron) | `Dm663_Npc_Assasin_A` | 6 + 27 | 10.9 MB |
| Races | `rito` (`revali`) | Revali (Rito) | `Dm_Npc_UMii_Hylia_BodyS_M_011` | 7 + 26 | 10.6 MB |
| Races | `zora` (`mipha`) | Mipha (Zora) | `Dm_Npc_UMii_Hylia_BodyS_M_003` | 9 + 24 | 11.0 MB |
| Monsters | `bokoblin` (`boko`) | Bokoblin | `Dm663_Npc_Assasin_B` | 26 + 0 | 6.2 MB |
| Monsters | `moblin` | Moblin | `Dm663_Npc_Gerudo_S_B` | 26 + 0 | 7.8 MB |

"Loads" is what the game reads to show that body (model, textures, animation files) when no real one is nearby
(from the study's size-table read). Urbosa is the heaviest: on a slow laptop pick a lighter one.

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
  name, sorted animation names, one clip each, the cutscene dummy's AI, no physics, the right profile, under 64 KB.
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

## Never shipped or sent

Packs, models, textures, animations and ActorInfo are never in this repo and never go over the network. Each PC
builds its own from its own files; `out\` is never committed, and `check_clean.py` flags packs and models. Only ids,
versions and names travel. The Linkle body comes from the Linkle mod on that PC (its own files, read only).

## In-game check

Not run yet. `python tools\mp\charsel_ingame_check.py` does steps 1-4, 6 and 8-9 in one go (it starts the game once
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
