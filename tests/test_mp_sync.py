"""Ghost v2 (animation sync, `ghost self`, the Link body): tools/mp, tools/aamp.py, tools/mp/ghost_link_pack.py.

Made-up data only: no game, no game files. The pack builder runs on small made-up packs that have the shape of the
game's (the key names of the format and made-up values). ghost.c's animation-name logic is checked in C by
tests/test_ghost_native.py (the rest by a C harness outside the repo: docs/ghost.md, "What was tested"); here only
what Python and C must agree on.
"""
import asyncio
import contextlib
import io
import math
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import aamp  # noqa: E402
import modman  # noqa: E402
import mp  # noqa: E402
from aamp import (BOOL, BUFFER_BINARY, BUFFER_F32, BUFFER_INT, BUFFER_U32, COLOR, CURVE1, CURVE2, F32, INT,  # noqa: E402
                  QUAT, STRING32, STRING64, STRING256, STRING_REF, U32, VEC2, VEC3, VEC4, Param, PList, PObject,
                  name_hash)
from control import ControlError  # noqa: E402
from mp import bot, client  # noqa: E402
from mp import ghost_ingame_check as gic  # noqa: E402
from mp import ghost_link_pack as glp  # noqa: E402
from wiiu_convert import sarc_build, yaz0_compress  # noqa: E402

GHOST_C = (REPO / "native" / "examples" / "ghost" / "ghost.c").read_text(encoding="utf-8")


def quiet(*_a, **_k):
    pass


def state(x=1.0, z=3.0, t=10.0, **extra):
    return dict({"type": "state", "t": t, "x": x, "y": 2.0, "z": z, "yaw": 45.0, "stamina": 1.0}, **extra)


# ---------------------------------------------------------------- protocol

class ProtocolTests(unittest.TestCase):
    def test_new_fields_kept_when_good(self):
        st = mp.clean_state(state(anim="CutS1", attack=True, weapon="Weapon_Sword_001", shield="", bow="Weapon_Bow_001"))
        self.assertEqual((st["anim"], st["attack"], st["weapon"], st["shield"], st["bow"]),
                         ("CutS1", True, "Weapon_Sword_001", "", "Weapon_Bow_001"))

    def test_bad_ones_dropped_alone(self):
        for bad in ({"anim": ""}, {"anim": "x" * 32}, {"anim": "Cut S1"}, {"anim": 5}, {"anim": "Cut-1"},
                    {"anim": "Wait\n"}, {"attack": 1}, {"attack": "yes"}, {"attack": False}, {"weapon": "x" * 64},
                    {"weapon": "Weapon Sword"}, {"shield": None}, {"bow": ["a"]}, {"weapon": "Wé"}):
            st = mp.clean_state(state(**bad))
            self.assertIsNotNone(st, bad)
            for k in bad:
                self.assertNotIn(k, st, bad)
        self.assertNotIn("anim", mp.clean_state(state()))

    def test_valid_anim(self):
        self.assertEqual(mp.valid_anim("ParashawlGlide"), "ParashawlGlide")
        for bad in ("", None, "a b", "x" * 32, 3, "-"):
            self.assertIsNone(mp.valid_anim(bad))


# ---------------------------------------------------------------- client: the state, the jitter buffer

