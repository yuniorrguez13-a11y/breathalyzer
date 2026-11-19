// mm_pages.c - what each page of the Mod menu shows (docs/mod-menu.md), for both paths onto the Options screen:
//   Mods       a row per mod (ON/OFF), Change order, Mod settings and info, More, How to use changes
//   Order      a row per mod with its place
//   More       Mod set, Font, Turn all mods off / on, What changed, Mods that share files
//   Mod settings and info   a row per mod: it opens the mod's page
//   a mod's page            what it does, its version, authors, files and website (menu.txt "about" and "files"
//                           lines: any modhost), then its settings (modhost's settings_ui: mod API 3), a button per
//                           [[pages]] sub-page, and Reset settings
// Everything here runs locked (mm_lock). Texts are plain ASCII; the symbols the game's font lacks are left in (the
// caller shows them: opt_page_a.c with the game's font tag 3, opt_page_b.c as look-alikes).
#include <stdlib.h>

#include "opt_page.h"

void mm_ordinal(int n, char *out, size_t cap) {
    const char *s = n % 100 >= 10 && n % 100 <= 20 ? "th" : n % 10 == 1 ? "st" : n % 10 == 2 ? "nd" : n % 10 == 3 ? "rd"
                                                                                                                   : "th";
    snprintf(out, cap, "%d%s", n, s);
}

static int page_mods(void) { return g_mm.nlisted < MM_PAGE_MODS ? g_mm.nlisted : MM_PAGE_MODS; }

static bool mod_ok(int m) { return m >= 0 && m < g_mm.n; }

// One setting's facts (false: no such setting)
static bool info_of(int sm, int s, SwModSettingInfo *out) {
    const SwModSettingsUi *ui = mm_ui();
    memset(out, 0, sizeof *out);
    out->size = sizeof *out;
    return ui && sm >= 0 && ui->info(sm, s, out) && out->key;
}

static bool same_str(const char *a, const char *b) { return !strcmp(a ? a : "", b ? b : ""); }

// The [[pages]] key of a sub-page ("" the mod's first page)
static const char *page_key(int sm, int sub) {
    const SwModSettingsUi *ui = mm_ui();
    const char *key = NULL;
    if (sub < 0 || !ui || !ui->page_info(sm, sub, &key, NULL, NULL)) return "";
    return key ? key : "";
}

static bool setting_on_page(int sm, int s, const char *key, SwModSettingInfo *info) {
    return info_of(sm, s, info) && same_str(info->page, key);
}

// ------------------------------------------------------------------ the rows of a page

int mm_page(PageRef ref, PageRow *out, int cap) {
    int n = 0;
#define ROW(k, m, a)                                           \
    do {                                                       \
        if (n < cap) out[n++] = (PageRow){(k), (m), (a)};      \
    } while (0)
    const SwModSettingsUi *ui = mm_ui();
    int page = ref.page;
    if (page == PG_MODS && g_mm.list == MM_LIST_OK && g_mm.stale) ROW(RK_STALE, -1, 0);
    if (page == PG_MODS && (g_mm.note[0] || g_mm.changed[0])) ROW(RK_NOTE, -1, 0);
    if (g_mm.list != MM_LIST_OK || !g_mm.nlisted) {
        ROW(RK_INFO, -1, 0);
        return n;
    }
    int shown = page_mods();
    switch (page) {
    case PG_MODS:
    case PG_ORDER:
        for (int k = 0; k < shown; k++) ROW(page == PG_ORDER ? RK_ORDER : RK_MOD, g_mm.order[k], 0);
        if (g_mm.nlisted > shown || g_mm.more) ROW(RK_MORE, -1, 0);
        if (page == PG_MODS) {
            if (g_mm.nlisted >= 2) ROW(RK_ORDER_BUTTON, -1, 0);
            ROW(RK_LIST_BUTTON, -1, 0);
            ROW(RK_MORE_BUTTON, -1, 0);
            ROW(RK_HOWTO, -1, 0);
        }
        break;
    case PG_MORE:
        if (g_mm.nprofiles > 0) ROW(RK_PROFILE, -1, 0);
        if (g_mm.nfonts > 0) ROW(RK_FONT, -1, 0);
        ROW(RK_ALL_OFF, -1, 0);
        ROW(RK_ALL_ON, -1, 0);
        ROW(RK_CHANGED, -1, 0);
        for (int i = 0; i < g_mm.n; i++)
            if (!g_mm.mods[i].hidden && g_mm.mods[i].files >= 0) {
                ROW(RK_CONFLICTS, -1, 0);
                break;
            }
        ROW(RK_HOWTO, -1, 0);
        break;
    case PG_LIST:
        for (int k = 0; k < shown; k++) ROW(RK_MODLINK, g_mm.order[k], 0);
        if (g_mm.nlisted > shown || g_mm.more) ROW(RK_MORE, -1, 0);
        break;
    case PG_MOD:
    case PG_SUB: {
        int m = ref.mod;
        if (!mod_ok(m)) {
            ROW(RK_INFO, -1, 0);
            break;
        }
        int sm = mm_settings_of(m);
        if (page == PG_MOD) {
            if (!g_mm.mods[m].on && !g_mm.mods[m].wanted) ROW(RK_OFF_NOTE, m, 0);
            ROW(RK_ABOUT, m, AB_WHAT);
            for (int i = 0; i < g_mm.mods[m].nfields; i++) {  // ("desc" is What it does; "settings" only without them)
                const char *f = g_mm.mods[m].fields[i].field;
                if (strcmp(f, "desc") && (strcmp(f, "settings") || sm < 0)) ROW(RK_ABOUT, m, AB_FIELD + i);
            }
            if (g_mm.mods[m].files >= 0) ROW(RK_ABOUT, m, AB_FILES);
        }
        const char *key = page == PG_SUB ? page_key(sm, ref.sub) : "";
        int before = n;
        for (int s = 0, count = sm >= 0 ? ui->count(sm) : 0; s < count; s++) {
            SwModSettingInfo info;
            if (setting_on_page(sm, s, key, &info) && (ui->shown(sm, s) || !g_mm.reopen_ok)) ROW(RK_SETTING, m, s);
        }
        if (page == PG_SUB && n == before) ROW(RK_EMPTY, m, 0);
        if (page == PG_MOD && sm >= 0) {
            for (int p = 0, pages = ui->page_count(sm); p < pages; p++) ROW(RK_SUBPAGE, m, p);
            ROW(RK_RESET, m, 0);
        }
        break;
    }
    }
#undef ROW
    if (!n && cap > 0) out[n++] = (PageRow){RK_INFO, -1, 0};
    return n;
}

