#!/usr/bin/env python3
"""In-game checks for the ghost's held items (ghost mod 0.6.2, docs/ghost.md "Held items"). Run through
ghost_ingame_check.py --items (it starts the game once, with the Link body, and closes it at the end):

    python tools/mp/ghost_ingame_check.py --items --spot -1033.6 252.3 1809.3 132 --give-weapon

The mod drives the items directly with `ghost items` (it plays the remote player); H10 goes through a relay, a bot and
the client. The ghost stands about 2.6 m in front of Link, a little to the left, facing him. Screenshots go to
out\\shots\\ghostitems_*.png and the results to out\\research\\ghost-items-results.txt (never committed).

  H1 in hand: sword, shield, bow held (state 255, holder the ghost) within 3 s as Link carries them with a sword out
     (MASK: sword in the right hand, shield on the left arm, bow on the back), each item in a hand within HAND_DIST of
     its hand bone while the ghost stands, walks and runs a circle, jumps, glides, climbs and swings (CutS1); then
     the bow drawn (MASK_BOW: the bow in the left hand, sword and shield on the back) (0.6.2: as Link, not all three
     in the hands)
  H2 switch: sword -> two-hander -> spear -> sword, each held within 2 s, then 10 switches 0.3 s apart; no stray
  H3 clear: the shield to none (the others stay the same actors), then none at all; gone within 1 s, no stray
  H4 back: mask 7 (camera behind the ghost), then 0, then 10 toggles: on Pod_A at the item's own offset (its
     distance to Pod_A within BACK_TOL of the offset's length), in the hand again on 0 (SKIP: no back placement)
  H5 can't take: Link walks up to the ghost and presses A five times (unarmed, and again armed after H12): his own
     items don't change, the ghost's are the same actors, still held
  H6 despawn, H7 far rule, H8 the game removes the ghost, H9 reload: no item left behind (stray 0), and the ghost
     gets its items again (H7: within 5 s; H9: within 60 s of the drop, through Game Over and the load)
  H10 network: relay + bot.py --items + the client with --ghost-items: names and places follow every change of
     every cycle within NET_IN of the bot's own time for it; then an older sender (bot --no-on-back): the bow on
     the back (the receiver's guess)
  H11 cost: frames a second with the ghost alone and with its 3 items, COST_PAIRS pairs in turn (0.6.2: 3; the
     median of each side, drop at most FPS_DROP; every pair is reported), items.us_mean (the plain mean over the last
     64 frames, the plan's rule) under US_MAX; the longest item part and the longest whole tick over the same frames;
     slow parts with the thread's own cycles (few cycles: it was switched out)
  H12 give: `ghost give` a sword, a shield and a bow near Link; he takes each (A); `ghost self` names all three
  H13 the sender's bytes: weapon, shield and bow out (Y, ZL, ZR) read "not on the back", after a sprint "on the back"
  H4P the ghost's back from the items' own offsets (H4) against Link's own (H13), each in its Pod_A's frame (0.6.1;
     the bow is about 0.1 m off: Link's armor adds to it)
  H4L like Link (0.6.2): Link sprints (all on his back), his own back offsets (`ghost self` `<item>_rel`) go to the
     mod (`ghost items rel`), and the ghost's items must sit where his do: the same place within LIKE_M and the same
     turn within LIKE_DEG; a picture of the two from behind
  H15 a real sender: a second client reads this Link (`ghost self`) and sends him, 1.5 m to the side, through a
     relay to the client with --ghost-items: the ghost shows what Link holds, where he holds it, after a sprint,
     a draw (Y), a throw (R: he drops the weapon) and a new sword (0.6.1); after the sprint, his items on the
     ghost's back exactly where his are (his own back offsets in the state he sends, 0.6.2)
  H14 log: no exception, assert, panic or fatal line in savagewind.log (judged by ghost_ingame_check.py after exit)
  H16 size (0.6.2): every held item drawn at its normal size (`items.N.size`, its matrix's axes times its own scale,
     within SIZE_TOL of 1) in every H1 and H4 sample, and Link's own items (H13's `ghost self` `<key>_size`) at 1 too;
     the first size (before the mod's fix), the axes and the scales are reported
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
MASK = 4              # 0.6.2: the usual look (Link with a sword out): sword in the right hand, shield on the left arm,
MASK_BOW = 3          # the bow on the back; the bow drawn: the bow in the left hand, sword and shield on the back
SIZE_TOL = 0.05       # H16: a held item drawn within this of its normal size (1)
SWITCHES = ("Weapon_Lsword_001", "Weapon_Spear_001", "Weapon_Sword_001")
NONE3 = ("", "", "")
HAND_DIST = 0.05      # m: H1 an item at most this far from its hand bone is in the hand
BACK_TOL = 0.05       # m: H4 |distance to Pod_A - the length of its own offset|
FPS_DROP = 1.0        # H11 frames a second
COST_PAIRS = 3        # H11 (0.6.2): alone / with the items, this many pairs in turn
US_MAX = 50.0         # H11 microseconds a frame (average)
HELD_IN = 3.0         # s: H1
SWITCH_IN = 2.0       # s: H2
CLEAR_IN = 1.0        # s: H3
FAR_BACK_IN = 5.0     # s: H7
RELOAD_BACK_IN = 60.0  # s: H9 from the drop (Game Over, Continue, the load) to the items held again
RELOAD_WAIT = 150.0   # s: H9 at most this long in all
NET_IN = 1.0          # s: H10 from the bot's change to the items held in place (the plan's "within 1 s")
GONE_IN = 1.0         # s: H2 the old item gone (no deleted item left: items.dying 0) after each switch
RUN_SPEED = 5.5       # m/s: H1 the ghost runs a circle too
LIKE_M = 0.05         # m: H4L the ghost's item and Link's, each in its Pod_A's frame
LIKE_DEG = 10.0       # degrees: ... and their turn
E2E_IN = 2.0          # s: H15 from Link's change to the ghost showing it (the sender's `ghost self` is read 20 times a
                      # second, the receiver's jitter buffer, then the spawn)
BOT_ITEMS = "Weapon_Sword_001/Weapon_Shield_001/Weapon_Bow_001/4:5,Weapon_Lsword_001/-/Weapon_Bow_001/5:5,-/-/-/0:3"
BOT_STEP = re.compile(r"items step: (\S+) (\S+) (\S+) mask (\d) at (\d+(?:\.\d+)?)")
LOG_BAD = re.compile(r"exception|assert|panic|fatal|access violation", re.I)
SLOT_KEYS = ("name", "state", "actor", "place", "dist_m", "hold_m", "tries", "item_state", "holder_ok", "why", "rel",
             "refusals", "size", "size_first", "axis", "own_scale", "model_scale", "copied")


# ------------------------------------------------------------------ judging (pure: tests/test_mp_items.py)

def slots(st: dict) -> list[dict]:
    """The three item slots of a `ghost` reply (weapon, shield, bow), each {name, state, actor, place, dist_m, ...}."""
    st = st if isinstance(st, dict) else {}
    return [{k: st.get(f"items.{i}.{k}") for k in SLOT_KEYS} for i in range(3)]


def held_as(st: dict, names, mask: int = 0, max_dist: float | None = None) -> str | None:
    """None when the reply shows exactly `names` held (state held, item state 255, holder the ghost; an empty name: an
    empty slot), each in its hand or, for a bit of mask, on the back; max_dist: also at most that far from its hand
    bone (0.6.2: only an item in a hand). Else why not."""
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
        if max_dist is not None and place == "hand" and not (isinstance(d, (int, float)) and 0 <= d <= max_dist):
            return f"slot {i}: {d} m from its hand bone"
    return None


def judge_held(phases: dict[str, list[dict]], names=ITEMS, max_dist: float = HAND_DIST, mask: int = 0
               ) -> tuple[str, str]:
    """H1: {"standing": [`ghost` replies], "walking": [...], "attacking": [...]}: every sample shows the three held in
    their places (mask: which on the back), those in a hand at most max_dist from their hand bones."""
    parts, ok = [], True
    for phase, samples in phases.items():
        if not samples:
            ok = False
            parts.append(f"{phase}: no samples")
            continue
        bad = [w for w in (held_as(s, names, mask, max_dist) for s in samples) if w]
        worst = max((sl["dist_m"] for s in samples for sl in slots(s)[:3]
                     if sl["place"] == "hand" and isinstance(sl["dist_m"], (int, float)) and sl["dist_m"] >= 0),
                    default=-1.0)
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


def judge_back(back: list[dict], hand: list[dict], bones: str, tol: float = BACK_TOL, hand_mask: int = 0
               ) -> tuple[str, str]:
    """H4: `ghost` replies with mask 7 (back), then with mask 0 (hand). On the back: each item on Pod_A at the length
    of its own offset (|dist_m - hold_m| <= tol). SKIP when the ghost has no Pod_A or no item ever got its offsets
    (the fallback: they stay in the hand). This only shows our follow is on (any offset passes); whether it looks
    like Link's is H4L."""
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
    hand_why = held_as(hand[-1], ITEMS, hand_mask)
    ok = why is None and hand_why is None
    text = (f"back: {why or 'all three on Pod_A'}, off by at most {max(errs, default=-1):.3f} m; "
            f"then hand (mask {hand_mask}): {hand_why or 'each in its place'}")
    return ("PASS" if ok else "FAIL"), text


