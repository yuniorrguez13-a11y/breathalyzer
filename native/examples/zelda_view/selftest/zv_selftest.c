// Self-test of zelda_view.c without the game (tests/test_zelda_view.py builds and runs it).
//
// It includes zelda_view.c and drives its command, its three hooks and its tick against a made-up world: a Link
// actor with a model (a skeleton with the bones the mod looks up by name), an armor piece, an equipped two-handed
// weapon, an animation holder with a body animation and the weapon parameter, and a fake game call for every
// function the mod uses (spawn request, handle, link, bone follow, animation, visibility, setter). Nothing here is
// game code or game data: the bone and animation names are the names the game uses (data), the rest is made up.
// Its pointers are this process's memory, so the memory check allows any address here (GUEST_LO / GUEST_HI). The
// globals the tick reads (player info, spawner, scene object, event manager, the two static name objects) get pages
// of their own at their real addresses (VirtualAlloc; those checks SKIP if that range is taken).
//
// zv_selftest(out, cap) writes one line per check ("<group> <check> PASS|FAIL <detail>") and returns the number of
// failed checks.
#define GUEST_LO 0x10000ull
#define GUEST_HI 0x7ffffffe0000ull
#include "../zelda_view.c"

__declspec(dllimport) void *__stdcall VirtualAlloc(void *addr, size_t size, unsigned long type, unsigned long protect);

static char *g_out;
static int g_cap, g_len, g_fails;
static void say(const char *fmt, ...) {
    if (!g_out || g_len >= g_cap) return;
    __builtin_va_list ap;
    __builtin_va_start(ap, fmt);
    int n = vsnprintf(g_out + g_len, (size_t)(g_cap - g_len), fmt, ap);
    __builtin_va_end(ap);
    if (n > 0) g_len = g_len + n < g_cap ? g_len + n : g_cap - 1;
}
__attribute__((format(printf, 4, 5))) static void check(const char *group, const char *name, int ok,
                                                        const char *fmt, ...) {
    char detail[256];
    __builtin_va_list ap;
    __builtin_va_start(ap, fmt);
    vsnprintf(detail, sizeof detail, fmt, ap);
    __builtin_va_end(ap);
    if (!ok) g_fails++;
    say("%s %s %s %s\n", group, name, ok ? "PASS" : "FAIL", detail);
}

static int approx(float a, float b) { return a - b < 1e-5f && b - a < 1e-5f; }

#define W64(p, off, v) (*(uint64_t *)(void *)((uint8_t *)(p) + (off)) = (uint64_t)(v))
#define W32(p, off, v) (*(uint32_t *)(void *)((uint8_t *)(p) + (off)) = (uint32_t)(v))
#define W16(p, off, v) (*(uint16_t *)(void *)((uint8_t *)(p) + (off)) = (uint16_t)(v))
#define R64(p, off) (*(uint64_t *)(void *)((uint8_t *)(p) + (off)))
#define R32(p, off) (*(uint32_t *)(void *)((uint8_t *)(p) + (off)))
#define FAKE_ISA 0x7000005000ull  // (made up: a function address only the fake call knows)

// ------------------------------------------------------------------ the fake api: replies, settings, log
static char g_reply_text[256], g_reply_fail[256];
static int g_reply_fields, g_reply_has_tick;
static void f_reply_str(SwModReply *r, const char *k, const char *v) {
    (void)r, (void)v;
    g_reply_fields++;
    if (!strcmp(k, "tick")) g_reply_has_tick = 1;
}
static void f_reply_int(SwModReply *r, const char *k, int64_t v) { (void)r, (void)k, (void)v, g_reply_fields++; }
static void f_reply_num(SwModReply *r, const char *k, double v) { (void)r, (void)k, (void)v, g_reply_fields++; }
static void f_reply_bool(SwModReply *r, const char *k, bool v) { (void)r, (void)k, (void)v, g_reply_fields++; }
static void f_reply_text(SwModReply *r, const char *fmt, ...) {
    (void)r;
    __builtin_va_list ap;
    __builtin_va_start(ap, fmt);
    vsnprintf(g_reply_text, sizeof g_reply_text, fmt, ap);
    __builtin_va_end(ap);
}
static void f_reply_fail(SwModReply *r, const char *fmt, ...) {
    (void)r;
    __builtin_va_list ap;
    __builtin_va_start(ap, fmt);
    vsnprintf(g_reply_fail, sizeof g_reply_fail, fmt, ap);
    __builtin_va_end(ap);
}
static bool f_dev(void) { return true; }
static void f_log(const char *fmt, ...) { (void)fmt; }

static struct {
    int secret, shoulders, heavy, zelda;
    char buff[16];
    double back, side;
} g_set = {1, 1, 1, 1, "extreme", 0, 0};
static int64_t f_setting_int(const struct SwModApi *self, const char *key, int64_t def) {
    (void)self;
    if (!strcmp(key, "secret_word")) return g_set.secret;
    if (!strcmp(key, "shoulders")) return g_set.shoulders;
    if (!strcmp(key, "heavy_fast")) return g_set.heavy;
    if (!strcmp(key, "zelda")) return g_set.zelda;
    return def;
}
static double f_setting_num(const struct SwModApi *self, const char *key, double def) {
    (void)self;
    if (!strcmp(key, "seat_back")) return g_set.back;
    if (!strcmp(key, "seat_side")) return g_set.side;
    return def;
}
static int f_setting_str(const struct SwModApi *self, const char *key, char *buf, size_t cap) {
    (void)self;
    if (strcmp(key, "buff")) return -1;
    snprintf(buf, cap, "%s", g_set.buff);
    return (int)strlen(g_set.buff);
}

// ------------------------------------------------------------------ a made-up skeleton with a model chain
#define FB 20
static const char *const LINK_BONES[FB] = {
    "Root", "Spine_1", "Spine_2", "Clavicle_L", "Clavicle_R", "Clavicle_Assist_L", "Clavicle_Assist_R", "Arm_1_L",
    "Arm_1_R", "Arm_1_Assist_L", "Arm_1_Assist_R", "Arm_2_L", "Arm_2_R", "Elbow_L", "Elbow_R", "Wrist_Assist_L",
    "Wrist_Assist_R", "Neck", "Weapon_R", "Hand_L"};

typedef struct {
    _Alignas(16) uint8_t sk[0x60];
    _Alignas(16) uint8_t res[0x48];
    _Alignas(16) uint8_t dict[0x20 + 0x10 * FB];
    char keys[FB][24];
    _Alignas(16) uint8_t rbones[FB * 0x50];
    _Alignas(16) float world[FB][16];
    _Alignas(16) uint8_t local[FB * 0x60];
    _Alignas(16) uint8_t model[0x48], unit[0x120];
    uint64_t units[1], slot0[1];
    int nb;
} FakeSkel;

