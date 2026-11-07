// settings_demo: a small native mod that reads one of every kind of mod setting (mod API 3, docs/mod-settings.md).
// Its settings are in mod.toml next to this file; modman compiles them into deploy\native\settings.txt and the
// player changes them in the game's Mod menu or in Mod Manager. It changes nothing in the game:
//   - the console command `settings_demo` answers what the DLL reads now (players may run it);
//   - a watcher counts the live changes, and the "Say hello" button writes a line into savagewind.log.
// The pattern for a real mod: read settings with the getters where you use them (they are cheap and need no lock),
// and keep a watcher short: set a flag, then do the work in your hook or command.
// Build: python tools/build_native.py build. Install: python tools/modman.py add native/examples/settings_demo.
#include "swmod.h"

static const SwModApi *api;
static bool g_settings;           // the host has mod API 3 (else every getter below gives the default)
static volatile uint32_t g_changes;  // live changes the watcher saw
static volatile uint32_t g_hellos;   // "Say hello" presses
static char g_last[40];           // the key of the last change ("several": a preset or a reset)

static void copy(char *dst, size_t cap, const char *src) {
    size_t n = strlen(src);
    if (n >= cap) n = cap - 1;
    memcpy(dst, src, n);
    dst[n] = 0;
}

// ---- reading settings (each getter takes the default to use on an older host or without settings.txt)
static int64_t get_int(const char *key, int64_t def) { return g_settings ? api->setting_int(api, key, def) : def; }
static double get_num(const char *key, double def) { return g_settings ? api->setting_num(api, key, def) : def; }
static void get_str(const char *key, const char *def, char *buf, size_t cap) {
    if (!g_settings || api->setting_str(api, key, buf, cap) < 0) copy(buf, cap, def);
}

// The watcher: on the thread that changed the setting (the game's, in the Options screen). Short work only.
static void on_change(const SwModApi *self, const char *key, void *user) {
    (void)user;
    g_changes++;
    copy(g_last, sizeof g_last, key ? key : "several");
    if (key && !strcmp(key, "say_hello")) {
        g_hellos++;
        char greeting[32];
        get_str("greeting", "Hello", greeting, sizeof greeting);
        self->log(get_int("loud", 0) ? "settings_demo: %s!!! (press %u)" : "settings_demo: %s (press %u)", greeting,
                  (unsigned)g_hellos);
    }
}

static void cmd_demo(int argc, char **argv, SwModReply *r) {
    (void)argc;
    (void)argv;
    char mode[32], key[32], greeting[32];
    get_str("mode", "classic", mode, sizeof mode);
    get_str("hotkey", "F6", key, sizeof key);
    get_str("greeting", "Hello", greeting, sizeof greeting);
    api->reply_bool(r, "settings", g_settings);
    api->reply_str(r, "mod_id", g_settings && api->mod_id ? api->mod_id : "");
    api->reply_bool(r, "numbers", get_int("numbers", 1) != 0);
    api->reply_str(r, "mode", mode);
    api->reply_int(r, "mode_index", get_int("mode", 0));
    api->reply_num(r, "speed", get_num("speed", 100));
    api->reply_num(r, "drain", get_num("drain", 1.0));
    api->reply_str(r, "hotkey", key);
    api->reply_int(r, "hotkey_vk", get_int("hotkey", 0x75));
    api->reply_str(r, "greeting", greeting);
    api->reply_bool(r, "loud", get_int("loud", 0) != 0);
    api->reply_bool(r, "note_file", get_int("note_file", 0) != 0);
    api->reply_int(r, "presses", get_int("say_hello", 0));
    api->reply_int(r, "changes", g_changes);
    api->reply_str(r, "last", g_last);
    api->reply_text(r, "Settings demo: mode %s, speed %.0f percent, stamina use %.1fx, key %s, greeting \"%s\"", mode,
                    get_num("speed", 100), get_num("drain", 1.0), *key ? key : "none", greeting);
    api->reply_text(r, "%u live change(s) seen, Say hello pressed %u time(s)", (unsigned)g_changes,
                    (unsigned)get_int("say_hello", 0));
}

static const SwModCommand commands[] = {
    {"settings_demo", "", "Show what the settings demo mod reads from its settings", cmd_demo, SWMOD_CMD_PLAYER},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    copy(g_last, sizeof g_last, "");
    g_settings = SWMOD_HAS(a, settings_ui) && a->mod_id;
    if (g_settings) api->setting_watch(api, on_change, NULL);
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log(g_settings ? "settings_demo: ready (settings of %s)"
                        : "settings_demo: ready, without settings (%s): every setting has its default",
             g_settings ? a->mod_id : SWMOD_HAS(a, settings_ui) ? "no settings.txt deployed" : "modhost too old");
    return 0;
}
