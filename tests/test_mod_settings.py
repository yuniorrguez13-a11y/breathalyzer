"""Mod settings (docs/mod-settings.md): tools/mod_settings.py, and modhost's settings engine (native/modhost/settings.c)
against the fake game.

The Python part needs nothing else. The native part builds modhost, the fake game, the settings_demo example and the
self-test's settings_probe with the zig that ships with Savage Wind (at below-normal priority), then drives them with
build_native's fake-game harness; it is skipped when Savage Wind's zig or savagewind.dll.a isn't there. Every library
here is made up, in a temp folder.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import mod_settings as ms  # noqa: E402

CHECK_GAME = ms._check_game

DEMO = REPO / "native" / "examples" / "settings_demo"

EXAMPLE = """
[mod]
id = "giruka.better-sprint"
name = "Better Sprint"
version = "1.0.0"
loader_api = 3
[code]
windows = "bin/better_sprint.dll"
[data]
romfs = "romfs"
[[pages]]
key = "advanced"
label = "Advanced"
description = "Fine tuning."
[[settings]]
key = "numbers"
type = "toggle"
label = "Stamina numbers"
default = true
applies = "live"
[[settings]]
key = "mode"
type = "choice"
label = "Sprint mode"
default = "classic"
applies = "live"
choices = [
  { value = "classic", label = "Classic" },
  { value = "fast", label = "Fast", sets = { speed = 150 } },
  { value = "custom", label = "Custom" },
  { value = "2", label = "Two" },
]
[[settings]]
key = "speed"
type = "slider"
label = "Sprint speed"
min = 50
max = 200
step = 10
default = 100
unit = " percent"
applies = "live"
show_if = "mode == 'custom'"
[[settings]]
key = "drain"
type = "slider"
label = "Stamina use"
min = 0.5
max = 2.0
step = 0.1
default = 1.0
unit = "x"
applies = "live"
page = "advanced"
enable_if = "mode != 'classic' && speed >= 100"
[[settings]]
key = "sprint_key"
type = "key"
label = "Sprint key"
default = "F6"
applies = "live"
page = "advanced"
[[settings]]
key = "title"
type = "text"
label = "Wheel caption"
default = "Stamina"
max_length = 20
page = "advanced"
[[settings]]
key = "reset_record"
type = "button"
label = "Sprint record"
button = "Reset"
page = "advanced"
[[settings]]
key = "look"
type = "choice"
label = "Wheel look"
default = "green"
applies = "deploy"
choices = [{ value = "green", label = "Green" },
           { value = "gold", label = "Gold", romfs = "options/gold", dlc_romfs = "options/gold-dlc" }]
show_if = "numbers || on('giruka.hud-pack')"
[[settings]]
key = "hd"
type = "toggle"
label = "HD wheel"
applies = "deploy"
romfs = "options/hd"
"""

BAD = """
[mod]
id = "giruka.bad"
name = "Bad"
version = "1.0"
[[settings]]
key = "a"
type = "toggle"
label = "Use the power of the fast mode now"
default = true
show_if = "b"
applies = "deploy"
[[settings]]
key = "b"
type = "toggle"
label = "B"
default = false
show_if = "a && mode == 'blue'"
[[settings]]
key = "mode"
type = "choice"
label = "Mode"
default = "red"
choices = [{ value = "red", label = "Red" }, { value = "green", label = "Green" }]
enable_if = "mode > 3"
[[settings]]
key = "speed"
type = "slider"
label = "Speed"
min = 0
max = 1000
step = 1
default = 7.5
"""


def make_mod(root: Path, toml: str, files=("romfs/System/x.txt", "options/gold/UI/y.txt", "options/hd/Pack/z.txt",
                                           "options/gold-dlc/Map/w.txt")) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "mod.toml").write_text(toml, encoding="utf-8")
    for f in files:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("made up", encoding="utf-8")
    return root


def schema_of(toml: str, root: Path | None = None):
    sch, errs, warns = ms.check(tomllib.loads(toml), str(root) if root else None)
    return sch, errs, warns


class FakeLibrary(SimpleNamespace):
    """What mod_settings needs of modman's Library: root, mods, mods_dir, enabled()."""

    @property
    def mods_dir(self) -> str:
        return os.path.join(self.root, "mods")

    def enabled(self):
        return [m for m in self.mods if m.get("enabled")]


