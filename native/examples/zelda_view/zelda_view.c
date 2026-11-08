// zelda_view: "Link from Zelda's view", a hidden easter egg (example native mod for BotW 1.9.0,
// docs/easter-eggs.md). Plan: out/research/zelda-view-plan.md (with the research notes it names).
//
// Type linkfromzeldasview in the in-game console (F12): Link gets extremely buff, swings heavy weapons (claymores)
// as fast as one-handed swords, and carries Zelda piggyback. Type it again: all of it goes away. It starts OFF at
// every game start and stays on through reloads. `linkfromzeldasview on|off|status` work too (status adds the test
// fields in developer mode). It is registered with an empty help line; Savage Wind's `help` still lists every
// command a player may run, so it is hidden only from what this repo writes.
//
// The parts, each of which checks itself and switches off alone (the others keep working):
// - Extremely buff (needs hook 0x7000045d60; wider shoulders also 0x7000045d0c): only the copy of the bone matrices
//   the game sends for drawing is scaled (skin_replace). The game's own bones, physics, IK and held items never
//   change; his worn armor is scaled the same way, so it grows with the body. The wider shoulders move four bones'
//   local translation out 18 % before the game's world calc (world_before); off writes the rest values back.
// - Heavy weapons swing like one-handers (hook 0x70014a1d04 + the tick): while a two-handed weapon is equipped,
//   Link's move-set class (+0xd24, +0x1ce0) is kept at "one-handed" and his animation profile (parameter 0x2c) at
//   WeaponSmallSword. Changes wait while he attacks. Off puts both back. The weapon itself is never changed.
// - Zelda piggyback (the tick): an actor pack built on the player's PC by tools/zelda_view.py (her model, no physics,
//   no AI, the riding pose Ride) under the free name ZV_SLOT, glued to Link's Spine_2 bone with the game's own bone
//   follow; if the follow is refused or drifts, the tick moves her itself (plan B). She copies Link's visibility.
//   Off deletes her; a reload or Game Over makes the mod forget her and spawn her again.
//
// Threads: the command runs on Savage Wind's frame thread and only flips g_want. The tick (an after hook on the
// per-frame job 0x7001129af8, or botw_on_frame without hook slots) runs once per frame on a game worker thread and is
// the only place that calls game code; it saves and restores every guest register around itself. The three other
// hooks run on game threads, many times a frame: they only compare pointers with what the tick published (a seqlock)
// and do nothing at all while the egg is off. Every game pointer is range-checked (hooks) or checked with a memory
// query (tick) before it is read, and every class by its vtable.
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "swmod.h"
#include "botw.h"

#define ZV_VERSION "1.0.0"                 // (the same as mod.toml)
#define ZV_WORD "linkfromzeldasview"
#define ZV_SLOT "Dm663_Npc_Assasin_A"      // Zelda's actor name: a cutscene name with no pack in the game. The pack
#define ZV_SLOT_W L"Dm663_Npc_Assasin_A"   // tools/zelda_view.py builds (its SLOT; a unit test keeps them equal)

// ---- Win32 / C runtime (declared here: the zig that ships with Savage Wind has no windows.h or stdio.h)
__declspec(dllimport) unsigned long __stdcall GetCurrentThreadId(void);
__declspec(dllimport) int __stdcall QueryPerformanceCounter(int64_t *out);
__declspec(dllimport) int __stdcall QueryPerformanceFrequency(int64_t *out);
typedef struct {  // MEMORY_BASIC_INFORMATION (64-bit)
    uint64_t base, alloc_base;
    uint32_t alloc_protect;
    uint16_t partition, pad0;
    uint64_t size;
    uint32_t state, protect, type, pad1;
} MemInfo;
_Static_assert(sizeof(MemInfo) == 48, "MEMORY_BASIC_INFORMATION is 48 bytes");
__declspec(dllimport) size_t __stdcall VirtualQuery(const void *addr, MemInfo *info, size_t len);
__declspec(dllimport) unsigned long __stdcall GetEnvironmentVariableW(const wchar_t *name, wchar_t *buf,
                                                                      unsigned long n);
__declspec(dllimport) unsigned long __stdcall GetFileAttributesW(const wchar_t *path);
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wbuiltin-requires-header"
int snprintf(char *buf, size_t n, const char *fmt, ...);
int vsnprintf(char *buf, size_t n, const char *fmt, __builtin_va_list ap);
#pragma clang diagnostic pop

// ---- BotW 1.9.0 addresses (docs/addresses.md). New in this mod (research only until the game check):
#define SKIN_UPLOAD 0x7000045d60ull      // skeleton -> GPU skin matrices (x0 skeleton, w1 buffer index): replace hook
#define WORLD_CALC 0x7000045d0cull       // skeleton local pose -> world matrices (x0 skeleton): before hook
#define STR_SETTER 0x70014a1d04ull       // animation holder: set a string parameter (holder, index, string obj, 0)
#define STR_SMALL_SWORD 0x7001da3c70ull  // the game's static string object "WeaponSmallSword" (table of 6, 0x10 apart)
#define STR_LARGE_SWORD 0x7001da3c80ull  // ... "WeaponLargeSword"
#define VIS_COPY 0x70012132d0ull         // (actor, owner): copy the owner's visibility (armor calls it each frame)
#define EVENT_MGR_PTR 0x7001e3a638ull    // u64: the event manager; its EVENT_RUNNING is not 0 while an event runs
#define EVENT_RUNNING 0x1d2b8
#define ARMOR_VPTR 0x7001d282a8ull       // the armor manager class (Link +PLAYER_ARMOR)
#define PLAYER_ARMOR 0x23e0              // Link: armor manager (owner at +ARMOR_OWNER, 6 links from +ARMOR_LINKS)
#define ARMOR_OWNER 0x128
#define ARMOR_LINKS 0x10
#define ARMOR_LINK_SIZE 0x10
#define ARMOR_SLOTS 6
#define PLAYER_MOVESET 0xd24             // Link: s32 move-set class: 0 one-handed, 1 two-handed, 2 spear, -1 none
#define PLAYER_DRAWN 0x1ce0              // Link: s32 class at the last draw / sheathe
#define PLAYER_HAND 0xd38                // Link: char* hand state (None, Sword, Shield, Bow...)
#define ACTOR_HIDDEN 0x518               // actor: bit 5 set while a cutscene hides it (the visibility copy reads it)
#define ACTOR_OWN_FOLLOW 0x520           // actor: u32, bit 5 set = it owns its follow (the follow setter refuses)
#define WEAPON_TYPE 0xcf0                // Weapon: s32 type: 0 one-handed, 1 two-handed (the only two used here)
#define HOLDER_PARAMS 0xe0               // animation holder: s32 parameter count, then the array (+0xe8) and the
#define HOLDER_PARAM_ARRAY 0xe8          // index map (+0xf0, s8 per index, negative: none); a parameter's text is
#define HOLDER_PARAM_MAP 0xf0            // the char* at its +0x8
#define PARAM_WEAPON 0x2c                // the weapon profile parameter (WeaponSelector)
#define FOLLOW_PHYS 0x18                 // bone follow: u8, 1 = also move the follower's physics (ours: 0)
#define SKEL_FLAGS 0x08                  // skeleton: u16 flags (the upload sets bit 3)
#define SKEL_LOCAL 0x18                  // skeleton: local pose, 0x60 a bone (u32 flags +0x0, translation +0x50)
#define SKEL_NBONES 0x50                 // skeleton: u16 bone count
#define RES_BONES 0x18                   // skeleton resource: bones, 0x50 each (rest translation at +0x44)
// From the ghost mod (docs/addresses.md, used in a mod):
#define FRAME_JOB BOTW_ADDR_FRAME_JOB    // the per-frame job 0x7001129af8: after hook = the tick
#define PLAYER_INFO_PTR 0x7001e13ed0ull  // u64: the player info block (+0x60 Link, +0x88 position copy)
#define PI_PLAYER 0x60
#define PI_POS 0x88
#define PLAYER_VPTR 0x7001cd7bb8ull      // Link's actor class
#define CREATOR_PTR 0x7001e95430ull      // u64: the actor spawner (+0x59: busy, requests refused)
#define CREATOR_BLOCKED 0x59
#define HEAP_HOLDER_PTR 0x7001e14cd8ull  // u64: the scene object; +0x28 = the heap for runtime spawns
#define HEAP_OFF 0x28
#define REQUEST_CREATE 0x7001535b10ull   // (spawner, name, heap, handle, pack, 0, 1) -> bool
#define PACK_CLEAR 0x700112615cull       // (buf)
#define PACK_ADD 0x7001126168ull         // (buf, value*, string object* key, size, type)
#define PACK_VPTR 0x7001bc5540ull
#define STRING_OBJ_VPTR 0x7001bc32c8ull
#define KEY_M 0x700160f9e5ull            // "@M" in main's rodata
#define HANDLE_CLEAR 0x7001516814ull
#define HANDLE_READY 0x7001516820ull
#define HANDLE_FAILED 0x7001516840ull
#define HANDLE_PEEK 0x7001516a5cull
#define HANDLE_TAKE 0x7001516b74ull
#define HANDLE_RELEASE_FAILED 0x70015168a8ull
#define HANDLE_RELEASE 0x7001516908ull
#define ACTOR_TYPEINFO 0x700056b6d0ull   // () -> Actor type info; proc->vtable[2](proc, info) = isA
#define LINK_SET 0x7001517e60ull         // (link, proc, acquire)
#define LINK_VALID 0x7001517e0cull       // (link) -> bool
#define LINK_RESET 0x7001517f8cull       // (link)
#define SET_MTX 0x700152261cull          // (actor, 3x4 matrix*, 0)
#define SET_TRANSFORM 0x70015223f0ull    // (actor, pos*, rot33*, scale*) any may be 0
#define DELETE_LATER 0x7001514bacull     // (proc, 0): remove it at a safe point later
#define ACTOR_MTX 0x398                  // actor: 3x4 matrix, row by row
#define AS_PLAY 0x700151e42cull          // (action, name, keep_if_same, slot, sub; s0 morph)
#define AS_CURRENT 0x70014a0040ull       // (holder, slot, sub) -> string object* of the playing AS
#define ACTOR_AS 0x560                   // actor: animation holder (owner +0xd8, slot count +0xb8)
#define AS_OWNER 0xd8
#define AS_SLOTS 0xb8
#define AS_SLOT_ARRAY 0xc0               // holder: slots, 0x50 each: sub count +0x20, subs +0x28 (0x98 each: the
#define AS_SLOT_SIZE 0x50                // definition +0x0 whose name is the char* at +0x10, playing +0x18)
#define AS_SLOT_NSUB 0x20
#define AS_SLOT_SUBS 0x28
#define AS_SUB_SIZE 0x98
#define AS_SUB_LIVE 0x18
#define AS_DEF_NAME 0x10
#define ACTOR_EQUIP 0xb90                // actor: equipment holder (links from +0x8, 0x18 apart: 0 weapon)
#define EQUIP_VPTR 0x7001d349d8ull
#define EQUIP_LINKS 0x8
#define EQUIP_OWNER 0x98
#define LINKDATA_ID 0x40                 // link data: u32 id; +0x48 the actor
#define LINKDATA_PROC 0x48
#define PROC_STATE 0x64                  // actor: u8 state, 3 while it is deleted
#define PROC_DELETING 3
#define WEAPON_VPTR 0x7001c3f330ull      // the Weapon class
#define ACTOR_FOLLOW 0x4d0               // actor: its bone follow (never written 0: the game reads it unchecked)
#define FOLLOW_SET 0x7001533750ull       // (actor, follow)
#define FOLLOW_CTOR 0x7001096f68ull      // (follow): builds a bone follow (FOLLOW_SIZE bytes)
#define FOLLOW_TARGET 0x08               // follow: link to the actor it follows
#define FOLLOW_BONE 0x28                 // follow: char* bone name
#define FOLLOW_INDEX 0x60                // follow: u16 unit, u16 bone (0xffff: look the name up again)
#define FOLLOW_OFFSET 0x68               // follow: 3x4 offset from the bone, row by row
#define FOLLOW_MODE 0x98                 // follow: u32 mode (0)
#define FOLLOW_SIZE 0xa0
#define ACTOR_MODEL 0x4e0                // actor: model holder (vptr MODEL_VPTR; unit count +0x38, slots +0x40)
#define MODEL_VPTR 0x7001d0fa48ull
#define MODEL_NUNITS 0x38
#define MODEL_UNITS 0x40
#define UNIT_VPTR_A 0x7001d10738ull
#define UNIT_VPTR_B 0x7001d52200ull
#define UNIT_SKEL 0x118                  // unit: its skeleton (+0x0 resource "FSKL", +0x20 world matrices)
#define SKEL_WORLD 0x20                  // skeleton: world matrices, 0x40 a bone, column by column (translation +0x30)
#define FSKL_MAGIC 0x4c4b5346u
#define RES_DICT 0x10                    // skeleton resource: bone names (bone i: char* at +0x20+i*0x10, 2 bytes on)
#define RES_NBONES 0x3c                  // ... u16 bone count

