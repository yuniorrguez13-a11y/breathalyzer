"""Tests for tools/bcml_texts.py (BCML texts.json, drops.json and shop.aamp logs). Every message file, archive, drop
table, shop file, size table and game folder here is made up, built in code (game folders in temp folders)."""
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import aamp  # noqa: E402
import bcml_texts as bt  # noqa: E402
import bnp  # noqa: E402
import wiiu_convert as wc  # noqa: E402

ARMOR = "ActorType/ArmorUpper.msbt"
MSG_USEN = "Message/Msg_USen.product.ssarc"


def crc(s: str) -> int:
    return zlib.crc32(s.encode())


def u16s(*v) -> bytes:
    return struct.pack(f"<{len(v)}H", *v)


def text(s: str) -> bytes:
    return s.encode("utf-16-le") + b"\0\0"


def msbt(labels: dict, attrs: list | None = None, slots: int | None = None) -> bytes:
    """A made-up MSBT: {label: plain text}."""
    return bt.msbt_build(bt.Msbt(slots or max(1, len(labels)), list(labels), 4 if attrs is not None else 0,
                                 attrs or [], [text(v) for v in labels.values()]))


def archive(files: dict, compress: bool = True) -> bytes:
    raw = bnp.sarc_build([(n, d, 8) for n, d in files.items()])
    return wc.yaz0_compress(raw) if compress else raw


def unpack(arc: bytes) -> dict:
    raw = wc.yaz0_decompress(arc)[0] if arc[:4] == b"Yaz0" else arc
    return {n: d for n, d in wc.sarc_files(raw)}


def texts_log(d: dict) -> bt.Log:
    return bt.read_log("texts.json", json.dumps(d).encode())


def lbl1_slots(data: bytes) -> dict:
    """{label: slot} read straight from LBL1."""
    assert data[0x20:0x24] == b"LBL1"
    body = 0x30
    slots = struct.unpack_from("<I", data, body)[0]
    out = {}
    for k in range(slots):
        n, off = struct.unpack_from("<II", data, body + 4 + 8 * k)
        q = body + off
        for _ in range(n):
            ln = data[q]
            out[data[q + 1:q + 1 + ln].decode()] = k
            q += 5 + ln
    return out


class Msbt(unittest.TestCase):
    def test_label_slot(self):
        self.assertEqual(bt.label_slot("A", 5), 65 % 5)
        self.assertEqual(bt.label_slot("AB", 1000), (65 * 0x492 + 66) % 1000)

    def test_build_and_read_back(self):
        m = bt.Msbt(3, ["Zeta", "Alpha", "Mid"], 4, ["v1", "", "v3"], [text("z"), text("a"), text("m")])
        data = bt.msbt_build(m)
        self.assertEqual(data[:8], b"MsgStdBn")
        self.assertEqual(data[8:10], b"\xff\xfe")
        self.assertEqual((data[0x0C], data[0x0D]), (1, 3))
        self.assertEqual(struct.unpack_from("<I", data, 0x12)[0], len(data))
        self.assertEqual(len(data) % 16, 0)
        back = bt.msbt_read(data)
        self.assertEqual(back, m)
        for label, slot in lbl1_slots(data).items():
            self.assertEqual(slot, bt.label_slot(label, 3))

    def test_sections_padded_with_ab(self):
        data = msbt({"A": "x"})
        size = struct.unpack_from("<I", data, 0x24)[0]
        end = 0x30 + size
        self.assertTrue(data[end:(end + 15) & ~15].strip(b"\xab") == b"")

    def test_damaged_msbt_is_refused(self):
        good = msbt({"A": "x"})
        for bad in (b"MsgStdBn", good[:-20], good[:8] + b"\xfe\xff" + good[10:], b"junk" * 10):
            with self.assertRaises(bt.TextError):
                bt.msbt_read(bad)


