// overlay.c - modhost's overlay host: mods draw over the game with Savage Wind's own Dear ImGui and hold the game's
// input while they need it (swmod_overlay.h, README.md "Overlays"). Part of modhost.c: it is
// #included there, so modhost still builds from modhost.c alone. Compiled on its own it is empty.
//
// Savage Wind 0.4.1-alpha.4 has no draw callback for mods (out/research/castle-minigame-ui.md). The first
// modhost_overlay_add (from a mod's swmod_init, before any game thread runs) checks the exe's 2 calls of menu_render
// and its 13 calls of menu_input_captured byte for byte (each must be a 5-byte call of that export) and points them at
// two 14-byte jumps to ovl_menu_render and ovl_captured here, placed within 2 GB of the exe. Nothing else in the exe
// changes; with any other Savage Wind nothing is patched and the call answers SWMOD_OVL_UNSUPPORTED. With no overlay
// added, nothing is patched at all.
//
// Every export it uses (savagewind.exe's ImGui 1.91.9 and Vulkan backend, rec_drain, framepace_*, ui_window; SDL3's
// input) is looked up by name at run time, so a Savage Wind without them still loads modhost (the overlay is then
// off). Per shown frame (present thread): read SDL's input (an SDL event watch adds up the mouse movement, wheel,
// clicks and key presses between frames), call each overlay's update; only when one wants to draw, make one ImGui
// frame in modhost's own context (FiraSans SemiBold from the Savage Wind folder), call their draw and record it into
// the frame's command buffer, then call Savage Wind's menu_render, whose menu, console and FPS stay on top.
// While an update answers SWMOD_OVL_CAPTURE, ovl_captured answers true (Savage Wind then gives the game no input and
// frees the cursor, as for its own menu); after that it keeps answering true until no key, mouse button or controller
// button is held (2 s at most), plus 400 ms, as Savage Wind does after its own size list.
#ifdef MODHOST_C

#include "swmod_overlay.h"

#define OVL_MAX 8
#define OVL_FONT_PX 64.0f      // the font atlas size (text is scaled from it)
#define OVL_HOLD_MS 400        // the game stays muted this long after every key is up
#define OVL_WAIT_UP_MS 2000    // ... but waits at most this long for the keys to come up
#define OVL_PAD_SCAN_MS 1000   // the controller list is read again this often

__declspec(dllimport) HMODULE WINAPI GetModuleHandleW(const wchar_t *name);
__declspec(dllimport) void *WINAPI VirtualAlloc(void *addr, size_t size, DWORD type, DWORD protect);
__declspec(dllimport) BOOL WINAPI VirtualProtect(void *addr, size_t size, DWORD protect, DWORD *old);
__declspec(dllimport) HANDLE WINAPI GetCurrentProcess(void);
__declspec(dllimport) BOOL WINAPI FlushInstructionCache(HANDLE process, const void *addr, size_t size);
__declspec(dllimport) BOOL WINAPI QueryPerformanceCounter(long long *out);
__declspec(dllimport) BOOL WINAPI QueryPerformanceFrequency(long long *out);

// ---- the patch (savagewind.exe 0.4.1-alpha.4: out/research/castle-ui-proto/pe_scan.txt, checked again 2026-11-14)
static const uint32_t k_ovl_render_calls[] = {0xb2f5c, 0xb334a};
static const uint32_t k_ovl_captured_calls[] = {0x4fcd2, 0x4fd9f, 0x501c4, 0x509ff, 0x50a86, 0x50ac9, 0x5111b,
                                                0x5113b, 0x51361, 0x515ce, 0x5307a, 0x538e8, 0x539da};
#define OVL_COUNT(a) (int)(sizeof(a) / sizeof((a)[0]))

typedef struct {
    float x, y;
} OvVec2;  // ImVec2

typedef struct {
    SwModOverlayUpdate update;
    SwModOverlayDraw draw;
    void *user;
    char mod[64];
    int id;  // the mod's id (ovl_switch_off)
} Overlay;

static Overlay g_ovl[OVL_MAX];
static int g_novl;           // set while mods start (before game threads), read-only after
static int g_ovl_state;      // 0 not tried, 1 patched, -1 can't (g_ovl_why says why)
static char g_ovl_why[160];

