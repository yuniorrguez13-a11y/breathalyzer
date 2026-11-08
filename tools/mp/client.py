#!/usr/bin/env python3
"""Multiplayer client: shares your Link's position through a relay and shows the other players on a radar.

    python tools/mp/client.py --relay 192.168.1.20 --name Ana          radar window
    python tools/mp/client.py --relay 192.168.1.20 --name Ana --no-ui  print distances once a second
    python tools/mp/client.py --relay 127.0.0.1 --record out/paths/walk.jsonl   also save your path (for bot.py;
                                                                                 replaces the file)
    python tools/mp/client.py --relay 192.168.1.20 --name Ana --ghost  also show the nearest player in your game
                                                                        (needs the ghost mod: docs/ghost.md)
    python tools/mp/client.py --relay 192.168.1.20 --name Ana --ghost-sync   ... and share animations (ghost mod
                                                                        0.5.0 on both PCs; not yet tested in game)

Start the game first with the control socket on (PowerShell: $env:SAVAGEWIND_CONTROL='7777'; .\\savagewind.exe).
The client reads `pos` and `camera` 20 times a second over that socket (tools/control.py) and sends them to the relay,
with the speed it measures from those positions (SpeedMeter).
Other players are drawn about 0.1 s behind, interpolated between their last two samples (a jitter buffer), so they
move smoothly even when packets arrive unevenly. "Go to" moves your Link to the selected player with the game's
`teleport` command. The other Link is not drawn inside the game, except with --ghost, which has the ghost mod
(native/examples/ghost) put an actor where the nearest other player is (docs/ghost.md).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import signal
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_TOOLS = str(Path(__file__).resolve().parent.parent)
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
from control import DEFAULT_PORT as GAME_PORT, Control, ControlError  # noqa: E402
from mp import (DEFAULT_RELAY_PORT, DEFAULT_ROOM, ITEM_KEYS, PROTOCOL_VERSION, clean_state, decode,  # noqa: E402
                encode, parse_hostport, safe_console, safe_name, valid_anim, valid_name, valid_on_back, valid_room)

DEFAULT_HZ = 20.0
DEFAULT_DELAY = 0.1       # how far behind real time other players are drawn (s)
STALE_AFTER = 3.0         # no news for this long: drawn grey
GOTO_LIFT = 0.5           # teleport this far above the other player's feet (a target below ground makes Link fall)
TP_ACTIVE = {"scanning", "probing", "testing", "verifying"}   # teleport states that are still running (control_tp.c)
JUMP = 30.0               # m: two samples farther apart are a teleport, never blended (the ghost mod jumps too)
JUMP_SPEED = 40.0         # m/s: ... and so are two closer ones that would need more than this (a short teleport)
SPEED_WINDOW = 0.4        # s: a player's speed is the distance they covered over this long (SpeedMeter)
SPEED_MIN_SPAN = 0.1      # s: ... once at least this much is measured (until then the last speed stays)
SPEED_GAP = 1.0           # s: no position for this long (a loading screen, a pause): measure again from there
SPEED_HITCH = 0.15        # s: a step this long (and over 3 usual steps) is a frame hitch in the sender's own game
SPEED_HOLD = 1.0          # s: a player shown at their newest sample for longer than this (no news) has speed 0
LATE_ATTACK_SHOW = 0.3    # s: an attack the jitter buffer skipped (all of it came during a stall) is shown this long


# ---------------------------------------------------------------- interpolation

def lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * f


def lerp_angle(a: float, b: float, f: float) -> float:
    """Blend two angles in degrees the short way round. Result in [-180, 180)."""
    d = (b - a + 180.0) % 360.0 - 180.0
    return (a + d * f + 180.0) % 360.0 - 180.0


def interpolate(s0: dict, s1: dict, f: float) -> dict:
    """The state a fraction f of the way from s0 to s1."""
    out = {k: lerp(s0[k], s1[k], f) for k in ("t", "x", "y", "z")}
    y0, y1 = s0.get("yaw"), s1.get("yaw")
    out["yaw"] = lerp_angle(y0, y1, f) if y0 is not None and y1 is not None else (y1 if y1 is not None else y0)
    m0, m1 = s0.get("stamina"), s1.get("stamina")
    out["stamina"] = lerp(m0, m1, f) if m0 is not None and m1 is not None else (m1 if m1 is not None else m0)
    v0, v1 = s0.get("speed"), s1.get("speed")
    if v0 is not None or v1 is not None:
        out["speed"] = lerp(v0, v1, f) if v0 is not None and v1 is not None else (v1 if v1 is not None else v0)
    near = s0 if f < 0.5 else s1
    for key in ("place", "anim", "attack", "on_back") + ITEM_KEYS:   # (names aren't blended: the nearer sample's)
        if near.get(key) is not None:
            out[key] = near[key]
    return out


def is_jump(s0: dict, s1: dict) -> bool:
    """The player teleported between these two samples (s0 the older): more than JUMP metres apart, or faster than
    JUMP_SPEED between them."""
    d = math.dist((s0["x"], s0["y"], s0["z"]), (s1["x"], s1["y"], s1["z"]))
    return d > JUMP or d > JUMP_SPEED * (s1["t"] - s0["t"])


class SpeedMeter:
    """A player's speed on the ground (m/s) from their positions and the times they were at them: the straight
    distance covered over the last `window` seconds (x and z only).

    Fed where the times are true (the sender's own clock: its own game's positions, or a peer's samples with the
    `t` they carry), so it is smooth and network jitter can't touch it. A step over JUMP m or faster than JUMP_SPEED
    (a teleport), more than `gap` s with no position (a loading screen, a pause) or the clock going back (a restart)
    starts the measurement again from there; until it spans `min_span` s the last speed stays.

    A frame hitch in the sender's own game (a screenshot, an autosave, streaming: one frame takes 0.2-0.5 s) is a
    step over `hitch` s and over 3 times the usual time between positions. The game's clock may stop during it, so
    Link moved only a frame's worth while the client's clock ran on: over the window that would read as a dip in
    speed, and the ghost would go Run, Walk, Run. So it starts the measurement again from there too, and the last
    speed stays until the new positions span the whole window (or, after hitches back to back, until the speed is
    `gap` s old: then `min_span` is enough again).
    """

    def __init__(self, window: float = SPEED_WINDOW, min_span: float = SPEED_MIN_SPAN, gap: float = SPEED_GAP,
                 hitch: float = SPEED_HITCH):
        self.window, self.min_span, self.gap, self.hitch = window, min_span, gap, hitch
        self.samples: deque[tuple[float, float, float]] = deque()   # x, z, t: the newest last
        self.speed = 0.0
        self.reset()

    def reset(self) -> None:
        self.samples.clear()
        self.speed = 0.0
        self.usual = 0.0                 # the usual time between positions (smoothed; 0: not known yet)
        self.refill = False              # after a hitch: the last speed stays until the window is full again
        self.speed_t: float | None = None   # when the speed was last measured
        self.hitches = 0

    def update(self, x: float, z: float, t: float) -> float:
        s = self.samples
        if s:
            lx, lz, lt = s[-1]
            dt = t - lt
            if dt == 0:
                return self.speed
            d = math.hypot(x - lx, z - lz)
            if dt < 0 or dt > self.gap or d > JUMP or d > JUMP_SPEED * dt:
                s.clear()
                self.refill = False
            else:
                if self.usual and dt > max(self.hitch, 3.0 * self.usual):
                    s.clear()
                    self.refill = True
                    self.hitches += 1
                self.usual = dt if not self.usual else self.usual + (dt - self.usual) * 0.1
        s.append((x, z, t))
        while len(s) > 2 and t - s[1][2] >= self.window:   # (keep the newest one at least `window` old)
            s.popleft()
        x0, z0, t0 = s[0]
        need = self.min_span
        if self.refill and self.speed_t is not None and t - self.speed_t <= self.gap:
            need = self.window
        if t - t0 >= need - 1e-9:   # (1e-9: 10.1 - 10.0 is a hair under 0.1)
            self.speed = math.hypot(x - x0, z - z0) / (t - t0)
            self.speed_t = t
            if t - t0 >= self.window - 1e-9:
                self.refill = False
        return self.speed


class JitterBuffer:
    """The last samples of one player, played back `delay` seconds late.

    Samples carry the sender's clock `t`. The offset to our clock is the smallest (arrival - t) seen lately, i.e. the
    fastest delivery; a sample that came late does not move it. sample(now) maps now - delay onto the sender's clock and
    blends the two samples around that moment. Past the newest sample it holds the newest (no guessing ahead).
    Two samples with a teleport between them (is_jump) are not blended: the older one is held until the newer one's
    time, so the jump shows as one step (the ghost mod jumps one over JUMP, and Heading doesn't take it as a direction).

    Every sample has a "speed": the one the sender measured, or (an older sender) one measured here from the samples'
    own `t` (SpeedMeter), which arrival times don't change. It is blended like the rest, so during a network stall the
    player is held where they were last seen but keeps their speed (the ghost keeps running on the spot rather than
    switching to standing) until SPEED_HOLD s past that sample; after that it is 0.

    An attack is short (the sender keeps it at least 0.3 s), so a stall can hide all of it: its samples arrive in the
    burst after the stall, when the moment shown has already passed them, and are never shown. Such a late attack
    (a sample with attack true that came after its moment was shown, not part of an attack just shown) is shown for
    LATE_ATTACK_SHOW s from the next sample(), unless the player is already in another attack.
    """

    def __init__(self, delay: float = DEFAULT_DELAY, size: int = 32, offset_window: int = 200):
        self.delay = delay
        self.samples: deque[dict] = deque(maxlen=size)
        self._offsets: deque[float] = deque(maxlen=offset_window)
        self.last_recv: float | None = None
        self.meter = SpeedMeter()
        self._reset_attacks()

    def _reset_attacks(self) -> None:
        self._shown_t: float | None = None                 # the latest moment shown (sender's clock)
        self._late: tuple[str, float] | None = None        # (anim, t): the newest attack sample that came late
        self._attack_out: tuple[str, float] | None = None  # (anim, moment): the last attack shown
        self._show: tuple[str, float] | None = None        # (anim, until on our clock): a late attack being shown
        self.late_attacks = 0                              # late attacks shown (counted for tests and logs)

    def clear(self) -> None:
        self.samples.clear()
        self._offsets.clear()
        self.last_recv = None
        self.meter.reset()
        self._reset_attacks()

    def push(self, state: dict, recv: float) -> bool:
        """Add a sample that arrived at `recv` (our clock). False for a repeat."""
        t = state["t"]
        if self.samples:
            last = self.samples[-1]["t"]
            if t == last:
                return False
            if t < last:          # the sender's clock went back: it restarted
                self.clear()
        measured = self.meter.update(state["x"], state["z"], t)
        if state.get("speed") is None:
            state = dict(state, speed=measured)
        self.samples.append(state)
        self._offsets.append(recv - t)
        self.last_recv = recv
        if state.get("attack") is True and state.get("anim") and self._shown_t is not None and t <= self._shown_t:
            self._late = (state["anim"], t)       # (its moment was shown before it came: maybe never shown)
        return True

    def offset(self) -> float:
        return min(self._offsets)

    def latest(self) -> dict | None:
        return self.samples[-1] if self.samples else None

    def sample(self, now: float) -> dict | None:
        if not self.samples:
            return None
        rt = now - self.offset() - self.delay     # the moment to show, on the sender's clock
        out, stale = self._sample_at(rt)
        self._shown_t = rt if self._shown_t is None else max(self._shown_t, rt)
        return self._attacks(out, rt, now, stale)

    def _sample_at(self, rt: float) -> tuple[dict, bool]:
        """(the state at the sender's moment rt, whether it is stale: no news for over SPEED_HOLD s)."""
        s = self.samples
        if rt <= s[0]["t"]:
            return dict(s[0]), False
        if rt >= s[-1]["t"]:
            out = dict(s[-1])
            if rt - s[-1]["t"] > SPEED_HOLD:   # (no news for a while: it doesn't keep running on the spot,
                if out.get("speed"):           # or swinging a sword)
                    out["speed"] = 0.0
                out.pop("anim", None)
                out.pop("attack", None)
                return out, True
            return out, False
        for i in range(len(s) - 1, 0, -1):
            a = s[i - 1]
            if a["t"] <= rt:
                b = s[i]
                if is_jump(a, b):
                    return dict(a), False
                return interpolate(a, b, (rt - a["t"]) / (b["t"] - a["t"])), False
        return dict(s[0]), False

    def _attacks(self, out: dict, rt: float, now: float, stale: bool) -> dict:
        """A late attack (see the class): shown for LATE_ATTACK_SHOW s, unless one shows already."""
        if out.get("attack") is True:         # (an attack shows now: a late one before it is past)
            self._attack_out = (out.get("anim"), rt)
            self._late = self._show = None
            return out
        late, self._late = self._late, None
        if late is not None and not stale:
            anim, t = late
            shown = self._attack_out is not None and self._attack_out[0] == anim and \
                t - self._attack_out[1] <= LATE_ATTACK_SHOW
            if not shown and rt - t <= SPEED_HOLD:
                self._show = (anim, now + LATE_ATTACK_SHOW)
                self._attack_out = (anim, rt)
                self.late_attacks += 1
        if self._show is not None:
            if now < self._show[1] and not stale:
                out["anim"], out["attack"] = self._show[0], True
            else:
                self._show = None
        return out


# ---------------------------------------------------------------- geometry and text

def compass(dx: float, dz: float) -> str:
    """8-point direction of an offset (north is -z on the game's map, east is +x)."""
    ang = math.degrees(math.atan2(dx, -dz)) % 360.0
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int((ang + 22.5) // 45.0) % 8]


def radar_xy(dx: float, dz: float, yaw: float | None = None) -> tuple[float, float]:
    """Screen offset (right, down) in metres of a world offset. yaw None: north up; else the camera's view is up."""
    a = math.radians(180.0 if yaw is None else yaw)
    fx, fz = math.sin(a), math.cos(a)          # camera forward on the ground (yaw 0 = +z, 90 = +x)
    rx, rz = -fz, fx                           # camera right
    return dx * rx + dz * rz, -(dx * fx + dz * fz)


RANGES = (25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)


def nice_range(dist: float | None) -> int:
    """The smallest radar range that shows a player at this distance with a little margin."""
    for r in RANGES:
        if dist is None or dist * 1.1 <= r:
            return r
    return RANGES[-1]


def height_text(dy: float) -> str:
    if abs(dy) < 1.0:
        return "same height"
    return f"{abs(dy):.0f} m {'higher' if dy > 0 else 'lower'}"


def describe(v: dict) -> str:
    name = v["name"] + (" (bot)" if v.get("bot") else "")
    if v.get("dist") is None:
        return f"{name}: no position yet"
    text = f"{name}: {v['dist']:.0f} m {compass(v['dx'], v['dz'])}, {height_text(v['dy'])}"
    if v.get("stale"):
        text += f" (no news for {v['age']:.0f} s)"
    return text


# ---------------------------------------------------------------- what we know about everyone

class Peer:
    def __init__(self, pid: int, name: str, bot: bool = False, delay: float = DEFAULT_DELAY):
        self.id, self.name, self.bot = pid, name, bot
        self.buffer = JitterBuffer(delay)


class Session:
    """Local player and peers. Thread safe: the network thread writes, the window reads."""

    def __init__(self, name: str, room: str = DEFAULT_ROOM, delay: float = DEFAULT_DELAY):
        self.name, self.room, self.delay = name, room, delay
        self.lock = threading.RLock()
        self.my_id: int | None = None
        self.peers: dict[int, Peer] = {}
        self.local: dict | None = None
        self.meter = SpeedMeter()      # our own speed, from our own positions and clock (sent as "speed")

    def _add(self, p: dict) -> Peer | None:
        pid, name = p.get("id"), valid_name(p.get("name"))
        if not isinstance(pid, int) or isinstance(pid, bool) or name is None or pid == self.my_id:
            return None
        peer = self.peers.get(pid)
        if peer is None:
            peer = self.peers[pid] = Peer(pid, name, bool(p.get("bot")), self.delay)
        return peer

    def handle(self, msg: dict, now: float) -> None:
        """Apply one message from the relay (or {"type": "disconnected"})."""
        kind = msg.get("type")
        with self.lock:
            if kind == "welcome":
                self.my_id = msg.get("id")
                self.room = msg.get("room", self.room)
                self.peers = {}
                for p in msg.get("peers") or []:
                    if not isinstance(p, dict):
                        continue
                    peer = self._add(p)
                    st = p.get("state")
                    if peer is not None and isinstance(st, dict):
                        clean = clean_state(st)
                        age = st.get("age")
                        age = float(age) if isinstance(age, (int, float)) and not isinstance(age, bool) and age >= 0 else 0.0
                        if clean is not None:
                            peer.buffer.push(clean, now - age)
            elif kind == "join":
                self._add(msg)
            elif kind == "leave":
                self.peers.pop(msg.get("id"), None)
            elif kind == "state":
                clean = clean_state(msg)
                peer = self._add(msg) if clean is not None else None
                if peer is not None:
                    peer.buffer.push(clean, now)
            elif kind == "disconnected":
                self.my_id = None
                self.peers = {}

    def update_local(self, pos: dict | None, cam: dict | None, now: float, own: dict | None = None) -> dict | None:
        """Take the game's `pos` and `camera` replies; return the state message to send (None: nothing to send).

        It carries our speed on the ground, measured here from our own positions and clock (SpeedMeter), so the
        others don't have to work it out from positions that came over the network unevenly. own: more fields from
        our own game (--ghost-sync: anim, attack, weapon, shield, bow, on_back from the mod's `ghost self`), checked
        like the others' (clean_state).
        """
        with self.lock:
            self.local = None
            if not isinstance(pos, dict):
                return None
            yaw = cam.get("yaw") if isinstance(cam, dict) else None
            msg = {"t": round(now, 3), "x": pos.get("x"), "y": pos.get("y"), "z": pos.get("z"),
                   "yaw": yaw, "stamina": pos.get("stamina"), "place": pos.get("place")}
            for key in ("anim", "attack", "on_back") + ITEM_KEYS:
                if own and own.get(key) is not None:
                    msg[key] = own[key]
            st = clean_state(msg)
            if st is None:
                return None
            for k in ("x", "y", "z"):
                st[k] = round(st[k], 3)
            if st["yaw"] is not None:
                st["yaw"] = round(st["yaw"], 1)
            st["speed"] = round(self.meter.update(st["x"], st["z"], st["t"]), 2)
            self.local = st
            return dict(st, type="state")

    def peer_sample(self, pid: int, now: float) -> dict | None:
        with self.lock:
            peer = self.peers.get(pid)
            return peer.buffer.sample(now) if peer else None

    def views(self, now: float) -> list[dict]:
        """Every peer as seen from the local player, nearest first."""
        with self.lock:
            local = self.local
            out = []
            for peer in self.peers.values():
                v = {"id": peer.id, "name": peer.name, "bot": peer.bot, "dist": None, "dx": None, "dy": None,
                     "dz": None, "age": None, "stale": True}
                s = peer.buffer.sample(now)
                if s is not None:
                    v.update(x=s["x"], y=s["y"], z=s["z"], yaw=s.get("yaw"), speed=s.get("speed"))
                    v.update((k, s[k]) for k in ("anim", "attack", "on_back") + ITEM_KEYS if k in s)
                    v["age"] = max(0.0, now - peer.buffer.last_recv)
                    v["stale"] = v["age"] > STALE_AFTER
                    if local is not None:
                        v["dx"], v["dy"], v["dz"] = s["x"] - local["x"], s["y"] - local["y"], s["z"] - local["z"]
                        v["dist"] = math.hypot(v["dx"], v["dz"])
                out.append(v)
        out.sort(key=lambda v: (v["dist"] is None, v["dist"] or 0.0, v["name"].lower(), v["id"]))
        return out

    def goto_target(self, pid: int, now: float) -> tuple[float, float, float, str] | None:
        """Where 'Go to' sends Link for this peer: their position, a little higher. None: no position yet."""
        with self.lock:
            peer = self.peers.get(pid)
            s = peer.buffer.sample(now) if peer else None
            if s is None:
                return None
            return s["x"], s["y"] + GOTO_LIFT, s["z"], peer.name

    def summary(self, now: float) -> str | None:
        """One line for --no-ui. None while the local position is unknown."""
        if self.local is None:
            return None
        views = self.views(now)
        if not views:
            return f"Nobody else in room {self.room} yet."
        return " | ".join(describe(v) for v in views)


# ---------------------------------------------------------------- the local game

def link_failure(reply) -> str | None:
    """Why a batch slot failed when the connection itself failed: 'timeout', 'closed' or 'garbled'. None otherwise.

    Control.batch(errors=True) puts these into the reply slots too, next to the game's own errors ("No world
    loaded"). The texts are tools/control.py's (Control._read_line and Control._reply).
    """
    if not isinstance(reply, ControlError):
        return None
    text = str(reply)
    if text == "the game didn't answer in time":
        return "timeout"
    if text == "the game closed the connection":
        return "closed"
    if text.startswith("invalid reply"):
        return "garbled"
    return None


class GameLink:
    """The game's control socket: poll() reads pos and camera in one frame, and runs a requested teleport.

    Blocking (call it from a worker thread). When the game isn't there it tries again every `retry` seconds, quietly.
    When the connection fails (the game closed it, didn't answer in time, or sent nonsense) it is closed and opened
    again: the game still answers the old requests later, so going on with the same connection would match every
    later reply to the wrong request.
    """

    def __init__(self, port: int = GAME_PORT, *, connect=None, retry: float = 2.0, clock=time.monotonic, log=print,
                 tp_timeout: float = 20.0):
        self.port = port
        self.connect = connect or (lambda: Control(port, timeout=5.0))
        self.retry, self.clock, self.log, self.tp_timeout = retry, clock, log, tp_timeout
        self.ctl = None
        self.next_try = 0.0
        self.status = "waiting"          # waiting | busy | not answering | no world | ok
        self._announced = False
        self._confirmed = False          # this connection has had a real answer
        self._said: set[str] = set()     # problems already reported since the last good connection
        self._lock = threading.Lock()
        self._tp_request = None
        self.tp_active = False
        self.tp_started = 0.0
        self.tp_label = ""
        self.tp_message = ""
        self.connections = 0             # game connections opened so far (a new one may be a new game)
        self.extra_replies = None        # replies to poll()'s `extra` requests; None: they weren't sent

    def request_teleport(self, x: float, y: float, z: float, label: str = "") -> None:
        with self._lock:
            self._tp_request = (float(x), float(y), float(z), label)

    def _note(self, key: str, text: str) -> None:
        if key not in self._said:
            self._said.add(key)
            self.log(text)

    def _drop(self, now: float, why: str = "closed") -> None:
        """Close the connection after it failed (why: closed | timeout | garbled) and try again later."""
        had = self.ctl is not None
        if had:
            try:
                self.ctl.close()
            except Exception:
                pass
            again = f"trying again every {self.retry:g} s."
            if why == "timeout":
                self._note("timeout", f"The game stopped answering; {again}" if self._confirmed
                           else f"The game isn't answering; {again}")
            elif why == "garbled":
                self._note("garbled", f"The game's reply made no sense; {again}")
            elif self._confirmed:
                self._note("lost", f"Lost the game; {again}")
            else:   # (closed before any answer: the game takes 4 control connections and closes a 5th at once)
                self._note("busy", f"The game's control socket is busy (it allows 4 connections); {again}")
        if not had:
            self.status = "waiting"
        elif why == "timeout":
            self.status = "not answering"
        elif why == "closed" and not self._confirmed:
            self.status = "busy"
        else:
            self.status = "waiting"
        self.ctl = None
        self._confirmed = False
        self.next_try = now + self.retry
        if self.tp_active:
            self.tp_message = "Lost the game during the teleport."
        self.tp_active = False

    def close(self) -> None:
        if self.ctl is not None:
            try:
                self.ctl.close()
            except Exception:
                pass
            self.ctl = None

    def send_last(self, requests) -> bool:
        """Send a last batch on the open connection, if any (best effort, before close). True when it was answered."""
        if self.ctl is None or not requests:
            return False
        try:
            replies = self.ctl.batch([list(r) for r in requests], errors=True)
        except Exception:
            return False
        return not any(map(link_failure, replies))

    def poll(self, extra=None) -> tuple[dict | None, dict | None]:
        """(pos reply, camera reply); None for each that isn't available.

        extra: more requests for the same batch (--ghost's). Their replies (a dict or a ControlError each) go to
        self.extra_replies, which is None when they weren't sent (no game, or the connection failed).
        """
        now = self.clock()
        self.extra_replies = None
        if self.ctl is None:
            if now < self.next_try:
                return None, None
            try:
                self.ctl = self.connect()
            except (ControlError, OSError):
                self.next_try = now + self.retry
                self.status = "waiting"
                if not self._announced:
                    self.log(f"Waiting for the game on port {self.port} (start it with SAVAGEWIND_CONTROL={self.port}).")
                    self._announced = True
                return None, None
            self._confirmed = False
            self.connections += 1
        with self._lock:
            req, self._tp_request = self._tp_request, None
        requests = [["pos"], ["camera"]]
        refused = None
        if req is not None and self.tp_active:
            refused, req = req, None
        if req is not None:
            requests.append(["teleport", f"{req[0]:.3f}", f"{req[1]:.3f}", f"{req[2]:.3f}"])
        elif self.tp_active:
            requests.append(["teleport", "status"])
        own = len(requests)
        requests += [list(r) for r in extra or ()]
        try:
            replies = self.ctl.batch(requests, errors=True)
        except Exception as e:   # (OSError from the socket, or a reply that isn't a JSON object: the link is broken)
            self._drop(now, "timeout" if isinstance(e, TimeoutError) or link_failure(e) == "timeout"
                       else "closed" if isinstance(e, OSError) or link_failure(e) == "closed" else "garbled")
            return None, None
        why = next((w for w in map(link_failure, replies) if w), None)
        if why is None and len(replies) != len(requests):
            why = "garbled"
        if why is not None:
            self._drop(now, why)
            return None, None
        if not self._confirmed:
            self._confirmed = True
            self._said.clear()
            self.log("Connected to the game.")
        pos = None if isinstance(replies[0], ControlError) else replies[0]
        cam = None if isinstance(replies[1], ControlError) else replies[1]
        self.status = "ok" if pos is not None else "no world"
        self.extra_replies = replies[own:]
        if own > 2:
            self._teleport_reply(replies[2], req, now)
        if refused is not None:   # (say so after the status reply, which would overwrite it)
            other = f" {refused[3]}" if refused[3] else ""   # (the button reads "Go to Bo")
            if self.tp_active:
                to = f" to {self.tp_label}" if self.tp_label else ""
                self.tp_message = f"Still going{to}; press Go to{other} again after."
            else:
                self.tp_message = f"{self.tp_message} Press Go to{other} again.".strip()
        return pos, cam

    def _teleport_reply(self, r, req, now: float) -> None:
        if req is not None:
            self.tp_started, self.tp_label = now, req[3]
        to = f" to {self.tp_label}" if self.tp_label else ""
        if isinstance(r, ControlError):
            self.tp_active = False
            text = str(r)
            if "Distance below" in text:
                self.tp_message = "You're already there."
            elif "Unknown command" in text:
                self.tp_message = "Teleport needs the control socket (SAVAGEWIND_CONTROL) on."
            else:
                self.tp_message = f"Teleport failed: {text}"
            return
        state = r.get("state", "") if isinstance(r, dict) else ""
        if state in TP_ACTIVE:
            if now - self.tp_started > self.tp_timeout:
                self.tp_active = False
                self.tp_message = "Teleport is taking too long; stopped waiting."
            else:
                self.tp_active = True
                self.tp_message = f"Teleporting{to}..."
        elif state == "done":
            self.tp_active = False
            self.tp_message = f"Arrived{' next to ' + self.tp_label if self.tp_label else ''}."
        elif state == "failed":
            self.tp_active = False
            self.tp_message = f"Teleport failed: {r.get('message') or 'no reason given'}"
        else:
            self.tp_active = False


# ---------------------------------------------------------------- the relay

async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except asyncio.TimeoutError:
        pass


class RelayLink:
    """Connection to the relay: says hello, hands every message to on_message, pings, reconnects."""

    def __init__(self, host: str, port: int, name: str, room: str = DEFAULT_ROOM, *, bot: bool = False,
                 on_message=None, log=print, retry: float = 2.0, ping_every: float = 5.0, clock=time.monotonic):
        self.host, self.port, self.name, self.room, self.bot = host, port, name, room, bot
        self.on_message = on_message
        self.log, self.retry, self.ping_every, self.clock = log, retry, ping_every, clock
        self.connected = False
        self.my_id: int | None = None
        self.error: str | None = None
        self.rtt: float | None = None
        self.peer_count = 0
        self._writer = None
        self._last_error = ("", -1e9)
        self._failed_said: str | None = None

    def _deliver(self, msg: dict) -> None:
        if self.on_message is not None:
            try:
                self.on_message(msg)
            except Exception as e:   # (a bad message must not end the connection)
                self.log(f"Ignored a message: {e}")

    def send(self, msg: dict) -> bool:
        """Queue one message. False when not connected or the relay is not keeping up (the message is dropped)."""
        w = self._writer
        if not self.connected or w is None or w.transport is None or w.transport.is_closing():
            return False
        if w.transport.get_write_buffer_size() > 64 * 1024:
            return False
        try:
            w.write(encode(msg))
        except (ValueError, OSError, RuntimeError):
            return False
        return True

    async def run(self, stop: asyncio.Event) -> None:
        announced = False
        while not stop.is_set():
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port, limit=1 << 20), 5.0)
            except (OSError, asyncio.TimeoutError):
                self.error = "can't reach the relay"
                if not announced:
                    self.log(f"Can't reach the relay at {self.host}:{self.port}; trying again every {self.retry:g} s.")
                    announced = True
                await _sleep(stop, self.retry)
                continue
            announced = False
            wait, was, failed = self.retry, False, None
            try:
                wait = await self._session(reader, writer, stop)
            except (OSError, ValueError, asyncio.IncompleteReadError, asyncio.TimeoutError):
                pass
            except Exception as e:   # (anything else, say a broken relay: say so, then reconnect as usual)
                failed = f"{type(e).__name__}: {str(e)[:120]}"
            finally:
                was = self.connected
                self.connected, self._writer, self.my_id, self.peer_count = False, None, None, 0
                try:
                    writer.close()
                except (OSError, RuntimeError):
                    pass
                self._deliver({"type": "disconnected"})
            if stop.is_set():
                break
            if failed:
                self.error = "error from the relay"
                if failed != self._failed_said:   # (the same error again and again: once)
                    self.log(f"Error on the relay connection ({failed}); reconnecting.")
                    self._failed_said = failed
            elif was:
                self._failed_said = None
                self.log("Lost the relay; reconnecting.")
            await _sleep(stop, wait)

    async def _session(self, reader, writer, stop: asyncio.Event) -> float:
        hello = {"type": "hello", "name": self.name, "room": self.room, "version": PROTOCOL_VERSION}
        if self.bot:
            hello["bot"] = True
        writer.write(encode(hello))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), 10.0)
        msg = decode(line) if line else None
        if msg is None:
            self.error = "no answer from the relay"
            return self.retry
        if msg["type"] == "error":
            self.error = str(msg.get("error"))[:200]
            self.log(f"The relay said no: {self.error}. Trying again in 10 s.")
            return max(self.retry, 10.0)
        if msg["type"] != "welcome":
            return self.retry
        self.my_id, self.room, self.error = msg.get("id"), msg.get("room", self.room), None
        self.peer_count = len(msg.get("peers") or [])
        self.connected, self._writer = True, writer
        self._deliver(msg)
        n = self.peer_count + 1
        self.log(f"Joined room {self.room} as {self.name} ({n} player{'s' if n != 1 else ''}).")
        closer = asyncio.create_task(self._close_on_stop(stop, writer))
        pinger = asyncio.create_task(self._pinger(writer))
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                msg = decode(line)
                if msg is None:
                    continue
                kind = msg["type"]
                if kind == "pong":
                    t = msg.get("t")
                    if isinstance(t, (int, float)) and not isinstance(t, bool):
                        self.rtt = max(0.0, self.clock() - t)
                elif kind == "join":
                    self.peer_count += 1
                elif kind == "leave":
                    self.peer_count = max(0, self.peer_count - 1)
                elif kind == "error":
                    text, at = str(msg.get("error"))[:200], self.clock()
                    if text != self._last_error[0] or at - self._last_error[1] > 10.0:
                        self.log(f"Relay: {text}")
                        self._last_error = (text, at)
                elif kind == "bye":
                    self.log(f"The relay closed ({msg.get('reason') or 'no reason'}).")
                self._deliver(msg)
        finally:
            closer.cancel()
            pinger.cancel()
        return self.retry

    async def _close_on_stop(self, stop: asyncio.Event, writer) -> None:
        await stop.wait()
        try:
            writer.write(encode({"type": "bye"}))
            writer.close()
        except (OSError, RuntimeError):
            pass

    async def _pinger(self, writer) -> None:
        while True:
            await asyncio.sleep(self.ping_every)
            if writer.transport is None or writer.transport.is_closing():
                return
            writer.write(encode({"type": "ping", "t": round(self.clock(), 3)}))


