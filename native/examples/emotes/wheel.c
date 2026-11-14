// wheel.c - the emote wheel (part of emotes.c, #included there; docs/emotes.md "The wheel").
//
// Hold G (or hold D-pad Down for 0.4 s on the controller): a big round wheel of 8 slices comes up, each with a drawn
// icon and a big name. Move the mouse (or a stick, or WASD / the arrows) toward a slice to light it up gold; let go of
// G (or D-pad Down) to play it, or click it. A tap of G keeps the wheel open until you pick (click, Enter, 1-8) or
// close it (Esc, right click, G). Pages: the mouse wheel, Q / R, or L / R on the controller: your favourites (Mods
// page > Emotes > Wheel favourites) first, then Hello, Dances, Hyrule, and Extras (your own emote ids). 0 or Y:
// ragdoll. While the wheel is up the game gets no input (modhost's overlay host, swmod_overlay.h): Link stands still,
// the camera stops, the click doesn't swing a sword; after it closes the game stays muted until every key is up.
// A toast at the bottom names the emote that starts, or says why it can't ("Link has to stand still").
//
// Threads: the wheel runs on Savage Wind's present thread (the overlay's update and draw). It only reads its own
// state, the settings (any thread) and g_snap under the lock, and leaves requests with request(), like the console.
// The tick (game thread) keeps the number keys 5-0 quiet while the wheel is up (g_wheel_up, g_wheel_opens).
// Icons: plain shapes drawn here (our own), no pictures.
#include "swmod_overlay.h"

#define WHEEL_SLICES 8
#define WHEEL_PAGES_MAX 8
#define WHEEL_TAP_S 0.25        // a shorter press of the wheel key keeps the wheel open
#define WHEEL_PAD_HOLD_S 0.4    // hold D-pad Down this long to open it
#define WHEEL_TOAST_S 2.0
#define WHEEL_STALE_S 0.5       // Link's update hasn't run for this long: no world (title, loading): no wheel
#define WHEEL_PI 3.14159265f

static const char *const WHEEL_FAV_DEFAULT[WHEEL_SLICES] = {"wave", "thumbs_up", "floss", "six_seven",
                                                            "in_love", "daruk_flex", "thumbs_down", "victory"};

// shared with the tick and the commands
static int g_wheel_up;              // the wheel is open now
static uint32_t g_wheel_opens;      // goes up each time it opens
static int g_wheel_cmd;             // `emote wheel`: open it (and keep it open)
static int g_wheel_state;           // 1 ready, 0 not set up, -1 off (g_wheel_off says why)
static char g_wheel_off[160];

typedef struct {
    int ix;                         // EMOTES index; -1: a community id (id); -2: empty
    char id[25];
} WEntry;
typedef struct {
    char title[24];
    WEntry e[WHEEL_SLICES];
} WPage;

static struct {
    // settings
    int on, vk, pad_on, show_names;
    float size;                     // percent, 100 .. 200
    char fav[WHEEL_SLICES][25];
    char extras[72];
    double settings_at;
    // the wheel
    WPage pages[WHEEL_PAGES_MAX];
    int npages;
    int open, sticky, by_pad, page, hl;
    float vx, vy;                   // the mouse's own pointer, from the centre (frame pixels)
    float wheel_acc;
    int key_was, key_opened;        // the wheel key: held last update; it opened the wheel and is still held
    double key_since;
    int pad_was, pad_armed, pad_opened;
    double pad_since;
    // the toast
    uint32_t toast_seen;
    char toast[128];
    double toast_at;
    int toast_primed;
} wh = {.on = 1, .vk = 'G', .pad_on = 1, .show_names = 1, .size = 150.0f, .hl = -1, .toast_at = -1e9};

// ------------------------------------------------------------------ small math (no libm here)
static float w_sin(float x) {
    const float two_pi = 2.0f * WHEEL_PI;
    x -= two_pi * (float)(int)(x / two_pi);
    if (x > WHEEL_PI) x -= two_pi;
    else if (x < -WHEEL_PI) x += two_pi;
    if (x > WHEEL_PI / 2) x = WHEEL_PI - x;
    else if (x < -WHEEL_PI / 2) x = -WHEEL_PI - x;
    float x2 = x * x;
    return x * (1 - x2 / 6 * (1 - x2 / 20 * (1 - x2 / 42 * (1 - x2 / 72))));
}
static float w_cos(float x) { return w_sin(x + WHEEL_PI / 2); }
static float w_atan01(float z) {  // |z| <= 1
    float z2 = z * z;
    return z * (0.99997726f + z2 * (-0.33262347f + z2 * (0.19354346f + z2 * (-0.11643287f +
                                                                          z2 * (0.05265332f + z2 * -0.01172120f)))));
}
static float w_atan2(float y, float x) {
    if (x == 0 && y == 0) return 0;
    float ax = __builtin_fabsf(x), ay = __builtin_fabsf(y);
    float a = ax >= ay ? w_atan01(ay / ax) : WHEEL_PI / 2 - w_atan01(ax / ay);
    if (x < 0) a = WHEEL_PI - a;
    return y < 0 ? -a : a;
}
// The slice a direction points at (x right, y down): 0 at the top, then clockwise.
static int wheel_slice_at(float x, float y) {
    float a = w_atan2(x, -y);  // 0 up, + clockwise
    if (a < 0) a += 2 * WHEEL_PI;
    int s = (int)((a + WHEEL_PI / WHEEL_SLICES) / (2 * WHEEL_PI / WHEEL_SLICES));
    return s % WHEEL_SLICES;
}

// ------------------------------------------------------------------ settings and pages
static void wheel_settings(double now) {
    if (!__atomic_exchange_n(&g_wheel_settings_dirty, 0, __ATOMIC_ACQ_REL) && wh.settings_at > 0 &&
        now - wh.settings_at < 1.0 && now >= wh.settings_at)
        return;
    wh.settings_at = now > 0 ? now : 1e-9;
    for (int i = 0; i < WHEEL_SLICES; i++) snprintf(wh.fav[i], sizeof wh.fav[i], "%s", WHEEL_FAV_DEFAULT[i]);
    wh.extras[0] = 0;
    if (!api || !SWMOD_HAS(api, setting_int) || !api->setting_int || !api->setting_str) return;
    wh.on = api->setting_int(api, "wheel", 1) != 0;
    wh.vk = (int)api->setting_int(api, "wheel_key", 'G');
    wh.show_names = api->setting_int(api, "show_names", 1) != 0;
    double size = api->setting_num ? api->setting_num(api, "wheel_size", 150.0) : 150.0;
    wh.size = size >= 100.0 && size <= 200.0 ? (float)size : 150.0f;
    char v[72];
    wh.pad_on = !(api->setting_str(api, "wheel_pad", v, sizeof v) > 0 && !strcmp(v, "off"));
    for (int i = 0; i < WHEEL_SLICES; i++) {
        char key[16];
        snprintf(key, sizeof key, "wheel_%d", i + 1);
        if (api->setting_str(api, key, v, sizeof v) > 0) snprintf(wh.fav[i], sizeof wh.fav[i], "%s", v);
    }
    if (api->setting_str(api, "extras", v, sizeof v) > 0) snprintf(wh.extras, sizeof wh.extras, "%s", v);
}

