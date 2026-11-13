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
//   actor show sets it, its hide actions clear it [V code]). His armor and his items, in his hands or on his back,
//   copy his model's flags every frame (0x70012132d0(item, holder), called by the armor's frame update and by each
//   weapon's, Weapon vf_63 at 0x70007a83f8 [V code]), so they go with him;
// - gives the puppet copies of what Link holds in his hands (weapon, shield, bow: the ghost's held items, ghost.c)
//   while no friend's ghost is up (with a friend's ghost up, the items are the ghost's: the puppet's hands are
//   empty). Nothing goes on the puppet's back: Link's back places on another body poke out of it (Daruk's weapon over
//   his head, 2026-11-13), so a sheathed item is only on Link's back, hidden with him.
// The puppet can't be locked on, talked to or hit: its pack's actor link has no attention (lock-on, talk), no damage
// and no physics (tools/charsel.py check_pack). The green ring next to it while it sprints is Link's stamina wheel.
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
//
// 0.9.1 Ragdoll (docs/charsel.md "Ragdoll"). While Link ragdolls (his ragdoll manager's "on" bit, or a ragdoll or
// lying clip: a bomb, a Guardian beam, a big hit, a tumble down a slope, his death) and until he is up again, the
// puppet follows his POSE, not a clip: its animation is paused (the game's own "animations run" bit of each model
// unit, which the game clears itself to pose a model by hand), and every frame each of its bones named as one of
// Link's gets that bone's turn from his skeleton (his bone world matrices), its own bone lengths kept, its own extra
// bones following their parents, its hips at his. Then it is raised (or lowered) so its lowest point lies on the
// ground under him: the ground is fitted under Link's own body (his body boxes), the costume's lowest point comes
// from its body boxes (pup_bodies.h, measured from the game's models by charsel.py bodies) turned with its bones.
// If the pose can't be followed (a skeleton or the animation can't be read, too few bones named as Link's,
// `ghost puppet ragdoll clip`), it plays its own lying clip (Dead) on that ground instead. [S: the addresses below,
// from the code; G: all of it in the game]

#include "pup_bodies.h"

#define LINK_RAGDOLL 0x1870            // 0.9.1: Link: his ragdoll manager (his vf 0x840 returns it; his actions reach it)
#define RD_FLAGS 0x110                 // ragdoll manager: u32 flags; bit 13 is set while the ragdoll runs: its start
#define RD_ON 0x2000u                  // 0x70011e704c sets it, its end 0x70011e56ac clears it, and the game's own starts
                                       // (0x70011e7880, 0x70011e7908) take it as "already on" [S]
#define UNIT_ANIM 0x188                // model unit: its animation (vptr ANIM_VPTR, its unit at +ANIM_UNIT)
#define ANIM_VPTR 0x7001d0fc38ull      // (its constructor 0x7000f7e934 sets it, and ANIM_FLAGS = ANIM_RUNS)
#define ANIM_UNIT 0x08
#define ANIM_FLAGS 0x34                // u8; bit 3: its animations run (0x7000f7f0c8 does nothing without it; the game
#define ANIM_RUNS 0x08                 // clears it itself to pose a model by hand: 0x7000596438, 0x700059ed24) [S]
#define UNIT_DIRTY 0x170               // unit: u8; bit 6: its local transforms changed (the world ones are made again)
#define UNIT_LOCALS_NEW 0x40
#define SKEL_FLAGS 0x08                // skeleton: u16; the game's set-local (unit vf_9, 0x7000f95fa8) sets bit 1
#define SKEL_LOCALS_SET 0x02
#define SKEL_LOCAL 0x18                // skeleton: bone locals, LOCAL_SIZE each: u32 flags, scale at +0x10, then 4
#define LOCAL_SIZE 0x60                // columns of 4 floats from +LOCAL_COLS (translation in the 4th) [S: vf_9, vf_12]
#define LOCAL_COLS 0x20
#define LOCAL_FLAGS_KEEP 0xf0ffffffu   // (vf_9 clears bits 24-27 of a bone's flags when it sets its matrix)
#define RES_BONES 0x18                 // skeleton resource (FSKL): the bone array, RES_BONE_SIZE each: u16 its index at
#define RES_BONE_SIZE 0x50             // +RES_BONE_INDEX, s16 its parent at +RES_BONE_PARENT (tools/wiiu_model reads the
#define RES_BONE_INDEX 0x18            // same layout from the files) [S]
#define RES_BONE_PARENT 0x1a
#define PUP_MAXB 256                   // bones read per skeleton
#define PUP_RD_UNITS 4                 // the costume's model units posed (those with Link's bones; head parts follow)
#define PUP_RD_MIN_MAP 8               // ... a unit with fewer bones named as Link's isn't
#define PUP_RD_LEVEL 0.02              // the ground's gentle pull to level (m^2 a point)
#define PUP_RD_SLOPE 0.7f              // ... and its steepest (35 degrees)
#define PUP_RD_BAND 0.08f              // ... fitted again without the corners this far over the median (m)
#define PUP_RD_UP_COS 0.82f            // Link is up again: his spine within 35 degrees of straight up ...
#define PUP_RD_UP_HIP 0.65f            // ... and his hips this high over the ground (m; 0.99 standing)
#define PUP_RD_TAIL_S 0.3              // the pose is followed this long at least after the ragdoll ends ...
#define PUP_RD_GETUP_S 4.0             // ... and at most this long while he gets up
#define PUP_RD_EASE_S 0.2              // the lift goes down this gently (up: at once)
#define PUP_RD_LIFT_MAX 3.0f           // m: the lift's range (up)
#define PUP_RD_SINK_MAX 0.4f           // m: ... (down: a costume thinner than Link)
#define PUP_RD_RETRY_S 2.0             // a skeleton that can't be read is looked at again this often

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
    int rd_mode;                   // 0.9.1 `ghost puppet ragdoll pose|clip|off`: RDM_*
    float girth;                   // `ghost puppet girth x`: the body boxes times x (0: 1)
} PupReq;

