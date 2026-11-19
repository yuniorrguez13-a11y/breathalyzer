// Self-test only: a made-up stand-in for the game code library (swgame.dll) for the Mod menu's self-test
// (tests/test_modmenu_native.py). Like the real one it calls Savage Wind's rt_opt_* functions through its import table
// (it is linked against Savage Wind's savagewind.dll.a; in the test they bind to the fake savagewind.exe, fake_sw.c),
// and it plays a much simplified Options screen with the command `g`: open it from the title or the pause menu, build
// its rows, move, A, left/right, Y, B, frames (the screen's update, closing, the menus' reopen), mouse clicks, and a
// reboot. Its "guest memory" (the option table, the screen manager, the screen and the title menu) is plain host
// memory. Nothing here comes from the game: the 14 entries, their texts and the row order are made up.
//
// Environment: FAKE_GAME_VERSION (default 1.9.0), FAKE_BOOTED=1 (the table is in use before the mods load, as for a
// mod loaded with modload), FAKE_TAMPER=1 (something else changed the rt_opt_tick import slot first),
// FAKE_NO_SITE=1 (the site table lacks opt_screen_rows).
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "../../../modhost/platform_min.h"
#include "../win_min.h"
#include "../../../modhost/sw_runtime.h"

#define FN_IMPORT __declspec(dllimport)
FN_IMPORT void rt_opt_rows(void);
FN_IMPORT int rt_opt_flag_type(uint64_t entry);
FN_IMPORT int rt_opt_read(uint64_t row);
FN_IMPORT int rt_opt_store(uint64_t row);
FN_IMPORT int rt_opt_reset(uint64_t row);
FN_IMPORT int rt_opt_msg(uint32_t id, uint64_t out);
FN_IMPORT int rt_opt_decide(uint64_t row);
FN_IMPORT uint64_t rt_opt_tick(uint64_t screen);
FN_IMPORT void rt_opt_back(void);
FN_IMPORT int rt_opt_title_tick(uint64_t menu);
FN_IMPORT int rt_opt_pause_tick(void);
FN_IMPORT uint64_t rt_opt_screen(void);
extern void *__imp_rt_opt_tick;  // (its import slot)

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
#define ADDR(p) ((uint64_t)(uintptr_t)(p))

#define TITLE_STATE 0x3654u  // (the 1.9.0 field offsets; any would do here)
#define SCREEN_ROWS 0x36b0u
#define MAX_ROWS 64

static struct {          // the option table: u32 count, +8 entries, +0x10 rows, +0x18 order
    uint32_t count, pad0;
    uint64_t entries;
    uint32_t rows, pad1;
    uint64_t order;
} g_table;
static uint64_t g_vtbl[2];                 // a stand-in for the flag names' SafeString vtable
static char g_flag[14][16];
static Entry g_game[14];
static uint32_t g_game_order[13] = {9, 13, 1, 2, 7, 4, 3, 6, 8, 5, 10, 11, 12};
static int g_game_value[14];
static struct {
    uint8_t pad[0x28];
    uint32_t count, pad1;
    uint64_t screens;
} g_mgr_obj;
static uint64_t g_screens[0x40];
static uint64_t g_mgr = 0;                 // site screen_mgr: the ScreenMgr pointer
static uint8_t g_screen[0x3700] __attribute__((aligned(16)));
static uint8_t g_menu[0x3700] __attribute__((aligned(16)));
static uint8_t g_row_mem[MAX_ROWS][0x98] __attribute__((aligned(16)));
static uint64_t g_row_ptr[MAX_ROWS];
static char g_row_title[MAX_ROWS][112], g_row_value[MAX_ROWS][112];
static int g_nrows, g_cursor;
static int g_closing;                      // frames until the closing screen is gone (0: not closing)
static int g_opener;                       // 0 none, 1 title menu, 2 pause menu
static bool g_pause_active;
static int g_opens;
static bool g_overflow;
static GameLibSite g_sites[4];
static uint32_t g_nsites = 4;

static void text_of(uint32_t id, char *out, size_t cap);

// ------------------------------------------------------------------ the made-up game data

