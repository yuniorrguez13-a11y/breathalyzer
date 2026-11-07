"""Tests for tools/bcml_data.py (BCML's actorinfo, gamedata, savedata, eventinfo, effects, areadata and residents
logs). Every log, game file, pack and size table here is made up, built in code (game folders in temp folders)."""
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import bcml_data as bd  # noqa: E402
import bnp  # noqa: E402
import romfs_mods as rm  # noqa: E402
import ukmm  # noqa: E402
import wiiu_convert as wc  # noqa: E402

U32, LogError = bd.U32, bd.LogError


def crc(s: str) -> int:
    return zlib.crc32(s.encode())


def sarc(files: dict, yaz0: bool = True) -> bytes:
    """A made-up archive of BYML documents (dict values) or raw bytes."""
    raw = bnp.sarc_build([(n, v if isinstance(v, bytes) else bd.byml_dump(v), 4) for n, v in files.items()])
    return ukmm.yaz0_stored(raw) if yaz0 else raw


def unsarc(stored: bytes) -> dict:
    raw = wc.yaz0_decompress(stored)[0] if stored[:4] == b"Yaz0" else stored
    return dict(wc.sarc_files(raw))


def docs(stored: bytes) -> dict:
    return {n: bd.byml_load(d) for n, d in unsarc(stored).items()}


def flag(name: str, init=0, **more) -> dict:
    return {"DataName": name, "DeleteRev": -1, "HashValue": bd._signed(crc(name)), "InitValue": init,
            "IsEventAssociated": False, "IsOneTrigger": False, "IsProgramReadable": True,
            "IsProgramWritable": True, "IsSave": True, "MaxValue": True, "MinValue": False, "ResetType": 0, **more}


def log(name: str, text: str):
    return bd.read_log(name, text.encode())


def names_by_hash_bit() -> tuple[str, str]:
    """Two made-up actor names: one hashes below 2^31, the other at or above it."""
    low = next(f"MadeUp_{i}" for i in range(100) if crc(f"MadeUp_{i}") < 1 << 31)
    high = next(f"MadeUp_{i}" for i in range(100) if crc(f"MadeUp_{i}") >= 1 << 31)
    return low, high


# ---------------------------------------------------------------------------------------------------- BYML

