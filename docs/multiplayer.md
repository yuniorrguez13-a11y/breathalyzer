# Multiplayer (proof of concept)

**What works:** 2 to 8 players, each with their own game on their own PC, see each other on a radar window
(distance, direction, height) and can teleport next to each other.
**What doesn't:** the other Link is not drawn in your game. With the ghost mod, `--ghost` puts a stand-in actor (a
cutscene copy of a Sheikah man, walking and running) where they are: see
[Ghost in the game](#ghost-in-the-game-experiment).

Tested on 2026-11-07 with two real PCs (two laptops of the same model on home Wi-Fi, one hosting the relay):
both players joined, teleported next to each other with **Go to**, and each saw the other as the ghost. The ghost
stayed in sync with little delay, but its animation stayed on standing while the other player ran. Fixed by sending
each player's speed (below); not yet re-tested in the game.

## Play with a friend in Breathalyzer (Breathalyzer Plus, Experimental)

Nothing to type: Breathalyzer's **Multiplayer** page (Home > **Multiplayer**) starts everything with its own Python.

You need, each of you: Breathalyzer Plus, Savage Wind 0.4.1-alpha.4 (not a newer one such as 0.5.x) with your own
game set up, a save of your own (multiplayer starts from a copy of your newest save), and both PCs on the same Wi-Fi
or router (home network only: there is no server of ours anywhere; the host's PC is the server).

1. **Turn it on.** Home > **Multiplayer** > Ghost multiplayer > **Turn on** (or turn it ON in the mod picker). It
   doesn't run while Infinite Stamina is on.
2. **Who am I** (if you like): **Change**, then pick a character: Link, Linkle, Zelda, a Hylian, Paya, Urbosa,
   Daruk, Revali, Mipha, a Bokoblin or a Moblin. **Each PC shows the others from its own built characters:** press
   **Build the characters** once on every PC (a few minutes, from your own game files; nothing is downloaded). Then
   you see your friends as who they picked, and they see your pick if they built them too; anyone else is a stand-in
   figure. Link is a stand-in figure for now (Breathalyzer doesn't build the Link body yet). All 9 characters load in
   the game.
3. **Host** (one of you): press **Host**. The first time you are asked for **your name** (everyone on your home
   network sees it: your world is called by it). Breathalyzer opens your world on your PC. The first time, it waits
   at **Your world is open**: Windows asks whether Python may use the network, press **Allow**; your Wi-Fi must be set
   to **Private** (Windows Settings > Network > your Wi-Fi), or Windows still blocks it. Then press **Start the
   game**, and **Continue** in the game. Missed the question? Windows Security > Firewall & network protection >
   Allow an app: tick Python for Private (the page says so until a friend has come in).
   **Story together** (ON at first): your friends' co-op saves get your towers, quests and freed Divine Beasts (each
   friend is asked first, and nothing is ever taken away). **Room code**: **Make one** and tell your friends the 3
   words; OFF, anyone on your home network can join.
4. **Join** (the others): press **Join** (your name too, the first time; if Windows asks about Python, press
   **Allow**). The worlds on your home network show up by themselves (with their check words: they must match the
   host's page). Pick one, type the room code if it asks, answer **Share story progress with Ana?**, and Breathalyzer
   starts the game: press **Continue**.
5. **Stop** ends it. While Breathalyzer still gets the mods ready, Stop cancels the game too; an open game keeps
   running, **on your multiplayer copy** (what you do there isn't in your own save: close it and press Play for your
   own). Closing the game, or Breathalyzer, ends multiplayer too. When the host stops, his friends are told.

The page says what happens in plain words: "You're hosting Ana's world.", "Ben joined your world.", "You see Ben in
your game.", "Ben: 12 m NE, 2 m higher"; **Details** has what the tools said (your home folder written as
`%USERPROFILE%`). Also on the page: **Your name** (what friends see; 1 to 20 letters, digits or spaces).

**Your save.** Multiplayer always plays on a **copy** of your save, made once: the host's world is
`modloader\multiplayer\worlds\<name>s_world`, a friend's copy is `modloader\multiplayer\joined\<world>-<check
words>` (one per host), with story backups in `modloader\multiplayer\mp\backups`. Your own save (`save` next to
`savagewind.exe`) is only read. Play (not Multiplayer) plays your own save, as always. Undo a story share: docs/coop.md.

| The page says | What to do |
|---|---|
| Turn on Ghost multiplayer first | Ghost multiplayer > Turn on |
| Turn off Infinite Stamina on Home first | Ghost multiplayer doesn't run with it |
| You have no save yet | Play once on your own and save: multiplayer starts from your newest save |
| No friend yet? / No friend has reached you yet / Friends' Join list can't find you | On the host's PC: Windows Security > Firewall & network protection > Allow an app: tick Python for Private; set the Wi-Fi to Private |
| Nothing yet? (the Join list) | Both PCs on the same Wi-Fi, set to Private; on both PCs Windows Firewall allows Python |
| Can't reach the host's PC | Same Wi-Fi or router? The host allows Python in Windows Firewall (private networks) |
| Wrong room code / This world needs a room code | Ask the host for the 3 words |
| Another program uses port 7788 | Another Breathalyzer is hosting on this PC: stop it |
| Ghost multiplayer didn't run in your game | Turn off Infinite Stamina (Home, or the game's Mods page), then Host or Join again |
| The game didn't start | The page says why (the mods couldn't be made ready, the game was already open...) |
| The characters couldn't be built | The page says why (game files not set up, the game open...); Details has the rest |

What the page starts (developers; all with Breathalyzer's own Python, `--library` last, ended through their stdin):
`tools\mp\coop_agent.py host --name N --game-port 7777 --stdin [--story-from <world>] [--code ...]` (the relay and
the Join list's answers on this PC) or `coop_agent.py join --name N --address A --port P --save-dir <copy> --yes|--no
--check <words> --game-port 7777 --stdin [--code ...]`, both with `--library <modloader>\multiplayer`; then the
game through the launcher's own Play (so the mods, the ghost mod too, are ready and the control port is open), with
`SAVAGEWIND_SAVE_DIR=<the copy>`; then `tools\mp\client.py --relay A:P --name N --no-ui --ghost-looks --game-port 7777
--stdin --library <modloader>`. The code: `tools\mp_page.py`.

Not checked yet between two PCs with the launcher: everything above passes its tests with stand-in tools; the tools
themselves were checked as below.

## Play with a friend by hand (from this repo)

- Each player: Savage Wind 0.4.1-alpha.4 with their own game set up, and this repo with Python 3.13 (or
  Breathalyzer Plus: where this page says `python`, use `runtime\python.exe` from Breathalyzer's `app` folder).
- One player runs the relay. The others must reach its TCP port 7788 on the same home network (or a virtual LAN
  tool). The relay turns away anyone outside the home network: internet play comes in a later update.

1. **Host: start the relay.**

   ```
   python tools\mp\relay.py
   ```

   If Windows asks whether Python may use the network, allow it for private networks. Tell your friend your
   address: the IPv4 address from `ipconfig`.

2. **Everyone: start the game with the control socket on**, then load a save. With Breathalyzer: Play (above).
   From this repo (this way skips modman run, so no mod loads; use `modman run` for the ghost mod):

   ```powershell
   $env:SAVAGEWIND_CONTROL='7777'; & '.\savagewind.exe'
   ```

3. **Everyone: start the client.** The host uses `--relay 127.0.0.1`.

   ```
   python tools\mp\client.py --relay 192.168.1.20 --name Ana
   ```

4. The radar shows the others. North is up (tick "Turn with camera" to put your view up). The mouse wheel zooms,
   a double-click goes back to automatic range. Pick a player in the list and press **Go to <name>** to teleport
   next to them. Stand still on the ground first.

Client options:

| Option | Default | What it does |
|---|---|---|
| `--relay HOST[:PORT]` | `127.0.0.1:7788` | Where the relay runs |
| `--name NAME` | your Windows user name | 1-20 letters, digits, space, `_ . ' -` |
| `--room ROOM` | `default` | Only players in the same room see each other |
| `--no-ui` | off | No window: print distances once a second |
| `--record FILE` | off | Also save your path as JSON lines, replacing FILE (for the bot) |
| `--game-port N` | `7777` | Must match `SAVAGEWIND_CONTROL` |
| `--hz N` / `--delay S` | `20` / `0.1` | Updates per second / how late others are drawn |
| `--ghost` / `--ghost-peer NAME` / `--ghost-actor ACTOR` | off | Show the nearest (or that) player in your game; needs the ghost mod, see below |
| `--ghost-sync` | off | Also share animations (attacks too) and say what the other player holds. Both PCs, ghost mod 0.5.0. Checked in the game on one PC with the bot; not yet between two PCs |
| `--ghost-link` | off | Show them with the Link body (build it, pass the in-game check, then deploy it: docs/ghost.md). Implies `--ghost-sync`. Checked in the game on one PC with the bot; not yet between two PCs |
| `--ghost-items` | off | The ghost also holds what they hold (weapon, shield, bow; in the hands or on the back). Needs `--ghost-link` and ghost mod 0.6.1. Only weapons, shields and bows are ever spawned (see below). Checked in the game on one PC with the bot; not yet between two PCs |

The relay takes `--port`, `--host`, `--room-cap`, `--rate` and `--max-clients`. Ctrl+C stops any of the three
tools; the relay tells its clients it stopped.

## Test alone

```
python tools\mp\relay.py
python tools\mp\client.py --name Me
python tools\mp\bot.py
```

The bot walks a 10 m circle around the first real player who joins (`--radius`, `--speed`, or `--at X Y Z` for
a fixed point). To replay a real walk, record it, then play it back:

```
python tools\mp\client.py --name Me --record out\paths\walk.jsonl
python tools\mp\bot.py --replay out\paths\walk.jsonl
```

To test animation sync alone, the bot can send animation names in a loop, as `--ghost-sync` does (`NAME@m/s:seconds`;
`@` sets its circle speed), and a weapon name:

```
python tools\mp\bot.py --radius 4 --anim "Wait@0:2,Move@2:3,Move@6:3,CutS1@0:1,Jump@0:1" --weapon Weapon_Sword_001
```

For held items (`--ghost-items`), the bot sends `--weapon`, `--shield`, `--bow` and `--on-back MASK` (bit 0 weapon,
1 shield, 2 bow on its back), or a loop of them (`WEAPON/SHIELD/BOW/MASK:seconds`, `-` for none):

```
python tools\mp\bot.py --radius 4 --items "Weapon_Sword_001/Weapon_Shield_001/Weapon_Bow_001/0:5,Weapon_Lsword_001/-/Weapon_Bow_001/1:5,-/-/-/0:3"
```

The replay starts where you stand when the bot picks you, and stays there: it doesn't move with you after that.
Add `--absolute` to walk it where it was recorded. Loading screens and pauses in the recording are skipped (the bot
jumps). Keep recordings in `out\`: they come from your game, and `out\` is never committed.

## How it works

- The client asks the game for `pos` (x, y, z, stamina, place) and `camera` (yaw) in one batch, 20 times a second,
  over the game's control socket (127.0.0.1 only), and sends them to the relay as one JSON line.
- Each line also carries `speed`: your speed on the ground in m/s, which your own client measures from your own
  positions and clock (the straight distance you covered over the last 0.4 s). A teleport, or a gap of over 1 s
  (a loading screen), starts the measurement again. So does a frame hitch in your game (one step over 0.15 s and
  over 3 usual steps: a screenshot, an autosave); then your last speed stays until 0.4 s is measured again, so a
  hitch doesn't show as a dip in speed. Network delays can't change it, since it is measured before
  sending. The field is optional and the protocol version stays 1: an older client doesn't send it (the receiver
  then works the speed out from the line's own time stamps), an older relay drops it, and an older client ignores it.
  A bad `speed` (not a number from 0 to 1000) is dropped, never the line.
- With `--ghost-sync` (opt-in, not yet tested in the game) a line also carries `anim` (the animation your Link
  plays, from the ghost mod's `ghost self`), `attack` (true during an attack) and `weapon`, `shield`, `bow` (what he
  has equipped, as actor names). With ghost mod 0.6.0 it also carries `on_back` (0-7: bit 0 weapon, 1 shield, 2 bow
  are on his back, not in his hands; read from Link's own equipment), and with 0.6.2 `weapon_rel`, `shield_rel`,
  `bow_rel` for the items on his back: 12 numbers each, where the item sits in the frame of his Link's `Pod_A` bone
  (a 3x4 matrix row by row; the turn's entries within 1.01, the place within 2 m), so the receiver's ghost carries it
  exactly there (his armor moves the bow: the item's own offsets put it 0.1 m off). Same rules: optional, protocol version 1, a bad
  one is dropped on its own; a receiver with `--ghost-items` and no `on_back` from an older sender puts the bow on
  the back (and the weapon and shield while the sender aims the bow). A line without an item key (the sender's
  game didn't answer in time: a hitch, a menu, a load) changes nothing on the receiver; only `""` means "none", so
  a gap never makes the ghost drop its items and spawn them again. The receiver sends the mod only a name that is a
  weapon for the right hand (`Weapon_Sword_`, `Weapon_Lsword_`, `Weapon_Spear_`), a shield (`Weapon_Shield_`) or a
  bow (`Weapon_Bow_`), and none of the special ones never tried (the Master Sword family and `Weapon_Bow_071`); the
  mod checks the same again, so another player's data can never make your game spawn anything else. An attack that a network stall hid (all its lines arrived after their moment was drawn) is shown for 0.3 s right
  after the stall.
- The relay passes each line to the others in the same room. The protocol is described in `tools\mp\__init__.py`.
- Others are drawn 0.1 s late, blended between the two samples around that moment (a jitter buffer), so packets
  that arrive unevenly don't make them jump. A teleport (two samples over 30 m apart, or faster than 40 m/s between
  them) is not blended: it shows as one jump.
- **Go to** sends `teleport x y+0.5 z` to your game, then checks `teleport status` until it says done or failed.
- If the game isn't running, the client tries again every 2 s; if the relay drops, it reconnects. If the game
  doesn't answer within 5 s (a long load, a pause, the window being dragged), the client opens a new connection,
  so a late answer is never taken for the next request.

What the window's **Game** line means:

| Game: | Meaning |
|---|---|
| in game | Your position is being shared |
| no world loaded | The game runs but no save is loaded |
| waiting on port 7777 | No game with `SAVAGEWIND_CONTROL=7777` is running |
| port 7777 busy | The game takes 4 control connections at a time and all are in use: close another tool that uses it |
| not answering | The game stopped answering for 5 s (loading or paused); the client keeps trying |

## Ghost in the game (experiment)

The ghost mod ([docs/ghost.md](ghost.md)) is a native mod for 1.9.0 that spawns an actor and moves it every frame.
Start the game with it (modhost and `SWMOD_DIR=<repo>\build\native\ghost-mods`, commands in docs/ghost.md),
then add `--ghost` to the client:

```
python tools\mp\client.py --relay 192.168.1.20 --name Ana --ghost
```

The client then also sends `ghost spawn` once and `ghost pos x y z yaw speed` with the nearest other player's
position and speed every tick, in the batch it already sends the game. The yaw is the direction that player walks
in, from their successive positions (slower than 0.5 m/s, or a teleport, keeps the last one; before they first
move, their camera direction). The speed is the one their client sent; the mod plays `Wait`, `Walk` or `Run` by it.
During a network stall the client holds them where it last saw them, with their last speed (for up to 1 s), so the
stand-in keeps running on the spot rather than standing. A mod older than 0.4.0 gets `ghost pos x y z yaw` (the
client asks the mod first). The mod glides the stand-in between these 20 Hz positions at the frame rate and jumps
a teleport over 30 m.
`--ghost-peer NAME` picks a player, `--ghost-actor` another actor. When that player leaves it sends `ghost despawn` until the game answers it (also after a reconnect), and
again when the client stops if the game may still have the ghost. If the mod reports that it stopped trying to spawn
(`wanted` false), the client sends `ghost spawn` again after 10 s, then 20, 40, at most 60 s apart. Without the mod
it says so once and keeps the radar. Tested with the relay and `bot.py` (circles at 2 to 6 m/s): the stand-in stood,
walked and ran around Link, facing the way it went (docs/ghost.md).

**Walk and run over Wi-Fi.** On two PCs (2026-11-07) the stand-in kept its position in sync, but it mostly stood
while the other player ran: the mod worked the speed out from positions that came in unevenly over Wi-Fi, and threw
most of them away as jumps. Now the speed is sent (above). `tests\test_mp_wifi.py` runs sender, relay, receiver and
`ghost pos` through simulated Wi-Fi (0-150 ms random delays, 0.3-0.6 s stalls every few seconds, power-saving
bursts): the right animation for every `ghost pos` while standing, walking and running. The same streams through the
mod's own code (compiled outside the repo): the right animation in every frame; the old code showed `Run` in only
about half to nine tenths of running frames (worse with stronger bursts) and flickered. Not yet re-tested in the
game.

It is a stand-in, not Link: the mod plays its Wait, Walk or Run animation by its speed, nothing more (no jump,
climb or riding pose, no attacks, no held weapons or items).

**Animation sync and the Link body (0.5.0, opt-in; checked in the game on one PC with the bot, not yet between two PCs).** Add `--ghost-sync` on both PCs: each
client then reads its own Link's animation through the ghost mod (`ghost self`) and sends it with its position. The
other side's mod plays that name when its actor has it: jumps, attacks, gliding, climbing, swimming. The Sheikah man
has only Wait, Walk and Run, so with him you still see those three. `--ghost-link` uses the Link body, a small
actor pack you build from your own game files (`python tools\mp\ghost_link_pack.py build`; see docs/ghost.md),
which has Link's animations, mostly under the player's own names (two-handed and spear swings under its own, picked
by the other player's weapon). Weapons, shields and bows are logged ("Ghost: Ana has weapon
Weapon_Sword_001"); add `--ghost-items` (ghost mod 0.6.1, with `--ghost-link`) and the ghost holds them, in its hands
or on its back, and you can't take them (docs/ghost.md, "Held items"). The check (one game and the bot; PASS or FAIL per check; last run 16 PASS, 0 FAIL,
with a sword attack and the sword's name read in the game):
`python tools\mp\ghost_ingame_check.py --link --spot -1033.6 252.3 1809.3 132 --give-weapon`. While the Link body is deployed,
every ghost uses it, and if it ever crashes the game, every use of the ghost does (no crash so far).

## Co-op: story together, shared time, host-only control (one PC checked in the game 2026-11-12; two PCs not yet)

[docs/coop.md](coop.md): the host owns the story and a friend's co-op save gets it **added** before his game loads
(whole pieces only: freed beasts, finished quests; backup first, only 0 to 1, asked every session), the host's hour
is everyone's, hosts on your home network are found without typing (Breathalyzer's Join list, or
`python tools/mp/coop_agent.py find`), and only
the host can change time, the story, kick, ban or end the session (`kick <name>` / `ban <name>` on the host's
terminal; a room code proved without sending it; limits per message kind). Story comes from the host's world save,
not live. Ghost clients join a world with a room code only with `--code`; a wrong or missing code stops them instead
of retrying. Checked in the game on one PC with the bot as host; the two-PC test steps are in coop.md.

## Who am I: pick who your friends see you as (not yet run in the game)

[charsel](charsel.md): pick a character (Breathalyzer's Home > Multiplayer > **Who am I**, the Multiplayer menu's
**4  Who am I**, `python tools\charsel.py wheel`, or `iam zelda` in the game's console), and with `--ghost-looks`
your friends see you as Zelda, Daruk, a Bokoblin... if they built them: each PC shows the others from its OWN built
characters (Breathalyzer: **Build the characters**, the Characters recipe `charsel.py build --add`, which puts them in
your mods, used only while Ghost multiplayer is on; by hand: `python tools\charsel.py build`). A friend who lacks
your pick sees you as the Link body, and without the Link body (`ghost_link_pack.py build`: Breathalyzer doesn't
build it yet) as the stand-in Sheikah figure. Client options: `--look NAME` (who you are; default your pick, else Link), `--no-look`, `--ghost-looks [DIR]`
(implies `--ghost-link`; `--library DIR`: the mod library whose deployed characters it uses). The bot takes
`--look "zelda:5,goron:5"` (a loop).

On the wire (feature `look`, protocol version 1, `tools/mp/session.py`):
- the hello carries `"features":["look"]` and `"look":{"id":"zelda","v":1,"name":"Zelda"}` (an id: lower-case
  letters, digits and `-`, up to 24; a name for people, up to 28 ASCII characters; anything else is dropped);
- the welcome's peers and each join carry the others' looks, to clients that said `look`;
- `{"type":"look","look":{...}}` changes it: a client sends at most one every 2 s; the relay takes one 1.5 s after
  the last (network jitter) and answers earlier ones with "look changes too often", after which the client sends its
  newest pick again; the relay keeps the latest for late joiners and passes it on as
  `{"type":"look","id","name","look"}`;
- an older client (no features) sees none of it and can't send it; an older relay (no `look` in its welcome) gets
  only the hello, so the client sends no `look` lines to it. Only the id travels, never game data.
- the co-op agents (host and friend) leave `look` out of their hello: they never send one and get none. A client that
  says `look` is no visitor, so the relay holds its states to 25 a second: `--hz` is at most 24 (client and bot).
- with `--ghost-looks`, each friend is shown only as a character that is in your game (built, added to your mods and
  deployed), else as the Link body.

## Limits

- Radar only, unless you use the ghost mod (above): a copy of a Sheikah man stands in for the other player.
- The yaw sent to the relay is the camera's direction, not the way Link faces (the ghost uses the walking direction
  instead). Only positions and speed are shared, plus, with `--ghost-sync` (checked in the game on one PC), the animation name
  and the equipped items' names. No facing, mount, health or world state.
- Teleport: stand still on the ground. Not while riding, in a cutscene, a menu or a loading screen. The first
  teleport of a session may need a short search, during which Link must not move. A target below the ground makes
  Link fall.
- Shrines and other interiors have their own coordinates. Distances and Go to between an interior and the
  overworld are meaningless.
- The relay has no passwords and no encryption. Anyone who can reach its port can join a room and see names and
  positions. Run it only while you play. Limits: 8 players per room, 30 messages a second per client, 4 KB per
  line, 64 connections, 10 s to say hello. Nothing is saved.
- `SAVAGEWIND_CONTROL` turns on the game's developer commands, so any program on your PC can read and write the
  game's memory while it runs. Without `--ghost` the client only sends `pos`, `camera` and `teleport` to your game,
  and the only data from the relay that reaches your game is the position you choose to go to. With `--ghost` it
  also sends `ghost spawn`, `ghost pos` and `ghost despawn` (and `ghost self`, which only reads, with
  `--ghost-sync`), so another player's position (and animation name) from the relay reaches your game every tick.

## Next step: drawing the other Link

Drawing a second Link needs code running inside the game, which the control socket can't do. Two routes:

1. **Upstream (Savage Wind):** a runtime command or hook that spawns a puppet actor (Link's model with its AI off)
   and moves it every frame, plus game cells for Link's rotation and animation state.
2. **Without waiting:** a proxy game library loaded with `SAVAGEWIND_GAME_LIB` that runs our code inside the game
   and calls the game's own actor-creation code. This needs the 1.9.0 names and addresses for actor creation
   (code-map work, phase 1 of [PLAN](PLAN.md)). The ghost mod (above) is a first working piece of this route, with
   a cutscene copy of a Sheikah man (or a crate) instead of a Link model.

Either way, the client's 20 Hz state stream and jitter buffer stay as they are and feed the puppet instead of the
radar. Rotation and animation fields would be added to the state message, as optional fields like `speed` (old
clients ignore them), or with a new protocol version.
