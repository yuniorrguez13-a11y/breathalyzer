// swmod_overlay.h - draw over the game and hold its input while you do (modhost's overlay host). Include it after
// swmod.h:
//
//     #include "swmod.h"
//     #include "swmod_overlay.h"
//
//     static unsigned my_update(const SwModOverlay *o, void *user) { return open ? SWMOD_OVL_DRAW | SWMOD_OVL_CAPTURE : 0; }
//     static void my_draw(const SwModOverlay *o, void *user) { o->text(40 * o->scale, 40 * o->scale, 48 * o->scale,
//                                                                     SWMOD_RGBA(255, 255, 255, 255), "Hello"); }
//     ... in swmod_init:  char why[160];
//                         if (swmod_overlay_add(api, my_update, my_draw, NULL, why, sizeof why)) api->log("mymod: %s", why);
//
// How it works (out/research/castle-minigame-ui.md): Savage Wind 0.4.1-alpha.4 has no draw callback for mods, so
// modhost points the exe's 2 calls of menu_render and its 13 calls of menu_input_captured at small functions of its
// own, once, after checking every call's bytes (any other Savage Wind: nothing is patched and swmod_overlay_add says
// so). Each shown frame, on Savage Wind's present thread, modhost reads the keyboard, mouse and the game's controller
// from SDL3, calls every overlay's update, and only when one of them answers SWMOD_OVL_DRAW it makes one Dear ImGui
// frame of its own (Savage Wind's ImGui, its own context) and calls their draw, before Savage Wind's menu and console,
// which stay on top. While any update answers SWMOD_OVL_CAPTURE the game gets no input (keys, buttons, sticks, mouse
// camera: the way Savage Wind's own menu holds it) and the mouse cursor is free; after that the game stays muted until
// every key and button is up, plus 400 ms, so the press that closed an overlay never reaches Link.
//
// This is an export of modhost.dll (modhost 0.4.1 with the overlay host), found through the api your swmod_init got;
// it isn't a field of SwModApi, so a mod needs no newer mod API. An older modhost answers SWMOD_OVL_NO_HOST.
// Threads: update and draw run on the present thread, not a game thread: share state with your game-thread code
// through atomics or a lock, and don't call game functions from them.
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "swmod.h"

#define SWMOD_OVERLAY_EXPORT "modhost_overlay_add"

// update's answer (bits)
#define SWMOD_OVL_DRAW 1u     // draw this frame: draw is called with a frame to draw into
#define SWMOD_OVL_CAPTURE 2u  // the game gets no input now (Link stands still, the camera stops, the cursor is free)

// swmod_overlay_add's answer
#define SWMOD_OVL_OK 0
#define SWMOD_OVL_NO_HOST 1      // this modhost has no overlay host (an older modhost.dll)
#define SWMOD_OVL_UNSUPPORTED 2  // this Savage Wind isn't one modhost can draw on (0.4.1-alpha.4 only): nothing patched
#define SWMOD_OVL_LATE 3         // only from swmod_init of a mod loaded at game start (not modload)
#define SWMOD_OVL_FULL 4         // 8 overlays at most
#define SWMOD_OVL_BAD_ARGS 5     // no update given

// ImGui colours: 0xAABBGGRR
#define SWMOD_RGBA(r, g, b, a) \
    ((uint32_t)(r) | (uint32_t)(g) << 8 | (uint32_t)(b) << 16 | (uint32_t)(a) << 24)

// SDL3 scancodes of the keys most overlays want (keys[] and keys_hit are indexed by scancode)
#define SWMOD_SC_A 4
#define SWMOD_SC_D 7
#define SWMOD_SC_E 8
#define SWMOD_SC_G 10
#define SWMOD_SC_Q 20
#define SWMOD_SC_R 21
#define SWMOD_SC_S 22
#define SWMOD_SC_W 26
#define SWMOD_SC_1 30           // 1..9 are 30..38
#define SWMOD_SC_0 39
#define SWMOD_SC_RETURN 40
#define SWMOD_SC_ESCAPE 41
#define SWMOD_SC_BACKSPACE 42
#define SWMOD_SC_TAB 43
#define SWMOD_SC_SPACE 44
#define SWMOD_SC_RIGHT 79
#define SWMOD_SC_LEFT 80
#define SWMOD_SC_DOWN 81
#define SWMOD_SC_UP 82
#define SWMOD_SC_KP_ENTER 88
#define SWMOD_SC_KP_1 89        // keypad 1..9 are 89..97
#define SWMOD_SC_KP_0 98

// SDL3 gamepad buttons (bits of pad_held / pad_hit: 1u << button). Positions, not labels: on a Nintendo pad EAST is
// A, SOUTH is B, WEST is Y, NORTH is X (Savage Wind and the game confirm with EAST).
#define SWMOD_PAD_SOUTH 0
#define SWMOD_PAD_EAST 1
#define SWMOD_PAD_WEST 2
#define SWMOD_PAD_NORTH 3
#define SWMOD_PAD_BACK 4
#define SWMOD_PAD_START 6
#define SWMOD_PAD_L 9
#define SWMOD_PAD_R 10
#define SWMOD_PAD_DPAD_UP 11
#define SWMOD_PAD_DPAD_DOWN 12
#define SWMOD_PAD_DPAD_LEFT 13
#define SWMOD_PAD_DPAD_RIGHT 14