def judge_no_take(me0: dict, me1: dict, actors0: list, st1: dict, mask: int = 0) -> tuple[str, str]:
    """H5: Link's own items before and after pressing A next to the ghost, and the ghost's item actors before and its
    reply after: nothing moved over."""
    own0 = tuple(me0.get(k) for k in ("weapon", "shield", "bow"))
    own1 = tuple(me1.get(k) for k in ("weapon", "shield", "bow"))
    actors1 = [sl["actor"] for sl in slots(st1)]
    why = held_as(st1, ITEMS, mask)
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


def median(vals) -> float | None:
    v = sorted(x for x in vals if isinstance(x, (int, float)))
    if not v:
        return None
    return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2


def judge_pairs(alone: list, items: list, us_mean: float | None, drop: float = FPS_DROP, us_max: float = US_MAX
                ) -> tuple[str, str]:
    """H11 (0.6.2): pairs measured in turn (alone, then with the items): the median of each side; every pair shown."""
    pairs = ", ".join(f"{'?' if a is None else f'{a:.1f}'}/{'?' if b is None else f'{b:.1f}'}"
                      for a, b in zip(alone, items))
    status, text = judge_cost(median(alone), median(items), us_mean, drop, us_max)
    return status, f"medians of {len(alone)} pairs: {text}; pairs alone/with {pairs}"


def judge_cost(base: float | None, with_items: float | None, us_mean: float | None, drop: float = FPS_DROP,
               us_max: float = US_MAX) -> tuple[str, str]:
    """H11 (the plan's rule): frames a second with the ghost alone and with its three items; the items' plain mean
    microseconds a frame (over the last 64 frames: one slow frame counts in full)."""
    if base is None or with_items is None:
        return "SKIP", "no frame rate"
    ok = base - with_items <= drop and isinstance(us_mean, (int, float)) and us_mean < us_max
    return ("PASS" if ok else "FAIL"), (f"{base:.1f} -> {with_items:.1f} frames a second (drop limit {drop:g}); items "
                                        f"{us_mean if us_mean is None else round(us_mean, 1)} us a frame on average "
                                        f"(limit {us_max:g})")


def bot_changes(lines: list[str]) -> list[tuple[float, tuple[str, str, str], int]]:
    """The bot's own log lines "items step: W S B mask M at T" (bot.py --items): (T, names, mask) for each change."""
    out = []
    for ln in lines:
        m = BOT_STEP.search(ln)
        if m:
            names = tuple("" if n == "-" else n for n in m.group(1, 2, 3))
            out.append((float(m.group(5)), names, int(m.group(4))))
    return out


def judge_network(samples: list[tuple[float, dict]], changes: list[tuple[float, tuple, int]], limit: float = NET_IN,
                  mask_for=None) -> tuple[str, str]:
    """H10: (time, `ghost` reply) while the bot ran, and the bot's changes (bot_changes: its own clock, the same as
    ours). Every change of every cycle: its names held in their places (mask_for(mask): the mask the receiver should
    show, e.g. its guess for an older sender) at most `limit` s after the bot made it, before the next change. The
    first change (sent before the client was ready) and one with no sample after it are left out."""
    mask_for = mask_for or (lambda m: m)
    delays, late = [], []
    end = samples[-1][0] if samples else 0.0
    for k, (t0, names, mask) in enumerate(changes):
        t1 = changes[k + 1][0] if k + 1 < len(changes) else end
        if k == 0 or t0 >= end:
            continue
        done = next((t for t, st in samples if t0 <= t <= t1 and held_as(st, names, mask_for(mask)) is None), None)
        if done is None or done - t0 > limit:
            late.append(("/".join(n or "-" for n in names), None if done is None else round(done - t0, 2)))
        else:
            delays.append(done - t0)
    ok = bool(delays) and not late
    text = (f"{len(delays)} changes held in place, the slowest {max(delays, default=0):.2f} s after the bot's change "
            f"(limit {limit:g})" + (f"; late or never: {late}" if late else ""))
    return ("PASS" if ok else "FAIL"), text


