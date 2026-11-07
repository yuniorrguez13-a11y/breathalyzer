// opt_page_a.c - the Mods page through Savage Wind's own Options calls (path A+). Tested in the game (docs/mod-menu.md).
//
// Savage Wind 0.4.1-alpha.4 shows its settings as pages of the game's own Options screen: the game code (swgame.dll)
// calls 12 functions of savagewind.exe for every Options row (rt_opt_rows, rt_opt_read, rt_opt_msg...), through
// swgame.dll's import table. This file puts its own functions into 11 of those import slots (rt_opt_screen is left
// alone) and forwards everything that isn't one of its rows to Savage Wind. So the game draws the Mods page: its font,
// sounds, cursor, scrolling and description line.
//
//  - Two tables. Savage Wind points the game's option table (site opt_table: u32 count, +8 entries, +0x10 row count,
//    +0x18 row order) at its own vector of entries, and checks that it is there. The game sees our copy of that vector
//    with our entries after it while one of our pages, or Savage Wind's first page (with the Mods button), is up; on
//    Savage Wind's other pages its own table stays in place. Around every call that checks the table (rows, flag
//    type, read, store, reset, msg, decide), Savage Wind's table goes back in, and our copy is refreshed after.
//  - rt_opt_tick is forwarded with our table in place: Savage Wind's mouse code presses rows through the game, which
//    reads the table. The row it returns for a redraw is read again here (Savage Wind can't, with our table in).
//  - The mouse: Savage Wind's pointer code calls rt_opt_pointer_kind inside the exe, not through the import table, and
//    that answers "no row" for every row while our table is in. Its first 16 bytes are checked, then it is replaced by
//    a jump to pointer_kind() below, which does the same for both tables.
//  - A page change closes the screen (rt_opt_decide answers 1); the title menu's or the pause menu's update opens it
//    again (rt_opt_title_tick / rt_opt_pause_tick answer 1), as Savage Wind does for its own pages.
//  - The description line: the game writes it only when the cursor comes to a row. After a change of ours (a switch,
//    a move, Y) it is written again for the row the cursor is on, through the game's own function for that (Savage
//    Wind's later versions name it opt_two_help and call it the same way; 0.4.1-alpha.4's game code doesn't name it).
//    It is found once, by its first 8 instructions (Savage Wind's own pattern), when Options first opens. Without it
//    the line changes when the cursor moves.
//
// Only for Savage Wind 0.4.1-alpha.4 with BotW 1.9.0: every check below must pass, or nothing is changed and the log
// says why. All of this happens in swmod_init, before any game thread runs.
//
// Ported from Savage Wind Recomp 0.4.1-alpha.4 (runtime/ui/game_options.mm; MIT License, Copyright (c) 2026 Revivedx
// and the Savage Wind Recomp contributors; Copyright (c) 2026 Peiikai): the option entry and row layouts, how a text is
// handed to the game (put_text), the reopen logic of the title and pause menus, and rt_opt_pointer_kind's logic.
#include <stdlib.h>

#include "opt_page.h"

typedef struct {         // the game's option entry (0x30 bytes; game_options.mm struct Entry)
    uint64_t name_vtbl;  // sead::SafeString: the game data flag name
    uint64_t name;
    uint32_t kind;       // 0 ON/OFF, 1 choice, 2 button
    uint32_t desc, title, first_value, last_value, button_off, button_on, pad;
} Entry;
_Static_assert(sizeof(Entry) == 0x30, "Entry");

// Row (0x98 bytes): +0x00 screen, +0x50 entry index, +0x54 on/off, +0x58 value, +0x5c max, +0x60 min, +0x64 button
#define ROW_ENTRY 0x50
#define ROW_ON 0x54
#define ROW_VALUE 0x58
#define ROW_MAX 0x5c
#define ROW_MIN 0x60
#define ROW_BUTTON 0x64

#define GAME_ENTRIES 14         // the game's own entries; Savage Wind's first is its first page's first button
#define SCREEN_OPTIONS 0x34u
#define MENU_OPTIONS_ACTIVE 0x6au
#define SW_VERSION "0.4.1-alpha.4"
#define GAME_VERSION "1.9.0"
// The sites come from the game library's own site table (api->find_site), the one Savage Wind's Options code reads
// them from (g_site): opt_table, screen_mgr, title_options_state (a field of the title menu: its active button screen,
// 0x6a: Options) and opt_screen_rows (a field of the Options screen: its row count, +8 its rows). No address is
// written into this mod.

// Message ids: 90,000-99,999 are ours (Savage Wind's are 9000 + 256 * item + part: 36,647 at most with its 108
// items; 5-digit ids are known to work).
#define MSG_LO 90000u
#define MSG_HI 99999u
#define MSG_MODS 90000u        // the Mods button: its name, description and button text
#define MSG_MODS_DESC 90001u
#define MSG_OPEN 90002u
#define MSG_OFF 90003u
#define MSG_ON 90004u
#define MSG_FIXED 8u
#define MSG_VALUE 90100u       // + v: the Order page's values, "1st" to "64th"
#define MSG_ROW 91000u         // + 4 * row + part: our page's rows
enum { PART_TITLE, PART_DESC, PART_BUTTON };

