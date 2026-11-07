"""Tests for tools/mp (relay, client logic, bot). Loopback sockets and made-up positions only: no game, no tkinter."""
import asyncio
import contextlib
import io
import json
import math
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import mp  # noqa: E402
from control import Control, ControlError, request_line  # noqa: E402
from mp import bot, client, relay  # noqa: E402


def quiet(*_a, **_k):
    pass


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def angle_gap(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


class Peer:
    """A raw JSON-lines client for poking the relay."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def open(cls, port):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        return cls(r, w)

    async def send(self, msg):
        self.writer.write(msg if isinstance(msg, bytes) else mp.encode(msg))
        await self.writer.drain()

    async def recv(self, timeout=3.0):
        line = await asyncio.wait_for(self.reader.readline(), timeout)
        if not line:
            return None
        return json.loads(line)

    async def recv_type(self, kind, timeout=3.0):
        while True:
            msg = await self.recv(timeout)
            if msg is None or msg["type"] == kind:
                return msg

    async def until_pong(self):
        """Ping and return every message that came before the pong."""
        await self.send({"type": "ping", "t": 1})
        got = []
        while True:
            msg = await self.recv()
            if msg is None:
                raise AssertionError("connection closed while waiting for pong")
            if msg["type"] == "pong":
                return got
            got.append(msg)

    async def is_closed(self, timeout=3.0):
        try:
            while True:
                line = await asyncio.wait_for(self.reader.readline(), timeout)
                if not line:
                    return True
        except (ConnectionError, OSError):
            return True
        except asyncio.TimeoutError:
            return False

    async def hello(self, name, room="default", **extra):
        await self.send(dict({"type": "hello", "name": name, "room": room, "version": mp.PROTOCOL_VERSION}, **extra))
        return await self.recv()

    def close(self):
        try:
            self.writer.close()
        except (OSError, RuntimeError):
            pass


def state(x=1.0, y=2.0, z=3.0, t=10.0, **extra):
    return dict({"type": "state", "t": t, "x": x, "y": y, "z": z, "yaw": 45.0, "stamina": 1.0,
                 "place": "swr:1 x=1.00 y=2.00 z=3.00"}, **extra)


# ---------------------------------------------------------------- protocol helpers

class ProtocolTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(mp.valid_name("  Ana "), "Ana")
        self.assertEqual(mp.valid_name("Zoë_2 o'neil"), "Zoë_2 o'neil")
        for bad in ("", "   ", "a" * 21, "bad\nname", "<b>", "tab\there", None, 5, "-dash"):
            self.assertIsNone(mp.valid_name(bad), bad)
        self.assertEqual(mp.safe_name("Jo<>hn!"), "John")
        self.assertEqual(mp.safe_name("<>"), "player")

    def test_rooms(self):
        self.assertEqual(mp.valid_room("team-1_A"), "team-1_A")
        for bad in ("", "has space", "x" * 33, "ü", None):
            self.assertIsNone(mp.valid_room(bad), bad)

    def test_hostport(self):
        self.assertEqual(mp.parse_hostport("example.org"), ("example.org", 7788))
        self.assertEqual(mp.parse_hostport("10.0.0.5:9000"), ("10.0.0.5", 9000))
        self.assertEqual(mp.parse_hostport("[::1]:9000"), ("::1", 9000))
        self.assertEqual(mp.parse_hostport("::1"), ("::1", 7788))
        for bad in (":80", "host:0", "host:99999", "host:abc"):
            with self.assertRaises(ValueError):
                mp.parse_hostport(bad)

    def test_clean_state(self):
        st = mp.clean_state(state(evil="x", place="p" * 500))
        self.assertEqual(set(st), {"t", "x", "y", "z", "yaw", "stamina", "place"})
        self.assertEqual(len(st["place"]), mp.MAX_PLACE)
        self.assertIsNone(mp.clean_state(state(x=float("nan"))))
        self.assertIsNone(mp.clean_state(state(x=True)))
        self.assertIsNone(mp.clean_state(state(x="1")))
        self.assertIsNone(mp.clean_state(state(z=2e6)))
        self.assertIsNone(mp.clean_state(state(yaw=float("inf"))))
        self.assertIsNone(mp.clean_state({"type": "state", "x": 1, "y": 2, "z": 3}))   # no t
        ok = mp.clean_state({"t": 1, "x": 1, "y": 2, "z": 3})
        self.assertIsNone(ok["yaw"])
        self.assertNotIn("place", ok)

    def test_encode_decode(self):
        self.assertEqual(mp.decode(mp.encode({"type": "ping", "t": 1.5})), {"type": "ping", "t": 1.5})
        self.assertTrue(mp.encode({"type": "x"}).endswith(b"\n"))
        with self.assertRaises(ValueError):
            mp.encode({"type": "state", "x": float("nan")})
        for bad in (b"not json", b"[1,2]", b'{"type": 5}', b"\xff\xfe", b"[" * 4000, b'{"type":"x","a":' + b"[" * 4000):
            self.assertIsNone(mp.decode(bad))

    def test_safe_console(self):
        raw = io.BytesIO()
        out = io.TextIOWrapper(raw, encoding="cp1252", newline="\n")   # what Windows Python writes to a pipe
        mp.safe_console(out)
        out.write("\u0141ukasz joined\n")
        out.flush()
        self.assertEqual(raw.getvalue(), b"\\u0141ukasz joined\n")
        mp.safe_console(object(), None)                     # not a text stream: ignored


# ---------------------------------------------------------------- relay

class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = FakeClock()
        self.peers = []
        self.relay = None

    async def asyncTearDown(self):
        for p in self.peers:
            p.close()
        if self.relay is not None:
            await self.relay.close()

    async def start(self, **kw):
        kw.setdefault("clock", self.clock)
        kw.setdefault("log", quiet)
        self.relay = relay.Relay("127.0.0.1", 0, **kw)
        _, self.port = await self.relay.start()

    async def connect(self, name=None, room="default", **extra):
        p = await Peer.open(self.port)
        self.peers.append(p)
        if name is not None:
            welcome = await p.hello(name, room, **extra)
            self.assertEqual(welcome["type"], "welcome", welcome)
            p.id = welcome["id"]
            p.welcome = welcome
        return p

    async def test_hello_join_state_leave(self):
        await self.start()
        a = await self.connect("Ana")
        self.assertEqual(a.welcome["peers"], [])
        self.assertEqual(a.welcome["version"], mp.PROTOCOL_VERSION)
        b = await self.connect("Bo", bot=True)
        self.assertEqual(b.welcome["peers"], [{"id": a.id, "name": "Ana", "bot": False}])
        self.assertEqual(await a.recv(), {"type": "join", "id": b.id, "name": "Bo", "bot": True})
        await a.send(state(x=5.5, evil=1))
        got = await b.recv()
        self.assertEqual(got["type"], "state")
        self.assertEqual((got["id"], got["name"], got["x"], got["yaw"]), (a.id, "Ana", 5.5, 45.0))
        self.assertNotIn("evil", got)
        self.assertEqual(await a.until_pong(), [])          # no echo of your own state
        b.close()
        self.assertEqual(await a.recv(), {"type": "leave", "id": b.id, "name": "Bo"})
        self.assertEqual(self.relay.player_count("default"), 1)

    async def test_welcome_has_last_state_and_its_age(self):
        await self.start()
        a = await self.connect("Ana")
        await a.send(state(x=7.0))
        await a.until_pong()
        self.clock.now += 2.0
        b = await self.connect("Bo")
        p = b.welcome["peers"][0]
        self.assertEqual(p["state"]["x"], 7.0)
        self.assertAlmostEqual(p["state"]["age"], 2.0)

    async def test_rooms_are_isolated(self):
        await self.start()
        a = await self.connect("Ana", "one")
        b = await self.connect("Bo", "two")
        c = await self.connect("Cy", "one")
        self.assertEqual([p["name"] for p in c.welcome["peers"]], ["Ana"])
        self.assertEqual((await a.recv())["name"], "Cy")
        await a.send(state(x=1.0))
        await a.until_pong()                 # the relay has handled the state
        self.assertEqual(await b.until_pong(), [])
        self.assertEqual([m["type"] for m in await c.until_pong()], ["state"])

    async def test_rate_limit_drops_extra_messages(self):
        await self.start(rate=30)
        a = await self.connect("Ana")
        b = await self.connect("Bo")
        await a.recv_type("join")
        self.clock.now += 1.0                # full bucket: 30 messages
        await a.send(b"".join(mp.encode(state(t=10 + i * 0.01, x=float(i))) for i in range(40)))
        err = await a.recv()
        self.assertEqual(err["type"], "error")
        self.assertIn("too many messages", err["error"])
        self.clock.now += 1.0
        await a.until_pong()
        got = await b.until_pong()
        self.assertEqual(len(got), 30)
        self.assertEqual([m["x"] for m in got], [float(i) for i in range(30)])

    async def test_flooding_gets_disconnected(self):
        await self.start(rate=10)
        a = await self.connect("Ana")
        b = await self.connect("Bo")
        await a.recv_type("join")
        self.clock.now += 1.0
        await a.send(mp.encode({"type": "ping", "t": 1}) * 100)   # 10 pass, 50 dropped is the limit
        msgs = []
        while (m := await a.recv()) is not None:
            msgs.append(m)
        self.assertEqual(sum(m["type"] == "pong" for m in msgs), 10)
        self.assertIn("disconnected", msgs[-1]["error"])
        self.assertEqual((await b.recv())["type"], "leave")

    async def test_line_size_limit(self):
        await self.start()
        a = await self.connect("Ana")
        await a.send(b"x" * 5000 + b"\n")
        err = await a.recv()
        self.assertIn("line too long", err["error"])
        self.assertEqual(await a.until_pong(), [])            # still connected
        exact = b'{"type":"ping","t":2' + b" " * (mp.MAX_LINE - 21) + b"}"
        self.assertEqual(len(exact), mp.MAX_LINE)
        await a.send(exact + b"\n")
        self.assertEqual(await a.recv(), {"type": "pong", "t": 2})
        await a.send(b"y" * 5000)                             # too long before its newline arrives
        await asyncio.sleep(0.05)
        await a.send(b"yyy\n")
        got = await a.until_pong()
        self.assertEqual([m["type"] for m in got], ["error"])

    async def test_hello_is_checked(self):
        await self.start()
        cases = [
            ({"type": "hello", "name": "", "version": 1}, "bad name"),
            ({"type": "hello", "name": "a" * 21, "version": 1}, "bad name"),
            ({"type": "hello", "name": "x\ny", "version": 1}, "bad name"),
            ({"type": "hello", "name": 5, "version": 1}, "bad name"),
            ({"type": "hello", "name": "Ana", "room": "a room", "version": 1}, "bad room"),
            ({"type": "hello", "name": "Ana", "version": 99}, "version"),
            ({"type": "hello", "name": "Ana"}, "version"),
            (state(), "hello first"),
            (b"nonsense\n", "JSON hello"),
        ]
        for msg, expect in cases:
            p = await self.connect()
            await p.send(msg)
            err = await p.recv()
            self.assertEqual(err["type"], "error", msg)
            self.assertIn(expect, err["error"])
            self.assertTrue(await p.is_closed(), msg)
        self.assertEqual(self.relay.player_count(), 0)

    async def test_room_cap(self):
        await self.start()
        self.assertEqual(self.relay.room_cap, 8)
        for i in range(8):
            await self.connect(f"p{i}")
        p = await self.connect()
        err = await p.hello("late")
        self.assertIn("full", err["error"])
        self.assertTrue(await p.is_closed())
        await self.connect("other", room="spare")          # another room still has space
        self.assertEqual(self.relay.player_count("default"), 8)

    async def test_bad_state_is_not_forwarded(self):
        await self.start()
        a = await self.connect("Ana")
        b = await self.connect("Bo")
        await a.recv_type("join")
        for raw in (b'{"type":"state","t":1,"x":NaN,"y":0,"z":0}\n', mp.encode(state(x="1")),
                    mp.encode({"type": "state", "t": 1, "x": 1, "y": 2}), mp.encode(state(x=True))):
            await a.send(raw)
            self.assertIn("bad state", (await a.recv())["error"])
        await a.send({"type": "teleport", "x": 1})
        self.assertIn("unknown message", (await a.recv())["error"])
        self.assertEqual(await b.until_pong(), [])

    async def test_ping_echoes_time(self):
        await self.start()
        a = await self.connect("Ana")
        await a.send({"type": "ping", "t": 12.25})
        self.assertEqual(await a.recv(), {"type": "pong", "t": 12.25})
        await a.send({"type": "ping", "t": "x"})
        self.assertEqual(await a.recv(), {"type": "pong", "t": None})
        for raw in (b'{"type":"ping","t":NaN}\n', b'{"type":"ping","t":1e400}\n', b'{"type":"ping","t":-Infinity}\n'):
            await a.send(raw)                                  # can't be echoed (JSON has no NaN): None, still connected
            self.assertEqual(await a.recv(), {"type": "pong", "t": None})

    async def test_deeply_nested_json(self):
        errors = []
        asyncio.get_running_loop().set_exception_handler(lambda _loop, ctx: errors.append(ctx))
        await self.start()
        a = await self.connect("Ana")
        await a.send(b"[" * 4000 + b"\n")
        self.assertIn("not a JSON message", (await a.recv())["error"])
        self.assertEqual(await a.until_pong(), [])            # still connected
        p = await self.connect()
        await p.send(b"[" * 4000 + b"\n")                    # before hello
        self.assertIn("JSON hello", (await p.recv())["error"])
        self.assertTrue(await p.is_closed())
        self.assertEqual(errors, [])

    async def test_log_that_fails_does_not_drop_the_player(self):
        def log(text):                                         # a console that can't show the name
            text.encode("cp1252")
        await self.start(log=log)
        a = await self.connect("Ana")
        b = await self.connect("\u0141ukasz")
        self.assertEqual((await a.recv())["type"], "join")
        self.assertEqual(await a.until_pong(), [])            # no leave
        self.assertEqual(await b.until_pong(), [])
        self.assertEqual(self.relay.player_count(), 2)

    async def test_unexpected_error_drops_only_that_client(self):
        errors, lines = [], []
        asyncio.get_running_loop().set_exception_handler(lambda _loop, ctx: errors.append(ctx))
        await self.start(log=lines.append)
        a = await self.connect("Ana")
        b = await self.connect("Bo")
        await a.recv_type("join")
        handle = self.relay._message

        def broken(conn, msg):
            if msg["type"] == "boom":
                raise RuntimeError("a bug")
            handle(conn, msg)
        self.relay._message = broken
        await b.send({"type": "boom"})
        self.assertTrue(await b.is_closed())
        self.assertEqual(await a.recv(), {"type": "leave", "id": b.id, "name": "Bo"})
        self.assertEqual(await a.until_pong(), [])
        self.assertTrue(any("Dropped Bo after an error: RuntimeError: a bug" in ln for ln in lines), lines)
        self.assertEqual(errors, [])

    async def test_hello_timeout(self):
        await self.start(hello_timeout=0.2)
        p = await self.connect()
        err = await p.recv()
        self.assertIn("no hello", err["error"])
        self.assertTrue(await p.is_closed())

    async def test_hello_timeout_is_a_deadline(self):
        """A byte now and then (no newline, no hello) must not keep a connection open past the hello time."""
        await self.start(hello_timeout=0.3, max_clients=1)
        p = await self.connect()
        started = time.monotonic()
        got = None
        while got is None and time.monotonic() - started < 3.0:
            p.writer.write(b" ")
            try:
                got = await p.recv(timeout=0.1)
            except asyncio.TimeoutError:
                pass
        self.assertIn("no hello", got["error"])
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(await p.is_closed())
        await asyncio.sleep(0.05)
        late = await self.connect("Ana")                        # the slot is free again
        self.assertEqual(late.welcome["type"], "welcome")

    async def test_shutdown_says_bye(self):
        await self.start()
        a = await self.connect("Ana")
        b = await self.connect("Bo")
        await a.recv_type("join")
        await self.relay.close()
        for p in (a, b):
            self.assertEqual((await p.recv_type("bye"))["reason"], "relay stopped")
            self.assertTrue(await p.is_closed())
        with self.assertRaises(OSError):
            await asyncio.wait_for(asyncio.open_connection("127.0.0.1", self.port), 3.0)

    async def test_cancel_closes_clients_at_once(self):
        """Ctrl+C cancels serve_forever; clients must hear bye right away, not when they hang up."""
        await self.start()
        task = asyncio.create_task(self.relay.serve_forever())
        a = await self.connect("Ana")
        await asyncio.sleep(0.05)
        started = time.monotonic()
        task.cancel()
        self.assertEqual((await a.recv_type("bye", timeout=2.0))["reason"], "relay stopped")
        self.assertTrue(await a.is_closed(2.0))
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3.0)
        self.assertLess(time.monotonic() - started, 2.0)


# ---------------------------------------------------------------- jitter buffer and geometry

def sample(t, x, yaw=0.0):
    return {"t": t, "x": x, "y": 0.0, "z": -x, "yaw": yaw, "stamina": None}


class JitterTests(unittest.TestCase):
    def test_lerp_angle_takes_the_short_way(self):
        self.assertAlmostEqual(client.lerp_angle(10, 30, 0.5), 20)
        self.assertLess(angle_gap(client.lerp_angle(170, -170, 0.5), 180), 1e-9)
        self.assertLess(angle_gap(client.lerp_angle(-170, 170, 0.25), -175), 1e-9)
        self.assertLess(angle_gap(client.lerp_angle(350, 10, 0.5), 0), 1e-9)

    def test_interpolates_100ms_behind(self):
        jb = client.JitterBuffer(delay=0.1)
        latency = 0.02
        for i in range(5):                   # x = 0, 0.5, 1.0, 1.5, 2.0 at t = 0 .. 0.2
            t = i * 0.05
            jitter = 0.04 if i == 2 else 0.0  # one late packet must not shift the timing
            jb.push(sample(t, 10 * t, yaw=170 if i < 4 else -170), 100.0 + t + latency + jitter)
        self.assertAlmostEqual(jb.offset(), 100.0 + latency)
        s = jb.sample(100.0 + latency + 0.175 + 0.1)          # shows the sender's t = 0.175
        self.assertAlmostEqual(s["x"], 1.75)
        self.assertAlmostEqual(s["z"], -1.75)
        self.assertLess(angle_gap(s["yaw"], 180), 1e-9)       # half way from 170 to -170
        self.assertAlmostEqual(jb.sample(100.0 + latency + 0.1 + 0.1)["x"], 1.0)   # exactly on a sample

    def test_holds_the_newest_and_oldest(self):
        jb = client.JitterBuffer(delay=0.1)
        jb.push(sample(1.0, 5.0), 51.0)
        jb.push(sample(1.05, 6.0), 51.05)
        self.assertEqual(jb.sample(60.0)["x"], 6.0)           # long after: hold, don't guess ahead
        self.assertEqual(jb.sample(50.0)["x"], 5.0)
        self.assertIsNone(client.JitterBuffer().sample(1.0))

    def test_teleport_is_not_blended(self):
        jb = client.JitterBuffer(delay=0.0)
        here = {"t": 1.0, "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "stamina": None}
        jb.push(here, 101.0)                                # (offset 100: shows the sender's t = now - 100)
        for i, (dx, dy, dz) in enumerate([(0, 0, 40), (0, 0, 1), (1.5, 0, 0), (3, 0, 0), (0, 35, 0), (0, 0, 0)]):
            here = dict(here, t=here["t"] + 0.05, x=here["x"] + dx, y=here["y"] + dy, z=here["z"] + dz)
            jb.push(here, 101.0 + 0.05 * (i + 1))
        jb.push(dict(here, t=here["t"] + 1.0, x=here["x"] + 35), 102.35)   # 35 m in 1 s: far, though not fast
        jb.push(dict(here, t=here["t"] + 2.0, x=here["x"] + 60), 103.35)   # 25 m in 1 s: running
        self.assertEqual(jb.sample(101.001)["z"], 0.0)      # 40 m: the older one is held until the newer one's time
        self.assertEqual(jb.sample(101.049)["z"], 0.0)
        self.assertAlmostEqual(jb.sample(101.075)["z"], 40.5)   # then 20 m/s: blended
        self.assertAlmostEqual(jb.sample(101.125)["x"], 0.75)   # 30 m/s: blended
        self.assertEqual(jb.sample(101.175)["x"], 1.5)      # 3 m in 0.05 s, 60 m/s: a short teleport
        self.assertEqual(jb.sample(101.225)["y"], 0.0)      # 35 m up: a teleport too
        self.assertEqual(jb.sample(101.8)["x"], 4.5)        # 35 m in 1 s
        self.assertAlmostEqual(jb.sample(102.8)["x"], 52.0)     # 25 m in 1 s: blended

    def test_repeat_dropped_and_restart_resets(self):
        jb = client.JitterBuffer()
        self.assertTrue(jb.push(sample(5.0, 1.0), 10.0))
        self.assertFalse(jb.push(sample(5.0, 2.0), 10.01))
        jb.push(sample(5.05, 2.0), 10.05)
        jb.push(sample(0.5, 9.0), 10.1)                      # sender restarted: its clock went back
        self.assertEqual(len(jb.samples), 1)
        self.assertAlmostEqual(jb.offset(), 9.6)
        self.assertEqual(jb.latest()["x"], 9.0)

    def test_buffer_is_bounded(self):
        jb = client.JitterBuffer(size=8)
        for i in range(100):
            jb.push(sample(i * 0.05, i), i * 0.05 + 1)
        self.assertEqual(len(jb.samples), 8)

    def test_radar_and_compass(self):
        def close(a, b):
            return all(abs(p - q) < 1e-9 for p, q in zip(a, b))
        self.assertTrue(close(client.radar_xy(10, 0), (10, 0)))     # east: right
        self.assertTrue(close(client.radar_xy(0, -10), (0, -10)))   # north: up
        self.assertTrue(close(client.radar_xy(0, -10, 180), (0, -10)))
        self.assertTrue(close(client.radar_xy(10, 0, 90), (0, -10)))   # looking east: east is up
        self.assertTrue(close(client.radar_xy(0, 10, 90), (10, 0)))    # ... and south is right
        self.assertTrue(close(client.radar_xy(0, 10, 0), (0, -10)))    # looking south (+z): south is up
        self.assertEqual([client.compass(*d) for d in ((0, -5), (5, 0), (0, 5), (-5, 0), (-5, -5), (3, 4))],
                         ["N", "E", "S", "W", "NW", "SE"])
        self.assertEqual([client.nice_range(d) for d in (None, 0, 22, 23, 400, 1e9)], [25, 25, 25, 50, 500, 10000])

    def test_text(self):
        self.assertEqual(client.height_text(0.4), "same height")
        self.assertEqual(client.height_text(5.2), "5 m higher")
        self.assertEqual(client.height_text(-12), "12 m lower")
        v = {"name": "Ana", "bot": False, "dist": 120.4, "dx": 100, "dz": -60, "dy": 5, "stale": False, "age": 0.1}
        self.assertEqual(client.describe(v), "Ana: 120 m NE, 5 m higher")
        self.assertEqual(client.describe(dict(v, dist=None, bot=True)), "Ana (bot): no position yet")
        self.assertIn("no news for 9 s", client.describe(dict(v, stale=True, age=9)))


# ---------------------------------------------------------------- client logic with a fake game

POS = {"x": 100.0, "y": 50.0, "z": -200.0, "stamina": 1.0, "player_info": "0x7240001000",
       "place": "swr:1 x=100.00 y=50.00 z=-200.00 yaw=90.0 pitch=-10.0"}
CAM = {"x": 100.0, "y": 52.0, "z": -205.0, "dx": 1.0, "dy": 0.0, "dz": 0.0, "yaw": 90.0, "pitch": -10.0}


class FakeControl:
    """Answers like the game's control socket (runtime/control_game.c, control_tp.c at 0.4.1-alpha.4).

    fail: the connection is gone. Like tools/control.py's Control.batch(errors=True), that comes back in every reply
    slot rather than raised. raise_error: batch raises it (Control lets a socket OSError through).
    """

    def __init__(self, ghost_mod=False):
        self.pos, self.cam = dict(POS), dict(CAM)
        self.calls = []
        self.tp_replies = []
        self.fail = False
        self.raise_error = None
        self.closed = False
        self.ghost_mod = ghost_mod          # the ghost mod is loaded: `ghost` answers (else Unknown command)
        self.ghost_state = {"state": "live", "last": "Dm_Kibako_Contain_01 spawned after 0.02 s"}

    def batch(self, requests, *, errors=False):
        assert errors
        self.calls.append([list(r) for r in requests])
        if self.raise_error is not None:
            raise self.raise_error
        if self.fail:
            return [ControlError("the game closed the connection") for _ in requests]
        out = []
        for r in requests:
            if r[0] == "pos":
                out.append(dict(self.pos) if self.pos else ControlError("No world loaded"))
            elif r[0] == "camera":
                out.append(dict(self.cam) if self.cam else ControlError("No world loaded"))
            elif r[0] == "teleport":
                reply = self.tp_replies.pop(0) if self.tp_replies else {"state": "idle"}
                out.append(reply)
            elif r[0] == "ghost" and self.ghost_mod:     # (the mod's replies: native/examples/ghost/ghost.c)
                out.append(dict(self.ghost_state) if len(r) == 1 else {r[1]: "requested"})
            else:
                out.append(ControlError("Unknown command"))
        return out

    def close(self):
        self.closed = True


class StandInGame:
    """A stand-in for the game's control socket on loopback, for the real Control class. Made-up positions only.

    Like runtime/control.c at 0.4.1-alpha.4 it answers every request line in order, even after a delay.
    stall: the next answer comes this many seconds late (a long frame). close_at_once: accept connections and close
    them at once, as the game does when its 4 control connections are taken.
    """

    def __init__(self):
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        self.stall = 0.0
        self.close_at_once = False
        self.lock = threading.Lock()
        self.conns = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                s, _ = self.srv.accept()
            except OSError:
                return
            if self.close_at_once:
                s.close()
                continue
            self.conns.append(s)
            threading.Thread(target=self._serve, args=(s,), daemon=True).start()

    def _serve(self, s):
        try:
            with s.makefile("rb") as f:
                for line in f:
                    with self.lock:
                        stall, self.stall = self.stall, 0.0
                    if stall:
                        time.sleep(stall)
                    word = line.split()[0]
                    data = POS if word == b"pos" else CAM if word == b"camera" else {"state": "idle"}
                    s.sendall((json.dumps({"ok": True, "data": data}) + "\n").encode())
        except (OSError, ValueError):
            pass

    def connect(self, timeout=1.0):
        return Control(self.port, timeout=timeout)

    def close(self):
        self.srv.close()
        for s in self.conns:
            try:
                s.close()
            except OSError:
                pass


class SessionTests(unittest.TestCase):
    def test_messages_update_peers(self):
        s = client.Session("Me")
        s.handle({"type": "welcome", "id": 2, "room": "r", "peers": [
            {"id": 1, "name": "Ana", "bot": False, "state": dict(state(x=10.0, t=5.0), age=1.5)},
            {"id": 3, "name": "bot", "bot": True}, {"id": "x", "name": "bad"}, "junk"]}, now=50.0)
        self.assertEqual((s.my_id, s.room, sorted(s.peers)), (2, "r", [1, 3]))
        self.assertAlmostEqual(s.peers[1].buffer.last_recv, 48.5)
        s.handle({"type": "join", "id": 4, "name": "Cy", "bot": False}, now=51.0)
        s.handle(dict(state(x=20.0, t=1.0), id=4, name="Cy"), now=51.0)
        s.handle(dict(state(x=99.0, t=1.0), id=2, name="Me"), now=51.0)        # ourselves: ignored
        s.handle(dict(state(x=float("nan")), id=5, name="Nan"), now=51.0)      # bad: ignored
        self.assertEqual(sorted(s.peers), [1, 3, 4])
        self.assertEqual(s.peers[4].buffer.latest()["x"], 20.0)
        s.handle({"type": "leave", "id": 3, "name": "bot"}, now=52.0)
        self.assertEqual(sorted(s.peers), [1, 4])
        s.handle({"type": "disconnected"}, now=53.0)
        self.assertEqual((s.my_id, s.peers), (None, {}))

    def test_local_state_from_game_replies(self):
        s = client.Session("Me")
        msg = s.update_local(POS, CAM, now=12.34567)
        self.assertEqual(msg, {"type": "state", "t": 12.346, "x": 100.0, "y": 50.0, "z": -200.0, "yaw": 90.0,
                               "stamina": 1.0, "place": POS["place"], "speed": 0.0})   # (speed: test_mp_wifi.py)
        self.assertIsNone(s.update_local(POS, None, 13.0)["yaw"])               # no camera: still a state
        self.assertIsNone(s.update_local(None, CAM, 14.0))                      # no world
        self.assertIsNone(s.local)
        self.assertIsNone(s.update_local({"x": 1}, CAM, 15.0))

    def test_views_and_goto(self):
        s = client.Session("Me", delay=0.0)
        s.handle({"type": "welcome", "id": 9, "peers": []}, now=0.0)
        s.handle(dict(state(x=130.0, y=55.0, z=-240.0, t=1.0), id=1, name="Far"), now=10.0)
        s.handle(dict(state(x=103.0, y=50.0, z=-196.0, t=1.0), id=2, name="Near"), now=10.0)
        s.handle({"type": "join", "id": 3, "name": "New"}, now=10.0)
        self.assertIsNone(s.summary(10.0))                                      # local unknown
        s.update_local(POS, CAM, now=10.0)
        views = s.views(10.0)
        self.assertEqual([v["name"] for v in views], ["Near", "Far", "New"])
        self.assertAlmostEqual(views[0]["dist"], 5.0)
        self.assertAlmostEqual(views[1]["dist"], 50.0)
        self.assertAlmostEqual(views[1]["dy"], 5.0)
        self.assertFalse(views[0]["stale"])
        self.assertTrue(s.views(20.0)[0]["stale"])
        self.assertEqual(s.summary(10.0), "Near: 5 m SE, same height | Far: 50 m NE, 5 m higher | New: no position yet")
        self.assertEqual(s.goto_target(1, 10.0), (130.0, 55.0 + client.GOTO_LIFT, -240.0, "Far"))
        self.assertIsNone(s.goto_target(3, 10.0))
        self.assertIsNone(s.goto_target(42, 10.0))
        s.handle({"type": "leave", "id": 1}, now=11.0)
        s.handle({"type": "leave", "id": 2}, now=11.0)
        s.handle({"type": "leave", "id": 3}, now=11.0)
        self.assertEqual(s.summary(11.0), "Nobody else in room default yet.")


class GameLinkTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(0.0)
        self.log = []
        self.fake = FakeControl()
        self.attempts = 0
        self.running = True

        def connect():
            self.attempts += 1
            if not self.running:
                raise ControlError("can't connect to the game on 127.0.0.1:7777")
            return self.fake
        self.game = client.GameLink(7777, connect=connect, clock=self.clock, log=self.log.append)

    def test_waits_quietly_and_retries_every_2s(self):
        self.running = False
        self.assertEqual(self.game.poll(), (None, None))
        self.clock.now = 1.0
        self.game.poll()
        self.assertEqual(self.attempts, 1)
        self.clock.now = 2.1
        self.game.poll()
        self.clock.now = 4.2
        self.game.poll()
        self.assertEqual(self.attempts, 3)
        self.assertEqual(len(self.log), 1)                 # said once
        self.assertIn("SAVAGEWIND_CONTROL=7777", self.log[0])
        self.running = True
        self.clock.now = 6.3
        pos, cam = self.game.poll()
        self.assertEqual((pos["x"], cam["yaw"], self.game.status), (100.0, 90.0, "ok"))
        self.assertEqual(self.fake.calls[-1], [["pos"], ["camera"]])

    def test_no_world(self):
        self.fake.pos = self.fake.cam = None
        self.assertEqual(self.game.poll(), (None, None))
        self.assertEqual(self.game.status, "no world")

    def test_lost_game_reconnects_later(self):
        self.game.poll()
        self.fake.fail = True
        self.clock.now = 10.0
        self.assertEqual(self.game.poll(), (None, None))
        self.assertTrue(self.fake.closed)
        self.assertEqual(self.game.status, "waiting")
        self.fake.fail = False
        self.clock.now = 11.0
        self.game.poll()
        self.assertEqual(self.attempts, 1)
        self.clock.now = 12.0
        self.assertIsNotNone(self.game.poll()[0])
        self.assertEqual(self.attempts, 2)

    def test_teleport_flow(self):
        self.game.poll()
        self.fake.tp_replies = [{"state": "verifying", "x": 100.0, "y": 50.0, "z": -200.0},
                                {"state": "verifying"}, {"state": "done", "message": "Arrived"}]
        self.game.request_teleport(-1.5, 2.25, 3, "Ana")
        self.game.poll()
        words = self.fake.calls[-1][2]
        self.assertEqual(words, ["teleport", "-1.500", "2.250", "3.000"])
        self.assertEqual(request_line(words), b"teleport -1.500 2.250 3.000\n")
        self.assertTrue(self.game.tp_active)
        self.assertEqual(self.game.tp_message, "Teleporting to Ana...")
        self.game.request_teleport(9, 9, 9, "Bo")          # while one runs: refused, and the window says so
        self.game.poll()
        self.assertEqual(self.fake.calls[-1][2], ["teleport", "status"])
        self.assertEqual(self.game.tp_message, "Still going to Ana; press Go to Bo again after.")
        self.game.poll()
        self.assertEqual(self.fake.calls[-1][2], ["teleport", "status"])
        self.assertEqual(self.game.tp_message, "Arrived next to Ana.")
        self.assertFalse(self.game.tp_active)
        self.game.poll()
        self.assertEqual(self.fake.calls[-1], [["pos"], ["camera"]])

    def test_refused_teleport_when_the_running_one_ends(self):
        self.game.poll()
        self.fake.tp_replies = [{"state": "probing"}, {"state": "done"}]
        self.game.request_teleport(1, 2, 3, "Ana")
        self.game.poll()
        self.game.request_teleport(9, 9, 9, "Bo")
        self.game.poll()                                    # Ana's teleport ends in the same frame
        self.assertEqual(self.game.tp_message, "Arrived next to Ana. Press Go to Bo again.")
        self.assertFalse(self.game.tp_active)

    def test_lost_game_during_teleport(self):
        self.game.poll()
        self.fake.tp_replies = [{"state": "scanning"}]
        self.game.request_teleport(1, 2, 3, "Ana")
        self.game.poll()
        self.assertTrue(self.game.tp_active)
        self.fake.fail = True
        self.clock.now = 1.0
        self.game.poll()
        self.assertFalse(self.game.tp_active)
        self.assertEqual(self.game.tp_message, "Lost the game during the teleport.")
        self.fake.fail = False
        self.clock.now = 5.0
        self.game.poll()
        self.assertEqual(self.fake.calls[-1], [["pos"], ["camera"]])       # no status request any more
        self.assertEqual(self.game.tp_message, "Lost the game during the teleport.")

    def test_failures_in_reply_slots_drop_the_connection(self):
        """A timeout or EOF comes back inside the batch (errors=True); it must not count as 'no world'."""
        for text, status in (("the game didn't answer in time", "not answering"),
                             ("the game closed the connection", "waiting"),
                             ("invalid reply from the game: Expecting value", "waiting")):
            self.setUp()
            self.game.poll()
            self.fake.batch = lambda requests, errors=False, text=text: [dict(POS), ControlError(text)]
            self.clock.now = 1.0
            self.assertEqual(self.game.poll(), (None, None), text)
            self.assertEqual(self.game.status, status, text)
            self.assertTrue(self.fake.closed, text)
            self.assertIsNone(self.game.ctl, text)
        self.setUp()
        self.game.poll()
        self.fake.raise_error = ConnectionResetError(10054, "reset")
        self.clock.now = 1.0
        self.assertEqual(self.game.poll(), (None, None))
        self.assertEqual(self.game.status, "waiting")
        self.assertEqual(self.log, ["Connected to the game.", "Lost the game; trying again every 2 s."])

    def test_connected_said_after_the_first_answer(self):
        self.fake.fail = True
        self.game.poll()
        self.assertNotIn("Connected to the game.", self.log)
        self.fake.fail = False
        self.clock.now = 2.5
        self.game.poll()
        self.assertEqual(self.log[-1], "Connected to the game.")
        self.assertEqual(self.log.count("Connected to the game."), 1)

    def test_teleport_errors(self):
        self.game.poll()
        self.fake.tp_replies = [ControlError("Distance below 0.05")]
        self.game.request_teleport(100, 50, -200, "Ana")
        self.game.poll()
        self.assertEqual(self.game.tp_message, "You're already there.")
        self.fake.tp_replies = [{"state": "scanning"}, {"state": "failed", "message": "Position not found"}]
        self.game.request_teleport(0, 0, 0, "Bo")
        self.game.poll()
        self.game.poll()
        self.assertEqual(self.game.tp_message, "Teleport failed: Position not found")
        self.fake.tp_replies = [{"state": "scanning"}, {"state": "scanning"}]
        self.game.request_teleport(0, 0, 0, "Cy")
        self.game.poll()
        self.clock.now = 100.0
        self.game.poll()
        self.assertFalse(self.game.tp_active)
        self.assertIn("too long", self.game.tp_message)


class GameLinkExtraTests(unittest.TestCase):
    """poll(extra): --ghost's requests ride in the same batch."""

    def setUp(self):
        self.clock = FakeClock(0.0)
        self.fake = FakeControl(ghost_mod=True)
        self.up = True

        def connect():
            if not self.up:
                raise ControlError("can't connect to the game on 127.0.0.1:7777")
            return self.fake
        self.game = client.GameLink(7777, connect=connect, clock=self.clock, log=quiet)

    def test_extra_requests_after_pos_camera_and_teleport(self):
        pos, cam = self.game.poll([["ghost", "pos", "1.000", "2.000", "3.000", "0.0"]])
        self.assertEqual((pos["x"], cam["yaw"]), (100.0, 90.0))
        self.assertEqual(self.fake.calls[-1], [["pos"], ["camera"], ["ghost", "pos", "1.000", "2.000", "3.000", "0.0"]])
        self.assertEqual(self.game.extra_replies, [{"pos": "requested"}])
        self.assertEqual(self.game.connections, 1)
        self.fake.tp_replies = [{"state": "verifying"}]
        self.game.request_teleport(1, 2, 3, "Ana")
        self.game.poll([["ghost"]])
        self.assertEqual([r[0] for r in self.fake.calls[-1]], ["pos", "camera", "teleport", "ghost"])
        self.assertTrue(self.game.tp_active)                   # (the teleport reply is still read from slot 2)
        self.assertEqual(self.game.extra_replies, [self.fake.ghost_state])
        self.game.poll()
        self.assertEqual(self.game.extra_replies, [])

    def test_extra_not_sent_without_a_game(self):
        self.up = False
        self.assertEqual(self.game.poll([["ghost", "despawn"]]), (None, None))
        self.assertIsNone(self.game.extra_replies)
        self.assertEqual((self.fake.calls, self.game.connections), ([], 0))
        self.assertFalse(self.game.send_last([["ghost", "despawn"]]))
        self.up = True
        self.clock.now = 5.0
        self.game.poll()
        self.assertTrue(self.game.send_last([["ghost", "despawn"]]))
        self.assertEqual(self.fake.calls[-1], [["ghost", "despawn"]])
        self.fake.fail = True
        self.assertFalse(self.game.send_last([["ghost", "despawn"]]))
        self.fake.raise_error = OSError("reset")
        self.assertFalse(self.game.send_last([["ghost", "despawn"]]))


def ghost_session(*peers, delay=0.0, now=10.0):
    """A session with the local player at POS and peers (name, x, y, z[, yaw]) seen at `now`."""
    s = client.Session("Me", delay=delay)
    s.handle({"type": "welcome", "id": 9, "peers": []}, now=0.0)
    for i, (name, x, y, z, *yaw) in enumerate(peers, start=1):
        s.handle(dict(state(x=x, y=y, z=z, t=1.0, yaw=yaw[0] if yaw else None), id=i, name=name), now=now)
    s.update_local(POS, CAM, now=now)
    return s


class GhostTests(unittest.TestCase):
    """--ghost: the requests for the ghost mod's `ghost` command, from made-up peers."""

    def setUp(self):
        self.log = []
        self.ghost = client.Ghost(log=self.log.append)

    def test_nobody_to_show(self):
        self.assertEqual(self.ghost.requests(ghost_session(), 10.0, 1), [])
        self.assertEqual(self.ghost.status_text(), "waiting for another player")
        self.assertEqual(self.ghost.last(), [])
        self.ghost.replies([])
        self.assertEqual(self.log, [])

    def test_spawn_once_then_follow_the_nearest(self):
        s = ghost_session(("Far", 160.0, 50.0, -200.0, 45.0), ("Bo", 103.0, 51.0, -196.0, -30.0))
        reqs = self.ghost.requests(s, 10.0, 1)
        self.assertEqual(reqs, [["ghost", "spawn"], ["ghost", "pos", "103.000", "51.000", "-196.000", "-30.0"],
                                ["ghost"]])
        self.assertEqual(request_line(reqs[1]), b"ghost pos 103.000 51.000 -196.000 -30.0\n")
        self.ghost.replies([{"spawn": "requested"}, {"pos": "requested"}, {"state": "pending", "last": "requested x"}])
        self.assertEqual(self.log, ["Ghost: showing Bo in your game.", "Ghost: requested x"])
        self.assertEqual(self.ghost.status_text(), "showing Bo (pending)")
        s.handle(dict(state(x=104.0, y=51.0, z=-196.0, t=2.0, yaw=None), id=2, name="Bo"), now=11.0)
        reqs = self.ghost.requests(s, 11.0, 1)                       # moved: pos only (status every 2 s)
        self.assertEqual(reqs, [["ghost", "pos", "104.000", "51.000", "-196.000", "90.0"]])  # (faces +x: its way)
        self.ghost.replies([{"pos": "requested"}])
        self.assertEqual(self.ghost.requests(s, 12.5, 1)[-1], ["ghost"])
        self.ghost.replies([{"pos": "requested"}, {"state": "live", "last": "requested x"}])
        self.assertEqual(self.ghost.status_text(), "showing Bo (live)")
        self.assertEqual(len(self.log), 2)                           # (the same news is said once)
        self.assertEqual(self.ghost.last(), [["ghost", "despawn"]])

    def test_chosen_peer_and_actor(self):
        g = client.Ghost(peer="far", actor="FldObj_Bokoblin_Bone", log=self.log.append)
        s = ghost_session(("Far", 160.0, 50.0, -200.0), ("Bo", 103.0, 51.0, -196.0))
        reqs = g.requests(s, 10.0, 1)
        self.assertEqual(reqs[:2], [["ghost", "spawn", "FldObj_Bokoblin_Bone"],
                                    ["ghost", "pos", "160.000", "50.000", "-200.000", "0.0"]])
        self.assertEqual(g.status_text(), "showing Far")
        g2 = client.Ghost(peer="Cy", log=quiet)
        self.assertEqual(g2.requests(s, 10.0, 1), [])                # not in the room: nothing
        self.assertEqual(g2.status_text(), "waiting for Cy")

    def test_peer_leaves_then_another_comes(self):
        s = ghost_session(("Bo", 103.0, 51.0, -196.0))
        self.ghost.requests(s, 10.0, 1)
        self.ghost.replies([{}, {}, {}])
        s.handle({"type": "leave", "id": 1}, now=10.5)
        self.assertEqual(self.ghost.requests(s, 10.5, 1), [["ghost", "despawn"]])
        self.ghost.replies([{"despawn": "requested"}])
        self.assertEqual(self.ghost.requests(s, 10.6, 1), [])        # (said once)
        self.assertEqual(self.ghost.last(), [])
        self.assertEqual(self.log[-1], "Ghost: Bo is gone; ghost removed.")
        s.handle(dict(state(x=1.0, y=2.0, z=3.0, t=1.0), id=5, name="Cy"), now=11.0)
        self.assertEqual(self.ghost.requests(s, 11.0, 1)[0], ["ghost", "spawn"])
        self.assertEqual(self.log[-1], "Ghost: showing Cy in your game.")

    def test_spawn_again_when_not_sent_or_new_connection(self):
        s = ghost_session(("Bo", 103.0, 51.0, -196.0))
        self.assertEqual(self.ghost.requests(s, 10.0, 0)[0], ["ghost", "spawn"])
        self.ghost.replies(None)                                     # no game yet: not sent
        self.assertEqual(self.ghost.requests(s, 10.1, 1)[0], ["ghost", "spawn"])
        self.ghost.replies([{}, {}, {}])
        self.assertEqual(self.ghost.requests(s, 10.2, 1)[0][1], "pos")
        self.ghost.replies(None)                                     # a pos that didn't go out: nothing to redo
        self.assertEqual(self.ghost.requests(s, 10.3, 1)[0][1], "pos")
        self.assertEqual(self.ghost.requests(s, 10.4, 2)[0], ["ghost", "spawn"])   # new connection, maybe a new game

    def test_game_without_the_mod(self):
        s = ghost_session(("Bo", 103.0, 51.0, -196.0))
        reqs = self.ghost.requests(s, 10.0, 1)
        self.ghost.replies([ControlError("Unknown command") for _ in reqs])
        self.assertEqual(self.ghost.status_text(), "no ghost mod in the game")
        self.assertIn("has no `ghost` command", self.log[-1])
        self.assertEqual(self.ghost.requests(s, 10.1, 1), [])        # stops asking ...
        self.assertEqual(self.ghost.last(), [])
        n = len(self.log)
        self.assertEqual(self.ghost.requests(s, 12.0, 2)[0], ["ghost", "spawn"])   # ... until a new connection
        self.assertEqual(len(self.log), n)

    def test_other_errors_are_said_once(self):
        s = ghost_session(("Bo", 103.0, 51.0, -196.0))
        self.ghost.requests(s, 10.0, 1)
        self.ghost.replies([{}, ControlError("bad number: nan"), ControlError("bad number: nan")])
        self.assertEqual(self.log.count("Ghost: the game said: bad number: nan"), 1)
        self.assertTrue(self.ghost.spawned)

    def tick(self, s, now, status=None, conn=1):
        """One tick: the requests, answered ({} each, `status` for `ghost`)."""
        reqs = self.ghost.requests(s, now, conn)
        self.ghost.replies([dict(status or {}) if r == ["ghost"] else {} for r in reqs])
        return reqs

    def test_mod_that_stopped_trying_is_asked_again(self):
        s = ghost_session(("Bo", 103.0, 51.0, -196.0))
        spawn = ["ghost", "spawn"]
        stopped = {"state": "idle", "wanted": False, "last": "x: creation failed; gave up after 20 tries"}
        self.assertIn(spawn, self.tick(s, 10.0, dict(stopped, last="")))   # (that batch's reply is from before it)
        self.assertEqual(self.ghost.status_text(), "showing Bo (idle)")
        self.tick(s, 12.0, stopped)
        self.assertEqual(self.log[-1], "Ghost: the mod stopped trying; asking again in 10 s.")
        self.assertIn("asking again soon", self.ghost.status_text())
        self.assertNotIn(spawn, self.tick(s, 21.9, stopped))
        self.assertIn(spawn, self.tick(s, 22.0))
        self.tick(s, 24.0, stopped)                                  # stopped again: twice as long
        self.assertEqual(self.log[-1], "Ghost: the mod stopped trying; asking again in 20 s.")
        self.assertNotIn(spawn, self.tick(s, 43.9, stopped))
        self.assertIn(spawn, self.tick(s, 44.0))
        self.tick(s, 46.0, {"state": "live", "wanted": True, "live_s": 10.5})   # a ghost that stayed: start over
        self.assertEqual(self.ghost.status_text(), "showing Bo (live)")
        self.tick(s, 60.0, stopped)
        self.assertEqual(self.log[-1], "Ghost: the mod stopped trying; asking again in 10 s.")
        self.assertEqual(sum("stopped trying" in line for line in self.log), 3)

    def test_asking_again_waits_at_most_a_minute(self):
        s = ghost_session(("Bo", 103.0, 51.0, -196.0))
        stopped = {"state": "idle", "wanted": False, "last": "x: request refused; gave up after 20 tries"}
        now, spawns = 10.0, 0
        while now < 400.0:                                           # (the mod never manages)
            spawns += ["ghost", "spawn"] in self.tick(s, now, stopped)
            now += 0.5
        waits = [line.split(" in ")[-1] for line in self.log if "stopped trying" in line]
        self.assertEqual(waits[:6], ["10 s.", "20 s.", "40 s.", "60 s.", "60 s.", "60 s."])
        self.assertEqual(spawns, len(waits))                          # (the first spawn, then one per wait but the last)

    def test_despawn_until_the_game_answers_it(self):
        s = ghost_session(("Bo", 103.0, 51.0, -196.0))
        self.tick(s, 10.0)
        s.handle({"type": "leave", "id": 1}, now=10.5)
        self.assertEqual(self.ghost.requests(s, 10.5, 1), [["ghost", "despawn"]])
        self.ghost.replies(None)                                     # that batch failed
        self.assertEqual(self.ghost.last(), [["ghost", "despawn"]])  # (the game may still have it)
        self.assertEqual(self.ghost.requests(s, 10.6, 2), [["ghost", "despawn"]])   # again, on the new connection
        self.ghost.replies([{"despawn": "requested"}])
        self.assertEqual(self.ghost.requests(s, 10.7, 2), [])
        self.assertEqual(self.ghost.last(), [])
        self.assertEqual(self.log.count("Ghost: Bo is gone; ghost removed."), 1)

    def test_peer_left_while_the_game_was_away(self):
        s = ghost_session(("Bo", 103.0, 51.0, -196.0))
        self.tick(s, 10.0)
        s.handle({"type": "leave", "id": 1}, now=11.0)
        self.assertEqual(self.ghost.requests(s, 11.0, 2), [["ghost", "despawn"]])   # reconnected: still removed
        self.ghost.replies([ControlError("Developer mode is off")])  # (any answer from the game ends it)
        self.assertEqual(self.ghost.requests(s, 11.1, 2), [])
        self.assertEqual(self.ghost.last(), [])

    def test_valid_actor(self):
        for ok in ("Dm_Kibako_Contain_01", "a", "x" * 63):
            self.assertEqual(client.valid_actor(ok), ok)
        for bad in ("", "x" * 64, "Obj Box", "Obj-Box", "Kibakoé", None, 5, "a;b"):
            self.assertIsNone(client.valid_actor(bad))

    def pos_yaw(self, reqs):
        return float(next(r for r in reqs if r[1:2] == ["pos"])[-1])

    def test_ghost_faces_the_way_the_player_walks(self):
        s = ghost_session(("Bo", 0.0, 0.0, 0.0, 30.0))
        self.assertEqual(self.pos_yaw(self.tick(s, 10.0)), 30.0)      # not moved yet: their camera yaw
        t = 1.0
        steps = [(0.0, -0.1, 180.0), (0.0, -0.2, 180.0), (0.1, -0.2, 90.0), (0.1, -0.2, 90.0)]   # north, east, stop
        for x, z, want in steps:
            t += 0.05
            s.handle(dict(state(x=x, y=0.0, z=z, t=t, yaw=30.0), id=1, name="Bo"), now=9.0 + t)
            self.assertEqual(abs(self.pos_yaw(self.tick(s, 9.0 + t))), want)   # (north: 180 or -180)
        s.handle(dict(state(x=0.1, y=0.0, z=-0.2, t=5.0, yaw=-60.0), id=1, name="Bo"), now=14.0)
        self.assertEqual(self.pos_yaw(self.tick(s, 14.0)), 90.0)      # standing still: keeps it (not the camera)

    def test_teleport_reaches_the_mod_as_one_step(self):
        """Through the Session's jitter buffer: a player walks +x at 2 m/s for 1 s, then teleports along +z and
        stands. Whatever the clock phase, the ghost gets the teleport as one step (the mod jumps one over 30 m) and
        keeps facing +x, the way they walked."""
        for warp in (5.0, 40.0, 100.0, 1000.0):
            samples = [(i * 0.05, 2.0 * min(i * 0.05, 1.0), warp if i * 0.05 > 1.0 else 0.0) for i in range(40)]
            for k in range(25):
                s = client.Session("Me")                     # (the default 0.1 s jitter buffer)
                s.handle({"type": "welcome", "id": 9, "peers": []}, now=0.0)
                g = client.Ghost(log=quiet)
                sent, now, si = [], k * 0.002, 0
                while now < 2.0:                             # the client at 20 Hz
                    while si < len(samples) and samples[si][0] + 0.02 <= now:    # 20 ms on the way
                        t, x, z = samples[si]
                        s.handle(dict(state(x=x, y=0.0, z=z, t=t, yaw=0.0), id=1, name="Bo"), now=t + 0.02)
                        si += 1
                    s.update_local(POS, CAM, now=now)
                    reqs = g.requests(s, now, 1)
                    g.replies([{} for _ in reqs])
                    sent += [tuple(map(float, r[2:6])) for r in reqs if r[1:2] == ["pos"]]
                    now += 0.05
                steps = [math.dist(a[:3], b[:3]) for a, b in zip(sent, sent[1:])]
                with self.subTest(warp=warp, phase=k):
                    self.assertEqual([round(d, 1) for d in steps if d > 0.5], [warp])
                    self.assertEqual(sent[-1][3], 90.0)

    def test_heading(self):
        h = client.Heading()
        self.assertEqual(h.update(0.0, 0.0, 0.0), 0.0)                # nothing yet, no camera: 0
        self.assertEqual(h.update(0.0, 0.0, 0.05, -45.0), -45.0)      # standing: the camera yaw
        self.assertEqual(h.update(0.01, 0.0, 0.10, -45.0), -45.0)     # 0.2 m/s: too slow to count
        self.assertAlmostEqual(h.update(0.11, 0.1, 0.15, -45.0), 45.0)   # 2.8 m/s toward +x +z
        self.assertAlmostEqual(h.update(0.11, 0.1, 0.20, 0.0), 45.0)  # stopped: keeps the last
        self.assertAlmostEqual(h.update(0.11, 0.1, 0.20), 45.0)       # (no time passed: nothing changes)
        self.assertAlmostEqual(h.update(0.01, 0.1, 0.25), -90.0)      # toward -x
        self.assertAlmostEqual(h.update(0.01, 50.1, 0.30), -90.0)     # a 50 m jump is a teleport: keeps it
        self.assertAlmostEqual(h.update(0.01, 49.9, 0.35), 180.0)     # toward -z
        self.assertAlmostEqual(h.update(3.01, 49.9, 0.40), 180.0)     # 3 m in 0.05 s (60 m/s): a short teleport
        self.assertAlmostEqual(h.update(4.01, 49.9, 0.45), 90.0)      # 20 m/s toward +x
        self.assertAlmostEqual(h.update(4.01, 89.9, 3.45), 90.0)      # 40 m in 3 s: too far for a step
        h.reset()
        self.assertEqual(h.update(5.0, 5.0, 1.0, 12.5), 12.5)         # (a new player: the camera again)


class GameLinkSocketTests(unittest.TestCase):
    """GameLink with the real Control class against a stand-in for the game's control socket."""

    def setUp(self):
        self.game_srv = StandInGame()
        self.addCleanup(self.game_srv.close)
        self.clock = FakeClock(0.0)
        self.log = []
        self.links = []

    def link(self, timeout):
        g = client.GameLink(self.game_srv.port, connect=lambda: self.game_srv.connect(timeout), clock=self.clock,
                            log=self.log.append)
        self.links.append(g)
        self.addCleanup(g.close)
        return g

    def test_late_reply_does_not_shift_later_replies(self):
        g = self.link(timeout=0.3)
        pos, cam = g.poll()
        self.assertEqual((pos["z"], cam["yaw"], g.status), (POS["z"], CAM["yaw"], "ok"))
        self.game_srv.stall = 0.45                      # one long frame: pos answers after the 0.3 s timeout
        self.clock.now = 1.0
        self.assertEqual(g.poll(), (None, None))
        self.assertEqual(g.status, "not answering")
        self.assertIn("The game stopped answering; trying again every 2 s.", self.log)
        self.clock.now = 3.5                            # a new connection: every reply matches its request
        for _ in range(3):
            pos, cam = g.poll()
            self.assertEqual((pos["x"], pos["z"], pos["stamina"]), (POS["x"], POS["z"], POS["stamina"]))
            self.assertEqual((cam["z"], cam["yaw"]), (CAM["z"], CAM["yaw"]))
            self.assertEqual(g.status, "ok")
        self.assertEqual(self.log.count("Connected to the game."), 2)

    def test_busy_control_socket(self):
        self.game_srv.close_at_once = True              # all 4 of the game's control connections taken
        g = self.link(timeout=2.0)
        for i in range(4):
            self.clock.now = i * 2.5
            self.assertEqual(g.poll(), (None, None))
            self.assertEqual(g.status, "busy")
        self.assertEqual(self.log, ["The game's control socket is busy (it allows 4 connections); "
                                    "trying again every 2 s."])
        self.game_srv.close_at_once = False
        self.clock.now = 20.0
        pos, _ = g.poll()
        self.assertEqual(pos["x"], POS["x"])
        self.assertEqual(self.log[-1], "Connected to the game.")
        runner = client.Runner(("127.0.0.1", 1), "Me", game_port=self.game_srv.port, log=quiet)
        runner.game.status = "busy"
        self.assertIn("busy (the game allows 4 control connections)", runner.status_text())


class ClientRelayTests(unittest.IsolatedAsyncioTestCase):
    """Runner (fake game) and bot against a real relay on loopback."""

    async def asyncSetUp(self):
        self.relay = relay.Relay("127.0.0.1", 0, log=quiet)
        _, self.port = await self.relay.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.peers = []

    async def asyncTearDown(self):
        for p in self.peers:
            p.close()
        await self.relay.close()
        self.tmp.cleanup()

    async def wait_for(self, cond, timeout=5.0):
        end = time.monotonic() + timeout
        while not cond():
            if time.monotonic() > end:
                raise AssertionError("timed out")
            await asyncio.sleep(0.02)

    async def test_runner_sends_state_and_teleports_to_a_peer(self):
        fake = FakeControl()
        record = Path(self.tmp.name) / "paths" / "walk.jsonl"
        record.parent.mkdir()
        record.write_text("an older recording\n", encoding="utf-8")
        lines = []
        runner = client.Runner(("127.0.0.1", self.port), "Me", control_factory=lambda: fake, log=quiet,
                               record=str(record), print_every=0.05, out=lines.append, hz=20)
        task = asyncio.create_task(runner.run())
        try:
            b = await Peer.open(self.port)
            self.peers.append(b)
            welcome = await b.hello("Bo")
            if not welcome["peers"]:
                self.assertEqual((await b.recv_type("join"))["name"], "Me")
            got = await b.recv_type("state")
            self.assertEqual((got["name"], got["x"], got["y"], got["z"], got["yaw"]), ("Me", 100.0, 50.0, -200.0, 90.0))
            self.assertEqual(got["place"], POS["place"])
            await b.send(state(x=100.0, y=60.0, z=-240.0, t=time.monotonic()))
            await self.wait_for(lambda: any(v["dist"] is not None for v in runner.session.views(time.monotonic())))
            await self.wait_for(lambda: any(ln.startswith("Bo: 40 m N, 10 m higher") for ln in lines))
            self.assertIn("Game: in game", runner.status_text())
            self.assertIn("room default, 2 players", runner.status_text())
            runner.request_goto(welcome["id"])
            await self.wait_for(lambda: any(len(c) > 2 for c in fake.calls))
            tp = next(c[2] for c in fake.calls if len(c) > 2)
            self.assertEqual(tp, ["teleport", "100.000", f"{60.0 + client.GOTO_LIFT:.3f}", "-240.000"])
        finally:
            runner.stop()
            await asyncio.wait_for(task, 10.0)
        self.assertTrue(fake.closed)
        self.assertTrue(record.read_text(encoding="utf-8").startswith("{"))   # (replaced, not appended to)
        rows = [json.loads(ln) for ln in record.read_text(encoding="utf-8").splitlines()]
        self.assertGreater(len(rows), 0)
        self.assertEqual(set(rows[0]), {"t", "x", "y", "z", "yaw"})
        self.assertGreater(runner.sent, 0)

    async def test_runner_with_ghost_moves_it_and_removes_it_at_exit(self):
        fake = FakeControl(ghost_mod=True)
        runner = client.Runner(("127.0.0.1", self.port), "Me", control_factory=lambda: fake, log=quiet, hz=20,
                               delay=0.0, ghost=client.Ghost(log=quiet))
        task = asyncio.create_task(runner.run())
        try:
            b = await Peer.open(self.port)
            self.peers.append(b)
            await b.hello("Bo")
            await b.send(state(x=110.0, y=50.0, z=-190.0, yaw=-90.0, t=time.monotonic()))
            ghost_calls = lambda: [r for c in fake.calls for r in c if r[0] == "ghost"]   # noqa: E731
            await self.wait_for(lambda: ["ghost", "pos", "110.000", "50.000", "-190.000", "-90.0"] in ghost_calls())
            self.assertEqual(ghost_calls()[0], ["ghost", "spawn"])
            self.assertEqual(sum(r == ["ghost", "spawn"] for r in ghost_calls()), 1)
            for c in fake.calls:                                     # (one batch per tick: pos, camera, then ghost)
                self.assertEqual([r[0] for r in c[:2]], ["pos", "camera"])
            await self.wait_for(lambda: runner.ghost.state == "live")
            self.assertIn("Ghost: showing Bo (live)", runner.status_text())
        finally:
            runner.stop()
            await asyncio.wait_for(task, 10.0)
        self.assertEqual(fake.calls[-1], [["ghost", "despawn"]])
        self.assertTrue(fake.closed)

    async def test_status_text(self):
        runner = client.Runner(("127.0.0.1", self.port), "Me", control_factory=FakeControl, log=quiet)
        text = runner.status_text()
        self.assertIn("Game: waiting on port 7777", text)
        self.assertIn(f"connecting to 127.0.0.1:{self.port}", text)
        runner.failure = "PermissionError: no"
        self.assertTrue(runner.status_text().startswith("Stopped after an error: PermissionError: no"))

    async def test_relay_link_survives_garbage_and_errors(self):
        msgs, lines, hellos = [], [], []

        async def fake_relay(reader, writer):
            hellos.append(await reader.readline())
            writer.write(mp.encode({"type": "welcome", "id": 1, "room": "default", "peers": []}))
            writer.write(b"[" * 4000 + b"\n")                  # a broken relay
            writer.write(mp.encode({"type": "join", "id": 2, "name": "Bo", "bot": False}))
            await writer.drain()
            await reader.read()
            writer.close()

        server = await asyncio.start_server(fake_relay, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        link = client.RelayLink("127.0.0.1", port, "Me", on_message=msgs.append, log=lines.append, retry=0.05)
        stop = asyncio.Event()
        task = asyncio.create_task(link.run(stop))
        try:
            await self.wait_for(lambda: any(m["type"] == "join" for m in msgs))
            self.assertTrue(link.connected)
            self.assertEqual((link.peer_count, len(hellos)), (1, 1))
        finally:
            stop.set()
            await asyncio.wait_for(task, 5.0)
            server.close()

        class Broken(client.RelayLink):                       # anything unexpected: say so, reconnect
            calls = 0

            async def _session(self, reader, writer, stop):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("boom")
                stop.set()
                return 0.0

        async def silent(reader, writer):
            await reader.read()
            writer.close()

        server = await asyncio.start_server(silent, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        link = Broken("127.0.0.1", port, "Me", log=lines.append, retry=0.05)
        await asyncio.wait_for(link.run(asyncio.Event()), 5.0)
        server.close()
        self.assertEqual(link.calls, 2)
        self.assertIn("Error on the relay connection (RuntimeError: boom); reconnecting.", lines)

    async def test_bot_replay_start_stays_where_the_player_stood(self):
        a = await Peer.open(self.port)
        self.peers.append(a)
        await a.hello("Ana")
        await a.send(state(x=1000.0, y=0.0, z=0.0, t=time.monotonic()))
        path = bot.ReplayPath([{"t": 0, "x": 0, "y": 0, "z": 0}, {"t": 10, "x": 10, "y": 0, "z": 0}])
        stop = asyncio.Event()
        task = asyncio.create_task(bot.run_bot(("127.0.0.1", self.port), "bot", path=path, stop=stop, log=quiet))
        try:
            st = await a.recv_type("state")
            self.assertLess(abs(st["x"] - 1000.0), 1.0)
            self.assertEqual(st["z"], 0.0)
            await a.send(state(x=1000.0, y=0.0, z=50.0, t=time.monotonic()))     # Ana walks off
            for _ in range(10):                                                  # half a second of bot states
                st = await a.recv_type("state")
            self.assertEqual(st["z"], 0.0)                     # the replay did not move with her
            self.assertGreater(st["x"], 1000.0)
        finally:
            stop.set()
            await asyncio.wait_for(task, 5.0)

    async def test_bot_circles_around_the_first_real_player(self):
        a = await Peer.open(self.port)
        self.peers.append(a)
        await a.hello("Ana")
        await a.send(state(x=500.0, y=20.0, z=600.0, t=time.monotonic()))
        stop = asyncio.Event()
        task = asyncio.create_task(bot.run_bot(("127.0.0.1", self.port), "bot", path=bot.CirclePath(10, 3),
                                               stop=stop, log=quiet))
        try:
            join = await a.recv_type("join")
            self.assertTrue(join["bot"])
            for _ in range(5):
                st = await a.recv_type("state")
                self.assertAlmostEqual(math.hypot(st["x"] - 500.0, st["z"] - 600.0), 10.0, places=2)
                self.assertEqual(st["y"], 20.0)
        finally:
            stop.set()
            sent = await asyncio.wait_for(task, 5.0)
        self.assertGreaterEqual(sent, 5)
        self.assertEqual((await a.recv_type("leave"))["name"], "bot")


class ClientMainTests(unittest.TestCase):
    def test_bad_record_file_stops_before_anything_starts(self):
        async def started(_self):
            raise AssertionError("the client started")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(client.Runner, "run", started), \
                contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ex:
                client.main(["--no-ui", "--record", tmp])       # a folder, not a file
        self.assertEqual(ex.exception.code, 2)
        self.assertIn("can't write --record", err.getvalue())

    def test_ghost_options(self):
        made = []

        async def ran(runner):
            made.append(runner.ghost)

        def run(args):
            with mock.patch.object(client.Runner, "run", ran), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(client.main(["--no-ui", *args]), 0)
            return made[-1]

        self.assertIsNone(run([]))                                       # off unless asked
        g = run(["--ghost"])
        self.assertEqual((g.peer, g.actor), (None, None))
        g = run(["--ghost-peer", "Bo"])                                  # implies --ghost
        self.assertEqual((g.peer, g.actor), ("Bo", None))
        g = run(["--ghost", "--ghost-actor", "FldObj_Bokoblin_Bone"])
        self.assertEqual(g.actor, "FldObj_Bokoblin_Bone")
        for bad, why in ((["--ghost-peer", "x" * 30], "bad --ghost-peer"),
                         (["--ghost-actor", "Obj Box"], "bad --ghost-actor")):
            with mock.patch.object(client.Runner, "run", ran), contextlib.redirect_stderr(io.StringIO()) as err, \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as ex:
                    client.main(["--no-ui", *bad])
            self.assertEqual(ex.exception.code, 2)
            self.assertIn(why, err.getvalue())

    def test_stop_after_the_loop_closed(self):
        runner = client.Runner(("127.0.0.1", 1), "Me", log=quiet)
        runner.stop()                                           # never ran
        loop = asyncio.new_event_loop()
        runner._loop, runner._stop = loop, asyncio.Event()
        loop.close()
        runner.stop()                                           # ran and ended: nothing to do, no error


# ---------------------------------------------------------------- bot paths

class BotPathTests(unittest.TestCase):
    def test_circle(self):
        c = bot.CirclePath(radius=10, speed=2)
        for t in (0.0, 1.0, 7.5, 40.0):
            p = c.at(t, (1.0, 2.0, 3.0))
            self.assertAlmostEqual(math.hypot(p["x"] - 1.0, p["z"] - 3.0), 10.0)
            self.assertEqual(p["y"], 2.0)
            q = c.at(t + 0.01, (1.0, 2.0, 3.0))                 # faces the way it walks
            heading = math.degrees(math.atan2(q["x"] - p["x"], q["z"] - p["z"]))
            self.assertLess(angle_gap(heading, p["yaw"]), 0.1)
        moved = math.hypot(c.at(1.0, (0, 0, 0))["x"] - c.at(0.0, (0, 0, 0))["x"],
                           c.at(1.0, (0, 0, 0))["z"] - c.at(0.0, (0, 0, 0))["z"])
        self.assertAlmostEqual(moved, 2.0, places=1)           # about speed metres in a second
        with self.assertRaises(ValueError):
            bot.CirclePath(radius=0)

    def test_replay_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "walk.jsonl"
            rows = [{"t": 50.0, "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0},
                    {"t": 51.0, "x": 10.0, "y": 1.0, "z": 0.0, "yaw": 90.0},
                    {"t": 51.0, "x": 99.0, "y": 0.0, "z": 0.0},          # a repeat: skipped
                    {"t": 51.5, "x": 10.0, "y": 1.0, "z": 5.0, "yaw": 0.0},
                    {"t": 52.0, "x": 10.0, "y": 1.0, "z": 10.0, "yaw": 0.0},
                    {"t": 52.5, "x": 10.0, "y": 1.0, "z": 15.0, "yaw": 0.0},
                    {"t": 53.0, "x": 10.0, "y": 1.0, "z": 20.0, "yaw": 0.0}]
            f.write_text("\n".join(json.dumps(r) for r in rows[:2]) + "\nnot json\n[1]\n\n"
                         + "\n".join(json.dumps(r) for r in rows[2:]) + "\n", encoding="utf-8")
            path = bot.ReplayPath.load(f)
        self.assertEqual((path.skipped, path.recordings, path.gaps), (3, 1, 0))
        self.assertEqual(path.notes(), ["Skipped 3 unusable lines."])
        self.assertEqual(path.duration, 3.0)
        self.assertEqual(path.at(0.0)["x"], 0.0)
        mid = path.at(0.5)
        self.assertAlmostEqual(mid["x"], 5.0)
        self.assertAlmostEqual(mid["yaw"], 45.0)
        self.assertAlmostEqual(path.at(2.0)["z"], 10.0)
        self.assertAlmostEqual(path.at(3.5)["x"], 5.0)          # loops
        shifted = path.at(0.5, (100.0, 200.0, 300.0))
        self.assertEqual((round(shifted["x"], 6), shifted["y"], shifted["z"]), (105.0, 200.5, 300.0))
        with self.assertRaises(ValueError):
            bot.ReplayPath([{"t": 1, "x": 0, "y": 0, "z": 0}])

    def test_replay_jumps_over_gaps(self):
        """Two walks appended to one file an hour and 3 km apart: no hour-long glide across the map."""
        walk1 = [{"t": 5000 + i * 0.05, "x": i * 0.05, "y": 0, "z": 0} for i in range(401)]      # 20 s
        walk2 = [{"t": 8600 + i * 0.05, "x": 3000 + i * 0.05, "y": 0, "z": 0} for i in range(401)]
        path = bot.ReplayPath(walk1 + walk2)
        self.assertEqual((path.recordings, path.gaps), (1, 1))
        self.assertAlmostEqual(path.duration, 40.05, places=6)
        self.assertAlmostEqual(path.at(10.0)["x"], 10.0)
        self.assertAlmostEqual(path.at(20.04)["x"], 20.0)       # held at the end of walk 1 ...
        self.assertAlmostEqual(path.at(20.06)["x"], 3000.01)    # ... then at the start of walk 2 at once
        self.assertAlmostEqual(path.at(30.06)["x"], 3010.01)
        self.assertIn("Jumping over 1 gap with no position (loading screens or pauses).", path.notes())
        sparse = bot.ReplayPath([{"t": 0, "x": 0, "y": 0, "z": 0}, {"t": 10, "x": 10, "y": 0, "z": 0},
                                 {"t": 20, "x": 10, "y": 0, "z": 10}])   # few points far apart: no gaps
        self.assertEqual((sparse.gaps, sparse.duration), (0, 20.0))
        self.assertAlmostEqual(sparse.at(5.0)["x"], 5.0)

    def test_replay_plays_the_last_recording(self):
        """The clock going back means the PC restarted between two recordings in one file: play the last one."""
        walk1 = [{"t": 5000 + i * 0.05, "x": i * 0.05, "y": 0, "z": 0} for i in range(401)]
        walk2 = [{"t": 100 + i * 0.05, "x": 3000 + i * 0.05, "y": 0, "z": 0} for i in range(201)]
        path = bot.ReplayPath(walk1 + walk2, "walk.jsonl")
        self.assertEqual((path.recordings, len(path.samples)), (2, 201))
        self.assertAlmostEqual(path.duration, 10.0)
        self.assertAlmostEqual(path.at(0.0)["x"], 3000.0)
        self.assertEqual(path.notes(), ["walk.jsonl holds 2 recordings; replaying the last one."])
        path = bot.ReplayPath(walk1 + walk2[:1], "walk.jsonl")  # the last one has a single position
        self.assertAlmostEqual(path.at(0.0)["x"], 0.0)
        self.assertIn("replaying the last one with at least two positions", path.notes()[0])

    def test_first_real_peer_skips_bots(self):
        s = client.Session("bot", delay=0.0)
        s.handle({"type": "welcome", "id": 1, "peers": []}, now=0.0)
        s.handle({"type": "join", "id": 2, "name": "other bot", "bot": True}, now=0.0)
        s.handle(dict(state(x=1.0, t=1.0), id=2, name="other bot"), now=0.0)
        self.assertIsNone(bot.first_real_peer(s, 0.0))
        s.handle(dict(state(x=7.0, t=1.0), id=5, name="Ana"), now=0.0)
        s.handle(dict(state(x=8.0, t=1.0), id=3, name="Bo"), now=0.0)
        self.assertEqual(bot.first_real_peer(s, 0.0)[:2], (3, "Bo"))
        self.assertEqual(bot.first_real_peer(s, 0.0, keep=5)[:2], (5, "Ana"))


if __name__ == "__main__":
    unittest.main()
