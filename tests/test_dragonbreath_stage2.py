"""Tests for Dragonbreath stage 2 (tools/dragonbreath_stage2.py): the recipe, the event flow (Rohan's three step talks,
the tempers, the re-buy, Daruk's first-draw scene), the quest, the cave map, the flags and texts, and a build on a
made-up game into stage-1-like package folders (temp folders). No game files."""
import copy
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import bcml_data as bd  # noqa: E402
import bcml_maps as bm  # noqa: E402
import bnp  # noqa: E402
import castle_build as cb  # noqa: E402
import dragonbreath_stage2 as s2  # noqa: E402
import evfl  # noqa: E402

SMITH = "Npc_Goron016"
BASE = "Weapon_Sword_Dragonbreath"
LEVELS = [BASE] + [f"{BASE}_{i}" for i in range(1, 5)]


def recipe() -> s2.Stage2:
    return s2.load_stage2()


def recipe_dict() -> dict:
    with open(os.path.join(s2.STAGE2, s2.RECIPE_JSON), encoding="utf-8") as f:
        return json.load(f)


def write_recipe(d: dict) -> str:
    tmp = tempfile.mkdtemp(prefix="db2-recipe-")
    with open(os.path.join(tmp, s2.RECIPE_JSON), "w", encoding="utf-8") as f:
        json.dump(d, f)
    return tmp


def events_after(spec: dict, start: str) -> list[str]:
    """Every event reachable from `start` (any branch)."""
    ev, seen, todo = spec["events"], [], [start]
    while todo:
        k = todo.pop()
        if k is None or k in seen:
            continue
        seen.append(k)
        e = ev[k]
        todo += [e.get("next")] if e["kind"] != "switch" else list(e["cases"].values())
    return seen


# --------------------------------------------------------------------------------------------- a made-up game

def _flow(name: str, entries: list[str]) -> bytes:
    f = evfl.Flow(name)
    for e in entries:
        i = f.act("EventSystemActor", "Demo_FlagON", evfl.params(IsWaitFinish=True, FlagName=f"{name}_{e}"))
        f.entry(e, i)
    return evfl.dump(f)


def _sarc(files: dict[str, bytes]) -> bytes:
    return bnp.sarc_build([(n, d, 8) for n, d in files.items()])


class Terrain:
    def height(self, x, z):
        return 527.0 + 0.01 * (x - 1960.0), 1.0