// rt_opt_pointer_kind's first 16 bytes in 0.4.1-alpha.4 (checked on the installed release)
static const uint8_t k_pointer_kind[16] = {0x56, 0x48, 0x83, 0xec, 0x30, 0x48, 0x8b, 0x15,
                                           0x3c, 0xf6, 0x37, 0x01, 0xb8, 0xff, 0xff, 0xff};

static inline uint8_t rd8(uint64_t a) { return *(volatile uint8_t *)(uintptr_t)a; }
static inline uint32_t rd32(uint64_t a) { return *(volatile uint32_t *)(uintptr_t)a; }
static inline uint64_t rd64(uint64_t a) { return *(volatile uint64_t *)(uintptr_t)a; }
static inline void wr8(uint64_t a, uint8_t v) { *(volatile uint8_t *)(uintptr_t)a = v; }
static inline void wr32(uint64_t a, uint32_t v) { *(volatile uint32_t *)(uintptr_t)a = v; }
static inline void wr64(uint64_t a, uint64_t v) { *(volatile uint64_t *)(uintptr_t)a = v; }
#define ADDR(p) ((uint64_t)(uintptr_t)(p))

// ------------------------------------------------------------------ Savage Wind's functions and our slots

enum { S_ROWS, S_FLAG_TYPE, S_READ, S_STORE, S_RESET, S_MSG, S_DECIDE, S_TICK, S_BACK, S_TITLE_TICK, S_PAUSE_TICK,
       S_COUNT };
static const char *const k_names[S_COUNT] = {
    "rt_opt_rows", "rt_opt_flag_type", "rt_opt_read", "rt_opt_store", "rt_opt_reset", "rt_opt_msg",
    "rt_opt_decide", "rt_opt_tick", "rt_opt_back", "rt_opt_title_tick", "rt_opt_pause_tick"};

static struct {  // Savage Wind's own functions (what the slots held)
    void (*rows)(void);
    int (*flag_type)(uint64_t entry);
    int (*read)(uint64_t row);
    int (*store)(uint64_t row);
    int (*reset)(uint64_t row);
    int (*msg)(uint32_t id, uint64_t out);
    int (*decide)(uint64_t row);
    uint64_t (*tick)(uint64_t screen);
    void (*back)(void);
    int (*title_tick)(uint64_t menu);
    int (*pause_tick)(void);
} sw;
static bool (*sw_hold)(void);  // game_options_input_hold: a key capture runs (no input to the page)

static uint64_t T;            // the option table (site opt_table)
static uint64_t g_mgr;        // site screen_mgr: ScreenMgr * (+0x28 screen count, +0x30 screens by id)
static uint64_t g_title_off;  // site title_options_state
static uint64_t g_rows_off;   // site opt_screen_rows
static uint64_t g_help;       // the game's function that writes a row's description to the line (x0: the row), or 0
#define ROWS_OFF_KNOWN 0x36b0u  // opt_screen_rows in BotW 1.9.0: then the screen's row under the cursor is at +0x18
#define OPT_COUNT (T)
#define OPT_ENTRIES (T + 8)
#define OPT_ROWS (T + 0x10)
#define OPT_ORDER (T + 0x18)

// ------------------------------------------------------------------ the tables

static uint64_t g_sw_ent;      // Savage Wind's entries (its vector) and how many
static uint32_t g_sw_count;
static Entry *g_my;            // our copy: Savage Wind's entries, the Mods button, then our page's rows
static uint32_t g_my_cap, g_my_count;
static uint32_t g_order[192];  // the row order the game sees (Savage Wind's first page plus Mods, or our page)
static char g_names[1 + MM_PAGE_ROWS][16];  // our entries' flag names (never a real game data flag)
static PageRow g_rows[MM_PAGE_ROWS];        // our page's rows (row k is entry g_sw_count + 1 + k)
static int g_nrows;
static int g_shown = PG_NONE;  // the page up now (PG_NONE: one of Savage Wind's)
static int g_due = PG_NONE;    // the page the next opening shows
static bool g_reopen;          // a page change of ours closed the screen: open it again once it has gone
static bool g_off;             // turned off while running (the log says why): everything goes to Savage Wind
static uint64_t g_dirty[MM_PAGE_ROWS + 8];  // our rows to redraw, one per frame
static int g_ndirty;

enum { NOT_MINE = -1, MODS_BUTTON = -2 };

static bool mine(void) { return g_my && rd64(OPT_ENTRIES) == ADDR(g_my); }

// Savage Wind's table in place for a call of its own, then ours again (with its changes copied in).
typedef struct {
    bool swapped;
    uint32_t count;
} Swap;

static Swap to_sw(void) {
    Swap s = {false, 0};
    if (mine()) {
        s.swapped = true;
        s.count = rd32(OPT_COUNT);
        wr64(OPT_ENTRIES, g_sw_ent);
        wr32(OPT_COUNT, g_sw_count);
    }
    return s;
}

