// emotes: Link's emotes and a ragdoll toggle (Breathalyzer, by giruka). docs/emotes.md.
//
// Plays the animation sets ZEmote_<id> that tools/emote_build.py builds on the player's PC (the package mod
// giruka.emotes-anims: our own keyframed clips, a few game clips by name, one copy of a game set) on Link's body
// slot, from Link's own per-frame update, never the frame job (the ghost mod wraps that one).
//
//   - Start: the keys 5 6 7 8 9 0 (and the numpad's), the console command `emote`, or the Play buttons on the Mods
//     page. Only while Link stands still with nothing in his hands: the PlayerWait action under PlayerNormal, or
//     (AI not found) the body slot has shown Wait for 0.5 s, he doesn't move (3D) and no talk or cutscene set was
//     seen in the last 2 s.
//   - Set names: every name we pass lives in one table for good (intern). Slot 0 is read back by its text (the Game
//     check showed Link's holder keeps its own copy of the name, not our char*); a char* into our table (outside the
//     game's memory, so the guest-window check would refuse it) is still read directly. "Ours" = a ZEmote_ set, or
//     the set we asked for while one runs (a fallback game set).
//   - Stop: Link moves, jumps, attacks or is hit (the game takes the body slot back: we touch nothing then), the same
//     key again, or `emote stop`. A one-shot goes back to Wait by itself.
//   - Ragdoll: plays ZEmote_ragdoll (Link lies flat) until he moves. The game's own slip-and-fall physics is built in
//     but stays off until the Game check passes (developer command `emotedbg rdphys on`).
//   - Missing anims package: wave, thumbs up, selfie and victory play a game set instead; the others are refused
//     with a hint.
//
// Game addresses (1.9.0, docs/addresses.md and out/research/emotes-play.md): Link's update 0x7000cd1ee8 (wrapped with
// hle_override), play an animation set by name 0x700151e42c (as the ghost uses it), what slot 0 plays 0x70014a0040,
// finished 0x70014a009c, the alive-ragdoll switch 0x7000cae8e0. Every pointer is checked (VirtualQuery) before it is
// read; a load (Link's pointer changes) forgets everything without writing.
//
// Why the game put Wait back at once (static study 2026-11-13, the Game check confirms): each frame, before Link's
// update, Link's AI (his RootAi, Link +0x558) runs the standing action PlayerWait; its calc 0x7000c9d0bc runs the
// walk/run code 0x7000cf1610, which (standing: Link +0xcec bit 0, set by PlayerWait's start) runs his standing animation
// code 0x7000ce0c44. That code plays Wait (0x7000ce1364 -> 0x7000cc7edc, forced) whenever slot 0's name isn't Wait or
// one of the game's own standing sets (StaminaOut, WaitHotCold, Get...). The holder keeps the set's name as a pointer
// (the string object AS_CURRENT returns, +8). So while our set plays, that pointer shows the game's own "Wait" text
// during PlayerWait's calc only (hle_override of 0x7000c9d0bc: it is called only through its vtable) and ours again right
// after: the standing code leaves slot 0 alone, everyone else (us, multiplayer) reads our name. Developer switch
// `emotedbg mask off|calc|always` (always: Wait the whole emote, for a replay found elsewhere).
//
// The game-thread code (tick, PlayerWait's calc) and the commands (frame thread) share only g_req / g_snap under a spin
// lock.
#include <stdint.h>
#include <string.h>

#include "swmod.h"
#include "botw.h"
#include "emote_list.h"

#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wbuiltin-requires-header"
int snprintf(char *buf, size_t n, const char *fmt, ...);
#pragma clang diagnostic pop
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
__declspec(dllimport) short __stdcall GetAsyncKeyState(int vk);
__declspec(dllimport) void *__stdcall GetForegroundWindow(void);
__declspec(dllimport) unsigned long __stdcall GetWindowThreadProcessId(void *hwnd, unsigned long *pid);
__declspec(dllimport) unsigned long __stdcall GetCurrentProcessId(void);
__declspec(dllimport) unsigned long __stdcall GetEnvironmentVariableA(const char *name, char *buf, unsigned long size);

#define EMOTES_VERSION "0.1.0"

// ------------------------------------------------------------------ game addresses (1.9.0)
#define LINK_UPDATE 0x7000cd1ee8ull      // Link's per-frame update (x0 = Link); one pointer to it, no direct calls
#define PLAYER_VPTR 0x7001cd7bb8ull      // Link's actor class
#define AS_PLAY 0x700151e42cull          // (action, name, keep_if_same, slot, sub; s0 morph): reads only action+8
#define AS_CURRENT 0x70014a0040ull       // (holder, slot, sub) -> string object of the playing set (char* at +8)
#define AS_FINISHED 0x70014a009cull      // (holder, slot, sub) -> 1 when a one-shot ended (also: no such slot)
#define ACTOR_AS 0x560                   // actor: animation holder
#define AS_OWNER 0xd8                    // holder: its actor
#define RAGDOLL_ON 0x7000cae8e0ull       // (PlayerNormal AI): switch to the alive ragdoll (slip and fall)
#define AI_SCAN 0x2600                   // bytes of Link scanned for his AI root (once per load)
#define AI_CUR 0x1e                      // AI node: u16 current child (0xffff none)
#define AI_NCHILD 0x28                   // AI node: u32 child count
#define AI_CHILDREN 0x30                 // AI node: child pointer array
#define AI_ACTOR 0x8                     // AI node: its actor
#define VT_ROOT 0x7001cd62e8ull          // PlayerRoot
#define VT_NORMAL 0x7001cd60a0ull        // PlayerNormal
#define VT_WAIT 0x7001cd4088ull          // PlayerWait
#define VT_MOVE 0x7001ccfff8ull          // PlayerMove
#define VT_SLIP 0x7001d31628ull          // PlayerSlippingDown (the alive ragdoll)
#define VT_ROOTAI 0x7001d1dec0ull        // RootAi: the top of an actor's AI tree (Link's: PlayerRoot below it)
#define LINK_ROOT_AI 0x558               // actor: its RootAi (the actor's calc calls its vf 0xa0 before Link's update)
#define AI_DEPTH 3                       // RootAi -> PlayerRoot -> PlayerNormal: levels searched below the top
#define PW_CALC 0x7000c9d0bcull          // PlayerWait's per-frame calc (its vtable +0xf8 only: no direct calls)
#define WAIT_TEXT 0x7001626a8dull        // the game's own "Wait" (what its standing code compares slot 0 with)
#define STR_PTR 8                        // a string object (what AS_CURRENT returns): its char*, kept by pointer
#define SLIP_DAMAGE 0x28                 // slip-fall action: pointer to its damage value
#define LINK_RAGDOLL_MGR 0x1870          // Link: ragdoll manager
#define RD_STILL 0x138                   // manager: "still" timer (gets up when it reaches +0x1dc)
#define RD_STILL2 0x13c                  // manager: second timer (... +0x1e0)
#define RD_LIMIT 0x1dc
#define RD_LIMIT2 0x1e0

#define SPAM_S 0.3                       // one start per this long
#define STILL_SPEED 0.3f                 // m/s (3D): "standing" when the AI isn't found
#define STILL_FOR_S 0.5                  // ... for this long (and slot 0 Wait that long)
#define LEVEL_M 0.05f                    // the physics ragdoll: the height may change this much while standing
#define VERIFY_TICKS 3                   // a set that doesn't show this many ticks after the call is missing
#define RAGDOLL_MAX_S 20.0               // the physics ragdoll gets up by itself after this long
#define BUTTON_DELAY_S 0.4               // a Mods page Play button plays this long after the menu closed
#define HOLD_MAX 150                     // developer hold: at most this many re-plays per start

// The game's memory. The selftest points this at its made-up game only, so its reads of the DLL's own names fail the
// same way as in the game (the game keeps the char* we pass as the set's name: it lives in this DLL, outside).
static uint64_t g_guest_lo = BOTW_ADDR_MAIN, g_guest_hi = BOTW_ADDR_GUEST_END;
#define GUEST_LO g_guest_lo
#define GUEST_HI g_guest_hi

static const SwModApi *api;
static SwModGuestFn g_orig;
static SwModGuestFn g_pw_orig;           // PlayerWait's calc (the game's)

// slot 0's name while our set plays: MASK_CALC = the game's "Wait" during PlayerWait's calc only (the fix),
// MASK_ALWAYS = Wait for the whole emote, MASK_OFF = untouched (developer checks: emotedbg mask)
enum { MASK_OFF, MASK_CALC, MASK_ALWAYS };
static int g_mask_mode = MASK_CALC;
static uint64_t g_mask_name;             // the "Wait" text the name pointer shows then (the game's own; set at init)

