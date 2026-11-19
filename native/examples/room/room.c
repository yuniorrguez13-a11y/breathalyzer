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
// - Textures past the game's texture heap (TextureArchiveWork, 192 MB fixed at boot): an allocation it can't give
//   comes from an extra game heap in memory More Room adds (the cap's size, made at the first need), and the game
//   frees it as usual (ExpHeap's free and resizes are wrapped; the extra heap is one of the game's root heaps).
//
// Commands (developer mode): room | room watch <actor> | room boost <MB>|auto|off | room rescue on|off|size <MB>|<heap> |
// room log on|off
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
typedef struct {
    uint64_t base, alloc_base;
    uint32_t alloc_protect;
    uint16_t partition;
    uint64_t region_size;
    uint32_t state, protect, type;
} RoomMemInfo;  // MEMORY_BASIC_INFORMATION (64-bit)
__declspec(dllimport) size_t __stdcall VirtualQuery(const void *p, RoomMemInfo *out, size_t n);
__declspec(dllimport) void *__stdcall VirtualAlloc(void *p, size_t n, uint32_t type, uint32_t protect);
#define ROOM_MEM_COMMIT 0x1000u
#define ROOM_MEM_RESERVE 0x2000u
#define ROOM_MEM_FREE 0x10000u
#define ROOM_PAGE_READWRITE 4u
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

// The texture rescue (docs/room.md): TextureArchiveWork's 192 MB are fixed at boot by direct calls (no dispatcher on
// the way, so nothing to wrap), so its failed allocations are given from an extra sead ExpHeap instead, which the
// game's own heap code builds over memory More Room adds to the guest. The game frees those blocks as usual: through
// the heap (ExpHeap's free and resizes, wrapped: a block of the extra heap goes to it) or through `delete` (the heap
// lookup finds the extra heap: it is added to the game's root heaps).
#define EXP_FREE 0x7000ea0a84ull          // ExpHeap free(heap, ptr): vtable slot 7
#define EXP_RESIZE_FRONT 0x7000ea0e00ull  // ExpHeap slot 8 (heap, ptr, size): checks the block is its own first
#define EXP_RESIZE_BACK 0x7000ea0f78ull   // ExpHeap slot 9 (heap, ptr, size): the same
#define EXP_REALLOC 0x7000ea139cull       // ExpHeap slot 10 (heap, ptr, size, alignment): the same
#define EXP_CTOR 0x7000e9ef38ull          // ExpHeap(this, SafeString *name, parent, start, size, direction, lock)
#define EXP_INIT_FREE 0x7000e9ff94ull     // ExpHeap: its free list over its whole memory (after the constructor)
#define ROOT_HEAPS 0x7001e33f88ull        // sead HeapMgr's root heaps: s32 count, s32 room, Heap **list, the slots
#define HEAP_TREE_CS 0x7001e34020ull      // the lock the heap lookup (findContainHeap) holds while it reads them
#define CS_LOCK 0x70015a0520ull           // lock / unlock a sead CriticalSection (x0)
#define CS_UNLOCK 0x70015a0530ull
#define SAFESTRING_VT 0x7001d66260ull     // sead::SafeString's vtable (a pointer to it; +0x10 is the vptr)
// The extra memory: host memory right above Savage Wind's guest window (BOTW_ADDR_GUEST_END), the first free run
// from here on, below 2^39 (the Switch's address width). Not inside the window: the guest maps its own memory into the
// window's gaps (in game 2026-11-18 the time service's shared memory landed at 70baf22000), and Savage Wind's kernel
// wouldn't know ours was there.
#define RESCUE_FROM 0x7400000000ull
#define RESCUE_TO (1ull << 39)
static const char RESCUE_NAME[] = "MoreRoomExtra";

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

static void fill_fail(struct Cpu *c, int dual, uint64_t heap, uint64_t size, int64_t align, RoomFail *f) {
    memset(f, 0, sizeof *f);
    f->dual = dual;
    f->heap = heap;
    f->request = size;
    f->align = align;
    snprintf(f->name, sizeof f->name, "%s", cstr(rd64(heap + H_NAME)));
    f->heap_size = rd64(heap + H_SIZE);
    heap_room(c, heap, &f->free_b, &f->max_b);
    if (dual) {
        uint64_t h2 = rd64(heap + D_HEAP2);
        f->over = rd64(heap + D_OVER);
        if (game_heap(h2)) {
            snprintf(f->heap2, sizeof f->heap2, "%s", cstr(rd64(h2 + H_NAME)));
            heap_room(c, h2, &f->free2, &f->max2);
            f->has2 = 1;
        }
    }
    uint64_t parent = rd64(heap + H_PARENT);
    if (game_heap(parent)) snprintf(f->parent, sizeof f->parent, "%s", cstr(rd64(parent + H_NAME)));
}

