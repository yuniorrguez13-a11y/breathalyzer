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
static int coop_call(struct Cpu *c, uint64_t fn);   // (the fake flag manager, below)
static int pup_call(struct Cpu *c, uint64_t fn);    // (0.9.0: the self puppet's fake calls, below)

static void fake_guest_call(struct Cpu *c, uint64_t fn) {
    SwModRegs *r = SWMOD_REGS(c);
    if (pup_call(c, fn)) return;
    if (coop_call(c, fn)) return;
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
        weapon_player_scale(P(sw->mem), 0);
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

// ------------------------------------------------------------------ co-op: a fake flag manager (coop_story.c)
//
// The manager is a made-up block (its busy byte at COOP_BUSY_OFF); the getter and setter look the name up in a small
// table by the string object's char*. Every set is logged: a set to 0, or of a name the list doesn't have, fails.

#define FF_N 6
#define FF_GET 0x70000000f0ull  // the fake getter's address (the game's getter is not known yet: COOP_GET_BOOL is 0)
static const char *const FF_NAMES[FF_N] = {"FirstTower_Activated", "MapTower_01", "Clear_Dungeon000",
                                            "IsGet_MemoryPhoto_001", "IsGet_Obj_Camera", "Outside_The_List"};
static int g_ff_val[FF_N];
static int64_t g_ff_sets, g_ff_set_zero, g_ff_set_unlisted, g_ff_gets, g_ff_refuse_next;
static _Alignas(16) uint8_t g_ff_mgr[0x1000];
static _Alignas(16) uint64_t g_ff_slot;
static int g_ff_world = 1, g_ff_free = 1;
static int ff_world(void) { return g_ff_world; }
static int ff_free(void) { return g_ff_free; }

static int ff_find(uint64_t obj) {
    const StrObj *o = (const StrObj *)(uintptr_t)obj;
    if (!o || o->vptr != STRING_OBJ_VPTR || !o->str) return -1;
    for (int i = 0; i < FF_N; i++)
        if (!strcmp(FF_NAMES[i], (const char *)(uintptr_t)o->str)) return i;
    return -1;
}

static int coop_call(struct Cpu *c, uint64_t fn) {
    SwModRegs *r = SWMOD_REGS(c);
    if (fn != FF_GET && fn != COOP_SET_BOOL) return 0;
    int busy = r->x[0] != P(g_ff_mgr) || (g_ff_mgr[COOP_BUSY_OFF] & COOP_BUSY_BIT);
    int i = ff_find(r->x[2]);
    if (fn == FF_GET) {
        g_ff_gets++;
        if (busy || i < 0) {
            r->x[0] = 0;
            return 1;
        }
        *(uint8_t *)(uintptr_t)r->x[1] = (uint8_t)g_ff_val[i];
        r->x[0] = 1;
        return 1;
    }
    if (busy || i < 0 || g_ff_refuse_next > 0) {
        if (g_ff_refuse_next > 0) g_ff_refuse_next--;
        r->x[0] = 0;
        return 1;
    }
    g_ff_sets++;
    if (!(r->x[1] & 1)) g_ff_set_zero++;
    if (i == FF_N - 1) g_ff_set_unlisted++;
    g_ff_val[i] = (int)(r->x[1] & 1);
    r->x[0] = 1;
    return 1;
}

static void coop_ticks(int n) {
    for (int k = 0; k < n; k++) coop_tick((struct Cpu *)(void *)g_cpu);
}

static CoopPub coop_pub_now(void) {
    CoopPub pub;
    lock();
    pub = g_coop_pub;
    unlock();
    return pub;
}

static const char *const COOP_LIST = "# Story list (names only), built from this PC's own game files by tools/mp/story.py\n"
                                     "base FirstTower_Activated\r\nbase MapTower_01\nbase Clear_Dungeon000\n"
                                     "base IsGet_MemoryPhoto_001\ndlc IsGet_Obj_Camera\n";
static void coop_load_between(void) {  // (a list load between an add's count and its queue)
    g_coop_between = NULL;
    coop_load_text(COOP_LIST, 1);
}

static void check_coop(void) {
    g_coop_mgr_slot = P(&g_ff_slot);
    g_ff_slot = P(g_ff_mgr);
    g_coop_world = ff_world;
    g_coop_free = ff_free;
    g_coop_settle = 1;
    g_coop_get_fn = FF_GET;
    memset(g_ff_val, 0, sizeof g_ff_val);
    g_ff_val[0] = 1;  // FirstTower_Activated already set
    int n = coop_load_text(COOP_LIST, 0);
    check("coop", "load", n == 4, "n %d (the dlc line left out)", n);
    check("coop", "bad_list", coop_load_text("base Bad-Name\n", 0) < 0, "a bad name refused");
    n = coop_load_text(COOP_LIST, 1);
    check("coop", "load_dlc", n == 5, "n %d", n);
    coop_ticks(1);
    CoopPub pub = coop_pub_now();
    char hex[2 * COOP_BYTES + 1];
    coop_bits_hex(pub.bits, pub.n, hex);
    check("coop", "reads", pub.ready && pub.seq == 1 && !strcmp(hex, "01"), "ready %d seq %lld bits %s", pub.ready,
          (long long)pub.seq, hex);
    coop_ticks(3);
    pub = coop_pub_now();
    check("coop", "seq_steady", pub.seq == 1, "seq %lld after passes with no change", (long long)pub.seq);

    check("coop", "bad_hex", coop_add_hex("20") < 0 && coop_add_hex("0") < 0 && coop_add_hex("0A") < 0 &&
          coop_add_hex("0001") < 0, "a bit past the list, odd, upper case, too long: refused");
    int64_t sets0 = g_ff_sets;
    int q = coop_add_hex("1f");  // all five; FirstTower is already 1; only the tower and the memory go live
    coop_ticks(2);
    pub = coop_pub_now();
    check("coop", "adds_live_only", q == 5 && g_ff_val[1] && g_ff_val[3] && !g_ff_val[2] && !g_ff_val[4] &&
          g_ff_sets - sets0 == 2 && !g_ff_set_zero && !g_ff_set_unlisted && !g_ff_val[5],
          "queued %d, sets %lld, shrine %d, rune %d, set to 0 %lld, unlisted %lld", q, (long long)(g_ff_sets - sets0),
          g_ff_val[2], g_ff_val[4], (long long)g_ff_set_zero, (long long)g_ff_set_unlisted);
    check("coop", "held", pub.held == 3, "held %lld (the quest step, the shrine and the rune)", (long long)pub.held);
    coop_ticks(2);
    pub = coop_pub_now();
    coop_bits_hex(pub.bits, pub.n, hex);
    check("coop", "get_hex", !strcmp(hex, "0b") && pub.seq == 2 && pub.pending == 0, "bits %s seq %lld pending %lld",
          hex, (long long)pub.seq, (long long)pub.pending);

    // busy: nothing read or set while the busy bit is up; done once it is down
    coop_load_text(COOP_LIST, 1);
    coop_ticks(1);
    memset(g_ff_val, 0, sizeof g_ff_val);
    coop_ticks(1);
    g_ff_mgr[COOP_BUSY_OFF] |= COOP_BUSY_BIT;
    int64_t gets0 = g_ff_gets;
    sets0 = g_ff_sets;
    coop_add_hex("08");
    coop_ticks(3);
    pub = coop_pub_now();
    check("coop", "busy_waits", g_ff_gets == gets0 && g_ff_sets == sets0 && pub.busy_waits >= 3 && pub.pending == 1 &&
          !pub.safe, "gets %lld sets %lld busy waits %lld pending %lld safe %d", (long long)(g_ff_gets - gets0),
          (long long)(g_ff_sets - sets0), (long long)pub.busy_waits, (long long)pub.pending, pub.safe);
    g_ff_mgr[COOP_BUSY_OFF] &= (uint8_t)~COOP_BUSY_BIT;
    g_ff_refuse_next = 1;  // the setter says no once: tried again next frame
    coop_ticks(1);
    int after_refuse = g_ff_val[3];
    coop_ticks(1);
    pub = coop_pub_now();
    check("coop", "retry", !after_refuse && g_ff_val[3] == 1 && pub.set_refused == 1, "after refuse %d, now %d, "
          "refused %lld", after_refuse, g_ff_val[3], (long long)pub.set_refused);

    // no world: nothing at all
    g_ff_world = 0;
    gets0 = g_ff_gets;
    coop_add_hex("02");
    coop_ticks(2);
    check("coop", "no_world", g_ff_gets == gets0 && g_ff_val[1] == 0, "gets %lld", (long long)(g_ff_gets - gets0));
    g_ff_world = 1;
    coop_ticks(2);
    check("coop", "world_back", g_ff_val[1] == 1, "set after the world is back");

    // Link not free (loading, a cutscene, riding, the pause menu): no add; then only after he is free for a while
    coop_load_text(COOP_LIST, 1);
    coop_ticks(1);
    memset(g_ff_val, 0, sizeof g_ff_val);
    coop_ticks(1);
    g_coop_settle = 3;
    g_ff_free = 0;
    sets0 = g_ff_sets;
    coop_add_hex("02");
    coop_ticks(3);
    pub = coop_pub_now();
    check("coop", "not_free_waits", g_ff_sets == sets0 && pub.unsafe_waits >= 3 && !pub.safe && pub.pending == 1,
          "sets %lld unsafe waits %lld safe %d pending %lld", (long long)(g_ff_sets - sets0),
          (long long)pub.unsafe_waits, pub.safe, (long long)pub.pending);
    g_ff_free = 1;
    coop_ticks(2);
    check("coop", "settles_first", g_ff_val[1] == 0, "set after %d free frames (needs %d)", 2, g_coop_settle);
    coop_ticks(1);
    pub = coop_pub_now();
    check("coop", "after_settle", g_ff_val[1] == 1 && pub.safe, "set %d safe %d", g_ff_val[1], pub.safe);

    // a load puts the save's 0 back: what we added is set again once it is safe (no getter: we can't see it)
    g_coop_get_fn = COOP_GET_BOOL;
    g_ff_world = 0;
    coop_ticks(1);
    g_ff_val[1] = 0;
    g_ff_world = 1;
    coop_ticks(2);
    int before_settle = g_ff_val[1];
    coop_ticks(1);
    check("coop", "set_again", !before_settle && g_ff_val[1] == 1 && !g_ff_set_zero, "before %d after %d",
          before_settle, g_ff_val[1]);
    g_coop_get_fn = FF_GET;
    g_coop_settle = 1;

    // a list loaded while an add is read: refused (its bits would mean other names)
    g_coop_between = coop_load_between;
    int stale = coop_add_hex("02");
    coop_ticks(2);
    pub = coop_pub_now();
    check("coop", "gen_refused", stale < 0 && pub.pending == 0, "returned %d pending %lld", stale,
          (long long)pub.pending);

    // the developer probe: one read on the next tick
    lock();
    memset(&g_coop_probe, 0, sizeof g_coop_probe);
    strcpy(g_coop_probe.name, "Outside_The_List");
    g_coop_probe.want = 1;
    unlock();
    g_ff_val[5] = 1;
    coop_ticks(1);
    check("coop", "probe_read", g_coop_probe.done && g_coop_probe.ok && g_coop_probe.value == 1 && !g_ff_set_unlisted,
          "done %d ok %lld value %lld", g_coop_probe.done, (long long)g_coop_probe.ok, (long long)g_coop_probe.value);
    // no known getter (the game's): a read never calls anything and answers "not found"
    g_coop_get_fn = COOP_GET_BOOL;
    int64_t gets = g_ff_gets;
    lock();
    memset(&g_coop_probe, 0, sizeof g_coop_probe);
    strcpy(g_coop_probe.name, "FirstTower_Activated");
    g_coop_probe.want = 1;
    unlock();
    coop_ticks(1);
    check("coop", "no_getter_no_call", g_coop_probe.done && !g_coop_probe.ok && g_ff_gets == gets,
          "done %d ok %lld gets %lld", g_coop_probe.done, (long long)g_coop_probe.ok, (long long)(g_ff_gets - gets));
    // ... and a list read without a getter never says "ready" (every read failed: its zeros mean nothing)
    coop_load_text(COOP_LIST, 0);
    coop_ticks(10);
    lock();
    int no_get_ready = g_coop_pub.ready;
    int64_t no_get_fails = g_coop_pub.read_fails;
    unlock();
    check("coop", "no_getter_not_ready", !no_get_ready && no_get_fails > 0 && g_ff_gets == gets,
          "ready %d read_fails %lld gets %lld", no_get_ready, (long long)no_get_fails, (long long)(g_ff_gets - gets));
    g_coop_world = coop_world_real;
    g_coop_free = coop_free_real;
    g_coop_settle = COOP_SETTLE_TICKS;
    g_coop_mgr_slot = COOP_FLAG_MGR;
    coop_load_text("", 0);
    coop_ticks(1);
}

// ------------------------------------------------------------------ 0.8.0 Who am I (iam.c): `iam` and `ghost looks`
// Fake replies: the fields the commands give, their console line and their failure.
static char g_rep_fail[512], g_rep_text[512], g_rep_iam[64];
static int64_t g_rep_seq, g_rep_looks;
static int g_rep_has_iam;
static void rep_str(SwModReply *r, const char *k, const char *v) {
    (void)r;
    if (!strcmp(k, "iam")) snprintf(g_rep_iam, sizeof g_rep_iam, "%s", v);
}
static void rep_int(SwModReply *r, const char *k, int64_t v) {
    (void)r;
    if (!strcmp(k, "iam_seq")) g_rep_seq = v;
    if (!strcmp(k, "looks")) g_rep_looks = v;
}
static void rep_num(SwModReply *r, const char *k, double v) { (void)r, (void)k, (void)v; }
static void rep_bool(SwModReply *r, const char *k, bool v) {
    (void)r;
    if (!strcmp(k, "has_iam")) g_rep_has_iam = v;
}
static void rep_text(SwModReply *r, const char *fmt, ...) {
    (void)r;
    __builtin_va_list ap;
    __builtin_va_start(ap, fmt);
    vsnprintf(g_rep_text, sizeof g_rep_text, fmt, ap);
    __builtin_va_end(ap);
}
static void rep_fail(SwModReply *r, const char *fmt, ...) {
    (void)r;
    __builtin_va_list ap;
    __builtin_va_start(ap, fmt);
    vsnprintf(g_rep_fail, sizeof g_rep_fail, fmt, ap);
    __builtin_va_end(ap);
}
// Run one command line through a command function, as the console splits it (spaces).
static void run_line(void (*fn)(int, char **, SwModReply *), const char *line) {
    static char buf[1024];
    char *argv[16];
    int argc = 0;
    snprintf(buf, sizeof buf, "%s", line);
    for (char *t = strtok(buf, " "); t && argc < 16; t = strtok(0, " ")) argv[argc++] = t;
    g_rep_fail[0] = g_rep_text[0] = g_rep_iam[0] = 0;
    g_rep_seq = g_rep_looks = -1;
    g_rep_has_iam = 0;
    fn(argc, argv, (SwModReply *)(void *)g_rep_text);
}

// 0.9.8 Linkle by restart: `iam linkle` spawns no costume (pup_id: none) and says whether her body is on, from the
// deploy's launch.json (its top "mods" list: a mod with linkle in its id or name), else how to get it in plain words.
static int g_fake_linkle;
static int fake_linkle(void) { return g_fake_linkle; }
static void check_iam_linkle(void) {
    static const char *const ON = "{\n \"env\": {\"X\": \"linkle\"},\n \"mods\": [\n  {\n   \"id\": \"the-linkle-mod\",\n"
                                  "   \"name\": \"The Linkle Mod\",\n   \"kinds\": [\n    \"romfs\"\n   ]\n  }\n ],\n"
                                  " \"parts\": {\"native\": {\"mods\": [\"ghost.dll\"]}}\n}\n";
    static const char *const OFF = "{\n \"mods\": [\n  {\n   \"id\": \"giruka.ak47\",\n   \"name\": \"Extra item\",\n"
                                   "   \"kinds\": [\"romfs\", \"linkle\"]\n  }\n ],\n \"notes\": [\"Better Sprint replaces 2 "
                                   "of Linkle's files\"]\n}\n";
    static const char *const BY_NAME = "{\"mods\": [{\"id\": \"x.y\", \"name\": \"LINKLE body\"}]}";
    int parse = iam_launch_linkle(ON) == 1 && iam_launch_linkle(OFF) == 0 && iam_launch_linkle(BY_NAME) == 1 &&
                iam_launch_linkle("{\"mods\": [{\"id\": \"a\"") == -1 && iam_launch_linkle("{}") == -1 &&
                iam_launch_linkle(NULL) == -1 && iam_linkle_file("Z:\\no\\such\\launch.json") == -1;
    check("iam", "linkle_launch_read", parse, "on %d off %d name %d cut %d", iam_launch_linkle(ON),
          iam_launch_linkle(OFF), iam_launch_linkle(BY_NAME), iam_launch_linkle("{\"mods\": [{\"id\": \"a\""));
    int (*was)(void) = g_iam_linkle;
    g_iam_linkle = fake_linkle;
    g_fake_linkle = 1;
    run_line(cmd_iam, "iam linkle");
    int on = !strcmp(g_rep_iam, "linkle") && strstr(g_rep_text, "You are Linkle (her body is on).") &&
             !strstr(g_rep_text, "pick Linkle") && !*g_rep_fail;
    char on_text[400];
    snprintf(on_text, sizeof on_text, "%s", g_rep_text);
    run_line(cmd_iam, "iam");
    int on_now = strstr(g_rep_text, "You are linkle") && strstr(g_rep_text, "her body is on");
    g_fake_linkle = 0;
    run_line(cmd_iam, "iam linkel");
    int off = !strcmp(g_rep_iam, "linkle") && strstr(g_rep_text, "Your friends see you as Linkle") &&
              strstr(g_rep_text, "pick Linkle in Breathalyzer") && strstr(g_rep_text, "restarts once") &&
              !strstr(g_rep_text, "her body is on") && !*g_rep_fail;
    g_fake_linkle = -1;  // (no deploy known: the same words as off)
    run_line(cmd_iam, "iam");
    int unknown = strstr(g_rep_text, "pick Linkle in Breathalyzer") != 0;
    g_iam_linkle = was;
    check("iam", "linkle_body_words", on && on_now && off && unknown, "on %d (%s) now %d off %d (%s) unknown %d", on,
          on_text, on_now, off, g_rep_text, unknown);
}

static void check_iam(void) {
    g_fake_api.reply_str = rep_str, g_fake_api.reply_int = rep_int, g_fake_api.reply_num = rep_num;
    g_fake_api.reply_bool = rep_bool, g_fake_api.reply_text = rep_text, g_fake_api.reply_fail = rep_fail;
    lock();
    memset(&g_iam, 0, sizeof g_iam);
    unlock();
    // before any client: `iam` says to start it; a word is kept as typed (the client checks it when it starts)
    run_line(cmd_iam, "iam");
    check("iam", "no_list_says_start", strstr(g_rep_text, "Start the multiplayer client") != 0 && !*g_rep_fail,
          "text %s fail %s", g_rep_text, g_rep_fail);
    run_line(cmd_iam, "iam Zelda");
    check("iam", "no_list_kept", !strcmp(g_rep_iam, "zelda") && g_rep_seq == 1 && !*g_rep_fail, "iam %s seq %lld",
          g_rep_iam, (long long)g_rep_seq);
    check("iam", "no_list_says_kept", strstr(g_rep_text, "Kept: zelda") && !strstr(g_rep_text, "a few seconds"),
          "text %s", g_rep_text);  // (no client yet: never "your friends see it")
    // the client's list, and your current pick (no `seen`: the typed word is cleared)
    run_line(cmd_looks, "ghost looks link/hero,linkle,zelda/princess,goron/daruk,bokoblin/boko zelda");
    check("iam", "list_taken", g_rep_looks == 5 && g_iam.n == 5 && !strcmp(g_iam.current, "zelda") && !*g_rep_fail
          && !*g_iam.want, "looks %lld n %d current %s want %s fail %s", (long long)g_rep_looks, g_iam.n,
          g_iam.current, g_iam.want, g_rep_fail);
    run_line(cmd_iam, "iam DARUK");
    check("iam", "alias_any_case", !strcmp(g_rep_iam, "goron") && g_rep_seq == 2, "iam %s seq %lld", g_rep_iam,
          (long long)g_rep_seq);
    check("iam", "no_client_running_says_so", strstr(g_rep_text, "when it runs") && !strstr(g_rep_text, "a few seconds"),
          "text %s", g_rep_text);  // (a list, but no `ghost` ask lately: no promise)
    run_line(cmd_iam, "iam ganon");
    check("iam", "unknown_refused", strstr(g_rep_fail, "no ganon") && strstr(g_rep_fail, "link, linkle, zelda, goron, "
          "bokoblin") && g_iam.seq == 2 && !strcmp(g_iam.want, "goron"), "fail %s seq %d want %s", g_rep_fail,
          g_iam.seq, g_iam.want);
    run_line(cmd_iam, "iam next");
    int next1 = !strcmp(g_rep_iam, "bokoblin");
    run_line(cmd_iam, "iam next");
    int wrap = !strcmp(g_rep_iam, "link");
    run_line(cmd_iam, "iam back");
    check("iam", "next_back_wrap", next1 && wrap && !strcmp(g_rep_iam, "bokoblin") && g_iam.seq == 5,
          "next %d wrap %d back %s seq %d", next1, wrap, g_rep_iam, g_iam.seq);
    run_line(cmd_iam, "iam");
    check("iam", "lists_names", strstr(g_rep_text, "You are bokoblin") && strstr(g_rep_text, "link, linkle, zelda"),
          "text %s", g_rep_text);
    // bad lists are refused whole; the old list stays
    const char *bad[] = {"ghost looks link,,zelda", "ghost looks LINK", "ghost looks a/b/", "ghost looks a,",
                         "ghost looks -a", "ghost looks zelda/aaaaaaaaaaaaaaaaaaaaaaaaa", "ghost looks a b/c"};
    int refused = 0;
    for (int i = 0; i < (int)(sizeof bad / sizeof *bad); i++) {
        run_line(cmd_looks, bad[i]);
        refused += *g_rep_fail != 0;
    }
    check("iam", "bad_lists_refused", refused == (int)(sizeof bad / sizeof *bad) && g_iam.n == 5, "refused %d of %d, n %d",
          refused, (int)(sizeof bad / sizeof *bad), g_iam.n);
    // words that can't be ids
    run_line(cmd_iam, "iam zel%da");
    int bad1 = *g_rep_fail != 0;
    run_line(cmd_iam, "iam aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    int bad2 = *g_rep_fail != 0;
    run_line(cmd_iam, "iam two words");
    check("iam", "bad_words_refused", bad1 && bad2 && *g_rep_fail && g_iam.seq == 5, "%d %d seq %d", bad1, bad2,
          g_iam.seq);
    // the `ghost` reply's part: the client takes iam_seq and iam from it
    g_rep_has_iam = 0, g_rep_seq = -1, g_rep_iam[0] = 0;
    iam_report((SwModReply *)(void *)g_rep_text);
    check("iam", "report", g_rep_has_iam && g_rep_seq == 5 && !strcmp(g_rep_iam, "bokoblin") && g_rep_looks == 5,
          "has %d seq %lld iam %s looks %lld", g_rep_has_iam, (long long)g_rep_seq, g_rep_iam, (long long)g_rep_looks);
    // the client just asked (`ghost`): now a pick says the friends see it soon
    run_line(cmd_iam, "iam zelda");
    check("iam", "client_running_says_soon", strstr(g_rep_text, "a few seconds") && g_iam.seq == 6, "text %s seq %d",
          g_rep_text, g_iam.seq);
    // `ghost looks <list> current seen`: the typed word is cleared only when the client saw the newest one, so an
    // old `iam` never wins over a newer pick, and a word typed since is kept for the client
    const char *five = "ghost looks link/hero,linkle,zelda/princess,goron/daruk,bokoblin/boko";
    char line[256];
    snprintf(line, sizeof line, "%s goron 5", five);
    run_line(cmd_looks, line);
    int kept_newer = !strcmp(g_iam.want, "zelda") && !strcmp(g_iam.current, "goron") && !*g_rep_fail;
    snprintf(line, sizeof line, "%s goron 6", five);
    run_line(cmd_looks, line);
    int cleared = !*g_iam.want && !strcmp(g_iam.current, "goron") && !*g_rep_fail;
    run_line(cmd_iam, "iam");
    check("iam", "looks_seen_clears_old_word", kept_newer && cleared && strstr(g_rep_text, "You are goron"),
          "kept %d cleared %d text %s", kept_newer, cleared, g_rep_text);
    snprintf(line, sizeof line, "%s goron 6x", five);
    run_line(cmd_looks, line);
    int bad_seen = *g_rep_fail != 0;
    snprintf(line, sizeof line, "%s goron 6 7", five);
    run_line(cmd_looks, line);
    check("iam", "looks_bad_seen_refused", bad_seen && *g_rep_fail, "%d fail %s", bad_seen, g_rep_fail);
    // the full list the client sends (tools/mp/client.py iam_list: 11 characters with their aliases) fits
    run_line(cmd_looks, "ghost looks link/hero,linkle/linkel,zelda/princess,hylian/hateno/villager,sheikah/paya,"
             "gerudo/urbosa,goron/daruk,rito/revali,zora/mipha,bokoblin/boko,moblin/moriblin link");
    check("iam", "client_list_fits", g_rep_looks == 11 && !*g_rep_fail, "looks %lld fail %s", (long long)g_rep_looks,
          g_rep_fail);
    check_iam_linkle();
    lock();
    memset(&g_iam, 0, sizeof g_iam);
    unlock();
    setup();  // (the plain fakes again)
}

// ------------------------------------------------------------------ 0.9.0 the self puppet (puppet.c)
//
// A made-up Link (his matrix, a model with one unit whose flags' bit 0 is "drawn") in the items' made-up world: the
// puppet's request makes a fake actor with an animation holder (the pack's 33 names) and a model of its own. The fake
// MODEL_SHOW sets or clears the bit in the model's units; SET_TRANSFORM puts the actor where it is asked.

typedef struct {
    _Alignas(16) uint8_t model[0x100], unit[0x200];  // (0.9.1: room for a skeleton and an animation)
    uint64_t units[1], slot0[1];
} FakeModel;
static void fake_model(uint8_t *actor, FakeModel *fm, int drawn) {
    memset(fm, 0, sizeof *fm);
    W64(actor, ACTOR_MODEL, fm->model);
    W64(fm->model, 0, MODEL_VPTR);
    W32(fm->model, MODEL_NUNITS, 1);
    W64(fm->model, MODEL_UNITS, fm->units);
    fm->units[0] = P(fm->slot0), fm->slot0[0] = P(fm->unit);
    *(uint16_t *)(void *)(fm->unit + UNIT_FLAGS) = (uint16_t)(drawn ? 0x11 : 0x10);
}
static int fm_drawn(const FakeModel *fm) { return *(const uint16_t *)(const void *)(fm->unit + UNIT_FLAGS) & 1; }

static struct {
    _Alignas(16) uint8_t mem[0x1a00];  // (0.9.1: up to his ragdoll manager, LINK_RAGDOLL)
    FakeModel fm;
    _Alignas(16) uint8_t pi[0x100];
} g_pl;                                    // Link (the player) and the player info block
static FakeModel g_pp_fm;                  // the puppet's model
static _Alignas(16) uint8_t g_pp_holder[0x100];
static uint64_t g_pp_proc;                 // the puppet (the fake actor its request made)
static char g_pp_current[32];              // what it plays
static int g_pp_free = 1, g_pp_paused, g_pp_shows, g_pp_plays, g_pp_plays_missing;
static int pp_busy(uint64_t pi, uint64_t player, double t) {  // (0.9.2: "not free" is an event)
    (void)pi, (void)player, (void)t;
    return g_pp_free ? PB_FREE : PB_EVENT;
}
static int pp_paused(void) { return g_pp_paused; }
static int pp_in_world(void) { return 1; }
static float g_pp_stam = -1.0f;            // 0.9.5: Link's stamina (-1: unreadable); falls each frame while g_pp_drain
static int g_pp_drain;
static float pp_stamina(void) {
    if (g_pp_drain && g_pp_stam < 0) g_pp_stam = 3000.0f;
    if (g_pp_drain) g_pp_stam -= 5.0f;
    return g_pp_stam;
}
static const char *const PACK33[] = {"Wait", "Walk", "Run", "RunGear2", "CutS1", "CutS2", "CutS3", "CutSF", "CutDash",
                                     "CutJumpSt", "CutCharge", "CutS1_Lsword", "CutSF_Lsword", "CutS1_Spear",
                                     "CutS2_Spear", "CutSF_Spear", "BowCharge", "BowShoot", "GuardJust", "Jump", "Fall",
                                     "Land", "ParashawlGlide", "ClimbWait", "ClimbMove", "SwimWait", "SwimMove",
                                     "SwimDash", "SquatWait", "SquatMove", "DamageS", "Dead", "WeaponEquipOn"};
static int pp_has(const char *name) {
    for (int i = 0; i < N_OF(PACK33); i++)
        if (!strcmp(PACK33[i], name)) return 1;
    return 0;
}

static int pup_call(struct Cpu *c, uint64_t fn) {
    SwModRegs *r = SWMOD_REGS(c);
    if (fn == MODEL_SHOW) {
        g_pp_shows++;
        uint8_t *m = (uint8_t *)(uintptr_t)r->x[0];
        int n = *(int32_t *)(void *)(m + MODEL_NUNITS);
        uint64_t *arr = (uint64_t *)(uintptr_t)R64(m, MODEL_UNITS);
        for (int i = 0; i < n; i++) {
            uint16_t *f = (uint16_t *)(uintptr_t)(*(uint64_t *)(uintptr_t)arr[i] + UNIT_FLAGS);
            *f = (uint16_t)(r->x[1] & 1 ? *f | 1u << r->x[2] : *f & ~(1u << r->x[2]));
        }
        return 1;
    }
    if (fn == REQUEST_CREATE && !strcmp((const char *)(uintptr_t)r->x[1], "Dm663_Npc_Assasin_A")) {
        g_requests++;
        if (!strcmp(g_refuse_name, "Dm663_Npc_Assasin_A")) {
            r->x[0] = 0;
            return 1;
        }
        g_not_weapon = 1;  // (an actor of another class: its isA answers through the fake vtable)
        FakeItem *f = fake_item_new("Dm663_Npc_Assasin_A", r->x[3]);
        if (!f) {
            r->x[0] = 0;
            return 1;
        }
        ((Handle *)(uintptr_t)r->x[3])->unit = P(&f->unit);
        g_pp_proc = P(f->mem);
        W64(f->mem, ACTOR_AS, g_pp_holder);
        W64(g_pp_holder, AS_OWNER, f->mem);
        W32(g_pp_holder, AS_SLOTS, 3);
        fake_model(f->mem, &g_pp_fm, 1);
        g_pp_current[0] = 0;
        r->x[0] = 1;
        return 1;
    }
    if (fn == SET_TRANSFORM && r->x[0] == g_pp_proc && g_pp_proc) {
        const float *pos = (const float *)(uintptr_t)r->x[1];
        float *mtx = (float *)(uintptr_t)(g_pp_proc + ACTOR_MTX);
        mtx[3] = pos[0], mtx[7] = pos[1], mtx[11] = pos[2];
        return 1;
    }
    if (fn == AS_PLAY && g_pp_proc && rd64(r->x[0] + 8) == g_pp_proc) {
        const char *name = (const char *)(uintptr_t)r->x[1];
        g_pp_plays++;
        if (pp_has(name)) copy_name(g_pp_current, name);
        else g_pp_plays_missing++;
        return 1;
    }
    if (fn == AS_CURRENT && r->x[0] == P(g_pp_holder)) {
        copy_name(g_cur_text, g_pp_current);
        g_cur.vptr = 0, g_cur.str = P(g_cur_text);
        r->x[0] = P(&g_cur);
        return 1;
    }
    return 0;
}

static double g_pt = 9000.0;
static float g_px = 100.0f;
// secs of frames (30 a second): Link plays `anim` and moves along x at v m/s; the tick's puppet part and items, then
// the game's frame.
static void pup_run(const char *anim, float v, double secs) {
    struct Cpu *c = (struct Cpu *)(void *)g_cpu;
    copy_name(t_status.self.anim, anim);
    t_status.self.found = 1;
    for (double end = g_pt + secs; g_pt < end; g_pt += 1.0 / 30.0) {
        g_px += v / 30.0f;
        ((float *)(void *)(g_pl.mem + ACTOR_MTX))[3] = g_px;
        ((float *)(void *)(g_pl.pi + PI_POS))[0] = g_px;
        pup_tick(c, P(g_pl.pi), P(g_pl.mem), g_pt);
        Requests irq;
        uint64_t holder = pup_items_holder(&g_irq, &irq);
        items_tick(c, holder, &irq, g_pt);
        world_step();
    }
}
static void pup_want(const char *word) {
    lock();
    memset(g_iam.want, 0, sizeof g_iam.want);
    memcpy(g_iam.want, word, strlen(word));
    unlock();
}
static float pp_x(void) { return ((const float *)(uintptr_t)(g_pp_proc + ACTOR_MTX))[3]; }

// 0.9.5 pure rows (the round-4 game check): the back place's tweak and the arm flag per body, a whole arm for the
// overlay, an attack's set at once, the arrow's name.
static void check_puppet_095(void) {
    PupGrip g = *pup_grip("link");
    const char *bone = NULL;
    float G0[12], G1[12];
    pup_grip_place(&g, pup_body("link"), 0, 1, &bone, G0);
    g.back[0] = 0.02f, g.back[1] = -0.03f, g.back[2] = 0.05f;
    pup_grip_place(&g, pup_body("link"), 0, 1, &bone, G1);
    int moved = !strcmp(bone, "Spine_2") && __builtin_fabsf(G1[3] - G0[3] - 0.02f) < 1e-6f &&
                __builtin_fabsf(G1[7] - G0[7] + 0.03f) < 1e-6f && __builtin_fabsf(G1[11] - G0[11] - 0.05f) < 1e-6f &&
                G1[0] == 1 && G1[5] == 1 && G1[10] == 1 && G1[1] == 0 && G1[2] == 0;  // (not turned: Link's Pod_A rests
                                                                                   // as his Spine_2, as every body's)
    float back_dx = G1[3] - G0[3], back_dy = G1[7] - G0[7], back_dz = G1[11] - G0[11];
    pup_grip_place(&g, pup_body("link"), 0, 0, &bone, G1);
    int hand_same = !strcmp(bone, "Weapon_R") && G1[3] == 0 && G1[7] == 0 && G1[11] == 0;
    int arms = pup_grip("sheikah")->arm && pup_grip("gerudo")->arm && !pup_grip("zelda")->arm && !pup_grip("goron")->arm &&
               !pup_grip("bokoblin")->arm;
    check("puppet", "back_tweak", moved && hand_same && arms, "moved %d (%.3f %.3f %.3f) hand %d arms %d", moved,
          back_dx, back_dy, back_dz, hand_same, arms);
    static const char nm[9][32] = {"Spine_2", "Clavicle_L", "Arm_1_L", "Wrist_L", "Clavicle_R", "Arm_1_R", "Wrist_R",
                                   "Finger_A_1_R", "Head"};
    static const int16_t par[9] = {-1, 0, 1, 2, 0, 4, 5, 6, 0};
    uint8_t sel[9];
    int nr = pup_ovl_pick(nm, par, 9, OVL_ARM_R, sel), r_ok = nr == 4 && sel[4] && sel[5] && sel[6] && sel[7] &&
                                                             !sel[0] && !sel[1] && !sel[8];
    int nl = pup_ovl_pick(nm, par, 9, OVL_ARM_L, sel), l_ok = nl == 3 && sel[1] && sel[2] && sel[3] && !sel[4];
    int nb = pup_ovl_pick(nm, par, 9, OVL_ARM_R | OVL_GUARD, sel);
    check("puppet", "arm_overlay", r_ok && l_ok && nb == 7, "right %d (%d) left %d (%d) both %d", r_ok, nr, l_ok, nl, nb);
    check("puppet", "attack_morph", pup_as_morph("CutS1") == 0.0f && pup_as_morph("CutDash_Spear") == 0.0f &&
          pup_as_morph("Walk") == -1.0f && pup_as_morph("BowCharge") == -1.0f && pup_as_morph(NULL) == -1.0f, "CutS1 %.0f",
          pup_as_morph("CutS1"));
    check("puppet", "arrow_name", pup_is_arrow("NormalArrow") && pup_is_arrow("BombArrow_A") &&
          pup_is_arrow("AncientArrow") && !pup_is_arrow("Weapon_Bow_001") && !pup_is_arrow(NULL), "names");
}

// 0.9.3 pure rows: the held copies' place per body, the kept names, the guard, the hands' turns, death keeps the
// costume, the ground clamp's easing, Daruk's ball through a sprint dip.
static float t93_rot_err(const float a[9], const float b[9]) {
    float d = 0;
    for (int i = 0; i < 9; i++) d = __builtin_fmaxf(d, __builtin_fabsf(a[i] - b[i]));
    return d;
}
static void t93_rot(float ax, float ay, float az, float R[9]) {  // (Rz Ry Rx, radians)
    float q[4] = {ax, ay, az, 1.0f};
    pup_rest_rot(q, 1, R);
}
static void check_puppet_093(void) {
    // grip and back: Link's hand bone as it is, Daruk's moved to his finger roots (mirrored on the left), the back at
    // Link's Pod_A for Link and on Daruk's own back; a body with no entry: Link's grip
    const char *bone = NULL;
    float G[12];
    pup_grip_place(pup_grip("link"), pup_body("link"), 0, 0, &bone, G);
    int link_hand = !strcmp(bone, "Weapon_R") && G[0] == 1 && G[5] == 1 && G[10] == 1 && G[3] == 0 && G[7] == 0 &&
                    G[11] == 0;
    pup_grip_place(pup_grip("link"), pup_body("link"), 0, 1, &bone, G);
    int link_back = !strcmp(bone, "Spine_2") && __builtin_fabsf(G[3] - 0.189f) < 1e-3f &&
                    __builtin_fabsf(G[7] + 0.122f) < 1e-3f && __builtin_fabsf(G[11] + 0.077f) < 1e-3f;
    const PupGrip *gg = pup_grip("goron");
    pup_grip_place(gg, pup_body("goron"), 1, 0, &bone, G);
    int daruk_left = !strcmp(bone, "Weapon_L") && __builtin_fabsf(G[3] + 0.24f) < 1e-4f &&
                     __builtin_fabsf(G[7] + 0.025f) < 1e-4f && gg->scale > 1.5f;
    pup_grip_place(gg, pup_body("goron"), 0, 1, &bone, G);
    int daruk_back = !strcmp(bone, "Spine_2") && G[7] < -0.9f && G[3] > 0.4f;
    check("puppet", "grip_place", link_hand && link_back && daruk_left && daruk_back &&
          pup_grip("hylian") == &PUP_GRIPS[0] && pup_grip("zelda")->scale == 1.0f,
          "link hand %d back %d, daruk left %d back %d (%.2f %.2f %.2f)", link_hand, link_back, daruk_left, daruk_back,
          G[3], G[7], G[11]);
    // the names kept through a short loss of his equipment read; a new name at once; gone after PUP_ITEM_KEEP_S
    PupKeep k;
    memset(&k, 0, sizeof k);
    pup_keep_step(&k, "Weapon_Sword_001", 0, 10.0);
    pup_keep_step(&k, "", -1, 10.3);
    int kept = !strcmp(k.name, "Weapon_Sword_001");
    pup_keep_step(&k, "Weapon_Sword_002", 1, 10.31);
    int switched = !strcmp(k.name, "Weapon_Sword_002") && k.back == 1;
    pup_keep_step(&k, "", -1, 10.31 + PUP_ITEM_KEEP_S + 0.01);
    check("puppet", "items_kept", kept && switched && !*k.name, "kept %d switched %d then '%s'", kept, switched, k.name);
    // his guard: the attention stance or a parry with the shield in his hand
    int guard = pup_is_guard("MoveAttention", "", 1) && pup_is_guard("Move", "WaitAttentionUpper", 1) &&
                pup_is_guard("GuardJust", "", 1) && pup_is_guard("SquatAtnMove", "", 1) &&
                !pup_is_guard("MoveAttention", "", 0) && !pup_is_guard("Move", "", 1) && !pup_is_guard("Wait", "MoveUpper", 1);
    check("puppet", "guard_detect", guard, "guard %d", guard);
    // the hands' bones: a fist's fingers, the guard's left arm from Clavicle_L down
    static const char nm[8][32] = {"Spine_2", "Clavicle_L", "Arm_1_L", "Wrist_L", "Finger_A_1_L", "Finger_B_1_R",
                                   "Weapon_L", "Head"};
    static const int16_t par[8] = {-1, 0, 1, 2, 3, 0, 3, 0};
    uint8_t sel[8];
    int nr = pup_ovl_pick(nm, par, 8, OVL_FIST_R, sel), r_ok = nr == 1 && sel[5];
    int ng = pup_ovl_pick(nm, par, 8, OVL_GUARD, sel), g_ok = ng == 5 && sel[1] && sel[2] && sel[3] && sel[4] && sel[6] &&
                                                              !sel[0] && !sel[5] && !sel[7];
    // the local turn: the same rests -> his own local; other rests -> the world turn the ragdoll's retarget gives
    float Lp[9], Lw[9], L[9], C[9], I[9] = {1, 0, 0, 0, 1, 0, 0, 0, 1};
    t93_rot(0.3f, -0.7f, 1.1f, Lp), t93_rot(-0.4f, 0.2f, 0.9f, Lw);
    pup_r_tmul(Lp, Lw, L);
    pup_ovl_local(L, I, I, I, I, C);
    float same = t93_rot_err(C, L);
    float rlp[9], rl[9], rcp[9], rc[9], cpw[9], cw[9], want[9], tmp[9];
    t93_rot(0.1f, 0.2f, 0.3f, rlp), t93_rot(0.5f, -0.2f, 0.1f, rl), t93_rot(-0.3f, 0.4f, 0.2f, rcp);
    t93_rot(0.2f, 0.1f, -0.6f, rc);
    pup_ovl_local(L, rlp, rl, rcp, rc, C);
    pup_r_multt(Lp, rlp, tmp), pup_r_mul(tmp, rcp, cpw);   // (its parent posed as the ragdoll poses it)
    pup_r_mul(cpw, C, cw);
    pup_r_multt(Lw, rl, tmp), pup_r_mul(tmp, rc, want);    // (R_link R_link_rest^T R_its_rest)
    float other = t93_rot_err(cw, want);
    check("puppet", "hands_overlay", r_ok && g_ok && same < 1e-5f && other < 1e-4f,
          "fist R %d (%d), guard %d (%d), same rests %.6f, other rests %.6f", r_ok, nr, g_ok, ng, same, other);
    // death: an event or his body gone keeps the costume while it follows his ragdoll or he plays a death clip
    int death = pup_busy_keep(PB_EVENT, RD_NONE, "DeadRagdoll") == PB_FREE &&
                pup_busy_keep(PB_BODY, RD_POSE, "Wait") == PB_FREE && pup_busy_keep(PB_EVENT, RD_NONE, "Wait") == PB_EVENT &&
                pup_busy_keep(PB_WORLD, RD_POSE, "Dead") == PB_WORLD &&
                pup_busy_keep(PB_EVENT, RD_NONE, "SwimDead") == PB_FREE && pup_busy_keep(PB_FREE, RD_NONE, "") == PB_FREE;
    check("puppet", "death_keeps_costume", death, "death %d", death);
    // the clamp: at once when new; eased up within a few frames; never past its range
    float a = pup_clamp_step(0.0f, 0.3f, 0.0f), b = 0.0f;
    for (int f = 0; f < 6; f++) b = pup_clamp_step(b, 0.3f, 1.0f / 30.0f);
    float one = pup_clamp_step(0.0f, 0.3f, 1.0f / 30.0f), cap = pup_clamp_step(0.0f, 5.0f, 0.0f);
    check("puppet", "clamp_step", a == 0.3f && b > 0.29f && b <= 0.3f && one > 0.0f && one <= PUP_CLAMP_MPS / 30.0f + 1e-6f &&
          cap == PUP_CLAMP_MAX, "at once %.2f, 6 frames %.3f, one %.3f, cap %.2f", a, b, one, cap);
    // Daruk's ball through a sprint dip (0.9.5: over a drop): kept while it rolls and Link was in his sprint less than
    // PUP_SPRINT_LEAVE_S ago; he is in it while it is picked by his speed, or while he Moves and his stamina fell lately,
    // whatever his speed (never a swim)
    int hold = pup_sprint_hold(1, 0.1) && pup_sprint_hold(1, PUP_SPRINT_LEAVE_S - 0.01) &&
               !pup_sprint_hold(1, PUP_SPRINT_LEAVE_S + 0.05) && !pup_sprint_hold(0, 0.1) && !pup_sprint_hold(2, 0.1) &&
               !pup_sprint_hold(1, -1.0);
    int in = pup_in_sprint(1, "Jump", -1.0) && pup_in_sprint(0, "Move", 0.1) && !pup_in_sprint(0, "Move", PUP_DRAIN_S + 0.1) &&
             !pup_in_sprint(0, "Move", -1.0) && !pup_in_sprint(0, "Jump", 0.1) && !pup_in_sprint(0, "SwimDash", 0.1);
    check("puppet", "ball_dip_hold", hold && in, "hold %d in sprint %d", hold, in);
    check_puppet_095();
}

static void check_puppet_items_grip(void);

// 0.9.5 in the made-up world: Link's nocked arrow (an actor named ...Arrow in his equipment slot 3) is hidden while the
// costume shows, drawn again once it leaves the slot (shot) and when Link shows (a horse).
static struct {
    _Alignas(16) uint8_t mem[0x600];
    FakeModel fm;
    FakeLinkData ld;
    char name[16];
} g_arrow;
static void check_puppet_arrow(void) {
    memset(&g_arrow, 0, sizeof g_arrow);
    snprintf(g_arrow.name, sizeof g_arrow.name, "NormalArrow");
    W64(g_arrow.mem, PROC_NAME, g_arrow.name);
    fake_model(g_arrow.mem, &g_arrow.fm, 1);
    ld_new(&g_arrow.ld, P(g_arrow.mem));
    uint64_t slot3 = ACTOR_EQUIP + EQUIP_LINKS + 3 * EQUIP_LINK_SIZE;
    W64(g_pl.mem, ACTOR_EQUIP, EQUIP_VPTR), W64(g_pl.mem, ACTOR_EQUIP + EQUIP_OWNER, g_pl.mem);
    W64(g_pl.mem, slot3, &g_arrow.ld), W32(g_pl.mem, slot3 + 8, g_arrow.ld.id);
    pup_run("BowCharge", 0, 0.3);
    int hidden = t_pup.shown && !fm_drawn(&g_arrow.fm) && t_pup.arrow_hidden && !strcmp(t_pup.arrow, "NormalArrow");
    W64(g_pl.mem, slot3, 0);  // (shot: it leaves the slot)
    pup_run("BowShoot", 0, 0.1);
    int shot = fm_drawn(&g_arrow.fm) && !t_pup.arrow_hidden;
    W64(g_pl.mem, slot3, &g_arrow.ld);  // (the next one nocked) ... then a horse: Link shows, and the arrow with him
    pup_run("BowCharge", 0, 0.2);
    int again = !fm_drawn(&g_arrow.fm);
    pup_run("HorseRide", 5.0f, 0.5);
    int link_shows = fm_drawn(&g_arrow.fm) && !t_pup.shown;
    check("puppet", "arrow_hidden", hidden && shot && again && link_shows, "hidden %d shot %d again %d link shows %d",
          hidden, shot, again, link_shows);
    memset(g_pl.mem + ACTOR_EQUIP, 0, 0xa0);
    pup_run("Wait", 0, 0.5);
}

static void check_puppet(void) {
    // the pure parts: picks, ids, sentences
    Missing *m = calloc(1, sizeof *m);
    if (!m) return;
    int sp = 0;
    const char *walk = pup_pick("Move", 1.6f, AN_WALK, &sp, m), *run = pup_pick("Move", 5.4f, AN_RUN, &sp, m);
    const char *dash = pup_pick("Move", 8.0f, AN_RUN, &sp, m), *still = pup_pick("Move", 6.5f, AN_RUN, &sp, m);
    const char *slow = pup_pick("Move", 6.0f, AN_RUN, &sp, m);
    check("puppet", "move_by_speed", !strcmp(walk, "Walk") && !strcmp(run, "Run") && !strcmp(dash, "RunGear2") &&
          !strcmp(still, "RunGear2") && !strcmp(slow, "Run"), "%s %s %s %s %s", walk, run, dash, still, slow);
    for (int k = 0; k < PUP_STRIKES; k++) missing_strike(m, "ClimbJump"), missing_strike(m, "CutS9");
    for (int k = 0; k < PUP_STRIKES; k++) missing_strike(m, "SwimJump"), missing_strike(m, "HoldWait");
    const char *cj = pup_pick("ClimbJump", 1.0f, AN_WAIT, &sp, m), *cut = pup_pick("CutS9", 0, AN_WAIT, &sp, m);
    const char *sw = pup_pick("SwimJump", 3.0f, AN_WAIT, &sp, m), *hold = pup_pick("HoldWait", 0, AN_WAIT, &sp, m);
    const char *horse = pup_pick("HorseRide", 5.0f, AN_RUN, &sp, m), *own = pup_pick("ParashawlGlide", 9, AN_RUN, &sp, m);
    check("puppet", "near_names", !strcmp(cj, "ClimbMove") && !strcmp(cut, "CutS1") && !strcmp(sw, "SwimDash") &&
          !strcmp(hold, "Wait") && !horse && !strcmp(own, "ParashawlGlide"), "%s %s %s %s %s %s", cj, cut, sw, hold,
          horse ? horse : "(Link)", own);
    for (int k = 0; k < PUP_STRIKES; k++) missing_strike(m, "ClimbMove"), missing_strike(m, "ClimbWait");
    const char *noclimb = pup_pick("ClimbJump", 1.0f, AN_WAIT, &sp, m);
    for (int k = 0; k < PUP_STRIKES; k++) missing_strike(m, "RunGear2");
    sp = 0;
    const char *nodash = pup_pick("Move", 8.0f, AN_RUN, &sp, m);
    // 0.9.3: a family the pack lacks is never Link (the Moblin swimming showed Link): it stands or walks; a glide falls
    for (int k = 0; k < PUP_STRIKES; k++)
        missing_strike(m, "SwimDash"), missing_strike(m, "SwimMove"), missing_strike(m, "SwimWait"),
            missing_strike(m, "ParashawlGlide");
    const char *noswim = pup_pick("SwimMove", 1.0f, AN_WALK, &sp, m), *noglide = pup_pick("ParashawlGlide", 9, AN_RUN, &sp, m);
    check("puppet", "missing_clip_no_link", noclimb && !strcmp(noclimb, "Wait") && !strcmp(nodash, "Run") && noswim &&
          !strcmp(noswim, "Walk") && noglide && !strcmp(noglide, "Fall"), "climb %s dash %s swim %s glide %s",
          noclimb ? noclimb : "(Link)", nodash, noswim ? noswim : "(Link)", noglide ? noglide : "(Link)");
    // 0.9.4: the held weapon's swing: a two-hander's / spear's name when known (the monsters' extra ones only on theirs),
    // never for a one-hander, a non-attack or a name the pack lacks
    char wb[40], wb2[40], wb3[40], wb4[40], wb5[40];
    const char *two = pup_weapon_pick("CutS1", "Weapon_Lsword_001", 0, m, wb, sizeof wb);
    const char *dash_link = pup_weapon_pick("CutDash", "Weapon_Spear_001", 0, m, wb2, sizeof wb2);
    const char *dash_own = pup_weapon_pick("CutDash", "Weapon_Spear_001", 1, m, wb3, sizeof wb3);
    const char *one = pup_weapon_pick("CutS1", "Weapon_Sword_001", 1, m, wb4, sizeof wb4);
    const char *wwalk = pup_weapon_pick("Walk", "Weapon_Spear_001", 1, m, wb5, sizeof wb5);
    for (int k = 0; k < PUP_STRIKES; k++) missing_strike(m, "CutSF_Spear");
    char wb6[40];
    const char *lacks = pup_weapon_pick("CutSF", "Weapon_Spear_001", 1, m, wb6, sizeof wb6);
    check("puppet", "weapon_swing", !strcmp(two, "CutS1_Lsword") && !strcmp(dash_link, "CutDash") &&
          !strcmp(dash_own, "CutDash_Spear") && !strcmp(one, "CutS1") && !strcmp(wwalk, "Walk") && !strcmp(lacks, "CutSF") &&
          !pup_weapon_pick(NULL, "Weapon_Spear_001", 1, m, wb6, sizeof wb6),
          "%s %s %s %s %s %s", two, dash_link, dash_own, one, wwalk, lacks);
    free(m);
    check_puppet_093();
    check("puppet", "ids", !strcmp(pup_id("daruk"), "goron") && !strcmp(pup_id("goron"), "goron") &&
          !*pup_id("link") && !*pup_id("linkle") && !*pup_id("linkel") && !pup_slot("linkle") &&
          !*pup_id("ganon") && pup_slot("zora") && !strcmp(pup_slot("goron")->actor, "Dm663_Npc_Assasin_A") &&
          !strcmp(pup_id("custom"), "custom") && !strcmp(pup_slot("custom")->actor, "Dm_Npc_Handmaid_A"),
          "daruk %s", pup_id("daruk"));
    PupStatus ps;
    memset(&ps, 0, sizeof ps);
    char line[300];
    pup_sentence(&ps, "daruk", line, sizeof line);
    int picked = strstr(line, "Daruk in a moment") != 0;
    ps.state = PS_FAILED;
    snprintf(ps.name, sizeof ps.name, "Daruk"), snprintf(ps.playas, sizeof ps.playas, "daruk");
    snprintf(ps.why, sizeof ps.why, "Daruk did not load (x): you stay Link");
    pup_sentence(&ps, NULL, line, sizeof line);
    check("puppet", "sentences", picked && strstr(line, "Restart the game to play as Daruk") &&
          strstr(line, "charsel.py playas daruk"), "%s", line);
    // 0.9.8: no Linkle costume: the puppet says nothing for her (iam.c answers: her body, or how to get it)
    char lk[300];
    pup_sentence(&ps, "linkle", lk, sizeof lk);
    check("puppet", "linkle_words", !*lk, "%s", lk);

    if (!g_items_world_ok) {
        check("puppet", "world", 1, "SKIP: the made-up globals' page is taken in this process");
        return;
    }
    // a made-up Link at 100 5 200, drawn
    items_fresh(1);
    memset(&g_pl, 0, sizeof g_pl);
    float lm[12] = {1, 0, 0, 100, 0, 1, 0, 5, 0, 0, 1, 200};
    memcpy(g_pl.mem + ACTOR_MTX, lm, sizeof lm);
    fake_model(g_pl.mem, &g_pl.fm, 1);
    W64(g_pl.pi, PI_PLAYER, g_pl.mem);
    float pp[3] = {100, 5, 200};
    memcpy(g_pl.pi + PI_POS, pp, sizeof pp);
    g_fake_vtable[2] = FAKE_ISA;
    g_pup_busy = pp_busy, g_pup_paused = pp_paused, g_pup_check_files = 0, g_pup_stamina = pp_stamina;
    g_pp_free = 1, g_pp_paused = 0, g_px = 100.0f;
    memset(&t_status.self, 0, sizeof t_status.self);
    t_status.wanted = 0, t_status.state = ST_IDLE;
    g_nanim_names = 0;

    // `iam daruk` (no client list: the word as typed): it spawns, Link hides once the puppet plays, the puppet shows
    pup_want("daruk");
    pup_run("Wait", 0, 1.0);
    check("puppet", "spawns_and_swaps", t_pup.state == PS_LIVE && g_requests == 1 && !fm_drawn(&g_pl.fm) &&
          fm_drawn(&g_pp_fm) && t_pup.shown && !strcmp(g_pp_current, "Wait") && t_pup.hides == 1,
          "state %s requests %d link drawn %d puppet drawn %d shown %d plays %s hides %lld why %s",
          PS_NAMES[t_pup.state], g_requests, fm_drawn(&g_pl.fm), fm_drawn(&g_pp_fm), t_pup.shown, g_pp_current,
          (long long)t_pup.hides, t_pup.why);
    check("puppet", "at_link", g_pp_proc && pp_x() == g_px, "puppet x %.2f, Link %.2f", g_pp_proc ? pp_x() : -1, g_px);
    // 0.9.6: its physics (a cloth pack's) is never moved by the physics call unless asked (`ghost puppet physmtx`); a
    // jump of 50 m in one frame is a warp (counted); `warp` moves it once after one, `on` every frame
    {
        static _Alignas(16) uint8_t phys[0x40];
        float px0 = g_px;
        W64((void *)(uintptr_t)g_pp_proc, ACTOR_PHYSICS, phys);
        int64_t m0 = t_pup.phys_moves, w0 = t_pup.warps;
        pup_run("Wait", 0, 0.3);
        int off_still = t_pup.phys_moves == m0 && t_pup.physics && t_pup.warps == w0;
        g_px += 50.0f;
        pup_run("Wait", 0, 0.3);
        int counted = t_pup.warps == w0 + 1 && t_pup.phys_moves == m0 && pp_x() == g_px;
        lock();
        g_pup_req.physmtx = PM_WARP;
        unlock();
        g_px += 50.0f;
        pup_run("Wait", 0, 0.3);
        int once = t_pup.warps == w0 + 2 && t_pup.phys_moves == m0 + 1;
        lock();
        g_pup_req.physmtx = PM_ON;
        unlock();
        pup_run("Wait", 0, 0.2);
        int every = t_pup.phys_moves >= m0 + 6;
        lock();
        g_pup_req.physmtx = PM_OFF;
        unlock();
        W64((void *)(uintptr_t)g_pp_proc, ACTOR_PHYSICS, 0);
        g_px = px0;
        pup_run("Wait", 0, 0.2);
        check("puppet", "physmtx_warp", off_still && counted && once && every && g_pup_req.physmtx == PM_OFF,
              "off %d (physics %d), warp counted %d (warps +%lld), once %d, every frame %d (moves +%lld)", off_still,
              t_pup.physics, counted, (long long)(t_pup.warps - w0), once, every, (long long)(t_pup.phys_moves - m0));
    }
    // Daruk's sprint: Link's Move at 8 m/s is RunGear2 (his rolling ball); walking is Walk
    pup_run("Move", 8.0f, 1.5);
    int sprint_ok = !strcmp(g_pp_current, "RunGear2");
    char sprint[32];
    copy_name(sprint, g_pp_current);
    pup_run("Move", 1.6f, 1.5);
    check("puppet", "sprint_and_walk", sprint_ok && !strcmp(g_pp_current, "Walk") && t_pup.shown, "sprint %s, then %s",
          sprint, g_pp_current);
    // the game shows Link again by itself: hidden again, counted
    *(uint16_t *)(void *)(g_pl.fm.unit + UNIT_FLAGS) |= 1;
    pup_run("Move", 1.6f, 0.2);
    check("puppet", "rehide", !fm_drawn(&g_pl.fm) && t_pup.rehides == 1, "drawn %d rehides %lld", fm_drawn(&g_pl.fm),
          (long long)t_pup.rehides);
    // a climb jump the pack lacks: ClimbMove after the misses, the puppet stays shown
    pup_run("ClimbJump", 1.0f, 1.0);
    check("puppet", "climb_near_name", !strcmp(g_pp_current, "ClimbMove") && t_pup.shown && !fm_drawn(&g_pl.fm),
          "plays %s shown %d", g_pp_current, t_pup.shown);
    // a horse: Link shows, the puppet hides; back on foot: the puppet again
    pup_run("HorseRide", 5.0f, 0.5);
    int horse_link = fm_drawn(&g_pl.fm) && !fm_drawn(&g_pp_fm) && !t_pup.shown;
    pup_run("Wait", 0, 0.5);
    check("puppet", "horse_shows_link", horse_link && t_pup.shown && !fm_drawn(&g_pl.fm), "horse %d then shown %d",
          horse_link, t_pup.shown);
    // a cutscene (no real position): Link; free again: the puppet after the settle time
    g_pp_free = 0;
    pup_run("Wait", 0, 0.5);
    int cut_link = fm_drawn(&g_pl.fm) && !fm_drawn(&g_pp_fm);
    g_pp_free = 1;
    pup_run("Wait", 0, 0.1);
    int settling = fm_drawn(&g_pl.fm);
    pup_run("Wait", 0, 0.5);
    check("puppet", "cutscene_shows_link", cut_link && settling && !fm_drawn(&g_pl.fm) && fm_drawn(&g_pp_fm),
          "cutscene %d settling %d now link %d puppet %d", cut_link, settling, fm_drawn(&g_pl.fm), fm_drawn(&g_pp_fm));
    // 0.9.2: a state that shows Link for less than PUP_BUSY_S (a fast slide's frame of doubt): the puppet stays
    int64_t states0 = t_pup.link_states;
    g_pp_free = 0;
    pup_run("Wait", 0, 0.1);
    int stayed = t_pup.shown && !fm_drawn(&g_pl.fm) && t_pup.link_states == states0;
    g_pp_free = 1;
    pup_run("Wait", 0, 0.3);
    check("puppet", "short_doubt_keeps", stayed && t_pup.shown && t_pup.busy == PB_FREE,
          "stayed %d shown %d link states +%lld", stayed, t_pup.shown, (long long)(t_pup.link_states - states0));
    // the pause menu keeps what was shown
    g_pp_paused = 1, g_pp_free = 0;
    pup_run("Wait", 0, 0.3);
    int kept = !fm_drawn(&g_pl.fm) && t_pup.shown;
    g_pp_paused = 0, g_pp_free = 1;
    pup_run("Wait", 0, 0.5);
    check("puppet", "pause_keeps", kept, "link drawn %d shown %d", fm_drawn(&g_pl.fm), t_pup.shown);
    // Link's sword in his hand, his shield on his back: a copy of the sword for the puppet's hand and (0.9.3) one of the
    // shield for its back (Link's own are hidden with him); the sword put away: on its back too; with a friend's ghost
    // wanted: not the puppet's
    t_status.self.equip_ok = 1;
    snprintf(t_status.self.weapon, 64, "Weapon_Sword_001"), snprintf(t_status.self.shield, 64, "Weapon_Shield_001");
    t_status.self.back[0] = 0, t_status.self.back[1] = 1, t_status.self.has_rel[1] = 1;
    Requests irq;
    uint64_t holder = pup_items_holder(&g_irq, &irq);
    int to_puppet = holder == g_pp_proc && !strcmp(irq.item_names[0], "Weapon_Sword_001") && irq.items_on;
    int on_back = !strcmp(irq.item_names[1], "Weapon_Shield_001") && irq.items_back && irq.items_mask == 2 &&
                  !irq.item_has_rel[1];
    t_status.self.back[0] = 1;
    pup_items_holder(&g_irq, &irq);
    int sheathed = !strcmp(irq.item_names[0], "Weapon_Sword_001") && irq.items_mask == 3;
    t_status.wanted = 1;
    uint64_t holder2 = pup_items_holder(&g_irq, &irq);
    t_status.wanted = 0;
    t_status.self.equip_ok = 0, t_status.self.weapon[0] = t_status.self.shield[0] = 0;
    t_status.self.back[0] = t_status.self.back[1] = t_status.self.has_rel[1] = 0;
    check("puppet", "items_to_puppet", to_puppet && on_back && sheathed && holder2 != g_pp_proc,
          "holder %d, shield on its back %d, sheathed %d, with a friend's ghost %d", holder == g_pp_proc, on_back,
          sheathed, holder2 == g_pp_proc);
    check_puppet_items_grip();
    check_puppet_arrow();
    // a stage change (a new spawn heap): forgotten without a call, spawned again
    static _Alignas(16) uint8_t heap2[0x40];
    uint64_t holder_obj = rd64(HEAP_HOLDER_PTR), heap1 = rd64(holder_obj + HEAP_OFF);
    int dels = g_deletes, reqs = g_requests;
    W64((void *)(uintptr_t)holder_obj, HEAP_OFF, heap2);
    pup_run("Wait", 0, 0.05);
    int forgot = t_pup.state == PS_WAIT && g_deletes == dels && t_pup.drops == 1;
    pup_run("Wait", 0, PUP_RETRY_S + 1.0);
    check("puppet", "stage_change", forgot && t_pup.state == PS_LIVE && g_requests == reqs + 1 && t_pup.shown,
          "forgot %d state %s requests +%d", forgot, PS_NAMES[t_pup.state], g_requests - reqs);
    W64((void *)(uintptr_t)holder_obj, HEAP_OFF, heap1);
    pup_run("Wait", 0, PUP_RETRY_S + 1.0);
    // `iam link`: the puppet goes (deleted), Link shows
    dels = g_deletes;
    pup_want("link");
    pup_run("Wait", 0, 0.2);
    check("puppet", "iam_link_back", t_pup.state == PS_OFF && fm_drawn(&g_pl.fm) && g_deletes > dels && !t_pup_proc,
          "state %s link drawn %d deletes +%d", PS_NAMES[t_pup.state], fm_drawn(&g_pl.fm), g_deletes - dels);
    // `iam self off` with a pick: no puppet
    pup_set_self(0);
    pup_want("daruk");
    pup_run("Wait", 0, 0.5);
    int off_ok = t_pup.state == PS_OFF && fm_drawn(&g_pl.fm);
    pup_set_self(1);
    pup_want("link");
    pup_run("Wait", 0, 0.1);
    check("puppet", "self_off", off_ok, "state %s", PS_NAMES[t_pup.state]);
    // 0.9.5: Game Over (an event) for a minute: the costume the game removed then is asked for only once the game is
    // free again; no try is spent meanwhile, and it comes back by itself
    pup_want("daruk");
    pup_run("Wait", 0, 1.0);
    int was_live = t_pup.state == PS_LIVE;
    int64_t waits0 = t_pup.busy_waits;
    int reqs_go = g_requests;
    g_pp_free = 0;
    FakeLinkData *pld = ld_of(g_pp_proc);
    if (pld) ld_kill(pld);  // (the game removes it during Game Over)
    pup_run("Dead", 0, 60.0);
    int waited = t_pup.state == PS_WAIT && t_pup.tries == 0 && t_pup.busy_waits == waits0 + 1 && g_requests == reqs_go &&
                 strstr(t_pup.why, "waiting");
    g_pp_free = 1;
    pup_run("Wait", 0, PUP_RETRY_S + 1.0);
    check("puppet", "busy_no_tries", was_live && pld && waited && t_pup.state == PS_LIVE && g_requests == reqs_go + 1 &&
          t_pup.shown, "live %d, waited %d (tries %d waits +%lld requests +%d why %s), then %s", was_live, waited,
          t_pup.tries, (long long)(t_pup.busy_waits - waits0), g_requests - reqs_go, t_pup.why, PS_NAMES[t_pup.state]);
    // ... and one given up by the game's doing after it spawned comes back on a new stage (a Continue), fresh tries
    static _Alignas(16) uint8_t heap3[0x40];
    uint64_t hobj = rd64(HEAP_HOLDER_PTR), heap_was = rd64(hobj + HEAP_OFF);
    pup_remove((struct Cpu *)(void *)g_cpu, P(g_pl.mem), g_pt);  // (as five removals in a row while free would end)
    t_pup.state = PS_FAILED, t_pup.tries = PUP_TRIES, t_pup_soft_fail = 1;
    t_pup_fail_heap = heap_was, t_pup_fail_player = P(g_pl.mem);
    pup_run("Wait", 0, 0.2);
    int stays = t_pup.state == PS_FAILED;
    int64_t back0 = t_pup.comebacks;
    W64((void *)(uintptr_t)hobj, HEAP_OFF, heap3);
    pup_run("Wait", 0, PUP_RETRY_S + 1.0);
    int came = t_pup.state == PS_LIVE && t_pup.comebacks == back0 + 1 && t_pup.tries == 0;
    W64((void *)(uintptr_t)hobj, HEAP_OFF, heap_was);
    pup_run("Wait", 0, PUP_RETRY_S + 1.0);
    check("puppet", "gameover_comeback", stays && came && t_pup.state == PS_LIVE, "stays %d came back %d (%s)", stays,
          came, PS_NAMES[t_pup.state]);
    pup_want("link");
    pup_run("Wait", 0, 0.2);
    // a pack the game refuses: given up after PUP_TRIES, Link stays, `iam` says to restart as Daruk
    const char *was = g_refuse_name;
    g_refuse_name = "Dm663_Npc_Assasin_A";
    int req = g_requests;
    pup_want("daruk");
    pup_run("Wait", 0, PUP_TRIES * PUP_RETRY_S + 1.0);
    lock();
    PupStatus fin = g_pup;
    unlock();
    pup_sentence(&fin, NULL, line, sizeof line);
    check("puppet", "refused_gives_up", fin.state == PS_FAILED && g_requests - req == PUP_TRIES && fm_drawn(&g_pl.fm) &&
          strstr(line, "Restart the game to play as Daruk"), "state %s requests %d line %s", PS_NAMES[fin.state],
          g_requests - req, line);
    // (never spawned: no coming back by itself on a new stage, as Linkle with no size entries)
    static _Alignas(16) uint8_t heap4[0x40];
    uint64_t hobj4 = rd64(HEAP_HOLDER_PTR), heap_was4 = rd64(hobj4 + HEAP_OFF);
    W64((void *)(uintptr_t)hobj4, HEAP_OFF, heap4);
    pup_run("Wait", 0, PUP_RETRY_S + 1.0);
    int never = t_pup.state == PS_FAILED && g_requests - req == PUP_TRIES;
    W64((void *)(uintptr_t)hobj4, HEAP_OFF, heap_was4);
    check("puppet", "refused_stays_failed", never, "state %s requests %d", PS_NAMES[t_pup.state], g_requests - req);
    g_refuse_name = was;
    pup_want("link");
    pup_run("Wait", 0, 0.1);
    // another mod holds the frame job (the tick never ran) while a save is loaded: `iam` says so plainly
    g_pup_in_world = pp_in_world;
    lock();
    int64_t ticks_was = g_status.ticks;
    g_status.ticks = 0;
    unlock();
    char no_tick[300], ticking[300];
    pup_iam_text("daruk", no_tick, sizeof no_tick);
    lock();
    g_status.ticks = 5;
    unlock();
    pup_iam_text("daruk", ticking, sizeof ticking);
    lock();
    g_status.ticks = ticks_was;
    unlock();
    g_pup_in_world = pup_in_world_real;
    check("puppet", "no_tick_says_why", strstr(no_tick, "isn't running") && strstr(no_tick, "Infinite Stamina") &&
          !strstr(ticking, "isn't running"), "no tick: %s | ticking: %s", no_tick, ticking);
    g_pup_busy = pup_busy_real, g_pup_paused = pup_paused_real, g_pup_check_files = 1, g_pup_stamina = pup_stamina_real;
    lock();
    memset(&g_iam, 0, sizeof g_iam);
    unlock();
}

// ------------------------------------------------------------------ 0.9.1 the costume's ragdoll (puppet.c)
//
// Made-up skeletons: Link's (10 bones) and a costume's (the same names in another order, twice as long, plus an extra
// "Belly" bone). Pure rows: the detection, the order, the pose (each named bone turns as Link's, keeps its own
// length, the extra bone follows), the ground under a lying Link (flat and on a slope), the lift that puts Daruk's
// boxes on it and never under, "up again". World rows: Link's ragdoll manager's bit set -> the costume's animation
// paused, its bones written as Link's, raised onto the ground; he gets up -> its animation runs again, its clip again;
// `ragdoll clip`: its lying clip; `ragdoll off`: nothing.

#define RDT_MAX 12
static const char *const RDT_LINK[] = {"Root", "Skl_Root", "Spine_1", "Spine_2", "Neck",
                                       "Head", "Leg_1_L", "Leg_2_L", "Leg_1_R", "Leg_2_R"};
static const int16_t RDT_LINK_PAR[] = {-1, 0, 1, 2, 3, 4, 1, 6, 1, 8};
static const char *const RDT_PUP[] = {"Root", "Skl_Root", "Leg_1_R", "Leg_2_R", "Spine_1", "Belly",
                                      "Spine_2", "Neck", "Head", "Leg_1_L", "Leg_2_L"};
static const int16_t RDT_PUP_PAR[] = {-1, 0, 1, 2, 1, 4, 4, 6, 7, 1, 9};

typedef struct {
    _Alignas(16) uint8_t sk[0x40], res[0x40], anim[0x40], dict[0x20 + 0x10 * RDT_MAX], bones[0x50 * RDT_MAX];
    char names[RDT_MAX][36];
    _Alignas(16) float locals[RDT_MAX][24], worlds[RDT_MAX][16];
} FakeSkel;

// Give a fake model's unit a skeleton (n bones: names, parents; locals: no turn, `len` m along y from the parent) and
// an animation whose "runs" bit is set.
static void fake_skel(FakeModel *fm, FakeSkel *fs, int n, const char *const *names, const int16_t *parent, float len) {
    memset(fs, 0, sizeof *fs);
    W64(fm->unit, 0, UNIT_VPTR_A);
    W64(fm->unit, UNIT_SKEL, fs->sk);
    W64(fm->unit, UNIT_ANIM, fs->anim);
    W64(fs->anim, 0, ANIM_VPTR);
    W64(fs->anim, ANIM_UNIT, fm->unit);
    fs->anim[ANIM_FLAGS] = ANIM_RUNS;
    W64(fs->sk, 0, fs->res);
    W64(fs->sk, SKEL_LOCAL, fs->locals);
    W64(fs->sk, SKEL_WORLD, fs->worlds);
    W32(fs->res, 0, FSKL_MAGIC);
    W64(fs->res, RES_DICT, fs->dict);
    W64(fs->res, RES_BONES, fs->bones);
    *(uint16_t *)(void *)(fs->res + RES_NBONES) = (uint16_t)n;
    for (int b = 0; b < n; b++) {
        size_t l = strlen(names[b]);
        fs->names[b][0] = (char)l;
        memcpy(fs->names[b] + 2, names[b], l + 1);
        W64(fs->dict, 0x20 + 0x10 * b, fs->names[b]);
        *(uint16_t *)(void *)(fs->bones + 0x50 * b + RES_BONE_INDEX) = (uint16_t)b;
        *(int16_t *)(void *)(fs->bones + 0x50 * b + RES_BONE_PARENT) = parent[b];
        float *lc = fs->locals[b];
        lc[4] = lc[5] = lc[6] = 1.0f;                             // (scale)
        lc[8] = 1.0f, lc[13] = 1.0f, lc[18] = 1.0f;               // (columns: no turn)
        lc[21] = parent[b] >= 1 && strcmp(names[b], "Belly") ? len : 0.0f;  // (translation y)
        if (!strcmp(names[b], "Belly")) lc[22] = 0.5f;            // (the extra bone: 0.5 m forward of its parent)
    }
}

// 3x3 rows: a turn of deg about z, then x (r = Rz * Rx).
static void rdt_rot(float zdeg, float xdeg, float r[9]) {
    float a = zdeg * 0.0174532925f, b = xdeg * 0.0174532925f;
    float cz = __builtin_cosf(a), sz = __builtin_sinf(a), cx = __builtin_cosf(b), sx = __builtin_sinf(b);
    float m[9] = {cz, -sz * cx, sz * sx, sz, cz * cx, -cz * sx, 0, sx, cx};
    memcpy(r, m, sizeof m);
}

// Link's bones placed (3x4 rows per bone, into w; Link's order): his hips at hip, his body turned by zdeg about z (0
// standing, -90 lying on his back, head to +x), his spine above the hips bent xdeg more; spine bones 0.1 m apart, legs
// 0.4 m. Root stays at his feet (root).
static void rdt_link_pose(const float hip[3], const float root[3], float zdeg, float xdeg, float w[][12]) {
    float body[9], bent[9];
    rdt_rot(zdeg, 0, body), rdt_rot(zdeg, xdeg, bent);
    for (int b = 0; b < 10; b++) {
        const float *R = b >= 2 && b <= 5 ? bent : body;
        float off[3] = {0, 0, 0};
        if (b >= 2 && b <= 5) off[1] = 0.1f * (float)(b - 1);                       // spine: up
        if (b >= 6) off[0] = b <= 7 ? 0.1f : -0.1f, off[1] = b % 2 ? -0.8f : -0.4f;  // legs: down
        for (int r = 0; r < 3; r++) {
            for (int k = 0; k < 3; k++) w[b][r * 4 + k] = b ? R[r * 3 + k] : (r == k ? 1.0f : 0.0f);
            w[b][r * 4 + 3] = b ? hip[r] + body[r * 3] * off[0] + body[r * 3 + 1] * off[1] + body[r * 3 + 2] * off[2]
                                : root[r];
        }
    }
}
static void rdt_set_worlds(FakeSkel *fs, float w[][12], int n) {  // (3x4 rows -> the skeleton's columns)
    for (int b = 0; b < n; b++)
        for (int r = 0; r < 3; r++)
            for (int k = 0; k < 4; k++) fs->worlds[b][k * 4 + r] = w[b][r * 4 + k];
}
static void rdt_skel(PupSkel *s, int n, const char *const *names, const int16_t *parent, const char *const *link) {
    memset(s, 0, sizeof *s);
    s->n = n, s->hip = s->head = -1;
    for (int k = 0; k < PUP_NBODY_BONES; k++) s->keyidx[k] = -1;
    for (int k = 0; k < PUP_LIMP_N; k++) s->limb[k] = -1;
    for (int b = 0; b < n; b++) {
        s->parent[b] = parent[b], s->map[b] = -1;
        for (int k = 0; k < PUP_LIMP_N; k++)
            if (!strcmp(names[b], PUP_LIMBS[k])) s->limb[k] = (int16_t)b;
        for (int j = 0; link && j < 10; j++)
            if (!strcmp(names[b], link[j])) s->map[b] = (int16_t)j, s->nmap++;
        for (int k = 0; k < PUP_NBODY_BONES; k++)
            if (!strcmp(names[b], PUP_BODY_BONES[k])) s->keyidx[k] = (int16_t)b;
        if (!strcmp(names[b], "Skl_Root")) s->hip = b;
        if (!strcmp(names[b], "Head")) s->head = b;
    }
    pup_rd_order(s->parent, n, s->order);
}
static float rdt_rdiff(const float a[9], const float b[9]) {
    float d = 0;
    for (int i = 0; i < 9; i++) d = __builtin_fmaxf(d, __builtin_fabsf(a[i] - b[i]));
    return d;
}
static float rdt_dist(const float a[3], const float b[3]) {
    float d[3] = {a[0] - b[0], a[1] - b[1], a[2] - b[2]};
    return pup_len3(d);
}

static FakeSkel g_rd_lfs, g_rd_pfs;
static _Alignas(16) uint8_t g_rd_mgr[0x200];

// 0.9.2: the angle (deg) between unit vectors a and b.
static float rdt_angle(const float a[3], const float b[3]) {
    float c = pup_dot3(a, b);
    c = c > 1.0f ? 1.0f : c < -1.0f ? -1.0f : c;
    return __builtin_acosf(c) * 57.29578f;
}
// 0.9.2: skeleton s's rest: no turn but `turned` (turned zdeg about z at rest), its bones `len` m along y from their
// parent (the extra "Belly" 0.5 m forward).
static void rdt_rest(PupSkel *s, const char *const *names, const int16_t *parent, int n, float len, const char *turned,
                     float zdeg) {
    static float sc[RDT_MAX][3], rot[RDT_MAX][4], tr[RDT_MAX][3];
    for (int b = 0; b < n; b++) {
        sc[b][0] = sc[b][1] = sc[b][2] = 1.0f;
        rot[b][0] = rot[b][1] = 0.0f, rot[b][2] = turned && !strcmp(names[b], turned) ? zdeg * 0.0174532925f : 0.0f;
        rot[b][3] = 1.0f;
        tr[b][0] = tr[b][1] = tr[b][2] = 0.0f;
        if (parent[b] >= 1 && strcmp(names[b], "Belly")) tr[b][1] = len;
        if (!strcmp(names[b], "Belly")) tr[b][2] = 0.5f;
    }
    pup_rest_set(s, (const float (*)[3])sc, (const float (*)[4])rot, (const float (*)[3])tr, 1);
}

// 0.9.2 pure rows: the retarget rest to rest, the motion's slope, the loose limbs, the ball's shape and place.
static void rdt_pure_092(const PupSkel *ls0, const PupSkel *ps0, float (*wl)[12], float (*cur)[12], const float base[12],
                         const float hip[3]) {
    static PupSkel ls, ps;
    static float out[11][12];
    static PupX wp[11], wq[11];
    ls = *ls0, ps = *ps0;
    // the costume's Spine_1 rests turned 90 degrees about z (its spine and head with it): it takes Link's turn FROM
    // HIS REST on its own rest; its extra bone (Belly) takes its rest
    rdt_rest(&ls, RDT_LINK, RDT_LINK_PAR, 10, 0.1f, NULL, 0);
    rdt_rest(&ps, RDT_PUP, RDT_PUP_PAR, 11, 0.2f, "Spine_1", 90.0f);
    pup_rd_solve(&ps, &ls, (const float (*)[12])wl, (const float (*)[12])cur, base, hip, out, wp, NULL, 0.0f, 0.0f);
    float bent[9], body[9], rz[9], want_sp[9], want_leg[9];
    rdt_rot(-90, 30, bent), rdt_rot(-90, 0, body), rdt_rot(90, 0, rz);
    pup_r_mul(bent, rz, want_sp);
    memcpy(want_leg, body, sizeof want_leg);
    float sp_off = rdt_rdiff(wp[4].R, want_sp), head_off = rdt_rdiff(wp[8].R, want_sp), leg_off = rdt_rdiff(wp[9].R, want_leg);
    float belly_local_off = __builtin_fabsf(out[5][11] - 0.5f) + __builtin_fabsf(out[5][0] - 1.0f);
    float spine_len = rdt_dist(wp[6].t, wp[4].t);
    check("puppet", "rd_rest_retarget", ls.rest && ps.rest && sp_off < 1e-4f && head_off < 1e-4f && leg_off < 1e-4f &&
          belly_local_off < 1e-5f && __builtin_fabsf(spine_len - 0.2f) < 1e-4f && rdt_dist(wp[ps.hip].t, hip) < 1e-4f,
          "rest %d %d spine off %.5f head %.5f leg %.5f belly %.5f spine length %.4f", ls.rest, ps.rest, sp_off, head_off,
          leg_off, belly_local_off, spine_len);
    // a resource with no usable rest (a scale of 0, a quaternion of no length): no rest, the 0.9.1 way
    static float zs[2][3], zr[2][4], zt[2][3];
    static PupSkel two;
    memset(&two, 0, sizeof two);
    two.n = 2, two.parent[0] = -1, two.parent[1] = 0, two.order[0] = 0, two.order[1] = 1;
    pup_rest_set(&two, (const float (*)[3])zs, (const float (*)[4])zr, (const float (*)[3])zt, 0);
    int no_rest = !two.rest;
    zs[0][0] = zs[0][1] = zs[0][2] = zs[1][0] = zs[1][1] = zs[1][2] = 1.0f;
    pup_rest_set(&two, (const float (*)[3])zs, (const float (*)[4])zr, (const float (*)[3])zt, 0);
    check("puppet", "rd_rest_refused", no_rest && !two.rest, "scale 0: %d; quaternion 0: %d", !no_rest, two.rest);

    // the ground's slope from his motion: down a hill along +x (5 m/s across, 2.5 down: tan 26.6); slow: none; a fall
    // (steeper than PUP_RD_SLOPE): at most that
    float g1[2], g2[2], g3[2], v1[3] = {5, -2.5f, 0}, v2[3] = {0.5f, -1, 0}, v3[3] = {3, -10, 4};
    float w1 = pup_motion_slope(v1, g1), w2 = pup_motion_slope(v2, g2), w3 = pup_motion_slope(v3, g3);
    check("puppet", "rd_motion_slope", w1 == 1.0f && __builtin_fabsf(g1[0] + 0.5f) < 1e-5f && g1[1] == 0.0f && w2 == 0.0f &&
          __builtin_fabsf(__builtin_sqrtf(g3[0] * g3[0] + g3[1] * g3[1]) - PUP_RD_SLOPE) < 1e-4f && g3[0] < 0 && g3[1] < 0 &&
          w3 == 1.0f, "down a hill %.3f %.3f (%.2f), slow %.2f, a fall %.3f %.3f", g1[0], g1[1], w1, w2, g3[0], g3[1]);

    // loose limbs: his left leg swings 60 degrees at once; limp 0: exactly his; limp 1: the costume's leg lags, never
    // more than PUP_LIMP_MAX, and comes to his within 2 s while he lies still
    static float wl2[10][12];
    memcpy(wl2, wl, sizeof wl2);
    float r60[9], tmp[9];
    rdt_rot(60, 0, r60);
    for (int b = 6; b <= 7; b++) {  // (Leg_1_L, Leg_2_L)
        float R[9] = {wl2[b][0], wl2[b][1], wl2[b][2], wl2[b][4], wl2[b][5], wl2[b][6], wl2[b][8], wl2[b][9], wl2[b][10]};
        pup_r_mul(r60, R, tmp);
        for (int r = 0; r < 3; r++) wl2[b][r * 4] = tmp[r * 3], wl2[b][r * 4 + 1] = tmp[r * 3 + 1], wl2[b][r * 4 + 2] = tmp[r * 3 + 2];
    }
    static PupLimp lp;
    memset(&lp, 0, sizeof lp);
    const PupSkel *pn = ps0;  // (no rest: the 0.9.1 turns, the leg along its own y)
    float y[3] = {0, 1, 0}, dl[3], dc[3], R60body[9];
    pup_r_mul(r60, body, R60body);
    pup_r_apply(R60body, y, dl);
    pup_rd_solve(pn, ls0, (const float (*)[12])wl2, (const float (*)[12])cur, base, hip, out, wq, &lp, 0.0f, 1.0f / 30);
    pup_r_apply(wq[9].R, y, dc);
    float exact = rdt_angle(dl, dc);
    memset(&lp, 0, sizeof lp);
    pup_rd_solve(pn, ls0, (const float (*)[12])wl, (const float (*)[12])cur, base, hip, out, wq, &lp, 1.0f, 1.0f / 30);
    pup_rd_solve(pn, ls0, (const float (*)[12])wl2, (const float (*)[12])cur, base, hip, out, wq, &lp, 1.0f, 1.0f / 30);
    pup_r_apply(wq[9].R, y, dc);
    float lag = rdt_angle(dl, dc), most = lag;
    int bad = 0;
    for (int f = 0; f < 60; f++) {
        pup_rd_solve(pn, ls0, (const float (*)[12])wl2, (const float (*)[12])cur, base, hip, out, wq, &lp, 1.0f, 1.0f / 30);
        pup_r_apply(wq[9].R, y, dc);
        float a = rdt_angle(dl, dc);
        if (!(a == a)) bad = 1;
        most = a > most ? a : most;
    }
    float settled = rdt_angle(dl, dc), knee = rdt_dist(wq[10].t, wq[9].t);
    check("puppet", "rd_limp", exact < 0.01f && lag > 10.0f && most <= PUP_LIMP_MAX * 57.3f + 0.5f && settled < 1.0f &&
          !bad && __builtin_fabsf(knee - 0.2f) < 1e-4f, "limp 0 off %.3f deg; limp 1: lag %.1f, most %.1f, after 2 s %.2f, "
          "knee %.4f", exact, lag, most, settled, knee);

    // the ball: a body of 20 cm cubes all at one point (a sphere of 0.1 m) 0.5 m up in its own frame; placed at a turn,
    // its lowest point is on the ground, its middle over the ground point; on a slope too
    static PupBody cubes;
    cubes.id = "cubes";
    for (int k = 0; k < PUP_NBODY_BONES; k++) {
        cubes.has[k] = 1;
        for (int i = 0; i < 6; i++) cubes.box[k][i] = (short)(i % 2 ? 10 : -10);
    }
    static PupX xb[11], xw[11];
    for (int b = 0; b < 11; b++) {
        memset(&xb[b], 0, sizeof xb[b]);
        xb[b].R[0] = xb[b].R[4] = xb[b].R[8] = xb[b].k = 1.0f;
        xb[b].t[1] = 0.5f;
    }
    float mid[3], r = 0;
    int shaped = pup_ball_shape(xb, ps0, &cubes, 1.0f, mid, &r);
    float worst = 0, f1[2] = {0, 1}, gp[3] = {10, 2, 20}, up[3] = {0, 1, 0}, om[12];
    for (int k = 0; k < 7; k++) {
        float th = 0.9f * (float)k, rad = pup_ball_place(xb, ps0, &cubes, 1.0f, mid, f1, th, gp, up, om);
        for (int b = 0; b < 11; b++) {  // (its bones in the world: om times them)
            float R[9] = {om[0], om[1], om[2], om[4], om[5], om[6], om[8], om[9], om[10]};
            pup_r_mul(R, xb[b].R, xw[b].R);
            pup_r_apply(R, xb[b].t, xw[b].t);
            xw[b].t[0] += om[3], xw[b].t[1] += om[7], xw[b].t[2] += om[11], xw[b].k = 1.0f;
        }
        float plane[3] = {0, 0, gp[1]}, d = pup_rd_drop(xw, ps0, &cubes, 1.0f, plane);
        float cy = om[7] + om[4] * mid[0] + om[5] * mid[1] + om[6] * mid[2];
        worst = __builtin_fmaxf(worst, __builtin_fabsf(d) + __builtin_fabsf(rad - 0.1f) + __builtin_fabsf(cy - 2.1f));
    }
    float sl[3] = {-0.3f, 1, 0}, ls_ = pup_len3(sl);
    for (int k = 0; k < 3; k++) sl[k] /= ls_;
    float rad_s = pup_ball_place(xb, ps0, &cubes, 1.0f, mid, f1, 0.4f, gp, sl, om);
    float cy_s = om[7] + om[4] * mid[0] + om[5] * mid[1] + om[6] * mid[2];
    check("puppet", "ball_shape_place", shaped && __builtin_fabsf(r - 0.1f) < 1e-4f && __builtin_fabsf(mid[1] - 0.5f) < 1e-5f &&
          worst < 1e-4f && __builtin_fabsf(cy_s - (2.0f + rad_s / sl[1])) < 1e-4f && pup_ball_id("goron") &&
          !pup_ball_id("zelda"), "radius %.4f middle y %.3f, off %.5f; on a slope middle %.3f", r, mid[1], worst, cy_s);
}

// 0.9.6 pure rows: the bones each body's own physics moves (PUP_CLOTH, from charsel.py bodies) are found by exact name;
// a costume's bone on that list (and not named as Link's) is never written by the tick's pose nor published for the
// after-step write, the bones named as Link's still are; a jump of more than PUP_WARP_M in one frame is a warp.
static void check_puppet_cloth(void) {
    const char *g = pup_cloth_list("gerudo"), *z = pup_cloth_list("zora");
    int look = g && pup_cloth_has(g, "Skirt_A_1") && pup_cloth_has(g, "Hair_B_10") && pup_cloth_has(g, "Hair_B_1") &&
               !pup_cloth_has(g, "Hair_B") && !pup_cloth_has(g, "Skirt_A_") && !pup_cloth_has(g, "kirt_A_1") &&
               !pup_cloth_has(g, "Waist") && !pup_cloth_has(g, "") && !pup_cloth_has(g, NULL) && z &&
               pup_cloth_has(z, "Cloth_A_1") && !pup_cloth_list("link") && !pup_cloth_list("hylian") &&
               !pup_cloth_list("linkle") && !pup_cloth_list(NULL) && !pup_cloth_list("nobody");
    int bodies = 0, link_named = 0;   // (every body with cloth has a list; no list names one of the main bones)
    for (int i = 0; PUP_CLOTH[i].id; i++) {
        bodies += pup_body(PUP_CLOTH[i].id) != &PUP_BODIES[0];
        for (int k = 0; k < PUP_NBODY_BONES; k++) link_named += pup_cloth_has(PUP_CLOTH[i].bones, PUP_BODY_BONES[k]);
    }
    check("puppet", "cloth_lookup", look && bodies >= 8 && !link_named, "lookups %d, bodies %d, main bones listed %d",
          look, bodies, link_named);

    // a costume with its rest read: the made-up one with its extra bone named as one of Urbosa's skirt bones, and a
    // second one where that name is one of Link's (map >= 0: never the physics')
    static const char *const CL[] = {"Root", "Skl_Root", "Leg_1_R", "Leg_2_R", "Spine_1", "Skirt_A_1",
                                     "Spine_2", "Neck", "Head", "Leg_1_L", "Leg_2_L"};
    static const char *const CL_LINK[] = {"Root", "Skl_Root", "Spine_1", "Spine_2", "Neck",
                                          "Head", "Leg_1_L", "Leg_2_L", "Leg_1_R", "Skirt_A_1"};
    static PupSkel ps, p2;
    static char nm[11][32];
    rdt_skel(&ps, 11, CL, RDT_PUP_PAR, RDT_LINK);
    rdt_rest(&ps, CL, RDT_PUP_PAR, 11, 0.2f, NULL, 0);
    for (int b = 0; b < 11; b++) snprintf(nm[b], sizeof nm[b], "%s", CL[b]);
    int marked = pup_cloth_mark(&ps, (const char (*)[32])nm, "gerudo");
    int sets = ps.rest && ps.cloth[5] && !pup_rd_sets(&ps, 5) && pup_rd_sets(&ps, 4) && pup_rd_sets(&ps, 1) &&
               pup_rd_sets(&ps, 0);
    rdt_skel(&p2, 11, CL, RDT_PUP_PAR, CL_LINK);
    int not_link = pup_cloth_mark(&p2, (const char (*)[32])nm, "gerudo") == 0 &&
                   pup_cloth_mark(&ps, (const char (*)[32])nm, "zelda") == 0;
    pup_cloth_mark(&ps, (const char (*)[32])nm, "gerudo");
    // the tick's write onto a made-up skeleton: the skirt bone's local as it was, every other bone written
    static FakeSkel fs;
    static _Alignas(16) uint8_t unit[0x200];
    memset(&fs, 0, sizeof fs);
    W64(fs.sk, SKEL_LOCAL, fs.locals);
    for (int b = 0; b < 11; b++)
        for (int k = 0; k < 24; k++) fs.locals[b][k] = 7.0f;
    ps.sk = P(fs.sk), ps.unit = P(unit);
    static float out[11][12];
    for (int b = 0; b < 11; b++)
        for (int k = 0; k < 12; k++) out[b][k] = 1.0f;
    pup_rd_write(&ps, (const float (*)[12])out);
    int kept = 1, written = 1;
    for (int k = LOCAL_COLS / 4; k < 24; k++) kept &= fs.locals[5][k] == 7.0f;
    for (int b = 0; b < 11; b++)
        if (b != 5) written &= fs.locals[b][LOCAL_COLS / 4] == 1.0f;
    // ... and published for the after-step write without it
    pup_ov_publish(0, &ps, (const float (*)[12])out);
    const PupOvPose *pp = &g_pup_ov_pose[0][__atomic_load_n(&g_pup_ov_pub[0], __ATOMIC_ACQUIRE) % 3];
    int pub = pp->n == 10;
    for (int j = 0; j < pp->n; j++) pub &= pp->bone[j] != 5;
    pup_ov_clear();
    check("puppet", "cloth_left_alone", marked == 1 && sets && not_link && kept && written && pub,
          "marked %d, sets %d, not when named as Link's %d, its local kept %d, others written %d, published %d "
          "(%d bones)", marked, sets, not_link, kept, written, pub, pp->n);

    // a warp: none at first, 0.3 m a frame is moving, 7 m is a warp, then moving from there
    float last[3] = {0, 0, 0}, a0[3] = {10, 0, 0}, a1[3] = {10.3f, 0, 0}, a2[3] = {17.3f, 0, 0}, a3[3] = {17.5f, 0, 0};
    int ok = 0;
    int w0 = pup_warp_step(a0, last, &ok), w1 = pup_warp_step(a1, last, &ok), w2 = pup_warp_step(a2, last, &ok);
    int w3 = pup_warp_step(a3, last, &ok);
    check("puppet", "warp_step", !w0 && !w1 && w2 && !w3 && ok && last[0] == 17.5f, "warps %d %d %d %d", w0, w1, w2,
          w3);
}

// 0.9.2: the game's unit step, made up: the costume's clip goes into its locals (every bone turned 40 degrees about x,
// 0.3 m along y: a walk's frame), as the game did before each frame's world matrices while the tick's pose was lost.
static int g_rdt_clip_writes;
static void rdt_fake_step(struct Cpu *c) {
    uint8_t *unit = (uint8_t *)(uintptr_t)SWMOD_REGS(c)->x[0];
    if (unit != g_pp_fm.unit) return;
    g_rdt_clip_writes++;
    float r40[9];
    rdt_rot(0, 40, r40);
    for (int b = 0; b < 11; b++) {
        float *lc = g_rd_pfs.locals[b];
        for (int k = 0; k < 3; k++)
            for (int r = 0; r < 3; r++) lc[8 + k * 4 + r] = r40[r * 3 + k];
        lc[20] = 0, lc[21] = 0.3f, lc[22] = 0;
    }
}
// secs of frames: Link plays anim, moving vx m/s along x and vy m/s up (a slope: his place's height changes too).
static void pup_run_y(const char *anim, float vx, float vy, double secs) {
    float *lm = (float *)(void *)(g_pl.mem + ACTOR_MTX), *pp = (float *)(void *)(g_pl.pi + PI_POS);
    for (double end = g_pt + secs; g_pt < end;) {
        lm[7] += vy / 30.0f, pp[1] = lm[7];
        pup_run(anim, vx, 1.0 / 30.0 - 1e-6);
    }
}
static int rdt_rot_is(const float *lc, const float want[9]) {  // (a fake local's turn: columns from [8])
    float R[9] = {lc[8], lc[12], lc[16], lc[9], lc[13], lc[17], lc[10], lc[14], lc[18]};
    return rdt_rdiff(R, want) < 1e-4f;
}

// 0.9.2 world rows: the pose written again after the game's own step (no clip shows; a unit with no animation of its
// own, one of another class), the slide after a tumble followed on its slope, Daruk's sprint ball, no stale why.
// 0.9.3 in the made-up world: the puppet (an NPC-class costume: the game would put it on its pod) holds the copies
// on OUR follows: the sword on its Weapon_R, the shield on its Spine_2 (its back); a short loss of Link's equipment
// read keeps the same copy; the game putting the item's own follow back: ours again next frame; sheathed: the same
// copy moves to its back.
// 0.9.9: the game's step of a unit shows it again (as the game re-shows a hidden pod each frame, 2026-11-18)
static void pod_fake_reshow(struct Cpu *c) {
    uint64_t u = SWMOD_REGS(c)->x[0];
    *(uint16_t *)(uintptr_t)(u + UNIT_FLAGS) |= (uint16_t)(1u << PUP_BIT);
}
static void check_puppet_items_grip(void) {
    {
        static const char *const PPH[] = {"Root", "Weapon_R", "Weapon_L"};
        static const int16_t PPH_PAR[] = {-1, 0, 0};
        fake_skel(&g_pp_fm, &g_rd_pfs, 3, PPH, PPH_PAR, 0.1f);
        W64(g_pp_fm.unit, UNIT_ANIM, 0);
        t_items_ghost = 0;  // (its bones looked up again: it has hands now)
        // 0.9.5: an NPC's own equipment holder in it, every slot "on the back" (as the NPC's set-up writes it)
        uint8_t *hold = (uint8_t *)(uintptr_t)(g_pp_proc + PUP_NPC_HOLDER);
        W64(hold, 0, EQUIP_VPTR), W64(hold, EQUIP_OWNER, g_pp_proc);
        for (int k = 0; k < 3; k++) hold[EQUIP_LINKS + k * EQUIP_LINK_SIZE + EQUIP_BACK] = 1;
        int64_t hw0 = t_pup.holder_writes;
        t_status.self.equip_ok = 1;
        snprintf(t_status.self.weapon, 64, "Weapon_Sword_001"), snprintf(t_status.self.shield, 64, "Weapon_Shield_001");
        t_status.self.back[0] = 0, t_status.self.back[1] = 1;
        pup_run("Wait", 0, 2.0);
        uint8_t *hb = hold + EQUIP_LINKS + EQUIP_BACK;
        int in_hand = t_pup.holder == 1 && hb[0] == 0 && hb[EQUIP_LINK_SIZE] == 0 && hb[2 * EQUIP_LINK_SIZE] == 1 &&
                      t_pup.holder_writes == hw0 + 2;
        hb[0] = 1;  // (the game's NPC code says "on the back" again: in hand again next frame)
        pup_run("Wait", 0, 0.1);
        in_hand = in_hand && hb[0] == 0 && t_pup.holder_writes == hw0 + 3;
        const ItemStatus *o0 = &t_status.items.slot[0], *o1 = &t_status.items.slot[1];
        uint64_t sw = o0->actor, sh = o1->actor;
        uint64_t f0 = sw ? R64((void *)(uintptr_t)sw, ACTOR_FOLLOW) : 0, f1 = sh ? R64((void *)(uintptr_t)sh, ACTOR_FOLLOW) : 0;
        int ours0 = f0 && follow_is_ours(0, f0) && !strcmp((const char *)(uintptr_t)R64((void *)(uintptr_t)f0, FOLLOW_BONE), "Weapon_R");
        int ours1 = f1 && follow_is_ours(1, f1) && !strcmp((const char *)(uintptr_t)R64((void *)(uintptr_t)f1, FOLLOW_BONE), "Spine_2");
        int placed = ours0 && ours1 && !strcmp(o0->place, "grip") && !strcmp(o1->place, "back") && o0->state == IS_HELD;
        // 0.9.8: our copies' pods hidden (the game binds them to the NPC's own Pod_A, sideways): the drawn sword's
        // sheath (its pod link) and a quiver in the costume holder's slot 3; another item's name there left alone;
        // nothing hidden once the links are gone
        static struct {
            _Alignas(16) uint8_t mem[0x600];
            FakeModel fm;
            FakeLinkData ld;
            char name[24];
        } pods[3];
        static const char *const POD_NAMES[3] = {"Weapon_Sheath_001", "Weapon_Quiver_001", "Weapon_Sword_002"};
        for (int k = 0; k < 3; k++) {
            memset(&pods[k], 0, sizeof pods[k]);
            snprintf(pods[k].name, sizeof pods[k].name, "%s", POD_NAMES[k]);
            W64(pods[k].mem, PROC_NAME, pods[k].name);
            fake_model(pods[k].mem, &pods[k].fm, 1);
            ld_new(&pods[k].ld, P(pods[k].mem));
        }
        uint8_t *swm = (uint8_t *)(uintptr_t)sw;
        uint64_t s3 = EQUIP_LINKS + 3 * EQUIP_LINK_SIZE, s4 = EQUIP_LINKS + 4 * EQUIP_LINK_SIZE;
        W64(swm, ITEM_POD, &pods[0].ld), W32(swm, ITEM_POD + 8, pods[0].ld.id);
        W64(hold, s3, &pods[1].ld), W32(hold, s3 + 8, pods[1].ld.id);
        W64(hold, s4, &pods[2].ld), W32(hold, s4 + 8, pods[2].ld.id);
        int64_t sh0 = t_pup.sheath_hides;
        pup_run("Wait", 0, 0.2);
        int pods_hidden = !fm_drawn(&pods[0].fm) && !fm_drawn(&pods[1].fm) && fm_drawn(&pods[2].fm) &&
                          t_pup.sheaths == 2 && t_pup.sheath_hides == sh0 + 2 && pup_is_pod("Weapon_Lsheath_013") &&
                          pup_is_pod("Weapon_SpearSheath_001") && !pup_is_pod("Weapon_Sword_002") && !pup_is_pod(NULL);
        // 0.9.9: the game shows the hidden sheath again in its unit's step: hidden again right after it (the
        // after-step hide), no new show/hide call from the tick; another unit's re-show left alone
        struct Cpu *c = (struct Cpu *)(void *)g_cpu;
        SwModGuestFn step0 = g_orig_unit_step;
        g_orig_unit_step = pod_fake_reshow;
        int64_t uh0 = g_pup_pod_hides;
        SWMOD_REGS(c)->x[0] = P(pods[0].fm.unit);
        unit_step_wrapper(c);
        int after_step = !fm_drawn(&pods[0].fm) && g_pup_pod_hides == uh0 + 1;
        SWMOD_REGS(c)->x[0] = P(pods[2].fm.unit);
        unit_step_wrapper(c);
        after_step = after_step && fm_drawn(&pods[2].fm) && g_pup_pod_hides == uh0 + 1;
        pup_run("Wait", 0, 0.1);
        after_step = after_step && !fm_drawn(&pods[0].fm) && t_pup.sheath_hides == sh0 + 2;
        W64(swm, ITEM_POD, 0), W64(hold, s3, 0), W64(hold, s4, 0);
        pup_run("Wait", 0, 0.1);
        // links gone: the table is empty, the game's re-show stays
        SWMOD_REGS(c)->x[0] = P(pods[0].fm.unit);
        unit_step_wrapper(c);
        int freed = !g_pup_pod_any && fm_drawn(&pods[0].fm) && g_pup_pod_hides == uh0 + 1;
        g_orig_unit_step = step0;
        check("puppet", "sheath_after_step", after_step && freed, "hidden after the step %d (+%lld unit hides, "
              "+%lld tick hides), freed %d", after_step, (long long)(g_pup_pod_hides - uh0),
              (long long)(t_pup.sheath_hides - sh0), freed);
        check("puppet", "sheath_hidden", pods_hidden && t_pup.sheaths == 0 && !strcmp(t_pup.sheath, "Weapon_Quiver_001"),
              "sheath drawn %d quiver drawn %d other drawn %d, hidden %d (+%lld), after %d, last %s",
              fm_drawn(&pods[0].fm), fm_drawn(&pods[1].fm), fm_drawn(&pods[2].fm), t_pup.sheaths,
              (long long)(t_pup.sheath_hides - sh0), t_pup.sheaths, t_pup.sheath);
        t_status.self.equip_ok = 0;
        pup_run("Wait", 0, 0.2);
        t_status.self.equip_ok = 1;
        pup_run("Wait", 0, 0.3);
        int kept = o0->actor == sw && o0->state == IS_HELD;
        int64_t sets = o0->grip_sets;
        W64((void *)(uintptr_t)sw, ACTOR_FOLLOW, sw + ITEM_FOLLOW);
        pup_run("Wait", 0, 0.1);
        int again = R64((void *)(uintptr_t)sw, ACTOR_FOLLOW) == f0 && o0->grip_sets == sets + 1;
        t_status.self.back[0] = 1;
        pup_run("Wait", 0, 0.5);
        uint64_t fb = R64((void *)(uintptr_t)sw, ACTOR_FOLLOW);
        int sheathed_back = o0->actor == sw && !strcmp(o0->place, "back") && follow_is_ours(0, fb) &&
                            !strcmp((const char *)(uintptr_t)R64((void *)(uintptr_t)fb, FOLLOW_BONE), "Spine_2");
        check("puppet", "items_grip", placed && kept && again && sheathed_back,
              "placed %d (%s/%s, ours %d %d), kept %d, ours again %d, sheathed on its back %d (%s)", placed, o0->place,
              o1->place, ours0, ours1, kept, again, sheathed_back, o0->place);
        // 0.9.5: the slots holding our copies kept "in hand" (the item's AI never sheathes them over our follows: the
        // game's NPC sheath place showed in the round-4 check), the empty one left alone; a body with no such holder: 0
        int still_hand = hb[0] == 0;
        memset(hold, 0, 0xa0);
        pup_run("Wait", 0, 0.1);
        check("puppet", "holder_in_hand", in_hand && still_hand && t_pup.holder == 0,
              "in hand %d (bytes %d %d %d, writes +%lld), sheathed %d, no holder %d", in_hand, hb[0], hb[EQUIP_LINK_SIZE],
              hb[2 * EQUIP_LINK_SIZE], (long long)(t_pup.holder_writes - hw0), still_hand, t_pup.holder);
        t_status.self.equip_ok = 0, t_status.self.weapon[0] = t_status.self.shield[0] = 0;
        t_status.self.back[0] = t_status.self.back[1] = 0;
        pup_run("Wait", 0, 1.0);  // (gone after the keep time: deleted)
    }
}

static void rdt_world_092(float (*wl)[12], float (*wu)[12], const float hip_up[3], const float root[3]) {
    (void)hip_up, (void)root;
    struct Cpu *c = (struct Cpu *)(void *)g_cpu;
    float lying[9], bent[9], r40[9];
    rdt_rot(-90, 0, lying), rdt_rot(0, 30, bent), rdt_rot(0, 40, r40);
    pup_want("daruk");
    W32(g_rd_mgr, RD_FLAGS, 0);
    rdt_set_worlds(&g_rd_lfs, wu, 10);
    pup_run("Wait", 0, 1.0);
    fake_skel(&g_pp_fm, &g_rd_pfs, 11, RDT_PUP, RDT_PUP_PAR, 0.2f);  // (a new costume: its skeleton again)
    W64(g_pp_fm.unit, UNIT_ANIM, 0);  // (no animation of its own at +0x188: Paya's, the Hylian's units, 2026-11-13)
    g_orig_unit_step = rdt_fake_step;
    W32(g_rd_mgr, RD_FLAGS, RD_ON);
    rdt_set_worlds(&g_rd_lfs, wl, 10);
    pup_run("SlippingDown", 0, 0.3);
    int posed = t_pup.rd == RD_POSE && strstr(t_pup.rd_anim, "u0 none") && !*t_pup.rd_why;
    int64_t w0 = g_pup_ov_writes;
    g_rdt_clip_writes = 0;
    SWMOD_REGS(c)->x[0] = P(g_pp_fm.unit);
    unit_step_wrapper(c);  // (the clip, then the pose)
    int after = g_rdt_clip_writes == 1 && g_pup_ov_writes == w0 + 1 && rdt_rot_is(g_rd_pfs.locals[1], lying) &&
                rdt_rot_is(g_rd_pfs.locals[4], bent) && rdt_rot_is(g_rd_pfs.locals[5], r40) &&
                (g_pp_fm.unit[UNIT_DIRTY] & UNIT_LOCALS_NEW);
    SWMOD_REGS(c)->x[0] = P(g_pl.fm.unit);  // (another unit: untouched)
    unit_step_wrapper(c);
    int others = g_pup_ov_writes == w0 + 1;
    // up again: no more after-step pose; then an animation of another class (Zelda's): held the same way
    W32(g_rd_mgr, RD_FLAGS, 0);
    rdt_set_worlds(&g_rd_lfs, wu, 10);
    pup_run("Wait", 0, 0.6);
    SWMOD_REGS(c)->x[0] = P(g_pp_fm.unit);
    unit_step_wrapper(c);
    int stopped = t_pup.rd == RD_NONE && g_pup_ov_writes == w0 + 1 && !g_pup_ov_any;
    W64(g_pp_fm.unit, UNIT_ANIM, g_rd_pfs.anim);
    W64(g_rd_pfs.anim, 0, 0x7001aaaa00ull);
    W32(g_rd_mgr, RD_FLAGS, RD_ON);
    rdt_set_worlds(&g_rd_lfs, wl, 10);
    pup_run("SlippingDown", 0, 0.3);
    int other = t_pup.rd == RD_POSE && strstr(t_pup.rd_anim, "after 0x7001aaaa00") && !*t_pup.rd_why;
    check("puppet", "rd_after_step", posed && after && others && stopped && other,
          "posed %d (%s, why %s) after %d (clip writes %d, after writes +%lld) others %d stopped %d other class %d (%s)",
          posed, t_pup.rd_anim, t_pup.rd_why, after, g_rdt_clip_writes, (long long)(g_pup_ov_writes - w0), others,
          stopped, other, t_pup.rd_anim);
    W64(g_rd_pfs.anim, 0, ANIM_VPTR);

    // the tumble ends on a steep hill: he slides on, upright, 6 m/s down a 27 degree slope (SlideBrake at the end):
    // still his pose (never Walk by his speed), the ground follows the hill, the costume over it; standing: its clip
    W32(g_rd_mgr, RD_FLAGS, 0);
    rdt_set_worlds(&g_rd_lfs, wu, 10);
    int plays0 = g_pp_plays;
    pup_run_y("Slide", 6.0f, -3.0f, 0.8);
    float g_used = t_rd.slope[0];
    const PupBody *pb = pup_body("goron");
    float gnd[3] = {t_rd.slope[0], t_rd.slope[1], 0};
    pup_rd_lower(t_rd.xl, &t_rd.link, pup_body("link"), 1.0f, gnd);
    float drop = pup_rd_drop(t_rd.wp, &t_rd.pu[0], pb, 1.0f, gnd) + t_pup.rd_lift;
    int sliding = t_pup.rd == RD_POSE && !strcmp(t_pup.rd_src, "anim") && g_pp_plays == plays0 &&
                  __builtin_fabsf(g_used + 0.5f) < 0.05f && drop > -PUP_RD_SINK_OK - 0.02f;
    pup_run_y("SlideBrake", 1.0f, -0.5f, 0.2);
    int braking = t_pup.rd == RD_POSE;
    ((float *)(void *)(g_pl.mem + ACTOR_MTX))[7] = 5.0f, ((float *)(void *)(g_pl.pi + PI_POS))[1] = 5.0f;  // (back up)
    pup_run("Wait", 0, 1.5);  // (its own clip again once he stands: Wait when his speed has settled)
    check("puppet", "rd_slide_followed", sliding && braking && t_pup.rd == RD_NONE && !strcmp(g_pp_current, "Wait"),
          "sliding %d (ragdoll %s src %s, plays +%d, slope %.3f, its lowest %.3f over the hill) braking %d, then %s plays "
          "%s", sliding, RD_NAMES[t_pup.rd], t_pup.rd_src, g_pp_plays - plays0, g_used, drop, braking,
          RD_NAMES[t_pup.rd], g_pp_current);

    // Daruk's sprint: his ball rolls at speed / radius, its turn in the matrix; slower again: upright, then Walk
    pup_run("Move", 8.0f, 1.2);
    float spin_want = t_pup.ball_r > 0 ? 8.0f / t_pup.ball_r : 0, ct = __builtin_cosf(t_pup.ball_angle);
    static PupX xw[11];  // (its bones in the world: its matrix times its pose in its own frame, the last ball frame's)
    for (int k = 0; k < t_rd.pu[0].n && k < 11; k++) {
        pup_r_mul(g_pup_rot, t_rd.wp[k].R, xw[k].R);
        pup_r_apply(g_pup_rot, t_rd.wp[k].t, xw[k].t);
        for (int r = 0; r < 3; r++) xw[k].t[r] += g_pup_pos[r];
        xw[k].k = t_rd.wp[k].k;
    }
    float on5[3] = {0, 0, 5.0f}, low = pup_rd_drop(xw, &t_rd.pu[0], pup_body("goron"), 1.0f, on5);
    int rolling = t_pup.ball == 1 && t_pup.balls >= 1 && !strcmp(g_pp_current, "RunGear2") && t_pup.ball_r > 0.3f &&
                  __builtin_fabsf(t_pup.ball_spin - spin_want) < 0.05f * spin_want &&
                  __builtin_fabsf(g_pup_rot[4] - ct) < 1e-3f && __builtin_fabsf(low) < 1e-3f;
    float spin = t_pup.ball_spin, r_ = t_pup.ball_r, y_ = low;
    pup_run("Move", 1.6f, 0.7);
    int upright = t_pup.ball == 0 && strcmp(g_pp_current, "RunGear2") && __builtin_fabsf(g_pup_rot[4] - 1.0f) < 1e-4f &&
                  g_pup_pos[1] == 5.0f;  // (then Run or Walk, by his speed as it settles)
    lock();
    g_pup_req.ball_off = 1;
    unlock();
    pup_run("Move", 8.0f, 0.5);
    int off = t_pup.ball == 0 && !strcmp(g_pp_current, "RunGear2") && __builtin_fabsf(g_pup_rot[4] - 1.0f) < 1e-4f;
    lock();
    g_pup_req.ball_off = 0;
    unlock();
    pup_run("Wait", 0, 0.3);
    check("puppet", "ball_rolls", rolling && upright && off, "rolling %d (spin %.2f want %.2f, radius %.2f, low %.4f) "
          "upright %d (%s) off %d", rolling, spin, spin_want, r_, y_, upright, g_pp_current, off);
    // 0.9.5: over a drop his speed falls to 2 m/s for 0.5 s while he still sprints (his stamina falls): the ball stays;
    // out of his sprint (no more drain) it comes back upright once he has been out of it PUP_SPRINT_LEAVE_S
    pup_run("Move", 8.0f, 1.0);
    int rolled = t_pup.ball == 1;
    g_pp_drain = 1;
    pup_run("Move", 8.0f, 0.2);
    pup_run("Move", 2.0f, 0.5);
    int kept_drop = t_pup.ball == 1 && !strcmp(g_pp_current, "RunGear2");
    g_pp_drain = 0;
    pup_run("Move", 2.0f, PUP_DRAIN_S + PUP_SPRINT_LEAVE_S + PUP_BALL_END_MAX + 0.2);
    int ended = t_pup.ball == 0 && strcmp(g_pp_current, "RunGear2");
    g_pp_stam = -1.0f;
    pup_run("Wait", 0, 0.3);
    check("puppet", "ball_drop_hold", rolled && kept_drop && ended, "rolled %d kept through the drop %d (%s) ended %d (%s)",
          rolled, kept_drop, g_pp_current, ended, g_pp_current);

    // 0.9.3: the ground clamp: standing, the costume's lowest foot (its pose in its own frame) at Link's lowest foot;
    // swimming, its head at his head's height; the lift applied to where it is put
    lock();
    g_pup_req.clamp_off = 0;
    unlock();
    pup_run("Wait", 0, 0.6);
    static float c93[PUP_MAXB][12];
    static PupX w93[PUP_MAXB];
    float lvl[3] = {0, 0, 0}, feet_want = 1e30f, head_want = 1e30f;
    if (pup_rd_locals(&t_rd.pu[0], c93, g_pt)) {
        pup_fk(&t_rd.pu[0], (const float (*)[12])c93, 1.0f, w93);
        float ll = pup_rd_drop_m(t_rd.xl, &t_rd.link, pup_body("link"), 1.0f, lvl, pup_rd_feet()) - 5.0f;
        float cl = pup_rd_drop_m(w93, &t_rd.pu[0], pup_body("goron"), 1.0f, lvl, pup_rd_feet());
        feet_want = ll - cl;
        head_want = (t_rd.xl[t_rd.link.head].t[1] - 5.0f) - w93[t_rd.pu[0].head].t[1];
    }
    float fw = feet_want > PUP_CLAMP_MAX ? PUP_CLAMP_MAX : feet_want < -PUP_CLAMP_MAX ? -PUP_CLAMP_MAX : feet_want;
    int feet = !strcmp(t_pup.clamp_by, "feet") && __builtin_fabsf(t_pup.clamp_want - feet_want) < 1e-4f &&
               __builtin_fabsf(t_pup.clamp - fw) < 1e-3f && __builtin_fabsf(g_pup_pos[1] - 5.0f - t_pup.clamp) < 1e-4f;
    float feet_y = g_pup_pos[1];
    pup_run("SwimMove", 0.5f, 0.6);
    float hw = head_want > PUP_CLAMP_MAX ? PUP_CLAMP_MAX : head_want < -PUP_CLAMP_MAX ? -PUP_CLAMP_MAX : head_want;
    int head = !strcmp(t_pup.clamp_by, "head") && __builtin_fabsf(t_pup.clamp_want - head_want) < 1e-4f &&
               __builtin_fabsf(t_pup.clamp - hw) < 1e-3f && __builtin_fabsf(g_pup_pos[1] - 5.0f - t_pup.clamp) < 1e-4f;
    lock();
    g_pup_req.clamp_off = 1;
    unlock();
    pup_run("Wait", 0, 0.3);
    int off93 = !*t_pup.clamp_by && t_pup.clamp == 0.0f && g_pup_pos[1] == 5.0f;
    check("puppet", "clamp_world", feet && head && off93,
          "feet %d (want %.3f/%.3f, lift %.3f, y %.3f) head %d (want %.3f/%.3f, lift %.3f, by %s) off %d", feet,
          t_pup.clamp_want, feet_want, t_pup.clamp, feet_y, head, t_pup.clamp_want, head_want, t_pup.clamp,
          t_pup.clamp_by, off93);

    // `ghost puppet ragdoll clip` says why; another pick: no word of it left
    lock();
    g_pup_req.rd_mode = RDM_CLIP;
    unlock();
    W32(g_rd_mgr, RD_FLAGS, RD_ON);
    rdt_set_worlds(&g_rd_lfs, wl, 10);
    pup_run("SlippingDown", 0, 0.3);
    int said = strstr(t_pup.rd_why, "ragdoll clip") != NULL;
    lock();
    g_pup_req.rd_mode = RDM_POSE;
    unlock();
    pup_want("link");
    pup_run("SlippingDown", 0, 0.1);
    lock();
    PupStatus now = g_pup;
    unlock();
    check("puppet", "rd_why_cleared", said && !*now.rd_why && !*now.rd_src && now.rd == RD_NONE && now.state == PS_OFF,
          "said %d now why '%s' src '%s'", said, now.rd_why, now.rd_src);
    g_orig_unit_step = NULL;
    pup_ov_clear();
}

static void check_puppet_ragdoll(void) {
    // detection: the manager's bit, or a ragdoll / lying clip; nothing else
    check("puppet", "rd_detect", pup_rd_now(1, RD_ON | 0x40, "Wait") == 1 && pup_rd_now(1, 0, "SlippingDown") == 2 &&
          pup_rd_now(0, 0, "DeadRagdoll") == 2 && !pup_rd_now(1, 0x40, "Wait") && !pup_rd_now(0, RD_ON, "Move") &&
          !pup_rd_now(1, 0, "DamageS"), "flag %d anim %d", pup_rd_now(1, RD_ON, "Wait"), pup_rd_now(1, 0, "Dead"));
    // 0.9.2: the slide he gets up into on a hill, the big knock-back and its getting up, a hard landing: followed too
    // (the packs lack them: the costume walked by his speed); a jump, a fall, a stagger: its own clips
    check("puppet", "rd_detect_slides", pup_rd_now(1, 0, "Slide") == 2 && pup_rd_now(1, 0, "SlideBrake") == 2 &&
          pup_rd_now(1, 0, "SlideLand") == 2 && pup_rd_now(1, 0, "DamageL") == 2 &&
          pup_rd_now(1, 0, "DamageLStand") == 2 && pup_rd_now(1, 0, "LandDamageStand") == 2 &&
          pup_rd_now(1, 0, "SlippingDownLand") == 2 && !pup_rd_now(1, 0, "Fall") && !pup_rd_now(1, 0, "Jump") &&
          !pup_rd_now(1, 0, "Land") && !pup_rd_now(1, 0, "Walk"), "Slide %d DamageL %d Fall %d",
          pup_rd_now(1, 0, "Slide"), pup_rd_now(1, 0, "DamageL"), pup_rd_now(1, 0, "Fall"));
    int16_t loop[3] = {-1, 2, 1}, ord[3];
    check("puppet", "rd_order_refuses_loop", !pup_rd_order(loop, 3, ord), "a loop was ordered");

    // the pose: Link lying on his back (head to +x), his spine bent 30 degrees; the costume twice as long
    static PupSkel ls, ps;
    static float wl[10][12], cur[11][12], out[11][12];
    static PupX xl[10], wp[11];
    rdt_skel(&ls, 10, RDT_LINK, RDT_LINK_PAR, NULL);
    rdt_skel(&ps, 11, RDT_PUP, RDT_PUP_PAR, RDT_LINK);
    float hip[3] = {100, 5.15f, 200}, root[3] = {100, 5, 200};
    rdt_link_pose(hip, root, -90, 30, wl);
    for (int b = 0; b < 10; b++) pup_x_from(wl[b], &xl[b]);
    for (int b = 0; b < 11; b++) {
        memset(cur[b], 0, sizeof cur[b]);
        cur[b][0] = cur[b][5] = cur[b][10] = 1.0f;
        cur[b][7] = RDT_PUP_PAR[b] >= 1 && strcmp(RDT_PUP[b], "Belly") ? 0.2f : 0.0f;
        if (!strcmp(RDT_PUP[b], "Belly")) cur[b][11] = 0.5f;
    }
    float base[12] = {1, 0, 0, 100, 0, 1, 0, 5, 0, 0, 1, 200};
    pup_rd_solve(&ps, &ls, wl, cur, base, hip, out, wp, NULL, 0.0f, 0.0f);
    float worst = 0, len_err = 0;
    for (int b = 1; b < 11; b++) {
        if (ps.map[b] < 0) continue;
        worst = __builtin_fmaxf(worst, rdt_rdiff(wp[b].R, xl[ps.map[b]].R));
        if (b != ps.hip && RDT_PUP_PAR[b] >= 1)
            len_err = __builtin_fmaxf(len_err, __builtin_fabsf(rdt_dist(wp[b].t, wp[RDT_PUP_PAR[b]].t) - 0.2f));
    }
    float belly_want[3] = {wp[4].t[0] + wp[4].R[2] * 0.5f, wp[4].t[1] + wp[4].R[5] * 0.5f, wp[4].t[2] + wp[4].R[8] * 0.5f};
    float bent[9], spine_local[9] = {out[4][0], out[4][1], out[4][2], out[4][4], out[4][5], out[4][6], out[4][8], out[4][9],
                                     out[4][10]};
    rdt_rot(0, 30, bent);
    check("puppet", "rd_pose", worst < 1e-4f && len_err < 1e-4f && rdt_dist(wp[ps.hip].t, hip) < 1e-4f &&
          rdt_dist(wp[5].t, belly_want) < 1e-4f && rdt_rdiff(spine_local, bent) < 1e-4f && out[5][11] == 0.5f,
          "turns off by %.5f, lengths off by %.5f, hips %.3f %.3f %.3f, belly off %.4f, spine local off %.5f", worst,
          len_err, wp[ps.hip].t[0], wp[ps.hip].t[1], wp[ps.hip].t[2], rdt_dist(wp[5].t, belly_want),
          rdt_rdiff(spine_local, bent));

    // the ground under him: with Link's boxes, under every corner and about level; a body of 20 cm cubes lying flat
    // (the ground exactly under the cubes) and lying down a 20 degree slope (head down to +x: the ground follows it,
    // a little less steep: the pull to level)
    const PupBody *lb = pup_body("link"), *goron = pup_body("goron");
    float plane[3], flat[3], slope[3];
    int g1 = pup_rd_ground(xl, &ls, lb, 1.0f, plane);
    float link_drop = pup_rd_drop(xl, &ls, lb, 1.0f, plane);
    static PupBody cubes;
    cubes.id = "cubes";
    for (int k = 0; k < PUP_NBODY_BONES; k++) {
        cubes.has[k] = 1;
        for (int i = 0; i < 6; i++) cubes.box[k][i] = (short)(i % 2 ? 10 : -10);
    }
    static PupX xf[10], xs[10];
    static const float PLANK_X[10] = {0, 0, 0.1f, 0.2f, 0.3f, 0.4f, -0.4f, -0.8f, -0.4f, -0.8f};
    static const float PLANK_Z[10] = {0, 0, 0, 0, 0, 0, 0.1f, 0.1f, -0.1f, -0.1f};
    float r20[9];
    rdt_rot(-20, 0, r20);
    for (int b = 0; b < 10; b++) {
        memset(&xf[b], 0, sizeof xf[b]);
        xf[b].R[0] = xf[b].R[4] = xf[b].R[8] = xf[b].k = 1.0f;
        xf[b].t[0] = hip[0] + PLANK_X[b], xf[b].t[1] = hip[1], xf[b].t[2] = hip[2] + PLANK_Z[b];
        xs[b] = xf[b];
        memcpy(xs[b].R, r20, sizeof r20);
        float o[3] = {PLANK_X[b], 0, PLANK_Z[b]};
        for (int r = 0; r < 3; r++) xs[b].t[r] = hip[r] + r20[r * 3] * o[0] + r20[r * 3 + 1] * o[1] + r20[r * 3 + 2] * o[2];
    }
    int g2 = pup_rd_ground(xf, &ls, &cubes, 1.0f, flat), g3 = pup_rd_ground(xs, &ls, &cubes, 1.0f, slope);
    float flat_y = flat[0] * hip[0] + flat[1] * hip[2] + flat[2], want_a = -0.364f;  // (tan 20, falling to +x)
    check("puppet", "rd_ground", g1 && g2 && g3 && __builtin_fabsf(plane[0]) < 0.25f && __builtin_fabsf(plane[1]) < 0.25f &&
          __builtin_fabsf(link_drop) < 1e-4f && __builtin_fabsf(flat[0]) < 1e-4f && __builtin_fabsf(flat[1]) < 1e-4f &&
          __builtin_fabsf(flat_y - (hip[1] - 0.1f)) < 1e-4f && __builtin_fabsf(slope[0] - want_a) < 0.08f &&
          __builtin_fabsf(slope[1]) < 0.05f, "Link's %.3f %.3f (drop %.4f); cubes flat %.4f %.4f y %.3f; slope %.3f %.3f "
          "(want %.3f)", plane[0], plane[1], link_drop, flat[0], flat[1], flat_y, slope[0], slope[1], want_a);

    // Daruk at Link's hips: his boxes go under the ground; lifted by -drop they touch it, never under
    float d0 = pup_rd_drop(wp, &ps, goron, 1.0f, plane);
    for (int b = 0; b < 11; b++) wp[b].t[1] += -d0;
    float d1 = pup_rd_drop(wp, &ps, goron, 1.0f, plane);
    float thin = pup_rd_drop(wp, &ps, pup_body("zora"), 1.0f, plane);
    // (0.9.2: up fast, never more than PUP_RD_RISE_MPS m/s; down gently while he lies, fast as he stands)
    float up1 = pup_rd_follow(0.2f, 0.5f, 0.033f, 0.0f), up2 = pup_rd_follow(0.0f, 2.0f, 0.033f, 0.0f);
    float down_lying = pup_rd_follow(0.5f, 0.0f, 0.033f, 0.0f), down_standing = pup_rd_follow(0.5f, 0.0f, 0.033f, 1.0f);
    check("puppet", "rd_lift", d0 < -0.3f && __builtin_fabsf(d1) < 1e-4f && thin > 0.0f && up1 > 0.45f && up1 <= 0.5f &&
          up2 <= PUP_RD_RISE_MPS * 0.033f + 1e-4f && up2 > 0.4f && down_lying > 0.4f && down_standing < down_lying,
          "drop %.3f, after %.5f, Mipha's %.3f; up %.3f %.3f, down lying %.3f standing %.3f", d0, d1, thin, up1, up2,
          down_lying, down_standing);

    // up again: standing (hips 0.99 m over the ground), not lying
    static float wu[10][12];
    static PupX xu[10];
    float hip_up[3] = {100, 5.99f, 200};
    rdt_link_pose(hip_up, root, 0, 0, wu);
    for (int b = 0; b < 10; b++) pup_x_from(wu[b], &xu[b]);
    float pu[3];
    pup_rd_ground(xu, &ls, lb, 1.0f, pu);
    check("puppet", "rd_upright", pup_rd_upright(xu, &ls, pu) && !pup_rd_upright(xl, &ls, plane),
          "standing %d lying %d", pup_rd_upright(xu, &ls, pu), pup_rd_upright(xl, &ls, plane));

    // the measured bodies: Link first, every pack's character but the Hylian (Mii-style parts: Link's, scaled)
    int all = !strcmp(PUP_BODIES[0].id, "link");
    for (size_t i = 0; i < sizeof PUP_SLOTS / sizeof *PUP_SLOTS; i++)
        if (strcmp(PUP_SLOTS[i].id, "hylian") && strcmp(PUP_SLOTS[i].id, "linkle") &&  // (Linkle: Link's skeleton)
            strcmp(PUP_SLOTS[i].id, "custom") &&                                       // (an import: Link's too)
            pup_body(PUP_SLOTS[i].id) == &PUP_BODIES[0])
            all = 0;
    check("puppet", "rd_bodies", all && pup_body("hylian") == &PUP_BODIES[0] && goron->has[2] &&
          goron->box[2][3] - goron->box[2][2] > 150, "goron Spine_2 y %d..%d", goron->box[2][2], goron->box[2][3]);

    rdt_pure_092(&ls, &ps, wl, cur, base, hip);
    check_puppet_cloth();  // (0.9.6)

    if (!g_items_world_ok) {
        check("puppet", "rd_world", 1, "SKIP: the made-up globals' page is taken in this process");
        return;
    }
    // the world: Link (made up, at 100 5 200) and Daruk's costume, live and shown
    items_fresh(1);
    memset(&g_pl, 0, sizeof g_pl);
    float lm[12] = {1, 0, 0, 100, 0, 1, 0, 5, 0, 0, 1, 200};
    memcpy(g_pl.mem + ACTOR_MTX, lm, sizeof lm);
    fake_model(g_pl.mem, &g_pl.fm, 1);
    W64(g_pl.pi, PI_PLAYER, g_pl.mem);
    float pp[3] = {100, 5, 200};
    memcpy(g_pl.pi + PI_POS, pp, sizeof pp);
    g_fake_vtable[2] = FAKE_ISA;
    g_pup_busy = pp_busy, g_pup_paused = pp_paused, g_pup_check_files = 0, g_pup_stamina = pp_stamina;
    g_pp_free = 1, g_pp_paused = 0, g_px = 100.0f;
    memset(&t_status.self, 0, sizeof t_status.self);
    t_status.wanted = 0, t_status.state = ST_IDLE;
    lock();
    g_pup_req.clamp_off = 1;  // (0.9.3: these rows put it at Link's own height; the clamp has its own row: clamp_world)
    unlock();
    pup_want("daruk");
    pup_run("Wait", 0, 1.0);
    int live = t_pup.state == PS_LIVE && t_pup.shown && g_pp_proc;
    fake_skel(&g_pl.fm, &g_rd_lfs, 10, RDT_LINK, RDT_LINK_PAR, 0.1f);
    fake_skel(&g_pp_fm, &g_rd_pfs, 11, RDT_PUP, RDT_PUP_PAR, 0.2f);
    t_rd.link_for = 0;  // (Link had no skeleton until now: read at once, not at the next retry)
    memset(g_rd_mgr, 0, sizeof g_rd_mgr);
    W64(g_pl.mem, LINK_RAGDOLL, g_rd_mgr);
    rdt_link_pose(hip_up, root, 0, 0, wu);
    rdt_set_worlds(&g_rd_lfs, wu, 10);
    pup_run("Wait", 0, 0.3);
    int calm = t_pup.rd == RD_NONE && (g_rd_pfs.anim[ANIM_FLAGS] & ANIM_RUNS);
    // a bomb: the manager's bit, Link lying (his spine bent)
    W32(g_rd_mgr, RD_FLAGS, RD_ON | 0x40);
    rdt_set_worlds(&g_rd_lfs, wl, 10);
    int64_t frames0 = t_pup.rd_frames;
    pup_run("SlippingDown", 0, 0.3);
    float *hl = g_rd_pfs.locals[1], *sl = g_rd_pfs.locals[4];  // (Skl_Root, Spine_1: columns from +0x20 = [8])
    float hip_rot[9] = {hl[8], hl[12], hl[16], hl[9], hl[13], hl[17], hl[10], hl[14], hl[18]};
    float sp_rot[9] = {sl[8], sl[12], sl[16], sl[9], sl[13], sl[17], sl[10], sl[14], sl[18]};
    float lying[9];
    rdt_rot(-90, 0, lying);
    float pup_y = ((const float *)(uintptr_t)(g_pp_proc + ACTOR_MTX))[7];
    int posed = t_pup.rd == RD_POSE && !strcmp(t_pup.rd_src, "flag") && !(g_rd_pfs.anim[ANIM_FLAGS] & ANIM_RUNS) &&
                rdt_rdiff(hip_rot, lying) < 1e-4f && rdt_rdiff(sp_rot, bent) < 1e-4f &&
                __builtin_fabsf(hl[21] - 0.15f) < 1e-4f && g_rd_pfs.locals[5][22] == 0.5f && t_pup.rd_mapped == 10 &&
                t_pup.rd_frames > frames0 && (g_pp_fm.unit[UNIT_DIRTY] & UNIT_LOCALS_NEW) && t_pup.shown &&
                !fm_drawn(&g_pl.fm);
    check("puppet", "rd_follow", live && calm && posed && t_pup.rd_lift > 0.3f && __builtin_fabsf(pup_y - 5.0f - t_pup.rd_lift) < 1e-4f,
          "live %d calm %d ragdoll %s src %s anim bit %d hips off %.4f spine off %.4f hip y %.3f bones %d lift %.3f "
          "puppet y %.3f why %s", live, calm, RD_NAMES[t_pup.rd], t_pup.rd_src, g_rd_pfs.anim[ANIM_FLAGS] & ANIM_RUNS,
          rdt_rdiff(hip_rot, lying), rdt_rdiff(sp_rot, bent), hl[21], t_pup.rd_mapped, t_pup.rd_lift, pup_y,
          t_pup.rd_why);
    // the ragdoll ends but he still lies there: still followed ("getup"); standing again: its animation and clip back
    W32(g_rd_mgr, RD_FLAGS, 0x40);
    pup_run("Wait", 0, 0.5);
    int getup = t_pup.rd == RD_POSE && !strcmp(t_pup.rd_src, "getup");
    rdt_set_worlds(&g_rd_lfs, wu, 10);
    pup_run("Wait", 0, 0.6);
    pup_y = ((const float *)(uintptr_t)(g_pp_proc + ACTOR_MTX))[7];
    check("puppet", "rd_getup", getup && t_pup.rd == RD_NONE && (g_rd_pfs.anim[ANIM_FLAGS] & ANIM_RUNS) &&
          !strcmp(g_pp_current, "Wait") && pup_y == 5.0f && t_pup.shown,
          "getup %d then %s anim bit %d plays %s y %.3f", getup, RD_NAMES[t_pup.rd],
          g_rd_pfs.anim[ANIM_FLAGS] & ANIM_RUNS, g_pp_current, pup_y);
    // `ghost puppet ragdoll clip`: its lying clip, its animation never paused; `off`: nothing
    lock();
    g_pup_req.rd_mode = RDM_CLIP;
    unlock();
    W32(g_rd_mgr, RD_FLAGS, RD_ON);
    rdt_set_worlds(&g_rd_lfs, wl, 10);
    pup_run("SlippingDown", 0, 1.0);
    int clip = t_pup.rd == RD_CLIP && !strcmp(g_pp_current, "Dead") && (g_rd_pfs.anim[ANIM_FLAGS] & ANIM_RUNS) &&
               strstr(t_pup.rd_why, "ragdoll clip");
    lock();
    g_pup_req.rd_mode = RDM_OFF;
    unlock();
    pup_run("SlippingDown", 0, 0.3);
    int off = t_pup.rd == RD_NONE && (g_rd_pfs.anim[ANIM_FLAGS] & ANIM_RUNS);
    lock();
    g_pup_req.rd_mode = RDM_POSE;
    unlock();
    W32(g_rd_mgr, RD_FLAGS, 0);
    rdt_set_worlds(&g_rd_lfs, wu, 10);
    pup_run("Wait", 0, 0.6);
    check("puppet", "rd_clip_and_off", clip && off && t_pup.rd == RD_NONE, "clip %d (%s, plays %s, why %s) off %d",
          clip, RD_NAMES[t_pup.rd], g_pp_current, t_pup.rd_why, off);
    // the costume goes mid-ragdoll (`iam link`): nothing left paused or posed, Link shows
    W32(g_rd_mgr, RD_FLAGS, RD_ON);
    rdt_set_worlds(&g_rd_lfs, wl, 10);
    pup_run("SlippingDown", 0, 0.2);
    int was = t_pup.rd == RD_POSE;
    pup_want("link");
    pup_run("SlippingDown", 0, 0.2);
    check("puppet", "rd_removed", was && t_pup.state == PS_OFF && t_pup.rd == RD_NONE && !t_rd.on && fm_drawn(&g_pl.fm),
          "was %d state %s ragdoll %s", was, PS_NAMES[t_pup.state], RD_NAMES[t_pup.rd]);
    rdt_world_092(wl, wu, hip_up, root);
    lock();
    g_pup_req.clamp_off = 0;
    unlock();
    W32(g_rd_mgr, RD_FLAGS, 0);
    W64(g_pl.mem, LINK_RAGDOLL, 0);
    g_pup_busy = pup_busy_real, g_pup_paused = pup_paused_real, g_pup_check_files = 1, g_pup_stamina = pup_stamina_real;
    lock();
    memset(&g_iam, 0, sizeof g_iam);
    unlock();
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
    check_coop();
    check_iam();
    check_puppet();
    check_puppet_ragdoll();
    return g_fails;
}