class Controls(unittest.TestCase):
    def enc(self, c) -> bytes:
        return bt.encode_control(c)

    def test_friendly_controls_exact_bytes(self):
        cases = [
            ({"kind": "set_colour", "colour": "blue"}, u16s(0x0E, 0, 3, 2, 2)),
            ({"kind": "reset_colour"}, u16s(0x0E, 0, 3, 2, 0xFFFF)),
            ({"kind": "pause", "frames": 30}, u16s(0x0E, 1, 0, 4) + struct.pack("<I", 30)),
            ({"kind": "pause", "length": "long"}, u16s(0x0E, 5, 1, 0)),
            ({"kind": "icon", "icon": "d_pad_down"}, u16s(0x0E, 1, 7, 2) + b"\x07\xcd"),
            ({"kind": "icon", "icon": {"zl": 15}}, u16s(0x0E, 1, 7, 2) + b"\x0f\xcd"),
            ({"kind": "variable", "variable_kind": 11, "name": "Ab"}, u16s(0x0E, 2, 11, 8, 4) + text("Ab")),
            ({"kind": "choice", "choice_labels": [1, 2], "selected_index": 0, "cancel_index": 1, "unknown": 6},
             u16s(0x0E, 1, 4, 6, 1, 2) + b"\x00\x01"),
            ({"kind": "single_choice", "label": 9}, u16s(0x0E, 1, 10, 4, 9) + b"\x01\xcd"),
            ({"kind": "sound", "unknown": [5, 0]}, u16s(0x0E, 3, 1, 2) + b"\x05\x00"),
            ({"kind": "sound2", "unknown": [7, 8]}, u16s(0x0E, 4, 1, 2) + b"\x07\x08"),
            ({"kind": "animation", "name": "Ab"}, u16s(0x0E, 4, 2, 6, 4) + "Ab".encode("utf-16-le")),
            ({"kind": "text_size", "percent": 125}, u16s(0x0E, 0, 2, 2, 125)),
            ({"kind": "auto_advance", "frames": 70000}, u16s(0x0E, 1, 3, 4) + struct.pack("<I", 70000)),
            ({"kind": "localisation", "localisation_kind": "plural", "options": ["a", "bc"]},
             u16s(0x0E, 201, 6, 10, 2) + "a".encode("utf-16-le") + u16s(4) + "bc".encode("utf-16-le")),
            ({"kind": "font", "font_kind": "hylian"}, u16s(0x0E, 0, 1, 2, 0)),
        ]
        for c, want in cases:
            with self.subTest(c["kind"]):
                self.assertEqual(self.enc(c), want)

    def test_every_shape_round_trips(self):
        samples = [
            {"kind": "set_colour", "colour": "light_green4"}, {"kind": "reset_colour"},
            {"kind": "pause", "frames": 5}, {"kind": "pause", "length": "longer"},
            {"kind": "icon", "icon": "gamepad"}, {"kind": "icon", "icon": {"a": 11}},
            {"kind": "icon", "icon": {"x": 37}}, {"kind": "icon", "icon": {"unknown": 99}},
            {"kind": "variable", "variable_kind": 2, "name": "Name"},
            {"kind": "choice", "choice_labels": [1, 2, 3, 4], "selected_index": 2, "cancel_index": 3, "unknown": 10},
            {"kind": "single_choice", "label": 3}, {"kind": "sound", "unknown": [1, 2, 3, 4]},
            {"kind": "sound2", "unknown": [9, 9]}, {"kind": "animation", "name": "Wait"},
            {"kind": "text_size", "percent": 80}, {"kind": "auto_advance", "frames": 12},
            {"kind": "localisation", "localisation_kind": "gender", "options": ["he", "she", ""]},
            {"kind": "localisation", "localisation_kind": {"unknown": 7}, "options": ["x"]},
            {"kind": "font", "font_kind": "normal"},
            {"kind": "raw", "zero": {"zero": {"field_1": 4, "field_2": 1, "field_3": 2}}},
            {"kind": "raw", "zero": {"three": {"field_1": 2, "field_2": 77}}},
            {"kind": "raw", "zero": {"four": {"field_1": 0}}},
            {"kind": "raw", "one": {"one": {"field_1": 4, "field_2": 123456}}},
            {"kind": "raw", "one": {"two": {"field_1": 0}}},
            {"kind": "raw", "one": {"seven": {"field_1": 2, "field_2": [1, 2]}}},
            {"kind": "raw", "one": {"eight": {"unknown_1": [[255, 255, 0, 0]], "field_1": [5, 6],
                                              "field_2": [1, 2, 3, 4]}}},
            {"kind": "raw", "one": {"nine": {"unknown_1": [255, 255, 0, 0] * 3,
                                             "strings": [{"field_1": i, "string": s} for i, s in enumerate("abcd")],
                                             "field_3": 1, "field_4": 2, "unknown_2": None, "field_6": [0, 1]}}},
            {"kind": "raw", "one": {"ten": {"field_1": 4, "field_2": 1, "field_3": [0, 0]}}},
            {"kind": "raw", "two": {"one_field": [7, {"field_1": 0}]}},
            {"kind": "raw", "two": {"variable": [14, {"field_1": 8, "string": "Hi", "field_3": 3}]}},
            {"kind": "raw", "three": {"field_1": 2, "field_2": [1, 0]}},
            {"kind": "raw", "four": {"zero": {"field_1": 6, "string": "Ab"}}},
            {"kind": "raw", "four": {"three": {"field_1": 0}}},
            {"kind": "raw", "five": {"field_1": 7, "field_2": 0}},
            {"kind": "raw", "two_hundred_one": {"dynamic": [0, {"len": 2, "field_2": [1, 2]}]}},
            {"kind": "raw", "two_hundred_one": {"one_field": [4, {"field_1": 0}]}},
        ]
        for c in samples:
            with self.subTest(c):
                contents = [{"text": "a"}, {"control": c}, {"text": "b"}]
                raw = bt.encode_contents(contents)
                self.assertEqual(bt.decode_text(raw), contents)
                self.assertEqual(bt.encode_contents(bt.decode_text(raw)), raw)

    def test_ruby_tag_keeps_its_text_as_msyt_does(self):
        """msyt reads a ruby tag (0.0) as 3 values; the rest it covers stays text, and comes back the same."""
        ruby = {"kind": "raw", "zero": {"zero": {"field_1": 6, "field_2": 1, "field_3": 2}}}
        raw = bt.encode_contents([{"control": ruby}, {"text": "xy"}])
        self.assertEqual(raw, u16s(0x0E, 0, 0, 6, 1, 2) + text("xy"))
        self.assertEqual(bt.decode_text(raw), [{"control": ruby}, {"text": "xy"}])

    def test_size_running_past_the_text_is_refused(self):
        bad = {"kind": "raw", "zero": {"zero": {"field_1": 10, "field_2": 1, "field_3": 2}}}
        with self.assertRaises(bt.TextError):
            bt.encode_contents([{"control": bad}, {"text": "x"}])
        with self.assertRaises(bt.TextError):     # a choice whose "unknown" (its size) says more than it has
            bt.encode_contents([{"control": {"kind": "choice", "choice_labels": [1, 2], "selected_index": 0,
                                             "cancel_index": 0, "unknown": 40}}])

    def test_bad_controls_are_refused(self):
        for c in ({"kind": "nope"}, {"kind": "set_colour", "colour": "pink"}, {"kind": "pause"},
                  {"kind": "raw", "six": {}}, {"kind": "variable", "variable_kind": -1, "name": "x"},
                  {"kind": "choice", "choice_labels": [1], "selected_index": 0, "cancel_index": 0, "unknown": 4},
                  {"kind": "sound", "unknown": [300]}, "text"):
            with self.subTest(c), self.assertRaises(bt.TextError):
                bt.encode_control(c)
        with self.assertRaises(bt.TextError):
            bt.encode_contents([{"text": "a\x0eb"}])


