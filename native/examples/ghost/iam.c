// 0.8.0 Who am I (docs/charsel.md): `iam <name>` in the game's console (F12) picks who your friends see you as.
// Included by ghost.c (one DLL: modhost runs one frame job per game). It is player safe: it only keeps a word under
// the lock, no game call and no game memory, so it works without developer mode.
//
// tools/mp/client.py gives the list of characters and your current pick (`ghost looks <id/alias,...> [current]`) on
// each new game connection, so `iam` alone lists them and a word not on the list is refused here. The client reads
// the pick back from the `ghost` reply (has_iam, iam_seq, iam) and tells the relay; the other players' clients turn
// the id into a body through their own built packs (never a raw actor name: tools/charsel.py Looks).
//
//   iam                 who you are now, and the names you can type
//   iam <name>          pick one (an id or an alias from the list: `iam zelda`, `iam daruk`)
//   iam next | back     the next or the previous one on the list (the wheel's order)
//   ghost looks <list> [current]   (the client) the list: comma-separated entries, each id/alias/alias (lower-case
//                                  letters, digits and -, 1-24 each), at most IAM_MAX entries

#define IAM_MAX 48
#define IAM_WORD 24
#define IAM_ENTRY 96
#define IAM_USAGE "[name | next | back]"

typedef struct {
    char entry[IAM_MAX][IAM_ENTRY];  // "id/alias/alias" as the client gave it
    int n;                           // entries (0: no client gave a list yet)
    char current[IAM_WORD + 1];      // your pick, as the client last said it
    char want[IAM_WORD + 1];         // the id typed last ("" none)
    int seq;                         // bumped by each `iam <name>` (the client takes a new seq once)
} IamState;

static IamState g_iam;  // (under lock(): commands run on the frame thread, the client asks from the socket)

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
    lock();
    st = g_iam;
    unlock();
    api->reply_bool(r, "has_iam", true);
    api->reply_int(r, "iam_seq", st.seq);
    api->reply_str(r, "iam", st.want);
    api->reply_str(r, "iam_current", st.current);
    api->reply_int(r, "looks", st.n);
}

// `ghost looks <list> [current]` (from the client).
static void cmd_looks(int argc, char **argv, SwModReply *r) {
    if (argc != 3 && argc != 4) {
        api->reply_fail(r, "usage: ghost looks id/alias,id,... [current]");
        return;
    }
    IamState *tmp = calloc(1, sizeof *tmp);
    if (!tmp) {
        api->reply_fail(r, "out of memory");
        return;
    }
    char cur[IAM_WORD + 1] = "";
    int n = iam_parse(argv[2], tmp);
    if (n < 0 || (argc == 4 && !iam_norm(argv[3], cur))) {
        free(tmp);
        api->reply_fail(r, "bad list (id/alias,... of lower-case letters, digits and -, up to %d)", IAM_MAX);
        return;
    }
    lock();
    memcpy(g_iam.entry, tmp->entry, sizeof g_iam.entry);
    g_iam.n = n;
    if (argc == 4) memcpy(g_iam.current, cur, sizeof cur);
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
    if (argc == 1) {
        api->reply_str(r, "iam", now);
        api->reply_int(r, "iam_seq", st->seq);
        if (st->n)
            api->reply_text(r, "You are %s. Type iam and a name: %s", now, names);
        else
            api->reply_text(r, "You are %s. Start the multiplayer client to see the names you can pick.", now);
        free(st);
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
    lock();
    memcpy(g_iam.want, id, sizeof id);
    g_iam.seq++;
    int seq = g_iam.seq;
    unlock();
    free(st);
    api->reply_str(r, "iam", id);
    api->reply_int(r, "iam_seq", seq);
    api->reply_text(r, "You are %s now. Your friends see it in a few seconds (the multiplayer client sends it).", id);
}
