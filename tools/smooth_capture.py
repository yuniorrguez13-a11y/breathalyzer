#!/usr/bin/env python3
"""Smooth capture: record a trailer offline at a fixed game step, then encode a smooth 60 fps video.

The game runs as slowly as it needs to (it waits for each saved frame), but every game update advances its clock by
exactly 1/120 s and every other frame is saved, so the video has exactly 1/60 s of game time between frames.
docs/smooth-capture.md has the how and why.

    python tools/smooth_capture.py build        # smoothcap.dll (native/examples/smoothcap), staged as a mod folder
    python tools/smooth_capture.py selftest     # its self-test (no game)
    python tools/smooth_capture.py record --name castle --seconds 10 --auto-continue --wait 20
        # a test game (scratch library, test save copy, capture settings), 10 s of game time, encoded to
        # out\\capture-castle\\castle.mp4 (1080p60, H.264 CRF 18); --4k upscales to 2160p
    python tools/smooth_capture.py attach --name castle --seconds 10 --port 6533
        # a game you started yourself with smoothcap in its library and SAVAGEWIND_CONTROL=6533
    python tools/smooth_capture.py encode --name castle [--fps 60] [--4k] [--keep-frames]

Frames: out\\capture-<name>\\frames\\000000.png..., deleted after a good encode unless --keep-frames.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
OUT = REPO / "out"
BUILD_LOCK = OUT / "build.lock"
GAME_LOCK = OUT / "game.lock"
MOD_SRC = REPO / "native" / "examples" / "smoothcap"
BUILD_OUT = REPO / "build" / "native"
DLL_SUBDIR = "smoothcap-mods"
STAGE = OUT / "capture-build" / "smoothcap-mod"   # the mod folder modman adds: mod.toml + smoothcap.dll
DEFAULT_LIBRARY = OUT / "capture-lib"
DEFAULT_SAVE = OUT / "test-save"
DEFAULT_PORT = 6533
MOD_NAME = "Smooth capture"
MOD_ID = "botw-mod-loader.smoothcap"   # (manifest.toml's id: the library's mods\<id> folder)
FFMPEG_FALLBACK = Path.home() / "Documents" / "SavageWind" / "ffmpeg" / "ffmpeg-9.0.2-essentials_build" / "bin" / \
    "ffmpeg.exe"
CONTINUE_INPUT = "900:A:8,1400:A:8,1900:A:8"   # presses Continue on the title screen (AGENTS.md's unattended run)
BELOW_NORMAL = 0x00004000
NEW_GROUP = 0x00000200
PNG_BYTES_PER_PIXEL = 1.8   # a game frame's PNG, generously (raw RGBA is 4)
MIN_FREE_GB = 4.5           # stop before Windows' swap file (3-4 GB while the game runs) runs out of room
FPS_MIN, FPS_MAX = 15, 150  # smoothcap.c: the step 1/(2 fps) stays within the runtime's 1/30 .. 1/300 s
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
REC_RE = re.compile(r"^rec_(\d+)\.png$", re.IGNORECASE)


class CaptureError(RuntimeError):
    """Something is missing or went wrong; the message says what to do."""


def say(msg: str) -> None:
    print(msg, flush=True)


# ------------------------------------------------------------------ pure logic (tests/test_smooth_capture.py)

def check_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise CaptureError(f"Bad capture name {name!r}: use letters, digits, - and _ (at most 40).")
    return name


def capture_dir(name: str, out: Path = OUT) -> Path:
    return out / f"capture-{check_name(name)}"


def check_fps(fps: int) -> int:
    if not FPS_MIN <= fps <= FPS_MAX:
        raise CaptureError(f"--fps must be {FPS_MIN} to {FPS_MAX}.")
    return fps


def frames_for(seconds: float, fps: int) -> int:
    """Frames for `seconds` of game time at `fps` (at least 1)."""
    if seconds <= 0:
        raise CaptureError("--seconds must be more than 0.")
    return max(1, int(round(seconds * fps)))


def step_ms(fps: int) -> float:
    """The game's fixed step for a video at fps: half its frame time (one present in two is saved)."""
    return 1000.0 / (2 * fps)


def out_size(four_k: bool) -> tuple[int, int]:
    return (3840, 2160) if four_k else (1920, 1080)


def estimate_bytes(frames: int, size: tuple[int, int]) -> int:
    return int(frames * size[0] * size[1] * PNG_BYTES_PER_PIXEL)


SETTINGS_OVERRIDES = {
    "frame_rate": "off",          # no pacing: the game runs as fast as the saves let it
    "vsync": "off",
    "frame_generation": "0",      # generated frames would be presents of their own
    "fullscreen": "0",
    "perf_overlay": "off",
}


