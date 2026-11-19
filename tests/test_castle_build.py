"""Tests for tools/castle_build.py (the castle date's builder). Made-up data only: every pack, flow, physics file
and actor list is built in memory from names and bytes of ours (the built mod goes to a temp folder); no game files
are read and nothing is added to a real library."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import aamp  # noqa: E402
from aamp import BOOL, INT, STRING64, STRING_REF, PList, PObject, name_hash  # noqa: E402
import bcml_maps as bm  # noqa: E402
import bnp  # noqa: E402
import castle_build as cb  # noqa: E402
import evfl  # noqa: E402
import modman as mm  # noqa: E402
import ukmm  # noqa: E402

OUR_PHYSICS = "MadeUp_ClothPhys"


def obj(**prm) -> PObject:
    o = PObject()
    for k, (t, v) in prm.items():
        o.set(k, t, v)
    return o


def actor_link(**users) -> bytes:
    base = {"ProfileUser": "NPC", "AIProgramUser": "Npc", "AttentionUser": "Dummy", "PhysicsUser": OUR_PHYSICS,
            "ASUser": "MadeUp_AS"}
    base.update(users)
    return aamp.dump(PList(objects={name_hash("LinkTarget"): obj(**{k: (STRING_REF, v) for k, v in base.items()})}))


def physics() -> bytes:
    """A made-up physics file: a header that turns cloth on and a Cloth list."""
    ps = PList(objects={name_hash("RigidBodySetHeader"): obj(use_cloth=(BOOL, True), use_rigid_body_set_num=(INT, 1))},
               lists={name_hash("Cloth"): PList(objects={name_hash("ClothHeader"): obj(cloth_num=(INT, 1))})})
    return aamp.dump(PList(lists={name_hash("ParamSet"): ps}))


def as_list(*names) -> bytes:
    defs = PList(objects={name_hash(f"ASDefine_{i}"): obj(Name=(STRING64, n)) for i, n in enumerate(names)})
    return aamp.dump(PList(lists={name_hash("ASDefines"): defs}))


def zelda_files() -> dict:
    return {f"Actor/ActorLink/{cb.ZELDA_SRC}.bxml": (actor_link(), 4),
            f"Actor/Physics/{OUR_PHYSICS}.bphysics": (physics(), 4),
            "Physics/Cloth/MadeUp_Skirt.hkcl": (b"made-up cloth", 8),
            f"Actor/ASList/{cb.ZELDA_SRC}.baslist": (as_list("Wait", "Hello", "Talk"), 4)}


def villager_files() -> dict:
    files = {n: (f"made-up attention {n}".encode(), 4) for n in cb.ATTENTION}
    files[f"Actor/ActorLink/{cb.VILLAGER}.bxml"] = (actor_link(AttentionUser="NpcStandard"), 4)
    return files


def made_up_flow(name: str, entries: list[str]) -> bytes:
    f = evfl.Flow(name)
    for e in entries:
        f.entry(e, f.act("EventSystemActor", "Demo_MadeUpWait", evfl.params(Frames=1)))
    return evfl.dump(f)


def event_files() -> dict:
    return {f"EventFlow/{cb.VILLAGER}.bfevfl": (made_up_flow(cb.VILLAGER, ["Talk"]), 8),
            "EventFlow/InitTalk.bfevfl": (made_up_flow("InitTalk", ["InitTalk"]), 8),
            "EventFlow/Common.bfevfl": (made_up_flow("Common", ["DisableSaveAndWarp", "EnableSaveAndWarp"]), 8)}


def pack(files: dict) -> bytes:
    return ukmm.yaz0(bnp.sarc_build([(n, d, a) for n, (d, a) in files.items()]))


def raw_files(data: bytes) -> dict:
    return {n: (c.data() if isinstance(c, bnp.Blob) else c) for n, (c, _) in bnp.Blob(data).open().items()}


def messages_pack() -> bytes:
    inner = bnp.sarc_build([("ActorType/NPC.msbt", b"made-up names", 4)])
    return bnp.sarc_build([("Message/Msg_USen.product.ssarc", ukmm.yaz0(inner), 4)])


class FakeRstb:
    def get(self, name):
        return 4096


class FakeGame(cb.Game):
    """The player's game, made up: its files in memory (every read goes to them)."""

    def __init__(self, files=None, actorinfo=None, eventinfo=None, used=(), flags=("GameClear",), rstb=True):
        super().__init__(tempfile.gettempdir())   # (never read: read() is replaced)
        self.files = {cb.FILES["zelda"]: pack(zelda_files()), cb.FILES["villager"]: pack(villager_files()),
                      cb.FILES["villager_events"]: pack(event_files()), cb.MESSAGES: messages_pack()}
        self.files.update(files or {})
        ai = {n: {"name": n, "profile": "MapObj"} for n in ("LinkTagAnd", "EventTag", "Area",
                                                             "TwnObj_Village_Hatenodoor_A_01", cb.VILLAGER)}
        ai[cb.ZELDA_SRC] = {"name": cb.ZELDA_SRC, "profile": "NPC", "instSize": 123, "elink": "MadeUp"}
        ai.update(actorinfo or {})
        self._cache.update({"actorinfo": ai, "hash_ids": set(used), "flags": set(flags),
                            "rstb": FakeRstb() if rstb else None,
                            "eventinfo": eventinfo if eventinfo is not None else {
                                f"{cb.VILLAGER}<Talk>": {"is_timeline": False, "mode": "Seamless", "madeup": 1,
                                                         "subfile": [{"file": "InitTalk.bfevfl"},
                                                                     {"file": "Common.bfevfl"}]}}})

    def read(self, rel):
        if rel not in self.files:
            raise cb.CastleError(f"no made-up {rel}")
        return self.files[rel]


