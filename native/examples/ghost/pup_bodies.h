// Made by python tools\charsel.py bodies from the game's own models (numbers only): each body's flesh around
// its main bones, the box its vertices fill in the bone's own frame at rest, cm (x-, x+, y-, y+, z-, z+).
// puppet.c turns them with the bones during a ragdoll to keep the costume's lowest point on the ground.
// A body with no entry (the Hylian: Mii-style parts) uses Link's, scaled to its spine.
#define PUP_NBODY_BONES 16
static const char *const PUP_BODY_BONES[PUP_NBODY_BONES] = {
    "Skl_Root", "Spine_1", "Spine_2", "Head", "Arm_1_L", "Arm_2_L", "Wrist_L", "Arm_1_R",
    "Arm_2_R", "Wrist_R", "Leg_1_L", "Leg_2_L", "Ankle_L", "Leg_1_R", "Leg_2_R", "Ankle_R",
};
typedef struct {
    const char *id;
    signed char has[PUP_NBODY_BONES];
    short box[PUP_NBODY_BONES][6];
} PupBody;
static const PupBody PUP_BODIES[] = {
    {"link", {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, {
        {-22, 20, -23, 0, -12, 17}, {0, 13, -5, 14, -13, 13}, {0, 36, -9, 13, -18, 18},
        {-4, 19, -5, 14, -12, 12}, {0, 27, -6, 5, -3, 7}, {0, 27, -6, 3, -4, 6},
        {0, 21, -5, 8, -2, 4}, {-27, 0, -5, 6, -7, 3}, {-27, 0, -4, 6, -6, 3},
        {-21, 0, -9, 5, -4, 2}, {0, 41, -9, 10, -8, 10}, {-1, 35, -9, 6, -7, 6},
        {-3, 25, -13, 1, -5, 5}, {-41, 0, -10, 9, -10, 8}, {-36, 1, -6, 9, -6, 7},
        {-25, 3, -2, 12, -5, 5},
    }},
    {"bokoblin", {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, {
        {-38, 39, -72, 2, -28, 42}, {-11, 34, -25, 38, -36, 32}, {-5, 70, -39, 58, -31, 39},
        {-47, 20, 0, 78, -78, 79}, {-5, 61, -24, 16, -14, 17}, {-2, 35, -20, 11, -12, 16},
        {-7, 56, -14, 21, -9, 14}, {-61, 5, -17, 24, -17, 13}, {-35, 0, -11, 18, -16, 11},
        {-56, 7, -20, 14, -14, 9}, {0, 43, -21, 14, -15, 16}, {-2, 35, -20, 7, -10, 12},
        {-8, 36, -17, 9, -16, 9}, {-51, 0, -16, 20, -17, 17}, {-35, 2, -7, 18, -12, 10},
        {-36, 8, -10, 17, -9, 16},
    }},
    {"gerudo", {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, {
        {-29, 30, -73, 5, -25, 19}, {0, 17, -6, 13, -14, 14}, {0, 34, -13, 18, -17, 17},
        {-64, 27, -47, 12, -15, 14}, {-2, 29, -12, 10, -19, 7}, {0, 35, -5, 5, -5, 5},
        {0, 22, -5, 9, -1, 4}, {-30, 2, -10, 11, -7, 19}, {-35, 0, -5, 5, -6, 5},
        {-22, 0, -9, 5, -4, 1}, {-3, 51, -14, 12, -17, 9}, {0, 51, -11, 6, -13, 6},
        {-2, 24, -19, 0, -5, 5}, {-54, 0, -12, 13, -9, 17}, {-51, 0, -6, 11, -6, 13},
        {-27, 2, -1, 19, -5, 5},
    }},
    {"goron", {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, {
        {-69, 69, -47, 0, -77, 61}, {-17, 41, -96, 96, -85, 94}, {-37, 79, -97, 94, -98, 98},
        {-38, 39, -30, 72, -50, 50}, {0, 35, -34, 24, -25, 25}, {0, 74, -39, 37, -44, 29},
        {0, 113, -37, 52, -27, 28}, {-37, 0, -25, 34, -26, 25}, {-74, 0, -37, 39, -29, 44},
        {-114, 0, -52, 36, -28, 27}, {0, 34, -23, 28, -24, 25}, {-4, 23, -13, 17, -13, 15},
        {-6, 35, -15, 8, -14, 14}, {-34, 0, -34, 19, -25, 22}, {-23, 4, -18, 13, -15, 13},
        {-35, 5, -8, 15, -15, 14},
    }},
    {"moblin", {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, {
        {-59, 59, -136, 2, -57, 46}, {0, 91, -33, 38, -51, 49}, {-9, 160, -90, 78, -71, 70},
        {0, 181, -39, 86, -66, 60}, {0, 133, -38, 38, -41, 26}, {0, 150, -39, 23, -25, 24},
        {-2, 106, -23, 45, -13, 25}, {-145, 0, -40, 37, -27, 38}, {-153, 0, -23, 36, -24, 24},
        {-107, 1, -46, 23, -26, 13}, {0, 77, -36, 28, -31, 28}, {-1, 87, -34, 19, -20, 16},
        {-15, 69, -39, 32, -32, 15}, {-78, 0, -28, 36, -30, 27}, {-87, 1, -20, 34, -19, 17},
        {-70, 16, -32, 38, -17, 32},
    }},
    {"rito", {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, {
        {-29, 29, -69, 8, -67, 24}, {0, 28, -3, 25, -15, 15}, {-20, 36, -17, 30, -38, 38},
        {-8, 44, -62, 23, -20, 20}, {0, 25, -14, 5, -4, 6}, {-1, 36, -33, 7, -7, 6},
        {0, 52, -34, 10, -7, 10}, {-25, 0, -5, 14, -7, 4}, {-36, 1, -8, 33, -7, 7},
        {-52, 0, -11, 34, -10, 7}, {0, 31, -9, 26, -15, 13}, {-4, 27, -11, 6, -7, 7},
        {-13, 24, -15, 6, -10, 9}, {-31, 0, -27, 8, -13, 15}, {-28, 3, -6, 11, -7, 7},
        {-24, 13, -6, 15, -9, 10},
    }},
    {"sheikah", {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, {
        {-19, 19, -30, 3, -15, 18}, {-1, 20, -13, 17, -14, 14}, {-3, 30, -14, 13, -18, 18},
        {-24, 33, -24, 13, -23, 22}, {0, 29, -13, 11, -17, 10}, {-1, 29, -8, 6, -10, 6},
        {0, 21, -6, 9, -3, 4}, {-29, 0, -11, 13, -12, 17}, {-29, 1, -6, 8, -6, 10},
        {-21, 0, -9, 6, -4, 3}, {0, 48, -17, 17, -12, 12}, {0, 31, -8, 8, -8, 8},
        {-2, 24, -13, 1, -5, 6}, {-50, 0, -17, 15, -12, 12}, {-32, 0, -8, 8, -8, 8},
        {-24, 2, -1, 13, -6, 5},
    }},
    {"zelda", {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 0, 1, 1}, {
        {-21, 21, -84, 0, -14, 17}, {-1, 13, -9, 18, -18, 18}, {0, 41, -9, 15, -15, 15},
        {-60, 22, -33, 11, -22, 22}, {0, 27, -5, 5, -4, 6}, {0, 27, -5, 4, -4, 5},
        {0, 21, -5, 8, -2, 3}, {-28, 0, -5, 5, -8, 4}, {-27, 0, -4, 5, -5, 3},
        {-21, 0, -8, 5, -3, 2}, {0, 0, 0, 0, 0, 0}, {0, 43, -10, 3, -7, 6},
        {-4, 20, -12, 2, -5, 5}, {0, 0, 0, 0, 0, 0}, {-44, 0, -3, 9, -6, 6},
        {-20, 4, -2, 12, -5, 5},
    }},
    {"zora", {1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1}, {
        {-18, 20, -26, 0, -11, 19}, {0, 17, -1, 21, -13, 13}, {-20, 35, -13, 17, -14, 18},
        {-34, 21, -32, 15, -14, 14}, {0, 23, -5, 5, -6, 6}, {0, 38, -24, 4, -2, 3},
        {0, 18, -3, 8, -1, 3}, {-25, 0, -5, 5, -6, 5}, {-38, 0, -4, 23, -3, 2},
        {-18, 0, -8, 3, -3, 1}, {0, 30, -4, 11, -9, 6}, {0, 23, -7, 2, -4, 4},
        {-3, 18, -8, 3, -6, 4}, {-31, 0, -14, 4, -6, 8}, {-23, 0, -2, 7, -4, 4},
        {-18, 3, -3, 7, -4, 6},
    }},
};

// Hand and weapon bones (charsel.py bodies), for the held items: per body, each bone's place (cm) and turn
// (degrees, Euler XYZ) at rest in its parent's frame (has 0: no such bone), how many finger bones each
// hand has (left, right), and the hips' (Skl_Root's) height at rest (m; a Link clip played without the
// retarget puts them at Link's). Define PUP_WANT_HANDS before including this file to get the table.
#ifdef PUP_WANT_HANDS
#define PUP_NHAND_BONES 8
static const char *const PUP_HAND_BONES[PUP_NHAND_BONES] = {
    "Wrist_L", "Wrist_R", "Weapon_L", "Weapon_R", "Pod_A", "Pod_B", "Pod_C", "Weapon_Root",
};
typedef struct {
    const char *id;
    signed char has[PUP_NHAND_BONES];
    float place[PUP_NHAND_BONES][3], turn[PUP_NHAND_BONES][3];
    signed char fingers[2];
    float hip;
} PupHands;
static const PupHands PUP_HANDS[] = {
    {"link", {1, 1, 1, 1, 1, 1, 1, 1},
     {{27.7f, 0.0f, 0.0f}, {-27.7f, 0.0f, 0.0f}, {10.7f, 0.0f, 2.8f}, {-10.7f, -0.0f, -2.8f}, {18.9f, -12.2f, -7.7f}, {0.2f, -0.8f, 0.1f}, {2.8f, 8.0f, -15.5f}, {0.0f, 0.0f, 0.0f}},
     {{0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {90.0f, 0.0f, 180.0f}, {90.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {-60.0f, 18.0f, 5.0f}, {0.0f, 0.0f, 0.0f}},
     {15, 15}, 0.994f},
    {"bokoblin", {1, 1, 1, 1, 0, 0, 0, 0},
     {{56.2f, 0.0f, 0.0f}, {-56.2f, 0.0f, 0.0f}, {29.0f, 6.0f, 3.6f}, {-29.0f, -6.1f, -3.6f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {{0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {90.0f, 0.0f, 180.0f}, {90.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {6, 6}, 1.203f},
    {"gerudo", {1, 1, 1, 1, 1, 1, 0, 0},
     {{35.1f, 0.0f, 0.0f}, {-35.1f, 0.0f, 0.0f}, {12.8f, 0.0f, 3.3f}, {-11.5f, -0.0f, -3.0f}, {-28.3f, -20.0f, 0.0f}, {3.0f, -17.6f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {{0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {90.0f, 0.0f, 180.0f}, {90.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {15, 15}, 1.343f},
    {"goron", {1, 1, 1, 1, 1, 1, 0, 0},
     {{71.0f, 0.0f, 0.0f}, {-71.0f, 0.0f, 0.0f}, {32.4f, 0.0f, 14.8f}, {-32.4f, 0.0f, -14.8f}, {70.0f, -57.0f, -62.0f}, {18.2f, -12.3f, 0.1f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {{0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {90.0f, 0.0f, 180.0f}, {90.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {15, 15}, 1.065f},
    {"moblin", {1, 1, 1, 1, 0, 0, 0, 0},
     {{155.0f, 0.0f, 0.0f}, {-155.0f, 0.0f, 0.0f}, {55.6f, 9.1f, 5.4f}, {-55.6f, -9.1f, -5.4f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {{0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {90.0f, 0.0f, 180.0f}, {90.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {6, 6}, 2.578f},
    {"rito", {1, 1, 1, 1, 1, 1, 0, 0},
     {{36.0f, 0.0f, 0.0f}, {-36.0f, 0.0f, 0.0f}, {22.5f, -9.7f, 4.7f}, {-22.5f, 9.8f, -4.7f}, {15.2f, -17.1f, 0.0f}, {0.0f, -8.3f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {{0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {90.0f, 0.0f, 180.0f}, {90.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {15, 15}, 0.846f},
    {"sheikah", {1, 1, 1, 1, 1, 1, 0, 0},
     {{29.4f, 0.0f, 0.0f}, {-29.4f, 0.0f, 0.0f}, {10.2f, 0.0f, 2.8f}, {-10.2f, -0.0f, -2.8f}, {7.9f, -11.8f, 0.0f}, {4.4f, -10.5f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {{0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {90.0f, 0.0f, 180.0f}, {90.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {15, 15}, 1.094f},
    {"zelda", {1, 1, 1, 1, 0, 0, 0, 0},
     {{26.0f, 0.0f, 0.0f}, {-26.0f, 0.0f, 0.0f}, {10.0f, 0.0f, 1.1f}, {-10.0f, -0.0f, -1.1f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {{0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {90.0f, 0.0f, 180.0f}, {90.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {15, 15}, 1.071f},
    {"zora", {1, 1, 1, 1, 1, 1, 0, 0},
     {{25.0f, 0.0f, 0.0f}, {-25.0f, 0.0f, 0.0f}, {11.9f, 0.0f, 3.1f}, {-11.9f, -0.0f, -3.1f}, {11.2f, -5.9f, -6.3f}, {3.2f, -2.7f, 0.1f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {{0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {90.0f, 0.0f, 180.0f}, {90.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f}},
     {15, 15}, 0.751f},
};
#endif

// Bones each body's own cloth and support-bone physics move (charsel.py bodies: the names in its pack's
// Physics files that are bones of its model, less the ones named as Link's: the anchors its pose sets).
// puppet.c leaves them to the physics: the ragdoll pose and the hands never write them (no fight, no
// jitter). Names only.
typedef struct {
    const char *id;
    const char *bones;  // each name followed by one space
} PupCloth;
static const PupCloth PUP_CLOTH[] = {
    {"bokoblin",
     "Apron_B_1 Apron_B_2 Apron_F_1 Apron_F_2 Ear_1_L Ear_1_R Ear_2_L Ear_2_R Earlobe_In_1_L "
     "Earlobe_In_1_R Earlobe_In_2_L Earlobe_In_2_R Earlobe_Out_1_L Earlobe_Out_1_R Earlobe_Out_2_L "
     "Earlobe_Out_2_R Necklace_1 Necklace_2 Necklace_3 "},
    {"gerudo",
     "Accs_D_1 Armparts_L Armparts_R Earring_A_1 Earring_B_1 Hair_A_1 Hair_A_2 Hair_B_1 Hair_B_10 "
     "Hair_B_11 Hair_B_12 Hair_B_13 Hair_B_14 Hair_B_15 Hair_B_16 Hair_B_17 Hair_B_18 Hair_B_19 "
     "Hair_B_2 Hair_B_20 Hair_B_21 Hair_B_22 Hair_B_23 Hair_B_24 Hair_B_3 Hair_B_4 Hair_B_5 Hair_B_6 "
     "Hair_B_7 Hair_B_8 Hair_B_9 Leg_Assist_L Leg_Assist_R Skirt_A_1 Skirt_A_2 Skirt_A_3 Skirt_B_1 "
     "Skirt_B_2 Skirt_B_3 Skirt_C_1 Skirt_C_2 Skirt_C_3 Skirt_C_4 Skirt_D_1 Skirt_D_2 Skirt_D_3 "
     "Skirt_D_4 Skirt_D_5 Skirt_E_1 Skirt_E_2 Skirt_E_3 Skirt_E_4 Skirt_E_5 Skirt_F_1 Skirt_F_2 "
     "Skirt_F_3 Skirt_F_4 Skirt_G_1 Skirt_G_2 Skirt_G_3 Skirt_G_4 Skirt_H_1 Skirt_H_2 Skirt_H_3 "},
    {"goron",
     "ArmFur_1_L ArmFur_1_R ArmFur_2_L ArmFur_2_R BacKhair_1 BacKhair_2 BacKhair_3 ChestHair_Help_1 "
     "ChestHair_Help_2 Cloths_A_1 Cloths_A_2 Cloths_B_1 Cloths_B_2 Cloths_C_1 Cloths_C_2 Cloths_D_1 "
     "Cloths_D_2 Cloths_E_1 Cloths_E_2 FrontBeard_1 FrontBeard_2 Mustache_L_1 Mustache_L_2 "
     "Mustache_L_3 Mustache_R_1 Mustache_R_2 Mustache_R_3 Seta_1_L Seta_1_R Seta_2_L Seta_2_R "
     "SideBeard_1_L SideBeard_1_R SideBeard_2_L SideBeard_2_R Sidehair_A_1_L Sidehair_A_1_R "
     "Sidehair_A_2_L Sidehair_A_2_R Sidehair_A_3_L Sidehair_A_3_R Sidehair_B_1_L Sidehair_B_1_R "
     "Sidehair_B_2_L Sidehair_B_2_R Sidehair_B_3_L Sidehair_B_3_R Tophair_1 Tophair_2 Tophair_3 "},
    {"moblin",
     "Apron_1_B Apron_1_F Apron_2_B Apron_2_F Apron_3_B Apron_3_F Apron_4_F Cloth_1_B Cloth_1_F "
     "Cloth_2_B Cloth_2_F Cloth_3_B Cloth_3_F Cloth_4_F Cloth_L Cloth_R "},
    {"rito",
     "BraidA_1_L BraidA_1_R BraidA_2_L BraidA_2_R BraidA_3_L BraidA_3_R BraidA_4_L BraidA_4_R "
     "BraidA_5_L BraidA_5_R BraidB_1_L BraidB_1_R BraidB_2_L BraidB_2_R BraidB_3_L BraidB_3_R "
     "BraidB_4_L BraidB_4_R BraidB_5_L BraidB_5_R ScarfA_1 ScarfA_2 ScarfA_3 ScarfA_4 ScarfA_5 "
     "ScarfA_6 ScarfB_1 ScarfB_2 ScarfB_3 ShoulderPlume_1_L ShoulderPlume_1_R "},
    {"sheikah",
     "Hair_C_1 Hair_C_2 Hair_C_3 Hair_L_1 Hair_L_2 Hair_L_3 Hair_R_1 Hair_R_2 Hair_R_3 Hair_S_L_1 "
     "Hair_S_L_2 Hair_S_L_3 Hair_S_R_1 Hair_S_R_2 Hair_S_R_3 "},
    {"zelda",
     "Hair_1_C Hair_1_C_L Hair_1_C_R Hair_1_Cl_L Hair_1_Cr_R Hair_1_L Hair_1_R Hair_2_C Hair_2_C_L "
     "Hair_2_C_R Hair_2_L Hair_2_R Hair_3_C Hair_3_C_L Hair_3_C_R Hair_3_L Hair_3_R Hair_4_C "
     "Hair_4_C_L Hair_4_C_R Hair_5_C Hair_Help_1 Necklace_C_1 Necklace_C_2 Necklace_L_1 Necklace_L_2 "
     "Necklace_R_1 Necklace_R_2 Necklace_Root Skirt_1_C_B Skirt_1_C_B_L Skirt_1_C_B_R Skirt_1_C_F "
     "Skirt_1_C_F_L Skirt_1_C_F_R Skirt_1_L Skirt_1_R Skirt_2_C_B Skirt_2_C_B_L Skirt_2_C_B_R "
     "Skirt_2_C_F Skirt_2_C_F_L Skirt_2_C_F_R Skirt_2_L Skirt_2_R Skirt_3_C_B Skirt_3_C_B_L "
     "Skirt_3_C_B_R Skirt_3_C_F Skirt_3_C_F_L Skirt_3_C_F_R Skirt_3_L Skirt_3_R Skirt_4_C_B "
     "Skirt_4_C_B_L Skirt_4_C_B_R Skirt_4_C_F Skirt_4_C_F_L Skirt_4_C_F_R Skirt_4_L Skirt_4_R "
     "Skirt_5_C_B Skirt_5_C_B_L Skirt_5_C_B_R Skirt_5_C_F Skirt_5_C_F_L Skirt_5_C_F_R Skirt_5_L "
     "Skirt_5_R Skirt_6_C_F Skirt_6_C_F_L Skirt_6_C_F_R "},
    {"zora",
     "Arm_Fin_1_L Arm_Fin_1_R Arm_Fin_2_L Arm_Fin_2_R Arm_Fin_3_L Arm_Fin_3_R Arm_Fin_4_L Arm_Fin_4_R "
     "Arm_Fin_5_L Arm_Fin_5_R Cloth_A_1 Cloth_A_2 Cloth_A_3 Cloth_A_4 Cloth_B_1 Cloth_B_2 Cloth_B_3 "
     "Cloth_C_1 Cloth_C_2 Ear_L_1 Ear_L_2 Ear_L_3 Ear_R_1 Ear_R_2 Ear_R_3 Head_Fin_1 Head_Fin_2 "
     "Head_Fin_3 Head_Fin_4 Head_Fin_5 Jewel_A_1 Jewel_A_2 Jewel_A_3 Jewel_A_4 Jewel_B_1 Jewel_B_2 "
     "Jewel_B_3 Jewel_B_4 Jewel_B_5 Jewel_B_6 Jewel_B_7 Leg_Fin_C Leg_Fin_L Leg_Fin_R Leg_Fin_U_C "
     "Leg_Fin_U_L Leg_Fin_U_R Parts_B_1 Parts_B_2 Parts_B_3 Spine_ClothA_1_C Spine_ClothA_1_L "
     "Spine_ClothA_1_R Spine_ClothA_2_C Spine_ClothA_2_L Spine_ClothA_2_R "},
    {0, 0},
};