class Schema(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_mod(Path(self.tmp.name) / "m", EXAMPLE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_example_is_accepted(self):
        sch, errs, warns = schema_of(EXAMPLE, self.root)
        self.assertEqual(errs, [])
        self.assertEqual([s.key for s in sch.settings],
                         ["numbers", "mode", "speed", "drain", "sprint_key", "title", "reset_record", "look", "hd"])
        by = sch.by_key
        self.assertEqual(by["speed"].steps, 16)
        self.assertEqual(by["drain"].steps, 16)
        self.assertEqual(by["drain"].decimals, 1)
        self.assertEqual(by["drain"].default, 1.0)
        self.assertEqual(by["speed"].default, 100)
        self.assertEqual(by["reset_record"].applies, "live")
        self.assertEqual(by["title"].applies, "restart")
        self.assertEqual(by["hd"].default, False)
        self.assertEqual(by["mode"].choice("fast").sets, {"speed": 150})
        self.assertEqual(by["look"].choice("gold").romfs, "options/gold")

    def test_bad_lists_every_problem(self):
        sch, errs, warns = schema_of(BAD)
        self.assertIsNone(sch)
        text = "\n".join(errs)
        for want in ("label 'Use the power of the fast mode now' is 34 characters",
                     "a: applies = 'deploy' needs romfs",
                     "b: applies = 'restart' needs a DLL",
                     "'blue' isn't a value of 'mode'",
                     "'mode' can't depend on itself",
                     "1001 values; at most 201",
                     "default 7.5 isn't one of the values"):
            self.assertIn(want, text)

    def test_font_symbols_only_warn(self):
        toml = EXAMPLE.replace('label = "Stamina numbers"', 'label = "Numbers {100%}"')
        sch, errs, warns = schema_of(toml, self.root)
        self.assertEqual(errs, [])
        self.assertTrue(any("{" in w and "font" in w for w in warns))

    def test_live_needs_api_3_and_a_dll(self):
        _, errs, _ = schema_of(EXAMPLE.replace("loader_api = 3", "loader_api = 2"), self.root)
        self.assertTrue(any("loader_api = 3" in e for e in errs))
        _, errs, _ = schema_of(EXAMPLE.replace('windows = "bin/better_sprint.dll"', ""), self.root)
        self.assertTrue(any("needs a DLL" in e for e in errs))
        self.assertTrue(any("a button needs a DLL" in e for e in errs))

    def test_deploy_rules(self):
        for old, new, want in (
                ('applies = "deploy"\nromfs = "options/hd"', 'applies = "deploy"', "needs romfs"),
                ('romfs = "options/hd"', 'romfs = "romfs/hd"', "inside (or holds) the mod's own [data] folder"),
                ('romfs = "options/hd"', 'romfs = "../hd"', "must be a folder inside the mod"),
                ('romfs = "options/hd"', 'romfs = "options/none"', "no such folder"),
                ('romfs = "options/gold"', 'romfs = "options/romfs"', "can't have a romfs or content folder"),
                ('[data]\nromfs = "romfs"', "", "needs its own RomFS folder too")):
            with self.subTest(want=want):
                _, errs, _ = schema_of(EXAMPLE.replace(old, new, 1), self.root)
                self.assertTrue(any(want in e for e in errs), errs)
        make_mod(self.root, EXAMPLE, ("options/hd/romfs/Pack/z.txt",))
        _, errs, _ = schema_of(EXAMPLE, self.root)
        self.assertTrue(any("has a romfs folder in it" in e for e in errs), errs)

    def test_key_text_slider_cant_pick_files(self):
        toml = EXAMPLE.replace('default = "F6"\napplies = "live"', 'default = "F6"\napplies = "deploy"')
        _, errs, _ = schema_of(toml, self.root)
        self.assertTrue(any("a key can't pick files" in e for e in errs))

    def test_sets_are_checked(self):
        _, errs, _ = schema_of(EXAMPLE.replace("sets = { speed = 150 }", "sets = { speed = 155 }"), self.root)
        self.assertTrue(any("sets speed = 155" in e for e in errs), errs)
        _, errs, _ = schema_of(EXAMPLE.replace("sets = { speed = 150 }", "sets = { title = 'x' }"), self.root)
        self.assertTrue(any("isn't another toggle, slider or choice" in e for e in errs), errs)

    def test_show_if_loop(self):
        toml = EXAMPLE.replace('show_if = "mode == \'custom\'"', 'show_if = "numbers"').replace(
            'label = "Stamina numbers"\ndefault = true', 'label = "Stamina numbers"\ndefault = true\nshow_if = "speed > 1"')
        _, errs, _ = schema_of(toml, self.root)
        self.assertTrue(any("round in a circle" in e for e in errs), errs)

    def test_rows_per_page(self):
        many = "".join(f'[[settings]]\nkey = "r{i}"\ntype = "toggle"\nlabel = "R{i}"\napplies = "live"\n'
                       for i in range(31))
        _, errs, _ = schema_of(EXAMPLE + many, self.root)
        self.assertTrue(any("rows; at most 30" in e for e in errs), errs)

    def test_no_settings(self):
        self.assertEqual(ms.check({"mod": {"id": "x"}}), (None, [], []))

    def test_demo_example_is_accepted(self):
        with open(DEMO / "mod.toml", "rb") as f:
            sch, errs, warns = ms.check(tomllib.load(f), str(DEMO))
        self.assertEqual(errs, [])
        self.assertEqual({s.type for s in sch.settings}, set(ms.TYPES))
        self.assertEqual({s.applies for s in sch.settings}, set(ms.APPLIES))
        self.assertEqual(sum(1 for s in sch.settings if s.page == "many"), 30)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ms.main(["check", str(DEMO)]), 0)
        self.assertIn("set\tbotw-mod-loader.settings-demo\tnumbers\ttoggle\tlive", out.getvalue())


class Conditions(unittest.TestCase):
    def setUp(self):
        self.sch, errs, _ = schema_of(EXAMPLE)
        self.assertIsNotNone(self.sch, errs)
        self.by = self.sch.by_key

    def rpn(self, src):
        return ms.compile_cond(src, self.by)[0]

    def test_tokens(self):
        self.assertEqual(self.rpn("mode == 'custom'"), ["k:mode", "s:custom", "=="])
        self.assertEqual(self.rpn("!numbers || speed >= 100.5"), ["k:numbers", "!", "k:speed", "n:100.5", ">=", "||"])
        self.assertEqual(self.rpn("(numbers && on('giruka.hud')) || false"),
                         ["k:numbers", "m:giruka.hud", "&&", "b:0", "||"])
        self.assertEqual(self.rpn("sprint_key == 'F6'"), ["k:sprint_key", "s:F6", "=="])

    def test_refused(self):
        for src, want in (("mode == 3", "compares a text with a number"), ("numbers > 1", "compares"),
                          ("mode < 'fast'", "needs numbers"), ("mode == 'slow'", "isn't a value of 'mode'"),
                          ("reset_record", "is a button"), ("nope", "no setting 'nope'"), ("mode ==", "ends too early"),
                          ("title == 'a b'", "has a space"), ("on(mode)", "needs a mod id"),
                          ("sprint_key == 'Esc'", "isn't a key name"), ("numbers numbers", "is left over"),
                          (" || ".join(["numbers"] * 20), "too long")):
            with self.subTest(src=src), self.assertRaises(ms.CondError) as cm:
                ms.compile_cond(src, self.by)
            self.assertIn(want, str(cm.exception))

    def test_eval_by_declared_type(self):
        vals = {"numbers": True, "mode": "2", "speed": 100, "drain": 0.5 + 5 * 0.1, "sprint_key": "F6", "title": ""}
        ev = lambda src: ms.eval_rpn(self.rpn(src), vals, self.by, {"giruka.hud": True})  # noqa: E731
        self.assertTrue(ev("mode == '2'"))       # (a choice value that looks like a number stays a text)
        self.assertTrue(ev("drain == 1"))        # (0.5 + 5 * 0.1 is 1.0000000000000002)
        self.assertFalse(ev("drain > 1"))
        self.assertTrue(ev("speed >= 100 && on('giruka.hud')"))
        self.assertFalse(ev("on('giruka.other')"))
        self.assertFalse(ev("title"))
        self.assertTrue(ev("!title"))
        self.assertTrue(ms.eval_rpn(["k:numbers", "&&"], vals, self.by, {}))   # (broken: counts as true)
        self.assertTrue(ms.eval_rpn(["k:mode", "n:1", "=="], vals, self.by, {}))

    def test_lock_words(self):
        self.assertEqual(ms.locked_line(self.sch, self.by["drain"]),
                         "Locked: needs Sprint mode not Classic and Sprint speed 100 percent or more.")
        sch, _, _ = schema_of(EXAMPLE.replace('enable_if = "mode != \'classic\' && speed >= 100"',
                                              "enable_if = \"!numbers || mode == 'fast' || on('giruka.hud')\""))
        names = {"giruka.hud": "HUD Pack"}
        self.assertEqual(ms.locked_line(sch, sch.by_key["drain"], names),
                         "Locked: needs Stamina numbers OFF or Sprint mode Fast or HUD Pack on.")
        self.assertEqual(ms.locked_line(sch, sch.by_key["speed"]), "")


class Values(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = self.tmp.name
        self.sch, _, _ = schema_of(EXAMPLE)
        self.path = Path(ms.values_path(self.lib, self.sch.mod_id))
        p = mock.patch.object(ms, "_check_game", lambda refuse: None)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_without_a_file(self):
        v = ms.load_values(self.lib, self.sch)
        self.assertEqual(v, {"numbers": True, "mode": "classic", "speed": 100, "drain": 1.0, "sprint_key": "F6",
                             "title": "Stamina", "look": "green", "hd": False})

    def test_only_changes_are_saved_and_unknown_keys_stay(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"format": 1, "mod": self.sch.mod_id, "values": {"future": [1, 2]}}))
        v = ms.load_values(self.lib, self.sch)
        v.update(speed=120, title="Go", numbers=True)
        ms.save_values(self.lib, self.sch, v)
        data = json.loads(self.path.read_text())
        self.assertEqual(data, {"format": 1, "mod": self.sch.mod_id,
                                "values": {"future": [1, 2], "speed": 120, "title": "Go"}})
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])  # (no temp file left)

    def test_bad_saved_values_are_dropped(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"values": {"speed": 999, "mode": "fast", "sprint_key": "Esc",
                                                    "drain": 1.27, "numbers": "off"}}))
        notes = []
        v = ms.load_values(self.lib, self.sch, notes)
        self.assertEqual((v["speed"], v["mode"], v["sprint_key"], v["numbers"]), (100, "fast", "F6", False))
        self.assertEqual(v["drain"], 1.3)  # (rounded to a step)
        self.assertEqual(len(notes), 2)
        self.path.write_text("{damaged")
        self.assertEqual(ms.load_values(self.lib, self.sch)["mode"], "classic")

    def test_set_value_like_the_mod_menu(self):
        with self.assertRaises(ms.SettingsError) as cm:
            ms.set_value(self.lib, self.sch, "drain", "1.5")
        self.assertEqual(str(cm.exception),
                         "Locked: needs Sprint mode not Classic and Sprint speed 100 percent or more.")
        self.assertEqual(ms.set_value(self.lib, self.sch, "mode", "Fast"), ["mode", "speed"])
        self.assertEqual(ms.set_value(self.lib, self.sch, "drain", "1.5"), ["drain"])
        self.assertEqual(ms.set_value(self.lib, self.sch, "sprint_key", "none"), ["sprint_key"])
        v = ms.load_values(self.lib, self.sch)
        self.assertEqual((v["mode"], v["speed"], v["drain"], v["sprint_key"]), ("fast", 150, 1.5, ""))
        for key, bad in (("speed", "abc"), ("mode", "slow"), ("title", "x" * 21), ("reset_record", "1")):
            with self.subTest(key=key), self.assertRaises(ms.SettingsError):
                ms.set_value(self.lib, self.sch, key, bad)
        ms.reset_values(self.lib, self.sch)
        self.assertEqual(json.loads(self.path.read_text())["values"], {})

    def test_refused_while_the_game_runs(self):
        import sw_rebuild as swr
        with mock.patch.object(ms, "_check_game", CHECK_GAME), \
                mock.patch.object(swr, "game_running", return_value=True), self.assertRaises(ms.SettingsError) as cm:
            ms.set_value(self.lib, self.sch, "mode", "fast")
        self.assertIn("Savage Wind is running", str(cm.exception))
        self.assertFalse(self.path.exists())

    def test_value_text(self):
        by = self.sch.by_key
        self.assertEqual(ms.value_text(by["speed"], 120), "120 percent")
        self.assertEqual(ms.value_text(by["drain"], 1.5), "1.5x")
        self.assertEqual(ms.value_text(by["mode"], "fast"), "Fast")
        self.assertEqual(ms.value_text(by["sprint_key"], ""), "None")
        self.assertEqual(ms.value_text(by["numbers"], False), "OFF")

    def test_rows_for_a_settings_screen(self):
        rows = {r.key: r for r in ms.rows(self.sch)}           # (defaults)
        self.assertEqual(list(rows), [s.key for s in self.sch.settings])
        d = rows["drain"]
        self.assertEqual((d.page, d.enabled, d.value_text, d.min, d.max, d.step), ("advanced", False, "1.0x", 0.5, 2.0,
                                                                                   0.1))
        self.assertEqual(d.description, "Locked: needs Sprint mode not Classic and Sprint speed 100 percent or more.")
        self.assertEqual(d.lock_line, d.description)
        self.assertFalse(rows["speed"].shown)                   # (show_if mode == 'custom')
        self.assertEqual((rows["mode"].choices[1], rows["mode"].value_text), (("fast", "Fast"), "Classic"))
        self.assertEqual((rows["reset_record"].value, rows["reset_record"].value_text), (None, "Reset"))
        self.assertEqual((rows["title"].max_length, rows["sprint_key"].value_text), (20, "F6"))
        self.assertFalse(rows["look"].waiting)                  # (no deploy known: nothing waits)
        vals = ms.apply_change(self.sch, ms.load_values(self.lib, self.sch), "mode", "custom")[0]
        vals = ms.apply_change(self.sch, vals, "look", "gold")[0]
        rows = {r.key: r for r in ms.rows(self.sch, vals, deployed={"look": "green", "hd": False})}
        self.assertTrue(rows["speed"].shown and rows["drain"].enabled)
        self.assertEqual((rows["drain"].description, rows["drain"].lock_line), ("", ""))
        self.assertEqual((rows["look"].waiting, rows["hd"].waiting), (True, False))
        self.assertEqual(ms.WAITING_LINE, "Changes at your next start.")