static void fake_skel(FakeSkel *s, const char *const *names, int nb, uint8_t *actor) {
    memset(s, 0, sizeof *s);
    s->nb = nb;
    W64(s->sk, 0, s->res);
    W64(s->sk, SKEL_LOCAL, s->local);
    W64(s->sk, SKEL_WORLD, s->world);
    W16(s->sk, SKEL_NBONES, nb);
    W32(s->res, 0, FSKL_MAGIC);
    W64(s->res, RES_DICT, s->dict);
    W64(s->res, RES_BONES, s->rbones);
    W16(s->res, RES_NBONES, nb);
    for (int b = 0; b < nb; b++) {
        size_t n = strlen(names[b]);
        *(uint16_t *)(void *)s->keys[b] = (uint16_t)n;
        memcpy(s->keys[b] + 2, names[b], n + 1);
        W64(s->dict, 0x20 + 0x10 * b, s->keys[b]);
        float rest[3] = {0.15f + 0.01f * (float)b, 0.02f, -0.01f};
        memcpy(s->rbones + b * 0x50 + 0x44, rest, sizeof rest);
        memcpy(s->local + b * 0x60 + 0x50, rest, sizeof rest);
        float *w = s->world[b];
        w[0] = w[5] = w[10] = w[15] = 1.0f;
        w[1] = 0.5f, w[6] = 0.25f;  // (not a pure identity: the scale must act on whole columns)
        w[12] = 1.0f + 0.1f * (float)b, w[13] = 1.2f, w[14] = 2.0f;
    }
    W64(s->model, 0, MODEL_VPTR);
    W32(s->model, MODEL_NUNITS, 1);
    W64(s->model, MODEL_UNITS, s->units);
    s->units[0] = P(s->slot0), s->slot0[0] = P(s->unit);
    W64(s->unit, 0, UNIT_VPTR_A);
    W64(s->unit, UNIT_SKEL, s->sk);
    if (actor) W64(actor, ACTOR_MODEL, s->model);
}

static int bone_of(const FakeSkel *s, const char *name) {
    for (int b = 0; b < s->nb; b++)
        if (!strcmp(s->keys[b] + 2, name)) return b;
    return -1;
}

// ------------------------------------------------------------------ the fake orig of the skin upload
static uint64_t g_orig_x0;
static float g_orig_seen[16];  // the world matrix of bone g_orig_bone as the "game" read it
static int g_orig_bone, g_orig_calls;
static void fake_upload(struct Cpu *c) {
    uint64_t sk = SWMOD_REGS(c)->x[0];
    g_orig_x0 = sk;
    g_orig_calls++;
    uint64_t w = R64((void *)(uintptr_t)sk, SKEL_WORLD);
    memcpy(g_orig_seen, (const void *)(uintptr_t)(w + (uint64_t)g_orig_bone * 0x40), sizeof g_orig_seen);
    *(uint16_t *)(uintptr_t)(sk + SKEL_FLAGS) |= 8;  // (the game's call sets bit 3 on what it got)
}

static _Alignas(16) uint8_t g_cpu[4096];
static struct Cpu *cpu(void) { return (struct Cpu *)(void *)g_cpu; }

static SwModApi g_fake_api;
static void fake_guest_call(struct Cpu *c, uint64_t fn);

static void setup(void) {
    memset(&g_fake_api, 0, sizeof g_fake_api);
    g_fake_api.api_version = SWMOD_API_VERSION;
    g_fake_api.size = sizeof g_fake_api;
    g_fake_api.game_version = "1.9.0";
    g_fake_api.mod_name = "zelda_view";
    g_fake_api.log = f_log;
    g_fake_api.reply_str = f_reply_str;
    g_fake_api.reply_int = f_reply_int;
    g_fake_api.reply_num = f_reply_num;
    g_fake_api.reply_bool = f_reply_bool;
    g_fake_api.reply_text = f_reply_text;
    g_fake_api.reply_fail = f_reply_fail;
    g_fake_api.dev_mode = f_dev;
    g_fake_api.guest_call = fake_guest_call;
    g_fake_api.mod_id = "botw-mod-loader.zelda-view";
    g_fake_api.setting_int = f_setting_int;
    g_fake_api.setting_num = f_setting_num;
    g_fake_api.setting_str = f_setting_str;
    api = &g_fake_api;
    QueryPerformanceFrequency(&g_qpf);
    g_hook_skin = g_hook_world = g_hook_setter = g_hook_frame = 1;
    g_tick_src = TICK_HOOK;
}

// ------------------------------------------------------------------ checks: the command
static int g_pack_there;
static int fake_pack(void) { return g_pack_there; }

static void cmd(const char *a, const char *b) {
    char *argv[3] = {(char *)ZV_WORD, (char *)a, (char *)b};
    g_reply_text[0] = g_reply_fail[0] = 0;
    g_reply_fields = g_reply_has_tick = 0;
    cmd_egg(1 + (a != NULL) + (b != NULL), argv, NULL);
}

static void check_command(void) {
    g_pack_fn = fake_pack;
    g_pack_there = 1;
    __atomic_store_n(&g_want, 0, __ATOMIC_RELAXED);
    cmd(NULL, NULL);
    check("cmd", "toggle_on", g_want == 1 && !strcmp(g_reply_text, "Link from Zelda's view: ON"), "want %d, '%s'",
          g_want, g_reply_text);
    cmd(NULL, NULL);
    check("cmd", "toggle_off", g_want == 0 && !strcmp(g_reply_text, "Link from Zelda's view: OFF"), "want %d, '%s'",
          g_want, g_reply_text);
    cmd("on", NULL);
    int on = g_want;
    cmd("off", NULL);
    check("cmd", "on_off", on == 1 && g_want == 0, "on %d then %d", on, g_want);
    g_pack_there = 0;
    cmd(NULL, NULL);
    check("cmd", "note_no_pack", !strcmp(g_reply_text, "Link from Zelda's view: ON (no Zelda: run tools/zelda_view.py "
                                                       "install)"), "'%s'", g_reply_text);
    g_set.zelda = 0;
    cmd("status", NULL);
    check("cmd", "no_note_without_zelda", !strcmp(g_reply_text, "Link from Zelda's view: ON") && g_reply_has_tick &&
          g_want == 1, "'%s', tick field %d", g_reply_text, g_reply_has_tick);
    g_set.zelda = 1;
    cmd("off", NULL);
    g_set.secret = 0;
    cmd(NULL, NULL);
    check("cmd", "secret_word_off", !strcmp(g_reply_fail, "Unknown command: linkfromzeldasview") && g_want == 0 &&
          !g_reply_text[0], "fail '%s', want %d", g_reply_fail, g_want);
    g_set.secret = 1;
    cmd("dance", NULL);
    check("cmd", "bad_word", !strncmp(g_reply_fail, "usage:", 6) && g_want == 0, "'%s'", g_reply_fail);
    g_hook_skin = g_hook_world = g_hook_setter = g_hook_frame = 0;
    g_pack_there = 1;
    cmd("on", NULL);
    check("cmd", "note_no_hooks", !strcmp(g_reply_text, "Link from Zelda's view: ON (needs the hook rebuild)"), "'%s'",
          g_reply_text);
    cmd("off", NULL);
    g_hook_skin = g_hook_world = g_hook_setter = g_hook_frame = 1;
    check("cmd", "registered", !strcmp(commands[0].name, "linkfromzeldasview") && commands[0].flags == SWMOD_CMD_PLAYER &&
          !commands[0].help[0] && !commands[0].args[0], "name %s, flags %u, help '%s'", commands[0].name,
          commands[0].flags, commands[0].help);
}