class BymlTest(unittest.TestCase):
    def test_round_trip_keeps_every_type(self):
        doc = {"i": -5, "f": 1.5, "u": U32(0x80000000), "b": True, "s": "text", "n": None,
               "l": bd.I64(-(1 << 40)), "ul": bd.U64(1 << 63), "d": bd.F64(0.1), "bin": b"\x01\x02\x03",
               "arr": [1, "x", [], {}, [2.0]], "empty": {}}
        back = bd.byml_load(bd.byml_dump(doc))
        self.assertTrue(bd.same(back, doc))
        self.assertIsInstance(back["u"], U32)
        self.assertIsInstance(back["d"], bd.F64)
        self.assertIs(type(back["i"]), int)

    def test_tables_sorted_and_dict_entries_by_key(self):
        data = bd.byml_dump({"zeta": "b", "Alpha": "a", "mid": ["c", "a"]})
        self.assertEqual(data[:4], b"YB\x02\x00")
        keys_off, strs_off, root = struct.unpack_from("<III", data, 4)

        def table(off):
            n = int.from_bytes(data[off + 1:off + 4], "little")
            offs = struct.unpack_from(f"<{n + 1}I", data, off + 4)
            return [data[off + offs[k]:data.index(b"\0", off + offs[k])] for k in range(n)]
        self.assertEqual(table(keys_off), [b"Alpha", b"mid", b"zeta"])
        self.assertEqual(table(strs_off), [b"a", b"b", b"c"])
        self.assertEqual(data[root], 0xC1)
        idx = [int.from_bytes(data[root + 4 + 8 * k:root + 7 + 8 * k], "little") for k in range(3)]
        self.assertEqual(idx, sorted(idx))

    def test_no_strings_means_no_string_table(self):
        data = bd.byml_dump([{"v": 1.0}])
        self.assertEqual(struct.unpack_from("<I", data, 8)[0], 0)
        self.assertTrue(bd.same(bd.byml_load(data), [{"v": 1.0}]))

    def test_identical_containers_are_written_once(self):
        one = {"is_timeline": False, "mode": "Seamless"}
        shared = bd.byml_dump({f"e{i}": dict(one) for i in range(50)})
        alone = bd.byml_dump({"e0": dict(one)})
        self.assertLess(len(shared), len(alone) + 50 * 8 + 50 * 8)
        self.assertTrue(bd.same(bd.byml_load(shared), {f"e{i}": one for i in range(50)}))

    def test_yaz0_document_loads(self):
        self.assertEqual(bd.byml_load(ukmm.yaz0_stored(bd.byml_dump({"a": 1}))), {"a": 1})

    def test_damaged_documents_are_refused_not_crashed(self):
        good = bd.byml_dump({"a": [1, {"b": "c"}]})
        damaged = [b"", b"YB", good[:20], b"XX" + good[2:], good[:-6]]
        loop = bytearray(bd.byml_dump({"a": []}))           # the array's value made to point back at the root
        root = struct.unpack_from("<I", loop, 12)[0]
        loop[root + 3 + 4] = 0xC1
        struct.pack_into("<I", loop, root + 8, root)
        damaged.append(bytes(loop))
        for d in damaged:
            with self.subTest(d=d[:12]):
                with self.assertRaises(wc.FormatError):
                    bd.byml_load(d)

    def test_dump_refuses_what_byml_cant_hold(self):
        for bad in ({"a": 1 << 31}, {"a": U32(-1)}, {"a": "x\0y"}, {1: "a"}, {"a": 1e300}, {"a": object()}, "x"):
            with self.subTest(bad=bad):
                with self.assertRaises((ValueError, TypeError)):
                    bd.byml_dump(bad)

    def test_same_tells_types_apart(self):
        self.assertFalse(bd.same(1, True))
        self.assertFalse(bd.same(1, U32(1)))
        self.assertFalse(bd.same(1, 1.0))
        self.assertTrue(bd.same(0.1, bd.f32(0.1)))
        self.assertTrue(bd.same({"a": [1, 2]}, {"a": [1, 2]}))
        self.assertFalse(bd.same({"a": [1, 2]}, {"a": [2, 1]}))


# ---------------------------------------------------------------------------------------------------- BYML text