enum { RDM_POSE, RDM_CLIP, RDM_OFF };
static const char *const RDM_NAMES[] = {"pose", "clip", "off"};
enum { RD_NONE, RD_POSE, RD_CLIP };
static const char *const RD_NAMES[] = {"no", "pose", "clip"};

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
    int rd;                        // 0.9.1: RD_*: what the puppet does for Link's ragdoll now
    char rd_src[8];                // ... what said he ragdolls: "flag", "anim", "getup" (after it, until he is up)
    char rd_why[80];               // ... why its lying clip, not his pose
    int rd_units, rd_mapped;       // ... model units posed, bones posed like his
    float rd_lift;                 // ... m it is raised (negative: lowered) to lie on the ground
    uint32_t rd_flags;             // his ragdoll manager's flags (0: not readable)
    uint64_t rd_mgr;
    int64_t rd_count, rd_frames;   // ragdolls followed, frames posed
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

// ---- 0.9.1 ragdoll, pure parts (the selftest checks them; docs/charsel.md "Ragdoll")
//
// While Link ragdolls (a bomb, a Guardian beam, a big hit, a slope's tumble, his death) the costume falls WITH him:
// each of its bones named as one of Link's takes that bone's turn (as if it played a Link clip of that very pose), its
// own bone lengths stay, its own extra bones follow their parents, and its hips go where his are. Then the body is
// raised (or lowered) until its lowest point lies on the ground under him: Daruk's torso is about 2 m wide where
// Link's is 0.4 m, so at Link's hip height most of Daruk went through the hillside (2026-11-13).

// Link ragdolls now: his ragdoll manager's "on" bit, or he plays a ragdoll or lying clip (a second source, should the
// manager's offset be wrong in this game version). mgr_ok: the manager was readable.
static int pup_rd_now(int mgr_ok, uint32_t flags, const char *anim) {
    if (mgr_ok && (flags & RD_ON)) return 1;
    static const char *const LYING[] = {"SlippingDown", "DeadRagdoll", "Dead", "Rolldown"};
    for (size_t i = 0; anim && i < sizeof LYING / sizeof *LYING; i++)
        if (pup_prefix(anim, LYING[i])) return 2;
    return 0;
}

// Bones in an order where each comes after its parent. 0: a parent out of range, or a loop.
static int pup_rd_order(const int16_t *parent, int n, int16_t *order) {
    uint8_t done[PUP_MAXB] = {0};
    int k = 0;
    if (n <= 0 || n > PUP_MAXB) return 0;
    for (int b = 0; b < n; b++)
        if (parent[b] < -1 || parent[b] >= n || parent[b] == b) return 0;
    while (k < n) {
        int added = 0;
        for (int b = 0; b < n; b++)
            if (!done[b] && (parent[b] < 0 || done[parent[b]])) done[b] = 1, order[k++] = (int16_t)b, added = 1;
        if (!added) return 0;
    }
    return 1;
}

// A 3x4 matrix (rows; translation in the 4th column) as a turn (orthonormal R, rows), a size k and a place t.
typedef struct {
    float R[9], t[3], k;
} PupX;

static float pup_len3(const float v[3]) { return __builtin_sqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]); }

// 0: the axes are degenerate (no turn can be read).
static int pup_x_from(const float m[12], PupX *x) {
    float c[3][3], len[3];
    for (int i = 0; i < 3; i++) c[i][0] = m[i], c[i][1] = m[4 + i], c[i][2] = m[8 + i];  // (column i)
    for (int i = 0; i < 3; i++) len[i] = pup_len3(c[i]);
    if (!(len[0] > 1e-6f && len[1] > 1e-6f && len[2] > 1e-6f)) return 0;
    float a[3], b[3], n[3];
    for (int r = 0; r < 3; r++) a[r] = c[0][r] / len[0];
    float d = a[0] * c[1][0] + a[1] * c[1][1] + a[2] * c[1][2];
    for (int r = 0; r < 3; r++) b[r] = c[1][r] - d * a[r];
    float lb = pup_len3(b);
    if (!(lb > 1e-6f)) return 0;
    for (int r = 0; r < 3; r++) b[r] /= lb;
    n[0] = a[1] * b[2] - a[2] * b[1], n[1] = a[2] * b[0] - a[0] * b[2], n[2] = a[0] * b[1] - a[1] * b[0];
    if (n[0] * c[2][0] + n[1] * c[2][1] + n[2] * c[2][2] < 0) n[0] = -n[0], n[1] = -n[1], n[2] = -n[2];  // (mirrored)
    for (int r = 0; r < 3; r++) x->R[r * 3 + 0] = a[r], x->R[r * 3 + 1] = b[r], x->R[r * 3 + 2] = n[r];
    x->t[0] = m[3], x->t[1] = m[7], x->t[2] = m[11];
    x->k = (len[0] + len[1] + len[2]) / 3.0f;
    return 1;
}

static void pup_r_mul(const float a[9], const float b[9], float o[9]) {  // a b
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++) o[i * 3 + j] = a[i * 3] * b[j] + a[i * 3 + 1] * b[3 + j] + a[i * 3 + 2] * b[6 + j];
}
static void pup_r_tmul(const float a[9], const float b[9], float o[9]) {  // a^T b
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++) o[i * 3 + j] = a[i] * b[j] + a[3 + i] * b[3 + j] + a[6 + i] * b[6 + j];
}

// A skeleton as the ragdoll needs it: parents, an order, the bones named as Link's (map: his index), the body box
// bones (key: PUP_BODY_BONES index) and the hips.
typedef struct {
    uint64_t unit, sk;
    int uidx, n, hip, head, nmap;
    int16_t parent[PUP_MAXB], order[PUP_MAXB], map[PUP_MAXB];
    int16_t keyidx[PUP_NBODY_BONES];  // bone index of each body box bone (-1: none)
} PupSkel;

