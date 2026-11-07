#!/usr/bin/env python3
"""In-game checks for the ghost's held items (ghost mod 0.6.0, docs/ghost.md "Held items"). Run through
ghost_ingame_check.py --items (it starts the game once, with the Link body, and closes it at the end):

    python tools/mp/ghost_ingame_check.py --items --spot -1033.6 252.3 1809.3 132 --give-weapon

The mod drives the items directly with `ghost items` (it plays the remote player); H10 goes through a relay, a bot and
the client. The ghost stands about 2.6 m in front of Link, a little to the left, facing him. Screenshots go to
out\\shots\\ghostitems_*.png and the results to out\\research\\ghost-items-results.txt (never committed).

  H1 in hand: sword, shield, bow held (state 255, holder the ghost) within 3 s, each within HAND_DIST of its hand
     bone while the ghost stands, walks a circle and swings (CutS1)
  H2 switch: sword -> two-hander -> spear -> sword, each held within 2 s, then 10 switches 0.3 s apart; no stray
  H3 clear: the shield to none (the others stay the same actors), then none at all; gone within 1 s, no stray
  H4 back: mask 7 (camera behind the ghost), then 0, then 10 toggles: on Pod_A at the item's own offset (its
     distance to Pod_A within BACK_TOL of the offset's length), in the hand again on 0 (SKIP: no back placement)
  H5 can't take: Link walks up to the ghost and presses A five times (unarmed, and again armed after H12): his own
     items don't change, the ghost's are the same actors, still held
  H6 despawn, H7 far rule, H8 the game removes the ghost, H9 reload: no item left behind (stray 0), and the ghost
     gets its items again (H7: within 5 s, H9: within 15 s of the world being back)
  H10 network: relay + bot.py --items + the client with --ghost-items: names and places follow each step
  H11 cost: frames a second with the ghost alone and with its 3 items (drop at most FPS_DROP), items.us under US_MAX
  H12 give: `ghost give` a sword, a shield and a bow near Link; he takes each (A); `ghost self` names all three
  H13 the sender's bytes: weapon, shield and bow out (Y, ZL, ZR) read "not on the back", after a sprint "on the back"
  H14 log: no exception, assert, panic or fatal line in savagewind.log (judged by ghost_ingame_check.py after exit)
"""
from __future__ import annotations

import asyncio
import math
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

_TOOLS = str(Path(__file__).resolve().parent.parent)
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
from control import Control, ControlError  # noqa: E402
from mp import client  # noqa: E402
from mp.bot import ItemScript  # noqa: E402

ITEMS = ("Weapon_Sword_001", "Weapon_Shield_001", "Weapon_Bow_001")
SWITCHES = ("Weapon_Lsword_001", "Weapon_Spear_001", "Weapon_Sword_001")
NONE3 = ("", "", "")
HAND_DIST = 0.05      # m: H1 an item at most this far from its hand bone is in the hand
BACK_TOL = 0.05       # m: H4 |distance to Pod_A - the length of its own offset|
FPS_DROP = 1.0        # H11 frames a second
US_MAX = 50.0         # H11 microseconds a frame (average)
HELD_IN = 3.0         # s: H1
SWITCH_IN = 2.0       # s: H2
CLEAR_IN = 1.0        # s: H3
FAR_BACK_IN = 5.0     # s: H7
RELOAD_BACK_IN = 15.0  # s: H9
NET_IN = 1.5          # s: H10 from the new names in the mod's reply to the items held in place
BOT_ITEMS = "Weapon_Sword_001/Weapon_Shield_001/Weapon_Bow_001/0:5,Weapon_Lsword_001/-/Weapon_Bow_001/1:5,-/-/-/0:3"
LOG_BAD = re.compile(r"exception|assert|panic|fatal|access violation", re.I)
SLOT_KEYS = ("name", "state", "actor", "place", "dist_m", "hold_m", "tries", "item_state", "holder_ok", "why")


# ------------------------------------------------------------------ judging (pure: tests/test_mp_items.py)

def slots(st: dict) -> list[dict]:
    """The three item slots of a `ghost` reply (weapon, shield, bow), each {name, state, actor, place, dist_m, ...}."""
    st = st if isinstance(st, dict) else {}
    return [{k: st.get(f"items.{i}.{k}") for k in SLOT_KEYS} for i in range(3)]