static void boot(void) {  // the game's own table, as at its start (and again after a reboot)
    g_vtbl[0] = 0x1234;
    for (int i = 0; i < 14; i++) {
        Entry *e = &g_game[i];
        memset(e, 0, sizeof *e);
        snprintf(g_flag[i], sizeof g_flag[i], "FakeFlag%d", i);
        e->name_vtbl = ADDR(g_vtbl), e->name = ADDR(g_flag[i]);
        e->kind = i == 7 ? 1 : 0;
        e->desc = 1u + (uint32_t)i, e->title = 19u + (uint32_t)i;
        e->first_value = i == 7 ? 10 : 0, e->last_value = i == 7 ? 14 : 0;
    }
    g_table.count = 14, g_table.entries = ADDR(g_game), g_table.rows = 13, g_table.order = ADDR(g_game_order);
    g_mgr_obj.count = 0x40, g_mgr_obj.screens = ADDR(g_screens);
    g_mgr = ADDR(&g_mgr_obj);
}

static const Entry *entry_of(uint32_t idx) {  // as the game does: entries[idx], or entry 0 past the end
    const Entry *e = (const Entry *)(uintptr_t)g_table.entries;
    return idx < g_table.count ? &e[idx] : &e[0];
}

static uint64_t row_at(int i) { return ADDR(g_row_mem[i]); }

static void game_read(uint64_t row) {
    uint32_t idx = rd32(row + 0x50);
    const Entry *e = entry_of(idx);
    int v = idx < 14 ? g_game_value[idx] : 0;
    wr8(row + 0x54, v != 0);
    wr32(row + 0x58, (uint32_t)v);
    wr32(row + 0x5c, e->kind == 1 ? e->last_value - e->first_value : 1);
    wr32(row + 0x60, 0);
}

static void game_store(uint64_t row) {
    uint32_t idx = rd32(row + 0x50);
    if (idx < 14) g_game_value[idx] = entry_of(idx)->kind == 1 ? (int)rd32(row + 0x58) : rd8(row + 0x54);
}

// The row's widget shows its value: ON/OFF, a choice's text (first value + value), or its button text
static void redraw(int i) {
    uint64_t row = row_at(i);
    const Entry *e = entry_of(rd32(row + 0x50));
    if (e->kind == 0) snprintf(g_row_value[i], sizeof g_row_value[i], "%s", rd8(row + 0x54) ? "ON" : "OFF");
    else if (e->kind == 1) text_of(e->first_value + rd32(row + 0x58), g_row_value[i], sizeof g_row_value[i]);
    else text_of(e->button_off, g_row_value[i], sizeof g_row_value[i]);
}

static int row_index(uint64_t row) {
    for (int i = 0; i < g_nrows; i++)
        if (row_at(i) == row) return i;
    return -1;
}

// A text: Savage Wind's (or the mod's) answer, else the game's own lookup
static void text_of(uint32_t id, char *out, size_t cap) {
    struct {
        uint64_t s;
        uint32_t len, pad;
    } m = {0, 0, 0};
    if (rt_opt_msg(id, ADDR(&m))) {
        // (the game's message tags, 0x0e group type size params, show as <font N>, </font>, <grey>, </grey>, <tag>)
        const uint16_t *s = (const uint16_t *)(uintptr_t)m.s;
        size_t n = 0;
        for (uint32_t i = 0; s && i + 1 < m.len && n + 1 < cap; i++) {
            char tag[24] = "";
            if (s[i] == 0x0e && i + 4 < m.len) {
                uint16_t type = s[i + 2], size = s[i + 3], v = size >= 2 ? s[i + 4] : 0;
                if (type == 1) snprintf(tag, sizeof tag, v == 0xffff ? "</font>" : "<font %u>", v);
                else if (type == 3) snprintf(tag, sizeof tag, v == 0xffff ? "</grey>" : "<grey>");
                else snprintf(tag, sizeof tag, "<tag>");
                i += 3 + size / 2u;
                for (const char *t = tag; *t && n + 1 < cap; t++) out[n++] = *t;
                continue;
            }
            if (!s[i]) break;
            out[n++] = s[i] < 0x80 ? (char)s[i] : '?';
        }
        out[n] = 0;
    } else {
        snprintf(out, cap, "Game text %04u", id);
    }
}

// ------------------------------------------------------------------ the screen

