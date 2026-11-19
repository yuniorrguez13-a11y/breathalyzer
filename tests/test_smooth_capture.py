"""Tests for tools/smooth_capture.py: the capture's logic with made-up folders and a fake game connection. No game,
no window, no compiler (the native self-test is `python tools/smooth_capture.py selftest`)."""
import contextlib
import io
import os
import shutil
import struct
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import smooth_capture as sc  # noqa: E402


def tiny_png(path: Path, w: int = 64, h: int = 36, shade: int = 0) -> None:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    rows = b"".join(b"\0" + bytes([shade, 255 - shade, 128]) * w for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class Numbers(unittest.TestCase):
    def test_names(self):
        for good in ("castle", "Trailer_2", "a-b"):
            self.assertEqual(sc.check_name(good), good)
        for bad in ("", "../x", "a b", "-x", "x" * 41, "c:\\x"):
            with self.assertRaises(sc.CaptureError):
                sc.check_name(bad)
        self.assertEqual(sc.capture_dir("castle", Path("o")), Path("o") / "capture-castle")

    def test_frames_and_step(self):
        self.assertEqual(sc.frames_for(10, 60), 600)
        self.assertEqual(sc.frames_for(0.01, 60), 1)
        self.assertAlmostEqual(sc.step_ms(60), 1000 / 120)   # (1/120 s: one present in two is saved)
        self.assertAlmostEqual(sc.step_ms(30), 1000 / 60)
        with self.assertRaises(sc.CaptureError):
            sc.frames_for(0, 60)
        for bad in (14, 151):
            with self.assertRaises(sc.CaptureError):
                sc.check_fps(bad)
        self.assertEqual(sc.out_size(False), (1920, 1080))
        self.assertEqual(sc.out_size(True), (3840, 2160))
        self.assertGreater(sc.estimate_bytes(600, (1920, 1080)), 1e9)

    def test_plan(self):
        p = sc.make_plan("castle", 2.5, 60, True, Path("o"))
        self.assertEqual((p.frames, p.fps, p.size), (150, 60, (3840, 2160)))
        self.assertEqual(p.frames_dir, Path("o") / "capture-castle" / "frames")
        self.assertEqual(p.video, Path("o") / "capture-castle" / "castle.mp4")


class Settings(unittest.TestCase):
    def test_overrides_replace_and_append(self):
        text = "# comment\nframe_rate = 30\nvsync = fast\nupscaler = fsr1\nframe_rate = 60\nvolume = 1.00\n"
        out = sc.capture_settings(text, (1920, 1080))
        lines = out.splitlines()
        self.assertEqual(lines[0], "# comment")
        self.assertEqual([l for l in lines if l.startswith("frame_rate")], ["frame_rate = off"])
        self.assertIn("vsync = off", lines)
        self.assertIn("upscaler = fsr1", lines)       # (not ours: kept)
        self.assertIn("volume = 1.00", lines)
        for key in ("frame_generation = 0", "fullscreen = 0", "perf_overlay = off", "window_size = 1920x1080"):
            self.assertIn(key, lines)
        self.assertTrue(out.endswith("\n"))

    def test_no_window_and_extra(self):
        out = sc.capture_settings("", None, {"upscaler": "off"})
        self.assertNotIn("window_size", out)
        self.assertIn("upscaler = off", out)


class Frames(unittest.TestCase):
    def test_ordered_recs(self):
        names = ["rec_00010.png", "rec_00008.png", "rec_100000.png", "frame_00001.png", "rec_x.png", "REC_00012.PNG",
                 "rec_00004.png"]
        self.assertEqual(sc.ordered_recs(names, ignore=["rec_00004.png"]),
                         ["rec_00008.png", "rec_00010.png", "REC_00012.PNG", "rec_100000.png"])
        self.assertEqual(sc.rec_number("rec_00042.png"), 42)
        self.assertIsNone(sc.rec_number("rec_00042.png.tmp"))
        self.assertEqual(sc.frame_name(7), "000007.png")

    def test_png_complete(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rec_00002.png"
            tiny_png(p)
            self.assertTrue(sc.png_complete(p))
            p.write_bytes(p.read_bytes()[:-5])   # (still being written)
            self.assertFalse(sc.png_complete(p))
            p.write_bytes(b"")
            self.assertFalse(sc.png_complete(p))
            self.assertFalse(sc.png_complete(Path(d) / "missing.png"))

    def test_parse_env(self):
        self.assertEqual(sc.parse_env(["A=1", "SAVAGEWIND_INPUT=900:A:8,1400:A:8", "E="]),
                         {"A": "1", "SAVAGEWIND_INPUT": "900:A:8,1400:A:8", "E": ""})
        for bad in ("A", "=1", "1A=2", "A B=1"):
            with self.assertRaises(sc.CaptureError):
                sc.parse_env([bad])

    def test_ffmpeg_cmd(self):
        cmd = sc.ffmpeg_cmd("ffmpeg", Path("f"), 60, Path("v.mp4"), (3840, 2160), 18, "slow")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertEqual(cmd[-1], "v.mp4")
        self.assertEqual(cmd[cmd.index("-framerate") + 1], "60")
        self.assertEqual(cmd[cmd.index("-r") + 1], "60")
        self.assertEqual(cmd[cmd.index("-i") + 1], str(Path("f") / "%06d.png"))
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "libx264")
        self.assertEqual(cmd[cmd.index("-crf") + 1], "18")
        vf = cmd[cmd.index("-vf") + 1]
        self.assertIn("scale=3840:2160", vf)
        self.assertIn("lanczos", vf)
        self.assertIn("yuv420p", vf)


class Scratch(unittest.TestCase):
    def test_only_copies_in_out(self):
        with tempfile.TemporaryDirectory() as d:
            install, real = Path(d) / "Savage Wind", Path(d) / "modloader"
            for bad in (install / "save", install / "save" / "save_0", install, real, real / "x", Path(d) / "else"):
                with self.assertRaises(sc.CaptureError, msg=str(bad)):
                    sc.check_scratch(bad, "save", install, real)
            sc.check_scratch(sc.OUT / "test-save", "save", install, real)
            sc.check_scratch(sc.OUT / "capture-lib", "library", install, real)


class FakeControl:
    """The game's control connection with smoothcap: each status call 'saves' the next frames into rec_dir."""

    def __init__(self, rec_dir: Path, per_call: int, total: int, first: int = 1000):
        self.rec_dir, self.per_call, self.total, self.next = rec_dir, per_call, total, first
        self.saved, self.calls, self.stopped = 0, [], False

    def cmd(self, *words):
        self.calls.append(words)
        if words[:2] == ("smoothcap", "start"):
            return {"state": "warm-up", "frames": int(words[2])}
        if words[:2] == ("smoothcap", "stop"):
            self.stopped = True
            return {"state": "off"}
        if words[:2] == ("smoothcap", "status"):
            for _ in range(min(self.per_call, self.total - self.saved)):
                tiny_png(self.rec_dir / f"rec_{self.next:05d}.png", shade=self.saved % 256)
                self.next += 2   # (every other present)
                self.saved += 1
            return {"state": "recording" if self.saved < self.total else "off"}
        raise AssertionError(words)


class Collect(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "game"
        self.root.mkdir()
        self.plan = sc.Plan("t", 5, 60, (128, 72), self.tmp / "capture-t")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_collect(self, ctl):
        with contextlib.redirect_stdout(io.StringIO()):
            return sc.collect(ctl, self.plan, self.root, min_free=0, settle=0.05)

    def test_moves_frames_in_order_and_cleans_up(self):
        ctl = FakeControl(self.root / "build" / "frames", per_call=3, total=6)
        (self.root / "build" / "frames").mkdir(parents=True)
        (self.root / "build" / "frames" / "notes.txt").write_text("x")   # (not a recording: left alone)
        moved = self.run_collect(ctl)
        self.assertEqual(moved, 5)
        self.assertEqual(sorted(os.listdir(self.plan.frames_dir)), [sc.frame_name(i) for i in range(5)])
        self.assertEqual(os.listdir(self.root / "build" / "frames"), ["notes.txt"])   # (the 6th one deleted)
        self.assertTrue(ctl.stopped)
        self.assertEqual(ctl.calls[0], ("smoothcap", "start", "5", "60"))
        self.assertIn(("smoothcap", "status"), ctl.calls[ctl.calls.index(("smoothcap", "stop")):])   # (S3: waits)

    def test_refuses_old_recordings_before_making_folders(self):
        rec = self.root / "build" / "frames"
        rec.mkdir(parents=True)
        tiny_png(rec / "rec_00002.png")   # (an F12 recording: would be overwritten by frame 2)
        ctl = FakeControl(rec, 1, 1)
        with self.assertRaises(sc.CaptureError) as e:
            self.run_collect(ctl)
        self.assertIn("rec_", str(e.exception))
        self.assertEqual(ctl.calls, [])   # (not started)
        self.assertFalse(self.plan.frames_dir.exists())
        self.assertEqual(os.listdir(rec), ["rec_00002.png"])
        with self.assertRaises(sc.CaptureError):
            sc.preflight(self.plan, self.root)

    def test_late_frames_after_stop_deleted(self):
        rec = self.root / "build" / "frames"

        class Late(FakeControl):
            '''Two more frames come after stop, and the status says recording until then.'''
            late = 2

            def cmd(self, *words):
                if words[:2] == ("smoothcap", "status") and self.stopped:
                    self.calls.append(words)
                    if self.late:
                        tiny_png(self.rec_dir / f"rec_{self.next:05d}.png")
                        self.next += 2
                        self.late -= 1
                        return {"state": "off", "recording": True}
                    return {"state": "off", "recording": False}
                return super().cmd(*words)

        ctl = Late(rec, per_call=5, total=5)
        self.assertEqual(self.run_collect(ctl), 5)
        self.assertEqual(ctl.late, 0)
        self.assertFalse((self.root / "build").exists())

    def test_made_folders_removed(self):
        ctl = FakeControl(self.root / "build" / "frames", per_call=5, total=5)
        self.assertEqual(self.run_collect(ctl), 5)
        self.assertFalse((self.root / "build").exists())   # (made for the capture, empty again: gone)

    def test_refuses_old_frames(self):
        self.plan.frames_dir.mkdir(parents=True)
        tiny_png(self.plan.frames_dir / "000000.png")
        with self.assertRaises(sc.CaptureError):
            self.run_collect(FakeControl(self.root / "build" / "frames", 1, 1))

    def test_low_disk_stops(self):
        ctl = FakeControl(self.root / "build" / "frames", per_call=1, total=50)
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(sc, "free_gb", return_value=1.0):
            moved = sc.collect(ctl, self.plan, self.root, min_free=4.5)
        self.assertEqual(moved, 0)
        self.assertTrue(ctl.stopped)


@unittest.skipUnless(shutil.which("ffmpeg"), "no ffmpeg on PATH")
class Encode(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.plan = sc.Plan("t", 4, 60, (128, 72), self.tmp / "capture-t")
        self.plan.frames_dir.mkdir(parents=True)
        for i in range(4):
            tiny_png(self.plan.frames_dir / sc.frame_name(i), shade=i * 60)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_encode_then_frames_deleted(self):
        with contextlib.redirect_stdout(io.StringIO()):
            video = sc.encode(self.plan, preset="ultrafast")
        self.assertTrue(video.is_file() and video.stat().st_size > 0)
        self.assertFalse(self.plan.frames_dir.exists())

    def test_keep_frames(self):
        with contextlib.redirect_stdout(io.StringIO()):
            sc.encode(self.plan, keep_frames=True, preset="ultrafast")
        self.assertEqual(len(os.listdir(self.plan.frames_dir)), 4)


class EncodeChecks(unittest.TestCase):
    def test_gaps_and_nothing_refused(self):
        with tempfile.TemporaryDirectory() as d:
            plan = sc.Plan("t", 2, 60, (128, 72), Path(d) / "capture-t")
            with self.assertRaises(sc.CaptureError):
                sc.encode(plan)
            plan.frames_dir.mkdir(parents=True)
            tiny_png(plan.frames_dir / "000000.png")
            tiny_png(plan.frames_dir / "000002.png")
            with self.assertRaises(sc.CaptureError):
                sc.encode(plan)

    def test_main_reports_errors(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sc, "OUT", Path(d)), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(sc.main(["encode", "--name", "nothing-here"]), 1)
        self.assertIn("error:", out.getvalue())

    def test_parser(self):
        a = sc.parser().parse_args(["record", "--name", "x", "--seconds", "3", "--4k", "--ctl", "warp 1 2 3",
                                     "--env", "A=B", "--auto-continue", "--hidden"])
        self.assertEqual((a.name, a.seconds, a.four_k, a.fps, a.ctl, a.env), ("x", 3.0, True, 60, ["warp 1 2 3"],
                                                                              ["A=B"]))
        self.assertTrue(a.auto_continue and a.hidden)
        self.assertEqual(a.port, sc.DEFAULT_PORT)


class ClockLog(unittest.TestCase):
    LOG = """[mod] smoothcap: ready (clock hooked, gui yes, arrows yes, sound yes, recording yes, controller step ours)
[clock] t 1.000 frames 100 game 140.00 tod 120.0 pos 1 2 3 stamina 1000.0 fps 0 at 5.0
[shot] recording on at frame 130
[clock] t 2.000 frames 140 game 160.00 tod 120.0 pos 1 2 3 stamina 1000.0 fps 0 at 6.0
[clock] t 3.000 frames 144 game 161.00 tod 120.0 pos 1 2 3 stamina 1000.0 fps 0 at 7.0
[clock] t 4.000 frames 150 game 162.50 tod 120.0 pos 1 2 3 stamina 1000.0 fps 0 at 8.0
[shot] recording off at frame 160
[clock] t 5.000 frames 200 game 200.00 tod 120.0 pos 1 2 3 stamina 1000.0 fps 0 at 9.0
"""

    def test_steps_while_recording(self):
        self.assertEqual(sc.clock_steps(self.LOG.splitlines()), [0.25, 0.25])
        self.assertEqual(sc.clock_steps(["[clock] t 1 frames 1 game 1.00"]), [])

    def test_check_log(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "savagewind.log"
            log.write_text(self.LOG)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(sc.main(["check-log", "--log", str(log)]), 0)
                self.assertEqual(sc.main(["check-log", "--log", str(log), "--fps", "30"]), 1)
                log.write_text("nothing at all")
                self.assertEqual(sc.main(["check-log", "--log", str(log)]), 1)
            self.assertIn("FIXED", out.getvalue())


class Locks(unittest.TestCase):
    def test_lock_waits_then_holds(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "game.lock"
            with sc.lock(p, "me", poll=0.01):
                text = p.read_text()
                self.assertEqual(text.splitlines()[0], "me")
                self.assertIn(f"pid={os.getpid()}", text)
                self.assertFalse(sc.lock_stale(text))   # (this process runs)
            self.assertFalse(p.exists())
            p.write_text("other")
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(sc.CaptureError):
                with sc.lock(p, "me", poll=0.01, max_wait=0.05):
                    pass
            self.assertEqual(p.read_text(), "other")


    def test_stale_lock_removed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "game.lock"
            p.write_text(sc.lock_text("old capture", pid=99999999, started=1.0))   # (no such process)
            with contextlib.redirect_stdout(io.StringIO()), sc.lock(p, "me", poll=0.01, max_wait=0.05):
                self.assertEqual(p.read_text().splitlines()[0], "me")
            # a pid used again by a process that started after the lock was written: stale too
            text = sc.lock_text("old", pid=1234, started=1000.0)
            self.assertTrue(sc.lock_stale(text, started_of=lambda pid: 2000.0))
            self.assertFalse(sc.lock_stale(text, started_of=lambda pid: 900.0))
            self.assertTrue(sc.lock_stale(text, started_of=lambda pid: None))
            self.assertFalse(sc.lock_stale("room job", started_of=lambda pid: None))   # (no pid: another job's)


class Library(unittest.TestCase):
    def test_dll_compared_with_stage(self):
        with tempfile.TemporaryDirectory() as d:
            lib, stage = Path(d) / "lib", Path(d) / "stage"
            (lib / "mods" / sc.MOD_ID).mkdir(parents=True)
            stage.mkdir()
            (stage / "smoothcap.dll").write_bytes(b"new")
            with mock.patch.object(sc, "STAGE", stage):
                self.assertTrue(sc.library_dll_stale(lib))   # (missing)
                (lib / "mods" / sc.MOD_ID / "smoothcap.dll").write_bytes(b"old")
                self.assertTrue(sc.library_dll_stale(lib))
                (lib / "mods" / sc.MOD_ID / "smoothcap.dll").write_bytes(b"new")
                self.assertFalse(sc.library_dll_stale(lib))


class Ready(unittest.TestCase):
    class Ctl:
        def __init__(self, **status):
            self.status = {"state": "off", "clock_hooked": True, "record_available": True, "controller_step": True,
                           **status}

        def cmd(self, *words):
            return dict(self.status)

    def test_checks(self):
        sc.check_ready(self.Ctl(), own_controller_step=True)
        sc.check_ready(self.Ctl(controller_step=False), own_controller_step=False)   # (attach: the runtime's)
        for bad in ({"clock_hooked": False}, {"record_available": False}, {"controller_step": False},
                    {"state": "recording"}):
            with self.assertRaises(sc.CaptureError):
                sc.check_ready(self.Ctl(**bad), own_controller_step=True)


class GoTo(unittest.TestCase):
    def test_parse(self):
        self.assertIsNone(sc.parse_goto(None))
        words, xyz = sc.parse_goto("-1037.7 250.8 1809.2")
        self.assertEqual(words[0], "goto")
        self.assertEqual(xyz, (-1037.7, 250.8, 1809.2))
        line = "swr:1 x=-1037.72 y=250.83 z=1809.20 hour=12.5"
        self.assertEqual(sc.parse_goto(line), (["goto", line], (-1037.72, 250.83, 1809.2)))
        for bad in ("here", "1 2", "a b c"):
            with self.assertRaises(sc.CaptureError):
                sc.parse_goto(bad)

    class Game:
        '''goto is ignored `ignore` times (a fall), then Link is there.'''
        def __init__(self, ignore):
            self.ignore, self.at, self.sent = ignore, (0.0, 0.0, 0.0), []

        def cmd(self, *words):
            self.sent.append(words)
            if words[0] == "goto":
                if self.ignore:
                    self.ignore -= 1
                else:
                    self.at = (10.0, 5.0, 20.0)
            return {"state": "done"}

        def pos(self):
            return self.at

    def test_retries_and_checks(self):
        goto = sc.parse_goto("10 5 20")
        g = self.Game(1)
        with contextlib.redirect_stdout(io.StringIO()):
            sc.go_to(g, goto, sleep=lambda s: None)
        self.assertEqual(sum(w[0] == "goto" for w in g.sent), 2)
        g = self.Game(5)
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(sc.CaptureError) as e:
            sc.go_to(g, goto, sleep=lambda s: None)
        self.assertIn("didn't take Link", str(e.exception))


class WorldWait(unittest.TestCase):
    class Title:
        """No world until A is pressed (a title screen the scripted Continue missed)."""
        def __init__(self):
            self.sent = []

        def pos(self):
            if ("press", "a") not in self.sent:
                raise RuntimeError("No world loaded")
            return (1.0, 2.0, 3.0)

        def cmd(self, *words):
            self.sent.append(words)
            return {}

    def test_presses_a_only_when_asked(self):
        with mock.patch.object(sc.time, "sleep"):
            t = self.Title()
            sc.wait_in_world(t, None, 5.0, press_a=1e-9)
            self.assertEqual(t.sent, [("press", "a")])
            t = self.Title()
            with self.assertRaises(sc.CaptureError):
                sc.wait_in_world(t, None, 0.05)
            self.assertEqual(t.sent, [])


if __name__ == "__main__":
    unittest.main()