typedef struct SwModOverlay SwModOverlay;
// Every shown frame (twice with frame generation), before anything is drawn: read the input, change your state, say
// what you want this frame (SWMOD_OVL_* bits, 0: nothing). Keep it short: it runs on the present thread.
typedef unsigned (*SwModOverlayUpdate)(const SwModOverlay *o, void *user);
// Right after, only when your update answered SWMOD_OVL_DRAW: draw with o's drawing functions.
typedef void (*SwModOverlayDraw)(const SwModOverlay *o, void *user);

struct SwModOverlay {
    uint32_t size;             // sizeof(SwModOverlay) in the host (fields are only ever added at the end)
    uint32_t frame;            // goes up by one each call
    float w, h;                // the frame, in pixels
    float scale;               // h / 1080: sizes made for a 1080-pixel-tall window, times this
    double time;               // seconds, a steady clock
    double dt;                 // seconds since the last update (0 .. 0.25)
    bool drawable;             // false: modhost can't draw (ImGui didn't start): draw is never called, CAPTURE is ignored
    bool focused;              // the game window has the keyboard
    bool sw_menu;              // Savage Wind's own menu, console or a name entry has the input
    bool paused;               // the game's pause screen (or the map)
    bool game_menu;            // one of the game's menus (inventory, ...)
    bool title;                // the title screen
    bool event;                // a cutscene or an event
    bool captured;             // the game's input is held now (an overlay's CAPTURE, or the short hold after it)
    // keyboard, by SDL3 scancode (SWMOD_SC_*)
    const bool *keys;          // keys[sc]: held now (NULL: unknown)
    int nkeys;
    uint32_t keys_hit[16];     // pressed since the last update: bit (sc % 32) of keys_hit[sc / 32]
    // mouse
    float mouse_x, mouse_y;    // the pointer, in frame pixels (where it is while the game doesn't hold the mouse)
    float mouse_dx, mouse_dy;  // its movement since the last update, frame pixels (also while the game holds it)
    uint32_t mouse_held;       // bit 0 left, bit 1 middle, bit 2 right
    uint32_t mouse_hit;        // pressed since the last update (the same bits)
    float wheel;               // wheel steps since the last update (+: up, away from you)
    // the controller the game uses (SWMOD_PAD_* bits)
    uint32_t pad_held;
    uint32_t pad_hit;          // pressed since the last update
    float pad_axes[6];         // left x, left y, right x, right y (-1 .. 1, +y is down), left and right trigger (0 .. 1)
    // ---- drawing: only inside draw (they do nothing elsewhere). Positions in frame pixels, colours SWMOD_RGBA.
    void (*rect_filled)(float x0, float y0, float x1, float y1, uint32_t col, float rounding);
    void (*rect)(float x0, float y0, float x1, float y1, uint32_t col, float rounding, float thickness);
    void (*line)(float x0, float y0, float x1, float y1, uint32_t col, float thickness);
    void (*circle_filled)(float x, float y, float r, uint32_t col);
    void (*circle)(float x, float y, float r, uint32_t col, float thickness);
    void (*triangle_filled)(float x0, float y0, float x1, float y1, float x2, float y2, uint32_t col);
    // a polygon (x, y pairs, n points, clockwise on screen for the smoothest edges); concave ones work too
    void (*poly_filled)(const float *xy, int n, uint32_t col);
    void (*polyline)(const float *xy, int n, uint32_t col, bool closed, float thickness);
    // text in Savage Wind's FiraSans SemiBold, px tall, its top left corner at x y (Latin-1: ASCII and accents)
    void (*text)(float x, float y, float px, uint32_t col, const char *s);
    float (*text_width)(float px, const char *s);
};

static inline bool swmod_key_hit(const SwModOverlay *o, int sc) {
    return o && sc >= 0 && sc < 512 && (o->keys_hit[sc >> 5] >> (sc & 31) & 1u);
}
static inline bool swmod_key_held(const SwModOverlay *o, int sc) {
    return o && o->keys && sc >= 0 && sc < o->nkeys && o->keys[sc];
}

// modhost.dll's export. why gets a line for the log when the answer isn't SWMOD_OVL_OK.
typedef int (*SwModOverlayAddFn)(const SwModApi *api, SwModOverlayUpdate update, SwModOverlayDraw draw, void *user,
                                 char *why, size_t cap);

#ifndef SWMOD_OVERLAY_NO_WIN32_DECLS  // (the documented kernel32 calls; define this if your own headers declare them)
__declspec(dllimport) int __stdcall GetModuleHandleExW(unsigned long flags, const wchar_t *name, void **out);
__declspec(dllimport) void *__stdcall GetProcAddress(void *module, const char *name);
#endif

// Add an overlay. Call it from swmod_init (a mod loaded at game start). SWMOD_OVL_* answer; why says what is wrong.
static inline int swmod_overlay_add(const SwModApi *api, SwModOverlayUpdate update, SwModOverlayDraw draw, void *user,
                                    char *why, size_t cap) {
    void *host = NULL;  // modhost.dll: the module the api's functions live in
    SwModOverlayAddFn add = NULL;
    if (api && api->log && GetModuleHandleExW(0x6ul /* from an address, keep the count */,
                                              (const wchar_t *)(const void *)api->log, &host) && host)
        add = (SwModOverlayAddFn)GetProcAddress(host, SWMOD_OVERLAY_EXPORT);
    if (!add) {
        if (why && cap) {
            static const char msg[] = "this modhost has no overlay host (update Breathalyzer)";
            size_t n = sizeof msg - 1 < cap - 1 ? sizeof msg - 1 : cap - 1;
            memcpy(why, msg, n);
            why[n] = 0;
        }
        return SWMOD_OVL_NO_HOST;
    }
    return add(api, update, draw, user, why, cap);
}
