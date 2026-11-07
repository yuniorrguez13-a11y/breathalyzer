// Self-test only: the entry of a fake game library built from the made-up "generated" files in this folder after
// tools/gen_hooks.py added hook slots (build_native.py selftest). Its version is "1.9.0" so that the stamina_hook
// example mod accepts it; nothing here comes from the game.
#include <stdint.h>

#include "../../sw_runtime.h"

extern const uint64_t main_fn_addrs[];
extern const GuestFn main_fn_ptrs[];
extern const uint64_t main_fn_count;

static const GameLibModule modules[] = {
    {"main", 0x7000000000ull, 0x1000000, "00", main_fn_addrs, main_fn_ptrs, &main_fn_count},
};
static const uint32_t nmodules = 1;
static const GameLibSite sites[] = {{"player_info", 0x7000123450ull}};
static const uint32_t nsites = 1;

GAMELIB_EXPORT const GameLibTables *swgame_tables(void) {
    static const GameLibTables t = {GAMELIB_ABI, "selftest-id", modules, &nmodules, "1.9.0", sites, &nsites};
    return &t;
}
