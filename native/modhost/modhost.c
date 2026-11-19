// modhost.dll: a proxy game library for Savage Wind that loads native code mods, without changing Savage Wind.
//
// Savage Wind loads its recompiled game code from a DLL and calls the DLL's one export, swgame_tables(). With
// SAVAGEWIND_GAME_LIB=<path to modhost.dll> it loads this DLL instead. On that first call modhost:
//   1. loads the real game library (SWMOD_REAL_GAME_LIB, default <data root>\extracted\swgame.dll) and calls its
//      swgame_tables(),
//   2. registers the control commands `mods`, `modload`, `modunload` and `modreload`,
//   3. loads every <data root>\mods\native\*.dll (SWMOD_DIR to change the folder), in name order, and calls each one's
//      swmod_init(api) if it has one (swmod.h), logging one line per mod,
//   4. returns the real library's tables unchanged, so Savage Wind checks and uses the real game code as usual.
// If the real library can't be loaded it returns NULL and loads no mods; Savage Wind then falls back to the game
// code in extracted\ as if modhost weren't there.
//
// Hooks (mod API 2): game code rebuilt with tools/sw_rebuild.py --hooks exports swmod_hooks(), a table of hook slots
// (tools/gen_hooks.py writes it). Each hooked function f_<addr> is then a wrapper that calls the slot's entry; modhost
// sets that entry to hook_entry() for the functions mods hook, before any game code runs.
//
// Loading a mod while the game runs (docs/code-mods.md): the developer commands modload, modunload and modreload load
// a mod DLL into the running game and take it out again. modhost notes what each such mod adds (commands, hooks,
// hle_overrides) under the mod. Savage Wind can't remove a command or an override, so for these mods modhost
// registers stand-ins of its own (cmd_hot, hot_tramp_N) that call the mod only while it is loaded. Before it frees a
// mod's DLL, modhost takes the mod's hooks and overrides out and waits until no game thread still runs them (Gate);
// if one does, it puts everything back and refuses. Mods loaded at game start are never unloaded. Such a mod can add
// commands and overrides only from its swmod_init and its own commands (the thread modhost's commands run on): from a
// hook, a thread of its own or swmod_exit they are refused, so nothing it adds can escape the unload.
//
// Overlays (overlay.c, swmod_overlay.h): the export modhost_overlay_add lets a mod draw over the game with Savage Wind's
// own ImGui and hold the game's input while it does. Only when a mod asks, modhost checks and points the exe's calls
// of menu_render and menu_input_captured at its own functions (Savage Wind 0.4.1-alpha.4 only).
//
// Savage Wind changes the working directory to its data root before it loads the game code (runtime/paths.c), so
// relative paths here are relative to the data root.
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "platform_min.h"
#include "sw_runtime.h"
#include "swmod.h"

#define MODHOST_C  // (settings.c is part of this file: see the #include before fill_api)
#define MODHOST_VERSION "0.4.1"
#define MAX_MODS 64
#define PATH_W 1024  // wide characters
#define PATH_U 3072  // UTF-8 bytes
#define WAIT_MS 1000 // how long an unload waits for game threads to leave a mod's hooks (SWMOD_UNLOAD_WAIT_MS)

// ---- the hook slots of game code built with hooks (the same layout as the main_hooks.c tools/gen_hooks.py writes)
#define SWHOOK_ABI 1
#define SWHOOK_EXPORT "swmod_hooks"
typedef struct SwHookSlot SwHookSlot;
// Returns nonzero when it handled the call; 0: the wrapper runs the function itself (as a tail call).
typedef int (*SwHookEntry)(struct Cpu *c, struct SwHookSlot *slot);
struct SwHookSlot {
    uint64_t addr;      // the function's guest address
    GuestFn orig;       // the function itself (without the hook)
    SwHookEntry entry;  // set by the mod host before game code runs; NULL = not hooked
    void *user;         // the mod host's own data
};
typedef struct {
    uint32_t abi;       // SWHOOK_ABI
    uint32_t count;
    SwHookSlot *slots;  // sorted by addr
    const char *made_by;
} SwHookTable;
typedef const SwHookTable *(*SwHookTableFn)(void);

#define MAX_HOOKED 256   // functions with hooks
#define MAX_PER_HOOK 8   // before hooks, and after hooks, per function
#define MAX_HOOK_REGS (2 * MAX_PER_HOOK + 1)  // api->hook calls per function (each adds a before, an after or the replace)

// Counts the game threads inside code that a mod loaded while the game runs can own (its hooks, its overrides), so
// that modhost can tell when none is left in what it took out. A thread counts itself in inside[epoch] before it reads
// what to run and leaves when done. gate_sync flips the epoch twice and waits for each old count to reach 0: after
// that, no thread still runs anything that was replaced before the call.
typedef struct {
    uint32_t epoch;      // 0 or 1: the counter that threads coming in now use
    uint32_t inside[2];  // threads inside, by the epoch they came in with
    bool dirty;          // a gate_sync timed out: a thread may still run something old, so don't reuse it yet
} Gate;

// One published version of a function's hooks. Not changed while a thread may run it.
typedef struct {
    SwModHookBefore before[MAX_PER_HOOK];
    SwModHookAfter after[MAX_PER_HOOK];
    SwModHookReplace replace;
    int nbefore, nafter;
} HookList;

typedef struct {  // one api->hook call
    int mod;      // the Mod's id
    bool hot;     // its mod was loaded while the game runs
    SwModHookBefore before;
    SwModHookAfter after;
    SwModHookReplace replace;
} HookReg;

typedef struct {         // a hooked function (its slot's user pointer; stays once set)
    HookList plain;      // the hooks of the mods loaded at game start: complete before any game code runs
    HookList hot[2];     // all its hooks, with those of mods loaded later: hot[cur] is the published one
    HookList *pub;       // what hook_entry runs while has_hot is 1
    uint32_t has_hot;    // 1: mods loaded while the game runs hook it too (calls then count themselves in gate)
    int cur;
    Gate gate;
    HookReg regs[MAX_HOOK_REGS];  // in load order
    int nregs;
} HookState;

typedef struct {
    int id;              // 1, 2, ... in load order; a mod loaded again gets a new one
    bool used;           // this slot holds a mod (the slots of unloaded mods are used again)
    bool hot;            // loaded with modload while the game runs (can be unloaded)
    bool unloading;      // modunload is taking it out: it can't add anything
    char name[256];      // file name without .dll (for modload also without a "-<number>" ending)
    char file[PATH_U];   // file name
    char path[PATH_U];   // the full path (mods loaded while the game runs)
    char status[96];     // "ok", "init failed (code 1)", ...
    char commands[256];  // the commands it registered, comma separated
    char skipped[256];   // the commands it asked for that were not registered, comma separated
    char hooks[256];     // the functions it hooked (0x... addresses), comma separated
    char overrides[256]; // the addresses it overrode with hle_override, comma separated
    char no_slot[256];   // addresses it asked to hook that have no hook slot in this game code, comma separated
    char notes[1024];    // modload's reply: what didn't work and why, one line each
    int ncommands;
    int nhooks;
    bool ok;
    bool told_no_slots;  // the "no hook slots" line was logged for it
    bool switched_off;   // its swmod_init failed: its hooks and overrides were taken out (the DLL stays loaded)
    int settings;        // its mod's index in settings.txt (settings.c), -1: none
    HMODULE h;
    SwModApi api;
} Mod;

static Mod g_mods[MAX_MODS];  // the mods loaded at game start (in load order), then those loaded while it runs
static int g_next_id = 1;
static Mod *g_current;   // the mod whose swmod_init is running on thread g_current_tid (what it adds is listed for it)
static DWORD g_current_tid;
static Mod *g_cmd_mod;   // the mod loaded while the game runs whose command is running (cmd_hot, on g_ctl_tid)
static DWORD g_ctl_tid;  // the thread that runs modhost's commands (Savage Wind's frame thread)
static const GameLibTables *g_real;
static HMODULE g_real_h;
static const SwHookTable *g_hooks;  // NULL: the game code has no hook slots
static HookState g_states[MAX_HOOKED];
static int g_nstates;
static char g_real_path[PATH_U];
static char g_mod_dir[PATH_U];
static uint32_t g_wait_ms = WAIT_MS;

// ------------------------------------------------------------------ small helpers

static size_t wlen(const wchar_t *s) {
    size_t n = 0;
    while (s[n]) n++;
    return n;
}

// Append src to dst (cap wide chars). false if it doesn't fit.
static bool wcat(wchar_t *dst, size_t cap, const wchar_t *src) {
    size_t a = wlen(dst), b = wlen(src);
    if (a + b + 1 > cap) return false;
    memcpy(dst + a, src, (b + 1) * sizeof *src);
    return true;
}

static void to_utf8(const wchar_t *w, char *out, int size) {
    int n = WideCharToMultiByte(SW_CP_UTF8, 0, w, -1, out, size, NULL, NULL);
    if (n <= 0) snprintf(out, (size_t)size, "?");
}

static void copy_str(char *dst, size_t cap, const char *src) {
    size_t n = strlen(src);
    if (n >= cap) n = cap - 1;
    memcpy(dst, src, n);
    dst[n] = 0;
}

// Append name to a comma separated list (dropped when it doesn't fit).
static void list_add(char *list, size_t cap, const char *name) {
    size_t used = strlen(list), need = strlen(name) + (used ? 1 : 0);
    if (used + need + 1 > cap) return;
    if (used) list[used++] = ',';
    copy_str(list + used, cap - used, name);
}

// Whether a comma separated list has name as one of its items.
static bool list_has(const char *list, const char *name) {
    size_t n = strlen(name);
    for (const char *p = list; *p;) {
        const char *e = strchr(p, ',');
        size_t k = e ? (size_t)(e - p) : strlen(p);
        if (k == n && !memcmp(p, name, n)) return true;
        if (!e) break;
        p = e + 1;
    }
    return false;
}

// Equal, ignoring the case of ASCII letters (Windows file names).
static bool same_name(const char *a, const char *b) {
    for (;; a++, b++) {
        char x = *a >= 'A' && *a <= 'Z' ? (char)(*a + 32) : *a, y = *b >= 'A' && *b <= 'Z' ? (char)(*b + 32) : *b;
        if (x != y) return false;
        if (!x) return true;
    }
}

