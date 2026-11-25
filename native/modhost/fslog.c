// fslog.c - modhost's file log: which files the game opens, one line each (phase 1 of the Linkle live swap,
// out/research/linkle-live-swap-design.md, section 4). Part of modhost.c: it is #included there, so modhost still
// builds from modhost.c alone. Compiled on its own it is empty.
//
// Only with the file_log setting on (settings.c: SWMOD_FILE_LOG=1, which Breathalyzer's Settings > File log sets). Then
// modhost wraps the game's four file-open functions (sead FileDevice doOpen_, botw.h BOTW_ADDR_FILE_OPEN..._4: every
// call is virtual, so hle_override catches them all) and writes fslog.log in the data root, next to savagewind.log
// (the last game's log is kept as fslog.prev.log). It only logs: each call runs the game's own function with the
// registers it came with, and nothing is redirected. Off (the default): nothing is wrapped, no file is written and
// there is no `fslog` command.
//
// Per call, on the loader thread that opens the file: the game's function first, then one line with the time, the
// thread, which of the four, the open mode (w3), the result (x0: the device back = ok, 0 = fail, else its value), how
// long the open took, the device's class (its vtable), and the path (x2 is a sead::SafeString*: its char* at +8). Every
// pointer is checked with VirtualQuery before it is read; the path is cut at 255 bytes.
//
// Limits, so a storm of opens can't fill the disk or slow the game down:
//   - the same open (function, mode, result and path) gets a line once a second at most; the others in that second are
//     counted and written as one "+N more" line when the second is over;
//   - 1000 lines a second at most (SWMOD_FILE_LOG_RATE); one line counts the rest;
//   - 20 MB in all (SWMOD_FILE_LOG_MAX); then one line says the log stopped, and nothing more is written;
//   - game threads only add lines to a buffer in memory, under a spin lock; a writer thread of its own writes the
//     buffer every 250 ms (sooner when it is half full), so no game thread waits for the disk. Lines that don't fit in
//     the buffer are counted, and a line says how many.
// The `fslog` command (F12 console, any player): `fslog` shows the counts (and writes what is waiting), `fslog off`
// and `fslog on` pause and resume the log, `fslog mark <words>` adds a line of your own to find a moment later.
//
// The file redirect (API 4 fs_redirect_*, phase 2 of the Linkle live swap: linkle-live-swap-design.md 2.1, "L1") rides
// on the same four wrappers. Only with SWMOD_FILE_REDIRECT=1 (settings.c file_redirect; OFF by default, modman sets it
// for a deploy that puts copies beside the game's files) AND a mod that named redirects in its swmod_init; else nothing
// changes. A mod names "from" -> "to" pairs in a group; the table is fixed once the game runs (no lock to read it).
// While a group is on, an open of a "from" path (as the game asks for it; a "content:/" style prefix is kept) gets x2
// = our own SafeString with the "to" path: the game object's vptr (so its own virtual calls work) in the layout of a
// sead::FixedSafeString<256>, in memory below 2^39 outside the guest window (room.c's rule), made once per redirect
// and SafeString class and never freed. If the copy fails to open (x0 = 0), the game's function runs again with the
// registers it came with: the original opens, as the game asked, and the group counts a fail. Groups flip from any
// thread (an atomic flag); fs_redirect_state says when the loaders are idle (in_flight, idle_ms), the time to flip.
// The file log's lines say the redirect in their note column.
#ifdef MODHOST_C

#include "botw.h"

#define FL_FNS 4
#define FL_BUF (256u << 10)  // each of the two line buffers
#define FL_PATH 256          // a path's text: 255 bytes at most (a longer one ends in "...")
#define FL_SLOTS 1024        // opens remembered for the once-a-second rule
#define FL_PROBE 8
#define FL_WINDOW_MS 1000
#define FL_TICK_MS 250       // the writer writes this often
#define FL_ROOM 512          // bytes kept (in the size limit and in each buffer) for the closing lines
#define FL_LINE 1024
#define FL_NOTE 1            // fl_put: a note (not counted as lost when it doesn't fit)
#define FL_RESERVE 2         // fl_put: a closing line (may use FL_ROOM, and goes in after the stop too)

typedef struct {
    uint64_t base, alloc_base;
    uint32_t alloc_protect;
    uint16_t partition;
    uint64_t region_size;
    uint32_t state, protect, type;
} FlMemInfo;  // MEMORY_BASIC_INFORMATION (64-bit)
typedef struct {
    uint16_t year, month, day_of_week, day, hour, minute, second, ms;
} FlTime;     // SYSTEMTIME
__declspec(dllimport) size_t WINAPI VirtualQuery(const void *p, FlMemInfo *out, size_t n);
__declspec(dllimport) void WINAPI GetLocalTime(FlTime *out);
#define FL_MEM_COMMIT 0x1000u
#define FL_PAGE_READ 0xeeu   // PAGE_READONLY, READWRITE, WRITECOPY and their EXECUTE_ forms
#define FL_PAGE_GUARD 0x100u
// (QueryPerformanceCounter and -Frequency: overlay.c; the file calls and the thread: settings.c)

// The four functions, in the order of the plan. id is the log's fn column (the end of the address).
static const struct {
    uint64_t addr;
    const char *id;
    const char *name;  // the override's name (Savage Wind keeps the pointer)
} k_fl_fns[FL_FNS] = {
    {BOTW_ADDR_FILE_OPEN, "e8fae0", "modhost_fslog_e8fae0"},
    {BOTW_ADDR_FILE_OPEN_2, "e92690", "modhost_fslog_e92690"},
    {BOTW_ADDR_FILE_OPEN_3, "15cba0", "modhost_fslog_15cba0"},
    {BOTW_ADDR_FILE_OPEN_4, "eaba98", "modhost_fslog_eaba98"},
};

typedef struct {
    uint64_t key;              // 0: free
    int64_t first_ms, last_ms; // first_ms: when its line was written (its second starts there)
    uint32_t more;             // the same open again since that line, not written yet
    uint32_t mode;
    int fn;
    char res[24];
    char path[FL_PATH + 4];
} FlSlot;

