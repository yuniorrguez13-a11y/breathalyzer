// ghost: shows another player as an actor in your game (example native mod for BotW 1.9.0, docs/ghost.md).
//
// How it runs code every frame without a game-code rebuild: the game's frame job at 0x7001129af8 is a virtual
// function, so it only runs through Savage Wind's dispatcher, and hle_override can replace it. The wrapper runs the
// game's function first, then ghost_tick(). That tick runs once per frame on one of the game's worker threads (it
// moves between three of them), never twice at the same time; it is the only place this mod calls game code.
// Commands run on Savage Wind's frame thread and only set requests under a lock.
//
// Spawning follows the game's own pattern (out/research/actor-spawn-verify.md):
//   request:  zero the handle, build a parameter pack with "@M" (a 3x4 matrix), the game's spawn request (async)
//   each tick while pending: if the handle's unit no longer points back at our handle (a stage change recycled it),
//     forget it; if ready: peek the proc, check it is an Actor, set its matrix, take it (wakes it), keep a link;
//     if failed: release it.
//   live: each tick check the link; move with the game's "set position and rotation" (position + 3x3 rotation from a
//     yaw), and for an actor with physics (+0x578, like the people's cutscene copies) also with the physics
//     transform, else its rigid body puts it back where it was spawned every frame; despawn with the game's "remove
//     at a safe point later".
// `ghost pos` targets (about 20 a second from tools/mp/client.py) are smoothed: each tick the shown pose moves in a
// straight line from where it was when the latest target came to that target, over the measured time between
// targets, and the yaw turns the short way; a target more than 30 m away is jumped to (smooth_* below).
// When the ghost goes away without a despawn (the link turns invalid: a reload after Link died did that in the test;
// or the spawn heap or the player actor changes) it is forgotten without being touched and spawned again once the new
// stage takes requests. A request that is refused, fails or times out is tried again too (a stage that is loading
// does that for a few seconds). Every 2 s, at most 20 tries in a row: only a ghost that stays 10 s ends the row.
// A ghost the game removes sooner than that waits twice as long each time (2, 4, 8 ... 60 s: back-off).
// Distance: the game removes these actors within a second when they are about 75 m or more from Link (measured
// horizontally; docs/ghost.md, sixth round). So a ghost whose place is farther than FAR_M is not spawned (a live
// one is removed): state "far", keeping the latest target, until it is within NEAR_M again (far_next).
// Animation (out/research/ghost-anim-plan.md): the tick plays the actor's Wait, Walk or Run animation set (AS) by name
// (AS_PLAY), picked from its ground speed with hysteresis, and only when the choice changes. The speed is the one
// `ghost pos` gives (the other player's client measures it from its own positions, so Wi-Fi jitter can't change it),
// else it is worked out from where the ghost is shown over the last half second (anim_*). It reads back what plays
// 5 times a second and asks again (at most once a second) if the actor's own AI changed it. Called from the tick (a
// worker thread), not from the actor's own update. Not proven safe, but a stress test made 17,767 play calls (10 a
// second for 30 minutes: Sheikah man standing, Zelda standing, Sheikah man following a moving Link) without a
// crash or hang (docs/ghost.md, fifth round). If it ever crashes, plan B calls it from the actor's own update.
// 0.5.0 (opt-in: nothing changes unless a client sends it or asks for it; in the game on one PC since 0.5.1, tenth
// and eleventh rounds of docs/ghost.md: names, `ghost self` (a sword's name too) and the Link body work; shield and
// bow names and two PCs not tested yet):
// - `ghost pos ... speed <name>` also gives the other player's own animation name (their `ghost self`). The ghost
//   then plays that name when its actor has it, else a stand-in for it (an attack: CutS1), else Wait/Walk/Run by
//   speed as before (anim_remote_pick). Which names the actor lacks is learned: a name it doesn't play
//   ANIM_VERIFY_S after the call is asked once more; one that misses ANIM_STRIKES times is noted and not asked for
//   again on that actor (0.5.1; before, one miss was enough, so a name the game showed a little late was lost).
// 0.5.1: room for every name Link has (MAX_ANIM_NAMES, MAX_MISSING: 512 each; Link's own list has 399), and the last
//   ANIM_NAMES_OWN name slots are kept for `ghost anim`, so names from the network can never lock it out.
// - `ghost self` reads your own Link's state in the tick: his body animation name, attack or not, and the names of
//   his weapon, shield and bow (out/research/player-state-plan.md). Plain memory reads, each address checked first
//   (self_*), no game calls; only while a client asks (SELF_KEEP_S).
// - `ghost spawn link`: the Link body (LINK_ACTOR). It looks like Link only with the Link body pack deployed
//   (tools/mp/ghost_link_pack.py, docs/ghost.md); without it, it is the Sheikah man.
// 0.6.0 (opt-in: nothing changes until a `ghost items`; out/research/ghost-items-plan.md, docs/ghost.md "Held items"):
// - `ghost items w s b [mask]`: the ghost holds real item actors (weapon, shield, bow) through the item's own grab call
//   with the ghost as its holder; the game keeps each on its hand bone and turns its pick-up off. A change deletes the
//   old item (never lets it go); mask: which go on the back (our own bone follow on Pod_A). Items go before the ghost
//   does. Only on a body with the hand bones (the Link body). See "held items" below.
// - `ghost self` also says which of Link's items are on his back (`on_back`); `ghost give` spawns an item for Link
//   to take (tests).
//
// Commands (developer mode, SAVAGEWIND_CONTROL=<port>):
//   ghost                                state (ticks, threads, the actor, its position, its animation)
//   ghost spawn [name|link] [dx dy dz]   spawn an actor near Link (default Dm_Npc_Sheiker_Searcher_M_001, 3 m along
//                                        x; the crate Dm_Kibako_Contain_01 if the default fails 5 times in a row
//                                        before it ever spawned; link: LINK_ACTOR, never the crate)
//   ghost pos <x> <y> <z> [yaw [speed [anim]]]  move it there, smoothly (world coordinates; yaw in degrees, 0 =
//                                        facing +z; speed: the other player's ground speed in m/s, for Wait/Walk/Run;
//                                        left out or negative: worked out from the positions; anim: the other
//                                        player's animation name, - for none)
//   ghost follow <dx> <dy> <dz>          keep it at that offset from Link (a demo without a network)
//   ghost stop                           stop moving it
//   ghost despawn                        remove it
//   ghost anim auto|off|<AS name>        animation: Wait/Walk/Run by speed (the default), none, or always that one
//   ghost far <far_m> [near_m]           the distance rule (default 60 50; ghost far 0: off)
//   ghost self                           your own Link: animation, attack, weapon, shield, bow (read only)
//   ghost items <w|-> <s|-> <b|-> [mask]  what the ghost holds (mask 0-7: bit 0 weapon, 1 shield, 2 bow on the back;
//                                        default 4); ghost items off; ghost items back on|off
//   ghost give <actor> [dx dy dz]        spawn an actor near Link for him to take (tests; never the ghost's items)
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "swmod.h"
#include "botw.h"  // (only BOTW_ADDR_MAIN and BOTW_ADDR_GUEST_END: the guest window for `ghost self`'s pointer check)

#define GHOST_VERSION "0.6.2"  // (the same as mod.toml)

// ---- Win32 / C runtime (declared here: the zig that ships with Savage Wind has no windows.h or stdio.h)
__declspec(dllimport) unsigned long __stdcall GetCurrentThreadId(void);
__declspec(dllimport) int __stdcall QueryPerformanceCounter(int64_t *out);
__declspec(dllimport) int __stdcall QueryPerformanceFrequency(int64_t *out);
__declspec(dllimport) void *__stdcall GetCurrentThread(void);
__declspec(dllimport) int __stdcall QueryThreadCycleTime(void *thread, uint64_t *cycles);
typedef struct {  // MEMORY_BASIC_INFORMATION (64-bit)
    uint64_t base, alloc_base;
    uint32_t alloc_protect;
    uint16_t partition, pad0;
    uint64_t size;
    uint32_t state, protect, type, pad1;
} MemInfo;
_Static_assert(sizeof(MemInfo) == 48, "MEMORY_BASIC_INFORMATION is 48 bytes");
__declspec(dllimport) size_t __stdcall VirtualQuery(const void *addr, MemInfo *info, size_t len);
__declspec(dllimport) void *__stdcall CreateThread(void *attr, size_t stack, unsigned long(__stdcall *fn)(void *),
                                                   void *arg, unsigned long flags, unsigned long *tid);
__declspec(dllimport) int __stdcall SetThreadPriority(void *thread, int prio);
__declspec(dllimport) void __stdcall Sleep(unsigned long ms);
__declspec(dllimport) unsigned long __stdcall WaitForSingleObject(void *h, unsigned long ms);
__declspec(dllimport) int __stdcall CloseHandle(void *h);
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wbuiltin-requires-header"
int snprintf(char *buf, size_t n, const char *fmt, ...);
int vsnprintf(char *buf, size_t n, const char *fmt, __builtin_va_list ap);
#pragma clang diagnostic pop

// ---- BotW 1.9.0 (main) addresses: out/research/actor-spawn-notes.md and actor-spawn-verify.md
#define FRAME_JOB 0x7001129af8ull        // vf_2 of the "Awereness+LOD+DL1" calc job: once per frame
#define PLAYER_INFO_PTR 0x7001e13ed0ull  // u64: the player info block
#define PLAYER_INFO_GET 0x7000cc6fd0ull  // (player info block) -> player actor (its +0x60)
#define PI_PLAYER 0x60                   // player info block: player actor
#define PI_POS 0x88                      // player info block: position copy (3 floats)
#define CREATOR_PTR 0x7001e95430ull      // u64: the actor spawner
#define CREATOR_BLOCKED 0x59             // u8: set = async requests refused
#define HEAP_HOLDER_PTR 0x7001e14cd8ull  // u64: the scene object; +0x28 = the heap for runtime spawns
#define HEAP_OFF 0x28
#define REQUEST_CREATE 0x7001535b10ull   // (spawner, name, heap, handle, pack, 0, w6) -> bool
#define PACK_CLEAR 0x700112615cull       // (buf)
#define PACK_ADD 0x7001126168ull         // (buf, value*, string object* key, size, type)
#define PACK_VPTR 0x7001bc5540ull
#define STRING_OBJ_VPTR 0x7001bc32c8ull
#define KEY_M 0x700160f9e5ull            // "@M" in main's rodata (the pack keeps the key pointer)
#define HANDLE_CLEAR 0x7001516814ull     // (handle)
#define HANDLE_READY 0x7001516820ull     // (handle) -> bool
#define HANDLE_FAILED 0x7001516840ull    // (handle) -> bool
#define HANDLE_PEEK 0x7001516a5cull      // (handle) -> proc (not taken)
#define HANDLE_TAKE 0x7001516b74ull      // (handle) -> proc (taken and woken; handle cleared)
#define HANDLE_RELEASE_FAILED 0x70015168a8ull  // (handle): release if failed
#define HANDLE_RELEASE 0x7001516908ull   // (handle): release (an untaken proc is deleted)
#define ACTOR_TYPEINFO 0x700056b6d0ull   // () -> Actor type info; proc->vtable[2](proc, info) = isA
#define LINK_SET 0x7001517e60ull         // (link, proc, acquire)
#define LINK_VALID 0x7001517e0cull       // (link) -> bool
#define LINK_RESET 0x7001517f8cull       // (link)
#define SET_MTX 0x700152261cull          // (actor, 3x4 matrix*, scale* or 0)
#define SET_TRANSFORM 0x70015223f0ull    // (actor, pos*, rot33*, scale*) any may be 0
#define PHYS_SET_MTX 0x7001212820ull     // (actor, 3x4 matrix*): moves the rigid bodies of an actor with physics
#define ACTOR_PHYSICS 0x578              // Actor: its physics (0: none)
#define DELETE_LATER 0x7001514bacull     // (proc, reason 0): remove it at a safe point later
#define ACTOR_MTX 0x398                  // Actor: 3x4 float matrix, row-major (translation at +0x3a4/+0x3b4/+0x3c4)

// Animation (out/research/ghost-anim-plan.md): play an animation set (AS) of the actor's ASList by name
#define AS_PLAY 0x700151e42cull          // (action, name, keep_if_same, slot, sub; s0 morph): reads only action+8
                                         // (the actor), then actor+0x560; keep_if_same: nothing if it already plays
#define AS_CURRENT 0x70014a0040ull       // (holder, slot, sub) -> string object* of the playing AS (char* at +8)
#define ACTOR_AS 0x560                   // Actor: AS holder (0, or a static dummy with no owner: no animations)
#define AS_OWNER 0xd8                    // holder: owner actor
#define AS_SLOTS 0xb8                    // holder: s32 slot count

// The default stand-in: the cutscene copy of a Sheikah man (Hylian body): rigid bodies (moved with PHYS_SET_MTX), no
// AI (DemoDummy: nothing changes its animation), Wait/Walk/Run, no cloth. Without an animation it stands in a T-pose.
// Dm_Npc_Zelda_Sibyl (Zelda in her white dress) also works, but runs the NPC AI and costs 3-7 frames a second.
#define DEFAULT_ACTOR "Dm_Npc_Sheiker_Searcher_M_001"
// `ghost spawn link`: the Link body. tools/mp/ghost_link_pack.py builds, from the user's own game files, a pack under
// the default's name with Link's model and Link's animations under the player's own names (Wait, Walk, Run, CutS1...;
// out/research/ghost-link-plan.md). So it is the same name: Link with that pack deployed, the Sheikah man without.
#define LINK_ACTOR DEFAULT_ACTOR
#define FALLBACK_ACTOR "Dm_Kibako_Contain_01"  // the cutscene copy of a wooden crate, when the default fails
                                               // FALLBACK_AFTER times in a row before it ever spawned
#define FALLBACK_AFTER 5
#define PENDING_TIMEOUT 30.0             // seconds before a request that never gets ready is released
#define RESPAWN_DELAY 2.0                // seconds between tries to spawn it again
#define MAX_RESPAWNS 20                  // tries in a row
#define STABLE_S 10.0                    // a ghost live this long ends the row (a quicker loss counts as a try)
#define BACKOFF_MAX 60.0                 // seconds: the longest wait after the game removed the ghost again and again

// Distance (horizontal, from Link to where the ghost should be). The game removed the Sheikah man and Zelda within
// a second at 76-80 m and kept them at 70-74 m (in 3 directions; 300 m straight up was kept): docs/ghost.md.
#define FAR_M 60.0f                      // farther than this: not spawned (state "far"); a live one is removed
#define NEAR_M 50.0f                     // ... until it is within this again

// Smoothing of `ghost pos` targets (they come about 20 times a second; the tick runs once per frame)
#define SNAP_DIST 30.0f                  // metres: a target this far from where the ghost is shown is jumped to
#define INTERVAL_DEFAULT 0.05            // seconds between targets before two have arrived
#define INTERVAL_MIN 0.016               // the measured time between targets is clamped to this range
#define INTERVAL_MAX 0.25
#define YAW_MIN_S 0.1                    // a turn takes at least this long

// `ghost anim auto`: Wait, Walk or Run from the ghost's ground speed (m/s), with hysteresis. The speed is the one the
// latest `ghost pos` gave, if it gave one less than ANIM_GIVEN_S ago (the client sends the other player's own speed:
// tested on two PCs over Wi-Fi on 2026-11-07, the ghost mostly stood while the other player ran, because the speed
// worked out here from positions that came unevenly was too often thrown away). Otherwise it is worked out here: the
// straight distance the shown pose (where the tick puts it, sampled each frame) covered over the last ANIM_WINDOW_S.
// A position that comes late, after a network stall, only moves the ghost later, so over the window it still counts.
// During a stall the other client repeats the last place it had (a `ghost pos` the same as the one before): the ghost
// stands there, but it was really there only when the repeats began, so for ANIM_STALL_S it is timed from then (the
// speed stays as it was, and the catch-up after the stall is not taken for a burst of speed). Repeats for longer:
// the player stopped. A jump is not a speed (anim_target, anim_sample). A worked-out speed that drops changes the
// choice to a slower one only after ANIM_DOWN_S (a frame hitch delays the ghost a moment before it catches up).
#define ANIM_WALK_FROM 0.4f              // Wait -> Walk at this speed or more
#define ANIM_WAIT_BELOW 0.3f             // Walk or Run -> Wait below this
#define ANIM_RUN_FROM 3.2f               // Walk -> Run at this speed or more
#define ANIM_RUN_BELOW 2.6f              // Run -> Walk below this
#define ANIM_HOLD_S 0.25                 // a choice is kept at least this long
#define ANIM_DOWN_S 0.2                  // worked-out speed: a slower choice must be wanted this long before it's taken
#define ANIM_STALL_S 0.6                 // `ghost pos` repeats are timed from the first one for this long
#define ANIM_SAME_M 0.005f               // m: a `ghost pos` this close to the one before is a repeat
#define ANIM_GIVEN_S 1.0                 // a speed `ghost pos` gave counts this long (no `ghost pos` since: worked out)
#define ANIM_WINDOW_S 0.5                // seconds of shown positions the speed is worked out over
#define ANIM_MIN_SPAN_S 0.1              // ... once there are this many (after a jump or a gap: the last speed stays)
#define ANIM_GAP_S 1.0                   // no frame, or no `ghost pos`, for this long (a load, a pause, a stuck
                                         // client): how it got to the next position is not known; measured again
#define ANIM_SPEED_TAU 0.1               // seconds: time constant of the low-pass filter on top
#define ANIM_MAX_SPEED 40.0f             // m/s: a `ghost pos` step faster than this is a jump (a short teleport)...
#define ANIM_CATCHUP_S 1.0               // ... unless the ghost moved (walking speed or more) within this long and
#define ANIM_CATCHUP_M 10.0f             // the step is at most this long: a late position catching up after a stall
#define ANIM_JUMP_GLIDES 4               // the ghost is at a jump's target after about 3 target intervals (each new
                                         // target starts the move again from where it is): the window starts after 4
#define ANIM_READ_S 0.2                  // read back the playing AS this often
#define ANIM_REASSERT_S 1.0              // it plays something else (the actor's AI changed it): ask again this often
#define ANIM_MISSES 3                    // ... but a name it never played only this many times
#define ANIM_VERIFY_S 0.1                // a name from `ghost pos ... anim` that doesn't play this long after the
                                         // call is a miss (asked again at once: it may just show a little late) ...
#define ANIM_STRIKES 2                   // ... and after this many misses the actor doesn't have it (noted; not
                                         // asked for again on it). A name that plays clears its misses.
#define MAX_MISSING 512                  // names noted per actor (more: the oldest is forgotten); Link has 399

// `ghost self`: your own Link's state, read in the tick (out/research/player-state-plan.md: static research, NOT
// TESTED IN GAME). Link = the player info block's +0x60, checked by its class. Every pointer is checked before it is read (mem_ok).
#define PLAYER_VPTR 0x7001cd7bb8ull      // Link's actor class
#define AS_SLOT_ARRAY 0xc0               // holder: the slot array (AS_SLOTS slots of AS_SLOT_SIZE bytes)
#define AS_SLOT_SIZE 0x50
#define AS_SLOT_NSUB 0x20                // slot: s32 count of subs
#define AS_SLOT_SUBS 0x28                // slot: the sub array (AS_SUB_SIZE bytes each)
#define AS_SUB_SIZE 0x98
#define AS_SUB_DEF 0x00                  // sub: the AS definition; its name is the char* at +AS_DEF_NAME
#define AS_SUB_WEIGHT 0x10               // sub: f32 blend weight
#define AS_SUB_LIVE 0x18                 // sub: non-zero while something plays
#define AS_DEF_NAME 0x10
#define ACTOR_EQUIP 0xb90                // actor: equipment holder (vptr EQUIP_VPTR, 6 links, owner at +EQUIP_OWNER)
#define EQUIP_VPTR 0x7001d349d8ull
#define EQUIP_LINKS 0x8                  // the first link (data*, u32 id), then one every EQUIP_LINK_SIZE bytes:
#define EQUIP_LINK_SIZE 0x18             // 0 weapon, 1 shield, 2 bow
#define EQUIP_OWNER 0x98
#define LINKDATA_ID 0x40                 // link data: u32 id (the link's must match)
#define LINKDATA_PROC 0x48               // link data: the item's actor (0: gone)
#define PROC_NAME 0x10                   // actor: char* of its name (Weapon_Sword_001...)
#define PROC_STATE 0x64                  // actor: u8 state, PROC_DELETING while it is deleted
#define PROC_DELETING 3
#define WEAPON_VPTR 0x7001c3f330ull      // the Weapon class (reported, not required)
#define SELF_SLOTS 5                     // slots read for `shape` (Link: 2, 2, 1, 3, 1 subs)
#define SELF_KEEP_S 2.0                  // read each frame while a `ghost self` came less than this long ago
#define SELF_EQUIP_S 0.25                // ... the equipment this often
#define ATTACK_HOLD_S 0.3                // an attack is reported at least this long (a client asking 20 times a second
                                         // sees every one; a quick parry or combo hit can be shorter)
#define SELF_WEIGHT_MIN 0.01f            // an overlay (upper body) counts above this weight
#define EQUIP_BACK 0x10                  // equipment slot (+EQUIP_LINKS + i*EQUIP_LINK_SIZE): u8, not 0 = on the back
#ifndef GUEST_LO                         // (the selftest widens the window to its own made-up memory)
#define GUEST_LO BOTW_ADDR_MAIN          // the guest window (Savage Wind: guest address = host address)
#define GUEST_HI BOTW_ADDR_GUEST_END
#endif

// Held items (0.6.0, opt-in: only after a `ghost items`; out/research/ghost-items-plan.md, static research; what the
// game showed: docs/ghost.md). The ghost becomes the holder of real item actors through the item's own grab call; the
// game then keeps each one on the ghost's hand bone and turns its pick-up off. Weapon class only (WEAPON_VPTR):
// swords, two-handers, spears, shields, bows.
#define ITEM_GRAB_VF 0x568               // item vtable: grab(item, slot, holder, hand char*, pod char*, 0, 0) -> bool;
#define WEAPON_GRAB 0x70007ac82cull      // ... the Weapon class's (its vtable is checked to hold it, when readable)
#define ITEM_STATE 0x920                 // item: u8 grab state: 0 asked, 1 let go, 2 dropped, 0xff settled
#define ITEM_HOLDER 0x938                // item: link to its holder (data*, u32 id), set once it is settled
#define ITEM_HOLD_ASK 0x880              // item: link to the holder a grab asked for (the grab sets it at once)
#define ACTOR_FLAGS 0x520                // actor: u32 flags; bit 5 set: FOLLOW_SET refuses, and the actor deletes the
#define FLAG_OWNS_FOLLOW (1u << 5)       // follow at +0x4d0 when it goes (so ours must never be there then)
#define ITEM_PARK_UP 6.0f                // m above the ghost's feet (about 4 m over its head, out of Link's reach): a
                                         // new item is spawned there and kept there until the ghost's grab took it
#define ITEM_FOLLOW 0xa00                // item: its own bone follow (the game uses it for the hand)
#define ITEM_KIND 0xcf0                  // Weapon: u32 kind: 0 one-handed, 1 two-handed, 2 spear, 3 shield, 4 bow
#define ACTOR_PARAMS 0x570               // actor: -> +PARAMS_LIST: its parameter list
#define PARAMS_LIST 0xa8
#define PLIST_COUNT 0x2b0                // parameter list: s32 object count
#define PLIST_OBJS 0x2b8                 // ... and the object array (u64 each)
#define ACTOR_FOLLOW 0x4d0               // actor: the bone follow its frame update runs (0: none). Never written 0
                                         // here: the game reads it again without a check (a null call)
#define FOLLOW_SET 0x7001533750ull       // (actor, follow): sets ACTOR_FOLLOW
#define FOLLOW_CTOR 0x7001096f68ull      // (follow): builds a bone follow (FOLLOW_SIZE bytes; writes fields only)
#define FOLLOW_TARGET 0x08               // follow: link to the actor it follows
#define FOLLOW_BONE 0x28                 // follow: char* of the bone's name
#define FOLLOW_INDEX 0x60                // follow: u16 unit, u16 bone (0xffff: look the name up again)
#define FOLLOW_OFFSET 0x68               // follow: 3x4 offset matrix from the bone
#define FOLLOW_MODE 0x98                 // follow: u32 mode (0)
#define FOLLOW_SIZE 0xa0
#define MTX_FROM_EULER 0x700056b4b0ull   // (out 3x4*, rot xyz radians*, trans*)
#define ACTOR_MODEL 0x4e0                // actor: its model holder (vptr MODEL_VPTR)
#define MODEL_VPTR 0x7001d0fa48ull
#define ACTOR_SCALE 0x418                // 0.6.2: actor: its scale (3 floats); each frame the game copies it, with the
#define MODEL_SCALE 0x88                 // actor's matrix, to its model holder (+0x88): the model is drawn with both
#define WEAPON_CALC 0x70007a95d8ull      // 0.6.2: Weapon vtable +0x228, its per-frame update. Each frame it sets the
                                         // weapon's scale (ACTOR_SCALE) by its holder (0x70007a9e18): in the player's
                                         // hands its base scale times its parameter object WEAPON_SCALE_OBJ's
                                         // WEAPON_SCALE_PLAYER; held by anyone else (the ghost, a DemoNPC) times
                                         // +0x70: 1.4-1.5 (fourteenth round: the ghost's items 1.4-1.5 times Link's)