class FakeGame:
    """The parts of ak47_build.Game stage 2 reads."""

    def __init__(self):
        st = recipe()
        names = {o["actor"] for o in st.cave["objects"]} | {st.cave["rock"], st.cave["rock_far"], "TBox_Field_Stone",
                                                            s2.DARUK, SMITH}
        self._ai = {n: {"profile": "Item" if n.startswith("Item_") else "MapDynamicPassive"} for n in names}
        for k in ("A", "B", "C", "D", "I", "J"):
            self._ai[f"Item_Ore_{k}"] = {"profile": "Item"}
        self.taken = {zlib.crc32(b"giruka.dragonbreath/stage2/chest")}   # (our chest's first choice is taken)
        self.rohan = {"EventFlow/Npc_Goron016.bfevfl": _flow(SMITH, ["Talk", "GoronSword3Repair"]),
                      "EventFlow/InitTalk.bfevfl": _flow("InitTalk", ["InitTalk"]),
                      "EventFlow/Common.bfevfl": _flow("Common", ["DisableSaveAndWarp"]),
                      "Sound/Resource/Event_HandicraftCommon.bars": b"BARS-made-up"}
        timeline = {"Nodes": [{"AIName": "Action1", "StartTime": 0, "EndTime": 24}],
                    "TriggerEvents": [{"EvflEntry": "Ready_Npc_Goron017_Talk", "IsPauseOtherActors": True,
                                       "Trigger": "Talk"}]}
        self.quests = [{"Name": "GoronCityMini_BeatGolem", "Type": "Mini", "Steps": [
            {"Name": "Ready", "Actors": [{"Name": "Npc_Goron017", "DefaultTimeline": timeline}]}]}]
        self.eventinfo = {"Npc_Goron016<Talk>": {"exist_enable_camera_input": True, "is_timeline": False,
                                                 "mode": "Seamless", "slink_user": "Event_HandicraftCommon",
                                                 "subfile": [{"file": "InitTalk.bfevfl"}, {"file": "GetDemo.bfevfl"},
                                                             {"file": "Common.bfevfl"}]}}

    def actorinfo(self):
        return self._ai

    def unit(self, name):
        if name == "H-2_Dynamic":
            return {"Objs": [{"UnitConfigName": "Obj_Mineral_B_01", "HashId": 5,
                              "!Parameters": {"DropTable": "Normal", "SharpWeaponJudgeType": 0,
                                              "TraversePos": {"X": 1.0, "Y": 2.0, "Z": 3.0}}},
                             {"UnitConfigName": "TBox_Field_Stone", "HashId": 6,
                              "!Parameters": {"DropActor": "Weapon_Shield_003", "EnableRevival": False,
                                              "IsInGround": False, "SharpWeaponJudgeType": 0}}]}
        raise KeyError(name)

    def terrain(self):
        return Terrain()

    def hash_ids(self, log=None):
        return set(self.taken)

    def flags(self):
        return {"GoronCityMini_BeatGolem_Activated": {"DataName": "GoronCityMini_BeatGolem_Activated",
                                                      "IsOneTrigger": True, "IsSave": True, "ResetType": 0},
                "MainField_TBox_Field_Stone_123": {"DataName": "MainField_TBox_Field_Stone_123", "ResetType": 0,
                                                   "IsSave": True}}

    def rstb(self):
        return None

    def read(self, rel):
        if rel == "Event/Npc_Goron016.sbeventpack":
            return _sarc(self.rohan)
        if rel == "Pack/TitleBG.pack":
            return _sarc({"Quest/QuestProduct.sbquestpack": bd.byml_dump(self.quests)})
        raise FileNotFoundError(rel)

    def bootup(self):
        return bnp.Blob(_sarc({"Event/EventInfo.product.sbyml": bd.byml_dump(self.eventinfo)}))


# --------------------------------------------------------------------------------------------- tests

