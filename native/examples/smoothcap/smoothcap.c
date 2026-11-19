// smoothcap: smooth offline capture (docs/smooth-capture.md). The game runs as slowly as it needs to, every game
// update advances the game's clock by exactly the same step, and the game image is saved at every other present.
// tools/smooth_capture.py drives it (control commands below) and turns the frames into a 60 fps video.
//
// How (Savage Wind 0.4.1-alpha.4, runtime/hle/framepace.c):
//   - The game's clock is ksys::VFR. Once per game update the game code (swgame.dll) calls the runtime's
//     rt_vfr_update(vfr), which writes the step it measured from the real time (ratio: the step in 1/30 s). This mod
//     replaces swgame.dll's import of rt_vfr_update (its import address table): the runtime's runs first, then, while
//     a capture runs, the step is overwritten with a fixed one (ratio, new ratio, interval).
//   - The runtime functions that scale by the measured frame length (rt_gui_speed, rt_arrow_gravity,
//     rt_snd_frame_floor) are replaced the same way and use the fixed step while a capture runs.
//   - The controller hold counters' step (site controller_step): the runtime writes the measured one at each present
//     unless SAVAGEWIND_NO_CONTROLLER_STEP=1. With that set (smooth_capture.py sets it) and the clock hooked, this
//     mod writes it: the fixed step while a capture runs, else the measured frame length as the runtime would.
//   - Only the game thread (in the hook) turns the F12 recording on and off while a capture runs: a stop from the
//     control thread sets the state and the next update writes 0 (status "recording" says when it's off).
//   - Saving: the runtime's own F12 recording (savagewind.exe's g_record_frames): while it is 1, every present with an
//     even frame number saves the game image to <data root>\build\frames\rec_<frame>.png, synchronously (the game
//     waits for the PNG). So the step is half the video's frame time: 1/120 s for 60 fps.
// Commands (developer mode: SAVAGEWIND_CONTROL=<port>):
//   smoothcap start <frames> [fps]   fixed step 1/(2*fps) s, record <frames> frames, then back to normal
//   smoothcap hold <fps>|off         the fixed step without recording (to look at it)
//   smoothcap stop                   recording off, normal clock
//   smoothcap status                 state, step, updates, frames left
// The zig that ships with Savage Wind has no windows.h (native/modhost/platform_min.h): the few Win32 calls and the
// PE file offsets below are declared by hand from Microsoft's documentation.
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "swmod.h"

// ------------------------------------------------------------------ Win32 (kernel32) and the C runtime
#define SC_WINAPI __stdcall
__declspec(dllimport) void *SC_WINAPI GetModuleHandleW(const wchar_t *name);
__declspec(dllimport) void *SC_WINAPI GetProcAddress(void *module, const char *name);
__declspec(dllimport) unsigned long SC_WINAPI GetEnvironmentVariableW(const wchar_t *name, wchar_t *out,
                                                                       unsigned long size);
__declspec(dllimport) int SC_WINAPI VirtualProtect(void *addr, size_t size, unsigned long prot, unsigned long *old);
__declspec(dllimport) int SC_WINAPI QueryPerformanceCounter(long long *count);
__declspec(dllimport) int SC_WINAPI QueryPerformanceFrequency(long long *freq);
int _stricmp(const char *a, const char *b);  // (ucrt)
#define SC_PAGE_READWRITE 0x04ul

// ------------------------------------------------------------------ the game's clock (ksys::VFR, framepace.c)
#define VFR_CHANGED 0x28
#define VFR_NEW_RATIO 0x30
#define VFR_INTERVAL 0x34
#define VFR_RATIO 0x3c
#define VF(T, base, off) (*(T *)(uintptr_t)((base) + (off)))

#define FPS_MIN 15     // ratio 1.0: the console's 30 Hz step
#define FPS_MAX 150    // ratio 0.1: 1/300 s, the runtime's shortest step (FPS_CEILING 300)
#define WARM_UPDATES 8 // updates at the fixed step before the first saved frame (TAA history, the record toggle)
#define TAIL_UPDATES 2 // updates still at the fixed step after the last one
#define REC_SLACK 3    // updates recorded past 2 x frames: either parity, and a present a frame behind its update