// ------------------------------------------------------------------ checks: the seat
static void check_seat(void) {
    float m[12];
    seat_offset(m, 0.0f, 0.0f);
    int base = m[3] == -0.097f && m[7] == -0.266f && m[11] == 0.0f && m[1] == 1 && m[6] == 1 && m[8] == 1;
    seat_offset(m, 0.04f, 0.10f);
    int moved = m[7] == -0.266f - 0.04f && m[11] == 0.10f && m[3] == -0.097f;
    float W[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 6, 7, 1}, pos[3], rot[9];
    seat_offset(m, 0.0f, 0.0f);
    seat_world(W, m, pos, rot);
    float det = rot[0] * (rot[4] * rot[8] - rot[5] * rot[7]) - rot[1] * (rot[3] * rot[8] - rot[5] * rot[6]) +
                rot[2] * (rot[3] * rot[7] - rot[4] * rot[6]);
    int world = approx(pos[0], 5.0f - 0.097f) && approx(pos[1], 6.0f - 0.266f) && approx(pos[2], 7.0f) &&
                det > 0.999f && det < 1.001f;
    check("seat", "numbers", base && moved && world, "base %d, moved %d, world %.3f %.3f %.3f det %.3f", base, moved,
          pos[0], pos[1], pos[2], det);
}

// ------------------------------------------------------------------ checks: the three hooks
static FakeSkel g_ls;  // Link's skeleton (also the tick's)

static void one_target(Target *t, int stage_a) {
    memset(t, 0, sizeof *t);
    t->sk = P(g_ls.sk), t->res = P(g_ls.res), t->nb = (uint16_t)g_ls.nb, t->scale_on = 1, t->n = NB;
    for (int k = 0; k < NB; k++) {
        t->bone[k] = (int16_t)bone_of(&g_ls, BUFF_BONES[k]);
        t->scale[k][0] = 1.0f, t->scale[k][1] = SIDE[1][k][0], t->scale[k][2] = SIDE[1][k][1];
    }
    if (stage_a) {
        t->na = 4, t->a_mode = 1;
        for (int j = 0; j < 4; j++) {
            int b = t->bone[STAGE_A_BONES[j]];
            t->abone[j] = (int16_t)b;
            memcpy(t->arest[j], g_ls.rbones + b * 0x50 + 0x44, 12);
            memcpy(t->aout[j], t->arest[j], 12);
            t->aout[j][0] *= STAGE_A_OUT;
        }
    }
}

static void call_skin(void) {
    SWMOD_REGS(cpu())->x[0] = P(g_ls.sk);
    skin_replace(cpu(), fake_upload);
}