#ifndef GUEST_LO                         // (the selftest widens the window to its own made-up memory)
#define GUEST_LO BOTW_ADDR_MAIN
#define GUEST_HI BOTW_ADDR_GUEST_END
#endif

// ---- the look and the timing
#define NB 17                            // bones scaled for the buff look
#define MAX_TARGETS 12                   // skeletons (Link's units and his armor's; and restores of the shoulders)
#define MAX_SK_BONES 256                 // bones a target skeleton may have (Link: 121)
#define WORLD_SIZE 0x40
#define LOCAL_SIZE 0x60
#define SHADOW_BYTES 0x60                // what the upload reads of a skeleton: +0x0 +0x8 +0x20 +0x40 +0x48
#define STAGE_A_OUT 1.18f                // wider shoulders: the four bones' translation x this along the bone
#define STAGE_A_RESTORE_S 1.0            // after the shoulders go off, the rest values are written this long
#define TARGETS_S 0.25                   // the buff targets are looked up this often
#define SEAT_UP (-0.097f)                // Zelda's seat on Link's Spine_2 (bone x up, y forward, z left): rows
#define SEAT_BACK (-0.266f)              // [0 1 0 SEAT_UP] [0 0 1 SEAT_BACK - back] [1 0 0 side]
#define SEAT_ROOT_UP 1.033f              // ... the same seat from Link's root, for the spawn (she appears in place)
#define BUFF_BACK_EXTREME 0.04f          // the extreme buff back is about 4 cm thicker: she sits that much further
#define BUFF_BACK_BUFF 0.03f             // back (the milder preset: 3 cm)
#define Z_RETRY_S 2.0                    // a failed spawn is tried again after this long ...
#define Z_MAX_FAILS 8                    // ... at most this many times in a row (then: "her pack didn't load")
#define Z_PENDING_S 30.0                 // a request not ready in this long is released
#define Z_RIDE_S 1.0                     // Ride must play this long after she spawned (else: not our pack)
#define Z_DIST_S 0.2                     // distance to the seat checked this often ...
#define Z_FAR_M 0.5f                     // ... over this for Z_FAR_S: the tick moves her itself (plan B)
#define Z_FAR_S 1.0
#define Z_AS_S 1.0                       // what she plays is checked this often once Ride played
#define Z_STRAY_S 1.0                    // a deleted Zelda still there after this long is a stray
#define Z_FORGET_S 30.0
#define SWINGS 16                        // swing times kept (status)

static const SwModApi *api;

// ------------------------------------------------------------------ small helpers

static int64_t g_qpf = 1;
static double now_s(void) {
    int64_t t;
    QueryPerformanceCounter(&t);
    return (double)t / (double)g_qpf;
}

#define P(v) ((uint64_t)(uintptr_t)(v))
// In the guest window (no memory query: for the hooks, which the game calls with its live objects).
static inline int in_guest(uint64_t a, uint64_t n) { return a >= GUEST_LO && a < GUEST_HI && n <= GUEST_HI - a; }
static inline int ptr_ok(uint64_t p) { return !(p & 7) && in_guest(p, 8); }
static inline uint64_t rd64(uint64_t a) { return *(volatile const uint64_t *)(uintptr_t)a; }
static inline uint32_t rd32(uint64_t a) { return *(volatile const uint32_t *)(uintptr_t)a; }
static inline uint16_t rd16(uint64_t a) { return *(volatile const uint16_t *)(uintptr_t)a; }
static inline void wr32(uint64_t a, uint32_t v) { *(volatile uint32_t *)(uintptr_t)a = v; }
static inline void wrf(uint64_t a, float v) { *(volatile float *)(uintptr_t)a = v; }
static inline float rdf(uint64_t a) {
    float f;
    memcpy(&f, (const void *)(uintptr_t)a, sizeof f);
    return f;
}
static inline int finite_f(float f) { return f == f && f - f == 0.0f; }

// A spin lock for the status the tick shares with the command; held only for copies.
static int g_lock;
static void lock(void) {
    while (__atomic_test_and_set(&g_lock, __ATOMIC_ACQUIRE)) __builtin_ia32_pause();
}
static void unlock(void) { __atomic_clear(&g_lock, __ATOMIC_RELEASE); }

// ---- checked reads (the tick only): a .. a+n-1 is committed, readable memory in the guest window. A region's answer
// is kept half a second (the game's heap is one region, so this is about one query a frame at most).
#define MEM_CACHE 16
typedef struct {
    uint64_t lo, hi;
    double until;
} MemSpan;
static MemSpan g_mem_cache[MEM_CACHE];
static int g_mem_next;
static double t_now;  // this tick's time (the cache's clock)

static int mem_ok(uint64_t a, uint64_t n) {
    if (!n || !in_guest(a, n)) return 0;
    for (int i = 0; i < MEM_CACHE; i++)
        if (a >= g_mem_cache[i].lo && a + n <= g_mem_cache[i].hi && t_now < g_mem_cache[i].until) return 1;
    MemInfo mi;
    if (VirtualQuery((const void *)(uintptr_t)a, &mi, sizeof mi) != sizeof mi) return 0;
    // MEM_COMMIT; readable (PAGE_READONLY, READWRITE, WRITECOPY and their EXECUTE_ forms), not NOACCESS or GUARD
    if (mi.state != 0x1000 || (mi.protect & 0x101) || !(mi.protect & 0xee)) return 0;
    if (a + n > mi.base + mi.size) return 0;
    g_mem_cache[g_mem_next] = (MemSpan){mi.base, mi.base + mi.size, t_now + 0.5};
    g_mem_next = (g_mem_next + 1) % MEM_CACHE;
    return 1;
}
static uint64_t t_u64(uint64_t a) { return !(a & 7) && mem_ok(a, 8) ? rd64(a) : 0; }
static uint32_t t_u32(uint64_t a) { return !(a & 3) && mem_ok(a, 4) ? rd32(a) : 0; }
static uint8_t t_u8(uint64_t a) { return mem_ok(a, 1) ? *(volatile const uint8_t *)(uintptr_t)a : 0; }
static float t_f32(uint64_t a) {
    uint32_t b = t_u32(a);
    float f;
    memcpy(&f, &b, sizeof f);
    return finite_f(f) ? f : 0.0f;
}
// The text at p into out (cap bytes with its 0): 1 to cap-1 printable characters, else 0 and "".
static int t_text(uint64_t p, char *out, size_t cap) {
    out[0] = 0;
    for (size_t i = 0; p && i < cap; i++) {
        uint64_t a = p + i;
        if ((i == 0 || !(a & 0xfff)) && !mem_ok(a, 1)) break;  // (checked again on each new page)
        char ch = *(volatile const char *)(uintptr_t)a;
        if (!ch) {
            if (!i) break;
            out[i] = 0;
            return 1;
        }
        if (ch <= ' ' || ch > '~') break;
        out[i] = ch;
    }
    out[0] = 0;
    return 0;
}

// The guest registers a tick may change: saved before and restored after, so the frame job's caller sees exactly
// what the game's function left.
typedef struct {
    uint64_t x[31], sp, pc, nzcv;
    SwModVReg v[32];
} SavedRegs;
static void save_regs(struct Cpu *c, SavedRegs *s) {
    SwModRegs *r = SWMOD_REGS(c);
    memcpy(s->x, r->x, sizeof s->x);
    s->sp = r->sp, s->pc = r->pc, s->nzcv = r->nzcv;
    memcpy(s->v, r->v, sizeof s->v);
}
static void restore_regs(struct Cpu *c, const SavedRegs *s) {
    SwModRegs *r = SWMOD_REGS(c);
    memcpy(r->x, s->x, sizeof s->x);
    r->sp = s->sp, r->pc = s->pc, r->nzcv = s->nzcv;
    memcpy(r->v, s->v, sizeof s->v);
}

static int64_t g_ncalls;  // game calls the tick made
// Call a guest function with up to 7 integer arguments (on the tick's game thread); returns x0.
static uint64_t gcall(struct Cpu *c, uint64_t fn, uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4,
                      uint64_t a5, uint64_t a6) {
    SwModRegs *r = SWMOD_REGS(c);
    r->x[0] = a0, r->x[1] = a1, r->x[2] = a2, r->x[3] = a3, r->x[4] = a4, r->x[5] = a5, r->x[6] = a6;
    api->guest_call(c, fn);
    g_ncalls++;
    return r->x[0];
}
#define GCALL1(c, fn, a) gcall(c, fn, P(a), 0, 0, 0, 0, 0, 0)
#define GCALL2(c, fn, a, b) gcall(c, fn, P(a), P(b), 0, 0, 0, 0, 0)
#define GCALL3(c, fn, a, b, d) gcall(c, fn, P(a), P(b), P(d), 0, 0, 0, 0)

// ------------------------------------------------------------------ settings (mod.toml [[settings]], all live)

enum { BUFF_OFF, BUFF_BUFF, BUFF_EXTREME };
typedef struct {
    int secret;           // secret_word: the word works (off: "Unknown command")
    int buff;             // BUFF_*
    int shoulders;        // wider shoulders
    int heavy;            // heavy_fast
    int zelda;            // Zelda on his back
    float seat_back;      // cm, -10..10: on top of the base seat (+ the buff's own)
    float seat_side;      // cm, 0..15: to Link's left (her head away from the bow-aim camera)
} Settings;
static const Settings DEFAULTS = {1, BUFF_EXTREME, 1, 1, 1, 0.0f, 0.0f};

static int has_settings(void) { return SWMOD_HAS(api, setting_str) && api->mod_id && api->setting_int; }

// The settings now (the getters work on any thread, without a lock). Without the settings API: the defaults.
static Settings read_settings(void) {
    Settings s = DEFAULTS;
    if (!has_settings()) return s;
    s.secret = api->setting_int(api, "secret_word", 1) != 0;
    char b[16];
    if (api->setting_str(api, "buff", b, sizeof b) >= 0)
        s.buff = !strcmp(b, "off") ? BUFF_OFF : !strcmp(b, "buff") ? BUFF_BUFF : BUFF_EXTREME;
    s.shoulders = api->setting_int(api, "shoulders", 1) != 0;
    s.heavy = api->setting_int(api, "heavy_fast", 1) != 0;
    s.zelda = api->setting_int(api, "zelda", 1) != 0;
    double back = api->setting_num(api, "seat_back", 0.0), side = api->setting_num(api, "seat_side", 0.0);
    s.seat_back = (float)(back == back && back >= -10.0 && back <= 10.0 ? back : 0.0);
    s.seat_side = (float)(side == side && side >= 0.0 && side <= 15.0 ? side : 0.0);
    return s;
}

static int g_settings_dirty = 1;  // a watcher sets it: the tick reads the settings at once
static void on_setting(const struct SwModApi *self, const char *key, void *user) {
    (void)self, (void)key, (void)user;
    __atomic_store_n(&g_settings_dirty, 1, __ATOMIC_RELEASE);
}