// An environment variable (wide), or false when unset or empty.
static bool env_w(const wchar_t *name, wchar_t *out, DWORD cap) {
    DWORD n = GetEnvironmentVariableW(name, out, cap);
    return n > 0 && n < cap;
}

// Absolute form of path (relative ones are taken from the working directory: the data root).
static bool full_path(const wchar_t *path, wchar_t *out, DWORD cap) {
    DWORD n = GetFullPathNameW(path, cap, out, NULL);
    return n > 0 && n < cap;
}

static bool is_dir(const wchar_t *path) {
    DWORD a = GetFileAttributesW(path);
    return a != SW_INVALID_ATTRIBUTES && (a & SW_ATTR_DIRECTORY);
}

// One line to stderr, which Savage Wind sends to savagewind.log. One write per line, like the runtime's LOG.
static void log_line(const char *prefix, const char *fmt, sw_va_list ap) {
    char buf[2048];
    size_t n = strlen(prefix);
    if (n > 64) n = 64;
    memcpy(buf, prefix, n);
    int m = vsnprintf(buf + n, sizeof buf - n - 1, fmt, ap);
    if (m < 0) m = 0;
    n += (size_t)m < sizeof buf - n - 1 ? (size_t)m : sizeof buf - n - 2;
    if (!n || buf[n - 1] != '\n') buf[n++] = '\n';
    struct _iobuf *err = __acrt_iob_func(2);
    fwrite(buf, 1, n, err);
    fflush(err);
}

static void host_log(const char *fmt, ...) {
    sw_va_list ap;
    sw_va_start(ap, fmt);
    log_line("[modhost] ", fmt, ap);
    sw_va_end(ap);
}

// A line for the reply of modload / modreload (only for mods loaded while the game runs). Lines are kept in m->notes.
static void note(Mod *m, const char *fmt, ...) {
    if (!m || !m->hot) return;
    char line[512];
    sw_va_list ap;
    sw_va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    sw_va_end(ap);
    size_t used = strlen(m->notes), n = strlen(line);
    if (used + n + 2 > sizeof m->notes) return;
    if (used) m->notes[used++] = '\n';
    memcpy(m->notes + used, line, n + 1);
}

static Mod *mod_by_id(int id) {
    for (int i = 0; i < MAX_MODS; i++)
        if (g_mods[i].used && g_mods[i].id == id) return &g_mods[i];
    return NULL;
}

static Mod *mod_by_name(const char *name) {
    for (int i = 0; i < MAX_MODS; i++)
        if (g_mods[i].used && same_name(g_mods[i].name, name)) return &g_mods[i];
    return NULL;
}

// The mod whose DLL has this code address, or NULL.
static Mod *mod_of_address(void *addr) {
    HMODULE h = NULL;
    if (!addr || !GetModuleHandleExW(SW_MODULE_FROM_ADDRESS | SW_MODULE_KEEP_REFCOUNT, (const wchar_t *)addr, &h))
        return NULL;
    for (int i = 0; i < MAX_MODS; i++)
        if (g_mods[i].used && g_mods[i].h == h) return &g_mods[i];
    return NULL;
}

// The mod whose swmod_init or command (cmd_hot) runs on this thread, or NULL: a call from any other thread (a game
// thread in a hook, a thread a mod started) gets NULL, even while some swmod_init runs on the frame thread.
static Mod *calling_mod(void) {
    DWORD me = GetCurrentThreadId();
    if (g_current && me == g_current_tid) return g_current;
    if (g_cmd_mod && me == g_ctl_tid) return g_cmd_mod;
    return NULL;
}

// The mod loaded while the game runs whose DLL holds p (its code or its data), or NULL. A function or name of such a
// mod must never reach Savage Wind directly: Savage Wind would keep it after the DLL is freed.
static Mod *hot_owner(const void *p) {
    Mod *m = mod_of_address((void *)p);
    return m && m->hot ? m : NULL;
}

#define LATE_WHY "a mod loaded while the game runs can only add it from swmod_init or its own command (not from a " \
                 "hook, a thread of its own or swmod_exit)"

// Guards the tables that a mod loaded at game start can still change from a game thread (g_start_ovr).
static uint32_t g_reg_lock;
static void reg_lock(void) {
    while (__atomic_exchange_n(&g_reg_lock, 1, __ATOMIC_ACQUIRE)) Sleep(0);
}
static void reg_unlock(void) { __atomic_store_n(&g_reg_lock, 0, __ATOMIC_RELEASE); }

// ------------------------------------------------------------------ gates (see Gate)

static uint64_t deadline(void) { return GetTickCount64() + g_wait_ms; }

static inline uint32_t gate_enter(Gate *g) {
    uint32_t i = __atomic_load_n(&g->epoch, __ATOMIC_SEQ_CST) & 1;
    __atomic_fetch_add(&g->inside[i], 1, __ATOMIC_SEQ_CST);  // (a full barrier: what it reads next is current)
    return i;
}

static inline void gate_leave(Gate *g, uint32_t i) { __atomic_fetch_sub(&g->inside[i], 1, __ATOMIC_RELEASE); }

// Wait until no thread still runs what was replaced before this call. false: the deadline passed first (the gate is
// then dirty: something old may still be in use).
static bool gate_sync(Gate *g, uint64_t until) {
    for (int round = 0; round < 2; round++) {
        uint32_t old = __atomic_load_n(&g->epoch, __ATOMIC_SEQ_CST) & 1;
        __atomic_store_n(&g->epoch, old ^ 1, __ATOMIC_SEQ_CST);
        for (int n = 0; __atomic_load_n(&g->inside[old], __ATOMIC_SEQ_CST); n++) {
            if (GetTickCount64() >= until) {
                g->dirty = true;
                return false;
            }
            Sleep(n < 50 ? 0 : 1);
        }
    }
    g->dirty = false;
    return true;
}

// ------------------------------------------------------------------ the API mods get (swmod.h)

static void api_log(const char *fmt, ...) {
    sw_va_list ap;
    sw_va_start(ap, fmt);
    log_line("[mod] ", fmt, ap);
    sw_va_end(ap);
}

// ---- control commands: register only names that are free, and count only what Savage Wind really added.
// Savage Wind's control_register returns nothing and skips a taken name or a full table with just a log line, so
// modhost asks Savage Wind's own `help` (run with the socket's rights, so it lists every command) before and after.

#define MAX_OWN 256
static const char *g_own[MAX_OWN];  // the names modhost and the mods registered (the strings stay valid)
static int g_nown;
static bool g_no_help;              // `help` didn't answer: duplicates are only checked among modhost's own names

// 1: Savage Wind has a command with this name, 0: it hasn't, -1: can't tell.
static int sw_has_command(const char *name) {
    if (g_no_help) return -1;
    ControlConsoleResult res;
    memset(&res, 0, sizeof res);
    control_exec_line("help", false, &res);
    int found = -1;
    if (!res.failed && res.text) {  // one line per command: "<name> <args>  <help>"
        found = 0;
        size_t n = strlen(name);
        for (const char *p = res.text; p && *p && !found;) {
            size_t k = 0;
            while (p[k] && p[k] != ' ' && p[k] != '\n') k++;
            found = k == n && !memcmp(p, name, n);
            p = strchr(p, '\n');
            if (p) p++;
        }
    }
    control_console_free(&res);
    if (found < 0) {
        g_no_help = true;
        host_log("Savage Wind's `help` didn't answer: command names are only checked among the mods");
    }
    return found;
}

static bool own_has(const char *name) {
    for (int i = 0; i < g_nown; i++)
        if (!strcmp(g_own[i], name)) return true;
    return false;
}

typedef enum { REG_ADDED, REG_TAKEN, REG_NOT_ADDED } RegResult;

static RegResult register_one(const ControlCmd *c) {
    int had = sw_has_command(c->name);
    if (had > 0 || own_has(c->name)) return REG_TAKEN;
    control_register(c, 1);  // (Savage Wind copies the entry; the strings stay the caller's)
    if (!sw_has_command(c->name)) return REG_NOT_ADDED;
    if (g_nown < MAX_OWN) g_own[g_nown++] = c->name;
    return REG_ADDED;
}

// ---- the commands of mods loaded while the game runs. Savage Wind can't remove a command and keeps pointers to its
// strings, so modhost registers its own copy of the name and help with cmd_hot as the function. cmd_hot runs the mod's
// function while the mod is loaded. After modunload the name stays (Savage Wind's `help` lists it), answers that the
// mod is gone, and goes back to work when a mod registers it again.

#define MAX_HOT_CMDS 64
typedef struct {
    char name[64], args[128], help[256];  // Savage Wind's table points at these: they never move
    SwModCommandFn fn;                     // NULL: its mod was unloaded
    int mod;                               // the owner's id, 0: none
} HotCommand;
static HotCommand g_hot_cmds[MAX_HOT_CMDS];
static int g_nhot_cmds;

static HotCommand *hot_cmd(const char *name) {
    for (int i = 0; i < g_nhot_cmds; i++)
        if (!strcmp(g_hot_cmds[i].name, name)) return &g_hot_cmds[i];
    return NULL;
}

// (runs on the frame thread, like modload and modunload: a mod can't be unloaded while its command runs)
static void cmd_hot(int argc, char **argv, ControlReply *r) {
    HotCommand *e = argc > 0 ? hot_cmd(argv[0]) : NULL;
    Mod *m = e && e->fn ? mod_by_id(e->mod) : NULL;
    if (!m) {
        control_fail(r, "%s: the mod that added this command is not loaded any more (load it again with modload)",
                     argc > 0 ? argv[0] : "?");
        return;
    }
    Mod *was = g_cmd_mod;
    g_ctl_tid = GetCurrentThreadId();
    g_cmd_mod = m;
    e->fn(argc, argv, (SwModReply *)r);
    g_cmd_mod = was;
}