// savagewind.exe
static void (*sw_menu_render)(void *cmd, uint32_t w, uint32_t h);
static bool (*sw_captured)(void);
static void (*sw_rec_drain)(void);
static bool (*sw_backend_init)(void);
static void *(*sw_ui_window)(void);
static bool (*sw_fp_paused)(void), (*sw_fp_menu)(void), (*sw_fp_title)(void), (*sw_fp_event)(void);
static void *(*ig_CreateContext)(void *atlas);
static void (*ig_SetCurrentContext)(void *ctx);
static void *(*ig_GetCurrentContext)(void);
static uint8_t *(*ig_GetIO)(void);
static void (*ig_NewFrame)(void);
static void (*ig_Render)(void);
static void *(*ig_GetDrawData)(void);
static void *(*ig_GetForegroundDrawList)(void);
static OvVec2 (*ig_CalcTextSize)(const char *b, const char *e, bool hide, float wrap);
static void *(*ig_AddFontFromFileTTF)(void *atlas, const char *path, float px, const void *cfg, const uint16_t *ranges);
static void (*dl_AddRectFilled)(void *dl, const OvVec2 *a, const OvVec2 *b, uint32_t col, float rounding, int flags);
static void (*dl_AddRect)(void *dl, const OvVec2 *a, const OvVec2 *b, uint32_t col, float rounding, int flags,
                          float thick);
static void (*dl_AddLine)(void *dl, const OvVec2 *a, const OvVec2 *b, uint32_t col, float thick);
static void (*dl_AddCircleFilled)(void *dl, const OvVec2 *c, float r, uint32_t col, int segments);
static void (*dl_AddCircle)(void *dl, const OvVec2 *c, float r, uint32_t col, int segments, float thick);
static void (*dl_AddTriangleFilled)(void *dl, const OvVec2 *a, const OvVec2 *b, const OvVec2 *c, uint32_t col);
static void (*dl_AddConcavePolyFilled)(void *dl, const OvVec2 *pts, int n, uint32_t col);
static void (*dl_AddPolyline)(void *dl, const OvVec2 *pts, int n, uint32_t col, int flags, float thick);
static void (*dl_AddText)(void *dl, void *font, float px, const OvVec2 *pos, uint32_t col, const char *b, const char *e,
                          float wrap, const void *clip);
static void (*vk_NewFrame)(void);
static void (*vk_RenderDrawData)(void *data, void *cmd, void *pipeline);
// SDL3.dll
static const bool *(*sdl_GetKeyboardState)(int *n);
static uint32_t (*sdl_GetMouseState)(float *x, float *y);
static uint32_t *(*sdl_GetGamepads)(int *n);
static void *(*sdl_GetGamepadFromID)(uint32_t id);
static bool (*sdl_GetGamepadButton)(void *pad, int button);
static int16_t (*sdl_GetGamepadAxis)(void *pad, int axis);
static bool (*sdl_GetWindowSize)(void *window, int *w, int *h);
static uint64_t (*sdl_GetWindowFlags)(void *window);
static bool (*sdl_AddEventWatch)(bool (*fn)(void *user, void *event), void *user);
static void (*sdl_free)(void *p);

typedef struct {
    void **slot;
    const char *name;
} OvlImport;

static bool ovl_resolve(HMODULE m, const OvlImport *list, int n, char *why, size_t cap) {
    for (int i = 0; i < n; i++) {
        *list[i].slot = m ? GetProcAddress(m, list[i].name) : NULL;
        if (!*list[i].slot) return snprintf(why, cap, "%s isn't there", list[i].name), false;
    }
    return true;
}

// The module that gives modhost its runtime imports (savagewind.exe; in modhost's self-test its stand-in).
static HMODULE ovl_exe(void) {
    HMODULE h = NULL;
    if (!GetModuleHandleExW(SW_MODULE_FROM_ADDRESS | SW_MODULE_KEEP_REFCOUNT, (const wchar_t *)(void *)gamelib_id, &h))
        return NULL;
    return h;
}

// Every site is a 5-byte call of the export at rva (and inside the image: its PE header's SizeOfImage).
static bool ovl_calls_ok(const uint8_t *exe, const uint32_t *sites, int n, uint32_t rva) {
    uint32_t lfanew, image;
    memcpy(&lfanew, exe + 0x3c, 4);
    if (exe[0] != 'M' || exe[1] != 'Z' || lfanew > 0x1000 || memcmp(exe + lfanew, "PE\0\0", 4)) return false;
    memcpy(&image, exe + lfanew + 0x50, 4);  // (PE32+ optional header + 56: SizeOfImage)
    for (int i = 0; i < n; i++) {
        if (sites[i] + 5 > image) return false;
        const uint8_t *p = exe + sites[i];
        int32_t rel;
        memcpy(&rel, p + 1, 4);
        if (p[0] != 0xe8 || (uint32_t)((int64_t)sites[i] + 5 + rel) != rva) return false;
    }
    return true;
}