// The costume's new bone transforms (locals, 3x4 rows) and where each bone then is (wp): a bone named as Link's turns
// as his does in the world (relative to its own parent: what a Link clip of that pose would set), keeping its own
// length (cur's translation); the hips go to hip_at; any other bone keeps cur. base: the costume's matrix (its size).
static void pup_rd_solve(const PupSkel *ps, const float (*wl)[12], const float (*cur)[12], const float base[12],
                         const float hip_at[3], float (*out)[12], PupX *wp) {
    PupX b;
    if (!pup_x_from(base, &b)) {
        memset(&b, 0, sizeof b);
        b.R[0] = b.R[4] = b.R[8] = b.k = 1.0f;
        b.t[0] = base[3], b.t[1] = base[7], b.t[2] = base[11];
    }
    for (int i = 0; i < ps->n; i++) {
        int p = ps->order[i];
        const PupX *par = ps->parent[p] < 0 ? &b : &wp[ps->parent[p]];
        PupX lc, lw;
        float Rl[9], tl[3];
        if (!pup_x_from(cur[p], &lc)) {
            memset(&lc, 0, sizeof lc);
            lc.R[0] = lc.R[4] = lc.R[8] = lc.k = 1.0f;
            lc.t[0] = cur[p][3], lc.t[1] = cur[p][7], lc.t[2] = cur[p][11];
        }
        memcpy(Rl, lc.R, sizeof Rl), memcpy(tl, lc.t, sizeof tl);
        if (ps->map[p] >= 0 && pup_x_from(wl[ps->map[p]], &lw)) {
            pup_r_tmul(par->R, lw.R, Rl);
            if (p == ps->hip) {
                float d[3] = {hip_at[0] - par->t[0], hip_at[1] - par->t[1], hip_at[2] - par->t[2]};
                for (int r = 0; r < 3; r++) tl[r] = (par->R[r] * d[0] + par->R[3 + r] * d[1] + par->R[6 + r] * d[2]) / par->k;
            }
        }
        for (int r = 0; r < 3; r++) {
            out[p][r * 4 + 0] = Rl[r * 3], out[p][r * 4 + 1] = Rl[r * 3 + 1], out[p][r * 4 + 2] = Rl[r * 3 + 2];
            out[p][r * 4 + 3] = tl[r];
        }
        pup_r_mul(par->R, Rl, wp[p].R);
        for (int r = 0; r < 3; r++)
            wp[p].t[r] = par->t[r] + par->k * (par->R[r * 3] * tl[0] + par->R[r * 3 + 1] * tl[1] + par->R[r * 3 + 2] * tl[2]);
        wp[p].k = par->k;
    }
}

// The 8 corners of body box k around a bone placed at x, scaled by s (cm in the box; m out).
static void pup_box_corners(const PupX *x, const short box[6], float s, float out[8][3]) {
    for (int i = 0; i < 8; i++) {
        float l[3] = {box[(i & 1) ? 1 : 0] * 0.01f * s, box[(i & 2) ? 3 : 2] * 0.01f * s, box[(i & 4) ? 5 : 4] * 0.01f * s};
        for (int r = 0; r < 3; r++)
            out[i][r] = x->t[r] + x->k * (x->R[r * 3] * l[0] + x->R[r * 3 + 1] * l[1] + x->R[r * 3 + 2] * l[2]);
    }
}

// The ground under a body lying as w says (Link, his bones placed: x per bone), as a plane y = a x + b z + c: fitted
// through the lowest corner of each body box (then again without the ones well above the rest: a raised knee; a
// gentle pull to level so a body lying across a slope doesn't tip it; at most PUP_RD_SLOPE steep), then lowered until
// no corner is under it.
// 0: no box could be placed.
static int pup_rd_ground(const PupX *x, const PupSkel *s, const PupBody *body, float scale, float plane[3]) {
    float q[PUP_NBODY_BONES][3];
    int nq = 0;
    for (int k = 0; k < PUP_NBODY_BONES; k++) {
        int b = s->keyidx[k];
        if (b < 0 || !body->has[k]) continue;
        float cs[8][3];
        pup_box_corners(&x[b], body->box[k], scale, cs);
        int lo = 0;
        for (int i = 1; i < 8; i++)
            if (cs[i][1] < cs[lo][1]) lo = i;
        memcpy(q[nq++], cs[lo], sizeof q[0]);
    }
    if (nq < 1) return 0;
    float a = 0, bz = 0, c = 0;
    uint8_t use[PUP_NBODY_BONES];
    memset(use, 1, sizeof use);
    for (int pass = 0; pass < 2; pass++) {
        double n = 0, mx = 0, my = 0, mz = 0;
        for (int i = 0; i < nq; i++)
            if (use[i]) n++, mx += q[i][0], my += q[i][1], mz += q[i][2];
        if (n < 1) break;
        mx /= n, my /= n, mz /= n;
        double sxx = PUP_RD_LEVEL * n, szz = PUP_RD_LEVEL * n, sxz = 0, sxy = 0, szy = 0;
        for (int i = 0; i < nq; i++) {
            if (!use[i]) continue;
            double dx = q[i][0] - mx, dy = q[i][1] - my, dz = q[i][2] - mz;
            sxx += dx * dx, szz += dz * dz, sxz += dx * dz, sxy += dx * dy, szy += dz * dy;
        }
        double det = sxx * szz - sxz * sxz;
        a = det > 1e-12 ? (float)((sxy * szz - szy * sxz) / det) : 0.0f;
        bz = det > 1e-12 ? (float)((szy * sxx - sxy * sxz) / det) : 0.0f;
        float g = __builtin_sqrtf(a * a + bz * bz);
        if (g > PUP_RD_SLOPE) a *= PUP_RD_SLOPE / g, bz *= PUP_RD_SLOPE / g;
        c = (float)(my - a * mx - bz * mz);
        if (pass == 0 && nq >= 6) {  // (the points the body rests on: not over the median by more than PUP_RD_BAND,
            float res[PUP_NBODY_BONES], sorted[PUP_NBODY_BONES];  // so a raised knee or arm doesn't tip it)
            for (int i = 0; i < nq; i++) res[i] = sorted[i] = q[i][1] - (a * q[i][0] + bz * q[i][2] + c);
            for (int i = 1; i < nq; i++)
                for (int j = i; j > 0 && sorted[j] < sorted[j - 1]; j--) {
                    float tmp = sorted[j];
                    sorted[j] = sorted[j - 1], sorted[j - 1] = tmp;
                }
            float med = sorted[(nq - 1) / 2];
            for (int i = 0; i < nq; i++) use[i] = res[i] <= med + PUP_RD_BAND;
        } else {
            break;
        }
    }
    float lowest = 1e30f;  // (lowered under every corner of every box)
    for (int k = 0; k < PUP_NBODY_BONES; k++) {
        int b = s->keyidx[k];
        if (b < 0 || !body->has[k]) continue;
        float cs[8][3];
        pup_box_corners(&x[b], body->box[k], scale, cs);
        for (int i = 0; i < 8; i++) {
            float h = cs[i][1] - (a * cs[i][0] + bz * cs[i][2]);
            if (h < lowest) lowest = h;
        }
    }
    plane[0] = a, plane[1] = bz, plane[2] = lowest;
    return 1;
}