static void wheel_entry(WEntry *e, const char *id) {
    e->ix = -2;
    e->id[0] = 0;
    if (!id || !*id || !strcmp(id, "none")) return;
    for (int i = 0; i < EMOTE_COUNT; i++)
        if (!strcmp(EMOTES[i].id, id)) {
            e->ix = i;
            snprintf(e->id, sizeof e->id, "%s", id);
            return;
        }
    if (valid_id(id)) e->ix = -1, snprintf(e->id, sizeof e->id, "%s", id);  // a community emote: ZEmote_<id>
}

static void wheel_build_pages(void) {
    static const char *const TITLES[] = {"", "Hello", "Dances", "Hyrule", "Poses"};  // (emote_list.h's page numbers)
    memset(wh.pages, 0, sizeof wh.pages);
    wh.npages = 0;
    WPage *p = &wh.pages[wh.npages++];
    snprintf(p->title, sizeof p->title, "Favourites");
    for (int i = 0; i < WHEEL_SLICES; i++) wheel_entry(&p->e[i], wh.fav[i]);
    int last = 0;  // every page of the list, in order (a page that comes later in tools/emotes shows up by itself)
    for (int i = 0; i < EMOTE_COUNT; i++) last = EMOTES[i].page > last ? EMOTES[i].page : last;
    for (int pg = 1; pg <= last && wh.npages < WHEEL_PAGES_MAX - 1; pg++) {
        p = &wh.pages[wh.npages++];
        if (pg < (int)(sizeof TITLES / sizeof TITLES[0])) snprintf(p->title, sizeof p->title, "%s", TITLES[pg]);
        else snprintf(p->title, sizeof p->title, "Page %d", pg);
        for (int i = 0; i < WHEEL_SLICES; i++) p->e[i].ix = -2;
        for (int i = 0; i < EMOTE_COUNT; i++)
            if (EMOTES[i].page == pg && EMOTES[i].slot >= 1 && EMOTES[i].slot <= WHEEL_SLICES)
                wheel_entry(&p->e[EMOTES[i].slot - 1], EMOTES[i].id);
    }
    // Extras: the ids in the `extras` setting (comma between), 8 a page
    int n = 0;
    const char *s = wh.extras;
    while (*s && wh.npages < WHEEL_PAGES_MAX) {
        char id[25];
        size_t k = 0;
        while (*s == ',' || *s == ' ') s++;
        while (*s && *s != ',' && *s != ' ') {
            if (k + 1 < sizeof id) id[k++] = *s;
            s++;
        }
        id[k] = 0;
        if (!k || !valid_id(id)) continue;
        if (n % WHEEL_SLICES == 0) {
            p = &wh.pages[wh.npages++];
            snprintf(p->title, sizeof p->title, n ? "Extras %d" : "Extras", n / WHEEL_SLICES + 1);
            for (int i = 0; i < WHEEL_SLICES; i++) p->e[i].ix = -2;
        }
        wheel_entry(&p->e[n % WHEEL_SLICES], id);
        n++;
    }
}

static const char *wheel_entry_name(const WEntry *e) {
    return e->ix >= 0 ? EMOTES[e->ix].name : e->ix == -1 ? e->id : "";
}

// ------------------------------------------------------------------ open, pick, close
static void wheel_open(double now, int sticky, int by_pad) {
    (void)now;
    wheel_build_pages();
    wh.open = 1, wh.sticky = sticky, wh.by_pad = by_pad;
    wh.page = 0, wh.hl = -1, wh.vx = wh.vy = 0, wh.wheel_acc = 0;
    __atomic_fetch_add(&g_wheel_opens, 1, __ATOMIC_ACQ_REL);
    __atomic_store_n(&g_wheel_up, 1, __ATOMIC_RELEASE);
}
static void wheel_close(void) {
    wh.open = 0, wh.sticky = 0, wh.key_opened = 0, wh.pad_opened = 0, wh.pad_armed = 0;
    __atomic_store_n(&g_wheel_up, 0, __ATOMIC_RELEASE);
}
// Play what slice s of the page shows (nothing for an empty one). The wheel closes when something was asked for.
static int wheel_pick(int s) {
    if (s < 0 || s >= WHEEL_SLICES || wh.page < 0 || wh.page >= wh.npages) return 0;
    const WEntry *e = &wh.pages[wh.page].e[s];
    if (e->ix == -2) return 0;
    if (e->ix >= 0) {
        request(e->ix == ragdoll_index() ? REQ_TOGGLE : REQ_PLAY, e->ix, 0);
    } else {
        char set[40];
        snprintf(set, sizeof set, "%s%s", EMOTE_SET_PREFIX, e->id);
        request(REQ_PLAY, -1, set);
    }
    wheel_close();
    return 1;
}
static void wheel_ragdoll(void) {
    request(REQ_TOGGLE, ragdoll_index(), 0);
    wheel_close();
}

// ------------------------------------------------------------------ the layout (frame pixels)
typedef struct {
    float cx, cy;                   // the centre
    float ro, ri;                   // the ring's outer and inner radius
    float s;                        // the size factor (1 = a 1080-pixel-tall window at Wheel size 150 percent)
    float u;                        // h / 1080
    int stacked;                    // too narrow for the side panels: the page on top, the controls below
} WGeo;
static WGeo wheel_geo(const SwModOverlay *o) {
    WGeo g;
    g.u = o->h > 0 ? o->h / 1080.0f : 1.0f;
    g.s = g.u * wh.size / 150.0f;
    g.ro = 400.0f * g.s;
    // the page (left) and the controls (right) want 300 pixels each beside the wheel; else they go above and below
    // (the number badges sit 46 pixels outside the rim at size 1: the wheel keeps room for them)
    float most = 0.5f * o->h - 70.0f * g.u, beside = 0.5f * o->w - 370.0f * g.u - 46.0f * g.s;
    g.stacked = beside < 0.28f * o->h;
    if (g.stacked) most = 0.5f * o->h - 160.0f * g.u;
    else if (beside < most) most = beside;
    if (g.ro > most && most > 0) g.s *= most / g.ro, g.ro = most;
    g.ri = 0.375f * g.ro;
    g.cx = o->w * 0.5f, g.cy = o->h * 0.5f;
    return g;
}

// ------------------------------------------------------------------ update (every frame)
static int wheel_hit_any(const SwModOverlay *o, const int *sc, int n) {
    for (int i = 0; i < n; i++)
        if (swmod_key_hit(o, sc[i])) return 1;
    return 0;
}
static int wheel_held_any(const SwModOverlay *o, const int *sc, int n) {
    for (int i = 0; i < n; i++)
        if (swmod_key_held(o, sc[i])) return 1;
    return 0;
}
#define WCOUNT(a) (int)(sizeof(a) / sizeof((a)[0]))

// Whether the wheel may be up now ("" yes, else why not: also shown in `emote state`).
static const char *wheel_blocker(const SwModOverlay *o) {
    if (!o->drawable) return "it can't draw";
    if (!o->focused) return "the game window isn't in front";
    if (o->sw_menu) return "Savage Wind's menu or console is open";
    if (o->title) return "the title screen";
    if (o->paused || o->game_menu) return "a game menu is open";
    if (o->event) return "a cutscene";
    lock();
    double at = g_snap.tick_at;
    unlock();
    double t = now_s();
    if (at <= 0 || t - at > WHEEL_STALE_S || t < at) return "Link isn't in the world";
    return "";
}