static void open_screen(void) {  // createRows (rt_opt_rows first), then each row's init
    g_screens[0x34] = ADDR(g_screen);
    g_closing = 0;
    g_opens++;
    rt_opt_screen();
    rt_opt_rows();
    uint32_t n = g_table.rows;
    g_overflow = n > MAX_ROWS;
    g_nrows = (int)(n > MAX_ROWS ? MAX_ROWS : n);
    for (int i = 0; i < g_nrows; i++) {
        uint64_t row = row_at(i);
        uint32_t idx = rd32(g_table.order + 4u * (uint32_t)i);
        memset(g_row_mem[i], 0, sizeof g_row_mem[i]);
        wr64(row, ADDR(g_screen));
        wr32(row + 0x50, idx);
        const Entry *e = entry_of(idx);
        int t = rt_opt_flag_type(ADDR(e));
        wr32(row + 0x68, t >= 0 ? (uint32_t)t : e->kind == 1);
        if (!rt_opt_read(row)) game_read(row);
        text_of(e->title, g_row_title[i], sizeof g_row_title[i]);
        redraw(i);
        g_row_ptr[i] = row;
    }
    wr32(ADDR(g_screen) + SCREEN_ROWS, (uint32_t)g_nrows);
    wr64(ADDR(g_screen) + SCREEN_ROWS + 8, ADDR(g_row_ptr));
    g_cursor = 0;
}

static void close_screen(void) { g_closing = 2; }

// A on a row (the game's decide; Savage Wind's mouse presses rows through this too)
static const char *press(int i) {
    uint64_t row = row_at(i);
    int r = rt_opt_decide(row);
    if (r == 1) {
        close_screen();
        return "closed";
    }
    if (r == 2) return "done";
    if (r == 3) return "quit";
    if (entry_of(rd32(row + 0x50))->kind == 0) {  // (the game's own: ON/OFF flips)
        wr8(row + 0x54, !rd8(row + 0x54));
        if (!rt_opt_store(row)) game_store(row);
    }
    redraw(i);
    return "game";
}

static void click(uint64_t row) {  // Savage Wind's mouse code: the game's decide, then a redraw
    int i = row_index(row);
    if (i >= 0) press(i);
}

static void step(int i, int d) {  // left / right
    uint64_t row = row_at(i);
    const Entry *e = entry_of(rd32(row + 0x50));
    if (e->kind == 0) {
        wr8(row + 0x54, !rd8(row + 0x54));
    } else if (e->kind == 1) {
        int v = (int)rd32(row + 0x58) + d, lo = (int)rd32(row + 0x60), hi = (int)rd32(row + 0x5c);
        if (v < lo || v > hi) return;
        wr32(row + 0x58, (uint32_t)v);
    } else {
        return;
    }
    if (!rt_opt_store(row)) game_store(row);
    redraw(i);
}

static int frame(void) {  // returns how many rows were redrawn
    int redraws = 0;
    if (g_screens[0x34] && !g_closing) {
        uint64_t r = rt_opt_tick(ADDR(g_screen));
        int i = r > 1 ? row_index(r) : -1;
        if (i >= 0) {
            redraw(i);
            redraws++;
        }
    }
    if (g_closing && --g_closing == 0) {
        g_screens[0x34] = 0;
        g_nrows = 0;
    }
    if (g_opener == 1) {  // the title menu's update
        if (rt_opt_title_tick(ADDR(g_menu))) open_screen();
        else if (!g_screens[0x34] && rd32(ADDR(g_menu) + TITLE_STATE) == 0x6a) wr32(ADDR(g_menu) + TITLE_STATE, 0);
        if (rd32(ADDR(g_menu) + TITLE_STATE) != 0x6a) g_opener = 0;
    } else if (g_opener == 2) {  // the pause menu's System page, Options state
        int r = rt_opt_pause_tick();
        if (r == 1) open_screen();
        else if (r == 0 && !g_screens[0x34]) g_opener = 0;
    }
    return redraws;
}

// ------------------------------------------------------------------ the command

static void reply_rows(ControlReply *r) {
    control_field_int(r, "count", g_nrows);
    control_field_bool(r, "open", g_screens[0x34] != 0 && !g_closing);
    for (int i = 0; i < g_nrows; i++) {
        char k[32];
        snprintf(k, sizeof k, "rows.%d.title", i);
        control_field_str(r, k, g_row_title[i]);
        snprintf(k, sizeof k, "rows.%d.value", i);
        control_field_str(r, k, g_row_value[i]);
        snprintf(k, sizeof k, "rows.%d.kind", i);
        control_field_int(r, k, entry_of(rd32(row_at(i) + 0x50))->kind);
        control_text(r, "%2d %s %-28s %s", i, i == g_cursor ? ">" : " ", g_row_title[i], g_row_value[i]);
    }
}