def held_as(st: dict, names, mask: int = 0, max_dist: float | None = None) -> str | None:
    """None when the reply shows exactly `names` held (state held, item state 255, holder the ghost; an empty name: an
    empty slot), each in its hand or, for a bit of mask, on the back; max_dist: also at most that far from its hand
    bone. Else why not."""
    for i, (sl, name) in enumerate(zip(slots(st), names)):
        if not name:
            if sl["state"] not in ("none", None) or sl["actor"] not in ("0x0", None):
                return f"slot {i} not empty ({sl['state']}, {sl['actor']})"
            continue
        if sl["name"] != name or sl["state"] != "held":
            return f"slot {i}: {sl['name']} {sl['state']} ({sl['why'] or '-'})"
        if sl["item_state"] != 255 or sl["holder_ok"] is not True:
            return f"slot {i}: item state {sl['item_state']}, holder ok {sl['holder_ok']}"
        place = "back" if mask >> i & 1 else "hand"
        if sl["place"] != place:
            return f"slot {i}: {sl['place']}, not {place}"
        d = sl["dist_m"]
        if max_dist is not None and not (isinstance(d, (int, float)) and 0 <= d <= max_dist):
            return f"slot {i}: {d} m from its hand bone"
    return None


def judge_held(phases: dict[str, list[dict]], names=ITEMS, max_dist: float = HAND_DIST) -> tuple[str, str]:
    """H1: {"standing": [`ghost` replies], "walking": [...], "attacking": [...]}: every sample shows the three held in
    their hands, at most max_dist from the hand bones."""
    parts, ok = [], True
    for phase, samples in phases.items():
        if not samples:
            ok = False
            parts.append(f"{phase}: no samples")
            continue
        bad = [w for w in (held_as(s, names, 0, max_dist) for s in samples) if w]
        worst = max((sl["dist_m"] for s in samples for sl in slots(s)[:3]
                     if isinstance(sl["dist_m"], (int, float)) and sl["dist_m"] >= 0), default=-1.0)
        ok &= not bad
        parts.append(f"{phase}: {len(samples) - len(bad)}/{len(samples)} ok, farthest {worst:.3f} m"
                     + (f" ({bad[0]})" if bad else ""))
    return ("PASS" if ok else "FAIL"), "; ".join(parts)


def judge_times(label: str, times: list[float | None], limit: float, final_why: str | None,
                stray) -> tuple[str, str]:
    """H2, H3, H7, H9: how long each change took (None: never), what the end state lacks (None: right), and `stray`
    (deleted items still there)."""
    shown = ", ".join("never" if t is None else f"{t:.2f}" for t in times)
    ok = bool(times) and all(t is not None and t <= limit for t in times) and final_why is None and stray == 0
    text = f"{label}: {shown} s (limit {limit:g}); end: {final_why or 'right'}; stray {stray}"
    return ("PASS" if ok else "FAIL"), text


def judge_back(back: list[dict], hand: list[dict], bones: str, tol: float = BACK_TOL) -> tuple[str, str]:
    """H4: `ghost` replies with mask 7 (back), then with mask 0 (hand). On the back: each item on Pod_A at the length
    of its own offset (|dist_m - hold_m| <= tol). SKIP when the ghost has no Pod_A or no item ever got its offsets
    (the fallback: they stay in the hand)."""
    if not back or not hand:
        return "FAIL", "no samples"
    last = slots(back[-1])
    if "no Pod_A" in str(bones) or all(not isinstance(sl["hold_m"], (int, float)) or sl["hold_m"] < 0 for sl in last):
        return "SKIP", f"no back placement (bones {bones!r}; offsets {[sl['hold_m'] for sl in last]}): the fallback"
    why = held_as(back[-1], ITEMS, 7)
    errs = [abs(sl["dist_m"] - sl["hold_m"]) for sl in last
            if isinstance(sl["dist_m"], (int, float)) and isinstance(sl["hold_m"], (int, float)) and sl["dist_m"] >= 0]
    if why is None and (len(errs) < 3 or max(errs) > tol):
        why = f"off by {[round(e, 3) for e in errs]} m from the offsets {[sl['hold_m'] for sl in last]}"
    hand_why = held_as(hand[-1], ITEMS, 0)
    ok = why is None and hand_why is None
    text = (f"back: {why or 'all three on Pod_A'}, off by at most {max(errs, default=-1):.3f} m; "
            f"then hand: {hand_why or 'all three in the hand'}")
    return ("PASS" if ok else "FAIL"), text


def judge_no_take(me0: dict, me1: dict, actors0: list, st1: dict) -> tuple[str, str]:
    """H5: Link's own items before and after pressing A next to the ghost, and the ghost's item actors before and its
    reply after: nothing moved over."""
    own0 = tuple(me0.get(k) for k in ("weapon", "shield", "bow"))
    own1 = tuple(me1.get(k) for k in ("weapon", "shield", "bow"))
    actors1 = [sl["actor"] for sl in slots(st1)]
    why = held_as(st1, ITEMS, 0)
    ok = own0 == own1 and actors0 == actors1 and why is None
    return ("PASS" if ok else "FAIL"), (f"Link's items {own0} -> {own1}; the ghost's actors "
                                        f"{'the same' if actors0 == actors1 else f'{actors0} -> {actors1}'}, "
                                        f"{why or 'still held'}")


