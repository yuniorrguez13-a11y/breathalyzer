"""Tests for tools/bcml_maps.py (BCML's map.yml, mainstatic.yml, dstatic.yml and quests.yml). Every map unit, static
file, quest list, log and game folder here is made up in code (temp folders for the game folder tests)."""
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import bcml_maps as bm  # noqa: E402
import bnp  # noqa: E402
import wiiu_convert as wc  # noqa: E402

U = bm.U32


def log(name: str, text: str):
    return bm.read_log(name, text.encode())


def yaz(root) -> bytes:
    return wc.yaz0_compress(bm.byml_dump(root))


def load(data: bytes):
    return bm.byml_load(data)


def obj(h: int, name: str = "Obj_A", x: float = 0.0) -> dict:
    return {"HashId": U(h), "SRTHash": 0, "Translate": [x, 1.5, -2.0], "UnitConfigName": name}


def unit(objs: list, rails: list | None = None) -> dict:
    return {"LocationPosX": 0.0, "LocationPosZ": 0.0, "LocationSize": 1000.0, "Objs": objs, "Rails": rails or []}


def ids(root, sec: str = "Objs") -> list[int]:
    return [int(o["HashId"]) for o in root[sec]]


def quest(name: str, steps: int = 1) -> dict:
    return {"Name": name, "Location": "Field", "NotPostNote": False, "Orderer": "Someone", "Type": 0,
            "StepDependencyFlags": [], "Steps": [{"StepName": f"{name}_{k}"} for k in range(steps)]}


def table_strings(data: bytes, off: int) -> list[str]:
    n = int.from_bytes(data[off + 1:off + 4], "little")
    offs = struct.unpack_from(f"<{n + 1}I", data, off + 4)
    return [data[off + offs[k]:data.index(b"\0", off + offs[k])].decode() for k in range(n)]


# ---------------------------------------------------------------------------------------------------- BYML

class Byml(unittest.TestCase):
    def test_round_trip_keeps_every_type(self):
        doc = {"str": "text", "int": -7, "uint": U(0xFFFFFFFF), "float": 1.25, "bool": True, "null": None,
               "list": [1, U(2), "x", [], {}], "map": {"deep": {"deeper": [False]}},
               "i64": bm.I64(-(1 << 40)), "u64": bm.U64(1 << 63), "f64": bm.F64(0.1), "bin": b"\x01\x02\x03"}
        data = bm.byml_dump(doc)
        self.assertEqual(data[:4], b"YB\x02\x00")
        back = load(data)
        self.assertTrue(bm.byml_same(back, doc))
        self.assertIs(type(back["uint"]), U)
        self.assertIs(type(back["f64"]), bm.F64)
        self.assertEqual(wc.byml_load(data)["int"], ("int", 0xFFFFFFF9))   # the repo's other reader agrees

    def test_key_and_string_tables_are_sorted_and_unique(self):
        data = bm.byml_dump({"b": "zeta", "B": ["alpha", "zeta"], "a": {"b": "Alpha"}})
        keys_off, strings_off, root_off = struct.unpack_from("<III", data, 4)
        self.assertEqual(table_strings(data, keys_off), ["B", "a", "b"])
        self.assertEqual(table_strings(data, strings_off), ["Alpha", "alpha", "zeta"])
        self.assertEqual(root_off % 4, 0)
        n = int.from_bytes(data[root_off + 1:root_off + 4], "little")
        key_idx = [int.from_bytes(data[root_off + 4 + 8 * k:root_off + 7 + 8 * k], "little") for k in range(n)]
        self.assertEqual(key_idx, sorted(key_idx))

    def test_equal_containers_are_written_once_unless_asked_not_to(self):
        doc = {"a": {"x": [1.0, 2.0]}, "b": {"x": [1.0, 2.0]}, "c": {"x": [1.0, 2.5]}, "d": [U(1)], "e": [1]}
        shared, plain = bm.byml_dump(doc), bm.byml_dump(doc, share=False)
        self.assertLess(len(shared), len(plain))
        for data in (shared, plain):
            self.assertTrue(bm.byml_same(load(data), doc))
        root = struct.unpack_from("<I", shared, 12)[0]
        values = [struct.unpack_from("<I", shared, root + 8 + 8 * k)[0] for k in range(5)]
        self.assertEqual(values[0], values[1])                 # a and b: one node
        self.assertEqual(len(set(values)), 4)                  # c differs; d (uint) and e (int) differ by type

    def test_no_strings_means_no_string_table(self):
        data = bm.byml_dump([{"a": 1.0}])
        self.assertEqual(struct.unpack_from("<I", data, 8)[0], 0)
        self.assertTrue(bm.byml_same(load(data), [{"a": 1.0}]))

    def test_same_minds_types_and_32_bit_floats(self):
        self.assertFalse(bm.byml_same(1, U(1)))
        self.assertFalse(bm.byml_same(True, 1))
        self.assertTrue(bm.byml_same(0.1, bm.f32(0.1)))
        self.assertFalse(bm.byml_same(1.0, 1.5))
        self.assertTrue(bm.byml_same({"a": 1, "b": [2.0]}, {"b": [2.0], "a": 1}))
        self.assertFalse(bm.byml_same({"a": 1}, {"a": 1, "b": 2}))

    def test_refuses_values_that_dont_fit(self):
        for doc in ({"a": 1 << 31}, {"a": U(-1)}, {"a": object()}, "root", {1: 2}):
            with self.assertRaises(ValueError):
                bm.byml_dump(doc)

    def test_reads_yaz0_and_refuses_big_endian(self):
        self.assertTrue(bm.byml_same(load(yaz({"a": [1]})), {"a": [1]}))
        with self.assertRaises(wc.FormatError):
            load(wc.byml_convert(bm.byml_dump({"a": 1}), wc.BE))


