// Self-test only: mods for modload that break swmod.h's rules on purpose (tools/build_native.py selftest), against
// the fake hooked game (selftest/hooked/, made-up functions). modhost must refuse what they do, so that nothing of
// them is left in Savage Wind once modunload frees their DLL. Built two ways:
//   -DMISUSE_TAIL : its after hook on FN_FIVE ends with api->hle_override(FN_ADD, ...): a tail call, from a game
//                   thread (so the call has no return address in the mod).
//   -DMISUSE_REARM: its swmod_init overrides FN_ADD and hooks FN_SETTER; the before hook waits 150 ms (modunload waits
//                   for it meanwhile), then calls api->hle_override(FN_ADD, ...) again from the game thread.
#include <stddef.h>
#include <stdint.h>

#include "../swmod.h"

__declspec(dllimport) void __stdcall Sleep(unsigned long ms);

#define FN_ADD 0x7000001000ull     // x0 = x0 + x1
#define FN_FIVE 0x7000002030ull    // x0 = 5
#define FN_SETTER 0x7000cc7410ull  // stores s0 at x0 + 0xa4

static const SwModApi *api;

static void override_add(struct Cpu *c) { SWMOD_REGS(c)->x[0] = 4242; }

#if defined(MISUSE_TAIL)
__attribute__((noinline)) static void after_five(struct Cpu *c) {
    SWMOD_REGS(c)->x[0] += 1;
    api->hle_override(FN_ADD, override_add, "misuse_tail");  // (its last call: a tail call at -O2)
}
#else
static bool before_setter(struct Cpu *c) {
    (void)c;
    Sleep(150);
    api->hle_override(FN_ADD, override_add, "misuse_rearm");
    return false;
}
#endif

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 2)) return 1;
    api = a;
#if defined(MISUSE_TAIL)
    int rc = api->hook(FN_FIVE, NULL, after_five, NULL);
#else
    api->hle_override(FN_ADD, override_add, "misuse_rearm");
    int rc = api->hook(FN_SETTER, before_setter, NULL, NULL);
#endif
    api->log("misuse: ready (hook %d)", rc);
    return rc;
}
