// Mod menu: a Mods page in the game's Options, in the game's own font and look, so a player can see the installed
// mods, turn them on or off and change their order without Mod Manager (docs/mod-menu.md).
//
// What the page lists comes from <library>\menu\menu.txt, which tools/modmenu.py writes from Mod Manager's library;
// the game reads it once, here in swmod_init. The player's changes go to <library>\menu\pending.txt (a thread of this
// mod writes it, never the game thread). Nothing changes while the game runs: tools/modmenu.py apply / play uses the
// changes (Mod Manager's Play only once it calls modmenu.apply: menu.txt's "use" line says which, and the page's
// texts follow it). The library is SWMOD_LIBRARY, else three folders up from SWMOD_DIR
// (<library>\deploy\native\mods, when config.json is there), else Documents\SavageWind\modloader.
//
// The page itself: opt_page_a.c (Savage Wind 0.4.1-alpha.4 with BotW 1.9.0, no change to Savage Wind) or
// opt_page_b.c (a Savage Wind with sw_options_add_page). Commands: `modmenu` (any player), and `modmenu_set`,
// `modmenu_move`, `modmenu_undo` (developer mode).
//
// Build: python tools/modmenu.py setup (it also adds the mod to the library). Not tested in the game yet.
#include <stdlib.h>

#include "opt_page.h"

#define PATH_W 1024
#define FILE_MAX (1u << 20)
#define WRITE_MS 50  // pending.txt is written within about 50 ms of a change (SWMOD_MODMENU_WRITE_MS changes it):
                     // the game may close right after a change, and the file is tiny

const SwModApi *g_api;
MmModel g_mm;
PageStatus g_page;

static SwSrwLock g_lock;
static wchar_t g_lib[PATH_W];        // the library folder
static char g_lib_u8[PATH_W * 3];
static const char *g_lib_from = "";  // how it was found
static HANDLE g_writer;
static uint32_t g_written;           // the g_mm.version pending.txt has
static int g_flush;                  // write now
static int g_stop;
static DWORD g_write_ms = WRITE_MS;

void mm_lock(void) { AcquireSRWLockExclusive(&g_lock); }
void mm_unlock(void) { ReleaseSRWLockExclusive(&g_lock); }

// ------------------------------------------------------------------ small helpers

// The game's Caption_00 font (the rows' names and the description line) has no # $ % @ ^ ` { | } ~: the game test
// showed a gap for each. The texts keep them; what shows them decides: path A+ draws them in the game's font tag 3
// (it has every ASCII symbol), or as they are once menu.txt says "symbols full" (font.py fix-symbols); path B, which
// takes plain text, gets look-alikes (mm_plain).
bool mm_font_lacks(char c) { return c && strchr("#$%@^`{|}~", c) != NULL; }

void mm_plain(char *s) {
    for (; *s; s++) {
        switch (*s) {
        case '{': *s = '('; break;
        case '}': *s = ')'; break;
        case '|': *s = '/'; break;
        case '`': case '^': *s = '\''; break;
        case '~': *s = '-'; break;
        case '#': case '$': case '%': case '@': *s = '?'; break;
        }
    }
}

void mm_copy(char *dst, size_t cap, const char *src) {
    size_t i = 0;
    for (; src && src[i] && i + 1 < cap; i++) {
        unsigned char c = (unsigned char)src[i];
        dst[i] = c >= 0x20 && c < 0x7f ? (char)c : '?';
    }
    if (cap) dst[i] = 0;
}

void mm_cat(char *dst, size_t cap, const char *src) {
    size_t n = strlen(dst);
    if (n < cap) mm_copy(dst + n, cap - n, src);
}

static size_t wlen(const wchar_t *s) {
    size_t n = 0;
    while (s[n]) n++;
    return n;
}

static bool wcat(wchar_t *dst, size_t cap, const wchar_t *src) {
    size_t n = wlen(dst), m = wlen(src);
    if (n + m + 1 > cap) return false;
    memcpy(dst + n, src, (m + 1) * sizeof(wchar_t));
    return true;
}

static bool env_w(const wchar_t *name, wchar_t *out, DWORD cap) {
    DWORD n = GetEnvironmentVariableW(name, out, cap);
    return n > 0 && n < cap;
}

static void strip_slashes(wchar_t *p) {
    size_t n = wlen(p);
    while (n > 3 && (p[n - 1] == L'\\' || p[n - 1] == L'/')) p[--n] = 0;
}

static bool up_one(wchar_t *p) {  // drop the last part of a path
    size_t n = wlen(p);
    while (n > 0 && p[n - 1] != L'\\' && p[n - 1] != L'/') n--;
    if (n <= 3) return false;
    p[n - 1] = 0;
    return true;
}

static bool lib_file(const wchar_t *rel, wchar_t *out) {
    out[0] = 0;
    return wcat(out, PATH_W, g_lib) && wcat(out, PATH_W, L"\\") && wcat(out, PATH_W, rel);
}

static bool file_exists(const wchar_t *path) { return GetFileAttributesW(path) != SW_INVALID_ATTRIBUTES; }

