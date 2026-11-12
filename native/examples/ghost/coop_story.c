// co-op: story flags through the game's own flag manager (docs/coop.md "Story together").
//
// Included by ghost.c (one DLL: modhost runs one frame job per game, so co-op shares the ghost's). In the game
// (probe run B, 2026-11-12): the setter and the string object work (through `coop probe`); the getter guess crashed
// (reads are off). Not yet run in the game: the safe-point gate, live-only names and set-again after a load (0.7.1).
//
// What it does, on the game thread (the frame job tick) only:
// - reads every name of the loaded story list through the game's get-bool-by-name, COOP_READS_PER_TICK a frame, and
//   keeps the bits (`coop story get`: ready, seq, bits; seq goes up when a full pass sees a change);
// - adds queued bits (`coop story add <hex>`) through the game's set-bool-by-name, value 1 only: never a 0, never a
//   name outside the list, never a bit the game already has; COOP_ADDS_PER_TICK a frame;
// - adds LIVE only towers and memories (MapTower_*, IsGet_MemoryPhoto_*): a beast, shrine, quest step or cutscene
//   flag set while the player is inside it or in its event can skip event steps, so those are dropped here ("held")
//   and wait for the next join's save merge (tools/mp/story.py);
// - adds only at a safe point: the world is up, the manager's busy bit is down, and Link is free (his real position
//   is found: not loading, no cutscene, not riding; no pause menu) for COOP_SETTLE_TICKS frames in a row;
// - after any time that wasn't safe (a load, a death, a cutscene...) it sets again what it added (a load can bring
//   back the save's 0): setting a 1 to 1 changes nothing;
// - waits (and tries again next frame) when the setter says no.
// Commands only fill buffers under the lock; the tick does every game call. A list load bumps a generation number;
// an add parsed for an older list is refused (its bits would mean other names).

// (no C library here: the list file is read with Windows' own calls; CloseHandle is declared in ghost.c)
__declspec(dllimport) void *__stdcall CreateFileA(const char *path, unsigned long access, unsigned long share,
                                                  void *security, unsigned long disposition, unsigned long flags,
                                                  void *templ);
__declspec(dllimport) int __stdcall ReadFile(void *h, void *buf, unsigned long size, unsigned long *got, void *ov);

#define COOP_FLAG_MGR 0x7001e3b188ull  // [ptr]: the game's flag manager (co-op study, static)
#define COOP_SET_BOOL 0x700112d5c0ull  // (mgr, w1 value, x2 name object) -> w0: 1 = set (both copies, one call)
// The game's get-bool-by-name is NOT known yet. The first guess (docs/addresses.md, docs/coop.md) takes (mgr, name)
// only and ends in a change-and-notify path (it looks like a reset, which would REMOVE progress); in probe run B
// (2026-11-12) it crashed the game. It is never called. Until a real getter is found, reads are off: `coop story get` never gets ready and
// `coop probe flag <name>` (a read) answers ok 0. Adds still go through the setter (value 1 only).
#define COOP_GET_BOOL 0ull
static uint64_t g_coop_get_fn = COOP_GET_BOOL;  // (the selftest points it at its fake getter)
#define COOP_BUSY_OFF 0xc1a            // mgr byte: bit 2 set = every set/get by name refuses (loading, saving: probe)
#define COOP_BUSY_BIT 0x04
#define COOP_MAX 1536                  // story flags (1,075 today)
#define COOP_NAME 64
#define COOP_READS_PER_TICK 48         // 1,075 names in 23 frames (under a second at 30 fps)
#define COOP_ADDS_PER_TICK 8
#define COOP_SETTLE_TICKS 60           // Link free this many frames in a row (2 s at 30 fps) before an add
#define COOP_BYTES (COOP_MAX / 8)

typedef struct {
    char names[COOP_MAX][COOP_NAME];
    int n;
} CoopList;