def capture_settings(text: str, window: tuple[int, int] | None = (1920, 1080), extra: dict | None = None) -> str:
    """A copy of savagewind.ini for capturing: SETTINGS_OVERRIDES (+ window_size, + extra) replaced or appended."""
    want = dict(SETTINGS_OVERRIDES)
    if window:
        want["window_size"] = f"{window[0]}x{window[1]}"
    want.update(extra or {})
    lines, seen = [], set()
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=", line)
        if m and m.group(1) in want:
            key = m.group(1)
            if key not in seen:
                lines.append(f"{key} = {want[key]}")
                seen.add(key)
            continue
        lines.append(line)
    lines += [f"{k} = {v}" for k, v in want.items() if k not in seen]
    return "\n".join(lines) + "\n"


def rec_number(filename: str) -> int | None:
    m = REC_RE.match(filename)
    return int(m.group(1)) if m else None


def ordered_recs(names, ignore=()) -> list[str]:
    """The runtime's rec_<frame>.png names in frame order, without those in `ignore` (there before the capture)."""
    skip = {n.lower() for n in ignore}
    recs = [(rec_number(n), n) for n in names if n.lower() not in skip]
    return [n for k, n in sorted((k, n) for k, n in recs if k is not None)]


def frame_name(i: int) -> str:
    return f"{i:06d}.png"


PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def png_complete(path: Path) -> bool:
    """The PNG ends with its IEND chunk (else the runtime is still writing it)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() < 8 + len(PNG_END):
                return False
            f.seek(-len(PNG_END), os.SEEK_END)
            return f.read() == PNG_END
    except OSError:
        return False


def parse_env(pairs) -> dict:
    """--env KEY=VALUE pairs."""
    env = {}
    for kv in pairs:
        key, eq, value = kv.partition("=")
        if not eq or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            raise CaptureError(f"Bad --env {kv!r}: use KEY=VALUE.")
        env[key] = value
    return env


def ffmpeg_cmd(ffmpeg: str, frames_dir: Path, fps: int, out: Path, size: tuple[int, int], crf: int = 18,
               preset: str = "slow") -> list[str]:
    w, h = size
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p")
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(fps), "-start_number", "0",
            "-i", str(frames_dir / "%06d.png"), "-vf", vf, "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-r", str(fps), "-movflags", "+faststart", str(out)]


CLOCK_RE = re.compile(r"\[clock\] t \S+ frames (\d+) game (-?[\d.]+)")
SHOT_RE = re.compile(r"\[shot\] recording (on|off) at frame (\d+)")


def clock_steps(lines) -> list[float]:
    """From savagewind.log started with SAVAGEWIND_CLOCK_PROBE=<ms>: the game's step (in 1/30 s) between each two
    "[clock]" lines while the F12 recording was on (the last capture's). Each "[clock]" line has the game updates so
    far and the game's own time so far (its delta frames summed)."""
    runs, cur, on = [], [], False
    for line in lines:
        m = SHOT_RE.search(line)
        if m:
            on = m.group(1) == "on"
            if on:
                cur = []
            else:
                runs.append(cur)
            continue
        m = CLOCK_RE.search(line)
        if m and on:
            cur.append((int(m.group(1)), float(m.group(2))))
    if on:
        runs.append(cur)
    pts = runs[-1] if runs else []
    return [(g1 - g0) / (f1 - f0) for (f0, g0), (f1, g1) in zip(pts, pts[1:]) if f1 > f0]


def same_or_inside(path: Path, folder: Path) -> bool:
    try:
        p, f = path.resolve(), folder.resolve()
    except OSError:
        return False
    return p == f or f in p.parents


def check_scratch(path: Path, what: str, install: Path, real_library: Path) -> None:
    """Saves and libraries for a capture are copies in out\\: never the game's own save or the real library."""
    if same_or_inside(path, install / "save") or same_or_inside(path, install):
        raise CaptureError(f"The {what} {path} is in the Savage Wind folder: use a copy in {OUT}.")
    if same_or_inside(path, real_library):
        raise CaptureError(f"The {what} {path} is your real mod library: use a scratch one in {OUT}.")
    if not same_or_inside(path, OUT):
        raise CaptureError(f"The {what} {path} must be inside {OUT}.")


@dataclass
class Plan:
    name: str
    frames: int
    fps: int
    size: tuple[int, int]
    folder: Path

    @property
    def frames_dir(self) -> Path:
        return self.folder / "frames"

    @property
    def video(self) -> Path:
        return self.folder / f"{self.name}.mp4"


def make_plan(name: str, seconds: float, fps: int, four_k: bool, out: Path = OUT) -> Plan:
    fps = check_fps(fps)
    return Plan(check_name(name), frames_for(seconds, fps), fps, out_size(four_k), capture_dir(name, out))


# ------------------------------------------------------------------ machine helpers

def free_gb(path: Path) -> float:
    p = path
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def game_running() -> bool:
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq savagewind.exe", "/NH"], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return "savagewind.exe" in r.stdout.lower()


LOCK_PID_RE = re.compile(r"^pid=(\d+)\s*$", re.MULTILINE)
LOCK_START_RE = re.compile(r"^started=([\d.]+)\s*$", re.MULTILINE)


def lock_text(owner: str, pid: int | None = None, started: float | None = None) -> str:
    """What a lock file says: the owner on the first line (what other jobs show), then pid= and started= (epoch s)."""
    pid = os.getpid() if pid is None else pid
    started = time.time() if started is None else started
    return f"{owner}\npid={pid}\nstarted={started:.1f}\n"


def process_started(pid: int) -> float | None:
    """When process pid started (epoch s), None: it isn't running."""
    if pid <= 0:
        return None
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except OSError:
            pass
        return 0.0
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return 0.0 if ctypes.get_last_error() == 5 else None   # (access denied: running, someone else's)
    try:
        code = wintypes.DWORD()
        if k32.GetExitCodeProcess(h, ctypes.byref(code)) and code.value != 259:   # (259: STILL_ACTIVE)
            return None
        t = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
            return 0.0
        ft = (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime
        return ft / 1e7 - 11644473600.0
    finally:
        k32.CloseHandle(h)


def lock_stale(text: str, started_of=process_started) -> bool:
    """A lock that names its pid is stale when that process isn't running, or started after the lock was written (the
    pid was used again). A lock without a pid (another job's) is never stale here."""
    m = LOCK_PID_RE.search(text)
    if not m:
        return False
    began = started_of(int(m.group(1)))
    if began is None:
        return True
    t = LOCK_START_RE.search(text)
    return bool(t and began and began > float(t.group(1)) + 5.0)


@contextlib.contextmanager
def lock(path: Path, owner: str, poll: float = 30.0, max_wait: float = 20 * 60, also=lambda: False):
    """Wait while path exists (or also() is true), then hold it (a file naming the owner, this process's pid and the
    time) until the block ends. A lock left by a process that isn't running any more is removed (lock_stale)."""
    deadline = time.monotonic() + max_wait
    while path.exists() or also():
        text = ""
        with contextlib.suppress(OSError):
            text = path.read_text(errors="replace")
        if text and lock_stale(text):
            say(f"removing {path.name}: its process isn't running ({text.splitlines()[0].strip()})")
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        if time.monotonic() > deadline:
            raise CaptureError(f"{path} (or a running game) is still there after {max_wait / 60:.0f} min.")
        say(f"waiting: {path.name} is held ({text.splitlines()[0].strip() if text else 'game'})")
        time.sleep(poll)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lock_text(owner))
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    if FFMPEG_FALLBACK.is_file():
        return str(FFMPEG_FALLBACK)
    raise CaptureError("No ffmpeg: install it (winget install Gyan.FFmpeg) or put it on PATH.")


def _bn():
    sys.path.insert(0, str(TOOLS))
    import build_native as bn  # noqa: E402
    return bn


# ------------------------------------------------------------------ build + selftest (native)

SELFTEST_RUN = """import ctypes, sys
lib = ctypes.CDLL(sys.argv[1])
lines = (ctypes.c_int * 32)()
n = lib.smoothcap_selftest(lines, 32)
print("smoothcap selftest: " + ("all passed" if not n else
      f"{n} FAILED at selftest.c lines {', '.join(str(x) for x in lines[:min(n, 32)])}"))
sys.exit(1 if n else 0)
"""


def build(install: Path | None = None) -> Path:
    """smoothcap.dll and its self-test DLL (one build lock), then smoothcap staged as a mod folder."""
    bn = _bn()
    try:
        tc = bn.find_toolchain(install or bn.default_install())
        targets = [bn.Target("smoothcap", (MOD_SRC / "smoothcap.c",), runtime=False, subdir=DLL_SUBDIR,
                             export="swmod_init"),
                   bn.Target("smoothcap_selftest", (MOD_SRC / "selftest.c",), runtime=False, subdir=DLL_SUBDIR,
                             export="smoothcap_selftest")]
        with lock(BUILD_LOCK, "smooth_capture build"):
            dll, _ = bn.build(tc, BUILD_OUT, targets)
        info = bn.pe_info(dll.read_bytes())
    except (bn.BuildError, bn.PeError) as e:
        raise CaptureError(str(e)) from None
    if "swmod_init" not in info.exports:
        raise CaptureError(f"{dll} has no swmod_init export.")
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MOD_SRC / "manifest.toml", STAGE / "mod.toml")
    shutil.copyfile(dll, STAGE / "smoothcap.dll")
    say(f"built {dll}\nstaged {STAGE}")
    return dll


def selftest(install: Path | None = None, rebuild: bool = True) -> int:
    """Build, then run the self-test DLL in a separate Python (a crash there can't take this one down)."""
    if rebuild:
        build(install)
    dll = BUILD_OUT / DLL_SUBDIR / "smoothcap_selftest.dll"
    r = subprocess.run([sys.executable, "-I", "-c", SELFTEST_RUN, str(dll)], capture_output=True, text=True,
                       timeout=120)
    sys.stdout.write(r.stdout or r.stderr)
    return r.returncode


