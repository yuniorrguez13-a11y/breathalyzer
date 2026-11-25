# Game basics for mods (BotW 1.9.0)

[`native/modhost/botw.h`](../native/modhost/botw.h) gives a native mod the most basic game values with plain names:
where Link is, his stamina, moving him up, and running code every frame. Include it after `swmod.h`. Three example
mods use it: [show position](#example-1-show-position), [infinite stamina](#example-2-infinite-stamina) and
[teleport up](#example-3-teleport-up).

- **What was checked.** The addresses under the helpers marked *checked in game* were tested in the real game (dates
  in [How each value was checked](#how-each-value-was-checked); the newest helpers carry the date in `botw.h` too).
  The three examples ran in the real game on 2026-11-07: show position and teleport up loaded with `modload` while
  the game ran, infinite stamina at game start.
- **(not tested in game)** marks a value nobody has checked in the game. Don't rely on those yet.
- **1.9.0 only.** Every helper first checks that the game is version 1.9.0 (`BOTW_GAME_VERSION`). On another version
  it returns 0, -1 or false and touches nothing.
- **Cheap to call often.** The helpers read and write the game's memory directly (in Savage Wind a game address is a
  PC address), with no game calls. They only follow a pointer that points into the game's heap, where the player,
  actors and their parts live, and stop otherwise. That stops almost every stale or bad pointer, but it is not a
  guarantee. Call them while the game runs (in a command or a `botw_on_frame` function), not from `swmod_init` at
  game start: the game isn't in memory yet then.
- Every address, with how it was checked: [addresses](addresses.md). Writing a mod: [code mods](code-mods.md) and
  [`native/modhost/README.md`](../native/modhost/README.md).

## The helpers

| Helper | What it gives | In game |
|---|---|---|
| `botw_version_ok(api)` | true when the game is 1.9.0 | (no game value) |
| `botw_player_info(api)` | the player info block's address, or 0 | checked in game |
| `botw_player_actor(api)` | Link's actor, or 0 | checked in game |
| `botw_player_pos(api, out)` | Link's position x y z | checked in game |
| `botw_in_world(api)` | true when a save is loaded and Link is in the world | checked in game |
| `botw_stamina_get(api)` | Link's stamina (1000 = one wheel), or -1 outside the world | checked in game |
| `botw_stamina_set(api, value)` | sets Link's stamina | checked in game |
| `botw_player_real_pos(api)` | the address of Link's real position, or 0 | checked in game |
| `botw_teleport_up(api, metres)` | moves Link straight up | checked in game |
| `botw_teleport(api, x, y, z)` | moves Link to a place | checked in game 2026-11-07 |
| `botw_on_frame(api, fn)` | runs your function once per frame | checked in game |
| `botw_frames()` | how many frames your function ran | (no game value) |
| `botw_time_of_day(api)` | the hour, 0 to 24, or -1 | checked in game 2026-11-07 |
| `botw_paused(api)` | true while the pause menu is open | checked in game 2026-11-07 (the pause menu; the map not tried) |

Hearts, rupees and the weather are not in `botw.h` yet: no address for them has been checked.

## Using it

```c
#include "swmod.h"
#include "botw.h"

static const SwModApi *api;   // keep the api your swmod_init gets, and pass it to the helpers
```

`python tools/build_native.py build` compiles with `native/modhost` on the include path, so both headers are found.
A mod that is built elsewhere needs both files next to its `.c` file (or `-I` to their folder).

Where helpers can run: in a command (Savage Wind's frame thread) and in a `botw_on_frame` function (a game thread).
Reading is safe from both. A mod's command and its frame function can run at the same time, so share flags between
them with atomics (`__atomic_load_n`, `__atomic_store_n`), as the examples do.

Each symbol in `botw.h` has a one-line doc just above it, starting with `//:`. The mod editor shows that line when you
search for a name.

## Each helper

### botw_version_ok

True when the game is version 1.9.0, the version these addresses are for. Every helper checks it; check it once in
`swmod_init` to log a clear message instead of doing nothing.

```c
if (!botw_version_ok(api)) {
    api->log("mymod: made for game version %s, this is %s", BOTW_GAME_VERSION, api->game_version);
    return 2;
}
```

### botw_player_info and botw_player_actor

`botw_player_info` gives the address of the player info block, the game's object with Link's position copy and his
stamina, or 0 before the game made one. `botw_player_actor` gives Link's actor (the block's `+0x60`), or 0. You only
need them to read values `botw.h` has no helper for.

```c
uint64_t link = botw_player_actor(api);
if (link) api->log("mymod: Link's actor is at %#llx", (unsigned long long)link);
```

- Address: the pointer at `0x7001e13ed0` (`BOTW_ADDR_PLAYER_INFO`); the actor at `+0x60` (`BOTW_PI_PLAYER`).
- The game's own function at `0x7000cc6fd0` (`BOTW_ADDR_GET_PLAYER`) returns the same actor; call it with
  `api->guest_call` on a game thread if you prefer.

### botw_player_pos and botw_in_world

`botw_player_pos(api, out)` puts Link's position into `out[0]`, `out[1]`, `out[2]`: x, y and z in metres, where y is
the height. It returns false, and 0 0 0, before Link is in the world: the title screen reads 0 0 0.
`botw_in_world(api)` is true when a save is loaded and Link is in the world.

```c
float pos[3];
if (botw_player_pos(api, pos)) api->log("mymod: Link is at %.1f %.1f %.1f", pos[0], pos[1], pos[2]);
```

- Address: the player info block `+0x88` (`BOTW_PI_POS`), 3 floats. This is a copy the game writes each frame:
  writing it moves nothing. To move Link, use `botw_teleport_up`.

### botw_stamina_get and botw_stamina_set

`botw_stamina_get(api)` gives Link's stamina: 1000 is one wheel (`BOTW_STAMINA_WHEEL`). That is not the most he can
have: a save with more wheels holds more, and nothing in `botw.h` knows his maximum yet. It returns -1 when Link isn't
in the world (the title screen too). `botw_stamina_set(api, value)` sets it and returns false when Link isn't in the
world. The game lowers stamina again while Link runs or climbs: to hold it, set it every frame (see
[infinite stamina](#example-2-infinite-stamina)).

```c
if (botw_stamina_get(api) < 300) botw_stamina_set(api, BOTW_STAMINA_WHEEL);   // low: fill one wheel
```

Don't set `BOTW_STAMINA_WHEEL` all the time: with two or three wheels that cuts stamina down to one wheel. To keep
stamina where it is, remember the value you read and put that back, as the infinite stamina example does.

- Address: the player info block `+0xa4` (`BOTW_PI_STAMINA`), a float.
- The other way, with a hook on the game's stamina setter `0x7000cc7410` (`BOTW_ADDR_STAMINA_SETTER`): see the
  `stamina_hook` example and [hooks](hooks.md). It needs game code built with a hook slot.

### botw_teleport_up and botw_player_real_pos

`botw_teleport_up(api, metres)` moves Link straight up by that many metres. It returns false when his real position
isn't found: no world, riding, a cutscene or loading. While Link climbs (hangs on a wall) it returns true, but he
doesn't move: the game keeps him on the wall, and a mod can't tell. Let him drop off the wall (B) and land, then it
works. Stand still first. A high fall hurts: open the paraglider.
A negative number moves him down. Only do that while he is in the air: on the ground it can put him inside it, and he
can fall through the world. Indoors or in a shrine, moving up can put him inside the ceiling.

```c
if (!botw_teleport_up(api, 10.0f)) api->log("mymod: can't move Link now");
```

`botw_player_real_pos(api)` gives the address of Link's real position (3 floats x y z), or 0. Writing it moves him
(the height since 2026-11-06, x and z since 2026-11-07), except while he climbs. It is reached
through a chain of pointers from the player info block: `+0x60` (his actor), `+0x578` (its physics), `+0x80`,
`+0x410`, then the position at `+0x1c0`, with the height at `+0x1c4` (`BOTW_POS_STEP_A`, `BOTW_POS_STEP_B`,
`BOTW_POS_XYZ`). Savage Wind's own `teleport` command tries this chain first. Like that command, the helper only accepts
the result when it is within 1 m of the position copy across the ground, so a stale chain is never written.

### botw_teleport

`botw_teleport(api, x, y, z)` writes all three values of Link's real position. Like `botw_teleport_up` it returns
false when his real position isn't found, and while he climbs it returns true but he stays on the wall. Give a height
a little above the ground there: he falls the rest of the way.

```c
if (!botw_teleport(api, -1035.0f, 253.5f, 1804.0f)) api->log("mymod: can't move Link now");
```

- Checked in game 2026-11-07: from (-1062.5, 249.0, 1794.6) to (-1035, 253.5, 1804), about 29 m. The next frame read
  (-1035.0, 253.4, 1804.0), then he dropped 2.1 m onto the grass and stayed there. Only short moves like this were
  tried. For a far place use Savage Wind's developer command `teleport x y z`, which shows its progress
  (`teleport status`).
- On a wall (checked in game 2026-11-07): six writes with `botw_teleport` and `botw_teleport_up` all returned true
  and none moved him; the game kept running. After he let go (B) and landed, `botw_teleport_up` moved him again.
  Savage Wind's `teleport` doesn't move him on a wall either: it ends with `failed`, and in one of two tries the game
  crashed while its status said `scanning`. Don't use it while Link climbs.

### botw_on_frame and botw_frames

`botw_on_frame(api, fn)` runs `fn` once per frame, on a game thread, after the game's own frame work. Call it from
`swmod_init`. `botw_frames()` says how many frames `fn` has run.

```c
static float hold = -1;   // the stamina to keep (-1: none yet)
static void every_frame(struct Cpu *c) {
    (void)c;
    float now = botw_stamina_get(api);                // -1 when Link isn't in the world
    if (now < 0 || now > hold) hold = now;            // no world, or it went up: keep the new value
    else if (now < hold) botw_stamina_set(api, hold); // it went down: put it back
}
// in swmod_init:
if (!botw_on_frame(api, every_frame)) return 3;
```

- How: it wraps the game's per-frame job at `0x7001129af8` (`BOTW_ADDR_FRAME_JOB`) with `api->hle_override`, as the
  ghost example does. No game-code rebuild is needed. `fn` runs on one of three worker threads; it was never seen
  running twice at once, and the wrapper skips a frame rather than run it twice. The wrapper puts the game's
  registers back after `fn`, so `fn` may use `api->guest_call`.
- **It returns true even when `fn` will never run**, because modhost can't tell the mod. Check `botw_frames()`: if it
  stays 0 while Link is in the world, `fn` isn't running.
- **Load the mod when the game starts** (in `mods\native`, with `SWMOD_DIR`, or with the mod manager), not with
  `modload` or the mod editor's F6 while the game runs. Savage Wind's threads remember where each function is once
  they have called it, so a mod loaded into a game that is already running may never get frames: restart the game
  with the mod installed.
- **Several mods can use it.** Since modhost 2026-11-22 the overrides of the mods loaded at game start chain
  ([override chains](code-mods.md#two-mods-on-one-function-override-chains)): every mod that uses `botw_on_frame`, and
  the ghost example (which wraps the same job), gets its frames, once per frame each. Before that only the first by
  file name ran. A mod loaded while the game runs (`modload`) can't join the chain and gets no frames.
- **One `.c` file.** Each `.c` file that includes `botw.h` gets its own copy of the frame wrapper. Call `botw_on_frame`
  and `botw_frames` in the same file.

### botw_time_of_day

`botw_time_of_day(api)` gives the hour of the day, 0 to 24 (13.5 is half past one in the afternoon), or -1 before the
world exists.

```c
float hour = botw_time_of_day(api);
if (hour >= 0) api->log("mymod: it is %.2f o'clock", hour);
```

- How: Savage Wind's named site `world_mgr` (found with `api->find_site`) points at the world object. Its manager list
  at `+0x5c8` (`BOTW_WORLD_MANAGERS`, count at `+0x5c0`, `BOTW_WORLD_MANAGER_COUNT`) starts with the clock, which
  keeps the time at `+0xb8` (`BOTW_CLOCK_TIME`) as 0 to 360 for 24 hours. This is how Savage Wind's own `state`
  command reads the hour. (`+0xb8` in the address table is another object's offset: the same number means other
  things in other objects.)
- Checked in game 2026-11-07: it read 11.0 at the test save's 11:00, then followed Savage Wind's `time set` to 6, 7.5,
  18 and 23.75 (each read a second later: 6.017, 7.517, 18.017, 23.767, the clock running on), the same as `time` and
  `state` and the game's own clock on the screen (11:45 PM at 23.75).

### botw_paused

`botw_paused(api)` is true while the pause menu is open, and false while playing (or when it can't tell).

```c
if (botw_paused(api)) return;   // don't change anything while the game is paused
```

- How: Savage Wind's named site `pause_state` is a 32-bit number, 0 while playing (2 in the pause menu or the map).
- Checked in game 2026-11-07: false (0) while playing; true (2) half a second after `+` opened the pause menu, for as
  long as it was open; false again half a second after `+` closed it.
- **The map is not checked.** On the test save (the very start of the game, with no map on the screen yet) `-`
  doesn't open the map: pressed for 3 and 10 frames and held for a second, it stayed 0 and the screen didn't change.
  Savage Wind says the map counts as paused too; a save that can open the map is needed to check it.

## Addresses and offsets in botw.h

| Name | Value | What it is |
|---|---|---|
| `BOTW_ADDR_MAIN` | `0x7000000000` | Start of the game's main code: `main+0x...` plus this is an address |
| `BOTW_ADDR_HEAP` | `0x7240000000` | Start of the game's heap (only a safety check for pointers) |
| `BOTW_ADDR_GUEST_END` | `0x7320000000` | End of the game's memory (only a safety check for pointers) |
| `BOTW_ADDR_PLAYER_INFO` | `0x7001e13ed0` | The pointer to the player info block |
| `BOTW_ADDR_GET_PLAYER` | `0x7000cc6fd0` | Game function: player info block in, Link's actor out |
| `BOTW_ADDR_STAMINA_SETTER` | `0x7000cc7410` | Game function that sets stamina (for hooks) |
| `BOTW_ADDR_FRAME_JOB` | `0x7001129af8` | Game job that runs once per frame |
| `BOTW_PI_PLAYER` | `+0x60` | Player info block: Link's actor |
| `BOTW_PI_POS` | `+0x88` | Player info block: copy of Link's position (3 floats) |
| `BOTW_PI_STAMINA` | `+0xa4` | Player info block: stamina (float) |
| `BOTW_ACTOR_PHYSICS` | `+0x578` | Actor: its physics (0 = none) |
| `BOTW_ACTOR_MATRIX` | `+0x398` | Actor: its 3x4 matrix, row by row |
| `BOTW_POS_STEP_A` | `+0x80` | Real position chain: in Link's physics |
| `BOTW_POS_STEP_B` | `+0x410` | Real position chain: the next object |
| `BOTW_POS_XYZ` | `+0x1c0` | Real position chain: Link's real position (3 floats; the height at `+0x1c4`) |
| `BOTW_WORLD_MANAGER_COUNT` | `+0x5c0` | World object: how many managers |
| `BOTW_WORLD_MANAGERS` | `+0x5c8` | World object: its manager list, the clock first |
| `BOTW_CLOCK_TIME` | `+0xb8` | Clock: the time of day, 0 to 360 |
| `BOTW_STAMINA_WHEEL` | `1000` | Stamina of one wheel (not the most Link can have) |

## The example mods

Each one is a folder in `native/examples` with its `.c` file and a `mod.toml`. Build all of them (PowerShell, from the
repo folder):

```
python tools\build_native.py build
```

They go to `build\native\basics-mods\`. To use one, do one of these:

- Copy its DLL into `<data root>\mods\native\` (where Savage Wind keeps its data), then start the game with modhost.
- Start the game with `$env:SWMOD_DIR = "$PWD\build\native\basics-mods"` and modhost, as [ghost](ghost.md#run-it)
  shows for the ghost example.
- Load it into the running game with the control command `modload <full path to the DLL>` (the mod editor does this
  for you). This works for show position and teleport up. Infinite stamina must be loaded when the game starts.

Type the commands in the in-game console (F12, or the key left of 1). `where` works without developer mode;
`infstamina` and `up` need it (`SAVAGEWIND_CONTROL=<port>` or `SAVAGEWIND_DEV=1`).

### Example 1: show position

The command `where` says where Link is. It only reads, so it is a player command: it works without developer mode.

```c
// basics_show_pos: the command `where` says where Link is (example mod for BotW 1.9.0, docs/game-basics.md).
// Type `where` in the in-game console (F12). It only reads, so it works without developer mode.
#include "swmod.h"
#include "botw.h"

static const SwModApi *api;

static void cmd_where(int argc, char **argv, SwModReply *r) {
    (void)argc;
    (void)argv;
    float pos[3];
    if (!botw_player_pos(api, pos)) {
        api->reply_fail(r, "Link is not in the world yet: load a save first");
        return;
    }
    api->reply_num(r, "x", pos[0]);
    api->reply_num(r, "y", pos[1]);
    api->reply_num(r, "z", pos[2]);
    api->reply_text(r, "Link is at x %.1f, y %.1f (height), z %.1f", pos[0], pos[1], pos[2]);
}

static const SwModCommand commands[] = {
    {"where", "", "Where Link is (example mod)", cmd_where, SWMOD_CMD_PLAYER},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    if (!botw_version_ok(api)) {
        api->log("basics_show_pos: made for game version %s, this is %s", BOTW_GAME_VERSION, api->game_version);
        return 2;
    }
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("basics_show_pos: ready (type where in the console)");
    return 0;
}
```

### Example 2: infinite stamina

Stamina never goes down. Every frame the mod compares stamina with the highest value it has seen and puts that back
when it dropped, so it works with any number of wheels. It starts again from the current stamina whenever Link's
actor changes (another save was loaded, or the game reloaded after Game Over), so a save with fewer wheels never gets
the old value. `infstamina` shows the state; `infstamina off` and `infstamina on` switch it. No game-code rebuild is
needed, but the mod must load when the game starts, and not together with the ghost example (see
[botw_on_frame](#botw_on_frame-and-botw_frames)). If `infstamina` says it never ran, that is why.

In game (2026-11-07): sprinting 3.5 s from a full wheel, stamina read 990 the whole time (the game takes one frame's
worth before the mod puts it back) and the wheel on the screen stayed full. With `infstamina off`, the same sprint
emptied it: 1000 to 0 in 3.5 s. While Link climbed, it held too.

```c
// basics_inf_stamina: stamina never goes down (example mod for BotW 1.9.0, docs/game-basics.md).
// Every frame it compares stamina with the highest value it has seen and puts that back when stamina dropped.
// No game-code rebuild needed. Install it so it loads when the game starts (botw_on_frame needs that; modload won't do).
// Command (developer mode): `infstamina` shows the state, `infstamina off` / `infstamina on` switches it.
#include "swmod.h"
#include "botw.h"

static const SwModApi *api;
static int g_on = 1;        // switched by the command, read every frame
static float g_hold = -1;   // the stamina to hold (-1: none yet); only every_frame uses these two
static uint64_t g_link;     // Link's actor when g_hold was taken: another one means another save was loaded

static void every_frame(struct Cpu *c) {
    (void)c;
    uint64_t link = botw_player_actor(api);
    if (!__atomic_load_n(&g_on, __ATOMIC_RELAXED) || !botw_in_world(api) || link != g_link) {
        g_hold = -1;  // (start again from the current stamina: a loaded save may have fewer wheels)
        g_link = link;
        return;
    }
    float now = botw_stamina_get(api);
    if (now > g_hold) g_hold = now;                        // it went up (resting, a new wheel): hold that
    else if (now < g_hold) botw_stamina_set(api, g_hold);  // it went down: put it back
}

static void cmd_infstamina(int argc, char **argv, SwModReply *r) {
    if (argc > 2 || (argc == 2 && strcmp(argv[1], "on") != 0 && strcmp(argv[1], "off") != 0)) {
        api->reply_fail(r, "usage: infstamina [on|off]");
        return;
    }
    if (argc == 2) __atomic_store_n(&g_on, strcmp(argv[1], "on") == 0, __ATOMIC_RELAXED);
    bool on = __atomic_load_n(&g_on, __ATOMIC_RELAXED);
    int64_t frames = botw_frames();
    api->reply_bool(r, "on", on);
    api->reply_num(r, "stamina", botw_stamina_get(api));
    api->reply_int(r, "frames", frames);
    if (frames == 0 && botw_in_world(api))
        api->reply_text(r, "infstamina %s, but it never ran: install the mod so it loads when the game starts (not "
                           "with modload), and not together with the ghost mod. Then restart the game.",
                        on ? "on" : "off");
    else if (frames == 0)
        api->reply_text(r, "infstamina %s. It starts once a save is loaded.", on ? "on" : "off");
    else
        api->reply_text(r, "infstamina %s (stamina %.0f)", on ? "on" : "off", botw_stamina_get(api));
}

static const SwModCommand commands[] = {
    {"infstamina", "[on|off]", "Infinite stamina (example mod): state, or switch it", cmd_infstamina, 0},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    if (!botw_version_ok(api)) {
        api->log("basics_inf_stamina: made for game version %s, this is %s", BOTW_GAME_VERSION, api->game_version);
        return 2;
    }
    if (!botw_on_frame(api, every_frame)) {
        api->log("basics_inf_stamina: can't run every frame (the frame job isn't in this game code)");
        return 3;
    }
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("basics_inf_stamina: ready if it loaded with the game (type infstamina in the console to check)");
    return 0;
}
```

### Example 3: teleport up

The command `up` moves Link 10 m straight up; `up 30` moves him 30 m (up to 1000). It only moves up: moving down
while Link stands on the ground can put him inside it. Indoors or in a shrine, moving up can put him inside the
ceiling, so use it outside. It moves Link, so it is a developer command. Stand still first, and open the paraglider:
a high fall hurts. While Link climbs it answers "Moved Link up", but he stays on the wall: let go first (see
[botw_teleport_up](#botw_teleport_up-and-botw_player_real_pos)).

In game (2026-11-07, loaded with `modload`): `up 10` moved Link from height 249.0 to 259.0, and he fell back down
unhurt.

```c
// basics_teleport_up: the command `up [metres]` moves Link straight up (example mod for BotW 1.9.0,
// docs/game-basics.md). `up` = 10 m, `up 30` = 30 m (up to 1000). A high fall hurts: open the paraglider.
// Developer mode only (it moves Link): start the game with SAVAGEWIND_CONTROL=<port> or SAVAGEWIND_DEV=1.
// Stand still first; it does nothing while riding, in a cutscene or while loading. It only moves up: moving down
// while Link stands on the ground can put him inside it, and indoors moving up can put him inside the ceiling.
#include <stdlib.h>

#include "swmod.h"
#include "botw.h"

static const SwModApi *api;

static void cmd_up(int argc, char **argv, SwModReply *r) {
    float metres = 10.0f;
    if (argc == 2) {
        char *end;
        double d = strtod(argv[1], &end);
        if (end == argv[1] || *end || !(d > 0.0 && d <= 1000.0)) {
            api->reply_fail(r, "usage: up [metres], more than 0 and up to 1000 (it only moves up)");
            return;
        }
        metres = (float)d;
    } else if (argc > 2) {
        api->reply_fail(r, "usage: up [metres]");
        return;
    }
    float before[3];
    if (!botw_player_pos(api, before)) {
        api->reply_fail(r, "Link is not in the world yet: load a save first");
        return;
    }
    if (!botw_teleport_up(api, metres)) {
        api->reply_fail(r, "Can't move Link now (riding, a cutscene or loading?)");
        return;
    }
    api->reply_num(r, "metres", metres);
    api->reply_num(r, "from_y", before[1]);
    api->reply_num(r, "to_y", before[1] + metres);
    api->reply_text(r, "Moved Link up %.1f m (height %.1f to %.1f)", metres, before[1], before[1] + metres);
}

static const SwModCommand commands[] = {
    {"up", "[metres]", "Move Link straight up (example mod; default 10 m)", cmd_up, 0},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    if (!botw_version_ok(api)) {
        api->log("basics_teleport_up: made for game version %s, this is %s", BOTW_GAME_VERSION, api->game_version);
        return 2;
    }
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("basics_teleport_up: ready (type up in the console, developer mode)");
    return 0;
}
```

## How each value was checked

| Value | Checked how | When |
|---|---|---|
| Player info pointer `0x7001e13ed0` | The ghost mod reads Link's position through it (`ghost follow` stayed 3 m ahead of Link while he ran) | in game 2026-11-07 |
| Position copy `+0x88` | The same ghost test; it reads 0 0 0 on the title screen | in game 2026-11-07 |
| Link's actor `+0x60`, `0x7000cc6fd0` | The block's `+0x60` equalled what the game function returns, 37 of 37 checks | in game 2026-11-07 |
| Stamina `+0xa4` | A cheat that writes 1000 there 12 times a second kept stamina full while sprinting; the `stamina_hook` mod compares with it | in game 2026-11-06 |
| Stamina setter `0x7000cc7410` | The `stamina_hook` mod's hook kept stamina at 1000 while sprinting | in game 2026-11-06 and 07 |
| Real position chain `+0x60`, `+0x578`, `+0x80`, `+0x410`, `+0x1c0` | A cheat that writes the height (`+0x1c4`) through this chain held Link in the air and let go when stopped ([cheats](cheats.md)). Savage Wind's own `teleport` tries the same chain first. Only the height was written | in game 2026-11-06 |
| Frame job `0x7001129af8` | The ghost mod wraps it: 50-60 calls a second, on 3 worker threads, never two at once | in game 2026-11-07 |
| Actor physics `+0x578`, matrix `+0x398` | The ghost mod: set on actors with physics, 0 on the crate; positions read back as set | in game 2026-11-07 |
| Real position x and z (`+0x1c0`, `+0x1c8`) | `botw_teleport` moved Link about 29 m to the point it wrote; while he climbed, six writes did nothing (no crash), and after he let go it worked again | in game 2026-11-07 |
| Hour of the day (`world_mgr`, `+0x5c0`, `+0x5c8`, `+0xb8`) | `botw_time_of_day` read the same hour as Savage Wind's `time` at 11, 6, 7.5, 18 and 23.75 (set with `time set`). The three offsets are in [addresses](addresses.md) | in game 2026-11-07 |
| Pause state (`pause_state`) | `botw_paused`: 0 while playing, 2 while the pause menu was open (the map doesn't open on the test save, so it isn't checked) | in game 2026-11-07 |

The cheats and the ghost mod wrote and read these values from Savage Wind's frame thread and from a game thread, the
same places the helpers run. None of the values above comes from copied game code: they were found by our own research
on a 1.9.0 `main`, by Savage Wind's own tools (the position chain and the two named sites) and by tests in the game
([addresses](addresses.md)).