static RegResult register_hot(Mod *m, const SwModCommand *c, const char **why) {
    HotCommand *e = hot_cmd(c->name);
    if (e && e->mod) return REG_TAKEN;  // (a loaded mod has it, maybe this one)
    if (!e) {
        if (strlen(c->name) >= sizeof e->name) {
            *why = "the name is too long (63 characters at most)";
            return REG_NOT_ADDED;
        }
        if (g_nhot_cmds == MAX_HOT_CMDS) {
            *why = "mods loaded while the game runs have added 64 command names: restart the game";
            return REG_NOT_ADDED;
        }
        e = &g_hot_cmds[g_nhot_cmds];
        memset(e, 0, sizeof *e);
        copy_str(e->name, sizeof e->name, c->name);
    }
    copy_str(e->args, sizeof e->args, c->args ? c->args : "");  // (only the frame thread reads these: no race)
    copy_str(e->help, sizeof e->help, c->help ? c->help : "");
    if (e == &g_hot_cmds[g_nhot_cmds]) {  // a new name: register the stand-in once; it stays
        ControlCmd cc = {e->name, e->args, e->help, cmd_hot, c->flags};
        RegResult rr = register_one(&cc);
        if (rr != REG_ADDED) return rr;  // (the entry isn't kept)
        g_nhot_cmds++;
    }
    e->fn = c->fn;
    e->mod = m->id;
    return REG_ADDED;
}

static int api_control_register(const SwModCommand *cmds, int count) {
    if (!cmds || count <= 0) return 0;
    // The owner: a mod loaded while the game runs whose code or names these are (found from the pointers, never from
    // the return address: a tail call has none), else the mod whose swmod_init or command runs on this thread (NULL:
    // a mod loaded at game start, after its swmod_init: not listed under a mod).
    Mod *caller = calling_mod();
    int added = 0;
    for (int i = 0; i < count; i++) {
        const char *name = cmds[i].name;
        Mod *owner = hot_owner((const void *)cmds[i].fn);
        if (!owner) owner = hot_owner(name);
        if (!owner) owner = hot_owner(cmds);
        Mod *m = owner ? owner : caller;
        const char *who = m ? m->file : "?";
        if (!name || !*name || !cmds[i].fn) {
            host_log("mod %s: a command without a name or function was skipped", who);
            continue;
        }
        const char *why = NULL;
        RegResult rr;
        if (owner && !caller) {
            why = LATE_WHY;
            rr = REG_NOT_ADDED;
        } else if (m && m->hot && m->unloading) {
            why = "the mod is being unloaded";
            rr = REG_NOT_ADDED;
        } else if (m && m->hot) {
            rr = register_hot(m, &cmds[i], &why);
        } else {
            ControlCmd c = {name, cmds[i].args ? cmds[i].args : "", cmds[i].help ? cmds[i].help : "",
                            (ControlFn)(void *)cmds[i].fn, cmds[i].flags};
            rr = register_one(&c);
        }
        if (rr == REG_ADDED) {
            added++;
            if (m) {
                list_add(m->commands, sizeof m->commands, name);
                m->ncommands++;
            }
            continue;
        }
        if (!why)
            why = rr == REG_TAKEN ? "the name is taken" : "Savage Wind didn't add it (its command table may be full)";
        host_log("mod %s: command %s skipped: %s", who, name, why);
        if (m && caller) {  // (only the frame thread writes a mod's lists)
            list_add(m->skipped, sizeof m->skipped, name);
            note(m, "command %s skipped: %s", name, why);
        }
    }
    return added;
}

static void api_reply_str(SwModReply *r, const char *key, const char *value) {
    control_field_str((ControlReply *)r, key, value ? value : "");
}
static void api_reply_int(SwModReply *r, const char *key, int64_t value) {
    control_field_int((ControlReply *)r, key, value);
}
static void api_reply_num(SwModReply *r, const char *key, double value) {
    control_field_num((ControlReply *)r, key, value);
}
static void api_reply_bool(SwModReply *r, const char *key, bool value) {
    control_field_bool((ControlReply *)r, key, value);
}
static void api_reply_text(SwModReply *r, const char *fmt, ...) {
    char buf[1024];
    sw_va_list ap;
    sw_va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    sw_va_end(ap);
    control_text((ControlReply *)r, "%s", buf);
}
static void api_reply_fail(SwModReply *r, const char *fmt, ...) {
    char buf[1024];
    sw_va_list ap;
    sw_va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    sw_va_end(ap);
    control_fail((ControlReply *)r, "%s", buf);
}
static bool api_dev_mode(void) { return control_enabled(); }

// The runtime's dispatch_lookup only works once the game has booted (runtime/dispatch.c dispatch_init); this one
// reads the same tables directly, so it works from swmod_init on.
static SwModGuestFn api_dispatch_lookup(uint64_t addr) {
    if (!g_real) return NULL;
    for (uint32_t k = 0; k < *g_real->nmodules; k++) {
        const GameLibModule *m = &g_real->modules[k];
        uint64_t n = *m->count;
        if (!n || addr < m->addrs[0] || addr > m->addrs[n - 1]) continue;
        uint64_t lo = 0, hi = n;
        while (lo < hi) {
            uint64_t mid = lo + (hi - lo) / 2;
            if (m->addrs[mid] < addr) lo = mid + 1;
            else hi = mid;
        }
        if (lo < n && m->addrs[lo] == addr) return (SwModGuestFn)m->fns[lo];
    }
    return NULL;
}

static uint64_t api_find_site(const char *name) {
    if (!g_real || !name) return 0;
    for (uint32_t k = 0; k < *g_real->nsites; k++)
        if (!strcmp(g_real->sites[k].name, name)) return g_real->sites[k].addr;
    return 0;
}

// ---- hle_override. Savage Wind can't remove an override, and the first one for an address wins
// (runtime/dispatch.c dispatch_slow). For a mod loaded while the game runs, modhost overrides the address once with a
// stand-in (hot_tramp_N) that calls the mod's function while the mod is loaded, and the game's function otherwise.

#define MAX_START_OVERRIDES 64
static struct {
    uint64_t addr;
    int mod;
    bool pending;      // asked for during its mod's swmod_init: sent to Savage Wind only once that init returns 0
    SwModGuestFn fn;
    const char *name;
} g_start_ovr[MAX_START_OVERRIDES];  // the overrides of mods loaded at game start
static int g_nstart_ovr;

#define MAX_HOT_OVERRIDES 32
typedef struct {
    uint64_t addr;
    GuestFn prev;      // the game's function (what ran before the override)
    SwModGuestFn fn;   // the mod's function; NULL once its mod was unloaded
    uint32_t active;   // 1 while fn is set; 0: calls go straight on to prev
    int mod;           // the owner's id, 0: none
    char name[64];     // (Savage Wind keeps a pointer to it)
    Gate gate;
} HotOverride;
static HotOverride g_hot_ovr[MAX_HOT_OVERRIDES];
static int g_nhot_ovr;

static void hot_override_call(HotOverride *e, struct Cpu *c) {
    uint32_t i = gate_enter(&e->gate);
    SwModGuestFn f = __atomic_load_n(&e->fn, __ATOMIC_SEQ_CST);
    if (f) f(c);
    else e->prev(c);
    gate_leave(&e->gate, i);
}

#define HOT_TRAMP(i)                                                   \
    static void hot_tramp_##i(struct Cpu *c) {                         \
        HotOverride *e = &g_hot_ovr[i];                                \
        if (!__atomic_load_n(&e->active, __ATOMIC_ACQUIRE)) {          \
            __attribute__((musttail)) return e->prev(c);               \
        }                                                              \
        hot_override_call(e, c);                                       \
    }
HOT_TRAMP(0) HOT_TRAMP(1) HOT_TRAMP(2) HOT_TRAMP(3) HOT_TRAMP(4) HOT_TRAMP(5) HOT_TRAMP(6) HOT_TRAMP(7)
HOT_TRAMP(8) HOT_TRAMP(9) HOT_TRAMP(10) HOT_TRAMP(11) HOT_TRAMP(12) HOT_TRAMP(13) HOT_TRAMP(14) HOT_TRAMP(15)
HOT_TRAMP(16) HOT_TRAMP(17) HOT_TRAMP(18) HOT_TRAMP(19) HOT_TRAMP(20) HOT_TRAMP(21) HOT_TRAMP(22) HOT_TRAMP(23)
HOT_TRAMP(24) HOT_TRAMP(25) HOT_TRAMP(26) HOT_TRAMP(27) HOT_TRAMP(28) HOT_TRAMP(29) HOT_TRAMP(30) HOT_TRAMP(31)
static const HostFn g_tramps[MAX_HOT_OVERRIDES] = {
    hot_tramp_0,  hot_tramp_1,  hot_tramp_2,  hot_tramp_3,  hot_tramp_4,  hot_tramp_5,  hot_tramp_6,  hot_tramp_7,
    hot_tramp_8,  hot_tramp_9,  hot_tramp_10, hot_tramp_11, hot_tramp_12, hot_tramp_13, hot_tramp_14, hot_tramp_15,
    hot_tramp_16, hot_tramp_17, hot_tramp_18, hot_tramp_19, hot_tramp_20, hot_tramp_21, hot_tramp_22, hot_tramp_23,
    hot_tramp_24, hot_tramp_25, hot_tramp_26, hot_tramp_27, hot_tramp_28, hot_tramp_29, hot_tramp_30, hot_tramp_31,
};

static void hot_override(Mod *m, uint64_t addr, const char *a, SwModGuestFn fn, const char *name) {
    const char *why = NULL;
    char buf[256];
    HotOverride *e = NULL;
    for (int k = 0; k < g_nhot_ovr; k++)
        if (g_hot_ovr[k].addr == addr) e = &g_hot_ovr[k];
    reg_lock();
    for (int k = 0; k < g_nstart_ovr && !why; k++)
        if (g_start_ovr[k].addr == addr) {
            const Mod *o = mod_by_id(g_start_ovr[k].mod);
            snprintf(buf, sizeof buf, "%s (loaded at game start) overrides it already, and the first override wins",
                     o ? o->file : "a mod");
            why = buf;
        }
    reg_unlock();
    if (!why && m->unloading) why = "the mod is being unloaded";
    if (!why && e && e->mod && e->mod != m->id) {
        const Mod *o = mod_by_id(e->mod);
        snprintf(buf, sizeof buf, "%s overrides it already", o ? o->name : "another mod");
        why = buf;
    }
    if (!why && e && e->gate.dirty && !gate_sync(&e->gate, deadline()))
        why = "a game thread still runs the override that was there before: try again";
    if (!why && !e && g_nhot_ovr == MAX_HOT_OVERRIDES)
        why = "mods loaded while the game runs have overridden 32 addresses: restart the game";
    GuestFn prev = e ? e->prev : (GuestFn)api_dispatch_lookup(addr);
    if (!why && !prev) why = "no game function starts there";
    if (why) {
        host_log("mod %s: hle_override %s refused: %s", m->file, a, why);
        note(m, "hle_override %s refused: %s", a, why);
        return;
    }
    if (!e) {
        e = &g_hot_ovr[g_nhot_ovr];
        memset(e, 0, sizeof *e);
        e->addr = addr;
        e->prev = prev;
        copy_str(e->name, sizeof e->name, name ? name : "mod");
    }
    __atomic_store_n(&e->fn, fn, __ATOMIC_SEQ_CST);
    e->mod = m->id;
    __atomic_store_n(&e->active, 1, __ATOMIC_SEQ_CST);
    if (e == &g_hot_ovr[g_nhot_ovr]) {  // a new address: the stand-in goes in once and stays
        hle_override(addr, g_tramps[g_nhot_ovr], e->name);
        g_nhot_ovr++;
    }
    if (!list_has(m->overrides, a)) list_add(m->overrides, sizeof m->overrides, a);
}

