"""Walk/run sync over Wi-Fi: the speed each client sends (tools/mp), and what the ghost gets from it.

Tested on two laptops over home Wi-Fi on 2026-11-07, the ghost kept its position in sync but mostly stood while the
other player ran: the mod worked the speed out from positions that came unevenly and threw too many of them away.
Now each client measures its own speed (from its own positions and clock) and sends it; the receiving client passes
it to the mod with `ghost pos`. These tests run that chain with made-up positions and made-up Wi-Fi: random delays
of 0-150 ms and stalls of 300-600 ms every few seconds. No game, no tkinter; loopback sockets in the last test only.

The Wait/Walk/Run thresholds are read from native/examples/ghost/ghost.c, so the check below picks what the mod
would (without its 0.25 s hold and 0.1 s smoothing, which only make it steadier).
"""
import asyncio
import math
import random
import re
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import mp  # noqa: E402
from mp import bot, client, relay  # noqa: E402

POS = {"x": 0.0, "y": 50.0, "z": 0.0, "stamina": 1.0, "place": "swr:1"}   # the receiving player (made up)
CAM = {"yaw": 0.0}
# (seconds, m/s): stand, walk, run, walk, stand, run, stand
PHASES = ((3.0, 0.0), (8.0, 2.0), (15.0, 6.5), (6.0, 2.0), (5.0, 0.0), (10.0, 6.5), (3.0, 0.0))
RADIUS = 40.0          # m: the made-up player goes round a circle this big
SETTLE = 1.5           # s after a change of speed before it must show (network, jitter buffer, speed window)


def quiet(*_a, **_k):
    pass


def ghost_thresholds() -> dict:
    """The ghost mod's Wait/Walk/Run thresholds (m/s), read from its source."""
    text = (REPO / "native" / "examples" / "ghost" / "ghost.c").read_text(encoding="utf-8")
    names = ("ANIM_WALK_FROM", "ANIM_WAIT_BELOW", "ANIM_RUN_FROM", "ANIM_RUN_BELOW")
    return {n: float(re.search(rf"#define {n} ([0-9.]+)f?\b", text).group(1)) for n in names}


WAIT, WALK, RUN = "Wait", "Walk", "Run"


def anim_pick(cur: str | None, v: float, th: dict) -> str:
    """ghost.c's anim_pick: the choice for speed v, given the current one (hysteresis)."""
    if cur == WALK:
        return RUN if v >= th["ANIM_RUN_FROM"] else WAIT if v < th["ANIM_WAIT_BELOW"] else WALK
    if cur == RUN:
        return WAIT if v < th["ANIM_WAIT_BELOW"] else WALK if v < th["ANIM_RUN_BELOW"] else RUN
    return RUN if v >= th["ANIM_RUN_FROM"] else WALK if v >= th["ANIM_WALK_FROM"] else WAIT


def expected(speed: float) -> str:
    return RUN if speed >= 5 else WALK if speed > 0 else WAIT


class Truth:
    """Where the made-up player is: round a circle, at the speed of each phase."""

    def __init__(self, phases=PHASES):
        self.phases, self.starts, t, s = phases, [], 0.0, 0.0
        for dur, v in phases:
            self.starts.append((t, s))
            t, s = t + dur, s + dur * v
        self.duration = t

    def at(self, t: float) -> tuple[float, float]:
        """(arc length in m, speed in m/s) at time t."""
        for (t0, s0), (dur, v) in zip(self.starts, self.phases):
            if t < t0 + dur:
                return s0 + (max(t, t0) - t0) * v, v
        t0, s0 = self.starts[-1]
        return s0 + self.phases[-1][0] * self.phases[-1][1], 0.0

    def pos(self, t: float) -> dict:
        s, _ = self.at(t)
        return {"x": 100.0 + RADIUS * math.sin(s / RADIUS), "y": 50.0, "z": -200.0 + RADIUS * math.cos(s / RADIUS),
                "stamina": 1.0, "place": "swr:1"}

    def steady(self, settle: float = SETTLE):
        """(from, to, speed) of each phase, without its first `settle` seconds."""
        for (t0, _), (dur, v) in zip(self.starts, self.phases):
            if dur > settle:
                yield t0 + settle, t0 + dur, v


