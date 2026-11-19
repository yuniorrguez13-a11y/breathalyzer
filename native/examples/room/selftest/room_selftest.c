// Self-test of More Room's bookkeeping (room_core.h) without the game (tests/test_room_native.py builds and runs it).
// room_selftest(out, cap) writes one line per check ("<group> <check> PASS|FAIL <detail>") and returns the number of
// failed checks. The numbers are made up (a 16 GB gaming PC, the 8 GB laptop, heap sizes like the game's).
#include "../room_core.h"

int vsnprintf(char *buf, size_t n, const char *fmt, __builtin_va_list ap);

static char *g_out;
static int g_cap, g_len, g_fails;
static void say(const char *fmt, ...) {
    if (!g_out || g_len >= g_cap) return;
    __builtin_va_list ap;
    __builtin_va_start(ap, fmt);
    int n = vsnprintf(g_out + g_len, (size_t)(g_cap - g_len), fmt, ap);
    __builtin_va_end(ap);
    if (n > 0) g_len = g_len + n < g_cap ? g_len + n : g_cap - 1;
}
static void check(const char *group, const char *name, int ok, const char *fmt, ...) {
    char detail[256];
    __builtin_va_list ap;
    __builtin_va_start(ap, fmt);
    vsnprintf(detail, sizeof detail, fmt, ap);
    __builtin_va_end(ap);
    if (!ok) g_fails++;
    say("%s %s %s %s\n", group, name, ok ? "PASS" : "FAIL", detail);
}

static RoomState st;  // (big: not on the stack)

static void machine(RoomState *s, double total_gb, double free_gb, double vram_gb, double shared_gb) {
    s->machine.ram_total = (uint64_t)(total_gb * 1024 * ROOM_MB);
    s->machine.ram_free = (uint64_t)(free_gb * 1024 * ROOM_MB);
    s->machine.vram_dedicated = (uint64_t)(vram_gb * 1024 * ROOM_MB);
    s->machine.vram_shared = (uint64_t)(shared_gb * 1024 * ROOM_MB);
    room_set_cap(s);
}

static RoomFail fail(int dual, const char *name, uint64_t size, uint64_t over, uint64_t req) {
    RoomFail f;
    memset(&f, 0, sizeof f);
    f.dual = dual;
    snprintf(f.name, sizeof f.name, "%s", name);
    f.heap_size = size, f.over = over, f.request = req, f.align = 8;
    return f;
}

static void t_watch(void) {
    room_init(&st);
    check("watch", "add", room_watch(&st, "Dm_Npc_Handmaid_A") && room_watched(&st, "Dm_Npc_Handmaid_A"), "added");
    check("watch", "twice", room_watch(&st, "Dm_Npc_Handmaid_A") && st.nwatch == 1, "nwatch %d", st.nwatch);
    check("watch", "other", !room_watched(&st, "Dm_Npc_Handmaid_B") && !room_watched(&st, ""), "not watched");
    char longname[100];
    memset(longname, 'a', sizeof longname - 1);
    longname[sizeof longname - 1] = 0;
    check("watch", "long_refused", !room_watch(&st, longname) && !room_watched(&st, longname), "refused");
    int ok = 1;
    char nm[32];
    for (int i = 0; i < ROOM_NAMES; i++) {
        snprintf(nm, sizeof nm, "Actor_%d", i);
        if (!room_watch(&st, nm)) break;
    }
    ok = st.nwatch <= ROOM_NAMES * 3 / 4 && room_watched(&st, "Dm_Npc_Handmaid_A") && room_watched(&st, "Actor_5");
    check("watch", "full_stops", ok, "nwatch %d of %d", st.nwatch, ROOM_NAMES);
}

