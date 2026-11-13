// 0.9.0 See yourself as your pick, live (Who am I, docs/charsel.md "See yourself"). Included by ghost.c after iam.c.
//
// Your pick (`iam daruk`, or the pick the multiplayer client or the wheel gave through `ghost looks ... current`) is
// shown on YOUR screen too, at once, with no restart: the tick spawns that character's pack (the same pack your
// friends' ghost of you uses: tools/charsel.py build, deployed with your mods) as a "self puppet", and every frame:
// - puts it exactly where Link is (his matrix: place and turn), raised by `ghost puppet lift` (0 m by default);
// - plays the character's clip for what Link does: Link's own animation name (read as `ghost self` reads it) when the
//   pack has it (the pack's 33 names map to the character's own clips first: Daruk's sprint is his rolling ball,
//   Revali glides with Rito wings, Mipha swims with Zora clips; Link's clips only where it has none); Link's `Move`
//   (walk, run and sprint are one name on Link: 1.6, 5.4 and 8 m/s in the game) by his speed: Walk, Run, and RunGear2
//   from PUP_SPRINT_FROM; a near name for the rest of a family (ClimbJump -> ClimbMove, SwimDash...);
// - hides Link with the game's own show/hide of a model (MODEL_SHOW: bit 0 of each model unit's flags; the game's
//   actor show sets it, its hide actions clear it [V code]). His armor and held items copy his model's flags every
//   frame (0x70012132d0, called by the armor's and the weapon's frame updates [V code]), so they go with him;
// - gives the puppet copies of Link's weapon, shield and bow (the ghost's held items, ghost.c) while no friend's
//   ghost is up (with a friend's ghost up, the items are the ghost's: the puppet's hands are empty).
// Link is shown instead (and the puppet hidden) whenever he can't be mirrored: no real position (a load, a cutscene,
// riding a horse), a state the character has no clip for (Link's climb, swim or glide family when the pack lacks it,
// a horse, a shield surf), and until the puppet's animations answer. The pause menu keeps what was shown. `iam link`
// (or `iam self off`) shows Link again and removes the puppet. The camera stays on Link: nothing here moves him.
// A pack that never spawns (not built or not deployed, or the game refuses it) is given up after PUP_TRIES tries and
// Link stays; `iam` then says to restart as that character instead (tools/charsel.py playas: the body in your own
// TitleBG.pack, the restart fallback).
// Not yet run in the game [G]: whether the game shows Link again by itself (he is then hidden again each frame and
// `rehides` counts it), whether his shadow goes with him, the puppet's hip height (`ghost puppet lift`), one frame
// of lag behind Link.

#define MODEL_SHOW 0x7000f7739cull     // (model holder, on, bit): sets or clears `bit` in every model unit's flags
#define UNIT_FLAGS 0x0c                // model unit: u16 flags; bit 0: drawn (MODEL_SHOW's own reads say so)
#define PUP_BIT 0
#define PUP_TRIES 5                    // spawn tries in a row before it gives up (Link stays)
#define PUP_RETRY_S 2.0
#define PUP_PENDING_S 20.0             // a request not ready in this long is released (a try)
#define PUP_STABLE_S 10.0              // a puppet live this long ends the row of tries
#define PUP_SETTLE_S 0.25              // Link free (real position, no pause) this long before the puppet shows again
#define PUP_SPRINT_FROM 6.9f           // m/s: Link's Move is a sprint (RunGear2) from this speed ...
#define PUP_SPRINT_BELOW 6.3f          // ... until he is slower than this
#define PUP_SWIM_DASH 2.5f             // m/s: a swim this fast is SwimDash (else SwimMove, below 0.3 SwimWait)
#define PUP_STILL 0.3f                 // m/s: slower than this is standing (ClimbWait, SwimWait, SquatWait)
#define PUP_VERIFY_S 0.15              // a name that doesn't play this long after the call is a miss ...
#define PUP_STRIKES 2                  // ... and after this many misses the pack lacks it
#define PUP_READ_S 0.25                // what plays is read back this often (the actor's own AI may change it)
#define PUP_REASSERT_S 1.0             // ... and asked again at most this often
#define PUP_LIFT_MAX 3.0f              // m: `ghost puppet lift` range (either way)
#define PUP_MAP_MAX 16                 // `ghost puppet map` entries

// The characters you can see yourself as, on the actor names tools/charsel.py builds them on (its SLOTS), with their
// names and the word `charsel playas` takes (people only: monsters can't be played as at a restart). The ids and
// aliases are charsel's RECIPES; tests/test_charsel_puppet.py checks this table against tools/charsel.py.
typedef struct {
    const char *id, *actor, *name, *playas;  // playas: "" none
} PupSlot;
static const PupSlot PUP_SLOTS[] = {
    {"zelda", "Dm_Npc_Sheiker_Searcher_M_000", "Zelda", "zelda"},
    {"hylian", "Dm_Npc_UMii_Hylia_BodyS_M_001", "Hylian", "hylian"},
    {"sheikah", "Dm_Npc_UMii_Hylia_BodyS_M_002", "Paya", "paya"},
    {"gerudo", "Dm_Npc_Handmaid_B", "Urbosa", "urbosa"},
    {"goron", "Dm663_Npc_Assasin_A", "Daruk", "daruk"},
    {"rito", "Dm_Npc_UMii_Hylia_BodyS_M_011", "Revali", "revali"},
    {"zora", "Dm_Npc_UMii_Hylia_BodyS_M_003", "Mipha", "mipha"},
    {"bokoblin", "Dm663_Npc_Assasin_B", "Bokoblin", ""},
    {"moblin", "Dm663_Npc_Gerudo_S_B", "Moblin", ""},
};
static const struct {
    const char *alias, *id;
} PUP_ALIASES[] = {  // (`iam daruk` with no client list keeps the word as typed: it still finds Daruk)
    {"princess", "zelda"}, {"hateno", "hylian"}, {"villager", "hylian"}, {"paya", "sheikah"}, {"urbosa", "gerudo"},
    {"daruk", "goron"},    {"revali", "rito"},   {"mipha", "zora"},      {"boko", "bokoblin"}, {"moriblin", "moblin"},
};

