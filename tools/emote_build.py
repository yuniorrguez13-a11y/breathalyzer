#!/usr/bin/env python3
"""Emotes (a Breathalyzer mod by giruka): builds Link's emote animations on your PC from our own keyframes
(tools/emotes/*.json) and your own game files, and adds them to your mod library with modman.

    python tools/emote_build.py build [--library DIR] [--game DIR] [--only a,b] [--no-add --out DIR] [--optional]
                                      [--quiet | --verbose]
    python tools/emote_build.py check [--game DIR]          offline: lint, solve, pose checks, write and read back
                                                            (writes nothing)
    python tools/emote_build.py lint                        the keyframe files only (no game files needed)
    python tools/emote_build.py preview <id|all> --out F    stick figures (front and side) as a PNG (or .svg) file;
                                                            never opens a window. With all: --out is a folder
    python tools/emote_build.py list                        id, name, page, frames, loop, kind
    python tools/emote_build.py header                      (developers) writes native/examples/emotes/emote_list.h

What `build` makes: one package mod, giruka.emotes-anims ([mod] part_of botw-mod-loader.emotes, secret: it shows as
the Emotes mod's card and follows it on and off). Everything in it is made here from the player's own
extracted\\romfs (only read); nothing from the game is in this repo:

  Pack/TitleBG.pack//Model/SwEmote_Animation.sbfres     our clips SwEmote_<id> (Switch skeletal animations, written
                                                         with tools/wiiu_model) next to a copy of the game's dummy
                                                         'Skeleton' model, as the game's own animation archives are
  Pack/TitleBG.pack//Actor/Pack/GameROMPlayer.sbactorpack//Actor/AS/Player_ZEmote_<id>.bas
                                                         one animation set per emote: one clip node (our clip or a
                                                         game clip by name), or a byte copy of a game set
  logs/aslist.aamp                                       Link's animation list change: our archive + the sets
                                                         ZEmote_<id>, sorted (the game looks names up with a binary
                                                         search; bnp.py's merge appends, and Z sorts last)
  logs/rstb.json                                         size table entries for the new files
  emotes.json                                            what was built (ids, set and clip names, frames): the
                                                         native mod, ghosts and character packs read names from it

The Linkle Mod adds its own archive to the same list the same way; modman's aslist merge keeps both.

Keyframe files: docs/emotes.md ("Make your own emote") describes the format. Axes are Link's model space: x toward
Link's LEFT, y up, z forward, metres from the ground between his feet; angles in degrees.

Pure Python (no third-party packages). Runs at below-normal priority.
"""
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import json
import math
import os
import re
import shutil
import struct
import sys
import tempfile
import traceback
import zlib
from dataclasses import dataclass, field

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import aamp  # noqa: E402
import bnp  # noqa: E402

REPO = os.path.dirname(TOOLS)
DATA = os.path.join(TOOLS, "emotes")
HEADER = os.path.join(REPO, "native", "examples", "emotes", "emote_list.h")
VERSION = "0.1.0"
BASE_TID = "01007EF00011E000"
MOD_ID = "botw-mod-loader.emotes"              # the native mod (native/examples/emotes)
PKG_ID = "giruka.emotes-anims"                 # the package this tool builds (part_of MOD_ID)
ARCHIVE = "SwEmote_Animation"                  # Model/<ARCHIVE>.sbfres; AddRes Anim
CLIP_PREFIX = "SwEmote_"                       # our clips (FSKA names): unique across every archive in the list
SET_PREFIX = "ZEmote_"                         # animation sets (ASDefine Name): sort after every game set
BAS_PREFIX = "Player_ZEmote_"                  # ASDefine Filename -> Actor/AS/<it>.bas
LAST_GAME_SET = "WeaponThrowCharge"            # the game's last set name (1.9.0): ours must sort after it
MAX_SET_NAME = 31                              # the ghost's name limit (32 with its 0)
PAGES = ("hello", "dances", "hyrule")
SPECIAL = "special"                            # ragdoll: its own key, not a wheel slice
SLOTS_PER_PAGE = 8
ASLIST_PATH = (f"{BASE_TID}/romfs/Pack/TitleBG.pack//Actor/Pack/GameROMPlayer.sbactorpack//Actor/ASList/"
               "Player.baslist")
TITLEBG = "Pack/TitleBG.pack"
PLAYER_PACK = "Actor/Pack/GameROMPlayer.sbactorpack"
ANIM_FILE = "Model/Player_Animation.sbfres"
LIST_FILE = "Actor/ASList/Player.baslist"
IDLE_CLIP = "Nml_Wait"                         # its frame 0 is the idle pose; its bones and flags our template
BAS_SIZE_REF = "Actor/AS/Player_DoorPushDouble.bas"
TOL_RAD = 0.002                                # curve fit: every frame within this (0.11 degrees)
READBACK_DEG = 0.15
JUMP_DEG = 25.0                                # sanity: a joint moves at most this much between frames (not snaps)
FEET_CM = 2.0                                  # sanity: planted feet stay this near their idle spots

# Face events (.bas trigger event type 7): our names -> the game's face event values, first one the game has wins
FACES = {"none": [], "default": ["FaceDefault"], "smile": ["FaceSmile"], "blush": ["FaceTalkEmbarrass"],
         "smug": ["FaceSelfCameraPose", "FaceSmile"], "frown": ["FaceSerious"], "serious": ["FaceSerious"],
         "surprise": ["FaceTalkSurprise"], "battle": ["FaceBattle"], "selfie": ["FaceSelfCameraPose"],
         "relaxed": ["FaceRandomRelaxUpper", "FaceDefault"]}

# Finger curl per joint (1, 2, 3) in degrees, toward the palm. None: the idle pose's fingers. A missing finger: fist.
_FIST = (80.0, 95.0, 60.0)
_THUMB_IN = (5.0, 35.0, 45.0)
_STRAIGHT = (0.0, 0.0, 0.0)
HAND_PRESETS: dict[str, dict | None] = {
    "relaxed": None,
    "open": {f: _STRAIGHT for f in "ABCDE"},
    "fist": {"A": _THUMB_IN, **{f: _FIST for f in "BCDE"}},
    "thumb_up": {"A": _STRAIGHT, **{f: _FIST for f in "BCDE"}},
    "point": {"A": _THUMB_IN, "B": _STRAIGHT, **{f: _FIST for f in "CDE"}},
    "peace": {"A": _THUMB_IN, "B": _STRAIGHT, "C": _STRAIGHT, "D": _FIST, "E": _FIST},
    "snap": {"A": (10.0, 20.0, 10.0), "B": (25.0, 30.0, 20.0), "C": (35.0, 45.0, 25.0), "D": _FIST, "E": _FIST},
    "snapped": {"A": (0.0, 10.0, 5.0), "B": (25.0, 30.0, 20.0), "C": (85.0, 95.0, 60.0), "D": _FIST, "E": _FIST},
    "claw": {f: (40.0, 50.0, 40.0) for f in "ABCDE"},
    "grip": {"A": (5.0, 20.0, 20.0), **{f: (60.0, 70.0, 40.0) for f in "BCDE"}},
}
EASES = ("smooth", "linear", "snap", "hold")
KINDS = ("keys", "game_clip", "game_set")

# Two-bone chains (reach): end -> (upper, lower). Aim: a bone points at this child.
CHAINS = {"Wrist_L": ("Arm_1_L", "Arm_2_L"), "Wrist_R": ("Arm_1_R", "Arm_2_R"),
          "Ankle_L": ("Leg_1_L", "Leg_2_L"), "Ankle_R": ("Leg_1_R", "Leg_2_R")}
TIP = {"Clavicle_L": "Arm_1_L", "Arm_1_L": "Arm_2_L", "Arm_2_L": "Wrist_L", "Wrist_L": "Finger_C_1_L",
       "Clavicle_R": "Arm_1_R", "Arm_1_R": "Arm_2_R", "Arm_2_R": "Wrist_R", "Wrist_R": "Finger_C_1_R",
       "Leg_1_L": "Leg_2_L", "Leg_2_L": "Ankle_L", "Ankle_L": "Toe_L",
       "Leg_1_R": "Leg_2_R", "Leg_2_R": "Ankle_R", "Ankle_R": "Toe_R",
       "Spine_1": "Spine_2", "Spine_2": "Neck", "Neck": "Head"}
# Bones solved first among their siblings (the head and hips before the arms, so a hand can follow them: reach "on")
SOLVE_FIRST = {"Neck": 0, "Head": 0, "Waist": 1}
# The body bones an emote may name (Link's own names). Face, belt and pouch bones are left to the game.
BODY_BONES = (["Root", "Skl_Root", "Spine_1", "Spine_2", "Neck", "Head", "Waist"]
              + [f"{b}_{s}" for s in "LR" for b in ("Clavicle", "Arm_1", "Arm_2", "Wrist", "Leg_1", "Leg_2",
                                                     "Ankle", "Toe")]
              + [f"Finger_{c}_{j}_{s}" for s in "LR" for c in "ABCDE" for j in (1, 2, 3)])
KEY_FIELDS = {"f", "pose", "mirror", "ease", "reset", "free", "aim", "palm", "reach", "step", "elbow", "knee",
              "hand", "turn", "local", "rest", "hips", "feet", "note"}
POSE_FIELDS = KEY_FIELDS - {"f", "pose", "mirror", "ease", "reset", "free"}
TOP_FIELDS = {"id", "name", "aliases", "page", "slot", "by", "about", "kind", "frames", "loop", "face", "morph",
              "poses", "keys", "expect", "clip", "set", "fallback", "key", "note", "contact", "button_text"}
BUTTON_TEXT_MAX = 80
EXPECTS = {"above", "below", "left_of", "right_of", "in_front", "behind", "near", "far", "thumb", "feet_on_ground"}


class EmoteError(Exception):
    pass


def _log(log, msg: str) -> None:
    if log:
        log(msg)


# ==================================================================================================== math
# 3x3 matrices are flat row-major 9-tuples; vectors 3-tuples. A bone's local transform is T * Rz * Ry * Rx (Euler
# XYZ, X applied first), as Link's skeleton and the game's clips use.

I3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def mmul(a, b):
    return (a[0] * b[0] + a[1] * b[3] + a[2] * b[6], a[0] * b[1] + a[1] * b[4] + a[2] * b[7],
            a[0] * b[2] + a[1] * b[5] + a[2] * b[8],
            a[3] * b[0] + a[4] * b[3] + a[5] * b[6], a[3] * b[1] + a[4] * b[4] + a[5] * b[7],
            a[3] * b[2] + a[4] * b[5] + a[5] * b[8],
            a[6] * b[0] + a[7] * b[3] + a[8] * b[6], a[6] * b[1] + a[7] * b[4] + a[8] * b[7],
            a[6] * b[2] + a[7] * b[5] + a[8] * b[8])


def mvec(a, v):
    return (a[0] * v[0] + a[1] * v[1] + a[2] * v[2], a[3] * v[0] + a[4] * v[1] + a[5] * v[2],
            a[6] * v[0] + a[7] * v[1] + a[8] * v[2])


def mT(a):
    return (a[0], a[3], a[6], a[1], a[4], a[7], a[2], a[5], a[8])


def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vscale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def vlen(a):
    return math.sqrt(dot(a, a))


def unit(a, fallback=(0.0, 0.0, 1.0)):
    n = vlen(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else tuple(fallback)


def e2m(e):
    """Rz(z) @ Ry(y) @ Rx(x) for radians e = (x, y, z)."""
    cx, sx, cy, sy, cz, sz = math.cos(e[0]), math.sin(e[0]), math.cos(e[1]), math.sin(e[1]), math.cos(e[2]), \
        math.sin(e[2])
    return (cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx,
            sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx,
            -sy, cy * sx, cy * cx)


def deg_m(d):
    return e2m((math.radians(d[0]), math.radians(d[1]), math.radians(d[2])))


def _wrap(a, p):
    return a + 2 * math.pi * round((p - a) / (2 * math.pi))


def m2e(m, prev=None):
    """Euler XYZ (radians) of a rotation; with prev, the equivalent triple nearest to it (no 2-pi jumps)."""
    y = math.asin(max(-1.0, min(1.0, -m[6])))
    if abs(math.cos(y)) > 1e-7:
        x, z = math.atan2(m[7], m[8]), math.atan2(m[3], m[0])
    else:
        x, z = math.atan2(-m[5], m[4]), 0.0
    cands = ((x, y, z), (x + math.pi, math.pi - y, z + math.pi))
    if prev is None:
        return tuple(_wrap(v, 0.0) for v in cands[0])
    best = None
    for c in cands:
        u = tuple(_wrap(v, p) for v, p in zip(c, prev))
        d = sum(abs(a - b) for a, b in zip(u, prev))
        if best is None or d < best[0]:
            best = (d, u)
    return best[1]


def axis_angle(axis, ang):
    x, y, z = unit(axis)
    c, s = math.cos(ang), math.sin(ang)
    t = 1 - c
    return (c + x * x * t, x * y * t - z * s, x * z * t + y * s,
            y * x * t + z * s, c + y * y * t, y * z * t - x * s,
            z * x * t - y * s, z * y * t + x * s, c + z * z * t)


def rot_between(u, v):
    """The smallest rotation taking direction u to direction v."""
    u, v = unit(u), unit(v)
    c = max(-1.0, min(1.0, dot(u, v)))
    ax = cross(u, v)
    if vlen(ax) < 1e-9:
        if c > 0:
            return I3
        ax = cross(u, (1.0, 0.0, 0.0) if abs(u[0]) < 0.9 else (0.0, 1.0, 0.0))
    return axis_angle(ax, math.acos(c))


def frame_cols(a, b):
    """The matrix whose columns are a, b and a x b."""
    c = cross(a, b)
    return (a[0], b[0], c[0], a[1], b[1], c[1], a[2], b[2], c[2])


def m2q(m):
    t = m[0] + m[4] + m[8]
    if t > 0:
        s = math.sqrt(t + 1) * 2
        q = (0.25 * s, (m[7] - m[5]) / s, (m[2] - m[6]) / s, (m[3] - m[1]) / s)
    elif m[0] > m[4] and m[0] > m[8]:
        s = math.sqrt(1 + m[0] - m[4] - m[8]) * 2
        q = ((m[7] - m[5]) / s, 0.25 * s, (m[1] + m[3]) / s, (m[2] + m[6]) / s)
    elif m[4] > m[8]:
        s = math.sqrt(1 + m[4] - m[0] - m[8]) * 2
        q = ((m[2] - m[6]) / s, (m[1] + m[3]) / s, 0.25 * s, (m[5] + m[7]) / s)
    else:
        s = math.sqrt(1 + m[8] - m[0] - m[4]) * 2
        q = ((m[3] - m[1]) / s, (m[2] + m[6]) / s, (m[5] + m[7]) / s, 0.25 * s)
    n = math.sqrt(sum(v * v for v in q))
    return tuple(v / n for v in q)


def q2m(q):
    w, x, y, z = q
    return (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
            2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
            2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y))


