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
//    again (rt_opt_title_tick / rt_opt_pause_tick answer 1), as Savage Wind does for its own pages. The pages and
//    their rows come from mm_pages.c: Mods, Order, More, "Mod settings and info", a mod's page and its sub-pages. B
//    goes back one page.
//  - Choice rows (the Order page, Mod set, Font, a mod's sliders and choices) answer 0 to rt_opt_decide: the game sends
//    left and right through it too (the early game check, 2026-11-14). Only button rows answer 2.
//  - Locked rows (a setting's enable_if): changes are refused, the title and a choice's value are drawn in the game's
//    grey (its message colour tag 3), and the description line says "Locked: needs ...". Rows a show_if hides stay
//    on the page, grey, with "Not used now" (the page isn't closed and opened again yet: see docs/mod-menu.md).
//  - Key rows: A starts a 10 s key capture. While it runs the screen's update is skipped (rt_opt_tick answers 1), the
//    keyboard is read only while the game's window is in front, and Savage Wind's own keys (Esc, F1, F10, F11, `) are
//    refused: Savage Wind acts on them first. The description line counts down.
//  - The symbols the menu font lacks (# $ % @ ^ ` { | } ~) are drawn in the game's font tag 3, which has all of
//    ASCII, unless menu.txt says "symbols full" (font.py fix-symbols added them to the menu font).
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
#define MSG_ROW 91000u         // + 4 * row + part: our page's rows (64 rows: 91000-91255)
#define MSG_SLOT 92000u        // + 240 * slot + v: the values of a choice row with its own texts (32: 92000-99679)
enum { PART_TITLE, PART_DESC, PART_BUTTON };
_Static_assert(MSG_ROW + 4u * MM_PAGE_ROWS <= MSG_SLOT, "row ids");
_Static_assert(MSG_SLOT + (uint32_t)MM_SLOT_VALUES * MM_SLOTS - 1 <= MSG_HI, "slot ids");
_Static_assert(MSG_VALUE + MM_MAX_MODS <= MSG_ROW, "order ids");

#define CAPTURE_MS 10000u      // a key capture's time
#define CAPTURE_AFTER_MS 400u  // after the key goes up, before the page takes input again

// rt_opt_pointer_kind's first 16 bytes in 0.4.1-alpha.4 (checked on the installed release)
static const uint8_t k_pointer_kind[16] = {0x56, 0x48, 0x83, 0xec, 0x30, 0x48, 0x8b, 0x15,
                                           0x3c, 0xf6, 0x37, 0x01, 0xb8, 0xff, 0xff, 0xff};

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
static int g_slot[MM_PAGE_ROWS];            // a choice row's value block (MSG_SLOT), -1: none
static int g_widget[MM_PAGE_ROWS];          // the entry kind row k was built as (0 ON/OFF, 1 value, 2 button)
static int g_nrows;
#define REF_NONE ((PageRef){PG_NONE, -1, -1})
static PageRef g_shown = {PG_NONE, -1, -1};  // the page up now (PG_NONE: one of Savage Wind's)
static PageRef g_due = {PG_NONE, -1, -1};    // the page the next opening shows
static bool g_reopen;          // a page change of ours closed the screen: open it again once it has gone
static bool g_off;             // turned off while running (the log says why): everything goes to Savage Wind
static uint64_t g_dirty[MM_PAGE_ROWS + 8];  // our rows to redraw, one per frame
static int g_ndirty;

// The key capture (a key setting's A). Game thread only, except `inject` (the modmenu_key command: under mm_lock).
static struct {
    bool on;
    int k;                 // the row
    uint64_t row;          // its row in the screen
    uint64_t until;        // GetTickCount64 when it runs out
    int phase;             // 0 wait until no key is down (the key that started it), 1 listen, 2 wait for the key up
    int vk;                // phase 2: the key
    uint64_t done_at;      // phase 2: when it went up
    char name[16];         // phase 2: its name ("" clears)
    int left;              // the seconds the description line says
    char refused[16];      // the last of Savage Wind's keys pressed during it
    char inject[24];       // a key from modmenu_key ("wait": let it run out)
} g_cap;
static char g_cap_note[MM_TEXT_MAX];  // the last capture's result, for its row's description line
static int g_cap_note_k = -1;
static uint64_t g_cap_note_until;

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