static void check_hooks(void) {
    fake_skel(&g_ls, LINK_BONES, FB, NULL);
    int arm = bone_of(&g_ls, "Arm_1_L"), k_arm = 6;  // (BUFF_BONES[6] is Arm_1_L)
    float real_before[FB][16];
    memcpy(real_before, g_ls.world, sizeof real_before);
    g_orig_bone = arm;
    // off: the game's call as it is
    tgt_publish(NULL, 0);
    g_orig_calls = 0;
    call_skin();
    check("skin", "off_passes", g_orig_calls == 1 && g_orig_x0 == P(g_ls.sk), "orig got %llx",
          (unsigned long long)g_orig_x0);
    // on: the game's call gets the shadow with scaled columns; the real matrices are untouched; bit 3 comes back
    Target t;
    one_target(&t, 0);
    tgt_publish(&t, 1);
    W16(g_ls.sk, SKEL_FLAGS, 0);
    int64_t copies = g_n_copies;
    call_skin();
    const float *w = real_before[arm];
    float sy = SIDE[1][k_arm][0], sz = SIDE[1][k_arm][1];
    int scaled = g_orig_x0 == P(g_slot[0].shadow) && g_orig_seen[0] == w[0] && g_orig_seen[1] == w[1] &&
                 g_orig_seen[4] == w[4] * sy && g_orig_seen[5] == w[5] * sy && g_orig_seen[6] == w[6] * sy &&
                 g_orig_seen[8] == w[8] * sz && g_orig_seen[10] == w[10] * sz && g_orig_seen[12] == w[12] &&
                 g_orig_seen[14] == w[14];
    check("skin", "copy_scaled", scaled && g_n_copies == copies + 1, "orig x0 %s, col1 %.3f (want %.3f)",
          g_orig_x0 == P(g_slot[0].shadow) ? "shadow" : "other", g_orig_seen[5], w[5] * sy);
    check("skin", "real_untouched", !memcmp(real_before, g_ls.world, sizeof real_before), "real world changed");
    check("skin", "flag_back", (R32(g_ls.sk, SKEL_FLAGS) & 0xffff) == 8, "flags %x", R32(g_ls.sk, SKEL_FLAGS) & 0xffff);
    // a slot busy on another thread: the game's call as it is
    g_slot_busy[0] = 1;
    call_skin();
    int busy = g_orig_x0 == P(g_ls.sk);
    g_slot_busy[0] = 0;
    check("skin", "busy_passes", busy, "orig got %llx", (unsigned long long)g_orig_x0);
    // a NaN scale: that bone is left as it is, the others are scaled
    t.scale[k_arm][1] = __builtin_nanf("");
    tgt_publish(&t, 1);
    call_skin();
    int nan_ok = g_orig_x0 == P(g_slot[0].shadow) && g_orig_seen[5] == w[5];
    g_orig_bone = bone_of(&g_ls, "Neck");
    call_skin();
    nan_ok = nan_ok && g_orig_seen[5] == real_before[g_orig_bone][5] * SIDE[1][16][0];
    check("skin", "nan_skipped", nan_ok, "arm col1 %.3f", g_orig_seen[5]);
    g_orig_bone = arm;
    one_target(&t, 0);
    // another object at that address (its resource changed): as it is
    t.res += 8;
    tgt_publish(&t, 1);
    int64_t bad = g_n_bad;
    call_skin();
    check("skin", "other_object_passes", g_orig_x0 == P(g_ls.sk) && g_n_bad == bad + 1, "orig got %llx",
          (unsigned long long)g_orig_x0);
    // in place: the game's call gets the real skeleton, scaled; afterwards the real matrices are exactly as before
    one_target(&t, 0);
    tgt_publish(&t, 1);
    g_inplace = 1;
    call_skin();
    g_inplace = 0;
    check("skin", "inplace_restores", g_orig_x0 == P(g_ls.sk) && g_orig_seen[5] == w[5] * sy &&
          !memcmp(real_before, g_ls.world, sizeof real_before), "orig col1 %.3f, restored %d", g_orig_seen[5],
          !memcmp(real_before, g_ls.world, sizeof real_before));

    // the shoulders: the four bones' translation = rest x 1.18 along the bone; restore = rest; a bone flagged
    // "translation zero" is left alone
    one_target(&t, 1);
    tgt_publish(&t, 1);
    for (int j = 0; j < 4; j++) memset(g_ls.local + t.abone[j] * 0x60 + 0x50, 0, 12);
    W32(g_ls.local, t.abone[3] * 0x60, 1u << 27);
    SWMOD_REGS(cpu())->x[0] = P(g_ls.sk);
    world_before(cpu());
    float got0[3], got3[3];
    memcpy(got0, g_ls.local + t.abone[0] * 0x60 + 0x50, 12);
    memcpy(got3, g_ls.local + t.abone[3] * 0x60 + 0x50, 12);
    check("shoulders", "wider", got0[0] == t.arest[0][0] * STAGE_A_OUT && got0[1] == t.arest[0][1] && got3[0] == 0.0f,
          "x %.4f (rest %.4f), flagged bone %.4f", got0[0], t.arest[0][0], got3[0]);
    t.a_mode = 2;
    tgt_publish(&t, 1);
    world_before(cpu());
    memcpy(got0, g_ls.local + t.abone[0] * 0x60 + 0x50, 12);
    check("shoulders", "restore", got0[0] == t.arest[0][0], "x %.4f (rest %.4f)", got0[0], t.arest[0][0]);
    W32(g_ls.local, t.abone[3] * 0x60, 0);
    tgt_publish(NULL, 0);

    // the setter: only Link's holder, parameter 0x2c and the text WeaponLargeSword; the caller's object never written
    static _Alignas(16) uint8_t holder[0x140], other[0x140];
    static char large[] = "WeaponLargeSword", spear[] = "WeaponSpear";
    static _Alignas(16) StrObj obj_large, obj_spear;
    obj_large.vptr = STRING_OBJ_VPTR, obj_large.str = P(large);
    obj_spear.vptr = STRING_OBJ_VPTR, obj_spear.str = P(spear);
    SwModRegs *r = SWMOD_REGS(cpu());
    struct {
        uint64_t holder;
        uint32_t idx;
        uint64_t obj;
        int swap;
    } cases[] = {{P(holder), PARAM_WEAPON, P(&obj_large), 1}, {P(holder), 0x2d, P(&obj_large), 0},
                 {P(other), PARAM_WEAPON, P(&obj_large), 0}, {P(holder), PARAM_WEAPON, P(&obj_spear), 0},
                 {P(holder), PARAM_WEAPON, 0, 0}};
    int ok = 1, n = 0;
    g_heavy_holder = P(holder);
    for (size_t i = 0; i < sizeof cases / sizeof *cases; i++) {
        r->x[0] = cases[i].holder, r->x[1] = cases[i].idx, r->x[2] = cases[i].obj;
        bool skip = setter_before(cpu());
        uint64_t want = cases[i].swap ? STR_SMALL_SWORD : cases[i].obj;
        ok = ok && !skip && r->x[2] == want;
        n++;
    }
    int untouched = !strcmp(large, "WeaponLargeSword") && obj_large.str == P(large);
    g_heavy_holder = 0;
    r->x[0] = P(holder), r->x[1] = PARAM_WEAPON, r->x[2] = P(&obj_large);
    setter_before(cpu());
    int off_ok = r->x[2] == P(&obj_large);
    check("setter", "swap_only_link_0x2c_large", ok && untouched && off_ok, "%d cases, caller's object untouched %d, "
          "off %d", n, untouched, off_ok);
}

// ------------------------------------------------------------------ a made-up world for the tick
typedef struct {
    uint8_t pad[LINKDATA_ID];
    uint32_t id, pad2;
    uint64_t proc;  // 0: gone
} FakeLinkData;
static uint32_t g_next_id = 100;
static void ld_new(FakeLinkData *ld, uint64_t proc) { ld->id = g_next_id++, ld->proc = proc; }
static void ld_kill(FakeLinkData *ld) { ld->proc = 0, ld->id = g_next_id++; }

static struct {
    _Alignas(16) uint8_t link[0x2800];
    FakeLinkData link_ld;
    _Alignas(16) uint8_t pi[0x100];
    _Alignas(16) uint8_t as[0x140], slots[0x50], sub[0x98], def[0x20], params[8], param0[0x20];
    char body[32], hand[16], param_buf[32];
    _Alignas(16) uint8_t weapon[0xd00];
    FakeLinkData weapon_ld;
    _Alignas(16) uint8_t armor_mgr[0x140], armor[0x600];
    FakeLinkData armor_ld;
    FakeSkel armor_skel;
    _Alignas(16) uint8_t creator[0x100], scene[0x40], heap[0x40];
} g_w;
static uint8_t *g_event_mgr;  // (0x1d2c0 bytes)

typedef struct {
    _Alignas(16) uint8_t mem[0x600];
    _Alignas(16) uint8_t as[0x100];
    uint64_t vtable[4];
    FakeLinkData ld;
    struct {
        uint64_t pad, handle;
    } unit;
    int used, ready_in, taken, deleting, dead;
    char current[32];
} FakeZelda;
static FakeZelda g_z[24];
static int g_nz;
static int g_z_ride = 1, g_z_refuse_follow, g_requests, g_deletes, g_releases, g_vis, g_transforms, g_setter_calls;
static int g_follow_sets, g_follow_zero, g_as_plays;
static _Alignas(16) StrObj g_cur_obj;
static char g_cur_text[32];

