"""Tests for the shared session layer and base security (tools/mp/session.py, security.py, discovery.py and the relay's
co-op rules). Loopback sockets and made-up values only: no game."""
import asyncio
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import mp  # noqa: E402
from mp import discovery, relay, security, session  # noqa: E402


def quiet(*_a, **_k):
    pass


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class Peer:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def open(cls, port):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        return cls(r, w)

    async def send(self, msg):
        self.writer.write(mp.encode(msg))
        await self.writer.drain()

    async def recv(self, timeout=3.0):
        line = await asyncio.wait_for(self.reader.readline(), timeout)
        return json.loads(line) if line else None

    async def until_pong(self):
        await self.send({"type": "ping", "t": 1})
        got = []
        while True:
            msg = await self.recv()
            if msg is None:
                return got + [None]
            if msg["type"] == "pong":
                return got
            got.append(msg)

    async def closed(self, timeout=3.0):
        try:
            while True:
                line = await asyncio.wait_for(self.reader.readline(), timeout)
                if not line:
                    return True
        except (ConnectionError, OSError):
            return True
        except asyncio.TimeoutError:
            return False

    def close(self):
        try:
            self.writer.close()
        except (OSError, RuntimeError):
            pass


SECRET = "11" * 32
FEATS = ["coop1", "story1"]