def wifi_stalls(rng: random.Random, duration: float) -> list[tuple[float, float]]:
    """Stalls of 0.3-0.6 s, 2-5 s apart: nothing gets through, then everything at once (TCP keeps the order)."""
    out, t = [], 2.0
    while t < duration:
        d = rng.uniform(0.3, 0.6)
        out.append((t, t + d))
        t += d + rng.uniform(2.0, 5.0)
    return out


def wifi_run(seed: int = 1, *, sender_speed: bool = True, takes_speed: bool = True, jitter: float = 0.15,
             stalls: bool = True, bursts: tuple[float, float] | None = None, hz: float = 20.0, fps: float = 55.0,
             phases=PHASES) -> dict:
    """Two clients and the relay's cleaning over made-up Wi-Fi, on a made-up clock (no sockets, no sleeping).

    The sender (client.Session, as the real client) reads its made-up position 20 times a second and sends the state;
    the relay keeps the known fields (mp.clean_state); each line gets 5 ms plus 0-`jitter` s on the way, in order,
    and nothing gets through during a stall. The receiver (client.Session and client.Ghost, as the real client with
    --ghost) takes every line when it arrives and, 20 times a second, makes its `ghost pos`; that reaches the game in
    its next frame (`fps` frames a second). bursts (lo, hi): on top, lines only get through every lo-hi s, all that
    waited at once (a laptop's Wi-Fi power saving does that). sender_speed False: an older sender (no "speed").
    takes_speed False: an older mod (no speed in `ghost pos`).

    Returns sent (the states), cmds (game time, x, y, z, yaw, speed or None), frames (the game's frame times),
    stalls, truth (Truth).
    """
    rng = random.Random(seed)
    truth = Truth(phases)
    sender = client.Session("Bo")
    sent, lines = [], []
    k = 0
    while True:
        ts = k / hz + rng.uniform(0.0, 0.004)
        if ts > truth.duration:
            break
        msg = sender.update_local(truth.pos(ts), {"yaw": 0.0}, ts)
        if not sender_speed:
            msg.pop("speed")
        sent.append(msg)
        st = mp.clean_state(msg)                                   # (all the relay changes)
        lines.append(dict(st, type="state", id=2, name="Bo"))
        k += 1
    stall_list = wifi_stalls(rng, truth.duration) if stalls else []
    ticks, b = [], 0.0
    while bursts and b < truth.duration + 2.0:
        ticks.append(b)
        b += rng.uniform(*bursts)
    arrivals, prev = [], 0.0
    for msg in lines:
        a = msg["t"] + 0.005 + rng.uniform(0.0, jitter)
        for s0, s1 in stall_list:
            if s0 <= a < s1:
                a = s1
        if ticks:
            a = next((tk for tk in ticks if tk >= a), a)
        a = max(a, prev)
        arrivals.append(a)
        prev = a
    frames, f = [], 0.0
    while f < truth.duration + 1.0:
        frames.append(f)
        f += (1.0 / fps) * rng.uniform(0.9, 1.1)
    receiver = client.Session("Me")
    receiver.handle({"type": "welcome", "id": 1, "peers": []}, 0.0)
    ghost = client.Ghost(log=quiet)
    cmds, li, fi = [], 0, 0
    j = 0
    while True:
        now = 0.0137 + j / hz + rng.uniform(0.0, 0.008)
        if now > truth.duration + 0.5:
            break
        while li < len(lines) and arrivals[li] <= now:
            receiver.handle(lines[li], arrivals[li])
            li += 1
        receiver.update_local(POS, CAM, now)
        reqs = ghost.requests(receiver, now, 1)
        ghost.replies([{"state": "live", "takes_speed": takes_speed} if r == ["ghost"] else {} for r in reqs])
        while fi < len(frames) and frames[fi] < now + 0.002:      # (the batch is run in the game's next frame)
            fi += 1
        at = frames[fi] - 0.0005 if fi < len(frames) else now + 0.02
        for r in reqs:
            if r[1:2] == ["pos"]:
                cmds.append((at, *map(float, r[2:6]), float(r[6]) if len(r) == 7 else None))
        j += 1
    return {"sent": sent, "cmds": cmds, "frames": frames, "stalls": stall_list, "truth": truth}