#define WEAPON_BASE_SCALE 0xd5c          // Weapon: its base scale (3 floats)
#define WEAPON_SCALE_OBJ 9               // its parameter object with the scales (object 0 when it has 9 or fewer)
#define WEAPON_SCALE_PLAYER 0x50         // ... the factor in the player's hands
#define MODEL_NUNITS 0x38                // model holder: s32 unit count
#define MODEL_UNITS 0x40                 // ... the unit slots (u64 each; the unit at slot+0)
#define UNIT_VPTR_A 0x7001d10738ull      // the two model unit classes
#define UNIT_VPTR_B 0x7001d52200ull
#define UNIT_SKEL 0x118                  // unit: its skeleton; skeleton +0: its resource (FSKL_MAGIC)
#define SKEL_WORLD 0x20                  // skeleton: bone world matrices, 0x40 each, column by column (translation at
                                         // +0x30 +0x34 +0x38)
#define FSKL_MAGIC 0x4c4b5346u           // "FSKL"
#define RES_DICT 0x10                    // skeleton resource: bone names (bone i: char* at +0x20+i*0x10, 2 bytes on)
#define RES_NBONES 0x3c                  // ... u16 bone count
#define ITEM_PENDING_S 10.0              // an item request not ready in this long is released
#define ITEM_GRAB_S 1.0                  // the grab is asked each tick for this long (refused until the item's AI ran)
#define ITEM_SETTLE_S 1.0                // ... then the item must be settled in the ghost's hand within this long
#define ITEM_RETRY_S 5.0                 // a failed try: the next one after this long ...
#define ITEM_TRIES 3                     // ... at most this many per name
#define ITEM_LOST_RETRY_S 2.0            // a held item that let go or lost its holder: deleted, asked again after this
#define ITEM_NOFOLLOW_S 0.5              // a held item with no bone follow for this long is deleted (and asked again)
#define ITEM_STATUS_S 0.2                // distances to the bones are read this often
#define ITEM_BONES_S 0.25                // a new ghost's bones are looked up this often ...
#define ITEM_BONES_FOR_S 5.0             // ... for at most this long
#define TRASH_MAX 16                     // deleted items watched until they are gone
#define TRASH_STRAY_S 1.0                // a deleted item still there after this long is a stray
#define TRASH_FORGET_S 30.0              // ... watched at most this long
#define LEFTOVER_WAIT_S 3.0              // 0.6.2: after a stage change, Link back this long on the new stage: what
#define LEFTOVER_WATCH_S 90.0            // it dropped and the game kept is removed; watched at most this long
#define MAX_ITEM_NAMES 256               // different item names used (the request keeps the pointer)
#define SLOW_CALL_MS 5.0                 // a game call that takes longer is counted and noted (its name, its time)
#define SLOW_PART_MS 5.0                 // an items part that takes longer is counted (wall time and the thread's own
                                         // cycles: few cycles = the thread was switched out, not working)
#define SLOW_FRAME_MS 50.0               // a frame (tick to tick) longer than this is counted as slow

static const SwModApi *api;
static SwModGuestFn g_orig_job;

// ------------------------------------------------------------------ small helpers

static int64_t g_qpf;
static double now_s(void) {
    int64_t t;
    QueryPerformanceCounter(&t);
    return (double)t / (double)g_qpf;
}

static uint64_t rd64(uint64_t a) { return a ? *(volatile const uint64_t *)(uintptr_t)a : 0; }
static uint8_t rd8(uint64_t a) { return *(volatile const uint8_t *)(uintptr_t)a; }
static float rdf(uint64_t a) {
    float f;
    memcpy(&f, (const void *)(uintptr_t)a, sizeof f);
    return f;
}

// A spin lock for what the command (frame thread) and the tick (a game worker thread) share; held only for copies.
static int g_lock;
static void lock(void) {
    while (__atomic_test_and_set(&g_lock, __ATOMIC_ACQUIRE)) __builtin_ia32_pause();
}
static void unlock(void) { __atomic_clear(&g_lock, __ATOMIC_RELEASE); }

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

static int64_t g_ncalls, g_nqueries;  // game calls made and memory queries (VirtualQuery) done, for the cost
static int64_t g_slow_calls;           // 0.6.1: game calls that took over SLOW_CALL_MS (wall clock) ...
static uint64_t g_slow_fn;             // ... the latest one's address and time, and the longest call seen
static double g_slow_ms, g_call_most_ms;

// Call a guest function with up to 7 integer arguments; returns x0. Each call is timed (two counter reads).
static uint64_t gcall(struct Cpu *c, uint64_t fn, uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4,
                      uint64_t a5, uint64_t a6) {
    SwModRegs *r = SWMOD_REGS(c);
    r->x[0] = a0, r->x[1] = a1, r->x[2] = a2, r->x[3] = a3, r->x[4] = a4, r->x[5] = a5, r->x[6] = a6;
    int64_t q0, q1;
    QueryPerformanceCounter(&q0);
    api->guest_call(c, fn);
    QueryPerformanceCounter(&q1);
    g_ncalls++;
    double ms = g_qpf > 0 ? (double)(q1 - q0) * 1e3 / (double)g_qpf : 0.0;
    if (ms > g_call_most_ms) g_call_most_ms = ms;
    if (ms > SLOW_CALL_MS) g_slow_calls++, g_slow_fn = fn, g_slow_ms = ms;
    return r->x[0];
}
#define P(v) ((uint64_t)(uintptr_t)(v))
#define GCALL1(c, fn, a) gcall(c, fn, P(a), 0, 0, 0, 0, 0, 0)
#define GCALL2(c, fn, a, b) gcall(c, fn, P(a), P(b), 0, 0, 0, 0, 0)
#define GCALL3(c, fn, a, b, d) gcall(c, fn, P(a), P(b), P(d), 0, 0, 0, 0)

// A 3x3 rotation about y (row-major): yaw 0 faces +z, 90 faces +x.
static void yaw_rot(float yaw_deg, float r[9]) {
    float a = yaw_deg * 0.017453292519943295f, s = __builtin_sinf(a), co = __builtin_cosf(a);
    float m[9] = {co, 0, s, 0, 1, 0, -s, 0, co};
    memcpy(r, m, sizeof m);
}

// ------------------------------------------------------------------ smoothing (pure: no game calls)
//
// The shown pose (x y z yaw) moves from where it was when the latest target arrived to that target, in a straight
// line, over the time the targets come apart (smoothed), so 20 targets a second become one move per frame at an even
// speed. Yaw turns the short way round. A target more than SNAP_DIST away is jumped to (a teleport). Two targets are
// kept with their arrival times: the latest (to, to_t) and the one before (prev_t), for the interval.

typedef struct {
    float from[4], to[4], shown[4];  // x y z yaw (degrees)
    double start_t;                  // when the move from `from` to `to` started (to_t, or when the ghost was placed)
    double to_t, prev_t;             // arrival times of the latest target and the one before (0: none yet)
    double interval;                 // smoothed time between targets (0: not measured yet)
    int has_to, placed;              // a target came; the ghost is shown somewhere (0: the next target is jumped to)
    int64_t targets, snaps;
} Smooth;

// a - b the short way round, in [-180, 180)
static double angle_diff(double a, double b) {
    double d = a - b;
    return d - 360.0 * __builtin_floor((d + 180.0) / 360.0);
}

static float dist3(const float a[3], const float b[3]) {
    float dx = a[0] - b[0], dy = a[1] - b[1], dz = a[2] - b[2];
    return __builtin_sqrtf(dx * dx + dy * dy + dz * dz);
}

// The ghost is now shown at `pose` (just spawned, or moved by `follow`): the next move starts from there.
static void smooth_place(Smooth *s, const float pose[4], double t) {
    memcpy(s->shown, pose, sizeof s->shown);
    memcpy(s->from, pose, sizeof s->from);
    if (!s->has_to) memcpy(s->to, pose, sizeof s->to);
    s->start_t = t;
    s->placed = 1;
}

// Nothing is shown any more (despawned, lost): the next target is jumped to.
static void smooth_forget(Smooth *s) { s->placed = 0; }

// A new target arrived at time t.
static void smooth_target(Smooth *s, const float target[4], double t) {
    if (s->has_to) {
        double dt = t - s->to_t;
        if (dt > 0) {
            if (dt < INTERVAL_MIN) dt = INTERVAL_MIN;
            if (dt > INTERVAL_MAX) dt = INTERVAL_MAX;
            s->interval = s->interval > 0 ? s->interval + (dt - s->interval) * 0.25 : dt;
        }
    }
    s->prev_t = s->to_t;
    if (!s->placed || dist3(s->shown, target) > SNAP_DIST) {
        if (s->placed) s->snaps++;
        memcpy(s->shown, target, sizeof s->shown);
    }
    memcpy(s->from, s->shown, sizeof s->from);
    memcpy(s->to, target, sizeof s->to);
    s->to_t = s->start_t = t;
    s->has_to = 1;
    s->targets++;
}

// The pose to show at time t (also kept as s->shown).
static void smooth_step(Smooth *s, double t, float out[4]) {
    double dur = s->interval > 0 ? s->interval : INTERVAL_DEFAULT;
    double f = (t - s->start_t) / dur, fy = (t - s->start_t) / (dur > YAW_MIN_S ? dur : YAW_MIN_S);
    f = f < 0 ? 0 : f > 1 ? 1 : f;
    fy = fy < 0 ? 0 : fy > 1 ? 1 : fy;
    for (int k = 0; k < 3; k++) out[k] = (float)(s->from[k] + (s->to[k] - s->from[k]) * f);
    double yaw = s->from[3] + angle_diff(s->to[3], s->from[3]) * fy;
    out[3] = (float)angle_diff(yaw, 0.0);
    memcpy(s->shown, out, sizeof s->shown);
}

// ------------------------------------------------------------------ animation choice (pure: no game calls)

enum { AN_NONE, AN_WAIT, AN_WALK, AN_RUN };
static const char AS_WAIT[] = "Wait", AS_WALK[] = "Walk", AS_RUN[] = "Run";  // (static: the game gets the pointer)
static const char *const AN_NAMES[] = {"", AS_WAIT, AS_WALK, AS_RUN};

// The animation for a ground speed v (m/s), given the current one: thresholds with hysteresis, so a speed near one
// doesn't flip it every frame.
static int anim_pick(int cur, float v) {
    switch (cur) {
    case AN_WALK:
        return v >= ANIM_RUN_FROM ? AN_RUN : v < ANIM_WAIT_BELOW ? AN_WAIT : AN_WALK;
    case AN_RUN:
        return v < ANIM_WAIT_BELOW ? AN_WAIT : v < ANIM_RUN_BELOW ? AN_WALK : AN_RUN;
    default:  // AN_WAIT, AN_NONE
        return v >= ANIM_RUN_FROM ? AN_RUN : v >= ANIM_WALK_FROM ? AN_WALK : AN_WAIT;
    }
}

#define ANIM_SAMPLES 96  // shown positions kept, one a frame: ANIM_WINDOW_S of them up to 190 frames a second

typedef struct {               // (start from a zeroed one)
    float x[ANIM_SAMPLES], z[ANIM_SAMPLES];
    double t[ANIM_SAMPLES];    // the frame's time
    double tt[ANIM_SAMPLES];   // when the ghost was really there: the frame's time, or when the repeats began
    int n, head;               // shown positions kept; the newest
    double skip_until;         // the ghost glides to a jump until then: not a speed, the window starts after it
    float tx, tz;              // the latest `ghost pos` target (x z) ...
    double target_t;           // ... and when it came
    int has_target;
    int repeating;             // the latest `ghost pos` repeats the one before (a stall, or the player stopped) ...
    double repeat_t;           // ... since the first repeat came at this time
    double usual_dt;           // usual time between targets (smoothed)
    float speed;               // ground speed (m/s), low-pass filtered: the one the choice uses
    double speed_t;            // when it was last updated (0: never)
    int moved;                 // the speed was walking speed or more ...
    double moved_t;            // ... at this time, the last time
    int down;                  // a slower choice (worked-out speed) is wanted ...
    double down_t;             // ... since this time
    int state;                 // AN_*: the current choice ...
    double changed_t;          // ... made at this time
} AnimAuto;

// A `ghost pos` target came at time t (x z on the ground); glide_s: the time the smoothing takes from one target to
// the next. A jump is not a speed: a step faster than ANIM_MAX_SPEED (over its own time, but at least the usual time
// between targets: one can come a frame early) is one, a short teleport, so the speed is worked out again once the
// ghost is there (ANIM_JUMP_GLIDES * glide_s later). Except a late position: a network stall holds the other player
// still in the client, then the next position catches up in one step, so a step of at most ANIM_CATCHUP_M while the
// ghost moved within the last ANIM_CATCHUP_S counts as moving. A target after more than ANIM_GAP_S with none (a stuck
// client) is not measured either: how it got there is not known. It also notes whether the target repeats the one
// before (anim_sample).
static void anim_target(AnimAuto *a, float x, float z, double t, double glide_s) {
    if (a->has_target) {
        double dt = t - a->target_t;
        if (dt < 0) return;
        float dx = x - a->tx, dz = z - a->tz, d = __builtin_sqrtf(dx * dx + dz * dz);
        double span = dt > a->usual_dt ? dt : a->usual_dt;
        if (span < 0.01) span = 0.01;
        int catchup = a->moved && t - a->moved_t <= ANIM_CATCHUP_S && d <= ANIM_CATCHUP_M;
        if (dt > ANIM_GAP_S || (d > ANIM_MAX_SPEED * span && !catchup)) a->skip_until = t + ANIM_JUMP_GLIDES * glide_s;
        double u = dt < 0.01 ? 0.01 : dt > 0.25 ? 0.25 : dt;
        a->usual_dt = a->usual_dt > 0 ? a->usual_dt + (u - a->usual_dt) * 0.1 : u;
        if (d > ANIM_SAME_M) a->repeating = 0;
        else if (!a->repeating) a->repeating = 1, a->repeat_t = t;
    }
    a->tx = x, a->tz = z, a->target_t = t, a->has_target = 1;
}

// The ghost is shown at x z (ground plane) at time t: once a frame, where the tick put it (or where it is). Gliding to
// a jump (anim_target), a step over ANIM_MAX_SPEED * ANIM_WINDOW_S (alone faster than that over the window: a snap
// to a teleport) and a frame after more than ANIM_GAP_S without one (a load) start the window again. While `ghost pos`
// repeats (for at most ANIM_STALL_S), the ghost was really here when the repeats began.
static void anim_sample(AnimAuto *a, float x, float z, double t) {
    if (t < a->skip_until) {
        a->n = 0;
        return;
    }
    if (a->n) {
        double dt = t - a->t[a->head];
        if (dt < 0) return;
        float dx = x - a->x[a->head], dz = z - a->z[a->head], d = __builtin_sqrtf(dx * dx + dz * dz);
        if (d > ANIM_MAX_SPEED * ANIM_WINDOW_S || dt > ANIM_GAP_S) a->n = 0;
    }
    a->head = (a->head + 1) % ANIM_SAMPLES;
    int stalled = a->repeating && t - a->repeat_t <= ANIM_STALL_S;
    a->x[a->head] = x, a->z[a->head] = z, a->t[a->head] = t;
    a->tt[a->head] = stalled && a->repeat_t < t ? a->repeat_t : t;
    if (a->n < ANIM_SAMPLES) a->n++;
}

// The worked-out speed: the straight distance from the newest shown position at least ANIM_WINDOW_S older (or the
// oldest kept) to the newest, over the time the ghost was really at each (*ok 0: less than ANIM_MIN_SPAN_S apart, as
// while it waits out a stall: keep the last one).
static float anim_window_speed(const AnimAuto *a, int *ok) {
    *ok = 0;
    if (a->n < 2) return 0;
    int h = a->head, i = h;
    for (int k = 1; k < a->n; k++) {
        i = (h - k + ANIM_SAMPLES) % ANIM_SAMPLES;
        if (a->t[h] - a->t[i] >= ANIM_WINDOW_S) break;
    }
    double dt = a->tt[h] - a->tt[i];
    if (dt < ANIM_MIN_SPAN_S) return 0;
    float dx = a->x[h] - a->x[i], dz = a->z[h] - a->z[i];
    *ok = 1;
    return (float)(__builtin_sqrtf(dx * dx + dz * dz) / dt);
}

// The speed `ghost pos` gave (v, at time v_t; v < 0: it gave none) if it still counts at time t, else -1.
static float anim_given(float v, double v_t, double t) { return v >= 0 && t - v_t < ANIM_GIVEN_S ? v : -1.0f; }

// One frame at time t, after anim_sample: update the speed (given >= 0: the one `ghost pos` gave, else worked out),
// then the choice (kept at least ANIM_HOLD_S; from a worked-out speed, a slower one only once it has been wanted for
// ANIM_DOWN_S). Returns the choice.
static int anim_auto_step(AnimAuto *a, double t, float given) {
    int ok = 1;
    float v = given >= 0 ? given : anim_window_speed(a, &ok);
    if (ok) {
        double dt = a->speed_t > 0 ? t - a->speed_t : 1.0;
        if (dt > 0) a->speed += (float)((v - a->speed) * (1.0 - __builtin_exp(-dt / ANIM_SPEED_TAU)));
    }
    a->speed_t = t;
    if (a->speed >= ANIM_WALK_FROM) a->moved = 1, a->moved_t = t;
    int next = anim_pick(a->state, a->speed);
    if (next < a->state && given < 0) {  // (AN_WAIT < AN_WALK < AN_RUN)
        if (!a->down) a->down = 1, a->down_t = t;
        if (t - a->down_t < ANIM_DOWN_S) next = a->state;
    } else {
        a->down = 0;
    }
    if (a->state == AN_NONE || (next != a->state && t - a->changed_t >= ANIM_HOLD_S)) {
        if (next != a->state) a->changed_t = t;
        a->state = next;
    }
    return a->state;
}

// ------------------------------------------------------------------ the other player's animation names (pure)
//
// `ghost pos ... anim` gives the name the other player's Link plays (their body slot: Wait, Move, Jump, CutS1...;
// out/research/player-state-plan.md). The Link body pack uses the same names (ghost_link_pack.py), so they are asked
// for as they are. Which ones the actor lacks is learned (Missing): the Sheikah man has Wait, Walk, Run, RunGear2 and
// a few more, none of them an attack. A name is missing once it didn't play ANIM_STRIKES times when asked.

typedef struct {
    char name[MAX_MISSING][32];
    uint8_t misses[MAX_MISSING];  // times it didn't play when asked (ANIM_STRIKES or more: missing)
    int n, next;  // names kept; the slot the next one goes to once it is full (the oldest)
} Missing;

static int missing_find(const Missing *m, const char *name) {
    for (int i = 0; i < m->n; i++)
        if (!strncmp(m->name[i], name, 31)) return i;
    return -1;
}

// The actor doesn't have this name (it missed ANIM_STRIKES times).
static int missing_has(const Missing *m, const char *name) {
    int i = missing_find(m, name);
    return i >= 0 && m->misses[i] >= ANIM_STRIKES;
}

// One more miss for name; returns its misses now.
static int missing_strike(Missing *m, const char *name) {
    int i = missing_find(m, name);
    if (i < 0) {
        size_t len = strlen(name);
        if (len > 31) len = 31;
        i = m->n < MAX_MISSING ? m->n++ : m->next;
        m->next = (i + 1) % MAX_MISSING;
        memcpy(m->name[i], name, len);
        m->name[i][len] = 0;
        m->misses[i] = 0;
    }
    if (m->misses[i] < 255) m->misses[i]++;
    return m->misses[i];
}

// name played: its misses so far were the game showing it late.
static void missing_clear(Missing *m, const char *name) {
    int i = missing_find(m, name);
    if (i >= 0) m->misses[i] = 0;
}

static int anim_is_attack(const char *name) {
    return !strncmp(name, "Cut", 3) || !strcmp(name, "SwimAttack");
}

// The name to ask the actor for while the other player plays `remote`, or NULL: Wait/Walk/Run by speed as before.
// Wait and Move (walk, run and sprint are one name, Move, on Link) go by speed. A name the actor has: that name. An
// attack it lacks: CutS1 (the first sword swing), if it has that. Anything else it lacks: by speed.
static const char *anim_remote_pick(const char *remote, const Missing *m) {
    if (!remote || !*remote || !strcmp(remote, "-") || !strcmp(remote, "Wait") || !strcmp(remote, "Move")) return NULL;
    if (!missing_has(m, remote)) return remote;
    if (anim_is_attack(remote) && !missing_has(m, "CutS1")) return "CutS1";
    return NULL;
}

// ------------------------------------------------------------------ distance rule and back-off (pure: no game calls)

// Far or not, for a ghost whose place is d metres from Link (horizontally), given whether it is far now: far above
// far_m, near again below near_m (the gap keeps a player near the limit from making it come and go). far_m <= 0: off.
static int far_next(int far, float d, float far_m, float near_m) {
    if (far_m <= 0 || d < 0) return far_m > 0 && far;  // (off; or d unknown: no change)
    return far ? d >= near_m : d > far_m;
}

// Seconds to wait before the next try after the game removed the ghost `quick` times in a row, each time sooner than
// STABLE_S after it spawned: RESPAWN_DELAY, then twice as long each time, at most BACKOFF_MAX.
static double backoff_delay(int quick) {
    double d = RESPAWN_DELAY;
    for (int i = 1; i < quick && d < BACKOFF_MAX; i++) d *= 2;
    return d < BACKOFF_MAX ? d : BACKOFF_MAX;
}

// ------------------------------------------------------------------ your own Link (`ghost self`: reads only)
//
// out/research/player-state-plan.md (static research). In the game (docs/ghost.md, tenth round): the class check, the
// slot shape 2,2,1,3,1 and the body names (Wait, Move, Jump, Land, Climb..., Fall) read right, and the equipment
// holder passes its checks; eleventh round: a sword Link took reads as Weapon_Sword_001 (Weapon class) and his
// attack as CutS1. Shield and bow names are not tested yet (none in the test save).
// Link's actor A = player info block +0x60, checked by its class. Body animation: slot 0 sub 0 of A's AS holder (+0x560); upper body: slot 1 sub 1 (bow); right
// arm: slot 2 sub 0 (draw, sheathe). Equipment: links at A+0xb98, 0x18 apart (0 weapon, 1 shield, 2 bow), each to the
// item's actor, whose name is the char* at +0x10. No game calls: every address is checked with mem_ok before it is read, so a
// wrong or stale pointer gives an empty or "?" answer, never a crash.

#define MEM_CACHE 32  // (0.6.0: 32, not 8: the items read more regions a frame, and a miss is a VirtualQuery)
#define MEM_KEEP_S 0.5         // a region's answer is kept this long ...
#define MEM_BIG (64ull << 20)  // ... 0.6.2: but one this big (the game's heap: one region of GBs) as long as the helper
#define MEM_BIG_KEEP_S 60.0    // thread saw it unchanged less than this long ago. Asking Windows about such a region
#define MEM_HELPER_MS 20000    // walks all its pages: 10-215 ms each time (fourteenth round: items parts of 30-110 ms
                               // with no game call, twice a second, and a stutter each time). The helper asks this
                               // often, at the lowest priority, only about the big regions the tick used since (rarely:
                               // the walk also holds Windows' lock on the address space, which the game's threads need).
typedef struct {
    uint64_t lo, hi;
    double until;
    uint64_t alloc;  // 0.6.2: the allocation it is in (the helper widens a big region to it when it can)
    int big, used;   // ... big: the helper keeps it; used: the tick read in it since the helper's last look
} MemSpan;
static MemSpan g_mem_cache[MEM_CACHE];  // new answers: the tick (under g_mem_lock); `until`, a wider lo/hi: the helper
static int g_mem_next;
static int g_mem_lock;                  // (held only for copies, never across a VirtualQuery)
static int64_t g_vq_tick, g_vq_slow, g_vq_helper, g_vq_widened;  // VirtualQuery calls on the tick, of those over 5 ms;
static double g_vq_most_ms, g_vq_helper_ms;  // the helper's; big regions it widened; the tick's longest, the helper's

static void mem_lock(void) {
    while (__atomic_test_and_set(&g_mem_lock, __ATOMIC_ACQUIRE)) __builtin_ia32_pause();
}
static void mem_unlock(void) { __atomic_clear(&g_mem_lock, __ATOMIC_RELEASE); }

// MEM_COMMIT; readable (PAGE_READONLY, READWRITE, WRITECOPY and their EXECUTE_ forms), not NOACCESS or GUARD
static int mem_readable(const MemInfo *mi) { return mi->state == 0x1000 && !(mi->protect & 0x101) && (mi->protect & 0xee); }

// a .. a+n-1 is committed, readable memory in the guest window (Windows says so: VirtualQuery). A small region's answer
// is kept MEM_KEEP_S; a big one's while the helper thread keeps confirming it (0.6.2), so the tick asks about the
// game's heap once, not twice a second.
static int mem_ok(uint64_t a, uint64_t n, double t) {
    if (!n || a < GUEST_LO || a >= GUEST_HI || n > GUEST_HI - a) return 0;
    for (int i = 0; i < MEM_CACHE; i++) {
        MemSpan *e = &g_mem_cache[i];
        if (a >= *(volatile uint64_t *)&e->lo && a + n <= *(volatile uint64_t *)&e->hi && t < *(volatile double *)&e->until) {
            if (e->big && !e->used) e->used = 1;
            return 1;
        }
    }
    MemInfo mi;
    g_nqueries++;
    int64_t q0, q1;
    QueryPerformanceCounter(&q0);
    size_t got = VirtualQuery((const void *)(uintptr_t)a, &mi, sizeof mi);
    QueryPerformanceCounter(&q1);
    double ms = g_qpf ? (double)(q1 - q0) * 1e3 / (double)g_qpf : 0.0;
    g_vq_tick++;
    if (ms > g_vq_most_ms) g_vq_most_ms = ms;
    if (ms > 5.0) g_vq_slow++;
    if (got != sizeof mi || !mem_readable(&mi)) return 0;
    if (a + n > mi.base + mi.size) return 0;
    int big = mi.size >= MEM_BIG;
    mem_lock();
    g_mem_cache[g_mem_next] = (MemSpan){mi.base, mi.base + mi.size, t + (big ? MEM_BIG_KEEP_S : MEM_KEEP_S),
                                        mi.alloc_base, big, 1};
    g_mem_next = (g_mem_next + 1) % MEM_CACHE;
    mem_unlock();
    return 1;
}

