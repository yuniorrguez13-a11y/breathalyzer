# Co-op: play the story together (beta work)

**Status (2026-11-12):** the session layer, base security, home-network discovery, shared time and story together
are built and pass their tests on made-up saves and on save **copies**. **Checked in the game on 2026-11-12, on one
PC with the bot as a stand-in host** (save copies only): an early save joined, got the 100 % save's story before
loading, and played it (see "Game check 2026-11-12" at the end). **Not yet checked:** two PCs, a host running the
real game, a room code, a kick, a reconnect, a third player, and the fixes of the second review (the save lock, whole
units only, the new backups). Live reading of story flags in the game is **off** (no safe getter yet). In
Breathalyzer Plus, Home > **Multiplayer** does all of this without a terminal (Host, Join, Story together, a room
code, Stop: [multiplayer.md](multiplayer.md)); the terminal's Multiplayer menu below stays for developers, and the
same menu over the game's title screen is not built yet.

## What you get

- **The host owns the story.** Your friend gets the host's progress **added** to his co-op save: towers, main quest
  steps, freed Divine Beasts, found shrines (their warps), memories, the paraglider, cutscenes already seen. Nothing is
  ever taken away, and his normal save is never touched.
- **Whole pieces only.** A Divine Beast counts once the host has **freed** it; a quest's middle steps once the host
  has **finished** that quest (until then the friend gets the quest started, not its middle). A host caught inside a
  beast or in the middle of a quest gives none of that half-done part.
- **Joining mid-story works.** The host's story is added to the friend's co-op save **before his game loads**, so he
  walks out with the towers up, the beasts freed and the paraglider (the game builds the world from those flags).
- **When it arrives:** the story is read from the **host's save** (his world folder). New host progress reaches a
  friend at the friend's next start (or next join), after the host's game has saved. It is **not** live yet.
- **Same time of day:** the host's hour is everyone's (an inn night on the host moves everyone).
- **The host alone controls the world:** only the host can change time, weather, the story, kick, ban or end the
  session. Friends' progress reaches the host's world only if the host turns it on (Off by default).

Each player keeps his own hearts, stamina, Spirit Orbs, items, rupees, armor, horses and the Master Sword.

### Held back (until a game check says they are safe)

| Not shared | Why |
|---|---|
| Shrine **clears** (`Clear_Dungeon*`): the friend gets the shrine found and its warp, not "done" | A cleared shrine's monk may not give his Spirit Orb any more: the friend could never earn hearts or stamina in his copy |
| Runes (`IsGet_Obj_*`) | If the runes are items, a set flag could stop the friend ever getting them. The paraglider **is** shared (checked) |
| `PictureMemory_GetShirt` | Impa gives the Champion's Tunic at this step: the friend earns it himself |
| A Divine Beast's Heart Container | Not held back but said in the question: a freed beast can't be entered again, so its Heart Container can't be won in that copy |

## The Multiplayer menu

```
python tools/mp/coop_agent.py menu --library <library> --name Ben --own-save <your save folder> [--game-port 7777]
```

```
Multiplayer
  1  Host a world      your friends join you
  2  Join a world      Ana's world  -  Ana  -  2 of 8 players  -  needs a room code
  3  Play solo         your own save
```

Keys 1 2 3, arrows, Enter, Esc. Join lists the hosts on your home network by name, found by themselves (no typing);
a room code is picked with three word wheels; then the story question. Your own save folder is only **read**: the
first time, it is copied to `<library>\joined\<world>-<check words>\` (a friend: one copy per host) or
`<library>\worlds\<world>\` (a host), checked byte for byte, and that copy is what co-op plays on. Start the game with
the folder it prints (`SAVAGEWIND_SAVE_DIR`) and press Continue: a co-op folder is always a copy of a save, so the game
never opens on the intro.

## How to play (terminal, step by step)

Both players need the ghost mod 0.7.1 (docs/ghost.md) and a story list built from their own game (names only, read
from your own game files; nothing is downloaded):

```
python tools/mp/story.py list --romfs "<Savage Wind>\extracted\romfs" --out <library>\mp\story-list.txt
```

`<library>` is a folder for multiplayer files (keys, bans, backups); tests use `out\coop-lib`.

**Host** (the PC that keeps the world):

```
python tools/mp/coop_agent.py copy --own-save <your save folder> --library <library> --world "Ana's world" --host
python tools/mp/coop_agent.py host --library <library> --name Ana --story-from <library>\worlds\Anas_world
                                   --game-port 7777 [--code "apple river stone"]
