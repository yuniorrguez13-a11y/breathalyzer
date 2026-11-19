"""Tests for the co-op agent (tools/mp/coop_agent.py): a host and a friend on 127.0.0.1 with stand-in games and
made-up save files (never a real save, never the game)."""
import asyncio
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from mp import coop_agent, story  # noqa: E402

NAMES = ["FirstTower_Activated", "Wind_Relic_Finish", "Clear_RemainsWind", "Open_Dungeon000", "Clear_Dungeon000",
         "MapTower_01", "IsGet_MemoryPhoto_001", "IsGet_PlayerStole2", "IsGet_Obj_Camera", "IsPlayed_Demo001_0"]


def make_save(values: dict) -> bytes:
    out = bytearray(struct.pack("<3I", story.SAVE_MAGIC, 0xffffffff, 1))
    for name, v in values.items():
        out += struct.pack("<II", story.name_hash(name), v)
    return bytes(out + b"\0\0\0\0")


def quiet(*_a, **_k):
    pass


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.host_lib, self.guest_lib = root / "host-lib", root / "guest-lib"
        self.coop = root / "guest-lib" / "joined" / "world1"
        for slot in ("0", "1"):
            (self.coop / "save_0" / slot).mkdir(parents=True)
            (self.coop / "save_0" / slot / story.SAVE_NAME).write_bytes(make_save({n: 0 for n in NAMES}))
        story.mark_coop_copy(self.coop)
        self.sl = story.StoryList(NAMES)

    def tearDown(self):
        self.tmp.cleanup()

    async def run_pair(self, share, *, code=None, guest_code=None, flip=None):
        host_game = coop_agent.FakeGame(21.0, {0, 2, 7})
        guest_game = coop_agent.FakeGame(9.0, set())
        stop = asyncio.Event()
        port = asyncio.get_running_loop().create_future()
        host = asyncio.create_task(coop_agent.host_session(
            self.host_lib, "Ana", host_game, self.sl, code=code, port=0, relay_host="127.0.0.1", stop=stop, log=quiet,
            tick=0.1, started=port.set_result))
        p = await asyncio.wait_for(port, 5)
        await asyncio.sleep(0.3)
        gstop = asyncio.Event()
        guest = asyncio.create_task(coop_agent.join_session(
            self.guest_lib, "Ben", "127.0.0.1", self.coop, self.sl, game=guest_game, code=guest_code, port=p,
            share=share, stop=gstop, log=quiet, processes=[]))
        await asyncio.sleep(2.0)   # (a slow PC, like the GitHub runner, needs more than 1 s to join)
        if flip is not None:
            host_game.bits.add(flip)
            for _ in range(80):     # (up to 8 s for the live change to reach the friend's game)
                await asyncio.sleep(0.1)
                if flip in guest_game.bits:
                    break
        stop.set()
        out = await asyncio.wait_for(guest, 5)
        counts = await asyncio.wait_for(host, 5)
        return out, counts, host_game, guest_game

    async def test_story_at_join_and_live(self):
        out, counts, host_game, guest_game = await self.run_pair(True, flip=5)
        self.assertIsNone(out["error"])
        self.assertIn("Ana's story added", out["merged"])
        self.assertTrue(Path(out["backup"]).is_dir())                    # backup before the merge
        for f in story.slot_files(self.coop):                             # every slot got the host's story
            self.assertEqual(story.story_bits(f, NAMES), {0, 2, 7})
        self.assertIn(21.0, guest_game.set_hours)                         # the host's time
        self.assertIn(5, guest_game.bits)                                 # a live change reached the friend's game
        self.assertEqual(out["ended"], "Ana ended the session")
        self.assertGreater(counts["story"], 0)

    async def test_no_means_no_story(self):
        out, _, _, guest_game = await self.run_pair(False, flip=5)
        self.assertIsNone(out["merged"])
        self.assertIsNone(out["backup"])
        for f in story.slot_files(self.coop):
            self.assertEqual(story.story_bits(f, NAMES), set())
        self.assertNotIn(5, guest_game.bits)
        self.assertIn(21.0, guest_game.set_hours)                         # time is still shared

    async def test_room_code(self):
        out, *_ = await self.run_pair(True, code="apple river stone", guest_code="apple river rock")
        self.assertEqual(out["error"], "wrong room code")
        for f in story.slot_files(self.coop):
            self.assertEqual(story.story_bits(f, NAMES), set())
        out, *_ = await self.run_pair(True, code="apple river stone", guest_code="Apple River Stone")
        self.assertIsNone(out["error"])
        self.assertIn("Ana's story added", out["merged"])

    # -- helpers for the longer cases --

    async def start_host(self, game, **kw):
        stop = asyncio.Event()
        port = asyncio.get_running_loop().create_future()
        kw.setdefault("log", quiet)
        task = asyncio.create_task(coop_agent.host_session(
            self.host_lib, "Ana", game, self.sl, port=0, relay_host="127.0.0.1", stop=stop, tick=0.1,
            started=port.set_result, **kw))
        return task, stop, await asyncio.wait_for(port, 5)

    def guest(self, p, game=None, **kw):
        stop = asyncio.Event()
        kw.setdefault("log", quiet)
        kw.setdefault("processes", [])
        kw.setdefault("share", True)
        task = asyncio.create_task(coop_agent.join_session(
            self.guest_lib, "Ben", "127.0.0.1", self.coop, self.sl, game=game, port=p, stop=stop, tick=0.1, **kw))
        return task, stop

    def host_save(self, bits):
        folder = Path(self.tmp.name) / "host-lib" / "worlds" / "Anas_world"
        (folder / "save_0" / "0").mkdir(parents=True, exist_ok=True)
        (folder / "save_0" / "0" / story.SAVE_NAME).write_bytes(
            make_save({n: int(i in bits) for i, n in enumerate(NAMES)}))
        return folder

    async def test_real_host_game_shares_the_story_of_its_save(self):
        """Two laptops, the host with his real game: the ghost mod can't read story live (ready false), so the story
        comes from his world save. The friend still gets it merged before his game starts."""
        control = coop_agent.ControlGame(7777, len(NAMES), Path("story-list.txt"))
        control._Control = FakeControlNotReady
        host_game = coop_agent.HostGame(control, coop_agent.SaveStory(self.host_save({0, 2, 7}), NAMES, settle=0))
        self.assertIsNone(control.story_get())                           # (live reads are off)
        host, hstop, p = await self.start_host(host_game)
        await asyncio.sleep(0.5)
        g, gstop = self.guest(p, coop_agent.FakeGame(9.0, set()))
        await asyncio.sleep(1.0)
        hstop.set()
        out = await asyncio.wait_for(g, 5)
        await asyncio.wait_for(host, 5)
        self.assertIn("Ana's story added", out["merged"])
        for f in story.slot_files(self.coop):
            self.assertEqual(story.story_bits(f, NAMES), {0, 2, 7})

    async def test_new_host_progress_reaches_later_joiners(self):
        folder = self.host_save({0})
        save = coop_agent.SaveStory(folder, NAMES, settle=0)
        host, hstop, p = await self.start_host(coop_agent.HostGame(None, save, 12.0))
        await asyncio.sleep(0.4)
        (folder / "save_0" / "0" / story.SAVE_NAME).write_bytes(          # the host's game saves: a tower more
            make_save({n: int(i in (0, 5)) for i, n in enumerate(NAMES)}))
        await asyncio.sleep(0.6)
        g, gstop = self.guest(p)
        await asyncio.sleep(0.8)
        hstop.set()
        out = await asyncio.wait_for(g, 5)
        await asyncio.wait_for(host, 5)
        self.assertEqual(story.story_bits(story.slot_files(self.coop)[0], NAMES), {0, 5})
        self.assertEqual(out["ended"], "Ana ended the session")

    async def test_silent_host_game_keeps_its_place(self):
        """The host's game gives nothing for a while (not started, a 70 s load): the host's own line pings, so the
        relay keeps it, and a friend who joins then still has a host."""
        silent = coop_agent.FakeGame(None)
        silent.bits = None                                                # (no hour, no story)
        host, hstop, p = await self.start_host(silent, relay_kw={"idle_timeout": 0.5}, ping_every=0.2)
        await asyncio.sleep(1.5)                                          # three idle timeouts of silence
        g, gstop = self.guest(p, coop_agent.FakeGame(9.0, set()), story_wait=0.2, ping_every=0.2)
        await asyncio.sleep(0.6)
        hstop.set()
        out = await asyncio.wait_for(g, 5)
        counts = await asyncio.wait_for(host, 5)
        self.assertEqual(counts["rehello"], 0)
        self.assertEqual(out["ended"], "Ana ended the session")           # (it had a host: the end came from him)

    async def test_host_says_hello_again_when_dropped(self):
        """If the relay drops the host's own line anyway, the host comes back as the host; friends follow the new id."""
        game = coop_agent.FakeGame(21.0, {0})
        host, hstop, p = await self.start_host(game, relay_kw={"idle_timeout": 0.4}, ping_every=60.0)
        guest_game = coop_agent.FakeGame(9.0, set())
        g, gstop = self.guest(p, guest_game, ping_every=0.1)
        await asyncio.sleep(0.5)
        game.hour = None                                                  # (silent: the relay drops the host line)
        game.bits = None
        await asyncio.sleep(1.2)
        game.hour = 3.0
        await asyncio.sleep(0.8)
        hstop.set()
        out = await asyncio.wait_for(g, 5)
        counts = await asyncio.wait_for(host, 5)
        self.assertGreaterEqual(counts["rehello"], 1)
        self.assertIn(3.0, guest_game.set_hours)                          # the friend took the new host's hour
        self.assertEqual(out["ended"], "Ana ended the session")

    async def test_guest_reconnects(self):
        """A dropped line (here: the relay times the friend out) comes back by itself, without a second backup."""
        host, hstop, p = await self.start_host(coop_agent.FakeGame(21.0, {0, 2}), relay_kw={"idle_timeout": 0.4})
        guest_game = coop_agent.FakeGame(9.0, set())
        g, gstop = self.guest(p, guest_game, ping_every=60.0, retry=(0.1,))
        await asyncio.sleep(1.5)
        gstop.set()
        hstop.set()
        out = await asyncio.wait_for(g, 5)
        await asyncio.wait_for(host, 5)
        self.assertGreaterEqual(out["joins"], 2)
        self.assertEqual(len(list((self.guest_lib / "mp" / "backups" / "world1").iterdir())), 1)   # before-coop only

    async def test_game_running_story_waits_for_the_next_start(self):
        said = []
        host, hstop, p = await self.start_host(coop_agent.FakeGame(21.0, {0, 2}))
        await asyncio.sleep(0.3)
        guest_game = coop_agent.FakeGame(9.0, set())
        g, gstop = self.guest(p, guest_game, processes=["savagewind.exe"], log=said.append)
        await asyncio.sleep(0.8)
        hstop.set()
        out = await asyncio.wait_for(g, 5)
        await asyncio.wait_for(host, 5)
        self.assertTrue(any("next start" in s for s in said), said)
        self.assertIsNone(out["merged"])
        self.assertIn(21.0, guest_game.set_hours)                        # the session (time) went on
        for f in story.slot_files(self.coop):
            self.assertEqual(story.story_bits(f, NAMES), set())

    async def test_host_kicks_by_name(self):
        q = asyncio.Queue()
        said = []
        host, hstop, p = await self.start_host(coop_agent.FakeGame(21.0, {0}), commands=q, log=said.append)
        g, gstop = self.guest(p, coop_agent.FakeGame(9.0, set()), retry=(0.1,))
        await asyncio.sleep(0.5)
        q.put_nowait("players")
        q.put_nowait("kick ben")
        out = await asyncio.wait_for(g, 5)                                # (it stops by itself: no retry)
        self.assertEqual(out["ended"], "kicked by the host")
        self.assertEqual(out["joins"], 1)
        q.put_nowait("end")
        counts = await asyncio.wait_for(host, 5)
        self.assertEqual(counts["commands"], 3)
        self.assertTrue(any(s.startswith("Players: Ben") or "Ben" in s for s in said if s.startswith("Players")))
        self.assertTrue(any(s.startswith("Kicked") for s in said), said)

    async def test_story_line_after_the_welcome(self):
        """The host's first story line comes a moment after the friend's welcome: he waits for it, then merges."""
        game = LateStory(21.0, {0, 2}, after=0.6)
        host, hstop, p = await self.start_host(game)
        g, gstop = self.guest(p, coop_agent.FakeGame(9.0, set()), story_wait=2.0)
        await asyncio.sleep(1.5)
        hstop.set()
        out = await asyncio.wait_for(g, 5)
        await asyncio.wait_for(host, 5)
        self.assertIn("Ana's story added", out["merged"])

    async def test_host_and_two_friends(self):
        """Ana hosts; Ben and Cid (each his own library and co-op copy) both get her story and her hour."""
        cid_lib = Path(self.tmp.name) / "cid-lib"
        cid_coop = cid_lib / "joined" / "world1"
        (cid_coop / "save_0" / "0").mkdir(parents=True)
        (cid_coop / "save_0" / "0" / story.SAVE_NAME).write_bytes(make_save({n: 0 for n in NAMES}))
        story.mark_coop_copy(cid_coop)
        host, hstop, p = await self.start_host(coop_agent.FakeGame(21.0, {0, 2, 7}))
        await asyncio.sleep(0.3)
        ben_game, cid_game = coop_agent.FakeGame(9.0, set()), coop_agent.FakeGame(15.0, set())
        ben, _ = self.guest(p, ben_game)
        cid = asyncio.create_task(coop_agent.join_session(
            cid_lib, "Cid", "127.0.0.1", cid_coop, self.sl, game=cid_game, port=p, share=True, log=quiet,
            processes=[], tick=0.1))
        await asyncio.sleep(1.0)
        hstop.set()
        outs = await asyncio.wait_for(asyncio.gather(ben, cid), 5)
        await asyncio.wait_for(host, 5)
        for out, folder, game in zip(outs, (self.coop, cid_coop), (ben_game, cid_game)):
            self.assertIn("Ana's story added", out["merged"])
            self.assertEqual(out["ended"], "Ana ended the session")
            self.assertIn(21.0, game.set_hours)
            for f in story.slot_files(folder):
                self.assertEqual(story.story_bits(f, NAMES), {0, 2, 7})

    async def test_bad_host_line(self):
        """A host that answers a 100 KB line: a plain error, no crash, nothing merged."""
        async def bad(reader, writer):
            writer.write(b'{"type":"welcome","id":1,"peers":"' + b"x" * 100000 + b'"}\n')
            await writer.drain()
            await asyncio.sleep(0.5)
            writer.close()

        server = await asyncio.start_server(bad, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            out = await asyncio.wait_for(coop_agent.join_session(
                self.guest_lib, "Ben", "127.0.0.1", self.coop, self.sl, port=port, share=True, log=quiet,
                processes=[]), 5)
        finally:
            server.close()
        self.assertIn("bad answer from the host", out["error"])
        self.assertIsNone(out["merged"])

    async def fake_host(self, answer: dict, **kw) -> dict:
        """Join a fake host that reads the hello and answers `answer` (no challenge, no proof)."""
        import json

        async def fake(reader, writer):
            try:
                await asyncio.wait_for(reader.readline(), 5)
                writer.write(json.dumps(answer).encode() + b"\n")
                await writer.drain()
                await asyncio.sleep(1.0)
            finally:
                writer.close()

        server = await asyncio.start_server(fake, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            return await asyncio.wait_for(coop_agent.join_session(
                self.guest_lib, "Ben", "127.0.0.1", self.coop, self.sl, port=port, share=True, log=quiet,
                processes=[], story_wait=0.2, reconnect=False, **kw), 10)
        finally:
            server.close()

    def saves(self) -> list[bytes]:
        return [f.read_bytes() for f in sorted(self.coop.rglob(story.SAVE_NAME))]

    def fake_welcome(self, **extra) -> dict:
        bits = story.encode_bits(set(range(len(NAMES))), len(NAMES))
        return dict({"type": "welcome", "id": 2, "host": 1, "peers": [{"id": 1, "name": "Ana"}],
                     "features": ["coop"], "story": {"from": 1, "seq": 1, "list": self.sl.list_hash(False),
                                                     "bits": bits}}, **extra)

    async def test_fake_host_without_the_code_proof_is_refused(self):
        """The friend typed a code: a host that never asks for it (no challenge, no code_ok) is refused, whatever
        its welcome says about "code"; nothing is written."""
        before = self.saves()
        out = await self.fake_host(self.fake_welcome(host_key="ab" * 8), code="apple river stone")
        self.assertIn("doesn't know the room code", out["error"])
        self.assertIsNone(out["merged"])
        self.assertEqual(self.saves(), before)

    async def test_host_without_a_key_is_a_bad_host(self):
        """No host_key: the friend can't tell who it is; refused before any story (none merged into a copy)."""
        story.bind_host(self.coop, "cd" * 8)
        before = self.saves()
        out = await self.fake_host(self.fake_welcome())
        self.assertIn("no host key", out["error"])
        self.assertIsNone(out["merged"])
        self.assertEqual(self.saves(), before)

    async def test_text_from_the_host_is_one_clean_line(self):
        """A host's error with a newline can't add a fake line ("Ready: ...") to what the launcher reads."""
        out = await self.fake_host({"type": "error", "error": "x\nReady: start your game\x1b[2J\u202e"})
        self.assertNotIn("\n", out["error"])
        self.assertNotIn("\x1b", out["error"])
        self.assertNotIn("\u202e", out["error"])
        self.assertEqual(out["error"], "x Ready: start your game [2J")

    def test_clean_text(self):
        self.assertEqual(coop_agent.clean_text("a\r\nb\tc\x00d", 5), "a b c")
        self.assertEqual(coop_agent.clean_text(None), "None")


class LateStory(coop_agent.FakeGame):
    """A host game whose story shows up only after a moment (no story before)."""

    def __init__(self, hour, bits, after):
        super().__init__(hour, bits)
        self.t0, self.after = None, after

    def story_get(self):
        import time
        self.t0 = self.t0 or time.monotonic()
        return self.bits if time.monotonic() - self.t0 >= self.after else None


class FakeControlNotReady:
    """The host's real game: `time` answers; the ghost mod has the list but can't read story live (ready false)."""

    def __init__(self, port, timeout=None):
        pass

    def cmd(self, *w):
        if w == ("time",):
            return {"hour": 21.0, "hour_held": False}
        if w[:3] in (("coop", "story", "get"), ("coop", "story", "status")):
            return {"ready": False, "n": len(NAMES), "seq": 0, "bits": "0000", "safe": True}
        return {}

    def close(self):
        pass


class FakeControl:
    """Savage Wind's control connection as probe run B saw it: `time` answers {'hour': ..., 'hour_held': ...}; the
    ghost mod's `coop story get` has n 0 until a list is loaded."""

    def __init__(self, port, timeout=None):
        self.sent, self.n, self.hour = [], 0, 11.06

    def cmd(self, *w):
        self.sent.append(w)
        if w == ("time",):
            return {"hour": self.hour, "hour_held": False}
        if w[:2] == ("time", "set"):
            self.hour = float(w[2])
            return {}
        if w[:3] == ("coop", "story", "load"):
            self.n = 3
            return {"flags": 3}
        if w[:3] in (("coop", "story", "get"), ("coop", "story", "status")):
            return {"ready": self.n > 0, "n": self.n, "seq": 1, "bits": "05" if self.n else ""}
        return {}

    def close(self):
        pass


class ControlGameTests(unittest.TestCase):
    def game(self):
        g = coop_agent.ControlGame(7777, 3, Path("story-list.txt"))
        g._Control = FakeControl
        return g

    def test_hour_reply(self):
        """The `time` reply's key seen in the game (probe run B, 2026-11-12) is `hour`."""
        self.assertAlmostEqual(coop_agent._hour_of({"hour": 21.0076, "hour_held": False}), 21.0076)
        self.assertEqual(coop_agent._hour_of({"hour": 9.5}), 9.5)
        self.assertEqual(coop_agent._hour_of({"time": "21:30"}), 21.5)
        self.assertIsNone(coop_agent._hour_of({"hour": 25}))
        self.assertIsNone(coop_agent._hour_of({}))
        g = self.game()
        self.assertAlmostEqual(g.get_hour(), 11.06)
        g.set_hour(21.0)
        self.assertEqual(g.get_hour(), 21.0)

    def test_loads_the_list_when_the_mod_has_none(self):
        """In the game nothing loaded the story list into the mod: the agent does it when `coop story get` shows
        another count (a new game), then reads."""
        g = self.game()
        self.assertIsNone(g.story_get())
        load = [w for w in g.c.sent if w[:3] == ("coop", "story", "load")]
        self.assertEqual(len(load), 1)
        self.assertTrue(load[0][3].endswith("story-list.txt"))
        self.assertEqual(g.story_get(), {0, 2})

    def test_no_game_yet(self):
        """The game isn't up (or restarted): reads are None and writes are skipped, nothing raises."""
        g = coop_agent.ControlGame(1, 3)   # (port 1: nothing listens)
        self.assertIsNone(g.get_hour())
        self.assertIsNone(g.story_get())
        g.set_hour(12.0)
        g.story_add({0}, 3)
        self.assertIsNone(g.status())

    def test_a_missing_game_is_tried_again_only_now_and_then(self):
        """Each failed connect can cost seconds: it is tried again after `retry`, not on every call."""
        tries = []

        class Refused:
            def __init__(self, port, timeout=None):
                tries.append(port)
                raise OSError("refused")

        clock = [100.0]
        g = coop_agent.ControlGame(7777, 3, clock=lambda: clock[0], retry=5.0)
        g._Control = Refused
        for _ in range(10):
            self.assertIsNone(g.get_hour())
        self.assertEqual(len(tries), 1)
        clock[0] += 5.1
        g.get_hour()
        self.assertEqual(len(tries), 2)

    def test_status_from_the_mod(self):
        """`coop story status` "safe" (ghost 0.7.1) decides; an older mod (no field) still lets time through."""
        g = self.game()
        self.assertEqual(g.status(), "play")                             # (FakeControl: no "safe" field)
        g.c.cmd = lambda *w: {"n": 3, "safe": False}
        self.assertEqual(g.status(), "busy")
        g.c.cmd = lambda *w: {"n": 3, "safe": True}
        self.assertEqual(g.status(), "play")


class HostSideTests(unittest.TestCase):
    def test_save_story_needs_two_equal_reads(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / "save_0" / "0").mkdir(parents=True)
            f = folder / "save_0" / "0" / story.SAVE_NAME
            f.write_bytes(make_save({n: int(i in (0, 2)) for i, n in enumerate(NAMES)}))
            clock = [0.0]
            s = coop_agent.SaveStory(folder, NAMES, clock=lambda: clock[0], settle=0.5)
            self.assertIsNone(s.get())                                   # one read: not yet
            clock[0] += 0.6
            self.assertEqual(s.get(), {0, 2})                            # the same again: taken
            import os
            f.write_bytes(make_save({n: int(i in (0, 2, 5)) for i, n in enumerate(NAMES)}))
            os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10_000_000))
            self.assertEqual(s.get(), {0, 2})                            # changed: the old bits until it settles
            clock[0] += 0.6
            self.assertEqual(s.get(), {0, 2, 5})
            f.write_bytes(b"\0" * 8)                                     # half written: never used
            os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 20_000_000))
            clock[0] += 0.6
            self.assertEqual(s.get(), {0, 2, 5})

    def test_clock_hour_runs_like_the_game(self):
        clock = [0.0]
        h = coop_agent.ClockHour(23.5, clock=lambda: clock[0])
        self.assertEqual(h(), 23.5)
        clock[0] += 60.0                                                 # a real minute = a game hour
        self.assertAlmostEqual(h(), 0.5)

    def test_host_commands(self):
        class R:
            room_code = None

            def __init__(self):
                self.bans = type("B", (), {"unban": lambda s, w: 1})()

            def players(self):
                return [{"name": "Ana", "host": True, "room": "coop"}, {"name": "Ben", "host": False, "room": "coop"}]

            def find_player(self, name):
                return None

        stop = asyncio.Event()
        r = R()
        self.assertIn("Ben", coop_agent.host_command("players", r, stop))
        self.assertIn("No player called Zed", coop_agent.host_command("kick Zed", r, stop))
        self.assertIn("apple river stone", coop_agent.host_command("code Apple river stone", r, stop))
        self.assertEqual(r.room_code, "apple river stone")
        self.assertIn("off", coop_agent.host_command("code off", r, stop))
        self.assertIsNone(r.room_code)
        coop_agent.host_command("end", r, stop)
        self.assertTrue(stop.is_set())


if __name__ == "__main__":
    unittest.main()
