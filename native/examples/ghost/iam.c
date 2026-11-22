// 0.8.0 Who am I (docs/charsel.md): `iam <name>` in the game's console (F12) picks who your friends see you as.
// Included by ghost.c (one DLL: modhost runs one frame job per game). It is player safe: it only keeps a word under
// the lock, no game call and no game memory, so it works without developer mode.
//
// tools/mp/client.py gives the list of characters and your current pick (`ghost looks <id/alias,...> current seen`) on
// each new game connection and on each change, so `iam` alone lists them and a word not on the list is refused here.
// The client reads the pick back from the `ghost` reply (has_iam, iam_seq, iam) and tells the relay; the other
// players' clients turn the id into a body through their own built packs (never a raw actor name: tools/charsel.py
// Looks). `seen` is the last iam_seq the client read: the typed word is cleared (the client's pick is the current one
// now) only when nothing newer was typed, so an old `iam` never wins over a newer pick (a client started later with
// --look, or the launcher's wheel). The list and the pick go through the client's control port, which needs the
// game's developer mode; `iam` itself is player safe.
//
//   iam                 who you are now, and the names you can type
//   iam <name>          pick one (an id or an alias from the list: `iam zelda`, `iam daruk`)
//   iam next | back     the next or the previous one on the list (the wheel's order)
//   iam self off | on   0.9.0: see yourself as Link, or (the default) as your pick, live (puppet.c)
//   iam linkle          0.9.8: no costume: says whether her body is on (The Linkle Mod as Link's own body, picked in
//                       Breathalyzer with one restart), else how to get it
//   iam link            with her body on: your body stays Linkle until you pick Link in Breathalyzer (one restart),
//                       or turn The Linkle Mod off on Home when you turned it on there; your friends see Link at once
//   ghost looks <list> [current [seen]]   (the client) the list: comma-separated entries, each id/alias/alias
//                                  (lower-case letters, digits and -, 1-24 each), at most IAM_MAX entries

#define IAM_MAX 48
#define IAM_WORD 24
#define IAM_ENTRY 96
#define IAM_USAGE "[name | next | back | self on|off]"
#define IAM_HEARD_S 6.0  // a client that asked for `ghost` this recently is running (it asks every 2 s)

typedef struct {
    char entry[IAM_MAX][IAM_ENTRY];  // "id/alias/alias" as the client gave it
    int n;                           // entries (0: no client gave a list yet)
    char current[IAM_WORD + 1];      // your pick, as the client last said it
    char want[IAM_WORD + 1];         // the id typed last ("" none)
    int seq;                         // bumped by each `iam <name>` (the client takes a new seq once)
    double heard;                    // now_s() of the last `ghost` state ask (the client's, every 2 s; 0: never)
} IamState;

static IamState g_iam;  // (under lock(): commands run on the frame thread, the client asks from the socket)

// 0.9.0: you see yourself as your pick, live (puppet.c, included after this file): its sentence, and `iam self`.
static void pup_iam_text(const char *picked, char *out, size_t cap);
static void pup_set_self(int on);

// 0.9.8 Linkle by restart (docs/charsel.md "Linkle"; out/research/linkle-body-swap-plan.md, stage 1): `iam linkle`
// shows no costume any more. Picking Linkle in Breathalyzer (Multiplayer > Who am I) turns The Linkle Mod on as Link's
// OWN body (every armor piece shows as hers, the inventory shows her) and restarts the game once; here `iam linkle`
// only says whether her body is on. Your friends still see your pick at once (the client takes it from the `ghost`
// reply, as for any other).
// 0.9.9 (phase 0 of out/research/linkle-live-swap-design.md): whether her body is on comes from launch.json's
// "made": {"linkle": {"body": "restart" | "live", "start": "link" | "linkle"}}, which modman writes itself when the mod
// that gives this deploy its Link model is The Linkle Mod (charsel.is_linkle_mod). No more guessing from mod names:
// her add-ons alone (title screen, armor icons, dialogue) made `iam` say her body was on with The Linkle Mod off
// (review N8). launch.json sits next to the deployed overlay (SAVAGEWIND_ROMFS_OVERLAY); it is read once, when first
// asked (the deploy can't change while the game runs). "live" (her copies beside Link's, switched in the game) is
// phase 2: this ghost can't switch yet, so it says the stage 1 words and gives body_err.
// 1.0.0 live (phase 2 of out/research/linkle-live-swap-design.md, 2.2): with made.linkle.body "live", her files sit
// beside Link's (SwLk_ copies, modman's "beside" deploy) and deploy\linkle-live.json (made.linkle.map) lists them.
// At swmod_init the ghost names each pair as a file redirect (API 4 fs_redirect_add, group "linkle"; modhost only
// takes them with SWMOD_FILE_REDIRECT=1, which modman sets for that deploy). `iam linkle` then queues the group on,
// `iam link` (or any other pick) off; the frame job flips it once the file loaders have been idle for IAM_IDLE_MS, never
// in the middle of a load, and the body changes at the next load screen (fast travel). made.linkle.start "linkle"
// queues it at swmod_init (the host turns a group on only once the game runs: the first idle frame does). Not ready
// (an older modhost, the setting off, the map unreadable, one of her files the host didn't take, a file open another
// mod overrides) or a copy that failed to open (the group goes off): the stage-1 words, and body_err says why (the
// launcher can offer the restart).
// made.linkle.owned (review 2026-11-21): true when Who am I turned The Linkle Mod on (charsel.linkle_owned), false when
// you turned it on yourself on Home. Picking Link in Who am I turns off only its own, so with false the way back to Link
// is Home; without the key (an older modman) both ways are said.
// (no C library calls for files here: Windows' own, as coop_story.c reads its list)
__declspec(dllimport) unsigned long __stdcall GetEnvironmentVariableA(const char *name, char *buf, unsigned long size);
__declspec(dllimport) void *__stdcall CreateFileA(const char *path, unsigned long access, unsigned long share,
                                                  void *security, unsigned long disposition, unsigned long flags,
                                                  void *templ);
