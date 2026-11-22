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
//   empty). 0.9.3: and of what he carries on his back, on its own back; each one on our own bone follow with the
//   body's grip (pup_item_grip, ghost.c item_place_pup), never the game's own place for an NPC holder.
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
//
// 0.9.2 (the 0.9.1 game check, 2026-11-13: Daruk's bomb and death ragdolls passed; three things didn't):
// - Getting up, Daruk floated 0.3-0.5 m for 1-1.6 s, and the lift jumped up to 1 m a frame in a tumble. The lowest
//   point is now each body box's ROUND flesh (the ellipsoid inside the box: a 2 m belly tilted forward reached as low
//   as its box's corner), the ground is levelled as Link stands up and its slope smoothed, the costume stands on its
//   feet as he gets upright (pup_rd_upness), and the lift goes up fast, down more gently, at most so many m/s
//   (pup_rd_follow).
// - Sliding fast down a hill, Link showed for 0.4 s: his real position's 1 m check against the player info's copy (a
//   teleport's guard, botw_player_real_pos) said "no real position". Link now shows only for the game's own states
//   (pup_busy_real: loading, an event, no body of his own), each held PUP_BUSY_S first.
// - Zelda's animation "can't be paused" and she played her lying clip, bobbing 0.4-1.66 m. Every unit's animation is
//   now held: paused (also one another unit owns), nothing (a unit with none), or posed again right after the game's
//   own step (unit_step_wrapper) for one of another class. The clip's lift no longer feeds on its last lift.
// 0.9.2, round 3 (the user as Daruk, 2026-11-13: "he just rolled around like a physics-less ball", then "the bomb
// ragdoll just makes him walk, I can see him walking while falling through a hill"):
// - The pose never reached the screen: the game puts a unit's clip into its bone locals BEFORE the unit's own step
//   (0x7001051914: the unit's animation list, then vf_79 0x7000f969ac, then its world matrices), and these costumes'
//   clips don't go through the unit's +0x188 animation at all (the user's game: Paya's and the Hylian's units have
//   none there, yet they play Wait). So the "runs" bit paused nothing, the clip overwrote the pose written by the
//   tick, and the costume showed its last clip (walk, or Daruk's rolling ball) placed at Link's tumbling matrix. Now
//   EVERY posed unit gets the whole pose right after its own step, before its world matrices are made
//   (unit_step_wrapper, all units, all bones): no clip can show during a ragdoll.
// - Link's own "walking" states after the tumble: on a steep hill he gets up into Slide / SlideBrake / SlideLand, which
//   the packs lack, so the costume fell back to Walk/Run by his speed at his feet, half in the hill. The pose is now
//   followed through every lying, sliding and knock-down clip (pup_rd_now), the ground follows his motion's slope as
//   he slides (never under the hill), and he is "up" only when standing (pup_rd_upness).
// - The retarget is Link's turn relative to his rest, put on the costume's own rest (each skeleton's rest read from
//   its resource), and the bones not named as Link's go to their own rest (hair, beard, cloth): Daruk's 21 main bones
//   rest exactly as Link's (measured), but the face and Zora's ears were 20-112 degrees off, the monsters' necks 40-82.
// - Loose limbs: the arms and legs swing behind the body's own motion (a damped spring per limb, `ghost puppet limp`).
// - Daruk's sprint ball rolls: turned about the axis across his motion at speed / radius, its lowest point on the
//   ground (the slope from Link's motion), started and stopped smoothly, facing the way he goes (pup_ball_*).
// - A costume with no own ragdoll: no Goron or Champion pack has a ragdoll (Physics/Ragdoll/*.hkrg: only Link's and
//   the monsters', Bokoblin, Moblin, Lizalfos, Lynel, Hinox), so the pose stays the way.

// 0.9.4 (2026-11-14, not yet in the game): Linkle's costume (PUP_SLOTS; no pack: `iam linkle` says what to do); the
// held weapon's swing (pup_weapon_pick: `_Lsword` / `_Spear`, the monsters' own swings timed to Link's hit by their
// packs' frame control, tools/charsel.py BOKO_CTRL / MOBLIN_CTRL). The hips of a body playing Link's clips without the
// retarget (Mipha 0.24 m up, Urbosa 0.35 m down) are not lifted by a fixed amount here: the ground clamp
// (pup_clamp_tick) already puts its posed feet at Link's each frame, and a fixed lift on top would count it twice.

// 0.9.5 (the round-4 game check, 2026-11-14; docs/charsel.md "Round 5"): the copies on the back never sheathed by the
// game (pup_holder_in_hand: what showed was the NPC's own sheath place, sideways at its Pod_A); Link's nocked arrow
// hidden with him (pup_arrow_tick); Paya's and Urbosa's arm is Link's while that hand holds a drawn copy
// (PupGrip.arm); Daruk's ball held while Link is in his sprint whatever his speed (his stamina falls: pup_in_sprint);
// no tries spent while the game is busy (Game Over), and a pack given up then comes back after a Continue; an attack
// starts at once, as Link's own (pup_as_morph: the Bokoblin's swing came 0.16 s late).

// 0.9.6 (2026-11-14, not yet in the game; docs/charsel.md "Cloth and hair"): the packs now carry their character's own
// cloth physics, cloth only (tools/charsel.py cloth_physics: no rigid body, no character controller, no contact). The
// bones that physics moves (the skirt, the hair, the scarf: PUP_CLOTH in pup_bodies.h, by name, never one named as
// Link's) are the physics' alone: the ragdoll pose (pup_rd_write, the after-step write) and the hands overlay never
// write them, so the pose and the cloth don't fight (jitter). The puppet's physics is never moved by the physics call
// (PHYS_SET_MTX: no rigid body to move; `ghost puppet physmtx warp|on` tries it). A new pick, a stage change and a
// respawn spawn a new puppet (a fresh cloth); a jump of more than PUP_WARP_M in one frame without one (a teleport on
// the same stage) is counted (`warps`): no cloth reset is known, so the skirt may whip once there [G].

// 0.9.9 (2026-11-18 game check: a hidden pod is shown again by the game each frame, `sheath_hides` ~110/s; not yet in
// the game; docs/charsel.md "Sheath: why hiding it each frame doesn't stick"): the hidden pods' model units go into a
// small table (pup_pod_units_publish) and get their draw bit cleared again right after the game's own step of each
// unit (unit_step_wrapper), i.e. after the game's re-show and before the frame is drawn (`sheath_unit_hides`).

// 0.9.10 (the game check: the costume walked on ~0.8 s after Link stopped, and slid ~0.2 s before its walk started;
// not yet in the game): Link's own Wait was shown by his speed, worked out over half a second (ANIM_WINDOW_S) and
// lowered only after ANIM_DOWN_S, so the stop came about 0.8 s late. Now his own name decides: Wait (and his Brake,
// which the packs lack) is the costume's stand at once, unless his place still moves over the last PUP_NOW_S (he moves
// before his name says Move: it walks at once). A stand, walk or run blends in over PUP_MOVE_MORPH frames, not the
// sets' own 6 (0.2 s, which read as a slide). The sprint, the near names and the ball are unchanged.

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
#define RES_FLAGS 0x38                 // 0.9.2: skeleton resource: u32 flags; RES_EULER: the bones' turns are Euler XYZ
#define RES_EULER 0x1000u              // (else quaternions) (tools/wiiu_model/nx_model.py reads the same)
#define RES_BONE_SCALE 0x28            // ... each bone's rest: scale (3 floats), turn (4: x y z, w or 1), place (3)
#define RES_BONE_ROT 0x34
#define RES_BONE_TRANS 0x44
#define PUP_MAXB 256                   // bones read per skeleton
#define PUP_RD_UNITS 4                 // the costume's model units posed (those with Link's bones; head parts follow)
#define PUP_RD_MIN_MAP 8               // ... a unit with fewer bones named as Link's isn't
#define PUP_RD_LEVEL 0.02              // the ground's gentle pull to level (m^2 a point)
#define PUP_RD_SLOPE 0.7f              // ... and its steepest (35 degrees)
#define PUP_RD_BAND 0.08f              // ... fitted again without the points this far over the median (m)
#define PUP_RD_SLOPE_S 0.25            // ... its slope smoothed over this long (a tumbling body's fit jumps)
#define PUP_RD_UP_COS 0.82f            // Link is up again: his spine within 35 degrees of straight up ...
#define PUP_RD_UP_HIP 0.65f            // ... and his hips this high over the ground (m; 0.99 standing)
#define PUP_RD_UP_U 0.9f               // ... or as upright as this (pup_rd_upness: 1 standing, about 0.8 sliding)
#define PUP_RD_MOVE_FROM 1.0f          // m/s: the ground's slope comes from Link's motion from this speed ...
#define PUP_RD_MOVE_FULL 2.5f          // ... fully from this one (a slide, a tumble down a hill)
#define PUP_RD_VEL_S 0.15              // ... his velocity smoothed over this long
#define PUP_LIMP 0.5f                  // `ghost puppet limp`: how loosely the arms and legs swing (0: exactly his)
#define PUP_LIMP_HZ 2.2f               // ... a limb's spring (Hz) and damping ratio
#define PUP_LIMP_ZETA 0.35f
#define PUP_LIMP_MAX 0.9f              // ... at most this far (rad) off his
#define PUP_LIMP_N 4                   // the limbs: Arm_1_L, Arm_1_R, Leg_1_L, Leg_1_R
#define PUP_BALL_SPIN_S 0.15           // Daruk's ball: its spin follows speed / radius this gently ...
#define PUP_BALL_END_S 0.08            // ... it comes back upright this fast when the sprint ends ...
#define PUP_BALL_END_MAX 0.4           // ... within this long (then his next clip)
#define PUP_BALL_R_MIN 0.3f            // m: its radius (from its body boxes, posed by its clip) at least
#define PUP_RD_TILT_COS 0.5f           // how upright he is: 0 with his spine 60 degrees or more off straight up, or
#define PUP_RD_LOW_HIP 0.35f           // ... his hips this low over his lowest point (m); 1 from the two above
#define PUP_RD_TAIL_S 0.3              // the pose is followed this long at least after the ragdoll ends ...
#define PUP_RD_GETUP_S 4.0             // ... and at most this long while he gets up
#define PUP_RD_RISE_S 0.03             // the lift goes up fast (this time constant) ...
#define PUP_RD_RISE_MPS 15.0f          // ... at most this many m/s (a 1 m jump in one frame is spread over 2) ...
#define PUP_RD_FALL_S 0.25             // ... down more gently while he lies ...
#define PUP_RD_FALL_MPS 2.5f           // ... (m/s) ...
#define PUP_RD_STAND_S 0.08            // ... and down fast as he stands up (to the standing height)
#define PUP_RD_STAND_MPS 6.0f
#define PUP_RD_LIFT_MAX 3.0f           // m: the lift's range (up)
#define PUP_RD_SINK_MAX 0.4f           // m: ... (down: a costume thinner than Link)
#define PUP_RD_SINK_OK 0.15f           // m: standing, the body's lowest point may be this far under the ground (its feet on it)
#define PUP_RD_RETRY_S 2.0             // a skeleton that can't be read is looked at again this often
#define UNIT_ANIM_STEP 0x7000f969acull // model unit vf_79 (both unit classes; only called through the vtable [S]): its
                                       // animation's step (0x7000f7f0c8, which writes the clip's bone locals), then
                                       // "locals changed"; the world transforms are made from the locals after it
#define PUP_BUSY_S 0.15                // a state that shows Link (an event, no body of his own) must last this long
#define PUP_EVENT_MGR 0x7001e3a638ull  // u64: the event manager (zelda_view's EVENT_MGR_PTR) ...
#define PUP_EVENT_RUNNING 0x1d2b8      // ... u64: not 0 while an event (a cutscene, a talk) runs [S, not yet run]

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
#define PUP_NOW_S 0.1                  // 0.9.10: s of Link's own place his "now" speed is worked out over ...
#define PUP_NOW_MOVING 0.4f            // ... m/s: from this, Link's Wait still moves (he starts before his Move shows)
#define PUP_MOVE_MORPH 2.0f            // 0.9.10: frames a stand, walk or run blends in (the sets' own 6 read as a slide)
#define PUP_VERIFY_S 0.15              // a name that doesn't play this long after the call is a miss ...
#define PUP_STRIKES 2                  // ... and after this many misses the pack lacks it
#define PUP_READ_S 0.25                // what plays is read back this often (the actor's own AI may change it)
#define PUP_REASSERT_S 1.0             // ... and asked again at most this often
#define PUP_LIFT_MAX 3.0f              // m: `ghost puppet lift` range (either way)
#define PUP_MAP_MAX 16                 // `ghost puppet map` entries
#define PUP_WARP_M 6.0f                // 0.9.6: m Link may move in one frame and still be "moving" (a warp beyond)

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
    // (0.9.8: no Linkle costume any more: she is Link's own body with The Linkle Mod on, picked in Breathalyzer with
    // one restart; iam.c answers `iam linkle`, and Link shows: out/research/linkle-body-swap-plan.md)
    // 0.9.7: a VRM avatar `charsel import-vrm` put on Link's skeleton (tools/vrm_import.py IMPORT_SLOT), Link's clips
    {"custom", "Dm_Npc_Handmaid_A", "Custom", ""},
};
static const struct {
    const char *alias, *id;
} PUP_ALIASES[] = {  // (`iam daruk` with no client list keeps the word as typed: it still finds Daruk)
    {"princess", "zelda"}, {"hateno", "hylian"}, {"villager", "hylian"}, {"paya", "sheikah"}, {"urbosa", "gerudo"},
    {"daruk", "goron"},    {"revali", "rito"},   {"mipha", "zora"},      {"boko", "bokoblin"}, {"moriblin", "moblin"},
    // (an imported costume is "custom"; vrm_import.py IMPORT_ALIASES lists any extra words. Linkle: no costume)
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
    int limp_set;                  // 0.9.2 `ghost puppet limp x`: how loosely its limbs swing (not set: PUP_LIMP)
    float limp;
    int ball_off;                  // 0.9.2 `ghost puppet ball off|on`: Daruk's sprint ball rolls (on by default)
    int clamp_off;                 // 0.9.3 `ghost puppet clamp off|on`: the ground clamp in every state (on by default)
    int hands_off;                 // 0.9.3 `ghost puppet hands off|on`: fists and the guard arm (on by default)
    int grip_set;                  // 0.9.3 `ghost puppet grip s [x y z]`: the held copies' size and grip (m) instead of
    float grip_scale, grip_hand[3];// the body's (PUP_GRIPS)
    int back_set;                  // 0.9.5 `ghost puppet back x y z`: the back place's tweak (m) instead of the body's
    float back[3];
    int physmtx;                   // 0.9.6 `ghost puppet physmtx off|warp|on`: PM_* (off by default)
} PupReq;

// 0.9.6: the physics call on the puppet (PHYS_SET_MTX: moves an actor's rigid bodies): never (its pack's physics is
// cloth only: no body to move), once after each warp (whether it resets the cloth: [G]), or every frame (as the ghost
// does for actors with rigid bodies). Not known in the game: whether the cloth needs it to follow [G].
enum { PM_OFF, PM_WARP, PM_ON };
static const char *const PM_NAMES[] = {"off", "warp", "on"};

// 0.9.6: the free actor names charsel builds its packs on (tools/charsel.py FREE_SLOTS; the test checks the two):
// any pack of ours, wherever `--slot` put it. Their physics is cloth only or none, so the ghost never moves it with
// the physics call either (ghost.c).
static const char *const PUP_FREE_NAMES[] = {
    "Dm_Npc_Sheiker_Searcher_M_000", "Dm663_Npc_Assasin_A", "Dm663_Npc_Assasin_B",
    "Dm_Npc_UMii_Hylia_BodyS_M_001", "Dm_Npc_UMii_Hylia_BodyS_M_002", "Dm_Npc_UMii_Hylia_BodyS_M_003",
    "Dm_Npc_UMii_Hylia_BodyS_M_011", "Dm_Npc_UMii_Hylia_BodyS_M_012", "Dm_Npc_Handmaid_A", "Dm_Npc_Handmaid_B",
    "Dm663_Npc_Gerudo_S_A", "Dm663_Npc_Gerudo_S_B", "Dm663_Npc_Gerudo_S_C", "Dm663_Npc_Gerudo_S_D",
    "Dm663_Npc_Gerudo_S_E", "Dm663_Npc_Gerudo_S_F",
};
static int pup_free_name(const char *actor) {
    for (size_t i = 0; actor && i < sizeof PUP_FREE_NAMES / sizeof *PUP_FREE_NAMES; i++)
        if (!strcmp(PUP_FREE_NAMES[i], actor)) return 1;
    return 0;
}