static CoopList g_coop_next;                   // a list loaded by `coop story load`, waiting for the tick
static int g_coop_swap;                        // (under the lock) g_coop_next is new
static CoopList g_coop_list;                   // the tick's list
static _Alignas(16) StrObj g_coop_obj[COOP_MAX];  // one game string object per name (vtable + char*)
static _Alignas(16) uint8_t g_coop_out[16];    // the getter's answer
static uint8_t g_coop_have[COOP_BYTES];        // (tick) what the game has, as read (and as we set it)
static uint8_t g_coop_last[COOP_BYTES];        // (tick) what seq was last raised for
static uint8_t g_coop_pass[COOP_BYTES];        // (tick) this pass's reads
static uint8_t g_coop_want[COOP_BYTES];        // (tick) bits to add
static uint8_t g_coop_ours[COOP_BYTES];        // (tick) bits this list's adds set (set again after an unsafe time)
static uint8_t g_coop_live[COOP_BYTES];        // (tick) names that may be added live (towers, memories)
static uint8_t g_coop_req[COOP_BYTES];         // (under the lock) bits a command asked to add
static int g_coop_req_new;
static uint32_t g_coop_gen;                    // (under the lock) +1 on every list load
static int g_coop_cursor, g_coop_seen_pass, g_coop_pass_fails;
static int g_coop_stable, g_coop_was_unsafe;   // (tick) safe frames in a row; an unsafe time since the last add
static int g_coop_settle = COOP_SETTLE_TICKS;  // (the selftest makes it short)
static uint64_t g_coop_mgr_slot = COOP_FLAG_MGR;  // (the selftest points it at its own memory)
static int coop_world_real(void) { return botw_time_of_day(api) >= 0.0f; }
static int (*g_coop_world)(void) = coop_world_real;
// Link is free: his real position is found (not loading, no cutscene, not riding) and no pause menu is open.
static int coop_free_real(void) { return botw_player_real_pos(api) != 0 && !botw_paused(api); }
static int (*g_coop_free)(void) = coop_free_real;

typedef struct {
    int n, ready, safe;
    int64_t seq, reads, read_fails, sets, set_refused, busy_waits, no_world_waits, pending, held, unsafe_waits;
    uint8_t bits[COOP_BYTES];
} CoopPub;
static CoopPub g_coop_pub;                     // (under the lock) what `coop story get|status` shows
static CoopPub t_coop;                         // (tick) the same, copied out once a tick

// `coop probe flag <name> [1]` (developer mode): one read or set on the next tick.
static struct {
    int want, set, done;
    int64_t ok, value;
    char name[COOP_NAME];
} g_coop_probe;
static _Alignas(16) StrObj g_coop_probe_obj;

#define BIT_GET(b, i) (((b)[(i) >> 3] >> ((i) & 7)) & 1)
#define BIT_SET(b, i) ((b)[(i) >> 3] |= (uint8_t)(1u << ((i) & 7)))

static uint64_t coop_mgr(void) {
    double t = now_s();
    if (!mem_ok(g_coop_mgr_slot, 8, t)) return 0;
    uint64_t m = *(volatile const uint64_t *)(uintptr_t)g_coop_mgr_slot;
    if (!guest_ptr(m) || !mem_ok(m, COOP_BUSY_OFF + 1, t)) return 0;
    return m;
}

// A name that may be added while the game runs: a tower or a memory (story.py LIVE_KINDS).
static int coop_live_name(const char *s) {
    if (!strncmp(s, "MapTower_", 9)) return 1;
    if (!strncmp(s, "IsGet_MemoryPhoto_", 18)) return 1;
    return 0;
}

// One name -> 0 or 1, or -1 if the game didn't answer.
static int coop_read(struct Cpu *c, uint64_t mgr, StrObj *obj) {
    if (!g_coop_get_fn) return -1;  // (no known getter: see COOP_GET_BOOL)
    g_coop_out[0] = 0;
    uint64_t ok = gcall(c, g_coop_get_fn, mgr, P(g_coop_out), P(obj), 0, 0, 0, 0) & 1;
    return ok ? (g_coop_out[0] & 1) : -1;
}

#define BIT_CLR(b, i) ((b)[(i) >> 3] &= (uint8_t)~(1u << ((i) & 7)))