// ------------------------------------------------------------------ time, memory, game calls
static int64_t g_qpf;
static double qpc_now(void) {
    int64_t t;
    QueryPerformanceCounter(&t);
    return g_qpf > 0 ? (double)t / (double)g_qpf : 0.0;
}
static double (*now_s)(void) = qpc_now;

// a .. a+n-1 is committed, readable memory in the guest window (VirtualQuery; a few answers kept for a second)
#define MEM_CACHE 8
typedef struct {
    uint64_t lo, hi;
    double until;
} MemSpan;
static MemSpan g_mem[MEM_CACHE];
static int g_mem_next;
static int mem_ok(uint64_t a, uint64_t n) {
    if (!n || a < GUEST_LO || a >= GUEST_HI || n > GUEST_HI - a) return 0;
    double t = now_s();
    for (int i = 0; i < MEM_CACHE; i++)
        if (a >= g_mem[i].lo && a + n <= g_mem[i].hi && t < g_mem[i].until) return 1;
    MemInfo mi;
    if (VirtualQuery((const void *)(uintptr_t)a, &mi, sizeof mi) != sizeof mi) return 0;
    if (mi.state != 0x1000 || (mi.protect & 0x101) || !(mi.protect & 0xee)) return 0;
    if (a + n > mi.base + mi.size) return 0;
    g_mem[g_mem_next] = (MemSpan){mi.base, mi.base + mi.size, t + 1.0};
    g_mem_next = (g_mem_next + 1) % MEM_CACHE;
    return 1;
}
static uint64_t rd64(uint64_t a) { return a && !(a & 7) && mem_ok(a, 8) ? *(volatile const uint64_t *)(uintptr_t)a : 0; }
static uint32_t rd32(uint64_t a) { return a && !(a & 3) && mem_ok(a, 4) ? *(volatile const uint32_t *)(uintptr_t)a : 0; }
static uint16_t rd16(uint64_t a) { return a && !(a & 1) && mem_ok(a, 2) ? *(volatile const uint16_t *)(uintptr_t)a : 0xffff; }
static int wr32(uint64_t a, uint32_t v) {
    if (!a || (a & 3) || !mem_ok(a, 4)) return 0;
    *(volatile uint32_t *)(uintptr_t)a = v;
    return 1;
}
static int wr64(uint64_t a, uint64_t v) {
    if (!a || (a & 7) || !mem_ok(a, 8)) return 0;
    *(volatile uint64_t *)(uintptr_t)a = v;
    return 1;
}
static int heap_obj(uint64_t p) { return p >= GUEST_LO && p < GUEST_HI && !(p & 7); }

#define P(v) ((uint64_t)(uintptr_t)(v))
static uint64_t gcall(struct Cpu *c, uint64_t fn, uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4) {
    SwModRegs *r = SWMOD_REGS(c);
    r->x[0] = a0, r->x[1] = a1, r->x[2] = a2, r->x[3] = a3, r->x[4] = a4, r->x[5] = 0, r->x[6] = 0;  // (as the ghost)
    api->guest_call(c, fn);
    return r->x[0];
}

// The text at p into out (printable ASCII, 1..cap-1 characters), else "".
static void rd_text(uint64_t p, char *out, size_t cap) {
    out[0] = 0;
    for (size_t i = 0; p && i + 1 < cap; i++) {
        if ((i == 0 || !((p + i) & 0xfff)) && !mem_ok(p + i, 1)) break;
        char ch = *(volatile const char *)(uintptr_t)(p + i);
        if (!ch) {
            out[i] = 0;
            return;
        }
        if (ch <= ' ' || ch > '~') break;
        out[i] = ch;
    }
    out[0] = 0;
}

// ------------------------------------------------------------------ the emote list
static int lower_eq(const char *a, const char *b) {
    for (; *a && *b; a++, b++) {
        char x = *a >= 'A' && *a <= 'Z' ? (char)(*a + 32) : *a, y = *b >= 'A' && *b <= 'Z' ? (char)(*b + 32) : *b;
        if (x != y) return 0;
    }
    return !*a && !*b;
}
static int alias_hit(const char *aliases, const char *word) {
    char one[40];
    const char *s = aliases;
    while (*s) {
        size_t n = 0;
        while (s[n] && s[n] != '|') n++;
        if (n < sizeof one) {
            memcpy(one, s, n);
            one[n] = 0;
            if (lower_eq(one, word)) return 1;
        }
        s += n + (s[n] == '|');
    }
    return 0;
}
static int valid_id(const char *s) {
    size_t n = strlen(s);
    if (!n || n > 24 || !(s[0] >= 'a' && s[0] <= 'z')) return 0;
    for (size_t i = 0; i < n; i++)
        if (!((s[i] >= 'a' && s[i] <= 'z') || (s[i] >= '0' && s[i] <= '9') || s[i] == '_')) return 0;
    return 1;
}
#define FIND_NONE (-1)
#define FIND_AMBIGUOUS (-2)
#define FIND_COMMUNITY (-3)
// An emote by id, list number (1..), alias or unique id prefix. FIND_COMMUNITY: not ours but a valid id (tried as
// ZEmote_<it>, so an emote someone adds to tools/emotes plays with no rebuild of this DLL).
static int find_emote(const char *word) {
    if (!word || !*word) return FIND_NONE;
    for (int i = 0; i < EMOTE_COUNT; i++)
        if (lower_eq(EMOTES[i].id, word)) return i;
    for (int i = 0; i < EMOTE_COUNT; i++)
        if (alias_hit(EMOTES[i].aliases, word)) return i;
    int num = 0, digits = 1;
    for (const char *p = word; *p; p++) {
        if (*p < '0' || *p > '9' || num > 1000) digits = 0;
        else num = num * 10 + (*p - '0');
    }
    if (digits && num >= 1 && num <= EMOTE_COUNT && strlen(word) <= 2 && word[0] != '0') return num - 1;
    int hit = FIND_NONE;
    size_t n = strlen(word);
    for (int i = 0; i < EMOTE_COUNT; i++)
        if (!strncmp(EMOTES[i].id, word, n)) hit = hit == FIND_NONE ? i : FIND_AMBIGUOUS;
    if (hit != FIND_NONE) return hit;
    return valid_id(word) ? FIND_COMMUNITY : FIND_NONE;
}
static int ragdoll_index(void) {
    for (int i = 0; i < EMOTE_COUNT; i++)
        if (!strcmp(EMOTES[i].id, "ragdoll")) return i;
    return -1;
}

// ------------------------------------------------------------------ shared state (commands <-> tick)
static int g_lock;
static void lock(void) {
    while (__atomic_exchange_n(&g_lock, 1, __ATOMIC_ACQUIRE)) {
    }
}
static void unlock(void) { __atomic_store_n(&g_lock, 0, __ATOMIC_RELEASE); }

enum { REQ_NONE, REQ_PLAY, REQ_STOP, REQ_RAGDOLL_ON, REQ_RAGDOLL_OFF, REQ_DBG_PLAY, REQ_TOGGLE };
typedef struct {
    int kind;
    int index;           // emote index, or -1 with name set (a community id or a debug set)
    char name[32];       // the set to play for a community id / debug play
    uint32_t seq;
} Request;
static Request g_req;
static uint32_t g_req_seq;

enum { ST_IDLE, ST_STARTING, ST_PLAYING };
typedef struct {         // what the tick knows, copied for the commands
    int64_t ticks;
    int state;
    int index;           // playing emote (-1: none or a community/debug set)
    char playing[32];    // the set we asked for
    char slot0[32];      // what the body slot shows
    int finished;
    int loop;
    uint32_t seq;        // goes up at each start (multiplayer: a new emote)
    double started;
    int ragdoll;         // 0 off, 1 pose, 2 physics
    int can;
    char why[128];       // why the last request was refused ("" when it played)
    char after[32];      // what slot 0 showed right after our last play call, in the same tick
    int after_ours;      // ... and it was the name we passed (the call took)
    int after_ptr;       // ... and slot 0 held our own char* (diagnostic)
    int call_works;      // -1 unknown, 1 a play of ours was seen to take (in the same tick)
    int hold, replays;   // developer: re-play while the game puts Wait back (emotedbg hold on); re-plays since start
    char trace[400];     // developer: slot 0 before>after Link's update for the first ticks after a start
    char job_trace[300]; // developer: slot 0 seen by the frame job after a `emotedbg jobplay` (EMOTES_DEV_FRAMEJOB=1)
    int ours_ticks;      // ticks slot 0 showed our set since the last start
    int weapon_out;      // something is in Link's hands (weapon, shield or bow not on his back)
    int pw_frames;       // ticks in a row PlayerWait's calc ran for this Link (he stands)
    int64_t pw_calls;    // PlayerWait calc calls seen (Link's)
    int64_t pw_masked;   // ... that ran with our set shown as Wait and left it (ours again after)
    int64_t pw_took;     // ... where the game replaced our set during that calc
    char pw_took_name[32];  // what it played then
    int masked;          // (mask always) slot 0's name shows Wait now
    int ai_found;
    int ai_len;          // nodes from the top to PlayerNormal (RootAi, PlayerRoot, PlayerNormal = 3)
    uint64_t ai_off;     // Link + this = the AI root pointer (or its wrapper)
    int ai_child_wait, ai_child_move, ai_child_slip;
    uint32_t rd_still, rd_still2, rd_limit, rd_limit2;
    int rd_have_mgr;
} Snap;
static Snap g_snap;