# ---------------------------------------------------------------------------------------------------- BYML text

class Text(unittest.TestCase):
    def test_scalars_and_tags(self):
        doc = bm.byml_from_text("\n".join([
            "i: 12", "neg: -3", "hex: 0x10", "oct: 010", "f: 1.5", "exp: 1e-05", "inf: -.inf", "s: '12'",
            "q: \"a\\tb\"", "t: true", "n: null", "tilde: ~", "u: !u 0x80000000", "l: !l -5", "ul: !ul 0x10",
            "d: !f64 0.5", "b: !!binary aGk=", "word: Link's", "empty:", "colon: a:b"]))
        expect = {"i": 12, "neg": -3, "hex": 16, "oct": 8, "f": 1.5, "exp": bm.f32(1e-05), "inf": float("-inf"),
                  "s": "12", "q": "a\tb", "t": True, "n": None, "tilde": None, "u": U(0x80000000),
                  "l": bm.I64(-5), "ul": bm.U64(16), "d": bm.F64(0.5), "b": b"hi", "word": "Link's",
                  "empty": None, "colon": "a:b"}
        self.assertTrue(bm.byml_same(doc, expect), doc)

    def test_oead_style_wrapped_flow_and_quoted_keys(self):
        text = ("A-1_Static:\n"
                "  Objs:\n"
                "    add:\n"
                "    - '!Parameters': {IncrementSave: false, MakeSaveFlag: 0, NoChangeSignal: false,\n"
                "        SaveFlagOnOffType: 0}\n"
                "      HashId: !u 0x7\n"
                "      LinksToObj:\n"
                "      - DefinitionName: BasicSigOnOnly\n"
                "        DestUnitHashId: !u 0xa6172bfa\n"
                "      Translate: [-1234.5677, 100.0,\n"
                "        1e-05]\n"
                "      UnitConfigName: 'It''s a long name that libyaml wrapped over\n"
                "        two lines'\n"
                "    del: [!u 0x5, !u\n"
                "        0x6]\n"
                "    mod:\n"
                "      \"9\": {HashId: !u 0x9, UnitConfigName: plain words\n"
                "          over lines}\n"
                "  Rails: {add: [], del: [], mod: {}}\n")
        doc = bm.byml_from_text(text)
        o = doc["A-1_Static"]["Objs"]["add"][0]
        self.assertEqual(o["!Parameters"]["SaveFlagOnOffType"], 0)
        self.assertIs(type(o["HashId"]), U)
        self.assertEqual(o["UnitConfigName"], "It's a long name that libyaml wrapped over two lines")
        self.assertTrue(bm.byml_same(o["Translate"], [bm.f32(-1234.5677), 100.0, bm.f32(1e-05)]))
        self.assertEqual(doc["A-1_Static"]["Objs"]["del"], [5, 6])
        self.assertEqual(doc["A-1_Static"]["Objs"]["mod"]["9"]["UnitConfigName"], "plain words over lines")
        self.assertEqual(doc["A-1_Static"]["Rails"], {"add": [], "del": [], "mod": {}})

    def test_lists_comments_and_markers(self):
        text = "---\n# a comment\nx:\n- 1\n-   - a\n    - b\n- {}\ny:\n  - c  # trailing\nz: []\n...\n"
        self.assertEqual(bm.byml_from_text(text), {"x": [1, ["a", "b"], {}], "y": ["c"], "z": []})
        self.assertIsNone(bm.byml_from_text("\n# nothing\n"))

    def test_refuses_what_bcml_never_writes_or_damage(self):
        for text in ("a: &x 1", "a: *x", "a: !weird 1", "a: [1, 2", "a: {b: 1", "a: 1\n   b: 2", "a: 1\na: 2",
                     "a: !u -1", "a: !u 0x100000000", "? a\n: b", "a: 'unclosed", "a: [1] b",
                     "a: 99999999999999999999999"):
            with self.subTest(text=text), self.assertRaises(bm.LogError):
                bm.byml_from_text(text)

    def test_depth_is_capped(self):
        with self.assertRaises(bm.LogError):
            bm.byml_from_text("a: " + "[" * 100 + "]" * 100)
        deep = "".join(" " * k + f"k{k}:\n" for k in range(80)) + " " * 80 + "v: 1\n"
        with self.assertRaises(bm.LogError):
            bm.byml_from_text(deep)