// ------------------------------------------------------------------ what the tick publishes to the hooks

static int g_want;                         // the command: the egg is on (atomic)
static int g_hook_skin, g_hook_world, g_hook_setter, g_hook_frame;  // hooks registered at init
enum { TICK_NONE, TICK_HOOK, TICK_ON_FRAME };
static const char *const TICK_NAMES[] = {"none", "hook", "on_frame"};
static int g_tick_src;
static int g_inplace;                      // buff mode: 0 copy (default), 1 in place (`mode inplace`, dev)

// One skeleton the buff hooks work on.
typedef struct {
    uint64_t sk, res;          // the skeleton and its resource ([sk] must still be res)
    uint16_t nb;               // its bone count (u16 at sk+SKEL_NBONES and res+RES_NBONES)
    uint8_t scale_on;          // scale its skin copy
    uint8_t a_mode;            // shoulders: 0 nothing, 1 write the wider translation, 2 write the rest translation
    int n;                     // scaled bones ...
    int16_t bone[NB];          // ... their indexes ...
    float scale[NB][3];        // ... and scales (along the bone, side, side)
    int na;                    // shoulder bones ...
    int16_t abone[4];          // ... their indexes ...
    float aout[4][3], arest[4][3];  // ... the wider and the rest translation
} Target;

static Target g_tgt[MAX_TARGETS];
static int g_ntgt;
static uint32_t g_tgt_seq;                 // seqlock: odd while the tick writes g_tgt
static int g_tgt_live;                     // g_ntgt > 0 (the hooks' one test while the egg is off)
static uint64_t g_heavy_holder;            // Link's animation holder while heavy swings are on, else 0

typedef struct {
    _Alignas(16) uint8_t shadow[SHADOW_BYTES];
    _Alignas(16) uint8_t world[MAX_SK_BONES * WORLD_SIZE];
    _Alignas(16) uint8_t saved[NB * WORLD_SIZE];  // in-place mode: the real matrices before the scale
} Slot;
static Slot g_slot[MAX_TARGETS];
static int g_slot_busy[MAX_TARGETS];
static int64_t g_n_copies, g_n_busy, g_n_bad, g_n_stagea, g_n_swaps, g_n_setter_seen;  // hook counters (atomics)

// The tick publishes a new set (n 0: none; the hooks then pass everything through).
static void tgt_publish(const Target *t, int n) {
    __atomic_fetch_add(&g_tgt_seq, 1, __ATOMIC_SEQ_CST);  // (odd: readers retry or pass through)
    if (n > 0) memcpy(g_tgt, t, (size_t)n * sizeof *t);
    __atomic_store_n(&g_ntgt, n, __ATOMIC_RELAXED);
    __atomic_fetch_add(&g_tgt_seq, 1, __ATOMIC_SEQ_CST);
    __atomic_store_n(&g_tgt_live, n > 0, __ATOMIC_RELEASE);
}

// A copy of the target for skeleton sk, and its index (-1: none, or the tick was writing: pass through).
static int tgt_find(uint64_t sk, Target *out) {
    uint32_t s1 = __atomic_load_n(&g_tgt_seq, __ATOMIC_ACQUIRE);
    if (s1 & 1) return -1;
    int n = __atomic_load_n(&g_ntgt, __ATOMIC_RELAXED), idx = -1;
    for (int i = 0; i < n && i < MAX_TARGETS; i++)
        if (__atomic_load_n(&g_tgt[i].sk, __ATOMIC_RELAXED) == sk) {
            memcpy(out, &g_tgt[i], sizeof *out);
            idx = i;
            break;
        }
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    return __atomic_load_n(&g_tgt_seq, __ATOMIC_RELAXED) == s1 ? idx : -1;
}

static int scale_finite(const float s[3]) {
    return finite_f(s[0]) && finite_f(s[1]) && finite_f(s[2]) && s[0] > 0.1f && s[0] < 10.0f && s[1] > 0.1f &&
           s[1] < 10.0f && s[2] > 0.1f && s[2] < 10.0f;
}
// A world matrix (4 columns of 4 floats) times a local scale: column j of the rotation part times s[j].
static void scale_mtx(float *m, const float s[3]) {
    for (int j = 0; j < 3; j++)
        for (int e = 0; e < 3; e++) m[4 * j + e] *= s[j];
}

// ------------------------------------------------------------------ hooks (game threads; cheap; off = one load)

// Skin upload (replace): for a target skeleton, the game's own function gets a copy of the skeleton whose world
// matrices are a scaled copy; the real ones are never written (copy mode). The call's flag bits are put back into the
// real skeleton. A slot busy on another thread, or anything that doesn't match: the game's call as it is.
static void skin_replace(struct Cpu *c, SwModGuestFn orig) {
    if (!__atomic_load_n(&g_tgt_live, __ATOMIC_ACQUIRE)) {
        orig(c);
        return;
    }
    SwModRegs *r = SWMOD_REGS(c);
    uint64_t sk = r->x[0];
    Target t;
    int i = tgt_find(sk, &t);
    if (i < 0 || !t.scale_on || t.n <= 0) {
        orig(c);
        return;
    }
    if (__atomic_exchange_n(&g_slot_busy[i], 1, __ATOMIC_ACQUIRE)) {
        __atomic_fetch_add(&g_n_busy, 1, __ATOMIC_RELAXED);
        orig(c);
        return;
    }
    Slot *s = &g_slot[i];
    uint64_t w = ptr_ok(sk) && in_guest(sk, SHADOW_BYTES) ? rd64(sk + SKEL_WORLD) : 0;
    if (!w || rd64(sk) != t.res || rd16(sk + SKEL_NBONES) != t.nb || !t.nb || t.nb > MAX_SK_BONES ||
        !ptr_ok(w) || !in_guest(w, (uint64_t)t.nb * WORLD_SIZE)) {
        __atomic_store_n(&g_slot_busy[i], 0, __ATOMIC_RELEASE);
        __atomic_fetch_add(&g_n_bad, 1, __ATOMIC_RELAXED);
        orig(c);
        return;
    }
    if (__atomic_load_n(&g_inplace, __ATOMIC_RELAXED)) {  // (fallback: scale the real ones, put them back after)
        int done[NB];
        for (int k = 0; k < t.n; k++) {
            done[k] = t.bone[k] >= 0 && t.bone[k] < t.nb && scale_finite(t.scale[k]);
            if (!done[k]) continue;
            float *m = (float *)(uintptr_t)(w + (uint64_t)t.bone[k] * WORLD_SIZE);
            memcpy(s->saved + (size_t)k * WORLD_SIZE, m, WORLD_SIZE);
            scale_mtx(m, t.scale[k]);
        }
        orig(c);
        for (int k = 0; k < t.n; k++)
            if (done[k]) memcpy((void *)(uintptr_t)(w + (uint64_t)t.bone[k] * WORLD_SIZE), s->saved + (size_t)k * WORLD_SIZE,
                                WORLD_SIZE);
    } else {
        memcpy(s->shadow, (const void *)(uintptr_t)sk, SHADOW_BYTES);
        memcpy(s->world, (const void *)(uintptr_t)w, (size_t)t.nb * WORLD_SIZE);
        for (int k = 0; k < t.n; k++)
            if (t.bone[k] >= 0 && t.bone[k] < t.nb && scale_finite(t.scale[k]))
                scale_mtx((float *)(void *)(s->world + (size_t)t.bone[k] * WORLD_SIZE), t.scale[k]);
        uint16_t before;
        memcpy(&before, s->shadow + SKEL_FLAGS, 2);
        uint64_t wp = P(s->world);
        memcpy(s->shadow + SKEL_WORLD, &wp, 8);
        r->x[0] = P(s->shadow);
        orig(c);
        uint16_t after;
        memcpy(&after, s->shadow + SKEL_FLAGS, 2);
        uint16_t set = (uint16_t)(after & ~before);
        if (set) __atomic_fetch_or((uint16_t *)(uintptr_t)(sk + SKEL_FLAGS), set, __ATOMIC_RELAXED);
    }
    __atomic_fetch_add(&g_n_copies, 1, __ATOMIC_RELAXED);
    __atomic_store_n(&g_slot_busy[i], 0, __ATOMIC_RELEASE);
}

// World calc (before): for a target skeleton with the wider shoulders, the four bones' local translation is set to
// the rest translation x STAGE_A_OUT along the bone (always from the rest value, so it never adds up); after they go
// off, the rest value for a while. A translation only: every matrix stays a clean rotation.
static bool world_before(struct Cpu *c) {
    if (!__atomic_load_n(&g_tgt_live, __ATOMIC_ACQUIRE)) return false;
    uint64_t sk = SWMOD_REGS(c)->x[0];
    Target t;
    if (tgt_find(sk, &t) < 0 || !t.a_mode || t.na <= 0) return false;
    if (!ptr_ok(sk) || rd64(sk) != t.res || rd16(sk + SKEL_NBONES) != t.nb) return false;
    uint64_t loc = rd64(sk + SKEL_LOCAL);
    if (!ptr_ok(loc) || !in_guest(loc, (uint64_t)t.nb * LOCAL_SIZE)) return false;
    for (int k = 0; k < t.na && k < 4; k++) {
        if (t.abone[k] < 0 || t.abone[k] >= t.nb) continue;
        uint64_t e = loc + (uint64_t)t.abone[k] * LOCAL_SIZE;
        if (rd32(e) & (1u << 27)) continue;  // ("translation zero": not a bone that has one)
        const float *v = t.a_mode == 1 ? t.aout[k] : t.arest[k];
        if (!finite_f(v[0]) || !finite_f(v[1]) || !finite_f(v[2])) continue;
        wrf(e + 0x50, v[0]), wrf(e + 0x54, v[1]), wrf(e + 0x58, v[2]);
        __atomic_fetch_add(&g_n_stagea, 1, __ATOMIC_RELAXED);
    }
    return false;
}

static const char LARGE_TEXT[] = "WeaponLargeSword", SMALL_TEXT[] = "WeaponSmallSword";

// Animation string setter (before): Link's holder, parameter 0x2c, the text WeaponLargeSword -> the game's own static
// WeaponSmallSword object instead. The caller's object is never written (on the draw it is the weapon's own name).
static bool setter_before(struct Cpu *c) {
    uint64_t holder = __atomic_load_n(&g_heavy_holder, __ATOMIC_ACQUIRE);
    if (!holder) return false;
    SwModRegs *r = SWMOD_REGS(c);
    if ((uint32_t)r->x[1] != PARAM_WEAPON || r->x[0] != holder) return false;
    __atomic_fetch_add(&g_n_setter_seen, 1, __ATOMIC_RELAXED);
    uint64_t s = r->x[2];
    if (!ptr_ok(s) || !in_guest(s, 16) || rd64(s) != STRING_OBJ_VPTR) return false;
    uint64_t text = rd64(s + 8);
    if (!in_guest(text, sizeof LARGE_TEXT) || memcmp((const void *)(uintptr_t)text, LARGE_TEXT, sizeof LARGE_TEXT))
        return false;
    r->x[2] = STR_SMALL_SWORD;
    __atomic_fetch_add(&g_n_swaps, 1, __ATOMIC_RELAXED);
    return false;
}

// ------------------------------------------------------------------ status (the tick's copy, shared under g_lock)

enum { ZS_IDLE, ZS_PENDING, ZS_LIVE };
static const char *const ZS_NAMES[] = {"idle", "pending", "live"};
enum { ZG_NONE, ZG_NO_PACK, ZG_LOAD, ZG_NOT_OURS, ZG_OWN_FOLLOW };  // why Zelda gave up (until the next ON)
static const char *const ZG_NAMES[] = {"", "no pack", "pack didn't load", "not our pack (no Ride)",
                                       "her actor makes its own follow"};

typedef struct {
    char name[16];
    int ticks;
    float ms;
} Swing;