bool mm_page_parent(PageRef ref, PageRef *parent, PageRow *from) {
    *parent = (PageRef){PG_NONE, -1, -1};
    *from = (PageRow){-1, -1, 0};
    switch (ref.page) {
    case PG_MODS: return true;  // (Savage Wind's first page: the Mods button)
    case PG_ORDER: *parent = (PageRef){PG_MODS, -1, -1}, *from = (PageRow){RK_ORDER_BUTTON, -1, 0}; return true;
    case PG_MORE: *parent = (PageRef){PG_MODS, -1, -1}, *from = (PageRow){RK_MORE_BUTTON, -1, 0}; return true;
    case PG_LIST: *parent = (PageRef){PG_MODS, -1, -1}, *from = (PageRow){RK_LIST_BUTTON, -1, 0}; return true;
    case PG_MOD: *parent = (PageRef){PG_LIST, -1, -1}, *from = (PageRow){RK_MODLINK, ref.mod, 0}; return true;
    case PG_SUB:
        *parent = (PageRef){PG_MOD, ref.mod, -1}, *from = (PageRow){RK_SUBPAGE, ref.mod, ref.sub};
        return true;
    }
    return false;
}

// Which of the page's settings are shown and enabled, as one number: when a change alters it, the page is built
// again (show_if hides rows, enable_if greys them; the game sets a row's name only when it builds the row).
uint64_t mm_page_shape(PageRef ref) {
    if ((ref.page != PG_MOD && ref.page != PG_SUB) || !mod_ok(ref.mod)) return 0;
    const SwModSettingsUi *ui = mm_ui();
    int sm = mm_settings_of(ref.mod);
    if (sm < 0) return 0;
    const char *key = ref.page == PG_SUB ? page_key(sm, ref.sub) : "";
    uint64_t h = 1469598103934665603ull;
    for (int s = 0, count = ui->count(sm); s < count; s++) {
        SwModSettingInfo info;
        if (!setting_on_page(sm, s, key, &info)) continue;
        unsigned bits = (unsigned)s << 2 | (ui->shown(sm, s) ? 2u : 0u) | (ui->enabled(sm, s) ? 1u : 0u);
        h = (h ^ bits) * 1099511628211ull;
    }
    return h;
}

// Resetting a mod's settings asks first: the first press (A on Reset, or Y on the mod's pages) arms it for 4 s and the
// description line says "press again"; a second press within that time resets. (A stray Y wiped keys and texts.)
static int g_reset_sm = -1;
static uint64_t g_reset_until;

static bool reset_armed(int sm) { return sm >= 0 && g_reset_sm == sm && GetTickCount64() < g_reset_until; }

