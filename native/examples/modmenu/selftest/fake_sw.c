// Self-test only: a much smaller, made-up stand-in for Savage Wind's Options code (runtime/ui/game_options.mm and its
// mouse code in 0.4.1-alpha.4), for the Mod menu's self-test (tests/test_modmenu_native.py). It is built together with
// native/modhost/selftest/fake_runtime.c into a DLL named savagewind.exe. Like Savage Wind it extends the game's
// option table with entries of its own the first time the screen opens (only when the table has the game's 14),
// answers its own rows only while its own table is in place, and closes and reopens the screen for a page change.
// Nothing here comes from the game. Three items on the first page, one each on two more pages.
//
// Builds: -DFAKE_OTHER_VERSION (another version text), -DFAKE_OTHER_BYTES (rt_opt_pointer_kind in plain C, so its
// first bytes differ), -DFAKE_ADD_PAGE (it exports sw_options_add_page, the proposed API).
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "../../../modhost/platform_min.h"
#include "../win_min.h"

#define EXPORT __declspec(dllexport)

#ifdef FAKE_OTHER_VERSION
EXPORT const char fake_sw_version[] = "Savage Wind 0.4.2-alpha.1";
#else
EXPORT const char fake_sw_version[] = "Savage Wind 0.4.1-alpha.4";
#endif

// fake_runtime.c (the same DLL)
typedef struct Reply Reply;
typedef void (*CmdFn)(int argc, char **argv, Reply *r);
typedef struct {
    const char *name, *args, *help;
    CmdFn fn;
    unsigned flags;
} Cmd;
void control_register(const Cmd *cmds, int count);
void control_field_str(Reply *r, const char *k, const char *v);
void control_field_int(Reply *r, const char *k, int64_t v);
void control_field_bool(Reply *r, const char *k, bool v);
void control_fail(Reply *r, const char *fmt, ...);

typedef struct {
    uint64_t name_vtbl, name;
    uint32_t kind, desc, title, first_value, last_value, button_off, button_on, pad;
} Entry;

static inline uint32_t rd32(uint64_t a) { return *(volatile uint32_t *)(uintptr_t)a; }
static inline uint64_t rd64(uint64_t a) { return *(volatile uint64_t *)(uintptr_t)a; }
static inline uint8_t rd8(uint64_t a) { return *(volatile uint8_t *)(uintptr_t)a; }
static inline void wr8(uint64_t a, uint8_t v) { *(volatile uint8_t *)(uintptr_t)a = v; }
static inline void wr32(uint64_t a, uint32_t v) { *(volatile uint32_t *)(uintptr_t)a = v; }
static inline void wr64(uint64_t a, uint64_t v) { *(volatile uint64_t *)(uintptr_t)a = v; }

enum { P_MAIN, P_ONE, P_TWO, P_COUNT };
enum { NITEMS = 5, GAME = 14, MSG_BASE = 9000 };
static const struct {
    int page, kind;  // kind: 0 ON/OFF, 1 choice (3 values), 2 button
    const char *title, *desc;
    int arg;         // a button: the page it opens (-1: quit)
} k_items[NITEMS] = {
    {P_MAIN, 2, "Fake page one", "Opens page one.", P_ONE},
    {P_MAIN, 2, "Fake page two", "Opens page two.", P_TWO},
    {P_MAIN, 2, "Quit game", "Asks whether to quit.", -1},
    {P_ONE, 0, "Fake toggle", "A made-up ON/OFF.", 0},
    {P_TWO, 1, "Fake choice", "A made-up choice.", 0},
};

static uint64_t T, g_mgr, g_title_off, g_rows_off;
static void (*g_click)(uint64_t row);
static Entry g_entries[GAME + NITEMS];
static char g_names[NITEMS][16];
static uint64_t g_installed;
static uint32_t g_order[P_COUNT][24], g_norder[P_COUNT];
static int g_value[NITEMS];
static int g_cur = P_MAIN, g_next = P_MAIN;
static bool g_reopen, g_switching;
static uint64_t g_dirty[64];
static int g_ndirty;
static int g_mouse = -1;   // the screen row a click is due on (the next rt_opt_tick)
static char g_kinds[80];   // what rt_opt_pointer_kind answered for each row at that click
static int g_hold;         // game_options_input_hold's answer
static int g_reads, g_installs;
static uint16_t g_text[NITEMS][20][40];

static uint64_t options_screen(void) {
    uint64_t mgr = g_mgr ? rd64(g_mgr) : 0;
    if (!mgr) return 0;
    uint64_t screens = rd64(mgr + 0x30);
    return screens && rd32(mgr + 0x28) > 0x34 ? rd64(screens + 8 * 0x34) : 0;
}