def judge_gone(st: dict, label: str, want_state=("idle", "far")) -> tuple[str, str]:
    """H6, H7 (far), H8: the ghost not live, no item held, stray 0."""
    held = [sl["name"] for sl in slots(st) if sl["state"] in ("pending", "grab", "held")]
    ok = st.get("state") in want_state and not held and st.get("items.stray") == 0
    return ("PASS" if ok else "FAIL"), (f"{label}: ghost {st.get('state')}, items left {held or 'none'}, stray "
                                        f"{st.get('items.stray')}, deleted {st.get('items.deleted')}")


def judge_cost(base: float | None, with_items: float | None, us: float | None, drop: float = FPS_DROP,
               us_max: float = US_MAX) -> tuple[str, str]:
    """H11: frames a second with the ghost alone and with its three items; the items' microseconds a frame."""
    if base is None or with_items is None:
        return "SKIP", "no frame rate"
    ok = base - with_items <= drop and isinstance(us, (int, float)) and us < us_max
    return ("PASS" if ok else "FAIL"), (f"{base:.1f} -> {with_items:.1f} frames a second (drop limit {drop:g}); items "
                                        f"{us if us is None else round(us, 1)} us a frame (limit {us_max:g})")


def judge_network(samples: list[tuple[float, dict]], script: ItemScript, limit: float = NET_IN) -> tuple[str, str]:
    """H10: (time, `ghost` reply) while the bot ran `script`: every step's names showed held in their places, each at
    most `limit` s after the names first showed in the reply."""
    seen, slow, missing = [], [], []
    for w, sh, b, mask, _d in script.steps:
        names = (w, sh, b)
        first = next((t for t, st in samples if tuple(sl["name"] or "" for sl in slots(st)) == names), None)
        if first is None:
            missing.append("/".join(n or "-" for n in names))
            continue
        done = next((t for t, st in samples if t >= first and held_as(st, names, mask) is None), None)
        if done is None or done - first > limit:
            slow.append(("/".join(n or "-" for n in names), None if done is None else round(done - first, 2)))
        else:
            seen.append(round(done - first, 2))
    ok = not missing and not slow
    text = f"steps held in place after {seen} s" + (f"; never named {missing}" if missing else "") + \
        (f"; late or never held {slow}" if slow else "")
    return ("PASS" if ok else "FAIL"), text


def judge_back_bytes(rows: dict[str, dict]) -> tuple[str, str]:
    """H13: `ghost self` replies: "<item>_out" (drawn) and "<item>_back" (after a sprint) for weapon, shield, bow. An
    item Link doesn't have is skipped; none at all: SKIP."""
    parts, ok, any_ = [], True, False
    for k in ("weapon", "shield", "bow"):
        out, back = rows.get(f"{k}_out"), rows.get(f"{k}_back")
        if not out or not back or not out.get(k):
            parts.append(f"{k}: skipped")
            continue
        any_ = True
        good = out.get(f"{k}_back") is False and back.get(f"{k}_back") is True
        ok &= good
        parts.append(f"{k}: out {out.get(f'{k}_back')}, after the sprint {back.get(f'{k}_back')}")
    if not any_:
        return "SKIP", "Link has none of them: " + "; ".join(parts)
    return ("PASS" if ok else "FAIL"), "; ".join(parts)


def judge_give(me: dict, names=ITEMS) -> tuple[str, str]:
    """H12: after taking what `ghost give` spawned, `ghost self` names all three."""
    got = tuple(me.get(k) for k in ("weapon", "shield", "bow"))
    return ("PASS" if got == tuple(names) else "FAIL"), f"Link has {got}"


def judge_log(text: str) -> tuple[str, str]:
    """H14: savagewind.log has no exception, assert, panic, fatal or access violation line."""
    bad = [ln.strip() for ln in text.splitlines() if LOG_BAD.search(ln)]
    return ("PASS" if not bad else "FAIL"), (f"{len(text.splitlines())} lines, none bad" if not bad
                                             else f"{len(bad)} bad lines, first: {bad[0][:160]}")


# ------------------------------------------------------------------ the run

def shot(c: Control, name: str) -> None:
    try:
        c.cmd("shot", f"ghostitems_{name}.png")
    except ControlError:
        pass


def gst(c: Control) -> dict:
    return c.cmd("ghost")


def set_items(c: Control, names, mask: int = 0) -> None:
    c.cmd("ghost", "items", *(n or "-" for n in names), str(mask))