static bool reset_confirmed(int sm) {
    if (reset_armed(sm)) {
        g_reset_sm = -1;
        return true;
    }
    g_reset_sm = sm, g_reset_until = GetTickCount64() + 4000;
    return false;
}

int mm_page_reset(PageRef ref) {
    switch (ref.page) {
    case PG_MODS:
    case PG_ORDER:
    case PG_MORE: return mm_undo() ? (int)CH_OTHERS : 0;
    case PG_MOD:
    case PG_SUB: {
        int sm = mod_ok(ref.mod) ? mm_settings_of(ref.mod) : -1;
        if (sm < 0) return 0;
        if (!reset_confirmed(sm)) return (int)CH_OTHERS;  // (the rows' line now says "press Y again")
        uint32_t before = mm_ui()->serial();
        mm_ui()->reset(sm);
        return mm_ui()->serial() != before ? (int)CH_OTHERS : 0;
    }
    }
    return 0;
}

// ------------------------------------------------------------------ one row

static bool row_mod_ok(const PageRow *r) { return mod_ok(r->mod); }

static int row_sm(const PageRow *r) { return row_mod_ok(r) ? mm_settings_of(r->mod) : -1; }

static int setting_widget(int type) {
    return type == SWMOD_SETTING_TOGGLE ? 0 : type == SWMOD_SETTING_SLIDER || type == SWMOD_SETTING_CHOICE ? 1 : 2;
}

int mm_row_widget(const PageRow *r) {
    switch (r->kind) {
    case RK_MOD: return 0;
    case RK_ORDER: case RK_PROFILE: case RK_FONT: return 1;
    case RK_SETTING: {
        SwModSettingInfo info;
        return info_of(row_sm(r), r->arg, &info) ? setting_widget(info.type) : 2;
    }
    }
    return 2;
}

int mm_row_value(const PageRow *r) {
    switch (r->kind) {
    case RK_MOD: return row_mod_ok(r) && g_mm.mods[r->mod].wanted;
    case RK_ORDER: return row_mod_ok(r) && mm_wanted_pos(r->mod) >= 0 ? mm_wanted_pos(r->mod) : 0;
    case RK_PROFILE: return g_mm.profile_want < 0 ? 0 : g_mm.profile_want;
    case RK_FONT: return g_mm.font_want < 0 ? 0 : g_mm.font_want;
    case RK_SETTING: {
        int sm = row_sm(r), v = sm >= 0 ? mm_ui()->get_index(sm, r->arg) : 0;
        return v < 0 ? 0 : v > mm_row_max(r) ? mm_row_max(r) : v;
    }
    }
    return 0;
}

// An Order row's places: only those the page shows (a mod moved past them would leave the page for good)
int mm_row_max(const PageRow *r) {
    switch (r->kind) {
    case RK_ORDER: return page_mods() > 1 ? page_mods() - 1 : 0;
    case RK_MOD: return 1;
    case RK_PROFILE: return g_mm.nprofiles > 1 ? g_mm.nprofiles - 1 : 0;
    case RK_FONT: return g_mm.nfonts > 1 ? g_mm.nfonts - 1 : 0;
    case RK_SETTING: {
        SwModSettingInfo info;
        if (!info_of(row_sm(r), r->arg, &info)) return 0;
        if (info.type == SWMOD_SETTING_TOGGLE) return 1;
        int max = info.values - 1;
        return max < 0 ? 0 : max > MM_SLOT_VALUES - 1 ? MM_SLOT_VALUES - 1 : max;
    }
    }
    return 0;
}

bool mm_row_own_values(const PageRow *r) {
    return (r->kind == RK_PROFILE || r->kind == RK_FONT || r->kind == RK_SETTING) && mm_row_widget(r) == 1;
}

bool mm_row_locked(const PageRow *r) {
    if (r->kind != RK_SETTING) return false;
    int sm = row_sm(r);
    if (sm < 0) return true;
    return !mm_ui()->enabled(sm, r->arg) || (!g_mm.reopen_ok && !mm_ui()->shown(sm, r->arg));
}

bool mm_row_same(const PageRow *a, const PageRow *b) {
    return a->kind == b->kind && a->mod == b->mod && a->arg == b->arg;
}

int mm_row_picture(const PageRow *r, PageRef ref) {
    if (r->kind == RK_MOD || r->kind == RK_ORDER || r->kind == RK_MODLINK) return row_mod_ok(r) ? r->mod : -1;
    if ((ref.page == PG_MOD || ref.page == PG_SUB) && mod_ok(ref.mod)) return ref.mod;
    return -1;
}

// Another mod set is picked: mod changes are refused (they'd change the set that isn't used next)
static bool set_picked(void) { return g_mm.profile_want >= 0 && g_mm.profile_want != g_mm.profile_now; }