static void coop_tick(struct Cpu *c) {
    lock();
    if (g_coop_swap) {
        g_coop_list = g_coop_next;
        g_coop_swap = 0;
        unlock();
        memset(g_coop_live, 0, sizeof g_coop_live);
        for (int i = 0; i < g_coop_list.n; i++) {
            g_coop_obj[i].vptr = STRING_OBJ_VPTR, g_coop_obj[i].str = P(g_coop_list.names[i]);
            if (coop_live_name(g_coop_list.names[i])) BIT_SET(g_coop_live, i);
        }
        memset(g_coop_have, 0, sizeof g_coop_have);
        memset(g_coop_last, 0, sizeof g_coop_last);
        memset(g_coop_pass, 0, sizeof g_coop_pass);
        memset(g_coop_want, 0, sizeof g_coop_want);
        memset(g_coop_ours, 0, sizeof g_coop_ours);
        g_coop_cursor = 0, g_coop_seen_pass = 0, g_coop_pass_fails = 0;
        g_coop_stable = 0, g_coop_was_unsafe = 0;
        memset(&t_coop, 0, sizeof t_coop);
        t_coop.n = g_coop_list.n;
        lock();
    }
    if (g_coop_req_new) {
        for (int k = 0; k < COOP_BYTES; k++) g_coop_want[k] |= g_coop_req[k];
        memset(g_coop_req, 0, sizeof g_coop_req);
        g_coop_req_new = 0;
    }
    int probe = g_coop_probe.want && !g_coop_probe.done;
    unlock();

    int n = g_coop_list.n;
    for (int i = 0; i < n; i++)  // not a tower or a memory: never live (it waits for the next join's save merge)
        if (BIT_GET(g_coop_want, i) && !BIT_GET(g_coop_live, i)) BIT_CLR(g_coop_want, i), t_coop.held++;
    // Is it a safe point? (every frame, list or not: `coop story status` "safe" also tells the agent when to set time)
    uint64_t mgr = 0;
    int world = g_coop_world() && (mgr = coop_mgr()) != 0;
    int busy = world && (*(volatile const uint8_t *)(uintptr_t)(mgr + COOP_BUSY_OFF) & COOP_BUSY_BIT);
    if (world && !busy && g_coop_free()) {
        if (g_coop_stable < 1 << 30) g_coop_stable++;
    } else {
        g_coop_stable = 0, g_coop_was_unsafe = 1;
        if (world && !busy && n > 0) t_coop.unsafe_waits++;
    }
    if (n > 0 || probe) {
        if (!world) {
            t_coop.no_world_waits++;
        } else if (busy) {
            t_coop.busy_waits++;
        } else {
            if (probe) {
                g_coop_probe_obj.vptr = STRING_OBJ_VPTR, g_coop_probe_obj.str = P(g_coop_probe.name);
                int64_t ok, value = -1;
                if (g_coop_probe.set) {
                    ok = (int64_t)(gcall(c, COOP_SET_BOOL, mgr, 1, P(&g_coop_probe_obj), 0, 0, 0, 0) & 1);
                } else {
                    value = coop_read(c, mgr, &g_coop_probe_obj);
                    ok = value >= 0;
                }
                lock();
                g_coop_probe.ok = ok, g_coop_probe.value = value, g_coop_probe.done = 1;
                unlock();
            }
            for (int k = 0; k < COOP_READS_PER_TICK && n > 0; k++) {  // read, round robin
                int i = g_coop_cursor;
                int v = coop_read(c, mgr, &g_coop_obj[i]);
                t_coop.reads++;
                if (v < 0) t_coop.read_fails++, g_coop_pass_fails++;
                else if (v) BIT_SET(g_coop_pass, i);
                if (++g_coop_cursor >= n && g_coop_pass_fails) {  // a pass with a failed read: never published
                    g_coop_cursor = 0, g_coop_pass_fails = 0;      // (a 0 there could be a 1: "ready" would lie)
                    memset(g_coop_pass, 0, sizeof g_coop_pass);
                } else if (g_coop_cursor >= n) {  // a full pass: publish it
                    g_coop_cursor = 0;
                    if (!g_coop_seen_pass) {  // (the first pass: seq 1 even when nothing is set)
                        t_coop.seq++;
                        memcpy(g_coop_last, g_coop_pass, sizeof g_coop_last);
                    }
                    memcpy(g_coop_have, g_coop_pass, sizeof g_coop_have);
                    memset(g_coop_pass, 0, sizeof g_coop_pass);
                    g_coop_seen_pass = 1;
                    t_coop.ready = 1;
                }
            }
            if (g_coop_stable >= g_coop_settle) {  // a safe point: Link free for a while
                if (g_coop_was_unsafe) {  // (after a load the save's 0 may be back: set ours again; 1 to 1 is no change)
                    for (int k = 0; k < COOP_BYTES; k++)
                        g_coop_want[k] |= g_coop_ours[k], g_coop_have[k] &= (uint8_t)~g_coop_ours[k];
                    g_coop_was_unsafe = 0;
                }
                int added = 0;  // add: only 0 -> 1, only listed live names
                for (int i = 0; i < n && added < COOP_ADDS_PER_TICK; i++) {
                    if (!BIT_GET(g_coop_want, i)) continue;
                    if (BIT_GET(g_coop_have, i)) {
                        BIT_CLR(g_coop_want, i);
                        continue;
                    }
                    added++;
                    if (gcall(c, COOP_SET_BOOL, mgr, 1, P(&g_coop_obj[i]), 0, 0, 0, 0) & 1) {
                        t_coop.sets++;
                        BIT_SET(g_coop_have, i);
                        BIT_SET(g_coop_ours, i);
                        BIT_CLR(g_coop_want, i);
                    } else {
                        t_coop.set_refused++;
                        break;  // (busy after all: next frame)
                    }
                }
            }
        }
    }
    t_coop.safe = g_coop_stable >= g_coop_settle;
    if (g_coop_seen_pass && memcmp(g_coop_have, g_coop_last, sizeof g_coop_last)) {  // a change (the game's or ours)
        t_coop.seq++;
        memcpy(g_coop_last, g_coop_have, sizeof g_coop_last);
    }
    int64_t pending = 0;
    for (int i = 0; i < n; i++) pending += BIT_GET(g_coop_want, i) && !BIT_GET(g_coop_have, i);
    t_coop.pending = pending;
    t_coop.n = n;
    memcpy(t_coop.bits, g_coop_have, sizeof t_coop.bits);
    lock();
    if (!g_coop_swap) g_coop_pub = t_coop;
    unlock();
}