# ---------------------------------------------------------------- --ghost: another player as an actor in your game

GHOST_STATUS_EVERY = 2.0     # ask the mod how its ghost is doing this often (s)
GHOST_ASK_AGAIN = 10.0       # the mod stopped trying (`wanted` false): send `ghost spawn` again after this long (s),
GHOST_ASK_AGAIN_MAX = 60.0   # ... twice as long each time, up to this, until a ghost stays GHOST_STABLE s
GHOST_STABLE = 10.0
GHOST_TURN_SPEED = 0.5       # m/s: a player moving slower than this keeps the ghost's last facing
GHOST_ITEMS_EVERY = 2.0      # --ghost-items: `ghost items` goes again this often even with no change (s)
# --ghost-link: the Link body. tools/mp/ghost_link_pack.py builds a pack under this name (the default stand-in's) from
# your own game files; deployed, the ghost looks like Link, else it is the Sheikah man. Asked for by name, so the mod
# never swaps in its crate. (Also ghost.c's LINK_ACTOR.)
GHOST_LINK_ACTOR = "Dm_Npc_Sheiker_Searcher_M_001"
# The Link body's two-handed and spear swings (ghost_link_pack.py; our own names: the player has none like them). The
# player's Link plays the same names (CutS1, CutSF...) whatever he holds, so the receiving client picks these by the
# weapon the sender holds: (weapon actor name prefix, suffix, the names that have such a swing). An actor without
# them (the Sheikah man) shows the CutS1 stand-in or the speed rule, as for any attack it lacks.
WEAPON_ANIMS = (("Weapon_Lsword_", "_Lsword", ("CutS1", "CutSF")),
                ("Weapon_Spear_", "_Spear", ("CutS1", "CutS2", "CutSF")))


