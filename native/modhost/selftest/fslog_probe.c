// Self-test only: mods that override one of the four file opens modhost's file log wraps (fslog.c), for the file log's
// overlap run (tools/build_native.py selftest, run D on the fake game). Built two ways from this file:
//   (default)         loaded at game start: overrides 0x7000e92690 (its function answers x0 = 0x7777, x1 = 9). The
//                     file log must leave that one alone (the first override wins: the mod's own function still runs),
//                     note why in fslog.log, and wrap the other three.
//   -DFSLOG_PROBE_HOT  for modload: overrides 0x7000e8fae0, which the file log wraps. modhost must refuse it and say
//                     that the file log wraps it.
#include <stdint.h>

#include "../swmod.h"

#define FN_OPEN_1 0x7000e8fae0ull  // (fake_game.c: made-up functions at BotW 1.9.0's file-open addresses)
#define FN_OPEN_2 0x7000e92690ull

static void mine(struct Cpu *c) {
    SWMOD_REGS(c)->x[0] = 0x7777;
    SWMOD_REGS(c)->x[1] = 9;
}

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
#ifdef FSLOG_PROBE_HOT
    a->hle_override(FN_OPEN_1, mine, "fslog_probe_hot");
    a->log("fslog probe: loaded while the game runs");
#else
    a->hle_override(FN_OPEN_2, mine, "fslog_probe_start");
    a->log("fslog probe: ready");
#endif
    return 0;
}
