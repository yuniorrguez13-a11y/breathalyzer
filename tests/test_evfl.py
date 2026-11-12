"""Tests for tools/evfl.py (BFEVFL event flows). Every flow here is made up and built in memory (the event packs for
the `check` tests go to temp folders). The byte-for-byte check against the player's own game flows is
`python tools/evfl.py check <extracted>`: game data, so not a test."""
import contextlib
import io
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import bnp  # noqa: E402
import evfl  # noqa: E402
import wiiu_convert as wc  # noqa: E402
from wiiu_model import dicts  # noqa: E402

E = evfl.NONE


def made_up_flow() -> evfl.Flow:
    """A small talk: greet, ask a two-way question, two answers, a fork that waits on two things, a subflow call.
    Names and values are ours."""
    f = evfl.Flow("TestPal")
    greet = f.subflow("TestCommon", "Greet", evfl.params(Arg_Mood=evfl.Arg("Mood"), Style="Wave"))
    hello = f.act("TestPal", "Demo_Say", evfl.params(MessageId="Msg/TestPal:Hello", IsWaitFinish=True,
                                                     Pause=0.5, Count=3))
    ask = f.switch("EventSystemActor", "AskTwo", None)
    yes = f.act("TestPal", "Demo_Say", evfl.params(MessageId="Msg/TestPal:Yes", Mark=evfl.Floats([1.0, -2.5, 3.0]),
                                                   Steps=[4, 5], Flags=evfl.Param(evfl.BOOLS, [True, False])))
    no = f.act("TestPal", "Demo_Say", evfl.params(MessageId="Msg/TestPal:No", Extra={"Inner": 7, "Deep": {"X": 1.0}}))
    wave = f.act("TestPal", "Demo_Wave")
    wait = f.act("EventSystemActor", "Demo_Wait", evfl.params(Frame=30))
    joined = f.join()
    both = f.fork([wave, wait], joined)
    again = f.subflow("", "Bye")
    bye = f.act("TestPal", "Demo_Say", evfl.params(MessageId="Msg/TestPal:Bye"), name="SayBye")
    f.chain(greet, hello, ask)
    f.case(ask, 0, yes)
    f.case(ask, 1, no)
    f.chain(yes, both)
    f.chain(joined, again)
    f.chain(no, again)
    f.entry("Talk", greet)
    f.entry("Bye", bye)
    return f


class RoundTrip(unittest.TestCase):
    def test_made_up_flow_round_trips(self):
        f = made_up_flow()
        data = evfl.dump(f)
        g = evfl.load(data)
        self.assertEqual(evfl.dump(g), data)
        f.entries[0].subflows = evfl.reachable_subflows(f, f.entries[0].main)
        f.entries[1].subflows = []
        self.assertEqual(g, f)

    def test_values_come_back_typed(self):
        g = evfl.load(evfl.dump(made_up_flow()))
        ev = {e.name: e for e in g.events}
        hello = ev["Event1"].params
        self.assertEqual(hello["IsWaitFinish"], evfl.Param(evfl.BOOL, True))
        self.assertEqual(hello["Pause"], evfl.Param(evfl.FLOAT, 0.5))
        self.assertEqual(hello["Count"], evfl.Param(evfl.INT, 3))
        self.assertEqual(hello["MessageId"], evfl.Param(evfl.TEXT, "Msg/TestPal:Hello"))
        yes = ev["Event3"].params
        self.assertEqual(yes["Mark"].value, [1.0, -2.5, 3.0])
        self.assertEqual(yes["Steps"], evfl.Param(evfl.INTS, [4, 5]))
        self.assertEqual(yes["Flags"].value, [True, False])
        no = ev["Event4"].params["Extra"]
        self.assertEqual(no.kind, evfl.GROUP)
        self.assertEqual(no.value["Deep"].value["X"], evfl.Param(evfl.FLOAT, 1.0))
        self.assertEqual(ev["Event0"].params["Arg_Mood"], evfl.Param(evfl.ARG, "Mood"))
        sub = ev["Event9"]
        self.assertIsInstance(sub, evfl.Subflow)
        self.assertEqual((sub.flow, sub.entry), ("", "Bye"))

    def test_bool_bits(self):
        f = evfl.Flow("B")
        f.entry("E", f.act("A", "Do", {"T": evfl.Bool(True), "F": evfl.Bool(False), "Odd": evfl.Param(evfl.BOOL, 5)}))
        data = evfl.dump(f)
        self.assertIn(struct.pack("<I", 0x80000001), data)
        p = evfl.load(data).events[0].params
        self.assertEqual((p["T"].value, p["F"].value, p["Odd"].value), (True, False, 5))
        self.assertEqual(evfl.dump(evfl.load(data)), data)

    def test_header_name_kept_when_different(self):
        f = made_up_flow()
        f.file_name = "OtherName"
        g = evfl.load(evfl.dump(f))
        self.assertEqual((g.name, g.file_name), ("TestPal", "OtherName"))

    def test_empty_flow(self):
        f = evfl.Flow("Empty")
        data = evfl.dump(f)
        self.assertEqual(evfl.dump(evfl.load(data)), data)
        self.assertEqual(evfl.load(data), f)