static void from_sw(Swap s) {
    if (!s.swapped) return;
    if (rd64(OPT_ENTRIES) == g_sw_ent)  // (Savage Wind changes its entries: the choices' last value)
        memcpy(g_my, (const void *)(uintptr_t)g_sw_ent, (size_t)g_sw_count * sizeof(Entry));
    wr64(OPT_ENTRIES, ADDR(g_my));
    wr32(OPT_COUNT, s.count);
}

#define FWD(type, call)          \
    do {                         \
        Swap s_ = to_sw();       \
        type r_ = call;          \
        from_sw(s_);             \
        return r_;               \
    } while (0)

// NOT_MINE, MODS_BUTTON, or the index of one of our page's rows
static int my_row(uint64_t row) {
    if (!row || !mine()) return NOT_MINE;
    uint32_t idx = rd32(row + ROW_ENTRY);
    if (idx < g_sw_count || idx >= g_my_count) return NOT_MINE;
    if (idx == g_sw_count) return MODS_BUTTON;
    int k = (int)(idx - g_sw_count - 1);
    return k < g_nrows ? k : NOT_MINE;
}

static bool readable(uint64_t a, size_t n) {
    for (uint64_t p = a, end = a + n; p < end;) {
        SwMemInfo mi;
        if (!VirtualQuery((const void *)(uintptr_t)p, &mi, sizeof mi) || mi.state != SW_MEM_COMMIT ||
            (mi.protect & 0x101) || !(mi.protect & 0xee))  // (no access or a guard page; else some read right)
            return false;
        uint64_t next = ADDR(mi.base) + mi.region_size;
        if (next <= p) return false;
        p = next;
    }
    return true;
}

static uint64_t options_screen(void) {
    uint64_t mgr = rd64(g_mgr);
    if (!mgr) return 0;
    uint32_t n = rd32(mgr + 0x28);
    uint64_t screens = rd64(mgr + 0x30);
    return screens && n > SCREEN_OPTIONS ? rd64(screens + 8 * SCREEN_OPTIONS) : 0;
}

static bool on_screen(uint64_t screen, uint64_t row) {
    if (!screen) return false;
    uint32_t n = rd32(screen + g_rows_off);
    uint64_t rows = rd64(screen + g_rows_off + 8);
    for (uint32_t i = 0; rows && i < n && i < 128; i++)
        if (rd64(rows + 8 * i) == row) return true;
    return false;
}

static void mark_rows(uint64_t screen, uint64_t except) {  // every row of ours on the screen, for a redraw
    if (!screen) return;
    uint32_t n = rd32(screen + g_rows_off);
    uint64_t rows = rd64(screen + g_rows_off + 8);
    for (uint32_t i = 0; rows && i < n && i < 128; i++) {
        uint64_t r = rd64(rows + 8 * i);
        bool dup = false;
        for (int k = 0; k < g_ndirty; k++) dup |= g_dirty[k] == r;
        if (r && r != except && !dup && my_row(r) >= 0 && g_ndirty < (int)(sizeof g_dirty / sizeof *g_dirty))
            g_dirty[g_ndirty++] = r;
    }
}

// The row under the cursor (as Savage Wind's mouse code reads it: only with the known screen layout), or 0
static uint64_t cursor_row(uint64_t screen) {
    if (!screen || g_rows_off != ROWS_OFF_KNOWN) return 0;
    uint64_t row = rd64(screen + g_rows_off + 0x18);
    return on_screen(screen, row) ? row : 0;
}

// Write the description line again for one of our rows (the cursor's), after a change: on the game's thread only, as
// a call of the game's own function (its registers kept as they were around it)
static void show_desc(uint64_t row) {
    if (!g_help || !row || my_row(row) == NOT_MINE || !SWMOD_HAS(g_api, current_cpu)) return;
    struct Cpu *c = g_api->current_cpu();
    if (!c) return;
    SwModRegs *r = SWMOD_REGS(c);
    uint64_t x[32], sp = r->sp, pc = r->pc, nzcv = r->nzcv;
    SwModVReg v[32];
    memcpy(x, r->x, sizeof x);
    memcpy(v, r->v, sizeof v);
    r->x[0] = row, r->x[1] = 0, r->x[2] = 0;  // (as Savage Wind calls it)
    g_api->guest_call(c, g_help);
    memcpy(r->x, x, sizeof x);
    memcpy(r->v, v, sizeof v);
    r->sp = sp, r->pc = pc, r->nzcv = nzcv;
}

// The description function's first 8 instructions (Savage Wind's tools/game_sites.py, opt_two_help): word, mask
static const uint32_t k_help[8][2] = {{0xd100c3ffu, ~0u},        {0xa9017bfdu, ~0u},        {0xf90013f3u, ~0u},
                                      {0x910043fdu, ~0u},        {0xaa0003f3u, ~0u},        {0x94000000u, 0xfc000000u},
                                      {0x90000009u, 0x9f00001fu}, {0x91000129u, 0xffc003ffu}};
#define MAIN_START 0x7000000000ull  // (swmod.h: the game's main module starts there; its data at the site main_data)

