"""Tests for tools/mapkit.py (the shared map, event and flow helpers). Made-up data and the repo's own recipes only
(castle/ and dragonbreath's stage 2 recipe): no game files are read. The equivalence tests pin mapkit's helpers to
the objects castle_build and dragonbreath_stage2 write by hand today, so moving the code later can't change a map."""
import struct
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import castle_build as cb  # noqa: E402
import dragonbreath_stage2 as s2  # noqa: E402
import evfl  # noqa: E402
import mapkit as mk  # noqa: E402


def by_hid(objs: list[dict]) -> dict[int, dict]:
    return {int(o["HashId"]): o for o in objs}


class ReexportTests(unittest.TestCase):
    def test_same_objects_as_castle_build(self):
        for name, theirs in (("map_object", cb.map_object), ("map_link", cb.map_link), ("tag_params", cb.tag_params),
                             ("check_flow", cb.check_flow), ("build_flow", cb.build_flow),
                             ("evfl_ready", cb.evfl_ready), ("subflows_of", cb.subflows_of),
                             ("event_pack", cb.event_pack), ("carried_flows", cb.carried_flows),
                             ("MapError", cb.CastleError), ("TALK_INFO", cb.TALK_INFO), ("DOOR_INFO", cb.DOOR_INFO)):
            with self.subTest(name):
                self.assertIs(getattr(mk, name), theirs)

    def test_all_names_exist(self):
        for name in mk.__all__:
            self.assertTrue(hasattr(mk, name), name)


class HashIdTests(unittest.TestCase):
    def test_matches_castle_and_dragonbreath(self):
        for ns, theirs in (("giruka.castle-date", cb._hid), ("giruka.dragonbreath/stage2", s2.hid)):
            for key in ("gate", "zelda", "fd_area", "chest"):
                with self.subTest(ns=ns, key=key):
                    self.assertEqual(int(mk.hash_id(ns, key, set())), int(theirs(key, set())))

    def test_skips_used_ids_like_theirs(self):
        first = int(mk.hash_id("giruka.castle-date", "gate", set()))
        used_a, used_b = {first}, {first}
        self.assertEqual(int(mk.hash_id("giruka.castle-date", "gate", used_a)), int(cb._hid("gate", used_b)))
        self.assertNotEqual(int(mk.hash_id("giruka.castle-date", "gate", {first})), first)
        self.assertEqual(used_a, used_b)

    def test_stable_and_per_namespace(self):
        a = mk.hash_ids("giruka.test", ["x", "y"], set())
        self.assertEqual(a, mk.hash_ids("giruka.test", ["x", "y"], set()))
        self.assertNotEqual(int(a["x"]), int(mk.hash_ids("giruka.other", ["x"], set())["x"]))
        self.assertNotEqual(int(a["x"]), int(a["y"]))

    def test_duplicate_keys_refused(self):
        with self.assertRaises(mk.MapError):
            mk.hash_ids("giruka.test", ["x", "x"], set())


class CastleEquivalenceTests(unittest.TestCase):
    """mapkit's trigger_chain and flag_gate make the castle's door triggers and gate exactly as map_objects does."""

    def setUp(self):
        self.layout = cb.load_recipe().layout
        used: set[int] = set()
        self.ids = {k: cb._hid(k, used) for k in cb.object_keys(self.layout, True)}
        self.theirs = by_hid(cb.map_objects(self.layout, set(), True))

    def test_door_triggers(self):
        warps = [d for d in self.layout["doors"] if d.get("warp")]
        self.assertTrue(warps)
        for d in warps:
            t = d["trigger"]
            ours = mk.trigger_chain(d["key"], t["pos"], self.ids, flow=cb.DOOR_FLOW, entry=d["warp"],
                                    half=t["half"], yaw=float(t.get("yaw", 0)))
            self.assertEqual(len(ours), 3)
            for o in ours:
                with self.subTest(door=d["key"], obj=o["UnitConfigName"]):
                    self.assertEqual(o, self.theirs[int(o["HashId"])])

    def test_gate(self):
        created = []
        for d in self.layout["doors"]:
            created.append(d["key"])
            if d.get("warp"):
                created.append(f"{d['key']}_area")
        created.append("zelda")
        ours = mk.flag_gate("gate", self.layout["tags"]["room"], self.ids, cb.GATE_FLAG, creates=created)
        self.assertEqual(ours, self.theirs[int(self.ids["gate"])])

    def test_talk_params(self):
        for close in (True, False):
            self.assertEqual(mk.talk_params(cb.ACTOR, "Hi", close, "Wave"), cb._demo_talk("Hi", close, "Wave"))