static void set_why(const char *why) {
    size_t n = strlen(why);
    if (n >= sizeof g_snap.why) n = sizeof g_snap.why - 1;
    memcpy(g_snap.why, why, n);
    g_snap.why[n] = 0;
}

static void request(int kind, int index, const char *name) {
    lock();
    g_req.kind = kind;
    g_req.index = index;
    g_req.name[0] = 0;
    if (name) {
        size_t n = strlen(name);
        if (n > 31) n = 31;
        memcpy(g_req.name, name, n);
        g_req.name[n] = 0;
    }
    g_req.seq = ++g_req_seq;
    unlock();
}

// ------------------------------------------------------------------ tick state (game thread only)
static uint64_t t_link;          // the Link we know (0: none yet)
static int t_state = ST_IDLE;
static int t_index = -1;
static int t_loop;
static char t_set[32];           // the set we asked for (stays valid: the game keeps the pointer for the name)
static int t_wait_ticks;
static int t_ours_ticks;         // ticks slot 0 showed our set since the start
static int t_call_works = -1;    // -1 unknown, 1 a play of ours took (seen in the same tick)
static int t_hold;               // developer: emotedbg hold on
static int t_replays;
#define TRACE_TICKS 10
static int t_trace_left, t_trace_n;
static char t_pre[32];
static char t_trace_pre[TRACE_TICKS][24], t_trace_post[TRACE_TICKS][24];
static int t_fallback_tried;
static int t_orphan_sent;        // Wait was played once for a set of ours we no longer track
static double t_last_start = -1e9;
static uint32_t t_seq;
static float t_last_pos[3];
static double t_last_pos_t;
static float t_speed;
static uint64_t t_ai_root, t_ai_normal;
static int t_ai_scanned;
static int t_rd_phys;            // the physics ragdoll is allowed (developer: emotedbg rdphys on)
static int t_rd_on;              // physics ragdoll held down
static uint64_t t_rd_child, t_rd_saved, t_rd_mgr;
static double t_rd_since;
static int32_t g_zero_damage;    // the slip-fall's damage value points here while our ragdoll runs (always 0)
static _Alignas(16) uint64_t g_fake_action[2];  // AS_PLAY's "action": it reads only +8, the actor
// slot 0's name pointer while a set of ours plays (from our play call; 0 = none): where it lives (AS_CURRENT's string
// object + 8, in the holder) and its value then (the game's own text of our set's name)
static uint64_t t_name_at, t_our_name;
static int t_masked;             // (mask always) that pointer shows g_mask_name now
// PlayerWait's calc (pw_calc, on the game thread that runs Link's AI just before his update)
static volatile int t_pw_ran;    // it ran for this Link since the last tick
static int t_pw_frames;          // ticks in a row it ran
static int t_pw_ever;            // it ran at least once for this Link (the hook works)
static uint64_t t_pw_action;     // Link's PlayerWait action
static int64_t t_pw_calls, t_pw_masked, t_pw_took;
static char t_pw_took_name[32];

// Every set name we give the game lives in this table for good: the game keeps the char* as the set's name, so a
// name is never moved or changed, and slot 0 holding a pointer into this table means "a set we played". (25 emotes,
// 4 fallbacks, Wait and a few community / debug names fit; when it is full, new names are refused.)
#define MAX_NAMES 64
static char g_names[MAX_NAMES][32];
static int g_nnames;
static const char *intern(const char *name) {
    if (!name || !*name) return 0;
    for (int i = 0; i < g_nnames; i++)
        if (!strcmp(g_names[i], name)) return g_names[i];
    if (g_nnames >= MAX_NAMES || strlen(name) > 31) return 0;
    snprintf(g_names[g_nnames], sizeof g_names[0], "%s", name);
    return g_names[g_nnames++];
}
static int is_ours(uint64_t p) { return p >= P(g_names) && p < P(g_names) + sizeof g_names; }
#define WAIT_NAME "Wait"
static const char *g_emote_names[EMOTE_COUNT];  // each emote's set, interned at init
static int g_names_ready;

static uint64_t holder_of(uint64_t link) {
    uint64_t h = rd64(link + ACTOR_AS);
    return h && rd64(h + AS_OWNER) == link ? h : 0;
}

// The text at p when it is readable memory anywhere in this process (another mod's name), else "".
static void rd_text_any(uint64_t p, char *out, size_t cap) {
    out[0] = 0;
    if (p < 0x10000 || p > 0x7ffffffe0000ull) return;
    MemInfo mi;
    if (VirtualQuery((const void *)(uintptr_t)p, &mi, sizeof mi) != sizeof mi) return;
    if (mi.state != 0x1000 || (mi.protect & 0x101) || !(mi.protect & 0xee)) return;
    uint64_t room = mi.base + mi.size - p;
    for (size_t i = 0; i + 1 < cap && i < room; i++) {
        char ch = *(volatile const char *)(uintptr_t)(p + i);
        if (!ch) {
            out[i] = 0;
            return;
        }
        if (ch <= ' ' || ch > '~') break;
        out[i] = ch;
    }
    out[0] = 0;
}

// The text a set name pointer points at (ours, the game's, or another mod's) into out, else "".
static void name_text(uint64_t p, char out[32]) {
    out[0] = 0;
    if (is_ours(p)) {
        uint64_t row = (p - P(g_names)) / sizeof g_names[0];
        if (p == P(g_names[row])) {
            memcpy(out, g_names[row], 32);
            out[31] = 0;
        }
        return;
    }
    if (p >= GUEST_LO && p < GUEST_HI) rd_text(p, out, 32);
    else rd_text_any(p, out, 32);
}
// What slot 0 plays (its name into out). *ptr_ours: its char* is one of ours (intern). *at / *raw: where the holder
// keeps the name's pointer and its value (0 / 0 when nothing is readable).
static void slot0_read_at(struct Cpu *c, uint64_t holder, char out[32], int *ptr_ours, uint64_t *at, uint64_t *raw) {
    uint64_t s = gcall(c, AS_CURRENT, holder, 0, 0, 0, 0);
    uint64_t p = s ? rd64(s + STR_PTR) : 0;
    if (ptr_ours) *ptr_ours = is_ours(p) && p == P(g_names[(p - P(g_names)) / sizeof g_names[0]]);
    if (at) *at = p ? s + STR_PTR : 0;
    if (raw) *raw = p;
    name_text(p, out);
}
static void slot0_read(struct Cpu *c, uint64_t holder, char out[32], int *ptr_ours) {
    slot0_read_at(c, holder, out, ptr_ours, 0, 0);
}
// ... and whether it is ours: a ZEmote_ set, or the set we asked for while one runs (a fallback game set), or (mask
// always) our set under the name Wait
static int slot0_name(struct Cpu *c, uint64_t holder, char out[32]) {
    uint64_t raw = 0;
    slot0_read_at(c, holder, out, 0, 0, &raw);
    if (t_masked && t_state != ST_IDLE && raw && raw == g_mask_name) {
        memcpy(out, t_set, 32);
        out[31] = 0;
        return 1;
    }
    if (!strncmp(out, EMOTE_SET_PREFIX, sizeof EMOTE_SET_PREFIX - 1)) return 1;
    return t_state != ST_IDLE && out[0] && !strcmp(out, t_set);
}

// The "Wait" text slot 0's name shows while masked: the game's own (the very pointer its standing code passes), else
// (main not readable yet at init) ours, from our table: the game compares the text too. Tried again at each load.
static void pick_mask_name(void) {
    char w[8];
    rd_text(WAIT_TEXT, w, sizeof w);
    if (!strcmp(w, WAIT_NAME)) g_mask_name = WAIT_TEXT;
    else if (!g_mask_name) g_mask_name = P(intern(WAIT_NAME));
}

// (mask always) slot 0's name back to ours, if it still shows our Wait
static void unmask(void) {
    if (t_masked && t_name_at && g_mask_name && rd64(t_name_at) == g_mask_name) wr64(t_name_at, t_our_name);
    t_masked = 0;
}