__declspec(dllimport) int __stdcall ReadFile(void *h, void *buf, unsigned long size, unsigned long *got, void *ov);
__declspec(dllimport) int __stdcall CloseHandle(void *h);
#define IAM_LAUNCH_MAX (2u << 20)  // launch.json read up to this ("made" is near its end; a real one is ~10 KB)
#define IAM_LINKLE_ON "You are Linkle (her body is on)."
// (her body off: "Her body is off", not "Your body is Link": a play-as mod may give the body another look; review)
#define IAM_LINKLE_HOW "Her body is off. To be Linkle yourself, pick Linkle in Breathalyzer (Multiplayer > Who am " \
                       "I): the game restarts once."
#define IAM_LINK_HOW "Your body is Linkle. To be Link again, pick Link in Breathalyzer (Multiplayer > Who am I): " \
                     "the game restarts once."
#define IAM_LINK_HOW_HOME "Your body is Linkle. To be Link again, turn The Linkle Mod off on Breathalyzer's Home, " \
                          "then restart the game."
#define IAM_LINK_HOW_BOTH "Your body is Linkle. To be Link again, pick Link in Breathalyzer (Multiplayer > Who am " \
                          "I), or turn The Linkle Mod off on Home: the game restarts once."
#define IAM_NO_LIVE "no live switch: modhost is too old for the file redirect (API 4)"  // (body_err)
#define IAM_LIVE_GROUP "linkle"
#define IAM_IDLE_MS 500   // the loaders idle this long before a flip
#define IAM_MAP_MAX (1u << 20)
#define IAM_LIVE_ON "Linkle: done at the next load screen (fast travel)."
#define IAM_LIVE_OFF "Link: back at the next load screen (fast travel)."
#define IAM_LIVE_HER "You are Linkle (live: her body, switched in the game)."
#define IAM_LIVE_FAIL "a copy of her files didn't open: back to Link's files"


enum { IAM_BODY_UNKNOWN = -1, IAM_BODY_NONE = 0, IAM_BODY_RESTART = 1, IAM_BODY_LIVE = 2 };
typedef struct {
    int mode;        // IAM_BODY_*: -1 no deploy known (no launch.json, unreadable, cut short), 0 no made.linkle,
                     // 1 restart (her body is Link's own), 2 live (her copies beside Link's)
    int owned;       // made.linkle.owned: 1 Who am I turned The Linkle Mod on, 0 you did on Home, -1 not given
    char start[12];  // made.linkle.start: the body the game starts in ("" when not given or too long)
    char map[64];    // made.linkle.map: her copies' list, next to launch.json ("" when not given or too long)
} IamBody;

// The string at p (just past its opening quote) into out (cut to cap, "" when longer: no false match); returns the
// character past its closing quote, or NULL when the text ends first.
static const char *iam_json_str(const char *p, char *out, size_t cap) {
    size_t n = 0;
    int cut = 0;
    for (; *p && *p != '"'; p++) {
        if (*p == '\\' && p[1]) p++;
        if (n + 1 < cap) out[n++] = *p;
        else cut = 1;
    }
    out[cut ? 0 : n] = 0;
    return *p ? p + 1 : NULL;
}