def choices(cmds, th) -> list[str]:
    """What the mod would pick at each `ghost pos`, from the speed it carries (None: no speed in it)."""
    out, cur = [], None
    for c in cmds:
        if c[5] is not None:
            cur = anim_pick(cur, c[5], th)
        out.append(cur)
    return out


def held_steps(cmds, truth) -> int:
    """`ghost pos` steps that didn't move while the player ran: the jitter buffer held them (a stall or a late line)."""
    n = 0
    for a, b in zip(cmds, cmds[1:]):
        if truth.at(b[0] - 0.3)[1] > 5 and math.dist(a[1:4], b[1:4]) < 1e-6:
            n += 1
    return n


class SpeedMeterTests(unittest.TestCase):
    def test_steady_speeds(self):
        for v in (0.0, 1.5, 6.5, 15.0):
            m = client.SpeedMeter()
            for i in range(40):
                got = m.update(v * i * 0.05, 0.0, i * 0.05)
            self.assertAlmostEqual(got, v, places=6)

    def test_uneven_times_dont_matter(self):
        rng = random.Random(3)
        m, t = client.SpeedMeter(), 0.0
        for _ in range(200):
            t += rng.uniform(0.01, 0.2)                    # (polls 10 to 200 ms apart: the times are the sender's)
            got = m.update(3.0 * t, 4.0 * t, t)
        self.assertAlmostEqual(got, 5.0, places=6)

    def test_window_and_first_samples(self):
        m = client.SpeedMeter()
        self.assertEqual(m.update(0.0, 0.0, 10.0), 0.0)
        self.assertEqual(m.update(0.3, 0.0, 10.05), 0.0)   # 0.05 s measured: not yet
        self.assertAlmostEqual(m.update(0.6, 0.0, 10.10), 6.0)
        for i in range(3, 20):                              # it stops at x = 1.8 (t 10.15)
            m.update(min(0.3 * i, 1.8), 0.0, 10.0 + 0.05 * i)
        self.assertAlmostEqual(m.speed, 0.0)                # (0.4 s after it stopped: standing)

    def test_jumps_and_gaps_start_again(self):
        m = client.SpeedMeter()
        for i in range(10):
            m.update(0.1 * i, 0.0, 0.05 * i)                # 2 m/s
        self.assertAlmostEqual(m.speed, 2.0)
        m.update(3.9, 0.0, 0.5)                             # 3 m in 0.05 s: a short teleport, not 60 m/s
        self.assertAlmostEqual(m.speed, 2.0)                # (the last speed stays until 0.1 s is measured)
        m.update(3.9, 0.0, 0.55)
        self.assertAlmostEqual(m.update(3.9, 0.0, 0.6), 0.0)    # standing there
        m.update(50.0, 0.0, 0.65)                           # 46 m: a teleport
        self.assertEqual(len(m.samples), 1)
        m.update(50.0, 0.0, 5.0)                            # 4 s with no position (a loading screen)
        self.assertEqual(len(m.samples), 1)
        m.update(50.0, 0.0, 4.0)                            # the clock went back (a restart)
        self.assertEqual(len(m.samples), 1)
        self.assertEqual(m.update(50.0, 0.0, 4.0), m.speed)     # (the same time again: nothing changes)


