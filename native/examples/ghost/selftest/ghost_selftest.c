// Self-test of ghost.c's animation-name logic without the game (tests/test_ghost_native.py builds and runs it).
//
// It includes ghost.c and drives anim_tick with a made-up actor: a fake guest_call answers the two animation calls
// (play by name, and what plays now) from a list of names the made-up actor has. No game code, no game data: the
// names below are the animation names the game's actors use (data), the rest is made up.
//
// ghost_selftest(out, cap) writes one line per check ("<group> <check> PASS|FAIL <detail>") and returns the number
// of failed checks.
//
// 0.6.0 adds the held items (items_tick, give_tick) against a made-up world: a ghost whose model has the hand bones, a
// fake game call for every function they use (request, handle, link, grab, bone follow), and a fake frame
// (world_step) that does what the research says the item's own AI does. Its pointers are in this process's memory,
// so the memory check allows any address here (GUEST_LO / GUEST_HI), and the three globals the request reads (the
// spawner, the scene object) get a page of their own at their real addresses (VirtualAlloc; SKIP if that's taken).
#define GUEST_LO 0x10000ull
#define GUEST_HI 0x7ffffffe0000ull
#include "../ghost.c"

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
static void check(const char *group, const char *name, int ok, const char *fmt, ...) {
    char detail[256];
    __builtin_va_list ap;
    __builtin_va_start(ap, fmt);
    vsnprintf(detail, sizeof detail, fmt, ap);
    __builtin_va_end(ap);
    if (!ok) g_fails++;
    say("%s %s %s %s\n", group, name, ok ? "PASS" : "FAIL", detail);
}

// ------------------------------------------------------------------ a made-up actor and a fake guest_call
typedef struct {
    uint8_t mem[0x600];     // the actor: +0x560 = its animation holder
    uint8_t holder[0x100];  // +0xd8 owner, +0xb8 slot count
    const char *const *names;
    int nnames;
    double lag;             // seconds before what plays shows the new name (0: at once)
    char current[32];       // what the "what plays now" call reports
    char pending[32];       // asked for, reported from pending_at on
    double pending_at;
    int calls, calls_missing;  // play calls; of those, for names it doesn't have
} FakeActor;

static FakeActor *g_fa;
static double g_now;  // the fake game's time (anim_tick's t)
static struct {
    uint64_t vptr, str;
} g_cur;
static char g_cur_text[32];

static int fa_has(const FakeActor *fa, const char *name) {
    for (int i = 0; i < fa->nnames; i++)
        if (!strcmp(fa->names[i], name)) return 1;
    return 0;
}

static int items_call(struct Cpu *c, uint64_t fn);  // (the held items' fake game calls, below)

static void fake_guest_call(struct Cpu *c, uint64_t fn) {
    SwModRegs *r = SWMOD_REGS(c);
    if (items_call(c, fn)) return;
    if (fn == AS_PLAY) {
        uint64_t actor = rd64(r->x[0] + 8);
        const char *name = (const char *)(uintptr_t)r->x[1];
        if (!g_fa || actor != P(g_fa->mem)) return;
        g_fa->calls++;
        if (!fa_has(g_fa, name)) {
            g_fa->calls_missing++;
            return;
        }
        int keep = (r->x[2] & 1) && (!strcmp(g_fa->current, name) || !strcmp(g_fa->pending, name));
        if (keep) return;
        if (g_fa->lag <= 0) {
            copy_name(g_fa->current, name);
            g_fa->pending[0] = 0;
        } else {
            copy_name(g_fa->pending, name);
            g_fa->pending_at = g_now + g_fa->lag;
        }
    } else if (fn == AS_CURRENT) {
        if (g_fa && g_fa->pending[0] && g_now >= g_fa->pending_at) {
            copy_name(g_fa->current, g_fa->pending);
            g_fa->pending[0] = 0;
        }
        copy_name(g_cur_text, g_fa ? g_fa->current : "");
        g_cur.vptr = 0, g_cur.str = P(g_cur_text);
        r->x[0] = P(&g_cur);
    }
}

static void fake_log(const char *fmt, ...) { (void)fmt; }

static SwModApi g_fake_api;
static _Alignas(16) uint8_t g_cpu[4096];

static void setup(void) {
    memset(&g_fake_api, 0, sizeof g_fake_api);
    g_fake_api.log = fake_log;
    g_fake_api.guest_call = fake_guest_call;
    g_fake_api.game_version = "1.9.0";
    api = &g_fake_api;
    QueryPerformanceFrequency(&g_qpf);
}

static void fake_actor(FakeActor *fa, const char *const *names, int n, double lag) {
    memset(fa, 0, sizeof *fa);
    *(uint64_t *)(void *)(fa->mem + ACTOR_AS) = P(fa->holder);
    *(uint64_t *)(void *)(fa->holder + AS_OWNER) = P(fa->mem);
    *(int32_t *)(void *)(fa->holder + AS_SLOTS) = 3;
    fa->names = names, fa->nnames = n, fa->lag = lag;
    g_fa = fa;
}

// A new ghost: what the tick keeps about the actor starts over; the name table too (each test starts empty).
static void fresh(void) {
    anim_forget();
    memset(&t_status, 0, sizeof t_status);
    t_status.anim = ANIM_AUTO, t_status.move = MOVE_POS;
    t_given_speed = -1, t_given_t = 0, t_given_anim[0] = 0;
    g_nanim_names = 0;
}

// The other player plays `name` for secs (speed v): `ghost pos` 20 times a second, frames 55 a second. Returns what
// the actor shows at the end.
static double g_t = 1000.0;
static const char *feed(const char *name, float v, double secs) {
    struct Cpu *c = (struct Cpu *)(void *)g_cpu;
    double end = g_t + secs, next_pos = g_t;
    static float x;
    while (g_t < end) {
        if (g_t >= next_pos) {
            next_pos += 0.05;
            t_given_speed = v, t_given_t = g_t;
            copy_name(t_given_anim, name);
        }
        x += v / 55.0f;
        g_now = g_t;
        anim_tick(c, P(g_fa->mem), x, 0, g_t);
        g_t += 1.0 / 55.0;
    }
    return g_fa->current;
}

static int missing_count(const Missing *m) {
    int n = 0;
    for (int i = 0; i < m->n; i++) n += m->misses[i] >= ANIM_STRIKES;
    return n;
}

static const char *const SHEIKAH[] = {"Wait", "Walk", "Run", "RunGear2", "Turn", "Move_End"};
static const char *const LINK_BODY[] = {"Wait", "Walk", "Run", "RunGear2", "CutS1", "CutS2", "CutS3", "CutSF",
                                        "CutDash", "CutJumpSt", "CutCharge", "BowCharge", "BowShoot", "GuardJust",
                                        "Jump", "Fall", "Land", "ParashawlGlide", "ClimbWait", "ClimbMove",
                                        "SwimWait", "SwimMove", "SwimDash", "SquatWait", "SquatMove", "DamageS",
                                        "Dead", "WeaponEquipOn"};