def rel_matrix(text) -> list[float] | None:
    """A `rel` reply value (12 numbers, a 3x4 matrix row by row) as a list; None when empty or bad."""
    try:
        v = [float(x) for x in str(text or "").split()]
    except ValueError:
        return None
    return v if len(v) == 12 and all(math.isfinite(x) for x in v) else None


def rel_compare(a: list[float], b: list[float]) -> tuple[float, float]:
    """Two 3x4 matrices: the distance between their places (m) and the angle between their turns (degrees; each
    3x3 part's axes are normalized first)."""
    dist = math.dist((a[3], a[7], a[11]), (b[3], b[7], b[11]))

    def axes(m):
        cols = [[m[r * 4 + c] for r in range(3)] for c in range(3)]
        out = []
        for col in cols:
            n = math.sqrt(sum(x * x for x in col)) or 1.0
            out.append([x / n for x in col])
        return out
    ca, cb = axes(a), axes(b)
    trace = sum(sum(ca[c][r] * cb[c][r] for r in range(3)) for c in range(3))
    angle = math.degrees(math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0))))
    return dist, angle


def judge_like_link(ghost_back: dict, link_rows: dict[str, dict], names=ITEMS, max_m: float = LIKE_M,
                    max_deg: float = LIKE_DEG) -> tuple[str, str]:
    """H4L: the ghost's items on its back (a `ghost` reply with mask 7, `items.N.rel`) against Link's own on his back
    (H13's `ghost self` rows "<key>_back": `<key>_rel`, and `<key>_pods`, the distances to Pod_A, B, C), the same
    item names. SKIP when Link had none of them on his back."""
    parts, ok, any_ = [], True, False
    gs = slots(ghost_back)
    for i, key in enumerate(("weapon", "shield", "bow")):
        row = link_rows.get(f"{key}_back") or {}
        lm, gm = rel_matrix(row.get(f"{key}_rel")), rel_matrix(gs[i]["rel"])
        if row.get(key) != names[i] or row.get(f"{key}_back") is not True or lm is None:
            parts.append(f"{key}: Link's not on his back or not read")
            continue
        any_ = True
        if gm is None:
            ok = False
            parts.append(f"{key}: the ghost's not read")
            continue
        d, a = rel_compare(gm, lm)
        good = d <= max_m and a <= max_deg
        ok &= good
        delta = "" if good else (f"; Link's minus the ghost's place in Pod_A's frame {lm[3] - gm[3]:+.3f} "
                                 f"{lm[7] - gm[7]:+.3f} {lm[11] - gm[11]:+.3f} m")   # (0.6.2: for a later fix)
        parts.append(f"{key}: {d:.3f} m and {a:.0f} deg apart (Link's nearest pods {row.get(f'{key}_pods')}){delta}")
    if not any_:
        return "SKIP", "; ".join(parts)
    return ("PASS" if ok else "FAIL"), "; ".join(parts) + f" (limits {max_m:g} m, {max_deg:g} deg)"


def judge_sender(steps: list[tuple[str, float | None, str | None]], limit: float = E2E_IN) -> tuple[str, str]:
    """H15: (what Link did, seconds until the ghost showed what `ghost self` read on him, or None; why not)."""
    ok = bool(steps) and all(t is not None and t <= limit for _, t, _ in steps)
    text = "; ".join(f"{what}: " + (f"{t:.2f} s" if t is not None else f"never ({why})") for what, t, why in steps)
    return ("PASS" if ok else "FAIL"), text + f" (limit {limit:g} s)"


def link_shows(st: dict, me: dict) -> str | None:
    """None when the ghost (a `ghost` reply) holds what Link holds (a `ghost self` reply: names and on_back), each
    in its place; else why not. A name the ghost may not hold (client.item_name_ok) counts as none."""
    names = tuple(me.get(k) if client.item_name_ok(i, me.get(k)) else "" for i, k in enumerate(("weapon", "shield",
                                                                                                  "bow")))
    mask = me.get("on_back") if isinstance(me.get("on_back"), int) else 4
    return held_as(st, names, mask)


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


def judge_size(samples: list[dict], link_rows: dict[str, dict] | None = None, tol: float = SIZE_TOL
               ) -> tuple[str, str]:
    """H16 (0.6.2): `ghost` replies (H1, H4): every held item drawn within tol of its normal size (`items.N.size`);
    Link's own items (`ghost self` rows, `<key>_size`) at 1 too (the mod's rule: Link's are drawn at 1). Reports the
    first size each slot had (before the mod's fix), its matrix's axes and its own and its model's scale."""
    vals, bad, first = [], [], {}
    for st in samples:
        for i, sl in enumerate(slots(st)):
            v = sl["size"]
            if sl["state"] != "held" or not isinstance(v, (int, float)) or v <= 0:
                continue
            vals.append(v)
            if abs(v - 1.0) > tol:
                bad.append((sl["name"], round(v, 2)))
            first.setdefault(i, sl)
    link = [(k, row.get(f"{k}_size")) for row in (link_rows or {}).values() for k in ("weapon", "shield", "bow")
            if isinstance(row.get(f"{k}_size"), (int, float)) and row.get(f"{k}_size") > 0]
    link_bad = [(k, round(v, 2)) for k, v in link if abs(v - 1.0) > tol]
    how = "; ".join(f"{sl['name']}: first {sl['size_first']}, axes {sl['axis']}, own scale {sl['own_scale']}, model "
                    f"scale {sl['model_scale']}" for _, sl in sorted(first.items()))
    sets = next((st.get("items.scale_sets") for st in reversed(samples) if "items.scale_sets" in st), None)
    how += f"; the player's scale set {sets} times (all items, so far)"
    if not vals:
        return "FAIL", "no held item's size read (an older mod?)"
    ok = not bad and not link_bad
    text = (f"{len(vals)} sizes {min(vals):.2f}-{max(vals):.2f} (limit 1 +- {tol:g})"
            + (f"; off: {bad[:4]}" if bad else "")
            + (f"; Link's own {sorted(set(round(v, 2) for _, v in link))}" if link else "; Link's own not read")
            + (f" (not 1: {link_bad})" if link_bad else "") + f". {how}")
    return ("PASS" if ok else "FAIL"), text


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