static const char *const k_imports[] = {"rt_opt_rows", "rt_opt_flag_type", "rt_opt_read", "rt_opt_store",
                                        "rt_opt_reset", "rt_opt_msg", "rt_opt_decide", "rt_opt_tick",
                                        "rt_opt_back", "rt_opt_title_tick", "rt_opt_pause_tick"};

static void cmd_g(int argc, char **argv, ControlReply *r) {
    const char *c = argc > 1 ? argv[1] : "";
    int n = argc > 2 ? atoi(argv[2]) : 0;
    if (!strcmp(c, "boot") || !strcmp(c, "reboot")) {
        g_screens[0x34] = 0, g_nrows = 0, g_closing = 0, g_opener = 0;
        boot();
    } else if (!strcmp(c, "open")) {
        if (g_screens[0x34]) {
            control_fail(r, "the screen is open");
            return;
        }
        g_opener = argc > 2 && !strcmp(argv[2], "title") ? 1 : 2;
        if (g_opener == 1) wr32(ADDR(g_menu) + TITLE_STATE, 0x6a);
        open_screen();
    } else if (!strcmp(c, "sel")) {
        if (n < 0 || n >= g_nrows) {
            control_fail(r, "no row %d", n);
            return;
        }
        g_cursor = n;
        char desc[160];
        text_of(entry_of(rd32(row_at(n) + 0x50))->desc, desc, sizeof desc);
        control_field_str(r, "desc", desc);
    } else if (!strcmp(c, "a") || !strcmp(c, "left") || !strcmp(c, "right")) {
        if (g_cursor >= g_nrows || g_closing) {
            control_fail(r, "no row");
            return;
        }
        if (!strcmp(c, "a")) control_field_str(r, "result", press(g_cursor));
        else step(g_cursor, !strcmp(c, "left") ? -1 : 1);
        if (!g_closing) control_field_str(r, "value", g_row_value[g_cursor]);
    } else if (!strcmp(c, "y")) {  // restore default settings: every row reset, read again and redrawn
        for (int i = 0; i < g_nrows; i++)
            if (!rt_opt_reset(row_at(i))) g_game_value[rd32(row_at(i) + 0x50) % 14] = 0;
        for (int i = 0; i < g_nrows; i++) {
            if (!rt_opt_read(row_at(i))) game_read(row_at(i));
            redraw(i);
        }
    } else if (!strcmp(c, "b")) {
        rt_opt_back();
        close_screen();
    } else if (!strcmp(c, "frame")) {
        int redraws = 0, opens = g_opens;
        for (int i = 0; i < (n > 0 ? n : 1); i++) redraws += frame();
        control_field_int(r, "redraws", redraws);
        control_field_int(r, "opened", g_opens - opens);
        control_field_int(r, "opener", g_opener);
    } else if (!strcmp(c, "click")) {
        HMODULE exe = GetModuleHandleW(L"savagewind.exe");
        void (*mouse)(int) = exe ? (void (*)(int))GetProcAddress(exe, "fake_sw_mouse") : NULL;
        if (!mouse) {
            control_fail(r, "no fake_sw_mouse");
            return;
        }
        mouse(n);
        control_field_int(r, "redraws", frame());
    } else if (!strcmp(c, "table")) {
        HMODULE exe = GetModuleHandleW(L"savagewind.exe");
        uint64_t (*sw)(void) = exe ? (uint64_t (*)(void))GetProcAddress(exe, "fake_sw_installed") : NULL;
        uint64_t ent = g_table.entries;
        control_field_str(r, "who", ent == ADDR(g_game) ? "game" : sw && ent == sw() ? "sw" : "mod");
        control_field_int(r, "count", g_table.count);
        control_field_int(r, "rows", g_table.rows);
        char order[256] = "";
        for (uint32_t i = 0; i < g_table.rows && i < 40; i++) {
            size_t len = strlen(order);
            snprintf(order + len, sizeof order - len, "%s%u", i ? "," : "", rd32(g_table.order + 4 * i));
        }
        control_field_str(r, "order", order);
    } else if (!strcmp(c, "iat")) {  // which import slots point somewhere else than savagewind.exe's functions
        HMODULE exe = GetModuleHandleW(L"savagewind.exe");
        void *now[] = {(void *)rt_opt_rows,  (void *)rt_opt_flag_type, (void *)rt_opt_read,
                       (void *)rt_opt_store, (void *)rt_opt_reset,     (void *)rt_opt_msg,
                       (void *)rt_opt_decide, (void *)rt_opt_tick,     (void *)rt_opt_back,
                       (void *)rt_opt_title_tick, (void *)rt_opt_pause_tick};
        int swapped = 0;
        for (size_t i = 0; i < sizeof now / sizeof *now; i++) swapped += now[i] != GetProcAddress(exe, k_imports[i]);
        control_field_int(r, "swapped", swapped);
        control_field_bool(r, "screen_swapped", (void *)rt_opt_screen != GetProcAddress(exe, "rt_opt_screen"));
    } else if (!strcmp(c, "text") && argc > 2) {
        char t[160];
        text_of((uint32_t)strtoul(argv[2], NULL, 10), t, sizeof t);
        control_field_str(r, "text", t);
        return;
    } else if (strcmp(c, "rows")) {
        control_fail(r, "g boot|reboot|open title|pause|rows|sel <i>|a|left|right|y|b|frame [n]|click <i>|table|iat"
                        "|text <id>");
        return;
    }
    control_field_bool(r, "overflow", g_overflow);
    reply_rows(r);
}