static void api_hle_override(uint64_t addr, SwModGuestFn fn, const char *name) {
    if (!fn) return;
    // The owner: a mod loaded while the game runs whose function or name this is (from the pointers, never from the
    // return address: a tail call has none), else the mod whose swmod_init or command runs on this thread.
    Mod *caller = calling_mod();
    Mod *owner = hot_owner((const void *)fn);
    if (!owner) owner = hot_owner(name);  // (Savage Wind keeps the name's pointer too)
    char a[24];
    snprintf(a, sizeof a, "%#llx", (unsigned long long)addr);
    if (owner && !caller) {  // a hook, a thread of its own or swmod_exit: it would escape modunload
        host_log("mod %s: hle_override %s refused: %s", owner->file, a, LATE_WHY);
        return;
    }
    Mod *m = owner ? owner : caller;
    if (m && m->hot) {
        hot_override(m, addr, a, fn, name);
        return;
    }
    // During its swmod_init it is only queued: Savage Wind can't take an override back, so it goes in once the init
    // returned 0 (start_overrides_done), and a failed init leaves nothing running.
    bool queue = m && m == g_current && GetCurrentThreadId() == g_current_tid;
    reg_lock();  // (a mod loaded at game start may call from a game thread)
    if (g_nstart_ovr < MAX_START_OVERRIDES) {
        g_start_ovr[g_nstart_ovr].addr = addr;
        g_start_ovr[g_nstart_ovr].pending = queue;
        g_start_ovr[g_nstart_ovr].fn = fn;
        g_start_ovr[g_nstart_ovr].name = name;
        g_start_ovr[g_nstart_ovr++].mod = m ? m->id : 0;
    } else {
        queue = false;  // (no room to queue it: it goes in now, as before)
    }
    if (m && !list_has(m->overrides, a)) list_add(m->overrides, sizeof m->overrides, a);
    reg_unlock();
    if (!queue) hle_override(addr, (HostFn)fn, name ? name : "mod");
}

// A mod loaded at game start finished its swmod_init: its queued overrides go in (ok), or are dropped (it failed).
static void start_overrides_done(Mod *m, bool ok) {
    reg_lock();
    int j = 0;
    for (int k = 0; k < g_nstart_ovr; k++) {
        if (g_start_ovr[k].pending && g_start_ovr[k].mod == m->id) {
            if (!ok) continue;
            g_start_ovr[k].pending = false;
            hle_override(g_start_ovr[k].addr, (HostFn)g_start_ovr[k].fn,
                         g_start_ovr[k].name ? g_start_ovr[k].name : "mod");
        }
        g_start_ovr[j++] = g_start_ovr[k];
    }
    g_nstart_ovr = j;
    if (!ok) m->overrides[0] = 0;
    reg_unlock();
}

static void api_guest_call(struct Cpu *c, uint64_t addr) {
    if (!c) return;
    if (!addr) {  // (Savage Wind stops the game with FATAL for an address with no function: refuse it here)
        Mod *m = calling_mod();
        host_log("mod %s: guest_call to address 0 refused (a site or pattern that wasn't found?)",
                 m ? m->file : "?");
        return;
    }
    guest_call(c, addr);
}
static struct Cpu *api_current_cpu(void) { return kernel_current_cpu(); }

// ---- hooks (API version 2)

static int run_list(struct Cpu *c, SwHookSlot *s, const HookList *h) {
    for (int i = 0; i < h->nbefore; i++)
        if (h->before[i](c)) return 1;  // skipped: the hook set the result
    if (!h->replace && !h->nafter) return 0;  // the wrapper runs the function (a tail call: no extra stack)
    if (h->replace) h->replace(c, (SwModGuestFn)s->orig);
    else s->orig(c);
    for (int i = 0; i < h->nafter; i++) h->after[i](c);
    return 1;
}

// Runs in place of a hooked function (called by its wrapper in the game code, on a game thread). Hooks of mods loaded
// at game start only: nothing to count. Hooks of mods loaded later: the call counts itself in the gate.
#ifdef MODHOST_SELFTEST_PAUSE
// Self-test build only (tools/build_native.py selftest): a pause where the OS could stop a game thread, between the
// has_hot check and gate_enter, so that the self-test can unload a mod while a thread waits there.
static uint32_t g_test_pause_ms;
GAMELIB_EXPORT void modhost_test_pause(uint32_t ms) { __atomic_store_n(&g_test_pause_ms, ms, __ATOMIC_SEQ_CST); }
#define TEST_PAUSE()                                                         \
    do {                                                                     \
        uint32_t p_ = __atomic_load_n(&g_test_pause_ms, __ATOMIC_SEQ_CST);  \
        if (p_) Sleep(p_);                                                   \
    } while (0)
#else
#define TEST_PAUSE() ((void)0)
#endif

static int hook_entry(struct Cpu *c, SwHookSlot *s) {
    HookState *st = (HookState *)s->user;
    if (!st) return 0;
    if (!__atomic_load_n(&st->has_hot, __ATOMIC_ACQUIRE)) return run_list(c, s, &st->plain);
    // A thread here isn't counted yet, so an unload can finish meanwhile: what it runs is only read below, after
    // gate_enter (publish points pub at the plain list before it clears has_hot).
    TEST_PAUSE();
    uint32_t i = gate_enter(&st->gate);
    int r = run_list(c, s, __atomic_load_n(&st->pub, __ATOMIC_SEQ_CST));
    gate_leave(&st->gate, i);
    return r;
}

static SwHookSlot *find_slot(uint64_t addr) {
    if (!g_hooks) return NULL;
    for (uint32_t i = 0; i < g_hooks->count; i++)
        if (g_hooks->slots[i].addr == addr) return &g_hooks->slots[i];
    return NULL;
}

static bool api_can_hook(uint64_t addr) { return find_slot(addr) != NULL; }

static bool has_hot_regs(const HookState *st, int skip) {
    for (int k = 0; k < st->nregs; k++)
        if (st->regs[k].hot && st->regs[k].mod != skip) return true;
    return false;
}

static bool has_regs_of(const HookState *st, int mod) {
    for (int k = 0; k < st->nregs; k++)
        if (st->regs[k].mod == mod) return true;
    return false;
}

// Publish st's hooks without those of mod `skip` (0: all) for the calls from now on. The list not published must be
// free (the gate not dirty): it is written here.
static void publish(HookState *st, int skip) {
    if (!has_hot_regs(st, skip)) {
        // Calls run the plain list again. pub first: a thread that saw has_hot = 1 just before, and enters the gate
        // only after the unload's wait, must not run the old list (its mod's DLL may be freed by then).
        __atomic_store_n(&st->pub, &st->plain, __ATOMIC_SEQ_CST);
        __atomic_store_n(&st->has_hot, 0, __ATOMIC_SEQ_CST);
        return;
    }
    int next = st->cur ^ 1;
    HookList *h = &st->hot[next];
    memset(h, 0, sizeof *h);
    for (int k = 0; k < st->nregs; k++) {
        const HookReg *g = &st->regs[k];
        if (g->mod == skip) continue;
        if (g->before) h->before[h->nbefore++] = g->before;
        if (g->after) h->after[h->nafter++] = g->after;
        if (g->replace) h->replace = g->replace;
    }
    __atomic_store_n(&st->pub, h, __ATOMIC_SEQ_CST);
    st->cur = next;
    __atomic_store_n(&st->has_hot, 1, __ATOMIC_SEQ_CST);
}