class SessionRelayTests(unittest.IsolatedAsyncioTestCase):
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
        kw.setdefault("host_secret", SECRET)
        self.relay = relay.Relay("127.0.0.1", 0, **kw)
        _, self.port = await self.relay.start()

    async def join(self, name, room="default", *, host=False, features=FEATS, expect="welcome", **extra):
        p = await Peer.open(self.port)
        self.peers.append(p)
        msg = {"type": "hello", "version": 1, "name": name, "room": room}
        if features is not None:
            msg["features"] = features
        if host:
            msg["host_proof"] = security.host_proof(SECRET, room)
        msg.update(extra)
        await p.send(msg)
        w = await p.recv()
        self.assertEqual(w["type"], expect, w)
        p.welcome = w
        p.id = w.get("id")
        return p

    async def test_host_and_friend_features(self):
        await self.start()
        host = await self.join("Ana", host=True)
        self.assertTrue(host.welcome["you_host"])
        self.assertEqual(host.welcome["host"], host.id)
        self.assertEqual(host.welcome["features"], list(session.FEATURES))
        self.assertEqual(host.welcome["rules"], {"friends_story": False})
        ben = await self.join("Ben", features=["coop1", "story1", "warp9"])   # unknown feature: ignored
        self.assertFalse(ben.welcome["you_host"])
        self.assertEqual(ben.welcome["host"], host.id)
        self.assertEqual(ben.welcome["peers"][0]["features"], FEATS)
        join = await host.recv()
        self.assertEqual(join["features"], FEATS)
        self.assertFalse(join["host"])

    async def test_wrong_proof_or_second_host_is_a_friend(self):
        await self.start()
        fake = await self.join("Eve", host_proof="00" * 16)
        self.assertFalse(fake.welcome["you_host"])
        host = await self.join("Ana", host=True)
        self.assertTrue(host.welcome["you_host"])
        again = await self.join("Ana2", host=True)          # one host per room
        self.assertFalse(again.welcome["you_host"])

    async def test_time_story_reach_friends_not_visitors(self):
        await self.start()
        host = await self.join("Ana", host=True)
        ben = await self.join("Ben")
        old = await self.join("Old", features=None)         # an older client: a visitor
        self.assertNotIn("features", old.welcome)
        await host.until_pong()
        await host.send({"type": "time", "h": 21.0, "from": 99, "name": "Fake"})
        await host.send({"type": "story", "seq": 1, "list": "9f3a10c2", "bits": "0f00"})
        got = await ben.until_pong()
        kinds = {m["type"]: m for m in got}
        self.assertEqual(kinds["time"]["h"], 21.0)
        self.assertEqual(kinds["time"]["from"], host.id)     # written by the relay
        self.assertEqual(kinds["time"]["name"], "Ana")
        self.assertEqual(kinds["story"]["bits"], "0f00")
        got_old = await old.until_pong()
        self.assertFalse([m for m in got_old if m["type"] in ("time", "story")])
        # a late joiner gets the host's last time and story in its welcome; a visitor doesn't
        late = await self.join("Cid")
        self.assertEqual(late.welcome["time"]["h"], 21.0)
        self.assertEqual(late.welcome["story"]["seq"], 1)
        late_old = await self.join("Dee", features=None)
        self.assertNotIn("time", late_old.welcome)

    async def test_host_only_from_a_friend_is_dropped_then_kicked(self):
        await self.start()
        host = await self.join("Ana", host=True)
        ben = await self.join("Ben")
        await host.until_pong()
        for kind in ("time", "story"):
            await ben.send({"type": kind, "h": 3.0, "seq": 5, "list": "9f3a10c2", "bits": "ff"})
        got = await host.until_pong()
        self.assertFalse([m for m in got if m["type"] in ("time", "story")])
        await ben.send({"type": "end"})                     # third host-only line in 10 s
        self.assertTrue(await ben.closed())
        self.assertIsNone(self.relay.room_info["default"]["time"])

    async def test_bad_values_dropped(self):
        await self.start()
        host = await self.join("Ana", host=True)
        ben = await self.join("Ben")
        await host.send({"type": "time", "h": 24.0})
        await host.send({"type": "story", "seq": 1, "list": "zz", "bits": "00"})
        await host.send({"type": "story", "seq": 1, "list": "9f3a10c2", "bits": "abc"})
        got = await host.until_pong()
        self.assertEqual([m["error"] for m in got if m["type"] == "error"], ["bad time", "bad story", "bad story"])
        self.assertFalse([m for m in await ben.until_pong() if m["type"] in ("time", "story")])

    async def test_rate_limit_per_kind(self):
        await self.start()
        host = await self.join("Ana", host=True)
        ben = await self.join("Ben")
        for h in range(6):
            await host.send({"type": "time", "h": float(h)})
        await host.until_pong()
        got = [m for m in await ben.until_pong() if m["type"] == "time"]
        self.assertEqual(len(got), 2)                       # 2 a second (the fake clock stands still)

    async def test_room_code(self):
        await self.start(room_code="Apple River Stone")
        await self.join("Ana", host=True)                    # the host needs no code
        ok = await self.join("Ben", code="apple river stone")
        self.assertEqual(ok.welcome["code"], True)
        bad = await self.join("Eve", code="apple river rock", expect="error")
        self.assertEqual(bad.welcome["error"], "wrong room code")
        for i in range(4):
            await self.join(f"Eve{i}", code="nope", expect="error")
        locked = await self.join("Eve9", code="apple river stone", expect="error")   # 5 wrong in a minute
        self.assertIn("10 minutes", locked.welcome["error"])
        self.clock.now += security.WRONG_CODE_WAIT + 1
        await self.join("Eve10", code="apple river stone")

    async def test_kick_and_ban(self):
        bans = security.BanList(clock=self.clock)
        await self.start(bans=bans)
        host = await self.join("Ana", host=True)
        ben = await self.join("Ben", key="ab" * 8)
        await host.send({"type": "kick", "id": ben.id})
        bye = await ben.recv()
        self.assertEqual(bye, {"type": "bye", "reason": "kicked by the host"})
        self.assertTrue(await ben.closed())
        p = await Peer.open(self.port)                       # same address: out for 10 minutes
        self.peers.append(p)
        await p.send({"type": "hello", "version": 1, "name": "Ben", "room": "default", "features": FEATS})
        self.assertEqual((await p.recv())["type"], "error")
        self.clock.now += security.KICK_WAIT + 1
        cid = await self.join("Cid", key="cd" * 8)
        await host.send({"type": "ban", "id": cid.id})
        self.assertTrue(await cid.closed())
        self.assertEqual(bans.blocked("cd" * 8, None), "banned")

    async def test_end_only_by_host(self):
        await self.start()
        host = await self.join("Ana", host=True)
        ben = await self.join("Ben")
        await host.send({"type": "end"})
        got = []
        while True:
            m = await ben.recv()
            if m is None:
                break
            got.append(m)
        self.assertEqual([m["type"] for m in got][-2:], ["end", "bye"])
        self.assertEqual(got[-1]["reason"], "Ana ended the session")

    async def test_story_offer_needs_the_host_rule(self):
        await self.start()
        host = await self.join("Ana", host=True)
        ben = await self.join("Ben")
        await host.until_pong()
        offer = {"type": "story_offer", "list": "9f3a10c2", "bits": "01"}
        await ben.send(offer)
        got = await ben.until_pong()
        self.assertIn("doesn't take", got[-1]["error"])
        self.assertFalse([m for m in await host.until_pong() if m["type"] == "story_offer"])
        await host.send({"type": "rules", "friends_story": True})
        self.assertEqual((await ben.recv())["type"], "rules")
        self.clock.now += 2
        await ben.send(offer)
        await ben.until_pong()
        got = [m for m in await host.until_pong() if m["type"] == "story_offer"]
        self.assertEqual(got[0]["from"], ben.id)

    async def test_no_secret_means_no_host(self):
        await self.start(host_secret=None)
        a = await self.join("Ana", host=True)
        self.assertFalse(a.welcome["you_host"])
        self.assertIsNone(a.welcome["host"])

    async def test_ghost_client_sends_the_code(self):
        from mp import client
        await self.start(room_code="apple river stone")
        stop = asyncio.Event()
        got = []
        link = client.RelayLink("127.0.0.1", self.port, "Ana", "default", log=quiet, on_message=got.append,
                                code="apple river stone")
        task = asyncio.create_task(link.run(stop))
        for _ in range(60):
            if link.connected:
                break
            await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, 5)
        self.assertTrue(any(m.get("type") == "welcome" for m in got), got)

    async def test_lan_only_allows_loopback(self):
        await self.start(lan_only=True)
        await self.join("Ana")


