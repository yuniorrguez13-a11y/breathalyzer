#!/usr/bin/env python3
"""Who am I: pick who your friends see you as, from a wheel (docs/charsel.md). Each PC builds the bodies itself, from
its own game files; only the character's id goes over the network.

    python tools/charsel.py list                      the characters on the wheel, by group, and how each looks
    python tools/charsel.py build [ID ...]            build the character packs into out\\charsel-packs (a RomFS mod
                                                      folder: mod.toml, romfs\\, charsel.json) from your own game files
        --romfs DIR      the game's extracted romfs (default: Documents\\SavageWind\\Savage Wind\\extracted\\romfs)
        --out DIR        where the mod folder goes (default out\\charsel-packs; never inside the game's files)
        --library DIR    your mod library (default: next to Savage Wind): finds the Linkle mod there (read only)
        --slot ID=ACTOR  put a character on another free actor name (if the in-game check says one doesn't load)
        --model-copies   also Link side by side (a renamed model copy; not yet tried in the game)
        --monster-link-clips   monsters climb, glide and swim with Link's clips (not yet tried in the game)
        --cloth off      no cloth physics (skirts, hair and capes stiff, as before 2026-11-14)
    python tools/charsel.py check [--out DIR]         read every built pack back, no game: sorted animation lists, one
                                                      clip each, sizes, the actor link
    python tools/charsel.py wheel                     the Who am I wheel in a window (arrow keys or a controller)
    python tools/charsel.py pick ID                   the same choice without a window
    python tools/charsel.py where                     your choice and where it is kept
    python tools/charsel.py playas daruk              play as Daruk yourself from the next start: builds a mod from your
                                                      own files (the body on Link's skeleton, his rolling sprint) and
                                                      adds it to your mods; turns The Linkle Mod off while it is on
        --no-own-moves   Link's moves only        --retarget   fit Link's clips to the body [G]
        --no-add         build and check only     --library DIR / --out DIR / --romfs DIR
    python tools/charsel.py playas off                back to Link (and Linkle back on) from the next start
    python tools/charsel.py playas linkle             be Linkle yourself from the next start: The Linkle Mod on as
                                                      Link's own body (in your library; any play-as mod out)
    python tools/charsel.py playas link               plain Link from the next start (The Linkle Mod off too)
    python tools/charsel.py playas check              read the built play-as mod back, no game

A character pack is a small actor pack (14-19 KB unpacked without cloth, 45-140 KB with) that names the game's own
model and animation files: the donor's model list, an animation list with one small animation set per ghost name
(the 33 names of the Link body, tools/mp/ghost_link_pack.py), no AI (the cutscene dummy's), and cloth-only physics:
the character's own skirt, hair, scarf or fur parts (its game actor's cloth file, copied from your game into the pack;
nothing that blocks, pushes or can be hit). People's models use Link's bone names, so Link's clips fill every move
they don't have (through the game's own retarget); monsters use their own clips.
Each pack goes on a free actor name the game knows but never uses (a cutscene name with no pack), so the ghost mod
spawns it with `ghost spawn <name>`. Nothing from the game is in this repo: recipes are names only. Keep the output
in out\\ (never committed) or your own mod library, and never share it: your friend builds their own. The build
leaves built.txt next to mod.toml, so Export as .breath (tools/breath.py) refuses to share it.

What this PC shows for a friend's pick comes only from the characters its game really gets: the library copy of
this mod (modman add out\\charsel-packs), enabled, with its pack in the deployed overlay (here_catalog). Anything
else is the Link body.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

VERSION = "0.1.0"
MOD_ID = "botw-mod-loader.characters"
DEFAULT_OUT = REPO / "out" / "charsel-packs"
DEFAULT_CACHE = REPO / "out" / "charsel-cache.json"
DEFAULT_ROMFS = Path.home() / "Documents" / "SavageWind" / "Savage Wind" / "extracted" / "romfs"
DEFAULT_LIBRARY = Path.home() / "Documents" / "SavageWind" / "modloader"
CATALOG = "charsel.json"          # in the built mod folder: which characters are built, on which actor name
STATE = "charsel.json"            # in the launcher's folder (%LOCALAPPDATA%\Breathalyzer): your choice
ENV_HOME = "BREATHALYZER_HOME"    # (the launcher's: tests point it elsewhere)
LINK_ACTOR = "Dm_Npc_Sheiker_Searcher_M_001"   # the Link body (ghost_link_pack.LINK_ACTOR): charsel never writes it
TEMPLATE = LINK_ACTOR             # its actor link and cutscene-dummy AI program come from the game's own pack
PLAYER_ANIM = "Player_Animation"  # Link's animation file (always loaded, in TitleBG)
PACK_LIMIT = 64 * 1024            # bytes unpacked: ours are 14-19 KB (the Link body's size-table entry is 348,448)
BUILT_MARK = "built.txt"          # next to mod.toml: made from the game's files (tools/breath.py BUILT_MARKS: never
#                                   exported as a .breath)
BUILT_TEXT = ("Built on this PC from its own game files (tools/charsel.py). Never share it: each player builds their "
              "own.\n")
MAX_ID = 24
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,23}")

# ------------------------------------------------------------------------------------------------ the characters

# The character's own clips for some of the 33 ghost names (the Link body's; ghost_link_pack.ANIMS). Clip names were
# checked against the user's own files by out/research/charsel_pack_proto.py (24 recipes: every clip found, no clash).
UMII_M = (("Wait", "Default_Wait"), ("Walk", "Default_Move_Walk"), ("Run", "Common_Move_Run"),
          ("RunGear2", "Common_Move_Run_Gear2"), ("SquatWait", "Common_Crouch"), ("DamageS", "Common_Stagger_F_L"))
UMII_W = tuple((n, "Common_Move_Escape" if n == "RunGear2" else c) for n, c in UMII_M)
# The monsters (v2, 2026-11-14): each of Link's swings gets the monster's swing for that weapon (one-hander, two-hander
# `_Lsword`, spear `_Spear`: the self puppet asks for the held weapon's name, puppet.c pup_weapon_pick), and BOKO_CTRL /
# MOBLIN_CTRL time it so the monster's hit (the middle of its clip's hit window) lands on the middle of Link's: hit windows
# and lengths read from the game's animation sets (Link's Player_Cut*, Bokoblin.baslist, Moriblin_Blue.baslist) [V
# frames], start frame and rate computed from them (out/research/charsel-anims-physics.md section 2) [G: the look].
# In water (no swim clip: the Moblin has only Drown [V]) they wade: their walk, slowed, their head at Link's (puppet.c).
BOKO = (("Wait", "Wait"), ("Walk", "Walk"), ("Run", "Run"), ("RunGear2", "Run"), ("Jump", "Jump_Start"),
        ("Fall", "Jump_Loop"), ("Land", "Jump_End"), ("SquatWait", "Wait_Squat_Loop"), ("CutS1", "Attack_Small"),
        ("CutS2", "Attack_Small_Two"), ("CutS3", "Attack_Small"), ("CutSF", "Attack_Run_Sword"),
        ("CutDash", "Attack_Run_Sword"), ("CutJumpSt", "Attack_Big_Quick"), ("CutCharge", "Attack_Ratation_End"),
        ("CutS1_Lsword", "Attack_Side_Lsword"), ("CutSF_Lsword", "Attack_Side_Lsword"),
        ("CutDash_Lsword", "Attack_Side_Lsword"), ("CutS1_Spear", "Attack_Small_Spear"),
        ("CutS2_Spear", "Attack_Small_Spear"), ("CutS3_Spear", "Attack_Small_Spear"), ("CutSF_Spear", "Attack_Small_Spear"),
        ("CutDash_Spear", "Attack_Small_Spear"), ("BowCharge", "Attack_Bow_Start"), ("BowShoot", "Attack_Bow_End"),
        ("GuardJust", "Guard_Just"), ("DamageS", "Damage_Small"), ("Dead", "Down_Wait"), ("WeaponEquipOn", "Weapon_Catch"),
        ("SwimWait", "Wait"), ("SwimMove", "Walk"), ("SwimDash", "Walk"))
# (ghost name, rate, start frame, end frame; None: the clip's own)
BOKO_CTRL = (("CutS1", 1.21, 23, None), ("CutS2", 1.28, 39, 65), ("CutS3", 1.11, 23, None), ("CutSF", 0.8, 6, None),
             ("CutDash", 0.8, 10, None), ("CutCharge", 1.19, 0, 40), ("CutS1_Lsword", 1.6, 34, None),
             ("CutSF_Lsword", 1.6, 27, None), ("CutDash_Lsword", 1.6, 37, None), ("CutS1_Spear", 0.8, 27, None),
             ("CutS2_Spear", 0.8, 27, None), ("CutS3_Spear", 0.8, 27, None), ("CutSF_Spear", 0.8, 22, None),
             ("CutDash_Spear", 0.8, 22, None), ("SwimMove", 0.6, None, None), ("SwimDash", 0.8, None, None))
MOBLIN = (("Wait", "Wait"), ("Walk", "Walk"), ("Run", "Run"), ("RunGear2", "Run"), ("Jump", "Jump_Start"),
          ("Fall", "Jump_Loop"), ("Land", "Jump_End"), ("SquatWait", "Wait_Squat_Loop"), ("CutS1", "Attack_Walk_Stay"),
          ("CutS2", "Attack_Walk_Stay"), ("CutS3", "Attack_Walk"), ("CutSF", "Attack_Side"),
          ("CutDash", "Attack_Walk"), ("CutJumpSt", "Attack_One_Lsword_Start"), ("CutCharge", "Attack_Side"),
          ("CutS1_Lsword", "Attack_Two_Lsword"), ("CutSF_Lsword", "Attack_One_Lsword_Start"),
          ("CutDash_Lsword", "Attack_Side"), ("CutS1_Spear", "Attack_Small"), ("CutS2_Spear", "Attack_Small"),
          ("CutS3_Spear", "Attack_Small"), ("CutSF_Spear", "Attack_Center"), ("CutDash_Spear", "Attack_Step"),
          ("BowCharge", "Attack_Bow_Start"), ("BowShoot", "Attack_Bow_End"), ("GuardJust", "Guard_Just_Shield"),
          ("DamageS", "Damage_Sword"), ("Dead", "Down_Wait"), ("WeaponEquipOn", "Weapon_Catch_R"),
          ("SwimWait", "Wait"), ("SwimMove", "Walk"), ("SwimDash", "Walk"))
MOBLIN_CTRL = (("CutS1", 0.8, 17, 30), ("CutS2", 1.25, 37, 62), ("CutS3", 0.8, 24, 40), ("CutSF", 1.6, 24, None),
               ("CutDash", 0.8, 22, 40), ("CutJumpSt", 0.8, 52, None), ("CutCharge", 1.6, 30, None),
               ("CutS1_Lsword", 0.8, 41, 80), ("CutSF_Lsword", 0.8, 52, None), ("CutDash_Lsword", 1.6, 30, None),
               ("CutS1_Spear", 1.4, 22, None), ("CutS2_Spear", 1.4, 22, None), ("CutS3_Spear", 1.4, 22, None),
               ("CutSF_Spear", 0.8, 24, None), ("CutDash_Spear", 0.91, 22, None), ("SwimMove", 0.6, None, None),
               ("SwimDash", 0.8, None, None))
# 0.9.5 (the round-4 game check: the Bokoblin's CutS1 came 0.59-0.89 s after the press, Link's 0.43-0.73 s; at Link's
# hit it was still "winding up"): the rule above assumes the costume starts its set with Link's, but it starts a frame or
# two later [V: puppet.c reads his set each frame], and it blended into the swing over the animation list's 6 frames
# while Link's own attack nodes have Morph 0 [V: Player_CutS1.bas]. So every attack set now starts at once (Morph 0 on
# its node, and puppet.c asks with no blend), and the monsters' swings start SWING_LEAD_S further into their clip, the
# time the costume still starts after Link [G: 1-2 frames]. Applied to every row of both tables (frame_ctrl).
SWING_LEAD_S = 0.05
CLIP_FPS = 30.0
ATTACK_MORPH = 0.0        # an attack set's node Morph, as Link's own attack nodes have it


def is_attack(name: str) -> bool:
    return name.startswith("Cut")


# Names only the costume packs have (not the Link body's, ghost_link_pack.ANIMS): the monsters' swing for a spear's
# third hit and the two-hander's and spear's dash attack (puppet.c PUP_SWINGS_OWN).
EXTRA_NAMES = ("CutS3_Spear", "CutDash_Lsword", "CutDash_Spear")
FRAME_CTRL = 0x43885a40   # a clip node's Extend > FrameCtrl object (Rate, StartFrame, EndFrame), as the game's sets [V]
# People's sprint where their family file has no sprint (UMii_Common_Body_W: only Common_Move_Escape, a panic run [V]):
# their own run, played faster.
SPRINT_RATE = 1.3


def _with(base: tuple, **kw) -> tuple:
    d = dict(base)
    d.update(kw)
    return tuple(d.items())


@dataclass(frozen=True)
class Recipe:
    """One character. Names only (the game's own file and clip names), like ghost_link_pack.ANIMS: no game data."""
    id: str
    name: str                 # on the wheel (up to 20 characters)
    cat: str                  # its group on the wheel
    kind: str                 # "pack": our pack from a donor; "link": the Link body; "linkle": a player-model mod
    donor: str = ""           # the actor whose model list (and Mii-style look file) we use
    anims: tuple = ()         # animation files, in order (the first with a clip is used [G])
    own: tuple = ()           # (ghost name, clip): the character's own clips
    rest: str = "link"        # every other ghost name: "link" (Link's clip through the retarget) or "speed" (none)
    load_mb: float = 0.0      # models, textures and animation files the game loads to show it (study 1, [V])
    aliases: tuple = ()       # other words for `iam` and `charsel pick`
    looks: str = ""           # one honest line: what looks right and what doesn't
    v: int = 1                # bump when the recipe changes: a build keeps an older pack only while its v matches
    #                           (it also travels in the look message, for the log; receivers don't compare it)
    ctrl: tuple = ()          # (ghost name, rate, start frame, end frame): how its set plays the clip (None: as is)
    cloth: tuple = ()         # the donor's cloth parts (names in its physics file), most important first: the pack
    #                           gets them as cloth-only physics (cloth_physics), the last ones left out when the heap
    #                           estimate is short (fit_cloth). (): no physics
    legs: bool = False        # Zelda: her model copied with a Hylian woman's legs under the dress (zelda_legs)


# Cloth and hair physics per character (2026-11-14, the user: "if there's no skirt or hair physics you can't do the
# Shorts"): the stock actor's own cloth parts [V: its Actor/Physics file], most important first (a skirt, a sash, a
# scarf, long hair before an earring or a fin). out/research/charsel-anims-physics.md section 3.
CLOTH_URBOSA = ("Skart_Havok", "Hair_B_Havok", "Hair_A_Havok", "Accs_D_Havok", "Earring_Havok")
CLOTH_ZELDA = ("Skirt_Havok", "Hair_C_Havok", "Necklace_Havok")
CLOTH_MIPHA = ("Cloth_A_Havok", "Cloth_B_Havok", "Cloth_C_Havok", "Head_Fin_Havok", "Ear_Havok", "Leg_Fin_Havok",
               "Arm_Fin_R_Havok", "Parts_B_Havok", "Parts_D_Havok")
CLOTH_REVALI = ("ScarfA_Havok", "ScarfB_Havok", "Braid_Havok", "ShoulderPlume_Havok")
CLOTH_DARUK = ("Tophair_Havok", "Beard_Havok", "Mustache_Havok", "Cloths_Havok", "ArmFur_Havok", "Seta_Havok")
CLOTH_PAYA = ("Hair_02_Havok", "Hair_Side_Havok")
CLOTH_BOKO = ("Apron_Havok", "Ear_L_Havok", "Ear_R_Havok", "Necklace_Havok")
CLOTH_MOBLIN = ("Cloth_A_Havok", "Cloth_B_Havok", "Cloth_C_Havok")


RECIPES: dict[str, Recipe] = {r.id: r for r in (
    Recipe("link", "Link", "Hylians", "link", aliases=("hero",),
           looks="Works in the game (the Link body). Bald and in underwear: hair and clothes are armor actors."),
    # Linkle (2026-11-18, stage 1 of out/research/linkle-body-swap-plan.md): no self-puppet any more. Picking her turns
    # The Linkle Mod on as Link's OWN body (linkle_body: it replaces Link's player and armor models), with one restart,
    # so every armor piece shows as hers and the inventory shows her. Friends still see her as her costume (built
    # from The Linkle Mod's files on THEIR PC, on or off), or as their Link body when they have the mod on.
    Recipe("linkle", "Linkle", "Hylians", "linkle", aliases=("linkel",), v=4,
           looks="Your own body becomes Linkle (The Linkle Mod, one restart): your armor shows as hers and the "
                 "inventory shows her. Needs The Linkle Mod in your mod library."),
    # Own moves (2026-11-14): the stock actors' own sets pick their stand and walk by the look file's personality
    # (Woman_Active -> Positive_*, Woman_Deflated -> Negative_*, else Default_*); Zelda, Mipha and Paya are
    # Woman_Normal, Urbosa Woman_Active on the men's sets (Man_Active / Man_Deflated / default), so all four play
    # Default_Wait and Default_Move_Walk, found first in the women's file [V: their packs]. The game has no
    # champion-only walk or run (memories are videos) [V]. Sprint: their run, faster (the women's file has only a panic
    # run, which v3 used).
    Recipe("zelda", "Zelda", "Hylians", "pack", "Dm_Npc_Zelda_Sibyl",
           (("UMii_Common_Body_W_Animation", True), (PLAYER_ANIM, True)), _with(UMII_W, RunGear2="Common_Move_Run"),
           load_mb=10.6, aliases=("princess",), v=6, ctrl=(("RunGear2", SPRINT_RATE, None, None),),
           cloth=CLOTH_ZELDA, legs=True,
           looks="Her game actor's own stand, walk, run (sprint: her run, faster); Link's moves for the rest. "
                 "Her dress, hair and necklace swing; legs under the dress (a Hylian woman's, in her skin tone)."),
    Recipe("hylian", "Hylian", "Hylians", "pack", "Npc_HatenoVillage004",
           (("UMii_Common_Body_M_Animation", True), (PLAYER_ANIM, True)), UMII_M, load_mb=11.5,
           aliases=("hateno", "villager"), looks="A Hateno villager: his own walk, Link's moves for the rest."),
    # Paya: her own shy stand (her own animation file, made for her skeleton: no retarget, as her game pack has it),
    # then the walk and run her game pack uses (the Hylian women's), Link's moves for the rest (retargeted).
    # load_mb: 6.4 + her own file (0.4 MB unpacked).
    Recipe("sheikah", "Paya (Sheikah)", "Races", "pack", "Npc_Kakariko002",
           (("Npc_Shiekah_Heir_Animation", False), ("UMii_Common_Body_W_Animation", True), (PLAYER_ANIM, True)),
           _with(UMII_W, Wait="Npc_Shiekah_Heir_Talk_Shy_Wait", RunGear2="Common_Move_Run"), load_mb=6.8,
           aliases=("paya",), v=4, ctrl=(("RunGear2", SPRINT_RATE, None, None),), cloth=CLOTH_PAYA,
           looks="Her own shy stand; the game's walk and run for her; Link's moves for the rest. Her hair swings "
                 "(cloth physics, not yet tried in the game)."),
    # Urbosa (v4): the women's file, where her game actor finds her stand, walk and run, no longer the Gerudo guards'
    # file (v3 had it first: her stand and walk were a Gerudo guard's; the real Urbosa never loads it [V]). Her actor
    # also loads the men's file (5.1 MB unpacked) for a real sprint; left out to keep her light: her run, faster.
    # load_mb: 16.2 - 2.9 (the guards' file, unpacked) [V].
    # v5 (the round-4 game check: ours stood straight, the spawned Dm_Npc_Gerdo_Hero hip-shot): her actor's Wait is
    # UC_M_Wait and her Walk UC_M_Walk [V: its animation list], whose personality branch is Man_Active / Man_Deflated /
    # default; her look file says Woman_Active [V], and what she plays is the Active branch [G: the hip-shot stand the
    # user saw; the default branch is our straight Default_Wait]: Positive_Wait and Positive_Move_Walk, found first in
    # the women's file [V: both there]. Her run has no personality branch (Common_Move_Run) [V].
    Recipe("gerudo", "Urbosa (Gerudo)", "Races", "pack", "Dm_Npc_Gerdo_Hero",
           (("UMii_Common_Body_W_Animation", True), (PLAYER_ANIM, True)),
           _with(UMII_W, Wait="Positive_Wait", Walk="Positive_Move_Walk", RunGear2="Common_Move_Run"),
           load_mb=13.3, aliases=("urbosa",), v=6, ctrl=(("RunGear2", SPRINT_RATE, None, None),),
           cloth=CLOTH_URBOSA,
           looks="Tall. Her game actor's own hip-shot stand, walk and run (sprint: her run, faster); Link's moves for "
                 "the rest. Her skirt, hair and earring swing (cloth physics, not yet tried in the game)."),
    Recipe("goron", "Daruk (Goron)", "Races", "pack", "Dm_Npc_Goron_Hero",
           (("UMii_Goron_BodyC_M_Animation", True), (PLAYER_ANIM, True)),
           _with(UMII_M, RunGear2="Common_Move_Ball"), load_mb=10.9, aliases=("daruk",), v=4, cloth=CLOTH_DARUK,
           looks="Sprints as a rolling ball. Link's climb and glide on a Goron body may look odd [G]. His hair, beard "
                 "and cloth swing (cloth physics, not yet tried in the game)."),
    Recipe("rito", "Revali (Rito)", "Races", "pack", "Dm_Npc_Rito_Hero",
           (("UMii_Rito_Body_M_Animation", True), ("UMii_Common_Body_M_Animation", True),
            ("Npc_Rito_Teba_Animation", True), (PLAYER_ANIM, True)),
           _with(UMII_M, ParashawlGlide="Npc_Rito_Teba_BattleFly"), load_mb=10.6, aliases=("revali",), v=4,
           cloth=CLOTH_REVALI,
           looks="Glides with real wings (a Rito flying clip). His scarf, braids and plume swing (cloth physics, not "
                 "yet tried in the game)."),
    # Mipha: the stand, walk and run her game pack plays (the Hylian women's file, retargeted to her), and a Zora
    # WOMAN's swim (UMii_Zora_BodyC_W; v3 had the Zora men's file, whose Default_Wait is a man's stance too). Her
    # size is her game model's, scale 1 (hips 0.75 m, legs 0.54 m: her own game clip Gift_Healing has her hips at
    # 0.753 m) [V]. load_mb: 11.0 + 0.2 (the women's file is bigger than the men's).
    Recipe("zora", "Mipha (Zora)", "Races", "pack", "Dm_Npc_Zora_Hero",
           (("UMii_Common_Body_W_Animation", True), ("UMii_Zora_BodyC_W_Animation", True), (PLAYER_ANIM, True)),
           _with(UMII_W, SwimWait="Common_Swim_Wait", SwimMove="Common_Move_Swim_Walk",
                 SwimDash="Common_Move_Swim_Run", RunGear2="Common_Move_Run"), load_mb=11.2, aliases=("mipha",), v=6,
           ctrl=(("RunGear2", SPRINT_RATE, None, None),), cloth=CLOTH_MIPHA,
           looks="Her game size (shorter than Link). Swims with a Zora woman's swim clips (lying in the water when "
                 "she moves, upright when she treads). Her sash and fins swing (cloth physics, not yet tried in the "
                 "game)."),
    Recipe("bokoblin", "Bokoblin", "Monsters", "pack", "Enemy_Bokoblin_Junior",
           (("Enemy_Bokoblin_Animation", False),), BOKO, rest="speed", load_mb=6.2, aliases=("boko",), v=4,
           ctrl=BOKO_CTRL, cloth=CLOTH_BOKO,
           looks="Its own clips; each swing timed to Link's hit and picked by his weapon. No climb or glide pose; "
                 "it wades in water. Apron and ears swing (cloth physics, not yet tried in the game)."),
    Recipe("moblin", "Moblin", "Monsters", "pack", "Enemy_Moriblin_Junior",
           (("Enemy_Moriblin_Animation", False),), MOBLIN, rest="speed", load_mb=7.8, aliases=("moriblin",), v=4,
           ctrl=MOBLIN_CTRL, cloth=CLOTH_MOBLIN,
           looks="Its own clips; each swing timed to Link's hit and picked by his weapon. No climb or glide pose; "
                 "it wades in water. Big: may pop in at the screen's edges. Its loincloth swings (cloth physics, not "
                 "yet tried in the game)."),
)}

CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Hylians", ("link", "linkle", "zelda", "hylian")),
    ("Races", ("sheikah", "gerudo", "goron", "rito", "zora")),
    ("Monsters", ("bokoblin", "moblin")),
)

# Free actor names: in the game's ActorInfo, with no pack anywhere in the base game and no size-table entry (cutscene
# names it never spawns; out/research/charsel-ghost-characters.md [V]). The cutscene-class ones first (the same class
# as today's ghost, which works); the NPC-class and tall-box ones are not yet tried in the game [G].
DEMO_SLOTS = ("Dm_Npc_Sheiker_Searcher_M_000", "Dm663_Npc_Assasin_A", "Dm663_Npc_Assasin_B")
NPC_SLOTS = ("Dm_Npc_UMii_Hylia_BodyS_M_001", "Dm_Npc_UMii_Hylia_BodyS_M_002", "Dm_Npc_UMii_Hylia_BodyS_M_003",
             "Dm_Npc_UMii_Hylia_BodyS_M_011", "Dm_Npc_UMii_Hylia_BodyS_M_012", "Dm_Npc_Handmaid_A", "Dm_Npc_Handmaid_B")
TALL_SLOTS = tuple(f"Dm663_Npc_Gerudo_S_{c}" for c in "ABCDEF")
FREE_SLOTS = DEMO_SLOTS + NPC_SLOTS + TALL_SLOTS
SLOTS = {"zelda": DEMO_SLOTS[0], "goron": DEMO_SLOTS[1], "bokoblin": DEMO_SLOTS[2],
         "hylian": NPC_SLOTS[0], "sheikah": NPC_SLOTS[1], "zora": NPC_SLOTS[2], "rito": NPC_SLOTS[3],
         "gerudo": NPC_SLOTS[6], "moblin": TALL_SLOTS[1],   # (Urbosa: a bigger heap than the tall box, HEAP_BASE)
         "linkle": NPC_SLOTS[4], "link": NPC_SLOTS[5]}
# (Link: only with --model-copies. Linkle: her costume, for the FRIENDS' view only (2026-11-18 review C1): your own
# Linkle is The Linkle Mod as Link's body, so no self-puppet uses it.)
SLOT_PROFILE = {**{s: "DemoNPC" for s in DEMO_SLOTS}, **{s: "NPC" for s in NPC_SLOTS + TALL_SLOTS}}


class CharselError(RuntimeError):
    pass


def order() -> list[str]:
    """Every id in wheel order."""
    return [i for _, ids in CATEGORIES for i in ids]


def find_id(text) -> str | None:
    """The character a player means: an id, a name or an alias (any case, spaces or _ for -). None if none."""
    if not isinstance(text, str):
        return None
    t = re.sub(r"[\s_]+", "-", text.strip().lower())
    if t in RECIPES:
        return t
    for r in RECIPES.values():
        if t in r.aliases or t == r.name.lower().replace(" ", "-") or t == r.name.split(" (")[0].lower():
            return r.id
    return None


def valid_id(text) -> str | None:
    """A look id as it may travel: lower-case letters, digits and -, 1-24, starting with a letter or digit."""
    return text if isinstance(text, str) and _ID_RE.fullmatch(text) else None


def look_message(cid: str) -> dict:
    """What goes in the hello's and the `look` message's "look": the id, the recipe version and the wheel name."""
    r = RECIPES.get(cid)
    return {"id": cid, "v": r.v if r else 1, "name": (r.name if r else cid)[:28]}


def display(look: dict | None) -> str:
    """A look's name for a person (its own name if it came with one, else the id)."""
    if not isinstance(look, dict):
        return "Link"
    r = RECIPES.get(look.get("id"))
    return r.name if r else str(look.get("name") or look.get("id") or "?")[:28]


# ------------------------------------------------------------------------------------------------ your choice

def home_dir(env=None) -> Path:
    """The launcher's folder (BREATHALYZER_HOME, else %LOCALAPPDATA%\\Breathalyzer), as tools/launcher.py has it."""
    env = os.environ if env is None else env
    if env.get(ENV_HOME):
        return Path(env[ENV_HOME]).resolve()
    base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Breathalyzer"


def state_path(env=None) -> Path:
    return home_dir(env) / STATE


def load_choice(path: Path | None = None) -> dict:
    """{"look": id, "packs": folder or None}. A missing or broken file gives Link and no folder."""
    path = Path(path) if path else state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    look = data.get("look") if find_id(data.get("look")) == data.get("look") else None
    packs = data.get("packs") if isinstance(data.get("packs"), str) and data.get("packs") else None
    return {"look": look or "link", "packs": packs}


def save_choice(cid: str, path: Path | None = None, packs: str | None = None) -> Path:
    """Keep your choice (a temp file, then a rename). Other keys in the file are kept."""
    if find_id(cid) != cid:
        raise CharselError(f"no character {cid!r}")
    path = Path(path) if path else state_path()
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        old = old if isinstance(old, dict) else {}
    except (OSError, ValueError):
        old = {}
    old.update(format=1, look=cid)
    if packs:
        old["packs"] = str(packs)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(old, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


# ------------------------------------------------------------------------------------------------ seeing others

def load_catalog(folder) -> dict:
    """The built mod folder's charsel.json ({} when there is none or it can't be read)."""
    if not folder:
        return {}
    p = Path(folder)
    p = p if p.suffix == ".json" else p / CATALOG
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("built"), dict) else {}


def overlay_dir(library=None) -> Path:
    """Where modman deploy (and Play) puts the RomFS files the game reads (library\\deploy\\overlay)."""
    return Path(library or DEFAULT_LIBRARY) / "deploy" / "overlay"