static int api_hook(uint64_t addr, SwModHookBefore before, SwModHookAfter after, SwModHookReplace replace) {
    Mod *m = g_current && GetCurrentThreadId() == g_current_tid ? g_current : NULL;  // (only from swmod_init)
    const char *who = m ? m->file : "?";
    if (!before && !after && !replace) {
        host_log("mod %s: hook %#llx: no before, after or replace given", who, (unsigned long long)addr);
        return SWMOD_HOOK_BAD_ARGS;
    }
    if (!m) {
        host_log("mod %s: hook %#llx refused: hooks can only be added from swmod_init", who, (unsigned long long)addr);
        return SWMOD_HOOK_LATE;
    }
    char a[24];
    snprintf(a, sizeof a, "%#llx", (unsigned long long)addr);
    if (!g_hooks) {
        if (!m->told_no_slots) {
            host_log("mod %s: this game code has no hook slots; rebuild with hooks (docs/hooks.md)", who);
            note(m, "this game code has no hook slots, so no hook runs: rebuild the game code with hooks for "
                    "these functions (python tools/sw_rebuild.py build --hooks <list>, docs/hooks.md)");
        }
        m->told_no_slots = true;
        if (!list_has(m->no_slot, a)) list_add(m->no_slot, sizeof m->no_slot, a);
        return SWMOD_HOOK_NO_SLOTS;
    }
    SwHookSlot *s = find_slot(addr);
    if (!s) {
        host_log("mod %s: no hook slot for %s in this game code; add it to the hooks list and rebuild", who, a);
        note(m, "%s has no hook slot in this game code: that function needs a rebuild of the game code with %s in "
                "the hooks list (docs/hooks.md)", a, a);
        if (!list_has(m->no_slot, a)) list_add(m->no_slot, sizeof m->no_slot, a);
        return SWMOD_HOOK_NO_SLOT;
    }
    HookState *st = (HookState *)s->user;
    if (!st && g_nstates == MAX_HOOKED) {
        host_log("mod %s: hook %s: more than %d functions hooked", who, a, MAX_HOOKED);
        note(m, "hook %s refused: more than %d functions hooked", a, MAX_HOOKED);
        return SWMOD_HOOK_FULL;
    }
    int nbefore = 0, nafter = 0;
    const HookReg *rep = NULL;
    for (int k = 0; st && k < st->nregs; k++) {
        nbefore += st->regs[k].before != NULL;
        nafter += st->regs[k].after != NULL;
        if (st->regs[k].replace) rep = &st->regs[k];
    }
    if (replace && rep) {
        const Mod *o = mod_by_id(rep->mod);
        host_log("mod %s: hook %s: mod %s already replaces it", who, a, o ? o->file : "?");
        note(m, "hook %s refused: mod %s already replaces it", a, o ? o->name : "?");
        return SWMOD_HOOK_TAKEN;
    }
    if ((before && nbefore == MAX_PER_HOOK) || (after && nafter == MAX_PER_HOOK) ||
        (st && st->nregs == MAX_HOOK_REGS)) {
        host_log("mod %s: hook %s: too many hooks on it", who, a);
        note(m, "hook %s refused: too many hooks on it", a);
        return SWMOD_HOOK_FULL;
    }
    if (m->hot && st && st->gate.dirty && !gate_sync(&st->gate, deadline())) {
        host_log("mod %s: hook %s: a game thread still runs its earlier hooks; try again", who, a);
        note(m, "hook %s refused: a game thread still runs its earlier hooks: try again", a);
        return SWMOD_HOOK_BUSY;
    }
    if (!st) {
        st = &g_states[g_nstates++];
        memset(st, 0, sizeof *st);
        s->user = st;  // (before entry is set: the wrapper reads it after it sees entry)
    }
    st->regs[st->nregs++] = (HookReg){m->id, m->hot, before, after, replace};
    if (m->hot) {  // game threads may run this function right now: publish a new list
        // No wait here: swmod_init runs on the frame thread, and a game thread inside this function may be waiting
        // for that thread (a frame). The old list may still be in use, so it is marked dirty: the next publish or
        // unload waits for it first. (The plain list is never rewritten, so leaving it needs no wait.)
        bool was_hot = __atomic_load_n(&st->has_hot, __ATOMIC_SEQ_CST) != 0;
        publish(st, 0);
        if (was_hot) st->gate.dirty = true;
    } else {       // mods loaded at game start: no game code runs yet, so the list is changed in place
        if (before) st->plain.before[st->plain.nbefore++] = before;
        if (after) st->plain.after[st->plain.nafter++] = after;
        if (replace) st->plain.replace = replace;
    }
    __atomic_store_n(&s->entry, hook_entry, __ATOMIC_RELEASE);  // (the list is complete first)
    if (!list_has(m->hooks, a)) list_add(m->hooks, sizeof m->hooks, a);
    m->nhooks++;
    return SWMOD_HOOK_OK;
}

// Stand-ins for the hooks of a switched-off mod in a list a game thread may still run (they do nothing).
static bool off_before(struct Cpu *c) { (void)c; return false; }
static void off_after(struct Cpu *c) { (void)c; }
static void off_replace(struct Cpu *c, SwModGuestFn orig) { orig(c); }

// Points every entry of h that is one of g's functions at a stand-in (one pointer store each: a thread reading the
// list sees the old function or the stand-in, and both stay loaded).
static void off_in_list(HookList *h, const HookReg *g) {
    for (int i = 0; i < h->nbefore; i++)
        if (g->before && h->before[i] == g->before) __atomic_store_n(&h->before[i], off_before, __ATOMIC_SEQ_CST);
    for (int i = 0; i < h->nafter; i++)
        if (g->after && h->after[i] == g->after) __atomic_store_n(&h->after[i], off_after, __ATOMIC_SEQ_CST);
    if (g->replace && h->replace == g->replace) __atomic_store_n(&h->replace, off_replace, __ATOMIC_SEQ_CST);
}

// A mod whose swmod_init failed: take its hooks (and, for a mod loaded while the game runs, its overrides and
// commands) out without waiting. The DLL stays loaded. A list a game thread may still run isn't rewritten (only its
// entries of this mod become stand-ins) and is marked dirty, so a later publish or modunload waits for it first.
static void switch_off(Mod *m) {
    for (int k = 0; k < g_nstates; k++) {
        HookState *st = &g_states[k];
        if (!has_regs_of(st, m->id)) continue;
        if (m->hot)
            for (int i = 0; i < st->nregs; i++)
                if (st->regs[i].mod == m->id) {
                    off_in_list(&st->hot[0], &st->regs[i]);
                    off_in_list(&st->hot[1], &st->regs[i]);
                }
        int j = 0;
        for (int i = 0; i < st->nregs; i++)
            if (st->regs[i].mod != m->id) st->regs[j++] = st->regs[i];
        st->nregs = j;
        if (m->hot) {  // game threads may run it right now
            if (!has_hot_regs(st, 0)) publish(st, 0);  // (back to the plain list: no hot list is rewritten)
            st->gate.dirty = true;
        } else {       // loaded at game start: no game code runs yet, so the plain list is rebuilt in place
            memset(&st->plain, 0, sizeof st->plain);
            for (int i = 0; i < st->nregs; i++) {
                const HookReg *g = &st->regs[i];
                if (g->before) st->plain.before[st->plain.nbefore++] = g->before;
                if (g->after) st->plain.after[st->plain.nafter++] = g->after;
                if (g->replace) st->plain.replace = g->replace;
            }
        }
        if (!j) {  // no hooks left: calls go straight on to the function again
            for (uint32_t i = 0; g_hooks && i < g_hooks->count; i++)
                if (g_hooks->slots[i].user == st) __atomic_store_n(&g_hooks->slots[i].entry, NULL, __ATOMIC_RELEASE);
        }
    }
    for (int k = 0; k < g_nhot_ovr; k++)
        if (g_hot_ovr[k].mod == m->id) {
            __atomic_store_n(&g_hot_ovr[k].active, 0, __ATOMIC_SEQ_CST);
            __atomic_store_n(&g_hot_ovr[k].fn, NULL, __ATOMIC_SEQ_CST);
            g_hot_ovr[k].mod = 0;
            g_hot_ovr[k].gate.dirty = true;
        }
    for (int k = 0; k < g_nhot_cmds; k++)
        if (g_hot_cmds[k].mod == m->id) {
            g_hot_cmds[k].fn = NULL;
            g_hot_cmds[k].mod = 0;
        }
    m->hooks[0] = 0;
    m->overrides[0] = 0;
    m->nhooks = 0;
    m->switched_off = true;
}

// The hook slots of the loaded game code, if it was built with them.
static void load_hooks(void) {
    SwHookTableFn fn = (SwHookTableFn)GetProcAddress(g_real_h, SWHOOK_EXPORT);
    if (!fn) return;  // (no hook slots: said when a mod asks for a hook)
    const SwHookTable *t = fn();
    if (!t || t->abi != SWHOOK_ABI || (t->count && !t->slots)) {
        host_log("the game code's hook slots are of another kind (%u); hooks are off", t ? t->abi : 0u);
        return;
    }
    g_hooks = t;
    host_log("game code has %u hook slot%s", t->count, t->count == 1 ? "" : "s");
}

// ------------------------------------------------------------------ mod settings (API version 3): settings.c

static void join_args(int from, int argc, char **argv, char *out, size_t cap);
#include "settings.c"

// ------------------------------------------------------------------ the overlay host (swmod_overlay.h): overlay.c
// (mods draw over the game with Savage Wind's ImGui; an export of modhost.dll, nothing is patched until one asks)
#include "overlay.c"

static void fill_api(SwModApi *a, const char *mod_name) {
    memset(a, 0, sizeof *a);
    a->api_version = SWMOD_API_VERSION;
    a->size = (uint32_t)sizeof *a;
    a->host_version = MODHOST_VERSION;
    a->game_version = g_real && g_real->version ? g_real->version : "";
    a->mod_name = mod_name;
    a->log = api_log;
    a->control_register = api_control_register;
    a->reply_str = api_reply_str;
    a->reply_int = api_reply_int;
    a->reply_num = api_reply_num;
    a->reply_bool = api_reply_bool;
    a->reply_text = api_reply_text;
    a->reply_fail = api_reply_fail;
    a->dev_mode = api_dev_mode;
    a->dispatch_lookup = api_dispatch_lookup;
    a->find_site = api_find_site;
    a->hle_override = api_hle_override;
    a->guest_call = api_guest_call;
    a->current_cpu = api_current_cpu;
    a->hook = api_hook;
    a->can_hook = api_can_hook;
    a->mod_id = NULL;  // (st_attach sets it once the mod's file name is known)
    a->setting_int = api_setting_int;
    a->setting_num = api_setting_num;
    a->setting_str = api_setting_str;
    a->setting_watch = api_setting_watch;
    a->setting_set = api_setting_set;
    a->settings_ui = api_settings_ui;
}

// ------------------------------------------------------------------ unloading (mods loaded while the game runs)