class DragonbreathEquivalenceTests(unittest.TestCase):
    """... and stage 2's first-draw trigger, its gates and Daruk's gate exactly as cave_objects does."""

    def setUp(self):
        self.st = s2.load_stage2()
        cave = self.st.cave
        keys = ["rock", "rock_far"] + [o["key"] for o in cave["objects"]] + \
            ["chest", "daruk", "daruk_gate", "fd_gate", "fd_off", "fd_area", "fd_relay", "fd_event"]
        self.ids = mk.hash_ids("giruka.dragonbreath/stage2", keys, set())
        self.cm = s2.cave_objects(self.st, set(), lambda x, z: 0.0, {}, chest_drop=self.st.base_actor)
        self.theirs = by_hid(self.cm.static + self.cm.dynamic)
        self.cave = cave

    def test_first_draw_trigger_and_gates(self):
        fa = self.cave["first_draw_area"]
        at = list(self.cm.spots["fd_area"])
        ours = mk.trigger_chain("fd", at, self.ids, flow=s2.FLOW, entry="FirstDraw",
                                half=fa.get("half", [3.0, 3.0, 3.0]), yaw=self.cave["yaw"])
        ours.append(mk.flag_gate("fd_gate", at, self.ids, f"IsGet_{self.st.base_actor}", creates=["fd_area"]))
        ours.append(mk.flag_gate("fd_off", at, self.ids, s2.flag("FirstDraw", self.st), deletes=["fd_area"]))
        for o in ours:
            with self.subTest(obj=o["UnitConfigName"], hid=int(o["HashId"])):
                self.assertEqual(o, self.theirs[int(o["HashId"])])

    def test_daruk_gate(self):
        ours = mk.flag_gate("daruk_gate", list(self.cm.spots["daruk_gate"]), self.ids, s2.flag("DarukOn", self.st),
                            creates=["daruk"])
        self.assertEqual(ours, self.theirs[int(self.ids["daruk_gate"])])

    def test_talk_params_and_infos(self):
        self.assertEqual(mk.talk_params(s2.FLOW, "Ask", True), s2._talk(self.st, "Npc", "Ask", True))
        spec = s2.flow_spec(self.st)
        game_talk = {"is_timeline": False, "mode": "Seamless", "slink_user": "MadeUp"}
        self.assertEqual(mk.info_entries(spec, game_talk, {"FirstDraw": s2.DEMO_INFO}),
                         s2.event_infos(spec, game_talk))
        self.assertEqual(mk.info_entries(spec, None, {"FirstDraw": s2.DEMO_INFO}), s2.event_infos(spec, None))

    def test_flowspec_same_dict_as_stage2s(self):
        theirs = s2._Spec("F")
        ours = mk.FlowSpec("F")
        for f in (theirs, ours):
            f.sub("init", "InitTalk", "InitTalk", {"Arg_Turn": 0}, "ask")
            f.sw("ask", "EventSystemActor", "GeneralChoice2", {}, {0: "yes", 1: None})
            f.act("yes", "Npc", "Demo_Talk", {"IsWaitFinish": True}, None)
            f.spec["entry_points"]["Talk"] = "init"
        self.assertEqual(ours.spec, theirs.spec)


class MapHelperTests(unittest.TestCase):
    def test_trigger_needs_its_ids(self):
        ids = mk.hash_ids("giruka.test", ["t_area", "t_relay"], set())
        with self.assertRaisesRegex(mk.MapError, "t_event"):
            mk.trigger_chain("t", [0.0, 0.0, 0.0], ids, flow="F", entry="E", half=[1, 1, 1])

    def test_trigger_links_area_relay_event(self):
        ids = mk.hash_ids("giruka.test", ["t_area", "t_relay", "t_event"], set())
        area, relay, event = mk.trigger_chain("t", [1.0, 2.0, 3.0], ids, flow="F", entry="E", half=[2, 2, 2],
                                              yaw=90.0)
        self.assertEqual(area["UnitConfigName"], "Area")
        self.assertEqual(area["LinksToObj"][0]["DestUnitHashId"], ids["t_relay"])
        self.assertEqual(relay["LinksToObj"][0]["DestUnitHashId"], ids["t_event"])
        self.assertEqual(event["!Parameters"]["EventFlowName"], "F")
        self.assertEqual(event["!Parameters"]["EventFlowEntryName"], "E")
        self.assertNotIn("Rotate", relay)
        self.assertIn("Rotate", area)
        area["!Parameters"]["Shape"] = "Sphere"           # (each trigger has its own copies)
        self.assertEqual(mk.AREA_BOX["Shape"], "Box")

    def test_gate_creates_and_deletes(self):
        ids = mk.hash_ids("giruka.test", ["g", "a", "b"], set())
        g = mk.flag_gate("g", [0.0, 0.0, 0.0], ids, "MyMod_On", creates=["a"], deletes=["b"])
        self.assertEqual(g["!Parameters"]["SaveFlag"], "MyMod_On")
        self.assertEqual([(l["DefinitionName"], l["DestUnitHashId"]) for l in g["LinksToObj"]],
                         [("Create", ids["a"]), ("Delete", ids["b"])])
        self.assertEqual(g["LinksToObj"][0]["!Parameters"], {"AppearFade": False})
        self.assertNotIn("!Parameters", g["LinksToObj"][1])
        with self.assertRaises(mk.MapError):
            mk.flag_gate("g", [0.0, 0.0, 0.0], ids, "MyMod_On")
        with self.assertRaisesRegex(mk.MapError, "nope"):
            mk.flag_gate("g", [0.0, 0.0, 0.0], ids, "MyMod_On", creates=["nope"])

    def test_yaz0_align(self):
        self.assertEqual(mk.yaz0_align(b"Yaz0" + struct.pack(">II", 100, 0x2000) + b"\0" * 4), 0x2000)
        self.assertEqual(mk.yaz0_align(b"SARC" + b"\0" * 12), 0)
        self.assertEqual(mk.yaz0_align(b"Yaz0"), 0)