# ---------------------------------------------------------------------------------------------------- map.yml

MAP_MOD1 = """A-1_Static:
  Objs:
    add:
    - {HashId: !u 0x7, SRTHash: 0, Translate: [7.0, 1.5, -2.0], UnitConfigName: First}
    mod:
      '9': {HashId: !u 0x9, SRTHash: 0, Translate: [99.0, 1.5, -2.0], UnitConfigName: Obj_A}
    del: []
  Rails: {add: [], mod: {}, del: []}
"""
MAP_MOD2 = """A-1_Static:
  Objs:
    add:
    - {HashId: !u 0x7, SRTHash: 0, Translate: [7.0, 1.5, -2.0], UnitConfigName: Second}
    - {HashId: !u 0x5, SRTHash: 0, Translate: [5.0, 1.5, -2.0], UnitConfigName: Again}
    mod: {}
    del: [!u 0x5]
  Rails: {add: [], mod: {}, del: []}
"""


class MapLog(unittest.TestCase):
    def setUp(self):
        self.game = {"A-1_Static": yaz(unit([obj(5), obj(9)]))}

    def test_two_mods_as_bcml_merges_them(self):
        out, notes = bm.merge_map(self.game, [log("map.yml", MAP_MOD1), log("map.yml", MAP_MOD2)])
        root = load(out["A-1_Static"])
        self.assertEqual(ids(root), [7, 9])
        self.assertEqual(root["Objs"][0]["UnitConfigName"], "Second")          # the later mod's addition
        self.assertEqual(root["Objs"][1]["Translate"][0], 99.0)                 # mod 1's change
        self.assertEqual(root["LocationSize"], 1000.0)
        self.assertEqual(out["A-1_Static"][:4], b"Yaz0")
        self.assertEqual(len(notes), 1)
        self.assertIn("0x00000005", notes[0])                                   # 5 was the game's: skipped

    def test_empty_logs_change_nothing(self):
        for text in ("", "{}", "A-1_Static: {Objs: {add: [], mod: {}, del: []}, Rails: {add: [], mod: {}, del: []}}"):
            out, notes = bm.merge_map(self.game, [log("map.yml", text)])
            self.assertEqual((out, notes), ({}, []))
        mod_same = "A-1_Static:\n  Objs:\n    mod:\n      '5': {HashId: !u 0x5, SRTHash: 0, Translate: [0.0, 1.5, -2.0], UnitConfigName: Obj_A}\n"
        self.assertEqual(bm.merge_map(self.game, [log("map.yml", mod_same)]), ({}, []))

    def test_rails_older_logs_and_unknown_changes(self):
        game = {"A-1_Static": yaz(unit([obj(1)], [{"HashId": U(40), "IsClosed": False}])),
                "A-1_Dynamic": bm.byml_dump({"Objs": [obj(2)], "Rails": []})}
        text = ("A-1_Static:\n  Rails:\n    add:\n    - {HashId: !u 0x20, IsClosed: true}\n    del: [!u 0x28]\n"
                "A-1_Dynamic:\n  add: []\n  mod:\n    '3': {HashId: !u 0x3, UnitConfigName: Lost}\n  del: [!u 0x2]\n")
        out, notes = bm.merge_map(game, [log("map.yml", text)])
        self.assertEqual(ids(load(out["A-1_Static"]), "Rails"), [32])
        self.assertEqual(ids(load(out["A-1_Static"])), [1])
        dyn = load(out["A-1_Dynamic"])
        self.assertEqual(ids(dyn), [3])                         # a change of an object the game lacks: added
        self.assertNotEqual(out["A-1_Dynamic"][:4], b"Yaz0")   # compressed only when the game's was
        self.assertEqual(notes, [])

    def test_adding_the_games_own_object_again_is_no_change(self):
        text = ("A-1_Static:\n  Objs:\n    add:\n    - {HashId: !u 0x9, SRTHash: 0, Translate: [0.0, 1.5, -2.0], "
                "UnitConfigName: Obj_A}\n")
        self.assertEqual(bm.merge_map(self.game, [log("map.yml", text)]), ({}, []))

    def test_sorted_by_hash_as_u32(self):
        text = ("A-1_Static:\n  Objs:\n    add:\n    - {HashId: !u 0x80000000, UnitConfigName: Big}\n"
                "    - {HashId: 6, UnitConfigName: Small}\n")
        out, _ = bm.merge_map(self.game, [log("map.yml", text)])
        self.assertEqual(ids(load(out["A-1_Static"])), [5, 6, 9, 0x80000000])

    def test_a_unit_the_game_lacks_is_named(self):
        text = "Z-9_Static:\n  Objs:\n    add:\n    - {HashId: !u 0x1}\n"
        out, notes = bm.merge_map(self.game, [log("map.yml", text)])
        self.assertEqual(out, {})
        self.assertIn("Z-9_Static", notes[0])
        self.assertEqual(bm.map_units([log("map.yml", text)]), ["Z-9_Static"])
        self.assertEqual(bm.map_target("A-1_Static"), "Pack/TitleBG.pack//Map/MainField/A-1/A-1_Static.smubin")
        self.assertEqual(bm.map_target("A-1_Dynamic"), "Map/MainField/A-1/A-1_Dynamic.smubin")

    def test_damaged_logs_are_refused(self):
        for text in ("- just a list", "A-1_Static: 5", "../x_Static:\n  Objs: {}",
                     "A-1_Static:\n  Objs:\n    add:\n    - {UnitConfigName: NoHash}",
                     "A-1_Static:\n  Objs:\n    mod:\n      abc: {HashId: 1}",
                     "A-1_Static:\n  Objs:\n    del: [x]", "A-1_Static:\n  Objs: {add: [], extra: 1}",
                     "A-1_Static:\n  Objs:\n    add:\n    - {HashId: !u 0x1, Big: !l 5}",
                     "A-1_Static:\n  Lights: {}", "A-1_Static: [unclosed"):
            with self.subTest(text=text), self.assertRaises(bm.LogError):
                log("map.yml", text)
        with self.assertRaises(bm.LogError):
            bm.read_log("map.yml", b"\xff\xfe\x00bad")