static uint64_t site(const char *name);

// Once, on the game's thread (in swmod_init the game's code isn't in memory yet): the one place in main's code that
// matches, if the dispatcher knows a function there
static void find_help(void) {
    static bool done;
    if (done) return;
    done = true;
    uint64_t end = site("main_data"), found = 0;
    int n = 0;
    if (end > MAIN_START + 64 && end - MAIN_START < (1ull << 30) && readable(MAIN_START, end - MAIN_START)) {
        const uint32_t *w = (const uint32_t *)(uintptr_t)MAIN_START, *last = (const uint32_t *)(uintptr_t)end - 8;
        for (; w <= last; w++) {
            if (*w != k_help[0][0]) continue;
            int i = 1;
            while (i < 8 && (w[i] & k_help[i][1]) == k_help[i][0]) i++;
            if (i == 8) found = ADDR(w), n++;
        }
    }
    if (n == 1 && SWMOD_HAS(g_api, dispatch_lookup) && g_api->dispatch_lookup && g_api->dispatch_lookup(found))
        g_help = found;
    if (g_help) g_api->log("modmenu: the description line follows each change (the game's function at %#llx)",
                           (unsigned long long)g_help);
    else g_api->log("modmenu: the description line changes when the cursor moves (%d places match its function)", n);
}

// Savage Wind installed its table (and nothing else did): its entries after the game's 14, the same flag-name type,
// and its texts below ours.
static bool sw_table_ok(uint64_t ent, uint32_t count) {
    if (!ent || count <= GAME_ENTRIES || count > 4096 || !readable(ent, (size_t)count * sizeof(Entry))) return false;
    const Entry *e = (const Entry *)(uintptr_t)ent;
    if (e[GAME_ENTRIES].name_vtbl != e[0].name_vtbl) return false;
    for (uint32_t i = GAME_ENTRIES; i < count; i++) {
        uint32_t ids[] = {e[i].desc, e[i].title, e[i].last_value, e[i].button_off, e[i].button_on};
        for (size_t k = 0; k < sizeof ids / sizeof *ids; k++)
            if (ids[k] >= MSG_LO && ids[k] <= MSG_HI) {
                g_off = true;
                g_api->log("modmenu: Savage Wind's option texts use ids from %u on; the Mods page is off", MSG_LO);
                return false;
            }
    }
    return true;
}

static void set_entry(uint32_t idx, uint32_t kind, uint32_t title, uint32_t desc, uint32_t first, uint32_t last,
                      uint32_t button, int name) {
    Entry *e = &g_my[idx];
    memset(e, 0, sizeof *e);
    e->name_vtbl = g_my[0].name_vtbl;
    snprintf(g_names[name], sizeof g_names[name], "modmenu_%d", name);
    e->name = ADDR(g_names[name]);
    e->kind = kind, e->title = title, e->desc = desc, e->first_value = first, e->last_value = last;
    e->button_off = e->button_on = button;
}

// Our copy of Savage Wind's entries, and the Mods button after them. The old copy is never freed: a screen may still
// point at it.
static bool copy_entries(uint64_t ent, uint32_t count) {
    uint32_t need = count + 1 + MM_PAGE_ROWS;
    if (g_my_cap < need) {
        Entry *e = malloc((size_t)need * sizeof(Entry));
        if (!e) return false;
        g_my = e, g_my_cap = need;
    }
    memcpy(g_my, (const void *)(uintptr_t)ent, (size_t)count * sizeof(Entry));
    g_sw_ent = ent, g_sw_count = count, g_my_count = count + 1;
    set_entry(count, 2, MSG_MODS, MSG_MODS_DESC, 0, 0, MSG_OPEN, 0);
    return true;
}

// ------------------------------------------------------------------ texts

// A text for the game ({char16_t *, u32 length with the terminator} at out), kept alive per id (game_options.mm
// put_text). Plain ASCII only.
static void put_text(uint32_t id, const char *s, uint64_t out) {
    static uint16_t fixed[MSG_FIXED][128], values[MM_MAX_MODS][8], rows[MM_PAGE_ROWS][3][MM_TEXT_MAX + 8], other[8];
    uint16_t *t = other;
    size_t cap = sizeof other / sizeof *other;
    if (id - MSG_LO < MSG_FIXED) {
        t = fixed[id - MSG_LO], cap = 128;
    } else if (id - MSG_VALUE < MM_MAX_MODS) {
        t = values[id - MSG_VALUE], cap = 8;
    } else if (id - MSG_ROW < 4u * MM_PAGE_ROWS && (id - MSG_ROW) % 4 < 3) {
        t = rows[(id - MSG_ROW) / 4][(id - MSG_ROW) % 4], cap = MM_TEXT_MAX + 8;
    } else {
        s = "";
    }
    size_t n = 0;
    for (; s[n] && n + 1 < cap; n++) t[n] = (uint8_t)s[n] < 0x80 ? (uint8_t)s[n] : '?';
    t[n] = 0;
    wr64(out, ADDR(t));
    wr32(out + 8, (uint32_t)n + 1);
}

