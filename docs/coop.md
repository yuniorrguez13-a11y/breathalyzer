# Co-op: play the story together (beta work)

**Status (2026-11-12):** the session layer, base security, home-network discovery, shared time and story together
are built and pass their tests on made-up saves and on save **copies**. **Checked in the game on 2026-11-12** (save
copies only, the bot as host): an early save joined, got the 100 % save's story before loading, and played it (see
"Game check 2026-11-12" at the end). Live reading of story flags in the game is **off** for now (no safe getter yet). The Multiplayer menu (Host / Join / Solo) is a small window
started from a terminal; Breathalyzer's Play button doesn't open it yet, and the same menu over the game's title
screen is not built yet.

## What you get

- **The host owns the story.** Your friend gets the host's progress **added** to his co-op save: main quest steps,
  Divine Beasts, shrines, towers, memories, the paraglider and runes, cutscenes already seen. Nothing is ever taken
  away, and his normal save is never touched.
- **Joining mid-story works.** The host's story is added to the friend's co-op save **before his game loads**, so he
  walks out with the towers up, the beasts freed and the paraglider (as the game builds the world from those flags).
- **Live:** while you play, the host's new progress reaches the friend's game within a few seconds (through the ghost
  mod's `coop` command and the game's own flag manager).
- **Same time of day:** the host's hour is everyone's (an inn night on the host moves everyone).
- **The host alone controls the world:** only the host can change time, weather, the story, kick, ban or end the
  session. Friends' progress reaches the host's world only if the host turns it on (Off by default).

Each player keeps his own hearts, stamina, Spirit Orbs, items, rupees, armor, horses and the Master Sword.

## The Multiplayer menu

```
python tools/mp/coop_agent.py menu --library <library> --name Ben --own-save <your save folder>
```

```
Multiplayer
  1  Host a world      your friends join you
  2  Join a world      Ana's world  -  Ana  -  2 of 8 players  -  needs a room code
  3  Play solo         your own save
```