static unsigned wheel_update(const SwModOverlay *o, void *user) {
    (void)user;
    if (!o || o->size < sizeof(SwModOverlay)) return 0;
    double now = o->time;
    wheel_settings(now);

    // the toast: the tick names what started, or why it didn't (the first one seen is only remembered)
    char toast[128];
    lock();
    uint32_t tn = g_snap.toast_n;
    memcpy(toast, g_snap.toast, sizeof toast);
    unlock();
    toast[sizeof toast - 1] = 0;
    if (!wh.toast_primed) wh.toast_primed = 1, wh.toast_seen = tn;
    if (tn != wh.toast_seen) {
        wh.toast_seen = tn;
        if (wh.show_names && toast[0]) memcpy(wh.toast, toast, sizeof toast), wh.toast_at = now;
    }

    const char *block = wheel_blocker(o);
    int blocked = block[0] != 0 || !wh.on;
    if (wh.open && blocked) wheel_close();

    // open: the wheel key (held: let go to play; tapped: stays open), D-pad Down held, or `emote wheel`
    int key = o->focused && wh.vk > 0 && wh.vk < 256 && key_is_down(wh.vk);
    if (key && !wh.key_was) {
        wh.key_since = now;
        if (!wh.open && !blocked) wheel_open(now, 0, 0), wh.key_opened = 1;
        else if (wh.open && !wh.key_opened) wheel_close();  // (pressed again while it stays open: close)
    } else if (!key && wh.key_was && wh.open && wh.key_opened) {
        wh.key_opened = 0;
        if (now - wh.key_since < WHEEL_TAP_S) wh.sticky = 1;
        else if (!wheel_pick(wh.hl)) wheel_close();
    }
    wh.key_was = key;

    int dpad = wh.pad_on && (o->pad_held >> SWMOD_PAD_DPAD_DOWN & 1u);
    if (dpad && !wh.pad_was) wh.pad_armed = 1, wh.pad_since = now;
    if (!dpad) wh.pad_armed = 0;
    if (!wh.open && dpad && wh.pad_armed && now - wh.pad_since >= WHEEL_PAD_HOLD_S && !blocked) {
        wheel_open(now, 0, 1);
        wh.pad_opened = 1, wh.pad_armed = 0;
    } else if (wh.open && wh.pad_opened && !dpad) {
        wh.pad_opened = 0;
        if (!wheel_pick(wh.hl)) wheel_close();
    }
    wh.pad_was = dpad;

    if (__atomic_exchange_n(&g_wheel_cmd, 0, __ATOMIC_ACQ_REL) && !wh.open && !blocked) wheel_open(now, 1, 0);

    if (wh.open) {
        WGeo g = wheel_geo(o);
        // which device: the hints follow the last one used
        if (o->pad_hit || __builtin_fabsf(o->pad_axes[0]) > 0.5f || __builtin_fabsf(o->pad_axes[1]) > 0.5f ||
            __builtin_fabsf(o->pad_axes[2]) > 0.5f || __builtin_fabsf(o->pad_axes[3]) > 0.5f)
            wh.by_pad = 1;
        if (o->mouse_hit || o->mouse_dx != 0 || o->mouse_dy != 0 || o->keys_hit[0] || o->keys_hit[1] ||
            o->keys_hit[2])
            wh.by_pad = 0;
        // pages
        wh.wheel_acc += o->wheel;
        int dp = 0;
        while (wh.wheel_acc >= 1.0f) dp--, wh.wheel_acc -= 1.0f;
        while (wh.wheel_acc <= -1.0f) dp++, wh.wheel_acc += 1.0f;
        static const int PREV[] = {SWMOD_SC_Q}, NEXT[] = {SWMOD_SC_R, SWMOD_SC_TAB};
        if (wheel_hit_any(o, PREV, WCOUNT(PREV)) || (o->pad_hit >> SWMOD_PAD_L & 1u)) dp--;
        if (wheel_hit_any(o, NEXT, WCOUNT(NEXT)) || (o->pad_hit >> SWMOD_PAD_R & 1u)) dp++;
        if (dp && wh.npages > 0) {
            wh.page = ((wh.page + dp) % wh.npages + wh.npages) % wh.npages;
            if (wh.hl >= 0 && wh.pages[wh.page].e[wh.hl].ix == -2) wh.hl = -1;
        }
        // pointing: the mouse moves its own pointer (back in the middle: nothing); keys and sticks point at once
        if (o->mouse_dx != 0 || o->mouse_dy != 0) {
            wh.vx += o->mouse_dx, wh.vy += o->mouse_dy;
            float d = __builtin_sqrtf(wh.vx * wh.vx + wh.vy * wh.vy);
            if (d > g.ro) wh.vx *= g.ro / d, wh.vy *= g.ro / d, d = g.ro;
            wh.hl = d > 0.45f * g.ri ? wheel_slice_at(wh.vx, wh.vy) : -1;
        }
        static const int UP[] = {SWMOD_SC_W, SWMOD_SC_UP}, DOWN[] = {SWMOD_SC_S, SWMOD_SC_DOWN};
        static const int LEFT[] = {SWMOD_SC_A, SWMOD_SC_LEFT}, RIGHT[] = {SWMOD_SC_D, SWMOD_SC_RIGHT};
        float kx = (float)(wheel_held_any(o, RIGHT, 2) - wheel_held_any(o, LEFT, 2));
        float ky = (float)(wheel_held_any(o, DOWN, 2) - wheel_held_any(o, UP, 2));
        if (kx != 0 || ky != 0) {
            wh.hl = wheel_slice_at(kx, ky);
            float d = __builtin_sqrtf(kx * kx + ky * ky), r = 0.5f * (g.ri + g.ro);
            wh.vx = kx / d * r, wh.vy = ky / d * r;
        }
        float lx = o->pad_axes[0], ly = o->pad_axes[1], rx = o->pad_axes[2], ry = o->pad_axes[3];
        float lm = lx * lx + ly * ly, rm = rx * rx + ry * ry;
        if (lm > 0.25f || rm > 0.25f) {  // (the mouse's pointer follows, so a nudge of the mouse keeps the slice)
            float sx = lm >= rm ? lx : rx, sy = lm >= rm ? ly : ry, d = __builtin_sqrtf(lm >= rm ? lm : rm);
            float r = 0.5f * (g.ri + g.ro);
            wh.hl = wheel_slice_at(sx, sy);
            wh.vx = sx / d * r, wh.vy = sy / d * r;
        }
        // play, ragdoll, close
        for (int i = 0; i < WHEEL_SLICES && wh.open; i++)
            if (swmod_key_hit(o, SWMOD_SC_1 + i) || swmod_key_hit(o, SWMOD_SC_KP_1 + i)) {
                if (wh.pages[wh.page].e[i].ix != -2) wh.hl = i;
                wheel_pick(i);
            }
        static const int PLAY[] = {SWMOD_SC_RETURN, SWMOD_SC_KP_ENTER, SWMOD_SC_SPACE, SWMOD_SC_E};
        static const int CLOSE[] = {SWMOD_SC_ESCAPE, SWMOD_SC_BACKSPACE};
        static const int RAG[] = {SWMOD_SC_0, SWMOD_SC_KP_0};
        if (wh.open && (wheel_hit_any(o, RAG, WCOUNT(RAG)) || (o->pad_hit >> SWMOD_PAD_WEST & 1u))) wheel_ragdoll();
        if (wh.open && ((o->mouse_hit & 1u) || wheel_hit_any(o, PLAY, WCOUNT(PLAY)) ||
                        (o->pad_hit >> SWMOD_PAD_EAST & 1u)))
            wheel_pick(wh.hl);
        if (wh.open && ((o->mouse_hit & 4u) || wheel_hit_any(o, CLOSE, WCOUNT(CLOSE)) ||
                        (o->pad_hit >> SWMOD_PAD_SOUTH & 1u)))
            wheel_close();
    }
    unsigned want = 0;
    if (wh.open) want |= SWMOD_OVL_DRAW | SWMOD_OVL_CAPTURE;
    if (now - wh.toast_at < WHEEL_TOAST_S && now >= wh.toast_at) want |= SWMOD_OVL_DRAW;
    return want;
}