static void our_msg(uint32_t id, uint64_t out) {
    char s[MM_TEXT_MAX + 32] = "";
    if (id == MSG_MODS) {
        mm_copy(s, sizeof s, TXT_MODS);
    } else if (id == MSG_MODS_DESC) {
        mm_lock();
        mm_mods_button_desc(s, sizeof s);
        mm_unlock();
    } else if (id == MSG_OPEN) {
        mm_copy(s, sizeof s, TXT_OPEN);
    } else if (id == MSG_OFF || id == MSG_ON) {
        mm_copy(s, sizeof s, id == MSG_ON ? "ON" : "OFF");
    } else if (id - MSG_VALUE < MM_MAX_MODS) {
        mm_ordinal((int)(id - MSG_VALUE) + 1, s, sizeof s);
    } else if (id - MSG_ROW < 4u * MM_PAGE_ROWS) {
        int k = (int)(id - MSG_ROW) / 4, part = (int)(id - MSG_ROW) % 4;
        mm_lock();
        if (k < g_nrows) {
            const PageRow *r = &g_rows[k];
            if (part == PART_TITLE) mm_row_title(r, s, sizeof s);
            else if (part == PART_DESC) mm_row_desc(r, s, MM_TEXT_MAX);
            else if (part == PART_BUTTON) mm_row_button(r, s, sizeof s);
        }
        mm_unlock();
    }
    put_text(id, s, out);
}

// ------------------------------------------------------------------ our rows

static void fill_row(uint64_t row, int k) {
    if (k == MODS_BUTTON) {
        wr8(row + ROW_BUTTON, 0);
        return;
    }
    mm_lock();
    PageRow r = g_rows[k];
    int w = mm_row_widget(&r), v = mm_row_value(&r), max = mm_row_max(&r);
    mm_unlock();
    if (w == 2) {
        wr8(row + ROW_BUTTON, 0);
        return;
    }
    wr8(row + ROW_ON, v != 0);
    wr32(row + ROW_VALUE, (uint32_t)v);
    wr32(row + ROW_MAX, (uint32_t)max);
    wr32(row + ROW_MIN, 0);
}

static int open_page(int page) {
    if (sw_hold && sw_hold()) return 2;  // (a key capture runs: as Savage Wind's own page buttons)
    g_due = page;
    g_reopen = true;
    mm_flush_soon();
    return 1;
}

// ------------------------------------------------------------------ the wrappers (all on the game's thread)

// createRows: Savage Wind installs its table and picks its page; then the Mods button or our page.
static void w_rows(void) {
    if (mine()) {  // (Savage Wind must find its own table to know it's installed)
        wr64(OPT_ENTRIES, g_sw_ent);
        wr32(OPT_COUNT, g_sw_count);
    }
    sw.rows();
    find_help();
    int page = g_due;
    g_due = PG_NONE, g_reopen = false, g_ndirty = 0, g_shown = PG_NONE;
    uint64_t ent = rd64(OPT_ENTRIES);
    uint32_t count = rd32(OPT_COUNT);
    if (g_off || !sw_table_ok(ent, count) || !copy_entries(ent, count)) return;
    uint32_t rows = rd32(OPT_ROWS);
    uint64_t order = rd64(OPT_ORDER);
    if (page == PG_NONE) {
        // Savage Wind's first page starts with its first entry (Gameplay): Mods goes just above the last row (Quit)
        if (rows < 2 || rows + 1 > sizeof g_order / sizeof *g_order || !order || rd32(order) != GAME_ENTRIES) return;
        for (uint32_t r = 0; r < rows; r++) g_order[r] = rd32(order + 4 * r);
        g_order[rows] = g_order[rows - 1];
        g_order[rows - 1] = count;
        wr64(OPT_ORDER, ADDR(g_order));
        wr32(OPT_ROWS, rows + 1);
        wr64(OPT_ENTRIES, ADDR(g_my));
        wr32(OPT_COUNT, g_my_count);
        return;
    }
    mm_lock();
    g_nrows = mm_page(page, g_rows, MM_PAGE_ROWS);  // (never 0 rows: the game would build none)
    for (int k = 0; k < g_nrows; k++) {
        const PageRow *r = &g_rows[k];
        int w = mm_row_widget(r);
        uint32_t base = MSG_ROW + 4u * (uint32_t)k;
        if (w == 1) set_entry(count + 1 + k, 1, base, base + 1, MSG_VALUE, MSG_VALUE + (uint32_t)mm_row_max(r), 0, k + 1);
        else if (w == 0) set_entry(count + 1 + k, 0, base, base + 1, MSG_OFF, MSG_ON, 0, k + 1);
        else set_entry(count + 1 + k, 2, base, base + 1, 0, 0, base + PART_BUTTON, k + 1);
        g_order[k] = count + 1 + (uint32_t)k;
    }
    mm_unlock();
    g_my_count = count + 1 + (uint32_t)g_nrows;
    wr64(OPT_ORDER, ADDR(g_order));
    wr32(OPT_ROWS, (uint32_t)g_nrows);
    wr64(OPT_ENTRIES, ADDR(g_my));
    wr32(OPT_COUNT, g_my_count);
    g_shown = page;
}