typedef struct {
    int64_t ticks;
    double rate, last_t;
    int threads;
    int on;                    // the egg, as the tick runs it (the word was typed and secret_word is ON)
    uint64_t link;
    Settings set;
    // buff
    int buff_on, shoulders_on, ntargets, link_targets, armor_targets, restoring;
    double copies_per_frame;
    // heavy
    int heavy_on, strings;     // strings: 0 not checked yet, 1 ok, -1 not where expected (heavy off)
    int wtype, moveset, drawn, applied, pending, attacking;
    char hand[16], param[32], body[32];
    int64_t setter_calls;
    Swing swing[SWINGS];
    int nswings;
    // zelda
    int zstate, zmanual, zgiveup, zpack, zstray, zfails, zride, zevent;
    float zdist;
    char zas[32];
    int64_t zspawns, zlosses, zretargets, zreasserts, zrefollows;
    char last[160];
} Status;

static Status t_st;  // the tick's
static Status g_st;  // under g_lock

// Say what happened (in `status` and, when it is news, in savagewind.log).
__attribute__((format(printf, 1, 2))) static void note(const char *fmt, ...) {
    char text[sizeof t_st.last];
    __builtin_va_list ap;
    __builtin_va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    __builtin_va_end(ap);
    if (strcmp(text, t_st.last)) api->log("zelda_view: %s", text);
    memcpy(t_st.last, text, sizeof text);
}

// ------------------------------------------------------------------ bones (plain reads, every pointer checked)

// Unit u of actor a's model: its skeleton (0: no model yet, or a check failed). *nunits: the model's unit count.
static uint64_t model_skel(uint64_t a, int u, int *nunits) {
    if (nunits) *nunits = 0;
    uint64_t m = t_u64(a + ACTOR_MODEL);
    if (!m || t_u64(m) != MODEL_VPTR) return 0;
    int32_t n = (int32_t)t_u32(m + MODEL_NUNITS);
    if (n < 0 || n > 64) return 0;
    if (nunits) *nunits = n;
    if (u < 0 || u >= n) return 0;
    uint64_t arr = t_u64(m + MODEL_UNITS);
    uint64_t slot = arr ? t_u64(arr + (uint64_t)u * 8) : 0;
    uint64_t unit = slot ? t_u64(slot) : 0;
    uint64_t vp = unit ? t_u64(unit) : 0;
    if (vp != UNIT_VPTR_A && vp != UNIT_VPTR_B) return 0;
    uint64_t sk = t_u64(unit + UNIT_SKEL);
    uint64_t res = sk ? t_u64(sk) : 0;
    if (!res || t_u32(res) != FSKL_MAGIC) return 0;
    return sk;
}

static int skel_nbones(uint64_t sk) {
    uint64_t res = t_u64(sk);
    int n = res ? (int)(t_u32(res + RES_NBONES) & 0xffff) : 0;
    return n <= 1024 ? n : 0;
}

// The indexes of n bone names in skeleton sk (one pass over its names): out[k] = index or -1. Returns how many found.
static int skel_find_many(uint64_t sk, const char *const *names, int n, int16_t *out) {
    uint64_t res = t_u64(sk), dict = res ? t_u64(res + RES_DICT) : 0;
    int nb = skel_nbones(sk), found = 0;
    for (int k = 0; k < n; k++) out[k] = -1;
    char got[32];
    for (int b = 0; dict && b < nb && found < n; b++) {
        uint64_t key = t_u64(dict + 0x20 + (uint64_t)b * 0x10);
        if (!key || !t_text(key + 2, got, sizeof got)) continue;
        for (int k = 0; k < n; k++)
            if (out[k] < 0 && !strcmp(got, names[k])) {
                out[k] = (int16_t)b;
                found++;
                break;
            }
    }
    return found;
}

// ------------------------------------------------------------------ part A: extremely buff (targets, each 0.25 s)

static const char *const BUFF_BONES[NB] = {
    "Spine_1", "Spine_2", "Clavicle_L", "Clavicle_R", "Clavicle_Assist_L", "Clavicle_Assist_R", "Arm_1_L", "Arm_1_R",
    "Arm_1_Assist_L", "Arm_1_Assist_R", "Arm_2_L", "Arm_2_R", "Elbow_L", "Elbow_R", "Wrist_Assist_L",
    "Wrist_Assist_R", "Neck"};
// Side scales (the bone's local y and z; Link's bones point along x) for the presets buff and extreme
// (out/research/zelda-view-buff.md: biceps 2x, forearms 1.7x, chest 1.5x, traps and delts 1.6-1.8x, neck 1.6x).
static const float SIDE[2][NB][2] = {
    {{1.15f, 1.15f}, {1.35f, 1.40f}, {1.45f, 1.45f}, {1.45f, 1.45f}, {1.60f, 1.60f}, {1.60f, 1.60f}, {1.75f, 1.75f},
     {1.75f, 1.75f}, {1.75f, 1.75f}, {1.75f, 1.75f}, {1.55f, 1.55f}, {1.55f, 1.55f}, {1.55f, 1.55f}, {1.55f, 1.55f},
     {1.35f, 1.35f}, {1.35f, 1.35f}, {1.45f, 1.45f}},
    {{1.20f, 1.20f}, {1.47f, 1.54f}, {1.61f, 1.61f}, {1.61f, 1.61f}, {1.81f, 1.81f}, {1.81f, 1.81f}, {2.01f, 2.01f},
     {2.01f, 2.01f}, {2.01f, 2.01f}, {2.01f, 2.01f}, {1.74f, 1.74f}, {1.74f, 1.74f}, {1.74f, 1.74f}, {1.74f, 1.74f},
     {1.47f, 1.47f}, {1.47f, 1.47f}, {1.61f, 1.61f}},
};
#define CLAVICLE_ALONG 1.18f             // the clavicles also get longer, unless the wider shoulders do that
static const int STAGE_A_BONES[4] = {4, 5, 6, 7};  // Clavicle_Assist_L/R, Arm_1_L/R (indexes into BUFF_BONES)

static Target t_cur[MAX_TARGETS];        // the set published last
static int t_ncur;
static double t_buff_next;
static struct {
    uint64_t sk, res;
    int na;
    int16_t abone[4];
    float arest[4][3];
    double until;
} t_restore[MAX_TARGETS];                // skeletons whose shoulders went off: the rest values until `until`
static int t_nrestore;
static int64_t t_copies_seen, t_copies_ticks;
static double t_copies_avg;

// Fill target t for skeleton sk (its bone indexes from the last set when it is the same skeleton). 0: not a target.
static int target_fill(Target *t, uint64_t sk, int preset, int shoulders) {
    memset(t, 0, sizeof *t);
    uint64_t res = t_u64(sk);
    int nb = skel_nbones(sk);
    if (!res || nb <= 0 || nb > MAX_SK_BONES || (t_u32(sk + SKEL_NBONES) & 0xffff) != (uint32_t)nb) return 0;
    uint64_t w = t_u64(sk + SKEL_WORLD);
    if (!w || !mem_ok(w, (uint64_t)nb * WORLD_SIZE)) return 0;
    int16_t idx[NB];
    const Target *old = NULL;
    for (int i = 0; i < t_ncur; i++)
        if (t_cur[i].sk == sk && t_cur[i].res == res && t_cur[i].nb == nb && t_cur[i].scale_on) old = &t_cur[i];
    if (old) {
        for (int k = 0; k < NB; k++) idx[k] = -1;
        for (int k = 0; k < old->n; k++) idx[k] = old->bone[k];  // (n == NB for a filled target: see below)
    } else if (!skel_find_many(sk, BUFF_BONES, NB, idx)) {
        return 0;
    }
    t->sk = sk, t->res = res, t->nb = (uint16_t)nb, t->scale_on = 1;
    t->n = NB;  // (all NB rows, -1 for a bone this skeleton lacks: the hooks skip those)
    for (int k = 0; k < NB; k++) {
        t->bone[k] = idx[k];
        int clav = k == 2 || k == 3;
        t->scale[k][0] = clav && !shoulders ? CLAVICLE_ALONG : 1.0f;
        t->scale[k][1] = SIDE[preset == BUFF_EXTREME][k][0];
        t->scale[k][2] = SIDE[preset == BUFF_EXTREME][k][1];
    }
    if (shoulders) {
        uint64_t bones = t_u64(res + RES_BONES);
        for (int j = 0; j < 4 && bones; j++) {
            int b = idx[STAGE_A_BONES[j]];
            uint64_t e = bones + (uint64_t)(b >= 0 ? b : 0) * 0x50 + 0x44;
            if (b < 0 || !mem_ok(e, 12)) continue;
            float v[3] = {rdf(e), rdf(e + 4), rdf(e + 8)};
            if (!finite_f(v[0]) || !finite_f(v[1]) || !finite_f(v[2]) || v[0] > 1.0f || v[0] < -1.0f) continue;
            int a = t->na++;
            t->abone[a] = (int16_t)b;
            memcpy(t->arest[a], v, sizeof v);
            t->aout[a][0] = v[0] * STAGE_A_OUT, t->aout[a][1] = v[1], t->aout[a][2] = v[2];
        }
        t->a_mode = t->na ? 1 : 0;
    }
    return 1;
}

// Add the targets of every unit of actor a's model. Returns how many were added.
static int targets_of(uint64_t a, Target *out, int *n, int preset, int shoulders) {
    int nunits = 0, added = 0;
    model_skel(a, 0, &nunits);
    for (int u = 0; u < nunits && u < 8 && *n < MAX_TARGETS; u++) {
        uint64_t sk = model_skel(a, u, NULL);
        if (sk && target_fill(&out[*n], sk, preset, shoulders)) (*n)++, added++;
    }
    return added;
}

// Every 0.25 s while the buff is on (and once when it goes off): Link's skeletons and his worn armor's.
static void buff_tick(uint64_t link, int want, int preset, int shoulders, double t, int force) {
    if (!force && t < t_buff_next) return;
    t_buff_next = t + TARGETS_S;
    Target nt[MAX_TARGETS];
    int n = 0, nl = 0, na = 0;
    if (want && link) {
        nl = targets_of(link, nt, &n, preset, shoulders);
        uint64_t am = t_u64(link + PLAYER_ARMOR);
        if (am && t_u64(am) == ARMOR_VPTR && t_u64(am + ARMOR_OWNER) == link) {
            for (int i = 0; i < ARMOR_SLOTS && n < MAX_TARGETS; i++) {
                uint64_t l = am + ARMOR_LINKS + (uint64_t)i * ARMOR_LINK_SIZE;
                uint64_t data = t_u64(l);
                uint32_t id = t_u32(l + 8);
                if (!data || id == 0xffffffffu || t_u32(data + LINKDATA_ID) != id) continue;
                uint64_t piece = t_u64(data + LINKDATA_PROC);
                if (!piece || piece == link || t_u8(piece + PROC_STATE) == PROC_DELETING) continue;
                na += targets_of(piece, nt, &n, preset, shoulders);
            }
        }
    }
    if (!n && !t_ncur && !t_nrestore) return;  // (nothing then, nothing now)
    // shoulders that were on and are not now (off, or the skeleton left the set): the rest values for a while
    for (int i = 0; i < t_ncur; i++) {
        const Target *o = &t_cur[i];
        if (o->a_mode != 1) continue;
        int still = 0;
        for (int k = 0; k < n && !still; k++) still = nt[k].sk == o->sk && nt[k].a_mode == 1;
        if (still || t_nrestore >= MAX_TARGETS) continue;
        t_restore[t_nrestore].sk = o->sk, t_restore[t_nrestore].res = o->res, t_restore[t_nrestore].na = o->na;
        memcpy(t_restore[t_nrestore].abone, o->abone, sizeof o->abone);
        memcpy(t_restore[t_nrestore].arest, o->arest, sizeof o->arest);
        t_restore[t_nrestore++].until = t + STAGE_A_RESTORE_S;
    }
    int keep = 0;
    for (int i = 0; i < t_nrestore; i++) {
        if (t >= t_restore[i].until) continue;
        int dup = 0;  // (a skeleton back in the set with its shoulders on needs no restore)
        for (int k = 0; k < n && !dup; k++) dup = nt[k].sk == t_restore[i].sk && nt[k].a_mode == 1;
        if (dup) continue;
        t_restore[keep++] = t_restore[i];
    }
    t_nrestore = keep;
    for (int i = 0; i < t_nrestore; i++) {
        int k = 0;
        while (k < n && nt[k].sk != t_restore[i].sk) k++;
        if (k == n) {  // (not a buff target now: a restore-only entry)
            if (n == MAX_TARGETS) continue;
            memset(&nt[n], 0, sizeof nt[n]);
            nt[n].sk = t_restore[i].sk, nt[n].res = t_restore[i].res;
            nt[n].nb = (uint16_t)skel_nbones(t_restore[i].sk);
            if (!nt[n].nb) continue;
            n++;
        }
        Target *e = &nt[k];
        e->a_mode = 2, e->na = t_restore[i].na;
        memcpy(e->abone, t_restore[i].abone, sizeof e->abone);
        memcpy(e->arest, t_restore[i].arest, sizeof e->arest);
    }
    tgt_publish(nt, n);
    memcpy(t_cur, nt, (size_t)n * sizeof *nt);
    t_ncur = n;
    t_st.ntargets = n, t_st.link_targets = nl, t_st.armor_targets = na, t_st.restoring = t_nrestore;
}