static void copy_key(char *dst, const char *s, size_t cap) {  // (a key longer than cap - 1: "", no false match)
    size_t n = strlen(s);
    if (n >= cap) n = 0;
    memcpy(dst, s, n);
    dst[n] = 0;
}

// launch.json's text: made.linkle, as out (out->mode is also returned). Only the top object's "made" -> "linkle"
// object counts (a "linkle" anywhere else, or a mod named Linkle in "mods", is nothing). A text cut short is -1.
static int iam_launch_body(const char *text, IamBody *out) {
    memset(out, 0, sizeof *out);
    out->mode = IAM_BODY_UNKNOWN;
    out->owned = -1;
    if (!text) return out->mode;
    const char *p = text;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    if (*p != '{') return out->mode;
    enum { MAXD = 64 };
    char key[4][16] = {{0}};  // the key being read at depth 1..3 ("" for an array or a long key)
    char obj[MAXD + 1] = {0}; // 1: the container at that depth is an object
    char s[sizeof out->map], body[16] = "", start[sizeof out->start] = "", map[sizeof out->map] = "";
    int depth = 0, found = 0, owned = -1;
    while (*p) {
        char ch = *p++;
        if (ch == 't' || ch == 'f') {  // (true / false: outside a string, only a value can start so)
            int yes = ch == 't';
            size_t n = yes ? 3 : 4;
            if (strncmp(p, yes ? "rue" : "alse", n)) continue;
            p += n;
            if (depth == 3 && obj[1] && obj[2] && obj[3] && !strcmp(key[1], "made") && !strcmp(key[2], "linkle") &&
                !strcmp(key[3], "owned"))
                owned = yes;
        } else if (ch == '{' || ch == '[') {
            if (depth >= MAXD) return out->mode;
            if (ch == '{' && depth == 2 && obj[1] && obj[2] && !strcmp(key[1], "made") && !strcmp(key[2], "linkle"))
                found = 1;
            obj[++depth] = ch == '{';
            if (depth <= 3) key[depth][0] = 0;
        } else if (ch == '}' || ch == ']') {
            if (!depth--) return out->mode;
            if (!depth) break;  // (the top object closed: the rest is ignored)
        } else if (ch == '"') {
            if (!(p = iam_json_str(p, s, sizeof s))) return out->mode;
            const char *q = p;
            while (*q == ' ' || *q == '\t' || *q == '\r' || *q == '\n') q++;
            if (*q == ':' && depth >= 1 && obj[depth]) {
                if (depth <= 3) copy_key(key[depth], s, sizeof key[depth]);
            } else if (depth == 3 && obj[1] && obj[2] && obj[3] && !strcmp(key[1], "made") &&
                       !strcmp(key[2], "linkle")) {
                if (!strcmp(key[3], "body") && strlen(s) < sizeof body) memcpy(body, s, strlen(s) + 1);
                else if (!strcmp(key[3], "start") && strlen(s) < sizeof start) memcpy(start, s, strlen(s) + 1);
                else if (!strcmp(key[3], "map")) memcpy(map, s, sizeof map);
            }
        }
    }
    if (depth) return out->mode;  // (cut short)
    out->mode = !found                    ? IAM_BODY_NONE
                : !strcmp(body, "restart") ? IAM_BODY_RESTART
                : !strcmp(body, "live")    ? IAM_BODY_LIVE
                                           : IAM_BODY_NONE;
    if (found) memcpy(out->start, start, sizeof start), memcpy(out->map, map, sizeof map), out->owned = owned;
    return out->mode;
}

// The words for the way back to Link while her body is on (made.linkle.owned: who turned The Linkle Mod on).
static const char *iam_link_how(const IamBody *b) {
    return b->owned == 1 ? IAM_LINK_HOW : b->owned == 0 ? IAM_LINK_HOW_HOME : IAM_LINK_HOW_BOTH;
}

// The same, for the file at path (mode -1: it can't be read).
static int iam_body_file(const char *path, IamBody *out) {
    memset(out, 0, sizeof *out);
    out->mode = IAM_BODY_UNKNOWN;
    out->owned = -1;
    void *h = CreateFileA(path, 0x80000000ul, 1, 0, 3, 0, 0);  // GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING
    if (h == (void *)(intptr_t)-1) return out->mode;
    char *text = malloc(IAM_LAUNCH_MAX + 1);
    unsigned long got = 0, total = 0;
    while (text && total < IAM_LAUNCH_MAX && ReadFile(h, text + total, IAM_LAUNCH_MAX - total, &got, 0) && got)
        total += got;
    CloseHandle(h);
    if (!text) return out->mode;
    text[total] = 0;
    iam_launch_body(text, out);
    free(text);
    return out->mode;
}