class Deploy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        make_mod(root / "mods" / "better-sprint", EXAMPLE)
        make_mod(root / "mods" / "plain", '[mod]\nid = "giruka.plain"\nname = "Plain"\nversion = "1.0"\n', ())
        self.lib = FakeLibrary(root=str(root), mods=[
            {"id": "plain", "name": "Plain", "enabled": True},
            {"id": "better-sprint", "name": "Better Sprint", "enabled": True},
            {"id": "hud", "name": "HUD Pack", "enabled": False}])
        self.sch = ms.load_schema(str(root / "mods" / "better-sprint"))
        p = mock.patch.object(ms, "_check_game", lambda refuse: None)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_option_folders_follow_the_values(self):
        self.assertEqual(ms.option_romfs(self.lib, self.lib.enabled()), [])
        ms.set_value(self.lib.root, self.sch, "look", "gold")
        ms.set_value(self.lib.root, self.sch, "hd", "on")
        got = ms.option_romfs(self.lib, self.lib.enabled())
        opt = Path(self.lib.root) / "mods" / "better-sprint" / "options"
        self.assertEqual([(m["id"], Path(f), *rest) for m, f, *rest in got],
                         [("better-sprint", opt / "gold"), ("better-sprint", opt / "gold-dlc", True),
                          ("better-sprint", opt / "hd")])
        self.lib.mods[1]["enabled"] = False
        self.assertEqual(ms.option_romfs(self.lib, self.lib.enabled()), [])

    def test_deploy_values_and_pending(self):
        on = self.lib.enabled()
        self.assertEqual(ms.deploy_values(self.lib, on), {"giruka.better-sprint": {"look": "green", "hd": False}})
        launch = {"settings": ms.deploy_values(self.lib, on)}
        self.assertFalse(ms.deploy_pending(self.lib, launch))
        ms.set_value(self.lib.root, self.sch, "mode", "fast")  # (live: no deploy needed)
        self.assertFalse(ms.deploy_pending(self.lib, launch))
        ms.set_value(self.lib.root, self.sch, "look", "gold")
        self.assertTrue(ms.deploy_pending(self.lib, launch))
        self.assertTrue(ms.deploy_pending(self.lib, None))

    def test_option_folders_into_a_plan(self):
        import romfs_mods as rm
        mod = str(Path(self.lib.root) / "mods" / "better-sprint")
        p = rm.plan([mod])
        self.assertEqual(ms.add_option_layers(p, self.lib, self.lib.enabled()), [])
        self.assertEqual(sorted(p.winners), ["System/x.txt"])
        ms.set_value(self.lib.root, self.sch, "look", "gold")
        added = ms.add_option_layers(p, self.lib, self.lib.enabled())
        self.assertEqual(len(added), 2)
        self.assertEqual(p.winner("UI/y.txt").mod, "better-sprint option gold")
        self.assertEqual(p.winner("aoc/0010/Map/w.txt").mod, "better-sprint option gold-dlc")
        self.assertEqual(p.winner("System/x.txt").mod, "better-sprint")
        self.assertIsNone(p.winner("Pack/z.txt"))                   # (hd is off)

    def test_deploy_values_reads_only_the_enabled_mods(self):
        # (modman hashes these on every run and status: a mod that is off isn't read, not even its mod.toml)
        make_mod(Path(self.lib.root) / "mods" / "hud", EXAMPLE.replace("giruka.better-sprint", "giruka.hud-pack"))
        read = []
        real = ms.load_schema
        with mock.patch.object(ms, "load_schema", lambda d, notes=None: read.append(os.path.basename(d)) or real(d)):
            got = ms.deploy_values(self.lib, self.lib.enabled())
        self.assertEqual(sorted(read), ["better-sprint", "plain"])
        self.assertEqual(list(got), ["giruka.better-sprint"])

    def test_settings_txt(self):
        ms.set_value(self.lib.root, self.sch, "look", "gold")
        text = ms.settings_txt(self.lib, self.lib.enabled())
        lines = text.splitlines()
        self.assertEqual(lines[0], "swsettings\t1")
        self.assertEqual(lines[1], f"library\t{os.path.abspath(self.lib.root)}")
        self.assertIn("mod\tgiruka.better-sprint\tbetter-sprint\ton\tBetter Sprint", lines)
        self.assertIn("dll\tgiruka.better-sprint\tbetter_sprint.dll", lines)
        sets = [ln.split("\t") for ln in lines if ln.startswith("set\t")]
        self.assertTrue(all(len(f) == 15 for f in sets))
        drain = next(f for f in sets if f[2] == "drain")
        self.assertEqual(drain[3:12], ["slider", "live", "advanced", "1", "0.5", "2", "0.1", "1", "0"])
        self.assertIn("sets\tgiruka.better-sprint\tmode\tfast\tspeed\t150", lines)
        self.assertIn("if\tgiruka.better-sprint\tspeed\tshow\tk:mode s:custom ==\t-", lines)
        self.assertIn("deployed\tgiruka.better-sprint\tlook\tgold", lines)
        self.assertIn("on\tgiruka.hud-pack\t0", lines)
        out = ms.write_settings_txt(self.lib, self.lib.enabled(), os.path.join(self.lib.root, "deploy"))
        self.assertEqual(Path(out).read_text(encoding="utf-8"), text)
        added = []

        class Dir:
            def add_file(self, rel):
                added.append(rel)
                p = os.path.join(self_root, *rel.split("/"))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                return p
        self_root = os.path.join(self.lib.root, "deploy2")
        ms.write_settings_txt(self.lib, self.lib.enabled(), Dir())
        self.assertEqual(added, ["native/settings.txt"])

    def test_no_settings_no_file(self):
        self.lib.mods = [self.lib.mods[0]]
        self.assertIsNone(ms.write_settings_txt(self.lib, self.lib.enabled(), os.path.join(self.lib.root, "d")))
        self.assertFalse(os.path.exists(os.path.join(self.lib.root, "d")))