static FakeZelda *z_of(uint64_t p) {
    for (int i = 0; i < g_nz; i++)
        if (p == P(g_z[i].mem)) return &g_z[i];
    return NULL;
}
static FakeZelda *z_of_handle(uint64_t h) {
    for (int i = 0; i < g_nz; i++)
        if (g_z[i].unit.handle == h && !g_z[i].taken && !g_z[i].dead) return &g_z[i];
    return NULL;
}
static FakeLinkData *ld_of(uint64_t proc) {
    if (proc == P(g_w.link)) return &g_w.link_ld;
    FakeZelda *z = z_of(proc);
    return z ? &z->ld : NULL;
}
static FakeZelda *live_zelda(int *count) {
    FakeZelda *last = NULL;
    *count = 0;
    for (int i = 0; i < g_nz; i++)
        if (g_z[i].used && g_z[i].taken && !g_z[i].dead) (*count)++, last = &g_z[i];
    return last;
}

static void fake_guest_call(struct Cpu *c, uint64_t fn) {
    SwModRegs *r = SWMOD_REGS(c);
    uint64_t a0 = r->x[0];
    FakeZelda *z;
    switch (fn) {
    case HANDLE_CLEAR:
        memset((void *)(uintptr_t)a0, 0, sizeof(Handle));
        return;
    case PACK_CLEAR:
    case PACK_ADD:
    case SET_MTX:
        return;
    case REQUEST_CREATE:
        g_requests++;
        if (strcmp((const char *)(uintptr_t)r->x[1], ZV_SLOT) || g_nz >= 24) {
            r->x[0] = 0;
            return;
        }
        z = &g_z[g_nz++];
        memset(z, 0, sizeof *z);
        z->used = 1, z->ready_in = 2;
        ld_new(&z->ld, P(z->mem));
        z->unit.handle = r->x[3];
        z->vtable[2] = FAKE_ISA;
        W64(z->mem, 0, z->vtable);
        W64(z->mem, ACTOR_AS, z->as);
        W64(z->as, AS_OWNER, z->mem);
        W32(z->as, AS_SLOTS, 1);
        ((Handle *)(uintptr_t)r->x[3])->unit = P(&z->unit);
        r->x[0] = 1;
        return;
    case HANDLE_READY:
        z = z_of_handle(a0);
        r->x[0] = z && z->ready_in == 0;
        return;
    case HANDLE_FAILED:
        r->x[0] = 0;
        return;
    case HANDLE_PEEK:
        z = z_of_handle(a0);
        r->x[0] = z ? P(z->mem) : 0;
        return;
    case HANDLE_TAKE:
        z = z_of_handle(a0);
        if (z) z->taken = 1, z->unit.handle = 0;
        memset((void *)(uintptr_t)a0, 0, sizeof(Handle));
        return;
    case HANDLE_RELEASE:
    case HANDLE_RELEASE_FAILED:
        g_releases++;
        if ((z = z_of_handle(a0))) z->deleting = 1, z->unit.handle = 0;
        return;
    case ACTOR_TYPEINFO:
    case FAKE_ISA:
        r->x[0] = 1;
        return;
    case LINK_SET: {
        Link *l = (Link *)(uintptr_t)a0;
        FakeLinkData *ld = ld_of(r->x[1]);
        l->data = P(ld), l->id = ld ? ld->id : 0xffffffffu;
        return;
    }
    case LINK_VALID: {
        const Link *l = (const Link *)(uintptr_t)a0;
        const FakeLinkData *ld = (const FakeLinkData *)(uintptr_t)l->data;
        r->x[0] = ld && ld->id == l->id && ld->proc;
        return;
    }
    case LINK_RESET:
        ((Link *)(uintptr_t)a0)->data = 0, ((Link *)(uintptr_t)a0)->id = 0xffffffffu;
        return;
    case DELETE_LATER:
        g_deletes++;
        if ((z = z_of(a0))) z->deleting = 1;
        return;
    case FOLLOW_CTOR:
        memset((void *)(uintptr_t)a0, 0, FOLLOW_SIZE);
        W64((void *)(uintptr_t)a0, 0, 0x7001d1cab8ull);
        W32((void *)(uintptr_t)a0, FOLLOW_PHYS, 1);
        return;
    case FOLLOW_SET:
        g_follow_sets++;
        if (!r->x[1]) g_follow_zero++;
        if ((z = z_of(a0)) && !g_z_refuse_follow && !(R32(z->mem, ACTOR_OWN_FOLLOW) & 0x20))
            W64(z->mem, ACTOR_FOLLOW, r->x[1]);
        return;
    case AS_PLAY: {
        uint64_t actor = R64((void *)(uintptr_t)a0, 8);
        g_as_plays++;
        if ((z = z_of(actor)) && g_z_ride && !strcmp((const char *)(uintptr_t)r->x[1], "Ride"))
            snprintf(z->current, sizeof z->current, "Ride");
        return;
    }
    case AS_CURRENT:
        g_cur_text[0] = 0;
        for (int i = 0; i < g_nz; i++)
            if (a0 == P(g_z[i].as)) snprintf(g_cur_text, sizeof g_cur_text, "%s", g_z[i].current);
        g_cur_obj.vptr = STRING_OBJ_VPTR, g_cur_obj.str = P(g_cur_text);
        r->x[0] = P(&g_cur_obj);
        return;
    case VIS_COPY:
        g_vis++;
        return;
    case SET_TRANSFORM:
        g_transforms++;
        if ((z = z_of(a0))) {
            const float *p = (const float *)(uintptr_t)r->x[1];
            float *m = (float *)(void *)(z->mem + ACTOR_MTX);
            m[3] = p[0], m[7] = p[1], m[11] = p[2];
        }
        return;
    case STR_SETTER:
        g_setter_calls++;
        if (a0 == P(g_w.as) && (uint32_t)r->x[1] == PARAM_WEAPON)
            snprintf(g_w.param_buf, sizeof g_w.param_buf, "%s",
                     (const char *)(uintptr_t)R64((void *)(uintptr_t)r->x[2], 8));
        return;
    }
}

// The game's frame between two ticks: requests get ready, deletes happen, a Zelda on a follow of ours is put at
// Link's Spine_2 x the follow's offset (the made-up Spine_2 has no rotation part of its own here: identity columns).
static void world_step(void) {
    int sp = bone_of(&g_ls, "Spine_2");
    for (int i = 0; i < g_nz; i++) {
        FakeZelda *z = &g_z[i];
        if (!z->used || z->dead) continue;
        if (z->deleting) {
            z->dead = 1;
            ld_kill(&z->ld);
            continue;
        }
        if (!z->taken) {
            if (z->ready_in > 0) z->ready_in--;
            continue;
        }
        uint64_t f = R64(z->mem, ACTOR_FOLLOW);
        if (f) {
            const float *off = (const float *)(uintptr_t)(f + FOLLOW_OFFSET), *w = g_ls.world[sp];
            float *m = (float *)(void *)(z->mem + ACTOR_MTX);
            for (int k = 0; k < 3; k++) m[4 * k + 3] = w[12 + k] + w[k] * off[3] + w[4 + k] * off[7] + w[8 + k] * off[11];
        }
    }
}