// This game's deploy: launch.json next to the overlay (mode -1: none known, the game wasn't started by modman or the
// launcher).
static int iam_body_real(IamBody *out) {
    char dir[400], path[440];
    unsigned long n = GetEnvironmentVariableA("SAVAGEWIND_ROMFS_OVERLAY", dir, sizeof dir);
    if (!n || n >= sizeof dir) {
        memset(out, 0, sizeof *out);
        out->mode = IAM_BODY_UNKNOWN;
        out->owned = -1;
        return out->mode;
    }
    snprintf(path, sizeof path, "%s\\..\\launch.json", dir);
    return iam_body_file(path, out);
}
static int (*g_iam_body_read)(IamBody *out) = iam_body_real;  // (the selftest fakes it)
static IamBody g_iam_body;                                     // (under lock(): read once, when first asked)
static int g_iam_body_have;

static void iam_body(IamBody *out) {
    lock();
    int have = g_iam_body_have;
    if (have) *out = g_iam_body;
    unlock();
    if (have) return;
    IamBody b;
    g_iam_body_read(&b);
    lock();
    g_iam_body = b;
    g_iam_body_have = 1;
    unlock();
    *out = b;
}

// 1.0.0 live: the redirect group's state (under lock()). Set at swmod_init (iam_live_init), then by the frame job
// (iam_live_tick) and `iam`.
static struct {
    int tried;        // iam_live_init ran with made.linkle.body "live"
    int files;        // redirects the host took
    int on;           // the group is on (what the frame job last set)
    int want;         // -1 nothing queued, 0 Link, 1 Linkle
    uint64_t fails;   // the group's fails seen
    const char *err;  // body_err: one of the words below (a constant; "" none)
} g_live = {0, 0, 0, -1, 0, ""};
#define IAM_LIVE_NOT_READY "the file redirect isn't ready (see savagewind.log)"

// her copies' list (linkle-live.json: {"group": "linkle", "files": [{"from": ..., "to": ..., "dev": ...}, ...]}):
// each "from" with the "to" after it, given to add. Returns the pairs found, -1 when the text isn't such a list.
static int iam_live_pairs(const char *text, int (*add)(const char *from, const char *to)) {
    char from[208], to[208], key[16];
    int n = 0, have = 0;  // (1 from, 2 to: what this object gave so far)
    const char *p = text ? strstr(text, "\"files\"") : NULL;
    if (!p) return -1;
    for (const char *prev = p; (p = strchr(p, '"')); prev = p) {
        if (memchr(prev, '{', (size_t)(p - prev))) have = 0;  // (a new object: its own from and to)
        if (!(p = iam_json_str(p + 1, key, sizeof key))) return -1;
        const char *q = p;
        while (*q == ' ' || *q == '\t' || *q == '\r' || *q == '\n') q++;
        if (*q != ':') continue;
        q++;
        while (*q == ' ' || *q == '\t' || *q == '\r' || *q == '\n') q++;
        if (*q != '"' || (strcmp(key, "from") && strcmp(key, "to"))) continue;
        char *dst = key[0] == 'f' ? from : to;
        if (!(p = iam_json_str(q + 1, dst, sizeof from)) || !*dst) return -1;
        if ((have |= key[0] == 'f' ? 1 : 2) == 3) {
            have = 0;
            if (add(from, to) == SWMOD_FSR_OK) n++;
        }
    }
    return n;
}

static int g_live_add_err;
static int iam_live_add(const char *from, const char *to) {
    int code = api->fs_redirect_add(IAM_LIVE_GROUP, from, to);
    if (code != SWMOD_FSR_OK && !g_live_add_err) g_live_add_err = code;
    return code;
}

// The map's text (NULL: can't be read): next to launch.json.
static char *iam_live_read(const char *map) {
    char dir[400], path[480];
    unsigned long n = GetEnvironmentVariableA("SAVAGEWIND_ROMFS_OVERLAY", dir, sizeof dir);
    if (!n || n >= sizeof dir || !*map || strchr(map, '\\') || strchr(map, '/') || strstr(map, "..")) return NULL;
    snprintf(path, sizeof path, "%s\\..\\%s", dir, map);
    void *h = CreateFileA(path, 0x80000000ul, 1, 0, 3, 0, 0);
    if (h == (void *)(intptr_t)-1) return NULL;
    char *text = malloc(IAM_MAP_MAX + 1);
    unsigned long got = 0, total = 0;
    while (text && total < IAM_MAP_MAX && ReadFile(h, text + total, IAM_MAP_MAX - total, &got, 0) && got) total += got;
    CloseHandle(h);
    if (text) text[total] = 0;
    return text;
}
static char *(*g_iam_live_read)(const char *map) = iam_live_read;  // (the selftest fakes it)

