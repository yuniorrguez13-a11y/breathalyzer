"""Tests for Who am I over the network (feature "look": tools/mp/session.py, relay.py, client.py, bot.py) and the
client's side of the ghost mod's `iam` (0.8.0). Loopback sockets and made-up values only: no game."""
import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import charsel  # noqa: E402
import mp  # noqa: E402
from mp import bot, client, relay, session  # noqa: E402


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
            if msg is None or msg["type"] == "pong":
                return got
            got.append(msg)

    def close(self):
        try:
            self.writer.close()
        except (OSError, RuntimeError):
            pass


ZELDA = {"id": "zelda", "v": 1, "name": "Zelda"}
GORON = {"id": "goron", "v": 1, "name": "Daruk (Goron)"}


class CleanLook(unittest.TestCase):
    def test_clean_look(self):
        self.assertEqual(session.clean_look(ZELDA), ZELDA)
        self.assertEqual(session.clean_look({"id": "built:zelda"}), {"id": "zelda", "v": 1})
        self.assertEqual(session.clean_look({"id": "zelda", "v": True}), {"id": "zelda", "v": 1})
        self.assertEqual(session.clean_look({"id": "zelda", "name": "  Big   Z  ", "x": "dropped", "model": "a" * 99}),
                         {"id": "zelda", "v": 1, "name": "Big Z"})
        self.assertEqual(session.clean_look({"id": "zelda", "name": "Zeldä"}), {"id": "zelda", "v": 1})
        self.assertEqual(session.clean_look({"id": "zelda", "name": "x" * 99})["name"], "x" * session.MAX_LOOK_NAME)
        for bad in (None, "zelda", {"id": "Zelda"}, {"id": "Dm_Npc_Zelda_Sibyl"}, {"id": "-zelda"}, {"id": "a" * 25},
                    {"id": "mod:../../x"}, {"id": ""}, {"id": 5}, {}):
            self.assertIsNone(session.clean_look(bad), bad)
        self.assertIn("look", session.FEATURES)
        self.assertIn("look", session.KNOWN_FEATURES)

    def test_coop_agents_never_say_look(self):
        """A co-op agent sends no look and wants none: its hello leaves "look" out."""
        from mp import coop_agent
        self.assertNotIn("look", session.COOP_FEATURES)
        src = Path(coop_agent.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count("session.hello("), src.count("features=session.COOP_FEATURES"))

    def test_hz_stays_under_the_relay_limit(self):
        from mp import security
        self.assertLess(client.MAX_HZ, security.KIND_RATES["state"])
        import contextlib
        import io
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            client.main(["--hz", "25", "--no-ui"])