static struct {
    bool on;                       // the file log was on at game start (its file is open)
    uint32_t active;               // 1: lines are made (not paused, not full) (atomic)
    uint32_t paused, full;         // (atomic; changed under the lock)
    uint32_t lock, io_lock;        // lock: the buffers and counts; io_lock: one writer at a time
    HANDLE file, event;
    char path[PATH_U];
    char buf[2][FL_BUF];
    size_t len[2];
    int cur;                       // the buffer lines go in
    bool signaled, wake;           // the writer was woken for this buffer / wake it after the unlock
    uint64_t bytes, max_bytes;     // bytes made so far, the limit
    uint32_t rate;                 // lines a second at most
    int64_t sec;                   // the second the rate counts
    uint32_t sec_lines, sec_dropped;
    uint64_t lines, more, rate_dropped, lost, lost_note, unreadable, paused_opens, write_fails;
    long long qpf, qp0;
    uint64_t opens[FL_FNS];        // calls of each function (atomic)
    GuestFn orig[FL_FNS];          // the game's functions (NULL: not wrapped)
    int wrapped;
    FlSlot slots[FL_SLOTS];
} g_fl;

static int64_t fl_us(void) {  // microseconds since the log started
    long long now = 0;
    QueryPerformanceCounter(&now);
    long long d = now - g_fl.qp0, f = g_fl.qpf > 0 ? g_fl.qpf : 1;
    return (int64_t)(d / f * 1000000 + d % f * 1000000 / f);
}

static void fl_lock(void) {
    for (int n = 0; __atomic_exchange_n(&g_fl.lock, 1, __ATOMIC_ACQUIRE); n++)
        if (n > 64) Sleep(0);
        else __builtin_ia32_pause();
}
static void fl_unlock(void) {  // (and wake the writer when a line filled half the buffer)
    bool wake = g_fl.wake;
    g_fl.wake = false;
    __atomic_store_n(&g_fl.lock, 0, __ATOMIC_RELEASE);
    if (wake && g_fl.event) SetEvent(g_fl.event);
}

static void fl_set_active(void) {
    __atomic_store_n(&g_fl.active, !g_fl.paused && !g_fl.full && g_fl.wrapped > 0, __ATOMIC_RELEASE);
}

static void fl_stop(void);

// Add a line to the buffer (lock held): kind 0 a line, FL_NOTE, or FL_RESERVE. Past the size limit the stop line goes
// in instead and the log stops. false: not added.
static bool fl_put(const char *s, size_t n, int kind) {
    size_t room = kind == FL_RESERVE ? 0 : FL_ROOM;
    if (kind != FL_RESERVE && g_fl.full) return false;
    if (g_fl.bytes + n + room > g_fl.max_bytes) {
        if (kind != FL_RESERVE) fl_stop();
        return false;
    }
    size_t *len = &g_fl.len[g_fl.cur];
    if (*len + n + room > FL_BUF) {
        if (!kind) g_fl.lost++, g_fl.lost_note++;
        return false;
    }
    memcpy(g_fl.buf[g_fl.cur] + *len, s, n);
    *len += n;
    g_fl.bytes += n;
    g_fl.lines++;
    if (!g_fl.signaled && *len > FL_BUF / 2) g_fl.signaled = g_fl.wake = true;
    return true;
}

static bool fl_printf(int kind, const char *fmt, ...) {
    char line[FL_LINE];
    sw_va_list ap;
    sw_va_start(ap, fmt);
    int n = vsnprintf(line, sizeof line, fmt, ap);
    sw_va_end(ap);
    if (n <= 0) return false;
    if ((size_t)n >= sizeof line) n = (int)sizeof line - 1, line[n - 1] = '\n';
    return fl_put(line, (size_t)n, kind);
}

static double fl_secs(int64_t ms) { return (double)ms / 1000.0; }

static void fl_stop(void) {  // (lock held)
    __atomic_store_n(&g_fl.full, 1, __ATOMIC_RELEASE);
    fl_set_active();
    fl_printf(FL_RESERVE, "# %.3f stopped: the log reached its size limit (%llu KB). Nothing more is written. "
              "Restart the game for a new log.\n", fl_secs(fl_us() / 1000),
              (unsigned long long)(g_fl.max_bytes >> 10));
}

// A new second for the rate: say how many opens the last one dropped (lock held). Only forward: lines come in out of
// order across the loader threads (a long open that started in second S is recorded after another thread's S+1), and
// going back to S would reset the count twice and let extra lines through (review 2026-11-21).
static void fl_roll(int64_t now_ms) {
    int64_t sec = now_ms / 1000;
    if (sec <= g_fl.sec) return;
    if (g_fl.sec_dropped)
        fl_printf(FL_NOTE, "# %.3f %u opens not written in the second before: more than %u lines a second\n",
                  fl_secs(now_ms), g_fl.sec_dropped, g_fl.rate);
    g_fl.sec = sec;
    g_fl.sec_lines = g_fl.sec_dropped = 0;
}

// The "+N more" line of an open whose repeats weren't written yet (lock held). Its time is its line's: it comes into
// the file later, when that second is over.
static void fl_more(FlSlot *s) {
    fl_printf(FL_NOTE, "%.3f\t-\t%s\t%u\t%s\t-\t-\t%s\t+%u more until %.3f\n", fl_secs(s->first_ms),
              k_fl_fns[s->fn].id, s->mode, s->res, s->path, s->more, fl_secs(s->last_ms));
    s->more = 0;
}

static uint64_t fl_hash(int fn, uint32_t mode, const char *res, const char *path, const char *note) {
    uint64_t h = 1469598103934665603ull;
    h = (h ^ (uint64_t)fn) * 1099511628211ull;
    h = (h ^ mode) * 1099511628211ull;
    for (const char *p = res; *p; p++) h = (h ^ (uint8_t)*p) * 1099511628211ull;
    h = (h ^ 0xff) * 1099511628211ull;
    for (const char *p = path; *p; p++) h = (h ^ (uint8_t)*p) * 1099511628211ull;
    h = (h ^ 0xfe) * 1099511628211ull;
    for (const char *p = note; *p; p++) h = (h ^ (uint8_t)*p) * 1099511628211ull;
    return h ? h : 1;
}