def sender_speeds(seed: int, model: str, *, hitch=(0.2, 0.5), every=(3.0, 6.0), v: float = 6.5, fps: float = 55.0,
                  stop_at: float | None = None, frames=None, duration: float = 30.0) -> list[tuple[float, float]]:
    """The speed the sender sends (client.Session, as the real client) while its own game hitches: one frame of
    `hitch` s every `every` s. The client asks 20 times a second and a reply comes in the game's next frame (its
    clock stamps it). model "frozen": the game's clock stops during a hitch (Link moves one frame's worth);
    "catchup": Link moves on with the wall clock. Link stands 1 s, then runs at v (until game time stop_at).
    Returns (client time, speed sent)."""
    rng = random.Random(seed)
    if frames is None:
        frames, f, nxt = [0.0], 0.0, 2.5
        while f < duration + 1:
            if hitch and f >= nxt:
                f += rng.uniform(*hitch)
                nxt = f + rng.uniform(*every)
            else:
                f += 1.0 / fps
            frames.append(f)

    def x_at(t):
        t = min(t, stop_at) if stop_at is not None else t
        return v * max(0.0, t - 1.0)
    sender = client.Session("Bo")
    out, now, k = [], 0.0, 0
    while now < duration:
        while k < len(frames) and frames[k] < now:
            k += 1
        if k >= len(frames):
            break
        t = frames[k] + 0.001
        msg = sender.update_local(dict(POS, x=x_at(k / fps if model == "frozen" else frames[k])), CAM, t)
        out.append((t, msg["speed"]))
        now = max(now + 0.05, t)
    return out


class SenderHitchTests(unittest.TestCase):
    """Review 2026-11-07: a frame hitch in the sender's own game (a screenshot, an autosave) made the sent speed dip
    (to 0.24 m/s at 6.5 m/s) when the game's clock stops during it, and the ghost went Run, Walk, Run."""

    def test_no_dip_while_running(self):
        th = ghost_thresholds()
        for model in ("frozen", "catchup"):
            for seed in (1, 2, 3, 4, 5):
                speeds = [s for t, s in sender_speeds(seed, model) if 2.0 <= t <= 29.0]
                with self.subTest(model=model, seed=seed):
                    self.assertGreater(min(speeds), 6.0)
                    self.assertGreaterEqual(min(speeds), th["ANIM_RUN_BELOW"])

    def test_a_stop_still_shows(self):
        # a 0.4 s hitch at 5 s, and Link stops right then: 0 within a second (the last speed stays 0.4 s after it)
        frames = [k / 55.0 for k in range(276)] + [5.4 + k / 55.0 for k in range(1, 300)]
        got = sender_speeds(1, "catchup", frames=frames, stop_at=5.0, duration=9.0)
        self.assertGreater(min(s for t, s in got if 2.0 <= t < 5.0), 6.0)
        self.assertLess(max(s for t, s in got if t >= 6.0), 0.3)

    def test_a_slow_game_is_still_measured(self):
        # 55 frames a second, then 5 from 5 s on: the first slow steps look like hitches, then they are the usual
        frames = [k / 55.0 for k in range(276)]
        frames += [frames[-1] + 0.2 * k for k in range(1, 30)]
        got = sender_speeds(1, "catchup", frames=frames, duration=10.0)
        self.assertGreater(min(s for t, s in got if 2.0 <= t), 6.0)
        self.assertAlmostEqual(got[-1][1], 6.5, delta=0.05)

    def test_hitches_back_to_back(self):
        # a 0.3 s hitch after every 6 frames (the window never fills again): a stop still shows within 2 s
        frames, f = [], 0.0
        while f < 16:
            for _ in range(6):
                frames.append(f)
                f += 0.05
            f += 0.3
        m = client.SpeedMeter()
        with mock.patch.object(client, "SpeedMeter", lambda: m):
            got = sender_speeds(1, "catchup", frames=frames, stop_at=10.0, duration=15.0)
        self.assertGreater(m.hitches, 20)                   # (they were hitches: one every 0.6 s for 15 s)
        self.assertGreater(min(s for t, s in got if 3.0 <= t < 10.0), 5.5)
        self.assertLess(max(s for t, s in got if t >= 12.0), 0.3)