# ---------------------------------------------------------------------------------------------------- mainstatic.yml

def mainstatic() -> dict:
    return {"StartPos": [{"Map": "A-1", "PosName": "P", "PlayerState": "Ground",
                          "Translate": {"X": 1.2, "Y": -3.5, "Z": 0.0}},
                         {"Map": "A-2", "PosName": "R", "Translate": {"X": 5.0, "Y": 5.0, "Z": 5.0}}],
            "KorokLocation": [{"Flag": "K1", "Translate": {"X": 0.5, "Y": 0.5, "Z": 0.5}}]}


class MainStaticLog(unittest.TestCase):
    def test_ids_as_bcml_makes_them(self):
        e = mainstatic()["StartPos"][0]
        self.assertEqual(bm.static_id(e), "2-30P")      # ceil(1.2), ceil(-3.5), ceil(0.0), then PosName
        self.assertEqual(bm.static_id({"WarpDestPosName": "b", "WarpDestMapName": "a",
                                       "Translate": {"X": -0.5, "Y": 0.0, "Z": 2.0}}), "002a")
        self.assertEqual(bm.static_id({"lowName": "l", "UpperName": "u", "Translate": {"X": 0, "Y": 0, "Z": 0}}),
                         "000u")
        self.assertEqual(bm.static_id({"Flag": "K1", "Translate": {"X": 0.5, "Y": 0.5, "Z": 0.5}}), "111")
        self.assertIsNone(bm.static_id({"Translate": [1.0, 2.0, 3.0]}))

    def test_removal_and_addition(self):
        game = yaz(mainstatic())
        text = ("StartPos:\n"
                "  2-30P: {Map: A-1, PlayerState: Ground, PosName: P, Translate: {X: 1.2, Y: -3.5, Z: 0.0},"
                " remove: true}\n"
                "  10010Q: {Map: B, PosName: Q, Translate: {X: 10.0, Y: 0.0, Z: 10.0}}\n")
        new, notes = bm.merge_mainstatic(game, [log("mainstatic.yml", text)])
        root = load(new)
        self.assertEqual([e["PosName"] for e in root["StartPos"]], ["R", "Q"])
        self.assertTrue(bm.byml_same(root["KorokLocation"], mainstatic()["KorokLocation"]))
        self.assertEqual(notes, [])

    def test_changes_merge_into_the_entry_and_the_later_mod_wins(self):
        game = yaz(mainstatic())
        one = log("mainstatic.yml", "StartPos:\n  555R: {Map: A-2, PosName: R, Extra: 1, Translate: {X: 5.0, "
                                    "Y: 5.0, Z: 5.0}}\n")
        two = log("mainstatic.yml", "StartPos:\n  555R: {Map: A-9, PosName: R, Translate: {X: 5.0, Y: 5.0, "
                                    "Z: 5.0}}\n")
        root = load(bm.merge_mainstatic(game, [one, two])[0])
        r = root["StartPos"][1]
        self.assertEqual((r["Map"], r["Extra"]), ("A-9", 1))
        self.assertEqual(root["StartPos"][0]["PlayerState"], "Ground")

    def test_categories_the_game_lacks_are_ignored_and_named(self):
        game = yaz(mainstatic())
        new, notes = bm.merge_mainstatic(game, [log("mainstatic.yml", "NewThing:\n  000: {Translate: "
                                                                      "{X: 0.0, Y: 0.0, Z: 0.0}}\n")])
        self.assertIs(new, game)
        self.assertIn("NewThing", notes[0])

    def test_several_game_entries_with_one_id(self):
        a = {"Shape": "Box", "Translate": {"X": 0.2, "Y": 0.0, "Z": 0.0}}
        b = {"Shape": "Ball", "Translate": {"X": 0.7, "Y": 0.0, "Z": 0.0}}
        game = yaz({"NonAutoGenArea": [a, b]})
        gone = log("mainstatic.yml", "NonAutoGenArea:\n  '100': {Shape: Box, Translate: {X: 0.2, Y: 0.0, Z: 0.0}, "
                                     "remove: true}\n")
        root = load(bm.merge_mainstatic(game, [gone])[0])
        self.assertEqual([e["Shape"] for e in root["NonAutoGenArea"]], ["Ball"])     # the one it equals
        same = log("mainstatic.yml", "NonAutoGenArea:\n  '100': {Shape: Box, Translate: {X: 0.2, Y: 0.0, Z: 0.0}}\n")
        self.assertEqual(bm.merge_mainstatic(game, [same]), (game, []))
        moved = log("mainstatic.yml", "NonAutoGenArea:\n  '100': {Shape: Cone, Translate: {X: 0.9, Y: 0.0, Z: 0.0}}\n")
        new, notes = bm.merge_mainstatic(game, [moved])
        self.assertEqual([e["Shape"] for e in load(new)["NonAutoGenArea"]], ["Box", "Cone"])   # both kept
        self.assertIn("several entries", notes[0])

    def test_empty_log_and_damage(self):
        game = yaz(mainstatic())
        self.assertEqual(bm.merge_mainstatic(game, [log("mainstatic.yml", "")]), (game, []))
        self.assertEqual(bm.merge_mainstatic(game, [log("mainstatic.yml", "StartPos: {}")]), (game, []))
        for text in ("StartPos: [1, 2]", "StartPos:\n  abc: 5", "- x"):
            with self.subTest(text=text), self.assertRaises(bm.LogError):
                log("mainstatic.yml", text)