# ------------------------------------------------------------------ the scratch library and the game

def modman(lib: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(TOOLS / "modman.py"), "--library", str(lib), *args]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise CaptureError(f"modman {' '.join(args)} failed:\n{(r.stderr or r.stdout).strip()[-1500:]}")
    return r


def prepare_library(lib: Path, install: Path) -> dict:
    """A scratch library with smoothcap in it, deployed. Returns its launch.json."""
    if not (STAGE / "smoothcap.dll").is_file():
        raise CaptureError("smoothcap.dll isn't built: run python tools/smooth_capture.py build")
    if not lib.is_dir() or not any(lib.iterdir()):
        modman(lib, "init", str(install))
    listed = MOD_NAME.lower() in modman(lib, "list").stdout.lower()
    if listed and library_dll_stale(lib):
        say("smoothcap was rebuilt: putting the new DLL into the library")
        modman(lib, "remove", MOD_ID)
        listed = False
    if not listed:
        modman(lib, "add", str(STAGE))
    modman(lib, "deploy")
    launch = json.loads((lib / "deploy" / "launch.json").read_text(encoding="utf-8"))
    if not launch.get("env", {}).get("SWMOD_DIR"):
        raise CaptureError(f"The deploy of {lib} has no native mods (no SWMOD_DIR): is modhost.dll built?")
    return launch


def library_dll_stale(lib: Path) -> bool:
    """The library's copy of smoothcap.dll isn't the staged one (modman add copies; a rebuild changes only STAGE)."""
    have = lib / "mods" / MOD_ID / "smoothcap.dll"
    try:
        return have.read_bytes() != (STAGE / "smoothcap.dll").read_bytes()
    except OSError:
        return True


def game_env(launch: dict, port: int, settings: Path, save: Path, hidden: bool, extra: dict) -> dict:
    sys.path.insert(0, str(TOOLS))
    import modman as mm  # noqa: E402
    env = mm.game_env(dict(os.environ), launch, port)
    env.update({"SAVAGEWIND_SAVE_DIR": str(save), "SAVAGEWIND_SETTINGS": str(settings),
                "SAVAGEWIND_NO_LAUNCHER": "1", "SAVAGEWIND_NO_CONTROLLER_STEP": "1", "SAVAGEWIND_FPS": "0"})
    if hidden:
        env["SAVAGEWIND_HIDDEN"] = "1"
    env.update(extra)
    return env


def connect(port: int, timeout: float, proc=None):
    """The game's control connection, once it answers (and while the game started here still runs)."""
    sys.path.insert(0, str(TOOLS))
    import control  # noqa: E402
    deadline = time.monotonic() + timeout
    while True:
        if proc is not None and proc.poll() is not None:
            raise CaptureError(f"The game closed (exit code {proc.returncode}): see savagewind.log.")
        try:
            return control.wait_for_game(port, min(10.0, max(1.0, deadline - time.monotonic())))
        except control.ControlError as e:
            if time.monotonic() >= deadline:
                raise CaptureError(f"No control connection on port {port}: {e}") from None


def wait_in_world(ctl, proc, timeout: float, press_a: float = 0.0) -> None:
    """Until Link's position is set and stays (the title screen and loads have none). With press_a, press A every
    press_a seconds while there's no world: the scripted Continue counts presented frames, and a title screen at 200+
    fps gets there before the menu does (seen 2026-11-18: it stayed on "Load save data?")."""
    deadline, steady, last_a = time.monotonic() + timeout, 0, time.monotonic()
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise CaptureError(f"The game closed (exit code {proc.returncode}): see savagewind.log.")
        try:
            x, y, z = ctl.pos()
            steady = steady + 1 if (x, y, z) != (0.0, 0.0, 0.0) else 0
        except Exception:  # (no player yet)
            steady = 0
            if press_a and time.monotonic() - last_a >= press_a:
                last_a = time.monotonic()
                with contextlib.suppress(Exception):
                    ctl.cmd("press", "a")
        if steady >= 3:
            return
        time.sleep(1.0)
    raise CaptureError(f"The game didn't reach the world in {timeout:.0f} s.")


# ------------------------------------------------------------------ capturing

def rec_files(rec_dir: Path) -> list[str]:
    """The runtime's rec_*.png in rec_dir, in frame order ([] when the folder isn't there)."""
    try:
        return ordered_recs(os.listdir(rec_dir))
    except OSError:
        return []


def preflight(plan: Plan, data_root: Path) -> None:
    """Before any folder is made or the game starts: the runtime names its frames rec_<frame>.png and starts counting
    again at each game start, so an old rec_*.png there would be overwritten (yours lost) and then skipped (a frame
    missing from the video). And frames from an earlier capture of this name must be encoded or deleted first."""
    rec_dir = data_root / "build" / "frames"
    old = rec_files(rec_dir)
    if old:
        raise CaptureError(f"{rec_dir} already has {len(old)} recorded frame(s) (rec_*.png, e.g. an F12 recording): "
                           f"move them somewhere else first, they would be overwritten.")
    if plan.frames_dir.exists() and any(plan.frames_dir.iterdir()):
        raise CaptureError(f"{plan.frames_dir} already has frames: encode or delete them first.")


def truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes")


def check_ready(ctl, own_controller_step: bool) -> dict:
    """smoothcap's status before a capture: the clock hooked, the F12 recording there, and (a game this tool started,
    SAVAGEWIND_NO_CONTROLLER_STEP=1) the controller step its own. Else a plain-words error."""
    try:
        st = ctl.cmd("smoothcap", "status")
    except Exception as e:
        raise CaptureError(f"smoothcap doesn't answer ({e}): is it in the game's mod library?") from None
    if not truthy(st.get("clock_hooked")):
        raise CaptureError("smoothcap couldn't hook the game's clock (not Savage Wind 0.4.1-alpha.4?): see "
                           "savagewind.log, the smoothcap line.")
    if not truthy(st.get("record_available")):
        raise CaptureError("This Savage Wind has no F12 recording (g_record_frames): smoothcap can't save frames.")
    if own_controller_step and not truthy(st.get("controller_step")):
        raise CaptureError("smoothcap doesn't drive the controller step (no controller_step site?): see "
                           "savagewind.log, the smoothcap line.")
    if st.get("state") not in (None, "off", "hold"):
        raise CaptureError(f"A capture is already running (smoothcap {st.get('state')}).")
    return st


GOTO_HELP = ("go here before the capture and check Link got there: \"x y z\" or a line from the pos command "
             "(swr:1 x=.. y=.. z=..)")
GOTO_XYZ_RE = re.compile(r"\b([xyz])=(-?[\d.]+)")


def parse_goto(text: str | None) -> tuple[list[str], tuple[float, float, float]] | None:
    """--goto: the goto command's words and where Link should end up."""
    if not text:
        return None
    t = text.strip()
    if t.lower().startswith("goto "):
        t = t[5:].strip()
    found = dict(GOTO_XYZ_RE.findall(t))
    try:
        if t.lower().startswith("swr:") and len(found) == 3:
            return ["goto", t], (float(found["x"]), float(found["y"]), float(found["z"]))
        parts = t.replace(",", " ").split()
        if len(parts) == 3:
            xyz = tuple(float(v) for v in parts)
            return ["goto", *(f"{v:.3f}" for v in xyz)], xyz
    except ValueError:
        pass
    raise CaptureError(f"Bad --goto {text!r}: use \"x y z\" or a line from the pos command (swr:1 x=.. y=.. z=..).")


def go_to(ctl, goto, tries: int = 3, near_m: float = 5.0, wait_s: float = 30.0, sleep=time.sleep) -> None:
    """Send goto, wait for the teleport to finish, check Link is there (within near_m across the ground); again up to
    `tries` times (the runtime refuses a goto while a teleport is still running). Else a plain-words error: the
    capture would start wherever Link happens to be (seen 2026-11-18: still where a fall ended)."""
    words, (tx, ty, tz) = goto
    where = None
    for _ in range(tries):
        with contextlib.suppress(Exception):
            ctl.cmd("release", "all")   # (scripted inputs held: Link must not keep running off)
        try:
            ctl.cmd(*words)
        except Exception as e:   # (e.g. "Teleport in progress")
            say(f"goto: {e}")
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            try:
                st = ctl.cmd("teleport", "status").get("state")
            except Exception:
                st = "done"
            if st in ("done", "failed", "idle"):
                break
            sleep(0.3)
        sleep(1.5)   # (he lands)
        with contextlib.suppress(Exception):
            where = ctl.pos()
        if where and ((where[0] - tx) ** 2 + (where[2] - tz) ** 2) ** 0.5 <= near_m:
            say(f"at {where[0]:.1f} {where[1]:.1f} {where[2]:.1f} (goto {tx:.1f} {ty:.1f} {tz:.1f})")
            return
    at = f"{where[0]:.1f} {where[1]:.1f} {where[2]:.1f}" if where else "unknown"
    raise CaptureError(f"goto didn't take Link to {tx:.1f} {ty:.1f} {tz:.1f} (he's at {at}): the game may ignore it "
                       f"while he falls, glides or climbs. Let him land and try again.")