static void t_cap(void) {
    room_init(&st);
    machine(&st, 16, 10, 4, 8);  // GTX 1650 class, 16 GB
    check("cap", "gaming_pc", st.cap == 1024 * ROOM_MB, "cap %lld", (long long)st.cap);
    check("cap", "gaming_vram", st.vram_budget == 1024 * ROOM_MB, "vram budget %lld", (long long)st.vram_budget);
    machine(&st, 8, 3.5, 0.125, 7.8);  // the iGPU laptop, the game running
    check("cap", "laptop", st.cap == 512 * ROOM_MB, "cap %lld", (long long)st.cap);
    check("cap", "laptop_vram_shared", st.vram_budget > 900 * ROOM_MB && st.vram_budget < 1000 * ROOM_MB,
          "vram budget %lld (half the shared, a quarter of that)", (long long)st.vram_budget);
    machine(&st, 8, 1.0, 0.125, 7.8);  // nearly full
    check("cap", "floor", st.cap == 16 * ROOM_MB, "cap %lld", (long long)st.cap);
    char text[256];
    room_cap_text(&st, text, sizeof text);
    check("cap", "text", strstr(text, "RAM 8.0 GB") && strstr(text, "16.0 MB"), "%s", text);
}

static void t_need(void) {
    room_init(&st);
    machine(&st, 16, 10, 4, 8);
    room_watch(&st, "Dm_Npc_Handmaid_A");
    RoomFail f = fail(1, "Dm_Npc_Handmaid_A", 900000, 0, 2000000);
    check("need", "logged", room_add_fail(&st, &f), "first failure logged");
    RoomNeed *n = room_need(&st, "Dm_Npc_Handmaid_A", 0);
    int64_t want = (900000 + 2000000 + 8) * 3 / 2;
    check("need", "from_failure", n && n->need == want, "need %lld want %lld", n ? (long long)n->need : -1,
          (long long)want);
    int64_t add = room_heap_extra(&st, "Dm_Npc_Handmaid_A", 880000, 0);
    check("need", "next_creation", add == want - 880000, "add %lld", (long long)add);
    check("need", "class_extra_counted", room_heap_extra(&st, "Dm_Npc_Handmaid_A", 880000, 0xa90) == want - 880000 - 0xa90,
          "the class's own extra counts as room it has");
    RoomFail g = fail(1, "Dm_Npc_Handmaid_B", 900000, 0, 2000000);
    room_add_fail(&st, &g);
    check("need", "unwatched_none", !room_need(&st, "Dm_Npc_Handmaid_B", 0) &&
          room_heap_extra(&st, "Dm_Npc_Handmaid_B", 880000, 0) == 0, "a game actor is never raised");
    RoomFail e = fail(0, "Dm_Npc_Handmaid_A", 900000, 0, 5000000);
    room_add_fail(&st, &e);
    check("need", "game_heap_no_need", room_need(&st, "Dm_Npc_Handmaid_A", 0)->need == want,
          "a game heap's failure doesn't change the actor's need");
    RoomFail f2 = fail(1, "Dm_Npc_Handmaid_A", 4350012, 0, 3000000);
    room_add_fail(&st, &f2);
    n = room_need(&st, "Dm_Npc_Handmaid_A", 0);
    check("need", "grows", n->need == (4350012 + 3000000 + 8) * 3 / 2, "need %lld", (long long)n->need);
    room_add_fail(&st, &f2);
    room_add_fail(&st, &f2);
    room_add_fail(&st, &f2);
    check("need", "repeats_quiet", !room_add_fail(&st, &f2) && st.nfails == 4, "the 4th repeat isn't logged (%d)",
          st.nfails);
}

static void t_boost(void) {
    room_init(&st);
    machine(&st, 8, 2.0, 0.125, 7.8);  // cap 128 MB
    room_watch(&st, "SwCostume");
    st.boost_mode = ROOM_BOOST_FIXED, st.boost_fixed = 8 * ROOM_MB;
    check("boost", "fixed", room_heap_extra(&st, "SwCostume", 1000000, 0) == 8 * ROOM_MB, "8 MB");
    check("boost", "fixed_unwatched", room_heap_extra(&st, "Other", 1000000, 0) == 0, "0");
    st.boost_fixed = 900 * ROOM_MB;
    int64_t add = room_heap_extra(&st, "SwCostume", 1000000, 0);
    RoomNeed *n = room_need(&st, "SwCostume", 0);
    check("boost", "capped", add == st.cap && n && n->capped, "add %lld cap %lld", (long long)add, (long long)st.cap);
    st.boost_mode = ROOM_BOOST_OFF;
    check("boost", "off", room_heap_extra(&st, "SwCostume", 1000000, 0) == 0, "0");
    st.boost_mode = ROOM_BOOST_AUTO;
    room_need(&st, "SwCostume", 0)->need = 0;
    check("boost", "auto_nothing_needed", room_heap_extra(&st, "SwCostume", 1000000, 0) == 0, "no failure yet: 0");
}