// One open (lock held): its line, or one more for the line of the same open less than a second ago.
static void fl_record(int fn, uint32_t mode, const char *res, int64_t t_us, double ms, DWORD tid, const char *dev,
                      const char *path, const char *note) {
    if (!__atomic_load_n(&g_fl.active, __ATOMIC_ACQUIRE)) {  // (paused or stopped since the call started)
        if (g_fl.paused) __atomic_fetch_add(&g_fl.paused_opens, 1, __ATOMIC_RELAXED);
        return;
    }
    int64_t now = t_us / 1000;
    fl_roll(now);
    uint64_t key = fl_hash(fn, mode, res, path, note);
    FlSlot *hit = NULL, *victim = NULL;  // (victim: a free slot, else the one used longest ago)
    for (int k = 0; k < FL_PROBE && !hit; k++) {
        FlSlot *s = &g_fl.slots[(key + (uint64_t)k) % FL_SLOTS];
        if (s->key == key) hit = s;
        else if (!victim || (!s->key && victim->key) || (s->key && victim->key && s->last_ms < victim->last_ms))
            victim = s;
    }
    if (hit && now - hit->first_ms < FL_WINDOW_MS) {  // (now may be a little before first_ms: out of order, above)
        hit->more++;
        if (now > hit->last_ms) hit->last_ms = now;
        g_fl.more++;
        return;
    }
    FlSlot *s = hit ? hit : victim;
    if (s->key && s->more) fl_more(s);
    if (g_fl.sec_lines >= g_fl.rate) {
        g_fl.sec_dropped++;
        g_fl.rate_dropped++;
        return;
    }
    g_fl.sec_lines++;
    if (!fl_printf(0, "%.3f\t%lu\t%s\t%u\t%s\t%.3f\t%s\t%s\t%s\n", fl_secs(now), tid, k_fl_fns[fn].id, mode, res, ms,
                   dev, path, note))
        return;
    s->key = key;
    s->first_ms = s->last_ms = now;
    s->more = 0;
    s->mode = mode;
    s->fn = fn;
    copy_str(s->res, sizeof s->res, res);
    copy_str(s->path, sizeof s->path, path);
}

// How many bytes from p on can be read, up to want (0: none).
static size_t fl_readable(uint64_t p, size_t want) {
    size_t got = 0;
    for (int regions = 0; got < want && regions < 4; regions++) {
        uint64_t a = p + got;
        FlMemInfo mi;
        memset(&mi, 0, sizeof mi);
        if (a < 0x10000 || a >= 0x800000000000ull || !VirtualQuery((const void *)(uintptr_t)a, &mi, sizeof mi) ||
            mi.state != FL_MEM_COMMIT || !(mi.protect & FL_PAGE_READ) || (mi.protect & FL_PAGE_GUARD))
            break;
        uint64_t end = mi.base + mi.region_size;
        if (end <= a) break;
        got += end - a >= want - got ? want - got : (size_t)(end - a);
    }
    return got;
}

// The path a sead::SafeString* points at (its char* at +8), as one line of text: control characters become ?, at most
// 255 bytes ("..." after a longer one), "(no path)" for none and "(unreadable)" when a pointer can't be read.
static void fl_path(uint64_t ss, char *out, size_t cap) {
    uint64_t obj[2];
    if (!ss) {
        copy_str(out, cap, "(no path)");
        return;
    }
    if (fl_readable(ss, sizeof obj) < sizeof obj) {
        __atomic_fetch_add(&g_fl.unreadable, 1, __ATOMIC_RELAXED);
        copy_str(out, cap, "(unreadable)");
        return;
    }
    memcpy(obj, (const void *)(uintptr_t)ss, sizeof obj);
    if (!obj[1]) {
        copy_str(out, cap, "(no path)");
        return;
    }
    size_t max = FL_PATH - 1 < cap - 4 ? FL_PATH - 1 : cap - 4;
    size_t avail = fl_readable(obj[1], max + 1), n = 0;
    if (!avail) {
        __atomic_fetch_add(&g_fl.unreadable, 1, __ATOMIC_RELAXED);
        copy_str(out, cap, "(unreadable)");
        return;
    }
    const volatile unsigned char *p = (const volatile unsigned char *)(uintptr_t)obj[1];
    for (; n < avail && n < max && p[n]; n++) out[n] = p[n] < 0x20 || p[n] == 0x7f ? '?' : (char)p[n];
    if (n == avail || p[n]) memcpy(out + n, "...", 3), n += 3;  // (longer, or it runs into memory we can't read)
    out[n] = 0;
}

// ---- the file redirect (API 4): the table, and one redirected open

#define FR_GROUPS 8
#define FR_MAX 2048
#define FR_HASH 4096                 // (a power of two, at least twice FR_MAX)
#define FR_GROUP_NAME 32
#define FR_PATH_MAX 200              // a "from" or "to" path: 200 bytes at most
#define FR_STR 256                   // our SafeString's buffer: the game's prefix + the "to" path
#define FR_VARIANTS 4                // SafeString classes (and prefixes) per redirect
#define FR_TEXT (256u << 10)         // the paths' text
#define FR_LOW_FROM 0x7600000000ull  // our SafeStrings: the first free run from here, below 2^39 (room.c's rule)
#define FR_LOW_TO (1ull << 39)
#define FR_LOW_STEP (1ull << 20)
#define FR_FAIL_LINES 20             // savagewind.log lines for copies that didn't open, at most
__declspec(dllimport) void *WINAPI VirtualAlloc(void *addr, size_t size, DWORD type, DWORD protect);

typedef struct {  // laid out as sead::FixedSafeString<FR_STR>: the vptr, the char* (buf), the buffer's size, buf at +0x14
    uint64_t vptr, top;
    int32_t size;
    char buf[FR_STR];
    uint16_t plen;  // (ours, after the game's part: how much of buf is the game's prefix)
} FrStr;
_Static_assert(offsetof(FrStr, buf) == 0x14, "FrStr: the buffer of a FixedSafeString is at +0x14");