// ------------------------------------------------------------------ co-op commands (frame thread: buffers only)

static int coop_name_ok(const char *s) { return valid_name(s); }  // letters, digits, _; 1-63

// The list file's text ("base Name" / "dlc Name" lines; # comments). dlc: take the dlc lines too. Returns the count,
// or -1 (a bad name, or too many).
static int coop_load_text(const char *text, int dlc) {
    static CoopList next;
    next.n = 0;
    const char *p = text;
    while (*p) {
        const char *e = strchr(p, '\n');
        size_t len = e ? (size_t)(e - p) : strlen(p);
        char line[COOP_NAME + 16];
        if (len < sizeof line) {
            memcpy(line, p, len);
            line[len] = 0;
            if (len && line[len - 1] == '\r') line[--len] = 0;
            const char *name = NULL;
            if (!strncmp(line, "base ", 5)) name = line + 5;
            else if (dlc && !strncmp(line, "dlc ", 4)) name = line + 4;
            if (name) {
                if (!coop_name_ok(name) || next.n >= COOP_MAX) return -1;
                strcpy(next.names[next.n++], name);
            }
        } else if (len && p[0] != '#') {
            return -1;
        }
        if (!e) break;
        p = e + 1;
    }
    lock();
    g_coop_next = next;
    g_coop_swap = 1;
    g_coop_gen++;                              // (an add parsed for the old list is refused: coop_add_hex)
    memset(g_coop_req, 0, sizeof g_coop_req);  // (adds asked for the old list mean nothing for this one)
    g_coop_req_new = 0;
    unlock();
    return next.n;
}

static int hexval(char ch) {
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    return -1;
}

// Queue a story line's bits to add. Returns how many bits it has, or -1 (not the list's exact length of lowercase
// hex, a bit past the list, no list yet, or a list loaded while it was being read: its bits would be other names).
static void (*g_coop_between)(void);  // (the selftest loads a list here, between reading the count and queuing)
static int coop_add_hex(const char *hex) {
    lock();
    int n = g_coop_swap ? g_coop_next.n : g_coop_list.n;
    uint32_t gen = g_coop_gen;
    unlock();
    if (g_coop_between) g_coop_between();
    size_t want_len = 2 * (size_t)((n + 7) / 8);
    if (n <= 0 || strlen(hex) != want_len) return -1;
    uint8_t b[COOP_BYTES] = {0};
    int count = 0;
    for (size_t k = 0; k < want_len / 2; k++) {
        int hi = hexval(hex[2 * k]), lo = hexval(hex[2 * k + 1]);
        if (hi < 0 || lo < 0) return -1;
        b[k] = (uint8_t)(hi << 4 | lo);
    }
    for (int i = n; i < (int)(want_len * 4); i++)
        if (BIT_GET(b, i)) return -1;
    for (int i = 0; i < n; i++) count += BIT_GET(b, i);
    lock();
    if (gen != g_coop_gen) {  // (a list was loaded meanwhile)
        unlock();
        return -1;
    }
    for (size_t k = 0; k < want_len / 2; k++) g_coop_req[k] |= b[k];
    g_coop_req_new = 1;
    unlock();
    return count;
}