// PlayerWait's calc: Link stands. Its walk/run code runs his standing animation code, which plays Wait whenever slot
// 0's name isn't Wait (see the top). While our set plays, the name pointer shows the game's own "Wait" for this call
// only; afterwards ours again, unless the game played something itself meanwhile (cold, out of breath...: left alone).
// No game calls here: the registers stay as the game's calc left them.
static void pw_calc(struct Cpu *c) {
    uint64_t act = SWMOD_REGS(c)->x[0];
    uint64_t link = t_link, at = t_name_at, ours = t_our_name;
    int mine = link && rd64(act) == VT_WAIT && rd64(act + AI_ACTOR) == link;
    uint64_t before = 0, expect = 0;
    int masked = 0;
    if (mine) {
        t_pw_action = act;
        t_pw_ran = 1;
        t_pw_calls++;
        if (at && ours) {
            before = rd64(at);
            expect = t_masked ? g_mask_name : ours;
            if (before == ours && g_mask_mode != MASK_OFF && g_mask_name) masked = wr64(at, g_mask_name);
        }
    }
    g_pw_orig(c);
    if (!mine || !at || !ours || !before || before != expect) return;
    uint64_t after = rd64(at);
    if (masked && after == g_mask_name) {
        wr64(at, ours);  // the standing code left slot 0 alone: our name again
        t_pw_masked++;
    } else if (after != before && !(masked && after == ours)) {
        t_pw_took++;     // the game played a set itself during PlayerWait's calc (mask off: its Wait)
        name_text(after, t_pw_took_name);
    }
}

// Play a set on Link's slot 0, exactly as the ghost does (keep_if_same 1, slot 0, sub 0, x5 = x6 = 0; keep 0 only for
// the Wait probe, which must replace the game's own Wait). name must be interned. Then reads slot 0 back in the same tick (into g_snap.after): our name there = the call took.
// It also notes where slot 0's name pointer lives and its value while a set of ours (not Wait) plays (t_name_at /
// t_our_name: PlayerWait's calc shows it as Wait).
static int t_after_ours;
static void as_play(struct Cpu *c, uint64_t link, uint64_t holder, const char *name, int keep, float morph) {
    if (!name) return;
    unmask();  // (the game's keep_if_same compares the real name)
    g_fake_action[0] = 0, g_fake_action[1] = link;
    swmod_set_s(c, 0, morph);
    gcall(c, AS_PLAY, P(g_fake_action), P(name), (uint64_t)keep, 0, 0);
    char now[32];
    int ptr = 0;
    uint64_t at = 0, raw = 0;
    slot0_read_at(c, holder, now, &ptr, &at, &raw);
    t_after_ours = !strcmp(now, name);
    t_name_at = t_our_name = 0;
    if (t_after_ours && strcmp(name, WAIT_NAME) && at && raw) t_name_at = at, t_our_name = raw;
    lock();
    memcpy(g_snap.after, now, sizeof now);
    g_snap.after_ours = t_after_ours;
    g_snap.after_ptr = ptr;
    unlock();
}

// ---- the AI: Link's RootAi (Link +0x558) -> PlayerRoot -> PlayerNormal, found once per load (else: a scan of Link
// for PlayerRoot, the first try's way). Every node's +8 is Link.
static uint64_t t_ai_path[AI_DEPTH + 1];  // the top ... PlayerNormal
static int t_ai_len;
static uint64_t ai_child(uint64_t node, int i) {
    uint32_t n = rd32(node + AI_NCHILD);
    uint64_t arr = rd64(node + AI_CHILDREN);
    if (i < 0 || (uint32_t)i >= n || n > 64 || !arr) return 0;
    return rd64(arr + 8ull * (uint64_t)i);
}
static uint64_t ai_current(uint64_t node) {
    uint16_t cur = rd16(node + AI_CUR);
    return cur == 0xffff ? 0 : ai_child(node, cur);
}
static uint64_t ai_find_child(uint64_t node, uint64_t vt) {
    uint32_t n = rd32(node + AI_NCHILD);
    for (uint32_t i = 0; i < n && i < 64; i++) {
        uint64_t ch = ai_child(node, (int)i);
        if (ch && rd64(ch) == vt) return ch;
    }
    return 0;
}
// node (at depth at of the path) or a child below it, down to depth levels, is Link's PlayerNormal: the path is kept
static int ai_dfs(uint64_t node, uint64_t link, int depth, int at) {
    if (at > AI_DEPTH) return 0;
    t_ai_path[at] = node;
    if (rd64(node) == VT_NORMAL && rd64(node + AI_ACTOR) == link) {
        t_ai_len = at + 1;
        return 1;
    }
    if (depth <= 0) return 0;
    uint32_t n = rd32(node + AI_NCHILD);
    for (uint32_t i = 0; i < n && i < 64; i++) {
        uint64_t ch = ai_child(node, (int)i);
        if (heap_obj(ch) && rd64(ch + AI_ACTOR) == link && ai_dfs(ch, link, depth - 1, at + 1)) return 1;
    }
    return 0;
}
static void ai_scan(uint64_t link) {
    t_ai_scanned = 1;
    if (g_mask_name != WAIT_TEXT) pick_mask_name();  // (main is surely there once Link is; nothing is masked now)
    t_ai_root = t_ai_normal = 0;
    t_ai_len = 0;
    uint64_t top = 0, off = 0;
    uint64_t r = rd64(link + LINK_ROOT_AI);
    if (heap_obj(r) && rd64(r) == VT_ROOTAI && rd64(r + AI_ACTOR) == link && ai_dfs(r, link, AI_DEPTH, 0))
        top = r, off = LINK_ROOT_AI;
    for (uint64_t o = 8; !top && o < AI_SCAN; o += 8) {  // (the first try's way: PlayerRoot itself or one wrapper)
        uint64_t p = rd64(link + o);
        if (!heap_obj(p)) continue;
        uint64_t root = rd64(p) == VT_ROOT ? p : 0;
        if (!root) {
            uint64_t q = rd64(p);
            if (heap_obj(q) && rd64(q) == VT_ROOT) root = q;
        }
        if (root && ai_dfs(root, link, 1, 0)) top = root, off = o;
    }
    if (!top) {
        t_ai_len = 0;
        if (api && api->log)
            api->log("emotes: Link's AI wasn't found (the standing check uses PlayerWait's calc, else the body slot)");
        return;
    }
    t_ai_root = top, t_ai_normal = t_ai_path[t_ai_len - 1];
    g_snap.ai_off = off;
    g_snap.ai_len = t_ai_len;
    if (api && api->log)
        api->log("emotes: Link's AI found at +0x%llx (%d levels to PlayerNormal)", (unsigned long long)off, t_ai_len);
}
// The class of PlayerNormal's current child, 0 when the AI isn't known or a level above isn't on the way to PlayerNormal
// (an event, riding, swimming...: then Normal's own index is stale and says nothing).
static uint64_t ai_now(void) {
    if (!t_ai_normal) return 0;
    for (int i = 0; i + 1 < t_ai_len; i++)
        if (ai_current(t_ai_path[i]) != t_ai_path[i + 1]) return 0;
    return rd64(ai_current(t_ai_normal));
}

// ---- the physics ragdoll (built, off until the Game check passes: emotedbg rdphys on). Only from standing still
// (PlayerWait) for STILL_FOR_S on level ground (the height hasn't changed): the tumble then stays short.
static void rd_restore(void) {
    if (t_rd_child && t_rd_saved) wr64(t_rd_child + SLIP_DAMAGE, t_rd_saved);
    t_rd_child = t_rd_saved = 0;
    t_rd_on = 0;
}
static double t_still_since;     // Link has stood (Wait, no speed, same height) since then; 0: not standing
static int rd_start(struct Cpu *c, uint64_t link) {
    if (ai_now() != VT_WAIT || !t_still_since || now_s() - t_still_since < STILL_FOR_S) return 0;
    uint64_t slip = ai_find_child(t_ai_normal, VT_SLIP);
    uint64_t mgr = rd64(link + LINK_RAGDOLL_MGR);
    uint64_t saved = slip ? rd64(slip + SLIP_DAMAGE) : 0;
    if (!slip || !mgr || !saved) return 0;
    if (!wr64(slip + SLIP_DAMAGE, P(&g_zero_damage))) return 0;
    t_rd_child = slip, t_rd_saved = saved, t_rd_mgr = mgr;
    t_rd_on = 1;
    t_rd_since = now_s();
    gcall(c, RAGDOLL_ON, t_ai_normal, 0, 0, 0, 0);
    return 1;
}
static void rd_tick(uint64_t link) {
    if (!t_rd_child) return;
    if (ai_now() != VT_SLIP) {  // he is up again (or never went down): the damage value goes back at once
        rd_restore();
        return;
    }
    if (rd64(link + LINK_RAGDOLL_MGR) != t_rd_mgr) {
        rd_restore();
        return;
    }
    if (t_rd_on && now_s() - t_rd_since > RAGDOLL_MAX_S) t_rd_on = 0;
    if (t_rd_on) {
        wr32(t_rd_mgr + RD_STILL, 0);
        wr32(t_rd_mgr + RD_STILL2, 0);
    }
}