static int world_setup(void) {
    static int done = -1;
    if (done >= 0) return done;
    uint8_t *page = VirtualAlloc((void *)(uintptr_t)0x7001d00000ull, 0x200000, 0x3000, 0x04);
    if (!page) return done = 0;
    static _Alignas(16) uint8_t evm[0x1d2c0];
    g_event_mgr = evm;
    static char small_t[] = "WeaponSmallSword", large_t[] = "WeaponLargeSword";
    W64((void *)(uintptr_t)STR_SMALL_SWORD, 0, STRING_OBJ_VPTR);
    W64((void *)(uintptr_t)STR_SMALL_SWORD, 8, small_t);
    W64((void *)(uintptr_t)STR_LARGE_SWORD, 0, STRING_OBJ_VPTR);
    W64((void *)(uintptr_t)STR_LARGE_SWORD, 8, large_t);
    W64((void *)(uintptr_t)PLAYER_INFO_PTR, 0, g_w.pi);
    W64((void *)(uintptr_t)CREATOR_PTR, 0, g_w.creator);
    W64((void *)(uintptr_t)HEAP_HOLDER_PTR, 0, g_w.scene);
    W64((void *)(uintptr_t)EVENT_MGR_PTR, 0, g_event_mgr);
    return done = 1;
}

// A fresh made-up Link: model (the 20-bone skeleton), armor piece (5 bones), a two-handed weapon equipped, drawn,
// the weapon parameter WeaponLargeSword, standing (Wait).
static void world_fresh(void) {
    memset(&g_w, 0, sizeof g_w);
    uint8_t *L = g_w.link;
    ld_new(&g_w.link_ld, P(L));
    W64(L, 0, PLAYER_VPTR);
    float m[12] = {1, 0, 0, 10, 0, 1, 0, 20, 0, 0, 1, 30};
    memcpy(L + ACTOR_MTX, m, sizeof m);
    fake_skel(&g_ls, LINK_BONES, FB, L);
    W64(g_w.pi, PI_PLAYER, L);
    float pos[3] = {10, 20, 30};
    memcpy(g_w.pi + PI_POS, pos, sizeof pos);
    // animation holder: body (slot 0 sub 0) and the weapon parameter (0x2c -> entry 0)
    W64(L, ACTOR_AS, g_w.as);
    W64(g_w.as, AS_OWNER, L);
    W32(g_w.as, AS_SLOTS, 1);
    W64(g_w.as, AS_SLOT_ARRAY, g_w.slots);
    W32(g_w.slots, AS_SLOT_NSUB, 1);
    W64(g_w.slots, AS_SLOT_SUBS, g_w.sub);
    W64(g_w.sub, 0, g_w.def);
    W64(g_w.sub, AS_SUB_LIVE, 1);
    W64(g_w.def, AS_DEF_NAME, g_w.body);
    snprintf(g_w.body, sizeof g_w.body, "Wait");
    W32(g_w.as, HOLDER_PARAMS, 1);
    W64(g_w.as, HOLDER_PARAM_ARRAY, g_w.params);
    memset(g_w.as + HOLDER_PARAM_MAP, 0xff, 0x43);
    g_w.as[HOLDER_PARAM_MAP + PARAM_WEAPON] = 0;
    W64(g_w.params, 0, g_w.param0);
    W64(g_w.param0, 8, g_w.param_buf);
    snprintf(g_w.param_buf, sizeof g_w.param_buf, "WeaponLargeSword");
    W64(L, PLAYER_HAND, g_w.hand);
    snprintf(g_w.hand, sizeof g_w.hand, "Sword");
    W32(L, PLAYER_MOVESET, 1);
    W32(L, PLAYER_DRAWN, 1);
    // weapon: equipment slot 0
    W64(L, ACTOR_EQUIP, EQUIP_VPTR);
    W64(L, ACTOR_EQUIP + EQUIP_OWNER, L);
    ld_new(&g_w.weapon_ld, P(g_w.weapon));
    W64(L, ACTOR_EQUIP + EQUIP_LINKS, &g_w.weapon_ld);
    W32(L, ACTOR_EQUIP + EQUIP_LINKS + 8, g_w.weapon_ld.id);
    W64(g_w.weapon, 0, WEAPON_VPTR);
    W32(g_w.weapon, WEAPON_TYPE, 1);
    // armor: one piece with its own model (5 of the bones)
    W64(L, PLAYER_ARMOR, g_w.armor_mgr);
    W64(g_w.armor_mgr, 0, ARMOR_VPTR);
    W64(g_w.armor_mgr, ARMOR_OWNER, L);
    for (int i = 0; i < ARMOR_SLOTS; i++) W32(g_w.armor_mgr, ARMOR_LINKS + i * ARMOR_LINK_SIZE + 8, 0xffffffffu);
    ld_new(&g_w.armor_ld, P(g_w.armor));
    W64(g_w.armor_mgr, ARMOR_LINKS + 2 * ARMOR_LINK_SIZE, &g_w.armor_ld);
    W32(g_w.armor_mgr, ARMOR_LINKS + 2 * ARMOR_LINK_SIZE + 8, g_w.armor_ld.id);
    static const char *const ARMOR_BONES[5] = {"Root", "Spine_2", "Arm_1_L", "Arm_1_R", "Neck"};
    fake_skel(&g_w.armor_skel, ARMOR_BONES, 5, g_w.armor);
    W64(g_w.scene, HEAP_OFF, g_w.heap);
    memset(g_event_mgr, 0, 0x1d2c0);
    // the mod's tick state
    for (int i = 0; i < g_nz; i++) g_z[i].used = 0;
    g_nz = 0;
    g_z_ride = 1, g_z_refuse_follow = 0;
    g_requests = g_deletes = g_releases = g_vis = g_transforms = g_setter_calls = g_follow_sets = g_follow_zero = 0;
    g_as_plays = 0;
    memset(&t_st, 0, sizeof t_st);
    t_link = 0, t_on = 0, t_set_t = -100;
    tgt_publish(NULL, 0);
    t_ncur = t_nrestore = 0, t_buff_next = 0;
    t_happlied = 0, t_cut_on = 0;
    zero_link(&g_zlink), zero_link(&g_ztrash);
    memset(&g_zhandle, 0, sizeof g_zhandle);
    t_ztrash_t = 0, t_zretry = 0, t_zfollow = 0, t_zpack_known = 0;
    t_zbone_u = t_zbone_b = -1, t_zbone_link = 0;
    g_heavy_holder = 0;
    g_settings_dirty = 1;
    __atomic_store_n(&g_want, 0, __ATOMIC_RELAXED);
}

