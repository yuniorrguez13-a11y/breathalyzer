// smoothcap's self-test (no game), built as a DLL: smoothcap_selftest(lines, cap) returns how many checks failed and
// puts their source lines in lines[]. Checks the import table patch (on this DLL's own import of GetCurrentProcessId)
// and the fixed-step phases on a made-up ksys::VFR. Built and run by: python tools/smooth_capture.py selftest
#define SMOOTHCAP_SELFTEST
#include "smoothcap.c"

__declspec(dllimport) unsigned long SC_WINAPI GetCurrentProcessId(void);
extern char __ImageBase;  // (this DLL's own base, from the linker)

static int fails, *fail_lines, fail_cap;
#define CHECK(cond)                                                \
    do {                                                           \
        if (!(cond)) {                                             \
            if (fails < fail_cap) fail_lines[fails] = __LINE__;    \
            fails++;                                               \
        }                                                          \
    } while (0)

static unsigned long SC_WINAPI fake_pid(void) { return 4242; }

// The runtime's rt_vfr_update, as framepace.c does it: the measured step (here a slow 1.5) and its interval.
static int runtime_calls;
static void fake_runtime_vfr(uint64_t vfr) {
    runtime_calls++;
    VF(float, vfr, VFR_RATIO) = 1.5f;
    VF(float, vfr, VFR_NEW_RATIO) = 1.5f;
    VF(uint32_t, vfr, VFR_INTERVAL) = 3;
    VF(uint8_t, vfr, VFR_CHANGED) = 0;
}
static void fake_freecam_vfr(uint64_t vfr) { VF(float, vfr, VFR_RATIO) = 0.001f; }
static float fake_gui(float g) { return g * 3; }
static float fake_arrow(uint64_t a, float g) { (void)a; return g * 1.5f; }
static float fake_snd(void) { return 0.75f; }