def ensure_ghost(c: Control, spot: Spot, gic, names=ITEMS, mask: int = MASK, secs: float = 15.0) -> dict:
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


def h1(c, port, spot, gic, rep, ctx: dict) -> None:
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
    set_items(c, ITEMS, MASK)
    took, st = poll(c, lambda s: held_as(s, ITEMS, MASK) is None, HELD_IN + 2)
    print(f"  H1: held after {took} s; bones {st.get('items.bones')!r}; {st.get('last')!r}", flush=True)
    time.sleep(0.5)
    shot(c, "H1_stand")
    phases = {"standing": sample(c, 2.0)}
    x, y, z = spot.at(3.2, 0.0)     # (0.6.1: the circle in front of Link, so the walk shot shows the ghost)
    th = threading.Thread(target=walk_circle, args=(port, (x, y, z), 4.0, 1.0), daemon=True)
    th.start()
    time.sleep(1.0)
    shot(c, "H1_walk")
    phases["walking"] = sample(c, 2.5)
    th.join(6)
    x, y, z = spot.at(5.0, 0.0)     # (running: a wider circle, farther ahead)
    th = threading.Thread(target=walk_circle, args=(port, (x, y, z), 4.0, 2.5, RUN_SPEED), daemon=True)
    th.start()
    time.sleep(1.0)
    shot(c, "H1_run")
    phases["running"] = sample(c, 2.5)
    th.join(6)
    spot.place(c)
    for anim in ("Jump", "ParashawlGlide", "ClimbMove"):   # (other poses with the items in hand)
        c.cmd("ghost", "anim", anim)
        time.sleep(0.3)
        phases.setdefault("jump, glide, climb", []).extend(sample(c, 0.8))
    shot(c, "H1_climb")
    c.cmd("ghost", "anim", "CutS1")
    time.sleep(0.25)
    shot(c, "H1_attack")
    phases["attacking"] = sample(c, 1.0)
    c.cmd("ghost", "anim", "auto")
    c.cmd("ghost", "anim", "CutS1")
    phases["attacking"] += sample(c, 1.0)
    c.cmd("ghost", "anim", "auto")
    ctx.setdefault("sizes", []).extend(s for v in phases.values() for s in v)   # (H16)
    set_items(c, ITEMS, MASK_BOW)   # (0.6.2: the bow drawn, as Link: the bow in the left hand, the rest on the back)
    poll(c, lambda s: held_as(s, ITEMS, MASK_BOW) is None, 3.0)
    time.sleep(0.4)
    shot(c, "H1_bow_drawn")
    bow_why = held_as(gst(c), ITEMS, MASK_BOW, HAND_DIST)
    set_items(c, ITEMS, MASK)
    poll(c, lambda s: held_as(s, ITEMS, MASK) is None, 3.0)
    time.sleep(0.4)
    shot(c, "H1_sword_out")
    status, text = judge_held(phases, mask=MASK)
    text += f"; the bow drawn: {bow_why or 'bow in the left hand, sword and shield on the back'}"
    if bow_why:
        status = "FAIL"
    if took is None or took > HELD_IN:
        status, text = "FAIL", f"not all held within {HELD_IN:g} s ({held_as(st, ITEMS, MASK)}); " + text
    rep.add("H1", status, f"held after {took if took is None else round(took, 2)} s; " + text)


def frames_over(c: Control, secs: float, during=None) -> tuple[float, int]:
    """The longest frame (ms) and the frames over 50 ms (the mod's own frame times) while `during` runs (or secs pass);
    the mod keeps the last 64 frames, so it is asked every 0.5 s."""
    st0 = gst(c)
    most, end = 0.0, time.monotonic() + secs
    th = threading.Thread(target=during, daemon=True) if during else None
    if th:
        th.start()
    while time.monotonic() < end or (th and th.is_alive()):
        time.sleep(0.5)
        st = gst(c)
        most = max(most, float(st.get("frame_ms_max") or 0.0))
    return most, int(gst(c).get("frames_slow", 0)) - int(st0.get("frames_slow", 0))


def h2(c, spot, gic, rep) -> None:
    ensure_ghost(c, spot, gic)
    times, gone = [], []
    for name in SWITCHES:
        old = slots(gst(c))[0]["actor"]
        set_items(c, (name, ITEMS[1], ITEMS[2]), MASK)
        t0 = time.monotonic()
        took, st = poll(c, lambda s, n=name, o=old: held_as(s, (n, ITEMS[1], ITEMS[2]), MASK) is None
                        and slots(s)[0]["actor"] != o, SWITCH_IN + 2)
        times.append(took)
        _, st = poll(c, lambda s: s.get("items.dying") == 0, GONE_IN + 1, 0.05)   # (0.6.1: the old one is gone)
        gone.append(time.monotonic() - t0 if st.get("items.dying") == 0 else None)
        shot(c, f"H2_{name}")
    quiet = frames_over(c, 3.0)

    def switches():
        for k in range(10):
            set_items(c2, (SWITCHES[k % 2], ITEMS[1], ITEMS[2]), MASK)
            time.sleep(0.3)
    with Control(c.sock.getpeername()[1], timeout=5) as c2:   # (a second connection: c is polled meanwhile)
        busy = frames_over(c, 0.0, switches)
    set_items(c, ITEMS, MASK)
    took, st = poll(c, lambda s: held_as(s, ITEMS, MASK) is None, SWITCH_IN + 2)
    times.append(took)
    time.sleep(1.5)
    st = gst(c)
    shot(c, "H2_end")
    status, text = judge_times("switches", times, SWITCH_IN, held_as(st, ITEMS, MASK), st.get("items.stray"))
    if any(g is None or g > GONE_IN for g in gone):   # (deleted at the switch: gone within GONE_IN of it)
        status = "FAIL"
    rep.add("H2", status, text + f"; the old item gone {['never' if g is None else round(g, 2) for g in gone]} s "
            f"after each switch; the longest frame {quiet[0]:.0f} ms quiet, {busy[0]:.0f} ms during the 10 quick "
            f"switches (frames over 50 ms: {quiet[1]} quiet, {busy[1]} switching)")