// How far the body's lowest point is above the plane, straight up (m; negative: under it). 1e30: no box.
static float pup_rd_drop(const PupX *x, const PupSkel *s, const PupBody *body, float scale, const float plane[3]) {
    float lowest = 1e30f;
    for (int k = 0; k < PUP_NBODY_BONES; k++) {
        int b = s->keyidx[k];
        if (b < 0 || !body->has[k]) continue;
        float cs[8][3];
        pup_box_corners(&x[b], body->box[k], scale, cs);
        for (int i = 0; i < 8; i++) {
            float h = cs[i][1] - (plane[0] * cs[i][0] + plane[1] * cs[i][2] + plane[2]);
            if (h < lowest) lowest = h;
        }
    }
    return lowest;
}

// Link is up again (after a ragdoll, once he has got up): his spine within PUP_RD_UP_DEG of straight up and his hips
// PUP_RD_UP_HIP m over the ground. Unknown (no hips or head): yes.
static int pup_rd_upright(const PupX *x, const PupSkel *s, const float plane[3]) {
    if (s->hip < 0 || s->head < 0) return 1;
    const float *h = x[s->hip].t, *d = x[s->head].t;
    float up[3] = {d[0] - h[0], d[1] - h[1], d[2] - h[2]}, l = pup_len3(up);
    if (!(l > 1e-4f)) return 1;
    float over = h[1] - (plane[0] * h[0] + plane[1] * h[2] + plane[2]);
    return up[1] / l >= PUP_RD_UP_COS && over >= PUP_RD_UP_HIP;
}

// The body boxes for a pick (Link's for one with none: the Hylian's Mii-style parts).
static const PupBody *pup_body(const char *id) {
    for (size_t i = 0; i < sizeof PUP_BODIES / sizeof *PUP_BODIES; i++)
        if (!strcmp(PUP_BODIES[i].id, id)) return &PUP_BODIES[i];
    return &PUP_BODIES[0];  // (charsel.py writes link first)
}

// The costume's next shown lift: up at once (never through the ground), down gently (PUP_RD_EASE_S).
static float pup_rd_ease(float shown, float want, float dt) {
    if (want >= shown) return want;
    float f = dt > 0 ? dt / PUP_RD_EASE_S : 1.0f;
    return shown + (want - shown) * (f > 1.0f ? 1.0f : f);
}

// ---- the game side (the tick only)

// 0.9.1: the ragdoll's state (the tick's).
typedef struct {
    int on, mode;                  // following Link's ragdoll now (until he is up again); RD_POSE or RD_CLIP
    double end_t, last_t;          // when the ragdoll ended (0: it hasn't); the last frame
    float lift;                    // m the puppet is raised now (eased)
    uint64_t link_for;             // the Link actor read (his skeleton in link)
    double link_next;              // ... looked at again then, if it couldn't be read
    PupSkel link;
    uint64_t pup_for;              // the puppet read (its posed units in pu)
    double pup_next;
    int npu;
    PupSkel pu[PUP_RD_UNITS];
    uint64_t anim[PUP_RD_UNITS];   // each posed unit's animation we paused (0: not paused)
    uint8_t anim_was[PUP_RD_UNITS];
    char lnames[PUP_MAXB][32], names[PUP_MAXB][32];
    float wl[PUP_MAXB][12], cur[PUP_MAXB][12], out[PUP_MAXB][12];
    PupX xl[PUP_MAXB], wp[PUP_MAXB];
} PupRd;
static PupRd t_rd;
static int t_pup_rd_mode;          // the request's RDM_* and girth (pup_tick copies them)
static float t_pup_girth;

// The puppet went (or a stage change took it): its ragdoll state is forgotten without touching it.
static void pup_rd_forget(void) {
    t_rd.on = 0, t_rd.end_t = 0, t_rd.lift = 0, t_rd.pup_for = 0, t_rd.npu = 0;
    memset(t_rd.anim, 0, sizeof t_rd.anim);
    t_pup.rd = RD_NONE, t_pup.rd_lift = 0, t_pup.rd_units = t_pup.rd_mapped = 0;
}

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
    pup_rd_forget();
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

// ---- 0.9.1 ragdoll, the game side (plain reads; writes only the puppet's own bone locals and animation bit)

// Unit u of actor a's model (0: none, or a check failed).
static uint64_t pup_unit(uint64_t a, int u, double t) {
    uint64_t m = self_u64(a + ACTOR_MODEL, t);
    if (!m || self_u64(m, t) != MODEL_VPTR) return 0;
    int32_t n = (int32_t)self_u32(m + MODEL_NUNITS, t);
    if (u < 0 || u >= n || n > 64) return 0;
    uint64_t arr = self_u64(m + MODEL_UNITS, t), slot = arr ? self_u64(arr + (uint64_t)u * 8, t) : 0;
    uint64_t unit = slot ? self_u64(slot, t) : 0, vp = unit ? self_u64(unit, t) : 0;
    return vp == UNIT_VPTR_A || vp == UNIT_VPTR_B ? unit : 0;
}

