"""docs/addresses.md: every game address the code and docs use is in its table, and each row is true to the files
it names (the address is really there, and a row marked "used in a mod" names a mod that has it).

Reads only this repo's own files. No game files.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = "docs/addresses.md"
BUILD_190 = "cd57b23fa4bbad65"
STATUSES = {"used in a mod", "verified", "research only", "made up (selftest)"}
COLUMNS = ["Address", "What it is", "Where the repo uses it", "How it was verified", "Status"]

# A guest address: 0x followed by 10 hex digits in the game's memory (main starts at 0x7000000000, guest memory ends
# at 0x7320000000), as Python or C writes it (a C constant may end in ull).
LO, HI = 0x7000000000, 0x7400000000
ADDR_RE = re.compile(r"(?<![0-9A-Za-z_])0x([0-9a-fA-F]{10})(?:ull|ULL)?(?![0-9A-Za-z_])")
ROW_ADDR_RE = re.compile(r"`0x([0-9a-f]{10})`")
ROW_OFF_RE = re.compile(r"`\+0x([0-9a-f]+)`")


def read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def addresses_in(text: str) -> set[int]:
    return {v for v in (int(h, 16) for h in ADDR_RE.findall(text)) if LO <= v < HI}


def code_files() -> list[Path]:
    """The files whose constants must be listed: the example mods, modhost (not its made-up selftest) and the
    tools."""
    files = [p for p in (REPO / "native" / "examples").glob("*/*") if p.is_file()]
    files += [p for p in (REPO / "native" / "modhost").glob("*") if p.is_file()]
    files += list((REPO / "tools").glob("*.py")) + list((REPO / "tools" / "mp").glob("*.py"))
    return sorted(files)


def doc_files() -> list[Path]:
    files = [REPO / "README.md", REPO / "native" / "modhost" / "README.md"] + list((REPO / "docs").glob("*.md"))
    return sorted(p for p in files if p.name != "addresses.md" and p.is_file())


def rel(p: Path) -> str:
    return p.relative_to(REPO).as_posix()


class Row:
    def __init__(self, cells: list[str], line: int):
        self.cells, self.line = cells, line
        self.addr_cell, self.what, self.where, self.how, self.status = cells
        m = ROW_ADDR_RE.fullmatch(self.addr_cell)
        o = ROW_OFF_RE.fullmatch(self.addr_cell)
        self.address = int(m.group(1), 16) if m else None
        self.offset = int(o.group(1), 16) if o else None
        self.files = re.findall(r"`([^`]+)`", self.where)

    def __repr__(self):
        return f"{DOC} line {self.line}: {self.addr_cell}"

    def found_in(self, text: str) -> bool:
        if self.address is not None:
            return self.address in {int(h, 16) for h in ADDR_RE.findall(text)}
        return re.search(rf"(?<![0-9A-Za-z_])0x0*{self.offset:x}(?![0-9A-Za-z_])", text, re.I) is not None


def parse_table() -> tuple[list[str], list[Row], list[str]]:
    """The table's header, its address rows, and its group rows (a bold name in the first cell, the rest empty)."""
    header, rows, groups = None, [], []
    for n, line in enumerate(read(DOC).splitlines(), 1):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if re.fullmatch(r"\|[\s|:-]+\|", s):
            continue
        if header is None:
            header = cells
        elif cells[0].startswith("**"):
            assert all(c == "" for c in cells[1:]), f"{DOC} line {n}: a group row has only its name"
            groups.append(cells[0].strip("*"))
        else:
            assert len(cells) == len(COLUMNS), f"{DOC} line {n}: {len(cells)} cells, not {len(COLUMNS)}"
            rows.append(Row(cells, n))
    return header or [], rows, groups


class AddressTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.header, cls.rows, cls.groups = parse_table()
        cls.listed = {r.address: r for r in cls.rows if r.address is not None}

    def test_shape(self):
        self.assertEqual(self.header, COLUMNS)
        self.assertGreaterEqual(len(self.rows), 40)
        for want in ("Memory layout", "Player", "Pointers and offsets", "Actor spawn, move and delete",
                     "Per-frame job", "Animation", "Stamina"):
            self.assertIn(want, self.groups)
        seen = set()
        for r in self.rows:
            with self.subTest(row=r):
                self.assertTrue(r.address is not None or r.offset is not None, "an address or a +0x offset")
                self.assertIn(r.status, STATUSES)
                self.assertTrue(r.what and r.how, "says what it is and how it was verified")
                self.assertTrue(r.files, "names at least one file")
                if r.address is not None:
                    self.assertTrue(LO <= r.address < HI, "in the game's memory")
                    self.assertNotIn(r.address, seen, "listed once")
                    seen.add(r.address)

    def test_says_which_version(self):
        text = read(DOC)
        self.assertIn("1.9.0", text)
        self.assertIn(BUILD_190, text)
        self.assertIn(f'"{BUILD_190}"', read("tools/sw_rebuild.py"))  # (the id the tools know as 1.9.0)
        self.assertIn("main+0xcc7410", text)  # the module offset example
        self.assertIn("0x7000000000 + 0xcc7410", text)

    def test_every_address_in_the_code_is_listed(self):
        found = {}
        for p in code_files():
            for a in addresses_in(p.read_text(encoding="utf-8", errors="replace")):
                found.setdefault(a, []).append(rel(p))
        self.assertIn(0x7000cc7410, found)  # (the scan works)
        self.assertIn(0x7001129af8, found)
        for a, where in sorted(found.items()):
            with self.subTest(address=hex(a), files=where):
                self.assertIn(a, self.listed, f"0x{a:x} ({', '.join(where)}) is not in {DOC}")

    def test_every_address_in_the_docs_is_listed(self):
        for p in doc_files():
            for a in addresses_in(p.read_text(encoding="utf-8")):
                with self.subTest(address=hex(a), doc=rel(p)):
                    self.assertIn(a, self.listed, f"0x{a:x} ({rel(p)}) is not in {DOC}")

    def test_each_row_is_in_the_files_it_names(self):
        for r in self.rows:
            for f in r.files:
                with self.subTest(row=r, file=f):
                    if f.startswith("out/"):
                        # a local research note: not in the repo, so nothing in the repo uses the address
                        self.assertEqual(r.status, "research only")
                        continue
                    path = REPO / f
                    self.assertTrue(path.is_file(), f"{f} doesn't exist")
                    self.assertTrue(r.found_in(path.read_text(encoding="utf-8")), f"{r.addr_cell} isn't in {f}")

    def test_used_in_a_mod_means_a_mod_has_it(self):
        mods = {rel(p) for p in (REPO / "native" / "examples").glob("*/*") if p.is_file()}
        for r in self.rows:
            with self.subTest(row=r):
                in_mods = [f for f in r.files if f in mods]
                if r.status == "used in a mod":
                    self.assertTrue(in_mods, "names the mod's file")
                else:
                    self.assertFalse(in_mods, "a row naming a mod's file is marked used in a mod")

    def test_every_address_a_mod_has_is_marked_used(self):
        for p in (REPO / "native" / "examples").glob("*/*.c"):
            for a in addresses_in(p.read_text(encoding="utf-8")):
                with self.subTest(address=hex(a), mod=rel(p)):
                    self.assertEqual(self.listed[a].status, "used in a mod")
                    self.assertIn(rel(p), self.listed[a].files)

    def test_made_up_addresses_stay_in_the_selftest(self):
        made_up = {a for a, r in self.listed.items() if r.status == "made up (selftest)"}
        self.assertTrue(made_up)
        for p in [*(REPO / "native" / "examples").glob("*/*"), *doc_files()]:
            if p.is_file():
                with self.subTest(file=rel(p)):
                    self.assertFalse(made_up & addresses_in(p.read_text(encoding="utf-8")))

    def test_file_open_rows(self):
        """The four file opens modhost's file log wraps (docs/code-mods.md "File log"): research only until a game run
        checks them, and named where botw.h, the selftest and the docs have them."""
        self.assertIn("File open (modhost's file log)", self.groups)
        for a in (0x7000e8fae0, 0x7000e92690, 0x700115cba0, 0x7000eaba98):
            with self.subTest(address=hex(a)):
                r = self.listed[a]
                self.assertEqual(r.status, "research only")
                for f in ("native/modhost/botw.h", "tools/build_native.py", "docs/code-mods.md"):
                    self.assertIn(f, r.files)

    def test_linked(self):
        self.assertIn("(docs/addresses.md)", read("README.md"))
        self.assertIn("(addresses.md)", read("docs/code-mods.md"))


if __name__ == "__main__":
    unittest.main()