def item_mask(v: dict) -> int:
    """--ghost-items: which of the shown player's items are on their back (bit 0 weapon, 1 shield, 2 bow): what they
    sent (`on_back`), else (an older sender) the bow, or the weapon and shield while they aim the bow (an animation
    name that starts with Bow)."""
    m = valid_on_back(v.get("on_back"))
    if m is not None:
        return m
    return 3 if str(v.get("anim") or "").startswith("Bow") else 4


# --ghost-items (ghost mod 0.6.1): the only names sent for the ghost to hold, as the mod's own rule (ghost.c
# item_name_ok): the other player's network data must never make your game spawn anything else. Slot 0 (weapon) a
# one-handed sword, two-hander or spear, slot 1 a shield, slot 2 a bow, by the game's name prefixes; none of the special
# ones never tried (Weapon_Sword_070 and the four with its sort key in the game's data, Weapon_Sword_500, Weapon_Bow_071).
ITEM_PREFIXES = (("Weapon_Sword_", 0), ("Weapon_Lsword_", 0), ("Weapon_Spear_", 0), ("Weapon_Shield_", 1),
                 ("Weapon_Bow_", 2))
ITEM_BLOCKED = frozenset({"Weapon_Sword_070", "Weapon_Sword_071", "Weapon_Sword_072", "Weapon_Sword_080",
                          "Weapon_Sword_081", "Weapon_Sword_500", "Weapon_Bow_071"})


