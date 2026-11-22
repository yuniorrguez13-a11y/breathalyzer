// Self-test only: a stand-in for the game code library (swgame.dll) with made-up tables: six "functions", one site.
// Nothing here comes from the game. -DSELFTEST_OTHER_ID builds one "for another Savage Wind version" (another id).
#include <stdint.h>
#include <string.h>

#include "../sw_runtime.h"

#ifdef SELFTEST_OTHER_ID
#define SELFTEST_ID "other-id"
#else
#define SELFTEST_ID "selftest-id"
#endif

struct Cpu {
    uint64_t x[32];
};

static void fake_fn_a(struct Cpu *c) { c->x[0] = 41; }
static void fake_fn_b(struct Cpu *c) { c->x[0] = 42; }

// The four file opens modhost's file log wraps (fslog.c: at BotW 1.9.0's addresses, but made up). x2 is a
// sead::SafeString* (its char* at +8). x0 stays (the device: opened), or becomes 0 for no path, one that can't be read
// (the self-test's pages that aren't committed, have no access or are a guard page: fake_runtime.c) or one that starts
// with "missing/". x1 says which of the four ran; x2 and x3 change, as in a real call (the wrapper must read them
// first). x4-x11 get the first 63 bytes of the path it opened (the file redirect's self-test: which file it was).
typedef struct {
    uint64_t vptr;
    const char *str;
} FakeSafeString;
typedef struct {
    uint64_t base, alloc_base;
    uint32_t alloc_protect;
    uint16_t partition;
    uint64_t region_size;
    uint32_t state, protect, type;
} FakeMemInfo;  // MEMORY_BASIC_INFORMATION (64-bit)
__declspec(dllimport) size_t __stdcall VirtualQuery(const void *p, FakeMemInfo *out, size_t n);

// How many bytes from p on can be read in its region, up to want (0: none). The fake game never reads past it (a real
// one would crash on such a path; the self-test must not).
static size_t readable(uint64_t p, size_t want) {
    FakeMemInfo mi;
    memset(&mi, 0, sizeof mi);
    if (p < 0x10000 || !VirtualQuery((const void *)(uintptr_t)p, &mi, sizeof mi) || mi.state != 0x1000u ||
        !(mi.protect & 0xeeu) || (mi.protect & 0x100u))
        return 0;
    uint64_t end = mi.base + mi.region_size;
    return end <= p ? 0 : end - p < want ? (size_t)(end - p) : want;
}

static void fake_open(struct Cpu *c, uint64_t which) {
    const FakeSafeString *s =
        readable(c->x[2], sizeof(FakeSafeString)) == sizeof(FakeSafeString) ? (const FakeSafeString *)(uintptr_t)c->x[2]
                                                                            : NULL;
    const char *p = s ? s->str : NULL;
    size_t n = p ? readable((uint64_t)(uintptr_t)p, 8) : 0;
    if (!n || (n == 8 && !strncmp(p, "missing/", 8))) c->x[0] = 0;
    char seen[64] = {0};
    for (size_t k = 0, m = p ? readable((uint64_t)(uintptr_t)p, 63) : 0; k < m && p[k]; k++) seen[k] = p[k];
    memcpy(&c->x[4], seen, sizeof seen);
    c->x[1] = which;
    c->x[2] = 0xdead;
    c->x[3] = 0xbeef;
}
static void fake_open_1(struct Cpu *c) { fake_open(c, 1); }
static void fake_open_2(struct Cpu *c) { fake_open(c, 2); }
static void fake_open_3(struct Cpu *c) { fake_open(c, 3); }
static void fake_open_4(struct Cpu *c) { fake_open(c, 4); }

static const uint64_t addrs[] = {0x7000001000ull, 0x7000002000ull, 0x7000e8fae0ull, 0x7000e92690ull, 0x7000eaba98ull,
                                 0x700115cba0ull};  // (sorted)
static const GuestFn fns[] = {fake_fn_a, fake_fn_b, fake_open_1, fake_open_2, fake_open_4, fake_open_3};
static const uint64_t count = 6;
static const GameLibModule modules[] = {
    {"main", 0x7000000000ull, 0x10000, "00", addrs, fns, &count},
};
static const uint32_t nmodules = 1;
static const GameLibSite sites[] = {{"player_info", 0x7000123450ull}};
static const uint32_t nsites = 1;

GAMELIB_EXPORT const GameLibTables *swgame_tables(void) {
    static const GameLibTables t = {GAMELIB_ABI, SELFTEST_ID, modules, &nmodules, "0.0.0-selftest", sites, &nsites};
    return &t;
}