def poll(c: Control, pred, secs: float, every: float = 0.1) -> tuple[float | None, dict]:
    """Ask `ghost` until pred(reply) or secs pass: (seconds it took or None, the last reply)."""
    t0, st = time.monotonic(), {}
    while True:
        st = gst(c)
        if pred(st):
            return time.monotonic() - t0, st
        if time.monotonic() - t0 > secs:
            return None, st
        time.sleep(every)


def sample(c: Control, secs: float, every: float = 0.2) -> list[dict]:
    out, end = [], time.monotonic() + secs
    while time.monotonic() < end:
        out.append(gst(c))
        time.sleep(every)
    return out


def link_pos(c: Control) -> tuple[float, float, float]:
    p = c.cmd("pos")
    return float(p["x"]), float(p["y"]), float(p["z"])


class Spot:
    """Where Link stands and the camera's forward direction, measured once: the ghost goes relative to these."""

    def __init__(self, c: Control, gic):
        self.p = link_pos(c)
        self.f = gic.cam_forward(c) or (0.0, 1.0)

    def look(self, c: Control) -> None:
        """Turn the camera back to the measured direction (after a teleport or a walk)."""
        try:
            c.cmd("look", f"{math.degrees(math.atan2(self.f[0], self.f[1])):.1f}", "8")
            time.sleep(0.5)
        except ControlError:
            pass

    def at(self, ahead: float, left: float) -> tuple[float, float, float]:
        fx, fz = self.f
        return self.p[0] + ahead * fx + left * fz, self.p[1], self.p[2] + ahead * fz - left * fx

    def place(self, c: Control, ahead: float = 0.3, left: float = 1.2, facing: str = "camera") -> tuple:
        """Put the ghost there (`ghost pos`; by default beside Link, close to the camera, so the screenshots show its
        hands), facing the camera, Link ("link") or away from the camera ("away": its back to us)."""
        x, y, z = self.at(ahead, left)
        yaw = math.degrees(math.atan2(-self.f[0], -self.f[1]))
        if facing == "link":
            yaw = math.degrees(math.atan2(self.p[0] - x, self.p[2] - z))
        elif facing == "away":
            yaw = math.degrees(math.atan2(self.f[0], self.f[1]))
        c.cmd("ghost", "pos", f"{x:.3f}", f"{y:.3f}", f"{z:.3f}", f"{yaw:.1f}")
        return x, y, z, yaw


def walk_circle(port: int, center: tuple[float, float, float], secs: float, radius: float = 1.2,
                speed: float = 2.0) -> None:
    """The ghost walks a circle at `speed` m/s (`ghost pos ... speed`, 20 a second) on a second connection."""
    with Control(port, timeout=5) as c2:
        t0 = time.monotonic()
        while time.monotonic() - t0 < secs:
            a = speed * (time.monotonic() - t0) / radius
            x, z = center[0] + radius * math.sin(a), center[2] + radius * math.cos(a)
            yaw = math.degrees(math.atan2(math.cos(a), -math.sin(a)))
            try:
                c2.cmd("ghost", "pos", f"{x:.3f}", f"{center[1]:.3f}", f"{z:.3f}", f"{yaw:.1f}", f"{speed:.2f}")
            except ControlError:
                return
            time.sleep(0.05)


def ensure_ghost(c: Control, spot: Spot, gic, names=ITEMS, mask: int = 0, secs: float = 15.0) -> dict:
    """A live Link-body ghost at its usual place holding `names`: spawned if it isn't live."""
    st = gst(c)
    if st.get("state") != "live":
        c.cmd("ghost", "spawn", "link")
        gic.wait_live(c, secs)
    spot.place(c)
    c.cmd("ghost", "anim", "auto")
    set_items(c, names, mask)
    _, st = poll(c, lambda s: held_as(s, names, mask) is None, secs)
    return st


def guard(rep, check: str, fn, *a) -> None:
    """Run one check; a game that stops answering is that check's FAIL (the next ones go on)."""
    try:
        fn(*a)
    except (ControlError, OSError, KeyError, ValueError) as e:
        rep.add(check, "FAIL", f"stopped: {type(e).__name__}: {e}")


