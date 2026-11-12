"""Tests for the co-op agent (tools/mp/coop_agent.py): a host and a friend on 127.0.0.1 with stand-in games and
made-up save files (never a real save, never the game)."""
import asyncio
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from mp import coop_agent, story  # noqa: E402

NAMES = ["FirstTower_Activated", "Wind_Relic_Finish", "Clear_RemainsWind", "Open_Dungeon000", "Clear_Dungeon000",
         "MapTower_01", "IsGet_MemoryPhoto_001", "IsGet_PlayerStole2", "IsGet_Obj_Camera", "IsPlayed_Demo001_0"]


def make_save(values: dict) -> bytes:
    out = bytearray(struct.pack("<3I", story.SAVE_MAGIC, 0xffffffff, 1))
    for name, v in values.items():
        out += struct.pack("<II", story.name_hash(name), v)
    return bytes(out + b"\0\0\0\0")


def quiet(*_a, **_k):
    pass


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.host_lib, self.guest_lib = root / "host-lib", root / "guest-lib"
        self.coop = root / "guest-lib" / "joined" / "world1"
        for slot in ("0", "1"):
            (self.coop / "save_0" / slot).mkdir(parents=True)
            (self.coop / "save_0" / slot / story.SAVE_NAME).write_bytes(make_save({n: 0 for n in NAMES}))
        story.mark_coop_copy(self.coop)
        self.sl = story.StoryList(NAMES)

    def tearDown(self):
        self.tmp.cleanup()

    async def run_pair(self, share, *, code=None, guest_code=None, flip=None):
        host_game = coop_agent.FakeGame(21.0, {0, 2, 7})
        guest_game = coop_agent.FakeGame(9.0, set())
        stop = asyncio.Event()
        port = asyncio.get_running_loop().create_future()
        host = asyncio.create_task(coop_agent.host_session(
            self.host_lib, "Ana", host_game, self.sl, code=code, port=0, relay_host="127.0.0.1", stop=stop, log=quiet,
            tick=0.1, started=port.set_result))
        p = await asyncio.wait_for(port, 5)
        await asyncio.sleep(0.3)
        gstop = asyncio.Event()
        guest = asyncio.create_task(coop_agent.join_session(
            self.guest_lib, "Ben", "127.0.0.1", self.coop, self.sl, game=guest_game, code=guest_code, port=p,
            share=share, stop=gstop, log=quiet, processes=[]))
        await asyncio.sleep(1.0)
        if flip is not None:
            host_game.bits.add(flip)
            await asyncio.sleep(1.0)
        stop.set()
        out = await asyncio.wait_for(guest, 5)
        counts = await asyncio.wait_for(host, 5)
        return out, counts, host_game, guest_game

    async def test_story_at_join_and_live(self):
        out, counts, host_game, guest_game = await self.run_pair(True, flip=5)
        self.assertIsNone(out["error"])
        self.assertIn("Ana's story added", out["merged"])
        self.assertTrue(Path(out["backup"]).is_dir())                    # backup before the merge
        for f in story.slot_files(self.coop):                             # every slot got the host's story
            self.assertEqual(story.story_bits(f, NAMES), {0, 2, 7})
        self.assertIn(21.0, guest_game.set_hours)                         # the host's time
        self.assertIn(5, guest_game.bits)                                 # a live change reached the friend's game
        self.assertEqual(out["ended"], "Ana ended the session")
        self.assertGreater(counts["story"], 0)

    async def test_no_means_no_story(self):
        out, _, _, guest_game = await self.run_pair(False, flip=5)
        self.assertIsNone(out["merged"])
        self.assertIsNone(out["backup"])
        for f in story.slot_files(self.coop):
            self.assertEqual(story.story_bits(f, NAMES), set())
        self.assertNotIn(5, guest_game.bits)
        self.assertIn(21.0, guest_game.set_hours)                         # time is still shared

    async def test_room_code(self):
        out, *_ = await self.run_pair(True, code="apple river stone", guest_code="apple river rock")
        self.assertEqual(out["error"], "wrong room code")
        for f in story.slot_files(self.coop):
            self.assertEqual(story.story_bits(f, NAMES), set())


if __name__ == "__main__":
    unittest.main()