// ---- something in Link's hands? (the equipment links the ghost reads: 0 weapon, 1 shield, 2 bow; a slot's byte at
// +0x10 not 0 = on his back)
#define ACTOR_EQUIP 0xb90
#define EQUIP_VPTR 0x7001d349d8ull
#define EQUIP_LINKS 0x8
#define EQUIP_LINK_SIZE 0x18
#define EQUIP_OWNER 0x98
#define EQUIP_BACK 0x10
#define LINKDATA_ID 0x40
#define LINKDATA_PROC 0x48
static int weapon_out(uint64_t link) {
    if (rd64(link + ACTOR_EQUIP) != EQUIP_VPTR || rd64(link + ACTOR_EQUIP + EQUIP_OWNER) != link) return 0;
    for (int i = 0; i < 3; i++) {
        uint64_t l = link + ACTOR_EQUIP + EQUIP_LINKS + (uint64_t)i * EQUIP_LINK_SIZE;
        uint64_t data = rd64(l);
        uint32_t id = rd32(l + 8);
        if (!data || id == 0xffffffffu || rd32(data + LINKDATA_ID) != id || !rd64(data + LINKDATA_PROC)) continue;
        uint64_t b = l + EQUIP_BACK;
        if (mem_ok(b, 1) && *(volatile const uint8_t *)(uintptr_t)b == 0) return 1;
    }
    return 0;
}

// ---- may an emote start now?
static double t_unsure_until;    // no start before this (an event, a talk, a cutscene pose was just seen on slot 0)
static double t_wait_since;      // slot 0 has shown Wait (or ours) since then; 0: it doesn't
static int t_weapon;
static int name_is_event(const char *s) {
    return !strncmp(s, "Demo", 4) || !strncmp(s, "Talk", 4) || !strncmp(s, "Event", 5) || !strncmp(s, "Get", 3);
}
static const char *start_blocker(uint64_t link, const char *slot0, int slot0_ours, double now) {
    (void)link;
    if (api && botw_paused(api)) return "the game is paused";
    if (t_weapon) return "put your weapon away first";
    int standing_name = !strcmp(slot0, WAIT_NAME) || slot0_ours;
    int pw = t_pw_frames >= 2;  // PlayerWait's calc ran this frame and the one before: Link stands (no event)
    if (t_ai_normal) {
        uint64_t vt = ai_now();
        if (vt != VT_WAIT && !(vt == 0 && pw)) return "Link has to stand still";  // (a level above unreadable: trust PW)
        return standing_name ? 0 : "Link has to stand still";
    }
    if (t_pw_ever) return pw && standing_name ? 0 : "Link has to stand still";  // no AI tree: PlayerWait's calc says
    // no AI found: slot 0, the speed and how long it has looked like that decide
    if (now < t_unsure_until) return "not during a talk or a cutscene";
    if (!standing_name || !t_wait_since || now - t_wait_since < STILL_FOR_S) return "Link has to stand still";
    if (t_speed > STILL_SPEED) return "Link has to stand still";
    return 0;
}

static void forget(void) {  // a new Link (a load): nothing we knew is valid; nothing is written
    t_state = ST_IDLE, t_index = -1, t_set[0] = 0;
    t_ai_scanned = 0, t_ai_root = t_ai_normal = 0, t_ai_len = 0;
    t_name_at = t_our_name = 0, t_masked = 0;
    t_pw_ran = 0, t_pw_frames = 0, t_pw_ever = 0, t_pw_action = 0;
    t_rd_child = t_rd_saved = t_rd_mgr = 0, t_rd_on = 0;
    t_last_pos_t = 0, t_speed = 0;
    t_wait_since = t_still_since = 0;
    t_unsure_until = now_s() + 2.0;  // (a load usually ends in an event: let it start first)
    lock();
    g_req.kind = REQ_NONE;  // a key pressed during the load doesn't fire in the new place
    g_snap.index = -1;
    unlock();
}

// slot 0 should show this set (an interned name); 0 when there is no room for the name
static int start_set(struct Cpu *c, uint64_t link, uint64_t holder, int index, const char *name, double now) {
    const char *nm = intern(name);
    if (!nm) return 0;
    memcpy(t_set, nm, strlen(nm) + 1);
    t_index = index;
    t_loop = index >= 0 ? EMOTES[index].loop : 1;
    t_state = ST_STARTING;
    t_wait_ticks = 0;
    t_ours_ticks = 0;
    t_replays = 0;
    t_last_start = now;
    t_seq++;
    t_trace_left = TRACE_TICKS, t_trace_n = 0;
    as_play(c, link, holder, nm, 1, -1.0f);
    if (t_after_ours) t_call_works = 1;
    return 1;
}

// ---- keys and buttons
static const int FAV_KEYS[6] = {'5', '6', '7', '8', '9', '0'};
static const int FAV_NUMPAD[6] = {0x65, 0x66, 0x67, 0x68, 0x69, 0x60};
static const char *const FAV_SETTINGS[6] = {"fav_5", "fav_6", "fav_7", "fav_8", "fav_9", "fav_0"};
static const char *const FAV_DEFAULT[6] = {"thumbs_up", "thumbs_down", "six_seven", "in_love", "floss", "ragdoll"};
static int t_key_down[6];
static int t_fav[6] = {-1, -1, -1, -1, -1, -1};
static int t_keys_on = 1, t_ragdoll_allow = 1;
static int64_t t_settings_read = -1000;
static int64_t t_buttons[EMOTE_COUNT];
static int t_button_pending = -1;
static double t_button_since;
static int g_settings_changed = 1;

static void settings_changed(const SwModApi *self, const char *key, void *user) {
    (void)self, (void)key, (void)user;
    __atomic_store_n(&g_settings_changed, 1, __ATOMIC_RELEASE);
}

static void read_settings(int first) {
    if (!api || !SWMOD_HAS(api, setting_int) || !api->setting_int) {
        for (int i = 0; i < 6; i++) t_fav[i] = find_emote(FAV_DEFAULT[i]);
        return;
    }
    t_keys_on = (int)api->setting_int(api, "keys", 1);
    t_ragdoll_allow = (int)api->setting_int(api, "ragdoll", 1);
    for (int i = 0; i < 6; i++) {
        char v[40];
        if (api->setting_str && api->setting_str(api, FAV_SETTINGS[i], v, sizeof v) > 0) t_fav[i] = find_emote(v);
        else t_fav[i] = find_emote(FAV_DEFAULT[i]);
        if (t_fav[i] < 0) t_fav[i] = find_emote(FAV_DEFAULT[i]);
    }
    for (int i = 0; i < EMOTE_COUNT; i++) {
        char key[40];
        snprintf(key, sizeof key, "play_%s", EMOTES[i].id);
        int64_t n = api->setting_int(api, key, 0);
        if (!first && n != t_buttons[i]) t_button_pending = i, t_button_since = now_s();
        t_buttons[i] = n;
    }
}

static int game_in_front(void) {
    void *w = GetForegroundWindow();
    unsigned long pid = 0;
    if (!w) return 0;
    GetWindowThreadProcessId(w, &pid);
    return pid == GetCurrentProcessId();
}
static int real_key(int vk) { return (GetAsyncKeyState(vk) & 0x8000) != 0; }
static int real_pos(float out[3]) { return api && botw_player_pos(api, out); }
// (the selftest swaps these for fakes)
static int (*in_front)(void) = game_in_front;
static int (*key_is_down)(int vk) = real_key;
static int (*player_pos)(float out[3]) = real_pos;

static void poll_keys(void) {
    int front = in_front();
    for (int i = 0; i < 6; i++) {
        int down = front && t_keys_on && (key_is_down(FAV_KEYS[i]) || key_is_down(FAV_NUMPAD[i]));
        if (down && !t_key_down[i] && t_fav[i] >= 0) {
            int ix = t_fav[i];
            if (ix == ragdoll_index()) request(REQ_TOGGLE, ix, 0);
            else if (t_state != ST_IDLE && t_index == ix) request(REQ_STOP, -1, 0);  // the same key again: stop
            else request(REQ_PLAY, ix, 0);
        }
        t_key_down[i] = down;
    }
}

