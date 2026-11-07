// Self-test only: a stand-in for the game code library (swgame.dll) with made-up tables: two "functions", one site.
// Nothing here comes from the game. -DSELFTEST_OTHER_ID builds one "for another Savage Wind version" (another id).
#include <stdint.h>

#include "../sw_runtime.h"

#ifdef SELFTEST_OTHER_ID
#define SELFTEST_ID "other-id"
#else
#define SELFTEST_ID "selftest-id"
#endif

struct Cpu {
    uint64_t x[32];
};

static void fake_fn_a(struct Cpu *c) { c->x[0] = 41; }
static void fake_fn_b(struct Cpu *c) { c->x[0] = 42; }

static const uint64_t addrs[] = {0x7000001000ull, 0x7000002000ull};
static const GuestFn fns[] = {fake_fn_a, fake_fn_b};
static const uint64_t count = 2;
static const GameLibModule modules[] = {
    {"main", 0x7000000000ull, 0x10000, "00", addrs, fns, &count},
};
static const uint32_t nmodules = 1;
static const GameLibSite sites[] = {{"player_info", 0x7000123450ull}};
static const uint32_t nsites = 1;

GAMELIB_EXPORT const GameLibTables *swgame_tables(void) {
    static const GameLibTables t = {GAMELIB_ABI, SELFTEST_ID, modules, &nmodules, "0.0.0-selftest", sites, &nsites};
    return &t;
}