class ProfilesKeepSettings(unittest.TestCase):
    """Mod sets (modman's profiles, docs/profiles.md) keep each set's setting values: a switch puts them back, and the
    next deploy follows them (option folders, the values in the deploy hash, settings.txt)."""

    MOD = "giruka.better-sprint"   # (a mod added with a mod.toml has its [mod] id as its library id)

    def setUp(self):
        import modman as mm
        if not all(hasattr(mm, f) for f in ("new_profile", "use_profile", "Library")):
            self.skipTest("modman has no mod sets")
        self.mm = mm
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "lib"
        make_mod(self.root / "mods" / self.MOD, EXAMPLE)
        mm.Library(str(self.root), None, [{"id": self.MOD, "name": "Better Sprint", "enabled": True,
                                           "kinds": ["romfs", "native"]}]).save()
        self.sch = ms.load_schema(str(self.root / "mods" / self.MOD))
        for target, attr, value in ((mm, "game_running", lambda *a, **k: False),
                                    (ms, "_check_game", lambda refuse: None)):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def lib(self):
        return self.mm.Library.load(str(self.root))

    def test_switching_sets_switches_the_values(self):
        mm, root = self.mm, str(self.root)
        ms.set_value(root, self.sch, "look", "gold")                  # (Default: the gold wheel)
        lib = self.lib()
        launch = {"settings": ms.deploy_values(lib, lib.enabled())}   # (as the last deploy recorded it)
        mm.new_profile(lib, "Plain look")                             # (starts as a copy, and is in use)
        ms.set_value(root, self.sch, "look", "green")
        ms.set_value(root, self.sch, "speed", "120")
        lib = self.lib()
        self.assertTrue(ms.deploy_pending(lib, launch))
        self.assertEqual(ms.option_romfs(lib, lib.enabled()), [])
        mm.use_profile(lib, "Default")
        lib = self.lib()
        vals = ms.load_values(root, self.sch)
        self.assertEqual((lib.profile, vals["look"], vals["speed"]), ("Default", "gold", 100))
        self.assertFalse(ms.deploy_pending(lib, launch))
        self.assertEqual([Path(f).name for _, f, *_ in ms.option_romfs(lib, lib.enabled())], ["gold", "gold-dlc"])
        self.assertIn(f"deployed\t{self.MOD}\tlook\tgold", ms.settings_txt(lib, lib.enabled()).splitlines())
        mm.use_profile(lib, "Plain look")
        vals = ms.load_values(root, self.sch)
        self.assertEqual((self.lib().profile, vals["look"], vals["speed"]), ("Plain look", "green", 120))


