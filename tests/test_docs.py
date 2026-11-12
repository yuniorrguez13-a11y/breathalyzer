"""Checks that the docs say what the tools do: every command a doc shows parses, and the facts the docs state
(verdicts, reply fields, id rules, messages, default paths, what was tested in game) match the code.

Only made-up data: tiny fake mods, cheat files and folders in a temp folder. No game files, and nothing connects to
the game (the cheat runner's connection is replaced so a test can't reach a running game by mistake).
"""
import argparse
import contextlib
import importlib
import io
import json
import os
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))
import cheats as ch  # noqa: E402
import check_clean as cc  # noqa: E402
import mod_inspect as mi  # noqa: E402
import modman  # noqa: E402
import romfs_mods  # noqa: E402
import sw_rebuild as sw  # noqa: E402

# The docs these tests cover. (docs/ghost.md and docs/multiplayer.md can join once their own work is done, and so can
# the Mods page v2 docs being written: docs/mod-settings.md, docs/fonts.md, docs/mod-pictures.md.)
DOCS = ["README.md", "NOTICE.md", "docs/cheats.md", "docs/code-mods.md", "docs/mod-manager.md", "docs/MOD-COMPAT.md",
        "docs/mod-format.md", "docs/rebuild.md", "docs/exefs-patches.md", "docs/hooks.md", "docs/romfs-mods.md",
        "docs/code-map.md", "docs/PLAN.md", "docs/addresses.md", "native/modhost/README.md", "docs/profiles.md"]

BUILD_190 = "cd57b23fa4bbad65"
CMD_RE = re.compile(r"^python\s+tools[\\/]((?:mp[\\/])?[A-Za-z_]+)\.py(?:\s+(.*))?$")


def read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def flat(text: str) -> str:
    """Text with every run of whitespace made one space (doc lines wrap anywhere)."""
    return " ".join(text.split())


def section(text: str, heading: str) -> str:
    """The text under a heading, up to the next heading of the same or a higher level."""
    m = re.search(rf"^(#+) {re.escape(heading)}\s*$", text, re.M)
    assert m, f"no heading '{heading}'"
    level = len(m.group(1))
    end = re.compile(rf"^#{{1,{level}}} ", re.M).search(text, m.end())
    return text[m.end():end.start() if end else len(text)]