class RelayLooks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = FakeClock()
        self.peers = []
        self.relay = relay.Relay("127.0.0.1", 0, clock=self.clock, log=quiet)
        _, self.port = await self.relay.start()

    async def asyncTearDown(self):
        for p in self.peers:
            p.close()
        await self.relay.close()

    async def join(self, name, look=None, features=("look",)):
        p = await Peer.open(self.port)
        self.peers.append(p)
        msg = {"type": "hello", "version": 1, "name": name, "room": "default"}
        if features is not None:
            msg["features"] = list(features)
        if look is not None:
            msg["look"] = look
        await p.send(msg)
        w = await p.recv()
        self.assertEqual(w["type"], "welcome", w)
        p.welcome, p.id = w, w["id"]
        return p

    async def test_looks_in_welcome_join_and_changes(self):
        ana = await self.join("Ana", ZELDA)
        self.assertIn("look", ana.welcome["features"])
        ben = await self.join("Ben", dict(GORON, extra="dropped"))
        self.assertEqual(ben.welcome["peers"][0]["look"], ZELDA)                 # a joiner sees the others' looks
        got = await ana.until_pong()
        join = next(m for m in got if m["type"] == "join")
        self.assertEqual(join["look"], GORON)                                    # ... and they see the joiner's
        # a change goes to the others, at most one every LOOK_EVERY s
        await ana.send({"type": "look", "look": {"id": "bokoblin", "v": 1}})
        got = await ben.until_pong()
        self.assertEqual([m for m in got if m["type"] == "look"],
                         [{"type": "look", "id": ana.id, "name": "Ana", "look": {"id": "bokoblin", "v": 1}}])
        await ana.send({"type": "look", "look": GORON})
        got = await ana.until_pong()
        self.assertIn("too often", got[0]["error"])
        self.clock.now += session.LOOK_EVERY
        await ana.send({"type": "look", "look": {"id": "Not_An_Id"}})
        got = await ana.until_pong()
        self.assertEqual(got[0]["error"], "bad look")
        # a late joiner sees the latest pick
        cid = await self.join("Cid", None)
        self.assertEqual({p["name"]: p.get("look") for p in cid.welcome["peers"]},
                         {"Ana": {"id": "bokoblin", "v": 1}, "Ben": GORON})
        cid_join = [m for m in await ana.until_pong() if m["type"] == "join"][0]
        self.assertNotIn("look", cid_join)                                       # (Cid said none)

    async def test_look_a_little_early_is_taken(self):
        """The client waits LOOK_EVERY by its own clock; with network jitter it reaches the relay a little early:
        still taken (LOOK_SLACK). Much earlier is refused."""
        ana = await self.join("Ana", ZELDA)
        ben = await self.join("Ben", GORON)
        await ana.until_pong()
        await ana.send({"type": "look", "look": GORON})
        await ben.until_pong()
        self.clock.now += session.LOOK_EVERY * 0.5
        await ana.send({"type": "look", "look": ZELDA})
        self.assertIn("too often", (await ana.until_pong())[0]["error"])
        self.clock.now += session.LOOK_EVERY * (session.LOOK_SLACK - 0.5) + 0.01   # (1.5 s after the last: 0.5 early)
        await ana.send({"type": "look", "look": ZELDA})
        self.assertEqual([m["look"] for m in await ben.until_pong() if m["type"] == "look"], [ZELDA])

    async def test_old_clients_never_see_looks(self):
        old = await self.join("Old", ZELDA, features=None)                       # a visitor: its look is ignored
        new = await self.join("New", GORON)
        self.assertNotIn("look", new.welcome["peers"][0])
        self.assertEqual(new.welcome["peers"][0]["name"], "Old")
        got = await old.until_pong()
        self.assertEqual([m["type"] for m in got], ["join"])
        self.assertNotIn("look", got[0])                                         # an old client: no new fields
        await new.send({"type": "look", "look": ZELDA})
        self.assertEqual(await old.until_pong(), [])                             # nor `look` lines
        await old.send({"type": "look", "look": ZELDA})                          # it can't send one either
        got = await old.until_pong()
        self.assertIn("unknown message type", got[0]["error"])
        # states still flow both ways
        await new.send({"type": "state", "t": 1, "x": 1, "y": 2, "z": 3})
        got = await old.until_pong()
        self.assertEqual(got[0]["type"], "state")