enum { SC_OFF, SC_HOLD, SC_WARM, SC_REC, SC_TAIL };
static const char *const k_state[] = {"off", "hold", "warm-up", "recording", "finishing"};

typedef struct {
    int state;                      // SC_* (atomic: the frame thread's commands set it, the game thread steps it)
    int hold_after;                 // a hold was on when the capture started: back to it after
    float ratio;                    // the fixed step, in 1/30 s
    float hold_ratio;
    int warm, left, tail;           // updates left in each phase
    int64_t updates;                // game updates at the fixed step (all of them)
    int64_t rec_updates;            // ... while recording
    int64_t wanted;                 // frames asked for (rec updates = 2 x frames + REC_SLACK)
    volatile int *record;           // savagewind.exe's g_record_frames (F12 recording), NULL: not there
    int own_record;                 // (game thread only) a capture runs: it writes *record until the capture is over
    float *ctrl_step;               // the controller hold counters' step (site controller_step), NULL: not owned
} SmoothCap;

static SmoothCap g_sc;

#define LOAD(x) __atomic_load_n(&(x), __ATOMIC_ACQUIRE)
#define STORE(x, v) __atomic_store_n(&(x), (v), __ATOMIC_RELEASE)
static void sc_move(int from, int to) { __atomic_compare_exchange_n(&g_sc.state, &from, to, 0, __ATOMIC_ACQ_REL,
                                                                    __ATOMIC_ACQUIRE); }

static int sc_capturing(void) { return LOAD(g_sc.state) != SC_OFF; }

static double sc_now_s(void) {
    static double hz;
    long long t = 0, f = 0;
    if (hz <= 0) hz = QueryPerformanceFrequency(&f) && f > 0 ? (double)f : 1e7;
    QueryPerformanceCounter(&t);
    return (double)t / hz;
}

// The fixed step for a video at fps frames per second: half the frame time (one saved present in two), in 1/30 s.
static float sc_ratio_for(int fps) { return 15.0f / (float)fps; }

// The game's interval for a step (framepace.c: 1 above ~43 fps, 2 below ~35, 3 below ~23).
static uint32_t sc_interval(float ratio) { return ratio < 0.7f ? 1 : ratio > 1.3f ? 3 : 2; }

// The F12 recording follows the capture: on while recording, off in every other state of a capture. Only the game
// thread calls this and only it writes *record and own_record (a stop from the command thread just sets the state),
// so a stop racing the warm-up's end can't leave the recording on. When the capture is over (off or hold) it is
// written 0 one last time and left to the player's F12 again.
static void sc_sync_record(int st) {
    if (st == SC_WARM || st == SC_REC || st == SC_TAIL) {
        g_sc.own_record = 1;
        if (g_sc.record) *g_sc.record = st == SC_REC;
    } else if (g_sc.own_record) {
        if (g_sc.record) *g_sc.record = 0;
        g_sc.own_record = 0;
    }
}

static float sc_step_now(void) { return LOAD(g_sc.state) == SC_HOLD ? g_sc.hold_ratio : g_sc.ratio; }

// After the runtime's rt_vfr_update(vfr): the fixed step while a capture runs, the phases counted.
// prev_iv: the interval the game had before this update.
static void sc_after_update(uint64_t vfr, uint32_t prev_iv) {
    int st = LOAD(g_sc.state);
    double now = sc_now_s();
    static double last;
    double real = last > 0 && now > last ? (now - last) * 30 : 1;  // (the real frame length, in 1/30 s)
    last = now;
    if (st == SC_OFF) {
        sc_sync_record(st);  // (after a stop: the recording ends here, on the game thread)
        if (g_sc.ctrl_step) {
            float s = (float)real;
            *g_sc.ctrl_step = s < 0.1f ? 0.1f : s > 1.5f ? 1.5f : s;
        }
        return;
    }
    float ratio = st == SC_HOLD ? g_sc.hold_ratio : g_sc.ratio;
    float was = VF(float, vfr, VFR_RATIO);
    if (!(was > 0 && was < 0.01f)) {  // (a near stop the runtime asked for: its free camera; left as it is)
        uint32_t iv = sc_interval(ratio);
        VF(float, vfr, VFR_RATIO) = ratio;
        VF(float, vfr, VFR_NEW_RATIO) = ratio;
        VF(uint32_t, vfr, VFR_INTERVAL) = iv;
        VF(uint8_t, vfr, VFR_CHANGED) = prev_iv && iv != prev_iv;
    }
    if (g_sc.ctrl_step) *g_sc.ctrl_step = ratio;
    __atomic_fetch_add(&g_sc.updates, 1, __ATOMIC_RELAXED);
    switch (st) {
    case SC_WARM:
        if (--g_sc.warm <= 0) sc_move(SC_WARM, SC_REC);
        break;
    case SC_REC:
        __atomic_fetch_add(&g_sc.rec_updates, 1, __ATOMIC_RELAXED);
        if (--g_sc.left <= 0) {
            g_sc.tail = TAIL_UPDATES;
            sc_move(SC_REC, SC_TAIL);
        }
        break;
    case SC_TAIL:
        if (--g_sc.tail <= 0) sc_move(SC_TAIL, g_sc.hold_after ? SC_HOLD : SC_OFF);
        break;
    default:
        break;
    }
    sc_sync_record(LOAD(g_sc.state));
}