// Unit u's skeleton as PupSkel: parents (the resource's bone array, each bone's own index checked), an order, the
// names into names, the body box bones, the hips and head. 0: not readable.
static int pup_skel_read(uint64_t a, int u, PupSkel *s, char (*names)[32], double t) {
    s->sk = s->unit = 0, s->n = s->nmap = 0, s->hip = s->head = -1, s->uidx = u;
    for (int k = 0; k < PUP_NBODY_BONES; k++) s->keyidx[k] = -1;
    uint64_t unit = pup_unit(a, u, t), sk = unit ? model_skel(a, u, NULL, t) : 0;
    int n = sk ? skel_nbones(sk, t) : 0;
    uint64_t res = sk ? self_u64(sk, t) : 0;
    uint64_t bones = res ? self_u64(res + RES_BONES, t) : 0, dict = res ? self_u64(res + RES_DICT, t) : 0;
    if (n <= 0 || n > PUP_MAXB || !bones || !dict || !mem_ok(bones, (uint64_t)n * RES_BONE_SIZE, t) ||
        !mem_ok(dict + 0x20, (uint64_t)n * 0x10, t))
        return 0;
    for (int b = 0; b < n; b++) {
        uint64_t e = bones + (uint64_t)b * RES_BONE_SIZE;
        if (*(volatile const uint16_t *)(uintptr_t)(e + RES_BONE_INDEX) != b) return 0;
        s->parent[b] = *(volatile const int16_t *)(uintptr_t)(e + RES_BONE_PARENT);
        s->map[b] = -1;
        uint64_t key = rd64(dict + 0x20 + (uint64_t)b * 0x10);
        if (!key || !self_text(key + 2, names[b], 32, t)) names[b][0] = 0;
        for (int k = 0; k < PUP_NBODY_BONES; k++)
            if (!strcmp(names[b], PUP_BODY_BONES[k])) s->keyidx[k] = (int16_t)b;
        if (!strcmp(names[b], "Skl_Root")) s->hip = b;
        if (!strcmp(names[b], "Head")) s->head = b;
    }
    if (!pup_rd_order(s->parent, n, s->order)) return 0;
    s->unit = unit, s->sk = sk, s->n = n;
    return 1;
}

// Link's skeleton (the unit with Skl_Root), read once per Link actor and checked each frame. 1: in t_rd.link.
static int pup_rd_link(uint64_t player, double t) {
    PupSkel *s = &t_rd.link;
    if (t_rd.link_for == player && s->sk && model_skel(player, s->uidx, NULL, t) == s->sk) return 1;
    if (t_rd.link_for == player && !s->sk && t < t_rd.link_next) return 0;
    t_rd.link_for = player, t_rd.link_next = t + PUP_RD_RETRY_S, s->sk = 0;
    t_rd.pup_for = 0;  // (the costume's bones are named against his again)
    int nunits = 0;
    model_skel(player, 0, &nunits, t);
    for (int u = 0; u < nunits && u < 8; u++)
        if (pup_skel_read(player, u, s, t_rd.lnames, t) && s->hip >= 0) return 1;
    s->sk = 0;
    return 0;
}

static void pup_rd_thaw(double t);

// The puppet's units to pose (up to PUP_RD_UNITS, each with PUP_RD_MIN_MAP bones named as Link's, its hips among
// them), read once per puppet and checked each frame. 1: at least one, in t_rd.pu.
static int pup_rd_puppet(uint64_t proc, double t) {
    if (t_rd.pup_for == proc && t_rd.npu) {
        int same = 1;
        for (int i = 0; i < t_rd.npu; i++) same &= model_skel(proc, t_rd.pu[i].uidx, NULL, t) == t_rd.pu[i].sk;
        if (same) return 1;
        pup_rd_thaw(t);  // (its model changed: what we paused runs again, then it is read anew)
        t_rd.pup_for = 0;
    }
    if (t_rd.pup_for == proc && t < t_rd.pup_next) return 0;
    if (t_rd.npu && t_rd.pup_for != proc) memset(t_rd.anim, 0, sizeof t_rd.anim);  // (another puppet: not ours to thaw)
    t_rd.pup_for = proc, t_rd.pup_next = t + PUP_RD_RETRY_S, t_rd.npu = 0;
    int nunits = 0;
    model_skel(proc, 0, &nunits, t);
    for (int u = 0; u < nunits && u < 8 && t_rd.npu < PUP_RD_UNITS; u++) {
        PupSkel *s = &t_rd.pu[t_rd.npu];
        if (!pup_skel_read(proc, u, s, t_rd.names, t)) continue;
        for (int b = 0; b < s->n; b++)
            for (int j = 0; *t_rd.names[b] && j < t_rd.link.n; j++)
                if (!strcmp(t_rd.names[b], t_rd.lnames[j])) {
                    s->map[b] = (int16_t)j, s->nmap++;
                    break;
                }
        if (s->nmap >= PUP_RD_MIN_MAP && s->hip >= 0 && s->map[s->hip] == t_rd.link.hip) t_rd.npu++;
    }
    return t_rd.npu > 0;
}

// Pause each posed unit's animation (the game's own "animations run" bit), so the pose written is the one drawn.
// 0: one can't be (its animation isn't the class the bit belongs to).
static int pup_rd_freeze(double t) {
    for (int i = 0; i < t_rd.npu; i++) {
        if (t_rd.anim[i]) continue;
        uint64_t unit = t_rd.pu[i].unit, a = self_u64(unit + UNIT_ANIM, t);
        if (!a || self_u64(a, t) != ANIM_VPTR || self_u64(a + ANIM_UNIT, t) != unit || !mem_ok(a + ANIM_FLAGS, 1, t))
            return 0;
        t_rd.anim_was[i] = __atomic_fetch_and((uint8_t *)(uintptr_t)(a + ANIM_FLAGS), (uint8_t)~ANIM_RUNS,
                                              __ATOMIC_SEQ_CST);
        t_rd.anim[i] = a;
    }
    return 1;
}