typedef struct {
    const char *from, *to;
    uint32_t hash;
    int group;
    FrStr *obj[FR_VARIANTS];  // (made at first use, never freed: the game may keep the pointer)
} FrEntry;

typedef struct {
    char name[FR_GROUP_NAME];
    uint32_t on;                       // (atomic)
    int count;
    uint64_t opens, redirects, fails;  // (atomic)
} FrGroup;

static struct {
    bool sealed;            // the game runs: the table is fixed
    int ngroups, n;
    FrGroup g[FR_GROUPS];
    FrEntry e[FR_MAX];
    int32_t slot[FR_HASH];  // entry index + 1, 0: free
    char text[FR_TEXT];
    size_t text_used;
    int32_t in_flight;      // opens running now (atomic)
    uint64_t last_end_ms;   // GetTickCount64 when the last open ended (atomic)
    uint8_t *low;           // our SafeStrings (NULL: none could be made: no group is ready)
    size_t low_cap, low_used;
    uint64_t nomem, fail_lines;
} g_fr;

static uint32_t fr_hash(const char *s) {
    uint32_t h = 2166136261u;
    for (; *s; s++) h = (h ^ (uint8_t)*s) * 16777619u;
    return h;
}

static FrEntry *fr_find(const char *path) {
    uint32_t h = fr_hash(path);
    for (uint32_t k = 0; k < FR_HASH; k++) {
        int32_t s = g_fr.slot[(h + k) & (FR_HASH - 1)];
        if (!s) return NULL;
        FrEntry *e = &g_fr.e[s - 1];
        if (e->hash == h && !strcmp(e->from, path)) return e;
    }
    return NULL;
}

static int fr_group(const char *name) {
    for (int i = 0; i < g_fr.ngroups; i++)
        if (!strcmp(g_fr.g[i].name, name)) return i;
    return -1;
}

static bool fr_ready(void) { return g_fr.sealed && g_fr.low && g_fl.wrapped == FL_FNS; }

// The path at a sead::SafeString* and its vptr: false when it can't be read, or is FR_STR bytes or longer.
static bool fr_read(uint64_t ss, char *out, uint64_t *vptr) {
    uint64_t obj[2];
    if (!ss || fl_readable(ss, sizeof obj) < sizeof obj) return false;
    memcpy(obj, (const void *)(uintptr_t)ss, sizeof obj);
    size_t avail = obj[1] ? fl_readable(obj[1], FR_STR) : 0;
    const volatile char *p = (const volatile char *)(uintptr_t)obj[1];
    for (size_t n = 0; n < avail; n++)
        if (!(out[n] = p[n])) {
            *vptr = obj[0];
            return true;
        }
    return false;
}

