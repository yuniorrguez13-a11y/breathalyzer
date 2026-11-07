// Self-test only: a mod that drives modhost's settings (mod API 3) against the fake game, through the `stprobe`
// command (tests/test_mod_settings.py runs it). It has settings of its own (the test writes its mod.toml and
// settings.txt), counts its watcher's calls and plays a settings screen through settings_ui.
//   stprobe get <key>            what the getters return
//   stprobe set <key> <value>    setting_set (the mod's own change: no watcher call)
//   stprobe watched              how often the watcher ran, and the last key ("(null)": several)
//   stprobe late                 setting_watch outside swmod_init (refused)
//   stprobe info <key> <size>    settings_ui()->info with out.size = size
//   stprobe ui <key> <index>     settings_ui()->set_index; replies the code, the value text, shown, enabled, lock
//   stprobe press <key>          settings_ui()->press
//   stprobe threads              2 threads read a toggle 200000 times while this thread flips it 2000 times
//   stprobe has                  SWMOD_HAS / SWMOD_UI_HAS against a smaller (older) host
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "../swmod.h"

typedef void *HANDLE;
typedef unsigned long (__stdcall *ThreadFn)(void *);
__declspec(dllimport) HANDLE __stdcall CreateThread(void *attrs, size_t stack, ThreadFn fn, void *arg,
                                                     unsigned long flags, unsigned long *id);
__declspec(dllimport) unsigned long __stdcall WaitForSingleObject(HANDLE h, unsigned long ms);
__declspec(dllimport) int __stdcall CloseHandle(HANDLE h);

static const SwModApi *api;
static volatile uint32_t g_watched;
static char g_last[64] = "-";
static int g_watch_rc = -1;

static void on_change(const SwModApi *self, const char *key, void *user) {
    g_watched += self == api && user == (void *)&g_watched;
    size_t n = key ? strlen(key) : 6;
    if (n >= sizeof g_last) n = sizeof g_last - 1;
    memcpy(g_last, key ? key : "(null)", n);
    g_last[n] = 0;
}

static const SwModSettingsUi *ui(void) { return SWMOD_HAS(api, settings_ui) ? api->settings_ui() : NULL; }

static int my_mod(const SwModSettingsUi *u) {
    for (int m = 0; u && m < u->mods(); m++)
        if (api->mod_id && !strcmp(u->mod_id(m), api->mod_id)) return m;
    return -1;
}

static volatile long g_bad;
static volatile int g_stop;
static unsigned long __stdcall reader(void *arg) {
    (void)arg;
    for (int i = 0; i < 200000; i++) {
        int64_t v = api->setting_int(api, "t", -5);
        double d = api->setting_num(api, "n", -5);
        if (v != 0 && v != 1) g_bad++;
        if (d < 0 || d > 10) g_bad++;
    }
    return 0;
}