def attack_run(seed: int, *, bursts=None, hold: float = 0.3, duration: float = 60.0) -> tuple[int, int, int]:
    """Quick attacks over the Wi-Fi model (wifi_stalls; bursts as in wifi_run): the sender's name is CutS1 for `hold`
    s every 1.2-2.2 s, Wait otherwise (as --ghost-sync sends it). The receiver is the real Session, JitterBuffer and
    Ghost with sync. Returns (attacks, attacks that reached the mod in a `ghost pos` within 1 s of their end,
    `ghost pos` that carried CutS1)."""
    rng = random.Random(seed)
    attacks, t = [], 1.0
    while t < duration - 2:
        attacks.append((t, t + hold))
        t += rng.uniform(1.2, 2.2)
    sender, lines, k = client.Session("Bo"), [], 0
    while (ts := k / 20.0 + rng.uniform(0.0, 0.004)) <= duration:
        cut = any(a <= ts < b for a, b in attacks)
        own = {"anim": "CutS1", "attack": True} if cut else {"anim": "Wait"}
        msg = sender.update_local(dict(POS, x=0.0), CAM, ts, own)
        lines.append(dict(mp.clean_state(msg), type="state", id=2, name="Bo"))
        k += 1
    stalls = wifi_stalls(rng, duration)
    ticks, b = [], 0.0
    while bursts and b < duration + 2:
        ticks.append(b)
        b += rng.uniform(*bursts)
    arrivals, prev = [], 0.0
    for m in lines:
        a = m["t"] + 0.005 + rng.uniform(0.0, 0.15)
        for s0, s1 in stalls:
            if s0 <= a < s1:
                a = s1
        if ticks:
            a = next((tk for tk in ticks if tk >= a), a)
        a = max(a, prev)
        arrivals.append(a)
        prev = a
    recv = client.Session("Me")
    recv.handle({"type": "welcome", "id": 1, "peers": []}, 0.0)
    g = client.Ghost(log=quiet, sync=True)
    got, li, j = [], 0, 0
    while (now := 0.0137 + j / 20.0 + rng.uniform(0, 0.008)) <= duration + 0.5:
        while li < len(lines) and arrivals[li] <= now:
            recv.handle(lines[li], arrivals[li])
            li += 1
        recv.update_local(POS, CAM, now)
        reqs = g.requests(recv, now, 1)
        g.replies([{"state": "live", "takes_speed": True, "takes_anim": True, "has_self": True}
                   if r == ["ghost"] else {} for r in reqs])
        got += [(now, r[7] if len(r) == 8 else "") for r in reqs if r[1:2] == ["pos"]]
        j += 1
    reached = sum(1 for a0, a1 in attacks if any(a0 <= t <= a1 + 1.0 and n == "CutS1" for t, n in got))
    return len(attacks), reached, sum(n == "CutS1" for _, n in got)


class AttackStallTests(unittest.TestCase):
    """Review 2026-11-07: a quick attack during a Wi-Fi stall was lost (5 to 7 % of 0.3 s attacks): after the stall
    the jitter buffer showed the moment 0.1 s behind the newest line and skipped the ones in between."""

    def test_attacks_survive_stalls(self):
        for seed, bursts in ((1, None), (2, None), (3, None), (4, (0.1, 0.3)), (5, (0.1, 0.3))):
            with self.subTest(seed=seed, bursts=bursts):
                with mock.patch.object(client, "LATE_ATTACK_SHOW", 0.0):     # (as before the fix)
                    n, old_reached, old_cuts = attack_run(seed, bursts=bursts)
                n2, reached, cuts = attack_run(seed, bursts=bursts)
                self.assertEqual(n, n2)
                self.assertEqual(reached, n)                                # every attack reaches the mod
                lost = n - old_reached
                # (only the lost ones are added, about 0.3 s each at 20 a second: the others are not made longer)
                self.assertLessEqual(cuts - old_cuts, 7 * lost)

    def test_the_model_does_lose_attacks_without_it(self):
        lost = 0
        for seed in (1, 2, 3):
            with mock.patch.object(client, "LATE_ATTACK_SHOW", 0.0):
                n, reached, _ = attack_run(seed)
            lost += n - reached
        self.assertGreater(lost, 0)