class Texts(unittest.TestCase):
    def setUp(self):
        self.usen = {ARMOR: msbt({"A_Name": "Tunic", "A_Desc": "Old desc"}),
                     "ActorType/Other.msbt": msbt({"O": "other"})}
        self.eude = {ARMOR: msbt({"A_Name": "Waffenrock", "A_Desc": "Alt"})}
        self.desc = [{"text": "x "}, {"control": {"kind": "set_colour", "colour": "red"}}, {"text": "y"},
                     {"control": {"kind": "reset_colour"}}]
        self.log = texts_log({"USen": {"ActorType/ArmorUpper.msyt": {
            "B_Name": {"contents": [{"text": "Hood"}]}, "A_Desc": {"contents": self.desc}}}})

    def test_spec_case(self):
        stats = {}
        new, notes = bt.merge_texts(archive(self.usen), "USen", [self.log], stats)
        self.assertEqual(notes, [])
        files = unpack(new)
        self.assertEqual(files["ActorType/Other.msbt"], self.usen["ActorType/Other.msbt"])
        m = bt.msbt_read(files[ARMOR])
        self.assertEqual(m.labels, ["A_Name", "A_Desc", "B_Name"])
        self.assertEqual(m.texts[2], text("Hood"))
        self.assertEqual(m.texts[1], "x ".encode("utf-16-le") + u16s(0x0E, 0, 3, 2, 0) + "y".encode("utf-16-le")
                         + u16s(0x0E, 0, 3, 2, 0xFFFF, 0))
        self.assertEqual(m.slots, 2)
        for label, slot in lbl1_slots(files[ARMOR]).items():
            self.assertEqual(slot, bt.label_slot(label, 2))
        self.assertEqual(stats["new_len"], len(wc.yaz0_decompress(new)[0]))

    def test_other_language_gets_new_texts_only(self):
        stats = {}
        new, _ = bt.merge_texts(archive(self.eude), "EUde", [self.log], stats)
        m = bt.msbt_read(unpack(new)[ARMOR])
        self.assertEqual(m.labels, ["A_Name", "A_Desc", "B_Name"])
        self.assertEqual(m.texts[1], text("Alt"))
        self.assertEqual(m.texts[2], text("Hood"))
        self.assertEqual(stats["kept"], 1)

    def test_same_language_part_takes_changed_texts(self):
        new, _ = bt.merge_texts(archive(self.usen), "EUen", [self.log])
        self.assertEqual(bt.decode_text(bt.msbt_read(unpack(new)[ARMOR]).texts[1]), self.desc)

    def test_empty_log_leaves_the_archive_alone(self):
        arc = archive(self.usen)
        for logs in ([], [texts_log({})], [texts_log({"USen": {}})]):
            new, notes = bt.merge_texts(arc, "USen", logs)
            self.assertIs(new, arc)
            self.assertEqual(notes, [])

    def test_unchanged_text_leaves_the_archive_alone(self):
        arc = archive(self.usen)
        same = texts_log({"USen": {"ActorType/ArmorUpper.msyt": {"A_Name": {"contents": [{"text": "Tunic"}]}}}})
        self.assertIs(bt.merge_texts(arc, "USen", [same])[0], arc)

    def test_later_mod_wins_and_attributes_carry(self):
        ef = "EventFlowMsg/Npc.msbt"
        arc = archive({ef: msbt({"T": "hi"}, attrs=["game"])})
        one = texts_log({"USen": {"EventFlowMsg/Npc.msyt": {"T": {"contents": [{"text": "one"}],
                                                                 "attributes": "mod1"}}}})
        two = texts_log({"USen": {"EventFlowMsg/Npc.msyt": {"T": {"contents": [{"text": "two"}]}}}})
        m = bt.msbt_read(unpack(bt.merge_texts(arc, "USen", [one, two])[0])[ef])
        self.assertEqual((m.texts, m.attrs), ([text("two")], ["mod1"]))
        m = bt.msbt_read(unpack(bt.merge_texts(arc, "USen", [two])[0])[ef])
        self.assertEqual((m.texts, m.attrs), ([text("two")], ["game"]))

    def test_new_files(self):
        log = texts_log({"USen": {"EventFlowMsg/New.msyt": {"a": {"contents": [{"text": "1"}]},
                                                            "b": {"contents": [], "attributes": "x"}},
                                  "ActorType/New.msyt": {"c": {"contents": [{"text": "3"}]}}}})
        files = unpack(bt.merge_texts(archive(self.usen), "USen", [log])[0])
        ef = bt.msbt_read(files["EventFlowMsg/New.msbt"])
        self.assertEqual((ef.slots, ef.labels, ef.attr_size, ef.attrs), (2, ["a", "b"], 4, ["", "x"]))
        other = bt.msbt_read(files["ActorType/New.msbt"])
        self.assertEqual((other.slots, other.attr_size, other.texts), (1, 0, [text("3")]))

    def test_language_picking(self):
        self.assertEqual(bt.pick_language("EUde", ["USen", "EUde"]), ("EUde", True))
        self.assertEqual(bt.pick_language("USfr", ["EUfr", "USen"]), ("EUfr", True))
        self.assertEqual(bt.pick_language("JPja", ["EUde", "USen"]), ("USen", False))
        self.assertEqual(bt.pick_language("JPja", ["EUde"]), ("EUde", False))
        self.assertEqual(bt.pick_language("JPja", []), (None, False))

    def test_damaged_logs(self):
        for data in (b"{not json", b"[1, 2]", b'{"USen": 5}', b"\xff\xfe\x00"):
            with self.subTest(data), self.assertRaises(bt.LogError):
                bt.read_log("texts.json", data)
        log = texts_log({"USen": {"ActorType/ArmorUpper.msyt": {
            "Good": {"contents": [{"text": "ok"}]},
            "Bad": {"contents": [{"control": {"kind": "set_colour", "colour": "pink"}}]},
            "Worse": {"contents": "nope"}},
            "../evil.msyt": {"x": {"contents": []}}}, "english": {}})
        self.assertEqual(list(log.value["USen"][ARMOR]), ["Good"])
        self.assertEqual(len(log.notes), 1)
        self.assertIn("4 part(s)", log.notes[0])
        new, _ = bt.merge_texts(archive(self.usen), "USen", [log])
        self.assertEqual(bt.msbt_read(unpack(new)[ARMOR]).labels, ["A_Name", "A_Desc", "Good"])

    def test_unreadable_game_file_is_named(self):
        arc = archive({ARMOR: b"MsgStdBn" + bytes(40)})
        new, notes = bt.merge_texts(arc, "USen", [self.log])
        self.assertIs(new, arc)
        self.assertIn("can't be rebuilt", notes[0])