class SessionTests(unittest.TestCase):
    def test_own_fields_go_into_the_state(self):
        s = client.Session("Me")
        pos = {"x": 1.0, "y": 2.0, "z": 3.0, "stamina": 1.0, "place": "p"}
        msg = s.update_local(pos, {"yaw": 0.0}, 5.0, own={"anim": "Move", "weapon": "Weapon_Sword_001", "shield": ""})
        self.assertEqual((msg["anim"], msg["weapon"], msg["shield"]), ("Move", "Weapon_Sword_001", ""))
        self.assertNotIn("attack", msg)
        msg = s.update_local(pos, None, 5.1, own={"anim": "bad name", "attack": True})
        self.assertNotIn("anim", msg)                     # (checked like a peer's)
        self.assertTrue(msg["attack"])
        self.assertNotIn("anim", s.update_local(pos, None, 5.2))

    def test_names_come_from_the_nearer_sample(self):
        a = mp.clean_state(state(x=0.0, t=1.0, anim="Wait", weapon="W_A"))
        b = mp.clean_state(state(x=1.0, t=2.0, anim="CutS1", attack=True, weapon="W_B"))
        early, late = client.interpolate(a, b, 0.3), client.interpolate(a, b, 0.7)
        self.assertEqual((early["anim"], early["weapon"], early.get("attack")), ("Wait", "W_A", None))
        self.assertEqual((late["anim"], late["weapon"], late["attack"]), ("CutS1", "W_B", True))
        self.assertAlmostEqual(late["x"], 0.7)

    def test_no_news_drops_the_animation(self):
        jb = client.JitterBuffer(delay=0.0)
        jb.push(mp.clean_state(state(t=1.0, anim="CutS1", attack=True, speed=6.0, weapon="W")), recv=1.0)
        held = jb.sample(1.5)
        self.assertEqual((held["anim"], held["speed"]), ("CutS1", 6.0))
        stale = jb.sample(1.0 + client.SPEED_HOLD + 0.1)
        self.assertNotIn("anim", stale)
        self.assertNotIn("attack", stale)
        self.assertEqual((stale["speed"], stale["weapon"]), (0.0, "W"))   # (items stay: still what they hold)

    def stall_stream(self, attack: tuple[float, float], stall: tuple[float, float] | None, *, jitter=0.0, seed=1,
                     until: float = 3.0):
        """20 lines a second (Wait, CutS1 with attack during `attack`), 20 ms on the way plus 0-`jitter` s, nothing
        through during `stall` (then all at once); sample() 20 times a second. Returns (now, anim, attack) shown."""
        import random
        rng = random.Random(seed)
        jb = client.JitterBuffer(delay=0.1)
        lines, prev = [], 0.0
        for i in range(int(until * 20)):
            t = i * 0.05
            cut = attack[0] <= t < attack[1]
            st = mp.clean_state(state(x=0.0, t=t, anim="CutS1" if cut else "Wait", **({"attack": True} if cut else {})))
            a = t + 0.02 + rng.uniform(0.0, jitter)
            if stall and stall[0] <= a < stall[1]:
                a = stall[1]
            prev = max(prev, a)
            lines.append((prev, st))
        shown, li = [], 0
        for j in range(int(until * 20) + 20):
            now = 0.013 + j * 0.05
            while li < len(lines) and lines[li][0] <= now:
                jb.push(lines[li][1], lines[li][0])
                li += 1
            s = jb.sample(now)
            if s is not None:
                shown.append((now, s.get("anim"), s.get("attack")))
        return shown, jb

    def test_attack_hidden_by_a_stall_is_shown(self):
        # the attack (1.1-1.4 s) comes only after the stall (1.0-1.6 s): before the fix, never shown
        shown, jb = self.stall_stream((1.1, 1.4), (1.0, 1.6))
        cuts = [now for now, anim, attack in shown if anim == "CutS1"]
        self.assertTrue(cuts)
        self.assertTrue(all(attack is True for _, anim, attack in shown if anim == "CutS1"))
        self.assertGreaterEqual(min(cuts), 1.6)                      # right after the stall ...
        self.assertLessEqual(max(cuts) - min(cuts), client.LATE_ATTACK_SHOW + 1e-9)   # ... for 0.3 s
        self.assertEqual(jb.late_attacks, 1)
        with mock.patch.object(client, "LATE_ATTACK_SHOW", 0.0):     # (as before the fix: lost)
            shown, _ = self.stall_stream((1.1, 1.4), (1.0, 1.6))
        self.assertFalse([1 for _, anim, _a in shown if anim == "CutS1"])

    def test_attack_not_made_longer(self):
        # no stall, but lines up to 0.15 s late (later than the 0.1 s delay): shown about as long as it lasted
        for seed in range(1, 9):
            shown, jb = self.stall_stream((1.0, 1.3), None, jitter=0.15, seed=seed)
            with mock.patch.object(client, "LATE_ATTACK_SHOW", 0.0):
                before, _ = self.stall_stream((1.0, 1.3), None, jitter=0.15, seed=seed)
            with self.subTest(seed=seed):
                self.assertEqual(shown, before)                       # (exactly as before the fix)
                self.assertTrue([1 for _, anim, _a in shown if anim == "CutS1"])
                self.assertEqual(jb.late_attacks, 0)

    def test_a_stall_inside_an_attack_is_not_shown_twice(self):
        shown, jb = self.stall_stream((1.0, 1.4), (1.2, 1.7))
        self.assertEqual(jb.late_attacks, 0)                         # (shown while held; its late lines are not new)
        runs, last = 0, None
        for _, anim, _a in shown:
            runs += anim == "CutS1" and last != "CutS1"
            last = anim
        self.assertEqual(runs, 1)

    def test_views_carry_the_names(self):
        s = client.Session("Me", delay=0.0)
        s.handle({"type": "welcome", "id": 9, "peers": []}, now=0.0)
        s.handle(dict(state(t=1.0, anim="Jump", bow="Weapon_Bow_001"), id=1, name="Bo"), now=10.0)
        v = s.views(10.0)[0]
        self.assertEqual((v["anim"], v["bow"]), ("Jump", "Weapon_Bow_001"))


# ---------------------------------------------------------------- client: Ghost with --ghost-sync

def sync_session(*peers):
    """A session with peers (name, x, z, extra fields) seen at 10.0, and the local player."""
    s = client.Session("Me", delay=0.0)
    s.handle({"type": "welcome", "id": 9, "peers": []}, now=0.0)
    for i, (name, x, z, extra) in enumerate(peers, start=1):
        s.handle(dict(state(x=x, z=z, t=1.0), id=i, name=name, **extra), now=10.0)
    s.update_local({"x": 0.0, "y": 2.0, "z": 0.0}, {"yaw": 0.0}, now=10.0)
    return s


NEW_MOD = {"state": "live", "last": "", "takes_speed": True, "takes_anim": True, "has_self": True, "version": "0.5.0"}
OLD_MOD = {"state": "live", "last": "", "takes_speed": True}
SELF = {"active": True, "found": True, "anim": "CutS1", "attack": True, "equip_ok": True, "weapon": "Weapon_Sword_001",
        "shield": "", "bow": "?"}


