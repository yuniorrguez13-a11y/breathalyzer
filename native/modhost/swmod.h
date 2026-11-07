// swmod.h - the API a native code mod gets from modhost (the proxy game library for Savage Wind, see README.md).
//
// A mod is a Windows DLL in <data root>\mods\native\ that exports one function:
//
//     #include "swmod.h"
//     SWMOD_EXPORT int swmod_init(const SwModApi *api) { ...; return 0; }   // 0 = ok, anything else = failed
//
// modhost calls it once, while Savage Wind loads the game code: before the game's modules are in memory and before
// any game thread runs. A mod loaded later with the developer command `modload` (docs/code-mods.md, "Load a mod while
// the game runs") gets its swmod_init on the frame thread instead, while game threads run; such a mod can be unloaded
// again, and may export swmod_exit (below). Keep the api pointer: it stays valid until the mod is unloaded (for a mod
// loaded at game start: for the life of the process). Everything the mod needs
// from the game comes through it, so a mod links against nothing from Savage Wind and needs only this header (guest
// registers: SWMOD_REGS below, or gamelib-src\cpu.h from the release).
//
// Versions: SWMOD_API_VERSION goes up by one each time fields are added, and fields are only ever added at the end.
// A host with api_version N has every field of versions 1..N. A mod that needs version N checks SWMOD_API_OK(api, N);
// a mod built with a newer header can still run on an older host and test single newer fields with SWMOD_HAS.
// Version 2 added hooks (api->hook, api->can_hook): see docs/hooks.md.
//
// C++ mods: this header declares swmod_init as extern "C", so it is exported unmangled. (The zig that ships with
// Savage Wind can't compile C++ that includes <stddef.h>: use C, or another compiler for C++.)
//
// Declarations that mirror Savage Wind's (the command table, the Cpu type) follow Savage Wind Recomp's
// runtime/control.h and runtime/cpu.h (MIT License, Copyright (c) 2026 Revivedx and the Savage Wind Recomp
// contributors; Copyright (c) 2026 Peiikai).
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define SWMOD_API_VERSION 2
#define SWMOD_INIT_NAME "swmod_init"
#ifdef __cplusplus
#define SWMOD_EXPORT extern "C" __declspec(dllexport)
#else
#define SWMOD_EXPORT __declspec(dllexport)
#endif

// True when the host gives at least API version `version` (it then has every field of that version).
#define SWMOD_API_OK(api, version) ((api) && (api)->api_version >= (uint32_t)(version))
// True when the host's SwModApi has `field`, e.g. SWMOD_HAS(api, current_cpu): for fields newer than the version
// the mod checked with SWMOD_API_OK.
#define SWMOD_HAS(api, field) \
    ((api) && offsetof(SwModApi, field) + sizeof(((const SwModApi *)0)->field) <= (size_t)(api)->size)