__declspec(dllexport) int smoothcap_selftest(int *lines, int cap) {
    fails = 0, fail_lines = lines, fail_cap = cap;
    void *self = &__ImageBase;

    // 1. the import table: this DLL's own import of GetCurrentProcessId
    unsigned long pid = GetCurrentProcessId();
    void *was = iat_patch(self, "KERNEL32.dll", "GetCurrentProcessId", (void *)fake_pid);
    CHECK(was != NULL);
    CHECK(GetCurrentProcessId() == 4242);
    CHECK(iat_patch(self, "kernel32.DLL", "GetCurrentProcessId", was) == (void *)fake_pid);
    CHECK(GetCurrentProcessId() == pid);
    CHECK(iat_patch(self, "nosuch.dll", "GetCurrentProcessId", (void *)fake_pid) == NULL);
    CHECK(iat_patch(self, "KERNEL32.dll", "NoSuchFunction", (void *)fake_pid) == NULL);
    CHECK(iat_patch(NULL, "KERNEL32.dll", "GetCurrentProcessId", (void *)fake_pid) == NULL);
    CHECK(!sc_patch_game());  // (no swgame.dll in this process: nothing hooked, nothing changed)
    CHECK(GetCurrentProcessId() == pid);

    // 2. the phases, on a made-up VFR with the runtime's update in front
    static uint8_t vfr_mem[0x100];
    uint64_t vfr = (uint64_t)(uintptr_t)vfr_mem;
    static volatile int record;
    static float ctrl = 1;
    memset(&g_sc, 0, sizeof g_sc);
    runtime_calls = 0;
    real_vfr = fake_runtime_vfr;
    real_gui_speed = fake_gui;
    real_arrow_gravity = fake_arrow;
    real_snd_floor = fake_snd;
    g_sc.record = &record;
    g_sc.ctrl_step = &ctrl;
    const char *why = "";

    my_vfr(vfr);  // off: the runtime's step stays
    CHECK(VF(float, vfr, VFR_RATIO) == 1.5f && VF(uint32_t, vfr, VFR_INTERVAL) == 3);
    CHECK(my_gui_speed(1) == 3 && my_arrow_gravity(0, 2) == 3 && my_snd_floor() == 0.75f);
    CHECK(ctrl >= 0.1f && ctrl <= 1.5f);  // (off: the measured frame length)

    CHECK(!sc_start(0, 60, &why));
    CHECK(!sc_start(10, 200, &why));
    CHECK(sc_start(10, 60, &why));
    CHECK(!sc_start(10, 60, &why));  // (one at a time)
    CHECK(g_sc.state == SC_WARM && g_sc.ratio == 0.25f);
    int saved_presents = 0, rec_updates = 0;
    for (int i = 0; i < 200; i++) {
        my_vfr(vfr);
        if (g_sc.state != SC_OFF) {
            CHECK(VF(float, vfr, VFR_RATIO) == 0.25f && VF(float, vfr, VFR_NEW_RATIO) == 0.25f);
            CHECK(VF(uint32_t, vfr, VFR_INTERVAL) == 1);
            CHECK(ctrl == 0.25f);
            CHECK(my_gui_speed(1) == 0.25f && my_arrow_gravity(0, 2) == 0.5f && my_snd_floor() == 0.125f);
        }
        if (record) {
            rec_updates++;
            if (rec_updates % 2 == 0) saved_presents++;  // (one present in two is saved)
        }
        if (i == 0) CHECK(VF(uint8_t, vfr, VFR_CHANGED) == 1);  // (3 -> 1 on the first fixed update)
        if (i == 1) CHECK(VF(uint8_t, vfr, VFR_CHANGED) == 0);  // (the game had 1 from the last update)
    }
    CHECK(runtime_calls == 201);
    CHECK(g_sc.state == SC_OFF && record == 0);
    CHECK(rec_updates == 20 + REC_SLACK && saved_presents >= 10);
    CHECK(g_sc.updates == WARM_UPDATES + 20 + REC_SLACK + TAIL_UPDATES);
    CHECK(VF(float, vfr, VFR_RATIO) == 1.5f);  // (back to the runtime's)

    // hold, and a capture inside a hold returns to it
    g_sc.hold_ratio = sc_ratio_for(30);
    STORE(g_sc.state, SC_HOLD);
    my_vfr(vfr);
    CHECK(VF(float, vfr, VFR_RATIO) == 0.5f && record == 0);
    CHECK(sc_start(2, 60, &why) && g_sc.hold_after);
    for (int i = 0; i < 40; i++) my_vfr(vfr);
    CHECK(g_sc.state == SC_HOLD && VF(float, vfr, VFR_RATIO) == 0.5f);
    sc_stop();
    my_vfr(vfr);
    CHECK(g_sc.state == SC_OFF && VF(float, vfr, VFR_RATIO) == 1.5f);

    // a stop from the command thread: only the next game update turns the recording off, and then leaves it alone
    CHECK(sc_start(5, 60, &why));
    for (int i = 0; i < WARM_UPDATES + 1; i++) my_vfr(vfr);
    CHECK(g_sc.state == SC_REC && record == 1);
    sc_stop();
    CHECK(record == 1);  // (the command thread doesn't write it)
    my_vfr(vfr);
    CHECK(g_sc.state == SC_OFF && record == 0 && !g_sc.own_record);
    record = 1;  // (the player's own F12 afterwards)
    my_vfr(vfr);
    CHECK(record == 1);
    record = 0;
    // a stop during the warm-up: the recording never comes on
    CHECK(sc_start(5, 60, &why));
    for (int i = 0; i < WARM_UPDATES - 1; i++) my_vfr(vfr);
    sc_stop();
    for (int i = 0; i < 5; i++) my_vfr(vfr);
    CHECK(g_sc.state == SC_OFF && record == 0);
    // unload during a recording: off
    CHECK(sc_start(5, 60, &why));
    for (int i = 0; i < WARM_UPDATES + 1; i++) my_vfr(vfr);
    CHECK(record == 1);
    sc_stop();
    sc_release();
    CHECK(record == 0 && !g_sc.own_record);

    // a near stop the runtime asked for (its free camera) is left alone
    CHECK(sc_start(1, 30, &why));
    real_vfr = fake_freecam_vfr;
    my_vfr(vfr);
    CHECK(VF(float, vfr, VFR_RATIO) == 0.001f);
    real_vfr = fake_runtime_vfr;
    sc_stop();

    // no recording in the runtime: start refuses
    g_sc.record = NULL;
    CHECK(!sc_start(5, 60, &why) && strstr(why, "g_record_frames"));
    (void)k_state;
    return fails;
}