// The old Link's skeletons are gone (a reload): nothing is published for them any more, nothing is written back.
static void buff_forget(void) {
    tgt_publish(NULL, 0);
    t_ncur = 0, t_nrestore = 0, t_buff_next = 0;  // (the new Link's are looked up at once)
    t_st.ntargets = t_st.link_targets = t_st.armor_targets = t_st.restoring = 0;
}

// ------------------------------------------------------------------ part B: heavy weapons swing like one-handers

static int t_happlied;     // we set Link's class to one-handed (put back when it goes off)
static int t_cut_on;       // a Cut... body animation plays ...
static char t_cut_name[16];
static int64_t t_cut_tick; // ... since this tick
static double t_cut_t;

// The name slot/sub of animation holder h plays ("" nothing; 0: no such slot or sub).
static int as_sub_name(uint64_t h, int slot, int sub, char out[32]) {
    out[0] = 0;
    int32_t n = (int32_t)t_u32(h + AS_SLOTS);
    uint64_t slots = t_u64(h + AS_SLOT_ARRAY);
    if (slot < 0 || slot >= n || n > 16 || !slots) return 0;
    uint64_t s = slots + (uint64_t)slot * AS_SLOT_SIZE;
    int32_t m = (int32_t)t_u32(s + AS_SLOT_NSUB);
    uint64_t subs = t_u64(s + AS_SLOT_SUBS);
    if (sub < 0 || sub >= m || m > 16 || !subs) return 0;
    uint64_t e = subs + (uint64_t)sub * AS_SUB_SIZE;
    if (!t_u64(e + AS_SUB_LIVE)) return 1;
    uint64_t d = t_u64(e);
    if (!d || !t_text(t_u64(d + AS_DEF_NAME), out, 32)) memcpy(out, "?", 2);
    return 1;
}

// The text of string parameter `index` of holder h ("" when it has none).
static int param_text(uint64_t h, int index, char out[32]) {
    out[0] = 0;
    int8_t slot = (int8_t)t_u8(h + HOLDER_PARAM_MAP + (uint64_t)index);
    int32_t n = (int32_t)t_u32(h + HOLDER_PARAMS);
    if (slot < 0 || slot >= n || n > 0x100) return 0;
    uint64_t arr = t_u64(h + HOLDER_PARAM_ARRAY);
    uint64_t entry = arr ? t_u64(arr + (uint64_t)slot * 8) : 0;
    return entry ? t_text(t_u64(entry + 8), out, 32) : 0;
}

// The type of Link's equipped weapon (slot 0): 0 one-handed, 1 two-handed... -1 none or not a Weapon.
static int weapon_type(uint64_t link) {
    uint64_t eq = link + ACTOR_EQUIP;
    if (t_u64(eq) != EQUIP_VPTR || t_u64(eq + EQUIP_OWNER) != link) return -1;
    uint64_t data = t_u64(eq + EQUIP_LINKS);
    uint32_t id = t_u32(eq + EQUIP_LINKS + 8);
    if (!data || id == 0xffffffffu || t_u32(data + LINKDATA_ID) != id) return -1;
    uint64_t w = t_u64(data + LINKDATA_PROC);
    if (!w || t_u8(w + PROC_STATE) == PROC_DELETING || t_u64(w) != WEAPON_VPTR || !mem_ok(w + WEAPON_TYPE, 4)) return -1;
    return (int32_t)rd32(w + WEAPON_TYPE);
}

static int is_attack(const char *body) {
    return !strncmp(body, "Cut", 3) || !strcmp(body, "GuardJust") || !strncmp(body, "WeaponThrow", 11);
}

// The game's two static name objects are where the research found them (checked once, at the first tick).
static int strings_ok(void) {
    char a[24], b[24];
    return t_u64(STR_SMALL_SWORD) == STRING_OBJ_VPTR && t_u64(STR_LARGE_SWORD) == STRING_OBJ_VPTR &&
           t_text(t_u64(STR_SMALL_SWORD + 8), a, sizeof a) && !strcmp(a, SMALL_TEXT) &&
           t_text(t_u64(STR_LARGE_SWORD + 8), b, sizeof b) && !strcmp(b, LARGE_TEXT);
}

static void swing_track(const char *body) {
    int cut = !strncmp(body, "Cut", 3);
    if (t_cut_on && (!cut || strncmp(body, t_cut_name, sizeof t_cut_name - 1))) {  // one ended
        Swing *s = &t_st.swing[t_st.nswings % SWINGS];
        memcpy(s->name, t_cut_name, sizeof s->name);
        s->ticks = (int)(t_st.ticks - t_cut_tick);
        s->ms = (float)((t_now - t_cut_t) * 1000.0);
        t_st.nswings++;
        t_cut_on = 0;
    }
    if (cut && !t_cut_on) {
        snprintf(t_cut_name, sizeof t_cut_name, "%s", body);
        t_cut_tick = t_st.ticks, t_cut_t = t_now, t_cut_on = 1;
    }
}

static void heavy_tick(struct Cpu *c, uint64_t link, int want) {
    uint64_t h = link ? t_u64(link + ACTOR_AS) : 0;
    if (h && t_u64(h + AS_OWNER) != link) h = 0;  // (only his own holder)
    int type = link ? weapon_type(link) : -1;
    char body[32] = "", hand[16] = "", param[32] = "";
    if (h) as_sub_name(h, 0, 0, body);
    if (link) t_text(t_u64(link + PLAYER_HAND), hand, sizeof hand);
    if (h) param_text(h, PARAM_WEAPON, param);
    swing_track(body);
    int attacking = is_attack(body);
    int on = want && t_st.strings == 1 && h && type == 1;
    __atomic_store_n(&g_heavy_holder, on && g_hook_setter ? h : 0, __ATOMIC_RELEASE);
    int mv = link && mem_ok(link + PLAYER_MOVESET, 4) ? (int32_t)rd32(link + PLAYER_MOVESET) : -1;
    int dc = link && mem_ok(link + PLAYER_DRAWN, 4) ? (int32_t)rd32(link + PLAYER_DRAWN) : -1;
    t_st.pending = 0;
    if (on) {
        if (attacking) {
            t_st.pending = mv == 1 || (!strcmp(hand, "Sword") && !strcmp(param, LARGE_TEXT));
        } else {
            if (mv == 1) wr32(link + PLAYER_MOVESET, 0), mv = 0;
            if (dc == 1) wr32(link + PLAYER_DRAWN, 0), dc = 0;
            if (!strcmp(hand, "Sword") && !strcmp(param, LARGE_TEXT)) {
                gcall(c, STR_SETTER, h, PARAM_WEAPON, STR_SMALL_SWORD, 0, 0, 0, 0);
                t_st.setter_calls++;
                param_text(h, PARAM_WEAPON, param);
            }
            if (!t_happlied) note("heavy swings: one-handed now");
            t_happlied = 1;
        }
    } else if (t_happlied) {
        if (type != 1 || !link) {
            t_happlied = 0;  // (another weapon, or none: the game set its own values)
        } else if (attacking) {
            t_st.pending = 1;
        } else {
            if (mv == 0) wr32(link + PLAYER_MOVESET, 1), mv = 1;
            if (dc == 0) wr32(link + PLAYER_DRAWN, 1), dc = 1;
            if (h && !strcmp(hand, "Sword") && !strcmp(param, SMALL_TEXT)) {
                gcall(c, STR_SETTER, h, PARAM_WEAPON, STR_LARGE_SWORD, 0, 0, 0, 0);
                t_st.setter_calls++;
                param_text(h, PARAM_WEAPON, param);
            }
            t_happlied = 0;
            note("heavy swings: back to two-handed");
        }
    }
    t_st.heavy_on = on, t_st.applied = t_happlied, t_st.attacking = attacking;
    t_st.wtype = type, t_st.moveset = mv, t_st.drawn = dc;
    memcpy(t_st.body, body, sizeof body);
    memcpy(t_st.hand, hand, sizeof hand);
    memcpy(t_st.param, param, sizeof param);
}

// ------------------------------------------------------------------ part C: Zelda piggyback

typedef struct {
    uint64_t unit;  // the request's unit (its +8 points back at the handle while it is ours)
    uint8_t failed;
    uint8_t pad[7];
} Handle;
typedef struct {
    uint64_t data;  // the link's data; the actor is at data+0x48
    uint32_t id;
    uint8_t acquired;
    uint8_t pad[3];
} Link;
typedef struct {
    uint64_t vptr, zero;
    uint8_t buf[0xc4];
    uint8_t pad[0xc];
} Pack;
typedef struct {
    uint64_t vptr;
    uint64_t str;
} StrObj;
typedef struct {
    _Alignas(16) uint8_t b[FOLLOW_SIZE];
} Follow;

// The game gets pointers to all of these: statics.
static _Alignas(16) Handle g_zhandle;
static _Alignas(16) Link g_zlink = {0, 0xffffffffu, 0, {0}};
static _Alignas(16) Link g_ztrash = {0, 0xffffffffu, 0, {0}};  // a deleted Zelda, watched until she is gone
static _Alignas(16) Pack g_zpack;
static _Alignas(16) StrObj g_key_m;
static _Alignas(16) float g_zmtx[12];
static _Alignas(16) Follow g_follow[2];   // used in turn: a Zelda being deleted may still run the one she had
static _Alignas(16) uint64_t g_fake_action[2];  // AS_PLAY's "action": it reads only +8, the actor
static _Alignas(16) float g_zpos[3], g_zrot[9];
static const char BONE_SPINE2[] = "Spine_2", AS_RIDE[] = "Ride";
static const char ZV_SLOT_NAME[] = ZV_SLOT;  // (the request keeps the pointer)

static uint8_t t_follow_built[2];
static int t_zfollow_next;
static uint64_t t_zfollow;        // the follow she has (P(&g_follow[k])), 0: none
static int t_zcheck_set;          // the follow was set last tick: check it took
static int t_zresets;             // the game took her follow away and it was set again
static double t_zsince, t_zretry, t_zfar_since, t_zdist_t, t_zas_t, t_zride_ask_t, t_ztrash_t;
static int t_zride_asks;
static uint64_t t_zstage_heap, t_zstage_link;
static float t_zseat[12];         // the seat offset in her follow now
static int t_zbone_u = -1, t_zbone_b = -1;  // Link's Spine_2 (unit, bone), -1: not found yet
static uint64_t t_zbone_link;
static int t_zpack_known;         // pack file checked in this ON period (1 there, -1 missing, 0 not yet)