// From swmod_init: with made.linkle.body "live", name her copies as redirects (and start in her body if asked).
static void iam_live_init(void) {
    IamBody b;
    iam_body(&b);
    if (b.mode != IAM_BODY_LIVE) return;
    g_live.tried = 1;
    if (!SWMOD_API_OK(api, 4) || !SWMOD_HAS(api, fs_redirect_state)) {
        g_live.err = IAM_NO_LIVE;
        return;
    }
    char *text = g_iam_live_read(b.map);
    g_live_add_err = 0;
    int n = text ? iam_live_pairs(text, iam_live_add) : -1;
    free(text);
    if (n <= 0 || g_live_add_err) {  // (one of her files the host didn't take: a half-and-half body, so none)
        g_live.err = g_live_add_err == SWMOD_FSR_OFF ? "the file redirect is off (SWMOD_FILE_REDIRECT): deploy again"
                     : n < 0 ? "her copies' list (linkle-live.json) can't be read: deploy again"
                     : n > 0 ? "not all of her files could be redirected (see savagewind.log)"
                             : "no copy of her files was taken (see savagewind.log)";
        api->log("ghost: Linkle live: %s (code %d)", g_live.err, g_live_add_err);
        return;
    }
    g_live.files = n;
    // (the host turns a group on only once the game runs: start "linkle" is queued, the frame job flips it)
    if (!strcmp(b.start, "linkle")) g_live.want = 1;
    api->log("ghost: Linkle live: %d of her files beside Link's%s", n, g_live.want == 1 ? ", starting as Linkle" : "");
}

// Whether the live switch can run now (fills why when not).
static int iam_live_ready(SwModFsRedirectInfo *in) {
    if (!g_live.files || !SWMOD_API_OK(api, 4)) return 0;
    memset(in, 0, sizeof *in);
    in->size = sizeof *in;
    return api->fs_redirect_state(IAM_LIVE_GROUP, in) == SWMOD_FSR_OK && in->ready;
}

// Each frame (job_wrapper): a queued flip once the loaders idle; a copy that failed: the group off.
static void iam_live_tick(void) {
    if (!g_live.files) return;
    lock();
    int want = g_live.want, on = g_live.on, asked = want;
    uint64_t seen = g_live.fails;
    unlock();
    SwModFsRedirectInfo in;
    int ready = iam_live_ready(&in);
    const char *err = NULL;
    if (ready && in.fails > seen && in.on) {  // (her copy didn't open: Link's files again, as a whole)
        api->fs_redirect_set(IAM_LIVE_GROUP, 0);
        err = IAM_LIVE_FAIL, want = -1, on = 0;
    } else if (want >= 0 && !ready) {
        err = IAM_LIVE_NOT_READY, want = -1;
    } else if (want >= 0 && !in.in_flight && in.idle_ms >= IAM_IDLE_MS) {
        if (api->fs_redirect_set(IAM_LIVE_GROUP, want) == SWMOD_FSR_OK) on = want;
        want = -1;
    } else if (want < 0) {
        return;
    }
    lock();
    if (g_live.want == asked || err == IAM_LIVE_FAIL) g_live.want = want;  // (a pick since then: next frame)
    g_live.on = on;
    if (ready) g_live.fails = in.fails;
    if (err) g_live.err = err;
    if (err == IAM_LIVE_FAIL) g_live.files = 0;  // (stage 1 from now on: no half-and-half body)
    unlock();
}

// `iam linkle` / `iam <other>` in live mode: queue the flip. 1: queued (the words are in out), 0: not live here.
static int iam_live_pick(int her, char *out, size_t cap) {
    SwModFsRedirectInfo in;
    if (!iam_live_ready(&in)) return 0;
    lock();
    int on = g_live.want >= 0 ? g_live.want : g_live.on;
    g_live.want = her;
    g_live.err = "";
    unlock();
    snprintf(out, cap, "%s", her ? (on ? IAM_LIVE_HER : IAM_LIVE_ON) : IAM_LIVE_OFF);
    return 1;
}