def h1(c, port, spot, gic, rep) -> None:
    c.cmd("ghost", "far", "60", "50")
    c.cmd("ghost", "spawn", "link")
    st = gic.wait_live(c)
    spot.place(c)
    c.cmd("ghost", "anim", "Wait")
    time.sleep(0.6)
    st = gst(c)
    c.cmd("ghost", "anim", "CutS1")
    time.sleep(0.6)
    cut = gst(c)
    shot(c, "L2_link")
    rep.add("L2", *gic.judge_link_spawn(st, cut))
    c.cmd("ghost", "anim", "auto")
    set_items(c, ITEMS, 0)
    took, st = poll(c, lambda s: held_as(s, ITEMS, 0) is None, HELD_IN + 2)
    print(f"  H1: held after {took} s; bones {st.get('items.bones')!r}; {st.get('last')!r}", flush=True)
    time.sleep(0.5)
    shot(c, "H1_stand")
    phases = {"standing": sample(c, 2.0)}
    x, y, z = spot.at(2.2, 1.3)
    th = threading.Thread(target=walk_circle, args=(port, (x, y, z), 4.0, 1.0), daemon=True)
    th.start()
    time.sleep(1.0)
    shot(c, "H1_walk")
    phases["walking"] = sample(c, 2.5)
    th.join(6)
    spot.place(c)
    c.cmd("ghost", "anim", "CutS1")
    time.sleep(0.25)
    shot(c, "H1_attack")
    phases["attacking"] = sample(c, 1.0)
    c.cmd("ghost", "anim", "auto")
    c.cmd("ghost", "anim", "CutS1")
    phases["attacking"] += sample(c, 1.0)
    c.cmd("ghost", "anim", "auto")
    status, text = judge_held(phases)
    if took is None or took > HELD_IN:
        status, text = "FAIL", f"not all held within {HELD_IN:g} s ({held_as(st, ITEMS, 0)}); " + text
    rep.add("H1", status, f"held after {took if took is None else round(took, 2)} s; " + text)


def h2(c, spot, gic, rep) -> None:
    ensure_ghost(c, spot, gic)
    times = []
    for name in SWITCHES:
        old = slots(gst(c))[0]["actor"]
        set_items(c, (name, ITEMS[1], ITEMS[2]), 0)
        took, st = poll(c, lambda s, n=name, o=old: held_as(s, (n, ITEMS[1], ITEMS[2]), 0) is None
                        and slots(s)[0]["actor"] != o, SWITCH_IN + 2)
        times.append(took)
        shot(c, f"H2_{name}")
    for k in range(10):
        set_items(c, (SWITCHES[k % 2], ITEMS[1], ITEMS[2]), 0)
        time.sleep(0.3)
    set_items(c, ITEMS, 0)
    took, st = poll(c, lambda s: held_as(s, ITEMS, 0) is None, SWITCH_IN + 2)
    times.append(took)
    time.sleep(1.5)
    st = gst(c)
    shot(c, "H2_end")
    rep.add("H2", *judge_times("switches", times, SWITCH_IN, held_as(st, ITEMS, 0), st.get("items.stray")))


def h3(c, spot, gic, rep) -> None:
    st = ensure_ghost(c, spot, gic)
    keep = [sl["actor"] for sl in slots(st)]
    set_items(c, (ITEMS[0], "", ITEMS[2]), 0)
    t1, st = poll(c, lambda s: held_as(s, (ITEMS[0], "", ITEMS[2]), 0) is None, CLEAR_IN + 1)
    same = [sl["actor"] for sl in slots(st)][0::2] == keep[0::2]
    set_items(c, NONE3, 0)
    t2, st = poll(c, lambda s: held_as(s, NONE3, 0) is None, CLEAR_IN + 1)
    time.sleep(1.5)
    st = gst(c)
    shot(c, "H3_cleared")
    status, text = judge_times("cleared", [t1, t2], CLEAR_IN, held_as(st, NONE3, 0), st.get("items.stray"))
    if not same:
        status, text = "FAIL", text + "; the weapon or bow was replaced when only the shield changed"
    rep.add("H3", status, text)


def h4(c, spot, gic, rep) -> None:
    ensure_ghost(c, spot, gic)
    spot.place(c, facing="away")
    set_items(c, ITEMS, 7)
    poll(c, lambda s: held_as(s, ITEMS, 7) is None, 3.0)
    time.sleep(0.6)
    shot(c, "H4_back")
    back = sample(c, 1.0)
    set_items(c, ITEMS, 0)
    poll(c, lambda s: held_as(s, ITEMS, 0) is None, 3.0)
    time.sleep(0.4)
    shot(c, "H4_hand_again")
    for k in range(10):
        set_items(c, ITEMS, 7 if k % 2 == 0 else 0)
        time.sleep(0.3)
    set_items(c, ITEMS, 0)
    poll(c, lambda s: held_as(s, ITEMS, 0) is None, 3.0)
    hand = sample(c, 0.6)
    st = hand[-1] if hand else {}
    status, text = judge_back(back, hand, str(st.get("items.bones")))
    rep.add("H4", status, text + f"; after 10 toggles stray {st.get('items.stray')}")
    spot.place(c)