class Layout(unittest.TestCase):
    def setUp(self):
        self.f = made_up_flow()
        self.d = evfl.dump(self.f)

    def u16(self, o):
        return struct.unpack_from("<H", self.d, o)[0]

    def u32(self, o):
        return struct.unpack_from("<I", self.d, o)[0]

    def ptr(self, o):
        return struct.unpack_from("<Q", self.d, o)[0]

    def relt(self) -> set:
        r = self.u32(0x18)
        self.assertEqual(self.d[r:r + 4], b"RELT")
        self.assertEqual(struct.unpack_from("<III", self.d, r + 4), (r, 1, 0))
        n = self.u32(r + 0x10 + 0x14)
        slots = set()
        for k in range(n):
            base, mask = struct.unpack_from("<II", self.d, r + 0x28 + 8 * k)
            slots.update(base + 8 * b for b in range(32) if mask >> b & 1)
        self.assertEqual(r + 0x28 + 8 * n, len(self.d))
        return slots

    def test_header(self):
        d = self.d
        self.assertEqual(d[:8], b"BFEVFL\0\0")
        self.assertEqual(d[8:0x10], bytes([0, 3, 0, 0, 0xFF, 0xFE, 3, 0]))
        self.assertEqual(self.u32(0x1C), len(d))
        self.assertEqual((self.u16(0x16), self.u16(0x20), self.u16(0x22)), (0x90, 1, 0))
        self.assertEqual(self.ptr(0x28), 0x48)
        self.assertEqual(self.ptr(0x48), 0x90)
        self.assertEqual(self.ptr(0x38), 0)                      # no timelines
        self.assertEqual(d[0x90:0x94], b"EVFL")
        name = self.u32(0x10)
        self.assertEqual(d[name - 2:name + len("TestPal") + 1], struct.pack("<H", 7) + b"TestPal\0")

    def test_counts(self):
        f = self.f
        counts = struct.unpack_from("<5H", self.d, 0x90 + 0x10)
        self.assertEqual(counts, (len(f.actors), sum(len(a.actions) for a in f.actors),
                                  sum(len(a.queries) for a in f.actors), len(f.events), len(f.entries)))

    def test_block_order(self):
        # header, flowchart list, its dictionary, the empty timeline dictionary, flowchart, actors, events
        na, ne, nep = len(self.f.actors), len(self.f.events), len(self.f.entries)
        actors = 0x90 + 0x48
        events = actors + 0x38 * na
        ep_dic = events + 0x28 * ne
        eps = ep_dic + 8 + 16 * (nep + 1)
        self.assertEqual([self.ptr(0x90 + 0x28 + 8 * k) for k in range(4)], [actors, events, ep_dic, eps])
        self.assertEqual(self.d[0x50:0x54] + self.d[0x78:0x7C], b"DIC DIC ")
        # the first event's parameters come right after the entry points
        first = next(i for i, e in enumerate(self.f.events) if getattr(e, "params", None) is not None)
        self.assertEqual(self.ptr(events + 0x28 * first + 0x10), eps + 0x20 * nep)
        # the string pool comes last, after each entry point's 0x18 zero bytes
        pool = 0x90 + self.u32(0x94)
        self.assertEqual(self.d[pool:pool + 0x10], b"STR " + bytes(12))
        self.assertEqual(self.d[pool - 0x18:pool], bytes(0x18))
        sub = self.ptr(eps)                                      # "Talk" has one reachable subflow list
        self.assertEqual(self.u16(eps + 0x18), 2)
        self.assertEqual(struct.unpack_from("<2H", self.d, sub), (0, 9))

    def test_string_pool_order(self):
        pool = 0x90 + self.u32(0x94)
        n = self.u32(pool + 0x10)
        o, got = pool + 0x14, []
        for _ in range(n + 1):
            ln = self.u16(o)
            got.append(self.d[o + 2:o + 2 + ln].decode())
            o += 2 + ln + 1
            o += o % 2
        self.assertEqual(got[0], "")
        self.assertEqual(len(got), len(set(got)))
        for a, b in zip(got[1:], got[2:]):
            ka, kb = a.encode(), b.encode()
            width = max(len(ka), len(kb))
            bit = dicts.first_diff(ka.rjust(width, b"\0"), kb.rjust(width, b"\0"))
            self.assertEqual((dicts.bit(ka, bit), dicts.bit(kb, bit)), (0, 1), (a, b))
        self.assertIn("MessageId", got)
        self.assertNotIn("Msg/TestPal:Hello", got)                # text values live next to their parameter
        self.assertEqual(self.u32(self.u32(0x18) + 0x10 + 0xC), o)  # RELT: the pool's end

    def test_pool_key_by_hand(self):
        # "a" = 0x61 and "b" = 0x62 differ in bit 0 of the last byte; "ab"/"ba" add a byte above it
        self.assertEqual(evfl.pool_order(["a", "b", "ab", "ba", ""]), ["b", "ab", "a", "ba"])

    def test_dictionaries_find_their_keys(self):
        ep_dic = self.ptr(0x90 + 0x38)
        nodes, ptrs = dicts.read_nx_dict(self.d, ep_dic)
        keys = [self.d[p + 2:p + 2 + self.u16(p)] for p in ptrs]
        self.assertEqual(keys, [b"Talk", b"Bye"])
        for i, k in enumerate(keys):
            self.assertEqual(dicts.lookup(nodes, keys, k), i)
        self.assertEqual(dicts.lookup(nodes, keys, b"Nope"), -1)

    def test_relocation_table(self):
        slots = self.relt()
        size = len(self.d)
        for s in slots:
            self.assertEqual(s % 8, 0)
            self.assertLess(self.ptr(s), size)
        for o in (0x28, 0x30, 0x38, 0x40, 0x48):                  # header ptrs, even the empty timeline list
            self.assertIn(o, slots)
        events = self.ptr(0x90 + 0x30)
        ask = next(i for i, e in enumerate(self.f.events) if isinstance(e, evfl.Switch))
        at = events + 0x28 * ask
        self.assertIn(at, slots)                                  # name
        self.assertNotIn(at + 0x10, slots)                        # no parameters: not listed
        self.assertIn(at + 0x18, slots)                           # cases
        actors = self.ptr(0x90 + 0x28)
        pal = next(i for i, a in enumerate(self.f.actors) if a.name == "TestPal")
        self.assertEqual(self.ptr(actors + 0x38 * pal + 0x20), 0)  # no queries...
        self.assertIn(actors + 0x38 * pal + 0x20, slots)          # ...listed anyway
        eps = self.ptr(0x90 + 0x40)
        bye = eps + 0x20
        self.assertEqual((bye in slots, bye + 8 in slots, bye + 16 in slots), (True, False, True))
        # every non-zero 8-byte value the reader follows is listed: all pointers inside a group
        grp = self.ptr(events + 0x28 * 1 + 0x10)
        n = self.u16(grp + 2)
        for k in range(n + 1):
            self.assertIn(grp + 8 + 8 * k, slots)

    def test_text_value_layout(self):
        events = self.ptr(0x90 + 0x30)
        grp = self.ptr(events + 0x28 * 1 + 0x10)
        keys = [self.d[p + 2:p + 2 + self.u16(p)].decode() for p in dicts.read_nx_dict(self.d, self.ptr(grp + 8))[1]]
        item = self.ptr(grp + 0x10 + 8 * keys.index("MessageId"))
        self.assertEqual(self.d[item:item + 0x10], bytes([evfl.TEXT, 0, 1, 0]) + bytes(12))
        txt = self.ptr(item + 0x10)
        self.assertEqual(txt, item + 0x18)
        self.assertEqual(self.d[txt:txt + 2 + 17 + 1], struct.pack("<H", 17) + b"Msg/TestPal:Hello\0")