```

Start your game with `SAVAGEWIND_SAVE_DIR=<library>\worlds\Anas_world` and `SAVAGEWIND_CONTROL=7777`. The agent runs
the relay on your PC (there are no project servers), answers Join lists on your home network, sends your hour once a
second (from the game) and your story (from your world's newest save; a save is used only once two reads agree, so
a half-written save is never sent). Without `--story-from` it warns: friends would get time only. You can start the
agent before the game: it waits for the game and keeps its place in the session meanwhile.

Type on the host's terminal: `players`, `kick <name>` (out for 10 minutes), `ban <name>`, `unban <name>`,
`code <3 words>`, `code off`, `end`. A kick or ban closes every connection of that player (his ghosts too).
If no friend reaches you within a minute it says what to check (Windows Firewall, a Private network).

**Friend:** make your **co-op copy** first (your own save folder is only read):

```
python tools/mp/coop_agent.py find
python tools/mp/coop_agent.py copy --own-save <your save folder> --library <library> --world "Ana's world"
                                   --check "<the host's 4 check words from find>"
python tools/mp/coop_agent.py join --library <library> --name Ben --save-dir <your co-op copy> [--code "..."]
                                   [--game-port 7777]
```

`find` lists hosts on your home network by name (no typing an address; `--address` if they aren't found). With one
host found, join takes it; with two or more it asks which (never the first by itself). Join asks, every session:

```
Share story progress with Ana?
Your co-op save gets Ana's towers, quests, Divine Beasts and shrine warps.
Nothing is taken away. A backup is made first; you can undo it.
Freed Divine Beasts give you no Heart Container in this copy.
[ Yes, share (Y / Enter) ]      [ No (N / Esc) ]
```

Yes: before your game starts, the host's story is added to every numbered slot of your co-op copy. Start your game
with that folder (`SAVAGEWIND_SAVE_DIR`) and Continue. If your game already uses the folder, nothing is written: the
story is added at your next start (the agent tries again while it runs). No: co-op without story (time and ghosts
only). If the line drops (Wi-Fi, the host restarting his agent), the agent reconnects by itself for up to 10 minutes.
`--same-pc`: the host's game runs on the same PC (testing): trust Savage Wind's save lock alone.

**Undo story share** (game closed):
`python tools/mp/story.py undo --save <co-op copy> --backups <library>\mp\backups\<copy folder name>` puts back the
copy from before the last join that added story; add `--before-coop` for the copy from before any co-op story. What
undo replaces is kept too (`...\undone\`).

## Save safety

- Only co-op copies are written: a copy gets a `breathalyzer-coop.json` mark when it is made (checked byte for byte
  against your save first), and the merge and Undo refuse any folder without it. A friend's copy is bound to one host
  (his key) at the first merge; another host's story is refused.
- Nothing is written while a game uses the folder. Savage Wind locks `<save folder>.lock`, a file **next to** the
  save folder, for as long as it runs (the file stays after the game ends; only the lock counts). The merge, undo and
  copy take that same lock for their whole work, so the game can't take it meanwhile; a running `savagewind.exe` is a
  second guard.
- The merge changes values **in place, only 0 to 1**; the file size never changes. Every slot's `game_data.sav.new`
  is written, flushed to disk and read back; only when all check out are they renamed over. A failure leaves every
  slot as it was and no `.new` file.
- Backups: the first merge into a copy keeps `<library>\mp\backups\<copy>\before-coop\` (never removed); a later join
  that adds something keeps a timestamped one (the newest 10). A join that adds nothing makes no backup.
- What counts as story: a bool flag that is saved, never reset and starts at 0, in the kinds above (1,075 on 1.9.0:
  986 base + 89 DLC). DLC kinds are shared only when both use `--dlc`.

## Security (host-only control)

| Risk | What stops it |
|---|---|
| A friend changes time, story, weather, kicks, bans or ends | Only the host's own connection (127.0.0.1 + a secret in `<library>\mp\host.json` that never leaves the PC) may send them. From anyone else the relay drops them; 3 in 10 s = cut off and kicked for 10 minutes |
| Fake sender | The relay writes `from`, `id` and `name` itself. Friends apply time and story only when `from` is the host |
| Friends push into the host's save | Never by default: a friend's `story_offer` reaches the host only if the host started with `--friends-story` |
| Strangers | Room code: 3 words from a 256-word list (`--code`, or `code` on the host's terminal). The code is never sent: the relay sends a nonce, the friend answers with a proof, and the relay proves back that it has the code. No code at all: "this world needs a room code" (no strike). 5 wrong a minute from one address = 10 minutes out. Discovery answers say only "needs a code" |
| Unwanted player | `kick <name>` (out for 10 minutes, key and address) and `ban <name>` (`<library>\mp\bans.json`) on the host's terminal. Every connection of that player closes. This PC (127.0.0.1) is never kicked or banned by address. A ban can be dodged by someone on your network with a new key: use a room code |
| Floods | 30 lines a second in all; per kind: state 25, story 2, time 2, weather 1, story offers 1 a second; 4 KB a line; 4 not-yet-hello connections per address; the co-op room takes no ghost states; cut off for flooding = kicked. A Join list keeps at most 2 rows per device |
| Bad data | Every field checked; story lines must be lowercase hex of the exact length for the receiver's own list, and the mod checks again (only its own names, only 0 to 1). A host's line over 16 KB or a bad welcome ends that join with "bad answer from the host" |
| Internet | The agent's relay takes home-network addresses only (private, link-local, loopback, 100.64.0.0/10 for virtual-LAN tools). Internet play comes later, still player-hosted |
| Fake host on the network | **Partly.** Check words tell hosts apart but don't prove who a host is (a fake host can copy them). Join never picks the first of several hosts by itself. With a room code, a fake host can't make the relay's proof, so the friend refuses it; but from one join answer it could guess a 3-word code on a fast PC. Even then it can only add listed story flags to the friend's co-op copy, after a backup. Play on networks you trust |

A client without the co-op features (an older ghost client) still joins as a **visitor** (ghosts only), but only when
the host set no room code or the client sends it (`client.py --code`).

## How it works

- `tools/mp/session.py`: the hello with `features` (`coop1` time, `story1` story), the new message kinds and their
  checks. Protocol version stays 1.
- `tools/mp/security.py`: room codes and their proofs, host secret, player keys (only a fingerprint is sent), bans,
  limits.
- `tools/mp/discovery.py`: UDP 7788, 512-byte query sent to 255.255.255.255 and each adapter's own subnet broadcast,
  answers never larger than the query, at most once a second per address, home addresses only, 16 hosts and 2 rows
  per device at most, a host is gone after 6 s.
- `tools/mp/story.py`: the story list, the 270-character story line (bit i = byte i/8, bit i%8 from the low end),
  `settle()` (whole units only), Savage Wind's save lock, the all-or-nothing merge, backups and undo.
- `tools/mp/coop.py`: following the host's time (only when 2+ game minutes off, only when the ghost mod says Link is
  free) and story (a union: what the host once had stays; live only towers and memories).
- `tools/mp/coop_agent.py`: host, join and the menu (game talk on a worker thread, never stalling the relay; the
  host's line pings and comes back if dropped; friends reconnect); `tools/mp/lobby.py`: the menu (a state machine any
  window can draw); `tools/mp/worlds.py`: the co-op save copies.
- `tools/mp/relay.py`: host role, host-only kinds, room code challenge, kick/ban of every connection, bans, per-kind
  limits, visitors.
- `native/examples/ghost/coop_story.c` (in the ghost DLL, because modhost runs one frame job per game): reads every
  listed flag through the game's get-bool-by-name (48 a frame; OFF until one is found: the first guess,
  `0x700112dba4`, is not a getter and crashed the game) and adds queued ones through set-bool-by-name
  `0x700112d5c0` (value 1 only), using the flag manager at `[0x7001e3b188]`. Live adds: towers and memories only, and
  only at a safe point (world up, busy bit `+0xc1a` bit 2 down, Link free: no loading, cutscene, riding or pause
  menu, for 60 frames in a row); what it added is set again after any time that wasn't safe (a load can bring the
  save's 0 back). Commands: `coop story load <list> [dlc]`, `coop story get`, `coop story add <hex>`,
  `coop story status` (with `safe`, `held`), and in developer mode `coop probe flag <name> [1]`.

## Honest limits

- **Live story reads are off.** The game's get-bool-by-name is not known yet (the first guess crashed the game in
  probe run B). So the host's story comes from his world save, and new progress reaches friends at their next start
  or join after the host's game saved; a friend's agent never adds live today (it would need to read his game too).
- **Weather is not shared yet** (needs probe A). Day count and Blood Moon are not shared.
- Not shared: Korok seeds, chests, side quests, puzzle states inside shrines and beasts, place names, Spirit Orbs,
  and what "Held back" lists.
- Cutscenes already seen are shared as they are; a cutscene tied to an unfinished quest step could be skipped
  (not seen in the game check).
- The friend needs his own save that has left the first cave (beta rule); a host's brand-new world is not built yet.
- Breathalyzer's Multiplayer page (`tools/mp_page.py`) makes the co-op copy, starts the agent (`--stdin`: it ends
  it through that pipe, so a host tells his friends), starts the game through Play on the copy
  (`SAVAGEWIND_SAVE_DIR`) once the host hosts or the friend's story is in (join prints `Ready: start your game`), and
  starts the ghost client. Not over the game's title screen yet (needs a probe of the overlay). The agent on its own
  doesn't start or stop the game.
- The ghosts (tools/mp/client.py) still use their own connection: point it at the host's address, with
  `--code "<the room code>"` when the host set one (without it, or with a wrong one, it stops instead of retrying).
- Home network only for the beta. The host's `python.exe` needs inbound TCP and UDP 7788 (Windows asks the first
  time: allow it on **Private** networks; a network set to Public blocks it).

## Game check 2026-11-12 (probe run B, save copies only, ONE PC, the bot as host)

Guest: a copy of `out\test-save` (Great Plateau, after the first tower: 7 story flags). Host: the bot (in the same
Python process, on this PC) with the 100 % copy's story (747 flags) and a fixed hour 21:00. The guest connected to
127.0.0.1, so Windows Firewall, real Wi-Fi and the home-network-only rule were not tested. Join with Yes, then the
game started on the merged copy. This was before the second review's fixes (shrine clears and runes were still
shared then).

| Check | Result | Evidence |
|---|---|---|
| Hosts found on the home network | PASS (one PC) | `find` listed Ana's world with its check words (the answer came from this same PC) |
| Backup before the first merge | PASS | `<library>\mp\backups\run-guest\<time>\`: equal to `out\test-save` byte for byte |
| Story added, nothing removed | PASS | every slot 7 to 747 flags before the game started; 0 removed; the same after playing |
| The merged copy loads, no cutscene loop | PASS | loaded on the plateau; Link ran 10 m with the stick; screenshots `out\shots\coop_*` |
| Time follows the host | PASS (bot hour) | the game went from 11:03 to 21:00 within 1.1 s; with a fixed bot hour the friend's clock was pulled back every 2-3 s (fixed since: `--time` now runs like the game's clock) |
| Divine Beasts | PASS (freed beasts only) | all four Champion abilities on the HUD; Mipha's Grace revived Link after a fall. A host mid-beast: not checked (now held back by `settle()`) |
| Paraglider | PASS | Link glided off the Great Plateau (`coop_29_glide.png`) |
| Quest log | PASS | Adventure Log: Destroy Ganon and The Hero's Sword open; Follow the Sheikah Slate, The Isolated Plateau, Seek Out Impa, Free the Divine Beasts done |
| Towers | PASS (seen) | blue (active) tower beams in the distance; the map screen didn't open with `press minus` |
| Setter | PASS (dev probe only) | `coop probe flag IsPlayed_Demo010_1 1` answered 1 in a later solo game (no session connected); a menu save right after had it set. The agent doesn't add live yet |
| Getter (live read) | FAIL | the first guess crashed the game (twice); the mod no longer calls it |
| Save loads after a restart | PASS | the merged copy, and the game's own save made on it, loaded again |
| Runes, map screen, a monk after a shared clear | not checked | probe 3 below |
| Weather follows the host | not built | needs probe A |

## Two-PC test (next game window)

1. Both PCs: the same Wi-Fi, set to **Private**; ghost mod 0.7.1; each builds its story list (`story.py list`).
2. Host PC: `coop_agent.py copy ... --host`, then `coop_agent.py host --library L --name Ana --story-from
   L\worlds\Anas_world --game-port 7777 --code "apple river stone"`; allow Python when Windows Firewall asks
   (Private). Start the game on the world folder with `SAVAGEWIND_CONTROL=7777` and load in. Save once (menu).
3. Friend PC: `coop_agent.py find` must list Ana's world with the same check words the host's terminal shows. Then
   `copy ... --check "<words>"` and `join ... --code "apple river stone" --game-port 7777`, answer Y. Expect
   "Ana's story added: ...". Start the game on the printed folder and Continue: towers, freed beasts, paraglider;
   the hour jumps to the host's.
4. A wrong code from a third device: "wrong room code"; no code: "this world needs a room code".
5. Third player (if any) joins the same way; all follow the host's hour.
6. Host types `kick Ben`: Ben's agent says "kicked by the host" and stops; his ghost disappears from the host.
7. Turn the friend's Wi-Fi off for 20 s and on: his agent says "Back in Ana's world" by itself.
8. Host progress: the host frees a tower and saves; the friend closes his game: "Ana's story added: 1 towers"; the
   friend starts again and sees it.
9. End: host types `end`; every friend's agent says "Ana ended the session". Close the games with control `exit`.

## Probe list (kept for the next run)

1. `coop probe flag FirstTower_Activated` reads 1 and `IsGet_PlayerStole2` reads 0 on the test-save copy.
2. `coop probe flag IsGet_Obj_Camera 1` sets it; `coop story get` shows it; the camera rune shows.
3. A merged copy (early save + the 100 % copy's story) loads: towers and blue shrines on the map, the paraglider
   works, the **rune menu** (are runes flags or items?), no replayed cutscene, one beast seen freed. A shrine shared
   as cleared: does its monk still give the Spirit Orb? (If yes, shrine clears can be shared; if runes are flags,
   runes can be.)
4. `frames 10` with the reads on and off (under 1 fps lost).
5. The bot as host (`coop_agent.py host --story-from <copy> --time 21`): the friend's game shows night within 2 s; the
   friend's clock is not pulled back.
6. A friend's `time` or `story` changes nothing; the friend is cut off after 3 and can't come back for 10 minutes.
7. Save, `exit`, read the copy: the added flags are in the file. Undo brings the backup back byte for byte.
8. `coop story status` while loading, in a cutscene, riding and in the pause menu: `safe` false; walking: true. The
   busy bit `+0xc1a` during a save and a load.