class ClientSide(unittest.TestCase):
    def test_session_keeps_looks(self):
        s = client.Session("Me")
        s.handle({"type": "welcome", "id": 1, "peers": [{"id": 2, "name": "Ana", "look": ZELDA},
                                                         {"id": 3, "name": "Ben", "look": {"id": "Bad Id"}},
                                                         {"id": 4, "name": "Old"}]}, 0.0)
        self.assertEqual({p.name: p.look for p in s.peers.values()}, {"Ana": ZELDA, "Ben": None, "Old": None})
        s.handle({"type": "join", "id": 5, "name": "Cid", "look": GORON}, 0.0)
        s.handle({"type": "look", "id": 2, "name": "Ana", "look": {"id": "rito", "v": 1}}, 0.0)
        s.handle({"type": "look", "id": 99, "look": GORON}, 0.0)                 # (nobody: ignored)
        s.handle({"type": "look", "id": 5, "look": "garbage"}, 0.0)              # (bad: the old one stays)
        self.assertEqual(s.peers[2].look, {"id": "rito", "v": 1})
        self.assertEqual(s.peers[5].look, GORON)
        self.assertEqual({v["name"]: v["look"] for v in s.views(0.0)}["Cid"], GORON)

    def test_hello_says_look_only_when_set(self):
        async def hello_of(look):
            got = {}

            async def serve(reader, writer):
                got["hello"] = json.loads(await reader.readline())
                writer.write(mp.encode({"type": "welcome", "id": 1, "room": "default", "peers": [],
                                        "features": ["look"]}))
                await writer.drain()
                await asyncio.sleep(0.2)
                writer.close()

            server = await asyncio.start_server(serve, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            link = client.RelayLink("127.0.0.1", port, "Me", look=look, log=quiet)
            stop = asyncio.Event()
            task = asyncio.create_task(link.run(stop))
            for _ in range(100):
                if "hello" in got and link.connected:
                    break
                await asyncio.sleep(0.02)
            speaks = link.speaks_look
            stop.set()
            await asyncio.wait_for(task, 3)
            server.close()
            return got["hello"], speaks

        h, speaks = asyncio.run(hello_of(ZELDA))
        self.assertEqual((h["features"], h["look"], speaks), (["look"], ZELDA, True))
        h, _ = asyncio.run(hello_of(None))
        self.assertNotIn("features", h)                                          # a plain visitor, as before
        self.assertNotIn("look", h)


class FakeGhostLooks:
    """A made-up catalog: zelda and goron built here."""

    @staticmethod
    def make():
        return charsel.Looks({"built": {"zelda": {"slot": charsel.SLOTS["zelda"]},
                                        "goron": {"slot": charsel.SLOTS["goron"]}}}, client.GHOST_LINK_ACTOR)


def view_session(look):
    s = client.Session("Me")
    s.local = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0}
    s.handle({"type": "welcome", "id": 1, "peers": [{"id": 2, "name": "Ana", "look": look}]}, 0.0)
    s.handle({"type": "state", "id": 2, "name": "Ana", "t": 1.0, "x": 3.0, "y": 0.0, "z": 0.0, "speed": 0}, 0.0)
    return s


def spawns(cmds):
    return [c for c in cmds if c[1:2] == ["spawn"]]