def bdrop(tables: dict) -> bytes:
    """{table: ((min, max, approach, speed), {item: probability})} as the game lays a .bdrop out."""
    root = aamp.PList()
    head = aamp.PObject()
    head.set("TableNum", aamp.INT, len(tables))
    for i, t in enumerate(tables, 1):
        head.set(f"Table{i:02}", aamp.STRING64, t)
    root.objects[crc("Header")] = head
    for t, (fields, items) in tables.items():
        o = aamp.PObject()
        for (_, name), v in zip(bt.DROP_KEYS, fields):
            o.set(name, aamp.INT, v)
        o.set("ColumnNum", aamp.INT, len(items))
        for j, (n, p) in enumerate(items.items(), 1):
            o.set(f"ItemName{j:02}", aamp.STRING64, n)
            o.set(f"ItemProbability{j:02}", aamp.F32, p)
        root.objects[crc(t)] = o
    return aamp.dump(root)


def drop_entry(fields=(1, 1, 0, 0), items=None) -> dict:
    d = dict(zip([k for k, _ in bt.DROP_KEYS], fields))
    d["items"] = items or {}
    return d


def read_drop(data: bytes) -> dict:
    return bt._read_drop(data)[0]


class Drops(unittest.TestCase):
    PATH = "01007EF00011E000/romfs/Actor/Pack/Enemy_T.sbactorpack//Actor/DropTable/T.bdrop"

    def game(self):
        return bdrop({"Normal": ((1, 2, 0, 0), {"Item_A": 30.0}), "Rare": ((1, 1, 0, 0), {"Item_R": 100.0})})

    def log(self, d: dict) -> bt.Log:
        return bt.read_log("drops.json", json.dumps(d).encode())

    def test_spec_case(self):
        log = self.log({self.PATH: {"Normal": drop_entry((1, 2, 0, 0), {"Item_A": bt.UNDERRIDE, "Item_New": 70.0})}})
        tables = log.value["Actor/Pack/Enemy_T.sbactorpack//Actor/DropTable/T.bdrop"]
        new, notes = bt.merge_drops(self.game(), [tables])
        self.assertEqual(notes, [])
        self.assertEqual(read_drop(new), {"Normal": {"repeat_num_min": 1, "repeat_num_max": 2, "approach_type": 0,
                                                     "occurrence_speed_type": 0,
                                                     "items": {"Item_A": 30.0, "Item_New": 70.0}}})
        root = aamp.load(new)
        self.assertEqual(root.objects[crc("Header")].get_value("TableNum"), 1)
        self.assertEqual(root.objects[crc("Normal")].get_value("ColumnNum"), 2)

    def test_whole_file_as_the_game_has_it_is_unchanged(self):
        game = self.game()
        same = {t: {**{k: v for k, v in d.items() if k != "items"}, "items": d["items"]}
                for t, d in read_drop(game).items()}
        self.assertEqual(bt.merge_drops(game, [same])[0], game)

    def test_two_mods_later_table_wins_whole(self):
        one = {"Normal": drop_entry((1, 2, 0, 0), {"Item_A": 30.0, "Item_B": 5.0}), "Rare": drop_entry()}
        two = {"Normal": drop_entry((3, 3, 0, 0), {"Item_C": 1.0})}
        got = read_drop(bt.merge_drops(self.game(), [one, two])[0])
        self.assertEqual(list(got), ["Normal", "Rare"])
        self.assertEqual(got["Normal"]["items"], {"Item_C": 1.0})
        self.assertEqual(got["Normal"]["repeat_num_min"], 3)

    def test_underride_in_a_count_keeps_the_game_value(self):
        log = self.log({self.PATH: {"Normal": {**drop_entry((5, bt.UNDERRIDE, 0, 0)), "items": {"Item_A": 1.0}}}})
        tables = next(iter(log.value.values()))
        got = read_drop(bt.merge_drops(self.game(), [tables])[0])["Normal"]
        self.assertEqual((got["repeat_num_min"], got["repeat_num_max"]), (5, 2))

    def test_underride_without_a_game_value_is_left_out(self):
        new, notes = bt.merge_drops(self.game(), [{"Rare": drop_entry(items={"Item_X": bt.UNDERRIDE})}])
        self.assertEqual(read_drop(new)["Rare"]["items"], {})
        self.assertIn("Item_X", notes[0])

    def test_new_table_and_new_file(self):
        partial = {"items": {"I": 1.0}, "repeat_num_min": 1}
        new, notes = bt.merge_drops(None, [{"New": drop_entry((2, 2, 1, 1), {"I": 50.0}), "Half": partial}])
        self.assertEqual(list(read_drop(new)), ["New"])
        self.assertIn("Half", notes[0])

    def test_damaged_logs(self):
        for data in (b"nope", b"[]", b"\x00\x01"):
            with self.subTest(data), self.assertRaises(bt.LogError):
                bt.read_log("drops.json", data)
        log = self.log({self.PATH: {"Normal": {"items": {"I": "lots"}}},
                        "Actor/Pack/B.sbactorpack//Actor/DropTable/B.bdrop": {"T": drop_entry(items={"I": 1})},
                        "../../x//y.bdrop": {"T": drop_entry()},
                        "01007EF00011F001/romfs/Actor/Pack/D.sbactorpack//Actor/DropTable/D.bdrop":
                            {"T": drop_entry()}})
        self.assertEqual(list(log.value), ["Actor/Pack/B.sbactorpack//Actor/DropTable/B.bdrop"])
        self.assertIn("2 drop table file(s) damaged", log.notes[0])
        self.assertIn("DLC", log.notes[1])

    def test_paths(self):
        self.assertEqual(bt.norm_path("content/Actor/Pack/A.sbactorpack//Actor/DropTable/A.bdrop"),
                         (["Actor/Pack/A.sbactorpack", "Actor/DropTable/A.bdrop"], False))
        self.assertEqual(bt.norm_path("aoc/0010/Pack/AocMainField.pack//X.bdrop")[1], True)
        for bad in ("/abs//x", "C:/x//y", "a/../b", "a////b", ""):
            with self.subTest(bad), self.assertRaises(bt.TextError):
                bt.norm_path(bad)