static bool install(void) {
    uint64_t ent = rd64(T + 8);
    if (g_installed && ent == g_installed) return true;
    if (rd32(T) != GAME || !ent) {
        g_installed = 0;
        return false;
    }
    memcpy(g_entries, (const void *)(uintptr_t)ent, GAME * sizeof(Entry));
    for (int i = 0; i < NITEMS; i++) {
        Entry *e = &g_entries[GAME + i];
        memset(e, 0, sizeof *e);
        e->name_vtbl = g_entries[0].name_vtbl;
        snprintf(g_names[i], sizeof g_names[i], "sw_option_%d", i);
        e->name = (uint64_t)(uintptr_t)g_names[i];
        e->kind = (uint32_t)k_items[i].kind;
        uint32_t m = MSG_BASE + 256u * (uint32_t)i;
        e->title = m, e->desc = m + 1, e->button_off = e->button_on = m + 2;
        e->first_value = m + 16;
        e->last_value = m + 16 + (k_items[i].kind == 1 ? 2 : 1);
    }
    for (int p = 0; p < P_COUNT; p++) g_norder[p] = 0;
    g_order[P_ONE][g_norder[P_ONE]++] = 1;  // (one of the game's own rows on page one)
    for (int i = 0; i < NITEMS; i++) g_order[k_items[i].page][g_norder[k_items[i].page]++] = GAME + (uint32_t)i;
    g_installed = (uint64_t)(uintptr_t)g_entries;
    wr64(T + 8, g_installed);
    wr32(T, GAME + NITEMS);
    g_installs++;
    return true;
}

static int item_of_index(uint32_t idx) {
    if (!g_installed || rd64(T + 8) != g_installed || idx < GAME || idx >= GAME + NITEMS) return -1;
    return (int)(idx - GAME);
}
static int item_of_row(uint64_t row) { return row ? item_of_index(rd32(row + 0x50)) : -1; }

static void put_text(uint16_t *t, const char *s, uint64_t out) {
    size_t n = 0;
    for (; s[n] && n < 39; n++) t[n] = (uint8_t)s[n];
    t[n] = 0;
    wr64(out, (uint64_t)(uintptr_t)t);
    wr32(out + 8, (uint32_t)n + 1);
}

EXPORT bool game_options_input_hold(void) { return g_hold != 0; }

EXPORT void rt_opt_rows(void) {
    if (!install()) return;
    int page = g_switching ? g_next : P_MAIN;
    g_switching = false;
    wr64(T + 0x18, (uint64_t)(uintptr_t)g_order[page]);
    wr32(T + 0x10, g_norder[page]);
    g_cur = page;
    g_reopen = false;
    g_ndirty = 0;
}

#ifdef FAKE_OTHER_BYTES
EXPORT int rt_opt_pointer_kind(uint64_t row) {
    if (!g_installed || rd64(T + 8) != g_installed || !row || game_options_input_hold()) return -1;
    uint32_t idx = rd32(row + 0x50);
    return idx < GAME + NITEMS ? (int)g_entries[idx].kind : -1;
}
#else
// The first 16 bytes of Savage Wind 0.4.1-alpha.4's rt_opt_pointer_kind (the bytes the mod checks; the instruction
// they start ends with one more 0xff), then traps. This stand-in never runs as it is: the mod puts a jump over it,
// and the runs where the mod stays off never use the mouse.
__asm__(".text\n"
        ".globl rt_opt_pointer_kind\n"
        ".p2align 4\n"
        "rt_opt_pointer_kind:\n"
        ".byte 0x56,0x48,0x83,0xec,0x30,0x48,0x8b,0x15,0x3c,0xf6,0x37,0x01,0xb8,0xff,0xff,0xff,0xff\n"
        ".fill 15, 1, 0xcc\n"
        ".section .drectve\n"
        ".ascii \" -export:rt_opt_pointer_kind\"\n"
        ".text\n");
int rt_opt_pointer_kind(uint64_t row);
#endif

EXPORT int rt_opt_flag_type(uint64_t entry) {
    if (!g_installed || entry < g_installed) return -1;
    int i = item_of_index((uint32_t)((entry - g_installed) / sizeof(Entry)));
    return i < 0 ? -1 : k_items[i].kind == 1;
}

EXPORT int rt_opt_read(uint64_t row) {
    int i = item_of_row(row);
    if (i < 0) return 0;
    g_reads++;
    if (k_items[i].kind == 2) {
        wr8(row + 0x64, 0);
        return 1;
    }
    wr8(row + 0x54, g_value[i] != 0);
    wr32(row + 0x58, (uint32_t)g_value[i]);
    wr32(row + 0x5c, k_items[i].kind == 1 ? 2 : 1);
    wr32(row + 0x60, 0);
    return 1;
}