#define N_OF(a) ((int)(sizeof(a) / sizeof *(a)))

// ------------------------------------------------------------------ the checks

// The name table holds every name Link has: names the body lacks don't crowd out the ones it has, and `ghost anim`
// always gets a slot.
static void check_names(void) {
    FakeActor fa;
    char nm[32];
    fake_actor(&fa, LINK_BODY, N_OF(LINK_BODY), 0);
    fresh();
    feed("Wait", 0, 0.5);
    for (int i = 0; i < 130; i++) {  // (more than the old table's 128)
        snprintf(nm, sizeof nm, "Other%03d", i);
        feed(nm, 0, 0.3);
    }
    const char *got = feed("ParashawlGlide", 0, 1.0);
    check("names", "glide_after_130_others", !strcmp(got, "ParashawlGlide"), "plays %s, %d names kept", got,
          g_nanim_names);
    got = feed("CutS3", 0, 1.0);
    check("names", "attack_after_130_others", !strcmp(got, "CutS3"), "plays %s", got);
    check("names", "room_for_link", MAX_ANIM_NAMES - ANIM_NAMES_OWN >= 399 && MAX_MISSING >= 399,
          "%d names from the network, %d noted missing per actor", MAX_ANIM_NAMES - ANIM_NAMES_OWN, MAX_MISSING);

    // A peer that sends ever more names: once the network's part is full, new ones go by speed; `ghost anim` still
    // gets a slot, and the table never overflows.
    fresh();
    for (int i = 0; i < 600; i++) {
        snprintf(nm, sizeof nm, "Flood%03d", i);
        feed(nm, 0, 0.3);
    }
    int flood = g_nanim_names;
    const char *own = intern_anim("Turn", 1);
    check("names", "flood_stops_short", flood == MAX_ANIM_NAMES - ANIM_NAMES_OWN, "%d names kept after 600",
          flood);
    check("names", "ghost_anim_after_flood", own != NULL && !strcmp(own, "Turn"), "intern_anim(Turn) %s",
          own ? own : "NULL");
    got = feed("Flood599", 0, 0.5);
    check("names", "flood_by_speed", !strcmp(got, "Wait"), "an unkept name shows %s", got);
}

// Each name the actor lacks is asked for at most ANIM_STRIKES times, however often it comes back.
static void check_missing(void) {
    FakeActor fa;
    char nm[32];
    fake_actor(&fa, SHEIKAH, N_OF(SHEIKAH), 0);
    fresh();
    feed("Wait", 0, 0.5);
    for (int round = 0; round < 3; round++) {
        for (int i = 0; i < 80; i++) {
            snprintf(nm, sizeof nm, "Name%02d", i);
            feed(nm, 0, 0.3);
            feed("Wait", 0, 0.3);
        }
        if (!round)
            check("missing", "first_round", fa.calls_missing == 80 * ANIM_STRIKES, "%d calls for 80 missing names",
                  fa.calls_missing);
    }
    check("missing", "no_thrash", fa.calls_missing == 80 * ANIM_STRIKES, "%d calls for 80 names over 3 rounds",
          fa.calls_missing);
    check("missing", "noted", missing_count(&t_missing) == 80 && t_status.remote_misses == 80,
          "%d noted, remote_misses %lld", missing_count(&t_missing), (long long)t_status.remote_misses);
    check("missing", "shows_wait", !strcmp(fa.current, "Wait"), "plays %s", fa.current);

    // the old behaviour stays: an attack the Link body lacks shows CutS1; the Sheikah man shows the speed rule
    fake_actor(&fa, LINK_BODY, N_OF(LINK_BODY), 0);
    fresh();
    feed("Wait", 0, 0.5);
    const char *got = feed("CutS4", 0, 0.8);
    check("missing", "stand_in_cut", !strcmp(got, "CutS1") && fa.calls_missing == ANIM_STRIKES,
          "plays %s after %d calls for CutS4", got, fa.calls_missing);
    fake_actor(&fa, SHEIKAH, N_OF(SHEIKAH), 0);
    fresh();
    feed("Wait", 0, 0.5);
    got = feed("CutS1", 6.0f, 0.8);
    check("missing", "sheikah_by_speed", !strcmp(got, "Run"), "plays %s", got);
    got = feed("Move", 2.0f, 0.8);
    check("missing", "move_by_speed", !strcmp(got, "Walk"), "plays %s", got);
}

// A game that shows a new name a little late (0.15 s, more than ANIM_VERIFY_S): the name is not lost.
static void check_late(void) {
    FakeActor fa;
    fake_actor(&fa, LINK_BODY, N_OF(LINK_BODY), 0.15);
    fresh();
    feed("Wait", 0, 0.5);
    const char *got = feed("CutS3", 0, 1.0);
    check("late", "plays", !strcmp(got, "CutS3"), "plays %s", got);
    check("late", "not_noted", !missing_has(&t_missing, "CutS3") && t_status.remote_misses == 0,
          "remote_misses %lld, missing '%s'", (long long)t_status.remote_misses, t_status.missing);
    feed("Wait", 0, 0.5);
    got = feed("CutS3", 0, 1.0);
    check("late", "plays_again", !strcmp(got, "CutS3"), "plays %s", got);
    feed("Wait", 0, 0.5);
    int before = fa.calls_missing;
    got = feed("CutS9", 0, 1.0);  // (an attack the body doesn't have)
    check("late", "missing_after_two", fa.calls_missing - before == ANIM_STRIKES && !strcmp(got, "CutS1"),
          "%d calls for CutS9, then plays %s", fa.calls_missing - before, got);
}

// ------------------------------------------------------------------ held items: a made-up world (0.6.0)

__declspec(dllimport) void *__stdcall VirtualAlloc(void *addr, size_t size, unsigned long type, unsigned long protect);

typedef struct {               // an actor's link data (the link's data pointer points here)
    uint8_t pad[LINKDATA_ID];
    uint32_t id, pad2;
    uint64_t proc;             // 0: gone
} FakeLinkData;

typedef struct {
    _Alignas(16) uint8_t mem[0x1000];  // the actor: vptr at 0, the fields ghost.c reads
    _Alignas(16) uint8_t params[0x100], plist[0x300], obj[0x300];
    uint64_t table[20];
    FakeLinkData ld;
    struct {
        uint64_t pad, handle;          // the request's unit: +8 points back at the handle
    } unit;
    int used, ready_in, taken, deleting, dead, grab_pending, grabs_ok, grabs_refused, slot;
    uint64_t holder;                   // the holder a grab asked for
    char name[64], hand[16], pod[16];
} FakeItem;