// The library: SWMOD_LIBRARY, else <library>\deploy\native\mods (SWMOD_DIR, as modman sets it) three folders up,
// else the default library of modman.
static void find_library(void) {
    wchar_t v[PATH_W];
    g_lib[0] = 0;
    if (env_w(L"SWMOD_LIBRARY", v, PATH_W) && GetFullPathNameW(v, PATH_W, g_lib, NULL) - 1u < PATH_W - 1u) {
        g_lib_from = "SWMOD_LIBRARY";
    } else if (env_w(L"SWMOD_DIR", v, PATH_W) && GetFullPathNameW(v, PATH_W, g_lib, NULL) - 1u < PATH_W - 1u) {
        strip_slashes(g_lib);
        wchar_t cfg[PATH_W];
        bool ok = up_one(g_lib) && up_one(g_lib) && up_one(g_lib) && lib_file(L"config.json", cfg) && file_exists(cfg);
        if (ok) g_lib_from = "SWMOD_DIR";
        else g_lib[0] = 0;
    }
    if (!g_lib[0] && env_w(L"USERPROFILE", v, PATH_W)) {
        wcat(g_lib, PATH_W, v);
        wcat(g_lib, PATH_W, L"\\Documents\\SavageWind\\modloader");
        g_lib_from = "default";
    }
    strip_slashes(g_lib);
    WideCharToMultiByte(SW_CP_UTF8, 0, g_lib, -1, g_lib_u8, (int)sizeof g_lib_u8, NULL, NULL);
}

// A whole small file, 0-terminated (free it); NULL when it isn't there or can't be read (*missing says which).
static char *read_all(const wchar_t *path, bool *missing) {
    *missing = false;
    HANDLE h = CreateFileW(path, SW_GENERIC_READ, SW_FILE_SHARE_ALL, NULL, SW_OPEN_EXISTING, SW_FILE_ATTRIBUTE_NORMAL,
                           NULL);
    if (h == SW_INVALID_HANDLE) {
        DWORD e = GetLastError();
        *missing = e == 2 || e == 3;  // (file / path not found)
        return NULL;
    }
    char *buf = malloc(FILE_MAX + 1);
    DWORD got = 0, total = 0;
    bool ok = buf != NULL;
    while (ok && total < FILE_MAX && ReadFile(h, buf + total, FILE_MAX - total, &got, NULL) && got) total += got;
    CloseHandle(h);
    if (!ok) return NULL;
    buf[total] = 0;
    return buf;
}

static char *next_line(char **p) {
    char *s = *p;
    if (!*s) return NULL;
    char *e = strchr(s, '\n');
    if (e) {
        *e = 0;
        *p = e + 1;
    } else {
        *p = s + strlen(s);
    }
    size_t n = strlen(s);
    if (n && s[n - 1] == '\r') s[n - 1] = 0;
    return s;
}

static int split_tabs(char *s, char **f, int max) {
    int n = 0;
    f[n++] = s;
    for (; *s && n < max; s++)
        if (*s == '\t') {
            *s = 0;
            f[n++] = s + 1;
        }
    return n;
}

static bool good_id(const char *s) {  // (modman's ids: up to 64 characters)
    size_t n = strlen(s);
    if (!n || n >= MM_ID_MAX) return false;
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '.' || c == '_')) return false;
    }
    return true;
}

static bool magic_ok(char **f, int n, const char *magic) {
    if (n < 2 || strcmp(f[0], magic) || !f[1][0]) return false;
    for (const char *p = f[1]; *p; p++)
        if (*p < '0' || *p > '9') return false;
    return atoi(f[1]) >= 1;
}

static bool has_flag(const char *flags, const char *want) {
    size_t n = strlen(want);
    for (const char *p = flags; *p;) {
        const char *e = strchr(p, ',');
        size_t len = e ? (size_t)(e - p) : strlen(p);
        if (len == n && !memcmp(p, want, n)) return true;
        p += len + (e ? 1 : 0);
    }
    return false;
}

// ------------------------------------------------------------------ menu.txt and pending.txt

static int find_mod(const char *id) {
    for (int i = 0; i < g_mm.n; i++)
        if (!strcmp(g_mm.mods[i].id, id)) return i;
    return -1;
}

// config.json's "<size>:<mtime in ns since 1970>", as tools/modmenu.py writes it (Python's st_mtime_ns)
static void config_stamp(char *out, size_t cap) {
    wchar_t path[PATH_W];
    SwFileAttrData d;
    out[0] = 0;
    if (!lib_file(L"config.json", path) || !GetFileAttributesExW(path, 0, &d)) return;
    uint64_t size = (uint64_t)d.size_high << 32 | d.size_low;
    uint64_t ft = (uint64_t)d.written.high << 32 | d.written.low;
    uint64_t ns = ft >= 116444736000000000ull ? (ft - 116444736000000000ull) * 100u : 0;
    snprintf(out, cap, "%llu:%llu", (unsigned long long)size, (unsigned long long)ns);
}

static int to_int(const char *s, int lo, int hi, int bad) {  // a whole number in lo..hi, else bad
    char *end;
    long v = s && *s ? strtol(s, &end, 10) : 0;
    return s && *s && !*end && v >= lo && v <= hi ? (int)v : bad;
}