def settle_recs(ctl, rec_dir: Path, before=(), quiet: float = 2.0, timeout: float = 30.0, sleep=time.sleep) -> list:
    """After smoothcap stop: until its status says off (recording off too) and no new rec_*.png came for `quiet` s,
    delete each one that comes (frames past the ones asked for). Returns the ones that couldn't be deleted."""
    deadline = time.monotonic() + timeout
    seen, last_new, off = set(), time.monotonic(), False
    left = []
    while True:
        left = []
        for n in ordered_recs(os.listdir(rec_dir), before) if rec_dir.is_dir() else []:
            if n not in seen:
                seen.add(n)
                last_new = time.monotonic()
            try:
                (rec_dir / n).unlink()
            except OSError:   # (still open in the game for a moment)
                left.append(n)
        if not off:
            try:
                st = ctl.cmd("smoothcap", "status")
                off = st.get("state") == "off" and not truthy(st.get("recording", False))
            except Exception:   # (the game is gone: nothing more comes)
                off = True
        if off and not left and time.monotonic() - last_new >= quiet:
            return []
        if time.monotonic() > deadline:
            return left
        sleep(0.2)


def collect(ctl, plan: Plan, data_root: Path, proc=None, min_free: float = MIN_FREE_GB,
            stall: float = 180.0, settle: float = 2.0) -> int:
    """Start smoothcap and move its frames into plan.frames_dir as 000000.png... Returns the frames moved."""
    build_dir, rec_dir = data_root / "build", data_root / "build" / "frames"
    preflight(plan, data_root)   # (before making any folder)
    made = [d for d in (build_dir, rec_dir) if not d.exists()]
    rec_dir.mkdir(parents=True, exist_ok=True)   # (the runtime's mkdir makes only the last folder)
    before = set(os.listdir(rec_dir))
    plan.frames_dir.mkdir(parents=True, exist_ok=True)
    moved, finished, last_new, last_status = 0, False, time.monotonic(), 0.0
    finish_by = 0.0
    try:
        reply = ctl.cmd("smoothcap", "start", str(plan.frames), str(plan.fps))
        say(f"capturing {plan.frames} frames ({plan.frames / plan.fps:.2f} s of game time, step "
            f"{step_ms(plan.fps):.3f} ms): {reply}")
        while moved < plan.frames:
            for n in ordered_recs(os.listdir(rec_dir), before):
                if not png_complete(rec_dir / n):
                    break   # (in frame order: the runtime is still writing this one)
                try:
                    os.replace(rec_dir / n, plan.frames_dir / frame_name(moved))
                    moved += 1
                except PermissionError:   # (still open in the game for a moment)
                    break
                last_new = time.monotonic()
                if moved >= plan.frames:
                    break
            if finished:
                if not ordered_recs(os.listdir(rec_dir), before) or time.monotonic() > finish_by:
                    break
                time.sleep(0.2)
                continue
            if moved >= plan.frames:
                break
            if proc is not None and proc.poll() is not None:
                raise CaptureError(f"The game closed during the capture (exit code {proc.returncode}).")
            if free_gb(plan.frames_dir) < min_free:
                say(f"stopping: less than {min_free} GB free on the disk ({moved} frames kept)")
                break
            if time.monotonic() - last_new > stall:
                raise CaptureError(f"No new frame for {stall:.0f} s: see savagewind.log.")
            now = time.monotonic()
            if now - last_status > 1.0:
                last_status = now
                st = ctl.cmd("smoothcap", "status")
                if st.get("state") in ("off", "hold"):
                    finished, finish_by = True, time.monotonic() + 30   # (the last PNGs may still be written)
                    continue
            time.sleep(0.2)
    finally:
        with contextlib.suppress(Exception):
            ctl.cmd("smoothcap", "stop")
        left = settle_recs(ctl, rec_dir, before, quiet=settle)   # (the extras past the frames asked for)
        if left:
            say(f"couldn't delete {len(left)} leftover frame(s) in {rec_dir} (e.g. {left[0]}): delete them before "
                f"the next capture")
        for d in reversed(made):
            with contextlib.suppress(OSError):
                d.rmdir()
    say(f"{moved} frames in {plan.frames_dir}")
    return moved


def encode(plan: Plan, keep_frames: bool = False, crf: int = 18, preset: str = "slow") -> Path:
    frames = sorted(p for p in plan.frames_dir.glob("*.png")) if plan.frames_dir.is_dir() else []
    if not frames:
        raise CaptureError(f"No frames in {plan.frames_dir}.")
    if frames[0].name != frame_name(0) or frames[-1].name != frame_name(len(frames) - 1):
        raise CaptureError(f"The frames in {plan.frames_dir} aren't numbered 000000.png on without gaps.")
    ffmpeg = find_ffmpeg()
    cmd = ffmpeg_cmd(ffmpeg, plan.frames_dir, plan.fps, plan.video, plan.size, crf, preset)
    say(f"encoding {len(frames)} frames -> {plan.video} ({plan.size[0]}x{plan.size[1]}, {plan.fps} fps, CRF {crf})")
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=BELOW_NORMAL if os.name == "nt" else 0)
    if r.returncode or not plan.video.is_file():
        raise CaptureError(f"ffmpeg failed (frames kept):\n{(r.stderr or r.stdout).strip()[-1500:]}")
    got = video_frames(ffmpeg, plan.video)
    if got is not None and got != len(frames):
        raise CaptureError(f"The video has {got} frames, not {len(frames)} (frames kept).")
    if not keep_frames:
        shutil.rmtree(plan.frames_dir)
    say(f"video: {plan.video} ({len(frames)} frames, {len(frames) / plan.fps:.2f} s)")
    return plan.video