class TextTest(unittest.TestCase):
    def test_typed_scalars(self):
        v = bd.from_text("i: 12\nneg: -3\nhex: 0x10\noct: 010\nf: 1.5\ne: 1e-05\ninf: -.inf\nu: !u 0x80000000\n"
                         "l: !l -5\nul: !ul 0x10\nd: !f64 0.25\nt: true\nn: null\ns: '12'\nq: \"true\"\nw: word\n"
                         "b: !!binary AQID\n")
        self.assertEqual((v["i"], v["neg"], v["hex"], v["oct"]), (12, -3, 16, 8))
        self.assertIs(type(v["i"]), int)
        self.assertEqual(v["f"], 1.5)
        self.assertEqual(v["e"], bd.f32(1e-05))
        self.assertEqual(v["inf"], float("-inf"))
        self.assertEqual((type(v["u"]), v["u"]), (U32, 0x80000000))
        self.assertEqual((type(v["l"]), type(v["ul"]), type(v["d"])), (bd.I64, bd.U64, bd.F64))
        self.assertEqual((v["t"], v["n"], v["s"], v["q"], v["w"], v["b"]), (True, None, "12", "true", "word",
                                                                            b"\x01\x02\x03"))

    def test_keys_are_text_quoted_or_not(self):
        v = bd.from_text("\"2214432981\": 1\n'12': 2\n34: 3\n100enemy<Active_Talk>: 4\n")
        self.assertEqual(list(v), ["2214432981", "12", "34", "100enemy<Active_Talk>"])

    def test_block_styles_libyaml_writes(self):
        text = ("# a comment\n---\nmap:\n  inner: {a: 1, b: !u 0x1}\nlist:\n- name: A\n  only_res: false\n"
                "- - 1\n  - 2\n- [x, y]\nnested:\n  - k: v\n    deeper:\n    - 3\n")
        self.assertTrue(bd.same(bd.from_text(text), {
            "map": {"inner": {"a": 1, "b": U32(1)}}, "list": [{"name": "A", "only_res": False}, [1, 2], ["x", "y"]],
            "nested": [{"k": "v", "deeper": [3]}]}))

    def test_lines_libyaml_wrapped(self):
        text = ("tags: {t1: 1, t2: !u 0x80000000,\n    t3: -2.5}\nlong: a plain text that goes on\n  past the line\n"
                "dq: \"one, two,\n  three\"\nesc: \"abc\\\n  def\"\nsq: 'it''s\n  here'\nlist: [1,\n   2]\n"
                "lit: |\n  line 1\n  line 2\n")
        v = bd.from_text(text)
        self.assertTrue(bd.same(v["tags"], {"t1": 1, "t2": U32(0x80000000), "t3": -2.5}))
        self.assertEqual(v["long"], "a plain text that goes on past the line")
        self.assertEqual((v["dq"], v["esc"], v["sq"]), ("one, two, three", "abcdef", "it's here"))
        self.assertEqual((v["list"], v["lit"]), ([1, 2], "line 1\nline 2\n"))

    def test_to_text_round_trip_wrapped(self):
        doc = {"Actors": [{"name": "A", "instSize": 100, "tags": {"t1": 1, "t2": U32(5)}, "f": 0.1, "inf": float("inf"),
                           "homeArea": [{"name": "X"}], "odd": "a: b # c", "num": "123", "empty": "", "t": "true",
                           "vec": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0], "nl": "x\ny"}],
               "12": {}, "big": list(range(12)), "l": bd.I64(7), "d": bd.F64(1.25), "bin": b"\x00\xff"}
        for width in (0, 40):
            with self.subTest(width=width):
                self.assertTrue(bd.same(bd.from_text(bd.to_text(doc, width)), doc))
        self.assertIn("\n    ", bd.to_text(doc, 40))

    def test_damaged_text_is_refused_with_its_line(self):
        for text in ("a: &x 1\n", "a: *x\n", "a: !h {}\n", "a: [1, 2\n", "a: 99999999999\n", "a: !u -1\n",
                     "a:\n    b: 1\n  c: 2\n", "a: {b 1}\n", "? complex\n: key\n", "a: 1\n---\nb: 2\n", "a: 'open\n",
                     "a: 1e99\n", "a: !!binary @@\n", "- 1\nb: 2\n", "a: 1\na: 2\n", "a: {b: 1, b: 2}\n"):
            with self.subTest(text=text):
                with self.assertRaises(LogError):
                    bd.from_text(text)

    def test_deep_nesting_is_refused(self):
        with self.assertRaises(LogError):
            bd.from_text("[" * 100 + "]" * 100)
        with self.assertRaises(LogError):
            bd.from_text("".join(" " * i + f"k{i}:\n" for i in range(100)))


# ---------------------------------------------------------------------------------------------------- reading logs

class ReadLogTest(unittest.TestCase):
    def test_damaged_logs_are_refused(self):
        cases = {
            "actorinfo.yml": [b"- a\n", b"notahash: {name: A}\n", b"'123': 5\n", b"'1': {x: !l 5}\n"],
            "gamedata.yml": [b"bool_data: {add: {F: {DataName: G}}}\n", b"bool_data: {other: 1}\n",
                             b"bool_data: {del: [1]}\n", b"bool_data: 5\n"],
            "savedata.yml": [b"add: [{DataName: A}]\n", b"del: [x]\n", b"add: 1\n", b"more: []\n"],
            "eventinfo.yml": [b"e<A>: 1\n"],
            "effects.yml": [b"AttackUp: 1.0\n"],
            "areadata.yml": [b"one: {Climate: X}\n"],
            "residents.yml": [b"A: [1]\n", b"A: {bin: !!binary AQID}\n"],
        }
        for name, bad in cases.items():
            for data in bad + [b"\xff\xfe not utf-8", b"a: [unclosed\n"]:
                with self.subTest(name=name, data=data):
                    with self.assertRaises(LogError) as cm:
                        bd.read_log(name, data)
                    self.assertIn(name, str(cm.exception))

    def test_empty_log_is_an_empty_diff(self):
        for name in bd.LOGS:
            with self.subTest(name=name):
                self.assertFalse(bd.read_log(name, b"") if name != "savedata.yml" else
                                 any(bd.read_log(name, b"").values()))

    def test_normalized_shapes(self):
        self.assertEqual(list(log("actorinfo.yml", "'-1': {name: A}\n'4294967295': {name: B}\n")), [0xFFFFFFFF])
        self.assertEqual(log("savedata.yml", "add: [{DataName: A, HashValue: !u 0xFFFFFFFF}]\ndel: [!u 0x80000000]\n"),
                         {"add": [{"DataName": "A", "HashValue": -1}], "del": [-(1 << 31)]})
        self.assertEqual(log("gamedata.yml", "s32_data: {add: [{DataName: A}], del: [{DataName: B}]}\n"),
                         {"s32_data": {"add": {"A": {"DataName": "A"}}, "del": ["B"]}})
        self.assertEqual(list(log("areadata.yml", "'7': {}\n12: {}\n")), ["7", "12"])