// The records menu.txt has beyond v1's (docs/mod-menu.md "menu.txt"); each is skipped when it doesn't fit.
static void menu_extra(char **f, int n) {
    if (!strcmp(f[0], "symbols") && n >= 2) {
        g_mm.symbols_full = !strcmp(f[1], "full");
    } else if (!strcmp(f[0], "changed") && n >= 2) {
        mm_copy(g_mm.changed, sizeof g_mm.changed, f[1]);
        g_mm.crashed = n >= 3 && !strcmp(f[2], "crash");
    } else if (!strcmp(f[0], "profile") && n >= 2 && f[1][0] && g_mm.nprofiles < MM_PROFILES) {
        mm_copy(g_mm.profiles[g_mm.nprofiles], MM_PROFILE_MAX, f[1]);
        if (n >= 3 && !strcmp(f[2], "now")) g_mm.profile_now = g_mm.profile_want = g_mm.nprofiles;
        g_mm.nprofiles++;
    } else if (!strcmp(f[0], "font") && n >= 3 && f[1][0] && g_mm.nfonts < MM_FONTS) {
        MmFont *x = &g_mm.fonts[g_mm.nfonts];
        mm_copy(x->key, sizeof x->key, f[1]);
        mm_copy(x->label, sizeof x->label, f[2]);
        if (n >= 4 && !strcmp(f[3], "now")) g_mm.font_now = g_mm.font_want = g_mm.nfonts;
        g_mm.nfonts++;
    } else if (!strcmp(f[0], "picgrid") && n >= 6 && g_mm.ngrids < MM_GRIDS) {
        MmGrid *gr = &g_mm.grids[g_mm.ngrids];
        mm_copy(gr->tex, sizeof gr->tex, f[1]);
        gr->w = to_int(f[2], 8, 8192, 0), gr->h = to_int(f[3], 8, 8192, 0);
        gr->cols = to_int(f[4], 1, 64, 0), gr->rows = to_int(f[5], 1, 64, 0);
        if (gr->w && gr->h && gr->cols && gr->rows) g_mm.ngrids++;
    } else if (!strcmp(f[0], "pic") && n >= 4) {
        int m = find_mod(f[1]);
        int icon = to_int(f[2], -1, 4095, -1), bg = to_int(f[3], -1, 4095, -1);
        if (!strcmp(f[1], "_mods-page")) g_mm.page_bg = bg;
        else if (m >= 0) g_mm.mods[m].icon = icon, g_mm.mods[m].bg = bg;
    } else if (!strcmp(f[0], "files") && n >= 5) {
        int m = find_mod(f[1]);
        if (m < 0) return;
        MmMod *x = &g_mm.mods[m];
        x->files = to_int(f[2], 0, 1 << 30, -1);
        x->wins = to_int(f[3], 0, 1 << 30, 0);
        x->loses = to_int(f[4], 0, 1 << 30, 0);
        mm_copy(x->rival, sizeof x->rival, n >= 6 ? f[5] : "");
    } else if (!strcmp(f[0], "about") && n >= 4) {
        int m = find_mod(f[1]);
        if (m < 0 || g_mm.mods[m].nfields >= MM_FIELDS || !f[3][0]) return;
        MmField *x = &g_mm.mods[m].fields[g_mm.mods[m].nfields++];
        mm_copy(x->field, sizeof x->field, f[2]);
        mm_copy(x->text, sizeof x->text, f[3]);
    }
}

static void load_menu(void) {
    wchar_t path[PATH_W];
    bool missing = true;
    char *text = lib_file(L"menu\\menu.txt", path) ? read_all(path, &missing) : NULL;
    memset(&g_mm, 0, sizeof g_mm);
    g_mm.list = MM_LIST_OK;
    g_mm.profile_now = g_mm.profile_want = g_mm.font_now = g_mm.font_want = -1;
    g_mm.page_bg = -1;
    if (!text) {
        g_mm.list = missing ? MM_LIST_MISSING : MM_LIST_DAMAGED;
        return;
    }
    char *p = text, *line, *f[8], config[64] = "";
    bool first = true;
    while ((line = next_line(&p))) {
        int n = split_tabs(line, f, 8);
        if (first) {
            first = false;
            if (!magic_ok(f, n, "swmodmenu")) {
                g_mm.list = MM_LIST_DAMAGED;
                break;
            }
        } else if (!strcmp(f[0], "serial") && n >= 2) {
            mm_copy(g_mm.serial, sizeof g_mm.serial, f[1]);
        } else if (!strcmp(f[0], "config") && n >= 2) {
            mm_copy(config, sizeof config, f[1]);
        } else if (!strcmp(f[0], "note")) {
            mm_copy(g_mm.note, sizeof g_mm.note, n >= 2 ? f[1] : "");
        } else if (!strcmp(f[0], "use") && n >= 2) {
            g_mm.use_modman = !strcmp(f[1], "modman");
        } else if (!strcmp(f[0], "mod") && n >= 5 && good_id(f[1]) && find_mod(f[1]) < 0) {
            bool hidden = has_flag(f[3], "hidden");
            if (!hidden && g_mm.nlisted >= MM_MAX_MODS) {
                g_mm.more = true;
                continue;
            }
            if (g_mm.n >= MM_MAX_MODS + 4) continue;
            MmMod *m = &g_mm.mods[g_mm.n];
            mm_copy(m->id, sizeof m->id, f[1]);
            m->on = m->wanted = !strcmp(f[2], "on");
            m->hidden = hidden;
            m->rebuild = has_flag(f[3], "rebuild");
            mm_copy(m->name, sizeof m->name, f[4]);
            mm_copy(m->about, sizeof m->about, n >= 6 ? f[5] : "");
            mm_copy(m->extra, sizeof m->extra, n >= 7 ? f[6] : "");
            m->pos = hidden ? -1 : g_mm.nlisted;
            m->files = -1, m->icon = m->bg = -1, m->sm = -2;
            if (!hidden) g_mm.order[g_mm.nlisted++] = g_mm.n;
            g_mm.n++;
        } else {
            menu_extra(f, n);
        }  // (other lines: a newer writer's; skipped)
    }
    free(text);
    if (first) g_mm.list = MM_LIST_DAMAGED;  // (an empty file)
    if (g_mm.list != MM_LIST_OK) {
        g_mm.n = g_mm.nlisted = 0;
        g_mm.nprofiles = g_mm.nfonts = g_mm.ngrids = 0;
        g_mm.profile_now = g_mm.profile_want = g_mm.font_now = g_mm.font_want = -1;
        return;
    }
    if (g_mm.nprofiles && g_mm.profile_now < 0) g_mm.nprofiles = 0;  // (a list without the set in use: no row)
    if (g_mm.nfonts && g_mm.font_now < 0) g_mm.nfonts = 0;
    char now[64];
    config_stamp(now, sizeof now);
    g_mm.stale = config[0] && now[0] && strcmp(config, now) != 0;
}

