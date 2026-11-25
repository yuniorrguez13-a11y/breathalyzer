// botw.h - the most basic game values of BotW 1.9.0 for native mods, with our own plain names.
//
//     #include "swmod.h"
//     #include "botw.h"
//
//     float pos[3];
//     if (botw_player_pos(api, pos)) api->log("mymod: Link is at %.1f %.1f %.1f", pos[0], pos[1], pos[2]);
//
// Every helper takes the api your swmod_init got. It checks that the game is version 1.9.0 (the version these
// addresses are for), and it follows a pointer only when it points into the game's heap, where the player, actors and
// their parts live; when anything is missing it returns 0, -1 or false and touches nothing. That check stops almost
// every stale or bad pointer, but it is not a guarantee: call the helpers while the game runs (in a command or a
// botw_on_frame function), not from swmod_init at game start, when the game isn't in memory yet. Guest addresses are
// host addresses in Savage Wind, so the helpers read and write the game's memory directly: they are cheap enough to
// call every frame.
//
// Docs, a small example for each helper and three full example mods: docs/game-basics.md. Every address, with how
// it was checked: docs/addresses.md. Each symbol below has its doc on the line just above it, starting with "//:"
// (the mod editor shows that line). "(not tested in game)" marks a value or helper that nobody has checked in the
// game yet; the others are built only on addresses that were checked in the game ("(checked in game <date>)" on the
// newest ones).
//
// The real position chain and its 1 m check, botw_time_of_day and botw_paused read the game the way Savage Wind's own
// runtime does (runtime/control_cells.c, runtime/control_tp.c, runtime/services/test_warp.c, runtime/game_sites.h;
// MIT License, Copyright (c) 2026 Revivedx and the Savage Wind Recomp contributors). No game code is in this file.
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "swmod.h"