class KeysMatchC(unittest.TestCase):
    def test_c_has_the_same_key_table(self):
        c = (REPO / "native" / "modhost" / "settings.c").read_text(encoding="utf-8")
        for name, vk in ms.KEYS.items():
            if len(name) == 1 or name[0] == "F" and name[1:].isdigit() or name.startswith("Num"):
                continue  # (computed in st_key_vk)
            self.assertIn(f'{{"{name}", 0x{vk:02X}}}', c)


# ---------------------------------------------------------------- modhost against the fake game

ST = REPO / "native" / "modhost" / "selftest"
OUT = REPO / "build" / "native" / "settings-selftest"
ENV = {"FAKE_MAX_CMDS": "32"}  # (the fake Savage Wind's command table is tiny by default)
PROBE_ID = "giruka.probe"
# The probe mod: base settings of every value type, and one hidden-or-shown toggle per condition (Python and C must
# agree on each, for every assignment below).
CONDITIONS = [
    "t", "!t", "t == true", "t != false", "n > 3", "n >= 3.5", "n < 1", "n <= 0.5", "n == 2.5", "n != 2.5",
    "c == 'a'", "c == '2'", "c != 'x.y'", "c == 'b' || t", "c == 'b' && t", "!(c == 'a') && n > 1",
    "k == 'F6'", "k != 'A'", "k", "!k", "tx", "tx == 'hi'", "tx != 'hi' && t", "on('other')",
    "on('off') || n >= 10", "(t || c == 'a') && (n < 5 || k == 'Num3')", "true", "false", "1 < 2", "n >= n",
    "!!t", "t && !t || c == '2'", "n > 2.5 && n < 5", "-1 < n", "c == c && k == k",
]
ASSIGN = [(0, 0, "a", "", ""), (1, 2.5, "b", "F6", "hi"), (1, 10, "2", "Num3", "x"), (0, 3.5, "x.y", "A", "hi"),
          (1, 0.5, "a", "F6", ""), (0, 5, "2", "", "hello"), (1, 3, "b", "A", "hi"), (0, 1, "x.y", "Num3", "z")]