// The changes waiting that are mods (on/off, order): not the mod set or the font
static int mod_changes(void) {
    return mm_changes() - (g_mm.profile_want != g_mm.profile_now) - (g_mm.font_want != g_mm.font_now);
}

static void picked_note(char *out, size_t cap) {
    snprintf(out, cap, "Mod set %s is picked. Change its mods once it is in use.", g_mm.profiles[g_mm.profile_want]);
}

unsigned mm_row_set(const PageRow *r, int value) {
    switch (r->kind) {
    case RK_MOD:
        if (!row_mod_ok(r)) return 0;
        if (set_picked()) return CH_REFUSED;  // (another mod set is picked: this list isn't the one used next)
        mm_set_wanted(r->mod, value != 0);
        return CH_THIS;
    case RK_ORDER:
        if (!row_mod_ok(r)) return 0;
        if (set_picked()) return CH_REFUSED;
        mm_move(r->mod, value > mm_row_max(r) ? mm_row_max(r) : value);
        return CH_THIS | CH_OTHERS;  // (a move changes every position on the Order page)
    case RK_PROFILE: mm_set_profile(value); return CH_THIS;
    case RK_FONT: mm_set_font(value); return CH_THIS;
    case RK_SETTING: {
        int sm = row_sm(r);
        if (sm < 0 || mm_row_locked(r)) return CH_REFUSED;
        int had = mm_ui()->get_index(sm, r->arg);
        if (had == value) return 0;
        int res = mm_ui()->set_index(sm, r->arg, value);
        return res == SWMOD_SET_OK ? CH_THIS | CH_OTHERS : res == SWMOD_SET_LOCKED ? CH_REFUSED : 0;
    }
    }
    return 0;
}

static void set_all(bool on) {
    for (int k = 0; k < g_mm.nlisted; k++) mm_set_wanted(g_mm.order[k], on);
}

int mm_row_press(const PageRow *r, PageRef *open, unsigned *changed) {
    *changed = 0;
    switch (r->kind) {
    case RK_MOD: case RK_ORDER: case RK_PROFILE: case RK_FONT: return ACT_GAME;
    case RK_ORDER_BUTTON: *open = (PageRef){PG_ORDER, -1, -1}; return ACT_OPEN;
    case RK_LIST_BUTTON: *open = (PageRef){PG_LIST, -1, -1}; return ACT_OPEN;
    case RK_MORE_BUTTON: *open = (PageRef){PG_MORE, -1, -1}; return ACT_OPEN;
    case RK_MODLINK:
        if (!row_mod_ok(r)) return ACT_DONE;
        *open = (PageRef){PG_MOD, r->mod, -1};
        return ACT_OPEN;
    case RK_SUBPAGE: *open = (PageRef){PG_SUB, r->mod, r->arg}; return ACT_OPEN;
    case RK_ALL_OFF:
    case RK_ALL_ON: {
        if (set_picked()) return ACT_REFUSED;  // (its line says which set is picked)
        int before = mm_changes();
        set_all(r->kind == RK_ALL_ON);
        *changed = mm_changes() != before ? CH_THIS | CH_OTHERS : CH_THIS;
        return ACT_DONE;
    }
    case RK_RESET: {
        int sm = row_sm(r);
        if (sm >= 0 && !reset_confirmed(sm)) {
            *changed = CH_THIS;  // (its line now says "press A again")
            return ACT_DONE;
        }
        if (sm >= 0) {
            uint32_t before = mm_ui()->serial();
            mm_ui()->reset(sm);
            *changed = mm_ui()->serial() != before ? CH_THIS | CH_OTHERS : 0;
        }
        return ACT_DONE;
    }
    case RK_SETTING: {
        int sm = row_sm(r);
        SwModSettingInfo info;
        if (!info_of(sm, r->arg, &info)) return ACT_DONE;
        if (mm_row_locked(r)) return ACT_REFUSED;
        if (info.type == SWMOD_SETTING_KEY) return ACT_KEY;
        if (info.type == SWMOD_SETTING_BUTTON) {
            *changed = mm_ui()->press(sm, r->arg) == SWMOD_SET_OK ? CH_THIS | CH_OTHERS : 0;
            return ACT_DONE;
        }
        return info.type == SWMOD_SETTING_TEXT ? ACT_DONE : ACT_GAME;
    }
    }
    return ACT_DONE;
}

unsigned mm_row_key(const PageRow *r, const char *name) {
    int sm = row_sm(r);
    if (r->kind != RK_SETTING || sm < 0 || mm_row_locked(r)) return CH_REFUSED;
    int had = mm_ui()->get_index(sm, r->arg);
    if (mm_ui()->set_key(sm, r->arg, name) != SWMOD_SET_OK) return CH_REFUSED;
    return mm_ui()->get_index(sm, r->arg) != had ? CH_THIS | CH_OTHERS : 0;
}

