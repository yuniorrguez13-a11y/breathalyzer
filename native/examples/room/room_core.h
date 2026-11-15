// room_core.h: More Room's bookkeeping, with no game and no Windows calls (room.c uses it in the game,
// selftest/room_selftest.c without it). Not thread safe: room.c holds its lock around every call.
//
// - Watched actors: names (the modded actors' packs, `room watch`). Their actor heaps are tracked.
// - Failures: the last ROOM_FAILS failed allocations, with what was asked and what the heap had.
// - Needs: for a watched actor whose heap ran out, how big its heap must be next time (from the failure's own
//   numbers, plus half again; never more than the cap). The next try of that actor gets the extra at creation.
// - The cap: the most one actor heap may grow, from the PC's real RAM (and a plain-words warning for graphics memory).
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifndef ROOM_HAVE_SNPRINTF
#define ROOM_HAVE_SNPRINTF
int snprintf(char *buf, size_t n, const char *fmt, ...);  // (ucrtbase; the zig here has no stdio.h)
#endif

#define ROOM_NAMES 1024      // watched names (a power of two: open addressing)
#define ROOM_NAME_LEN 64
#define ROOM_FAILS 32        // failures kept (the newest)
#define ROOM_HEAPS 64        // watched actor heaps seen
#define ROOM_NEEDS 64        // actors that need more room
#define ROOM_MB 1048576ll

enum { ROOM_BOOST_AUTO, ROOM_BOOST_FIXED, ROOM_BOOST_OFF };

typedef struct {
    uint64_t ram_total, ram_free;            // bytes (GlobalMemoryStatusEx)
    uint64_t vram_dedicated, vram_shared;    // bytes (the biggest graphics adapter)
    char gpu[128];
} RoomMachine;

typedef struct {
    int dual, has2;
    uint64_t heap, request, heap_size, free_b, max_b, over, free2, max2;
    int64_t align;
    char name[ROOM_NAME_LEN], heap2[ROOM_NAME_LEN], parent[ROOM_NAME_LEN];
    int repeats;                             // the same heap and size failed again this often
} RoomFail;

typedef struct {
    char name[ROOM_NAME_LEN];
    uint64_t heap, size, over_max, req_max, allocs, fails;
} RoomHeap;

typedef struct {
    char name[ROOM_NAME_LEN];
    int64_t need;          // the heap size its next creation should have (bytes)
    int64_t given;         // the extra given at its last creation
    int creations, capped; // creations seen; 1: the need is over the cap (said once)
} RoomNeed;

typedef struct {
    char names[ROOM_NAMES][ROOM_NAME_LEN];
    int nwatch;
    RoomFail fails[ROOM_FAILS];
    int nfails;            // all failures seen (the ring keeps the newest ROOM_FAILS)
    RoomHeap heaps[ROOM_HEAPS];
    int nheaps;
    RoomNeed needs[ROOM_NEEDS];
    int nneeds;
    int boost_mode;
    int64_t boost_fixed;   // bytes, for ROOM_BOOST_FIXED
    int64_t cap;           // the most extra one actor heap gets (bytes)
    int64_t vram_budget;   // graphics memory a mod's textures may take before the warning (bytes)
    RoomMachine machine;
} RoomState;

static inline uint32_t room_hash(const char *s) {
    uint32_t h = 2166136261u;
    for (; *s; s++) h = (h ^ (uint8_t)*s) * 16777619u;
    return h;
}

static inline void room_init(RoomState *st) {
    memset(st, 0, sizeof *st);
    st->boost_mode = ROOM_BOOST_AUTO;
}

// Add a watched name; 1: added or already there, 0: no room, or no name.
static inline int room_watch(RoomState *st, const char *name) {
    if (!name || !*name || strlen(name) >= ROOM_NAME_LEN) return 0;
    uint32_t i = room_hash(name) & (ROOM_NAMES - 1);
    for (int k = 0; k < ROOM_NAMES; k++, i = (i + 1) & (ROOM_NAMES - 1)) {
        if (!st->names[i][0]) {
            if (st->nwatch >= ROOM_NAMES * 3 / 4) return 0;
            snprintf(st->names[i], ROOM_NAME_LEN, "%s", name);
            st->nwatch++;
            return 1;
        }
        if (!strcmp(st->names[i], name)) return 1;
    }
    return 0;
}

