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
// Version 2 added hooks (api->hook, api->can_hook): see docs/hooks.md. Version 3 added settings (api->setting_*,
// api->settings_ui): see docs/mod-settings.md. Version 4 added the file redirect (api->fs_redirect_*): see
// docs/code-mods.md "File redirect".
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

#define SWMOD_API_VERSION 4
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

// ---- file redirect (version 4, docs/code-mods.md "File redirect"). A mod names copies of game files ("from" ->
// "to", RomFS paths as the game asks for them, e.g. "Model/Armor_001.sbfres" -> "Model/SwLk_Armor_001.sbfres") in a
// group; while the group is on, the game's file opens of "from" open "to" instead (modhost wraps the four sead
// FileDevice doOpen_). OFF for everyone unless the player's SWMOD_FILE_REDIRECT=1 (modman sets it for a deploy that
// puts copies beside the game's files). A copy that fails to open: the original opens, as the game asked.
#define SWMOD_FSR_OK 0
#define SWMOD_FSR_OFF 1        // the file redirect is off (SWMOD_FILE_REDIRECT isn't set)
#define SWMOD_FSR_LATE 2       // fs_redirect_add only from swmod_init of a mod loaded at game start
#define SWMOD_FSR_BAD_ARGS 3   // an empty or too long group or path (group 31 bytes, path 200), or from == to
#define SWMOD_FSR_FULL 4       // too many groups (8) or redirects (2048)
#define SWMOD_FSR_TAKEN 5      // "from" is redirected already (to another copy, or by another group or mod)
#define SWMOD_FSR_NO_GROUP 6   // no such group
#define SWMOD_FSR_NOT_READY 7  // the group's redirects can't run: the file opens aren't wrapped (see savagewind.log)
typedef struct {
    uint32_t size;          // set it to sizeof(SwModFsRedirectInfo) before the call
    int32_t ready;          // 1: the four file opens are wrapped and the group has redirects: fs_redirect_set works
    int32_t on;             // the group is on now
    int32_t count;          // its redirects
    int32_t in_flight;      // file opens running now (any file, any group): 0 = the loaders are idle
    int32_t wrapped;        // how many of the four file opens modhost wraps
    uint64_t opens;         // opens of one of its "from" files (on or off)
    uint64_t redirects;     // opens it sent to a copy
    uint64_t fails;         // copies that failed to open (the original was opened instead)
    int64_t idle_ms;        // ms since the last file open ended (any file): flip a group only when the loaders idle
} SwModFsRedirectInfo;

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

// ---- settings (version 3, docs/mod-settings.md). A mod lists its settings in mod.toml ([[settings]]); modman
// compiles them into deploy\native\settings.txt, and modhost keeps their values in <library>\settings\<mod id>.json.
#define SWMOD_SET_OK 0
#define SWMOD_SET_NO_SETTING 1   // no such setting (not in the mod's mod.toml, or no settings.txt was deployed)
#define SWMOD_SET_BAD_VALUE 2    // not one of its values (a slider value between two steps is rounded to a step: OK)
#define SWMOD_SET_LOCKED 3       // settings_ui only: its enable_if is false now (setting_set, the mod's own, isn't)
#define SWMOD_SET_READ_ONLY 4    // settings_ui only: a text (edited on the PC) or a button (use press)

#define SWMOD_SETTING_TOGGLE 0   // ON/OFF: value 0 or 1
#define SWMOD_SETTING_SLIDER 1   // a number from min to max in steps: value = the step's index (min = 0)
#define SWMOD_SETTING_CHOICE 2   // one of its choices: value = the choice's index (mod.toml order)
#define SWMOD_SETTING_KEY 3      // a keyboard key: value = its Windows virtual-key code (0: none)
#define SWMOD_SETTING_TEXT 4     // a line of text (changed in Mod Manager on the PC, read-only in the game)
#define SWMOD_SETTING_BUTTON 5   // a button: value = how often it was pressed since the game started

#define SWMOD_APPLIES_LIVE 0     // the mod is told at once (its watchers run)
#define SWMOD_APPLIES_RESTART 1  // at the next game start: until then the getters return the value it started with
#define SWMOD_APPLIES_DEPLOY 2   // picks RomFS files, at the next deploy (Play): the getters return the deployed value