// Its animations run again (where we paused them and they ran before; only the same animation of the same unit).
static void pup_rd_thaw(double t) {
    for (int i = 0; i < PUP_RD_UNITS; i++) {
        uint64_t a = t_rd.anim[i];
        t_rd.anim[i] = 0;
        if (!a || i >= t_rd.npu || !(t_rd.anim_was[i] & ANIM_RUNS)) continue;
        if (self_u64(t_rd.pu[i].unit + UNIT_ANIM, t) == a && self_u64(a, t) == ANIM_VPTR)
            __atomic_fetch_or((uint8_t *)(uintptr_t)(a + ANIM_FLAGS), (uint8_t)ANIM_RUNS, __ATOMIC_SEQ_CST);
    }
}

// The bone world matrices of skeleton s (3x4 rows into w, and as PupX into x). 0: not readable.
static int pup_rd_worlds(const PupSkel *s, float (*w)[12], PupX *x, double t) {
    uint64_t arr = self_u64(s->sk + SKEL_WORLD, t);
    if (!arr || !mem_ok(arr, (uint64_t)s->n * 0x40, t)) return 0;
    for (int b = 0; b < s->n; b++) {
        uint64_t e = arr + (uint64_t)b * 0x40;
        for (int r = 0; r < 3; r++)
            for (int k = 0; k < 4; k++) {
                w[b][r * 4 + k] = rdf(e + 16 * (uint64_t)k + 4 * (uint64_t)r);
                if (!finite_f(w[b][r * 4 + k], 1e6f)) return 0;
            }
        if (!pup_x_from(w[b], &x[b])) {  // (a bone scaled to nothing: placed, not turned)
            memset(&x[b], 0, sizeof x[b]);
            x[b].R[0] = x[b].R[4] = x[b].R[8] = 1.0f;
            x[b].t[0] = w[b][3], x[b].t[1] = w[b][7], x[b].t[2] = w[b][11];
        }
    }
    return 1;
}

// The bone locals of skeleton s (3x4 rows: its 4 columns). 0: not readable.
static int pup_rd_locals(const PupSkel *s, float (*cur)[12], double t) {
    uint64_t arr = self_u64(s->sk + SKEL_LOCAL, t);
    if (!arr || !mem_ok(arr, (uint64_t)s->n * LOCAL_SIZE, t)) return 0;
    for (int b = 0; b < s->n; b++) {
        uint64_t e = arr + (uint64_t)b * LOCAL_SIZE + LOCAL_COLS;
        for (int r = 0; r < 3; r++)
            for (int k = 0; k < 4; k++) {
                cur[b][r * 4 + k] = rdf(e + 16 * (uint64_t)k + 4 * (uint64_t)r);
                if (!finite_f(cur[b][r * 4 + k], 1e6f)) return 0;
            }
    }
    return 1;
}

// Write the bones named as Link's (as the game's own set-local does: 4 columns, the flags' bits 24-27 cleared, the
// skeleton's and the unit's "locals changed" bits); the scale and every other bone stay.
static void pup_rd_write(const PupSkel *s, const float (*out)[12]) {
    uint64_t arr = rd64(s->sk + SKEL_LOCAL);  // (pup_rd_locals checked it this frame)
    for (int b = 0; b < s->n; b++) {
        if (s->map[b] < 0) continue;
        uint64_t e = arr + (uint64_t)b * LOCAL_SIZE;
        volatile float *f = (volatile float *)(uintptr_t)(e + LOCAL_COLS);
        for (int k = 0; k < 4; k++) {
            for (int r = 0; r < 3; r++) f[k * 4 + r] = out[b][r * 4 + k];
            f[k * 4 + 3] = 0.0f;
        }
        *(volatile uint32_t *)(uintptr_t)e &= LOCAL_FLAGS_KEEP;
    }
    __atomic_fetch_or((uint16_t *)(uintptr_t)(s->sk + SKEL_FLAGS), (uint16_t)SKEL_LOCALS_SET, __ATOMIC_SEQ_CST);
    __atomic_fetch_or((uint8_t *)(uintptr_t)(s->unit + UNIT_DIRTY), (uint8_t)UNIT_LOCALS_NEW, __ATOMIC_SEQ_CST);
}

// Put the puppet at matrix m (rows), dy higher (its rigid bodies too, for a pack with physics: as the ghost).
static void pup_place(struct Cpu *c, uint64_t proc, const float m[12], float dy) {
    g_pup_pos[0] = m[3], g_pup_pos[1] = m[7] + dy, g_pup_pos[2] = m[11];
    float rot[9] = {m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]};
    memcpy(g_pup_rot, rot, sizeof rot);
    gcall(c, SET_TRANSFORM, proc, P(g_pup_pos), P(g_pup_rot), 0, 0, 0, 0);
    if (rd64(proc + ACTOR_PHYSICS)) {
        float pm[12] = {rot[0], rot[1], rot[2], g_pup_pos[0], rot[3], rot[4], rot[5], g_pup_pos[1],
                        rot[6], rot[7], rot[8], g_pup_pos[2]};
        memcpy(g_pup_phys, pm, sizeof pm);
        GCALL2(c, PHYS_SET_MTX, proc, g_pup_phys);
    }
}

// The follow ends: its animations run again, its clip is asked again.
static void pup_rd_stop(double t) {
    pup_rd_thaw(t);
    t_rd.on = 0, t_rd.end_t = 0, t_rd.lift = 0;
    t_pup.rd = RD_NONE, t_pup.rd_lift = 0, t_pup.rd_units = t_pup.rd_mapped = 0;
    t_pup_asked = NULL, t_pup_seen = 0;
}