def recipe() -> cb.Recipe:
    """The repo's own recipe (castle/: ours, no game data), read again each time (a test may change it)."""
    return cb.load_recipe()


def write_recipe(folder: str, layout=None, texts=None, toml=None) -> str:
    os.makedirs(folder, exist_ok=True)
    for name in ("mod.toml", "layout.json", "texts.json"):
        shutil.copy(os.path.join(cb.RECIPE, name), os.path.join(folder, name))
    if layout is not None:
        Path(folder, "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    if texts is not None:
        Path(folder, "texts.json").write_text(json.dumps(texts), encoding="utf-8")
    if toml is not None:
        Path(folder, "mod.toml").write_text(toml, encoding="utf-8")
    return folder


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="castle-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)


class Recipe(TempDir):
    def test_the_repos_recipe_is_good(self):
        r = cb.load_recipe()
        self.assertEqual(r.manifest["id"], "giruka.castle-date")
        self.assertEqual(r.manifest["authors"], ["giruka"])
        self.assertEqual(r.texts["actor"], cb.ACTOR)
        keys = [d["key"] for d in r.layout["doors"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(sum(1 for d in r.layout["doors"] if d.get("warp") == "ToStudy"), 1)   # the right door
        self.assertEqual(sum(1 for d in r.layout["doors"] if not d.get("warp")), 2)            # the wrong ones

    def test_bad_recipes_are_refused(self):
        base = json.loads(Path(cb.RECIPE, "layout.json").read_text(encoding="utf-8"))
        texts = json.loads(Path(cb.RECIPE, "texts.json").read_text(encoding="utf-8"))
        cases = {
            "no doors": ({**base, "doors": []}, None, "no doors"),
            "a bad spot": ({**base, "doors": [{"key": "d", "pos": [1, 2]}]}, None, "three numbers"),
            "nan": ({**base, "doors": [{"key": "d", "pos": [1, 2, float("nan")]}]}, None, "three numbers"),
            "unknown warp": ({**base, "doors": [{"key": "d", "pos": [1, 2, 3], "warp": "Nowhere",
                                                 "trigger": {"pos": [1, 2, 3], "half": [1, 1, 1]}}]}, None,
                             "isn't in warps"),
            "five choices": (None, {**texts, "talk": {**texts["talk"], "choices": [{"text": "x"}] * 5}},
                             "2 to 4 choices"),
            "someone else": (None, {**texts, "actor": "MadeUpPal"}, "not SwZelda"),
        }
        for i, (what, (layout, tx, msg)) in enumerate(cases.items()):
            with self.subTest(what):
                folder = write_recipe(os.path.join(self.tmp, str(i)), layout, tx)
                with self.assertRaises(cb.CastleError) as cm:
                    cb.load_recipe(folder)
                self.assertIn(msg, str(cm.exception))

    def test_a_recipe_that_cant_be_read(self):
        folder = write_recipe(os.path.join(self.tmp, "r"), toml="[mod\nid = ")
        with self.assertRaises(cb.CastleError):
            cb.load_recipe(folder)
        with self.assertRaises(cb.CastleError):
            cb.load_recipe(os.path.join(self.tmp, "missing"))


class Texts(unittest.TestCase):
    def test_greeting_choice_and_replies(self):
        msgs, by_reply = cb.talk_messages(recipe().texts)
        talk = msgs["Talk00"]
        self.assertEqual(talk["attributes"], cb.ACTOR)
        choice = talk["contents"][-1]["control"]
        self.assertEqual(choice["kind"], "choice")
        self.assertEqual(choice["choice_labels"], [0, 1, 2])
        self.assertEqual(choice["cancel_index"], 2)
        self.assertEqual([msgs[f"{i:04d}"]["contents"][0]["text"] for i in range(3)],
                         ["I'd find you anywhere.", "Lucky guess.", "There were other doors?"])
        self.assertEqual(by_reply, {"Talk01": "0000", "Talk02": "0001", "Talk03": "0002"})
        for label in ("Talk01", "Talk02", "Talk03"):
            self.assertEqual(msgs[label]["attributes"], cb.ACTOR)

    def test_lines_fit_the_talk_box(self):
        msgs, _ = cb.talk_messages(recipe().texts)
        for label, m in msgs.items():
            for part in m["contents"]:
                for line in part.get("text", "").split("\n"):
                    self.assertLessEqual(len(line), cb.TEXT_WIDTH, (label, line))

    def test_no_choices_closes_the_box(self):
        texts = recipe().texts
        texts["talk"] = {"label": "Talk00", "text": "Hello."}
        msgs, _ = cb.talk_messages(texts)
        self.assertEqual(list(msgs), ["Talk00"])
        self.assertEqual(msgs["Talk00"]["contents"], [{"text": "Hello."}])
        spec = cb.flow_specs(recipe().layout, texts)[cb.ACTOR]
        self.assertTrue(spec["events"]["greet"]["params"]["IsCloseMessageDialog"])
        self.assertIsNone(spec["events"]["greet"]["next"])

    def test_the_log_is_one_modman_reads(self):
        log = cb.texts_log(recipe().texts)
        self.assertEqual(log["USen"]["ActorType/NPC.msyt"][f"{cb.ACTOR}_Name"]["contents"], [{"text": "Zelda"}])
        cb.check_logs({"texts.json": json.dumps(log)})   # (raises when bcml_texts would refuse or cut it)
        no_voice = json.dumps(log)
        self.assertNotIn('"sound"', no_voice)


def walk(spec: dict, key: str) -> list[dict]:
    out = []
    while key is not None:
        e = spec["events"][key]
        out.append(e)
        key = e.get("next")
    return out


class Flows(unittest.TestCase):
    def setUp(self):
        r = recipe()
        self.layout, self.texts = r.layout, r.texts
        self.specs = cb.flow_specs(self.layout, self.texts)

    def test_both_flows_are_whole(self):
        self.assertEqual(set(self.specs), {cb.ACTOR, cb.DOOR_FLOW})
        for s in self.specs.values():
            self.assertEqual(cb.check_flow(s), [])

    def test_zelda_greets_asks_and_answers(self):
        z = self.specs[cb.ACTOR]
        self.assertEqual(z["entry_points"], {"Talk": "init"})
        self.assertEqual(z["events"]["init"]["flow"], "InitTalk")
        greet = z["events"]["greet"]
        self.assertEqual(greet["params"]["MessageId"], f"EventFlowMsg/{cb.ACTOR}:Talk00")
        self.assertEqual(greet["params"]["ASName"], "Hello")
        choice = z["events"]["choice"]
        self.assertEqual(choice["query"], "GeneralChoice3")
        self.assertEqual(choice["cases"], {0: "reply0", 1: "reply1", 2: "reply2"})
        for i in range(3):
            self.assertEqual(z["events"][f"reply{i}"]["params"]["MessageId"], f"EventFlowMsg/{cb.ACTOR}:Talk0{i + 1}")
            self.assertIsNone(z["events"][f"reply{i}"]["next"])

    def test_every_door_warp_blocks_saving_and_unblocks_it(self):
        door = self.specs[cb.DOOR_FLOW]
        self.assertEqual(set(door["entry_points"]), set(self.layout["warps"]))
        for name, key in door["entry_points"].items():
            steps = walk(door, key)
            kinds = [(e.get("action") or e.get("entry")) for e in steps]
            self.assertEqual(kinds, ["Demo_FadeOut", "DisableSaveAndWarp", "Demo_Idling",
                                     "Demo_WarpPlayerToDestination", "Demo_FadeIn", "EnableSaveAndWarp"], name)
            warp = steps[3]["params"]
            self.assertEqual([warp["DestinationX"], warp["DestinationY"], warp["DestinationZ"]],
                             self.layout["warps"][name]["pos"])
            self.assertEqual(steps[-1]["flow"], "Common")

    def test_check_flow_names_broken_links(self):
        bad = {"name": "MadeUp", "entry_points": {"Talk": "nope"},
               "events": {"a": {"kind": "action", "next": "gone"}, "b": {"kind": "switch", "cases": {}}}}
        problems = cb.check_flow(bad)
        self.assertEqual(len(problems), 3)

    def test_written_flows_read_back(self):
        for name, spec in self.specs.items():
            with self.subTest(name):
                data = cb.build_flow(spec)
                f = evfl.load(data)
                self.assertEqual(f.name, name)
                self.assertEqual({e.name for e in f.entries}, set(spec["entry_points"]))
                self.assertEqual(len(f.events), len(spec["events"]))
                self.assertEqual(evfl.dump(f), data)

    def test_event_info_and_carried_flows(self):
        infos = cb.event_infos(self.specs, {"mode": "Seamless", "madeup": 1, "subfile": [{"file": "InitTalk.bfevfl"},
                                                                                        {"file": "Common.bfevfl"}]})
        self.assertEqual(set(infos), {f"{cb.ACTOR}<Talk>", f"{cb.DOOR_FLOW}<ToStudy>", f"{cb.DOOR_FLOW}<ToRoom>"})
        self.assertEqual(infos[f"{cb.ACTOR}<Talk>"]["madeup"], 1)
        self.assertEqual(infos[f"{cb.DOOR_FLOW}<ToRoom>"]["subfile"], [{"file": "Common.bfevfl"}])
        carry = cb.carried_flows(self.specs, infos)
        self.assertEqual(carry, {cb.ACTOR: ["Common", "InitTalk"], cb.DOOR_FLOW: ["Common"]})
        self.assertEqual(cb.event_infos(self.specs, None)[f"{cb.ACTOR}<Talk>"], cb.TALK_INFO)
        cb.check_logs({"eventinfo.yml": cb.bd.to_text(infos)})

    def test_evfl_ready(self):
        shared = {n: (d, a) for n, (d, a) in event_files().items()}
        self.assertIsNone(cb.evfl_ready(shared))
        shared["EventFlow/Broken.bfevfl"] = (b"BFEVFL\0\0" + bytes(40), 8)
        self.assertIn("Broken.bfevfl", cb.evfl_ready(shared))
        self.assertIn("no flows", cb.evfl_ready({}))

    def test_event_pack_holds_the_flow_and_the_shared_ones(self):
        shared = event_files()
        data, raw_len = cb.event_pack("MadeUp", b"made-up flow", shared, ["Common", "InitTalk"])
        files = raw_files(data)
        self.assertEqual(set(files), {"EventFlow/MadeUp.bfevfl", "EventFlow/Common.bfevfl",
                                      "EventFlow/InitTalk.bfevfl"})
        self.assertEqual(files["EventFlow/Common.bfevfl"], shared["EventFlow/Common.bfevfl"][0])
        self.assertEqual(raw_len, ukmm.unpacked_size(data))
        with self.assertRaises(cb.CastleError):
            cb.event_pack("MadeUp", b"x", shared, ["NotThere"])

    def test_event_pack_can_carry_the_source_packs_other_files(self):
        shared = dict(event_files())
        shared["Sound/Resource/Event_MadeUp.bars"] = (b"BARS made up", 4)
        data, _ = cb.event_pack("MadeUp", b"made-up flow", shared, ["Common"], source="Npc_MadeUp",
                                other_files=True)
        files = raw_files(data)
        self.assertEqual(files["Sound/Resource/Event_MadeUp.bars"], b"BARS made up")
        self.assertNotIn("EventFlow/InitTalk.bfevfl", files)          # (only the flows asked for)
        data, _ = cb.event_pack("MadeUp", b"made-up flow", shared, ["Common"])
        self.assertNotIn("Sound/Resource/Event_MadeUp.bars", raw_files(data))
        with self.assertRaises(cb.CastleError) as e:
            cb.event_pack("MadeUp", b"x", shared, ["NotThere"], source="Npc_MadeUp")
        self.assertIn("Npc_MadeUp", str(e.exception))


class ZeldaPack(unittest.TestCase):
    def link(self, files):
        return aamp.load(files[f"Actor/ActorLink/{cb.ACTOR}.bxml"]).object("LinkTarget")

    def test_light_physics_and_talk_attention(self):
        p = cb.zelda_pack(zelda_files(), villager_files())
        files = raw_files(p.data)
        self.assertEqual(len(bnp.Blob(p.data).raw), p.raw_len)
        t = self.link(files)
        self.assertEqual(t.get_value("AttentionUser"), "NpcStandard")
        self.assertEqual(t.get_value("PhysicsUser"), cb.LIGHT_PHYSICS)
        self.assertEqual(t.get_value("AIProgramUser"), "Npc")
        self.assertNotIn(f"Actor/ActorLink/{cb.ZELDA_SRC}.bxml", files)
        self.assertNotIn(f"Actor/Physics/{OUR_PHYSICS}.bphysics", files)
        self.assertFalse([n for n in files if n.startswith("Physics/Cloth/")])
        light = aamp.load(files[f"Actor/Physics/{cb.LIGHT_PHYSICS}.bphysics"]).list("ParamSet")
        self.assertIsNone(light.list("Cloth"))
        self.assertIs(light.object("RigidBodySetHeader").get_value("use_cloth"), False)
        for n in cb.ATTENTION:
            self.assertEqual(files[n], villager_files()[n][0])
        self.assertIn(f"Actor/ASList/{cb.ZELDA_SRC}.baslist", files)
        self.assertEqual(p.refs[f"Actor/Physics/{cb.LIGHT_PHYSICS}.bphysics"], f"Actor/Physics/{OUR_PHYSICS}.bphysics")

    def test_cloth_on_keeps_her_physics(self):
        files = raw_files(cb.zelda_pack(zelda_files(), villager_files(), cloth=True).data)
        self.assertEqual(self.link(files).get_value("PhysicsUser"), OUR_PHYSICS)
        self.assertIn("Physics/Cloth/MadeUp_Skirt.hkcl", files)
        self.assertNotIn(f"Actor/Physics/{cb.LIGHT_PHYSICS}.bphysics", files)

    def test_missing_parts_are_named(self):
        v = villager_files()
        del v[cb.ATTENTION[1]]
        with self.assertRaises(cb.CastleError) as cm:
            cb.zelda_pack(zelda_files(), v)
        self.assertIn(cb.ATTENTION[1], str(cm.exception))
        z = zelda_files()
        del z[f"Actor/ActorLink/{cb.ZELDA_SRC}.bxml"]
        with self.assertRaises(cb.CastleError):
            cb.zelda_pack(z, villager_files())


class Map(unittest.TestCase):
    def setUp(self):
        self.layout = recipe().layout

    def by_key(self, objs):
        used: set = set()
        ids = {k: cb._hid(k, used) for k in cb.object_keys(self.layout, True)}   # (as map_objects picks them)
        return {k: next(o for o in objs if int(o["HashId"]) == int(v)) for k, v in ids.items()}

    def test_the_gate_reads_game_clear_and_never_writes_it(self):
        objs = cb.map_objects(self.layout, set(), True)
        gate = next(o for o in objs if o["!Parameters"].get("SaveFlag") == cb.GATE_FLAG)
        self.assertEqual(gate["UnitConfigName"], "LinkTagAnd")
        self.assertEqual(gate["!Parameters"]["MakeSaveFlag"], 0)
        self.assertFalse(gate["!Parameters"]["IncrementSave"])
        dests = [int(lk["DestUnitHashId"]) for o in objs for lk in o.get("LinksToObj") or []]
        self.assertNotIn(int(gate["HashId"]), dests)                 # no inputs
        made = {int(lk["DestUnitHashId"]) for lk in gate["LinksToObj"]}
        self.assertTrue(all(lk["DefinitionName"] == "Create" for lk in gate["LinksToObj"]))
        doors = {int(o["HashId"]) for o in objs if o["UnitConfigName"] == "TwnObj_Village_Hatenodoor_A_01"}
        zelda = {int(o["HashId"]) for o in objs if o["UnitConfigName"] == cb.ACTOR}
        areas = {int(o["HashId"]) for o in objs if o["UnitConfigName"] == "Area"}
        self.assertEqual((len(doors), len(zelda), len(areas)), (4, 1, 2))
        self.assertEqual(made, doors | zelda | areas)

    def test_door_triggers_launch_the_warps(self):
        objs = cb.map_objects(self.layout, set(), True)
        k = self.by_key(objs)
        for d in self.layout["doors"]:
            if not d.get("warp"):
                continue
            area, relay, event = k[f"{d['key']}_area"], k[f"{d['key']}_relay"], k[f"{d['key']}_event"]
            self.assertEqual(int(area["LinksToObj"][0]["DestUnitHashId"]), int(relay["HashId"]))
            self.assertEqual(int(relay["LinksToObj"][0]["DestUnitHashId"]), int(event["HashId"]))
            self.assertEqual(event["!Parameters"]["EventFlowName"], cb.DOOR_FLOW)
            self.assertEqual(event["!Parameters"]["EventFlowEntryName"], d["warp"])
            self.assertTrue(event["!Parameters"]["LaunchEventByOnSignal"])
            self.assertEqual(relay["!Parameters"]["MakeSaveFlag"], 0)
            self.assertNotIn("SaveFlag", relay["!Parameters"])

    def test_no_pick_ups_chests_or_enemies(self):
        for o in cb.map_objects(self.layout, set(), True):
            self.assertFalse(o["UnitConfigName"].startswith(("TBox_", "Item_", "Enemy_", "Weapon_", "Armor_")))

    def test_hash_ids_are_new_unique_and_stable(self):
        a = cb.map_objects(self.layout, set(), True)
        self.assertEqual(a, cb.map_objects(self.layout, set(), True))
        ids = [int(o["HashId"]) for o in a]
        self.assertEqual(len(ids), len(set(ids)))
        b = cb.map_objects(self.layout, set(ids[:3]), True)
        bids = {int(o["HashId"]) for o in b}
        self.assertFalse(bids & set(ids[:3]))
        self.assertEqual(len(bids), len(a))

    def test_without_events_only_doors_and_zelda(self):
        objs = cb.map_objects(self.layout, set(), False)
        self.assertEqual(sorted(o["UnitConfigName"] for o in objs),
                         sorted(["LinkTagAnd", cb.ACTOR] + ["TwnObj_Village_Hatenodoor_A_01"] * 4))

    def test_the_map_log_is_one_modman_reads(self):
        objs = cb.map_objects(self.layout, set(), True)
        for unit in ("static", "dynamic"):
            text = cb.bd.to_text({cb.unit_name(self.layout, unit): {"Objs": {"add": objs}}})
            cb.check_logs({"map.yml": text})
            self.assertTrue(bm.read_log("map.yml", text.encode()))
        self.assertEqual(cb.unit_name(self.layout, "static"), "E-4_Static")
        self.assertEqual(bm.map_target("E-4_Static").split("//")[0], cb.TITLEBG)


class Build(TempDir):
    def build(self, game=None, **kw):
        out = os.path.join(self.tmp, "castle")
        return out, cb.build_package(game or FakeGame(), recipe(), out, **kw)

    def test_the_whole_mod(self):
        out, res = self.build()
        romfs = os.path.join(out, cb.BASE_TID, "romfs")
        self.assertTrue(res.events)
        self.assertEqual(res.unit, "E-4_Static")
        self.assertEqual(len(res.objects), 12)
        self.assertEqual(Path(out, "mod.toml").read_text(encoding="utf-8"),
                         Path(cb.RECIPE, "mod.toml").read_text(encoding="utf-8"))
        info = json.loads(Path(out, "info.json").read_text(encoding="utf-8"))
        self.assertEqual((info["name"], info["platform"]), ("Castle date", "switch"))
        files = raw_files(Path(romfs, "Actor", "Pack", f"{cb.ACTOR}.sbactorpack").read_bytes())
        self.assertIn(f"Actor/ActorLink/{cb.ACTOR}.bxml", files)
        zp = raw_files(Path(romfs, "Event", f"{cb.ACTOR}.sbeventpack").read_bytes())
        self.assertEqual(set(zp), {f"EventFlow/{cb.ACTOR}.bfevfl", "EventFlow/InitTalk.bfevfl",
                                   "EventFlow/Common.bfevfl"})
        dp = raw_files(Path(romfs, "Event", f"{cb.DOOR_FLOW}.sbeventpack").read_bytes())
        self.assertEqual(set(dp), {f"EventFlow/{cb.DOOR_FLOW}.bfevfl", "EventFlow/Common.bfevfl"})
        self.assertEqual(evfl.load(zp[f"EventFlow/{cb.ACTOR}.bfevfl"]).name, cb.ACTOR)
        logs = {n: Path(out, "logs", n).read_text(encoding="utf-8") for n in os.listdir(os.path.join(out, "logs"))}
        self.assertEqual(set(logs), {"actorinfo.yml", "eventinfo.yml", "map.yml", "texts.json", "rstb.json"})
        cb.check_logs(logs)
        ai = cb.bd.read_log("actorinfo.yml", logs["actorinfo.yml"].encode())
        self.assertIn(cb.ACTOR, logs["actorinfo.yml"])
        self.assertTrue(ai)
        self.assertIn(f"{cb.ACTOR}<Talk>", logs["eventinfo.yml"])
        self.assertIn("madeup: 1", logs["eventinfo.yml"])          # (the villager's own entry, copied)
        sizes = json.loads(logs["rstb.json"])
        for n in (f"Actor/Pack/{cb.ACTOR}.bactorpack", f"Actor/Physics/{cb.LIGHT_PHYSICS}.bphysics",
                  f"Event/{cb.ACTOR}.beventpack", f"EventFlow/{cb.DOOR_FLOW}.bfevfl"):
            self.assertGreater(sizes[n], 0, n)
        self.assertTrue(any("guessed" in n for n in res.notes))

    def test_without_events(self):
        out, res = self.build(events=False)
        self.assertFalse(res.events)
        self.assertFalse(os.path.exists(os.path.join(out, cb.BASE_TID, "romfs", "Event")))
        self.assertFalse(os.path.exists(os.path.join(out, "logs", "eventinfo.yml")))
        self.assertEqual(len(res.objects), 6)
        self.assertTrue(any("--no-events" in n for n in res.notes))

    def test_a_flow_evfl_cant_rebuild_leaves_events_out(self):
        files = event_files()
        files["EventFlow/Broken.bfevfl"] = (b"BFEVFL\0\0" + bytes(40), 8)
        out, res = self.build(FakeGame({cb.FILES["villager_events"]: pack(files)}))
        self.assertFalse(res.events)
        self.assertTrue(any("Broken.bfevfl" in n for n in res.notes))
        self.assertFalse(any(o["UnitConfigName"] == "EventTag" for o in res.objects))

    def test_dynamic_unit_and_no_size_table(self):
        out, res = self.build(FakeGame(rstb=False), unit="dynamic")
        self.assertEqual(res.unit, "E-4_Dynamic")
        self.assertIn("E-4_Dynamic", Path(out, "logs", "map.yml").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(Path(out, "logs", "rstb.json").read_text(encoding="utf-8")), {})
        self.assertTrue(any("size table" in n for n in res.notes))

    def test_refusals(self):
        os.makedirs(os.path.join(self.tmp, "castle", "x"))
        with self.assertRaises(cb.CastleError):
            self.build()                                            # (not empty)
        with self.assertRaises(cb.CastleError):
            cb.build_package(FakeGame(), recipe(), os.path.join(self.tmp, "other"), unit="sideways")
        game = FakeGame()
        del game._cache["actorinfo"][cb.ZELDA_SRC]
        with self.assertRaises(cb.CastleError):
            cb.build_package(game, recipe(), os.path.join(self.tmp, "third"))

    def test_another_titlebg_mod_is_named(self):
        lib = mm.Library(os.path.join(self.tmp, "lib"), mods=[
            {"id": "madeup-body", "name": "Made-up body", "enabled": True},
            {"id": "madeup-off", "name": "Off", "enabled": False}])
        for mid in ("madeup-body", "madeup-off"):
            p = Path(lib.mods_dir, mid, cb.BASE_TID, "romfs", "Pack")
            p.mkdir(parents=True)
            (p / "TitleBG.pack").write_bytes(b"made up")
        self.assertEqual(cb.titlebg_mods(lib, "giruka.castle-date"), ["Made-up body"])
        out = os.path.join(self.tmp, "castle")
        res = cb.build_package(FakeGame(), recipe(), out, lib=lib)
        self.assertTrue(any("Made-up body" in n and "file by file" in n for n in res.notes))
        self.assertFalse(os.path.exists(os.path.join(out, cb.BASE_TID, "romfs", "Pack")))   # (never a copy of theirs)


class Library(TempDir):
    def lib(self, mods):
        lib = mm.Library(os.path.join(self.tmp, "lib"), mods=[dict(m) for m in mods])
        os.makedirs(lib.mods_dir, exist_ok=True)
        return lib

    def fake_add(self, lib):
        def add(lib_, folder):
            e = {"id": "giruka.castle-date", "name": "Castle date", "enabled": True}
            lib_.mods.append(e)
            return e, None, ["made-up note"]
        return add

    def test_a_rebuild_keeps_its_place_and_switch(self):
        lib = self.lib([{"id": "a", "name": "A", "enabled": True},
                        {"id": "giruka.castle-date", "name": "Castle date", "enabled": False},
                        {"id": "b", "name": "B", "enabled": True}])

        def remove(lib_, ref):
            lib_.mods[:] = [m for m in lib_.mods if m["id"] != ref]
        with mock.patch.object(mm, "remove_mod", side_effect=remove) as rm_, \
                mock.patch.object(mm, "add_mod", side_effect=self.fake_add(lib)):
            m, notes = cb.add_to_library(lib, "folder", "giruka.castle-date")
        rm_.assert_called_once()
        self.assertEqual([x["id"] for x in lib.mods], ["a", "giruka.castle-date", "b"])
        self.assertFalse(m["enabled"])
        self.assertEqual(notes, ["made-up note"])

    def test_a_first_build_goes_last(self):
        lib = self.lib([{"id": "a", "name": "A", "enabled": True}])
        with mock.patch.object(mm, "add_mod", side_effect=self.fake_add(lib)):
            m, _ = cb.add_to_library(lib, "folder", "giruka.castle-date")
        self.assertEqual([x["id"] for x in lib.mods], ["a", "giruka.castle-date"])
        self.assertTrue(m["enabled"])

    def test_a_folder_the_library_doesnt_list(self):
        lib = self.lib([])
        os.makedirs(os.path.join(lib.mods_dir, "giruka.castle-date"))
        with mock.patch.object(mm, "add_mod") as add:
            with self.assertRaises(cb.CastleError):
                cb.add_to_library(lib, "folder", "giruka.castle-date")
        add.assert_not_called()

    def test_find_game(self):
        with self.assertRaises(cb.CastleError) as cm:
            cb.find_game(os.path.join(self.tmp, "no-game"))
        self.assertIn("--game", str(cm.exception))
        os.makedirs(os.path.join(self.tmp, "sw", "extracted", "romfs"))
        with self.assertRaises(cb.CastleError) as cm:
            cb.find_game(os.path.join(self.tmp, "sw"))
        self.assertIn(cb.FILES["zelda"], str(cm.exception))


class Check(TempDir):
    def check(self, game, recipe_dir=cb.RECIPE):
        with mock.patch.object(cb, "find_game", return_value=game):
            return cb.check(os.path.join(self.tmp, "no-library"), recipe_dir=recipe_dir)

    def test_all_good(self):
        self.assertEqual(self.check(FakeGame()), [])

    def test_problems_are_named(self):
        layout = json.loads(Path(cb.RECIPE, "layout.json").read_text(encoding="utf-8"))
        layout["door_actor"] = "Item_MadeUpFlower"
        folder = write_recipe(os.path.join(self.tmp, "r"), layout)
        game = FakeGame(actorinfo={"Item_MadeUpFlower": {"name": "Item_MadeUpFlower", "profile": "Item"}},
                        flags=(), eventinfo={f"{cb.ACTOR}<Talk>": {}})
        problems = "\n".join(self.check(game, folder))
        self.assertIn("pick-up, chest or enemy", problems)
        self.assertIn("GameClear isn't in your game's game data", problems)
        self.assertIn(f"already has {cb.ACTOR}<Talk>", problems)

    def test_an_animation_she_lacks(self):
        texts = json.loads(Path(cb.RECIPE, "texts.json").read_text(encoding="utf-8"))
        texts["talk"]["anim"] = "MadeUpDance"
        folder = write_recipe(os.path.join(self.tmp, "r"), texts=texts)
        self.assertIn("MadeUpDance", "\n".join(self.check(FakeGame(), folder)))


class CommandLine(TempDir):
    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cb, "_low_priority"), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cb.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_no_add_needs_out(self):
        code, _, err = self.run_main("--library", os.path.join(self.tmp, "lib"), "build", "--no-add")
        self.assertEqual(code, 2)
        self.assertIn("--out", err)

    def test_where_lists_the_files(self):
        code, out, _ = self.run_main("--library", os.path.join(self.tmp, "lib"), "--game",
                                     os.path.join(self.tmp, "sw"), "where")
        self.assertEqual(code, 1)
        self.assertEqual(out.count("MISSING"), len(cb.FILES))

    def test_build_without_a_library(self):
        code, _, err = self.run_main("--library", os.path.join(self.tmp, "lib"), "build")
        self.assertEqual(code, 2)
        self.assertIn("modman.py init", err)


if __name__ == "__main__":
    unittest.main()