def item_name_ok(slot: int, name) -> bool:
    """A name the ghost may hold in slot (0 weapon, 1 shield, 2 bow): see ITEM_PREFIXES."""
    if valid_actor(name) is None or name in ITEM_BLOCKED:
        return False
    return any(s == slot and name.startswith(p) and len(name) > len(p) for p, s in ITEM_PREFIXES)


def items_command(v: dict) -> list[str]:
    """--ghost-items: `ghost items weapon shield bow mask` for the shown player ("-": none, or a name the ghost may not
    hold: item_name_ok)."""
    names = [v.get(k) if item_name_ok(i, v.get(k)) else "-" for i, k in enumerate(ITEM_KEYS)]
    return ["ghost", "items", *names, str(item_mask(v))]


def weapon_anim(anim: str | None, weapon) -> str | None:
    """The name to send the mod for the other player's `anim`: their two-handed or spear swing when they hold such a
    weapon (WEAPON_ANIMS), else anim as it is."""
    if anim and isinstance(weapon, str):
        for prefix, suffix, names in WEAPON_ANIMS:
            if weapon.startswith(prefix) and anim in names:
                return anim + suffix
    return anim


class Heading:
    """The facing the ghost gets: the direction a player moves in, from their successive positions (yaw in degrees,
    0 faces +z, 90 faces +x, as the mod takes it). Moving slower than min_speed (standing still) keeps the last
    facing; so does a step faster than max_speed or longer than jump (a teleport). Before any movement it is the
    camera yaw they sent (0 without one). Only the ground plane counts."""

    def __init__(self, min_speed: float = GHOST_TURN_SPEED, max_speed: float = JUMP_SPEED, jump: float = JUMP):
        self.min_speed, self.max_speed, self.jump = min_speed, max_speed, jump
        self.reset()

    def reset(self) -> None:
        self.last: tuple[float, float, float] | None = None   # x, z, time of the previous position
        self.yaw: float | None = None                         # from movement; None: no movement seen yet

    def update(self, x: float, z: float, t: float, camera_yaw: float | None = None) -> float:
        if self.last is not None:
            lx, lz, lt = self.last
            dt = t - lt
            if dt <= 0:
                return self.current(camera_yaw)
            dx, dz = x - lx, z - lz
            d = math.hypot(dx, dz)
            if self.min_speed <= d / dt <= self.max_speed and d <= self.jump:
                self.yaw = math.degrees(math.atan2(dx, dz))
        self.last = (x, z, t)
        return self.current(camera_yaw)

    def current(self, camera_yaw: float | None = None) -> float:
        if self.yaw is not None:
            return self.yaw
        return camera_yaw if camera_yaw is not None else 0.0