// ------------------------------------------------------------------ texts

// When the changes apply: "at your next start" only once Play in Mod Manager uses them (menu.txt "use modman")
static const char *when(void) { return g_mm.use_modman ? " at your next start." : " once you use changes."; }

static const MmField *field_of(int m, const char *name) {
    for (int i = 0; mod_ok(m) && i < g_mm.mods[m].nfields; i++)
        if (!strcmp(g_mm.mods[m].fields[i].field, name)) return &g_mm.mods[m].fields[i];
    return NULL;
}

// The description line: `what` shortened (at a word end, with "...") so that `tail` always fits in 80 characters
// (MM_TEXT_MAX: the game shows about 82, then cuts the rest at a word)
static void fit(const char *what, const char *tail, char *out, size_t cap) {
    size_t room = cap > MM_TEXT_MAX ? MM_TEXT_MAX : cap;
    size_t want = strlen(tail) + (tail[0] ? 1 : 0);
    char about[MM_TEXT_MAX + 8];
    mm_copy(about, sizeof about, what);
    if (want + strlen(about) + 1 > room) {  // (too long for the line: shorten what it does)
        size_t keep = room > want + 4 ? room - want - 4 : 0;
        if (keep < strlen(about)) {
            size_t sp = keep;
            while (sp > keep / 2 && about[sp] != ' ') sp--;  // (at the end of a word, when one ends in the second half)
            if (about[sp] == ' ') keep = sp;
            while (keep && strchr(" ,;:", about[keep - 1])) keep--;
            about[keep] = 0;
            if (keep) mm_cat(about, sizeof about, "...");
        }
    }
    mm_copy(out, room, about);
    if (about[0] && tail[0]) mm_cat(out, room, " ");
    mm_cat(out, room, tail);
}

// A mod's description line: what it does, then what changes at the next start (or Mod Manager's warnings): what it
// does is shortened, never the change.
static void mod_desc(const MmMod *m, int k, char *out, size_t cap) {
    char tail[MM_TEXT_MAX] = "";
    if (m->wanted != m->on) mm_copy(tail, sizeof tail, m->wanted ? "Turns on" : "Turns off");
    else if (m->pos != k) mm_copy(tail, sizeof tail, "Moves");
    if (tail[0]) mm_cat(tail, sizeof tail, when());
    if (tail[0] && m->rebuild) mm_cat(tail, sizeof tail, " Next start takes 10-20 minutes.");
    if (!tail[0]) mm_copy(tail, sizeof tail, m->extra);
    fit(m->about, tail, out, cap);
}

static const char *about_title(const char *field) {
    if (!strcmp(field, "version")) return "Version";
    if (!strcmp(field, "authors")) return "Made by";
    if (!strcmp(field, "url")) return "Website";
    if (!strcmp(field, "settings")) return "Settings";
    return field;
}

static const MmField *about_field(const PageRow *r) {
    int i = r->arg - AB_FIELD;
    return row_mod_ok(r) && i >= 0 && i < g_mm.mods[r->mod].nfields ? &g_mm.mods[r->mod].fields[i] : NULL;
}