class GhostLooks(unittest.TestCase):
    def test_each_player_as_their_pick(self):
        logs = []
        g = client.Ghost(actor=client.GHOST_LINK_ACTOR, sync=True, looks=FakeGhostLooks.make(), log=logs.append)
        s = view_session(ZELDA)
        self.assertEqual(spawns(g.requests(s, 1.0, 1)), [["ghost", "spawn", charsel.SLOTS["zelda"]]])
        self.assertEqual(spawns(g.requests(s, 1.05, 1)), [])                     # (once)
        self.assertIn("Ghost: Ana is Zelda.", logs)
        # a new pick: the new body (the mod replaces the old one)
        s.handle({"type": "look", "id": 2, "look": GORON}, 1.1)
        self.assertEqual(spawns(g.requests(s, 1.1, 1)), [["ghost", "spawn", charsel.SLOTS["goron"]]])
        # a pick this PC lacks: the Link body, and why
        s.handle({"type": "look", "id": 2, "look": {"id": "rito", "v": 1}}, 1.2)
        self.assertEqual(spawns(g.requests(s, 1.2, 1)), [["ghost", "spawn", client.GHOST_LINK_ACTOR]])
        self.assertIn("Ghost: Ana is Revali (Rito) (shown as Link: not built here).", logs)
        self.assertIn("as Revali (Rito) (Link body here)", g.status_text())

    def test_a_body_that_never_loads_falls_back(self):
        logs = []
        g = client.Ghost(actor=client.GHOST_LINK_ACTOR, sync=True, looks=FakeGhostLooks.make(), log=logs.append)
        s = view_session(ZELDA)
        g.requests(s, 1.0, 1)
        g.replies([None])
        g.requests(s, 3.0, 1)                                                    # (asks `ghost`)
        g.replies([{"state": "idle", "wanted": False} if c == ["ghost"] else {} for c in g._sent])
        self.assertTrue(any("didn't load" in m for m in logs), logs)
        self.assertEqual(spawns(g.requests(s, 3.1, 1)), [["ghost", "spawn", client.GHOST_LINK_ACTOR]])
        self.assertEqual(g.looks.actor(ZELDA)[0], client.GHOST_LINK_ACTOR)       # for the rest of the session

    def test_without_looks_as_before(self):
        g = client.Ghost(actor=client.GHOST_LINK_ACTOR, sync=True, log=quiet)
        s = view_session(ZELDA)
        self.assertEqual(spawns(g.requests(s, 1.0, 1)), [["ghost", "spawn", client.GHOST_LINK_ACTOR]])
        g = client.Ghost(log=quiet)
        self.assertEqual(spawns(g.requests(view_session(GORON), 1.0, 1)), [["ghost", "spawn"]])

    def test_iam_from_the_game(self):
        g = client.Ghost(sync=True, log=quiet)
        s = view_session(None)
        g.requests(s, 1.0, 1)
        g.replies([{"state": "live", "has_iam": True, "iam_seq": 0, "iam": ""} if c == ["ghost"] else {}
                   for c in g._sent])
        self.assertIsNone(g.take_iam())
        cmds = g.requests(s, 3.5, 1)
        looks = [c for c in cmds if c[1:2] == ["looks"]]
        self.assertEqual(looks, [["ghost", "looks", client.iam_list(), "link", "0"]])   # the list, your pick, seen
        self.assertEqual([c for c in g.requests(s, 3.6, 1) if c[1:2] == ["looks"]], [])
        g._sent = [["ghost"]]
        g.replies([{"state": "live", "has_iam": True, "iam_seq": 1, "iam": "goron"}])
        self.assertEqual(g.take_iam(), "goron")
        self.assertIsNone(g.take_iam())
        g.my_look = "goron"                                                      # (the Runner took it)
        self.assertEqual([c for c in g.requests(s, 3.7, 1) if c[1:2] == ["looks"]],
                         [["ghost", "looks", client.iam_list(), "goron", "1"]])  # (seen 1: the mod clears the word)
        g._sent = [["ghost"]]
        g.replies([{"state": "live", "has_iam": True, "iam_seq": 1, "iam": "goron"}])
        self.assertIsNone(g.take_iam())                                          # (the same seq: once)
        g._sent = [["ghost"]]
        g.replies([{"state": "live", "has_iam": True, "iam_seq": 2, "iam": "Enemy_Lynel"}])
        self.assertIsNone(g.take_iam())                                          # (not a character: ignored)
        # a new game connection: the list goes again
        cmds = g.requests(s, 9.0, 2)
        self.assertEqual([c for c in cmds if c[1:2] == ["looks"]], [])           # (not until the mod says has_iam)
        g._sent = [["ghost"]]
        g.replies([{"state": "live", "has_iam": True, "iam_seq": 0, "iam": ""}])
        self.assertEqual(len([c for c in g.requests(s, 9.1, 2) if c[1:2] == ["looks"]]), 1)


    def first_reply(self, take_old: bool, seq: int, typed: str):
        g = client.Ghost(sync=True, log=quiet)
        g.take_old_iam = take_old
        s = view_session(None)
        g.requests(s, 1.0, 1)
        g.replies([{"state": "live", "has_iam": True, "iam_seq": seq, "iam": typed} if c == ["ghost"] else {}
                   for c in g._sent])
        return g, s

    def test_an_old_iam_never_wins(self):
        """The first iam_seq on a connection is where the client starts. A word an earlier client already took is
        cleared by the mod (iam ""), so it never comes back; with --look (take_old_iam False) a word typed before
        this client is not taken either. A word typed while it runs always is."""
        g, s = self.first_reply(True, 3, "")                                     # (an earlier client took it)
        self.assertIsNone(g.take_iam())
        self.assertEqual([c[-1] for c in g.requests(s, 3.5, 1) if c[1:2] == ["looks"]], ["3"])
        g, s = self.first_reply(False, 3, "zelda")                               # --look goron: the command line wins
        self.assertIsNone(g.take_iam())
        g, s = self.first_reply(True, 3, "zelda")                                # typed before any client: taken
        self.assertEqual(g.take_iam(), "zelda")
        g, s = self.first_reply(False, 3, "zelda")
        g._sent = [["ghost"]]
        g.replies([{"state": "live", "has_iam": True, "iam_seq": 4, "iam": "rito"}])   # typed while it runs
        self.assertEqual(g.take_iam(), "rito")

    def test_no_look_sends_no_list(self):
        g = client.Ghost(sync=True, log=quiet)
        client.Runner(("127.0.0.1", 9), "Me", log=quiet, ghost=g, look=None)    # --no-look
        s = view_session(None)
        g.requests(s, 1.0, 1)
        g.replies([{"state": "live", "has_iam": True, "iam_seq": 0, "iam": ""} if c == ["ghost"] else {}
                   for c in g._sent])
        self.assertEqual([c for c in g.requests(s, 3.5, 1) if c[1:2] == ["looks"]], [])

    def test_two_friends_at_about_the_same_distance(self):
        """The ghost stays on the one shown until another is GHOST_SWITCH_M nearer for GHOST_SWITCH_S: with
        --ghost-looks every swap would be a new body."""
        g = client.Ghost(actor=client.GHOST_LINK_ACTOR, sync=True, looks=FakeGhostLooks.make(), log=quiet)
        s = client.Session("Me")
        s.local = {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0}
        s.handle({"type": "welcome", "id": 1, "peers": [{"id": 2, "name": "Ana", "look": ZELDA},
                                                         {"id": 3, "name": "Ben", "look": GORON}]}, 0.0)

        def at(t, ana, ben):
            for pid, name, x in ((2, "Ana", ana), (3, "Ben", ben)):
                s.handle({"type": "state", "id": pid, "name": name, "t": t, "x": x, "y": 0.0, "z": 0.0, "speed": 0},
                         t)
            return spawns(g.requests(s, t + client.DEFAULT_DELAY, 1))   # (the newest sample)

        self.assertEqual(at(1.0, 3.0, 3.5), [["ghost", "spawn", charsel.SLOTS["zelda"]]])
        n = 0
        for k in range(40):                                                      # they swap places, a little
            t = 1.1 + k * 0.05
            n += len(at(t, 3.0 + (0.6 if k % 2 else 0.0), 3.3))
        self.assertEqual((n, g.shown), (0, 2))                                   # no new body, Ana still shown
        self.assertEqual(at(4.0, 9.0, 3.0), [])                                  # Ben clearly nearer: not at once
        self.assertEqual(at(4.5, 9.0, 3.0), [])
        self.assertEqual(at(5.1, 9.0, 3.0), [["ghost", "spawn", charsel.SLOTS["goron"]]])   # ... after 1 s
        self.assertEqual(g.shown, 3)