def slerp(q0, q1, t):
    d = sum(a * b for a, b in zip(q0, q1))
    if d < 0:
        q1, d = tuple(-v for v in q1), -d
    if d > 0.9995:
        q = tuple(a + t * (b - a) for a, b in zip(q0, q1))
        n = math.sqrt(sum(v * v for v in q))
        return tuple(v / n for v in q)
    th = math.acos(min(1.0, d))
    s0, s1 = math.sin((1 - t) * th) / math.sin(th), math.sin(t * th) / math.sin(th)
    return tuple(s0 * a + s1 * b for a, b in zip(q0, q1))


def angle_between_m(a, b):
    """Degrees of the rotation between two rotation matrices."""
    r = mmul(mT(a), b)
    return math.degrees(math.acos(max(-1.0, min(1.0, (r[0] + r[4] + r[8] - 1) / 2))))


# ==================================================================================================== skeleton

@dataclass
class Bone:
    name: str
    parent: int
    t: tuple                 # local translation (m)
    r: tuple                 # local rotation, Euler XYZ radians


class Skeleton:
    """Bones in parent-first order, their rest (T-pose) world frames and the helpers the solver needs."""

    def __init__(self, bones: list[Bone]):
        self.bones = bones
        self.idx = {b.name: i for i, b in enumerate(bones)}
        if len(self.idx) != len(bones):
            raise EmoteError("the skeleton has two bones with one name")
        for i, b in enumerate(bones):
            if b.parent >= i:
                raise EmoteError(f"bone {b.name} comes before its parent")
        self.rest_rot = [e2m(b.r) for b in bones]
        self.rest_W, self.rest_P = fk(self, self.rest_rot, [b.t for b in bones])
        kids: dict[int, list[int]] = {}
        for i, b in enumerate(bones):
            kids.setdefault(b.parent, []).append(i)
        order: list[int] = []

        def visit(i):
            order.append(i)
            for k in sorted(kids.get(i, []), key=lambda k: (SOLVE_FIRST.get(bones[k].name, 9), k)):
                visit(k)
        for r in kids.get(-1, []):
            visit(r)
        self.order = order
        # finger curl axes (local): a joint turns its finger toward the palm (the palm faces down in the T-pose)
        self.curl_axis: dict[str, tuple] = {}
        for side in "LR":
            for c in "ABCDE":
                n1, n2 = f"Finger_{c}_1_{side}", f"Finger_{c}_2_{side}"
                if n1 not in self.idx or n2 not in self.idx:
                    continue
                d = unit(vsub(self.rest_P[self.idx[n2]], self.rest_P[self.idx[n1]]))
                palm = (0.0, -1.0, 0.0)
                if c == "A":   # the thumb folds across the palm, toward the little finger
                    pinky = f"Finger_E_1_{side}"
                    if pinky in self.idx:
                        palm = unit(vadd(palm, unit(vsub(self.rest_P[self.idx[pinky]], self.rest_P[self.idx[n1]]))))
                ax = cross(d, palm)
                if vlen(ax) < 1e-6:
                    continue
                ax = unit(ax)
                for j in (1, 2, 3):
                    n = f"Finger_{c}_{j}_{side}"
                    if n in self.idx:
                        self.curl_axis[n] = mvec(mT(self.rest_W[self.idx[n]]), ax)

    def __len__(self):
        return len(self.bones)

    def has(self, name: str) -> bool:
        return name in self.idx

    def match(self, pattern: str) -> list[str]:
        return [b.name for b in self.bones if fnmatch.fnmatchcase(b.name, pattern)]


def fk(skel, rots, trans):
    """World rotations and positions from local rotation matrices and translations (bones parent first)."""
    Wr, Wp = [None] * len(skel.bones), [None] * len(skel.bones)
    for i, b in enumerate(skel.bones):
        if b.parent < 0:
            Wr[i], Wp[i] = rots[i], tuple(trans[i])
        else:
            Wr[i] = mmul(Wr[b.parent], rots[i])
            Wp[i] = vadd(Wp[b.parent], mvec(Wr[b.parent], trans[i]))
    return Wr, Wp


def skeleton_from_model(model) -> Skeleton:
    """A Skeleton from tools/wiiu_model's skeleton (the game's dummy 'Skeleton' model)."""
    sk = model.skeleton
    return Skeleton([Bone(b.name, b.parent, tuple(float(v) for v in b.translation[:3]),
                          tuple(float(v) for v in b.rotation[:3])) for b in sk.bones])


def demo_skeleton() -> Skeleton:
    """A made-up humanoid with Link's body bone names (our own numbers; for tests and previews without game files).
    T-pose, palms down, every local frame lined up with the model's axes."""
    out: list[Bone] = []

    def add(name, parent, t):
        out.append(Bone(name, -1 if parent is None else next(i for i, b in enumerate(out) if b.name == parent),
                        tuple(t), (0.0, 0.0, 0.0)))
    add("Root", None, (0, 0, 0))
    add("Skl_Root", "Root", (0, 1.0, 0))
    add("Spine_1", "Skl_Root", (0, 0, 0))
    add("Spine_2", "Spine_1", (0, 0.14, 0))
    add("Neck", "Spine_2", (0, 0.26, 0))
    add("Head", "Neck", (0, 0.12, 0))
    add("Waist", "Skl_Root", (0, 0, 0))
    for s, sx in (("L", 1.0), ("R", -1.0)):
        add(f"Clavicle_{s}", "Spine_2", (0.02 * sx, 0.23, 0.02))
        add(f"Arm_1_{s}", f"Clavicle_{s}", (0.14 * sx, 0, 0))
        add(f"Arm_2_{s}", f"Arm_1_{s}", (0.25 * sx, 0, 0))
        add(f"Wrist_{s}", f"Arm_2_{s}", (0.27 * sx, 0, 0))
        for c, (dz, ln) in zip("ABCDE", ((0.03, 0.05), (0.025, 0.045), (0.0, 0.05), (-0.022, 0.045), (-0.04, 0.035))):
            first = (0.03 * sx, -0.015, 0.04) if c == "A" else (0.10 * sx, 0.0, dz)
            add(f"Finger_{c}_1_{s}", f"Wrist_{s}", first)
            add(f"Finger_{c}_2_{s}", f"Finger_{c}_1_{s}", (ln * sx, 0, 0.02 if c == "A" else 0))
            add(f"Finger_{c}_3_{s}", f"Finger_{c}_2_{s}", (ln * 0.6 * sx, 0, 0.01 if c == "A" else 0))
        add(f"Leg_1_{s}", "Waist", (0.1 * sx, -0.02, 0))
        add(f"Leg_2_{s}", f"Leg_1_{s}", (0, -0.42, 0.0))
        add(f"Ankle_{s}", f"Leg_2_{s}", (0, -0.47, -0.02))
        add(f"Toe_{s}", f"Ankle_{s}", (0, -0.06, 0.15))
    return Skeleton(out)


@dataclass
class Idle:
    """The idle pose: local rotation matrices, Euler angles and translations per bone."""
    rots: list
    eulers: list
    trans: list


def idle_from_rest(skel: Skeleton) -> Idle:
    return Idle(list(skel.rest_rot), [b.r for b in skel.bones], [b.t for b in skel.bones])


def demo_idle(skel: Skeleton) -> Idle:
    """A made-up standing pose for the demo skeleton: arms down by the sides, hands relaxed."""
    st = State()
    st.aim = {"Arm_1_R": (-0.15, -1, 0.0), "Arm_2_R": (-0.08, -1, 0.12), "Wrist_R": (-0.05, -1, 0.1),
              "Arm_1_L": (0.15, -1, 0.0), "Arm_2_L": (0.08, -1, 0.12), "Wrist_L": (0.05, -1, 0.1)}
    st.palm = {"Wrist_R": (1, 0, 0), "Wrist_L": (-1, 0, 0)}
    st.hand = {"R": "claw", "L": "claw"}
    st.feet = "free"
    rots, trans = solve_pose(skel, idle_from_rest(skel), st, {})
    return Idle(rots, [m2e(r) for r in rots], [tuple(t) for t in trans])


# ==================================================================================================== emote data

@dataclass
class State:
    aim: dict = field(default_factory=dict)       # bone -> direction (model space)
    palm: dict = field(default_factory=dict)      # wrist -> direction the palm faces
    reach: dict = field(default_factory=dict)     # wrist/ankle -> point, or {"on": bone, "at": offset}
    step: dict = field(default_factory=dict)      # wrist/ankle -> offset from its idle spot
    hint: dict = field(default_factory=dict)      # wrist/ankle -> elbow/knee direction
    hand: dict = field(default_factory=dict)      # "R"/"L" -> preset
    turn: dict = field(default_factory=dict)      # bone -> degrees about the model's axes
    local: dict = field(default_factory=dict)     # bone -> degrees in the bone's own frame
    rest: set = field(default_factory=set)        # bones at the skeleton's rest rotation
    hips: tuple = (0.0, 0.0, 0.0)
    feet: str = "planted"

    def copy(self) -> State:
        return State(dict(self.aim), dict(self.palm), dict(self.reach), dict(self.step), dict(self.hint),
                     dict(self.hand), dict(self.turn), dict(self.local), set(self.rest), tuple(self.hips), self.feet)


def _side_swap(name: str) -> str:
    if name in ("R", "L"):
        return "L" if name == "R" else "R"
    if name.endswith("_L"):
        return name[:-2] + "_R"
    if name.endswith("_R"):
        return name[:-2] + "_L"
    return name


def _mx(v):
    return (-v[0], v[1], v[2])


def mirror_state(st: State) -> State:
    """Left and right swapped: names swap, x flips, turns about y and z change sign."""
    def reach_m(v):
        if isinstance(v, dict):
            return {"on": _side_swap(v["on"]), "at": _mx(v["at"])}
        return _mx(v)
    return State({_side_swap(k): _mx(v) for k, v in st.aim.items()},
                 {_side_swap(k): _mx(v) for k, v in st.palm.items()},
                 {_side_swap(k): reach_m(v) for k, v in st.reach.items()},
                 {_side_swap(k): _mx(v) for k, v in st.step.items()},
                 {_side_swap(k): _mx(v) for k, v in st.hint.items()},
                 {_side_swap(k): v for k, v in st.hand.items()},
                 {_side_swap(k): (v[0], -v[1], -v[2]) for k, v in st.turn.items()},
                 {_side_swap(k): (v[0], -v[1], -v[2]) for k, v in st.local.items()},
                 {_side_swap(k) for k in st.rest}, _mx(st.hips), st.feet)


def _vec(v, what: str) -> tuple:
    if not (isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(x, (int, float)) and
                                                                 not isinstance(x, bool) for x in v)):
        raise EmoteError(f"{what}: needs 3 numbers, like [0, 1, 0]")
    return tuple(float(x) for x in v)


def _end_bone(k: str, kind: str) -> str:
    """'R'/'L' for elbow/knee/hand-like fields -> the chain's end bone."""
    if k in ("R", "L"):
        return ("Wrist_" if kind == "elbow" else "Ankle_") + k
    return k


