// Self-test only: a mod that uses every part of the API against the fake game (fake_game.c, fake_runtime.c) and
// reports what it saw through the `probe` command. Built three ways: normal, -DPROBE_FAIL (swmod_init returns 7) and
// -DPROBE_NOINIT (no swmod_init at all).
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "../swmod.h"

#ifndef PROBE_NOINIT
static const SwModApi *api;
static int registered;  // what control_register returned

struct Cpu {
    uint64_t x[32];
};

static void replacement(struct Cpu *c) { (void)c; }

static void cmd_probe(int argc, char **argv, SwModReply *r) {
    if (argc > 1) {
        api->reply_fail(r, "failing on purpose: %s", argv[1]);
        return;
    }
    struct Cpu cpu;
    memset(&cpu, 0, sizeof cpu);
    SwModGuestFn f = api->dispatch_lookup(0x7000002000ull);
    if (f) f(&cpu);
    api->guest_call(&cpu, 0x7000003000ull);  // passed on
    api->guest_call(NULL, 0x7000003004ull);  // dropped by the host
    api->guest_call(&cpu, 0);                // refused by the host (Savage Wind would stop with FATAL)
    api->reply_str(r, "mod", api->mod_name);
    api->reply_str(r, "game", api->game_version);
    api->reply_str(r, "host", api->host_version);
    api->reply_bool(r, "size_ok", api->size == sizeof(SwModApi));
    api->reply_int(r, "registered", registered);
    api->reply_int(r, "cpu_ptr", (int64_t)(intptr_t)&cpu);
    api->reply_int(r, "site", (int64_t)api->find_site("player_info"));
    api->reply_int(r, "no_site", (int64_t)api->find_site("not_a_site"));
    api->reply_bool(r, "found_fn", f != NULL);
    api->reply_int(r, "fn_x0", (int64_t)cpu.x[0]);
    api->reply_bool(r, "between_fns", api->dispatch_lookup(0x7000001004ull) == NULL);
    api->reply_bool(r, "outside", api->dispatch_lookup(0x7100000000ull) == NULL);
    api->reply_bool(r, "dev", api->dev_mode());
    api->reply_bool(r, "cpu_null", api->current_cpu() == NULL);
    api->reply_num(r, "half", 0.5);

    // The version macros, against a host whose struct is smaller than this header's (as with a newer header).
    SwModApi older = *api;
    older.size = (uint32_t)offsetof(SwModApi, current_cpu);
    api->reply_bool(r, "ok_smaller_host", SWMOD_API_OK(&older, 1));
    api->reply_bool(r, "has_field", SWMOD_HAS(api, current_cpu));
    api->reply_bool(r, "has_missing_field", SWMOD_HAS(&older, current_cpu));
    older.api_version = 0;
    api->reply_bool(r, "ok_older_version", SWMOD_API_OK(&older, 1));

    api->reply_text(r, "probe ok: %d %s", 42, "lines");
    api->reply_text(r, "second line");
}

static const SwModCommand commands[] = {
    {"probe", "[fail]", "Self-test probe", cmd_probe, SWMOD_CMD_PLAYER},
    {NULL, "", "a broken entry the host must skip", cmd_probe, 0},
    {"help", "", "Savage Wind's own (player safe)", cmd_probe, 0},
    {"ping", "", "Savage Wind's own (developer only)", cmd_probe, 0},
    {"mods", "", "modhost's", cmd_probe, 0},
    {"hello", "", "hello.dll's (loaded before this mod)", cmd_probe, 0},
    {"probe", "", "this mod's own, again", cmd_probe, 0},
    {"probe_more", "", "a second command", cmd_probe, 0},
    {"probe_full", "", "Savage Wind's (fake) table is full by now", cmd_probe, 0},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
#ifdef PROBE_FAIL
    (void)commands;
    (void)registered;
    a->hle_override(0x7000001100ull, replacement, "bad_override");  // dropped: its init fails
    a->log("probe: failing on purpose");
    return 7;
#else
    api = a;
    registered = api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->hle_override(0x7000001000ull, replacement, "probe_override");
    api->log("probe: ready, %d command(s)", registered);
    return 0;
#endif
}
#else
SWMOD_EXPORT int not_a_mod(void) { return 0; }
#endif