# ---------------------------------------------------------------------------------------------------- actorinfo

class ActorInfoTest(unittest.TestCase):
    def setUp(self):
        self.a, self.b = names_by_hash_bit()
        actors = {crc(self.a): {"name": self.a, "instSize": 100, "tags": {"t1": 1}, "homeArea": [{"name": "X"}]},
                  crc(self.b): {"name": self.b, "instSize": 10}}
        order = sorted(actors)
        self.game = ukmm.yaz0_stored(bd.byml_dump({
            "Actors": [actors[h] for h in order], "Hashes": [h if h < 1 << 31 else U32(h) for h in order]}))

    def test_changed_keys_merge_and_new_actor_added(self):
        c = "MadeUp_C"
        diff = log("actorinfo.yml", f'"{crc(self.a)}":\n  instSize: 200\n  homeArea:\n  - name: Y\n'
                                    f'  tags: {{t2: !u 0x80000000}}\n'
                                    f'"{crc(c)}": {{name: {c}, instSize: 50}}\n')
        new, notes = bd.merge_actorinfo(self.game, [[diff]])
        self.assertEqual(notes, [])
        self.assertEqual(new[:4], b"Yaz0")
        v = bd.byml_load(new)
        by_name = {a["name"]: a for a in v["Actors"]}
        a = by_name[self.a]
        self.assertEqual(a["instSize"], 200)
        self.assertEqual(a["homeArea"], [{"name": "Y"}])
        self.assertTrue(bd.same(a["tags"], {"t1": 1, "t2": U32(0x80000000)}))
        self.assertEqual(by_name[c], {"name": c, "instSize": 50})
        hashes = [int(h) & 0xFFFFFFFF for h in v["Hashes"]]
        self.assertEqual(hashes, sorted(hashes))
        self.assertEqual(hashes, [crc(x["name"]) for x in v["Actors"]])
        for h in v["Hashes"]:
            self.assertIs(type(h), int if h < 1 << 31 else U32)

    def test_largest_instsize_wins_across_mods(self):
        h = crc(self.a)
        mod1, mod2 = {str(h): {"instSize": 300}}, {str(h): {"instSize": 250, "x": 1}}
        new, _ = bd.merge_actorinfo(self.game, [[mod1], [mod2]])
        a = next(x for x in bd.byml_load(new)["Actors"] if x["name"] == self.a)
        self.assertEqual((a["instSize"], a["x"]), (300, 1))
        # within one package an option simply wins (BCML's dict merge)
        new, _ = bd.merge_actorinfo(self.game, [[mod1, mod2]])
        a = next(x for x in bd.byml_load(new)["Actors"] if x["name"] == self.a)
        self.assertEqual(a["instSize"], 250)

    def test_entry_whose_name_isnt_its_hash_is_skipped_and_named(self):
        diff = {"12345": {"name": "Wrong", "instSize": 5}, str(crc(self.b)): {"instSize": 11}}
        new, notes = bd.merge_actorinfo(self.game, [diff])
        self.assertEqual(len(notes), 1)
        self.assertIn("12345", notes[0])
        self.assertEqual(len(bd.byml_load(new)["Actors"]), 2)

    def test_empty_log_returns_the_game_file(self):
        self.assertEqual(bd.merge_actorinfo(self.game, [[{}]]), (self.game, []))
        same_value = {str(crc(self.b)): {"instSize": 10}}
        self.assertEqual(bd.merge_actorinfo(self.game, [same_value]), (self.game, []))