static void mark_one(uint64_t r) {  // one row of ours, for a redraw: the next one (the rows go last in, first out)
    for (int k = 0; k < g_ndirty; k++)
        if (g_dirty[k] == r) {
            g_dirty[k] = g_dirty[g_ndirty - 1], g_dirty[g_ndirty - 1] = r;
            return;
        }
    if (r && my_row(r) >= 0 && g_ndirty < (int)(sizeof g_dirty / sizeof *g_dirty)) g_dirty[g_ndirty++] = r;
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
// put_text). Plain ASCII. grey: in the game's grey (message colour tag 3). Unless the menu font has every symbol
// (menu.txt "symbols full"), the symbols it lacks go in the game's font tag 3: 0x0e, group 0, tag 1 (font), 2 bytes,
// font 3; then 0xffff (the text's own font) after them.
#define TAG_LEN 5
#define TEXT_CAP (MM_TEXT_MAX + 8 + 2 * TAG_LEN + 32 * TAG_LEN)  // (room for 16 tagged symbols; more: look-alikes)
#define VALUE_CAP 40
static void put_text(uint32_t id, const char *s, uint64_t out, bool grey, bool symbols_full) {
    static uint16_t fixed[MSG_FIXED][128], values[MM_MAX_MODS][8], rows[MM_PAGE_ROWS][3][TEXT_CAP],
        slots[MM_SLOTS][MM_SLOT_VALUES][VALUE_CAP], other[8];
    static const uint16_t font_on[TAG_LEN] = {0x0e, 0, 1, 2, 3}, font_off[TAG_LEN] = {0x0e, 0, 1, 2, 0xffff},
                          grey_on[TAG_LEN] = {0x0e, 0, 3, 2, 3}, grey_off[TAG_LEN] = {0x0e, 0, 3, 2, 0xffff};
    uint16_t *t = other;
    size_t cap = sizeof other / sizeof *other;
    if (id - MSG_LO < MSG_FIXED) {
        t = fixed[id - MSG_LO], cap = 128;
    } else if (id - MSG_VALUE < MM_MAX_MODS) {
        t = values[id - MSG_VALUE], cap = 8;
    } else if (id - MSG_ROW < 4u * MM_PAGE_ROWS && (id - MSG_ROW) % 4 < 3) {
        t = rows[(id - MSG_ROW) / 4][(id - MSG_ROW) % 4], cap = TEXT_CAP;
    } else if (id - MSG_SLOT < (uint32_t)MM_SLOTS * MM_SLOT_VALUES) {
        t = slots[(id - MSG_SLOT) / MM_SLOT_VALUES][(id - MSG_SLOT) % MM_SLOT_VALUES], cap = VALUE_CAP;
    } else {
        s = "";
    }
    size_t n = 0;
#define PUT_TAG(tag)                                                       \
    do {                                                                   \
        if (n + TAG_LEN + 1 < cap) memcpy(t + n, (tag), sizeof(tag)), n += TAG_LEN; \
    } while (0)
    if (grey && cap > 2 * TAG_LEN + 4) PUT_TAG(grey_on);
    size_t room = grey ? cap - TAG_LEN - 1 : cap - 1;  // (the grey's end always fits)
    for (; *s && n < room; s++) {
        uint16_t c = (uint8_t)*s < 0x80 && (uint8_t)*s >= 0x20 ? (uint8_t)*s : '?';
        if (!symbols_full && mm_font_lacks((char)c)) {
            if (n + 2 * TAG_LEN + 1 < room) {  // (room for both tags: font_off needs n + TAG_LEN + 1 < cap after the letter)
                PUT_TAG(font_on);
                t[n++] = c;
                PUT_TAG(font_off);
            } else {
                char one[2] = {(char)c, 0};
                mm_plain(one);
                t[n++] = (uint8_t)one[0];
            }
            continue;
        }
        t[n++] = c;
    }
    if (grey && cap > 2 * TAG_LEN + 4) {
        memcpy(t + n, grey_off, sizeof grey_off);
        n += TAG_LEN;
    }
#undef PUT_TAG
    t[n] = 0;
    wr64(out, ADDR(t));
    wr32(out + 8, (uint32_t)n + 1);
}

// The row with this value block, or -1
static int row_of_slot(int slot) {
    for (int k = 0; k < g_nrows; k++)
        if (g_slot[k] == slot) return k;
    return -1;
}

// The description line while a key capture runs on row k, or after it: false when there is nothing to say
static bool capture_desc(int k, char *s, size_t cap) {
    if (g_cap.on && g_cap.k == k) {
        snprintf(s, cap, "Press a key now. %d s left. Backspace clears.", g_cap.left);
        if (g_cap.refused[0]) {
            char more[48];
            snprintf(more, sizeof more, " %s is Savage Wind's.", g_cap.refused);
            mm_cat(s, cap, more);
        }
        return true;
    }
    if (g_cap_note_k == k && g_cap_note[0] && GetTickCount64() < g_cap_note_until) {
        mm_copy(s, cap, g_cap_note);
        return true;
    }
    return false;
}

static void our_msg(uint32_t id, uint64_t out) {
    char s[MM_TEXT_MAX + 32] = "";
    bool grey = false, full = false;
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
            if (part == PART_TITLE) mm_row_title(r, s, sizeof s), grey = mm_row_locked(r);
            else if (part == PART_DESC && !capture_desc(k, s, MM_TEXT_MAX)) mm_row_desc(r, s, MM_TEXT_MAX);
            else if (part == PART_BUTTON) mm_row_button(r, s, sizeof s), grey = mm_row_locked(r);
        }
        full = g_mm.symbols_full;
        mm_unlock();
    } else if (id - MSG_SLOT < (uint32_t)MM_SLOTS * MM_SLOT_VALUES) {
        int slot = (int)(id - MSG_SLOT) / MM_SLOT_VALUES, v = (int)(id - MSG_SLOT) % MM_SLOT_VALUES;
        mm_lock();
        int k = row_of_slot(slot);
        if (k >= 0) mm_row_value_text(&g_rows[k], v, s, sizeof s), grey = mm_row_locked(&g_rows[k]);
        full = g_mm.symbols_full;
        mm_unlock();
    }
    put_text(id, s, out, grey, full);
}

