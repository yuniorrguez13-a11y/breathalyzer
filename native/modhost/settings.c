// settings.c - mod settings for modhost (mod API 3, docs/mod-settings.md). Part of modhost.c: it is #included there
// (after the helpers and gates it uses), so modhost still builds from modhost.c alone. Compiled on its own it is
// empty.
//
// At game start modhost reads deploy\native\settings.txt (SWMOD_SETTINGS, else settings.txt next to the mods folder),
// which tools/mod_settings.py writes at deploy: every library mod's settings, compiled. The values the player changed
// are in <library>\settings\<mod id>.json; this file reads them at start and writes them (a temp file, then a rename)
// from a writer thread that starts with the first change. Conditions (show_if, enable_if) come as postfix tokens that
// Python checked; here they are only evaluated, by each setting's declared type.
//
// modhost's own settings (not a mod's: st_host_load, so far only the file log of fslog.c) come from the environment
// at game start instead.
//
// Threads: values are 64-bit atomics (getters need no lock); texts sit behind a small spin lock. Changes come from the
// game's thread (the Mod menu in the Options screen), the frame thread (console commands) or a mod's own thread
// (setting_set). Watchers run on the thread that made the change; a mod loaded with modload has its watchers taken
// out at modunload behind a Gate, like its hooks.
#ifdef MODHOST_C

// ---- the Win32 calls this part needs beyond platform_min.h (documented Win32 interface, declared by hand)
#define ST_GENERIC_READ 0x80000000ul
#define ST_GENERIC_WRITE 0x40000000ul
#define ST_SHARE_ALL 0x7ul
#define ST_CREATE_ALWAYS 2ul
#define ST_OPEN_EXISTING 3ul
#define ST_ATTR_NORMAL 0x80ul
#define ST_MOVE_REPLACE 0x1ul
#define ST_MOVE_WRITE_THROUGH 0x8ul
#define ST_INFINITE 0xFFFFFFFFul
typedef DWORD(WINAPI *StThreadFn)(void *arg);
__declspec(dllimport) HANDLE WINAPI CreateFileW(const wchar_t *path, DWORD access, DWORD share, void *security,
                                                DWORD disposition, DWORD flags, HANDLE templ);
__declspec(dllimport) BOOL WINAPI ReadFile(HANDLE h, void *buf, DWORD size, DWORD *read, void *overlapped);
__declspec(dllimport) BOOL WINAPI WriteFile(HANDLE h, const void *buf, DWORD size, DWORD *written, void *overlapped);
__declspec(dllimport) BOOL WINAPI GetFileSizeEx(HANDLE h, long long *size);
__declspec(dllimport) BOOL WINAPI FlushFileBuffers(HANDLE h);
__declspec(dllimport) BOOL WINAPI CloseHandle(HANDLE h);
__declspec(dllimport) BOOL WINAPI MoveFileExW(const wchar_t *from, const wchar_t *to, DWORD flags);
__declspec(dllimport) BOOL WINAPI DeleteFileW(const wchar_t *path);
__declspec(dllimport) BOOL WINAPI CreateDirectoryW(const wchar_t *path, void *security);
__declspec(dllimport) HANDLE WINAPI CreateThread(void *security, size_t stack, StThreadFn fn, void *arg, DWORD flags,
                                                 DWORD *id);
__declspec(dllimport) HANDLE WINAPI CreateEventW(void *security, BOOL manual, BOOL initial, const wchar_t *name);
__declspec(dllimport) BOOL WINAPI SetEvent(HANDLE h);
__declspec(dllimport) DWORD WINAPI WaitForSingleObject(HANDLE h, DWORD ms);

#define ST_MAX_MODS 64
#define ST_MAX_SETTINGS 64
#define ST_MAX_PAGES 16
#define ST_MAX_CHOICES 64
#define ST_MAX_SETS 64
#define ST_MAX_DLLS 4
#define ST_MAX_ON 128
#define ST_MAX_WATCH 8          // per mod
#define ST_WATCH_SLOTS 256
#define ST_TEXT 65              // a text setting: 64 characters and the 0
#define ST_TXT_MAX (4u << 20)   // settings.txt: 4 MiB at most
#define ST_JSON_MAX (1u << 20)  // a values file: 1 MiB at most
#define MAX_RPN_STACK 32        // (Python allows 32 tokens, so the stack never gets deeper)

typedef struct {
    const char *key;  // another setting of the mod
    const char *text; // its value, as text
} StSet;

typedef struct {
    const char *value, *label;
    StSet *sets;
    int nsets;
} StChoice;

typedef struct {
    const char *key, *label, *desc, *page, *unit, *def_text, *deployed_text;
    const char *show_rpn, *enable_rpn, *lock_words;  // NULL: no condition
    int type, applies, decimals, max_length, steps;
    double min, max, step;
    StChoice *choices;
    int nchoices;
    int64_t def;               // the default, as an index (toggle 0/1, slider step, choice index, key code)
    int64_t cur;               // the value now (atomic)
    int64_t start;             // what the getters return for restart / deploy settings
    char *tcur, *tstart;       // text settings: ST_TEXT bytes each, behind g_st_text_lock
} StSetting;

typedef struct {
    const char *key, *label, *desc;
} StPage;

typedef struct {
    const char *id, *lib_id, *name;
    bool on;
    const char *dlls[ST_MAX_DLLS];
    int ndlls;
    StPage pages[ST_MAX_PAGES];
    int npages;
    StSetting *s;  // ST_MAX_SETTINGS of them
    int n;
    uint32_t dirty;  // values changed since the writer last saved them (atomic)
    int nwatch;      // watchers registered for it (frame thread / init only)
} StMod;

typedef struct {
    SwModSettingFn fn;
    void *user;
    const SwModApi *api;  // the mod's own api (passed back as self)
    int st;               // StMod index
    int mod;              // modhost's Mod id; 0: a free slot
    uint32_t active;      // 1: called (atomic)
} StWatch;

static StMod g_st[ST_MAX_MODS];
static int g_nst;
static struct {
    const char *id;
    bool on;
} g_st_on[ST_MAX_ON];
static int g_nst_on;
static char g_st_library[PATH_U];
static char g_st_path[PATH_U];   // settings.txt (empty: none found)
static bool g_st_loaded;
static uint32_t g_st_serial;
static uint32_t g_st_text_lock;
static StWatch g_st_watch[ST_WATCH_SLOTS];
static uint32_t g_st_nwatch;     // slots in use so far (atomic: game threads read it)
static Gate g_st_gate;           // around watcher calls (an unload waits for them)
static HANDLE g_st_event;
static uint32_t g_st_writer;     // 1: the writer thread runs
static uint32_t g_st_write_ms = 30;
static uint32_t g_st_writes;     // files written (for `modsettings`)
static uint32_t g_st_write_fails;
static bool g_st_exiting;        // the game is closing (st_flush_at_exit)
static void st_flush_at_exit(void);

static void st_text_lock(void) {
    while (__atomic_exchange_n(&g_st_text_lock, 1, __ATOMIC_ACQUIRE)) Sleep(0);
}
static void st_text_unlock(void) { __atomic_store_n(&g_st_text_lock, 0, __ATOMIC_RELEASE); }

// ------------------------------------------------------------------ small helpers

static int64_t st_round(double x) { return (int64_t)(x < 0 ? x - 0.5 : x + 0.5); }

static double st_p10(int d) {
    double p = 1;
    while (d-- > 0) p *= 10;
    return p;
}

static double st_slider_value(const StSetting *s, int64_t i) {
    double p = st_p10(s->decimals);
    return (double)st_round((s->min + (double)i * s->step) * p) / p;
}

static bool st_num_equal(double a, double b) {
    double m = a < 0 ? -a : a, n = b < 0 ? -b : b, d = a - b;
    if (n > m) m = n;
    if (m < 1) m = 1;
    return (d < 0 ? -d : d) <= 1e-9 * m;
}

// Key names and their Windows virtual-key codes: the same table as tools/mod_settings.py KEYS.
static const struct {
    const char *name;
    int vk;
} g_st_keys[] = {
    {"Space", 0x20}, {"Tab", 0x09}, {"Enter", 0x0D}, {"Insert", 0x2D}, {"Delete", 0x2E}, {"Home", 0x24},
    {"End", 0x23}, {"PageUp", 0x21}, {"PageDown", 0x22}, {"Left", 0x25}, {"Up", 0x26}, {"Right", 0x27},
    {"Down", 0x28}, {"Shift", 0x10}, {"Ctrl", 0x11}, {"Alt", 0x12},
};

static bool st_same_ci(const char *a, const char *b) { return same_name(a, b); }