def h3(c, spot, gic, rep) -> None:
    st = ensure_ghost(c, spot, gic)
    keep = [sl["actor"] for sl in slots(st)]
    set_items(c, (ITEMS[0], "", ITEMS[2]), MASK)
    t1, st = poll(c, lambda s: held_as(s, (ITEMS[0], "", ITEMS[2]), MASK) is None, CLEAR_IN + 1)
    same = [sl["actor"] for sl in slots(st)][0::2] == keep[0::2]
    time.sleep(0.4)
    shot(c, "H3_shield_cleared")   # (0.6.1: sword and bow kept, the shield gone)
    set_items(c, NONE3, 0)
    t2, st = poll(c, lambda s: held_as(s, NONE3, 0) is None, CLEAR_IN + 1)
    time.sleep(1.5)
    st = gst(c)
    shot(c, "H3_cleared")
    status, text = judge_times("cleared", [t1, t2], CLEAR_IN, held_as(st, NONE3, 0), st.get("items.stray"))
    if not same:
        status, text = "FAIL", text + "; the weapon or bow was replaced when only the shield changed"
    rep.add("H3", status, text)


def h4(c, spot, gic, rep, ctx: dict) -> None:
    ensure_ghost(c, spot, gic)
    spot.place(c, facing="away")
    set_items(c, ITEMS, 7)
    poll(c, lambda s: held_as(s, ITEMS, 7) is None, 3.0)
    time.sleep(0.6)
    shot(c, "H4_back")
    back = sample(c, 1.0)
    ctx["ghost_back"] = back[-1] if back else {}   # (H4L: against Link's own, after H13)
    ctx.setdefault("sizes", []).extend(back)      # (H16: on the back too)
    set_items(c, ITEMS, MASK)
    poll(c, lambda s: held_as(s, ITEMS, MASK) is None, 3.0)
    time.sleep(0.4)
    shot(c, "H4_hand_again")
    for k in range(10):
        set_items(c, ITEMS, 7 if k % 2 == 0 else MASK)
        time.sleep(0.3)
    set_items(c, ITEMS, MASK)
    poll(c, lambda s: held_as(s, ITEMS, MASK) is None, 3.0)
    hand = sample(c, 0.6)
    st = hand[-1] if hand else {}
    status, text = judge_back(back, hand, str(st.get("items.bones")), hand_mask=MASK)
    rep.add("H4", status, text + f"; after 10 toggles stray {st.get('items.stray')}")
    spot.place(c)


def h5(c, spot, gic, rep, label: str) -> None:
    gic.back_to(c, spot.p)
    spot.look(c)
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
    status, text = judge_no_take(me0, me1, actors0, st1, MASK)
    far = math.hypot(x - p[0], z - p[2])
    if far > 1.2:   # (0.6.2: run 4's armed H5 "passed" 20 m away: Link never reached the ghost)
        status, text = "FAIL", f"Link didn't reach the ghost; {text}"
    rep.add("H5", status, f"{label}, {far:.2f} m from the ghost: {text}")
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
    took, st = poll(c, lambda s: s.get("state") == "live" and held_as(s, ITEMS, MASK) is None, FAR_BACK_IN + 3)
    shot(c, "H7_back")
    s2, t2 = judge_times("back", [took], FAR_BACK_IN, held_as(st, ITEMS, MASK), st.get("items.stray"))
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
    shot(c, "H8_removed")   # (0.6.1: the ghost 90 m off; nothing left near Link)
    status, text = judge_gone(st, "the game removed it", ("idle", "pending", "live"))
    if took is None:
        status, text = "FAIL", "the game didn't remove the ghost at 90 m within 6 s; " + text
    c.cmd("ghost", "far", "60", "50")
    spot.place(c)
    took2, st2 = poll(c, lambda s: s.get("state") == "live" and held_as(s, ITEMS, MASK) is None, 20.0)
    time.sleep(0.4)
    shot(c, "H8_back")
    rep.add("H8", status, f"{text}; back with its items after {took2 if took2 is None else round(took2, 1)} s")


def h9(c, spot, gic, rep) -> None:
    """A reload: Link teleported 90 m up falls, Game Over, Continue (A) loads the save again (as in the fourth round).
    The ghost follows him (`ghost follow`), so after the reload it spawns next to him again."""
    st = ensure_ghost(c, spot, gic)
    fx, fz = spot.f
    c.cmd("ghost", "follow", f"{0.3 * fx + 1.2 * fz:.2f}", "0", f"{0.3 * fz - 1.2 * fx:.2f}")
    before = slots(st)
    drops = st.get("drops", 0)
    p = link_pos(c)   # (2 m ahead too: Savage Wind's teleport refuses a target under 0.05 m away on the ground)
    c.cmd("teleport", f"{p[0] + 2 * fx:.2f}", f"{p[1] + 90:.2f}", f"{p[2] + 2 * fz:.2f}")
    t0 = time.monotonic()
    lost = None                      # (the reload drops the ghost: a stage change, or the game removed it)
    press_at = t0 + 8.0              # (from then on A every 2.5 s until the ghost holds its items again: Game Over's
    took, st = None, {}              # Continue. Run 4 of the twelfth round stopped pressing once the ghost was
    while time.monotonic() - t0 < RELOAD_WAIT:   # dropped, and the game stayed on the Game Over screen)
        try:
            st = gst(c)
            if lost is None and st.get("drops", 0) > drops:
                lost = time.monotonic() - t0
            if lost is not None and st.get("state") == "live" and held_as(st, ITEMS, MASK) is None:
                took = time.monotonic() - t0 - lost
                break
            if time.monotonic() >= press_at:
                press_at = time.monotonic() + 2.5
                c.cmd("press", "a")
        except ControlError:
            pass
        time.sleep(0.25)
    shot(c, "H9_after_reload")
    time.sleep(1.5)
    st = gst(c) if took is not None else st   # (0.6.1: the old items are watched through the stage change: a stray
    old = {sl["actor"] for sl in before}      # is one the reload left in the world)
    reused = [sl["actor"] for sl in slots(st) if sl["actor"] in old]
    status, text = judge_times("back with its items after the drop", [took], RELOAD_BACK_IN, held_as(st, ITEMS, MASK),
                               st.get("items.stray"))
    if lost is None:
        status = "FAIL"
    rep.add("H9", status, f"ghost dropped {lost if lost is None else round(lost, 1)} s after the teleport up (Game "
            f"Over, Continue, the reload); {text}; old items still there (watched) {st.get('items.dying')}; frames "
            f"with no player {st.get('no_player_ticks')}; last {st.get('last')!r}; old addresses seen again {reused} "
            "(the heap reusing them)")
    c.cmd("ghost", "stop")
    gic.back_to(c, spot.p)
    spot.look(c)
    spot.place(c)


