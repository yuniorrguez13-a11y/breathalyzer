"""More Room's bookkeeping in C (native/examples/room/room_core.h), without the game.

It builds native/examples/room/selftest/room_selftest.c with the zig that ships with Savage Wind, at below-normal
priority, and runs it. Skipped when Savage Wind's zig isn't there (SWMOD_INSTALL, else Documents/SavageWind/Savage Wind).

Checks: watched names (the modded actors), the cap from the PC's RAM and graphics memory (a 16 GB gaming PC, the 8 GB
laptop, a nearly full one), an actor's need from its own failed try and the extra its next creation gets (never for a
game actor, never past the cap), a fixed boost, the plain-words texts, and the texture rescue's bookkeeping.
"""
import ctypes
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import build_native as bn  # noqa: E402
import modmenu  # noqa: E402  (its low-priority runner)

SRC = REPO / "native" / "examples" / "room" / "selftest" / "room_selftest.c"
OUT = REPO / "build" / "native" / "room-selftest"


@unittest.skipUnless(sys.platform == "win32", "Windows only (a Windows DLL)")
class RoomNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            tc = bn.find_toolchain(bn.default_install())
        except bn.BuildError as e:
            raise unittest.SkipTest(str(e))
        t = bn.Target("room_selftest", (SRC,), runtime=False, out_name="room_selftest.dll", export="room_selftest")
        built = bn.build(tc, OUT, [t], runner=modmenu.low_priority_runner)
        bn.check_exports(zip(built, [t]))
        dll = ctypes.CDLL(str(built[0]))
        buf = ctypes.create_string_buffer(16384)
        dll.room_selftest.argtypes = (ctypes.c_char_p, ctypes.c_int)
        dll.room_selftest.restype = ctypes.c_int
        cls.fails = dll.room_selftest(buf, len(buf))
        cls.report = buf.value.decode("utf-8", "replace")
        cls.rows = {}
        for line in cls.report.splitlines():
            group, name, status, detail = (line.split(" ", 3) + ["", "", "", ""])[:4]
            cls.rows[(group, name)] = (status, detail)

    def group(self, group: str, names: list[str]) -> None:
        for name in names:
            with self.subTest(check=f"{group} {name}"):
                status, detail = self.rows.get((group, name), ("MISSING", "not run"))
                self.assertEqual(status, "PASS", detail)

    def test_watched_names(self):
        self.group("watch", ["add", "twice", "other", "long_refused", "full_stops"])

    def test_cap_from_the_machine(self):
        self.group("cap", ["gaming_pc", "gaming_vram", "laptop", "laptop_vram_shared", "floor", "text"])

    def test_need_from_the_failure(self):
        self.group("need", ["logged", "from_failure", "next_creation", "class_extra_counted", "unwatched_none",
                            "game_heap_no_need", "grows", "repeats_quiet"])

    def test_boost(self):
        self.group("boost", ["fixed", "fixed_unwatched", "capped", "off", "auto_nothing_needed"])

    def test_texts(self):
        self.group("text", ["fail", "report", "textures_fit", "textures_warn", "report_small_buffer"])

    def test_model_names(self):
        """A model file named as its file (the cause of the Linkle and Custom failures, 2026-11-15)."""
        self.group("names", ["yaz0", "fres_name", "link_inside_costume", "own_name_ok", "tex_misnamed", "not_yaz0",
                             "name_out_of_range"])

    def test_texture_rescue(self):
        """TextureArchiveWork's failed allocations go to the extra memory (docs/room.md): which heaps, its size from
        the PC (64 MB to 1 GB), its blocks, the game's root heap list, the log rate, the plain-words lines."""
        self.group("rescue", ["default_textures", "add_heap", "full_stops", "off", "size_gaming_pc", "size_laptop",
                              "size_floor", "too_big", "size_200mb_model", "size_asked", "fit_now", "fit_commit_headroom",
                              "fit_low_ram_no_wrap", "fit_unknown", "fit_small_asked", "fit_need",
                              "step_first_makes", "step_made_uses", "step_waits_while_committing",
                              "step_no_wait_in_game_heap_code", "step_waiter_tries_own_ask", "step_wait_bounded",
                              "step_refused", "commit_failed_tries_later", "no_address_space_never",
                              "not_made_has_nothing", "has", "root_slot",
                              "log_rate", "used", "line", "vram_quiet", "vram_warn_once", "text_made",
                              "text_not_made", "text_not_now", "text_waiting"])

    def test_no_failures(self):
        self.assertEqual(self.fails, 0, self.report)


if __name__ == "__main__":
    unittest.main()