// 0.6.2: the helper thread. Every MEM_HELPER_MS it asks Windows again about each big region the tick used since its
// last look (never the game's code or memory: only VirtualQuery), from the start of its allocation if that is one
// readable run reaching as far (then the region widens to it, so reads lower down hit it too), else from the region's
// own start. Unchanged and readable: kept MEM_BIG_KEEP_S more; else dropped at once (the tick asks again itself).
static void *g_helper;
static int g_helper_stop;
static int mem_helper_stop(void) { return __atomic_load_n(&g_helper_stop, __ATOMIC_ACQUIRE); }
static unsigned long __stdcall mem_helper(void *arg) {
    (void)arg;
    while (!mem_helper_stop()) {
        for (int w = 0; w < MEM_HELPER_MS / 100 && !mem_helper_stop(); w++) Sleep(100);
        for (int k = 0; k < MEM_CACHE && !mem_helper_stop(); k++) {
            mem_lock();
            MemSpan e = g_mem_cache[k];
            g_mem_cache[k].used = 0;
            mem_unlock();
            if (!e.big || !e.used || !e.until) continue;  // (not used since: it runs out, the tick asks again if needed)
            int64_t q0, q1;
            QueryPerformanceCounter(&q0);
            MemInfo mi;
            uint64_t lo = 0, hi = 0;
            if (e.alloc && e.alloc < e.lo && VirtualQuery((const void *)(uintptr_t)e.alloc, &mi, sizeof mi) == sizeof mi &&
                mem_readable(&mi) && mi.base <= e.lo && mi.base + mi.size >= e.hi)
                lo = mi.base, hi = mi.base + mi.size;
            else if (VirtualQuery((const void *)(uintptr_t)e.lo, &mi, sizeof mi) == sizeof mi && mem_readable(&mi) &&
                     mi.base == e.lo && mi.base + mi.size >= e.hi)
                lo = mi.base, hi = mi.base + mi.size;
            QueryPerformanceCounter(&q1);
            double ms = g_qpf ? (double)(q1 - q0) * 1e3 / (double)g_qpf : 0.0;
            mem_lock();
            MemSpan *n = &g_mem_cache[k];
            if (n->lo == e.lo && n->hi == e.hi && n->big) {  // (the tick didn't put another region in this slot meanwhile)
                if (hi) {
                    if (lo < e.lo) g_vq_widened++;
                    *(volatile uint64_t *)&n->hi = hi;  // (a read between the two stores sees a part of the new region:
                    *(volatile uint64_t *)&n->lo = lo;  // it contains the old one, and all of it was just confirmed)
                    *(volatile double *)&n->until = now_s() + MEM_BIG_KEEP_S;
                } else {
                    *(volatile double *)&n->until = 0;
                }
            }
            g_vq_helper++;
            if (ms > g_vq_helper_ms) g_vq_helper_ms = ms;
            mem_unlock();
        }
    }
    return 0;
}

static uint64_t self_u64(uint64_t a, double t) {
    return !(a & 7) && mem_ok(a, 8, t) ? *(volatile const uint64_t *)(uintptr_t)a : 0;
}
static uint32_t self_u32(uint64_t a, double t) {
    return !(a & 3) && mem_ok(a, 4, t) ? *(volatile const uint32_t *)(uintptr_t)a : 0;
}
static float self_f32(uint64_t a, double t) {
    uint32_t b = self_u32(a, t);
    float f;
    memcpy(&f, &b, sizeof f);
    return f == f ? f : 0.0f;  // (NaN: 0)
}