enum { PS_OFF, PS_WAIT, PS_PENDING, PS_LIVE, PS_FAILED };
static const char *const PS_NAMES[] = {"off", "wait", "pending", "live", "failed"};

typedef struct {
    int self_off;                  // `iam self off`: see yourself as Link (your friends still see your pick)
    float lift;                    // `ghost puppet lift m`: the puppet this much higher than Link (m)
    int nmap;                      // `ghost puppet map id=Actor,...`: other actor names (the client's catalog)
    char map_id[PUP_MAP_MAX][IAM_WORD + 1], map_actor[PUP_MAP_MAX][64];
    int retry_seq;                 // `ghost puppet retry`: a failed pack is tried again
} PupReq;

typedef struct {
    int state;                     // PS_*
    char id[IAM_WORD + 1];         // the pick it shows ("" none)
    char name[24];                 // ... its name ("Daruk")
    char playas[IAM_WORD + 1];     // ... the word `charsel playas` takes ("" can't)
    char actor[64];                // ... its pack's actor name
    uint64_t proc;                 // the puppet (0: none)
    int shown;                     // the puppet is shown and Link hidden now
    int link_hidden;               // Link's model is hidden by us
    int as_ok;                     // the puppet has animations of its own
    char link_anim[32];            // what Link plays (his body's animation name)
    char as_want[32], as[32];      // what the puppet is asked to play; what it plays (read back)
    float speed;                   // Link's ground speed (m/s)
    float lift;
    int items;                     // the puppet holds Link's items (no friend's ghost up)
    int tries;
    int64_t requests, spawned, drops, hides, shows, rehides, as_calls, misses, link_states;
    double live_s;
    char why[120];                 // why Link shows now, or why it gave up
    char missing[160];             // names the puppet lacks (the latest)
} PupStatus;

static PupReq g_pup_req;           // under lock()
static PupStatus g_pup;            // under lock(): the tick's copy, for `iam` and `ghost`
static PupStatus t_pup;            // the tick's
static _Alignas(16) Handle g_pup_handle;
static _Alignas(16) Link g_pup_link = {0, 0xffffffffu, 0, {0}};
static _Alignas(16) Pack g_pup_pack;
static _Alignas(16) float g_pup_mtx[12], g_pup_pos[3], g_pup_rot[9], g_pup_phys[12];
static _Alignas(16) uint64_t g_pup_action[2];
static uint64_t t_pup_heap, t_pup_player;           // the stage and Link the puppet was spawned with
static uint64_t t_pup_hidden_for;                   // the Link actor we hid (0: none)
static double t_pup_pending_since, t_pup_live_since, t_pup_retry_at, t_pup_free_since;
static int t_pup_retry_seq, t_pup_sprint, t_pup_seen, t_pup_hid_puppet;
static int t_pup_ever;                              // the puppet played what it was asked once (never a T-pose)
static const char *t_pup_asked;                     // the name last asked for (interned; NULL: none)
static double t_pup_call_t, t_pup_read_t;
static AnimAuto t_pup_anim;                         // Link's speed (his place each frame)
static Missing t_pup_missing;                       // names this puppet lacks
static int pup_free_real(void) { return botw_player_real_pos(api) != 0; }
static int pup_paused_real(void) { return botw_paused(api); }
static int (*g_pup_free)(void) = pup_free_real;      // (the selftest fakes these two)
static int (*g_pup_paused)(void) = pup_paused_real;
static int g_pup_check_files = 1;                   // (the selftest has no overlay)

// ---- pure parts (the selftest checks them)

static const PupSlot *pup_slot(const char *id) {
    for (size_t i = 0; i < sizeof PUP_SLOTS / sizeof *PUP_SLOTS; i++)
        if (!strcmp(PUP_SLOTS[i].id, id)) return &PUP_SLOTS[i];
    return NULL;
}

// The id for a word `iam` kept (an id, or an alias typed before a client gave the list). "" for Link and Linkle.
static const char *pup_id(const char *word) {
    for (size_t i = 0; i < sizeof PUP_ALIASES / sizeof *PUP_ALIASES; i++)
        if (!strcmp(PUP_ALIASES[i].alias, word)) return PUP_ALIASES[i].id;
    return pup_slot(word) ? word : "";
}

static int pup_prefix(const char *s, const char *p) { return !strncmp(s, p, strlen(p)); }