static inline int room_watched(const RoomState *st, const char *name) {
    if (!name || !*name || strlen(name) >= ROOM_NAME_LEN) return 0;
    uint32_t i = room_hash(name) & (ROOM_NAMES - 1);
    for (int k = 0; k < ROOM_NAMES; k++, i = (i + 1) & (ROOM_NAMES - 1)) {
        if (!st->names[i][0]) return 0;
        if (!strcmp(st->names[i], name)) return 1;
    }
    return 0;
}

static inline RoomNeed *room_need(RoomState *st, const char *name, int make) {
    for (int i = 0; i < st->nneeds; i++)
        if (!strcmp(st->needs[i].name, name)) return &st->needs[i];
    if (!make || st->nneeds >= ROOM_NEEDS) return NULL;
    RoomNeed *n = &st->needs[st->nneeds++];
    memset(n, 0, sizeof *n);
    snprintf(n->name, sizeof n->name, "%s", name);
    return n;
}

// The cap, from the machine: an actor heap may grow by at most a quarter of the RAM that is free now, less 1.5 GB
// kept for Windows and the rest of the game, and never past 1 GB (no actor needs more; a bad number can't eat the
// PC). Floor 16 MB, so the low-end laptop still gets the room a costume needs. Graphics memory: the dedicated
// memory, or (integrated graphics) half the shared memory; a mod's textures may take a quarter of it.
static inline void room_set_cap(RoomState *st) {
    const RoomMachine *m = &st->machine;
    int64_t spare = (int64_t)m->ram_free - 1536 * ROOM_MB;
    int64_t cap = spare > 0 ? spare / 4 : 0;
    if (cap > 1024 * ROOM_MB) cap = 1024 * ROOM_MB;
    if (cap < 16 * ROOM_MB) cap = 16 * ROOM_MB;
    st->cap = cap;
    int64_t vram = m->vram_dedicated >= 512 * (uint64_t)ROOM_MB ? (int64_t)m->vram_dedicated
                                                                 : (int64_t)(m->vram_shared / 2);
    st->vram_budget = vram / 4;
}

static inline void room_mb(char *out, size_t cap, int64_t b) {
    if (b >= 1024 * ROOM_MB) snprintf(out, cap, "%.1f GB", (double)b / (1024.0 * ROOM_MB));
    else snprintf(out, cap, "%.1f MB", (double)b / ROOM_MB);
}

static inline void room_cap_text(const RoomState *st, char *out, size_t cap) {
    char a[32], b[32], c[32], d[32];
    room_mb(a, sizeof a, (int64_t)st->machine.ram_total);
    room_mb(b, sizeof b, (int64_t)st->machine.ram_free);
    room_mb(c, sizeof c, st->cap);
    room_mb(d, sizeof d, (int64_t)(st->machine.vram_dedicated ? st->machine.vram_dedicated : st->machine.vram_shared));
    snprintf(out, cap, "RAM %s (%s free): an actor heap may grow by up to %s; graphics %s%s%s", a, b, c, d,
             st->machine.gpu[0] ? " on " : "", st->machine.gpu);
}

// Will textures of this many bytes fit? 0: yes; else the plain-words warning in out.
static inline int room_textures_warn(const RoomState *st, const char *what, int64_t bytes, char *out, size_t cap) {
    if (st->vram_budget <= 0 || bytes <= st->vram_budget) return 0;
    char a[32], b[32];
    room_mb(a, sizeof a, bytes);
    room_mb(b, sizeof b, st->vram_budget);
    snprintf(out, cap, "%s has %s of textures; this PC's graphics memory gives a mod about %s. It may load slowly, "
             "flicker or not show. A PC with more graphics memory shows it.", what, a, b);
    return 1;
}

