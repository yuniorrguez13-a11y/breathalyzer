#!/usr/bin/env python3
"""Dragonbreath's own attack clips (stage 3 of the Dragonbreath mod by giruka, Breathalyzer): fast, acrobatic
one-handed moves (snap cuts, a spin with two cuts, a flip finisher, a dash lunge, a five-cut flurry, a crouched
quick-draw) keyframed by us in the emotes key language, solved on Link's skeleton by tools/emote_build.py, written
into our own animation archive, and played by the game's own attack sets for the weapon sub-type `Dragonbreath`
only (a sub-type selector wraps each set: every other weapon keeps the game's tree, byte for byte below the new root).

Our own keys only: no other game's animations, models, names, timing or effects, and nothing traced.

    python tools/dragonbreath_clips.py lint                     the clip files (no game files needed)
    python tools/dragonbreath_clips.py check [--demo]           solve and check every clip (joints, feet, blade, hits)
    python tools/dragonbreath_clips.py preview [--demo] [--out DIR]   pictures of every clip (front and side, blade)
    python tools/dragonbreath_clips.py build --out DIR          the moves package (reads your game files, only reads)
    python tools/dragonbreath_clips.py build                    ... and add it to your mod library (part of the
                                                                Dragonbreath switch, hidden)

Clip files: assets/dragonbreath/clips/<id>.json (the emotes key language, docs/emotes.md, plus `blade` and `spin`)
and `_poses.json` (stances every clip may use). Extra fields: slot (the game's set: CutS1...), part (its place in
the set: a sequence when a set has more than one), hits [{start, end, side, at}] (one window per clip, clip
frames; `at` the point in front of Link the blade must sweep through), next (the frame the next attack may start).
"""
from __future__ import annotations

import copy
import glob
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import aamp  # noqa: E402
import bnp  # noqa: E402
import emote_build as eb  # noqa: E402

REPO = os.path.dirname(TOOLS)
CLIPS = os.path.join(REPO, "assets", "dragonbreath", "clips")
SHARED = "_poses.json"
OUT_PICS = os.path.join(REPO, "out", "research", "dragonbreath-clips")
VERSION = "0.1.0"
PKG_ID = "giruka.dragonbreath-moves"
PART_OF = "botw-mod-loader.dragonbreath"
ARCHIVE = "SwDragon_Animation"                 # Model/<ARCHIVE>.sbfres, AddRes in Link's animation list
CLIP_PREFIX = "SwDragon_"                      # our clip names (unique across every archive in the list)
SUBTYPE = "Dragonbreath"                       # SmallSword.WeaponSubType (stage 1's stats file)
BLADE_LEN = 1.051                              # m: the blade's tip from the weapon bone (dragonbreath.json preview_top)
# slot (the game's attack set, Actor/AS/Player_<slot>.bas) -> the game's one-handed clip whose node is the template
SLOTS = {"CutS1": "Sword_Attack_S1", "CutS2": "Sword_Attack_S2", "CutS3": "Sword_Attack_S3",
         "CutSF": "Sword_Attack_SF", "CutDash": "Sword_Attack_Dash", "CutJustRush": "Sword_Attack_Just_Rush{part}",
         "CutNinja": "Sword_Attack_Ninja"}
COMBO = ("CutS1", "CutS2", "CutS3", "CutSF")
GRIP_CLIP = "Sword_Attack_S1"                  # the game's own sword grip (Weapon_R in the hand) is read from it
SIDES = ("Left", "Right", "Lower", "Upper")
MIN_WINDOW = 3                                 # frames a hit window spans at least
HIT_CM = 25.0                                  # the blade passes this near the hit point inside its window
TIP_DEG = 50.0                                 # the blade turns at most this much a frame inside a window
FLOOR_CM = 2.0                                 # feet and toes stay above the floor (their idle height) by this
KNEE_MAX = 150.0
WRIST_MAX = 80.0                               # the hand against the forearm line
NECK_MAX = 75.0                                # the head against the chest, from the idle pose
BODY_CM = 7.0                                  # the blade stays this far from the body's middle line
MORPH = 2.0                                    # frames of blend into each of our clips (the game's are 3 to 6)
STOCK_COMBO = (74.0, 4)                        # frames, hits: the game's own 4-hit one-handed combo (CutS1..SF)
ID_RE = re.compile(r"[a-z][a-z0-9_]{0,15}")
FIELDS = {"id", "slot", "part", "name", "by", "about", "frames", "next", "hits", "max_turn", "keys", "poses",
          "expect", "contact", "lag", "ease", "note", "morph", "_file"}


class ClipError(Exception):
    pass


def _log(log, msg: str) -> None:
    if log is not None:
        log(msg)


# ==================================================================================================== data

def load_clips(folder: str = CLIPS) -> list[dict]:
    """Every clip file (sorted by slot, then part), each with the shared stances under its own poses."""
    shared = {}
    sp = os.path.join(folder, SHARED)

    def read(p: str):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError) as e:
            raise ClipError(f"{os.path.basename(p)}: not valid JSON ({e})") from None
        if not isinstance(d, dict) or not isinstance(d.get("poses") or {}, dict):
            raise ClipError(f"{os.path.basename(p)}: not a clip file (an object, its poses an object)")
        return d
    if os.path.isfile(sp):
        shared = read(sp).get("poses") or {}
    out = []
    for p in sorted(glob.glob(os.path.join(folder, "*.json"))):
        if os.path.basename(p) == SHARED:
            continue
        d = read(p)
        d["_file"] = os.path.basename(p)
        d["poses"] = {**shared, **(d.get("poses") or {})}
        out.append(d)
    order = {s: i for i, s in enumerate(SLOTS)}
    out.sort(key=lambda d: (order.get(d.get("slot"), 99) if isinstance(d.get("slot"), str) else 99,
                            d.get("part", 0) if _int(d.get("part", 0)) else 0, str(d.get("id"))))
    return out