// ------------------------------------------------------------------ the export modhost loads

static uint64_t (*g_tick_orig)(uint64_t);
static uint64_t tamper_tick(uint64_t screen) { return g_tick_orig(screen); }

static const uint64_t k_addrs[1] = {0x7000001000ull};
static void fake_fn(struct Cpu *c) { (void)c; }
static const GuestFn k_fns[1] = {fake_fn};
static const uint64_t k_count = 1;
static const GameLibModule k_modules[] = {{"main", 0x7000000000ull, 0x10000, "00", k_addrs, k_fns, &k_count}};
static const uint32_t k_nmodules = 1;

GAMELIB_EXPORT const GameLibTables *swgame_tables(void) {
    static char version[16] = "1.9.0";
    static GameLibTables t;
    wchar_t v[16];
    DWORD n = GetEnvironmentVariableW(L"FAKE_GAME_VERSION", v, 16);
    if (n && n < 16) WideCharToMultiByte(SW_CP_UTF8, 0, v, -1, version, sizeof version, NULL, NULL);
    g_sites[0] = (GameLibSite){"opt_table", ADDR(&g_table)};
    g_sites[1] = (GameLibSite){"screen_mgr", ADDR(&g_mgr)};
    g_sites[2] = (GameLibSite){"title_options_state", TITLE_STATE};
    g_sites[3] = (GameLibSite){"opt_screen_rows", SCREEN_ROWS};
    t = (GameLibTables){GAMELIB_ABI, "selftest-id", k_modules, &k_nmodules, version, g_sites, &g_nsites};
    HMODULE exe = GetModuleHandleW(L"savagewind.exe");
    typedef void (*Setup)(uint64_t, uint64_t, uint64_t, uint64_t, void (*)(uint64_t));
    Setup setup = exe ? (Setup)GetProcAddress(exe, "fake_sw_setup") : NULL;
    if (setup) setup(ADDR(&g_table), ADDR(&g_mgr), TITLE_STATE, SCREEN_ROWS, click);
    static const ControlCmd cmds[] = {{"g", "<what>", "The fake game's Options screen", cmd_g, 0}};
    control_register(cmds, 1);
    g_nsites = GetEnvironmentVariableW(L"FAKE_NO_SITE", v, 16) ? 3 : 4;
    if (GetEnvironmentVariableW(L"FAKE_BOOTED", v, 16)) boot();
    if (GetEnvironmentVariableW(L"FAKE_TAMPER", v, 16)) {  // (another mod got to the slot first)
        DWORD old, tmp;
        g_tick_orig = (uint64_t (*)(uint64_t))__imp_rt_opt_tick;
        VirtualProtect(&__imp_rt_opt_tick, 8, SW_PAGE_READWRITE, &old);
        __imp_rt_opt_tick = (void *)tamper_tick;
        VirtualProtect(&__imp_rt_opt_tick, 8, old, &tmp);
    }
    return &t;
}