// What the body is, in words for the `ghost` reply: body ("linkle", "link", "" no deploy known), body_mode ("none",
// "restart", "live"), body_err (why a live switch can't run; "" none). 1: her body is on.
static int iam_body_words(const IamBody *b, const char **body, const char **mode, const char **err) {
    *err = "";
    switch (b->mode) {
    case IAM_BODY_RESTART:
        *body = "linkle", *mode = "restart";
        return 1;
    case IAM_BODY_LIVE: {  // (her copies beside Link's: switched in the game, or stage 1 when it can't run)
        SwModFsRedirectInfo in;
        int ok = iam_live_ready(&in);
        lock();
        int on = g_live.on;
        const char *e = g_live.err && *g_live.err ? g_live.err : "";
        int tried = g_live.tried;
        unlock();
        if (!ok && !*e) e = tried ? IAM_LIVE_NOT_READY : IAM_NO_LIVE;
        *err = e;
        *body = ok && on ? "linkle" : "link", *mode = ok ? "live" : "restart";
        return ok && on;
    }
    case IAM_BODY_NONE:
        *body = "link", *mode = "none";
        return 0;
    default:
        *body = "", *mode = "none";
        return 0;
    }
}
static int iam_linkle_on(IamBody *b) {  // (b: the body, for iam_link_how)
    const char *x, *y, *z;
    iam_body(b);
    return iam_body_words(b, &x, &y, &z);
}

static int iam_is_linkle(const char *id) { return !strcmp(id, "linkle") || !strcmp(id, "linkel"); }