class ProtocolSpeedTests(unittest.TestCase):
    def test_clean_state_keeps_a_good_speed_only(self):
        base = {"t": 1.0, "x": 1.0, "y": 2.0, "z": 3.0}
        self.assertEqual(mp.clean_state(dict(base, speed=6.25))["speed"], 6.25)
        self.assertEqual(mp.clean_state(dict(base, speed=0))["speed"], 0.0)
        for bad in (-1.0, float("nan"), float("inf"), 2e3, "6", True, None):
            st = mp.clean_state(dict(base, speed=bad))
            self.assertIsNotNone(st, bad)                   # (a bad speed costs the speed, not the position)
            self.assertNotIn("speed", st)

    def test_sender_sends_its_own_speed(self):
        s = client.Session("Me")
        for i in range(10):
            msg = s.update_local(dict(POS, x=0.3 * i), CAM, 100.0 + 0.05 * i)
        self.assertEqual(msg["speed"], 6.0)
        self.assertEqual(msg["type"], "state")

    def test_receiver_works_it_out_for_an_older_sender(self):
        jb = client.JitterBuffer(delay=0.1)
        rng = random.Random(5)
        for i in range(30):                                 # 4 m/s, arriving 0-150 ms late: the t's are what count
            t = 0.05 * i
            st = {"t": t, "x": 4.0 * t, "y": 0.0, "z": 0.0, "yaw": None, "stamina": None}
            jb.push(st, 50.0 + t + rng.uniform(0, 0.15))
        self.assertAlmostEqual(jb.latest()["speed"], 4.0)
        jb.push({"t": 1.5, "x": 6.0, "y": 0.0, "z": 0.0, "yaw": None, "stamina": None, "speed": 9.0}, 51.6)
        self.assertEqual(jb.latest()["speed"], 9.0)         # (a sent speed wins)

    def test_held_player_keeps_the_speed_for_a_second(self):
        jb = client.JitterBuffer(delay=0.1)
        for i in range(10):
            t = 0.05 * i
            jb.push({"t": t, "x": 6.0 * t, "y": 0.0, "z": 0.0, "yaw": None, "stamina": None, "speed": 6.0}, 10.0 + t)
        self.assertEqual(jb.sample(10.0 + 0.3)["speed"], 6.0)   # blended: 6
        self.assertEqual(jb.sample(10.0 + 0.45 + 0.1 + 0.5)["speed"], 6.0)   # held 0.5 s (a stall): still running
        self.assertEqual(jb.sample(10.0 + 0.45 + 0.1 + 1.2)["speed"], 0.0)   # no news for over 1 s: standing
        self.assertEqual(jb.sample(10.0 + 0.45 + 0.1 + 1.2)["x"], 2.7)       # (still held where it was)


