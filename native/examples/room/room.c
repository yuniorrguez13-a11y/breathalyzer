// room: "More Room" (docs/room.md). Gives modded actors (the costumes first) the memory they need, at run time,
// instead of shrinking their models. Mod API 2 (dispatcher overrides only: no hook slots, no game-code rebuild).
//
// What it does, all through hle_override (calls that go through Savage Wind's dispatcher: virtual calls):
// - Watches every heap allocation that fails (sead ExpHeap and the actor heap, a "DualHeap": its own block, then an
//   overflow heap): which heap, how big it is, how much was asked and how much was free. `room` shows them, and the
//   log says it in plain words ("Dm_Npc_Handmaid_A needed 1,234,567 bytes, had 345,678").
// - The actor heap's size: the game sizes it from ActorInfo's instSize plus a per-class extra (a virtual call, vptr
//   +0x330, from 0x700152a0d8). For a watched actor (a modded actor: its pack is in the deployed RomFS, or named with
//   `room watch`) the extra grows to what it really needs: what a failed try of the same actor needed (sizes from the
//   game's own failure, never guessed), or a fixed boost (`room boost`), within the cap.
// - The cap: from the PC's real RAM (and the graphics memory, for the textures warning): `room` says both, and when a
//   model won't fit it says so in plain words instead of failing quietly.
//
// Commands (developer mode): room | room watch <actor> | room boost <MB>|auto|off | room log on|off
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "swmod.h"
#include "botw.h"  // (only BOTW_ADDR_MAIN and BOTW_ADDR_GUEST_END: the guest window)
#include "room_core.h"

// ---- Win32, declared by hand (the zig that ships with Savage Wind has no windows.h; documented interfaces)
typedef struct {
    uint32_t len, load;
    uint64_t total_phys, avail_phys, total_page, avail_page, total_virt, avail_virt, avail_ext;
} RoomMemStatus;  // MEMORYSTATUSEX
typedef struct {
    uint32_t attributes;
    uint32_t times[6];
    uint32_t size_high, size_low, reserved0, reserved1;
    char name[260];
    char short_name[14];
} RoomFindData;  // WIN32_FIND_DATAA
typedef struct {
    uint32_t d1;
    uint16_t d2, d3;
    uint8_t d4[8];
} RoomGuid;
__declspec(dllimport) int __stdcall GlobalMemoryStatusEx(RoomMemStatus *out);
__declspec(dllimport) void *__stdcall LoadLibraryA(const char *name);
__declspec(dllimport) void *__stdcall GetProcAddress(void *h, const char *name);
__declspec(dllimport) int __stdcall FreeLibrary(void *h);
__declspec(dllimport) uint32_t __stdcall GetEnvironmentVariableA(const char *name, char *out, uint32_t size);
__declspec(dllimport) void *__stdcall FindFirstFileA(const char *pattern, RoomFindData *out);
__declspec(dllimport) int __stdcall FindNextFileA(void *h, RoomFindData *out);
__declspec(dllimport) int __stdcall FindClose(void *h);
__declspec(dllimport) int __stdcall WideCharToMultiByte(uint32_t cp, uint32_t flags, const uint16_t *w, int wn, char *out,
                                                        int n, const char *def, int *used);
__declspec(dllimport) void *__stdcall CreateFileA(const char *path, uint32_t access, uint32_t share, void *sec,
                                                 uint32_t disp, uint32_t flags, void *tpl);
__declspec(dllimport) int __stdcall GetFileSizeEx(void *h, int64_t *size);
__declspec(dllimport) int __stdcall ReadFile(void *h, void *buf, uint32_t n, uint32_t *got, void *ov);
__declspec(dllimport) int __stdcall CloseHandle(void *h);
#define ROOM_INVALID_HANDLE ((void *)(intptr_t)-1)