enum { RDM_POSE, RDM_CLIP, RDM_OFF };
static const char *const RDM_NAMES[] = {"pose", "clip", "off"};
enum { RD_NONE, RD_POSE, RD_CLIP };
static const char *const RD_NAMES[] = {"no", "pose", "clip"};
// 0.9.2: how each posed unit's animation is held: paused (the "runs" bit), paused although another unit owns it, none
// to hold, or posed again after the game's own step (unit_step_wrapper: an animation of another class)
enum { AK_UNSET, AK_PAUSED, AK_SHARED, AK_NONE, AK_AFTER };
static const char *const AK_NAMES[] = {"?", "paused", "shared", "none", "after"};
// 0.9.2: why Link shows now, from the game's own states (0: he is free)
enum { PB_FREE, PB_WORLD, PB_EVENT, PB_BODY };
static const char *const PB_NAMES[] = {"", "loading", "event", "no_body"};
static const char *const PB_WHY[] = {"", "loading", "an event or a cutscene", "riding or loading (no body of his own)"};

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
    char rd_why[120];              // ... why its lying clip, not his pose
    int rd_units, rd_mapped;       // ... model units posed, bones posed like his
    float rd_lift;                 // ... m it is raised (negative: lowered) to lie on the ground
    uint32_t rd_flags;             // his ragdoll manager's flags (0: not readable)
    uint64_t rd_mgr;
    int64_t rd_count, rd_frames;   // ragdolls followed, frames posed
    char rd_anim[72];              // 0.9.2: how each posed unit's animation is held ("u0 paused", "u0 after 0x7001...")
    float rd_up, rd_want;          // ... how upright Link is (0 lying, 1 standing); the lift it goes to
    int busy;                      // ... PB_*: why Link shows now (after PUP_BUSY_S)
    int event;                     // ... the event manager: 1 an event runs, 0 none, -1 unreadable, -2 not trusted yet
    float real_gap, real_gap_max;  // ... m: his real position's lead over the copy now, and the most since the pick
    int64_t gap_frames;            // ... frames the old 1 m check would have shown Link (a fast slide); none now
    int rd_rest;                   // ... rests read: 1 Link's, 2 the costume's (both: the turns go rest to rest)
    int ball;                      // ... Daruk's sprint ball rolls now (1), comes back upright (2)
    float ball_r, ball_spin, ball_angle;  // ... its radius (m), spin (rad/s) and turn (rad, 0 upright)
    int64_t balls;                 // ... sprints rolled
    float clamp, clamp_want;       // 0.9.3: m the ground clamp raises it now (negative: lowers), and its aim
    char clamp_by[8];              // ... "feet" (its lowest foot at Link's), "head" (a swim: its head at his), "" none
    int hands;                     // ... OVL_* the hand overlay shows now (fists, the guard arm)
    int hand_bones;                // ... bones it poses
    int64_t hand_frames;
    float grip_scale, grip_hand[3];// ... the held copies' grip in use
    // 0.9.5 (the round-4 game check)
    float back[3];                 // the back place's tweak in use (m)
    int holder;                    // the costume's own equipment holder (an NPC's): 1 found (its slots kept "in hand")
    int64_t holder_writes;         // ... "on the back" bytes put back to 0 (the item's AI then never sheathes it)
    char arrow[48];                // Link's nocked arrow ("" none seen)
    int arrow_hidden;
    int64_t arrow_hides;
    // 0.9.8: our copies' sheaths and quivers (the game's pods, at the NPC's own Pod_A), hidden
    char sheath[48];               // the last one seen ("" none)
    int sheaths;                   // how many are hidden now
    int64_t sheath_hides;          // times one was hidden (it climbs when the game shows one again each frame)
    float sprint_since;           // s since Link was last in his sprint (-1: not yet)
    float stamina;                 // his stamina (-1: unreadable)
    int64_t busy_waits;            // tries not spent because the game was busy (an event, Game Over, a load, no body)
    int64_t comebacks;             // a pack given up after a busy time spawned again by itself (a Continue)
    // 0.9.6 (cloth)
    int cloth_bones;               // bones of its posed units its own physics moves (never written by the pose, hands)
    int physics;                   // it has physics (its pack's cloth: an actor physics object)
    int64_t warps;                 // jumps of more than PUP_WARP_M in one frame on the same stage (a whip risk)
    int64_t phys_moves;            // PHYS_SET_MTX calls (`ghost puppet physmtx warp|on`)
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
static int pup_busy_real(uint64_t pi, uint64_t player, double t);
static int pup_paused_real(void) { return botw_paused(api); }
static int (*g_pup_busy)(uint64_t pi, uint64_t player, double t) = pup_busy_real;  // (the selftest fakes these two)
static int (*g_pup_paused)(void) = pup_paused_real;
static double t_pup_busy_since;                     // 0.9.2: a state that shows Link has lasted since then (0: none)
static int t_pup_ev_zero;                           // ... the event manager's field was seen 0 (else it isn't trusted)
static int g_pup_check_files = 1;                   // (the selftest has no overlay)
// 0.9.5 (the round-4 game check: Game Over up for about a minute spent all 5 tries, "Mipha did not load ...: you stay
// Link", and after Continue Link stayed): no request while the game is busy (an event such as Game Over, a load, Link
// with no body), a try that fails while it is busy isn't counted, and a pack that spawned before and was given up
// comes back by itself on a new stage or a new Link (a Continue)
static float pup_stamina_real(void) { return botw_stamina_get(api); }
static float (*g_pup_stamina)(void) = pup_stamina_real;  // (the selftest fakes it)
static int t_pup_busy_now;                          // the game's own state this tick (PB_*, before pup_busy_keep)
static int t_pup_try_busy;                          // ... busy since the last request: a failed try isn't counted
static int t_pup_pick_spawned;                      // this pick spawned once (a soft give-up may come back)
static int t_pup_soft_fail;                         // the give-up was the game's (refused, removed, couldn't create)
static uint64_t t_pup_tick_player, t_pup_fail_heap, t_pup_fail_player;  // Link this tick; the stage and Link then

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

// 0.9.10: Link's speed over the last `window` s of his own place (the frames anim_sample keeps; 0 with fewer than two).
static float pup_now_speed(const AnimAuto *a, double window) {
    if (a->n < 2) return 0;
    int h = a->head, i = h;
    for (int k = 1; k < a->n; k++) {
        i = (h - k + ANIM_SAMPLES) % ANIM_SAMPLES;
        if (a->t[h] - a->t[i] >= window) break;
    }
    double dt = a->t[h] - a->t[i];
    if (!(dt > 0)) return 0;
    float dx = a->x[h] - a->x[i], dz = a->z[h] - a->z[i];
    return (float)(__builtin_sqrtf(dx * dx + dz * dz) / dt);
}

// What the puppet plays while Link plays `link` at v m/s (auto: Wait/Walk/Run by his speed; *sprint: the sprint's
// hysteresis), or NULL: it can't be mirrored, show Link (0.9.3: only a horse, a ride, a surf, a cutscene's own move;
// a clip the pack lacks is never Link). now_v: his speed over the last PUP_NOW_S (pup_now_speed). m: the names this
// puppet lacks.
static const char *pup_pick(const char *link, float v, float now_v, int autoc, int *sprint, const Missing *m) {
    const char *speed = AN_NAMES[autoc >= AN_WAIT && autoc <= AN_RUN ? autoc : AN_WAIT];
    if (!*link || !strcmp(link, "-") || !strcmp(link, "?")) {  // (his name isn't known: by his speed)
        *sprint = 0;
        return speed;
    }
    // 0.9.10: his own stand (or his Brake, which the packs lack) is the costume's at once; by the half-second speed it
    // walked on ~0.8 s after he stopped. Only while his place still moves (he starts before his Move shows) it walks.
    int brake = !strcmp(link, "Brake") && missing_has(m, "Brake");
    if (!strcmp(link, "Wait") || !strcmp(link, "DemoWait") || brake) {
        *sprint = 0;
        if (brake || now_v < PUP_NOW_MOVING) return AS_WAIT;
        return autoc == AN_WAIT ? AS_WALK : speed;
    }
    if (!strcmp(link, "Move")) {  // walk, run and sprint: by speed
        *sprint = v >= (*sprint ? PUP_SPRINT_BELOW : PUP_SPRINT_FROM);
        if (*sprint) return missing_has(m, "RunGear2") ? AS_RUN : "RunGear2";
        return autoc == AN_WAIT ? AS_WALK : speed;  // (Move while the speed still says Wait: he starts to walk)
    }
    *sprint = 0;
    if (pup_prefix(link, "Horse") || pup_prefix(link, "Ride") || pup_prefix(link, "Sled") || pup_prefix(link, "Surf") ||
        pup_prefix(link, "Demo") || pup_prefix(link, "Event") || strstr(link, "Horse"))
        return NULL;  // (a horse, a shield surf, a cutscene's own move: Link shows; 0.9.2: CutHorseRide, AimHorse... too:
                      // a ride is told by his clips now, not by his real position, docs/charsel.md "Ragdoll")
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
    // 0.9.3: never Link for a clip the pack lacks (the Moblin turned into Link as he swam, 2026-11-13): a glide falls,
    // anything else stands or walks there by his speed (a swim is lowered to Link's head height: pup_clamp_tick)
    if (must && !strcmp(near, "ParashawlGlide") && !missing_has(m, "Fall")) return "Fall";
    return speed;
}

// 0.9.4 The swing for the weapon Link holds: with a two-hander (Weapon_Lsword_*) or a spear (Weapon_Spear_*) an attack
// name p becomes p_Lsword / p_Spear when the pack has that name (as the multiplayer client does for a friend's ghost,
// client.WEAPON_ANIMS; the self puppet never did, so the Moblin stabbed with every weapon and Zelda swung a two-hander
// one-handed). The Link-body names every pack has; the monsters' packs (own: 1) also CutS3_Spear, CutDash_Lsword and
// CutDash_Spear (their own clips timed to Link's hit: tools/charsel.py BOKO, MOBLIN). Returns p, or buf with the name.
static const char *const PUP_SWINGS[] = {"CutS1_Lsword", "CutSF_Lsword", "CutS1_Spear", "CutS2_Spear", "CutSF_Spear"};
static const char *const PUP_SWINGS_OWN[] = {"CutS3_Spear", "CutDash_Lsword", "CutDash_Spear"};
static int pup_own_swings(const char *id) { return !strcmp(id, "bokoblin") || !strcmp(id, "moblin"); }
static const char *pup_weapon_pick(const char *p, const char *weapon, int own, const Missing *m, char *buf, size_t cap) {
    if (!p || !weapon || !anim_is_attack(p)) return p;
    const char *suf = pup_prefix(weapon, "Weapon_Lsword_") ? "_Lsword" : pup_prefix(weapon, "Weapon_Spear_") ? "_Spear" : NULL;
    if (!suf) return p;
    snprintf(buf, cap, "%s%s", p, suf);
    int known = 0;
    for (size_t i = 0; i < sizeof PUP_SWINGS / sizeof *PUP_SWINGS; i++) known |= !strcmp(buf, PUP_SWINGS[i]);
    for (size_t i = 0; own && i < sizeof PUP_SWINGS_OWN / sizeof *PUP_SWINGS_OWN; i++) known |= !strcmp(buf, PUP_SWINGS_OWN[i]);
    return known && !missing_has(m, buf) ? buf : p;
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
// 0.9.2: also every clip of his that throws, slides or knocks him down and that no pack has (Player.baslist): the big
// knock-back and its getting up (DamageL, DamageLStand), a hard landing (LandDamage, LandDamageStand), the slide down
// a steep slope he gets up into after a tumble on a hill (Slide, SlideBrake, SlideLand: the costume walked there, by
// his speed, half in the hill), and dying in water. The costume follows his pose through them.
static int pup_rd_now(int mgr_ok, uint32_t flags, const char *anim) {
    if (mgr_ok && (flags & RD_ON)) return 1;
    static const char *const LYING[] = {"SlippingDown", "DeadRagdoll", "Dead",     "Rolldown", "Slide",
                                        "LandDamage",   "DamageL",     "SwimDead", "MasterSwordDead"};
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
    int16_t limb[PUP_LIMP_N];         // 0.9.2: Arm_1_L, Arm_1_R, Leg_1_L, Leg_1_R (-1: none)
    int rest;                         // 0.9.2: its rest pose was read (pup_rest_set): rw, rl
    float rw[PUP_MAXB][9];            // ... each bone's turn at rest in model space (rows)
    float rl[PUP_MAXB][12];           // ... its rest local (3x4 rows: turn and place, no scale)
    uint8_t cloth[PUP_MAXB];          // 0.9.6: 1: its own physics moves this bone (PUP_CLOTH): never written here
    int ncloth;
} PupSkel;

// 0.9.6: the bones body `id`'s own cloth and support-bone physics move (PUP_CLOTH; NULL: none).
static const char *pup_cloth_list(const char *id) {
    for (int i = 0; id && PUP_CLOTH[i].id; i++)
        if (!strcmp(PUP_CLOTH[i].id, id)) return PUP_CLOTH[i].bones;
    return NULL;
}
// 1: `name` is one of the list's names (each followed by one space).
static int pup_cloth_has(const char *list, const char *name) {
    size_t n = name ? strlen(name) : 0;
    for (const char *p = list; n && p && *p;) {
        const char *e = strchr(p, ' ');
        if (!e) break;
        if ((size_t)(e - p) == n && !strncmp(p, name, n)) return 1;
        p = e + 1;
    }
    return 0;
}
// Mark skeleton s's bones (names) that body id's physics moves and that aren't named as Link's (map < 0). Returns
// how many.
static int pup_cloth_mark(PupSkel *s, const char (*names)[32], const char *id) {
    const char *list = pup_cloth_list(id);
    s->ncloth = 0;
    for (int b = 0; b < s->n && b < PUP_MAXB; b++) {
        s->cloth[b] = (uint8_t)(list && s->map[b] < 0 && pup_cloth_has(list, names[b]));
        s->ncloth += s->cloth[b];
    }
    return s->ncloth;
}

static const char *const PUP_LIMBS[PUP_LIMP_N] = {"Arm_1_L", "Arm_1_R", "Leg_1_L", "Leg_1_R"};

// 0.9.2: a bone's rest turn (rows) from the resource's numbers: Euler XYZ (r = Rz Ry Rx, as tools/charsel.py _rot3),
// or a quaternion x y z w. 0: not a turn (not finite, a quaternion of no length).
static int pup_rest_rot(const float q[4], int euler, float R[9]) {
    for (int i = 0; i < 4; i++)
        if (!(q[i] == q[i] && q[i] < 1e4f && q[i] > -1e4f)) return 0;
    if (euler) {
        float cx = __builtin_cosf(q[0]), sx = __builtin_sinf(q[0]), cy = __builtin_cosf(q[1]), sy = __builtin_sinf(q[1]);
        float cz = __builtin_cosf(q[2]), sz = __builtin_sinf(q[2]);
        float m[9] = {cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx,
                      sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx,
                      -sy,     cy * sx,                cy * cx};
        memcpy(R, m, sizeof m);
        return 1;
    }
    float l = __builtin_sqrtf(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]);
    if (!(l > 0.5f)) return 0;
    float x = q[0] / l, y = q[1] / l, z = q[2] / l, w = q[3] / l;
    float m[9] = {1 - 2 * (y * y + z * z), 2 * (x * y - z * w),     2 * (x * z + y * w),
                  2 * (x * y + z * w),     1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
                  2 * (x * z - y * w),     2 * (y * z + x * w),     1 - 2 * (x * x + y * y)};
    memcpy(R, m, sizeof m);
    return 1;
}

static void pup_r_mul(const float a[9], const float b[9], float o[9]);

// 0.9.2: skeleton s's rest pose from each bone's resource numbers (rot: 4 floats, trans: 3; scale: 3, each must be
// about 1: the game's people and monsters all are): rest locals and the turns at rest in model space. s->rest says it
// worked (else the pose is followed the 0.9.1 way: his turns as they are).
static void pup_rest_set(PupSkel *s, const float (*scale)[3], const float (*rot)[4], const float (*trans)[3], int euler) {
    s->rest = 0;
    if (s->n <= 0 || s->n > PUP_MAXB) return;
    for (int i = 0; i < s->n; i++) {
        int b = s->order[i];
        float R[9];
        for (int k = 0; k < 3; k++)
            if (!(scale[b][k] > 0.5f && scale[b][k] < 2.0f) || !(trans[b][k] == trans[b][k]) ||
                !(trans[b][k] < 1e3f && trans[b][k] > -1e3f))
                return;
        if (!pup_rest_rot(rot[b], euler, R)) return;
        for (int r = 0; r < 3; r++) {
            s->rl[b][r * 4] = R[r * 3], s->rl[b][r * 4 + 1] = R[r * 3 + 1], s->rl[b][r * 4 + 2] = R[r * 3 + 2];
            s->rl[b][r * 4 + 3] = trans[b][r];
        }
        if (s->parent[b] < 0) memcpy(s->rw[b], R, sizeof R);
        else pup_r_mul(s->rw[s->parent[b]], R, s->rw[b]);
    }
    s->rest = 1;
}

// 0.9.2: the loose limbs' state (one spring per limb, its tip in the world), kept by the tick.
typedef struct {
    int ok[PUP_LIMP_N];
    float p[PUP_LIMP_N][3], v[PUP_LIMP_N][3];
} PupLimp;

static void pup_r_multt(const float a[9], const float b[9], float o[9]) {  // a b^T
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++) o[i * 3 + j] = a[i * 3] * b[j * 3] + a[i * 3 + 1] * b[j * 3 + 1] + a[i * 3 + 2] * b[j * 3 + 2];
}
static void pup_r_apply(const float R[9], const float v[3], float o[3]) {
    for (int r = 0; r < 3; r++) o[r] = R[r * 3] * v[0] + R[r * 3 + 1] * v[1] + R[r * 3 + 2] * v[2];
}
static float pup_dot3(const float a[3], const float b[3]) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

// 0.9.2: the shortest turn (rows) taking unit vector a to unit vector b.
static void pup_r_between(const float a[3], const float b[3], float R[9]) {
    float c = pup_dot3(a, b), k[3] = {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]};
    memset(R, 0, 9 * sizeof *R);
    R[0] = R[4] = R[8] = 1.0f;
    if (c > 0.999999f || c < -0.99f) return;  // (no turn; nearly opposite never happens here: the swing is clamped)
    float f = 1.0f / (1.0f + c);
    float K[9] = {0, -k[2], k[1], k[2], 0, -k[0], -k[1], k[0], 0}, K2[9];
    pup_r_mul(K, K, K2);
    for (int i = 0; i < 9; i++) R[i] += K[i] + K2[i] * f;  // (Rodrigues: I + K + K^2 / (1 + c))
}

// 0.9.2: limb k's tip, a damped spring towards where his pose puts it (tgt) around its joint j at length len, dt s
// (in steps of at most 1/60 s), never more than PUP_LIMP_MAX off; out: the direction it is drawn along, limp of the way
// from his towards the spring's.
static void pup_limp_step(PupLimp *lp, int k, const float j[3], const float tgt[3], float len, float dt, float limp,
                          float out[3]) {
    float dt_[3] = {tgt[0] - j[0], tgt[1] - j[1], tgt[2] - j[2]}, l0 = pup_len3(dt_);
    if (!(l0 > 1e-4f)) {
        memcpy(out, dt_, sizeof dt_);
        return;
    }
    for (int r = 0; r < 3; r++) dt_[r] /= l0;
    if (!lp->ok[k] || !(dt > 0.0f) || dt > 0.5f) {  // (new, or a long gap: from his pose, at rest)
        if (!lp->ok[k] || dt > 0.5f) memcpy(lp->p[k], tgt, sizeof lp->p[k]), memset(lp->v[k], 0, sizeof lp->v[k]);
        lp->ok[k] = 1;
    } else {
        const float w = 6.2831853f * PUP_LIMP_HZ, K = w * w, C = 2.0f * PUP_LIMP_ZETA * w;
        int n = (int)(dt * 60.0f) + 1;
        n = n > 8 ? 8 : n;
        float h = dt / (float)n;
        for (int s = 0; s < n; s++) {
            float *p = lp->p[k], *v = lp->v[k], d[3];
            for (int r = 0; r < 3; r++) v[r] += (K * (tgt[r] - p[r]) - C * v[r]) * h;
            for (int r = 0; r < 3; r++) p[r] += v[r] * h, d[r] = p[r] - j[r];
            float l = pup_len3(d);
            if (!(l > 1e-4f)) {
                memcpy(p, tgt, 3 * sizeof *p), memset(v, 0, 3 * sizeof *v);
                continue;
            }
            for (int r = 0; r < 3; r++) d[r] /= l;
            float vr = pup_dot3(v, d);
            for (int r = 0; r < 3; r++) p[r] = j[r] + d[r] * len, v[r] -= vr * d[r];  // (on its sphere)
        }
    }
    float s[3] = {lp->p[k][0] - j[0], lp->p[k][1] - j[1], lp->p[k][2] - j[2]}, ls = pup_len3(s);
    if (!(ls > 1e-4f) || !(ls == ls)) {
        memcpy(lp->p[k], tgt, sizeof lp->p[k]), memset(lp->v[k], 0, sizeof lp->v[k]);
        memcpy(out, dt_, sizeof dt_);
        return;
    }
    for (int r = 0; r < 3; r++) s[r] /= ls;
    float c = pup_dot3(s, dt_), cmax = __builtin_cosf(PUP_LIMP_MAX);
    if (c < cmax) {  // (too far off: back on the cone's edge, slower)
        float q[3] = {s[0] - c * dt_[0], s[1] - c * dt_[1], s[2] - c * dt_[2]}, lq = pup_len3(q);
        float sm = __builtin_sinf(PUP_LIMP_MAX);
        for (int r = 0; r < 3; r++) s[r] = lq > 1e-5f ? dt_[r] * cmax + q[r] / lq * sm : dt_[r];
        for (int r = 0; r < 3; r++) lp->p[k][r] = j[r] + s[r] * len, lp->v[k][r] *= 0.5f;
    }
    limp = limp < 0.0f ? 0.0f : limp > 1.0f ? 1.0f : limp;
    float e[3] = {dt_[0] + (s[0] - dt_[0]) * limp, dt_[1] + (s[1] - dt_[1]) * limp, dt_[2] + (s[2] - dt_[2]) * limp};
    float le = pup_len3(e);
    for (int r = 0; r < 3; r++) out[r] = le > 1e-5f ? e[r] / le : dt_[r];
}