def video_frames(ffmpeg: str, video: Path) -> int | None:
    probe = Path(ffmpeg).with_name("ffprobe.exe" if ffmpeg.lower().endswith(".exe") else "ffprobe")
    exe = str(probe) if probe.is_file() else shutil.which("ffprobe")
    if not exe:
        return None
    r = subprocess.run([exe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames",
                        "-of", "csv=p=0", str(video)], capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def close_game(ctl, proc, timeout: float = 60.0) -> None:
    """Close the game this tool started: the control command exit, then wait for the process."""
    with contextlib.suppress(Exception):
        ctl.cmd("exit")
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        say("the game didn't close after exit: stopping it")
        proc.terminate()
        proc.wait(timeout=30)


def record(args) -> int:
    plan = make_plan(args.name, args.seconds, args.fps, args.four_k)
    install = Path(args.install)
    real_library = install.parent / "modloader"
    lib, save_src = Path(args.library).resolve(), Path(args.save).resolve()
    check_scratch(lib, "library", install, real_library)
    check_scratch(save_src, "save", install, real_library)
    if not save_src.is_dir():
        raise CaptureError(f"No test save at {save_src}: copy one there first (never the real save).")
    need = MIN_FREE_GB + estimate_bytes(plan.frames, plan.size) / 1e9
    if free_gb(OUT) < need:
        raise CaptureError(f"{plan.frames} frames need about {need:.1f} GB free on the disk "
                           f"({free_gb(OUT):.1f} GB now): capture fewer seconds.")
    goto = parse_goto(args.goto)
    preflight(plan, install)   # (before any folder is made or the game starts)
    plan.folder.mkdir(parents=True, exist_ok=True)
    save = plan.folder / "save"
    if save.exists():
        shutil.rmtree(save)
    shutil.copytree(save_src, save)
    base_ini = Path(args.settings) if args.settings else install / "savagewind.ini"
    text = base_ini.read_text(encoding="utf-8", errors="replace") if base_ini.is_file() else ""
    settings = plan.folder / "savagewind.ini"
    settings.write_text(capture_settings(text, (args.width, args.height)), encoding="utf-8")
    launch = prepare_library(lib, install)
    extra = parse_env(args.env)
    if args.auto_continue and "SAVAGEWIND_INPUT" not in extra:
        extra["SAVAGEWIND_INPUT"] = CONTINUE_INPUT
    env = game_env(launch, args.port, settings, save, args.hidden, extra)
    data_root = Path(env.get("SAVAGEWIND_DATA_ROOT") or launch["cwd"])
    with lock(GAME_LOCK, args.lock_name, max_wait=150 * 60, also=game_running):
        if free_gb(OUT) < need:
            raise CaptureError(f"Less than {need:.1f} GB free on the disk now.")
        preflight(plan, data_root)   # (again: the data root, and a recording made while this waited)
        say(f"starting Savage Wind (library {lib}, save copy {save}, settings {settings}, port {args.port})")
        proc = subprocess.Popen(launch["argv"], cwd=launch["cwd"], env=env,
                                creationflags=(BELOW_NORMAL | NEW_GROUP) if os.name == "nt" else 0)
        ctl = None
        try:
            ctl = connect(args.port, args.world_timeout, proc)
            wait_in_world(ctl, proc, args.world_timeout, press_a=20.0 if args.auto_continue else 0.0)
            for line in args.ctl:
                say(f"> {line}: {ctl.cmd(line.split())}")
            check_ready(ctl, own_controller_step=True)
            if goto:
                go_to(ctl, goto)
            if args.enter:
                input("Press Enter to start the capture...")
            elif args.wait:
                say(f"starting in {args.wait:.0f} s")
                time.sleep(args.wait)
            moved = collect(ctl, plan, data_root, proc)
        finally:
            if ctl is not None:
                close_game(ctl, proc)
                ctl.close()
            elif proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=30)
    if not moved:
        raise CaptureError("No frames were saved: see savagewind.log ([shot] and [mod] smoothcap lines).")
    if args.no_encode:
        return 0
    encode(plan, args.keep_frames, args.crf, args.preset)
    return 0