// What the puppet plays while Link plays `link` at v m/s (auto: Wait/Walk/Run by his speed; *sprint: the sprint's
// hysteresis), or NULL: it can't be mirrored, show Link. m: the names this puppet lacks.
static const char *pup_pick(const char *link, float v, int autoc, int *sprint, const Missing *m) {
    const char *speed = AN_NAMES[autoc >= AN_WAIT && autoc <= AN_RUN ? autoc : AN_WAIT];
    if (!*link || !strcmp(link, "-") || !strcmp(link, "?") || !strcmp(link, "Wait") || !strcmp(link, "DemoWait")) {
        *sprint = 0;
        return speed;
    }
    if (!strcmp(link, "Move")) {  // walk, run and sprint: by speed
        *sprint = v >= (*sprint ? PUP_SPRINT_BELOW : PUP_SPRINT_FROM);
        if (*sprint) return missing_has(m, "RunGear2") ? AS_RUN : "RunGear2";
        return autoc == AN_WAIT ? AS_WALK : speed;  // (Move while the speed still says Wait: he starts to walk)
    }
    *sprint = 0;
    if (pup_prefix(link, "Horse") || pup_prefix(link, "Ride") || pup_prefix(link, "Sled") || pup_prefix(link, "Surf") ||
        pup_prefix(link, "Demo") || pup_prefix(link, "Event"))
        return NULL;  // (a horse, a shield surf, a cutscene's own move: Link shows)
    if (!missing_has(m, link)) return link;  // (the pack's own name: asked; a miss is learned)
    if (anim_is_attack(link)) return missing_has(m, "CutS1") ? speed : "CutS1";
    // a near name of the same family; NULL where that family can't be shown on the ground
    const char *near = NULL;
    int must = 1;  // (the family can't be shown by speed: without a near name, show Link)
    if (pup_prefix(link, "Climb") || pup_prefix(link, "Wall")) near = v >= PUP_STILL ? "ClimbMove" : "ClimbWait";
    else if (pup_prefix(link, "Swim")) near = v >= PUP_SWIM_DASH ? "SwimDash" : v >= PUP_STILL ? "SwimMove" : "SwimWait";
    else if (pup_prefix(link, "Parashawl") || pup_prefix(link, "Glide") || pup_prefix(link, "Float"))
        near = "ParashawlGlide";
    else {
        must = 0;
        if (pup_prefix(link, "Dead") || pup_prefix(link, "Die")) near = "Dead";
        else if (pup_prefix(link, "Squat")) near = v >= PUP_STILL ? "SquatMove" : "SquatWait";
        else if (pup_prefix(link, "Jump")) near = "Jump";
        else if (pup_prefix(link, "Fall")) near = "Fall";
        else if (pup_prefix(link, "Land")) near = "Land";
        else if (pup_prefix(link, "Damage")) near = "DamageS";
        else if (pup_prefix(link, "Bow")) near = "BowCharge";
    }
    if (near && !missing_has(m, near)) return near;
    if (near && !strcmp(near, "ClimbMove") && !missing_has(m, "ClimbWait")) return "ClimbWait";
    if (near && (!strcmp(near, "SwimDash") || !strcmp(near, "SwimMove")) && !missing_has(m, "SwimMove")) return "SwimMove";
    return must ? NULL : speed;
}

// ---- the game side (the tick only)

static void pup_zero_link(void) {
    memset(&g_pup_link, 0, sizeof g_pup_link);
    g_pup_link.id = 0xffffffffu;
}

// Actor a's model holder (0: none, or not the model class).
static uint64_t pup_model(uint64_t a, double t) {
    uint64_t m = a ? self_u64(a + ACTOR_MODEL, t) : 0;
    return m && self_u64(m, t) == MODEL_VPTR ? m : 0;
}

// Bit 0 of the model's units' flags, ORed as the game's own read of them does: 1 drawn, 0 hidden, -1 unreadable.
static int pup_drawn(uint64_t m, double t) {
    int32_t n = (int32_t)self_u32(m + MODEL_NUNITS, t);
    uint64_t arr = n > 0 && n <= 64 ? self_u64(m + MODEL_UNITS, t) : 0;
    if (!arr) return -1;
    int any = 0;
    for (int i = 0; i < n; i++) {
        uint64_t slot = self_u64(arr + (uint64_t)i * 8, t), unit = slot ? self_u64(slot, t) : 0;
        if (!unit || !mem_ok(unit + UNIT_FLAGS, 2, t)) return -1;
        any |= *(volatile const uint16_t *)(uintptr_t)(unit + UNIT_FLAGS) >> PUP_BIT & 1;
    }
    return any;
}

// Show or hide actor a's model (the game's own call; only when it isn't so already). 1: it was changed.
static int pup_draw(struct Cpu *c, uint64_t a, int on, double t) {
    uint64_t m = pup_model(a, t);
    int d = m ? pup_drawn(m, t) : -1;
    if (d < 0 || d == on) return 0;
    gcall(c, MODEL_SHOW, m, (uint64_t)on, PUP_BIT, 0, 0, 0, 0);
    return 1;
}

// Link shown again (only the Link actor we hid: a new one, after a load, is shown by the game).
static void pup_show_link(struct Cpu *c, uint64_t player, double t) {
    if (!t_pup.link_hidden) return;
    if (player && player == t_pup_hidden_for && pup_draw(c, player, 1, t)) t_pup.shows++;
    t_pup.link_hidden = 0, t_pup_hidden_for = 0;
}

static void pup_hide_link(struct Cpu *c, uint64_t player, double t) {
    if (t_pup.link_hidden && player != t_pup_hidden_for) t_pup.link_hidden = 0;  // (another Link: start over)
    if (pup_draw(c, player, 0, t)) {
        if (t_pup.link_hidden) t_pup.rehides++;  // (the game showed him again by itself)
        else t_pup.hides++;
    }
    t_pup.link_hidden = 1, t_pup_hidden_for = player;
}