// A page of jumps within 2 GB below (else above) the exe, so that a 5-byte call reaches it.
static uint8_t *g_ovl_stubs;
static void *ovl_stub(const uint8_t *exe, int i, void *to) {
    if (!g_ovl_stubs) {
        uint64_t base = (uint64_t)(uintptr_t)exe & ~0xffffull;
        for (uint64_t d = 0x10000; !g_ovl_stubs && d < 0x70000000ull; d += 0x10000) {
            if (base > d + 0x10000) g_ovl_stubs = VirtualAlloc((void *)(uintptr_t)(base - d), 4096, 0x3000, 0x40);
            if (!g_ovl_stubs) g_ovl_stubs = VirtualAlloc((void *)(uintptr_t)(base + 0x4000000ull + d), 4096, 0x3000, 0x40);
        }
        if (!g_ovl_stubs) return NULL;
    }
    uint8_t *s = g_ovl_stubs + 16 * i;
    static const uint8_t jmp[6] = {0xff, 0x25, 0, 0, 0, 0};  // jmp [rip+0], then the address: no register changes
    memcpy(s, jmp, 6);
    memcpy(s + 6, &to, 8);
    return s;
}

static bool ovl_reach(const uint8_t *exe, const uint32_t *sites, int n, const void *stub) {
    for (int i = 0; i < n; i++) {
        int64_t d = (int64_t)(uintptr_t)stub - (int64_t)(uintptr_t)(exe + sites[i] + 5);
        if (d < INT32_MIN || d > INT32_MAX) return false;
    }
    return true;
}

static void ovl_point(uint8_t *exe, const uint32_t *sites, int n, const void *stub) {
    for (int i = 0; i < n; i++) {
        uint8_t *p = exe + sites[i];
        int32_t rel = (int32_t)((int64_t)(uintptr_t)stub - (int64_t)(uintptr_t)(p + 5));
        DWORD old, tmp;
        if (!VirtualProtect(p, 5, 0x40, &old)) continue;
        __builtin_memcpy(p + 1, &rel, 4);  // (one 4-byte store)
        VirtualProtect(p, 5, old, &tmp);
    }
    FlushInstructionCache(GetCurrentProcess(), NULL, 0);
}

static void ovl_menu_render(void *cmd, uint32_t w, uint32_t h);
static bool ovl_captured(void);