// Row init: the flag type of an entry (0 bool, 1 s32); -1: the game's own
static int w_flag_type(uint64_t entry) {
    if (mine() && entry >= ADDR(g_my) && entry < ADDR(g_my + g_my_count)) {
        uint32_t idx = (uint32_t)((entry - ADDR(g_my)) / sizeof(Entry));
        if (idx >= g_sw_count) return g_my[idx].kind == 1 ? 1 : 0;
        FWD(int, sw.flag_type(g_sw_ent + (uint64_t)idx * sizeof(Entry)));
    }
    FWD(int, sw.flag_type(entry));
}

static int w_read(uint64_t row) {
    int k = my_row(row);
    if (k == NOT_MINE) FWD(int, sw.read(row));
    fill_row(row, k);
    return 1;
}

static int w_store(uint64_t row) {
    int k = my_row(row);
    if (k == NOT_MINE) FWD(int, sw.store(row));
    if (k == MODS_BUTTON) return 1;
    mm_lock();
    PageRow r = g_rows[k];
    int w = mm_row_widget(&r), max = mm_row_max(&r);
    bool others = false;
    if (w == 0) {
        others = mm_row_set(&r, (rd32(row + ROW_ON) & 0xff) != 0);
    } else if (w == 1) {
        int v = (int)rd32(row + ROW_VALUE);
        others = mm_row_set(&r, v < 0 ? 0 : v > max ? max : v);
    }
    mm_unlock();
    if (others) mark_rows(rd64(row), row);  // (a move changes every position on the Order page)
    show_desc(row);                          // (the row's line says what changes: "Turns off once you use changes.")
    return 1;
}

// Y (restore default settings): on our pages, every mod back to how it is now
static int w_reset(uint64_t row) {
    int k = my_row(row);
    if (k == NOT_MINE) FWD(int, sw.reset(row));
    if (k == MODS_BUTTON) return 1;
    mm_lock();
    bool any = mm_undo();
    mm_unlock();
    if (any) {
        mark_rows(rd64(row), 0);  // (all of them: whether the game redraws a reset row itself isn't known)
        mm_flush_soon();
    }
    fill_row(row, k);
    if (any) show_desc(cursor_row(rd64(row)));
    return 1;
}

static int w_msg(uint32_t id, uint64_t out) {
    if (!g_off && id >= MSG_LO && id <= MSG_HI) {
        our_msg(id, out);
        return 1;
    }
    FWD(int, sw.msg(id, out));
}

// A on a row: 0 the game's own handling (ON/OFF flips, then store), 1 close the screen (a page change), 2 done
static int w_decide(uint64_t row) {
    int k = my_row(row);
    if (k == NOT_MINE) FWD(int, sw.decide(row));
    if (k == MODS_BUTTON) return open_page(PG_MODS);
    mm_lock();
    int kind = g_rows[k].kind;
    mm_unlock();
    if (kind == RK_ORDER_BUTTON) return open_page(PG_ORDER);
    return kind == RK_MOD || kind == RK_ORDER ? 0 : 2;
}

// The screen's update: Savage Wind's first (with our table in place); then one of our rows to redraw, if any
static uint64_t w_tick(uint64_t screen) {
    uint64_t r = sw.tick(screen);
    if (r == 1) return 1;  // (the quit question is up: the screen's update is skipped)
    if (r) {
        int k = my_row(r);
        if (k != NOT_MINE) {
            fill_row(r, k);
        } else if (mine()) {  // (Savage Wind reads its row again before the redraw; with our table in it can't)
            uint32_t idx = rd32(r + ROW_ENTRY);
            const Entry *e = (const Entry *)(uintptr_t)g_sw_ent;
            if (idx >= GAME_ENTRIES && idx < g_sw_count && e[idx].kind != 2) {
                Swap s = to_sw();
                sw.read(r);
                from_sw(s);
            }
        }
        return r;
    }
    while (g_ndirty > 0) {
        uint64_t d = g_dirty[--g_ndirty];
        if (!on_screen(screen, d)) continue;  // (first: a row of a closed screen may be freed, so it isn't read)
        int k = my_row(d);
        if (k < 0) continue;
        fill_row(d, k);
        return d;
    }
    return 0;
}

// B: Savage Wind cancels a key capture; from our pages, back one page
static void w_back(void) {
    sw.back();
    if (g_shown == PG_MODS) g_due = PG_NONE, g_reopen = true;   // (Savage Wind's first page)
    else if (g_shown == PG_ORDER) g_due = PG_MODS, g_reopen = true;
    mm_flush_soon();
}

// The title menu's update: 1 = open the Options screen again now (it has gone, and a page of ours is due)
static int w_title_tick(uint64_t menu) {
    int r = sw.title_tick(menu);
    if (r || !menu) return r;
    if (rd32(menu + g_title_off) != MENU_OPTIONS_ACTIVE) {  // (its Options button isn't active: forget the reopen)
        g_reopen = false, g_due = PG_NONE;
        return 0;
    }
    if (!g_reopen || options_screen()) return 0;
    g_reopen = false;
    return 1;
}