// Take out everything mod m added, wait until no game thread runs its code, call its swmod_exit (call_exit) and free
// its DLL; the slot is free afterwards. false: a game thread still runs one of its hooks or overrides; everything
// was put back as it was and why says so.
static bool unload_mod(Mod *m, bool call_exit, char *why, size_t cap) {
    uint64_t until = deadline();
    m->unloading = true;  // from now on it can't add anything (hot_override and register_hot refuse it)
    HookState *sts[MAX_HOOKED];
    struct {
        int cur;
        uint32_t has_hot;
    } was[MAX_HOOKED];
    int n = 0;
    for (int k = 0; k < g_nstates; k++)
        if (has_regs_of(&g_states[k], m->id)) sts[n++] = &g_states[k];
    HotOverride *ovs[MAX_HOT_OVERRIDES];
    SwModGuestFn ov_fn[MAX_HOT_OVERRIDES];
    int no = 0;
    for (int k = 0; k < g_nhot_ovr; k++)
        if (g_hot_ovr[k].mod == m->id) ovs[no++] = &g_hot_ovr[k];

    // 1. something replaced earlier may still be in use: wait for that first (nothing is changed yet)
    bool ok = true;
    if (m->switched_off) {  // (its lists were taken out without a wait: every dirty one may still run its code)
        for (int k = 0; k < g_nstates && ok; k++)
            if (g_states[k].gate.dirty) ok = gate_sync(&g_states[k].gate, until);
        for (int k = 0; k < g_nhot_ovr && ok; k++)
            if (g_hot_ovr[k].gate.dirty) ok = gate_sync(&g_hot_ovr[k].gate, until);
    }
    for (int k = 0; k < n && ok; k++)
        if (sts[k]->gate.dirty) ok = gate_sync(&sts[k]->gate, until);
    for (int k = 0; k < no && ok; k++)
        if (ovs[k]->gate.dirty) ok = gate_sync(&ovs[k]->gate, until);
    // 2. take its hooks and overrides out, then wait until no game thread runs them any more
    if (ok) {
        for (int k = 0; k < n; k++) {
            was[k].cur = sts[k]->cur;
            was[k].has_hot = sts[k]->has_hot;
            publish(sts[k], m->id);
        }
        for (int k = 0; k < no; k++) {
            ov_fn[k] = ovs[k]->fn;
            __atomic_store_n(&ovs[k]->active, 0, __ATOMIC_SEQ_CST);
            __atomic_store_n(&ovs[k]->fn, NULL, __ATOMIC_SEQ_CST);
        }
        for (int k = 0; k < n; k++) ok &= gate_sync(&sts[k]->gate, until);
        for (int k = 0; k < no; k++) ok &= gate_sync(&ovs[k]->gate, until);
        if (ok) ok = st_detach_watchers(m, until);  // (its setting watchers; put back by itself on a timeout)
        if (!ok) {  // put them back: the old lists weren't changed (and stay dirty until a later wait succeeds)
            for (int k = 0; k < n; k++) {
                __atomic_store_n(&sts[k]->pub, &sts[k]->hot[was[k].cur], __ATOMIC_SEQ_CST);
                sts[k]->cur = was[k].cur;
                __atomic_store_n(&sts[k]->has_hot, was[k].has_hot, __ATOMIC_SEQ_CST);
                sts[k]->gate.dirty = true;
            }
            for (int k = 0; k < no; k++) {
                __atomic_store_n(&ovs[k]->fn, ov_fn[k], __ATOMIC_SEQ_CST);
                __atomic_store_n(&ovs[k]->active, 1, __ATOMIC_SEQ_CST);
                ovs[k]->gate.dirty = true;
            }
        }
    }
    if (!ok) {
        m->unloading = false;
        snprintf(why, cap, "%s is still in use: a game thread kept running one of its hooks or overrides for %u ms. "
                 "Nothing changed. Try again; if it keeps failing, restart the game to replace this mod",
                 m->name, g_wait_ms);
        host_log("modunload %s (%s): refused: a game thread still runs one of its hooks or overrides", m->name,
                 m->file);
        return false;
    }

    // 3. no game thread runs its code: forget what it added
    for (int k = 0; k < n; k++) {
        HookState *st = sts[k];
        int j = 0;
        for (int i = 0; i < st->nregs; i++)
            if (st->regs[i].mod != m->id) st->regs[j++] = st->regs[i];
        st->nregs = j;
        if (!j) {  // no hooks left: calls go straight on to the function again
            for (uint32_t i = 0; g_hooks && i < g_hooks->count; i++)
                if (g_hooks->slots[i].user == st) __atomic_store_n(&g_hooks->slots[i].entry, NULL, __ATOMIC_RELEASE);
        }
    }
    for (int k = 0; k < g_nhot_ovr; k++)  // (every one it owns, not only those it had when the unload started)
        if (g_hot_ovr[k].mod == m->id) {
            __atomic_store_n(&g_hot_ovr[k].active, 0, __ATOMIC_SEQ_CST);
            __atomic_store_n(&g_hot_ovr[k].fn, NULL, __ATOMIC_SEQ_CST);
            g_hot_ovr[k].mod = 0;
        }
    for (int k = 0; k < g_nhot_cmds; k++)
        if (g_hot_cmds[k].mod == m->id) {
            g_hot_cmds[k].fn = NULL;
            g_hot_cmds[k].mod = 0;
        }
    st_forget(m);
    if (call_exit && m->ok) {  // (not for a mod whose swmod_init failed)
        SwModExitFn ex = (SwModExitFn)GetProcAddress(m->h, SWMOD_EXIT_NAME);
        if (ex) ex(&m->api);
    }
    if (!FreeLibrary(m->h)) host_log("modunload %s: FreeLibrary failed (error %lu)", m->name, GetLastError());
    host_log("modunload %s (%s): unloaded", m->name, m->file);
    memset(m, 0, sizeof *m);  // (the slot is free)
    return true;
}

// ------------------------------------------------------------------ loading

static HMODULE own_module(void) {
    HMODULE h = NULL;
    GetModuleHandleExW(SW_MODULE_FROM_ADDRESS | SW_MODULE_KEEP_REFCOUNT, (const wchar_t *)(void *)&own_module, &h);
    return h;
}

static Mod *free_slot(void) {
    for (int i = 0; i < MAX_MODS; i++)
        if (!g_mods[i].used) return &g_mods[i];
    return NULL;
}

// A new mod in slot m: its id, names and API.
static void start_mod(Mod *m, const char *file, const char *name, bool hot) {
    memset(m, 0, sizeof *m);
    m->used = true;
    m->hot = hot;
    m->id = g_next_id++;
    copy_str(m->file, sizeof m->file, file);
    copy_str(m->name, sizeof m->name, name);
    fill_api(&m->api, m->name);
    st_attach(m);
}

static void log_loaded(const char *prefix, const Mod *m) {
    host_log("%s%s: %s%s%s%s%s%s%s", prefix, m->file, m->status, m->ncommands ? ", commands: " : "", m->commands,
             *m->skipped ? ", skipped: " : "", m->skipped, *m->hooks ? ", hooks: " : "", m->hooks);
}

// The name of a mod loaded while the game runs: its file name without ".dll", and without the "-<number>" the editor
// adds to each build's copy (mymod-3.dll is mymod), so that the mod keeps its name across modreload.
static void hot_name(const char *file, char *out, size_t cap) {
    copy_str(out, cap, file);
    size_t n = strlen(out);
    if (n > 4 && same_name(out + n - 4, ".dll")) out[n -= 4] = 0;
    size_t k = n;
    while (k > 0 && out[k - 1] >= '0' && out[k - 1] <= '9') k--;
    if (k < n && k > 1 && out[k - 1] == '-') out[k - 1] = 0;
}

// A mod DLL that modload or modreload checked and loaded, before its swmod_init runs.
typedef struct {
    wchar_t full[PATH_W];
    char upath[PATH_U];  // the full path (UTF-8)
    char file[PATH_U];   // its file name
    char name[256];      // the mod's name
    HMODULE h;           // NULL: the same file as the mod it replaces (load it again once that one is unloaded)
    SwModInitFn init;
} HotFile;

// Load hf's DLL and find its swmod_init. replacing: the mod modreload replaces (its DLL may be this file). false with
// err set: nothing stays loaded.
static bool load_hot_dll(HotFile *hf, const Mod *replacing, char *err, size_t cap) {
    hf->h = LoadLibraryExW(hf->full, NULL, SW_LOAD_SEARCH_DLL_DIR | SW_LOAD_SEARCH_DEFAULT_DIRS);
    if (!hf->h) {
        DWORD e = GetLastError();
        snprintf(err, cap, "%s: load failed (error %lu%s)", hf->file, e,
                 e == 193 ? ": not a 64-bit Windows DLL" : e == 126 ? ": a DLL it needs is missing" : "");
        host_log("modload %s", err);
        return false;
    }
    const Mod *twin = NULL;
    for (int i = 0; i < MAX_MODS; i++)
        if (g_mods[i].used && g_mods[i].h == hf->h) twin = &g_mods[i];
    if (twin && twin == replacing) {  // (modreload of the file that is loaded: load it again after the unload)
        FreeLibrary(hf->h);  // (only drops the count this LoadLibrary added)
        hf->h = NULL;
        return true;
    }
    if (twin || hf->h == own_module() || hf->h == g_real_h) {  // (LoadLibrary gave the module that is in memory)
        snprintf(err, cap, "%s is in use already (%s): copy the DLL to a new file name and load that", hf->file,
                 twin ? twin->name : "it is modhost or the game code");
        FreeLibrary(hf->h);
        hf->h = NULL;
        return false;
    }
    hf->init = (SwModInitFn)GetProcAddress(hf->h, SWMOD_INIT_NAME);
    if (!hf->init) {
        snprintf(err, cap, "%s has no swmod_init: it isn't a mod (swmod.h)", hf->file);
        FreeLibrary(hf->h);
        hf->h = NULL;
        return false;
    }
    return true;
}

// Check the mod DLL at path (UTF-8) and load it, without calling its swmod_init yet, so that modreload can refuse
// before it unloads the build that works. name: the mod's name (NULL: from the file name). replacing: the mod that
// modreload replaces (NULL for modload). split: the path came in more than one word (it wasn't in quotes).
static bool open_hot(const char *path, const char *name, const Mod *replacing, bool split, HotFile *hf, char *err,
                     size_t cap) {
    memset(hf, 0, sizeof *hf);
    wchar_t given[PATH_W];
    if (!MultiByteToWideChar(SW_CP_UTF8, 0, path, -1, given, PATH_W) || !full_path(given, hf->full, PATH_W)) {
        snprintf(err, cap, "the path is too long or not valid: %s", path);
        return false;
    }
    to_utf8(hf->full, hf->upath, sizeof hf->upath);
    const char *file = hf->upath;
    for (const char *p = hf->upath; *p; p++)
        if (*p == '\\' || *p == '/') file = p + 1;
    copy_str(hf->file, sizeof hf->file, file);
    size_t n = strlen(file);
    if (n < 5 || !same_name(file + n - 4, ".dll")) {
        snprintf(err, cap, "not a .dll file: %s", hf->upath);
        return false;
    }
    DWORD attrs = GetFileAttributesW(hf->full);
    if (attrs == SW_INVALID_ATTRIBUTES || (attrs & SW_ATTR_DIRECTORY)) {
        snprintf(err, cap, "there is no file %s%s", hf->upath,
                 split ? " (a path with spaces must be in double quotes: \"C:\\my mods\\x.dll\")" : "");
        return false;
    }
    if (name) copy_str(hf->name, sizeof hf->name, name);
    else hot_name(file, hf->name, sizeof hf->name);
    if (!*hf->name) {
        snprintf(err, cap, "the mod needs a name");
        return false;
    }
    const Mod *same = mod_by_name(hf->name);
    if (same && same != replacing && !same->hot) {
        snprintf(err, cap, "a mod named %s was loaded at game start (%s): restart the game to replace this mod",
                 hf->name, same->file);
        return false;
    }
    if (same && same != replacing) {
        snprintf(err, cap, "a mod named %s is already loaded: use modreload %s <path> to replace it", hf->name,
                 hf->name);
        return false;
    }
    if (!replacing && !free_slot()) {
        snprintf(err, cap, "%d mods are loaded already: unload one first", MAX_MODS);
        return false;
    }
    return load_hot_dll(hf, replacing, err, cap);
}