static void pup_anim_forget(void) {
    t_pup_asked = NULL, t_pup_seen = 0, t_pup_sprint = 0, t_pup_hid_puppet = 0, t_pup_ever = 0;
    memset(&t_pup_anim, 0, sizeof t_pup_anim);
    memset(&t_pup_missing, 0, sizeof t_pup_missing);
    t_pup.as[0] = t_pup.as_want[0] = t_pup.missing[0] = 0, t_pup.as_ok = 0, t_pup.shown = 0;
}

// The puppet goes (its items first; a pending request is released), and Link shows again.
static void pup_remove(struct Cpu *c, uint64_t player, double t) {
    if (t_pup.proc && t_items_ghost == t_pup.proc) items_drop(c, t);
    if (t_pup.state == PS_PENDING) {
        GCALL1(c, HANDLE_RELEASE, &g_pup_handle);
        GCALL1(c, HANDLE_CLEAR, &g_pup_handle);
    } else if (t_pup.state == PS_LIVE) {
        if (GCALL1(c, LINK_VALID, &g_pup_link) & 1) {
            uint64_t proc = rd64(g_pup_link.data + LINKDATA_PROC);
            if (proc) gcall(c, DELETE_LATER, proc, 0, 0, 0, 0, 0, 0);
        }
        GCALL1(c, LINK_RESET, &g_pup_link);
        pup_zero_link();
    }
    pup_show_link(c, player, t);
    t_pup.proc = 0, t_pup_proc = 0;
    pup_anim_forget();
}

// A try ended without a puppet: again PUP_RETRY_S later, at most PUP_TRIES in a row; then Link stays.
static void pup_try_failed(const char *why, double t) {
    t_pup.proc = 0, t_pup_proc = 0;
    if (++t_pup.tries >= PUP_TRIES) {
        t_pup.state = PS_FAILED;
        snprintf(t_pup.why, sizeof t_pup.why, "%s did not load (%s): you stay Link", t_pup.name, why);
        note("puppet: %s", t_pup.why);
    } else {
        t_pup.state = PS_WAIT;
        t_pup_retry_at = t + PUP_RETRY_S;
        snprintf(t_pup.why, sizeof t_pup.why, "%s: %s; trying again", t_pup.name, why);
    }
}

static void pup_request(struct Cpu *c, uint64_t pi, uint64_t player, double t) {
    uint64_t creator = rd64(CREATOR_PTR), heap = spawn_heap();
    float pos[3], yaw;
    if (!creator || !heap || !player_pose(pi, player, pos, &yaw) || rd8(creator + CREATOR_BLOCKED)) {
        t_pup_retry_at = t + 0.5;  // (no world yet, or the creator is busy: not a try)
        return;
    }
    const char *name = intern(t_pup.actor);
    if (!name) {
        t_pup.tries = PUP_TRIES - 1;
        pup_try_failed("too many actor names used; restart the game", t);
        return;
    }
    float r[9];
    yaw_rot(yaw, r);
    float m[12] = {r[0], r[1], r[2], pos[0], r[3], r[4], r[5], pos[1] + t_pup.lift, r[6], r[7], r[8], pos[2]};
    memcpy(g_pup_mtx, m, sizeof m);
    GCALL1(c, HANDLE_CLEAR, &g_pup_handle);
    memset(&g_pup_pack, 0, sizeof g_pup_pack);
    g_pup_pack.vptr = PACK_VPTR;
    GCALL1(c, PACK_CLEAR, g_pup_pack.buf);
    g_key_m.vptr = STRING_OBJ_VPTR, g_key_m.str = KEY_M;
    gcall(c, PACK_ADD, P(g_pup_pack.buf), P(g_pup_mtx), P(&g_key_m), 0x30, 7, 0, 0);
    t_pup.requests++;
    if (!(gcall(c, REQUEST_CREATE, creator, P(name), heap, P(&g_pup_handle), P(&g_pup_pack), 0, 1) & 1)) {
        GCALL1(c, HANDLE_CLEAR, &g_pup_handle);
        pup_try_failed("the game refused the request", t);
        return;
    }
    t_pup.state = PS_PENDING;
    t_pup_pending_since = t;
    t_pup_heap = heap, t_pup_player = player;
}

static void pup_pending(struct Cpu *c, double t) {
    uint64_t unit = g_pup_handle.unit;
    if (unit && rd64(unit + 8) != P(&g_pup_handle)) {  // (a stage change took the request: don't touch it)
        GCALL1(c, HANDLE_CLEAR, &g_pup_handle);
        t_pup.state = PS_WAIT, t_pup_retry_at = t + PUP_RETRY_S;
        return;
    }
    if (GCALL1(c, HANDLE_READY, &g_pup_handle) & 1) {
        uint64_t proc = GCALL1(c, HANDLE_PEEK, &g_pup_handle);
        uint64_t info = proc ? gcall(c, ACTOR_TYPEINFO, 0, 0, 0, 0, 0, 0, 0) : 0;
        if (!(proc && info && (GCALL2(c, rd64(rd64(proc) + 0x10), proc, info) & 1))) {
            GCALL1(c, HANDLE_RELEASE, &g_pup_handle);
            t_pup.tries = PUP_TRIES - 1;
            pup_try_failed("not an actor", t);
            return;
        }
        GCALL3(c, SET_MTX, proc, g_pup_mtx, 0);
        GCALL1(c, HANDLE_TAKE, &g_pup_handle);
        GCALL3(c, LINK_SET, &g_pup_link, proc, 0);
        t_pup.proc = t_pup_proc = proc;
        t_pup.state = PS_LIVE;
        t_pup.spawned++;
        t_pup_live_since = t;  // (the tries in a row end once it stayed PUP_STABLE_S: pup_live)
        pup_anim_forget();
        pup_draw(c, proc, 0, t);  // (hidden until it is placed and animated: Link shows meanwhile)
        t_pup_hid_puppet = 1;
        note("puppet: %s spawned after %.2f s", t_pup.name, t - t_pup_pending_since);
        return;
    }
    if (GCALL1(c, HANDLE_FAILED, &g_pup_handle) & 1) {
        GCALL1(c, HANDLE_RELEASE_FAILED, &g_pup_handle);
        GCALL1(c, HANDLE_CLEAR, &g_pup_handle);
        pup_try_failed("the game could not create it", t);
    } else if (t - t_pup_pending_since > PUP_PENDING_S) {
        GCALL1(c, HANDLE_RELEASE, &g_pup_handle);
        GCALL1(c, HANDLE_CLEAR, &g_pup_handle);
        pup_try_failed("not ready in time", t);
    }
}