// The pause System page's Options state: 0 as usual, 2 wait (the screen is closing), 1 open it again
static int w_pause_tick(void) {
    int r = sw.pause_tick();
    if (r || !g_reopen) return r;
    if (options_screen()) return 2;
    g_reopen = false;
    return 1;
}

// In place of Savage Wind's rt_opt_pointer_kind (its mouse code): the row kind under the pointer, or -1. The same as
// Savage Wind's, for its own table and for ours.
static int pointer_kind(uint64_t row) {
    if (!row || (sw_hold && sw_hold())) return -1;
    uint64_t ent = rd64(OPT_ENTRIES);
    uint32_t idx = rd32(row + ROW_ENTRY);
    if (g_my && ent == ADDR(g_my)) return idx < g_my_count ? (int)g_my[idx].kind : -1;
    if (g_sw_ent && ent == g_sw_ent) return idx < g_sw_count ? (int)((const Entry *)(uintptr_t)g_sw_ent)[idx].kind : -1;
    return -1;
}

static void *const k_ours[S_COUNT] = {(void *)w_rows,   (void *)w_flag_type, (void *)w_read,       (void *)w_store,
                                      (void *)w_reset,  (void *)w_msg,       (void *)w_decide,     (void *)w_tick,
                                      (void *)w_back,   (void *)w_title_tick, (void *)w_pause_tick};

// ------------------------------------------------------------------ finding things (before any game thread runs)

static const uint8_t *nt_headers(HMODULE m) {
    const uint8_t *b = (const uint8_t *)m;
    if (!b || b[0] != 'M' || b[1] != 'Z') return NULL;
    uint32_t pe = *(const uint32_t *)(b + 0x3c);
    if (pe > 0x1000 || memcmp(b + pe, "PE\0\0", 4) || *(const uint16_t *)(b + pe + 24) != 0x20b) return NULL;
    return b + pe;
}

static bool same_ci(const char *a, const char *b) {
    for (;; a++, b++) {
        char x = *a >= 'A' && *a <= 'Z' ? *a + 32 : *a, y = *b >= 'A' && *b <= 'Z' ? *b + 32 : *b;
        if (x != y) return false;
        if (!x) return true;
    }
}

// The import slots of savagewind.exe's rt_opt_* functions in the game library (found by their names)
static bool find_slots(HMODULE lib, uint64_t **slots, char *why, size_t cap) {
    const uint8_t *b = (const uint8_t *)lib, *nt = nt_headers(lib);
    if (!nt) return snprintf(why, cap, "the game code library isn't a 64-bit Windows DLL"), false;
    uint32_t ndirs = *(const uint32_t *)(nt + 24 + 108), imp = ndirs > 1 ? *(const uint32_t *)(nt + 24 + 120) : 0;
    bool any = false;
    for (const uint32_t *d = imp ? (const uint32_t *)(b + imp) : NULL; d && d[3]; d += 5) {  // (IMAGE_IMPORT_DESCRIPTOR)
        if (!same_ci((const char *)(b + d[3]), "savagewind.exe")) continue;
        any = true;
        if (!d[0]) return snprintf(why, cap, "the game code library has no import names"), false;
        const uint64_t *names = (const uint64_t *)(b + d[0]);
        uint64_t *iat = (uint64_t *)(uintptr_t)(b + d[4]);
        for (size_t i = 0; names[i]; i++) {
            if (names[i] >> 63) continue;  // (by ordinal)
            const char *name = (const char *)(b + (uint32_t)names[i] + 2);
            for (int k = 0; k < S_COUNT; k++)
                if (!strcmp(name, k_names[k])) slots[k] = &iat[i];
        }
    }
    if (!any) return snprintf(why, cap, "the game code library imports nothing from savagewind.exe"), false;
    for (int k = 0; k < S_COUNT; k++)
        if (!slots[k]) return snprintf(why, cap, "the game code library doesn't import %s", k_names[k]), false;
    return true;
}

// The exe's read-only data holds "<text>\0" (Savage Wind's version). Its start can only be told from a '.' before it:
// the real 0.4.1-alpha.4 packs its texts without a 0 between them, and its version follows a commit id
// ("...dda70.4.1-alpha.4\0"), so a digit before it is normal. The import slots and rt_opt_pointer_kind's bytes, checked
// next, pin the build.
static bool exe_has_text(HMODULE exe, const char *text) {
    const uint8_t *b = (const uint8_t *)exe, *nt = nt_headers(exe);
    if (!nt) return false;
    size_t n = strlen(text) + 1;
    uint16_t nsec = *(const uint16_t *)(nt + 6), opt = *(const uint16_t *)(nt + 20);
    for (uint16_t i = 0; i < nsec; i++) {
        const uint8_t *s = nt + 24 + opt + 40u * i;
        uint32_t size = *(const uint32_t *)(s + 8), va = *(const uint32_t *)(s + 12), flags = *(const uint32_t *)(s + 36);
        if (!(flags & 0x40) || (flags & 0x20000020) || size < n) continue;  // (initialized data, not code)
        const uint8_t *p = b + va, *end = p + size - n;
        for (; p <= end; p++) {
            p = memchr(p, text[0], (size_t)(end - p) + 1);
            if (!p) break;
            bool start = p == b + va || p[-1] != '.';
            if (start && !memcmp(p, text, n)) return true;
        }
    }
    return false;
}