static bool ovl_install(char *why, size_t cap) {
    HMODULE exe = ovl_exe(), sdl = GetModuleHandleW(L"SDL3.dll");
    if (!exe) return snprintf(why, cap, "can't find savagewind.exe"), false;
    const OvlImport sw[] = {
        {(void **)&sw_menu_render, "menu_render"},
        {(void **)&sw_captured, "menu_input_captured"},
        {(void **)&sw_rec_drain, "rec_drain"},
        {(void **)&sw_backend_init, "vk_imgui_backend_init"},
        {(void **)&sw_ui_window, "ui_window"},
        {(void **)&sw_fp_paused, "framepace_paused"},
        {(void **)&sw_fp_menu, "framepace_menu_ui"},
        {(void **)&sw_fp_title, "framepace_title_ui"},
        {(void **)&sw_fp_event, "framepace_in_event"},
        {(void **)&ig_CreateContext, "_ZN5ImGui13CreateContextEP11ImFontAtlas"},
        {(void **)&ig_SetCurrentContext, "_ZN5ImGui17SetCurrentContextEP12ImGuiContext"},
        {(void **)&ig_GetCurrentContext, "_ZN5ImGui17GetCurrentContextEv"},
        {(void **)&ig_GetIO, "_ZN5ImGui5GetIOEv"},
        {(void **)&ig_NewFrame, "_ZN5ImGui8NewFrameEv"},
        {(void **)&ig_Render, "_ZN5ImGui6RenderEv"},
        {(void **)&ig_GetDrawData, "_ZN5ImGui11GetDrawDataEv"},
        {(void **)&ig_GetForegroundDrawList, "_ZN5ImGui21GetForegroundDrawListEv"},
        {(void **)&ig_CalcTextSize, "_ZN5ImGui12CalcTextSizeEPKcS1_bf"},
        {(void **)&ig_AddFontFromFileTTF, "_ZN11ImFontAtlas18AddFontFromFileTTFEPKcfPK12ImFontConfigPKt"},
        {(void **)&dl_AddRectFilled, "_ZN10ImDrawList13AddRectFilledERK6ImVec2S2_jfi"},
        {(void **)&dl_AddRect, "_ZN10ImDrawList7AddRectERK6ImVec2S2_jfif"},
        {(void **)&dl_AddLine, "_ZN10ImDrawList7AddLineERK6ImVec2S2_jf"},
        {(void **)&dl_AddCircleFilled, "_ZN10ImDrawList15AddCircleFilledERK6ImVec2fji"},
        {(void **)&dl_AddCircle, "_ZN10ImDrawList9AddCircleERK6ImVec2fjif"},
        {(void **)&dl_AddTriangleFilled, "_ZN10ImDrawList17AddTriangleFilledERK6ImVec2S2_S2_j"},
        {(void **)&dl_AddConcavePolyFilled, "_ZN10ImDrawList20AddConcavePolyFilledEPK6ImVec2ij"},
        {(void **)&dl_AddPolyline, "_ZN10ImDrawList11AddPolylineEPK6ImVec2ijif"},
        {(void **)&dl_AddText, "_ZN10ImDrawList7AddTextEP6ImFontfRK6ImVec2jPKcS6_fPK6ImVec4"},
        {(void **)&vk_NewFrame, "_Z25ImGui_ImplVulkan_NewFramev"},
        {(void **)&vk_RenderDrawData,
         "_Z31ImGui_ImplVulkan_RenderDrawDataP10ImDrawDataP17VkCommandBuffer_TP12VkPipeline_T"},
    };
    const OvlImport sd[] = {
        {(void **)&sdl_GetKeyboardState, "SDL_GetKeyboardState"},
        {(void **)&sdl_GetMouseState, "SDL_GetMouseState"},
        {(void **)&sdl_GetGamepads, "SDL_GetGamepads"},
        {(void **)&sdl_GetGamepadFromID, "SDL_GetGamepadFromID"},
        {(void **)&sdl_GetGamepadButton, "SDL_GetGamepadButton"},
        {(void **)&sdl_GetGamepadAxis, "SDL_GetGamepadAxis"},
        {(void **)&sdl_GetWindowSize, "SDL_GetWindowSize"},
        {(void **)&sdl_GetWindowFlags, "SDL_GetWindowFlags"},
        {(void **)&sdl_AddEventWatch, "SDL_AddEventWatch"},
        {(void **)&sdl_free, "SDL_free"},
    };
    char what[96];
    if (!ovl_resolve(exe, sw, OVL_COUNT(sw), what, sizeof what))
        return snprintf(why, cap, "not Savage Wind 0.4.1-alpha.4 (%s)", what), false;
    if (!sdl || !ovl_resolve(sdl, sd, OVL_COUNT(sd), what, sizeof what))
        return snprintf(why, cap, "SDL3.dll: %s", sdl ? what : "not loaded"), false;
    uint8_t *b = (uint8_t *)exe;
    uint32_t r_render = (uint32_t)((uint8_t *)(void *)sw_menu_render - b);
    uint32_t r_capt = (uint32_t)((uint8_t *)(void *)sw_captured - b);
    if (!ovl_calls_ok(b, k_ovl_render_calls, OVL_COUNT(k_ovl_render_calls), r_render) ||
        !ovl_calls_ok(b, k_ovl_captured_calls, OVL_COUNT(k_ovl_captured_calls), r_capt))
        return snprintf(why, cap, "not Savage Wind 0.4.1-alpha.4 (its calls differ): nothing changed"), false;
    void *s_render = ovl_stub(b, 0, (void *)ovl_menu_render), *s_capt = ovl_stub(b, 1, (void *)ovl_captured);
    if (!s_render || !s_capt || !ovl_reach(b, k_ovl_render_calls, OVL_COUNT(k_ovl_render_calls), s_render) ||
        !ovl_reach(b, k_ovl_captured_calls, OVL_COUNT(k_ovl_captured_calls), s_capt))
        return snprintf(why, cap, "no memory near the exe for the jumps: nothing changed"), false;
    ovl_point(b, k_ovl_render_calls, OVL_COUNT(k_ovl_render_calls), s_render);
    ovl_point(b, k_ovl_captured_calls, OVL_COUNT(k_ovl_captured_calls), s_capt);
    return true;
}

// ------------------------------------------------------------------ input between frames (SDL event watch)

static uint32_t g_ev_lock;
static struct {
    float dx, dy, wheel;
    uint32_t mouse_hit, pad_hit;
    uint32_t keys_hit[16];
} g_ev;

static void ev_lock(void) {
    while (__atomic_exchange_n(&g_ev_lock, 1, __ATOMIC_ACQUIRE)) {
    }
}
static void ev_unlock(void) { __atomic_store_n(&g_ev_lock, 0, __ATOMIC_RELEASE); }