static void record_fail(const RoomFail *f) {
    lock();
    int logit = room_add_fail(&g_room, f) && g_log && g_fail_logged < 200;
    if (logit) g_fail_logged++;
    unlock();
    if (logit) {
        char line[512];
        room_fail_text(f, line, sizeof line);
        api->log("room: %s", line);
    }
}

static void note_fail(struct Cpu *c, int dual, uint64_t heap, uint64_t size, int64_t align) {
    RoomFail f;
    fill_fail(c, dual, heap, size, align, &f);
    record_fail(&f);
}

// ---- the texture rescue: the extra memory and its heap (made at the first allocation it must give)
static uint64_t g_rescue_lo, g_rescue_hi, g_rescue_heap;  // its heap's memory (lo 0: not made); set once
static int g_rescue_making;
static int64_t g_rescue_redirects;
static SwModGuestFn g_ptr_orig[4];
static const uint64_t PTR_FN[4] = {EXP_FREE, EXP_RESIZE_FRONT, EXP_RESIZE_BACK, EXP_REALLOC};

// Reserve and commit size bytes in the first free run of address space from RESCUE_FROM to RESCUE_TO (64 MB steps:
// only addresses no one has, Windows says). Its address, or 0 (and why).
static uint64_t commit_extra(uint64_t size, char *why, size_t cap) {
    const uint64_t step = 64ull << 20;
    uint64_t a = RESCUE_FROM;
    int tried = 0;
    while (a + size <= RESCUE_TO) {
        RoomMemInfo mi;
        memset(&mi, 0, sizeof mi);
        if (!VirtualQuery((const void *)(uintptr_t)a, &mi, sizeof mi) || !mi.region_size) break;
        uint64_t end = mi.base + mi.region_size;
        if (mi.state == ROOM_MEM_FREE && end >= a + size) {
            tried++;
            void *p = VirtualAlloc((void *)(uintptr_t)a, (size_t)size, ROOM_MEM_RESERVE | ROOM_MEM_COMMIT,
                                   ROOM_PAGE_READWRITE);
            if (p == (void *)(uintptr_t)a) return a;
            if (p) break;  // (never: Windows gives the address asked or none)
        }
        uint64_t next = (end + step - 1) & ~(step - 1);
        a = next > a ? next : a + step;
    }
    char mb[32];
    room_mb(mb, sizeof mb, (int64_t)size);
    snprintf(why, cap, tried ? "Windows gave no memory (%s asked: the PC's RAM and page file are full)"
                             : "no free %s of address space above the guest's memory", mb);
    return 0;
}

// Add the heap to the game's root heaps, under the heap tree's lock: then the heap lookup (`delete`) finds it.
static int root_add(struct Cpu *c, uint64_t heap) {
    SwModRegs *r = SWMOD_REGS(c);
    r->x[0] = HEAP_TREE_CS;
    api->guest_call(c, CS_LOCK);
    volatile int32_t *count = (volatile int32_t *)(uintptr_t)ROOT_HEAPS;
    uint64_t list = rd64(ROOT_HEAPS + 8);
    int ok = room_root_slot_ok(count[0], count[1], list, ROOT_HEAPS + 0x10);
    if (ok) {
        ((volatile uint64_t *)(uintptr_t)list)[count[0]] = heap;
        __atomic_store_n(&count[0], count[0] + 1, __ATOMIC_RELEASE);
    }
    r->x[0] = HEAP_TREE_CS;
    api->guest_call(c, CS_UNLOCK);
    return ok;
}