def valid_actor(name) -> str | None:
    """An actor name for `ghost spawn`: letters, digits and _, up to 63 (what the mod takes). None if not."""
    if isinstance(name, str) and 0 < len(name) <= 63 and all(c.isascii() and (c.isalnum() or c == "_") for c in name):
        return name
    return None


class Ghost:
    """--ghost: one peer drawn in your own game as an actor, through the `ghost` command of the ghost mod
    (native/examples/ghost, docs/ghost.md).

    Every tick requests() gives the commands to add to the game batch: `ghost spawn` once, then `ghost pos x y z yaw`
    with the peer's interpolated position (the jitter buffer's sample, as on the radar) and their direction of travel
    as the yaw (Heading: the camera yaw only until they first move). A mod that takes it (its `ghost` reply says
    takes_speed: ghost 0.4.0 and later) also gets their speed as a fifth number, `ghost pos x y z yaw speed`, which it
    uses for Wait, Walk or Run instead of working the speed out from the positions; an older mod gets four numbers,
    as before. The `ghost` state is asked for at once on each new game connection. When nobody is left to show
    it sends `ghost despawn` (every tick until the game answers it), and last() gives the `ghost despawn` to send at
    exit while the game may still hold a ghost from us. replies() takes the answers. If the mod says it stopped
    trying (its `ghost` reply has wanted false), `ghost spawn` goes again after GHOST_ASK_AGAIN s, then twice as long
    each time. The peer is the nearest one, or the one named with --ghost-peer. Runner calls these from its tick only.

    sync (--ghost-sync, opt-in; checked in game on one PC with the bot): with a mod that says it can (its `ghost` reply: has_self,
    takes_anim; ghost 0.5.0), it also asks `ghost self` every tick: your own Link's animation, attack and items, which
    own_state() hands to the state you send. The other player's animation name goes to the mod after the speed
    (`ghost pos x y z yaw speed anim`; a swing with a two-handed sword or a spear as its own name, weapon_anim); the
    mod plays it when its actor has it. Their weapon, shield and bow are logged. The `ghost` state is asked for even
    before anyone is shown, so a mod that can is found at once.

    items (--ghost-items, opt-in; ghost mod 0.6.0, its `ghost` reply says has_items): the ghost holds what they hold.
    `ghost items weapon shield bow mask` goes on each change, every GHOST_ITEMS_EVERY s and on a new game connection
    (items_command; mask: item_mask). Your own `on_back` (from `ghost self`) goes into the state you send.
    """

    def __init__(self, peer: str | None = None, actor: str | None = None, log=print, clock=time.monotonic,
                 sync: bool = False, items: bool = False):
        self.peer, self.actor, self.log, self.clock, self.sync = peer, actor, log, clock, sync or items
        self.items = items           # --ghost-items: the ghost holds what the shown player holds (ghost mod 0.6.0)
        self.has_items = False       # ... the mod takes `ghost items` (its `ghost` reply said has_items)
        self._items_sent: list[str] | None = None   # the last `ghost items` sent on this connection
        self._items_at = 0.0         # ... and when it goes again with no change
        self._no_items_said = False
        self.spawned = False         # `ghost spawn` went to the game on the current connection
        self.in_game = False         # the game may hold a ghost from us: cleared only when a `ghost despawn` is answered
        self.shown: int | None = None
        self.shown_name = ""
        self.unavailable = False     # the game has no `ghost` command (no mod): stop asking until a new connection
        self.takes_speed = False     # the mod takes `ghost pos x y z yaw speed` (its `ghost` reply said so)
        self.takes_anim = False      # ... and `ghost pos x y z yaw speed anim` (0.5.0: takes_anim)
        self.has_self = False        # ... and `ghost self` (0.5.0: has_self)
        self.own: dict = {}          # sync: your own Link (from `ghost self`), for the state you send
        self._items: dict[str, str] = {}      # sync: the shown player's items, as last logged
        self._held: dict = {}                 # --ghost-items: what the shown player holds, as last sent to us (0.6.1)
        self._old_mod_said = False
        self.state = ""              # the mod's own state ("live", "pending", ...), from `ghost`
        self.mod_note = ""           # ... and what last happened there
        self._conn = None
        self._sent: list[list[str]] = []
        self._now = 0.0              # the time of the last requests()
        self._next_status = 0.0
        self._spawn_at: float | None = None   # when the last `ghost spawn` was queued
        self._ask_at: float | None = None     # the mod stopped trying: send `ghost spawn` again at this time
        self._ask_wait = GHOST_ASK_AGAIN
        self.heading = Heading()              # the shown player's facing: their direction of travel

    def pick(self, session: Session, now: float) -> dict | None:
        """The peer to show: nearest first (Session.views), or the --ghost-peer one. None: nobody with a position."""
        for v in session.views(now):
            if "x" not in v:
                continue
            if self.peer is not None and v["name"].casefold() != self.peer.casefold():
                continue
            return v
        return None

    def requests(self, session: Session, now: float, connection: int) -> list[list[str]]:
        if connection != self._conn:          # a new game connection (maybe a new game): start over
            self._conn, self.spawned, self.unavailable, self.state = connection, False, False, ""
            self._ask_at, self._ask_wait = None, GHOST_ASK_AGAIN
            self.takes_speed, self._next_status = False, 0.0   # (maybe another mod: ask what it takes at once)
            self.takes_anim = self.has_self = self.has_items = False
            self.own = {}
            self._items_sent = None
        self._sent, self._now = [], now
        if self.unavailable:
            return []
        v = self.pick(session, now)
        out: list[list[str]] = []
        if self.sync and self.has_self:
            out.append(["ghost", "self"])     # (your own Link, for the state you send: own_state)
        if v is None:
            if self.in_game:                  # (until the game answers it: a lost batch or a reconnect sends it again)
                out.append(["ghost", "despawn"])
            if self.shown is not None:
                self.log(f"Ghost: {self.shown_name or 'the other player'} is gone; ghost removed.")
            self.spawned, self.shown, self.shown_name, self._ask_at = False, None, "", None
            self._items_sent = None           # (the despawn removes its items too; the next one gets them anew)
        else:
            if not self.spawned or (self._ask_at is not None and now >= self._ask_at):
                out.append(["ghost", "spawn"] + ([self.actor] if self.actor else []))
                self.spawned = self.in_game = True
                self._spawn_at, self._ask_at = now, None
            if v["id"] != self.shown:
                self.shown, self.shown_name = v["id"], v["name"]
                self.heading.reset()
                self._items, self._held = {}, {}
                self.log(f"Ghost: showing {v['name']} in your game.")
            yaw = self.heading.update(v["x"], v["z"], now, v.get("yaw"))
            pos = ["ghost", "pos", f"{v['x']:.3f}", f"{v['y']:.3f}", f"{v['z']:.3f}", f"{yaw:.1f}"]
            anim = valid_anim(v.get("anim")) if self.sync and self.takes_anim else None
            anim = weapon_anim(anim, v.get("weapon"))
            if self.takes_speed and (v.get("speed") is not None or anim):
                pos.append(f"{v['speed']:.2f}" if v.get("speed") is not None else "-1")
            if anim:
                pos.append(anim)
            out.append(pos)
            if self.items and self.has_items:
                cmd = items_command(self.held(v))
                if cmd != self._items_sent or now >= self._items_at:
                    out.append(cmd)
                    self._items_sent, self._items_at = cmd, now + GHOST_ITEMS_EVERY
            if self.sync:
                self._log_items(v)
        if (self.spawned or self.sync) and now >= self._next_status:
            self._next_status = now + GHOST_STATUS_EVERY
            out.append(["ghost"])
        self._sent = out
        return out

    def held(self, v: dict) -> dict:
        """--ghost-items: what the shown player holds. A state without an item key (their game didn't answer `ghost
        self` in time: a hitch, a menu, a load) leaves it as it was; only an explicit "" means none. So a gap in
        their data never deletes the ghost's items and spawns them again. The same for on_back."""
        for k in ITEM_KEYS:
            if isinstance(v.get(k), str):
                self._held[k] = v[k]
        if valid_on_back(v.get("on_back")) is not None:
            self._held["on_back"] = v["on_back"]
        return dict(self._held, anim=v.get("anim"))

    def _log_items(self, v: dict) -> None:
        """--ghost-sync: say what the shown player has equipped, once and then on each change (not drawn yet)."""
        items = {k: v[k] for k in ITEM_KEYS if isinstance(v.get(k), str)}
        if not items or items == self._items:
            return
        self._items = items
        what = ", ".join(f"{k} {items[k] or '-'}" for k in ITEM_KEYS if k in items)
        if self.items and self.has_items:
            how = "the ghost holds them"
        elif self.items:
            how = "this ghost mod can't show them: rebuild it (0.6.0)"
        else:
            how = "start the client with --ghost-items to show them on the ghost"
        self.log(f"Ghost: {v['name']} has {what} ({how}).")

    def own_state(self) -> dict:
        """--ghost-sync: the fields of your own Link to add to the state you send ({} without them)."""
        return dict(self.own) if self.sync else {}

    def _take_self(self, r) -> None:
        """A `ghost self` reply: your own Link's animation and items, checked. Nothing while the mod has no Link."""
        self.own = {}
        if not isinstance(r, dict) or r.get("active") is not True or r.get("found") is not True:
            return
        anim = valid_anim(r.get("anim"))
        if anim:
            self.own["anim"] = anim
            if r.get("attack") is True:
                self.own["attack"] = True
        if r.get("equip_ok") is True:
            for k in ITEM_KEYS:
                item = r.get(k)
                if item == "" or valid_actor(item) is not None:
                    self.own[k] = item
            on_back = valid_on_back(r.get("on_back"))   # (ghost mod 0.6.0)
            if on_back is not None:
                self.own["on_back"] = on_back

    def replies(self, replies) -> None:
        """The game's answers to the last requests(); None: they weren't sent (no game connection)."""
        sent, self._sent = self._sent, []
        if replies is None:
            self.own = {}
            if any(r[1:2] == ["spawn"] for r in sent):
                self.spawned = False          # (send it again on the next connection)
            return                            # (a `ghost despawn` goes again too: in_game is still set)
        for req, r in zip(sent, replies):
            if req[1:2] == ["despawn"]:
                self.in_game = False          # (answered: removed, or nothing there to remove)
            if req[1:2] == ["self"]:
                if isinstance(r, ControlError) and "usage" in str(r):
                    self.has_self = False     # (an older mod after all)
                self._take_self(r)
                continue
            if isinstance(r, ControlError):
                text = str(r)
                if "Unknown command" in text:
                    if not self.unavailable:
                        self.log("Ghost: the game has no `ghost` command. Start it with modhost and the ghost mod "
                                 "(docs/ghost.md); showing the radar only.")
                    self.unavailable, self.spawned, self.in_game = True, False, False
                    return
                if req[1:2] == ["items"] and "usage" in text:
                    self.has_items = False    # (it didn't take `ghost items` after all)
                if req[1:2] == ["pos"] and len(req) == 8 and ("usage" in text or "animation" in text):
                    self.takes_anim = False   # (it didn't take the name after all: no name from now on)
                elif req[1:2] == ["pos"] and len(req) == 7 and "usage" in text:
                    self.takes_speed = False  # (it didn't take the speed after all: four numbers from now on)
                if text != self.mod_note:
                    self.mod_note = text
                    self.log(f"Ghost: the game said: {text}")
            elif len(req) == 1 and isinstance(r, dict):   # `ghost`: the mod's state
                state, note = str(r.get("state", "")), str(r.get("last", ""))
                if note and note != self.mod_note:
                    self.log(f"Ghost: {note}")
                self.state, self.mod_note = state, note or self.mod_note
                self.takes_speed = r.get("takes_speed") is True
                self.takes_anim = r.get("takes_anim") is True and self.takes_speed
                self.has_self = r.get("has_self") is True
                was, self.has_items = self.has_items, r.get("has_items") is True
                if not was and self.has_items:
                    self._items_sent = None   # (send what they hold at once)
                if self.items and not self.has_items and not self._no_items_said:
                    self._no_items_said = True
                    self.log("Ghost: this ghost mod can't show items (it is older than 0.6.0). Rebuild it: "
                             "python tools\\build_native.py build")
                if self.sync and not self.has_self and not self._old_mod_said:
                    self._old_mod_said = True
                    self.log("Ghost: this ghost mod has no `ghost self` (it is older than 0.5.0), so no animation "
                             "sync. Rebuild it: python tools\\build_native.py build")
                self._check_mod(r, state)

    def _check_mod(self, r: dict, state: str) -> None:
        """A `ghost` reply: a ghost that stayed resets the back-off; a mod that stopped trying gets asked again."""
        live_s = r.get("live_s")
        if state == "live" and isinstance(live_s, (int, float)) and live_s >= GHOST_STABLE:
            self._ask_wait = GHOST_ASK_AGAIN
        # (wanted false in the reply to the batch that carried the spawn is from before the mod saw it: skip that)
        if (r.get("wanted") is False and self.spawned and self._ask_at is None and self._spawn_at is not None
                and self._now - self._spawn_at >= 1.0):
            self._ask_at = self._now + self._ask_wait
            self.log(f"Ghost: the mod stopped trying; asking again in {self._ask_wait:g} s.")
            self._ask_wait = min(self._ask_wait * 2, GHOST_ASK_AGAIN_MAX)

    def last(self) -> list[list[str]]:
        """What to send before the client closes: remove the ghost it made (if the game may still hold one)."""
        return [["ghost", "despawn"]] if self.in_game and not self.unavailable else []

    def status_text(self) -> str:
        if self.unavailable:
            return "no ghost mod in the game"
        if not self.spawned:
            return f"waiting for {self.peer}" if self.peer else "waiting for another player"
        if self._ask_at is not None:
            return f"showing {self.shown_name} (the mod stopped trying; asking again soon)"
        text = f"showing {self.shown_name}" + (f" ({self.state})" if self.state else "")
        if self.sync:
            text += f"; you: {self.own.get('anim', '-')}" if self.has_self else "; no animation sync (old mod)"
        if self.items:
            text += "; items" if self.has_items else "; no items (old mod)"
        return text


