"""Tests for the Mods page v2 contract (out of the repo: out/research/modmenu-v2-contract.md): romfs_mods' plan API
(Layer, Plan.add_layer, Plan.winner, add_generated), modman's mod.toml keys, its hooks into the tools of the other
parts (each one faked here), history.jsonl and profiles.

Every install, mod and file here is made up, in temp folders. No game is started and nothing is rebuilt."""
import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modman as mm  # noqa: E402
import romfs_mods as rm  # noqa: E402
from test_build_native import make_pe  # noqa: E402
from test_nso import make_nso  # noqa: E402

BID = "cd57b23fa4bbad65"
SEGS = [(0x0, bytes(0x1000)), (0x1000, b"\x55" * 0x200), (0x2000, b"\x66" * 0x100)]


def write(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return root


def fake_install(inst: Path) -> Path:
    return write(inst, {
        "savagewind.exe": b"MZ made-up exe\0test-id\0",
        "extracted/exefs/main": make_nso(bytes.fromhex(BID) + bytes(24), SEGS),
        "extracted/romfs/Actor/Pack/Hero.txt": "game hero",
        "extracted/romfs/Pack/Title.txt": "game title",
        "extracted/romfs/System/Version.txt": "made up",
        "extracted/swgame.dll": b"made-up game code",
        "extracted/swgame.dll.id": "test-id\n",
    })


def quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **kw)
    return r, buf.getvalue()


MOD_TOML = """[mod]
id = "{id}"
name = "{name}"
version = "1.0"
{extra}
"""


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="contract-test-"))
        self.addCleanup(lambda: mm.remove_tree(str(self.tmp)))


# ---------------------------------------------------------------- romfs_mods: the plan API