void mm_row_title(const PageRow *r, char *out, size_t cap) {
    const char *s = "";
    char buf[MM_TEXT_MAX] = "";
    switch (r->kind) {
    case RK_MOD:
    case RK_ORDER:
    case RK_MODLINK: s = row_mod_ok(r) ? g_mm.mods[r->mod].name : "?"; break;
    case RK_NOTE:
        s = g_mm.crashed && g_mm.changed[0] ? "Last start had an error"
            : g_mm.note[0]                  ? "Last change not used"
                                            : "New since last start";
        break;
    case RK_INFO: s = g_mm.list == MM_LIST_MISSING ? "Mod list missing" : g_mm.list == MM_LIST_DAMAGED
                                                                                ? "Mod list damaged"
                                                                                : g_mm.nlisted ? "Nothing here"
                                                                                               : "No mods yet"; break;
    case RK_MORE: s = "More mods in Mod Manager"; break;
    case RK_ORDER_BUTTON: s = "Change order"; break;
    case RK_HOWTO: s = "How to use changes"; break;
    case RK_STALE: s = "This list may be old"; break;
    case RK_LIST_BUTTON: s = "Mod settings and info"; break;
    case RK_MORE_BUTTON: s = "More"; break;
    case RK_PROFILE: s = "Mod set"; break;
    case RK_FONT: s = "Font"; break;
    case RK_ALL_OFF: s = "Turn all mods off"; break;
    case RK_ALL_ON: s = "Turn all mods on"; break;
    case RK_CHANGED: s = "What changed"; break;
    case RK_CONFLICTS: s = "Mods that share files"; break;
    case RK_OFF_NOTE: s = "This mod is off"; break;
    case RK_ABOUT:
        if (r->arg == AB_WHAT && row_mod_ok(r)) {  // (the first row says whose page this is)
            snprintf(buf, sizeof buf, "%s", g_mm.mods[r->mod].name);
            s = buf;
        } else if (r->arg == AB_WHAT) s = "What it does";
        else if (r->arg == AB_FILES) s = "Game files";
        else s = about_field(r) ? about_title(about_field(r)->field) : "";
        break;
    case RK_SETTING: {
        SwModSettingInfo info;
        s = info_of(row_sm(r), r->arg, &info) && info.label ? info.label : "?";
        break;
    }
    case RK_SUBPAGE: {
        const char *label = NULL;
        const SwModSettingsUi *ui = mm_ui();
        if (ui && row_sm(r) >= 0) ui->page_info(row_sm(r), r->arg, NULL, &label, NULL);
        s = label ? label : "?";
        break;
    }
    case RK_RESET: s = "Reset settings"; break;
    case RK_EMPTY: s = "Nothing here now"; break;
    }
    mm_copy(buf, sizeof buf, s);
    mm_copy(out, cap, buf);
}

static void files_desc(const MmMod *m, char *out, size_t cap) {
    if (m->files <= 0) {
        mm_copy(out, cap, "It changes no game files.");
    } else if (m->loses > 0) {
        snprintf(out, cap, "%d of its %d files are replaced by a later mod%s%s%s.", m->loses, m->files,
                 m->rival[0] ? " (" : "", m->rival, m->rival[0] ? ")" : "");
    } else {
        snprintf(out, cap, "It changes %d game file%s. No later mod replaces them.", m->files, m->files == 1 ? "" : "s");
    }
}

static void setting_desc(const PageRow *r, char *out, size_t cap) {
    const SwModSettingsUi *ui = mm_ui();
    int sm = row_sm(r);
    SwModSettingInfo info;
    if (!info_of(sm, r->arg, &info)) {
        out[0] = 0;
        return;
    }
    char lock[MM_TEXT_MAX] = "", tail[MM_TEXT_MAX] = "";
    if (!ui->enabled(sm, r->arg)) {
        ui->lock_text(sm, r->arg, lock, sizeof lock);   // ("Locked: needs Sprint mode Custom.")
        mm_copy(out, cap > MM_TEXT_MAX ? MM_TEXT_MAX : cap, lock[0] ? lock : "Locked.");
        return;
    }
    if (!ui->shown(sm, r->arg)) {  // (no reopen: the row stays, grey)
        mm_copy(out, cap > MM_TEXT_MAX ? MM_TEXT_MAX : cap, "Not used now: other settings turn it off.");
        return;
    }
    if (info.type == SWMOD_SETTING_KEY) mm_copy(tail, sizeof tail, "A: set a key.");
    if (info.type == SWMOD_SETTING_TEXT) mm_copy(tail, sizeof tail, "Change it in Mod Manager.");
    if (ui->waiting(sm, r->arg)) {
        if (tail[0]) mm_cat(tail, sizeof tail, " ");
        mm_cat(tail, sizeof tail, "Changes at your next start.");
    } else if (row_mod_ok(r) && !g_mm.mods[r->mod].on) {
        if (tail[0]) mm_cat(tail, sizeof tail, " ");
        mm_cat(tail, sizeof tail, "Used once the mod is on.");
    }
    fit(info.desc ? info.desc : "", tail, out, cap);
}

static void conflicts_desc(char *out, size_t cap) {
    int losing = 0, first = -1;
    for (int i = 0; i < g_mm.n; i++)
        if (!g_mm.mods[i].hidden && g_mm.mods[i].loses > 0) {
            if (first < 0) first = i;
            losing++;
        }
    if (!losing) {
        mm_copy(out, cap, "No two mods change the same file.");
    } else if (losing == 1) {
        const MmMod *m = &g_mm.mods[first];
        snprintf(out, cap, "%s replaces %d of %s's files. The later one is used.", m->rival[0] ? m->rival : "A later mod",
                 m->loses, m->name);
    } else {
        snprintf(out, cap, "%d mods have files a later mod replaces. Each mod's info page says which.", losing);
    }
}