class RecipeTests(unittest.TestCase):
    def test_real_recipe_loads_and_fits(self):
        st = recipe()
        self.assertEqual(st.level_actors(), LEVELS)
        self.assertEqual(st.rebuy, {"Item_Ore_A": 8})               # the user's: re-buy for 8 diamonds
        self.assertEqual(st.levels[0]["attack"], 40)
        self.assertEqual([lv["guardian_ratio"] for lv in st.levels], sorted(lv["guardian_ratio"] for lv in st.levels))
        self.assertEqual(s2.text_problems(st), [])
        self.assertEqual(s2.placement_problems(st), [])
        self.assertEqual(s2.unit_of(*st.cave["origin"][::2]), "G-1")

    def test_bad_recipes_are_refused(self):
        for change, want in [
                (lambda d: d["levels"][1].update(suffix="_7"), "suffix"),
                (lambda d: d["ore_names"].pop("Item_Ore_J"), "ore_names"),
                (lambda d: d["cave"].update(unit="A-1_Dynamic"), "map section"),
                (lambda d: d["levels"][2].update(attack=10), "weaker"),
                (lambda d: d["rebuy"].update({"Bad Name": 1}), "item name"),
                (lambda d: d["cave"]["objects"].append(dict(d["cave"]["objects"][0])), "used twice"),
                (lambda d: d["texts"]["rohan"].pop("lost"), "lost")]:
            d = recipe_dict()
            change(d)
            folder = write_recipe(d)
            try:
                with self.assertRaises(s2.Stage2Error) as cm:
                    s2.load_stage2(folder)
                self.assertIn(want, str(cm.exception))
            finally:
                shutil.rmtree(folder, ignore_errors=True)

    def test_long_line_and_crowded_spots_are_found(self):
        d = recipe_dict()
        d["texts"]["daruk"].append("word " * 40)
        d["cave"]["objects"][1]["at"] = list(d["cave"]["objects"][0]["at"])
        d["cave"]["objects"][2]["at"] = [200.0, 0.0]
        folder = write_recipe(d)
        try:
            st = s2.load_stage2(folder)
            self.assertTrue(any("daruk" in p for p in s2.text_problems(st)))
            probs = s2.placement_problems(st)
            self.assertTrue(any("closer than" in p for p in probs))
            self.assertTrue(any("outside the corridor" in p for p in probs))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_level_recipes(self):
        import dataclasses

        @dataclasses.dataclass
        class R:
            actor: str
            stats: dict
            texts: dict
        base = R(BASE, {"attack": 40, "guardian_ratio": 0.25, "life": 1000}, {"name": "Dragonbreath", "desc": "x"})
        lv = s2.level_recipes(recipe(), base)
        self.assertEqual([r.actor for r in lv], LEVELS[1:])
        self.assertEqual([r.stats["attack"] for r in lv], [44, 48, 52, 56])
        self.assertEqual(lv[-1].stats["guardian_ratio"], 1.0)
        self.assertEqual(lv[0].stats["life"], 1000)
        self.assertEqual(lv[2].texts["name"], "Dragonbreath +3")


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.st = recipe()
        self.spec = s2.flow_spec(self.st)
        self.ev = self.spec["events"]
        cm = s2.cave_objects(self.st, set(), lambda x, z: 0.0, {}, chest_drop=BASE)
        self.flags = s2.all_flags(self.st, cm)

    def test_flow_is_whole(self):
        items = set(self.st.rebuy) | {k for x in self.st.levels[1:] for k in x["cost"]} | set(LEVELS)
        self.assertEqual(s2.flow_problems(self.st, self.spec, items=items, flags=self.flags), [])
        self.assertEqual(set(self.spec["entry_points"]),
                         {f"{s}_{SMITH}_Talk" for s in ("Ready", "Get", "Finish")} | {"FirstDraw"})

    def test_checks_find_mistakes(self):
        spec = copy.deepcopy(self.spec)
        spec["events"]["ready_tip1"]["params"]["MessageId"] = "EventFlowMsg/Dragonbreath:nope"
        spec["events"]["lonely"] = {"kind": "action", "actor": "Fader", "action": "Demo_FadeIn", "params": {},
                                    "next": None}
        probs = s2.flow_problems(self.st, spec, flags=self.flags)
        self.assertTrue(any("nope" in p for p in probs))
        self.assertTrue(any("lonely is never reached" in p for p in probs))
        self.assertTrue(any("isn't an item" in p for p in s2.flow_problems(self.st, spec, items=set())))

    def test_each_temper_takes_the_old_level_and_ores_and_gives_the_next(self):
        for i in range(1, 5):
            path = events_after(self.spec, f"smith_t{i}_offer")
            takes = [self.ev[k]["params"]["PorchItemName"] for k in path
                     if self.ev[k].get("action") == "Demo_DeletePorchItemInEquip"]
            self.assertEqual(takes, [LEVELS[i - 1]])
            ores = {self.ev[k]["params"]["PorchItemName"]: -self.ev[k]["params"]["Value"] for k in path
                    if self.ev[k].get("action") == "Demo_IncreasePorchItem"}
            self.assertEqual(ores, self.st.levels[i]["cost"])
            checked = {self.ev[k]["params"]["PorchItemName"]: self.ev[k]["params"]["Count"] for k in path
                       if self.ev[k].get("query") == "HasPorchItem"}
            self.assertEqual(checked, self.st.levels[i]["cost"])
            gives = [self.ev[k]["params"]["CheckTargetActorName"] for k in path
                     if self.ev[k]["kind"] == "subflow" and self.ev[k]["flow"] == "GetDemo"]
            self.assertEqual(gives, [LEVELS[i]])
            flags = [self.ev[k]["params"]["FlagName"] for k in path if self.ev[k].get("action") == "Demo_FlagON"]
            self.assertIn(f"Dragonbreath_Temper{i}", flags)
            self.assertEqual("Dragonbreath_Finish" in flags, i == 4)

    def test_rebuy_costs_8_diamonds_and_gives_the_highest_level_reached(self):
        path = events_after(self.spec, "smith_rb_offer")
        ores = {self.ev[k]["params"]["PorchItemName"]: -self.ev[k]["params"]["Value"] for k in path
                if self.ev[k].get("action") == "Demo_IncreasePorchItem"}
        self.assertEqual(ores, {"Item_Ore_A": 8})
        self.assertFalse(any(self.ev[k].get("action") == "Demo_DeletePorchItemInEquip" for k in path))
        gives = {self.ev[k]["params"]["CheckTargetActorName"] for k in path if self.ev[k]["kind"] == "subflow"
                 and self.ev[k]["flow"] == "GetDemo"}
        self.assertEqual(gives, set(LEVELS))
        # Temper4 on -> the +4 comes back
        self.assertEqual(self.ev["smith_rb_lvl4"]["cases"][1], "smith_rb_give4")
        self.assertEqual(self.ev["smith_rb_give4"]["params"]["CheckTargetActorName"], LEVELS[4])

    def test_no_rebuy_while_a_blade_is_in_the_pouch_or_the_world(self):
        # pouch: every level checked, highest first; none -> the world check; any there -> "away", no offer
        self.assertEqual(self.ev["smith_pouch4"]["cases"][1], "smith_maxed")
        self.assertEqual(self.ev["smith_pouch0"]["cases"][1], "smith_t1_offer")
        self.assertEqual(self.ev["smith_pouch0"]["cases"][0], "smith_world0")
        for i in range(5):
            w = self.ev[f"smith_world{i}"]
            self.assertEqual(w["query"], "CheckExistActor")
            self.assertEqual(w["params"]["ActorName"], LEVELS[i])
            self.assertEqual(w["cases"][1], "smith_away")
        self.assertEqual(self.ev["smith_world4"]["cases"][0], "smith_rb_offer")

    def test_boulder_breaker_stays_reachable(self):
        for p in ("ready", "get", "finish"):
            self.assertEqual(self.ev[f"{p}_bbq"]["params"]["FlagName"], "Get_GoronWeapon")
            bb = self.ev[f"{p}_bb"]
            self.assertEqual((bb["flow"], bb["entry"]), (SMITH, "GoronSword3Repair"))

    def test_ready_tip_starts_the_quest_once(self):
        path = events_after(self.spec, "ready_tip1")
        self.assertIn("ready_activate", path)
        self.assertEqual(self.ev["ready_told"]["cases"][1], "ready_remind")

    def test_first_draw(self):
        path = events_after(self.spec, "fd_out")
        on = [self.ev[k]["params"]["FlagName"] for k in path if self.ev[k].get("action") == "Demo_FlagON"]
        off = [self.ev[k]["params"]["FlagName"] for k in path if self.ev[k].get("action") == "Demo_FlagOFF"]
        self.assertEqual(on, ["Dragonbreath_DarukOn", "Dragonbreath_FirstDraw", "Dragonbreath_Activated"])
        self.assertEqual(off, ["Dragonbreath_DarukOn"])
        talks = [k for k in path if self.ev[k].get("actor") == s2.DARUK and self.ev[k]["action"] == "Demo_Talk"]
        self.assertEqual(len(talks), len(self.st.texts["daruk"]))
        self.assertTrue(self.ev[talks[-1]]["params"]["IsCloseMessageDialog"])
        self.assertEqual(self.ev[talks[0]]["params"]["ASName"], "Act_Happy_Wait")      # (the laugh)
        # the game's own spirit pattern (the King's): Wait, then the Ghost look, before he speaks; the
        # Demo_Disappear effect after; none of them waits (a missing pose or effect can't hold the scene)
        acts = [self.ev[k]["action"] for k in path if self.ev[k].get("actor") == s2.DARUK]
        self.assertEqual(acts[:3], ["Demo_TrigNullASPlay", "Demo_PlayASForDemo", "Demo_BecomeSpeaker"])
        self.assertEqual(acts[-1], "Demo_XLinkEventCreate")
        self.assertEqual(self.ev["fd_ghost"]["params"]["ASName"], "Ghost")
        for k in ("fd_wait_as", "fd_ghost", "fd_vanish"):
            self.assertFalse(self.ev[k]["params"]["IsWaitFinish"])
        self.assertLess(path.index("fd_on"), path.index("fd_settle"))                   # (made, then a wait)
        self.assertLess(path.index("fd_settle"), path.index("fd_ghost"))
        self.assertEqual(self.ev[path[-1]]["action"], "Demo_AutoSave")
        self.assertEqual(self.ev["fd_check"]["cases"][1], None)          # played already: nothing

    def test_spirit_look_can_be_left_out(self):
        d = recipe_dict()
        d["daruk_look"] = {"ghost": False, "laugh_as": "", "vanish_xlink": ""}
        folder = write_recipe(d)
        try:
            st = s2.load_stage2(folder)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        spec = s2.flow_spec(st)
        acts = {e["action"] for e in spec["events"].values() if e.get("actor") == s2.DARUK}
        self.assertEqual(acts, {"Demo_Talk", "Demo_BecomeSpeaker"})
        self.assertEqual(s2.flow_problems(st, spec), [])

    def test_flow_writes_and_reads_back(self):
        data = cb.build_flow(self.spec)
        f = evfl.load(data)
        self.assertEqual({e.name for e in f.entries}, set(self.spec["entry_points"]))
        self.assertEqual(evfl.dump(f), data)

    def test_unknown_calls(self):
        known = {"EventSystemActor": {"Demo_FlagON"}, "Npc_X": {"Demo_Talk"}}
        out = s2.unknown_calls(self.spec, known)
        self.assertTrue(any("Fader Demo_FadeOut" in p for p in out))
        self.assertFalse(any(f"{s2.DARUK} Demo_Talk" in p for p in out))   # (held to what a talking NPC does)