class WifiSimTests(unittest.TestCase):
    """The chain on a made-up clock: sender, relay cleaning, Wi-Fi, receiver, `ghost pos`."""

    @classmethod
    def setUpClass(cls):
        cls.th = ghost_thresholds()

    def check_run(self, run, *, label):
        truth, cmds = run["truth"], run["cmds"]
        picks = choices(cmds, self.th)
        for t0, t1, v in truth.steady():
            rows = [(c, p) for c, p in zip(cmds, picks) if t0 <= c[0] < t1]
            want = expected(v)
            good = sum(p == want for _, p in rows)
            errs = [abs(c[5] - v) for c, _ in rows]
            with self.subTest(label=label, phase=(round(t0, 1), v)):
                self.assertGreater(len(rows), 10)
                self.assertEqual(good, len(rows), f"{want} at {good} of {len(rows)} `ghost pos`")
                self.assertLess(max(errs), 0.25 if v else 0.01)   # (the speed the mod gets: within 0.25 m/s)

    def test_sender_speed_is_right(self):
        run = wifi_run(1)
        truth = run["truth"]
        for t0, t1, v in truth.steady(settle=0.5):
            speeds = [m["speed"] for m in run["sent"] if t0 <= m["t"] < t1]
            self.assertTrue(speeds)
            self.assertLess(max(abs(s - v) for s in speeds), 0.02, v)   # (a circle: the chord is a hair shorter)

    def test_wifi_jitter_and_stalls_ghost_picks_run_while_running(self):
        for seed in (1, 2, 3, 4, 5):
            run = wifi_run(seed)
            self.assertGreaterEqual(len(run["stalls"]), 8)
            self.assertGreater(held_steps(run["cmds"], run["truth"]), 20)   # (the positions did stall and jump)
            self.assertTrue(all(c[5] is not None for c in run["cmds"][1:]))
            self.check_run(run, label=f"seed {seed}")

    def test_older_sender_without_speed(self):
        for seed in (6, 7):
            self.check_run(wifi_run(seed, sender_speed=False), label=f"older sender, seed {seed}")

    def test_older_mod_gets_four_numbers(self):
        run = wifi_run(8, takes_speed=False, phases=((2.0, 0.0), (3.0, 6.5)))
        self.assertTrue(run["cmds"])
        self.assertTrue(all(c[5] is None for c in run["cmds"]))

    def test_no_jitter_no_stalls(self):
        self.check_run(wifi_run(9, jitter=0.0, stalls=False), label="clean network")

    def test_power_saving_bursts(self):
        for seed in (10, 11):
            self.check_run(wifi_run(seed, bursts=(0.1, 0.3)), label=f"bursts, seed {seed}")


class GhostSpeedArgTests(unittest.TestCase):
    def session(self, speed=6.0):
        s = client.Session("Me", delay=0.0)
        s.handle({"type": "welcome", "id": 9, "peers": []}, now=0.0)
        s.handle({"type": "state", "id": 1, "name": "Bo", "t": 1.0, "x": 103.0, "y": 51.0, "z": -196.0, "yaw": 0.0,
                  "speed": speed}, now=10.0)
        s.update_local(POS, CAM, now=10.0)
        return s

    def test_speed_only_once_the_mod_says_it_takes_it(self):
        g, s = client.Ghost(log=quiet), self.session()
        reqs = g.requests(s, 10.0, 1)
        self.assertEqual(reqs[1], ["ghost", "pos", "103.000", "51.000", "-196.000", "0.0"])   # (not known yet)
        self.assertEqual(reqs[2], ["ghost"])
        g.replies([{}, {}, {"state": "live", "takes_speed": True}])
        self.assertEqual(g.requests(s, 10.05, 1), [["ghost", "pos", "103.000", "51.000", "-196.000", "0.0", "6.00"]])
        g.replies([{}])
        reqs = g.requests(s, 10.1, 2)                     # a new game connection: maybe an older mod
        self.assertEqual([r for r in reqs if r[1:2] == ["pos"]][0][-1], "0.0")
        self.assertIn(["ghost"], reqs)                    # (asked at once)
        g.replies([{} if r != ["ghost"] else {"state": "live"} for r in reqs])   # an older mod: no takes_speed
        self.assertEqual(len(g.requests(s, 10.15, 2)[0]), 6)

    def test_usage_error_drops_the_speed(self):
        g, s = client.Ghost(log=quiet), self.session()
        g.takes_speed, g._conn, g.spawned = True, 1, True
        g._next_status = 99.0
        reqs = g.requests(s, 10.0, 1)
        self.assertEqual(len(reqs[0]), 7)
        g.replies([client.ControlError("usage: ghost [spawn [name] [dx dy dz] | pos x y z [yaw] | ...]")])
        self.assertEqual(len(g.requests(s, 10.05, 1)[0]), 6)


