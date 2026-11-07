// stamina_hook: infinite stamina through a hook (mod API 2, docs/hooks.md). Example mod for BotW 1.9.0.
//
// It hooks the game's stamina setter at 0x7000cc7410 (1.9.0): x0 = the player info block, s0 = the new stamina, which the
// function stores at player info + 0xa4. A before hook raises the new value to the current one whenever it is lower,
// so stamina can go up but never down.
//
// The game code needs a hook slot for that address: build it with the list next to this file,
//     python tools/sw_rebuild.py build --hooks native\examples\stamina_hook\hooks.txt --out out\rebuild-hooks
// then start Savage Wind with modhost (SAVAGEWIND_GAME_LIB) and SWMOD_REAL_GAME_LIB=<that swgame.dll>.
//
// Command (developer mode, e.g. SAVAGEWIND_CONTROL=<port>): `stamina_hook` says how often the setter ran and how often
// the hook kept stamina from going down; `stamina_hook off` / `stamina_hook on` switches it.
#include <stdint.h>
#include <string.h>

#include "swmod.h"

#define STAMINA_SETTER 0x7000cc7410ull  // 1.9.0
#define STAMINA_OFFSET 0xa4             // in the player info block (float)

static const SwModApi *api;
static int64_t g_calls;  // the setter ran (on game threads: atomics)
static int64_t g_kept;   // ... and the hook kept the old, higher value
static int g_on = 1;

static bool before_set_stamina(struct Cpu *c) {
    __atomic_fetch_add(&g_calls, 1, __ATOMIC_RELAXED);
    uint64_t player = SWMOD_REGS(c)->x[0];
    if (!__atomic_load_n(&g_on, __ATOMIC_RELAXED) || !player) return false;
    float now, next = swmod_get_s(c, 0);
    memcpy(&now, (const void *)(uintptr_t)(player + STAMINA_OFFSET), sizeof now);  // (guest pointers are host ones)
    if (next < now) {
        swmod_set_s(c, 0, now);
        __atomic_fetch_add(&g_kept, 1, __ATOMIC_RELAXED);
    }
    return false;  // the game's setter runs, with the new value
}

static void cmd_stamina_hook(int argc, char **argv, SwModReply *r) {
    if (argc > 1) {
        if (!strcmp(argv[1], "on") || !strcmp(argv[1], "off")) {
            __atomic_store_n(&g_on, argv[1][1] == 'n', __ATOMIC_RELAXED);
        } else {
            api->reply_fail(r, "usage: stamina_hook [on|off]");
            return;
        }
    }
    int64_t calls = __atomic_load_n(&g_calls, __ATOMIC_RELAXED), kept = __atomic_load_n(&g_kept, __ATOMIC_RELAXED);
    bool on = __atomic_load_n(&g_on, __ATOMIC_RELAXED);
    api->reply_bool(r, "on", on);
    api->reply_int(r, "calls", calls);
    api->reply_int(r, "kept", kept);
    api->reply_text(r, "stamina_hook %s: the setter ran %lld times, stamina kept %lld times", on ? "on" : "off",
                    (long long)calls, (long long)kept);
}

static const SwModCommand commands[] = {
    {"stamina_hook", "[on|off]", "Infinite stamina hook: counts, or switch it", cmd_stamina_hook, 0},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 2)) {  // (log is there since version 1)
        if (SWMOD_API_OK(a, 1)) a->log("stamina_hook: needs modhost with mod API 2 (hooks)");
        return 1;
    }
    api = a;
    if (strcmp(api->game_version, "1.9.0") != 0) {
        api->log("stamina_hook: made for game version 1.9.0, this is %s", api->game_version);
        return 2;
    }
    int rc = api->hook(STAMINA_SETTER, before_set_stamina, NULL, NULL);
    if (rc != SWMOD_HOOK_OK) {
        api->log("stamina_hook: can't hook the stamina setter (error %d)", rc);
        return 10 + rc;
    }
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("stamina_hook: ready");
    return 0;
}