def apply_fields(st: State, src: dict, skel: Skeleton | None, what: str) -> None:
    """Put a key's (or a named pose's) fields on top of a state."""
    def bones(names, field_name):
        out = []
        for n in names:
            hits = skel.match(n) if skel is not None else ([n] if not any(c in n for c in "*?[") else [n])
            if skel is not None and not hits:
                raise EmoteError(f"{what}: {field_name} names {n!r}, which isn't a bone of Link's")
            out += hits
        return out

    for k, v in (src.get("aim") or {}).items():
        for b in bones([k], "aim"):
            if b not in TIP:
                raise EmoteError(f"{what}: aim on {b}: only these bones can aim: {', '.join(sorted(TIP))}")
            st.aim[b] = _vec(v, f"{what} aim {k}")
    for k, v in (src.get("palm") or {}).items():
        if k not in ("Wrist_L", "Wrist_R"):
            raise EmoteError(f"{what}: palm is for Wrist_L or Wrist_R, not {k}")
        st.palm[k] = _vec(v, f"{what} palm {k}")
    for k, v in (src.get("reach") or {}).items():
        if k not in CHAINS:
            raise EmoteError(f"{what}: reach is for {', '.join(CHAINS)}, not {k}")
        if isinstance(v, dict):
            if set(v) - {"on", "at"} or not isinstance(v.get("on"), str):
                raise EmoteError(f"{what}: reach {k} {{\"on\": bone, \"at\": [x, y, z]}}")
            bones([v["on"]], "reach on")
            st.reach[k] = {"on": v["on"], "at": _vec(v.get("at", [0, 0, 0]), f"{what} reach {k} at")}
        else:
            st.reach[k] = _vec(v, f"{what} reach {k}")
        st.step.pop(k, None)
    for k, v in (src.get("step") or {}).items():
        if k not in CHAINS:
            raise EmoteError(f"{what}: step is for {', '.join(CHAINS)}, not {k}")
        st.step[k] = _vec(v, f"{what} step {k}")
        st.reach.pop(k, None)
    for fname in ("elbow", "knee"):
        for k, v in (src.get(fname) or {}).items():
            end = _end_bone(k, fname)
            if end not in CHAINS:
                raise EmoteError(f"{what}: {fname} {k}: use R or L")
            st.hint[end] = _vec(v, f"{what} {fname} {k}")
    for k, v in (src.get("hand") or {}).items():
        if k not in ("R", "L"):
            raise EmoteError(f"{what}: hand is {{\"R\": preset, \"L\": preset}}, not {k}")
        if v not in HAND_PRESETS:
            raise EmoteError(f"{what}: hand {k} {v!r}: one of {', '.join(HAND_PRESETS)}")
        st.hand[k] = v
    for k, v in (src.get("turn") or {}).items():
        for b in bones([k], "turn"):
            st.turn[b] = _vec(v, f"{what} turn {k}")
    for k, v in (src.get("local") or {}).items():
        for b in bones([k], "local"):
            st.local[b] = _vec(v, f"{what} local {k}")
    if "rest" in src:
        if not isinstance(src["rest"], list):
            raise EmoteError(f"{what}: rest is a list of bones")
        st.rest |= set(bones(src["rest"], "rest"))
    if "hips" in src:
        st.hips = _vec(src["hips"], f"{what} hips")
    if "feet" in src:
        if src["feet"] not in ("planted", "free"):
            raise EmoteError(f"{what}: feet is planted or free")
        st.feet = src["feet"]


def free_bones(st: State, names: list[str], skel: Skeleton | None) -> None:
    gone = set()
    for n in names:
        if n in ("R", "L"):
            st.hand.pop(n, None)
            gone |= {f"Wrist_{n}", f"Ankle_{n}"}
        gone |= set(skel.match(n)) if skel is not None else {n}
    for d in (st.aim, st.palm, st.reach, st.step, st.hint, st.turn, st.local):
        for k in list(d):
            if k in gone:
                del d[k]
    st.rest -= gone
    if "Skl_Root" in gone:
        st.hips = (0.0, 0.0, 0.0)


def key_states(spec: dict, skel: Skeleton | None = None) -> list[tuple[int, State, str]]:
    """Each key's full state: (frame, state, ease into it)."""
    poses = spec.get("poses") or {}
    out = []
    cur = State()
    for n, k in enumerate(spec["keys"]):
        what = f"{spec['id']} key {n} (frame {k.get('f')})"
        if k.get("reset"):
            cur = State()
        if "pose" in k:
            if k["pose"] not in poses:
                raise EmoteError(f"{what}: no pose called {k['pose']!r}")
            cur = State()
            apply_fields(cur, poses[k["pose"]], skel, f"{spec['id']} pose {k['pose']}")
            if k.get("mirror"):
                cur = mirror_state(cur)
        elif k.get("mirror"):
            cur = mirror_state(cur)
        if k.get("free"):
            free_bones(cur, list(k["free"]), skel)
        apply_fields(cur, k, skel, what)
        out.append((int(k["f"]), cur.copy(), k.get("ease", "smooth")))
    return out


# ==================================================================================================== solving

def _two_bone(S, T, a, b, hint):
    """The elbow/knee point of a two-bone chain from S (lengths a then b) reaching T, bending toward hint."""
    d_vec = vsub(T, S)
    d = vlen(d_vec)
    u = unit(d_vec, (0.0, -1.0, 0.0))
    d = max(abs(a - b) + 1e-4, min(a + b - 1e-4, d))
    cos_a = max(-1.0, min(1.0, (a * a + d * d - b * b) / (2 * a * d)))
    sin_a = math.sqrt(max(0.0, 1 - cos_a * cos_a))
    v = vsub(hint, vscale(u, dot(hint, u)))
    if vlen(v) < 1e-6:
        v = cross(u, (1.0, 0.0, 0.0)) if abs(u[0]) < 0.9 else cross(u, (0.0, 0.0, 1.0))
    v = unit(v)
    E = vadd(S, vadd(vscale(u, a * cos_a), vscale(v, a * sin_a)))
    return E, vadd(S, vscale(u, d))


def default_hint(end: str, skel: Skeleton, idle_P) -> tuple:
    """Elbows bend out and back; knees bend the way the idle pose bends them (forward)."""
    if end.startswith("Wrist"):
        sx = 1.0 if end.endswith("_L") else -1.0
        return unit((0.55 * sx, -0.25, -0.8))
    up, lo = CHAINS[end]
    S, E, T = idle_P[skel.idx[up]], idle_P[skel.idx[lo]], idle_P[skel.idx[end]]
    u = unit(vsub(T, S))
    off = vsub(vsub(E, S), vscale(u, dot(vsub(E, S), u)))
    return unit(off) if vlen(off) > 1e-4 else (0.0, 0.0, 1.0)


class Solver:
    """Solves key states into local rotations on one skeleton and idle pose (caches the idle pose's world frames)."""

    def __init__(self, skel: Skeleton, idle: Idle):
        self.skel, self.idle = skel, idle
        self.idle_W, self.idle_P = fk(skel, idle.rots, idle.trans)
        self.hints = {e: default_hint(e, skel, self.idle_P) for e in CHAINS if all(skel.has(n) for n in (e, *CHAINS[e]))}

    def solve(self, st: State) -> tuple[list, list]:
        return solve_pose(self.skel, self.idle, st, self.hints, self.idle_W, self.idle_P)


def solve_pose(skel: Skeleton, idle: Idle, st: State, hints: dict, idle_W=None, idle_P=None):
    """Local rotation matrices and translations of every bone for one state, on the idle pose."""
    if idle_W is None:
        idle_W, idle_P = fk(skel, idle.rots, idle.trans)
    n = len(skel.bones)
    trans = [tuple(t) for t in idle.trans]
    if "Skl_Root" in skel.idx and any(st.hips):
        r = skel.idx["Skl_Root"]
        trans[r] = vadd(trans[r], st.hips)
    targets: dict[str, object] = {}
    lock: dict[str, tuple] = {}
    for end, off in st.step.items():
        if skel.has(end):
            targets[end] = vadd(idle_P[skel.idx[end]], off)
    for end, v in st.reach.items():
        if skel.has(end):
            targets[end] = v
    body_moves = any(st.hips) or any(b in st.turn or b in st.aim for b in ("Skl_Root", "Waist"))
    if st.feet == "planted" and body_moves:
        for end in ("Ankle_L", "Ankle_R"):
            if skel.has(end) and end not in targets:
                targets[end] = idle_P[skel.idx[end]]
    for end in ("Ankle_L", "Ankle_R"):
        if end in targets and skel.has(end):
            lock[end] = idle_W[skel.idx[end]]
    upper = {CHAINS[e][0]: e for e in targets if e in CHAINS and all(skel.has(x) for x in CHAINS[e])}
    rots = [None] * n
    Wr, Wp = [None] * n, [None] * n
    dyn_aim: dict[str, tuple] = {}
    hand_of: dict[str, tuple] = {}
    for side, preset in st.hand.items():
        p = HAND_PRESETS.get(preset)
        if p is None:
            continue
        for c in "ABCDE":
            angles = p.get(c, _FIST)
            for j in (1, 2, 3):
                hand_of[f"Finger_{c}_{j}_{side}"] = angles[j - 1]
    for i in skel.order:
        b = skel.bones[i]
        name = b.name
        if name in hand_of:
            L = skel.rest_rot[i]
            ax = skel.curl_axis.get(name)
            if ax is not None and hand_of[name]:
                L = mmul(L, axis_angle(ax, math.radians(hand_of[name])))
        elif name in st.rest:
            L = skel.rest_rot[i]
        else:
            L = idle.rots[i]
        if name in st.local:
            L = mmul(L, deg_m(st.local[name]))
        Wpar = Wr[b.parent] if b.parent >= 0 else I3
        Ppar = Wp[b.parent] if b.parent >= 0 else (0.0, 0.0, 0.0)
        pos = vadd(Ppar, mvec(Wpar, trans[i]))
        W = mmul(Wpar, L)
        if name in upper:
            end = upper[name]
            lo = CHAINS[end][1]
            a = vlen(skel.bones[skel.idx[lo]].t)
            bl = vlen(skel.bones[skel.idx[end]].t)
            tgt = targets[end]
            if isinstance(tgt, dict):
                o = skel.idx[tgt["on"]]
                if Wr[o] is None:
                    raise EmoteError(f"reach {end} on {tgt['on']}: that bone is solved after the arm")
                tgt = vadd(Wp[o], mvec(Wr[o], mvec(mT(skel.rest_W[o]), tgt["at"])))
            E, _ = _two_bone(pos, tgt, a, bl, st.hint.get(end) or hints.get(end) or (0.0, 0.0, 1.0))
            dyn_aim[name] = vsub(E, pos)
            dyn_aim[lo] = vsub(tgt, E)
        aim = dyn_aim.get(name) or st.aim.get(name)
        if name in st.palm and name in TIP and skel.has(TIP[name]):
            u = unit(skel.bones[skel.idx[TIP[name]]].t)
            a = unit(aim) if aim else unit(mvec(W, u))
            v = mvec(mT(skel.rest_W[i]), (0.0, -1.0, 0.0))
            v = unit(vsub(v, vscale(u, dot(v, u))))
            s = st.palm[name]
            s = vsub(s, vscale(a, dot(s, a)))
            if vlen(s) < 1e-6:
                W = mmul(rot_between(mvec(W, u), a), W)
            else:
                W = mmul(frame_cols(a, unit(s)), mT(frame_cols(u, v)))
        elif aim and name in TIP and skel.has(TIP[name]):
            u = skel.bones[skel.idx[TIP[name]]].t
            W = mmul(rot_between(mvec(W, u), aim), W)
        if name in lock:
            W = lock[name]
        if name in st.turn:
            W = mmul(deg_m(st.turn[name]), W)
        rots[i] = mmul(mT(Wpar), W)
        Wr[i], Wp[i] = W, pos
    return rots, trans


def smooth(t):
    return t * t * (3 - 2 * t)


@dataclass
class Clip:
    """One emote solved: per-frame local rotations and translations (frames + 1 of them)."""
    spec: dict
    frames: list                  # [(rots, trans)]
    eases: list                   # per frame: the ease of the key it moves toward (for the sanity checks)
    key_frames: list


def sample(solver: Solver, spec: dict) -> Clip:
    """Every frame of a keys emote: keys solved, eased in and out, rotations slerped. A loop's last frame is its
    first (a closing key is added when the data has none)."""
    ks = key_states(spec, solver.skel)
    nfr = int(spec["frames"])
    if spec.get("loop") and ks[-1][0] != nfr:
        ks.append((nfr, ks[0][1].copy(), "smooth"))
    elif not spec.get("loop") and ks[-1][0] != nfr:
        ks.append((nfr, ks[-1][1].copy(), "hold"))
    solved = []
    for f, st, ease in ks:
        rots, trans = solver.solve(st)
        solved.append((f, rots, trans, [m2q(r) for r in rots], ease))
    frames, eases = [], []
    for fr in range(nfr + 1):
        k = max(i for i, s in enumerate(solved) if s[0] <= fr)
        if k == len(solved) - 1:
            frames.append((solved[k][1], solved[k][2]))
            eases.append("hold")
            continue
        f0, r0, t0, q0, _ = solved[k]
        f1, r1, t1, q1, ease = solved[k + 1]
        u = (fr - f0) / (f1 - f0)
        t = {"smooth": smooth(u), "linear": u, "snap": max(0.0, min(1.0, (fr - (f1 - 2)) / 2.0)), "hold": 0.0}[ease]
        if t <= 0.0:
            frames.append((r0, t0))
        elif t >= 1.0:
            frames.append((r1, t1))
        else:
            rots = [a if a is b else q2m(slerp(qa, qb, t)) for a, b, qa, qb in zip(r0, r1, q0, q1)]
            trans = [p if p == q else tuple(x + (y - x) * t for x, y in zip(p, q)) for p, q in zip(t0, t1)]
            frames.append((rots, trans))
        eases.append(ease)
    return Clip(spec, frames, eases, [s[0] for s in solved])


# ==================================================================================================== curves

def hermite_rows(fr, vals):
    """Cubic coefficients per key (Catmull-Rom slopes; the last key holds its value)."""
    n = len(fr)
    m = []
    for i in range(n):
        if n == 1:
            m.append(0.0)
        elif i == 0:
            m.append((vals[1] - vals[0]) / (fr[1] - fr[0]))
        elif i == n - 1:
            m.append((vals[-1] - vals[-2]) / (fr[-1] - fr[-2]))
        else:
            m.append((vals[i + 1] - vals[i - 1]) / (fr[i + 1] - fr[i - 1]))
    rows = []
    for i in range(n - 1):
        h = fr[i + 1] - fr[i]
        p0, p1, m0, m1 = vals[i], vals[i + 1], m[i] * h, m[i + 1] * h
        rows.append((p0, m0, 3 * (p1 - p0) - 2 * m0 - m1, 2 * (p0 - p1) + m0 + m1))
    rows.append((vals[-1], 0.0, 0.0, 0.0))
    return rows