// ---- game addresses (1.9.0, docs/addresses.md)
#define EXP_TRY_ALLOC 0x700000218cull   // sead ExpHeap tryAlloc(heap, size, alignment): its vtable's slot 6
#define DUAL_TRY_ALLOC 0x7001402e38ull  // the actor heap's tryAlloc (a DualHeap): its vtable's slot 6
#define HEAP_SIZE_RET 0x700152a168ull   // return address of the actor heap's extra-size virtual call in 0x700152a0d8
#define VT_FREE_SIZE 0x78               // heap vtable: free bytes (slot 15)
#define VT_MAX_ALLOC 0x80               // heap vtable: largest block (alignment) (slot 16)
#define H_NAME 0x28                     // heap: its name (char *)
#define H_SIZE 0x40                     // heap: its size
#define H_PARENT 0x48                   // heap: its parent heap
#define D_HEAP2 0x110                   // actor heap: its overflow heap (0: none)
#define D_OVER 0x118                    // actor heap: bytes it put in the overflow heap
#define ACTOR_NAME 0x10                 // actor: its name (char *)

// The actor heap's extra-size virtual (vf_102 of the actor classes, out/codemap): the base one, which returns 0. It is
// the one of every class the costumes use (NPC, DemoNPC: in the game 2026-11-15 every costume's size went through it)
// and of about 35 other actor classes; a class with its own (Link's, 40 others) keeps its heap, only logged.
static const uint64_t HS_FN[] = {0x700056c048};
#define NHS ((int)(sizeof HS_FN / sizeof *HS_FN))

static const SwModApi *api;
static SwModGuestFn g_exp_orig, g_dual_orig, g_hs_orig[NHS];
static RoomState g_room;  // the bookkeeping (room_core.h), shared by the game threads and the commands
static int g_lockv;
static void lock(void) {
    while (__atomic_test_and_set(&g_lockv, __ATOMIC_ACQUIRE)) __builtin_ia32_pause();
}
static void unlock(void) { __atomic_clear(&g_lockv, __ATOMIC_RELEASE); }
static int g_log = 1;
static int g_fail_logged;  // (failure lines logged: at most 200 a game)
static int64_t g_exp_fails, g_dual_fails, g_hs_hits, g_hs_ours, g_dual_seen, g_hs_logged;

static int readable(uint64_t p, size_t n) {
    return p >= 0x10000 && p + n > p && p + n < 0x800000000000ull;
}
static uint64_t rd64(uint64_t a) { return readable(a, 8) ? *(volatile uint64_t *)(uintptr_t)a : 0; }
static const char *cstr(uint64_t p) { return readable(p, 1) ? (const char *)(uintptr_t)p : ""; }
static int game_heap(uint64_t h) {
    return h >= BOTW_ADDR_MAIN && h < BOTW_ADDR_GUEST_END && !(h & 7);
}

// ---- guest calls inside a wrapper: every register saved and put back
typedef struct {
    uint64_t x[31], sp, pc, nzcv;
    SwModVReg v[32];
} Saved;
static void save(struct Cpu *c, Saved *s) {
    SwModRegs *r = SWMOD_REGS(c);
    memcpy(s->x, r->x, sizeof s->x);
    s->sp = r->sp, s->pc = r->pc, s->nzcv = r->nzcv;
    memcpy(s->v, r->v, sizeof s->v);
}
static void restore(struct Cpu *c, const Saved *s) {
    SwModRegs *r = SWMOD_REGS(c);
    memcpy(r->x, s->x, sizeof s->x);
    r->sp = s->sp, r->pc = s->pc, r->nzcv = s->nzcv;
    memcpy(r->v, s->v, sizeof s->v);
}
static uint64_t vcall(struct Cpu *c, uint64_t obj, uint64_t slot_off, uint64_t a1) {
    uint64_t vp = rd64(obj), fn = vp ? rd64(vp + slot_off) : 0;
    if (!fn || fn < BOTW_ADDR_MAIN || fn >= BOTW_ADDR_MAIN + (1ull << 32)) return 0;  // (code is in main)
    SwModRegs *r = SWMOD_REGS(c);
    r->x[0] = obj, r->x[1] = a1;
    api->guest_call(c, fn);
    return r->x[0];
}