static HMODULE game_library(void) {
    wchar_t given[1024], full[1024];
    DWORD n = GetEnvironmentVariableW(L"SWMOD_REAL_GAME_LIB", given, 1024);
    if (n && n < 1024) {
        DWORD m = GetFullPathNameW(given, 1024, full, NULL);
        if (m && m < 1024) {
            HMODULE h = GetModuleHandleW(full);
            if (h) return h;
        }
    }
    return GetModuleHandleW(L"swgame.dll");
}

static uint64_t site(const char *name) { return g_api->find_site ? g_api->find_site(name) : 0; }

bool page_a_game_is_up(void) {
    uint64_t t = site("opt_table");
    return t && readable(t, 4) && rd32(t) != 0;
}

bool page_a_start(char *why, size_t cap) {
    if (!g_api->game_version || strcmp(g_api->game_version, GAME_VERSION))
        return snprintf(why, cap, "the game code is for BotW %s; the Mods page needs %s",
                        g_api->game_version ? g_api->game_version : "?", GAME_VERSION), false;
    HMODULE exe = GetModuleHandleW(L"savagewind.exe");
    if (!exe) return snprintf(why, cap, "can't find savagewind.exe in this process"), false;
    if (!exe_has_text(exe, SW_VERSION))
        return snprintf(why, cap, "this Savage Wind isn't %s (the only one the Mods page knows)", SW_VERSION), false;
    HMODULE lib = game_library();
    if (!lib) return snprintf(why, cap, "can't find the game code library (swgame.dll) in this process"), false;
    uint64_t *slots[S_COUNT] = {0};
    if (!find_slots(lib, slots, why, cap)) return false;
    void *orig[S_COUNT];
    for (int k = 0; k < S_COUNT; k++) {
        orig[k] = GetProcAddress(exe, k_names[k]);
        if (!orig[k] || *slots[k] != ADDR(orig[k]))
            return snprintf(why, cap, "%s was already changed by something else", k_names[k]), false;
    }
    uint8_t *pk = GetProcAddress(exe, "rt_opt_pointer_kind");
    if (!pk || memcmp(pk, k_pointer_kind, sizeof k_pointer_kind))
        return snprintf(why, cap, "rt_opt_pointer_kind isn't the one of %s", SW_VERSION), false;
    sw_hold = (bool (*)(void))GetProcAddress(exe, "game_options_input_hold");
    if (!sw_hold) return snprintf(why, cap, "savagewind.exe has no game_options_input_hold"), false;
    static const char *const sites[] = {"opt_table", "screen_mgr", "title_options_state", "opt_screen_rows"};
    uint64_t *where[] = {&T, &g_mgr, &g_title_off, &g_rows_off};
    for (int k = 0; k < 4; k++)
        if (!(*where[k] = site(sites[k])))
            return snprintf(why, cap, "the game code doesn't name the site %s", sites[k]), false;

    // all checks passed: the slots, then the jump, each with its page unlocked and locked again
    uint64_t lo = ADDR(slots[0]), hi = lo;
    for (int k = 0; k < S_COUNT; k++) {
        lo = ADDR(slots[k]) < lo ? ADDR(slots[k]) : lo;
        hi = ADDR(slots[k]) + 8 > hi ? ADDR(slots[k]) + 8 : hi;
    }
    DWORD old_iat, old_code, tmp;
    if (!VirtualProtect((void *)(uintptr_t)lo, hi - lo, SW_PAGE_READWRITE, &old_iat))
        return snprintf(why, cap, "can't unlock the import slots (error %lu)", GetLastError()), false;
    if (!VirtualProtect(pk, 14, SW_PAGE_EXECUTE_READWRITE, &old_code)) {
        DWORD e = GetLastError();
        VirtualProtect((void *)(uintptr_t)lo, hi - lo, old_iat, &tmp);
        return snprintf(why, cap, "can't unlock rt_opt_pointer_kind (error %lu)", e), false;
    }
    void **fns = (void **)&sw;
    for (int k = 0; k < S_COUNT; k++) {
        fns[k] = orig[k];
        *slots[k] = ADDR(k_ours[k]);
    }
    uint8_t jump[14] = {0xff, 0x25, 0, 0, 0, 0};  // jmp [rip+0], then the address: no register changes
    uint64_t to = ADDR(pointer_kind);
    memcpy(jump + 6, &to, 8);
    memcpy(pk, jump, sizeof jump);
    VirtualProtect(pk, 14, old_code, &tmp);
    VirtualProtect((void *)(uintptr_t)lo, hi - lo, old_iat, &tmp);
    FlushInstructionCache(GetCurrentProcess(), pk, 14);
    return true;
}