static void pup_as_play(struct Cpu *c, uint64_t proc, const char *name) {
    g_pup_action[0] = 0, g_pup_action[1] = proc;
    swmod_set_s(c, 0, -1.0f);  // (morph frames: the ASList's own)
    gcall(c, AS_PLAY, P(g_pup_action), P(name), 1, 0, 0, 0, 0);
    t_pup.as_calls++;
}

// Each frame while live: place it at Link, pick and play its clip. Returns 1 when it mirrors Link now.
static int pup_live(struct Cpu *c, uint64_t player, const SelfState *self, double t) {
    uint64_t proc = t_pup.proc;
    t_pup.live_s = t - t_pup_live_since;
    if (t_pup.live_s >= PUP_STABLE_S) t_pup.tries = 0;  // (one the game keeps removing gives up after PUP_TRIES)
    float m[12];
    for (int k = 0; k < 12; k++) m[k] = rdf(player + ACTOR_MTX + 4 * (uint64_t)k);
    int ok = 1;
    for (int k = 0; k < 12; k++) ok &= finite_f(m[k], k % 4 == 3 ? 1e6f : 100.0f);
    if (!ok) {
        snprintf(t_pup.why, sizeof t_pup.why, "Link's place can't be read");
        return 0;
    }
    g_pup_pos[0] = m[3], g_pup_pos[1] = m[7] + t_pup.lift, g_pup_pos[2] = m[11];
    float rot[9] = {m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]};
    memcpy(g_pup_rot, rot, sizeof rot);
    gcall(c, SET_TRANSFORM, proc, P(g_pup_pos), P(g_pup_rot), 0, 0, 0, 0);
    if (rd64(proc + ACTOR_PHYSICS)) {  // (a pack with physics: its rigid bodies too, as the ghost)
        float pm[12] = {rot[0], rot[1], rot[2], g_pup_pos[0], rot[3], rot[4], rot[5], g_pup_pos[1],
                        rot[6], rot[7], rot[8], g_pup_pos[2]};
        memcpy(g_pup_phys, pm, sizeof pm);
        GCALL2(c, PHYS_SET_MTX, proc, g_pup_phys);
    }
    // Link's speed from his own place (exact: no network)
    anim_sample(&t_pup_anim, m[3], m[11], t);
    int autoc = anim_auto_step(&t_pup_anim, t, -1.0f);
    t_pup.speed = t_pup_anim.speed;
    uint64_t holder = rd64(proc + ACTOR_AS);
    t_pup.as_ok = holder && rd64(holder + AS_OWNER) == proc && *(volatile const int32_t *)(uintptr_t)(holder + AS_SLOTS) > 0;
    if (!t_pup.as_ok) {
        snprintf(t_pup.why, sizeof t_pup.why, "%s has no animations yet", t_pup.name);
        return 0;
    }
    copy_name(t_pup.link_anim, self->found ? self->anim : "");
    const char *pick = pup_pick(t_pup.link_anim, t_pup.speed, autoc, &t_pup_sprint, &t_pup_missing);
    if (!pick) {
        snprintf(t_pup.why, sizeof t_pup.why, "%s has no clip for Link's %s", t_pup.name, t_pup.link_anim);
        copy_name(t_pup.as_want, "");
        return 0;
    }
    const char *want = intern_anim(pick, 0);
    if (!want) want = AN_NAMES[autoc >= AN_WAIT && autoc <= AN_RUN ? autoc : AN_WAIT];  // (the name table is full)
    copy_name(t_pup.as_want, want);
    if (want != t_pup_asked) {
        pup_as_play(c, proc, want);
        t_pup_asked = want, t_pup_seen = 0;
        t_pup_call_t = t, t_pup_read_t = t + PUP_VERIFY_S;
    }
    if (t >= t_pup_read_t) {
        t_pup_read_t = t + PUP_READ_S;
        uint64_t s = gcall(c, AS_CURRENT, holder, 0, 0, 0, 0, 0, 0);
        copy_name(t_pup.as, (const char *)(uintptr_t)rd64(s ? s + 8 : 0));
        if (!strcmp(t_pup.as, want)) {
            if (!t_pup_seen) missing_clear(&t_pup_missing, want);
            t_pup_seen = 1, t_pup_ever = 1;
        } else if (!t_pup_seen) {  // (it never played: a miss; twice and the pack lacks it, picked again next frame)
            if (missing_strike(&t_pup_missing, want) >= PUP_STRIKES) {
                t_pup.misses++;
                missing_join(&t_pup_missing, t_pup.missing, sizeof t_pup.missing);
                t_pup_asked = NULL;
            } else {
                pup_as_play(c, proc, want);
                t_pup_call_t = t, t_pup_read_t = t + PUP_VERIFY_S;
            }
        } else if (t - t_pup_call_t >= PUP_REASSERT_S) {  // (its own AI changed it: ask again, slowly)
            pup_as_play(c, proc, want);
            t_pup_call_t = t;
        }
    }
    t_pup.why[0] = 0;
    if (!t_pup_ever) snprintf(t_pup.why, sizeof t_pup.why, "%s's animations are starting", t_pup.name);
    return t_pup_ever;  // (shown once it played a clip: never a T-pose; a new name is checked while the last plays)
}

