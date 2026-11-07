// opt_page.h - shared by the files of the Mod menu mod (docs/mod-menu.md):
//   modmenu.c     the mod list (menu.txt from tools/modmenu.py), the player's changes (pending.txt, written by a
//                 thread of its own), the commands, and swmod_init
//   opt_page_a.c  the Mods page through Savage Wind's own Options calls (path A+: 0.4.1-alpha.4 with BotW 1.9.0)
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
#define MM_PAGE_ROWS (MM_PAGE_MODS + 6)
#define MM_ID_MAX 72       // ids up to 71 characters (modman allows 64)
#define MM_NAME_MAX 29     // 28 characters (the row's name box) and the terminator
#define MM_ABOUT_MAX 71
#define MM_EXTRA_MAX 61
#define MM_NOTE_MAX 81
#define MM_TEXT_MAX 81     // the description line: 80 characters (the game shows about 82, then cuts at a word)

extern const SwModApi *g_api;

// ---- the mod list. Lock it around every use: the game thread (the page), the frame thread (commands) and the
// writer thread all use it.
enum { MM_LIST_OK, MM_LIST_MISSING, MM_LIST_DAMAGED };

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
} MmMod;

typedef struct {
    int list;                     // MM_LIST_*
    int n;                        // mods read (listed and hidden)
    MmMod mods[MM_MAX_MODS + 4];
    int nlisted;
    int order[MM_MAX_MODS];       // the listed mods in the wanted order (indices into mods)
    bool more;                    // menu.txt has more mods than the game lists
    bool stale;                   // config.json changed since menu.txt was written (Mod Manager made changes)
    char note[MM_NOTE_MAX];       // "Last change not used": why
    char serial[24];
    bool use_modman;              // menu.txt "use modman": Play in Mod Manager uses the changes (else modmenu.py play)
    uint32_t version;             // goes up with every change (the writer thread writes pending.txt)
} MmModel;

extern MmModel g_mm;
void mm_lock(void);
void mm_unlock(void);

// Changes (call them locked). m: an index into g_mm.mods.
void mm_set_wanted(int m, bool on);
void mm_move(int m, int pos);   // to wanted position pos (0-based, clamped)
bool mm_undo(void);             // every mod back to how it is now; true when something changed
int mm_wanted_pos(int m);       // -1: hidden
int mm_changes(void);
void mm_flush_soon(void);       // write pending.txt now (not after the usual pause)

// ---- the pages, shared by both paths (call them locked)
enum { PG_NONE, PG_MODS, PG_ORDER };   // PG_NONE: one of Savage Wind's pages
enum { RK_NOTE, RK_INFO, RK_MOD, RK_MORE, RK_ORDER_BUTTON, RK_HOWTO, RK_ORDER, RK_STALE };
typedef struct {
    int kind;  // RK_*
    int mod;   // RK_MOD, RK_ORDER: an index into g_mm.mods
} PageRow;

int mm_page(int page, PageRow *out, int cap);  // the rows of a page: always at least one
int mm_row_widget(const PageRow *r);           // the game's row kind: 0 ON/OFF, 1 a choice with arrows, 2 a button
int mm_row_value(const PageRow *r);            // ON/OFF: 0 or 1; choice: the wanted position (0-based)
int mm_row_max(const PageRow *r);
bool mm_row_set(const PageRow *r, int value);  // true when other rows' values changed too (the order)
void mm_row_title(const PageRow *r, char *out, size_t cap);
void mm_row_desc(const PageRow *r, char *out, size_t cap);
void mm_row_button(const PageRow *r, char *out, size_t cap);
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