// Start a capture of `frames` frames for a video at `fps`. false: bad numbers or no recording in this runtime.
static int sc_start(int64_t frames, int fps, const char **why) {
    if (frames < 1 || frames > 1000000) { *why = "frames must be 1 to 1000000"; return 0; }
    if (fps < FPS_MIN || fps > FPS_MAX) { *why = "fps must be 15 to 150"; return 0; }
    if (!g_sc.record) { *why = "this Savage Wind has no g_record_frames (F12 recording)"; return 0; }
    int st = LOAD(g_sc.state);
    if (st == SC_WARM || st == SC_REC || st == SC_TAIL) { *why = "a capture is already running"; return 0; }
    g_sc.hold_after = st == SC_HOLD;
    g_sc.ratio = sc_ratio_for(fps);
    g_sc.warm = WARM_UPDATES;
    g_sc.left = (int)(frames * 2 + REC_SLACK);  // (one present in two is saved; the tool drops the extras)
    g_sc.wanted = frames;
    STORE(g_sc.rec_updates, 0);
    STORE(g_sc.state, SC_WARM);
    return 1;
}

// (any thread) the capture ends: the game thread's next update turns the recording off (sc_sync_record).
static void sc_stop(void) {
    g_sc.hold_after = 0;
    STORE(g_sc.state, SC_OFF);
}

// (modunload, after the imports are back: no more updates come) the recording off if a capture still had it.
static void sc_release(void) {
    if (g_sc.own_record && g_sc.record) *g_sc.record = 0;
    g_sc.own_record = 0;
}

// ------------------------------------------------------------------ swgame.dll's imports (PE32+ import table)
#define RD16(p) (*(const uint16_t *)(const void *)(p))
#define RD32(p) (*(const uint32_t *)(const void *)(p))

// Replace the import `fn` from `dll` in the loaded module m's import address table with repl. Returns the old
// address, NULL: not imported there.
static void *iat_patch(void *m, const char *dll, const char *fn, void *repl) {
    uint8_t *base = (uint8_t *)m;
    if (!m || RD16(base) != 0x5a4d) return NULL;                       // "MZ"
    uint8_t *nt = base + RD32(base + 0x3c);
    if (RD32(nt) != 0x00004550 || RD16(nt + 24) != 0x20b) return NULL;  // "PE\0\0", PE32+
    uint32_t imports = RD32(nt + 24 + 112 + 8);                          // data directory 1: imports
    if (!imports) return NULL;
    for (uint8_t *d = base + imports; RD32(d + 12); d += 20) {           // IMAGE_IMPORT_DESCRIPTOR
        if (_stricmp((const char *)(base + RD32(d + 12)), dll)) continue;
        uint32_t lookup = RD32(d) ? RD32(d) : RD32(d + 16);
        uint64_t *names = (uint64_t *)(void *)(base + lookup), *iat = (uint64_t *)(void *)(base + RD32(d + 16));
        for (; *names; names++, iat++) {
            if (*names >> 63) continue;                                   // (by ordinal)
            if (strcmp((const char *)(base + (uint32_t)*names + 2), fn)) continue;
            unsigned long old;
            if (!VirtualProtect(iat, sizeof *iat, SC_PAGE_READWRITE, &old)) return NULL;
            void *was = (void *)(uintptr_t)__atomic_exchange_n(iat, (uint64_t)(uintptr_t)repl, __ATOMIC_ACQ_REL);
            VirtualProtect(iat, sizeof *iat, old, &old);
            return was;
        }
    }
    return NULL;
}