def deployed_catalog(library=None, overlay=None) -> dict:
    """The characters this PC's game really gets: the catalog of this mod's library copy while it is enabled (modman
    add out\\charsel-packs), keeping only the characters whose pack is in the deployed overlay
    (Actor\\Pack\\<free name>.sbactorpack). {} when there is none: every look is then the Link body. The catalog
    says where it came from ("where") and what it left out ("not_deployed")."""
    lib = Path(library or DEFAULT_LIBRARY)
    ov = Path(overlay) if overlay else overlay_dir(lib)
    for m in reversed(library_mods(lib)):            # (the last enabled copy wins, as in the deploy)
        cat = load_catalog(m["folder"])
        if cat.get("mod") != MOD_ID:
            continue
        built, gone = {}, []
        for cid, info in cat["built"].items():
            slot = info.get("slot") if isinstance(info, dict) else None
            pack = ov / "Actor" / "Pack" / f"{slot}.sbactorpack" if isinstance(slot, str) else None
            if slot in FREE_SLOTS and pack is not None and pack.is_file():
                built[cid] = info
            else:
                gone.append(cid)
        return dict(cat, built=built, not_deployed=gone, where=str(m["folder"]))
    return {}


def here_catalog(folder=None, library=None) -> tuple[dict, str]:
    """(the catalog to show looks with, where it is from). A folder you name (--ghost-looks DIR, --out) is taken as
    it is; otherwise only what this PC's game really gets (deployed_catalog)."""
    if folder:
        return load_catalog(folder), str(folder)
    cat = deployed_catalog(library)
    if not cat:
        return {}, "none in your game (build, add to your mods, then Play or deploy)"
    return cat, cat["where"]


def linkle_ready(cat: dict | None) -> bool | None:
    """Whether this PC shows Linkle (the wheel's Ready): her costume is built (friends who pick her show as it), or the
    Linkle Mod was on when the characters were built (Link's body is Linkle then). None: no catalog. (tools/mp_page.py
    and tools/mp/lobby.py ask this too.)"""
    if not cat:
        return None
    lk = cat.get("linkle") if isinstance(cat.get("linkle"), dict) else {}
    return lk.get("how") == "link-body" or "linkle" in (cat.get("built") or {})


class Looks:
    """How this PC shows other players' looks: an id from the network becomes an actor name only through this PC's
    own catalog (what it built, on which free name), never raw. Anything else is the Link body, with a reason."""

    def __init__(self, catalog: dict | None = None, fallback: str = LINK_ACTOR):
        catalog = catalog or {}
        self.fallback = fallback
        self.slots: dict[str, str] = {}
        for cid, info in (catalog.get("built") or {}).items():
            slot = info.get("slot") if isinstance(info, dict) else None
            if cid in RECIPES and isinstance(slot, str) and slot in FREE_SLOTS:
                self.slots[cid] = slot   # (Linkle's too: a friend who picks her shows as her costume)
        lk = catalog.get("linkle") if isinstance(catalog.get("linkle"), dict) else {}
        self.linkle_body = lk.get("how") == "link-body"   # Linkle mod here: the Link body *is* Linkle
        self.failed: set[str] = set()                      # slots that didn't load in this session

    def actor(self, look) -> tuple[str, str]:
        """(the actor name to spawn, "" or why it is the Link body instead)."""
        cid = look.get("id") if isinstance(look, dict) else None
        cid = cid.split(":", 1)[1] if isinstance(cid, str) and cid.startswith("built:") else cid
        if cid is None or cid == "link":
            slot = self.slots.get("link")
            return (slot, "") if slot and slot not in self.failed else (self.fallback, "")
        r = RECIPES.get(cid)
        if r is None:
            return self.fallback, "unknown here (a newer Breathalyzer?)"
        slot = self.slots.get(cid)
        if slot and slot in self.failed:
            return self.fallback, "didn't load in this game"
        if slot:
            return slot, ""
        if r.kind == "linkle":
            return (self.fallback, "") if self.linkle_body else (self.fallback, "the Linkle mod isn't installed here")
        return self.fallback, "not built here"

    def mark_failed(self, slot: str) -> bool:
        """The mod gave up spawning this slot: show the Link body instead for the rest of the session."""
        if slot == self.fallback or slot in self.failed:
            return False
        self.failed.add(slot)
        return True

    def ids(self) -> list[str]:
        """What this PC can pick from (the wheel's order): everything; a friend who lacks one sees the Link body."""
        return order()


# ------------------------------------------------------------------------------------------------ the wheel

class Wheel:
    """Who am I, as a state machine (no window): the launcher's window, `charsel wheel` and the in-game screen draw its
    lines() or slices() and send it keys. Left/Right: the next character around the wheel; Up/Down: the next group;
    Enter: pick (returns the id); Esc: back (returns ""). A controller's d-pad and A/B map to the same."""

    def __init__(self, current: str = "link", built: dict | None = None):
        self.ids = order()
        self.built = built if built is not None else None   # catalog "built" (None: don't say what is built)
        self.linkle = None
        self.pos = self.ids.index(current) if current in self.ids else 0
        self.start = self.ids[self.pos]
        self.done: str | None = None
        self.playas_note = ""       # P (the restart fallback): what the background build says now
        self.playas_busy = False
        self.playas_run = None      # (tests: a stand-in for playas_on)

    @property
    def cid(self) -> str:
        return self.ids[self.pos]

    def cat(self) -> str:
        return RECIPES[self.cid].cat

    def key(self, k: str) -> str | None:
        """One key: 'left' 'right' 'up' 'down' 'enter' 'esc'. Returns the id on Enter, "" on Esc, else None."""
        n = len(self.ids)
        if k == "right":
            self.pos = (self.pos + 1) % n
        elif k == "left":
            self.pos = (self.pos - 1) % n
        elif k in ("down", "up"):
            names = [c for c, _ in CATEGORIES]
            i = (names.index(self.cat()) + (1 if k == "down" else -1)) % len(names)
            self.pos = self.ids.index(CATEGORIES[i][1][0])
        elif k == "enter":
            self.done = self.cid
            return self.cid
        elif k == "esc":
            self.done = ""
            return ""
        elif k == "playas":
            self.start_playas(self.cid)
        return None

    def start_playas(self, cid: str, wait: bool = False) -> None:
        """P on the wheel: build the play-as mod for cid in the background (playas_on: about a minute) and say how
        it went in playas_note. The live view (`iam` in the game) needs no restart; this is the restart way."""
        r = RECIPES[cid]
        short = r.name.split(" (")[0]
        if self.playas_busy:
            return
        if cid not in PLAYAS_PEOPLE:
            self.playas_note = ("You are Link already." if r.kind != "pack"
                                else f"{short} can't be played as at a restart yet (live only).")
            return
        self.playas_busy = True
        self.playas_note = f"Building Play as {short} (about a minute)..."
        run = self.playas_run or playas_on

        def work() -> None:
            try:
                info = run(cid, say=lambda _s: None)
                off = info.get("turned_off") or []
                self.playas_note = (f"Done. Restart the game to play as {short}." +
                                    (f" {', '.join(off)} is off meanwhile." if off else ""))
            except CharselError as e:
                self.playas_note = f"Play as {short}: {e}"
            except Exception as e:   # (anything else: said, never a crash of the menu)
                self.playas_note = f"Play as {short} failed: {e}"
            finally:
                self.playas_busy = False

        if wait:
            work()
            return
        import threading
        threading.Thread(target=work, name="charsel-playas", daemon=True).start()

    def status(self, cid: str | None = None) -> str:
        """Ready / Build first / Needs a mod (the plan's three words), for this PC."""
        r = RECIPES[cid or self.cid]
        if r.kind == "link":
            return "Ready"
        if r.kind == "linkle":
            return "Ready" if self.linkle else "Needs a mod"
        if self.built is None:
            return ""
        return "Ready" if r.id in self.built else "Build first"

    def lines(self) -> list[str]:
        r = RECIPES[self.cid]
        groups = "   ".join(f"[{c.upper()}]" if c == r.cat else c for c, _ in CATEGORIES)
        st = self.status()
        short = r.name.split(" (")[0]
        out = ["Who am I?", f"  {groups}", f"  <  {r.name}  >" + (f"   {st}" if st else ""),
               f"  Others see you as: {short}." + (f" You see yourself as {short} too, live in the game."
                                                    if r.kind == "pack" else
                                                    # (review C6: a pick here never switches mods)
                                                    " To be Linkle yourself, pick her in Breathalyzer's Who am I "
                                                    "(The Linkle Mod on, one restart)." if r.kind == "linkle" else
                                                    " You play as: Link.")]
        if r.looks:
            out.append(f"  {r.looks}")
        if self.playas_note:
            out.append(f"  {self.playas_note}")
        out.append("  Left/Right: character   Up/Down: group   Enter: OK   Esc: back" +
                   (f"   P: restart as {short}" if r.id in PLAYAS_PEOPLE else ""))
        return out

    def slices(self) -> list[dict]:
        """The wheel for a drawing: one slice per character, in order, with its angle span (degrees, 0 = up,
        clockwise), group and whether it is the chosen one."""
        n = len(self.ids)
        step = 360.0 / n
        return [{"id": cid, "name": RECIPES[cid].name.split(" (")[0], "cat": RECIPES[cid].cat,
                 "start": i * step - step / 2, "extent": step, "on": i == self.pos} for i, cid in enumerate(self.ids)]


# XInput (a controller): the d-pad or the left stick move, A picks, B goes back. Pure mapping here; the window polls.
XI_UP, XI_DOWN, XI_LEFT, XI_RIGHT, XI_A, XI_B, XI_Y = 0x0001, 0x0002, 0x0004, 0x0008, 0x1000, 0x2000, 0x8000
STICK_DEAD = 16000
PAD_KEYS = ((XI_UP, "up"), (XI_DOWN, "down"), (XI_LEFT, "left"), (XI_RIGHT, "right"), (XI_A, "enter"), (XI_B, "esc"),
            (XI_Y, "playas"))     # (Y: restart as the character, the play-as mod)


def pad_buttons(buttons: int, lx: int = 0, ly: int = 0) -> int:
    """The pad's buttons with the left stick folded into the d-pad bits (past STICK_DEAD)."""
    b = buttons & 0xFFFF
    if lx <= -STICK_DEAD:
        b |= XI_LEFT
    elif lx >= STICK_DEAD:
        b |= XI_RIGHT
    if ly >= STICK_DEAD:
        b |= XI_UP
    elif ly <= -STICK_DEAD:
        b |= XI_DOWN
    return b


def pad_keys(prev: int, now: int) -> list[str]:
    """The keys for buttons that went down since the last poll."""
    pressed = now & ~prev
    return [k for bit, k in PAD_KEYS if pressed & bit]


class Pad:
    """The first XInput controller, polled (Windows; does nothing elsewhere or without a controller)."""

    def __init__(self):
        self.dll = None
        self.prev = 0
        if sys.platform != "win32":
            return
        import ctypes
        for name in ("xinput1_4", "xinput9_1_0", "xinput1_3"):
            try:
                self.dll = getattr(ctypes.windll, name)
                break
            except OSError:
                continue
        self._buf = ctypes.create_string_buffer(16) if self.dll else None

    def keys(self) -> list[str]:
        if self.dll is None:
            return []
        import struct
        try:
            if self.dll.XInputGetState(0, self._buf) != 0:
                self.prev = 0
                return []
        except OSError:
            self.dll = None
            return []
        _pkt, buttons, _lt, _rt, lx, ly = struct.unpack_from("<IHBBhh", self._buf.raw)
        now = pad_buttons(buttons, lx, ly)
        out, self.prev = pad_keys(self.prev, now), now
        return out


TK_KEYS = {"Left": "left", "Right": "right", "Up": "up", "Down": "down", "Return": "enter", "KP_Enter": "enter",
           "Escape": "esc", "BackSpace": "esc", "space": "enter", "p": "playas", "P": "playas"}
CAT_COLORS = {"Hylians": "#237032", "Races": "#1971c2", "Monsters": "#c92a2a"}   # (white text: 5:1 or more)
MIN_PT = 18                       # the smallest text on the wheel (points)


def screen_scale(root) -> float:
    """How much bigger than 96 dpi this screen draws (1.0-3.0): the canvas is in pixels, fonts are in points, so a
    window made DPI aware (modman_gui.dpi_aware) scales the canvas by this to keep the labels inside their slices."""
    try:
        return max(1.0, min(3.0, float(root.winfo_fpixels("1i")) / 96.0))
    except Exception:   # (no display)
        return 1.0