class PlanApi(Tmp):
    def setUp(self):
        super().setUp()
        self.a = write(self.tmp / "a", {"romfs/Actor/Pack/X.txt": "a", "romfs/Actor/Pack/Y.txt": "a-y",
                                        "options/fast/Actor/Pack/X.txt": "fast",
                                        "options/dlc/Map/MainField/D.txt": "dlc option"})
        self.b = write(self.tmp / "b", {"romfs/Actor/Pack/Z.txt": "b"})
        self.fast = str(self.a / "options" / "fast")

    def test_option_folders_are_not_part_of_the_mod_itself(self):
        p = rm.plan([str(self.a)])
        self.assertEqual(sorted(p.winners), ["Actor/Pack/X.txt", "Actor/Pack/Y.txt"])
        self.assertEqual(p.winners["Actor/Pack/X.txt"][1].src, str(self.a / "romfs" / "Actor" / "Pack" / "X.txt"))

    def test_a_layer_wins_over_its_mod_and_loses_to_later_mods(self):
        c = write(self.tmp / "c", {"romfs/Actor/Pack/Y.txt": "c"})
        p = rm.plan([str(self.a), rm.Layer(self.fast, "a"), str(self.b), str(c)])
        self.assertEqual(p.winner("actor/pack/x.txt").read(), b"fast")
        self.assertEqual(p.winner("Actor/Pack/X.txt").mod, "a option fast")
        self.assertEqual(p.winner("Actor/Pack/Y.txt").read(), b"c")
        # (the option replacing its own mod's file is no clash; c over a is)
        self.assertEqual([c["path"] for c in p.conflicts], ["Actor/Pack/Y.txt"])
        q = rm.plan([str(self.a), str(self.b), str(c)])
        q.add_layer("a", self.fast)
        self.assertEqual({t: q.winner(t).read() for t in q.winners}, {t: p.winner(t).read() for t in p.winners})
        self.assertEqual([type(s) for s in q.sources], [str, rm.Layer, str, str])
        with self.assertRaises(rm.DeployError):
            q.add_layer("nope", self.fast)

    def test_a_dlc_layer_goes_to_the_dlc(self):
        p = rm.plan([str(self.a), rm.Layer(str(self.a / "options" / "dlc"), "a", dlc=True)])
        self.assertIn("aoc/0010/Map/MainField/D.txt", p.winners)
        self.assertEqual(p.winner("aoc/0010/Map/MainField/D.txt").read(), b"dlc option")

    def test_winner_falls_back_to_the_game(self):
        inst = fake_install(self.tmp / "inst")
        g = rm.GameIndex.scan(str(inst / "extracted"))
        p = rm.plan([str(self.b)], g)
        w = p.winner("pack/title.txt")
        self.assertIsNone(w.mod)
        self.assertEqual((w.target, w.read()), ("Pack/Title.txt", b"game title"))
        self.assertEqual(p.winner("Actor/Pack/Z.txt").mod, "b")
        self.assertIsNone(p.winner("Pack/Nothing.txt"))

    def test_generated_files_win_and_are_listed_with_their_owner(self):
        inst = fake_install(self.tmp / "inst")
        g = rm.GameIndex.scan(str(inst / "extracted"))
        p = rm.plan([str(self.a)], g)
        taken = rm.add_generated(p, [("actor/pack/x.txt", b"made", None), ("Pack/Title.txt", b"title", None),
                                     ("Pack/Big.txt", b"big", 4096)], "generated:fonts")
        self.assertEqual(taken, ["Actor/Pack/X.txt", "Pack/Title.txt"])
        self.assertEqual(p.winner("Actor/Pack/X.txt").read(), b"made")
        self.assertEqual(p.winner("Actor/Pack/X.txt").mod, "generated:fonts")
        self.assertEqual(p.generated["Actor/Pack/X.txt"], {"owner": "generated:fonts", "built_on": "a"})
        self.assertIsNone(p.generated["Pack/Title.txt"]["built_on"])
        with self.assertRaises(ValueError):
            rm.add_generated(p, [("Pack/T.txt", b"x", None)], "fonts")
        with self.assertRaises(ValueError):
            rm.add_generated(p, [("../x.txt", b"x", None)], "generated:fonts")
        out = self.tmp / "overlay"
        man = rm.deploy([], str(out), plan_=p)
        self.assertEqual((out / "Actor" / "Pack" / "X.txt").read_bytes(), b"made")
        self.assertFalse((out / "Pack" / "Big.txt").exists())
        f = man["files"]["Actor/Pack/X.txt"]
        self.assertEqual((f["mod"], f.get("generated"), f.get("built_on")), ("generated:fonts", True, "a"))
        self.assertNotIn("merged_from", f)
        self.assertEqual(man["generated"], {"generated:fonts": ["Actor/Pack/X.txt", "Pack/Title.txt"]})
        self.assertTrue(any("Pack/Big.txt needs a bigger size-table" in w for w in man["warnings"]))
        self.assertEqual(json.loads((out / rm.MARKER).read_text(encoding="utf-8"))["generated"], man["generated"])


# ---------------------------------------------------------------- mod.toml: the Mods page v2 keys

