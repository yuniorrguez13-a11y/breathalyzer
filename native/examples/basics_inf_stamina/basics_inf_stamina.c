// basics_inf_stamina: stamina never goes down (example mod for BotW 1.9.0, docs/game-basics.md).
// Every frame it compares stamina with the highest value it has seen and puts that back when stamina dropped.
// No game-code rebuild needed. Install it so it loads when the game starts (botw_on_frame needs that; modload won't do).
// Command (developer mode): `infstamina` shows the state, `infstamina off` / `infstamina on` switches it.
#include "swmod.h"
#include "botw.h"

static const SwModApi *api;
static int g_on = 1;        // switched by the command, read every frame
static float g_hold = -1;   // the stamina to hold (-1: none yet); only every_frame uses these two
static uint64_t g_link;     // Link's actor when g_hold was taken: another one means another save was loaded

static void every_frame(struct Cpu *c) {
    (void)c;
    uint64_t link = botw_player_actor(api);
    if (!__atomic_load_n(&g_on, __ATOMIC_RELAXED) || !botw_in_world(api) || link != g_link) {
        g_hold = -1;  // (start again from the current stamina: a loaded save may have fewer wheels)
        g_link = link;
        return;
    }
    float now = botw_stamina_get(api);
    if (now > g_hold) g_hold = now;                        // it went up (resting, a new wheel): hold that
    else if (now < g_hold) botw_stamina_set(api, g_hold);  // it went down: put it back
}

static void cmd_infstamina(int argc, char **argv, SwModReply *r) {
    if (argc > 2 || (argc == 2 && strcmp(argv[1], "on") != 0 && strcmp(argv[1], "off") != 0)) {
        api->reply_fail(r, "usage: infstamina [on|off]");
        return;
    }
    if (argc == 2) __atomic_store_n(&g_on, strcmp(argv[1], "on") == 0, __ATOMIC_RELAXED);
    bool on = __atomic_load_n(&g_on, __ATOMIC_RELAXED);
    int64_t frames = botw_frames();
    api->reply_bool(r, "on", on);
    api->reply_num(r, "stamina", botw_stamina_get(api));
    api->reply_int(r, "frames", frames);
    if (frames == 0 && botw_in_world(api))
        api->reply_text(r, "infstamina %s, but it never ran: install the mod so it loads when the game starts (not "
                           "with modload), and not together with the ghost mod. Then restart the game.",
                        on ? "on" : "off");
    else if (frames == 0)
        api->reply_text(r, "infstamina %s. It starts once a save is loaded.", on ? "on" : "off");
    else
        api->reply_text(r, "infstamina %s (stamina %.0f)", on ? "on" : "off", botw_stamina_get(api));
}

static const SwModCommand commands[] = {
    {"infstamina", "[on|off]", "Infinite stamina (example mod): state, or switch it", cmd_infstamina, 0},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    if (!botw_version_ok(api)) {
        api->log("basics_inf_stamina: made for game version %s, this is %s", BOTW_GAME_VERSION, api->game_version);
        return 2;
    }
    if (!botw_on_frame(api, every_frame)) {
        api->log("basics_inf_stamina: can't run every frame (the frame job isn't in this game code)");
        return 3;
    }
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("basics_inf_stamina: ready if it loaded with the game (type infstamina in the console to check)");
    return 0;
}
