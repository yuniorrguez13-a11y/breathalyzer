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
        self.assertEqual(res.backup, self.backups / story.BEFORE_COOP)  # the first merge keeps "before co-op"
        self.assertEqual(len(before), 3)                               # every numbered slot
        shared = story.settle(NAMES, self.host)
        self.assertEqual(shared, {0, 2, 3, 7})                         # a shrine's clear is held back
        for p, old in before.items():
            new = p.read_bytes()
            self.assertEqual(len(new), len(old))                       # size never changes
            self.assertEqual(story.story_bits(p, NAMES), shared | had[p])   # a union
            diff = [k for k in range(0, len(old), 4) if old[k:k + 4] != new[k:k + 4]]
            for k in diff:                                             # only values, only 0 -> 1
                self.assertEqual(struct.unpack_from("<I", old, k)[0], 0)
                self.assertEqual(struct.unpack_from("<I", new, k)[0], 1)
            self.assertFalse(p.with_name(p.name + ".new").exists())
        self.assertIn("Divine Beasts", res.summary("Ana"))
        again = story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[])
        self.assertFalse(any(again.added.values()))                    # idempotent
        self.assertIsNone(again.backup)                                # nothing to add: no backup either
        self.assertIn("already has", again.summary("Ana"))
        self.assertEqual(story._stamped(self.backups), [])

    def test_never_removes_or_touches_non_bool(self):
        p = story.slot_files(self.coop)[0]
        story.merge_file(p, NAMES, set())
        self.assertIn(0, story.story_bits(p, NAMES))                  # FirstTower stays 1 with an empty host line
        weird = self.root / "w.sav"
        weird.write_bytes(make_save({"FirstTower_Activated": 5}, extra=(("Wind_Relic_Finish", 0),
                                                                        ("Wind_Relic_Finish", 0))))
        self.assertEqual(story.merge_file(weird, NAMES, {0, 1}), [])   # not 0, or not a single value: left alone

    def slots(self):
        return {p: p.read_bytes() for p in story.slot_files(self.coop)}

    def test_backup_and_undo(self):
        original = self.slots()
        story.merge_folder(self.coop.parent, NAMES, {0, 2}, self.backups, processes=[])     # first join
        first = self.slots()
        self.assertNotEqual(original, first)
        story.merge_folder(self.coop.parent, NAMES, {0, 2, 5}, self.backups, processes=[])  # second join: a tower
        second = self.slots()
        self.assertEqual(len(story._stamped(self.backups)), 1)         # a backup from before the second join
        story.undo(self.coop.parent, self.backups, processes=[])       # "last": back to after the first join
        self.assertEqual(self.slots(), first)
        undone = story._stamped(self.backups / story.UNDONE)
        self.assertEqual(len(undone), 1)                               # what undo replaced is kept too
        self.assertEqual(story.story_bits(undone[0] / "save_0" / "1" / story.SAVE_NAME, NAMES), {0, 2, 5})
        story.undo(self.coop.parent, self.backups, processes=[], which=story.BEFORE_COOP)
        self.assertEqual(self.slots(), original)                       # byte for byte, before any co-op story
        self.assertEqual((self.coop / "option.sav").read_bytes(), b"opt")
        self.assertNotEqual(second, original)

    def test_undo_refuses_another_copys_backup(self):
        story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[])
        story.mark_coop_copy(self.coop.parent, "some other save")     # (as if this were another copy)
        before = self.slots()
        with self.assertRaises(ValueError):
            story.undo(self.coop.parent, self.backups, processes=[])
        self.assertEqual(before, self.slots())

    def test_refuses_while_the_game_runs(self):
        """Savage Wind holds <folder>.lock NEXT TO the save folder (LockFileEx, byte 0). A held lock stops the merge
        and undo; the lock file left behind by a closed game does not."""
        before = self.slots()
        with self.assertRaises(story.GameRunning):
            story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=["savagewind.exe"])
        lock = story.lock_path(self.coop.parent)
        self.assertEqual(lock.parent, self.coop.parent.parent)        # a sibling, not inside the folder
        self.assertEqual(lock.name, self.coop.parent.name + ".lock")
        game = story.SaveLock(self.coop.parent).acquire()             # (a running game, as the game takes it)
        try:
            with self.assertRaises(story.GameRunning):
                story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[])
            with self.assertRaises(story.GameRunning):
                story.undo(self.coop.parent, self.backups, processes=[])
            self.assertIsNotNone(story.game_running(self.coop.parent, processes=[]))
        finally:
            game.release()
        self.assertEqual(before, self.slots())
        self.assertFalse(self.backups.exists())
        self.assertTrue(lock.exists())                                # the file stays after the game ends ...
        self.assertIsNone(story.game_running(self.coop.parent, processes=[]))   # ... and blocks nothing
        story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[])

    def test_merge_holds_the_lock(self):
        """While the merge writes, the game can't take its lock (it would warn and not share the folder)."""
        seen = []
        real = story._write_checked

        def spy(*a):
            try:
                story.SaveLock(self.coop.parent).acquire().release()
                seen.append("free")
            except story.GameRunning:
                seen.append("held")
            return real(*a)

        story._write_checked = spy
        try:
            story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[])
        finally:
            story._write_checked = real
        self.assertEqual(set(seen), {"held"})

    def test_all_slots_or_none(self):
        """A slot that fails its check: no slot is changed and no .new file is left."""
        before = self.slots()
        real, calls = story._write_checked, []

        def flaky(path, old, new, changed):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(path, old, new, changed)

        story._write_checked = flaky
        try:
            with self.assertRaises(OSError):
                story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[])
        finally:
            story._write_checked = real
        self.assertEqual(before, self.slots())
        self.assertFalse([p for p in self.coop.rglob("*.new")])

    def test_bound_to_one_host(self):
        story.merge_folder(self.coop.parent, NAMES, {0}, self.backups, processes=[], host_key="aa" * 8)
        self.assertEqual(story.read_mark(self.coop.parent)["host"], "aa" * 8)
        before = self.slots()
        with self.assertRaises(story.WrongHost):
            story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[], host_key="bb" * 8)
        self.assertEqual(before, self.slots())
        story.mark_coop_copy(self.coop.parent, "", check="apple river stone moon")   # made for one host's words
        with self.assertRaises(story.WrongHost):
            story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[], host_key="cc" * 8,
                               check_words=lambda fp: "other words here now")
        self.assertEqual(before, self.slots())

    def test_only_coop_copies(self):
        (self.coop.parent / story.COPY_MARK).unlink()          # (as if it were your own save)
        before = {p: p.read_bytes() for p in story.slot_files(self.coop)}
        with self.assertRaises(PermissionError):
            story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[])
        with self.assertRaises(PermissionError):
            story.undo(self.coop.parent, self.backups, processes=[])
        self.assertEqual(before, {p: p.read_bytes() for p in story.slot_files(self.coop)})
        self.assertFalse(self.backups.exists())

    def test_keeps_ten_backups_and_before_coop(self):
        story.merge_folder(self.coop.parent, NAMES, self.host, self.backups, processes=[])
        for _ in range(12):
            story.backup(self.coop.parent, self.backups)
        self.assertEqual(len(story._stamped(self.backups)), story.KEEP_BACKUPS)
        self.assertTrue((self.backups / story.BEFORE_COOP).is_dir())  # never rotated away

    def test_not_a_save(self):
        bad = self.root / "x.sav"
        bad.write_bytes(b"\0" * 40)
        with self.assertRaises(ValueError):
            story.merge_file(bad, NAMES, {0})

    def test_question(self):
        q = story.story_question("Ana")
        self.assertEqual(q[0], "Share story progress with Ana?")
        self.assertIn("backup", q[2])
        self.assertIn("Heart Container", " ".join(q))                  # what a shared beast costs is said


