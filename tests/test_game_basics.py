"""native/modhost/botw.h, docs/game-basics.md and the basics_* example mods agree with each other and with
docs/addresses.md: every symbol has its //: doc, the doc's example code is the real code, the examples use only helpers
whose game values were checked in game, and the header's addresses are in the address table.

Reads only this repo's own files. No game files, no compiler (tools/build_native.py builds the examples).
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
import build_native as bn  # noqa: E402
from test_addresses import parse_table  # noqa: E402  (a function: its tests are not collected again here)

HEADER = REPO / "native" / "modhost" / "botw.h"
DOC = REPO / "docs" / "game-basics.md"
EXAMPLES = ("basics_show_pos", "basics_inf_stamina", "basics_teleport_up")
NOT_TESTED = "(not tested in game)"

# The symbols botw.h defines: macros, functions, the frame function type and the frame wrapper's variables.
SYMBOL_RES = (
    re.compile(r"^#define\s+(BOTW_\w+)"),
    re.compile(r"^static\s+inline\s+[\w\s*]+?\b(botw_\w+)\s*\("),
    re.compile(r"^typedef\s+.*\(\s*\*\s*(Botw\w+)\s*\)"),
    re.compile(r"^static\s+(?!inline)[\w\s*]+?\b(botw__\w+)\s*;"),
)
ADDR_RE = re.compile(r"(?<![0-9A-Za-z_])0x([0-9a-fA-F]{10})(?:ull|ULL)?(?![0-9A-Za-z_])")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def header_symbols() -> dict[str, str]:
    """Each symbol of botw.h with its doc (the //: line just above it; '' when there is none)."""
    out, prev = {}, ""
    for line in read(HEADER).splitlines():
        s = line.strip()
        for rx in SYMBOL_RES:
            m = rx.match(s)
            if m:
                out[m.group(1)] = prev[3:].strip() if prev.startswith("//:") else ""
                break
        if s:
            prev = s
    return out


def code_blocks(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^```c\n(.*?)^```", text, re.M | re.S)]


class Header(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.symbols = header_symbols()
        cls.helpers = {n: d for n, d in cls.symbols.items() if n.startswith("botw_") and not n.startswith("botw__")}

    def test_every_symbol_has_a_doc(self):
        self.assertIn("botw_player_pos", self.symbols)  # (the scan works)
        self.assertIn("BOTW_ADDR_PLAYER_INFO", self.symbols)
        self.assertIn("botw__frame_orig", self.symbols)
        self.assertIn("BotwFrameFn", self.symbols)
        for name, doc in self.symbols.items():
            with self.subTest(symbol=name):
                self.assertTrue(doc, f"{name} has no //: line just above it")
                self.assertLessEqual(len(doc), 200, "one short line")

    def test_shared_interface_names(self):
        for name in ("botw_player_pos", "botw_stamina_get", "botw_stamina_set"):
            self.assertIn(name, self.helpers)
        self.assertTrue(any(n.startswith("BOTW_ADDR_") for n in self.symbols))

    def test_untested_helpers_say_so_first(self):
        untested = {n for n, d in self.helpers.items() if NOT_TESTED in d}
        self.assertEqual(untested, set())
        for n in untested:
            self.assertTrue(self.helpers[n].startswith(NOT_TESTED), n)

    def test_newest_helpers_carry_the_date(self):
        """The helpers first checked in game on 2026-11-07 say so where "(not tested in game)" was."""
        for n in ("botw_teleport", "botw_time_of_day", "botw_paused"):
            self.assertTrue(self.helpers[n].startswith("(checked in game 2026-11-07"), n)
        self.assertIn("the pause menu", self.helpers["botw_paused"])
        self.assertIn("map too; not tried", self.helpers["botw_paused"])  # (the map never opened on the test save)
        self.assertIn("let go first", self.helpers["botw_teleport_up"])

    def test_addresses_are_in_the_table(self):
        """Every address botw.h names is a row of docs/addresses.md that was checked in game, unless its doc says
        (not tested in game): then the row says research only (as for offsets, below)."""
        _, rows, _ = parse_table()
        status = {r.address: r.status for r in rows if r.address is not None}
        found = {int(h, 16) for h in ADDR_RE.findall(read(HEADER))}
        self.assertIn(0x7001e13ed0, found)  # (the scan works)
        untested = {int(v, 16) for n, v in re.findall(r"^#define\s+(BOTW_\w+)\s+0x([0-9a-fA-F]+)(?:ull)?\s*$",
                                                       read(HEADER), re.M) if self.symbols[n].startswith(NOT_TESTED)}
        self.assertIn(0x7000e8fae0, untested)  # (the file log's open: research only until a game run)
        for a in sorted(found):
            with self.subTest(address=hex(a)):
                self.assertIn(a, status, "listed in docs/addresses.md")
                if a in untested:
                    self.assertEqual(status[a], "research only", "not tested in game")
                elif a not in (0x7240000000, 0x7320000000):  # (the heap and guest memory: bounds of the pointer check)
                    self.assertIn(status[a], {"used in a mod", "verified"}, "checked in game")

    def test_offsets_are_in_the_table(self):
        """Every offset botw.h names (BOTW_PI_, BOTW_ACTOR_, BOTW_POS_ ...) is a row of docs/addresses.md that was
        checked in game, unless its doc says (not tested in game)."""
        _, rows, _ = parse_table()
        offsets = {}
        for r in rows:
            if r.offset is not None:
                offsets.setdefault(r.offset, set()).add(r.status)
        defines = re.findall(r"^#define\s+(BOTW_\w+)\s+0x([0-9a-fA-F]+)\s*$", read(HEADER), re.M)
        names = {n for n, _ in defines}
        self.assertTrue({"BOTW_PI_POS", "BOTW_ACTOR_PHYSICS", "BOTW_POS_STEP_A", "BOTW_POS_XYZ"} <= names)
        for name, value in defines:
            v = int(value, 16)
            if v >= 0x7000000000:
                continue  # (an address: test_addresses_are_in_the_table)
            with self.subTest(offset=name):
                if self.symbols[name].startswith(NOT_TESTED):
                    continue
                self.assertIn(v, offsets, f"+{v:#x} ({name}) is a row of docs/addresses.md")
                self.assertTrue(offsets[v] & {"used in a mod", "verified"}, "checked in game")
        # the height the hover cheat wrote, which botw_teleport_up writes too
        self.assertIn(0x1c4, offsets)
        self.assertIn("+0x1c4", read(HEADER))

    def test_frame_and_stamina_docs_warn(self):
        """The limits a mod editor user must see in the //: line itself."""
        on_frame = self.helpers["botw_on_frame"]
        self.assertIn("game start", on_frame)
        self.assertIn("not modload", on_frame)
        self.assertIn("one mod per game", on_frame)
        self.assertIn("If it stays 0", self.helpers["botw_frames"])
        self.assertNotIn("BOTW_STAMINA_FULL", read(HEADER))
        self.assertIn("Not the most", self.symbols["BOTW_STAMINA_WHEEL"])
        self.assertIn("climbs", self.symbols["BOTW_POS_XYZ"])
        for name in ("botw_player_real_pos", "botw_teleport_up", "botw_teleport"):  # (in game: true, but no move)
            self.assertIn("climbs", self.helpers[name], name)
        self.assertIn("isn't in the world", self.helpers["botw_stamina_get"])

    def test_pointers_must_be_in_the_heap(self):
        """Pointers the helpers follow are checked against the game's heap, not all of guest memory."""
        text = read(HEADER)
        self.assertRegex(text, r"botw__obj_ok\(uint64_t p\) \{\s*return p >= BOTW_ADDR_HEAP")
        body = text[text.index("static inline uint64_t botw_player_real_pos"):]
        body = body[:body.index("\n}\n")]
        self.assertEqual(body.count("botw__ptr("), 3)   # physics, step A, step B
        self.assertNotIn("botw__rd64(", body)           # (no pointer followed without the heap check)

    def test_includes_swmod_and_has_no_game_code(self):
        text = read(HEADER)
        self.assertIn('#include "swmod.h"', text)
        self.assertIn("#pragma once", text)
        self.assertNotRegex(text, r"\bf_[0-9a-f]{8,16}\s*\(")  # (no generated game functions)


class Doc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read(DOC)
        cls.helpers = {n: d for n, d in header_symbols().items() if n.startswith("botw_")
                       and not n.startswith("botw__")}

    def test_helper_table_matches_the_header(self):
        rows = {}
        for line in self.text.splitlines():
            m = re.match(r"\|\s*`(botw_\w+)\(.*?`\s*\|.*\|\s*(.*?)\s*\|$", line)
            if m:
                rows[m.group(1)] = m.group(2)
        self.assertEqual(set(rows), set(self.helpers))
        for name, cell in rows.items():
            with self.subTest(helper=name):
                self.assertEqual(cell == NOT_TESTED, NOT_TESTED in self.helpers[name])
                dated = re.match(r"\(checked in game (\d{4}-\d\d-\d\d)", self.helpers[name])
                if dated:
                    self.assertTrue(cell.startswith(f"checked in game {dated.group(1)}"), cell)
        self.assertIn("the map not tried", rows["botw_paused"])

    def test_climbing_and_map_limits_are_written_down(self):
        """In game 2026-11-07: no move on a wall (let go first), Savage Wind's teleport failed there and once crashed,
        and the map never opened on the test save."""
        text = " ".join(self.text.split())
        self.assertIn("Let him drop off the wall (B) and land, then it works", text)
        self.assertIn("Don't use it while Link climbs", text)
        self.assertIn("The map is not checked", text)

    def test_constant_table_matches_the_header(self):
        defines = dict(re.findall(r"^#define\s+(BOTW_\w+)\s+(\S+)", read(HEADER), re.M))
        rows = re.findall(r"^\|\s*`(BOTW_\w+)`\s*\|\s*`?([^`|]+?)`?\s*\|", self.text, re.M)
        self.assertGreaterEqual(len(rows), 10)
        for name, value in rows:
            with self.subTest(constant=name):
                self.assertIn(name, defines)
                want = defines[name]
                if want.startswith("0x"):
                    want = want.removesuffix("ull")
                else:
                    want = want.removesuffix("f").removesuffix(".0")  # (1000.0f)
                self.assertEqual(value.lstrip("+"), want)

    def test_example_code_is_the_real_code(self):
        blocks = code_blocks(self.text)
        for n in EXAMPLES:
            with self.subTest(example=n):
                src = read(REPO / "native" / "examples" / n / f"{n}.c")
                self.assertIn(src, blocks, f"docs/game-basics.md doesn't show {n}.c as it is")

    def test_links(self):
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", self.text):
            if "://" not in target:
                with self.subTest(link=target):
                    self.assertTrue((DOC.parent / target).exists(), target)
        self.assertIn("(docs/game-basics.md)", read(REPO / "README.md"))


class Examples(unittest.TestCase):
    def test_each_example_is_built_but_not_installed(self):
        names = [t.name for t in bn.BASICS_EXAMPLES]
        self.assertEqual(names, list(EXAMPLES))
        for t in bn.BASICS_EXAMPLES:
            self.assertEqual(t.subdir, "basics-mods")
            self.assertEqual(t.export, "swmod_init")
            self.assertFalse(t.runtime)
        self.assertFalse(set(EXAMPLES) & {t.name for t in (*bn.EXAMPLES, *bn.HOOK_EXAMPLES, *bn.GHOST_EXAMPLES)})

    def test_examples_use_the_header_and_only_checked_helpers(self):
        untested = {n for n, d in header_symbols().items() if NOT_TESTED in d}
        for n in EXAMPLES:
            folder = REPO / "native" / "examples" / n
            with self.subTest(example=n):
                src = read(folder / f"{n}.c")
                self.assertIn('#include "botw.h"', src)
                self.assertFalse(ADDR_RE.search(src), "addresses come from botw.h")
                for name in untested:
                    self.assertNotRegex(src, rf"\b{name}\b")
                toml = read(folder / "mod.toml")
                self.assertIn(f'windows = "{n}.dll"', toml)
                self.assertIn('game_versions = ["1.9.0"]', toml)

    def test_inf_stamina_starts_again_for_another_save(self):
        """A save loaded from the pause menu or after Game Over has another actor: the held value must be dropped,
        or a 3-wheel value would be written into a 1-wheel save."""
        src = read(REPO / "native" / "examples" / "basics_inf_stamina" / "basics_inf_stamina.c")
        frame = src[src.index("static void every_frame"):src.index("static void cmd_infstamina")]
        self.assertIn("botw_player_actor(api)", frame)
        self.assertRegex(frame, r"link != g_link\)\s*\{\s*g_hold = -1;")
        self.assertNotIn("BOTW_STAMINA_WHEEL", frame)   # (never forces one wheel)
        self.assertIn("never ran", src)                 # (says so when botw_on_frame gets no frames)

    def test_up_only_moves_up(self):
        src = read(REPO / "native" / "examples" / "basics_teleport_up" / "basics_teleport_up.c")
        self.assertIn("d > 0.0 && d <= 1000.0", src)
        self.assertNotIn("up -", src)
        self.assertNotIn("up -", read(DOC))


if __name__ == "__main__":
    unittest.main()