typedef void (*VfrFn)(uint64_t);
typedef float (*GuiSpeedFn)(float);
typedef float (*ArrowFn)(uint64_t, float);
typedef float (*SndFloorFn)(void);
static VfrFn real_vfr;
static GuiSpeedFn real_gui_speed;
static ArrowFn real_arrow_gravity;
static SndFloorFn real_snd_floor;

static void my_vfr(uint64_t vfr) {
    uint32_t prev_iv = VF(uint32_t, vfr, VFR_INTERVAL);
    real_vfr(vfr);
    sc_after_update(vfr, prev_iv);
}
static float my_gui_speed(float game) {
    float r = real_gui_speed(game);
    return sc_capturing() ? sc_step_now() : r;
}
static float my_arrow_gravity(uint64_t arrow, float g) {
    return sc_capturing() ? g * sc_step_now() : real_arrow_gravity(arrow, g);
}
static float my_snd_floor(void) { return sc_capturing() ? 0.5f * sc_step_now() : real_snd_floor(); }

#define RUNTIME_DLL "savagewind.exe"
static void *g_game;  // the module whose imports were replaced

// Replace the imports in swgame.dll (SWMOD_REAL_GAME_LIB as modhost loaded it, else a module named swgame.dll).
static int sc_patch_game(void) {
    void *cand[2] = {NULL, GetModuleHandleW(L"swgame.dll")};
    wchar_t path[1024];
    unsigned long n = GetEnvironmentVariableW(L"SWMOD_REAL_GAME_LIB", path, 1024);
    if (n && n < 1024) cand[0] = GetModuleHandleW(path);
    for (int i = 0; i < 2; i++) {
        void *v = cand[i] ? iat_patch(cand[i], RUNTIME_DLL, "rt_vfr_update", (void *)my_vfr) : NULL;
        if (!v) continue;
        real_vfr = (VfrFn)v;
        g_game = cand[i];
        real_gui_speed = (GuiSpeedFn)iat_patch(g_game, RUNTIME_DLL, "rt_gui_speed", (void *)my_gui_speed);
        real_arrow_gravity = (ArrowFn)iat_patch(g_game, RUNTIME_DLL, "rt_arrow_gravity", (void *)my_arrow_gravity);
        real_snd_floor = (SndFloorFn)iat_patch(g_game, RUNTIME_DLL, "rt_snd_frame_floor", (void *)my_snd_floor);
        return 1;
    }
    return 0;
}

#ifndef SMOOTHCAP_SELFTEST
// ------------------------------------------------------------------ commands
static const SwModApi *api;

static void reply_status(SwModReply *r) {
    int st = LOAD(g_sc.state);
    api->reply_str(r, "state", k_state[st >= SC_OFF && st <= SC_TAIL ? st : 0]);
    api->reply_num(r, "ratio", st == SC_OFF ? 0 : sc_step_now());
    api->reply_num(r, "step_ms", st == SC_OFF ? 0 : sc_step_now() * 1000.0 / 30.0);
    api->reply_int(r, "updates", LOAD(g_sc.updates));
    api->reply_int(r, "rec_updates", LOAD(g_sc.rec_updates));
    api->reply_int(r, "frames", g_sc.wanted);
    api->reply_int(r, "left", st == SC_REC ? g_sc.left : st == SC_WARM ? g_sc.wanted * 2 + REC_SLACK : 0);
    api->reply_bool(r, "record_available", g_sc.record != NULL);
    api->reply_bool(r, "recording", g_sc.record && *g_sc.record);  // (the F12 recording itself: off after a stop)
    api->reply_bool(r, "clock_hooked", real_vfr != NULL);
    api->reply_bool(r, "controller_step", g_sc.ctrl_step != NULL);
}

