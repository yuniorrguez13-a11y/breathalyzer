// Self-test only: a mod for modload, modreload and modunload (tools/build_native.py selftest), against the fake hooked
// game (selftest/hooked/, made-up functions). Built three ways from this file: v1 (default), -DHOT_V2 (the changed
// build that modreload puts in its place) and -DHOT_FAIL (its swmod_init adds a command, a hook and an override, then
// fails). Its command `hot` reports what it sees; `hot block` holds a thread of its own inside one of its hooks, so
// that modunload must refuse, and `hot release` lets it go.
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "../swmod.h"
#include "hooked/cpu.h"  // (the made-up Cpu: the release's layout)

// kernel32 (every DLL the build makes links it)
typedef void *HANDLE;
typedef unsigned long(__stdcall *ThreadFn)(void *arg);
__declspec(dllimport) HANDLE __stdcall CreateThread(void *attrs, size_t stack, ThreadFn fn, void *arg,
                                                    unsigned long flags, unsigned long *id);
__declspec(dllimport) unsigned long __stdcall WaitForSingleObject(HANDLE h, unsigned long ms);
__declspec(dllimport) int __stdcall CloseHandle(HANDLE h);
__declspec(dllimport) void __stdcall Sleep(unsigned long ms);

#define FN_ADD 0x7000001000ull      // x0 = x0 + x1; hook_probe (loaded at game start) adds 100 to x1 first
#define FN_NO_SLOT 0x7000001020ull  // x0 = 77, no hook slot: v2 asks for a hook (refused), all override it
#define FN_FIVE 0x7000002030ull     // x0 = 5; nobody hooks it at game start
#define FN_SETTER 0x7000cc7410ull   // v1's before hook on it can hold a thread (`hot block`)

#if defined(HOT_V2)
#define VERSION 2
#define FIVE_PLUS 200
#define OVERRIDE_X0 2002
#elif defined(HOT_FAIL)
#define VERSION 9
#define FIVE_PLUS 5000
#define OVERRIDE_X0 9009
#else
#define VERSION 1
#define FIVE_PLUS 100
#define OVERRIDE_X0 1001
#endif

static const SwModApi *api;
static volatile int g_block, g_inside;
static HANDLE g_thread;
static int rc_five, rc_add, rc_setter, rc_no_slot;

static void after_five(struct Cpu *c) { SWMOD_REGS(c)->x[0] += FIVE_PLUS; }
__attribute__((unused)) static bool before_add(struct Cpu *c) {
    SWMOD_REGS(c)->x[1] += 1000;  // (after hook_probe's +100: hooks of mods loaded later run later)
    return false;
}
__attribute__((unused)) static bool before_setter(struct Cpu *c) {
    (void)c;
    if (g_block) {
        g_inside = 1;
        while (g_block) Sleep(1);
        g_inside = 0;
    }
    return false;
}
static void override_fn(struct Cpu *c) { SWMOD_REGS(c)->x[0] = OVERRIDE_X0; }

// Call a function the way the game's dispatcher does (through the function table): x0, x1 in, x0 out.
static int64_t run(uint64_t addr, uint64_t x0, uint64_t x1) {
    Cpu cpu;
    memset(&cpu, 0, sizeof cpu);
    cpu.x[0] = x0;
    cpu.x[1] = x1;
    SwModGuestFn f = api->dispatch_lookup(addr);
    if (!f) return -1;
    f(&cpu);
    return (int64_t)cpu.x[0];
}

static unsigned long __stdcall setter_thread(void *arg) {
    (void)arg;
    static float player[64];  // (a made-up PlayerInfo: the setter stores s0 at +0xa4)
    Cpu cpu;
    memset(&cpu, 0, sizeof cpu);
    cpu.x[0] = (uint64_t)(uintptr_t)player;
    SwModGuestFn f = api->dispatch_lookup(FN_SETTER);
    if (f) f(&cpu);
    return 0;
}

static void cmd_hot(int argc, char **argv, SwModReply *r) {
    if (argc > 1 && !strcmp(argv[1], "block")) {
        g_inside = 0;
        g_block = 1;
        g_thread = CreateThread(NULL, 0, setter_thread, NULL, 0, NULL);
        for (int i = 0; i < 5000 && g_thread && !g_inside; i++) Sleep(1);
        api->reply_bool(r, "blocked", g_inside != 0);
        return;
    }
    if (argc > 1 && !strcmp(argv[1], "release")) {
        g_block = 0;
        bool done = g_thread && WaitForSingleObject(g_thread, 5000) == 0;  // (its code is the mod's: wait for it)
        if (g_thread) CloseHandle(g_thread);
        g_thread = NULL;
        api->reply_bool(r, "released", done);
        return;
    }
    api->reply_int(r, "version", VERSION);
    api->reply_str(r, "mod", api->mod_name);
    api->reply_int(r, "add", run(FN_ADD, 1, 2));
    api->reply_int(r, "five", run(FN_FIVE, 0, 0));
    api->reply_int(r, "rc_five", rc_five);
    api->reply_int(r, "rc_add", rc_add);
    api->reply_int(r, "rc_setter", rc_setter);
    api->reply_int(r, "rc_no_slot", rc_no_slot);
}

__attribute__((unused)) static void cmd_hot2(int argc, char **argv, SwModReply *r) {
    (void)argc;
    (void)argv;
    api->reply_int(r, "version", VERSION);
}

#if defined(HOT_V2)
static const SwModCommand commands[] = {
    {"hot", "[block|release]", "Self-test: the mod loaded while the game runs", cmd_hot, SWMOD_CMD_PLAYER},
    {"hot2", "", "Self-test: a command only the second build has", cmd_hot2, SWMOD_CMD_PLAYER},
};
#elif defined(HOT_FAIL)
static const SwModCommand commands[] = {
    {"hotfail", "", "Self-test: a mod whose swmod_init fails", cmd_hot, SWMOD_CMD_PLAYER},
};
#else
static const SwModCommand commands[] = {
    {"hot", "[block|release]", "Self-test: the mod loaded while the game runs", cmd_hot, SWMOD_CMD_PLAYER},
};
#endif

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 2)) return 1;
    api = a;
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    rc_five = api->hook(FN_FIVE, NULL, after_five, NULL);
#if defined(HOT_V2)
    rc_no_slot = api->hook(FN_NO_SLOT, before_add, NULL, NULL);  // (no slot: the reply says it needs a rebuild)
#elif !defined(HOT_FAIL)
    rc_add = api->hook(FN_ADD, before_add, NULL, NULL);
    rc_setter = api->hook(FN_SETTER, before_setter, NULL, NULL);
#endif
    api->hle_override(FN_NO_SLOT, override_fn, "hot_override");
    api->log("hot: ready (v%d)", VERSION);
#ifdef HOT_FAIL
    return 3;
#else
    return 0;
#endif
}

SWMOD_EXPORT void swmod_exit(const SwModApi *a) { a->log("hot: exit (v%d)", VERSION); }