def run_bot_items(c, port, gic, relay_port: int, cycles: float, extra=(), label: str = "H10"):
    """A relay, bot.py --items (its stdout read for its own "items step" lines) and a client with --ghost-items on
    this game, for `cycles` of the script; `ghost` sampled 10 times a second. (samples, the bot's changes, how its
    times were taken)."""
    py = [sys.executable, "-u"]
    script = ItemScript.parse(BOT_ITEMS)
    procs = [subprocess.Popen(py + [str(gic.REPO / "tools" / "mp" / "relay.py"), "--port", str(relay_port)])]
    time.sleep(1.0)
    bot = subprocess.Popen(py + [str(gic.REPO / "tools" / "mp" / "bot.py"), "--relay", f"127.0.0.1:{relay_port}",
                                 "--radius", "4", "--speed", "1", "--items", BOT_ITEMS, *extra],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                           errors="replace")
    procs.append(bot)
    lines: list[tuple[float, str]] = []

    def read():
        for ln in bot.stdout:
            lines.append((time.monotonic(), ln.strip()))
    threading.Thread(target=read, daemon=True).start()
    ghost = client.Ghost(actor=client.GHOST_LINK_ACTOR, sync=True, items=True, log=lambda s: print("  " + s))
    runner = client.Runner(("127.0.0.1", relay_port), "Check", game_port=port, ghost=ghost, log=lambda s: None)
    th = threading.Thread(target=lambda: asyncio.run(runner.run()), daemon=True)
    th.start()
    samples = []
    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < cycles * script.duration:
            try:
                samples.append((time.monotonic(), gst(c)))
            except ControlError:
                pass
            if len(samples) % 25 == 10:
                shot(c, f"{label}_{len(samples)}")
            time.sleep(0.1)
    finally:
        runner.stop()
        th.join(10)
        for pr in procs:
            pr.terminate()
    changes = bot_changes([ln for _, ln in lines])
    arrived = [t for t, ln in lines if BOT_STEP.search(ln)]
    # (the bot's clock is ours on the same PC; if not, the time its line came is used: a few ms later)
    skew = max((abs(a - ch[0]) for a, ch in zip(arrived, changes)), default=0.0)
    how = "the bot's own times"
    if skew > 0.5:
        changes = [(a, names, mask) for a, (_, names, mask) in zip(arrived, changes)]
        how = f"the times its lines came (its clock was {skew:.1f} s off)"
    return samples, changes, how


def h10(c, port, gic, rep, relay_port: int) -> None:
    samples, changes, how = run_bot_items(c, port, gic, relay_port, 3)
    status, text = judge_network(samples, changes)
    rep.add("H10", status, text + f" ({how})")
    # an older sender (no on_back): the receiver's guess, the bow on its back (client.item_mask)
    samples, changes, how = run_bot_items(c, port, gic, relay_port, 2, ("--no-on-back",), "H10old")
    status, text = judge_network(samples, changes, mask_for=lambda m: client.item_mask({}))
    rep.add("H10", status, "an older sender (no on_back, so the bow on the back): " + text + f" ({how})")


def watched_fps(c, gic) -> tuple[float | None, list[dict]]:
    """Frames a second over 5 s (`frames 5`) while a second connection asks `ghost` every 0.5 s."""
    out, stop = [], threading.Event()

    def watch():
        with Control(c.sock.getpeername()[1], timeout=5) as c2:
            while not stop.is_set():
                try:
                    out.append(c2.cmd("ghost"))
                except ControlError:
                    return
                time.sleep(0.5)
    th = threading.Thread(target=watch, daemon=True)
    th.start()
    try:
        return gic.fps(c), out
    finally:
        stop.set()
        th.join(3)


def cost_text(rows: list[dict]) -> str:
    """What the `ghost` replies over a measurement say about the cost: the items' mean (plan) and most, the whole
    tick's most over the same frames, the longest frame, slow parts and calls."""
    def most(key):
        return max((float(r.get(key) or 0) for r in rows), default=0.0)

    def mean(key):
        v = [float(r[key]) for r in rows if isinstance(r.get(key), (int, float))]
        return sum(v) / len(v) if v else None
    first, last = (rows[0], rows[-1]) if rows else ({}, {})
    m = mean("items.us_mean")
    return (f"items mean {m if m is None else round(m, 1)} us (worst 64 frames {most('items.us_mean'):.1f}), most "
            f"{most('items.us_max'):.0f} us; the whole tick most {most('tick_us_max'):.0f} us over the same frames; "
            f"{mean('items.calls')} game calls a frame; the longest frame {most('frame_ms_max'):.0f} ms, frames over "
            f"50 ms {int(last.get('frames_slow', 0)) - int(first.get('frames_slow', 0))}; slow item parts "
            f"{int(last.get('items.slow_parts', 0)) - int(first.get('items.slow_parts', 0))} (last: "
            f"{last.get('items.slow_part') or '-'}); slow game calls "
            f"{int(last.get('slow_calls', 0)) - int(first.get('slow_calls', 0))} (last: {last.get('slow_call') or '-'})")