// Our SafeString for this redirect, SafeString class and prefix (made the first time; NULL: no room).
static FrStr *fr_obj(FrEntry *e, uint64_t vptr, const char *path, size_t plen) {
    int k0 = 0;  // (the slots fill in order: the first empty one ends the search)
    for (; k0 < FR_VARIANTS; k0++) {
        FrStr *o = __atomic_load_n(&e->obj[k0], __ATOMIC_ACQUIRE);
        if (!o) break;
        if (o->vptr == vptr && o->plen == plen && !memcmp(o->buf, path, plen)) return o;
    }
    if (k0 == FR_VARIANTS) return NULL;  // (every slot taken by other classes: the original, and no memory used)
    size_t need = (sizeof(FrStr) + 15) & ~(size_t)15;
    size_t at = __atomic_fetch_add(&g_fr.low_used, need, __ATOMIC_RELAXED);
    if (!g_fr.low || at + need > g_fr.low_cap) return NULL;
    FrStr *o = (FrStr *)(void *)(g_fr.low + at);
    memset(o, 0, sizeof *o);
    o->vptr = vptr;
    o->top = (uint64_t)(uintptr_t)o->buf;
    o->size = FR_STR;
    o->plen = (uint16_t)plen;
    memcpy(o->buf, path, plen);
    copy_str(o->buf + plen, FR_STR - plen, e->to);
    for (int k = k0; k < FR_VARIANTS; k++) {
        FrStr *had = NULL;
        if (__atomic_compare_exchange_n(&e->obj[k], &had, o, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return o;
        if (had->vptr == vptr && had->plen == plen && !memcmp(had->buf, path, plen)) return had;  // (another thread's)
    }
    return NULL;  // (more classes than FR_VARIANTS: this one opens the original)
}

// One open through wrapper i: true when it was a redirect (the game's function ran here, note says what happened);
// false: no redirect, the caller runs the game's function.
static bool fr_open(struct Cpu *c, int i, uint64_t ss, char *note, size_t cap) {
    char path[FR_STR];
    uint64_t vptr = 0;
    if (!fr_read(ss, path, &vptr)) return false;
    size_t plen = 0;  // (a device prefix the game put in front, like "content:/": kept)
    for (size_t k = 0; k < 32 && path[k]; k++)
        if (path[k] == ':' && path[k + 1] == '/') {
            plen = k + 2;
            break;
        }
    FrEntry *e = fr_find(path + plen);
    if (!e) return false;
    FrGroup *g = &g_fr.g[e->group];
    __atomic_fetch_add(&g->opens, 1, __ATOMIC_RELAXED);
    if (!__atomic_load_n(&g->on, __ATOMIC_ACQUIRE)) return false;
    FrStr *o = plen + strlen(e->to) < FR_STR ? fr_obj(e, vptr, path, plen) : NULL;
    if (!o) {
        __atomic_fetch_add(&g_fr.nomem, 1, __ATOMIC_RELAXED);
        snprintf(note, cap, "redirect skipped (no room): the original");
        return false;
    }
    SwModRegs *r = SWMOD_REGS(c);
    uint64_t saved[19];  // (x0-x18: the arguments and what the call may change; the game keeps x19 on)
    memcpy(saved, r->x, sizeof saved);
    r->x[2] = (uint64_t)(uintptr_t)o;
    __atomic_fetch_add(&g->redirects, 1, __ATOMIC_RELAXED);
    g_fl.orig[i](c);
    if (r->x[0]) {
        snprintf(note, cap, "-> %s", o->buf);
        return true;
    }
    __atomic_fetch_add(&g->fails, 1, __ATOMIC_RELAXED);
    memcpy(r->x, saved, sizeof saved);  // the copy didn't open: the original, as the game asked
    g_fl.orig[i](c);
    snprintf(note, cap, "copy %s didn't open: the original", o->buf);
    if (__atomic_fetch_add(&g_fr.fail_lines, 1, __ATOMIC_RELAXED) < FR_FAIL_LINES)
        host_log("file redirect: group %s: %s didn't open, so %s did (as the game asked)", g->name, o->buf, path);
    return true;
}

static void fl_call(struct Cpu *c, int i) {
    __atomic_fetch_add(&g_fl.opens[i], 1, __ATOMIC_RELAXED);
    SwModRegs *r = SWMOD_REGS(c);
    uint64_t dev = r->x[0], ss = r->x[2];  // (x2 and x3 aren't kept by the call: read them first)
    uint32_t mode = (uint32_t)r->x[3];
    bool log = __atomic_load_n(&g_fl.active, __ATOMIC_ACQUIRE), redir = g_fr.n > 0 && g_fr.low;
    if (!log && __atomic_load_n(&g_fl.paused, __ATOMIC_RELAXED))  // (paused: no line)
        __atomic_fetch_add(&g_fl.paused_opens, 1, __ATOMIC_RELAXED);
    char note[FR_STR + 48];
    note[0] = 0;
    int64_t t0 = log ? fl_us() : 0;
    if (redir) __atomic_fetch_add(&g_fr.in_flight, 1, __ATOMIC_ACQ_REL);
    if (!redir || !fr_open(c, i, ss, note, sizeof note)) g_fl.orig[i](c);
    if (redir) {
        __atomic_store_n(&g_fr.last_end_ms, GetTickCount64(), __ATOMIC_RELEASE);
        __atomic_fetch_sub(&g_fr.in_flight, 1, __ATOMIC_ACQ_REL);
    }
    if (!log) return;
    int64_t t1 = fl_us();
    uint64_t ret = r->x[0];
    char path[FL_PATH + 4], devt[24], res[24];
    fl_path(ss, path, sizeof path);
    uint64_t vt = 0;
    if (dev && fl_readable(dev, 8) == 8) memcpy(&vt, (const void *)(uintptr_t)dev, 8);
    if (vt) snprintf(devt, sizeof devt, "%llx", (unsigned long long)vt);
    else copy_str(devt, sizeof devt, "-");
    if (!ret) copy_str(res, sizeof res, "fail");
    else if (ret == dev) copy_str(res, sizeof res, "ok");
    else snprintf(res, sizeof res, "ret=%llx", (unsigned long long)ret);
    for (char *p = note; *p; p++)
        if ((unsigned char)*p < 0x20 || *p == 0x7f) *p = '?';
    fl_lock();
    fl_record(i, mode, res, t0, (double)(t1 - t0) / 1000.0, GetCurrentThreadId(), devt, path, note);
    fl_unlock();
}

static void fl_wrap_0(struct Cpu *c) { fl_call(c, 0); }
static void fl_wrap_1(struct Cpu *c) { fl_call(c, 1); }
static void fl_wrap_2(struct Cpu *c) { fl_call(c, 2); }
static void fl_wrap_3(struct Cpu *c) { fl_call(c, 3); }
static const HostFn k_fl_wraps[FL_FNS] = {fl_wrap_0, fl_wrap_1, fl_wrap_2, fl_wrap_3};

// Write what is waiting: the "+N more" lines of seconds that are over (closing: all), the notes, then the buffer.
// closing: from the exit list, when the other threads are gone; a lock a dead thread held is only checked.
static void fl_flush(bool closing) {
    if (!g_fl.on) return;
    if (closing) {
        if (__atomic_exchange_n(&g_fl.io_lock, 1, __ATOMIC_ACQUIRE)) return;
        if (__atomic_exchange_n(&g_fl.lock, 1, __ATOMIC_ACQUIRE)) {
            __atomic_store_n(&g_fl.io_lock, 0, __ATOMIC_RELEASE);
            return;
        }
    } else {
        while (__atomic_exchange_n(&g_fl.io_lock, 1, __ATOMIC_ACQUIRE)) Sleep(1);
        fl_lock();
    }
    int64_t now = fl_us() / 1000;
    fl_roll(now);
    for (int k = 0; k < FL_SLOTS; k++) {
        FlSlot *s = &g_fl.slots[k];
        if (s->key && s->more && (closing || now - s->first_ms >= FL_WINDOW_MS)) fl_more(s);
    }
    uint64_t lost = g_fl.lost_note;
    if (lost && fl_printf(FL_NOTE, "# %.3f %llu lines lost: the game opened files faster than they could be written\n",
                          fl_secs(now), (unsigned long long)lost))
        g_fl.lost_note -= lost;
    if (closing) {
        uint64_t opens = 0;
        for (int i = 0; i < FL_FNS; i++) opens += __atomic_load_n(&g_fl.opens[i], __ATOMIC_RELAXED);
        fl_printf(FL_RESERVE, "# %.3f game closed: %llu opens, %llu lines, %llu counted in \"more\" lines, %llu over "
                  "the rate, %llu lost\n", fl_secs(now), (unsigned long long)opens, (unsigned long long)g_fl.lines,
                  (unsigned long long)g_fl.more, (unsigned long long)g_fl.rate_dropped,
                  (unsigned long long)g_fl.lost);
    }
    int w = g_fl.cur;
    size_t n = g_fl.len[w];
    g_fl.cur = w ^ 1;
    g_fl.signaled = false;
    __atomic_store_n(&g_fl.lock, 0, __ATOMIC_RELEASE);
    for (size_t done = 0; n && done < n && g_fl.file;) {
        DWORD wrote = 0, part = n - done > (1u << 30) ? 1u << 30 : (DWORD)(n - done);
        if (!WriteFile(g_fl.file, g_fl.buf[w] + done, part, &wrote, NULL) || !wrote) {
            if (!g_fl.write_fails++) {
                host_log("file log: can't write %s (error %lu); the log stops here", g_fl.path, GetLastError());
                __atomic_store_n(&g_fl.full, 1, __ATOMIC_RELEASE);
                __atomic_store_n(&g_fl.active, 0, __ATOMIC_RELEASE);
            }
            break;
        }
        done += wrote;
    }
    g_fl.len[w] = 0;
    __atomic_store_n(&g_fl.io_lock, 0, __ATOMIC_RELEASE);
}

static DWORD WINAPI fl_writer(void *arg) {
    (void)arg;
    for (;;) {
        WaitForSingleObject(g_fl.event, FL_TICK_MS);
        fl_flush(false);
    }
    return 0;
}

static void fl_exit(void) { fl_flush(true); }

// ---- the `fslog` command (F12 console: any player; it only changes what the log writes)

static void cmd_fslog(int argc, char **argv, ControlReply *r) {
    const char *a = argc > 1 ? argv[1] : "";
    if (!g_fl.on) {
        control_fail(r, "The file log didn't start (see the [modhost] lines in savagewind.log).");
        return;
    }
    if (*a && strcmp(a, "on") && strcmp(a, "off") && strcmp(a, "mark")) {
        control_fail(r, "usage: fslog [on|off|mark <words>]");
        return;
    }
    fl_lock();
    double now = fl_secs(fl_us() / 1000);
    const char *fail = NULL;
    if (!strcmp(a, "off") && !g_fl.paused && !g_fl.full) {
        fl_printf(FL_NOTE, "# %.3f paused (fslog off)\n", now);
        __atomic_store_n(&g_fl.paused, 1, __ATOMIC_RELEASE);
    } else if (!strcmp(a, "on")) {
        if (g_fl.full) {
            fail = "The file log is full. Restart the game for a new one.";
        } else if (g_fl.paused) {
            __atomic_store_n(&g_fl.paused, 0, __ATOMIC_RELEASE);
            fl_printf(FL_NOTE, "# %.3f resumed (fslog on): %llu opens while paused were not written\n", now,
                      (unsigned long long)__atomic_exchange_n(&g_fl.paused_opens, 0, __ATOMIC_ACQ_REL));
        }
    } else if (!strcmp(a, "mark")) {
        char words[256];
        join_args(2, argc, argv, words, sizeof words);
        for (char *p = words; *p; p++)
            if ((unsigned char)*p < 0x20 || *p == 0x7f) *p = ' ';
        if (g_fl.full) fail = "The file log is full: the mark wasn't written.";
        else fl_printf(FL_NOTE, "# %.3f mark: %s\n", now, words);
    }
    fl_set_active();
    fl_unlock();
    if (fail) {
        control_fail(r, "%s", fail);
        return;
    }
    fl_flush(false);  // (what is waiting is in the file before the answer)
    fl_lock();
    uint64_t opens = 0;
    for (int i = 0; i < FL_FNS; i++) {
        char key[32];
        uint64_t n = __atomic_load_n(&g_fl.opens[i], __ATOMIC_RELAXED);
        opens += n;
        snprintf(key, sizeof key, "opens.%s", k_fl_fns[i].id);
        control_field_int(r, key, (int64_t)n);
    }
    bool paused = g_fl.paused, full = g_fl.full;
    uint64_t lines = g_fl.lines, bytes = g_fl.bytes;
    control_field_bool(r, "on", true);
    control_field_bool(r, "paused", paused);
    control_field_bool(r, "full", full);
    control_field_str(r, "file", g_fl.path);
    control_field_int(r, "wrapped", g_fl.wrapped);
    control_field_int(r, "opens", (int64_t)opens);
    control_field_int(r, "lines", (int64_t)lines);
    control_field_int(r, "bytes", (int64_t)bytes);
    control_field_int(r, "max_bytes", (int64_t)g_fl.max_bytes);
    control_field_int(r, "more", (int64_t)g_fl.more);
    control_field_int(r, "rate_dropped", (int64_t)g_fl.rate_dropped);
    control_field_int(r, "lost", (int64_t)g_fl.lost);
    control_field_int(r, "unreadable", (int64_t)__atomic_load_n(&g_fl.unreadable, __ATOMIC_RELAXED));
    control_field_int(r, "paused_opens", (int64_t)__atomic_load_n(&g_fl.paused_opens, __ATOMIC_RELAXED));
    fl_unlock();
    control_text(r, "File log: %s. %llu files opened, %llu lines, %llu of %llu KB.",
                 full ? "full (restart the game for a new one)" : paused ? "paused (fslog on to go on)" : "on",
                 (unsigned long long)opens, (unsigned long long)lines, (unsigned long long)(bytes >> 10),
                 (unsigned long long)(g_fl.max_bytes >> 10));
    control_text(r, "%s", g_fl.path);
}

// ---- start (swgame_tables)

// Before the mods load: the `fslog` command, only when the file log is on (settings.c st_host_load).
static void fl_setup(void) {
    if (!g_st_host.file_log) return;
    static const ControlCmd cmd = {"fslog", "[on|off|mark <words>]",
                                   "File log: show it, pause it (off), go on (on) or add a line (mark)", cmd_fslog,
                                   CONTROL_PLAYER};
    if (register_one(&cmd) != REG_ADDED)
        host_log("couldn't add the command fslog (the name is taken?): the file log still writes");
}

static void fl_open_log(void);
static void fl_wrap_all(void);
static bool fr_seal(void);

// After the mods loaded (their overrides went in first): fix the redirect table, open fslog.log, wrap the four
// functions (for the log, the redirect or both), start the writer.
static void fl_install(void) {
    bool redir = fr_seal();
    if (!g_st_host.file_log && !redir) return;
    if (g_st_host.file_log) fl_open_log();
    if (!g_fl.on && !redir) return;  // (the log's file couldn't be made: nothing to wrap for)
    fl_wrap_all();
    if (redir)
        host_log("file redirect: on, %d of %d file opens wrapped%s", g_fl.wrapped, FL_FNS,
                 g_fl.wrapped == FL_FNS ? "" : ": not ready (a mod overrides one of them), no file is redirected");
    if (!g_fl.on) return;
    fl_lock();
    fl_set_active();
    fl_unlock();
    g_fl.event = CreateEventW(NULL, 0, 0, NULL);
    HANDLE w = g_fl.event ? CreateThread(NULL, 0, fl_writer, NULL, 0, NULL) : NULL;
    if (w) CloseHandle(w);
    else host_log("file log: can't start its writer; lines are written by the fslog command and when the game closes");
    atexit(fl_exit);
    host_log("file log: on, %d of %d file opens wrapped, writing %s (fslog off pauses it)", g_fl.wrapped, FL_FNS,
             g_fl.path);
}

// The file log's file and its first lines (g_fl.on: it is open).
static void fl_open_log(void) {
    QueryPerformanceFrequency(&g_fl.qpf);
    QueryPerformanceCounter(&g_fl.qp0);
    g_fl.max_bytes = g_st_host.file_log_max;
    g_fl.rate = g_st_host.file_log_rate;
    wchar_t path[PATH_W], prev[PATH_W];
    if (!full_path(L"fslog.log", path, PATH_W) || !full_path(L"fslog.prev.log", prev, PATH_W)) {
        host_log("file log: the path is too long; no file log");
        return;
    }
    to_utf8(path, g_fl.path, sizeof g_fl.path);
    MoveFileExW(path, prev, ST_MOVE_REPLACE);  // (the last game's log is kept; none: nothing to keep)
    HANDLE f = CreateFileW(path, ST_GENERIC_WRITE, ST_SHARE_ALL, NULL, ST_CREATE_ALWAYS, ST_ATTR_NORMAL, NULL);
    if (f == SW_INVALID_HANDLE) {
        host_log("file log: can't write %s (error %lu); no file log", g_fl.path, GetLastError());
        return;
    }
    g_fl.file = f;
    g_fl.on = true;
    FlTime t;
    memset(&t, 0, sizeof t);
    GetLocalTime(&t);
    fl_lock();
    fl_printf(FL_NOTE,
              "# fslog: the files the game opened (modhost %s, game %s), started %04u-%02u-%02u %02u:%02u:%02u.\n",
              MODHOST_VERSION, g_real && g_real->version ? g_real->version : "?", t.year, t.month, t.day, t.hour,
              t.minute, t.second);
    fl_printf(FL_NOTE, "# One line per open, tab separated. time: seconds since this log started. result: ok (the "
              "device came back), fail (0), or what came back.\n");
    fl_printf(FL_NOTE, "# The same open again within a second is counted in one \"+N more\" line (it has the time of "
              "the first, and comes when that second is over). Limits: %u lines a second, %llu KB.\n", g_fl.rate,
              (unsigned long long)(g_fl.max_bytes >> 10));
    fl_printf(FL_NOTE, "# time\tthread\tfn\tmode\tresult\tms\tdevice\tpath\tnote\n");
    fl_unlock();
}

// Wrap the four file opens, each unless a mod overrides it already (the first override wins).
static void fl_wrap_all(void) {
    for (int i = 0; i < FL_FNS; i++) {
        uint64_t a = k_fl_fns[i].addr;
        const char *why = NULL;
        reg_lock();
        for (int k = 0; k < g_nstart_ovr && !why; k++)
            if (g_start_ovr[k].addr == a) why = "a mod overrides it already (the first override wins)";
        reg_unlock();
        GuestFn orig = why ? NULL : (GuestFn)api_dispatch_lookup(a);
        if (!why && !orig) why = "no game function starts there (another game version?)";
        if (why) {
            host_log("file %s: %#llx not wrapped: %s", g_fl.on ? "log" : "redirect", (unsigned long long)a, why);
            if (g_fl.on) {
                fl_lock();
                fl_printf(FL_NOTE, "# %s (%#llx) not wrapped: %s\n", k_fl_fns[i].id, (unsigned long long)a, why);
                fl_unlock();
            }
            continue;
        }
        // Noted with the overrides of game start (a mod loaded later is told it can't have it) and sent to Savage Wind
        // in one step under the lock (its override table has none): an override no one notes could go in twice.
        reg_lock();
        for (int k = 0; k < g_nstart_ovr && !why; k++)
            if (g_start_ovr[k].addr == a) why = "a mod overrides it already (the first override wins)";
        if (!why && g_nstart_ovr == MAX_START_OVERRIDES) why = "128 overrides are in already: no room to note it";
        if (!why) {
            g_fl.orig[i] = orig;
            g_start_ovr[g_nstart_ovr].addr = a;
            g_start_ovr[g_nstart_ovr].mod = 0;
            g_start_ovr[g_nstart_ovr].pending = false;
            g_start_ovr[g_nstart_ovr].fslog = true;
            g_start_ovr[g_nstart_ovr].fn = (SwModGuestFn)k_fl_wraps[i];
            g_start_ovr[g_nstart_ovr++].name = k_fl_fns[i].name;
            hle_override(a, k_fl_wraps[i], k_fl_fns[i].name);
            g_fl.wrapped++;
        }
        reg_unlock();
        if (why) {
            host_log("file %s: %#llx not wrapped: %s", g_fl.on ? "log" : "redirect", (unsigned long long)a, why);
            if (g_fl.on) {
                fl_lock();
                fl_printf(FL_NOTE, "# %s (%#llx) not wrapped: %s\n", k_fl_fns[i].id, (unsigned long long)a, why);
                fl_unlock();
            }
        }
    }
}


// ---- the file redirect's API (swmod.h version 4)

static bool fr_text_ok(const char *s, size_t max) {
    size_t n = s ? strlen(s) : 0;
    if (!n || n > max) return false;
    for (; *s; s++)
        if ((unsigned char)*s < 0x20 || *s == 0x7f) return false;
    return true;
}

static const char *fr_keep(const char *s) {  // (a copy in the table's text; NULL: full)
    size_t n = strlen(s) + 1;
    if (g_fr.text_used + n > sizeof g_fr.text) return NULL;
    char *p = g_fr.text + g_fr.text_used;
    memcpy(p, s, n);
    g_fr.text_used += n;
    return p;
}

static int api_fs_redirect_add(const char *group, const char *from, const char *to) {
    Mod *m = g_current && GetCurrentThreadId() == g_current_tid ? g_current : NULL;  // (only from swmod_init)
    const char *who = m ? m->file : "?";
    if (!g_st_host.file_redirect) return SWMOD_FSR_OFF;
    if (!m || g_fr.sealed) {
        host_log("mod %s: fs_redirect_add refused: only from swmod_init of a mod loaded at game start", who);
        return SWMOD_FSR_LATE;
    }
    if (!fr_text_ok(group, FR_GROUP_NAME - 1) || !fr_text_ok(from, FR_PATH_MAX) || !fr_text_ok(to, FR_PATH_MAX) ||
        !strcmp(from, to))
        return SWMOD_FSR_BAD_ARGS;
    int gi = fr_group(group);
    FrEntry *old = fr_find(from);
    if (old) return old->group == gi && !strcmp(old->to, to) ? SWMOD_FSR_OK : SWMOD_FSR_TAKEN;
    if (gi < 0 && g_fr.ngroups >= FR_GROUPS) return SWMOD_FSR_FULL;
    if (g_fr.n >= FR_MAX) return SWMOD_FSR_FULL;
    size_t used = g_fr.text_used;
    const char *f = fr_keep(from), *t = f ? fr_keep(to) : NULL;
    if (!t) {
        g_fr.text_used = used;
        return SWMOD_FSR_FULL;
    }
    if (gi < 0) {
        gi = g_fr.ngroups++;
        copy_str(g_fr.g[gi].name, sizeof g_fr.g[gi].name, group);
    }
    FrEntry *e = &g_fr.e[g_fr.n];
    memset(e, 0, sizeof *e);
    e->from = f, e->to = t, e->hash = fr_hash(f), e->group = gi;
    for (uint32_t k = 0;; k++) {
        int32_t *s = &g_fr.slot[(e->hash + k) & (FR_HASH - 1)];
        if (!*s) {
            *s = ++g_fr.n;
            break;
        }
    }
    g_fr.g[gi].count++;
    return SWMOD_FSR_OK;
}

static int api_fs_redirect_set(const char *group, int on) {
    if (!g_st_host.file_redirect) return SWMOD_FSR_OFF;
    int gi = group ? fr_group(group) : -1;
    if (gi < 0) return SWMOD_FSR_NO_GROUP;
    if (!fr_ready()) return SWMOD_FSR_NOT_READY;
    if (__atomic_exchange_n(&g_fr.g[gi].on, on ? 1u : 0u, __ATOMIC_ACQ_REL) != (on ? 1u : 0u))
        host_log("file redirect: group %s %s (%d files)", g_fr.g[gi].name, on ? "on" : "off", g_fr.g[gi].count);
    return SWMOD_FSR_OK;
}

static int api_fs_redirect_state(const char *group, SwModFsRedirectInfo *info) {
    if (!info || info->size < sizeof *info) return SWMOD_FSR_BAD_ARGS;
    uint32_t size = info->size;
    memset(info, 0, sizeof *info);
    info->size = size;
    info->wrapped = g_fl.wrapped;
    info->in_flight = __atomic_load_n(&g_fr.in_flight, __ATOMIC_ACQUIRE);
    uint64_t last = __atomic_load_n(&g_fr.last_end_ms, __ATOMIC_ACQUIRE), now = GetTickCount64();
    info->idle_ms = info->in_flight ? 0 : last && now > last ? (int64_t)(now - last) : last ? 0 : (int64_t)now;
    if (!g_st_host.file_redirect) return SWMOD_FSR_OFF;
    int gi = group ? fr_group(group) : -1;
    if (gi < 0) return SWMOD_FSR_NO_GROUP;
    FrGroup *g = &g_fr.g[gi];
    info->ready = fr_ready() && g->count > 0;
    info->on = (int32_t)__atomic_load_n(&g->on, __ATOMIC_ACQUIRE);
    info->count = g->count;
    info->opens = __atomic_load_n(&g->opens, __ATOMIC_RELAXED);
    info->redirects = __atomic_load_n(&g->redirects, __ATOMIC_RELAXED);
    info->fails = __atomic_load_n(&g->fails, __ATOMIC_RELAXED);
    return SWMOD_FSR_OK;
}

// At fl_install, after the mods' swmod_init: the table is fixed, and our SafeStrings get their memory. false: no
// redirect will run (none named, the setting is off, or no memory below 2^39).
static bool fr_seal(void) {
    g_fr.sealed = true;
    if (!g_st_host.file_redirect || !g_fr.n) return false;
    size_t need = ((size_t)g_fr.n * FR_VARIANTS * ((sizeof(FrStr) + 15) & ~(size_t)15) + 0xffff) & ~(size_t)0xffff;
    for (uint64_t a = FR_LOW_FROM; a + need <= FR_LOW_TO && !g_fr.low; a += FR_LOW_STEP) {
        void *p = VirtualAlloc((void *)(uintptr_t)a, need, 0x3000u /* MEM_RESERVE | MEM_COMMIT */, 0x04u);
        if (p) g_fr.low = p, g_fr.low_cap = need;
    }
    if (!g_fr.low) {
        host_log("file redirect: no memory below 2^39 for its paths: no file is redirected");
        return false;
    }
    for (int i = 0; i < g_fr.ngroups; i++)
        host_log("file redirect: group %s: %d files (off until a mod turns it on)", g_fr.g[i].name, g_fr.g[i].count);
    return true;
}

#endif  // MODHOST_C