SHOP_KEYS = [k for k, _ in bt.SHOP_KEYS]


def bshop(tables: dict) -> bytes:
    """{table: [(name, num, price, look flag, amount)]} as the game lays a .bshop out."""
    root = aamp.PList()
    head = aamp.PObject()
    head.set("TableNum", aamp.INT, len(tables))
    for i, t in enumerate(tables, 1):
        head.set(f"Table{i:02}", aamp.STRING64, t)
    root.objects[crc("Header")] = head
    for t, items in tables.items():
        o = aamp.PObject()
        o.set("ColumnNum", aamp.INT, len(items))
        for j, it in enumerate(items, 1):
            o.set(f"ItemSort{j:03}", aamp.INT, j - 1)
            for (k, ty), v in zip(bt.SHOP_KEYS, it):
                o.set(f"{k}{j:03}", ty, v)
        root.objects[crc(t)] = o
    return aamp.dump(root)


def item_obj(**params) -> aamp.PObject:
    o = aamp.PObject()
    types = dict(bt.SHOP_KEYS)
    for k, v in params.items():
        o.set(k, types[k], v)
    return o


def shop_diff(adds: dict | None = None, rems: dict | None = None, names: list | None = None) -> aamp.PList:
    """{table: {item name: PObject}} for Additions and Removals; names: new tables for TableNames."""
    pl = aamp.PList()
    for key, tables in (("Additions", adds), ("Removals", rems)):
        if tables is None:
            continue
        sub = aamp.PList()
        for t, items in tables.items():
            sub.lists[crc(t)] = aamp.PList(objects={crc(n): o for n, o in items.items()})
        if key == "Additions" and names:
            tn = aamp.PObject()
            for n in names:
                tn.set(n, aamp.STRING64, n)
            sub.objects[crc("TableNames")] = tn
        pl.lists[crc(key)] = sub
    return pl