#define N_FAKE 48
static FakeItem g_fi[N_FAKE];
static int g_nfi;
static uint32_t g_next_id = 100;
static uint64_t g_fake_vtable[4];     // (`ghost give`'s actor: its isA at +0x10)
#define FAKE_ISA 0x7000005000ull       // (made up: a function address only the fake call knows)
static struct {
    _Alignas(16) uint8_t mem[0x1000];
    FakeLinkData ld;
    _Alignas(16) uint8_t model[0x100], unit[0x200], skel[0x40], res[0x80], dict[0x20 + 0x10 * 8];
    uint64_t units[1], slot0[1];
    char keys[8][32];
    float world[8][16];
} g_gh;                                // the ghost: Root, Weapon_R, Weapon_L, Pod_A, Spine_2
static int g_calls, g_deletes, g_releases, g_requests, g_follow_zero, g_follow_sets, g_ctors, g_lets_go;
static const char *g_refuse_name = "Weapon_Sword_999";  // the request is refused for this name
static float g_set_y;                  // SET_MTX: the y of the last matrix set
static int g_not_weapon;               // the next request makes an actor of another class
static float g_fake_axis;              // 0.6.2: new items' matrix axes have this length (0: left at 0) and scale 1

static void ld_new(FakeLinkData *ld, uint64_t proc) { ld->id = g_next_id++, ld->proc = proc; }
static void ld_kill(FakeLinkData *ld) { ld->proc = 0, ld->id = g_next_id++; }
static FakeLinkData *ld_of(uint64_t proc) {
    if (proc == P(g_gh.mem)) return &g_gh.ld;
    for (int i = 0; i < g_nfi; i++)
        if (proc == P(g_fi[i].mem)) return &g_fi[i].ld;
    return NULL;
}
static FakeItem *fi_of(uint64_t proc) {
    for (int i = 0; i < g_nfi; i++)
        if (proc == P(g_fi[i].mem)) return &g_fi[i];
    return NULL;
}
static FakeItem *fi_of_handle(uint64_t h) {
    for (int i = 0; i < g_nfi; i++)
        if (g_fi[i].unit.handle == h && !g_fi[i].taken && !g_fi[i].dead) return &g_fi[i];
    return NULL;
}
#define W64(p, off, v) (*(uint64_t *)(void *)((uint8_t *)(p) + (off)) = (uint64_t)(v))
#define W32(p, off, v) (*(uint32_t *)(void *)((uint8_t *)(p) + (off)) = (uint32_t)(v))
#define R64(p, off) (*(uint64_t *)(void *)((uint8_t *)(p) + (off)))

static void fake_ghost(int with_hands) {
    memset(&g_gh, 0, sizeof g_gh);
    ld_new(&g_gh.ld, P(g_gh.mem));
    W64(g_gh.mem, ACTOR_MODEL, g_gh.model);
    W64(g_gh.model, 0, MODEL_VPTR);
    W32(g_gh.model, MODEL_NUNITS, 1);
    W64(g_gh.model, MODEL_UNITS, g_gh.units);
    g_gh.units[0] = P(g_gh.slot0), g_gh.slot0[0] = P(g_gh.unit);
    W64(g_gh.unit, 0, UNIT_VPTR_B);
    W64(g_gh.unit, UNIT_SKEL, g_gh.skel);
    W64(g_gh.skel, 0, g_gh.res);
    W64(g_gh.skel, SKEL_WORLD, g_gh.world);
    W32(g_gh.res, 0, FSKL_MAGIC);
    W64(g_gh.res, RES_DICT, g_gh.dict);
    const char *bones[5] = {"Root", with_hands ? "Weapon_R" : "Hand_R", with_hands ? "Weapon_L" : "Hand_L", "Pod_A",
                            "Spine_2"};
    W32(g_gh.res, RES_NBONES, 5);
    for (int b = 0; b < 5; b++) {
        size_t n = strlen(bones[b]);
        *(uint16_t *)(void *)g_gh.keys[b] = (uint16_t)n;
        memcpy(g_gh.keys[b] + 2, bones[b], n + 1);
        W64(g_gh.dict, 0x20 + 0x10 * b, g_gh.keys[b]);
        g_gh.world[b][0] = g_gh.world[b][5] = g_gh.world[b][10] = g_gh.world[b][15] = 1.0f;
        g_gh.world[b][12] = 10.0f + (float)b * 0.1f, g_gh.world[b][13] = 1.0f + (float)b * 0.2f;
        g_gh.world[b][14] = 20.0f;
    }
    float m[12] = {1, 0, 0, 10, 0, 1, 0, 0, 0, 0, 1, 20};
    memcpy(g_gh.mem + ACTOR_MTX, m, sizeof m);
}

// The game's frame between two ticks: requests get ready, items' AI runs (an unheld item settles with no holder; a
// grabbed one settles in the holder's hand bone follow), deletes happen. An item on a follow is put at its bone.
static void world_step(void) {
    for (int i = 0; i < g_nfi; i++) {
        FakeItem *f = &g_fi[i];
        if (!f->used || f->dead) continue;
        if (f->deleting) {
            f->dead = 1;
            ld_kill(&f->ld);
            continue;
        }
        if (!f->taken) {
            if (f->ready_in > 0) f->ready_in--;
            continue;
        }
        uint8_t *st = f->mem + ITEM_STATE;
        if (f->grab_pending) {
            f->grab_pending = 0;
            FakeLinkData *hl = ld_of(f->holder);
            W64(f->mem, ITEM_HOLDER, hl);
            W32(f->mem, ITEM_HOLDER + 8, hl ? hl->id : 0xffffffffu);
            *st = 0xff;
            W64(f->mem, ACTOR_FOLLOW, f->mem + ITEM_FOLLOW);
        } else if (*st == 0) {
            *st = 0xff;  // (its first AI tick: no holder)
        }
        uint64_t fol = R64(f->mem, ACTOR_FOLLOW);
        int bone = fol == P(f->mem + ITEM_FOLLOW) ? (!strcmp(f->hand, "Weapon_R") ? 1 : 2) : fol ? 3 : -1;
        if (bone >= 0) {  // (the follow puts it at the bone; ours on the back adds its offset's length along x)
            float extra = bone == 3 ? 0.3f : 0.0f;
            float *mtx = (float *)(void *)(f->mem + ACTOR_MTX);
            mtx[3] = g_gh.world[bone][12] + extra, mtx[7] = g_gh.world[bone][13], mtx[11] = g_gh.world[bone][14];
        }
    }
}