static void t_text(void) {
    room_init(&st);
    machine(&st, 16, 10, 4, 8);
    room_watch(&st, "Dm_Npc_Handmaid_A");
    RoomFail f = fail(1, "Dm_Npc_Handmaid_A", 905000, 12, 2000000);
    f.free_b = 1234, f.max_b = 1000, f.has2 = 1, f.free2 = 55, f.max2 = 50;
    snprintf(f.heap2, sizeof f.heap2, "ActorOverflow");
    char line[600];
    room_fail_text(&f, line, sizeof line);
    check("text", "fail", strstr(line, "\"Dm_Npc_Handmaid_A\" (905000 bytes) needed 2000000 bytes, had 1234 free") &&
          strstr(line, "overflow \"ActorOverflow\": 55 free"), "%s", line);
    room_add_fail(&st, &f);
    room_heap_seen(&st, "Dm_Npc_Handmaid_A", 0x7250000000ull, 905000, 12, 2000000, 0);
    static char rep[8192];
    room_report(&st, rep, sizeof rep);
    check("text", "report", strstr(rep, "need Dm_Npc_Handmaid_A") && strstr(rep, "heap Dm_Npc_Handmaid_A: 905000") &&
          strstr(rep, "fail actor heap"), "%.200s", rep);
    char warn[400];
    check("text", "textures_fit", !room_textures_warn(&st, "custom", 6 * ROOM_MB, warn, sizeof warn), "6 MB fits");
    int w = room_textures_warn(&st, "custom", 2048 * ROOM_MB, warn, sizeof warn);
    check("text", "textures_warn", w && strstr(warn, "2.0 GB of textures"), "%s", warn);
    static char small[64];
    room_report(&st, small, sizeof small);
    check("text", "report_small_buffer", strlen(small) < sizeof small, "cut, never past the buffer");
}

// A made-up FRES header with its name at 0x40 (u16 length, text), Yaz0-packed with literals and one back-reference.
static size_t made_up_fres(const char *name, uint8_t *raw, size_t cap) {
    memset(raw, 0, cap);
    memcpy(raw, "FRES    ", 8);
    uint64_t off = 0x40;
    memcpy(raw + 0x20, &off, 8);
    size_t n = strlen(name);
    raw[0x40] = (uint8_t)n, raw[0x41] = 0;
    memcpy(raw + 0x42, name, n);
    return 0x42 + n + 1;
}
static size_t yaz0_pack(const uint8_t *raw, size_t n, uint8_t *out) {
    // literal groups, except the 0x18 zero bytes at 0x28: one back-reference to the zeros at 0x08 (dist 0x20)
    size_t o = 16, i = 0;
    memcpy(out, "Yaz0", 4);
    out[4] = (uint8_t)(n >> 24), out[5] = (uint8_t)(n >> 16), out[6] = (uint8_t)(n >> 8), out[7] = (uint8_t)n;
    memset(out + 8, 0, 8);
    while (i < n) {
        size_t code_at = o++;
        uint8_t code = 0;
        for (int b = 0; b < 8 && i < n; b++) {
            if (i == 0x28 && n > 0x40) {  // 0x18 bytes from 0x28 to 0x40 copy the zeros at 0x08
                out[o++] = (uint8_t)((0x20 - 1) >> 8), out[o++] = (uint8_t)((0x20 - 1) & 0xff);  // (3-byte form)
                out[o++] = (uint8_t)(0x18 - 0x12);
                i += 0x18;
            } else {
                code |= (uint8_t)(0x80 >> b);
                out[o++] = raw[i++];
            }
        }
        out[code_at] = code;
    }
    return o;
}