// What a heap has now (free bytes, largest block): guest calls, only on failures and watched heaps.
static void heap_room(struct Cpu *c, uint64_t heap, uint64_t *free_b, uint64_t *max_b) {
    Saved s;
    save(c, &s);
    *free_b = vcall(c, heap, VT_FREE_SIZE, 0);
    *max_b = vcall(c, heap, VT_MAX_ALLOC, 8);
    restore(c, &s);
}

static void note_fail(struct Cpu *c, int dual, uint64_t heap, uint64_t size, int64_t align) {
    RoomFail f;
    memset(&f, 0, sizeof f);
    f.dual = dual;
    f.heap = heap;
    f.request = size;
    f.align = align;
    snprintf(f.name, sizeof f.name, "%s", cstr(rd64(heap + H_NAME)));
    f.heap_size = rd64(heap + H_SIZE);
    heap_room(c, heap, &f.free_b, &f.max_b);
    if (dual) {
        uint64_t h2 = rd64(heap + D_HEAP2);
        f.over = rd64(heap + D_OVER);
        if (game_heap(h2)) {
            snprintf(f.heap2, sizeof f.heap2, "%s", cstr(rd64(h2 + H_NAME)));
            heap_room(c, h2, &f.free2, &f.max2);
            f.has2 = 1;
        }
    }
    uint64_t parent = rd64(heap + H_PARENT);
    if (game_heap(parent)) snprintf(f.parent, sizeof f.parent, "%s", cstr(rd64(parent + H_NAME)));
    lock();
    int logit = room_add_fail(&g_room, &f) && g_log && g_fail_logged < 200;
    if (logit) g_fail_logged++;
    unlock();
    if (logit) {
        char line[512];
        room_fail_text(&f, line, sizeof line);
        api->log("room: %s", line);
    }
}

// ---- the wrappers
static void exp_try_alloc(struct Cpu *c) {
    SwModRegs *r = SWMOD_REGS(c);
    uint64_t heap = r->x[0], size = r->x[1];
    int64_t align = (int32_t)r->x[2];
    g_exp_orig(c);
    if (r->x[0]) return;
    __atomic_fetch_add(&g_exp_fails, 1, __ATOMIC_RELAXED);
    Saved s;
    save(c, &s);
    note_fail(c, 0, heap, size, align);
    restore(c, &s);
}

static void dual_try_alloc(struct Cpu *c) {
    SwModRegs *r = SWMOD_REGS(c);
    uint64_t heap = r->x[0], size = r->x[1];
    int64_t align = (int32_t)r->x[2];
    const char *name = cstr(rd64(heap + H_NAME));
    int w;
    lock();
    w = room_watched(&g_room, name);
    unlock();
    g_dual_orig(c);
    uint64_t got = r->x[0];
    if (w) {
        __atomic_fetch_add(&g_dual_seen, 1, __ATOMIC_RELAXED);
        lock();
        room_heap_seen(&g_room, name, heap, rd64(heap + H_SIZE), rd64(heap + D_OVER), size, got != 0);
        unlock();
    }
    if (got) return;
    __atomic_fetch_add(&g_dual_fails, 1, __ATOMIC_RELAXED);
    Saved s;
    save(c, &s);
    note_fail(c, 1, heap, size, align);
    restore(c, &s);
}