// The body boxes' size for this costume: 1, or (a body with no boxes of its own: Link's) its spine over his.
static float pup_rd_scale(const PupBody *pb, const PupSkel *s, const PupX *wp) {
    if (pb != &PUP_BODIES[0] || !strcmp(t_pup.id, "link")) return 1.0f;
    const PupSkel *l = &t_rd.link;
    if (s->hip < 0 || s->head < 0 || l->hip < 0 || l->head < 0) return 1.0f;
    float a[3], b[3];
    for (int r = 0; r < 3; r++) a[r] = wp[s->head].t[r] - wp[s->hip].t[r], b[r] = t_rd.xl[l->head].t[r] - t_rd.xl[l->hip].t[r];
    float la = pup_len3(a), lb = pup_len3(b), f = lb > 1e-3f ? la / lb : 1.0f;
    return f < 0.5f ? 0.5f : f > 3.0f ? 3.0f : f;
}

// Each frame while live, Link's ragdoll: RD_POSE: the puppet was placed and posed here; RD_CLIP: placed here on the
// ground under him, it plays its lying clip (pup_live asks for it); RD_NONE: he doesn't ragdoll (or `ragdoll off`).
static int pup_rd_tick(struct Cpu *c, uint64_t player, uint64_t proc, const SelfState *self, const float m[12],
                       double t) {
    double dt = t_rd.last_t > 0 && t > t_rd.last_t ? t - t_rd.last_t : 0;
    t_rd.last_t = t;
    uint64_t mgr = self_u64(player + LINK_RAGDOLL, t);
    int mgr_ok = mgr && mem_ok(mgr + RD_FLAGS, 4, t);
    uint32_t fl = mgr_ok ? self_u32(mgr + RD_FLAGS, t) : 0;
    t_pup.rd_mgr = mgr_ok ? mgr : 0, t_pup.rd_flags = fl;
    if (t_pup_rd_mode == RDM_OFF) {  // (`ghost puppet ragdoll off`: the 0.9.0 way, its clips at his place)
        if (t_rd.on) pup_rd_stop(t);
        return RD_NONE;
    }
    int now = pup_rd_now(mgr_ok, fl, self && self->found ? self->anim : "");
    if (!now && !t_rd.on) return RD_NONE;  // (his bones are read only while it matters)
    int link_ok = pup_rd_link(player, t) && pup_rd_worlds(&t_rd.link, t_rd.wl, t_rd.xl, t);
    float plane[3] = {0, 0, m[7]};  // (Link's skeleton unread: level, at his feet)
    if (link_ok && !pup_rd_ground(t_rd.xl, &t_rd.link, pup_body("link"), 1.0f, plane))
        plane[0] = plane[1] = 0, plane[2] = m[7];
    if (now) {
        if (!t_rd.on) t_rd.on = 1, t_rd.mode = RD_NONE, t_pup.rd_count++;
        t_rd.end_t = 0;
        snprintf(t_pup.rd_src, sizeof t_pup.rd_src, "%s", now == 1 ? "flag" : "anim");
    } else if (t_rd.on) {  // (he gets up: followed until he is up, PUP_RD_TAIL_S at least, PUP_RD_GETUP_S at most)
        if (!t_rd.end_t) t_rd.end_t = t;
        snprintf(t_pup.rd_src, sizeof t_pup.rd_src, "getup");
        int up = !link_ok || pup_rd_upright(t_rd.xl, &t_rd.link, plane);
        if ((up && t - t_rd.end_t >= PUP_RD_TAIL_S) || t - t_rd.end_t >= PUP_RD_GETUP_S) pup_rd_stop(t);
    }
    if (!t_rd.on) return RD_NONE;
    int first = t_rd.mode == RD_NONE;
    const char *why = "";
    if (t_pup_rd_mode == RDM_CLIP) why = "ghost puppet ragdoll clip";
    else if (!link_ok) why = "Link's skeleton can't be read";
    else if (!pup_rd_puppet(proc, t)) why = "its skeleton can't be read, or too few bones are named as Link's";
    else if (!pup_rd_freeze(t)) why = "its animation can't be paused";
    const PupBody *pb = pup_body(t_pup.id);
    float girth = t_pup_girth > 0 ? t_pup_girth : 1.0f, want = 0;
    int has_drop = 0, units = 0, mapped = 0;
    if (!*why) {  // his pose
        const float *h = t_rd.wl[t_rd.link.hip];
        float hip_at[3] = {h[3], h[7], h[11]}, base[12], sc = model_scale(proc, t);
        memcpy(base, m, sizeof base);  // (the costume is drawn at its matrix times its model's own scale)
        for (int k = 0; sc > 0.01f && sc < 100.0f && k < 12; k++) base[k] *= k % 4 == 3 ? 1.0f : sc;
        for (int i = 0; i < t_rd.npu; i++) {
            const PupSkel *s = &t_rd.pu[i];
            if (!pup_rd_locals(s, t_rd.cur, t)) continue;
            pup_rd_solve(s, t_rd.wl, t_rd.cur, base, hip_at, t_rd.out, t_rd.wp);
            if (!units) {  // (the body: its boxes)
                float d = pup_rd_drop(t_rd.wp, s, pb, girth * pup_rd_scale(pb, s, t_rd.wp), plane);
                if (d < 1e29f) want = -d, has_drop = 1;
            }
            pup_rd_write(s, t_rd.out);
            units++, mapped += s->nmap;
        }
        if (!units) why = "its bone transforms can't be read";
    }
    snprintf(t_pup.rd_why, sizeof t_pup.rd_why, "%s", why);
    t_pup.rd_units = units, t_pup.rd_mapped = mapped;
    const float *at = m;
    float gm[12];
    if (units) {
        if (t_rd.mode != RD_POSE) t_rd.mode = RD_POSE, first = 1;
        t_pup.rd_frames++;
    } else {  // its lying clip on the ground under his hips, his way round
        if (t_rd.mode != RD_CLIP) pup_rd_thaw(t), t_rd.mode = RD_CLIP, first = 1;
        float x = link_ok ? t_rd.xl[t_rd.link.hip].t[0] : m[3], z = link_ok ? t_rd.xl[t_rd.link.hip].t[2] : m[11];
        memcpy(gm, m, sizeof gm);
        gm[3] = x, gm[11] = z, gm[7] = plane[0] * x + plane[1] * z + plane[2];
        if (t_rd.npu && t_rd.pup_for == proc && pup_rd_worlds(&t_rd.pu[0], t_rd.cur, t_rd.wp, t)) {
            // (its own bones, as the game placed them last frame: lifted from where it is)
            float d = pup_rd_drop(t_rd.wp, &t_rd.pu[0], pb, girth * pup_rd_scale(pb, &t_rd.pu[0], t_rd.wp), plane);
            if (d < 1e29f) want = t_rd.lift - d, has_drop = 1;
        }
        at = gm;
    }
    if (!has_drop) want = 0;
    want = want < -PUP_RD_SINK_MAX ? -PUP_RD_SINK_MAX : want > PUP_RD_LIFT_MAX ? PUP_RD_LIFT_MAX : want;
    t_rd.lift = first ? want : pup_rd_ease(t_rd.lift, want, (float)dt);
    t_pup.rd = units ? RD_POSE : RD_CLIP, t_pup.rd_lift = t_rd.lift;
    pup_place(c, proc, at, t_rd.lift);
    return t_pup.rd;
}