static void t_names(void) {
    static uint8_t raw[256], packed[512], un[256];
    size_t n = made_up_fres("Link", raw, sizeof raw);
    size_t p = yaz0_pack(raw, n, packed);
    size_t got = room_yaz0(packed, p, un, sizeof un);
    check("names", "yaz0", got == n && !memcmp(un, raw, n), "%zu of %zu bytes, same", got, n);
    char inside[64] = "";
    check("names", "fres_name", room_fres_name(un, got, inside, sizeof inside) && !strcmp(inside, "Link"), "%s",
          inside);
    check("names", "link_inside_costume", room_model_misnamed("SwChar_linkle.sbfres", inside), "misnamed");
    check("names", "own_name_ok", !room_model_misnamed("SwChar_linkle.sbfres", "SwChar_linkle") &&
          !room_model_misnamed("Npc_Gerudo_Hero.Tex.sbfres", "Npc_Gerudo_Hero.Tex"), "named as the file");
    check("names", "tex_misnamed", room_model_misnamed("SwChar_linkle.Tex.sbfres", "Link.Tex"), "misnamed");
    check("names", "not_yaz0", room_yaz0(raw, n, un, sizeof un) == 0 && !room_fres_name(packed, p, inside, 64),
          "refused");
    raw[0x40] = 200;  // a name past the end
    check("names", "name_out_of_range", !room_fres_name(raw, n, inside, sizeof inside), "refused");
}