struct SwModApi;
// A watcher (setting_watch): key is the setting that changed, or NULL when several did (a choice's `sets`, a reset).
typedef void (*SwModSettingFn)(const struct SwModApi *self, const char *key, void *user);

// One setting, as settings_ui()->info fills it. Strings stay valid while the game runs.
typedef struct {
    uint32_t size;          // sizeof(SwModSettingInfo) in the host (fields are only added at the end)
    int type;               // SWMOD_SETTING_*
    int applies;            // SWMOD_APPLIES_*
    int values;             // how many values the row steps through: toggle 2, slider its steps, choice its choices;
                            // key, text and button 0
    const char *key;        // its mod.toml key
    const char *label;      // the row's title (at most 28 characters)
    const char *desc;       // the description line (at most 80 characters)
    const char *page;       // its [[pages]] key, "" on the mod's first page
    const char *unit;       // slider: the unit after the number (" percent"); button: the button's text; else ""
    double min, max, step;  // slider: value of step i = min + i * step
    int max_length;         // text: at most this many characters
    int default_index;      // the default (as get_index counts)
} SwModSettingInfo;

// For a settings screen (the Mod menu's "Mod settings and info" pages): every library mod that has settings, with
// m = 0..mods()-1 and s = 0..count(m)-1. Call it on the game's thread (the Options screen); the getters work on any.
// It has its own size: test a field newer than your header's with SWMOD_UI_HAS(ui, field).
typedef struct SwModSettingsUi {
    uint32_t size;                                 // sizeof(SwModSettingsUi) in the host
    uint32_t (*serial)(void);                      // goes up on every change of any value: redraw when it moved
    int (*mods)(void);                             // the mods with settings, in the library's order
    const char *(*mod_id)(int m);                  // its [mod] id ("giruka.better-sprint")
    const char *(*mod_lib_id)(int m);              // its id in the library (menu.txt's "mod" lines use it)
    const char *(*mod_name)(int m);
    bool (*mod_on)(int m);                         // on in this deploy (else its settings wait until it is on)
    int (*count)(int m);                           // its settings
    int (*page_count)(int m);                      // its [[pages]] (each one is a button on the mod's first page)
    // page p's key, label and description (any pointer may be NULL); false: no such page
    bool (*page_info)(int m, int p, const char **key, const char **label, const char **desc);
    // Set out->size = sizeof *out first: the host fills only the fields that fit, and sets size to what it filled.
    bool (*info)(int m, int s, SwModSettingInfo *out);   // false: no such setting
    int (*find)(int m, const char *key);           // the index of the setting with this key, -1: none
    int (*get_index)(int m, int s);                // the value now, as SWMOD_SETTING_* counts it (-1: no setting)
    // The text of value `index` (-1: of the value now): a choice's label, "120 percent", "F6" ("None"), the text,
    // the button's text, "ON"/"OFF". Returns its full length (-1: no setting); buf always ends in 0.
    int (*value_text)(int m, int s, int index, char *buf, size_t cap);
    int (*set_index)(int m, int s, int index);     // SWMOD_SET_*: a choice also sets its `sets`; live: watchers run
    int (*set_key)(int m, int s, const char *name);  // a key setting from key capture: "F6", "" = none; SWMOD_SET_*
    bool (*shown)(int m, int s);                   // its show_if now (true without one)
    bool (*enabled)(int m, int s);                 // its enable_if now (true without one)
    // Why it is locked, in words for the description line ("Locked: needs Sprint mode Custom."); "" when enabled.
    int (*lock_text)(int m, int s, char *buf, size_t cap);
    bool (*waiting)(int m, int s);                 // changed, and it applies at the next start or deploy
    int (*press)(int m, int s);                    // a button: counts the press, watchers run; SWMOD_SET_*
    int (*reset)(int m);                           // every setting of mod m back to its default (live: watchers run)
} SwModSettingsUi;
#define SWMOD_UI_HAS(ui, field) \
    ((ui) && offsetof(SwModSettingsUi, field) + sizeof(((const SwModSettingsUi *)0)->field) <= (size_t)(ui)->size)

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
    // The recompiled function whose entry is at guest_addr, or NULL. Works from swmod_init on. When mods loaded before
    // this one override guest_addr, it is the newest of their overrides (never this mod's own): see hle_override.
    SwModGuestFn (*dispatch_lookup)(uint64_t guest_addr);
    // The address of a named site of the game code (e.g. "player_info"), or 0. Works from swmod_init on.
    uint64_t (*find_site)(const char *name);
    // Calls of guest_addr that go through Savage Wind's dispatcher (virtual calls, function pointers, imports) run
    // fn instead. Direct calls inside main do not. Call it from swmod_init (before game threads start: each thread
    // caches lookups). To wrap: keep orig = dispatch_lookup(addr) and call orig(c) from fn. name must stay valid.
    // Mods loaded at game start chain: each one's orig is the override of the mod before it, the mod loaded last runs
    // first (docs/code-mods.md, "Two mods on one function"). Once the game runs, an overridden address is refused.
    // A mod loaded with `modload`: only from swmod_init or its own commands; a call from a hook, a thread of its own
    // or swmod_exit is refused with a log line (the override would outlive the mod's DLL).
    void (*hle_override)(uint64_t guest_addr, SwModGuestFn fn, const char *name);
    // Call a guest function on the calling game thread's Cpu (inside a hooked function). Saves and restores x30
    // only: save any other registers you need first. Address 0, or one inside the game's code where no function
    // starts (a site of another game version), is refused with a log line and does nothing (registers unchanged).
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

    // ---- version 3: settings (docs/mod-settings.md). Check SWMOD_API_OK(api, 3) or SWMOD_HAS(api, setting_int)
    // first; on an older host use your defaults. The getters work on any thread, at any time, without a lock.
    // The [mod] id of the mod.toml this DLL came with (from deploy\native\settings.txt), or NULL: no settings.
    const char *mod_id;
    // The value of setting `key` of this mod, or def when there is none: toggle 0/1, slider its value (rounded; use
    // setting_num for steps below 1), choice its index, key the Windows virtual-key code (0: none), button how often
    // it was pressed. For `restart` and `deploy` settings: the value the game started with.
    int64_t (*setting_int)(const struct SwModApi *self, const char *key, int64_t def);
    // The same as a number: slider its value (0.1 steps stay 0.1 steps), the others as setting_int.
    double (*setting_num)(const struct SwModApi *self, const char *key, double def);
    // The value as text: choice its value ("fast", not the label), key its name ("F6", "" none), text the text,
    // toggle "true"/"false", slider the number. Returns the full length (buf gets as much as fits, ending in 0),
    // or -1 when there is no such setting (buf gets "").
    int (*setting_str)(const struct SwModApi *self, const char *key, char *buf, size_t cap);
    // Call fn after a `live` setting of this mod changed in the Mod menu or with modset, or a button was pressed. It
    // runs on the thread that changed it: the game's thread inside the Options screen (or the frame thread for a
    // console command). Keep it short: set a flag and do the work in your hook or command. From swmod_init only; at
    // most 8 per mod. Returns SWMOD_SET_OK, or SWMOD_SET_NO_SETTING when called later or with too many.
    int (*setting_watch)(const struct SwModApi *self, SwModSettingFn fn, void *user);
    // Change a setting of this mod yourself (a counter, a preset worked out in code): checked like the Mod menu's
    // change (but not refused when locked), saved, and shown on its page at once. Your watchers are not called for
    // it. value is text as setting_str gives it ("fast", "120", "true", "F6"). Returns SWMOD_SET_*.
    int (*setting_set)(const struct SwModApi *self, const char *key, const char *value);
    // The table a settings screen uses (the Mod menu): every mod's settings. NULL when no settings.txt was deployed.
    const SwModSettingsUi *(*settings_ui)(void);

    // ---- version 4: the file redirect (above). Check SWMOD_API_OK(api, 4) first.
    // Add a redirect to `group` (made on first use). From swmod_init of a mod loaded at game start only: the table is
    // fixed once the game runs. Returns SWMOD_FSR_*. The strings are copied.
    int (*fs_redirect_add)(const char *group, const char *from, const char *to);
    // Turn a group on (1) or off (0), from any thread. It counts from the next file open on: flip it while the loaders
    // are idle (fs_redirect_state's in_flight 0 and idle_ms), so no model loads half old, half new. SWMOD_FSR_*.
    int (*fs_redirect_set)(const char *group, int on);
    // A group's state into *info (set info->size first). SWMOD_FSR_OK, _OFF or _NO_GROUP (info is still filled).
    int (*fs_redirect_state)(const char *group, SwModFsRedirectInfo *info);
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