class LayoutRules(unittest.TestCase):
    """Layout rules seen in the game's flows that the made-up talk above doesn't use."""

    @staticmethod
    def slots(d: bytes) -> set:
        r = struct.unpack_from("<I", d, 0x18)[0]
        n = struct.unpack_from("<I", d, r + 0x24)[0]
        out = set()
        for k in range(n):
            base, mask = struct.unpack_from("<II", d, r + 0x28 + 8 * k)
            out.update(base + 8 * b for b in range(32) if mask >> b & 1)
        return out

    def test_actor_id_value(self):
        # an actor named by its two names: count 2, two ptrs, the two copies after them, the second 2-aligned
        f = evfl.Flow("Who")
        f.entry("E", f.act("A", "LookAt", {"Target": evfl.ActorId("Pal_Odd", "Two"), "Me": evfl.ActorId("Me")}))
        d = evfl.dump(f)
        g = evfl.load(d)
        self.assertEqual(g.events[0].params["Target"], evfl.Param(evfl.ACTOR_ID, ("Pal_Odd", "Two")))
        self.assertEqual(g.events[0].params["Me"].value, ("Me", ""))
        self.assertEqual(evfl.dump(g), d)
        events = struct.unpack_from("<Q", d, 0x90 + 0x30)[0]
        grp = struct.unpack_from("<Q", d, events + 0x10)[0]
        keys = [d[p + 2:p + 2 + struct.unpack_from("<H", d, p)[0]].decode()
                for p in dicts.read_nx_dict(d, struct.unpack_from("<Q", d, grp + 8)[0])[1]]
        item = struct.unpack_from("<Q", d, grp + 0x10 + 8 * keys.index("Target"))[0]
        self.assertEqual(d[item:item + 0x10], bytes([evfl.ACTOR_ID, 0, 2, 0]) + bytes(12))
        a, b = struct.unpack_from("<2Q", d, item + 0x10)
        self.assertEqual(a, item + 0x20)
        self.assertEqual(d[a:a + 10], struct.pack("<H", 7) + b"Pal_Odd\0")
        self.assertEqual(b, a + 10)                                   # 2 + 7 + 1 = 10: already even
        self.assertEqual(d[b:b + 6], struct.pack("<H", 3) + b"Two\0")
        self.assertTrue({item + 0x10, item + 0x18} <= self.slots(d))
        me = struct.unpack_from("<Q", d, grp + 0x10 + 8 * keys.index("Me"))[0]
        a, b = struct.unpack_from("<2Q", d, me + 0x10)
        self.assertEqual((a, b), (me + 0x20, me + 0x26))             # 2 + 2 + 1 = 5, padded to 6
        self.assertEqual(d[b:b + 3], b"\0\0\0")                      # the empty second name: its own copy

    def test_empty_lists_have_no_ptr(self):
        d = evfl.dump(evfl.Flow("Nothing"))
        ptrs = struct.unpack_from("<4Q", d, 0x90 + 0x28)
        self.assertEqual((ptrs[0], ptrs[1], ptrs[3]), (0, 0, 0))     # actors, events, entry points
        self.assertEqual(d[ptrs[2]:ptrs[2] + 4], b"DIC ")             # the entry dictionary is always there
        self.assertTrue({0x90 + 0x28 + 8 * k for k in range(4)} <= self.slots(d))
        f = evfl.Flow("NoActors")                                     # a talk that only runs another flow
        f.entry("Talk", f.subflow("Common", "Hello", evfl.params(Arg_Turn=0)))
        d = evfl.dump(f)
        ptrs = struct.unpack_from("<4Q", d, 0x90 + 0x28)
        self.assertEqual(ptrs[0], 0)
        self.assertEqual(ptrs[1], 0x90 + 0x48)
        self.assertEqual(evfl.dump(evfl.load(d)), d)

    def test_switch_without_cases_lists_its_cases_ptr(self):
        f = evfl.Flow("S")
        f.entry("E", f.switch("A", "Ask"))
        d = evfl.dump(f)
        events = struct.unpack_from("<Q", d, 0x90 + 0x30)[0]
        self.assertEqual(struct.unpack_from("<Q", d, events + 0x18)[0], 0)
        self.assertIn(events + 0x18, self.slots(d))
        self.assertNotIn(events + 0x10, self.slots(d))               # no parameters: not listed
        self.assertEqual(evfl.dump(evfl.load(d)), d)


