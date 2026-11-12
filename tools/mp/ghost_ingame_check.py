#!/usr/bin/env python3
"""In-game checks for ghost v2 (docs/ghost.md): animation sync, `ghost self` and the Link body. Run it only when
you may start the game: it starts Savage Wind once, drives Link with scripted input, runs a relay and a bot on this
PC, and prints PASS, FAIL or SKIP per check. About 3 minutes; the game is closed at the end.

    python tools/mp/ghost_ingame_check.py               every check with the Sheikah man as the stand-in
    python tools/mp/ghost_ingame_check.py --link        also build the Link body (ghost_link_pack.py), deploy it
                                                        to out\\ghost-check-overlay and check it
    python tools/mp/ghost_ingame_check.py --no-launch   use a game that is already running (control port 7777, the
                                                        ghost mod loaded, a save loaded); it is left running
    python tools/mp/ghost_ingame_check.py --give-weapon when Link has no weapon: at the end the ghost mod spawns a
                                                        sword in front of him and he takes it (S6, S9)
    python tools/mp/ghost_ingame_check.py --items --spot -1033.6 252.3 1809.3 132 --give-weapon --lib out\\ghost-items-lib
                                                        the held items' checks instead (H1-H16, implies --link:
                                                        ghost_items_check.py); --lib: a scratch mod library (the
                                                        ghost.dll and the Link body are copied there, never to your
                                                        own library)

Before: python tools\\build_native.py build (ghost.dll 0.6.2). The game starts with the test save in out\\test-save
when that folder exists (so your own save slot isn't touched), else with your saves. Results also go to
out\\research\\ghost-ingame-results.txt and screenshots to out\\shots\\ghostcheck\\ (never committed).

Checks (plans: out/research/player-state-plan.md, out/research/ghost-link-plan.md):
  S1 Link found by `ghost self` (his class)          S5 jump: Jump, Fall or Land seen
  S2 animation holder shape 2,2,1,3,1                S6 attack (Y): a Cut... name and attack true (SKIP: no weapon)
  S3 standing: Wait                                  S7 weapon, shield, bow: actor names or empty, never "?"
  S4 walk, run, sprint: Move, faster each time       S8 cost: frames a second with `ghost self` 20 times a second
  L1 Link body built and deployed (--link)           L3 Walk, Run, RunGear2 play on it
  L2 `ghost spawn link`: live, Wait and CutS1 play   L4 CutS1, CutSF, Jump, ParashawlGlide, ClimbMove, SwimMove play
     (CutS1: the Sheikah man has none, so it shows the Link body loaded)
  L5 frames a second with and without the ghost      R1 the bot's animation names reach the ghost and show (or
  R2 your own state carries your animation name          fall back to Wait/Walk/Run on the Sheikah man)
  S9 (--give-weapon) the sword Link took reads as its actor name, of the Weapon class
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

_TOOLS = str(Path(__file__).resolve().parent.parent)
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
from control import Control, ControlError, wait_for_game  # noqa: E402
from mp import client  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
GAME_DIR = Path.home() / "Documents" / "SavageWind" / "Savage Wind"
TEST_SAVE = REPO / "out" / "test-save"
OVERLAY = REPO / "out" / "ghost-check-overlay"
SHOTS = REPO / "out" / "shots" / "ghostcheck"
RESULTS = REPO / "out" / "research" / "ghost-ingame-results.txt"
RELAY_PORT = 7790          # (not the usual 7788, so a relay you run for play doesn't clash)
WANT_VERSION = "0.7.0"
SHAPE = (2, 2, 1, 3, 1)
LINK_NAMES = ("Wait", "Walk", "Run", "RunGear2", "CutS1", "CutSF", "Jump", "ParashawlGlide", "ClimbMove", "SwimMove")
# The bot's names for R1 (NAME@m/s:seconds): stand, walk, run, three attacks, a jump, a glide, a climb, stand
BOT_SCRIPT = ("Wait@0:3,Move@2:4,Move@6:4,CutS1@0:1.5,CutS2@0:1.5,CutDash@0:1.5,Jump@0:1.5,ParashawlGlide@0:2,"
              "ClimbMove@0.5:2,Wait@0:2")
WALKS = {"Wait", "Walk", "Run"}
GIVE_WEAPON = "Weapon_Sword_001"   # (--give-weapon: a Traveler's Sword)
SPOT = (-1033.6, 252.3, 1809.3, 132.0)   # (--spot on the test save: Great Plateau meadow; a 3 s sprint that way falls
#                                          off the edge, so only the 2.4 s runs of S4)


# ------------------------------------------------------------------ judging (pure: tests/test_mp_sync.py)

def judge_shape(shape: str) -> tuple[str, str]:
    """S2: the holder's subs per slot, as `ghost self` says them ("2,2,1,3,1")."""
    try:
        got = tuple(int(x) for x in shape.split(",")) if shape else ()
    except ValueError:
        return "FAIL", f"unreadable shape {shape!r}"
    if got[:len(SHAPE)] == SHAPE:
        return "PASS", f"subs per slot {shape}"
    return "FAIL", f"subs per slot {shape or 'none'} (expected 2,2,1,3,1: slot meanings may differ)"