// Runs on the thread that pumps SDL's events (Savage Wind's main thread) for every event: only adds things up.
static bool ovl_event(void *user, void *event) {
    (void)user;
    const uint8_t *e = event;
    uint32_t type;
    memcpy(&type, e, 4);
    if (type != 0x300 && type != 0x400 && type != 0x401 && type != 0x403 && type != 0x651) return true;
    ev_lock();
    if (type == 0x300) {         // SDL_EVENT_KEY_DOWN: scancode +24, repeat +37
        uint32_t sc;
        memcpy(&sc, e + 24, 4);
        if (sc < 512 && !e[37]) g_ev.keys_hit[sc >> 5] |= 1u << (sc & 31);
    } else if (type == 0x400) {  // SDL_EVENT_MOUSE_MOTION: xrel +36, yrel +40
        float x, y;
        memcpy(&x, e + 36, 4);
        memcpy(&y, e + 40, 4);
        if (x == x && y == y) g_ev.dx += x, g_ev.dy += y;
    } else if (type == 0x401) {  // SDL_EVENT_MOUSE_BUTTON_DOWN: button +24 (1 left, 2 middle, 3 right)
        if (e[24] >= 1 && e[24] <= 3) g_ev.mouse_hit |= 1u << (e[24] - 1);
    } else if (type == 0x403) {  // SDL_EVENT_MOUSE_WHEEL: y +28, direction +32 (1: flipped)
        float y;
        uint32_t dir;
        memcpy(&y, e + 28, 4);
        memcpy(&dir, e + 32, 4);
        if (y == y) g_ev.wheel += dir == 1 ? -y : y;
    } else {                     // SDL_EVENT_GAMEPAD_BUTTON_DOWN: button +20
        if (e[20] < 32) g_ev.pad_hit |= 1u << e[20];
    }
    ev_unlock();
    return true;
}

// ------------------------------------------------------------------ drawing (inside an ImGui frame only)

static void *g_dl;  // the foreground draw list while the overlays draw, else NULL
static void *g_font;
static float g_font_px = 13.0f;

static void d_rect_filled(float x0, float y0, float x1, float y1, uint32_t col, float rounding) {
    OvVec2 a = {x0, y0}, b = {x1, y1};
    if (g_dl) dl_AddRectFilled(g_dl, &a, &b, col, rounding, 0);
}
static void d_rect(float x0, float y0, float x1, float y1, uint32_t col, float rounding, float thick) {
    OvVec2 a = {x0, y0}, b = {x1, y1};
    if (g_dl) dl_AddRect(g_dl, &a, &b, col, rounding, 0, thick);
}
static void d_line(float x0, float y0, float x1, float y1, uint32_t col, float thick) {
    OvVec2 a = {x0, y0}, b = {x1, y1};
    if (g_dl) dl_AddLine(g_dl, &a, &b, col, thick);
}
static void d_circle_filled(float x, float y, float r, uint32_t col) {
    OvVec2 c = {x, y};
    if (g_dl && r > 0) dl_AddCircleFilled(g_dl, &c, r, col, 0);
}
static void d_circle(float x, float y, float r, uint32_t col, float thick) {
    OvVec2 c = {x, y};
    if (g_dl && r > 0) dl_AddCircle(g_dl, &c, r, col, 0, thick);
}
static void d_triangle_filled(float x0, float y0, float x1, float y1, float x2, float y2, uint32_t col) {
    OvVec2 a = {x0, y0}, b = {x1, y1}, c = {x2, y2};
    if (g_dl) dl_AddTriangleFilled(g_dl, &a, &b, &c, col);
}
static void d_poly_filled(const float *xy, int n, uint32_t col) {
    if (g_dl && xy && n >= 3) dl_AddConcavePolyFilled(g_dl, (const OvVec2 *)(const void *)xy, n, col);
}
static void d_polyline(const float *xy, int n, uint32_t col, bool closed, float thick) {
    if (g_dl && xy && n >= 2) dl_AddPolyline(g_dl, (const OvVec2 *)(const void *)xy, n, col, closed ? 1 : 0, thick);
}
static void d_text(float x, float y, float px, uint32_t col, const char *s) {
    OvVec2 p = {x, y};
    if (g_dl && s && *s && px > 0) dl_AddText(g_dl, g_font, px, &p, col, s, NULL, 0.0f, NULL);
}
static float d_text_width(float px, const char *s) {
    if (!g_dl || !s || !*s || px <= 0) return 0.0f;
    OvVec2 v = ig_CalcTextSize(s, NULL, false, -1.0f);
    return v.x * px / g_font_px;
}

// ------------------------------------------------------------------ the frame (Savage Wind's present thread)