// ------------------------------------------------------------------ the tick (after Link's own update)
static int64_t g_ticks;
static void tick(struct Cpu *c, uint64_t link) {
    double now = now_s();
    __atomic_fetch_add(&g_ticks, 1, __ATOMIC_RELAXED);
    if (rd64(link) != PLAYER_VPTR) return;
    if (link != t_link) {
        forget();
        t_button_pending = -1;
        t_link = link;
    }
    if (!t_ai_scanned) ai_scan(link);
    uint64_t holder = holder_of(link);
    if (!holder) return;
    if (__atomic_exchange_n(&t_pw_ran, 0, __ATOMIC_ACQ_REL)) {  // PlayerWait's calc ran (this frame, before us)
        t_pw_frames++;
        t_pw_ever = 1;
    } else {
        t_pw_frames = 0;
    }
    if (t_masked && __atomic_load_n(&g_mask_mode, __ATOMIC_ACQUIRE) != MASK_ALWAYS) unmask();  // (switched)

    // speed (fallback standing check; 3D, so lifts and falls count)
    float pos[3];
    float dy = 0;
    if (player_pos(pos)) {
        if (t_last_pos_t > 0 && now > t_last_pos_t) {
            float dx = pos[0] - t_last_pos[0], dz = pos[2] - t_last_pos[2];
            dy = pos[1] - t_last_pos[1];
            float d = __builtin_sqrtf(dx * dx + dy * dy + dz * dz);
            t_speed = d / (float)(now - t_last_pos_t);
        }
        memcpy(t_last_pos, pos, sizeof pos);
        t_last_pos_t = now;
    }
    t_weapon = weapon_out(link);

    // settings and keys
    if (__atomic_exchange_n(&g_settings_changed, 0, __ATOMIC_ACQ_REL) || g_ticks - t_settings_read > 30) {
        read_settings(t_settings_read < 0);
        t_settings_read = g_ticks;
    }
    poll_keys();
    if (t_button_pending >= 0 && (!api || !botw_paused(api)) && now - t_button_since >= BUTTON_DELAY_S) {
        request(t_button_pending == ragdoll_index() ? REQ_TOGGLE : REQ_PLAY, t_button_pending, 0);
        t_button_pending = -1;
    }

    Request rq;
    lock();
    rq = g_req;
    g_req.kind = REQ_NONE;
    unlock();

    char slot0[32];
    int ours = slot0_name(c, holder, slot0);
    if (t_trace_left > 0 && t_trace_n < TRACE_TICKS) {  // (developer trace: before > after Link's update)
        memcpy(t_trace_pre[t_trace_n], t_pre, sizeof t_trace_pre[0]);
        t_trace_pre[t_trace_n][23] = 0;
        memcpy(t_trace_post[t_trace_n], slot0, sizeof t_trace_post[0]);
        t_trace_post[t_trace_n][23] = 0;
        t_trace_n++;
        t_trace_left--;
    }
    int is_wait = !strcmp(slot0, WAIT_NAME);
    if (!ours && name_is_event(slot0)) t_unsure_until = now + 2.0;  // a talk / cutscene / item get: wait it out
    if (is_wait || ours) {
        if (!t_wait_since) t_wait_since = now;
    } else {
        t_wait_since = 0;
    }
    uint64_t ai_vt = ai_now();
    int standing = is_wait && t_speed <= STILL_SPEED && __builtin_fabsf(dy) < LEVEL_M &&
                   (!t_ai_normal || ai_vt == VT_WAIT || (!ai_vt && t_pw_frames > 0)) &&
                   (!t_pw_ever || t_pw_frames > 0);
    if (!standing) t_still_since = 0;
    else if (!t_still_since) t_still_since = now;
    rd_tick(link);

    // what plays now
    if (t_state == ST_STARTING) {
        int match = ours && !strcmp(slot0, t_set);
        if (!match && t_hold && t_after_ours && !strcmp(slot0, WAIT_NAME) && t_replays < HOLD_MAX) {
            as_play(c, link, holder, intern(t_set), 1, -1.0f);  // developer: the game put Wait back: again
            t_replays++;
            t_wait_ticks = 0;
        } else if (match) {
            t_state = ST_PLAYING;
            t_ours_ticks = 1;
            t_call_works = 1;
        } else if (++t_wait_ticks >= VERIFY_TICKS) {
            const char *fb = t_index >= 0 ? EMOTES[t_index].fallback : "";
            if (!t_after_ours && fb[0] && !t_fallback_tried && intern(fb)) {
                // the set didn't take: the anims package is off or missing? the game's own set instead
                t_fallback_tried = 1;
                memcpy(t_set, intern(fb), strlen(fb) + 1);
                t_wait_ticks = 0;
                as_play(c, link, holder, intern(fb), 1, -1.0f);
                if (t_after_ours) t_call_works = 1;
            } else {
                t_state = ST_IDLE;
                if (t_after_ours) {
                    set_why("the game put Link back to standing right away (see: emotedbg slot)");
                } else if (t_call_works == 1 && !strncmp(t_set, EMOTE_SET_PREFIX, sizeof EMOTE_SET_PREFIX - 1)) {
                    set_why("the emote animations are missing: turn on Emotes (animations) in the launcher, then "
                            "Deploy");
                } else {
                    set_why("the game didn't take the animation: are Emotes (animations) on in the launcher (then "
                            "Deploy)? See: emotedbg slot");
                }
            }
        }
    } else if (t_state == ST_PLAYING) {
        if (t_hold && !strcmp(slot0, WAIT_NAME) && t_replays < HOLD_MAX) {
            as_play(c, link, holder, intern(t_set), 1, -1.0f);  // developer: the game put Wait back: again
            t_replays++;
        } else if (!ours || strcmp(slot0, t_set)) {
            t_state = ST_IDLE;  // the game took Link back (he moved, jumped, attacked, was hit): touch nothing
        } else {
            t_ours_ticks++;
            if (!t_loop && gcall(c, AS_FINISHED, holder, 0, 0, 0, 0) & 1) {
                as_play(c, link, holder, intern(WAIT_NAME), 1, 8.0f);
                t_state = ST_IDLE;
            }
        }
    } else if (ours && !is_wait && !t_orphan_sent) {
        // a set of ours shows up while we track none (it took after we gave up): back to Wait, once
        as_play(c, link, holder, intern(WAIT_NAME), 1, 8.0f);
        t_orphan_sent = 1;
    }
    if (t_state == ST_IDLE && (!ours || is_wait)) t_orphan_sent = 0;

    // the request
    if (rq.kind == REQ_TOGGLE)
        rq.kind = (t_rd_child || (t_state != ST_IDLE && t_index == ragdoll_index())) ? REQ_RAGDOLL_OFF : REQ_RAGDOLL_ON;
    switch (rq.kind) {
    case REQ_STOP:
    case REQ_RAGDOLL_OFF:
        if (rq.kind == REQ_RAGDOLL_OFF && t_rd_child) {
            t_rd_on = 0;  // the game stands him up; the damage value goes back when he is up
            break;
        }
        if (t_state != ST_IDLE && ours && !strcmp(slot0, t_set)) as_play(c, link, holder, intern(WAIT_NAME), 1, 8.0f);
        t_state = ST_IDLE;
        break;
    case REQ_PLAY:
    case REQ_DBG_PLAY:
    case REQ_RAGDOLL_ON: {
        int ix = rq.kind == REQ_RAGDOLL_ON ? ragdoll_index() : rq.index;
        if (now - t_last_start < SPAM_S) {
            set_why("too quick: one emote per 0.3 s");
            break;
        }
        if (rq.kind == REQ_RAGDOLL_ON && !t_ragdoll_allow) {
            set_why("ragdoll is turned off on the Mods page");
            break;
        }
        const char *block = start_blocker(link, slot0, ours, now);
        if (rq.kind == REQ_RAGDOLL_ON && t_rd_phys && t_ai_normal && !block) {
            if (rd_start(c, link)) {
                set_why("");
                t_last_start = now;
                t_seq++;
            } else {
                set_why("the ragdoll needs Link standing still on level ground for a moment");
            }
            break;
        }
        if (block) {
            set_why(block);
            break;
        }
        set_why("");
        t_fallback_tried = 0;
        int ok = (rq.kind == REQ_DBG_PLAY || ix < 0) ? start_set(c, link, holder, -1, rq.name, now)
                                                       : start_set(c, link, holder, ix, EMOTES[ix].set, now);
        if (!ok) set_why("too many different animation names asked for: restart the game");
        break;
    }
    default:
        break;
    }

    // slot 0's name: nothing of ours plays any more -> let go; (mask always) ours plays -> show it as Wait
    if (t_state == ST_IDLE) {
        unmask();
        t_name_at = t_our_name = 0;
    } else if (g_mask_mode == MASK_ALWAYS && !t_masked && g_mask_name && t_name_at && rd64(t_name_at) == t_our_name) {
        t_masked = wr64(t_name_at, g_mask_name);
    }

    // what the commands see
    lock();
    g_snap.ticks = g_ticks;
    g_snap.state = t_state;
    g_snap.index = t_state == ST_IDLE ? -1 : t_index;
    memcpy(g_snap.playing, t_state == ST_IDLE ? "" : t_set, t_state == ST_IDLE ? 1 : strlen(t_set) + 1);
    memcpy(g_snap.slot0, slot0, sizeof slot0);
    g_snap.loop = t_loop;
    g_snap.seq = t_seq;
    g_snap.started = t_last_start;
    g_snap.ragdoll = t_rd_child ? 2 : (t_state != ST_IDLE && t_index == ragdoll_index() ? 1 : 0);
    g_snap.can = start_blocker(link, slot0, ours, now) == 0;
    g_snap.call_works = t_call_works;
    g_snap.ours_ticks = t_ours_ticks;
    g_snap.hold = t_hold, g_snap.replays = t_replays;
    {
        size_t n = 0;
        g_snap.trace[0] = 0;
        for (int i = 0; i < t_trace_n && n + 52 < sizeof g_snap.trace; i++)
            n += (size_t)snprintf(g_snap.trace + n, sizeof g_snap.trace - n, "%s%s>%s", i ? "|" : "", t_trace_pre[i],
                                  t_trace_post[i]);
    }
    g_snap.weapon_out = t_weapon;
    g_snap.pw_frames = t_pw_frames;
    g_snap.pw_calls = t_pw_calls, g_snap.pw_masked = t_pw_masked, g_snap.pw_took = t_pw_took;
    memcpy(g_snap.pw_took_name, t_pw_took_name, sizeof g_snap.pw_took_name);
    g_snap.masked = t_masked;
    g_snap.ai_found = t_ai_normal != 0;
    g_snap.ai_len = t_ai_len;
    uint64_t vt = ai_now();
    g_snap.ai_child_wait = vt == VT_WAIT, g_snap.ai_child_move = vt == VT_MOVE, g_snap.ai_child_slip = vt == VT_SLIP;
    uint64_t mgr = rd64(link + LINK_RAGDOLL_MGR);
    g_snap.rd_have_mgr = mgr != 0;
    if (mgr) {
        g_snap.rd_still = rd32(mgr + RD_STILL), g_snap.rd_still2 = rd32(mgr + RD_STILL2);
        g_snap.rd_limit = rd32(mgr + RD_LIMIT), g_snap.rd_limit2 = rd32(mgr + RD_LIMIT2);
    }
    unlock();
}