def h5(c, spot, gic, rep, label: str) -> None:
    c.cmd("ghost", "self")
    time.sleep(0.3)
    me0 = c.cmd("ghost", "self")
    ensure_ghost(c, spot, gic)
    x, y, z, _ = spot.place(c, ahead=2.4, left=0.0, facing="link")
    st = gst(c)
    actors0 = [sl["actor"] for sl in slots(st)]
    start = link_pos(c)
    end = time.monotonic() + 4.0
    try:
        while time.monotonic() < end:
            p = link_pos(c)
            if math.hypot(x - p[0], z - p[2]) < 0.9:
                break
            c.cmd("stick", "l", "0", "0.35", "10")
            time.sleep(0.1)
    finally:
        c.cmd("release", "all")
    time.sleep(0.4)
    p = link_pos(c)
    for k in range(5):
        c.cmd("press", "a")
        time.sleep(0.6)
        if k == 0:
            shot(c, f"H5_{label}_A")
    time.sleep(0.8)
    me1 = c.cmd("ghost", "self")
    st1 = gst(c)
    shot(c, f"H5_{label}_after")
    status, text = judge_no_take(me0, me1, actors0, st1)
    rep.add("H5", status, f"{label}, {math.hypot(x - p[0], z - p[2]):.2f} m from the ghost: {text}")
    gic.back_to(c, start)
    spot.look(c)
    spot.place(c)


def h6(c, spot, gic, rep) -> None:
    ensure_ghost(c, spot, gic)
    c.cmd("ghost", "despawn")
    time.sleep(1.5)
    st = gst(c)
    shot(c, "H6_despawned")
    rep.add("H6", *judge_gone(st, "despawn", ("idle",)))


def h7(c, spot, gic, rep) -> None:
    ensure_ghost(c, spot, gic)
    x, y, z = spot.at(70.0, 0.0)
    c.cmd("ghost", "pos", f"{x:.3f}", f"{y:.3f}", f"{z:.3f}", "0")
    _, far = poll(c, lambda s: s.get("state") == "far", 3.0)
    time.sleep(1.5)
    far = gst(c)
    status, text = judge_gone(far, "far", ("far",))
    spot.place(c, ahead=3.0)
    took, st = poll(c, lambda s: s.get("state") == "live" and held_as(s, ITEMS, 0) is None, FAR_BACK_IN + 3)
    shot(c, "H7_back")
    s2, t2 = judge_times("back", [took], FAR_BACK_IN, held_as(st, ITEMS, 0), st.get("items.stray"))
    rep.add("H7", "PASS" if status == s2 == "PASS" else "FAIL", f"{text}; {t2}")


def h8(c, spot, gic, rep) -> None:
    ensure_ghost(c, spot, gic)
    drops = gst(c).get("drops", 0)
    c.cmd("ghost", "far", "0")
    x, y, z = spot.at(90.0, 0.0)
    c.cmd("ghost", "pos", f"{x:.3f}", f"{y:.3f}", f"{z:.3f}", "0")
    took, st = poll(c, lambda s: s.get("drops", 0) > drops, 6.0, 0.05)
    time.sleep(1.5)
    st = gst(c)
    status, text = judge_gone(st, "the game removed it", ("idle", "pending", "live"))
    if took is None:
        status, text = "FAIL", "the game didn't remove the ghost at 90 m within 6 s; " + text
    c.cmd("ghost", "far", "60", "50")
    spot.place(c)
    took2, st2 = poll(c, lambda s: s.get("state") == "live" and held_as(s, ITEMS, 0) is None, 20.0)
    rep.add("H8", status, f"{text}; back with its items after {took2 if took2 is None else round(took2, 1)} s")


