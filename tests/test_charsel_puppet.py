"""See yourself as your pick, live (ghost mod 0.9.0, native/examples/ghost/puppet.c): no game.

- The DLL's own table (which actor name each character's pack is on, its name, its aliases, the word `charsel
  playas` takes) matches tools/charsel.py, so `iam daruk` in the game spawns the pack charsel builds for Daruk.
- The selftest's "puppet" group (native/examples/ghost/selftest/ghost_selftest.c, built with Savage Wind's zig):
  Link's Move by speed (Daruk's sprint is RunGear2, his rolling ball), near names (ClimbJump -> ClimbMove), Link shown
  where the character can't mirror him (a horse, a climb with no climb clip, a cutscene), the pause menu keeps it,
  Link hidden with the game's model show/hide and hidden again when the game shows him by itself, the puppet at
  Link's place, Link's items to the puppet only without a friend's ghost, a stage change, `iam link`, `iam self off`,
  and a pack the game refuses (given up after 5 tries, "Restart the game to play as Daruk").
"""

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import charsel  # noqa: E402

GHOST = REPO / "native" / "examples" / "ghost"
PUPPET_C = GHOST / "puppet.c"
SRC = GHOST / "selftest" / "ghost_selftest.c"
OUT = REPO / "build" / "native" / "charsel-puppet-selftest"


def dll_slots() -> dict[str, tuple[str, str, str]]:
    text = PUPPET_C.read_text(encoding="utf-8")
    block = text.split("PUP_SLOTS[] = {", 1)[1].split("};", 1)[0]
    return {m[0]: (m[1], m[2], m[3]) for m in re.findall(r'\{"([^"]*)", "([^"]*)", "([^"]*)", "([^"]*)"\}', block)}


def dll_aliases() -> dict[str, str]:
    text = PUPPET_C.read_text(encoding="utf-8")
    block = text.split("PUP_ALIASES[] = {", 1)[1].split("};", 1)[0]
    return dict(re.findall(r'\{"([^"]*)", "([^"]*)"\}', block))


class Table(unittest.TestCase):
    def test_every_pack_character_on_charsels_actor_name(self):
        slots = dll_slots()
        packs = [r.id for r in charsel.RECIPES.values() if r.kind == "pack"]
        self.assertEqual(sorted(slots), sorted(packs))
        for cid, (actor, name, playas) in slots.items():
            with self.subTest(cid=cid):
                r = charsel.RECIPES[cid]
                self.assertEqual(actor, charsel.SLOTS[cid])
                self.assertEqual(name, r.name.split(" (")[0])
                want = charsel.playas_word(cid) if cid in charsel.PLAYAS_PEOPLE else ""
                self.assertEqual(playas, want)
                if playas:
                    self.assertEqual(charsel.find_id(playas), cid)   # (`charsel playas <word>` takes it)

    def test_aliases_match(self):
        want = {a: r.id for r in charsel.RECIPES.values() if r.kind == "pack" for a in r.aliases}
        self.assertEqual(dll_aliases(), want)

    def test_versions(self):
        toml = (GHOST / "mod.toml").read_text(encoding="utf-8")
        ver = re.search(r'^version = "([^"]+)"', toml, re.M)[1]
        self.assertEqual(ver, "0.9.0")
        self.assertIn(f'#define GHOST_VERSION "{ver}"', (GHOST / "ghost.c").read_text(encoding="utf-8"))
        self.assertIn('#include "puppet.c"', (GHOST / "ghost.c").read_text(encoding="utf-8"))

    def test_hint_words(self):
        self.assertIn("Restart the game to play as Daruk", charsel.playas_hint("goron"))
        self.assertIn("charsel.py playas daruk", charsel.playas_hint("goron"))
        self.assertIn("iam daruk", charsel.playas_hint("goron"))
        self.assertNotIn("Restart", charsel.playas_hint("bokoblin"))
        self.assertEqual(charsel.playas_hint("link"), "")


@unittest.skipUnless(sys.platform == "win32", "Windows only (a Windows DLL)")
class Selftest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import build_native as bn
        import modmenu
        try:
            tc = bn.find_toolchain(bn.default_install())
        except bn.BuildError as e:
            raise unittest.SkipTest(str(e))
        t = bn.Target("ghost_selftest", (SRC,), runtime=False, out_name="ghost_selftest.dll", export="ghost_selftest")
        built = bn.build(tc, OUT, [t], runner=modmenu.low_priority_runner)
        # (run in a process of its own: the made-up world sits at fixed addresses, and test_ghost_native's copy of
        # the same selftest may hold them in this one)
        import subprocess
        code = ("import ctypes,sys; d=ctypes.CDLL(sys.argv[1]); b=ctypes.create_string_buffer(1<<18); "
                "d.ghost_selftest.argtypes=(ctypes.c_char_p,ctypes.c_int); d.ghost_selftest(b,len(b)); "
                "sys.stdout.write(b.value.decode('utf-8','replace'))")
        out = subprocess.run([sys.executable, "-I", "-c", code, str(built[0])], capture_output=True, text=True,
                             timeout=300)
        cls.rows = {}
        for line in out.stdout.splitlines():
            group, name, status, detail = (line.split(" ", 3) + ["", "", "", ""])[:4]
            cls.rows[(group, name)] = (status, detail)

    def test_puppet(self):
        names = ["move_by_speed", "near_names", "unmirrored_shows_link", "ids", "sentences", "spawns_and_swaps",
                 "at_link", "sprint_and_walk", "rehide", "climb_near_name", "horse_shows_link", "cutscene_shows_link",
                 "pause_keeps", "items_to_puppet", "stage_change", "iam_link_back", "self_off", "refused_gives_up",
                 "no_tick_says_why"]
        if ("puppet", "world") in self.rows:      # (the made-up globals' page was taken: the pure part only)
            names = names[:5]
        for name in names:
            with self.subTest(check=name):
                status, detail = self.rows.get(("puppet", name), ("MISSING", "not run"))
                self.assertEqual(status, "PASS", detail)

    def test_nothing_failed(self):
        bad = {k: v for k, v in self.rows.items() if v[0] == "FAIL"}
        self.assertEqual(bad, {})


if __name__ == "__main__":
    unittest.main()
