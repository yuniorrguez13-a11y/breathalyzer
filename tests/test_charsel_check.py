"""The Who am I in-game check's judging (tools/mp/charsel_ingame_check.py), on made-up replies: no game."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from mp import charsel_ingame_check as cic  # noqa: E402


class Judges(unittest.TestCase):
    def test_spawn(self):
        self.assertEqual(cic.judge_spawn({"state": "live", "as_ok": True, "name": "X"})[0], "PASS")
        self.assertEqual(cic.judge_spawn({"state": "pending", "as_ok": False})[0], "FAIL")
        self.assertEqual(cic.judge_spawn({"state": "live", "as_ok": False})[0], "FAIL")   # (no animation holder)

    def test_anims(self):
        self.assertEqual(cic.judge_anims({"Wait": "Wait", "CutS1": "CutS1"}, ["Wait", "CutS1"])[0], "PASS")
        status, text = cic.judge_anims({"Wait": "Wait", "ClimbMove": "Wait"}, ["Wait", "ClimbMove"])
        self.assertEqual(status, "FAIL")
        self.assertIn("ClimbMove", text)

    def test_iam(self):
        self.assertEqual(cic.judge_iam("link, linkle, zelda", {"iam": "goron"}, "iam: no ganon. Try: ...")[0], "PASS")
        self.assertEqual(cic.judge_iam("", {"iam": "goron"}, "iam: no ganon")[0], "FAIL")
        self.assertEqual(cic.judge_iam("zelda", {"iam": "daruk"}, "iam: no ganon")[0], "FAIL")
        self.assertEqual(cic.judge_iam("zelda", {"iam": "goron"}, "")[0], "FAIL")

    def test_bot_looks(self):
        self.assertEqual(cic.judge_bot_looks(["A", "A", "B", "C"], ["A", "B", "C"])[0], "PASS")
        status, text = cic.judge_bot_looks(["A", "Dm_Npc_Sheiker_Searcher_M_001"], ["A", "B"])
        self.assertEqual(status, "FAIL")
        self.assertIn("never ['B']", text)

    def test_nopack(self):
        self.assertEqual(cic.judge_nopack(True, {"state": "idle", "wanted": False})[0], "PASS")
        self.assertEqual(cic.judge_nopack(False, {})[0], "FAIL")                   # (the game stopped answering)
        self.assertEqual(cic.judge_nopack(True, {"state": "live"})[0], "FAIL")
        cat = {"built": {"gerudo": {"slot": "Dm663_Npc_Gerudo_S_F"}}}
        slot = cic.free_without_pack(cat)
        self.assertIn(slot, cic.charsel.FREE_SLOTS)
        self.assertNotEqual(slot, "Dm663_Npc_Gerudo_S_F")                          # (never one a character uses)
        self.assertIsNone(cic.free_without_pack({"built": {str(i): {"slot": s}
                                                           for i, s in enumerate(cic.charsel.FREE_SLOTS)}}))

    def test_never_the_players_own_folder(self):
        self.assertNotEqual(cic.PACKS, cic.charsel.DEFAULT_OUT)                    # (the check builds its own)

    def test_log(self):
        log = ("[ghost] ready\n[res] heap too small for Actor/Pack/Dm663_Npc_Assasin_A.sbactorpack\n"
               "[res] heap note about something else\n")
        status, text = cic.judge_log(log, ["Dm663_Npc_Assasin_A"])
        self.assertEqual(status, "FAIL")
        self.assertIn("Dm663_Npc_Assasin_A", text)
        self.assertEqual(cic.judge_log("[res] heap note about something else\n", ["Dm663_Npc_Assasin_A"])[0], "PASS")
        self.assertEqual(cic.judge_log("[res] SwChar_zelda resource failed\n", [])[0], "FAIL")


if __name__ == "__main__":
    unittest.main()
