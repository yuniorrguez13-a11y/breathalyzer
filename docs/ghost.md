# Ghost: another player as an actor in your game (experiment)

The ghost mod ([`native/examples/ghost/ghost.c`](../native/examples/ghost/ghost.c)) is a native mod for BotW 1.9.0
that spawns an actor in your game, moves it every frame and animates it. With the multiplayer client's `--ghost`
option it stands where the nearest other player is, so you see them in the world and not only on the radar.

It is not a second Link: the default actor is `Dm_Npc_Sheiker_Searcher_M_001`, the cutscene copy of a Sheikah man
(a person-sized Hylian body, no AI). It stands, walks or runs by the other player's speed (`Wait`, `Walk`, `Run`:
the game's own animations); since 0.4.0 that speed comes from the other player's own client. If it fails 5 times in a
row before it has ever spawned, the mod uses `Dm_Kibako_Contain_01` (the cutscene copy of a wooden crate) instead. It
glides from one network position to the next at the frame rate and faces the way the other player walks. Tested in
the real game on 2026-11-07; see [What was tested](#what-was-tested) and [Stand-ins](#stand-ins-which-actors-work).

**Two PCs over Wi-Fi (2026-11-07):** the ghost stayed in sync with little delay, but it mostly showed the standing
pose while the other player ran. Fixed in 0.4.0 by sending the speed (see [Animation](#animation)). The fix passed
simulated Wi-Fi tests; it is not yet re-tested in the game.

**New in 0.5.0, all opt-in** (nothing changes unless you ask for it; 0.5.1 fixes a review's findings). **Checked in
the game on one PC on 2026-11-07** (tenth round in [What was tested](#what-was-tested): 14 PASS, 1 SKIP). Not yet
tried between two PCs, and attacks with a real weapon are not checked (the test save has no weapon):

- **Animation sync** (`client.py --ghost-sync` on both PCs): each client reads its own Link's animation with the new
  `ghost self` and sends it; the ghost plays that name when its actor has it. Attacks too. See
  [Animation sync](#animation-sync-050-opt-in).
- **Link body** (`client.py --ghost-link`): a small actor pack, built on your PC from your own game files, that gives
  the ghost Link's body and his animations. It works: Link's face and body (bald, in underwear) standing, walking,
  running and swinging next to you. See [Link body](#link-body-050-opt-in).
- **Held items** (0.6.0, `client.py --ghost-items`): the Link-body ghost holds the other player's weapon, shield and
  bow, in its hands or on its back, at Link's size and where Link carries them, and Link can't take them. **0.6.2
  checked in the game on one PC** (2026-11-12, fourteenth round, last run: 22 PASS, 1 FAIL: only H4P, the record of the items' own offsets without the copy). Not tried between two PCs.
  See [Held items](#held-items-060-opt-in).
- The check, with one game and the bot: `python tools\mp\ghost_ingame_check.py --link --spot -1033.6 252.3 1809.3 132`
  (see [In-game check](#in-game-check)).
- **Who am I** (0.8.0, not yet run in the game): `iam zelda` in the console picks who your friends see you as, and
  `client.py --ghost-looks` shows each player as the one they picked (Zelda, a Goron, a Bokoblin...). See
  [Who am I](#who-am-i-080) and [charsel](charsel.md).

## Run it

You need the vanilla game code (`extracted\swgame.dll`): the mod needs no hook slots, so no rebuild.
In PowerShell, from the repo folder:

```
python tools\build_native.py build        # builds build\native\modhost.dll and build\native\ghost-mods\ghost.dll
$env:SAVAGEWIND_GAME_LIB = "$PWD\build\native\modhost.dll"
$env:SWMOD_DIR = "$PWD\build\native\ghost-mods"
$env:SAVAGEWIND_CONTROL = '7777'
Remove-Item Env:SWMOD_REAL_GAME_LIB -ErrorAction SilentlyContinue
& "$HOME\Documents\SavageWind\Savage Wind\savagewind.exe" --play
```

`savagewind.log` should show `[mod] ghost: ready (frame job 0x7001129af8 wrapped)` and
`[modhost] mod ghost.dll: ok, commands: ghost`. Load a save, then use the commands below (over the control socket,
or in the in-game console, F12: `SAVAGEWIND_CONTROL` turns on developer mode, which the command needs).

With another player (see [multiplayer](multiplayer.md)):

```
python tools\mp\client.py --relay 192.168.1.20 --name Ana --ghost
```

Alone, to try it: `python tools\mp\relay.py`, then the client with `--ghost`, then
`python tools\mp\bot.py --radius 6 --speed 2`. The Sheikah man walks a 6 m circle around Link (`--speed 6`: he runs).

## Commands

| Command | What it does |
|---|---|
| `ghost` | The state: `idle`, `pending`, `live` or `far` (too far from Link to spawn: see [Distance](#distance)), `wanted` (false once it stopped trying), `dist_m` (how far from Link, measured flat, it should be; -1: can't tell), `far_m` and `near_m` (the distance rule), `fars` (times it went far), `quick_losses` (times in a row the game removed it within 10 s), `retry_in_s` (the next try, while it waits), the actor's address and position (`x y z yaw`, read back from the actor right after the mod moved it), what last happened (`last`), `tries` (in the current row), `live_s`, counters (`moves`: frames it was moved, `drops`, `targets`: `ghost pos` taken, `snaps`: jumps over 30 m), `interval_s` (smoothed time between targets), `behind_m` (from the shown pose to the latest target; 0 unless it moves by `ghost pos`), `physics` (the actor has rigid bodies), `pulled_back_m` (how far the game moved it between two frames: about 0 when it follows), the animation (`anim`: the mode; `anim_want`: what it asks for; `as`: what the actor plays, read back from the game; `as_ok`: the actor has animations; `speed_mps`: the speed the choice uses; `speed_from`: `sent` (the speed `ghost pos` gave) or `positions` (worked out by the mod); `takes_speed`: true (the mod takes `ghost pos ... speed`; the client looks for it); `as_calls`: play calls; `as_reasserts`: calls again because it played something else; `as_changes`: changes of the automatic choice), and the frame callback's ticks, rate (`ticks_per_s`: one tick a frame, so the frame rate) and threads |
| `ghost spawn [actor] [dx dy dz]` | Spawn an actor `dx dy dz` metres from Link (default `Dm_Npc_Sheiker_Searcher_M_001`, falling back to the crate; 3 0 0). A ghost that is already there is removed first. Sent on the title screen, it waits until a save is loaded. |
| `ghost spawn link [dx dy dz]` | (0.5.0) The Link body: the same actor name, never the crate. It looks like Link only with the [Link body](#link-body-050-opt-in) deployed. |
| `ghost pos x y z [yaw [speed [anim]]]` | Move it there, smoothly (world coordinates; yaw in degrees, 0 faces +z, 90 faces +x; a jump over 30 m is not smoothed). `speed`: the other player's ground speed in m/s, for `Wait`/`Walk`/`Run` (0.4.0 and later; left out or negative: the mod works it out from the positions). `anim` (0.5.0): the other player's animation name (`-`: none), see [Animation sync](#animation-sync-050-opt-in) |
| `coop story load <list> [dlc]`, `coop story get`, `coop story add <hex>`, `coop story status` | (0.7.0; in the game 2026-11-12: the setter works through the developer probe, reads are off until a safe getter is found. 0.7.1, not yet run in the game: live adds only for towers and memories, only when Link is free for 2 s; `status` says `safe` and `held`) Story flags for co-op: read the story list's flags through the game's flag manager and add a host's (only 0 to 1); `coop probe flag <name> [1]` in developer mode. See [co-op](coop.md) |
| `ghost self` | (0.5.0, read only) Your own Link: `anim` (the name to send), `attack`, `attacks` (count), `body`, `lower`, `upper` and `gesture` (what plays in those slots) with weights, `weapon`, `shield`, `bow` (actor names, "" when none, "?" unreadable), `equip_ok`, `weapon_class`, `shape` (subs per animation slot, `2,2,1,3,1` expected), `found`, `active`. Each call keeps the reads going for 2 s; the first answer has `active` false |
| `ghost follow dx dy dz` | Keep it at that offset from Link, turned like Link (a demo without a network) |
| `ghost stop` | Stop moving it (it stays where it is) |
| `ghost despawn` | Remove it |
| `ghost anim auto` | The default: `Wait`, `Walk` or `Run` by its speed (see [Animation](#animation)) |
| `ghost anim off` | Play nothing (the actor keeps what it plays; the Sheikah man stands in a T-pose until something plays) |
| `ghost anim <name>` | Always play this animation set of the actor (`Wait`, `Walk`, `Run`, `RunGear2`, `Turn`...). A name the actor doesn't have changes nothing: the mod asks 3 more times, then `last` says so |
| `ghost far <far_m> [near_m]` | The distance rule (default `60 50`; `near_m` left out: 10 m less). `ghost far 0` turns it off (for a test, or an actor the game keeps farther away) |
| `ghost items <weapon\|-> <shield\|-> <bow\|-> [mask]` | (0.6.0) What the ghost holds: item actor names, `-` for none. `mask` (0-7, default 4): which go on its back (bit 0 weapon, 1 shield, 2 bow). See [Held items](#held-items-060-opt-in) |
| `ghost items off` | (0.6.0) Hold nothing (the items are deleted) |
| `ghost items back on\|off` | (0.6.0) Back placement on (default) or off: off, everything is in the hands |
| `ghost items rel <slot> <12 numbers>\|-` | (0.6.2) Where slot 0-2's item sits on the back: a 3x4 matrix, row by row, in the frame of the ghost's `Pod_A` (as `ghost self` gives Link's own, `<item>_rel`); `-`: the item's own offsets again. The client sends the other player's |
| `ghost give <actor> [dx dy dz]` | (0.6.0, for tests) Spawn an actor near Link (default 1.5 m in front of him, 0.8 m up) for him to take. It never touches the ghost or its items |
| `ghost looks <list> [current [seen]]` | (0.8.0) The characters `iam` takes, from the client: `id/alias/alias,...` (lower-case letters, digits and `-`), your current pick, and the last `iam_seq` the client read (the typed word is cleared when it matches; without `seen`, always). See [Who am I](#who-am-i-080) |
| `iam [name \| next \| back]` | (0.8.0, player safe: the command works without developer mode; the client that sends your pick uses the control port, which turns it on) Who am I: `iam` lists the names, `iam zelda` (or an alias: `iam daruk`) picks one, `next`/`back` go round the list. See [Who am I](#who-am-i-080) |

The commands only leave a request; the mod acts on it in the next frame. A `ghost spawn` sent in the same batch as a
`ghost pos` spawns the actor right at that position. Of a `spawn` and a `despawn` sent before that frame, the later
one wins.

## Client options

| Option | What it does |
|---|---|
| `--ghost` | Show the nearest other player as the ghost |
| `--ghost-peer NAME` | Show this player instead (implies `--ghost`) |
| `--ghost-actor ACTOR` | Another actor name (implies `--ghost`), e.g. `Dm_Npc_Zelda_Sibyl` |
| `--ghost-sync` | (0.5.0, checked in game on one PC) Also share animations and say what the other player holds. Use it on both PCs, each with ghost mod 0.5.0 (implies `--ghost`) |
| `--ghost-link` | (0.5.0, checked in game on one PC) Show the other player with the [Link body](#link-body-050-opt-in) (implies `--ghost-sync`; not together with `--ghost-actor`) |
| `--ghost-items` | (0.6.0, checked in game on one PC) The ghost also holds what the other player holds. Needs `--ghost-link` (or `--ghost-looks`) and ghost mod 0.6.0 on your PC (implies `--ghost-sync`). Holding is checked on the Link body only |
| `--ghost-looks [DIR]` | (0.8.0, not yet run in the game) Show each player as the one they picked on the Who am I wheel, from the characters this PC built (`tools\charsel.py build`); a pick this PC lacks is the Link body (implies `--ghost-link`). See [Who am I](#who-am-i-080) |
| `--look NAME` / `--no-look` | Who you are for the others (default: your pick on the wheel, else Link) / say nothing (join as a visitor, as before) |

The `ghost` reply of 0.5.0 also has `version`, `takes_anim` and `has_self` (the client looks for these), `remote`
(the other player's animation name now), `remote_calls`, `remote_misses` and `remote_missing` (the names the actor
turned out not to have), and `link_actor`.

Every tick (20 a second) the client adds `ghost pos x y z yaw` with the other player's position to the batch it
already sends the game (the same jitter-buffered position the radar shows), `ghost spawn` once at the start, and every
2 s `ghost`, whose news it prints. The yaw is the direction that player moves in, from one position to the next on
the ground (`Heading` in client.py): slower than 0.5 m/s (standing), or a teleport (a step over 30 m or faster than
40 m/s), keeps the last one; before they have moved at all it is their camera direction. The jitter buffer doesn't
blend across a teleport (two samples over 30 m apart, or faster than 40 m/s between them), so it reaches the mod as
one `ghost pos` step, which the mod jumps when it is over 30 m. When that player leaves it sends `ghost despawn`
until the game answers it (a lost batch or a reconnect sends it again), and when the client stops it sends it once
more if the game may still have the ghost. If a `ghost` reply says `wanted` false (the mod stopped trying), it sends
`ghost spawn` again after 10 s, then 20, 40, at most 60 s apart; back to 10 s once a ghost has stayed 10 s. If the
game has no `ghost` command (no mod) it says so once and shows the radar only. `Ghost:` in the window's status says
what it shows (`(far)` while the other player is too far away to show).

The client also sends the other player's speed: `ghost pos x y z yaw speed`. Each client measures its own player's
speed from its own positions and clock (the straight distance covered over the last 0.4 s) and sends it with every
state (the `speed` field, see [multiplayer](multiplayer.md#how-it-works)). Network jitter can't change it. A frame
hitch in the sender's own game (a screenshot, an autosave: one frame of 0.2-0.5 s) doesn't either (0.5.1): the game's
clock may stop during it, so Link barely moves while the client's clock runs on, which read as a dip in speed (the
ghost went Run, Walk, Run). Now a step over 0.15 s and over 3 usual steps starts the measurement again, and the last
speed stays until 0.4 s is measured again. An older client sends no speed; then the receiving client works it out from that player's samples and the times they carry
(the sender's clock, not the arrival times). The speed goes with the position the client shows (blended the same
way). During a network stall the client holds the other player where it last saw them, with their last speed, so the
ghost keeps running on the spot instead of standing; after 1 s with no news the speed is 0. The client sends the speed
only to a mod that says it takes it (`takes_speed` in the `ghost` reply, asked at once on each game connection); an
older mod gets `ghost pos x y z yaw` as before.

## Animation

With `ghost anim auto` (the default) the mod picks `Wait`, `Walk` or `Run` from the ghost's ground speed:

| Now | Next |
|---|---|
| `Wait` | `Walk` from 0.4 m/s, `Run` from 3.2 m/s |
| `Walk` | `Wait` below 0.3 m/s, `Run` from 3.2 m/s |
| `Run` | `Walk` below 2.6 m/s, `Wait` below 0.3 m/s |

- **No flicker**: the gaps between the thresholds (0.3/0.4 and 2.6/3.2 m/s) keep a speed near one from switching back
  and forth, and a choice stays at least 0.25 s.
- **The speed (0.4.0)** is the one `ghost pos` sent, smoothed a little (0.1 s), while the latest `ghost pos` sent one
  and came less than 1 s ago (`speed_from` `sent`). The client sends the other player's own measured speed, so Wi-Fi
  jitter can't change it.
- **Without a sent speed** (an older client, or `ghost follow`), the mod works it out (`speed_from` `positions`): the
  straight distance the ghost covered over the last 0.5 s, from where the mod shows it each frame, smoothed a little.
  A position that comes late, after a network stall, still counts: it only moves the ghost later, and over half a
  second the distance comes out right. During a stall the client repeats the last place it had; the mod times those
  repeats from the first one (for up to 0.6 s), so the speed stays as it was and the catch-up after the stall is not
  taken for a burst of speed. Repeats for longer mean the player stopped. A slower choice is taken only after it has
  been wanted for 0.2 s (a frame hitch holds the ghost back a moment). So a stop shows about 1 s late on this path
  (0.1-0.4 s with a sent speed).
- **A jump is not a speed** (without a sent speed): a step of a `ghost pos` faster than 40 m/s (a short teleport), or
  one after more than 1 s without a `ghost pos`, is not measured; the mod measures again once the ghost has glided
  there. A step of up to 10 m right after the ghost moved (within 1 s) counts: that is a late position catching up
  after a stall. A step over 20 m in one frame (a snap over 30 m) and a frame after more than 1 s without one (a load)
  start the measurement again too.
- **Before 0.4.0** the mod measured the speed over 0.25 s of `ghost pos` arrivals and threw away steps that looked
  like jumps: after a 0.3 s gap, or faster than 40 m/s. Over real Wi-Fi the client's positions stop and catch up all
  the time, so most steps were thrown away and the ghost stood (see [What was tested](#what-was-tested), seventh round).
- **One call per change**: the mod calls the game's "play animation set by name" (`0x700151e42c`, with a stand-in for
  the "action" that holds only the actor; morph frames from the actor's own animation list) only when its choice
  changes, not every frame. 5 times a second it reads back what the actor plays (`0x70014a0040`) into `as`. If the
  actor plays something else (its own AI changed it), it asks again, at most once a second (`as_reasserts`). A name
  the actor never played gets 3 more tries, then `last` says so.
- **Only the actor's own animations**: before each call the mod checks that the actor has an animation holder
  (`+0x560`) that belongs to it (`holder+0xd8` = the actor) with at least one slot (`as_ok`). The crate has none, so
  nothing is called for it.
- **Thread**: the call runs in the mod's frame callback, on a game worker thread, not in the actor's own update. The
  research flagged that as a risk (the call rebuilds the actor's animation tree at once) and planned a fallback
  (call it from the actor's own AI update through `hle_override`). The fourth round's long runs made only about 50
  play calls. So a stress test (fifth round) made 17,767 play calls: `Walk` and `Run` in turn, 10 a second, 10
  minutes each with the Sheikah man standing, Zelda standing, and the Sheikah man following a moving Link. No crash,
  no hang, no stalled frame callback. That is good evidence, not proof. The fallback is not built.
- **Look**: the clips play at their own speed, so the feet slide a little when the speed doesn't match (a 2 m/s walk
  and a 6 m/s run look right). Each new `Walk` starts with a walk-start step. No turn, jump, climb or riding pose.

## Animation sync (0.5.0, opt-in)

**Checked in the game on one PC** (tenth and eleventh rounds): `ghost self` reads Link (`Wait`, `Move` for walk,
run and sprint, `Jump`, `Land`, `ClimbMove`...), his sword attack (`CutS1`, `attack` true) and the sword's name
(`Weapon_Sword_001`, of the Weapon class); the bot's names reach the ghost and play on the Link body; on the Sheikah
man the names he lacks are noted and shown by speed; the client sends its own `Wait`/`Move`. **Not checked yet:** the
shield and bow names (the test save has none), the bow names `BowCharge`/`BowShoot` from a real bow, and two PCs.
The rest below comes from reading the game's code and files (out\research\player-state-plan.md).

- **Turn it on:** `client.py --ghost-sync` (or `--ghost-link`) on both PCs, each with ghost mod 0.5.0. Without it,
  nothing changes: the client sends no new fields, and the mod gets no names.
- **The sender** asks its own mod `ghost self` 20 times a second. The mod reads Link's animation in its frame tick:
  the body slot (`Wait`, `Move`, `Jump`, `Fall`, `ClimbMove`, `CutS1`...), and the upper-body slot for the bow
  (`BowCharge`, `BowShoot`). Plain memory reads, no game calls, only while a client asks. Every pointer is checked
  first (Windows says whether the memory is there), so a wrong one gives an empty or `?` answer, not a crash.
- **Attacks** are the names that start with `Cut`, plus `SwimAttack` and the bow. A short attack is reported for at
  least 0.3 s, so a client asking 20 times a second always sees it.
- The client sends the name as `anim` in its state (and `attack`, `weapon`, `shield`, `bow`). These are optional
  fields: older clients ignore them, older relays drop them.
- **The receiver** passes the name to its mod: `ghost pos x y z yaw speed anim`, only when the mod says
  `takes_anim`. The mod then picks what to play:
  - `Wait` and `Move`: by speed, as before (walking, running and sprinting are all `Move` on Link).
  - A name the actor has: that name.
  - An attack the actor lacks: `CutS1`, if it has that. Anything else it lacks: by speed.
- **Which names the actor lacks is learned.** The play call switches at once when the actor has the name. If the
  name doesn't play 0.1 s later, the mod asks once more (the game might show it a little late); if it still doesn't
  play 0.1 s after that, the mod notes it (`remote_missing`) and doesn't ask for it again on that actor. A name that
  plays clears its misses. The Sheikah man has only `Wait`, `Walk`, `Run`, `RunGear2`, `Turn` and `Move_End`, so he
  shows every other name by speed (each one costs two calls, once per ghost). The
  [Link body](#link-body-050-opt-in) has the names in its table.
- **Room for every name (0.5.1).** The mod keeps up to 512 names (Link's own list has 399), and up to 512 noted
  missing per actor. The last 32 name slots are only for `ghost anim`, so names from the network can never lock it
  out. (0.5.0 kept 128 and 64: a sender whose Link played many names the body lacks filled the table, and the body
  then never showed a new name, say a glide or an attack first seen late.)
- **Attacks during a Wi-Fi stall (0.5.1).** A quick attack can happen while the network stalls: its samples then
  arrive after the moment the client shows, and were skipped. Now the client shows such a skipped attack for 0.3 s
  right after the stall (unless the player is already in another one).
- **Two-handed swords and spears (0.5.1).** The player's Link plays the same names (`CutS1`...) whatever he holds. So
  the receiving client sends `CutS1_Lsword`, `CutSF_Lsword`, `CutS1_Spear`, `CutS2_Spear` or `CutSF_Spear` (the Link
  body's own swings) when the sender's `weapon` is a `Weapon_Lsword_...` or `Weapon_Spear_...`. An actor without
  them shows the `CutS1` stand-in, or the speed rule.
- **No flicker:** the shown name changes at most every 0.25 s, as before. A name older than 1 s (no `ghost pos`
  since) is dropped. After 1 s with no news from the other player, the client drops their name too.
- **Held items** (`weapon`, `shield`, `bow`: actor names like `Weapon_Sword_001`) are sent and the receiver logs
  them ("Ghost: Ana has weapon ... (not drawn on the ghost yet)"). They are not drawn. Next work: attach the item's
  actor to a bone of the ghost (Link's skeleton has `Weapon_R`, `Weapon_L` and `Weapon_Root`); no safe way to do
  that was found yet.
- **Not known yet:** whether a weapon is drawn or on Link's back; Link's real velocity (the speed still comes from
  positions); which of Link's 5 animation slots the game really uses for what (the in-game check prints them).

## Link body (0.5.0, opt-in)

**Works in the game** (tenth round): Link's face and body, standing, walking, running, and every name tried (18 of 18:
attacks, jump, glide, climb, swim, bow, squat...). The first build showed no animation: the game looks names up by
a binary search, so the list must be sorted by name, like the game's own lists; the builder sorts it since then.
Only two actors in the game use Link's model: the player and the pause-menu Link. Neither works as a ghost.
So `tools\mp\ghost_link_pack.py` builds our own small actor pack on your PC, from your own game files:

```
python tools\mp\ghost_link_pack.py build          # writes out\ghost-link-mod (a RomFS mod folder)
python tools\mp\ghost_link_pack.py check          # reads it back: names, clips, size
```

**Build it again** if you built it before the sort fix (`ghost_link_pack.py check` lists the names; `BowCharge` must
come first, else the ghost plays no animation). `python
tools\mp\ghost_ingame_check.py --link` builds it and deploys it to the check's own overlay (`out\ghost-check-overlay`),
used only for that game run; it passed there. Before adding it to your mods, keep in mind: the pack replaces the default
stand-in's actor, so while it is deployed every ghost is the Link body, plain `--ghost` too. If the pack crashes the
game, every use of the ghost crashes it until you remove the pack (no crash in the tenth round). Deploy it like any
RomFS mod (docs/romfs-mods.md) and use `client.py --ghost-link` (or `ghost spawn link`).

- **Same name as the stand-in.** The pack replaces `Dm_Npc_Sheiker_Searcher_M_001`, which nothing in the game uses
  (checked: events, maps, every pack; DLC not checked: the dump has none). So the ghost mod needs no other name, and
  without the pack the Sheikah man is back.
- **What is in it:** the Sheikah man's actor link (no AI) with no physics; Link's model list (the pause-menu
  Link's, with the Sheikah man's animation slots; `--slots pause` keeps the pause-menu Link's own slots, the fallback
  if Link stands in a T-pose); and our own animation list: Link's animation file and one small animation set per
  name. About 19 KB unpacked; the game's size table allows 348 KB for this name, so no size table change.
- **The names** (`python tools\mp\ghost_link_pack.py names`). The player's own (what `ghost self` reads, so the
  receiver replays them as they are): `Wait`, `CutS1`-`CutS3`, `CutSF`, `CutDash`, `CutJumpSt`, `CutCharge`,
  `BowCharge`, `BowShoot`, `GuardJust`, `Jump`, `Fall`, `Land`, `ParashawlGlide`, `ClimbWait`, `ClimbMove`,
  `SwimWait`, `SwimMove`, `SwimDash`, `SquatWait`, `SquatMove`, `DamageS`, `Dead`, `WeaponEquipOn`. Our own (checked
  against the player's list of 399 names: he has none of these): `Walk` and `Run` (the mod's speed rule; the player
  has one `Move`), `RunGear2` (only for `ghost anim`), and the two-handed and spear swings `CutS1_Lsword`,
  `CutSF_Lsword`, `CutS1_Spear`, `CutS2_Spear`, `CutSF_Spear`, which the receiving client picks by the sender's
  weapon. (0.5.0 also had `Guard` and `Jump_Run`, which the player never plays: dropped.)
- **What it looks like (seen in the game):** Link's face and body, but bald and in underwear (hair and clothes are
  separate armor actors), holding nothing, no paraglider. Glide, climb and swim play on the ground where the ghost
  stands. Screenshots `out\shots\ghost2_*.png`.
- **Risks:** the new files in the pack have no size table entries (the game estimates small files; they loaded
  fine); the bounding box is the Sheikah man's. Replacing an actor pack through the overlay works.
- Keep the built folder to yourself (it is made from your game files). `out\` is never committed.

## Held items (0.6.0, opt-in)

**0.6.2 works in the game on one PC** (2026-11-12, fourteenth round, five runs; the last: 22 PASS, 1 FAIL, the one a record kept on purpose). The
Link-body ghost holds a sword, two-hander or spear in its right hand and a shield on its left arm, with the bow on its
back, as Link carries them with a sword out; with the bow drawn, the bow is in its left hand and the rest on its back;
after a sprint everything is on its back. The items are Link's size (they were 1.4-1.5 times bigger before 0.6.2).
It follows a real Link through two clients and a relay (sprint, draw, throw, a new sword: each shown 0.1-0.45 s
later), switches, clears, and Link can't take its items. A reload after Game Over leaves no second ghost (0.6.2).
On its back the items sit exactly where the other player's Link carries his (0.000 m off, with his own offsets sent
over the network). The frame cost is below what the 30 fps cap can show: 30.0 frames a second with and without the
items in 3 pairs out of 3 (fourth run of the fourteenth round, with the PC otherwise idle). Not yet tried between two
PCs.

```
python tools\build_native.py build                       # ghost.dll 0.6.2
python tools\mp\client.py --relay 192.168.1.20 --name Ana --ghost-link --ghost-items
```

Use it on both PCs (each sends what its own Link holds; ghost mod 0.6.2 on each). Without `--ghost-items` nothing
changes. An older client on the other PC (no `on_back`) still works: its weapon, shield and bow show, with the bow on
the back (H10 in the game: each change shown within 0.33-0.63 s in runs 1 and 3; 1.06 s once in run 2, over the 1 s
limit).

**Load ghost.dll at game start only** (the normal way: modhost loads `mods\native` when the game starts). Never
`modunload` or `modreload` it while the ghost holds items: the items' bone follows on the back and the item and
actor names live in the DLL's memory, so the game would read freed memory.

- **How.** For each of the other player's items the mod spawns that item (a real item actor, by its name) 6 m over
  the ghost's feet, checks it is of the Weapon class, then calls the item's own "grab" with the ghost as the holder.
  From then on the game itself keeps the item on the ghost's hand bone (`Weapon_R` for the weapon, `Weapon_L` for the
  shield and the bow), every frame, and turns the item's pick-up off. The grab is refused until the item's AI has
  run once, so the mod asks each frame for up to 1 s; until it is taken, the item is a loose world item, so the mod
  keeps it up there each frame, out of Link's reach (0.6.1). In the game (0.6.2, parked over the ghost): all three
  held 0.27-0.55 s after the request, the grab never refused first (every "holds" note of run 1's log).
- **Link's size** (0.6.2). The weapon's own per-frame update (`0x70007a95d8`) sets its scale by its holder: in the
  player's hands its base scale times one factor, held by anyone else (an NPC, our ghost) times another: 1.5 for the
  sword and the shield, 1.4 for the bow. So the ghost's items were 1.4-1.5 times Link's (a giant spear and
  two-hander, a shield over half the ghost's height; fourteenth round runs 1-2). Setting the scale from the frame job
  did not stick (the update writes it again each frame: 4,627 sets, no change in the pictures). 0.6.2 wraps the
  update (`hle_override`) and, for the ghost's own items only, puts the player's scale back right after it (the same
  reads the game's player branch makes). In the game: size 1.00 in all 177 samples of H1 and H4, the same as Link's
  own, and the pictures show the shield on the ghost's back the size of Link's
  (`out\shots\ghostitems-r14-run3\ghostitems_H15_new_sword.png`). `items.N.size` reports it.
- **Only weapons, shields and bows** (0.6.1): the mod spawns a name only if it is a one-handed sword, two-hander or
  spear for the right hand (`Weapon_Sword_`, `Weapon_Lsword_`, `Weapon_Spear_`), a shield (`Weapon_Shield_`) or a bow
  (`Weapon_Bow_`) for the left, and not one of the special ones never tried: `Weapon_Sword_070` and the four the
  game's data gives the same sort key (071, 072, 080, 081), `Weapon_Sword_500` (sort key 0: not an inventory item) and
  `Weapon_Bow_071`. The client sends only such names too. Anything else shows as none (`ghost items` says it in
  `refused`), so another player's data can never make your game spawn an enemy, a person or anything else.
- **Switching** deletes the old item and spawns the new one; **dropping** deletes it. An item is never "let go"
  (a let-go item falls and can be picked up). If the game ever lets one go anyway, the mod deletes it at once and asks
  for it again (at most 3 tries per name). A gap in the other player's data (a line without the item names: their
  game didn't answer in time) changes nothing; only an explicit "none" clears (0.6.1).
- **Never Link's.** If Link ever took an item before the ghost's grab did (the mod checks the item's holder links),
  the item is his: the mod never deletes it, only forgets it (`items.taken`; tried in the C selftest only). Before an item is
  deleted it gets its own bone follow back, so no item ever runs the mod's follow after the mod let it go; if the game
  refused that, the item is kept, not deleted (`items.kept`; C selftest only).
- **On the back.** The client sends which items are on the sender's back (`on_back`, read from Link's own equipment:
  `ghost self`) and, from 0.6.2, where each sits there (`weapon_rel`, `shield_rel`, `bow_rel`: its matrix in the
  frame of his Link's `Pod_A` bone, also from `ghost self`). The mod then moves the item to a bone follow of its own
  on the ghost's `Pod_A` bone, with that matrix as the offset (`ghost items rel`), or, from an older sender, with the
  item's own "on the back" offsets from the game data. In the game (0.6.0) each item sat at its offset's distance from `Pod_A`:
  that only shows the mod's follow is on. With the item's own offsets, against Link's own (H4P, each item in its
  `Pod_A`'s frame): the weapon matches (0.000 m, 0 degrees), the shield is 0.010 m off and **the bow 0.108 m and 4
  degrees**. The reason: the game adds a second offset for the player's bow and shield from his armor (the item's
  virtual calls at Weapon vtable `+0x780` and `+0x790` ask the armor actor's own; zero for anyone but the player), so
  the item's data alone can't put it where Link's is. With Link's own offsets copied (H4L, 0.6.2): **all three 0.000 m
  and 0 degrees off** (`out\shots\ghostitems-r14-run4\ghostitems_H4L_like_link.png`: the ghost and Link from
  behind, the same shield and bow placement). Through the network (run 5, H15: the sender's offsets in his state, the receiving client sends them to the mod): all three 0.000 m off 0.07 s after his sprint (`out\shots\ghostitems-r14-run5\ghostitems_H15_back_like_link.png`). An older sender sends no offsets: then the item's own (the bow 0.1 m off). The item that rose high over the ghost's shoulder in the twelfth
  round was the 1.4-1.5 times scale (fixed in 0.6.2). `ghost items back off` keeps everything in the hands.
- **Can't take them.** Link walked up to the ghost and pressed A five times, unarmed and armed: no prompt, his own
  items stayed his, the ghost's stayed the same actors, still held (twelfth and fourteenth rounds, every run).
- **Cleanup.** The items go before the ghost does (`ghost despawn`, the distance rule, a new spawn) and when the game
  removed the ghost. On a reload (a new spawn heap) they are forgotten without a call, as the ghost is, but still
  watched with plain reads (0.6.1), so one the reload left in the world shows in `items.stray` (still there 1 s after
  the mod let it go). The same heap with another player actor is no reload: the ghost and its items are removed the
  normal way; with no player actor for a moment and the same heap, they are left alone until he is back (0.6.1).
  **Reload (0.6.2):** the fourteenth round's first run fell 90 m (Game Over, Continue) while the ghost was being
  spawned again: a ghost that had spawned 0.1 s before the stage change survived it, holding two items, next to the
  new ghost (`out\shots\ghostitems-r14-run1\ghostitems_H9_after_reload.png`: two ghosts; `stray` 2). So 0.6.2 keeps
  watching what a stage change dropped (plain reads), and once Link has been on the new stage for 3 s it removes
  whatever of it the game kept, the normal way (the game's own link check first; an item gets its own bone follow
  back first; never Link's). Runs 2 and 3: the ghost and its items back 12-15 s after the fall, `stray` 0, one ghost
  in the picture. The same race came up in both (the ghost spawned again 0.1-0.2 s before the change); the log
  says the old ghost and its items were removed 3 s after the change (`leftovers` 4 in run 2), no crash. Checked in the game: despawn, the distance rule,
  the game removing the ghost, the reload.
- **Which body.** Only the Link body has the hand bones; on the Sheikah man the mod finds none and spawns no item
  (`items.bones` says `missing`).
- **Cost.** The 30-110 ms item frames of the earlier rounds were the mod's memory check: Windows' `VirtualQuery`
  walks every page of the region it is asked about, and the game's heap is one region of gigabytes, asked about
  again twice a second (fourteenth round run 1: item parts of 10-113 ms with no game call and the thread busy the
  whole time; 3,529 microseconds a frame on average). 0.6.2 keeps the answer for a big region (64 MB or more) and lets
  a helper thread of its own, at the lowest priority, confirm it every 20 s (only `VirtualQuery`, never game code);
  small regions are asked again after 0.5 s as before. Since then the items' part is 12-13 microseconds a frame on
  average (the plan's limit is 50) and the whole tick at most about 0.3 ms, with 0.003-3 game calls a frame. Frames a
  second, ghost alone against ghost with its 3 items, **3 pairs in turn, medians** (run 4, 2026-11-12 08:59-09:01,
  the PC otherwise idle; 7.5 GB of 15.3 GB RAM free before the game, 1.9-2.4 GB while it ran): **30.0 -> 30.0** (every
  pair 30.0/30.0), items 9.5 microseconds a frame, no frame over 50 ms, the longest 41 ms. Run 5 (09:09-09:10, 2.5-2.8 GB free while the game ran): the same, 30.0 -> 30.0 in all 3 pairs, 9.5 microseconds a frame. The game is capped
  at 30 frames a second here (`savagewind.ini`), so a cost that only eats headroom above 30 doesn't show; uncapped it
  was not measured. Run 3's 30.0/29.0 -> 25.4/28.7 came while the user was working on the laptop (RAM-heavy). The
  helper's own `VirtualQuery` takes up to 217 ms (other threads' memory calls may wait on it); every 20 s per big
  region (3 here).

`ghost` reply fields (0.6.0): `has_items` (the client looks for it), `items.on`, `items.back`, `items.mask`,
`items.bones` (`ok`, `ok, no Pod_A`, `missing ...`, `looking`, `no model`), `items.stray`, `items.us` (median
microseconds of the items' part of a frame; `items.us_avg`, `items.us_max`), `items.calls` and `items.queries` (game
calls and memory queries a frame), `tick_us` (the whole frame's tick), counters `items.requests`, `items.held`,
`items.deleted`, `items.lost`, and per slot (0 weapon, 1 shield, 2 bow) `items.N.name`, `state` (`none`, `wait`,
`pending`, `grab`, `held`, `failed`), `actor`, `place` (`hand`, `back`), `dist_m` (from the item to its bone),
`hold_m` (the length of its "on the back" offset), `tries`, `item_state` (255 held), `holder_ok`, `why`; and
`give.state`, `give.name`, `give.actor`. 0.6.1 adds `items.dying` (deleted items still there, any age),
`items.us_mean` (the plain mean of the last 64 item parts), `items.taken`, `items.kept`, `items.parks`,
`items.slow_parts` and `items.slow_part` (item parts over 5 ms: wall time, the thread's own cycles, game calls),
`slow_calls`, `slow_call` and `call_most_ms` (game calls over 5 ms: name and time), `frame_ms`, `frame_ms_max`,
`frames`, `frames_slow` (frames over 50 ms), `no_player`, `no_player_ticks`, and per slot `refusals` (grab calls
refused before the grab was taken) and `rel` (the held item's matrix in the ghost's `Pod_A` frame). 0.6.2 adds per
slot `size` (how big it is drawn: its matrix's axes times its own scale; 1 = Link's), `size_first`, `axis`,
`own_scale`, `model_scale`, `copied` (on the back with the other player's own offset); `has_items_rel` (it takes
`ghost items rel`), `items.scale_sets` (player's scales set by the wrapper), `ghost_scale`, `mem.queries`,
`mem.slow`, `mem.most_ms` (`VirtualQuery` on the tick: count, over 5 ms, longest), `mem.big`, `mem.helper`,
`mem.helper_ms`, `mem.widened` (the helper thread), `leftovers`, `old_ghosts`, `old_items` (what a stage change
dropped: removed, watched). `ghost self` (0.6.0) adds `weapon_back`, `shield_back`, `bow_back` and `on_back`; 0.6.1
`weapon_rel`, `shield_rel`, `bow_rel` (each of Link's items in his `Pod_A`'s frame) and `weapon_pods`, `shield_pods`,
`bow_pods` (its distance to his `Pod_A`, `Pod_B`, `Pod_C`); 0.6.2 `weapon_size`, `weapon_axis` (and the same for the
shield and the bow).

**Not done:** special items (the Master Sword, torches, boomerangs, elemental weapons) were not tried, and the
Master Sword family and `Weapon_Bow_071` are refused until they are; a sheath or quiver is not drawn; the ghost's own
pose doesn't change for a shield or a bow (it plays the other player's animation, as before). **Not verified:** two
PCs; the frame cost above the 30 fps cap. **Load ghost.dll at game start**
(as before): 0.6.2 also wraps the Weapon class's update and runs a helper thread (its `swmod_exit` stops the thread
for a `modload`ed copy). **Armor is not built:** it would work the same way (an armor actor bound to the ghost copies
its pose by bone name), but almost every head piece and most upper pieces have cloth physics, estimated at 1-4 frames
a second, and some of the game's code paths would meet a missing pointer that is not checked yet
(out/research/ghost-items-plan.md, "Armor").

## Who am I (0.8.0)

Not yet run in the game. Pick who your friends see you as ([charsel](charsel.md)): on the wheel, with
`python tools\charsel.py pick zelda`, or in the game's console (F12) with **`iam zelda`**. (`look` is taken: the game
uses it for the camera.) `iam` only keeps the word you typed, under the lock: no game call, no game memory, so it is
player safe and the command works without developer mode. The list and your pick travel through the client's control
port, though, which turns developer mode on.

- `iam`: who you are now and the names you can type (`names` in the reply). Before a client has given the list it
  says to start the multiplayer client, and a word is kept as typed ("Kept: zelda. The multiplayer client checks it
  and tells your friends once it runs with --ghost-looks"). After `iam <name>` it says "Your friends see it in a few
  seconds" only while a client asked for `ghost` in the last 6 s; otherwise that the client tells them when it runs.
- `iam <name>`: an id or an alias from the list, any case (`iam Daruk` picks `goron`); a name not on the list is
  refused with the names. `iam next` / `iam back` go round the list in the wheel's order.
- The client gives the list on each game connection and each change (`ghost looks <list> <pick> <seen>`,
  `client.iam_list()`: 11 characters with their aliases; up to 48 entries; `seen` is the last `iam_seq` it read) and
  reads the `ghost` reply every 2 s: `has_iam`, `iam_seq` (one more for each `iam <name>`) and `iam` (the id typed
  last, "" once a client saw it), plus `iam_current` and `looks`. A new `iam_seq` becomes your pick: the client sends
  it to the relay (a `look` message) and keeps it in your wheel choice (not with `--look`). The first `iam_seq` on a
  connection is only where the client starts: its word counts only if no client took it yet and no `--look` was
  given, so an old `iam` never wins over a newer pick. `--no-look`: no list.
- With `--ghost-looks` the client spawns each player's pick by the actor name this PC built it on, and only when
  that pack is in your game (the library copy of the characters, turned on, with the pack in the deployed overlay:
  `charsel.here_catalog`), never a name from the network. With three or more players the ghost changes to another
  player only when that one is 2 m nearer for 1 s (each change of body is a new spawn). A new pick is a new `ghost spawn` (the mod
  replaces the ghost). A body the mod gives up on (`wanted` false) is the Link body for the rest of the session, and
  the log says so. Held items go to the new body again after a change.

Self-test (`tests\test_ghost_native.py`, group `iam`): no list ("Kept"), the list taken or refused whole, aliases in
any case, an unknown name refused with the list, next/back round the list, the reply's fields, "a few seconds" only
while a client asks, `seen` clearing the old word only when nothing newer was typed, a bad `seen` refused, the client's
own list fits.

## How it works

- **A per-frame callback without a rebuild.** `0x7001129af8` is the run function of one of the game's per-frame calc
  jobs ("Awereness+LOD+DL1"). It is only ever called through a vtable, so through Savage Wind's dispatcher, and
  `hle_override` can replace it. The mod's wrapper calls the game's function, then `ghost_tick`. It saves the guest
  registers before its own calls and puts them back after.
- **Only in a world.** The title screen has a player actor too, but its position reads 0 0 0 (the rule Savage
  Wind's `pos` uses for "No world loaded"). An actor spawned there crashed the game in the test, so the mod requests
  nothing until Link has a position. Waiting there doesn't use up tries.
- **Spawning** follows the pattern of the game's own code (out\research\actor-spawn-verify.md, read-only research on
  the user's own 1.9.0 main): zero the handle, a parameter pack with `@M` (a 3x4 matrix at the spawn point),
  the spawn request (`0x7001535b10`, async). Each frame while pending: if the handle's unit no longer points back
  at our handle, forget it; when ready, check that the created proc is an Actor, set its matrix, take it (which wakes
  it) and keep a link to it; when failed, release it.
- **Moving**: each frame, after checking the link, "set position and rotation" (`0x70015223f0`) with the position
  and a rotation about y. An actor with physics (`+0x578` set; the people's cutscene copies have it, the crate doesn't) puts its
  matrix back from its rigid body every frame, so for those the mod also calls the physics transform
  (`0x7001212820`, which moves every rigid body of the actor). Without it Zelda stayed where she was spawned while
  `ghost` showed the new position (see [Stand-ins](#stand-ins-which-actors-work)). **Removing**: "remove at a safe
  point later" (`0x7001514bac`).
- **Smoothing**: the mod keeps the latest `ghost pos` target with its arrival time, and the time of the one before.
  Each frame the shown pose moves in a straight line from where it was when the latest target arrived to that
  target, over the time between targets (smoothed, clamped to 16-250 ms). So the 20 Hz targets become an even move
  every frame instead of a 10 cm step every third frame. It reaches each target about when the next one comes (one
  target, about 50 ms, behind). The yaw turns the short way round, over at least 0.1 s. A target more than 30 m from
  the shown pose (a teleport) is jumped to. With no new target it stays at the last one (no guessing ahead).
- **Gone without a despawn** (the link turns invalid, or the spawn heap or the player actor changes): the mod forgets
  the ghost without touching it and spawns it again 2 s later. If the ghost was live less than 10 s, the next wait is
  twice as long each time in a row (2, 4, 8, 16, 32, then 60 s; `quick_losses` counts them). So a ghost the game keeps
  removing is not spawned every 2 s. A ghost that stays 10 s, a new `ghost spawn`, or going `far` starts it over.
- **Distance**: each frame the mod works out how far from Link (measured flat, x and z) the ghost should be: the
  `ghost pos` target, the `follow` offset, or the spawn offset. Over 60 m it removes the ghost (or its pending
  request) itself and doesn't spawn it: state `far`. It keeps taking `ghost pos` targets. Once the latest one is within
  50 m again, it spawns it there at once. The gap between 60 and 50 m keeps a player near the limit from making it
  come and go. The game removes these actors at about 75 m (see [Distance](#distance)). A ghost left where it is
  (`ghost stop`) is not checked; if the game removes it, it comes back next to Link.
- **Failed requests** are tried again the same way, the first one too: a stage that is loading refuses requests or
  fails creations for a few seconds (two failed in the reload test). An unknown actor name is refused the same way,
  so it also takes the full row of tries before the mod stops. Only a created thing that is not an actor stops at once.
- **The crate as fallback** is for a default that never works: when the default (the Sheikah man, no name given) has
  not spawned yet in this game run and fails 5 times in a row, or creates something that is not an actor, the mod
  asks for the crate instead (`last` says so). Once the default has spawned, its failures are a loading stage and
  never switch to the crate. When the crate is lost (a stage change), the mod tries the default again first, since a
  slow first load can also cause the fallback. A name you give is never replaced.
- **At most 20 tries in a row** (about 40 s of failed requests; longer with the back-off above). Only a ghost that
  stays 10 s ends the row: one the game removes sooner counts as a try. Then `wanted` turns false and the mod waits
  for a new `ghost spawn` (the client sends one later). Going `far` is not a try.
- **Threads**: the commands run on Savage Wind's frame thread and only write requests under a small lock (a
  `ghost pos` also stamps its arrival time); all game calls happen in the tick, the animation calls too.

## What was tested

All in the real game (Savage Wind 0.4.1-alpha.4, BotW 1.9.0, vanilla `extracted\swgame.dll`, the test save on the
Great Plateau). Screenshots in `out\shots\` (never committed).

| Step | Result |
|---|---|
| Frame callback | 50-60 ticks a second in game (one per frame), about 2 a second on the title screen, on 3 different worker threads, never two at once (0 overlaps in every run). |
| `guest_call` | The player info block's getter `0x7000cc6fd0` called from the tick returned the same pointer as the block's +0x60, 37 of 37 checks. |
| `ghost spawn` | `Dm_Kibako_Contain_01` live 0.02 s after the request, 4 m in front of Link, visible (`ghost_step1_Dm_Kibako_Contain_01.png`); gone after `ghost despawn` (`..._despawned.png`). |
| `ghost pos` | Moved 3 m right with yaw 45, then 3 m left and 2 m up (`ghost_step2_b_pos_right.png`, `ghost_step2_c_pos_left_up.png`); the yaw read back as 0, 90 and -45 after `pos ... 0/90/-45`. |
| `ghost follow` | Stayed 3 m ahead of Link while he ran (`ghost_step2_d_follow_*.png`). |
| Reload | After a teleport 86 m up, the game reloaded at the save point (Link fell); on the reload the link turned invalid ("the game removed the ghost"), two requests failed while the stage loaded, the third spawned it again (`ghost_step2b_after_reload.png`). The player pointer stayed the same across the reload, so the link check is what caught it. |
| Multiplayer | `relay.py` + `client.py --no-ui --ghost` + `bot.py --radius 6 --speed 2`: the crate circled Link 6 m away, about 3 m between screenshots 1.3 s apart (`ghost_cli_0.png` to `ghost_cli_2.png`, and `ghost_step3_*.png` with the client run in-process). Stopping the client removed the ghost. |
| Other actor | `ghost spawn FldObj_Bokoblin_Bone` went live in 0.08 s and moved, but wasn't visible in the screenshot. |
| Title screen | Before the 0 0 0 check, `ghost spawn` on the title screen spawned the crate there and the game crashed at once (access violation in game code). With the check: the mod waited ("no world yet"), the crate went live 0.08 s after Continue with a single request, then `pos` and `follow` worked (`ghost_fix_pos.png`, `ghost_fix_follow.png`). |
| Order | `ghost spawn` and `ghost despawn` in one batch: idle, `wanted` false, no new request. |

Second round (smoothing, facing, a person as the stand-in), same save, about 01:00-01:35:

| Step | Result |
|---|---|
| Zelda, before the physics transform | `ghost` reported each new position, but the screenshots showed her where she was spawned (`ghost2_static_rel*.png`, `ghost2_probe_Dm_Npc_Zelda_Sibyl_moved.png`). With the mod not moving her (`ghost stop`), her matrix was back at the spawn point (0.3 m higher) within a second, every time; "set matrix" (`0x700152261c`) instead of "set position and rotation" changed nothing; `Dm_Npc_Goron_Hero` did the same. Her `+0x578` (physics) is set, the crate's is 0. |
| Zelda, with it | She stays where she is put and turns (`ghost2_probe8_phys_Dm_Npc_Zelda_Sibyl_moving.png`); after `ghost stop` her matrix stayed there (0.1 m lower: her body settles). |
| Multiplayer | `relay.py` + `client.py --no-ui --ghost` + `bot.py --radius 6 --speed 2`: Zelda live 66 s, 0 drops, 0 snaps, 0 overlaps, 2385 moves, `pulled_back_m` 0.002-0.08. Screenshots 0.55 s apart show her walking around Link, from behind him out to his left, side-on, facing the way she walks (`ghost2_circle_0.png` to `ghost2_circle_2.png`). |
| Facing | While she circled, her yaw was within 1.5° on average (3° at most) of her direction of travel, measured from her positions 0.2 s apart. |
| Smoothing | The positions the mod set (`ghost` reads them back right after the move), during the circling: 129 reads in 3 s gave 129 different positions, steps of 4.7 ± 1.5 cm (a read about every 23 ms at 41 frames a second), while the targets were 12.4 cm apart; with the physics transform (31-48 frames a second) 84 of 95 reads differed (the rest fell in the same frame), steps 7.3 ± 5.0 cm. `interval_s` about 0.06 (targets came about 16 a second), `behind_m` 0.01-0.25. The smoothing functions also passed a C test compiled outside the repo (halfway point, even steps per frame, hold, the short way round, 30 m jump, clamp after a pause). |
| Jump | `ghost pos` 40 m away: `snaps` 1, the actor there 0.1 s later. |
| Next to Link | 1.5 m in front of Link, facing him: no Talk prompt (`ghost2_near_link.png`). Moved onto Link's spot (0.3 m off) for 3 s: Link was pushed 0.47 m (`ghost2_on_link.png`). |
| Frame rate | Frames a second over 10 s with relay, client and bot running: no ghost 52, crate 53, Zelda 44-45. Without the client: no ghost 50, Zelda (standing) 41. |
| Long run | No crash or hang with the physics transform in 4 runs (the longest 66 s of moves). |

Third round (review fixes), about 01:55:

| Step | Result |
|---|---|
| Teleport through the client | Before the fix the jitter buffer blended a 40 m teleport into pieces (24 + 16 m, 4 + 36 m...): in 47 of 50 clock phases one piece was 30 m or less (the mod glided it) and in 49 of 50 the ghost ended up facing the teleport. Now (unit test over 25 phases, teleports of 5 to 1000 m) it is one step and the facing stays the walking direction. In the game, the client's own `Session` and `Ghost` at 20 Hz, a peer walking +x then teleporting 40 m along +z: one 40 m step, `snaps` 1, Zelda at the target 0.5 s later, yaw 90 (the walking direction). |
| `behind_m` | After a `ghost pos` target then `ghost follow 0 0 4`: 0 (it showed the distance to the old target before). |
| Fallback | Checked with ghost.c compiled outside the repo (no game): after Zelda spawned once, 6 refusals in a row keep Zelda; before she ever spawned, 5 refusals switch to the crate and the next loss goes back to Zelda. |

Fourth round (animation, ghost.c 0.3.0), about 04:00-05:30. `ghost` read back what the actor plays (`as`) the whole
time. Screenshot strips (the ghost cut out of the frames, side by side) are `*_strip.png`.

| Step | Result |
|---|---|
| Holder | Sheikah man with `ghost anim off`: holder found and its own (`as_ok` true), `as` empty, physics true, T-pose (`anim_t1_sheikah_probe.png`). |
| `ghost anim Wait` | `as` Wait at once; arms down, standing (`anim_t2_wait_0.png`, `anim_t2_wait_side_0.png`). |
| `ghost anim Walk`, not moved | `as` Walk; legs and arms in a different walking pose in each of 3 shots 0.3 s apart (`anim_t3_walk_strip.png`: first two frames Wait, then Walk). He walks in place: no drift over 5 s, so the clip doesn't move him. |
| `ghost anim Nope` | `as` stays Walk, no crash; after 3 more tries `last` says the actor doesn't play it. `ghost anim bad-name` is refused. |
| Auto, `ghost pos` circle, Sheikah | 15 s standing, 2 min at 2 m/s, 2 min at 6 m/s, 1 min standing: `as` Wait, then Walk, then Run, then Wait. 5 play calls in 5 minutes (one per change; Run to Wait passes Walk for 0.25 s while the speed falls). Measured speed 1.98 and 5.89 m/s on average. Clear poses: `anim_s2_walk_strip.png`, `anim_s2_run_strip.png` (a long stride, arms bent, a different leg forward in each frame), `anim_s2_wait_strip.png`. |
| Auto, same circle, Zelda | 2 min walk, 2 min run, 1 min standing: the same changes, 4 calls, `as_reasserts` 0 (her AI never changed her animation in 5 minutes). Walk and run show in her arms and dress (`anim_z_walk_strip.png`, `anim_z_run_strip.png`); her legs are hidden by the dress. |
| `ghost follow` | (First build, speed from each frame's step.) Link walking (stick at 45 %): Walk at 1.5 m/s; running: Run at 5 m/s (`anim_t5_walk_*.png`, `anim_t5_run_*.png`). Moving Link was not tried again: the run in this test ended off a cliff. |
| Reload | Link ran off a cliff and died. The game removed the ghost; after Continue the new ghost (new actor and holder) got Wait again, with one call. |
| Multiplayer | `relay.py` + `client.py --no-ui --ghost` (default actor) + `bot.py --replay` of a path that stands, walks at 2 m/s, runs at 6, walks at 2.5, stands, runs at 5 (5.5 minutes): `as` followed each phase, 10 calls in 5.5 minutes, 0 drops, 0 snaps. Screenshots `anim_net2_*.png`, strips `anim_net2_walk_strip.png`, `anim_net2_run_strip.png`. Before the late-target fix, screenshot hitches (the frame stops for about 0.3 s) made it switch Run, Walk, Run, Walk, Run within a second (and the first build, which measured each frame's step, showed Run for a moment at 2 m/s); with the fix, no extra switch. The animation choice also passed a C test compiled outside the repo (steady walk and run, 0.2-0.5 s hitches with a client that waits for answers, 8 to 45 frames a second, stop, jump, thresholds and the 0.25 s hold). |
| Frame rate | `frames 20` in the same spot, 25 s each: no ghost 51-53, Sheikah standing 53.9, Sheikah walking 53.2, Zelda standing 49.9, Zelda walking 46.4. During the 5-minute runs about 47-50 on average (dips to 6-15 during screenshots). |
| Long run | 4 game starts, about 45 minutes in all, no crash or hang, 0 overlaps; each closed with `exit`. Only about 50 play calls in all (one per change), so the fifth round stress-tested the call. |

Fifth round (review fixes: play-call stress test, jump check), 2026-11-07, one game start of 44 minutes, 17,841
play calls in all, no crash or hang, 0 overlaps, closed with `exit`. Screenshots `out\shots\fix_*.png`.

| Step | Result |
|---|---|
| Stress, Sheikah man | `ghost anim Walk` and `ghost anim Run` in turn, 10 a second, 10 minutes, standing near Link: 5,922 play calls (one per command). Frame rate median 54 (lowest 36, during screenshots); the frame callback was running at every check (every 2 s); 0 drops. Walking pose in `fix_stress_sheikah_1.png`. |
| Stress, Zelda | The same with `Dm_Npc_Zelda_Sibyl`: 5,922 play calls, frame rate median 53.5, 0 drops (`fix_stress_zelda_*.png`). |
| Stress, following a moving Link | The same with the Sheikah man on `ghost follow 0 0 3`, while Link ran and walked back and forth with the stick (back to the start once a minute): 5,923 play calls, frame rate median 52, 0 drops (`fix_stress_follow_1.png`: running beside Link). |
| Jump check | The fix in `anim_sample`: a step over 30 m, a step after more than 0.3 s without a position, or one faster than 40 m/s over at least the usual time between positions is a jump. Checked with ghost.c's own functions compiled outside the repo: a single 3-6 m step of a standing player, and a step of 3-60 m after a 1-5 s pause, now give no Walk or Run (before: Walk, or Walk and Run, for 0.5-0.8 s, 2-4 extra calls). A step under 40 m/s still counts (1.5 m in one 50 ms target: Walk and Run for 0.5 s). Steady speeds and frame hitches give the same changes as before. In the game: the ghost following Link through a 215 m teleport and back kept `Wait` with no play call. |
| Despawn, respawn | `ghost despawn`: idle, gone (`fix_despawned.png`). `ghost spawn`: live after 0.19 s, `Wait` with one call, arms down (`fix_respawned.png`). |
| Cycles | `ghost pos` on a 4 m circle, 2 minutes 10 s: stand, walk at 2 m/s, run at 6, stand, walk, run, stand. `as` went Wait, Walk, Run, Wait, Walk, Run, Wait: 8 calls for 6 changes (Run to Wait passes Walk for 0.25 s), no extra switch. Measured 1.9-2.0 and 5.8-5.9 m/s. Clear walk and run poses (`fix_cyc_p1v2_*.png`, `fix_cyc_p2v6_*.png`). |
| Teleport, ghost left standing | The ghost at a `ghost pos` spot, Link teleported 215 m away into the forest below the plateau (-1227 187 1724). The game removed the ghost within a second; the mod spawned it at its spot again every 2 s and the game removed it each time (the distance limit below). Link teleported back: the ghost spawned and stayed, `Wait` (`fix_tp2_back_later.png`). |
| Teleport, ghost following | With `ghost follow`, the ghost went along to the forest and back, stayed live, kept `Wait`, no play call (`fix_tp4_follow_far_look.png`, `fix_tp5_follow_back_look.png`). |
| Teleport off the plateau | To -400 200 1300: the game reloaded and put Link back on the plateau. The ghost was removed and came back with `Wait`. |
| Follow, auto | Link walked and ran with the stick: `Walk` at 0.5-2.2 m/s, `Run` at 3.5-6.2 m/s (Walk for a moment while he turned round), `Wait` after he stopped (`fix_follow_walk_out.png`, `fix_follow_run_out.png`). Then Link ran off the cliff east of the start and died, as in the fourth round; after Continue the ghost came back with `Wait` (`fix_after_continue.png`). |

Sixth round (distance rule and back-off, ghost.c 0.3.1), 2026-11-07, about 06:10-06:30, two game starts, both
closed with `exit`, 0 overlaps. Link stood still at the start of the plateau (-1030 252 1806); the ghost was put at
a distance with `ghost pos`. Screenshots `out\shots\far_*.png`.

| Step | Result |
|---|---|
| Where the game removes it (0.3.0) | Kept 6 s or more at 20, 40, 50, 60, 70, 72, 73 and 74 m. Removed within 0.5-0.8 s at 76, 77, 78, 80, 120, 160 and 200 m (each time, and again after each respawn). The same along +x, -x and -z, so it is the distance from Link, not from the camera. Zelda: kept at 73 m, removed at 77 m. Measured flat: 80 m and 300 m straight up were kept, and so was 80 m up and out (48 m flat). The crate was kept at 77 m (its limit is farther). |
| Thresholds | Far over 60 m, near again under 50 m: 15 m below the 75 m limit, so a target can move some way between two checks. |
| Near | `ghost spawn` 3 m from Link: live after 0.5 s, `Wait`, 1 play call (`far_near_wait.png`). A 2 m/s circle: `Walk` at 2.0 m/s, `Wait` again after it stopped (`far_walk_front_*.png`). |
| Far, 215 m | `ghost pos` 215 m away: `far` within 0.02 s, the mod removed the ghost itself. Over 30 s with a new target every 0.5 s: 0 spawns, 0 requests, 0 drops; `target` kept the latest one (`far_215_far.png`). Before (fifth round): about 15 spawns in 30 s. |
| Back near | `ghost pos` 3 m away: one spawn, live after 0.24 s, `Wait` with 1 play call; then 10 s with no more spawns or drops (`far_back_near_wait.png`). |
| Edge | The target moved out from 40 to 70 m and back at 5 m/s: `far` at 60.1 m, spawned again at 50 m (1 spawn, 0 drops: the game never had to remove it). |
| Back-off | `ghost far 0` (rule off), target 80 m away for 30 s: 3 spawns and 4 removals by the game; waits of 2, 4, 8, then 16 s (`quick_losses` 4). `ghost far 60 50` again: `far` at once. |
| Teleport | The fifth round's case: the ghost 3 m from Link, Link teleported 216 m away (-1227 187 1724): `far` at once, then 30 s with 0 spawns, 0 requests, 0 drops (`far_tp_far.png`). Teleported back: one spawn after 0.02 s, `Wait`, 1 play call, then 10 s with no more (`far_tp_back.png`). |
| Follow | `ghost follow 0 0 3` through the same teleport and back: stayed live, 0 spawns, 0 drops (as before). |
| Pure functions | `far_next` and `backoff_delay` compiled outside the repo: out to 80 m and back in 1 m steps, far at 61 m and near at 49 m; hovering between 51 and 60 m never changes it; waits 2, 4, 8, 16, 32, 60, 60 s. |

Seventh round (two PCs, then the speed fix, ghost.c 0.4.0), 2026-11-07:

| Step | Result |
|---|---|
| Two PCs over home Wi-Fi (0.3.1) | Two laptops, each with its own game, one hosting the relay. The ghost's position stayed in sync with little delay. But while the other player ran, the ghost mostly showed the standing pose. Attacks and held items are not shown at all (nothing sends them). |
| The fix | Each client measures its own speed and sends it (`speed` in the state); the receiving client passes it with `ghost pos ... speed`; the mod uses it for `Wait`/`Walk`/`Run`. Without it, the mod's own measurement now uses 0.5 s of shown positions and keeps late positions (see [Animation](#animation)). |
| Simulated Wi-Fi (Python) | `tests/test_mp_wifi.py`: sender, relay cleaning, receiver and `ghost pos` on a made-up clock, with 0-150 ms random delays and 0.3-0.6 s stalls every 2-5 s (and, in one test, power-saving bursts every 0.1-0.3 s). Stand, walk at 2 m/s, run at 6.5, walk, stand, run, stand (50 s): every `ghost pos` after the first 1.5 s of each phase carries a speed within 0.25 m/s that picks the right animation (5 seeds, an older sender, bursts, a clean network). The real relay and bot on loopback with random delays and one 0.5 s stall: the speed came through the relay, and every `ghost pos` picked `Run` at 6 m/s. |
| Simulated Wi-Fi (C) | ghost.c's own functions compiled outside the repo, fed with the same simulated `ghost pos` streams at 55 frames a second, 10 seeds of 50 s each. With the sent speed: the right animation in 100% of frames in every phase, 9 changes per run (one per change of speed, plus `Walk` for a moment from `Run` to `Wait`). Without it (the mod's own measurement): also 100%, 8-9 changes. The 0.3.1 code on the same streams: `Run` in 88-91% of running frames, 26-38 changes (flicker). With harsher power-saving bursts (0.2-0.4 s): sent speed 100%, own measurement at least 98% `Run` while running, 0.3.1 `Run` in 59% of running frames on average (51% at worst) and 63-85 changes. Frame hitches (0.2-0.5 s every 3-6 s) on top: sent speed 100%, own measurement at least 99.5% in every phase. A stop shows 0.1-0.4 s late with the sent speed, about 1 s late with the mod's own measurement (0.3-0.6 s before). A standing player's 3-6 m step, and a 3-25 m step after 1-5 s without `ghost pos`: no `Walk` or `Run`, as before. |
| In the game | Not yet re-tested. |

Apart from the title-screen crash above, the game never crashed or hung in these runs (10 game starts in the first
round, 13 in the second, 1 in the third, 4 in the fourth, 1 in the fifth, 2 in the sixth, each closed with `exit`).

Eighth round (0.5.0: animation sync, `ghost self`, Link body), 2026-11-07, without the game:

| Step | Result |
|---|---|
| C check | ghost.c 0.5.0 compiled outside the repo with a fake game: 69 of 69 checks pass. Link body: every sent name shows within 0.4 s, `Move` goes by speed, `CutS4` (not in its list) shows `CutS1`. Sheikah man: only `Wait`/`Walk`/`Run`, 11 names it lacks noted after one call each. No names sent: exactly the old speed rule. A name that flips every 50 ms: 12 changes in 3 s, at least 0.25 s apart. `ghost self` on fake memory at the real guest addresses: names, the 0.3 s attack hold, combos, bow, items; bad, uncommitted, misaligned and foreign pointers give empty or `?`, never a crash. |
| Speed regression | The 0.4.0 Wi-Fi scenarios (3 seeds) through 0.5.0: the same as 0.4.0 (right animation in 100 % of frames, 9 changes per run). |
| Python | `tests\test_mp_sync.py`: protocol fields, the client's sync requests and fallbacks for older mods, the bot's `--anim`, the pack builder on made-up packs, AAMP. |
| Link body | Built from the user's own files: 39 files, 18,688 bytes unpacked (allowed: 348,448). Our one-clip animation set has byte for byte the layout of the game's simplest one; the blend table's key names match the game's. |
| In the game | Not run (the laptop was in use). |

Ninth round (review fixes, ghost.c 0.5.1), 2026-11-07, without the game:

| Step | Result |
|---|---|
| Name table | `tests\test_ghost_native.py` builds ghost.c with a made-up actor (`native\examples\ghost\selftest`). After 130 names the Link body lacks, the glide and `CutS3` still play (0.5.0: `Wait`, the table was full at 128); 600 names from the network stop at 480 and `ghost anim Turn` still works. |
| Missing names | The Sheikah man, 80 names he lacks, 3 rounds: 160 play calls in all, 2 per name (0.5.0: 240, each name asked again every round, as only 64 were kept). |
| Late names | A made-up game that shows a new name 0.15 s late: `CutS3` plays and is not noted missing (0.5.0: noted at once, `CutS1` too). A name the body lacks is noted after 2 calls. |
| Sender hitch | The sender's game stalls 0.2-0.5 s every 3-6 s with its clock stopped, Link running at 6.5 m/s (`tests\test_mp_wifi.py`, and the mod's own code on the same stream): the sent speed stays at 6.49 m/s, `Run` in 100 % of running frames, no extra switch (0.5.0: down to 0.24 m/s, `Run` in 94-96 %, 6-10 extra switches in 28.5 s). A stop right at a hitch still shows within 1 s. |
| Attacks in a stall | 0.3 s attacks every 1.2-2.2 s over the Wi-Fi model, 5 runs of 2 minutes: 344 of 344 reach the mod, with stalls and with power-saving bursts (0.5.0: 326 and 319). Only the hidden ones are added (0.3 s each); with jitter alone the shown names are exactly as before. |
| In-game check | L2 now also plays `CutS1` (the Sheikah man has none), so it fails when the Link body didn't load. |

Tenth round (first in-game run of 0.5.1: `ghost self`, sync, Link body), 2026-11-07, test save on the Great Plateau,
3 game starts, each closed with `exit`, no crash or hang. The game was capped at 30 frames a second (its setting).
Screenshots `out\shots\ghost2_*.png` (strips: `*_strip.png`) and `out\shots\ghostcheck\`.

| Step | Result |
|---|---|
| First check run | 9 PASS, 5 FAIL, 1 SKIP. `ghost self` passed. The Link body spawned (live, `as_ok` true) but played nothing: `as` stayed empty for `Wait`, `Walk`, `CutS1`...; only `ClimbMove` played. S4 failed too: the save loads Link by a rock face, so he climbed it. |
| Cause | Every animation list of the game's (the Sheikah man's, the pause-menu Link's, the player's 399 names) is sorted by name, and ours was not. A binary search over our unsorted list of 33 lands on `BowCharge`, then `ClimbMove`: exactly the one name that played. Fix: `ghost_link_pack.py` sorts the sets and the blend list by name. |
| Second check run (sorted pack, `--spot` on open ground) | **14 PASS, 0 FAIL, 1 SKIP** (S6). Below. |
| `ghost self` (S1-S5, S7) | Link found, slots `2,2,1,3,1`. Standing `Wait` (`DemoWait` for a moment right after a load). Walk, run, sprint: all `Move`, at 1.6, 5.5 and 7.9 m/s. X: `Jump`, `Land`. On a rock face: `ClimbMove`, `ClimbWait`, `WallDash`, `WallDashSt`, `WallOff`. After a fall: `Fall`, then `DeadRagdoll`. Weapon, shield, bow `""` with `equip_ok` true: the test save's inventory is empty (seen in the menu), so S6 (attack) is SKIP and the item names are not checked. |
| Cost of `ghost self` (S8) | 28.7 -> 26.6 frames a second while asked 20 times a second (first run: 30.0 -> 30.0). Within the 3 limit; at the 30 cap a small cost hardly shows. |
| Link body (L2-L4, by hand) | Every name played at once (`as` = the name): `Wait`, `Walk`, `Run`, `RunGear2`, `CutS1`, `CutSF`, `CutS1_Lsword`, `CutS1_Spear`, `Jump`, `Fall`, `ParashawlGlide`, `ClimbMove`, `SwimMove`, `SquatWait`, `BowCharge`, `GuardJust`, `DamageS`, `WeaponEquipOn`: 18 of 18, 20 play calls, no re-asserts. Looks like Link: his face and body, bald, in underwear, arms down standing (`ghost2_link_wait.png`). |
| Next to Link (`ghost follow`, anim auto) | Link walked: the ghost `Walk` at 1.1-1.8 m/s, a different leg forward in each shot (`ghost2_walk_strip.png`); ran: `Run` at 3.5-6 m/s, long strides beside Link (`ghost2_run_strip.png`); sprinted: `Run` at 5-7.5 m/s (`ghost2_sprint_strip.png`). |
| Attack replayed | `ghost anim CutS1` shot 0.05, 0.25, 0.45 and 0.65 s after: wind-up, a lunging swing across the body, follow-through, back (`ghost2_atk_CutS1_strip.png`; no sword: held items are not drawn). Other poses in `ghost2_poses_strip.png` (spin, two-hander, jump, glide, climb, swim, bow, squat). |
| Bot replay (R1, R2) | The bot's `CutS1`, `CutS2`, `CutDash`, `Jump`, `ParashawlGlide`, `ClimbMove` all showed on the Link body as sent; the client sent its own `Wait` and `Move`. |
| Cost of the Link body (L5) | 29.2 without, 29.7 with (walking in place: 30.0 and 30.0). None measurable at the cap. |
| Sheikah man (no pack) | Not re-run in this round (another game ran on this PC for the next 30 minutes; done in the eleventh round). Last seen in the fourth and fifth rounds (`anim_s2_*_strip.png`, `fix_respawned.png`). The fallback for missing names did work in the first run (the unsorted body lacked them): each was noted once ("the actor has no CutS1 animation; showing another one for it") and the speed rule took over. |
| Cliff | A 3 s sprint from the plateau spot toward yaw 132 ran off the edge (about 18 m): `Fall`, `DeadRagdoll`, Game Over; Continue reloaded and the ghost came back. So `--spot` starts 5 m farther back, and S4 runs 2.4 s. |

Eleventh round (what the tenth left open: an attack, item names, the Sheikah man with 0.5.1), 2026-11-07, about
14:35-14:50, same test save and spot, ghost.dll 0.5.1 rebuilt (comments only), 3 game starts, each closed with
`exit`, no crash or hang. Another program's game ran on this PC from 13:42 to 14:35: the check waited. Results:
`out\research\ghost-ingame-results-run11{a,b,c}.txt`, screenshots `out\shots\ghostcheck\ghostcheck_S6_*.png`.

| Step | Result |
|---|---|
| **Last run** (`--link --spot ... --give-weapon`) | **16 PASS, 0 FAIL, 0 SKIP.** S1-S5, S7, L1-L5, R1, R2 as in the tenth round (walk 1.6, run 5.4, sprint 8.1 m/s), plus S6 and S9 below. |
| A sword for the test save (`--give-weapon`) | The save has no weapon, so the check now has the ghost mod spawn `Weapon_Sword_001` 1.6 m in front of Link (no animation, not moved), walks him up to it and presses A. It lies in the grass with a "Take" prompt (`ghostcheck_S6_weapon_on_ground.png`); A takes it ("Traveler's Sword" window, `ghostcheck_S6_taken.png`). It worked in all 3 runs. |
| Item name (S9) | Right after: `weapon` `Weapon_Sword_001`, `weapon_class` true; shield and bow `""` (none). 3 of 3 runs. |
| Attack (S6) | The first weapon opens a "Use Weapon" window that eats the first Y. After A, then B, the third Y swung: `CutS1`, then `CutChargeWait`, `attack` true, attacks 0 -> 2 (`ghostcheck_S6_swing3.png`: the sword out to his right). Runs 2 and 3; in run 1 the check pressed Y only once, so S6 failed there (fixed: up to 4 tries, A or B between). |
| The taken sword is the spawned actor | After the take the mod still had a valid link to the same actor (same address): the game keeps the world sword as Link's weapon. So the check never sends `ghost despawn` after it (that would remove Link's sword) and just closes the game. The client only spawns people; don't `ghost spawn` an item by hand and then take it. |
| Sheikah man, 0.5.1 (no pack, run 2) | R1 PASS: the bot's 8 names reached the mod; the ghost showed only `Wait`/`Walk`/`Run`; `CutS1`, `CutS2`, `CutDash`, `Jump`, `ParashawlGlide`, `ClimbMove` were each noted once as missing ("the actor has no CutS2 animation; showing another one for it"). |
| Cost of `ghost self` (S8) | Run 2 read 30.0 -> 25.2 with one 5 s pair (FAIL), run 1 29.9 -> 28.9. The PC was busy (another tool's search used much of the processor; 78 % load with no game running), so S8 now measures two pairs in turn and averages them: run 3 without 30.0 and 30.0, with 30.0 and 29.9. No cost shows at the 30 cap. |
| Cost of the Link body (L5) | 30.0 -> 29.9 and 29.9 -> 29.9. |
| Save | The game autosaved into the test save's slots 2 and 3 during the runs, both before the sword was taken (the next run started without it). A copy from before is in `out\test-save-backup-1425`. |

Twelfth round (held items, ghost.c 0.6.0), 2026-11-07, about 16:00-17:50, same test save and spot, the Link body,
`ghost_ingame_check.py --items --give-weapon --lib out\ghost-items-lib` (a scratch library). 4 game starts, each closed
with `exit` (code 0), no crash or hang, no bad line in the log. Another program's game ran on this PC in between; the
check waited for it (it now waits by itself: `--wait-max`). Run 1 never loaded the mod (a relative `--lib` path;
fixed); run 2: 13 PASS, 5 FAIL; run 3: 17 PASS, 1 FAIL; run 4: 15 PASS, 3 FAIL (all from the check, below).
Results: `out\research\ghost-items-results.txt` (combined) and `-run{2,3,4}.txt`, logs
`out\research\ghost-items-run{1,2,3,4}.log`, screenshots `out\shots\ghostitems-run4\`,
`out\shots\ghostitems-run3\` (the best close-ups) and `ghostitems-run2\`. **Which build:** ghost.c changed at 17:33,
after run 3 (two link checks went from the game's call to plain reads, and tick timers were added); only run 4 used
that build, and in run 4 H9, H10 and H12 failed and H11 was measured on the Game Over screen. So H10-H12 and the cost
below are from the build before (runs 2-3). A review (thirteenth round) found the rows marked below wrong or too
strong; they are corrected here.

| Step | Result |
|---|---|
| In the hands (H1) | Sword, shield and bow held 0.39 s after `ghost items`, item state 255, holder the ghost; 0.000 m from the hand bones in all 30 samples (standing, walking a circle at 2 m/s, swinging `CutS1`; no running, jump, glide or climb). The pictures show the wooden shield on the ghost's left arm and the sword in its right hand (`ghostitems-run3\ghostitems_H1_stand.png`). In the walking and swinging shots the items can't be told apart (far, behind Link, or the blade beside Link's own hand), and the bow, next to the shield, can't be seen in any H1 or H2 shot; the clearest bow in the hand is `ghostitems-run3\ghostitems_H10_160.png`. |
| Switch, clear (H2, H3) | Sword -> two-hander -> spear -> sword: each held 0.27-0.39 s after the change, then 10 switches 0.3 s apart: the right sword at the end, nothing left over (`stray` 0). Clearing the shield kept the same sword and bow actors; gone in 0.04 s. |
| Back (H4) | All three on `Pod_A` at their own offsets' length (off by 0.000 m), in the hands again on mask 0; 10 toggles, no stray. **This check grades itself:** any offset passes, so it only shows the mod's follow is on. Next to the real Link the ghost's back looks different: the item over its right shoulder rises about 3 head-heights above it, over Link's about 1, and Link carries a second item across his waist (`ghostitems-run3\ghostitems_H4_back.png` against `ghostitems_H13_weapon_back.png`). Which item is over the ghost's shoulder can't be told. |
| Can't take (H5) | Link 0.4 m from the ghost, A five times, unarmed and later armed: no prompt, his items unchanged, the ghost's the same actors, still held. |
| Network (H10) | Relay, `bot.py --items` (3 steps), the client with `--ghost-items` (runs 2-3, the earlier build): the ghost followed each step in every cycle (the client log, and `ghostitems-run3\ghostitems_H10_160.png`: the two-hander on the back, the bow in the left hand). The "0.0-0.3 s" read then was not a delay: it timed only the first cycle, from the first sample that already showed the names; the time from the bot's change was not measured (the check now does). |
| Give, sender (H12, H13) | `ghost give` put a sword, a shield and a bow in front of Link; he took each (A); `ghost self` named all three (this also closes the gap of the eleventh round: shield and bow names read right). Out (Y, ZL, ZR) each read "not on the back", after a sprint "on the back". |
| Runs 3 and 4 | H1-H8 passed again in both (held 0.36-0.38 s after the request, switches 0.29-0.43 s), with the ghost now beside Link, close to the camera: the close-ups show the wooden shield on its left arm and the sword, two-hander or spear in its right hand (`ghostitems-run3\ghostitems_H1_stand.png`, `ghostitems_H2_Weapon_Spear_001.png`), all three on its back (`ghostitems_H4_back.png`) and empty hands after clearing (`ghostitems_H3_cleared.png`; tall grass hides the ground, so "nothing on the ground" rests on `stray` 0). Run 3 (the earlier build): H10, H12, H13 and both H5 passed (the armed H5 with all three); the cost 29.8 -> 29.2 frames a second, but see the cost row. Run 4 had no sword (the first A went to Game Over's Continue, so H12 gave only the shield and the bow; H13's weapon skipped, the armed H5 with shield and bow only). In H13 the "Attack with Bow" window covers Link in the `weapon_out` shots of runs 2-4. |
| Reload (H9): not verified | Run 2's teleport to -400 200 1300 put Link on a cliff (no reload; its `H9_after_reload` shot shows him on a cliff); run 3's straight-up teleport was refused (Savage Wind's `teleport` wants a target at least 0.05 m away on the ground); in run 4 Link fell 90 m and died, the ghost was dropped 8.9 s after the teleport (no crash), but the check stopped pressing A then, so the game sat on the Game Over screen, where it creates no actors (the shot is the Game Over screen): the ghost's request timed out twice, and H10, the first H12 item and run 4's H11 (measured with no ghost) failed with it. Its `stray` 0 meant nothing: 0.6.0 wiped the watch list on a stage change (0.6.1 keeps watching). The reload still needs a run. |
| Run 2 fails, fixed | H6-H8: the items were deleted (`items.deleted` went up, `stray` 0), but the slot status still said "held" once the ghost was gone: fixed (the status is cleared with them). H9: a teleport to -400 200 1300 put Link on a cliff, no reload: now Link falls from 90 m, Game Over, Continue. H11: 27.3 -> 26.5 frames a second (inside the budget), but `items.us` read 3,895 microseconds, an average: the check then switched to the median of 64 frames and passed. That was wrong: the plan's rule is the average, and it failed in runs 2 and 3 (4,871 microseconds, most 50,508). The 50-70 ms item frames are not explained, and a 50 ms item frame is a 50 ms tick too (the items run inside the timed tick). The thirteenth round restores the average and times what the frames do. |
| Cost (H11): not verified | Fps pairs at the 30 cap differ by as much as the drop (run 2's two "alone" readings 27.7 and 26.8). Valid runs: run 2 27.3 -> 26.5, run 3 29.8 -> 29.2; the average items part failed the plan's rule in both. Run 4 measured on Game Over. |

Thirteenth round (held items 0.6.1: a review's fixes), 2026-11-07, 18:10-20:40. **Not run in the game:** another
program's game (process 8652) ran on this PC from before 18:37 to after 20:38; the check waited the full 2 hours,
asking every 30 s, and gave up without starting ours (`out\research\ghost-items-run5.log`). Done without the game:

| Fix | Checked |
|---|---|
| A new item waits 6 m over the ghost (out of Link's reach) until the grab takes it, moved back there each frame | C selftest `parked` (spawned and set at 6 m; the grab refused once first, `refusals` 1) |
| An item someone else holds (Link took it before the ghost's grab) is never deleted, only forgotten | C selftest `taken_in_gap` (no delete, `items.taken` 1, asked for again later) |
| Only weapons, shields and bows, each for its hand, none of the special ones (mod and client) | C selftest `names`; `tests\test_mp_items.py` (both rules the same) |
| A missing item key in the other player's data changes nothing (only "" clears) | `tests\test_mp_items.py` |
| An item gets its own bone follow back before it is deleted; one whose follow the game won't change is kept | C selftest `own_follow_first`, `owns_follow_kept` |
| A stage change keeps watching the items (a stray shows); a pending request is released if still ours | C selftest `stage_change`, `stage_change_stray`, `orphan_released` |
| `ghost give` names go in the item table (256), not the ghost's (32) | code |
| Slow game calls, slow item parts (with the thread's cycles) and frames are timed and reported | code; the check reports them (H2, H11) |
| The check: every change of every cycle timed from the bot's own time (H10, limit 1 s, the plan's), an older sender, the plan's average for the cost, the old item gone within 1 s of each switch, running and jump/glide/climb poses, the comparison with Link's back (H4L), a real Link as the sender through two clients (H15), more shots (H1 run, bow alone, H3 shield cleared, H8, H11), the tutorial window closed before H13 | `tests\test_mp_items.py` (the judges); run in the fourteenth round |

Fourteenth round (held items in the game: 0.6.1, then 0.6.2), 2026-11-12, 07:56-09:00, the test save (backed up first,
restored after), the scratch library `out\ghost-items-lib`, the spot above, `ghost_ingame_check.py --items --link
--spot -1033.6 252.3 1809.3 132 --give-weapon --lib out\ghost-items-lib`. Five game starts, one at a time, each
closed with `exit` (code 0), no crash, no bad log line. Run 1 (0.6.1): 17 PASS, 4 FAIL; run 2 (0.6.2 without the
update wrapper): 19 PASS, 3 FAIL; run 3 (0.6.2): **20 PASS, 2 FAIL**. Someone watching run 1 saw the items far bigger
than normal and the bow on the sword's side; both came from the mod or the check, and are fixed. Results
`out\research\ghost-items-results-r14-run{1,2,3,4,5}.txt`, logs `ghost-items-r14-run{1..5}.log` (runs 4 and 5 also
free RAM and CPU every 10 s: `-run4-ram.log`, `-run5-ram.log`), screenshots `out\shots\ghostitems-r14-run{1..5}\`.
Runs 4 and 5 (08:57-09:13, the laptop left to the check): 0.6.2 with the back offsets copied (`ghost items rel`, then
over the network) and H11 as 3 pairs in turn; both **22 PASS, 1 FAIL** (H4P, the record below).

| Check | Run 3 (0.6.2) | Before |
|---|---|---|
| H1 in hand | PASS: held 0.41 s after the request; sword in the right hand, shield on the left arm, bow on the back; 0.000 m from the hand bones in 54 samples (standing, walking, running, jump/glide/climb, swinging); the bow drawn: in the left hand, sword and shield on the back (`ghostitems_H1_stand.png`, `H1_bow_drawn.png`) | Runs 1-2 PASS. Run 1's check put all three in the hands (mask 0): the bow, held flat in the left hand and 1.4 times its size, reached across to the sword's side. The check now uses Link's own layout (MASK 4; MASK 3 with the bow drawn) |
| H16 size (new) | PASS: 177 samples at size 1.00; Link's own items 1.0 (`H15_new_sword.png`: the ghost's shield the size of Link's) | Run 2 FAIL: 1.4-1.5 (the weapon's own update gives an NPC holder's scale; a `SET_TRANSFORM` from the frame job didn't stick) |
| H2 switch, H3 clear | PASS: 0.14-0.30 s, old item gone 0.18-0.33 s after each switch, stray 0; clear 0.02-0.03 s | PASS in all runs |
| H4 back | PASS: all three on `Pod_A` at their offsets (0.000 m off), back to the usual layout, 10 toggles, stray 0 | PASS in all runs |
| H4L like Link's back | FAIL: weapon 0.000 m, shield 0.010 m, **bow 0.108 m and 4 degrees** off Link's (Link's minus the ghost's in `Pod_A`'s frame: -0.100, +0.010, -0.040 m) | the same in all runs: open |
| H5 can't take | PASS unarmed and armed (0.51-0.59 m, A five times; nothing moved over) | PASS in all runs |
| H6-H8 | PASS: despawn, the distance rule (back in 0.27 s), the game removing it (back in 0.3 s); stray 0 | PASS in all runs |
| H9 reload | PASS: the ghost and its items back 15.3 s after the fall, stray 0, one ghost in the picture | Run 1 FAIL: a second ghost with two items next to the new one (`ghostitems-r14-run1\ghostitems_H9_after_reload.png`); fixed (leftovers, above); run 2 PASS |
| H10 network | PASS: every change within 0.52 s (newer sender) and 0.63 s (older sender) | Run 2: the older sender's clear once took 1.06 s (limit 1) |
| H11 cost | FAIL: 30.0 / 29.0 alone against 25.4 / 28.7 with the items (mean drop 2.5, budget 1); items part 12.2 microseconds a frame (limit 50), the whole tick at most 0.33 ms; 48 frames over 50 ms while measuring with the items | Run 1 FAIL: 3,529 microseconds (the memory check: fixed); run 2 PASS: 30.0 -> 29.1, 13.0 microseconds |
| H12, H13 | PASS: Link took the sword, shield and bow (`ghost give`); out / after a sprint read right for all three | PASS in all runs |
| H15 real sender | PASS: sprint 0.09 s, draw 0.10 s, sprint again 0.08 s, throw 0.45 s, a new sword 0.10 s | Run 1 FAIL: two sprints in a row ran Link off the plateau (Game Over); the check now goes back to the spot before each sprint |
| H14 log | PASS: no bad line, exit code 0 | PASS in all runs |
| H4L, runs 4-5 | PASS: Link's own back offsets copied: weapon, shield, bow 0.000 m and 0 degrees off his (`ghostitems-r14-run4\ghostitems_H4L_like_link.png`); through the network too (run 5's H15: 0.07 s after his sprint, `ghostitems-r14-run5\ghostitems_H15_back_like_link.png`) | H4P (the items' own offsets, kept as a record): the bow 0.108 m, 4 degrees off in every run |
| H11, runs 4-5 | PASS: 3 pairs in turn, medians 30.0 -> 30.0 in both runs (every pair 30.0/30.0, the 30 fps cap); items 9.5 microseconds a frame; no frame over 50 ms in run 4. Free RAM 7.5-8.2 GB before the game, 1.9-2.8 GB while it ran | Run 3's drop came while the user worked on the laptop |
| H5 armed, run 5 | PASS at 0.49 m (run 4's "PASS" was 20 m away, after H4L's sprint: not valid; the check now walks Link back first and fails beyond 1.2 m) | runs 1-3 PASS at 0.49-0.52 m |

## In-game check

When you may start the game, one command runs the checks of both research plans with one game and the bot, and
prints PASS, FAIL or SKIP for each (about 3 minutes; it starts and closes the game, with the test save in
`out\test-save` when there is one):

```
python tools\build_native.py build
python tools\mp\ghost_ingame_check.py --link --spot -1033.6 252.3 1809.3 132 --give-weapon
```

`--spot x y z yaw` first moves Link to open ground and turns the camera to `yaw`, so the walk, run and sprint have
room (the test save loads Link by a rock face; without it S4 climbs the rock). The spot above is on the Great Plateau,
about 22 m from the edge. The check never teleports Link while he climbs (it lets go with B first). `--give-weapon`
(for a save without a weapon, like the test save): at the end the ghost mod spawns a Traveler's Sword in front of
Link and he takes it, for the attack (S6) and the item name (S9); the game is then closed without `ghost despawn`.
If the game saves after that, the save keeps the sword. Without `--link` it skips the Link body and checks the
Sheikah man instead. `--no-launch` uses a game that is already running. Last run: **16 PASS, 0 FAIL, 0 SKIP**
(eleventh round; the Sheikah man run: all PASS but S8, a busy PC, see there). Results also go to
`out\research\ghost-ingame-results.txt`, screenshots to `out\shots\ghostcheck\`. Checks: Link found by `ghost self`,
the animation slots' shape, `Wait` standing, `Move` walking, running and sprinting, a jump, an attack (needs a weapon:
equipped, or `--give-weapon`; else SKIP), item names, the frame cost of `ghost self` (two pairs, averaged), the Link
body (spawn, `Wait`, walk and run, attacks, jump, glide, climb, swim, frame cost), and a relay with the bot sending
animation names (`bot.py --anim`).

The held items (0.6.2) have their own run, H1-H16 (`tools\mp\ghost_items_check.py`; it builds and deploys the Link
body too, about 12 minutes):

```
python tools\mp\ghost_ingame_check.py --items --spot -1033.6 252.3 1809.3 132 --give-weapon --lib out\ghost-items-lib
```

`--lib` is a scratch mod library: `ghost.dll` goes to `out\ghost-items-lib\mods` and the Link body to
`out\ghost-items-lib\overlay`, never to your own library. The mod plays the other player with `ghost items`; H10 goes
through a relay, the bot (`--items`) and the client (`--ghost-items`). Unless a check says otherwise, the ghost holds
them as Link does with a sword out: sword in the right hand, shield on the left arm, bow on the back. Checks: H1 all
three held in their places while the ghost stands, walks, runs, jumps, glides, climbs and swings, then the bow drawn
(the bow in the left hand, the rest on the back); H2 switching (each old item gone within 1 s; then
10 switches 0.3 s apart, with the longest frame against a quiet moment); H3 clearing; H4 on the back and back again
(10 toggles); H4P the ghost's back from the items' own offsets against Link's own (the bow 0.1 m off: for the
record); H4L Link's own offsets copied (`ghost items rel`): the ghost's back must match his, with a picture of both from
behind; H5 Link can't take them (unarmed, and armed after H12); H6
despawn, H7 the distance rule, H8 the game removing the ghost, H9 a reload (Link falls from 90 m, Game Over,
Continue): the old items watched through it and the items come back; H10 the network (every change of every cycle
within 1 s of the bot's own time for it; then an older sender without `on_back`); H11 the frame cost (3 pairs alone /
with the items in turn, the medians; the plan's average for the items' part, with the whole tick's worst over the
same frames); H12 `ghost give`: Link takes a sword, a shield and a bow
(this changes the test save if the game saves); H13 the sender's "on the back" bytes; H15 a real sender: a second
client sends this Link (1.5 m to the side) through a relay, and the ghost must show what he holds after a sprint, a
draw, a throw (he drops his sword) and a new sword, and after the sprint his items on the ghost's back where his are
(his offsets through the network); H16 every held item drawn at Link's size (`items.N.size` 1,
and Link's own `ghost self` sizes); H14 no bad line in the log, and the game closed with `exit`. Last run:
**22 PASS, 1 FAIL** (fourteenth round, run 5: H4P, the record of the items' own offsets; the copy, H4L, passes). Results go to
`out\research\ghost-items-results.txt`, screenshots to `out\shots\ghostitems_*.png`.


## Stand-ins: which actors work

Picked from the names in `Actor\Pack` (names only): cutscene ("demo", `Dm_`) copies of people. Not all of them are
without AI: Zelda's copy runs the full NPC AI (`AIProgramUser` `Npc`; it plays her `Wait`, which is why she stood
with her arms down), while the Sheikah man's has none (`DemoDummy`, so he stood in a T-pose). An earlier version of
this page said Zelda's copy had no AI: that was wrong (out\research\ghost-anim-plan.md). Each was spawned 4 m in
front of Link, then left alone for 3 s. Screenshots `out\shots\ghost2_cand_<name>.png` and `ghost2_side_<name>.png`.

| Actor | Result |
|---|---|
| `Dm_Npc_Sheiker_Searcher_M_001` | **The default.** A Sheikah man, Hylian body, Link's size; visible and stable. No AI, so nothing changes his animation; `Wait`, `Walk`, `Run` (and `RunGear2`, `Turn`); no cloth physics, no measurable frame cost. Has physics (moved with the physics transform). With `ghost anim off` he stands in a T-pose. |
| `Dm_Npc_Zelda_Sibyl` | The default before 0.3.0. Zelda in her white dress, upright, arms down, Link's size; visible and stable, no Talk prompt. Has physics: moves only with the physics transform (now done). Runs the NPC AI, which plays her `Wait`; it never changed our animation in the test. Walks and runs too, but the dress hides her legs, and she costs 3-7 frames a second. |
| `Dm_Npc_Hyrule_KingSoul` | The old king; visible, stable, large. |
| `Dm_Npc_Rito_Hero`, `Dm_Npc_Zora_Hero`, `Dm_Npc_Gerdo_Hero`, `Dm_Npc_Goron_Hero` | The four champions; visible and stable (the Goron is very large). `Dm_Npc_Goron_Hero` didn't follow "set position and rotation" either (like Zelda before the physics transform); none of them was tried with it. |
| `Dm_Npc_Goron_Hammer_C_000` | Live, nothing visible. |
| `Dm_PlayerStole2` | A cloth lying on the ground. |
| `PauseMenuPlayer` | The pause-menu Link: live for 5 s, nothing visible in the world. |
| `Mannequin_001_Upper` | Live, nothing visible; goes back to the spawn point when not moved. |
| `FldObj_KnightDoll_01`, `FldObj_KnightDollMini_01`, `TwnObj_GerudoDollBlue_A_01`, `FldObj_RuinStatueKnight_A_01` | Not people: a training dummy, a small doll (its physics pulls it down), a green toy, a huge statue (a wall in front of the camera). |
| Not tried | `GameROMPlayer` (the player itself: never spawn it twice), `Npc_*` (AI and talk events), `WolfLink` (AI). |

A "Check Sheikah Slate" tutorial popup, which the plateau shows after some minutes of play, covered the screenshots
of `Mannequin_001_Upper` and the dolls. All spawned in 0.02-0.6 s. `--ghost-actor` (client) or
`ghost spawn <name>` picks another one; `ghost` shows `physics` and `pulled_back_m` (well above 0 while it moves:
the actor doesn't follow).

## Limits

- **Not Link** (by default). A Sheikah man who stands, walks or runs by his speed: no head turn, no jump, climb, swim,
  glide or riding pose, no attacks, no held weapons or items. With `--ghost-sync` he still shows only those three
  (he has nothing else); the [Link body](#link-body-050-opt-in) has the rest (works on one PC). Held items (0.6.0,
  `--ghost-items`) only on the Link body; no armor. The feet slide a little when the speed doesn't match the clip. The
  fallback is a crate (no animation). The facing is the walking direction, so a player who walks backwards or
  sideways is shown facing the way they move (the game's state exposes no facing).
- **About 50 ms behind**: the smoothing reaches each position when the next one arrives, on top of the client's
  0.1 s jitter buffer. Over Wi-Fi the position still stops for a moment during a stall and then catches up (the
  animation now keeps running through it). A larger client `--delay` (say 0.2 s) gives the jitter buffer more slack,
  so short delays no longer stop it (not measured).
- **Walk/run over Wi-Fi: not re-tested in the game yet** (0.4.0). The simulation says it works; the two-PC test is
  the real proof.
- **0.5.x in the game:** checked on one PC with the bot (tenth round). Not checked yet: attacks and item names from a
  real weapon (the test save has none; equip one and run the check), and two PCs over Wi-Fi.
- **Cost**: the Sheikah man and the crate cost nothing measurable; Zelda costs 3-8 frames a second (44 instead of
  52 in the second round, 46-50 instead of 51-53 in the fourth).
- **1.9.0 only.** Every address is for the 1.9.0 `main`; the mod refuses other versions.
- **Physics.** For an actor with physics (both people) the mod moves its rigid bodies from the frame job each frame.
  The research flagged that as the main deadlock risk (the physics step may run on another core). Nothing went
  wrong in the tests (about 25 minutes of moves in all), but it is not proven safe. The body also collides: Zelda
  placed on Link's spot pushed him 0.47 m, so a player walking into the ghost may be nudged.
- **Threading.** The tick runs on a worker job next to the game's Awareness/LOD jobs, not on the main game thread.
  Moving the actor there can race with the actor's own update on another core (at worst one frame drawn at the old
  place), and so can the animation switch (it rebuilds the actor's animation tree at once). Nothing went wrong in
  17,767 play calls in the stress test (fifth round), but it is not proven safe either. If it ever crashes there,
  the planned fix is to make the call from the actor's own update (out\research\ghost-anim-plan.md, plan B).
- **Distance.** The game removes the ghost within a second once it is about 75 m from Link, measured flat (kept at
  74 m, removed at 76 m; height doesn't count). So the mod shows nobody farther than 60 m: state `far`, and the ghost
  comes back when the other player is within 50 m again. The limit was measured on the Great Plateau for the Sheikah
  man and Zelda only; other places or actors may differ (the crate was kept at 77 m). If the game still removes the
  ghost (the rule missed), the mod waits 2, 4, 8 ... up to 60 s between tries, and stops after 20 tries in a row; the
  client asks again later.
- **Shrines and other interiors** have their own coordinates: a player in a shrine shows up at meaningless overworld
  coordinates.
- **Going back to the title screen** wipes guest memory. The mod drops the ghost when it sees its link turn invalid;
  pointers reused by chance after a full reload were not seen but are not ruled out.
- One ghost per game. Install it with `modman` (it has a `mod.toml`) or use `SWMOD_DIR` as above.
