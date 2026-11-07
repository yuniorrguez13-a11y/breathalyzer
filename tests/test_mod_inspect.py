"""Tests for tools/mod_inspect.py. They build fake mods from dummy bytes: no game files."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import mod_inspect as mi  # noqa: E402
import wiiu_convert as wc  # noqa: E402

BUILD_190 = "cd57b23fa4bbad65" + "0" * 48
BUILD_OTHER = "0123456789abcdef" + "0" * 48


def make(root: Path, files: dict[str, bytes | str]) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)
    return root


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_on(self, files):
        mod = make(self.root / "mod", files)
        findings = mi.inspect(mi.open_source(mod))
        return {f.kind: f for f in findings}, mi.overall(findings)

    def test_switch_romfs(self):
        kinds, overall = self.run_on({"01007EF00011E000/romfs/Actor/x.sbactorpack": b"x"})
        self.assertEqual(kinds["Switch RomFS files"].verdict, mi.WORKS)
        self.assertEqual(overall, mi.VERDICTS[mi.WORKS])

    def test_wrapped_in_one_folder(self):
        kinds, _ = self.run_on({"MyMod/romfs/Pack/Bootup.pack": b"x"})
        self.assertIn("Bootup.pack", kinds["Switch RomFS files"].detail)

    def test_wiiu_data_with_models(self):
        # Wii U models and textures convert (tools/wiiu_model); other model/texture formats stay Wii U data
        kinds, overall = self.run_on({"content/Actor/a.sbactorpack": b"x", "content/Model/b.sbfres": b"x",
                                      "content/UI/Map/c.sbmaptex": b"x"})
        f = kinds["Wii U game data"]
        self.assertEqual(f.verdict, mi.CONVERT)
        self.assertIn("(text, maps, actor data, 1 model/texture file(s))", f.detail)
        self.assertIn("tools/wiiu_convert.py converts them on your PC", f.detail)
        self.assertIn("not proven on real Wii U files yet, so check them in game", f.detail)
        self.assertIn("Not converted yet, kept as Wii U data: 1 model/texture", f.detail)
        self.assertEqual(overall, mi.VERDICTS[mi.CONVERT])
        # an X.Tex1 without its X.Tex2 converts, with a note; an X.Tex2 alone stays Wii U data
        kinds, _ = self.run_on({"content/Model/b.Tex1.sbfres": b"x", "content/Model/c.Tex2.sbfres": b"x"})
        f = kinds["Wii U game data"]
        self.assertEqual(f.verdict, mi.CONVERT)
        self.assertIn("1 X.Tex1 without its X.Tex2", f.detail)
        self.assertIn("model/texture (incl. 1 X.Tex2 without its X.Tex1)", f.detail)

    def test_wiiu_kinds_wiiu_convert_keeps_are_for_later(self):
        # modman add refuses these (nothing converts): mod_inspect must not call them "convert". The RSTB, which many
        # model mods ship, and files that aren't game data don't count.
        rstb = "content/System/Resource/ResourceSizeTable.product.srsizetable"
        cases = {
            "map texture + the RSTB": ({"content/UI/MapTex/A.sbmaptex": b"x", rstb: b"x", "content/Thumbs.db": b"x"},
                                       "1 file(s), all 1 model/texture"),
            "mipmaps alone": ({"content/Model/Armor.Tex2.sbfres": b"x"}, "1 file(s), all 1 model/texture"),
            "UI layouts": ({"content/Layout/Common.sblarc": b"x", "content/Layout/Title.bflyt": b"x"},
                           "2 UI layout"),
            "effects": ({"content/Effect/Fire.sesetlist": b"x"}, "1 effect"),
            "event flows": ({"content/EventFlow/Demo.bfevfl": b"x", "aoc/0010/EventFlow/Dlc.bfevfl": b"x"},
                            "2 event flow"),
            "Havok physics": ({"content/Physics/RigidBody/A.hkrb": b"x"}, "1 Havok physics"),
            "only the RSTB": ({rstb: b"x"}, "only the size table (RSTB)"),
        }
        for name, (files, want) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                findings = mi.inspect(mi.open_source(make(Path(d), files)))
                f = {x.kind: x for x in findings}["Wii U game data"]
                self.assertEqual(f.verdict, mi.LATER, f.detail)
                self.assertIn(want, f.detail)
                self.assertNotIn("modman.py add does it", f.detail)
        # with one file wiiu_convert converts, it is "convert" again; the RSTB still doesn't count
        kinds, _ = self.run_on({"content/Model/A.sbmaptex": b"x", rstb: b"x", "content/Pack/Title.sbyml": b"x"})
        f = kinds["Wii U game data"]
        self.assertEqual(f.verdict, mi.CONVERT)
        self.assertTrue(f.detail.startswith("2 file(s): "), f.detail)
        self.assertIn("kept as Wii U data: 1 model/texture", f.detail)
        # a model (with its RSTB) converts now
        kinds, _ = self.run_on({"content/Model/Armor.sbfres": b"x", rstb: b"x"})
        self.assertEqual(kinds["Wii U game data"].verdict, mi.CONVERT)

    def test_kept_kinds_match_wiiu_convert(self):
        # mod_inspect's list of what wiiu_convert keeps as Wii U data is a copy of wiiu_convert's own
        self.assertEqual(mi.WIIU_MODEL_EXT, wc.MODEL_EXT)
        self.assertEqual(set(mi.WIIU_KEPT), set(wc.PLATFORM_EXT))
        for kind, exts in wc.PLATFORM_EXT.items():
            self.assertEqual(mi.WIIU_KEPT[kind] - {"blarc"}, exts, kind)
        for ext, kind in wc.EXT_KIND.items():
            self.assertEqual(mi.wiiu_kept_kind(f"content/A/b.{ext}"), kind)
            self.assertEqual(mi.wiiu_kept_kind(f"content/A/b.s{ext}"), kind)
        self.assertIsNone(mi.wiiu_kept_kind("content/Actor/Pack/A.sbactorpack"))
        self.assertIsNone(mi.wiiu_kept_kind("content/Pack/Title.txt"))

    def test_switch_overlay_folder_is_not_wiiu(self):
        # bare RomFS folders next to aoc/0010/: a Switch overlay folder, which modman and romfs_mods take as it is
        kinds, overall = self.run_on({"Pack/Title.pack": b"x", "aoc/0010/Map/MainField/New.smubin": b"x"})
        self.assertNotIn("Wii U game data", kinds)
        f = kinds["Switch RomFS files"]
        self.assertEqual(f.verdict, mi.WORKS)
        self.assertIn("2 file(s), 1 of them DLC", f.detail)
        self.assertEqual(overall, mi.VERDICTS[mi.WORKS])
        # aoc/0010 on its own, or next to content/, is still Wii U data
        for files in ({"aoc/0010/Map/MainField/New.smubin": b"x"},
                      {"content/Pack/Title.pack": b"x", "aoc/0010/Map/MainField/New.smubin": b"x"}):
            with self.subTest(files=list(files)), tempfile.TemporaryDirectory() as d:
                found = {x.kind: x for x in mi.inspect(mi.open_source(make(Path(d), files)))}
                self.assertEqual(set(found), {"Wii U game data"})
                self.assertTrue(found["Wii U game data"].detail.startswith(f"{len(files)} file(s): "))

    def test_wiiu_data_converts_with_wiiu_convert(self):
        kinds, overall = self.run_on({"content/Actor/a.sbactorpack": b"x", "aoc/0010/Map/b.smubin": b"x"})
        f = kinds["Wii U game data"]
        self.assertEqual(f.verdict, mi.CONVERT)  # docs/MOD-COMPAT.md: "Wii U data that can be merged"
        self.assertIn("2 file(s): tools/wiiu_convert.py converts them on your PC", f.detail)
        self.assertIn("modman.py add", f.detail)
        self.assertNotIn("UKMM", f.detail)
        self.assertNotIn("Not converted yet", f.detail)
        self.assertEqual(overall, mi.VERDICTS[mi.CONVERT])

    def test_wiiu_audio_only_is_for_later(self):
        # docs/MOD-COMPAT.md: "Wii U audio, Havok physics, ..." is "later"; "Wii U models and textures" convert
        kinds, overall = self.run_on({"content/Sound/c.bfstm": b"x", "content/Physics/A.hkrb": b"x"})
        f = kinds["Wii U game data"]
        self.assertEqual(f.verdict, mi.LATER)
        self.assertIn("can't convert these yet", f.detail)
        self.assertEqual(overall, mi.VERDICTS[mi.LATER])
        kinds, overall = self.run_on({"content/Model/b.sbfres": b"x", "content/Sound/c.bfstm": b"x"})
        self.assertEqual(kinds["Wii U game data"].verdict, mi.CONVERT)

    def test_cemu_code_patch(self):
        rules = "[Definition]\ntitleIds = 00050000101C9300,00050000101C9400\nname = Test\n"
        kinds, overall = self.run_on({"rules.txt": rules, "patch_test.asm": "[x]\nmoduleMatches = 0x0\n"})
        self.assertEqual(kinds["Cemu code patch (PowerPC)"].verdict, mi.REWRITE)
        self.assertEqual(overall, mi.VERDICTS[mi.REWRITE])

    def test_cemu_pack_with_data_is_partly_usable(self):
        rules = "[Definition]\ntitleIds = 00050000101C9300\n"
        kinds, overall = self.run_on({"rules.txt": rules, "patch.asm": "x",
                                      "content/Actor/a.sbactorpack": b"x"})
        self.assertIn("Wii U game data", kinds)
        self.assertEqual(overall, "partly usable")

    def test_cemu_preset_only(self):
        kinds, _ = self.run_on({"rules.txt": "[Definition]\ntitleIds = 00050000101C9500\n[Preset]\n"})
        self.assertEqual(kinds["Cemu settings preset"].verdict, mi.REWRITE)

    def test_ips_for_190_and_other(self):
        kinds, overall = self.run_on({f"exefs/{BUILD_190}.ips": b"IPS32" + b"\0" * 8})
        self.assertEqual(kinds["ExeFS patch"].verdict, mi.WORKS)  # docs/MOD-COMPAT.md: ExeFS patch for 1.9.0
        self.assertEqual(overall, mi.VERDICTS[mi.WORKS])
        self.assertIn("tools/exefs_patch.py", kinds["ExeFS patch"].detail)
        (self.root / "mod").rename(self.root / "old")
        kinds, _ = self.run_on({f"exefs/{BUILD_OTHER}.ips": b"PATCH" + b"\0" * 8})
        self.assertEqual(kinds["ExeFS patch"].verdict, mi.RETARGET)

    def test_pchtxt_build_id_from_text(self):
        kinds, _ = self.run_on({"exefs/fps.pchtxt": f"@nsobid-{BUILD_190}\n@enabled\n"})
        self.assertEqual(kinds["ExeFS patch"].verdict, mi.WORKS)

    def test_invalid_ips(self):
        kinds, _ = self.run_on({f"{BUILD_OTHER}.ips": b"nope"})
        self.assertIn("not a valid IPS", kinds["ExeFS patch"].detail)

    def test_exlaunch(self):
        kinds, overall = self.run_on({"exefs/subsdk9": b"x", "exefs/main.npdm": b"x"})
        f = kinds["Switch code mod (exlaunch / Skyline)"]
        self.assertEqual(f.verdict, mi.REWRITE)  # docs/MOD-COMPAT.md: exlaunch / Skyline code mod = rewrite
        self.assertEqual(overall, mi.VERDICTS[mi.REWRITE])
        self.assertIn("native mod", f.detail)
        self.assertNotIn("exlaunch", f.detail)  # (modman quotes kind and detail together: name it once)

    def test_exlaunch_next_to_romfs_is_partly_usable(self):
        _, overall = self.run_on({"romfs/Pack/Title.txt": b"x", "exefs/subsdk9": b"x"})
        self.assertEqual(overall, "partly usable")

    def test_cheats(self):
        kinds, overall = self.run_on({"cheats/cd57b23fa4bbad65.txt": "[Inf]\n04000000 00000000 00000000\n"})
        self.assertEqual(kinds["Atmosphere cheats"].verdict, mi.WORKS)  # docs/MOD-COMPAT.md: works (data cheats)
        self.assertEqual(overall, mi.VERDICTS[mi.WORKS])
        self.assertIn("tools/cheats.py", kinds["Atmosphere cheats"].detail)

    def test_cheats_for_another_version(self):
        kinds, _ = self.run_on({"cheats/0123456789abcdef.txt": "[Inf]\n04000000 00000000 00000000\n"})
        self.assertEqual(kinds["Atmosphere cheats"].verdict, mi.RETARGET)

    def test_native_mod_with_mod_toml(self):
        kinds, overall = self.run_on({"mod.toml": '[mod]\nid = "test.made-up"\n', "bin/made_up.dll": b"MZ"})
        self.assertEqual(kinds["Native mod (this loader)"].verdict, mi.WORKS)
        self.assertEqual(overall, mi.VERDICTS[mi.WORKS])

    def test_nothing_built_is_called_planned(self):
        """The four kinds docs/MOD-COMPAT.md lists as done all said 'later' (planned, not supported yet)."""
        kinds, _ = self.run_on({f"exefs/{BUILD_190}.ips": b"PATCH" + b"\0" * 8, "exefs/subsdk9": b"x",
                                "cheats/cd57b23fa4bbad65.txt": "x", "mod.toml": "[mod]\n"})
        self.assertEqual(len(kinds), 4)
        self.assertNotIn(mi.LATER, {f.verdict for f in kinds.values()})

    def test_bnp_wiiu(self):
        kinds, _ = self.run_on({"info.json": json.dumps({"name": "x", "platform": "wiiu"}),
                                "content/Actor/a.sbactorpack": b"x"})
        self.assertEqual(kinds["BCML mod (BNP)"].verdict, mi.CONVERT)
        self.assertIn("Wii U game data", kinds)

    def test_ukmm_zip(self):
        # modman add builds a Switch (or cross-platform) package from the game's files (tools/ukmm.py), but only part
        # of UKMM's kinds, and it isn't tried in the game: "partial", not "works"
        z = self.root / "m.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("meta.yml", "name: T\nplatform: !Specific Switch\n")
            zf.writestr("manifest.yml", "content: []\n")
        findings = mi.inspect(mi.open_source(z))
        self.assertEqual([(f.kind, f.verdict) for f in findings], [("UKMM package", mi.PARTIAL)])
        self.assertIn("tools/ukmm.py", findings[0].detail)
        self.assertIn("Not tried in the game", findings[0].detail)
        self.assertEqual(mi.overall(findings), mi.VERDICTS[mi.PARTIAL])

    def test_ukmm_platform_is_read_as_modman_add_reads_it(self):
        """ukmm-platform-agrees: mod_inspect and modman add (tools/ukmm.py) read meta.yml's platform the same way, so
        a package add refuses isn't rated as working."""
        manifest = "content: []\n"
        for platform, want, verdict in (("!Specific Switch", "switch", mi.PARTIAL), ("Universal", "any", mi.PARTIAL),
                                        ("\n  Specific: Wii U", "wiiu", mi.MERGE), ("Moon", "any", mi.LATER),
                                        (None, "any", mi.LATER)):
            meta = "name: T\n" + (f"platform: {platform}\n" if platform is not None else "")
            with self.subTest(platform=platform):
                kinds, _ = self.run_on({"meta.yml": meta, "manifest.yml": manifest})
                f = kinds["UKMM package"]
                self.assertEqual((f.platform, f.verdict), (want, verdict))
                if verdict == mi.LATER:
                    self.assertIn("modman.py add refuses it", f.detail)
        kinds, _ = self.run_on({"meta.yml": "name: T\nplatform: Universal\n", "manifest.yml": manifest})
        self.assertIn("cross-platform", kinds["UKMM package"].detail)

    def test_ukmm_wiiu(self):
        # neither converter takes a UKMM package, and the models in it can't be converted: "later", not "convert"
        manifest = "content:\n- Actor/Pack/A.sbactorpack\n- Model/B.sbfres\naoc: []\n"
        kinds, overall = self.run_on({"meta.yml": "platform: !Specific Wii U\n", "manifest.yml": manifest})
        f = kinds["UKMM package"]
        self.assertEqual(f.verdict, mi.LATER)
        self.assertEqual(overall, mi.VERDICTS[mi.LATER])
        self.assertIn("1 models/textures", f.detail)
        self.assertIn("deploy it in UKMM (Wii U mode)", f.detail)
        self.assertNotIn("convertible", f.detail)

    def test_ukmm_wiiu_all_mergeable(self):
        manifest = "content:\n- Actor/ActorInfo.product.sbyml\n"
        kinds, _ = self.run_on({"meta.yml": "platform: !Specific Wii U\n", "manifest.yml": manifest})
        self.assertIn("can be merged", kinds["UKMM package"].detail)
        self.assertEqual(kinds["UKMM package"].verdict, mi.MERGE)
        self.assertIn("install it in UKMM (Switch mode)", kinds["UKMM package"].detail)

    def test_packed_bnp(self):
        """A packed Switch BNP (info.json + logs/) works through modman add (tools/bnp.py installs it); a Wii U one
        still needs UKMM or BCML (made-up mods)."""
        cases = {"switch": {"01007EF00011E000/romfs/Pack/A.pack": b"x"}, "wiiu": {"content/Pack/A.pack": b"x"}}
        for platform, files in cases.items():
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as d:
                files = {"info.json": json.dumps({"name": "x", "platform": platform}), "logs/packs.json": "{}", **files}
                findings = mi.inspect(mi.open_source(make(Path(d), files)))
                if platform == "switch":
                    self.assertEqual([(f.kind, f.verdict) for f in findings],
                                     [("BCML mod (BNP)", mi.WORKS), ("Switch RomFS files", mi.WORKS)])
                    self.assertIn("tools/modman.py add installs it", findings[0].detail)
                    self.assertEqual(mi.overall(findings), "works as is")
                    continue
                self.assertEqual([(f.kind, f.platform, f.verdict) for f in findings],
                                 [("BCML mod (BNP)", platform, mi.MERGE)])
                self.assertIn("install it in UKMM or BCML (Switch mode)", findings[0].detail)
                self.assertEqual(mi.overall(findings), "works after merging in UKMM (Switch mode)")

    def test_unknown(self):
        _, overall = self.run_on({"readme.txt": "hi"})
        self.assertIn("doesn't look like", overall)

    def test_7z_is_refused_clearly(self):
        p = self.root / "m.bnp"
        p.write_bytes(b"7z\xbc\xaf\x27\x1c")
        with self.assertRaises(ValueError) as cm:
            mi.open_source(p)
        self.assertIn("python tools/modman.py add installs it", str(cm.exception))

    def test_cli(self):
        make(self.root / "mod", {"romfs/a.txt": "x"})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mi.main([str(self.root / "mod"), "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["overall"], mi.VERDICTS[mi.WORKS])

    def test_cli_name_outside_the_code_page_when_redirected(self):
        """Redirected stdout on Windows is the ANSI code page with strict errors: a mod named with a letter it lacks
        crashed with UnicodeEncodeError (made-up mod name)."""
        mod = make(self.root / "\u0141ukasz \u8865\u4e01", {"romfs/a.txt": "x"})
        raw = io.BytesIO()
        out = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
        with mock.patch.object(sys, "stdout", out):
            self.assertEqual(mi.main([str(mod)]), 0)
            out.flush()
        text = raw.getvalue().decode("cp1252")
        self.assertIn("Mod: ?ukasz ??", text)
        self.assertIn("Overall: works as is", text)


if __name__ == "__main__":
    unittest.main()