static int pack_deployed(void) {
    wchar_t p[1100];
    static const wchar_t tail[] = L"\\Actor\\Pack\\" ZV_SLOT_W L".sbactorpack";
    unsigned long n = GetEnvironmentVariableW(L"SAVAGEWIND_ROMFS_OVERLAY", p, 1000);
    if (!n || n >= 1000) return 0;
    memcpy(p + n, tail, sizeof tail);
    unsigned long attrs = GetFileAttributesW(p);
    return attrs != 0xffffffffu && !(attrs & 0x10);  // (a file, not a folder)
}
static int (*g_pack_fn)(void) = pack_deployed;  // (the selftest puts its own here)

// The seat offset (row-major 3x4, Zelda's root in Link's Spine_2 space: bone x up, y forward, z left).
static void seat_offset(float m[12], float back_m, float side_m) {
    float s[12] = {0, 1, 0, SEAT_UP, 0, 0, 1, SEAT_BACK - back_m, 1, 0, 0, side_m};
    memcpy(m, s, sizeof s);
}

static uint64_t spawn_heap(void) {
    uint64_t holder = t_u64(HEAP_HOLDER_PTR);
    return holder ? t_u64(holder + HEAP_OFF) : 0;
}

// An event (cutscene, talk) runs: new spawns wait (she stays on his back through one). -1: can't tell (not blocking).
static int event_running(void) {
    uint64_t mgr = t_u64(EVENT_MGR_PTR);
    if (!mgr || !mem_ok(mgr + EVENT_RUNNING, 8)) return -1;
    return rd64(mgr + EVENT_RUNNING) != 0;
}

static void zero_link(Link *l) {
    memset(l, 0, sizeof *l);
    l->id = 0xffffffffu;
}

// A link still names a live actor (the game's own test, as plain reads).
static int link_ok(const Link *l) {
    if (!l->data || l->id == 0xffffffffu || t_u32(l->data + LINKDATA_ID) != l->id) return 0;
    uint64_t proc = t_u64(l->data + LINKDATA_PROC);
    return proc && t_u8(proc + PROC_STATE) != PROC_DELETING;
}

// Link's Spine_2 world matrix (4 columns of 4 floats) into W. 0: not readable.
static int link_spine(uint64_t link, float W[16]) {
    if (link != t_zbone_link) t_zbone_link = link, t_zbone_u = t_zbone_b = -1;
    if (t_zbone_b < 0) {
        int nunits = 0;
        model_skel(link, 0, &nunits);
        for (int u = 0; u < nunits && u < 8 && t_zbone_b < 0; u++) {
            uint64_t sk = model_skel(link, u, NULL);
            int16_t b;
            if (sk && skel_find_many(sk, (const char *const[]){BONE_SPINE2}, 1, &b)) t_zbone_u = u, t_zbone_b = b;
        }
        if (t_zbone_b < 0) return 0;
    }
    uint64_t sk = model_skel(link, t_zbone_u, NULL);
    if (!sk || t_zbone_b >= skel_nbones(sk)) return 0;
    uint64_t w = t_u64(sk + SKEL_WORLD);
    if (!w || !mem_ok(w + (uint64_t)t_zbone_b * WORLD_SIZE, WORLD_SIZE)) return 0;
    memcpy(W, (const void *)(uintptr_t)(w + (uint64_t)t_zbone_b * WORLD_SIZE), WORLD_SIZE);
    for (int k = 0; k < 16; k++)
        if (!finite_f(W[k]) || W[k] > 1e6f || W[k] < -1e6f) return 0;
    return 1;
}

// The seat in the world: the bone's matrix W (columns) times the offset M (row-major 3x4): position and a row-major
// 3x3 rotation.
static void seat_world(const float W[16], const float M[12], float pos[3], float rot[9]) {
    for (int i = 0; i < 3; i++) {
        for (int j = 0; j < 3; j++)
            rot[3 * i + j] = W[i] * M[j] + W[4 + i] * M[4 + j] + W[8 + i] * M[8 + j];
        pos[i] = W[i] * M[3] + W[4 + i] * M[7] + W[8 + i] * M[11] + W[12 + i];
    }
}

static void zelda_status_idle(void) {
    t_st.zstate = ZS_IDLE, t_st.zdist = -1.0f, t_st.zas[0] = 0, t_st.zmanual = 0;
}

// She is gone without us removing her (a reload, Game Over, a stage change): forget her without touching her.
static void zelda_forget(const char *why, double t) {
    if (t_st.zstate == ZS_LIVE) t_st.zlosses++;
    zero_link(&g_zlink);
    memset(&g_zhandle, 0, sizeof g_zhandle);
    t_zfollow = 0;
    zelda_status_idle();
    t_zretry = t + 1.0;
    if (why) note("Zelda: %s; again soon", why);
}

// Remove her (off): a pending request is released, a live Zelda deleted and watched until she is gone.
static void zelda_remove(struct Cpu *c, double t) {
    if (t_st.zstate == ZS_PENDING) {
        GCALL1(c, HANDLE_RELEASE, &g_zhandle);  // (an actor created but not taken is deleted)
        GCALL1(c, HANDLE_CLEAR, &g_zhandle);
    } else if (t_st.zstate == ZS_LIVE) {
        if (GCALL1(c, LINK_VALID, &g_zlink) & 1) {
            uint64_t proc = t_u64(g_zlink.data + LINKDATA_PROC);
            if (proc) {
                g_ztrash = g_zlink;
                t_ztrash_t = t;
                gcall(c, DELETE_LATER, proc, 0, 0, 0, 0, 0, 0);
            }
        }
        GCALL1(c, LINK_RESET, &g_zlink);
        zero_link(&g_zlink);
        note("Zelda: off");
    }
    t_zfollow = 0;
    zelda_status_idle();
}

static void zelda_fail(const char *why, double t, int counts) {
    zelda_status_idle();
    if (counts && ++t_st.zfails >= Z_MAX_FAILS) {
        t_st.zgiveup = ZG_LOAD;
        note("Zelda: %s; gave up after %d tries (her pack didn't load)", why, Z_MAX_FAILS);
        return;
    }
    t_zretry = t + Z_RETRY_S;
    note("Zelda: %s; trying again", why);
}

// Ask for her, already in her seat (Link's matrix x the seat in his root space).
static void zelda_request(struct Cpu *c, uint64_t pi, uint64_t link, float back_m, double t) {
    uint64_t creator = t_u64(CREATOR_PTR), heap = spawn_heap();
    float lp[3] = {t_f32(pi + PI_POS), t_f32(pi + PI_POS + 4), t_f32(pi + PI_POS + 8)};
    if (!creator || !heap || (lp[0] == 0 && lp[1] == 0 && lp[2] == 0) || !mem_ok(link + ACTOR_MTX, 48)) {
        t_zretry = t + 1.0;  // (no world yet: the title screen reads 0 0 0, and a spawn there crashed the game)
        return;
    }
    t_st.zevent = event_running();
    if (t_u8(creator + CREATOR_BLOCKED) || t_st.zevent == 1) {
        t_zretry = t + 0.5;
        return;
    }
    float m[12];
    for (int k = 0; k < 12; k++) m[k] = rdf(link + ACTOR_MTX + 4 * (uint64_t)k);
    for (int k = 0; k < 12; k++)
        if (!finite_f(m[k])) {
            t_zretry = t + 1.0;
            return;
        }
    float off[3] = {0.0f, SEAT_ROOT_UP, SEAT_BACK - back_m};
    float out[12] = {m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7], m[8], m[9], m[10], m[11]};
    for (int i = 0; i < 3; i++) out[4 * i + 3] += m[4 * i] * off[0] + m[4 * i + 1] * off[1] + m[4 * i + 2] * off[2];
    memcpy(g_zmtx, out, sizeof out);
    GCALL1(c, HANDLE_CLEAR, &g_zhandle);  // (a failed request leaves its flag set: always start from zero)
    memset(&g_zpack, 0, sizeof g_zpack);
    g_zpack.vptr = PACK_VPTR;
    GCALL1(c, PACK_CLEAR, g_zpack.buf);
    g_key_m.vptr = STRING_OBJ_VPTR, g_key_m.str = KEY_M;
    gcall(c, PACK_ADD, P(g_zpack.buf), P(g_zmtx), P(&g_key_m), 0x30, 7, 0, 0);
    uint64_t ok = gcall(c, REQUEST_CREATE, creator, P(ZV_SLOT_NAME), heap, P(&g_zhandle), P(&g_zpack), 0, 1) & 1;
    if (!ok) {
        GCALL1(c, HANDLE_CLEAR, &g_zhandle);
        zelda_fail("request refused (the game is busy, or no pack)", t, 1);
        return;
    }
    t_st.zstate = ZS_PENDING;
    t_zsince = t;
    t_zstage_heap = heap, t_zstage_link = link;
}

// Glue her to Link's Spine_2 with one of our two follows (the game's own bone follow).
static void zelda_attach(struct Cpu *c, uint64_t z, uint64_t link, const float seat[12], double t) {
    int k = t_zfollow_next;
    t_zfollow_next ^= 1;
    Follow *f = &g_follow[k];
    if (!t_follow_built[k]) {
        GCALL1(c, FOLLOW_CTOR, f);
        t_follow_built[k] = 1;
    }
    GCALL3(c, LINK_SET, f->b + FOLLOW_TARGET, link, 0);
    uint64_t bone = P(BONE_SPINE2);
    uint32_t again = 0xffffffffu, mode = 0;
    f->b[FOLLOW_PHYS] = 0;
    memcpy(f->b + FOLLOW_BONE, &bone, 8);
    memcpy(f->b + FOLLOW_INDEX, &again, 4);
    memcpy(f->b + FOLLOW_OFFSET, seat, 48);
    memcpy(f->b + FOLLOW_MODE, &mode, 4);
    memcpy(t_zseat, seat, sizeof t_zseat);
    t_zfollow = P(f);
    gcall(c, FOLLOW_SET, z, P(f), 0, 0, 0, 0, 0);
    t_zcheck_set = 1, t_zresets = 0;
    t_st.zmanual = 0;
    t_zfar_since = 0, t_zdist_t = 0;
    (void)t;
}

static void zelda_pending(struct Cpu *c, uint64_t link, const float seat[12], double t) {
    uint64_t unit = g_zhandle.unit;
    if (unit && (!mem_ok(unit + 8, 8) || rd64(unit + 8) != P(&g_zhandle))) {  // (the unit went to someone else)
        GCALL1(c, HANDLE_CLEAR, &g_zhandle);
        zelda_fail("request lost (stage change?)", t, 0);
        return;
    }
    if (GCALL1(c, HANDLE_READY, &g_zhandle) & 1) {
        uint64_t proc = GCALL1(c, HANDLE_PEEK, &g_zhandle);
        uint64_t info = proc ? gcall(c, ACTOR_TYPEINFO, 0, 0, 0, 0, 0, 0, 0) : 0;
        uint64_t isa = proc ? t_u64(t_u64(proc) + 0x10) : 0;
        uint64_t is_actor = proc && info && isa ? GCALL2(c, isa, proc, info) & 1 : 0;
        if (!is_actor) {
            GCALL1(c, HANDLE_RELEASE, &g_zhandle);
            GCALL1(c, HANDLE_CLEAR, &g_zhandle);
            zelda_fail("the created thing is not an actor; released", t, 1);
            return;
        }
        if (t_u32(proc + ACTOR_OWN_FOLLOW) & 0x20) {  // (the follow setter would refuse her: not our pack)
            GCALL1(c, HANDLE_RELEASE, &g_zhandle);
            GCALL1(c, HANDLE_CLEAR, &g_zhandle);
            zelda_status_idle();
            t_st.zgiveup = ZG_OWN_FOLLOW;
            note("Zelda: her actor makes its own follow (not our pack?); Zelda off");
            return;
        }
        GCALL3(c, SET_MTX, proc, g_zmtx, 0);
        GCALL1(c, HANDLE_TAKE, &g_zhandle);
        GCALL3(c, LINK_SET, &g_zlink, proc, 0);
        note("Zelda: on Link's back (%.2f s after the request)", t - t_zsince);
        t_st.zstate = ZS_LIVE;
        t_st.zspawns++;
        t_st.zride = 0;
        t_zsince = t;
        t_zride_asks = 0, t_zride_ask_t = 0, t_zas_t = 0;
        zelda_attach(c, proc, link, seat, t);
        return;
    }
    if (GCALL1(c, HANDLE_FAILED, &g_zhandle) & 1) {
        GCALL1(c, HANDLE_RELEASE_FAILED, &g_zhandle);
        GCALL1(c, HANDLE_CLEAR, &g_zhandle);
        zelda_fail("creation failed", t, 1);
        return;
    }
    if (t - t_zsince > Z_PENDING_S) {
        GCALL1(c, HANDLE_RELEASE, &g_zhandle);
        GCALL1(c, HANDLE_CLEAR, &g_zhandle);
        zelda_fail("not ready in time; released", t, 1);
    }
}