static FakeItem *fake_item_new(const char *name, uint64_t handle) {
    if (g_nfi >= N_FAKE) return NULL;
    FakeItem *f = &g_fi[g_nfi++];
    memset(f, 0, sizeof *f);
    f->used = 1, f->ready_in = 2;
    snprintf(f->name, sizeof f->name, "%s", name);
    ld_new(&f->ld, P(f->mem));
    f->unit.handle = handle;
    W64(f->mem, 0, g_not_weapon ? P(g_fake_vtable) : WEAPON_VPTR);
    g_not_weapon = 0;
    W64(f->mem, ITEM_HOLDER, 0);
    W32(f->mem, ITEM_HOLDER + 8, 0xffffffffu);
    W32(f->mem, ITEM_HOLD_ASK + 8, 0xffffffffu);
    int kind = strstr(name, "Shield") ? 3 : strstr(name, "Bow") ? 4 : strstr(name, "Lsword") ? 1
               : strstr(name, "Spear") ? 2 : 0;
    W32(f->mem, ITEM_KIND, kind);
    W64(f->mem, ACTOR_PARAMS, f->params);
    W64(f->params, PARAMS_LIST, f->plist);
    W32(f->plist, PLIST_COUNT, 18);
    W64(f->plist, PLIST_OBJS, f->table);
    for (int k = 0; k < 18; k++) f->table[k] = P(f->obj);
    if (g_fake_axis > 0) {  // (0.6.2: a hand bone with a scale: the matrix's axes carry it; its own scale 1)
        float *mtx = (float *)(void *)(f->mem + ACTOR_MTX), *sc = (float *)(void *)(f->mem + ACTOR_SCALE);
        mtx[0] = mtx[5] = mtx[10] = g_fake_axis;
        sc[0] = sc[1] = sc[2] = 1.0f;
    }
    static const int tr[5] = {0x78, 0x78, 0x78, 0x2c0, 0x50}, rt[5] = {0xa0, 0xa0, 0xa0, 0x2e8, 0x78};
    float trans[3] = {0.3f, 0.0f, 0.0f}, rot[3] = {180.0f, 120.0f, 0.0f};
    memcpy(f->obj + tr[kind], trans, sizeof trans);
    memcpy(f->obj + rt[kind], rot, sizeof rot);
    return f;
}

static int items_call(struct Cpu *c, uint64_t fn) {
    SwModRegs *r = SWMOD_REGS(c);
    uint64_t a0 = r->x[0];
    FakeItem *f;
    g_calls++;
    switch (fn) {
    case HANDLE_CLEAR:
        memset((void *)(uintptr_t)a0, 0, sizeof(Handle));
        return 1;
    case PACK_CLEAR:
    case PACK_ADD:
    case PHYS_SET_MTX:
        return 1;
    case SET_MTX:
        g_set_y = ((const float *)(uintptr_t)r->x[1])[7];
        return 1;
    case REQUEST_CREATE:
        g_requests++;
        if (!strcmp((const char *)(uintptr_t)r->x[1], g_refuse_name) || !(f = fake_item_new((const char *)(uintptr_t)r->x[1], r->x[3]))) {
            r->x[0] = 0;
            return 1;
        }
        ((Handle *)(uintptr_t)r->x[3])->unit = P(&f->unit);
        r->x[0] = 1;
        return 1;
    case HANDLE_READY:
        f = fi_of_handle(a0);
        r->x[0] = f && f->ready_in == 0;
        return 1;
    case HANDLE_FAILED:
        r->x[0] = 0;
        return 1;
    case HANDLE_PEEK:
        f = fi_of_handle(a0);
        r->x[0] = f ? P(f->mem) : 0;
        return 1;
    case HANDLE_TAKE:
        f = fi_of_handle(a0);
        if (f) f->taken = 1, f->unit.handle = 0;
        memset((void *)(uintptr_t)a0, 0, sizeof(Handle));
        r->x[0] = f ? P(f->mem) : 0;
        return 1;
    case HANDLE_RELEASE:
    case HANDLE_RELEASE_FAILED:
        g_releases++;
        if ((f = fi_of_handle(a0))) f->deleting = 1, f->unit.handle = 0;
        return 1;
    case LINK_SET: {
        Link *l = (Link *)(uintptr_t)a0;
        FakeLinkData *ld = ld_of(r->x[1]);
        l->data = P(ld), l->id = ld ? ld->id : 0xffffffffu;
        return 1;
    }
    case LINK_VALID: {
        const Link *l = (const Link *)(uintptr_t)a0;
        const FakeLinkData *ld = (const FakeLinkData *)(uintptr_t)l->data;
        r->x[0] = ld && ld->id == l->id && ld->proc;
        return 1;
    }
    case LINK_RESET:
        ((Link *)(uintptr_t)a0)->data = 0, ((Link *)(uintptr_t)a0)->id = 0xffffffffu;
        return 1;
    case DELETE_LATER:
        g_deletes++;
        if ((f = fi_of(a0))) f->deleting = 1;
        else if (a0 == P(g_gh.mem)) ld_kill(&g_gh.ld);
        return 1;
    case WEAPON_GRAB:  // refused while its state is 0 (its AI didn't run) or it has a live holder
        f = fi_of(a0);
        if (!f || f->mem[ITEM_STATE] == 0 || (R64(f->mem, ITEM_HOLDER) &&
                                               ((FakeLinkData *)(uintptr_t)R64(f->mem, ITEM_HOLDER))->proc)) {
            if (f) f->grabs_refused++;
            r->x[0] = 0;
            return 1;
        }
        f->grabs_ok++, f->grab_pending = 1, f->holder = r->x[2], f->slot = (int)r->x[1];
        f->mem[ITEM_STATE] = 0;
        {  // (the grab links the holder it asked for at once: +0x880)
            FakeLinkData *hl = ld_of(f->holder);
            W64(f->mem, ITEM_HOLD_ASK, hl);
            W32(f->mem, ITEM_HOLD_ASK + 8, hl ? hl->id : 0xffffffffu);
        }
        snprintf(f->hand, sizeof f->hand, "%s", (const char *)(uintptr_t)r->x[3]);
        snprintf(f->pod, sizeof f->pod, "%s", (const char *)(uintptr_t)r->x[4]);
        r->x[0] = 1;
        return 1;
    case FOLLOW_SET:  // (refused while the actor's FLAG_OWNS_FOLLOW is set, as in the game)
        g_follow_sets++;
        if (!r->x[1]) g_follow_zero++;
        if ((f = fi_of(a0)) && !(*(uint32_t *)(void *)(f->mem + ACTOR_FLAGS) & FLAG_OWNS_FOLLOW))
            W64(f->mem, ACTOR_FOLLOW, r->x[1]);
        return 1;
    case FOLLOW_CTOR:
        g_ctors++;
        memset((void *)(uintptr_t)a0, 0, FOLLOW_SIZE);
        W64((void *)(uintptr_t)a0, 0, 0x7001d1cab8ull);
        return 1;
    case MTX_FROM_EULER: {
        float *o = (float *)(uintptr_t)a0, *tr = (float *)(uintptr_t)r->x[2];
        float m[12] = {1, 0, 0, tr[0], 0, 1, 0, tr[1], 0, 0, 1, tr[2]};
        memcpy(o, m, sizeof m);
        return 1;
    }
    case ACTOR_TYPEINFO:
        r->x[0] = 1;
        return 1;
    case FAKE_ISA:
        r->x[0] = 1;
        return 1;
    }
    g_calls--;
    return 0;
}

