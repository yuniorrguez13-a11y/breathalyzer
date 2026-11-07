// Self-test only: a mod that uses the hook API (mod API 2) against the fake hooked game (selftest/hooked/, made-up
// functions given hook slots by tools/gen_hooks.py) and reports what it saw through the `hookprobe` command.
// Against the plain fake game (no hook slots) its hooks fail with SWMOD_HOOK_NO_SLOTS, which it reports too.
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "../swmod.h"
#include "hooked/cpu.h"  // (the made-up Cpu: the release's layout)

#define FN_ADD 0x7000001000ull           // x0 = x0 + x1                         hooked here: before
#define FN_CALLS_ADD 0x7000001010ull     // calls FN_ADD directly (same file), then x0 *= 2
#define FN_NO_SLOT 0x7000001020ull       // x0 = 77, not on the hooks list
#define FN_JUMPS_TO_ADD 0x7000002000ull  // x1 += 1, then a tail jump to FN_ADD
#define FN_TIMES3 0x7000002020ull        // x0 *= 3                              hooked here: replace + after
#define FN_FIVE 0x7000002030ull          // x0 = 5, a slot nobody hooks
#define FN_SETTER 0x7000cc7410ull        // stores s0 at [x0 + 0xa4]             stamina_hook.dll hooks it

static const SwModApi *api;
static int rc_add, rc_times3, rc_taken, rc_no_slot, rc_bad_args;
static int before_runs, after_runs, replace_runs;

static bool before_add(struct Cpu *c) {
    before_runs++;
    if (SWMOD_REGS(c)->x[1] == 1000) {  // skip the function, with our own result
        SWMOD_REGS(c)->x[0] = 7;
        return true;
    }
    SWMOD_REGS(c)->x[1] += 100;  // change an argument
    return false;
}
static void replace_times3(struct Cpu *c, SwModGuestFn orig) {
    replace_runs++;
    orig(c);
    orig(c);
}
static void after_times3(struct Cpu *c) {
    after_runs++;
    SWMOD_REGS(c)->x[0] += 1;
}
static void replace_other(struct Cpu *c, SwModGuestFn orig) { orig(c); }

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

// The made-up setter on a made-up PlayerInfo whose float at +0xa4 is `stored`; returns what is stored after.
static double set_value(float stored, float value) {
    static float player[64];
    memcpy((char *)player + 0xa4, &stored, sizeof stored);
    Cpu cpu;
    memset(&cpu, 0, sizeof cpu);
    cpu.x[0] = (uint64_t)(uintptr_t)player;
    swmod_set_s(&cpu, 0, value);
    SwModGuestFn f = api->dispatch_lookup(FN_SETTER);
    if (!f) return -1;
    f(&cpu);
    float out;
    memcpy(&out, (char *)player + 0xa4, sizeof out);
    return out;
}

static void cmd_hookprobe(int argc, char **argv, SwModReply *r) {
    (void)argc;
    (void)argv;
    api->reply_int(r, "api", api->api_version);
    api->reply_int(r, "rc_add", rc_add);
    api->reply_int(r, "rc_times3", rc_times3);
    api->reply_int(r, "rc_taken", rc_taken);
    api->reply_int(r, "rc_no_slot", rc_no_slot);
    api->reply_int(r, "rc_bad_args", rc_bad_args);
    api->reply_int(r, "rc_late", api->hook(FN_FIVE, before_add, NULL, NULL));  // (not from swmod_init)
    api->reply_bool(r, "can_hook_slot", api->can_hook(FN_FIVE));
    api->reply_bool(r, "can_hook_no_slot", api->can_hook(FN_NO_SLOT));
    api->reply_int(r, "add", run(FN_ADD, 1, 2));
    api->reply_int(r, "add_skipped", run(FN_ADD, 1, 1000));
    api->reply_int(r, "direct_call", run(FN_CALLS_ADD, 1, 2));
    api->reply_int(r, "tail_jump", run(FN_JUMPS_TO_ADD, 1, 2));
    api->reply_int(r, "times3", run(FN_TIMES3, 2, 0));
    api->reply_int(r, "five", run(FN_FIVE, 0, 0));
    api->reply_int(r, "no_slot_fn", run(FN_NO_SLOT, 0, 0));
    api->reply_num(r, "setter_down", set_value(100.0f, 50.0f));
    api->reply_num(r, "setter_up", set_value(100.0f, 150.0f));
    api->reply_int(r, "before_runs", before_runs);
    api->reply_int(r, "after_runs", after_runs);
    api->reply_int(r, "replace_runs", replace_runs);
    api->reply_bool(r, "regs_layout", offsetof(SwModRegs, v) == offsetof(Cpu, v) &&
                                          offsetof(SwModRegs, sp) == offsetof(Cpu, sp));
}

static const SwModCommand commands[] = {
    {"hookprobe", "", "Self-test hook probe", cmd_hookprobe, SWMOD_CMD_PLAYER},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 2) || !SWMOD_HAS(a, can_hook)) return 1;
    api = a;
    rc_add = api->hook(FN_ADD, before_add, NULL, NULL);
    rc_times3 = api->hook(FN_TIMES3, NULL, after_times3, replace_times3);
    rc_taken = api->hook(FN_TIMES3, NULL, NULL, replace_other);  // a second replace
    rc_no_slot = api->hook(FN_NO_SLOT, before_add, NULL, NULL);  // (no slots at all: said once per mod)
    rc_bad_args = api->hook(FN_ADD, NULL, NULL, NULL);
    api->control_register(commands, 1);
    api->log("hookprobe: ready");
    return 0;
}