class ModToml(unittest.TestCase):
    def parse(self, extra: str):
        return mm.parse_manifest(MOD_TOML.format(id="test.v2", name="V2", extra=extra))

    def test_good_keys(self):
        man, errs = self.parse('icon = "pics/icon.png"\nbackground = "bg.jpg"\ntitle_picture = "t.webp"\n'
                               'url = "https://example.org/v2"\nupdate_url = "https://example.org/v2.json"\n'
                               '[font]\nfile = "fonts/F.ttf"\nlanguages = ["US", "JP"]\nlicense = "OFL-1.1"\n'
                               '[[pages]]\nkey = "adv"\nlabel = "Advanced"\n'
                               '[[settings]]\nkey = "speed"\ntype = "slider"\nlabel = "Speed 100%"\n')
        self.assertEqual(errs, [])
        self.assertEqual((man.icon, man.background, man.title_picture), ("pics/icon.png", "bg.jpg", "t.webp"))
        self.assertEqual((man.url, man.update_url), ("https://example.org/v2", "https://example.org/v2.json"))
        self.assertEqual(man.font, {"file": "fonts/F.ttf", "for": "menu", "languages": ["US", "JP"],
                                    "license": "OFL-1.1"})
        self.assertEqual([s["key"] for s in man.settings], ["speed"])
        self.assertEqual([p["key"] for p in man.pages], ["adv"])
        self.assertTrue(any("has %" in n for n in man.notes))  # (warned, not refused)
        self.assertFalse(any("isn't something modman knows" in n for n in man.notes))

    def test_bad_keys(self):
        for extra, want in (('icon = "icon.gif"', "[mod] icon"), ('background = "../bg.png"', "[mod] background"),
                            ('url = "http://example.org"', "[mod] url"), ('update_url = "https://a b"', "update_url"),
                            ('[font]\nlicense = "x"', "[font] needs file"), ('[font]\nfile = "f.otf"', ".ttf font"),
                            ('[font]\nfile = "f.ttf"\nfor = "some"', "[font] for"),
                            ('[font]\nfile = "f.ttf"\nlanguages = ["KR"]', "[font] languages has none"),
                            ('[font]\nfile = "f.ttf"\nlanguages = ["XX"]', "[font] languages must be"),
                            ('[settings]\nkey = "x"', "[[settings]]"), ("[pages]\nx = 1", "[[pages]]")):
            with self.subTest(extra=extra):
                man, errs = self.parse(extra)
                self.assertIsNone(man)
                self.assertTrue(any(want in e for e in errs), errs)

    def test_later_font_kinds_are_notes(self):
        man, errs = self.parse('[font]\nfile = "f.ttf"\nfor = "all"\nlanguages = ["us", "KR"]')
        self.assertEqual(errs, [])
        self.assertEqual((man.font["for"], man.font["languages"]), ("menu", ["US"]))
        self.assertTrue(any('for = "all" comes later' in n for n in man.notes))
        self.assertTrue(any("languages KR come later" in n for n in man.notes))

    def test_named_files_must_be_there(self):
        man, _ = self.parse('icon = "icon.png"\n[font]\nfile = "f.ttf"\n[[settings]]\nkey = "a"\n')
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(mm._FEATURE_CACHE, {"mod_settings": None,
                                                                                    "font": None}):
            problems, notes = mm.manifest_v2_problems(man, d)
            self.assertEqual(sorted(problems), ['[font] file = "f.ttf": no such file in the mod',
                                                '[mod] icon = "icon.png": no such file in the mod'])
            self.assertTrue(any("tools/mod_settings.py" in n for n in notes))
            write(Path(d), {"icon.png": b"made up", "f.ttf": b"made up"})
            self.assertEqual(mm.manifest_v2_problems(man, d)[0], [])

    def test_a_tool_that_is_missing_or_broken_is_off(self):
        self.assertIsNone(mm.feature("no_such_tool_here"))
        broken = types.SimpleNamespace(manifest_problems=lambda data, root: 1 / 0)
        man, _ = self.parse('[[settings]]\nkey = "a"\n')
        with mock.patch.dict(mm._FEATURE_CACHE, {"mod_settings": broken}), tempfile.TemporaryDirectory() as d:
            problems, notes = mm.manifest_v2_problems(man, d)
        self.assertEqual(problems, [])  # (a check that fails is never a refusal)
        self.assertTrue(any("off this time (ZeroDivisionError" in n for n in notes))


# ---------------------------------------------------------------- modman deploy: the hooks, in order