// The pick the puppet should show: the word `iam` kept, else the client's current pick; "" none.
static void pup_pick_id(char out[IAM_WORD + 1]) {
    lock();
    const char *w = *g_iam.want ? g_iam.want : g_iam.current;
    memcpy(out, w, IAM_WORD + 1);
    unlock();
    out[IAM_WORD] = 0;
}

// The pack is in the deployed overlay (SAVAGEWIND_ROMFS_OVERLAY, set by modman run and the launcher). 1 when it is,
// or when the overlay isn't known (then the game decides).
__declspec(dllimport) unsigned long __stdcall GetEnvironmentVariableA(const char *name, char *buf, unsigned long size);
__declspec(dllimport) unsigned long __stdcall GetFileAttributesA(const char *path);
static int pup_pack_there(const char *actor) {
    if (!g_pup_check_files) return 1;
    char dir[400], path[600];
    unsigned long n = GetEnvironmentVariableA("SAVAGEWIND_ROMFS_OVERLAY", dir, sizeof dir);
    if (!n || n >= sizeof dir) return 1;
    snprintf(path, sizeof path, "%s\\Actor\\Pack\\%s.sbactorpack", dir, actor);
    return GetFileAttributesA(path) != 0xffffffffu;
}

static void pup_tick(struct Cpu *c, uint64_t pi, uint64_t player, double t) {
    PupReq rq;
    lock();
    rq = g_pup_req;
    unlock();
    char word[IAM_WORD + 1];
    pup_pick_id(word);
    const char *id = rq.self_off ? "" : pup_id(word);
    const PupSlot *slot = *id ? pup_slot(id) : NULL;
    const char *actor = slot ? slot->actor : NULL;
    for (int i = 0; slot && i < rq.nmap && i < PUP_MAP_MAX; i++)  // (`ghost puppet map`: the client's catalog)
        if (!strcmp(rq.map_id[i], id)) actor = rq.map_actor[i];
    t_pup.lift = rq.lift;
    if (strcmp(id, t_pup.id) || (actor && strcmp(actor, t_pup.actor)) || (!actor && t_pup.state != PS_OFF) ||
        (rq.retry_seq != t_pup_retry_seq && t_pup.state == PS_FAILED)) {  // a new pick: start over
        t_pup_retry_seq = rq.retry_seq;
        if (t_pup.state != PS_OFF) pup_remove(c, player, t);
        memset(t_pup.id, 0, sizeof t_pup.id);
        memcpy(t_pup.id, id, strlen(id));
        t_pup.name[0] = t_pup.playas[0] = t_pup.actor[0] = t_pup.why[0] = 0, t_pup.tries = 0;
        t_pup.state = PS_OFF;
        if (actor) {
            snprintf(t_pup.name, sizeof t_pup.name, "%s", slot->name);
            snprintf(t_pup.playas, sizeof t_pup.playas, "%s", slot->playas);
            snprintf(t_pup.actor, sizeof t_pup.actor, "%s", actor);
            if (pup_pack_there(actor)) {
                t_pup.state = PS_WAIT, t_pup_retry_at = 0;
            } else {
                t_pup.state = PS_FAILED;
                snprintf(t_pup.why, sizeof t_pup.why, "%s isn't in your game (charsel build, then add it)", t_pup.name);
                note("puppet: %s", t_pup.why);
            }
        }
    }
    t_pup_retry_seq = rq.retry_seq;
    // A stage change took the puppet along: forget it (no call), spawn again later. Link is a new actor then.
    if (t_pup.state == PS_LIVE || t_pup.state == PS_PENDING) {
        if (spawn_heap() != t_pup_heap) {
            if (t_pup.state == PS_LIVE) {
                if (t_items_ghost == t_pup.proc) items_forget(t);
                old_link_add(&g_pup_link, t);  // (watched: removed after the load if the game kept it)
                pup_zero_link();
            } else {
                memset(&g_pup_handle, 0, sizeof g_pup_handle);
            }
            t_pup.proc = 0, t_pup_proc = 0, t_pup.drops++;
            pup_anim_forget();
            t_pup.state = PS_WAIT, t_pup_retry_at = t + PUP_RETRY_S;
        } else if (player && player != t_pup_player) {
            pup_remove(c, player, t);
            t_pup.state = PS_WAIT, t_pup_retry_at = t + PUP_RETRY_S;
        }
    }
    if (t_pup.link_hidden && player != t_pup_hidden_for) t_pup.link_hidden = 0, t_pup_hidden_for = 0;
    int mirror = 0;
    switch (t_pup.state) {
    case PS_WAIT:
        if (player && t >= t_pup_retry_at) pup_request(c, pi, player, t);
        break;
    case PS_PENDING:
        pup_pending(c, t);
        break;
    case PS_LIVE:
        if (!player) break;  // (a load: nothing is touched)
        if (!(GCALL1(c, LINK_VALID, &g_pup_link) & 1)) {  // the game removed it
            if (t_items_ghost == t_pup.proc) items_drop(c, t);
            pup_zero_link();
            t_pup.drops++;
            pup_show_link(c, player, t);
            pup_anim_forget();
            pup_try_failed("the game removed it", t);
            break;
        }
        mirror = pup_live(c, player, &t_status.self, t);
        break;
    }
    // Who is drawn: the puppet while it mirrors Link and he is free (settled), else Link. The pause menu keeps it.
    if (t_pup.state == PS_LIVE && player) {
        int paused = g_pup_paused();
        if (!g_pup_free()) {
            t_pup_free_since = 0;
            if (!*t_pup.why) snprintf(t_pup.why, sizeof t_pup.why, "loading, a cutscene or riding: Link shows");
            mirror = 0;
        } else if (!t_pup_free_since) {
            t_pup_free_since = t;
        }
        if (t_pup_free_since && t - t_pup_free_since < PUP_SETTLE_S) mirror = 0;
        if (paused) mirror = t_pup.shown;
        if (mirror) {
            pup_hide_link(c, player, t);
            pup_draw(c, t_pup.proc, 1, t);
            t_pup_hid_puppet = 0, t_pup.shown = 1;
        } else {
            if (t_pup.shown) t_pup.link_states++;
            pup_show_link(c, player, t);
            if (pup_draw(c, t_pup.proc, 0, t)) t_pup_hid_puppet = 1;
            t_pup.shown = 0;
        }
    } else if (t_pup.link_hidden) {
        pup_show_link(c, player, t);
    }
    t_pup.items = t_pup.state == PS_LIVE && t_items_ghost == t_pup.proc && t_pup.proc;
    lock();
    g_pup = t_pup;
    unlock();
}