def judge_moves(runs: dict[str, tuple[set[str], float]]) -> tuple[str, str]:
    """S4: {"walk"|"run"|"sprint": (body names seen, mean speed m/s)}. Move in all three, each faster."""
    text = "; ".join(f"{k}: {'/'.join(sorted(v[0])) or '-'} at {v[1]:.1f} m/s" for k, v in runs.items())
    if not all("Move" in runs.get(k, (set(), 0))[0] for k in ("walk", "run", "sprint")):
        return "FAIL", "no Move in every one: " + text
    w, r, s = (runs[k][1] for k in ("walk", "run", "sprint"))
    if not 0.3 < w < r < s:
        return "FAIL", "speeds not walk < run < sprint: " + text
    return "PASS", text


def judge_item(name) -> bool:
    """S7: an item name `ghost self` gave is an actor name or empty."""
    return isinstance(name, str) and (name == "" or client.valid_actor(name) is not None)


def judge_frames(base: float | None, other: float | None, drop: float = 3.0) -> tuple[str, str]:
    """S8 / L5: frames a second without and with the thing measured."""
    if base is None or other is None:
        return "SKIP", "no frame rate"
    return ("PASS" if base - other <= drop else "FAIL"), f"{base:.1f} -> {other:.1f} frames a second"


def judge_replay(samples: list[tuple[str, str]], names: list[str], link: bool, missing: str) -> tuple[str, str]:
    """R1: samples of the mod's (remote, as) while the bot sent `names`. Link body: each name the body has (not Wait
    or Move) showed as it is while it was the remote one. Sheikah man: it only ever showed Wait/Walk/Run, and the
    names it lacks were noted (remote_missing)."""
    seen = {r for r, _ in samples if r}
    if not seen:
        return "FAIL", "no animation name reached the mod (`remote` always empty)"
    special = [n for n in names if n not in ("Wait", "Move")]
    if link:
        shown = {n for n in special if any(r == n and a == n for r, a in samples)}
        lost = [n for n in special if n not in shown]
        return ("PASS" if not lost else "FAIL"), f"shown {sorted(shown)}" + (f"; never shown {lost}" if lost else "")
    odd = sorted({a for _, a in samples if a and a not in WALKS})
    noted = set(filter(None, missing.split(",")))
    unnoted = [n for n in special if n in seen and n not in noted]
    if odd or unnoted:
        return "FAIL", f"showed {odd}; not noted as missing {unnoted}"
    return "PASS", f"names reached the mod ({len(seen)}); shown only Wait/Walk/Run; noted missing {sorted(noted)}"


def judge_link_spawn(wait: dict, cut: dict) -> tuple[str, str]:
    """L2: the `ghost` replies after `ghost spawn link` + `ghost anim Wait`, then `ghost anim CutS1`. The Sheikah man
    (what spawns when the Link body didn't load) has Wait, Walk, Run and RunGear2 too, but no CutS1: so CutS1 playing
    is what shows the Link body is there."""
    text = (f"state {wait.get('state')}, as_ok {wait.get('as_ok')}, as {wait.get('as')!r}, then {cut.get('as')!r} "
            "for CutS1 (look at the screenshot: Link's body and face?)")
    ok = wait.get("state") == "live" and wait.get("as_ok") is True and wait.get("as") == "Wait"
    if ok and cut.get("as") != "CutS1":
        return "FAIL", text + "; no CutS1: the Sheikah man, not the Link body (is the pack deployed?)"
    return ("PASS" if ok else "FAIL"), text