static Requests g_irq;
static double g_it = 5000.0;
static int g_items_world_ok;

static void items_fresh(int with_hands) {
    for (int i = 0; i < g_nfi; i++) g_fi[i].used = 0;
    g_nfi = 0;
    memset(t_items, 0, sizeof t_items);
    memset(&t_status.items, 0, sizeof t_status.items);
    memset(g_trash, 0, sizeof g_trash);
    memset(t_trash_t, 0, sizeof t_trash_t);
    memset(g_item_handle, 0, sizeof g_item_handle);
    memset(t_item_orphan, 0, sizeof t_item_orphan);
    for (int i = 0; i < ITEM_SLOTS; i++) item_zero_link(i);
    t_items_ghost = 0;
    memset(&g_irq, 0, sizeof g_irq);
    g_irq.items_back = 1, g_irq.items_mask = 4;
    fake_ghost(with_hands);
    g_calls = g_deletes = g_releases = g_requests = g_follow_zero = g_follow_sets = g_ctors = 0;
}

static void items_want(const char *w, const char *s, const char *b, int mask) {
    g_irq.items_on = 1, g_irq.items_mask = mask;
    snprintf(g_irq.item_names[0], 64, "%s", w);
    snprintf(g_irq.item_names[1], 64, "%s", s);
    snprintf(g_irq.item_names[2], 64, "%s", b);
}

// secs of frames (30 a second): the tick, then the game's frame.
static void items_run(uint64_t ghost, double secs) {
    struct Cpu *c = (struct Cpu *)(void *)g_cpu;
    for (double end = g_it + secs; g_it < end; g_it += 1.0 / 30.0) {
        items_tick(c, ghost, &g_irq, g_it);
        world_step();
    }
}

static int held_count(void) {
    int n = 0;
    for (int i = 0; i < ITEM_SLOTS; i++) n += t_status.items.slot[i].state == IS_HELD;
    return n;
}
static int live_items(void) {
    int n = 0;
    for (int i = 0; i < g_nfi; i++) n += g_fi[i].used && !g_fi[i].dead;
    return n;
}