static bool good_serial(const char *s) {  // (tools/modmenu.py: 16 hex digits; it names a file, so nothing else)
    size_t n = strlen(s);
    if (n != 16) return false;
    for (size_t i = 0; i < n; i++)
        if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f'))) return false;
    return true;
}

// What the game showed when it wrote a pending.txt: the listed mods (indices into g_mm.mods) in order, and their
// ON/OFF then. For this menu.txt, the model itself; for an older one, its copy menu\base-<serial>.txt (tools/modmenu.py
// keeps one per serial). False when there is no copy.
typedef struct {
    int n;
    int mod[MM_MAX_MODS];
    bool on[MM_MAX_MODS];
} Base;

static bool load_base(const char *serial, Base *b) {
    b->n = 0;
    if (!strcmp(serial, g_mm.serial)) {
        for (int k = 0; k < g_mm.nlisted; k++) {
            b->mod[b->n] = g_mm.order[k];
            b->on[b->n++] = g_mm.mods[g_mm.order[k]].on;
        }
        return true;
    }
    wchar_t rel[48] = L"menu\\base-", path[PATH_W];
    size_t at = wlen(rel);
    for (size_t i = 0; serial[i] && at + 5 < sizeof rel / sizeof *rel; i++) rel[at++] = (wchar_t)serial[i];
    rel[at] = 0;
    bool missing;
    char *text = wcat(rel, sizeof rel / sizeof *rel, L".txt") && lib_file(rel, path) ? read_all(path, &missing) : NULL;
    if (!text) return false;
    char *p = text, *line, *f[8];
    bool first = true, ok = false;
    while ((line = next_line(&p))) {
        int n = split_tabs(line, f, 8);
        if (first) {
            first = false;
            if (!magic_ok(f, n, "swmodmenu")) break;
        } else if (!strcmp(f[0], "serial") && n >= 2) {
            ok = !strcmp(f[1], serial);
            if (!ok) break;
        } else if (ok && !strcmp(f[0], "mod") && n >= 5 && good_id(f[1]) && !has_flag(f[3], "hidden")) {
            int m = find_mod(f[1]);
            bool dup = false;
            for (int i = 0; i < b->n; i++) dup |= b->mod[i] == m;
            if (m < 0 || g_mm.mods[m].hidden || dup || b->n >= MM_MAX_MODS) continue;
            b->mod[b->n] = m;
            b->on[b->n++] = !strcmp(f[2], "on");
        }
    }
    free(text);
    return ok;
}