# ---------------------------------------------------------------- the loop that ties them together

class Runner:
    """Every tick: read the game, send our state, keep the peers' states. Runs in an asyncio loop."""

    def __init__(self, relay: tuple[str, int], name: str, room: str = DEFAULT_ROOM, *, game_port: int = GAME_PORT,
                 hz: float = DEFAULT_HZ, delay: float = DEFAULT_DELAY, record: str | None = None,
                 control_factory=None, clock=time.monotonic, log=print, print_every: float | None = None, out=print,
                 ghost: Ghost | None = None):
        self.clock, self.log, self.out = clock, log, out
        self.ghost = ghost
        self.session = Session(name, room, delay)
        self.game = GameLink(game_port, connect=control_factory, clock=clock, log=log)
        self.relay = RelayLink(relay[0], relay[1], name, room, on_message=self._on_message, log=log, clock=clock)
        self.period = 1.0 / hz
        self.record = record
        self.print_every = print_every
        self.sent = 0
        self._goto = None
        self._lock = threading.Lock()
        self._loop = None
        self._stop = None
        self._last_line = None
        self._rec = None
        self.failure: str | None = None   # set when run() ended with an error (the window shows it)

    def _on_message(self, msg: dict) -> None:
        self.session.handle(msg, self.clock())

    def request_goto(self, pid: int) -> None:
        """Ask for a teleport to this peer on the next tick (any thread)."""
        with self._lock:
            self._goto = pid

    def stop(self) -> None:
        """End run() (any thread). Does nothing once run() has ended."""
        loop, stop = self._loop, self._stop
        if loop is None or stop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(stop.set)
        except RuntimeError:   # (the loop closed just now)
            pass

    def open_record(self) -> None:
        """Open the --record file, replacing it (one recording per file). Raises OSError. run() calls it too."""
        if self.record and self._rec is None:
            Path(self.record).parent.mkdir(parents=True, exist_ok=True)
            self._rec = open(self.record, "w", encoding="utf-8")

    async def run(self) -> None:
        self.open_record()
        rec = self._rec
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        executor = ThreadPoolExecutor(1, thread_name_prefix="game")
        relay_task = asyncio.create_task(self.relay.run(self._stop))
        next_print = self.clock() + (self.print_every or 0.0)
        try:
            while not self._stop.is_set():
                started = self._loop.time()
                await self.tick(executor, rec)
                if self.print_every and self.clock() >= next_print:
                    next_print = self.clock() + self.print_every
                    self._print_summary()
                await _sleep(self._stop, max(0.0, self.period - (self._loop.time() - started)))
        finally:
            self._stop.set()
            try:
                await asyncio.wait_for(relay_task, 3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
            if self.ghost is not None and self.ghost.last():
                try:   # (remove the ghost this client made)
                    await asyncio.wait_for(self._loop.run_in_executor(executor, self.game.send_last,
                                                                      self.ghost.last()), 6.0)
                except (asyncio.TimeoutError, Exception):
                    pass
            try:
                await asyncio.wait_for(self._loop.run_in_executor(executor, self.game.close), 6.0)
            except (asyncio.TimeoutError, Exception):
                pass
            executor.shutdown(wait=False)
            if rec is not None:
                rec.close()
                self._rec = None

    async def tick(self, executor=None, rec=None) -> dict | None:
        with self._lock:
            pid, self._goto = self._goto, None
        if pid is not None:
            target = self.session.goto_target(pid, self.clock())
            if target is None:
                self.game.tp_message = "No position for that player yet."
            else:
                self.game.request_teleport(*target)
        loop = asyncio.get_running_loop()
        extra = self.ghost.requests(self.session, self.clock(), self.game.connections) if self.ghost else None
        pos, cam = await loop.run_in_executor(executor, self.game.poll, extra)
        if self.ghost is not None:
            self.ghost.replies(self.game.extra_replies)
        msg = self.session.update_local(pos, cam, self.clock(), self.ghost.own_state() if self.ghost else None)
        if msg is not None:
            if rec is not None:
                rec.write(json.dumps({k: msg[k] for k in ("t", "x", "y", "z", "yaw")}) + "\n")
            if self.relay.send(msg):
                self.sent += 1
        return msg

    def _print_summary(self) -> None:
        line = self.session.summary(self.clock())
        if line is None and self.game.status == "no world":
            line = "No world loaded yet; load a save to share your position."
        if line is None:
            return
        distances = self.session.local is not None and not line.startswith("Nobody")
        if not distances and line == self._last_line:   # (say a status once, distances every time)
            return
        self._last_line = line
        self.out(line)

    def status_text(self) -> str:
        if self.failure:
            return f"Stopped after an error: {self.failure}\nClose this window and start the client again."
        game = {"ok": "in game", "no world": "no world loaded (load a save)",
                "waiting": f"waiting on port {self.game.port}",
                "busy": f"port {self.game.port} busy (the game allows 4 control connections)",
                "not answering": "not answering; trying again"}.get(self.game.status, self.game.status)
        r = self.relay
        if r.connected:
            n = r.peer_count + 1
            relay = f"room {r.room}, {n} player{'s' if n != 1 else ''}"
            if r.rtt is not None:
                relay += f", ping {r.rtt * 1000:.0f} ms"
        else:
            relay = f"connecting to {r.host}:{r.port}" + (f" ({r.error})" if r.error else "")
        text = f"Game: {game}\nRelay: {relay}"
        if self.ghost is not None:
            text += f"\nGhost: {self.ghost.status_text()}"
        return text


# ---------------------------------------------------------------- window

COLORS = ("#ff6b6b", "#4dabf7", "#ffd43b", "#69db7c", "#da77f2", "#ffa94d", "#3bc9db", "#f783ac")


def run_ui(runner: Runner) -> None:
    import tkinter as tk

    clock = runner.clock
    session = runner.session
    size, pad = 320, 16
    rad = size / 2 - pad

    root = tk.Tk()
    root.title("Ghost Link radar")
    root.configure(bg="#1b2026")
    canvas = tk.Canvas(root, width=size, height=size, bg="#10161c", highlightthickness=0)
    canvas.grid(row=0, column=0, rowspan=5, padx=8, pady=8)
    fg, bg = "#e9ecef", "#1b2026"
    tk.Label(root, text="Players", fg=fg, bg=bg, anchor="w").grid(row=0, column=1, sticky="we", padx=(0, 8), pady=(8, 0))
    listbox = tk.Listbox(root, width=36, height=10, font=("Consolas", 9), activestyle="none", exportselection=False)
    listbox.grid(row=1, column=1, sticky="nswe", padx=(0, 8))
    go_btn = tk.Button(root, text="Go to ...", state="disabled")
    go_btn.grid(row=2, column=1, sticky="we", padx=(0, 8), pady=4)
    rotate = tk.BooleanVar(value=False)
    tk.Checkbutton(root, text="Turn with camera", variable=rotate, fg=fg, bg=bg, selectcolor="#343a40",
                   activebackground=bg, activeforeground=fg).grid(row=3, column=1, sticky="w")
    status = tk.Label(root, text="", fg=fg, bg=bg, anchor="w", justify="left")
    status.grid(row=4, column=1, sticky="we", padx=(0, 8))
    message = tk.Label(root, text="", fg="#ffd43b", bg=bg, anchor="w", justify="left")
    message.grid(row=5, column=0, columnspan=2, sticky="we", padx=8, pady=(0, 8))
    root.columnconfigure(1, weight=1)
    root.rowconfigure(1, weight=1)

    ui = {"ids": [], "selected": None, "lines": None, "range": None, "shown": RANGES[0]}

    def selected_name():
        for v in session.views(clock()):
            if v["id"] == ui["selected"]:
                return v["name"]
        return None

    def on_select(_event=None):
        sel = listbox.curselection()
        ui["selected"] = ui["ids"][sel[0]] if sel and sel[0] < len(ui["ids"]) else None

    def go():
        if ui["selected"] is not None:
            runner.request_goto(ui["selected"])

    def close():
        runner.stop()
        root.destroy()

    def on_wheel(event):   # zoom: a fixed range; double-click goes back to automatic
        i = RANGES.index(ui["range"] or ui["shown"])
        ui["range"] = RANGES[max(0, i - 1) if event.delta > 0 else min(len(RANGES) - 1, i + 1)]

    def auto_range(_event=None):
        ui["range"] = None

    canvas.bind("<MouseWheel>", on_wheel)
    canvas.bind("<Button-4>", lambda e: on_wheel(type("E", (), {"delta": 1})))     # (X11 wheel)
    canvas.bind("<Button-5>", lambda e: on_wheel(type("E", (), {"delta": -1})))
    canvas.bind("<Double-Button-1>", auto_range)
    listbox.bind("<<ListboxSelect>>", on_select)
    go_btn.configure(command=go)
    root.protocol("WM_DELETE_WINDOW", close)
    signal.signal(signal.SIGINT, lambda *_: root.after(0, close))

    def draw(views):
        canvas.delete("all")
        c = size / 2
        local = session.local
        yaw = local.get("yaw") if local else None
        turn = yaw if (rotate.get() and yaw is not None) else None
        far = max((v["dist"] for v in views if v["dist"] is not None), default=None)
        rng = ui["range"] or nice_range(far)
        ui["shown"] = rng
        scale = rad / rng
        canvas.create_text(4, size - 4, anchor="sw", fill="#6c7a89", font=("Segoe UI", 8),
                           text=("fixed range" if ui["range"] else "auto range") + " - wheel: zoom, double-click: auto")
        for frac in (0.5, 1.0):
            r = rad * frac
            canvas.create_oval(c - r, c - r, c + r, c + r, outline="#2f3a45")
            canvas.create_text(c + 4, c - r + 2, text=f"{rng * frac:g} m", fill="#6c7a89", anchor="nw", font=("Segoe UI", 8))
        nx, ny = radar_xy(0.0, -1.0, turn)
        canvas.create_text(c + nx * (rad + 8), c + ny * (rad + 8), text="N", fill="#adb5bd", font=("Segoe UI", 9, "bold"))
        if local is None:
            canvas.create_text(c, c + 30, text="Your position: unknown", fill="#6c7a89", font=("Segoe UI", 9))
        else:
            if yaw is not None:
                a = math.radians(yaw)
                fx, fy = radar_xy(math.sin(a), math.cos(a), turn)
                canvas.create_line(c, c, c + fx * 22, c + fy * 22, fill="#e9ecef", arrow="last")
            canvas.create_oval(c - 4, c - 4, c + 4, c + 4, fill="#e9ecef", outline="")
        for v in views:
            if v["dist"] is None:
                continue
            sx, sy = radar_xy(v["dx"], v["dz"], turn)
            d = math.hypot(sx, sy)
            edge = d > rng
            if edge:
                sx, sy = sx / d * rng, sy / d * rng
            px, py = c + sx * scale, c + sy * scale
            color = "#6c7a89" if v["stale"] else COLORS[v["id"] % len(COLORS)]
            r = 5
            canvas.create_oval(px - r, py - r, px + r, py + r, outline=color, width=2, fill="" if edge else color)
            dy = v["dy"]
            h = "" if abs(dy) < 1 else (f" +{dy:.0f}" if dy > 0 else f" {dy:.0f}")
            right = px > size * 0.6          # (labels near the right edge go on the left of the dot)
            canvas.create_text(px - 8 if right else px + 8, py, text=f"{v['name']} {v['dist']:.0f} m{h}", fill=color,
                               anchor="e" if right else "w", font=("Segoe UI", 8))

    def refresh():
        try:
            now = clock()
            views = session.views(now)
            draw(views)
            lines, ids = [], []
            for v in views:
                name = (v["name"] + (" (bot)" if v["bot"] else ""))[:16]
                if v["dist"] is None:
                    lines.append(f"{name:<16} no position")
                else:
                    lines.append(f"{name:<16} {v['dist']:>6.0f} m {compass(v['dx'], v['dz']):<2} "
                                 f"{v['dy']:+5.0f} m{' ?' if v['stale'] else ''}")
                ids.append(v["id"])
            if lines != ui["lines"]:
                ui["lines"], ui["ids"] = lines, ids
                listbox.delete(0, "end")
                for ln in lines:
                    listbox.insert("end", ln)
                if ui["selected"] in ids:
                    listbox.selection_set(ids.index(ui["selected"]))
                elif ui["selected"] is not None:
                    ui["selected"] = None
            name = selected_name() if ui["selected"] is not None else None
            go_btn.configure(text=f"Go to {name}" if name else "Go to ...",
                             state="normal" if name and session.local is not None else "disabled")
            status.configure(text=runner.status_text())
            message.configure(text=runner.game.tp_message)
        finally:
            root.after(100, refresh)

    refresh()
    root.mainloop()


# ---------------------------------------------------------------- command line

def default_name() -> str:
    return safe_name(os.environ.get("USERNAME") or os.environ.get("USER") or "player")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Share your position with other players and show them on a radar.")
    ap.add_argument("--relay", default="127.0.0.1", help=f"relay address, HOST or HOST:PORT (default port {DEFAULT_RELAY_PORT})")
    ap.add_argument("--name", default=None, help="your name (1-20 letters, digits, space, _ . ' -)")
    ap.add_argument("--room", default=DEFAULT_ROOM, help="room to join (default: default)")
    ap.add_argument("--game-port", type=int, default=GAME_PORT, help=f"the game's control port (default {GAME_PORT})")
    ap.add_argument("--hz", type=float, default=DEFAULT_HZ, help="updates per second (default 20, max 25)")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="draw others this many seconds late (default 0.1)")
    ap.add_argument("--no-ui", action="store_true", help="no window: print distances once a second")
    ap.add_argument("--record", metavar="FILE", help="also save your path to this JSONL file, replacing it (for bot.py --replay)")
    ap.add_argument("--ghost", action="store_true",
                    help="also show the nearest other player in your game, as an actor (needs the ghost mod: docs/ghost.md)")
    ap.add_argument("--ghost-peer", metavar="NAME", help="show this player instead of the nearest (implies --ghost)")
    ap.add_argument("--ghost-actor", metavar="ACTOR",
                    help="the actor that stands for them (default: the mod's, a Sheikah man; implies --ghost)")
    ap.add_argument("--ghost-sync", action="store_true",
                    help="also share animations (attacks too) and say what the other player holds; needs ghost mod "
                         "0.5.0 on both PCs (implies --ghost; not yet tested in the game)")
    ap.add_argument("--ghost-link", action="store_true",
                    help="show them with the Link body (build and deploy it first: tools/mp/ghost_link_pack.py; "
                         "implies --ghost-sync; not yet tested in the game)")
    ap.add_argument("--ghost-items", action="store_true",
                    help="the ghost also holds what they hold (weapon, shield, bow); needs --ghost-link and ghost mod "
                         "0.6.0 on your PC (implies --ghost-sync; docs/ghost.md)")
    args = ap.parse_args(argv)
    safe_console()
    name = valid_name(args.name) if args.name is not None else default_name()
    if name is None:
        ap.error("bad --name (1-20 letters, digits, space, _ . ' -)")
    if valid_room(args.room) is None:
        ap.error("bad --room (1-32 of A-Z a-z 0-9 _ -)")
    if not 0 < args.hz <= 25:
        ap.error("--hz must be above 0 and at most 25 (the relay allows 30 messages a second)")
    if not 0 <= args.delay <= 2:
        ap.error("--delay must be from 0 to 2 seconds")
    try:
        relay = parse_hostport(args.relay)
    except ValueError as e:
        ap.error(str(e))
    if args.ghost_peer is not None and valid_name(args.ghost_peer) is None:
        ap.error("bad --ghost-peer (1-20 letters, digits, space, _ . ' -)")
    if args.ghost_actor is not None and valid_actor(args.ghost_actor) is None:
        ap.error("bad --ghost-actor (an actor name: letters, digits and _, up to 63)")
    if args.ghost_link and args.ghost_actor is not None:
        ap.error("--ghost-link and --ghost-actor both pick the actor: use one")
    ghost = None
    sync = args.ghost_sync or args.ghost_link or args.ghost_items
    if args.ghost or args.ghost_peer is not None or args.ghost_actor is not None or sync:
        ghost = Ghost(args.ghost_peer, GHOST_LINK_ACTOR if args.ghost_link else args.ghost_actor, sync=sync,
                      items=args.ghost_items)
    if args.ghost_items and not args.ghost_link:
        print("--ghost-items: only the Link body has the hand bones the items go on; without --ghost-link the ghost "
              "holds nothing.")
    if args.ghost_link:
        print("--ghost-link: the ghost looks like Link only with the Link body deployed (build it with "
              "python tools\\mp\\ghost_link_pack.py build; docs/ghost.md). Without it you see the Sheikah man. "
              "Checked in the game: python tools\\mp\\ghost_ingame_check.py --link (docs/ghost.md, tenth round).")

    ui = not args.no_ui
    if ui:
        try:
            import tkinter  # noqa: F401
        except ImportError:
            print("No tkinter in this Python; printing instead of a window.")
            ui = False
    runner = Runner(relay, name, args.room, game_port=args.game_port, hz=args.hz, delay=args.delay,
                    record=args.record, print_every=None if ui else 1.0, ghost=ghost)
    try:
        runner.open_record()
    except OSError as e:
        ap.error(f"can't write --record {args.record}: {e.strerror or e}")
    print(f"{name} joining room {args.room} on {relay[0]}:{relay[1]}; game on port {args.game_port}.")
    if not ui:
        try:
            asyncio.run(runner.run())
        except KeyboardInterrupt:
            pass
        print("Stopped.")
        return 0

    def network():
        try:
            asyncio.run(runner.run())
        except Exception as e:   # (the window stays open: show the error there instead of a stale status)
            runner.failure = f"{type(e).__name__}: {str(e)[:200]}"
            print(f"Stopped after an error: {runner.failure}")

    thread = threading.Thread(target=network, name="network", daemon=True)
    thread.start()
    while runner._stop is None and thread.is_alive():
        time.sleep(0.01)
    try:
        run_ui(runner)
    finally:
        runner.stop()
        thread.join(8.0)
    print("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