// The name at p into out (cap bytes with its 0): 1 to cap-1 printable characters, else 0 and "".
static int self_text(uint64_t p, char *out, size_t cap, double t) {
    out[0] = 0;
    for (size_t i = 0; p && i < cap; i++) {
        uint64_t a = p + i;
        if ((i == 0 || !(a & 0xfff)) && !mem_ok(a, 1, t)) break;  // (checked again on each new page)
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

typedef struct {
    int found;                 // Link's actor (the class check passed)
    int nslots;                // AS holder: slot count (0: no holder of his own)
    int shape[SELF_SLOTS];     // ... subs per slot (-1: no such slot)
    char body[32], lower[32], upper[32], gesture[32];  // what plays: "" nothing, "?" unreadable
    float body_w, lower_w, upper_w;                    // their blend weights
    char anim[32];             // the name to send: the body's; the bow's (upper) while he aims; an attack is kept
    double anim_t;             // ... at least ATTACK_HOLD_S (anim_t: when it last changed)
    int attack;                // anim is an attack (a Cut... name, SwimAttack, or the bow)
    int64_t attacks;           // attacks seen
    int equip_ok;              // the equipment holder passed its checks
    char weapon[64], shield[64], bow[64];  // the items' actor names ("" none, "?" unreadable)
    int weapon_class;          // the weapon is of the Weapon class
    int back[3];               // 0.6.0: weapon, shield, bow on his back (the holder's slot byte; 0 when the slot is
                               // empty)
    uint64_t item[3];          // 0.6.1: the items' actors (0: none), as read with the names
    float rel[3][12];          // ... each one's matrix in the frame of Link's Pod_A (3x4 row by row, m) ...
    float pods[3][3];          // ... its distance to Link's Pod_A, Pod_B, Pod_C (m; -1: not readable)
    float size[3], axis[3];    // 0.6.2: ... how big it is drawn and its matrix's axes (as items.N.size, .axis; -1)
    int has_rel[3];            // ... (0: not read: no item, or no bone)
    int equip_new;             // ... the equipment was just read (self_rel runs once after each read)
    double equip_t;            // when the equipment was read last
    int64_t reads;
    double read_t;             // when it was read last
} SelfState;

// slot/sub of the AS holder h: its name and weight. 0: the holder has no such slot or sub.
static int self_sub(uint64_t h, int slot, int sub, char name[32], float *w, double t) {
    name[0] = 0, *w = 0;
    int32_t n = (int32_t)self_u32(h + AS_SLOTS, t);
    uint64_t slots = self_u64(h + AS_SLOT_ARRAY, t);
    if (slot < 0 || slot >= n || n > 16 || !slots) return 0;
    uint64_t s = slots + (uint64_t)slot * AS_SLOT_SIZE;
    int32_t m = (int32_t)self_u32(s + AS_SLOT_NSUB, t);
    uint64_t subs = self_u64(s + AS_SLOT_SUBS, t);
    if (sub < 0 || sub >= m || m > 16 || !subs) return 0;
    uint64_t e = subs + (uint64_t)sub * AS_SUB_SIZE;
    *w = self_f32(e + AS_SUB_WEIGHT, t);
    if (!self_u64(e + AS_SUB_LIVE, t)) return 1;  // nothing plays there
    uint64_t d = self_u64(e + AS_SUB_DEF, t);
    if (!d || !self_text(self_u64(d + AS_DEF_NAME, t), name, 32, t)) memcpy(name, "?", 2);
    return 1;
}

// Equipment slot i (0 weapon, 1 shield, 2 bow) of actor a: the item's actor name, "" when the slot is empty or the
// item is gone or being deleted, "?" when its name can't be read.
static uint64_t self_item(uint64_t a, int i, char out[64], int *weapon_class, double t) {
    out[0] = 0;
    if (weapon_class) *weapon_class = 0;
    uint64_t l = a + ACTOR_EQUIP + EQUIP_LINKS + (uint64_t)i * EQUIP_LINK_SIZE;
    uint64_t data = self_u64(l, t);
    uint32_t id = self_u32(l + 8, t);
    if (!data || id == 0xffffffffu || self_u32(data + LINKDATA_ID, t) != id) return 0;
    uint64_t w = self_u64(data + LINKDATA_PROC, t);
    if (!w || !mem_ok(w + PROC_STATE, 1, t) || *(volatile const uint8_t *)(uintptr_t)(w + PROC_STATE) == PROC_DELETING)
        return 0;
    if (!self_text(self_u64(w + PROC_NAME, t), out, 64, t)) memcpy(out, "?", 2);
    else if (weapon_class) *weapon_class = self_u64(w, t) == WEAPON_VPTR;
    return w;
}

// Read your own Link's state into s (pi: the player info block). Each frame while a client asks; the equipment every
// SELF_EQUIP_S.
static void self_read(SelfState *s, uint64_t pi, double t) {
    s->reads++, s->read_t = t;
    uint64_t a = pi ? self_u64(pi + PI_PLAYER, t) : 0;
    s->found = a && self_u64(a, t) == PLAYER_VPTR;
    uint64_t h = s->found ? self_u64(a + ACTOR_AS, t) : 0;
    int own = h && self_u64(h + AS_OWNER, t) == a;
    if (!own) h = 0;  // (only his own holder)
    s->nslots = own ? (int)(int32_t)self_u32(h + AS_SLOTS, t) : 0;
    if (s->nslots < 0 || s->nslots > 16) s->nslots = 0;
    uint64_t slots = s->nslots ? self_u64(h + AS_SLOT_ARRAY, t) : 0;
    if (slots && !mem_ok(slots, (uint64_t)s->nslots * AS_SLOT_SIZE, t)) slots = 0;
    for (int k = 0; k < SELF_SLOTS; k++)
        s->shape[k] = slots && k < s->nslots ? (int)(int32_t)self_u32(slots + (uint64_t)k * AS_SLOT_SIZE + AS_SLOT_NSUB, t)
                                             : -1;
    float gw;
    self_sub(h, 0, 0, s->body, &s->body_w, t);  // (h 0: all "")
    self_sub(h, 0, 1, s->lower, &s->lower_w, t);
    self_sub(h, 1, 1, s->upper, &s->upper_w, t);
    self_sub(h, 2, 0, s->gesture, &gw, t);
    int bow = s->upper_w > SELF_WEIGHT_MIN && (!strcmp(s->upper, "BowCharge") || !strcmp(s->upper, "BowShoot"));
    const char *now = !anim_is_attack(s->body) && bow ? s->upper : s->body;
    int attack = anim_is_attack(now) || bow;
    if (strcmp(now, s->anim) && !(s->attack && t - s->anim_t >= 0 && t - s->anim_t < ATTACK_HOLD_S)) {
        if (attack) s->attacks++;
        memcpy(s->anim, now, strlen(now) + 1);
        s->anim_t = t, s->attack = attack;
    }
    if (!(t - s->equip_t < SELF_EQUIP_S && t >= s->equip_t)) {
        s->equip_t = t;
        s->equip_ok = s->found && self_u64(a + ACTOR_EQUIP, t) == EQUIP_VPTR &&
                      self_u64(a + ACTOR_EQUIP + EQUIP_OWNER, t) == a;
        if (s->equip_ok) {
            s->item[0] = self_item(a, 0, s->weapon, &s->weapon_class, t);
            s->item[1] = self_item(a, 1, s->shield, NULL, t);
            s->item[2] = self_item(a, 2, s->bow, NULL, t);
            const char *names[3] = {s->weapon, s->shield, s->bow};
            for (int i = 0; i < 3; i++) {  // (the slot's byte: not 0 = on the back; out/research/ghost-items-plan.md)
                uint64_t b = a + ACTOR_EQUIP + EQUIP_LINKS + (uint64_t)i * EQUIP_LINK_SIZE + EQUIP_BACK;
                s->back[i] = *names[i] && mem_ok(b, 1, t) && *(volatile const uint8_t *)(uintptr_t)b != 0;
            }
        } else {
            s->weapon[0] = s->shield[0] = s->bow[0] = 0, s->weapon_class = 0;
            s->back[0] = s->back[1] = s->back[2] = 0;
            s->item[0] = s->item[1] = s->item[2] = 0;
        }
        s->equip_new = 1;  // (self_rel reads the items' places in the tick)
    }
}

// ------------------------------------------------------------------ guest-visible state (only the tick touches it)

typedef struct {
    uint64_t unit;  // the request's unit (its +8 points back at the handle while it is ours)
    uint8_t failed;
    uint8_t pad[7];
} Handle;
typedef struct {
    uint64_t data;  // the link's data; the proc is at data+0x48
    uint32_t id;
    uint8_t acquired;
    uint8_t pad[3];
} Link;
typedef struct {
    uint64_t vptr, zero;
    uint8_t buf[0xc4];  // s16 count, s16 used, entries
    uint8_t pad[0xc];
} Pack;
typedef struct {  // the game's string object (vtable, then the char*)
    uint64_t vptr;
    uint64_t str;
} StrObj;

static _Alignas(16) Handle g_handle;
static _Alignas(16) Link g_link = {0, 0xffffffffu, 0, {0}};
static _Alignas(16) Pack g_pack;
static _Alignas(16) StrObj g_key_m;
static _Alignas(16) float g_mtx[12];
static _Alignas(16) float g_pos[3];
static _Alignas(16) float g_rot[9];
static _Alignas(16) float g_move_mtx[12];
static _Alignas(16) uint64_t g_fake_action[2];  // AS_PLAY's "action": it reads only +8, the actor

// AS names asked for with `ghost anim <name>` or `ghost pos ... <anim>`: the game gets the pointer, so they stay in
// this table for good (Wait, Walk and Run: the static names, so a name is always the same pointer). Link's own list
// has 399 names, so the other player's all fit (a name is only asked for while the actor isn't known to lack it,
// anim_remote_pick). The last ANIM_NAMES_OWN slots are only for `ghost anim` (own = 1), so names from the network
// can never lock it out.
#define MAX_ANIM_NAMES 512
#define ANIM_NAMES_OWN 32
static char g_anim_names[MAX_ANIM_NAMES][32];
static int g_nanim_names;
static const char *intern_anim(const char *name, int own) {  // (tick thread only)
    for (int i = AN_WAIT; i <= AN_RUN; i++)
        if (!strcmp(AN_NAMES[i], name)) return AN_NAMES[i];
    for (int i = 0; i < g_nanim_names; i++)
        if (!strcmp(g_anim_names[i], name)) return g_anim_names[i];
    if (g_nanim_names >= MAX_ANIM_NAMES - (own ? 0 : ANIM_NAMES_OWN)) return NULL;
    size_t len = strlen(name);
    if (len > 31) len = 31;
    memcpy(g_anim_names[g_nanim_names], name, len);
    g_anim_names[g_nanim_names][len] = 0;
    return g_anim_names[g_nanim_names++];
}

// Actor names: the request keeps the pointer, so every name used stays in this table for good.
#define MAX_NAMES 32
static char g_names[MAX_NAMES][64];
static int g_nnames;
static const char *intern(const char *name) {  // (tick thread only)
    for (int i = 0; i < g_nnames; i++)
        if (!strcmp(g_names[i], name)) return g_names[i];
    if (g_nnames == MAX_NAMES) return NULL;
    memcpy(g_names[g_nnames], name, strlen(name) + 1);
    return g_names[g_nnames++];
}

// Held items (0.6.0): each slot (0 weapon, 1 shield, 2 bow) has its own request handle, link, pack and matrix, and two
// bone follows of ours for the back (used in turn: an item being deleted may still run the one it had). The game gets
// pointers to all of these, so they are statics. The item names stay in their own table for good (as g_names).
#define ITEM_SLOTS 3
typedef struct {
    _Alignas(16) uint8_t b[FOLLOW_SIZE];
} Follow;
static _Alignas(16) Handle g_item_handle[ITEM_SLOTS];
static _Alignas(16) Link g_item_link[ITEM_SLOTS];
static _Alignas(16) Pack g_item_pack[ITEM_SLOTS];
static _Alignas(16) float g_item_mtx[ITEM_SLOTS][12];
static _Alignas(16) Follow g_follow[ITEM_SLOTS][2];
static _Alignas(16) float g_euler_rot[4], g_euler_trans[4];
static uint64_t g_size_item[ITEM_SLOTS];  // 0.6.2: the ghost's grabbed items: the WEAPON_CALC wrapper gives them the
                                          // player's scale (written by the tick, read on any game thread)
static int64_t g_scale_sets;               // ... how often it did
static void size_item_set(int i, uint64_t item) { __atomic_store_n(&g_size_item[i], item, __ATOMIC_RELEASE); }
static _Alignas(16) Link g_trash[TRASH_MAX];  // deleted items, watched until they are gone (`stray`)
static _Alignas(16) Handle g_give_handle;     // `ghost give`: its own request, never the ghost's or an item's
static _Alignas(16) Pack g_give_pack;
static _Alignas(16) float g_give_mtx[12];
static const char BONE_R_NAME[] = "Weapon_R", BONE_L_NAME[] = "Weapon_L", BONE_POD_NAME[] = "Pod_A";
enum { BONE_R, BONE_L, BONE_POD, NBONES };
static const char *const BONE_NAMES[NBONES] = {BONE_R_NAME, BONE_L_NAME, BONE_POD_NAME};
static char g_item_names[MAX_ITEM_NAMES][64];
static int g_nitem_names;
static const char *intern_item(const char *name) {  // (tick thread only; NULL: the table is full)
    for (int i = 0; i < g_nitem_names; i++)
        if (!strcmp(g_item_names[i], name)) return g_item_names[i];
    if (g_nitem_names == MAX_ITEM_NAMES) return NULL;
    size_t len = strlen(name);
    if (len > 63) len = 63;
    memcpy(g_item_names[g_nitem_names], name, len);
    g_item_names[g_nitem_names][len] = 0;
    return g_item_names[g_nitem_names++];
}

// 0.6.1: the only names `ghost items` spawns (the names come from the other player's network data): slot 0 a one-handed
// sword, two-hander or spear, slot 1 a shield, slot 2 a bow, by the game's own name prefixes, and none of the special
// ones that were never tried: Weapon_Sword_070 and the four the game's data gives the same sort key (11000-11002: 071,
// 072, 080, 081), Weapon_Sword_500 (sort key 0: not an inventory item) and Weapon_Bow_071. tools/mp/client.py keeps
// the same rule (ITEM_PREFIXES, ITEM_BLOCKED).
static const char *const ITEM_PREFIX[] = {"Weapon_Sword_", "Weapon_Lsword_", "Weapon_Spear_", "Weapon_Shield_",
                                          "Weapon_Bow_"};
static const int ITEM_PREFIX_SLOT[] = {0, 0, 0, 1, 2};
static const char *const ITEM_BLOCKED[] = {"Weapon_Sword_070", "Weapon_Sword_071", "Weapon_Sword_072",
                                           "Weapon_Sword_080", "Weapon_Sword_081", "Weapon_Sword_500",
                                           "Weapon_Bow_071"};
static int item_name_ok(int slot, const char *name) {
    for (size_t k = 0; k < sizeof ITEM_BLOCKED / sizeof *ITEM_BLOCKED; k++)
        if (!strcmp(name, ITEM_BLOCKED[k])) return 0;
    for (size_t k = 0; k < sizeof ITEM_PREFIX / sizeof *ITEM_PREFIX; k++) {
        size_t n = strlen(ITEM_PREFIX[k]);
        if (ITEM_PREFIX_SLOT[k] == slot && !strncmp(name, ITEM_PREFIX[k], n) && name[n]) return 1;
    }
    return 0;
}

// ------------------------------------------------------------------ requests (commands -> tick) and status

enum { MOVE_NONE, MOVE_POS, MOVE_FOLLOW };
enum { ST_IDLE, ST_PENDING, ST_LIVE, ST_FAR };  // ST_FAR: wanted, but its place is too far from Link (far_next)
static const char *const STATE_NAMES[] = {"idle", "pending", "live", "far"};

typedef struct {
    int seq;               // bumped by `ghost spawn` and `ghost despawn`: the tick applies only the latest one
    int want;              // ... which asked for: 1 a ghost (spawn), 0 none (despawn)
    char name[64];         // spawn: the actor
    int is_default;        // spawn: no actor named (the default, with the crate as fallback)
    float off[3];          // spawn: offset from Link
    int move;              // MOVE_*
    float target[4];       // MOVE_POS: x y z yaw; MOVE_FOLLOW: dx dy dz
    int pos_seq;           // bumped by every `ghost pos` ...
    double pos_t;          // ... which arrived at this time
    float pos_speed;       // ... with this speed (m/s; -1: none given)
    char pos_anim[32];     // ... and this animation name of the other player ("": none given)
    double self_until;     // `ghost self`: read your own Link each frame until then (now_s)
    int anim;              // ANIM_*: `ghost anim`
    char anim_name[32];    // ANIM_NAME: the AS
    int anim_seq;          // bumped by every `ghost anim`
    float far_m, near_m;   // `ghost far`: the distance rule (far_m 0: off)
    int items_on;          // `ghost items` (0.6.0): held items asked for (0: never, or `ghost items off`) ...
    char item_names[3][64];  // ... weapon, shield, bow ("" none)
    int items_mask;        // ... which of them are on the back (bit 0 weapon, 1 shield, 2 bow)
    int items_back;        // `ghost items back on|off`: back placement (default on)
    float item_rel[3][12]; // 0.6.2 `ghost items rel slot m...`: the other player's own item on his back, in his Pod_A's
    int item_has_rel[3];   // frame (3x4 row by row; their `ghost self` `<item>_rel`): used as that slot's back offset
    int give_seq;          // bumped by every `ghost give` ...
    char give_name[64];    // ... the actor
    float give_off[3];     // ... its offset from Link
    int give_has_off;      // ... (0: 1.5 m in front of him)
} Requests;

enum { ANIM_AUTO, ANIM_OFF, ANIM_NAME };

enum { IS_NONE, IS_WAIT, IS_PENDING, IS_GRAB, IS_HELD, IS_FAILED };  // an item slot (IS_FAILED: gave up on the name)
static const char *const IS_NAMES[] = {"none", "wait", "pending", "grab", "held", "failed"};
enum { GV_NONE, GV_PENDING, GV_DONE, GV_FAILED };  // `ghost give`
static const char *const GV_NAMES[] = {"none", "pending", "done", "failed"};

typedef struct {
    char name[64];       // asked for ("" none)
    int state;           // IS_*
    uint64_t actor;      // the item actor (0: none yet)
    char place[8];       // where its bone follow puts it: "hand", "back", "none", "other" ("" not held)
    float dist;          // m from the item to that bone (-1: not known)
    float hold;          // m: length of its own "on the back" offset (-1: not read)
    int tries;           // requests for this name
    int item_state;      // its grab state byte (-1: not read)
    int holder_ok;       // its holder link names the ghost
    char why[64];        // why the last try failed or the item was dropped
    int refusals;        // 0.6.1: grab calls refused before the held item's grab was taken (0 until held)
    float rel[12];       // 0.6.1: the held item's matrix in Pod_A's frame (3x4 row by row, m), for the comparison
    int has_rel;         // ... with Link's own items (`ghost self`); 0: not read
    float size;          // 0.6.2: how big it is drawn (1: its normal size, as Link's): its matrix's axes times its own
                         // scale, the mean of the three (-1: not read)
    float size_first;    // ... the same when it was first held, before any fix (-1: not read)
    float axis;          // ... the mean length of its matrix's axes: the scale the bone it follows passes on
    float own_scale;     // ... its own scale (ACTOR_SCALE), the mean of the three
    float model_scale;   // ... its model's scale (the model holder's copy of it; -1: not read)
    int copied;          // 0.6.2: on the back with the other player's own offset (`ghost items rel`)
} ItemStatus;

typedef struct {
    int on, back, mask;            // `ghost items`: asked; back placement on; the on-back mask
    char bones[48];                // the ghost's bones: "ok", "ok, no Pod_A", "missing ...", "looking", "no model"
    ItemStatus slot[3];
    int stray;                     // deleted items still there TRASH_STRAY_S after the delete
    int dying;                     // 0.6.1: deleted items still there (any age; watched with plain reads)
    int64_t requests, held, deleted, lost;  // item requests; items settled in the hand; deleted; lost while held
    int64_t taken;                 // 0.6.1: items someone else (Link) held when the mod went to delete them: forgotten
    int64_t kept;                  // ... items not deleted because our follow couldn't be taken off (FLAG_OWNS_FOLLOW)
    int64_t parks;                 // ... moves of a new item back to its place over the ghost before the grab took it
    double us, us_avg, us_max;     // the items' part of a tick in microseconds: the median, the average and the most
                                   // over the last TIME_KEEP ticks it worked (wall clock: a thread switch counts)
    double us_mean;                // ... 0.6.1: the plain mean over those ticks (us_avg is a running average)
    double calls, queries;         // ... game calls and memory queries a tick (average)
    double tick_us, tick_us_avg, tick_us_max;  // the whole tick (ghost and items): median, average, most over the
                                               // last TIME_KEEP ticks
    int64_t slow_parts;            // 0.6.1: items parts over SLOW_PART_MS ...
    char slow_part[80];            // ... the latest: wall ms, the thread's own cycles, game calls
    int64_t slow_calls;            // game calls over SLOW_CALL_MS (all of the mod's) ...
    char slow_call[48];            // ... the latest one's name and ms
    double call_most_ms;           // ... the longest game call so far
    double frame_ms, frame_ms_max; // frames (tick to tick): median and most over the last TIME_KEEP
    int64_t frames, frames_slow;   // frames counted; frames over SLOW_FRAME_MS
    int give_state;                // GV_*
    char give_name[64];
    uint64_t give_actor;
    int64_t scale_sets;            // 0.6.2: the player's scale given to the ghost's items (WEAPON_CALC wrapper)
    float ghost_scale;             // ... the ghost's own scale (ACTOR_SCALE, mean; -1: not read)
    int64_t vq_tick, vq_slow, vq_helper, vq_widened;  // 0.6.2: VirtualQuery on the tick (all of the mod's), of those
    double vq_most_ms, vq_helper_ms;  // over 5 ms, the longest; the helper thread's calls, longest, regions widened
    int mem_big;                   // ... big regions known now (kept by the helper)
    int64_t leftovers;             // 0.6.2: a ghost or items a stage change left behind, removed after it
    int old_ghosts, old_items;     // ... watched now (the ghost and items a stage change dropped)
} ItemsStatus;

#define MAX_THREADS 8
typedef struct {
    int64_t ticks, overlaps;
    double rate, first_t, last_t;
    unsigned long tid[MAX_THREADS];
    int64_t tid_ticks[MAX_THREADS];
    int ntid;
    int64_t checks, check_ok, check_bad;  // guest_call check, once a second
    uint64_t check_got, check_raw, player_info, player;
    // the ghost
    int state, wanted;
    char name[64];
    uint64_t actor;
    float actor_pos[3], actor_yaw;  // read back from the actor's matrix
    int has_actor_pos;
    int64_t requests, spawned, moves, respawns, drops;  // respawns: tries in the current row
    double pending_s, live_s;
    char last[160];        // the last thing that happened
    int move;
    float target[4];
    int64_t targets, snaps;  // smoothing: `ghost pos` targets taken, jumps (> SNAP_DIST)
    double interval;         // ... smoothed time between targets (s)
    float behind;            // ... distance from the shown pose to the latest target (m)
    int physics;             // the actor has physics (its rigid bodies are moved too)
    float pulled_back;       // how far the game moved it since our last move (m; about 0 when it follows)
    // animation
    int anim;                // ANIM_*
    char anim_name[32];      // ANIM_NAME: the AS named in `ghost anim`
    char anim_want[32];      // the AS it asks for now ("" none)
    char as[32];             // the AS the actor plays (read back; "" none)
    uint64_t as_holder;      // the actor's AS holder
    int as_ok;               // ... which is its own (it has animations)
    float speed;             // smoothed ground speed (m/s)
    int speed_given;         // ... from the speed `ghost pos` gave (else worked out from the positions)
    int64_t as_calls, as_reasserts, as_changes;  // play calls; of those, asked again (it played something else);
                                                 // changes of the auto choice
    char remote[32];         // the other player's animation name (`ghost pos ... anim`; "" none, or too old)
    int64_t remote_calls;    // play calls for one of those names (or its stand-in)
    int64_t remote_misses;   // names the actor turned out not to have
    char missing[256];       // ... the latest of them (comma-separated)
    SelfState self;          // `ghost self`: your own Link
    // distance and back-off
    float dist;              // horizontal distance from Link to where the ghost should be (m; -1: can't tell)
    float far_m, near_m;     // the distance rule in use
    int64_t fars;            // times it went far
    int quick;               // times in a row the game removed it sooner than STABLE_S after it spawned
    double retry_at;         // idle and wanted: the next try is due at this time (now_s)
    int no_player;           // 0.6.1: live, but no player actor this frame and the same spawn heap: the ghost and its
    int64_t no_player_ticks; // items are left alone (no call) until he is back or the heap changes; frames of that
    ItemsStatus items;       // 0.6.0: held items
} Status;

static Requests g_req = {.far_m = FAR_M, .near_m = NEAR_M, .pos_speed = -1.0f, .items_mask = 4,
                         .items_back = 1};  // under g_lock
static Status g_status;  // under g_lock (the tick's copy is t_status)

// ------------------------------------------------------------------ the tick (a game worker thread, once per frame)

static int g_in_tick;
static Status t_status;
static double t_win_start, t_next_check, t_pending_since, t_respawn_at, t_live_since;
static int64_t t_win_ticks;
static int t_seq_seen, t_pos_seq;
static char t_want_name[64];
static int t_default, t_default_fails;  // the default actor was asked for; its failed tries in a row
static int t_default_seen;              // the default actor spawned once in this run: its name works
static float t_want_off[3];
static uint64_t t_stage_heap, t_stage_player;
static Smooth t_smooth;
static float t_last_moved[3];  // where the last move put it ...
static int t_moved;            // ... (0: not moved since it went live)
static int t_anim_seq;
static const char *t_anim_forced;  // ANIM_NAME: the interned AS name (NULL: too many names)
static uint64_t t_anim_actor;      // the actor the animation state below is for (0: none)
static AnimAuto t_anim_auto;
static float t_given_speed = -1.0f;  // the speed the latest `ghost pos` gave (-1: none) ...
static char t_given_anim[32];        // ... and the animation name ("": none) ...
static double t_given_t;             // ... which came at this time
static Missing t_missing;            // names the current actor turned out not to have
static const char *t_anim_asked;   // the AS last asked for on that actor (NULL: none yet)
static int t_anim_remote;          // ... for the other player's name (anim_remote_pick), not the speed rule
static double t_anim_call_t, t_anim_read_t, t_anim_switch_t;  // last call; next read-back; last change of name
static int t_anim_seen;            // it played what was last asked for at least once ...
static int t_anim_misses;          // ... else: how often it was asked again

// Say what happened (in `ghost` and, when it is news, in savagewind.log).
__attribute__((format(printf, 1, 2))) static void note(const char *fmt, ...) {
    char text[sizeof t_status.last];
    __builtin_va_list ap;
    __builtin_va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    __builtin_va_end(ap);
    if (strcmp(text, t_status.last)) api->log("ghost: %s", text);
    memcpy(t_status.last, text, sizeof text);
}

static void note_thread(void) {
    unsigned long id = GetCurrentThreadId();
    for (int i = 0; i < t_status.ntid; i++)
        if (t_status.tid[i] == id) {
            t_status.tid_ticks[i]++;
            return;
        }
    if (t_status.ntid < MAX_THREADS) {
        t_status.tid[t_status.ntid] = id;
        t_status.tid_ticks[t_status.ntid++] = 1;
    }
}

static void zero_link(void) {
    memset(&g_link, 0, sizeof g_link);
    g_link.id = 0xffffffffu;
}

// An attempt ended without a ghost. retry: while it is still wanted, request it again `delay` seconds later (at most
// MAX_RESPAWNS times in a row); otherwise stop wanting it.
static void end_attempt(const char *why, double t, int retry, double delay) {
    t_status.state = ST_IDLE;
    t_status.actor = 0;
    t_status.has_actor_pos = 0;
    smooth_forget(&t_smooth);
    t_respawn_at = t + delay;
    if (!t_status.wanted) {
        note("%s", why);
    } else if (!retry) {
        t_status.wanted = 0;
        note("%s; giving up", why);
    } else if (t_status.respawns >= MAX_RESPAWNS) {
        t_status.wanted = 0;
        note("%s; gave up after %d tries", why, MAX_RESPAWNS);
    } else {
        t_status.respawns++;
        if (delay > RESPAWN_DELAY) note("%s; trying again in %.0f s", why, delay);
        else note("%s; trying again", why);
    }
}

// The ghost (or its pending request) is gone without us removing it: a stage change, or the game deleted it.
// If the crate stood in for the default, the new stage gets the default again (a slow load can cause the fallback).
// live_s: how long it was live (-1: it was still pending). Gone sooner than STABLE_S after it spawned: the next try
// waits twice as long each time in a row (back-off), so a ghost the game keeps removing (too far from Link, if the
// distance rule missed it) is not spawned again every 2 s.
static void lose(const char *why, double t, double live_s) {
    t_status.drops++;
    if (t_default && strcmp(t_want_name, DEFAULT_ACTOR)) {
        memcpy(t_want_name, DEFAULT_ACTOR, sizeof DEFAULT_ACTOR);
        t_default_fails = 0;
    }
    double delay = RESPAWN_DELAY;
    char text[sizeof t_status.last];
    if (live_s >= 0 && live_s < STABLE_S) {
        delay = backoff_delay(++t_status.quick);
        snprintf(text, sizeof text, "%s %.1f s after it spawned", why, live_s);
        why = text;
    }
    end_attempt(why, t, 1, delay);
}

// A request didn't work. A refusal can mean an unknown name, but also a stage that is loading (no free unit, the
// process manager not taking requests), and creations fail for a few seconds during a load (seen in the test after
// a reload): from here these look the same, so it is tried again (retry), up to MAX_RESPAWNS in a row.
// The fallback is for a default that never works: the default actor (no name given) that has not spawned yet in this
// run and fails FALLBACK_AFTER times in a row, or makes something that is not an actor, is replaced by the crate
// until the ghost is lost (lose) or the next `ghost spawn`. Once the default has spawned, its failures are a loading
// stage and never switch to the crate.
static void request_failed(const char *what, double t, int retry) {
    char why[sizeof t_status.last];
    int n = snprintf(why, sizeof why, "%s: %s", t_status.name, what);
    if (t_default && !t_default_seen && strcmp(t_want_name, FALLBACK_ACTOR) &&
        (!retry || ++t_default_fails >= FALLBACK_AFTER)) {
        memcpy(t_want_name, FALLBACK_ACTOR, sizeof FALLBACK_ACTOR);
        if (n > 0 && (size_t)n < sizeof why) snprintf(why + n, sizeof why - n, "; using %s instead", FALLBACK_ACTOR);
        retry = 1;
    }
    end_attempt(why, t, retry, RESPAWN_DELAY);
}

static uint64_t spawn_heap(void) {
    uint64_t holder = rd64(HEAP_HOLDER_PTR);
    return holder ? rd64(holder + HEAP_OFF) : 0;
}

// Link's position and facing (yaw in degrees from the actor matrix's z axis). 0 before there is a world: no player,
// or the title screen's, whose position still reads 0 0 0 (Savage Wind's `pos` uses the same rule). An actor spawned
// on the title screen crashed the game in the test, so nothing is spawned until this answers.
static int player_pose(uint64_t pi, uint64_t player, float pos[3], float *yaw) {
    if (!pi || !player) return 0;
    pos[0] = rdf(pi + PI_POS), pos[1] = rdf(pi + PI_POS + 4), pos[2] = rdf(pi + PI_POS + 8);
    if (pos[0] == 0 && pos[1] == 0 && pos[2] == 0) return 0;
    float zx = rdf(player + ACTOR_MTX + 0x08), zz = rdf(player + ACTOR_MTX + 0x28);
    *yaw = (zx || zz) ? __builtin_atan2f(zx, zz) * 57.29577951308232f : 0.0f;
    return 1;
}

static void request_spawn(struct Cpu *c, uint64_t pi, uint64_t player, double t) {
    uint64_t creator = rd64(CREATOR_PTR), heap = spawn_heap();
    float pos[3], yaw;
    if (!creator || !heap || !player_pose(pi, player, pos, &yaw)) {
        note("no world yet (title screen, or creator, heap or player missing); waiting");
        t_respawn_at = t + RESPAWN_DELAY;
        return;
    }
    if (rd8(creator + CREATOR_BLOCKED)) {
        note("the actor creator refuses requests now (busy); trying again");
        t_respawn_at = t + RESPAWN_DELAY;
        return;
    }
    const char *name = intern(t_want_name);
    if (!name) {
        note("too many different actor names used; restart the game");
        t_status.wanted = 0;
        return;
    }
    float r[9];
    yaw_rot(t_status.move == MOVE_POS ? t_status.target[3] : yaw, r);
    float x = pos[0] + t_want_off[0], y = pos[1] + t_want_off[1], z = pos[2] + t_want_off[2];
    if (t_status.move == MOVE_POS) x = t_status.target[0], y = t_status.target[1], z = t_status.target[2];
    float m[12] = {r[0], r[1], r[2], x, r[3], r[4], r[5], y, r[6], r[7], r[8], z};
    memcpy(g_mtx, m, sizeof m);

    GCALL1(c, HANDLE_CLEAR, &g_handle);  // (a failed request leaves its flag set: always start from zero)
    memset(&g_pack, 0, sizeof g_pack);
    g_pack.vptr = PACK_VPTR;
    GCALL1(c, PACK_CLEAR, g_pack.buf);
    g_key_m.vptr = STRING_OBJ_VPTR, g_key_m.str = KEY_M;
    gcall(c, PACK_ADD, P(g_pack.buf), P(g_mtx), P(&g_key_m), 0x30, 7, 0, 0);
    t_status.requests++;
    uint64_t ok = gcall(c, REQUEST_CREATE, creator, P(name), heap, P(&g_handle), P(&g_pack), 0, 1) & 1;
    memcpy(t_status.name, name, strlen(name) + 1);
    if (!ok) {
        GCALL1(c, HANDLE_CLEAR, &g_handle);
        request_failed("request refused (unknown name, or the game is busy)", t, 1);
        return;
    }
    t_status.state = ST_PENDING;
    t_pending_since = t;
    t_stage_heap = heap, t_stage_player = player;
    note("requested %s", name);
}

static void tick_pending(struct Cpu *c, double t) {
    uint64_t unit = g_handle.unit;
    if (unit && rd64(unit + 8) != P(&g_handle)) {  // the unit went to someone else: don't touch it
        GCALL1(c, HANDLE_CLEAR, &g_handle);
        lose("pending request lost (stage change?)", t, -1);
        return;
    }
    t_status.pending_s = t - t_pending_since;
    if (GCALL1(c, HANDLE_READY, &g_handle) & 1) {
        uint64_t proc = GCALL1(c, HANDLE_PEEK, &g_handle);
        uint64_t info = proc ? gcall(c, ACTOR_TYPEINFO, 0, 0, 0, 0, 0, 0, 0) : 0;
        uint64_t is_actor = proc && info ? GCALL2(c, rd64(rd64(proc) + 0x10), proc, info) & 1 : 0;
        if (!is_actor) {
            GCALL1(c, HANDLE_RELEASE, &g_handle);
            request_failed("the created thing is not an actor; released", t, 0);  // (the same every time)
            return;
        }
        GCALL3(c, SET_MTX, proc, g_mtx, 0);
        GCALL1(c, HANDLE_TAKE, &g_handle);
        GCALL3(c, LINK_SET, &g_link, proc, 0);
        t_status.actor = proc;
        t_status.state = ST_LIVE;
        t_status.spawned++;
        t_status.live_s = 0;
        t_live_since = t;  // (the row of tries ends only once it has stayed STABLE_S: tick_live)
        t_default_fails = 0;
        if (!strcmp(t_status.name, DEFAULT_ACTOR)) t_default_seen = 1;
        float pose[4] = {g_mtx[3], g_mtx[7], g_mtx[11], __builtin_atan2f(g_mtx[2], g_mtx[10]) * 57.29577951308232f};
        smooth_place(&t_smooth, pose, t);  // (moves start from where it was spawned)
        t_moved = 0;
        t_status.physics = rd64(proc + ACTOR_PHYSICS) != 0;
        t_status.pulled_back = 0;
        note("%s spawned after %.2f s", t_status.name, t - t_pending_since);
        return;
    }
    if (GCALL1(c, HANDLE_FAILED, &g_handle) & 1) {
        GCALL1(c, HANDLE_RELEASE_FAILED, &g_handle);
        GCALL1(c, HANDLE_CLEAR, &g_handle);
        request_failed("creation failed", t, 1);
        return;
    }
    if (t - t_pending_since > PENDING_TIMEOUT) {
        GCALL1(c, HANDLE_RELEASE, &g_handle);
        GCALL1(c, HANDLE_CLEAR, &g_handle);
        request_failed("not ready in time; released", t, 1);
    }
}

// ---- held items (0.6.0, opt-in; out/research/ghost-items-plan.md)
//
// While the ghost is live and a `ghost items` asked for names, each slot (0 weapon, 1 shield, 2 bow) spawns that item
// next to the ghost's hand (the same async request as the ghost, with its own handle), checks it is of the Weapon
// class, and calls the item's own grab with the ghost as its holder (weapon: hand bone Weapon_R; shield and bow:
// Weapon_L). The grab is refused until the item's AI ran once, so it is asked each tick for ITEM_GRAB_S. Once the item
// settled (its state 0xff, its holder link naming the ghost) the game keeps it on the bone by itself and its pick-up
// is off (a held item has no pick-up action, and a second grab is refused). A new name, or none: the old item is
// deleted (never let go first: a let-go item falls and can be picked up). An item that ends up loose is deleted at once.
// On the back (mask, `ghost items back`): a bone follow of ours on Pod_A with the item's own "on the back" offsets.
// Cleanup: items are deleted before the ghost (despawn, the far rule, a new spawn) and when the game removed the
// ghost; on a stage change they are forgotten without a call, as the ghost is. Every pointer is checked before use.

// Tick times: the last TIME_KEEP, for a median (one slow tick, a thread switch, doesn't count) and the most.
#define TIME_KEEP 64
typedef struct {
    double v[TIME_KEEP];
    int n, next;
} Times;
static void times_add(Times *tm, double us) {
    tm->v[tm->next] = us;
    tm->next = (tm->next + 1) % TIME_KEEP;
    if (tm->n < TIME_KEEP) tm->n++;
}
static double times_median(const Times *tm, double *most) {
    double s[TIME_KEEP];
    int n = tm->n;
    memcpy(s, tm->v, sizeof s);
    for (int i = 1; i < n; i++)  // (insertion sort: 64 numbers)
        for (int j = i; j > 0 && s[j - 1] > s[j]; j--) {
            double x = s[j];
            s[j] = s[j - 1], s[j - 1] = x;
        }
    if (most) *most = n ? s[n - 1] : 0;
    return n ? s[n / 2] : 0;
}

typedef struct {
    const char *name;   // the interned name this slot shows or asks for (NULL: none)
    int state;          // IS_*
    double since;       // when the state began
    double retry_at;    // IS_WAIT: the next request
    int grabbed;        // IS_GRAB: the grab call said yes ...
    double grab_t;      // ... at this time
    double nofollow_t;  // IS_HELD: no bone follow since this time (0: it has one)
    double dist_t;      // the next distance read
    int back_asked;     // it was asked onto our follow last tick ...
    double back_t;      // ... and the game refused it at this time (asked again a second later; 0: not refused)
    int tries;          // requests for this name
    int next_follow;    // which of the slot's two follows is set up next
    int has_hold;       // its "on the back" offsets were read ...
    float rot[3], trans[3];  // ... (degrees; m)
    int grab_refusals;  // 0.6.1: grab calls refused for the item now in IS_GRAB (its AI hadn't run yet)
    int has_rel;        // 0.6.2: a copied back offset (`ghost items rel`) is used instead of the item's own ...
    float rel[12];      // ... this one (3x4 row by row, m)
    int rel_new;        // ... it changed while the item was on our follow: set up again
} ItemSlot;

static ItemSlot t_items[ITEM_SLOTS];
static uint64_t t_items_ghost;            // the ghost the bones below are for (0: none)
static int t_bone_unit[NBONES], t_bone_idx[NBONES];  // -1: not found
static int t_bones_done;                  // the bone lookup is over for this ghost (found, or gave up)
static double t_bones_next, t_bones_until;
static uint8_t t_follow_built[ITEM_SLOTS][2];
static uint8_t t_item_orphan[ITEM_SLOTS]; // a request still pending at a stage change: released (if still ours) by the
                                          // next items part, before the slot's handle is used again
static double t_trash_t[TRASH_MAX];       // when each watched item was deleted (0: free)
static int t_trash_stage[TRASH_MAX];      // 0.6.2: ... dropped by a stage change (forgotten, not deleted: leftovers_tick)
#define OLD_MAX 4                         // 0.6.2: ghosts a stage change dropped, watched (leftovers_tick)
static _Alignas(16) Link g_old_ghost[OLD_MAX];
static double t_old_ghost_t[OLD_MAX];     // ... when (0: free)
static double t_stage_back_t;             // ... Link there (on the current heap) since then (0: not now) ...
static uint64_t t_stage_back_heap;        // ... and that heap
static double t_items_us_avg, t_items_calls, t_items_queries;
static Times t_items_times, t_tick_times, t_frame_times;
static double t_last_tick_t;              // the previous tick's time (frame times)
static int64_t t_slow_seen;               // g_slow_calls when the last one was noted
static double t_slow_note_t;              // ... and when (at most one note a second)
static int t_give_seq, t_give_state;
static double t_give_since;
static double t_now;                      // this tick's time (for remove_ghost)

// ---- the ghost's bones (plain reads, every pointer checked: out/research/ghost-items-manual-bone.md)

// Unit u of actor a's model: its skeleton (0: no model yet, or a check failed). *nunits: the model's unit count.
static uint64_t model_skel(uint64_t a, int u, int *nunits, double t) {
    if (nunits) *nunits = 0;
    uint64_t m = self_u64(a + ACTOR_MODEL, t);
    if (!m || self_u64(m, t) != MODEL_VPTR) return 0;
    int32_t n = (int32_t)self_u32(m + MODEL_NUNITS, t);
    if (n < 0 || n > 64) return 0;
    if (nunits) *nunits = n;
    if (u < 0 || u >= n) return 0;
    uint64_t arr = self_u64(m + MODEL_UNITS, t);
    uint64_t slot = arr ? self_u64(arr + (uint64_t)u * 8, t) : 0;
    uint64_t unit = slot ? self_u64(slot, t) : 0;
    uint64_t vp = unit ? self_u64(unit, t) : 0;
    if (vp != UNIT_VPTR_A && vp != UNIT_VPTR_B) return 0;
    uint64_t sk = self_u64(unit + UNIT_SKEL, t);
    uint64_t res = sk ? self_u64(sk, t) : 0;
    if (!res || self_u32(res, t) != FSKL_MAGIC) return 0;
    return sk;
}

static int skel_nbones(uint64_t sk, double t) {
    uint64_t res = self_u64(sk, t);
    int n = res ? (int)(self_u32(res + RES_NBONES, t) & 0xffff) : 0;
    return n <= 1024 ? n : 0;
}

// Bone `name` in skeleton sk: its index, or -1.
static int skel_find(uint64_t sk, const char *name, double t) {
    uint64_t res = self_u64(sk, t), dict = res ? self_u64(res + RES_DICT, t) : 0;
    int n = skel_nbones(sk, t);
    char got[32];
    for (int b = 0; dict && b < n; b++) {
        uint64_t key = self_u64(dict + 0x20 + (uint64_t)b * 0x10, t);
        if (key && self_text(key + 2, got, sizeof got, t) && !strcmp(got, name)) return b;
    }
    return -1;
}

// The world position of bone b of unit u of actor a (0: not readable).
static int bone_pos(uint64_t a, int u, int b, float out[3], double t) {
    uint64_t sk = model_skel(a, u, NULL, t);
    if (!sk || b < 0 || b >= skel_nbones(sk, t)) return 0;
    uint64_t w = self_u64(sk + SKEL_WORLD, t);
    if (!w || !mem_ok(w + (uint64_t)b * 0x40, 0x40, t)) return 0;
    for (int k = 0; k < 3; k++) {
        out[k] = rdf(w + (uint64_t)b * 0x40 + 0x30 + 4 * (uint64_t)k);
        if (!(out[k] == out[k]) || out[k] > 1e6f || out[k] < -1e6f) return 0;
    }
    return 1;
}

static int finite_f(float f, float lim) { return f == f && f <= lim && f >= -lim; }

// 0.6.1: the world matrix of bone b of unit u of actor a, as 3x4 rows (the skeleton keeps 4x4 columns). 0: not readable.
static int bone_mtx(uint64_t a, int u, int b, float out[12], double t) {
    uint64_t sk = model_skel(a, u, NULL, t);
    if (!sk || b < 0 || b >= skel_nbones(sk, t)) return 0;
    uint64_t w = self_u64(sk + SKEL_WORLD, t);
    if (!w || !mem_ok(w + (uint64_t)b * 0x40, 0x40, t)) return 0;
    w += (uint64_t)b * 0x40;
    for (int r = 0; r < 3; r++)
        for (int k = 0; k < 4; k++) {
            out[r * 4 + k] = rdf(w + 16 * (uint64_t)k + 4 * (uint64_t)r);
            if (!finite_f(out[r * 4 + k], 1e6f)) return 0;
        }
    return 1;
}

// Actor `item`'s matrix in the frame of bone matrix bm (both 3x4 rows): bm^-1 x item (the bone's axes may carry a
// scale; each is divided out). 0: not readable.
static int rel_to_bone(const float bm[12], uint64_t item, float out[12], double t) {
    if (!mem_ok(item + ACTOR_MTX, 0x30, t)) return 0;
    float im[12];
    for (int k = 0; k < 12; k++) {
        im[k] = rdf(item + ACTOR_MTX + 4 * (uint64_t)k);
        if (!finite_f(im[k], 1e6f)) return 0;
    }
    for (int i = 0; i < 3; i++) {  // row i of the result: bone axis i (column i of bm) dotted with item's columns
        float s2 = bm[0 * 4 + i] * bm[0 * 4 + i] + bm[1 * 4 + i] * bm[1 * 4 + i] + bm[2 * 4 + i] * bm[2 * 4 + i];
        if (!(s2 > 1e-6f)) return 0;
        for (int j = 0; j < 4; j++) {
            float v = 0;
            for (int k = 0; k < 3; k++) v += bm[k * 4 + i] * (j < 3 ? im[k * 4 + j] : im[k * 4 + 3] - bm[k * 4 + 3]);
            out[i * 4 + j] = v / s2;
        }
    }
    return 1;
}

// 0.6.2: how big actor a is drawn, per axis: each axis of its matrix (a bone it follows passes the bone's scale on)
// and its own scale (ACTOR_SCALE); drawn = axis x scale. 0: not readable or not sane.
static int actor_size(uint64_t a, float axis[3], float scale[3], double t) {
    if (!mem_ok(a + ACTOR_MTX, 0x30, t) || !mem_ok(a + ACTOR_SCALE, 12, t)) return 0;
    for (int k = 0; k < 3; k++) {  // (the matrix is 3 rows of 4: axis k is column k)
        float x = rdf(a + ACTOR_MTX + 4 * (uint64_t)k), y = rdf(a + ACTOR_MTX + 0x10 + 4 * (uint64_t)k),
              z = rdf(a + ACTOR_MTX + 0x20 + 4 * (uint64_t)k);
        if (!finite_f(x, 100.0f) || !finite_f(y, 100.0f) || !finite_f(z, 100.0f)) return 0;
        axis[k] = __builtin_sqrtf(x * x + y * y + z * z);
        scale[k] = rdf(a + ACTOR_SCALE + 4 * (uint64_t)k);
        if (!(axis[k] > 0.01f && axis[k] < 100.0f) || !(scale[k] > 0.01f && scale[k] < 100.0f)) return 0;
    }
    return 1;
}

// 0.6.2: the scale actor a's model holder has (MODEL_SCALE, the mean of the three; -1: not readable).
static float model_scale(uint64_t a, double t) {
    uint64_t m = self_u64(a + ACTOR_MODEL, t);
    if (!m || self_u64(m, t) != MODEL_VPTR || !mem_ok(m + MODEL_SCALE, 12, t)) return -1.0f;
    float s = 0;
    for (int k = 0; k < 3; k++) {
        float v = rdf(m + MODEL_SCALE + 4 * (uint64_t)k);
        if (!finite_f(v, 100.0f)) return -1.0f;
        s += v;
    }
    return s / 3.0f;
}

// Bone `name` of actor a: its unit and index (-1: not found), looked through the first 8 units.
static int actor_bone(uint64_t a, const char *name, int *unit, double t) {
    int nunits = 0;
    model_skel(a, 0, &nunits, t);
    for (int u = 0; u < nunits && u < 8; u++) {
        uint64_t sk = model_skel(a, u, NULL, t);
        int b = sk ? skel_find(sk, name, t) : -1;
        if (b >= 0) {
            *unit = u;
            return b;
        }
    }
    return -1;
}

// `ghost self` (0.6.1): where Link's own items are on him: each one's matrix in his Pod_A's frame and its distance
// to Pod_A, Pod_B and Pod_C, for the comparison with the ghost's (tools/mp/ghost_items_check.py H4). Once after each
// equipment read (SELF_EQUIP_S), only while a client asks; plain reads. The bones are looked up once per Link actor.
static uint64_t t_self_bones_for;
static int t_self_pod_unit[3], t_self_pod_idx[3];
static double t_self_bones_next;
static void self_rel(SelfState *s, uint64_t pi, double t) {
    if (!s->equip_new) return;
    s->equip_new = 0;
    uint64_t a = s->found && pi ? self_u64(pi + PI_PLAYER, t) : 0;
    static const char *const PODS[3] = {"Pod_A", "Pod_B", "Pod_C"};
    if (a && (a != t_self_bones_for || (t_self_pod_idx[0] < 0 && t >= t_self_bones_next))) {
        t_self_bones_for = a, t_self_bones_next = t + 2.0;  // (not found: looked up again at most every 2 s)
        for (int k = 0; k < 3; k++) t_self_pod_idx[k] = actor_bone(a, PODS[k], &t_self_pod_unit[k], t);
    }
    float pm[3][12];
    int has_pm[3];
    for (int k = 0; k < 3; k++) has_pm[k] = a && bone_mtx(a, t_self_pod_unit[k], t_self_pod_idx[k], pm[k], t);
    for (int i = 0; i < 3; i++) {
        uint64_t it = s->item[i];
        s->has_rel[i] = it && has_pm[0] && rel_to_bone(pm[0], it, s->rel[i], t);
        float ip[3] = {it ? self_f32(it + ACTOR_MTX + 0x0c, t) : 0, it ? self_f32(it + ACTOR_MTX + 0x1c, t) : 0,
                       it ? self_f32(it + ACTOR_MTX + 0x2c, t) : 0};
        for (int k = 0; k < 3; k++) {
            float bp[3] = {pm[k][3], pm[k][7], pm[k][11]};
            s->pods[i][k] = it && has_pm[k] ? dist3(ip, bp) : -1.0f;
        }
        float ax[3], sc[3];  // (0.6.2: how big Link's own item is drawn: what the ghost's should match)
        int ok = it && actor_size(it, ax, sc, t);
        s->size[i] = ok ? (ax[0] * sc[0] + ax[1] * sc[1] + ax[2] * sc[2]) / 3.0f : -1.0f;
        s->axis[i] = ok ? (ax[0] + ax[1] + ax[2]) / 3.0f : -1.0f;
    }
}

// Look the ghost's Weapon_R, Weapon_L and Pod_A up (once per ghost; its model may come a little after it spawned).
static void items_bones(uint64_t ghost, double t) {
    ItemsStatus *st = &t_status.items;
    if (t_bones_done || t < t_bones_next) return;
    t_bones_next = t + ITEM_BONES_S;
    int nunits = 0, skels = 0;
    model_skel(ghost, 0, &nunits, t);
    for (int k = 0; k < NBONES; k++) t_bone_unit[k] = t_bone_idx[k] = -1;
    for (int u = 0; u < nunits && u < 8; u++) {
        uint64_t sk = model_skel(ghost, u, NULL, t);
        if (!sk) continue;
        skels++;
        for (int k = 0; k < NBONES; k++)
            if (t_bone_idx[k] < 0 && (t_bone_idx[k] = skel_find(sk, BONE_NAMES[k], t)) >= 0) t_bone_unit[k] = u;
    }
    int hands = t_bone_idx[BONE_R] >= 0 && t_bone_idx[BONE_L] >= 0;
    if (hands) {
        t_bones_done = 1;
        snprintf(st->bones, sizeof st->bones, t_bone_idx[BONE_POD] >= 0 ? "ok" : "ok, no %s", BONE_POD_NAME);
        note("items: the ghost has the hand bones%s", t_bone_idx[BONE_POD] >= 0 ? " and Pod_A" : " (no Pod_A: no back)");
    } else if (t >= t_bones_until) {
        t_bones_done = 1;
        if (!skels) {
            snprintf(st->bones, sizeof st->bones, "no model");
        } else {
            snprintf(st->bones, sizeof st->bones, "missing%s%s", t_bone_idx[BONE_R] < 0 ? " Weapon_R" : "",
                     t_bone_idx[BONE_L] < 0 ? " Weapon_L" : "");
        }
        note("items: the ghost has no hand bones (%s: not the Link body?); no items", st->bones);
    } else {
        snprintf(st->bones, sizeof st->bones, "looking");
    }
}

static int items_hands(void) { return t_bones_done && t_bone_idx[BONE_R] >= 0 && t_bone_idx[BONE_L] >= 0; }

// ---- an item's own fields (plain reads)

// The actor a game link stored at `at` (data*, u32 id) names: 0 when it names none (no data, the ids differ: that
// actor is gone).
static uint64_t link_at(uint64_t at, double t) {
    uint64_t data = self_u64(at, t);
    uint32_t id = self_u32(at + 8, t);
    if (!data || id == 0xffffffffu || self_u32(data + LINKDATA_ID, t) != id) return 0;
    return self_u64(data + LINKDATA_PROC, t);
}

// Its holder link names `holder`.
static int item_holder_is(uint64_t item, uint64_t holder, double t) {
    return holder && link_at(item + ITEM_HOLDER, t) == holder;
}

// 0.6.1: someone other than the ghost holds the item, or a grab asked for someone else (Link took it in the moment
// before the ghost's grab): that actor, else 0. Such an item is never deleted (it is Link's now).
static uint64_t item_other_holder(uint64_t item, uint64_t ghost, double t) {
    uint64_t a = link_at(item + ITEM_HOLDER, t), b = link_at(item + ITEM_HOLD_ASK, t);
    return a && a != ghost ? a : b && b != ghost ? b : 0;
}

static int item_state(uint64_t item, double t) {
    return mem_ok(item + ITEM_STATE, 1, t) ? *(volatile const uint8_t *)(uintptr_t)(item + ITEM_STATE) : -1;
}

// Its own "on the back" offsets (the player's: trans in m, rot in degrees), by its kind (out/research/
// ghost-items-plan.md): the parameter object and the two values' places in it. 0: not readable.
static int item_hold(uint64_t item, float rot[3], float trans[3], double t) {
    static const struct {
        int obj, trans, rot;
    } K[5] = {{12, 0x78, 0xa0}, {14, 0x78, 0xa0}, {15, 0x78, 0xa0}, {17, 0x2c0, 0x2e8}, {16, 0x50, 0x78}};
    uint32_t kind = self_u32(item + ITEM_KIND, t);
    if (kind > 4) return 0;
    uint64_t pl = self_u64(item + ACTOR_PARAMS, t), gp = pl ? self_u64(pl + PARAMS_LIST, t) : 0;
    if (!gp) return 0;
    int32_t n = (int32_t)self_u32(gp + PLIST_COUNT, t);
    if (n <= K[kind].obj || n > 256) return 0;
    uint64_t tab = self_u64(gp + PLIST_OBJS, t), obj = tab ? self_u64(tab + (uint64_t)K[kind].obj * 8, t) : 0;
    if (!obj || !mem_ok(obj + (uint64_t)K[kind].trans, 12, t) || !mem_ok(obj + (uint64_t)K[kind].rot, 12, t)) return 0;
    for (int k = 0; k < 3; k++) {
        trans[k] = rdf(obj + (uint64_t)K[kind].trans + 4 * (uint64_t)k);
        rot[k] = rdf(obj + (uint64_t)K[kind].rot + 4 * (uint64_t)k);
        if (!finite_f(trans[k], 5.0f) || !finite_f(rot[k], 720.0f)) return 0;
    }
    return 1;
}

// A link still names a live actor: the same test as the game's LINK_VALID (the id matches the link data's; the actor
// is there and, for a link that doesn't hold it, not being deleted), as plain reads, every address checked. So a held
// item costs no game call a frame.
static int link_ok(const Link *l, double t) {
    if (!l->data || l->id == 0xffffffffu || self_u32(l->data + LINKDATA_ID, t) != l->id) return 0;
    uint64_t proc = self_u64(l->data + LINKDATA_PROC, t);
    if (l->acquired) return proc != 0;
    return proc && mem_ok(proc + PROC_STATE, 1, t) && rd8(proc + PROC_STATE) != PROC_DELETING;
}

// ---- deleted items: watched until they are gone

static void trash_add(const Link *l, double t, int stage) {
    for (int k = 0; k < TRASH_MAX; k++)
        if (!t_trash_t[k]) {
            g_trash[k] = *l;
            t_trash_t[k] = t;
            t_trash_stage[k] = stage;
            return;
        }
}

static void trash_tick(double t) {
    int stray = 0, dying = 0;
    for (int k = 0; k < TRASH_MAX; k++) {
        if (!t_trash_t[k]) continue;
        if (!link_ok(&g_trash[k], t) || t - t_trash_t[k] > (t_trash_stage[k] ? LEFTOVER_WATCH_S : TRASH_FORGET_S)) {
            memset(&g_trash[k], 0, sizeof g_trash[k]);
            t_trash_t[k] = 0, t_trash_stage[k] = 0;
        } else {
            dying++;
            stray += t - t_trash_t[k] > TRASH_STRAY_S;
        }
    }
    t_status.items.stray = stray, t_status.items.dying = dying;
}

// ---- one slot

static void item_zero_link(int i) {
    memset(&g_item_link[i], 0, sizeof g_item_link[i]);
    g_item_link[i].id = 0xffffffffu;
}

static int follow_is_ours(int i, uint64_t follow) {
    return follow && (follow == P(&g_follow[i][0]) || follow == P(&g_follow[i][1]));
}

// 0.6.1: before an item is deleted (or forgotten), it gets its own bone follow back, so no item ever runs ours (our
// memory) after we let it go. 0: ours is still on it (the game refused: FLAG_OWNS_FOLLOW), so it must not be deleted.
static int item_follow_own(struct Cpu *c, int i, uint64_t item, double t) {
    if (!follow_is_ours(i, self_u64(item + ACTOR_FOLLOW, t))) return 1;
    gcall(c, FOLLOW_SET, item, item + ITEM_FOLLOW, 0, 0, 0, 0, 0);
    return !follow_is_ours(i, self_u64(item + ACTOR_FOLLOW, t));
}

// Remove what slot i has: a pending request is released, an item deleted (never let go first). The slot is empty
// after this (its name stays). 0.6.1: an item someone else holds (Link took it before the ghost's grab) is not
// deleted, only forgotten; an item gets its own follow back first.
static void item_remove(struct Cpu *c, int i, double t) {
    ItemSlot *s = &t_items[i];
    size_item_set(i, 0);  // (0.6.2: before anything else: the wrapper never touches an item we let go)
    if (s->state == IS_PENDING) {
        GCALL1(c, HANDLE_RELEASE, &g_item_handle[i]);  // (an actor created but not taken is deleted)
        GCALL1(c, HANDLE_CLEAR, &g_item_handle[i]);
    } else if (s->state == IS_GRAB || s->state == IS_HELD) {
        if (GCALL1(c, LINK_VALID, &g_item_link[i]) & 1) {
            uint64_t proc = rd64(g_item_link[i].data + LINKDATA_PROC);
            uint64_t other = proc ? item_other_holder(proc, t_items_ghost, t) : 0;
            if (other) {
                uint64_t pi = self_u64(PLAYER_INFO_PTR, t), player = pi ? self_u64(pi + PI_PLAYER, t) : 0;
                t_status.items.taken++;
                note("items: %s was taken by %s before the ghost held it: not deleted, forgotten",
                     s->name ? s->name : "?", other == player ? "Link" : "another actor");
            } else if (proc && !item_follow_own(c, i, proc, t)) {
                t_status.items.kept++;
                note("items: %s keeps the mod's bone follow (the game refused to change it): not deleted",
                     s->name ? s->name : "?");
            } else if (proc) {
                trash_add(&g_item_link[i], t, 0);
                gcall(c, DELETE_LATER, proc, 0, 0, 0, 0, 0, 0);
                t_status.items.deleted++;
            }
        }
        GCALL1(c, LINK_RESET, &g_item_link[i]);
        item_zero_link(i);
    }
    s->state = IS_NONE;
    s->grabbed = 0, s->nofollow_t = 0, s->has_hold = 0;
    t_status.items.slot[i].actor = 0;
}

// The try failed (why): the next one after `delay`, unless the name had its ITEM_TRIES.
static void item_fail(int i, const char *why, double delay, double t) {
    ItemSlot *s = &t_items[i];
    ItemStatus *o = &t_status.items.slot[i];
    snprintf(o->why, sizeof o->why, "%s", why);
    if (s->tries >= ITEM_TRIES) {
        s->state = IS_FAILED;
        note("items: %s: %s; gave up after %d tries", s->name ? s->name : "?", why, ITEM_TRIES);
    } else {
        s->state = IS_WAIT;
        s->retry_at = t + delay;
        note("items: %s: %s; trying again in %.0f s", s->name ? s->name : "?", why, delay);
    }
}

// Slot i's parking place: ITEM_PARK_UP over the ghost's feet, turned as the ghost (into g_item_mtx[i]). A new item is
// spawned there and kept there until the ghost's grab took it (0.6.1): before that it is a loose world item, and Link
// can't reach it up there. 0: the ghost's matrix isn't readable.
static int item_park_mtx(int i, uint64_t ghost, double t) {
    if (!mem_ok(ghost + ACTOR_MTX, 0x30, t)) return 0;
    float m[12];
    for (int k = 0; k < 12; k++) {
        m[k] = rdf(ghost + ACTOR_MTX + 4 * (uint64_t)k);
        if (!finite_f(m[k], 1e6f)) return 0;
    }
    m[7] += ITEM_PARK_UP;
    memcpy(g_item_mtx[i], m, sizeof m);
    return 1;
}

// Move a new item (not taken by the grab yet) back to its parking place: its matrix and, if it has physics, its rigid
// bodies (or it falls).
static void item_park(struct Cpu *c, int i, uint64_t item, uint64_t ghost, double t) {
    if (!item_park_mtx(i, ghost, t)) return;
    GCALL3(c, SET_MTX, item, g_item_mtx[i], 0);
    if (self_u64(item + ACTOR_PHYSICS, t)) GCALL2(c, PHYS_SET_MTX, item, g_item_mtx[i]);
    t_status.items.parks++;
}

// Ask for slot i's item at its parking place over the ghost. 0: not now (no world, the creator busy: asked again soon).
static int item_request(struct Cpu *c, int i, uint64_t ghost, double t) {
    ItemSlot *s = &t_items[i];
    uint64_t creator = rd64(CREATOR_PTR), heap = spawn_heap();
    if (!creator || !heap || rd8(creator + CREATOR_BLOCKED) || !item_park_mtx(i, ghost, t)) return 0;
    GCALL1(c, HANDLE_CLEAR, &g_item_handle[i]);
    memset(&g_item_pack[i], 0, sizeof g_item_pack[i]);
    g_item_pack[i].vptr = PACK_VPTR;
    GCALL1(c, PACK_CLEAR, g_item_pack[i].buf);
    g_key_m.vptr = STRING_OBJ_VPTR, g_key_m.str = KEY_M;
    gcall(c, PACK_ADD, P(g_item_pack[i].buf), P(g_item_mtx[i]), P(&g_key_m), 0x30, 7, 0, 0);
    s->tries++;
    t_status.items.requests++;
    uint64_t ok = gcall(c, REQUEST_CREATE, creator, P(s->name), heap, P(&g_item_handle[i]), P(&g_item_pack[i]), 0, 1) & 1;
    if (!ok) {
        GCALL1(c, HANDLE_CLEAR, &g_item_handle[i]);
        item_fail(i, "request refused (unknown name, or the game is busy)", ITEM_RETRY_S, t);
        return 1;
    }
    s->state = IS_PENDING;
    s->since = t;
    return 1;
}

// The item's own grab, with the ghost as its holder (slot i: 0 the weapon in the right hand, 1 and 2 the left).
static int item_grab(struct Cpu *c, int i, uint64_t item, uint64_t ghost, double t) {
    uint64_t vt = self_u64(item, t), fn = vt == WEAPON_VPTR ? self_u64(vt + ITEM_GRAB_VF, t) : 0;
    if (vt != WEAPON_VPTR || (fn && fn != WEAPON_GRAB)) return 0;  // (not readable: the address from the research)
    const char *hand = i == 0 ? BONE_R_NAME : BONE_L_NAME;
    return gcall(c, WEAPON_GRAB, item, (uint64_t)i, ghost, P(hand), P(BONE_POD_NAME), 0, 0) & 1;
}

static void item_pending(struct Cpu *c, int i, uint64_t ghost, double t) {
    ItemSlot *s = &t_items[i];
    uint64_t unit = g_item_handle[i].unit;
    if (unit && rd64(unit + 8) != P(&g_item_handle[i])) {  // (the unit went to someone else: don't touch it)
        GCALL1(c, HANDLE_CLEAR, &g_item_handle[i]);
        item_fail(i, "request lost (stage change?)", ITEM_RETRY_S, t);
        return;
    }
    if (GCALL1(c, HANDLE_READY, &g_item_handle[i]) & 1) {
        uint64_t proc = GCALL1(c, HANDLE_PEEK, &g_item_handle[i]);
        if (!proc || self_u64(proc, t) != WEAPON_VPTR) {
            GCALL1(c, HANDLE_RELEASE, &g_item_handle[i]);
            GCALL1(c, HANDLE_CLEAR, &g_item_handle[i]);
            s->state = IS_FAILED;  // (the same every time: no retry)
            snprintf(t_status.items.slot[i].why, sizeof t_status.items.slot[i].why, "not a weapon; released");
            note("items: %s is not of the Weapon class; released", s->name);
            return;
        }
        item_park_mtx(i, ghost, t);  // (where the ghost is now; else where it was at the request)
        GCALL3(c, SET_MTX, proc, g_item_mtx[i], 0);
        GCALL1(c, HANDLE_TAKE, &g_item_handle[i]);
        GCALL3(c, LINK_SET, &g_item_link[i], proc, 0);
        s->state = IS_GRAB;
        s->since = t;
        s->grab_refusals = 0;
        s->grabbed = item_grab(c, i, proc, ghost, t);
        if (s->grabbed) s->grab_t = t, size_item_set(i, proc);
        else s->grab_refusals++;
        return;
    }
    if (GCALL1(c, HANDLE_FAILED, &g_item_handle[i]) & 1) {
        GCALL1(c, HANDLE_RELEASE_FAILED, &g_item_handle[i]);
        GCALL1(c, HANDLE_CLEAR, &g_item_handle[i]);
        item_fail(i, "creation failed", ITEM_RETRY_S, t);
        return;
    }
    if (t - s->since > ITEM_PENDING_S) {
        GCALL1(c, HANDLE_RELEASE, &g_item_handle[i]);
        GCALL1(c, HANDLE_CLEAR, &g_item_handle[i]);
        item_fail(i, "not ready in time; released", ITEM_RETRY_S, t);
    }
}

// Set up one of slot i's follows for the back of `ghost`, with the item's own offsets: (returns its address). Only
// one that no item runs now is written (the other of the two may still be in use by the item before).
static uint64_t follow_setup(struct Cpu *c, int i, uint64_t ghost) {
    ItemSlot *s = &t_items[i];
    int k = s->next_follow;
    s->next_follow ^= 1;
    Follow *f = &g_follow[i][k];
    if (!t_follow_built[i][k]) {
        GCALL1(c, FOLLOW_CTOR, f);
        t_follow_built[i][k] = 1;
    }
    GCALL3(c, LINK_SET, f->b + FOLLOW_TARGET, ghost, 0);
    uint64_t bone = P(BONE_POD_NAME);
    uint32_t again = 0xffffffffu, mode = 0;
    memcpy(f->b + FOLLOW_BONE, &bone, 8);
    memcpy(f->b + FOLLOW_INDEX, &again, 4);
    memcpy(f->b + FOLLOW_MODE, &mode, 4);
    s->rel_new = 0;
    if (s->has_rel) {  // (0.6.2: the other player's own: exactly where his Link carries it, armor and all)
        memcpy(f->b + FOLLOW_OFFSET, s->rel, sizeof s->rel);
        return P(f);
    }
    for (int j = 0; j < 3; j++) g_euler_rot[j] = s->rot[j] * 0.0174533f, g_euler_trans[j] = s->trans[j];
    gcall(c, MTX_FROM_EULER, P(f->b + FOLLOW_OFFSET), P(g_euler_rot), P(g_euler_trans), 0, 0, 0, 0);
    return P(f);
}

// Where a held item goes: its own follow (the hand), or one of ours (the back). ACTOR_FOLLOW is only changed between
// the item's own follow and ours, never to 0 and never away from anything else. Returns the place.
static const char *item_place(struct Cpu *c, int i, uint64_t item, uint64_t ghost, int want_back, double t) {
    ItemSlot *s = &t_items[i];
    uint64_t cur = self_u64(item + ACTOR_FOLLOW, t), own = item + ITEM_FOLLOW;
    int ours = follow_is_ours(i, cur);
    if (!cur) return "none";
    if (want_back && cur == own) {
        if (s->back_asked) {  // (asked last time and it is still its own: the game refused it; again in a second)
            s->back_asked = 0, s->back_t = t;
            return "hand";
        }
        if (s->back_t && t - s->back_t < 1.0 && t >= s->back_t) return "hand";
        // (0.6.1: never ours on an actor that deletes its follow when it goes: the game would free our memory)
        if (!mem_ok(item + ACTOR_FLAGS, 4, t) || (self_u32(item + ACTOR_FLAGS, t) & FLAG_OWNS_FOLLOW)) return "hand";
        gcall(c, FOLLOW_SET, item, follow_setup(c, i, ghost), 0, 0, 0, 0, 0);
        s->back_asked = 1;
        return "hand";  // (until the next tick reads it back)
    }
    s->back_asked = 0;
    if (ours) s->back_t = 0;
    if (want_back && ours && s->rel_new) {  // (0.6.2: a new copied offset: the other of our two follows, then switch)
        gcall(c, FOLLOW_SET, item, follow_setup(c, i, ghost), 0, 0, 0, 0, 0);
        return "back";
    }
    if (!want_back && ours) {
        gcall(c, FOLLOW_SET, item, own, 0, 0, 0, 0, 0);
        return "back";
    }
    return cur == own ? "hand" : ours ? "back" : "other";
}

// 0.6.2: how big a held item is drawn (plain reads; the status only). The fourteenth round showed the ghost's items
// 1.4-1.5 times Link's: the weapon's own per-frame update gives a weapon held by anyone but the player a bigger scale.
// weapon_calc_wrapper gives the ghost's the player's scale instead (g_size_item), right after the game's update.
static void item_size_read(int i, uint64_t item, double t) {
    ItemStatus *o = &t_status.items.slot[i];
    float ax[3], sc[3];
    if (!actor_size(item, ax, sc, t)) {
        o->size = o->axis = o->own_scale = -1.0f;
        return;
    }
    o->size = (ax[0] * sc[0] + ax[1] * sc[1] + ax[2] * sc[2]) / 3.0f;
    o->axis = (ax[0] + ax[1] + ax[2]) / 3.0f;
    o->own_scale = (sc[0] + sc[1] + sc[2]) / 3.0f;
    if (o->size_first <= 0) o->size_first = o->size;  // (0 or -1: a fresh slot)
}

// 0.6.2: the player's scale for weapon w, as the game's own update gives it in Link's hands: its base scale times its
// scale object's WEAPON_SCALE_PLAYER (the same reads the game's update just made). On a game thread: plain reads,
// each pointer in the guest window.
static int guest_ptr(uint64_t a) { return a >= GUEST_LO && a < GUEST_HI && !(a & 7); }
static void weapon_player_scale(uint64_t w) {
    uint64_t pl = rd64(w + ACTOR_PARAMS);
    uint64_t gp = guest_ptr(pl) ? rd64(pl + PARAMS_LIST) : 0;
    if (!guest_ptr(gp)) return;
    int32_t n = *(volatile const int32_t *)(uintptr_t)(gp + PLIST_COUNT);
    uint64_t tab = rd64(gp + PLIST_OBJS);
    if (!guest_ptr(tab)) return;
    uint64_t obj = rd64(tab + (n > WEAPON_SCALE_OBJ ? WEAPON_SCALE_OBJ * 8u : 0u));
    if (!guest_ptr(obj)) return;
    float k = rdf(obj + WEAPON_SCALE_PLAYER), out[3];
    if (!(k > 0.01f && k < 100.0f)) return;
    for (int j = 0; j < 3; j++) {
        float b = rdf(w + WEAPON_BASE_SCALE + 4 * (uint64_t)j);
        if (!(b > 0.01f && b < 100.0f)) return;
        out[j] = b * k;
    }
    memcpy((void *)(uintptr_t)(w + ACTOR_SCALE), out, sizeof out);
    __atomic_fetch_add(&g_scale_sets, 1, __ATOMIC_RELAXED);
}

// 0.6.2: wraps WEAPON_CALC (a virtual call: every weapon, every frame): the game's update first, then, for one of the
// ghost's items only, the player's scale. Everything else is untouched.
static SwModGuestFn g_orig_weapon_calc;
static void weapon_calc_wrapper(struct Cpu *c) {
    uint64_t self = SWMOD_REGS(c)->x[0];
    g_orig_weapon_calc(c);
    if (!self) return;
    for (int i = 0; i < ITEM_SLOTS; i++)
        if (__atomic_load_n(&g_size_item[i], __ATOMIC_ACQUIRE) == self) {
            weapon_player_scale(self);
            return;
        }
}

static void item_held(struct Cpu *c, int i, uint64_t ghost, int want_back, double t) {
    ItemSlot *s = &t_items[i];
    ItemStatus *o = &t_status.items.slot[i];
    if (!link_ok(&g_item_link[i], t)) {
        size_item_set(i, 0);
        GCALL1(c, LINK_RESET, &g_item_link[i]);
        item_zero_link(i);
        s->state = IS_NONE;
        t_status.items.lost++;
        item_fail(i, "the item went away", ITEM_LOST_RETRY_S, t);
        return;
    }
    uint64_t item = rd64(g_item_link[i].data + LINKDATA_PROC);
    o->actor = item;
    o->item_state = item_state(item, t);
    o->holder_ok = item_holder_is(item, ghost, t);
    const char *why = o->item_state == 1 ? "it was let go" : o->item_state == 2 ? "it was dropped"
                      : !o->holder_ok ? "its holder changed" : o->item_state != 0xff ? "its state changed" : NULL;
    uint64_t cur = self_u64(item + ACTOR_FOLLOW, t);
    if (!why && !cur) {
        if (!s->nofollow_t) s->nofollow_t = t;
        if (t - s->nofollow_t >= ITEM_NOFOLLOW_S) why = "it has no bone follow";
    } else {
        s->nofollow_t = 0;
    }
    if (why) {  // (never leave a loose item: delete it, ask again)
        item_remove(c, i, t);
        t_status.items.lost++;
        item_fail(i, why, ITEM_LOST_RETRY_S, t);
        return;
    }
    const float *tr = s->has_rel ? (const float[3]){s->rel[3], s->rel[7], s->rel[11]} : s->trans;
    o->hold = s->has_hold || s->has_rel ? __builtin_sqrtf(tr[0] * tr[0] + tr[1] * tr[1] + tr[2] * tr[2]) : -1.0f;
    o->copied = s->has_rel;
    const char *place = item_place(c, i, item, ghost, want_back && (s->has_hold || s->has_rel), t);
    snprintf(o->place, sizeof o->place, "%s", place);
    size_item_set(i, item);  // (0.6.2: the player's scale: weapon_calc_wrapper)
    item_size_read(i, item, t);
    if (t >= s->dist_t) {
        s->dist_t = t + ITEM_STATUS_S;
        o->model_scale = model_scale(item, t);
        int b = !strcmp(place, "back") ? BONE_POD : i == 0 ? BONE_R : BONE_L;
        float bp[3], ip[3] = {self_f32(item + ACTOR_MTX + 0x0c, t), self_f32(item + ACTOR_MTX + 0x1c, t),
                              self_f32(item + ACTOR_MTX + 0x2c, t)};
        o->dist = bone_pos(ghost, t_bone_unit[b], t_bone_idx[b], bp, t) ? dist3(ip, bp) : -1.0f;
        float pm[12];  // (0.6.1: its matrix in Pod_A's frame, to compare with Link's own: `ghost self`)
        o->has_rel = t_bone_idx[BONE_POD] >= 0 && bone_mtx(ghost, t_bone_unit[BONE_POD], t_bone_idx[BONE_POD], pm, t) &&
                     rel_to_bone(pm, item, o->rel, t);
    }
}

static void item_grab_tick(struct Cpu *c, int i, uint64_t ghost, double t) {
    ItemSlot *s = &t_items[i];
    ItemStatus *o = &t_status.items.slot[i];
    if (!link_ok(&g_item_link[i], t)) {
        size_item_set(i, 0);
        GCALL1(c, LINK_RESET, &g_item_link[i]);
        item_zero_link(i);
        s->state = IS_NONE;
        item_fail(i, "the item went away before it was held", ITEM_RETRY_S, t);
        return;
    }
    uint64_t item = rd64(g_item_link[i].data + LINKDATA_PROC);
    o->actor = item;
    o->item_state = item_state(item, t);
    o->holder_ok = item_holder_is(item, ghost, t);
    if (!s->grabbed) {  // (a loose world item until the grab takes it: kept out of Link's reach over the ghost)
        uint64_t other = item_other_holder(item, ghost, t);
        if (other || t - s->since > ITEM_GRAB_S) {
            item_remove(c, i, t);  // (one someone else took is only forgotten)
            item_fail(i, other ? "someone else took it before the ghost's grab" : "the grab was refused",
                      ITEM_RETRY_S, t);
            return;
        }
        item_park(c, i, item, ghost, t);
        if ((s->grabbed = item_grab(c, i, item, ghost, t))) s->grab_t = t, size_item_set(i, item);
        else s->grab_refusals++;
        return;
    }
    if (o->item_state == 0xff && o->holder_ok) {
        note("items: the ghost holds %s (%.2f s after it was taken; the grab was refused %d times first)", s->name,
             t - s->since, s->grab_refusals);
        o->refusals = s->grab_refusals;
        s->state = IS_HELD;
        s->since = t;
        s->dist_t = 0, s->back_t = 0, s->back_asked = 0, s->nofollow_t = 0;
        s->has_hold = item_hold(item, s->rot, s->trans, t);  // (its "on the back" offsets, read once)
        t_status.items.held++;
    } else if (t - s->grab_t > ITEM_SETTLE_S) {
        item_remove(c, i, t);
        item_fail(i, "not settled in the ghost's hand", ITEM_RETRY_S, t);
    }
}

// Slot i has nothing now: say so in the status (its name stays).
static void item_status_empty(int i) {
    ItemStatus *o = &t_status.items.slot[i];
    o->state = t_items[i].state;
    o->actor = 0, o->place[0] = 0, o->dist = -1.0f, o->item_state = -1, o->holder_ok = 0, o->has_rel = 0;
}

// Forget every item without a call (a stage change took them along, as the ghost). 0.6.1: a taken item goes on the
// watch list (plain reads), so one the stage change did not take along counts as a stray; a request still pending
// is released by the next items part if its unit still points back at our handle (items_orphans).
static void items_forget(double t) {
    for (int i = 0; i < ITEM_SLOTS; i++) {
        size_item_set(i, 0);
        if (t_items[i].state == IS_GRAB || t_items[i].state == IS_HELD) trash_add(&g_item_link[i], t, 1);
        if (t_items[i].state == IS_PENDING) t_item_orphan[i] = 1;
        else memset(&g_item_handle[i], 0, sizeof g_item_handle[i]);
        if (t_items[i].state != IS_NONE && t_items[i].state != IS_FAILED) t_items[i].state = IS_NONE;
        t_items[i].name = NULL;
        item_zero_link(i);
        item_status_empty(i);
    }
    t_items_ghost = 0;
}

// The requests items_forget left pending: released if the unit is still ours (as tick_pending does for the ghost),
// else only forgotten. Before anything else uses the handles.
static void items_orphans(struct Cpu *c, double t) {
    for (int i = 0; i < ITEM_SLOTS; i++) {
        if (!t_item_orphan[i]) continue;
        t_item_orphan[i] = 0;
        uint64_t unit = g_item_handle[i].unit;
        if (unit && self_u64(unit + 8, t) == P(&g_item_handle[i])) {
            GCALL1(c, HANDLE_RELEASE, &g_item_handle[i]);
            GCALL1(c, HANDLE_CLEAR, &g_item_handle[i]);
        } else {
            memset(&g_item_handle[i], 0, sizeof g_item_handle[i]);
        }
    }
}

// Delete every item (the ghost goes, or went: the game removed it). Their names stay: a new ghost asks for them again.
static void items_drop(struct Cpu *c, double t) {
    for (int i = 0; i < ITEM_SLOTS; i++) {
        item_remove(c, i, t);
        t_items[i].name = NULL;  // (asked for again, with fresh tries, once there is a ghost)
        item_status_empty(i);
    }
    t_items_ghost = 0;
}

// Each tick (after the ghost's part): ghost is the live ghost (0: none).
static void items_tick(struct Cpu *c, uint64_t ghost, const Requests *rq, double t) {
    ItemsStatus *st = &t_status.items;
    st->on = rq->items_on, st->back = rq->items_back, st->mask = rq->items_mask;
    for (int i = 0; i < ITEM_SLOTS; i++)
        if (t_item_orphan[i]) {
            items_orphans(c, t);  // (requests a stage change left pending: before a handle is used again)
            break;
        }
    int busy = 0;
    for (int i = 0; i < ITEM_SLOTS; i++) busy |= t_items[i].state != IS_NONE && t_items[i].state != IS_FAILED;
    for (int k = 0; k < TRASH_MAX && !busy; k++) busy = t_trash_t[k] != 0;
    if (!busy && !(rq->items_on && ghost)) {  // (nothing to do: the feature costs nothing until a `ghost items`)
        if (!ghost) t_items_ghost = 0;
        for (int i = 0; i < ITEM_SLOTS; i++)
            if (t_items[i].name || t_items[i].state != IS_NONE) {  // (only failed or empty ones: no game calls)
                t_items[i].name = NULL, t_items[i].state = IS_NONE;
                memset(&st->slot[i], 0, sizeof st->slot[i]);
                st->slot[i].dist = st->slot[i].hold = -1.0f, st->slot[i].item_state = -1;
            }
        return;
    }
    int64_t q0, q1, calls0 = g_ncalls, queries0 = g_nqueries;
    uint64_t cy0 = 0, cy1 = 0;
    QueryThreadCycleTime(GetCurrentThread(), &cy0);
    QueryPerformanceCounter(&q0);
    if (!ghost) {
        items_drop(c, t);  // (a safety: every way out of "live" removes them first)
    } else {
        if (ghost != t_items_ghost) {  // a new ghost: look its bones up again; every slot starts over
            items_drop(c, t);
            t_items_ghost = ghost;
            t_bones_done = 0, t_bones_next = 0, t_bones_until = t + ITEM_BONES_FOR_S;
            for (int k = 0; k < NBONES; k++) t_bone_unit[k] = t_bone_idx[k] = -1;
            memset(st->bones, 0, sizeof st->bones);
        }
        items_bones(ghost, t);
        for (int i = 0; i < ITEM_SLOTS; i++) {
            ItemSlot *s = &t_items[i];
            ItemStatus *o = &st->slot[i];
            if (!t_bones_done) break;  // (still looking: nothing changes yet)
            const char *want = rq->items_on && items_hands() ? rq->item_names[i] : "";
            if (*want && !item_name_ok(i, want)) want = "";  // (cmd_items refused it already: a second check)
            if (strcmp(want, s->name ? s->name : "")) {  // a new name, or none: the old item goes first
                item_remove(c, i, t);
                s->name = *want ? intern_item(want) : NULL;
                s->tries = 0;
                s->state = s->name ? IS_WAIT : *want ? IS_FAILED : IS_NONE;
                s->retry_at = 0;
                memset(o, 0, sizeof *o);
                o->dist = o->hold = -1.0f, o->item_state = -1;
                if (*want && !s->name) {
                    snprintf(o->why, sizeof o->why, "too many different item names; restart the game");
                    note("items: too many different item names used; restart the game");
                }
            }
            snprintf(o->name, sizeof o->name, "%s", want);
            int has_rel = rq->item_has_rel[i] != 0;  // (0.6.2: the other player's own back offset, when sent)
            if (has_rel != s->has_rel || (has_rel && memcmp(s->rel, rq->item_rel[i], sizeof s->rel))) {
                s->has_rel = has_rel;
                if (has_rel) memcpy(s->rel, rq->item_rel[i], sizeof s->rel);
                s->rel_new = 1;
            }
            switch (s->state) {
            case IS_WAIT:
                if (t >= s->retry_at && !item_request(c, i, ghost, t)) s->retry_at = t + 0.5;
                break;
            case IS_PENDING:
                item_pending(c, i, ghost, t);
                break;
            case IS_GRAB:
                item_grab_tick(c, i, ghost, t);
                break;
            case IS_HELD:
                item_held(c, i, ghost, rq->items_back && (rq->items_mask >> i & 1) && t_bone_idx[BONE_POD] >= 0, t);
                break;
            }
            o->state = s->state;
            o->tries = s->tries;
            if (s->state != IS_HELD) o->place[0] = 0, o->dist = -1.0f, o->has_rel = 0;
            if (s->state != IS_GRAB && s->state != IS_HELD) o->actor = 0, o->item_state = -1, o->holder_ok = 0;
        }
    }
    trash_tick(t);
    QueryPerformanceCounter(&q1);
    QueryThreadCycleTime(GetCurrentThread(), &cy1);
    double us = (double)(q1 - q0) * 1e6 / (double)g_qpf;
    int64_t calls = g_ncalls - calls0;
    t_items_us_avg = t_items_us_avg > 0 ? t_items_us_avg + (us - t_items_us_avg) * 0.05 : us;
    t_items_calls += ((double)calls - t_items_calls) * 0.05;
    t_items_queries += ((double)(g_nqueries - queries0) - t_items_queries) * 0.05;
    times_add(&t_items_times, us);
    st->us = times_median(&t_items_times, &st->us_max);
    double sum = 0;
    for (int k = 0; k < t_items_times.n; k++) sum += t_items_times.v[k];
    st->us_mean = t_items_times.n ? sum / t_items_times.n : 0;
    st->us_avg = t_items_us_avg, st->calls = t_items_calls, st->queries = t_items_queries;
    if (us > SLOW_PART_MS * 1e3) {  // (0.6.1: few cycles for a long wall time = the thread was switched out)
        st->slow_parts++;
        snprintf(st->slow_part, sizeof st->slow_part, "%.1f ms wall, %llu cycles, %lld game calls", us / 1e3,
                 (unsigned long long)(cy1 - cy0), (long long)calls);
        if (t - t_slow_note_t >= 1.0) {
            t_slow_note_t = t;
            note("items: a slow frame part: %s", st->slow_part);
        }
    }
}

// `ghost give` (tests): spawn an actor near Link with a handle of its own, take it and forget it. Never the ghost or
// its items (a second `ghost spawn` would delete the sword Link took from it).
static void give_tick(struct Cpu *c, const Requests *rq, uint64_t pi, uint64_t player, double t) {
    ItemsStatus *st = &t_status.items;
    if (rq->give_seq != t_give_seq) {
        t_give_seq = rq->give_seq;
        if (t_give_state == GV_PENDING) {
            GCALL1(c, HANDLE_RELEASE, &g_give_handle);
            GCALL1(c, HANDLE_CLEAR, &g_give_handle);
        }
        snprintf(st->give_name, sizeof st->give_name, "%s", rq->give_name);
        st->give_actor = 0;
        uint64_t creator = rd64(CREATOR_PTR), heap = spawn_heap();
        const char *name = intern_item(rq->give_name);  // (0.6.1: the item table, not the ghost's 32 names)
        float pos[3], yaw;
        if (!name || !creator || !heap || rd8(creator + CREATOR_BLOCKED) || !player_pose(pi, player, pos, &yaw)) {
            t_give_state = GV_FAILED;
            note("give: no world, the game is busy, or too many names: not spawned");
            return;
        }
        float a = yaw * 0.017453292519943295f, r[9];
        float off[3] = {1.5f * __builtin_sinf(a), 0.8f, 1.5f * __builtin_cosf(a)};
        if (rq->give_has_off) memcpy(off, rq->give_off, sizeof off);
        yaw_rot(yaw, r);
        float m[12] = {r[0], r[1], r[2], pos[0] + off[0], r[3], r[4], r[5], pos[1] + off[1],
                       r[6], r[7], r[8], pos[2] + off[2]};
        memcpy(g_give_mtx, m, sizeof m);
        GCALL1(c, HANDLE_CLEAR, &g_give_handle);
        memset(&g_give_pack, 0, sizeof g_give_pack);
        g_give_pack.vptr = PACK_VPTR;
        GCALL1(c, PACK_CLEAR, g_give_pack.buf);
        g_key_m.vptr = STRING_OBJ_VPTR, g_key_m.str = KEY_M;
        gcall(c, PACK_ADD, P(g_give_pack.buf), P(g_give_mtx), P(&g_key_m), 0x30, 7, 0, 0);
        if (!(gcall(c, REQUEST_CREATE, creator, P(name), heap, P(&g_give_handle), P(&g_give_pack), 0, 1) & 1)) {
            GCALL1(c, HANDLE_CLEAR, &g_give_handle);
            t_give_state = GV_FAILED;
            note("give: %s refused", name);
            return;
        }
        t_give_state = GV_PENDING;
        t_give_since = t;
    } else if (t_give_state == GV_PENDING) {
        uint64_t unit = g_give_handle.unit;
        if (unit && rd64(unit + 8) != P(&g_give_handle)) {
            GCALL1(c, HANDLE_CLEAR, &g_give_handle);
            t_give_state = GV_FAILED;
        } else if (GCALL1(c, HANDLE_READY, &g_give_handle) & 1) {
            uint64_t proc = GCALL1(c, HANDLE_PEEK, &g_give_handle);
            uint64_t info = proc ? gcall(c, ACTOR_TYPEINFO, 0, 0, 0, 0, 0, 0, 0) : 0;
            uint64_t isa = proc ? self_u64(self_u64(proc, t) + 0x10, t) : 0;
            if (!proc || !info || !isa || !(GCALL2(c, isa, proc, info) & 1)) {
                GCALL1(c, HANDLE_RELEASE, &g_give_handle);
                GCALL1(c, HANDLE_CLEAR, &g_give_handle);
                t_give_state = GV_FAILED;
                return;
            }
            GCALL3(c, SET_MTX, proc, g_give_mtx, 0);
            GCALL1(c, HANDLE_TAKE, &g_give_handle);  // (taken and woken; then forgotten: Link takes it)
            st->give_actor = proc;
            t_give_state = GV_DONE;
            note("give: %s spawned near Link", st->give_name);
        } else if (GCALL1(c, HANDLE_FAILED, &g_give_handle) & 1) {
            GCALL1(c, HANDLE_RELEASE_FAILED, &g_give_handle);
            GCALL1(c, HANDLE_CLEAR, &g_give_handle);
            t_give_state = GV_FAILED;
        } else if (t - t_give_since > PENDING_TIMEOUT) {
            GCALL1(c, HANDLE_RELEASE, &g_give_handle);
            GCALL1(c, HANDLE_CLEAR, &g_give_handle);
            t_give_state = GV_FAILED;
        }
    }
    st->give_state = t_give_state;
}

// Remove our ghost (a pending request is released) and forget it. Its items go first, in the same tick.
// 0.6.2: a stage change drops the ghost and its items without a call (they were thought gone with the old stage).
// The fourteenth round's Game Over and Continue kept a ghost that had spawned 0.1 s before the change, and two items,
// next to the new ghost. So what a stage change drops is watched (plain reads), and once Link is back on the current
// stage for LEFTOVER_WAIT_S, whatever of it the game kept is removed the normal way: the game's own link check first,
// an item gets its own bone follow back first, never Link's. Nothing is touched while there is no player (a load).
static void old_ghost_add(double t) {
    for (int k = 0; k < OLD_MAX; k++)
        if (!t_old_ghost_t[k]) {
            g_old_ghost[k] = g_link;
            t_old_ghost_t[k] = t;
            return;
        }
}

static void leftovers_tick(struct Cpu *c, uint64_t player, double t) {
    int ghosts = 0, items = 0;
    for (int k = 0; k < OLD_MAX; k++) ghosts += t_old_ghost_t[k] != 0;
    for (int k = 0; k < TRASH_MAX; k++) items += t_trash_t[k] != 0 && t_trash_stage[k];
    t_status.items.old_ghosts = ghosts, t_status.items.old_items = items;
    if (!ghosts && !items) {
        t_stage_back_t = 0;
        return;
    }
    uint64_t heap = spawn_heap();
    if (!player || !heap) {  // (loading: wait)
        t_stage_back_t = 0;
        return;
    }
    if (!t_stage_back_t || heap != t_stage_back_heap) t_stage_back_t = t, t_stage_back_heap = heap;
    if (t - t_stage_back_t < LEFTOVER_WAIT_S) return;
    for (int k = 0; k < OLD_MAX; k++) {
        if (!t_old_ghost_t[k]) continue;
        if (t - t_old_ghost_t[k] <= LEFTOVER_WATCH_S && link_ok(&g_old_ghost[k], t) &&
            (GCALL1(c, LINK_VALID, &g_old_ghost[k]) & 1)) {
            uint64_t proc = rd64(g_old_ghost[k].data + LINKDATA_PROC);
            if (proc && proc != t_status.actor && proc != player) {
                gcall(c, DELETE_LATER, proc, 0, 0, 0, 0, 0, 0);
                t_status.items.leftovers++;
                note("a ghost the stage change left behind was removed (%.0f s after the change)", t - t_old_ghost_t[k]);
            }
        }
        memset(&g_old_ghost[k], 0, sizeof g_old_ghost[k]);  // (forgotten: no game call on an old link)
        g_old_ghost[k].id = 0xffffffffu;
        t_old_ghost_t[k] = 0;
    }
    for (int k = 0; k < TRASH_MAX; k++) {
        if (!t_trash_t[k] || !t_trash_stage[k]) continue;
        t_trash_stage[k] = 0;  // (once; from now on an ordinary watched item: still there in a second, a stray)
        if (!link_ok(&g_trash[k], t) || !(GCALL1(c, LINK_VALID, &g_trash[k]) & 1)) continue;
        uint64_t proc = rd64(g_trash[k].data + LINKDATA_PROC);
        if (!proc || link_at(proc + ITEM_HOLDER, t) == player) continue;  // (Link's: never touched)
        uint64_t fol = self_u64(proc + ACTOR_FOLLOW, t);
        int ours = 0;
        for (int i = 0; i < ITEM_SLOTS; i++) ours |= follow_is_ours(i, fol);
        if (ours) {  // (its own follow back first: no item runs ours after we let it go)
            gcall(c, FOLLOW_SET, proc, proc + ITEM_FOLLOW, 0, 0, 0, 0, 0);
            fol = self_u64(proc + ACTOR_FOLLOW, t);
            int still = 0;
            for (int i = 0; i < ITEM_SLOTS; i++) still |= follow_is_ours(i, fol);
            if (still) {
                t_status.items.kept++;
                continue;
            }
        }
        gcall(c, DELETE_LATER, proc, 0, 0, 0, 0, 0, 0);
        t_trash_t[k] = t;  // (watched from now: gone within a second, or a stray)
        t_status.items.deleted++;
        t_status.items.leftovers++;
        note("an item the stage change left behind was removed");
    }
}

static void remove_ghost(struct Cpu *c) {
    items_drop(c, t_now);
    if (t_status.state == ST_PENDING) {
        GCALL1(c, HANDLE_RELEASE, &g_handle);  // (deletes a proc that was created but not taken)
        GCALL1(c, HANDLE_CLEAR, &g_handle);
    } else if (t_status.state == ST_LIVE) {
        if (GCALL1(c, LINK_VALID, &g_link) & 1) {
            uint64_t proc = rd64(g_link.data + 0x48);
            if (proc) gcall(c, DELETE_LATER, proc, 0, 0, 0, 0, 0, 0);
        }
        GCALL1(c, LINK_RESET, &g_link);
        zero_link();
    }
    t_status.state = ST_IDLE;
    t_status.actor = 0;
    t_status.has_actor_pos = 0;
    smooth_forget(&t_smooth);
}

static void despawn(struct Cpu *c) {
    remove_ghost(c);
    note("despawned");
}

// ---- animation (plan A of out/research/ghost-anim-plan.md: called from this tick, only when the choice changes)

static void anim_forget(void) {
    t_anim_actor = 0;
    t_anim_asked = NULL;
    t_anim_remote = 0;
    memset(&t_anim_auto, 0, sizeof t_anim_auto);
    memset(&t_missing, 0, sizeof t_missing);  // (another actor may have other names)
    t_status.as[0] = 0, t_status.anim_want[0] = 0, t_status.remote[0] = 0, t_status.missing[0] = 0;
    t_status.as_holder = 0, t_status.as_ok = 0, t_status.speed = 0, t_status.speed_given = 0;
}

// The names in m the actor lacks (missing_has), oldest first, comma-separated, cut to fit out (cap bytes).
static void missing_join(const Missing *m, char *out, size_t cap) {
    size_t n = 0;
    out[0] = 0;
    for (int k = 0; k < m->n; k++) {
        int i = m->n < MAX_MISSING ? k : (m->next + k) % MAX_MISSING;
        if (m->misses[i] < ANIM_STRIKES) continue;
        const char *s = m->name[i];
        size_t len = strlen(s);
        if (n + len + 2 >= cap) break;
        if (n) out[n++] = ',';
        memcpy(out + n, s, len + 1);
        n += len;
    }
}

static void copy_name(char out[32], const char *s) {
    size_t n = 0;
    if (s)
        while (n < 31 && s[n]) n++;
    if (n) memcpy(out, s, n);
    out[n] = 0;
}

// The AS the actor plays now (slot 0), into t_status.as.
static void as_read(struct Cpu *c, uint64_t holder) {
    uint64_t s = gcall(c, AS_CURRENT, holder, 0, 0, 0, 0, 0, 0);
    copy_name(t_status.as, (const char *)(uintptr_t)rd64(s ? s + 8 : 0));
}

static void as_play(struct Cpu *c, uint64_t actor, const char *name) {
    g_fake_action[0] = 0, g_fake_action[1] = actor;
    swmod_set_s(c, 0, -1.0f);  // morph frames: -1 = the ASList's own
    gcall(c, AS_PLAY, P(g_fake_action), P(name), 1, 0, 0, 0, 0);  // keep_if_same, slot 0, sub 0
    t_status.as_calls++;
}

// Each frame while live: the ghost is shown at x z now. The speed `ghost pos` gave counts only while it moves to
// `ghost pos` targets.
static void anim_tick(struct Cpu *c, uint64_t proc, float x, float z, double t) {
    if (proc != t_anim_actor) {  // a new ghost: start over
        anim_forget();
        t_anim_actor = proc;
    }
    anim_sample(&t_anim_auto, x, z, t);
    float given = t_status.move == MOVE_POS ? anim_given(t_given_speed, t_given_t, t) : -1.0f;
    int choice = anim_auto_step(&t_anim_auto, t, given);
    t_status.speed = t_anim_auto.speed;
    t_status.speed_given = given >= 0;
    uint64_t holder = rd64(proc + ACTOR_AS);
    t_status.as_holder = holder;
    // only the actor's own holder (the static dummy of an actor without animations has no owner)
    t_status.as_ok = holder && rd64(holder + AS_OWNER) == proc &&
                     *(volatile const int32_t *)(uintptr_t)(holder + AS_SLOTS) > 0;
    if (!t_status.as_ok) return;
    const char *want = t_status.anim == ANIM_AUTO ? AN_NAMES[choice]
                       : t_status.anim == ANIM_NAME ? t_anim_forced : NULL;
    int remote = 0;  // want is for the other player's name
    // `ghost pos ... anim` (0.5.0): the other player's own name, while it is fresh (as the speed) and the ghost moves
    // to `ghost pos` targets. Nothing changes when no name is given.
    const char *rn = t_status.anim == ANIM_AUTO && t_status.move == MOVE_POS && t_given_anim[0] &&
                             t - t_given_t < ANIM_GIVEN_S ? t_given_anim : NULL;
    copy_name(t_status.remote, rn);
    if (rn) {
        const char *pick = anim_remote_pick(rn, &t_missing), *name = pick ? intern_anim(pick, 0) : NULL;
        if (name) want = name, remote = name != AN_NAMES[choice];
        else if (pick) note("too many different animation names from the other player; new ones show by speed");
        // a change at most every ANIM_HOLD_S (the speed rule's own changes already wait that long)
        if (want != t_anim_asked && t_anim_asked && t - t_anim_switch_t < ANIM_HOLD_S)
            want = t_anim_asked, remote = t_anim_remote;
    }
    copy_name(t_status.anim_want, want);
    if (want && want != t_anim_asked) {  // the choice changed: ask once
        if (t_status.anim == ANIM_AUTO && t_anim_asked) t_status.as_changes++;
        as_play(c, proc, want);
        t_anim_asked = want;
        t_anim_remote = remote;
        t_status.remote_calls += remote;
        t_anim_call_t = t_anim_switch_t = t;
        t_anim_read_t = 0;  // (read it back now)
        t_anim_seen = 0, t_anim_misses = 0;
    }
    if (t < t_anim_read_t) return;
    t_anim_read_t = t + ANIM_READ_S;
    as_read(c, holder);
    if (!want) return;
    if (!strcmp(t_status.as, want)) {
        if (t_anim_remote && !t_anim_seen) missing_clear(&t_missing, want);  // (a miss before was only late)
        t_anim_seen = 1;
    } else if (t_anim_remote && !t_anim_seen && want == t_anim_asked) {
        // The other player's name doesn't play ANIM_VERIFY_S after the call: a miss. The call switches at once when
        // the actor has the name, but the game might show it a little late, so a first miss asks again at once (the
        // call keeps a name that already plays); after ANIM_STRIKES misses the actor lacks it: noted, and the next
        // frame picks a stand-in or the speed rule. Misses add up over later asks too (a change can come between).
        if (t - t_anim_call_t >= ANIM_VERIFY_S) {
            if (missing_strike(&t_missing, want) >= ANIM_STRIKES) {
                missing_join(&t_missing, t_status.missing, sizeof t_status.missing);
                t_status.remote_misses++;
                note("the actor has no %s animation; showing another one for it", want);
                t_anim_asked = NULL;
            } else {
                as_play(c, proc, want);
                t_status.remote_calls++;
                t_anim_call_t = t;
                t_anim_read_t = t + ANIM_VERIFY_S;
            }
        } else {
            t_anim_read_t = t_anim_call_t + ANIM_VERIFY_S;
        }
    } else if (t - t_anim_call_t >= ANIM_REASSERT_S) {
        // It plays something else: the actor's own AI changed it (ask again, slowly), or it never played it (a name
        // that isn't in the actor's ASList: ask ANIM_MISSES more times, then say so and stop).
        if (t_anim_seen || t_anim_misses < ANIM_MISSES) {
            t_anim_misses += !t_anim_seen;
            as_play(c, proc, want);
            t_status.as_reasserts++;
            t_anim_call_t = t;
        } else if (t_anim_misses++ == ANIM_MISSES) {
            note("the actor doesn't play %s (not one of its animations?)", want);
        }
    }
}

static void tick_live(struct Cpu *c, uint64_t pi, uint64_t player, double t) {
    if (!(GCALL1(c, LINK_VALID, &g_link) & 1)) {
        items_drop(c, t);  // (its items: each one still there is deleted)
        zero_link();
        lose("the game removed the ghost", t, t - t_live_since);
        return;
    }
    uint64_t proc = rd64(g_link.data + 0x48);
    t_status.actor = proc;
    t_status.live_s = t - t_live_since;
    if (t_status.live_s >= STABLE_S) t_status.respawns = 0, t_status.quick = 0;  // it stayed: the row ends
    int moved = 0;
    if (t_status.move != MOVE_NONE) {
        float lp[3], lyaw, yaw;
        if (t_status.move == MOVE_POS) {  // eased toward the latest target (smooth_step)
            float pose[4];
            smooth_step(&t_smooth, t, pose);
            memcpy(g_pos, pose, sizeof g_pos);
            yaw = pose[3];
        } else if (player_pose(pi, player, lp, &lyaw)) {
            g_pos[0] = lp[0] + t_status.target[0], g_pos[1] = lp[1] + t_status.target[1];
            g_pos[2] = lp[2] + t_status.target[2];
            yaw = lyaw;
            float pose[4] = {g_pos[0], g_pos[1], g_pos[2], yaw};
            smooth_place(&t_smooth, pose, t);  // (a `ghost pos` after this starts from here)
        } else {
            goto read_back;
        }
        yaw_rot(yaw, g_rot);
        // (where the game left it since our last move: far from there = the actor's own update puts it back)
        float was[3] = {rdf(proc + ACTOR_MTX + 0x0c), rdf(proc + ACTOR_MTX + 0x1c), rdf(proc + ACTOR_MTX + 0x2c)};
        t_status.pulled_back = t_moved ? dist3(was, t_last_moved) : 0.0f;
        gcall(c, SET_TRANSFORM, proc, P(g_pos), P(g_rot), 0, 0, 0, 0);
        // An actor with physics (the people's cutscene copies have it) gets its matrix back from its rigid body every
        // frame: move the bodies too, or it stays where it was spawned.
        t_status.physics = rd64(proc + ACTOR_PHYSICS) != 0;
        if (t_status.physics) {
            float m[12] = {g_rot[0], g_rot[1], g_rot[2], g_pos[0], g_rot[3], g_rot[4], g_rot[5], g_pos[1],
                           g_rot[6], g_rot[7], g_rot[8], g_pos[2]};
            memcpy(g_move_mtx, m, sizeof m);
            GCALL2(c, PHYS_SET_MTX, proc, g_move_mtx);
        }
        memcpy(t_last_moved, g_pos, sizeof t_last_moved);
        t_moved = 1;
        t_status.moves++;
        moved = 1;
    }
read_back:
    t_status.actor_pos[0] = rdf(proc + ACTOR_MTX + 0x0c);
    t_status.actor_pos[1] = rdf(proc + ACTOR_MTX + 0x1c);
    t_status.actor_pos[2] = rdf(proc + ACTOR_MTX + 0x2c);
    float zx = rdf(proc + ACTOR_MTX + 0x08), zz = rdf(proc + ACTOR_MTX + 0x28);
    t_status.actor_yaw = (zx || zz) ? __builtin_atan2f(zx, zz) * 57.29577951308232f : 0.0f;
    t_status.has_actor_pos = 1;
    // the speed: the one `ghost pos` gave (taken in ghost_tick), else from where the tick put it or where it is
    anim_tick(c, proc, moved ? g_pos[0] : t_status.actor_pos[0], moved ? g_pos[2] : t_status.actor_pos[2], t);
}

// How far (horizontally) from Link the ghost should be: the `ghost pos` target, the `follow` offset, or (not live yet)
// the spawn offset. -1: can't tell (no world yet), or a live ghost left where it is (`ghost stop`).
static float want_dist(uint64_t pi, uint64_t player) {
    float lp[3], lyaw;
    if (!player_pose(pi, player, lp, &lyaw)) return -1.0f;
    float dx, dz;
    if (t_status.move == MOVE_POS) {
        dx = t_status.target[0] - lp[0], dz = t_status.target[2] - lp[2];
    } else if (t_status.move == MOVE_FOLLOW) {
        dx = t_status.target[0], dz = t_status.target[2];
    } else if (t_status.state != ST_LIVE) {
        dx = t_want_off[0], dz = t_want_off[2];
    } else {
        return -1.0f;
    }
    return __builtin_sqrtf(dx * dx + dz * dz);
}

// The distance rule, each tick while the ghost is wanted: the game removes these actors farther than about 75 m from
// Link, so beyond far_m the mod doesn't spawn it (a live one, or a pending request, is removed): state "far", until
// it is within near_m again, then it spawns at once (at the latest target).
static void far_check(struct Cpu *c, uint64_t pi, uint64_t player, float far_m, float near_m, double t) {
    t_status.far_m = far_m, t_status.near_m = near_m;
    t_status.dist = t_status.wanted ? want_dist(pi, player) : -1.0f;
    if (!t_status.wanted) return;
    int was_far = t_status.state == ST_FAR, far = far_next(was_far, t_status.dist, far_m, near_m);
    if (far && !was_far) {
        remove_ghost(c);
        t_status.state = ST_FAR;
        t_status.fars++;
        t_status.respawns = 0, t_status.quick = 0;  // (not a failure; the back-off starts over)
        note("too far: %.0f m from Link (over %.0f m), not spawned until within %.0f m", t_status.dist, far_m,
             near_m);
    } else if (!far && was_far) {
        t_status.state = ST_IDLE;
        t_respawn_at = 0;  // (spawn now)
        if (t_status.dist >= 0) note("back within %.0f m (%.0f m from Link): spawning", near_m, t_status.dist);
        else note("distance rule off: spawning");
    }
}

static void ghost_tick_body(struct Cpu *c, double t);

// The name of a game call the mod makes (for the slow-call note).
static const char *call_name(uint64_t fn) {
    static const struct {
        uint64_t fn;
        const char *name;
    } N[] = {{REQUEST_CREATE, "REQUEST_CREATE"}, {HANDLE_TAKE, "HANDLE_TAKE"}, {HANDLE_RELEASE, "HANDLE_RELEASE"},
             {HANDLE_READY, "HANDLE_READY"}, {HANDLE_PEEK, "HANDLE_PEEK"}, {DELETE_LATER, "DELETE_LATER"},
             {WEAPON_GRAB, "WEAPON_GRAB"}, {SET_MTX, "SET_MTX"}, {SET_TRANSFORM, "SET_TRANSFORM"},
             {PHYS_SET_MTX, "PHYS_SET_MTX"}, {FOLLOW_SET, "FOLLOW_SET"}, {FOLLOW_CTOR, "FOLLOW_CTOR"},
             {LINK_VALID, "LINK_VALID"}, {LINK_SET, "LINK_SET"}, {LINK_RESET, "LINK_RESET"}, {AS_PLAY, "AS_PLAY"},
             {AS_CURRENT, "AS_CURRENT"}, {PLAYER_INFO_GET, "PLAYER_INFO_GET"}, {MTX_FROM_EULER, "MTX_FROM_EULER"}};
    for (size_t k = 0; k < sizeof N / sizeof *N; k++)
        if (N[k].fn == fn) return N[k].name;
    return "another call";
}

static void ghost_tick(struct Cpu *c) {
    int64_t q0, q1;
    QueryPerformanceCounter(&q0);
    double now = now_s();
    if (t_last_tick_t > 0 && now > t_last_tick_t) {  // (0.6.1: frame times: the tick runs once a frame)
        double ms = (now - t_last_tick_t) * 1e3;
        times_add(&t_frame_times, ms);
        t_status.items.frames++;
        t_status.items.frames_slow += ms > SLOW_FRAME_MS;
        t_status.items.frame_ms = times_median(&t_frame_times, &t_status.items.frame_ms_max);
    }
    t_last_tick_t = now;
    ghost_tick_body(c, now);
    QueryPerformanceCounter(&q1);
    double us = (double)(q1 - q0) * 1e6 / (double)g_qpf, sum = 0;
    times_add(&t_tick_times, us);
    t_status.items.tick_us = times_median(&t_tick_times, &t_status.items.tick_us_max);
    for (int k = 0; k < t_tick_times.n; k++) sum += t_tick_times.v[k];
    t_status.items.tick_us_avg = t_tick_times.n ? sum / t_tick_times.n : 0;
    t_status.items.slow_calls = g_slow_calls, t_status.items.call_most_ms = g_call_most_ms;
    if (g_slow_calls != t_slow_seen) {  // (0.6.1: a game call over SLOW_CALL_MS: its name; at most a note a second)
        t_slow_seen = g_slow_calls;
        snprintf(t_status.items.slow_call, sizeof t_status.items.slow_call, "%s %.1f ms", call_name(g_slow_fn),
                 g_slow_ms);
        if (now - t_slow_note_t >= 1.0) {
            t_slow_note_t = now;
            note("a slow game call: %s", t_status.items.slow_call);
        }
    }
}

static void ghost_tick_body(struct Cpu *c, double t) {
    t_now = t;
    t_status.ticks++;
    if (!t_status.first_t) t_status.first_t = t, t_win_start = t;
    t_status.last_t = t;
    t_win_ticks++;
    if (t - t_win_start >= 1.0) {
        t_status.rate = (double)t_win_ticks / (t - t_win_start);
        t_win_start = t, t_win_ticks = 0;
    }
    note_thread();

    uint64_t pi = rd64(PLAYER_INFO_PTR);
    uint64_t player = pi ? rd64(pi + PI_PLAYER) : 0;
    t_status.player_info = pi, t_status.player = player;
    if (t >= t_next_check) {  // guest_call check: the game's getter agrees with the memory read
        t_next_check = t + 1.0;
        if (pi) {
            uint64_t got = GCALL1(c, PLAYER_INFO_GET, pi);
            uint64_t raw = rd64(pi + PI_PLAYER);
            t_status.checks++;
            t_status.check_got = got, t_status.check_raw = raw;
            if (got == raw) t_status.check_ok++;
            else t_status.check_bad++;
        }
    }

    // take the requests
    Requests rq;
    lock();
    rq = g_req;
    unlock();
    t_status.move = rq.move;
    memcpy(t_status.target, rq.target, sizeof rq.target);
    if (rq.move == MOVE_POS && rq.pos_seq != t_pos_seq) {  // a new target (if two came since the last tick: the latest)
        t_pos_seq = rq.pos_seq;
        double at = rq.pos_t < t ? rq.pos_t : t;
        smooth_target(&t_smooth, rq.target, at);
        t_given_speed = rq.pos_speed, t_given_t = at;
        memcpy(t_given_anim, rq.pos_anim, sizeof t_given_anim);
        if (t_status.state == ST_LIVE)  // (a jump is not a speed: anim_target)
            anim_target(&t_anim_auto, rq.target[0], rq.target[2], at,
                        t_smooth.interval > 0 ? t_smooth.interval : INTERVAL_DEFAULT);
    }
    if (rq.seq != t_seq_seen) {  // a spawn or despawn since the last tick: only the latest counts
        t_seq_seen = rq.seq;
        if (t_status.state != ST_IDLE) despawn(c);  // (a spawn replaces the ghost there is)
        t_status.wanted = rq.want;
        if (rq.want) {
            memcpy(t_want_name, rq.name, sizeof t_want_name);
            memcpy(t_want_off, rq.off, sizeof t_want_off);
            t_default = rq.is_default, t_default_fails = 0;
            t_status.respawns = 0, t_status.quick = 0;
            t_respawn_at = 0;
        } else {
            note("despawned");
        }
    }
    if (rq.anim_seq != t_anim_seq) {  // `ghost anim`: ask (once) for what it says now
        t_anim_seq = rq.anim_seq;
        t_status.anim = rq.anim;
        memcpy(t_status.anim_name, rq.anim_name, sizeof t_status.anim_name);
        t_anim_forced = rq.anim == ANIM_NAME ? intern_anim(rq.anim_name, 1) : NULL;
        if (rq.anim == ANIM_NAME && !t_anim_forced) note("too many different animation names used; restart the game");
        t_anim_asked = NULL;
    }
    if (t < rq.self_until) {  // `ghost self` (only while a client asks)
        self_read(&t_status.self, pi, t);
        self_rel(&t_status.self, pi, t);  // (0.6.1: where his items are on him, after each equipment read)
    }

    // A stage change (a new spawn heap) takes the ghost along: forget it without touching it, spawn again later.
    // 0.6.1: the same heap with another player actor is no reload: the ghost and its items are removed the normal
    // way (the link checks come first). No player but the same heap: they are left alone (no call) until he is
    // back or the heap changes (before, this forgot them, and a ghost the game kept went on holding its items).
    int no_player = 0;
    if (t_status.state == ST_LIVE) {
        if (spawn_heap() != t_stage_heap) {
            old_ghost_add(t);  // (0.6.2: watched; removed after the change if the game kept it: leftovers_tick)
            items_forget(t);   // (its items too, without a call: watched until they are gone)
            zero_link();
            lose("stage changed: ghost dropped", t, t - t_live_since);
        } else if (player && player != t_stage_player) {
            double live_s = t - t_live_since;
            remove_ghost(c);
            lose("the player actor changed without a reload: ghost removed", t, live_s);
        } else if (!player) {
            no_player = 1;
            t_status.no_player_ticks++;
        }
    }
    t_status.no_player = no_player;
    leftovers_tick(c, player, t);  // (0.6.2: what a stage change dropped and the game kept)
    far_check(c, pi, player, rq.far_m, rq.near_m, t);

    switch (t_status.state) {
    case ST_IDLE:
        if (t_status.wanted && player && t >= t_respawn_at) request_spawn(c, pi, player, t);
        break;
    case ST_PENDING:
        tick_pending(c, t);
        break;
    case ST_LIVE:
        if (!no_player) tick_live(c, pi, player, t);
        break;
    }
    if (t_status.state != ST_LIVE && t_anim_actor) anim_forget();  // (never touch an old actor's animations)
    if (!no_player) items_tick(c, t_status.state == ST_LIVE ? t_status.actor : 0, &rq, t);  // 0.6.0: held items
    if (rq.give_seq != t_give_seq || t_give_state == GV_PENDING) give_tick(c, &rq, pi, player, t);
    t_status.targets = t_smooth.targets, t_status.snaps = t_smooth.snaps;
    t_status.interval = t_smooth.interval;
    t_status.behind =  // (only while it moves to `ghost pos` targets: `follow` places it without a target)
        t_status.move == MOVE_POS && t_smooth.placed && t_smooth.has_to ? dist3(t_smooth.shown, t_smooth.to) : 0.0f;
    t_status.retry_at = t_status.state == ST_IDLE && t_status.wanted ? t_respawn_at : 0;
    ItemsStatus *is = &t_status.items;  // (0.6.2: the ghost's own scale; the memory checks' cost)
    float ax[3], sc[3];
    is->ghost_scale = t_status.state == ST_LIVE && t_status.actor && actor_size(t_status.actor, ax, sc, t)
                          ? (sc[0] + sc[1] + sc[2]) / 3.0f : -1.0f;
    is->vq_tick = g_vq_tick, is->vq_slow = g_vq_slow, is->vq_most_ms = g_vq_most_ms;
    is->scale_sets = __atomic_load_n(&g_scale_sets, __ATOMIC_RELAXED);
    is->vq_helper = g_vq_helper, is->vq_helper_ms = g_vq_helper_ms, is->vq_widened = g_vq_widened;
    int big = 0;
    for (int k = 0; k < MEM_CACHE; k++) big += g_mem_cache[k].big && t < g_mem_cache[k].until;
    is->mem_big = big;
}

static void job_wrapper(struct Cpu *c) {
    g_orig_job(c);  // the game's frame job first, as if nothing changed
    if (__atomic_exchange_n(&g_in_tick, 1, __ATOMIC_ACQUIRE)) {  // (never seen: the job doesn't overlap itself)
        lock();
        g_status.overlaps++;
        unlock();
        return;
    }
    SavedRegs saved;
    save_regs(c, &saved);
    ghost_tick(c);
    restore_regs(c, &saved);
    lock();
    int64_t overlaps = g_status.overlaps;
    g_status = t_status;
    g_status.overlaps = overlaps;
    unlock();
    __atomic_store_n(&g_in_tick, 0, __ATOMIC_RELEASE);
}

// ------------------------------------------------------------------ the command (frame thread)

static void reply_hex(SwModReply *r, const char *key, uint64_t v) {
    char buf[24];
    snprintf(buf, sizeof buf, "0x%llx", (unsigned long long)v);
    api->reply_str(r, key, buf);
}

static int parse_float(const char *s, float *out) {
    char *end;
    double d = strtod(s, &end);
    if (end == s || *end || d != d || d > 1e7 || d < -1e7) return 0;
    *out = (float)d;
    return 1;
}

static int valid_name(const char *s) {
    size_t n = strlen(s);
    if (!n || n > 63) return 0;
    for (size_t i = 0; i < n; i++) {
        char ch = s[i];
        if (!((ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '_'))
            return 0;
    }
    return 1;
}

// A 3x4 matrix as 12 numbers (row by row), "" when there is none.
static void rel_text(int has, const float m[12], char *out, size_t cap) {
    size_t n = 0;
    out[0] = 0;
    for (int k = 0; has && k < 12 && n < cap; k++) {
        int w = snprintf(out + n, cap - n, k ? " %.4f" : "%.4f", (double)m[k]);
        if (w < 0) break;
        n += (size_t)w;
    }
}

static void report(SwModReply *r) {
    Status s;
    lock();
    s = g_status;
    unlock();
    double t = now_s();
    api->reply_str(r, "state", STATE_NAMES[s.state]);
    api->reply_bool(r, "wanted", s.wanted != 0);
    api->reply_str(r, "name", s.name);
    reply_hex(r, "actor", s.actor);
    if (s.has_actor_pos) {
        api->reply_num(r, "x", s.actor_pos[0]);
        api->reply_num(r, "y", s.actor_pos[1]);
        api->reply_num(r, "z", s.actor_pos[2]);
        api->reply_num(r, "yaw", s.actor_yaw);
    }
    static const char *const moves[] = {"none", "pos", "follow"};
    api->reply_str(r, "move", moves[s.move]);
    if (s.move != MOVE_NONE) {
        api->reply_num(r, "target.0", s.target[0]);
        api->reply_num(r, "target.1", s.target[1]);
        api->reply_num(r, "target.2", s.target[2]);
        if (s.move == MOVE_POS) api->reply_num(r, "target.yaw", s.target[3]);
    }
    api->reply_int(r, "requests", s.requests);
    api->reply_int(r, "spawned", s.spawned);
    api->reply_int(r, "moves", s.moves);
    api->reply_int(r, "targets", s.targets);
    api->reply_int(r, "snaps", s.snaps);
    api->reply_num(r, "interval_s", s.interval);
    api->reply_num(r, "behind_m", s.behind);
    api->reply_bool(r, "physics", s.physics != 0);
    api->reply_num(r, "pulled_back_m", s.pulled_back);
    static const char *const anims[] = {"auto", "off", "name"};
    api->reply_str(r, "anim", s.anim == ANIM_NAME ? s.anim_name : anims[s.anim]);
    api->reply_str(r, "anim_want", s.anim_want);
    api->reply_str(r, "as", s.as);
    reply_hex(r, "as_holder", s.as_holder);
    api->reply_bool(r, "as_ok", s.as_ok != 0);
    api->reply_num(r, "speed_mps", s.speed);
    api->reply_str(r, "speed_from", s.speed_given ? "sent" : "positions");
    api->reply_bool(r, "takes_speed", true);  // (`ghost pos x y z yaw speed`: tools/mp/client.py looks for this)
    api->reply_bool(r, "takes_anim", true);   // (0.5.0: `ghost pos x y z yaw speed anim`; the client looks for it)
    api->reply_bool(r, "has_self", true);     // (0.5.0: `ghost self`)
    api->reply_bool(r, "has_items", true);    // (0.6.0: `ghost items`; the client looks for it)
    api->reply_bool(r, "has_items_rel", true);  // (0.6.2: `ghost items rel`; the client looks for it)
    api->reply_str(r, "version", GHOST_VERSION);
    const ItemsStatus *it = &s.items;
    api->reply_bool(r, "items.on", it->on != 0);
    api->reply_bool(r, "items.back", it->back != 0);
    api->reply_int(r, "items.mask", it->mask);
    api->reply_str(r, "items.bones", it->bones);
    api->reply_int(r, "items.stray", it->stray);
    api->reply_int(r, "items.dying", it->dying);
    api->reply_num(r, "items.us", it->us);
    api->reply_num(r, "items.us_avg", it->us_avg);
    api->reply_num(r, "items.us_mean", it->us_mean);
    api->reply_num(r, "items.us_max", it->us_max);
    api->reply_num(r, "items.calls", it->calls);
    api->reply_num(r, "items.queries", it->queries);
    api->reply_num(r, "tick_us", it->tick_us);
    api->reply_num(r, "tick_us_avg", it->tick_us_avg);
    api->reply_num(r, "tick_us_max", it->tick_us_max);
    api->reply_int(r, "items.slow_parts", it->slow_parts);
    api->reply_str(r, "items.slow_part", it->slow_part);
    api->reply_int(r, "slow_calls", it->slow_calls);
    api->reply_str(r, "slow_call", it->slow_call);
    api->reply_num(r, "call_most_ms", it->call_most_ms);
    api->reply_num(r, "frame_ms", it->frame_ms);
    api->reply_num(r, "frame_ms_max", it->frame_ms_max);
    api->reply_int(r, "frames", it->frames);
    api->reply_int(r, "frames_slow", it->frames_slow);
    api->reply_bool(r, "no_player", s.no_player != 0);
    api->reply_int(r, "no_player_ticks", s.no_player_ticks);
    api->reply_int(r, "items.requests", it->requests);
    api->reply_int(r, "items.held", it->held);
    api->reply_int(r, "items.deleted", it->deleted);
    api->reply_int(r, "items.lost", it->lost);
    api->reply_int(r, "items.taken", it->taken);
    api->reply_int(r, "items.kept", it->kept);
    api->reply_int(r, "items.parks", it->parks);
    api->reply_int(r, "items.scale_sets", it->scale_sets);
    api->reply_num(r, "ghost_scale", it->ghost_scale);
    api->reply_int(r, "mem.queries", it->vq_tick);  // (0.6.2: VirtualQuery on the tick; over 5 ms; the longest)
    api->reply_int(r, "mem.slow", it->vq_slow);
    api->reply_num(r, "mem.most_ms", it->vq_most_ms);
    api->reply_int(r, "mem.big", it->mem_big);
    api->reply_int(r, "mem.helper", it->vq_helper);
    api->reply_num(r, "mem.helper_ms", it->vq_helper_ms);
    api->reply_int(r, "mem.widened", it->vq_widened);
    api->reply_int(r, "leftovers", it->leftovers);
    api->reply_int(r, "old_ghosts", it->old_ghosts);
    api->reply_int(r, "old_items", it->old_items);
    for (int i = 0; i < ITEM_SLOTS; i++) {
        const ItemStatus *o = &it->slot[i];
        char key[32];
#define ITEM_KEY(k) (snprintf(key, sizeof key, "items.%d." k, i), key)
        api->reply_str(r, ITEM_KEY("name"), o->name);
        api->reply_str(r, ITEM_KEY("state"), IS_NAMES[o->state >= 0 && o->state <= IS_FAILED ? o->state : 0]);
        reply_hex(r, ITEM_KEY("actor"), o->actor);
        api->reply_str(r, ITEM_KEY("place"), o->place);
        api->reply_num(r, ITEM_KEY("dist_m"), o->dist);
        api->reply_num(r, ITEM_KEY("hold_m"), o->hold);
        api->reply_int(r, ITEM_KEY("tries"), o->tries);
        api->reply_int(r, ITEM_KEY("item_state"), o->item_state);
        api->reply_bool(r, ITEM_KEY("holder_ok"), o->holder_ok != 0);
        api->reply_str(r, ITEM_KEY("why"), o->why);
        api->reply_int(r, ITEM_KEY("refusals"), o->refusals);
        char rel[160];
        rel_text(o->has_rel, o->rel, rel, sizeof rel);
        api->reply_str(r, ITEM_KEY("rel"), rel);
        int held = o->state == IS_HELD;  // (0.6.2: how big it is drawn, and why)
        api->reply_num(r, ITEM_KEY("size"), held ? o->size : -1.0);
        api->reply_num(r, ITEM_KEY("size_first"), held ? o->size_first : -1.0);
        api->reply_num(r, ITEM_KEY("axis"), held ? o->axis : -1.0);
        api->reply_num(r, ITEM_KEY("own_scale"), held ? o->own_scale : -1.0);
        api->reply_num(r, ITEM_KEY("model_scale"), held ? o->model_scale : -1.0);
        api->reply_bool(r, ITEM_KEY("copied"), held && o->copied);
#undef ITEM_KEY
    }
    api->reply_str(r, "give.state", GV_NAMES[it->give_state >= 0 && it->give_state <= GV_FAILED ? it->give_state : 0]);
    api->reply_str(r, "give.name", it->give_name);
    reply_hex(r, "give.actor", it->give_actor);
    api->reply_str(r, "remote", s.remote);
    api->reply_int(r, "remote_calls", s.remote_calls);
    api->reply_int(r, "remote_misses", s.remote_misses);
    api->reply_str(r, "remote_missing", s.missing);
    api->reply_str(r, "link_actor", LINK_ACTOR);
    api->reply_int(r, "as_calls", s.as_calls);
    api->reply_int(r, "as_reasserts", s.as_reasserts);
    api->reply_int(r, "as_changes", s.as_changes);
    api->reply_int(r, "drops", s.drops);
    api->reply_int(r, "tries", s.respawns);
    api->reply_num(r, "dist_m", s.dist);
    api->reply_num(r, "far_m", s.far_m);
    api->reply_num(r, "near_m", s.near_m);
    api->reply_int(r, "fars", s.fars);
    api->reply_int(r, "quick_losses", s.quick);
    api->reply_num(r, "retry_in_s", s.retry_at > t ? s.retry_at - t : 0.0);
    api->reply_num(r, "pending_s", s.state == ST_PENDING ? s.pending_s : 0.0);
    api->reply_num(r, "live_s", s.state == ST_LIVE ? s.live_s : 0.0);
    api->reply_str(r, "last", s.last);
    api->reply_int(r, "ticks", s.ticks);
    api->reply_num(r, "ticks_per_s", s.rate);
    api->reply_num(r, "since_last_tick_s", s.ticks ? t - s.last_t : -1.0);
    api->reply_int(r, "overlaps", s.overlaps);
    api->reply_int(r, "threads", s.ntid);
    for (int i = 0; i < s.ntid; i++) {
        char key[32];
        snprintf(key, sizeof key, "thread.%d.id", i);
        api->reply_int(r, key, (int64_t)s.tid[i]);
        snprintf(key, sizeof key, "thread.%d.ticks", i);
        api->reply_int(r, key, s.tid_ticks[i]);
    }
    api->reply_int(r, "check.ok", s.check_ok);
    api->reply_int(r, "check.bad", s.check_bad);
    reply_hex(r, "check.getter", s.check_got);
    reply_hex(r, "check.raw", s.check_raw);
    reply_hex(r, "player", s.player);
    if (s.has_actor_pos)
        api->reply_text(r, "ghost %s: %s at %.1f %.1f %.1f, %.1f m/s%s, playing %s (%s); %lld ticks, %.0f/s",
                        STATE_NAMES[s.state], s.name, s.actor_pos[0], s.actor_pos[1], s.actor_pos[2], s.speed,
                        s.speed_given ? " (sent)" : "", *s.as ? s.as : "-", s.last, (long long)s.ticks, s.rate);
    else
        api->reply_text(r, "ghost %s%s%s (%s); %lld ticks, %.0f/s", STATE_NAMES[s.state], *s.name ? ": " : "",
                        s.name, s.last, (long long)s.ticks, s.rate);
}

// `ghost self`: your own Link, as the tick read it (`active` false until the next frame after the first ask).
static void report_self(SwModReply *r) {
    Status st;
    lock();
    st = g_status;
    unlock();
    const SelfState *s = &st.self;
    double age = s->reads ? now_s() - s->read_t : -1.0;
    int active = s->reads && age >= 0 && age < 0.5;
    api->reply_bool(r, "active", active != 0);
    api->reply_bool(r, "found", s->found != 0);
    api->reply_str(r, "anim", s->anim);
    api->reply_bool(r, "attack", s->attack != 0);
    api->reply_int(r, "attacks", s->attacks);
    api->reply_str(r, "body", s->body);
    api->reply_num(r, "body_w", s->body_w);
    api->reply_str(r, "lower", s->lower);
    api->reply_num(r, "lower_w", s->lower_w);
    api->reply_str(r, "upper", s->upper);
    api->reply_num(r, "upper_w", s->upper_w);
    api->reply_str(r, "gesture", s->gesture);
    api->reply_bool(r, "equip_ok", s->equip_ok != 0);
    api->reply_str(r, "weapon", s->weapon);
    api->reply_bool(r, "weapon_class", s->weapon_class != 0);
    api->reply_str(r, "shield", s->shield);
    api->reply_str(r, "bow", s->bow);
    api->reply_bool(r, "weapon_back", s->back[0] != 0);  // (0.6.0: on his back, not in his hand)
    api->reply_bool(r, "shield_back", s->back[1] != 0);
    api->reply_bool(r, "bow_back", s->back[2] != 0);
    api->reply_int(r, "on_back", (s->back[0] ? 1 : 0) | (s->back[1] ? 2 : 0) | (s->back[2] ? 4 : 0));
    static const char *const KEYS[3] = {"weapon", "shield", "bow"};
    for (int i = 0; i < 3; i++) {  // (0.6.1: each item in the frame of his Pod_A, and its distance to Pod_A, B, C)
        char key[32], text[160];
        snprintf(key, sizeof key, "%s_rel", KEYS[i]);
        rel_text(s->has_rel[i], s->rel[i], text, sizeof text);
        api->reply_str(r, key, text);
        snprintf(key, sizeof key, "%s_pods", KEYS[i]);
        snprintf(text, sizeof text, "%.4f %.4f %.4f", (double)s->pods[i][0], (double)s->pods[i][1],
                 (double)s->pods[i][2]);
        api->reply_str(r, key, s->item[i] ? text : "");
        snprintf(key, sizeof key, "%s_size", KEYS[i]);  // (0.6.2: how big his item is drawn; its matrix's axes)
        api->reply_num(r, key, s->item[i] ? s->size[i] : -1.0);
        snprintf(key, sizeof key, "%s_axis", KEYS[i]);
        api->reply_num(r, key, s->item[i] ? s->axis[i] : -1.0);
    }
    char shape[64];
    int n = 0;
    shape[0] = 0;
    for (int k = 0; k < SELF_SLOTS && s->shape[k] >= 0 && n < (int)sizeof shape - 8; k++)
        n += snprintf(shape + n, sizeof shape - n, k ? ",%d" : "%d", s->shape[k]);
    api->reply_str(r, "shape", shape);
    api->reply_int(r, "slots", s->nslots);
    api->reply_int(r, "reads", s->reads);
    api->reply_num(r, "age_s", age);
    if (!active)
        api->reply_text(r, "self: reading starts with the next frame (ask again)");
    else if (!s->found)
        api->reply_text(r, "self: no Link (title screen, loading, or not the player class)");
    else
        api->reply_text(r, "self: %s%s; weapon %s, shield %s, bow %s", *s->anim ? s->anim : "-",
                        s->attack ? " (attack)" : "", *s->weapon ? s->weapon : "-", *s->shield ? s->shield : "-",
                        *s->bow ? s->bow : "-");
}

#define USAGE                                                                                                     \
    "usage: ghost [spawn [name|link] [dx dy dz] | pos x y z [yaw [speed [anim]]] | follow dx dy dz | stop"           \
    " | despawn | anim auto|off|name | far far_m [near_m] | self | items weapon|- shield|- bow|- [mask]"             \
    " | items off | items back on|off | give name [dx dy dz]]"

// `ghost items ...` (0.6.0): what the ghost holds.
static void cmd_items(int argc, char **argv, SwModReply *r) {
    if (argc == 3 && !strcmp(argv[2], "off")) {
        lock();
        g_req.items_on = 0;
        memset(g_req.item_names, 0, sizeof g_req.item_names);
        memset(g_req.item_has_rel, 0, sizeof g_req.item_has_rel);
        unlock();
        api->reply_str(r, "items", "off");
        return;
    }
    if (argc == 4 && !strcmp(argv[2], "back") && (!strcmp(argv[3], "on") || !strcmp(argv[3], "off"))) {
        int on = !strcmp(argv[3], "on");
        lock();
        g_req.items_back = on;
        unlock();
        api->reply_bool(r, "back", on != 0);
        return;
    }
    if (argc >= 4 && !strcmp(argv[2], "rel")) {  // 0.6.2: `ghost items rel <slot 0-2> <12 numbers>|-`
        char *end;
        long slot = strtol(argv[3], &end, 10);
        float m[12];
        int ok = end != argv[3] && !*end && slot >= 0 && slot <= 2 && (argc == 5 || argc == 16);
        if (ok && argc == 16)
            for (int k = 0; k < 12 && ok; k++)  // (a turn: each entry within 1.01; a place: within 2 m of Pod_A)
                ok = parse_float(argv[4 + k], &m[k]) && finite_f(m[k], (k % 4 == 3) ? 2.0f : 1.01f);
        if (ok && argc == 5) ok = !strcmp(argv[4], "-");
        if (!ok) {
            api->reply_fail(r, "usage: ghost items rel <slot 0-2> <12 numbers: a 3x4 matrix row by row> | -");
            return;
        }
        lock();
        g_req.item_has_rel[slot] = argc == 16;
        if (argc == 16) memcpy(g_req.item_rel[slot], m, sizeof m);
        unlock();
        api->reply_int(r, "slot", slot);
        api->reply_bool(r, "rel", argc == 16);
        return;
    }
    if (argc != 5 && argc != 6) {
        api->reply_fail(r, "usage: ghost items weapon|- shield|- bow|- [mask 0-7] | off | back on|off | rel slot m...|-");
        return;
    }
    char names[3][64] = {{0}}, refused[200] = {0};
    for (int i = 0; i < 3; i++) {
        const char *a = argv[2 + i];
        if (!strcmp(a, "-")) continue;
        if (!valid_name(a)) {
            api->reply_fail(r, "bad item name (letters, digits and _, up to 63; - for none): %s", a);
            return;
        }
        if (!item_name_ok(i, a)) {  // (0.6.1: not an item for that hand, or a special one: shown as none)
            size_t n = strlen(refused);
            snprintf(refused + n, sizeof refused - n, "%s%s", n ? " " : "", a);
            continue;
        }
        memcpy(names[i], a, strlen(a) + 1);
    }
    int mask = 4;  // (the default: the bow on the back)
    if (argc == 6) {
        char *end;
        long m = strtol(argv[5], &end, 10);
        if (end == argv[5] || *end || m < 0 || m > 7) {
            api->reply_fail(r, "bad mask (0-7: bit 0 weapon, 1 shield, 2 bow on the back): %s", argv[5]);
            return;
        }
        mask = (int)m;
    }
    lock();
    g_req.items_on = 1;
    memcpy(g_req.item_names, names, sizeof names);
    g_req.items_mask = mask;
    unlock();
    api->reply_str(r, "weapon", names[0]);
    api->reply_str(r, "shield", names[1]);
    api->reply_str(r, "bow", names[2]);
    api->reply_int(r, "mask", mask);
    api->reply_str(r, "refused", refused);  // (0.6.1: names not spawned: not a weapon, shield or bow for that hand,
                                            // or a special one never tried; "" none)
}

// `ghost give name [dx dy dz]` (tests): an actor near Link for him to take; never the ghost or its items.
static void cmd_give(int argc, char **argv, SwModReply *r) {
    float off[3] = {0, 0, 0};
    if ((argc != 3 && argc != 6) || !valid_name(argv[2])) {
        api->reply_fail(r, "usage: ghost give name [dx dy dz] (default: 1.5 m in front of Link)");
        return;
    }
    for (int k = 0; argc == 6 && k < 3; k++)
        if (!parse_float(argv[3 + k], &off[k])) {
            api->reply_fail(r, "bad offset: %s", argv[3 + k]);
            return;
        }
    lock();
    g_req.give_seq++;
    memcpy(g_req.give_name, argv[2], strlen(argv[2]) + 1);
    memcpy(g_req.give_off, off, sizeof off);
    g_req.give_has_off = argc == 6;
    unlock();
    api->reply_str(r, "give", argv[2]);
}

static void cmd_ghost(int argc, char **argv, SwModReply *r) {
    if (argc == 1) {
        report(r);
        return;
    }
    const char *sub = argv[1];
    float v[5] = {0, 0, 0, 0, -1.0f};
    if (!strcmp(sub, "spawn")) {
        const char *name = DEFAULT_ACTOR;
        int i = 2, is_default = 1;
        if (argc > 2 && !parse_float(argv[2], &v[0])) name = argv[i++], is_default = 0;
        if (!strcmp(name, "link")) name = LINK_ACTOR;  // (the Link body: no crate fallback)
        if (!valid_name(name)) {
            api->reply_fail(r, "bad actor name (letters, digits and _, up to 63)");
            return;
        }
        float off[3] = {3.0f, 0.0f, 0.0f};
        if (argc - i == 3) {
            for (int k = 0; k < 3; k++)
                if (!parse_float(argv[i + k], &off[k])) {
                    api->reply_fail(r, "bad offset: %s", argv[i + k]);
                    return;
                }
        } else if (argc != i) {
            api->reply_fail(r, USAGE);
            return;
        }
        lock();
        g_req.seq++;
        g_req.want = 1;
        memcpy(g_req.name, name, strlen(name) + 1);
        g_req.is_default = is_default;
        memcpy(g_req.off, off, sizeof off);
        unlock();
        api->reply_str(r, "spawn", name);
        api->reply_text(r, "ghost: spawning %s (see `ghost` for the state)", name);
        return;
    }
    if (!strcmp(sub, "pos") || !strcmp(sub, "follow")) {
        int pos = sub[0] == 'p', n = argc - 2;
        if (!(n == 3 || (pos && (n == 4 || n == 5 || n == 6)))) {
            api->reply_fail(r, USAGE);
            return;
        }
        for (int k = 0; k < n && k < 5; k++)
            if (!parse_float(argv[2 + k], &v[k])) {
                api->reply_fail(r, "bad number: %s", argv[2 + k]);
                return;
            }
        const char *anim = n == 6 && strcmp(argv[7], "-") ? argv[7] : "";
        if (*anim && (!valid_name(anim) || strlen(anim) > 31)) {
            api->reply_fail(r, "bad animation name (letters, digits and _, up to 31; - for none): %s", anim);
            return;
        }
        double at = now_s();
        lock();
        g_req.move = pos ? MOVE_POS : MOVE_FOLLOW;
        memcpy(g_req.target, v, sizeof g_req.target);
        if (pos) {
            g_req.pos_seq++, g_req.pos_t = at, g_req.pos_speed = v[4] >= 0 ? v[4] : -1.0f;
            memcpy(g_req.pos_anim, anim, strlen(anim) + 1);
        }
        unlock();
        api->reply_str(r, "move", sub);
        return;
    }
    if (!strcmp(sub, "stop")) {
        lock();
        g_req.move = MOVE_NONE;
        unlock();
        api->reply_str(r, "move", "none");
        return;
    }
    if (!strcmp(sub, "despawn")) {
        lock();
        g_req.seq++;
        g_req.want = 0;
        g_req.move = MOVE_NONE;
        unlock();
        api->reply_str(r, "despawn", "requested");
        return;
    }
    if (!strcmp(sub, "anim") && argc == 3) {
        const char *a = argv[2];
        int mode = !strcmp(a, "auto") ? ANIM_AUTO : !strcmp(a, "off") ? ANIM_OFF : ANIM_NAME;
        if (mode == ANIM_NAME && (!valid_name(a) || strlen(a) > 31)) {
            api->reply_fail(r, "bad animation name (auto, off, or an AS name: letters, digits and _, up to 31)");
            return;
        }
        lock();
        g_req.anim = mode;
        memcpy(g_req.anim_name, a, strlen(a) + 1);
        g_req.anim_seq++;
        unlock();
        api->reply_str(r, "anim", a);
        return;
    }
    if (!strcmp(sub, "self") && argc == 2) {
        double until = now_s() + SELF_KEEP_S;
        lock();
        g_req.self_until = until;  // (the tick reads Link from the next frame on, for SELF_KEEP_S)
        unlock();
        report_self(r);
        return;
    }
    if (!strcmp(sub, "far") && (argc == 3 || argc == 4)) {
        float far_m, near_m = 0;
        if (!parse_float(argv[2], &far_m) || far_m < 0 || (argc == 4 && !parse_float(argv[3], &near_m))) {
            api->reply_fail(r, "usage: ghost far far_m [near_m] (metres; far_m 0: off)");
            return;
        }
        if (argc == 3) near_m = far_m > 10 ? far_m - 10 : 0;
        if (near_m < 0 || near_m > far_m) {
            api->reply_fail(r, "near_m must be between 0 and far_m");
            return;
        }
        lock();
        g_req.far_m = far_m, g_req.near_m = near_m;
        unlock();
        api->reply_num(r, "far_m", far_m);
        api->reply_num(r, "near_m", near_m);
        return;
    }
    if (!strcmp(sub, "items")) {
        cmd_items(argc, argv, r);
        return;
    }
    if (!strcmp(sub, "give")) {
        cmd_give(argc, argv, r);
        return;
    }
    api->reply_fail(r, USAGE);
}

static const SwModCommand commands[] = {
    {"ghost",
     "[spawn [name|link] [dx dy dz] | pos x y z [yaw [speed [anim]]] | follow dx dy dz | stop | despawn"
     " | anim auto|off|name | far m [m] | self | items w|- s|- b|- [mask] | items off | items back on|off"
     " | give name [dx dy dz]]",
     "Ghost actor (example mod): state, or spawn / move / animate / remove it; self: your own Link's state; items: "
     "what it holds",
     cmd_ghost, 0},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    if (strcmp(api->game_version, "1.9.0") != 0) {
        api->log("ghost: made for game version 1.9.0, this is %s", api->game_version);
        return 2;
    }
    g_orig_job = api->dispatch_lookup(FRAME_JOB);
    if (!g_orig_job) {
        api->log("ghost: no recompiled function at %#llx", (unsigned long long)FRAME_JOB);
        return 3;
    }
    QueryPerformanceFrequency(&g_qpf);
    g_helper = CreateThread(0, 64 * 1024, mem_helper, 0, 0, 0);  // (0.6.2: keeps the big memory regions' answers)
    if (g_helper) SetThreadPriority(g_helper, -2);                 // (THREAD_PRIORITY_LOWEST)
    else api->log("ghost: no helper thread: big memory regions are asked about on the frame (slower)");
    api->hle_override(FRAME_JOB, job_wrapper, "ghost_frame_job");
    g_orig_weapon_calc = api->dispatch_lookup(WEAPON_CALC);  // (0.6.2: the ghost's items at the player's scale)
    if (g_orig_weapon_calc) api->hle_override(WEAPON_CALC, weapon_calc_wrapper, "ghost_weapon_calc");
    else api->log("ghost: no recompiled function at %#llx: the ghost's items are drawn at an NPC's scale",
                  (unsigned long long)WEAPON_CALC);
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("ghost: ready (frame job %#llx wrapped)", (unsigned long long)FRAME_JOB);
    return 0;
}

// Only for a `modload`ed copy (modhost never unloads mods loaded at game start): the helper thread stops first.
SWMOD_EXPORT void swmod_exit(const SwModApi *a) {
    (void)a;
    __atomic_store_n(&g_helper_stop, 1, __ATOMIC_RELEASE);
    if (g_helper) {
        WaitForSingleObject(g_helper, 5000);
        CloseHandle(g_helper);
        g_helper = 0;
    }
}