static void as_play(struct Cpu *c, uint64_t actor, const char *name) {
    g_fake_action[0] = 0, g_fake_action[1] = actor;
    swmod_set_s(c, 0, -1.0f);  // morph frames: -1 = the list's own
    gcall(c, AS_PLAY, P(g_fake_action), P(name), 1, 0, 0, 0, 0);  // keep_if_same, slot 0, sub 0
}

// Her riding pose: asked once she has her own animation holder; it must play within Z_RIDE_S (our pack's mark),
// else she is not our pack: deleted, Zelda off. Then checked every Z_AS_S (asked again if something changed it).
static void zelda_as_read(struct Cpu *c, uint64_t h) {
    uint64_t s = gcall(c, AS_CURRENT, h, 0, 0, 0, 0, 0, 0);
    t_text(s ? t_u64(s + 8) : 0, t_st.zas, sizeof t_st.zas);
}

static int zelda_anim(struct Cpu *c, uint64_t z, double t) {
    uint64_t h = t_u64(z + ACTOR_AS);
    int own = h && t_u64(h + AS_OWNER) == z && (int32_t)t_u32(h + AS_SLOTS) > 0;
    if (!t_st.zride) {
        if (own) {
            if (t_zride_asks && t - t_zride_ask_t >= 0.1) {  // (read back what plays)
                zelda_as_read(c, h);
                if (!strcmp(t_st.zas, AS_RIDE)) {
                    t_st.zride = 1;
                    t_zas_t = t + Z_AS_S;
                    return 1;
                }
            }
            if (!t_zride_asks || (t_zride_asks < 4 && t - t_zride_ask_t >= 0.1)) {
                as_play(c, z, AS_RIDE);
                t_zride_asks++, t_zride_ask_t = t;
            }
        }
        return t - t_zsince <= Z_RIDE_S;  // (no own holder or no Ride by then: not our pack)
    }
    if (t >= t_zas_t && own) {
        t_zas_t = t + Z_AS_S;
        zelda_as_read(c, h);
        if (strcmp(t_st.zas, AS_RIDE)) {
            as_play(c, z, AS_RIDE);
            t_st.zreasserts++;
        }
    }
    return 1;
}

static void zelda_live(struct Cpu *c, uint64_t link, const float seat[12], double t) {
    if (link != t_zstage_link || spawn_heap() != t_zstage_heap) {
        zelda_forget("stage changed", t);
        return;
    }
    if (!(GCALL1(c, LINK_VALID, &g_zlink) & 1)) {
        zelda_forget("the game removed her (a reload?)", t);
        return;
    }
    uint64_t z = t_u64(g_zlink.data + LINKDATA_PROC);
    if (!z) {
        zelda_forget("her actor is gone", t);
        return;
    }
    Follow *f = t_zfollow == P(&g_follow[0]) ? &g_follow[0] : t_zfollow == P(&g_follow[1]) ? &g_follow[1] : NULL;
    if (!t_st.zmanual && f) {
        uint64_t cur = t_u64(z + ACTOR_FOLLOW);
        if (cur != t_zfollow) {
            if (t_zcheck_set || t_zresets >= 3) {  // refused, or taken away again and again: plan B
                t_st.zmanual = 1;
                note("Zelda: the game %s her follow; the mod moves her itself", t_zcheck_set ? "refused" : "drops");
            } else {
                gcall(c, FOLLOW_SET, z, t_zfollow, 0, 0, 0, 0, 0);
                t_zresets++, t_st.zrefollows++;
            }
        }
        // the follow must still name Link (if Link's own follow target were set, the game would copy it into ours)
        Link fl;
        memcpy(&fl, f->b + FOLLOW_TARGET, sizeof fl);
        if (!t_st.zmanual && (!fl.data || t_u64(fl.data + LINKDATA_PROC) != link)) {
            GCALL3(c, LINK_SET, f->b + FOLLOW_TARGET, link, 0);
            uint32_t again = 0xffffffffu;
            memcpy(f->b + FOLLOW_INDEX, &again, 4);
            t_st.zretargets++;
        }
        if (!t_st.zmanual && memcmp(t_zseat, seat, sizeof t_zseat)) {  // (seat settings changed)
            memcpy(f->b + FOLLOW_OFFSET, seat, 48);
            memcpy(t_zseat, seat, sizeof t_zseat);
        }
    }
    t_zcheck_set = 0;
    gcall(c, VIS_COPY, z, link, 0, 0, 0, 0, 0);  // (hidden while a cutscene hides Link, as his armor is)
    float W[16];
    int have = link_spine(link, W);
    if (have) seat_world(W, seat, g_zpos, g_zrot);
    if (t_st.zmanual && have) gcall(c, SET_TRANSFORM, z, P(g_zpos), P(g_zrot), 0, 0, 0, 0);
    if (t >= t_zdist_t) {
        t_zdist_t = t + Z_DIST_S;
        if (have && mem_ok(z + ACTOR_MTX, 48)) {
            float dx = rdf(z + ACTOR_MTX + 0x0c) - g_zpos[0], dy = rdf(z + ACTOR_MTX + 0x1c) - g_zpos[1];
            float dz = rdf(z + ACTOR_MTX + 0x2c) - g_zpos[2];
            float d = __builtin_sqrtf(dx * dx + dy * dy + dz * dz);
            t_st.zdist = finite_f(d) ? d : -1.0f;
            if (!t_st.zmanual && t_st.zdist > Z_FAR_M) {
                if (!t_zfar_since) t_zfar_since = t;
                if (t - t_zfar_since >= Z_FAR_S) {
                    t_st.zmanual = 1;
                    note("Zelda: %.2f m off her seat for %.0f s; the mod moves her itself", t_st.zdist, Z_FAR_S);
                }
            } else {
                t_zfar_since = 0;
            }
        } else {
            t_st.zdist = -1.0f;
        }
    }
    if (!zelda_anim(c, z, t)) {
        zelda_remove(c, t);
        t_st.zgiveup = ZG_NOT_OURS;
        note("Zelda: no Ride animation (not our pack? run tools/zelda_view.py install); Zelda off");
    }
}

static void zelda_trash(double t) {
    if (!t_ztrash_t) {
        t_st.zstray = 0;
        return;
    }
    if (!link_ok(&g_ztrash) || t - t_ztrash_t > Z_FORGET_S) {
        zero_link(&g_ztrash);
        t_ztrash_t = 0;
        t_st.zstray = 0;
    } else {
        t_st.zstray = t - t_ztrash_t > Z_STRAY_S;
    }
}

static void zelda_tick(struct Cpu *c, uint64_t pi, uint64_t link, int want, const float seat[12], float back_m,
                       int paused, double t) {
    zelda_trash(t);
    if (!want || t_st.zgiveup) {  // (a pending request is released; a live Zelda deleted, or forgotten with Link)
        if (t_st.zstate == ZS_PENDING || (t_st.zstate == ZS_LIVE && link)) zelda_remove(c, t);
        else if (t_st.zstate == ZS_LIVE) zelda_forget(NULL, t);
        return;
    }
    if (!t_zpack_known) {
        t_zpack_known = g_pack_fn() ? 1 : -1;
        t_st.zpack = t_zpack_known;
        if (t_zpack_known < 0) {
            t_st.zgiveup = ZG_NO_PACK;
            note("Zelda: her pack isn't deployed (run tools/zelda_view.py install, then deploy)");
            return;
        }
    }
    if (!link) {  // (loading, the title screen: she went with the stage; a request is not attached to nobody)
        if (t_st.zstate == ZS_LIVE) zelda_forget("Link is gone (loading)", t);
        else if (t_st.zstate == ZS_PENDING) zelda_remove(c, t), t_zretry = t + 1.0;
        return;
    }
    if (paused) return;
    switch (t_st.zstate) {
    case ZS_IDLE:
        if (t >= t_zretry && t_ztrash_t == 0) zelda_request(c, pi, link, back_m, t);
        break;
    case ZS_PENDING:
        zelda_pending(c, link, seat, t);
        break;
    case ZS_LIVE:
        zelda_live(c, link, seat, t);
        if (t_st.zstate == ZS_LIVE && t - t_zsince > 10.0) t_st.zfails = 0;  // (she stayed: the row of tries ends)
        break;
    }
}

// ------------------------------------------------------------------ the tick (a game worker thread, once per frame)

static int g_in_tick;
static uint64_t t_link;
static int t_on;                 // the egg as last run (an ON edge starts every part over)
static Settings t_set;
static double t_set_t, t_win_start;
static int64_t t_win_ticks;
static unsigned long t_tid[8];
static int t_ntid;

static void note_thread(void) {
    unsigned long id = GetCurrentThreadId();
    for (int i = 0; i < t_ntid; i++)
        if (t_tid[i] == id) return;
    if (t_ntid < 8) t_tid[t_ntid++] = id;
    t_st.threads = t_ntid;
}

// Link changed (a reload, the title screen): forget everything that belonged to the old one, touching nothing.
static void forget_link(double t) {
    buff_forget();
    t_happlied = 0, t_cut_on = 0;
    __atomic_store_n(&g_heavy_holder, 0, __ATOMIC_RELEASE);
    if (t_st.zstate == ZS_LIVE) zelda_forget("Link changed (a reload)", t);
    t_zbone_u = t_zbone_b = -1, t_zbone_link = 0;
}

static void tick_body(struct Cpu *c, double t) {
    t_now = t;
    t_st.ticks++;
    if (!t_win_start) t_win_start = t;
    t_win_ticks++;
    if (t - t_win_start >= 1.0) {
        t_st.rate = (double)t_win_ticks / (t - t_win_start);
        t_win_start = t, t_win_ticks = 0;
    }
    t_st.last_t = t;
    note_thread();
    if (__atomic_exchange_n(&g_settings_dirty, 0, __ATOMIC_ACQUIRE) || t - t_set_t >= 0.25 || t < t_set_t) {
        t_set = read_settings();
        t_set_t = t;
    }
    t_st.set = t_set;
    int on = __atomic_load_n(&g_want, __ATOMIC_ACQUIRE) && t_set.secret;
    if (on && !t_on) {  // ON: every part starts over (Zelda's pack is checked again, her give-up forgotten)
        t_zpack_known = 0, t_st.zgiveup = ZG_NONE, t_st.zfails = 0, t_zretry = 0;
        note("ON");
    } else if (!on && t_on) {
        note("OFF");
    }
    t_on = on;
    t_st.on = on;
    if (!t_st.strings) t_st.strings = strings_ok() ? 1 : -1;  // (once: main's data is there by the first frame)

    uint64_t pi = t_u64(PLAYER_INFO_PTR);
    uint64_t link = pi ? t_u64(pi + PI_PLAYER) : 0;
    if (link && t_u64(link) != PLAYER_VPTR) link = 0;
    if (link != t_link) {
        forget_link(t);
        t_link = link;
    }
    t_st.link = link;
    int paused = botw_paused(api);

    // part A: the buff (only while the skin hook is there)
    int buff = on && t_set.buff != BUFF_OFF && g_hook_skin;
    int shoulders = buff && t_set.shoulders && g_hook_world;
    int was = t_st.buff_on || t_st.shoulders_on;
    buff_tick(link, buff, t_set.buff, shoulders, t, was != (buff || shoulders) || t_st.shoulders_on != shoulders);
    t_st.buff_on = buff, t_st.shoulders_on = shoulders;
    int64_t copies = __atomic_load_n(&g_n_copies, __ATOMIC_RELAXED);
    t_copies_avg += ((double)(copies - t_copies_seen) - t_copies_avg) * (t_copies_ticks ? 0.05 : 1.0);
    t_copies_seen = copies, t_copies_ticks++;
    t_st.copies_per_frame = t_copies_avg;

    // part B: heavy swings (also without the setter hook: the draw may then look two-handed once)
    if (!paused) heavy_tick(c, link, on && t_set.heavy);

    // part C: Zelda
    float back_m = t_set.seat_back / 100.0f, side_m = t_set.seat_side / 100.0f;
    if (buff) back_m += t_set.buff == BUFF_EXTREME ? BUFF_BACK_EXTREME : BUFF_BACK_BUFF;
    float seat[12];
    seat_offset(seat, back_m, side_m);
    zelda_tick(c, pi, link, on && t_set.zelda, seat, back_m, paused, t);
}