# ---------------------------------------------------------------------------------------------------- gamedata

class GameDataTest(unittest.TestCase):
    def setUp(self):
        self.game = sarc({"/bool_data_0.bgdata": {"bool_data": [flag("FlagA")]},
                          "/revival_bool_data_0.bgdata": {"bool_data": [flag("FlagR")]},
                          "/s32_data_0.bgdata": {"s32_data": [flag("CountB", 3, MaxValue=10, MinValue=0)]}})

    def test_add_and_delete_keep_the_game_layout(self):
        diff = log("gamedata.yml", "bool_data:\n  add:\n    FlagNew: {DataName: FlagNew, DeleteRev: -1, HashValue: "
                                   f"{bd._signed(crc('FlagNew'))}, InitValue: 1}}\n  del: []\n"
                                   "s32_data: {add: {}, del: [CountB]}\n")
        new, notes = bd.merge_gamedata(self.game, [[diff]])
        self.assertEqual(notes, [])
        v = docs(new)
        self.assertEqual(sorted(v), sorted(docs(self.game)))
        self.assertEqual([e["DataName"] for e in v["/bool_data_0.bgdata"]["bool_data"]], ["FlagA", "FlagNew"])
        self.assertEqual(unsarc(new)["/revival_bool_data_0.bgdata"], unsarc(self.game)["/revival_bool_data_0.bgdata"])
        self.assertEqual(v["/s32_data_0.bgdata"], {"s32_data": []})

    def test_full_file_spills_into_a_new_one(self):
        diff = {"bool_data": {"add": {"FlagNew": flag("FlagNew")}, "del": []}}
        with mock.patch.object(bd, "GAMEDATA_CHUNK", 1):
            new, _ = bd.merge_gamedata(self.game, [diff])
        v = docs(new)
        self.assertEqual(v["/bool_data_1.bgdata"], {"bool_data": [flag("FlagNew")]})
        self.assertEqual(len(v["/bool_data_0.bgdata"]["bool_data"]), 1)

    def test_two_mods_same_flag_last_wins_and_deletes_join(self):
        mod1 = {"bool_data": {"add": {"FlagA": flag("FlagA", 1), "FlagX": flag("FlagX")}, "del": ["FlagR"]}}
        mod2 = {"bool_data": {"add": {"FlagA": flag("FlagA", 0, IsSave=False)}, "del": ["FlagX"]}}
        new, _ = bd.merge_gamedata(self.game, [[mod1], [mod2]])
        v = docs(new)
        self.assertEqual(v["/bool_data_0.bgdata"]["bool_data"], [flag("FlagA", 0, IsSave=False)])
        self.assertEqual(v["/revival_bool_data_0.bgdata"]["bool_data"], [])   # (BCML would keep only mod2's del)

    def test_later_add_undoes_an_earlier_delete(self):
        mod1 = {"s32_data": {"add": {}, "del": ["CountB"]}}
        mod2 = {"s32_data": {"add": {"CountB": flag("CountB", 7)}, "del": []}}
        v = docs(bd.merge_gamedata(self.game, [mod1, mod2])[0])
        self.assertEqual(v["/s32_data_0.bgdata"]["s32_data"], [flag("CountB", 7)])

    def test_unknown_type_and_wrong_hash_are_named(self):
        diff = {"vector9f_data": {"add": {"V": flag("V")}, "del": []},
                "bool_data": {"add": {"FlagW": {**flag("FlagW"), "HashValue": 5}}, "del": []}}
        new, notes = bd.merge_gamedata(self.game, [diff])
        self.assertEqual(len(notes), 2)
        self.assertTrue(any("vector9f_data" in n for n in notes))
        self.assertTrue(any("FlagW" in n for n in notes))
        self.assertIn("FlagW", [e["DataName"] for e in docs(new)["/bool_data_0.bgdata"]["bool_data"]])

    def test_empty_log_returns_the_game_file(self):
        self.assertEqual(bd.merge_gamedata(self.game, [{}]), (self.game, []))
        self.assertEqual(bd.merge_gamedata(self.game, [{"bool_data": {"add": {"FlagA": flag("FlagA")}}}]),
                         (self.game, []))