def clip_name(c: dict) -> str:
    return CLIP_PREFIX + c["id"]


def emote_spec(c: dict) -> dict:
    """The clip as an emotes key file (tools/emote_build.py solves and checks it)."""
    spec = {"id": c["id"], "name": c["id"], "page": "poses", "slot": 1, "by": c.get("by", ""),
            "about": c.get("about", ""), "kind": "keys", "frames": c.get("frames"), "loop": False, "face": "none",
            "poses": c.get("poses", {}), "keys": c.get("keys"), "expect": c.get("expect", []),
            "contact": c.get("contact", ["Wrist_L"])}
    for k in ("max_turn", "lag", "ease"):
        if k in c:
            spec[k] = c[k]
    return spec


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def lint(clips: list[dict], skel=None) -> list[str]:
    """Problems in the clip files (empty: fine)."""
    p: list[str] = []
    seen: dict[str, str] = {}
    parts: dict[str, list[int]] = {}
    last_part: dict = {}         # slot -> its last part: only that one's `next` is used (the others play to the end)
    for c in clips:
        if isinstance(c, dict) and isinstance(c.get("slot"), str) and _int(c.get("part", 1)):
            last_part[c.get("slot")] = max(last_part.get(c.get("slot"), 1), c.get("part", 1))
    for c in clips:
        fn = c.get("_file", "?")
        cid = c.get("id")
        if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
            p.append(f"{fn}: id is 1 to 16 lowercase letters, digits and _, starting with a letter")
            continue
        if fn != "?" and fn != f"{cid}.json":
            p.append(f"{fn}: the file must be called {cid}.json")
        if cid in seen:
            p.append(f"{cid}: the id is used twice ({seen[cid]})")
        seen[cid] = fn
        for k in c:
            if k not in FIELDS:
                p.append(f"{cid}: unknown field {k!r}")
        slot = c.get("slot")
        if slot not in SLOTS:
            p.append(f"{cid}: slot is one of {', '.join(SLOTS)}")
        part = c.get("part", 1)
        if not _int(part) or not 1 <= part <= 8:
            p.append(f"{cid}: part is 1 to 8 (its place in the set)")
        elif slot in SLOTS:
            parts.setdefault(slot, []).append(part)
        for k in ("name", "by", "about"):
            if not isinstance(c.get(k), str) or not c[k].strip():
                p.append(f"{cid}: {k} is needed")
        frames = c.get("frames")
        if not _int(frames) or not 2 <= frames <= 120:
            p.append(f"{cid}: frames is 2 to 120 (30 = 1 second)")
            continue
        hits = c.get("hits")
        if not isinstance(hits, list) or len(hits) != 1:
            p.append(f"{cid}: hits is a list of exactly one window (a second cut is a second part of the set)")
            continue
        h = hits[0]
        if not isinstance(h, dict) or set(h) - {"start", "end", "side", "at", "power"}:
            p.append(f"{cid}: a hit is {{start, end, side, at}}")
            continue
        st, en = h.get("start"), h.get("end")
        if not (_int(st) and _int(en) and 0 <= st < en <= frames):
            p.append(f"{cid}: a hit's start and end are frames, start before end, within the clip")
            continue
        if en - st + 1 < MIN_WINDOW:
            p.append(f"{cid}: a hit window spans at least {MIN_WINDOW} frames (frames {st} to {en} is "
                     f"{en - st + 1})")
        if h.get("side", "Left") not in SIDES:
            p.append(f"{cid}: side is one of {', '.join(SIDES)}")
        at = h.get("at")
        if not (isinstance(at, list) and len(at) == 3 and all(isinstance(x, (int, float)) for x in at)):
            p.append(f"{cid}: at is the point [x, y, z] the blade must sweep through")
        nx = c.get("next")
        if (not isinstance(slot, str) or part == last_part.get(slot, part)) and (not _int(nx) or not en < nx <= frames):
            p.append(f"{cid}: next (the frame the next attack may start) is after the hit, at most frames")
        p += [f"{cid}: {x}" if not x.startswith(cid) else x for x in eb.lint_one(emote_spec(c), skel)]
    for slot, ps in parts.items():
        if sorted(ps) != list(range(1, len(ps) + 1)):
            p.append(f"{slot}: parts must be 1, 2, ... with none missing or twice (got {sorted(ps)})")
    return sorted(set(p), key=p.index)


# ==================================================================================================== rigs

@dataclass
class Rig:
    skel: object
    idle: object
    fixed: dict
    solver: object
    template: list = field(default_factory=list)
    res: object = None
    source: str = "demo"


def demo_rig() -> Rig:
    """The emotes' made-up skeleton with a weapon bone in the right fist (no game files: tests and previews)."""
    sk = eb.demo_skeleton()
    bones = list(sk.bones)
    wr = sk.idx["Wrist_R"]
    bones.append(eb.Bone("Weapon_R", wr, (-0.085, -0.02, 0.0), (0.0, 0.0, 0.0)))
    skel = eb.Skeleton(bones)
    idle = eb.demo_idle(skel)
    fixed = {"Weapon_R": (eb.I3, (-0.085, -0.02, 0.0))}
    return Rig(skel, idle, fixed, eb.Solver(skel, idle, fixed), eb.plain_template(skel), None, "demo")