static void run_tick(struct Cpu *c) {
    if (__atomic_exchange_n(&g_in_tick, 1, __ATOMIC_ACQUIRE)) return;  // (never two at once)
    SavedRegs saved;
    save_regs(c, &saved);
    tick_body(c, now_s());
    restore_regs(c, &saved);
    lock();
    g_st = t_st;
    unlock();
    __atomic_store_n(&g_in_tick, 0, __ATOMIC_RELEASE);
}

static void frame_after(struct Cpu *c) { run_tick(c); }  // the after hook on the per-frame job
static void frame_fn(struct Cpu *c) { run_tick(c); }     // botw_on_frame (no hook slots)

// ------------------------------------------------------------------ the command (Savage Wind's frame thread)

static void reply_hex(SwModReply *r, const char *key, uint64_t v) {
    char buf[24];
    snprintf(buf, sizeof buf, "0x%llx", (unsigned long long)v);
    api->reply_str(r, key, buf);
}

static int tick_running(const Status *s) {
    if (g_tick_src == TICK_NONE) return 0;
    return g_tick_src == TICK_HOOK || s->ticks > 0 || botw_frames() > 0;
}

// The one short note for the reply (the most useful one), or NULL.
static const char *egg_note(const Status *s, const Settings *set, int on) {
    if (!on) return NULL;
    if (!g_hook_skin && !g_hook_world && !g_hook_setter && !g_hook_frame) return "needs the hook rebuild";
    int tick = tick_running(s) || g_tick_src == TICK_ON_FRAME;  // (on_frame: frames may just not have come yet)
    if (set->zelda) {
        if (!g_pack_fn()) return "no Zelda: run tools/zelda_view.py install";
        if (s->zgiveup == ZG_LOAD) return "no Zelda: her pack didn't load";
        if (s->zgiveup == ZG_NOT_OURS || s->zgiveup == ZG_OWN_FOLLOW) return "no Zelda: not her pack";
    }
    if (set->buff != BUFF_OFF && !g_hook_skin) return "buff needs the hook rebuild";
    if (set->heavy && s->strings < 0) return "heavy swings don't work on this game";
    if (!tick) return "Zelda and heavy swings need the hook rebuild";
    return NULL;
}

static void report_fields(SwModReply *r, const Status *s) {
    api->reply_str(r, "version", ZV_VERSION);
    api->reply_str(r, "tick", TICK_NAMES[g_tick_src]);
    api->reply_int(r, "ticks", s->ticks);
    api->reply_num(r, "ticks_per_s", s->rate);
    api->reply_num(r, "since_last_tick_s", s->ticks ? now_s() - s->last_t : -1.0);
    api->reply_int(r, "threads", s->threads);
    api->reply_bool(r, "hook.skin", g_hook_skin != 0);
    api->reply_bool(r, "hook.world", g_hook_world != 0);
    api->reply_bool(r, "hook.setter", g_hook_setter != 0);
    api->reply_bool(r, "hook.frame", g_hook_frame != 0);
    reply_hex(r, "link", s->link);
    static const char *const presets[] = {"off", "buff", "extreme"};
    api->reply_str(r, "buff.preset", presets[s->set.buff >= 0 && s->set.buff <= 2 ? s->set.buff : 0]);
    api->reply_bool(r, "buff.on", s->buff_on != 0);
    api->reply_bool(r, "buff.shoulders", s->shoulders_on != 0);
    api->reply_str(r, "buff.mode", __atomic_load_n(&g_inplace, __ATOMIC_RELAXED) ? "inplace" : "copy");
    api->reply_int(r, "buff.targets", s->ntargets);
    api->reply_int(r, "buff.link_targets", s->link_targets);
    api->reply_int(r, "buff.armor_targets", s->armor_targets);
    api->reply_int(r, "buff.restoring", s->restoring);
    api->reply_num(r, "buff.copies_per_frame", s->copies_per_frame);
    api->reply_int(r, "buff.copies", __atomic_load_n(&g_n_copies, __ATOMIC_RELAXED));
    api->reply_int(r, "buff.busy", __atomic_load_n(&g_n_busy, __ATOMIC_RELAXED));
    api->reply_int(r, "buff.bad", __atomic_load_n(&g_n_bad, __ATOMIC_RELAXED));
    api->reply_int(r, "buff.shoulder_writes", __atomic_load_n(&g_n_stagea, __ATOMIC_RELAXED));
    api->reply_bool(r, "heavy.on", s->heavy_on != 0);
    api->reply_int(r, "heavy.strings", s->strings);
    api->reply_int(r, "heavy.weapon_type", s->wtype);
    api->reply_int(r, "heavy.moveset", s->moveset);
    api->reply_int(r, "heavy.drawn_class", s->drawn);
    api->reply_str(r, "heavy.hand", s->hand);
    api->reply_str(r, "heavy.param", s->param);
    api->reply_str(r, "heavy.body", s->body);
    api->reply_bool(r, "heavy.applied", s->applied != 0);
    api->reply_bool(r, "heavy.pending", s->pending != 0);
    api->reply_bool(r, "heavy.attacking", s->attacking != 0);
    api->reply_int(r, "heavy.swaps", __atomic_load_n(&g_n_swaps, __ATOMIC_RELAXED));
    api->reply_int(r, "heavy.setter_seen", __atomic_load_n(&g_n_setter_seen, __ATOMIC_RELAXED));
    api->reply_int(r, "heavy.setter_calls", s->setter_calls);
    char sw[SWINGS * 28];
    int n = 0, first = s->nswings > SWINGS ? s->nswings - SWINGS : 0;
    sw[0] = 0;
    for (int i = first; i < s->nswings && n < (int)sizeof sw - 32; i++) {
        const Swing *w = &s->swing[i % SWINGS];
        n += snprintf(sw + n, sizeof sw - (size_t)n, "%s%s:%d:%.0f", i > first ? "," : "", w->name, w->ticks, w->ms);
    }
    api->reply_str(r, "heavy.swings", sw);  // name:ticks:ms, oldest first
    api->reply_int(r, "heavy.nswings", s->nswings);
    api->reply_str(r, "zelda.state", ZS_NAMES[s->zstate >= 0 && s->zstate <= ZS_LIVE ? s->zstate : 0]);
    api->reply_str(r, "zelda.follow", s->zmanual ? "manual" : "game");
    api->reply_num(r, "zelda.dist_m", s->zdist);
    api->reply_str(r, "zelda.as", s->zas);
    api->reply_bool(r, "zelda.ride", s->zride != 0);
    api->reply_int(r, "zelda.pack", s->zpack);
    api->reply_str(r, "zelda.gave_up", ZG_NAMES[s->zgiveup >= 0 && s->zgiveup <= ZG_OWN_FOLLOW ? s->zgiveup : 0]);
    api->reply_int(r, "zelda.event", s->zevent);
    api->reply_int(r, "zelda.stray", s->zstray);
    api->reply_int(r, "zelda.fails", s->zfails);
    api->reply_int(r, "zelda.spawns", s->zspawns);
    api->reply_int(r, "zelda.losses", s->zlosses);
    api->reply_int(r, "zelda.retargets", s->zretargets);
    api->reply_int(r, "zelda.refollows", s->zrefollows);
    api->reply_int(r, "zelda.reasserts", s->zreasserts);
    api->reply_str(r, "zelda.slot", ZV_SLOT);
    api->reply_str(r, "last", s->last);
}

static void cmd_egg(int argc, char **argv, SwModReply *r) {
    Settings set = read_settings();
    if (!set.secret) {  // (Savage Wind's own wording for a command it doesn't have)
        api->reply_fail(r, "Unknown command: %s", argv[0]);
        return;
    }
    int dev = api->dev_mode && api->dev_mode();
    const char *sub = argc > 1 ? argv[1] : NULL;
    int status = 0;
    if (!sub) {
        int on = !__atomic_load_n(&g_want, __ATOMIC_ACQUIRE);
        __atomic_store_n(&g_want, on, __ATOMIC_RELEASE);
    } else if (argc == 2 && (!strcmp(sub, "on") || !strcmp(sub, "off"))) {
        __atomic_store_n(&g_want, sub[1] == 'n', __ATOMIC_RELEASE);
    } else if (argc == 2 && !strcmp(sub, "status")) {
        status = 1;
    } else if (dev && argc == 3 && !strcmp(sub, "mode") && (!strcmp(argv[2], "copy") || !strcmp(argv[2], "inplace"))) {
        __atomic_store_n(&g_inplace, argv[2][0] == 'i', __ATOMIC_RELEASE);
        status = 1;
    } else {
        api->reply_fail(r, "usage: %s [on|off|status]", argv[0]);
        return;
    }
    Status s;
    lock();
    s = g_st;
    unlock();
    int on = __atomic_load_n(&g_want, __ATOMIC_ACQUIRE);
    const char *why = egg_note(&s, &set, on);
    api->reply_bool(r, "on", on != 0);
    if (status && dev) report_fields(r, &s);
    if (why) api->reply_str(r, "note", why);
    if (why) api->reply_text(r, "Link from Zelda's view: %s (%s)", on ? "ON" : "OFF", why);
    else api->reply_text(r, "Link from Zelda's view: %s", on ? "ON" : "OFF");
}

// A player-safe command (SWMOD_CMD_PLAYER), so players can type it without developer mode. It changes nothing that is
// saved and every part undoes itself. Empty help: it isn't advertised (Savage Wind's `help` still lists it).
static const SwModCommand commands[] = {
    {ZV_WORD, "", "", cmd_egg, SWMOD_CMD_PLAYER},
};

// Hook one function if this game code has a slot for it. 1: hooked.
static int try_hook(uint64_t addr, SwModHookBefore before, SwModHookAfter after, SwModHookReplace replace) {
    if (!SWMOD_API_OK(api, 2) || !api->can_hook(addr)) return 0;
    return api->hook(addr, before, after, replace) == SWMOD_HOOK_OK;
}

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    if (!botw_version_ok(api)) {
        api->log("zelda_view: made for game version %s, this is %s", BOTW_GAME_VERSION, api->game_version);
        return 2;
    }
    QueryPerformanceFrequency(&g_qpf);
    if (g_qpf <= 0) g_qpf = 1;
    g_hook_skin = try_hook(SKIN_UPLOAD, NULL, NULL, skin_replace);
    g_hook_world = try_hook(WORLD_CALC, world_before, NULL, NULL);
    g_hook_setter = try_hook(STR_SETTER, setter_before, NULL, NULL);
    g_hook_frame = try_hook(FRAME_JOB, NULL, frame_after, NULL);
    g_tick_src = g_hook_frame ? TICK_HOOK : botw_on_frame(api, frame_fn) ? TICK_ON_FRAME : TICK_NONE;
    if (has_settings()) api->setting_watch(api, on_setting, NULL);
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("zelda_view: ready (hooks: skin %d, shoulders %d, swings %d, frame %d; tick %s)", g_hook_skin,
             g_hook_world, g_hook_setter, g_hook_frame, TICK_NAMES[g_tick_src]);
    return 0;
}