class WifiSocketTests(unittest.IsolatedAsyncioTestCase):
    """The real relay and bot on loopback, with Wi-Fi-like delays and one stall between the relay and the receiver."""

    async def test_bot_speed_through_relay_and_jitter(self):
        rly = relay.Relay("127.0.0.1", 0, log=quiet)
        _, port = await rly.start()
        rng = random.Random(11)
        stall = {"at": None}

        async def proxy(reader, writer):          # receiver <-> relay; relay-to-receiver lines are delayed
            up_r, up_w = await asyncio.open_connection("127.0.0.1", port)
            q: asyncio.Queue = asyncio.Queue()

            async def up():
                while line := await reader.readline():
                    up_w.write(line)
                up_w.close()

            async def down_read():
                last = 0.0
                while line := await up_r.readline():
                    now = time.monotonic()
                    due = max(last, now + rng.uniform(0.0, 0.15))
                    if stall["at"] is not None and stall["at"] <= due < stall["at"] + 0.5:
                        due = stall["at"] + 0.5
                    last = due
                    q.put_nowait((due, line))
                q.put_nowait((0.0, b""))

            async def down_write():
                while True:
                    due, line = await q.get()
                    if not line:
                        break
                    await asyncio.sleep(max(0.0, due - time.monotonic()))
                    writer.write(line)
                writer.close()

            await asyncio.gather(up(), down_read(), down_write(), return_exceptions=True)

        px = await asyncio.start_server(proxy, "127.0.0.1", 0)
        px_port = px.sockets[0].getsockname()[1]
        session = client.Session("Me")
        got = []

        def on_message(m):
            session.handle(m, time.monotonic())
            if m.get("type") == "state":
                got.append(m)

        stop = asyncio.Event()
        link = client.RelayLink("127.0.0.1", px_port, "Me", on_message=on_message, log=quiet)
        tasks = [asyncio.create_task(link.run(stop)),
                 asyncio.create_task(bot.run_bot(("127.0.0.1", port), "bot", path=bot.CirclePath(10, 6),
                                                 center=(0.0, 0.0, 0.0), stop=stop, log=quiet))]
        ghost = client.Ghost(log=quiet)
        cmds = []
        try:
            start = time.monotonic()
            # 3.5 s, or longer (up to 15 s) until enough came: on a busy PC (a game running) the loops run slower, and
            # a fixed 3.5 s once gave only 13-16 states after the first 10
            while (time.monotonic() - start < 3.5
                   or (time.monotonic() - start < 15.0 and (len(got) < 35 or len(cmds) < 35))):
                now = time.monotonic()
                if stall["at"] is None and now - start > 1.5:
                    stall["at"] = now                     # (one 0.5 s stall)
                reqs = ghost.requests(session, now, 1)
                ghost.replies([{"state": "live", "takes_speed": True} if r == ["ghost"] else {} for r in reqs])
                if now - start > 1.0:
                    cmds += [r for r in reqs if r[1:2] == ["pos"]]
                await asyncio.sleep(0.05)
        finally:
            stop.set()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5.0)
            px.close()
            await rly.close()
        speeds = [m["speed"] for m in got[10:]]
        self.assertGreater(len(speeds), 20)
        self.assertTrue(all(abs(v - 6.0) < 0.1 for v in speeds), speeds[:5])   # (the relay passed it on)
        pos_speeds = [float(r[6]) for r in cmds if len(r) == 7]
        self.assertGreater(len(pos_speeds), 30)
        th = ghost_thresholds()
        self.assertTrue(all(v >= th["ANIM_RUN_FROM"] for v in pos_speeds), pos_speeds)


if __name__ == "__main__":
    unittest.main()