#ifdef __cplusplus
extern "C" {
#endif

// The guest CPU state of a game thread. Its layout is gamelib-src\cpu.h in the Savage Wind release; read registers
// through SWMOD_REGS(c) below, or include that header. Guest pointers are host pointers.
struct Cpu;
typedef void (*SwModGuestFn)(struct Cpu *c);

// The registers of a Cpu, at the offsets of gamelib-src\cpu.h (0.4.1-alpha.4). SWMOD_REGS(c)->x[0] is x0.
typedef struct {
    uint64_t lo, hi;          // one 128-bit FP/SIMD register: s<n> and d<n> are the low 32 / 64 bits of v<n>
} SwModVReg;
typedef struct {
    uint64_t x[32];           // 0: x0-x30 (x[31] unused; sp is separate)
    uint64_t sp;              // 256
    uint64_t pc;              // 264
    uint64_t nzcv;            // 272
    uint8_t _runtime[512 - 280];
    SwModVReg v[32];          // 512: v0-v31
} SwModRegs;
#ifdef __cplusplus
static_assert(offsetof(SwModRegs, sp) == 256 && offsetof(SwModRegs, v) == 512, "Cpu layout");
#else
_Static_assert(offsetof(SwModRegs, sp) == 256 && offsetof(SwModRegs, v) == 512, "Cpu layout");
#endif
#define SWMOD_REGS(c) ((SwModRegs *)(void *)(c))

// s<n> / d<n> as float / double. Setting one clears the rest of v<n>, as the instructions that write it do.
static inline float swmod_get_s(struct Cpu *c, int n) {
    uint32_t b = (uint32_t)SWMOD_REGS(c)->v[n].lo;
    float f;
    memcpy(&f, &b, sizeof f);
    return f;
}
static inline void swmod_set_s(struct Cpu *c, int n, float f) {
    uint32_t b;
    memcpy(&b, &f, sizeof b);
    SWMOD_REGS(c)->v[n].lo = b;
    SWMOD_REGS(c)->v[n].hi = 0;
}
static inline double swmod_get_d(struct Cpu *c, int n) {
    double d;
    memcpy(&d, &SWMOD_REGS(c)->v[n].lo, sizeof d);
    return d;
}
static inline void swmod_set_d(struct Cpu *c, int n, double d) {
    memcpy(&SWMOD_REGS(c)->v[n].lo, &d, sizeof d);
    SWMOD_REGS(c)->v[n].hi = 0;
}

// ---- hooks (version 2, docs/hooks.md). They run on the game thread that calls the function. The game code must
// have a hook slot for the address: build it with tools/sw_rebuild.py build --hooks <list of addresses>.
// Arguments are in x0-x7 and v0-v7 (s0/d0 for the first float), the result in x0 or v0. Change only those: the game
// expects x19-x29, sp and v8-v15 unchanged after a call.
// before: runs first. Return true to skip the function (and the after hooks): set its result yourself then.
typedef bool (*SwModHookBefore)(struct Cpu *c);
// after: runs when the function has returned; its result is in x0 / v0.
typedef void (*SwModHookAfter)(struct Cpu *c);
// replace: runs instead of the function. orig(c) runs the game's own function (as often as you like).
typedef void (*SwModHookReplace)(struct Cpu *c, SwModGuestFn orig);

#define SWMOD_HOOK_OK 0
#define SWMOD_HOOK_NO_SLOTS 1  // this game code has no hook slots: rebuild it with hooks
#define SWMOD_HOOK_NO_SLOT 2   // no hook slot for this address: add it to the hooks list and rebuild
#define SWMOD_HOOK_TAKEN 3     // another mod already replaces this function (one replace per function)
#define SWMOD_HOOK_LATE 4      // hooks can only be added from swmod_init
#define SWMOD_HOOK_BAD_ARGS 5  // no before, after or replace given
#define SWMOD_HOOK_FULL 6      // too many hooks (8 before and 8 after per function, 256 functions)
#define SWMOD_HOOK_BUSY 7      // (modload only) a game thread still runs this function's earlier hooks: try again

// ---- control commands (the SAVAGEWIND_CONTROL socket and the in-game console, docs/CONTROL.md in Savage Wind)
typedef struct SwModReply SwModReply;  // a command's answer (opaque)
// argv[0] is the command name, argv[1..argc-1] its arguments. Answer with api->reply_*; nothing = ok with no data.
typedef void (*SwModCommandFn)(int argc, char **argv, SwModReply *r);

#define SWMOD_CMD_PLAYER 1u  // flags: only reads state, cannot break anything: any player may run it from the console.
                             // 0 = developer mode only (SAVAGEWIND_CONTROL=<port> or SAVAGEWIND_DEV=1)

typedef struct {         // the same layout as Savage Wind's ControlCmd
    const char *name;    // unique, e.g. "hello"
    const char *args;    // usage for `help`, e.g. "<count>" ("" if none)
    const char *help;    // one line: what it does
    SwModCommandFn fn;   // runs on the frame thread, once per presented frame at most
    unsigned flags;      // SWMOD_CMD_PLAYER or 0
} SwModCommand;

typedef struct SwModApi {
    uint32_t api_version;      // the host's SWMOD_API_VERSION
    uint32_t size;             // sizeof(SwModApi) in the host
    const char *host_version;  // modhost's version, e.g. "0.1.0"
    const char *game_version;  // the game version the game code was built from, e.g. "1.9.0"
    const char *mod_name;      // this mod's file name without ".dll"

    // One line into savagewind.log as "[mod] <text>" (printf format; start the text with your mod's name).
    void (*log)(const char *fmt, ...);

    // Control commands. The table and its strings must stay valid (make them static). Call it from swmod_init (or a
    // command). Returns how many were registered. An entry is skipped, with a "[modhost] mod <file>: command <name>
    // skipped" log line, when its name is taken (by Savage Wind, modhost or another mod), when it has no name or
    // function, or when Savage Wind doesn't add it (its command table is full). A mod loaded with `modload` can only
    // add commands from swmod_init or its own commands: from a hook, a thread of its own or swmod_exit they are
    // skipped (they would stay after the unload).
    int (*control_register)(const SwModCommand *cmds, int count);
    void (*reply_str)(SwModReply *r, const char *key, const char *value);
    void (*reply_int)(SwModReply *r, const char *key, int64_t value);
    void (*reply_num)(SwModReply *r, const char *key, double value);
    void (*reply_bool)(SwModReply *r, const char *key, bool value);
    void (*reply_text)(SwModReply *r, const char *fmt, ...);  // a line the console prints instead of the fields
    void (*reply_fail)(SwModReply *r, const char *fmt, ...);  // {"ok":false,"error":"..."}
    bool (*dev_mode)(void);                                   // developer mode on (SAVAGEWIND_CONTROL / SAVAGEWIND_DEV)

    // Game code. Addresses are guest addresses (main starts at 0x7000000000).
    // The recompiled function whose entry is at guest_addr, or NULL. Works from swmod_init on.
    SwModGuestFn (*dispatch_lookup)(uint64_t guest_addr);
    // The address of a named site of the game code (e.g. "player_info"), or 0. Works from swmod_init on.
    uint64_t (*find_site)(const char *name);
    // Calls of guest_addr that go through Savage Wind's dispatcher (virtual calls, function pointers, imports) run
    // fn instead. Direct calls inside main do not. Call it from swmod_init (before game threads start: each thread
    // caches lookups). To wrap: keep orig = dispatch_lookup(addr) and call orig(c) from fn. name must stay valid.
    // A mod loaded with `modload`: only from swmod_init or its own commands; a call from a hook, a thread of its own
    // or swmod_exit is refused with a log line (the override would outlive the mod's DLL).
    void (*hle_override)(uint64_t guest_addr, SwModGuestFn fn, const char *name);
    // Call a guest function on the calling game thread's Cpu (inside a hooked function). Saves and restores x30
    // only: save any other registers you need first.
    void (*guest_call)(struct Cpu *c, uint64_t guest_addr);
    // The calling game thread's Cpu, NULL on other threads (the frame thread commands run on is not a game thread).
    struct Cpu *(*current_cpu)(void);

    // ---- version 2: check SWMOD_API_OK(api, 2) or SWMOD_HAS(api, hook) first.
    // Hook the recompiled function whose entry is at guest_addr, for every call from another function (direct calls,
    // tail jumps and the dispatcher; not the function's jumps back to its own start, see docs/hooks.md). Give any of
    // before / after / replace (NULL for the rest). Call it from swmod_init. Returns
    // SWMOD_HOOK_OK, or an SWMOD_HOOK_* error (also logged). Several mods can hook one function: befores and afters
    // run in mod load order; only one replace per function.
    int (*hook)(uint64_t guest_addr, SwModHookBefore before, SwModHookAfter after, SwModHookReplace replace);
    // True when the game code has a hook slot for guest_addr.
    bool (*can_hook)(uint64_t guest_addr);
} SwModApi;

typedef int (*SwModInitFn)(const SwModApi *api);

// The one function a mod exports (declared here so that it gets C linkage in C++ too).
SWMOD_EXPORT int swmod_init(const SwModApi *api);

// Optional. For a mod loaded with `modload`: modhost calls swmod_exit(api) when it unloads the mod (modunload,
// modreload), after it took out the mod's hooks, overrides and commands and no game thread runs them any more, and
// before it frees the DLL. Stop any thread the mod started here, and wait for it: no code of the mod may run after
// swmod_exit returns. Not called when swmod_init failed, and never for mods loaded at game start (they stay loaded).
#define SWMOD_EXIT_NAME "swmod_exit"
typedef void (*SwModExitFn)(const SwModApi *api);
SWMOD_EXPORT void swmod_exit(const SwModApi *api);

#ifdef __cplusplus
}
#endif
