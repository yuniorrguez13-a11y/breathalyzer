// Self-test only: a mod that uses the file redirect (API 4 fs_redirect_*, fslog.c), for the redirect runs of
// tools/build_native.py selftest on the fake game. At swmod_init it names:
//   group "linkle": Model/Armor_001.sbfres -> Model/SwLk_Armor_001.sbfres (the fake game opens it),
//                   Model/Fail.sbfres -> missing/SwLk_Fail.sbfres (the fake game fails a path in missing/),
//                   the first one again (same copy: OK), and bad ones (empty, too long, from == to, taken);
//   group "other":  Model/Other.sbfres -> Model/SwLk_Other.sbfres, and Model/Armor_001.sbfres again (taken).
// Its command `fsr`: `fsr init` (the codes swmod_init got), `fsr <group> on|off`, `fsr thread <group> on|off` (the
// flip from a thread of its own), `fsr state <group>` and `fsr late` (an add after swmod_init: refused).
#include <stdint.h>

#include "../swmod.h"

typedef void *HANDLE;
__declspec(dllimport) HANDLE __stdcall CreateThread(void *sec, size_t stack, unsigned long(__stdcall *fn)(void *),
                                                    void *arg, unsigned long flags, unsigned long *tid);
__declspec(dllimport) unsigned long __stdcall WaitForSingleObject(HANDLE h, unsigned long ms);
__declspec(dllimport) int __stdcall CloseHandle(HANDLE h);

static const SwModApi *api;
static int g_init[9];
static char g_long[300];

static struct {
    const char *group;
    int on, code;
} g_flip;
static unsigned long __stdcall flip(void *arg) {
    (void)arg;
    g_flip.code = api->fs_redirect_set(g_flip.group, g_flip.on);
    return 0;
}

static void cmd_fsr(int argc, char **argv, SwModReply *r) {
    if (argc == 2 && !strcmp(argv[1], "init")) {
        char key[3] = "c0";
        for (int i = 0; i < (int)(sizeof g_init / sizeof *g_init); i++) {
            key[1] = (char)('0' + i);
            api->reply_int(r, key, g_init[i]);
        }
    } else if (argc == 2 && !strcmp(argv[1], "late")) {
        api->reply_int(r, "code", api->fs_redirect_add("linkle", "Model/Late.sbfres", "Model/SwLk_Late.sbfres"));
    } else if (argc == 3 && !strcmp(argv[1], "state")) {
        SwModFsRedirectInfo in;
        memset(&in, 0, sizeof in);
        in.size = sizeof in;
        int code = api->fs_redirect_state(argv[2], &in);
        api->reply_int(r, "code", code);
        api->reply_int(r, "ready", in.ready);
        api->reply_int(r, "on", in.on);
        api->reply_int(r, "count", in.count);
        api->reply_int(r, "in_flight", in.in_flight);
        api->reply_int(r, "wrapped", in.wrapped);
        api->reply_int(r, "opens", (int64_t)in.opens);
        api->reply_int(r, "redirects", (int64_t)in.redirects);
        api->reply_int(r, "fails", (int64_t)in.fails);
        api->reply_bool(r, "idle_known", in.idle_ms >= 0);
    } else if (argc == 4 && !strcmp(argv[1], "thread")) {
        g_flip.group = argv[2], g_flip.on = !strcmp(argv[3], "on"), g_flip.code = -1;
        HANDLE h = CreateThread(NULL, 0, flip, NULL, 0, NULL);
        if (h) WaitForSingleObject(h, 5000), CloseHandle(h);
        api->reply_int(r, "code", g_flip.code);
    } else if (argc == 3) {
        api->reply_int(r, "code", api->fs_redirect_set(argv[1], !strcmp(argv[2], "on")));
    } else {
        api->reply_fail(r, "usage: fsr init | late | state <group> | <group> on|off | thread <group> on|off");
    }
}

static const SwModCommand cmds[] = {{"fsr", "...", "file redirect probe", cmd_fsr, SWMOD_CMD_PLAYER}};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 4)) return 1;
    api = a;
    memset(g_long, 'L', sizeof g_long - 1);
    g_init[0] = a->fs_redirect_add("linkle", "Model/Armor_001.sbfres", "Model/SwLk_Armor_001.sbfres");
    g_init[1] = a->fs_redirect_add("linkle", "Model/Fail.sbfres", "missing/SwLk_Fail.sbfres");
    g_init[2] = a->fs_redirect_add("linkle", "Model/Armor_001.sbfres", "Model/SwLk_Armor_001.sbfres");  // OK again
    g_init[3] = a->fs_redirect_add("linkle", "", "Model/X.sbfres");                                   // BAD_ARGS
    g_init[4] = a->fs_redirect_add("linkle", g_long, "Model/X.sbfres");                               // BAD_ARGS
    g_init[5] = a->fs_redirect_add("linkle", "Model/Same.sbfres", "Model/Same.sbfres");               // BAD_ARGS
    g_init[6] = a->fs_redirect_add("other", "Model/Other.sbfres", "Model/SwLk_Other.sbfres");
    g_init[7] = a->fs_redirect_add("other", "Model/Armor_001.sbfres", "Model/SwLk_Armor_001.sbfres");  // TAKEN
    g_init[8] = a->fs_redirect_add("linkle", "Model/Armor_001.sbfres", "Model/Other_Copy.sbfres");     // TAKEN
    a->control_register(cmds, 1);
    a->log("fsredir probe: ready");
    return 0;
}