// Only what the player changed then (pending against its base) goes onto this list, as tools/modmenu.py merge does:
// a mod whose ON/OFF differs from the base gets that ON/OFF; the mods the player moved (those outside the longest run
// that kept the base's order) go just after the mod before them in the wanted order. The rest stays as menu.txt says.
static void rebase(const Base *b, const int *pm, const bool *pon, int np) {
    int at[MM_MAX_MODS + 4], seq[MM_MAX_MODS], best[MM_MAX_MODS], prev[MM_MAX_MODS], use[MM_MAX_MODS], nu = 0;
    bool use_on[MM_MAX_MODS], moved[MM_MAX_MODS + 4] = {0};
    for (int i = 0; i < g_mm.n; i++) at[i] = -1;
    for (int k = 0; k < b->n; k++) at[b->mod[k]] = k;
    for (int i = 0; i < np; i++) {
        bool dup = false;
        for (int j = 0; j < nu; j++) dup |= use[j] == pm[i];
        if (dup || at[pm[i]] < 0) continue;
        use[nu] = pm[i], use_on[nu] = pon[i], seq[nu] = at[pm[i]];
        nu++;
    }
    for (int i = 0; i < nu; i++)
        if (use_on[i] != b->on[seq[i]]) g_mm.mods[use[i]].wanted = use_on[i];
    int top = -1;  // the longest run in the base's order (the first one found, as tools/modmenu.py picks it)
    for (int j = 0; j < nu; j++) {
        best[j] = 1, prev[j] = -1;
        for (int k = 0; k < j; k++)
            if (seq[k] < seq[j] && best[k] + 1 > best[j]) best[j] = best[k] + 1, prev[j] = k;
        if (top < 0 || best[j] > best[top]) top = j;
    }
    for (int i = 0; i < nu; i++) moved[use[i]] = true;
    for (int j = top; j >= 0; j = prev[j]) moved[use[j]] = false;
    int cur[MM_MAX_MODS], nc = 0;
    for (int k = 0; k < g_mm.nlisted; k++)
        if (!moved[g_mm.order[k]]) cur[nc++] = g_mm.order[k];
    for (int i = 0; i < nu; i++) {
        if (!moved[use[i]] || nc >= MM_MAX_MODS) continue;
        int to = 0;
        if (i > 0)
            for (int k = 0; k < nc; k++)
                if (cur[k] == use[i - 1]) to = k + 1;
        memmove(&cur[to + 1], &cur[to], (size_t)(nc - to) * sizeof(int));
        cur[to] = use[i];
        nc++;
    }
    if (nc == g_mm.nlisted) memcpy(g_mm.order, cur, (size_t)nc * sizeof(int));
}

// The changes the game wrote last time (pending.txt): the player sees them again after a restart that didn't use
// them. A file made from an older menu.txt is moved onto this one through that list's copy, so nothing the player
// changed is lost when the writer replaces the file; without the copy its changes can't be read, and it is kept as
// pending.old.txt (as tools/modmenu.py does) instead of being overwritten.
static void load_pending(void) {
    wchar_t path[PATH_W];
    bool missing;
    char *text = g_mm.list == MM_LIST_OK && lib_file(L"menu\\pending.txt", path) ? read_all(path, &missing) : NULL;
    if (!text) return;
    char *p = text, *line, *f[4], serial[24] = "";
    int pm[MM_MAX_MODS], np = 0, profile = -1, font = -1;
    bool pon[MM_MAX_MODS], first = true, good = false;
    while ((line = next_line(&p))) {
        int n = split_tabs(line, f, 4);
        if (first) {
            first = false;
            if (!magic_ok(f, n, "swmodmenu-pending")) break;
            good = true;
        } else if (!strcmp(f[0], "serial") && n >= 2 && !serial[0]) {
            mm_copy(serial, sizeof serial, f[1]);
        } else if (!strcmp(f[0], "mod") && n >= 3 && (!strcmp(f[2], "on") || !strcmp(f[2], "off"))) {
            int m = find_mod(f[1]);
            if (m < 0 || g_mm.mods[m].hidden || np >= MM_MAX_MODS) continue;
            pm[np] = m, pon[np++] = !strcmp(f[2], "on");
        } else if (!strcmp(f[0], "profile") && n >= 2) {  // (a mod set or font that is gone: no change)
            for (int i = 0; i < g_mm.nprofiles; i++)
                if (!strcmp(g_mm.profiles[i], f[1])) profile = i;
        } else if (!strcmp(f[0], "font") && n >= 2) {
            for (int i = 0; i < g_mm.nfonts; i++)
                if (!strcmp(g_mm.fonts[i].key, f[1])) font = i;
        }
    }
    free(text);
    Base b;
    if (good && good_serial(serial) && load_base(serial, &b)) {
        rebase(&b, pm, pon, np);
        if (profile >= 0) g_mm.profile_want = profile;
        if (font >= 0) g_mm.font_want = font;
        if (strcmp(serial, g_mm.serial))
            g_api->log("modmenu: pending.txt was made from an older mod list; its changes were moved onto this one");
        return;
    }
    // (damaged, or made from a list that is gone: set it aside, so the writer never replaces changes unread)
    wchar_t old[PATH_W];
    bool kept = lib_file(good ? L"menu\\pending.old.txt" : L"menu\\pending.bad.txt", old) &&
                MoveFileExW(path, old, SW_MOVEFILE_REPLACE_EXISTING | SW_MOVEFILE_WRITE_THROUGH);
    g_api->log("modmenu: pending.txt %s; %s", good ? "was made from a mod list that is gone" : "is damaged",
               kept ? (good ? "kept as pending.old.txt" : "kept as pending.bad.txt") : "can't move it aside");
    if (!kept) {
        g_mm.list = MM_LIST_DAMAGED;  // (no page this time: a change would overwrite the file)
        g_mm.n = g_mm.nlisted = 0;
    } else if (!g_mm.note[0]) {
        mm_copy(g_mm.note, sizeof g_mm.note,
                good ? "The game's changes were made from an old mod list and were not used."
                     : "The game's changes file was damaged and was not used.");
    }
}