# ---------------------------------------------------------------------------------------------------- dstatic.yml

def dstatic() -> dict:
    return {"StartPos": [{"Map": "D1", "PosName": "Entrance_1", "Rotate": {"X": 0.0, "Y": 90.0, "Z": 0.0},
                          "Translate": {"X": 0.0, "Y": 0.0, "Z": 0.0}},
                         {"Map": "D3", "PosName": "Entrance_1", "Rotate": {"X": 0.0, "Y": 0.0, "Z": 0.0},
                          "Translate": {"X": 3.0, "Y": 3.0, "Z": 3.0}}]}


class DungeonStaticLog(unittest.TestCase):
    def test_moved_and_new_start_points(self):
        game = yaz(dstatic())
        text = ("D1___Entrance_1:\n  Translate: {X: 5.0, Y: 0.0, Z: 0.0}\n"
                "D2: {Map: D2, PosName: Entrance_1, Rotate: {X: 0.0, Y: 0.0, Z: 0.0}, "
                "Translate: {X: 1.0, Y: 2.0, Z: 3.0}}\n")
        new, notes = bm.merge_dstatic(game, [log("dstatic.yml", text)])
        sp = load(new)["StartPos"]
        self.assertEqual([e["Map"] for e in sp], ["D1", "D3", "D2"])
        self.assertEqual(sp[0]["Translate"]["X"], 5.0)
        self.assertEqual(sp[0]["Rotate"]["Y"], 90.0)
        self.assertEqual(notes, [])

    def test_across_mods_a_later_entry_replaces_inside_a_package_they_merge(self):
        game = yaz(dstatic())
        base = log("dstatic.yml", "D3___Entrance_1:\n  Translate: {X: 9.0, Y: 9.0, Z: 9.0}\n")
        option = log("dstatic.yml", "D3___Entrance_1:\n  Rotate: {X: 0.0, Y: 45.0, Z: 0.0}\n")
        d3 = load(bm.merge_dstatic(game, [[base, option]])[0])["StartPos"][1]
        self.assertEqual((d3["Translate"]["X"], d3["Rotate"]["Y"]), (9.0, 45.0))     # one package: both
        d3 = load(bm.merge_dstatic(game, [base, option])[0])["StartPos"][1]
        self.assertEqual((d3["Translate"]["X"], d3["Rotate"]["Y"]), (3.0, 45.0))     # two mods: the later's entry

    def test_new_point_without_map_is_skipped(self):
        game = yaz(dstatic())
        new, notes = bm.merge_dstatic(game, [log("dstatic.yml", "D9___Entrance_1:\n  Translate: {X: 1.0, Y: 1.0, "
                                                                "Z: 1.0}\n")])
        self.assertIs(new, game)
        self.assertIn("D9___Entrance_1", notes[0])

    def test_empty_log_and_damage(self):
        game = yaz(dstatic())
        self.assertEqual(bm.merge_dstatic(game, [log("dstatic.yml", "")]), (game, []))
        for text in ("D1___Entrance_1: 5", "- D1", "D1: [1]"):
            with self.subTest(text=text), self.assertRaises(bm.LogError):
                log("dstatic.yml", text)