def probe_toml() -> str:
    out = [f'[mod]\nid = "{PROBE_ID}"\nname = "Probe"\nversion = "1.0"\nloader_api = 3\n'
           '[code]\nwindows = "settings_probe.dll"\n',
           '[[settings]]\nkey = "t"\ntype = "toggle"\nlabel = "T"\napplies = "live"\n',
           '[[settings]]\nkey = "n"\ntype = "slider"\nlabel = "N"\nmin = 0\nmax = 10\nstep = 0.5\napplies = "live"\n',
           '[[settings]]\nkey = "c"\ntype = "choice"\nlabel = "C"\napplies = "live"\nchoices = [{ value = "a", '
           'label = "A" }, { value = "b", label = "B" }, { value = "2", label = "Two" }, { value = "x.y", '
           'label = "XY" }]\n',
           '[[settings]]\nkey = "k"\ntype = "key"\nlabel = "K"\napplies = "live"\n',
           '[[settings]]\nkey = "tx"\ntype = "text"\nlabel = "TX"\napplies = "live"\n',
           '[[settings]]\nkey = "r"\ntype = "toggle"\nlabel = "R"\napplies = "restart"\n',
           '[[settings]]\nkey = "locked"\ntype = "toggle"\nlabel = "Locked row"\napplies = "live"\n'
           'enable_if = "t && c == \'b\'"\n',
           '[[pages]]\nkey = "conds"\nlabel = "Conditions"\n',
           '[[pages]]\nkey = "conds2"\nlabel = "More conditions"\n']
    for i, src in enumerate(CONDITIONS):  # (20 a page: a page holds 30 rows)
        out.append(f'[[settings]]\nkey = "q{i:02d}"\ntype = "toggle"\nlabel = "Q{i}"\napplies = "live"\n'
                   f'page = "{"conds" if i < 20 else "conds2"}"\nshow_if = "{src}"\n')
    return "".join(out)


def low_priority(cmd, env):
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", env=env,
                       creationflags=0x00004000 if sys.platform == "win32" else 0)
    if r.returncode:
        import build_native as bn
        raise bn.BuildError(f"{Path(cmd[0]).name} {cmd[1]} failed:\n{(r.stderr or r.stdout)[-3000:]}")