static void check_items(void) {
    if (!g_items_world_ok) {
        check("items", "world", 1, "SKIP: the made-up globals' page is taken in this process");
        return;
    }
    uint64_t ghost = P(g_gh.mem);
    // the bones, and all three held with the right hand bone, retried until the item's AI ran once
    items_fresh(1);
    items_want("Weapon_Sword_001", "Weapon_Shield_001", "Weapon_Bow_001", 0);
    items_run(ghost, 1.5);
    check("items", "bones", !strcmp(t_status.items.bones, "ok") && t_bone_idx[BONE_R] == 1 && t_bone_idx[BONE_L] == 2 &&
          t_bone_idx[BONE_POD] == 3, "bones '%s' %d %d %d", t_status.items.bones, t_bone_idx[0], t_bone_idx[1],
          t_bone_idx[2]);
    int hands_ok = g_nfi == 3 && !strcmp(g_fi[0].hand, "Weapon_R") && !strcmp(g_fi[1].hand, "Weapon_L") &&
                   !strcmp(g_fi[2].hand, "Weapon_L") && !strcmp(g_fi[0].pod, "Pod_A") && g_fi[2].slot == 2;
    check("items", "held", held_count() == 3 && hands_ok && g_fi[0].grabs_refused >= 1,
          "%d held, %d made, hands %s/%s/%s, refused first %d", held_count(), g_nfi, g_fi[0].hand, g_fi[1].hand,
          g_fi[2].hand, g_fi[0].grabs_refused);
    check("items", "in_hand", !strcmp(t_status.items.slot[0].place, "hand") && t_status.items.slot[0].dist >= 0 &&
          t_status.items.slot[0].dist < 0.01f, "place %s, dist %.3f", t_status.items.slot[0].place,
          t_status.items.slot[0].dist);
    // the same names again: nothing new
    int req = g_requests;
    items_want("Weapon_Sword_001", "Weapon_Shield_001", "Weapon_Bow_001", 0);
    items_run(ghost, 1.0);
    check("items", "same_again", g_requests == req && held_count() == 3, "%d new requests", g_requests - req);
    // switch the weapon: the old one is deleted (never let go), the new one held; the others stay the same actors
    uint64_t shield = t_status.items.slot[1].actor;
    int del = g_deletes;
    items_want("Weapon_Lsword_001", "Weapon_Shield_001", "Weapon_Bow_001", 0);
    items_run(ghost, 1.0);
    check("items", "switch", g_deletes == del + 1 && g_fi[0].dead && held_count() == 3 &&
          t_status.items.slot[1].actor == shield && !strcmp(t_status.items.slot[0].name, "Weapon_Lsword_001"),
          "deletes +%d, old dead %d, held %d", g_deletes - del, g_fi[0].dead, held_count());
    // ten quick switches: always one weapon at the end, nothing left over
    for (int k = 0; k < 10; k++) {
        items_want(k % 2 ? "Weapon_Sword_001" : "Weapon_Spear_001", "Weapon_Shield_001", "Weapon_Bow_001", 0);
        items_run(ghost, 0.3);
    }
    items_run(ghost, 2.0);
    check("items", "quick_switches", held_count() == 3 && live_items() == 3 && t_status.items.stray == 0 &&
          !strcmp(t_status.items.slot[0].name, "Weapon_Sword_001"), "held %d, live %d, stray %d", held_count(),
          live_items(), t_status.items.stray);
    // the back: our follow (built once), the item's own offsets; then the hand again; never a 0 written
    items_want("Weapon_Sword_001", "Weapon_Shield_001", "Weapon_Bow_001", 7);
    items_run(ghost, 0.5);
    check("items", "back", !strcmp(t_status.items.slot[0].place, "back") && !strcmp(t_status.items.slot[2].place, "back") &&
          g_ctors == 3 && t_status.items.slot[0].hold > 0.29f && t_status.items.slot[0].hold < 0.31f,
          "place %s/%s, ctors %d, hold %.2f", t_status.items.slot[0].place, t_status.items.slot[2].place, g_ctors,
          t_status.items.slot[0].hold);
    for (int k = 0; k < 10; k++) {
        items_want("Weapon_Sword_001", "Weapon_Shield_001", "Weapon_Bow_001", k % 2 ? 7 : 0);
        items_run(ghost, 0.3);
    }
    items_want("Weapon_Sword_001", "Weapon_Shield_001", "Weapon_Bow_001", 0);
    items_run(ghost, 0.5);
    check("items", "back_toggles", !strcmp(t_status.items.slot[0].place, "hand") && g_follow_zero == 0 &&
          g_ctors == 6 && held_count() == 3, "place %s, zero writes %d, ctors %d", t_status.items.slot[0].place,
          g_follow_zero, g_ctors);
    g_irq.items_back = 0;
    items_want("Weapon_Sword_001", "Weapon_Shield_001", "Weapon_Bow_001", 7);
    items_run(ghost, 0.5);
    check("items", "back_off", !strcmp(t_status.items.slot[2].place, "hand"), "place %s", t_status.items.slot[2].place);
    g_irq.items_back = 1;
    // clear the shield, then everything
    uint64_t sword = t_status.items.slot[0].actor;
    items_want("Weapon_Sword_001", "", "Weapon_Bow_001", 0);
    items_run(ghost, 1.5);
    check("items", "clear_one", t_status.items.slot[1].state == IS_NONE && t_status.items.slot[0].actor == sword &&
          live_items() == 2 && t_status.items.stray == 0, "shield %s, live %d", IS_NAMES[t_status.items.slot[1].state],
          live_items());
    items_want("", "", "", 0);
    items_run(ghost, 1.5);
    check("items", "clear_all", live_items() == 0 && held_count() == 0 && t_status.items.stray == 0, "live %d",
          live_items());
    // the game lets one go: it is deleted at once and asked for again (a try)
    items_want("Weapon_Sword_001", "", "", 0);
    items_run(ghost, 1.0);
    FakeItem *f = fi_of(t_status.items.slot[0].actor);
    if (f) f->mem[ITEM_STATE] = 1;
    del = g_deletes;
    items_run(ghost, 0.2);
    int deleted_at_once = g_deletes == del + 1;
    items_run(ghost, 3.0);
    check("items", "let_go", deleted_at_once && held_count() == 1 && t_status.items.lost == 1 &&
          t_status.items.slot[0].tries == 2, "deleted at once %d, held %d, tries %d", deleted_at_once, held_count(),
          t_status.items.slot[0].tries);
    // the ghost is removed (despawn, the far rule): every item deleted; a new ghost gets them again
    del = g_deletes;
    items_drop((struct Cpu *)(void *)g_cpu, g_it);
    items_run(0, 0.5);
    check("items", "ghost_gone", g_deletes == del + 1 && live_items() == 0 && held_count() == 0 &&
          t_status.items.slot[0].actor == 0 && !t_status.items.slot[0].place[0],
          "deletes +%d, live %d, status %s %llx", g_deletes - del, live_items(),
          IS_NAMES[t_status.items.slot[0].state], (unsigned long long)t_status.items.slot[0].actor);
    items_run(ghost, 1.5);
    check("items", "ghost_back", held_count() == 1, "held %d", held_count());
    // a stage change: forgotten without a single game call, but watched (0.6.1): one the stage change didn't take
    // along (the game kept the old ghost) shows as a stray
    int calls = g_calls;
    uint64_t kept_actor = t_status.items.slot[0].actor;
    items_forget(g_it);
    int watched = 0;
    for (int k = 0; k < TRASH_MAX; k++) watched += t_trash_t[k] != 0;
    check("items", "stage_change", g_calls == calls && t_items_ghost == 0 && g_item_link[0].data == 0 && watched == 1,
          "%d calls, %d watched", g_calls - calls, watched);
    items_run(0, 1.5);
    int stray_seen = t_status.items.stray;
    FakeItem *kf = fi_of(kept_actor);
    if (kf) kf->dead = 1, ld_kill(&kf->ld);
    items_run(0, 0.2);
    check("items", "stage_change_stray", stray_seen == 1 && t_status.items.stray == 0 && t_status.items.dying == 0,
          "stray %d, then %d (dying %d)", stray_seen, t_status.items.stray, t_status.items.dying);
    // 0.6.1: a request still pending at a stage change: released by the next items part (its unit still ours)
    items_fresh(1);
    items_want("Weapon_Sword_001", "", "", 0);
    for (int k = 0; k < 40 && t_items[0].state != IS_PENDING; k++) items_run(ghost, 1.0 / 30.0);
    int was_pending = t_items[0].state == IS_PENDING;
    items_forget(g_it);
    int rel0 = g_releases;
    items_run(0, 0.2);
    check("items", "orphan_released", was_pending && g_releases == rel0 + 1 && live_items() == 0 &&
          !t_item_orphan[0] && g_item_handle[0].unit == 0, "pending %d, releases +%d, live %d", was_pending,
          g_releases - rel0, live_items());
    // 0.6.1: spawned over the ghost (out of Link's reach) and kept there until the grab took it
    items_fresh(1);
    items_want("Weapon_Sword_001", "Weapon_Shield_001", "Weapon_Bow_001", 0);
    items_run(ghost, 1.5);
    check("items", "parked", held_count() == 3 && t_status.items.parks >= 3 && g_item_mtx[0][7] > 5.99f &&
          g_item_mtx[0][7] < 6.01f && g_set_y > 5.99f && t_status.items.slot[0].refusals == 1,
          "held %d, parks %lld, y %.2f (set %.2f), refusals %d", held_count(), (long long)t_status.items.parks,
          g_item_mtx[0][7], g_set_y, t_status.items.slot[0].refusals);
    // 0.6.1: Link takes the new item in the moment before the ghost's grab: it is his now, never deleted
    items_fresh(1);
    FakeItem *lk = fake_item_new("GameROMPlayer", 0);  // (made up: an actor with a link of its own)
    lk->ready_in = 0;
    items_want("Weapon_Sword_001", "", "", 0);
    for (int k = 0; k < 40 && !(t_items[0].state == IS_GRAB && !t_items[0].grabbed); k++) items_run(ghost, 1.0 / 30.0);
    FakeItem *gap = g_item_link[0].data ? fi_of(((FakeLinkData *)(uintptr_t)g_item_link[0].data)->proc) : NULL;
    int in_gap = gap && t_items[0].state == IS_GRAB && !t_items[0].grabbed;
    if (gap) {  // (his grab settled: his holder link)
        W64(gap->mem, ITEM_HOLDER, &lk->ld);
        W32(gap->mem, ITEM_HOLDER + 8, lk->ld.id);
        gap->mem[ITEM_STATE] = 0xff;
    }
    int del0 = g_deletes;
    items_run(ghost, 1.0 / 30.0);
    check("items", "taken_in_gap", in_gap && g_deletes == del0 && gap && !gap->deleting && !gap->dead &&
          t_status.items.taken == 1 && t_items[0].state == IS_WAIT && g_item_link[0].data == 0,
          "in gap %d, deletes +%d, taken %lld, state %s, why '%s'", in_gap, g_deletes - del0,
          (long long)t_status.items.taken, IS_NAMES[t_items[0].state], t_status.items.slot[0].why);
    // 0.6.1: an item on our follow gets its own back before it is deleted; one whose follow the game won't change
    // (FLAG_OWNS_FOLLOW) is not deleted
    items_fresh(1);
    items_want("Weapon_Sword_001", "Weapon_Shield_001", "Weapon_Bow_001", 7);
    items_run(ghost, 2.0);
    int on_back = !strcmp(t_status.items.slot[0].place, "back") && !strcmp(t_status.items.slot[1].place, "back");
    FakeItem *stuck = fi_of(t_status.items.slot[2].actor);
    if (stuck) *(uint32_t *)(void *)(stuck->mem + ACTOR_FLAGS) |= FLAG_OWNS_FOLLOW;
    items_want("", "", "", 0);
    items_run(ghost, 1.0);
    int own_back = 0;
    for (int k = 0; k < g_nfi; k++)
        own_back += g_fi[k].dead && R64(g_fi[k].mem, ACTOR_FOLLOW) == P(g_fi[k].mem + ITEM_FOLLOW);
    check("items", "own_follow_first", on_back && own_back == 2 && g_follow_zero == 0,
          "on back %d, %d deleted with their own follow", on_back, own_back);
    check("items", "owns_follow_kept", stuck && !stuck->deleting && !stuck->dead && t_status.items.kept == 1,
          "deleting %d, kept %lld", stuck ? stuck->deleting : -1, (long long)t_status.items.kept);
    // 0.6.1: only weapons, shields and bows by the game's prefixes, each in its own hand, and no special one
    int names_ok = item_name_ok(0, "Weapon_Sword_001") && item_name_ok(0, "Weapon_Lsword_001") &&
                   item_name_ok(0, "Weapon_Spear_001") && item_name_ok(1, "Weapon_Shield_001") &&
                   item_name_ok(2, "Weapon_Bow_001") && !item_name_ok(0, "Weapon_Shield_001") &&
                   !item_name_ok(2, "Weapon_Sword_001") && !item_name_ok(0, "Weapon_Sword_070") &&
                   !item_name_ok(2, "Weapon_Bow_071") && !item_name_ok(0, "Enemy_Bokoblin_Junior") &&
                   !item_name_ok(0, "GameROMPlayer") && !item_name_ok(0, "Weapon_Sword_");
    items_fresh(1);
    items_want("Enemy_Bokoblin_Junior", "Npc_Hylia", "Weapon_Sword_070", 0);
    items_run(ghost, 2.0);
    check("items", "names", names_ok && g_requests == 0, "rule %d, %d requests", names_ok, g_requests);
    // a name the game refuses: 3 tries, then given up (no more requests)
    items_fresh(1);
    items_want(g_refuse_name, "", "", 0);
    items_run(ghost, 20.0);
    req = g_requests;
    items_run(ghost, 10.0);
    check("items", "gave_up", t_status.items.slot[0].state == IS_FAILED && t_status.items.slot[0].tries == ITEM_TRIES &&
          g_requests == req, "state %s, tries %d", IS_NAMES[t_status.items.slot[0].state], t_status.items.slot[0].tries);
    // not of the Weapon class: released, no retry
    items_fresh(1);
    g_not_weapon = 1;
    items_want("Weapon_Sword_998", "", "", 0);
    items_run(ghost, 8.0);
    check("items", "not_a_weapon", t_status.items.slot[0].state == IS_FAILED && g_requests == 1 && g_releases == 1 &&
          live_items() == 0, "state %s, requests %d, releases %d", IS_NAMES[t_status.items.slot[0].state], g_requests,
          g_releases);
    // a body without hand bones (the Sheikah man): no item asked for
    items_fresh(0);
    items_want("Weapon_Sword_001", "", "", 0);
    items_run(ghost, 6.0);
    check("items", "no_hand_bones", g_requests == 0 && !strncmp(t_status.items.bones, "missing", 7), "bones '%s', %d requests",
          t_status.items.bones, g_requests);
    // off: nothing is asked for, and the tick makes no game call
    items_fresh(1);
    calls = g_calls;
    items_run(ghost, 1.0);
    check("items", "off_costs_nothing", g_calls == calls, "%d calls", g_calls - calls);
    // `ghost give`: spawned near Link and taken, never the ghost's items
    items_fresh(1);
    static _Alignas(16) uint8_t pi[0x100], player[0x400];
    float lp[3] = {1, 2, 3}, lm[12] = {1, 0, 0, 1, 0, 1, 0, 2, 0, 0, 1, 3};
    memcpy(pi + PI_POS, lp, sizeof lp);
    memcpy(player + ACTOR_MTX, lm, sizeof lm);
    g_fake_vtable[2] = FAKE_ISA;
    g_not_weapon = 1;  // (its vtable: the fake one with isA)
    g_irq.give_seq++;
    snprintf(g_irq.give_name, sizeof g_irq.give_name, "%s", "Weapon_Sword_001");
    struct Cpu *c = (struct Cpu *)(void *)g_cpu;
    for (int k = 0; k < 5; k++) {
        give_tick(c, &g_irq, P(pi), P(player), g_it);
        world_step();
        g_it += 1.0 / 30.0;
    }
    check("items", "give", t_status.items.give_state == GV_DONE && g_nfi == 1 && g_fi[0].taken &&
          t_status.items.slot[0].state == IS_NONE, "give %s, made %d", GV_NAMES[t_status.items.give_state], g_nfi);
    g_not_weapon = 0;
    // 0.6.2: a weapon held by anyone but the player gets a bigger scale from its own update (1.5 here): the ghost's
    // grabbed items are named to the WEAPON_CALC wrapper, which gives them the player's scale (base x object 9's
    // +0x50); a deleted one is no longer named
    items_fresh(1);
    g_fake_axis = 1.0f;
    items_want("Weapon_Sword_001", "Weapon_Shield_001", "", 0);
    items_run(ghost, 2.0);
    FakeItem *sw = fi_of(t_status.items.slot[0].actor);
    int named = sw && g_size_item[0] == P(sw->mem) && g_size_item[1] == t_status.items.slot[1].actor &&
                g_size_item[2] == 0;
    float npc[3] = {1.5f, 1.5f, 1.5f}, base[3] = {1.0f, 1.0f, 1.0f}, k = 1.0f;
    if (sw) {
        memcpy(sw->mem + ACTOR_SCALE, npc, sizeof npc);  // (the game's update: an NPC's scale)
        memcpy(sw->mem + WEAPON_BASE_SCALE, base, sizeof base);
        memcpy(sw->obj + WEAPON_SCALE_PLAYER, &k, sizeof k);
        weapon_player_scale(P(sw->mem));
    }
    items_run(ghost, 0.3);
    const ItemStatus *o0 = &t_status.items.slot[0];
    check("items", "size_player", held_count() == 2 && named && o0->size > 0.99f && o0->size < 1.01f &&
          o0->own_scale > 0.99f && o0->axis > 0.99f && g_scale_sets >= 1,
          "held %d, named %d, size %.2f (own %.2f, axes %.2f), sets %lld", held_count(), named, o0->size,
          o0->own_scale, o0->axis, (long long)g_scale_sets);
    items_want("Weapon_Lsword_001", "", "", 0);
    items_run(ghost, 0.1);
    int unnamed = sw && g_size_item[0] != P(sw->mem) && g_size_item[1] == 0;
    items_run(ghost, 1.5);
    check("items", "size_unnamed", unnamed && g_size_item[0] == t_status.items.slot[0].actor && held_count() == 1,
          "unnamed %d, held %d", unnamed, held_count());
    g_fake_axis = 0;
    // 0.6.2: a stage change drops the ghost and its items without a call; the game kept them (as after the fourteenth
    // round's Game Over): nothing while there is no player or for LEFTOVER_WAIT_S with him, then they are removed
    items_fresh(1);
    items_want("Weapon_Sword_001", "Weapon_Shield_001", "Weapon_Bow_001", 4);
    items_run(ghost, 2.0);
    int held3 = held_count() == 3;
    memset(t_old_ghost_t, 0, sizeof t_old_ghost_t);
    g_link.data = P(&g_gh.ld), g_link.id = g_gh.ld.id;
    t_stage_back_t = 0;
    t_status.items.leftovers = 0;
    calls = g_calls;
    old_ghost_add(g_it);
    items_forget(g_it);
    zero_link();
    for (int k = 0; k < 30; k++, g_it += 1.0 / 30.0) leftovers_tick(c, 0, g_it);  // (loading: no player)
    int quiet = g_calls == calls && t_status.items.old_ghosts == 1 && t_status.items.old_items == 3;
    for (int k = 0; k < 60; k++, g_it += 1.0 / 30.0) leftovers_tick(c, P(player), g_it);  // (2 s with him)
    int waited = t_status.items.leftovers == 0;
    del = g_deletes;
    for (int k = 0; k < 45; k++, g_it += 1.0 / 30.0) {  // (then 1.5 s more)
        leftovers_tick(c, P(player), g_it);
        world_step();
    }
    check("items", "leftovers", held3 && quiet && waited && t_status.items.leftovers == 4 && g_deletes == del + 4 &&
          live_items() == 0 && !g_gh.ld.proc && t_status.items.old_ghosts == 0 && t_status.items.old_items == 0,
          "held %d, quiet %d, waited %d, removed %lld, deletes +%d, live %d", held3, quiet, waited,
          (long long)t_status.items.leftovers, g_deletes - del, live_items());
    // ... and one that is gone (the stage change took it along): nothing is called
    items_fresh(1);
    items_want("Weapon_Sword_001", "", "", 0);
    items_run(ghost, 2.0);
    g_link.data = P(&g_gh.ld), g_link.id = g_gh.ld.id;
    t_stage_back_t = 0;
    t_status.items.leftovers = 0;
    old_ghost_add(g_it);
    items_forget(g_it);
    zero_link();
    ld_kill(&g_gh.ld);
    for (int i = 0; i < g_nfi; i++) g_fi[i].dead = 1, ld_kill(&g_fi[i].ld);
    del = g_deletes;
    for (int k = 0; k < 150; k++, g_it += 1.0 / 30.0) leftovers_tick(c, P(player), g_it);
    // 0.6.2: the other player's own back offset (`ghost items rel`): the bow's follow gets exactly that matrix (no
    // euler call), its hold length is the matrix's; a new one while on the back moves it to the other follow
    items_fresh(1);
    float relm[12] = {0, 0, 1, 0.12f, 0, 1, 0, -0.05f, -1, 0, 0, 0.2f};
    memcpy(g_irq.item_rel[2], relm, sizeof relm);
    g_irq.item_has_rel[2] = 1;
    items_want("Weapon_Sword_001", "", "Weapon_Bow_001", 4);
    items_run(ghost, 2.0);
    uint64_t bow_fol = t_status.items.slot[2].actor ? R64((void *)(uintptr_t)t_status.items.slot[2].actor, ACTOR_FOLLOW) : 0;
    int copied_ok = follow_is_ours(2, bow_fol) &&
                    !memcmp((const uint8_t *)(uintptr_t)bow_fol + FOLLOW_OFFSET, relm, sizeof relm) &&
                    t_status.items.slot[2].copied && t_status.items.slot[2].hold > 0.238f &&
                    t_status.items.slot[2].hold < 0.239f && !t_status.items.slot[0].copied;
    relm[3] = 0.3f;
    memcpy(g_irq.item_rel[2], relm, sizeof relm);
    items_run(ghost, 0.3);
    uint64_t bow_fol2 = t_status.items.slot[2].actor ? R64((void *)(uintptr_t)t_status.items.slot[2].actor, ACTOR_FOLLOW) : 0;
    int moved = bow_fol2 != bow_fol && follow_is_ours(2, bow_fol2) &&
                !memcmp((const uint8_t *)(uintptr_t)bow_fol2 + FOLLOW_OFFSET, relm, sizeof relm);
    g_irq.item_has_rel[2] = 0;
    items_run(ghost, 0.3);
    int sets = g_follow_sets;
    items_run(ghost, 0.5);
    check("items", "back_copied", held_count() == 2 && copied_ok && moved && !t_status.items.slot[2].copied &&
          g_follow_sets == sets, "held %d, copied %d, moved %d, follow sets +%d once settled", held_count(), copied_ok,
          moved, g_follow_sets - sets);
    check("items", "leftovers_gone", t_status.items.leftovers == 0 && g_deletes == del && t_status.items.old_ghosts == 0,
          "removed %lld, deletes +%d", (long long)t_status.items.leftovers, g_deletes - del);
}

static void items_setup(void) {
    // the spawner (CREATOR_PTR) and the scene object (HEAP_HOLDER_PTR) get made-up memory at their real addresses
    uint8_t *page = VirtualAlloc((void *)(uintptr_t)0x7001e00000ull, 0x100000, 0x3000, 0x04);
    if (!page) return;
    static _Alignas(16) uint8_t creator[0x100], holder[0x100], heap[0x40];
    W64((void *)(uintptr_t)CREATOR_PTR, 0, creator);
    W64((void *)(uintptr_t)HEAP_HOLDER_PTR, 0, holder);
    W64(holder, HEAP_OFF, heap);
    g_items_world_ok = 1;
}

SWMOD_EXPORT int ghost_selftest(char *out, int cap) {
    g_out = out, g_cap = cap, g_len = 0, g_fails = 0;
    if (out && cap > 0) out[0] = 0;
    setup();
    check_names();
    check_missing();
    check_late();
    items_setup();
    check_items();
    return g_fails;
}