#ifdef __cplusplus
extern "C" {
#endif

// ------------------------------------------------------------------ addresses and offsets (docs/addresses.md)

//: The game version these addresses are for. On any other version every helper does nothing.
#define BOTW_GAME_VERSION "1.9.0"
//: Start of the game's main code in memory: an offset main+0x... plus this is an address.
#define BOTW_ADDR_MAIN 0x7000000000ull
//: Start of the game's heap (Savage Wind's memory layout): the player, actors and their parts are here.
#define BOTW_ADDR_HEAP 0x7240000000ull
//: End of the game's memory (Savage Wind's memory layout): no game pointer is at or above this.
#define BOTW_ADDR_GUEST_END 0x7320000000ull
//: Where the game keeps the pointer to the player info block (u64; read it with botw_player_info).
#define BOTW_ADDR_PLAYER_INFO 0x7001e13ed0ull
//: Game function: player info block in x0, returns Link's actor (the same as botw_player_actor). Call it with api->guest_call on a game thread.
#define BOTW_ADDR_GET_PLAYER 0x7000cc6fd0ull
//: Game function that sets stamina (x0 = player info, s0 = the new value). Hook it with api->hook (needs game code built with a hook slot).
#define BOTW_ADDR_STAMINA_SETTER 0x7000cc7410ull
//: Game job that runs once per frame on a game thread; botw_on_frame wraps it.
#define BOTW_ADDR_FRAME_JOB 0x7001129af8ull
//: (not tested in game) Game function: open a file (sead doOpen_, nn::fs devices). x0 device, x2 path (char* at +8), w3 mode; x0 = device, or 0.
#define BOTW_ADDR_FILE_OPEN 0x7000e8fae0ull
//: (not tested in game) The same open of another file device class (maybe the one for TitleBG.pack). Same registers.
#define BOTW_ADDR_FILE_OPEN_2 0x7000e92690ull
//: (not tested in game) The same open of a third file device class. Same registers.
#define BOTW_ADDR_FILE_OPEN_3 0x700115cba0ull
//: (not tested in game) The same open of a fourth file device class. Same registers.
#define BOTW_ADDR_FILE_OPEN_4 0x7000eaba98ull

//: Offset in the player info block: Link's actor (u64).
#define BOTW_PI_PLAYER 0x60
//: Offset in the player info block: a copy of Link's position, 3 floats x y z (read only: writing it moves nothing).
#define BOTW_PI_POS 0x88
//: Offset in the player info block: stamina (float; 1000 = one wheel).
#define BOTW_PI_STAMINA 0xa4
//: Offset in an actor: its physics (u64; 0 = no physics).
#define BOTW_ACTOR_PHYSICS 0x578
//: Offset in an actor: its 3x4 matrix, row by row (position at +0x3a4, +0x3b4, +0x3c4).
#define BOTW_ACTOR_MATRIX 0x398
//: Real position chain, third step: offset in Link's physics (u64).
#define BOTW_POS_STEP_A 0x80
//: Real position chain, fourth step: offset in the object BOTW_POS_STEP_A points at (u64).
#define BOTW_POS_STEP_B 0x410
//: Real position chain, last step: Link's real position, 3 floats x y z (height at +0x1c4). Writing it moved him in game, except while he climbs.
#define BOTW_POS_XYZ 0x1c0
//: Offset in the world object (site world_mgr): how many managers its list has (u32).
#define BOTW_WORLD_MANAGER_COUNT 0x5c0
//: Offset in the world object (site world_mgr): its manager list (u64); the first one is the clock.
#define BOTW_WORLD_MANAGERS 0x5c8
//: Offset in the clock: the time of day, 0 to 360 for 24 hours (float).
#define BOTW_CLOCK_TIME 0xb8

//: Stamina of one wheel (1000). Not the most Link can have: a save with more wheels holds more.
#define BOTW_STAMINA_WHEEL 1000.0f

// ------------------------------------------------------------------ reading and writing game memory

//: (internal) True when a is a game address to read or write: inside the game's memory and 4-byte aligned.
static inline bool botw__addr_ok(uint64_t a) {
    return a >= BOTW_ADDR_MAIN && a < BOTW_ADDR_GUEST_END && (a & 3) == 0;
}
//: (internal) True when p looks like a pointer to a game object: inside the game's heap and 8-byte aligned.
static inline bool botw__obj_ok(uint64_t p) {
    return p >= BOTW_ADDR_HEAP && p < BOTW_ADDR_GUEST_END && (p & 7) == 0;
}
//: (internal) The u64 at game address a, or 0 when a is not a game address.
static inline uint64_t botw__rd64(uint64_t a) {
    return botw__addr_ok(a) && (a & 7) == 0 ? *(volatile const uint64_t *)(uintptr_t)a : 0;
}
//: (internal) The pointer at game address a when it points at a game object in the heap, else 0.
static inline uint64_t botw__ptr(uint64_t a) {
    uint64_t p = botw__rd64(a);
    return botw__obj_ok(p) ? p : 0;
}
//: (internal) The u32 at game address a, or 0 when a is not a game address.
static inline uint32_t botw__rd32(uint64_t a) {
    return botw__addr_ok(a) ? *(volatile const uint32_t *)(uintptr_t)a : 0;
}
//: (internal) The float at game address a, or 0 when a is not a game address.
static inline float botw__rdf(uint64_t a) {
    return botw__addr_ok(a) ? *(volatile const float *)(uintptr_t)a : 0.0f;
}
//: (internal) Write a float at game address a. False when a is not a game address.
static inline bool botw__wrf(uint64_t a, float v) {
    if (!botw__addr_ok(a)) return false;
    *(volatile float *)(uintptr_t)a = v;
    return true;
}
//: (internal) True for a normal number (not NaN, not infinite).
static inline bool botw__finite(float v) {
    return v == v && v - v == 0.0f;
}

// ------------------------------------------------------------------ the game and the player

//: True when the game is version 1.9.0, the version these addresses are for. Every helper checks it.
static inline bool botw_version_ok(const SwModApi *api) {
    return api && api->game_version && strcmp(api->game_version, BOTW_GAME_VERSION) == 0;
}

//: The player info block's address, or 0 before the game made one (or on another game version).
static inline uint64_t botw_player_info(const SwModApi *api) {
    if (!botw_version_ok(api)) return 0;
    return botw__ptr(BOTW_ADDR_PLAYER_INFO);
}

//: Link's actor (the player info block's +0x60), or 0 when there is none.
static inline uint64_t botw_player_actor(const SwModApi *api) {
    uint64_t pi = botw_player_info(api);
    return pi ? botw__ptr(pi + BOTW_PI_PLAYER) : 0;
}

//: Link's position x y z in metres (y is the height) into out[3]. False, and 0 0 0, before he is in the world.
static inline bool botw_player_pos(const SwModApi *api, float out[3]) {
    if (!out) return false;
    out[0] = out[1] = out[2] = 0.0f;
    uint64_t pi = botw_player_info(api);
    if (!pi) return false;
    float x = botw__rdf(pi + BOTW_PI_POS), y = botw__rdf(pi + BOTW_PI_POS + 4), z = botw__rdf(pi + BOTW_PI_POS + 8);
    if (!botw__finite(x) || !botw__finite(y) || !botw__finite(z)) return false;
    if (x == 0.0f && y == 0.0f && z == 0.0f) return false;  // (the title screen reads 0 0 0)
    out[0] = x, out[1] = y, out[2] = z;
    return true;
}

//: True when a save is loaded and Link is in the world (not the title screen).
static inline bool botw_in_world(const SwModApi *api) {
    float pos[3];
    return botw_player_pos(api, pos);
}

//: Link's stamina (1000 = one wheel), or -1 when he isn't in the world (the title screen too).
static inline float botw_stamina_get(const SwModApi *api) {
    if (!botw_in_world(api)) return -1.0f;
    return botw__rdf(botw_player_info(api) + BOTW_PI_STAMINA);
}

//: Set Link's stamina (1000 = one wheel). False when he isn't in the world. The game changes it again as he runs: set it every frame to hold it.
static inline bool botw_stamina_set(const SwModApi *api, float value) {
    if (!botw__finite(value) || value < 0.0f || !botw_in_world(api)) return false;
    return botw__wrf(botw_player_info(api) + BOTW_PI_STAMINA, value);
}

// ------------------------------------------------------------------ moving Link

//: Address of Link's real position x y z, or 0 when not found (no world, riding, a cutscene, loading). Writing it moves him, but not while he climbs.
static inline uint64_t botw_player_real_pos(const SwModApi *api) {
    float copy[3];
    if (!botw_player_pos(api, copy)) return 0;
    uint64_t p = botw_player_actor(api);           // player info +0x60
    p = p ? botw__ptr(p + BOTW_ACTOR_PHYSICS) : 0;  // +0x578
    p = p ? botw__ptr(p + BOTW_POS_STEP_A) : 0;     // +0x80
    p = p ? botw__ptr(p + BOTW_POS_STEP_B) : 0;     // +0x410
    if (!p) return 0;
    uint64_t xyz = p + BOTW_POS_XYZ;                // +0x1c0 (the height at +0x1c4)
    if (!botw__addr_ok(xyz)) return 0;
    // Only when it is really Link's: within 1 m of the position copy across the ground (as Savage Wind checks it).
    float dx = botw__rdf(xyz) - copy[0], dz = botw__rdf(xyz + 8) - copy[2];
    if (!(dx <= 1.0f && dx >= -1.0f && dz <= 1.0f && dz >= -1.0f)) return 0;
    return xyz;
}

//: Move Link up this many metres (negative: down, only in the air). False when his real position isn't found; true but no move while he climbs (let go first). A high fall hurts.
static inline bool botw_teleport_up(const SwModApi *api, float metres) {
    if (!botw__finite(metres)) return false;
    uint64_t xyz = botw_player_real_pos(api);
    if (!xyz) return false;
    return botw__wrf(xyz + 4, botw__rdf(xyz + 4) + metres);
}

//: (checked in game 2026-11-07) Move Link to x y z (metres; y is the height), a short way (30 m tried). False when his real position isn't found; true but no move while he climbs.
static inline bool botw_teleport(const SwModApi *api, float x, float y, float z) {
    if (!botw__finite(x) || !botw__finite(y) || !botw__finite(z)) return false;
    uint64_t xyz = botw_player_real_pos(api);
    if (!xyz) return false;
    return botw__wrf(xyz, x) && botw__wrf(xyz + 4, y) && botw__wrf(xyz + 8, z);
}

// ------------------------------------------------------------------ the world

//: (checked in game 2026-11-07) The hour of the day, 0 to 24 (13.5 = half past one in the afternoon), or -1 before the world exists.
static inline float botw_time_of_day(const SwModApi *api) {
    if (!botw_version_ok(api) || !api->find_site) return -1.0f;
    uint64_t world = botw__ptr(api->find_site("world_mgr"));  // Savage Wind's site: the world object
    if (!world || !botw__rd32(world + BOTW_WORLD_MANAGER_COUNT)) return -1.0f;
    uint64_t list = botw__ptr(world + BOTW_WORLD_MANAGERS);
    uint64_t clock = list ? botw__ptr(list) : 0;              // the first manager: the clock
    if (!clock) return -1.0f;
    float t = botw__rdf(clock + BOTW_CLOCK_TIME);              // 0 to 360 for 24 hours
    return t >= 0.0f && t <= 360.0f ? t / 15.0f : -1.0f;
}

//: (checked in game 2026-11-07: the pause menu) True while the pause menu is open (Savage Wind says the map too; not tried), false while playing or when it can't tell.
static inline bool botw_paused(const SwModApi *api) {
    if (!botw_version_ok(api) || !api->find_site) return false;
    uint64_t site = api->find_site("pause_state");  // Savage Wind's site: a u32, 0 while playing
    return site && botw__rd32(site) != 0;
}

// ------------------------------------------------------------------ running code every frame

//: A function botw_on_frame runs every frame. c is the game thread's CPU (for api->guest_call).
typedef void (*BotwFrameFn)(struct Cpu *c);

//: (internal) The game's own frame job, kept by botw_on_frame (one copy per .c file: use botw_on_frame in one file).
static SwModGuestFn botw__frame_orig;
//: (internal) The function botw_on_frame runs.
static BotwFrameFn botw__frame_fn;
//: (internal) How many frames that function has run.
static int64_t botw__frame_count;
//: (internal) 1 while that function runs: a second thread that comes meanwhile skips it.
static int botw__frame_busy;

//: (internal) Runs instead of the game's frame job: the game's job first, then your function, with the game's registers put back.
static inline void botw__frame_job(struct Cpu *c) {
    botw__frame_orig(c);
    BotwFrameFn fn = __atomic_load_n(&botw__frame_fn, __ATOMIC_ACQUIRE);
    if (!fn || __atomic_exchange_n(&botw__frame_busy, 1, __ATOMIC_ACQUIRE)) return;  // (never two at once)
    SwModRegs *r = SWMOD_REGS(c);
    uint64_t x[31], sp = r->sp, pc = r->pc, nzcv = r->nzcv;
    SwModVReg v[32];
    memcpy(x, r->x, sizeof x);
    memcpy(v, r->v, sizeof v);
    fn(c);
    memcpy(r->x, x, sizeof x);
    memcpy(r->v, v, sizeof v);
    r->sp = sp, r->pc = pc, r->nzcv = nzcv;
    __atomic_fetch_add(&botw__frame_count, 1, __ATOMIC_RELAXED);
    __atomic_store_n(&botw__frame_busy, 0, __ATOMIC_RELEASE);
}

//: Run fn once per frame on a game thread. Only in swmod_init of a mod loaded at game start (not modload: it can't chain then); several mods chain, each once per frame; in one .c file.
static inline bool botw_on_frame(const SwModApi *api, BotwFrameFn fn) {
    if (!botw_version_ok(api) || !fn || !api->dispatch_lookup || !api->hle_override) return false;
    if (!botw__frame_orig) {
        SwModGuestFn orig = api->dispatch_lookup(BOTW_ADDR_FRAME_JOB);
        if (!orig) return false;
        botw__frame_orig = orig;
        api->hle_override(BOTW_ADDR_FRAME_JOB, botw__frame_job, "botw_on_frame");
    }
    __atomic_store_n(&botw__frame_fn, fn, __ATOMIC_RELEASE);
    return true;
}

//: How many frames botw_on_frame's function has run. If it stays 0 while Link is in the world, it isn't running: load the mod at game start. Same .c file only.
static inline int64_t botw_frames(void) {
    return __atomic_load_n(&botw__frame_count, __ATOMIC_RELAXED);
}

#ifdef __cplusplus
}
#endif