class DataTests(unittest.TestCase):
    def setUp(self):
        self.st = recipe()

    def test_texts(self):
        main, item = s2.texts_logs(self.st)
        msgs = main["USen"]["EventFlowMsg/Dragonbreath.msyt"]
        used = {e["params"]["MessageId"].split(":", 1)[1] for e in s2.flow_spec(self.st)["events"].values()
                if "MessageId" in (e.get("params") or {})}
        self.assertLessEqual(used, set(msgs))
        for n in (0, 1, 2, 3):
            self.assertIn(f"{n:04d}", msgs)                                 # the choices
        self.assertIn("8 Diamond", msgs["lost"]["contents"][0]["text"].replace("\n", " "))
        self.assertIn("4 Diamond and 2 Star Fragment", msgs["offer4"]["contents"][0]["text"].replace("\n", " "))
        self.assertEqual(msgs["daruk2"]["attributes"], s2.DARUK)
        q = main["USen"]["QuestMsg/QL_Dragonbreath.msyt"]
        self.assertEqual(set(q), {f"QL_Dragonbreath_{k}" for k in ("Name", "Desc", "Get", "Finish")})
        for v in q.values():                                             # (the quest log breaks lines by hand)
            self.assertTrue(all(len(x) <= s2.QUEST_WIDTH for x in v["contents"][0]["text"].split("\n")))
        self.assertIn("\n", q["QL_Dragonbreath_Desc"]["contents"][0]["text"])
        names = item["USen"]["ActorType/WeaponSmallSword.msyt"]
        self.assertEqual(names[f"{BASE}_4_Name"]["contents"][0]["text"], "Dragonbreath +4")
        s2.check_logs({"texts.json": json.dumps(main)})
        s2.check_logs({"texts.json": json.dumps(item)})

    def test_quest(self):
        nodes = [{"AIName": "Action1", "StartTime": 0, "EndTime": 24}]
        log = s2.quests_log(self.st, nodes)
        q = log["add"][0]
        self.assertEqual((q["Name"], q["Orderer"], q["Type"], q[bm.PREV]),
                         ("Dragonbreath", SMITH, "Mini", "GoronCityMini_BeatGolem"))
        self.assertEqual([s["Name"] for s in q["Steps"]], ["Ready", "Get", "Finish"])
        self.assertEqual([s["NextFlag"] for s in q["Steps"]],
                         ["Dragonbreath_Activated", "Dragonbreath_Get", "Dragonbreath_Finish"])
        self.assertEqual(q["StepDependencyFlags"][0]["DependencyFlag"], f"IsGet_{BASE}")
        for s in q["Steps"]:
            ev = s["Actors"][0]["DefaultTimeline"]["TriggerEvents"][0]
            self.assertEqual(ev["EvflEntry"], f"{s['Name']}_{SMITH}_Talk")
        s2.check_logs({"quests.yml": bd.to_text(log)})

    def test_cave_map(self):
        used = {zlib.crc32(b"giruka.dragonbreath/stage2/rock")}
        cm = s2.cave_objects(self.st, used, lambda x, z: 500.0, {"Obj_Mineral_B_01": {"DropTable": "Normal"}},
                             chest_drop=BASE)
        objs = cm.static + cm.dynamic
        ids = [int(o["HashId"]) for o in objs]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(set(ids) & used)
        by = {o["UnitConfigName"]: o for o in objs}
        rock, far = by["FldObj_CliffRedRock_Cave_A"], by["FldObj_CliffRedRock_Cave_A_Far"]
        self.assertEqual(rock["LinksToObj"][0]["DefinitionName"], "PlacementLOD")
        self.assertEqual(int(rock["LinksToObj"][0]["DestUnitHashId"]), int(far["HashId"]))
        self.assertAlmostEqual(rock["Rotate"], math.radians(225), places=5)
        self.assertEqual({o["UnitConfigName"] for o in cm.static}, {rock["UnitConfigName"], far["UnitConfigName"]})
        chest = by["TBox_Field_Stone"]
        self.assertEqual(chest["!Parameters"]["DropActor"], BASE)
        self.assertFalse(chest["!Parameters"]["EnableRevival"])
        happy = [o for o in objs if o["UnitConfigName"] == "Obj_Mineral_B_01"]
        self.assertEqual(len(happy), 6)
        self.assertTrue(all(o["!Parameters"]["DropTable"] == "Happy" for o in happy))
        self.assertEqual(len(cm.loose), 2)
        # the first-draw chain: gate(IsGet) creates the area; area -> relay -> event; FirstDraw deletes the area
        tags = {o["!Parameters"].get("SaveFlag"): o for o in objs if o["UnitConfigName"] == "LinkTagAnd"}
        area = by["Area"]
        self.assertEqual(tags[f"IsGet_{BASE}"]["LinksToObj"][0]["DefinitionName"], "Create")
        self.assertEqual(int(tags[f"IsGet_{BASE}"]["LinksToObj"][0]["DestUnitHashId"]), int(area["HashId"]))
        self.assertEqual(tags["Dragonbreath_FirstDraw"]["LinksToObj"][0]["DefinitionName"], "Delete")
        self.assertEqual(by["EventTag"]["!Parameters"]["EventFlowEntryName"], "FirstDraw")
        self.assertEqual(int(tags["Dragonbreath_DarukOn"]["LinksToObj"][0]["DestUnitHashId"]),
                         int(by[s2.DARUK]["HashId"]))
        # every spot is on the ground we gave (+ its lift)
        self.assertTrue(all(o["Translate"][1] >= 500.0 for o in cm.dynamic))
        s2.check_logs({"map.yml": bd.to_text(s2.map_log(self.st, cm))})

    def test_local_to_world(self):
        cave = {"origin": [100.0, 0.0, 200.0], "yaw": 0.0}
        self.assertEqual(s2.local_to_world(cave, 10, 0), (110.0, 200.0))
        cave["yaw"] = 90.0
        x, z = s2.local_to_world(cave, 10, 0)
        self.assertAlmostEqual(x, 100.0)
        self.assertAlmostEqual(z, 190.0)
        cave["yaw"] = 225.0
        x, z = s2.local_to_world(cave, 3, 4)
        self.assertAlmostEqual(math.hypot(x - 100, z - 200), 5.0)

    def test_flags(self):
        cm = s2.cave_objects(self.st, set(), lambda x, z: 0.0, {}, chest_drop=BASE)
        gd, sd = s2.flags_logs(self.st, cm, {"GoronCityMini_BeatGolem_Activated": {"IsOneTrigger": True}})
        names = set(gd["bool_data"]["add"])
        for k in ("Activated", "Get", "Finish", "FirstDraw", "DarukOn", "SmithSaw", "Temper1", "Temper4"):
            self.assertIn(f"Dragonbreath_{k}", names)
        for a in LEVELS[1:]:
            self.assertIn(f"IsGet_{a}", names)
        self.assertNotIn(f"IsGet_{BASE}", names)                             # (stage 1's)
        self.assertIn(f"MainField_TBox_Field_Stone_{cm.chest_hid}", names)
        self.assertEqual(sum(n.startswith("MainField_Item_Ore_A_") for n in names), 2)
        self.assertFalse(gd["bool_data"]["add"]["Dragonbreath_DarukOn"]["IsOneTrigger"])   # (turned off again)
        self.assertTrue(gd["bool_data"]["add"]["Dragonbreath_Temper1"]["IsOneTrigger"])
        self.assertEqual({e["DataName"] for e in sd["add"]}, names)
        s2.check_logs({"gamedata.yml": bd.to_text(gd), "savedata.yml": bd.to_text(sd)})


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="db2-build-")
        self.item = os.path.join(self.tmp, "giruka.dragonbreath-item")
        self.main = os.path.join(self.tmp, "botw-mod-loader.dragonbreath")
        # stage 1's logs already there
        os.makedirs(os.path.join(self.item, "logs"))
        os.makedirs(os.path.join(self.main, "logs"))
        spot = {"HashId": bd.U32(77), "Translate": [1.0, 2.0, 3.0], "UnitConfigName": BASE}
        with open(os.path.join(self.main, "logs", "map.yml"), "w", encoding="utf-8") as f:
            f.write(bd.to_text({"D-6_Dynamic": {"Objs": {"add": [spot]}}}))
        isget = {"DataName": f"IsGet_{BASE}", "HashValue": 1, "IsSave": True}
        with open(os.path.join(self.item, "logs", "gamedata.yml"), "w", encoding="utf-8") as f:
            f.write(bd.to_text({"bool_data": {"add": {isget["DataName"]: isget}, "del": []}}))
        with open(os.path.join(self.item, "logs", "savedata.yml"), "w", encoding="utf-8") as f:
            f.write(bd.to_text({"add": [{"DataName": isget["DataName"], "HashValue": 1}], "del": []}))
        with open(os.path.join(self.item, "logs", "texts.json"), "w", encoding="utf-8") as f:
            json.dump({"USen": {"ActorType/WeaponSmallSword.msyt": {f"{BASE}_Name": {
                "contents": [{"text": "Dragonbreath"}]}}}}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read_log(self, folder, name):
        with open(os.path.join(folder, "logs", name), encoding="utf-8") as f:
            return f.read()

    def test_build_into_stage1_folders(self):
        game = FakeGame()
        res = s2.build_stage2(game, recipe(), self.item, self.main)
        self.assertTrue(res.events and res.quest, res.notes)
        self.assertNotIn(zlib.crc32(b"giruka.dragonbreath/stage2/chest"), {res.cave.chest_hid})
        # the map keeps stage 1's spot and adds ours
        m = bd.from_text(self.read_log(self.main, "map.yml"))
        self.assertEqual(int(m["D-6_Dynamic"]["Objs"]["add"][0]["HashId"]), 77)
        self.assertEqual(len(m["G-1_Static"]["Objs"]["add"]), 2)
        dyn = m["G-1_Dynamic"]["Objs"]["add"]
        mineral = next(o for o in dyn if o["UnitConfigName"] == "Obj_Mineral_B_01")
        self.assertNotIn("TraversePos", mineral["!Parameters"])              # (the template's spot left out)
        self.assertEqual(mineral["!Parameters"]["DropTable"], "Happy")
        # the flags and texts join stage 1's
        gd = bd.from_text(self.read_log(self.item, "gamedata.yml"))
        self.assertIn(f"IsGet_{BASE}", gd["bool_data"]["add"])
        self.assertIn("Dragonbreath_Temper4", gd["bool_data"]["add"])
        sd = bd.from_text(self.read_log(self.item, "savedata.yml"))
        self.assertEqual(len(sd["add"]), len(gd["bool_data"]["add"]))
        tx = json.loads(self.read_log(self.item, "texts.json"))["USen"]["ActorType/WeaponSmallSword.msyt"]
        self.assertIn(f"{BASE}_Name", tx)
        self.assertIn(f"{BASE}_2_Name", tx)
        # the event pack: our flow, the game's shared flows and Rohan's own, his forging sounds
        with open(os.path.join(self.main, "01007EF00011E000", "romfs", "Event", "Dragonbreath.sbeventpack"),
                  "rb") as f:
            files = dict(bnp.Blob(f.read()).open().items())
        self.assertEqual(set(files), {"EventFlow/Dragonbreath.bfevfl", "EventFlow/InitTalk.bfevfl",
                                      "EventFlow/Common.bfevfl", "EventFlow/Npc_Goron016.bfevfl",
                                      "Sound/Resource/Event_HandicraftCommon.bars"})
        self.assertEqual(files["EventFlow/Npc_Goron016.bfevfl"][0], game.rohan["EventFlow/Npc_Goron016.bfevfl"])
        ei = bd.from_text(self.read_log(self.main, "eventinfo.yml"))
        self.assertEqual(ei[f"Dragonbreath<Get_{SMITH}_Talk>"]["slink_user"], "Event_HandicraftCommon")
        self.assertNotIn("slink_user", ei["Dragonbreath<FirstDraw>"])
        q = bd.from_text(self.read_log(self.main, "quests.yml"))
        self.assertEqual(q["add"][0]["Steps"][1]["Actors"][0]["DefaultTimeline"]["Nodes"][0]["AIName"], "Action1")
        # everything reads back with modman's mergers
        logs = {n: self.read_log(d, n) for d in (self.main, self.item) for n in os.listdir(os.path.join(d, "logs"))
                if n != "texts.json"}
        s2.check_logs(logs)

    def test_build_without_events_places_no_trigger(self):
        res = s2.build_stage2(FakeGame(), recipe(), self.item, self.main, events=False)
        self.assertFalse(res.events)
        m = bd.from_text(self.read_log(self.main, "map.yml"))
        objs = m["G-1_Dynamic"]["Objs"]["add"]
        kinds = {o["UnitConfigName"] for o in objs}
        self.assertNotIn("EventTag", kinds)
        self.assertNotIn("LinkTagAnd", kinds)
        self.assertNotIn(s2.DARUK, kinds)
        self.assertIn("TBox_Field_Stone", kinds)
        ids = {int(o["HashId"]) for o in objs}
        for o in objs:                                                   # (no link to an object not placed)
            for ln in o.get("LinksToObj") or []:
                self.assertIn(int(ln["DestUnitHashId"]), ids)
        self.assertFalse(os.path.exists(os.path.join(self.main, "logs", "quests.yml")))

    def test_evfl_not_trusted_places_the_cave_only(self):
        game = FakeGame()
        game.rohan["EventFlow/Common.bfevfl"] = b"BFEVFL\0\0" + bytes(40)   # (a flow evfl.py can't rebuild)
        res = s2.build_stage2(game, recipe(), self.item, self.main)
        self.assertFalse(res.events)
        self.assertTrue(any("event flows not built" in n for n in res.notes))
        m = bd.from_text(self.read_log(self.main, "map.yml"))
        kinds = {o["UnitConfigName"] for o in m["G-1_Dynamic"]["Objs"]["add"]}
        self.assertFalse(kinds & {"EventTag", "Area", "LinkTagAnd", s2.DARUK})
        self.assertFalse(os.path.exists(os.path.join(self.main, "01007EF00011E000")))   # (no event pack)
        gd = bd.from_text(self.read_log(self.item, "gamedata.yml"))
        self.assertIn(f"MainField_TBox_Field_Stone_{res.cave.chest_hid}", gd["bool_data"]["add"])

    def test_missing_actor_is_refused(self):
        game = FakeGame()
        del game._ai["Enemy_Keese_Fire"]
        with self.assertRaises(s2.Stage2Error):
            s2.build_stage2(game, recipe(), self.item, self.main)


class CommandTests(unittest.TestCase):
    def test_plan_needs_no_game(self):
        text = s2.plan_text(recipe())
        self.assertIn("8 Diamond", text)
        self.assertNotIn("problem:", text)


if __name__ == "__main__":
    unittest.main()