class SecurityTests(unittest.TestCase):
    def test_words(self):
        self.assertEqual(len(security.WORDS), 256)
        self.assertEqual(len(set(security.WORDS)), 256)
        self.assertTrue(all(w.isalpha() and w.islower() for w in security.WORDS))

    def test_codes(self):
        code = security.make_code()
        self.assertEqual(security.normalize_code(code.upper()), code)
        self.assertEqual(security.normalize_code(" Apple-river,STONE "), "apple river stone")
        for bad in ("apple river", "apple river zzz", "a" * 80, None, 5):
            self.assertIsNone(security.normalize_code(bad))
        self.assertTrue(security.code_matches(None, None))
        self.assertTrue(security.code_matches("apple river stone", "APPLE RIVER STONE"))
        self.assertFalse(security.code_matches("apple river stone", "apple river rock"))
        self.assertFalse(security.code_matches("apple river stone", None))

    def test_keys_files(self):
        with tempfile.TemporaryDirectory() as d:
            s1 = security.host_secret(Path(d))
            self.assertEqual(s1, security.host_secret(Path(d)))
            self.assertEqual(len(s1), 64)
            k = security.player_key(Path(d))
            fp = security.fingerprint(k)
            self.assertEqual(security.valid_fingerprint(fp), fp)
            self.assertNotIn(k, fp)
            self.assertEqual(len(security.check_words(fp).split()), 4)
            self.assertTrue(security.host_proof_matches(s1, "r", security.host_proof(s1, "r")))
            self.assertFalse(security.host_proof_matches(s1, "other", security.host_proof(s1, "r")))

    def test_addresses(self):
        for ok in ("127.0.0.1", "192.168.1.20", "10.0.0.5", "172.16.3.4", "169.254.1.1", "100.100.1.1", "::1",
                   "fe80::1%3", "fd00::5", "::ffff:192.168.0.2"):
            self.assertTrue(security.address_allowed(ok), ok)
        for bad in ("8.8.8.8", "1.1.1.1", "2001:4860::8888", "0.0.0.0", "nope", None, "224.0.0.1"):
            self.assertFalse(security.address_allowed(bad), bad)

    def test_bans_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "mp" / "bans.json"
            b = security.BanList(path)
            b.ban("ab" * 8, "192.168.1.9", "Eve")
            again = security.BanList(path)
            self.assertEqual(again.blocked(None, "192.168.1.9"), "banned")
            self.assertEqual(again.unban("Eve"), 1)
            self.assertIsNone(security.BanList(path).blocked("ab" * 8, None))

    def test_limiter_and_strikes(self):
        clock = FakeClock()
        lim = security.KindLimiter(clock=clock)
        self.assertEqual([lim.allow("weather") for _ in range(3)], [True, False, False])
        clock.now += 1.0
        self.assertTrue(lim.allow("weather"))
        self.assertTrue(all(lim.allow("ping") for _ in range(100)))   # not limited per kind
        st = security.Strikes(3, 10.0, clock)
        self.assertEqual([st.hit("x"), st.hit("x")], [False, False])
        clock.now += 11
        self.assertFalse(st.hit("x"))