void mm_row_desc(const PageRow *r, char *out, size_t cap) {
    char a[16], tail[MM_TEXT_MAX];
    out[0] = 0;
    if (r->kind != RK_RESET && (r->kind == RK_SETTING || r->kind == RK_ABOUT || r->kind == RK_SUBPAGE ||
                                r->kind == RK_EMPTY || r->kind == RK_OFF_NOTE) && reset_armed(row_sm(r))) {
        mm_copy(out, cap, "Press Y again to reset every setting of this mod, keys too.");  // (Y on a mod's page)
        return;
    }
    switch (r->kind) {
    case RK_MOD:
        if (set_picked()) picked_note(out, cap);
        else if (row_mod_ok(r)) mod_desc(&g_mm.mods[r->mod], mm_wanted_pos(r->mod), out, cap);
        break;
    case RK_ORDER:
        if (set_picked()) {
            picked_note(out, cap);
        } else if (row_mod_ok(r) && g_mm.mods[r->mod].pos != mm_wanted_pos(r->mod)) {  // ("Was 12th. Moves once...")
            mm_ordinal(g_mm.mods[r->mod].pos + 1, a, sizeof a);
            snprintf(out, cap, "Was %s. Moves", a);
            mm_cat(out, cap, when());
        } else {
            mm_copy(out, cap, "When two mods change the same file, the later one in the list is used.");
        }
        break;
    case RK_NOTE:
        mm_copy(out, cap, g_mm.crashed && g_mm.changed[0] ? g_mm.changed : g_mm.note[0] ? g_mm.note : g_mm.changed);
        break;
    case RK_INFO:
        mm_copy(out, cap,
                g_mm.list != MM_LIST_OK ? "Close the game. Then on your PC run: python tools/modmenu.py sync"
                : g_mm.nlisted          ? "This mod has no settings or info here."
                                        : "Add mods in Mod Manager on your PC.");
        break;
    case RK_MORE:
        snprintf(out, cap, "This page shows the first %d. Mod Manager on your PC shows all of them.", MM_PAGE_MODS);
        break;
    case RK_ORDER_BUTTON: mm_copy(out, cap, "When two mods change the same file, the later one is used. Order them here.");
        break;
    case RK_HOWTO:
        mm_copy(out, cap, g_mm.use_modman ? "Close the game. Then press Play in Mod Manager."
                                          : "Close the game. Then on your PC run: python tools/modmenu.py play");
        break;
    case RK_STALE:
        mm_copy(out, cap, "Mod Manager changed mods after this list was made. ON/OFF may be wrong.");
        break;
    case RK_LIST_BUTTON: mm_copy(out, cap, "Each mod's settings, version, files and website."); break;
    case RK_MORE_BUTTON: mm_copy(out, cap, "Mod sets, the font, turn all mods off or on, and what changed."); break;
    case RK_PROFILE:
        if (g_mm.profile_want >= 0 && g_mm.profile_want != g_mm.profile_now) {
            snprintf(tail, sizeof tail, "Uses the mod set %s", g_mm.profiles[g_mm.profile_want]);
            mm_cat(tail, sizeof tail, when());
            if (mod_changes()) mm_cat(tail, sizeof tail, " Mod changes here are not used.");
            fit("", tail, out, cap);
        } else {
            mm_copy(out, cap, "A mod set keeps mods, their settings and the font. Make sets in Mod Manager.");
        }
        break;
    case RK_FONT:
        if (g_mm.font_want >= 0 && g_mm.font_want != g_mm.font_now) {
            snprintf(tail, sizeof tail, "%s", g_mm.fonts[g_mm.font_want].label);
            mm_cat(tail, sizeof tail, when());
            mm_copy(out, cap > MM_TEXT_MAX ? MM_TEXT_MAX : cap, tail);
        } else {
            mm_copy(out, cap, "The font of the menus and texts.");
        }
        break;
    case RK_ALL_OFF:
    case RK_ALL_ON: {
        int n = mm_changes();
        if (set_picked()) {
            picked_note(out, cap);
        } else if (n) {
            snprintf(tail, sizeof tail, "%d change%s wait%s", n, n == 1 ? "" : "s", n == 1 ? "s" : "");
            mm_cat(tail, sizeof tail, when());
            fit(r->kind == RK_ALL_OFF ? "Every mod off. The Mod menu stays." : "Every mod on.", tail, out, cap);
        } else {
            mm_copy(out, cap, r->kind == RK_ALL_OFF ? "Every mod off at your next start. The Mod menu stays."
                                                    : "Every mod on at your next start.");
        }
        break;
    }
    case RK_CHANGED: mm_copy(out, cap, g_mm.changed[0] ? g_mm.changed : "Nothing new since your last start."); break;
    case RK_CONFLICTS: conflicts_desc(out, cap); break;
    case RK_MODLINK:
        if (row_mod_ok(r)) {
            int sm = mm_settings_of(r->mod), n = sm >= 0 ? mm_ui()->count(sm) : 0;
            tail[0] = 0;
            if (n) snprintf(tail, sizeof tail, "%d setting%s.", n, n == 1 ? "" : "s");
            fit(g_mm.mods[r->mod].about, tail, out, cap);
        }
        break;
    case RK_OFF_NOTE: mm_copy(out, cap, "Turn it on on the Mods page. Its settings are used once it is on."); break;
    case RK_ABOUT:
        if (!row_mod_ok(r)) break;
        if (r->arg == AB_WHAT) {
            const MmField *d = field_of(r->mod, "desc");
            fit(d ? d->text : g_mm.mods[r->mod].about, g_mm.mods[r->mod].extra, out, cap);
        } else if (r->arg == AB_FILES) {
            files_desc(&g_mm.mods[r->mod], out, cap > MM_TEXT_MAX ? MM_TEXT_MAX : cap);
        } else if (about_field(r)) {
            const MmField *f = about_field(r);
            if (!strcmp(f->field, "settings"))
                mm_copy(out, cap, "Its settings show after your next start from Mod Manager.");
            else mm_copy(out, cap > MM_TEXT_MAX ? MM_TEXT_MAX : cap, f->text);
        }
        break;
    case RK_SETTING: setting_desc(r, out, cap); break;
    case RK_SUBPAGE: {
        const char *desc = NULL;
        const SwModSettingsUi *ui = mm_ui();
        if (ui && row_sm(r) >= 0) ui->page_info(row_sm(r), r->arg, NULL, NULL, &desc);
        mm_copy(out, cap > MM_TEXT_MAX ? MM_TEXT_MAX : cap, desc ? desc : "");
        break;
    }
    case RK_RESET:
        mm_copy(out, cap, reset_armed(row_sm(r)) ? "Press A again to reset every setting of this mod, keys too."
                                                 : "Every setting of this mod back to its default. Asks first.");
        break;
    case RK_EMPTY: mm_copy(out, cap, "Other settings hide these rows now."); break;
    }
}