EXPORT int rt_opt_store(uint64_t row) {
    int i = item_of_row(row);
    if (i < 0) return 0;
    if (k_items[i].kind == 0) g_value[i] = rd8(row + 0x54) != 0;
    if (k_items[i].kind == 1) {
        int v = (int)rd32(row + 0x58);
        g_value[i] = v < 0 ? 0 : v > 2 ? 2 : v;
    }
    return 1;
}

EXPORT int rt_opt_reset(uint64_t row) {
    int i = item_of_row(row);
    if (i < 0) return 0;
    g_value[i] = 0;
    return 1;
}

EXPORT int rt_opt_msg(uint32_t id, uint64_t out) {
    if (id < MSG_BASE || !g_installed) return 0;
    uint32_t i = (id - MSG_BASE) / 256, part = (id - MSG_BASE) % 256;
    if (i >= NITEMS || item_of_index(GAME + i) < 0 || part >= 20) return 0;
    char s[40] = "";
    if (part == 0) snprintf(s, sizeof s, "%s", k_items[i].title);
    else if (part == 1) snprintf(s, sizeof s, "%s", k_items[i].desc);
    else if (part == 2) snprintf(s, sizeof s, "%s", k_items[i].arg < 0 ? "Quit" : "Open");
    else if (part >= 16) snprintf(s, sizeof s, "Value %u", part - 16);
    put_text(g_text[i][part], s, out);
    return 1;
}

EXPORT int rt_opt_decide(uint64_t row) {
    int i = item_of_row(row);
    if (i < 0 || k_items[i].kind != 2) return 0;
    if (k_items[i].arg < 0) return 3;
    if (g_hold) return 2;
    g_next = k_items[i].arg;
    g_reopen = g_switching = true;
    return 1;
}

// The mouse (Savage Wind's pc_cursor options code): the kind of every row, then a click on the row asked for, through
// the game (its decide and redraw: g_click). Only when a click was asked for.
static void mouse(uint64_t screen) {
    uint32_t n = rd32(screen + g_rows_off);
    uint64_t rows = rd64(screen + g_rows_off + 8);
    size_t k = 0;
    uint64_t hit = 0;
    for (uint32_t i = 0; rows && i < n && i < 64 && k + 1 < sizeof g_kinds; i++) {
        uint64_t row = rd64(rows + 8 * i);
        int kind = rt_opt_pointer_kind(row);
        g_kinds[k++] = kind < 0 ? '-' : (char)('0' + kind);
        if ((int)i == g_mouse && kind >= 0 && kind <= 2) hit = row;
    }
    g_kinds[k] = 0;
    g_mouse = -1;
    if (hit && g_click) g_click(hit);
}

EXPORT uint64_t rt_opt_tick(uint64_t screen) {
    if (g_mouse >= 0 && !game_options_input_hold()) mouse(screen);
    if (!g_ndirty) return 0;
    uint64_t r = g_dirty[--g_ndirty];
    int i = item_of_row(r);
    if (i >= 0 && k_items[i].kind != 2) rt_opt_read(r);
    return r;
}

EXPORT void rt_opt_back(void) {
    if (g_installed && g_cur != P_MAIN) {
        g_next = P_MAIN;
        g_reopen = g_switching = true;
    } else {
        g_reopen = g_switching = false;
    }
}

EXPORT int rt_opt_title_tick(uint64_t menu) {
    if (rd32(menu + g_title_off) != 0x6a) {
        g_reopen = g_switching = false;
        return 0;
    }
    if (!g_reopen || options_screen()) return 0;
    g_reopen = false;
    return 1;
}

EXPORT int rt_opt_pause_tick(void) {
    if (!g_reopen) return 0;
    if (options_screen()) return 2;
    g_reopen = false;
    return 1;
}

EXPORT uint64_t rt_opt_screen(void) { return options_screen(); }

#ifdef FAKE_ADD_PAGE
// ---- the proposed sw_options_add_page (opt_page_b.c has the same layout)
typedef struct {
    uint32_t size;
    int kind;
    const char *title, *desc;
    int count, value;
    bool restart;
} SwOptionRow;
typedef struct {
    uint32_t size;
    const char *title, *desc;
    int (*rows)(void *user);
    void (*row)(void *user, int i, SwOptionRow *out);
    const char *(*label)(void *user, int i, int value);
    void (*set)(void *user, int i, int value);
    int (*press)(void *user, int i);
    void *user;
} SwOptionsPage;
static const SwOptionsPage *g_page;
EXPORT bool sw_options_add_page(const SwOptionsPage *page) {
    if (g_page || !page || page->size < sizeof(SwOptionsPage)) return false;
    g_page = page;
    return true;
}

