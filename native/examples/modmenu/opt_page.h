// opt_page.h - shared by the files of the Mod menu mod (docs/mod-menu.md):
//   modmenu.c     the mod list (menu.txt from tools/modmenu.py), the player's changes (pending.txt, written by a
//                 thread of its own), the commands, and swmod_init
//   mm_pages.c    what each page shows: the Mods and Order pages, More, "Mod settings and info", a mod's own page
//                 (its info and its settings, through modhost's settings_ui) and its sub-pages
//   opt_page_a.c  the pages through Savage Wind's own Options calls (path A+: 0.4.1-alpha.4 with BotW 1.9.0), with
//                 key capture and grey rows (show_if hides rows as grey ones; pictures: later, see docs/mod-menu.md)
//   opt_page_b.c  the Mods page through sw_options_add_page (path B: a Savage Wind that has it; none does yet)
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "win_min.h"  // (platform_min.h, and the Win32 calls this mod needs beyond it)
#include "swmod.h"

#define MM_MAX_MODS 64     // mods the game lists; menu.txt may have more ("More mods in Mod Manager")
#define MM_PAGE_MODS 30    // mod rows on one page. Savage Wind's longest page has about 20 rows: more is untested
#define MM_PAGE_ROWS 64    // rows on one page (message ids 91000 + 4 * row + part: 91000-91255)
#define MM_SLOTS 32        // choice and slider rows on one page with values of their own (92000 + 240 * slot + v)
#define MM_SLOT_VALUES 240
#define MM_ID_MAX 72       // ids up to 71 characters (modman allows 64)
#define MM_NAME_MAX 29     // 28 characters (the row's name box) and the terminator
#define MM_ABOUT_MAX 71
#define MM_EXTRA_MAX 61
#define MM_NOTE_MAX 81
#define MM_TEXT_MAX 81     // the description line: 80 characters (the game shows about 82, then cuts at a word)
#define MM_FIELDS 4        // "about" lines a mod keeps (version, authors, website, settings)
#define MM_FIELD_MAX 81
#define MM_PROFILES 16     // mod sets the Mod set row lists
#define MM_PROFILE_MAX 41  // 40 characters (modman's limit)
#define MM_FONTS 16
#define MM_FONT_KEY 80     // "game", "easy-read" or "mod:<id>"
#define MM_GRIDS 2         // picture atlases: [0] icons (panel and rows), [1] backgrounds

extern const SwModApi *g_api;

// ---- the mod list. Lock it around every use: the game thread (the page), the frame thread (commands) and the
// writer thread all use it.
enum { MM_LIST_OK, MM_LIST_MISSING, MM_LIST_DAMAGED };

typedef struct {
    char field[12];          // version, authors, url, settings
    char text[MM_FIELD_MAX];
} MmField;

typedef struct {
    char id[MM_ID_MAX];
    char name[MM_NAME_MAX];
    char about[MM_ABOUT_MAX];
    char extra[MM_EXTRA_MAX];
    bool on;        // on in the game now
    bool wanted;    // on at the next start
    bool hidden;    // the Mod menu mod itself: never listed
    bool rebuild;   // hooks or code patches: the next start can take 10-20 minutes
    int pos;        // its place now among the listed mods (0-based), -1 when hidden
    MmField fields[MM_FIELDS];
    int nfields;
    int files, wins, loses;  // its RomFS files in the last deploy: how many, used, replaced by a later mod (-1: none)
    char rival[MM_NAME_MAX]; // the mod that replaces most of them
    int icon, bg;            // its picture cells in the atlases (-1: none)
    int sm;                  // its index in modhost's settings_ui (-1: none; -2: not looked up yet)
} MmMod;

typedef struct {
    char key[MM_FONT_KEY];
    char label[MM_NAME_MAX];
} MmFont;

typedef struct {
    char tex[32];
    int w, h, cols, rows;
} MmGrid;

typedef struct {
    int list;                     // MM_LIST_*
    int n;                        // mods read (listed and hidden)
    MmMod mods[MM_MAX_MODS + 4];
    int nlisted;
    int order[MM_MAX_MODS];       // the listed mods in the wanted order (indices into mods)
    bool more;                    // menu.txt has more mods than the game lists
    bool stale;                   // config.json changed since menu.txt was written (Mod Manager made changes)
    char note[MM_NOTE_MAX];       // "Last change not used": why
    char changed[MM_TEXT_MAX];    // what is new since the last start ("" none)
    bool crashed;                 // the last start ended with an error
    char serial[24];
    bool use_modman;              // menu.txt "use modman": Play in Mod Manager uses the changes (else modmenu.py play)
    bool symbols_full;            // the deployed menu font has every ASCII symbol (font.py fix-symbols)
    char profiles[MM_PROFILES][MM_PROFILE_MAX];
    int nprofiles, profile_now, profile_want;   // -1: no mod sets listed
    MmFont fonts[MM_FONTS];
    int nfonts, font_now, font_want;            // -1: no font choices listed
    MmGrid grids[MM_GRIDS];
    int ngrids;
    int page_bg;                  // the Mods page picture the player picked (a background cell, -1: none)
    bool reopen_ok;               // the page can close and open again (show_if hides rows; else they go grey)
    uint32_t version;             // goes up with every change (the writer thread writes pending.txt)
} MmModel;

extern MmModel g_mm;
void mm_lock(void);
void mm_unlock(void);