# ---------------------------------------------------------------------------------------------------- savedata

class SaveDataTest(unittest.TestCase):
    HEAD = {"IsCommon": False, "IsCommonAtSameAccount": False, "IsSaveSecureCode": True, "file_name": "game_data.sav"}
    INFO = [{"directory_num": 8, "is_build_machine": True, "revision": 777}]

    def setUp(self):
        def fmt(head, entries):
            return {"file_list": [head, entries], "save_info": self.INFO}
        self.caption = bd.byml_dump(fmt({**self.HEAD, "file_name": "caption.sav"}, [{"DataName": "C", "HashValue": 1}]))
        self.option = bd.byml_dump(fmt({**self.HEAD, "file_name": "option.sav", "IsCommonAtSameAccount": True},
                                       [{"DataName": "O", "HashValue": 2}]))
        self.game = sarc({"/saveformat_0.bgsvdata": fmt(self.HEAD, [{"DataName": "A", "HashValue": -5},
                                                                     {"DataName": "B", "HashValue": 3}]),
                          "/saveformat_1.bgsvdata": self.caption, "/saveformat_2.bgsvdata": self.option})

    def test_two_mods_latest_add_wins_deletes_join_sorted(self):
        mod1 = log("savedata.yml", "add:\n- {DataName: X, HashValue: 1}\ndel: []\n")
        mod2 = log("savedata.yml", "add:\n- {DataName: Y, HashValue: 1}\n- {DataName: Z, HashValue: -9}\ndel: [3]\n")
        new, notes = bd.merge_savedata(self.game, [[mod1], [mod2]])
        self.assertEqual(len(notes), 1)            # (the made-up HashValues aren't their names' CRC-32: named)
        files = unsarc(new)
        v = bd.byml_load(files["/saveformat_0.bgsvdata"])
        self.assertEqual(v["file_list"][1], [{"DataName": "Z", "HashValue": -9}, {"DataName": "A", "HashValue": -5},
                                             {"DataName": "Y", "HashValue": 1}])
        self.assertEqual(v["file_list"][0], self.HEAD)
        self.assertEqual(v["save_info"], self.INFO)          # (the game's revision, not BCML's)
        self.assertEqual((files["/saveformat_1.bgsvdata"], files["/saveformat_2.bgsvdata"]),
                         (self.caption, self.option))

    def test_entries_split_into_files_then_caption_and_option(self):
        add = [{"DataName": n, "HashValue": bd._signed(crc(n))} for n in ("P", "Q")]
        with mock.patch.object(bd, "SAVEDATA_CHUNK", 2):
            new, notes = bd.merge_savedata(self.game, [{"add": add, "del": []}])
        self.assertEqual(notes, [])
        files = unsarc(new)
        self.assertEqual(sorted(files), [f"/saveformat_{i}.bgsvdata" for i in range(4)])
        counts = [len(bd.byml_load(files[f"/saveformat_{i}.bgsvdata"])["file_list"][1]) for i in range(2)]
        self.assertEqual(counts, [2, 2])
        self.assertEqual((files["/saveformat_2.bgsvdata"], files["/saveformat_3.bgsvdata"]),
                         (self.caption, self.option))

    def test_empty_log_returns_the_game_file(self):
        self.assertEqual(bd.merge_savedata(self.game, [{}]), (self.game, []))


# ---------------------------------------------------------------------------------------------------- the rest