def shop_log(files: dict) -> bytes:
    root = aamp.PList()
    fn = aamp.PObject()
    for i, (path, diff) in enumerate(files.items()):
        fn.set(f"File{i}", aamp.STRING_REF, path)
        root.lists[crc(path)] = diff
    root.objects[crc("Filenames")] = fn
    return aamp.dump(root)


def read_shop(data: bytes) -> dict:
    tables = bt._read_shop(data)[0]
    return {t: [tuple(it[k].value for k in SHOP_KEYS) for it in items.values()] for t, items in tables}


A = ("Item_A", 1, 0, False, 0)
B = ("Item_B", 2, 5, True, 1)
C = ("Item_C", 3, 0, False, 2)


class Shops(unittest.TestCase):
    def game(self):
        return bshop({"Normal": [A, B], "Other": [C]})

    def test_spec_case(self):
        diff = shop_diff(adds={"Normal": {"Item_B": item_obj(ItemNum=9),
                                          "Item_C": item_obj(**dict(zip(SHOP_KEYS, C)))}},
                         rems={"Normal": {"Item_A": item_obj()}})
        new, notes = bt.merge_shop(self.game(), [diff])
        self.assertEqual(notes, [])
        self.assertEqual(read_shop(new)["Normal"], [("Item_B", 9, 5, True, 1), C])
        obj = aamp.load(new).objects[crc("Normal")]
        self.assertEqual((obj.get_value("ColumnNum"), obj.get_value("ItemSort001"), obj.get_value("ItemSort002")),
                         (2, 0, 1))

    def test_empty_diff_is_unchanged(self):
        game = self.game()
        self.assertEqual(bt.merge_shop(game, [aamp.PList()])[0], game)
        self.assertEqual(bt.merge_shop(game, [shop_diff(adds={}, rems={})])[0], game)

    def test_two_mods_merge_per_value(self):
        one = shop_diff(adds={"Normal": {"Item_A": item_obj(ItemNum=7, ItemAmount=3)}})
        two = shop_diff(adds={"Normal": {"Item_A": item_obj(ItemNum=8)}})
        self.assertEqual(read_shop(bt.merge_shop(self.game(), [one, two])[0])["Normal"][0],
                         ("Item_A", 8, 0, False, 3))

    def test_new_table_and_removed_then_changed_item(self):
        add = shop_diff(adds={"Extra": {"Item_D": item_obj(ItemName="Item_D", ItemNum=1, ItemAdjustPrice=0,
                                                           ItemLookGetFlg=False, ItemAmount=0)},
                              "Normal": {"Item_A": item_obj(ItemNum=4)}}, names=["Extra"])
        rem = shop_diff(rems={"Normal": {"Item_A": item_obj()}})
        got = read_shop(bt.merge_shop(self.game(), [rem, add])[0])
        self.assertEqual(list(got), ["Normal", "Other", "Extra"])
        self.assertEqual(got["Normal"], [B, ("Item_A", 4, 0, False, 0)])     # (re-added last, as BCML does)

    def test_partial_new_item_and_unnamed_table_are_left_out(self):
        diff = shop_diff(adds={"Normal": {"Item_Z": item_obj(ItemNum=1)}, "Ghost": {"X": item_obj()}})
        new, notes = bt.merge_shop(self.game(), [diff])
        self.assertEqual(read_shop(new)["Normal"], [A, B])
        self.assertIn("2 shop change(s) left out", notes[0])

    def test_emptied_table_stays(self):
        new, notes = bt.merge_shop(self.game(), [shop_diff(rems={"Other": {"Item_C": item_obj()}})])
        self.assertEqual(read_shop(new)["Other"], [])
        self.assertIn("kept empty", notes[0])

    def test_log_reading(self):
        path = "01007EF00011E000/romfs/Actor/Pack/Npc_T.sbactorpack//Actor/ShopData/T.bshop"
        log = bt.read_log("shop.aamp", shop_log({path: shop_diff(adds={"Normal": {}})}))
        self.assertEqual(list(log.value), ["Actor/Pack/Npc_T.sbactorpack//Actor/ShopData/T.bshop"])
        for data in (b"!io\nversion: 0\n", b"garbage", b"AAMP" + bytes(60)):
            with self.subTest(data), self.assertRaises(bt.LogError):
                bt.read_log("shop.aamp", data)
        no_names = aamp.dump(aamp.PList())
        with self.assertRaises(bt.LogError):
            bt.read_log("shop.aamp", no_names)


