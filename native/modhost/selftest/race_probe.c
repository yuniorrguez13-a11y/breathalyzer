// Self-test only: a mod loaded at game start that plays a game thread (tools/build_native.py selftest), against the
// fake hooked game (selftest/hooked/, made-up functions). `race <address>` starts a thread of this DLL (it is never
// unloaded) that calls the function at that address once through the function table, as a game thread does, with
// x0 = a made-up player block; `race wait` waits for it and answers its x0. Nothing here comes from the game.
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
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

static const SwModApi *api;
static uint64_t g_addr;
static volatile int64_t g_x0 = -1;
static HANDLE g_thread;
static float g_player[64];  // (a made-up player block: the made-up stamina setter stores s0 at +0xa4)

static unsigned long __stdcall game_thread(void *arg) {
    (void)arg;
    Cpu cpu;
    memset(&cpu, 0, sizeof cpu);
    cpu.x[0] = (uint64_t)(uintptr_t)g_player;
    SwModGuestFn f = api->dispatch_lookup(g_addr);
    if (f) f(&cpu);
    g_x0 = (int64_t)cpu.x[0];
    return 0;
}

static void cmd_race(int argc, char **argv, SwModReply *r) {
    if (argc == 2 && !strcmp(argv[1], "wait")) {
        bool done = g_thread && WaitForSingleObject(g_thread, 10000) == 0;
        if (g_thread) CloseHandle(g_thread);
        g_thread = NULL;
        api->reply_bool(r, "done", done);
        api->reply_int(r, "x0", g_x0);
        return;
    }
    if (argc != 2 || g_thread) {
        api->reply_fail(r, "usage: race <address>, then race wait");
        return;
    }
    g_addr = strtoull(argv[1], NULL, 0);
    g_x0 = -1;
    g_thread = CreateThread(NULL, 0, game_thread, NULL, 0, NULL);
    api->reply_bool(r, "started", g_thread != NULL);
}

static const SwModCommand commands[] = {
    {"race", "<address>|wait", "Self-test: call a function on a thread of its own", cmd_race, SWMOD_CMD_PLAYER},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 2)) return 1;
    api = a;
    api->control_register(commands, 1);
    api->log("race: ready");
    return 0;
}