class Builder(unittest.TestCase):
    def test_actors_and_actions_once(self):
        f = evfl.Flow("X")
        a = f.act("Pal", "Do")
        b = f.act("Pal", "Do")
        c = f.act("Pal", "Other")
        self.assertEqual(len(f.actors), 1)
        self.assertEqual(f.actors[0].actions, ["Do", "Other"])
        self.assertEqual([f.events[i].action for i in (a, b, c)], [0, 0, 1])
        self.assertEqual(list(f.actors[0].params), ["CreateMode", "IsGrounding", "IsWorld", "PosX", "PosY", "PosZ",
                                                    "RotX", "RotY", "RotZ"])
        f.act("Pal", "Do", sub="Two")
        self.assertEqual(len(f.actors), 2)

    def test_names(self):
        f = evfl.Flow("X")
        f.act("A", "Do", name="Event1")
        self.assertEqual(f.act("A", "Do"), 1)
        self.assertEqual(f.events[1].name, "Event2")              # Event1 is taken
        with self.assertRaises(evfl.EvflError):
            f.act("A", "Do", name="Event1")
        f.entry("E", 0)
        with self.assertRaises(evfl.EvflError):
            f.entry("E", 1)

    def test_case_replaces(self):
        f = evfl.Flow("X")
        s = f.switch("A", "Ask", {0: E})
        f.case(s, 0, s)
        f.case(s, 2, E)
        self.assertEqual(f.events[s].cases, [(0, s), (2, E)])
        with self.assertRaises(evfl.EvflError):
            f.case(f.act("A", "Do"), 0, 0)
        with self.assertRaises(evfl.EvflError):
            f.chain(s, 0)                                         # a switch has no single next

    def test_params_kinds(self):
        p = evfl.params(B=True, I=1, F=1.5, S="x", L=[1, 2], M=[1, 2.5], G={"K": 1})
        self.assertEqual([v.kind for v in p.values()],
                         [evfl.BOOL, evfl.INT, evfl.FLOAT, evfl.TEXT, evfl.INTS, evfl.FLOATS, evfl.GROUP])
        with self.assertRaises(evfl.EvflError):
            evfl.params(X=None)

    def test_reachable_subflows_order(self):
        # switch cases in their order, a fork's branches before its join, loops visited once
        f = evfl.Flow("X")
        s = f.switch("A", "Ask")
        sub = [f.subflow("", f"S{i}") for i in range(5)]
        j = f.join(sub[4])
        k = f.fork([sub[2], sub[3]], j)
        f.case(s, 0, sub[0])
        f.case(s, 1, k)
        f.chain(sub[0], sub[1], s)                                # a loop back to the switch
        self.assertEqual(evfl.reachable_subflows(f, s), [sub[0], sub[1], sub[2], sub[3], sub[4]])
        f.events[s].cases.reverse()
        self.assertEqual(evfl.reachable_subflows(f, s), [sub[2], sub[3], sub[4], sub[0], sub[1]])