def rstb(entries: dict) -> bytes:
    body = b"".join(struct.pack("<II", zlib.crc32(k.encode()), v) for k, v in sorted(entries.items(),
                                                                                     key=lambda kv: crc(kv[0])))
    return b"RSTB" + struct.pack("<II", len(entries), 0) + body


class Contract(unittest.TestCase):
    """read_log / combine / apply against a made-up game folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = self.tmp.name
        self.romfs = Path(self.game) / "romfs"
        usen_msg = archive({ARMOR: msbt({"A_Name": "Tunic"})})
        self.usen_raw_len = len(wc.yaz0_decompress(usen_msg)[0])
        files = {
            "Pack/Bootup_USen.pack": bnp.sarc_build([(MSG_USEN, usen_msg, 8)]),
            "Pack/Bootup_EUde.pack": bnp.sarc_build([("Message/Msg_EUde.product.ssarc",
                                                      archive({ARMOR: msbt({"A_Name": "Rock"})}), 8)]),
            "Pack/Bootup_Graphics.pack": b"not a language",
            "Actor/Pack/Enemy_T.sbactorpack": wc.yaz0_compress(bnp.sarc_build(
                [("Actor/DropTable/T.bdrop", Drops().game(), 8)])),
            "Pack/TitleBG.pack": bnp.sarc_build([("Actor/Pack/Npc_T.sbactorpack", wc.yaz0_compress(
                bnp.sarc_build([("Actor/ShopData/T.bshop", Shops().game(), 4)])), 8)]),
            "System/Resource/ResourceSizeTable.product.srsizetable": rstb(
                {"Message/Msg_USen.product.sarc": 5000, "Actor/DropTable/T.bdrop": 3000}),
        }
        for rel, data in files.items():
            p = self.romfs / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_texts(self):
        base = bt.read_log("texts.json", json.dumps({"USen": {"ActorType/ArmorUpper.msyt": {
            "New": {"contents": [{"text": "Hi"}]}}}}).encode(), "logs/")
        opt = bt.read_log("texts.json", json.dumps({"USen": {"ActorType/ArmorUpper.msyt": {
            "A_Name": {"contents": [{"text": "Hood"}]}}}}).encode(), "options/a/logs/")
        res = bt.apply("texts.json", bt.combine("texts.json", [base, opt]), self.game)
        self.assertEqual(sorted(res["files"]), ["Pack/Bootup_EUde.pack//Message/Msg_EUde.product.ssarc",
                                                f"Pack/Bootup_USen.pack//{MSG_USEN}"])
        usen = bt.msbt_read(unpack(res["files"][f"Pack/Bootup_USen.pack//{MSG_USEN}"])[ARMOR])
        self.assertEqual(usen.texts, [text("Hood"), text("Hi")])
        new_len = len(wc.yaz0_decompress(res["files"][f"Pack/Bootup_USen.pack//{MSG_USEN}"])[0])
        self.assertEqual(res["sizes"], {"Message/Msg_USen.product.sarc":
                                        5000 + bnp.round32(new_len) - bnp.round32(self.usen_raw_len)})
        self.assertEqual(len(res["notes"]), 1)
        self.assertIn("EUde", res["notes"][0])

    def test_drops(self):
        log = bt.read_log("drops.json", json.dumps({Drops.PATH: {"Normal": drop_entry(
            (1, 2, 0, 0), {"Item_A": 30.0, "Item_Long_Name_Here": 70.0})}}).encode())
        res = bt.apply("drops.json", bt.combine("drops.json", [log]), self.game)
        target = "Actor/Pack/Enemy_T.sbactorpack//Actor/DropTable/T.bdrop"
        self.assertEqual(list(res["files"]), [target])
        self.assertEqual(list(read_drop(res["files"][target])), ["Normal"])
        self.assertEqual(list(res["sizes"]), ["Actor/DropTable/T.bdrop"])
        self.assertGreaterEqual(res["sizes"]["Actor/DropTable/T.bdrop"], 3000)

    def test_shop_in_a_title_actor(self):
        path = "Actor/Pack/Npc_T.sbactorpack//Actor/ShopData/T.bshop"
        log = bt.read_log("shop.aamp", shop_log({path: shop_diff(rems={"Normal": {"Item_A": item_obj()}})}))
        res = bt.apply("shop.aamp", bt.combine("shop.aamp", [log]), self.game)
        target = "Pack/TitleBG.pack//" + path
        self.assertEqual(list(res["files"]), [target])
        self.assertEqual(read_shop(res["files"][target])["Normal"], [B])
        self.assertEqual(res["sizes"], {})        # (the made-up game table has no entry for it)

    def test_missing_game_file_and_unknown_log(self):
        log = bt.read_log("drops.json", json.dumps({"Actor/Pack/Nope.sbactorpack//Actor/DropTable/N.bdrop": {
            "T": drop_entry(items={"I": 1.0})}}).encode())
        res = bt.apply("drops.json", log, self.game)
        target = "Actor/Pack/Nope.sbactorpack//Actor/DropTable/N.bdrop"
        self.assertEqual(list(read_drop(res["files"][target])), ["T"])
        for call in (lambda: bt.read_log("map.yml", b""), lambda: bt.combine("x.yml", []),
                     lambda: bt.apply("x.yml", log, self.game)):
            with self.assertRaises(bt.LogError):
                call()

    def test_empty_logs_change_nothing(self):
        for name, data in (("texts.json", b"{}"), ("drops.json", b"{}"), ("shop.aamp", shop_log({}))):
            with self.subTest(name):
                res = bt.apply(name, bt.combine(name, [bt.read_log(name, data)]), self.game)
                self.assertEqual(res, {"files": {}, "sizes": {}, "notes": []})


if __name__ == "__main__":
    unittest.main()