Keys 1 2 3, arrows, Enter, Esc. Join lists the hosts on your home network by name, found by themselves (no typing);
a room code is picked with three word wheels; then the story question. Your own save folder is only **read**: the
first time, it is copied to `<library>\joined\<world>\` (a friend) or `<library>\worlds\<world>\` (a host), checked
byte for byte, and that copy is what co-op plays on. Start the game with the folder it prints
(`SAVAGEWIND_SAVE_DIR`) and press Continue: a co-op folder is always a copy of a save, so the game never opens on the
intro.

## How to play (terminal, step by step)

Both players need the ghost mod 0.7.0 (docs/ghost.md) and a story list built from their own game (names only, read
from your own game files; nothing is downloaded):

```
python tools/mp/story.py list --romfs "<Savage Wind>\extracted\romfs" --out <library>\mp\story-list.txt
```

`<library>` is a folder for multiplayer files (keys, bans, backups); tests use `out\coop-lib`.

**Host** (the PC that keeps the world):

```
python tools/mp/coop_agent.py host --library <library> --name Ana --game-port 7777 [--code "apple river stone"]
```

It runs the relay on your PC (there are no project servers), answers Join lists on your home network, and sends your
hour and your story once a second.

**Friend:** make your **co-op copy** first (your own save folder is only read):

```
python tools/mp/coop_agent.py copy --own-save <your save folder> --library <library> --world "Ana's world"
python tools/mp/coop_agent.py find
python tools/mp/coop_agent.py join --library <library> --name Ben --save-dir <your co-op copy> [--code "..."]
```

`find` lists hosts on your home network by name (no typing an address; `--address` if they aren't found). Join asks,
every session:

```
Share story progress with Ana?
Your co-op save gets Ana's story: towers, shrines, Divine Beasts, quests.
Nothing is taken away. A backup is made first.
[ Yes, share (Y / Enter) ]      [ No (N / Esc) ]
```

Yes: the whole co-op folder is backed up to `<library>\mp\backups\<folder>\<time>\` (the newest 10 are kept), then
the host's story is added to every numbered slot. Start your game with that folder (`SAVAGEWIND_SAVE_DIR`) and
Continue. No: co-op without story (time and ghosts only).

**Undo story share** (game closed): `python tools/mp/story.py undo --save <co-op copy> --backups <that backup folder>`
puts the newest backup back, byte for byte.

## Save safety

- Only co-op copies are written: a copy gets a `breathalyzer-coop.json` mark when it is made (checked byte for byte
  against your save first), and the merge and Undo refuse any folder without it. The merge changes values **in place, only 0 to 1**; the file size never changes. It
  writes `game_data.sav.new`, reads it back, checks that only those values changed, then renames it over.
- Nothing is written while a game uses the folder (`save.lock`, or `savagewind.exe` running).
- What counts as story: a bool flag that is saved, never reset and starts at 0, in the kinds above (1,075 on 1.9.0:
  986 base + 89 DLC). The Master Sword's "got it" step is left out (the sword is each player's own). DLC kinds are
  shared only when both use `--dlc`.
- Checked on copies (2026-11-12): the 100 % copy's 747 base story flags added to the early copy (7 before); all 6
  slots kept their size; the source copies were unchanged; the backup equals `out\test-save`.

## Security (host-only control)

| Risk | What stops it |
|---|---|
| A friend changes time, story, weather, kicks, bans or ends | Only the host's own connection (127.0.0.1 + a secret in `<library>\mp\host.json` that never leaves the PC) may send them. From anyone else the relay drops them; 3 in 10 s = kicked |
| Fake sender | The relay writes `from`, `id` and `name` itself. Friends apply time and story only when `from` is the host |
| Friends push into the host's save | Never by default: a friend's `story_offer` reaches the host only if the host started with `--friends-story` |
| Strangers | Room code: 3 words from a 256-word list (`--code`). Wrong codes are refused; 5 a minute from one address = 10 minutes out. Discovery answers say only "needs a code", never the code |
| Unwanted player | Kick (out for 10 minutes, key and address) and ban (`<library>\mp\bans.json`). A ban can be dodged by someone on your network with a new key: use a room code |
| Floods | 30 lines a second in all; per kind: state 25, story 2, time 2, weather 1, story offers 1 a second; 4 KB a line; 4 not-yet-hello connections per address |
| Bad data | Every field checked; story lines must be lowercase hex of the exact length for the receiver's own list, and the mod checks again (only its own names, only 0 to 1) |
| Internet | The agent's relay takes home-network addresses only (private, link-local, loopback, 100.64.0.0/10 for virtual-LAN tools). Internet play comes later, still player-hosted |
| Fake host on the network | `find` shows each host's 4 check words; the host's terminal shows the same words |

Older Breathalyzer versions still join as **visitors**: they see the ghosts; they never get time or story.

## How it works

- `tools/mp/session.py`: the hello with `features` (`coop1` time, `story1` story), the new message kinds and their
  checks. Protocol version stays 1.
- `tools/mp/security.py`: room codes, host secret, player keys (only a fingerprint is sent), bans, limits.
- `tools/mp/discovery.py`: UDP 7788 broadcast, 512-byte query, answers never larger than the query, at most once a
  second per address, home addresses only, 16 hosts at most, a host is gone after 6 s.
- `tools/mp/story.py`: the story list, the 270-character story line (bit i = byte i/8, bit i%8 from the low end),
  the in-place merge, backup and undo.
- `tools/mp/coop.py`: following the host's time (only when 2+ game minutes off, never in a menu or loading) and story
  (a union: what the host once had stays).
- `tools/mp/coop_agent.py`: host, join and the menu; `tools/mp/lobby.py`: the menu (a state machine any window
  can draw); `tools/mp/worlds.py`: the co-op save copies.
- `tools/mp/relay.py`: host role, host-only kinds, room code, bans, per-kind limits, visitors.
- `native/examples/ghost/coop_story.c` (in the ghost DLL, because modhost runs one frame job per game): reads every
  listed flag through the game's get-bool-by-name (48 a frame; OFF until one is found: the first guess,
  `0x700112dba4`, is not a getter and crashed the game) and adds queued ones through
  set-bool-by-name `0x700112d5c0` (value 1 only), using the flag manager at `[0x7001e3b188]`; waits while its busy
  byte `+0xc1a` (bit 2) is set or there is no world. Commands: `coop story load <list> [dlc]`, `coop story get`,
  `coop story add <hex>`, `coop story status`, and in developer mode `coop probe flag <name> [1]`.

## Honest limits

- **Live story reads are off.** The game's get-bool-by-name is not known yet (the first guess crashed the game in
  probe run B), so a host's game can't report new progress live yet and a friend's agent can't see what his game
  has. What works: the story merge at join (before the game loads) and the setter (adds). Until a getter is found,
  the host's story comes from his save file (`--story-from`), so new progress reaches friends at their next join.
- **Weather is not shared yet** (needs probe A). Day count and Blood Moon are not shared.
- A live add of a tower, shrine or beast shows after your next loading screen [guess]; at join everything is there.
- Not shared: Korok seeds, chests, side quests, puzzle states inside shrines and beasts, place names, Spirit Orbs.
- The friend needs his own save that has left the first cave (beta rule); a host's brand-new world is not built yet.
- The menu is not on Breathalyzer's Play button yet (the launcher's owner: Play with the multiplayer mod on calls
  `tools.mp.lobby.choose()`, then starts the game with the co-op folder and the agent), and not over the game's title
  screen yet (needs a probe of the overlay). The agent doesn't start or stop the game itself yet.
- The ghosts (tools/mp/client.py) still use their own connection: point it at the host's address, with
  `--code "<the room code>"` when the host set one. It joins the relay's ghost room, not the co-op one, so the agent
  and the ghosts are two connections per player for now.
- Home network only for the beta.

## Game check 2026-11-12 (probe run B, save copies only)

Guest: a copy of `out\test-save` (Great Plateau, after the first tower: 7 story flags). Host: the bot with the 100 %
copy's story (747 flags) and the hour 21:00. Join with Yes, then the game started on the merged copy.

| Check | Result | Evidence |
|---|---|---|
| Hosts found on the home network | PASS | `find` listed Ana's world with its check words |
| Backup before the first merge | PASS | `<library>\mp\backups\run-guest\<time>\`: equal to `out\test-save` byte for byte |
| Story added, nothing removed | PASS | every slot 7 to 747 flags before the game started; 0 removed; the same after playing |
| The merged copy loads, no cutscene loop | PASS | loaded on the plateau; Link ran 10 m with the stick; screenshots `out\shots\coop_*` |
| Time follows the host | PASS | the game went from 11:03 to 21:00 within 1.1 s (`time` answers `hour`) |
| Divine Beasts | PASS | all four Champion abilities on the HUD; Mipha's Grace revived Link after a fall |
| Paraglider | PASS | Link glided off the Great Plateau (`coop_29_glide.png`) |
| Quest log | PASS | Adventure Log: Destroy Ganon and The Hero's Sword open; Follow the Sheikah Slate, The Isolated Plateau, Seek Out Impa, Free the Divine Beasts done |
| Towers | PASS (seen) | blue (active) tower beams in the distance; the map screen didn't open with `press minus` |
| Setter (live add) | PASS | `coop probe flag IsPlayed_Demo010_1 1` answered 1; a menu save right after had it set, with all 747 kept |
| Getter (live read) | FAIL | the first guess crashed the game (twice); the mod no longer calls it |
| Save loads after a restart | PASS | the merged copy, and the game's own save made on it, loaded again |
| Weather follows the host | not built | needs probe A |

Still to check in the game: a real getter (then `coop story get`, the host's live progress through the agent and the
read cost), the map screen, and a friend's `time`/`story` refused in a real game (the relay tests cover it).

## Probe list (kept for the next run)

1. `coop probe flag FirstTower_Activated` reads 1 and `IsGet_PlayerStole2` reads 0 on the test-save copy.
2. `coop probe flag IsGet_Obj_Camera 1` sets it; `coop story get` shows it; the camera rune shows.
3. A merged copy (early save + the 100 % copy's story) loads: towers and blue shrines on the map, the paraglider
   works, the runes are there, no replayed cutscene, one beast seen freed.
4. `frames 10` with the reads on and off (under 1 fps lost).
5. The bot as host (`coop_agent.py host --story-from <copy> --time 21`): the friend's game shows night within 2 s;
   a story change arrives within 2 s.
6. A friend's `time` or `story` changes nothing; the friend is kicked after 3.
7. Save, `exit`, read the copy: the added flags are in the file. Undo brings the backup back byte for byte.
