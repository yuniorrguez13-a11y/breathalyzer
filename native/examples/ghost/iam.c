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
#define IAM_NO_LIVE "no live switch in this ghost"  // (body_err: launch.json asked for "live"; phase 2)

enum { IAM_BODY_UNKNOWN = -1, IAM_BODY_NONE = 0, IAM_BODY_RESTART = 1, IAM_BODY_LIVE = 2 };
typedef struct {
    int mode;        // IAM_BODY_*: -1 no deploy known (no launch.json, unreadable, cut short), 0 no made.linkle,
                     // 1 restart (her body is Link's own), 2 live (her copies beside Link's)
    int owned;       // made.linkle.owned: 1 Who am I turned The Linkle Mod on, 0 you did on Home, -1 not given
    char start[12];  // made.linkle.start: the body the game starts in ("" when not given or too long)
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
    char s[16], body[16] = "", start[sizeof out->start] = "";
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
                if (depth <= 3) memcpy(key[depth], s, sizeof s);
            } else if (depth == 3 && obj[1] && obj[2] && obj[3] && !strcmp(key[1], "made") &&
                       !strcmp(key[2], "linkle")) {
                if (!strcmp(key[3], "body")) memcpy(body, s, sizeof s);
                else if (!strcmp(key[3], "start") && strlen(s) < sizeof start) memcpy(start, s, strlen(s) + 1);
            }
        }
    }
    if (depth) return out->mode;  // (cut short)
    out->mode = !found                    ? IAM_BODY_NONE
                : !strcmp(body, "restart") ? IAM_BODY_RESTART
                : !strcmp(body, "live")    ? IAM_BODY_LIVE
                                           : IAM_BODY_NONE;
    if (found) memcpy(out->start, start, sizeof start), out->owned = owned;
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

// What the body is, in words for the `ghost` reply: body ("linkle", "link", "" no deploy known), body_mode ("none",
// "restart"; "live" only once a ghost can switch), body_err (why a live switch can't run; "" none). 1: her body is on.
static int iam_body_words(const IamBody *b, const char **body, const char **mode, const char **err) {
    *err = "";
    switch (b->mode) {
    case IAM_BODY_RESTART:
        *body = "linkle", *mode = "restart";
        return 1;
    case IAM_BODY_LIVE:  // (phase 2: her copies sit beside Link's and this ghost can't switch: Link's body, stage 1)
        *body = "link", *mode = "restart", *err = IAM_NO_LIVE;
        return 0;
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
