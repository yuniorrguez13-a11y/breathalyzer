"""The ghost's held items (0.6.0, --ghost-items): the protocol's `on_back`, the client's `ghost items` requests, the
bot's --items script and the pure judges of the in-game checks (tools/mp/ghost_items_check.py).

Made-up data only: no game, no game files. ghost.c's own item logic is checked in C by tests/test_ghost_native.py.
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import mp  # noqa: E402
from control import ControlError  # noqa: E402
from mp import bot, client  # noqa: E402
from mp import ghost_items_check as gi  # noqa: E402

GHOST_C = (REPO / "native" / "examples" / "ghost" / "ghost.c").read_text(encoding="utf-8")
ITEMS = ("Weapon_Sword_001", "Weapon_Shield_001", "Weapon_Bow_001")


def state(x=1.0, z=3.0, t=1.0, **extra):
    return dict({"type": "state", "t": t, "x": x, "y": 2.0, "z": z, "yaw": 45.0, "stamina": 1.0}, **extra)


def session(**extra):
    """A session with one peer, Bo, 3 m away, seen at 10.0 with these extra state fields."""
    s = client.Session("Me", delay=0.0)
    s.handle({"type": "welcome", "id": 9, "peers": []}, now=0.0)
    s.handle(dict(state(x=3.0, z=0.0), id=1, name="Bo", **extra), now=10.0)
    s.update_local({"x": 0.0, "y": 2.0, "z": 0.0}, {"yaw": 0.0}, now=10.0)
    return s


MOD_060 = {"state": "live", "last": "", "takes_speed": True, "takes_anim": True, "has_self": True, "has_items": True,
           "version": "0.6.1"}
MOD_051 = {"state": "live", "last": "", "takes_speed": True, "takes_anim": True, "has_self": True, "version": "0.5.1"}


# ---------------------------------------------------------------- protocol

class OnBackTests(unittest.TestCase):
    def test_kept_when_good(self):
        for v in range(8):
            self.assertEqual(mp.clean_state(state(on_back=v))["on_back"], v)

    def test_bad_ones_dropped_alone(self):
        for bad in (8, -1, True, False, 1.0, "4", None, [4], 2 ** 40):
            st = mp.clean_state(state(on_back=bad))
            self.assertIsNotNone(st, bad)
            self.assertNotIn("on_back", st, bad)

    def test_a_state_without_it_has_none(self):
        self.assertNotIn("on_back", mp.clean_state(state()))

    def test_it_travels_to_the_views(self):
        v = session(weapon="Weapon_Sword_001", on_back=1).views(10.0)[0]
        self.assertEqual((v["weapon"], v["on_back"]), ("Weapon_Sword_001", 1))

    def test_the_sender_sends_its_own(self):
        s = client.Session("Me")
        msg = s.update_local({"x": 1.0, "y": 2.0, "z": 3.0}, {"yaw": 0.0}, 5.0, own={"weapon": "W_1", "on_back": 5})
        self.assertEqual((msg["weapon"], msg["on_back"]), ("W_1", 5))
        msg = s.update_local({"x": 1.0, "y": 2.0, "z": 3.0}, {"yaw": 0.0}, 5.1, own={"on_back": 9})
        self.assertNotIn("on_back", msg)


# ---------------------------------------------------------------- client

class GhostItemsTests(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.g = client.Ghost(actor=client.GHOST_LINK_ACTOR, log=self.log.append, items=True)

    def tick(self, s, now, mod=MOD_060, conn=1, items_reply=None):
        reqs = self.g.requests(s, now, conn)
        replies = []
        for r in reqs:
            if r == ["ghost"]:
                replies.append(dict(mod))
            elif r[1:2] == ["items"] and items_reply is not None:
                replies.append(items_reply)
            elif r == ["ghost", "self"]:
                replies.append({"active": True, "found": True, "anim": "Wait", "equip_ok": True, "weapon": "W_1",
                                "shield": "", "bow": "", "on_back": 1})
            else:
                replies.append({})
        self.g.replies(replies)
        return reqs

    @staticmethod
    def items_of(reqs):
        return [r for r in reqs if r[1:2] == ["items"]]

    def test_items_imply_sync(self):
        self.assertTrue(self.g.sync)

    def test_sent_only_once_the_mod_says_it_can(self):
        s = session(weapon=ITEMS[0], shield=ITEMS[1], bow=ITEMS[2], on_back=4)
        first = self.tick(s, 10.0)
        self.assertEqual(self.items_of(first), [])                  # (what the mod takes isn't known yet)
        second = self.tick(s, 10.05)
        self.assertEqual(self.items_of(second), [["ghost", "items", *ITEMS, "4"]])

    def test_on_change_and_every_two_seconds(self):
        s = session(weapon=ITEMS[0], shield="", bow=ITEMS[2], on_back=0)
        self.tick(s, 10.0)
        self.assertEqual(self.items_of(self.tick(s, 10.05)), [["ghost", "items", ITEMS[0], "-", ITEMS[2], "0"]])
        self.assertEqual(self.items_of(self.tick(s, 10.1)), [])     # (no change)
        self.assertEqual(len(self.items_of(self.tick(s, 12.1))), 1)  # (the refresh)
        s.handle(dict(state(x=3.0, z=0.0, t=2.0), id=1, name="Bo", weapon="Weapon_Lsword_001", shield="", bow="",
                      on_back=1), now=12.15)
        self.assertEqual(self.items_of(self.tick(s, 12.2)), [["ghost", "items", "Weapon_Lsword_001", "-", "-", "1"]])

    def test_a_new_connection_sends_again(self):
        s = session(weapon=ITEMS[0], on_back=0)
        self.tick(s, 10.0)
        self.tick(s, 10.05)
        self.assertEqual(self.items_of(self.tick(s, 10.1, conn=2)), [])   # (new game: ask what it takes first)
        self.assertEqual(len(self.items_of(self.tick(s, 10.15, conn=2))), 1)

    def test_bad_names_go_as_none(self):
        s = session(weapon="Weapon_Sword_001", shield="", bow="")
        self.tick(s, 10.0)
        self.g.has_items = True
        v = {"weapon": "x" * 64, "shield": "Bad name", "bow": 5, "anim": "Wait"}
        self.assertEqual(client.items_command(v), ["ghost", "items", "-", "-", "-", "4"])

    def test_only_weapons_shields_and_bows_each_in_its_hand(self):
        """0.6.1 (review): the other player's data can't make your game spawn anything else."""
        for bad in ({"weapon": "Enemy_Bokoblin_Junior"}, {"weapon": "GameROMPlayer"}, {"weapon": "Weapon_Shield_001"},
                    {"shield": "Weapon_Sword_001"}, {"bow": "Weapon_Lsword_001"}, {"weapon": "Weapon_Sword_070"},
                    {"weapon": "Weapon_Sword_080"}, {"bow": "Weapon_Bow_071"}, {"weapon": "Weapon_Sword_"}):
            with self.subTest(v=bad):
                self.assertEqual(client.items_command(bad)[2:5], ["-", "-", "-"])
        good = {"weapon": "Weapon_Spear_001", "shield": "Weapon_Shield_002", "bow": "Weapon_Bow_002"}
        self.assertEqual(client.items_command(good)[2:5], ["Weapon_Spear_001", "Weapon_Shield_002", "Weapon_Bow_002"])
        self.assertTrue(client.item_name_ok(0, "Weapon_Lsword_001"))

    def test_a_missing_key_leaves_it_as_it_was(self):
        """0.6.1 (review): a state without item keys (their ghost self didn't answer in time) changes nothing; only
        an explicit "" clears."""
        s = session(weapon=ITEMS[0], shield=ITEMS[1], bow=ITEMS[2], on_back=4)
        self.tick(s, 10.0)
        self.assertEqual(self.items_of(self.tick(s, 10.05)), [["ghost", "items", *ITEMS, "4"]])
        s.handle(dict(state(x=3.0, z=0.0, t=2.0), id=1, name="Bo"), now=10.1)         # (no item keys at all)
        s.handle(dict(state(x=3.0, z=0.0, t=2.1), id=1, name="Bo"), now=10.2)
        self.assertEqual(self.items_of(self.tick(s, 10.3)), [])                       # (no change: nothing sent)
        self.assertEqual(self.g.held({})["weapon"], ITEMS[0])
        s.handle(dict(state(x=3.0, z=0.0, t=2.2), id=1, name="Bo", weapon="", on_back=0), now=10.4)
        s.handle(dict(state(x=3.0, z=0.0, t=2.3), id=1, name="Bo", weapon="", on_back=0), now=10.5)
        self.assertEqual(self.items_of(self.tick(s, 10.6)), [["ghost", "items", "-", ITEMS[1], ITEMS[2], "0"]])

    def test_a_new_player_starts_empty(self):
        s = session(weapon=ITEMS[0], on_back=0)
        self.tick(s, 10.0)
        self.tick(s, 10.05)
        s.handle({"type": "leave", "id": 1}, now=10.1)
        self.tick(s, 10.1)
        s.handle(dict(state(x=3.0, z=0.0, t=3.0), id=2, name="Cy"), now=10.2)     # (Cy sends no items)
        self.assertEqual(self.items_of(self.tick(s, 10.25)), [["ghost", "items", "-", "-", "-", "4"]])

    def test_mask_of_an_older_sender(self):
        self.assertEqual(client.item_mask({}), 4)                         # (the bow on the back)
        self.assertEqual(client.item_mask({"anim": "BowCharge"}), 3)       # (aiming: weapon and shield on the back)
        self.assertEqual(client.item_mask({"anim": "CutS1"}), 4)
        self.assertEqual(client.item_mask({"anim": "BowCharge", "on_back": 0}), 0)   # (its own wins)
        s = session(weapon=ITEMS[0], anim="BowShoot")
        self.tick(s, 10.0)
        self.assertEqual(self.items_of(self.tick(s, 10.05))[0][-1], "3")

    def test_an_old_mod_gets_nothing_new(self):
        s = session(weapon=ITEMS[0], shield=ITEMS[1], bow=ITEMS[2], on_back=4)
        for k in range(5):
            self.assertEqual(self.items_of(self.tick(s, 10.0 + k * 0.5, mod=MOD_051)), [])
        self.assertTrue(any("can't show items" in line for line in self.log))
        self.assertIn("no items (old mod)", self.g.status_text())

    def test_without_the_flag_nothing_new(self):
        g = client.Ghost(actor=client.GHOST_LINK_ACTOR, log=self.log.append, sync=True)
        s = session(weapon=ITEMS[0], on_back=4)
        for k in range(4):
            reqs = g.requests(s, 10.0 + k, 1)
            g.replies([dict(MOD_060) if r == ["ghost"] else {} for r in reqs])
            self.assertEqual(self.items_of(reqs), [])
        self.assertNotIn("on_back", g.own_state())   # (ghost self wasn't answered with it here)

    def test_a_refusal_turns_it_off(self):
        s = session(weapon=ITEMS[0])
        self.tick(s, 10.0)
        self.tick(s, 10.05, items_reply=ControlError("usage: ghost ..."))
        self.assertFalse(self.g.has_items)

    def test_own_on_back_goes_into_the_state(self):
        s = session(weapon=ITEMS[0])
        self.tick(s, 10.0)
        self.tick(s, 10.05)
        self.tick(s, 10.1)
        self.assertEqual(self.g.own_state().get("on_back"), 1)

    def test_nobody_left_then_someone_new(self):
        s = session(weapon=ITEMS[0], on_back=0)
        self.tick(s, 10.0)
        self.tick(s, 10.05)
        s.handle({"type": "leave", "id": 1}, now=10.1)
        reqs = self.tick(s, 10.1)
        self.assertIn(["ghost", "despawn"], reqs)            # (the mod removes its items with it)
        s.handle(dict(state(x=3.0, z=0.0, t=3.0), id=2, name="Cy", weapon=ITEMS[0], on_back=0), now=10.2)
        self.assertEqual(len(self.items_of(self.tick(s, 10.25))), 1)

    def test_the_log_says_they_are_shown(self):
        s = session(weapon=ITEMS[0], shield="", bow="")
        self.tick(s, 10.0)
        self.tick(s, 10.05)
        s.handle(dict(state(x=3.0, z=0.0, t=2.0), id=1, name="Bo", weapon=ITEMS[1], shield="", bow=""), now=10.1)
        self.tick(s, 10.1)
        self.assertTrue(any("the ghost holds them" in line for line in self.log), self.log)


# ---------------------------------------------------------------- bot

class BotItemsTests(unittest.TestCase):
    def test_script(self):
        sc = bot.ItemScript.parse(gi.BOT_ITEMS)
        self.assertEqual(sc.duration, 13.0)
        self.assertEqual(sc.at(0.0), (*ITEMS, 0))
        self.assertEqual(sc.at(6.0), ("Weapon_Lsword_001", "", "Weapon_Bow_001", 1))
        self.assertEqual(sc.at(11.0), ("", "", "", 0))
        self.assertEqual(sc.at(13.5), (*ITEMS, 0))            # (a loop)
        self.assertEqual(bot.ItemScript.parse("A_1/-/-/7").steps, [("A_1", "", "", 7, 1.0)])

    def test_bad_scripts(self):
        for bad in ("", "A/B/C", "A/B/C/8:1", "A/B/C/x:1", "A b/-/-/0:1", "A/-/-/0:0", "A/-/-/0:x", "A/-/-/-1:1",
                    "/".join(["x" * 64, "-", "-", "0"])):
            with self.subTest(script=bad), self.assertRaises(ValueError):
                bot.ItemScript.parse(bad)

    def test_states_carry_them(self):
        import asyncio
        sent = []

        class Link:
            def __init__(self, *a, **k):
                self.connected, self.room = True, "default"

            async def run(self, stop):
                await stop.wait()

            def send(self, msg):
                sent.append(msg)
                if len(sent) >= 3:
                    stop.set()
                return True

        stop = asyncio.Event()
        orig = bot.RelayLink
        bot.RelayLink = Link
        try:
            asyncio.run(bot.run_bot(("h", 1), center=(0.0, 0.0, 0.0), stop=stop, hz=1000, log=lambda *_: None,
                                    weapon="W_1", shield="", bow="B_1", on_back=4))
        finally:
            bot.RelayLink = orig
        self.assertTrue(sent)
        self.assertEqual({(m["weapon"], m["shield"], m["bow"], m["on_back"]) for m in sent}, {("W_1", "", "B_1", 4)})
        self.assertIsNotNone(mp.clean_state(sent[0]).get("on_back"))

    def test_steps_logged_and_an_older_sender(self):
        import asyncio
        sent, logged = [], []

        class Link:
            def __init__(self, *a, **k):
                self.connected, self.room = True, "default"

            async def run(self, stop):
                await stop.wait()

            def send(self, msg):
                sent.append(msg)
                if len(sent) >= 3:
                    stop.set()
                return True

        stop = asyncio.Event()
        orig = bot.RelayLink
        bot.RelayLink = Link
        try:
            asyncio.run(bot.run_bot(("h", 1), center=(0.0, 0.0, 0.0), stop=stop, hz=1000, log=logged.append,
                                    items=bot.ItemScript.parse(gi.BOT_ITEMS), send_on_back=False))
        finally:
            bot.RelayLink = orig
        self.assertTrue(sent)
        self.assertTrue(all("on_back" not in m and m["weapon"] == ITEMS[0] for m in sent))
        changes = gi.bot_changes(logged)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0][1:], (ITEMS, 0))

    def test_command_line(self):
        for bad in (["--on-back", "9"], ["--shield", "a b"], ["--items", "A/-/-/0", "--weapon", "W_1"]):
            with self.subTest(argv=bad), self.assertRaises(SystemExit):
                import contextlib
                import io
                with contextlib.redirect_stderr(io.StringIO()):
                    bot.main(bad)


# ---------------------------------------------------------------- the in-game checks' judges

def reply(names=ITEMS, states=("held",) * 3, places=("hand",) * 3, dists=(0.01, 0.0, 0.02), holds=(0.3, 0.2, 0.4),
          stray=0, **extra):
    st = {"state": "live", "items.stray": stray, "items.bones": "ok"}
    for i in range(3):
        named = bool(names[i])
        st.update({f"items.{i}.name": names[i], f"items.{i}.state": states[i] if named else "none",
                   f"items.{i}.actor": f"0x{0x7240000000 + i * 0x1000:x}" if named else "0x0",
                   f"items.{i}.place": places[i] if named else "", f"items.{i}.dist_m": dists[i] if named else -1,
                   f"items.{i}.hold_m": holds[i], f"items.{i}.item_state": 255 if named else -1,
                   f"items.{i}.holder_ok": named, f"items.{i}.why": ""})
    st.update(extra)
    return st


class JudgeTests(unittest.TestCase):
    def test_held(self):
        ok = {"standing": [reply()], "walking": [reply(), reply()], "attacking": [reply()]}
        self.assertEqual(gi.judge_held(ok)[0], "PASS")
        far = dict(ok, walking=[reply(dists=(0.2, 0, 0))])
        self.assertEqual(gi.judge_held(far)[0], "FAIL")
        self.assertEqual(gi.judge_held(dict(ok, attacking=[]))[0], "FAIL")
        dropped = reply()
        dropped["items.1.item_state"] = 2
        self.assertIn("item state 2", gi.held_as(dropped, ITEMS))
        self.assertIn("not empty", gi.held_as(reply(), (ITEMS[0], "", ITEMS[2])))
        self.assertIsNone(gi.held_as(reply(names=(ITEMS[0], "", ITEMS[2])), (ITEMS[0], "", ITEMS[2])))

    def test_times(self):
        self.assertEqual(gi.judge_times("x", [0.5, 1.9], 2.0, None, 0)[0], "PASS")
        self.assertEqual(gi.judge_times("x", [0.5, None], 2.0, None, 0)[0], "FAIL")
        self.assertEqual(gi.judge_times("x", [2.5], 2.0, None, 0)[0], "FAIL")
        self.assertEqual(gi.judge_times("x", [0.5], 2.0, "slot 0: wrong", 0)[0], "FAIL")
        self.assertEqual(gi.judge_times("x", [0.5], 2.0, None, 1)[0], "FAIL")
        self.assertEqual(gi.judge_times("x", [], 2.0, None, 0)[0], "FAIL")

    def test_back(self):
        back = reply(places=("back",) * 3, dists=(0.31, 0.22, 0.38), holds=(0.3, 0.2, 0.4))
        self.assertEqual(gi.judge_back([back], [reply()], "ok")[0], "PASS")
        off = reply(places=("back",) * 3, dists=(0.5, 0.22, 0.38))
        self.assertEqual(gi.judge_back([off], [reply()], "ok")[0], "FAIL")
        self.assertEqual(gi.judge_back([back], [back], "ok")[0], "FAIL")          # (not in the hand again)
        self.assertEqual(gi.judge_back([reply()], [reply()], "ok, no Pod_A")[0], "SKIP")
        self.assertEqual(gi.judge_back([reply(holds=(-1, -1, -1))], [reply()], "ok")[0], "SKIP")

    def test_no_take(self):
        me = {"weapon": "", "shield": "", "bow": ""}
        st = reply()
        actors = [sl["actor"] for sl in gi.slots(st)]
        self.assertEqual(gi.judge_no_take(me, dict(me), actors, st)[0], "PASS")
        self.assertEqual(gi.judge_no_take(me, dict(me, weapon=ITEMS[0]), actors, st)[0], "FAIL")
        self.assertEqual(gi.judge_no_take(me, me, actors[:2] + ["0x1"], st)[0], "FAIL")

    def test_gone(self):
        self.assertEqual(gi.judge_gone(reply(names=("", "", ""), state="idle"), "x", ("idle",))[0], "PASS")
        self.assertEqual(gi.judge_gone(reply(names=("", "", ""), state="idle", stray=1), "x", ("idle",))[0], "FAIL")
        self.assertEqual(gi.judge_gone(reply(state="idle"), "x", ("idle",))[0], "FAIL")
        self.assertEqual(gi.judge_gone(reply(names=("", "", ""), state="live"), "x", ("far",))[0], "FAIL")

    def test_cost(self):
        self.assertEqual(gi.judge_cost(30.0, 29.2, 20.0)[0], "PASS")
        self.assertEqual(gi.judge_cost(30.0, 28.5, 20.0)[0], "FAIL")
        self.assertEqual(gi.judge_cost(30.0, 29.5, 80.0)[0], "FAIL")     # (the plan's rule: the mean, under 50)
        self.assertEqual(gi.judge_cost(None, 29.5, 10.0)[0], "SKIP")
        rows = [{"items.us_mean": 10.0, "items.us_max": 30.0, "tick_us_max": 40.0, "frame_ms_max": 34.0,
                 "frames_slow": 1, "items.slow_parts": 0, "slow_calls": 2},
                {"items.us_mean": 12.0, "items.us_max": 50.0, "tick_us_max": 60.0, "frame_ms_max": 36.0,
                 "frames_slow": 3, "items.slow_parts": 1, "items.slow_part": "60.0 ms wall, 9000 cycles, 0 game calls",
                 "slow_calls": 2}]
        text = gi.cost_text(rows)
        for part in ("items mean 11.0", "most 50 us", "the whole tick most 60 us", "frames over 50 ms 2",
                     "slow item parts 1 (last: 60.0 ms wall, 9000 cycles", "slow game calls 0"):
            self.assertIn(part, text)

    def test_network(self):
        """Every change of every cycle, timed from the bot's own time for it (0.6.1)."""
        a = reply()
        b_named = reply(names=("Weapon_Lsword_001", "", "Weapon_Bow_001"), states=("grab", "none", "held"))
        b = reply(names=("Weapon_Lsword_001", "", "Weapon_Bow_001"), places=("back", "", "hand"))
        c = reply(names=("", "", ""))
        changes = gi.bot_changes([
            "items step: Weapon_Sword_001 Weapon_Shield_001 Weapon_Bow_001 mask 0 at 0.000",
            "items step: Weapon_Lsword_001 - Weapon_Bow_001 mask 1 at 5.000",
            "noise", "items step: - - - mask 0 at 10.000",
            "items step: Weapon_Sword_001 Weapon_Shield_001 Weapon_Bow_001 mask 0 at 13.000"])
        self.assertEqual(len(changes), 4)
        good = [(0.5, a), (5.3, b_named), (5.7, b), (10.4, c), (13.8, a), (14.0, a)]
        status, text = gi.judge_network(good, changes)
        self.assertEqual(status, "PASS", text)
        self.assertIn("3 changes", text)
        self.assertIn("0.80 s", text)                                          # (the slowest: 13.0 -> 13.8)
        slow = [(0.5, a), (5.3, b_named), (6.2, b), (10.4, c), (13.8, a)]
        self.assertEqual(gi.judge_network(slow, changes)[0], "FAIL")
        never = [(0.5, a), (5.3, b_named), (10.4, c), (13.8, a)]              # (the step never shown in place)
        self.assertEqual(gi.judge_network(never, changes)[0], "FAIL")
        old = [(0.5, a), (5.6, reply(names=("Weapon_Lsword_001", "", "Weapon_Bow_001"), places=("hand", "", "back"))),
               (10.4, c), (13.8, reply(places=("hand", "hand", "back")))]
        self.assertEqual(gi.judge_network(old, changes, mask_for=lambda m: 4)[0], "PASS")

    def test_like_link(self):
        eye = [1, 0, 0, 0.1, 0, 1, 0, 0.2, 0, 0, 1, 0.0]
        turned = [0, -1, 0, 0.1, 1, 0, 0, 0.2, 0, 0, 1, 0.0]                   # (90 degrees about z)
        self.assertEqual(gi.rel_compare(eye, eye), (0.0, 0.0))
        d, ang = gi.rel_compare(eye, turned)
        self.assertAlmostEqual(ang, 90.0, places=3)
        text = " ".join(str(x) for x in eye)
        ghost = reply(places=("back",) * 3)
        for i in range(3):
            ghost[f"items.{i}.rel"] = text
        rows = {f"{k}_back": {k: n, f"{k}_back": True, f"{k}_rel": text, f"{k}_pods": "0.1 0.3 0.4"}
                for k, n in zip(("weapon", "shield", "bow"), ITEMS)}
        self.assertEqual(gi.judge_like_link(ghost, rows)[0], "PASS")
        rows["shield_back"]["shield_rel"] = " ".join(str(x) for x in turned)
        self.assertEqual(gi.judge_like_link(ghost, rows)[0], "FAIL")
        self.assertEqual(gi.judge_like_link(ghost, {})[0], "SKIP")
        self.assertIsNone(gi.rel_matrix(""))
        self.assertIsNone(gi.rel_matrix("1 2 x"))

    def test_sender(self):
        me = {"weapon": ITEMS[0], "shield": ITEMS[1], "bow": ITEMS[2], "on_back": 7}
        self.assertIsNone(gi.link_shows(reply(places=("back",) * 3), me))
        self.assertIsNotNone(gi.link_shows(reply(), me))
        self.assertIsNone(gi.link_shows(reply(names=("", ITEMS[1], ITEMS[2]), places=("", "back", "back")),
                                        dict(me, weapon="")))
        self.assertEqual(gi.judge_sender([("sprint", 0.8, None), ("throw", 1.1, None)])[0], "PASS")
        self.assertEqual(gi.judge_sender([("sprint", 0.8, None), ("throw", None, "late")])[0], "FAIL")
        self.assertEqual(gi.judge_sender([])[0], "FAIL")

    def test_back_bytes(self):
        rows = {"weapon_out": {"weapon": "W", "weapon_back": False}, "weapon_back": {"weapon": "W", "weapon_back": True}}
        self.assertEqual(gi.judge_back_bytes(rows)[0], "PASS")
        rows["weapon_back"] = {"weapon": "W", "weapon_back": False}
        self.assertEqual(gi.judge_back_bytes(rows)[0], "FAIL")
        self.assertEqual(gi.judge_back_bytes({})[0], "SKIP")

    def test_give_and_log(self):
        self.assertEqual(gi.judge_give({"weapon": ITEMS[0], "shield": ITEMS[1], "bow": ITEMS[2]})[0], "PASS")
        self.assertEqual(gi.judge_give({"weapon": ITEMS[0], "shield": "", "bow": ITEMS[2]})[0], "FAIL")
        self.assertEqual(gi.judge_log("[mod] ghost: ready\n[modhost] ok\n")[0], "PASS")
        self.assertEqual(gi.judge_log("ok\nAssertion failed: x\n")[0], "FAIL")
        self.assertEqual(gi.judge_log("Unhandled EXCEPTION at 0x0\n")[0], "FAIL")


# ---------------------------------------------------------------- what C and Python agree on

class AgreementTests(unittest.TestCase):
    def test_the_mod_says_it_can(self):
        self.assertIn('"has_items", true', GHOST_C)
        for key in ("items.%d.", "items.stray", "items.us", "items.bones", "give.state"):
            self.assertIn(key.split("%")[0], GHOST_C)

    def test_the_command_shape(self):
        self.assertIn("items weapon|- shield|- bow|- [mask]", GHOST_C)
        self.assertIn("int mask = 4;", GHOST_C)                           # (the default: the bow on the back)
        self.assertEqual(client.item_mask({}), 4)
        self.assertIn("m < 0 || m > 7", GHOST_C)
        self.assertEqual(mp.MAX_ON_BACK, 7)

    def test_self_reply_keys(self):
        for key in ("weapon_back", "shield_back", "bow_back", "on_back"):
            self.assertIn(f'"{key}"', GHOST_C)
        for key in ('"%s_rel"', '"%s_pods"', '"items.dying"', '"items.us_mean"', '"frame_ms_max"', '"frames_slow"',
                    '"items.slow_part"', '"slow_call"', 'ITEM_KEY("rel")', 'ITEM_KEY("refusals")', '"refused"'):
            self.assertIn(key, GHOST_C)

    def test_the_same_item_rule(self):
        """The mod's item_name_ok and the client's: the same prefixes per hand and the same blocked names."""
        prefixes = re.search(r"ITEM_PREFIX\[\] = \{([^}]*)\}", GHOST_C).group(1)
        slots_c = re.search(r"ITEM_PREFIX_SLOT\[\] = \{([^}]*)\}", GHOST_C).group(1)
        blocked = re.search(r"ITEM_BLOCKED\[\] = \{([^}]*)\}", GHOST_C).group(1)
        pairs = tuple(zip(re.findall(r'"([^"]+)"', prefixes), (int(x) for x in re.findall(r"\d", slots_c))))
        self.assertEqual(pairs, client.ITEM_PREFIXES)
        self.assertEqual(set(re.findall(r'"([^"]+)"', blocked)), set(client.ITEM_BLOCKED))

    def test_slot_names_the_judges_read(self):
        names = re.search(r"IS_NAMES\[\] = \{([^}]*)\}", GHOST_C).group(1)
        for state_name in ("none", "held", "grab", "pending"):
            self.assertIn(f'"{state_name}"', names)
        for place in ("hand", "back"):
            self.assertIn(f'"{place}"', GHOST_C)


if __name__ == "__main__":
    unittest.main()