// ------------------------------------------------------------------ drawing
#define WCOL SWMOD_RGBA
static const uint32_t W_WHITE = WCOL(255, 255, 255, 255), W_SHADOW = WCOL(0, 0, 0, 220);
static const uint32_t W_GOLD = WCOL(232, 184, 74, 255), W_BLACK = WCOL(12, 12, 12, 255);
static const uint32_t W_SLICE = WCOL(18, 20, 24, 222), W_EMPTY = WCOL(18, 20, 24, 150);
static const uint32_t W_EDGE = WCOL(245, 245, 245, 150), W_GREY = WCOL(205, 205, 205, 255);
static const uint32_t W_WARN = WCOL(255, 170, 70, 255);

static void w_text_at(const SwModOverlay *o, float x, float y, float px, uint32_t col, int shadow, const char *s) {
    float d = px / 20.0f;
    d = d < 1.5f ? 1.5f : d;
    if (shadow) o->text(x + d, y + d, px, W_SHADOW, s);
    o->text(x, y, px, col, s);
}
static void w_text_c(const SwModOverlay *o, float cx, float y, float px, uint32_t col, int shadow, const char *s) {
    w_text_at(o, cx - 0.5f * o->text_width(px, s), y, px, col, shadow, s);
}
// s in at most two lines of at most maxw (split at the space that balances them), px tall or smaller (minpx at least)
#define W_LINE 128
static float w_fit(const SwModOverlay *o, const char *s, float px, float minpx, float maxw, char l1[W_LINE],
                   char l2[W_LINE]) {
    snprintf(l1, W_LINE, "%s", s);
    l2[0] = 0;
    float w = o->text_width(px, s);
    if (w <= maxw) return px;
    int best = -1;
    float best_w = w;
    for (int i = 1; s[i] && i < W_LINE - 1; i++) {
        if (s[i] != ' ') continue;
        char a[W_LINE], b[W_LINE];
        snprintf(a, sizeof a, "%.*s", i, s);
        snprintf(b, sizeof b, "%s", s + i + 1);
        float wa = o->text_width(px, a), wb = o->text_width(px, b), m = wa > wb ? wa : wb;
        if (m < best_w) best_w = m, best = i;
    }
    if (best > 0) {
        snprintf(l1, W_LINE, "%.*s", best, s);
        snprintf(l2, W_LINE, "%s", s + best + 1);
        w = best_w;
    }
    if (w <= maxw || w <= 0) return px;
    float p = px * maxw / w;
    return p < minpx ? minpx : p;
}
// a name, centred on cx, its block's top at y; returns its height
static float w_name(const SwModOverlay *o, float cx, float y, float px, float minpx, float maxw, uint32_t col,
                    int shadow, const char *s) {
    char l1[W_LINE], l2[W_LINE];
    float p = w_fit(o, s, px, minpx, maxw, l1, l2);
    w_text_c(o, cx, y, p, col, shadow, l1);
    if (l2[0]) w_text_c(o, cx, y + p * 1.0f, p, col, shadow, l2);
    return l2[0] ? 2.0f * p : p;
}
static float w_name_height(const SwModOverlay *o, const char *s, float px, float minpx, float maxw) {
    char l1[W_LINE], l2[W_LINE];
    float p = w_fit(o, s, px, minpx, maxw, l1, l2);
    return l2[0] ? 2.0f * p : p;
}

// ---- icons: our own simple shapes, in a box from -1 to 1 (y down) around cx cy, s = half its size
typedef struct {
    const SwModOverlay *o;
    float cx, cy, s;
    uint32_t fg, bg;
} Ic;
static float icx(const Ic *c, float x) { return c->cx + x * c->s; }
static float icy(const Ic *c, float y) { return c->cy + y * c->s; }
static uint32_t icol(const Ic *c, int bg) { return bg ? c->bg : c->fg; }
static void ic_line(const Ic *c, float x0, float y0, float x1, float y1, float t, int bg) {
    c->o->line(icx(c, x0), icy(c, y0), icx(c, x1), icy(c, y1), icol(c, bg), t * c->s);
}
static void ic_dot(const Ic *c, float x, float y, float r, int bg) {
    c->o->circle_filled(icx(c, x), icy(c, y), r * c->s, icol(c, bg));
}
static void ic_ring(const Ic *c, float x, float y, float r, float t, int bg) {
    c->o->circle(icx(c, x), icy(c, y), r * c->s, icol(c, bg), t * c->s);
}
static void ic_box(const Ic *c, float x0, float y0, float x1, float y1, float round, int bg) {
    c->o->rect_filled(icx(c, x0), icy(c, y0), icx(c, x1), icy(c, y1), icol(c, bg), round * c->s);
}
static void ic_tri(const Ic *c, float x0, float y0, float x1, float y1, float x2, float y2, int bg) {
    c->o->triangle_filled(icx(c, x0), icy(c, y0), icx(c, x1), icy(c, y1), icx(c, x2), icy(c, y2), icol(c, bg));
}
static void ic_poly(const Ic *c, const float *pts, int n, int bg) {
    float xy[48];
    if (n > 24) n = 24;
    for (int i = 0; i < n; i++) xy[2 * i] = icx(c, pts[2 * i]), xy[2 * i + 1] = icy(c, pts[2 * i + 1]);
    c->o->poly_filled(xy, n, icol(c, bg));
}
static void ic_text(const Ic *c, const char *t, float px) {
    float p = px * c->s;
    c->o->text(c->cx - 0.5f * c->o->text_width(p, t), c->cy - 0.55f * p, p, c->fg, t);
}
static void ic_stick(const Ic *c, float hx, float hy) {  // a stick figure's head, from which the rest is drawn
    ic_dot(c, hx, hy, 0.2f, 0);
}