def rows_eval(fr, rows, x):
    if x <= fr[0]:
        return rows[0][0]
    lo, hi = 0, len(fr) - 1
    if x >= fr[hi]:
        return rows[-1][0]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if fr[mid] <= x:
            lo = mid
        else:
            hi = mid
    t = (x - fr[lo]) / (fr[lo + 1] - fr[lo])
    c = rows[lo]
    return c[0] + t * (c[1] + t * (c[2] + t * c[3]))


def reduce_keys(vals, tol=TOL_RAD):
    """Keys to keep so the cubic curve stays within tol of every frame. A key is dropped when the curve over the
    span its removal changes (two keys either side) still fits."""
    keep = list(range(len(vals)))
    i = 1
    while i < len(keep) - 1:
        trial = keep[:i] + keep[i + 1:]
        lo, hi = max(0, i - 2), min(len(trial) - 1, i + 1)
        sub = trial[lo:hi + 1]
        # slopes at the ends of the window need their outer neighbours
        ext_lo, ext_hi = max(0, lo - 1), min(len(trial) - 1, hi + 1)
        ext = trial[ext_lo:ext_hi + 1]
        fr = [float(k) for k in ext]
        rows = hermite_rows(fr, [vals[k] for k in ext])
        if all(abs(rows_eval(fr, rows, float(x)) - vals[x]) <= tol for x in range(sub[0], sub[-1] + 1)):
            keep = trial
        else:
            i += 1
    return keep


@dataclass
class Curve:
    target: int
    frames: list
    rows: list
    delta: float

    def head(self) -> bytes:
        ftype = 2 if self.frames[-1] <= 255 else 1
        return struct.pack("<HHIfffff4x", ftype, len(self.frames), self.target, self.frames[0], self.frames[-1],
                           1.0, 0.0, self.delta)

    def frame_bytes(self) -> bytes:
        if self.frames[-1] <= 255:
            return bytes(int(f) for f in self.frames)
        return struct.pack("<%dh" % len(self.frames), *[round(f * 32) for f in self.frames])

    def key_bytes(self) -> bytes:
        return struct.pack("<%df" % (4 * len(self.rows)), *[c for r in self.rows for c in r])


def make_curve(target: int, vals: list, tol=TOL_RAD) -> Curve:
    keep = reduce_keys(vals, tol)
    fr = [float(k) for k in keep]
    rows = hermite_rows(fr, [vals[k] for k in keep])
    return Curve(target, fr, rows, vals[keep[-1]] - vals[0])


