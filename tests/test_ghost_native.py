"""The ghost mod's animation-name logic in C (native/examples/ghost/ghost.c), without the game.

It builds native/examples/ghost/selftest/ghost_selftest.c (which includes ghost.c and fakes the two animation calls
with a made-up actor) with the zig that ships with Savage Wind, at below-normal priority, and runs it. Skipped when
Savage Wind's zig isn't there (SWMOD_INSTALL, else Documents/SavageWind/Savage Wind).

Checks (review of ghost v2, 2026-11-07):
- names:   the name table has room for every name Link has (130 names the body lacks no longer crowd out the glide
           or an attack), and names from the network can't lock out `ghost anim`;
- missing: a name the actor lacks is asked for at most twice, however often it comes back (no thrash);
- late:    a name the game shows a little late (0.15 s) is not taken for missing.
"""
import ctypes
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import build_native as bn  # noqa: E402
import modmenu  # noqa: E402  (its low-priority runner)

SRC = REPO / "native" / "examples" / "ghost" / "selftest" / "ghost_selftest.c"
OUT = REPO / "build" / "native" / "ghost-selftest"


@unittest.skipUnless(sys.platform == "win32", "Windows only (a Windows DLL)")
class GhostNative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            tc = bn.find_toolchain(bn.default_install())
        except bn.BuildError as e:
            raise unittest.SkipTest(str(e))
        t = bn.Target("ghost_selftest", (SRC,), runtime=False, out_name="ghost_selftest.dll", export="ghost_selftest")
        built = bn.build(tc, OUT, [t], runner=modmenu.low_priority_runner)
        bn.check_exports(zip(built, [t]))
        dll = ctypes.CDLL(str(built[0]))
        buf = ctypes.create_string_buffer(16384)
        dll.ghost_selftest.argtypes = (ctypes.c_char_p, ctypes.c_int)
        dll.ghost_selftest.restype = ctypes.c_int
        cls.fails = dll.ghost_selftest(buf, len(buf))
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

    def test_room_for_every_name(self):
        self.group("names", ["glide_after_130_others", "attack_after_130_others", "room_for_link",
                             "flood_stops_short", "ghost_anim_after_flood", "flood_by_speed"])

    def test_missing_names_asked_twice_at_most(self):
        self.group("missing", ["first_round", "no_thrash", "noted", "shows_wait", "stand_in_cut",
                               "sheikah_by_speed", "move_by_speed"])

    def test_a_late_name_is_not_lost(self):
        self.group("late", ["plays", "not_noted", "plays_again", "missing_after_two"])

    def test_held_items(self):
        """0.6.0 (made-up world): held with the right hand bone after the grab is refused once, switched and cleared
        by delete (never let go), the back on a follow of ours and back again (never a 0 written), a let-go item
        deleted at once, the ghost's removal and a stage change, a refused name given up after 3 tries, a non-weapon
        released, no items on a body without hand bones, nothing at all while off, and `ghost give`. 0.6.2: the
        ghost's grabbed items get the player's scale (not an NPC's), only while they are its; what a stage change
        dropped and the game kept removed after Link is back (and nothing for what the game took along)."""
        self.group("items", ["bones", "held", "in_hand", "same_again", "switch", "quick_switches", "back",
                             "back_toggles", "back_off", "clear_one", "clear_all", "let_go", "ghost_gone",
                             "ghost_back", "stage_change", "stage_change_stray", "orphan_released", "parked", "taken_in_gap",
                             "own_follow_first", "owns_follow_kept", "names", "gave_up", "not_a_weapon", "no_hand_bones",
                             "off_costs_nothing", "give", "size_player", "size_unnamed", "leftovers", "back_copied", "leftovers_gone"])

    def test_coop_story_flags(self):
        """0.7.0 (coop_story.c, a fake flag manager): the story list loads (dlc lines only when asked; a bad name is
        refused), every name is read and published with a seq that only moves on a change, a story line adds only
        0 -> 1 and only listed names, a bad line is refused, nothing is read or set while the busy bit is up or
        there is no world, a refused set is tried again, and the developer probe reads one flag. With no known getter
        (the game's, today: the first guess crashed the game in probe run B) nothing is called for a read and the list
        never says "ready". 0.7.1: only towers and memories are added live (the rest is "held" for the next join's
        save merge), only once Link is free (no loading, cutscene, riding or pause menu) for a few frames in a row,
        what was added is set again after a load, and an add read while another list was loaded is refused."""
        self.group("coop", ["load", "bad_list", "load_dlc", "reads", "seq_steady", "bad_hex", "adds_live_only", "held",
                            "get_hex", "busy_waits", "retry", "no_world", "world_back", "not_free_waits",
                            "settles_first", "after_settle", "set_again", "gen_refused", "probe_read",
                            "no_getter_no_call", "no_getter_not_ready"])

    def test_who_am_i(self):
        """0.8.0 (iam.c): `iam` before any client says to start it and keeps a word as typed; the client's list
        (`ghost looks`) is taken whole or refused whole; an alias in any case picks its id; a name not on the list is
        refused with the names; next/back go round the list; the `ghost` reply carries has_iam, iam_seq and iam; the
        client's own list (11 characters with aliases) fits. A pick says "a few seconds" only while a client asks
        (`ghost` in the last 6 s), else "Kept" (no list) or that the client tells them when it runs; `ghost looks
        <list> <pick> <seen>` clears the typed word only when nothing newer was typed; a bad `seen` is refused."""
        self.group("iam", ["no_list_says_start", "no_list_kept", "no_list_says_kept", "list_taken", "alias_any_case",
                           "no_client_running_says_so", "unknown_refused", "next_back_wrap", "lists_names",
                           "bad_lists_refused", "bad_words_refused", "report", "client_running_says_soon",
                           "looks_seen_clears_old_word", "looks_bad_seen_refused", "client_list_fits"])

    def test_costume_ragdoll_and_ball(self):
        """0.9.2 (puppet.c, docs/charsel.md "Ragdoll", round 3): the slide he gets up into on a hill, the big knock-back
        and a hard landing are followed like the ragdoll; his turn from his rest goes onto the costume's own rest (and
        a resource with no usable rest is refused); the ground's slope comes from his motion; the limbs swing loosely,
        never more than PUP_LIMP_MAX, and settle; the ball's shape and place keep its lowest point on the ground. In the
        made-up world: the pose is written again right after the game's own unit step, over the clip (a unit with no
        animation, one of another class), the slide is followed over the hill, Daruk's sprint ball rolls at speed /
        radius and comes back upright, a short state of doubt doesn't show Link, and no why is left for another pick."""
        self.group("puppet", ["rd_detect", "rd_detect_slides", "rd_pose", "rd_lift", "rd_rest_retarget", "rd_rest_refused",
                              "rd_motion_slope", "rd_limp", "ball_shape_place"])
        if ("puppet", "rd_world") not in self.rows:  # (the made-up world ran)
            self.group("puppet", ["short_doubt_keeps", "rd_follow", "rd_getup", "rd_after_step", "rd_slide_followed",
                                  "ball_rolls", "rd_why_cleared"])

    def test_nothing_else_failed(self):
        self.assertEqual(self.fails, 0, self.report)


if __name__ == "__main__":
    unittest.main()