// Make the extra memory and its heap (registers: the caller saves them). 1 made, 0 not for this request (too big),
// -1 never (why says it).
static int rescue_make(struct Cpu *c, uint64_t need, char *why, size_t cap) {
    lock();
    int64_t size = room_rescue_size(&g_room, need);
    unlock();
    if (!size) {
        snprintf(why, cap, "one allocation of %llu bytes is more than the extra memory can be", (unsigned long long)need);
        return 0;
    }
    uint64_t vt = rd64(SAFESTRING_VT);
    if (vt < BOTW_ADDR_MAIN || vt >= BOTW_ADDR_MAIN + (1ull << 32)) {
        snprintf(why, cap, "the game's string class isn't where 1.9.0 has it");
        return -1;
    }
    uint64_t base = commit_extra((uint64_t)size, why, cap), heap = base + ROOM_RESCUE_HEAD;
    if (!base) return -1;
    // its name (a sead SafeString, kept: the heap points at its text) in the first page, the heap after it
    *(volatile uint64_t *)(uintptr_t)base = vt + 0x10;
    *(volatile uint64_t *)(uintptr_t)(base + 8) = base + 0x10;
    memcpy((void *)(uintptr_t)(base + 0x10), RESCUE_NAME, sizeof RESCUE_NAME);
    SwModRegs *r = SWMOD_REGS(c);
    r->x[0] = heap, r->x[1] = base, r->x[2] = 0, r->x[3] = heap, r->x[4] = (uint64_t)(size - ROOM_RESCUE_HEAD);
    r->x[5] = 1, r->x[6] = 1;  // forward, with its own lock (game threads share it)
    api->guest_call(c, EXP_CTOR);
    r->x[0] = heap;
    api->guest_call(c, EXP_INIT_FREE);
    uint64_t free_b = vcall(c, heap, VT_FREE_SIZE, 0);
    if (rd64(heap + H_SIZE) != (uint64_t)(size - ROOM_RESCUE_HEAD) || !free_b || free_b > (uint64_t)size) {
        snprintf(why, cap, "the game's heap code didn't make the heap (size %llu, free %llu)",
                 (unsigned long long)rd64(heap + H_SIZE), (unsigned long long)free_b);
        return -1;
    }
    int root = root_add(c, heap);
    lock();
    g_room.rescue.base = base, g_room.rescue.size = (uint64_t)size, g_room.rescue.heap = heap;
    g_room.rescue.root = root, g_room.rescue.state = 1, g_room.rescue.free_now = free_b;
    unlock();
    g_rescue_heap = heap, g_rescue_hi = base + (uint64_t)size;
    __atomic_store_n(&g_rescue_lo, heap, __ATOMIC_RELEASE);
    char mb[32];
    room_mb(mb, sizeof mb, size);
    api->log("room: made the extra memory for full heaps: %s, a game heap \"%s\" at %llx%s", mb, RESCUE_NAME,
             (unsigned long long)heap, root ? "" : " (not among the root heaps: blocks freed with delete stay used)");
    return 1;
}

// One allocation from the extra heap (registers: the caller saves them). 0: none.
static uint64_t rescue_alloc(struct Cpu *c, uint64_t size, int64_t align) {
    if (!__atomic_load_n(&g_rescue_lo, __ATOMIC_ACQUIRE)) {
        int state;
        lock();
        state = g_room.rescue.state;
        unlock();
        if (state == 2 || __atomic_exchange_n(&g_rescue_making, 1, __ATOMIC_ACQUIRE)) return 0;  // (or making now)
        char why[200] = "";
        int ok = rescue_make(c, size, why, sizeof why);
        if (ok < 0) {
            lock();
            g_room.rescue.state = 2;
            snprintf(g_room.rescue.why, sizeof g_room.rescue.why, "%s", why);
            unlock();
            api->log("room: the extra memory for full heaps couldn't be made: %s", why);
        } else if (!ok && g_log) {
            static int said;  // (a texture system may ask again every frame: the first 10)
            if (__atomic_fetch_add(&said, 1, __ATOMIC_RELAXED) < 10) api->log("room: %s", why);
        }
        __atomic_store_n(&g_rescue_making, 0, __ATOMIC_RELEASE);
        if (ok <= 0) return 0;
    }
    SwModRegs *r = SWMOD_REGS(c);
    r->x[0] = g_rescue_heap, r->x[1] = size, r->x[2] = (uint64_t)align;
    g_exp_orig(c);  // (the game's own tryAlloc, on the extra heap)
    return r->x[0];
}