static double g_t = 1000.0;
static void run(double secs) {  // 30 ticks a second, the game's frame after each
    for (double end = g_t + secs; g_t < end; g_t += 1.0 / 30.0) {
        tick_body(cpu(), g_t);
        world_step();
    }
    g_st = t_st;
}

static int zelda_count(void) {
    int n;
    live_zelda(&n);
    return n;
}

static void check_tick(void) {
    if (!world_setup()) {
        check("tick", "world", 1, "SKIP: the made-up globals' pages are taken in this process");
        return;
    }
    g_pack_fn = fake_pack;
    g_pack_there = 1;
    g_set = (__typeof__(g_set)){1, 1, 1, 1, "extreme", 0, 0};

    // off: nothing published, nothing called
    world_fresh();
    run(0.5);
    check("tick", "off_does_nothing", g_ntgt == 0 && g_requests == 0 && g_setter_calls == 0 && t_st.strings == 1 &&
          R32(g_w.link, PLAYER_MOVESET) == 1, "targets %d, requests %d, strings %d", g_ntgt, g_requests,
          t_st.strings);

    // ON: buff targets (Link + armor), shoulders, heavy applied (class 0, the parameter set to one-handed)
    cmd("on", NULL);
    run(1.0);
    Target lt;
    int li = tgt_find(P(g_ls.sk), &lt), ai = tgt_find(P(g_w.armor_skel.sk), &lt);
    check("buff", "targets", t_st.link_targets == 1 && t_st.armor_targets == 1 && li >= 0 && ai >= 0 && g_tgt_live,
          "link %d, armor %d", t_st.link_targets, t_st.armor_targets);
    tgt_find(P(g_ls.sk), &lt);
    int arm = bone_of(&g_ls, "Arm_1_L");
    check("buff", "bones_by_name", lt.bone[6] == arm && lt.scale[6][1] == SIDE[1][6][0] && lt.a_mode == 1 &&
          lt.na == 4 && lt.scale[2][0] == 1.0f, "Arm_1_L %d (want %d), a_mode %d, na %d", lt.bone[6], arm, lt.a_mode,
          lt.na);
    check("heavy", "applied", R32(g_w.link, PLAYER_MOVESET) == 0 && R32(g_w.link, PLAYER_DRAWN) == 0 &&
          !strcmp(g_w.param_buf, "WeaponSmallSword") && g_heavy_holder == P(g_w.as) && t_st.applied,
          "moveset %d, drawn %d, param %s", (int)R32(g_w.link, PLAYER_MOVESET), (int)R32(g_w.link, PLAYER_DRAWN),
          g_w.param_buf);
    // the game sets it back (a re-equip): kept at one-handed
    W32(g_w.link, PLAYER_MOVESET, 1);
    run(0.2);
    check("heavy", "kept", R32(g_w.link, PLAYER_MOVESET) == 0, "moveset %d", (int)R32(g_w.link, PLAYER_MOVESET));
    // Zelda: requested, live, on our follow, Ride, at her seat
    run(1.0);
    FakeZelda *z = NULL;
    int nz = 0;
    z = live_zelda(&nz);
    float dist = t_st.zdist;
    check("zelda", "live", nz == 1 && t_st.zstate == ZS_LIVE && z && R64(z->mem, ACTOR_FOLLOW) == t_zfollow &&
          t_zfollow && !t_st.zmanual && t_st.zride && !strcmp(z->current, "Ride") && g_vis > 0,
          "live %d, state %s, follow %d, ride %d, vis %d", nz, ZS_NAMES[t_st.zstate],
          z && R64(z->mem, ACTOR_FOLLOW) == t_zfollow, t_st.zride, g_vis);
    check("zelda", "seat", dist >= 0 && dist < 0.01f && R32((void *)(uintptr_t)t_zfollow, FOLLOW_INDEX) == 0xffffffffu &&
          ((uint8_t *)(uintptr_t)t_zfollow)[FOLLOW_PHYS] == 0, "dist %.4f", dist);
    // a reload took her (her link invalid): forgotten, spawned again
    int req = g_requests;
    ld_kill(&z->ld), z->dead = 1;
    run(2.5);
    check("zelda", "back_after_reload", zelda_count() == 1 && t_st.zstate == ZS_LIVE && g_requests == req + 1 &&
          t_st.zspawns == 2 && g_follow_zero == 0, "live %d, state %s, requests +%d, spawns %lld", zelda_count(),
          ZS_NAMES[t_st.zstate], g_requests - req, (long long)t_st.zspawns);
    // the seat follows the settings
    g_set.back = 5, g_settings_dirty = 1;
    run(0.5);
    const float *off = (const float *)(uintptr_t)(t_zfollow + FOLLOW_OFFSET);
    check("zelda", "seat_setting", off[7] < -0.266f - 0.05f - 0.039f && off[7] > -0.266f - 0.05f - 0.041f,
          "back row %.3f (want %.3f)", off[7], -0.266f - 0.05f - 0.04f);
    g_set.back = 0, g_settings_dirty = 1;

    // attacking: OFF waits until the swing is over, then everything goes back
    snprintf(g_w.body, sizeof g_w.body, "CutS1");
    run(0.3);
    cmd("off", NULL);
    run(0.3);
    int waited = R32(g_w.link, PLAYER_MOVESET) == 0 && t_st.pending;
    snprintf(g_w.body, sizeof g_w.body, "Wait");
    run(0.3);
    check("heavy", "waits_for_swing", waited && R32(g_w.link, PLAYER_MOVESET) == 1 && R32(g_w.link, PLAYER_DRAWN) == 1 &&
          !strcmp(g_w.param_buf, "WeaponLargeSword") && !g_heavy_holder && t_st.nswings >= 1, "waited %d, moveset %d, "
          "param %s, swings %d", waited, (int)R32(g_w.link, PLAYER_MOVESET), g_w.param_buf, t_st.nswings);
    run(1.5);
    check("zelda", "off_deletes", zelda_count() == 0 && t_st.zstate == ZS_IDLE && t_st.zstray == 0 && g_deletes >= 1,
          "live %d, deletes %d, stray %d", zelda_count(), g_deletes, t_st.zstray);
    check("buff", "off_clears", g_ntgt == 0 && !g_tgt_live, "targets %d", g_ntgt);

    // never touches a class it doesn't own: -1 (none) and 2 (spear) stay, even with the egg on
    W32(g_w.link, PLAYER_MOVESET, 2);
    cmd("on", NULL);
    run(0.3);
    int spear = R32(g_w.link, PLAYER_MOVESET) == 2;
    W32(g_w.link, PLAYER_MOVESET, 0xffffffffu);
    run(0.3);
    check("heavy", "leaves_other_classes", spear && R32(g_w.link, PLAYER_MOVESET) == 0xffffffffu, "spear %d, none %d",
          spear, (int)R32(g_w.link, PLAYER_MOVESET));
    // a one-handed weapon: nothing changes
    W32(g_w.weapon, WEAPON_TYPE, 0);
    W32(g_w.link, PLAYER_MOVESET, 0);
    snprintf(g_w.param_buf, sizeof g_w.param_buf, "WeaponSmallSword");
    int calls = g_setter_calls;
    run(0.3);
    check("heavy", "one_handed_untouched", g_setter_calls == calls && !g_heavy_holder, "setter calls +%d",
          g_setter_calls - calls);

    // Link changes (a reload): targets dropped and Zelda forgotten without a call; then found again on the new one
    W32(g_w.weapon, WEAPON_TYPE, 1);
    run(1.5);
    int del = g_deletes, before = zelda_count();
    FakeZelda *old = live_zelda(&nz);
    static _Alignas(16) uint8_t link2[0x2800];  // (a new Link with no model yet: nothing to find on him)
    memcpy(link2, g_w.link, sizeof link2);
    W64(link2, ACTOR_MODEL, 0);
    W64(g_w.pi, PI_PLAYER, link2);
    tick_body(cpu(), g_t);
    int cleared = g_ntgt == 0 && t_st.zstate == ZS_IDLE && g_deletes == del && !t_happlied && !g_heavy_holder;
    if (old) ld_kill(&old->ld), old->dead = 1;  // (the reload took her along)
    W64(g_w.pi, PI_PLAYER, g_w.link);
    run(2.0);
    check("tick", "link_changed", before == 1 && cleared && zelda_count() == 1 && g_ntgt > 0,
          "zelda before %d, cleared %d (deletes +%d), after %d", before, cleared, g_deletes - del, zelda_count());

    // secret_word OFF while on: all of it goes away
    run(1.5);
    g_set.secret = 0, g_settings_dirty = 1;
    run(1.5);
    int gone = zelda_count() == 0 && g_ntgt == 0 && R32(g_w.link, PLAYER_MOVESET) == 1 && !t_st.on;
    g_set.secret = 1, g_settings_dirty = 1;
    cmd("off", NULL);
    run(0.5);
    check("tick", "secret_word_off", gone, "zelda %d, targets %d, moveset %d", zelda_count(), g_ntgt,
          (int)R32(g_w.link, PLAYER_MOVESET));

    // the follow refused: the mod moves her itself (plan B), she is still at her seat
    world_fresh();
    g_z_refuse_follow = 1;
    cmd("on", NULL);
    run(2.0);
    check("zelda", "plan_b", t_st.zmanual && g_transforms > 0 && t_st.zdist >= 0 && t_st.zdist < 0.01f &&
          zelda_count() == 1 && g_follow_zero == 0, "manual %d, transforms %d, dist %.4f", t_st.zmanual, g_transforms,
          t_st.zdist);
    cmd("off", NULL);
    run(1.5);

    // not our pack (no Ride): deleted after a second, given up (no more requests until the next ON)
    world_fresh();
    g_z_ride = 0;
    cmd("on", NULL);
    run(3.0);
    req = g_requests;
    run(3.0);
    check("zelda", "no_ride_gives_up", t_st.zgiveup == ZG_NOT_OURS && zelda_count() == 0 && g_requests == req &&
          req == 1, "gave up %d, live %d, requests %d", t_st.zgiveup, zelda_count(), g_requests);
    cmd("off", NULL);
    run(0.5);

    // no pack deployed: nothing is requested at all
    world_fresh();
    g_pack_there = 0;
    cmd("on", NULL);
    run(2.0);
    check("zelda", "no_pack_no_request", g_requests == 0 && t_st.zgiveup == ZG_NO_PACK && g_ntgt > 0,
          "requests %d, gave up %d, buff targets %d", g_requests, t_st.zgiveup, g_ntgt);
    g_pack_there = 1;
    cmd("off", NULL);
    run(0.5);

    // the shoulders: on writes the wider translation before the world calc; off writes the rest back for a second
    world_fresh();
    cmd("on", NULL);
    run(0.5);
    int b = bone_of(&g_ls, "Arm_1_L");
    float rest = 0.15f + 0.01f * (float)b;
    SWMOD_REGS(cpu())->x[0] = P(g_ls.sk);
    world_before(cpu());
    float on_x;
    memcpy(&on_x, g_ls.local + b * 0x60 + 0x50, 4);
    g_set.shoulders = 0, g_settings_dirty = 1;
    run(0.3);
    SWMOD_REGS(cpu())->x[0] = P(g_ls.sk);  // (the tick's game calls used the same registers)
    world_before(cpu());
    float off_x;
    memcpy(&off_x, g_ls.local + b * 0x60 + 0x50, 4);
    run(1.5);
    tgt_find(P(g_ls.sk), &lt);
    check("shoulders", "on_then_rest", approx(on_x, rest * STAGE_A_OUT) && approx(off_x, rest) && lt.a_mode == 0 &&
          lt.scale[2][0] == CLAVICLE_ALONG, "on %.4f, off %.4f (rest %.4f), a_mode later %d", on_x, off_x, rest,
          lt.a_mode);
    g_set.shoulders = 1, g_settings_dirty = 1;
    cmd("off", NULL);
    run(0.5);

    // 20 quick toggles: at most one Zelda at any time, none left at the end
    world_fresh();
    int most = 0;
    for (int k = 0; k < 20; k++) {
        cmd(NULL, NULL);
        for (int f = 0; f < 45; f++) {
            tick_body(cpu(), g_t);
            world_step();
            g_t += 1.0 / 30.0;
            int n = zelda_count();
            if (n > most) most = n;
        }
    }
    if (g_want) cmd("off", NULL);
    run(2.0);
    check("tick", "toggles", most == 1 && zelda_count() == 0 && t_st.zstray == 0 && g_ntgt == 0 && g_follow_zero == 0 &&
          R32(g_w.link, PLAYER_MOVESET) == 1, "most %d, left %d, stray %d, targets %d", most, zelda_count(),
          t_st.zstray, g_ntgt);
}

SWMOD_EXPORT int zv_selftest(char *out, int cap) {
    g_out = out, g_cap = cap, g_len = 0, g_fails = 0;
    if (out && cap > 0) out[0] = 0;
    setup();
    check_command();
    check_seat();
    check_hooks();
    check_tick();
    return g_fails;
}