def game_rig(game, log=None) -> Rig:
    """Link's skeleton and idle pose from the player's game files; the sword grip from the game's own sword clip."""
    prep, res = eb.prepare(game, log)
    a = next((x for x in res.skel_anims if x.name == GRIP_CLIP), None)
    if a is None:
        raise ClipError(f"Your Player_Animation has no {GRIP_CLIP} (the sword grip is read from it)")
    if not prep.skel.has("Weapon_R"):
        raise ClipError("Link's skeleton has no Weapon_R bone")
    eul, tr = eb.clip_pose(prep.skel, a, 0.0)
    i = prep.skel.idx["Weapon_R"]
    fixed = {"Weapon_R": (eb.e2m(eul[i]), tuple(tr[i]))}
    return Rig(prep.skel, prep.idle, fixed, eb.Solver(prep.skel, prep.idle, fixed), prep.template, res, "game")


def solve(rig: Rig, c: dict):
    return eb.sample(rig.solver, emote_spec(c))


# ==================================================================================================== checks

def _seg_point(a, b, p) -> float:
    ab = eb.vsub(b, a)
    t = max(0.0, min(1.0, eb.dot(eb.vsub(p, a), ab) / max(eb.dot(ab, ab), 1e-12)))
    return eb.vlen(eb.vsub(p, eb.vadd(a, eb.vscale(ab, t))))


def _ang(u, v) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, eb.dot(eb.unit(u), eb.unit(v))))))


def _lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


@dataclass
class Report:
    cid: str
    problems: list
    hit_cm: float = 0.0              # the blade's nearest pass to the hit point inside the window
    tip_deg: float = 0.0             # the blade's fastest turn a frame inside the window
    edge_lead: str = ""              # frames of the window where the edge leads the swing
    lowest_cm: float = 0.0           # the lowest foot or toe against its idle height
    highest_hips_cm: float = 0.0


def check_clip(rig: Rig, c: dict, clip) -> Report:
    """Pose checks (the emotes' sanity and expect checks), joint limits, the floor, the blade (hit point, speed,
    the body) on every frame."""
    skel = rig.skel
    S = rig.solver
    rep = Report(c["id"], [])
    for e in c.get("expect", []):
        r = eb.run_expect(skel, clip, e, S.idle_P)
        if r:
            rep.problems.append(f"expect {r}")
    rep.problems += eb.sanity(skel, clip, S.idle_P)
    worlds = [eb.fk(skel, r, t) for r, t in clip.frames]
    has = skel.has
    idle_rel = None
    if has("Head") and has("Spine_2"):
        iw = S.idle_W
        idle_rel = eb.mmul(eb.mT(iw[skel.idx["Spine_2"]]), iw[skel.idx["Head"]])
    lowest = 0.0
    blades = []
    for fr, (Wr, Wp) in enumerate(worlds):
        P = lambda n: Wp[skel.idx[n]]  # noqa: E731
        for s in "LR":
            if all(has(x) for x in (f"Leg_1_{s}", f"Leg_2_{s}", f"Ankle_{s}")):
                bend = _ang(eb.vsub(P(f"Leg_2_{s}"), P(f"Leg_1_{s}")), eb.vsub(P(f"Ankle_{s}"), P(f"Leg_2_{s}")))
                if bend > KNEE_MAX:
                    rep.problems.append(f"frame {fr}: knee {s} bends {bend:.0f} degrees (at most {KNEE_MAX:.0f})")
            tip = eb.TIP.get(f"Wrist_{s}")
            if tip and all(has(x) for x in (f"Arm_2_{s}", f"Wrist_{s}", tip)):
                bend = _ang(eb.vsub(P(f"Wrist_{s}"), P(f"Arm_2_{s}")), eb.vsub(P(tip), P(f"Wrist_{s}")))
                if bend > WRIST_MAX:
                    rep.problems.append(f"frame {fr}: wrist {s} bends {bend:.0f} degrees (at most {WRIST_MAX:.0f})")
            for n in (f"Ankle_{s}", f"Toe_{s}"):
                if has(n):
                    d = (Wp[skel.idx[n]][1] - S.idle_P[skel.idx[n]][1]) * 100
                    lowest = min(lowest, d)
                    if d < -FLOOR_CM:
                        rep.problems.append(f"frame {fr}: {n} is {-d:.1f} cm below the floor")
        if idle_rel is not None:
            rel = eb.mmul(eb.mT(Wr[skel.idx["Spine_2"]]), Wr[skel.idx["Head"]])
            a = eb.angle_between_m(rel, idle_rel)
            if a > NECK_MAX:
                rep.problems.append(f"frame {fr}: the neck turns {a:.0f} degrees (at most {NECK_MAX:.0f})")
        bl = eb.weapon_line(skel, Wr, Wp, "Wrist_R", BLADE_LEN)
        blades.append(bl)
        if bl is not None and has("Skl_Root") and has("Neck"):
            lo, hi = P("Skl_Root"), P("Neck")
            for t in (0.25, 0.5, 0.75, 1.0):
                q = _lerp(bl[0], bl[1], t)
                d = _seg_point(lo, hi, q) * 100
                if d < BODY_CM:
                    rep.problems.append(f"frame {fr}: the blade goes through the body ({d:.1f} cm from its middle)")
                    break
    rep.lowest_cm = lowest
    if has("Skl_Root"):
        rep.highest_hips_cm = max((Wp[skel.idx["Skl_Root"]][1] - S.idle_P[skel.idx["Skl_Root"]][1]) * 100
                                  for _, Wp in worlds)
    last = worlds[-1][1]
    for s in "LR":
        n = f"Ankle_{s}"
        if has(n) and abs(last[skel.idx[n]][1] - S.idle_P[skel.idx[n]][1]) * 100 > 3.0:
            rep.problems.append(f"the last frame: {n} isn't on the ground")
    if any(b is None for b in blades):
        rep.problems.append("the skeleton has no Weapon_R bone: the blade can't be checked")
        return rep
    for h in c["hits"]:
        st, en = h["start"], h["end"]
        best = 1e9
        for fr in range(st, en + 1):
            for k in range(4):
                if fr == en and k:
                    break
                t = k / 4.0
                b0, b1 = blades[fr], blades[min(fr + 1, len(blades) - 1)]
                g, tp = _lerp(b0[0], b1[0], t), _lerp(b0[1], b1[1], t)
                best = min(best, _seg_point(g, tp, tuple(h["at"])))
        rep.hit_cm = best * 100
        if rep.hit_cm > HIT_CM:
            rep.problems.append(f"hit frames {st}-{en}: the blade passes {rep.hit_cm:.0f} cm from its hit point "
                                f"(at most {HIT_CM:.0f})")
        fast, lead = 0.0, 0
        for fr in range(st, en):
            d0 = eb.vsub(blades[fr][1], blades[fr][0])
            d1 = eb.vsub(blades[fr + 1][1], blades[fr + 1][0])
            fast = max(fast, _ang(d0, d1))
            vel = eb.vsub(blades[fr + 1][1], blades[fr][1])
            if eb.dot(vel, blades[fr][2]) > 0:
                lead += 1
        rep.tip_deg = fast
        rep.edge_lead = f"{lead}/{en - st}"
        if fast > TIP_DEG:
            rep.problems.append(f"hit frames {st}-{en}: the blade turns {fast:.0f} degrees in one frame (at most "
                                f"{TIP_DEG:.0f}: a hit window that fast can miss)")
    rep.problems = sorted(set(rep.problems), key=rep.problems.index)[:10]
    return rep