// Call the swmod_init of a DLL open_hot loaded (in a free slot). Returns the mod, or NULL with err set (nothing stays
// loaded then, unless err says so).
static Mod *init_hot(HotFile *hf, char *err, size_t cap) {
    if (!hf->h && !load_hot_dll(hf, NULL, err, cap)) return NULL;  // (the file the replaced mod had: it is free now)
    Mod *m = free_slot();
    if (!m) {
        snprintf(err, cap, "%d mods are loaded already: unload one first", MAX_MODS);
        FreeLibrary(hf->h);
        return NULL;
    }
    const char *want = hf->name;
    start_mod(m, hf->file, want, true);
    copy_str(m->path, sizeof m->path, hf->upath);
    m->h = hf->h;
    g_ctl_tid = GetCurrentThreadId();
    g_current_tid = g_ctl_tid;
    g_current = m;
    int rc = hf->init(&m->api);
    g_current = NULL;
    m->ok = rc == 0;
    if (m->ok) {
        copy_str(m->status, sizeof m->status, "ok");
        char prefix[320];
        snprintf(prefix, sizeof prefix, "modload %s (id %d) ", m->name, m->id);
        log_loaded(prefix, m);
        return m;
    }
    snprintf(m->status, sizeof m->status, "init failed (code %d)", rc);
    char lines[1024], why[512];
    copy_str(lines, sizeof lines, m->notes);
    for (char *p = lines; *p; p++)
        if (*p == '\n') *p = ' ';
    if (unload_mod(m, false, why, sizeof why)) {  // (no swmod_exit: its init failed)
        snprintf(err, cap, "%s: swmod_init failed (code %d), so it was unloaded again.%s%s", want, rc, *lines ? " " : "",
                 lines);
        host_log("modload %s: swmod_init failed (code %d); unloaded again", want, rc);
        return NULL;
    }
    switch_off(m);  // (it can't be freed yet, but nothing of it runs any more)
    snprintf(m->status, sizeof m->status, "init failed (code %d), switched off, still loaded", rc);
    snprintf(err, cap, "%s: swmod_init failed (code %d): its hooks and overrides were switched off, but it can't be "
             "unloaded yet: %s", want, rc, why);
    host_log("modload %s: swmod_init failed (code %d); switched off, still loaded", want, rc);
    return NULL;
}

// The words of argv from `from` on, joined with single spaces (a path the client didn't quote; two spaces in a row
// or a tab can't come back this way, so the replies ask for quotes).
static void join_args(int from, int argc, char **argv, char *out, size_t cap) {
    out[0] = 0;
    for (int i = from; i < argc; i++) {
        size_t used = strlen(out);
        snprintf(out + used, cap - used, "%s%s", i > from ? " " : "", argv[i]);
    }
}

static void reply_loaded(ControlReply *r, const Mod *m, bool replaced) {
    control_field_str(r, "name", m->name);
    control_field_int(r, "id", m->id);
    control_field_str(r, "file", m->file);
    control_field_str(r, "path", m->path);
    control_field_bool(r, "replaced", replaced);
    control_field_str(r, "commands", m->commands);
    control_field_str(r, "skipped", m->skipped);
    control_field_str(r, "hooks", m->hooks);
    control_field_str(r, "no_slot", m->no_slot);
    control_field_str(r, "overrides", m->overrides);
    control_field_str(r, "notes", m->notes);
    control_text(r, "%s %s (id %d)%s%s%s%s%s%s", m->name, replaced ? "reloaded" : "loaded", m->id,
                 *m->commands ? ", commands: " : "", m->commands, *m->hooks ? ", hooks: " : "", m->hooks,
                 *m->overrides ? ", overrides: " : "", m->overrides);
    char lines[sizeof m->notes];
    copy_str(lines, sizeof lines, m->notes);
    for (char *p = lines; *p;) {
        char *e = strchr(p, '\n');
        if (e) *e = 0;
        control_text(r, "%s", p);
        if (!e) break;
        p = e + 1;
    }
}

static void cmd_modload(int argc, char **argv, ControlReply *r) {
    g_ctl_tid = GetCurrentThreadId();
    if (argc < 2) {
        control_fail(r, "usage: modload \"<full path to the mod's .dll>\" (in double quotes when it has spaces)");
        return;
    }
    char path[PATH_U], err[2048];
    join_args(1, argc, argv, path, sizeof path);
    HotFile hf;
    Mod *m = open_hot(path, NULL, NULL, argc > 2, &hf, err, sizeof err) ? init_hot(&hf, err, sizeof err) : NULL;
    if (!m) {
        control_fail(r, "%s", err);
        return;
    }
    reply_loaded(r, m, false);
}

static void cmd_modunload(int argc, char **argv, ControlReply *r) {
    g_ctl_tid = GetCurrentThreadId();
    if (argc != 2) {
        control_fail(r, "usage: modunload <name> (the command mods lists the names)");
        return;
    }
    Mod *m = mod_by_name(argv[1]);
    if (!m) {
        control_fail(r, "no mod named %s is loaded (the command mods lists them)", argv[1]);
        return;
    }
    if (!m->hot) {
        control_fail(r, "%s was loaded at game start (%s): restart the game to replace this mod", m->name, m->file);
        return;
    }
    char name[256], why[512];
    copy_str(name, sizeof name, m->name);
    int id = m->id;
    if (!unload_mod(m, true, why, sizeof why)) {
        control_fail(r, "%s", why);
        return;
    }
    control_field_str(r, "name", name);
    control_field_int(r, "id", id);
    control_field_bool(r, "unloaded", true);
    control_text(r, "%s unloaded", name);
}

static void cmd_modreload(int argc, char **argv, ControlReply *r) {
    g_ctl_tid = GetCurrentThreadId();
    if (argc < 3) {
        control_fail(r, "usage: modreload <name> \"<full path to the new .dll>\" (in double quotes when it has spaces)");
        return;
    }
    char name[256], path[PATH_U], why[512], err[2048];
    copy_str(name, sizeof name, argv[1]);
    join_args(2, argc, argv, path, sizeof path);
    Mod *old = mod_by_name(name);
    if (old && !old->hot) {
        control_fail(r, "%s was loaded at game start (%s): restart the game to replace this mod", old->name,
                     old->file);
        return;
    }
    if (old) copy_str(name, sizeof name, old->name);  // (its own spelling)
    // The new file first (it exists, is a 64-bit DLL with swmod_init, isn't loaded already): a bad path or build keeps
    // the old build running.
    HotFile hf;
    if (!open_hot(path, name, old, argc > 3, &hf, err, sizeof err)) {
        if (old) control_fail(r, "%s was not replaced, the old build still runs: %s", name, err);
        else control_fail(r, "%s", err);
        return;
    }
    if (old && !unload_mod(old, true, why, sizeof why)) {
        if (hf.h) FreeLibrary(hf.h);
        control_fail(r, "%s was not replaced: %s", name, why);
        return;
    }
    Mod *m = init_hot(&hf, err, sizeof err);
    if (!m) {
        if (old) control_fail(r, "the old %s was unloaded, but the new one didn't load: %s", name, err);
        else control_fail(r, "%s", err);
        return;
    }
    reply_loaded(r, m, old != NULL);
}

// ------------------------------------------------------------------ `mods`: what was loaded (proves the injection)

static void cmd_mods(int argc, char **argv, ControlReply *r) {
    (void)argc;
    (void)argv;
    const Mod *list[MAX_MODS];
    int n = 0, ok = 0, hot = 0, hooked = 0;
    for (int k = 0; k < MAX_MODS; k++)
        if (g_mods[k].used) {
            list[n++] = &g_mods[k];
            ok += g_mods[k].ok;
            hot += g_mods[k].hot;
        }
    for (int k = 0; k < g_nstates; k++) hooked += g_states[k].nregs > 0;
    control_field_str(r, "host", MODHOST_VERSION);
    control_field_int(r, "api", SWMOD_API_VERSION);
    control_field_str(r, "game_version", g_real && g_real->version ? g_real->version : "");
    control_field_str(r, "game_lib", g_real_path);
    control_field_str(r, "dir", g_mod_dir);
    control_field_int(r, "found", n);
    control_field_int(r, "loaded", ok);
    control_field_int(r, "hot", hot);
    control_field_int(r, "hook_slots", g_hooks ? g_hooks->count : 0);
    control_field_int(r, "hooked", hooked);
    control_text(r, "%d of %d native mods loaded (modhost %s, API %d, %u hook slots)", ok, n, MODHOST_VERSION,
                 SWMOD_API_VERSION, g_hooks ? g_hooks->count : 0u);
    for (int i = 0; i < n; i++) {
        const Mod *m = list[i];
        char key[64];
        snprintf(key, sizeof key, "mods.%d.name", i);
        control_field_str(r, key, m->name);
        snprintf(key, sizeof key, "mods.%d.id", i);
        control_field_int(r, key, m->id);
        snprintf(key, sizeof key, "mods.%d.file", i);
        control_field_str(r, key, m->file);
        snprintf(key, sizeof key, "mods.%d.hot", i);
        control_field_bool(r, key, m->hot);
        snprintf(key, sizeof key, "mods.%d.ok", i);
        control_field_bool(r, key, m->ok);
        snprintf(key, sizeof key, "mods.%d.status", i);
        control_field_str(r, key, m->status);
        snprintf(key, sizeof key, "mods.%d.commands", i);
        control_field_str(r, key, m->commands);
        snprintf(key, sizeof key, "mods.%d.skipped", i);
        control_field_str(r, key, m->skipped);
        snprintf(key, sizeof key, "mods.%d.hooks", i);
        control_field_str(r, key, m->hooks);
        snprintf(key, sizeof key, "mods.%d.overrides", i);
        control_field_str(r, key, m->overrides);
        control_text(r, "%s: %s%s%s%s%s%s%s%s", m->file, m->status, m->ncommands ? ", commands: " : "", m->commands,
                     *m->skipped ? ", skipped: " : "", m->skipped, *m->hooks ? ", hooks: " : "", m->hooks,
                     m->hot ? " (loaded while the game runs)" : "");
    }
}