def h9(c, spot, gic, rep) -> None:
    """A reload: Link teleported 90 m up falls, Game Over, Continue (A) loads the save again (as in the fourth round).
    The ghost follows him (`ghost follow`), so after the reload it spawns next to him again."""
    st = ensure_ghost(c, spot, gic)
    fx, fz = spot.f
    c.cmd("ghost", "follow", f"{0.3 * fx + 1.2 * fz:.2f}", "0", f"{0.3 * fz - 1.2 * fx:.2f}")
    before = slots(st)
    drops = st.get("drops", 0)
    p = link_pos(c)
    c.cmd("teleport", f"{p[0]:.2f}", f"{p[1] + 90:.2f}", f"{p[2]:.2f}")
    t0 = time.monotonic()
    lost = None                      # (the reload drops the ghost: a stage change, or the game removed it)
    press_at = t0 + 8.0              # (from then on A every 2.5 s: Game Over's Continue)
    while time.monotonic() - t0 < 120 and lost is None:
        try:
            if c.cmd("ghost").get("drops", 0) > drops:
                lost = time.monotonic() - t0
            elif time.monotonic() >= press_at:
                press_at = time.monotonic() + 2.5
                c.cmd("press", "a")
        except ControlError:
            pass
        time.sleep(0.25)
    t_world, end = None, time.monotonic() + 120
    while lost is not None and t_world is None and time.monotonic() < end:   # (the world answers again)
        try:
            c.cmd("pos")
            if gst(c).get("player", "0x0") != "0x0":
                t_world = time.monotonic()
                break
        except ControlError:
            pass
        time.sleep(0.5)
    took, st = (None, gst(c))
    if t_world is not None:
        took, st = poll(c, lambda s: s.get("state") == "live" and held_as(s, ITEMS, 0) is None, RELOAD_BACK_IN + 5)
    shot(c, "H9_after_reload")
    old = {sl["actor"] for sl in before}
    reused = [sl["actor"] for sl in slots(st) if sl["actor"] in old]
    status, text = judge_times("back after the world", [took], RELOAD_BACK_IN, held_as(st, ITEMS, 0),
                               st.get("items.stray"))
    if lost is None or t_world is None:
        status = "FAIL"
    rep.add("H9", status, f"ghost dropped {lost if lost is None else round(lost, 1)} s after the teleport up, the "
            f"world back {None if t_world is None else round(t_world - t0, 1)} s after it; {text}; last "
            f"{st.get('last')!r}; old addresses seen again {reused} (the heap reusing them)")
    c.cmd("ghost", "stop")
    gic.back_to(c, spot.p)
    spot.look(c)
    spot.place(c)