# ---------------------------------------------------------------------------------------------------- quests.yml

def quests_yaml(add=(), mod=(), dele=()) -> str:
    def q(name, prev, steps=1, orderer="Someone"):
        s = ", ".join(f"{{StepName: {name}_{k}}}" for k in range(steps))
        return (f"{{Location: Field, Name: {name}, NotPostNote: false, Orderer: {orderer}, StepDependencyFlags: [],"
                f" Steps: [{s}], Type: 0" + (f", prev_quest: '{prev}'" if prev else "") + "}")
    out = "add:\n" + "".join(f"- {q(*a)}\n" for a in add) if add else "add: []\n"
    out += "mod:\n" + "".join(f"  {m[0]}: {q(*m)}\n" for m in mod) if mod else "mod: {}\n"
    return out + f"del: [{', '.join(dele)}]\n"


class QuestLog(unittest.TestCase):
    def setUp(self):
        self.game = yaz([quest("Q1"), quest("Q2")])

    def names(self, data: bytes) -> list[str]:
        return [q["Name"] for q in load(data)]

    def test_add_after_prev_delete_and_unchanged_mod(self):
        text = quests_yaml(add=[("Q3", "Q1")], mod=[("Q1", "--index_zero")], dele=["Q2"])
        new, notes = bm.merge_quests(self.game, [log("quests.yml", text)])
        root = load(new)
        self.assertEqual([q["Name"] for q in root], ["Q1", "Q3"])
        self.assertTrue(bm.byml_same(root[0], quest("Q1")))
        self.assertNotIn("prev_quest", root[1])
        self.assertEqual(notes, [])

    def test_two_mods_each_changing_one_quest_keep_both(self):
        # BCML logs every quest as changed (prev_quest); a quest equal to the game's isn't a change here
        one = log("quests.yml", quests_yaml(mod=[("Q1", "--index_zero", 3), ("Q2", "Q1")]))
        two = log("quests.yml", quests_yaml(mod=[("Q1", "--index_zero"), ("Q2", "Q1", 2)]))
        root = load(bm.merge_quests(self.game, [one, two])[0])
        self.assertEqual([len(q["Steps"]) for q in root], [3, 2])

    def test_the_later_change_wins(self):
        one = log("quests.yml", quests_yaml(mod=[("Q1", None, 3)]))
        two = log("quests.yml", quests_yaml(mod=[("Q1", None, 1, "Other")]))
        root = load(bm.merge_quests(self.game, [one, two])[0])
        self.assertEqual((root[0]["Orderer"], len(root[0]["Steps"])), ("Other", 1))

    def test_chained_additions_first_place_and_unknown_prev(self):
        text = quests_yaml(add=[("Q4", "Q3"), ("Q3", "Q1"), ("Q0", "--index_zero"), ("Q9", "Gone")])
        new, _ = bm.merge_quests(self.game, [log("quests.yml", text)])
        self.assertEqual(self.names(new), ["Q0", "Q1", "Q3", "Q4", "Q2", "Q9"])

    def test_a_change_of_a_quest_the_game_lacks_is_added_after_its_prev(self):
        new, _ = bm.merge_quests(self.game, [log("quests.yml", quests_yaml(mod=[("Q5", "Q1")]))])
        self.assertEqual(self.names(new), ["Q1", "Q5", "Q2"])

    def test_adding_a_quest_the_list_has_is_skipped(self):
        new, notes = bm.merge_quests(self.game, [log("quests.yml", quests_yaml(add=[("Q2", "Q1", 4)]))])
        self.assertIs(new, self.game)
        self.assertIn("Q2", notes[0])
        same = bm.merge_quests(self.game, [log("quests.yml", quests_yaml(add=[("Q2", "Q1")]))])
        self.assertEqual(same, (self.game, []))                # the very same quest: nothing to say

    def test_empty_log_and_damage(self):
        self.assertEqual(bm.merge_quests(self.game, [log("quests.yml", "")]), (self.game, []))
        self.assertEqual(bm.merge_quests(self.game, [log("quests.yml", "{add: [], mod: {}, del: []}")]),
                         (self.game, []))
        for text in ("- Q1", "add: [{Location: X}]", "del: [1]", "mod: {Q1: 5}", "add: x", "other: []",
                     "add:\n- {Name: Q7, prev_quest: 5}"):
            with self.subTest(text=text), self.assertRaises(bm.LogError):
                log("quests.yml", text)