static void hs_common(struct Cpu *c, int i) {
    SwModRegs *r = SWMOD_REGS(c);
    uint64_t ret = r->x[30], actor = r->x[0], base = (uint32_t)r->x[23];
    g_hs_orig[i](c);
    if (ret != HEAP_SIZE_RET) return;
    __atomic_fetch_add(&g_hs_hits, 1, __ATOMIC_RELAXED);
    const char *name = cstr(rd64(actor + ACTOR_NAME));
    int32_t extra = (int32_t)(uint32_t)r->x[0];
    int64_t add;
    int w;
    lock();
    w = room_watched(&g_room, name);
    add = room_heap_extra(&g_room, name, base, extra);
    unlock();
    if (w && g_log && __atomic_fetch_add(&g_hs_logged, 1, __ATOMIC_RELAXED) < 64)
        api->log("room: actor heap of %s: game's size %llu (instSize part %llu, class extra %d, size function %d)", name,
                 (unsigned long long)(base + (extra > 0 ? extra : 0)), (unsigned long long)base, extra, i);
    if (add > 0) {
        __atomic_fetch_add(&g_hs_ours, 1, __ATOMIC_RELAXED);
        r->x[0] = (uint32_t)(extra + (int32_t)add);
        if (g_log) api->log("room: %s gets %lld more bytes of actor heap (game's size %llu)", name, (long long)add,
                            (unsigned long long)(base + (extra > 0 ? extra : 0)));
    }
}
static void hs_0(struct Cpu *c) { hs_common(c, 0); }
static void (*const HS_WRAP[NHS])(struct Cpu *) = {hs_0};

// ---- the machine: RAM and graphics memory
static void read_machine(RoomMachine *m) {
    memset(m, 0, sizeof *m);
    RoomMemStatus ms;
    memset(&ms, 0, sizeof ms);
    ms.len = sizeof ms;
    if (GlobalMemoryStatusEx(&ms)) m->ram_total = ms.total_phys, m->ram_free = ms.avail_phys;
    // graphics memory: the adapter with the most dedicated memory (DXGI: no window, no device)
    void *dx = LoadLibraryA("dxgi.dll");
    if (!dx) return;
    typedef long(__stdcall * CreateFn)(const RoomGuid *, void **);
    CreateFn create = (CreateFn)GetProcAddress(dx, "CreateDXGIFactory1");
    static const RoomGuid IID_F1 = {0x770aae78, 0xf26f, 0x4dba, {0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87}};
    void *f = NULL;
    if (create && create(&IID_F1, &f) >= 0 && f) {
        // IDXGIFactory1: slot 12 EnumAdapters1(UINT, IDXGIAdapter1 **), slot 2 Release; IDXGIAdapter1: slot 10 GetDesc1
        void **vt = *(void ***)f;
        typedef long(__stdcall * EnumFn)(void *, uint32_t, void **);
        typedef uint32_t(__stdcall * RelFn)(void *);
        typedef long(__stdcall * DescFn)(void *, void *);
        for (uint32_t i = 0; i < 8; i++) {
            void *ad = NULL;
            if (((EnumFn)vt[12])(f, i, &ad) < 0 || !ad) break;
            struct {
                uint16_t desc[128];
                uint32_t vendor, device, subsys, rev;
                size_t dedicated_video, dedicated_system, shared_system;
                uint32_t luid_lo;
                int32_t luid_hi;
                uint32_t flags;
            } d;
            void **avt = *(void ***)ad;
            memset(&d, 0, sizeof d);
            if (((DescFn)avt[10])(ad, &d) >= 0 && !(d.flags & 2)) {  // (2: the software adapter)
                if ((uint64_t)d.dedicated_video > m->vram_dedicated ||
                    (!m->vram_dedicated && (uint64_t)d.shared_system > m->vram_shared)) {
                    m->vram_dedicated = d.dedicated_video, m->vram_shared = d.shared_system;
                    WideCharToMultiByte(65001, 0, d.desc, -1, m->gpu, (int)sizeof m->gpu - 1, NULL, NULL);
                }
            }
            ((RelFn)avt[2])(ad);
        }
        ((RelFn)vt[2])(f);
    }
    FreeLibrary(dx);
}