class SessionTests(unittest.TestCase):
    def test_hello_small_and_features(self):
        h = session.hello("Ben", "default", caps={"ghost": "0.6.2", "game": "1.9.0", "dlc": 0},
                          story={"v": 1, "base": "9f3a10c2", "dlc": None}, code="apple river stone", key="ab" * 8,
                          host_proof="cd" * 16)
        self.assertLess(len(mp.encode(h)), session.MAX_HELLO)
        self.assertEqual(session.clean_features(["coop1", "x" * 40, 5, "coop1", "look"]), ["coop1", "look"])
        self.assertIsNone(session.clean_features(None))
        self.assertEqual(session.clean_caps({"ghost": "0.6.2", "dlc": True, "sw": "bad value!"}), {"ghost": "0.6.2"})

    def test_validators(self):
        self.assertEqual(session.clean_time({"h": 21}), {"h": 21.0})
        for bad in (-1, 24, "3", True, float("nan")):
            self.assertIsNone(session.clean_time({"h": bad}))
        self.assertIsNotNone(session.clean_story({"seq": 0, "list": "0123abcd", "bits": "00ff"}))
        for bad in ({"seq": -1}, {"seq": True}, {"list": "XYZ"}, {"bits": "0"}, {"bits": "AB"}, {"bits": "00" * 300}):
            msg = dict({"seq": 1, "list": "0123abcd", "bits": "00"}, **bad)
            self.assertIsNone(session.clean_story(msg), bad)
        self.assertIsNotNone(session.clean_story({"list": "0123abcd", "bits": "00"}, offer=True))
        self.assertEqual(session.clean_rules({"friends_story": True}), {"friends_story": True})
        self.assertIsNone(session.clean_rules({"friends_story": 1}))
        self.assertTrue(session.older_relay({"type": "welcome"}))
        self.assertFalse(session.older_relay({"type": "welcome", "features": []}))


class DiscoveryTests(unittest.TestCase):
    def test_query_and_answer(self):
        q = discovery.make_query()
        self.assertEqual(len(q), discovery.QUERY_SIZE)
        self.assertTrue(discovery.is_query(q))
        self.assertFalse(discovery.is_query(q[:100]))
        info = {"name": "Ana", "players": 2, "max": 8, "code": True, "port": 7788, "ver": "0.7.0",
                "check": "apple river stone moon"}
        a = discovery.make_answer(dict(info, room_code="apple river stone"))
        self.assertNotIn(b"room_code", a)                                     # only "needs a code", never the code
        parsed = discovery.parse_answer(a)
        self.assertEqual(parsed["world"], "Ana's world")
        self.assertTrue(parsed["code"])
        self.assertIsNone(discovery.make_answer(info, limit=50))              # never more bytes than the query
        for bad in (b"{}", b"x" * 600, json.dumps({"t": "brz", "v": 1, "name": "<evil>", "world": "w", "players": 1,
                                                   "max": 8, "code": False, "port": 1}).encode()):
            self.assertIsNone(discovery.parse_answer(bad))

    def test_responder_rules(self):
        clock = FakeClock()
        r = discovery.Responder(lambda: {"name": "Ana", "code": False}, clock)
        q = discovery.make_query()
        self.assertIsNotNone(r.answer_for(q, "192.168.1.5"))
        self.assertIsNone(r.answer_for(q, "192.168.1.5"))                    # once a second each
        self.assertIsNone(r.answer_for(q, "8.8.8.8"))                        # not a home address
        self.assertIsNone(r.answer_for(b"hi", "192.168.1.6"))
        clock.now += 1.0
        self.assertIsNotNone(r.answer_for(q, "192.168.1.5"))

    def test_table(self):
        clock = FakeClock()
        t = discovery.HostTable(clock)
        info = discovery.parse_answer(discovery.make_answer({"name": "Ana"}))
        for i in range(20):
            t.add(f"192.168.1.{i}", info)
        self.assertEqual(len(t.list()), discovery.MAX_HOSTS)
        self.assertFalse(t.add("8.8.8.8", info))
        clock.now += discovery.GONE_AFTER + 0.1
        self.assertEqual(t.list(), [])

    def test_search_on_loopback(self):
        sock = discovery.open_responder_socket(0, "127.0.0.1")
        port = sock.getsockname()[1]
        stop = threading.Event()
        r = discovery.Responder(lambda: {"name": "Ana", "players": 1, "code": True, "port": 7788})
        th = threading.Thread(target=r.serve, args=(sock, stop.is_set), daemon=True)
        th.start()
        try:
            found = discovery.search(1.0, port, targets=("127.0.0.1",))
        finally:
            stop.set()
            th.join(2)
            sock.close()
        self.assertEqual([h["name"] for h in found], ["Ana"])
        self.assertEqual(found[0]["address"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