static volatile uint32_t g_ovl_cap;       // an overlay holds the game's input
static volatile uint64_t g_ovl_hold_ms;   // GetTickCount64(): muted until then (UINT64_MAX: until the keys are up)
static uint64_t g_ovl_wait_until;         // (present thread) the wait for the keys ends then at the latest
static void *g_ctx;
static int g_ctx_state;                   // 0 not made, 1 ready, -1 failed (no drawing)
static long long g_qpf, g_qpc0, g_qpc_last;
static uint64_t g_pads_at;
static uint32_t g_pad_ids[8];
static int g_npads;
static bool g_watching;
static uint32_t g_frame;
static uint8_t g_prev_keys[512];

static bool ovl_captured(void) {
    if (__atomic_load_n(&g_ovl_cap, __ATOMIC_ACQUIRE)) return true;
    uint64_t hold = __atomic_load_n(&g_ovl_hold_ms, __ATOMIC_ACQUIRE);
    if (hold && GetTickCount64() < hold) return true;
    return sw_captured();
}

static bool ovl_ctx(void) {
    if (g_ctx_state) return g_ctx_state > 0;
    g_ctx_state = -1;
    void *prev = ig_GetCurrentContext();
    g_ctx = ig_CreateContext(NULL);
    if (!g_ctx) {
        host_log("overlay: ImGui context failed: nothing is drawn");
        return false;
    }
    ig_SetCurrentContext(g_ctx);
    uint8_t *io = ig_GetIO();
    *(const char **)(io + 24) = NULL;  // IniFilename (else ImGui writes imgui.ini)
    *(const char **)(io + 32) = NULL;  // LogFilename
    // FiraSans SemiBold ships with Savage Wind, next to savagewind.exe (OFL)
    wchar_t exe_w[PATH_W];
    char path[PATH_U];
    DWORD n = GetModuleFileNameW(ovl_exe(), exe_w, PATH_W);
    path[0] = 0;
    if (n > 0 && n < PATH_W) {
        while (n > 0 && exe_w[n - 1] != L'\\' && exe_w[n - 1] != L'/') n--;
        exe_w[n] = 0;
        static const wchar_t *const fonts[] = {L"FiraSans-SemiBold.ttf", L"FiraSans-Medium.ttf"};
        for (int i = 0; i < 2 && !path[0]; i++) {
            wchar_t f[PATH_W] = {0};
            if (wcat(f, PATH_W, exe_w) && wcat(f, PATH_W, fonts[i]) && GetFileAttributesW(f) != SW_INVALID_ATTRIBUTES)
                to_utf8(f, path, sizeof path);
        }
    }
    if (path[0]) {
        g_font = ig_AddFontFromFileTTF(*(void **)(io + 48), path, OVL_FONT_PX, NULL, NULL);  // (io.Fonts)
        if (g_font) g_font_px = OVL_FONT_PX;
    }
    if (!g_font) host_log("overlay: FiraSans not found next to savagewind.exe: ImGui's small font instead");
    if (!sw_backend_init()) {
        ig_SetCurrentContext(prev);
        host_log("overlay: ImGui's Vulkan backend didn't start: nothing is drawn");
        return false;
    }
    ig_SetCurrentContext(prev);
    g_ctx_state = 1;
    return true;
}

static void ovl_input(SwModOverlay *o, float w, float h) {
    void *win = sw_ui_window();
    int ww = 0, wh = 0;
    if (win) sdl_GetWindowSize(win, &ww, &wh);
    float sx = ww > 0 ? w / (float)ww : 1.0f, sy = wh > 0 ? h / (float)wh : 1.0f;
    o->focused = win && (sdl_GetWindowFlags(win) & 0x200ull);  // SDL_WINDOW_INPUT_FOCUS
    int nk = 0;
    o->keys = sdl_GetKeyboardState(&nk);
    o->nkeys = o->keys ? (nk < 512 ? nk : 512) : 0;
    float mx = 0, my = 0;
    uint32_t mb = sdl_GetMouseState(&mx, &my);
    o->mouse_x = mx * sx, o->mouse_y = my * sy;
    o->mouse_held = mb & 7u;
    ev_lock();
    o->mouse_dx = g_ev.dx * sx, o->mouse_dy = g_ev.dy * sy, o->wheel = g_ev.wheel;
    o->mouse_hit = g_ev.mouse_hit;
    o->pad_hit = g_ev.pad_hit;
    memcpy(o->keys_hit, g_ev.keys_hit, sizeof o->keys_hit);
    memset(&g_ev, 0, sizeof g_ev);
    ev_unlock();
    for (int sc = 0; sc < o->nkeys; sc++) {  // (a press shorter than a frame without the event watch: the edge)
        if (o->keys[sc] && !g_prev_keys[sc]) o->keys_hit[sc >> 5] |= 1u << (sc & 31);
        g_prev_keys[sc] = o->keys[sc];
    }
    uint64_t now = GetTickCount64();
    if (now >= g_pads_at) {  // the controllers SDL knows; only the one Savage Wind opened answers GetGamepadFromID
        g_pads_at = now + OVL_PAD_SCAN_MS;
        int n = 0;
        uint32_t *ids = sdl_GetGamepads(&n);
        g_npads = 0;
        for (int i = 0; ids && i < n && g_npads < 8; i++) g_pad_ids[g_npads++] = ids[i];
        if (ids) sdl_free(ids);
    }
    for (int i = 0; i < g_npads; i++) {
        void *pad = sdl_GetGamepadFromID(g_pad_ids[i]);
        if (!pad) continue;
        for (int b = 0; b < 15; b++)
            if (sdl_GetGamepadButton(pad, b)) o->pad_held |= 1u << b;
        for (int a = 0; a < 6; a++) {
            float v = sdl_GetGamepadAxis(pad, a) / 32767.0f;
            v = v < -1.0f ? -1.0f : v > 1.0f ? 1.0f : v;
            if (__builtin_fabsf(v) > __builtin_fabsf(o->pad_axes[a])) o->pad_axes[a] = v;
        }
    }
}