class Errors(unittest.TestCase):
    def test_bad_flows_refused(self):
        def flow_with(change):
            f = made_up_flow()
            change(f)
            return f
        bad = [
            lambda f: setattr(f.events[1], "next", 99),
            lambda f: setattr(f.events[1], "actor", 9),
            lambda f: setattr(f.events[1], "action", 9),
            lambda f: setattr(f.events[2], "cases", [(0, 1), (0, 2)]),
            lambda f: setattr(f.events[2], "cases", [(-1, 1)]),
            lambda f: setattr(f.entries[0], "main", 99),
            lambda f: setattr(f.entries[0], "subflows", [1]),     # not a subflow event
            lambda f: setattr(f.events[3], "name", f.events[4].name),
            lambda f: f.events[1].params.update(W=evfl.Param(evfl.WTEXT, "x")),
            lambda f: f.events[1].params.update(W=evfl.Param(evfl.TEXTS, ["a", "b"])),
            lambda f: f.events[1].params.update(W=evfl.Param(evfl.ACTOR_ID, ("a",))),
            lambda f: f.events[1].params.update(W=evfl.Param(evfl.ACTOR_ID, ("a", 2))),
            lambda f: f.events[1].params.update(W=evfl.Param(evfl.TEXT, ("a", "b"))),
            lambda f: f.events[1].params.update(W=3),
            lambda f: f.events[1].params.update(W=evfl.Param(evfl.INTS, [])),
            lambda f: f.events.append("not an event"),
        ]
        for i, change in enumerate(bad):
            with self.subTest(i=i), self.assertRaises(evfl.EvflError):
                evfl.dump(flow_with(change))

    def test_damaged_files(self):
        data = evfl.dump(made_up_flow())
        with self.assertRaises(evfl.EvflError):
            evfl.load(b"NOTEVFL\0" + data[8:])
        for cut in (0x30, 0x100, len(data) // 2):
            with self.subTest(cut=cut), self.assertRaises(evfl.EvflError):
                evfl.load(data[:cut])
        two = bytearray(data)
        struct.pack_into("<H", two, 0x20, 2)                      # two flowcharts
        with self.assertRaises(evfl.EvflError):
            evfl.load(bytes(two))

    def test_unsupported_kind_is_named(self):
        f = evfl.Flow("K")
        f.entry("E", f.act("A", "Do", evfl.params(V=1)))
        data = bytearray(evfl.dump(f))
        events = struct.unpack_from("<Q", data, 0x90 + 0x30)[0]
        grp = struct.unpack_from("<Q", data, events + 0x10)[0]
        item = struct.unpack_from("<Q", data, grp + 0x10)[0]
        data[item] = evfl.TEXTS
        with self.assertRaisesRegex(evfl.EvflError, "kind 10"):
            evfl.load(bytes(data))

    def test_actor_id_count_checked(self):
        f = evfl.Flow("K")
        f.entry("E", f.act("A", "Do", {"Who": evfl.ActorId("Pal")}))
        data = bytearray(evfl.dump(f))
        events = struct.unpack_from("<Q", data, 0x90 + 0x30)[0]
        grp = struct.unpack_from("<Q", data, events + 0x10)[0]
        item = struct.unpack_from("<Q", data, grp + 0x10)[0]
        struct.pack_into("<H", data, item + 2, 1)
        with self.assertRaisesRegex(evfl.EvflError, "actor with count 1"):
            evfl.load(bytes(data))


def pack(flows: dict) -> bytes:
    return wc.yaz0_compress(bnp.sarc_build([(f"EventFlow/{n}.bfevfl", d, 8) for n, d in flows.items()]))


class GameCheck(unittest.TestCase):
    """`check` on a made-up romfs in a temp folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ext = os.path.join(self.tmp.name, "extracted")
        ev = os.path.join(self.ext, "romfs", "Event")
        os.makedirs(ev)
        good = evfl.dump(made_up_flow())
        other = evfl.Flow("TestCommon")
        other.entry("Greet", other.act("EventSystemActor", "Demo_Wait", evfl.params(Frame=1)))
        with open(os.path.join(ev, "TestPal.sbeventpack"), "wb") as f:
            f.write(pack({"TestPal": good, "TestCommon": evfl.dump(other)}))
        with open(os.path.join(ev, "Odd.sbeventpack"), "wb") as f:
            f.write(pack({"Odd": good + b"\0" * 8}))             # trailing bytes: rebuilt shorter

    def test_named_flows(self):
        exact, checked, notes = evfl.check(self.ext, ["TestPal", "TestPal/TestCommon"])
        self.assertEqual((exact, checked, notes), (2, 2, []))

    def test_every_flow_and_out(self):
        out = os.path.join(self.tmp.name, "o")
        exact, checked, notes = evfl.check(os.path.join(self.ext, "romfs"), every=True, out=out)
        self.assertEqual((exact, checked), (2, 3))
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0][0], "Odd/Odd")
        self.assertIn("differs at", notes[0][1])
        self.assertEqual(sorted(os.listdir(out)), ["Odd__Odd.game.bfevfl", "Odd__Odd.rebuilt.bfevfl",
                                                   "TestPal__TestCommon.rebuilt.bfevfl",
                                                   "TestPal__TestPal.rebuilt.bfevfl"])

    def test_missing(self):
        exact, checked, notes = evfl.check(self.ext, ["Nope", "TestPal/Nope"])
        self.assertEqual((exact, checked), (0, 0))
        self.assertEqual([n[0] for n in notes], ["Nope", "TestPal/Nope"])
        with self.assertRaises(evfl.EvflError):
            evfl.check(self.tmp.name)                             # no Event folder

    def test_out_in_repo_refused(self):
        with self.assertRaisesRegex(evfl.EvflError, "inside the repo"):
            evfl.check(self.ext, ["TestPal"], out=str(REPO / "tools" / "x"))

    def test_cli(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(evfl.main(["check", self.ext, "--flow", "TestPal"]), 0)
            self.assertEqual(evfl.main(["check", self.ext, "--all"]), 1)
            self.assertEqual(evfl.main(["show", os.path.join(self.ext, "romfs", "Event", "TestPal.sbeventpack")]), 0)
        text = buf.getvalue()
        self.assertIn("1 of 1 flow(s) rebuilt byte for byte.", text)
        self.assertIn("2 of 3 flow(s) rebuilt byte for byte.", text)
        self.assertIn("run TestCommon<Greet>", text)
        self.assertIn("EventSystemActor.AskTwo? [0: 3:Event3, 1: 4:Event4]", text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(evfl.main(["check", self.ext, "--all", "--flow", "X"]), 2)
        self.assertIn("don't go together", err.getvalue())


if __name__ == "__main__":
    unittest.main()