// ------------------------------------------------------------------ loading at game start

static const GameLibTables *load_real(void) {
    wchar_t given[PATH_W] = {0}, path[PATH_W];
    if (!env_w(L"SWMOD_REAL_GAME_LIB", given, PATH_W)) {
        given[0] = 0;
        wcat(given, PATH_W, L"extracted\\swgame.dll");
    }
    if (!full_path(given, path, PATH_W)) {
        host_log("the game code path is too long");
        return NULL;
    }
    to_utf8(path, g_real_path, sizeof g_real_path);
    HMODULE h = LoadLibraryExW(path, NULL, SW_LOAD_SEARCH_DLL_DIR | SW_LOAD_SEARCH_DEFAULT_DIRS);
    if (!h) {
        host_log("can't load the game code %s (error %lu); mods not loaded", g_real_path, GetLastError());
        return NULL;
    }
    if (h == own_module()) {
        host_log("SWMOD_REAL_GAME_LIB points at modhost itself; mods not loaded");
        return NULL;
    }
    GameLibEntry entry = (GameLibEntry)GetProcAddress(h, GAMELIB_ENTRY);
    if (!entry) {
        host_log("%s is not game code (no %s); mods not loaded", g_real_path, GAMELIB_ENTRY);
        return NULL;
    }
    const GameLibTables *t = entry();
    if (!t) {
        host_log("%s returned no tables; mods not loaded", g_real_path);
        return NULL;
    }
    g_real_h = h;
    return t;
}

// The game code matches this Savage Wind (the check the runtime makes next, runtime/gamelib.c open_lib).
static bool real_matches(const GameLibTables *t) {
    const char *want = gamelib_id();
    return t->abi == GAMELIB_ABI && t->id && want && !strcmp(t->id, want);
}

static int by_name(const wchar_t *a, const wchar_t *b) {  // case-insensitive (ASCII), for a stable load order
    for (;; a++, b++) {
        wchar_t x = *a >= L'A' && *a <= L'Z' ? *a + 32 : *a, y = *b >= L'A' && *b <= L'Z' ? *b + 32 : *b;
        if (x != y || !x) return (int)x - (int)y;
    }
}

static void load_one(const wchar_t *dir, const wchar_t *file) {
    Mod *m = free_slot();
    if (!m) {
        host_log("more than %d mods: the rest are skipped", MAX_MODS);
        return;
    }
    char ufile[PATH_U], name[256];
    to_utf8(file, ufile, sizeof ufile);
    copy_str(name, sizeof name, ufile);
    size_t n = strlen(name);
    if (n > 4 && !strcmp(name + n - 4, ".dll")) name[n - 4] = 0;
    else if (n > 4 && name[n - 4] == '.') name[n - 4] = 0;
    start_mod(m, ufile, name, false);

    wchar_t path[PATH_W] = {0};
    if (!wcat(path, PATH_W, dir) || !wcat(path, PATH_W, L"\\") || !wcat(path, PATH_W, file)) {
        copy_str(m->status, sizeof m->status, "path too long");
        host_log("mod %s: path too long", m->file);
        return;
    }
    m->h = LoadLibraryExW(path, NULL, SW_LOAD_SEARCH_DLL_DIR | SW_LOAD_SEARCH_DEFAULT_DIRS);
    if (!m->h) {
        snprintf(m->status, sizeof m->status, "load failed (error %lu)", GetLastError());
        host_log("mod %s: %s", m->file, m->status);
        return;
    }
    SwModInitFn init = (SwModInitFn)GetProcAddress(m->h, SWMOD_INIT_NAME);
    if (!init) {
        copy_str(m->status, sizeof m->status, "loaded, no swmod_init");
        host_log("mod %s: %s", m->file, m->status);
        return;
    }
    g_current_tid = GetCurrentThreadId();
    g_current = m;
    int rc = init(&m->api);
    g_current = NULL;
    m->ok = rc == 0;
    start_overrides_done(m, m->ok);  // (a failed init: its overrides never go in)
    if (m->ok) copy_str(m->status, sizeof m->status, "ok");
    else {
        switch_off(m);  // (its hooks come out again: no game code runs yet)
        snprintf(m->status, sizeof m->status, "init failed (code %d)", rc);
        host_log("mod %s: swmod_init failed (code %d): its hooks and overrides were taken out", m->file, rc);
    }
    log_loaded("mod ", m);
}

static void load_mods(void) {
    wchar_t given[PATH_W] = {0}, dir[PATH_W];
    if (!env_w(L"SWMOD_DIR", given, PATH_W)) {
        given[0] = 0;
        wcat(given, PATH_W, L"mods\\native");
    }
    if (!full_path(given, dir, PATH_W)) {
        host_log("the mods folder path is too long");
        return;
    }
    size_t n = wlen(dir);
    while (n > 3 && (dir[n - 1] == L'\\' || dir[n - 1] == L'/')) dir[--n] = 0;
    to_utf8(dir, g_mod_dir, sizeof g_mod_dir);
    st_load();  // (deploy\native\settings.txt, next to the mods folder: before any mod starts)
    if (!is_dir(dir)) {
        host_log("no mods folder at %s", g_mod_dir);
        return;
    }
    wchar_t pattern[PATH_W] = {0};
    if (!wcat(pattern, PATH_W, dir) || !wcat(pattern, PATH_W, L"\\*.dll")) return;

    // the file names, sorted (FindFirstFile's order depends on the file system)
    static wchar_t names[MAX_MODS + 1][260];
    int count = 0;
    SwFindData fd;
    HANDLE f = FindFirstFileW(pattern, &fd);
    if (f != SW_INVALID_HANDLE) {
        do {
            if (fd.attributes & SW_ATTR_DIRECTORY) continue;
            size_t len = wlen(fd.name);
            // (*.dll also matches *.dll_ and the like through 8.3 names: take only real .dll files)
            if (len < 5 || by_name(fd.name + len - 4, L".dll")) continue;
            if (count == MAX_MODS + 1) break;
            memcpy(names[count], fd.name, (len + 1) * sizeof(wchar_t));
            for (int i = count++; i > 0 && by_name(names[i - 1], names[i]) > 0; i--) {
                wchar_t tmp[260];
                memcpy(tmp, names[i], sizeof tmp);
                memcpy(names[i], names[i - 1], sizeof tmp);
                memcpy(names[i - 1], tmp, sizeof tmp);
            }
        } while (FindNextFileW(f, &fd));
        FindClose(f);
    }
    host_log("%d native mod%s in %s", count > MAX_MODS ? MAX_MODS : count, count == 1 ? "" : "s", g_mod_dir);
    for (int i = 0; i < count; i++) load_one(dir, names[i]);
}

// SWMOD_UNLOAD_WAIT_MS: how long modunload waits for game threads to leave a mod's hooks (0..60000 ms).
static void read_wait_ms(void) {
    wchar_t v[16];
    if (!env_w(L"SWMOD_UNLOAD_WAIT_MS", v, 16)) return;
    uint32_t ms = 0;
    for (const wchar_t *p = v; *p; p++) {
        if (*p < L'0' || *p > L'9' || ms > 60000) return;
        ms = ms * 10 + (uint32_t)(*p - L'0');
    }
    g_wait_ms = ms > 60000 ? 60000 : ms;
}

// ------------------------------------------------------------------ the export Savage Wind calls

GAMELIB_EXPORT const GameLibTables *swgame_tables(void) {
    static bool done;
    if (done) return g_real;  // (Savage Wind calls it once; this keeps a second call harmless)
    done = true;
    host_log("modhost %s (mod API %d)", MODHOST_VERSION, SWMOD_API_VERSION);
    read_wait_ms();
    const GameLibTables *t = load_real();
    if (!t) return NULL;
    g_real = t;
    host_log("game code %s (game version %s)", g_real_path, t->version ? t->version : "?");
    if (!real_matches(t)) {
        host_log("that game code was built for another Savage Wind version; mods not loaded");
        return t;  // (Savage Wind refuses it the same way it would without modhost)
    }
    static const ControlCmd host_cmds[] = {
        {"mods", "", "List the native code mods modhost loaded", cmd_mods, CONTROL_PLAYER},
        {"modload", "<path to .dll>", "Load a native code mod while the game runs", cmd_modload, 0},
        {"modunload", "<name>", "Unload a mod that modload loaded", cmd_modunload, 0},
        {"modreload", "<name> <path to .dll>", "Unload a mod and load the new build of it", cmd_modreload, 0},
        {"modsettings", "[<mod>]", "List the mods with settings, or one mod's settings", cmd_modsettings,
         CONTROL_PLAYER},
        {"modset", "<mod> <key> [<value>]", "Change a mod's setting as the Mod menu does (reset: all)", cmd_modset, 0},
    };
    for (size_t i = 0; i < sizeof host_cmds / sizeof *host_cmds; i++)
        if (register_one(&host_cmds[i]) != REG_ADDED)
            host_log(i >= 4 ? "couldn't add the command %s (the name is taken?): mod settings work, but not from "
                              "the console"
                     : i ? "couldn't add the command %s (the name is taken?): mods can't be loaded while the game runs"
                         : "couldn't add the command %s (the name is taken?): see these [modhost] lines instead",
                     host_cmds[i].name);
    load_hooks();
    load_mods();
    return t;
}