// Changes (call them locked). m: an index into g_mm.mods.
void mm_set_wanted(int m, bool on);
void mm_move(int m, int pos);   // to wanted position pos (0-based, clamped)
void mm_set_profile(int p);     // the mod set used at the next start (an index into g_mm.profiles)
void mm_set_font(int f);
bool mm_undo(void);             // every mod back to how it is now; true when something changed
int mm_wanted_pos(int m);       // -1: hidden
int mm_changes(void);
void mm_flush_soon(void);       // write pending.txt now (not after the usual pause)
const SwModSettingsUi *mm_ui(void);   // modhost's settings (API 3), or NULL
int mm_settings_of(int m);      // mod m's index in mm_ui() (-1: none)

// ---- the pages, shared by both paths (call them locked)
enum { PG_NONE, PG_MODS, PG_ORDER, PG_MORE, PG_LIST, PG_MOD, PG_SUB };   // PG_NONE: one of Savage Wind's pages
typedef struct {
    int page;  // PG_*
    int mod;   // PG_MOD, PG_SUB: an index into g_mm.mods
    int sub;   // PG_SUB: the mod's [[pages]] index
} PageRef;

enum {
    RK_NOTE, RK_INFO, RK_MOD, RK_MORE, RK_ORDER_BUTTON, RK_HOWTO, RK_ORDER, RK_STALE,  // (v1)
    RK_LIST_BUTTON, RK_MORE_BUTTON,                                                   // the Mods page's new buttons
    RK_PROFILE, RK_FONT, RK_ALL_OFF, RK_ALL_ON, RK_CHANGED, RK_CONFLICTS,             // More
    RK_MODLINK,                                                                       // Mod settings and info
    RK_OFF_NOTE, RK_ABOUT, RK_SETTING, RK_SUBPAGE, RK_RESET, RK_EMPTY,                // a mod's pages
    RK_LEFT_OUT,  // a mod's page: settings past the row limit (arg: how many), said instead of left out quietly
};
enum { AB_WHAT, AB_FILES, AB_FIELD };   // RK_ABOUT's arg: AB_FIELD + i is the mod's fields[i]
typedef struct {
    int kind;  // RK_*
    int mod;   // an index into g_mm.mods, -1 none
    int arg;   // RK_SETTING: the setting's index in mm_ui(); RK_SUBPAGE: the page's; RK_ABOUT: AB_*
} PageRow;

// What a row's A does (mm_row_press)
enum { ACT_GAME, ACT_DONE, ACT_OPEN, ACT_KEY, ACT_REFUSED };
// What a change did (mm_row_set, mm_row_press): bits
#define CH_THIS 1u    // this row's value changed
#define CH_OTHERS 2u  // other rows' values may have changed too (redraw them all)
#define CH_REFUSED 4u // the row is locked: nothing changed

int mm_page(PageRef ref, PageRow *out, int cap);  // the rows of a page: always at least one
bool mm_page_parent(PageRef ref, PageRef *parent, PageRow *from);  // B: where it goes, and the row that opened ref
uint64_t mm_page_shape(PageRef ref);           // which of its rows are shown and enabled: a change needs a reopen
int mm_page_reset(PageRef ref);                // Y on the page: CH_* bits
int mm_row_widget(const PageRow *r);           // the game's row kind: 0 ON/OFF, 1 a choice with arrows, 2 a button
int mm_row_value(const PageRow *r);            // ON/OFF: 0 or 1; choice: the value's index
int mm_row_max(const PageRow *r);
bool mm_row_own_values(const PageRow *r);      // a choice whose value texts come from mm_row_value_text (a slot)
unsigned mm_row_set(const PageRow *r, int value);   // CH_* bits
int mm_row_press(const PageRow *r, PageRef *open, unsigned *changed);  // A on a row: ACT_*
unsigned mm_row_key(const PageRow *r, const char *name);  // a key setting from key capture ("" clears): CH_* bits
bool mm_row_locked(const PageRow *r);          // grey: its changes are refused
bool mm_row_same(const PageRow *a, const PageRow *b);
int mm_row_picture(const PageRow *r, PageRef ref);  // the mod whose picture goes with the row, -1 none
void mm_row_title(const PageRow *r, char *out, size_t cap);
void mm_row_desc(const PageRow *r, char *out, size_t cap);
void mm_row_button(const PageRow *r, char *out, size_t cap);
void mm_row_value_text(const PageRow *r, int v, char *out, size_t cap);
void mm_mods_button_desc(char *out, size_t cap);
void mm_ordinal(int n, char *out, size_t cap);  // 1st, 2nd, 3rd, 4th...

// Fixed texts (English, plain ASCII: the option fonts miss many letters)
#define TXT_MODS "Mods"
#define TXT_OPEN "Open"
#define TXT_SEE_BELOW "See below"

// ---- the two ways onto the Options screen. Each returns true when the Mods page is on; why says why not.
bool page_b_available(void);
bool page_b_start(char *why, size_t cap);
bool page_a_start(char *why, size_t cap);
bool page_a_game_is_up(void);   // the game already runs (a mod loaded with modload): too late for path A+
void page_a_status(SwModReply *r);   // the `modmenu` command's fields about the page up now (call it locked)
bool page_a_inject_key(const char *name, char *why, size_t cap);   // modmenu_key: a key for the key capture

// For the `modmenu` command
typedef struct {
    bool on;
    const char *path;   // "A+", "B" or ""
    char why[200];
} PageStatus;
extern PageStatus g_page;

// Small helpers (modmenu.c)
void mm_copy(char *dst, size_t cap, const char *src);   // plain ASCII: anything else becomes '?'
void mm_cat(char *dst, size_t cap, const char *src);
void mm_plain(char *s);   // the symbols the menu font lacks as look-alikes ({ -> ( ...), for a text with no font tags
bool mm_font_lacks(char c);   // one of # $ % @ ^ ` { | } ~ (the game's menu font has no glyph for them)