static void wheel_icon(const SwModOverlay *o, const char *id, float cx, float cy, float s, uint32_t fg, uint32_t bg) {
    Ic c = {o, cx, cy, s, fg, bg};
    const Ic *k = &c;
    if (!strcmp(id, "wave")) {  // an open hand, waving
        ic_box(k, -0.38f, -0.1f, 0.34f, 0.72f, 0.18f, 0);
        for (int i = 0; i < 4; i++) {  // the fingers (the outer two shorter)
            float tip = -0.62f + (i == 0 || i == 3 ? 0.12f : 0.0f);
            ic_line(k, -0.27f + 0.18f * i, 0.0f, -0.29f + 0.2f * i, tip, 0.15f, 0);
        }
        ic_line(k, -0.3f, 0.35f, -0.72f, 0.0f, 0.16f, 0);
        ic_line(k, 0.6f, -0.62f, 0.82f, -0.8f, 0.09f, 0);
        ic_line(k, 0.62f, -0.3f, 0.92f, -0.36f, 0.09f, 0);
    } else if (!strcmp(id, "thumbs_up") || !strcmp(id, "thumbs_down")) {  // a fist, the thumb up or down
        float f = !strcmp(id, "thumbs_up") ? 1.0f : -1.0f;
        ic_box(k, -0.45f, f > 0 ? -0.05f : -0.65f, 0.3f, f > 0 ? 0.65f : 0.05f, 0.16f, 0);
        ic_box(k, -0.3f, f > 0 ? -0.85f : -0.0f, 0.0f, f > 0 ? 0.0f : 0.85f, 0.14f, 0);
        ic_box(k, 0.36f, f > 0 ? 0.0f : -0.6f, 0.62f, f > 0 ? 0.6f : 0.0f, 0.06f, 0);
        for (int i = 0; i < 3; i++) {  // the curled fingers
            float y = (f > 0 ? 0.15f : -0.5f) + 0.17f * i;
            ic_line(k, -0.05f, y, 0.25f, y, 0.05f, 1);
        }
    } else if (!strcmp(id, "clap")) {  // two hands meeting, with sparks
        ic_box(k, -0.46f, -0.42f, -0.04f, 0.7f, 0.18f, 0);
        ic_box(k, 0.04f, -0.42f, 0.46f, 0.7f, 0.18f, 0);
        ic_line(k, 0.0f, -0.6f, 0.0f, -0.92f, 0.1f, 0);
        ic_line(k, -0.38f, -0.58f, -0.6f, -0.85f, 0.1f, 0);
        ic_line(k, 0.38f, -0.58f, 0.6f, -0.85f, 0.1f, 0);
    } else if (!strcmp(id, "point")) {  // a fist, the finger out
        ic_dot(k, -0.4f, 0.15f, 0.38f, 0);
        ic_box(k, -0.15f, -0.08f, 0.82f, 0.16f, 0.12f, 0);
        ic_tri(k, 0.62f, -0.4f, 0.98f, 0.04f, 0.62f, 0.48f, 0);
    } else if (!strcmp(id, "shrug")) {  // palms up, shoulders up
        ic_stick(k, 0.0f, -0.45f);
        ic_line(k, 0.0f, -0.25f, 0.0f, 0.45f, 0.13f, 0);
        float a[] = {-0.85f, -0.6f, -0.55f, -0.05f, 0.55f, -0.05f, 0.85f, -0.6f};
        float xy[8];
        for (int i = 0; i < 4; i++) xy[2 * i] = icx(k, a[2 * i]), xy[2 * i + 1] = icy(k, a[2 * i + 1]);
        o->polyline(xy, 4, fg, false, 0.13f * s);
        ic_line(k, 0.0f, 0.45f, -0.3f, 0.92f, 0.13f, 0);
        ic_line(k, 0.0f, 0.45f, 0.3f, 0.92f, 0.13f, 0);
    } else if (!strcmp(id, "facepalm")) {  // a face, a hand over it
        ic_ring(k, 0.0f, 0.0f, 0.62f, 0.12f, 0);
        ic_box(k, -0.58f, -0.42f, 0.3f, 0.02f, 0.16f, 0);
        ic_line(k, 0.25f, -0.2f, 0.85f, 0.85f, 0.2f, 0);
    } else if (!strcmp(id, "bow")) {  // bent from the waist
        ic_line(k, -0.35f, -0.25f, -0.35f, 0.92f, 0.15f, 0);
        ic_line(k, -0.35f, -0.25f, 0.35f, 0.15f, 0.15f, 0);
        ic_line(k, 0.0f, -0.05f, 0.1f, 0.45f, 0.12f, 0);
        ic_dot(k, 0.55f, 0.3f, 0.22f, 0);
    } else if (!strcmp(id, "floss")) {  // arms swung to one side, hips the other
        ic_stick(k, 0.05f, -0.7f);
        ic_line(k, 0.05f, -0.48f, 0.15f, 0.2f, 0.13f, 0);
        ic_line(k, 0.05f, -0.35f, -0.7f, 0.05f, 0.12f, 0);
        ic_line(k, 0.05f, -0.35f, -0.55f, 0.45f, 0.12f, 0);
        ic_line(k, 0.15f, 0.2f, -0.15f, 0.92f, 0.13f, 0);
        ic_line(k, 0.15f, 0.2f, 0.45f, 0.92f, 0.13f, 0);
    } else if (!strcmp(id, "six_seven")) {  // the meme's numbers
        ic_text(k, "67", 1.5f);
    } else if (!strcmp(id, "in_love")) {  // a heart
        ic_dot(k, -0.33f, -0.2f, 0.38f, 0);
        ic_dot(k, 0.33f, -0.2f, 0.38f, 0);
        ic_tri(k, -0.69f, -0.05f, 0.69f, -0.05f, 0.0f, 0.8f, 0);
    } else if (!strcmp(id, "robot")) {  // a robot's head
        ic_box(k, -0.6f, -0.45f, 0.6f, 0.55f, 0.12f, 0);
        ic_dot(k, -0.27f, 0.0f, 0.13f, 1);
        ic_dot(k, 0.27f, 0.0f, 0.13f, 1);
        ic_box(k, -0.28f, 0.25f, 0.28f, 0.35f, 0.0f, 1);
        ic_line(k, 0.0f, -0.45f, 0.0f, -0.75f, 0.08f, 0);
        ic_dot(k, 0.0f, -0.82f, 0.11f, 0);
    } else if (!strcmp(id, "moonwalk")) {  // a hat and a gliding shoe
        float crown[] = {-0.45f, 0.0f, -0.35f, -0.65f, 0.35f, -0.65f, 0.45f, 0.0f};
        ic_poly(k, crown, 4, 0);
        ic_box(k, -0.85f, -0.05f, 0.85f, 0.12f, 0.06f, 0);
        ic_box(k, -0.42f, -0.2f, 0.42f, -0.08f, 0.0f, 1);
        ic_box(k, -0.2f, 0.45f, 0.75f, 0.68f, 0.1f, 0);
        ic_line(k, -0.85f, 0.5f, -0.4f, 0.5f, 0.07f, 0);
        ic_line(k, -0.75f, 0.68f, -0.35f, 0.68f, 0.07f, 0);
    } else if (!strcmp(id, "air_guitar")) {  // a guitar
        ic_line(k, -0.05f, 0.1f, 0.72f, -0.68f, 0.14f, 0);
        ic_box(k, 0.6f, -0.92f, 0.92f, -0.6f, 0.06f, 0);
        ic_dot(k, -0.38f, 0.42f, 0.4f, 0);
        ic_dot(k, -0.05f, 0.08f, 0.3f, 0);
        ic_dot(k, -0.25f, 0.3f, 0.11f, 1);
    } else if (!strcmp(id, "korok_hop")) {  // a leaf
        float leaf[] = {0.0f, -0.88f, 0.45f, -0.38f, 0.52f, 0.12f, 0.22f, 0.52f, 0.0f, 0.6f,
                        -0.22f, 0.52f, -0.52f, 0.12f, -0.45f, -0.38f};
        ic_poly(k, leaf, 8, 0);
        ic_line(k, 0.0f, -0.6f, 0.0f, 0.45f, 0.06f, 1);
        ic_line(k, 0.0f, 0.55f, 0.05f, 0.95f, 0.1f, 0);
    } else if (!strcmp(id, "hestu_shake")) {  // two maracas
        ic_dot(k, -0.38f, -0.38f, 0.32f, 0);
        ic_dot(k, 0.4f, -0.22f, 0.32f, 0);
        ic_line(k, -0.28f, -0.1f, -0.08f, 0.85f, 0.12f, 0);
        ic_line(k, 0.3f, 0.06f, 0.15f, 0.9f, 0.12f, 0);
        ic_line(k, -0.7f, -0.8f, -0.85f, -0.95f, 0.07f, 0);
        ic_line(k, 0.75f, -0.68f, 0.92f, -0.82f, 0.07f, 0);
    } else if (!strcmp(id, "salute")) {  // a guard's shield
        float shield[] = {-0.62f, -0.75f, 0.62f, -0.75f, 0.62f, 0.0f, 0.0f, 0.85f, -0.62f, 0.0f};
        ic_poly(k, shield, 5, 0);
        ic_line(k, 0.0f, -0.55f, 0.0f, 0.5f, 0.12f, 1);
        ic_line(k, -0.4f, -0.2f, 0.4f, -0.2f, 0.12f, 1);
    } else if (!strcmp(id, "revali_smug")) {  // a feather
        float feather[] = {0.62f, -0.82f, 0.45f, -0.05f, -0.1f, 0.55f, -0.62f, 0.8f, -0.38f, 0.28f, 0.1f, -0.4f};
        ic_poly(k, feather, 6, 0);
        ic_line(k, 0.55f, -0.7f, -0.75f, 0.92f, 0.07f, 1);
        ic_line(k, -0.55f, 0.75f, -0.8f, 0.97f, 0.08f, 0);
    } else if (!strcmp(id, "daruk_flex")) {  // a flexed arm
        ic_line(k, -0.85f, 0.55f, 0.05f, 0.55f, 0.32f, 0);
        ic_line(k, 0.05f, 0.55f, 0.18f, -0.3f, 0.3f, 0);
        ic_dot(k, 0.2f, -0.5f, 0.28f, 0);
        ic_dot(k, -0.3f, 0.25f, 0.36f, 0);
    } else if (!strcmp(id, "urbosa_snap")) {  // a lightning bolt
        float bolt[] = {0.28f, -0.92f, 0.32f, -0.92f, 0.05f, -0.18f, 0.48f, -0.18f,
                        -0.2f, 0.92f, -0.02f, 0.12f, -0.45f, 0.12f};
        ic_poly(k, bolt, 7, 0);
    } else if (!strcmp(id, "mipha_wave")) {  // a water drop
        ic_dot(k, 0.0f, 0.25f, 0.52f, 0);
        ic_tri(k, 0.0f, -0.9f, 0.48f, 0.05f, -0.48f, 0.05f, 0);
        ic_dot(k, -0.18f, 0.3f, 0.12f, 1);
    } else if (!strcmp(id, "selfie")) {  // a camera
        ic_box(k, -0.8f, -0.42f, 0.8f, 0.6f, 0.14f, 0);
        ic_box(k, -0.32f, -0.65f, 0.18f, -0.35f, 0.06f, 0);
        ic_dot(k, 0.0f, 0.08f, 0.36f, 1);
        ic_dot(k, 0.0f, 0.08f, 0.2f, 0);
    } else if (!strcmp(id, "snack")) {  // an apple
        ic_dot(k, -0.24f, 0.15f, 0.48f, 0);
        ic_dot(k, 0.24f, 0.15f, 0.48f, 0);
        ic_line(k, 0.0f, -0.3f, 0.08f, -0.75f, 0.1f, 0);
        ic_tri(k, 0.1f, -0.55f, 0.5f, -0.78f, 0.35f, -0.45f, 0);
    } else if (!strcmp(id, "victory")) {  // a star
        float star[20];
        for (int i = 0; i < 10; i++) {
            float a = -WHEEL_PI / 2 + i * WHEEL_PI / 5, r = i % 2 ? 0.4f : 0.95f;
            star[2 * i] = r * w_cos(a), star[2 * i + 1] = r * w_sin(a) + 0.05f;
        }
        ic_poly(k, star, 10, 0);
    } else if (!strcmp(id, "archer")) {  // a bow and an arrow
        float a[18];
        for (int i = 0; i < 9; i++) {  // the bow's limb, an arc on the left
            float t = -1.1f + 2.2f * i / 8;
            a[2 * i] = -0.2f - 0.55f * w_cos(t), a[2 * i + 1] = 0.85f * w_sin(t);
        }
        float xy[18];
        for (int i = 0; i < 9; i++) xy[2 * i] = icx(k, a[2 * i]), xy[2 * i + 1] = icy(k, a[2 * i + 1]);
        o->polyline(xy, 9, fg, false, 0.12f * s);
        ic_line(k, a[0], a[1], a[16], a[17], 0.04f, 0);
        ic_line(k, -0.45f, 0.0f, 0.75f, 0.0f, 0.08f, 0);
        ic_tri(k, 0.95f, 0.0f, 0.68f, -0.16f, 0.68f, 0.16f, 0);
    } else if (!strcmp(id, "sword_up") || !strcmp(id, "sword_pose") || !strcmp(id, "big_sword")) {  // a sword
        float w = !strcmp(id, "big_sword") ? 0.13f : 0.09f, tilt = !strcmp(id, "sword_pose") ? 0.45f : 0.0f;
        ic_line(k, -tilt * 0.6f, 0.45f, tilt, -0.9f, 2 * w, 0);         // the blade
        ic_line(k, -0.38f - tilt * 0.4f, 0.48f + tilt * 0.3f, 0.38f - tilt * 0.4f, 0.48f - tilt * 0.3f, 0.12f, 0);
        ic_line(k, -tilt * 0.7f, 0.5f, -tilt * 0.95f, 0.88f, 0.13f, 0);  // the grip
        ic_dot(k, -tilt * 0.98f, 0.93f, 0.1f, 0);
    } else if (!strcmp(id, "spear_pose")) {  // a spear
        ic_line(k, -0.7f, 0.85f, 0.55f, -0.55f, 0.1f, 0);
        ic_tri(k, 0.9f, -0.9f, 0.38f, -0.6f, 0.62f, -0.36f, 0);
    } else if (!strcmp(id, "hip_pose") || !strcmp(id, "hand_up") || !strcmp(id, "chin_pose")) {  // posing figures
        ic_stick(k, 0.0f, -0.62f);
        ic_line(k, 0.0f, -0.4f, 0.0f, 0.3f, 0.13f, 0);
        ic_line(k, 0.0f, 0.3f, -0.3f, 0.92f, 0.13f, 0);
        ic_line(k, 0.0f, 0.3f, 0.3f, 0.92f, 0.13f, 0);
        if (!strcmp(id, "hip_pose")) {  // both hands on the hips
            ic_line(k, 0.0f, -0.28f, -0.42f, 0.0f, 0.12f, 0), ic_line(k, -0.42f, 0.0f, -0.05f, 0.22f, 0.12f, 0);
            ic_line(k, 0.0f, -0.28f, 0.42f, 0.0f, 0.12f, 0), ic_line(k, 0.42f, 0.0f, 0.05f, 0.22f, 0.12f, 0);
        } else if (!strcmp(id, "hand_up")) {  // one hand high
            ic_line(k, 0.0f, -0.28f, 0.45f, -0.95f, 0.12f, 0);
            ic_line(k, 0.0f, -0.28f, -0.4f, 0.15f, 0.12f, 0);
        } else {  // a hand under the chin
            ic_line(k, 0.0f, -0.28f, 0.4f, -0.05f, 0.12f, 0), ic_line(k, 0.4f, -0.05f, 0.12f, -0.42f, 0.12f, 0);
            ic_line(k, 0.0f, -0.28f, -0.4f, 0.15f, 0.12f, 0);
        }
    } else if (!strcmp(id, "ragdoll")) {  // flat on the ground, seeing stars
        ic_dot(k, -0.65f, 0.4f, 0.2f, 0);
        ic_line(k, -0.45f, 0.45f, 0.3f, 0.5f, 0.14f, 0);
        ic_line(k, 0.3f, 0.5f, 0.85f, 0.25f, 0.13f, 0);
        ic_line(k, 0.3f, 0.5f, 0.9f, 0.7f, 0.13f, 0);
        ic_line(k, -0.2f, 0.47f, -0.1f, 0.0f, 0.12f, 0);
        ic_dot(k, -0.75f, -0.15f, 0.09f, 0);
        ic_dot(k, -0.45f, -0.35f, 0.09f, 0);
        ic_dot(k, -0.15f, -0.2f, 0.09f, 0);
    } else {  // an emote of your own
        ic_ring(k, 0.0f, 0.0f, 0.75f, 0.12f, 0);
        ic_text(k, "?", 1.2f);
    }
}