void mm_row_button(const PageRow *r, char *out, size_t cap) {
    switch (r->kind) {
    case RK_ORDER_BUTTON: case RK_LIST_BUTTON: case RK_MORE_BUTTON: case RK_MODLINK: case RK_SUBPAGE:
        mm_copy(out, cap, TXT_OPEN);
        return;
    case RK_ALL_OFF: mm_copy(out, cap, "Turn off"); return;
    case RK_ALL_ON: mm_copy(out, cap, "Turn on"); return;
    case RK_RESET: mm_copy(out, cap, "Reset"); return;
    case RK_ABOUT:
        if (r->arg == AB_FILES && row_mod_ok(r)) {
            snprintf(out, cap, "%d", g_mm.mods[r->mod].files);
            return;
        }
        if (about_field(r) && strcmp(about_field(r)->field, "url") && strcmp(about_field(r)->field, "settings") &&
            strlen(about_field(r)->text) <= 16) {
            mm_copy(out, cap, about_field(r)->text);
            return;
        }
        break;
    case RK_SETTING: {
        char v[MM_TEXT_MAX] = "";
        int sm = row_sm(r);
        if (sm >= 0) mm_ui()->value_text(sm, r->arg, -1, v, sizeof v);
        if (strlen(v) > 20) strcpy(v + 17, "...");
        mm_copy(out, cap, v);
        return;
    }
    }
    mm_copy(out, cap, TXT_SEE_BELOW);
}

void mm_row_value_text(const PageRow *r, int v, char *out, size_t cap) {
    out[0] = 0;
    if (r->kind == RK_PROFILE && v >= 0 && v < g_mm.nprofiles) mm_copy(out, cap, g_mm.profiles[v]);
    else if (r->kind == RK_FONT && v >= 0 && v < g_mm.nfonts) mm_copy(out, cap, g_mm.fonts[v].label);
    else if (r->kind == RK_SETTING && row_sm(r) >= 0) {
        char buf[MM_TEXT_MAX];
        mm_ui()->value_text(row_sm(r), r->arg, v, buf, sizeof buf);
        mm_copy(out, cap, buf);
    }
}

void mm_mods_button_desc(char *out, size_t cap) {
    int n = mm_changes();
    mm_copy(out, cap, "Turn mods on or off, and change their order.");
    if (n) {
        char more[48];
        snprintf(more, sizeof more, " %d change%s wait%s.", n, n == 1 ? "" : "s", n == 1 ? "s" : "");
        mm_cat(out, cap, more);
    }
    if (g_mm.stale) mm_cat(out, cap, " List may be old.");  // (80 characters at most, with 64 changes)
}
