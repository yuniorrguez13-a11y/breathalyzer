"""Tests for shared time (tools/mp/coop.TimeFollower): made-up hours and statuses, no game."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from mp import coop  # noqa: E402


class TimeTests(unittest.TestCase):
    def test_follows_only_the_host(self):
        f = coop.TimeFollower(host_id=1)
        self.assertFalse(f.on_time({"type": "time", "from": 2, "h": 21.0}))   # a friend's time: ignored
        self.assertIsNone(f.decide(9.0, "play"))
        self.assertTrue(f.on_time({"type": "time", "from": 1, "h": 21.0}))
        self.assertEqual(f.decide(9.0, "play"), 21.0)
        self.assertFalse(f.on_time({"type": "time", "from": 1, "h": 25.0}))
        self.assertFalse(f.on_time({"type": "time", "from": 1, "h": True}))

    def test_slack_and_busy(self):
        f = coop.TimeFollower(host_id=1)
        f.on_time({"from": 1, "h": 23.99})
        self.assertIsNone(f.decide(0.01, "play"))              # 1.2 game minutes apart across midnight
        self.assertEqual(f.decide(0.2, "play"), 23.99)
        for busy in ("menu", "cutscene", "loading", None):
            self.assertIsNone(f.decide(12.0, busy), busy)

    def test_no_host(self):
        f = coop.TimeFollower(host_id=None)
        self.assertFalse(f.on_time({"from": None, "h": 3.0}))

    def test_gap(self):
        self.assertAlmostEqual(coop.hour_gap(21.9, 0.1), 2.2)
        self.assertAlmostEqual(coop.hour_gap(3.0, 5.0), 2.0)


if __name__ == "__main__":
    unittest.main()