// Anything held: a key, a mouse button, a controller button or trigger.
static bool ovl_any_held(const SwModOverlay *o) {
    for (int sc = 4; sc < o->nkeys; sc++)  // (0-3 aren't keys)
        if (o->keys[sc]) return true;
    return o->mouse_held || o->pad_held || o->pad_axes[4] > 0.5f || o->pad_axes[5] > 0.5f;
}

static void ovl_menu_render(void *cmd, uint32_t w, uint32_t h) {
    if (!g_watching) {  // (once: SDL is up by now)
        g_watching = true;
        if (!sdl_AddEventWatch(ovl_event, NULL)) host_log("overlay: SDL's event watch didn't start (held keys only)");
    }
    int novl = __atomic_load_n(&g_novl, __ATOMIC_ACQUIRE);
    long long now;
    QueryPerformanceCounter(&now);
    if (!g_qpf) QueryPerformanceFrequency(&g_qpf), g_qpc0 = g_qpc_last = now;
    SwModOverlay o;
    memset(&o, 0, sizeof o);
    o.size = (uint32_t)sizeof o;
    o.frame = ++g_frame;
    o.w = (float)w, o.h = (float)h, o.scale = h ? (float)h / 1080.0f : 1.0f;
    o.time = (double)(now - g_qpc0) / (double)g_qpf;
    o.dt = (double)(now - g_qpc_last) / (double)g_qpf;
    o.dt = o.dt < 0 ? 0 : o.dt > 0.25 ? 0.25 : o.dt;
    g_qpc_last = now;
    o.drawable = g_ctx_state >= 0;
    o.sw_menu = sw_captured();
    o.paused = sw_fp_paused(), o.game_menu = sw_fp_menu(), o.title = sw_fp_title(), o.event = sw_fp_event();
    ovl_input(&o, (float)w, (float)h);
    uint64_t hold = __atomic_load_n(&g_ovl_hold_ms, __ATOMIC_ACQUIRE);
    o.captured = __atomic_load_n(&g_ovl_cap, __ATOMIC_ACQUIRE) || (hold && GetTickCount64() < hold);
    o.rect_filled = d_rect_filled, o.rect = d_rect, o.line = d_line, o.circle_filled = d_circle_filled;
    o.circle = d_circle, o.triangle_filled = d_triangle_filled, o.poly_filled = d_poly_filled;
    o.polyline = d_polyline, o.text = d_text, o.text_width = d_text_width;

    unsigned want[OVL_MAX] = {0}, any = 0;
    for (int i = 0; i < novl; i++) any |= want[i] = g_ovl[i].update(&o, g_ovl[i].user);
    if (!o.drawable) any &= ~SWMOD_OVL_CAPTURE;  // (never hold the game with nothing on the screen)

    // the game's input: held while an overlay asks, then until every key is up (2 s at most), then 400 ms more
    uint64_t ms = GetTickCount64();
    if (any & SWMOD_OVL_CAPTURE) {
        __atomic_store_n(&g_ovl_cap, 1u, __ATOMIC_RELEASE);
    } else if (__atomic_load_n(&g_ovl_cap, __ATOMIC_ACQUIRE)) {
        __atomic_store_n(&g_ovl_hold_ms, UINT64_MAX, __ATOMIC_RELEASE);  // (first: no moment without a hold)
        __atomic_store_n(&g_ovl_cap, 0u, __ATOMIC_RELEASE);
        g_ovl_wait_until = ms + OVL_WAIT_UP_MS;
    }
    if (__atomic_load_n(&g_ovl_hold_ms, __ATOMIC_ACQUIRE) == UINT64_MAX && (!ovl_any_held(&o) || ms >= g_ovl_wait_until))
        __atomic_store_n(&g_ovl_hold_ms, ms + OVL_HOLD_MS, __ATOMIC_RELEASE);

    if ((any & SWMOD_OVL_DRAW) && h && w) {
        sw_rec_drain();  // (ImGui records into cmd from this thread, and may upload its font: as Savage Wind's menu)
        if (ovl_ctx()) {
            void *prev = ig_GetCurrentContext();
            ig_SetCurrentContext(g_ctx);
            uint8_t *io = ig_GetIO();
            OvVec2 size = {(float)w, (float)h};
            float dt = o.dt > 0.0001 ? (float)o.dt : 0.0001f;
            memcpy(io + 8, &size, 8);  // DisplaySize
            memcpy(io + 16, &dt, 4);   // DeltaTime
            vk_NewFrame();
            ig_NewFrame();
            g_dl = ig_GetForegroundDrawList();
            for (int i = 0; i < novl; i++)
                if ((want[i] & SWMOD_OVL_DRAW) && g_ovl[i].draw) g_ovl[i].draw(&o, g_ovl[i].user);
            g_dl = NULL;
            ig_Render();
            vk_RenderDrawData(ig_GetDrawData(), cmd, NULL);
            ig_SetCurrentContext(prev);
        }
    }
    sw_menu_render(cmd, w, h);
}