// sw page: the page's rows as Savage Wind would show them; sw page set <i> <v>; sw page press <i>
static void cmd_page(int argc, char **argv, Reply *r) {
    if (!g_page) {
        control_fail(r, "no page added");
        return;
    }
    if (argc >= 5 && !strcmp(argv[2], "set")) g_page->set(g_page->user, atoi(argv[3]), atoi(argv[4]));
    if (argc >= 4 && !strcmp(argv[2], "press"))
        control_field_int(r, "pressed", g_page->press(g_page->user, atoi(argv[3])));
    control_field_str(r, "title", g_page->title);
    int n = g_page->rows(g_page->user);
    control_field_int(r, "count", n);
    for (int i = 0; i < n; i++) {
        SwOptionRow row = {sizeof row, -1, NULL, NULL, 0, 0, false};
        g_page->row(g_page->user, i, &row);
        char k[48];
        snprintf(k, sizeof k, "rows.%d.title", i);
        control_field_str(r, k, row.title ? row.title : "");
        snprintf(k, sizeof k, "rows.%d.kind", i);
        control_field_int(r, k, row.kind);
        snprintf(k, sizeof k, "rows.%d.value", i);
        control_field_int(r, k, row.value);
        snprintf(k, sizeof k, "rows.%d.desc", i);
        control_field_str(r, k, row.desc ? row.desc : "");
        snprintf(k, sizeof k, "rows.%d.label", i);
        control_field_str(r, k, g_page->label(g_page->user, i, row.kind == 1 ? row.value : -1));
    }
}
#endif

// ---- for the fake game and the test

// The game library gives its sites (Savage Wind's g_site) and the game's "row pressed" path, for the mouse.
EXPORT void fake_sw_setup(uint64_t table, uint64_t mgr, uint64_t title_off, uint64_t rows_off,
                          void (*click)(uint64_t row));
EXPORT uint64_t fake_sw_installed(void) { return g_installed; }
EXPORT void fake_sw_mouse(int row) { g_mouse = row; }

// The screen's rows all marked for a redraw (as Savage Wind does after a key capture or the quit question)
static void mark_all(void) {
    uint64_t s = options_screen();
    if (!s) return;
    uint32_t n = rd32(s + g_rows_off);
    uint64_t rows = rd64(s + g_rows_off + 8);
    for (uint32_t i = 0; rows && i < n && g_ndirty < 64; i++) g_dirty[g_ndirty++] = rd64(rows + 8 * i);
}

// sw: Savage Wind's state; sw hold 0|1; sw markall; sw page ... (FAKE_ADD_PAGE)
static void cmd_sw(int argc, char **argv, Reply *r) {
    if (argc >= 3 && !strcmp(argv[1], "hold")) g_hold = atoi(argv[2]);
    if (argc >= 2 && !strcmp(argv[1], "markall")) mark_all();
#ifdef FAKE_ADD_PAGE
    if (argc >= 2 && !strcmp(argv[1], "page")) {
        cmd_page(argc, argv, r);
        return;
    }
#endif
    control_field_bool(r, "installed", g_installed != 0);
    control_field_int(r, "installs", g_installs);
    control_field_int(r, "page", g_cur);
    control_field_int(r, "toggle", g_value[3]);
    control_field_int(r, "choice", g_value[4]);
    control_field_int(r, "reads", g_reads);
    control_field_int(r, "dirty", g_ndirty);
    control_field_str(r, "kinds", g_kinds);
    const uint8_t *pk = (const uint8_t *)(void *)rt_opt_pointer_kind;
    char hex[40];
    for (int i = 0; i < 16; i++) snprintf(hex + 2 * i, 3, "%02x", pk[i]);
    control_field_str(r, "pointer_kind", hex);
    control_field_bool(r, "pointer_kind_jump", pk[0] == 0xff && pk[1] == 0x25);
}

void fake_sw_setup(uint64_t table, uint64_t mgr, uint64_t title_off, uint64_t rows_off, void (*click)(uint64_t row)) {
    T = table, g_mgr = mgr, g_title_off = title_off, g_rows_off = rows_off, g_click = click;
    static const Cmd cmds[] = {{"sw", "[hold 0|1 | markall | page ...]", "The fake Savage Wind's options state",
                                cmd_sw, 0}};
    control_register(cmds, 1);
}