static void link_update(struct Cpu *c) {
    uint64_t link = SWMOD_REGS(c)->x[0];
    SwModRegs *r = SWMOD_REGS(c);
    uint64_t x[31], sp, pc, nzcv;
    SwModVReg v[32];
    t_pre[0] = 0;
    if (t_trace_left > 0 && link == t_link && rd64(link) == PLAYER_VPTR) {  // developer trace: slot 0 before
        uint64_t holder = holder_of(link);
        sp = r->sp, pc = r->pc, nzcv = r->nzcv;
        memcpy(x, r->x, sizeof x);
        memcpy(v, r->v, sizeof v);
        if (holder) slot0_name(c, holder, t_pre);
        memcpy(r->x, x, sizeof x);
        memcpy(r->v, v, sizeof v);
        r->sp = sp, r->pc = pc, r->nzcv = nzcv;
    }
    g_orig(c);
    sp = r->sp, pc = r->pc, nzcv = r->nzcv;
    memcpy(x, r->x, sizeof x);
    memcpy(v, r->v, sizeof v);
    tick(c, link);
    memcpy(r->x, x, sizeof x);
    memcpy(r->v, v, sizeof v);
    r->sp = sp, r->pc = pc, r->nzcv = nzcv;
}

// ------------------------------------------------------------------ developer: play from the frame job once
// Only with EMOTES_DEV_FRAMEJOB=1 in the game's environment (botw_on_frame wraps the game's frame job, which the
// ghost mod also wraps: never in a normal run). `emotedbg jobplay <emote>` plays its set from there and records what
// slot 0 shows in the next frames.
static int g_job_on;
static int g_job_req = -1;       // emote index to play from the frame job, -1 none
static _Alignas(16) uint64_t g_job_action[2];
static int j_left, j_n;
static char j_trace[10][24];
static void job_fn(struct Cpu *c) {
    uint64_t link = api ? botw_player_actor(api) : 0;
    if (!link || rd64(link) != PLAYER_VPTR) return;
    uint64_t holder = holder_of(link);
    if (!holder) return;
    char now[32];
    if (j_left > 0 && j_n < 10) {
        slot0_read(c, holder, now, 0);
        memcpy(j_trace[j_n], now, 23);
        j_trace[j_n][23] = 0;
        j_n++, j_left--;
    }
    int ix = __atomic_exchange_n(&g_job_req, -1, __ATOMIC_ACQ_REL);
    if (ix >= 0 && ix < EMOTE_COUNT && g_names_ready) {
        g_job_action[0] = 0, g_job_action[1] = link;
        swmod_set_s(c, 0, -1.0f);
        gcall(c, AS_PLAY, P(g_job_action), P(g_emote_names[ix]), 1, 0, 0);
        slot0_read(c, holder, now, 0);
        memcpy(j_trace[0], now, 23);
        j_trace[0][23] = 0;
        j_n = 1, j_left = 9;
    }
    lock();
    size_t n = 0;
    g_snap.job_trace[0] = 0;
    for (int i = 0; i < j_n && n + 30 < sizeof g_snap.job_trace; i++)
        n += (size_t)snprintf(g_snap.job_trace + n, sizeof g_snap.job_trace - n, "%s%s", i ? "|" : "", j_trace[i]);
    unlock();
}

// ------------------------------------------------------------------ commands
static Snap snap_copy(void) {
    lock();
    Snap s = g_snap;
    unlock();
    return s;
}

static void reply_list(SwModReply *r) {
    static const char *const PAGE[] = {"", "hello", "dances", "hyrule"};
    for (int i = 0; i < EMOTE_COUNT; i++)
        api->reply_text(r, "%2d  %-13s %s%s", i + 1, EMOTES[i].id, EMOTES[i].page ? PAGE[EMOTES[i].page] : "special",
                        EMOTES[i].loop ? " (loops)" : "");
    api->reply_text(r, "emote <name or number> plays one; emote stop; emote ragdoll [on|off]. Keys 5-0 too.");
}

static void cmd_emote(int argc, char **argv, SwModReply *r) {
    if (argc < 2) {
        reply_list(r);
        return;
    }
    if (!strcmp(argv[1], "state")) {
        Snap s = snap_copy();
        api->reply_bool(r, "has_emote", true);
        api->reply_str(r, "version", EMOTES_VERSION);
        api->reply_str(r, "id", s.index >= 0 ? EMOTES[s.index].id : (s.playing[0] ? s.playing : ""));
        api->reply_int(r, "seq", s.seq);
        api->reply_num(r, "t", s.state != ST_IDLE ? now_s() - s.started : 0.0);
        api->reply_bool(r, "loop", s.loop != 0);
        api->reply_int(r, "ragdoll", s.ragdoll);
        api->reply_bool(r, "share", api->setting_int ? api->setting_int(api, "share", 1) != 0 : true);
        api->reply_bool(r, "can", s.can != 0);
        api->reply_str(r, "why", s.why);
        api->reply_int(r, "ticks", s.ticks);
        char list[1024];
        size_t n = 0;
        list[0] = 0;
        for (int i = 0; i < EMOTE_COUNT && n + 32 < sizeof list; i++)
            n += (size_t)snprintf(list + n, sizeof list - n, "%s%s", i ? "," : "", EMOTES[i].id);
        api->reply_str(r, "list", list);
        return;
    }
    if (!strcmp(argv[1], "stop")) {
        request(REQ_STOP, -1, 0);
        api->reply_text(r, "Stopping.");
        return;
    }
    if (!strcmp(argv[1], "ragdoll") || !strcmp(argv[1], "flop")) {
        int on = argc < 3 ? -1 : !strcmp(argv[2], "on") ? 1 : !strcmp(argv[2], "off") ? 0 : -2;
        if (on == -2) {
            api->reply_fail(r, "emote ragdoll [on|off]");
            return;
        }
        request(on < 0 ? REQ_TOGGLE : on ? REQ_RAGDOLL_ON : REQ_RAGDOLL_OFF, ragdoll_index(), 0);
        api->reply_text(r, on == 0 ? "Getting up." : "Ragdoll!");
        return;
    }
    char word[64];  // the words joined ("6 7" is two arguments)
    size_t n = 0;
    for (int i = 1; i < argc && n + 2 < sizeof word; i++) {
        size_t len = strlen(argv[i]);
        if (n + len + 2 > sizeof word) break;
        if (i > 1) word[n++] = ' ';
        memcpy(word + n, argv[i], len);
        n += len;
    }
    word[n] = 0;
    int ix = find_emote(word);
    if (ix == FIND_AMBIGUOUS) {
        api->reply_fail(r, "\"%s\" fits more than one emote: type more of its name", word);
        return;
    }
    if (ix == FIND_NONE) {
        api->reply_fail(r, "No emote \"%s\". Type emote for the list.", word);
        return;
    }
    if (ix == FIND_COMMUNITY) {
        char set[40];
        snprintf(set, sizeof set, "%s%s", EMOTE_SET_PREFIX, word);
        request(REQ_PLAY, -1, set);
        api->reply_text(r, "Trying %s (not in this DLL's list: from your own tools/emotes).", set);
        return;
    }
    request(ix == ragdoll_index() ? REQ_TOGGLE : REQ_PLAY, ix, 0);
    api->reply_text(r, "%s!", EMOTES[ix].name);
}