def table_rows(text: str) -> list[list[str]]:
    """The body rows of the markdown tables in text (not the header rows), as stripped cells."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        if re.fullmatch(r"\|[\s|:-]+\|", s):
            rows.pop()  # (the line before the separator is the header)
            continue
        rows.append([c.strip() for c in s.strip("|").split("|")])
    return rows


def doc_commands(text: str) -> list[str]:
    """Every `python tools/...` command in a doc: in code blocks (PowerShell ` continuations joined) and in inline
    code (which may wrap onto the next line)."""
    found: list[str] = []

    def fence(m: re.Match) -> str:
        buf = ""
        for line in m.group(1).splitlines():
            s = (buf + " " + line.strip()).strip() if buf else line.strip()
            buf = ""
            if s.endswith("`"):
                buf = s[:-1].rstrip()
                continue
            if CMD_RE.match(s):
                found.append(s)
        return ""

    rest = re.sub(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", fence, text, flags=re.M | re.S)
    for m in re.finditer(r"`([^`]+)`", rest):
        s = flat(m.group(1))
        if CMD_RE.match(s):
            found.append(s)
    return found


def argv_variants(args: str) -> list[list[str]]:
    """A doc command's arguments as argv lists: comments, pipes and redirects cut, <placeholders> filled in, and
    `check|apply` style alternatives tried one by one."""
    args = re.sub(r"(^|\s+)#.*$", "", args)
    args = re.sub(r"<[^<>]*>", "x", args)
    args = re.split(r"\s+\|\s+", args)[0]
    args = re.split(r"\s+>\s+", args)[0]
    toks = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t
            for t in shlex.split(args, posix=False)]
    variants: list[list[str]] = [[]]
    for t in toks:
        alts = t.split("|") if "|" in t and not t.startswith("-") else [t]
        variants = [v + [a] for v in variants for a in alts]
    return variants


class _Parsed(Exception):
    def __init__(self, parser, ns):
        super().__init__("parsed")
        self.parser, self.ns = parser, ns


def parse(tool: str, argv: list[str]):
    """Run the tool's main() only as far as its argument parser: returns (parser, namespace), or raises
    AssertionError with argparse's message. Nothing after the parse runs."""
    module = importlib.import_module(tool.replace("/", ".").replace("\\", "."))
    orig = argparse.ArgumentParser.parse_args

    def stop_after_parse(self, args=None, namespace=None):
        raise _Parsed(self, orig(self, args, namespace))

    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(argparse.ArgumentParser, "parse_args", stop_after_parse), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            module.main(argv)
        except _Parsed as p:
            return p.parser, p.ns
        except SystemExit as e:
            raise AssertionError(f"{tool} {argv}: exit {e.code}: {err.getvalue().strip()}") from None
    raise AssertionError(f"{tool} {argv}: main() returned without parsing its arguments")


def subparser(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[name]
    raise AssertionError(f"no subcommand {name}")


def options_of(parser: argparse.ArgumentParser) -> set[str]:
    return {o for a in parser._actions for o in a.option_strings}


class DocCommandsParse(unittest.TestCase):
    """Every command a doc shows must be one the tool accepts."""

    def test_every_command_parses(self):
        total = 0
        for rel in DOCS:
            for cmd in doc_commands(read(rel)):
                tool, args = CMD_RE.match(cmd).groups()
                for argv in argv_variants(args or ""):
                    total += 1
                    with self.subTest(doc=rel, command=cmd, argv=argv):
                        if tool == "check_clean":
                            self.assertLessEqual(len(argv), 1, "check_clean takes at most one folder")
                        else:
                            parse(tool, argv)
        self.assertGreater(total, 40)  # (the extraction itself still finds the commands)

    def test_extraction(self):
        text = ("```powershell\npython tools\\romfs_mods.py deploy --out \"D:\\o\" `\n    \"D:\\m\"  # c\n```\n"
                "See `python tools/build_native.py\nuninstall` and `python tools/gen_hooks.py check|apply <f> <h>`.")
        cmds = doc_commands(text)
        self.assertEqual(cmds, ['python tools\\romfs_mods.py deploy --out "D:\\o" "D:\\m"  # c',
                                "python tools/build_native.py uninstall",
                                "python tools/gen_hooks.py check|apply <f> <h>"])
        self.assertEqual(argv_variants(CMD_RE.match(cmds[0]).group(2)), [["deploy", "--out", "D:\\o", "D:\\m"]])
        self.assertEqual(argv_variants(CMD_RE.match(cmds[2]).group(2)), [["check", "x", "x"], ["apply", "x", "x"]])
        with self.assertRaises(AssertionError):
            parse("modman", ["no-such-command"])

    def test_option_tables(self):
        """The options tables name options the commands have."""
        p, _ = parse("cheats", ["run", "x.txt"])
        run = options_of(subparser(p, "run"))
        for row in table_rows(section(read("docs/cheats.md"), "Options")):
            for opt in re.findall(r"`(--?[a-z][a-z-]*)", row[0]):
                self.assertIn(opt, run, f"cheats.md option {opt}")
        build = options_of(subparser(sw.parser(), "build"))
        for row in table_rows(section(read("docs/rebuild.md"), "Options")):
            for opt in re.findall(r"`(--?[a-z][a-z-]*)", row[0]):
                self.assertIn(opt, build, f"rebuild.md option {opt}")


# One made-up mod per row of MOD-COMPAT's "By mod type" table, found by a piece of the row's first cell.
WIIU_RULES = "[Definition]\ntitleIds = 00050000101C9400\nname = Made-up pack\n"
COMPAT_SAMPLES = {
    "Native mod for this loader": {"mod.toml": '[mod]\nid = "test.made-up"\nname = "Made up"\nversion = "1.0"\n'},
    "Switch RomFS mod": {"romfs/Actor/Pack/MadeUp.txt": "made up"},
    "UKMM package": {"meta.yml": "name: Made up\nplatform: universal\n", "manifest.yml": "- Actor/MadeUp.txt\n"},
    "BCML `.bnp`, Switch": {"info.json": '{"platform": "switch"}', "logs/packs.json": "{}",
                            "01007EF00011E000/romfs/Actor/MadeUp.txt": "made up"},
    "BCML `.bnp`, Wii U": {"info.json": '{"platform": "wiiu"}', "logs/packs.json": "{}",
                           "content/Actor/MadeUp.txt": "made up"},
    "Wii U data that can be merged": {"content/Actor/MadeUp.sbyml": "made up"},
    "Wii U event flows": {"content/EventFlow/MadeUp.bfevfl": "made up"},
    "Wii U models": {"content/Model/MadeUp.sbfres": "made up"},
    "Wii U audio": {"content/Sound/MadeUp.bfstm": "made up"},
    "ExeFS patch (`.ips`, `.pchtxt`) for 1.9.0": {"exefs/made-up.pchtxt": f"@nsobid-{BUILD_190}{'0' * 48}\n"},
    "ExeFS patch for another version": {"exefs/made-up.pchtxt": f"@nsobid-0123456789abcdef{'0' * 48}\n"},
    "cheats for 1.9.0": {f"cheats/{BUILD_190}.txt": "[Made up]\n04000000 00001234 00000001\n"},
    "exlaunch / Skyline code mod": {"exefs/subsdk9": "made up", "exefs/main.npdm": "made up"},
    "Cemu code patch": {"Pack/rules.txt": WIIU_RULES, "Pack/patches.txt": "[MadeUp]\n"},
    "Cemu shader pack": {"Pack/rules.txt": WIIU_RULES, "Pack/0123456789abcdef_0123456789abcdef_ps.txt": "made up"},
    "Cemu settings preset": {"Pack/rules.txt": WIIU_RULES},
}


class ModCompatMatchesModInspect(unittest.TestCase):
    """docs-modcompat-vs-mod-inspect: MOD-COMPAT says mod_inspect gives its verdicts, so each row must."""

    def setUp(self):
        self.text = read("docs/MOD-COMPAT.md")

    def test_each_mod_type_gets_the_documented_verdict(self):
        rows = table_rows(section(self.text, "By mod type"))
        self.assertGreater(len(rows), 10)
        used = set()
        for kind, verdict, _how in rows:
            keys = [k for k in COMPAT_SAMPLES if k in kind]
            self.assertEqual(len(keys), 1, f"MOD-COMPAT row '{kind}': give it one made-up sample in this test")
            used.add(keys[0])
            want = verdict.split()[0]
            with tempfile.TemporaryDirectory() as d, self.subTest(row=kind):
                for rel, data in COMPAT_SAMPLES[keys[0]].items():
                    p = Path(d, rel)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(data, encoding="utf-8")
                findings = mi.inspect(mi.open_source(Path(d)))
                self.assertTrue(findings)
                self.assertEqual({f.verdict for f in findings}, {want},
                                 [(f.kind, f.verdict, f.detail) for f in findings])
        self.assertEqual(used, set(COMPAT_SAMPLES), "a sample no row uses")

    def test_verdict_table(self):
        listed = [r[0] for r in table_rows(self.text.split("## By mod type")[0])]
        self.assertTrue(listed)
        for v in listed:
            self.assertIn(v, mi.VERDICTS)
        used = {r[1].split()[0] for r in table_rows(section(self.text, "By mod type"))}
        self.assertLessEqual(used, set(listed))

    def test_build_order_names_the_current_api(self):
        """docs-modcompat-build-order-stale: the native mods step names the mod API swmod.h has."""
        api = re.search(r"#define SWMOD_API_VERSION (\d+)", read("native/modhost/swmod.h")).group(1)
        line = next(ln for ln in section(self.text, "Build order").splitlines() if "Native code mods" in ln)
        self.assertIn(f"API {api}", line)
        self.assertNotIn("next: hooks", line)


class ModsReplySample(unittest.TestCase):
    """docs-code-mods-mods-reply-sample: the sample `mods` reply has exactly the fields modhost sends, in order."""

    def test_fields_match_modhost(self):
        c = read("native/modhost/modhost.c")
        body = re.search(r"static void cmd_mods\(.*?\n}\n", c, re.S).group(0)
        sent = [(field or key).replace("%d", "0") for field, key in
                re.findall(r'control_field_\w+\(r, "([^"]+)"|snprintf\(key, sizeof key, "([^"]+)", i\)', body)]
        self.assertIn("hook_slots", sent)
        doc = section(read("docs/code-mods.md"), "The `mods` command")
        sample = re.search(r"```\n(\{\"ok\".*?)```", doc, re.S).group(1)
        data = json.loads(sample)["data"]
        self.assertEqual(list(data), sent)
        self.assertEqual(data["host"], re.search(r'#define MODHOST_VERSION "([^"]+)"', c).group(1))
        self.assertEqual(str(data["api"]),
                         re.search(r"#define SWMOD_API_VERSION (\d+)", read("native/modhost/swmod.h")).group(1))


class ModFormatIdRules(unittest.TestCase):
    """docs-mod-format-id-name-rules: each rule modman applies to ids and names is in the key table."""

    def setUp(self):
        rows = {r[0]: r[2] for r in table_rows(section(read("docs/mod-format.md"), "The keys"))}
        self.id_row, self.name_row = flat(rows["`[mod] id`"]), flat(rows["`name`"])

    def test_id_rules(self):
        # (each of these looks fine by the basic pattern; only the extra rule refuses it)
        extra = {"con": "`con`", "nul.mods": "before its first `.`", "com1.x": "`com0`-`com9`",
                 "lpt9": "`lpt0`-`lpt9`", "a..b": "no `..`", "abc.": "Not ending in `.`",
                 "cd57b23fa4bbad65": "16 hex digits"}
        for mod_id, phrase in extra.items():
            with self.subTest(id=mod_id):
                self.assertTrue(modman.MANIFEST_ID_RE.match(mod_id))
                self.assertFalse(modman.manifest_id_ok(mod_id))
                self.assertIn(phrase, self.id_row)
        for mod_id in ("yourname.my-mod", "aux-x", "a" * 64, "0.b_c-d"):
            self.assertTrue(modman.manifest_id_ok(mod_id), mod_id)
        self.assertFalse(modman.manifest_id_ok("a" * 65))
        self.assertIn("at most 64", self.id_row)
        self.assertFalse(modman.manifest_id_ok("-a"))
        self.assertIn("starting with a letter or digit", self.id_row)

    def test_name_length(self):
        def name_error(n: int) -> bool:
            _, errs = modman.parse_manifest(f'[mod]\nid = "test.made-up"\nname = "{"N" * n}"\nversion = "1.0"\n')
            return any("[mod] name" in e for e in errs)

        longest = next(n for n in range(1, 5000) if name_error(n + 1))
        self.assertFalse(name_error(longest))
        self.assertIn(f"at most {longest} characters", self.name_row)


class ModFormatV2(unittest.TestCase):
    """The Mods page v2 contract: every mod.toml key modman knows is in the key table, the deploy order and the owners
    of files made at deploy are the ones modman uses, and the library files are named."""

    def setUp(self):
        self.text = read("docs/mod-format.md")
        self.cells = [r[0] for r in table_rows(section(self.text, "The keys"))]

    def test_every_key_is_in_the_table(self):
        for table, keys in modman.MANIFEST_KEYS.items():
            if keys is None:
                want = (f"`[{table}]`", f"`[[{table}]]`")
                self.assertTrue(any(w in c for c in self.cells for w in want), f"[{table}] has no row")
                continue
            for k in keys:
                with self.subTest(table=table, key=k):
                    self.assertTrue(any(re.search(rf"`(\[{table}\] )?{k}`", c) for c in self.cells),
                                    f"[{table}] {k} has no row")

    def test_deploy_order_and_owners(self):
        self.assertEqual(modman.FEATURES, ("mod_settings", "font", "menu_bg", "mod_pictures"))
        steps = section(self.text, "What deploy does with them")
        order = [steps.find(w) for w in ("option folders", "font tool", "title picture", "pictures tool",
                                         "settings.txt")]
        self.assertTrue(all(i >= 0 for i in order) and order == sorted(order), order)
        for owner in modman.GENERATED_OWNER.values():
            self.assertIn(f"`{owner}`", steps)
        self.assertTrue(all(o.startswith(romfs_mods.GENERATED) for o in modman.GENERATED_OWNER.values()))

    def test_library_files(self):
        files = flat(section(self.text, "Files in the library"))
        for name in (f"`{modman.SETTINGS_DIR}\\<id>.json`", f"`{modman.PICTURES_DIR}\\<id>\\chosen.png`",
                     f"`{modman.PROFILES_DIR}\\<name>.json`", f"`deploy\\{modman.HISTORY}`",
                     f"`{modman.PICTURES_DIR}\\{modman.TITLE_PICK}\\`", f"`{modman.PICTURES_DIR}\\{modman.MODS_PAGE_PICK}\\`"):
            self.assertIn(name, files)
        self.assertIn("``" + " ".join("{}#$%@^`|~") + "``", self.text)   # (the symbols add warns about)
        self.assertEqual(modman.MENU_SYMBOLS, set("{}#$%@^`|~"))


class ProfilesDocs(unittest.TestCase):
    def setUp(self):
        self.text = read("docs/profiles.md")

    def test_file_example_is_what_modman_writes(self):
        sample = json.loads(re.search(r"```json\n(.*?)```", self.text, re.S).group(1))
        lib = modman.Library(tempfile.gettempdir(), None, [{"id": "x", "name": "X", "enabled": True, "kinds": []}])
        with mock.patch.object(modman, "read_setting_values", return_value={}):
            snap = modman.profile_snapshot(lib, "Hard mode")
        self.assertEqual(list(sample), list(snap))
        self.assertEqual((sample["tool"], sample["kind"]), (modman.TOOL_ID, "profile"))
        self.assertEqual(os.path.basename(modman.profile_file(lib, sample["name"])), "hard-mode.json")
        self.assertIn("`hard-mode.json`", self.text)
        self.assertEqual(modman.DEFAULT_PROFILE, "Default")

    def test_history_and_names(self):
        text = flat(self.text)
        self.assertIn(f"past {2 * modman.HISTORY_KEEP} lines only the newest {modman.HISTORY_KEEP} stay", text)
        types_ = [r[0] for r in table_rows(section(self.text, "History"))]
        self.assertEqual(types_, ["`deploy`", "`start`", "`result`"])
        self.assertIn("up to 40 letters, digits, spaces, `.`, `-` and `_`, starting with a letter or digit", text)
        self.assertTrue(modman.profile_name_ok("A" * 40) and not modman.profile_name_ok("A" * 41))
        self.assertFalse(modman.profile_name_ok(" lead") or modman.profile_name_ok("a/b"))


class ModManagerDocs(unittest.TestCase):
    def setUp(self):
        self.text = flat(read("docs/mod-manager.md"))

    def test_failed_rebuild_names_the_hooks(self):
        """docs-mod-manager-rebuild-fail-hooks: modman's own message names the hooks, so the doc does too."""
        src = (TOOLS / "modman.py").read_text(encoding="utf-8")
        m = re.search(r"left = .{0,800}?everything except \{left\}", src, re.S)
        self.assertTrue(m)
        self.assertIn('"the hooks" if hooks', m.group(0))
        para = re.search(r"rebuild itself fails.*?\.(?: [A-Z*-]|$)", self.text).group(0)
        self.assertIn("everything except the code patches and the hooks", para)

    def test_cheat_merge_as_documented(self):
        """Each mod's cheats in their own group; same cheat kept once; a different one renamed; a broken master
        leaves its mod out (made-up cheats)."""
        with tempfile.TemporaryDirectory() as d:
            files = {"Mod A": "{Master A}\n40000000 00000000 00000010\n[Hearts]\n04000000 00001000 00000001\n",
                     "Mod B": "{Master B}\n40000000 00000000 00000020\n[Hearts]\n04000000 00002000 00000001\n",
                     "Mod C": "{Master C}\nB0000000\n[Arrows]\n04000000 00003000 00000001\n"}
            items = []
            for mod, text in files.items():
                p = Path(d, mod, "cheats", f"{BUILD_190}.txt")
                p.parent.mkdir(parents=True)
                p.write_text(text, encoding="utf-8")
                items.append((mod, str(p)))
            r = modman.merge_cheats(items)
        self.assertIn("// group: Mod A", r.text)
        self.assertIn("// group: Mod B", r.text)
        self.assertIn("{Master B}", r.text)
        self.assertEqual(r.names, ["Hearts", "Hearts (Mod B)"])
        self.assertTrue(any("its cheats were left out" in a for a in r.attention))
        self.assertIn("`// group: <mod name>`", self.text)
        good = flat(section(read("docs/mod-manager.md"), "Good to know"))
        bullet = next(b for b in good.split(" - ") if "master code" in b)
        self.assertNotIn("only the first mod's cheats", bullet)
        self.assertIn("each mod's in its own", bullet)
        self.assertIn("`<name> (<mod name>)`", bullet)
        self.assertIn("same name, the same code and the same master code", bullet)
        self.assertIn("left out", bullet)
        self.assertTrue(modman.CHEAT_RE.search(f"Cheats/{BUILD_190.upper()}.TXT"))
        self.assertIn("in any case", self.text)

    def test_tested_in_game_is_current(self):
        """docs-mod-manager-tested-stale: what the overnight log shows ran in game isn't called untested."""
        tested = section(read("docs/mod-manager.md"), "What is tested")
        self.assertNotIn("haven't run against the real game yet", tested)
        only_fake = flat(tested.split("Tested with made-up files only")[1])
        for ran_in_game in ("mod.toml", "combined hooks list", "--hooks"):
            self.assertNotIn(ran_in_game, only_fake)
        self.assertIn("2026-11-07", tested)

    def test_disk_space_matches_what_the_library_links(self):
        """A data file of exactly 1 MiB is a link, and a drive without hard links gets copies; the bullet is wrapped."""
        good = section(read("docs/mod-manager.md"), "Good to know")
        bullet = re.search(r"^- \*\*Disk space\.\*\*.*?(?=^- |\Z)", good, re.M | re.S).group(0)
        self.assertTrue(all(len(line) <= 116 for line in bullet.splitlines()), bullet)
        text = flat(bullet)
        self.assertFalse(modman.keep_own_copy("romfs/Model/Big.bin", 2**20))
        self.assertTrue(modman.keep_own_copy("romfs/Model/Big.bin", 2**20 - 1))
        self.assertIn("Data files of 1 MiB or more", text)
        self.assertNotIn("Bigger data files", text)
        self.assertIn("when the drive has them", text)
        self.assertIn("drive without hard links (FAT32, exFAT) are copied", text)

    def test_modman_docstring_calls_a_link_one_file(self):
        """A hard link is one file with two names, not two copies."""
        self.assertNotIn("neither copy", modman.__doc__)
        self.assertIn("stay hard links to the mod's folder (no extra space): the same file", flat(modman.__doc__))


class CheatsDocs(unittest.TestCase):
    def setUp(self):
        self.text = flat(read("docs/cheats.md"))
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name, f"{BUILD_190}.txt")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, text: str, *args: str) -> tuple[int, str]:
        self.path.write_text(text, encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(ch, "Control", side_effect=AssertionError("would connect to the game")), \
                contextlib.redirect_stdout(out):
            code = ch.main([args[0], str(self.path), *args[1:]])
        return code, out.getvalue()

    def template(self, start: str) -> str:
        m = re.search(rf"`({re.escape(start)}[^`]*)`", self.text)
        self.assertTrue(m, start)
        return m.group(1)

    def test_group_messages_match(self):
        two = "// group: Mod A\n{M1}\n40000000 00000000 00000010\n{M2}\n40000000 00000000 00000020\n[X]\n20000000\n"
        cheats, _ = ch.parse_text(two)
        err = next(c.error for c in cheats if c.name == "M2")
        self.assertIn(f'"{err}"', self.text)

        broken = "// group: Mod B\n{Master B}\nB0000000\n[B: hearts]\n04000000 00123450 00000050\n"
        code, out = self.cli(broken, "run", "--all")
        self.assertEqual(code, 1)
        want = self.template("Skipped: <cheat>").replace("<cheat>", "B: hearts").replace("<name>", "Mod B")
        self.assertIn(want, out)

        grouped = ("// group: Mod A\n{Master A}\n40000000 00000000 00000010\n[A]\n04000000 00001000 00000001\n"
                   "// group: Mod B\n{Master B}\n40000000 00000000 00000020\n[B]\n04000000 00002000 00000001\n")
        code, out = self.cli(grouped, "list")
        self.assertEqual(code, 0)
        self.assertIn(self.template(", in N groups").replace("N", "2").replace("M master", "2 master"), out)
        self.assertIn("group: Mod A", out)

    def test_tested_in_game_is_current(self):
        """docs-cheats-untested-claim: cheats.py ran in game on 2026-11-06 (overnight log)."""
        self.assertNotIn("Not yet tried against the real game", self.text)
        self.assertIn("Tested in game", self.text)


class CodeModsDocs(unittest.TestCase):
    def test_tested_in_game_is_current(self):
        """docs-code-mods-unverified-stale: what the overnight log shows ran in game isn't listed as unverified."""
        text = read("docs/code-mods.md")
        self.assertNotIn("has not been tried with the real game", text)
        todo = section(text, "What is verified").split("Not verified yet")[1]
        for ran_in_game in ("SAVAGEWIND_GAME_LIB", "hle_override", "guest_call", "dispatch_lookup", "over the socket"):
            self.assertNotIn(ran_in_game, todo)


class ReadmeAndNotice(unittest.TestCase):
    """clean-7, and the check_clean facts README states."""

    def test_layout_names_every_example(self):
        """Every example but the secret ones ([mod] secret: an easter egg; README ships in app\\, and only
        docs/easter-eggs.md, the spoilers doc, names them)."""
        row = next(r for r in table_rows(read("README.md")) if r[0] == "`native/`")
        tomls = sorted((REPO / "native" / "examples").glob("*/mod.toml"))
        secret = {p.parent.name for p in tomls if modman.read_manifest(str(p.parent))[0].secret}
        examples = [p.parent.name for p in tomls if p.parent.name not in secret]
        self.assertTrue(examples)
        self.assertIn("zelda_view", secret)
        for name in examples:
            self.assertIn(name, row[1])
        for name in secret:
            self.assertNotIn(name, row[1])
            self.assertIn(f"native/examples/{name}", read("docs/easter-eggs.md"))
        self.assertNotIn("an example mod", row[1])

    def test_notice_link_exists(self):
        if "we link to it" in flat(read("NOTICE.md")):
            self.assertTrue(any("https://github.com/zeldaret/botw" in read(rel) for rel in DOCS))

    def test_check_clean_exit_codes_and_links(self):
        readme = flat(read("README.md"))
        self.assertIn("exits with 0 when clean, 1 when it blocked a file, and 2 when the folder isn't one", readme)
        with tempfile.TemporaryDirectory() as d:
            clean, bad = Path(d, "clean"), Path(d, "bad")
            clean.mkdir()
            bad.mkdir()
            (clean / "notes.md").write_text("See https://example.invalid/made-up.nsp\n", encoding="utf-8")
            (bad / "made-up.sarc").write_bytes(b"made up")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                codes = [cc.main([str(clean)]), cc.main([str(bad)]), cc.main([str(Path(d, "missing"))])]
        self.assertEqual(codes, [0, 1, 2])
        # (check_clean doesn't look for links; README says so. If it starts to, README can say it does.)
        self.assertIn("can't spot links", readme)


class DefaultPaths(unittest.TestCase):
    """clean-6 / W4 docs side: no doc names one user's profile folder, and the default folder the docs give is the
    one sw_rebuild uses."""

    def test_no_user_profile_paths(self):
        for rel in DOCS:
            for m in re.finditer(r"[A-Za-z]:[\\/]+Users[\\/]+([^\\/\s'\"`]+)", read(rel)):
                self.assertIn(m.group(1), ("<you>", "..."), f"{rel}: {m.group(0)}")

    def test_rebuild_default_matches_sw_rebuild(self):
        text = read("docs/rebuild.md")
        self.assertIn(r"`%USERPROFILE%\Documents\SavageWind\Savage Wind`", text)
        self.assertIn("`%SWMOD_INSTALL%`", text)
        env = {k: v for k, v in os.environ.items() if k != sw.INSTALL_ENV}
        env["USERPROFILE"] = os.path.join("Z:" + os.sep, "Made-up profile")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(sw.default_install(),
                             os.path.join(env["USERPROFILE"], "Documents", "SavageWind", "Savage Wind"))
        with mock.patch.dict(os.environ, {sw.INSTALL_ENV: "Z:\\made-up"}):
            self.assertEqual(sw.default_install(), "Z:\\made-up")
        self.assertIn(r'"$env:USERPROFILE\Documents\SavageWind\Savage Wind\savagewind.exe"', read("docs/hooks.md"))


if __name__ == "__main__":
    unittest.main()