// A word as `iam` takes it: lower-case letters, digits and -, 1-IAM_WORD, not starting with -.
static int iam_word_ok(const char *s, size_t n) {
    if (!n || n > IAM_WORD || s[0] == '-') return 0;
    for (size_t i = 0; i < n; i++) {
        char ch = s[i];
        if (!((ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '-')) return 0;
    }
    return 1;
}

// What the player typed, as a word: lower case, '_' as '-'. 0 if it can't be one.
static int iam_norm(const char *src, char *dst) {
    size_t n = strlen(src);
    if (!n || n > IAM_WORD) return 0;
    for (size_t i = 0; i < n; i++) {
        char ch = src[i];
        if (ch >= 'A' && ch <= 'Z') ch = (char)(ch - 'A' + 'a');
        if (ch == '_') ch = '-';
        dst[i] = ch;
    }
    dst[n] = 0;
    return iam_word_ok(dst, n);
}

// The list from `ghost looks`, checked whole: every entry id/alias/alias with good words. -1: a bad list.
static int iam_parse(const char *list, IamState *out) {
    int n = 0;
    const char *p = list;
    while (*p) {
        const char *e = strchr(p, ',');
        size_t len = e ? (size_t)(e - p) : strlen(p);
        if (!len || len >= IAM_ENTRY || n >= IAM_MAX) return -1;
        const char *w = p;  // each word of the entry
        while (w < p + len) {
            const char *s = memchr(w, '/', (size_t)(p + len - w));
            size_t wl = s ? (size_t)(s - w) : (size_t)(p + len - w);
            if (!iam_word_ok(w, wl)) return -1;
            w += wl + (s ? 1 : 0);
            if (s && w == p + len) return -1;  // (a trailing /)
        }
        memcpy(out->entry[n], p, len);
        out->entry[n][len] = 0;
        n++;
        p += len;
        if (*p == ',') {
            p++;
            if (!*p) return -1;  // (a trailing ,)
        }
    }
    out->n = n;
    return n;
}

// The entry whose id or alias is `word`: its index, or -1.
static int iam_find(const IamState *st, const char *word) {
    size_t wl = strlen(word);
    for (int i = 0; i < st->n; i++) {
        const char *w = st->entry[i];
        while (*w) {
            const char *s = strchr(w, '/');
            size_t l = s ? (size_t)(s - w) : strlen(w);
            if (l == wl && !memcmp(w, word, l)) return i;
            if (!s) break;
            w = s + 1;
        }
    }
    return -1;
}

// An entry's id (its first word) into out.
static void iam_id(const IamState *st, int i, char *out) {
    const char *e = st->entry[i];
    size_t l = strcspn(e, "/");
    if (l > IAM_WORD) l = IAM_WORD;
    memcpy(out, e, l);
    out[l] = 0;
}

// "link, linkle, zelda, ..." (the ids only) into out.
static void iam_names(const IamState *st, char *out, size_t cap) {
    size_t n = 0;
    out[0] = 0;
    for (int i = 0; i < st->n && n + 2 < cap; i++) {
        char id[IAM_WORD + 1];
        iam_id(st, i, id);
        int w = snprintf(out + n, cap - n, i ? ", %s" : "%s", id);
        if (w < 0) break;
        n += (size_t)w;
        if (n >= cap) n = cap - 1;
    }
}

// The `ghost` reply's part (tools/mp/client.py looks for has_iam, iam_seq and iam). 0.9.9: body, body_mode and
// body_err (iam_body_words) for the client and the launcher.
static void iam_report(SwModReply *r) {
    IamState st;
    IamBody b;
    const char *body, *mode, *err;
    double t = now_s();
    lock();
    g_iam.heard = t;
    st = g_iam;
    unlock();
    iam_body(&b);
    iam_body_words(&b, &body, &mode, &err);
    api->reply_bool(r, "has_iam", true);
    api->reply_int(r, "iam_seq", st.seq);
    api->reply_str(r, "iam", st.want);
    api->reply_str(r, "iam_current", st.current);
    api->reply_int(r, "looks", st.n);
    api->reply_str(r, "body", body);
    api->reply_str(r, "body_mode", mode);
    api->reply_str(r, "body_err", err);
    lock();
    int want = g_live.want;
    unlock();
    api->reply_str(r, "body_pending", b.mode == IAM_BODY_LIVE && want >= 0 ? (want ? "linkle" : "link") : "");
}

// A whole number 0-999999999 (the client's `seen`). -1 if it isn't one.
static int iam_seq_arg(const char *s) {
    size_t n = strlen(s);
    if (!n || n > 9) return -1;
    int v = 0;
    for (size_t i = 0; i < n; i++) {
        if (s[i] < '0' || s[i] > '9') return -1;
        v = v * 10 + (s[i] - '0');
    }
    return v;
}

// `ghost looks <list> [current [seen]]` (from the client). With current: that is your pick now, and the typed word
// is cleared unless a newer one was typed after `seen` (without seen: always cleared).
static void cmd_looks(int argc, char **argv, SwModReply *r) {
    if (argc < 3 || argc > 5) {
        api->reply_fail(r, "usage: ghost looks id/alias,id,... [current [seen]]");
        return;
    }
    IamState *tmp = calloc(1, sizeof *tmp);
    if (!tmp) {
        api->reply_fail(r, "out of memory");
        return;
    }
    char cur[IAM_WORD + 1] = "";
    int n = iam_parse(argv[2], tmp);
    int seen = argc == 5 ? iam_seq_arg(argv[4]) : -1;
    if (n < 0 || (argc >= 4 && !iam_norm(argv[3], cur)) || (argc == 5 && seen < 0)) {
        free(tmp);
        api->reply_fail(r, "bad list (id/alias,... of lower-case letters, digits and -, up to %d)", IAM_MAX);
        return;
    }
    lock();
    memcpy(g_iam.entry, tmp->entry, sizeof g_iam.entry);
    g_iam.n = n;
    if (argc >= 4) {
        memcpy(g_iam.current, cur, sizeof cur);
        if (argc == 4 || seen == g_iam.seq) g_iam.want[0] = 0;  // (the client has it: the pick is `current` now)
    }
    unlock();
    free(tmp);
    api->reply_int(r, "looks", n);
}

// `iam [name | next | back]`
static void cmd_iam(int argc, char **argv, SwModReply *r) {
    IamState *st = calloc(1, sizeof *st);
    if (!st) {
        api->reply_fail(r, "out of memory");
        return;
    }
    lock();
    *st = g_iam;
    unlock();
    char names[IAM_MAX * (IAM_WORD + 2) + 8];
    iam_names(st, names, sizeof names);
    IamBody body;
    int her = iam_linkle_on(&body);  // (0.9.9: launch.json's made.linkle; read once)
    // no pick known yet (single player, or before the client): her body on means you picked Linkle in Breathalyzer
    const char *now = *st->want ? st->want : *st->current ? st->current : her ? "linkle" : "link";
    char see[300];
    if (argc == 1) {
        api->reply_str(r, "iam", now);
        api->reply_int(r, "iam_seq", st->seq);
        api->reply_str(r, "names", names);
        if (iam_is_linkle(now))  // 0.9.8: no costume for her: whether her body is on
            snprintf(see, sizeof see, "%s", her ? "Her body is on." : IAM_LINKLE_HOW);
        else if (her && !strcmp(now, "link"))  // 0.9.9: Link shows no puppet: you see her body
            snprintf(see, sizeof see, "%s", iam_link_how(&body));
        else
            pup_iam_text(NULL, see, sizeof see);
        if (st->n)
            api->reply_text(r, "You are %s. %s%sType iam and a name: %s", now, see, *see ? " " : "", names);
        else
            api->reply_text(r, "You are %s. %s%sStart the multiplayer client to see the names you can pick.", now, see,
                            *see ? " " : "");
        free(st);
        return;
    }
    if (argc == 3 && !strcmp(argv[1], "self") && (!strcmp(argv[2], "on") || !strcmp(argv[2], "off"))) {
        free(st);
        pup_set_self(argv[2][1] == 'n');
        api->reply_bool(r, "self", argv[2][1] == 'n');
        api->reply_text(r, argv[2][1] == 'n' ? "You see yourself as your pick again (live)."
                           : her             ? "You see your own body (Linkle). Your friends still see your pick."
                                             : "You see yourself as Link. Your friends still see your pick.");
        return;
    }
    char word[IAM_WORD + 1], id[IAM_WORD + 1];
    if (argc != 2 || !iam_norm(argv[1], word)) {
        free(st);
        api->reply_fail(r, "usage: iam %s", IAM_USAGE);
        return;
    }
    if (!strcmp(word, "next") || !strcmp(word, "back") || !strcmp(word, "prev")) {
        if (!st->n) {
            free(st);
            api->reply_fail(r, "iam: start the multiplayer client first (it gives the names)");
            return;
        }
        int i = iam_find(st, now);
        i = i < 0 ? 0 : (i + (word[0] == 'n' ? 1 : st->n - 1)) % st->n;
        iam_id(st, i, id);
    } else if (st->n) {
        int i = iam_find(st, word);
        if (i < 0) {
            api->reply_fail(r, "iam: no %s. Try: %s", word, names);
            free(st);
            return;
        }
        iam_id(st, i, id);
    } else {
        memcpy(id, word, sizeof word);  // (no list yet: the client checks it when it starts)
    }
    double t = now_s();
    lock();
    memcpy(g_iam.want, id, sizeof id);
    g_iam.seq++;
    int seq = g_iam.seq;
    unlock();
    int listening = st->heard > 0 && t - st->heard <= IAM_HEARD_S;
    int listed = st->n > 0;
    free(st);
    api->reply_str(r, "iam", id);
    api->reply_int(r, "iam_seq", seq);
    pup_iam_text(id, see, sizeof see);  // (0.9.0: you see yourself as it too, live)
    char live[160];  // 1.0.0: her body switched in the game (made.linkle.body "live" and the redirect ready)
    lock();
    int live_her = g_live.want >= 0 ? g_live.want : g_live.on;  // (on, or queued on: a pick of Link takes it back)
    unlock();
    if (body.mode == IAM_BODY_LIVE && (iam_is_linkle(id) || her || live_her) &&
        iam_live_pick(iam_is_linkle(id), live, sizeof live) && (iam_is_linkle(id) || !strcmp(id, "link"))) {
        const char *who = iam_is_linkle(id) ? "Linkle" : "Link";  // (another pick: her body off too, words below)
        if (!listed)
            api->reply_text(r, "%s The multiplayer client tells your friends once it runs with --ghost-looks.", live);
        else if (listening)
            api->reply_text(r, "%s Your friends see %s in a few seconds.", live, who);
        else
            api->reply_text(r, "%s The multiplayer client (--ghost-looks) tells your friends when it runs.", live);
        return;
    }
    // 0.9.8: Linkle has no puppet (Link shows): her body is Link's own when The Linkle Mod is on. 0.9.9: and Link with
    // her body on is still her: your body changes only by a restart from Breathalyzer, the pick friends see at once.
    if (iam_is_linkle(id) || (her && !strcmp(id, "link"))) {
        const char *who = iam_is_linkle(id) ? "Linkle" : "Link";
        const char *how = !iam_is_linkle(id) ? iam_link_how(&body) : her ? IAM_LINKLE_ON : IAM_LINKLE_HOW;
        if (!listed)
            api->reply_text(r, "%s The multiplayer client tells your friends once it runs with --ghost-looks.", how);
        else if (listening)
            api->reply_text(r, "%s Your friends see %s in a few seconds.", how, who);
        else
            api->reply_text(r, "%s The multiplayer client (--ghost-looks) tells your friends when it runs.", how);
        return;
    }
    if (!listed)  // (no client gave the list yet: the word is kept as typed; the client checks it when it starts)
        api->reply_text(r, "Kept: %s. %s%sThe multiplayer client checks it and tells your friends once it runs with "
                        "--ghost-looks.", id, see, *see ? " " : "");
    else if (listening)
        api->reply_text(r, "You are %s now. %s%sYour friends see it in a few seconds.", id, see, *see ? " " : "");
    else
        api->reply_text(r, "You are %s now. %s%sThe multiplayer client (--ghost-looks) tells your friends when it runs.",
                        id, see, *see ? " " : "");
}
