// basics_show_pos: the command `where` says where Link is (example mod for BotW 1.9.0, docs/game-basics.md).
// Type `where` in the in-game console (F12). It only reads, so it works without developer mode.
#include "swmod.h"
#include "botw.h"

static const SwModApi *api;

static void cmd_where(int argc, char **argv, SwModReply *r) {
    (void)argc;
    (void)argv;
    float pos[3];
    if (!botw_player_pos(api, pos)) {
        api->reply_fail(r, "Link is not in the world yet: load a save first");
        return;
    }
    api->reply_num(r, "x", pos[0]);
    api->reply_num(r, "y", pos[1]);
    api->reply_num(r, "z", pos[2]);
    api->reply_text(r, "Link is at x %.1f, y %.1f (height), z %.1f", pos[0], pos[1], pos[2]);
}

static const SwModCommand commands[] = {
    {"where", "", "Where Link is (example mod)", cmd_where, SWMOD_CMD_PLAYER},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    if (!botw_version_ok(api)) {
        api->log("basics_show_pos: made for game version %s, this is %s", BOTW_GAME_VERSION, api->game_version);
        return 2;
    }
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("basics_show_pos: ready (type where in the console)");
    return 0;
}