// A failed allocation. Returns 1 when it should be logged (a new heap and size, or one of its first 3 repeats).
// A watched actor heap that ran out records what its next creation needs.
static inline int room_add_fail(RoomState *st, const RoomFail *f) {
    for (int i = 0; i < ROOM_FAILS && i < st->nfails; i++) {
        RoomFail *o = &st->fails[i];
        if (o->request == f->request && !strcmp(o->name, f->name)) {
            o->repeats++;
            return o->repeats <= 3;
        }
    }
    st->fails[st->nfails % ROOM_FAILS] = *f;
    st->nfails++;
    if (f->dual && room_watched(st, f->name)) {
        // its own block, its overflow and the request, then half again (the allocations after this one need room too)
        int64_t want = (int64_t)(f->heap_size + f->over + f->request + (uint64_t)(f->align > 0 ? f->align : 0));
        want += want / 2;
        RoomNeed *n = room_need(st, f->name, 1);
        if (n && want > n->need) n->need = want;
    }
    return 1;
}

static inline void room_heap_seen(RoomState *st, const char *name, uint64_t heap, uint64_t size, uint64_t over,
                                  uint64_t request, int ok) {
    RoomHeap *h = NULL;
    for (int i = 0; i < st->nheaps; i++)
        if (st->heaps[i].heap == heap && !strcmp(st->heaps[i].name, name)) h = &st->heaps[i];
    if (!h) {
        h = &st->heaps[st->nheaps < ROOM_HEAPS ? st->nheaps++ : (ROOM_HEAPS - 1)];
        memset(h, 0, sizeof *h);
        snprintf(h->name, sizeof h->name, "%s", name);
        h->heap = heap;
    }
    h->size = size;
    if (over > h->over_max) h->over_max = over;
    if (request > h->req_max) h->req_max = request;
    h->allocs++;
    if (!ok) h->fails++;
}

// At an actor heap's creation: the extra bytes to add to the game's size (base = instSize + its fixed part, extra =
// the class's own extra). 0 for an actor that isn't watched, or needs nothing.
static inline int64_t room_heap_extra(RoomState *st, const char *name, uint64_t base, int32_t extra) {
    if (st->boost_mode == ROOM_BOOST_OFF || !room_watched(st, name)) return 0;
    int64_t have = (int64_t)base + (extra > 0 ? extra : 0), add = 0;
    RoomNeed *n = room_need(st, name, st->boost_mode == ROOM_BOOST_FIXED);
    if (st->boost_mode == ROOM_BOOST_FIXED) add = st->boost_fixed;
    else if (n && n->need > have) add = n->need - have;
    if (add > st->cap) {
        if (n) n->capped = 1;
        add = st->cap;
    }
    if (add < 0) add = 0;
    if (add > 0x40000000) add = 0x40000000;  // (the game keeps the size in 32 bits)
    if (n) n->creations++, n->given = add;
    return add;
}

static inline void room_fail_text(const RoomFail *f, char *out, size_t cap) {
    int n = snprintf(out, cap, "%s heap \"%s\" (%llu bytes) needed %llu bytes, had %llu free (largest block %llu)",
                     f->dual ? "actor" : "game", f->name, (unsigned long long)f->heap_size,
                     (unsigned long long)f->request, (unsigned long long)f->free_b, (unsigned long long)f->max_b);
    if (n < 0 || (size_t)n >= cap) return;
    if (f->dual)
        n += snprintf(out + n, cap - (size_t)n, f->has2 ? "; its overflow \"%s\": %llu free (largest %llu), %llu used"
                                                       : "; no overflow heap%.0s%llu%llu, %llu used",
                      f->heap2, (unsigned long long)f->free2, (unsigned long long)f->max2,
                      (unsigned long long)f->over);
    if (n > 0 && (size_t)n < cap && f->parent[0]) snprintf(out + n, cap - (size_t)n, "; parent \"%s\"", f->parent);
}

// ---- model files named as their files (2026-11-15: the cause of "Linkle / Custom could not be created")
// Every model file of the game holds its own name: Model/Npc_Gerudo_Hero.sbfres is named Npc_Gerudo_Hero inside, its
// textures Npc_Gerudo_Hero.Tex. Two costumes made from Link's file and still named "Link" inside were never created;
// renamed as their files they were [V in the game]. room_model_name reads a .sbfres (Yaz0) far enough for its name.