// ------------------------------------------------------------------ the export mods call (swmod_overlay.h)

GAMELIB_EXPORT int modhost_overlay_add(const SwModApi *api, SwModOverlayUpdate update, SwModOverlayDraw draw,
                                       void *user, char *why, size_t cap) {
    char tmp[8];
    if (!why || !cap) why = tmp, cap = sizeof tmp;
    why[0] = 0;
    Mod *m = NULL;
    for (int i = 0; i < MAX_MODS && !m; i++)
        if (g_mods[i].used && &g_mods[i].api == api) m = &g_mods[i];
    if (!update) return snprintf(why, cap, "overlay: no update function"), SWMOD_OVL_BAD_ARGS;
    if (!m || m->hot || m != g_current || GetCurrentThreadId() != g_current_tid)
        return snprintf(why, cap, "overlay: only from swmod_init of a mod loaded at game start"), SWMOD_OVL_LATE;
    if (g_novl >= OVL_MAX) return snprintf(why, cap, "overlay: %d overlays at most", OVL_MAX), SWMOD_OVL_FULL;
    if (!g_ovl_state) {
        g_ovl_state = ovl_install(g_ovl_why, sizeof g_ovl_why) ? 1 : -1;
        if (g_ovl_state > 0)
            host_log("overlay: ready (2 + 13 calls of savagewind.exe now go through modhost first)");
        else
            host_log("overlay: off: %s", g_ovl_why);
    }
    if (g_ovl_state < 0) return snprintf(why, cap, "overlay: %s", g_ovl_why), SWMOD_OVL_UNSUPPORTED;
    Overlay *e = &g_ovl[g_novl];
    e->update = update, e->draw = draw, e->user = user;
    copy_str(e->mod, sizeof e->mod, m->name);
    e->id = m->id;
    __atomic_store_n(&g_novl, g_novl + 1, __ATOMIC_RELEASE);
    host_log("overlay: added for %s", m->name);
    return SWMOD_OVL_OK;
}

// switch_off (the mod's swmod_init failed after it added an overlay): its update is replaced by one that wants
// nothing, so neither its update nor its draw runs again (one pointer store each: the present thread sees the old one
// or the stand-in, and the DLL stays loaded). The slot stays used.
static unsigned ovl_off_update(const SwModOverlay *o, void *user) {
    (void)o, (void)user;
    return 0;
}
static void ovl_switch_off(Mod *m) {
    int n = __atomic_load_n(&g_novl, __ATOMIC_ACQUIRE);
    for (int i = 0; i < n; i++)
        if (g_ovl[i].id == m->id && g_ovl[i].update != ovl_off_update) {
            __atomic_store_n(&g_ovl[i].update, ovl_off_update, __ATOMIC_SEQ_CST);
            __atomic_store_n(&g_ovl[i].draw, (SwModOverlayDraw)0, __ATOMIC_SEQ_CST);
            host_log("overlay: taken out for %s (its swmod_init failed)", m->name);
        }
}

#endif  // MODHOST_C