// The clip the puppet plays for a ragdoll it can't follow: its lying death, else its stagger, else standing.
static const char *pup_rd_clip(const Missing *mi, int autoc) {
    if (!missing_has(mi, "Dead")) return "Dead";
    if (!missing_has(mi, "DamageS")) return "DamageS";
    return AN_NAMES[autoc >= AN_WAIT && autoc <= AN_RUN ? autoc : AN_WAIT];
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
    // 0.9.1: his ragdoll: followed (his pose) or its lying clip, placed on the ground under him; else at his place
    int rd = pup_rd_tick(c, player, proc, self, m, t);
    if (rd == RD_NONE) pup_place(c, proc, m, t_pup.lift);
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
    if (rd == RD_POSE) {  // (posed like him: no clip; its animation is paused until he is up)
        copy_name(t_pup.as_want, "");
        t_pup.why[0] = 0;
        return 1;
    }
    const char *pick = rd == RD_CLIP ? pup_rd_clip(&t_pup_missing, autoc)
                                     : pup_pick(t_pup.link_anim, t_pup.speed, autoc, &t_pup_sprint, &t_pup_missing);
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
    t_pup_rd_mode = rq.rd_mode, t_pup_girth = rq.girth;
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
// wanted or up; else the puppet, holding copies of what Link holds in his hands (a drawn weapon, a raised shield, an
// aimed bow; the copy comes 0.3-0.8 s after he draws it [G] and goes when he puts it away); else none. Nothing on the
// puppet's back: Link's own back items are hidden with him.
static uint64_t pup_items_holder(const Requests *rq, Requests *out) {
    *out = *rq;
    if (t_status.wanted || t_status.state != ST_IDLE) return t_status.state == ST_LIVE ? t_status.actor : 0;
    if (t_pup.state != PS_LIVE || !t_pup.proc) return 0;
    const SelfState *s = &t_status.self;
    const char *names[3] = {s->weapon, s->shield, s->bow};
    out->items_on = 1, out->items_back = 0, out->items_mask = 0;
    for (int i = 0; i < 3; i++) {
        const char *n =
            s->equip_ok && !s->back[i] && valid_name(names[i]) && item_name_ok(i, names[i]) ? names[i] : "";
        memcpy(out->item_names[i], n, strlen(n) + 1);
        out->item_has_rel[i] = 0;
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
    if (argc == 4 && !strcmp(argv[2], "ragdoll")) {  // 0.9.1: pose (the default), clip, off
        int mode = -1;
        for (int i = 0; i <= RDM_OFF; i++)
            if (!strcmp(argv[3], RDM_NAMES[i])) mode = i;
        if (mode < 0) {
            api->reply_fail(r, "usage: ghost puppet ragdoll pose | clip | off");
            return;
        }
        lock();
        g_pup_req.rd_mode = mode;
        unlock();
        api->reply_str(r, "ragdoll", RDM_NAMES[mode]);
        return;
    }
    if (argc == 4 && !strcmp(argv[2], "girth")) {  // 0.9.1: the body boxes times x (how much it is raised)
        float v;
        if (!parse_float(argv[3], &v) || v < 0.3f || v > 3.0f) {
            api->reply_fail(r, "usage: ghost puppet girth <0.3 to 3, 1 = as measured>");
            return;
        }
        lock();
        g_pup_req.girth = v;
        unlock();
        api->reply_num(r, "girth", v);
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
        api->reply_fail(r, "usage: ghost puppet [off | on | retry | lift m | ragdoll pose|clip|off | girth x | "
                           "map id=Actor,... | -]");
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
    api->reply_str(r, "ragdoll", RD_NAMES[p.rd >= 0 && p.rd <= RD_CLIP ? p.rd : 0]);  // 0.9.1
    api->reply_str(r, "ragdoll_mode", RDM_NAMES[q.rd_mode >= 0 && q.rd_mode <= RDM_OFF ? q.rd_mode : 0]);
    api->reply_str(r, "ragdoll_src", p.rd ? p.rd_src : "");
    api->reply_str(r, "ragdoll_why", p.rd_why);
    api->reply_int(r, "ragdoll_units", p.rd_units);
    api->reply_int(r, "ragdoll_bones", p.rd_mapped);
    api->reply_num(r, "ragdoll_lift", p.rd_lift);
    api->reply_num(r, "girth", q.girth > 0 ? q.girth : 1.0);
    reply_hex(r, "ragdoll_mgr", p.rd_mgr);
    reply_hex(r, "ragdoll_flags", p.rd_flags);
    api->reply_int(r, "ragdolls", p.rd_count);
    api->reply_int(r, "ragdoll_frames", p.rd_frames);
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