// one ring slice (the annulus between ri and ro, a0 .. a1 clockwise from the top)
static void w_slice_points(const WGeo *g, float a0, float a1, float ri, float ro, float *xy, int *n) {
    const int steps = 10;
    int k = 0;
    for (int i = 0; i <= steps; i++) {
        float a = a0 + (a1 - a0) * i / steps;
        xy[k++] = g->cx + ro * w_sin(a), xy[k++] = g->cy - ro * w_cos(a);
    }
    for (int i = steps; i >= 0; i--) {
        float a = a0 + (a1 - a0) * i / steps;
        xy[k++] = g->cx + ri * w_sin(a), xy[k++] = g->cy - ri * w_cos(a);
    }
    *n = k / 2;
}

static void wheel_draw_ring(const SwModOverlay *o, const WGeo *g) {
    const WPage *pg = &wh.pages[wh.page];
    const float span = 2 * WHEEL_PI / WHEEL_SLICES;
    float xy[2 * 22];
    int n;
    for (int pass = 0; pass < 2; pass++)  // the lit one last, popped out
        for (int i = 0; i < WHEEL_SLICES; i++) {
            int lit = i == wh.hl && pg->e[i].ix != -2;
            if (lit != pass) continue;
            float a = i * span, ro = lit ? g->ro + 16.0f * g->s : g->ro;
            w_slice_points(g, a - span / 2, a + span / 2, g->ri, ro, xy, &n);
            o->poly_filled(xy, n, lit ? W_GOLD : pg->e[i].ix == -2 ? W_EMPTY : W_SLICE);
            o->polyline(xy, n, lit ? W_WHITE : W_EDGE, true, (lit ? 5.0f : 3.0f) * g->s);
            float rc = 0.5f * (g->ri + ro), x = g->cx + rc * w_sin(a), y = g->cy - rc * w_cos(a);
            uint32_t fg = lit ? W_BLACK : W_WHITE;
            // the number key, just outside the rim
            float rb = ro + 26.0f * g->s, bx = g->cx + rb * w_sin(a), by = g->cy - rb * w_cos(a);
            char num[4];
            snprintf(num, sizeof num, "%d", i + 1);
            o->circle_filled(bx, by, 20.0f * g->s, lit ? W_GOLD : WCOL(0, 0, 0, 200));
            o->circle(bx, by, 20.0f * g->s, lit ? W_WHITE : W_EDGE, 2.0f * g->s);
            w_text_c(o, bx, by - 15.0f * g->s, 30.0f * g->s, lit ? W_BLACK : W_WHITE, 0, num);
            const WEntry *e = &pg->e[i];
            if (e->ix == -2) continue;
            // the icon above the name, the block centred in the slice
            const char *name = wheel_entry_name(e);
            float isz = 40.0f * g->s, gap = 10.0f * g->s, maxw = 210.0f * g->s;
            float nh = w_name_height(o, name, 40.0f * g->s, 26.0f * g->s, maxw);
            float top = y - 0.5f * (2 * isz + gap + nh);
            wheel_icon(o, e->ix >= 0 ? EMOTES[e->ix].id : "", x, top + isz, isz, fg, lit ? W_GOLD : W_SLICE);
            w_name(o, x, top + 2 * isz + gap, 40.0f * g->s, 26.0f * g->s, maxw, fg, !lit, name);
        }
    // the middle: what is lit, big
    float rc = g->ri - 8.0f * g->s;
    o->circle_filled(g->cx, g->cy, rc, WCOL(8, 9, 11, 238));
    o->circle(g->cx, g->cy, rc, W_EDGE, 3.0f * g->s);
    const WEntry *e = wh.hl >= 0 ? &pg->e[wh.hl] : NULL;
    if (e && e->ix != -2) {
        const char *name = wheel_entry_name(e);
        float isz = 46.0f * g->s, gap = 8.0f * g->s, maxw = 1.7f * rc;
        float nh = w_name_height(o, name, 54.0f * g->s, 32.0f * g->s, maxw);
        float top = g->cy - 0.5f * (2 * isz + gap + nh);
        wheel_icon(o, e->ix >= 0 ? EMOTES[e->ix].id : "", g->cx, top + isz, isz, W_GOLD, WCOL(8, 9, 11, 255));
        w_name(o, g->cx, top + 2 * isz + gap, 54.0f * g->s, 32.0f * g->s, maxw, W_WHITE, 1, name);
        // an arrow on the middle's rim toward the lit slice
        float a = wh.hl * span, r0 = rc + 2.0f * g->s, r1 = rc + 26.0f * g->s, w = 0.12f;
        o->triangle_filled(g->cx + r1 * w_sin(a), g->cy - r1 * w_cos(a), g->cx + r0 * w_sin(a + w),
                           g->cy - r0 * w_cos(a + w), g->cx + r0 * w_sin(a - w), g->cy - r0 * w_cos(a - w), W_GOLD);
    } else {
        w_name(o, g->cx, g->cy - 34.0f * g->s, 34.0f * g->s, 24.0f * g->s, 1.7f * rc, W_GREY, 1, "Point at one");
    }
    if (!wh.by_pad && wh.hl < 0 && (wh.vx != 0 || wh.vy != 0)) {  // the mouse's pointer, while nothing is lit
        o->circle_filled(g->cx + wh.vx, g->cy + wh.vy, 10.0f * g->s, W_GOLD);
        o->circle(g->cx + wh.vx, g->cy + wh.vy, 10.0f * g->s, W_BLACK, 2.5f * g->s);
    }
}

