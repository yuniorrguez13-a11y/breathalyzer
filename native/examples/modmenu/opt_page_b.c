// opt_page_b.c - the Mods page through sw_options_add_page (path B): a function a future Savage Wind may export (the
// proposal: out of this repo, upstream-proposal.md; docs/mod-menu.md). No Savage Wind has it yet, so this path is
// unused: the mod looks for the function first and uses it when it is there. Then Savage Wind owns the rows, so the
// mouse and its own later changes keep working, and no import slot or code of Savage Wind is touched.
//
// The page model is the one opt_page_a.c uses (mm_pages.c). One page in Savage Wind's list: Mods. A button that opens
// another page (Change order, More, Mod settings and info, a mod's page) asks for the page again, which then lists
// that page's rows once. Texts are plain: the symbols the menu font lacks become look-alikes (mm_plain). Key rows are
// shown, but set on the PC (this path has no key capture).
#include "opt_page.h"

// The proposed runtime/ui/game_options.h (sizes first, so either side can grow them later)
typedef struct {
    uint32_t size;              // sizeof(SwOptionRow), set by Savage Wind
    int kind;                   // 0 ON/OFF, 1 choice, 2 button
    const char *title, *desc;   // UTF-8; Savage Wind copies them when it shows them
    int count;                  // choice: how many values
    int value;                  // ON/OFF and choice: the value now
    bool restart;               // "Applies after a restart." goes after the description
} SwOptionRow;

typedef struct {
    uint32_t size;                                        // sizeof(SwOptionsPage)
    const char *title, *desc;                             // its button on the first page
    int (*rows)(void *user);                              // how many rows: asked each time the page opens
    void (*row)(void *user, int i, SwOptionRow *out);     // row i
    const char *(*label)(void *user, int i, int value);   // a choice's value text; value -1: a button's text
    void (*set)(void *user, int i, int value);            // an ON/OFF or a choice changed
    int (*press)(void *user, int i);                      // A on a button: 0 done, 1 open the page again
    void *user;
} SwOptionsPage;

typedef bool (*AddPageFn)(const SwOptionsPage *page);

static PageRow g_rows[MM_PAGE_ROWS];
static int g_n;
static PageRef g_next = {PG_MODS, -1, -1};  // the page the next opening lists
static PageRef g_cur = {PG_MODS, -1, -1};   // the page listed now
static char g_title[MM_PAGE_ROWS][MM_NAME_MAX + 8], g_desc[MM_PAGE_ROWS][MM_TEXT_MAX + 8], g_label[32];

static int b_rows(void *user) {
    (void)user;
    mm_lock();
    g_n = mm_page(g_next, g_rows, MM_PAGE_ROWS);
    g_cur = g_next;
    g_next = (PageRef){PG_MODS, -1, -1};
    mm_unlock();
    return g_n;
}

static void b_row(void *user, int i, SwOptionRow *out) {
    (void)user;
    if (!out || i < 0 || i >= g_n || out->size < offsetof(SwOptionRow, restart) + sizeof out->restart) return;
    mm_lock();
    const PageRow *r = &g_rows[i];
    mm_row_title(r, g_title[i], sizeof g_title[i]);
    mm_row_desc(r, g_desc[i], MM_TEXT_MAX);
    mm_plain(g_title[i]);
    mm_plain(g_desc[i]);
    out->kind = mm_row_widget(r);
    out->title = g_title[i];
    out->desc = g_desc[i];
    out->count = out->kind == 1 ? mm_row_max(r) + 1 : 0;
    out->value = mm_row_value(r);
    out->restart = false;  // (the description says it: "... at your next start")
    mm_unlock();
}

static const char *b_label(void *user, int i, int value) {
    (void)user;
    mm_lock();
    if (value < 0 && i >= 0 && i < g_n) mm_row_button(&g_rows[i], g_label, sizeof g_label);
    else if (i >= 0 && i < g_n && mm_row_own_values(&g_rows[i])) mm_row_value_text(&g_rows[i], value, g_label, sizeof g_label);
    else mm_ordinal(value + 1, g_label, sizeof g_label);
    mm_plain(g_label);
    mm_unlock();
    return g_label;
}

static void b_set(void *user, int i, int value) {
    (void)user;
    mm_lock();
    if (i >= 0 && i < g_n) mm_row_set(&g_rows[i], value);
    mm_unlock();
}

static int b_press(void *user, int i) {
    (void)user;
    PageRef open = {PG_NONE, -1, -1};
    unsigned ch = 0;
    int act = ACT_DONE;
    mm_lock();
    if (i >= 0 && i < g_n) act = mm_row_press(&g_rows[i], &open, &ch);
    mm_unlock();
    if (ch) mm_flush_soon();
    if (act != ACT_OPEN && !(ch & CH_OTHERS)) return 0;
    g_next = act == ACT_OPEN ? open : g_cur;  // (other rows changed: list the page again)
    mm_flush_soon();
    return 1;
}

static AddPageFn add_page_fn(void) {
    HMODULE exe = GetModuleHandleW(L"savagewind.exe");
    return exe ? (AddPageFn)GetProcAddress(exe, "sw_options_add_page") : NULL;
}

bool page_b_available(void) { return add_page_fn() != NULL; }

bool page_b_start(char *why, size_t cap) {
    static const SwOptionsPage page = {
        sizeof(SwOptionsPage), TXT_MODS, "Turn mods on or off, and change their order.", b_rows, b_row, b_label,
        b_set, b_press, NULL};
    AddPageFn add = add_page_fn();
    if (!add) return snprintf(why, cap, "Savage Wind has no sw_options_add_page"), false;
    if (!add(&page)) return snprintf(why, cap, "Savage Wind refused the page (too late, or no room)"), false;
    return true;
}
