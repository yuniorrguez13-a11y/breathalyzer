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
// only says whether her body is on: The Linkle Mod is in this game's deploy (modman lists the mods it deployed in
// launch.json, next to the deployed overlay SAVAGEWIND_ROMFS_OVERLAY), read when asked. Your friends still see your
// pick (the client takes it from the `ghost` reply, as for any other).
// (no C library calls for files here: Windows' own, as coop_story.c reads its list)
__declspec(dllimport) unsigned long __stdcall GetEnvironmentVariableA(const char *name, char *buf, unsigned long size);
__declspec(dllimport) void *__stdcall CreateFileA(const char *path, unsigned long access, unsigned long share,
                                                  void *security, unsigned long disposition, unsigned long flags,
                                                  void *templ);
__declspec(dllimport) int __stdcall ReadFile(void *h, void *buf, unsigned long size, unsigned long *got, void *ov);
__declspec(dllimport) int __stdcall CloseHandle(void *h);
#define IAM_LAUNCH_MAX (512u << 10)  // launch.json read up to this (its mod list is near the top)
#define IAM_LINKLE_ON "You are Linkle (her body is on)."
#define IAM_LINKLE_HOW "To be Linkle yourself, pick Linkle in Breathalyzer (Multiplayer > Who am I): it turns The " \
                       "Linkle Mod on, then the game restarts once. You look like Link until then."

// launch.json's text: 1 when a mod in its top "mods" list has "linkle" in its id or name (any case), 0 when none,
// -1 when there is no such list (or it is cut short).
static int iam_launch_linkle(const char *text) {
    const char *p = text ? strstr(text, "\"mods\": [") : NULL;
    if (!p) return -1;
    p += 9;
    int depth = 1, value = 0;  // value: the next string is an "id" or a "name" of a mod
    char s[160];
    while (*p && depth > 0) {
        char ch = *p++;
        if (ch == '[' || ch == '{') {
            depth++;
        } else if (ch == ']' || ch == '}') {
            depth--;
        } else if (ch == '"') {
            size_t n = 0;
            for (; *p && *p != '"'; p++) {
                if (*p == '\\' && p[1]) p++;
                if (n + 1 < sizeof s) s[n++] = (char)(*p >= 'A' && *p <= 'Z' ? *p + 32 : *p);
            }
            if (!*p) return -1;
            p++, s[n] = 0;
            const char *q = p;
            while (*q == ' ' || *q == '\t' || *q == '\r' || *q == '\n') q++;
            if (*q == ':') value = depth == 2 && (!strcmp(s, "id") || !strcmp(s, "name"));
            else if (value && strstr(s, "linkle")) return 1;
            else value = 0;
        }
    }
    return depth == 0 ? 0 : -1;
}

// The same, for the file at path (-1: it can't be read).
static int iam_linkle_file(const char *path) {
    void *h = CreateFileA(path, 0x80000000ul, 1, 0, 3, 0, 0);  // GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING
    if (h == (void *)(intptr_t)-1) return -1;
    char *text = malloc(IAM_LAUNCH_MAX + 1);
    unsigned long got = 0, total = 0;
    while (text && total < IAM_LAUNCH_MAX && ReadFile(h, text + total, IAM_LAUNCH_MAX - total, &got, 0) && got)
        total += got;
    CloseHandle(h);
    if (!text) return -1;
    text[total] = 0;
    int on = iam_launch_linkle(text);
    free(text);
    return on;
}

// 1: The Linkle Mod is in this game's deploy (her body is Link's); 0: it isn't; -1: no deploy known (the game wasn't
// started by modman or the launcher).
static int iam_linkle_real(void) {
    char dir[400], path[440];
    unsigned long n = GetEnvironmentVariableA("SAVAGEWIND_ROMFS_OVERLAY", dir, sizeof dir);
    if (!n || n >= sizeof dir) return -1;
    snprintf(path, sizeof path, "%s\\..\\launch.json", dir);
    return iam_linkle_file(path);
}
static int (*g_iam_linkle)(void) = iam_linkle_real;  // (the selftest fakes it)

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

// The `ghost` reply's part (tools/mp/client.py looks for has_iam, iam_seq and iam).
static void iam_report(SwModReply *r) {
    IamState st;
    double t = now_s();
    lock();
    g_iam.heard = t;
    st = g_iam;
    unlock();
    api->reply_bool(r, "has_iam", true);
    api->reply_int(r, "iam_seq", st.seq);
    api->reply_str(r, "iam", st.want);
    api->reply_str(r, "iam_current", st.current);
    api->reply_int(r, "looks", st.n);
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
    const char *now = *st->want ? st->want : *st->current ? st->current : "link";
    char see[300];
    if (argc == 1) {
        api->reply_str(r, "iam", now);
        api->reply_int(r, "iam_seq", st->seq);
        api->reply_str(r, "names", names);
        if (iam_is_linkle(now))  // 0.9.8: no costume for her: whether her body is on
            snprintf(see, sizeof see, "%s", g_iam_linkle() == 1 ? IAM_LINKLE_ON : IAM_LINKLE_HOW);
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
    if (iam_is_linkle(id)) {  // 0.9.8: no puppet (Link shows): her body is Link's own when The Linkle Mod is on
        const char *friends = !listed     ? "The multiplayer client tells your friends once it runs with --ghost-looks."
                              : listening ? "Your friends see Linkle in a few seconds."
                                          : "The multiplayer client (--ghost-looks) tells your friends when it runs.";
        if (g_iam_linkle() == 1) api->reply_text(r, "%s %s", IAM_LINKLE_ON, friends);
        else api->reply_text(r, "Your friends see you as Linkle. %s %s", IAM_LINKLE_HOW, friends);
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