// pending.txt: every listed mod in the wanted order, then the mod set and the font when they change; deleted when
// nothing differs from now.
static int pending_text(char *out, size_t cap) {
    size_t n = (size_t)snprintf(out, cap, "swmodmenu-pending\t1\nserial\t%s\n", g_mm.serial);
    for (int k = 0; k < g_mm.nlisted && n < cap; k++) {
        const MmMod *m = &g_mm.mods[g_mm.order[k]];
        n += (size_t)snprintf(out + n, cap - n, "mod\t%s\t%s\n", m->id, m->wanted ? "on" : "off");
    }
    if (n < cap && g_mm.profile_want >= 0 && g_mm.profile_want != g_mm.profile_now)
        n += (size_t)snprintf(out + n, cap - n, "profile\t%s\n", g_mm.profiles[g_mm.profile_want]);
    if (n < cap && g_mm.font_want >= 0 && g_mm.font_want != g_mm.font_now)
        n += (size_t)snprintf(out + n, cap - n, "font\t%s\n", g_mm.fonts[g_mm.font_want].key);
    return n < cap ? (int)n : -1;
}

static bool write_pending(const char *text, int len) {
    wchar_t dir[PATH_W], path[PATH_W], tmp[PATH_W];
    if (!lib_file(L"menu", dir) || !lib_file(L"menu\\pending.txt", path) || !lib_file(L"menu\\pending.txt.tmp", tmp))
        return false;
    if (len < 0) return DeleteFileW(path) || GetLastError() == 2;
    CreateDirectoryW(dir, NULL);
    HANDLE h = CreateFileW(tmp, SW_GENERIC_WRITE, 0, NULL, SW_CREATE_ALWAYS, SW_FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == SW_INVALID_HANDLE) return false;
    DWORD done = 0;
    bool ok = WriteFile(h, text, (DWORD)len, &done, NULL) && done == (DWORD)len;
    ok = FlushFileBuffers(h) && ok;
    CloseHandle(h);
    if (ok) ok = MoveFileExW(tmp, path, SW_MOVEFILE_REPLACE_EXISTING | SW_MOVEFILE_WRITE_THROUGH);
    if (!ok) DeleteFileW(tmp);
    return ok;
}

// The writer: pending.txt after every change, at most once per WRITE_MS. It never runs on the game thread.
static DWORD WINAPI writer_main(void *arg) {
    (void)arg;
    static char text[MM_MAX_MODS * 96 + 256];
    uint64_t next = 0;
    while (!__atomic_load_n(&g_stop, __ATOMIC_ACQUIRE)) {
        Sleep(20);
        uint32_t v = __atomic_load_n(&g_mm.version, __ATOMIC_ACQUIRE);
        if (v == g_written) continue;
        bool now = __atomic_exchange_n(&g_flush, 0, __ATOMIC_ACQ_REL) != 0;
        if (!now && GetTickCount64() < next) continue;
        mm_lock();
        v = g_mm.version;
        int len = mm_changes() ? pending_text(text, sizeof text) : -1;
        mm_unlock();
        static bool said;
        if (write_pending(text, len)) {
            g_written = v;
        } else if (!said) {
            said = true;
            g_api->log("modmenu: can't write pending.txt in %s\\menu (error %lu)", g_lib_u8, GetLastError());
        }
        next = GetTickCount64() + g_write_ms;
    }
    return 0;
}

void mm_flush_soon(void) { __atomic_store_n(&g_flush, 1, __ATOMIC_RELEASE); }

// ------------------------------------------------------------------ the model's changes (locked)

static void changed(void) { __atomic_add_fetch(&g_mm.version, 1, __ATOMIC_RELEASE); }

void mm_set_wanted(int m, bool on) {
    if (m < 0 || m >= g_mm.n || g_mm.mods[m].hidden || g_mm.mods[m].wanted == on) return;
    g_mm.mods[m].wanted = on;
    changed();
}

int mm_wanted_pos(int m) {
    for (int k = 0; k < g_mm.nlisted; k++)
        if (g_mm.order[k] == m) return k;
    return -1;
}

void mm_move(int m, int pos) {
    int from = mm_wanted_pos(m);
    if (from < 0 || !g_mm.nlisted) return;
    pos = pos < 0 ? 0 : pos >= g_mm.nlisted ? g_mm.nlisted - 1 : pos;
    if (pos == from) return;
    if (pos > from) memmove(&g_mm.order[from], &g_mm.order[from + 1], (size_t)(pos - from) * sizeof(int));
    else memmove(&g_mm.order[pos + 1], &g_mm.order[pos], (size_t)(from - pos) * sizeof(int));
    g_mm.order[pos] = m;
    changed();
}

void mm_set_profile(int p) {
    if (p < 0 || p >= g_mm.nprofiles || p == g_mm.profile_want) return;
    g_mm.profile_want = p;
    changed();
}

void mm_set_font(int f) {
    if (f < 0 || f >= g_mm.nfonts || f == g_mm.font_want) return;
    g_mm.font_want = f;
    changed();
}

bool mm_undo(void) {
    if (!mm_changes()) return false;
    for (int i = 0; i < g_mm.n; i++) {
        g_mm.mods[i].wanted = g_mm.mods[i].on;
        if (g_mm.mods[i].pos >= 0) g_mm.order[g_mm.mods[i].pos] = i;
    }
    g_mm.profile_want = g_mm.profile_now;
    g_mm.font_want = g_mm.font_now;
    changed();
    return true;
}

int mm_changes(void) {
    int n = 0;
    for (int k = 0; k < g_mm.nlisted; k++) {
        const MmMod *m = &g_mm.mods[g_mm.order[k]];
        n += m->wanted != m->on || m->pos != k;
    }
    return n + (g_mm.profile_want != g_mm.profile_now) + (g_mm.font_want != g_mm.font_now);
}

