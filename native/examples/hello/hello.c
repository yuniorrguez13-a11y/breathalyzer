// hello: the smallest native code mod. It adds the control command `hello`, which answers
//     {"ok":true,"data":{"hello":"world","mods_api":2}}   (mods_api: the host's mod API version)
// Build: python tools/build_native.py build. Install: copy hello.dll to <data root>\mods\native\.
#include "swmod.h"

static const SwModApi *api;

static void cmd_hello(int argc, char **argv, SwModReply *r) {
    (void)argc;
    (void)argv;
    api->reply_str(r, "hello", "world");
    api->reply_int(r, "mods_api", api->api_version);
}

static const SwModCommand commands[] = {
    {"hello", "", "Say hello (example native mod)", cmd_hello, SWMOD_CMD_PLAYER},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;  // a host older than this mod
    api = a;
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("hello: ready (game %s, mod API %u)", api->game_version, (unsigned)api->api_version);
    return 0;
}