class WheelWindow:
    """The wheel drawn on a Tk canvas: one slice per character around a ring (its group's colour, the chosen one
    lit), the chosen name big in the middle ("Daruk (Goron)"), its group and status, and under the wheel who others
    see, the honest looks line and the hints. Big text (at least MIN_PT points; the player's eyes come first). scale:
    screen_scale(root) when the window is DPI aware; the wheel never grows taller than the screen."""

    def __init__(self, root, wheel: Wheel, size: int = 640, font: int = 26, on_done=None, pad: Pad | None = None,
                 scale: float = 1.0):
        import tkinter as tk
        self.root, self.wheel, self.font, self.on_done = root, wheel, font, on_done
        self.pad = pad
        self.px = scale * 96 / 72                    # pixels per point
        self.small = max(MIN_PT, font * 2 // 3)
        self.text_h = int(self.small * self.px * 8.5)   # who + looks (up to 3 lines) + hints, with gaps
        try:
            room = int(root.winfo_screenheight()) - self.text_h - 120
        except Exception:   # (no display)
            room = 10_000
        self.size = max(360, min(int(size * scale), room))
        root.configure(bg="#101418")
        self.canvas = tk.Canvas(root, width=self.size, height=self.size + self.text_h, bg="#101418",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        root.bind("<Key>", self._on_key)
        self.draw()

    def _on_key(self, ev) -> None:
        k = TK_KEYS.get(ev.keysym)
        if k:
            self.press(k)

    def press(self, k: str) -> None:
        got = self.wheel.key(k)
        self.draw()
        if self.wheel.playas_busy:
            self.root.after(500, self._poll_playas)
        if got is not None and self.on_done is not None:
            self.on_done(got)

    def _poll_playas(self) -> None:
        """Redraw while P's build runs (its words change when it is done)."""
        try:
            self.draw()
            if self.wheel.playas_busy:
                self.root.after(500, self._poll_playas)
        except Exception:   # (the window is gone)
            pass

    def poll_pad(self) -> None:
        if self.pad is not None:
            for k in self.pad.keys():
                self.press(k)
            if self.wheel.done is None:
                self.root.after(50, self.poll_pad)

    def _fit(self, text: str, pt: int, width: float, bold: bool = True) -> tuple:
        """The biggest font from pt down to MIN_PT that fits text in width pixels."""
        try:
            import tkinter.font as tkfont
            while pt > MIN_PT and tkfont.Font(root=self.root, family="Segoe UI", size=pt,
                                              weight="bold" if bold else "normal").measure(text) > width:
                pt -= 1
        except Exception:   # (no display: the asked size)
            pass
        return ("Segoe UI", pt, "bold") if bold else ("Segoe UI", pt)

    def draw(self) -> None:
        import math
        c, s, f, sm, px = self.canvas, self.size, self.font, self.small, self.px
        c.delete("all")
        mid, r_out, r_in = s / 2, s / 2 - 10, s / 4
        rr = (r_out + r_in) / 2
        slices = self.wheel.slices()
        chord = 2 * rr * math.sin(math.radians(slices[0]["extent"] / 2)) - 8 if slices else s
        for sl in slices:
            color = CAT_COLORS.get(sl["cat"], "#495057")
            # (Tk arcs: 0 degrees is 3 o'clock, counter-clockwise; ours: 0 is 12 o'clock, clockwise)
            start = 90 - sl["start"] - sl["extent"]
            c.create_arc(mid - r_out, mid - r_out, mid + r_out, mid + r_out, start=start, extent=sl["extent"],
                         fill="#ffd43b" if sl["on"] else color, outline="#101418", width=3, tags=("slice", sl["id"]))
            a = math.radians(sl["start"] + sl["extent"] / 2)
            c.create_text(mid + rr * math.sin(a), mid - rr * math.cos(a), text=sl["name"],
                          fill="#000000" if sl["on"] else "#ffffff", font=self._fit(sl["name"], sm, chord),
                          tags=("label", sl["id"]))
        c.create_oval(mid - r_in, mid - r_in, mid + r_in, mid + r_in, fill="#101418", outline="#101418")
        r = RECIPES[self.wheel.cid]
        st = self.wheel.status()
        c.create_text(mid, mid - f * px * 0.55, text=r.name, fill="#ffffff", font=self._fit(r.name, f, 2 * r_in - 16),
                      tags=("name",))
        c.create_text(mid, mid + sm * px * 0.9, text=r.cat + (f"  -  {st}" if st else ""), fill="#ffffff",
                      font=self._fit(r.cat + (f"  -  {st}" if st else ""), sm, 2 * r_in - 24, bold=False),
                      tags=("cat",))
        y = s + sm * px * 0.3
        short = r.name.split(" (")[0]
        who = f"Others see you as: {r.name}" + ("  -  you too, live" if r.kind == "pack" else "")
        c.create_text(mid, y, text=who, fill="#ffffff", anchor="n", font=("Segoe UI", sm, "bold"), width=s - 16,
                      tags=("who",))
        y += sm * px * 1.6
        note = self.wheel.playas_note or r.looks
        if note:
            c.create_text(mid, y, text=note, fill="#ffd43b" if self.wheel.playas_note else "#f1f3f5", anchor="n",
                          font=("Segoe UI", sm), width=s - 16, justify="center", tags=("looks",))
        c.create_text(mid, s + self.text_h - sm * px * 0.3, text="Left/Right: character   Up/Down: group   "
                      "Enter: OK   Esc: back" + (f"   P: restart as {short}" if r.id in PLAYAS_PEOPLE else ""),
                      fill="#dee2e6", anchor="s", font=("Segoe UI", sm), width=s - 16, justify="center",
                      tags=("hint",))


def choose_window(current: str = "link", built: dict | None = None, linkle: bool | None = None,
                  timeout_s: float | None = None) -> str | None:
    """Show the wheel in a window and return the picked id (None: closed or Esc). Opens a window: never in tests."""
    import tkinter as tk
    root = tk.Tk()
    root.title("Who am I")
    wheel = Wheel(current, built)
    wheel.linkle = linkle
    picked: list[str | None] = [None]

    def done(cid: str) -> None:
        picked[0] = cid or None
        root.quit()

    win = WheelWindow(root, wheel, on_done=done, pad=Pad(), scale=screen_scale(root))
    win.poll_pad()
    if timeout_s:
        root.after(int(timeout_s * 1000), root.quit)
    root.protocol("WM_DELETE_WINDOW", root.quit)
    try:
        root.mainloop()
    finally:
        root.destroy()
    return picked[0]


# ------------------------------------------------------------------------------------------------ the library

def library_mods(library) -> list[dict]:
    """The enabled mods of a modman library, in order (later wins): {"id", "name", "version", "folder"}."""
    try:
        cfg = json.loads((Path(library) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    out = []
    for m in cfg.get("mods") or [] if isinstance(cfg, dict) else []:
        if isinstance(m, dict) and m.get("enabled") and isinstance(m.get("id"), str):
            out.append({"id": m["id"], "name": str(m.get("name") or m["id"]), "version": str(m.get("version") or ""),
                        "folder": Path(library) / "mods" / m["id"]})
    return out


ROMFS_TOPS = ("romfs", "01007EF00011E000/romfs", "atmosphere/contents/01007EF00011E000/romfs", "")


def _link_model_in(folder: Path) -> tuple[Path, str] | None:
    """Where a mod folder replaces Link's model: (Pack/TitleBG.pack, member) or (Model/Link.sbfres, "")."""
    from mp import ghost_link_pack as glp
    for top in ROMFS_TOPS:
        base = folder / top if top else folder
        loose = base / "Model" / "Link.sbfres"
        if loose.is_file():
            return loose, ""
        title = base / "Pack" / "TitleBG.pack"
        if title.is_file():
            try:
                if glp._sarc_member(title, "Model/Link.sbfres") is not None:
                    return title, "Model/Link.sbfres"
            except OSError:
                pass
    return None


def player_model_mod(library, romfs=None) -> dict | None:
    """The enabled mod that replaces Link's model (the last one wins), or None: {"id", "name", "version", "linkle",
    "where", "member", "fp"} (fp: the first 16 hex of the model file's sha256). romfs (the game's files): a mod whose
    Link model is the game's own byte for byte doesn't count (2026-11-18: Emote animations ships a whole TitleBG.pack
    filled from the game, and the file-by-file merge keeps The Linkle Mod's model over it). Reads only."""
    found = None
    game = model_file(Path(romfs), "Link") if romfs is not None else None
    for m in library_mods(library):
        hit = _link_model_in(m["folder"])
        if hit and not (game and _read_link_model(Path(hit[0]), hit[1]) == game):
            found = dict(m, where=str(hit[0]), member=hit[1])
    if found is None:
        return None
    data = _read_link_model(Path(found["where"]), found["member"])
    found["fp"] = hashlib.sha256(data).hexdigest()[:16] if data else ""
    found["linkle"] = "linkle" in (found["id"] + " " + found["name"]).lower()
    found["folder"] = str(found["folder"])
    return found


def _read_link_model(where: Path, member: str, tex: bool = False) -> bytes | None:
    return _read_mod_model(where, member, "Link.Tex" if tex else "Link")


def _read_mod_model(where: Path, member: str, name: str) -> bytes | None:
    """Model/<name>.sbfres next to a mod's Link model (where/member as _link_model_in gives them): in the same pack, or
    loose in the mod's romfs Model folder (The Linkle Mod keeps her armor there [V]); None when neither has it."""
    from mp import ghost_link_pack as glp
    if not member:
        p = Path(where).with_name(f"{name}.sbfres")
        return p.read_bytes() if p.is_file() else None
    got = glp._sarc_member(Path(where), f"Model/{name}.sbfres")
    if got is None:
        p = Path(where).parent.parent / "Model" / f"{name}.sbfres"    # (romfs/Pack/TitleBG.pack -> romfs/Model)
        got = p.read_bytes() if p.is_file() else None
    return got


def linkle_mod(library) -> dict | None:
    """The Linkle Mod in a modman library, ON OR OFF (read only; the last one listed wins): its own files make the
    Linkle costume, so `iam linkle` works with the mod off too. {"id", "name", "version", "enabled", "where",
    "member", "fp"}, or None when the library has none."""
    try:
        cfg = json.loads((Path(library) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    found = None
    for m in (cfg.get("mods") or []) if isinstance(cfg, dict) else []:
        if not (isinstance(m, dict) and isinstance(m.get("id"), str)):
            continue
        name = str(m.get("name") or m["id"])
        if "linkle" not in (m["id"] + " " + name).lower():
            continue
        hit = _link_model_in(Path(library) / "mods" / m["id"])
        if hit:
            found = {"id": m["id"], "name": name, "version": str(m.get("version") or ""),
                     "enabled": bool(m.get("enabled")), "where": str(hit[0]), "member": hit[1]}
    if found:
        data = _read_link_model(Path(found["where"]), found["member"])
        found["fp"] = hashlib.sha256(data).hexdigest()[:16] if data else ""
    return found


# The Linkle costume (friends' view: a friend who picks Linkle shows as this, review C1 2026-11-18): the Linkle Mod's
# body (its Model/Link) wears its default look (its Model/Armor_Default: the hair, the skirt and the pouches, which the
# game puts on the player as armor). One model with both, so a costume pack shows all of her.
LINKLE_LOOK = "Armor_Default"
# Linkle v3 (2026-11-18 night, friends' view): v2's Armor_Default alone is her NO-armor look (bandeau, shorts, bare
# feet). Her outfit is the mod's Model/Armor_001 (the game's Hylian set, which the mod turns into her hood, tunic, skirt,
# tights and boots; its Head_A has her hair and the hood both up and down [V: 3.0.1's shape names]), merged on like
# Zelda's legs (merge_parts), with her belt pouches from Armor_Default (the tunic's IsDispOffPorch is false [V]).
LINKLE_OUTFIT = "Armor_001"
LINKLE_FROM_LOOK = ("Armor_Default_Extra_00", "Armor_Default_Extra_01")   # her pouches (the look's hair: Head_A's)
LINKLE_HOOD_UP = ("Hood_A_001_Metal__Mt_Hood_001", "green hood", "green necklace")   # left out: the hood is down [G]
OUTFIT_PREFIX = "SwOutfit_"  # an outfit texture named like one already in the costume's textures
# Parts merged onto a body (merge_parts: Zelda's legs on her model): each part's shapes go on the body's skeleton.
PART_PREFIX = "SwPart"      # a part's material names: SwPart<k>_<name> (k: the part's number)
REST_GAP = 1e-3             # m: a part bone may rest this far from the body's (else its bone-space shapes are left out)


def merge_parts(base, parts, tex_rename: dict | None = None) -> tuple[object, dict]:
    """The model `base` (a neutral Model) with the shapes of `parts` (models skinned to bones named as the base's, like
    an armor's) added: one skeleton (the base's bones, then each part's own new bones, resting where they rest in the
    part), each part vertex skinned to the merged skeleton's matrix for the same bone. Both bind poses must be their
    skeletons' rest (the game's models are: checked), so a vertex in model space stays where it was; a bone-space
    (rigid) part shape whose bone rests elsewhere in the base is left out. Materials: SwPart<k>_<name>, textures
    renamed by tex_rename (one dict for all parts, or a list with one per part). Returns (the model, info)."""
    import copy
    import struct
    from wiiu_model import formats
    from wiiu_model.neutral import Bone, Skeleton
    m = copy.deepcopy(base)
    sk = m.skeleton
    if not sk.flags & EULER_ROT:
        raise CharselError("only Euler skeletons are merged here")
    bones = list(sk.bones)
    index = {b.name: i for i, b in enumerate(bones)}
    if len(index) != len(bones):
        raise CharselError("the body's skeleton has a bone name twice")
    worlds = _worlds(bones)
    s0 = sk.smooth_count
    smooth = list(sk.matrix_to_bone[:s0])
    rigid = list(sk.matrix_to_bone[s0:])
    ibs = bytearray(sk.inverse_matrices)
    smooth_of = {b: k for k, b in enumerate(smooth)}
    rigid_of = {b: k for k, b in enumerate(rigid)}
    new_smooth: list[int] = []      # bones of the smooth matrices added (after the base's)
    new_rigid: list[int] = []
    pending: list[tuple] = []       # (vertex buffer, [(attribute, {part matrix: ("s"|"r", merged bone)})])
    info = {"parts": [], "shapes": 0, "left_out": [], "new_bones": 0}

    def check_bind(p):
        pw = _worlds(p.skeleton.bones)
        for k in range(p.skeleton.smooth_count):
            ib = struct.unpack_from("<12f", p.skeleton.inverse_matrices, 48 * k)
            want = _inv(pw[p.skeleton.matrix_to_bone[k]])
            if max(abs(ib[4 * r + c] - want[r][c]) for r in range(3) for c in range(4)) > 1e-3:
                raise CharselError(f"{p.name}'s bind pose isn't its rest pose: not merged")
        return pw

    for k, part in enumerate(parts):
        ren = (tex_rename[k] if isinstance(tex_rename, (list, tuple)) else tex_rename) or {}
        psk = part.skeleton
        if not psk.flags & EULER_ROT:
            raise CharselError(f"{part.name}: only Euler skeletons are merged here")
        pw = check_bind(part)
        pmap: dict[int, int] = {}                       # part bone -> merged bone
        for i, b in enumerate(psk.bones):
            if b.name in index:
                pmap[i] = index[b.name]
                continue
            par = pmap[b.parent] if b.parent >= 0 else -1
            rel = _mul(_inv(worlds[par]), pw[i]) if par >= 0 else pw[i]
            sc, rot, tr = _decompose(rel)
            nb = Bone(b.name, par, -1, -1, b.billboard_index, b.flags & ~0x0F000000, sc, rot, tr,
                      list(b.user_data), dict(b.extra))
            bones.append(nb)
            index[b.name] = pmap[i] = len(bones) - 1
            worlds.append(_local(nb) if par < 0 else _mul(worlds[par], _local(nb)))
            info["new_bones"] += 1
        moved = {i for i in pmap if _gap(pw[i], worlds[pmap[i]]) > REST_GAP}   # (part bones resting elsewhere)
        mat0 = len(m.materials)
        for mat in part.materials:
            mm = copy.deepcopy(mat)
            mm.name = f"{PART_PREFIX}{k}_{mat.name}"
            mm.textures = [ren.get(t, t) for t in mm.textures]
            m.materials.append(mm)
        names = {s.name for s in m.shapes}
        kept = 0
        for sh in part.shapes:
            vb = part.vertex_buffers[sh.vertex_buffer]
            if vb.vertex_count <= 1:                    # (a placeholder: a part the mod shrank away)
                continue
            at = {a.name: a for a in vb.attributes}
            idx_attrs = [a for n, a in sorted(at.items()) if n.startswith("_i")]
            used: dict[int, tuple] = {}
            for a in idx_attrs:
                for v in range(vb.vertex_count):
                    for mi in _attr_values(a.format, vb.buffers[a.buffer], v * vb.strides[a.buffer] + a.offset):
                        mi = int(mi)
                        if mi not in used:
                            if mi >= len(psk.matrix_to_bone):
                                raise CharselError(f"{part.name}: a vertex names skin matrix {mi} of "
                                                   f"{len(psk.matrix_to_bone)}")
                            pb = psk.matrix_to_bone[mi]
                            used[mi] = ("s" if mi < psk.smooth_count else "r", pmap[pb], pb)
            bone_space = sh.skin_count == 0 or any(kind == "r" for kind, _b, _p in used.values())
            local_bones = {pb for _k, _b, pb in used.values()} | ({sh.bone} if sh.skin_count == 0 else set())
            if bone_space and local_bones & moved:
                info["left_out"].append(f"{part.name}/{sh.name}")
                continue
            for kind, mb, _pb in used.values():         # (a matrix for each bone, the base's when it has one)
                if kind == "s" and mb not in smooth_of and mb not in new_smooth:
                    new_smooth.append(mb)
                elif kind == "r" and mb not in rigid_of and mb not in new_rigid:
                    new_rigid.append(mb)
            nvb = copy.deepcopy(vb)
            m.vertex_buffers.append(nvb)
            pending.append((nvb, [(a, {mi: (kind, mb) for mi, (kind, mb, _p) in used.items()}) for a in idx_attrs]))
            nsh = copy.deepcopy(sh)
            nsh.vertex_buffer = len(m.vertex_buffers) - 1
            nsh.material = mat0 + sh.material
            nsh.bone = pmap[sh.bone]
            nsh.skin_bones = [pmap[b] for b in sh.skin_bones]
            if nsh.name in names:
                nsh.name = f"{PART_PREFIX}{k}_{nsh.name}"
            names.add(nsh.name)
            m.shapes.append(nsh)
            kept += 1
        info["parts"].append({"model": part.name, "shapes": kept})
        info["shapes"] += kept
    # the final matrix list: the base's smooth, the new smooth, the base's rigid, the new rigid
    ns = len(new_smooth)
    final = smooth + new_smooth + rigid + new_rigid
    if len(final) > 255:
        raise CharselError(f"{len(final)} skin matrices: more than one byte can name")
    s_at = {b: i for i, b in enumerate(smooth + new_smooth)}
    r_at = {b: s0 + ns + i for i, b in enumerate(rigid + new_rigid)}

    def write(vb, a, fn):
        sizes = formats.attr_component_sizes(a.format)
        buf = bytearray(vb.buffers[a.buffer])
        for v in range(vb.vertex_count):
            off = v * vb.strides[a.buffer] + a.offset
            vals = _attr_values(a.format, bytes(buf), off)
            for c, size in enumerate(sizes):
                new = fn(int(vals[c]))
                if new >= 1 << (8 * size):
                    raise CharselError(f"skin matrix {new} doesn't fit its {size}-byte index")
                buf[off:off + size] = new.to_bytes(size, "little")
                off += size
        vb.buffers[a.buffer] = bytes(buf)

    if ns:                                              # (the base's rigid matrices move up by the new smooth ones)
        for vb in m.vertex_buffers[:len(base.vertex_buffers)]:
            for a in vb.attributes:
                if a.name.startswith("_i"):
                    write(vb, a, lambda i: i + ns if i >= s0 else i)
    for vb, attrs in pending:
        for a, mp_ in attrs:
            write(vb, a, lambda i, mp_=mp_: s_at[mp_[i][1]] if mp_[i][0] == "s" else r_at[mp_[i][1]])
    for b in new_smooth:
        w = _inv(worlds[b])
        ibs += struct.pack("<12f", *(w[r][c] for r in range(3) for c in range(4)))
    for b in bones:
        b.smooth_index = b.rigid_index = -1
    for i, b in enumerate(final):
        if i < s0 + ns:
            bones[b].smooth_index = i
        else:
            bones[b].rigid_index = i
    m.skeleton = Skeleton(sk.flags, bones, final, s0 + ns, len(rigid) + len(new_rigid), bytes(ibs), dict(sk.extra))
    m.total_vertex_count = sum(vb.vertex_count for vb in m.vertex_buffers)
    info.update(bones=len(bones), matrices=len(final), vertices=m.total_vertex_count)
    return m, info


# Skin drawn with an NPC's material (2026-11-15). The game check: Linkle's skin came out strong orange-yellow while
# her hair, top and shorts looked right, and the imported avatar's skin lime green while her dress looked right. The
# wrong ones all drew with Link's PLAYER materials (Linkle's skin: priority hint "player", the player's own skin
# colour calculation; the avatar: Link's underwear and eyelashes materials); every costume that looks right draws with
# an NPC's own (Zelda, Urbosa, Mipha...) [V: in-game shots]. Why the player's skin goes wrong on another actor isn't
# known [G]. So such skin now draws with Zelda's skin material (Npc_Zelda_Miko Mt_Skin, the game's file on this PC),
# its textures pointed at the costume's own.
NPC_SKIN_MODEL, NPC_SKIN_MAT = "Npc_Zelda_Miko", "Mt_Skin"
NPC_SKIN_ROLES = {"_a0": ("_a0",), "_a1": ("_gn0", "_a1"), "_n0": ("_n0",), "_s0": ("_s0",), "_ao0": ("_ao0",)}
FLAT_DAMAGE, FLAT_AO = "SwFlat_Damage_Alb", "SwFlat_AO"


def _hint(mat) -> str:
    return next((str(r.values[0]) for r in mat.render_infos if r.name == "gsys_priority_hint" and r.values), "")


def npc_skin(romfs) -> tuple[object, dict[str, object]]:
    """(Zelda's skin material, {texture name: Texture} of its texture file, as the flat fill-ins' templates) from
    the game's files."""
    import copy
    from wiiu_model import nx_bfres, nx_model
    raw, tex = model_file(Path(romfs), NPC_SKIN_MODEL), model_file(Path(romfs), NPC_SKIN_MODEL + ".Tex")
    if not raw or not tex:
        raise CharselError(f"the game's {NPC_SKIN_MODEL} model isn't in your files")
    mats = {m.name: m for mo in nx_model.read(_unyaz(raw)).models for m in mo.materials}
    if NPC_SKIN_MAT not in mats:
        raise CharselError(f"{NPC_SKIN_MODEL} has no {NPC_SKIN_MAT} (other game version?)")
    m = copy.deepcopy(mats[NPC_SKIN_MAT])
    if [s.name for s in m.samplers] != list(NPC_SKIN_ROLES):
        raise CharselError(f"{NPC_SKIN_MODEL}'s {NPC_SKIN_MAT} has samplers {[s.name for s in m.samplers]}")
    return m, {t.name: t for t in nx_bfres.read_textures(_unyaz(tex)).textures}


def flat_texture(tpl, name: str, value: int):
    """A 4x4 texture of one value in tpl's format (BC1: grey value, BC4: value; others refused), one level."""
    import copy
    import struct
    kind = tpl.format >> 8
    if kind == 0x1a:
        v5, v6 = round(value * 31 / 255), round(value * 63 / 255)
        c = (v5 << 11) | (v6 << 5) | v5
        block = struct.pack("<HHI", c, c, 0)
    elif kind == 0x1d:
        block = bytes((value, value)) + bytes(6)
    else:
        raise CharselError(f"no flat texture in format {tpl.format:#x}")
    t = copy.deepcopy(tpl)
    t.name, t.width, t.height, t.levels = name, 4, 4, [block]
    t.extra.pop("nx.block_height_log2", None)
    return t


def flat_skin_fills(tex_of: dict) -> list:
    """The two fill-ins npc_skin_materials may point at: no damage (white) and no ambient shade (white)."""
    return [flat_texture(tex_of["Npc_Zelda_Skin_Damage_Alb"], FLAT_DAMAGE, 255),
            flat_texture(tex_of["Npc_Zelda_Skin_AO"], FLAT_AO, 255)]


def ensure_uv1(vb) -> bool:
    """Give a vertex buffer a second texture coordinate (_u1, a copy of _u0, in a buffer of its own) when it has none:
    the NPC skin material reads it (its shade map). Returns whether one was added."""
    names = {a.name for a in vb.attributes}
    if "_u1" in names or "_u0" not in names:
        return False
    from wiiu_model import formats
    from wiiu_model.neutral import Attribute
    a = next(x for x in vb.attributes if x.name == "_u0")
    size = sum(formats.attr_component_sizes(a.format))
    src, stride = vb.buffers[a.buffer], vb.strides[a.buffer]
    data = b"".join(src[v * stride + a.offset:v * stride + a.offset + size] for v in range(vb.vertex_count))
    vb.attributes.append(Attribute("_u1", a.format, 0, len(vb.buffers)))
    vb.buffers.append(data)
    vb.strides.append(size)
    return True


def npc_skin_materials(model, tmpl, pick=lambda m: _hint(m) == "player") -> list[str]:
    """Every material pick() takes (default: the player's own, priority hint "player") becomes a copy of tmpl (Zelda's
    skin material) under its own name and hide groups, its samplers pointed at the material's own textures by role
    (damage: _gn0; none: FLAT_DAMAGE / FLAT_AO); the shapes drawn with one get _u1. Returns the materials changed."""
    import copy
    done = []
    for i, m in enumerate(model.materials):
        if not pick(m):
            continue
        own = {s.name: t for s, t in zip(m.samplers, m.textures)}
        new = copy.deepcopy(tmpl)
        new.name, new.user_data = m.name, copy.deepcopy(m.user_data)
        new.textures = []
        for s in new.samplers:
            t = next((own[r] for r in NPC_SKIN_ROLES.get(s.name, ()) if r in own), None)
            new.textures.append(t or (FLAT_AO if s.name == "_ao0" else FLAT_DAMAGE))
        model.materials[i] = new
        done.append(m.name)
        for sh in model.shapes:
            if sh.material == i:
                ensure_uv1(model.vertex_buffers[sh.vertex_buffer])
    return done


# Legs for Zelda (2026-11-15, the user's pick after the game check: "zelda_run": her sandals floated under the
# dress, no legs). Her model (Npc_Zelda_Miko) has skin for the arms, the neck, the feet and a few strips, nothing from
# the ankle to the hip, where her long dress swings open when she runs [V: its skin vertices by height]. The user
# asked for a Hylian woman's legs, but no woman's body in the game has bare knees or thighs: every UMii_*_Body*_W_*
# has skin only up to mid-shin (UMii_Hylia_BodyC_W_005: ankle to 0.45 m once fitted) [V: survey 2026-11-15]. The
# slim Hylian body that does is Link's own bare legs (the game's Armor_Default_Lower, under his shorts: calves, knees
# and the lower thighs, 0.10-0.75 m once fitted to her). Each vertex is moved from the donor's leg bones onto Zelda's
# (her bones' rest turns, stretched along each bone to her lengths: her knees sit higher), coloured as her own skin
# (the donor's skin texture tinted to her skin texture's average), drawn with her own skin material, and merged into a
# copy of her model (Model/SwChar_zelda.sbfres, her textures beside it). His feet are left out (she has her own and
# her sandals). Her dress, hair and cloth physics stay as they are.
LEGS_DONOR = ("Armor_Default", "Armor_Default_Lower")    # (model file, model in it)
LEGS_TEX_FILES = ("Armor_Default.Tex", "Link.Tex")       # where its skin material's textures are (in this order)
LEGS_KEEP = ("Leg_1", "Leg_2")           # a triangle is kept when a corner is mostly on these (thigh, calf)...
LEGS_DROP = ("Toe",)                     # ...and none on these (his feet: she has her own)
LEGS_NEXT = {"Leg_1": "Leg_2", "Leg_2": "Ankle", "Ankle": "Toe"}
LEGS_TEX = ("SwLegs_Alb", "SwLegs_Nrm", "SwLegs_Spm")


def _s10(v: int) -> float:
    return (v - 1024 if v & 512 else v) / 511


def fit_legs(donor, base, keep=LEGS_KEEP, drop=LEGS_DROP) -> tuple[list, list, list, list, list[int], dict]:
    """The donor body's leg skin moved onto `base`'s leg bones: (positions, normals, uvs, [(4 BASE smooth-matrix
    indices, 4 u8 weights)], triangle corner list, info). Only the skin material's shapes; a triangle is kept when a
    corner weighs most on a `keep` bone and none on a `drop` one (the feet). A donor bone the base lacks (Link's knee
    and ankle helpers) counts as its nearest ancestor the base has. Each vertex is moved by its bones' fits (base world
    . stretch along the bone to the base's length . donor world^-1), blended by weight; only the shape's skin_count
    influences are read (a 4th byte past them is padding)."""
    import math
    import struct
    dsk, bsk = donor.skeleton, base.skeleton
    dn, bn = {b.name: i for i, b in enumerate(dsk.bones)}, {b.name: i for i, b in enumerate(bsk.bones)}
    dw, bw = _worlds(dsk.bones), _worlds(bsk.bones)

    def as_base(i):              # (donor bone -> the donor bone of the same name as its base target)
        while i >= 0 and dsk.bones[i].name not in bn:
            i = dsk.bones[i].parent
        return i

    def smooth_of(j):            # (base bone -> its smooth matrix, or its nearest ancestor's)
        while j >= 0 and bsk.bones[j].smooth_index < 0:
            j = bsk.bones[j].parent
        return max(0, bsk.bones[j].smooth_index) if j >= 0 else 0
    fits: dict[int, tuple] = {}
    stretch = {}
    for i in range(len(dsk.bones)):
        d = as_base(i)
        if d < 0:
            continue
        name = dsk.bones[d].name
        j = bn[name]
        side = name[-2:] if name[-2:] in ("_L", "_R") else ""
        stem = name[:-2] if side else name
        k, axis = 1.0, (1.0, 0.0, 0.0)
        nxt = LEGS_NEXT.get(stem)
        if nxt and nxt + side in dn and nxt + side in bn:
            dv = [dw[dn[nxt + side]][r][3] - dw[d][r][3] for r in range(3)]
            dl = math.sqrt(sum(c * c for c in dv))
            bl = math.dist([bw[j][r][3] for r in range(3)], [bw[bn[nxt + side]][r][3] for r in range(3)])
            if dl > 1e-4:
                k = bl / dl
                loc = [sum(dw[d][r][c] * dv[r] for r in range(3)) / dl for c in range(3)]   # (in the bone's frame)
                n = math.sqrt(sum(c * c for c in loc)) or 1.0
                axis = tuple(c / n for c in loc)
            stretch[name] = round(k, 3)
        sc = [[(1.0 if r == c else 0.0) + (k - 1.0) * axis[r] * axis[c] for c in range(3)] + [0.0] for r in range(3)]
        sc.append([0.0, 0.0, 0.0, 1.0])
        fits[i] = (_mul(_mul(bw[j], sc), _inv(dw[d])), _mul(bw[j], _inv(dw[d])), j, name)
    pos, nrm, uv, infl, tris = [], [], [], [], []
    stems = lambda names: {n + s for n in names for s in ("_L", "_R")}      # noqa: E731
    keep_n, drop_n = stems(keep), stems(drop)
    vmap: dict[tuple, int] = {}
    for sh in donor.shapes:
        if "Skin" not in donor.materials[sh.material].name or sh.skin_count == 0:
            continue
        vb = donor.vertex_buffers[sh.vertex_buffer]
        at = {a.name: a for a in vb.attributes}
        if not {"_p0", "_n0", "_u0", "_i0", "_w0"} <= set(at):
            continue
        sc_n = sh.skin_count

        def val(a, v):
            return _attr_values(a.format, vb.buffers[a.buffer], v * vb.strides[a.buffer] + a.offset)

        def infl_of(v):
            ix, w = val(at["_i0"], v)[:sc_n], val(at["_w0"], v)[:sc_n]
            return [(dsk.matrix_to_bone[int(m)], wt) for m, wt in zip(ix, w) if wt > 0]
        main: dict[int, str] = {}

        def main_name(v):
            if v not in main:
                inf = infl_of(v)
                b = max(inf, key=lambda x: x[1])[0] if inf else -1
                main[v] = fits[b][3] if b in fits else ""
            return main[v]
        m0 = sh.meshes[0]
        if m0.primitive != "triangles":
            continue
        size = {"u8": 1, "u16": 2, "u32": 4}[m0.index_format]
        idx = list(struct.unpack_from(f"<{m0.index_count}{'BHI'[size // 2]}", m0.indices))
        for t in range(0, len(idx) - 2, 3):
            tri = [idx[t] + m0.first_vertex, idx[t + 1] + m0.first_vertex, idx[t + 2] + m0.first_vertex]
            names = [main_name(v) for v in tri]
            if not any(n in keep_n for n in names) or any(n in drop_n for n in names):
                continue
            for v in tri:
                key = (sh.vertex_buffer, v)
                if key not in vmap:
                    p = val(at["_p0"], v)[:3]
                    nw = struct.unpack_from("<I", vb.buffers[at["_n0"].buffer],
                                            v * vb.strides[at["_n0"].buffer] + at["_n0"].offset)[0]
                    n = (_s10(nw & 1023), _s10((nw >> 10) & 1023), _s10((nw >> 20) & 1023))
                    q, qn, tot = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 0.0
                    mw: dict[int, float] = {}
                    for b, wt in infl_of(v):
                        if b not in fits:
                            continue
                        f, r_, j, _n = fits[b]
                        for r in range(3):
                            q[r] += wt * (sum(f[r][c] * p[c] for c in range(3)) + f[r][3])
                            qn[r] += wt * sum(r_[r][c] * n[c] for c in range(3))
                        tot += wt
                        m = smooth_of(j)
                        mw[m] = mw.get(m, 0.0) + wt
                    if tot <= 0:
                        raise CharselError(f"{donor.name}: a leg vertex has no bone the base has")
                    ln = math.sqrt(sum(c * c for c in qn)) or 1.0
                    vmap[key] = len(pos)
                    pos.append(tuple(c / tot for c in q))
                    nrm.append(tuple(c / ln for c in qn))
                    uv.append(tuple(val(at["_u0"], v)[:2]))
                    top = sorted(mw.items(), key=lambda kv: -kv[1])[:4]
                    wq = [round(255 * w_ / tot) for _m, w_ in top]
                    wq[0] += 255 - sum(wq)
                    ixs = [m for m, _w in top]
                    infl.append((ixs + [ixs[0]] * (4 - len(ixs)), wq + [0] * (4 - len(wq))))
                tris.append(vmap[key])
    if not tris:
        raise CharselError(f"{donor.name} has no leg skin on {', '.join(keep)}")
    ys = [p[1] for p in pos]
    return pos, nrm, uv, infl, tris, {"donor": donor.name, "vertices": len(pos), "triangles": len(tris) // 3,
                                      "stretch": stretch, "y": (round(min(ys), 3), round(max(ys), 3))}


def _shown(p) -> bool:
    return any(p.vertex_buffers[s.vertex_buffer].vertex_count > 1 for s in p.shapes)   # (not all shrunk away)


def outfit_parts(outfit_models, look_models, have_tex) -> tuple[list, list, list[str]]:
    """Linkle v3's parts: (the outfit's models, the look's models, what was left out and why). Each outfit model
    keeps its shapes but the hood-up ones (named in LINKLE_HOOD_UP) and those naming a texture the mod doesn't ship
    (have_tex: every texture name the costume has; e.g. its "greenshirt"); a <x>_B model is left out when <x>_A is
    there (the head's second copy: the same shapes). From the look only her pouches (LINKLE_FROM_LOOK: its hair would
    double Head_A's, its shorts poke through the skirt). Models are shallow copies, with only the materials (renumbered)
    their kept shapes use."""
    import copy
    have = set(have_tex)
    left: list[str] = []
    outfit = []
    names = {p.name for p in outfit_models}
    for p in outfit_models:
        if p.name.endswith("_B") and p.name[:-1] + "A" in names:
            left.append(f"{p.name}: a second copy of {p.name[:-1]}A")
            continue
        keep = []
        for sh in p.shapes:
            if p.vertex_buffers[sh.vertex_buffer].vertex_count <= 1:
                continue
            mat = p.materials[sh.material]
            missing = [t for t in mat.textures if t not in have]
            if sh.name in LINKLE_HOOD_UP:
                left.append(f"{p.name}/{sh.name}: hood up")
            elif missing:
                left.append(f"{p.name}/{sh.name}: no texture {missing[0]}")
            else:
                keep.append(sh)
        if keep:   # (only the materials its kept shapes use: a left-out one names textures that aren't there)
            mats = sorted({sh.material for sh in keep})
            q = copy.copy(p)
            q.materials = [p.materials[i] for i in mats]
            q.shapes = [copy.copy(sh) for sh in keep]
            for sh in q.shapes:
                sh.material = mats.index(sh.material)
            outfit.append(q)
    look = [p for p in look_models if p.name in LINKLE_FROM_LOOK and _shown(p)]
    return outfit, look, left


def linkle_costume(where, member) -> tuple[bytes, bytes, dict]:
    """(model, textures, info) of the Linkle costume, from the Linkle Mod's own files (where/member: linkle_mod's):
    its Link model wearing her outfit (v3: LINKLE_OUTFIT, hood down, plus her hair and pouches from Armor_Default),
    and its Link textures with the outfit's and the look's beside them. A mod without the outfit file: her default
    look (v2: all of Armor_Default), said in info["look"]. Yaz0-packed, like the game's .sbfres. Read only."""
    import copy
    from wiiu_model import checks, nx_bfres, nx_model
    from wiiu_model.convert import set_texinfo
    from wiiu_model.neutral import ResFile
    where = Path(where)
    body, body_tex = _read_link_model(where, member), _read_link_model(where, member, tex=True)
    look, look_tex = _read_mod_model(where, member, LINKLE_LOOK), _read_mod_model(where, member, LINKLE_LOOK + ".Tex")
    fit = _read_mod_model(where, member, LINKLE_OUTFIT)
    fit_tex = _read_mod_model(where, member, LINKLE_OUTFIT + ".Tex") if fit else None
    if not body or not body_tex:
        raise CharselError("the Linkle Mod's Link model or textures aren't in its files")
    if not look or not look_tex:
        raise CharselError(f"the Linkle Mod has no {LINKLE_LOOK} (her hair and clothes)")
    res = nx_model.read(_unyaz(body))
    if len(res.models) != 1:
        raise CharselError(f"the Linkle Mod's Link model file has {len(res.models)} models, expected 1")
    look_models = nx_model.read(_unyaz(look)).models
    tex_raw, tex_names, rename = merge_textures(body_tex, look_tex)
    if fit and fit_tex:   # (have: texture names as the parts' materials say them)
        have = set(tex_names) | set(rename) | {t.name for t in nx_bfres.read_textures(_unyaz(fit_tex)).textures}
        outfit, keep, left = outfit_parts(nx_model.read(_unyaz(fit)).models, look_models, have)
        if not outfit:
            raise CharselError(f"the Linkle Mod's {LINKLE_OUTFIT} has nothing to wear ({left[:3]})")
        used = {t for p in outfit for sh in p.shapes for t in p.materials[sh.material].textures}
        tex_raw, tex_names, fit_rename = merge_textures(tex_raw, fit_tex, OUTFIT_PREFIX, keep=used)
        parts, renames, how = outfit + keep, [fit_rename] * len(outfit) + [rename] * len(keep), "outfit"
    else:
        parts = [p for p in look_models if _shown(p)]
        renames, left, how = rename, [], "default look"
    model, info = merge_parts(res.models[0], parts, renames)
    info["left_out"] = left + info["left_out"]
    info["look"] = how
    out = ResFile(res.name, [model], [], [], [copy.deepcopy(e) for e in res.external], dict(res.extra))
    set_texinfo(out, len(tex_names))
    raw = nx_model.write(out)
    bad = checks.switch_model_problems(raw)
    if bad:
        raise CharselError(f"the Linkle costume model fails the self-check: {bad[:3]}")
    info.update(textures=len(tex_names), texture_renames=rename)
    return _yaz(raw), _yaz(tex_raw), info


def tinted_texture(src_tex, like_tex, name: str, max_side: int = 512):
    """src_tex (a BC1 colour texture) recoloured so its average is like_tex's, as a new BC1 sRGB texture with mips
    (Pillow); both read with menu_bg's decoder."""
    import menu_bg
    import vrm_import as vi
    Image = menu_bg.pillow()
    if Image is None:
        raise CharselError("Zelda's legs need Pillow for their texture (pip install pillow)")

    def rgba(t):
        if t.format >> 8 != 0x1a:
            raise CharselError(f"{t.name} isn't a BC1 texture")
        return Image.frombytes("RGBA", (t.width, t.height), bytes(menu_bg.decode_bc1(t.levels[0], t.width, t.height)))

    def mean(img):
        small = img.convert("RGB").resize((1, 1), 2)
        return small.getpixel((0, 0))
    img, like = rgba(src_tex), rgba(like_tex)
    if max(img.size) > max_side:
        f = max_side / max(img.size)
        img = img.resize((max(4, round(img.width * f)), max(4, round(img.height * f))), 1)
    ms, ml = mean(img), mean(like)
    r, g, b, a = img.split()
    chans = [c.point([max(0, min(255, round(v * ml[k] / max(1, ms[k])))) for v in range(256)])
             for k, c in enumerate((r, g, b))]
    out = Image.merge("RGBA", chans + [Image.new("L", img.size, 255)])
    return vi._texture(src_tex, name, out, False), {"donor_mean": ms, "her_mean": ml}


def zelda_legs(romfs, folder: str) -> tuple[bytes, bytes, dict]:
    """(model, textures, info): Zelda's game model with bare legs (LEGS_DONOR's, fitted to her) under her dress, named
    as Model/<folder> (own_names). Game files only, read only."""
    import copy
    import struct
    import vrm_import as vi
    from wiiu_model import checks, nx_bfres, nx_model
    from wiiu_model.convert import set_texinfo
    from wiiu_model.neutral import Mesh, Model, ResFile, TextureSet
    romfs = Path(romfs)
    zraw, ztex = model_file(romfs, NPC_SKIN_MODEL), model_file(romfs, NPC_SKIN_MODEL + ".Tex")
    draw = model_file(romfs, LEGS_DONOR[0])
    if not (zraw and ztex and draw):
        raise CharselError(f"the game's {NPC_SKIN_MODEL} or {LEGS_DONOR[0]} model isn't in your files")
    zres = nx_model.read(_unyaz(zraw))
    if len(zres.models) != 1:
        raise CharselError(f"{NPC_SKIN_MODEL} has {len(zres.models)} models, expected 1")
    zelda = zres.models[0]
    donor = next((m for m in nx_model.read(_unyaz(draw)).models if m.name == LEGS_DONOR[1]), None)
    if donor is None:
        raise CharselError(f"the game's {LEGS_DONOR[0]} has no {LEGS_DONOR[1]}")
    pos, nrm, uv, infl, tris, info = fit_legs(donor, zelda)
    zts = nx_bfres.read_textures(_unyaz(ztex))
    zt = {t.name: t for t in zts.textures}
    dmat = next(m for m in donor.materials if "Skin" in m.name)
    dt: dict = {}
    for f in reversed(LEGS_TEX_FILES):      # (the first file listed wins a name)
        data = model_file(romfs, f)
        if data:
            dt.update({t.name: t for t in nx_bfres.read_textures(_unyaz(data)).textures})
    own = {s.name: t for s, t in zip(dmat.samplers, dmat.textures)}
    if any(own.get(s) not in dt for s in ("_a0", "_n0", "_s0")):
        raise CharselError(f"{LEGS_DONOR[1]}'s skin textures aren't in {' or '.join(LEGS_TEX_FILES)}")
    zmat = next(m for m in zelda.materials if m.name == NPC_SKIN_MAT)
    zown = {s.name: t for s, t in zip(zmat.samplers, zmat.textures)}
    alb, tint = tinted_texture(dt[own["_a0"]], zt[zown["_a0"]], LEGS_TEX[0])
    nrm_t, spm_t = copy.deepcopy(dt[own["_n0"]]), copy.deepcopy(dt[own["_s0"]])
    nrm_t.name, spm_t.name = LEGS_TEX[1], LEGS_TEX[2]
    extra = [alb, nrm_t, spm_t] + flat_skin_fills(zt)
    mat = copy.deepcopy(zmat)
    mat.name = "SwLegs_" + NPC_SKIN_MAT
    mat.textures = [{"_a0": LEGS_TEX[0], "_a1": FLAT_DAMAGE, "_n0": LEGS_TEX[1], "_s0": LEGS_TEX[2],
                     "_ao0": FLAT_AO}[s.name] for s in mat.samplers]
    vb = vi.vertex_buffer(pos, nrm, uv, infl)
    tpl = next(s for s in zelda.shapes if zelda.materials[s.material].name == NPC_SKIN_MAT)
    sh = copy.deepcopy(tpl)
    sh.name, sh.material, sh.vertex_buffer, sh.bone = "SwLegs__" + NPC_SKIN_MAT, 0, 0, 0
    sh.skin_count, sh.key_shapes = 4, []
    sh.skin_bones = sorted({zelda.skeleton.matrix_to_bone[i] for ix, _w in infl for i in ix})
    sh.meshes = [Mesh("triangles", "u16", len(tris), struct.pack(f"<{len(tris)}H", *tris), 0, [(0, len(tris))])]
    box = (0.0, 0.5, 0.0, 0.6, 0.6, 0.6)
    sh.bounds, sh.radii = [box, box], [1.1]
    # (the part is skinned to Zelda's own matrices, so it rides on her skeleton: no new bones)
    part = Model(LEGS_DONOR[1], donor.path, copy.deepcopy(zelda.skeleton), [vb], [sh], [mat], [], vb.vertex_count, {})
    model, minfo = merge_parts(zelda, [part])
    info.update(tint=tint, new_bones=minfo["new_bones"], bones=minfo["bones"])
    ts = TextureSet(zts.name, zts.textures + extra, "model", dict(zts.extra))
    out = ResFile(zres.name, [model], [], [], [copy.deepcopy(e) for e in zres.external], dict(zres.extra))
    set_texinfo(out, len(ts.textures))
    raw = nx_model.write(out)
    bad = checks.switch_model_problems(raw)
    if bad:
        raise CharselError(f"Zelda's model with legs fails the self-check: {bad[:3]}")
    m, t, _ = own_names(_yaz(raw), _yaz(nx_bfres.write_textures(ts)), folder)
    return m, t, info


def repoint_model_list(model_list: bytes, old: str, new: str) -> tuple[bytes, int]:
    """A model list whose ModelData folders and units named `old` name `new`. Returns (the file, how many changed)."""
    import aamp
    from aamp import Param, name_hash
    ml = aamp.load(model_list)
    md = ml.list("ModelData")
    n = 0
    for entry in (md.lists.values() if md else ()):
        base = entry.object("Base")
        f = base.get(name_hash("Folder")) if base else None
        if f is not None and f.value == old:
            base[name_hash("Folder")] = Param(f.type, new)
            n += 1
        ul = entry.lists.get(name_hash("Unit"))
        for o in (ul.objects.values() if ul else ()):
            p = o.get(name_hash("UnitName"))
            if p is not None and p.value == old:
                o[name_hash("UnitName")] = Param(p.type, new)
                n += 1
    return aamp.dump(ml), n


# ------------------------------------------------------------------------------------------------ the game's files

def _unyaz(d: bytes) -> bytes:
    from wiiu_convert import yaz0_decompress
    return yaz0_decompress(d)[0] if d[:4] == b"Yaz0" else d


def bfres_clips(data: bytes) -> list[str]:
    """The skeletal clip names of a Switch .bfres (its dictionary order)."""
    import struct
    from wiiu_model import dicts, strpool
    data = _unyaz(data)
    if data[:4] != b"FRES":
        raise CharselError("not a Switch .bfres")
    counts = struct.unpack_from("<7H", data, 0xBC)
    if not counts[1]:
        return []
    _, keys = dicts.read_nx_dict(data, struct.unpack_from("<Q", data, 0x40)[0])
    return [strpool.nx_string(data, keys[i]) for i in range(counts[1])]


def model_file(romfs: Path, name: str) -> bytes | None:
    """Model/<name>.sbfres from the game's files (loose, or inside Pack/TitleBG.pack)."""
    from mp import ghost_link_pack as glp
    p = Path(romfs) / "Model" / f"{name}.sbfres"
    if p.is_file():
        return p.read_bytes()
    t = Path(romfs) / "Pack" / "TitleBG.pack"
    return glp._sarc_member(t, f"Model/{name}.sbfres") if t.is_file() else None


class ClipCache:
    """Clip names per animation file, kept in a small JSON file (names only) keyed by the file's size and time, so a
    second build doesn't unpack the files again."""

    def __init__(self, romfs: Path, path: Path | None = None):
        self.romfs, self.path = Path(romfs), Path(path) if path else None
        self.data: dict = {}
        if self.path and self.path.is_file():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    def clips(self, name: str) -> set[str] | None:
        p = self.romfs / "Model" / f"{name}.sbfres"
        key = f"{name}|{p.stat().st_size}|{int(p.stat().st_mtime)}" if p.is_file() else f"{name}|titlebg"
        if key not in self.data:
            d = model_file(self.romfs, name)
            if d is None:
                return None
            self.data[key] = bfres_clips(d)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data), encoding="utf-8")
        return set(self.data[key])


def read_actorinfo(romfs: Path) -> dict[str, dict]:
    """{actor name: {"profile", "instSize"}} for the free names only, from the game's own ActorInfo."""
    import bcml_data
    doc = bcml_data.byml_load((Path(romfs) / "Actor" / "ActorInfo.product.sbyml").read_bytes())
    out = {}
    for a in doc.get("Actors", []):
        name = str(a.get("name", ""))
        if name in FREE_SLOTS:
            out[name] = {"profile": str(a.get("profile", "")), "instSize": int(a.get("instSize", 0) or 0)}
    return out


def check_slot(romfs: Path, slot: str, info: dict | None) -> tuple[str, list[str]]:
    """(the slot's profile, problems): it must be a free name of the game's: in ActorInfo with a profile we use, a
    heap of at least 500 KB, and no pack in the game's files."""
    from mp import ghost_link_pack as glp
    bad = []
    profile = SLOT_PROFILE.get(slot, "DemoNPC")
    if info is not None:
        a = info.get(slot)
        if a is None:
            bad.append(f"{slot} isn't in the game's ActorInfo")
        else:
            profile = a["profile"] or profile
            if profile not in ("DemoNPC", "NPC"):
                bad.append(f"{slot} is a {profile}, not a person")
            if a["instSize"] < 500_000:
                bad.append(f"{slot} has a heap of {a['instSize']:,} bytes (under 500 KB)")
    try:
        glp.find_pack(Path(romfs), slot)
        bad.append(f"{slot} has a pack in the game: it isn't free")
    except glp.PackError:
        pass
    return profile, bad


# ------------------------------------------------------------------------------------------------ making a pack

def ours(cid: str) -> str:
    """Our file names inside a pack (model list, animation list, animation sets): SwChar_<id>."""
    return "SwChar_" + cid.replace("-", "_")


def clip_map(r: Recipe, rest: str | None = None) -> dict[str, str]:
    """{ghost name: clip}: the character's own, then (rest "link") Link's clip for every other ghost name."""
    from mp import ghost_link_pack as glp
    link = {n: c for n, c, _f in glp.ANIMS}
    unknown = [n for n, _ in r.own if n not in link and n not in EXTRA_NAMES]
    if unknown:
        raise CharselError(f"{r.id}: {', '.join(unknown)} isn't a ghost name")
    m = dict(link) if (rest or r.rest) == "link" or r.kind != "pack" else {}
    m.update(r.own)
    return m


def swing_start(start, rate) -> float | None:
    """A monster swing's start frame with the lead (SWING_LEAD_S of game time, in clip frames at its rate)."""
    if start is None:
        return None
    return float(round(start + SWING_LEAD_S * CLIP_FPS * (rate or 1.0)))


def frame_ctrl(r: Recipe) -> dict[str, tuple]:
    """{ghost name: (rate, start frame, end frame)} for the names whose set doesn't play its clip as it is. An attack's
    start frame gets the lead (swing_start)."""
    m = clip_map(r)
    bad = [c[0] for c in r.ctrl if c[0] not in m]
    if bad:
        raise CharselError(f"{r.id}: frame control for {', '.join(bad)}, which it has no clip for")
    return {n: (rate, swing_start(start, rate) if is_attack(n) else start, end) for n, rate, start, end in r.ctrl}


def as_file(clip: str, rate=None, start=None, end=None, morph=None) -> bytes:
    """An animation set with one clip node (ghost_link_pack.as_file), and the game's own frame control on the node
    when given: Extend > FrameCtrl > Rate / StartFrame / EndFrame, as Link's Player_CutS1 has it [V]. morph: the node's
    Morph (blend-in frames; Link's attack nodes have 0 [V])."""
    import aamp
    from aamp import F32, INT, STRING64, PList, PObject, name_hash
    from mp import ghost_link_pack as glp
    if rate is None and start is None and end is None and morph is None:
        return glp.as_file(clip)
    params = PObject()
    params.set("TypeIndex", INT, 67)
    params.set("FileName", STRING64, clip)
    if morph is not None:
        params.set("Morph", F32, float(morph))
    if rate is None and start is None and end is None:
        element = PList(objects={name_hash("Parameters"): params})
        return aamp.dump(PList(lists={name_hash("Elements"): PList(lists={name_hash("Element0"): element})}))
    fc = PObject()
    for key, v in (("Rate", rate), ("StartFrame", start), ("EndFrame", end)):
        if v is not None:
            fc.set(key, F32, float(v))
    ext = PList(lists={name_hash("FrameCtrl"): PList(objects={FRAME_CTRL: fc})})
    element = PList(objects={name_hash("Parameters"): params}, lists={name_hash("Extend"): ext})
    return aamp.dump(PList(lists={name_hash("Elements"): PList(lists={name_hash("Element0"): element})}))


def as_ctrl(bas: bytes) -> tuple:
    """(rate, start frame, end frame) of a one-clip set (None: not set)."""
    import aamp
    from aamp import name_hash
    el = aamp.load(bas).list("Elements").list("Element0")
    ext = el.lists.get(name_hash("Extend"))
    fcl = ext.lists.get(name_hash("FrameCtrl")) if ext else None
    fc = fcl.objects.get(FRAME_CTRL) if fcl else None
    return tuple(fc.get_value(k) if fc else None for k in ("Rate", "StartFrame", "EndFrame"))


def anim_files(r: Recipe, rest: str | None = None) -> list[tuple[str, bool]]:
    files = list(r.anims)
    if (rest or r.rest) == "link" and all(f != PLAYER_ANIM for f, _ in files):
        files.append((PLAYER_ANIM, True))
    if (rest or r.rest) != "link":
        files = [(f, rt) for f, rt in files if f != PLAYER_ANIM]
    return files


def resolve(r: Recipe, clips_of, rest: str | None = None) -> dict:
    """Where each clip comes from: {"own", "link", "missing", "clash"}. clips_of(file) gives a file's clip names, or
    None to trust it (Link's file: its 33 clips play on the Link body in the game [V]; trusted for those only)."""
    from mp import ghost_link_pack as glp
    link_clips = {c for _n, c, _f in glp.ANIMS}
    m = clip_map(r, rest)
    files = anim_files(r, rest)
    own = dict(r.own)
    sets = {f: clips_of(f) for f, _ in files}
    res = {"own": {}, "link": {}, "missing": [], "clash": []}
    for name, clip in m.items():
        hit = None
        for f, _ in files:
            s = sets[f]
            if (s is None and name not in own and clip in link_clips) or (s is not None and clip in s):
                hit = f
                break
        if hit is None:
            res["missing"].append(f"{name}->{clip}")
            continue
        if name not in own and hit != PLAYER_ANIM:
            res["clash"].append(f"{clip} is found first in {hit}")
        (res["link"] if hit == PLAYER_ANIM else res["own"])[name] = clip
    return res


# The animation targets (the model list's AnmTarget block). This is why Zelda, Urbosa, Daruk, Revali and Mipha were
# never created ("the game could not create it", two in-game checks, 2026-11-12 and -13, on 6 different free names,
# with and without Link's file retargeted). Their donors' lists (Dm_Npc_Zelda_Sibyl, Dm_Npc_*_Hero) have 4 targets of
# types 0, 0, 3, 2; every pack that loads has 0, 1, 3 (Paya, the Hylian, the Link body) or 0, 0 / 0 (the monsters).
# A type-2 target is set up apart from the others (the game counts them in a pool of their own when it sizes an
# actor's animations) and the game only ever has one with a look file and an animation info: all 132 of its NPC packs
# with a type-2 target have both [V: game packs read], and our packs give neither (UMii and AnimationInfo Dummy). So a
# list with a type-2 target gets the Link body's block instead (the template's, the Sheikah man's: 0, 1, 3 on Root,
# Spine_1 and Face_Root, which these skeletons have): the same block Paya's own list has, proven in the game. The face
# keeps still (no type-2 target: the game's own Paya pack has none either) [G].
SWAP_TARGET_TYPES = (2,)   # the animation target types we never ship


def target_types(ml) -> list:
    """The TargetType of each animation target of a model list (a PList), in order."""
    at = ml.list("AnmTarget")
    return [t.object("Base").get_value("TargetType") if t.object("Base") else None
            for t in (at.lists.values() if at else ())]


def target_bones(ml) -> set[str]:
    """The bones a model list's animation targets name (their partial lists)."""
    at = ml.list("AnmTarget")
    out = set()
    for t in (at.lists.values() if at else ()):
        part = t.list("Partial")
        for o in (part.objects.values() if part else ()):
            b = o.get_value("Bone")
            if isinstance(b, str) and b:
                out.add(b)
    return out


def body_targets(model_list: bytes, template: dict[str, bytes], bones=None) -> tuple[bytes, bool]:
    """(the model list, swapped): a donor's model list with a type-2 animation target gets the template's (the Link
    body's) block of targets; any other list stays byte for byte. bones: the body's bone names, when known (the
    template's targets must find theirs in it)."""
    import aamp
    from aamp import name_hash
    from mp import ghost_link_pack as glp
    ml = aamp.load(model_list)
    if not any(t in SWAP_TARGET_TYPES for t in target_types(ml)):
        return model_list, False
    _, lt = glp.link_target(template, TEMPLATE)
    tml = template.get(f"Actor/ModelList/{glp.user(lt, 'ModelUser')}.bmodellist")
    key = name_hash("AnmTarget")
    tpl = aamp.load(tml) if tml else None
    if tpl is None or key not in tpl.lists or any(t in SWAP_TARGET_TYPES for t in target_types(tpl)):
        raise CharselError(f"{TEMPLATE}'s model list has no animation targets to copy")
    missing = sorted(target_bones(tpl) - set(bones)) if bones else []
    if missing:
        raise CharselError(f"the body has no {', '.join(missing)} bone (the Link body's animation targets need it)")
    ml.lists[key] = tpl.lists[key]
    return aamp.dump(ml), True


def donor_model_list(r: Recipe, donor: dict[str, bytes]) -> bytes | None:
    """The donor's model list file, or None."""
    from mp import ghost_link_pack as glp
    try:
        _, dlt = glp.link_target(donor, r.donor)
        return donor.get(f"Actor/ModelList/{glp.user(dlt, 'ModelUser')}.bmodellist")
    except (KeyError, glp.PackError, AttributeError):
        return None


# The actor heap. Each free name has its own heap (ActorInfo `instSize`, 554 KB-1.1 MB), and everything the actor makes
# at creation must fit in it. After the first in-game check this looked like the cause of the five failures (big
# skeletons x about 1,400 retargeted clips); the second check (2026-11-13) failed them the same way with Link's file
# not retargeted and on bigger names, and the animation targets (above) explain all of them. So the rule below is
# only a precaution now, unproven either way [G]: a body whose estimate outgrows its name's heap gets Link's file
# without the retarget (`charsel build --link-retarget on` keeps it, to try in the game; `off` drops it everywhere).
# Link's clips turn bones only (no limb moves), and these bodies' main bones have Link's names, parents and rest
# turns (play-as reads the same), so they still drive them.
RETARGET_BYTES = 4         # heap per retargeted clip and model bone [G: fitted to the first in-game check]
HEAP_BASE = 320_000        # bytes: the rest of the actor (model instance, animation slots, the cutscene dummy's AI) [G]
PLAYER_CLIPS = 1167        # clips in Link's animation file (when it can't be read here)
LINK_RETARGET = ("auto", "on", "off")   # charsel build --link-retarget: by the heap estimate, always, never


def pack_anim_files(r: Recipe, donor: dict[str, bytes], rest: str | None = None) -> list[tuple[str, bool]]:
    """The pack's animation files and their retarget: the donor's own animation list says which of its files it
    retargets, and we do the same for those files; the recipe's flag for the others."""
    import aamp
    from mp import ghost_link_pack as glp
    _, dlt = glp.link_target(donor, r.donor)
    flags: dict[str, bool] = {}
    d_as = dlt.get_value("ASUser")
    if isinstance(d_as, str) and f"Actor/ASList/{d_as}.baslist" in donor:
        add = aamp.load(donor[f"Actor/ASList/{d_as}.baslist"]).list("AddReses")
        for o in (add.objects.values() if add else ()):
            a = o.get_value("Anim")
            if isinstance(a, str):
                flags[a] = o.get_value("RetargetModel") not in (None, "")
    return [(anim, flags.get(anim, rt)) for anim, rt in anim_files(r, rest)]


def donor_model(r: Recipe, donor: dict[str, bytes]) -> str | None:
    """The donor's model folder (its model list's first ModelData), or None (a body put together from a look file)."""
    import aamp
    try:
        ml = aamp.load(donor_model_list(r, donor) or b"").list("ModelData")
        if ml and ml.lists:
            folder = next(iter(ml.lists.values())).object("Base").get_value("Folder")
            return folder if isinstance(folder, str) and folder else None
    except (aamp.AampError, AttributeError):
        pass
    return None


def model_bone_names(romfs: Path, folder: str | None) -> list[str] | None:
    """The bone names of a model file's first model (Model/<folder>.sbfres from the game's files), or None."""
    if not folder:
        return None
    try:
        from wiiu_model import nx_model
        data = model_file(Path(romfs), folder)
        if data is None:
            return None
        res = nx_model.read(_unyaz(data))
        return [b.name for b in res.models[0].skeleton.bones] if res.models else None
    except Exception:   # (any read failure: no estimate, no bone check; the pack is built as before)
        return None


def model_bones(romfs: Path, folder: str | None) -> int | None:
    """How many bones a model file's first model has, or None."""
    names = model_bone_names(romfs, folder)
    return len(names) if names is not None else None


def heap_need(files: list[tuple[str, bool]], bones: int, count) -> int:
    """About how much actor heap a pack needs (bytes): HEAP_BASE plus RETARGET_BYTES per retargeted clip and bone."""
    return HEAP_BASE + RETARGET_BYTES * bones * sum(count(f) for f, rt in files if rt)


def fit_heap(files: list[tuple[str, bool]], bones: int | None, heap: int | None, count
             ) -> tuple[list[tuple[str, bool]], int | None]:
    """(the files, the estimated need): Link's file without its retarget when the body would outgrow the name's heap
    with it (the family's files keep theirs: the own walk and run fit the body). Unknown bones or heap: as given."""
    if not bones or not heap:
        return files, None
    need = heap_need(files, bones, count)
    if need <= heap:
        return files, need
    off = [(f, rt and f != PLAYER_ANIM) for f, rt in files]
    return off, heap_need(off, bones, count)


# ------------------------------------------------------------------------------------------------ cloth (2026-11-14)
#
# Skirt, hair, scarf and fur physics: the pack's PhysicsUser is SwChar_<id>, a copy of the donor's physics file made
# CLOTH ONLY, the way every armor piece of the game does it [V: 3 armors read, Armor_001_Upper's below]: no rigid body
# (the donor's 'Tgt' sensor capsule), no character controller (its 60 kg capsule on EntityNPC would push Link), no
# contact info, no ragdoll (the monsters'), only the cloth parts and the support bones (helper bones turned by others).
# The cloth (.hkcl) and support-bone (.bphyssb) files are the donor's own, copied from the player's game into the pack
# at build time, at the paths the physics file names (Physics/Cloth/<path>, Physics/SupportBone/<path>) [V: the
# donors' packs]. They name the donor's bones, and the pack draws the donor's model: the same bones.
# Not seen in the game [V: 0 of the game's NPC and DemoNPC actors use cloth with no rigid body; the Great Fairies keep a
# 'Tgt' capsule on SensorNoHit with no controller]: whether an NPC-class actor runs with no rigid body at all [G].
# Fallback: `charsel build --cloth off` (the packs as before: no physics). Every game physics file lists every part of
# its cloth file (508 read [V]); leaving parts out (fit_cloth, only when the heap estimate is short) is [G].
PHYS_HEADER = "ParamSetHeader"     # (its hash 0x4b0843d2, as in the game's files [V])
PHYS_OFF = {"use_rigid_body_set_num": 0, "use_ragdoll": False, "use_character_controller": False,
            "use_contact_info": False, "use_edge_rigid_body_num": 0}
CLOTH_HEAP_X = 1.5                 # actor heap per byte of the cloth and support-bone files [G: no game number]
PACK_LIMIT_CLOTH = 256 * 1024      # bytes unpacked, a pack with physics (the cloth files: 27-95 KB [V])
CLOTH_SWITCH = ("on", "off")       # charsel build --cloth


def donor_physics(r: Recipe, donor: dict[str, bytes]) -> tuple[str, object] | None:
    """(the donor's physics file name, its parameters) or None (no physics)."""
    import aamp
    from mp import ghost_link_pack as glp
    _, dlt = glp.link_target(donor, r.donor)
    pu = dlt.get_value("PhysicsUser")
    data = donor.get(f"Actor/Physics/{pu}.bphysics") if isinstance(pu, str) and pu not in ("", "Dummy") else None
    return (pu, aamp.load(data)) if data else None


def cloth_parts(phys) -> list[str]:
    """The cloth part names a physics file lists (Cloth > Cloth_0, Cloth_1...), in its order; [] none."""
    from aamp import name_hash
    ps = phys.list("ParamSet")
    cl = ps.list("Cloth") if ps else None
    hdr = ps.object(PHYS_HEADER) if ps else None
    if not cl or not hdr or not hdr.get_value("use_cloth"):
        return []
    n = cl.object("ClothHeader").get_value("cloth_num") if cl.object("ClothHeader") else 0
    out = []
    for i in range(int(n or 0)):
        o = cl.objects.get(name_hash(f"Cloth_{i}"))
        name = o.get_value("name") if o else None
        if isinstance(name, str) and name:
            out.append(name)
    return out


def physics_paths(phys) -> dict[str, str]:
    """{"cloth": "Physics/Cloth/<path>", "support": "Physics/SupportBone/<path>"}: the files a physics file names (only
    the kinds it uses)."""
    ps = phys.list("ParamSet")
    hdr = ps.object(PHYS_HEADER) if ps else None
    out = {}
    cl = ps.list("Cloth") if ps else None
    if hdr and hdr.get_value("use_cloth") and cl and cl.object("ClothHeader"):
        p = cl.object("ClothHeader").get_value("cloth_setup_file_path")
        if isinstance(p, str) and p:
            out["cloth"] = f"Physics/Cloth/{p}"
    sb = ps.object("SupportBone") if ps else None
    if hdr and hdr.get_value("use_support_bone") and sb:
        p = sb.get_value("support_bone_setup_file_path")
        if isinstance(p, str) and p:
            out["support"] = f"Physics/SupportBone/{p}"
    return out


def cloth_physics(phys, keep) -> bytes:
    """The donor's physics file made cloth only (the armor pattern above): its header with PHYS_OFF, its support bones,
    and the cloth parts in `keep` (in the file's own order, numbered again; the cloth list's other objects as they
    are). Nothing else: no rigid bodies, no character controller, no contact info, no ragdoll."""
    import aamp
    from aamp import BOOL, INT, PList, PObject, Param, name_hash
    ps = phys.list("ParamSet")
    hdr = ps.object(PHYS_HEADER) if ps else None
    cl = ps.list("Cloth") if ps else None
    if not hdr or not cl or not cl.object("ClothHeader"):
        raise CharselError("the donor's physics has no cloth")
    keep = set(keep)
    new_hdr = PObject(hdr)
    for k, v in PHYS_OFF.items():
        new_hdr[name_hash(k)] = Param(INT if isinstance(v, int) and not isinstance(v, bool) else BOOL, v)
    new_hdr[name_hash("use_cloth")] = Param(BOOL, True)
    paths = physics_paths(phys)
    new_hdr[name_hash("use_support_bone")] = Param(BOOL, "support" in paths)
    objects = {name_hash(PHYS_HEADER): new_hdr}
    if "support" in paths:
        objects[name_hash("SupportBone")] = PObject(ps.object("SupportBone"))
    parts = [n for n in cloth_parts(phys) if n in keep]
    if not parts:
        raise CharselError("no cloth part left")
    by_name = {o.get_value("name"): o for h, o in cl.objects.items() if o.get_value("name")}
    cloth = PList()
    for h, o in cl.objects.items():   # (the header and the cloth's own settings: as they are, then the parts)
        if o.get_value("name") is None:
            cloth.objects[h] = PObject(o)
    ch = PObject(cloth.objects[name_hash("ClothHeader")])
    ch[name_hash("cloth_num")] = Param(INT, len(parts))
    cloth.objects[name_hash("ClothHeader")] = ch
    for i, n in enumerate(parts):
        cloth.objects[name_hash(f"Cloth_{i}")] = PObject(by_name[n])
    return aamp.dump(PList(lists={name_hash("ParamSet"): PList(objects=objects,
                                                                 lists={name_hash("Cloth"): cloth})}))


def cloth_only_problems(data: bytes) -> list[str]:
    """What makes a physics file more than cloth: [] = cloth (and support bones) only."""
    import aamp
    try:
        ps = aamp.load(data).list("ParamSet")
        hdr = ps.object(PHYS_HEADER)
    except Exception as e:   # (any read failure is the answer)
        return [f"its physics doesn't read ({type(e).__name__})"]
    if hdr is None:
        return ["its physics has no header"]
    bad = [f"physics {k} = {hdr.get_value(k)!r}" for k, v in PHYS_OFF.items() if hdr.get_value(k, v) != v]
    for name in ("RigidBodySet", "CharacterController", "RigidContactInfo"):
        if ps.list(name) is not None:
            bad.append(f"physics has {name}")
    if ps.object("Ragdoll") is not None:
        bad.append("physics has a ragdoll")
    return bad


def cloth_bytes(files: dict[str, bytes], parts: int, of: int) -> int:
    """The heap estimate of a pack's cloth: CLOTH_HEAP_X per byte of its cloth and support-bone files, the cloth's
    share by parts kept [G: no game number; the instances live in the actor heap]."""
    cloth = sum(len(d) for n, d in files.items() if n.startswith("Physics/Cloth/"))
    support = sum(len(d) for n, d in files.items() if n.startswith("Physics/SupportBone/"))
    share = parts / of if of else 0.0
    return int(CLOTH_HEAP_X * (cloth * share + support))


def fit_cloth(want: list[str], files: dict[str, bytes], all_parts: int, room: int | None
              ) -> tuple[list[str], list[str], int]:
    """(parts kept, parts left out, their heap estimate): the last ones of `want` (least important) left out until the
    rest fit `room` (bytes of heap left after the model and animations); never the first. No room known: all."""
    kept = list(want)
    need = cloth_bytes(files, len(kept), all_parts)
    while room is not None and need > room and len(kept) > 1:
        kept.pop()
        need = cloth_bytes(files, len(kept), all_parts)
    return kept, [p for p in want if p not in kept], need


def pack_cloth(r: Recipe, donor: dict[str, bytes]) -> tuple[object, dict[str, bytes], list[str], str]:
    """(the donor's physics parameters, its cloth and support-bone files, the recipe's parts it has (in the recipe's
    order), a note) for a recipe with cloth; (None, {}, [], why) when the donor has none to give."""
    if not r.cloth:
        return None, {}, [], ""
    got = donor_physics(r, donor)
    if got is None:
        return None, {}, [], f"{r.donor} has no physics file"
    phys = got[1]
    have = cloth_parts(phys)
    paths = physics_paths(phys)
    files = {p: donor[p] for p in paths.values() if p in donor}
    if "cloth" not in paths or paths["cloth"] not in files:
        return None, {}, [], f"{r.donor}'s cloth file isn't in its pack"
    if "support" in paths and paths["support"] not in files:
        return None, {}, [], f"{r.donor}'s support-bone file isn't in its pack"
    parts = [p for p in r.cloth if p in have]
    missing = [p for p in r.cloth if p not in have]
    if not parts:
        return None, {}, [], f"none of the cloth parts is in {r.donor}'s physics"
    return phys, files, parts, (f"not in its physics: {', '.join(missing)}" if missing else "")


def physics_bones(files: dict[str, bytes], bones, link_bones) -> list[str]:
    """The bones a pack's cloth and support bones move: the names in its cloth file and its support-bone file that are
    bones of its model, less the ones named as Link's (the ones the costume's pose sets: the cloth's anchors, the arms
    the support bones read). Sorted. A cloth file is a Havok packfile: its bone names are plain strings in it [V]."""
    import aamp
    names: set[str] = set()
    for n, d in files.items():
        if n.startswith("Physics/Cloth/"):
            names.update(m.decode("ascii") for m in re.findall(rb"[A-Za-z_][A-Za-z0-9_]{0,63}", d))
        elif n.startswith("Physics/SupportBone/"):
            def walk(pl):
                for o in pl.objects.values():
                    for p in o.values():
                        if isinstance(p.value, str):
                            names.add(p.value)
                for sub in pl.lists.values():
                    walk(sub)
            walk(aamp.load(d))
    return sorted((names & set(bones)) - set(link_bones))


def make_pack(r: Recipe, slot: str, profile: str, template: dict[str, bytes], donor: dict[str, bytes],
              rest: str | None = None, files: list[tuple[str, bool]] | None = None, bones=None,
              physics: tuple[bytes, dict[str, bytes]] | None = None) -> dict[str, bytes]:
    """The files of a character's pack, from the template's pack (the game's cutscene Sheikah man: actor link, AI
    program, animation targets when the donor's can't be used: body_targets) and the donor's (its model list, its
    animation list's flags, its look file). files: the animation files and their retarget (default pack_anim_files;
    build passes fit_heap's). bones: the body's bone names, when known. physics: (our cloth-only physics file, the
    donor's cloth and support-bone files) (cloth_physics, pack_cloth); None: no physics."""
    import aamp
    from mp import ghost_link_pack as glp
    if r.kind != "pack":
        raise CharselError(f"{r.id} has no pack of its own")
    _, dlt = glp.link_target(donor, r.donor)
    d_model = glp.user(dlt, "ModelUser")
    if d_model.startswith("SwChar_") or d_model == glp.OURS:
        raise CharselError(f"{r.donor}'s pack is already ours: point --romfs at the game's own files")
    ml_name = f"Actor/ModelList/{d_model}.bmodellist"
    if ml_name not in donor:
        raise CharselError(f"{r.donor}'s pack has no {ml_name}")
    ml = aamp.load(donor[ml_name])
    md = ml.list("ModelData")
    umii = not (md and md.lists)                   # no ModelData: a body put together by the Mii-style look file
    d_umii = dlt.get_value("UMiiUser") or "Dummy"
    if umii and (d_umii == "Dummy" or f"Actor/UMii/{d_umii}.bumii" not in donor):
        raise CharselError(f"{r.donor} has no model data and no look file")
    if files is None:
        files = pack_anim_files(r, donor, rest)
    look = (d_umii, donor[f"Actor/UMii/{d_umii}.bumii"]) if umii else None
    model_list, _swapped = body_targets(donor[ml_name], template, bones)
    return _pack_files(r.id, slot, profile, template, model_list, look, files, clip_map(r, rest), frame_ctrl(r),
                       physics)


def _pack_files(cid: str, slot: str, profile: str, template: dict[str, bytes], model_list: bytes,
                look: tuple[str, bytes] | None, files: list[tuple[str, bool]], mapping: dict[str, str],
                ctrl: dict[str, tuple] | None = None, physics: tuple[bytes, dict[str, bytes]] | None = None
                ) -> dict[str, bytes]:
    """The pack: the template's actor link with our users (no AI but the cutscene dummy's; no physics, or our
    cloth-only physics), the files the kept users need, our model list, the look file (bodies the game puts together
    from one), our animation list (files, sorted names, blend frames) and one animation set per name. Every name of
    ours is SwChar_<id>, so two packs never share a path with different contents."""
    import aamp
    from aamp import F32, STRING32, STRING64, PList, PObject, Param, name_hash
    from mp import ghost_link_pack as glp
    if slot == LINK_ACTOR or slot not in FREE_SLOTS:
        raise CharselError(f"{slot} isn't one of the free names")
    al_root, lt = glp.link_target(template, TEMPLATE)
    if glp.user(lt, "ModelUser") == glp.OURS or glp.user(lt, "ModelUser").startswith("SwChar_"):
        raise CharselError(f"{TEMPLATE}'s pack is already ours: point --romfs at the game's own files")
    tpl_as = glp.user(lt, "ASUser")
    if f"Actor/ASList/{tpl_as}.baslist" not in template:
        raise CharselError(f"{TEMPLATE}'s pack has no animation list")
    tpl_list = aamp.load(template[f"Actor/ASList/{tpl_as}.baslist"])
    tmpl = next((o for o in tpl_list.list("AddReses").objects.values()
                 if o.get(name_hash("RetargetModel")) is not None), None)
    if tmpl is None:
        raise CharselError(f"{TEMPLATE}'s animation list has no retarget entry to copy")
    name = ours(cid)
    users = {"ModelUser": name, "ASUser": name, "PhysicsUser": name if physics else "Dummy",
             "AnimationInfo": "Dummy",
             "UMiiUser": look[0] if look else "Dummy", "AIProgramUser": "DemoDummy", "ProfileUser": profile}
    for k, v in users.items():
        p = lt.get(name_hash(k))
        if p is None:
            raise CharselError(f"the actor link has no {k}")
        lt[name_hash(k)] = Param(p.type, v)
    out: dict[str, bytes] = {f"Actor/ActorLink/{slot}.bxml": aamp.dump(al_root)}
    for n, b in template.items():   # (what the kept users need: the AI program, as the Link body does)
        if not n.startswith(glp.DROPPED_FOLDERS) and not n.startswith("Actor/ActorLink/"):
            out[n] = b
    if "Actor/AIProgram/DemoDummy.baiprog" not in out:
        raise CharselError(f"{TEMPLATE}'s pack has no DemoDummy AI program")
    out[f"Actor/ModelList/{name}.bmodellist"] = model_list
    if physics:   # (2026-11-14: cloth only; its cloth and support-bone files at the paths it names)
        out[f"Actor/Physics/{name}.bphysics"] = physics[0]
        for n, b in physics[1].items():
            if not n.startswith(("Physics/Cloth/", "Physics/SupportBone/")):
                raise CharselError(f"{n} isn't a cloth or support-bone file")
            out[n] = b
    if look:
        out[f"Actor/UMii/{look[0]}.bumii"] = look[1]
    add = PList()
    for i, (anim, rt) in enumerate(files):
        o = PObject()
        o[name_hash("Anim")] = Param(tmpl[name_hash("Anim")].type, anim)
        if rt:
            o[name_hash("RetargetModel")] = Param(tmpl[name_hash("RetargetModel")].type, "Skeleton")
        add.objects[name_hash(f"AddRes_{i}")] = o
    names = sorted(mapping, key=str.encode)        # the game binary-searches this list: keep it sorted
    defines = PList()
    for i, n in enumerate(names):
        o = PObject()
        o.set("Name", STRING64, n)
        o.set("Filename", STRING64, f"{name}_{n}")
        defines.objects[name_hash(f"ASDefine_{i}")] = o
    frames = {n: f for n, _c, f in glp.ANIMS}
    cf = PList()
    for i, pre in enumerate(names):   # blend frames between sets, as the Link body's list (ghost_link_pack.as_list)
        posts = PList()
        targets = [""] + sorted((p for p in glp.LOCOMOTION if p != pre and p in mapping), key=str.encode)
        for k, post in enumerate(targets):
            o = PObject()
            o.set("Name", STRING32, post)
            o.set("Frame", F32, 6.0 if post == "" else glp.blend_frames(pre, post, frames))
            o[0xCF996FB7] = Param(F32, -1.0)
            posts.objects[name_hash(f"CFPost_{k}")] = o
        pre_o = PObject()
        pre_o.set("Name", STRING32, pre)
        cf.lists[name_hash(f"CFDefine_{i}")] = PList(lists={name_hash("CFPosts"): posts},
                                                    objects={name_hash("CFPre"): pre_o})
    out[f"Actor/ASList/{name}.baslist"] = aamp.dump(PList(lists={name_hash("AddReses"): add,
                                                                 name_hash("ASDefines"): defines,
                                                                 name_hash("CFDefines"): cf}))
    for n, clip in mapping.items():   # (0.9.5: an attack's set starts at once, as Link's own)
        out[f"Actor/AS/{name}_{n}.bas"] = as_file(clip, *(ctrl or {}).get(n, (None, None, None)),
                                                  morph=ATTACK_MORPH if is_attack(n) else None)
    return out


def make_copy_pack(cid: str, slot: str, profile: str, template: dict[str, bytes], pause: dict[str, bytes]
                   ) -> dict[str, bytes]:
    """--model-copies: a Link-skeleton body under its own model name (Model/SwChar_<id>.sbfres, a renamed copy of
    the model file): the Link body's model list (the pause-menu Link's, with the Sheikah man's animation slots) with
    that folder, Link's own clips (no retarget: the same skeleton), on a free name."""
    import aamp
    from aamp import Param, name_hash
    from mp import ghost_link_pack as glp
    if cid in RECIPES and RECIPES[cid].kind == "pack" or slot not in FREE_SLOTS:   # (link, linkle, an import-vrm id)
        raise CharselError(f"no model copy for {cid} on {slot}")
    _, tlt = glp.link_target(template, TEMPLATE)
    _, plt = glp.link_target(pause, glp.DONOR_LINK)
    tml = f"Actor/ModelList/{glp.user(tlt, 'ModelUser')}.bmodellist"
    pml = f"Actor/ModelList/{glp.user(plt, 'ModelUser')}.bmodellist"
    if tml not in template or pml not in pause:
        raise CharselError("a model list is missing")
    ml = aamp.load(glp.model_list(aamp.load(pause[pml]), aamp.load(template[tml]), "sheikah"))
    md0 = ml.list("ModelData").list("ModelData_0")
    base = md0.object("Base")
    f = base.get(name_hash("Folder"))
    base[name_hash("Folder")] = Param(f.type, ours(cid))
    _own_units(md0, ours(cid))   # (the model file's own name: own_names)
    mapping = {n: c for n, c, _f in glp.ANIMS}
    return _pack_files(cid, slot, profile, template, aamp.dump(ml), None, [(PLAYER_ANIM, False)], mapping)


# ---- our own model files carry their own name (2026-11-15, the More Room probe; docs/room.md)
#
# "Linkle / Custom did not load (the game could not create it)": both costume model files were Link's model file with
# new shapes, still named "Link" inside (the file's name, its one model's name, and "Link.Tex" for the textures), with
# the model list's unit "Link". Every model file of the game is named as its file (Npc_Gerudo_Hero.sbfres holds model
# Npc_Gerudo_Hero; Npc_Gerudo_Hero.Tex.sbfres is named Npc_Gerudo_Hero.Tex). In the game [V 2026-11-15]: the actor heap
# was not the cause (12 small allocations, none failed; 64 MB more heap changed nothing), and the same two costumes with
# their files renamed SwChar_linkle / SwChar_yuniar (file, model, textures, unit) were created at once.
# [G: which one the game checks: a clash with the player's own loaded "Link", or the name against the file.]

def _own_units(md0, folder: str) -> int:
    """Every unit of a model list's ModelData entry named folder (our model files hold one model, named as the file).
    Returns how many changed."""
    from aamp import Param, name_hash
    ul = md0.lists.get(name_hash("Unit"))
    n = 0
    for o in ul.objects.values() if ul else ():
        p = o.get(name_hash("UnitName"))
        if p is not None and p.value != folder:
            o[name_hash("UnitName")] = Param(p.type, folder)
            n += 1
    return n


def own_names(model: bytes, tex: bytes, folder: str) -> tuple[bytes, bytes, bool]:
    """A model file of ours (Model/<folder>.sbfres, one model) and its textures (<folder>.Tex.sbfres) named as their
    own files: the file and its model <folder>, the textures <folder>.Tex. Returns (model, tex, changed); the same
    bytes when they already are."""
    from wiiu_model import checks, nx_bfres, nx_model
    res = nx_model.read(_unyaz(model))
    if len(res.models) != 1:
        raise CharselError(f"Model/{folder}.sbfres has {len(res.models)} models, expected 1")
    ts = nx_bfres.read_textures(_unyaz(tex))
    if res.name == folder and res.models[0].name == folder and ts.name == folder:
        return model, tex, False
    res.name = res.models[0].name = folder
    ts.name = folder
    raw = nx_model.write(res)
    bad = checks.switch_model_problems(raw)
    if bad:
        raise CharselError(f"Model/{folder}.sbfres fails the self-check after its rename: {bad[:3]}")
    return _yaz(raw), _yaz(nx_bfres.write_textures(ts)), True


def own_pack_units(pack: bytes, folder: str) -> tuple[bytes, bool]:
    """A pack of ours whose model list (ModelUser = folder) names a unit other than folder, with it renamed."""
    import aamp
    from mp import ghost_link_pack as glp
    files = glp.read_pack(pack)
    rel = f"Actor/ModelList/{folder}.bmodellist"
    if rel not in files:
        return pack, False
    ml = aamp.load(files[rel])
    md = ml.list("ModelData")
    if not md or not md.lists or not _own_units(next(iter(md.lists.values())), folder):
        return pack, False
    files[rel] = aamp.dump(ml)
    return glp.pack_bytes(files)[0], True


def repair_own_names(top: Path, cids, slot_of: dict[str, str], say=print) -> list[str]:
    """Kept models of ours (an import-vrm costume, which a build keeps as it is) still named "Link" inside: renamed in
    place, with their pack's unit. Returns the ids repaired."""
    done = []
    for cid in cids:
        name = ours(cid)
        mp, tp = top / "Model" / f"{name}.sbfres", top / "Model" / f"{name}.Tex.sbfres"
        pp = top / "Actor" / "Pack" / f"{slot_of.get(cid, '')}.sbactorpack"
        if not (mp.is_file() and tp.is_file()):
            continue
        try:
            model, tex, changed = own_names(mp.read_bytes(), tp.read_bytes(), name)
            pack, pchanged = own_pack_units(pp.read_bytes(), name) if pp.is_file() else (b"", False)
        except Exception as e:   # (any read failure: left as it is, said)
            say(f"  {cid}: its model couldn't be named as its own file ({str(e)[:100]})")
            continue
        for path, data, ch in ((mp, model, changed), (tp, tex, changed), (pp, pack, pchanged)):
            if ch:
                path.unlink()   # (a new file, never written through a hard link the library may share)
                path.write_bytes(data)
        if changed or pchanged:
            done.append(cid)
            say(f"  {cid}: its model files and unit renamed {name} (they were still named Link inside)")
    return done


def read_back(data: bytes) -> dict:
    """A built pack read back: its actor link's users, the animation names (in file order) and each one's clip, the
    animation files and their retarget, its sizes."""
    import aamp
    from mp import ghost_link_pack as glp
    from wiiu_convert import yaz0_decompress
    raw = yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data
    files = glp.read_pack(data)
    links = [n for n in files if n.startswith("Actor/ActorLink/")]
    if len(links) != 1:
        raise CharselError(f"{len(links)} actor links in the pack")
    slot = links[0].rsplit("/", 1)[1][:-5]
    _, lt = glp.link_target(files, slot)
    users = {k: lt.get_value(k) for k in ("ProfileUser", "ModelUser", "ASUser", "PhysicsUser", "AIProgramUser",
                                          "UMiiUser", "AnimationInfo") + UNTOUCHABLE}
    asl = aamp.load(files[f"Actor/ASList/{users['ASUser']}.baslist"])
    names, clips, missing, ctrls = [], {}, [], {}
    for o in asl.list("ASDefines").objects.values():
        n, fn = o.get_value("Name"), o.get_value("Filename")
        names.append(n)
        bas = files.get(f"Actor/AS/{fn}.bas")
        if bas is None:
            missing.append(n)
            continue
        clips[n] = aamp.load(bas).list("Elements").list("Element0").object("Parameters").get_value("FileName")
        fc = as_ctrl(bas)
        if any(v is not None for v in fc):
            ctrls[n] = fc
    adds =[(o.get_value("Anim"), o.get_value("RetargetModel")) for o in asl.list("AddReses").objects.values()]
    ml = files.get(f"Actor/ModelList/{users['ModelUser']}.bmodellist")
    folder, targets, units = None, [], []
    if ml:
        mlp = aamp.load(ml)
        md = mlp.list("ModelData")
        if md and md.lists:
            md0 = next(iter(md.lists.values()))
            folder = md0.object("Base").get_value("Folder")
            ul = md0.lists.get(aamp.name_hash("Unit"))
            units = [o.get_value("UnitName") for o in ul.objects.values()] if ul else []
        targets = target_types(mlp)
    physics = None   # (2026-11-14: our cloth-only physics: its parts, the files it names and whether they are there)
    pu = users["PhysicsUser"]
    if isinstance(pu, str) and pu not in ("", "Dummy"):
        pd = files.get(f"Actor/Physics/{pu}.bphysics")
        physics = {"file": pd is not None, "cloth": [], "paths": {}, "missing": [], "bad": []}
        if pd is not None:
            try:
                ph = aamp.load(pd)
                physics.update(cloth=cloth_parts(ph), paths=physics_paths(ph))
            except Exception as e:   # (any read failure: said by check_pack)
                physics["bad"].append(f"its physics doesn't read ({type(e).__name__})")
            physics["missing"] = [v for v in physics["paths"].values() if v not in files]
            physics["bad"] += cloth_only_problems(pd)
    return {"slot": slot, "users": users, "names": names, "clips": clips, "ctrl": ctrls, "missing_sets": missing,
            "adds": adds, "physics": physics,
            "folder": folder, "units": units, "targets": targets, "files": len(files), "unpacked": len(raw),
            "packed": len(data)}


# The actor link's users that would make a ghost or the self puppet something Link can lock on to, talk to or hit
# (the attention list: lock-on and talk targets; the damage parameters). The template's are Dummy (none): check_pack
# keeps it so (a missing user is none too). Physics: none, or ours with cloth only (no rigid body, no character
# controller, no contact info, no ragdoll: nothing that blocks, pushes or is hit; cloth_only_problems).
UNTOUCHABLE = ("AttentionUser", "DamageParamUser")


def check_pack(data: bytes, expect_slot: str | None = None) -> list[str]:
    """What is wrong with a built pack ([] = nothing)."""
    try:
        r = read_back(data)
    except Exception as e:   # (any read failure is the answer)
        return [f"doesn't read back: {type(e).__name__}: {e}"]
    bad = []
    if expect_slot and r["slot"] != expect_slot:
        bad.append(f"actor link for {r['slot']}, expected {expect_slot}")
    if r["names"] != sorted(r["names"], key=str.encode):
        bad.append("animation names not sorted (the game binary-searches them)")
    if len(set(r["names"])) != len(r["names"]):
        bad.append("an animation name twice")
    if r["missing_sets"]:
        bad.append(f"no animation set for {', '.join(r['missing_sets'])}")
    if any(not c for c in r["clips"].values()):
        bad.append("an animation set without a clip")
    u = r["users"]
    if u["AIProgramUser"] != "DemoDummy":
        bad.append(f"AI program {u['AIProgramUser']}, not the cutscene dummy")
    ph = r["physics"]
    if u["PhysicsUser"] not in (None, "Dummy"):   # (2026-11-14: only ours, cloth only, with the files it names)
        if u["PhysicsUser"] != u["ModelUser"] or not str(u["PhysicsUser"]).startswith("SwChar_"):
            bad.append(f"physics {u['PhysicsUser']}")
        elif not ph or not ph["file"]:
            bad.append(f"no physics file for {u['PhysicsUser']}")
        else:
            bad += ph["bad"]
            if not ph["cloth"]:
                bad.append("physics with no cloth part")
            if "cloth" not in ph["paths"]:
                bad.append("physics with no cloth file")
            bad += [f"{m} is missing (the physics names it)" for m in ph["missing"]]
    for k in UNTOUCHABLE:
        if u[k] not in (None, "Dummy"):
            bad.append(f"{k} {u[k]} (it could be locked on, talked to or hit)")
    if u["ProfileUser"] not in ("DemoNPC", "NPC"):
        bad.append(f"profile {u['ProfileUser']}")
    if u["ProfileUser"] != SLOT_PROFILE.get(r["slot"], u["ProfileUser"]):
        bad.append(f"profile {u['ProfileUser']} on a {SLOT_PROFILE.get(r['slot'])} name")
    limit = PACK_LIMIT_CLOTH if ph else PACK_LIMIT
    if r["unpacked"] >= limit:
        bad.append(f"{r['unpacked']:,} bytes unpacked (limit {limit:,})")
    if not r["adds"]:
        bad.append("no animation files")
    if any(t in SWAP_TARGET_TYPES for t in r["targets"]):
        bad.append("a type-2 animation target (the game can't create it without a look file: rebuild)")
    if str(r["folder"] or "").startswith("SwChar_") and r["units"] != [r["folder"]]:
        bad.append(f"its model {r['folder']} is unit {', '.join(map(str, r['units'])) or 'none'} (our own model files "
                   "are named as their file: the game can't create it otherwise; rebuild)")
    return bad


# ------------------------------------------------------------------------------------------------ building

MOD_TOML = f'''# Characters for multiplayer ("Who am I", docs/charsel.md). Built on your PC from your own game files by
# tools/charsel.py: never share or commit this folder. Your friend builds their own.
[mod]
id = "{MOD_ID}"
name = "Characters for multiplayer (Who am I)"
version = "{VERSION}"
authors = ["giruka"]
description = "Small actor packs that let the ghost mod show other players as Zelda, a Goron, a Bokoblin and more, on cutscene names the game never uses. Built from your own game files. Not yet tried in the game."
game_versions = ["1.9.0"]

[data]
romfs = "romfs"
'''


def parse_slots(items) -> dict[str, str]:
    """--slot ID=ACTOR, checked: a known id and one of the free names."""
    out = {}
    for it in items or []:
        cid, _, slot = str(it).partition("=")
        cid = find_id(cid)
        if cid is None or slot not in FREE_SLOTS:
            raise CharselError(f"bad --slot {it!r}: ID=ACTOR with ACTOR one of {', '.join(FREE_SLOTS)}")
        out[cid] = slot
    return out


def build(romfs=DEFAULT_ROMFS, out=DEFAULT_OUT, ids=None, *, library=None, slots=None, model_copies=False,
          monster_link=False, link_retarget="auto", cache=DEFAULT_CACHE, cloth="on", say=print) -> dict:
    """Build the mod folder (mod.toml, romfs\\Actor\\Pack\\<free name>.sbactorpack, charsel.json). Returns the catalog.
    link_retarget: Link's file retargeted by the heap estimate ("auto"), always ("on") or never ("off"). cloth: the
    recipes' cloth physics ("on") or none ("off": the packs as before 2026-11-14)."""
    from mp import ghost_link_pack as glp
    from wiiu_convert import FormatError
    import aamp
    romfs, out = Path(romfs).resolve(), Path(out).resolve()
    if out == romfs or romfs in out.parents or out in romfs.parents:
        raise CharselError("--out must be a folder of your own, not inside the game's files")
    if not (romfs / "Actor" / "Pack").is_dir():
        raise CharselError(f"{romfs} isn't the game's extracted romfs (no Actor\\Pack)")
    if link_retarget not in LINK_RETARGET:
        raise CharselError(f"--link-retarget is {', '.join(LINK_RETARGET)}")
    if cloth not in CLOTH_SWITCH:
        raise CharselError(f"--cloth is {', '.join(CLOTH_SWITCH)}")
    all_ids = not ids
    ids = list(dict.fromkeys(find_id(i) or i for i in (ids or order())))
    bad = [i for i in ids if i not in RECIPES]
    if bad:
        raise CharselError(f"no character {', '.join(bad)} (charsel list)")
    # a build of some characters keeps the others from the last build (same recipe version, pack still there)
    old = load_catalog(out)
    keep: dict[str, dict] = {}
    for cid, info in ((old.get("built") or {}).items() if old.get("mod") == MOD_ID and not all_ids else ()):
        r = RECIPES.get(cid)
        slot = info.get("slot") if isinstance(info, dict) else None
        cl_then = (info.get("cloth") or {}) if isinstance(info, dict) else {}
        off_then = cl_then.get("off") == "charsel build --cloth off"
        if (r is not None and cid not in ids and slot in FREE_SLOTS and info.get("v") == r.v
                and off_then == (cloth == "off" and bool(r.cloth))   # (the same --cloth as this build)
                and (out / "romfs" / "Actor" / "Pack" / f"{slot}.sbactorpack").is_file()):
            keep[cid] = info
    table = dict(SLOTS)
    table.update(slots or {})
    lm = linkle_mod(library) if library else None    # (on or off: her costume comes from its files)
    on: dict[str, list[str]] = {}
    imported = {k: v for k, v in (old.get("imported") or {}).items()   # (import-vrm's models: kept by every build)
                if old.get("mod") == MOD_ID and isinstance(v, dict) and v.get("slot") in FREE_SLOTS
                and (out / "romfs" / "Actor" / "Pack" / f"{v['slot']}.sbactorpack").is_file()}
    for cid, info in imported.items():
        on.setdefault(info["slot"], []).append(cid)
    for cid in ids:   # (Link gets a name of its own only with --model-copies; Linkle whenever her mod's files are here)
        if cid in table and (RECIPES[cid].kind == "pack" or model_copies or cid == "linkle" and lm):
            on.setdefault(table[cid], []).append(cid)
    for cid, info in keep.items():
        on.setdefault(info["slot"], []).append(cid)
    clash = {s: c for s, c in on.items() if len(c) > 1}
    if clash:
        raise CharselError("two characters on the same actor name: " + "; ".join(
            f"{' and '.join(c)} on {s}" for s, c in clash.items()) + " (move one with --slot, or build both)")
    notes: list[str] = []
    if keep:
        notes.append(f"Kept from the last build: {', '.join(keep)} (charsel build with no names rebuilds all).")
    try:
        ainfo = read_actorinfo(romfs)
    except Exception as e:   # (bcml_data is another job's; without it the free names aren't checked again)
        ainfo = None
        notes.append(f"The game's ActorInfo wasn't read ({type(e).__name__}): free names not checked again.")
    try:
        template = glp.read_pack(glp.find_pack(romfs, TEMPLATE))
        pause = glp.read_pack(glp.find_pack(romfs, glp.DONOR_LINK)) if model_copies else None
    except (glp.PackError, FormatError, aamp.AampError, OSError) as e:
        raise CharselError(f"a game file didn't read as expected: {e}") from None
    catalog_note = ""
    if pause is None and lm and "linkle" in ids:    # (Linkle's costume: the pause-menu Link's model list)
        try:
            pause = glp.read_pack(glp.find_pack(romfs, glp.DONOR_LINK))
        except (glp.PackError, FormatError, aamp.AampError, OSError) as e:
            lm = None
            catalog_note = f"Linkle wasn't built: the game's {glp.DONOR_LINK} didn't read ({e})"
    clips = ClipCache(romfs, cache)
    pm = player_model_mod(library, romfs=romfs) if library else None
    catalog = {"format": 1, "builder": VERSION, "mod": MOD_ID, "built": dict(keep), "kept": sorted(keep),
               "skipped": {k: v for k, v in (old.get("skipped") or {}).items()
                           if not all_ids and k in RECIPES and k not in ids and k not in keep and isinstance(v, str)},
               "notes": notes, "linkle": ({"how": "off", "mod": lm["name"], "version": lm["version"]} if lm
                                          else {"how": "missing"}), "link_modded": bool(pm),
               "model_copies": bool(model_copies) or any(RECIPES[k].kind != "pack" for k in keep),
               "monster_link_clips": bool(monster_link)}
    if imported:
        catalog["imported"] = imported
    if pm:
        catalog["player_model"] = {k: pm[k] for k in ("id", "name", "version", "fp", "linkle")}
        if pm["linkle"]:
            catalog["linkle"] = {"how": "link-body", "mod": pm["name"], "version": pm["version"], "fp": pm["fp"]}
        else:
            notes.append(f"{pm['name']} replaces Link's model here: the Link body shows it, not Link.")
    packs: dict[str, bytes] = {}
    models: dict[str, bytes] = {}
    for cid in ids:
        r = RECIPES[cid]
        rest = None
        need = heap = bones = None
        link_rt, swap = True, False
        cl_info: dict | None = None
        legs_info: dict | None = None
        try:
            if r.kind == "pack":
                rest = "link" if (monster_link and r.cat == "Monsters") else None
                slot = table[cid]
                profile, problems = check_slot(romfs, slot, ainfo)
                if problems:
                    raise CharselError("; ".join(problems))
                donor = glp.read_pack(glp.find_pack(romfs, r.donor))
                res = resolve(r, lambda f: None if f == PLAYER_ANIM else clips.clips(f), rest)
                if res["missing"] or res["clash"]:
                    raise CharselError(f"clips missing {res['missing']} clash {res['clash']}")
                anims = pack_anim_files(r, donor, rest)
                dml = donor_model_list(r, donor)
                swap = bool(dml) and any(t in SWAP_TARGET_TYPES for t in target_types(aamp.load(dml)))
                names = (model_bone_names(romfs, donor_model(r, donor))
                         if swap or any(rt for _f, rt in anims) else None)
                bones = len(names) if names else None
                heap = ((ainfo or {}).get(slot) or {}).get("instSize")

                def count(f):
                    return len(clips.clips(f) or ()) or (PLAYER_CLIPS if f == PLAYER_ANIM else 0)
                # 2026-11-14: its cloth (the recipe's parts the donor has), counted in the heap first: Link's file
                # loses its retarget before a cloth part is left out, and the last parts go when even that is short
                phys, cfiles, cparts, cnote = pack_cloth(r, donor) if cloth == "on" else (None, {}, [], "")
                call = len(cloth_parts(phys)) if phys is not None else 0
                cneed = cloth_bytes(cfiles, len(cparts), call) if phys is not None else 0
                room = heap - cneed if heap else heap
                if link_retarget == "auto":
                    anims, need = fit_heap(anims, bones, room, count)
                else:
                    anims = [(f, rt and (f != PLAYER_ANIM or link_retarget == "on")) for f, rt in anims]
                    need = heap_need(anims, bones, count) if bones and heap else None
                link_rt = any(rt for f, rt in anims if f == PLAYER_ANIM)
                physics = None
                if phys is not None:
                    base = need if need else HEAP_BASE
                    kept, dropped, cneed = fit_cloth(cparts, cfiles, call, heap - base if heap else None)
                    if heap and base + cneed > heap:   # (even its most important part alone: no cloth)
                        cl_info = {"off": f"not even {kept[0]} fits the heap estimate", "parts": [], "dropped": cparts}
                        notes.append(f"{r.name}: no cloth physics (the heap estimate is short even for {kept[0]})")
                    else:
                        physics = (cloth_physics(phys, kept), cfiles)
                        cl_info = {"parts": kept, "dropped": dropped, "bytes": cneed,
                                   "file": physics_paths(phys)["cloth"]}
                        if dropped:
                            notes.append(f"{r.name}: cloth parts left out for the heap: {', '.join(dropped)}")
                        if cnote:
                            cl_info["note"] = cnote
                        need = base + cneed if heap else need
                elif r.cloth and cloth == "on":
                    cl_info = {"off": cnote or "no cloth", "parts": [], "dropped": list(r.cloth)}
                    notes.append(f"{r.name}: no cloth physics ({cnote})")
                elif r.cloth:
                    cl_info = {"off": "charsel build --cloth off", "parts": [], "dropped": list(r.cloth)}
                if need and heap and need > heap:
                    notes.append(f"{r.name} may not fit {slot}'s heap (about {need:,} of {heap:,} bytes): if it "
                                 "doesn't load, move it to a bigger free name with --slot.")
                files = make_pack(r, slot, profile, template, donor, rest, anims, names, physics)
                if r.legs:    # (Zelda: her model copied with legs; her pack's model list names the copy)
                    legs_info = None
                    try:
                        if donor_model(r, donor) != NPC_SKIN_MODEL:
                            raise CharselError(f"her model isn't {NPC_SKIN_MODEL}")
                        lmodel, ltex, legs_info = zelda_legs(romfs, ours(cid))
                        ml_rel = f"Actor/ModelList/{ours(cid)}.bmodellist"
                        files[ml_rel], n = repoint_model_list(files[ml_rel], NPC_SKIN_MODEL, ours(cid))
                        if not n:
                            raise CharselError("her model list doesn't name her model")
                        models[f"Model/{ours(cid)}.sbfres"], models[f"Model/{ours(cid)}.Tex.sbfres"] = lmodel, ltex
                    except Exception as e:   # (any failure: her game model as before, said)
                        legs_info = {"off": str(e)[:200]}
                        notes.append(f"{r.name}'s legs couldn't be added ({str(e)[:120]}): her game model as before.")
            elif cid == "linkle" and lm or model_copies and cid == "link" and pm:
                slot = table[cid]
                profile, problems = check_slot(romfs, slot, ainfo)
                if problems:
                    raise CharselError("; ".join(problems))
                if cid == "linkle":   # her costume (friends' view): the mod's body wearing its own hair and clothes
                    src = Path(lm["where"]), lm["member"]
                    try:
                        model, tex, linfo = linkle_costume(*src)
                        look = {"how": "costume", "wears": linfo["look"], "parts": linfo["parts"],
                                "left_out": linfo["left_out"]}
                    except Exception as e:   # (any read failure: the mod's body as it is, said)
                        model, tex = _read_link_model(*src), _read_link_model(*src, tex=True)
                        look = {"how": "body only", "why": str(e)[:200]}
                        notes.append(f"Linkle's hair and clothes couldn't be put on her ({str(e)[:120]}): her costume "
                                     "is the Linkle Mod's body only.")
                    catalog["linkle"].update(costume=slot, mod=lm["name"], version=lm["version"], fp=lm["fp"],
                                             mod_on=lm["enabled"], look=look)
                else:
                    model, tex = model_file(romfs, "Link"), model_file(romfs, "Link.Tex")
                if not model or not tex:
                    raise CharselError("Link's model or textures not found")
                try:   # (named as their files, not "Link": the game can't create it otherwise)
                    model, tex, _ = own_names(model, tex, ours(cid))
                except Exception as e:   # (any read failure: kept as it is, said)
                    notes.append(f"{RECIPES[cid].name if cid in RECIPES else cid}'s model couldn't be named as its "
                                 f"own file ({str(e)[:100]}): the game may not create it.")
                models[f"Model/{ours(cid)}.sbfres"], models[f"Model/{ours(cid)}.Tex.sbfres"] = model, tex
                files = make_copy_pack(cid, slot, profile, template, pause)
                res = {"own": {}, "link": {n: c for n, c, _ in glp.ANIMS}}
            else:
                if cid == "linkle" and catalog_note:
                    catalog["skipped"][cid] = catalog_note
                elif cid == "linkle" and not lm:
                    catalog["skipped"][cid] = ("no Linkle mod in your mod library (on or off): her costume can't be "
                                               "built, so friends who pick Linkle show as Link here")
                continue
        except (CharselError, glp.PackError, FormatError, aamp.AampError, OSError) as e:
            catalog["skipped"][cid] = str(e)[:300]
            say(f"  {r.name}: skipped ({e})")
            continue
        data, unpacked = glp.pack_bytes(files)
        problems = check_pack(data, slot)
        if problems:
            catalog["skipped"][cid] = "; ".join(problems)
            say(f"  {r.name}: skipped ({'; '.join(problems)})")
            continue
        packs[slot] = data
        catalog["built"][cid] = {"slot": slot, "profile": profile, "v": r.v, "name": r.name, "files": len(files),
                                 "unpacked": unpacked, "packed": len(data), "own": len(res["own"]),
                                 "link": len(res["link"]), "names": len(res["own"]) + len(res["link"]),
                                 "rest": (rest or r.rest) if r.kind == "pack" else "link", "load_mb": r.load_mb}
        if r.kind == "pack" and need:
            catalog["built"][cid].update(heap={"need": need, "have": heap, "bones": bones,
                                               "cloth": (cl_info or {}).get("bytes", 0)},
                                         link_retarget=link_rt)
        if cl_info is not None:
            catalog["built"][cid]["cloth"] = cl_info
        if r.kind == "pack" and r.legs and legs_info is not None:
            catalog["built"][cid]["legs"] = {k: legs_info[k] for k in ("off", "donor", "vertices", "triangles", "y")
                                             if k in legs_info}
        if r.kind == "pack" and swap:
            catalog["built"][cid]["anim_targets"] = "link-body"
        say(f"  {r.name:16} on {slot:31} {len(files):3} files, {unpacked:6,} B unpacked, own clips "
            f"{len(res['own']):2}, Link's {len(res['link']):2}"
            + (f"{'' if link_rt else ' (not retargeted)'}" if r.kind == "pack" and res["link"] else "")
            + (" (Link body's anim targets)" if r.kind == "pack" and swap else "")
            + (f", cloth {len(cl_info['parts'])} parts" + (f" ({len(cl_info['dropped'])} left out)"
                                                          if cl_info["dropped"] else "")
               if cl_info and cl_info.get("parts") else ""))
    # write: our files in the romfs folder (the kept characters' stay; every other pack or model of ours goes), the
    # catalog, and built.txt (made from the game's files: never exported as a .breath)
    top = out / "romfs"
    keep_slots = {info["slot"] for info in list(keep.values()) + list(imported.values())}
    keep_models = {ours(cid) for cid in list(keep) + list(imported)}
    if top.is_dir():
        for p in sorted(top.rglob("*"), reverse=True):
            if p.is_file() and (p.name.endswith(".sbactorpack") and p.stem in FREE_SLOTS and p.stem not in keep_slots
                                or p.name.startswith("SwChar_") and p.name.split(".")[0] not in keep_models):
                p.unlink()
    for rel, data in [(f"Actor/Pack/{slot}.sbactorpack", d) for slot, d in packs.items()] + list(models.items()):
        t = top / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        if t.exists():
            t.unlink()       # (a new file, never written through a hard link the library may share)
        t.write_bytes(data)
    out.mkdir(parents=True, exist_ok=True)
    repaired = repair_own_names(top, list(imported), {k: v["slot"] for k, v in imported.items()}, say)
    if repaired:
        notes.append(f"{', '.join(repaired)}: the model was still named Link inside, so the game couldn't create it; "
                     "renamed as its own file.")
    catalog["sizes"] = write_sizes(romfs, out, say)   # (0.9.5: the size table, after every file of ours is in place)
    if catalog["sizes"].get("note"):
        notes.append(catalog["sizes"]["note"])
    (out / BUILT_MARK).write_text(BUILT_TEXT, encoding="utf-8")
    (out / "mod.toml").write_text(MOD_TOML, encoding="utf-8")
    (out / CATALOG).write_text(json.dumps(catalog, indent=1) + "\n", encoding="utf-8")
    return catalog


# ------------------------------------------------------------------------------------------- size table (0.9.5)
#
# The round-4 game check: "Linkle did not load (the game could not create it)". Every other costume loads, and its pack
# and the files in it have no size-table (RSTB) entry either; Linkle alone brings a new model file
# (Model/SwChar_linkle.sbfres, 885 KB unpacked) and its textures, which a model needs more memory than its size for
# (Link's own: his entry is 1.58 times his file [V: the game's table]). Since another mod in the library ships a size
# table (the emotes' animations), Savage Wind leaves the table alone (docs/romfs-mods.md, issue #505), so nothing gives
# her model an entry [G: the cause, from the facts above]. So the characters mod now ships its own table, the way a
# BCML install writes one (tools/bnp.py, play-as's logs/rstb.json through modman add): the game's table with an entry
# for every file of ours it lacks: each pack, each file inside a pack that isn't the game's own (its new animation sets,
# lists, actor link), and Linkle's model and textures, each sized from a game file of the same kind as the emotes and
# play-as builds do (ukmm.estimate_entry: the reference's overhead and its entry grown in proportion; a model from
# Link's, the textures as play-as sizes Link's). modman deploy merges it with the other mods' tables (bnp.deploy_hook:
# a table a mod ships is diffed against the game's). Not logs/rstb.json at the mod's top: modman would take a folder
# with logs/ for a BCML package and refuse to deploy it as it is (romfs_mods, mod_inspect); the entries are also in
# charsel.json ("sizes").
RSTB_REL = "System/Resource/ResourceSizeTable.product.srsizetable"
SIZE_PACKS = False   # size entries for the packs broke every costume in game (see size_entries)
SIZE_MODEL_REF = "Model/Link.bfres"        # Linkle's costume is Link's skeleton with more parts: his model is its twin
SIZE_TEX_REF = "Model/Link.Tex.bfres"


def _raw(data: bytes) -> bytes:
    return _unyaz(data) if data[:4] == b"Yaz0" else data


def size_entries(romfs: Path, top: Path, rstb) -> tuple[dict[str, int], dict]:
    """({canonical name: size}, info) for the files of ours under `top` (a built romfs folder) that the game's table
    (`rstb`, bnp.Rstb) has no entry for, or one too small: Actor/Pack/<free name>.sbactorpack and the files inside,
    Model/SwChar_*.sbfres. info: {"refs": {kind: reference}, "model_ratio": entry / size of Link's model}. Reads the
    game's files only."""
    import bnp
    import ukmm
    from mp import ghost_link_pack as glp
    from wiiu_model.convert import RSTB_TEX_EXTRA
    sizes: dict[str, int] = {}
    info: dict = {"refs": {}}
    # references: the template's own pack and the files inside it (the cutscene Sheikah man's: every kind our packs hold)
    tpl_rel = f"Actor/Pack/{TEMPLATE}.sbactorpack"
    tpl_data = glp.find_pack(Path(romfs), TEMPLATE)
    tpl_data = tpl_data.read_bytes() if isinstance(tpl_data, Path) else tpl_data
    refs: dict[str, list] = {}      # extension -> [(unpacked size, entry, name)] of the game's files of that kind
    e = rstb.get(bnp.canon(tpl_rel, tpl_data))
    if e:
        refs["pack"] = [(len(_raw(tpl_data)), e, bnp.canon(tpl_rel, tpl_data))]
    for n, d in sorted(glp.read_pack(tpl_data).items()):
        e = rstb.get(bnp.canon(n, d))
        if e:
            refs.setdefault(n.rsplit(".", 1)[-1], []).append((len(_raw(d)), e, bnp.canon(n, d)))

    def nearest(kind: str, raw_len: int):
        """The game file of that kind nearest ours in size (an entry carries what the game makes from the file: a
        one-clip set needs about what a small set of the game's needs, not what its 48 KB talk set does)."""
        import math
        c = refs.get(kind)
        if not c and kind != "pack":   # (a kind its pack lacks: the nearest of its other parameter files)
            c = [r for k, rs in refs.items() if k != "pack" for r in rs]
        if not c:
            return None
        r = min(c, key=lambda x: (abs(math.log(max(x[0], 1) / max(raw_len, 1))), x[2]))
        info["refs"].setdefault(kind, set()).add(r[2])
        return r

    def need(name: str, raw_len: int, ref, pack: bool) -> None:
        if ref is None:
            info.setdefault("no_reference", []).append(name)
            return
        want = ukmm.estimate_entry(ref[0], ref[1], raw_len, pack)
        have = rstb.get(name)
        if not have or have < want:
            sizes[name] = max(want, have or 0)

    packs = top / "Actor" / "Pack"
    # SIZE_PACKS off (2026-11-14 game check): with entries for the packs and the files inside them, EVERY costume
    # failed to load ("could not create"), even the Hylian; without the table all of them loaded. Only the new models
    # (Linkle's) get entries now.
    for f in sorted(packs.glob("*.sbactorpack")) if packs.is_dir() and SIZE_PACKS else ():
        if f.stem not in FREE_SLOTS:
            continue
        data = f.read_bytes()
        need(bnp.canon(f"Actor/Pack/{f.name}", data), len(_raw(data)), nearest("pack", len(_raw(data))), True)
        for n, d in sorted(glp.read_pack(data).items()):
            cn = bnp.canon(n, d)
            if rstb.get(cn) and not Path(n).stem.startswith("SwChar_") and Path(n).stem != f.stem:
                continue                     # (the game's own file, copied as it is: its entry is there)
            need(cn, len(_raw(d)), nearest(n.rsplit(".", 1)[-1], len(_raw(d))), False)
    models = top / "Model"
    for f in sorted(models.glob("SwChar_*.sbfres")) if models.is_dir() else ():
        data = f.read_bytes()
        cn = bnp.canon(f"Model/{f.name}", data)
        tex = f.name.endswith(".Tex.sbfres")
        ref_name = SIZE_TEX_REF if tex else SIZE_MODEL_REF
        ref_file = model_file(Path(romfs), ref_name[len("Model/"):-len(".bfres")])
        e = rstb.get(ref_name)
        ref = (len(_raw(ref_file)), e) if ref_file and e else None
        if ref and not tex:
            info["model_ratio"] = round(e / ref[0], 3)
            info["refs"]["model"] = {ref_name}
        raw_len = len(_raw(data))
        need(cn, raw_len, ref, False)
        if tex:   # (as play-as sizes Link's textures: the file plus the texture overhead, never less)
            sizes[cn] = max(sizes.get(cn, 0), bnp.round32(raw_len) + RSTB_TEX_EXTRA)
            info["refs"]["tex"] = {ref_name}
    info["refs"] = {k: sorted(v) for k, v in info["refs"].items()}
    return sizes, info


def write_sizes(romfs, out, say=print) -> dict:
    """Write the characters mod's size table (romfs/RSTB_REL: the game's own with size_entries' entries) or remove an
    old one when there is nothing to add or the game's table isn't there. Returns {"entries": {name: size}, "table":
    bool, "note": str}."""
    import bnp
    out = Path(out)
    target = out / "romfs" / Path(RSTB_REL)
    rstb = bnp.load_game_rstb(str(Path(romfs).parent))
    if rstb is None:
        if target.is_file():
            target.unlink()
        return {"entries": {}, "table": False,
                "note": "The game's size table isn't in your extracted romfs: the characters have no size entries "
                        "(Zelda's and an imported avatar's own models may not load)."}
    sizes, info = size_entries(Path(romfs), out / "romfs", rstb)
    if not sizes:
        if target.is_file():
            target.unlink()
        return {"entries": {}, "table": False, "note": "", **info}
    rstb.apply(sizes)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()       # (a new file, never written through a hard link the library may share)
    target.write_bytes(rstb.to_bytes())
    say(f"  size table: {len(sizes)} entries for the characters' files")
    return {"entries": dict(sorted(sizes.items())), "table": True, "note": "", **info}


def check_sizes(out=DEFAULT_OUT) -> list[str]:
    """The shipped size table read back (no game): it reads, and has the catalog's entries at their sizes or more."""
    import bnp
    out = Path(out)
    cat = load_catalog(out)
    want = ((cat.get("sizes") or {}).get("entries") or {}) if cat else {}
    target = out / "romfs" / Path(RSTB_REL)
    if not want:
        return [] if not target.is_file() else [f"{RSTB_REL} is there but the catalog lists no entries"]
    if not target.is_file():
        return [f"{RSTB_REL} is missing ({len(want)} entries listed)"]
    try:
        t = bnp.Rstb(target.read_bytes())
    except bnp.BnpError as e:
        return [f"{RSTB_REL} doesn't read: {e}"]
    return [f"{n}: {t.get(n)} in the table, {v} listed" for n, v in want.items() if (t.get(n) or 0) < v]


def check(out=DEFAULT_OUT) -> dict:
    """Read every built pack back (no game): {id: problems}, plus the catalog's own consistency."""
    out = Path(out)
    cat = load_catalog(out)
    if not cat:
        raise CharselError(f"no {CATALOG} in {out}: build first")
    res = {}
    for cid, info in cat["built"].items():
        p = out / "romfs" / "Actor" / "Pack" / f"{info.get('slot')}.sbactorpack"
        if not p.is_file():
            res[cid] = [f"{p.name} is missing"]
            continue
        bad = check_pack(p.read_bytes(), info.get("slot"))
        if not bad:
            rb = read_back(p.read_bytes())
            if len(rb["names"]) != info.get("names"):
                bad.append(f"{len(rb['names'])} names, the catalog says {info.get('names')}")
            if rb["users"]["ModelUser"] != ours(cid) and RECIPES[cid].kind == "pack":
                bad.append(f"model list {rb['users']['ModelUser']}")
        res[cid] = bad
    sizes = check_sizes(out)            # (0.9.5: the size table, when the catalog lists entries)
    if sizes:
        res["sizes"] = sizes
    return res


# ------------------------------------------------------------------------------------------------ play as (tier 2)
#
# Your own Link becomes the character at the next start, the way The Linkle Mod does it (out/research/
# charsel-play-as.md, option 1): Model/Link.sbfres in your TitleBG.pack becomes Link's model with every one of Link's
# shapes shrunk to one point (his 11 materials and their hide groups stay, so the armor code and his face animations
# still find them) plus the character's body put on Link's exact skeleton (Link's 121 bones first, in Link's order,
# with Link's parents; the body's own bones after them; the body's own bone lengths). Link's 1167 clips turn bones
# only, so they drive the body. Model/Link.Tex.sbfres gets the body's textures beside Link's, and the default hair,
# belt, trousers and pouches (Model/Armor_Default.sbfres) shrink to a point too. With own moves (on unless
# --no-own-moves), the player's sprint, glide or swim plays the character's own clip where the recipe has one
# (Daruk's rolling ball): the player's animation list gets the character's animation file and those animation sets
# point at that clip. Everything is built here from your own files into a BCML-style package (only the changed files
# of TitleBG.pack, and logs/rstb.json); modman add fills the pack from your game, as for any BCML mod.

PLAYAS_ID = "botw-mod-loader.playas"
PLAYAS_VERSION = "0.1.0"
PLAYAS_OUT = REPO / "out" / "charsel-playas"
PLAYAS_INFO = "playas.json"            # in the built folder: what it is (and what playas check reads)
BASE_TID = "01007EF00011E000"
TITLEBG = "Pack/TitleBG.pack"
LINK_MODEL, LINK_TEX, DEFAULT_ARMOR = "Model/Link.sbfres", "Model/Link.Tex.sbfres", "Model/Armor_Default.sbfres"
PLAYER_ACTOR = "GameROMPlayer"
PLAYER_PACK = f"Actor/Pack/{PLAYER_ACTOR}.sbactorpack"
BODY_PREFIX = "SwBody_"     # the body's material names (and any texture name Link's has too) in Link's model
REST_TOL = 1e-4             # metres: a body bone's rest pose may move this much in model space (float rounding)
# The people on Link's skeleton (the study: the 21 main bones with Link's names, parents and rest rotations).
# Monsters are left out: their spine, neck and toes start 20-60 degrees off Link's.
PLAYAS_PEOPLE = ("goron", "zelda", "zora", "gerudo", "rito", "sheikah", "hylian")
# The player's moves the character's own clip takes over (ghost name -> the player's animation sets that play it,
# Link's clip there): only the ones that are the character's own thing. Walk, run and stand stay Link's (the
# player's speed and foot timing are tuned to them).
PLAYER_MOVES = {
    "RunGear2": (("Actor/AS/Player_Move.bas",), "Nml_Move_Dash"),
    "ParashawlGlide": (("Actor/AS/Player_ParashawlGlide.bas",), "Float_Float"),
    "SwimWait": (("Actor/AS/Player_SwimWait.bas", "Actor/AS/Player_SwimMove.bas", "Actor/AS/Player_SwimAtnMove.bas"),
                 "Swim_Wait"),
    "SwimMove": (("Actor/AS/Player_SwimMove.bas",), "Swim_Move_Slow_F"),
    "SwimDash": (("Actor/AS/Player_SwimMove.bas",), "Swim_Move_Fast_F"),
}
PLAYAS_TOML = '''# Play as {name} ("Who am I", docs/charsel.md). Built on your PC from your own game files by tools/charsel.py
# playas: never share or commit this folder. Your friend builds their own.
[mod]
id = "{id}"
name = "Play as {name} (Who am I)"
version = "{version}"
authors = ["giruka"]
description = "Your own Link looks like {name} after a restart: the body on Link's skeleton, Link's moves{own}. Armor shows on top. Turns The Linkle Mod off while it is on. Built from your own game files."
game_versions = ["1.9.0"]
'''


# 4x4 bone math (pure Python: 100-200 bones, no numpy needed). A bone's local transform is T * Rz * Ry * Rx * S.

def _rot3(r) -> list[list[float]]:
    import math
    cx, sx, cy, sy = math.cos(r[0]), math.sin(r[0]), math.cos(r[1]), math.sin(r[1])
    cz, sz = math.cos(r[2]), math.sin(r[2])
    return [[cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
            [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
            [-sy, cy * sx, cy * cx]]


def _local(b) -> list[list[float]]:
    r = _rot3(b.rotation)
    s, t = b.scale, b.translation
    return [[r[i][0] * s[0], r[i][1] * s[1], r[i][2] * s[2], t[i]] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def _mul(a, b) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _inv(m) -> list[list[float]]:
    """Inverse of an affine 4x4 (3x3 part by cofactors)."""
    a = [row[:3] for row in m[:3]]
    c00 = a[1][1] * a[2][2] - a[1][2] * a[2][1]
    c01 = a[1][2] * a[2][0] - a[1][0] * a[2][2]
    c02 = a[1][0] * a[2][1] - a[1][1] * a[2][0]
    det = a[0][0] * c00 + a[0][1] * c01 + a[0][2] * c02
    if abs(det) < 1e-12:
        raise CharselError("a bone has no size (its transform can't be inverted)")
    inv = [[c00 / det, (a[0][2] * a[2][1] - a[0][1] * a[2][2]) / det, (a[0][1] * a[1][2] - a[0][2] * a[1][1]) / det],
           [c01 / det, (a[0][0] * a[2][2] - a[0][2] * a[2][0]) / det, (a[0][2] * a[1][0] - a[0][0] * a[1][2]) / det],
           [c02 / det, (a[0][1] * a[2][0] - a[0][0] * a[2][1]) / det, (a[0][0] * a[1][1] - a[0][1] * a[1][0]) / det]]
    t = [m[i][3] for i in range(3)]
    return [[inv[i][0], inv[i][1], inv[i][2], -sum(inv[i][k] * t[k] for k in range(3))] for i in range(3)] + \
        [[0.0, 0.0, 0.0, 1.0]]


def _worlds(bones) -> list:
    out: list = []
    for b in bones:
        out.append(_local(b) if b.parent < 0 else _mul(out[b.parent], _local(b)))
    return out


def _decompose(m) -> tuple[tuple, tuple, tuple]:
    """(scale, Euler rotation + 1.0, translation) of an affine 4x4 without shear."""
    import math
    sc = [math.sqrt(sum(m[i][j] ** 2 for i in range(3))) for j in range(3)]
    sc = [1.0 if abs(s - 1.0) < 1e-5 else s for s in sc]
    r = [[m[i][j] / sc[j] for j in range(3)] for i in range(3)]
    y = math.asin(max(-1.0, min(1.0, -r[2][0])))
    return tuple(sc), (math.atan2(r[2][1], r[2][2]), y, math.atan2(r[1][0], r[0][0]), 1.0), (m[0][3], m[1][3], m[2][3])


def _gap(a, b) -> float:
    return max(abs(a[i][j] - b[i][j]) for i in range(3) for j in range(4))


def rerig(link_skel, body_skel) -> dict:
    """The body's skeleton put on Link's: Link's bones first, in Link's order, with Link's parents (the body's own
    bone where the name matches, with its own rest lengths, else Link's own rest), then the body's other bones in
    their order. A body bone whose parent changes keeps its rest pose in model space. The body's skin matrices keep
    their order (so its vertex data and inverse bind matrices stay as they are). Returns {"bones", "matrix_to_bone",
    "index" (old body bone index -> new), "reparented", "link_only"}."""
    from wiiu_model.neutral import Bone
    if not (link_skel.flags & 0x1000 and body_skel.flags & 0x1000):
        raise CharselError("only Euler skeletons can be put on Link's (the game's people all are)")
    lnames = [b.name for b in link_skel.bones]
    if len(set(lnames)) != len(lnames):
        raise CharselError("Link's skeleton has a bone name twice")
    src = {b.name: i for i, b in enumerate(body_skel.bones)}
    if len(src) != len(body_skel.bones):
        raise CharselError("the body's skeleton has a bone name twice")
    src_w = _worlds(body_skel.bones)
    order_ = lnames + [b.name for b in body_skel.bones if b.name not in set(lnames)]
    index = {n: i for i, n in enumerate(order_)}
    bones, new_w, reparented, link_only = [], [], [], []
    for k, n in enumerate(order_):
        if k < len(lnames):
            parent = link_skel.bones[k].parent
        else:
            sb = body_skel.bones[src[n]]
            parent = index[body_skel.bones[sb.parent].name] if sb.parent >= 0 else -1
        if n in src:
            sb = body_skel.bones[src[n]]
            b = Bone(n, parent, -1, -1, sb.billboard_index, sb.flags, tuple(sb.scale), tuple(sb.rotation),
                     tuple(sb.translation), list(sb.user_data), dict(sb.extra))
            old_parent = body_skel.bones[sb.parent].name if sb.parent >= 0 else None
            if old_parent != (order_[parent] if parent >= 0 else None):
                rel = _mul(_inv(new_w[parent]), src_w[src[n]]) if parent >= 0 else src_w[src[n]]
                b.scale, b.rotation, b.translation = _decompose(rel)
                b.flags &= ~0x0F000000      # (the scale/rotate/translate shortcut hints: computed in full instead)
                reparented.append(n)
        else:                               # a Link-only bone (belt, sword helpers, face): Link's rest, no skin
            lb = link_skel.bones[k]
            b = Bone(n, parent, -1, -1, lb.billboard_index, lb.flags, tuple(lb.scale), tuple(lb.rotation),
                     tuple(lb.translation), list(lb.user_data), dict(lb.extra))
            link_only.append(n)
        bones.append(b)
        new_w.append(_local(b) if parent < 0 else _mul(new_w[parent], _local(b)))
    old = {i: index[b.name] for i, b in enumerate(body_skel.bones)}
    m2b = [old[bi] for bi in body_skel.matrix_to_bone]
    for mi, bi in enumerate(m2b):
        if mi < body_skel.smooth_count:
            bones[bi].smooth_index = mi
        else:
            bones[bi].rigid_index = mi
    return {"bones": bones, "matrix_to_bone": m2b, "index": old, "reparented": reparented, "link_only": link_only}


def _shrink_model(model) -> int:
    """Every shape of a model shrunk to one point (the Linkle trick): its vertex buffers keep one vertex (skin
    matrix 0) and each mesh draws one empty triangle. Materials, bones and hide groups stay. Returns the shapes."""
    from wiiu_model import formats
    from wiiu_model.neutral import Mesh, VertexBuffer
    from wiiu_model.nx_model import INDEX_SIZE
    done = set()
    for sh in model.shapes:
        vi = sh.vertex_buffer
        if vi not in done:
            vb = model.vertex_buffers[vi]
            bufs = [bytearray(b[:s]) for b, s in zip(vb.buffers, vb.strides)]
            for a in vb.attributes:
                if a.name.startswith("_i"):       # (skin matrix indices: matrix 0, which every skeleton has)
                    n = sum(formats.attr_component_sizes(a.format))
                    bufs[a.buffer][a.offset:a.offset + n] = bytes(n)
            model.vertex_buffers[vi] = VertexBuffer(1, vb.skin_count, list(vb.attributes), list(vb.strides),
                                                    [bytes(b) for b in bufs], dict(vb.extra))
            done.add(vi)
        sh.meshes = [Mesh("triangles", m.index_format, 3, bytes(3 * INDEX_SIZE[m.index_format]), 0,
                          [(0, 3)] * len(m.submeshes), dict(m.extra)) for m in sh.meshes]
    model.total_vertex_count = sum(vb.vertex_count for vb in model.vertex_buffers)
    return len(model.shapes)


def body_on_link(link_res, body_model, tex_count: int, tex_rename: dict | None = None) -> tuple[object, dict]:
    """Link's model file with Link's shapes shrunk to a point and the body (its shapes, vertex buffers, materials
    renamed SwBody_*, bones) on Link's skeleton. tex_count: the textures of the new Link.Tex (TexInfo.txt)."""
    import copy
    from wiiu_model.convert import set_texinfo
    from wiiu_model.neutral import ResFile, Skeleton
    if len(link_res.models) != 1:
        raise CharselError(f"Link's model file has {len(link_res.models)} models, expected 1")
    link = copy.deepcopy(link_res.models[0])
    body = copy.deepcopy(body_model)
    rig = rerig(link.skeleton, body.skeleton)
    _shrink_model(link)
    nv, nm = len(link.vertex_buffers), len(link.materials)
    lmats = {m.name for m in link.materials}
    ren = tex_rename or {}
    for mat in body.materials:
        mat.name = BODY_PREFIX + mat.name
        if mat.name in lmats:
            raise CharselError(f"the body's material {mat.name} clashes with Link's")
        mat.textures = [ren.get(t, t) for t in mat.textures]
    for sh in body.shapes:
        sh.vertex_buffer += nv
        sh.material += nm
        sh.bone = rig["index"][sh.bone]
        sh.skin_bones = [rig["index"][b] for b in sh.skin_bones]
    names = [s.name for s in link.shapes]
    for sh in body.shapes:
        if sh.name in names:
            sh.name = BODY_PREFIX + sh.name
        names.append(sh.name)
    bsk = body.skeleton
    link.skeleton = Skeleton(link.skeleton.flags, rig["bones"], rig["matrix_to_bone"], bsk.smooth_count,
                             bsk.rigid_count, bsk.inverse_matrices, dict(bsk.extra))
    link.vertex_buffers += body.vertex_buffers
    link.shapes += body.shapes
    link.materials += body.materials
    link.total_vertex_count = sum(vb.vertex_count for vb in link.vertex_buffers)
    res = ResFile(link_res.name, [link], [], [], [copy.deepcopy(e) for e in link_res.external], dict(link_res.extra))
    set_texinfo(res, tex_count)
    info = {"bones": len(rig["bones"]), "link_bones": len(link_res.models[0].skeleton.bones),
            "body_bones": len(body_model.skeleton.bones), "reparented": rig["reparented"],
            "link_only": len(rig["link_only"]), "shapes": len(link.shapes), "materials": len(link.materials),
            "vertices": link.total_vertex_count}
    return res, info


def merge_textures(link_tex: bytes, body_tex: bytes, prefix: str = BODY_PREFIX,
                   keep=None) -> tuple[bytes, list[str], dict[str, str]]:
    """Link.Tex with the body's textures after Link's (a body texture named like one of Link's is renamed
    <prefix>*, SwBody_* by default; keep: only the body's textures with these names, e.g. not an outfit's 15 unused
    dye colours). Returns (the file, its texture names, the renames)."""
    from wiiu_model import nx_bfres
    from wiiu_model.neutral import TextureSet
    lt, bt = nx_bfres.read_textures(_unyaz(link_tex)), nx_bfres.read_textures(_unyaz(body_tex))
    if keep is not None:
        bt.textures = [t for t in bt.textures if t.name in keep]
    names = {t.name for t in lt.textures}
    rename = {}
    for t in bt.textures:
        if t.name in names:
            rename[t.name] = prefix + t.name
            t.name = rename[t.name]
        names.add(t.name)
    ts = TextureSet(lt.name, lt.textures + bt.textures, "model", dict(lt.extra))
    return nx_bfres.write_textures(ts), [t.name for t in ts.textures], rename


def shrink_default_armor(data: bytes) -> tuple[bytes, int]:
    """Armor_Default (the hair, belt, trousers and pouches Link wears with no armor) with every shape shrunk."""
    from wiiu_model import nx_model
    res = nx_model.read(_unyaz(data))
    n = sum(_shrink_model(m) for m in res.models)
    return nx_model.write(res), n


def aamp_repoint(data: bytes, mapping: dict[str, str]) -> tuple[bytes, int]:
    """An AAMP file with every text parameter whose text is a key of mapping pointing at its new text (added at the
    end of the string section); every other byte stays. Returns (the file, how many parameters now point elsewhere).
    For the player's big animation sets, which the parameter reader refuses (they share parts on purpose)."""
    import struct
    import aamp
    if len(data) < 0x30 or data[:4] != b"AAMP":
        raise CharselError("not an AAMP file")
    _v, _f, size, _pv, pio, nl, no, npar, dsize, ssize, _u = struct.unpack_from("<11I", data, 4)
    param_at = 0x30 + pio + 12 * nl + 8 * no
    if size != len(data) or param_at + 8 * npar + dsize + ssize != len(data):
        raise CharselError("an AAMP file laid out unlike the game's (its texts are not last)")
    out = bytearray(data)
    extra = bytearray()
    spots: dict[str, int] = {}
    changed = 0
    for k in range(npar):
        p = param_at + 8 * k
        word = struct.unpack_from("<I", data, p + 4)[0]
        t = word >> 24
        if t not in aamp.STRING_TYPES:
            continue
        at = p + (word & 0xFFFFFF) * 4
        end = data.find(b"\0", at)
        new = mapping.get(data[at:end].decode("utf-8", "replace")) if 0 <= at < end else None
        if new is None:
            continue
        cap = aamp.STRING_TYPES[t]
        if cap is not None and len(new.encode()) >= cap:
            raise CharselError(f"{new!r} is too long for its parameter")
        if new not in spots:
            spots[new] = len(data) + len(extra)
            extra += new.encode() + b"\0"
            extra += bytes(-len(extra) % 4)
        struct.pack_into("<I", out, p + 4, ((spots[new] - p) // 4) | (t << 24))
        changed += 1
    out += extra
    struct.pack_into("<I", out, 0x0C, len(out))
    struct.pack_into("<I", out, 0x28, ssize + len(extra))
    return bytes(out), changed


def aslist_add(data: bytes, files: list[tuple[str, bool]], retarget_player: bool = False) -> bytes:
    """The player's animation list with more animation files (after his own; RetargetModel "Skeleton" where asked:
    the game then fits that file's clips to this body, as it does for people who share clips), and with
    retarget_player, Link's own file retargeted too."""
    import aamp
    from aamp import STRING64, Param, PObject, name_hash
    root = aamp.load(data)
    add = root.list("AddReses")
    if add is None or not add.objects:
        raise CharselError("the player's animation list has no animation files")
    have = {o.get_value("Anim"): o for o in add.objects.values()}
    first = next(iter(add.objects.values()))
    at = first.get(name_hash("Anim")).type
    rt = next((o.get(name_hash("RetargetModel")).type for o in add.objects.values()
               if o.get(name_hash("RetargetModel")) is not None), STRING64)
    if retarget_player:
        o = have.get(PLAYER_ANIM)
        if o is None:
            raise CharselError(f"the player's animation list has no {PLAYER_ANIM}")
        o[name_hash("RetargetModel")] = Param(rt, "Skeleton")
    i = len(add.objects)
    for anim, retarget in files:
        if anim in have:
            continue
        while name_hash(f"AddRes_{i}") in add.objects:
            i += 1
        o = PObject()
        o[name_hash("Anim")] = Param(at, anim)
        if retarget:
            o[name_hash("RetargetModel")] = Param(rt, "Skeleton")
        add.objects[name_hash(f"AddRes_{i}")] = o
        have[anim] = o
    return aamp.dump(root)


def player_moves(r: Recipe, clips_of, flags: dict[str, bool] | None = None) -> tuple[dict, dict, list[str]]:
    """The character's own clips for the player's special moves (PLAYER_MOVES): ({animation set: {Link's clip: the
    character's}}, {the character's clip: (its animation file, retarget)}, notes). clips_of(file): its clip names
    (None: unknown). flags: the donor's own retarget per file (else the recipe's)."""
    own = dict(r.own)
    edits: dict[str, dict[str, str]] = {}
    where: dict[str, tuple[str, bool]] = {}
    notes: list[str] = []
    rt = dict(r.anims)
    rt.update(flags or {})
    for ghost, (sets, link_clip) in PLAYER_MOVES.items():
        clip = own.get(ghost)
        if not clip or clip == link_clip:
            continue
        hit = next((f for f, _ in r.anims if f != PLAYER_ANIM and clip in (clips_of(f) or ())), None)
        if hit is None:
            notes.append(f"{ghost}: {clip} isn't in {r.name}'s animation files here (Link's move stays)")
            continue
        for s in sets:
            edits.setdefault(s, {})[link_clip] = clip
        where[clip] = (hit, bool(rt.get(hit, True)))
    return edits, where, notes


def apply_moves(ppack: dict[str, bytes], edits: dict, where: dict, retarget: bool = False
                ) -> tuple[dict[str, bytes], dict[str, str], list[str]]:
    """The player's pack files that change ({name: bytes}), the moves that got the character's clip ({ghost name:
    clip}) and notes. ppack: the files of the game's GameROMPlayer pack."""
    from mp import ghost_link_pack as glp
    _, plt = glp.link_target(ppack, PLAYER_ACTOR)
    asl_name = f"Actor/ASList/{glp.user(plt, 'ASUser')}.baslist"
    changed: dict[str, bytes] = {}
    moves: dict[str, str] = {}
    notes: list[str] = []
    for set_name, mapping in sorted(edits.items()):
        old = ppack.get(set_name)
        short = set_name.rsplit("/", 1)[-1]
        if old is None:
            notes.append(f"the player has no {short}: that move stays Link's")
            continue
        for link_clip, clip in mapping.items():
            new, n = aamp_repoint(old, {link_clip: clip})
            if not n:
                notes.append(f"{short} doesn't play {link_clip}: that move stays Link's there")
                continue
            old = new
            changed[set_name] = new
            for g, (sets, lc) in PLAYER_MOVES.items():
                if lc == link_clip and set_name in sets:
                    moves[g] = clip
    want = list(dict.fromkeys(where[c] for c in moves.values()))
    if want or retarget:
        old = ppack.get(asl_name)
        if old is None:
            raise CharselError("the player's pack has no animation list")
        changed[asl_name] = aslist_add(old, want, retarget)
    return changed, moves, notes


def body_of(romfs: Path, r: Recipe) -> tuple[str, str]:
    """(model file, model name) of a character's body: its donor's model list (the game's own names)."""
    import aamp
    from mp import ghost_link_pack as glp
    if r.id not in PLAYAS_PEOPLE:
        why = ("monsters' skeletons start 20-60 degrees off Link's" if r.cat == "Monsters"
               else "Link and Linkle are the player already: use playas off, or playas linkle")
        raise CharselError(f"{r.name} can't be played as yet ({why})")
    donor = glp.read_pack(glp.find_pack(Path(romfs), r.donor))
    _, lt = glp.link_target(donor, r.donor)
    ml = donor.get(f"Actor/ModelList/{glp.user(lt, 'ModelUser')}.bmodellist")
    md = aamp.load(ml).list("ModelData") if ml else None
    if not (md and md.lists):
        raise CharselError(f"{r.name}'s body is put together from Mii-style parts (no model file of its own): "
                           "not yet")
    d0 = next(iter(md.lists.values()))
    folder = d0.object("Base").get_value("Folder")
    units = d0.list("Unit")
    unit = next(iter(units.objects.values())).get_value("UnitName") if units and units.objects else folder
    if not isinstance(folder, str) or not folder:
        raise CharselError(f"{r.donor}'s model list names no model file")
    return folder, unit if isinstance(unit, str) and unit else folder


def _donor_flags(romfs: Path, r: Recipe) -> dict[str, bool]:
    """The donor's own animation files and whether its list retargets them (the same as make_pack copies)."""
    import aamp
    from mp import ghost_link_pack as glp
    try:
        donor = glp.read_pack(glp.find_pack(Path(romfs), r.donor))
        _, lt = glp.link_target(donor, r.donor)
        asl = donor.get(f"Actor/ASList/{lt.get_value('ASUser')}.baslist")
        add = aamp.load(asl).list("AddReses") if asl else None
    except Exception:   # (the recipe's own flags then)
        return {}
    out = {}
    for o in (add.objects.values() if add else ()):
        a = o.get_value("Anim")
        if isinstance(a, str):
            out[a] = o.get_value("RetargetModel") not in (None, "")
    return out


def _yaz(data: bytes) -> bytes:
    from wiiu_convert import yaz0_compress
    return yaz0_compress(data)


def _game_entry(rstb, name: str) -> int | None:
    try:
        return rstb.get(name) if rstb is not None else None
    except Exception:   # (a damaged table: no entry)
        return None


def build_playas(cid: str, romfs=DEFAULT_ROMFS, out=PLAYAS_OUT, *, own_moves: bool = True, retarget: bool = False,
                 cache=DEFAULT_CACHE, say=print) -> dict:
    """Build the play-as package for one character into `out` (a BCML-style folder: mod.toml, info.json, built.txt,
    playas.json, logs/rstb.json and 01007EF00011E000/romfs/Pack/TitleBG.pack holding only the changed files). Reads
    only the game's own files. Returns playas.json's contents."""
    import bnp
    from mp import ghost_link_pack as glp
    from wiiu_model import checks, nx_model
    from wiiu_model.convert import RSTB_TEX_EXTRA, rstb_model
    romfs, out = Path(romfs).resolve(), Path(out).resolve()
    if out == romfs or romfs in out.parents or out in romfs.parents:
        raise CharselError("--out must be a folder of your own, not inside the game's files")
    title = romfs / "Pack" / "TitleBG.pack"
    if not title.is_file():
        raise CharselError(f"{romfs} isn't the game's extracted romfs (no Pack\\TitleBG.pack)")
    cid = find_id(cid) or cid
    r = RECIPES.get(cid)
    if r is None:
        raise CharselError(f"no character {cid!r} (charsel list)")
    folder, unit = body_of(romfs, r)
    say(f"Play as {r.name}: building from {folder} (your game's files, read only)...")

    def member(name: str) -> bytes:
        d = glp._sarc_member(title, name)
        if d is None:
            raise CharselError(f"your TitleBG.pack has no {name}")
        return d

    link_raw = _unyaz(member(LINK_MODEL))
    link_res = nx_model.read(link_raw)
    body_file, body_tex = model_file(romfs, folder), model_file(romfs, folder + ".Tex")
    if body_file is None or body_tex is None:
        raise CharselError(f"{folder}'s model or textures aren't in your game files")
    body_res = nx_model.read(_unyaz(body_file))
    body = next((m for m in body_res.models if m.name == unit), body_res.models[0] if body_res.models else None)
    if body is None:
        raise CharselError(f"{folder} has no model")
    say("  textures (Link's and the body's in one file)...")
    tex_raw, tex_names, tex_ren = merge_textures(member(LINK_TEX), body_tex)
    say("  the body on Link's skeleton...")
    res, rinfo = body_on_link(link_res, body, len(tex_names), tex_ren)
    model_raw = nx_model.write(res)
    bad = checks.switch_model_problems(model_raw)
    if bad:
        raise CharselError(f"the new Link model fails the self-check: {bad[:3]}")
    armor_old = _unyaz(member(DEFAULT_ARMOR))
    armor_raw, armor_shapes = shrink_default_armor(armor_old)
    bad = checks.switch_model_problems(armor_raw)
    if bad:
        raise CharselError(f"the default armor fails the self-check: {bad[:3]}")

    # own moves: the player's animation list and sets, in his own pack (inside TitleBG.pack)
    notes: list[str] = []
    moves: dict[str, str] = {}
    player_files: dict[str, bytes] = {}
    sizes_in: dict[str, tuple[bytes, bytes]] = {}       # name in the player's pack -> (the game's, ours)
    if own_moves or retarget:
        edits, where = {}, {}
        if own_moves:
            edits, where, mnotes = player_moves(r, ClipCache(romfs, cache).clips, _donor_flags(romfs, r))
            notes += mnotes
        if edits or retarget:
            ppack = glp.read_pack(member(PLAYER_PACK))
            player_files, moves, mnotes = apply_moves(ppack, edits, where, retarget)
            notes += mnotes
            sizes_in = {n: (ppack[n], d) for n, d in player_files.items()}

    # the package: only the changed files of TitleBG.pack (modman add fills the rest from your game)
    say("  packing (Yaz0, a few seconds)...")
    members = [(LINK_MODEL, _yaz(model_raw), 8), (LINK_TEX, _yaz(tex_raw), 8), (DEFAULT_ARMOR, _yaz(armor_raw), 8)]
    if player_files:
        inner = bnp.sarc_build([(n, d, 8) for n, d in sorted(player_files.items())])
        members.append((PLAYER_PACK, _yaz(inner), 8))
    pack = bnp.sarc_build(members)
    # size table entries (logs/rstb.json), from the game's entries for the files they replace
    rstb = bnp.load_game_rstb(str(romfs.parent))
    sizes: dict[str, int] = {}
    count = lambda m: len(m.shapes) + len(m.materials) + len(m.skeleton.bones)   # noqa: E731
    e = _game_entry(rstb, "Model/Link.bfres")
    if e:
        sizes["Model/Link.bfres"] = rstb_model(len(model_raw), count(res.models[0]), e, len(link_raw),
                                               count(link_res.models[0]))
    e = _game_entry(rstb, "Model/Link.Tex.bfres")
    sizes["Model/Link.Tex.bfres"] = max(e or 0, len(tex_raw) + RSTB_TEX_EXTRA)
    for name, (old, new) in sizes_in.items():
        canon_ = bnp.canon(name)
        e = _game_entry(rstb, canon_)
        if not e:
            notes.append(f"the game's size table has no {canon_}")
            continue
        structs = None
        if name.endswith(".baslist"):
            structs = bnp.aslist_growth(bnp.aamp_read(old).root, bnp.aamp_read(new).root) + 0x100
        sizes[canon_] = bnp.needed_size(e, len(old), len(new), False, structs)
    if rstb is None:
        notes.append("the game's size table isn't there: the new files have no size entries")

    # write (our files only; the folder may hold an older build)
    for rel in (f"{BASE_TID}/romfs/{TITLEBG}", "logs/rstb.json", "mod.toml", "info.json", BUILT_MARK, PLAYAS_INFO):
        p = out.joinpath(*rel.split("/"))
        if p.is_file():
            p.unlink()
    info = {"format": 1, "builder": VERSION, "mod": PLAYAS_ID, "version": PLAYAS_VERSION, "id": cid,
            "name": r.name, "body": {"file": folder, "model": unit}, "rig": rinfo, "textures": len(tex_names),
            "texture_renames": tex_ren, "default_armor_shapes": armor_shapes, "own_moves": moves,
            "retarget": bool(retarget), "sizes": sizes,
            "files": {LINK_MODEL: len(model_raw), LINK_TEX: len(tex_raw), DEFAULT_ARMOR: len(armor_raw),
                      **({PLAYER_PACK: sorted(player_files)} if player_files else {})},
            "titlebg_part": len(pack), "notes": notes}
    files = {f"{BASE_TID}/romfs/{TITLEBG}": pack,
             "logs/rstb.json": (json.dumps(dict(sorted(sizes.items())), indent=1) + "\n").encode(),
             "mod.toml": PLAYAS_TOML.format(name=r.name.split(" (")[0], id=PLAYAS_ID, version=PLAYAS_VERSION,
                                            own=(", its own " + ", ".join(sorted(moves))) if moves else "").encode(),
             "info.json": (json.dumps({"name": f"Play as {r.name}", "version": PLAYAS_VERSION,
                                       "desc": "Built on this PC by tools/charsel.py playas. Never share it.",
                                       "platform": "switch", "options": {}}, indent=1) + "\n").encode(),
             BUILT_MARK: BUILT_TEXT.encode(),
             PLAYAS_INFO: (json.dumps(info, indent=1) + "\n").encode()}
    for rel, data in files.items():
        p = out.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return info


def check_playas(out=PLAYAS_OUT, romfs=DEFAULT_ROMFS) -> list[str]:
    """Read a built play-as package back, no game: the model reads and passes the model self-check; Link's 121
    bones come first, in Link's order, with Link's parents; every body bone keeps its rest pose in model space and
    every skin matrix its bone's name; Link's shapes and the default armor are shrunk; TexInfo.txt counts the
    textures and every material's texture is there; the animation sets point at the character's clips; the size
    entries cover the new files. [] = all good."""
    import aamp
    import bnp
    from mp import ghost_link_pack as glp
    from wiiu_model import checks, nx_bfres, nx_model
    from wiiu_model.convert import TEXINFO
    out, romfs = Path(out), Path(romfs)
    try:
        info = json.loads((out / PLAYAS_INFO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"no {PLAYAS_INFO} in {out}: build first (charsel playas <name>)"]
    bad: list[str] = []
    pack_path = out / BASE_TID / "romfs" / "Pack" / "TitleBG.pack"
    try:
        part = bnp.Blob(pack_path.read_bytes()).open()
    except Exception as e:   # (any read failure is the answer)
        return [f"{pack_path.name} doesn't read: {e}"]
    get = lambda n: _unyaz(part[n][0]) if n in part else None   # noqa: E731
    model, tex, armor = get(LINK_MODEL), get(LINK_TEX), get(DEFAULT_ARMOR)
    if model is None or tex is None or armor is None:
        return ["the package lacks the model, its textures or the default armor"]
    for what, data in (("model", model), ("default armor", armor)):
        bad += [f"{what}: {p}" for p in checks.switch_model_problems(data)]
    new = nx_model.read(model).models[0]
    title = romfs / "Pack" / "TitleBG.pack"
    link = nx_model.read(_unyaz(glp._sarc_member(title, LINK_MODEL))).models[0]
    folder, unit = info["body"]["file"], info["body"]["model"]
    bres = nx_model.read(_unyaz(model_file(romfs, folder)))
    body = next((m for m in bres.models if m.name == unit), bres.models[0])
    names = [b.name for b in new.skeleton.bones]
    lnames = [b.name for b in link.skeleton.bones]
    if names[:len(lnames)] != lnames:
        bad.append(f"Link's {len(lnames)} bones are not first, in Link's order")
    for i, lb in enumerate(link.skeleton.bones):
        want = lnames[lb.parent] if lb.parent >= 0 else None
        nb = new.skeleton.bones[i]
        if (names[nb.parent] if nb.parent >= 0 else None) != want:
            bad.append(f"{lb.name}: its parent isn't Link's")
    bnames = [b.name for b in body.skeleton.bones]
    if [names[b] for b in new.skeleton.matrix_to_bone] != [bnames[b] for b in body.skeleton.matrix_to_bone]:
        bad.append("a skin matrix points at another bone")
    if new.skeleton.inverse_matrices != body.skeleton.inverse_matrices:
        bad.append("the inverse bind matrices changed")
    bw, nw = _worlds(body.skeleton.bones), _worlds(new.skeleton.bones)
    worst = max((_gap(nw[names.index(n)], bw[i]) for i, n in enumerate(bnames)), default=0.0)
    if worst > REST_TOL:
        bad.append(f"the body's rest pose moved by up to {worst:.6f} m")
    nl = len(link.shapes)
    if any(new.vertex_buffers[s.vertex_buffer].vertex_count != 1 for s in new.shapes[:nl]):
        bad.append("one of Link's shapes isn't shrunk")
    if [new.vertex_buffers[s.vertex_buffer].vertex_count for s in new.shapes[nl:]] != \
            [body.vertex_buffers[s.vertex_buffer].vertex_count for s in body.shapes]:
        bad.append("the body's shapes changed")
    if [m.name for m in new.materials[:len(link.materials)]] != [m.name for m in link.materials]:
        bad.append("Link's materials changed")
    ts = nx_bfres.read_textures(tex)
    tnames = {t.name for t in ts.textures}
    texinfo = next((e.data for e in nx_model.read(model).external if e.name == TEXINFO), b"")
    if texinfo != str(len(ts.textures)).encode():
        bad.append(f"TexInfo.txt says {texinfo!r}, the textures are {len(ts.textures)}")
    missing = sorted({t for m in new.materials for t in m.textures if t not in tnames})
    if missing:
        bad.append(f"textures missing: {missing[:4]}")
    arm = nx_model.read(armor)
    if any(arm_m.vertex_buffers[s.vertex_buffer].vertex_count != 1 for arm_m in arm.models for s in arm_m.shapes):
        bad.append("the default armor isn't shrunk")
    sizes = info.get("sizes") or {}
    if sizes.get("Model/Link.bfres", 0) < len(model):
        bad.append("no size entry big enough for the model")
    if sizes.get("Model/Link.Tex.bfres", 0) < len(tex):
        bad.append("no size entry big enough for the textures")
    try:
        logged = json.loads((out / "logs" / "rstb.json").read_text(encoding="utf-8"))
        if logged != sizes:
            bad.append("logs/rstb.json differs from playas.json")
    except (OSError, ValueError):
        bad.append("logs/rstb.json doesn't read")
    if info.get("own_moves") or info.get("retarget"):
        ppart = part.get(PLAYER_PACK)
        pfiles = glp.read_pack(ppart[0]) if ppart else {}
        aslists = [n for n in pfiles if n.startswith("Actor/ASList/")]
        if len(aslists) != 1:
            bad.append("the player's animation list isn't in the package")
        else:
            adds = {o.get_value("Anim"): o.get_value("RetargetModel")
                    for o in aamp.load(pfiles[aslists[0]]).list("AddReses").objects.values()}
            if PLAYER_ANIM not in adds:
                bad.append(f"the player's animation list lost {PLAYER_ANIM}")
            if info.get("retarget") and adds.get(PLAYER_ANIM) != "Skeleton":
                bad.append(f"{PLAYER_ANIM} isn't retargeted")
        for ghost, clip in (info.get("own_moves") or {}).items():
            sets, _lc = PLAYER_MOVES[ghost]
            if not any(clip.encode() + b"\0" in pfiles.get(s, b"") for s in sets):
                bad.append(f"no animation set plays {clip} for {ghost}")
    return bad


# your play-as choice, next to the Who am I pick (charsel.json in the launcher's folder)

def load_playas(path: Path | None = None) -> dict:
    """{"id", "library", "turned_off": [...]} of the play-as mod this tool put in a library, or {}."""
    path = Path(path) if path else state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    pa = data.get("playas") if isinstance(data, dict) else None
    if not isinstance(pa, dict) or find_id(pa.get("id")) != pa.get("id") or not isinstance(pa.get("library"), str):
        return {}
    off = pa.get("turned_off")
    return {"id": pa["id"], "library": pa["library"],
            "turned_off": [x for x in off if isinstance(x, str)] if isinstance(off, list) else []}


def save_playas(pa: dict | None, path: Path | None = None) -> Path:
    path = Path(path) if path else state_path()
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        old = old if isinstance(old, dict) else {}
    except (OSError, ValueError):
        old = {}
    old.setdefault("format", 1)
    if pa:
        old["playas"] = pa
    else:
        old.pop("playas", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(old, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _open_library(library):
    import modman as mm
    root = mm.resolve_library(str(library) if library else None)
    lib = mm.Library.load(root)
    if lib is None:
        raise CharselError(f"No mod library in {root} yet. Run: python tools\\modman.py init")
    return lib


def playas_on(cid: str, *, library=None, romfs=DEFAULT_ROMFS, out=PLAYAS_OUT, own_moves: bool = True,
              retarget: bool = False, state: Path | None = None, add: bool = True, say=print) -> dict:
    """Build the play-as mod for a character, put it in the library (last, on: it holds Link's model), turn off any
    other mod that replaces Link's model (The Linkle Mod) and keep what was turned off, for playas off."""
    import modman as mm
    cid = find_id(cid) or cid
    if cid in ("link", "linkle"):
        raise CharselError("Link and Linkle are the player already: use playas off")
    lib = _open_library(library) if add else None
    info = build_playas(cid, romfs, out, own_moves=own_moves, retarget=retarget, say=say)
    bad = check_playas(out, romfs)
    if bad:
        raise CharselError("the built mod fails its check: " + "; ".join(bad[:4]))
    if lib is None:
        return dict(info, added=False, turned_off=[])
    old = load_playas(state)
    turned = [x for x in old.get("turned_off", []) if old.get("library") == lib.root]
    if any(m["id"] == PLAYAS_ID for m in lib.mods):
        mm.remove_mod(lib, PLAYAS_ID)
    say("  adding it to your mods (modman fills TitleBG.pack from your game: about a minute)...")
    try:
        entry, _scan, _notes = mm.add_mod(lib, str(out))
    except mm.ModmanError as e:
        raise CharselError(str(e)) from None
    i = next(k for k, m in enumerate(lib.mods) if m["id"] == entry["id"])
    m = lib.mods.pop(i)
    m["enabled"] = True
    lib.mods.append(m)
    for other in lib.mods:   # (another body for Link: The Linkle Mod. Off while this is on; playas off turns it on)
        if other["id"] != PLAYAS_ID and other.get("enabled") and _link_model_in(Path(lib.mods_dir) / other["id"]):
            other["enabled"] = False
            turned.append(other["id"])
    lib.save()
    names = {x["id"]: x["name"] for x in lib.mods}
    save_playas({"id": cid, "library": lib.root, "turned_off": list(dict.fromkeys(turned))}, state)
    return dict(info, added=True, turned_off=[names.get(x, x) for x in dict.fromkeys(turned)])


def playas_off(*, library=None, state: Path | None = None) -> dict:
    """Take the play-as mod out of the library and turn back on what playas turned off. {"removed", "turned_on"}."""
    import modman as mm
    old = load_playas(state)
    lib = _open_library(library or old.get("library"))
    removed = any(m["id"] == PLAYAS_ID for m in lib.mods)
    if removed:
        mm.remove_mod(lib, PLAYAS_ID)
    on = []
    if old.get("library") in (None, lib.root):
        for m in lib.mods:
            if m["id"] in old.get("turned_off", []) and not m.get("enabled"):
                m["enabled"] = True
                on.append(m["name"])
    lib.save()
    save_playas(None, state)
    return {"removed": removed, "turned_on": on}


def playas_hint(cid: str) -> str:
    """What the launcher and the lobby say about seeing yourself as a character ("" for Link and Linkle): live in the
    game at once (`iam`, the ghost mod's self puppet), or at a restart (charsel playas, the body in your own files)."""
    r = RECIPES.get(cid)
    if r is None or r.kind != "pack":
        return ""
    short = r.name.split(" (")[0]
    if cid not in PLAYAS_PEOPLE:
        return f"You see yourself as {short} live in the game (iam {cid}); no restart way for monsters yet."
    word = playas_word(cid)
    return (f"You see yourself as {short} live in the game (iam {word}). Restart the game to play as {short} "
            f"instead: python tools\\charsel.py playas {word}")


def playas_word(cid: str) -> str:
    """The word to type for a character: its short name when that is one of its aliases (daruk), else its id."""
    r = RECIPES[cid]
    short = r.name.split(" (")[0].lower()
    return short if short in r.aliases else cid


# ------------------------------------------------------------------------------------------------ Linkle by restart
# Stage 1 of out/research/linkle-body-swap-plan.md (2026-11-18). The user, on the old Linkle costume (a puppet that
# copied Link's moves in one outfit): equipped armor did nothing on her and the inventory showed Link, "terribly wrong".
# Now picking Linkle (the launcher's Who am I, or `charsel playas linkle`) turns The Linkle Mod ON as Link's OWN body:
# it replaces Link's player and armor models, so every armor piece shows as hers and the pause menu's inventory shows
# her. It takes one restart (the deploy). Picking anyone else turns it OFF again. Only the mod list changes (modman's
# own on/off); in the game `iam linkle` says whether her body is on (native/examples/ghost/iam.c reads the deploy's
# launch.json). Friends see her as before: her costume (each PC builds it from The Linkle Mod's files, on or off).
LINKLE_ADD = ("The Linkle Mod isn't in your mods. Add it first: on Home press Add mod and pick The Linkle Mod's folder "
              "or .zip (its BotW Switch version), then pick Linkle again.")


def is_linkle_mod(mod_id: str, name: str, library) -> bool:
    """A mod that is The Linkle Mod, the way linkle_mod tells her: "linkle" in its id or name AND its folder in the
    library replaces Link's model (review C2 2026-11-18: by name alone a "Linkle Hair Recolor" add-on counted)."""
    return ("linkle" in f"{mod_id} {name}".lower() and library is not None
            and _link_model_in(Path(library) / "mods" / mod_id) is not None)


def linkle_owned(state: Path | None = None) -> list[str]:
    """The Linkle Mod ids Who am I itself turned on (and so may turn off again: review C3 2026-11-18)."""
    path = Path(state) if state else state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    got = data.get("linkle_owned") if isinstance(data, dict) else None
    return [x for x in got if isinstance(x, str)] if isinstance(got, list) else []


def linkle_switched(plan: dict, on: bool, state: Path | None = None) -> None:
    """After a linkle_plan was applied: keep what it turned on as Who am I's own (C3: picking anyone else turns off
    only that, never a Linkle Mod you turned on yourself on Home), and drop every id it switched from the play-as
    state's turned_off (C7: a later `charsel playas off` doesn't turn The Linkle Mod back on)."""
    import os
    path = Path(state) if state else state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    owned = linkle_owned(path)
    if on:
        owned += [i for i in plan["enable"] if i not in owned]
    else:
        owned = [i for i in owned if i not in plan["disable"]]
    touched = set(plan["enable"]) | set(plan["disable"])
    pa = data.get("playas")
    if not touched and owned == linkle_owned(path):
        return
    data["linkle_owned"] = owned
    if isinstance(pa, dict) and isinstance(pa.get("turned_off"), list):
        pa["turned_off"] = [x for x in pa["turned_off"] if x not in touched]
    data.setdefault("format", 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def linkle_plan(mods, on: bool, library, owned: list[str] | None = None) -> dict:
    """What picking Linkle (on) or anyone else (off) changes in a mod list, read only. mods: the library's list, in
    order ({"id", "name", "enabled"}). On: The Linkle Mod ON (the last one listed) and the play-as mod OFF (it is later
    and would win over her body); off: every Linkle Mod OFF. library: its folder (is_linkle_mod looks at each mod's
    files: other mods with "linkle" in the name are left alone). owned (Who am I: linkle_owned()): off turns off only
    those; None (the command line's explicit `playas link`): every one. {"mod": its entry or None, "enable": [ids],
    "disable": [ids]}: nothing to do when both are empty."""
    mods = [m for m in mods or () if isinstance(m, dict) and isinstance(m.get("id"), str)]
    lk = [m for m in mods if is_linkle_mod(m["id"], str(m.get("name") or ""), library)]
    mod = lk[-1] if lk else None
    if on:
        enable = [mod["id"]] if mod is not None and not mod.get("enabled") else []
        disable = [m["id"] for m in mods if m.get("enabled") and (m["id"] == PLAYAS_ID or m in lk[:-1])]
    else:
        enable, disable = [], [m["id"] for m in lk if m.get("enabled") and (owned is None or m["id"] in owned)]
    return {"mod": mod, "enable": enable, "disable": disable}


def linkle_body(on: bool, *, library=None, state: Path | None = None) -> dict:
    """Linkle's body on (The Linkle Mod ON as Link's own; the play-as mod off) or off, in the library (modman's own
    Library: on/off, then save). {"changed": [names], "mod": The Linkle Mod's name or None}. CharselError (LINKLE_ADD)
    when on and the library has no Linkle Mod. The next deploy (Play) uses it: restart the game."""
    lib = _open_library(library)
    plan = linkle_plan(lib.mods, on, lib.root)     # (the command line: an explicit on/off of every one)
    if on and plan["mod"] is None:
        raise CharselError(LINKLE_ADD)
    changed = []
    for m in lib.mods:
        if m["id"] in plan["enable"] or m["id"] in plan["disable"]:
            m["enabled"] = m["id"] in plan["enable"]
            changed.append(str(m.get("name") or m["id"]))
    if changed:
        lib.save()
        linkle_switched(plan, on, state)
    return {"changed": changed, "mod": str(plan["mod"].get("name") or plan["mod"]["id"]) if plan["mod"] else None}


# ------------------------------------------------------------------------------------------------ command line

def _print_list(cat: dict | None = None) -> None:
    built = (cat or {}).get("built") or {}
    for group, ids in CATEGORIES:
        print(group)
        for cid in ids:
            r = RECIPES[cid]
            st = "built" if cid in built else ("Link body" if r.kind == "link" else "")
            print(f"  {cid:9} {r.name:16} {st:9} {r.looks}")


def _low_priority() -> None:
    """Below normal priority while a big build runs (Windows), so the PC stays usable."""
    if sys.platform == "win32":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)
        except (OSError, AttributeError):
            pass


def _playas_cmd(args) -> int:
    what = (args.what or "").strip().lower()
    if not what:
        pa = load_playas(args.state)
        if not pa:
            print("You play as Link. charsel playas <name> makes you a character from the next start.")
        else:
            print(f"You play as {RECIPES[pa['id']].name} (in {pa['library']}). charsel playas off: back to Link.")
        return 0
    if what == "check":
        bad = check_playas(args.out, args.romfs)
        print("OK" if not bad else "BAD: " + "; ".join(bad))
        return 0 if not bad else 1
    if what in ("off", "link", "linkle", "linkel"):
        res = playas_off(library=args.library, state=args.state)
        if what == "off":
            print("Back to Link at the next start: restart the game." if res["removed"]
                  else "No play-as mod in your library: you play as Link.")
            if res["turned_on"]:
                print(f"{', '.join(res['turned_on'])}: on again.")
            return 0
        try:   # (2026-11-18: Linkle is Link's own body, The Linkle Mod on; link: it off too)
            lk = linkle_body(what != "link", library=args.library, state=args.state)
        except CharselError as e:
            print(str(e))
            return 1
        who = "Linkle (her body: The Linkle Mod on)" if what != "link" else "Link"
        print(f"You play as {who} from the next start: restart the game." if lk["changed"] or res["removed"]
              else f"You play as {who} already.")
        return 0
    cid = find_id(what)
    if cid is None:
        print(f"No character {what!r}. Pick one of: {', '.join(i for i in order() if i in PLAYAS_PEOPLE)}")
        return 1
    _low_priority()
    info = playas_on(cid, library=args.library, romfs=args.romfs, out=args.out, own_moves=not args.no_own_moves,
                     retarget=args.retarget, state=args.state, add=not args.no_add)
    short = RECIPES[cid].name.split(" (")[0]
    if info["added"]:
        print(f"Done. Restart the game to play as {short}.")
    else:
        print(f"Built and checked in {args.out} (not added: --no-add).")
    if info["own_moves"]:
        print("  Own moves: " + ", ".join(f"{g} -> {c}" for g, c in info["own_moves"].items()))
    for name in info["turned_off"]:
        print(f"  {name} is off while you play as {short}. charsel playas off turns it back on.")
    print("  Armor you wear shows on top (it is cut for Link's body).")
    for n in info["notes"]:
        print(f"  note: {n}")
    return 0


RECIPE_TOML = REPO / "assets" / "characters" / "mod.toml"   # (build --add: the mod.toml the library copy gets)


def _build_add(args) -> int:
    """charsel build --add (Breathalyzer Plus' Characters recipe, the launcher's --library last): build from the
    library's own Savage Wind into a temp folder, give it the recipe's mod.toml ([mod] part_of the multiplayer mod),
    and put it in the library ON. A rebuild replaces the old copy and keeps its ON/OFF and its place."""
    import shutil
    import tempfile
    import modman as mm
    lib = _open_library(args.library)
    romfs = args.romfs or (Path(lib.install) / "extracted" / "romfs" if lib.install else DEFAULT_ROMFS)
    _low_priority()
    tmp = Path(tempfile.mkdtemp(prefix="charsel-build-"))
    out = args.out or tmp / MOD_ID
    try:
        print("Building the characters from your own game files (a few minutes)...")
        cat = build(romfs, out, args.ids, library=lib.root, slots=parse_slots(args.slot),
                    model_copies=args.model_copies, monster_link=args.monster_link_clips,
                    link_retarget=args.link_retarget, cache=args.cache or tmp / "cache.json",
                    cloth=getattr(args, "cloth", "on"))
        if not cat["built"]:
            print(f"No character could be built ({len(cat['skipped'])} skipped).")
            return 1
        if RECIPE_TOML.is_file():
            import tomllib
            text = RECIPE_TOML.read_text(encoding="utf-8")
            if (tomllib.loads(text).get("mod") or {}).get("id") != MOD_ID:
                raise CharselError(f"{RECIPE_TOML.name} isn't the characters' mod.toml")
            (out / "mod.toml").write_text(text, encoding="utf-8")
        idx = next((i for i, m in enumerate(lib.mods) if m["id"] == MOD_ID), None)
        was_on = lib.mods[idx]["enabled"] if idx is not None else True
        if idx is not None:
            mm.remove_mod(lib, MOD_ID)
        print("Adding them to your mods...")
        entry, _scan, _notes = mm.add_mod(lib, str(out))
        i = next(k for k, m in enumerate(lib.mods) if m["id"] == entry["id"])
        m = lib.mods.pop(i)
        m["enabled"] = was_on
        lib.mods.insert(len(lib.mods) if idx is None else min(idx, len(lib.mods)), m)
        lib.save()
    except mm.ModmanError as e:
        raise CharselError(str(e)) from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"Built {len(cat['built'])} characters: {', '.join(RECIPES[c].name.split(' (')[0] for c in cat['built'])}."
          + (f" Not built: {', '.join(cat['skipped'])}." if cat["skipped"] else ""))
    return 0


# Each body's flesh around its main bones, for the live costume's ragdoll (ghost mod 0.9.1, puppet.c, docs/charsel.md
# "Ragdoll"): per bone, the box its vertices fill in that bone's own frame at rest (cm: x-, x+, y-, y+, z-, z+). A vertex
# counts for its heaviest bone, or the nearest of these above it (a hand for the wrist, the skirt for the hips).
# The DLL turns the boxes with the bones each frame to find the body's lowest point, so a big body (Daruk: a torso
# about 2 m wide) lies ON the ground instead of through it. `charsel bodies` writes them from your own game files.
BODY_BONES = ("Skl_Root", "Spine_1", "Spine_2", "Head", "Arm_1_L", "Arm_2_L", "Wrist_L", "Arm_1_R", "Arm_2_R", "Wrist_R",
              "Leg_1_L", "Leg_2_L", "Ankle_L", "Leg_1_R", "Leg_2_R", "Ankle_R")
BODY_HEADER = REPO / "native" / "examples" / "ghost" / "pup_bodies.h"
BODY_TRIM = 0.02           # each side drops the outermost 2 % of a bone's vertices (a stray strap or effect)
EULER_ROT = 0x1000         # FSKL flags: rotations are Euler XYZ (the bone math here)


def _attr_values(fmt: int, raw: bytes, off: int) -> list[float]:
    """The components of one vertex attribute (nn::gfx format) as numbers."""
    import struct
    from wiiu_model import formats
    typ, out = fmt & 0xFF, []
    for size in formats.attr_component_sizes(fmt):
        b = raw[off:off + size]
        off += size
        if typ == 5:
            out.append(struct.unpack("<f" if size == 4 else "<e", b)[0])
        elif typ == 1:
            out.append(int.from_bytes(b, "little") / ((1 << (8 * size)) - 1))
        elif typ == 2:
            out.append(max(-1.0, int.from_bytes(b, "little", signed=True) / ((1 << (8 * size - 1)) - 1)))
        else:
            out.append(int.from_bytes(b, "little", signed=typ == 4))
    return out


def body_boxes(model, trim: float = BODY_TRIM) -> dict[str, list[int]]:
    """{bone: [x-, x+, y-, y+, z-, z+] in cm} for the BODY_BONES a model has (made-up or the game's)."""
    sk = model.skeleton
    if not sk.flags & EULER_ROT:
        raise CharselError("the skeleton's turns aren't Euler (not read here)")
    worlds = _worlds(sk.bones)
    index = {b.name: i for i, b in enumerate(sk.bones)}
    inv = {n: _inv(worlds[index[n]]) for n in BODY_BONES if n in index}
    pts: dict[str, list] = {n: [] for n in inv}

    def apply(m, p):
        return [sum(m[r][k] * p[k] for k in range(3)) + m[r][3] for r in range(3)]

    for sh in model.shapes:
        vb = model.vertex_buffers[sh.vertex_buffer]
        at = {a.name: a for a in vb.attributes}
        if "_p0" not in at:
            continue

        def get(name, v):
            a = at[name]
            return _attr_values(a.format, vb.buffers[a.buffer], v * vb.strides[a.buffer] + a.offset)

        for v in range(vb.vertex_count):
            p = get("_p0", v)[:3]
            if sh.skin_count == 0:        # (rigid shape: on its bone, in that bone's frame)
                bone = sh.bone
                p = apply(worlds[bone], p)
            else:
                ii = get("_i0", v)[:sh.skin_count]
                ww = get("_w0", v) if "_w0" in at else [1.0]
                k = max(range(len(ii)), key=lambda j: ww[j] if j < len(ww) else 0.0)
                bone = sk.matrix_to_bone[int(ii[k])]
                if sh.skin_count == 1:    # (one bone each: in that bone's frame, as the game stores it)
                    p = apply(worlds[bone], p)
            b = bone
            while b >= 0 and sk.bones[b].name not in inv:
                b = sk.bones[b].parent
            if b >= 0:
                pts[sk.bones[b].name].append(apply(inv[sk.bones[b].name], p))
    out = {}
    for name, ps in pts.items():
        if not ps:
            continue
        box = []
        for ax in range(3):
            vals = sorted(q[ax] for q in ps)
            lo, hi = vals[int(trim * (len(vals) - 1))], vals[int((1 - trim) * (len(vals) - 1))]
            box += [round(min(lo, 0.0) * 100), round(max(hi, 0.0) * 100)]
        out[name] = box
    return out


def body_model(romfs: Path, cid: str) -> tuple[str, str] | None:
    """(model file, model name) a character is drawn with: Link's own for link, the donor's for a pack; None for a
    body put together from Mii-style parts (the Hylian)."""
    if cid == "link":
        return "Link", "Link"
    r = RECIPES[cid]
    if r.id in PLAYAS_PEOPLE:
        try:
            return body_of(romfs, r)
        except CharselError:
            return None
    import aamp
    from mp import ghost_link_pack as glp
    donor = glp.read_pack(glp.find_pack(Path(romfs), r.donor))
    _, lt = glp.link_target(donor, r.donor)
    ml = donor.get(f"Actor/ModelList/{glp.user(lt, 'ModelUser')}.bmodellist")
    md = aamp.load(ml).list("ModelData") if ml else None
    if not (md and md.lists):
        return None
    d0 = next(iter(md.lists.values()))
    folder = d0.object("Base").get_value("Folder")
    units = d0.list("Unit")
    unit = next(iter(units.objects.values())).get_value("UnitName") if units and units.objects else folder
    return (folder, unit) if isinstance(folder, str) and folder else None


# Each body's hand and weapon bones, for the costume's held items (the "character weapons" job): the bones a held
# weapon, shield or bow can sit on, where each rests in its parent's frame, and the finger bones of each hand (for a
# closed fist). Every person and monster here has Wrist_L/R and Weapon_L/R (Weapon_* a child of Wrist_*); Pod_A/B
# (the back and waist places) all but Zelda's model and the monsters; Link alone has Pod_C and Weapon_Root [V: the
# game's models, `charsel.py bodies`].
HAND_BONES = ("Wrist_L", "Wrist_R", "Weapon_L", "Weapon_R", "Pod_A", "Pod_B", "Pod_C", "Weapon_Root")


def hand_bones(model) -> dict:
    """{"bones": {name: {"parent", "place" (cm), "turn" (degrees, Euler XYZ)}} for the HAND_BONES a model has, at rest
    in the parent's frame; "fingers": {"L": [names], "R": [names]} (the bones under each wrist named Finger*); "hip":
    the hips' (Skl_Root's) height at rest, m}. (A Link clip played WITHOUT the retarget puts a body's hips at Link's
    height, 0.994 m at rest: Mipha's rest at 0.751 then hangs 0.24 m up, Urbosa's at 1.343 sinks 0.35 m.)"""
    import math
    sk = model.skeleton
    if not sk.flags & EULER_ROT:
        raise CharselError("the skeleton's turns aren't Euler (not read here)")
    bones = sk.bones
    root = next((i for i, b in enumerate(bones) if b.name == "Skl_Root"), None)
    out: dict = {"bones": {}, "fingers": {"L": [], "R": []},
                 "hip": round(_worlds(bones)[root][1][3], 3) if root is not None else 0.0}
    for b in bones:
        if b.name in HAND_BONES:
            out["bones"][b.name] = {"parent": bones[b.parent].name if b.parent >= 0 else "",
                                    "place": [round(t * 100, 1) for t in b.translation[:3]],
                                    "turn": [round(math.degrees(r), 1) for r in b.rotation[:3]]}
    for side in "LR":
        wrist = next((i for i, b in enumerate(bones) if b.name == f"Wrist_{side}"), None)
        for i, b in enumerate(bones):
            p = b.parent
            while p >= 0 and p != wrist:
                p = bones[p].parent
            if wrist is not None and p == wrist and b.name.startswith("Finger"):
                out["fingers"][side].append(b.name)
    return out


def hands_header(hands: dict[str, dict]) -> str:
    """The hand bones as C (an addition to pup_bodies.h, compiled only with PUP_WANT_HANDS defined before it)."""
    n = len(HAND_BONES)
    lines = ["",
             "// Hand and weapon bones (charsel.py bodies), for the held items: per body, each bone's place (cm) and turn",
             "// (degrees, Euler XYZ) at rest in its parent's frame (has 0: no such bone), how many finger bones each",
             "// hand has (left, right), and the hips' (Skl_Root's) height at rest (m; a Link clip played without the",
             "// retarget puts them at Link's). Define PUP_WANT_HANDS before including this file to get the table.",
             "#ifdef PUP_WANT_HANDS", f"#define PUP_NHAND_BONES {n}",
             "static const char *const PUP_HAND_BONES[PUP_NHAND_BONES] = {",
             "    " + ", ".join(f'"{b}"' for b in HAND_BONES) + ",", "};",
             "typedef struct {", "    const char *id;", "    signed char has[PUP_NHAND_BONES];",
             "    float place[PUP_NHAND_BONES][3], turn[PUP_NHAND_BONES][3];", "    signed char fingers[2];",
             "    float hip;", "} PupHands;", "static const PupHands PUP_HANDS[] = {"]
    for cid in sorted(hands, key=lambda c: (c != "link", c)):
        h = hands[cid]["bones"]
        has = ", ".join("1" if b in h else "0" for b in HAND_BONES)
        place = ", ".join("{" + ", ".join(f"{v:.1f}f" for v in h.get(b, {}).get("place", [0, 0, 0])) + "}"
                          for b in HAND_BONES)
        turn = ", ".join("{" + ", ".join(f"{v:.1f}f" for v in h.get(b, {}).get("turn", [0, 0, 0])) + "}"
                         for b in HAND_BONES)
        f = hands[cid]["fingers"]
        lines += [f'    {{"{cid}", {{{has}}},', f"     {{{place}}},", f"     {{{turn}}},",
                  f"     {{{len(f['L'])}, {len(f['R'])}}}, {hands[cid].get('hip', 0.0):.3f}f}},"]
    lines += ["};", "#endif", ""]
    return "\n".join(lines)


def cloth_header(cloth: dict[str, list[str]]) -> str:
    """The bones each body's own physics moves, as C (an addition to pup_bodies.h, after the hands): PUP_CLOTH, one
    string of names per body, each name followed by one space (puppet.c pup_cloth_has), then an end entry."""
    lines = ["",
             "// Bones each body's own cloth and support-bone physics move (charsel.py bodies: the names in its pack's",
             "// Physics files that are bones of its model, less the ones named as Link's: the anchors its pose sets).",
             "// puppet.c leaves them to the physics: the ragdoll pose and the hands never write them (no fight, no",
             "// jitter). Names only.",
             "typedef struct {", "    const char *id;", "    const char *bones;  // each name followed by one space",
             "} PupCloth;", "static const PupCloth PUP_CLOTH[] = {"]
    for cid in sorted(cloth):
        rows, row = [], ""
        for n in cloth[cid]:
            if row and len(row) + len(n) + 1 > 96:
                rows.append(row)
                row = ""
            row += n + " "
        rows = rows + [row] if row else rows
        lines.append(f'    {{"{cid}",')
        lines += [f'     "{r}"' for r in rows[:-1]] + ([f'     "{rows[-1]}"}},'] if rows else ['     ""},'])
    lines += ["    {0, 0},", "};", ""]
    return "\n".join(lines)


def bodies_header(boxes: dict[str, dict[str, list[int]]], hands: dict[str, dict] | None = None,
                  cloth: dict[str, list[str]] | None = None) -> str:
    """pup_bodies.h: the C table puppet.c reads ({id: {bone: box}}; link first), then (hands given) the hand bones,
    then (cloth given) the bones each body's physics moves."""
    lines = ["// Made by python tools\\charsel.py bodies from the game's own models (numbers only): each body's flesh around",
             "// its main bones, the box its vertices fill in the bone's own frame at rest, cm (x-, x+, y-, y+, z-, z+).",
             "// puppet.c turns them with the bones during a ragdoll to keep the costume's lowest point on the ground.",
             "// A body with no entry (the Hylian: Mii-style parts) uses Link's, scaled to its spine.",
             f"#define PUP_NBODY_BONES {len(BODY_BONES)}",
             "static const char *const PUP_BODY_BONES[PUP_NBODY_BONES] = {"]
    for k in range(0, len(BODY_BONES), 8):
        lines.append("    " + ", ".join(f'"{n}"' for n in BODY_BONES[k:k + 8]) + ",")
    lines += ["};", "typedef struct {", "    const char *id;", "    signed char has[PUP_NBODY_BONES];",
              "    short box[PUP_NBODY_BONES][6];", "} PupBody;", "static const PupBody PUP_BODIES[] = {"]
    for cid in sorted(boxes, key=lambda c: (c != "link", c)):
        b = boxes[cid]
        has = ", ".join("1" if n in b else "0" for n in BODY_BONES)
        lines.append(f'    {{"{cid}", {{{has}}}, {{')
        rows = ["{" + ", ".join(str(v) for v in b.get(n, [0] * 6)) + "}" for n in BODY_BONES]
        for k in range(0, len(rows), 3):
            lines.append("        " + ", ".join(rows[k:k + 3]) + ",")
        lines.append("    }},")
    lines.append("};")
    return "\n".join(lines) + "\n" + (hands_header(hands) if hands else "") + (cloth_header(cloth) if cloth else "")


def build_bodies(romfs: Path = DEFAULT_ROMFS, out: Path = BODY_HEADER, hands: dict | None = None,
                 cloth: dict | None = None) -> dict[str, dict[str, list[int]]]:
    """Measure Link and every pack body from the game's files and write pup_bodies.h (the body boxes, then the hand
    bones, then the bones each body's own physics moves). Read-only on the game. hands, cloth: dicts to fill with each
    body's hand_bones() and physics_bones()."""
    from wiiu_model import nx_model
    from mp import ghost_link_pack as glp
    got: dict[str, dict[str, list[int]]] = {}
    hand: dict[str, dict] = {} if hands is None else hands
    moved: dict[str, list[str]] = {} if cloth is None else cloth
    link_bones: list[str] = []
    for cid in ["link"] + [c for c, r in RECIPES.items() if r.kind == "pack"]:
        where = body_model(romfs, cid)
        data = model_file(romfs, where[0]) if where else None
        if not data:
            continue
        res = nx_model.read(_unyaz(data))
        model = next((m for m in res.models if m.name == where[1]), res.models[0])
        got[cid] = body_boxes(model)
        hand[cid] = hand_bones(model)
        if cid == "link":
            link_bones = [b.name for b in model.skeleton.bones]
        elif RECIPES[cid].cloth:   # (the donor's cloth and support-bone files: the bones they move on this model)
            r = RECIPES[cid]
            donor = glp.read_pack(glp.find_pack(Path(romfs), r.donor))
            phys, files, _parts, _note = pack_cloth(r, donor)
            if phys is not None:
                moved[cid] = physics_bones(files, [b.name for b in model.skeleton.bones], link_bones)
    if "link" not in got:
        raise CharselError("Link's model isn't in your game files: nothing written")
    Path(out).write_text(bodies_header(got, hand, moved), encoding="utf-8", newline="\n")
    return got


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Who am I: character packs and the wheel (docs/charsel.md).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="the characters on the wheel").add_argument("--out", type=Path, default=DEFAULT_OUT)
    b = sub.add_parser("build", help="build the character packs from your own game files")
    b.add_argument("ids", nargs="*", help="characters (default: all)")
    b.add_argument("--romfs", type=Path, help=f"default: the library's Savage Wind with --add, else {DEFAULT_ROMFS}")
    b.add_argument("--out", type=Path, help=f"default: a temp folder with --add, else {DEFAULT_OUT}")
    b.add_argument("--library", type=Path, default=DEFAULT_LIBRARY if DEFAULT_LIBRARY.is_dir() else None)
    b.add_argument("--add", action="store_true",
                   help="then put it in your mod library, ON, used only while Ghost multiplayer is on (Breathalyzer "
                        "Plus' Characters recipe: assets/characters/mod.toml)")
    b.add_argument("--slot", action="append", metavar="ID=ACTOR")
    b.add_argument("--model-copies", action="store_true")
    b.add_argument("--monster-link-clips", action="store_true")
    b.add_argument("--link-retarget", choices=LINK_RETARGET, default="auto",
                   help="fit Link's clips to each body: by the heap estimate (auto), always (on) or never (off)")
    b.add_argument("--cache", type=Path, help=f"default {DEFAULT_CACHE} (with --add: none kept)")
    b.add_argument("--cloth", choices=CLOTH_SWITCH, default="on",
                   help="skirts, hair and capes swing (the characters' own cloth physics, cloth only), or off: none")
    c = sub.add_parser("check", help="read every built pack back")
    c.add_argument("--out", type=Path, default=DEFAULT_OUT)
    w = sub.add_parser("wheel", help="pick who you are on the wheel (a window)")
    w.add_argument("--state", type=Path)
    w.add_argument("--out", type=Path, help="the built characters' folder (default: found)")
    p = sub.add_parser("pick", help="pick who you are without a window")
    p.add_argument("id")
    p.add_argument("--state", type=Path)
    sub.add_parser("where", help="your choice and where it is kept").add_argument("--state", type=Path)
    pa = sub.add_parser("playas", help="play as a character yourself, from the next start (or off, or check)")
    pa.add_argument("what", nargs="?", help="a character (daruk, zelda, mipha...), off, or check")
    pa.add_argument("--romfs", type=Path, default=DEFAULT_ROMFS)
    pa.add_argument("--library", type=Path, help="your mod library (default: modman's)")
    pa.add_argument("--out", type=Path, default=PLAYAS_OUT)
    pa.add_argument("--state", type=Path)
    pa.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    pa.add_argument("--no-add", action="store_true", help="build only (and check); don't touch the library")
    pa.add_argument("--no-own-moves", action="store_true", help="Link's moves only (no rolling sprint, wings...)")
    pa.add_argument("--retarget", action="store_true",
                    help="fit Link's clips to the body (the game's retarget; hips at the body's height) [G]")
    bo = sub.add_parser("bodies", help="measure each body's flesh for the costume's ragdoll (writes pup_bodies.h)")
    bo.add_argument("--romfs", type=Path, default=DEFAULT_ROMFS)
    bo.add_argument("--out", type=Path, default=BODY_HEADER)
    iv = sub.add_parser("import-vrm", help="a VRM avatar (VRoid...) as a costume on Link's skeleton: iam custom")
    iv.add_argument("vrm", type=Path)
    iv.add_argument("--id", default="custom", help="its name in the files (SwChar_<id>)")
    iv.add_argument("--romfs", type=Path, help=f"default: the library's Savage Wind with --library, else {DEFAULT_ROMFS}")
    iv.add_argument("--out", type=Path, help=f"the characters mod folder (default {DEFAULT_OUT})")
    iv.add_argument("--library", type=Path, help="put it in this mod library's characters mod instead")
    iv.add_argument("--skin", choices=("link", "zelda", "zelda-fed"),
                    help="the parts' material (default link: vrm_import.SKIN_MATERIAL; the others for a game check)")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "import-vrm":
            import vrm_import as vi
            _low_priority()
            if args.skin:
                vi.SKIN_MATERIAL = args.skin
            got = (vi.import_into_library(args.vrm, args.id, args.library, args.romfs) if args.library
                   else vi.import_vrm(args.vrm, args.id, args.romfs, args.out))
            print(f"Imported {args.vrm.name}: {got['shapes']} parts, {got['vertices']:,} vertices, {got['textures']} "
                  f"textures, on {got['slot']}." + (f" Left out: {', '.join(got['left_out'])}." if got['left_out']
                                                    else ""))
            print(f"In the game (after modman deploy): iam {vi.IMPORT_ID}" +
                  "".join(f" (or iam {a})" for a in vi.IMPORT_ALIASES) +
                  ". Not done: spring bones, blend shapes, MToon outline.")
            return 0
        if args.cmd == "playas":
            return _playas_cmd(args)
        if args.cmd == "bodies":
            hands: dict = {}
            moved: dict = {}
            got = build_bodies(args.romfs, args.out, hands, moved)
            for cid, b in got.items():
                h = hands.get(cid, {"bones": {}, "fingers": {"L": [], "R": []}})
                print(f"  {cid:9} {len(b)} bones; hands: " + ", ".join(
                    f"{n} (on {v['parent']})" for n, v in h["bones"].items())
                      + f"; fingers L {len(h['fingers']['L'])}, R {len(h['fingers']['R'])}; hips at "
                      f"{h.get('hip', 0.0):.3f} m"
                      + (f"; its physics moves {len(moved[cid])} bones" if cid in moved else ""))
            print(f"Written: {args.out}. Rebuild the ghost mod: python tools\\build_native.py build")
            return 0
        if args.cmd == "list":
            _print_list(load_catalog(args.out))
            return 0
        if args.cmd == "build" and args.add:
            return _build_add(args)
        if args.cmd == "build":
            args.romfs, args.out = args.romfs or DEFAULT_ROMFS, args.out or DEFAULT_OUT
            print(f"Building from {args.romfs} into {args.out}:")
            cat = build(args.romfs, args.out, args.ids, library=args.library, slots=parse_slots(args.slot),
                        model_copies=args.model_copies, monster_link=args.monster_link_clips,
                        link_retarget=args.link_retarget, cache=args.cache or DEFAULT_CACHE, cloth=args.cloth)
            print(f"Built {len(cat['built']) - len(cat['kept'])}, kept {len(cat['kept'])}, skipped "
                  f"{len(cat['skipped'])}. Linkle: {cat['linkle']['how']}.")
            print(f"Next: python tools\\modman.py add {args.out} (once), then Play or deploy: only deployed "
                  "characters are shown.")
            for cid, why in cat["skipped"].items():
                print(f"  skipped {cid}: {why}")
            for n in cat["notes"]:
                print(f"  note: {n}")
            return 0 if cat["built"] else 1
        if args.cmd == "check":
            res = check(args.out)
            for cid, bad in res.items():
                print(f"  {cid:9} {'OK' if not bad else 'BAD: ' + '; '.join(bad)}")
            return 0 if all(not b for b in res.values()) else 1
        if args.cmd == "pick":
            cid = find_id(args.id)
            if cid is None:
                print(f"No character {args.id!r}. Pick one of: {', '.join(order())}")
                return 1
            save_choice(cid, args.state)
            print(f"Done. Others see you as {RECIPES[cid].name.split(' (')[0]}.")
            return 0
        if args.cmd == "where":
            path = args.state or state_path()
            ch = load_choice(path)
            print(f"You: {RECIPES[ch['look']].name} ({path})")
            return 0
        ch = load_choice(args.state)
        cat, _where = here_catalog(args.out or ch["packs"])
        got = choose_window(ch["look"], cat.get("built") if cat else None, linkle_ready(cat))
        if got:
            save_choice(got, args.state)
            print(f"Done. Others see you as {RECIPES[got].name.split(' (')[0]}.")
        return 0
    except CharselError as e:
        print(f"charsel: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