// The texture rescue: TextureArchiveWork's failures (192 MB, 10-14 MB asks while the world loaded, 2026-11-15) go to
// the extra memory, made at the cap's size (64 MB to 1 GB).
static void t_rescue(void) {
    room_init(&st);
    machine(&st, 16, 10, 4, 8);
    check("rescue", "default_textures", room_rescue_wanted(&st, "TextureArchiveWork") &&
          !room_rescue_wanted(&st, "TeraSZSWork") && !room_rescue_wanted(&st, "Dm_Npc_Handmaid_A") &&
          !room_rescue_wanted(&st, ""), "only TextureArchiveWork by default");
    check("rescue", "add_heap", room_rescue_add(&st, "TeraSZSWork") && room_rescue_add(&st, "TeraSZSWork") &&
          st.rescue.nheaps == 2 && room_rescue_wanted(&st, "TeraSZSWork"), "added once");
    int added = 0;
    char nm[32];
    for (int i = 0; i < 20; i++) {
        snprintf(nm, sizeof nm, "Heap_%d", i);
        added += room_rescue_add(&st, nm);
    }
    check("rescue", "full_stops", st.rescue.nheaps == ROOM_RESCUE_HEAPS && added == ROOM_RESCUE_HEAPS - 2,
          "%d heaps", st.rescue.nheaps);
    st.rescue.on = 0;
    check("rescue", "off", !room_rescue_wanted(&st, "TextureArchiveWork"), "off: failures stay failures");
    st.rescue.on = 1;
    check("rescue", "size_gaming_pc", room_rescue_size(&st, 14385352) == 1024 * ROOM_MB, "%lld",
          (long long)room_rescue_size(&st, 14385352));
    machine(&st, 8, 3.5, 0.125, 7.8);
    check("rescue", "size_laptop", room_rescue_size(&st, 14385352) == 512 * ROOM_MB, "%lld",
          (long long)room_rescue_size(&st, 14385352));
    machine(&st, 8, 1.0, 0.125, 7.8);
    check("rescue", "size_floor", room_rescue_size(&st, 14385352) == ROOM_RESCUE_MIN, "%lld",
          (long long)room_rescue_size(&st, 14385352));
    check("rescue", "too_big", room_rescue_size(&st, 200 * ROOM_MB) == 0 && room_rescue_size(&st, 0x80000000ull) == 0,
          "a 200 MB ask on a 64 MB extra memory, a 2 GB ask: none");
    machine(&st, 16, 10, 4, 8);
    check("rescue", "size_200mb_model", room_rescue_size(&st, 200 * ROOM_MB) == 1024 * ROOM_MB,
          "a 200 MB texture fits a gaming PC's extra memory");
    st.rescue.want_size = 64 * ROOM_MB;
    int64_t asked = room_rescue_size(&st, ROOM_MB);
    st.rescue.want_size = 5 * ROOM_MB;
    int64_t tiny = room_rescue_size(&st, ROOM_MB);
    st.rescue.want_size = 4096 * ROOM_MB;
    int64_t huge = room_rescue_size(&st, ROOM_MB);
    st.rescue.want_size = 0;
    check("rescue", "size_asked", asked == 64 * ROOM_MB && tiny == 16 * ROOM_MB && huge == ROOM_RESCUE_MAX,
          "room rescue size: %lld, %lld, %lld", (long long)asked, (long long)tiny, (long long)huge);
    check("rescue", "not_made_has_nothing", !room_rescue_has(&st, 0x7400001000ull), "no heap yet");
    st.rescue.state = 1, st.rescue.base = 0x7400000000ull, st.rescue.size = 1024 * ROOM_MB;
    st.rescue.heap = st.rescue.base + ROOM_RESCUE_HEAD;
    st.rescue.free_now = st.rescue.size - ROOM_RESCUE_HEAD;
    check("rescue", "has", room_rescue_has(&st, st.rescue.heap + 0x200) && room_rescue_has(&st, st.rescue.base +
          st.rescue.size - 1) && !room_rescue_has(&st, st.rescue.base) && !room_rescue_has(&st, st.rescue.base +
          st.rescue.size) && !room_rescue_has(&st, 0x7250000000ull), "its heap's memory only");
    check("rescue", "root_slot", room_root_slot_ok(1, 4, 0x7001e33f98ull, 0x7001e33f98ull) &&
          !room_root_slot_ok(4, 4, 0x7001e33f98ull, 0x7001e33f98ull) &&
          !room_root_slot_ok(0, 4, 0x7001e33f98ull, 0x7001e33f98ull) &&
          !room_root_slot_ok(1, 4, 0x7250000000ull, 0x7001e33f98ull) &&
          !room_root_slot_ok(1, 4000, 0x7001e33f98ull, 0x7001e33f98ull), "a free slot in a list that looks right");
    int logged = 0;
    for (int i = 0; i < 250; i++) logged += room_rescue_given(&st, 10 * ROOM_MB, st.rescue.size - ROOM_RESCUE_HEAD -
                                                                                     (uint64_t)(i + 1) * ROOM_MB);
    check("rescue", "log_rate", logged == 20 + 2 && st.rescue.given == 250, "%d lines for 250 allocations", logged);
    check("rescue", "used", room_rescue_used(&st) == 250 * ROOM_MB, "%lld", (long long)room_rescue_used(&st));
    RoomFail f = fail(0, "TextureArchiveWork", 201326288, 0, 14385352);
    f.free_b = 639232, f.max_b = 538080;
    char line[600];
    room_rescue_line(&st, &f, line, sizeof line);
    check("rescue", "line", strstr(line, "TextureArchiveWork (192.0 MB) was full (needed 13.7 MB, had 0.6 MB free") &&
          strstr(line, "250.0 MB of 1.0 GB in use"), "%s", line);
    char warn[400];
    check("rescue", "vram_quiet", !room_rescue_vram_warn(&st, warn, sizeof warn), "250 MB fits a 4 GB card's share");
    machine(&st, 8, 3.5, 0.125, 0.8);  // (a small shared share: 100 MB)
    int w1 = room_rescue_vram_warn(&st, warn, sizeof warn), w2 = room_rescue_vram_warn(&st, warn, sizeof warn);
    check("rescue", "vram_warn_once", w1 && !w2 && strstr(warn, "extra texture memory has 250.0 MB"), "%s", warn);
    st.rescue.root = 1, st.rescue.refused = 3, st.rescue.redirected = 7;
    char text[600];
    room_rescue_text(&st, text, sizeof text);
    check("rescue", "text_made", strstr(text, "texture rescue: on, for TextureArchiveWork") &&
          strstr(text, "gave 250 allocations") && strstr(text, "3 refused") && strstr(text, "7 frees"), "%s", text);
    st.rescue.state = 2;
    snprintf(st.rescue.why, sizeof st.rescue.why, "Windows gave no memory (1.0 GB asked: the PC's RAM and page file are full)");
    room_rescue_text(&st, text, sizeof text);
    check("rescue", "text_not_made", strstr(text, "couldn't be made: Windows gave no memory") != NULL, "%s", text);
    room_init(&st);
    room_rescue_text(&st, text, sizeof text);
    static char rep[8192];
    room_report(&st, rep, sizeof rep);
    check("rescue", "text_waiting", strstr(text, "no heap ran out yet") && strstr(rep, "texture rescue: on"), "%s", text);
}

__declspec(dllexport) int room_selftest(char *out, int cap) {
    g_out = out, g_cap = cap, g_len = 0, g_fails = 0;
    if (cap > 0) out[0] = 0;
    t_watch();
    t_cap();
    t_need();
    t_boost();
    t_text();
    t_names();
    t_rescue();
    return g_fails;
}