class RunnerLook(unittest.TestCase):
    def test_set_look_sends_one_every_few_seconds(self):
        clock = FakeClock(100.0)
        kept = []
        r = client.Runner(("127.0.0.1", 9), "Me", clock=clock, log=quiet, look=charsel.look_message("link"),
                          on_look=kept.append)
        sent = []
        r.relay.connected, r.relay.speaks_look = True, True
        r.relay.send = lambda m: sent.append(m) or True
        self.assertTrue(r.set_look("zelda"))
        self.assertFalse(r.set_look("ganon"))
        r._send_look()
        self.assertEqual(sent, [{"type": "look", "look": charsel.look_message("zelda")}])
        self.assertEqual(r.relay.look, charsel.look_message("zelda"))            # (the next hello says it too)
        r.set_look("goron")
        clock.now += 0.5
        r._send_look()
        self.assertEqual(len(sent), 1)                                           # (too soon: it waits)
        clock.now += session.LOOK_EVERY
        r._send_look()
        self.assertEqual(sent[-1]["look"]["id"], "goron")
        self.assertEqual(kept, ["zelda", "goron"])                              # (kept for next time)
        self.assertIn("You: Daruk (Goron)", r.status_text())
        # an older relay (no "look" in its welcome): nothing is sent, the hello still carries it
        r.relay.speaks_look = False
        r.set_look("rito")
        clock.now += 10
        r._send_look()
        self.assertEqual(sent[-1]["look"]["id"], "goron")

    def test_a_refused_look_goes_again(self):
        """The relay said "look changes too often" (it came a little early by the relay's clock): the newest look
        goes again once the wait is over, so the friend never keeps the old one."""
        clock = FakeClock(100.0)
        r = client.Runner(("127.0.0.1", 9), "Me", clock=clock, log=quiet, look=charsel.look_message("link"))
        sent = []
        r.relay.connected, r.relay.speaks_look = True, True
        r.relay.send = lambda m: sent.append(m) or True
        r.set_look("zelda")
        r._send_look()
        r.relay.look_refused = True                                              # (the relay's error line)
        clock.now += 0.5
        r._send_look()
        self.assertEqual(len(sent), 1)                                           # (not too soon again)
        clock.now += session.LOOK_EVERY
        r._send_look()
        self.assertEqual([m["look"]["id"] for m in sent], ["zelda", "zelda"])
        clock.now += session.LOOK_EVERY
        r._send_look()
        self.assertEqual(len(sent), 2)                                           # (once)