// The held items' holder this tick and its request (ghost.c's items part): the ghost's, while a friend's ghost is
// wanted or up; else the puppet, holding copies of Link's own weapon, shield and bow (his back ones on its back, at
// his own offsets); else none.
static uint64_t pup_items_holder(const Requests *rq, Requests *out) {
    *out = *rq;
    if (t_status.wanted || t_status.state != ST_IDLE) return t_status.state == ST_LIVE ? t_status.actor : 0;
    if (t_pup.state != PS_LIVE || !t_pup.proc) return 0;
    const SelfState *s = &t_status.self;
    const char *names[3] = {s->weapon, s->shield, s->bow};
    out->items_on = 1, out->items_back = 1, out->items_mask = 0;
    for (int i = 0; i < 3; i++) {
        const char *n = s->equip_ok && valid_name(names[i]) && item_name_ok(i, names[i]) ? names[i] : "";
        memcpy(out->item_names[i], n, strlen(n) + 1);
        out->items_mask |= (s->back[i] ? 1 : 0) << i;
        out->item_has_rel[i] = s->has_rel[i] && *n;
        if (out->item_has_rel[i]) memcpy(out->item_rel[i], s->rel[i], sizeof out->item_rel[i]);
    }
    return t_pup.proc;
}

// The tick reads Link (as `ghost self`) while there is a puppet to drive.
static int pup_wants_self(void) { return t_pup.state == PS_WAIT || t_pup.state == PS_PENDING || t_pup.state == PS_LIVE; }

// For `iam`: one short sentence on what you see yourself as (out: "" when there is nothing to say).
static void pup_sentence(const PupStatus *p, const char *picked, char *out, size_t cap) {
    out[0] = 0;
    if (picked) {  // (just picked: the tick takes it next frame)
        const char *id = pup_id(picked);
        const PupSlot *s = *id ? pup_slot(id) : NULL;
        if (s) snprintf(out, cap, "You see yourself as %s in a moment (live, no restart).", s->name);
        else if (!strcmp(picked, "link") || !strcmp(picked, "linkle")) snprintf(out, cap, "You see yourself as Link.");
        return;
    }
    switch (p->state) {
    case PS_LIVE:
        if (p->shown) snprintf(out, cap, "You see yourself as %s.", p->name);
        else snprintf(out, cap, "You see yourself as %s when you can (%s).", p->name, *p->why ? p->why : "Link shows now");
        break;
    case PS_WAIT:
    case PS_PENDING:
        snprintf(out, cap, "Loading %s for you...", p->name);
        break;
    case PS_FAILED:
        if (*p->playas)
            snprintf(out, cap, "%s. Restart the game to play as %s: python tools\\charsel.py playas %s", p->why, p->name,
                     p->playas);
        else
            snprintf(out, cap, "%s.", p->why);
        break;
    }
}

