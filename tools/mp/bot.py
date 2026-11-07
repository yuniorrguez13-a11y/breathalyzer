#!/usr/bin/env python3
"""A fake second player for testing the multiplayer client alone. It needs no game.

    python tools/mp/bot.py                                  circle 10 m around the first real player who joins
    python tools/mp/bot.py --at -1000 250 1800              circle around a fixed point
    python tools/mp/bot.py --replay out/paths/walk.jsonl    walk a path saved by client.py --record, starting where
                                                            the first real player stood when the bot picked them (the
                                                            start stays there), or --at X Y Z, or --absolute: as recorded
    python tools/mp/bot.py --anim "Wait:2,Move@2:3,Move@6:3,CutS1:0.7,CutS2:0.7" --weapon Weapon_Sword_001
                                                            also send these animation names in a loop (NAME[@m/s][:s]),
                                                            as client.py --ghost-sync does; @m/s sets the circle's speed
    python tools/mp/bot.py --weapon Weapon_Sword_001 --shield Weapon_Shield_001 --bow Weapon_Bow_001 --on-back 4
                                                            what it holds, and which of it is on its back (bit 0 weapon,
                                                            1 shield, 2 bow), as client.py sends them (--ghost-items)
    python tools/mp/bot.py --items "Weapon_Sword_001/Weapon_Shield_001/Weapon_Bow_001/0:5,-/-/-/0:3"
                                                            ... changing in a loop (WEAPON/SHIELD/BOW/MASK:seconds,
                                                            - for none)

It joins the relay like client.py (marked as a bot) and sends 20 states a second, each with its speed measured from
its own positions, as client.py does.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path

_TOOLS = str(Path(__file__).resolve().parent.parent)
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
from mp import (DEFAULT_RELAY_PORT, DEFAULT_ROOM, clean_state, parse_hostport, safe_console, valid_anim,  # noqa: E402
                valid_name, valid_on_back, valid_room)
from mp.client import DEFAULT_HZ, RelayLink, Session, SpeedMeter, _sleep, interpolate  # noqa: E402


MAX_GAP = 1.0                 # no position for longer than this (s): a loading screen or a pause. Jump, don't glide.
GAP_FACTOR = 10.0             # ... and longer than this many usual steps between positions in the file
JUMP_STEP = 1.0 / DEFAULT_HZ  # what is left of such a gap in the replay (s)


class CirclePath:
    """Walk a circle of `radius` metres at `speed` m/s around a centre, facing the way it walks."""

    follows = True     # the centre moves with the player it circles

    def __init__(self, radius: float = 10.0, speed: float = 3.0):
        if radius <= 0 or speed < 0:
            raise ValueError("radius must be above 0 and speed at least 0")
        self.radius, self.speed = radius, speed

    def describe(self) -> str:
        return f"circling {self.radius:g} m"

    def at(self, elapsed: float, center: tuple[float, float, float], arc: float | None = None) -> dict:
        """Where it is `elapsed` seconds in, or (arc given) after walking arc metres (an --anim script's speeds)."""
        a = (self.speed * elapsed if arc is None else arc) / self.radius
        cx, cy, cz = center
        return {"x": cx + self.radius * math.sin(a), "y": cy, "z": cz + self.radius * math.cos(a),
                "yaw": math.degrees(math.atan2(math.cos(a), -math.sin(a)))}


class ReplayPath:
    """Play back a path saved by client.py --record, in a loop. Shifted so it starts at the centre, if one is given.

    A file can hold several recordings (older clients appended to it). The time going back marks a new one (the PC
    restarted in between); only the last recording with at least two positions is played. Inside it, a gap with no
    position longer than `max_gap` seconds and 10 usual steps (a loading screen, fast travel, a pause, or two
    appended sessions) is cut out of the timeline and jumped over instead of glided across.
    """

    follows = False    # the start point is fixed when the bot first picks a player

    def __init__(self, samples: list[dict], name: str = "path", *, max_gap: float = MAX_GAP, skipped: int = 0):
        runs, cur = [], []
        self.skipped = skipped          # lines that were not usable positions, and repeats
        for s in samples:
            st = clean_state(s)
            if st is None:
                self.skipped += 1
                continue
            if cur and st["t"] <= cur[-1]["t"]:
                if st["t"] == cur[-1]["t"]:
                    self.skipped += 1
                    continue
                runs.append(cur)        # the clock went back: a new recording
                cur = []
            cur.append(st)
        if cur:
            runs.append(cur)
        usable = [r for r in runs if len(r) >= 2]
        if not usable:
            raise ValueError("a path needs at least two samples with increasing t")
        self.recordings = len(runs)
        self.played_last = usable[-1] is runs[-1]
        clean = usable[-1]
        steps = sorted(b["t"] - a["t"] for a, b in zip(clean, clean[1:]))
        limit = max(max_gap, GAP_FACTOR * steps[len(steps) // 2])   # (a path saved at a low rate has no gaps)
        self.gaps = 0
        cut = clean[0]["t"]             # how much to take off each t: the start, plus every gap cut out so far
        prev = None
        for st in clean:
            raw = st["t"]
            if prev is not None and raw - prev > limit:
                cut += raw - prev - JUMP_STEP
                st["jump"] = True       # arrive here at once, don't glide from the sample before
                self.gaps += 1
            st["t"] = raw - cut
            prev = raw
        self.samples, self.name = clean, name
        self.duration = clean[-1]["t"]

    @classmethod
    def load(cls, path: str | Path) -> "ReplayPath":
        samples, skipped = [], 0
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, RecursionError):
                    skipped += 1
                    continue
                if isinstance(obj, dict):
                    samples.append(obj)
                else:
                    skipped += 1
        return cls(samples, Path(path).name, skipped=skipped)

    def describe(self) -> str:
        return f"replaying {self.name} ({self.duration:.0f} s)"

    def notes(self) -> list[str]:
        """Things worth telling the user about the file."""
        out = []
        if self.recordings > 1:
            which = "the last one" if self.played_last else "the last one with at least two positions"
            out.append(f"{self.name} holds {self.recordings} recordings; replaying {which}.")
        if self.gaps:
            out.append(f"Jumping over {self.gaps} gap{'s' if self.gaps != 1 else ''} with no position "
                       f"(loading screens or pauses).")
        if self.skipped:
            out.append(f"Skipped {self.skipped} unusable line{'s' if self.skipped != 1 else ''}.")
        return out

    def at(self, elapsed: float, center: tuple[float, float, float] | None = None) -> dict:
        e = elapsed % self.duration if self.duration > 0 else 0.0
        s = self.samples
        lo, hi = 0, len(s) - 1
        while hi - lo > 1:                      # last sample at or before e
            mid = (lo + hi) // 2
            if s[mid]["t"] <= e:
                lo = mid
            else:
                hi = mid
        a, b = s[lo], s[lo + 1]
        if b.get("jump") and e < b["t"]:
            p = a                               # (hold until a jump, then be there at once)
        else:
            p = interpolate(a, b, min(1.0, max(0.0, (e - a["t"]) / (b["t"] - a["t"]))))
        out = {"x": p["x"], "y": p["y"], "z": p["z"], "yaw": p.get("yaw")}
        if center is not None:
            first = s[0]
            out["x"] += center[0] - first["x"]
            out["y"] += center[1] - first["y"]
            out["z"] += center[2] - first["z"]
        return out


class AnimScript:
    """--anim: animation names to send in a loop, as client.py --ghost-sync sends its player's (`anim`, and `attack`
    for an attack). Each step is NAME, NAME:SECONDS (default 1) or NAME@SPEED:SECONDS: while it lasts the circle is
    walked at SPEED m/s (a step without @ keeps the --speed). Example: "Wait:2,Move@2:3,Move@6:3,CutS1:0.7"."""

    def __init__(self, steps: list[tuple[str, float | None, float]]):
        if not steps:
            raise ValueError("an --anim script needs at least one step")
        self.steps = steps
        self.duration = sum(d for _, _, d in steps)

    @classmethod
    def parse(cls, text: str) -> "AnimScript":
        steps = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            head, _, secs = part.partition(":")
            name, _, speed = head.partition("@")
            try:
                d = float(secs) if secs else 1.0
                v = float(speed) if speed else None
            except ValueError:
                raise ValueError(f"bad --anim step {part!r} (NAME, NAME:SECONDS or NAME@SPEED:SECONDS)") from None
            if valid_anim(name) is None:
                raise ValueError(f"bad animation name {name!r} (letters, digits and _, up to 31)")
            if not 0 < d <= 3600 or (v is not None and not 0 <= v <= 50):
                raise ValueError(f"bad --anim step {part!r} (0-3600 s, 0-50 m/s)")
            steps.append((name, v, d))
        return cls(steps)

    def at(self, elapsed: float) -> tuple[str, float | None]:
        """(name, speed or None) of the step `elapsed` seconds in (looping)."""
        e = elapsed % self.duration
        for name, v, d in self.steps:
            if e < d:
                return name, v
            e -= d
        return self.steps[-1][0], self.steps[-1][1]


class ItemScript:
    """--items: what it holds, changing in a loop. Each step is WEAPON/SHIELD/BOW/MASK:SECONDS (- for none; MASK: which
    of them are on its back, bit 0 weapon, 1 shield, 2 bow; SECONDS default 1). Example:
    "Weapon_Sword_001/Weapon_Shield_001/Weapon_Bow_001/0:5,Weapon_Lsword_001/-/Weapon_Bow_001/1:5,-/-/-/0:3"."""

    def __init__(self, steps: list[tuple[str, str, str, int, float]]):
        if not steps:
            raise ValueError("an --items script needs at least one step")
        self.steps = steps
        self.duration = sum(st[4] for st in steps)

    @classmethod
    def parse(cls, text: str) -> "ItemScript":
        steps = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            head, _, secs = part.partition(":")
            fields = head.split("/")
            if len(fields) != 4:
                raise ValueError(f"bad --items step {part!r} (WEAPON/SHIELD/BOW/MASK:SECONDS, - for none)")
            names = []
            for f in fields[:3]:
                f = f.strip()
                if f != "-" and not valid_item(f):
                    raise ValueError(f"bad item name {f!r} (letters, digits and _, up to 63; - for none)")
                names.append("" if f == "-" else f)
            try:
                mask = int(fields[3])
                d = float(secs) if secs else 1.0
            except ValueError:
                raise ValueError(f"bad --items step {part!r} (MASK 0-7, SECONDS a number)") from None
            if valid_on_back(mask) is None or not 0 < d <= 3600:
                raise ValueError(f"bad --items step {part!r} (MASK 0-7, 0-3600 s)")
            steps.append((names[0], names[1], names[2], mask, d))
        return cls(steps)

    def at(self, elapsed: float) -> tuple[str, str, str, int]:
        """(weapon, shield, bow, mask) of the step `elapsed` seconds in (looping); "" for none."""
        e = elapsed % self.duration
        for w, sh, b, m, d in self.steps:
            if e < d:
                return w, sh, b, m
            e -= d
        w, sh, b, m, _ = self.steps[-1]
        return w, sh, b, m


def valid_item(name) -> bool:
    """An item's actor name as the state carries it: letters, digits and _, 1-63."""
    return isinstance(name, str) and 0 < len(name) <= 63 and all(c.isascii() and (c.isalnum() or c == "_")
                                                                for c in name)


def is_attack(name: str) -> bool:
    """An attack, by the player's own names (out/research/player-state-plan.md): Cut..., SwimAttack, the bow."""
    return name.startswith("Cut") or name in ("SwimAttack", "BowCharge", "BowShoot")


def first_real_peer(session: Session, now: float, keep: int | None = None) -> tuple[int, str, tuple] | None:
    """The player to walk around: `keep` while it is still there, else the real (not bot) player who joined first."""
    with session.lock:
        ids = sorted(session.peers)
        if keep in session.peers:
            ids = [keep] + ids
        for pid in ids:
            peer = session.peers[pid]
            if peer.bot:
                continue
            s = peer.buffer.sample(now)
            if s is not None:
                return pid, peer.name, (s["x"], s["y"], s["z"])
    return None


async def run_bot(relay: tuple[str, int], name: str = "bot", room: str = DEFAULT_ROOM, *, path=None,
                  center: tuple[float, float, float] | None = None, absolute: bool = False, hz: float = DEFAULT_HZ,
                  stop: asyncio.Event | None = None, clock=time.monotonic, log=print, anims: AnimScript | None = None,
                  weapon: str | None = None, shield: str | None = None, bow: str | None = None,
                  on_back: int | None = None, items: ItemScript | None = None) -> int:
    """Send states until `stop` is set. Returns how many were sent. anims: names to send (--anim); weapon, shield,
    bow: item names to send (--weapon, --shield, --bow); on_back: the mask to send (--on-back); items: what it holds,
    changing in a loop (--items: replaces weapon, shield, bow and on_back)."""
    path = path or CirclePath()
    stop = stop or asyncio.Event()
    session = Session(name, room)
    link = RelayLink(relay[0], relay[1], name, room, bot=True, log=log, clock=clock,
                     on_message=lambda m: session.handle(m, clock()))
    task = asyncio.create_task(link.run(stop))
    t0 = None
    following = None
    anchor = None          # where a replay starts: the followed player's position when the bot picked them
    waiting_said = False
    sent = 0
    meter = SpeedMeter()   # its speed, as client.py measures a player's
    arc, last = 0.0, None  # (an --anim script with speeds: the distance walked so far, and when)
    try:
        while not stop.is_set():
            now = clock()
            c = center
            if absolute and isinstance(path, ReplayPath):
                c = None
            elif c is None:
                found = first_real_peer(session, now, following)
                if found is None:
                    if following is not None:
                        log("The player left; waiting for another one.")
                        following, waiting_said = None, True
                    elif link.connected and not waiting_said:
                        log(f"Waiting for a player with a position in room {link.room}.")
                        waiting_said = True
                else:
                    follows = getattr(path, "follows", True)
                    if found[0] != following:
                        where = f"around {found[1]}" if follows else f"from where {found[1]} stands"
                        log(f"{name}: {path.describe()} {where}.")
                        following, anchor = found[0], found[2]
                        if not follows:
                            t0 = None   # (a new player: the replay starts again, where they stand)
                    c = found[2] if follows else anchor
            if c is not None or (absolute and isinstance(path, ReplayPath)):
                if t0 is None:
                    t0, arc, last = now, 0.0, None
                    if center is not None or absolute:
                        log(f"{name}: {path.describe()}" + (f" around {center[0]:g} {center[1]:g} {center[2]:g}."
                                                            if center is not None else " where it was recorded."))
                step = anims.at(now - t0) if anims is not None else None
                if step is not None and isinstance(path, CirclePath):
                    v = path.speed if step[1] is None else step[1]
                    arc += v * (now - last) if last is not None else 0.0
                    last = now
                    p = path.at(now - t0, c, arc)
                else:
                    p = path.at(now - t0, c)
                msg = {"type": "state", "t": round(now, 3), "x": round(p["x"], 3), "y": round(p["y"], 3),
                       "z": round(p["z"], 3), "yaw": None if p.get("yaw") is None else round(p["yaw"], 1),
                       "stamina": None}
                msg["speed"] = round(meter.update(msg["x"], msg["z"], msg["t"]), 2)
                if step is not None:
                    msg["anim"] = step[0]
                    if is_attack(step[0]):
                        msg["attack"] = True
                held = {"weapon": weapon, "shield": shield, "bow": bow, "on_back": on_back}
                if items is not None:
                    w, sh, b, m = items.at(now - t0)
                    held = {"weapon": w, "shield": sh, "bow": b, "on_back": m}
                msg.update((k, v) for k, v in held.items() if v is not None)
                if link.send(msg):
                    sent += 1
            await _sleep(stop, 1.0 / hz)
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, 3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass
    return sent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A fake player for testing the multiplayer client alone.")
    ap.add_argument("--relay", default="127.0.0.1", help=f"relay address, HOST or HOST:PORT (default port {DEFAULT_RELAY_PORT})")
    ap.add_argument("--name", default="bot", help="bot name (default bot)")
    ap.add_argument("--room", default=DEFAULT_ROOM, help="room to join (default: default)")
    ap.add_argument("--at", nargs=3, type=float, metavar=("X", "Y", "Z"), help="walk around this point")
    ap.add_argument("--radius", type=float, default=10.0, help="circle radius in metres (default 10)")
    ap.add_argument("--speed", type=float, default=3.0, help="walking speed in m/s (default 3)")
    ap.add_argument("--replay", metavar="FILE", help="walk a path saved with client.py --record")
    ap.add_argument("--absolute", action="store_true", help="with --replay: walk the path where it was recorded")
    ap.add_argument("--hz", type=float, default=DEFAULT_HZ, help="states per second (default 20, max 25)")
    ap.add_argument("--anim", metavar="SCRIPT",
                    help='also send these animation names in a loop, as --ghost-sync does: "Wait:2,Move@6:3,CutS1:0.7" '
                         "(NAME[@m/s][:seconds]; @ sets the circle's speed)")
    ap.add_argument("--weapon", metavar="ACTOR", help="also send this as the weapon it holds (e.g. Weapon_Sword_001)")
    ap.add_argument("--shield", metavar="ACTOR", help="... and this as its shield (e.g. Weapon_Shield_001)")
    ap.add_argument("--bow", metavar="ACTOR", help="... and this as its bow (e.g. Weapon_Bow_001)")
    ap.add_argument("--on-back", type=int, metavar="MASK",
                    help="... and which of them are on its back: bit 0 weapon, 1 shield, 2 bow (0-7)")
    ap.add_argument("--items", metavar="SCRIPT",
                    help='what it holds, changing in a loop: "W/S/B/MASK:seconds,..." (- for none), e.g. '
                         '"Weapon_Sword_001/Weapon_Shield_001/Weapon_Bow_001/0:5,-/-/-/0:3"')
    args = ap.parse_args(argv)
    safe_console()
    if valid_name(args.name) is None:
        ap.error("bad --name (1-20 letters, digits, space, _ . ' -)")
    if valid_room(args.room) is None:
        ap.error("bad --room (1-32 of A-Z a-z 0-9 _ -)")
    if not 0 < args.hz <= 25:
        ap.error("--hz must be above 0 and at most 25")
    if args.absolute and not args.replay:
        ap.error("--absolute only works with --replay")
    for flag in ("weapon", "shield", "bow"):
        v = getattr(args, flag)
        if v is not None and v != "" and not valid_item(v):
            ap.error(f"bad --{flag} (an actor name: letters, digits and _, up to 63)")
    if args.on_back is not None and valid_on_back(args.on_back) is None:
        ap.error("bad --on-back (0-7: bit 0 weapon, 1 shield, 2 bow)")
    if args.items and (args.weapon or args.shield or args.bow or args.on_back is not None):
        ap.error("--items sets what it holds: leave out --weapon, --shield, --bow and --on-back")
    try:
        relay = parse_hostport(args.relay)
        path = ReplayPath.load(args.replay) if args.replay else CirclePath(args.radius, args.speed)
        anims = AnimScript.parse(args.anim) if args.anim else None
        items = ItemScript.parse(args.items) if args.items else None
    except (ValueError, OSError) as e:
        ap.error(str(e))
    print(f"Bot {args.name} joining room {args.room} on {relay[0]}:{relay[1]}. Ctrl+C stops it.")
    for note in path.notes() if isinstance(path, ReplayPath) else ():
        print(note)
    try:
        asyncio.run(run_bot(relay, valid_name(args.name), args.room, path=path,
                            center=tuple(args.at) if args.at else None, absolute=args.absolute, hz=args.hz,
                            anims=anims, weapon=args.weapon, shield=args.shield, bow=args.bow,
                            on_back=args.on_back, items=items))
    except KeyboardInterrupt:
        pass
    print("Bot stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
