// Self-test only: three mods loaded at game start that override the same function, for the chain run (run E,
// tools/build_native.py selftest). Built three ways from this file, loaded in this order (file names chain1..3):
//   -DCHAIN_TAG=1                 wraps 0x7000001000 (fake_game.c: x0 = 41) and adds its tag: x0 = x0 * 10 + 1. Also
//                                 overrides 0x7000002000 alone (a chain of one: no chain line). Its command `chainlate`
//                                 asks dispatch_lookup once the game runs (the bottom link: the game's function, not
//                                 chain3 on top) and tries to add 0x7000001000 again: modhost must refuse it.
//   -DCHAIN_TAG=2 -DCHAIN_FAIL     wraps it too, then fails its swmod_init: its override must leave the chain before
//                                 chain3 starts, so chain3 never calls it (no 2 in x0).
//   -DCHAIN_TAG=3                 wraps it (on top of chain1), twice with the same function (no loop), and asks
//                                 dispatch_lookup again after its override (never its own function back: `chainwho`).
// One call through the dispatcher must answer 4113: the game's 41, then chain1's 1, then chain3's 3, each once.
#include <stdbool.h>
#include <stdint.h>

#include "../swmod.h"

#define FN_A 0x7000001000ull  // (fake_game.c: x0 = 41)
#define FN_B 0x7000002000ull  // (fake_game.c: x0 = 42)

static const SwModApi *api;
static SwModGuestFn g_orig;
__attribute__((unused)) static SwModGuestFn g_orig_b, g_orig_after;

static void wrap(struct Cpu *c) {
    if (g_orig) g_orig(c);
    SWMOD_REGS(c)->x[0] = SWMOD_REGS(c)->x[0] * 10 + CHAIN_TAG;
}
__attribute__((unused)) static void wrap_b(struct Cpu *c) {
    if (g_orig_b) g_orig_b(c);
    SWMOD_REGS(c)->x[0] += 100;
}

#if CHAIN_TAG == 1
static void cmd_late(int argc, char **argv, SwModReply *r) {
    (void)argc;
    (void)argv;
    // chain1 is the bottom link: asked now (as from its wrapper), it must get the game's function, never chain3's
    // wrapper on top (that calls chain1 again: a loop)
    api->reply_bool(r, "low", api->dispatch_lookup(FN_A) == g_orig);
    api->hle_override(FN_A, wrap, "chain_late");  // refused: the game runs, and chain3 is on top of it
    api->reply_bool(r, "tried", true);
}
static const SwModCommand commands[] = {{"chainlate", "", "Self-test: hle_override once the game runs", cmd_late, 0}};
#elif CHAIN_TAG == 3
static void cmd_who(int argc, char **argv, SwModReply *r) {
    (void)argc;
    (void)argv;
    SwModGuestFn now = api->dispatch_lookup(FN_A);
    api->reply_bool(r, "own", now == wrap || g_orig_after == wrap);
    api->reply_bool(r, "same", now == g_orig && g_orig_after == g_orig);
}
static const SwModCommand commands[] = {{"chainwho", "", "Self-test: what dispatch_lookup gives chain3", cmd_who, 0}};
#endif

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    g_orig = a->dispatch_lookup(FN_A);
    a->hle_override(FN_A, wrap, "chain_probe");
#if CHAIN_TAG == 1
    g_orig_b = a->dispatch_lookup(FN_B);
    a->hle_override(FN_B, wrap_b, "chain_probe_b");
#elif CHAIN_TAG == 3
    a->hle_override(FN_A, wrap, "chain_probe");  // (the same again: one link, not a loop)
    g_orig_after = a->dispatch_lookup(FN_A);
#endif
#ifdef CHAIN_FAIL
    a->log("chain probe %d: failing on purpose", CHAIN_TAG);
    return 5;
#else
    a->control_register(commands, 1);
    a->log("chain probe %d: ready", CHAIN_TAG);
    return 0;
#endif
}