// `ghost puppet [off|on|retry|lift m|map id=Actor,...]` (developer mode): the self puppet's state, or a change.
static void cmd_puppet(int argc, char **argv, SwModReply *r) {
    if (argc == 3 && (!strcmp(argv[2], "off") || !strcmp(argv[2], "on") || !strcmp(argv[2], "retry"))) {
        lock();
        if (argv[2][0] == 'r') g_pup_req.retry_seq++;
        else g_pup_req.self_off = argv[2][1] == 'f';
        unlock();
        api->reply_str(r, "puppet", argv[2]);
        return;
    }
    if (argc == 4 && !strcmp(argv[2], "lift")) {
        float v;
        if (!parse_float(argv[3], &v) || v < -PUP_LIFT_MAX || v > PUP_LIFT_MAX) {
            api->reply_fail(r, "usage: ghost puppet lift <m, %.0f to %.0f>", -PUP_LIFT_MAX, PUP_LIFT_MAX);
            return;
        }
        lock();
        g_pup_req.lift = v;
        unlock();
        api->reply_num(r, "lift", v);
        return;
    }
    if (argc == 4 && !strcmp(argv[2], "map")) {  // id=Actor,id=Actor (only the ids above; "-" clears)
        PupReq tmp;
        memset(&tmp, 0, sizeof tmp);
        const char *p = argv[3];
        int bad = 0;
        while (strcmp(argv[3], "-") && *p && !bad) {
            const char *e = strchr(p, ','), *eq = strchr(p, '=');
            size_t len = e ? (size_t)(e - p) : strlen(p);
            if (!eq || eq >= p + len || tmp.nmap >= PUP_MAP_MAX) {
                bad = 1;
                break;
            }
            size_t il = (size_t)(eq - p), al = len - il - 1;
            char id[IAM_WORD + 1] = {0}, actor[64] = {0};
            if (!il || il > IAM_WORD || !al || al > 63) {
                bad = 1;
                break;
            }
            memcpy(id, p, il), memcpy(actor, eq + 1, al);
            if (!pup_slot(id) || !valid_name(actor)) {
                bad = 1;
                break;
            }
            memcpy(tmp.map_id[tmp.nmap], id, sizeof id), memcpy(tmp.map_actor[tmp.nmap], actor, sizeof actor);
            tmp.nmap++;
            p += len + (e ? 1 : 0);
        }
        if (bad) {
            api->reply_fail(r, "usage: ghost puppet map id=Actor,id=Actor | - (ids: zelda, goron, ...)");
            return;
        }
        lock();
        g_pup_req.nmap = tmp.nmap;
        memcpy(g_pup_req.map_id, tmp.map_id, sizeof tmp.map_id);
        memcpy(g_pup_req.map_actor, tmp.map_actor, sizeof tmp.map_actor);
        unlock();
        api->reply_int(r, "map", tmp.nmap);
        return;
    }
    if (argc != 2) {
        api->reply_fail(r, "usage: ghost puppet [off | on | retry | lift m | map id=Actor,... | -]");
        return;
    }
    PupStatus p;
    PupReq q;
    lock();
    p = g_pup, q = g_pup_req;
    unlock();
    api->reply_str(r, "state", PS_NAMES[p.state >= 0 && p.state <= PS_FAILED ? p.state : 0]);
    api->reply_bool(r, "self_off", q.self_off != 0);
    api->reply_str(r, "id", p.id);
    api->reply_str(r, "name", p.name);
    api->reply_str(r, "actor", p.actor);
    reply_hex(r, "proc", p.proc);
    api->reply_bool(r, "shown", p.shown != 0);
    api->reply_bool(r, "link_hidden", p.link_hidden != 0);
    api->reply_bool(r, "as_ok", p.as_ok != 0);
    api->reply_str(r, "link_anim", p.link_anim);
    api->reply_str(r, "as_want", p.as_want);
    api->reply_str(r, "as", p.as);
    api->reply_num(r, "speed_mps", p.speed);
    api->reply_num(r, "lift", p.lift);
    api->reply_bool(r, "items", p.items != 0);
    api->reply_int(r, "tries", p.tries);
    api->reply_int(r, "requests", p.requests);
    api->reply_int(r, "spawned", p.spawned);
    api->reply_int(r, "drops", p.drops);
    api->reply_int(r, "hides", p.hides);
    api->reply_int(r, "shows", p.shows);
    api->reply_int(r, "rehides", p.rehides);
    api->reply_int(r, "link_states", p.link_states);
    api->reply_int(r, "as_calls", p.as_calls);
    api->reply_int(r, "misses", p.misses);
    api->reply_str(r, "missing", p.missing);
    api->reply_num(r, "live_s", p.state == PS_LIVE ? p.live_s : 0.0);
    api->reply_str(r, "why", p.why);
    char line[300];
    pup_sentence(&p, NULL, line, sizeof line);
    api->reply_text(r, "puppet %s%s%s: %s", PS_NAMES[p.state >= 0 && p.state <= PS_FAILED ? p.state : 0],
                    *p.name ? " " : "", p.name, *line ? line : "you see yourself as Link");
}

// iam.c's hooks: the sentence for `iam`, and `iam self on|off`.
// A save is loaded but this mod's frame tick never ran: another mod wraps the same frame job and loaded first (only
// the first runs: docs/game-basics.md, botw_on_frame; Infinite Stamina does, and basics_inf_stamina.dll sorts first).
// Seen in the game 2026-11-12 (out\showcase-lib): `iam daruk` did nothing, `ghost` said 0 ticks.
#define PUP_NO_TICK_TEXT "You stay Link: the ghost mod isn't running. Another mod (Infinite Stamina?) uses the same " \
                         "frame hook and starts first. Turn that one off and restart the game."
static int pup_in_world_real(void) { return botw_in_world(api); }
static int (*g_pup_in_world)(void) = pup_in_world_real;  // (the selftest fakes it)

static void pup_iam_text(const char *picked, char *out, size_t cap) {
    PupStatus *p = calloc(1, sizeof *p);
    out[0] = 0;
    if (!p) return;
    lock();
    *p = g_pup;
    int off = g_pup_req.self_off;
    int64_t ticks = g_status.ticks;
    unlock();
    if (ticks == 0 && g_pup_in_world()) snprintf(out, cap, "%s", PUP_NO_TICK_TEXT);
    else if (off) snprintf(out, cap, "You see yourself as Link (iam self on: as your pick).");
    else pup_sentence(p, picked, out, cap);
    free(p);
}
static void pup_set_self(int on) {
    lock();
    g_pup_req.self_off = !on;
    unlock();
}
