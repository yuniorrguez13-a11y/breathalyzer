#!/usr/bin/env python3
"""In-game checks for Who am I (docs/charsel.md): every built character spawns, animates and costs little; `iam`
works; a bot's picks reach the ghost. Run it only when you may start the game (it starts Savage Wind once, like
tools/mp/ghost_ingame_check.py: one copy only, the test save in out\\test-save when it is there, closed with `exit`).
Not yet run.

    python tools/mp/charsel_ingame_check.py              build the Link body and the characters (into
                                                         out\\charsel-check-packs: your out\\charsel-packs is never
                                                         touched), deploy both to out\\charsel-check-overlay (only for
                                                         this run), start the game
    python tools/mp/charsel_ingame_check.py --no-launch  use a game that is already running (control port 7777, ghost
                                                         mod 0.8.0, the characters deployed with your mods, a save
                                                         loaded); it is left running
    python tools/mp/charsel_ingame_check.py --only zelda,bokoblin     some characters only
    --library out\\showcase-lib (with --no-launch: the library the game was deployed from), --no-shots,
    --link-retarget auto|on|off (the check's own build: as charsel build)

Before: python tools\\build_native.py build (ghost.dll 0.8.0) and python tools\\charsel.py build. Results go to
out\\research\\charsel-ingame-results.txt and screenshots to out\\shots\\charselcheck\\ (never committed).

Checks, per character X (its free actor name from the check's own charsel.json; with --no-launch, from the
characters deployed with your mods: charsel.deployed_catalog):
  X.spawn   `ghost spawn <name>` is live within 15 s, with its own animation holder (as_ok)
  X.anims   Wait Walk Run RunGear2 CutS1 BowCharge DamageS Dead ParashawlGlide ClimbMove SwimMove (the ones it has)
            each show as `as` 0.6 s after `ghost anim` (a screenshot each: look at hips, feet and twists)
  X.frames  frames a second with it and without (at most 3 fewer)
  I1        `iam`: lists the names; `iam daruk` picks goron (iam_seq goes up); a name off the list is refused
  R1        a bot that picks zelda, goron, bokoblin in turn (5 s each): the client's ghost spawns each one's name
  NOPACK    `ghost spawn` of a free name with no pack in the game (what a stale catalog would ask for): the game
            still answers 15 s later (no crash, no hang) and nothing shows. Last, as it may stop the game.
  LOG       no heap, resource or size-table lines about our names in savagewind.log
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

_TOOLS = str(Path(__file__).resolve().parent.parent)
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
import charsel  # noqa: E402
from control import Control, ControlError, wait_for_game  # noqa: E402
from mp import bot, client  # noqa: E402
from mp import ghost_ingame_check as gic  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent
OVERLAY = REPO / "out" / "charsel-check-overlay"
PACKS = REPO / "out" / "charsel-check-packs"   # the check's own build (never the player's out\charsel-packs)
SHOTS = REPO / "out" / "shots" / "charselcheck"
RESULTS = REPO / "out" / "research" / "charsel-ingame-results.txt"
RELAY_PORT = 7791
ANIMS = ("Wait", "Walk", "Run", "RunGear2", "CutS1", "BowCharge", "DamageS", "Dead", "ParashawlGlide", "ClimbMove",
         "SwimMove")
BOT_LOOKS = "zelda:5,goron:5,bokoblin:5"
LOG_WORDS = ("heap", "resource", "size table", "rstb", "out of memory")


# ------------------------------------------------------------------ judging (pure: tests/test_charsel_check.py)

def judge_spawn(st: dict) -> tuple[str, str]:
    ok = st.get("state") == "live" and st.get("as_ok") is True
    return ("PASS" if ok else "FAIL"), f"state {st.get('state')}, as_ok {st.get('as_ok')}, name {st.get('name')}"


def judge_anims(played: dict[str, str], want: list[str]) -> tuple[str, str]:
    """played: name -> what `as` said after `ghost anim name`."""
    bad = [n for n in want if played.get(n) != n]
    text = f"played {[n for n in want if n not in bad]}" + (f"; not {bad} (got {[played.get(n) for n in bad]})"
                                                            if bad else "")
    return ("PASS" if not bad else "FAIL"), text


def judge_iam(listed: str, picked: dict, refused: str) -> tuple[str, str]:
    ok = "zelda" in listed and picked.get("iam") == "goron" and "no ganon" in refused
    return ("PASS" if ok else "FAIL"), f"listed {listed[:60]!r}; picked {picked.get('iam')!r}; refused {refused[:40]!r}"


def judge_bot_looks(names_seen: list[str], want: list[str]) -> tuple[str, str]:
    lost = [n for n in want if n not in names_seen]
    return ("PASS" if not lost else "FAIL"), f"the ghost was {sorted(set(names_seen))}" + (
        f"; never {lost}" if lost else "")


def judge_nopack(answered: bool, st: dict) -> tuple[str, str]:
    """A spawn of a free name with no pack: the game must keep answering, and nothing may show."""
    if not answered:
        return "FAIL", "the game stopped answering after `ghost spawn` of a name with no pack"
    ok = st.get("state") != "live"
    return ("PASS" if ok else "FAIL"), f"the game answers; state {st.get('state')}, wanted {st.get('wanted')}"


def free_without_pack(cat: dict, overlay: Path | None = None) -> str | None:
    """A free name no character uses and with no pack in the overlay: for NOPACK."""
    used = {i.get("slot") for i in (cat.get("built") or {}).values() if isinstance(i, dict)}
    for slot in reversed(charsel.FREE_SLOTS):
        if slot in used or (overlay is not None and (overlay / "Actor" / "Pack" / f"{slot}.sbactorpack").exists()):
            continue
        return slot
    return None


def judge_log(text: str, slots: list[str]) -> tuple[str, str]:
    bad = [ln.strip() for ln in text.splitlines()
           if any(s in ln for s in slots + ["SwChar_"]) and any(w in ln.lower() for w in LOG_WORDS)]
    return ("PASS" if not bad else "FAIL"), (f"{len(bad)} lines, first: {bad[0][:160]}" if bad else "none")


# ------------------------------------------------------------------ the run

def deploy(game_dir: Path, overlay: Path = OVERLAY, packs: Path = PACKS, link_retarget: str = "auto"
           ) -> dict[str, str]:
    """Build the Link body and the characters (into `packs`, the check's own folder; the Linkle mod read from your
    library), and deploy both alone to `overlay`. Returns the game's env."""
    from mp import ghost_link_pack as glp
    romfs = game_dir / "extracted" / "romfs"
    glp.build(romfs, glp.DEFAULT_OUT)
    charsel.build(romfs, packs, library=charsel.DEFAULT_LIBRARY if charsel.DEFAULT_LIBRARY.is_dir() else None,
                  link_retarget=link_retarget)
    py = [sys.executable, str(REPO / "tools" / "romfs_mods.py")]
    subprocess.run(py + ["deploy", "--out", str(overlay), "--game-romfs", str(romfs), str(glp.DEFAULT_OUT),
                         str(packs)], check=True)
    out = subprocess.run(py + ["env", "--out", str(overlay)], check=True, capture_output=True, text=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


FPS_AGAIN = 27.0   # frames a second: under this with the character there, measured once more (and the better kept)


def check_character(c: Control, cid: str, info: dict, rep: gic.Report, shots: bool = True) -> None:
    slot = info["slot"]
    fwd = gic.cam_forward(c)
    off = (f"{3 * fwd[0]:.2f}", "0", f"{3 * fwd[1]:.2f}") if fwd else ("0", "0", "3")
    c.cmd("ghost", "spawn", slot, *off)
    st = gic.wait_live(c)
    rep.add(f"{cid}.spawn", *judge_spawn(st))
    if st.get("state") != "live":
        c.cmd("ghost", "despawn")
        return
    have = set(charsel.clip_map(charsel.RECIPES[cid], info.get("rest")))
    want = [n for n in ANIMS if n in have]
    played = {}
    for name in want:
        c.cmd("ghost", "anim", name)
        time.sleep(0.6)
        if shots:
            gic.shot(c, f"{cid}_{name}")
        played[name] = str(c.cmd("ghost").get("as", ""))
    rep.add(f"{cid}.anims", *judge_anims(played, want))
    c.cmd("ghost", "anim", "auto")
    with_it = gic.fps(c)
    # (2026-11-13: the first body of a run dropped to 4-21 fps for about 12 s right after it spawned, with the host
    # late on vsync, then ran at 30 with it still there: a one-off stall, not its cost. A cost shows twice.)
    if with_it is not None and with_it < FPS_AGAIN:
        again = gic.fps(c)
        with_it = max(with_it, again) if again is not None else with_it
    c.cmd("ghost", "despawn")
    without = gic.fps(c, 3)
    rep.add(f"{cid}.frames", *gic.judge_frames(without, with_it))


def check_iam(c: Control, rep: gic.Report) -> None:
    c.cmd("ghost", "looks", client.iam_list(), "link")
    listed = str(c.cmd("iam").get("names", ""))
    seq = int(c.cmd("ghost").get("iam_seq", 0))
    c.cmd("iam", "daruk")
    picked = c.cmd("ghost")
    try:
        c.cmd("iam", "ganon")
        refused = ""
    except ControlError as e:
        refused = str(e)
    ok_seq = int(picked.get("iam_seq", 0)) == seq + 1
    status, text = judge_iam(listed, picked, refused)
    rep.add("I1", status if ok_seq else "FAIL", text + ("" if ok_seq else f"; iam_seq {seq} -> {picked.get('iam_seq')}"))


def check_bot_looks(c: Control, port: int, cat: dict, rep: gic.Report) -> None:
    from mp import relay as mp_relay
    stop = asyncio.Event()
    loop_box = {}

    async def serve():
        r = mp_relay.Relay("127.0.0.1", RELAY_PORT, log=lambda s: None)
        await r.start()
        loop_box["loop"] = asyncio.get_running_loop()
        try:
            await bot.run_bot(("127.0.0.1", RELAY_PORT), "Bot", stop=stop, log=lambda s: None,
                              looks=bot.LookScript.parse(BOT_LOOKS), path=bot.CirclePath(4.0, 1.0))
        finally:
            await r.close()
    th = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    th.start()
    time.sleep(1.0)
    ghost = client.Ghost(actor=client.GHOST_LINK_ACTOR, sync=True, looks=charsel.Looks(cat, client.GHOST_LINK_ACTOR),
                         log=lambda s: print("  " + s))
    runner = client.Runner(("127.0.0.1", RELAY_PORT), "Check", game_port=port, ghost=ghost, log=lambda s: None,
                           look=charsel.look_message("link"))
    rt = threading.Thread(target=lambda: asyncio.run(runner.run()), daemon=True)
    rt.start()
    names = []
    try:
        end = time.monotonic() + 20
        while time.monotonic() < end:
            time.sleep(0.25)
            try:
                st = c.cmd("ghost")
            except ControlError:
                continue
            if st.get("state") == "live":
                names.append(str(st.get("name", "")))
        want = [cat["built"][i]["slot"] for i in ("zelda", "goron", "bokoblin") if i in cat["built"]]
        rep.add("R1", *judge_bot_looks(names, want))
    finally:
        runner.stop()
        rt.join(10)
        loop = loop_box.get("loop")
        if loop is not None:
            loop.call_soon_threadsafe(stop.set)
        th.join(10)


def check_nopack(c: Control, cat: dict, overlay: Path | None, rep: gic.Report) -> None:
    slot = free_without_pack(cat, overlay)
    if slot is None:
        rep.add("NOPACK", "SKIP", "every free name has a pack here")
        return
    try:
        c.cmd("ghost", "spawn", slot)
    except ControlError as e:
        rep.add("NOPACK", "PASS", f"refused at once: {e}")
        return
    st, answered = {}, False
    end = time.monotonic() + 15
    while time.monotonic() < end:
        time.sleep(1.0)
        try:
            st, answered = c.cmd("ghost"), True
        except (ControlError, OSError):
            answered = False
    rep.add("NOPACK", *judge_nopack(answered, st))
    try:
        c.cmd("ghost", "despawn")
    except (ControlError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="In-game checks for Who am I (starts the game).")
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--game", type=Path, default=gic.GAME_DIR)
    ap.add_argument("--no-launch", action="store_true", help="use a running game (left running)")
    ap.add_argument("--only", help="these characters only (comma-separated ids)")
    ap.add_argument("--wait-max", type=float, default=120.0, metavar="MIN")
    ap.add_argument("--library", type=Path,
                    help="with --no-launch: the mod library the running game was deployed from (default: modman's)")
    ap.add_argument("--no-shots", action="store_true", help="no screenshot per animation (saves disk)")
    ap.add_argument("--link-retarget", choices=charsel.LINK_RETARGET, default="auto",
                    help="the check's own build: Link's clips fitted to each body by the heap estimate, always, never")
    args = ap.parse_args(argv)
    rep = gic.Report()
    game = None
    env = None
    if not args.no_launch:
        dll = REPO / "build" / "native" / "ghost-mods" / "ghost.dll"
        if not dll.is_file():
            print(f"No {dll}: run python tools\\build_native.py build first.")
            return 2
        try:
            env = deploy(args.game, link_retarget=args.link_retarget)
        except Exception as e:   # (say it and stop: nothing to check without the packs)
            print(f"Couldn't build or deploy: {type(e).__name__}: {e}")
            return 2
        game = gic.start_game_alone([str(args.game / "savagewind.exe"), "--play"], args.game,
                                    gic.game_env(args.port, env), max_wait=args.wait_max * 60)
        if game is None:
            rep.add("setup", "SKIP", "another program's game ran the whole time")
            return 1
    # (launched: the check's own build; --no-launch: only what your game really has, deployed with your mods)
    cat = charsel.load_catalog(PACKS) if not args.no_launch else charsel.deployed_catalog(args.library)
    overlay = OVERLAY if not args.no_launch else charsel.overlay_dir(args.library)
    ids = [i for i in (args.only.split(",") if args.only else cat.get("built", {})) if i in cat.get("built", {})]
    try:
        c = wait_for_game(args.port, timeout=180)
        if not gic.wait_world(c):
            rep.add("setup", "FAIL", "no save loaded within 4 minutes")
            return 1
        time.sleep(3)
        st = c.cmd("ghost")
        if st.get("version") != gic.WANT_VERSION or st.get("has_iam") is not True:
            rep.add("setup", "FAIL", f"ghost mod {st.get('version')}; rebuild it (0.8.0)")
            return 1
        SHOTS.mkdir(parents=True, exist_ok=True)
        c.cmd("ghost", "far", "0")
        for cid in ids:
            check_character(c, cid, cat["built"][cid], rep, shots=not args.no_shots)
        check_iam(c, rep)
        c.cmd("ghost", "far", "60", "50")
        if {"zelda", "goron", "bokoblin"} & set(ids):
            check_bot_looks(c, args.port, cat, rep)
        c.cmd("ghost", "despawn")
        check_nopack(c, cat, overlay, rep)
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
        try:
            text = (args.game / "savagewind.log").read_text(encoding="utf-8", errors="replace")
            rep.add("LOG", *judge_log(text, [cat["built"][i]["slot"] for i in ids]))
        except OSError as e:
            rep.add("LOG", "SKIP", f"no log: {e}")
        for f in args.game.glob("ghostcheck_*.png"):
            SHOTS.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(SHOTS / f.name))
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        RESULTS.write_text("\n".join(f"{s} {k}: {t}" for k, s, t in rep.rows) + f"\n{rep.summary()}\n",
                           encoding="utf-8")
        print(rep.summary())
    return 0 if all(s != "FAIL" for _, s, _ in rep.rows) else 1


if __name__ == "__main__":
    sys.exit(main())