class BotLooks(unittest.TestCase):
    def test_look_script(self):
        ls = bot.LookScript.parse("Zelda:5, daruk:5,boko")
        self.assertEqual([ls.at(t) for t in (0, 4.9, 5, 10.1)], ["zelda", "zelda", "goron", "bokoblin"])
        for bad in ("ganon:5", "zelda:1", "zelda:x", ""):
            with self.assertRaises(ValueError):
                bot.LookScript.parse(bad)


class EndToEnd(unittest.IsolatedAsyncioTestCase):
    async def test_bot_look_reaches_a_client_session(self):
        r = relay.Relay("127.0.0.1", 0, log=quiet)
        _, port = await r.start()
        stop = asyncio.Event()
        watcher = client.Session("Me")
        link = client.RelayLink("127.0.0.1", port, "Me", look=charsel.look_message("link"), log=quiet,
                                on_message=lambda m: watcher.handle(m, 0.0))
        tasks = [asyncio.create_task(link.run(stop)),
                 asyncio.create_task(bot.run_bot(("127.0.0.1", port), "Bot", center=(0.0, 0.0, 0.0), stop=stop,
                                                 log=quiet, looks=bot.LookScript.parse("zelda:2,goron:2")))]
        try:
            seen = set()
            for _ in range(300):
                with watcher.lock:
                    for p in watcher.peers.values():
                        if p.look:
                            seen.add(p.look["id"])
                if {"zelda", "goron"} <= seen:
                    break
                await asyncio.sleep(0.02)
            self.assertEqual(seen, {"zelda", "goron"})
        finally:
            stop.set()
            await asyncio.wait(tasks, timeout=5)
            await r.close()


if __name__ == "__main__":
    unittest.main()