# ---------------------------------------------------------------------------------------------------- interface

def rstb(entries: dict[str, int]) -> bytes:
    rows = sorted((zlib.crc32(k.encode()), v) for k, v in entries.items())
    return b"RSTB" + struct.pack("<II", len(rows), 0) + b"".join(struct.pack("<II", *r) for r in rows)


class Interface(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = Path(self.tmp.name) / "extracted"
        romfs = self.game / "romfs"
        self.static_raw = bm.byml_dump(unit([obj(5), obj(9)]))
        self.dyn_raw = bm.byml_dump({"Objs": [obj(2)], "Rails": []})
        self.quests_raw = bm.byml_dump([quest("Q1"), quest("Q2")])
        self.ms_raw = bm.byml_dump(mainstatic())
        self.ds_raw = bm.byml_dump(dstatic())
        title = bnp.sarc_build([("Map/MainField/A-1/A-1_Static.smubin", wc.yaz0_compress(self.static_raw), 4),
                                ("Quest/QuestProduct.sbquestpack", wc.yaz0_compress(self.quests_raw), 8)])
        boot = bnp.sarc_build([("Map/MainField/Static.smubin", wc.yaz0_compress(self.ms_raw), 4),
                               ("Map/CDungeon/Static.smubin", wc.yaz0_compress(self.ds_raw), 4)])
        sizes = {"Map/MainField/A-1/A-1_Static.mubin": bnp.round32(len(self.static_raw)) + 448,
                 "Map/MainField/A-1/A-1_Dynamic.mubin": bnp.round32(len(self.dyn_raw)) + 448,
                 "Quest/QuestProduct.bquestpack": bnp.round32(len(self.quests_raw)) + 448,
                 "Map/MainField/Static.mubin": 4 * len(self.ms_raw),
                 "Map/CDungeon/Static.mubin": 4 * len(self.ds_raw)}
        files = {"Pack/TitleBG.pack": title, "Pack/Bootup.pack": boot,
                 "Map/MainField/A-1/A-1_Dynamic.smubin": wc.yaz0_compress(self.dyn_raw),
                 "System/Resource/ResourceSizeTable.product.srsizetable": rstb(sizes)}
        for rel, data in files.items():
            p = romfs / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        self.sizes = sizes

    def tearDown(self):
        self.tmp.cleanup()

    def run_log(self, name: str, *texts: str, read=None) -> dict:
        diff = bm.combine(name, [log(name, t) for t in texts])
        return bm.apply(name, diff, str(self.game), read=read)

    def test_logs_and_unknown_names(self):
        self.assertEqual(set(bm.LOGS), {"map.yml", "mainstatic.yml", "dstatic.yml", "quests.yml"})
        for call in (lambda: bm.read_log("gamedata.yml", b""), lambda: bm.combine("drops.json", []),
                     lambda: bm.apply("texts.json", {}, str(self.game))):
            with self.assertRaises(bm.LogError):
                call()

    def test_map_targets_and_exact_sizes(self):
        text = ("A-1_Static:\n  Objs:\n    add:\n" + "".join(
            f"    - {{HashId: !u {0x100 + k}, UnitConfigName: Tree_{k}, Translate: [1.0, 2.0, 3.0]}}\n"
            for k in range(40)) + "A-1_Dynamic:\n  Objs:\n    del: [!u 0x2]\n")
        r = self.run_log("map.yml", text)
        st = "Pack/TitleBG.pack//Map/MainField/A-1/A-1_Static.smubin"
        dy = "Map/MainField/A-1/A-1_Dynamic.smubin"
        self.assertEqual(set(r["files"]), {st, dy})
        self.assertEqual(len(ids(load(r["files"][st]))), 42)
        new_len = len(bm.unyaz0(r["files"][st]))
        self.assertEqual(r["sizes"], {"Map/MainField/A-1/A-1_Static.mubin": bnp.round32(new_len) + 448})
        self.assertEqual(r["notes"], [])

    def test_quests_mainstatic_and_dstatic_targets(self):
        r = self.run_log("quests.yml", quests_yaml(add=[("Q3", "Q1", 30)]))
        self.assertEqual(list(r["files"]), ["Pack/TitleBG.pack//Quest/QuestProduct.sbquestpack"])
        grown = len(bm.unyaz0(r["files"]["Pack/TitleBG.pack//Quest/QuestProduct.sbquestpack"]))
        self.assertEqual(r["sizes"]["Quest/QuestProduct.bquestpack"], bnp.round32(grown) + 448)
        r = self.run_log("mainstatic.yml", "StartPos:\n  10010Q: {Map: B, PosName: Q, Translate: {X: 10.0, Y: 0.0, "
                                           "Z: 10.0}}\n")
        self.assertEqual(list(r["files"]), ["Pack/Bootup.pack//Map/MainField/Static.smubin"])
        grown = len(bm.unyaz0(r["files"]["Pack/Bootup.pack//Map/MainField/Static.smubin"]))
        old = len(self.ms_raw)
        self.assertEqual(r["sizes"], {"Map/MainField/Static.mubin": bnp.round32(-(-4 * old * grown // old))})
        r = self.run_log("dstatic.yml", "D1:\n  Translate: {X: 5.0, Y: 0.0, Z: 0.0}\n")
        self.assertEqual(list(r["files"]), ["Pack/Bootup.pack//Map/CDungeon/Static.smubin"])
        grown = len(bm.unyaz0(r["files"]["Pack/Bootup.pack//Map/CDungeon/Static.smubin"]))
        g = self.sizes["Map/CDungeon/Static.mubin"]
        need = bnp.needed_size(g, len(self.ds_raw), grown, False)
        self.assertEqual(r["sizes"], {"Map/CDungeon/Static.mubin": need} if need != g else {})
        r = self.run_log("dstatic.yml", "D1:\n  Rotate: {X: 0.0, Y: 45.0, Z: 0.0}\n")   # no bigger: no size
        self.assertEqual(len(bm.unyaz0(r["files"]["Pack/Bootup.pack//Map/CDungeon/Static.smubin"])),
                         len(self.ds_raw))
        self.assertEqual(r["sizes"], {})

    def test_empty_or_unchanged_logs_write_nothing(self):
        for name, text in (("map.yml", ""), ("quests.yml", quests_yaml(mod=[("Q1", "--index_zero")])),
                           ("mainstatic.yml", ""), ("dstatic.yml", "{}")):
            with self.subTest(name=name):
                self.assertEqual(self.run_log(name, text), {"files": {}, "sizes": {}, "notes": []})

    def test_a_mods_own_copy_is_merged_into(self):
        own = wc.yaz0_compress(bm.byml_dump([quest("Q1"), quest("Mine"), quest("Q2")]))
        r = self.run_log("quests.yml", quests_yaml(add=[("Q3", "Mine")]),
                         read=lambda t: own if t.endswith("QuestProduct.sbquestpack") else None)
        root = load(r["files"]["Pack/TitleBG.pack//Quest/QuestProduct.sbquestpack"])
        self.assertEqual([q["Name"] for q in root], ["Q1", "Mine", "Q3", "Q2"])

    def test_dlc_maps_are_named_not_merged(self):
        (self.game / "aoc0").mkdir()
        r = self.run_log("mainstatic.yml", "StartPos:\n  10010Q: {Map: B, PosName: Q, Translate: {X: 10.0, Y: 0.0, "
                                           "Z: 10.0}}\n")
        self.assertEqual(r["files"], {})
        self.assertIn("DLC", r["notes"][0])

    def test_no_game_file_is_named(self):
        (self.game / "romfs" / "Pack" / "TitleBG.pack").unlink()
        r = self.run_log("quests.yml", quests_yaml(add=[("Q3", "Q1")]))
        self.assertEqual(r["files"], {})
        self.assertIn("not merged", r["notes"][0])
        r = self.run_log("map.yml", "A-1_Static:\n  Objs:\n    del: [!u 0x5]\n")
        self.assertIn("A-1_Static", r["notes"][0])


if __name__ == "__main__":
    unittest.main()
