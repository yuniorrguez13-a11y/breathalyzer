"""Tests for story together (tools/mp/story.py, coop.StoryFollower): made-up flag names and made-up save files only
(never a real save, never game data)."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from mp import coop, story  # noqa: E402

NAMES = ["FirstTower_Activated", "Wind_Relic_Finish", "Clear_RemainsWind", "Open_Dungeon000", "Clear_Dungeon000",
         "MapTower_01", "IsGet_MemoryPhoto_001", "IsGet_PlayerStole2", "IsGet_Obj_Camera", "IsPlayed_Demo001_0"]


def make_save(values: dict, extra=(("PlayerPos", 7), ("SomeArray", 1), ("SomeArray", 2))) -> bytes:
    """A made-up game_data.sav: the header, (CRC-32 of the name, value) pairs, 4 trailing bytes."""
    out = bytearray(struct.pack("<3I", story.SAVE_MAGIC, 0xffffffff, 1))
    for name, v in list(values.items()) + list(extra):
        out += struct.pack("<II", story.name_hash(name), v)
    return bytes(out + b"\0\0\0\0")


class ListTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(story.kind_of("FirstTower_Activated"), "main")
        self.assertEqual(story.kind_of("Clear_Dungeon119"), "shrines")
        self.assertEqual(story.kind_of("IsGet_Obj_Camera"), "slate")
        self.assertEqual(story.kind_of("BalladOfHeroes_Step01"), "ballad")
        self.assertIsNone(story.kind_of("Get_MasterSword_Finish"))     # the guest's sword is his own
        self.assertIsNone(story.kind_of("PlayerPos"))
        self.assertIsNone(story.kind_of("IsGet_Obj_Bomb_Arrow"))
        self.assertTrue(story.is_dlc("Clear_Dungeon120"))
        self.assertFalse(story.is_dlc("Clear_Dungeon119"))
        self.assertTrue(story.is_dlc("BalladOfHeroGerudo_Step1"))

    def test_make_list_rule(self):
        rows = [(n, "bool_data", {"ResetType": 0, "InitValue": 0, "IsSave": True}) for n in NAMES]
        rows += [("MapTower_02", "bool_data", {"ResetType": 1, "InitValue": 0}),       # reset: not story
                 ("IsGet_MemoryPhoto_008", "bool_data", {"ResetType": 0, "InitValue": 1}),   # starts at 1
                 ("Clear_Dungeon001", "s32_data", {"ResetType": 0, "InitValue": 0}),
                 ("Clear_Dungeon121", "bool_data", {"ResetType": 0, "InitValue": 0}),
                 ("Clear_Dungeon002", "bool_data", {"ResetType": 0, "InitValue": 0})]
        saved = {story.name_hash(n) for n in NAMES + ["Clear_Dungeon121"]}
        sl = story.make_list(rows, saved)
        self.assertEqual(set(sl.base), set(NAMES))
        self.assertEqual(sl.dlc, ["Clear_Dungeon121"])
        self.assertEqual(sl.base[0], "FirstTower_Activated")            # kind order: main quests first
        self.assertEqual(sl.base[-1], "IsPlayed_Demo001_0")
        self.assertEqual(len(sl.base_hash), 8)
        with tempfile.TemporaryDirectory() as d:
            sl.save(Path(d) / "l.txt")
            back = story.StoryList.load(Path(d) / "l.txt")
        self.assertEqual((back.base, back.dlc), (sl.base, sl.dlc))
        self.assertEqual(back.hello(), {"v": 1, "base": sl.base_hash, "dlc": sl.dlc_hash})

    def test_bits(self):
        line = story.encode_bits({0, 3, 9}, 10)
        self.assertEqual(line, "0902")
        self.assertEqual(story.decode_bits(line, 10), {0, 3, 9})
        self.assertEqual(story.decode_bits(story.encode_bits(set(), 1075), 1075), set())
        self.assertEqual(len(story.encode_bits(set(), 1075)), 270)
        for bad in ("090", "0902ff", "0X02", "0904", None, 5):          # length, hex, a bit past n
            self.assertIsNone(story.decode_bits(bad, 10), bad)
        with self.assertRaises(ValueError):
            story.encode_bits({10}, 10)


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.coop = self.root / "joined" / "save_0"
        for slot, vals in (("0", {"FirstTower_Activated": 1}), ("1", {}), ("2", {"Clear_Dungeon000": 1})):
            d = self.coop / slot
            d.mkdir(parents=True)
            base = {n: 0 for n in NAMES}
            base.update(vals)
            (d / story.SAVE_NAME).write_bytes(make_save(base))
        (self.coop / "option.sav").write_bytes(b"opt")
        story.mark_coop_copy(self.coop.parent)
        self.backups = self.root / "backups" / "world1"
        self.host = {0, 2, 3, 4, 7}       # FirstTower, Clear_RemainsWind, Open/Clear_Dungeon000, paraglider

    def tearDown(self):
        self.tmp.cleanup()

    def test_merge_adds_only(self):
        before = {p: p.read_bytes() for p in story.slot_files(self.coop)}
        had = {p: story.story_bits(p, NAMES) for p in before}
        res = story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[])
        self.assertIsNotNone(res.backup)
        self.assertEqual(len(before), 3)                               # every numbered slot
        for p, old in before.items():
            new = p.read_bytes()
            self.assertEqual(len(new), len(old))                       # size never changes
            self.assertEqual(story.story_bits(p, NAMES), self.host | had[p])   # a union
            diff = [k for k in range(0, len(old), 4) if old[k:k + 4] != new[k:k + 4]]
            for k in diff:                                             # only values, only 0 -> 1
                self.assertEqual(struct.unpack_from("<I", old, k)[0], 0)
                self.assertEqual(struct.unpack_from("<I", new, k)[0], 1)
        self.assertIn("Divine Beasts", res.summary("Ana"))
        again = story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, made_backup=True, processes=[])
        self.assertFalse(any(again.added.values()))                    # idempotent
        self.assertIn("already has", again.summary("Ana"))

    def test_never_removes_or_touches_non_bool(self):
        p = story.slot_files(self.coop)[0]
        story.merge_file(p, NAMES, set())
        self.assertIn(0, story.story_bits(p, NAMES))                  # FirstTower stays 1 with an empty host line
        weird = self.root / "w.sav"
        weird.write_bytes(make_save({"FirstTower_Activated": 5}, extra=(("Wind_Relic_Finish", 0),
                                                                        ("Wind_Relic_Finish", 0))))
        self.assertEqual(story.merge_file(weird, NAMES, {0, 1}), [])   # not 0, or not a single value: left alone

    def test_backup_and_undo(self):
        before = {p: p.read_bytes() for p in story.slot_files(self.coop)}
        story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[])
        self.assertNotEqual(before, {p: p.read_bytes() for p in story.slot_files(self.coop)})
        story.undo(self.coop.parent, self.backups, processes=[])
        self.assertEqual(before, {p: p.read_bytes() for p in story.slot_files(self.coop)})
        self.assertEqual((self.coop / "option.sav").read_bytes(), b"opt")

    def test_refuses_while_the_game_runs(self):
        before = {p: p.read_bytes() for p in story.slot_files(self.coop)}
        with self.assertRaises(OSError):
            story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=["savagewind.exe"])
        (self.coop / "save.lock").write_text("")
        with self.assertRaises(OSError):
            story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[])
        with self.assertRaises(OSError):
            story.undo(self.coop.parent, self.backups, processes=[])
        self.assertEqual(before, {p: p.read_bytes() for p in story.slot_files(self.coop)})
        self.assertFalse(self.backups.exists())

    def test_only_coop_copies(self):
        (self.coop.parent / story.COPY_MARK).unlink()          # (as if it were your own save)
        before = {p: p.read_bytes() for p in story.slot_files(self.coop)}
        with self.assertRaises(PermissionError):
            story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[])
        with self.assertRaises(PermissionError):
            story.undo(self.coop.parent, self.backups, processes=[])
        self.assertEqual(before, {p: p.read_bytes() for p in story.slot_files(self.coop)})
        self.assertFalse(self.backups.exists())

    def test_keeps_ten_backups(self):
        for _ in range(12):
            story.backup(self.coop.parent, self.backups)
        self.assertEqual(len(list(self.backups.iterdir())), story.KEEP_BACKUPS)

    def test_not_a_save(self):
        bad = self.root / "x.sav"
        bad.write_bytes(b"\0" * 40)
        with self.assertRaises(ValueError):
            story.merge_file(bad, NAMES, {0})

    def test_question(self):
        q = story.story_question("Ana")
        self.assertEqual(q[0], "Share story progress with Ana?")
        self.assertIn("backup", q[2])


class FollowerTests(unittest.TestCase):
    def test_story_follower(self):
        f = coop.StoryFollower(10, "0123abcd", host_id=1)
        line = {"type": "story", "from": 1, "seq": 1, "list": "0123abcd", "bits": story.encode_bits({1, 2}, 10)}
        self.assertFalse(f.on_story(dict(line, **{"from": 2})))       # not the host
        self.assertTrue(f.on_story(line))
        self.assertEqual(f.to_add({2, 5}), {1})
        self.assertFalse(f.on_story(dict(line, bits="0000")))          # old seq
        self.assertTrue(f.on_story(dict(line, seq=2, bits="0000")))    # the host loaded an older save ...
        self.assertEqual(f.to_add(set()), {1, 2})                      # ... nothing is taken away
        self.assertFalse(f.on_story(dict(line, seq=3, list="ffffffff")))
        self.assertIn("another story list", f.why_off)


if __name__ == "__main__":
    unittest.main()