# A host caught mid-story: inside the Fire beast (entered, Blight seen, not freed), mid Vah Medoh's approach battle,
# Wind freed, the Champion's Tunic step done, two beasts' "freed" quest steps.
STORY = ["FirstTower_Activated", "FirstTower_Finish", "Wind_Relic_Activated", "Wind_Relic_BattlePlaying",
         "Wind_Relic_ParashawlPlaying", "Fire_Relic_Activated", "Fire_Relic_BattlePlaying", "Fire_Relic_Finished",
         "Find_4Relic_Activated", "Find_4Relic_1stClear", "Find_4Relic_2ndClear", "Find_4Relic_Finish",
         "PictureMemory_Activated", "PictureMemory_Find11", "PictureMemory_GetShirt",
         "CarryingBlueFireEXMini_Carry", "Get_MasterSword_Activated",
         "Clear_RemainsWind", "Enter_RemainsWind", "Die_PGanonWind", "Appear_PGanonWind",
         "Enter_RemainsFire", "Appear_PGanonFire", "FirstMessage_RemainsFire",
         "Open_Dungeon000", "Enter_Dungeon000", "Clear_Dungeon000", "MapTower_01", "IsGet_MemoryPhoto_001",
         "IsGet_PlayerStole2", "IsGet_Obj_RemoteBomb", "IsPlayed_Demo010_1"]