def baked_size(curves) -> int:
    """FSKA +0x54, as every game clip has it: sum over curves of align8(4 * (ceil(end - start) + 8))."""
    return sum(((4 * (math.ceil(c.frames[-1] - c.frames[0]) + 8) + 7) // 8) * 8 for c in curves)


CURVE_BIT = {("r", 0): 0x200, ("r", 1): 0x400, ("r", 2): 0x800, ("t", 0): 0x2000, ("t", 1): 0x4000,
             ("t", 2): 0x8000}
FLAG_ROT_ZERO, FLAG_TRANS_ZERO, FLAG_SCALE_ONE = 0x4000000, 0x8000000, 0x3000000


@dataclass
class TemplateBone:
    """A bone of the game's idle clip: its name, its flags and base bits, its head bytes (copied)."""
    name: str
    flags: int
    head: bytes


def template_from_clip(clip) -> list[TemplateBone]:
    return [TemplateBone(ba.name, struct.unpack_from("<I", ba.head)[0], bytes(ba.head)) for ba in clip.bone_anims]


def plain_template(skel: Skeleton) -> list[TemplateBone]:
    """A template for a skeleton without a game clip (tests): every body bone with scale, rotation and translation
    base data, the game's high flag bits."""
    out = []
    for b in skel.bones:
        flags = 0x38 | FLAG_SCALE_ONE | (0 if b.name in ("Root", "Skl_Root") else 0x800000)
        out.append(TemplateBone(b.name, flags, struct.pack("<IBBBBii", flags, 3, 6, 0, 7, 0, 0)))
    return out


@dataclass
class BoneTrack:
    name: str
    flags: int
    head: bytes                   # u32 flags, u8 x4, s32 first curve, s32 0
    base: bytes | None
    curves: list


@dataclass
class ClipData:
    """A clip ready for the writer: its FSKA head fields and bone tracks; eulers per bone for checks."""
    name: str
    frames: int
    loop: bool
    tracks: list
    eulers: dict

    @property
    def curves(self):
        return [c for t in self.tracks for c in t.curves]

    def head(self) -> bytes:
        cs = self.curves
        return struct.pack("<IiiIHHI", 0x1200 | (4 if self.loop else 0), self.frames, len(cs), baked_size(cs),
                           len(self.tracks), 0, 0)


def build_clip(skel: Skeleton, template: list[TemplateBone], clip: Clip, name: str) -> ClipData:
    """Euler curves per template bone (the game's idle clip's bones and flag conventions, our values)."""
    eulers: dict[str, list] = {}
    tracks, begin = [], 0
    nfr = len(clip.frames)
    for tb in template:
        if not skel.has(tb.name):
            raise EmoteError(f"the idle clip names {tb.name}, which the skeleton lacks")
        i = skel.idx[tb.name]
        _, br, bt, _nc, bbt, _bc, _p = struct.unpack("<IBBBBii", tb.head)
        basebits = tb.flags & 0x38
        if not basebits:                     # assist bones: the game drives them; nothing in the clip
            tracks.append(BoneTrack(tb.name, tb.flags, struct.pack("<IBBBBii", tb.flags, 0, 0, 0, 0, begin, 0),
                                    None, []))
            continue
        seq, prev = [], None
        for rots, _ in clip.frames:
            prev = m2e(rots[i], prev)
            seq.append(prev)
        eulers[tb.name] = seq
        T = [tr[i] for _, tr in clip.frames]
        curves, flags = [], (tb.flags & 0xFF800000) | basebits
        if basebits & 0x10:
            for j in range(3):
                ch = [e[j] for e in seq]
                if max(ch) - min(ch) > 1e-5:
                    curves.append(make_curve(0x20 + 4 * j, ch))
                    flags |= CURVE_BIT[("r", j)]
        if basebits & 0x20:
            for j in range(3):
                ch = [t[j] for t in T]
                if max(ch) - min(ch) > 1e-6:
                    curves.append(make_curve(0x10 + 4 * j, ch))
                    flags |= CURVE_BIT[("t", j)]
        rot_zero = max(abs(v) for e in seq for v in e) < 1e-6
        flags = (flags | FLAG_ROT_ZERO) if rot_zero else (flags & ~FLAG_ROT_ZERO)
        if basebits & 0x20:
            tr_zero = max(abs(v) for t in T for v in t) < 1e-6
            flags = (flags | FLAG_TRANS_ZERO) if tr_zero else (flags & ~FLAG_TRANS_ZERO)
        base = b""
        if basebits & 0x08:
            base += struct.pack("<3f", 1.0, 1.0, 1.0)
        if basebits & 0x10:
            base += struct.pack("<4f", *seq[0], 1.0)
        if basebits & 0x20:
            base += struct.pack("<3f", *T[0])
        tracks.append(BoneTrack(tb.name, flags, struct.pack("<IBBBBii", flags, br, bt, len(curves), bbt, begin, 0),
                                base, curves))
        begin += len(curves)
    if nfr - 1 > 255:
        raise EmoteError(f"{name}: at most 255 frames")
    return ClipData(name, nfr - 1, bool(clip.spec.get("loop")), tracks, eulers)


def to_skel_anim(cd: ClipData):
    """tools/wiiu_model's SkelAnim for a clip."""
    from wiiu_model.neutral import AnimCurve, BoneAnim, SkelAnim
    bas = [BoneAnim(t.name, t.head, t.base, [AnimCurve(c.head(), c.frame_bytes(), c.key_bytes()) for c in t.curves])
           for t in cd.tracks]
    return SkelAnim(cd.name, "", cd.head(), [0xFFFF] * len(bas), bas, [])


# ---- reading curves back (the game's clips and ours)
TARGET = {0x04: ("s", 0), 0x08: ("s", 1), 0x0C: ("s", 2), 0x10: ("t", 0), 0x14: ("t", 1), 0x18: ("t", 2),
          0x20: ("r", 0), 0x24: ("r", 1), 0x28: ("r", 2)}


def read_curve(head: bytes, frames: bytes, keys: bytes):
    """(frames, rows) of a stored curve (any frame and key type, cubic or linear)."""
    f, k, _tg, _s, _e, sc, off, _d = struct.unpack_from("<HHIfffff", head)
    ft, kt, ct = f & 3, (f >> 2) & 3, (f >> 4) & 7
    if ft == 0:
        fr = list(struct.unpack("<%df" % k, frames))
    elif ft == 1:
        fr = [v / 32 for v in struct.unpack("<%dh" % k, frames)]
    else:
        fr = [float(v) for v in frames[:k]]
    n = {0: 4, 1: 2}.get(ct, 4)
    vals = struct.unpack("<%d%s" % (k * n, {0: "f", 1: "h", 2: "b"}[kt]), keys[:k * n * {0: 4, 1: 2, 2: 1}[kt]])
    rows = []
    for i in range(k):
        cs = vals[i * n:(i + 1) * n]
        row = [cs[0] * sc + off] + [x * sc for x in cs[1:]]
        rows.append(tuple(row + [0.0] * (4 - len(row))))
    return fr, rows


def unpack_base(flags: int, base: bytes | None) -> dict:
    out, at = {}, 0
    vals = struct.unpack("<%df" % (len(base) // 4), base) if base else ()
    if flags & 0x08:
        out["s"] = list(vals[at:at + 3])
        at += 3
    if flags & 0x10:
        out["r"] = list(vals[at:at + 3])
        at += 4
    if flags & 0x20:
        out["t"] = list(vals[at:at + 3])
    return out


def clip_pose(skel: Skeleton, clip, fr: float) -> tuple[list, list]:
    """Local Euler angles and translations of every bone at frame fr of a stored clip (rest where it says nothing)."""
    eul = [list(b.r) for b in skel.bones]
    tr = [list(b.t) for b in skel.bones]
    for ba in clip.bone_anims:
        if ba.name not in skel.idx:
            continue
        i = skel.idx[ba.name]
        flags = struct.unpack_from("<I", ba.head)[0]
        base = unpack_base(flags, ba.base)
        if "r" in base:
            eul[i] = list(base["r"])
        if "t" in base:
            tr[i] = list(base["t"])
        for c in ba.curves:
            key, j = TARGET[struct.unpack_from("<I", c.head, 4)[0]]
            frs, rows = read_curve(c.head, c.frames, c.keys)
            v = rows_eval(frs, rows, fr)
            if key == "r":
                eul[i][j] = v
            elif key == "t":
                tr[i][j] = v
    return eul, tr


def idle_from_clip(skel: Skeleton, clip) -> Idle:
    eul, tr = clip_pose(skel, clip, 0.0)
    return Idle([e2m(e) for e in eul], [tuple(e) for e in eul], [tuple(t) for t in tr])


# ==================================================================================================== data files

def load_emotes(folder: str = DATA) -> list[dict]:
    """Every tools/emotes/*.json, in wheel order (page, slot; the special ones last)."""
    if not os.path.isdir(folder):
        raise EmoteError(f"{folder} isn't there")
    out = []
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(folder, fn)
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError) as e:
            raise EmoteError(f"{fn} can't be read: {e}") from None
        if not isinstance(d, dict):
            raise EmoteError(f"{fn}: one emote per file (a JSON object)")
        d["_file"] = fn
        out.append(d)
    order = {p: i for i, p in enumerate(PAGES + (SPECIAL,))}
    out.sort(key=lambda d: (order.get(d.get("page"), 99), d.get("slot", 0), str(d.get("id"))))
    return out


ID_RE = re.compile(r"[a-z][a-z0-9_]{0,23}")


def wrap_name(name: str, width: int = 12) -> list[str]:
    lines, cur = [], ""
    for w in name.split():
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def lint_one(d: dict, skel: Skeleton | None = None) -> list[str]:
    """Problems in one emote's data (empty: fine). With a skeleton, bones are checked against it too."""
    p: list[str] = []
    fn = d.get("_file", "?")
    eid = d.get("id")
    if not isinstance(eid, str) or not ID_RE.fullmatch(eid):
        return [f"{fn}: id must be lowercase letters, digits and _ (at most 24, starting with a letter)"]
    if fn != "?" and fn != f"{eid}.json":
        p.append(f"{fn}: the file must be called {eid}.json")
    for k in d:
        if k not in TOP_FIELDS and k != "_file":
            p.append(f"{eid}: unknown field {k!r}")
    name = d.get("name")
    if not isinstance(name, str) or not name.isascii() or not name.strip():
        p.append(f"{eid}: name must be plain ASCII text")
    elif len(wrap_name(name)) > 2 or any(len(x) > 12 for x in wrap_name(name)):
        p.append(f"{eid}: name {name!r} must fit 2 lines of 12 letters (the wheel)")
    if len(SET_PREFIX + eid) > MAX_SET_NAME:
        p.append(f"{eid}: id too long for the set name {SET_PREFIX}{eid}")
    page = d.get("page")
    if page not in PAGES + (SPECIAL,):
        p.append(f"{eid}: page must be one of {', '.join(PAGES + (SPECIAL,))}")
    slot = d.get("slot")
    if page in PAGES and not (isinstance(slot, int) and 1 <= slot <= SLOTS_PER_PAGE):
        p.append(f"{eid}: slot must be 1 to {SLOTS_PER_PAGE}")
    if not isinstance(d.get("by"), str) or not d["by"].strip():
        p.append(f"{eid}: by (who made it) is needed")
    if not isinstance(d.get("about"), str) or not d["about"].strip():
        p.append(f"{eid}: about (one line: what it looks like) is needed")
    bt = d.get("button_text")
    if bt is not None and (not isinstance(bt, str) or not bt.strip() or len(bt) > BUTTON_TEXT_MAX):
        p.append(f"{eid}: button_text (the Play button's line) is up to {BUTTON_TEXT_MAX} characters")
    al = d.get("aliases", [])
    if not isinstance(al, list) or not all(isinstance(a, str) and a.strip() and a.isascii() for a in al):
        p.append(f"{eid}: aliases is a list of words")
    kind = d.get("kind")
    if kind not in KINDS:
        p.append(f"{eid}: kind must be one of {', '.join(KINDS)}")
        return p
    if not isinstance(d.get("loop"), bool):
        p.append(f"{eid}: loop is true or false")
    face = d.get("face", "none")
    if face not in FACES:
        p.append(f"{eid}: face {face!r}: one of {', '.join(FACES)}")
    morph = d.get("morph", 6)
    if not isinstance(morph, (int, float)) or isinstance(morph, bool) or not 0 <= morph <= 30:
        p.append(f"{eid}: morph is 0 to 30 frames")
    if "fallback" in d and not (isinstance(d["fallback"], str) and re.fullmatch(r"[A-Za-z0-9_]{1,31}",
                                                                                d["fallback"])):
        p.append(f"{eid}: fallback is a game set name")
    if kind == "game_clip":
        if not isinstance(d.get("clip"), str) or not d["clip"]:
            p.append(f"{eid}: a game_clip needs \"clip\": the game clip's name")
        return p
    if kind == "game_set":
        if not isinstance(d.get("set"), str) or not d["set"]:
            p.append(f"{eid}: a game_set needs \"set\": the game set's name")
        return p
    frames = d.get("frames")
    if not isinstance(frames, int) or isinstance(frames, bool) or not 2 <= frames <= 255:
        p.append(f"{eid}: frames must be 2 to 255 (30 = 1 second)")
        return p
    keys = d.get("keys")
    if not isinstance(keys, list) or not keys:
        p.append(f"{eid}: keys is a list of keyframes")
        return p
    last = -1
    for n, k in enumerate(keys):
        if not isinstance(k, dict):
            p.append(f"{eid}: key {n} isn't an object")
            continue
        for f in k:
            if f not in KEY_FIELDS:
                p.append(f"{eid} key {n}: unknown field {f!r}")
        f = k.get("f")
        if not isinstance(f, int) or isinstance(f, bool) or not 0 <= f <= frames:
            p.append(f"{eid} key {n}: f must be a frame 0 to {frames}")
            continue
        if n == 0 and f != 0:
            p.append(f"{eid}: the first key must be at frame 0")
        if f <= last:
            p.append(f"{eid} key {n}: frames must go up (frame {f} after {last})")
        last = f
        if k.get("ease", "smooth") not in EASES:
            p.append(f"{eid} key {n}: ease is one of {', '.join(EASES)}")
    poses = d.get("poses", {})
    if not isinstance(poses, dict):
        p.append(f"{eid}: poses is an object of named poses")
        poses = {}
    for pn, pv in poses.items():
        if not isinstance(pv, dict):
            p.append(f"{eid}: pose {pn} isn't an object")
            continue
        for f in pv:
            if f not in POSE_FIELDS:
                p.append(f"{eid} pose {pn}: field {f!r} doesn't belong in a pose")
    if not p:
        try:
            key_states(d, skel)
        except EmoteError as e:
            p.append(str(e))
    if not p and skel is None:
        names = set(BODY_BONES)
        for st in (s for _, s, _ in key_states(d, None)):
            for dct in (st.aim, st.turn, st.local, st.palm):
                for b in dct:
                    if b not in names:
                        p.append(f"{eid}: {b} isn't one of Link's body bones")
            for b in st.rest:
                if not any(fnmatch.fnmatchcase(x, b) for x in names):
                    p.append(f"{eid}: rest {b} matches none of Link's body bones")
            for v in st.reach.values():
                if isinstance(v, dict) and v["on"] not in names:
                    p.append(f"{eid}: reach on {v['on']} isn't one of Link's body bones")
    for e in d.get("expect", []):
        p += _lint_expect(eid, e, frames)
    contact = d.get("contact", [])
    if not isinstance(contact, list) or not all(c in ("Wrist_L", "Wrist_R") for c in contact):
        p.append(f"{eid}: contact lists the hands that may touch the body (Wrist_L, Wrist_R)")
    return sorted(set(p), key=p.index)


def _lint_expect(eid, e, frames) -> list[str]:
    if not isinstance(e, dict) or not isinstance(e.get("f"), int) or not 0 <= e["f"] <= frames:
        return [f"{eid}: each expect needs f, a frame 0 to {frames}"]
    ks = [k for k in e if k != "f"]
    if len(ks) != 1 or ks[0] not in EXPECTS:
        return [f"{eid}: an expect has f and one check of {', '.join(sorted(EXPECTS))}"]
    k, v = ks[0], e[ks[0]]
    if k == "feet_on_ground":
        return [] if v is True else [f"{eid}: feet_on_ground: true"]
    if k == "thumb":
        return [] if isinstance(v, list) and len(v) == 2 and v[0] in ("R", "L") and v[1] in ("up", "down") else \
            [f"{eid}: thumb: [\"R\" or \"L\", \"up\" or \"down\"]"]
    if k in ("near", "far"):
        ok = isinstance(v, list) and len(v) == 3 and all(isinstance(x, str) for x in v[:2]) and \
            isinstance(v[2], (int, float))
        return [] if ok else [f"{eid}: {k}: [bone, bone, centimetres]"]
    return [] if isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) for x in v) else \
        [f"{eid}: {k}: [bone, bone]"]


def lint_all(emotes: list[dict], skel: Skeleton | None = None) -> list[str]:
    p: list[str] = []
    ids, aliases, slots = {}, {}, {}
    for d in emotes:
        p += lint_one(d, skel)
        eid = d.get("id")
        if not isinstance(eid, str):
            continue
        if eid in ids:
            p.append(f"two emotes have the id {eid}")
        ids[eid] = d
        if d.get("page") in PAGES:
            s = (d["page"], d.get("slot"))
            if s in slots:
                p.append(f"{eid} and {slots[s]} both take {d['page']} slot {d.get('slot')}")
            slots[s] = eid
        for a in d.get("aliases", []) if isinstance(d.get("aliases"), list) else []:
            a2 = str(a).lower()
            if a2 in aliases and aliases[a2] != eid:
                p.append(f"the alias {a!r} is used by {aliases[a2]} and {eid}")
            aliases[a2] = eid
    for a, eid in aliases.items():
        if a in ids and a != eid:
            p.append(f"the alias {a!r} of {eid} is another emote's id")
    names = sorted(SET_PREFIX + i for i in ids)
    if names and names[0].encode() <= LAST_GAME_SET.encode():
        p.append("a set name sorts before the game's last set: the game wouldn't find it")
    return p


# ==================================================================================================== checks

def bone_pos(skel, Wp, name):
    return Wp[skel.idx[name]]


def thumb_dir(skel, Wp, side):
    a, b = f"Finger_A_1_{side}", f"Finger_A_3_{side}"
    if not (skel.has(a) and skel.has(b)):
        return None
    return vsub(Wp[skel.idx[b]], Wp[skel.idx[f"Wrist_{side}"]])


def run_expect(skel: Skeleton, clip: Clip, e: dict, idle_P) -> str | None:
    """None when the check passes, else what failed."""
    fr = e["f"]
    rots, trans = clip.frames[fr]
    _, Wp = fk(skel, rots, trans)
    k = next(x for x in e if x != "f")
    v = e[k]
    if k == "feet_on_ground":
        for s in "LR":
            n = f"Ankle_{s}"
            if skel.has(n) and abs(Wp[skel.idx[n]][1] - idle_P[skel.idx[n]][1]) > FEET_CM / 100:
                return f"frame {fr}: {n} is {100 * (Wp[skel.idx[n]][1] - idle_P[skel.idx[n]][1]):+.1f} cm off the ground"
        return None
    if k == "thumb":
        t = thumb_dir(skel, Wp, v[0])
        if t is None:
            return None
        ok = t[1] > 0.03 if v[1] == "up" else t[1] < -0.03
        return None if ok else f"frame {fr}: thumb {v[0]} isn't {v[1]} (tip {100 * t[1]:+.1f} cm from the wrist)"
    a, b = v[0], v[1]
    for n in (a, b):
        if not skel.has(n):
            return f"frame {fr}: no bone {n}"
    pa, pb = Wp[skel.idx[a]], Wp[skel.idx[b]]
    if k in ("near", "far"):
        d = vlen(vsub(pa, pb)) * 100
        ok = d <= v[2] if k == "near" else d >= v[2]
        return None if ok else f"frame {fr}: {a} and {b} are {d:.1f} cm apart ({k} {v[2]})"
    axis, sign = {"above": (1, 1), "below": (1, -1), "left_of": (0, 1), "right_of": (0, -1), "in_front": (2, 1),
                  "behind": (2, -1)}[k]
    ok = (pa[axis] - pb[axis]) * sign > 0
    return None if ok else f"frame {fr}: {a} isn't {k.replace('_', ' ')} {b} ({pa[axis]:.3f} vs {pb[axis]:.3f})"


def sanity(skel: Skeleton, clip: Clip, idle_P) -> list[str]:
    """Pose sanity on every frame: no joint jumps (except snaps), no hand deep in the body, elbows and knees bend the
    human way, planted feet stay put."""
    out: list[str] = []
    spec = clip.spec
    contact = set(spec.get("contact", []))
    names = [n for n in ("Arm_1_L", "Arm_2_L", "Wrist_L", "Arm_1_R", "Arm_2_R", "Wrist_R", "Spine_1", "Head",
                         "Leg_1_L", "Leg_2_L", "Leg_1_R", "Leg_2_R") if skel.has(n)]
    prevW = None
    worst = (0.0, "", 0)
    for fr, (rots, trans) in enumerate(clip.frames):
        Wr, Wp = fk(skel, rots, trans)
        if prevW is not None and clip.eases[fr] not in ("snap", "hold") and clip.eases[fr - 1] not in ("snap", "hold"):
            for n in names:
                a = angle_between_m(prevW[skel.idx[n]], Wr[skel.idx[n]])
                if a > worst[0]:
                    worst = (a, n, fr)
        prevW = Wr
        if skel.has("Skl_Root") and skel.has("Neck"):
            lo, hi = Wp[skel.idx["Skl_Root"]], Wp[skel.idx["Neck"]]
            axis = vsub(hi, lo)
            for s in "LR":
                w = f"Wrist_{s}"
                if w in contact or not skel.has(w):
                    continue
                p = vsub(Wp[skel.idx[w]], lo)
                t = max(0.0, min(1.0, dot(p, axis) / max(dot(axis, axis), 1e-9)))
                d = vlen(vsub(p, vscale(axis, t)))
                if d < 0.06 and 0.05 < t < 0.95:
                    out.append(f"frame {fr}: {w} is inside the body ({100 * d:.1f} cm from its middle)")
        for s in "LR":
            if all(skel.has(x) for x in (f"Arm_1_{s}", f"Arm_2_{s}", f"Wrist_{s}")):
                S, E, W = (Wp[skel.idx[x]] for x in (f"Arm_1_{s}", f"Arm_2_{s}", f"Wrist_{s}"))
                bend = math.degrees(math.acos(max(-1.0, min(1.0, dot(unit(vsub(E, S)), unit(vsub(W, E)))))))
                if bend > 155:
                    out.append(f"frame {fr}: elbow {s} bends {bend:.0f} degrees (at most 155)")
            if all(skel.has(x) for x in (f"Leg_1_{s}", f"Leg_2_{s}", f"Ankle_{s}")):
                S, K, A = (Wp[skel.idx[x]] for x in (f"Leg_1_{s}", f"Leg_2_{s}", f"Ankle_{s}"))
                if dot(vsub(K, vscale(vadd(S, A), 0.5)), (0.0, 0.0, 1.0)) < -0.03:
                    out.append(f"frame {fr}: knee {s} bends backward")
    if worst[0] > JUMP_DEG:
        out.append(f"frame {worst[2]}: {worst[1]} turns {worst[0]:.0f} degrees in one frame (at most {JUMP_DEG:.0f})")
    # planted feet: frames whose two keys are both planted with no ankle move
    ks = key_states(spec, skel)

    def planted(st: State) -> bool:
        return st.feet == "planted" and not ({"Ankle_L", "Ankle_R"} & (set(st.step) | set(st.reach)))
    for fr, (rots, trans) in enumerate(clip.frames):
        k0 = max((i for i, (f, _, _) in enumerate(ks) if f <= fr), default=0)
        k1 = min(k0 + 1, len(ks) - 1)
        if not (planted(ks[k0][1]) and planted(ks[k1][1])):
            continue
        _, Wp = fk(skel, rots, trans)
        for s in "LR":
            n = f"Ankle_{s}"
            if skel.has(n):
                d = vlen(vsub(Wp[skel.idx[n]], idle_P[skel.idx[n]])) * 100
                if d > FEET_CM:
                    out.append(f"frame {fr}: planted foot {s} moved {d:.1f} cm")
                    break
    return sorted(set(out), key=out.index)[:8]


# ==================================================================================================== game files

class Game:
    """The player's game files (extracted\\romfs, only read), each read once."""

    def __init__(self, install: str):
        self.install = install
        self.romfs = os.path.join(install, "extracted", "romfs")
        self._tb = None
        self._pp = None
        self._anim = None
        self._res = None
        self._list = None
        self.hashes: dict[str, str] = {}

    def path(self, rel: str) -> str:
        return os.path.join(self.romfs, *rel.split("/"))

    def titlebg(self) -> bnp.Blob:
        if self._tb is None:
            p = self.path(TITLEBG)
            try:
                with open(p, "rb") as f:
                    data = f.read()
            except OSError as e:
                raise EmoteError(f"Your game's {TITLEBG} can't be read ({e.strerror or e})") from None
            self.hashes[TITLEBG] = hashlib.sha256(data).hexdigest()
            self._tb = bnp.Blob(data)
        return self._tb

    def player_pack(self) -> bnp.Blob:
        if self._pp is None:
            pp = self.titlebg().child(PLAYER_PACK)
            if pp is None:
                raise EmoteError(f"Your game's TitleBG.pack has no {PLAYER_PACK}")
            self._pp = pp
        return self._pp

    def anim_blob(self) -> bnp.Blob:
        if self._anim is None:
            b = self.titlebg().child(ANIM_FILE)
            if b is None:
                raise EmoteError(f"Your game's TitleBG.pack has no {ANIM_FILE}")
            self._anim = b
        return self._anim

    def anims(self):
        """Player_Animation as tools/wiiu_model's ResFile (about 6 s)."""
        if self._res is None:
            from wiiu_model import nx_model
            self._res = nx_model.read(self.anim_blob().raw)
        return self._res

    def aslist(self) -> bnp.Pio:
        if self._list is None:
            b = self.player_pack().child(LIST_FILE)
            if b is None:
                raise EmoteError(f"Your game's player pack has no {LIST_FILE}")
            self._list = bnp.aamp_read(b.raw)
        return self._list

    def set_names(self) -> dict[str, str]:
        """The game's animation sets: Name -> Filename."""
        out = {}
        for o in (self.aslist().root.lists.get(bnp.AS_DEFINES) or bnp.PList()).objs.values():
            n, f = bnp._text(o, bnp.P_NAME), bnp._text(o, bnp.P_FILENAME)
            if n is not None and f is not None:
                out[n.decode("utf-8", "replace")] = f.decode("utf-8", "replace")
        return out

    def bas(self, filename: str) -> bytes | None:
        b = self.player_pack().child(f"Actor/AS/{filename}.bas")
        return b.raw if b is not None else None

    def face_values(self) -> set[str]:
        """Every face event value (trigger type 7) the game's player sets use."""
        out: set[str] = set()

        def walk(pl):
            for o in pl.objects.values():
                with contextlib.suppress(Exception):
                    if o.get_value("TypeIndex") == 7 and isinstance(o.get_value("Value"), str):
                        out.add(o.get_value("Value"))
            for sub in pl.lists.values():
                walk(sub)
        for name in self.player_pack().open():
            if name.startswith("Actor/AS/") and name.endswith(".bas"):
                with contextlib.suppress(Exception):
                    walk(aamp.load(self.player_pack().child(name).raw))
        return out


def default_install() -> str:
    import modman as mm
    return mm.default_install()


def find_game(install: str | None, lib=None) -> Game:
    install = install or (lib.install if lib is not None else None) or default_install()
    g = Game(os.path.abspath(install))
    if not os.path.isfile(g.path(TITLEBG)):
        raise EmoteError(f"Savage Wind's game files aren't in {g.romfs}. Set them up in Savage Wind first, or pass "
                         "--game <Savage Wind folder>.")
    return g


# ==================================================================================================== writing

def make_bas(clip: str, morph: float, face: str | None) -> bytes:
    """An animation set with one clip node (as the game's simplest player sets), and a face event."""
    params = aamp.PObject()
    params.set("TypeIndex", aamp.INT, 67)
    params.set("FileName", aamp.STRING64, clip)
    params.set("Morph", aamp.F32, float(morph))
    lists = {}
    if face:
        ev = aamp.PObject()
        ev.set("TypeIndex", aamp.INT, 7)
        ev.set("Frame", aamp.F32, 0.0)
        ev.set("Value", aamp.STRING_REF, face)
        lists[aamp.name_hash("Extend")] = aamp.PList(lists={aamp.name_hash("TriggerEvents"): aamp.PList(
            objects={aamp.name_hash("Event0"): ev})})
    element = aamp.PList(objects={aamp.name_hash("Parameters"): params}, lists=lists)
    return aamp.dump(aamp.PList(lists={aamp.name_hash("Elements"): aamp.PList(
        lists={aamp.name_hash("Element0"): element})}))


def read_bas(data: bytes) -> dict:
    """The first clip node of a set: TypeIndex, FileName, Morph, faces."""
    root = aamp.load(data)
    e0 = root.list("Elements").list("Element0")
    pr = e0.object("Parameters")
    faces = []
    with contextlib.suppress(Exception):
        for o in e0.list("Extend").list("TriggerEvents").objects.values():
            if o.get_value("TypeIndex") == 7:
                faces.append(o.get_value("Value"))
    return {"TypeIndex": pr.get_value("TypeIndex"), "FileName": pr.get_value("FileName"),
            "Morph": pr.get_value("Morph"), "faces": faces}


def first_face(data: bytes) -> str | None:
    """The first face event of a game set that isn't the default face."""
    out = []

    def walk(pl):
        for o in pl.objects.values():
            with contextlib.suppress(Exception):
                v = o.get_value("Value")
                if o.get_value("TypeIndex") == 7 and isinstance(v, str) and v.startswith("Face") and v != "FaceDefault":
                    out.append(v)
        for sub in pl.lists.values():
            walk(sub)
    walk(aamp.load(data))
    return out[0] if out else None


def aslist_log(emotes: list[dict]) -> bnp.Pio:
    """logs/aslist.aamp: our archive and the sets, sorted by name bytes."""
    defs = sorted((SET_PREFIX + d["id"], BAS_PREFIX + d["id"]) for d in emotes)
    defs.sort(key=lambda t: t[0].encode())
    objs = {}
    for i, (name, filename) in enumerate(defs):
        objs[zlib.crc32(f"ASDefine_{i}".encode())] = bnp.PObj({bnp.P_NAME: (8, name.encode()),
                                                                bnp.P_FILENAME: (8, filename.encode())})
    body = bnp.PList(lists={
        bnp.ADD_RESES: bnp.PList(objs={zlib.crc32(b"AddRes_0"): bnp.PObj({bnp.P_ANIM: (8, ARCHIVE.encode())})}),
        bnp.AS_DEFINES: bnp.PList(objs=objs),
        bnp.CF_DEFINES: bnp.PList()})
    ft = bnp.PObj({zlib.crc32(b"File0"): (20, ASLIST_PATH.encode())})
    return bnp.Pio(version=0, flags=3, type=b"\0\0\0\0", root_crc=bnp.PARAM_ROOT,
                   root=bnp.PList(lists={zlib.crc32(ASLIST_PATH.encode()): body}, objs={bnp.FILE_TABLE: ft}))


def list_names(root: bnp.PList) -> tuple[list[str], list[str]]:
    """(archives, set names in file order) of an animation list."""
    anims = [bnp._text(o, bnp.P_ANIM).decode() for o in (root.lists.get(bnp.ADD_RESES) or bnp.PList()).objs.values()
             if bnp._text(o, bnp.P_ANIM) is not None]
    names = [bnp._text(o, bnp.P_NAME).decode() for o in (root.lists.get(bnp.AS_DEFINES) or bnp.PList()).objs.values()
             if bnp._text(o, bnp.P_NAME) is not None]
    return anims, names


def bsearch(names: list[str], key: str) -> bool:
    """The game's lookup: a binary search on the name bytes."""
    lo, hi = 0, len(names) - 1
    k = key.encode()
    while lo <= hi:
        mid = (lo + hi) // 2
        m = names[mid].encode()
        if m == k:
            return True
        if m < k:
            lo = mid + 1
        else:
            hi = mid - 1
    return False


def header_text(emotes: list[dict]) -> str:
    """native/examples/emotes/emote_list.h: the emote table the DLL compiles in."""
    page_no = {p: i + 1 for i, p in enumerate(PAGES)}
    rows = []
    for d in emotes:
        aliases = "|".join(a.lower() for a in d.get("aliases", []))
        rows.append(f'    {{"{d["id"]}", "{d["name"]}", "{SET_PREFIX}{d["id"]}", "{d.get("fallback", "")}", '
                    f'"{aliases}", {page_no.get(d["page"], 0)}, {d.get("slot", 0)}, {1 if d["loop"] else 0}, '
                    f'{KINDS.index(d["kind"])}}},')
    return "\n".join([
        "// Generated by tools/emote_build.py header from tools/emotes/*.json: do not edit (tests check it).",
        "// The emotes the DLL knows: id, wheel name, animation set, the game set it plays when the anims package is",
        "// missing (\"\": none), console aliases (| between), page (1 hello, 2 dances, 3 hyrule, 0 special), slot,",
        "// loop, kind (0 our keyframes, 1 a game clip, 2 a copy of a game set).",
        "#pragma once",
        "",
        "typedef struct {",
        "    const char *id, *name, *set, *fallback, *aliases;",
        "    unsigned char page, slot, loop, kind;",
        "} EmoteInfo;",
        "",
        f"#define EMOTE_COUNT {len(emotes)}",
        f"#define EMOTE_SET_PREFIX \"{SET_PREFIX}\"",
        "static const EmoteInfo EMOTES[EMOTE_COUNT] = {",
        *rows,
        "};",
        ""])


MOD_TOML = os.path.join(REPO, "native", "examples", "emotes", "mod.toml")
GEN_BEGIN = "# ---- generated by tools/emote_build.py header (the favourites and the Play page): do not edit below"
GEN_END = "# ---- end of generated settings"
FAV_KEYS = (("5", "thumbs_up"), ("6", "thumbs_down"), ("7", "six_seven"), ("8", "in_love"), ("9", "floss"),
            ("0", "ragdoll"))


def button_text(d: dict, cap: int = BUTTON_TEXT_MAX) -> str:
    """The Play button's line: button_text, else about cut at a word boundary (ending in "...") to fit cap."""
    text = (d.get("button_text") or d["about"]).replace('"', "'").strip()
    if len(text) <= cap:
        return text
    cut = text[: cap - 3].rstrip()
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:(") + "..."


def settings_block(emotes: list[dict]) -> str:
    """The mod.toml settings that list every emote: the 6 favourite keys (choices) and one Play button each."""
    choices = "\n".join(f'  {{ value = "{d["id"]}", label = "{d["name"]}" }},' for d in emotes)
    out = [GEN_BEGIN]
    for key, default in FAV_KEYS:
        out += ["", "[[settings]]", f'key = "fav_{key}"', "type = \"choice\"", f'label = "Key {key}"',
                f'description = "The emote key {key} plays (the numpad\'s {key} too)."', f'default = "{default}"',
                'applies = "live"', 'page = "keys"', "choices = [", choices, "]"]
    for d in emotes:
        out += ["", "[[settings]]", f'key = "play_{d["id"]}"', 'type = "button"', f'label = "{d["name"]}"',
                'button = "Play"', f'description = "{button_text(d)}"', 'page = "play"']
    out += [GEN_END]
    return "\n".join(out) + "\n"


def mod_toml_text(current: str, emotes: list[dict]) -> str:
    """mod.toml with its generated block made from the emote list (the rest kept as it is)."""
    head, sep, rest = current.partition(GEN_BEGIN)
    if not sep:
        raise EmoteError(f"{MOD_TOML} has no generated block (a line starting {GEN_BEGIN[:30]!r})")
    _, sep2, tail = rest.partition(GEN_END)
    if not sep2:
        raise EmoteError(f"{MOD_TOML}: the generated block has no end line")
    return head + settings_block(emotes) + tail.lstrip("\n")


@dataclass
class Built:
    folder: str
    clips: int = 0
    sets: int = 0
    archive_size: int = 0
    readback_deg: float = 0.0
    notes: list = field(default_factory=list)
    record: dict = field(default_factory=dict)


def _write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
    with open(path, mode, **({} if mode == "wb" else {"encoding": "utf-8", "newline": "\n"})) as f:
        f.write(data)


def mod_toml() -> str:
    return "\n".join([
        "# Built on this PC by tools/emote_build.py from the player's own game files and our keyframes",
        "# (tools/emotes). Part of the Emotes mod: used only while it is on.",
        "",
        "[mod]",
        f'id = "{PKG_ID}"',
        'name = "Emote animations"',
        f'version = "{VERSION}"',
        'authors = ["giruka"]',
        'description = "The animations of the Emotes mod (built on your PC from your own game files)."',
        'game_versions = ["1.9.0"]',
        f'part_of = "{MOD_ID}"',
        "secret = true",
        ""])


@dataclass
class Prepared:
    skel: Skeleton
    idle: Idle
    template: list
    solver: Solver


def prepare(game: Game, log=None) -> tuple[Prepared, object]:
    _log(log, "Reading Link's animations from your game files (about 10 s)...")
    res = game.anims()
    if not res.models:
        raise EmoteError("Player_Animation has no dummy skeleton model")
    skel = skeleton_from_model(res.models[0])
    wait = next((a for a in res.skel_anims if a.name == IDLE_CLIP), None)
    if wait is None:
        raise EmoteError(f"Player_Animation has no {IDLE_CLIP}")
    idle = idle_from_clip(skel, wait)
    return Prepared(skel, idle, template_from_clip(wait), Solver(skel, idle)), res


def solve_all(prep: Prepared, emotes: list[dict], log=None, checks: bool = True) -> tuple[dict, list[str]]:
    """Every keys emote solved; problems from expect and sanity checks."""
    clips, problems = {}, []
    for d in emotes:
        if d["kind"] != "keys":
            continue
        clip = sample(prep.solver, d)
        clips[d["id"]] = clip
        if checks:
            for e in d.get("expect", []):
                r = run_expect(prep.skel, clip, e, prep.solver.idle_P)
                if r:
                    problems.append(f"{d['id']}: expect {r}")
            for s in sanity(prep.skel, clip, prep.solver.idle_P):
                problems.append(f"{d['id']}: {s}")
    _log(log, f"Solved {len(clips)} keyframed emotes.")
    return clips, problems


def build_package(game: Game, emotes: list[dict], out: str, *, log=None, strict: bool = True) -> Built:
    """Write giruka.emotes-anims (a package folder) into out/<PKG_ID>. Reads only the player's game files."""
    from wiiu_model import checks, nx_model
    from wiiu_model.neutral import ResFile
    import ukmm
    import wiiu_convert as wc
    folder = os.path.join(out, PKG_ID)
    if os.path.isdir(folder) and os.listdir(folder):
        raise EmoteError(f"{folder} isn't empty")
    problems = lint_all(emotes)
    if problems:
        raise EmoteError("the keyframe files have problems: " + "; ".join(problems[:5]))
    built = Built(folder)
    prep, res = prepare(game, log)
    game_sets = game.set_names()
    if any(n.startswith(SET_PREFIX) for n in game_sets):
        raise EmoteError(f"Your game already has a set starting with {SET_PREFIX}")
    last = max(game_sets, key=lambda s: s.encode()) if game_sets else ""
    if last.encode() >= SET_PREFIX.encode():
        raise EmoteError(f"Your game's last set name {last} sorts after {SET_PREFIX}: the list merge would hide ours")
    game_clips = {a.name: a for a in res.skel_anims}
    clips, probs = solve_all(prep, emotes, log)
    if probs:
        if strict:
            raise EmoteError("pose checks failed: " + "; ".join(probs[:6]))
        built.notes += probs
    faces = game.face_values()

    def face_value(d) -> str | None:
        for v in FACES.get(d.get("face", "none"), []):
            if v in faces:
                return v
        if d.get("face", "none") != "none":
            built.notes.append(f"{d['id']}: your game has no face event for {d.get('face')!r}: none used")
        return None

    # the archive
    _log(log, "Writing the animation archive...")
    datas = [build_clip(prep.skel, prep.template, clips[d["id"]], CLIP_PREFIX + d["id"]) for d in emotes
             if d["kind"] == "keys"]
    for d in emotes:
        if d["kind"] == "keys" and CLIP_PREFIX + d["id"] in game_clips:
            raise EmoteError(f"Your game already has a clip called {CLIP_PREFIX}{d['id']}")
    anims = [to_skel_anim(cd) for cd in datas]
    rf = ResFile(ARCHIVE, [res.models[0]], anims, [], [], dict(res.extra))
    data = nx_model.write(rf)
    bad = checks.switch_model_problems(data)
    if bad:
        raise EmoteError(f"the archive fails its self-check: {'; '.join(map(str, bad[:3]))}")
    worst = readback(prep.skel, data, datas)
    if worst > READBACK_DEG:
        raise EmoteError(f"the archive reads back {worst:.3f} degrees off (at most {READBACK_DEG})")
    built.readback_deg = worst
    built.clips = len(datas)
    built.archive_size = len(data)
    pa = game.anim_blob()
    yz = ukmm.yaz0(data, pa.align)
    if wc.yaz0_decompress(yz)[0] != data:
        raise EmoteError("the archive's Yaz0 doesn't round-trip")

    # the sets
    bas_files: dict[str, bytes] = {}
    record_sets = []
    for d in emotes:
        eid = d["id"]
        clip_name, frames, face = None, d.get("frames"), None
        if d["kind"] == "keys":
            clip_name = CLIP_PREFIX + eid
            face = face_value(d)
            bas = make_bas(clip_name, d.get("morph", 6), face)
        elif d["kind"] == "game_clip":
            a = game_clips.get(d["clip"])
            if a is None:
                raise EmoteError(f"{eid}: your game has no clip {d['clip']}")
            clip_name = d["clip"]
            frames = struct.unpack_from("<i", a.head, 4)[0]
            face = face_value(d)
            bas = make_bas(clip_name, d.get("morph", 6), face)
        else:
            fn = game_sets.get(d["set"])
            src = game.bas(fn) if fn else None
            if src is None:
                raise EmoteError(f"{eid}: your game has no set {d['set']}")
            bas = src
        bas_files[f"Actor/AS/{BAS_PREFIX}{eid}.bas"] = bas
        record_sets.append({"id": eid, "name": d["name"], "set": SET_PREFIX + eid, "clip": clip_name,
                            "frames": frames, "loop": d["loop"], "kind": d["kind"], "page": d["page"],
                            "slot": d.get("slot", 0), "aliases": d.get("aliases", []), "face": face,
                            "fallback": d.get("fallback", ""), "bas_bytes": len(bas)})
    built.sets = len(bas_files)

    # the packs: TitleBG.pack with our archive and the player pack holding only our sets (deploy fills the rest)
    tb = game.titlebg()
    tb_files = tb.open()
    pp = game.player_pack()
    pp_items = [(n, b, 8) for n, b in sorted(bas_files.items())]
    pp_raw = bnp.sarc_build(pp_items, pp.key)
    pp_data = ukmm.yaz0(pp_raw, pp.align) if pp.yaz0 else pp_raw
    tb_items = [(f"Model/{ARCHIVE}.sbfres", yz, tb_files[ANIM_FILE][1]),
                (PLAYER_PACK, pp_data, tb_files[PLAYER_PACK][1])]
    tb_raw = bnp.sarc_build(tb_items, tb.key)
    romfs = os.path.join(folder, BASE_TID, "romfs")
    _write(os.path.join(romfs, *TITLEBG.split("/")), tb_raw)

    # logs
    log_pio = aslist_log(emotes)
    rstb = bnp.load_game_rstb(os.path.join(game.install, "extracted"))
    sizes: dict[str, int] = {}
    if rstb is None:
        built.notes.append("the game's size table isn't there: no size entries")
    else:
        g = rstb.get("Model/Player_Animation.bfres")
        if g:
            sizes[f"Model/{ARCHIVE}.bfres"] = ukmm.estimate_entry(len(pa.raw), g, len(data), False)
        ref = pp.child(BAS_SIZE_REF)
        g2 = rstb.get(BAS_SIZE_REF)
        for n, b in bas_files.items():
            if g2 and ref is not None:
                sizes[n] = ukmm.estimate_entry(len(ref.raw), g2, len(b), False)
    logs = {"aslist.aamp": bnp.aamp_write(log_pio), "rstb.json": json.dumps(dict(sorted(sizes.items())),
                                                                             indent=1) + "\n"}
    for name, content in logs.items():
        _write(os.path.join(folder, "logs", name), content)
    built.record = {"tool": "tools/emote_build.py", "version": VERSION, "archive": ARCHIVE,
                    "archive_bytes": len(data), "sets": record_sets,
                    "inputs": dict(sorted(game.hashes.items())),
                    "readback_degrees": round(worst, 4)}
    _write(os.path.join(folder, "emotes.json"), json.dumps(built.record, indent=1) + "\n")
    _write(os.path.join(folder, "mod.toml"), mod_toml())
    _write(os.path.join(folder, "info.json"), json.dumps({
        "name": "Emote animations", "version": VERSION, "desc": "The animations of the Emotes mod.",
        "platform": "switch", "options": {}}, indent=1) + "\n")
    _write(os.path.join(folder, "BUILT.md"), "\n".join([
        "# Emote animations: built on this PC by tools/emote_build.py from the player's own game files.", "",
        f"- archive {ARCHIVE}: {built.clips} clips, {len(data)} bytes ({len(yz)} with Yaz0), read back within "
        f"{worst:.3f} degrees",
        f"- sets: {built.sets} ({', '.join(SET_PREFIX + d['id'] for d in emotes)})",
        *[f"- note: {n}" for n in built.notes], ""]))
    return built


def readback(skel: Skeleton, data: bytes, datas: list[ClipData]) -> float:
    """Worst difference (degrees) between our authored Euler angles and the written archive's, every frame."""
    from wiiu_model import nx_model
    back = nx_model.read(data)
    by_name = {a.name: a for a in back.skel_anims}
    worst = 0.0
    for cd in datas:
        a = by_name.get(cd.name)
        if a is None:
            return 999.0
        for fr in range(cd.frames + 1):
            eul, _ = clip_pose(skel, a, float(fr))
            for name, seq in cd.eulers.items():
                got = eul[skel.idx[name]]
                for j in range(3):
                    worst = max(worst, abs(got[j] - seq[fr][j]))
    return math.degrees(worst)


def merged_list(game: Game, emotes: list[dict], extra_logs: list[bnp.Pio] = ()) -> bnp.PList:
    """The game's list with other logs (Linkle's) merged first, then ours: what deploy makes."""
    base = bnp.aamp_read(bnp.aamp_write(game.aslist()))
    key = zlib.crc32(ASLIST_PATH.encode())
    for p in list(extra_logs) + [aslist_log(emotes)]:
        if key in p.root.lists:
            bnp.aslist_merge(base.root, p.root.lists[key], [])
    return base.root


# ==================================================================================================== library

def add_to_library(lib, folder: str, mod_id: str, log=None) -> tuple[dict, list[str]]:
    """The built folder into the library through modman's normal add (an old copy removed first, its on/off and
    place kept; a new one goes last, on)."""
    import modman as mm
    idx = next((i for i, m in enumerate(lib.mods) if m["id"] == mod_id), None)
    was_on = lib.mods[idx]["enabled"] if idx is not None else True
    if idx is not None:
        mm.remove_mod(lib, mod_id)
    elif os.path.isdir(os.path.join(lib.mods_dir, mod_id)):
        raise EmoteError(f"{os.path.join(lib.mods_dir, mod_id)} is there but the library doesn't list it: move that "
                         "folder away and build again.")
    _log(log, "Adding it to your mod library (modman add)...")
    try:
        entry, _scan, notes = mm.add_mod(lib, folder)
    except mm.ModmanError as e:
        raise EmoteError(str(e)) from None
    i = next(i for i, m in enumerate(lib.mods) if m["id"] == entry["id"])
    m = lib.mods.pop(i)
    m["enabled"] = was_on
    lib.mods.insert(idx if idx is not None else len(lib.mods), m)
    lib.save()
    return m, notes


def select(emotes: list[dict], only: str | None) -> list[dict]:
    if not only:
        return emotes
    want = [w.strip() for w in only.split(",") if w.strip()]
    ids = {d["id"] for d in emotes}
    bad = [w for w in want if w not in ids]
    if bad:
        raise EmoteError(f"no emote called {', '.join(bad)}")
    return [d for d in emotes if d["id"] in want]


def build(library: str | None = None, *, install: str | None = None, out: str | None = None, add: bool = True,
          only: str | None = None, log=None) -> dict:
    import modman as mm
    emotes = select(load_emotes(), only)
    root = mm.resolve_library(library)
    lib = mm.Library.load(root)
    if add and lib is None:
        raise EmoteError(f"No mod library in {root} yet. Run: python tools/modman.py init")
    game = find_game(install, lib)
    tmp = None
    if out is None:
        tmp = tempfile.mkdtemp(prefix="emotes-build-")
        out = os.path.join(tmp, "out")
    out = os.path.abspath(out)
    try:
        built = build_package(game, emotes, out, log=log)
        entry, notes = None, []
        if add:
            entry, notes = add_to_library(lib, built.folder, PKG_ID, log)
        return {"built": built, "entry": entry, "notes": notes}
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def check(install: str | None = None, library: str | None = None, log=None) -> list[str]:
    """Offline: lint, solve, pose checks, write the archive, read it back, merge the list. Writes nothing."""
    import modman as mm
    emotes = load_emotes()
    problems = lint_all(emotes)
    if problems:
        return problems
    lib = mm.Library.load(mm.resolve_library(library))
    game = find_game(install, lib)
    tmp = tempfile.mkdtemp(prefix="emotes-check-")
    try:
        built = build_package(game, emotes, tmp, log=log, strict=False)
        problems += built.notes
        _log(log, f"Archive: {built.clips} clips, {built.archive_size} bytes, read back within "
                  f"{built.readback_deg:.3f} degrees; {built.sets} sets.")
        root = merged_list(game, emotes)
        anims, names = list_names(root)
        for d in emotes:
            if not bsearch(names, SET_PREFIX + d["id"]):
                problems.append(f"the merged list doesn't find {SET_PREFIX}{d['id']}")
        if not bsearch(names, "Wait"):
            problems.append("the merged list doesn't find Wait")
        if ARCHIVE not in anims:
            problems.append("the merged list lacks our archive")
        _log(log, f"Merged list: {len(anims)} archives, {len(names)} sets, sorted "
                  f"{names == sorted(names, key=lambda s: s.encode())}.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return problems


# ==================================================================================================== preview

class Canvas:
    """An RGB picture with thick lines and dots (for previews; PNG out, no window)."""

    def __init__(self, w: int, h: int, bg=(255, 255, 255)):
        self.w, self.h = w, h
        self.px = bytearray(bytes(bg) * (w * h))

    def dot(self, x: float, y: float, r: float, c) -> None:
        x0, x1 = int(math.floor(x - r)), int(math.ceil(x + r))
        y0, y1 = int(math.floor(y - r)), int(math.ceil(y + r))
        rr = r * r
        for yy in range(max(0, y0), min(self.h, y1 + 1)):
            for xx in range(max(0, x0), min(self.w, x1 + 1)):
                if (xx - x) ** 2 + (yy - y) ** 2 <= rr:
                    o = (yy * self.w + xx) * 3
                    self.px[o:o + 3] = bytes(c)

    def line(self, a, b, c, width: float = 2.0) -> None:
        n = max(1, int(max(abs(b[0] - a[0]), abs(b[1] - a[1]))))
        for i in range(n + 1):
            t = i / n
            self.dot(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, width / 2, c)

    def ring(self, x, y, r, c, width=2.0) -> None:
        n = max(12, int(r * 6))
        pts = [(x + r * math.cos(2 * math.pi * i / n), y + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
        for p, q in zip(pts, pts[1:]):
            self.line(p, q, c, width)

    def png(self) -> bytes:
        stride = self.w * 3
        raw = b"".join(b"\0" + bytes(self.px[y * stride:(y + 1) * stride]) for y in range(self.h))

        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


LIMB_CHAINS = (
    ("c", ["Skl_Root", "Spine_2", "Neck", "Head"]),
    ("L", ["Spine_2", "Clavicle_L", "Arm_1_L", "Arm_2_L", "Wrist_L", "Finger_C_3_L"]),
    ("R", ["Spine_2", "Clavicle_R", "Arm_1_R", "Arm_2_R", "Wrist_R", "Finger_C_3_R"]),
    ("L", ["Wrist_L", "Finger_A_1_L", "Finger_A_2_L", "Finger_A_3_L"]),
    ("R", ["Wrist_R", "Finger_A_1_R", "Finger_A_2_R", "Finger_A_3_R"]),
    ("L", ["Wrist_L", "Finger_B_1_L", "Finger_B_2_L", "Finger_B_3_L"]),
    ("R", ["Wrist_R", "Finger_B_1_R", "Finger_B_2_R", "Finger_B_3_R"]),
    ("L", ["Skl_Root", "Leg_1_L", "Leg_2_L", "Ankle_L", "Toe_L"]),
    ("R", ["Skl_Root", "Leg_1_R", "Leg_2_R", "Ankle_R", "Toe_R"]),
)
COLORS = {"c": (40, 40, 40), "L": (40, 90, 220), "R": (215, 50, 40)}


def preview_frames(nfr: int, keys: list[int], count: int = 8) -> list[int]:
    picks = sorted(set([0, nfr] + keys))
    while len(picks) < count:
        gaps = [(picks[i + 1] - picks[i], i) for i in range(len(picks) - 1)]
        g, i = max(gaps)
        if g < 2:
            break
        picks.insert(i + 1, picks[i] + g // 2)
    if len(picks) > count:
        step = (len(picks) - 1) / (count - 1)
        picks = [picks[round(i * step)] for i in range(count)]
    return picks


def draw_figure(cv: Canvas, skel: Skeleton, Wr, Wp, ox: float, oy: float, scale: float, view: str) -> None:
    def P(p):
        h = p[0] if view == "front" else p[2]
        return (ox + h * scale, oy - p[1] * scale)
    cv.line((ox - 0.4 * scale, oy), (ox + 0.4 * scale, oy), (170, 170, 170), 1.5)
    order = LIMB_CHAINS if view == "front" else tuple(sorted(LIMB_CHAINS, key=lambda c: {"R": 0, "c": 1, "L": 2}[c[0]]))
    for side, chain in order:
        pts = [P(Wp[skel.idx[n]]) for n in chain if skel.has(n)]
        for a, b in zip(pts, pts[1:]):
            cv.line(a, b, COLORS[side], 3.0 if side != "c" else 3.5)
    if skel.has("Head"):
        h = skel.idx["Head"]
        up = mvec(Wr[h], mvec(mT(skel.rest_W[h]), (0.0, 1.0, 0.0)))
        fwd = mvec(Wr[h], mvec(mT(skel.rest_W[h]), (0.0, 0.0, 1.0)))
        c = vadd(Wp[h], vscale(up, 0.1))
        cx, cy = P(c)
        cv.ring(cx, cy, 0.1 * scale, COLORS["c"], 2.5)
        nose = vadd(c, vscale(fwd, 0.16))
        cv.line(P(c), P(nose), (20, 150, 60), 2.5)


def render_preview(skel: Skeleton, clip: Clip, scale: float = 95.0) -> Canvas:
    nfr = len(clip.frames) - 1
    picks = preview_frames(nfr, clip.key_frames)
    cell_w, cell_h = int(1.1 * scale) + 20, int(2.05 * scale)
    cv = Canvas(cell_w * len(picks), cell_h * 2 + 8)
    for col, fr in enumerate(picks):
        rots, trans = clip.frames[fr]
        Wr, Wp = fk(skel, rots, trans)
        for row, view in enumerate(("front", "side")):
            ox = col * cell_w + cell_w / 2
            oy = row * (cell_h + 8) + cell_h - 12
            draw_figure(cv, skel, Wr, Wp, ox, oy, scale, view)
        # a tick per frame position (fraction of the clip) under the top row
        x0 = col * cell_w + 6
        cv.line((x0, cell_h + 2), (x0 + (cell_w - 12) * fr / max(1, nfr), cell_h + 2), (120, 120, 120), 3.0)
    return cv


def render_svg(skel: Skeleton, clip: Clip, scale: float = 95.0) -> str:
    nfr = len(clip.frames) - 1
    picks = preview_frames(nfr, clip.key_frames)
    cell_w, cell_h = int(1.1 * scale) + 20, int(2.05 * scale)
    w, h = cell_w * len(picks), cell_h * 2 + 8
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="sans-serif" font-size="12">',
           f'<rect width="{w}" height="{h}" fill="#fff"/>']
    hexc = {k: "#%02x%02x%02x" % v for k, v in COLORS.items()}
    for col, fr in enumerate(picks):
        rots, trans = clip.frames[fr]
        _, Wp = fk(skel, rots, trans)
        for row, view in enumerate(("front", "side")):
            ox, oy = col * cell_w + cell_w / 2, row * (cell_h + 8) + cell_h - 12
            for side, chain in LIMB_CHAINS:
                pts = " ".join(f"{ox + (Wp[skel.idx[n]][0] if view == 'front' else Wp[skel.idx[n]][2]) * scale:.1f},"
                               f"{oy - Wp[skel.idx[n]][1] * scale:.1f}" for n in chain if skel.has(n))
                out.append(f'<polyline points="{pts}" fill="none" stroke="{hexc[side]}" stroke-width="3"/>')
        out.append(f'<text x="{col * cell_w + 6}" y="14">{clip.spec["id"]} f{fr}</text>')
    out.append("</svg>")
    return "\n".join(out)


def write_preview(skel, clip, path: str) -> None:
    if path.lower().endswith(".svg"):
        _write(path, render_svg(skel, clip))
    else:
        _write(path, render_preview(skel, clip).png())


def preview_solver(install: str | None, library: str | None, demo: bool, log=None) -> Solver:
    if not demo:
        import modman as mm
        try:
            lib = mm.Library.load(mm.resolve_library(library))
            game = find_game(install, lib)
            prep, _ = prepare(game, log)
            return prep.solver
        except EmoteError as e:
            _log(log, f"({e}: using the made-up demo skeleton)")
    sk = demo_skeleton()
    return Solver(sk, demo_idle(sk))


# ==================================================================================================== command line

def _low_priority() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)   # below normal: keeps the PC (and a game) smooth
        except (OSError, AttributeError):
            pass


def list_table(emotes: list[dict]) -> str:
    rows = [f"{'id':<14} {'name':<14} {'page':<8} {'slot':>4} {'frames':>6} {'loop':<5} kind"]
    for d in emotes:
        rows.append(f"{d['id']:<14} {d['name']:<14} {d['page']:<8} {d.get('slot', 0):>4} "
                    f"{str(d.get('frames', '-')):>6} {'yes' if d.get('loop') else 'no':<5} {d['kind']}"
                    + (f" ({d.get('clip') or d.get('set')})" if d['kind'] != 'keys' else ""))
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    with contextlib.suppress(AttributeError, ValueError, OSError):
        sys.stdout.reconfigure(errors="replace")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--library", default=argparse.SUPPRESS, help="the mod library (default: as modman)")
    common.add_argument("--game", default=argparse.SUPPRESS, help="the Savage Wind folder (default: the library's)")
    ap = argparse.ArgumentParser(prog="emote_build.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--library")
    ap.add_argument("--game")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", parents=[common], help="build the emote animations and add them to the library")
    b.add_argument("--only", help="only these emotes (ids, comma between)")
    b.add_argument("--no-add", action="store_true", help="only write the package folder (into --out)")
    b.add_argument("--out", help="where to write the package folder (needed with --no-add)")
    b.add_argument("--optional", action="store_true", help="a build that can't be made says why and exits 0")
    say = b.add_mutually_exclusive_group()
    say.add_argument("--quiet", action="store_true", help="say only whether it worked")
    say.add_argument("--verbose", action="store_true", help="also list every emote built")
    sub.add_parser("check", parents=[common], help="offline checks on your files (writes nothing)")
    sub.add_parser("lint", help="check the keyframe files (no game files needed)")
    p = sub.add_parser("preview", parents=[common], help="draw stick figures to a PNG or SVG file")
    p.add_argument("id", help="an emote id, or all")
    p.add_argument("--out", required=True, help="the file to write (with all: a folder)")
    p.add_argument("--demo", action="store_true", help="the made-up skeleton (no game files)")
    sub.add_parser("list", help="the emotes")
    sub.add_parser("header", help="(developers) write native/examples/emotes/emote_list.h")
    args = ap.parse_args(argv)
    _low_priority()
    optional = args.cmd == "build" and args.optional
    quiet = getattr(args, "quiet", False)
    try:
        if args.cmd == "lint":
            problems = lint_all(load_emotes())
            print("Emote data: all good." if not problems else f"Emote data: {len(problems)} problem(s):")
            for x in problems:
                print(f"  - {x}")
            return 1 if problems else 0
        if args.cmd == "list":
            print(list_table(load_emotes()))
            return 0
        if args.cmd == "header":
            emotes = load_emotes()
            problems = lint_all(emotes)
            if problems:
                raise EmoteError("fix the data first: " + "; ".join(problems[:3]))
            _write(HEADER, header_text(emotes))
            print(f"Written: {HEADER}")
            with open(MOD_TOML, encoding="utf-8") as f:
                cur = f.read()
            new = mod_toml_text(cur, emotes)
            if new != cur:
                _write(MOD_TOML, new)
                print(f"Updated: {MOD_TOML}")
            return 0
        if args.cmd == "preview":
            emotes = [d for d in load_emotes() if d["kind"] == "keys"]
            if args.id != "all":
                emotes = [d for d in emotes if d["id"] == args.id]
                if not emotes:
                    raise EmoteError(f"no keyframed emote called {args.id}")
            solver = preview_solver(args.game, args.library, args.demo, log=print)
            for d in emotes:
                clip = sample(solver, d)
                path = os.path.join(args.out, f"{d['id']}.png") if args.id == "all" else args.out
                write_preview(solver.skel, clip, path)
                print(f"Written: {path}")
            return 0
        if args.cmd == "check":
            problems = check(args.game, args.library, log=print)
            print("Emote check: all good." if not problems else f"Emote check: {len(problems)} problem(s):")
            for x in problems:
                print(f"  - {x}")
            return 1 if problems else 0
        if args.no_add and not args.out:
            raise EmoteError("--no-add needs --out <folder>")
        r = build(args.library, install=args.game, out=args.out, add=not args.no_add, only=args.only,
                  log=None if quiet else print)
        made = r["built"]
        if quiet:
            print(f"Done: {made.sets} emote animations built" + (" and in your library." if r["entry"] else "."))
            return 0
        where = f"in your library as {r['entry']['name']}" if r["entry"] else made.folder
        print(f"Emote animations built ({made.clips} keyframed clips, {made.sets} sets): {where}.")
        if args.verbose:
            for s in made.record.get("sets", []):
                print(f"  {s['set']}: {s['kind']} {s['clip'] or ''} {s['frames']} frames")
        for n in made.notes + r["notes"]:
            print(f"  note: {n}")
        if r["entry"]:
            print("Next: python tools/modman.py deploy")
        return 0
    except EmoteError as e:
        print(f"error: {e}", file=sys.stderr)
        if not optional:
            return 2
    except Exception as e:  # noqa: BLE001 (--optional: anything else never stops the Emotes mod either)
        if not optional:
            raise
        print("error: " + "".join(traceback.format_exception(type(e), e, e.__traceback__, limit=-3)).rstrip(),
              file=sys.stderr)
    print("The emote animations weren't built (the Emotes mod still plays the game's own ones). Try again with: "
          "python tools/emote_build.py build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