def h11(c, spot, gic, rep) -> None:
    ensure_ghost(c, spot, gic)
    alone, items, rows_alone, rows_items, held_ok = [], [], [], [], True
    for _ in range(COST_PAIRS):
        set_items(c, NONE3, 0)
        time.sleep(1.5)
        f, rows = watched_fps(c, gic)
        alone.append(f)
        rows_alone += rows
        set_items(c, ITEMS, MASK)
        poll(c, lambda s: held_as(s, ITEMS, MASK) is None, 4.0)
        time.sleep(2.5)   # (64 frames held before the measurement: the items' timer has only frames that held them)
        f, rows = watched_fps(c, gic)
        items.append(f)
        rows_items += rows
        held_ok &= bool(rows) and all(held_as(r, ITEMS, MASK) is None for r in rows)
    shot(c, "H11_with_items")
    means = [float(r["items.us_mean"]) for r in rows_items if isinstance(r.get("items.us_mean"), (int, float))]
    us_mean = sum(means) / len(means) if means else None
    status, text = judge_pairs(alone, items, us_mean)
    if not held_ok:   # (run 4 measured on the Game Over screen, with no ghost: not a cost)
        status, text = "FAIL", "the items were not held in every sample while measuring; " + text
    rows_alone_t = [{"tick_us_max": r.get("tick_us_max"), "frame_ms_max": r.get("frame_ms_max"),
                     "frames_slow": r.get("frames_slow")} for r in rows_alone]
    rep.add("H11", status, text + f". With the items: {cost_text(rows_items)}. Alone: the whole tick most "
            f"{max((float(r.get('tick_us_max') or 0) for r in rows_alone_t), default=0):.0f} us, the longest frame "
            f"{max((float(r.get('frame_ms_max') or 0) for r in rows_alone_t), default=0):.0f} ms")
    print(f"  H11: alone {gic.fmt_fps(alone)}, with items {gic.fmt_fps(items)}, us_mean {us_mean}", flush=True)


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


def sprint(c) -> None:
    """Link sprints 2 s (B held, the stick forward): he puts everything on his back."""
    c.cmd("hold", "b")
    c.cmd("stick", "l", "0", "1", "2000")
    time.sleep(2.0)
    c.cmd("release", "all")
    time.sleep(0.8)


def close_windows(c) -> None:
    """A tutorial window (H12's bow brings "Attack with Bow") waits for A; then B for anything else."""
    for button in ("a", "b"):
        try:
            c.cmd("press", button)
        except ControlError:
            pass
        time.sleep(1.0)


def h13(c, spot, gic, rep, ctx: dict) -> None:
    rows: dict[str, dict] = {}
    ctx["link_rows"] = rows   # (H4L)
    time.sleep(3.0)
    close_windows(c)

    def sprint_():
        sprint(c)

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
        sprint_()
        c.cmd("ghost", "self")
        time.sleep(0.2)
        rows[f"{key}_back"] = c.cmd("ghost", "self")
        shot(c, f"H13_{key}_back")
    rep.add("H13", *judge_back_bytes(rows))
    gic.back_to(c, spot.p)


def h4l(c, spot, gic, rep) -> None:
    """H4L (0.6.2): Link sprints (everything on his back); his own back offsets from `ghost self` go to the mod
    (`ghost items rel`); the ghost (beside him, its back to the camera like his) puts its items there."""
    gic.back_to(c, spot.p)
    spot.look(c)
    sprint(c)
    time.sleep(0.5)
    c.cmd("ghost", "self")
    time.sleep(0.3)
    me = c.cmd("ghost", "self")
    names = tuple(me.get(k) or "" for k in ("weapon", "shield", "bow"))
    mask = me.get("on_back") if isinstance(me.get("on_back"), int) else 0
    sent = []
    for i, key in enumerate(("weapon", "shield", "bow")):
        m = rel_matrix(me.get(f"{key}_rel"))
        if names[i] and mask >> i & 1 and m:
            c.cmd("ghost", "items", "rel", str(i), *(f"{x:.5f}" for x in m))
            sent.append(key)
        else:
            c.cmd("ghost", "items", "rel", str(i), "-")
    p = link_pos(c)
    fx, fz = spot.f
    st = gst(c)
    if st.get("state") != "live":
        c.cmd("ghost", "spawn", "link")
        gic.wait_live(c, 15.0)
    yaw = math.degrees(math.atan2(fx, fz))   # (its back to the camera, as Link's)
    c.cmd("ghost", "pos", f"{p[0] + 1.2 * fz:.3f}", f"{p[1]:.3f}", f"{p[2] - 1.2 * fx:.3f}", f"{yaw:.1f}")
    c.cmd("ghost", "anim", "Wait")
    set_items(c, names, mask)
    idx = [("weapon", "shield", "bow").index(k) for k in sent]
    poll(c, lambda s: held_as(s, names, mask) is None and all(slots(s)[i]["copied"] is True for i in idx), 5.0)
    time.sleep(1.0)
    st = gst(c)
    shot(c, "H4L_like_link")
    rows = {f"{k}_back": me for k in ("weapon", "shield", "bow")}
    status, text = judge_like_link(st, rows, names)
    rep.add("H4L", status, f"Link's own offsets sent for {sent or 'nothing'} (on_back {mask}): " + text)
    for i in range(3):
        c.cmd("ghost", "items", "rel", str(i), "-")
    c.cmd("ghost", "anim", "auto")
    gic.back_to(c, spot.p)   # (the next check starts from the spot)
    spot.look(c)


def net_back(c, steps: list) -> None:
    """H15 (0.6.2): after a sprint, Link's own back offsets went into the state he sends; the receiving client passed
    them to the mod (`ghost items rel`): the ghost's items must sit where his do (H4L's judge), within E2E_IN + 3 s."""
    t0, why = time.monotonic(), "no reply"
    while time.monotonic() - t0 < E2E_IN + 3:
        c.cmd("ghost", "self")
        me = c.cmd("ghost", "self")
        st = gst(c)
        names = tuple(me.get(k) or "" for k in ("weapon", "shield", "bow"))
        mask = me.get("on_back") if isinstance(me.get("on_back"), int) else 0
        copied = all(slots(st)[i]["copied"] is True for i in range(3) if names[i] and mask >> i & 1)
        status, text = judge_like_link(st, {f"{k}_back": me for k in ("weapon", "shield", "bow")}, names)
        why = f"copied {copied}: {text}"
        if copied and status == "PASS":
            steps.append(("back like Link's", time.monotonic() - t0, None))
            print(f"  H15: back like Link's after {time.monotonic() - t0:.2f} s: {text}", flush=True)
            shot(c, "H15_back_like_link")
            return
        time.sleep(0.2)
    steps.append(("back like Link's", None, why))
    shot(c, "H15_back_like_link")