static void cmd(int argc, char **argv, SwModReply *r) {
    const SwModSettingsUi *u = ui();
    int m = my_mod(u);
    const char *what = argc > 1 ? argv[1] : "";
    if (!strcmp(what, "get") && argc > 2) {
        char buf[80];
        api->reply_int(r, "int", api->setting_int(api, argv[2], -99));
        api->reply_num(r, "num", api->setting_num(api, argv[2], -99));
        api->reply_int(r, "len", api->setting_str(api, argv[2], buf, sizeof buf));
        api->reply_str(r, "str", buf);
        char small[4];
        int n = api->setting_str(api, argv[2], small, sizeof small);
        api->reply_int(r, "small_len", n);
        api->reply_str(r, "small", small);
    } else if (!strcmp(what, "set") && argc > 3) {
        api->reply_int(r, "rc", api->setting_set(api, argv[2], argv[3]));
    } else if (!strcmp(what, "watched")) {
        api->reply_int(r, "count", g_watched);
        api->reply_str(r, "last", g_last);
        api->reply_int(r, "init_rc", g_watch_rc);
    } else if (!strcmp(what, "late")) {
        api->reply_int(r, "rc", api->setting_watch(api, on_change, NULL));
    } else if (!strcmp(what, "info") && argc > 3 && u) {
        SwModSettingInfo info;
        memset(&info, 0x5A, sizeof info);
        info.size = (uint32_t)atoi(argv[3]);
        int s = u->find(m, argv[2]);
        bool ok = u->info(m, s, &info);
        api->reply_bool(r, "ok", ok);
        api->reply_int(r, "size", info.size);
        api->reply_int(r, "type", info.type);
        if (ok && info.size >= sizeof info) {
            api->reply_int(r, "applies", info.applies);
            api->reply_int(r, "values", info.values);
            api->reply_str(r, "key", info.key);
            api->reply_str(r, "label", info.label);
            api->reply_str(r, "page", info.page);
            api->reply_str(r, "unit", info.unit);
            api->reply_num(r, "min", info.min);
            api->reply_num(r, "step", info.step);
            api->reply_int(r, "default_index", info.default_index);
        } else {  // (a smaller struct: nothing after `type` may be written)
            api->reply_bool(r, "untouched", ((unsigned char *)&info)[offsetof(SwModSettingInfo, values)] == 0x5A);
        }
    } else if (!strcmp(what, "ui") && argc > 3 && u) {
        int s = u->find(m, argv[2]);
        char text[80], lock[96];
        api->reply_int(r, "rc", u->set_index(m, s, atoi(argv[3])));
        u->value_text(m, s, -1, text, sizeof text);
        u->lock_text(m, s, lock, sizeof lock);
        api->reply_str(r, "value", text);
        api->reply_int(r, "index", u->get_index(m, s));
        api->reply_bool(r, "shown", u->shown(m, s));
        api->reply_bool(r, "enabled", u->enabled(m, s));
        api->reply_bool(r, "waiting", u->waiting(m, s));
        api->reply_str(r, "lock", lock);
        api->reply_int(r, "serial", u->serial());
    } else if (!strcmp(what, "press") && argc > 2 && u) {
        api->reply_int(r, "rc", u->press(m, u->find(m, argv[2])));
    } else if (!strcmp(what, "threads") && u) {
        g_bad = 0;
        HANDLE t[2];
        for (int i = 0; i < 2; i++) t[i] = CreateThread(NULL, 0, reader, NULL, 0, NULL);
        int s = u->find(m, "t");
        for (int i = 0; i < 2000; i++) u->set_index(m, s, i & 1);
        for (int i = 0; i < 2; i++) {
            WaitForSingleObject(t[i], 0xFFFFFFFFul);
            CloseHandle(t[i]);
        }
        api->reply_int(r, "bad", g_bad);
    } else if (!strcmp(what, "has")) {
        SwModApi older = *api;
        older.size = (uint32_t)offsetof(SwModApi, mod_id);
        api->reply_bool(r, "has_settings", SWMOD_HAS(api, setting_set) && SWMOD_HAS(api, settings_ui));
        api->reply_bool(r, "older_has", SWMOD_HAS(&older, setting_int));
        api->reply_bool(r, "api3", SWMOD_API_OK(api, 3));
        if (u) {
            SwModSettingsUi small = *u;
            small.size = (uint32_t)offsetof(SwModSettingsUi, reset);
            api->reply_bool(r, "ui_has_reset", SWMOD_UI_HAS(u, reset));
            api->reply_bool(r, "small_has_reset", SWMOD_UI_HAS(&small, reset));
            api->reply_int(r, "mods", u->mods());
            api->reply_str(r, "lib_id", m >= 0 ? u->mod_lib_id(m) : "");
            api->reply_bool(r, "mod_on", m >= 0 && u->mod_on(m));
            api->reply_int(r, "pages", m >= 0 ? u->page_count(m) : -1);
        }
    } else {
        api->reply_fail(r, "usage: stprobe get|set|watched|late|info|ui|press|threads|has ...");
    }
}

static const SwModCommand commands[] = {
    {"stprobe", "<what> ...", "Self-test: mod settings", cmd, 0},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 3)) return 1;
    api = a;
    g_watch_rc = api->setting_watch(api, on_change, (void *)&g_watched);
    api->control_register(commands, 1);
    api->log("stprobe: ready (%s, watcher %d)", a->mod_id ? a->mod_id : "no settings", g_watch_rc);
    return 0;
}