class SmallKindsTest(unittest.TestCase):
    def test_eventinfo_later_mod_replaces_whole_entry(self):
        game = ukmm.yaz0_stored(bd.byml_dump({"e<A>": {"mode": "Seamless"}, "e<B>": {"mode": "Keep"}}))
        mod1, mod2 = {"e<A>": {"mode": "X", "is_timeline": False}}, {"e<A>": {"mode": "Y"}}
        v = bd.byml_load(bd.merge_eventinfo(game, [[mod1], [mod2]])[0])
        self.assertEqual(v, {"e<A>": {"mode": "Y"}, "e<B>": {"mode": "Keep"}})
        # within one package, an option merges into the base's entry
        v = bd.byml_load(bd.merge_eventinfo(game, [[mod1, mod2]])[0])
        self.assertEqual(v["e<A>"], {"mode": "Y", "is_timeline": False})
        self.assertEqual(bd.merge_eventinfo(game, [{}]), (game, []))

    def test_effects_value_replaced_whole(self):
        game = ukmm.yaz0_stored(bd.byml_dump([{"AttackUp": [{"special": False}, {"values": [{"val": 1.0}]}],
                                               "DefenseUp": [{"special": False}, {"values": [{"val": 1.0}]}]}]))
        diff = log("effects.yml", "AttackUp:\n- {special: false}\n- values:\n  - {val: 1.5}\n  - {val: 2.0}\n")
        new, notes = bd.merge_effects(game, [diff])
        v = bd.byml_load(new)
        self.assertEqual((notes, len(v)), ([], 1))
        self.assertEqual(v[0]["AttackUp"], [{"special": False}, {"values": [{"val": 1.5}, {"val": 2.0}]}])
        self.assertEqual(v[0]["DefenseUp"], [{"special": False}, {"values": [{"val": 1.0}]}])
        self.assertEqual(bd.merge_effects(game, [{}]), (game, []))

    def test_areadata_merged_and_sorted_new_area_skipped(self):
        game = ukmm.yaz0_stored(bd.byml_dump([
            {"AreaNumber": 1, "Area": "X", "Animal": [{"name": "W", "num": 2.0}]}, {"AreaNumber": 0}]))
        diff = log("areadata.yml", '"1":\n  Animal:\n  - {name: Z, num: 1.0}\n"5": {Area: New}\n')
        new, notes = bd.merge_areadata(game, [diff])
        v = bd.byml_load(new)
        self.assertEqual([a["AreaNumber"] for a in v], [0, 1])
        self.assertEqual(v[1], {"AreaNumber": 1, "Area": "X", "Animal": [{"name": "Z", "num": 1.0}]})
        self.assertEqual(len(notes), 1)
        self.assertIn("5", notes[0])
        mod1, mod2 = {"1": {"Area": "Y", "Climate": "C"}}, {"1": {"Area": "Q"}}
        v = bd.byml_load(bd.merge_areadata(game, [[mod1], [mod2]])[0])
        self.assertEqual((v[1]["Area"], v[1]["Climate"]), ("Q", "C"))
        self.assertEqual(bd.merge_areadata(game, [{}]), (game, []))

    def test_residents_removed_added_replaced(self):
        game = bd.byml_dump([{"name": "A", "only_res": False}, {"name": "B", "only_res": True}])
        diff = log("residents.yml", "B: {name: B, only_res: true, remove: true}\nC: {name: C, only_res: false}\n")
        new, notes = bd.merge_residents(game, [diff])
        self.assertEqual(new[:2], b"YB")                    # (the game's isn't Yaz0: neither is this)
        self.assertEqual(bd.byml_load(new), [{"name": "A", "only_res": False}, {"name": "C", "only_res": False}])
        mod2 = {"A": {"name": "A", "only_res": True}}
        v = bd.byml_load(bd.merge_residents(game, [[diff], [mod2]])[0])
        self.assertEqual(v, [{"name": "A", "only_res": True}, {"name": "C", "only_res": False}])
        self.assertEqual(bd.merge_residents(game, [{}]), (game, []))

    def test_damaged_log_left_out_and_named_the_rest_merged(self):
        game = bd.byml_dump([{"name": "A", "only_res": False}])
        new, notes = bd.merge_residents(game, [[{"B": {"name": "B", "only_res": True}}, {"C": 5}], [[1, 2]]])
        self.assertEqual(bd.byml_load(new), [{"name": "A", "only_res": False}, {"name": "B", "only_res": True}])
        self.assertEqual(len(notes), 2)
        self.assertIn("package 1, option 1", notes[0])
        self.assertIn("package 2", notes[1])

    def test_bad_game_file_raises_format_error(self):
        with self.assertRaises(wc.FormatError):
            bd.merge_residents(b"not a byml file", [{"A": {"name": "A"}}])
        with self.assertRaises(wc.FormatError):
            bd.merge_effects(bd.byml_dump({"not": "a list"}), [{"A": []}])