def h15(c, port, spot, gic, rep, relay_port: int) -> None:
    """A real sender: client A (sync, shows nobody) reads this Link with `ghost self` and sends him through a relay,
    shifted 1.5 m to the side; client B (--ghost-items, shows A) drives the ghost. Link sprints, draws (Y), throws his
    weapon (R: he drops it) and takes a new sword; after each, the ghost must show what `ghost self` reads on him."""
    py = [sys.executable, "-u"]
    relay = subprocess.Popen(py + [str(gic.REPO / "tools" / "mp" / "relay.py"), "--port", str(relay_port)])
    time.sleep(1.0)
    c.cmd("ghost", "despawn")
    c.cmd("ghost", "items", "off")
    fx, fz = spot.f
    side = (1.5 * fz, -1.5 * fx)   # (to the camera's left)
    sender = client.Runner(("127.0.0.1", relay_port), "Sender", game_port=port, log=lambda s: None,
                           ghost=client.Ghost(sync=True, peer="Nobody at all", log=lambda s: None))
    orig = sender.session.update_local

    def shifted(pos, cam, now, own=None):
        if isinstance(pos, dict) and isinstance(pos.get("x"), (int, float)):
            pos = dict(pos, x=pos["x"] + side[0], z=pos["z"] + side[1])
        return orig(pos, cam, now, own)
    sender.session.update_local = shifted
    receiver = client.Runner(("127.0.0.1", relay_port), "Check", game_port=port, log=lambda s: None,
                             ghost=client.Ghost(actor=client.GHOST_LINK_ACTOR, items=True, peer="Sender",
                                                log=lambda s: print("  " + s)))
    threads = [threading.Thread(target=lambda r=r: asyncio.run(r.run()), daemon=True) for r in (sender, receiver)]
    for th in threads:
        th.start()
    steps: list[tuple[str, float | None, str | None]] = []

    def follow(what: str) -> None:
        """Wait until the ghost shows what Link holds now (asked again each time: he may still change)."""
        t0, why, me = time.monotonic(), "no reply", {}
        while time.monotonic() - t0 < E2E_IN + 3:
            c.cmd("ghost", "self")
            me = c.cmd("ghost", "self")
            why = link_shows(gst(c), me)
            if why is None:
                steps.append((what, time.monotonic() - t0, None))
                print(f"  H15: {what}: shown after {time.monotonic() - t0:.2f} s ({me.get('weapon')!r} "
                      f"{me.get('shield')!r} {me.get('bow')!r} on_back {me.get('on_back')})", flush=True)
                shot(c, f"H15_{what.replace(' ', '_')}")
                return
            time.sleep(0.1)
        steps.append((what, None, f"{why}; Link: {me.get('weapon')!r} {me.get('shield')!r} {me.get('bow')!r} "
                                  f"on_back {me.get('on_back')}"))
        shot(c, f"H15_{what.replace(' ', '_')}")
    def home() -> None:   # (0.6.2: back on the meadow before each sprint: two in a row ran off the plateau's edge)
        gic.back_to(c, spot.p)
        spot.look(c)

    try:
        home()
        gic.wait_live(c, 20.0)
        sprint(c)
        follow("sprint")
        net_back(c, steps)
        home()
        c.cmd("press", "y")
        time.sleep(0.6)
        follow("draw")
        sprint(c)
        follow("sprint again")
        home()
        try:
            c.cmd("press", "y")      # (draw first: a throw needs the weapon out)
            time.sleep(1.2)
            c.cmd("hold", "r")       # (R held, then let go: he throws it; it lands in front of him)
            time.sleep(1.2)
            c.cmd("release", "all")
            me, end = {}, time.monotonic() + 4.0
            while time.monotonic() < end:   # (the throw takes a moment)
                c.cmd("ghost", "self")
                time.sleep(0.2)
                me = c.cmd("ghost", "self")
                if not me.get("weapon"):
                    break
            if me.get("weapon"):
                shot(c, "H15_no_throw")
                steps.append(("throw", None, f"Link still holds {me.get('weapon')!r} (the throw didn't happen)"))
            else:
                follow("throw")
        except ControlError as e:
            steps.append(("throw", None, f"R: {e}"))
        took = take_given(c, spot, gic, ITEMS[0], "weapon")
        if took.get("weapon") == ITEMS[0]:
            follow("new sword")
        else:
            steps.append(("new sword", None, f"Link didn't take it ({took.get('weapon')!r})"))
    finally:
        for r in (receiver, sender):
            r.stop()
        for th in threads:
            th.join(10)
        relay.terminate()
    rep.add("H15", *judge_sender(steps))


def run(c: Control, port: int, gic, rep, *, give: bool) -> None:
    """Every check in the plan's order (out/research/ghost-items-plan.md): L2, H1-H4, H5 (unarmed), H6-H9, H11, H10,
    H12, H13, H4L, H16, H5 again (armed), H15. H14 is judged by the caller after the game closed."""
    spot = Spot(c, gic)
    c.cmd("ghost", "self")
    time.sleep(0.3)
    me = c.cmd("ghost", "self")
    armed = bool(me.get("weapon") or me.get("shield") or me.get("bow"))
    print(f"  Link has {me.get('weapon')!r} {me.get('shield')!r} {me.get('bow')!r}", flush=True)
    ctx: dict = {}
    guard(rep, "H1", h1, c, port, spot, gic, rep, ctx)
    guard(rep, "H2", h2, c, spot, gic, rep)
    guard(rep, "H3", h3, c, spot, gic, rep)
    guard(rep, "H4", h4, c, spot, gic, rep, ctx)
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
    guard(rep, "H13", h13, c, spot, gic, rep, ctx)
    if ctx.get("ghost_back") and ctx.get("link_rows"):
        status, text = judge_like_link(ctx["ghost_back"], ctx["link_rows"])
        rep.add("H4P", status, "the items' own offsets (no copy): " + text)
    else:
        rep.add("H4P", "SKIP", "H4 or H13 gave nothing to compare")
    guard(rep, "H4L", h4l, c, spot, gic, rep)
    rep.add("H16", *judge_size(ctx.get("sizes", []), ctx.get("link_rows")))
    me = c.cmd("ghost", "self")
    if me.get("weapon") or me.get("shield") or me.get("bow"):
        guard(rep, "H5", h5, c, spot, gic, rep, "armed")
    if give and (me.get("weapon") or me.get("shield") or me.get("bow")):
        guard(rep, "H15", h15, c, port, spot, gic, rep, gic.RELAY_PORT)
    else:
        rep.add("H15", "SKIP", "Link has no items (needs --give-weapon)")
    c.cmd("ghost", "items", "off")
    c.cmd("ghost", "despawn")