static void cmd_emotedbg(int argc, char **argv, SwModReply *r) {
    Snap s = snap_copy();
    const char *what = argc > 1 ? argv[1] : "slot";
    if (!strcmp(what, "slot")) {
        api->reply_str(r, "slot0", s.slot0);
        api->reply_str(r, "playing", s.playing);
        api->reply_int(r, "state", s.state);
        api->reply_int(r, "ticks", s.ticks);
        api->reply_str(r, "after", s.after);
        api->reply_bool(r, "after_ours", s.after_ours != 0);
        api->reply_bool(r, "after_ptr", s.after_ptr != 0);
        api->reply_bool(r, "hold", s.hold != 0);
        api->reply_int(r, "replays", s.replays);
        api->reply_str(r, "trace", s.trace);
        api->reply_str(r, "job_trace", s.job_trace);
        api->reply_bool(r, "job_on", g_job_on != 0);
        api->reply_int(r, "call_works", s.call_works);
        api->reply_int(r, "ours_ticks", s.ours_ticks);
        api->reply_bool(r, "weapon_out", s.weapon_out != 0);
        api->reply_bool(r, "can", s.can != 0);
        api->reply_str(r, "why", s.why);
        static const char *const MASKS[] = {"off", "calc", "always"};
        api->reply_str(r, "mask", MASKS[__atomic_load_n(&g_mask_mode, __ATOMIC_ACQUIRE) % 3]);
        api->reply_bool(r, "masked", s.masked != 0);
        api->reply_bool(r, "pw_hook", g_pw_orig != 0);
        api->reply_int(r, "pw_frames", s.pw_frames);
        api->reply_int(r, "pw_calls", s.pw_calls);
        api->reply_int(r, "pw_masked", s.pw_masked);
        api->reply_int(r, "pw_took", s.pw_took);
        api->reply_str(r, "pw_took_name", s.pw_took_name);
    } else if (!strcmp(what, "mask") && argc > 2) {
        int m = !strcmp(argv[2], "off") ? MASK_OFF : !strcmp(argv[2], "calc") ? MASK_CALC
                : !strcmp(argv[2], "always") ? MASK_ALWAYS : -1;
        if (m < 0) {
            api->reply_fail(r, "emotedbg mask off|calc|always");
            return;
        }
        __atomic_store_n(&g_mask_mode, m, __ATOMIC_RELEASE);
        api->reply_text(r, "Mask %s.", argv[2]);
    } else if (!strcmp(what, "play") && argc > 2) {
        request(REQ_DBG_PLAY, -1, argv[2]);
        api->reply_text(r, "Asked for %s.", argv[2]);
    } else if (!strcmp(what, "ai")) {
        api->reply_bool(r, "found", s.ai_found != 0);
        api->reply_int(r, "offset", (int64_t)s.ai_off);
        api->reply_int(r, "levels", s.ai_len);
        api->reply_int(r, "pw_frames", s.pw_frames);
        api->reply_bool(r, "wait", s.ai_child_wait != 0);
        api->reply_bool(r, "move", s.ai_child_move != 0);
        api->reply_bool(r, "slip", s.ai_child_slip != 0);
    } else if (!strcmp(what, "rd")) {
        api->reply_bool(r, "manager", s.rd_have_mgr != 0);
        api->reply_int(r, "still", s.rd_still);
        api->reply_int(r, "still2", s.rd_still2);
        api->reply_int(r, "limit", s.rd_limit);
        api->reply_int(r, "limit2", s.rd_limit2);
        api->reply_int(r, "ragdoll", s.ragdoll);
    } else if (!strcmp(what, "jobplay") && argc > 2) {
        int ix = find_emote(argv[2]);
        if (!g_job_on || ix < 0) {
            api->reply_fail(r, "needs EMOTES_DEV_FRAMEJOB=1 at game start and an emote of the list");
            return;
        }
        __atomic_store_n(&g_job_req, ix, __ATOMIC_RELEASE);
        api->reply_text(r, "Asked the frame job for %s.", EMOTES[ix].set);
    } else if (!strcmp(what, "hold") && argc > 2) {
        __atomic_store_n(&t_hold, !strcmp(argv[2], "on"), __ATOMIC_RELEASE);
        api->reply_text(r, "Hold %s.", t_hold ? "on (developer test: re-play while the game puts Wait back)" : "off");
    } else if (!strcmp(what, "rdphys") && argc > 2) {
        __atomic_store_n(&t_rd_phys, !strcmp(argv[2], "on"), __ATOMIC_RELEASE);
        api->reply_text(r, "Physics ragdoll %s.", t_rd_phys ? "on (developer test)" : "off");
    } else {
        api->reply_fail(r, "emotedbg slot | play <set> | ai | rd | mask off|calc|always | hold on|off | rdphys on|off");
    }
}

static const SwModCommand COMMANDS[] = {
    {"emote", "[name|number|stop|ragdoll [on|off]|state]", "Link does an emote (the list: emote)", cmd_emote,
     SWMOD_CMD_PLAYER},
    {"emotedbg", "slot|play <set>|ai|rd|mask off|calc|always|hold on|off|rdphys on|off",
     "Emotes mod: developer checks", cmd_emotedbg, 0},
};

// The emote playing now (its id, "" when none), for other mods (the play-as puppet later). Read only.
SWMOD_EXPORT size_t emotes_active(char *out, size_t cap) {
    if (!out || !cap) return 0;
    Snap s = snap_copy();
    const char *id = s.index >= 0 ? EMOTES[s.index].id : s.playing;
    size_t n = strlen(id);
    if (n >= cap) n = cap - 1;
    memcpy(out, id, n);
    out[n] = 0;
    return n;
}

// modunload / modreload: no game thread runs our tick any more; put the slip-fall's damage pointer back (it points
// into this DLL while the physics ragdoll runs).
SWMOD_EXPORT void swmod_exit(const SwModApi *a) {
    (void)a;
    rd_restore();
    unmask();
}

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    api = a;
    g_snap.index = -1;
    g_snap.call_works = -1;
    QueryPerformanceFrequency(&g_qpf);
    if (!botw_version_ok(a)) {
        a->log("emotes: this game version isn't 1.9.0: the emotes stay off");
        return 0;
    }
    for (int i = 0; i < EMOTE_COUNT; i++) {
        g_emote_names[i] = intern(EMOTES[i].set);
        if (EMOTES[i].fallback[0]) intern(EMOTES[i].fallback);
    }
    intern(WAIT_NAME);
    g_names_ready = 1;
    a->control_register(COMMANDS, (int)(sizeof COMMANDS / sizeof COMMANDS[0]));
    if (SWMOD_API_OK(a, 3) && a->setting_watch) a->setting_watch(a, settings_changed, 0);
    g_orig = a->dispatch_lookup(LINK_UPDATE);
    if (!g_orig) {
        a->log("emotes: Link's update wasn't found: the emotes stay off (console only answers)");
        return 0;
    }
    a->hle_override(LINK_UPDATE, link_update, "emotes_link_update");
    pick_mask_name();
    g_pw_orig = a->dispatch_lookup(PW_CALC);
    if (g_pw_orig) a->hle_override(PW_CALC, pw_calc, "emotes_playerwait_calc");
    else a->log("emotes: PlayerWait's calc wasn't found: the game will put Link's standing animation back at once");
    char dev[8];
    if (GetEnvironmentVariableA("EMOTES_DEV_FRAMEJOB", dev, sizeof dev) && dev[0] == '1') {
        g_job_on = botw_on_frame(a, job_fn);
        a->log("emotes: developer frame-job test %s", g_job_on ? "on" : "couldn't start");
    }
    a->log("emotes %s: %d emotes, keys 5-0, console: emote", EMOTES_VERSION, EMOTE_COUNT);
    return 0;
}