# ---------------------------------------------------------------------------------------------------- the contract

class ContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = Path(self.tmp.name) / "extracted"
        romfs = self.game / "romfs"
        self.events = bd.byml_dump({"e<A>": {"mode": "Seamless"}})
        boot = bnp.sarc_build([("Event/EventInfo.product.sbyml", ukmm.yaz0_stored(self.events), 4),
                               ("Actor/ResidentActors.byml", bd.byml_dump([{"name": "A", "only_res": False}]), 8)])
        self.entry = bnp.round32(len(self.events)) + 432
        rstb = b"RSTB" + struct.pack("<II", 1, 0) + struct.pack("<II", crc("Event/EventInfo.product.byml"), self.entry)
        for rel, data in {"Pack/Bootup.pack": boot, rm.RSTB: rstb}.items():
            p = romfs / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_gives_files_and_grown_sizes(self):
        diff = bd.combine("eventinfo.yml", [[log("eventinfo.yml", "e<B>: {mode: Long event name to grow it}\n")]])
        r = bd.apply("eventinfo.yml", diff, str(self.game))
        target = "Pack/Bootup.pack//Event/EventInfo.product.sbyml"
        self.assertEqual(list(r["files"]), [target])
        self.assertEqual(r["notes"], [])
        raw = wc.yaz0_decompress(r["files"][target])[0]
        self.assertEqual(r["sizes"], {"Event/EventInfo.product.byml": self.entry + bnp.round32(len(raw))
                                      - bnp.round32(len(self.events))})
        self.assertEqual(bd.byml_load(raw)["e<B>"], {"mode": "Long event name to grow it"})

    def test_apply_unchanged_or_missing(self):
        r = bd.apply("residents.yml", {"A": {"name": "A", "only_res": False}}, str(self.game))
        self.assertEqual(r, {"files": {}, "sizes": {}, "notes": []})
        r = bd.apply("residents.yml", {"B": {"name": "B", "only_res": False}}, str(self.game))
        self.assertEqual(list(r["files"]), ["Pack/Bootup.pack//Actor/ResidentActors.byml"])
        self.assertEqual(r["sizes"], {})                   # (no entry in this made-up table: none written)
        r = bd.apply("actorinfo.yml", {"1": {"name": "x"}}, str(self.game))
        self.assertEqual(r["files"], {})
        self.assertIn("isn't in extracted", r["notes"][0])
        r = bd.apply("effects.yml", {"A": []}, str(self.game))
        self.assertIn("isn't in extracted", r["notes"][0])

    def test_apply_refuses_a_damaged_diff(self):
        r = bd.apply("residents.yml", {"B": 5}, str(self.game))
        self.assertEqual(r["files"], {})
        self.assertIn("not merged", r["notes"][0])

    def test_combine_takes_packages_or_bare_logs(self):
        a, b = {"e<A>": {"mode": "X", "k": 1}}, {"e<A>": {"mode": "Y"}}
        self.assertEqual(bd.combine("eventinfo.yml", [a, b]), {"e<A>": {"mode": "Y"}})            # two mods
        self.assertEqual(bd.combine("eventinfo.yml", [[a, b]]), {"e<A>": {"mode": "Y", "k": 1}})  # base + option
        with self.assertRaises(LogError):
            bd.combine("eventinfo.yml", [{"e<A>": 1}])

    def test_every_log_has_a_kind(self):
        self.assertEqual(set(bd.LOGS), set(bd.KINDS))
        for name, (target, _, _) in bd.KINDS.items():
            self.assertTrue(target.startswith(("Actor/", "Pack/Bootup.pack//")), name)


if __name__ == "__main__":
    unittest.main()