// Yaz0 into out (at most cap bytes); returns the bytes written, 0 when it isn't Yaz0 or is cut short early.
static inline size_t room_yaz0(const uint8_t *in, size_t n, uint8_t *out, size_t cap) {
    if (n < 16 || memcmp(in, "Yaz0", 4)) return 0;
    size_t want = (size_t)in[4] << 24 | (size_t)in[5] << 16 | (size_t)in[6] << 8 | in[7];
    if (want < cap) cap = want;
    size_t i = 16, o = 0;
    while (o < cap && i < n) {
        uint8_t code = in[i++];
        for (int b = 0; b < 8 && o < cap && i < n; b++, code <<= 1) {
            if (code & 0x80) {
                out[o++] = in[i++];
                continue;
            }
            if (i + 1 >= n) return o;
            uint8_t b1 = in[i], b2 = in[i + 1];
            i += 2;
            size_t dist = ((size_t)(b1 & 0xf) << 8 | b2) + 1, len = b1 >> 4;
            if (!len) {
                if (i >= n) return o;
                len = (size_t)in[i++] + 0x12;
            } else {
                len += 2;
            }
            if (dist > o) return o;
            for (; len && o < cap; len--, o++) out[o] = out[o - dist];
        }
    }
    return o;
}

// The name a Switch BFRES (unpacked) holds: 1 and name filled, 0 when it isn't one or the name is out of range.
static inline int room_fres_name(const uint8_t *d, size_t n, char *name, size_t cap) {
    if (n < 0x30 || memcmp(d, "FRES    ", 8) || !cap) return 0;
    uint64_t off = 0;
    memcpy(&off, d + 0x20, 8);
    if (off + 2 > n) return 0;
    size_t len = (size_t)d[off] | (size_t)d[off + 1] << 8;
    if (off + 2 + len > n || len + 1 > cap) return 0;
    memcpy(name, d + off + 2, len);
    name[len] = 0;
    return 1;
}

// A model file's stem ("SwChar_linkle" for SwChar_linkle.sbfres, "X.Tex" for X.Tex.sbfres) against its name inside:
// 1 when they differ (the game can't create an actor that uses it).
static inline int room_model_misnamed(const char *file, const char *inside) {
    size_t n = strlen(file);
    const char *ext = ".sbfres";
    size_t e = strlen(ext);
    if (n <= e || strcmp(file + n - e, ext)) return 0;
    return strlen(inside) != n - e || memcmp(file, inside, n - e) != 0;
}

static inline void room_report(const RoomState *st, char *out, size_t cap) {
    size_t n = 0;
    char line[600];
#define ROOM_ADD(...)                                                     \
    do {                                                                  \
        int k_ = snprintf(out + n, n < cap ? cap - n : 0, __VA_ARGS__);   \
        if (k_ > 0) n = n + (size_t)k_ < cap ? n + (size_t)k_ : cap - 1;  \
    } while (0)
    if (!cap) return;
    out[0] = 0;
    room_cap_text(st, line, sizeof line);
    ROOM_ADD("%s\n", line);
    ROOM_ADD("boost: %s", st->boost_mode == ROOM_BOOST_OFF ? "off" : st->boost_mode == ROOM_BOOST_AUTO ? "auto" : "fixed");
    if (st->boost_mode == ROOM_BOOST_FIXED) ROOM_ADD(" %lld bytes", (long long)st->boost_fixed);
    ROOM_ADD("\n");
    for (int i = 0; i < st->nneeds; i++) {
        const RoomNeed *d = &st->needs[i];
        ROOM_ADD("need %s: %lld bytes, gave %lld more at its last creation (%d)%s\n", d->name, (long long)d->need,
                 (long long)d->given, d->creations, d->capped ? ", over the cap" : "");
    }
    for (int i = 0; i < st->nheaps; i++) {
        const RoomHeap *h = &st->heaps[i];
        ROOM_ADD("heap %s: %llu bytes, %llu allocs, %llu failed, biggest ask %llu, overflow up to %llu\n", h->name,
                 (unsigned long long)h->size, (unsigned long long)h->allocs, (unsigned long long)h->fails,
                 (unsigned long long)h->req_max, (unsigned long long)h->over_max);
    }
    int first = st->nfails > ROOM_FAILS ? st->nfails - ROOM_FAILS : 0;
    for (int i = first; i < st->nfails; i++) {
        const RoomFail *f = &st->fails[i % ROOM_FAILS];
        room_fail_text(f, line, sizeof line);
        ROOM_ADD("fail %s (x%d)\n", line, f->repeats + 1);
    }
#undef ROOM_ADD
}