def combo_speed(clips: list[dict]) -> dict:
    """Frames and hits of our 4-slot combo (CutS1..CutSF, a set with parts plays them in a row), against the game's."""
    frames, hits = 0, 0
    for slot in COMBO:
        ps = sorted((c for c in clips if c.get("slot") == slot), key=lambda c: c.get("part", 1))
        for i, c in enumerate(ps):
            frames += c["next"] if i == len(ps) - 1 else c["frames"]
            hits += len(c["hits"])
    sf, sh = STOCK_COMBO
    ratio = (hits / frames) / (sh / sf) if frames else 0.0
    return {"frames": frames, "hits": hits, "stock_frames": sf, "stock_hits": sh, "hits_per_second_x": ratio}


def check_all(rig: Rig, clips: list[dict], log=None) -> tuple[dict, list[Report]]:
    solved, reps = {}, []
    for c in clips:
        clip = solve(rig, c)
        solved[c["id"]] = clip
        reps.append(check_clip(rig, c, clip))
    _log(log, f"Solved and checked {len(solved)} clips on the {rig.source} skeleton.")
    return solved, reps


# ==================================================================================================== pictures

CHAINS_DRAWN = (
    ("c", ["Skl_Root", "Spine_2", "Neck", "Head"]),
    ("L", ["Spine_2", "Clavicle_L", "Arm_1_L", "Arm_2_L", "Wrist_L", "Finger_C_1_L"]),
    ("R", ["Spine_2", "Clavicle_R", "Arm_1_R", "Arm_2_R", "Wrist_R", "Finger_C_1_R"]),
    ("L", ["Skl_Root", "Waist", "Leg_1_L", "Leg_2_L", "Ankle_L", "Toe_L"]),
    ("R", ["Skl_Root", "Waist", "Leg_1_R", "Leg_2_R", "Ankle_R", "Toe_R"]),
)
INK = {"c": (40, 40, 40), "L": (40, 90, 220), "R": (215, 50, 40)}