// ---- watched names: every actor pack in the deployed RomFS (the modded actors), plus `room watch`
static void watch_overlay(void) {
    char dir[520], pat[600];
    uint32_t n = GetEnvironmentVariableA("SAVAGEWIND_ROMFS_OVERLAY", dir, sizeof dir);
    if (!n || n >= sizeof dir) return;
    snprintf(pat, sizeof pat, "%s\\Actor\\Pack\\*.sbactorpack", dir);
    RoomFindData fd;
    void *h = FindFirstFileA(pat, &fd);
    if (h == ROOM_INVALID_HANDLE) return;
    int added = 0;
    do {
        char name[96];
        snprintf(name, sizeof name, "%s", fd.name);
        char *dot = strstr(name, ".sbactorpack");
        if (dot) *dot = 0;
        if (room_watch(&g_room, name)) added++;
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    api->log("room: watching %d modded actors (their packs are in the deployed RomFS)", added);
}

// ---- model files named as their files (room_core.h): every .sbfres in the deployed RomFS's Model folder
static int g_models_checked, g_models_misnamed;
static char g_misnamed[600];

static uint8_t *read_file(const char *path, size_t *n, size_t limit) {
    void *h = CreateFileA(path, 0x80000000u /* GENERIC_READ */, 1 /* share read */, NULL, 3 /* OPEN_EXISTING */, 0, NULL);
    if (h == ROOM_INVALID_HANDLE) return NULL;
    int64_t size = 0;
    uint8_t *buf = NULL;
    uint32_t got = 0;
    if (GetFileSizeEx(h, &size) && size > 0 && (size_t)size <= limit && (buf = (uint8_t *)malloc((size_t)size)) &&
        ReadFile(h, buf, (uint32_t)size, &got, NULL) && got == (uint32_t)size) {
        *n = (size_t)size;
    } else {
        free(buf);
        buf = NULL;
    }
    CloseHandle(h);
    return buf;
}

static void check_model_names(void) {
    char dir[520], pat[600], path[900];
    uint32_t n = GetEnvironmentVariableA("SAVAGEWIND_ROMFS_OVERLAY", dir, sizeof dir);
    if (!n || n >= sizeof dir) return;
    snprintf(pat, sizeof pat, "%s\\Model\\*.sbfres", dir);
    RoomFindData fd;
    void *h = FindFirstFileA(pat, &fd);
    if (h == ROOM_INVALID_HANDLE) return;
    size_t tl = 0;
    do {
        if (g_models_checked >= 512) break;  // (a big mod: the first 512 files)
        snprintf(path, sizeof path, "%s\\Model\\%s", dir, fd.name);
        size_t len = 0;
        uint8_t *raw = read_file(path, &len, 64u << 20);
        if (!raw) continue;
        size_t want = len >= 8 ? ((size_t)raw[4] << 24 | (size_t)raw[5] << 16 | (size_t)raw[6] << 8 | raw[7]) : 0;
        uint8_t *un = want && want <= (256u << 20) ? (uint8_t *)malloc(want) : NULL;
        char inside[128];
        if (un) {
            size_t got = room_yaz0(raw, len, un, want);
            g_models_checked++;
            if (room_fres_name(un, got, inside, sizeof inside) && room_model_misnamed(fd.name, inside)) {
                g_models_misnamed++;
                api->log("room: Model/%s is named \"%s\" inside: the game can't create an actor that uses it (a model "
                         "file must be named as its file). Rebuild the mod that made it (charsel build).", fd.name, inside);
                if (tl < sizeof g_misnamed - 1) {
                    int k = snprintf(g_misnamed + tl, sizeof g_misnamed - tl, "%s%s (inside: %s)", tl ? "; " : "",
                                     fd.name, inside);
                    if (k > 0) tl = tl + (size_t)k < sizeof g_misnamed ? tl + (size_t)k : sizeof g_misnamed - 1;
                }
            }
            free(un);
        }
        free(raw);
    } while (FindNextFileA(h, &fd));
    FindClose(h);
}

// ---- commands
static void cmd_room(int argc, char **argv, SwModReply *r) {
    if (argc >= 3 && !strcmp(argv[1], "watch")) {
        lock();
        int ok = room_watch(&g_room, argv[2]);
        unlock();
        if (!ok) {
            api->reply_fail(r, "can't watch %s (no room for more names)", argv[2]);
            return;
        }
    } else if (argc >= 3 && !strcmp(argv[1], "boost")) {
        lock();
        if (!strcmp(argv[2], "auto")) g_room.boost_mode = ROOM_BOOST_AUTO;
        else if (!strcmp(argv[2], "off")) g_room.boost_mode = ROOM_BOOST_OFF;
        else {
            double mb = atof(argv[2]);
            g_room.boost_mode = ROOM_BOOST_FIXED, g_room.boost_fixed = (int64_t)(mb * 1048576.0);
        }
        unlock();
    } else if (argc >= 3 && !strcmp(argv[1], "log")) {
        g_log = strcmp(argv[2], "off") != 0;
    } else if (argc > 1) {
        api->reply_fail(r, "usage: room [watch <actor> | boost <MB>|auto|off | log on|off]");
        return;
    }
    char *buf = (char *)malloc(16384);
    if (!buf) return;
    lock();
    room_report(&g_room, buf, 16384);
    int nfails = g_room.nfails, nheaps = g_room.nheaps, nwatch = g_room.nwatch;
    unlock();
    api->reply_int(r, "fails", nfails);
    api->reply_int(r, "exp_fails", g_exp_fails);
    api->reply_int(r, "dual_fails", g_dual_fails);
    api->reply_int(r, "heaps", nheaps);
    api->reply_int(r, "watched", nwatch);
    api->reply_int(r, "size_calls", g_hs_hits);
    api->reply_int(r, "size_raised", g_hs_ours);
    api->reply_int(r, "watched_allocs", g_dual_seen);
    api->reply_int(r, "models_checked", g_models_checked);
    api->reply_int(r, "models_misnamed", g_models_misnamed);
    api->reply_str(r, "misnamed", g_misnamed);
    api->reply_str(r, "report", buf);
    free(buf);
}

static const SwModCommand commands[] = {
    {"room", "[watch <actor> | boost <MB>|auto|off | log on|off]",
     "More Room: heap failures, actor heaps it raised, the RAM cap", cmd_room, 0},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 2)) {
        if (SWMOD_API_OK(a, 1)) a->log("room: needs modhost with mod API 2");
        return 1;
    }
    api = a;
    if (strcmp(api->game_version, "1.9.0") != 0) {
        api->log("room: made for game version 1.9.0, this is %s", api->game_version);
        return 2;
    }
    room_init(&g_room);
    read_machine(&g_room.machine);
    room_set_cap(&g_room);
    watch_overlay();
    check_model_names();
    g_exp_orig = api->dispatch_lookup(EXP_TRY_ALLOC);
    g_dual_orig = api->dispatch_lookup(DUAL_TRY_ALLOC);
    if (!g_exp_orig || !g_dual_orig) {
        api->log("room: the heap functions aren't in this game code");
        return 3;
    }
    api->hle_override(EXP_TRY_ALLOC, exp_try_alloc, "room_exp_try_alloc");
    api->hle_override(DUAL_TRY_ALLOC, dual_try_alloc, "room_dual_try_alloc");
    int n = 0;
    for (int i = 0; i < NHS; i++) {
        g_hs_orig[i] = api->dispatch_lookup(HS_FN[i]);
        if (g_hs_orig[i]) api->hle_override(HS_FN[i], HS_WRAP[i], "room_actor_heap_size"), n++;
    }
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    char cap[256];
    room_cap_text(&g_room, cap, sizeof cap);
    api->log("room: ready (%d actor heap size function%s); %s", n, n == 1 ? "" : "s", cap);
    return 0;
}