static const char *wheel_key_name(char buf[8]) {
    int vk = wh.vk;
    if ((vk >= 'A' && vk <= 'Z') || (vk >= '0' && vk <= '9')) snprintf(buf, 8, "%c", vk);
    else if (vk >= 0x70 && vk <= 0x7b) snprintf(buf, 8, "F%d", vk - 0x6f);
    else snprintf(buf, 8, "the key");
    return buf;
}

// the page (left of the wheel) and the controls (right), in big lines; a narrow window: the page above the wheel and
// the controls in one or two lines below it
static void wheel_draw_sides(const SwModOverlay *o, const WGeo *g, const char *cant) {
    float lx = 30.0f * g->u, px = 36.0f * g->s, minpx = 22.0f * g->u;
    float side = 0.5f * o->w - g->ro - 46.0f * g->s - 50.0f * g->u;  // (beside the number badges)
    float cxl = lx + 0.5f * side, cxr = o->w - lx - 0.5f * side, wl = side;
    const WPage *pg = &wh.pages[wh.page];
    char line[64], k[8], l0[48], cantc[112];
    snprintf(cantc, sizeof cantc, "%s", cant);
    if (cantc[0] >= 'a' && cantc[0] <= 'z') cantc[0] = (char)(cantc[0] - 32);
    const char *lines[6];
    int n = 0;
    if (wh.by_pad) {
        lines[n++] = wh.pad_opened ? "Let go of D-pad: play" : "A: play";
        lines[n++] = "Y: ragdoll";
        lines[n++] = "B: close";
    } else {
        if (wh.key_opened) snprintf(l0, sizeof l0, "Let go of %s: play", wheel_key_name(k)), lines[n++] = l0;
        lines[n++] = "Click: play";
        lines[n++] = "1 to 8: play that one";
        lines[n++] = "0: ragdoll";
        lines[n++] = "Esc: close";
    }
    if (g->stacked) {
        snprintf(line, sizeof line, "%s   %d / %d", pg->title, wh.page + 1, wh.npages);
        w_name(o, g->cx, 12.0f * g->u, 48.0f * g->s, minpx, o->w - 2 * lx, W_GOLD, 1, line);
        char all[200];
        size_t m = 0;
        all[0] = 0;
        for (int i = 0; i < n; i++)
            m += (size_t)snprintf(all + m, sizeof all - m, "%s%s", i ? "    " : "", lines[i]);
        float y = g->cy + g->ro + 20.0f * g->s;
        if (cantc[0]) y += w_name(o, g->cx, y, px, minpx, o->w - 2 * lx, W_WARN, 1, cantc);
        w_name(o, g->cx, y, 30.0f * g->s, minpx, o->w - 2 * lx, W_WHITE, 1, all);
        return;
    }
    float y = g->cy - 150.0f * g->s;
    y += w_name(o, cxl, y, 64.0f * g->s, 30.0f * g->u, wl, W_GOLD, 1, pg->title) + 12.0f * g->s;
    snprintf(line, sizeof line, "Page %d of %d", wh.page + 1, wh.npages);
    y += w_name(o, cxl, y, px, minpx, wl, W_WHITE, 1, line) + 14.0f * g->s;
    float dot = 11.0f * g->s, dx = 34.0f * g->s, x0 = cxl - 0.5f * dx * (wh.npages - 1);
    for (int i = 0; i < wh.npages; i++) {
        o->circle_filled(x0 + i * dx, y + dot, dot, i == wh.page ? W_GOLD : WCOL(255, 255, 255, 70));
        o->circle(x0 + i * dx, y + dot, dot, W_WHITE, 2.0f * g->s);
    }
    y += 2 * dot + 18.0f * g->s;
    y += w_name(o, cxl, y, px, minpx, wl, W_WHITE, 1, wh.by_pad ? "L / R: pages" : "Q / R: pages") + 0.3f * px;
    if (!wh.by_pad) w_name(o, cxl, y, px, minpx, wl, W_WHITE, 1, "or the mouse wheel");
    float ry = g->cy - 0.5f * n * px * 1.3f;
    for (int i = 0; i < n; i++) ry += w_name(o, cxr, ry, px, minpx, wl, W_WHITE, 1, lines[i]) + 0.3f * px;
    if (cantc[0]) w_name(o, cxr, ry + 0.4f * px, px, minpx, wl, W_WARN, 1, cantc);
}