class FlowTests(unittest.TestCase):
    def spec(self) -> dict:
        f = mk.FlowSpec("MyMod")
        f.entry("Talk", f.sub("init", "InitTalk", "InitTalk", {"Arg_Turn": 0, "Arg_Greeting": "FollowAISchedule"},
                              "hi"))
        f.act("hi", "MyNpc", "Demo_Talk", mk.talk_params("MyMod", "Hi", close=False), "ask")
        f.sw("ask", "EventSystemActor", "GeneralChoice2", None, {0: "yes", 1: None})
        f.act("yes", "MyNpc", "Demo_Talk", mk.talk_params("MyMod", "Yes", close=True))
        return f.spec

    def test_duplicates_refused(self):
        f = mk.FlowSpec("F")
        f.act("a", "X", "Y")
        with self.assertRaises(mk.MapError):
            f.act("a", "X", "Y")
        f.entry("E", "a")
        with self.assertRaises(mk.MapError):
            f.entry("E", "a")

    def test_problems(self):
        self.assertEqual(mk.check_flow(self.spec()), [])
        f = mk.FlowSpec("F")
        f.entry("E", f.act("a", "X", "Y", nxt="missing"))
        self.assertTrue(any("missing" in p for p in f.problems()))

    def test_subflows(self):
        self.assertEqual(mk.subflows_of(self.spec()), ["InitTalk"])

    def test_build_flow_reads_back(self):
        data = mk.build_flow(self.spec())
        self.assertEqual(evfl.dump(evfl.load(data)), data)

    def test_event_pack_carries_shared_flows_and_files(self):
        shared = {"EventFlow/InitTalk.bfevfl": (b"init-flow", 8), "EventFlow/Common.bfevfl": (b"common-flow", 8),
                  "EventFlow/Npc_X.bfevfl": (b"npc-flow", 8), "Sound/Talk.bars": (b"sounds", 0x20)}
        data = mk.build_flow(self.spec())
        pack, size = mk.event_pack("MyMod", data, shared, ["InitTalk"], source="Npc_X")
        files = mk.read_pack(pack)
        self.assertEqual(sorted(files), ["EventFlow/InitTalk.bfevfl", "EventFlow/MyMod.bfevfl"])
        self.assertEqual(files["EventFlow/MyMod.bfevfl"][0], data)
        self.assertEqual(len(mk.unyaz0(pack)), size)
        pack2, _ = mk.event_pack("MyMod", data, shared, ["InitTalk", "Common"], source="Npc_X", other_files=True)
        self.assertEqual(sorted(mk.read_pack(pack2)), ["EventFlow/Common.bfevfl", "EventFlow/InitTalk.bfevfl",
                                                       "EventFlow/MyMod.bfevfl", "Sound/Talk.bars"])
        with self.assertRaisesRegex(mk.MapError, "Npc_X's event pack has no EventFlow/Nope.bfevfl"):
            mk.event_pack("MyMod", data, shared, ["Nope"], source="Npc_X")

    def test_carried_flows(self):
        spec = self.spec()
        infos = mk.info_entries(spec)                       # (TALK_INFO: InitTalk + Common)
        self.assertEqual(mk.carried_flows({"MyMod": spec}, infos), {"MyMod": ["Common", "InitTalk"]})


if __name__ == "__main__":
    unittest.main()