// ------------------------------------------------------------------ our rows

static void fill_row(uint64_t row, int k) {
    if (k == MODS_BUTTON) {
        wr8(row + ROW_BUTTON, 0);
        return;
    }
    mm_lock();
    PageRow r = g_rows[k];
    int w = g_widget[k], v = mm_row_value(&r), max = mm_row_max(&r);  // (a value row past the value blocks is a
    mm_unlock();                                                       // button: no ON/VALUE/MAX in its row)
    if (w == 2) {
        wr8(row + ROW_BUTTON, 0);
        return;
    }
    wr8(row + ROW_ON, v != 0);
    wr32(row + ROW_VALUE, (uint32_t)v);
    wr32(row + ROW_MAX, (uint32_t)max);
    wr32(row + ROW_MIN, 0);
}

static int open_page(PageRef page) {
    if (sw_hold && sw_hold()) return 2;  // (a key capture runs: as Savage Wind's own page buttons)
    g_due = page;
    g_reopen = true;
    mm_flush_soon();
    return 1;
}

static const char *page_name(int page) {
    switch (page) {
    case PG_MODS: return "mods";
    case PG_ORDER: return "order";
    case PG_MORE: return "more";
    case PG_LIST: return "list";
    case PG_MOD: return "mod";
    case PG_SUB: return "sub";
    }
    return "";
}