// modhost's settings (mod API 3, with a deployed settings.txt), or NULL: then a mod's page shows only its info
const SwModSettingsUi *mm_ui(void) {
    if (!SWMOD_HAS(g_api, settings_ui) || !g_api->settings_ui) return NULL;
    const SwModSettingsUi *ui = g_api->settings_ui();
    return SWMOD_UI_HAS(ui, reset) ? ui : NULL;
}

int mm_settings_of(int m) {
    if (m < 0 || m >= g_mm.n) return -1;
    MmMod *x = &g_mm.mods[m];
    if (x->sm == -2) {  // (settings.txt is read once, at the start: the answer doesn't change)
        const SwModSettingsUi *ui = mm_ui();
        x->sm = -1;
        for (int i = 0, n = ui ? ui->mods() : 0; i < n; i++) {
            const char *id = ui->mod_lib_id(i);
            if (id && !strcmp(id, x->id) && ui->count(i) > 0) {
                x->sm = i;
                break;
            }
        }
    }
    return x->sm;
}

// ------------------------------------------------------------------ commands

static int mod_ref(const char *ref) {  // an id, or a number: the wanted position (1-based)
    int m = find_mod(ref);
    if (m >= 0) return g_mm.mods[m].hidden ? -2 : m;
    char *end;
    long k = strtol(ref, &end, 10);
    return *ref && !*end && k >= 1 && k <= g_mm.nlisted ? g_mm.order[k - 1] : -1;
}

static void cmd_modmenu(int argc, char **argv, SwModReply *r) {
    (void)argc, (void)argv;
    mm_lock();
    int changes = mm_changes();
    g_api->reply_bool(r, "on", g_page.on);
    g_api->reply_str(r, "why", g_page.why);
    g_api->reply_str(r, "path", g_page.path);
    g_api->reply_str(r, "library", g_lib_u8);
    g_api->reply_str(r, "library_from", g_lib_from);
    g_api->reply_str(r, "list", g_mm.list == MM_LIST_OK ? "ok" : g_mm.list == MM_LIST_MISSING ? "missing" : "damaged");
    g_api->reply_bool(r, "stale", g_mm.stale);
    g_api->reply_str(r, "note", g_mm.note);
    g_api->reply_int(r, "count", g_mm.nlisted);
    g_api->reply_int(r, "changes", changes);
    for (int k = 0; k < g_mm.nlisted; k++) {
        int i = g_mm.order[k];
        const MmMod *m = &g_mm.mods[i];
        char key[48];
#define FIELD(kind, name, value)                            \
    do {                                                    \
        snprintf(key, sizeof key, "mods.%d." name, k);      \
        g_api->reply_##kind(r, key, value);                 \
    } while (0)
        FIELD(str, "id", m->id);
        FIELD(str, "name", m->name);
        FIELD(bool, "on", m->on);
        FIELD(bool, "wanted", m->wanted);
        FIELD(int, "position", m->pos + 1);
        FIELD(int, "wanted_position", k + 1);
        FIELD(bool, "rebuild", m->rebuild);
        FIELD(int, "settings", mm_settings_of(i) >= 0 ? mm_ui()->count(mm_settings_of(i)) : 0);
        FIELD(int, "icon", m->icon);
#undef FIELD
    }
    g_api->reply_bool(r, "symbols_full", g_mm.symbols_full);
    g_api->reply_str(r, "profile", g_mm.profile_now >= 0 ? g_mm.profiles[g_mm.profile_now] : "");
    g_api->reply_str(r, "profile_next", g_mm.profile_want >= 0 ? g_mm.profiles[g_mm.profile_want] : "");
    g_api->reply_str(r, "font", g_mm.font_now >= 0 ? g_mm.fonts[g_mm.font_now].key : "");
    g_api->reply_str(r, "font_next", g_mm.font_want >= 0 ? g_mm.fonts[g_mm.font_want].key : "");
    g_api->reply_int(r, "pictures", g_mm.ngrids);
    g_api->reply_bool(r, "settings_ui", mm_ui() != NULL);
    if (g_page.on && !strcmp(g_page.path, "A+")) page_a_status(r);
    if (g_page.on)
        g_api->reply_text(r, "Mod menu on: %d mod%s, %d change%s wait%s for your next start.", g_mm.nlisted,
                          g_mm.nlisted == 1 ? "" : "s", changes, changes == 1 ? "" : "s", changes == 1 ? "s" : "");
    else
        g_api->reply_text(r, "Mod menu off: %s", g_page.why);
    mm_unlock();
}

static void cmd_set(int argc, char **argv, SwModReply *r) {
    if (argc != 3 || (strcmp(argv[2], "on") && strcmp(argv[2], "off"))) {
        g_api->reply_fail(r, "usage: modmenu_set <id or number> on|off");
        return;
    }
    mm_lock();
    int m = mod_ref(argv[1]);
    if (m >= 0) mm_set_wanted(m, !strcmp(argv[2], "on"));
    int changes = mm_changes();
    mm_unlock();
    if (m < 0) {
        g_api->reply_fail(r, m == -2 ? "that is the Mod menu itself" : "no mod %s in the list", argv[1]);
        return;
    }
    g_api->reply_str(r, "id", g_mm.mods[m].id);
    g_api->reply_bool(r, "wanted", !strcmp(argv[2], "on"));
    g_api->reply_int(r, "changes", changes);
}