// The costume's new bone transforms (locals, 3x4 rows) and where each bone then is (wp): a bone named as Link's turns
// as his does in the world (relative to its own parent: what a Link clip of that pose would set), keeping its own
// length (cur's translation); the hips go to hip_at; any other bone keeps cur. base: the costume's matrix (its size).
// 0.9.2: lk (Link's skeleton; NULL: none) and the costume both with their rest: his turn from HIS rest is put on ITS
// rest (R = R_link * R_link_rest^T * R_its_rest, in the world), each bone keeps its rest length, and the bones not
// named as his take their rest (no clip shows). lp (NULL: none): the limbs swing loosely (limp, dt s).
static void pup_rd_solve(const PupSkel *ps, const PupSkel *lk, const float (*wl)[12], const float (*cur)[12],
                         const float base[12], const float hip_at[3], float (*out)[12], PupX *wp, PupLimp *lp,
                         float limp, float dt) {
    static float sw[PUP_MAXB][9];  // (each bone's swing from the limbs above it; the tick's thread only)
    PupX b;
    if (!pup_x_from(base, &b)) {
        memset(&b, 0, sizeof b);
        b.R[0] = b.R[4] = b.R[8] = b.k = 1.0f;
        b.t[0] = base[3], b.t[1] = base[7], b.t[2] = base[11];
    }
    const float I[9] = {1, 0, 0, 0, 1, 0, 0, 0, 1};
    int rest = ps->rest && lk && lk->rest;
    for (int i = 0; i < ps->n; i++) {
        int p = ps->order[i];
        const PupX *par = ps->parent[p] < 0 ? &b : &wp[ps->parent[p]];
        const float *spar = ps->parent[p] < 0 ? I : sw[ps->parent[p]];
        PupX lc, lw;
        float Rl[9], tl[3];
        if (ps->rest) {  // (0.9.2: its rest, not the clip it played)
            for (int r = 0; r < 3; r++)
                Rl[r * 3] = ps->rl[p][r * 4], Rl[r * 3 + 1] = ps->rl[p][r * 4 + 1], Rl[r * 3 + 2] = ps->rl[p][r * 4 + 2],
                tl[r] = ps->rl[p][r * 4 + 3];
        } else {
            if (!pup_x_from(cur[p], &lc)) {
                memset(&lc, 0, sizeof lc);
                lc.R[0] = lc.R[4] = lc.R[8] = lc.k = 1.0f;
                lc.t[0] = cur[p][3], lc.t[1] = cur[p][7], lc.t[2] = cur[p][11];
            }
            memcpy(Rl, lc.R, sizeof Rl), memcpy(tl, lc.t, sizeof tl);
        }
        memcpy(sw[p], spar, sizeof sw[p]);
        if (ps->map[p] >= 0 && pup_x_from(wl[ps->map[p]], &lw)) {
            float Rt[9], Rw[9], tmp[9];
            if (rest) {
                pup_r_multt(lw.R, lk->rw[ps->map[p]], tmp);
                pup_r_mul(tmp, ps->rw[p], Rt);
            } else {
                memcpy(Rt, lw.R, sizeof Rt);
            }
            pup_r_mul(spar, Rt, Rw);
            int k = -1;
            for (int q = 0; lp && limp > 0.0f && q < PUP_LIMP_N; q++)
                if (ps->limb[q] == p) k = q;
            int c = -1;
            float ct[3] = {0, 0, 0}, cl = 0;
            for (int q = 0; k >= 0 && q < ps->n; q++) {  // (its longest child: along the limb, Arm_2, Leg_2; not
                if (ps->parent[q] != p || ps->map[q] < 0) continue;  // Arm_1_Assist 6 cm off its start)
                float qt[3];
                if (ps->rest) qt[0] = ps->rl[q][3], qt[1] = ps->rl[q][7], qt[2] = ps->rl[q][11];
                else qt[0] = cur[q][3], qt[1] = cur[q][7], qt[2] = cur[q][11];
                float ql = pup_len3(qt);
                if (ql > cl) c = q, cl = ql, memcpy(ct, qt, sizeof ct);
            }
            if (c >= 0) {
                float len = cl * par->k;
                if (cl > 1e-3f && len > 0.05f) {
                    float j[3], ax[3], d[3], tgt[3], e[3], Q[9];
                    pup_r_apply(par->R, tl, j);
                    for (int r = 0; r < 3; r++) j[r] = par->t[r] + par->k * j[r], ax[r] = ct[r] / cl;
                    pup_r_apply(Rw, ax, d);
                    for (int r = 0; r < 3; r++) tgt[r] = j[r] + d[r] * len;
                    pup_limp_step(lp, k, j, tgt, len, dt, limp, e);
                    pup_r_between(d, e, Q);
                    pup_r_mul(Q, Rw, tmp), memcpy(Rw, tmp, sizeof Rw);
                    pup_r_mul(Q, spar, sw[p]);
                }
            }
            pup_r_tmul(par->R, Rw, Rl);
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

// 0.9.2: the lowest point of body box `box` (cm, in the bone's frame) around a bone placed at x, scaled by s, along up
// = (n0, n1, n2) (a plane y = a x + b z + c has up (-a, 1, -b)): the flesh is taken as the ellipsoid inside the box.
// Lying square in it, that is the box's own bottom; tilted, it stays higher than the box's corner (a round body: Daruk's
// 2 m belly tilted 45 degrees reached 0.4 m lower as a box, and he floated getting up, 2026-11-13). Returns n . p (its
// height along up, through 0); the point into p (NULL: not wanted).
static float pup_box_low(const PupX *x, const short box[6], float s, const float n[3], float p[3]) {
    float g[3], m[3], e[3], l[3], w[3];
    for (int j = 0; j < 3; j++) {
        g[j] = x->R[j] * n[0] + x->R[3 + j] * n[1] + x->R[6 + j] * n[2];  // (up in the bone's frame: R^T n)
        m[j] = (float)(box[2 * j] + box[2 * j + 1]) * 0.005f * s;           // (the box's middle, m)
        e[j] = (float)(box[2 * j + 1] - box[2 * j]) * 0.005f * s;           // (its half sizes)
    }
    float q = __builtin_sqrtf(e[0] * e[0] * g[0] * g[0] + e[1] * e[1] * g[1] * g[1] + e[2] * e[2] * g[2] * g[2]);
    for (int j = 0; j < 3; j++) l[j] = m[j] - (q > 1e-9f ? e[j] * e[j] * g[j] / q : 0.0f);
    for (int r = 0; r < 3; r++) w[r] = x->t[r] + x->k * (x->R[r * 3] * l[0] + x->R[r * 3 + 1] * l[1] + x->R[r * 3 + 2] * l[2]);
    if (p) memcpy(p, w, sizeof w);
    return n[0] * w[0] + n[1] * w[1] + n[2] * w[2];
}

// The body boxes the costume stands on (shins and feet: Leg_2_*, Ankle_*), as a mask of PUP_BODY_BONES.
static uint32_t pup_rd_feet(void) {
    uint32_t m = 0;
    for (int k = 0; k < PUP_NBODY_BONES && k < 32; k++)
        if (pup_prefix(PUP_BODY_BONES[k], "Leg_2_") || pup_prefix(PUP_BODY_BONES[k], "Ankle_")) m |= 1u << k;
    return m;
}
#define PUP_RD_ALL 0xffffffffu

// How far the lowest point of the boxes in mask is over the plane (along its up, per m straight up; negative: under
// it). 1e30: no box.
static float pup_rd_drop_m(const PupX *x, const PupSkel *s, const PupBody *body, float scale, const float plane[3],
                           uint32_t mask) {
    float n[3] = {-plane[0], 1.0f, -plane[1]}, lowest = 1e30f;
    for (int k = 0; k < PUP_NBODY_BONES; k++) {
        int b = s->keyidx[k];
        if (b < 0 || !body->has[k] || !(mask >> k & 1)) continue;
        float h = pup_box_low(&x[b], body->box[k], scale, n, NULL) - plane[2];
        if (h < lowest) lowest = h;
    }
    return lowest;
}
static float pup_rd_drop(const PupX *x, const PupSkel *s, const PupBody *body, float scale, const float plane[3]) {
    return pup_rd_drop_m(x, s, body, scale, plane, PUP_RD_ALL);
}

// The plane's height (plane[2]) for its slope (plane[0], [1]): as low as the body's lowest point, so none of it is under.
static void pup_rd_lower(const PupX *x, const PupSkel *s, const PupBody *body, float scale, float plane[3]) {
    float c = plane[2];
    plane[2] = 0.0f;
    float d = pup_rd_drop(x, s, body, scale, plane);
    plane[2] = d < 1e29f ? d : c;
}

// The ground under a body lying as w says (Link, his bones placed: x per bone), as a plane y = a x + b z + c: fitted
// through the lowest point of each body box (then again without the ones well above the rest: a raised knee; a
// gentle pull to level so a body lying across a slope doesn't tip it; at most PUP_RD_SLOPE steep), then lowered until
// no box is under it.
// 0: no box could be placed.
static int pup_rd_ground(const PupX *x, const PupSkel *s, const PupBody *body, float scale, float plane[3]) {
    float q[PUP_NBODY_BONES][3];
    const float up[3] = {0.0f, 1.0f, 0.0f};
    int nq = 0;
    for (int k = 0; k < PUP_NBODY_BONES; k++) {
        int b = s->keyidx[k];
        if (b < 0 || !body->has[k]) continue;
        pup_box_low(&x[b], body->box[k], scale, up, q[nq++]);
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
    plane[0] = a, plane[1] = bz, plane[2] = c;
    pup_rd_lower(x, s, body, scale, plane);  // (lowered under every box)
    return 1;
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

static float pup_clamp01(float v) { return v < 0.0f ? 0.0f : v > 1.0f ? 1.0f : v; }

// 0.9.2: how upright Link is, 0 (lying) to 1 (standing): his spine (1 within PUP_RD_UP_COS of straight up, 0 from
// PUP_RD_TILT_COS) times his hips' height over his own lowest point (1 from PUP_RD_UP_HIP, 0 under PUP_RD_LOW_HIP).
// Unknown (no hips, head or boxes): 1.
static float pup_rd_upness(const PupX *x, const PupSkel *s, const PupBody *body) {
    if (s->hip < 0 || s->head < 0) return 1.0f;
    const float *h = x[s->hip].t, *d = x[s->head].t;
    float up[3] = {d[0] - h[0], d[1] - h[1], d[2] - h[2]}, l = pup_len3(up);
    const float level[3] = {0.0f, 0.0f, 0.0f};
    float low = pup_rd_drop(x, s, body, 1.0f, level);  // (his lowest point's height)
    if (!(l > 1e-4f) || !(low < 1e29f)) return 1.0f;
    float spine = pup_clamp01((up[1] / l - PUP_RD_TILT_COS) / (PUP_RD_UP_COS - PUP_RD_TILT_COS));
    float hips = pup_clamp01((h[1] - low - PUP_RD_LOW_HIP) / (PUP_RD_UP_HIP - PUP_RD_LOW_HIP));
    return spine * hips;
}

// 0.9.2: the lift that puts the costume posed as wp on the ground: its lowest point on the plane while Link lies
// (u 0), its feet on it as he stands (u 1; its feet's lift is never more than its whole body's), in between as he
// gets up; the rest of it never more than PUP_RD_SINK_OK under the ground for that (round 3: "falling through a
// hill": a belly 1 m uphill of his feet). 1e30: no box.
static float pup_rd_want(const PupX *wp, const PupSkel *s, const PupBody *body, float scale, const float plane[3],
                         float u) {
    float all = pup_rd_drop(wp, s, body, scale, plane);
    if (!(all < 1e29f)) return 1e30f;
    float feet = pup_rd_drop_m(wp, s, body, scale, plane, pup_rd_feet());
    if (!(feet < 1e29f)) feet = all;  // (no feet boxes: the whole body)
    float sink = feet - all;
    sink = sink > PUP_RD_SINK_OK ? PUP_RD_SINK_OK : sink;
    return -all - pup_clamp01(u) * sink;
}

// The body boxes for a pick (Link's for one with none: the Hylian's Mii-style parts).
static const PupBody *pup_body(const char *id) {
    for (size_t i = 0; i < sizeof PUP_BODIES / sizeof *PUP_BODIES; i++)
        if (!strcmp(PUP_BODIES[i].id, id)) return &PUP_BODIES[i];
    return &PUP_BODIES[0];  // (charsel.py writes link first)
}

// 0.9.2: the costume's next shown lift, from `shown` towards `want`: up fast (PUP_RD_RISE_S, at most PUP_RD_RISE_MPS:
// a body that must rise isn't left in the ground, but a 1 m jump of one frame is spread over a few), down more gently
// while Link lies (PUP_RD_FALL_S, PUP_RD_FALL_MPS), and down fast as he stands up (u: how upright he is; at the
// standing height within a few frames, not 1.6 s later). dt 0: at once.
static float pup_rd_follow(float shown, float want, float dt, float u) {
    if (!(dt > 0.0f)) return want;
    float d = want - shown, tau, mps;
    if (d >= 0.0f) {
        tau = (float)PUP_RD_RISE_S, mps = PUP_RD_RISE_MPS;
    } else {
        u = pup_clamp01(u);
        tau = (float)(PUP_RD_FALL_S + (PUP_RD_STAND_S - PUP_RD_FALL_S) * u);
        mps = PUP_RD_FALL_MPS + (PUP_RD_STAND_MPS - PUP_RD_FALL_MPS) * u;
    }
    float step = d * (dt >= tau ? 1.0f : dt / tau), cap = mps * dt;
    step = step > cap ? cap : step < -cap ? -cap : step;
    return shown + step;
}

// 0.9.2: the ground's slope from a velocity v (m/s): along the motion it rises as v does (a slide or a tumble down a
// hill follows it), level across it, at most PUP_RD_SLOPE; into g (y = g0 x + g1 z + c). Returns how much it counts:
// 0 under PUP_RD_MOVE_FROM m/s across the ground, 1 from PUP_RD_MOVE_FULL.
static float pup_motion_slope(const float v[3], float g[2]) {
    float h = __builtin_sqrtf(v[0] * v[0] + v[2] * v[2]);
    g[0] = g[1] = 0.0f;
    if (!(h > 1e-3f) || !(v[1] == v[1])) return 0.0f;
    float s = v[1] / h;
    s = s > PUP_RD_SLOPE ? PUP_RD_SLOPE : s < -PUP_RD_SLOPE ? -PUP_RD_SLOPE : s;
    g[0] = s * v[0] / h, g[1] = s * v[2] / h;
    return pup_clamp01((h - PUP_RD_MOVE_FROM) / (PUP_RD_MOVE_FULL - PUP_RD_MOVE_FROM));
}

// 0.9.2: a ball as a clip poses the body (x: its bones in its own frame, scaled): the middle of its body boxes (by
// their size) and its radius about its own x axis (the mean reach of the boxes from the middle in 8 directions
// around it). 0: no box.
static int pup_ball_shape(const PupX *x, const PupSkel *s, const PupBody *body, float scale, float c[3], float *r) {
    double sv = 0, sc[3] = {0, 0, 0};
    for (int k = 0; k < PUP_NBODY_BONES; k++) {
        int b = s->keyidx[k];
        if (b < 0 || !body->has[k]) continue;
        float m[3], w[3], v = 1.0f;
        for (int j = 0; j < 3; j++) {
            m[j] = (float)(body->box[k][2 * j] + body->box[k][2 * j + 1]) * 0.005f * scale;
            v *= (float)(body->box[k][2 * j + 1] - body->box[k][2 * j]) * 0.01f * scale + 0.01f;
        }
        pup_r_apply(x[b].R, m, w);
        for (int j = 0; j < 3; j++) sc[j] += v * (x[b].t[j] + x[b].k * w[j]);
        sv += v;
    }
    if (!(sv > 0)) return 0;
    for (int j = 0; j < 3; j++) c[j] = (float)(sc[j] / sv);
    float sum = 0;
    for (int i = 0; i < 8; i++) {
        float a = 0.785398f * (float)i, n[3] = {0.0f, -__builtin_cosf(a), -__builtin_sinf(a)}, low = 1e30f;
        for (int k = 0; k < PUP_NBODY_BONES; k++) {
            int b = s->keyidx[k];
            if (b < 0 || !body->has[k]) continue;
            float h = pup_box_low(&x[b], body->box[k], scale, n, NULL);
            if (h < low) low = h;
        }
        sum += -low - (-pup_dot3(n, c));  // (its reach along -n, from the middle)
    }
    *r = sum / 8.0f;
    return *r > 0.0f;
}

// 0.9.2: the ball's actor matrix (rows) facing f (across the ground, unit: x z), rolled theta about its own x (forward
// for theta growing), its lowest point (x posed in its own frame, its boxes) on the plane through gp with up n (unit),
// its middle c (its own frame) straight over gp. Returns the radius at this turn (its middle over its lowest point).
static float pup_ball_place(const PupX *x, const PupSkel *s, const PupBody *body, float scale, const float c[3],
                            const float f[2], float theta, const float gp[3], const float n[3], float out[12]) {
    float Y[9] = {f[1], 0, f[0], 0, 1, 0, -f[0], 0, f[1]}, ct = __builtin_cosf(theta), st = __builtin_sinf(theta);
    float X[9] = {1, 0, 0, 0, ct, -st, 0, st, ct}, R[9], nl[3], rc[3];
    pup_r_mul(Y, X, R);
    for (int j = 0; j < 3; j++) nl[j] = R[j] * n[0] + R[3 + j] * n[1] + R[6 + j] * n[2];  // (R^T n)
    float low = 1e30f;
    for (int k = 0; k < PUP_NBODY_BONES; k++) {
        int b = s->keyidx[k];
        if (b < 0 || !body->has[k]) continue;
        float h = pup_box_low(&x[b], body->box[k], scale, nl, NULL);
        if (h < low) low = h;
    }
    float rad = low < 1e29f ? pup_dot3(nl, c) - low : 0.0f, ny = n[1] > 0.3f ? n[1] : 0.3f;
    float C[3] = {gp[0], gp[1] + rad / ny, gp[2]};
    pup_r_apply(R, c, rc);
    for (int r = 0; r < 3; r++) {
        out[r * 4] = R[r * 3], out[r * 4 + 1] = R[r * 3 + 1], out[r * 4 + 2] = R[r * 3 + 2];
        out[r * 4 + 3] = C[r] - rc[r];
    }
    return rad;
}

// 0.9.2: a body's bones in its own frame (model space) from its locals cur, scaled by sc (no matrix of its own).
static void pup_fk(const PupSkel *s, const float (*cur)[12], float sc, PupX *x) {
    PupX b;
    memset(&b, 0, sizeof b);
    b.R[0] = b.R[4] = b.R[8] = 1.0f, b.k = sc;
    for (int i = 0; i < s->n; i++) {
        int p = s->order[i];
        const PupX *par = s->parent[p] < 0 ? &b : &x[s->parent[p]];
        PupX lc;
        if (!pup_x_from(cur[p], &lc)) {
            memset(&lc, 0, sizeof lc);
            lc.R[0] = lc.R[4] = lc.R[8] = 1.0f;
            lc.t[0] = cur[p][3], lc.t[1] = cur[p][7], lc.t[2] = cur[p][11];
        }
        pup_r_mul(par->R, lc.R, x[p].R);
        float w[3];
        pup_r_apply(par->R, lc.t, w);
        for (int r = 0; r < 3; r++) x[p].t[r] = par->t[r] + par->k * w[r];
        x[p].k = par->k;
    }
}

// 0.9.2: the costume's sprint is a ball that rolls (Daruk: RunGear2 is his Common_Move_Ball, a still curled pose; the
// game rolls the body itself).
static int pup_ball_id(const char *id) { return !strcmp(id, "goron"); }

// ---- 0.9.3 held items, the ground in every state, the hands (docs/charsel.md "Held items"), pure parts
//
// Measured from the game's models (scratch reads of each body's skeleton, 2026-11-14) [V]: every costume body has
// Weapon_R and Weapon_L on its wrists, turned exactly as Link's at rest, and every one sits at its closed hand's grip
// (within 2-5 cm of Link's place relative to the finger roots) except Daruk's: his is 24 cm short of his finger roots, in
// the middle of a 1.1 m palm ("clipped at his wrist", 2026-11-13). Spine_2 rests as Link's in every body (the
// Bokoblin's and Moblin's 12-20 degrees forward). The sizes are a choice for big hands [G: to be looked at in the game;
// `ghost puppet grip` changes them live].
// 0.9.5 (the round-4 game check, 2026-11-14): `back` moves the back place per body (Spine_2's frame: x up its spine, y
// forward, z its left; 0: Link's Pod_A place, as measured) [G: no body needed one yet, `ghost puppet back` tries one];
// `arm`: while a hand holds a drawn copy, that whole arm takes Link's turns (the hand overlay), so the copy points
// where his does. Paya's own stand holds her hands clasped at her chest: her sword pointed straight up in front of it
// while walking [V: the game check]; Urbosa's own stand (v5: the game's hip-shot Positive_Wait) has a hand on her hip.
typedef struct {
    const char *id;
    float scale;    // the held copy's size (1: as in Link's hand)
    float hand[3];  // m: the grip moved from the body's Weapon_R in its own frame (Weapon_L: y mirrored)
    float back[3];  // 0.9.5: m: the back place moved in Spine_2's frame
    int arm;        // 0.9.5: the arm of a hand holding a drawn copy is Link's
} PupGrip;
static const PupGrip PUP_GRIPS[] = {
    {"link", 1.0f, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, 0},       // (and every body not listed: as it is)
    {"goron", 1.8f, {-0.24f, 0.025f, 0.02f}, {0.0f, 0.0f, 0.0f}, 0},  // Daruk: 1.8 times, at his finger roots
    {"moblin", 2.0f, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, 0},
    {"bokoblin", 1.3f, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, 0},
    {"sheikah", 1.0f, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, 1},     // Paya: her shy stand's hands (above)
    {"gerudo", 1.0f, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, 1},      // Urbosa: a hand on her hip when she stands
};
#define PUP_ITEM_KEEP_S 0.4   // a held item Link's equipment read loses for less than this stays (no respawn: no flicker)
// 0.9.5: the ball through a drop (the game check: his speed fell to 2 m/s for 0.5 s while he still sprinted): Link is
// "in his sprint" while the costume's sprint is picked by his speed, or while he Moves and his stamina falls (only a
// sprint drains it on foot); the ball ends only once he has been out of it this long
#define PUP_SPRINT_LEAVE_S 0.3
#define PUP_DRAIN_S 0.3       // his stamina fell this recently: he still sprints
#define PUP_DRAIN_MIN 0.01f   // ... by more than this (stamina units: 1000 a wheel)
#define PUP_CLAMP_MAX 1.2f    // m: the ground clamp's range either way
#define PUP_CLAMP_UP_S 0.05   // ... up fast, down a little slower, at most so many m/s
#define PUP_CLAMP_DOWN_S 0.12
#define PUP_CLAMP_MPS 6.0f
#define PUP_BACK_SIDE 0.077f  // m: Link's Pod_A is this far to his right of his spine (and 3.2 cm out of his back)
static const char PUP_BONE_R[] = "Weapon_R", PUP_BONE_L[] = "Weapon_L", PUP_BONE_BACK[] = "Spine_2";

static const PupGrip *pup_grip(const char *id) {
    for (size_t i = 0; i < sizeof PUP_GRIPS / sizeof *PUP_GRIPS; i++)
        if (!strcmp(PUP_GRIPS[i].id, id)) return &PUP_GRIPS[i];
    return &PUP_GRIPS[0];
}

// Where a held copy goes on body `b` with grip `g`: its bone and the grip (3x4 rows), before the item's own offsets.
// In the hand: the hand bone (slot 0 Weapon_R; the shield and the bow Weapon_L), moved by the grip. On the back: Spine_2,
// at the place Link's Pod_A has on his (half way up its box, 3.2 cm out of its back, a little to the right; the
// Hylian: Link's), not turned (Link's Pod_A rests as his Spine_2).
static void pup_grip_place(const PupGrip *g, const PupBody *b, int slot, int back, const char **bone, float G[12]) {
    memset(G, 0, 12 * sizeof *G);
    G[0] = G[5] = G[10] = 1.0f;
    if (back) {
        const short *box = b->has[2] ? b->box[2] : PUP_BODIES[0].box[2];  // (Spine_2's: x up, y forward, z its left)
        *bone = PUP_BONE_BACK;
        G[3] = (float)box[1] * 0.00525f, G[7] = (float)box[2] * 0.01f - 0.032f, G[11] = -PUP_BACK_SIDE * g->scale;
        G[3] += g->back[0], G[7] += g->back[1], G[11] += g->back[2];  // (0.9.5: the body's tweak)
        return;
    }
    *bone = slot ? PUP_BONE_L : PUP_BONE_R;
    G[3] = g->hand[0], G[7] = slot ? -g->hand[1] : g->hand[1], G[11] = g->hand[2];
}

// The name a held slot shows: Link's (now), else the one kept for PUP_ITEM_KEEP_S after his equipment read lost it (a
// read in the middle of a draw, a link being remade: the copy was deleted and made again 0.3-0.8 s later, "disappears
// here and there", 2026-11-13). A new name: at once. back: his slot's "on the back" (-1: not read now).
typedef struct {
    char name[64];
    int back;
    double seen;
} PupKeep;
static void pup_keep_step(PupKeep *k, const char *now, int back, double t) {
    if (*now) {
        if (strcmp(k->name, now)) snprintf(k->name, sizeof k->name, "%s", now);
        k->seen = t;
        if (back >= 0) k->back = back != 0;
    } else if (*k->name && !(t >= k->seen && t - k->seen < PUP_ITEM_KEEP_S)) {
        k->name[0] = 0, k->back = 0;
    }
}

// Link guards with his shield: his parry or a guard hit, or the "attention" stance (ZL: the shield up as he walks or
// stands) while the shield is in his hand.
static int pup_is_guard(const char *body, const char *upper, int shield_in_hand) {
    if (!shield_in_hand) return 0;
    const char *n[2] = {body ? body : "", upper ? upper : ""};
    for (int i = 0; i < 2; i++)
        if (pup_prefix(n[i], "Guard") || strstr(n[i], "Attention") || strstr(n[i], "Atn")) return 1;
    return 0;
}

// A bone's local turn on the costume for Link's local turn of the same bone (L: his parent's world^T his world), from
// both skeletons' rests (model space): C = (Rc_prest^T Rl_prest) L (Rl_rest^T Rc_rest). The same bodies' rests: C = L;
// a costume whose parent were posed as his (pup_rd_solve) gets the same world turn as pup_rd_solve gives it.
static void pup_ovl_local(const float L[9], const float Rl_prest[9], const float Rl_rest[9], const float Rc_prest[9],
                          const float Rc_rest[9], float C[9]) {
    float A[9], B[9], T[9];
    pup_r_tmul(Rc_prest, Rl_prest, A);
    pup_r_tmul(Rl_rest, Rc_rest, B);
    pup_r_mul(A, L, T);
    pup_r_mul(T, B, C);
}

// The hand overlay's bones (a mask per kind): the fingers of a hand that holds a copy (a closed fist, as Link's), and
// Link's whole left arm while he guards (his shield up). 0.9.5: a whole arm (from its clavicle down) for a body whose
// own stand holds its hands elsewhere (PupGrip.arm) while that hand holds a drawn copy. out: bone b selected; returns
// how many.
enum { OVL_FIST_R = 1, OVL_FIST_L = 2, OVL_GUARD = 4, OVL_ARM_R = 8, OVL_ARM_L = 16 };
static int pup_ovl_pick(const char (*names)[32], const int16_t *parent, int n, int kinds, uint8_t *out) {
    int cnt = 0, left = kinds & (OVL_GUARD | OVL_ARM_L), right = kinds & OVL_ARM_R;
    for (int b = 0; b < n; b++) {
        const char *s = names[b];
        size_t l = strlen(s);
        int fin = pup_prefix(s, "Finger_") && l > 2 && s[l - 2] == '_';
        int sel = fin && (((kinds & OVL_FIST_R) && s[l - 1] == 'R') || ((kinds & OVL_FIST_L) && s[l - 1] == 'L'));
        for (int p = b, hops = 0; !sel && (left || right) && p >= 0 && hops < PUP_MAXB; p = parent[p], hops++)
            if ((left && !strcmp(names[p], "Clavicle_L")) || (right && !strcmp(names[p], "Clavicle_R"))) sel = 1;
        out[b] = (uint8_t)sel;
        cnt += sel;
    }
    return cnt;
}

// 0.9.3: dying starts the Game Over event 0.1-0.2 s into DeadRagdoll: the costume stays while it follows his ragdoll,
// or while he plays a death clip (an event, or his body gone: not "Link shows").
static int pup_is_death(const char *a) {
    return a && (pup_prefix(a, "Dead") || pup_prefix(a, "SwimDead") || pup_prefix(a, "MasterSwordDead"));
}
static int pup_busy_keep(int busy, int rd, const char *anim) {
    if ((busy == PB_EVENT || busy == PB_BODY) && (rd != RD_NONE || pup_is_death(anim))) return PB_FREE;
    return busy;
}

// 0.9.3: the ground clamp's next lift from `shown` towards `want` (up fast, down a little slower; dt 0: at once).
static float pup_clamp_step(float shown, float want, float dt) {
    want = want > PUP_CLAMP_MAX ? PUP_CLAMP_MAX : want < -PUP_CLAMP_MAX ? -PUP_CLAMP_MAX : want;
    if (!(dt > 0.0f) || dt > 0.5f) return want;
    float d = want - shown, tau = (float)(d >= 0 ? PUP_CLAMP_UP_S : PUP_CLAMP_DOWN_S);
    float step = d * (dt >= tau ? 1.0f : dt / tau), cap = PUP_CLAMP_MPS * dt;
    step = step > cap ? cap : step < -cap ? -cap : step;
    return shown + step;
}

// 0.9.3: Daruk's ball keeps rolling through a short dip of the sprint (a frame hitch: the speed from his place dips
// under PUP_SPRINT_BELOW for a frame or two and the ball popped upright). 0.9.5 (over a drop his speed fell to 2 m/s for
// 0.5 s while he still sprinted, and the ball was out): Link is in his sprint while it is picked by his speed
// (picked), or while he still Moves (his walk/run/sprint name; or a sprint name, should his file have one) and his
// stamina fell less than PUP_DRAIN_S ago (since_drain; negative: never), whatever his speed.
static int pup_in_sprint(int picked, const char *link_anim, double since_drain) {
    if (picked) return 1;
    int moves = link_anim && (!strcmp(link_anim, "Move") || !strcmp(link_anim, "RunGear2") || strstr(link_anim, "Dash"));
    return moves && strncmp(link_anim, "Swim", 4) && since_drain >= 0 && since_drain < PUP_DRAIN_S;
}
// ... and the ball keeps rolling until he has been out of his sprint PUP_SPRINT_LEAVE_S (since: since he was last in it).
static int pup_sprint_hold(int rolling, double since) {
    return rolling == 1 && since >= 0 && since < PUP_SPRINT_LEAVE_S;
}

// 0.9.5: an attack's set starts at once, as Link's own attack sets do (their clip nodes have Morph 0 [V: Player_CutS1]):
// the costume's sets blended into a swing over the animation list's 6 frames, which read as a wind-up and put the
// Bokoblin's hit about 0.16 s after Link's (the game check, 2026-11-14). Morph frames for AS_PLAY: 0 for an attack,
// -1 (the set's own) for the rest. 0.9.10: PUP_MOVE_MORPH for a stand, walk or run (the sets' 6 frames read as a slide
// at the start and as a walk on at the stop); the sprint keeps its own (Daruk curls into his ball).
static float pup_as_morph(const char *name) {
    if (!name) return -1.0f;
    if (pup_prefix(name, "Cut")) return 0.0f;
    if (!strcmp(name, AS_WAIT) || !strcmp(name, AS_WALK) || !strcmp(name, AS_RUN)) return PUP_MOVE_MORPH;
    return -1.0f;
}

// 0.9.5: the nocked arrow. With the bow drawn, Link's arrow stayed where his hidden hand is (it doesn't copy his model's
// hide as his items do) and floated next to the costume's head [V: the game check]. An equipment slot from 3 up whose
// actor is an arrow (NormalArrow, FireArrow, ... [G: which slot holds it]) is hidden with Link and shown again once it
// leaves the slot (shot) or Link shows.
static int pup_is_arrow(const char *name) { return name && strstr(name, "Arrow") != NULL; }

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
    uint8_t akind[PUP_RD_UNITS];   // 0.9.2: how each is held (AK_*; 0: not looked at yet)
    uint64_t aclass[PUP_RD_UNITS]; // ... its animation's class (AK_AFTER: not the one with the "runs" bit)
    float slope[2];                // 0.9.2: the ground's slope (smoothed), slope_ok once it has one
    int slope_ok;
    float vel[3], vel_at[3];       // 0.9.2: Link's hips' velocity (smoothed, m/s) and where they were (vel_ok: read)
    int vel_ok;
    double vel_t;
    PupLimp limp;                  // 0.9.2: the loose limbs (the body unit's)
    char lnames[PUP_MAXB][32], names[PUP_MAXB][32];
    float wl[PUP_MAXB][12], cur[PUP_MAXB][12], out[PUP_MAXB][12];
    PupX xl[PUP_MAXB], wp[PUP_MAXB];
} PupRd;
static PupRd t_rd;
static int t_pup_rd_mode;          // the request's RDM_* and girth (pup_tick copies them)
static float t_pup_girth;

// 0.9.2: EVERY posed unit gets the pose again right after the game's own step each frame (unit_step_wrapper, on the
// game's threads), after its clip went into its locals and before its world matrices are made from them: the unit
// and its skeleton (atomics; 0: none), and the tick's latest pose for it in a triple buffer (the tick writes the one
// after the published one, never the one being read). Pausing its animation alone showed the clip (round 3, above).
typedef struct {
    int n;
    int16_t bone[PUP_MAXB];
    float m[PUP_MAXB][12];         // (3x4 rows, as pup_rd_solve gives them)
} PupOvPose;
static SwModGuestFn g_orig_unit_step;   // the game's unit step (0: not wrapped: no after-step pose)
static uint64_t g_pup_ov_unit[PUP_RD_UNITS], g_pup_ov_sk[PUP_RD_UNITS];
static int g_pup_ov_pub[PUP_RD_UNITS], g_pup_ov_any;
static int64_t g_pup_ov_writes;         // poses written after a unit's step (`ghost puppet` ragdoll_after_writes)
static PupOvPose g_pup_ov_pose[PUP_RD_UNITS][3];

static void pup_ov_clear(void) {
    __atomic_store_n(&g_pup_ov_any, 0, __ATOMIC_RELEASE);
    for (int i = 0; i < PUP_RD_UNITS; i++) __atomic_store_n(&g_pup_ov_unit[i], 0, __ATOMIC_RELEASE);
}

// 0.9.9: the model units of the pods hidden now (pup_sheath_tick publishes them every tick; 0: free), for the
// after-step hide in unit_step_wrapper: the game shows a hidden pod again in its own frame [V: sheath_hides ~110/s,
// 2026-11-18], so its units' draw bit is cleared again after their step, before the frame is drawn.
#define PUP_POD_UNITS 16
static uint64_t g_pup_pod_unit[PUP_POD_UNITS];
static int g_pup_pod_any;
static int64_t g_pup_pod_hides;         // draw bits cleared after a step (`ghost puppet` sheath_unit_hides)

// The table becomes units[0..n) (n 0: empty). Called from the tick only.
static void pup_pod_units_publish(const uint64_t *units, int n) {
    if (n > PUP_POD_UNITS) n = PUP_POD_UNITS;
    for (int i = 0; i < PUP_POD_UNITS; i++) __atomic_store_n(&g_pup_pod_unit[i], i < n ? units[i] : 0, __ATOMIC_RELEASE);
    __atomic_store_n(&g_pup_pod_any, n > 0, __ATOMIC_RELEASE);
}

// After unit's own step (game thread): a unit in the table drawn again gets its draw bit cleared. 1: it was cleared.
static int pup_pod_unit_after_step(uint64_t unit) {
    for (int i = 0; i < PUP_POD_UNITS; i++) {
        if (__atomic_load_n(&g_pup_pod_unit[i], __ATOMIC_ACQUIRE) != unit) continue;
        uint16_t *f = (uint16_t *)(uintptr_t)(unit + UNIT_FLAGS);
        if (!(__atomic_load_n(f, __ATOMIC_RELAXED) >> PUP_BIT & 1)) return 0;
        __atomic_fetch_and(f, (uint16_t) ~(1u << PUP_BIT), __ATOMIC_RELAXED);
        __atomic_fetch_add(&g_pup_pod_hides, 1, __ATOMIC_RELAXED);
        return 1;
    }
    return 0;
}

// The puppet went (or a stage change took it): its ragdoll state is forgotten without touching it.
static void pup_ball_forget(void);
static void pup_rd_forget(void) {
    pup_ov_clear();
    pup_pod_units_publish(NULL, 0);  // (0.9.9: no pod hidden after the step)
    t_rd.on = 0, t_rd.end_t = 0, t_rd.lift = 0, t_rd.pup_for = 0, t_rd.npu = 0, t_rd.slope_ok = 0, t_rd.vel_ok = 0;
    memset(t_rd.anim, 0, sizeof t_rd.anim);
    memset(t_rd.akind, 0, sizeof t_rd.akind);
    memset(&t_rd.limp, 0, sizeof t_rd.limp);
    t_pup.rd = RD_NONE, t_pup.rd_lift = 0, t_pup.rd_units = t_pup.rd_mapped = 0, t_pup.rd_rest = 0;
    t_pup.rd_why[0] = t_pup.rd_anim[0] = t_pup.rd_src[0] = 0, t_pup.rd_up = t_pup.rd_want = 0;  // (0.9.2: no stale why)
    t_pup.cloth_bones = 0;  // (0.9.6: read again with its skeleton)
    pup_ball_forget();
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

static void pup_093_forget(void);
static void pup_anim_forget(void) {
    pup_rd_forget();
    pup_093_forget();
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

// A try ended without a puppet: again PUP_RETRY_S later, at most PUP_TRIES in a row; then Link stays. hard: no use
// trying again (the last try). 0.9.5: a try that failed while the game was busy (or was busy since the request: Game
// Over, a load) isn't counted.
static void pup_try_failed(const char *why, double t, int hard) {
    t_pup.proc = 0, t_pup_proc = 0;
    if (!hard && (t_pup_try_busy || t_pup_busy_now != PB_FREE)) {
        t_pup.busy_waits++;
        t_pup.state = PS_WAIT;
        t_pup_retry_at = t + PUP_RETRY_S;
        snprintf(t_pup.why, sizeof t_pup.why, "%s: %s while the game was busy; again once it's free", t_pup.name, why);
        return;
    }
    if (hard) t_pup.tries = PUP_TRIES - 1;
    if (++t_pup.tries >= PUP_TRIES) {
        t_pup.state = PS_FAILED;
        t_pup_soft_fail = !hard;
        t_pup_fail_heap = spawn_heap(), t_pup_fail_player = t_pup_tick_player;
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
        pup_try_failed("too many actor names used; restart the game", t, 1);
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
    t_pup_try_busy = t_pup_busy_now != PB_FREE;  // (0.9.5: busy from here on: a failure isn't counted)
    if (!(gcall(c, REQUEST_CREATE, creator, P(name), heap, P(&g_pup_handle), P(&g_pup_pack), 0, 1) & 1)) {
        GCALL1(c, HANDLE_CLEAR, &g_pup_handle);
        pup_try_failed("the game refused the request", t, 0);
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
            pup_try_failed("not an actor", t, 1);
            return;
        }
        GCALL3(c, SET_MTX, proc, g_pup_mtx, 0);
        GCALL1(c, HANDLE_TAKE, &g_pup_handle);
        GCALL3(c, LINK_SET, &g_pup_link, proc, 0);
        t_pup.proc = t_pup_proc = proc;
        t_pup.state = PS_LIVE;
        t_pup.spawned++;
        t_pup_pick_spawned = 1;  // (0.9.5: this pack can be made: a soft give-up may come back)
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
        pup_try_failed("the game could not create it", t, 0);
    } else if (t - t_pup_pending_since > PUP_PENDING_S) {
        GCALL1(c, HANDLE_RELEASE, &g_pup_handle);
        GCALL1(c, HANDLE_CLEAR, &g_pup_handle);
        pup_try_failed("not ready in time", t, 0);
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
    s->sk = s->unit = 0, s->n = s->nmap = 0, s->hip = s->head = -1, s->uidx = u, s->rest = 0, s->ncloth = 0;
    memset(s->cloth, 0, sizeof s->cloth);
    for (int k = 0; k < PUP_NBODY_BONES; k++) s->keyidx[k] = -1;
    for (int k = 0; k < PUP_LIMP_N; k++) s->limb[k] = -1;
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
        for (int k = 0; k < PUP_LIMP_N; k++)
            if (!strcmp(names[b], PUP_LIMBS[k])) s->limb[k] = (int16_t)b;
        if (!strcmp(names[b], "Skl_Root")) s->hip = b;
        if (!strcmp(names[b], "Head")) s->head = b;
    }
    if (!pup_rd_order(s->parent, n, s->order)) return 0;
    s->unit = unit, s->sk = sk, s->n = n;
    // 0.9.2: its rest pose (each bone's scale, turn and place in the resource, as the model file has them)
    static float sc[PUP_MAXB][3], rot[PUP_MAXB][4], tr[PUP_MAXB][3];
    int euler = (self_u32(res + RES_FLAGS, t) & RES_EULER) != 0;
    for (int b = 0; b < n; b++) {
        uint64_t e = bones + (uint64_t)b * RES_BONE_SIZE;
        for (int k = 0; k < 3; k++)
            sc[b][k] = rdf(e + RES_BONE_SCALE + 4 * (uint64_t)k), tr[b][k] = rdf(e + RES_BONE_TRANS + 4 * (uint64_t)k);
        for (int k = 0; k < 4; k++) rot[b][k] = rdf(e + RES_BONE_ROT + 4 * (uint64_t)k);
    }
    pup_rest_set(s, (const float (*)[3])sc, (const float (*)[4])rot, (const float (*)[3])tr, euler);
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
    if (t_rd.npu && t_rd.pup_for != proc) {  // (another puppet: not ours to thaw)
        pup_ov_clear();
        memset(t_rd.anim, 0, sizeof t_rd.anim);
        memset(t_rd.akind, 0, sizeof t_rd.akind);
    }
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
        pup_cloth_mark(s, (const char (*)[32])t_rd.names, t_pup.id);  // (0.9.6: its physics' bones, never written)
        if (s->nmap >= PUP_RD_MIN_MAP && s->hip >= 0 && s->map[s->hip] == t_rd.link.hip) t_rd.npu++;
    }
    t_pup.cloth_bones = 0;
    for (int i = 0; i < t_rd.npu; i++) t_pup.cloth_bones += t_rd.pu[i].ncloth;
    return t_rd.npu > 0;
}

// Hold each posed unit's animation, so the pose written is the one drawn (0.9.2: per unit, AK_*, kept until thawed):
// - its animation is the class with the "runs" bit: paused (the game's own bit). Also when another unit owns it
//   (its +8): that is the same object, and every unit with it stops (the costume's own parts);
// - it has none: nothing to hold (nothing animates it through its own step);
// - another class (the bit isn't known for it; 0.9.1's "its animation can't be paused", Zelda 2026-11-13): it runs,
//   and its pose is written again right after the game's own step each frame (unit_step_wrapper), when that is
//   wrapped. 0 (why says which unit and class): it isn't.
// A unit with none or another class also gets the after-step pose (one frame of the clip can't show either way).
static int pup_rd_freeze(char *why, size_t cap, double t) {
    for (int i = 0; i < t_rd.npu && !g_orig_unit_step; i++) {  // (first: can each be held? else none is paused)
        uint64_t unit = t_rd.pu[i].unit, a = t_rd.akind[i] ? 0 : self_u64(unit + UNIT_ANIM, t);
        uint64_t vp = a ? self_u64(a, t) : 0;
        if (a && !(vp == ANIM_VPTR && mem_ok(a + ANIM_FLAGS, 1, t))) {
            snprintf(why, cap, "its animation can't be paused (unit %d: class %#llx; no after-step pose)",
                     t_rd.pu[i].uidx, (unsigned long long)vp);
            return 0;
        }
    }
    for (int i = 0; i < t_rd.npu; i++) {
        if (t_rd.akind[i]) continue;
        uint64_t unit = t_rd.pu[i].unit, a = self_u64(unit + UNIT_ANIM, t);
        uint64_t vp = a ? self_u64(a, t) : 0;
        if (!a) {
            t_rd.akind[i] = AK_NONE;
        } else if (vp == ANIM_VPTR && mem_ok(a + ANIM_FLAGS, 1, t)) {
            t_rd.anim_was[i] = __atomic_fetch_and((uint8_t *)(uintptr_t)(a + ANIM_FLAGS), (uint8_t)~ANIM_RUNS,
                                                  __ATOMIC_SEQ_CST);  // (a second unit with the same one: was 0)
            t_rd.anim[i] = a;
            t_rd.akind[i] = self_u64(a + ANIM_UNIT, t) == unit ? AK_PAUSED : AK_SHARED;
        } else {  // (another class, or unreadable: only with the after-step pose, checked above)
            t_rd.akind[i] = AK_AFTER, t_rd.aclass[i] = vp;
        }
    }
    (void)why, (void)cap;
    return 1;
}

// How each posed unit's animation is held, for `ghost puppet` (ragdoll_anim): "u0 paused", "u0 after 0x7001...".
static void pup_rd_anim_text(char *out, size_t cap) {
    size_t n = 0;
    out[0] = 0;
    for (int i = 0; i < t_rd.npu && n < cap; i++) {
        int k = t_rd.akind[i] <= AK_AFTER ? t_rd.akind[i] : 0;
        int w = k == AK_AFTER ? snprintf(out + n, cap - n, "%su%d after %#llx", n ? "; " : "", t_rd.pu[i].uidx,
                                         (unsigned long long)t_rd.aclass[i])
                              : snprintf(out + n, cap - n, "%su%d %s", n ? "; " : "", t_rd.pu[i].uidx, AK_NAMES[k]);
        if (w < 0) break;
        n += (size_t)w;
    }
}

// Its animations run again (where we paused them and they ran before; only the same animation of the same unit), and
// no after-step pose is written any more.
static void pup_rd_thaw(double t) {
    pup_ov_clear();
    for (int i = 0; i < PUP_RD_UNITS; i++) {
        uint64_t a = t_rd.anim[i];
        t_rd.anim[i] = 0, t_rd.akind[i] = 0;
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

// One bone's local transform (3x4 rows), written as the game's own set-local writes it: 4 columns, the flags' bits
// 24-27 cleared. arr: the skeleton's locals.
static void pup_local_put(uint64_t arr, int b, const float m[12]) {
    uint64_t e = arr + (uint64_t)b * LOCAL_SIZE;
    volatile float *f = (volatile float *)(uintptr_t)(e + LOCAL_COLS);
    for (int k = 0; k < 4; k++) {
        for (int r = 0; r < 3; r++) f[k * 4 + r] = m[r * 4 + k];
        f[k * 4 + 3] = 0.0f;
    }
    *(volatile uint32_t *)(uintptr_t)e &= LOCAL_FLAGS_KEEP;
}

// The skeleton's and the unit's "locals changed" bits (the world transforms are made again from them).
static void pup_locals_changed(uint64_t sk, uint64_t unit) {
    __atomic_fetch_or((uint16_t *)(uintptr_t)(sk + SKEL_FLAGS), (uint16_t)SKEL_LOCALS_SET, __ATOMIC_SEQ_CST);
    __atomic_fetch_or((uint8_t *)(uintptr_t)(unit + UNIT_DIRTY), (uint8_t)UNIT_LOCALS_NEW, __ATOMIC_SEQ_CST);
}

// The bones the pose sets: those named as Link's; 0.9.2: with its rest read, every bone (the others at their rest);
// 0.9.6: never one its own physics moves (the cloth's: the pose and the cloth would fight).
static int pup_rd_sets(const PupSkel *s, int b) { return !s->cloth[b] && (s->map[b] >= 0 || s->rest); }

// Write the bones the pose sets (as the game's own set-local does) and the "locals changed" bits; the scale and every
// other bone stay.
static void pup_rd_write(const PupSkel *s, const float (*out)[12]) {
    uint64_t arr = rd64(s->sk + SKEL_LOCAL);  // (pup_rd_locals checked it this frame)
    for (int b = 0; b < s->n; b++)
        if (pup_rd_sets(s, b)) pup_local_put(arr, b, out[b]);
    pup_locals_changed(s->sk, s->unit);
}

// 0.9.2: unit i's pose for the after-step write (unit_step_wrapper): the bones it sets, into the buffer after the
// published one, then published.
static void pup_ov_publish(int i, const PupSkel *s, const float (*out)[12]) {
    int k = (__atomic_load_n(&g_pup_ov_pub[i], __ATOMIC_ACQUIRE) + 1) % 3, n = 0;
    PupOvPose *p = &g_pup_ov_pose[i][k];
    for (int b = 0; b < s->n && n < PUP_MAXB; b++)
        if (pup_rd_sets(s, b)) p->bone[n] = (int16_t)b, memcpy(p->m[n], out[b], sizeof p->m[n]), n++;
    p->n = n;
    __atomic_store_n(&g_pup_ov_pub[i], k, __ATOMIC_RELEASE);
    __atomic_store_n(&g_pup_ov_sk[i], s->sk, __ATOMIC_RELEASE);
    __atomic_store_n(&g_pup_ov_unit[i], s->unit, __ATOMIC_RELEASE);
    __atomic_store_n(&g_pup_ov_any, 1, __ATOMIC_RELEASE);
}

// 0.9.2: the game's per-frame step of a model unit (UNIT_ANIM_STEP, wrapped with hle_override by swmod_init; its only
// caller 0x7001051914 runs the unit's animation list first and makes the world matrices right after it [S]). After
// it, every posed costume unit gets the tick's latest pose again: its clip has just been put into the locals, and the
// world transforms are made from them next. Any other unit: one atomic read. On the game's threads: only plain reads
// of this unit and its skeleton, and writes of its own locals.
// 0.9.9: a hidden pod's unit (pup_pod_unit_after_step) gets its draw bit cleared again here too.
static void unit_step_wrapper(struct Cpu *c) {
    uint64_t unit = SWMOD_REGS(c)->x[0];
    g_orig_unit_step(c);
    if (unit && __atomic_load_n(&g_pup_pod_any, __ATOMIC_ACQUIRE)) pup_pod_unit_after_step(unit);
    if (!unit || !__atomic_load_n(&g_pup_ov_any, __ATOMIC_ACQUIRE)) return;
    for (int i = 0; i < PUP_RD_UNITS; i++) {
        if (__atomic_load_n(&g_pup_ov_unit[i], __ATOMIC_ACQUIRE) != unit) continue;
        uint64_t sk = __atomic_load_n(&g_pup_ov_sk[i], __ATOMIC_ACQUIRE);
        uint64_t arr = sk && rd64(unit + UNIT_SKEL) == sk ? rd64(sk + SKEL_LOCAL) : 0;  // (still its skeleton)
        uint64_t res = arr ? rd64(sk) : 0;
        if (!res) return;
        int nb = *(volatile const uint16_t *)(uintptr_t)(res + RES_NBONES);
        const PupOvPose *p = &g_pup_ov_pose[i][__atomic_load_n(&g_pup_ov_pub[i], __ATOMIC_ACQUIRE) % 3];
        for (int j = 0; j < p->n && j < PUP_MAXB; j++)
            if (p->bone[j] >= 0 && p->bone[j] < nb) pup_local_put(arr, p->bone[j], p->m[j]);
        pup_locals_changed(sk, unit);
        __atomic_fetch_add(&g_pup_ov_writes, 1, __ATOMIC_RELAXED);
        return;
    }
}

// 0.9.6: the physics call's mode (the request's: pup_tick copies it), and a warp seen since the last placing.
static int t_pup_physmtx, t_pup_warp_pending;
static float t_warp_at[3];
static int t_warp_ok;

// Put the puppet at matrix m (rows), dy higher. 0.9.6: its physics (its pack's cloth) is not moved with the physics
// call unless `ghost puppet physmtx` says so (PM_*): a cloth-only physics has no rigid body to move.
static void pup_place(struct Cpu *c, uint64_t proc, const float m[12], float dy) {
    g_pup_pos[0] = m[3], g_pup_pos[1] = m[7] + dy, g_pup_pos[2] = m[11];
    float rot[9] = {m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]};
    memcpy(g_pup_rot, rot, sizeof rot);
    gcall(c, SET_TRANSFORM, proc, P(g_pup_pos), P(g_pup_rot), 0, 0, 0, 0);
    t_pup.physics = rd64(proc + ACTOR_PHYSICS) != 0;
    if (t_pup.physics && (t_pup_physmtx == PM_ON || (t_pup_physmtx == PM_WARP && t_pup_warp_pending))) {
        float pm[12] = {rot[0], rot[1], rot[2], g_pup_pos[0], rot[3], rot[4], rot[5], g_pup_pos[1],
                        rot[6], rot[7], rot[8], g_pup_pos[2]};
        memcpy(g_pup_phys, pm, sizeof pm);
        GCALL2(c, PHYS_SET_MTX, proc, g_pup_phys);
        t_pup.phys_moves++;
    }
    t_pup_warp_pending = 0;
}

// 0.9.6: Link moved more than PUP_WARP_M since the last frame (a teleport on the same stage: a load or a new pick
// spawns a new puppet, a fresh cloth). Pure: (where he is, the last place and whether there is one); 1: a warp.
static int pup_warp_step(const float at[3], float last[3], int *ok) {
    float d[3] = {at[0] - last[0], at[1] - last[1], at[2] - last[2]};
    int warp = *ok && !(pup_len3(d) <= PUP_WARP_M);
    memcpy(last, at, 3 * sizeof *at), *ok = 1;
    return warp;
}

// The follow ends: its animations run again, its clip is asked again.
static void pup_rd_stop(double t) {
    pup_rd_thaw(t);
    t_rd.on = 0, t_rd.end_t = 0, t_rd.lift = 0, t_rd.slope_ok = 0;
    t_pup.rd = RD_NONE, t_pup.rd_lift = 0, t_pup.rd_units = t_pup.rd_mapped = 0, t_pup.rd_up = t_pup.rd_want = 0;
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

static float t_pup_limp = PUP_LIMP;  // the request's limp (pup_tick copies it)

// 0.9.2: Link's velocity from his place each frame (m/s, smoothed over PUP_RD_VEL_S): the ground's slope as he slides
// or tumbles down a hill, and the ball's way and speed. A jump of more than 40 m/s (a teleport, a load) is ignored.
static void pup_vel_sample(const float at[3], double t) {
    if (t_rd.vel_ok && t > t_rd.vel_t && t - t_rd.vel_t < 0.25) {
        float dt = (float)(t - t_rd.vel_t), f = dt >= (float)PUP_RD_VEL_S ? 1.0f : dt / (float)PUP_RD_VEL_S;
        for (int r = 0; r < 3; r++) {
            float v = (at[r] - t_rd.vel_at[r]) / dt;
            if (!(v < 40.0f && v > -40.0f)) v = t_rd.vel[r];
            t_rd.vel[r] += (v - t_rd.vel[r]) * f;
        }
    } else if (!t_rd.vel_ok || t - t_rd.vel_t >= 0.25) {
        memset(t_rd.vel, 0, sizeof t_rd.vel);
    }
    memcpy(t_rd.vel_at, at, sizeof t_rd.vel_at), t_rd.vel_t = t, t_rd.vel_ok = 1;
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
    const PupBody *lb = pup_body("link");
    float plane[3] = {0, 0, m[7]};  // (Link's skeleton unread: level, at his feet)
    if (link_ok && !pup_rd_ground(t_rd.xl, &t_rd.link, lb, 1.0f, plane))
        plane[0] = plane[1] = 0, plane[2] = m[7];
    // 0.9.2: how upright he is (0 lying, 1 standing; sliding on his feet about 0.8)
    float u = link_ok ? pup_rd_upness(t_rd.xl, &t_rd.link, lb) : 0.0f;
    if (now) {
        if (!t_rd.on) {
            t_rd.on = 1, t_rd.mode = RD_NONE, t_rd.slope_ok = 0, t_pup.rd_count++;
            memset(&t_rd.limp, 0, sizeof t_rd.limp);
        }
        t_rd.end_t = 0;
        snprintf(t_pup.rd_src, sizeof t_pup.rd_src, "%s", now == 1 ? "flag" : "anim");
    } else if (t_rd.on) {  // (he gets up: followed until he is up, PUP_RD_TAIL_S at least, PUP_RD_GETUP_S at most)
        if (!t_rd.end_t) t_rd.end_t = t;
        snprintf(t_pup.rd_src, sizeof t_pup.rd_src, "getup");
        int up = !link_ok || u >= PUP_RD_UP_U || pup_rd_upright(t_rd.xl, &t_rd.link, plane);
        if ((up && t - t_rd.end_t >= PUP_RD_TAIL_S) || t - t_rd.end_t >= PUP_RD_GETUP_S) pup_rd_stop(t);
    }
    if (!t_rd.on) return RD_NONE;
    // 0.9.2: the ground the costume goes on: his body's fit, levelled as he stands up (a standing body's fit tips), and
    // the slope of his motion as he moves (a slide or a tumble down a hill follows the hill: across it, his lying body
    // can't tell it, and Daruk's 2 m went into it uphill), its slope smoothed over PUP_RD_SLOPE_S (a tumbling body's
    // fit jumps from frame to frame), then lowered under his body again
    float ground[3];
    memcpy(ground, plane, sizeof ground);
    if (link_ok) {
        float gs[2], wm = pup_motion_slope(t_rd.vel, gs) * (u > 0.5f ? u : 0.5f);
        ground[0] = ground[0] * (1.0f - u) * (1.0f - wm) + gs[0] * wm;
        ground[1] = ground[1] * (1.0f - u) * (1.0f - wm) + gs[1] * wm;
        if (t_rd.slope_ok && dt > 0) {
            float f = dt >= PUP_RD_SLOPE_S ? 1.0f : (float)(dt / PUP_RD_SLOPE_S);
            ground[0] = t_rd.slope[0] + (ground[0] - t_rd.slope[0]) * f;
            ground[1] = t_rd.slope[1] + (ground[1] - t_rd.slope[1]) * f;
        }
        t_rd.slope[0] = ground[0], t_rd.slope[1] = ground[1], t_rd.slope_ok = 1;
        pup_rd_lower(t_rd.xl, &t_rd.link, lb, 1.0f, ground);
    }
    t_pup.rd_up = u;
    int first = t_rd.mode == RD_NONE;
    char why[sizeof t_pup.rd_why] = "";
    if (t_pup_rd_mode == RDM_CLIP) snprintf(why, sizeof why, "ghost puppet ragdoll clip");
    else if (!link_ok) snprintf(why, sizeof why, "Link's skeleton can't be read");
    else if (!pup_rd_puppet(proc, t)) snprintf(why, sizeof why, "its skeleton can't be read, or too few bones are named as Link's");
    else {
        pup_rd_freeze(why, sizeof why, t);  // (why stays "" when every posed unit's animation is held)
        pup_rd_anim_text(t_pup.rd_anim, sizeof t_pup.rd_anim);
    }
    const PupBody *pb = pup_body(t_pup.id);
    float girth = t_pup_girth > 0 ? t_pup_girth : 1.0f, want = 0;
    int has_drop = 0, units = 0, mapped = 0, rest = t_rd.link.rest ? 1 : 0;
    if (!*why) {  // his pose
        const float *h = t_rd.wl[t_rd.link.hip];
        float hip_at[3] = {h[3], h[7], h[11]}, base[12], sc = model_scale(proc, t);
        memcpy(base, m, sizeof base);  // (the costume is drawn at its matrix times its model's own scale)
        for (int k = 0; sc > 0.01f && sc < 100.0f && k < 12; k++) base[k] *= k % 4 == 3 ? 1.0f : sc;
        int all_rest = 1;
        for (int i = 0; i < t_rd.npu; i++) {
            const PupSkel *s = &t_rd.pu[i];
            if (!pup_rd_locals(s, t_rd.cur, t)) continue;
            pup_rd_solve(s, &t_rd.link, t_rd.wl, t_rd.cur, base, hip_at, t_rd.out, t_rd.wp, units ? NULL : &t_rd.limp,
                         t_pup_limp, (float)dt);
            if (!units) {  // (the body: its boxes, from this very pose, every frame)
                float w = pup_rd_want(t_rd.wp, s, pb, girth * pup_rd_scale(pb, s, t_rd.wp), ground, u);
                if (w < 1e29f) want = w, has_drop = 1;
            }
            pup_rd_write(s, t_rd.out);  // (now, and again right after its own step: no clip shows)
            if (g_orig_unit_step) pup_ov_publish(i, s, t_rd.out);
            units++, mapped += s->nmap, all_rest &= s->rest;
        }
        if (!units) snprintf(why, sizeof why, "its bone transforms can't be read");
        if (units && all_rest) rest |= 2;
    }
    t_pup.rd_rest = rest;
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
        gm[3] = x, gm[11] = z, gm[7] = ground[0] * x + ground[1] * z + ground[2];
        if (t_rd.npu && t_rd.pup_for == proc && pup_rd_worlds(&t_rd.pu[0], t_rd.cur, t_rd.wp, t)) {
            // 0.9.2: its own bones as the game placed them (a frame or two ago), moved so that its root is where it
            // goes now, not lifted: the lift from that alone. 0.9.1 took its last lift minus that drop, and with the
            // frames of lag it bobbed 0.4-1.66 m (Zelda, 2026-11-13).
            const PupSkel *s0 = &t_rd.pu[0];
            int r = s0->order[0];  // (a root: no parent)
            float off[3] = {gm[3] - t_rd.wp[r].t[0], gm[7] - t_rd.wp[r].t[1], gm[11] - t_rd.wp[r].t[2]};
            for (int b = 0; b < s0->n; b++)
                for (int k = 0; k < 3; k++) t_rd.wp[b].t[k] += off[k];
            float w = pup_rd_want(t_rd.wp, s0, pb, girth * pup_rd_scale(pb, s0, t_rd.wp), ground, 0.0f);
            if (w < 1e29f) want = w, has_drop = 1;
        }
        at = gm;
    }
    if (!has_drop) want = 0;
    want = want < -PUP_RD_SINK_MAX ? -PUP_RD_SINK_MAX : want > PUP_RD_LIFT_MAX ? PUP_RD_LIFT_MAX : want;
    t_rd.lift = first ? want : pup_rd_follow(t_rd.lift, want, (float)dt, units ? u : 0.0f);
    t_pup.rd = units ? RD_POSE : RD_CLIP, t_pup.rd_lift = t_rd.lift, t_pup.rd_want = want;
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
    swmod_set_s(c, 0, pup_as_morph(name));  // (morph frames: the set's own; 0.9.5: an attack at once)
    gcall(c, AS_PLAY, P(g_pup_action), P(name), 1, 0, 0, 0, 0);
    t_pup.as_calls++;
}

// 0.9.2: Daruk's rolling sprint ball (the tick's; docs/charsel.md "Ragdoll"). His sprint clip (RunGear2 ->
// Common_Move_Ball) is a still curled pose: the game rolls the body itself. So while it plays, the costume is turned
// about its own x (across his motion) at his speed over the ball's radius, its lowest point on the ground under him
// (the slope from his motion), facing the way he goes; the spin starts and stops gently, and when the sprint ends it
// comes back upright (the nearest whole turn) before its next clip.
typedef struct {
    int on;                        // 1 rolling, 2 coming back upright, 0 none
    double end_t, last_t;          // when the sprint ended (2); the last frame
    float theta, spin;             // its turn about its own x (rad, 0 to 2 pi) and spin (rad/s)
    float f[2];                    // the way it faces (x z, unit)
    float r;                       // its radius (m, smoothed; 0: not yet)
    int f_ok;
} PupBall;
static PupBall t_ball;
static int t_pup_ball_off;         // the request's `ball off` (pup_tick copies it)

static void pup_ball_forget(void) {
    t_ball.on = 0, t_ball.theta = t_ball.spin = t_ball.r = 0, t_ball.f_ok = 0;
    t_pup.ball = 0, t_pup.ball_r = t_pup.ball_spin = t_pup.ball_angle = 0;
}

// Each frame while live and not ragdolling: sprinting (the costume asked to play its ball clip) or not. 1: it rolled
// the costume and placed it (it keeps its ball clip while it comes back upright); 0: nothing (place it at Link).
static int pup_ball_tick(struct Cpu *c, uint64_t player, uint64_t proc, const float m[12], int sprinting, double t) {
    double dt = t_ball.last_t > 0 && t > t_ball.last_t && t - t_ball.last_t < 0.5 ? t - t_ball.last_t : 0;
    t_ball.last_t = t;
    if (t_pup_ball_off || !pup_ball_id(t_pup.id)) sprinting = 0;
    if (!sprinting && !t_ball.on) return 0;
    if (sprinting) {
        if (!t_ball.on) t_pup.balls++, t_ball.theta = t_ball.spin = 0;
        t_ball.on = 1;
    } else if (t_ball.on == 1) {
        t_ball.on = 2, t_ball.end_t = t;
    }
    // its pose in its own frame (its clip's, from its locals) and its shape
    const PupBody *pb = pup_body(t_pup.id);
    float girth = t_pup_girth > 0 ? t_pup_girth : 1.0f, mid[3], r, sc = model_scale(proc, t);
    if (!(sc > 0.01f && sc < 100.0f)) sc = 1.0f;
    if (!pup_rd_link(player, t) || !pup_rd_puppet(proc, t) || !pup_rd_locals(&t_rd.pu[0], t_rd.cur, t)) {
        pup_ball_forget();
        return 0;
    }
    const PupSkel *s = &t_rd.pu[0];
    pup_fk(s, (const float (*)[12])t_rd.cur, sc, t_rd.wp);
    if (!pup_ball_shape(t_rd.wp, s, pb, girth, mid, &r)) {
        pup_ball_forget();
        return 0;
    }
    float fdt = (float)dt;
    r = r < PUP_BALL_R_MIN ? PUP_BALL_R_MIN : r;
    t_ball.r = t_ball.r > 0 && fdt > 0 ? t_ball.r + (r - t_ball.r) * (fdt >= 0.2f ? 1.0f : fdt / 0.2f) : r;
    // the way he goes (his velocity; slow: the way he faces), smoothed
    const float *v = t_rd.vel;
    float h = __builtin_sqrtf(v[0] * v[0] + v[2] * v[2]), want_f[2];
    if (h > 0.5f) want_f[0] = v[0] / h, want_f[1] = v[2] / h;
    else {
        float lz = __builtin_sqrtf(m[2] * m[2] + m[10] * m[10]);
        want_f[0] = lz > 1e-4f ? m[2] / lz : 0.0f, want_f[1] = lz > 1e-4f ? m[10] / lz : 1.0f;
    }
    if (!t_ball.f_ok || !(fdt > 0)) {
        memcpy(t_ball.f, want_f, sizeof t_ball.f), t_ball.f_ok = 1;
    } else {
        float k = fdt >= 0.1f ? 1.0f : fdt / 0.1f;
        t_ball.f[0] += (want_f[0] - t_ball.f[0]) * k, t_ball.f[1] += (want_f[1] - t_ball.f[1]) * k;
        float l = __builtin_sqrtf(t_ball.f[0] * t_ball.f[0] + t_ball.f[1] * t_ball.f[1]);
        if (l > 1e-3f) t_ball.f[0] /= l, t_ball.f[1] /= l;
        else memcpy(t_ball.f, want_f, sizeof t_ball.f);
    }
    // the ground under him: the slope of his motion; his speed along it
    float gs[2], w = pup_motion_slope(v, gs), n[3] = {-gs[0] * w, 1.0f, -gs[1] * w};
    float nl = pup_len3(n), speed = w > 0 ? pup_len3(v) : h;
    for (int k = 0; k < 3; k++) n[k] /= nl;
    const float TWO_PI = 6.2831853f;
    if (t_ball.on == 1) {
        float target = speed / t_ball.r;
        t_ball.spin += (target - t_ball.spin) * (fdt >= (float)PUP_BALL_SPIN_S ? 1.0f : fdt / (float)PUP_BALL_SPIN_S);
        t_ball.theta += t_ball.spin * fdt;
        while (t_ball.theta >= TWO_PI) t_ball.theta -= TWO_PI;
        while (t_ball.theta < 0) t_ball.theta += TWO_PI;
    } else {  // (upright again: the nearest whole turn, then its next clip)
        float goal = t_ball.theta > 3.14159265f ? TWO_PI : 0.0f;
        t_ball.theta += (goal - t_ball.theta) * (fdt >= (float)PUP_BALL_END_S ? 1.0f : fdt / (float)PUP_BALL_END_S);
        t_ball.spin = 0;
        if (__builtin_fabsf(goal - t_ball.theta) < 0.03f || t - t_ball.end_t >= PUP_BALL_END_MAX) {
            pup_ball_forget();
            return 0;
        }
    }
    float gp[3] = {m[3], m[7] + t_pup.lift, m[11]}, out[12];
    pup_ball_place(t_rd.wp, s, pb, girth, mid, t_ball.f, t_ball.theta, gp, n, out);
    pup_place(c, proc, out, 0.0f);
    t_pup.ball = t_ball.on, t_pup.ball_r = t_ball.r, t_pup.ball_spin = t_ball.spin, t_pup.ball_angle = t_ball.theta;
    return 1;
}

// ---- 0.9.3 the game side: the ground clamp, the hands, the held copies' grip (docs/charsel.md "Held items")

static double t_pup_now;           // this tick's time (pup_items_holder)
static int t_pup_clamp_off, t_pup_hands_off;  // the request's (pup_tick copies them)
static PupGrip t_pup_grip_use = {"link", 1.0f, {0, 0, 0}};  // the grip in use: the body's, or `ghost puppet grip`
static double t_pup_sprint_t;      // when the sprint (RunGear2) was last picked
static double t_pup_in_sprint_t;   // 0.9.5: when Link was last in his sprint (pup_in_sprint; 0: not yet)
static double t_stam_drain_t;      // ... when his stamina last fell (0: not yet)
static float t_stam_last;          // ... and what it was last frame (t_stam_ok: read)
static int t_stam_ok;
static PupKeep t_pit[3];           // the held names kept through a short loss (pup_items_holder)
static struct {
    float lift;
    double last_t;
    int ok;                        // (0: the next one is taken at once)
} t_clamp;
static int t_ovl_on;               // the hand overlay published a pose (taken back when it ends)

// A new puppet, or none: no clamp, no hands (pup_rd_forget took the after-step pose back), no sprint kept.
static void pup_093_forget(void) {
    memset(&t_clamp, 0, sizeof t_clamp);
    t_ovl_on = 0, t_pup_sprint_t = 0;
    t_pup_in_sprint_t = t_stam_drain_t = 0, t_stam_ok = 0;  // (0.9.5)
    t_pup.clamp = t_pup.clamp_want = 0, t_pup.clamp_by[0] = 0, t_pup.hands = t_pup.hand_bones = 0;
    t_pup.sprint_since = -1.0f;
    t_warp_ok = 0, t_pup_warp_pending = 0, t_pup.physics = 0;  // (0.9.6: a new puppet: no warp from the last place)
}

// 0.9.5: the costume's own equipment holder, an NPC's (vf_98 returns this+PUP_NPC_HOLDER: the class of Link's,
// EQUIP_VPTR, its owner at +EQUIP_OWNER [S: out/research/ghost-items-game-attach.md]). Its slot byte "on the back"
// (EQUIP_BACK; the NPC's own set-up writes 1) is what a held item's AI asks each frame (Weapon vf_153), and while it says
// "on the back" the item's AI is in its sheathed state: the game's own place, the NPC's Pod_A with the item's NPC
// offsets. That is exactly what the round-4 check saw [V: Urbosa's Pod_A rests 28 cm under her Spine_2 (1.26 m, her
// hips), a spear's NPCHoldTransOffset is (0, 0, -0.85) with no turn: sideways across her hips; a sword's is 0: sticking
// out sideways at Paya's and Revali's Pod_A, shoulder height]. A DemoNPC holder (Zelda, Daruk, the Bokoblin) has no
// such byte: always "in hand", and our own back follow held there (the Link-body ghost's back, 2026-11-11). So for each
// slot holding one of our copies the byte is kept 0 ("in hand"): the item stays in its drawn state and our follow (its
// hand grip, or Link's back place on its Spine_2) is never sheathed over. 1: the holder is there; 0: none.
#define PUP_NPC_HOLDER 0xd00
static int pup_holder_in_hand(uint64_t proc, double t) {
    uint64_t h = proc + PUP_NPC_HOLDER;
    if (!proc || !mem_ok(h, EQUIP_OWNER + 8, t) || rd64(h) != EQUIP_VPTR || rd64(h + EQUIP_OWNER) != proc) return 0;
    for (int i = 0; i < 3 && i < ITEM_SLOTS; i++) {
        if (t_items_ghost != proc || (t_items[i].state != IS_GRAB && t_items[i].state != IS_HELD)) continue;
        volatile uint8_t *b = (volatile uint8_t *)(uintptr_t)(h + EQUIP_LINKS + (uint64_t)i * EQUIP_LINK_SIZE + EQUIP_BACK);
        if (*b) *b = 0, t_pup.holder_writes++;
    }
    return 1;
}

// 0.9.5: Link's nocked arrow, hidden while the costume shows (pup_is_arrow). The actor a link names (data, id), 0: gone.
static struct {
    uint64_t actor, data;
    uint32_t id;
} t_arrow;
static uint64_t pup_link_proc(uint64_t data, uint32_t id, double t) {
    if (!data || id == 0xffffffffu || self_u32(data + LINKDATA_ID, t) != id) return 0;
    uint64_t p = self_u64(data + LINKDATA_PROC, t);
    return p && mem_ok(p + PROC_STATE, 1, t) && rd8(p + PROC_STATE) != PROC_DELETING ? p : 0;
}
static void pup_arrow_tick(struct Cpu *c, uint64_t player, int hide, double t) {
    uint64_t a = 0, data = 0;
    uint32_t id = 0xffffffffu;
    char nm[64] = "";
    if (hide && player && self_u64(player + ACTOR_EQUIP, t) == EQUIP_VPTR &&
        self_u64(player + ACTOR_EQUIP + EQUIP_OWNER, t) == player)
        for (int k = 3; k < 6 && !a; k++) {  // (slots 3-5: past the weapon, the shield and the bow)
            uint64_t w = self_item(player, k, nm, NULL, t);
            if (w && pup_is_arrow(nm)) {
                uint64_t l = player + ACTOR_EQUIP + EQUIP_LINKS + (uint64_t)k * EQUIP_LINK_SIZE;
                a = w, data = self_u64(l, t), id = self_u32(l + 8, t);
                snprintf(t_pup.arrow, sizeof t_pup.arrow, "%s", nm);
            }
        }
    if (t_arrow.actor && t_arrow.actor != a) {  // (shot, or Link shows: drawn again if it is still there)
        uint64_t still = pup_link_proc(t_arrow.data, t_arrow.id, t);
        if (still == t_arrow.actor) pup_draw(c, still, 1, t);
        memset(&t_arrow, 0, sizeof t_arrow);
    }
    if (a) {
        if (pup_draw(c, a, 0, t)) t_pup.arrow_hides++;
        t_arrow.actor = a, t_arrow.data = data, t_arrow.id = id;
    }
    t_pup.arrow_hidden = t_arrow.actor != 0;
}

// 0.9.8 (the user, 2026-11-15: with a sword drawn, the EMPTY sheath sticks out sideways on Revali, Paya and the
// Hylian). Link's own sheath: the weapon's pod actor (Weapon_Sheath_* / _Lsheath_* / _SpearSheath_*, the bow's
// Weapon_Quiver_001: class OptionalWeapon, AI `Pod`, BindNodeName Pod_A [V: ActorInfo, the pack files,
// out/research/ghost-items-manual-bone.md]) stays on his back whether the sword is drawn or sheathed, placed by the
// player's own code with his PlayerHold offsets. On a costume the game makes the pod for the NPC holder too and binds it
// to the NPC's own Pod_A with no player offsets: sideways at shoulder height on Paya and Revali (where the round-4
// sword stuck out [V]), at the hips on Urbosa. Its place can't be ours (its own AI binds it each frame [G]), so a pod of
// one of our copies is hidden while the costume holds it: the copy hangs bare on the costume's back (our back place,
// Link's on its Spine_2) and shows gripped in the hand when drawn. Found two ways (which one the game uses for an NPC
// holder isn't known [G]): the copy's pod link (ITEM_POD, set right after the game makes it [S: 0x700121d384]) and the
// costume holder's slots 3-5 (where Link's arrow sits). Hidden with the game's own model show/hide, as Link's arrow.
#define ITEM_POD 0x958  // item: link to its sheath / quiver actor (data*, u32 id)
#define PUP_PODS 6
static int pup_is_pod(const char *name) {
    return name && !strncmp(name, "Weapon_", 7) && (strstr(name, "heath") || strstr(name, "Quiver"));
}
// The pod a game link at `at` names (its name into nm), else 0.
static uint64_t pup_pod_at(uint64_t at, char nm[64], double t) {
    nm[0] = 0;
    uint64_t a = link_at(at, t);
    if (!a || !mem_ok(a + PROC_STATE, 1, t) || rd8(a + PROC_STATE) == PROC_DELETING) return 0;
    if (!self_text(self_u64(a + PROC_NAME, t), nm, 64, t) || !pup_is_pod(nm)) return 0;
    return a;
}
// 0.9.9: model m's units (as pup_drawn reads them) added to units[*nu] (up to PUP_POD_UNITS), for the after-step hide.
static void pup_pod_units_add(uint64_t m, uint64_t units[PUP_POD_UNITS], int *nu, double t) {
    int32_t n = m ? (int32_t)self_u32(m + MODEL_NUNITS, t) : 0;
    uint64_t arr = n > 0 && n <= 64 ? self_u64(m + MODEL_UNITS, t) : 0;
    for (int i = 0; arr && i < n && *nu < PUP_POD_UNITS; i++) {
        uint64_t slot = self_u64(arr + (uint64_t)i * 8, t), unit = slot ? self_u64(slot, t) : 0;
        if (unit && mem_ok(unit + UNIT_FLAGS, 2, t)) units[(*nu)++] = unit;
    }
}
static void pup_pod_hide(struct Cpu *c, uint64_t pod, const char *nm, uint64_t seen[PUP_PODS], int *n,
                         uint64_t units[PUP_POD_UNITS], int *nu, double t) {
    for (int k = 0; k < *n; k++)
        if (seen[k] == pod) return;
    if (*n < PUP_PODS) seen[(*n)++] = pod;
    if (pup_draw(c, pod, 0, t)) t_pup.sheath_hides++;
    pup_pod_units_add(pup_model(pod, t), units, nu, t);  // (0.9.9: hidden again after the game's step of each)
    snprintf(t_pup.sheath, sizeof t_pup.sheath, "%s", nm);
}
static void pup_sheath_tick(struct Cpu *c, uint64_t proc, int live, double t) {
    uint64_t seen[PUP_PODS], units[PUP_POD_UNITS];
    int n = 0, nu = 0;
    char nm[64];
    if (live && proc && t_items_ghost == proc) {
        for (int i = 0; i < ITEM_SLOTS; i++) {
            uint64_t it = t_status.items.slot[i].actor;
            if (!it || (t_items[i].state != IS_GRAB && t_items[i].state != IS_HELD) || !mem_ok(it + ITEM_POD, 12, t))
                continue;
            uint64_t pod = pup_pod_at(it + ITEM_POD, nm, t);
            if (pod) pup_pod_hide(c, pod, nm, seen, &n, units, &nu, t);
        }
        uint64_t h = proc + PUP_NPC_HOLDER;
        if (mem_ok(h, EQUIP_OWNER + 8, t) && rd64(h) == EQUIP_VPTR && rd64(h + EQUIP_OWNER) == proc)
            for (int k = 3; k < 6; k++) {
                uint64_t pod = pup_pod_at(h + EQUIP_LINKS + (uint64_t)k * EQUIP_LINK_SIZE, nm, t);
                if (pod) pup_pod_hide(c, pod, nm, seen, &n, units, &nu, t);
            }
    }
    t_pup.sheaths = n;
    pup_pod_units_publish(units, nu);  // (0.9.9: an empty table when none)
}

// For ghost.c's items part: the self puppet's copy in slot `slot` (on its back or in its hand): the bone, the grip, the
// size.
static int pup_item_grip(int slot, int back, const char **bone, float G[12], float *scale) {
    pup_grip_place(&t_pup_grip_use, pup_body(t_pup.id), slot, back, bone, G);
    *scale = t_pup_grip_use.scale > 0.05f && t_pup_grip_use.scale < 20.0f ? t_pup_grip_use.scale : 1.0f;
    return 1;
}

// The ground clamp in every state (not the ragdoll, not the ball: they place it themselves): Link's attack clips move
// his hips down and a costume playing them (retargeted) took that with its own longer legs into the ground (Urbosa sank
// attacking, 2026-11-13); a shorter body floated. Each frame: the costume's lowest foot (its feet boxes, posed by its
// clip: its locals, in its own frame) goes as high as Link's lowest foot (his skeleton, his feet boxes), so it stands
// where he stands in attacks, landings, crouching, on slopes, in the air. Swimming: its head at his head's height instead
// (his head is over the water: Mipha's was under it, the Moblin showed Link). Eased (pup_clamp_step). Returns the lift.
static float pup_clamp_tick(uint64_t player, uint64_t proc, const float m[12], int head, double t) {
    float dt = t_clamp.last_t > 0 && t > t_clamp.last_t ? (float)(t - t_clamp.last_t) : 0.0f;
    t_clamp.last_t = t;
    t_pup.clamp_by[0] = 0;
    if (t_pup_clamp_off) {
        t_clamp.lift = t_pup.clamp = t_pup.clamp_want = 0.0f;
        return 0.0f;
    }
    float want = t_clamp.lift;  // (unread: as it is)
    if (pup_rd_link(player, t) && pup_rd_worlds(&t_rd.link, t_rd.wl, t_rd.xl, t) && pup_rd_puppet(proc, t) &&
        pup_rd_locals(&t_rd.pu[0], t_rd.cur, t)) {
        const PupSkel *s = &t_rd.pu[0], *l = &t_rd.link;
        float sc = model_scale(proc, t);
        if (!(sc > 0.01f && sc < 100.0f)) sc = 1.0f;
        pup_fk(s, (const float (*)[12])t_rd.cur, sc, t_rd.wp);
        if (head && s->head >= 0 && l->head >= 0) {
            want = (t_rd.xl[l->head].t[1] - m[7]) - t_rd.wp[s->head].t[1];
            snprintf(t_pup.clamp_by, sizeof t_pup.clamp_by, "head");
        } else if (!head) {
            const PupBody *pb = pup_body(t_pup.id), *lb = pup_body("link");
            float girth = t_pup_girth > 0 ? t_pup_girth : 1.0f, level[3] = {0, 0, 0};
            float ll = pup_rd_drop_m(t_rd.xl, l, lb, 1.0f, level, pup_rd_feet());
            float cl = pup_rd_drop_m(t_rd.wp, s, pb, girth * pup_rd_scale(pb, s, t_rd.wp), level, pup_rd_feet());
            if (ll < 1e29f && cl < 1e29f) {
                want = (ll - m[7]) - cl;
                snprintf(t_pup.clamp_by, sizeof t_pup.clamp_by, "feet");
            }
        }
    }
    if (!t_clamp.ok) t_clamp.lift = pup_clamp_step(t_clamp.lift, want, 0.0f), t_clamp.ok = 1;  // (new: at once)
    else if (dt > 0.0f) t_clamp.lift = pup_clamp_step(t_clamp.lift, want, dt);
    t_pup.clamp = t_clamp.lift, t_pup.clamp_want = want;
    return t_clamp.lift;
}

// Unit i's pose for the after-step write, the bones in sel only (pup_ov_publish: those the ragdoll sets).
static void pup_ov_publish_sel(int i, const PupSkel *s, const float (*out)[12], const uint8_t *sel) {
    int k = (__atomic_load_n(&g_pup_ov_pub[i], __ATOMIC_ACQUIRE) + 1) % 3, n = 0;
    PupOvPose *p = &g_pup_ov_pose[i][k];
    for (int b = 0; b < s->n && n < PUP_MAXB; b++)
        if (sel[b]) p->bone[n] = (int16_t)b, memcpy(p->m[n], out[b], sizeof p->m[n]), n++;
    p->n = n;
    __atomic_store_n(&g_pup_ov_pub[i], k, __ATOMIC_RELEASE);
    __atomic_store_n(&g_pup_ov_sk[i], s->sk, __ATOMIC_RELEASE);
    __atomic_store_n(&g_pup_ov_unit[i], s->unit, __ATOMIC_RELEASE);
    __atomic_store_n(&g_pup_ov_any, 1, __ATOMIC_RELEASE);
}

// The hands (not during the ragdoll or the ball): a hand holding a copy closes as Link's does (his fingers' turns), and
// while he guards with his shield in hand his whole left arm is his (the shield raised: Urbosa walked on with her arm
// down, 2026-11-13); the rest is the costume's own clip. Written right after each unit's own step (unit_step_wrapper),
// those bones only; taken back when it ends. ok: it may show now; rd: the ragdoll owns the after-step pose now.
static void pup_ovl_tick(uint64_t player, uint64_t proc, int ok, int rd, double t) {
    int kinds = 0, bones = 0;
    if (ok && !t_pup_hands_off && g_orig_unit_step && proc && t_items_ghost == proc) {
        const ItemsStatus *is = &t_status.items;
        int r = !strcmp(is->slot[0].place, "grip"), sh = !strcmp(is->slot[1].place, "grip");
        int l = sh || !strcmp(is->slot[2].place, "grip");
        const SelfState *se = &t_status.self;
        kinds = (r ? OVL_FIST_R : 0) | (l ? OVL_FIST_L : 0) |
                (pup_is_guard(se->body, se->upper_w > SELF_WEIGHT_MIN ? se->upper : "", sh) ? OVL_GUARD : 0);
        if (t_pup_grip_use.arm) kinds |= (r ? OVL_ARM_R : 0) | (l ? OVL_ARM_L : 0);  // (0.9.5: Paya, Urbosa)
    }
    if (kinds && pup_rd_link(player, t) && t_rd.link.rest && pup_rd_worlds(&t_rd.link, t_rd.wl, t_rd.xl, t) &&
        pup_rd_puppet(proc, t)) {
        static uint8_t sel[PUP_MAXB];
        static char nm[PUP_MAXB][32];
        for (int i = 0; i < t_rd.npu; i++) {
            const PupSkel *s = &t_rd.pu[i];
            int cnt = 0;
            for (int b = 0; s->rest && b < s->n; b++)  // (its bones by Link's names: the ones it can take from him)
                snprintf(nm[b], sizeof nm[b], "%s", s->map[b] >= 0 ? t_rd.lnames[s->map[b]] : "");
            if (s->rest && pup_ovl_pick((const char (*)[32])nm, s->parent, s->n, kinds, sel)) {
                for (int b = 0; b < s->n; b++) {
                    int lb = s->map[b], p = s->parent[b], lp = lb >= 0 ? t_rd.link.parent[lb] : -1;
                    if (!sel[b] || lb < 0 || lp < 0 || p < 0 || s->cloth[b]) {  // (0.9.6: not the cloth's)
                        sel[b] = 0;
                        continue;
                    }
                    float L[9], C[9];
                    pup_r_tmul(t_rd.xl[lp].R, t_rd.xl[lb].R, L);
                    pup_ovl_local(L, t_rd.link.rw[lp], t_rd.link.rw[lb], s->rw[p], s->rw[b], C);
                    for (int r = 0; r < 3; r++) {
                        t_rd.out[b][r * 4] = C[r * 3], t_rd.out[b][r * 4 + 1] = C[r * 3 + 1];
                        t_rd.out[b][r * 4 + 2] = C[r * 3 + 2], t_rd.out[b][r * 4 + 3] = s->rl[b][r * 4 + 3];
                    }
                    cnt++;
                }
            }
            if (cnt) pup_ov_publish_sel(i, s, (const float (*)[12])t_rd.out, sel), bones += cnt;
            else __atomic_store_n(&g_pup_ov_unit[i], 0, __ATOMIC_RELEASE);
        }
    }
    if (bones) {
        t_ovl_on = 1, t_pup.hand_frames++;
    } else if (t_ovl_on) {
        if (!rd) pup_ov_clear();  // (the ragdoll's own pose is published now: not ours to take back)
        t_ovl_on = 0;
    }
    t_pup.hands = bones ? kinds : 0, t_pup.hand_bones = bones;
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
    float at[3] = {m[3], m[7], m[11]};
    pup_vel_sample(at, t);  // (0.9.2: his velocity: the ground's slope, the ball)
    if (pup_warp_step(at, t_warp_at, &t_warp_ok)) t_pup.warps++, t_pup_warp_pending = 1;  // (0.9.6: a cloth whip risk)
    // 0.9.1: his ragdoll: followed (his pose) or its lying clip, placed on the ground under him; else at his place
    // (0.9.2: or rolled, Daruk's sprint ball: below, once its clip is picked)
    int was_rd = t_pup.rd != RD_NONE;
    float rd_lift0 = t_pup.rd_lift;
    int rd = pup_rd_tick(c, player, proc, self, m, t);
    if (was_rd && rd == RD_NONE) t_clamp.lift = rd_lift0, t_clamp.last_t = t, t_clamp.ok = 1;  // (0.9.3: on from there)
    if (rd != RD_NONE && t_ball.on) pup_ball_forget();
    // Link's speed from his own place (exact: no network)
    anim_sample(&t_pup_anim, m[3], m[11], t);
    int autoc = anim_auto_step(&t_pup_anim, t, -1.0f);
    t_pup.speed = t_pup_anim.speed;
    uint64_t holder = rd64(proc + ACTOR_AS);
    t_pup.as_ok = holder && rd64(holder + AS_OWNER) == proc && *(volatile const int32_t *)(uintptr_t)(holder + AS_SLOTS) > 0;
    if (!t_pup.as_ok) {
        if (rd == RD_NONE) pup_place(c, proc, m, t_pup.lift);
        snprintf(t_pup.why, sizeof t_pup.why, "%s has no animations yet", t_pup.name);
        return 0;
    }
    copy_name(t_pup.link_anim, self->found ? self->anim : "");
    if (rd == RD_POSE) {  // (posed like him: no clip; its animation is paused until he is up)
        pup_ovl_tick(player, proc, 0, 1, t);
        copy_name(t_pup.as_want, "");
        t_pup.why[0] = 0;
        return 1;
    }
    const char *pick = rd == RD_CLIP ? pup_rd_clip(&t_pup_missing, autoc)
                                     : pup_pick(t_pup.link_anim, t_pup.speed, pup_now_speed(&t_pup_anim, PUP_NOW_S), autoc,
                                                &t_pup_sprint, &t_pup_missing);
    static char swing[40];  // (0.9.4: the held weapon's swing; interned below)
    if (rd == RD_NONE)
        pick = pup_weapon_pick(pick, self->equip_ok ? self->weapon : "", pup_own_swings(t_pup.id), &t_pup_missing, swing,
                               sizeof swing);
    // 0.9.2: Daruk's sprint ball rolls (and keeps its clip while it comes back upright); 0.9.3: also through a short
    // dip of the sprint (a frame hitch popped it upright); 0.9.5: as long as Link is in his sprint, whatever his speed
    // (his stamina falls while he Moves), until he has been out of it PUP_SPRINT_LEAVE_S
    float st = g_pup_stamina();
    t_pup.stamina = st;
    if (st >= 0 && t_stam_ok && st < t_stam_last - PUP_DRAIN_MIN) t_stam_drain_t = t;
    t_stam_last = st, t_stam_ok = st >= 0;
    int sprinting = rd == RD_NONE && pick && !strcmp(pick, "RunGear2");
    if (sprinting) t_pup_sprint_t = t;
    if (pup_in_sprint(sprinting, t_pup.link_anim, t_stam_drain_t > 0 ? t - t_stam_drain_t : -1.0)) t_pup_in_sprint_t = t;
    t_pup.sprint_since = t_pup_in_sprint_t > 0 ? (float)(t - t_pup_in_sprint_t) : -1.0f;
    if (!sprinting && rd == RD_NONE && pick && pup_sprint_hold(t_ball.on, t_pup_in_sprint_t > 0 ? t - t_pup_in_sprint_t : -1.0))
        pick = "RunGear2", sprinting = 1;
    int ball = rd == RD_NONE && pup_ball_tick(c, player, proc, m, sprinting, t);
    if (ball) {
        pick = "RunGear2";
    } else if (rd == RD_NONE) {  // 0.9.3: at his place, its feet where his are (its head, swimming)
        float cl = pup_clamp_tick(player, proc, m, pup_prefix(t_pup.link_anim, "Swim"), t);
        pup_place(c, proc, m, t_pup.lift + cl);
    }
    if (ball) t_clamp.ok = 0, t_clamp.lift = 0, t_pup.clamp_by[0] = 0;  // (after the ball: at once, from its own feet)
    else if (rd != RD_NONE) t_pup.clamp_by[0] = 0;
    pup_ovl_tick(player, proc, rd == RD_NONE && !ball, rd != RD_NONE, t);
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

// 0.9.2: why Link shows now, from the game's own states (PB_*; PB_FREE: he is free): no world (a load), an event
// running (a cutscene, a talk: the event manager's field, trusted once it was seen 0), or no body of his own (his
// physics' position chain is gone: a load, riding). No longer his real position's 1 m check against the player
// info's copy (botw_player_real_pos): sliding fast down a hill the copy lags more than 1 m, and Link showed for 0.4 s
// (2026-11-13). That gap is only measured now (real_gap, gap_frames: frames the old check would have shown Link).
static int pup_busy_real(uint64_t pi, uint64_t player, double t) {
    (void)pi;
    float copy[3];
    if (!botw_player_pos(api, copy)) return PB_WORLD;
    uint64_t mgr = self_u64(PUP_EVENT_MGR, t);
    int ev = mgr && mem_ok(mgr + PUP_EVENT_RUNNING, 8, t) ? rd64(mgr + PUP_EVENT_RUNNING) != 0 : -1;
    if (ev == 0) t_pup_ev_zero = 1;
    t_pup.event = ev < 0 ? -1 : !t_pup_ev_zero ? -2 : ev;
    if (ev == 1 && t_pup_ev_zero) return PB_EVENT;
    uint64_t p = player ? self_u64(player + BOTW_ACTOR_PHYSICS, t) : 0;
    p = p ? self_u64(p + BOTW_POS_STEP_A, t) : 0;
    p = p ? self_u64(p + BOTW_POS_STEP_B, t) : 0;
    if (!p || !mem_ok(p + BOTW_POS_XYZ, 12, t)) return PB_BODY;
    float dx = rdf(p + BOTW_POS_XYZ) - copy[0], dz = rdf(p + BOTW_POS_XYZ + 8) - copy[2];
    float gap = __builtin_sqrtf(dx * dx + dz * dz);
    if (gap == gap && gap < 1e4f) {
        t_pup.real_gap = gap;
        if (gap > t_pup.real_gap_max) t_pup.real_gap_max = gap;
        if (dx > 1.0f || dx < -1.0f || dz > 1.0f || dz < -1.0f) t_pup.gap_frames++;
    }
    return PB_FREE;
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
    t_pup_limp = rq.limp_set ? rq.limp : PUP_LIMP, t_pup_ball_off = rq.ball_off;
    t_pup_now = t, t_pup_clamp_off = rq.clamp_off, t_pup_hands_off = rq.hands_off;  // 0.9.3
    t_pup_physmtx = rq.physmtx >= PM_OFF && rq.physmtx <= PM_ON ? rq.physmtx : PM_OFF;  // 0.9.6
    t_pup_grip_use = *pup_grip(id);
    if (rq.grip_set) {
        t_pup_grip_use.scale = rq.grip_scale;
        memcpy(t_pup_grip_use.hand, rq.grip_hand, sizeof t_pup_grip_use.hand);
    }
    if (rq.back_set) memcpy(t_pup_grip_use.back, rq.back, sizeof t_pup_grip_use.back);  // 0.9.5
    t_pup.grip_scale = t_pup_grip_use.scale;
    memcpy(t_pup.grip_hand, t_pup_grip_use.hand, sizeof t_pup.grip_hand);
    memcpy(t_pup.back, t_pup_grip_use.back, sizeof t_pup.back);
    if (strcmp(id, t_pup.id) || (actor && strcmp(actor, t_pup.actor)) || (!actor && t_pup.state != PS_OFF) ||
        (rq.retry_seq != t_pup_retry_seq && t_pup.state == PS_FAILED)) {  // a new pick: start over
        t_pup_retry_seq = rq.retry_seq;
        if (t_pup.state != PS_OFF) pup_remove(c, player, t);
        pup_anim_forget();  // (0.9.2: nothing of the last pick's ragdoll or ball is said for this one)
        t_pup.real_gap_max = 0, t_pup.gap_frames = 0;
        memset(t_pup.id, 0, sizeof t_pup.id);
        memcpy(t_pup.id, id, strlen(id));
        t_pup.name[0] = t_pup.playas[0] = t_pup.actor[0] = t_pup.why[0] = 0, t_pup.tries = 0;
        t_pup.state = PS_OFF;
        t_pup_pick_spawned = t_pup_soft_fail = t_pup_try_busy = 0;  // (0.9.5)
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
    // 0.9.5: the game's own state (an event such as Game Over, a load, no body of his own), before the costume's
    // death rule: no request while it is busy, and a try that fails then isn't counted (pup_try_failed)
    t_pup_tick_player = player;
    int want = t_pup.state == PS_WAIT || t_pup.state == PS_PENDING || t_pup.state == PS_LIVE;
    t_pup_busy_now = !want ? PB_FREE : player ? g_pup_busy(pi, player, t) : PB_WORLD;
    if (t_pup_busy_now != PB_FREE && (t_pup.state == PS_PENDING || t_pup.state == PS_LIVE)) t_pup_try_busy = 1;
    // ... and a pack given up by the game's doing that spawned before comes back on a new stage or a new Link (a
    // Continue after Game Over), with fresh tries
    if (t_pup.state == PS_FAILED && t_pup_soft_fail && t_pup_pick_spawned && player &&
        (spawn_heap() != t_pup_fail_heap || player != t_pup_fail_player)) {
        t_pup.tries = 0, t_pup_soft_fail = 0, t_pup.comebacks++;
        t_pup.state = PS_WAIT, t_pup_retry_at = t + PUP_RETRY_S;
        snprintf(t_pup.why, sizeof t_pup.why, "%s: trying again (a new stage)", t_pup.name);
        note("puppet: %s", t_pup.why);
    }
    int mirror = 0;
    switch (t_pup.state) {
    case PS_WAIT:
        if (player && t >= t_pup_retry_at && t_pup_busy_now != PB_FREE) {  // (0.9.5: not while the game is busy)
            t_pup_retry_at = t + 0.5;
            snprintf(t_pup.why, sizeof t_pup.why, "%s: waiting (%s)", t_pup.name,
                     PB_WHY[t_pup_busy_now >= PB_FREE && t_pup_busy_now <= PB_BODY ? t_pup_busy_now : PB_BODY]);
        } else if (player && t >= t_pup_retry_at) {
            pup_request(c, pi, player, t);
        }
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
            pup_try_failed("the game removed it", t, 0);
            break;
        }
        mirror = pup_live(c, player, &t_status.self, t);
        break;
    }
    // Who is drawn: the puppet while it mirrors Link and he is free (settled), else Link. The pause menu keeps it.
    // 0.9.2: "not free" is one of the game's own states (pup_busy_real), held PUP_BUSY_S first: a fast slide's frame of
    // doubt no longer shows Link for 0.4 s.
    if (t_pup.state == PS_LIVE && player) {
        int paused = g_pup_paused(), busy = t_pup_busy_now;
        // Dying starts the Game Over event 0.1-0.2 s into DeadRagdoll: keep the costume while it follows his ragdoll
        // (it showed Link for the whole death in the round-3 game check); 0.9.3: also while he plays a death clip, and
        // if his body goes (pup_busy_keep)
        busy = pup_busy_keep(busy, t_pup.rd, t_pup.link_anim);
        if (busy && !t_pup_busy_since) t_pup_busy_since = t;
        if (!busy) t_pup_busy_since = 0;
        t_pup.busy = busy && t - t_pup_busy_since >= PUP_BUSY_S ? busy : PB_FREE;
        if (t_pup.busy) {
            t_pup_free_since = 0;
            if (!*t_pup.why)
                snprintf(t_pup.why, sizeof t_pup.why, "%s: Link shows",
                         PB_WHY[t_pup.busy >= PB_FREE && t_pup.busy <= PB_BODY ? t_pup.busy : PB_BODY]);
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
    // 0.9.5: its copies never sheathed by the game (an NPC holder's slots kept "in hand"); Link's arrow hidden with him
    t_pup.holder = t_pup.state == PS_LIVE && t_pup.proc ? pup_holder_in_hand(t_pup.proc, t) : 0;
    pup_arrow_tick(c, player, t_pup.state == PS_LIVE && t_pup.shown, t);
    pup_sheath_tick(c, t_pup.proc, t_pup.state == PS_LIVE, t);  // 0.9.8: our copies' sheaths hidden
    t_pup.items = t_pup.state == PS_LIVE && t_items_ghost == t_pup.proc && t_pup.proc;
    lock();
    g_pup = t_pup;
    unlock();
}

// The held items' holder this tick and its request (ghost.c's items part): the ghost's, while a friend's ghost is
// wanted or up; else the puppet, holding copies of what Link has: in its hands what he holds (a drawn weapon, a raised
// shield, an aimed bow), 0.9.3: on its back what he carries there (each body's own back: pup_item_grip), each one kept
// through a short loss of his equipment read (pup_keep_step: no delete and make again, no flicker); else none.
static uint64_t pup_items_holder(const Requests *rq, Requests *out) {
    *out = *rq;
    if (t_status.wanted || t_status.state != ST_IDLE || t_pup.state != PS_LIVE || !t_pup.proc) {
        memset(t_pit, 0, sizeof t_pit);
        if (t_status.wanted || t_status.state != ST_IDLE) return t_status.state == ST_LIVE ? t_status.actor : 0;
        return 0;
    }
    const SelfState *s = &t_status.self;
    const char *names[3] = {s->weapon, s->shield, s->bow};
    out->items_on = 1, out->items_back = 1, out->items_mask = 0;
    for (int i = 0; i < 3; i++) {
        const char *n = s->equip_ok && valid_name(names[i]) && item_name_ok(i, names[i]) ? names[i] : "";
        pup_keep_step(&t_pit[i], n, s->equip_ok && *n ? s->back[i] : -1, t_pup_now);
        snprintf(out->item_names[i], sizeof out->item_names[i], "%s", t_pit[i].name);
        if (*t_pit[i].name && t_pit[i].back) out->items_mask |= 1 << i;
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
        else if (!strcmp(picked, "link")) snprintf(out, cap, "You see yourself as Link.");
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
    if (argc == 4 && !strcmp(argv[2], "limp")) {  // 0.9.2: how loosely the limbs swing in a ragdoll (0: his exactly)
        float v;
        if (!parse_float(argv[3], &v) || v < 0.0f || v > 1.0f) {
            api->reply_fail(r, "usage: ghost puppet limp <0 to 1, %.1f by default>", PUP_LIMP);
            return;
        }
        lock();
        g_pup_req.limp = v, g_pup_req.limp_set = 1;
        unlock();
        api->reply_num(r, "limp", v);
        return;
    }
    if (argc == 4 && !strcmp(argv[2], "ball") && (!strcmp(argv[3], "on") || !strcmp(argv[3], "off"))) {  // 0.9.2
        lock();
        g_pup_req.ball_off = argv[3][1] == 'f';
        unlock();
        api->reply_str(r, "ball", argv[3]);
        return;
    }
    if (argc == 4 && (!strcmp(argv[2], "clamp") || !strcmp(argv[2], "hands")) &&
        (!strcmp(argv[3], "on") || !strcmp(argv[3], "off"))) {  // 0.9.3
        lock();
        if (argv[2][0] == 'c') g_pup_req.clamp_off = argv[3][1] == 'f';
        else g_pup_req.hands_off = argv[3][1] == 'f';
        unlock();
        api->reply_str(r, argv[2], argv[3]);
        return;
    }
    if (argc >= 4 && !strcmp(argv[2], "grip")) {  // 0.9.3: grip <size> [x y z cm] | grip - (the body's own again)
        float v[4] = {1, 0, 0, 0};
        int ok = !strcmp(argv[3], "-") ? argc == 4 : (argc == 4 || argc == 7);
        for (int k = 0; ok && strcmp(argv[3], "-") && k < argc - 3; k++) ok = parse_float(argv[3 + k], &v[k]);
        if (!ok || !(v[0] >= 0.2f && v[0] <= 5.0f) || v[1] < -100 || v[1] > 100 || v[2] < -100 || v[2] > 100 ||
            v[3] < -100 || v[3] > 100) {
            api->reply_fail(r, "usage: ghost puppet grip <size 0.2 to 5> [x y z: cm, -100 to 100] | grip -");
            return;
        }
        lock();
        g_pup_req.grip_set = strcmp(argv[3], "-") != 0;
        g_pup_req.grip_scale = v[0];
        for (int k = 0; k < 3; k++) g_pup_req.grip_hand[k] = argc == 7 ? v[1 + k] * 0.01f : pup_grip(t_pup.id)->hand[k];
        unlock();
        api->reply_str(r, "grip", g_pup_req.grip_set ? "set" : "the body's own");
        return;
    }
    if ((argc == 4 && !strcmp(argv[2], "back") && !strcmp(argv[3], "-")) ||
        (argc == 6 && !strcmp(argv[2], "back"))) {  // 0.9.5: back <x y z cm> (Spine_2's frame) | back - (the body's own)
        float v[3] = {0, 0, 0};
        int ok = 1;
        for (int k = 0; argc == 6 && k < 3; k++) ok = ok && parse_float(argv[3 + k], &v[k]) && v[k] >= -100 && v[k] <= 100;
        if (!ok) {
            api->reply_fail(r, "usage: ghost puppet back <x up y forward z left: cm, -100 to 100> | back -");
            return;
        }
        lock();
        g_pup_req.back_set = argc == 6;
        for (int k = 0; k < 3; k++) g_pup_req.back[k] = v[k] * 0.01f;
        unlock();
        api->reply_str(r, "back", argc == 6 ? "set" : "the body's own");
        return;
    }
    if (argc == 4 && !strcmp(argv[2], "physmtx")) {  // 0.9.6: off (the default), warp, on
        int mode = -1;
        for (int i = 0; i <= PM_ON; i++)
            if (!strcmp(argv[3], PM_NAMES[i])) mode = i;
        if (mode < 0) {
            api->reply_fail(r, "usage: ghost puppet physmtx off | warp | on");
            return;
        }
        lock();
        g_pup_req.physmtx = mode;
        unlock();
        api->reply_str(r, "physmtx", PM_NAMES[mode]);
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
                           "limp x | ball on|off | clamp on|off | hands on|off | grip size [x y z] | grip - | "
                           "back x y z | back - | physmtx off|warp|on | map id=Actor,... | -]");
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
    // 0.9.2
    api->reply_str(r, "ragdoll_anim", p.rd_anim);
    api->reply_int(r, "ragdoll_after_writes", __atomic_load_n(&g_pup_ov_writes, __ATOMIC_RELAXED));
    api->reply_bool(r, "ragdoll_after_step", g_orig_unit_step != 0);
    api->reply_str(r, "ragdoll_rest", p.rd_rest == 3 ? "both" : p.rd_rest == 1 ? "link" : p.rd_rest == 2 ? "costume" : "");
    api->reply_num(r, "ragdoll_up", p.rd_up);
    api->reply_num(r, "ragdoll_want", p.rd_want);
    api->reply_num(r, "limp", q.limp_set ? q.limp : PUP_LIMP);
    api->reply_str(r, "busy", PB_NAMES[p.busy >= PB_FREE && p.busy <= PB_BODY ? p.busy : 0]);
    api->reply_int(r, "event", p.event);
    api->reply_num(r, "real_gap", p.real_gap);
    api->reply_num(r, "real_gap_max", p.real_gap_max);
    api->reply_int(r, "gap_frames", p.gap_frames);
    api->reply_str(r, "ball", q.ball_off ? "off" : p.ball == 1 ? "rolling" : p.ball == 2 ? "upright" : "no");
    api->reply_num(r, "ball_radius", p.ball_r);
    api->reply_num(r, "ball_spin", p.ball_spin);
    api->reply_num(r, "ball_angle", p.ball_angle);
    api->reply_int(r, "balls", p.balls);
    // 0.9.3
    api->reply_str(r, "clamp", q.clamp_off ? "off" : p.clamp_by);
    api->reply_num(r, "clamp_m", p.clamp);
    api->reply_num(r, "clamp_want_m", p.clamp_want);
    char hands[64];
    snprintf(hands, sizeof hands, "%s%s%s%s%s%s", q.hands_off ? "off" : "", p.hands & OVL_FIST_R ? "fist_r " : "",
             p.hands & OVL_FIST_L ? "fist_l " : "", p.hands & OVL_ARM_R ? "arm_r " : "",
             p.hands & OVL_ARM_L ? "arm_l " : "", p.hands & OVL_GUARD ? "guard" : "");
    api->reply_str(r, "hands", hands);
    api->reply_int(r, "hand_bones", p.hand_bones);
    api->reply_int(r, "hand_frames", p.hand_frames);
    api->reply_num(r, "grip_size", p.grip_scale);
    char grip[48];
    snprintf(grip, sizeof grip, "%.1f %.1f %.1f", p.grip_hand[0] * 100.0f, p.grip_hand[1] * 100.0f,
             p.grip_hand[2] * 100.0f);
    api->reply_str(r, "grip_cm", grip);
    // 0.9.5
    char back[48];
    snprintf(back, sizeof back, "%.1f %.1f %.1f", p.back[0] * 100.0f, p.back[1] * 100.0f, p.back[2] * 100.0f);
    api->reply_str(r, "back_cm", back);
    api->reply_bool(r, "holder", p.holder != 0);
    api->reply_int(r, "holder_writes", p.holder_writes);
    api->reply_str(r, "arrow", p.arrow);
    api->reply_bool(r, "arrow_hidden", p.arrow_hidden != 0);
    api->reply_int(r, "arrow_hides", p.arrow_hides);
    api->reply_str(r, "sheath", p.sheath);  // 0.9.8
    api->reply_int(r, "sheaths", p.sheaths);
    api->reply_int(r, "sheath_hides", p.sheath_hides);
    api->reply_int(r, "sheath_unit_hides", __atomic_load_n(&g_pup_pod_hides, __ATOMIC_RELAXED));  // 0.9.9
    api->reply_num(r, "sprint_since", p.sprint_since);
    api->reply_num(r, "stamina", p.stamina);
    api->reply_int(r, "busy_waits", p.busy_waits);
    api->reply_int(r, "comebacks", p.comebacks);
    // 0.9.6
    api->reply_int(r, "cloth_bones", p.cloth_bones);
    api->reply_bool(r, "physics", p.physics != 0);
    api->reply_str(r, "physmtx", PM_NAMES[q.physmtx >= PM_OFF && q.physmtx <= PM_ON ? q.physmtx : PM_OFF]);
    api->reply_int(r, "phys_moves", p.phys_moves);
    api->reply_int(r, "warps", p.warps);
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