def h10(c, port, gic, rep, relay_port: int) -> None:
    py = [sys.executable, "-u"]
    script = ItemScript.parse(BOT_ITEMS)
    procs = [subprocess.Popen(py + [str(gic.REPO / "tools" / "mp" / "relay.py"), "--port", str(relay_port)])]
    time.sleep(1.0)
    procs.append(subprocess.Popen(py + [str(gic.REPO / "tools" / "mp" / "bot.py"), "--relay",
                                        f"127.0.0.1:{relay_port}", "--radius", "4", "--speed", "1",
                                        "--items", BOT_ITEMS]))
    ghost = client.Ghost(actor=client.GHOST_LINK_ACTOR, sync=True, items=True, log=lambda s: print("  " + s))
    runner = client.Runner(("127.0.0.1", relay_port), "Check", game_port=port, ghost=ghost, log=lambda s: None)
    th = threading.Thread(target=lambda: asyncio.run(runner.run()), daemon=True)
    th.start()
    samples = []
    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < 3 * script.duration:
            try:
                samples.append((time.monotonic(), gst(c)))
            except ControlError:
                pass
            if len(samples) % 25 == 10:
                shot(c, f"H10_{len(samples)}")
            time.sleep(0.1)
        rep.add("H10", *judge_network(samples[len(samples) // 6:], script))
    finally:
        runner.stop()
        th.join(10)
        for pr in procs:
            pr.terminate()


def h11(c, spot, gic, rep) -> None:
    ensure_ghost(c, spot, gic)
    alone, items, us, tick_alone, st = [], [], None, None, {}
    for _ in range(2):
        set_items(c, NONE3, 0)
        time.sleep(1.5)
        alone.append(gic.fps(c))
        tick_alone = gst(c).get("tick_us")
        set_items(c, ITEMS, 0)
        poll(c, lambda s: held_as(s, ITEMS, 0) is None, 4.0)
        items.append(gic.fps(c))
        st = gst(c)
        us = st.get("items.us")
    status, text = judge_cost(gic.mean_fps(alone), gic.mean_fps(items), us)
    rep.add("H11", status, text + f" (median of 64 frames; average {st.get('items.us_avg')}, most {st.get('items.us_max')}; "
            f"{st.get('items.calls')} game calls and {st.get('items.queries')} memory queries a frame; the whole tick "
            f"{tick_alone} us alone, {st.get('tick_us')} us with the items)")
    print(f"  H11: alone {gic.fmt_fps(alone)}, with items {gic.fmt_fps(items)}, us {us}", flush=True)


def take_given(c, spot, gic, name: str, slot_key: str) -> dict:
    """`ghost give` the item 1.6 m ahead of Link, walk up, press A until `ghost self` names it in its slot."""
    gic.back_to(c, spot.p)
    spot.look(c)
    fx, fz = gic.cam_forward(c) or spot.f
    p = link_pos(c)
    c.cmd("ghost", "give", name, f"{1.6 * fx:.2f}", "0.8", f"{1.6 * fz:.2f}")
    poll(c, lambda s: s.get("give.state") in ("done", "failed"), 5.0)
    time.sleep(1.5)
    shot(c, f"H12_{slot_key}_given")
    tx, tz = p[0] + 1.6 * fx, p[2] + 1.6 * fz
    end = time.monotonic() + 4.0
    try:
        while time.monotonic() < end:
            q = link_pos(c)
            if math.hypot(tx - q[0], tz - q[2]) < 0.6:
                break
            c.cmd("stick", "l", "0", "0.35", "10")
            time.sleep(0.1)
    finally:
        c.cmd("release", "all")
    time.sleep(0.4)
    me: dict = {}
    for k in range(8):
        c.cmd("press", "a" if k % 3 != 2 else "b")
        time.sleep(1.2)
        me = c.cmd("ghost", "self")
        if me.get(slot_key) == name:
            break
    time.sleep(1.0)
    for _ in range(2):   # (close a "you got" window, if one is open)
        c.cmd("press", "b")
        time.sleep(0.6)
    return c.cmd("ghost", "self")


def h12(c, spot, gic, rep) -> None:
    c.cmd("ghost", "stop")
    x, y, z = spot.at(-4.0, 3.0)      # (the ghost out of the way: behind Link)
    c.cmd("ghost", "pos", f"{x:.3f}", f"{y:.3f}", f"{z:.3f}", "0")
    me = {}
    for name, key in zip(ITEMS, ("weapon", "shield", "bow")):
        me = take_given(c, spot, gic, name, key)
        print(f"  H12: after {name}: {me.get('weapon')!r} {me.get('shield')!r} {me.get('bow')!r}", flush=True)
    shot(c, "H12_taken")
    rep.add("H12", *judge_give(me))
    gic.back_to(c, spot.p)


def h13(c, spot, gic, rep) -> None:
    rows: dict[str, dict] = {}

    def sprint():
        c.cmd("hold", "b")
        c.cmd("stick", "l", "0", "1", "2000")
        time.sleep(2.0)
        c.cmd("release", "all")
        time.sleep(0.8)

    for key, button, how in (("weapon", "y", "press"), ("shield", "zl", "hold"), ("bow", "zr", "hold")):
        gic.back_to(c, spot.p)
        spot.look(c)
        try:
            c.cmd(how, button)
        except ControlError as e:
            print(f"  H13: {how} {button}: {e}", flush=True)
            continue
        time.sleep(0.8)
        c.cmd("ghost", "self")
        time.sleep(0.2)
        rows[f"{key}_out"] = c.cmd("ghost", "self")
        shot(c, f"H13_{key}_out")
        c.cmd("release", "all")
        time.sleep(0.3)
        sprint()
        c.cmd("ghost", "self")
        time.sleep(0.2)
        rows[f"{key}_back"] = c.cmd("ghost", "self")
        shot(c, f"H13_{key}_back")
    rep.add("H13", *judge_back_bytes(rows))
    gic.back_to(c, spot.p)


def run(c: Control, port: int, gic, rep, *, give: bool) -> None:
    """Every check in the plan's order (out/research/ghost-items-plan.md): L2, H1-H4, H5 (unarmed), H6-H9, H11, H10,
    H12, H13, H5 again (armed). H14 is judged by the caller after the game closed."""
    spot = Spot(c, gic)
    c.cmd("ghost", "self")
    time.sleep(0.3)
    me = c.cmd("ghost", "self")
    armed = bool(me.get("weapon") or me.get("shield") or me.get("bow"))
    print(f"  Link has {me.get('weapon')!r} {me.get('shield')!r} {me.get('bow')!r}", flush=True)
    guard(rep, "H1", h1, c, port, spot, gic, rep)
    guard(rep, "H2", h2, c, spot, gic, rep)
    guard(rep, "H3", h3, c, spot, gic, rep)
    guard(rep, "H4", h4, c, spot, gic, rep)
    guard(rep, "H5", h5, c, spot, gic, rep, "armed" if armed else "unarmed")
    guard(rep, "H6", h6, c, spot, gic, rep)
    guard(rep, "H7", h7, c, spot, gic, rep)
    guard(rep, "H8", h8, c, spot, gic, rep)
    guard(rep, "H9", h9, c, spot, gic, rep)
    spot = Spot(c, gic)   # (after the reload: measured again)
    guard(rep, "H11", h11, c, spot, gic, rep)
    guard(rep, "H10", h10, c, port, gic, rep, gic.RELAY_PORT)
    c.cmd("ghost", "spawn", "link")
    gic.wait_live(c)
    spot.place(c)
    if give:
        guard(rep, "H12", h12, c, spot, gic, rep)
    else:
        rep.add("H12", "SKIP", "no --give-weapon")
    guard(rep, "H13", h13, c, spot, gic, rep)
    me = c.cmd("ghost", "self")
    if me.get("weapon") or me.get("shield") or me.get("bow"):
        guard(rep, "H5", h5, c, spot, gic, rep, "armed")
    c.cmd("ghost", "items", "off")
    c.cmd("ghost", "despawn")
