// basics_teleport_up: the command `up [metres]` moves Link straight up (example mod for BotW 1.9.0,
// docs/game-basics.md). `up` = 10 m, `up 30` = 30 m (up to 1000). A high fall hurts: open the paraglider.
// Developer mode only (it moves Link): start the game with SAVAGEWIND_CONTROL=<port> or SAVAGEWIND_DEV=1.
// Stand still first; it does nothing while riding, in a cutscene or while loading. It only moves up: moving down
// while Link stands on the ground can put him inside it, and indoors moving up can put him inside the ceiling.
#include <stdlib.h>

#include "swmod.h"
#include "botw.h"

static const SwModApi *api;

static void cmd_up(int argc, char **argv, SwModReply *r) {
    float metres = 10.0f;
    if (argc == 2) {
        char *end;
        double d = strtod(argv[1], &end);
        if (end == argv[1] || *end || !(d > 0.0 && d <= 1000.0)) {
            api->reply_fail(r, "usage: up [metres], more than 0 and up to 1000 (it only moves up)");
            return;
        }
        metres = (float)d;
    } else if (argc > 2) {
        api->reply_fail(r, "usage: up [metres]");
        return;
    }
    float before[3];
    if (!botw_player_pos(api, before)) {
        api->reply_fail(r, "Link is not in the world yet: load a save first");
        return;
    }
    if (!botw_teleport_up(api, metres)) {
        api->reply_fail(r, "Can't move Link now (riding, a cutscene or loading?)");
        return;
    }
    api->reply_num(r, "metres", metres);
    api->reply_num(r, "from_y", before[1]);
    api->reply_num(r, "to_y", before[1] + metres);
    api->reply_text(r, "Moved Link up %.1f m (height %.1f to %.1f)", metres, before[1], before[1] + metres);
}

static const SwModCommand commands[] = {
    {"up", "[metres]", "Move Link straight up (example mod; default 10 m)", cmd_up, 0},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    if (!botw_version_ok(api)) {
        api->log("basics_teleport_up: made for game version %s, this is %s", BOTW_GAME_VERSION, api->game_version);
        return 2;
    }
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("basics_teleport_up: ready (type up in the console, developer mode)");
    return 0;
}