def render_clip(rig: Rig, c: dict, clip, path: str) -> None:
    """Every frame of a clip from the front (top row) and from Link's right (bottom row): the body as lines, the
    blade in orange (its edge a short green tick), the hit window's frames on yellow, the hit point as a cross, the
    floor. PNG through Pillow; never opens a window."""
    from PIL import Image, ImageDraw
    skel = rig.skel
    n = len(clip.frames)
    sc = 70.0
    cw, ch = 96, 190
    img = Image.new("RGB", (max(1, n) * cw, 2 * ch + 40), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    hits = c.get("hits", [])
    floor = 0.0
    if skel.has("Toe_R"):
        floor = rig.solver.idle_P[skel.idx["Toe_R"]][1] - 0.04
    dr.text((4, 2), f"{c['id']} ({c.get('slot')} part {c.get('part', 1)}): {c.get('name', '')}  "
                    f"{n - 1} frames, next {c.get('next')}", fill=(0, 0, 0))
    for fr, (rots, trans) in enumerate(clip.frames):
        Wr, Wp = eb.fk(skel, rots, trans)
        bl = eb.weapon_line(skel, Wr, Wp, "Wrist_R", BLADE_LEN)
        for row, view in enumerate(("front", "side")):
            ox, oy = fr * cw + cw / 2, 20 + row * ch + ch - 14

            def pr(p):
                h = p[0] if view == "front" else p[2]
                return (ox + h * sc, oy - (p[1] - floor) * sc)      # front: facing Link, his left on the right
            x0 = fr * cw
            inwin = any(h["start"] <= fr <= h["end"] for h in hits)
            dr.rectangle([x0 + 1, 20 + row * ch, x0 + cw - 2, 20 + row * ch + ch - 2],
                         fill=(255, 246, 190) if inwin else (246, 246, 246))
            dr.line([x0 + 4, oy, x0 + cw - 6, oy], fill=(150, 150, 150))
            for side, names in CHAINS_DRAWN:
                pts = [pr(Wp[skel.idx[nm]]) for nm in names if skel.has(nm)]
                if len(pts) > 1:
                    dr.line(pts, fill=INK[side], width=2)
            if skel.has("Head"):
                hx, hy = pr(Wp[skel.idx["Head"]])
                dr.ellipse([hx - 6, hy - 9, hx + 6, hy + 3], outline=INK["c"], width=2)
            if bl is not None:
                g, t, e = bl
                dr.line([pr(g), pr(t)], fill=(240, 140, 0), width=3)
                mid = _lerp(g, t, 0.6)
                dr.line([pr(mid), pr(eb.vadd(mid, eb.vscale(e, 0.08)))], fill=(0, 150, 60), width=2)
            for h in hits:
                if h["start"] <= fr <= h["end"]:
                    px, py = pr(tuple(h["at"]))
                    dr.line([px - 5, py - 5, px + 5, py + 5], fill=(0, 140, 0), width=2)
                    dr.line([px - 5, py + 5, px + 5, py - 5], fill=(0, 140, 0), width=2)
            dr.text((x0 + 4, 22 + row * ch), f"{fr}" + (" front" if row == 0 and fr == 0 else "")
                    + (" side" if row == 1 and fr == 0 else ""), fill=(0, 0, 0))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img.save(path)


# ==================================================================================================== the sets

H = aamp.name_hash


def _events(node: aamp.PList, kind: str) -> list:
    ex = node.list("Extend")
    lst = ex.list(kind) if ex is not None else None
    return list(lst.objects.values()) if lst is not None else []


def _drop_events(node: aamp.PList, kind: str, type_index: int) -> None:
    """Take the events of one type out of a node's event list (the others keep their order and the first names)."""
    ex = node.list("Extend")
    lst = ex.list(kind) if ex is not None else None
    if lst is None:
        return
    keys = list(lst.objects)
    keep = [o for o in lst.objects.values() if o.get_value("TypeIndex") != type_index]
    lst.objects.clear()
    for k, o in zip(keys, keep):
        lst.objects[k] = o


def _parent_sequence(els: list, name: str):
    """The sequence node (TypeIndex 11) the clip `name` plays under, else None: found by that child, not by its place
    in the set (Player_CutJustRush.bas in 0.4.1 has three: the spear's, the claymore's and, third in the tree, the
    one-handed one, where Sword_Attack_Just_Rush1 sits in a speed selector (T16) right under the sequence)."""
    def kids(i):
        return [c.value for c in (els[i].object("Children") or {}).values() if isinstance(c.value, int)]

    def holds(i, seen) -> bool:
        if i in seen or not 0 <= i < len(els):
            return False
        seen.add(i)
        p = els[i].object("Parameters")
        if p is None:
            return False
        if p.get_value("FileName") == name:
            return True
        if p.get_value("TypeIndex") == 11:          # (another sequence: what it holds is its own)
            return False
        return any(holds(k, seen) for k in kids(i))
    for i, e in enumerate(els):
        p = e.object("Parameters")
        if p is not None and p.get_value("TypeIndex") == 11 and any(holds(k, {i}) for k in kids(i)):
            return e
    return None


RUSH_FIRST = SLOTS["CutJustRush"].format(part=1)     # the clip whose parent is the game's Flurry Rush sequence


def _find_node(els: list, name: str):
    for e in els:
        p = e.object("Parameters")
        if p is not None and p.get_value("TypeIndex") == 67 and p.get_value("FileName") == name:
            return e
    return None


def frame_map(stock: list[tuple[float, float]]):
    """A piecewise-linear map from the game clip's frames to ours through anchor pairs (stock, ours)."""
    pts = sorted(set(stock))
    clean = []
    for s, o in pts:
        if clean and (s <= clean[-1][0] or o < clean[-1][1]):
            continue
        clean.append((s, o))

    def f(x: float) -> float:
        if x <= clean[0][0]:
            return clean[0][1] + (x - clean[0][0])
        for (s0, o0), (s1, o1) in zip(clean, clean[1:]):
            if x <= s1:
                return o0 + (o1 - o0) * (x - s0) / (s1 - s0)
        return clean[-1][1] + (x - clean[-1][0]) * 0.0
    return f


def clip_node(template: aamp.PList, c: dict, stock_len: float, last: bool = True) -> aamp.PList:
    """A clip node shaped like the game's one-handed one (its events kept, their frames moved onto ours: the hit
    window exactly, the next-input frame exactly, the others in step), playing our clip at rate 1. last=False (a
    part of a set but its last): no next-attack trigger (TypeIndex 4), as the game's own sequence children, so a
    buffered press can't start the next attack before the set's later parts have played."""
    e = copy.deepcopy(template)
    if not last:
        _drop_events(e, "TriggerEvents", 4)
    p = e.object("Parameters")
    p[H("FileName")] = aamp.Param(p[H("FileName")].type, clip_name(c))
    p[H("Morph")] = aamp.Param(aamp.F32, float(c.get("morph", MORPH)))
    ex = e.list("Extend")
    fc = ex.list("FrameCtrl") if ex is not None else None
    if fc is not None:
        for o in fc.objects.values():
            if H("Rate") in o:
                o[H("Rate")] = aamp.Param(aamp.F32, 1.0)
    hit = c["hits"][0]
    win = next((o for o in _events(e, "HoldEvents") if o.get_value("TypeIndex") == 3), None)
    nxt = next((o for o in _events(e, "TriggerEvents") if o.get_value("TypeIndex") == 4), None)
    anchors = [(0.0, 0.0), (float(stock_len), float(c["frames"]))]
    if win is not None:
        anchors += [(win.get_value("StartFrame"), float(hit["start"])), (win.get_value("EndFrame"), float(hit["end"]))]
    if nxt is not None and nxt.get_value("Frame") >= 0:
        anchors.append((nxt.get_value("Frame"), float(c["next"])))
    fmap = frame_map(anchors)

    def mv(v: float) -> float:
        return v if v < 0 else round(max(0.0, min(float(c["frames"]), fmap(v))), 1)
    for o in _events(e, "TriggerEvents"):
        t = o.get_value("TypeIndex")
        if t == 4:
            o[H("Frame")] = aamp.Param(aamp.F32, float(c["next"]))
        elif t == 0 and o.get_value("Frame") >= 0:
            o[H("Frame")] = aamp.Param(aamp.F32, float(hit["start"]))
        else:
            o[H("Frame")] = aamp.Param(aamp.F32, mv(o.get_value("Frame")))
    for o in _events(e, "HoldEvents"):
        if o is win:
            o[H("StartFrame")] = aamp.Param(aamp.F32, float(hit["start"]))
            o[H("EndFrame")] = aamp.Param(aamp.F32, float(hit["end"]))
            if hit.get("side"):
                o[H("Value")] = aamp.Param(o[H("Value")].type, hit["side"])
        else:
            s, en = o.get_value("StartFrame"), o.get_value("EndFrame")
            o[H("StartFrame")] = aamp.Param(aamp.F32, mv(s))
            if en >= 0:
                o[H("EndFrame")] = aamp.Param(aamp.F32, max(mv(s), mv(en)))
    if win is None:
        raise ClipError(f"{c['id']}: the game's node {template.object('Parameters').get_value('FileName')} has no "
                        "hit window to move")
    return e


def _selector(n_old_root: int, ours: int) -> aamp.PList:
    sel = aamp.PList()
    sel.objects[H("Parameters")] = aamp.PObject({H("TypeIndex"): aamp.Param(aamp.INT, 97),
                                                 H("JudgeOnce"): aamp.Param(aamp.BOOL, True)})
    sel.objects[H("Children")] = aamp.PObject({H("Child0"): aamp.Param(aamp.INT, ours),
                                               H("Child1"): aamp.Param(aamp.INT, n_old_root)})
    sa = aamp.PList()
    sa.objects[H("StringArray0")] = aamp.PObject({H("Value0"): aamp.Param(aamp.STRING64, SUBTYPE),
                                                  H("Value1"): aamp.Param(aamp.STRING64, "default")})
    ext = aamp.PList()
    ext.lists[H("StringArray")] = sa
    sel.lists[H("Extend")] = ext
    return sel


def _sequence(template: aamp.PList, children: list[int]) -> aamp.PList:
    """A sequence node (TypeIndex 11) like the game's Flurry Rush one, with our children; its per-child number list
    (1, 2, ... in the game's) follows the child count."""
    e = copy.deepcopy(template)
    e.objects[H("Children")] = aamp.PObject({H(f"Child{i}"): aamp.Param(aamp.INT, k) for i, k in enumerate(children)})
    ex = e.list("Extend")
    if ex is not None:
        for sub in ex.lists.values():
            for o in sub.objects.values():
                if all(isinstance(v.value, int) and not isinstance(v.value, bool) for v in o.values()):
                    o.clear()
                    for i in range(len(children)):
                        o[H(f"Value{i}")] = aamp.Param(aamp.INT, i + 1)
    return e


def wrap_bas(data: bytes, slot: str, parts: list[dict], stock_lens: dict[str, float],
             seq_template: aamp.PList | None = None) -> bytes:
    """The game's set with a sub-type selector on top: `Dragonbreath` plays our clip (or our parts in a row), every
    other weapon the game's own tree (its nodes kept as they are, only moved one place down)."""
    root = aamp.load(data)
    els_pl = root.list("Elements")
    old = list(els_pl.lists.values())
    n = len(old)
    ours: list[aamp.PList] = []
    parts = sorted(parts, key=lambda c: c.get("part", 1))
    for i, c in enumerate(parts):
        stock = SLOTS[slot].format(part=c.get("part", 1))
        tmpl = _find_node(old, stock)
        if tmpl is None:
            raise ClipError(f"your game's Player_{slot}.bas has no {stock} node")
        ours.append(clip_node(tmpl, c, stock_lens.get(stock, float(c["frames"])), last=i == len(parts) - 1))
    first = n + 1
    if len(ours) == 1:
        new = [_selector(n, first)] + old[1:] + [old[0]] + ours
    else:
        own = _parent_sequence(old, SLOTS[slot].format(part=1))
        seq = own or seq_template
        if seq is None:
            raise ClipError(f"{slot}: a set with parts needs a sequence node to copy (the game's Flurry Rush has one)")
        kids = list(range(first + 1, first + 1 + len(ours)))
        if own is not None:
            # the set's own sequence: as many children as the game's (its Flurry Rush repeats the last cut once)
            want = len(own.object("Children") or {})
            kids += [kids[-1]] * max(0, want - len(kids))
        new = [_selector(n, first)] + old[1:] + [old[0], _sequence(seq, kids)] + ours
    els_pl.lists.clear()
    for i, e in enumerate(new):
        els_pl.lists[H(f"Element{i}")] = e
    return aamp.dump(root, aamp.type_name(data) or "xml")


def tree(data: bytes) -> list[str]:
    """A set's node tree, one line a node (type, clip, selector values, hit windows)."""
    r = aamp.load(data)
    els = list(r.list("Elements").lists.values())
    out: list[str] = []

    def walk(i, d, seen):
        if i in seen or i >= len(els):
            return
        seen.add(i)
        p = els[i].object("Parameters")
        ex = els[i].list("Extend")
        s = f"T{p.get_value('TypeIndex')}"
        if p.get_value("FileName"):
            s += " " + p.get_value("FileName")
        if ex is not None and ex.list("StringArray") is not None:
            s += " " + ",".join(str(v.value) for o in ex.list("StringArray").objects.values() for v in o.values())
        w = [o for o in _events(els[i], "HoldEvents") if o.get_value("TypeIndex") == 3]
        if w:
            s += " hit=" + ",".join(f"[{o.get_value('StartFrame'):g},{o.get_value('EndFrame'):g}]" for o in w)
        out.append("  " * d + f"#{i} {s}")
        for ch in (els[i].object("Children") or {}).values():
            walk(ch.value, d + 1, seen)
    walk(0, 0, set())
    return out


def untouched(src: bytes, wrapped: bytes) -> bool:
    """Every node of the game's set is in the wrapped one unchanged (the old root moved to the end)."""
    a = list(aamp.load(src).list("Elements").lists.values())
    b = list(aamp.load(wrapped).list("Elements").lists.values())
    n = len(a)

    def same(x, y):
        return aamp.dump(aamp.PList(lists={1: x})) == aamp.dump(aamp.PList(lists={1: y}))
    return len(b) > n and same(a[0], b[n]) and all(same(a[i], b[i]) for i in range(1, n))


# ==================================================================================================== the package

@dataclass
class Moves:
    folder: str
    clips: int = 0
    sets: list = field(default_factory=list)
    archive_bytes: int = 0
    readback_deg: float = 0.0
    speed: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


def mod_toml() -> str:
    return "\n".join([
        "# Built on this PC by tools/dragonbreath_clips.py from the player's own game files and our keyframes",
        "# (assets/dragonbreath/clips). Part of the Dragonbreath switch: used only while it is on.",
        "",
        "[mod]",
        f'id = "{PKG_ID}"',
        'name = "Dragonbreath moves"',
        f'version = "{VERSION}"',
        'authors = ["giruka"]',
        'description = "Dragonbreath\'s own fast sword moves (built on your PC from your own game files)."',
        'game_versions = ["1.9.0"]',
        f'part_of = "{PART_OF}"',
        "secret = true",
        ""])


def stock_lengths(res) -> dict[str, float]:
    import struct
    return {a.name: float(struct.unpack_from("<i", a.head, 4)[0]) for a in res.skel_anims}


def build_package(game, clips: list[dict], out: str, *, log=None) -> Moves:
    """Write giruka.dragonbreath-moves (a package folder) into out/<PKG_ID>. Reads only the player's game files."""
    import ukmm
    folder = os.path.join(out, PKG_ID)
    if os.path.isdir(folder) and os.listdir(folder):
        raise ClipError(f"{folder} isn't empty")
    probs = lint(clips)
    if probs:
        raise ClipError("the clip files have problems: " + "; ".join(probs[:5]))
    rig = game_rig(game, log)
    solved, reps = check_all(rig, clips, log)
    bad = [f"{r.cid}: {p}" for r in reps for p in r.problems]
    if bad:
        raise ClipError("pose checks failed: " + "; ".join(bad[:6]))
    mv = Moves(folder)
    game_clips = {a.name for a in rig.res.skel_anims}
    for c in clips:
        if clip_name(c) in game_clips:
            raise ClipError(f"your game already has a clip called {clip_name(c)}")
    datas = [eb.build_clip(rig.skel, rig.template, solved[c["id"]], clip_name(c)) for c in clips]
    pa = game.anim_blob()
    _log(log, "Writing the animation archive...")
    data, yz, worst = eb.write_anim_archive(rig.skel, rig.res, datas, ARCHIVE, pa.align)
    mv.clips, mv.archive_bytes, mv.readback_deg = len(datas), len(data), worst
    lens = stock_lengths(rig.res)
    seq_src = game.bas("Player_CutJustRush")
    seq_t = None
    if seq_src is not None:
        seq_t = _parent_sequence(list(aamp.load(seq_src).list("Elements").lists.values()), RUSH_FIRST)
    bas_files: dict[str, bytes] = {}
    for slot in SLOTS:
        parts = [c for c in clips if c["slot"] == slot]
        if not parts:
            continue
        src = game.bas(f"Player_{slot}")
        if src is None:
            raise ClipError(f"your game has no Player_{slot}.bas")
        w = wrap_bas(src, slot, parts, lens, seq_t)
        if not untouched(src, w):
            raise ClipError(f"Player_{slot}.bas: the game's own nodes changed in the wrap")
        bas_files[f"Actor/AS/Player_{slot}.bas"] = w
        mv.sets.append(slot)
    tb = game.titlebg()
    tb_files = tb.open()
    pp = game.player_pack()
    pp_raw = bnp.sarc_build([(nm, b, 8) for nm, b in sorted(bas_files.items())], pp.key)
    pp_data = ukmm.yaz0(pp_raw, pp.align) if pp.yaz0 else pp_raw
    tb_raw = bnp.sarc_build([(f"Model/{ARCHIVE}.sbfres", yz, tb_files[eb.ANIM_FILE][1]),
                             (eb.PLAYER_PACK, pp_data, tb_files[eb.PLAYER_PACK][1])], tb.key)
    romfs = os.path.join(folder, eb.BASE_TID, "romfs")
    eb._write(os.path.join(romfs, *eb.TITLEBG.split("/")), tb_raw)
    sizes: dict[str, int] = {}
    rstb = bnp.load_game_rstb(os.path.join(game.install, "extracted"))
    if rstb is None:
        mv.notes.append("the game's size table isn't there: no size entries")
    else:
        g = rstb.get("Model/Player_Animation.bfres")
        if g:
            sizes[f"Model/{ARCHIVE}.bfres"] = ukmm.estimate_entry(len(pa.raw), g, len(data), False)
        for nm, b in bas_files.items():
            ref = pp.child(nm)
            g2 = rstb.get(nm)
            if g2 and ref is not None:
                sizes[nm] = ukmm.estimate_entry(len(ref.raw), g2, len(b), False)
    mv.speed = combo_speed(clips)
    eb._write(os.path.join(folder, "logs", "aslist.aamp"), bnp.aamp_write(eb.anim_list_log(ARCHIVE)))
    eb._write(os.path.join(folder, "logs", "rstb.json"), json.dumps(dict(sorted(sizes.items())), indent=1) + "\n")
    eb._write(os.path.join(folder, "mod.toml"), mod_toml())
    eb._write(os.path.join(folder, "info.json"), json.dumps({
        "name": "Dragonbreath moves", "version": VERSION, "desc": "Dragonbreath's own fast sword moves.",
        "platform": "switch", "options": {}}, indent=1) + "\n")
    record = {"tool": "tools/dragonbreath_clips.py", "version": VERSION, "archive": ARCHIVE,
              "archive_bytes": len(data), "readback_degrees": round(worst, 4), "subtype": SUBTYPE,
              "sets": mv.sets, "speed": mv.speed, "inputs": dict(sorted(game.hashes.items())),
              "clips": [{"id": c["id"], "clip": clip_name(c), "slot": c["slot"], "part": c.get("part", 1),
                         "frames": c["frames"], "next": c.get("next"), "hit": [c["hits"][0]["start"],
                                                                           c["hits"][0]["end"]]} for c in clips]}
    eb._write(os.path.join(folder, "moves.json"), json.dumps(record, indent=1) + "\n")
    eb._write(os.path.join(folder, "BUILT.md"), "\n".join([
        "# Dragonbreath moves: built on this PC by tools/dragonbreath_clips.py from the player's own game files.", "",
        f"- archive {ARCHIVE}: {mv.clips} clips, {len(data)} bytes, read back within {worst:.3f} degrees",
        f"- sets wrapped for the sub-type {SUBTYPE}: {', '.join(mv.sets)}",
        *[f"- note: {x}" for x in mv.notes], ""]))
    return mv


# ==================================================================================================== CLI

def _rig(args, log) -> Rig:
    if args.demo:
        return demo_rig()
    return game_rig(eb.find_game(args.game), log)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="dragonbreath_clips.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lint")
    for name in ("check", "preview"):
        s = sub.add_parser(name)
        s.add_argument("--demo", action="store_true", help="the made-up skeleton (no game files)")
        s.add_argument("--game", help="the Savage Wind folder")
        s.add_argument("--only", help="clip ids, comma separated")
        if name == "preview":
            s.add_argument("--out", default=OUT_PICS)
    b = sub.add_parser("build")
    b.add_argument("--game")
    b.add_argument("--out", help="write the package folder here (no library add)")
    b.add_argument("--library")
    a = ap.parse_args(argv)
    eb._low_priority()
    log = print
    try:
        clips = load_clips()
        if getattr(a, "only", None):
            want = {x.strip() for x in a.only.split(",")}
            clips = [c for c in clips if c.get("id") in want]
        if a.cmd == "lint":
            probs = lint(clips)
            for x in probs:
                print("problem:", x)
            print(f"{len(clips)} clips, {len(probs)} problems")
            return 1 if probs else 0
        if a.cmd in ("check", "preview"):
            probs = lint(clips)
            if probs:
                for x in probs:
                    print("problem:", x)
                return 1
            rig = _rig(a, log)
            solved, reps = check_all(rig, clips, log)
            n = 0
            for c, r in zip(clips, reps):
                print(f"{c['id']:6} {c['slot']:12} {c['frames']:3} f, hit {c['hits'][0]['start']}-"
                      f"{c['hits'][0]['end']}, next {str(c.get('next', '-')):>2}: blade {r.hit_cm:4.1f} cm from the "
                      "hit point, "
                      f"{r.tip_deg:4.1f} deg/frame, edge leads {r.edge_lead}, feet {r.lowest_cm:+.1f} cm, hips up "
                      f"{r.highest_hips_cm:.0f} cm" + ("" if not r.problems else f"  PROBLEMS: {len(r.problems)}"))
                for x in r.problems:
                    print("   ", x)
                n += len(r.problems)
                if a.cmd == "preview":
                    render_clip(rig, c, solved[c["id"]], os.path.join(a.out, f"{c['id']}.png"))
            sp = combo_speed(load_clips())
            print(f"combo: {sp['hits']} hits in {sp['frames']} frames (the game's: {sp['stock_hits']} in "
                  f"{sp['stock_frames']:g}): {sp['hits_per_second_x']:.2f}x the hits per second")
            if a.cmd == "preview":
                print(f"pictures: {a.out}")
            return 1 if n else 0
        if a.cmd == "build":
            import modman as mm
            game = eb.find_game(a.game)
            if a.out:
                mv = build_package(game, clips, a.out, log=log)
                print(f"built {mv.folder}: {mv.clips} clips, sets {', '.join(mv.sets)}, "
                      f"{mv.speed['hits_per_second_x']:.2f}x hits per second")
                return 0
            import tempfile
            lib = mm.Library.load(mm.resolve_library(a.library))
            if lib is None:
                raise ClipError("No mod library yet. Run: python tools/modman.py init")
            with tempfile.TemporaryDirectory() as tmp:
                mv = build_package(game, clips, tmp, log=log)
                eb.add_to_library(lib, mv.folder, PKG_ID, log)
            print(f"added {PKG_ID}: {mv.clips} clips, sets {', '.join(mv.sets)}")
            return 0
    except (ClipError, eb.EmoteError) as e:
        print("error:", e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