class Hooks(Tmp):
    def setUp(self):
        super().setUp()
        self.install = fake_install(self.tmp / "SavageWind" / "Savage Wind")
        self.libdir = self.tmp / "SavageWind" / "modloader"
        mm.Library(str(self.libdir), str(self.install)).save()
        self.host_dir = self.tmp / "build-native"
        write(self.host_dir, {"modhost.dll": make_pe(exports=("swgame_tables",))})
        for p in (mock.patch.object(mm, "game_running", return_value=False),
                  mock.patch.object(mm, "MODHOST_DIR", str(self.host_dir)),
                  mock.patch.dict(os.environ, {"USERPROFILE": str(self.tmp / "home")})):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop(mm.ENV_LIBRARY, None)
        self.calls: list[str] = []
        self.mode = "fast"
        self.fail_font = False
        self.fakes = self.make_fakes()
        p = mock.patch.dict(mm._FEATURE_CACHE, self.fakes)
        p.start()
        self.addCleanup(p.stop)

    @property
    def lib(self):
        return mm.Library.load(str(self.libdir))

    def make_fakes(self) -> dict:
        t = self

        def manifest_problems(data, root):
            t.calls.append(f"check {data['mod']['id']}")
            assert os.path.isfile(os.path.join(root, "mod.toml"))
            return [], [f"{len(data['settings'])} setting(s)"]

        def has_options(lib, m):
            return os.path.isdir(os.path.join(lib.mods_dir, m["id"], "options"))

        def option_romfs(lib, mods):  # (mods: the enabled library entries; items (entry, folder) as mod_settings.py)
            t.calls.append("option_romfs")
            return [(m, os.path.join(lib.mods_dir, m["id"], "options", t.mode)) for m in mods if has_options(lib, m)]

        def deploy_values(lib, mods):
            return {m["id"]: {"mode": t.mode} for m in mods if has_options(lib, m)}

        def write_settings_txt(lib, mods, deploy):
            t.calls.append("settings_txt")
            text = "".join(f"mod\t{m['id']}\ton\n" for m in mods) + f"library\t{lib.root}\n"
            with open(deploy.add_file("native/settings.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

        class Generated(list):
            pass

        def font_files(lib, plan):
            t.calls.append("font")
            if t.fail_font:
                raise RuntimeError("made-up failure")
            w = plan.winner("Pack/Title.txt")
            gen = Generated([("Pack/Title.txt", w.read() + b" +font", None),
                             ("Font/Font_US.sbfarc", b"grew", ("Font/Font_US.bfarc", 4096))])
            gen.notes, gen.attention, gen.symbols_full, gen.font = ["symbols added"], [], True, "Easy-read"
            return gen

        def title_files(lib, plan, picture):
            t.calls.append(f"menu_bg {os.path.basename(picture) if picture else None}")
            return [("System/Version.txt", b"title picture", None)] if picture else []

        def picture_files(lib, plan):
            t.calls.append("mod_pictures")
            w = plan.winner("Pack/Title.txt")
            t.title_base = w.mod
            return [("Pack/Title.txt", w.read() + b" +pictures", None),
                    ("deploy/pictures/atlas.json", b'{"format": 1}', None),
                    ("deploy/../launch.json", b"no", None)]

        def on_add(lib, entry, folder, man):
            t.calls.append(f"on_add {entry['id']}")
            return ["picture cache made"]

        return {"mod_settings": types.SimpleNamespace(manifest_problems=manifest_problems, option_romfs=option_romfs,
                                                      deploy_values=deploy_values,
                                                      write_settings_txt=write_settings_txt),
                "font": types.SimpleNamespace(deploy_files=font_files),
                "menu_bg": types.SimpleNamespace(deploy_files=title_files),
                "mod_pictures": types.SimpleNamespace(deploy_files=picture_files, on_add=on_add)}

    def add(self, name: str, files: dict):
        src = write(self.tmp / "downloads" / name, files)
        return quiet(mm.add_mod, self.lib, str(src))[0]

    def add_all(self):
        opts = MOD_TOML.format(id="test.opts", name="Opts", extra='title_picture = "title.png"\n[data]\nromfs = "romfs"'
                               '\n[[settings]]\nkey = "mode"\ntype = "choice"\napplies = "deploy"\n')
        entry, scan, notes = self.add("Opts", {"mod.toml": opts, "title.png": b"made-up picture",
                                               "romfs/Actor/Pack/Hero.txt": "mod hero",
                                               "options/fast/Actor/Pack/Hero.txt": "fast hero",
                                               "options/slow/Actor/Pack/Hero.txt": "slow hero"})
        self.assertIn("picture cache made", notes)
        self.assertIn("1 setting(s)", scan.notes)
        dll = MOD_TOML.format(id="test.dll", name="Dll", extra='loader_api = 1\n[code]\nwindows = "bin/d.dll"\n'
                              '[[settings]]\nkey = "on"\ntype = "toggle"\n')
        self.add("Dll", {"mod.toml": dll, "bin/d.dll": make_pe(exports=("swmod_init",))})

    def test_deploy_runs_the_parts_in_order(self):
        self.add_all()
        self.assertEqual(self.calls[:3], ["check test.opts", "on_add test.opts", "check test.dll"])
        self.calls.clear()
        launch, out = quiet(mm.deploy, self.lib)
        self.assertEqual([c for c in self.calls if not c.startswith("check")],
                         ["option_romfs", "font", "menu_bg title.png", "mod_pictures", "settings_txt"])
        ov = Path(self.libdir / "deploy" / "overlay")
        self.assertEqual((ov / "Actor" / "Pack" / "Hero.txt").read_text(), "fast hero")
        self.assertEqual((ov / "Pack" / "Title.txt").read_text(), "game title +font +pictures")
        self.assertEqual(self.title_base, "generated:fonts")  # (pictures run last, over the final winner)
        self.assertEqual((ov / "System" / "Version.txt").read_text(), "title picture")
        man = json.loads((ov / rm.MARKER).read_text(encoding="utf-8"))
        self.assertEqual(man["files"]["Pack/Title.txt"]["mod"], "generated:pictures")
        self.assertEqual(man["files"]["Pack/Title.txt"]["built_on"], "generated:fonts")
        self.assertEqual(man["generated"]["generated:title"], ["System/Version.txt"])
        self.assertEqual(launch["parts"]["overlay"]["mods"], 2)   # (the mod and its option folder)
        self.assertEqual(launch["settings"], {"test.opts": {"mode": "fast"}})
        dep = self.libdir / "deploy"
        self.assertEqual((dep / "pictures" / "atlas.json").read_text(), '{"format": 1}')
        marker = json.loads((dep / mm.MARKER).read_text(encoding="utf-8"))
        self.assertIn("pictures/atlas.json", marker["files"])
        self.assertIn("native/settings.txt", marker["files"])
        txt = (dep / "native" / "settings.txt").read_text(encoding="utf-8")
        self.assertIn("mod\ttest.opts\ton\n", txt)
        self.assertIn(f"library\t{self.libdir}\n", txt)
        self.assertIn("Fonts: symbols added", launch["notes"])
        self.assertEqual(launch["made"], {"fonts": {"symbols_full": True, "font": "Easy-read"}})
        self.assertFalse((ov / "Font" / "Font_US.sbfarc").exists())  # (it needs a size-table entry: not yet)
        self.assertTrue(any("Font/Font_US.sbfarc needs a bigger size-table" in a for a in launch["attention"]))
        self.assertTrue(any("deploy\\../launch.json" in a for a in launch["attention"]), launch["attention"])
        self.assertIn(mm.ENV_LIBRARY, launch["clear_env"])
        hist = mm.read_history(self.lib)
        self.assertEqual([h["type"] for h in hist], ["deploy"])
        self.assertEqual(hist[0]["hash"], launch["hash"])
        self.assertEqual(hist[0]["settings"], {"test.opts": {"mode": "fast"}})
        self.assertEqual([(m["id"], m["enabled"]) for m in hist[0]["mods"]], [("test.opts", True), ("test.dll", True)])
        # a deploy setting's value is part of what the deploy is made from
        h = mm.inputs_hash(self.lib)
        self.assertEqual(h, launch["hash"])
        self.mode = "slow"
        self.assertNotEqual(mm.inputs_hash(self.lib), h)
        launch2, _ = quiet(mm.deploy, self.lib)
        self.assertEqual((ov / "Actor" / "Pack" / "Hero.txt").read_text(), "slow hero")
        self.assertEqual(len(mm.read_history(self.lib)), 2)

    def test_a_failing_part_is_off_and_the_deploy_goes_on(self):
        self.add_all()
        self.fail_font = True
        launch, _ = quiet(mm.deploy, self.lib)
        self.assertTrue(any(a.startswith("Fonts: off this time (RuntimeError: made-up failure)")
                            for a in launch["attention"]), launch["attention"])
        ov = Path(self.libdir / "deploy" / "overlay")
        self.assertEqual((ov / "Pack" / "Title.txt").read_text(), "game title +pictures")

    def test_a_dry_run_makes_nothing(self):
        self.add_all()
        self.calls.clear()
        launch, _ = quiet(mm.deploy, self.lib, dry_run=True)
        self.assertNotIn("font", self.calls)   # (nothing is made in a dry run)
        self.assertEqual(launch["settings"], {"test.opts": {"mode": "fast"}})
        self.assertFalse((self.libdir / "deploy" / mm.HISTORY).exists())

    def test_without_the_parts_nothing_changes(self):
        with mock.patch.dict(mm._FEATURE_CACHE, {k: None for k in mm.FEATURES}):
            self.add("Plain", {"romfs/Actor/Pack/Hero.txt": "plain hero"})
            launch, _ = quiet(mm.deploy, self.lib)
        self.assertNotIn("settings", launch)
        man = json.loads((self.libdir / "deploy" / "overlay" / rm.MARKER).read_text(encoding="utf-8"))
        self.assertNotIn("generated", man)
        self.assertEqual(list(man["files"]), ["Actor/Pack/Hero.txt"])
        cfg = json.loads((self.libdir / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(list(cfg), ["tool", "format", "install", "mods"])  # (no "profile": Default)

    def test_run_records_the_start(self):
        with mock.patch.dict(mm._FEATURE_CACHE, {k: None for k in mm.FEATURES}):
            self.add("Plain", {"romfs/Actor/Pack/Hero.txt": "plain hero"})
            quiet(mm.deploy, self.lib)
        proc = mock.Mock(pid=4321)
        with mock.patch.object(mm.subprocess, "Popen", return_value=proc) as popen, \
                mock.patch.dict(os.environ, {mm.ENV_LIBRARY: "Z:\\stale"}):
            quiet(mm.run, self.lib)
        self.assertNotIn(mm.ENV_LIBRARY, popen.call_args.kwargs["env"])  # (a leftover one is cleared)
        hist = mm.read_history(self.lib)
        self.assertEqual([h["type"] for h in hist], ["deploy", "start"])
        self.assertEqual((hist[1]["pid"], hist[1]["hash"], hist[1]["profile"]), (4321, hist[0]["hash"], "Default"))


# ---------------------------------------------------------------- history.jsonl

class History(Tmp):
    def test_append_read_and_trim(self):
        lib = mm.Library(str(self.tmp / "lib"))
        with mock.patch.object(mm, "HISTORY_KEEP", 3):
            for k in range(8):
                mm.history_append(lib, {"type": "deploy", "n": k, "pad": "x" * 5000})
        (self.tmp / "lib" / "deploy" / mm.HISTORY).open("a", encoding="utf-8").write("not json\n[1]\n")
        recs = mm.read_history(lib)
        self.assertEqual([r["n"] for r in recs], [4, 5, 6, 7])  # (cut to 3 past 6 lines, then one more)
        self.assertTrue(all("time" in r for r in recs))

    def test_the_history_file_doesnt_stop_a_deploy_folder_claim(self):
        lib = mm.Library(str(self.tmp / "lib"))
        mm.history_append(lib, {"type": "start"})
        mm.DeployDir(lib.deploy_dir).claim()  # (no marker yet, only history.jsonl: fine)
        (Path(lib.deploy_dir) / "other.txt").write_text("x")
        with self.assertRaises(mm.ModmanError):
            mm.DeployDir(lib.deploy_dir).claim()


# ---------------------------------------------------------------- profiles

class Profiles(Tmp):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(mm, "game_running", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.root = self.tmp / "lib"
        lib = mm.Library(str(self.root), None, [
            {"id": "a", "name": "A", "enabled": True, "kinds": ["romfs"]},
            {"id": "b", "name": "B", "enabled": True, "kinds": ["romfs"], "bnp": {"options": ["x"]}},
            {"id": "c", "name": "C", "enabled": False, "kinds": ["romfs"]}], extra={"window": {"w": 1}})
        lib.save()
        mm.write_setting_values(lib, "a", {"speed": 120})

    @property
    def lib(self):
        return mm.Library.load(str(self.root))

    def test_default_is_todays_library(self):
        lib = self.lib
        self.assertEqual((lib.profile, mm.profile_names(lib)), ("Default", ["Default"]))
        self.assertFalse((self.root / mm.PROFILES_DIR).exists())
        cfg = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.assertNotIn("profile", cfg)
        self.assertEqual(cfg["window"], {"w": 1})   # (keys this modman doesn't know are kept)

    def test_new_use_and_back(self):
        lib = self.lib
        mm.new_profile(lib, "Hard mode")
        self.assertEqual(self.lib.profile, "Hard mode")
        self.assertTrue((self.root / "profiles" / "default.json").is_file())
        self.assertTrue((self.root / "profiles" / "hard-mode.json").is_file())
        # change the set: c on, a off, b first, a's setting changed
        lib = self.lib
        lib.mods = [lib.mods[1], lib.mods[0], lib.mods[2]]
        lib.mods[1]["enabled"], lib.mods[2]["enabled"] = False, True
        lib.save()
        mm.write_setting_values(lib, "a", {"speed": 200})
        lib.mods.append({"id": "d", "name": "D", "enabled": True, "kinds": []})
        lib.save()
        notes = mm.use_profile(self.lib, "default")
        lib = self.lib
        self.assertEqual(lib.profile, "Default")
        self.assertEqual([(m["id"], m["enabled"]) for m in lib.mods],
                         [("a", True), ("b", True), ("c", False), ("d", False)])
        self.assertEqual(mm.read_setting_values(lib, "a"), {"speed": 120})
        self.assertTrue(any("Off in this set" in n and "D" in n for n in notes), notes)
        self.assertNotIn("profile", json.loads((self.root / "config.json").read_text(encoding="utf-8")))
        mm.use_profile(self.lib, "HARD MODE")
        lib = self.lib
        self.assertEqual([(m["id"], m["enabled"]) for m in lib.mods],
                         [("b", True), ("a", False), ("c", True), ("d", True)])
        self.assertEqual(mm.read_setting_values(lib, "a"), {"speed": 200})
        self.assertEqual(json.loads((self.root / "config.json").read_text(encoding="utf-8"))["profile"], "Hard mode")
        self.assertEqual(mm.profile_names(lib), ["Default", "Hard mode"])

    def test_options_differences_are_named(self):
        mm.new_profile(self.lib, "Other")
        mm.use_profile(self.lib, "Default")   # (leaving a set saves it: edit its file only after that)
        data = json.loads((self.root / "profiles" / "other.json").read_text(encoding="utf-8"))
        self.assertEqual(data["options"], {"b": ["x"]})
        data["options"]["b"] = ["y"]
        (self.root / "profiles" / "other.json").write_text(json.dumps(data), encoding="utf-8")
        notes = mm.use_profile(self.lib, "Other")
        self.assertTrue(any("B was added with other options" in n for n in notes), notes)

    def test_refusals(self):
        with self.assertRaises(mm.ModmanError):
            mm.new_profile(self.lib, "bad/name")
        with self.assertRaises(mm.ModmanError):
            mm.new_profile(self.lib, "default")
        with self.assertRaises(mm.ModmanError):
            mm.use_profile(self.lib, "missing")
        mm.new_profile(self.lib, "One")
        with self.assertRaises(mm.ModmanError):
            mm.remove_profile(self.lib, "one")     # (in use)
        with self.assertRaises(mm.ModmanError):
            mm.remove_profile(self.lib, "Default")
        with mock.patch.object(mm, "game_running", return_value=True), self.assertRaises(mm.ModmanError):
            mm.use_profile(self.lib, "Default")
        mm.use_profile(self.lib, "Default")
        mm.remove_profile(self.lib, "One")
        self.assertEqual(mm.profile_names(self.lib), ["Default"])

    def test_cli(self):
        args = ["--library", str(self.root), "profile"]
        code, out = quiet(mm.main, args)
        self.assertEqual(code, 0)
        self.assertIn("in use: Default", out)
        self.assertEqual(quiet(mm.main, args + ["new", "Speedrun"])[0], 0)
        code, out = quiet(mm.main, args + ["use", "default"])
        self.assertEqual(code, 0)
        self.assertIn("Run deploy to use it.", out)
        code, out = quiet(mm.main, args + ["use"])
        self.assertEqual(code, 2)
        code, out = quiet(mm.main, args + ["remove", "speedrun"])
        self.assertEqual(code, 0, out)
        self.assertIn("Removed the mod set 'Speedrun'", out)


if __name__ == "__main__":
    unittest.main()
