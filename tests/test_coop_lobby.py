"""Tests for the Multiplayer menu's state machine (tools/mp/lobby.py) and the co-op save copies (tools/mp/worlds.py).
Made-up hosts and made-up save folders only: no window, no game, never a real save."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from mp import lobby, security, story, worlds  # noqa: E402

ANA = {"name": "Ana", "world": "Ana's world", "players": 2, "max": 8, "code": True, "port": 7788, "ver": "0.7.0",
       "check": "apple river stone moon", "address": "192.168.1.20"}
BO = dict(ANA, name="Bo", world="Bo's world", code=False, address="192.168.1.21")


class LobbyTests(unittest.TestCase):
    def test_solo_and_host(self):
        self.assertEqual(lobby.Lobby().key("3"), {"mode": "solo"})
        self.assertEqual(lobby.Lobby().key("esc"), {"mode": "solo"})
        self.assertEqual(lobby.Lobby().key("1"), {"mode": "host"})

    def test_join_with_code_and_story(self):
        lb = lobby.Lobby()
        self.assertIn("looking for hosts", lb.lines()[2])
        lb.set_hosts([ANA, BO])
        self.assertIn("Ana's world  -  Ana  -  2 of 8 players  -  needs a room code", lb.lines()[2])
        lb.key("2")
        self.assertIsNone(lb.key("enter"))
        self.assertEqual(lb.screen, "code")
        lb.key("down")                     # first wheel: the second word
        lb.key("right")
        lb.key("up")                       # second wheel: the last word
        self.assertEqual(lb.code(), f"{security.WORDS[1]} {security.WORDS[-1]} {security.WORDS[0]}")
        lb.key("enter")
        self.assertEqual(lb.lines()[0], "Share story progress with Ana?")
        plan = lb.key("enter")
        self.assertEqual(plan, {"mode": "join", "address": "192.168.1.20", "port": 7788, "world": "Ana's world",
                                "code": lb.code(), "share": True})

    def test_join_no_code_story_no_and_back(self):
        lb = lobby.Lobby()
        lb.set_hosts([ANA, BO])
        lb.key("2")
        lb.key("esc")
        self.assertEqual(lb.screen, "main")
        lb.key("2")
        lb.key("down")
        lb.set_hosts([BO, ANA])           # the list changes order: the selection stays on Bo
        self.assertEqual(lb.hosts[lb.sel]["name"], "Bo")
        lb.key("enter")
        self.assertEqual(lb.screen, "story")   # no code needed
        plan = lb.key("esc")
        self.assertEqual((plan["share"], plan["code"]), (False, None))


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.own = self.root / "own-save"
        (self.own / "save_0" / "0").mkdir(parents=True)
        (self.own / "save_0" / "0" / story.SAVE_NAME).write_bytes(b"\x30\x47\0\0" + b"\1" * 40)
        (self.own / "save_0" / "option.sav").write_bytes(b"opt")
        self.lib = self.root / "mp-lib"

    def tearDown(self):
        self.tmp.cleanup()

    def test_copy_once_source_untouched(self):
        before = (self.own / "save_0" / "0" / story.SAVE_NAME).read_bytes()
        dest = worlds.joined_copy(self.own, self.lib, "Ana's world", processes=[])
        self.assertEqual(dest, self.lib / "joined" / "Anas_world")
        self.assertEqual((dest / "save_0" / "0" / story.SAVE_NAME).read_bytes(), before)
        self.assertTrue(story.is_coop_copy(dest))
        self.assertFalse(story.is_coop_copy(self.own))                 # the source is never marked
        (dest / "save_0" / "0" / story.SAVE_NAME).write_bytes(b"changed")
        again = worlds.joined_copy(self.own, self.lib, "Ana's world", processes=[])   # reused, not copied again
        self.assertEqual((again / "save_0" / "0" / story.SAVE_NAME).read_bytes(), b"changed")
        self.assertEqual((self.own / "save_0" / "0" / story.SAVE_NAME).read_bytes(), before)

    def test_refusals(self):
        with self.assertRaises(OSError):
            worlds.host_world(self.own, self.lib, "w", processes=["savagewind.exe"])
        with self.assertRaises(ValueError):
            worlds.copy_save(self.own, self.own / "inside", processes=[])
        with self.assertRaises(ValueError):
            worlds.copy_save(self.own, self.root / "x" / "y", protect=[self.root / "x"], processes=[])
        with self.assertRaises(FileNotFoundError):
            worlds.copy_save(self.root / "nothing", self.root / "z", processes=[])
        self.assertFalse((self.lib / "worlds").exists())

    def test_world_id(self):
        self.assertEqual(worlds.world_id("Ana's world"), "Anas_world")
        self.assertEqual(worlds.world_id("../.."), "world")


if __name__ == "__main__":
    unittest.main()