class SettleTests(unittest.TestCase):
    def shared(self, host_names):
        return {STORY[i] for i in story.settle(STORY, {STORY.index(n) for n in host_names})}

    def test_host_mid_beast_and_mid_quest(self):
        host = set(STORY) - {"Fire_Relic_Finished", "Find_4Relic_Finish", "Find_4Relic_2ndClear"}
        got = self.shared(host)
        for n in ("Enter_RemainsFire", "Appear_PGanonFire", "FirstMessage_RemainsFire",   # Fire not freed yet
                  "Wind_Relic_BattlePlaying", "Wind_Relic_ParashawlPlaying",              # Medoh's quest not done
                  "Fire_Relic_BattlePlaying", "PictureMemory_Find11", "CarryingBlueFireEXMini_Carry",
                  "PictureMemory_GetShirt",                                                # Impa's tunic: his own
                  "Clear_Dungeon000", "IsGet_Obj_RemoteBomb"):                             # held until checked
            self.assertNotIn(n, got, n)
        for n in ("Clear_RemainsWind", "Enter_RemainsWind", "Die_PGanonWind", "Appear_PGanonWind",   # Wind freed
                  "Wind_Relic_Activated", "Fire_Relic_Activated", "Find_4Relic_Activated", "PictureMemory_Activated",
                  "Find_4Relic_1stClear",                                                  # 1 beast freed: a fact
                  "FirstTower_Activated", "FirstTower_Finish", "Get_MasterSword_Activated",
                  "Open_Dungeon000", "Enter_Dungeon000", "MapTower_01", "IsGet_MemoryPhoto_001",
                  "IsGet_PlayerStole2", "IsPlayed_Demo010_1"):
            self.assertIn(n, got, n)

    def test_finished_quest_shares_its_steps(self):
        got = self.shared({"Fire_Relic_Activated", "Fire_Relic_BattlePlaying", "Fire_Relic_Finished"})
        self.assertEqual(got, {"Fire_Relic_Activated", "Fire_Relic_BattlePlaying", "Fire_Relic_Finished"})

    def test_mid_beast_merge_on_a_save(self):
        """A save merged with a host caught inside a beast gets nothing of that beast."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / story.SAVE_NAME
            p.write_bytes(make_save({n: 0 for n in STORY}, extra=()))
            host = {STORY.index(n) for n in ("Enter_RemainsFire", "Appear_PGanonFire", "Wind_Relic_Activated",
                                               "Wind_Relic_BattlePlaying", "MapTower_01")}
            added = story.merge_file(p, STORY, host)
            self.assertEqual(set(added), {"Wind_Relic_Activated", "MapTower_01"})

    def test_live_only_towers_and_memories(self):
        self.assertTrue(story.live_ok("MapTower_01"))
        self.assertTrue(story.live_ok("IsGet_MemoryPhoto_001"))
        for n in ("Clear_RemainsWind", "Clear_Dungeon000", "Open_Dungeon000", "FirstTower_Activated",
                  "IsPlayed_Demo010_1", "IsGet_PlayerStole2"):
            self.assertFalse(story.live_ok(n), n)


class FollowerTests(unittest.TestCase):
    def test_story_follower(self):
        f = coop.StoryFollower(10, "0123abcd", host_id=1, names=NAMES)
        line = {"type": "story", "from": 1, "seq": 1, "list": "0123abcd", "bits": story.encode_bits({1, 2, 5}, 10)}
        self.assertFalse(f.on_story(dict(line, **{"from": 2})))       # not the host
        self.assertTrue(f.on_story(line))
        self.assertTrue(f.changed)
        self.assertEqual(f.to_add({2}), {5})                           # live: the tower only
        self.assertFalse(f.on_story(dict(line, bits="0000")))          # old seq
        self.assertTrue(f.on_story(dict(line, seq=2, bits="0000")))    # the host loaded an older save ...
        self.assertEqual(f.to_add(set()), {5})                         # ... nothing is taken away
        self.assertEqual(f.host_bits, {1, 2, 5})
        self.assertFalse(f.on_story(dict(line, seq=3, list="ffffffff")))
        self.assertIn("another story list", f.why_off)
        f.new_host(7)                                                  # the host's agent came back: new id, new seq
        self.assertTrue(f.on_story(dict(line, **{"from": 7, "seq": 1})))
        self.assertEqual(coop.StoryFollower(10, "0123abcd", 1).to_add(set()), set())   # no names: nothing live


if __name__ == "__main__":
    unittest.main()