static void coop_bits_hex(const uint8_t *b, int n, char *out) {  // out: 2 * COOP_BYTES + 1
    static const char H[] = "0123456789abcdef";
    int bytes = (n + 7) / 8;
    for (int k = 0; k < bytes; k++) out[2 * k] = H[b[k] >> 4], out[2 * k + 1] = H[b[k] & 15];
    out[2 * bytes] = 0;
}

#define COOP_USAGE "coop story load <story-list.txt> [dlc] | story get | story add <hex> | story status" \
                   " | probe flag <name> [1] | probe (developer mode)"

static void cmd_coop(int argc, char **argv, SwModReply *r) {
    const char *sub = argc > 1 ? argv[1] : "";
    const char *what = argc > 2 ? argv[2] : "";
    if (!strcmp(sub, "story") && !strcmp(what, "load") && (argc == 4 || (argc == 5 && !strcmp(argv[4], "dlc")))) {
        // GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING
        void *h = CreateFileA(argv[3], 0x80000000ul, 1, 0, 3, 0, 0);
        if (h == (void *)(intptr_t)-1) {
            api->reply_fail(r, "coop: can't open %s", argv[3]);
            return;
        }
        static char text[COOP_MAX * (COOP_NAME + 6) + 4096];
        unsigned long got = 0, total = 0;
        while (total < sizeof text - 1 && ReadFile(h, text + total, (unsigned long)(sizeof text - 1 - total), &got, 0)
               && got)
            total += got;
        int more = total >= sizeof text - 1;
        CloseHandle(h);
        text[total] = 0;
        int n = more ? -1 : coop_load_text(text, argc == 5);
        if (n < 0) {
            api->reply_fail(r, "coop: that isn't a story list (bad name, or more than %d)", COOP_MAX);
            return;
        }
        api->reply_int(r, "flags", n);
        return;
    }
    if (!strcmp(sub, "story") && (!strcmp(what, "get") || !strcmp(what, "status")) && argc == 3) {
        static CoopPub pub;
        lock();
        pub = g_coop_pub;
        unlock();
        api->reply_bool(r, "ready", pub.ready != 0);
        api->reply_int(r, "n", pub.n);
        api->reply_int(r, "seq", pub.seq);
        if (!strcmp(what, "get")) {
            static char hex[2 * COOP_BYTES + 1];
            coop_bits_hex(pub.bits, pub.n, hex);
            api->reply_str(r, "bits", hex);
        } else {
            api->reply_int(r, "reads", pub.reads);
            api->reply_int(r, "read_fails", pub.read_fails);
            api->reply_int(r, "sets", pub.sets);
            api->reply_int(r, "set_refused", pub.set_refused);
            api->reply_int(r, "busy_waits", pub.busy_waits);
            api->reply_int(r, "no_world_waits", pub.no_world_waits);
            api->reply_int(r, "unsafe_waits", pub.unsafe_waits);
            api->reply_int(r, "pending", pub.pending);
            api->reply_int(r, "held", pub.held);
            api->reply_bool(r, "safe", pub.safe != 0);
        }
        return;
    }
    if (!strcmp(sub, "story") && !strcmp(what, "add") && argc == 4) {
        int count = coop_add_hex(argv[3]);
        if (count < 0) {
            api->reply_fail(r, "coop: that line doesn't fit the loaded story list (load one first)");
            return;
        }
        api->reply_int(r, "queued", count);
        return;
    }
    if (!strcmp(sub, "probe")) {
        if (!api->dev_mode || !api->dev_mode()) {
            api->reply_fail(r, "coop probe: developer mode only");
            return;
        }
        if (argc == 2) {
            lock();
            int done = g_coop_probe.done;
            int64_t ok = g_coop_probe.ok, value = g_coop_probe.value;
            unlock();
            api->reply_bool(r, "done", done != 0);
            api->reply_int(r, "ok", ok);
            api->reply_int(r, "value", value);
            return;
        }
        if (argc >= 4 && argc <= 5 && !strcmp(what, "flag") && coop_name_ok(argv[3]) &&
            (argc == 4 || !strcmp(argv[4], "1"))) {
            lock();
            memset(&g_coop_probe, 0, sizeof g_coop_probe);
            strcpy(g_coop_probe.name, argv[3]);
            g_coop_probe.set = argc == 5;
            g_coop_probe.want = 1;
            unlock();
            api->reply_text(r, "coop probe: queued (see `coop probe`)");
            return;
        }
    }
    api->reply_fail(r, COOP_USAGE);
}