static void cmd_smoothcap(int argc, char **argv, SwModReply *r) {
    const char *sub = argc > 1 ? argv[1] : "status";
    if (!strcmp(sub, "status")) { reply_status(r); return; }  // (also unhooked: the tool reads clock_hooked)
    if (!real_vfr) { api->reply_fail(r, "smoothcap: the game's clock isn't hooked (see savagewind.log)"); return; }
    if (!strcmp(sub, "start")) {
        int64_t frames = argc > 2 ? strtoll(argv[2], NULL, 10) : 0;
        int fps = argc > 3 ? atoi(argv[3]) : 60;
        const char *why = "";
        if (!sc_start(frames, fps, &why)) { api->reply_fail(r, "smoothcap start: %s", why); return; }
        api->log("smoothcap: capture of %lld frames at %d fps (step %.3f ms)", (long long)frames, fps,
                 g_sc.ratio * 1000.0 / 30.0);
        reply_status(r);
    } else if (!strcmp(sub, "stop")) {
        sc_stop();
        reply_status(r);
    } else if (!strcmp(sub, "hold")) {
        const char *v = argc > 2 ? argv[2] : "60";
        if (!strcmp(v, "off")) {
            g_sc.hold_after = 0;
            sc_move(SC_HOLD, SC_OFF);
        } else {
            int fps = atoi(v);
            if (fps < FPS_MIN || fps > FPS_MAX) { api->reply_fail(r, "smoothcap hold: fps must be 15 to 150"); return; }
            g_sc.hold_ratio = sc_ratio_for(fps);
            if (LOAD(g_sc.state) == SC_OFF) STORE(g_sc.state, SC_HOLD);
            else g_sc.hold_after = 1;
        }
        reply_status(r);
    } else {
        api->reply_fail(r, "smoothcap: start <frames> [fps] | hold <fps>|off | stop | status");
    }
}

static const SwModCommand commands[] = {
    {"smoothcap", "start <frames> [fps] | hold <fps>|off | stop | status",
     "Smooth capture: a fixed game step, every frame saved (tools/smooth_capture.py)", cmd_smoothcap, 0},
};

SWMOD_EXPORT int swmod_init(const SwModApi *a) {
    if (!SWMOD_API_OK(a, 1)) return 1;
    api = a;
    g_sc.record = (volatile int *)GetProcAddress(GetModuleHandleW(NULL), "g_record_frames");
    if (!sc_patch_game()) {
        api->log("smoothcap: swgame.dll doesn't import rt_vfr_update from savagewind.exe: not this Savage Wind? "
                 "(made for 0.4.1-alpha.4); the commands only answer with an error");
    }
    // The controller step is ours only when the clock hook is in (it's written from there): without the hook nobody
    // would write it. The tool checks controller_step in the status before a capture.
    const char *own = getenv("SAVAGEWIND_NO_CONTROLLER_STEP");
    if (own && atoi(own) != 0 && real_vfr) {
        uint64_t site = api->find_site("controller_step");
        g_sc.ctrl_step = site ? (float *)(uintptr_t)site : NULL;
    } else if (own && atoi(own) != 0) {
        api->log("smoothcap: SAVAGEWIND_NO_CONTROLLER_STEP=1 but the clock isn't hooked: the controller step isn't "
                 "written by anyone; start the game without it");
    }
    api->control_register(commands, (int)(sizeof commands / sizeof *commands));
    api->log("smoothcap: ready (clock %s, gui %s, arrows %s, sound %s, recording %s, controller step %s)",
             real_vfr ? "hooked" : "NOT hooked", real_gui_speed ? "yes" : "no", real_arrow_gravity ? "yes" : "no",
             real_snd_floor ? "yes" : "no", g_sc.record ? "yes" : "no", g_sc.ctrl_step ? "ours" : "the runtime's");
    return 0;
}

// (modload / modunload) the imports back as they were
SWMOD_EXPORT void swmod_exit(const SwModApi *a) {
    (void)a;
    sc_stop();
    if (g_game) {
        if (real_vfr) iat_patch(g_game, RUNTIME_DLL, "rt_vfr_update", (void *)real_vfr);
        if (real_gui_speed) iat_patch(g_game, RUNTIME_DLL, "rt_gui_speed", (void *)real_gui_speed);
        if (real_arrow_gravity) iat_patch(g_game, RUNTIME_DLL, "rt_arrow_gravity", (void *)real_arrow_gravity);
        if (real_snd_floor) iat_patch(g_game, RUNTIME_DLL, "rt_snd_frame_floor", (void *)real_snd_floor);
    }
    sc_release();
}
#endif