def attach(args) -> int:
    plan = make_plan(args.name, args.seconds, args.fps, args.four_k)
    need = MIN_FREE_GB + estimate_bytes(plan.frames, plan.size) / 1e9
    if free_gb(OUT) < need:
        raise CaptureError(f"{plan.frames} frames need about {need:.1f} GB free on the disk.")
    goto = parse_goto(args.goto)
    data_root = Path(args.data_root or args.install)
    preflight(plan, data_root)
    ctl = connect(args.port, 30)
    try:
        check_ready(ctl, own_controller_step=False)
        if goto:
            go_to(ctl, goto)
        if args.enter:
            input("Press Enter to start the capture...")
        plan.folder.mkdir(parents=True, exist_ok=True)
        moved = collect(ctl, plan, data_root)
    finally:
        ctl.close()
    if not moved:
        raise CaptureError("No frames were saved: see savagewind.log.")
    if not args.no_encode:
        encode(plan, args.keep_frames, args.crf, args.preset)
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="smooth_capture.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    default_install = str(Path.home() / "Documents" / "SavageWind" / "Savage Wind")

    def common(p, capture=True):
        p.add_argument("--install", default=default_install, help="the Savage Wind folder")
        if not capture:
            return
        p.add_argument("--name", required=True, help="the capture's name: out\\capture-<name>")
        p.add_argument("--fps", type=int, default=60, help="the video's frame rate (the game steps 1/(2 fps) s)")
        p.add_argument("--4k", dest="four_k", action="store_true", help="upscale to 3840x2160 (else 1920x1080)")
        p.add_argument("--crf", type=int, default=18, help="H.264 quality (lower: better, bigger)")
        p.add_argument("--preset", default="slow", help="x264 preset")
        p.add_argument("--keep-frames", action="store_true", help="keep the PNG frames after encoding")

    for name in ("build", "selftest"):
        common(sub.add_parser(name), capture=False)
    p = sub.add_parser("record", help="start a test game, capture, encode")
    common(p)
    p.add_argument("--seconds", type=float, required=True, help="game time to record")
    p.add_argument("--library", default=str(DEFAULT_LIBRARY), help="a scratch mod library in out\\")
    p.add_argument("--save", default=str(DEFAULT_SAVE), help="a test save folder in out\\ (copied first)")
    p.add_argument("--settings", help="a savagewind.ini to start from (copied; default: the game's, read only)")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--hidden", action="store_true", help="no game window (SAVAGEWIND_HIDDEN=1)")
    p.add_argument("--auto-continue", action="store_true", help="press Continue on the title screen (scripted "
                   "input: your own controller is ignored then)")
    p.add_argument("--ctl", action="append", default=[], help="a control command to send before the capture")
    p.add_argument("--goto", help=GOTO_HELP)
    p.add_argument("--env", action="append", default=[], help="KEY=VALUE for the game")
    p.add_argument("--wait", type=float, default=5.0, help="seconds in the world before the capture")
    p.add_argument("--enter", action="store_true", help="start the capture when you press Enter here")
    p.add_argument("--world-timeout", type=float, default=600.0)
    p.add_argument("--lock-name", default="smooth_capture", help="what out\\game.lock says while the game runs")
    p.add_argument("--no-encode", action="store_true")
    p = sub.add_parser("attach", help="capture from a running game that has smoothcap")
    common(p)
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--data-root", help="where the game writes build\\frames (default: the Savage Wind folder)")
    p.add_argument("--goto", help=GOTO_HELP)
    p.add_argument("--enter", action="store_true")
    p.add_argument("--no-encode", action="store_true")
    p = sub.add_parser("encode", help="encode out\\capture-<name>\\frames")
    common(p)
    p = sub.add_parser("check-log", help="the game's step during the last capture, from savagewind.log "
                       "(start the game with --env SAVAGEWIND_CLOCK_PROBE=1000)")
    common(p, capture=False)
    p.add_argument("--fps", type=int, default=60)
    p.add_argument("--log", help="the log (default: savagewind.log in the Savage Wind folder)")
    return ap


def check_log(args) -> int:
    log = Path(args.log) if args.log else Path(args.install) / "savagewind.log"
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        raise CaptureError(f"Can't read {log}: {e}") from None
    for line in lines:
        if "smoothcap:" in line:
            say(line.strip())
    steps = clock_steps(lines)
    want = 15.0 / check_fps(args.fps)
    if not steps:
        raise CaptureError("No [clock] lines during a recording: start the game with SAVAGEWIND_CLOCK_PROBE=1000.")
    bad = [s for s in steps if abs(s - want) > 0.02]
    say(f"step during the capture: {len(steps)} samples, {min(steps):.3f} .. {max(steps):.3f} (want {want:.3f}, "
        f"1/30 s units): {'FIXED' if not bad else f'{len(bad)} off'}")
    return 0 if not bad else 1


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.cmd == "build":
            build(Path(args.install))
            return 0
        if args.cmd == "selftest":
            return selftest(Path(args.install))
        if args.cmd == "record":
            return record(args)
        if args.cmd == "attach":
            return attach(args)
        if args.cmd == "check-log":
            return check_log(args)
        plan = make_plan(args.name, 1, args.fps, args.four_k)
        encode(plan, args.keep_frames, args.crf, args.preset)
        return 0
    except RuntimeError as e:   # (CaptureError, and control.py's ControlError: the game answered with an error)
        say(f"error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