@unittest.skipUnless(sys.platform == "win32", "Windows only (the fakes are Windows DLLs)")
class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import build_native as bn
        cls.bn = bn
        try:
            tc = bn.find_toolchain(bn.default_install())
        except bn.BuildError as e:
            raise unittest.SkipTest(str(e))
        t = [bn.Target("st_modhost", (bn.HOST_SRC / "modhost.c",), runtime=True, out_name="modhost.dll",
                       export="swgame_tables"),
             bn.Target("st_runtime", (ST / "fake_runtime.c",), runtime=False, out_name=bn.RUNTIME_DLL,
                       subdir="selftest"),
             bn.Target("st_game", (ST / "fake_game.c",), runtime=False, out_name="fake_swgame.dll", subdir="selftest",
                       export="swgame_tables"),
             bn.Target("st_demo", (DEMO / "settings_demo.c",), runtime=False, out_name="settings_demo.dll",
                       subdir="dlls", export="swmod_init"),
             bn.Target("st_probe", (ST / "settings_probe.c",), runtime=False, out_name="settings_probe.dll",
                       subdir="dlls", export="swmod_init")]
        built = bn.build(tc, OUT, t, runner=low_priority)
        bn.check_exports(zip(built, t))
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.lib = FakeLibrary(root=str(root / "lib"), mods=[
            {"id": "settings-demo", "name": "Settings demo", "enabled": True},
            {"id": "probe-lib", "name": "Probe", "enabled": True},
            {"id": "other", "name": "Other", "enabled": True},
            {"id": "off", "name": "Off", "enabled": False}])
        shutil.copytree(DEMO, root / "lib" / "mods" / "settings-demo")
        make_mod(root / "lib" / "mods" / "probe-lib", probe_toml(), ())
        (root / "lib" / "mods" / "other").mkdir()
        (root / "lib" / "mods" / "off").mkdir()
        cls.demo = ms.load_schema(str(root / "lib" / "mods" / "settings-demo"))
        cls.probe = ms.load_schema(str(root / "lib" / "mods" / "probe-lib"))
        assert cls.demo and cls.probe
        with mock.patch.object(ms, "_check_game", lambda refuse: None):
            ms.set_value(cls.lib.root, cls.probe, "r", "on")   # (restart: the game starts with it on)
            run = root / "run" / "native"
            ms.write_settings_txt(cls.lib, cls.lib.enabled(), str(root / "run"))
            ms.set_value(cls.lib.root, cls.demo, "note_file", "on")  # (deploy: after the deploy, so it waits)
        vp = Path(ms.values_path(cls.lib.root, PROBE_ID))
        data = json.loads(vp.read_text())
        data["values"]["zzz_future"] = {"kept": True}
        vp.write_text(json.dumps(data))
        (run / "mods").mkdir(parents=True)
        for n in ("settings_demo.dll", "settings_probe.dll"):
            shutil.copy2(OUT / "dlls" / n, run / "mods" / n)
        steps = [("st0", "modsettings"), ("demo0", "settings_demo"), ("has", "stprobe has"),
                 ("info", "stprobe info n 128"), ("info_small", "stprobe info n 12"), ("w0", "stprobe watched"),
                 ("r_get", "stprobe get r"), ("r_set", "modset probe-lib r 0"), ("r_after", "stprobe get r"),
                 ("r_ui", "stprobe ui r 0"),
                 ("t_on", "modset probe-lib t 1"), ("w1", "stprobe watched"), ("own", "stprobe set t 0"),
                 ("w2", "stprobe watched"), ("own_bad", "stprobe set c nope"), ("own_none", "stprobe set nope 1"),
                 ("own_text", "stprobe set tx hello"), ("tx_get", "stprobe get tx"),
                 ("locked", "modset probe-lib locked 1"), ("locked_ui", "stprobe ui locked 1"),
                 ("late", "stprobe late"), ("press", "stprobe press t"),
                 ("demo_drain_locked", "modset settings-demo drain 1.5"), ("demo_fast", "modset settings-demo mode fast"),
                 ("demo_drain", "modset settings-demo drain 1.5"), ("demo_hello", "modset settings-demo say_hello"),
                 ("demo_key", "modset settings-demo hotkey"), ("demo_text", "modset settings-demo greeting Hi"),
                 ("demo1", "settings_demo"), ("demo_list", "modsettings settings-demo"),
                 ("threads", "stprobe threads")]
        cls.agree = []
        for i, (tv, nv, cv, kv, txv) in enumerate(ASSIGN):
            steps += [(f"a{i}t", f"modset probe-lib t {tv}"), (f"a{i}n", f"modset probe-lib n {nv}"),
                      (f"a{i}c", f"modset probe-lib c {cv}"), (f"a{i}k", f"modset probe-lib k {kv}".rstrip()),
                      (f"a{i}x", f'stprobe set tx "{txv}"'), (f"a{i}", "modsettings probe-lib")]
            cls.agree.append((f"a{i}", {"t": bool(tv), "n": nv, "c": cv, "k": kv, "tx": txv}))
        steps += [("st1", "modsettings"), ("sleep", "!sleep 400"), ("st2", "modsettings")]
        result, cls.log = bn._run_fake_game(OUT, OUT / "selftest" / "fake_swgame.dll", run / "mods",
                                            [ln for _, ln in steps], ENV)
        cls.s = dict(zip((k for k, _ in steps), result.get("steps", [])))
        # a mod loaded while the game runs: its watcher goes at modunload
        hot = root / "hot" / "native"
        (hot / "mods").mkdir(parents=True)
        shutil.copy2(run.parent / "native" / "settings.txt", hot / "settings.txt")
        shutil.copy2(OUT / "dlls" / "settings_probe.dll", root / "hot" / "settings_probe-1.dll")
        path = bn.control_word(root / "hot" / "settings_probe-1.dll")
        hsteps = [("load", f"modload {path}"), ("set", "modset probe-lib t 1"), ("w", "stprobe watched"),
                  ("unload", "modunload settings_probe"), ("set_after", "modset probe-lib t 0"),
                  ("list", "modsettings")]
        hres, cls.hot_log = bn._run_fake_game(OUT, OUT / "selftest" / "fake_swgame.dll", hot / "mods",
                                              [ln for _, ln in hsteps], {**ENV, "SWMOD_UNLOAD_WAIT_MS": "300"})
        cls.h = dict(zip((k for k, _ in hsteps), hres.get("steps", [])))
        # no settings.txt: every getter gives its default
        bare = root / "bare" / "native" / "mods"
        bare.mkdir(parents=True)
        shutil.copy2(OUT / "dlls" / "settings_demo.dll", bare / "settings_demo.dll")
        bres, cls.bare_log = bn._run_fake_game(OUT, OUT / "selftest" / "fake_swgame.dll", bare,
                                               ["settings_demo", "modsettings"], ENV)
        cls.b = bres.get("steps", [])
        # a change made just before the game closes: the writer still waits (10 s here), the exit list saves it
        late_lib, late = root / "late-lib", root / "late" / "native"
        (late / "mods").mkdir(parents=True)
        late_lib.mkdir()
        shutil.copy2(run / "settings.txt", late / "settings.txt")
        shutil.copy2(OUT / "dlls" / "settings_probe.dll", late / "mods" / "settings_probe.dll")
        lres, cls.late_log = bn._run_fake_game(OUT, OUT / "selftest" / "fake_swgame.dll", late / "mods",
                                               ["modset probe-lib t 1", "modsettings"],
                                               {**ENV, "SWMOD_SETTINGS_WRITE_MS": "10000", "SWMOD_LIBRARY": str(late_lib)})
        cls.late = lres.get("steps", [])
        try:
            cls.late_values = json.loads((late_lib / "settings" / f"{PROBE_ID}.json").read_text())
        except (OSError, ValueError):
            cls.late_values = None

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def d(self, k, where=None):
        return (where or self.s).get(k, {}).get("data", {})

    def ok(self, k, where=None):
        return (where or self.s).get(k, {}).get("ok") is True

    def err(self, k):
        return str(self.s.get(k, {}).get("error", ""))

    def test_loaded_and_no_writer_before_a_change(self):
        st0 = self.d("st0")
        self.assertEqual((st0.get("mods"), st0.get("writer"), st0.get("writes")), (2, False, 0), st0)
        self.assertIn("[modhost] settings: ", self.log)
        h = self.d("has")
        self.assertEqual(h, {"has_settings": True, "older_has": False, "api3": True, "ui_has_reset": True,
                             "small_has_reset": False, "mods": 2, "lib_id": "probe-lib", "mod_on": True,
                             "pages": 2})

    def test_info_respects_the_callers_size(self):
        i = self.d("info")
        self.assertEqual((i.get("ok"), i.get("type"), i.get("values"), i.get("unit"), i.get("min"), i.get("step"),
                          i.get("default_index"), i.get("page")), (True, 1, 21, "", 0, 0.5, 0, ""))
        s = self.d("info_small")
        self.assertEqual((s.get("ok"), s.get("size"), s.get("type"), s.get("untouched")), (True, 12, 1, True))

    def test_demo_reads_its_settings(self):
        d0 = self.d("demo0")
        self.assertEqual({k: d0.get(k) for k in ("settings", "mod_id", "numbers", "mode", "speed", "drain", "hotkey",
                                                 "hotkey_vk", "greeting", "note_file", "presses")},
                         {"settings": True, "mod_id": "botw-mod-loader.settings-demo", "numbers": True,
                          "mode": "classic", "speed": 100, "drain": 1.0, "hotkey": "F6", "hotkey_vk": 0x75,
                          "greeting": "Hello", "note_file": False, "presses": 0})
        self.assertIn("Locked: needs Sprint mode not Classic.", self.err("demo_drain_locked"))
        self.assertTrue(self.ok("demo_fast") and self.ok("demo_drain"))
        d1 = self.d("demo1")
        self.assertEqual((d1.get("mode"), d1.get("speed"), d1.get("drain"), d1.get("hotkey"), d1.get("presses")),
                         ("fast", 150, 1.5, "", 1))
        self.assertEqual(d1.get("greeting"), "Hello")   # (a text is changed on the PC, not with modset)
        self.assertIn("a text is changed in Mod Manager", self.err("demo_text"))
        self.assertEqual(d1.get("changes"), 4)          # (mode + its preset, drain, the button, the key)
        self.assertIn("[mod] settings_demo: Hello (press 1)", self.log.splitlines())
        rows = self.d("demo_list")
        note = next(i for i in range(rows["count"]) if rows[f"settings.{i}.key"] == "note_file")
        self.assertEqual((rows[f"settings.{note}.value"], rows[f"settings.{note}.waiting"]), ("ON", True))
        self.assertFalse(self.d("demo0").get("note_file"))  # (the getters give what this deploy used)

    def test_watchers_and_own_changes(self):
        self.assertEqual(self.d("w0"), {"count": 0, "last": "-", "init_rc": 0})
        self.assertEqual(self.d("w1")["count"], 1)
        self.assertEqual(self.d("w1")["last"], "t")
        self.assertEqual(self.d("own"), {"rc": 0})
        self.assertEqual(self.d("w2")["count"], 1)       # (setting_set: no watcher call)
        self.assertEqual((self.d("own_bad"), self.d("own_none")), ({"rc": 2}, {"rc": 1}))
        self.assertEqual(self.d("tx_get")["str"], "hello")
        self.assertEqual(self.d("late"), {"rc": 1})
        self.assertIn("setting_watch refused: watchers can only be added from swmod_init", self.log)
        self.assertEqual(self.d("press"), {"rc": 2})     # (not a button)

    def test_restart_waits_for_the_next_start(self):
        self.assertEqual(self.d("r_get")["int"], 1)
        self.assertTrue(self.ok("r_set"))
        self.assertEqual(self.d("r_after")["int"], 1)    # (still what the game started with)
        self.assertEqual((self.d("r_ui")["waiting"], self.d("r_ui")["value"]), (True, "OFF"))

    def test_locked_rows(self):
        self.assertIn("Locked: needs T ON and C B.", self.err("locked"))
        lu = self.d("locked_ui")
        self.assertEqual((lu["rc"], lu["enabled"], lu["lock"]), (3, False, "Locked: needs T ON and C B."))

    def test_getters_from_two_threads(self):
        self.assertEqual(self.d("threads"), {"bad": 0})

    def test_python_and_c_agree_on_every_condition(self):
        by = self.probe.by_key
        mods_on = ms.mods_on(self.lib, self.lib.enabled())
        for step, vals in self.agree:
            rows = self.d(step)
            got = {rows[f"settings.{i}.key"]: rows[f"settings.{i}.shown"] for i in range(rows.get("count", 0))}
            for i, src in enumerate(CONDITIONS):
                key = f"q{i:02d}"
                with self.subTest(step=step, cond=src):
                    want = ms.eval_rpn(by[key].show_rpn, vals, by, mods_on)
                    self.assertEqual(got.get(key), want, f"{src} with {vals}")

    def test_values_file_written_by_the_writer(self):
        self.assertTrue(self.d("st1")["writer"])
        self.assertGreaterEqual(self.d("st2")["writes"], 1)
        data = json.loads(Path(ms.values_path(self.lib.root, PROBE_ID)).read_text())
        last = self.agree[-1][1]
        self.assertEqual(data["values"], {"zzz_future": {"kept": True}, "c": last["c"], "k": last["k"],
                                          "tx": last["tx"], "n": last["n"]})
        self.assertEqual(ms.load_values(self.lib.root, self.probe)["n"], last["n"])  # (Python reads what C wrote)
        demo = json.loads(Path(ms.values_path(self.lib.root, self.demo.mod_id)).read_text())["values"]
        self.assertEqual(demo, {"mode": "fast", "speed": 150, "drain": 1.5, "hotkey": "", "note_file": True})

    def test_a_change_just_before_the_game_closes_is_saved(self):
        self.assertTrue(self.late[0].get("ok"), self.late)
        self.assertEqual(self.late[1].get("data", {}).get("writes"), 0)   # (the writer thread was still waiting)
        self.assertEqual(self.late_values, {"format": 1, "mod": PROBE_ID, "values": {"t": True}}, self.late_log)

    def test_unload_takes_the_watcher_out(self):
        h = self.h
        self.assertTrue(self.ok("load", h), h.get("load"))
        self.assertEqual(self.d("w", h)["count"], 1)
        self.assertTrue(self.ok("unload", h), h.get("unload"))
        self.assertTrue(self.ok("set_after", h), h.get("set_after"))
        self.assertEqual(self.d("list", h)["mods"], 2)

    def test_without_settings_txt_defaults(self):
        d = self.b[0].get("data", {})
        self.assertEqual((d.get("settings"), d.get("mode"), d.get("speed")), (False, "classic", 100))
        self.assertEqual(self.b[1].get("data", {}).get("mods"), 0)
        self.assertIn("ready, without settings (no settings.txt deployed)", self.bare_log)


if __name__ == "__main__":
    unittest.main()