// The code of a key name ("F6", "a", "Num3"; any case), 0 for "" or "none", -1 for no key.
static int st_key_vk(const char *name) {
    if (!name || !*name || st_same_ci(name, "none")) return 0;
    char c = name[0] >= 'a' && name[0] <= 'z' ? (char)(name[0] - 32) : name[0];
    if (!name[1] && ((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9'))) return c;
    if ((c == 'F') && name[1] >= '1' && name[1] <= '9') {
        int n = 0;
        for (const char *p = name + 1; *p; p++) {
            if (*p < '0' || *p > '9' || n > 24) return -1;
            n = n * 10 + (*p - '0');
        }
        return n >= 1 && n <= 24 ? 0x6F + n : -1;
    }
    if (strlen(name) == 4 && (name[0] | 32) == 'n' && (name[1] | 32) == 'u' && (name[2] | 32) == 'm' &&
        name[3] >= '0' && name[3] <= '9')
        return 0x60 + (name[3] - '0');
    for (size_t i = 0; i < sizeof g_st_keys / sizeof *g_st_keys; i++)
        if (st_same_ci(name, g_st_keys[i].name)) return g_st_keys[i].vk;
    return -1;
}

// The name of a key code ("" for 0).
static void st_key_name(int vk, char *out, size_t cap) {
    if (!vk) {
        copy_str(out, cap, "");
    } else if ((vk >= 'A' && vk <= 'Z') || (vk >= '0' && vk <= '9')) {
        snprintf(out, cap, "%c", vk);
    } else if (vk >= 0x70 && vk <= 0x87) {
        snprintf(out, cap, "F%d", vk - 0x6F);
    } else if (vk >= 0x60 && vk <= 0x69) {
        snprintf(out, cap, "Num%d", vk - 0x60);
    } else {
        snprintf(out, cap, "Key%d", vk);
        for (size_t i = 0; i < sizeof g_st_keys / sizeof *g_st_keys; i++)
            if (g_st_keys[i].vk == vk) copy_str(out, cap, g_st_keys[i].name);
    }
}

static bool st_text_ok(const char *t, int max) {
    int n = 0;
    for (const unsigned char *p = (const unsigned char *)t; *p; p++, n++)
        if (*p < 0x20 || *p > 0x7E) return false;
    return n <= max;
}

// A value given as text -> its index (toggle 0/1, slider step, choice index, key code). false: not a value.
static bool st_parse_index(const StSetting *s, const char *t, int64_t *out) {
    switch (s->type) {
    case SWMOD_SETTING_TOGGLE:
        if (st_same_ci(t, "true") || st_same_ci(t, "on") || !strcmp(t, "1") || st_same_ci(t, "yes")) *out = 1;
        else if (st_same_ci(t, "false") || st_same_ci(t, "off") || !strcmp(t, "0") || st_same_ci(t, "no")) *out = 0;
        else return false;
        return true;
    case SWMOD_SETTING_SLIDER: {
        char *end;
        double x = strtod(t, &end);
        if (!*t || *end || x != x || x < s->min - 1e-9 * (s->max - s->min + 1) ||
            x > s->max + 1e-9 * (s->max - s->min + 1))
            return false;
        int64_t i = st_round((x - s->min) / s->step);
        *out = i < 0 ? 0 : i >= s->steps ? s->steps - 1 : i;
        return true;
    }
    case SWMOD_SETTING_CHOICE:
        for (int i = 0; i < s->nchoices; i++)
            if (!strcmp(s->choices[i].value, t)) return *out = i, true;
        for (int i = 0; i < s->nchoices; i++)  // (the label, as a player may type it)
            if (st_same_ci(s->choices[i].label, t)) return *out = i, true;
        return false;
    case SWMOD_SETTING_KEY: {
        int vk = st_key_vk(t);
        if (vk < 0) return false;
        *out = vk;
        return true;
    }
    default:
        return false;
    }
}

// ------------------------------------------------------------------ settings.txt

static char *st_read_file(const char *upath, size_t max, size_t *len) {
    wchar_t w[PATH_W];
    if (!MultiByteToWideChar(SW_CP_UTF8, 0, upath, -1, w, PATH_W)) return NULL;
    HANDLE h = CreateFileW(w, ST_GENERIC_READ, ST_SHARE_ALL, NULL, ST_OPEN_EXISTING, ST_ATTR_NORMAL, NULL);
    if (h == SW_INVALID_HANDLE) return NULL;
    long long size = 0;
    char *buf = NULL;
    if (GetFileSizeEx(h, &size) && size >= 0 && (unsigned long long)size <= max) {
        buf = malloc((size_t)size + 1);
        DWORD got = 0;
        if (buf && (!size || (ReadFile(h, buf, (DWORD)size, &got, NULL) && got == (DWORD)size))) {
            buf[size] = 0;
            *len = (size_t)size;
        } else {
            free(buf);
            buf = NULL;
        }
    }
    CloseHandle(h);
    return buf;
}

static StMod *st_mod(const char *id) {
    for (int i = 0; i < g_nst; i++)
        if (!strcmp(g_st[i].id, id)) return &g_st[i];
    return NULL;
}

static StSetting *st_find(StMod *m, const char *key) {
    for (int i = 0; m && i < m->n; i++)
        if (!strcmp(m->s[i].key, key)) return &m->s[i];
    return NULL;
}

static int st_type_of(const char *t) {
    static const char *names[] = {"toggle", "slider", "choice", "key", "text", "button"};
    for (int i = 0; i < 6; i++)
        if (!strcmp(t, names[i])) return i;
    return -1;
}

static int st_applies_of(const char *t) {
    return !strcmp(t, "live") ? SWMOD_APPLIES_LIVE : !strcmp(t, "deploy") ? SWMOD_APPLIES_DEPLOY
                                                                           : SWMOD_APPLIES_RESTART;
}

// Split a line on tabs (in place): up to max fields.
static int st_fields(char *line, char **f, int max) {
    int n = 0;
    f[n++] = line;
    for (char *p = line; *p && n < max; p++)
        if (*p == '\t') {
            *p = 0;
            f[n++] = p + 1;
        }
    return n;
}

static void st_record(char **f, int n) {
    const char *k = f[0];
    if (!strcmp(k, "library") && n >= 2) {
        copy_str(g_st_library, sizeof g_st_library, f[1]);
    } else if (!strcmp(k, "mod") && n >= 5) {
        if (g_nst == ST_MAX_MODS || st_mod(f[1])) return;
        StMod *m = &g_st[g_nst];
        m->s = calloc(ST_MAX_SETTINGS, sizeof *m->s);
        if (!m->s) return;
        m->id = f[1];
        m->lib_id = strcmp(f[2], "-") ? f[2] : f[1];
        m->on = !strcmp(f[3], "on");
        m->name = f[4];
        g_nst++;
    } else if (n >= 3 && !strcmp(k, "on")) {
        if (g_nst_on < ST_MAX_ON) {
            g_st_on[g_nst_on].id = f[1];
            g_st_on[g_nst_on++].on = !strcmp(f[2], "1");
        }
    } else if (n >= 3) {
        StMod *m = st_mod(f[1]);
        if (!m) return;
        if (!strcmp(k, "dll")) {
            if (m->ndlls < ST_MAX_DLLS) m->dlls[m->ndlls++] = f[2];
        } else if (!strcmp(k, "page") && n >= 5) {
            if (m->npages < ST_MAX_PAGES) m->pages[m->npages++] = (StPage){f[2], f[3], f[4]};
        } else if (!strcmp(k, "set") && n >= 15) {
            int type = st_type_of(f[3]);
            if (type < 0 || m->n == ST_MAX_SETTINGS || st_find(m, f[2])) return;
            StSetting *s = &m->s[m->n];
            memset(s, 0, sizeof *s);
            s->key = f[2];
            s->type = type;
            s->applies = type == SWMOD_SETTING_BUTTON ? SWMOD_APPLIES_LIVE : st_applies_of(f[4]);
            s->page = strcmp(f[5], "-") ? f[5] : "";
            s->def_text = f[6];
            s->min = strtod(f[7], NULL);
            s->max = strtod(f[8], NULL);
            s->step = strtod(f[9], NULL);
            s->decimals = atoi(f[10]);
            if (s->decimals < 0 || s->decimals > 9) s->decimals = 0;
            s->max_length = atoi(f[11]);
            if (s->max_length < 0 || s->max_length > ST_TEXT - 1) s->max_length = ST_TEXT - 1;
            s->label = f[12];
            s->desc = f[13];
            s->unit = f[14];
            if (type == SWMOD_SETTING_SLIDER) {
                if (!(s->step > 0) || !(s->max > s->min)) return;  // (a broken line: the setting isn't there)
                int64_t steps = st_round((s->max - s->min) / s->step) + 1;
                s->steps = steps > 1000 ? 1000 : (int)steps;
            }
            if (type == SWMOD_SETTING_TEXT) {
                s->tcur = calloc(2, ST_TEXT);
                if (!s->tcur) return;
                s->tstart = s->tcur + ST_TEXT;
            }
            m->n++;
        } else if (!strcmp(k, "choice") && n >= 5) {
            StSetting *s = st_find(m, f[2]);
            if (!s || s->type != SWMOD_SETTING_CHOICE) return;
            if (!s->choices) s->choices = calloc(ST_MAX_CHOICES, sizeof *s->choices);
            if (!s->choices || s->nchoices == ST_MAX_CHOICES) return;
            s->choices[s->nchoices++] = (StChoice){f[3], f[4], NULL, 0};
        } else if (!strcmp(k, "sets") && n >= 6) {
            StSetting *s = st_find(m, f[2]);
            StChoice *c = NULL;
            for (int i = 0; s && i < s->nchoices; i++)
                if (!strcmp(s->choices[i].value, f[3])) c = &s->choices[i];
            if (!c) return;
            if (!c->sets) c->sets = calloc(ST_MAX_SETS, sizeof *c->sets);
            if (!c->sets || c->nsets == ST_MAX_SETS) return;
            c->sets[c->nsets++] = (StSet){f[4], f[5]};
        } else if (!strcmp(k, "if") && n >= 5) {
            StSetting *s = st_find(m, f[2]);
            if (!s) return;
            if (!strcmp(f[3], "show")) s->show_rpn = f[4];
            else if (!strcmp(f[3], "enable")) {
                s->enable_rpn = f[4];
                s->lock_words = n >= 6 && strcmp(f[5], "-") ? f[5] : "Locked: needs other settings changed first.";
            }
        } else if (!strcmp(k, "deployed") && n >= 4) {
            StSetting *s = st_find(m, f[2]);
            if (s) s->deployed_text = f[3];
        }
    }
}

// ------------------------------------------------------------------ the values file (a JSON subset)

typedef struct {
    int kind;             // 's' string, 'n' number, 'b' true/false, '0' null, 'o' object, 'a' array
    double num;
    bool b;
    char str[ST_TEXT * 2];
    const char *raw, *raw_end;  // the value's text in the file
} StJson;

typedef struct {
    const char *p, *end;
    int depth;
} StJp;

static void jp_ws(StJp *j) {
    while (j->p < j->end && (*j->p == ' ' || *j->p == '\t' || *j->p == '\r' || *j->p == '\n')) j->p++;
}

static bool jp_string(StJp *j, char *out, size_t cap) {
    if (j->p >= j->end || *j->p != '"') return false;
    j->p++;
    size_t n = 0;
    while (j->p < j->end && *j->p != '"') {
        char c = *j->p++;
        if (c == '\\') {
            if (j->p >= j->end) return false;
            c = *j->p++;
            if (c == 'u') {  // (only ASCII is kept; anything else becomes '?')
                unsigned v = 0;
                for (int i = 0; i < 4; i++, j->p++) {
                    if (j->p >= j->end) return false;
                    char h = *j->p;
                    v = v * 16 + (unsigned)(h >= '0' && h <= '9' ? h - '0' : h >= 'a' && h <= 'f' ? h - 'a' + 10
                                            : h >= 'A' && h <= 'F' ? h - 'A' + 10 : 0);
                }
                c = v >= 0x20 && v < 0x7F ? (char)v : '?';
            } else {
                c = c == 'n' ? '\n' : c == 't' ? '\t' : c == 'r' ? '\r' : c == 'b' ? '\b' : c == 'f' ? '\f' : c;
            }
        }
        if (out && n + 1 < cap) out[n++] = c;
    }
    if (j->p >= j->end) return false;
    j->p++;
    if (out && cap) out[n] = 0;
    return true;
}

static bool jp_value(StJp *j, StJson *v);

static bool jp_skip_container(StJp *j, char close) {
    if (++j->depth > 32) return false;
    j->p++;
    jp_ws(j);
    if (j->p < j->end && *j->p == close) {
        j->p++;
        j->depth--;
        return true;
    }
    for (;;) {
        StJson tmp;
        jp_ws(j);
        if (close == '}') {
            if (!jp_string(j, NULL, 0)) return false;
            jp_ws(j);
            if (j->p >= j->end || *j->p != ':') return false;
            j->p++;
            jp_ws(j);
        }
        if (!jp_value(j, &tmp)) return false;
        jp_ws(j);
        if (j->p < j->end && *j->p == ',') {
            j->p++;
            continue;
        }
        if (j->p < j->end && *j->p == close) {
            j->p++;
            j->depth--;
            return true;
        }
        return false;
    }
}

static bool jp_value(StJp *j, StJson *v) {
    jp_ws(j);
    if (j->p >= j->end) return false;
    v->raw = j->p;
    char c = *j->p;
    bool ok;
    if (c == '"') {
        v->kind = 's';
        ok = jp_string(j, v->str, sizeof v->str);
    } else if (c == '{' || c == '[') {
        v->kind = c == '{' ? 'o' : 'a';
        ok = jp_skip_container(j, c == '{' ? '}' : ']');
    } else if (j->end - j->p >= 4 && !memcmp(j->p, "true", 4)) {
        v->kind = 'b', v->b = true, j->p += 4, ok = true;
    } else if (j->end - j->p >= 5 && !memcmp(j->p, "false", 5)) {
        v->kind = 'b', v->b = false, j->p += 5, ok = true;
    } else if (j->end - j->p >= 4 && !memcmp(j->p, "null", 4)) {
        v->kind = '0', j->p += 4, ok = true;
    } else {
        char num[64];
        size_t n = 0;
        while (j->p < j->end && n + 1 < sizeof num && strchr("+-0123456789.eE", *j->p)) num[n++] = *j->p++;
        num[n] = 0;
        char *e;
        v->kind = 'n';
        v->num = strtod(num, &e);
        ok = n > 0 && !*e;
    }
    v->raw_end = j->p;
    return ok;
}

typedef void (*StJsonFn)(void *ctx, const char *key, const StJson *v);

// Call fn for each member of the file's "values" object. false: not a JSON object as written by us or modman.
static bool st_json_values(const char *text, size_t len, StJsonFn fn, void *ctx) {
    StJp j = {text, text + len, 0};
    jp_ws(&j);
    if (j.p >= j.end || *j.p != '{') return false;
    j.p++;
    for (;;) {
        jp_ws(&j);
        if (j.p < j.end && *j.p == '}') return true;
        char key[128];
        if (!jp_string(&j, key, sizeof key)) return false;
        jp_ws(&j);
        if (j.p >= j.end || *j.p != ':') return false;
        j.p++;
        jp_ws(&j);
        if (!strcmp(key, "values") && j.p < j.end && *j.p == '{') {
            j.p++;
            for (;;) {
                jp_ws(&j);
                if (j.p < j.end && *j.p == '}') {
                    j.p++;
                    break;
                }
                char k2[128];
                StJson v;
                if (!jp_string(&j, k2, sizeof k2)) return false;
                jp_ws(&j);
                if (j.p >= j.end || *j.p != ':') return false;
                j.p++;
                if (!jp_value(&j, &v)) return false;
                fn(ctx, k2, &v);
                jp_ws(&j);
                if (j.p < j.end && *j.p == ',') j.p++;
            }
        } else {
            StJson skip;
            if (!jp_value(&j, &skip)) return false;
        }
        jp_ws(&j);
        if (j.p < j.end && *j.p == ',') j.p++;
    }
}

static void st_values_path(const StMod *m, char *out, size_t cap, const char *suffix) {
    snprintf(out, cap, "%s\\settings\\%s.json%s", g_st_library, m->id, suffix);
}

// A value from the file -> the setting's index (or its text). false: it doesn't fit (the default stays).
static bool st_json_index(const StSetting *s, const StJson *v, int64_t *out, char *text) {
    char buf[64];
    switch (s->type) {
    case SWMOD_SETTING_TOGGLE:
        if (v->kind == 'b') return *out = v->b, true;
        if (v->kind == 'n') return *out = v->num != 0, true;
        return false;
    case SWMOD_SETTING_SLIDER:
        if (v->kind != 'n') return false;
        snprintf(buf, sizeof buf, "%.17g", v->num);
        return st_parse_index(s, buf, out);
    case SWMOD_SETTING_CHOICE:
    case SWMOD_SETTING_KEY:
        return v->kind == 's' && st_parse_index(s, v->str, out);
    case SWMOD_SETTING_TEXT:
        if (v->kind != 's' || !st_text_ok(v->str, s->max_length)) return false;
        copy_str(text, ST_TEXT, v->str);
        return true;
    default:
        return false;
    }
}

static void st_load_value(void *ctx, const char *key, const StJson *v) {
    StSetting *s = st_find((StMod *)ctx, key);
    if (!s || s->type == SWMOD_SETTING_BUTTON) return;
    int64_t i;
    char text[ST_TEXT];
    if (!st_json_index(s, v, &i, text)) return;
    if (s->type == SWMOD_SETTING_TEXT) copy_str(s->tcur, ST_TEXT, text);
    else s->cur = i;
}

// After settings.txt is read: defaults, the saved values, and what the game starts with.
static void st_init_values(StMod *m) {
    for (int k = 0; k < m->n; k++) {
        StSetting *s = &m->s[k];
        int64_t i = 0;
        if (s->type == SWMOD_SETTING_TEXT) {
            copy_str(s->tcur, ST_TEXT, st_text_ok(s->def_text, s->max_length) ? s->def_text : "");
        } else if (s->type != SWMOD_SETTING_BUTTON && st_parse_index(s, s->def_text, &i)) {
            s->def = i;
        }
        s->cur = s->def;
    }
    char path[PATH_U];
    st_values_path(m, path, sizeof path, "");
    size_t len = 0;
    char *text = *g_st_library ? st_read_file(path, ST_JSON_MAX, &len) : NULL;
    if (text) {
        if (!st_json_values(text, len, st_load_value, m))
            host_log("settings: %s is damaged; %s uses its defaults until a setting changes", path, m->name);
        free(text);
    }
    for (int k = 0; k < m->n; k++) {
        StSetting *s = &m->s[k];
        s->start = s->cur;
        if (s->type == SWMOD_SETTING_TEXT) copy_str(s->tstart, ST_TEXT, s->tcur);
        int64_t i;
        if (s->applies == SWMOD_APPLIES_DEPLOY && s->deployed_text && st_parse_index(s, s->deployed_text, &i))
            s->start = i;  // (the files this deploy used; the value the player picked since waits for Play)
        if (s->type == SWMOD_SETTING_BUTTON) s->cur = s->start = 0;
    }
}

static void st_load(void) {
    wchar_t w[PATH_W], full[PATH_W];
    if (env_w(L"SWMOD_SETTINGS", w, PATH_W) && full_path(w, full, PATH_W)) {
        to_utf8(full, g_st_path, sizeof g_st_path);
    } else if (*g_mod_dir) {
        snprintf(g_st_path, sizeof g_st_path, "%s\\..\\settings.txt", g_mod_dir);
    } else {
        return;
    }
    if (env_w(L"SWMOD_SETTINGS_WRITE_MS", w, 16)) {
        uint32_t ms = 0;
        for (const wchar_t *p = w; *p >= L'0' && *p <= L'9' && ms < 100000; p++) ms = ms * 10 + (uint32_t)(*p - L'0');
        g_st_write_ms = ms > 10000 ? 10000 : ms;
    }
    size_t len = 0;
    char *text = st_read_file(g_st_path, ST_TXT_MAX, &len);
    if (!text) {
        g_st_path[0] = 0;  // (no settings deployed: every mod uses its defaults)
        return;
    }
    char *line = text, *f[20];
    bool first = true;
    while (line && *line) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = 0;
        size_t n = strlen(line);
        if (n && line[n - 1] == '\r') line[n - 1] = 0;
        int nf = st_fields(line, f, 20);
        if (first) {
            if (strcmp(f[0], "swsettings") || nf < 2 || atoi(f[1]) != 1) {
                host_log("settings: %s isn't a settings file this modhost reads (deploy again)", g_st_path);
                free(text);
                g_st_path[0] = 0;
                return;
            }
            first = false;
        } else {
            st_record(f, nf);
        }
        line = nl ? nl + 1 : NULL;
    }
    // (text stays allocated: every string points into it)
    if (env_w(L"SWMOD_LIBRARY", w, PATH_W) && full_path(w, full, PATH_W)) to_utf8(full, g_st_library, sizeof g_st_library);
    for (int i = 0; i < g_nst; i++) st_init_values(&g_st[i]);
    g_st_event = CreateEventW(NULL, 0, 0, NULL);  // (auto-reset, not set)
    g_st_loaded = true;
    atexit(st_flush_at_exit);  // (a change made just before the game closes is saved too)
    int total = 0;
    for (int i = 0; i < g_nst; i++) total += g_st[i].n;
    host_log("settings: %d setting%s of %d mod%s from %s", total, total == 1 ? "" : "s", g_nst, g_nst == 1 ? "" : "s",
             g_st_path);
}

// ------------------------------------------------------------------ modhost's own settings (not a mod's)
// Read once at game start from the game's environment: Breathalyzer sets them from its Settings page (launcher.json),
// and modman run passes on any the player set by hand. They never change while the game runs.
//   SWMOD_FILE_LOG=1           file_log: write fslog.log, the files the game opens (fslog.c). OFF by default.
//   SWMOD_FILE_LOG_MAX=<bytes> the file log's size limit (4096 to 1 GiB; default 20 MiB)
//   SWMOD_FILE_LOG_RATE=<n>    the file log's lines a second at most (1 to 100000; default 1000)
#define ST_FILE_LOG_MAX (20ull << 20)
#define ST_FILE_LOG_RATE 1000u
static struct {
    bool file_log;
    uint64_t file_log_max;
    uint32_t file_log_rate;
} g_st_host = {false, ST_FILE_LOG_MAX, ST_FILE_LOG_RATE};

// A yes: 1, on, true or yes (any case).
static bool st_env_yes(const wchar_t *name) {
    wchar_t v[16];
    if (!env_w(name, v, 16)) return false;
    char a[16];
    size_t n = 0;
    for (; v[n] && n < sizeof a - 1; n++) a[n] = v[n] < 128 ? (char)v[n] : '?';
    a[n] = 0;
    return !strcmp(a, "1") || st_same_ci(a, "on") || st_same_ci(a, "true") || st_same_ci(a, "yes");
}

// A whole number from lo to hi, or def when unset or not one.
static uint64_t st_env_num(const wchar_t *name, uint64_t lo, uint64_t hi, uint64_t def) {
    wchar_t v[24];
    if (!env_w(name, v, 24)) return def;
    uint64_t x = 0;
    for (const wchar_t *p = v; *p; p++) {
        if (*p < L'0' || *p > L'9' || x > hi) return def;
        x = x * 10 + (uint64_t)(*p - L'0');
    }
    return x < lo || x > hi ? def : x;
}

static void st_host_load(void) {
    g_st_host.file_log = st_env_yes(L"SWMOD_FILE_LOG");
    g_st_host.file_log_max = st_env_num(L"SWMOD_FILE_LOG_MAX", 4096, 1ull << 30, ST_FILE_LOG_MAX);
    g_st_host.file_log_rate = (uint32_t)st_env_num(L"SWMOD_FILE_LOG_RATE", 1, 100000, ST_FILE_LOG_RATE);
}

// The settings mod a mod DLL belongs to (its file name is on a dll line), or -1.
static int st_for_dll(const char *file, const char *name) {
    for (int i = 0; i < g_nst; i++)
        for (int k = 0; k < g_st[i].ndlls; k++) {
            const char *d = g_st[i].dlls[k];
            char base[256];
            copy_str(base, sizeof base, d);
            size_t n = strlen(base);
            if (n > 4 && same_name(base + n - 4, ".dll")) base[n - 4] = 0;
            if (same_name(d, file) || same_name(base, name)) return i;
        }
    return -1;
}

// ------------------------------------------------------------------ the writer thread

// Append to out (cap bytes, *n used); what doesn't fit is dropped, and *n never passes cap - 1.
static void st_add(char *out, size_t cap, size_t *n, const char *fmt, ...) {
    if (*n + 1 >= cap) return;
    sw_va_list ap;
    sw_va_start(ap, fmt);
    int k = vsnprintf(out + *n, cap - *n, fmt, ap);
    sw_va_end(ap);
    if (k > 0) *n = *n + (size_t)k < cap ? *n + (size_t)k : cap - 1;
}

static void st_json_str(char *out, size_t cap, size_t *n, const char *s) {
    if (*n + 2 < cap) out[(*n)++] = '"';
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        char e[8];
        if (*p == '"' || *p == '\\') snprintf(e, sizeof e, "\\%c", *p);
        else if (*p < 0x20 || *p > 0x7E) snprintf(e, sizeof e, "\\u%04x", *p);
        else e[0] = (char)*p, e[1] = 0;
        size_t k = strlen(e);
        if (*n + k + 2 < cap) memcpy(out + *n, e, k), *n += k;
    }
    if (*n + 1 < cap) out[(*n)++] = '"';
}

typedef struct {
    StMod *m;
    char *out;
    size_t cap, *n;
    int count;
} StKeep;

static void st_keep_unknown(void *ctx, const char *key, const StJson *v) {
    StKeep *k = ctx;
    StSetting *s = st_find(k->m, key);
    if (s && s->type != SWMOD_SETTING_BUTTON) return;  // (ours: written from the values now)
    size_t raw = (size_t)(v->raw_end - v->raw);
    if (*k->n + strlen(key) * 6 + raw + 16 >= k->cap) return;
    st_add(k->out, k->cap, k->n, "%s\n  ", k->count++ ? "," : "");
    st_json_str(k->out, k->cap, k->n, key);
    st_add(k->out, k->cap, k->n, ": %.*s", (int)raw, v->raw);
}

static bool st_write_file(const char *upath, const char *data, size_t len) {
    wchar_t w[PATH_W], tw[PATH_W];
    char tmp[PATH_U];
    snprintf(tmp, sizeof tmp, "%s.tmp", upath);
    if (!MultiByteToWideChar(SW_CP_UTF8, 0, upath, -1, w, PATH_W) ||
        !MultiByteToWideChar(SW_CP_UTF8, 0, tmp, -1, tw, PATH_W))
        return false;
    HANDLE h = CreateFileW(tw, ST_GENERIC_WRITE, 0, NULL, ST_CREATE_ALWAYS, ST_ATTR_NORMAL, NULL);
    if (h == SW_INVALID_HANDLE) return false;
    DWORD put = 0;
    bool ok = WriteFile(h, data, (DWORD)len, &put, NULL) && put == (DWORD)len && FlushFileBuffers(h);
    CloseHandle(h);
    if (ok) ok = MoveFileExW(tw, w, ST_MOVE_REPLACE | ST_MOVE_WRITE_THROUGH);
    if (!ok) DeleteFileW(tw);
    return ok;
}

// Write one mod's values file: the values that differ from the defaults, plus the keys the file had that this
// modhost doesn't know (a newer mod's), as they were.
static void st_save(StMod *m) {
    char path[PATH_U], dir[PATH_U];
    wchar_t wdir[PATH_W];
    snprintf(dir, sizeof dir, "%s\\settings", g_st_library);
    if (MultiByteToWideChar(SW_CP_UTF8, 0, dir, -1, wdir, PATH_W)) CreateDirectoryW(wdir, NULL);
    st_values_path(m, path, sizeof path, "");
    size_t cap = 64 * 1024 + (size_t)m->n * 256, n = 0;
    char *out = malloc(cap);
    if (!out) return;
    st_add(out, cap, &n, "{\n \"format\": 1,\n \"mod\": ");
    st_json_str(out, cap, &n, m->id);
    st_add(out, cap, &n, ",\n \"values\": {");
    StKeep keep = {m, out, cap, &n, 0};
    size_t len = 0;
    char *old = st_read_file(path, ST_JSON_MAX, &len);
    if (old) {
        st_json_values(old, len, st_keep_unknown, &keep);
        free(old);
    }
    for (int k = 0; k < m->n; k++) {
        StSetting *s = &m->s[k];
        if (s->type == SWMOD_SETTING_BUTTON) continue;
        int64_t v = __atomic_load_n(&s->cur, __ATOMIC_ACQUIRE);
        char text[ST_TEXT];
        if (s->type == SWMOD_SETTING_TEXT) {
            st_text_lock();
            copy_str(text, sizeof text, s->tcur);
            st_text_unlock();
            if (!strcmp(text, st_text_ok(s->def_text, s->max_length) ? s->def_text : "")) continue;
        } else if (v == s->def) {
            continue;
        }
        st_add(out, cap, &n, "%s\n  ", keep.count++ ? "," : "");
        st_json_str(out, cap, &n, s->key);
        st_add(out, cap, &n, ": ");
        char buf[96];
        switch (s->type) {
        case SWMOD_SETTING_TOGGLE:
            st_add(out, cap, &n, "%s", v ? "true" : "false");
            break;
        case SWMOD_SETTING_SLIDER:
            st_add(out, cap, &n, "%.*f", s->decimals, st_slider_value(s, v));
            break;
        case SWMOD_SETTING_CHOICE:
            st_json_str(out, cap, &n, v >= 0 && v < s->nchoices ? s->choices[v].value : "");
            break;
        case SWMOD_SETTING_KEY:
            st_key_name((int)v, buf, sizeof buf);
            st_json_str(out, cap, &n, buf);
            break;
        default:
            st_json_str(out, cap, &n, text);
        }
    }
    st_add(out, cap, &n, "%s}\n}\n", keep.count ? "\n " : "");
    if (st_write_file(path, out, n)) {
        __atomic_fetch_add(&g_st_writes, 1, __ATOMIC_RELAXED);
    } else {
        __atomic_fetch_add(&g_st_write_fails, 1, __ATOMIC_RELAXED);
        if (!g_st_exiting)  // (at exit Savage Wind's log may be closed already)
            host_log("settings: can't write %s (error %lu); the change stays until the game closes", path,
                     GetLastError());
    }
    free(out);
}

// When the game closes: save the changes the writer thread hadn't saved yet (made in its last g_st_write_ms). Runs
// from modhost's exit list (atexit), when the other threads are gone, so the text lock is only checked: a thread that
// ended while holding it can't let go of it any more, and then nothing is written.
static void st_flush_at_exit(void) {
    g_st_exiting = true;
    if (!g_st_loaded || !*g_st_library || __atomic_load_n(&g_st_text_lock, __ATOMIC_ACQUIRE)) return;
    for (int i = 0; i < g_nst; i++)
        if (__atomic_exchange_n(&g_st[i].dirty, 0, __ATOMIC_ACQ_REL)) st_save(&g_st[i]);
}

static DWORD WINAPI st_writer(void *arg) {
    (void)arg;
    for (;;) {
        WaitForSingleObject(g_st_event, ST_INFINITE);
        if (g_st_write_ms) Sleep(g_st_write_ms);  // (a row of changes, one write)
        for (int i = 0; i < g_nst; i++)
            if (__atomic_exchange_n(&g_st[i].dirty, 0, __ATOMIC_ACQ_REL)) st_save(&g_st[i]);
    }
    return 0;
}

// A value of mod m changed: count it, and have the writer save the file (started with the first change).
static void st_changed(StMod *m, bool save) {
    __atomic_fetch_add(&g_st_serial, 1, __ATOMIC_ACQ_REL);
    if (!save || !*g_st_library || !g_st_event) return;
    __atomic_store_n(&m->dirty, 1, __ATOMIC_RELEASE);
    if (!__atomic_exchange_n(&g_st_writer, 1, __ATOMIC_ACQ_REL)) {
        HANDLE t = CreateThread(NULL, 0, st_writer, NULL, 0, NULL);
        if (t) CloseHandle(t);
        else host_log("settings: can't start the writer thread; changes are not saved");
    }
    SetEvent(g_st_event);
}

// ------------------------------------------------------------------ conditions

typedef struct {
    bool is_str;
    double n;
    char s[ST_TEXT];
} StVal;

static bool st_truthy(const StVal *v) { return v->is_str ? v->s[0] != 0 : v->n != 0; }

static bool st_mod_on(const char *id) {
    for (int i = 0; i < g_nst_on; i++)
        if (!strcmp(g_st_on[i].id, id)) return g_st_on[i].on;
    for (int i = 0; i < g_nst; i++)
        if (!strcmp(g_st[i].id, id) || !strcmp(g_st[i].lib_id, id)) return g_st[i].on;
    return false;
}

static void st_value_of(const StSetting *s, StVal *v) {
    int64_t i = __atomic_load_n(&s->cur, __ATOMIC_ACQUIRE);
    memset(v, 0, sizeof *v);
    switch (s->type) {
    case SWMOD_SETTING_TOGGLE:
        v->n = i != 0;
        break;
    case SWMOD_SETTING_SLIDER:
        v->n = st_slider_value(s, i);
        break;
    case SWMOD_SETTING_CHOICE:
        v->is_str = true;
        copy_str(v->s, sizeof v->s, i >= 0 && i < s->nchoices ? s->choices[i].value : "");
        break;
    case SWMOD_SETTING_KEY:
        v->is_str = true;
        st_key_name((int)i, v->s, sizeof v->s);
        break;
    case SWMOD_SETTING_TEXT:
        v->is_str = true;
        st_text_lock();
        copy_str(v->s, sizeof v->s, s->tcur);
        st_text_unlock();
        break;
    default:
        v->n = (double)i;
    }
}

// 1 true, 0 false, -1 a token list that doesn't evaluate (the caller then counts it as true).
static int st_eval(const StMod *m, const char *rpn) {
    StVal st[MAX_RPN_STACK];
    int sp = 0;
    char tok[80];
    for (const char *p = rpn; *p;) {
        while (*p == ' ') p++;
        size_t n = strcspn(p, " ");
        if (!n) break;
        if (n >= sizeof tok) return -1;
        memcpy(tok, p, n);
        tok[n] = 0;
        p += n;
        if (tok[1] == ':' && strchr("knsbm", tok[0])) {
            if (sp == MAX_RPN_STACK) return -1;
            StVal *v = &st[sp++];
            memset(v, 0, sizeof *v);
            if (tok[0] == 'k') {
                const StSetting *s = st_find((StMod *)m, tok + 2);
                if (!s || s->type == SWMOD_SETTING_BUTTON) return -1;
                st_value_of(s, v);
            } else if (tok[0] == 'm') {
                v->n = st_mod_on(tok + 2);
            } else if (tok[0] == 's') {
                v->is_str = true;
                copy_str(v->s, sizeof v->s, tok + 2);
            } else if (tok[0] == 'b') {
                v->n = tok[2] == '1';
            } else {
                char *e;
                v->n = strtod(tok + 2, &e);
                if (*e) return -1;
            }
            continue;
        }
        if (!strcmp(tok, "!")) {
            if (sp < 1) return -1;
            bool t = st_truthy(&st[sp - 1]);
            memset(&st[sp - 1], 0, sizeof st[0]);
            st[sp - 1].n = !t;
            continue;
        }
        if (sp < 2) return -1;
        StVal *b = &st[--sp], *a = &st[sp - 1];
        bool r;
        if (!strcmp(tok, "&&") || !strcmp(tok, "||")) {
            bool x = st_truthy(a), y = st_truthy(b);
            r = tok[0] == '&' ? x && y : x || y;
        } else {
            if (a->is_str != b->is_str) return -1;
            int c = a->is_str ? strcmp(a->s, b->s) : st_num_equal(a->n, b->n) ? 0 : a->n > b->n ? 1 : -1;
            c = c > 0 ? 1 : c < 0 ? -1 : 0;
            if (!strcmp(tok, "==")) r = c == 0;
            else if (!strcmp(tok, "!=")) r = c != 0;
            else if (!strcmp(tok, "<")) r = c < 0;
            else if (!strcmp(tok, "<=")) r = c <= 0;
            else if (!strcmp(tok, ">")) r = c > 0;
            else if (!strcmp(tok, ">=")) r = c >= 0;
            else return -1;
        }
        memset(a, 0, sizeof *a);
        a->n = r;
    }
    return sp == 1 ? st_truthy(&st[0]) : -1;
}

static bool st_cond(const StMod *m, const char *rpn) { return !rpn || st_eval(m, rpn) != 0; }

// ------------------------------------------------------------------ changing values

static StMod *st_at(int m) { return m >= 0 && m < g_nst ? &g_st[m] : NULL; }
static StSetting *st_set_at(int m, int s) {
    StMod *x = st_at(m);
    return x && s >= 0 && s < x->n ? &x->s[s] : NULL;
}

// Call the mod's watchers (key NULL: several settings changed). On the thread that made the change.
static void st_notify(int st, const char *key) {
    uint32_t i = gate_enter(&g_st_gate);
    uint32_t n = __atomic_load_n(&g_st_nwatch, __ATOMIC_ACQUIRE);
    for (uint32_t k = 0; k < n; k++) {
        StWatch *w = &g_st_watch[k];
        if (__atomic_load_n(&w->active, __ATOMIC_ACQUIRE) && w->st == st && w->fn) w->fn(w->api, key, w->user);
    }
    gate_leave(&g_st_gate, i);
}

// Set setting s of mod m to index v (no checks), and a choice's `sets`. Returns how many settings changed; *live is
// set when one of them applies live, *only the one key when exactly one changed.
static int st_assign(StMod *m, StSetting *s, int64_t v, bool *live, const char **only) {
    int changed = 0;
    if (__atomic_exchange_n(&s->cur, v, __ATOMIC_ACQ_REL) != v) {
        changed++;
        *only = s->key;
        *live |= s->applies == SWMOD_APPLIES_LIVE;
    }
    if (s->type == SWMOD_SETTING_CHOICE && v >= 0 && v < s->nchoices) {
        const StChoice *c = &s->choices[v];
        for (int k = 0; k < c->nsets; k++) {
            StSetting *o = st_find(m, c->sets[k].key);
            int64_t i;
            if (!o || o == s || o->type == SWMOD_SETTING_TEXT || o->type == SWMOD_SETTING_BUTTON ||
                !st_parse_index(o, c->sets[k].text, &i))
                continue;
            if (__atomic_exchange_n(&o->cur, i, __ATOMIC_ACQ_REL) != i) {
                changed++;
                *only = changed == 1 ? o->key : NULL;
                *live |= o->applies == SWMOD_APPLIES_LIVE;
            }
        }
    }
    if (changed > 1) *only = NULL;
    return changed;
}

// A change from a settings screen or the console: checked, `sets` applied, saved, and the watchers told.
static int st_ui_set(int mi, int si, int64_t v) {
    StMod *m = st_at(mi);
    StSetting *s = st_set_at(mi, si);
    if (!s) return SWMOD_SET_NO_SETTING;
    if (s->type == SWMOD_SETTING_TEXT || s->type == SWMOD_SETTING_BUTTON) return SWMOD_SET_READ_ONLY;
    int64_t count = s->type == SWMOD_SETTING_TOGGLE ? 2 : s->type == SWMOD_SETTING_SLIDER ? s->steps
                    : s->type == SWMOD_SETTING_CHOICE ? s->nchoices : 256;
    if (v < 0 || v >= count) return SWMOD_SET_BAD_VALUE;
    if (!st_cond(m, s->enable_rpn)) return SWMOD_SET_LOCKED;
    bool live = false;
    const char *only = NULL;
    if (st_assign(m, s, v, &live, &only)) {
        st_changed(m, true);
        if (live) st_notify(mi, only);
    }
    return SWMOD_SET_OK;
}

// ------------------------------------------------------------------ SwModSettingsUi

static uint32_t ui_serial(void) { return __atomic_load_n(&g_st_serial, __ATOMIC_ACQUIRE); }
static int ui_mods(void) { return g_nst; }
static const char *ui_mod_id(int m) { return st_at(m) ? g_st[m].id : NULL; }
static const char *ui_mod_lib_id(int m) { return st_at(m) ? g_st[m].lib_id : NULL; }
static const char *ui_mod_name(int m) { return st_at(m) ? g_st[m].name : NULL; }
static bool ui_mod_on(int m) { return st_at(m) && g_st[m].on; }
static int ui_count(int m) { return st_at(m) ? g_st[m].n : 0; }
static int ui_page_count(int m) { return st_at(m) ? g_st[m].npages : 0; }

static bool ui_page_info(int m, int p, const char **key, const char **label, const char **desc) {
    StMod *x = st_at(m);
    if (!x || p < 0 || p >= x->npages) return false;
    if (key) *key = x->pages[p].key;
    if (label) *label = x->pages[p].label;
    if (desc) *desc = x->pages[p].desc;
    return true;
}

static bool ui_info(int m, int si, SwModSettingInfo *out) {
    StSetting *s = st_set_at(m, si);
    if (!out) return false;
    size_t want = out->size;
    if (!s || want < offsetof(SwModSettingInfo, type) + sizeof out->type) return false;
    SwModSettingInfo i;
    memset(&i, 0, sizeof i);
    i.size = (uint32_t)(want < sizeof i ? want : sizeof i);
    i.type = s->type;
    i.applies = s->applies;
    i.values = s->type == SWMOD_SETTING_TOGGLE ? 2 : s->type == SWMOD_SETTING_SLIDER ? s->steps
               : s->type == SWMOD_SETTING_CHOICE ? s->nchoices : 0;
    i.key = s->key;
    i.label = s->label;
    i.desc = s->desc;
    i.page = s->page;
    i.unit = s->unit;
    i.min = s->min;
    i.max = s->max;
    i.step = s->step;
    i.max_length = s->max_length;
    i.default_index = (int)s->def;
    memcpy(out, &i, i.size);
    return true;
}

static int ui_find(int m, const char *key) {
    StMod *x = st_at(m);
    StSetting *s = key ? st_find(x, key) : NULL;
    return s ? (int)(s - x->s) : -1;
}

static int ui_get_index(int m, int si) {
    StSetting *s = st_set_at(m, si);
    return s ? (int)__atomic_load_n(&s->cur, __ATOMIC_ACQUIRE) : -1;
}

static int ui_value_text(int m, int si, int index, char *buf, size_t cap) {
    StSetting *s = st_set_at(m, si);
    char tmp[160];
    if (!buf || !cap) return -1;
    buf[0] = 0;
    if (!s) return -1;
    int64_t v = index < 0 ? __atomic_load_n(&s->cur, __ATOMIC_ACQUIRE) : index;
    switch (s->type) {
    case SWMOD_SETTING_TOGGLE:
        copy_str(tmp, sizeof tmp, v ? "ON" : "OFF");
        break;
    case SWMOD_SETTING_SLIDER:
        snprintf(tmp, sizeof tmp, "%.*f%s", s->decimals, st_slider_value(s, v), s->unit);
        break;
    case SWMOD_SETTING_CHOICE:
        copy_str(tmp, sizeof tmp, v >= 0 && v < s->nchoices ? s->choices[v].label : "");
        break;
    case SWMOD_SETTING_KEY:
        st_key_name((int)v, tmp, sizeof tmp);
        if (!tmp[0]) copy_str(tmp, sizeof tmp, "None");
        break;
    case SWMOD_SETTING_TEXT:
        st_text_lock();
        copy_str(tmp, sizeof tmp, s->tcur);
        st_text_unlock();
        break;
    default:
        copy_str(tmp, sizeof tmp, s->unit);
    }
    copy_str(buf, cap, tmp);
    return (int)strlen(tmp);
}

static int ui_set_index(int m, int s, int index) { return st_ui_set(m, s, index); }

static int ui_set_key(int m, int si, const char *name) {
    StSetting *s = st_set_at(m, si);
    if (!s) return SWMOD_SET_NO_SETTING;
    if (s->type != SWMOD_SETTING_KEY) return SWMOD_SET_BAD_VALUE;
    int vk = st_key_vk(name);
    if (vk < 0) return SWMOD_SET_BAD_VALUE;
    return st_ui_set(m, si, vk);
}

static bool ui_shown(int m, int si) {
    StSetting *s = st_set_at(m, si);
    return s && st_cond(&g_st[m], s->show_rpn);
}

static bool ui_enabled(int m, int si) {
    StSetting *s = st_set_at(m, si);
    return s && st_cond(&g_st[m], s->enable_rpn);
}

static int ui_lock_text(int m, int si, char *buf, size_t cap) {
    StSetting *s = st_set_at(m, si);
    if (!buf || !cap) return -1;
    buf[0] = 0;
    if (!s) return -1;
    if (st_cond(&g_st[m], s->enable_rpn)) return 0;
    copy_str(buf, cap, s->lock_words ? s->lock_words : "Locked.");
    return (int)strlen(s->lock_words ? s->lock_words : "Locked.");
}

static bool ui_waiting(int m, int si) {
    StSetting *s = st_set_at(m, si);
    if (!s || s->applies == SWMOD_APPLIES_LIVE || s->type == SWMOD_SETTING_BUTTON) return false;
    if (s->type == SWMOD_SETTING_TEXT) {
        st_text_lock();
        bool w = strcmp(s->tcur, s->tstart) != 0;
        st_text_unlock();
        return w;
    }
    return __atomic_load_n(&s->cur, __ATOMIC_ACQUIRE) != s->start;
}

static int ui_press(int m, int si) {
    StSetting *s = st_set_at(m, si);
    if (!s) return SWMOD_SET_NO_SETTING;
    if (s->type != SWMOD_SETTING_BUTTON) return SWMOD_SET_BAD_VALUE;
    if (!st_cond(&g_st[m], s->enable_rpn)) return SWMOD_SET_LOCKED;
    __atomic_fetch_add(&s->cur, 1, __ATOMIC_ACQ_REL);
    st_changed(&g_st[m], false);
    st_notify(m, s->key);
    return SWMOD_SET_OK;
}

static int ui_reset(int mi) {
    StMod *m = st_at(mi);
    if (!m) return SWMOD_SET_NO_SETTING;
    bool live = false;
    int changed = 0;
    for (int k = 0; k < m->n; k++) {
        StSetting *s = &m->s[k];
        if (s->type == SWMOD_SETTING_BUTTON) continue;
        if (s->type == SWMOD_SETTING_TEXT) {
            const char *d = st_text_ok(s->def_text, s->max_length) ? s->def_text : "";
            st_text_lock();
            bool diff = strcmp(s->tcur, d) != 0;
            copy_str(s->tcur, ST_TEXT, d);
            st_text_unlock();
            changed += diff;
            live |= diff && s->applies == SWMOD_APPLIES_LIVE;
        } else if (__atomic_exchange_n(&s->cur, s->def, __ATOMIC_ACQ_REL) != s->def) {
            changed++;
            live |= s->applies == SWMOD_APPLIES_LIVE;
        }
    }
    if (changed) {
        st_changed(m, true);
        if (live) st_notify(mi, NULL);
    }
    return SWMOD_SET_OK;
}

static const SwModSettingsUi g_st_ui = {
    sizeof(SwModSettingsUi), ui_serial, ui_mods, ui_mod_id, ui_mod_lib_id, ui_mod_name, ui_mod_on, ui_count,
    ui_page_count, ui_page_info, ui_info, ui_find, ui_get_index, ui_value_text, ui_set_index, ui_set_key, ui_shown,
    ui_enabled, ui_lock_text, ui_waiting, ui_press, ui_reset,
};

static const SwModSettingsUi *api_settings_ui(void) { return g_st_loaded ? &g_st_ui : NULL; }

// ------------------------------------------------------------------ the mod's own API (SwModApi version 3)

static Mod *st_mod_of_api(const SwModApi *self) {
    for (int i = 0; i < MAX_MODS; i++)
        if (g_mods[i].used && &g_mods[i].api == self) return &g_mods[i];
    return NULL;
}

static StSetting *st_own(const SwModApi *self, const char *key, int *st) {
    Mod *m = self && key ? st_mod_of_api(self) : NULL;
    if (!m || m->settings < 0 || m->settings >= g_nst) return NULL;
    if (st) *st = m->settings;
    return st_find(&g_st[m->settings], key);
}

// What a getter returns: the start value for restart / deploy settings, else the value now.
static int64_t st_get(const StSetting *s) {
    return s->applies == SWMOD_APPLIES_LIVE || s->type == SWMOD_SETTING_BUTTON
               ? __atomic_load_n(&s->cur, __ATOMIC_ACQUIRE)
               : s->start;
}

static int64_t api_setting_int(const SwModApi *self, const char *key, int64_t def) {
    StSetting *s = st_own(self, key, NULL);
    if (!s || s->type == SWMOD_SETTING_TEXT) return def;
    int64_t v = st_get(s);
    return s->type == SWMOD_SETTING_SLIDER ? st_round(st_slider_value(s, v)) : v;
}

static double api_setting_num(const SwModApi *self, const char *key, double def) {
    StSetting *s = st_own(self, key, NULL);
    if (!s || s->type == SWMOD_SETTING_TEXT) return def;
    int64_t v = st_get(s);
    return s->type == SWMOD_SETTING_SLIDER ? st_slider_value(s, v) : (double)v;
}

static int api_setting_str(const SwModApi *self, const char *key, char *buf, size_t cap) {
    StSetting *s = st_own(self, key, NULL);
    char tmp[160];
    if (buf && cap) buf[0] = 0;
    if (!s) return -1;
    int64_t v = st_get(s);
    switch (s->type) {
    case SWMOD_SETTING_TOGGLE:
        copy_str(tmp, sizeof tmp, v ? "true" : "false");
        break;
    case SWMOD_SETTING_SLIDER:
        snprintf(tmp, sizeof tmp, "%.*f", s->decimals, st_slider_value(s, v));
        break;
    case SWMOD_SETTING_CHOICE:
        copy_str(tmp, sizeof tmp, v >= 0 && v < s->nchoices ? s->choices[v].value : "");
        break;
    case SWMOD_SETTING_KEY:
        st_key_name((int)v, tmp, sizeof tmp);
        break;
    case SWMOD_SETTING_TEXT:
        st_text_lock();
        copy_str(tmp, sizeof tmp, s->applies == SWMOD_APPLIES_LIVE ? s->tcur : s->tstart);
        st_text_unlock();
        break;
    default:
        snprintf(tmp, sizeof tmp, "%lld", (long long)v);
    }
    if (buf && cap) copy_str(buf, cap, tmp);
    return (int)strlen(tmp);
}

static int api_setting_watch(const SwModApi *self, SwModSettingFn fn, void *user) {
    Mod *m = self ? st_mod_of_api(self) : NULL;
    Mod *caller = calling_mod();
    if (!m || !fn) return SWMOD_SET_NO_SETTING;
    if (caller != m) {
        host_log("mod %s: setting_watch refused: watchers can only be added from swmod_init", m->file);
        return SWMOD_SET_NO_SETTING;
    }
    if (m->settings < 0) return SWMOD_SET_NO_SETTING;
    StMod *x = &g_st[m->settings];
    if (x->nwatch >= ST_MAX_WATCH) {
        host_log("mod %s: setting_watch refused: %d watchers at most", m->file, ST_MAX_WATCH);
        return SWMOD_SET_NO_SETTING;
    }
    uint32_t n = __atomic_load_n(&g_st_nwatch, __ATOMIC_ACQUIRE), k;
    for (k = 0; k < n && g_st_watch[k].mod; k++) {
    }
    if (k == ST_WATCH_SLOTS) return SWMOD_SET_NO_SETTING;
    StWatch *w = &g_st_watch[k];
    w->fn = fn;
    w->user = user;
    w->api = self;
    w->st = m->settings;
    w->mod = m->id;
    __atomic_store_n(&w->active, 1, __ATOMIC_RELEASE);
    if (k == n) __atomic_store_n(&g_st_nwatch, n + 1, __ATOMIC_RELEASE);
    x->nwatch++;
    return SWMOD_SET_OK;
}

static int api_setting_set(const SwModApi *self, const char *key, const char *value) {
    int st = -1;
    StSetting *s = st_own(self, key, &st);
    if (!s) return SWMOD_SET_NO_SETTING;
    if (!value || s->type == SWMOD_SETTING_BUTTON) return SWMOD_SET_BAD_VALUE;
    StMod *m = &g_st[st];
    if (s->type == SWMOD_SETTING_TEXT) {
        if (!st_text_ok(value, s->max_length)) return SWMOD_SET_BAD_VALUE;
        st_text_lock();
        bool diff = strcmp(s->tcur, value) != 0;
        copy_str(s->tcur, ST_TEXT, value);
        st_text_unlock();
        if (diff) st_changed(m, true);
        return SWMOD_SET_OK;
    }
    int64_t v;
    if (!st_parse_index(s, value, &v)) return SWMOD_SET_BAD_VALUE;
    bool live = false;
    const char *only = NULL;
    if (st_assign(m, s, v, &live, &only)) st_changed(m, true);  // (the mod's own change: no watchers)
    return SWMOD_SET_OK;
}

// modhost: a mod gets its settings (by its DLL's file name) when it starts.
static void st_attach(Mod *m) {
    m->settings = g_st_loaded ? st_for_dll(m->file, m->name) : -1;
    m->api.mod_id = m->settings >= 0 ? g_st[m->settings].id : NULL;
}

// modunload: take the mod's watchers out and wait until no thread runs one. false: one still runs (they are back).
static bool st_detach_watchers(Mod *m, uint64_t until) {
    uint32_t n = __atomic_load_n(&g_st_nwatch, __ATOMIC_ACQUIRE);
    bool any = false;
    for (uint32_t k = 0; k < n; k++)
        if (g_st_watch[k].mod == m->id) {
            __atomic_store_n(&g_st_watch[k].active, 0, __ATOMIC_SEQ_CST);
            any = true;
        }
    if (!any) return true;
    if (gate_sync(&g_st_gate, until)) return true;
    for (uint32_t k = 0; k < n; k++)
        if (g_st_watch[k].mod == m->id) __atomic_store_n(&g_st_watch[k].active, 1, __ATOMIC_SEQ_CST);
    return false;
}

// modunload went through: the slots are free again.
static void st_forget(Mod *m) {
    uint32_t n = __atomic_load_n(&g_st_nwatch, __ATOMIC_ACQUIRE);
    for (uint32_t k = 0; k < n; k++)
        if (g_st_watch[k].mod == m->id) {
            if (g_st_watch[k].st >= 0 && g_st_watch[k].st < g_nst) g_st[g_st_watch[k].st].nwatch--;
            memset(&g_st_watch[k], 0, sizeof g_st_watch[k]);
        }
}

// ------------------------------------------------------------------ console commands

static int st_mod_by_ref(const char *ref) {
    for (int i = 0; i < g_nst; i++)
        if (!strcmp(g_st[i].id, ref) || same_name(g_st[i].lib_id, ref)) return i;
    for (int i = 0; i < g_nst; i++)
        if (same_name(g_st[i].name, ref)) return i;
    return -1;
}

static void cmd_modsettings(int argc, char **argv, ControlReply *r) {
    if (!g_st_loaded) {
        control_field_int(r, "mods", 0);
        control_text(r, "No mod settings: no settings.txt was deployed (Mod Manager writes it when a mod has "
                        "settings)");
        return;
    }
    if (argc < 2) {
        control_field_int(r, "mods", g_nst);
        control_field_str(r, "file", g_st_path);
        control_field_str(r, "library", g_st_library);
        control_field_bool(r, "writer", __atomic_load_n(&g_st_writer, __ATOMIC_ACQUIRE) != 0);
        control_field_int(r, "writes", __atomic_load_n(&g_st_writes, __ATOMIC_ACQUIRE));
        control_field_int(r, "serial", ui_serial());
        control_text(r, "%d mod%s with settings (modsettings <mod> lists one mod's):", g_nst, g_nst == 1 ? "" : "s");
        for (int i = 0; i < g_nst; i++) {
            char key[64];
            snprintf(key, sizeof key, "mods.%d.id", i);
            control_field_str(r, key, g_st[i].id);
            snprintf(key, sizeof key, "mods.%d.name", i);
            control_field_str(r, key, g_st[i].name);
            snprintf(key, sizeof key, "mods.%d.count", i);
            control_field_int(r, key, g_st[i].n);
            control_text(r, "  %s (%s): %d setting%s%s", g_st[i].name, g_st[i].id, g_st[i].n,
                         g_st[i].n == 1 ? "" : "s", g_st[i].on ? "" : ", mod off");
        }
        return;
    }
    int mi = st_mod_by_ref(argv[1]);
    if (mi < 0) {
        control_fail(r, "no mod %s with settings (modsettings lists them)", argv[1]);
        return;
    }
    StMod *m = &g_st[mi];
    control_field_str(r, "id", m->id);
    control_field_str(r, "name", m->name);
    control_field_int(r, "count", m->n);
    control_text(r, "%s:", m->name);
    for (int k = 0; k < m->n; k++) {
        StSetting *s = &m->s[k];
        char key[80], val[160], lock[160];
        ui_value_text(mi, k, -1, val, sizeof val);
        bool shown = ui_shown(mi, k), on = ui_enabled(mi, k), wait = ui_waiting(mi, k);
        ui_lock_text(mi, k, lock, sizeof lock);
        snprintf(key, sizeof key, "settings.%d.key", k);
        control_field_str(r, key, s->key);
        snprintf(key, sizeof key, "settings.%d.value", k);
        control_field_str(r, key, val);
        snprintf(key, sizeof key, "settings.%d.index", k);
        control_field_int(r, key, __atomic_load_n(&s->cur, __ATOMIC_ACQUIRE));
        snprintf(key, sizeof key, "settings.%d.start", k);
        control_field_int(r, key, s->start);
        snprintf(key, sizeof key, "settings.%d.shown", k);
        control_field_bool(r, key, shown);
        snprintf(key, sizeof key, "settings.%d.enabled", k);
        control_field_bool(r, key, on);
        snprintf(key, sizeof key, "settings.%d.waiting", k);
        control_field_bool(r, key, wait);
        control_text(r, "  %s = %s%s%s%s%s", s->label, val, wait ? " (at your next start)" : "",
                     shown ? "" : " (hidden)", *lock ? "  " : "", lock);
    }
}

static void cmd_modset(int argc, char **argv, ControlReply *r) {
    if (argc < 3) {
        control_fail(r, "usage: modset <mod> <key> <value>  (a button: no value; modset <mod> reset: all defaults)");
        return;
    }
    if (!g_st_loaded) {
        control_fail(r, "no mod settings: no settings.txt was deployed");
        return;
    }
    int mi = st_mod_by_ref(argv[1]);
    if (mi < 0) {
        control_fail(r, "no mod %s with settings (modsettings lists them)", argv[1]);
        return;
    }
    int si = ui_find(mi, argv[2]);
    int rc;
    if (si < 0 && !strcmp(argv[2], "reset")) {
        rc = ui_reset(mi);
    } else if (si < 0) {
        control_fail(r, "%s has no setting %s (modsettings %s lists them)", g_st[mi].name, argv[2], argv[1]);
        return;
    } else {
        StSetting *s = &g_st[mi].s[si];
        char value[160];
        join_args(3, argc, argv, value, sizeof value);
        int64_t v;
        if (s->type == SWMOD_SETTING_BUTTON) rc = ui_press(mi, si);
        else if (s->type == SWMOD_SETTING_TEXT) rc = SWMOD_SET_READ_ONLY;
        else if (argc < 4 && s->type != SWMOD_SETTING_KEY) rc = SWMOD_SET_BAD_VALUE;
        else rc = st_parse_index(s, value, &v) ? st_ui_set(mi, si, v) : SWMOD_SET_BAD_VALUE;
    }
    static const char *why[] = {"ok", "no such setting", "not one of its values", "",
                                ("a text is changed in Mod Manager on your PC")};
    if (rc == SWMOD_SET_LOCKED) {
        char lock[160];
        ui_lock_text(mi, si, lock, sizeof lock);
        control_fail(r, "%s", lock);
        return;
    }
    if (rc != SWMOD_SET_OK) {
        control_fail(r, "%s", rc >= 0 && rc <= 4 ? why[rc] : "refused");
        return;
    }
    control_field_bool(r, "ok", true);
    control_field_int(r, "serial", ui_serial());
    if (si >= 0) {
        char val[160];
        ui_value_text(mi, si, -1, val, sizeof val);
        control_field_str(r, "value", val);
        control_text(r, "%s: %s = %s%s", g_st[mi].name, g_st[mi].s[si].label, val,
                     ui_waiting(mi, si) ? " (at your next start)" : "");
    } else {
        control_text(r, "%s: every setting is back to its default", g_st[mi].name);
    }
}

#endif  // MODHOST_C