// After a change on row k (CH_* bits): its row and, when others changed too, every row of ours is redrawn, and the
// description line says what changed. A refused change puts the row back as it was.
static void after_change(uint64_t row, int k, unsigned ch) {
    if (!ch) return;
    if (ch & CH_OTHERS) mark_rows(rd64(row), row);
    if (ch & CH_REFUSED) fill_row(row, k), mark_one(row);
    if (ch & (CH_THIS | CH_OTHERS)) mm_flush_soon();
    show_desc(row);
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
    PageRef page = g_due;
    g_due = REF_NONE, g_reopen = false, g_ndirty = 0, g_shown = REF_NONE;
    g_cap.on = false, g_cap_note_k = -1;
    uint64_t ent = rd64(OPT_ENTRIES);
    uint32_t count = rd32(OPT_COUNT);
    if (g_off || !sw_table_ok(ent, count) || !copy_entries(ent, count)) return;
    uint32_t rows = rd32(OPT_ROWS);
    uint64_t order = rd64(OPT_ORDER);
    if (page.page == PG_NONE) {
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
    int slots = 0;
    for (int k = 0; k < g_nrows; k++) {
        const PageRow *r = &g_rows[k];
        int w = mm_row_widget(r);
        uint32_t base = MSG_ROW + 4u * (uint32_t)k, max = (uint32_t)mm_row_max(r);
        g_slot[k] = -1;
        if (w == 1 && mm_row_own_values(r) && slots >= MM_SLOTS) w = 2;  // (no value block left: a button; A steps)
        g_widget[k] = w;
        if (w == 1 && mm_row_own_values(r)) {
            g_slot[k] = slots++;
            uint32_t first = MSG_SLOT + (uint32_t)MM_SLOT_VALUES * (uint32_t)g_slot[k];
            set_entry(count + 1 + k, 1, base, base + 1, first, first + max, 0, k + 1);
        } else if (w == 1) set_entry(count + 1 + k, 1, base, base + 1, MSG_VALUE, MSG_VALUE + max, 0, k + 1);
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
    int w = g_widget[k], max = mm_row_max(&r);
    unsigned ch = 0;
    if (w == 0) {
        ch = mm_row_set(&r, (rd32(row + ROW_ON) & 0xff) != 0);
    } else if (w == 1) {
        int v = (int)rd32(row + ROW_VALUE);
        ch = mm_row_set(&r, v < 0 ? 0 : v > max ? max : v);
    }
    mm_unlock();
    // (a move changes every position on the Order page; a setting can lock others. The row's line says what
    // changes: "Turns off at your next start.")
    after_change(row, k, ch);
    return 1;
}

// Y (restore default settings): on the Mods, Order and More pages every change for the next start is undone; on a
// mod's page its settings go back to their defaults
static int w_reset(uint64_t row) {
    int k = my_row(row);
    if (k == NOT_MINE) FWD(int, sw.reset(row));
    if (k == MODS_BUTTON) return 1;
    mm_lock();
    bool any = mm_page_reset(g_shown) != 0;
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

static void start_capture(uint64_t row, int k) {
    memset(&g_cap, 0, sizeof g_cap);
    g_cap.on = true, g_cap.k = k, g_cap.row = row;
    g_cap.until = GetTickCount64() + CAPTURE_MS;
    g_cap.left = (int)(CAPTURE_MS / 1000);
    g_cap_note_k = -1;
    show_desc(row);
}

// A on a row: 0 the game's own handling (ON/OFF flips, then store; a choice row's left and right come here too),
// 1 close the screen (a page change), 2 done
static int w_decide(uint64_t row) {
    int k = my_row(row);
    if (k == NOT_MINE) FWD(int, sw.decide(row));
    if (k == MODS_BUTTON) return open_page((PageRef){PG_MODS, -1, -1});
    if (g_cap.on) return 2;
    PageRef open = REF_NONE;
    unsigned ch = 0;
    mm_lock();
    PageRow r = g_rows[k];
    int act = mm_row_press(&r, &open, &ch);
    if (act == ACT_GAME && g_widget[k] == 2) {  // (a value row built as a button: the game has no left/right for
        int max = mm_row_max(&r);               // it, so A steps to the next value)
        ch = mm_row_set(&r, max > 0 ? (mm_row_value(&r) + 1) % (max + 1) : 0);
        act = ch & CH_REFUSED ? ACT_REFUSED : ACT_DONE;
    }
    mm_unlock();
    switch (act) {
    case ACT_GAME: return 0;
    case ACT_OPEN: return open_page(open);
    case ACT_KEY: start_capture(row, k); return 2;
    case ACT_REFUSED: show_desc(row); return 2;  // (a locked row: its line says why)
    }
    after_change(row, k, ch);
    return 2;
}

// ------------------------------------------------------------------ the key capture (game thread)

__declspec(dllimport) short WINAPI GetAsyncKeyState(int vk);
__declspec(dllimport) void *WINAPI GetForegroundWindow(void);
__declspec(dllimport) DWORD WINAPI GetWindowThreadProcessId(void *hwnd, DWORD *pid);
__declspec(dllimport) DWORD WINAPI GetCurrentProcessId(void);

// The keys a key setting can take (modhost's names: native/modhost/settings.c), and Savage Wind's own (refused: it
// acts on them first, and saves F10, F11 and its menu's changes to savagewind.ini)
typedef struct {
    int vk;
    const char *name;
} KeyName;
static const KeyName k_named[] = {{0x20, "Space"}, {0x09, "Tab"},    {0x0D, "Enter"}, {0x2D, "Insert"},
                                  {0x2E, "Delete"}, {0x24, "Home"},  {0x23, "End"},   {0x21, "PageUp"},
                                  {0x22, "PageDown"}, {0x25, "Left"}, {0x26, "Up"},   {0x27, "Right"},
                                  {0x28, "Down"},  {0x10, "Shift"},  {0x11, "Ctrl"},  {0x12, "Alt"}};
static const KeyName k_theirs[] = {{0x1B, "Esc"}, {0x70, "F1"}, {0x79, "F10"}, {0x7A, "F11"}, {0xC0, "`"}};
#define VK_BACKSPACE 0x08

static bool same_ci(const char *a, const char *b);

// A key's name; false: not a key a setting can take
static bool vk_name(int vk, char *out, size_t cap) {
    if ((vk >= 'A' && vk <= 'Z') || (vk >= '0' && vk <= '9')) return snprintf(out, cap, "%c", vk), true;
    if (vk >= 0x70 && vk <= 0x87) return snprintf(out, cap, "F%d", vk - 0x6F), true;
    if (vk >= 0x60 && vk <= 0x69) return snprintf(out, cap, "Num%d", vk - 0x60), true;
    for (size_t i = 0; i < sizeof k_named / sizeof *k_named; i++)
        if (k_named[i].vk == vk) return mm_copy(out, cap, k_named[i].name), true;
    return false;
}

static const char *theirs(int vk) {
    for (size_t i = 0; i < sizeof k_theirs / sizeof *k_theirs; i++)
        if (k_theirs[i].vk == vk) return k_theirs[i].name;
    return NULL;
}

static bool game_in_front(void) {
    void *w = GetForegroundWindow();
    DWORD pid = 0;
    if (!w) return false;
    GetWindowThreadProcessId(w, &pid);
    return pid == GetCurrentProcessId();
}

static bool key_down(int vk) { return (GetAsyncKeyState(vk) & 0x8000) != 0; }

// The first key down that counts (Savage Wind's and Backspace too), or 0
static int first_down(void) {
    for (int vk = 0x08; vk <= 0xC0; vk++) {
        char name[16];
        if ((vk == VK_BACKSPACE || theirs(vk) || vk_name(vk, name, sizeof name)) && key_down(vk)) return vk;
    }
    return 0;
}

static void capture_end(const char *note) {
    g_cap.on = false;
    mm_copy(g_cap_note, sizeof g_cap_note, note);
    g_cap_note_k = g_cap.k, g_cap_note_until = GetTickCount64() + 4000;
    mark_one(g_cap.row);
    show_desc(g_cap.row);
}

// One frame of the key capture: true while it runs (the screen's update is skipped, so the page takes no input)
static bool capture_tick(uint64_t screen) {
    if (!g_cap.on) return false;
    if (!on_screen(screen, g_cap.row) || my_row(g_cap.row) != g_cap.k) {
        g_cap.on = false;
        return false;
    }
    uint64_t now = GetTickCount64();
    char inject[24];
    mm_lock();
    mm_copy(inject, sizeof inject, g_cap.inject);
    g_cap.inject[0] = 0;
    mm_unlock();
    if (g_cap.phase == 2) {  // (a key was taken: wait for it to go up, then a little, so the page doesn't get it)
        if (g_cap.vk && key_down(g_cap.vk)) return true;
        if (!g_cap.done_at) g_cap.done_at = now;
        if (g_cap.vk && now - g_cap.done_at < CAPTURE_AFTER_MS) return true;
        mm_lock();
        PageRow r = g_rows[g_cap.k];
        unsigned ch = mm_row_key(&r, g_cap.name);
        mm_unlock();
        char note[MM_TEXT_MAX];
        if (ch & CH_REFUSED) mm_copy(note, sizeof note, "That key can't be used now. Nothing changed.");
        else if (g_cap.name[0]) snprintf(note, sizeof note, "Got %s.", g_cap.name);
        else mm_copy(note, sizeof note, "No key now.");
        if (ch & CH_OTHERS) mark_rows(screen, 0);
        if (ch & (CH_THIS | CH_OTHERS)) mm_flush_soon();
        capture_end(note);
        return true;
    }
    int vk = 0;
    const char *refused = NULL;
    char name[16] = "";
    bool got = false;
    if (inject[0]) {  // (modmenu_key: as if pressed, with no key to wait for)
        if (same_ci(inject, "wait")) {
            g_cap.until = now;
        } else if (same_ci(inject, "Backspace")) {
            got = true;
        } else {
            for (size_t i = 0; i < sizeof k_theirs / sizeof *k_theirs; i++)
                if (same_ci(inject, k_theirs[i].name)) refused = k_theirs[i].name;
            for (int v = 0x08; !refused && !got && v <= 0x87; v++)
                if (vk_name(v, name, sizeof name) && same_ci(inject, name)) got = true;
            if (!got) name[0] = 0;
        }
    } else if (game_in_front()) {  // (never a key typed into another window)
        int down = first_down();
        if (g_cap.phase == 0) {  // (the key that started it goes up first)
            if (!down) g_cap.phase = 1;
        } else if (down) {
            refused = theirs(down);
            if (!refused) {
                vk = down;
                got = down == VK_BACKSPACE || vk_name(down, name, sizeof name);
                if (down == VK_BACKSPACE) name[0] = 0;
            }
        }
    }
    if (refused) mm_copy(g_cap.refused, sizeof g_cap.refused, refused);
    if (got) {
        g_cap.phase = 2, g_cap.vk = vk, g_cap.done_at = 0;
        mm_copy(g_cap.name, sizeof g_cap.name, name);
        return true;
    }
    if (now >= g_cap.until) {
        capture_end("No key in 10 s: nothing changed.");
        return true;
    }
    int left = (int)((g_cap.until - now + 999) / 1000);
    if (left != g_cap.left || refused) {
        g_cap.left = left;
        show_desc(g_cap.row);
    }
    return true;
}


// The screen's update: Savage Wind's first (with our table in place); then one of our rows to redraw, if any
static uint64_t w_tick(uint64_t screen) {
    if (capture_tick(screen)) return 1;  // (a key capture runs: no input for the page, and none for Savage Wind's)
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

// B: Savage Wind cancels a key capture; from our pages, back one page (from the Mods page: Savage Wind's first page)
static void w_back(void) {
    sw.back();
    PageRef parent;
    PageRow from;
    mm_lock();
    bool up = g_shown.page != PG_NONE && mm_page_parent(g_shown, &parent, &from);
    mm_unlock();
    if (up) g_due = parent, g_reopen = true;
    mm_flush_soon();
}

// The title menu's update: 1 = open the Options screen again now (it has gone, and a page of ours is due)
static int w_title_tick(uint64_t menu) {
    int r = sw.title_tick(menu);
    if (r || !menu) return r;
    if (rd32(menu + g_title_off) != MENU_OPTIONS_ACTIVE) {  // (its Options button isn't active: forget the reopen)
        g_reopen = false, g_due = REF_NONE;
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
    if (!row || (sw_hold && sw_hold()) || g_cap.on) return -1;
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

// ------------------------------------------------------------------ for the commands (frame thread)

// (called locked: from the modmenu command)
void page_a_status(SwModReply *r) {
    PageRef p = g_shown;
    g_api->reply_str(r, "page", page_name(p.page));
    g_api->reply_str(r, "page_mod", (p.page == PG_MOD || p.page == PG_SUB) && p.mod >= 0 && p.mod < g_mm.n
                                        ? g_mm.mods[p.mod].id
                                        : "");
    g_api->reply_int(r, "page_rows", p.page == PG_NONE ? 0 : g_nrows);
    g_api->reply_bool(r, "capture", g_cap.on);
    if (g_cap.on) g_api->reply_int(r, "capture_left", g_cap.left);
}

bool page_a_inject_key(const char *name, char *why, size_t cap) {
    if (!g_cap.on) return snprintf(why, cap, "no key capture runs: press A on a key row first"), false;
    mm_lock();
    mm_copy(g_cap.inject, sizeof g_cap.inject, name);
    mm_unlock();
    return true;
}