class GhostSyncTests(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.g = client.Ghost(log=self.log.append, sync=True)

    def tick(self, s, now, status=None, me=None, conn=1):
        reqs = self.g.requests(s, now, conn)
        replies = []
        for r in reqs:
            if r == ["ghost"]:
                replies.append(dict(status or NEW_MOD))
            elif r == ["ghost", "self"]:
                replies.append(me if me is not None else dict(SELF))
            else:
                replies.append({})
        self.g.replies(replies)
        return reqs

    def test_sync_with_a_new_mod(self):
        s = sync_session(("Bo", 3.0, 0.0, {"anim": "CutS2", "speed": 0.0, "weapon": "Weapon_Sword_002"}))
        first = self.tick(s, 10.0)
        self.assertNotIn(["ghost", "self"], first)        # (the mod's abilities aren't known yet)
        self.assertEqual(len(next(r for r in first if r[1:2] == ["pos"])), 6)
        reqs = self.tick(s, 10.05)
        self.assertEqual(reqs[0], ["ghost", "self"])
        self.assertEqual(next(r for r in reqs if r[1:2] == ["pos"])[6:], ["0.00", "CutS2"])
        self.assertEqual(self.g.own_state(), {"anim": "CutS1", "attack": True, "weapon": "Weapon_Sword_001",
                                              "shield": ""})                      # ("?" dropped)
        self.assertIn("Ghost: Bo has weapon Weapon_Sword_002 (start the client with --ghost-items to show them on the ghost).",
                      self.log)
        n = len(self.log)
        self.tick(s, 10.1)
        self.assertEqual(len(self.log), n)                # (said once)
        self.assertIn("you: CutS1", self.g.status_text())

    def test_no_link_no_own_state(self):
        s = sync_session(("Bo", 3.0, 0.0, {}))
        self.tick(s, 10.0)
        for me in ({"active": False, "found": False}, {"active": True, "found": False, "anim": "Wait"},
                   ControlError("No world loaded"), {"active": True, "found": True, "anim": "x y", "equip_ok": False,
                                                    "weapon": "Weapon_Sword_001"}):
            self.tick(s, 10.05, me=me)
            self.assertEqual(self.g.own_state(), {}, me)
        self.g.replies(None)
        self.assertEqual(self.g.own, {})

    def test_peer_without_names_and_no_speed(self):
        s = sync_session(("Bo", 3.0, 0.0, {}))
        self.tick(s, 10.0)
        pos = next(r for r in self.tick(s, 10.05) if r[1:2] == ["pos"])
        self.assertEqual(len(pos), 7)                     # (speed worked out by the receiver; no name)
        s2 = sync_session(("Bo", 3.0, 0.0, {"anim": "Jump"}))
        g = client.Ghost(log=quiet, sync=True)
        g.requests(s2, 10.0, 1)
        g.replies([{}, {}, dict(NEW_MOD)])
        with mock.patch.object(client.JitterBuffer, "sample", lambda self, now: {"x": 3.0, "y": 2.0, "z": 0.0,
                                                                                 "anim": "Jump"}):
            pos = next(r for r in g.requests(s2, 10.05, 1) if r[1:2] == ["pos"])
        self.assertEqual(pos[6:], ["-1", "Jump"])         # (a name with no speed: speed -1, as ghost.c takes it)

    def test_older_mod(self):
        s = sync_session(("Bo", 3.0, 0.0, {"anim": "CutS2", "speed": 0.0}))
        self.tick(s, 10.0, status=OLD_MOD)
        reqs = self.tick(s, 10.05, status=OLD_MOD)
        self.assertNotIn(["ghost", "self"], reqs)
        self.assertEqual(len(next(r for r in reqs if r[1:2] == ["pos"])), 7)   # (speed, no name)
        self.assertEqual(sum("older than 0.5.0" in line for line in self.log), 1)
        self.assertIn("no animation sync", self.g.status_text())

    def test_mod_refuses_the_name_or_self(self):
        s = sync_session(("Bo", 3.0, 0.0, {"anim": "CutS2", "speed": 0.0}))
        self.tick(s, 10.0)
        reqs = self.g.requests(s, 10.05, 1)
        self.g.replies([ControlError("usage: ghost [...]") if r[1:2] in (["pos"], ["self"]) else {} for r in reqs])
        self.assertFalse(self.g.takes_anim)
        self.assertFalse(self.g.has_self)
        reqs = self.g.requests(s, 10.1, 1)
        self.assertEqual(len(next(r for r in reqs if r[1:2] == ["pos"])), 7)
        self.assertNotIn(["ghost", "self"], reqs)

    def test_new_connection_forgets_what_the_mod_could(self):
        s = sync_session(("Bo", 3.0, 0.0, {"anim": "CutS2", "speed": 0.0}))
        self.tick(s, 10.0)
        self.tick(s, 10.05)
        self.assertTrue(self.g.own)
        reqs = self.g.requests(s, 10.1, 2)
        self.assertEqual(self.g.own, {})
        self.assertNotIn(["ghost", "self"], reqs)
        self.assertIn(["ghost"], reqs)

    def test_status_asked_before_anyone_is_shown(self):
        s = sync_session()
        self.assertEqual(self.g.requests(s, 10.0, 1), [["ghost"]])
        self.g.replies([dict(NEW_MOD)])
        self.assertEqual(self.g.requests(s, 10.05, 1), [["ghost", "self"]])
        self.g.replies([dict(SELF)])
        self.assertEqual(self.g.own_state()["anim"], "CutS1")
        plain = client.Ghost(log=quiet)                   # (without sync: nothing, as before)
        self.assertEqual(plain.requests(s, 10.0, 1), [])

    def test_weapon_swings(self):
        self.assertEqual(client.weapon_anim("CutS1", "Weapon_Lsword_001"), "CutS1_Lsword")
        self.assertEqual(client.weapon_anim("CutSF", "Weapon_Lsword_036"), "CutSF_Lsword")
        self.assertEqual(client.weapon_anim("CutS2", "Weapon_Spear_004"), "CutS2_Spear")
        self.assertEqual(client.weapon_anim("CutS2", "Weapon_Lsword_001"), "CutS2")    # (no such swing: as it is)
        self.assertEqual(client.weapon_anim("CutS1", "Weapon_Sword_001"), "CutS1")
        self.assertEqual(client.weapon_anim("Jump", "Weapon_Spear_001"), "Jump")
        self.assertEqual(client.weapon_anim("CutS1", None), "CutS1")
        self.assertIsNone(client.weapon_anim(None, "Weapon_Spear_001"))
        s = sync_session(("Bo", 3.0, 0.0, {"anim": "CutS1", "speed": 0.0, "weapon": "Weapon_Spear_002"}))
        self.tick(s, 10.0)
        pos = next(r for r in self.tick(s, 10.05) if r[1:2] == ["pos"])
        self.assertEqual(pos[6:], ["0.00", "CutS1_Spear"])

    def test_without_sync_nothing_changes(self):
        g = client.Ghost(log=quiet)
        s = sync_session(("Bo", 3.0, 0.0, {"anim": "CutS2", "speed": 0.0, "weapon": "W"}))
        g.requests(s, 10.0, 1)
        g.replies([{}, {}, dict(NEW_MOD)])
        reqs = g.requests(s, 10.05, 1)
        self.assertEqual(len(next(r for r in reqs if r[1:2] == ["pos"])), 7)
        self.assertNotIn(["ghost", "self"], reqs)
        self.assertEqual(g.own_state(), {})


class RunnerSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_own_animation_reaches_the_relay(self):
        class Game:
            def batch(self, requests, *, errors=False):
                out = []
                for r in requests:
                    if r[0] == "pos":
                        out.append({"x": 1.0, "y": 2.0, "z": 3.0, "stamina": 1.0, "place": "p"})
                    elif r[0] == "camera":
                        out.append({"yaw": 0.0})
                    elif r == ["ghost"]:
                        out.append(dict(NEW_MOD))
                    elif r == ["ghost", "self"]:
                        out.append(dict(SELF, anim="Move", attack=False))
                    else:
                        out.append(ControlError("Unknown command"))
                return out

            def close(self):
                pass
        ghost = client.Ghost(log=quiet, sync=True)
        runner = client.Runner(("127.0.0.1", 1), "Me", control_factory=Game, log=quiet, ghost=ghost)
        sent = []
        runner.relay.send = lambda m: sent.append(m) or True
        for _ in range(4):
            await runner.tick()
        # (1: no game connection yet when the requests were made; 2: a new connection: the mod is asked what it can
        # do; 3 on: `ghost self`)
        self.assertEqual([m.get("anim") for m in sent], [None, None, "Move", "Move"])
        self.assertEqual(sent[-1]["weapon"], "Weapon_Sword_001")
        self.assertNotIn("attack", sent[-1])


class ClientOptionTests(unittest.TestCase):
    def made(self, args):
        made = []

        async def ran(runner):
            made.append(runner.ghost)
        with mock.patch.object(client.Runner, "run", ran), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(client.main(["--no-ui", *args]), 0)
        return made[-1], out.getvalue()

    def test_sync_and_link(self):
        g, _ = self.made(["--ghost-sync"])
        self.assertEqual((g.sync, g.actor), (True, None))
        g, text = self.made(["--ghost-link", "--ghost-peer", "Bo"])
        self.assertEqual((g.sync, g.actor, g.peer), (True, client.GHOST_LINK_ACTOR, "Bo"))
        self.assertIn("Link body", text)
        g, _ = self.made(["--ghost"])
        self.assertFalse(g.sync)
        with mock.patch.object(client.Runner, "run", lambda r: None), contextlib.redirect_stderr(io.StringIO()) as err, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                client.main(["--no-ui", "--ghost-link", "--ghost-actor", "Foo"])
        self.assertIn("use one", err.getvalue())


# ---------------------------------------------------------------- bot

class BotTests(unittest.TestCase):
    def test_anim_script(self):
        a = bot.AnimScript.parse("Wait@0:2, Move@6:3,CutS1:0.5")
        self.assertEqual(a.steps, [("Wait", 0.0, 2.0), ("Move", 6.0, 3.0), ("CutS1", None, 0.5)])
        self.assertEqual([a.at(t) for t in (0.0, 2.1, 5.2, 5.6)],
                         [("Wait", 0.0), ("Move", 6.0), ("CutS1", None), ("Wait", 0.0)])
        self.assertEqual(bot.AnimScript.parse("Jump").steps, [("Jump", None, 1.0)])
        for bad in ("", "Cut S1", "Wait:0", "Wait:x", "Wait@99:1", "Wait@-1:1", ",,"):
            with self.assertRaises(ValueError, msg=bad):
                bot.AnimScript.parse(bad)
        self.assertTrue(bot.is_attack("CutJumpSt") and bot.is_attack("BowShoot"))
        self.assertFalse(bot.is_attack("Move"))

    def test_states_carry_names_and_follow_the_speeds(self):
        sent = []

        class Link:
            def __init__(self, *a, **k):
                self.connected, self.room = True, "default"

            async def run(self, stop):
                await stop.wait()

            def send(self, msg):
                sent.append(msg)
                return True
        clock = [100.0]

        async def fake_sleep(stop, secs):
            clock[0] += secs
            if clock[0] >= 107.0:
                stop.set()
        script = bot.AnimScript.parse("Wait@0:2,Move@4:3,CutS1@0:2")
        with mock.patch.object(bot, "RelayLink", Link), mock.patch.object(bot, "_sleep", fake_sleep):
            asyncio.run(bot.run_bot(("x", 1), center=(0.0, 0.0, 0.0), anims=script, weapon="Weapon_Sword_001",
                                    clock=lambda: clock[0], log=quiet))
        names = [m["anim"] for m in sent]
        self.assertEqual(names[0], "Wait")
        self.assertIn("Move", names)
        self.assertEqual(names[-1], "CutS1")
        self.assertTrue(all(m.get("attack") is (True if m["anim"] == "CutS1" else None) for m in sent))
        self.assertTrue(all(m["weapon"] == "Weapon_Sword_001" for m in sent))
        by = {}
        for m in sent:
            by.setdefault(m["anim"], []).append(m["speed"])
        self.assertEqual(max(by["Wait"]), 0.0)                       # (stood still)
        self.assertAlmostEqual(by["Move"][-1], 4.0, delta=0.3)
        self.assertLess(by["CutS1"][-1], 0.3)
        self.assertTrue(all(mp.clean_state(m) is not None for m in sent))


# ---------------------------------------------------------------- what Python and C must agree on

class AgreementTests(unittest.TestCase):
    def define(self, name):
        m = re.search(rf"#define {name} (.+?)(?:\s*//|$)", GHOST_C, re.M)
        return m.group(1).strip()

    def test_link_actor_name(self):
        default = self.define("DEFAULT_ACTOR").strip('"')
        self.assertEqual(self.define("LINK_ACTOR"), "DEFAULT_ACTOR")
        self.assertEqual(client.GHOST_LINK_ACTOR, default)
        self.assertEqual(glp.LINK_ACTOR, default)

    def test_version(self):
        toml = (REPO / "native" / "examples" / "ghost" / "mod.toml").read_text(encoding="utf-8")
        version = re.search(r'^version = "([^"]+)"', toml, re.M).group(1)
        self.assertEqual(self.define("GHOST_VERSION").strip('"'), version)
        self.assertEqual(gic.WANT_VERSION, version)

    def test_names_fit_both_sides(self):
        self.assertIn("strlen(anim) > 31", GHOST_C)        # (ghost.c refuses longer names; so does valid_anim)
        self.assertIsNone(mp.valid_anim("x" * 32))
        for name, _clip, _f in glp.ANIMS:
            self.assertEqual(mp.valid_anim(name), name)
        self.assertTrue(set(gic.LINK_NAMES) <= {n for n, _c, _f in glp.ANIMS})
        for step in gic.BOT_SCRIPT.split(","):
            bot.AnimScript.parse(step)
        self.assertIn('"CutS1"', GHOST_C)                  # (the stand-in for an attack the actor lacks)
        self.assertIn("CutS1", {n for n, _c, _f in glp.ANIMS})

    def test_pack_names(self):
        """Review 2026-11-07: Guard and Jump_Run are not names the player plays (checked against his list): dropped.
        The two-handed and spear swings are ours, reached through the client's weapon pick."""
        names = {n for n, _c, _f in glp.ANIMS}
        self.assertFalse(names & {"Guard", "Jump_Run"})
        swings = {a + suffix for _p, suffix, anims in client.WEAPON_ANIMS for a in anims}
        self.assertEqual(swings, {"CutS1_Lsword", "CutSF_Lsword", "CutS1_Spear", "CutS2_Spear", "CutSF_Spear"})
        self.assertLessEqual(swings, names)                          # (each pick has a clip in the pack)
        self.assertEqual({n for n in names if "_" in n}, swings)     # (and the pack has no other such name)
        doc = (REPO / "docs" / "ghost.md").read_text(encoding="utf-8")
        self.assertNotIn("The names are the player's own", doc)
        self.assertIn("`CutS1_Lsword`", doc)

    def test_mod_replies_the_client_reads(self):
        for key in ('"takes_anim", true', '"has_self", true', '"version", GHOST_VERSION', '"remote_missing"'):
            self.assertIn(key, GHOST_C)
        for key in ("active", "found", "anim", "attack", "equip_ok", "weapon", "shield", "bow", "shape"):
            self.assertRegex(GHOST_C, rf'api->reply_\w+\(r, "{key}"')


# ---------------------------------------------------------------- AAMP

def sample_root():
    o = PObject()
    for t, v in ((BOOL, True), (F32, 1.5), (INT, -7), (U32, 0xFFFFFFFF), (VEC2, [1.0, 2.0]), (VEC3, [1.0, 2.0, 3.0]),
                 (VEC4, [1.0, 2.0, 3.0, 4.0]), (COLOR, [0.5, 0.5, 0.5, 1.0]), (QUAT, [0.0, 0.0, 0.0, 1.0]),
                 (STRING32, "short"), (STRING64, "a longer text of letters"), (STRING256, "x" * 200),
                 (STRING_REF, "Löwe"), (CURVE1, [(1, 2, [float(i) for i in range(30)])]),
                 (CURVE2, [(0, 0, [0.0] * 30), (3, 4, [1.0] * 30)]), (BUFFER_INT, [1, -2, 3]),
                 (BUFFER_F32, [0.25, 0.5]), (BUFFER_U32, [7]), (BUFFER_BINARY, b"\x01\x02\x03")):
        o.set(f"p{t}", t, v)
    same = PObject()
    same.set("A", STRING64, "short")                         # (the same text and number again: stored once)
    same.set("B", F32, 1.5)
    inner = PList(objects={name_hash("Same"): same})
    return PList(lists={name_hash("Inner"): inner, name_hash("Empty"): PList()}, objects={name_hash("All"): o})


class AampTests(unittest.TestCase):
    def test_round_trip_every_type(self):
        root = sample_root()
        data = aamp.dump(root)
        self.assertEqual(aamp.load(data), root)
        self.assertEqual(aamp.type_name(data), "xml")
        self.assertEqual(aamp.dump(aamp.load(data)), data)

    def test_header_and_layout(self):
        params = PObject()
        params.set("TypeIndex", INT, 67)
        params.set("FileName", STRING64, "Clip_A")
        el = PList(objects={name_hash("Parameters"): params})
        data = aamp.dump(PList(lists={name_hash("Elements"): PList(lists={name_hash("Element0"): el})}))
        magic, ver, flags, size, pio, off, nl, no, np_, dsz, ssz, unk = struct.unpack_from("<4s11I", data)
        self.assertEqual((magic, ver, flags, size, pio, off, nl, no, np_, dsz, ssz, unk),
                         (b"AAMP", 2, 3, len(data), 0, 4, 3, 1, 2, 4, 8, 0))
        self.assertEqual(data[0x30:0x34], b"xml\0")
        root = struct.unpack_from("<IHHHH", data, 0x34)
        self.assertEqual(root, (aamp.ROOT, 3, 1, 9, 0))      # (no objects: their offset still points past the lists)
        self.assertEqual(len(data), 0x7C)                    # (lists, 1 object, 2 parameters, 4 + 8 bytes of values)

    def test_same_values_stored_once(self):
        root = sample_root()
        data = aamp.dump(root)
        self.assertEqual(data.count(b"short\0"), 1)
        self.assertEqual(data.count(struct.pack("<f", 1.5)), 1)

    def test_bad_files(self):
        good = aamp.dump(sample_root())
        for bad, why in ((b"BAMP" + good[4:], "not an AAMP"), (good[:4] + b"\x01" + good[5:], "version"),
                         (good[:8] + b"\x02" + good[9:], "big-endian"), (good[:0x40], "cut short"),
                         (good[:0x34] + b"\0\0\0\0" + good[0x38:], "param_root")):
            with self.assertRaisesRegex(aamp.AampError, why):
                aamp.load(bad)
        o = PObject()
        o.set("T", STRING32, "x" * 32)
        with self.assertRaisesRegex(aamp.AampError, "too long"):
            aamp.dump(PList(objects={1: o}))
        o.set("T", 99, 0)
        with self.assertRaises(aamp.AampError):
            aamp.dump(PList(objects={1: o}))

    def test_param_equality(self):
        self.assertEqual(Param(F32, 0.1), Param(F32, struct.unpack("<f", struct.pack("<f", 0.1))[0]))
        self.assertNotEqual(Param(F32, 0.1), Param(INT, 0))
        self.assertTrue(math.isclose(aamp.load(aamp.dump(sample_root())).objects[name_hash("All")][name_hash("p1")]
                                     .value, 1.5))


# ---------------------------------------------------------------- the Link body pack (made-up packs)

def obj(**params):
    o = PObject()
    for k, (t, v) in params.items():
        o.set(k, t, v)
    return o


def link_target(**users):
    base = {k: "Dummy" for k in ("AIProgramUser", "ASUser", "ModelUser", "PhysicsUser", "UMiiUser", "AnimationInfo",
                                 "GParamUser")}
    base.update(users)                                   # (None: the actor link lacks that user)
    return aamp.dump(PList(objects={name_hash("LinkTarget"): obj(**{k: (STRING_REF, v) for k, v in base.items()
                                                                    if v is not None}),
                                    name_hash("Tags"): obj(Tag0=(STRING_REF, "MadeUpTag"))}))


def anm_target(bone):
    t0 = PList(objects={name_hash("Base"): obj(NumASSlot=(INT, 1), TargetType=(INT, 0))},
               lists={name_hash("Partial"): PList(objects={1: obj(Bone=(STRING32, bone))})})
    return PList(lists={name_hash("AnmTarget_0"): t0})


def model_list(folder, bone):
    md0 = PList(objects={name_hash("Base"): obj(Folder=(STRING64, folder))},
                lists={name_hash("Unit"): PList(objects={name_hash("Unit_0"): obj(UnitName=(STRING64, folder))})})
    return aamp.dump(PList(lists={name_hash("ModelData"): PList(lists={name_hash("ModelData_0"): md0}),
                                  name_hash("AnmTarget"): anm_target(bone)},
                           objects={name_hash("ControllerInfo"): obj(Mode=(STRING64, "Wait"))}))


def made_up_packs(root: Path, *, link_in_titlebg=False, sheikah_users=None):
    """Two made-up packs with the shape of the game's: the stand-in's and the pause-menu Link's."""
    users = dict(AIProgramUser="DemoDummy", ASUser="MadeUp_AS", ModelUser="MadeUp_Model", PhysicsUser="MadeUp_Phys",
                 UMiiUser="MadeUp_Mii", AnimationInfo="MadeUp_Info")
    users.update(sheikah_users or {})
    sheikah = {f"Actor/ActorLink/{glp.LINK_ACTOR}.bxml": link_target(**users),
               "Actor/AIProgram/DemoDummy.baiprog": aamp.dump(PList(objects={1: obj(X=(INT, 1))})),
               "Actor/ModelList/MadeUp_Model.bmodellist": model_list("MadeUpBody", "BoneFromStandIn"),
               "Actor/ASList/MadeUp_AS.baslist": aamp.dump(PList()), "Actor/AS/MadeUp_Wait.bas": b"made up",
               "Actor/Physics/MadeUp_Phys.bphysics": b"made up", "Actor/UMii/MadeUp_Mii.bumii": b"made up",
               "Actor/AnimationInfo/MadeUp_Info.baniminfo": b"made up",
               "Physics/SupportBone/MadeUp/MadeUp.bphyssb": b"made up"}
    add = PList(objects={name_hash("AddRes_0"): obj(Anim=(STRING64, "MadeUp_Animation"))})
    link = {f"Actor/ActorLink/{glp.DONOR_LINK}.bxml": link_target(ModelUser="MadeUp_Link", ASUser="MadeUp_LinkAS"),
            "Actor/ModelList/MadeUp_Link.bmodellist": model_list("Link", "BoneFromLink"),
            "Actor/ASList/MadeUp_LinkAS.baslist": aamp.dump(PList(lists={name_hash("AddReses"): add,
                                                                         name_hash("ASDefines"): PList()}))}
    packs = root / "Actor" / "Pack"
    packs.mkdir(parents=True)
    pack = lambda files: yaz0_compress(sarc_build(sorted(files.items())))   # noqa: E731
    (packs / f"{glp.LINK_ACTOR}.sbactorpack").write_bytes(pack(sheikah))
    if link_in_titlebg:
        (root / "Pack").mkdir()
        (root / "Pack" / "TitleBG.pack").write_bytes(sarc_build([(f"Actor/Pack/{glp.DONOR_LINK}.sbactorpack",
                                                                   pack(link)), ("Other/file.txt", b"x")]))
    else:
        (packs / f"{glp.DONOR_LINK}.sbactorpack").write_bytes(pack(link))


class LinkPackTests(unittest.TestCase):
    def build(self, **kw):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        romfs, out = Path(tmp.name, "romfs"), Path(tmp.name, "mod")
        made_up_packs(romfs, **{k: v for k, v in kw.items() if k != "slots"})
        r = glp.build(romfs, out, kw.get("slots", "sheikah"), say=quiet)
        return romfs, out, r

    def test_build_and_read_back(self):
        _, out, r = self.build()
        self.assertLess(r["unpacked"], glp.SIZE_LIMIT)
        c = glp.check(out)
        self.assertEqual(c["users"], {"ModelUser": glp.OURS, "ASUser": glp.OURS, "PhysicsUser": "Dummy",
                                      "AIProgramUser": "DemoDummy"})
        self.assertEqual(c["names"], {n: clip for n, clip, _f in glp.ANIMS})
        files = glp.read_pack((out / "romfs" / "Actor" / "Pack" / f"{glp.LINK_ACTOR}.sbactorpack").read_bytes())
        self.assertNotIn("Actor/Physics/MadeUp_Phys.bphysics", files)       # (dropped with the users)
        self.assertNotIn("Physics/SupportBone/MadeUp/MadeUp.bphyssb", files)
        self.assertNotIn("Actor/AS/MadeUp_Wait.bas", files)
        self.assertIn("Actor/AIProgram/DemoDummy.baiprog", files)
        lt = aamp.load(files[f"Actor/ActorLink/{glp.LINK_ACTOR}.bxml"]).object("LinkTarget")
        self.assertEqual((lt.get_value("UMiiUser"), lt.get_value("AnimationInfo"), lt.get_value("GParamUser")),
                         ("Dummy", "Dummy", "Dummy"))
        self.assertEqual(lt[name_hash("ModelUser")].type, STRING_REF)       # (types kept)
        ml = aamp.load(files[f"Actor/ModelList/{glp.OURS}.bmodellist"])
        bone = ml.list("AnmTarget").list("AnmTarget_0").list("Partial").objects[1].get_value("Bone")
        self.assertEqual(bone, "BoneFromStandIn")                           # (the stand-in's animation slots)
        self.assertEqual(ml.list("ModelData").list("ModelData_0").object("Base").get_value("Folder"), "Link")
        asl = aamp.load(files[f"Actor/ASList/{glp.OURS}.baslist"])
        self.assertEqual(asl.list("AddReses").object("AddRes_0").get_value("Anim"), "MadeUp_Animation")
        cf = asl.list("CFDefines")
        self.assertEqual(len(cf.lists), len(glp.ANIMS))
        walk = next(l for l in cf.lists.values() if l.object("CFPre").get_value("Name") == "Walk")
        posts = {o.get_value("Name"): o.get_value("Frame") for o in walk.list("CFPosts").objects.values()}
        self.assertEqual(posts, {"": 6.0, "Wait": 10.0, "Run": 12.0, "RunGear2": 8.0})
        # sorted by name, as in the game's own lists: the game binary-searches them (unsorted, only ClimbMove played)
        names = [o.get_value("Name") for o in asl.list("ASDefines").objects.values()]
        self.assertEqual(names, sorted(names, key=str.encode))
        self.assertEqual(names[0], "BowCharge")
        self.assertEqual(list(asl.list("ASDefines").objects), [name_hash(f"ASDefine_{i}") for i in range(len(names))])
        pres = [l.object("CFPre").get_value("Name") for l in cf.lists.values()]
        self.assertEqual(pres, sorted(pres, key=str.encode))
        self.assertEqual(list(posts), sorted(posts, key=str.encode))
        bas = aamp.load(files[f"Actor/AS/{glp.OURS}_CutS1.bas"]).list("Elements").list("Element0").object("Parameters")
        self.assertEqual((bas.get_value("TypeIndex"), bas.get_value("FileName")), (67, "Sword_Attack_S1"))
        man, errs = modman.parse_manifest((out / "mod.toml").read_text(encoding="utf-8"))
        self.assertEqual(errs, [])
        self.assertEqual(man.game_versions, ["1.9.0"])

    def test_pause_slots_and_titlebg(self):
        _, out, _ = self.build(slots="pause", link_in_titlebg=True)
        files = glp.read_pack((out / "romfs" / "Actor" / "Pack" / f"{glp.LINK_ACTOR}.sbactorpack").read_bytes())
        ml = aamp.load(files[f"Actor/ModelList/{glp.OURS}.bmodellist"])
        bone = ml.list("AnmTarget").list("AnmTarget_0").list("Partial").objects[1].get_value("Bone")
        self.assertEqual(bone, "BoneFromLink")

    def test_refusals(self):
        romfs, out, _ = self.build()
        with self.assertRaisesRegex(glp.PackError, "folder of your own"):
            glp.build(romfs, romfs / "x", say=quiet)
        with self.assertRaisesRegex(glp.PackError, "already ours"):          # (pointed at a built mod)
            glp.build(out / "romfs", out.parent / "again", say=quiet)
        with self.assertRaisesRegex(glp.PackError, "No pack"):
            glp.build(out.parent / "nothing", out.parent / "x", say=quiet)
        with self.assertRaisesRegex(glp.PackError, "--slots"):
            glp.build_pack({}, {}, "both")
        with tempfile.TemporaryDirectory() as d:
            made_up_packs(Path(d), sheikah_users={"UMiiUser": None})
            with self.assertRaisesRegex(glp.PackError, "game file|UMiiUser"):
                glp.build(Path(d), Path(d).parent / "glp-out-never", say=quiet)

    def test_main(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(glp.main(["names"]), 0)
        self.assertIn("Sword_Attack_S1", out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(glp.main(["check", str(REPO / "no-such-folder")]), 1)
        self.assertIn("no Dm_Npc", err.getvalue())


# ---------------------------------------------------------------- the in-game check's judging (not run: no game)

class IngameCheckJudgeTests(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(gic.judge_shape("2,2,1,3,1")[0], "PASS")
        self.assertEqual(gic.judge_shape("2,2,1")[0], "FAIL")
        self.assertEqual(gic.judge_shape("")[0], "FAIL")
        self.assertEqual(gic.judge_shape("a,b")[0], "FAIL")

    def test_moves(self):
        good = {"walk": ({"Move"}, 2.0), "run": ({"Move", "Wait"}, 5.0), "sprint": ({"Move"}, 7.5)}
        self.assertEqual(gic.judge_moves(good)[0], "PASS")
        self.assertEqual(gic.judge_moves(dict(good, sprint=({"Move"}, 4.0)))[0], "FAIL")
        self.assertEqual(gic.judge_moves(dict(good, walk=({"Wait"}, 2.0)))[0], "FAIL")

    def test_items_and_frames(self):
        self.assertTrue(gic.judge_item("") and gic.judge_item("Weapon_Sword_001"))
        self.assertFalse(gic.judge_item("?") or gic.judge_item(None))
        self.assertEqual(gic.judge_frames(50.0, 48.0)[0], "PASS")
        self.assertEqual(gic.judge_frames(50.0, 40.0)[0], "FAIL")
        self.assertEqual(gic.judge_frames(None, 40.0)[0], "SKIP")
        # S8 averages two pairs (one pair alone read 30.0 -> 25.2 on a busy PC)
        self.assertAlmostEqual(gic.mean_fps([30.0, None, 29.0]), 29.5)
        self.assertIsNone(gic.mean_fps([None, None]))
        self.assertEqual(gic.fmt_fps([30.0, None]), "30.0 and ?")
        self.assertEqual(gic.judge_frames(gic.mean_fps([30.0, 29.8]), gic.mean_fps([25.2, 29.6]))[0], "PASS")

    def test_replay(self):
        names = ["Wait", "Move", "CutS1", "Jump"]
        link = [("Wait", "Wait"), ("Move", "Walk"), ("CutS1", "Wait"), ("CutS1", "CutS1"), ("Jump", "Jump")]
        self.assertEqual(gic.judge_replay(link, names, True, "")[0], "PASS")
        self.assertEqual(gic.judge_replay(link[:-1], names, True, "")[0], "FAIL")
        sheikah = [("Wait", "Wait"), ("Move", "Run"), ("CutS1", "Wait"), ("Jump", "Run")]
        self.assertEqual(gic.judge_replay(sheikah, names, False, "CutS1,Jump")[0], "PASS")
        self.assertEqual(gic.judge_replay(sheikah, names, False, "CutS1")[0], "FAIL")
        self.assertEqual(gic.judge_replay(sheikah + [("Jump", "Jump")], names, False, "CutS1,Jump")[0], "FAIL")
        self.assertEqual(gic.judge_replay([("", "Wait")], names, False, "")[0], "FAIL")

    def test_own(self):
        self.assertEqual(gic.judge_own(["Wait", "Wait", "Move"])[0], "PASS")
        self.assertEqual(gic.judge_own(["Wait"])[0], "FAIL")

    def test_link_spawn_tells_the_body_from_the_sheikah_man(self):
        """Review 2026-11-07: L2 passed with the Sheikah man too (he has Wait). Now it also needs CutS1, which he
        hasn't."""
        live = {"state": "live", "as_ok": True, "as": "Wait"}
        self.assertEqual(gic.judge_link_spawn(live, dict(live, **{"as": "CutS1"}))[0], "PASS")
        status, text = gic.judge_link_spawn(live, live)              # (the Sheikah man: CutS1 doesn't play)
        self.assertEqual(status, "FAIL")
        self.assertIn("Sheikah man", text)
        self.assertEqual(gic.judge_link_spawn(dict(live, state="pending"), dict(live, **{"as": "CutS1"}))[0], "FAIL")
        self.assertEqual(gic.judge_link_spawn(dict(live, as_ok=False), {"as": "CutS1"})[0], "FAIL")
        src = Path(gic.__file__).read_text(encoding="utf-8")
        self.assertIn('c.cmd("ghost", "anim", "CutS1")', src)       # (check_link asks for it)
        self.assertIn("judge_link_spawn(st, cut)", src)


class ReviewDocTests(unittest.TestCase):
    """Review of ghost v2 (2026-11-07): text that must stay right."""

    def test_link_body_is_not_deployed_for_good_before_the_check(self):
        doc = (REPO / "docs" / "ghost.md").read_text(encoding="utf-8")
        self.assertNotIn("modman add out\\ghost-link-mod` works", doc)
        # (the check passed in the game on 2026-11-07, tenth round: the doc says so, and how an old unsorted build
        # shows: no animation)
        self.assertIn("ghost_ingame_check.py --link", doc)
        self.assertIn("Tenth round", doc)
        self.assertIn("sorted by name", doc)
        self.assertIn("Build it again", doc)
        self.assertIn("ghost_ingame_check.py --link", glp.NEXT)
        self.assertIn("ghost_ingame_check.py --link", glp.MOD_TOML)
        self.assertIn("ghost_ingame_check.py --link", glp.__doc__)
        self.assertNotIn("or `modman add`", glp.__doc__)
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()) as out:
            made_up_packs(Path(d, "romfs"))
            self.assertEqual(glp.main(["build", "--romfs", str(Path(d, "romfs")), "--out", str(Path(d, "mod"))]), 0)
        self.assertIn("Checked in the game", out.getvalue())

    def test_relay_docstring_lists_every_state_field(self):
        from mp import relay
        for key in ("speed", "anim", "attack", "weapon", "shield", "bow"):
            self.assertIn(key, relay.__doc__.split("Nothing is saved")[0], key)

    def test_no_names_from_the_decomp(self):
        """Our own plain labels, not class or function names from the decompilation projects."""
        names = ("PlayerInfo", "ActorCreator", "BaseProcUnit", "LinkData", "SafeString", "Matrix34f",
                 "requestCreateActor", "deleteLater", "setTransform", "setMtx", "GameScene", "ActorPhysics")
        for path in (REPO / "native" / "examples" / "ghost" / "ghost.c", REPO / "docs" / "ghost.md",
                     REPO / "docs" / "multiplayer.md", REPO / "native" / "examples" / "ghost" / "README.md"):
            text = path.read_text(encoding="utf-8")
            for name in names:
                self.assertNotIn(name, text, f"{name} in {path.name}")

    def test_game_env(self):
        env = gic.game_env(7777, {"SAVAGEWIND_ROMFS_OVERLAY": "X"})
        self.assertEqual((env["SAVAGEWIND_CONTROL"], env["SAVAGEWIND_ROMFS_OVERLAY"]), ("7777", "X"))
        self.assertTrue(env["SWMOD_DIR"].endswith("ghost-mods"))
        self.assertNotIn("SWMOD_REAL_GAME_LIB", env)


if __name__ == "__main__":
    unittest.main()