// ExpHeap free and resizes (dispatcher calls): a block of the extra heap goes to the extra heap, whichever heap the
// game asks. Without this the game's code would only refuse it (it checks the block is its own first).
static void ptr_common(struct Cpu *c, int i) {
    SwModRegs *r = SWMOD_REGS(c);
    uint64_t lo = __atomic_load_n(&g_rescue_lo, __ATOMIC_ACQUIRE), p = r->x[1];
    int ours = lo && p >= lo && p < g_rescue_hi;
    if (ours && r->x[0] != g_rescue_heap) {
        r->x[0] = g_rescue_heap;
        __atomic_fetch_add(&g_rescue_redirects, 1, __ATOMIC_RELAXED);
    }
    g_ptr_orig[i](c);
    if (ours) {  // (its free bytes now, for `room`)
        Saved s;
        save(c, &s);
        uint64_t free_b = vcall(c, g_rescue_heap, VT_FREE_SIZE, 0);
        restore(c, &s);
        lock();
        g_room.rescue.free_now = free_b;
        unlock();
    }
}
static void ptr_0(struct Cpu *c) { ptr_common(c, 0); }
static void ptr_1(struct Cpu *c) { ptr_common(c, 1); }
static void ptr_2(struct Cpu *c) { ptr_common(c, 2); }
static void ptr_3(struct Cpu *c) { ptr_common(c, 3); }
static void (*const PTR_WRAP[4])(struct Cpu *) = {ptr_0, ptr_1, ptr_2, ptr_3};

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
    RoomFail f;
    fill_fail(c, 0, heap, size, align, &f);
    int want;
    lock();
    want = g_ptr_orig[0] && heap != g_rescue_heap && room_rescue_wanted(&g_room, f.name);
    unlock();
    uint64_t got = want ? rescue_alloc(c, size, align) : 0, free_b = 0;
    if (got) free_b = vcall(c, g_rescue_heap, VT_FREE_SIZE, 0);
    restore(c, &s);
    if (!got) {
        if (want) {
            lock();
            g_room.rescue.refused++;
            unlock();
        }
        record_fail(&f);
        return;
    }
    r->x[0] = got;
    char line[512], warn[400];
    lock();
    int logit = room_rescue_given(&g_room, size, free_b) && g_log;
    int w = room_rescue_vram_warn(&g_room, warn, sizeof warn);
    if (logit) room_rescue_line(&g_room, &f, line, sizeof line);
    unlock();
    if (logit) api->log("room: %s", line);
    if (w) api->log("room: %s", warn);
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
    } else if (argc >= 3 && !strcmp(argv[1], "rescue")) {
        lock();
        int ok = 1;
        if (!strcmp(argv[2], "on")) g_room.rescue.on = 1;
        else if (!strcmp(argv[2], "off")) g_room.rescue.on = 0;
        else if (!strcmp(argv[2], "size") && argc >= 4) g_room.rescue.want_size = (int64_t)(atof(argv[3]) * 1048576.0);
        else ok = room_rescue_add(&g_room, argv[2]);
        unlock();
        if (!ok) {
            api->reply_fail(r, "can't rescue %s too (at most %d heaps)", argv[2], ROOM_RESCUE_HEAPS);
            return;
        }
    } else if (argc > 1) {
        api->reply_fail(r, "usage: room [watch <actor> | boost <MB>|auto|off | rescue on|off|size <MB>|<heap> | log on|off]");
        return;
    }
    char *buf = (char *)malloc(16384);
    if (!buf) return;
    lock();
    g_room.rescue.redirected = __atomic_load_n(&g_rescue_redirects, __ATOMIC_RELAXED);
    room_report(&g_room, buf, 16384);
    int nfails = g_room.nfails, nheaps = g_room.nheaps, nwatch = g_room.nwatch;
    RoomRescue rs = g_room.rescue;
    int64_t rs_used = room_rescue_used(&g_room);
    unlock();
    api->reply_bool(r, "rescue_on", rs.on && g_ptr_orig[0]);
    api->reply_int(r, "rescue_state", rs.state);
    api->reply_int(r, "rescue_size", (int64_t)rs.size);
    api->reply_int(r, "rescue_used", rs_used);
    api->reply_int(r, "rescue_given", rs.given);
    api->reply_int(r, "rescue_bytes", rs.given_bytes);
    api->reply_int(r, "rescue_refused", rs.refused);
    api->reply_int(r, "rescue_redirects", rs.redirected);
    api->reply_bool(r, "rescue_root", rs.root);
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
    {"room", "[watch <actor> | boost <MB>|auto|off | rescue on|off|size <MB>|<heap> | log on|off]",
     "More Room: heap failures, actor heaps it raised, the extra texture memory, the RAM cap", cmd_room, 0},
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
    // the texture rescue: only with all four of ExpHeap's block functions wrapped (else a rescued block couldn't be
    // freed through the heap the game thinks it came from)
    SwModGuestFn ptr_orig[4];
    int nptr = 0;
    for (int i = 0; i < 4; i++) nptr += (ptr_orig[i] = api->dispatch_lookup(PTR_FN[i])) != NULL;
    if (nptr == 4) {
        static const char *const names[4] = {"room_exp_free", "room_exp_resize_front", "room_exp_resize_back",
                                             "room_exp_realloc"};
        for (int i = 0; i < 4; i++) g_ptr_orig[i] = ptr_orig[i], api->hle_override(PTR_FN[i], PTR_WRAP[i], names[i]);
    } else {
        api->log("room: ExpHeap's free isn't in this game code: no texture rescue");
    }
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    char cap[256], rescue[400];
    room_cap_text(&g_room, cap, sizeof cap);
    room_rescue_text(&g_room, rescue, sizeof rescue);
    api->log("room: ready (%d actor heap size function%s); %s; %s", n, n == 1 ? "" : "s", cap,
             nptr == 4 ? rescue : "no texture rescue");
    return 0;
}