static void cmd_move(int argc, char **argv, SwModReply *r) {
    char *end = NULL;
    long pos = argc == 3 ? strtol(argv[2], &end, 10) : 0;
    if (argc != 3 || !end || *end || pos < 1) {
        g_api->reply_fail(r, "usage: modmenu_move <id or number> <position>");
        return;
    }
    mm_lock();
    int m = mod_ref(argv[1]);
    bool ok = m >= 0 && pos <= g_mm.nlisted;
    if (ok) mm_move(m, (int)pos - 1);
    int changes = mm_changes(), n = g_mm.nlisted;
    mm_unlock();
    if (!ok) {
        if (m >= 0) g_api->reply_fail(r, "position must be 1 to %d", n);
        else g_api->reply_fail(r, m == -2 ? "that is the Mod menu itself" : "no mod %s in the list", argv[1]);
        return;
    }
    g_api->reply_str(r, "id", g_mm.mods[m].id);
    g_api->reply_int(r, "wanted_position", pos);
    g_api->reply_int(r, "changes", changes);
}

static void cmd_undo(int argc, char **argv, SwModReply *r) {
    (void)argc, (void)argv;
    mm_lock();
    bool any = mm_undo();
    mm_unlock();
    mm_flush_soon();
    g_api->reply_bool(r, "undone", any);
}

// modmenu_key <key>: a key for the key capture that runs now, as if it was pressed on the keyboard (for scripts and the
// self-test; the capture still refuses Savage Wind's own keys). "Backspace" clears; "wait" lets the capture run out.
static void cmd_key(int argc, char **argv, SwModReply *r) {
    char why[120] = "";
    if (argc != 2) {
        g_api->reply_fail(r, "usage: modmenu_key <key name, as in mod.toml, or Backspace>");
        return;
    }
    if (!page_a_inject_key(argv[1], why, sizeof why)) {
        g_api->reply_fail(r, "%s", why);
        return;
    }
    g_api->reply_str(r, "key", argv[1]);
}

static const SwModCommand commands[] = {
    {"modmenu", "", "The Mod menu: is it on, and the mods with the changes for your next start", cmd_modmenu,
     SWMOD_CMD_PLAYER},
    {"modmenu_set", "<id or number> on|off", "Turn a mod on or off at your next start", cmd_set, 0},
    {"modmenu_move", "<id or number> <position>", "Move a mod in the order for your next start", cmd_move, 0},
    {"modmenu_undo", "", "Undo every change for your next start", cmd_undo, 0},
    {"modmenu_key", "<key>", "Press a key for the Mods page's key capture (Backspace clears)", cmd_key, 0},
};

// ------------------------------------------------------------------ start

static void read_write_ms(void) {
    wchar_t v[16];
    if (!env_w(L"SWMOD_MODMENU_WRITE_MS", v, 16)) return;
    DWORD ms = 0;
    for (const wchar_t *p = v; *p; p++) {
        if (*p < L'0' || *p > L'9' || ms > 60000) return;
        ms = ms * 10 + (DWORD)(*p - L'0');
    }
    g_write_ms = ms > 60000 ? 60000 : ms;
}

SWMOD_EXPORT int swmod_init(const SwModApi *api) {
    if (!SWMOD_API_OK(api, 1)) return 1;
    g_api = api;
    if (page_a_game_is_up()) {  // (modload: the Options table is in use already; swapping it now isn't safe)
        api->log("modmenu: the game is already running; load it at game start (turn it on in Mod Manager)");
        return 3;
    }
    InitializeSRWLock(&g_lock);
    read_write_ms();
    find_library();
    load_menu();
    load_pending();
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    g_writer = CreateThread(NULL, 0, writer_main, NULL, 0, NULL);
    if (!g_writer) api->log("modmenu: can't start its writer thread: changes won't be saved");
    g_page.path = "";
    char why[200] = "";
    if (page_b_available()) {
        g_page.on = page_b_start(why, sizeof why);
        if (g_page.on) g_page.path = "B";
    } else {
        g_page.on = page_a_start(why, sizeof why);
        if (g_page.on) g_page.path = "A+";
    }
    mm_copy(g_page.why, sizeof g_page.why, g_page.on ? "" : why);
    const char *list = g_mm.list == MM_LIST_OK ? "" : g_mm.list == MM_LIST_MISSING ? ", no mod list yet"
                                                                                    : ", the mod list is damaged";
    if (g_page.on)
        api->log("modmenu: Mods page on (%d mods, path %s%s)", g_mm.nlisted, g_page.path, list);
    else
        api->log("modmenu: Mods page off: %s", g_page.why);
    return 0;
}

// Only for a mod loaded with modload, which swmod_init refuses: nothing of it may run after this returns.
SWMOD_EXPORT void swmod_exit(const SwModApi *api) {
    (void)api;
    __atomic_store_n(&g_stop, 1, __ATOMIC_RELEASE);
    if (g_writer) {
        WaitForSingleObject(g_writer, SW_INFINITE);
        CloseHandle(g_writer);
        g_writer = NULL;
    }
}