def judge_own(anims: list[str]) -> tuple[str, str]:
    """R2: the `anim` of the states this client sent while Link stood, then ran."""
    got = set(anims)
    if {"Wait", "Move"} <= got:
        return "PASS", f"sent {sorted(got)}"
    return "FAIL", f"sent {sorted(got) or 'no anim'} (expected Wait and Move)"


# ------------------------------------------------------------------ the run

class Report:
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []

    def add(self, check: str, status: str, text: str) -> None:
        self.rows.append((check, status, text))
        print(f"{status} {check}: {text}", flush=True)

    def summary(self) -> str:
        n = {s: sum(1 for _, st, _ in self.rows if st == s) for s in ("PASS", "FAIL", "SKIP")}
        return f"{n['PASS']} PASS, {n['FAIL']} FAIL, {n['SKIP']} SKIP"


def game_env(port: int, overlay: dict[str, str] | None, mods: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["SAVAGEWIND_GAME_LIB"] = str(REPO / "build" / "native" / "modhost.dll")
    env["SWMOD_DIR"] = str(mods or REPO / "build" / "native" / "ghost-mods")
    env["SAVAGEWIND_CONTROL"] = str(port)
    for k in ("SWMOD_REAL_GAME_LIB", "SAVAGEWIND_ROMFS_OVERLAY", "SAVAGEWIND_NO_HUD_LETTERS"):
        env.pop(k, None)
    env.update(overlay or {})
    if TEST_SAVE.is_dir():
        env["SAVAGEWIND_SAVE_DIR"] = str(TEST_SAVE)
    return env


def deploy_link(game_dir: Path, overlay: Path = OVERLAY) -> dict[str, str]:
    """L1: build the Link body into out\\ghost-link-mod and deploy it alone to `overlay`. Returns the game's env."""
    from mp import ghost_link_pack as glp
    romfs = game_dir / "extracted" / "romfs"
    r = glp.build(romfs, glp.DEFAULT_OUT)
    py = [sys.executable, str(REPO / "tools" / "romfs_mods.py")]
    subprocess.run(py + ["deploy", "--out", str(overlay), "--game-romfs", str(romfs), str(glp.DEFAULT_OUT)],
                   check=True)
    out = subprocess.run(py + ["env", "--out", str(overlay)], check=True, capture_output=True, text=True).stdout
    env = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    env["_built"] = f"{r['unpacked']:,} bytes unpacked (limit {glp.SIZE_LIMIT:,})"
    return env


def game_pids() -> set[int]:
    """The process ids of every running savagewind.exe (tasklist)."""
    try:
        out = subprocess.run(["tasklist", "/fi", "imagename eq savagewind.exe", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    pids = set()
    for row in csv.reader(out.splitlines()):
        if len(row) > 1 and row[0].lower() == "savagewind.exe" and row[1].isdigit():
            pids.add(int(row[1]))
    return pids


def start_game_alone(cmd: list[str], cwd: Path, env: dict, poll: float = 30.0, max_wait: float = 7200.0,
                     watch: float = 20.0, log=print) -> subprocess.Popen | None:
    """Start the game only while no other copy runs: one that another program started is never touched; the check
    waits, asking every `poll` s, at most max_wait s. If another copy shows up within `watch` s of ours (both started
    at once), ours (still loading) is closed and the wait goes on. None: no free moment within max_wait."""
    t0, said = time.monotonic(), False
    while time.monotonic() - t0 < max_wait:
        others = game_pids()
        if others:
            if not said:
                log(f"Another game runs (process {sorted(others)}): waiting, asking every {poll:g} s.")
                said = True
            time.sleep(poll)
            continue
        game = subprocess.Popen(cmd, cwd=cwd, env=env)
        end, clash = time.monotonic() + watch, set()
        while time.monotonic() < end and not clash and game.poll() is None:
            clash = game_pids() - {game.pid}
            time.sleep(0.5)
        if not clash:
            log(f"Game started (process {game.pid}) after {time.monotonic() - t0:.0f} s.")
            return game
        log(f"Another game started at the same time (process {sorted(clash)}): closing ours, waiting again.")
        game.kill()   # (ours, still loading; never the other one)
        game.wait(30)
        said = False
    return None


def wait_world(c: Control, timeout: float = 240.0) -> bool:
    """Until `pos` answers (a save loaded): press A every 2.5 s meanwhile (title screen: Continue)."""
    end, press_at = time.monotonic() + timeout, 0.0
    while time.monotonic() < end:
        try:
            c.cmd("pos")
            return True
        except ControlError:
            pass
        if time.monotonic() >= press_at:
            press_at = time.monotonic() + 2.5
            try:
                c.cmd("press", "a")
            except ControlError:
                pass
        time.sleep(0.5)
    return False


def fps(c: Control, secs: int = 5) -> float | None:
    time.sleep(secs)
    try:
        r = c.cmd("frames", str(secs))
    except ControlError:
        return None
    v = r.get("fps_avg") if isinstance(r, dict) else None
    return float(v) if isinstance(v, (int, float)) else None


def shot(c: Control, name: str) -> None:
    try:
        c.cmd("shot", f"ghostcheck_{name}.png")
    except ControlError:
        pass


def sample_self(c: Control, secs: float) -> tuple[list[dict], float]:
    """`ghost self` and `pos` 20 times a second for secs: (self replies, mean ground speed from the positions)."""
    out, meter, speeds = [], client.SpeedMeter(), []
    end = time.monotonic() + secs
    while time.monotonic() < end:
        try:
            pos, me = c.batch([["pos"], ["ghost", "self"]], errors=True)
        except ControlError:
            break
        if isinstance(me, dict):
            out.append(me)
        if isinstance(pos, dict):
            speeds.append(meter.update(float(pos["x"]), float(pos["z"]), time.monotonic()))
        time.sleep(0.05)
    tail = speeds[len(speeds) // 3:]          # (after it got going)
    return out, (sum(tail) / len(tail) if tail else 0.0)


def climbing(c: Control) -> bool:
    """Link is on a wall or a ladder (`ghost self` body name). A teleport then crashed the game once."""
    try:
        me = c.cmd("ghost", "self")
    except ControlError:
        return False
    return any(str(me.get(k, "")).startswith(("Climb", "Ladder")) for k in ("body", "anim"))


def back_to(c: Control, where: tuple[float, float, float]) -> None:
    try:
        c.cmd("release", "all")   # (buttons and sticks)
        time.sleep(0.5)           # (teleport wants Link standing still)
        end = time.monotonic() + 8
        while climbing(c):        # (never teleport while Link climbs: let go with B, wait till he lands)
            if time.monotonic() > end:
                print("  Link still climbing: no teleport back", flush=True)
                return
            c.cmd("press", "b")
            time.sleep(1.5)
        c.cmd("teleport", f"{where[0]:.2f}", f"{where[1] + 0.3:.2f}", f"{where[2]:.2f}")
        end = time.monotonic() + 20
        while time.monotonic() < end and c.cmd("teleport", "status").get("state") in client.TP_ACTIVE:
            time.sleep(0.3)
    except ControlError:
        pass
    time.sleep(1.0)


def check_attack(c: Control, me: dict, rep: Report, tries: int = 1) -> None:
    """S6: Y with a weapon in hand: a Cut... name, `attack` true, and the attack count goes up. With tries > 1, a Y
    that shows no attack is followed by A, then B (a window may be open: the first weapon opens "Use Weapon") and Y
    again."""
    before = me.get("attacks", 0)
    cuts, got, ok, n = [], [], False, 0
    for n in range(1, tries + 1):
        if n > 1:
            shot(c, f"S6_try{n - 1}")
            c.cmd("press", "a" if n % 2 == 0 else "b")
            time.sleep(1.2)
        c.cmd("press", "y")
        time.sleep(0.15)
        shot(c, f"S6_swing{n}")
        got, _ = sample_self(c, 1.5)
        cuts = sorted({m.get("anim") for m in got if str(m.get("anim", "")).startswith("Cut")})
        ok = bool(cuts and got and any(m.get("attack") is True for m in got) and got[-1].get("attacks", 0) > before)
        if ok:
            break
    rep.add("S6", "PASS" if ok else "FAIL", f"with {me.get('weapon')}: {cuts or 'no Cut name'}, attacks "
            f"{before} -> {got[-1].get('attacks') if got else '?'}" + (f" (Y try {n})" if tries > 1 else ""))


def check_self(c: Control, port: int, rep: Report, give_later: bool = False) -> bool:
    """S1-S8. Returns whether Link had a weapon (else S6 waits for --give-weapon, or is SKIP)."""
    c.cmd("ghost", "self")
    time.sleep(0.3)
    me = c.cmd("ghost", "self")
    rep.add("S1", "PASS" if me.get("found") is True else "FAIL", f"found {me.get('found')}, active {me.get('active')}")
    rep.add("S2", *judge_shape(str(me.get("shape", ""))))
    got, _ = sample_self(c, 2.0)
    bodies = {m.get("body") for m in got}
    rep.add("S3", "PASS" if "Wait" in bodies else "FAIL", f"standing: {sorted(map(str, bodies))}")
    p = c.cmd("pos")
    start = (float(p["x"]), float(p["y"]), float(p["z"]))
    runs = {}
    for label, y, sprint in (("walk", 0.5, False), ("run", 1.0, False), ("sprint", 1.0, True)):
        if sprint:
            c.cmd("hold", "b")
        c.cmd("stick", "l", "0", f"{y}", "150")
        got, speed = sample_self(c, 2.4)
        runs[label] = ({str(m.get("body")) for m in got if m.get("body")}, speed)
        back_to(c, start)
    rep.add("S4", *judge_moves(runs))
    c.cmd("press", "x")
    got, _ = sample_self(c, 1.5)
    bodies = {str(m.get("body")) for m in got}
    rep.add("S5", "PASS" if bodies & {"Jump", "Fall", "Land"} else "FAIL", f"after X: {sorted(bodies)}")
    back_to(c, start)
    me = c.cmd("ghost", "self")
    if me.get("weapon"):
        check_attack(c, me, rep)
    elif not give_later:
        rep.add("S6", "SKIP", "no weapon equipped in this save (equip a sword, or use --give-weapon)")
    items = {k: me.get(k) for k in ("weapon", "shield", "bow")}
    ok = me.get("equip_ok") is True and all(judge_item(v) for v in items.values())
    rep.add("S7", "PASS" if ok else "FAIL", f"equip_ok {me.get('equip_ok')}, {items}, weapon class {me.get('weapon_class')}")
    # S8: 5 s without, 5 s with, twice in turn: on a busy PC the rate moves by a few frames a second on its own (one
    # pair alone once read 30.0 -> 25.2, the next run 29.9 -> 28.9)
    without, polled = [], []
    for _ in range(2):
        if polled:
            time.sleep(2.5)                 # (the mod reads for 2 s after the last `ghost self`: SELF_KEEP_S)
        without.append(fps(c))
        polled.append(fps_polled(c, port))
    status, text = judge_frames(mean_fps(without), mean_fps(polled))
    rep.add("S8", status, f"{text} (on average; without {fmt_fps(without)}, with {fmt_fps(polled)})")
    return bool(me.get("weapon"))


def mean_fps(vals: list[float | None]) -> float | None:
    got = [v for v in vals if v is not None]
    return sum(got) / len(got) if got else None


def fmt_fps(vals: list[float | None]) -> str:
    return " and ".join("?" if v is None else f"{v:.1f}" for v in vals)


def fps_polled(c: Control, port: int) -> float | None:
    """Frames a second while a second connection asks `ghost self` 20 times a second (as a client does)."""
    stop = threading.Event()

    def poll():
        with Control(port, timeout=5) as c2:
            while not stop.is_set():
                try:
                    c2.cmd("ghost", "self")
                except ControlError:
                    return
                time.sleep(0.05)
    th = threading.Thread(target=poll, daemon=True)
    th.start()
    try:
        return fps(c)
    finally:
        stop.set()
        th.join(3)


def wait_live(c: Control, secs: float = 15.0) -> dict:
    end, st = time.monotonic() + secs, {}
    while time.monotonic() < end:
        st = c.cmd("ghost")
        if st.get("state") == "live":
            return st
        time.sleep(0.25)
    return st


def cam_forward(c: Control) -> tuple[float, float] | None:
    """The camera's forward direction on the ground (x, z), length 1; None if the game didn't say."""
    try:
        cam = c.cmd("camera")
        fx, fz = float(cam["dx"]), float(cam["dz"])
    except (ControlError, KeyError, TypeError, ValueError):
        return None
    n = (fx * fx + fz * fz) ** 0.5
    return (fx / n, fz / n) if n > 1e-3 else None


def check_link(c: Control, rep: Report) -> None:
    c.cmd("ghost", "far", "0")
    off = ("0", "0", "3")
    fwd = cam_forward(c)
    if fwd:   # (in front of the camera, a little to the left, so the screenshots show it)
        fx, fz = fwd
        off = (f"{3 * fx + 1.2 * fz:.2f}", "0", f"{3 * fz - 1.2 * fx:.2f}")
    c.cmd("ghost", "spawn", "link", *off)
    st = wait_live(c)
    c.cmd("ghost", "anim", "Wait")
    time.sleep(0.6)
    st = c.cmd("ghost")
    shot(c, "L2_wait")
    c.cmd("ghost", "anim", "CutS1")      # (the Sheikah man has no CutS1: this tells the two apart)
    time.sleep(0.6)
    cut = c.cmd("ghost")
    rep.add("L2", *judge_link_spawn(st, cut))
    for check, names in (("L3", ("Walk", "Run", "RunGear2")),
                         ("L4", ("CutS1", "CutSF", "Jump", "ParashawlGlide", "ClimbMove", "SwimMove"))):
        bad = []
        for name in names:
            c.cmd("ghost", "anim", name)
            time.sleep(0.6)
            shot(c, f"{check}_{name}")
            if c.cmd("ghost").get("as") != name:
                bad.append(name)
        rep.add(check, "PASS" if not bad else "FAIL", f"played {[n for n in names if n not in bad]}"
                + (f"; not {bad}" if bad else ""))
    c.cmd("ghost", "anim", "auto")
    with_ghost = fps(c)
    c.cmd("ghost", "despawn")
    without = fps(c)
    rep.add("L5", *judge_frames(without, with_ghost))
    c.cmd("ghost", "far", "60", "50")


def give_weapon(c: Control, name: str, rep: Report) -> bool:
    """--give-weapon, for a save without one: the ghost mod spawns the weapon `name` in front of Link (no animation,
    not moved), Link walks up to it and takes it with A; then S6 (the attack) and S9 (its name read back). Run last:
    a later `ghost spawn` would remove the ghost there is, which may then be the weapon in Link's hand. Returns whether
    `ghost despawn` is safe afterwards (False while the mod may still hold the weapon Link took)."""
    c.cmd("ghost", "despawn")
    c.cmd("ghost", "anim", "off")
    c.cmd("ghost", "far", "0")
    time.sleep(0.5)
    fx, fz = cam_forward(c) or (0.0, 1.0)
    c.cmd("ghost", "spawn", name, f"{1.6 * fx:.2f}", "0.8", f"{1.6 * fz:.2f}")
    st = wait_live(c, 10)
    if st.get("state") != "live":
        rep.add("S6", "SKIP", f"no weapon: {name} didn't spawn ({st.get('state')}: {st.get('last')})")
        return True
    first = st.get("actor")
    time.sleep(2.0)                                  # (it falls and settles)
    shot(c, "S6_weapon_on_ground")
    end = time.monotonic() + 3.0                     # walk up to it (stick forward = the camera's way)
    try:
        while time.monotonic() < end:
            p, g = c.batch([["pos"], ["ghost"]], errors=True)
            if not (isinstance(p, dict) and isinstance(g, dict) and "x" in g):
                break
            d = ((float(g["x"]) - float(p["x"])) ** 2 + (float(g["z"]) - float(p["z"])) ** 2) ** 0.5
            if d < 0.9:
                break
            c.cmd("stick", "l", "0", "0.35", "10")
            time.sleep(0.1)
    finally:
        c.cmd("release", "all")
    time.sleep(0.5)
    me: dict = {}
    for _ in range(8):                               # A: take it (and close the item window it opens)
        c.cmd("press", "a")
        time.sleep(1.2)
        me = c.cmd("ghost", "self")
        if me.get("weapon"):
            break
    if not me.get("weapon"):
        shot(c, "S6_no_weapon")
        rep.add("S6", "SKIP", f"no weapon: Link didn't take the {name} (look at ghostcheck_S6_no_weapon.png)")
    else:
        time.sleep(1.5)
        shot(c, "S6_taken")
        me = c.cmd("ghost", "self")
        ok = me.get("weapon") == name and me.get("weapon_class") is True and judge_item(me.get("shield")) \
            and judge_item(me.get("bow"))
        rep.add("S9", "PASS" if ok else "FAIL", f"took {name}: weapon {me.get('weapon')!r}, weapon class "
                f"{me.get('weapon_class')}, shield {me.get('shield')!r}, bow {me.get('bow')!r}")
        check_attack(c, me, rep, tries=4)   # (the first weapon opens a "Use Weapon" window: A or B closes it)
    st = c.cmd("ghost")
    print(f"  ghost after the take: {st.get('state')}, actor {st.get('actor')} (the sword was {first}), "
          f"last {st.get('last')!r}", flush=True)
    return not (st.get("state") == "live" and st.get("actor") == first)


def check_replay(c: Control, port: int, link: bool, rep: Report) -> None:
    py = [sys.executable, "-u"]
    procs = [subprocess.Popen(py + [str(REPO / "tools" / "mp" / "relay.py"), "--port", str(RELAY_PORT)])]
    time.sleep(1.0)
    procs.append(subprocess.Popen(py + [str(REPO / "tools" / "mp" / "bot.py"), "--relay", f"127.0.0.1:{RELAY_PORT}",
                                        "--radius", "4", "--anim", BOT_SCRIPT, "--weapon", "Weapon_Sword_001"]))
    sent: list[str] = []
    ghost = client.Ghost(actor=client.GHOST_LINK_ACTOR if link else None, sync=True, log=lambda s: print("  " + s))
    runner = client.Runner(("127.0.0.1", RELAY_PORT), "Check", game_port=port, ghost=ghost, log=lambda s: None)
    real_send = runner.relay.send

    def send(msg):
        if msg.get("type") == "state" and msg.get("anim"):
            sent.append(msg["anim"])
        return real_send(msg)
    runner.relay.send = send
    th = threading.Thread(target=lambda: asyncio.run(runner.run()), daemon=True)
    th.start()
    samples, st = [], {}
    try:
        end = time.monotonic() + 28
        while time.monotonic() < end:
            time.sleep(0.2)
            try:
                st = c.cmd("ghost")
            except ControlError:
                continue
            samples.append((str(st.get("remote", "")), str(st.get("as", ""))))
        names = [step.split("@")[0].split(":")[0] for step in BOT_SCRIPT.split(",")]
        rep.add("R1", *judge_replay(samples, names, link, str(st.get("remote_missing", ""))))
        p = c.cmd("pos")
        start = (float(p["x"]), float(p["y"]), float(p["z"]))
        c.cmd("stick", "l", "0", "1", "120")
        time.sleep(2.5)
        back_to(c, start)
        rep.add("R2", *judge_own(sent))
    finally:
        runner.stop()
        th.join(10)
        for pr in procs:
            pr.terminate()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="In-game checks for ghost v2 (starts the game).")
    ap.add_argument("--port", type=int, default=7777, help="the game's control port (default 7777)")
    ap.add_argument("--game", type=Path, default=GAME_DIR, help="the Savage Wind folder")
    ap.add_argument("--link", action="store_true", help="also build, deploy and check the Link body")
    ap.add_argument("--no-launch", action="store_true", help="use a running game (left running)")
    ap.add_argument("--spot", nargs=4, type=float, metavar=("X", "Y", "Z", "YAW"),
                    help="first move Link to this open ground and turn the camera to YAW (the walk, run and sprint "
                         "go that way, 2.4 s each). The test save loads by a rock face, so S4 climbs it without this. "
                         f"Tried: {' '.join(map(str, SPOT))} (about 22 m of meadow to the plateau's edge)")
    ap.add_argument("--items", action="store_true",
                    help="the held items' checks H1-H16 instead of the others (ghost mod 0.6.2; implies --link; "
                         "tools/mp/ghost_items_check.py); --give-weapon adds H12 (`ghost give`)")
    ap.add_argument("--wait-max", type=float, default=120.0, metavar="MIN",
                    help="another program's game runs: wait at most this many minutes for it to close (asking every "
                         "30 s; default 120); it is never touched")
    ap.add_argument("--lib", type=Path, metavar="DIR",
                    help="a scratch mod library: ghost.dll goes to DIR\\mods and the Link body to DIR\\overlay (default: "
                         "build\\native\\ghost-mods and out\\ghost-check-overlay)")
    ap.add_argument("--give-weapon", nargs="?", const=GIVE_WEAPON, metavar="ACTOR",
                    help="when Link has no weapon (the test save has none): at the end, spawn one in front of him "
                         f"and take it, for S6 and S9 (default {GIVE_WEAPON}; this changes what the save holds if "
                         "the game saves)")
    args = ap.parse_args(argv)
    if args.items:
        args.link = True
    if args.lib:
        args.lib = args.lib.resolve()   # (the game runs in its own folder: a relative path would point there)
    rep = Report()
    game = None
    overlay = None
    mods = None
    results, shots, prefix = RESULTS, SHOTS, "ghostcheck_"
    if args.items:
        results, shots, prefix = RESULTS.parent / "ghost-items-results.txt", SHOTS.parent, "ghostitems_"
    if args.lib and not args.no_launch:
        mods = args.lib / "mods"
        mods.mkdir(parents=True, exist_ok=True)
        dll = REPO / "build" / "native" / "ghost-mods" / "ghost.dll"
        if dll.is_file():
            shutil.copy2(dll, mods / "ghost.dll")
    log_path = args.game / "savagewind.log"
    if args.link and not args.no_launch:
        try:
            overlay = deploy_link(args.game, args.lib / "overlay" if args.lib else OVERLAY)
            rep.add("L1", "PASS", f"built and deployed: {overlay.pop('_built')}")
        except Exception as e:   # (any failure here: say it, go on without the body)
            rep.add("L1", "FAIL", f"{type(e).__name__}: {e}")
            overlay, args.link = None, False
    elif args.link:
        rep.add("L1", "SKIP", "--no-launch: deploy the Link body yourself")
    if not args.no_launch:
        dll = REPO / "build" / "native" / "ghost-mods" / "ghost.dll"
        if not dll.is_file():
            print(f"No {dll}: run python tools\\build_native.py build first.")
            return 2
        game = start_game_alone([str(args.game / "savagewind.exe"), "--play"], args.game,
                                game_env(args.port, overlay, mods), max_wait=args.wait_max * 60)
        if game is None:
            rep.add("setup", "SKIP", "another program's game ran the whole time: no free moment to start ours")
            return 1
    try:
        c = wait_for_game(args.port, timeout=180)
        if not wait_world(c):
            rep.add("setup", "FAIL", "no save loaded within 4 minutes")
            return 1
        time.sleep(3)
        st = c.cmd("ghost")
        if st.get("version") != WANT_VERSION:
            rep.add("setup", "FAIL", f"ghost mod {st.get('version', 'older than 0.5.0')}; rebuild it")
            return 1
        SHOTS.mkdir(parents=True, exist_ok=True)
        if args.spot:
            back_to(c, tuple(args.spot[:3]))
            c.cmd("look", f"{args.spot[3]:.1f}", "8")
            time.sleep(1.5)
        if args.items:   # (the held items' checks: ghost_items_check.py)
            from mp import ghost_items_check
            ghost_items_check.run(c, args.port, sys.modules[__name__], rep, give=bool(args.give_weapon))
            return 0 if all(s != "FAIL" for _, s, _ in rep.rows) else 1
        armed = check_self(c, args.port, rep, give_later=bool(args.give_weapon))
        if args.link:
            check_link(c, rep)
        check_replay(c, args.port, args.link, rep)
        if args.give_weapon and not armed:   # (last: see give_weapon)
            if not give_weapon(c, args.give_weapon, rep):
                print("  the mod may still hold the weapon Link took: no `ghost despawn` (the game closes next)")
                return 0 if all(s != "FAIL" for _, s, _ in rep.rows) else 1
        c.cmd("ghost", "despawn")
    except (ControlError, OSError) as e:
        rep.add("run", "FAIL", f"stopped: {e}")
    finally:
        if game is not None:
            try:
                Control(args.port, timeout=5).cmd("exit")
            except (ControlError, OSError):
                pass
            try:
                game.wait(30)
            except subprocess.TimeoutExpired:
                game.kill()   # (only the game this run started)
            if args.items:
                from mp import ghost_items_check
                try:
                    rep.add("H14", *ghost_items_check.judge_log(log_path.read_text(encoding="utf-8",
                                                                                   errors="replace")))
                except OSError as e:
                    rep.add("H14", "SKIP", f"no log: {e}")
                rep.add("H14", "PASS" if game.returncode == 0 else "FAIL", f"the game closed with exit code "
                        f"{game.returncode} after `exit`")
        for f in args.game.glob(f"{prefix}*.png"):   # (the screenshots this run made: to out\shots)
            shots.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(shots / f.name))
        results.parent.mkdir(parents=True, exist_ok=True)
        results.write_text("\n".join(f"{s} {k}: {t}" for k, s, t in rep.rows) + f"\n{rep.summary()}\n", encoding="utf-8")
        print(rep.summary())
    return 0 if all(s != "FAIL" for _, s, _ in rep.rows) else 1


if __name__ == "__main__":
    sys.exit(main())