static void wheel_draw(const SwModOverlay *o, void *user) {
    (void)user;
    if (!o || o->size < sizeof(SwModOverlay)) return;
    WGeo g = wheel_geo(o);
    if (wh.open && wh.npages > 0) {
        char cant[96];
        lock();
        memcpy(cant, g_snap.cant, sizeof cant);
        unlock();
        cant[sizeof cant - 1] = 0;
        o->rect_filled(0, 0, o->w, o->h, WCOL(0, 0, 0, 90), 0);  // the world dimmed a little: friends stay visible
        wheel_draw_ring(o, &g);
        wheel_draw_sides(o, &g, cant);
    }
    double age = o->time - wh.toast_at;
    if (age >= 0 && age < WHEEL_TOAST_S && wh.toast[0]) {
        float t = g.u * wh.size / 150.0f, px = 48.0f * t;
        float a = age > WHEEL_TOAST_S - 0.4 ? (float)((WHEEL_TOAST_S - age) / 0.4) : 1.0f;  // (fades out)
        char l1[W_LINE], l2[W_LINE];
        float maxw = 0.8f * o->w, p = w_fit(o, wh.toast, px, 28.0f * g.u, maxw, l1, l2);
        float w1 = o->text_width(p, l1), w2 = l2[0] ? o->text_width(p, l2) : 0, w = w1 > w2 ? w1 : w2;
        // (bottom centre; while the wheel is up, at the top: its own line on the right says why already)
        float hgt = (l2[0] ? 2 : 1) * p, pad = 20.0f * t, y = wh.open ? 20.0f * g.u + pad : o->h - 150.0f * t - hgt;
        o->rect_filled(0.5f * o->w - 0.5f * w - pad, y - pad, 0.5f * o->w + 0.5f * w + pad, y + hgt + pad,
                       WCOL(0, 0, 0, (int)(200 * a)), 16.0f * t);
        uint32_t col = WCOL(255, 255, 255, (int)(255 * a));
        w_text_c(o, 0.5f * o->w, y, p, col, 0, l1);
        if (l2[0]) w_text_c(o, 0.5f * o->w, y + p, p, col, 0, l2);
    }
}

// for `emote state`: up, down, or off and why
static void wheel_state_text(char *out, size_t cap) {
    int st = __atomic_load_n(&g_wheel_state, __ATOMIC_ACQUIRE);
    if (st > 0) snprintf(out, cap, "%s", __atomic_load_n(&g_wheel_up, __ATOMIC_ACQUIRE) ? "up" : "down");
    else snprintf(out, cap, "off: %s", st < 0 ? g_wheel_off : "not set up");
}
