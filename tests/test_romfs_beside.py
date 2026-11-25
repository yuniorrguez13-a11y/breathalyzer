"""The "beside" deploy (out/research/linkle-live-swap-design.md 2.4 and 6, phase 2 of the Linkle live swap): a mod's
player and armor files go in as SwLk_ copies next to the game's, its body members become new members of the pack in
use, and the size table gets max(his, hers). Made-up files in temp folders: no game files."""
import json
import os
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import modman as mm  # noqa: E402
import romfs_mods as rm  # noqa: E402
import wiiu_convert as wc  # noqa: E402
from test_romfs_mods import RSTB, _sizes, _table, make  # noqa: E402

PACK = "Pack/TitleBG.pack"


def crc(name: str) -> int:
    return zlib.crc32(name.encode()) & 0xFFFFFFFF


def sarc(files: dict) -> bytes:
    return wc.sarc_build(sorted(files.items()), align={"sbfres": 0x2000})


class Beside(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        game_pack = sarc({"Model/Link.sbfres": b"HIS BODY", "Model/Link.Tex.sbfres": b"HIS TEX",
                          "Actor/Pack/GameROMPlayer.sbactorpack": b"HIS PLAYER"})
        make(self.tmp / "extracted", {
            "romfs/" + PACK: game_pack,
            "romfs/Model/Armor_001.sbfres": b"his armor",
            "romfs/Actor/Pack/Armor_001_Head.sbactorpack": b"his head",
            "romfs/Sound/Resource/PlayerVoice.bars": b"his voice",
            "romfs/" + RSTB: _table({"Model/Armor_001.bfres": 500, "Model/Link.bfres": 900,
                                     "Actor/Pack/Armor_001_Head.bactorpack": 100}),
        })
        self.game = rm.GameIndex.scan(str(self.tmp / "extracted"))
        her_pack = sarc({"Model/Link.sbfres": b"HER BODY!", "Model/Link.Tex.sbfres": b"HIS TEX",
                         "Actor/Pack/GameROMPlayer.sbactorpack": b"HER PLAYER"})
        self.her = make(self.tmp / "the-linkle-mod", {
            "romfs/" + PACK: her_pack,
            "romfs/Model/Armor_001.sbfres": b"her armor",
            "romfs/Actor/Pack/Armor_001_Head.sbactorpack": b"her head",
            "romfs/Sound/Resource/PlayerVoice.bars": b"her voice",
            "romfs/" + RSTB: _table({"Model/Armor_001.bfres": 300, "Model/Link.bfres": 1200,
                                     "Actor/Pack/Armor_001_Head.bactorpack": 400}),
        })

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, prefix="SwLk_"):
        p = rm.plan([rm.Beside(str(self.her), prefix, "the-linkle-mod")], self.game)
        return p, rm.add_beside(p)

    def test_copies_land_beside_and_the_originals_stay_the_games(self):
        p, copies = self.plan()
        self.assertEqual(p.winner("Model/SwLk_Armor_001.sbfres").read(), b"her armor")
        self.assertEqual(p.winner("Actor/Pack/SwLk_Armor_001_Head.sbactorpack").read(), b"her head")
        for orig, his in (("Model/Armor_001.sbfres", b"his armor"), ("Sound/Resource/PlayerVoice.bars", b"his voice")):
            self.assertIsNone(p.winner(orig).mod, orig)       # (the game's own file)
            self.assertEqual(p.winner(orig).read(), his)
        self.assertIn({"from": "Model/Armor_001.sbfres", "to": "Model/SwLk_Armor_001.sbfres", "dev": "file",
                       "mod": "the-linkle-mod"}, copies)
        self.assertTrue(any(m == "romfs/Sound/Resource/PlayerVoice.bars" and r == rm.R_BESIDE_LEFT
                            for mod in p.mods for m, r in mod.skipped))

    def test_deployed_as_hard_links(self):
        p, _ = self.plan()
        out = self.tmp / "deploy" / "overlay"
        rm.deploy([], str(out), plan_=p)
        dst, src = out / "Model" / "SwLk_Armor_001.sbfres", self.her / "romfs" / "Model" / "Armor_001.sbfres"
        self.assertEqual(dst.read_bytes(), b"her armor")
        if rm._links_work(str(out)):
            self.assertTrue(os.path.samefile(dst, src))
        self.assertFalse((out / "Model" / "Armor_001.sbfres").exists())

    def test_body_copies_become_members_of_the_pack_in_use(self):
        p, copies = self.plan()
        w = p.winner(PACK)
        self.assertEqual(w.mod, rm.BESIDE_OWNER)
        members = dict(wc.sarc_files(w.read()))
        self.assertEqual(members["Model/Link.sbfres"], b"HIS BODY")            # (his stays)
        self.assertEqual(members["Model/SwLk_Link.sbfres"], b"HER BODY!")      # (hers beside it)
        self.assertNotIn("Model/SwLk_Link.Tex.sbfres", members)                # (the same as his: no copy)
        self.assertEqual(members["Actor/Pack/GameROMPlayer.sbactorpack"], b"HIS PLAYER")  # (resident: his)
        self.assertIn({"from": "Model/Link.sbfres", "to": "Model/SwLk_Link.sbfres", "dev": "archive", "pack": PACK,
                       "mod": "the-linkle-mod"}, copies)
        node = next(n for n in wc.sarc_parse(w.read()).nodes if n.name == "Model/SwLk_Link.sbfres")
        self.assertEqual((wc.sarc_parse(w.read()).data_offset + node.start) % 0x2000, 0)  # (aligned as his)

    def test_size_table_max_never_lowered_and_copies_get_hers(self):
        p, _ = self.plan()
        got = _sizes(p.winner(RSTB).read())
        self.assertEqual(got[crc("Model/Armor_001.bfres")], 500)              # max(his 500, hers 300): his
        self.assertEqual(got[crc("Actor/Pack/Armor_001_Head.bactorpack")], 400)  # max(100, 400): hers
        self.assertEqual(got[crc("Model/Link.bfres")], 1200)
        self.assertEqual(got[crc("Model/SwLk_Armor_001.bfres")], 300)         # the copies: hers
        self.assertEqual(got[crc("Actor/Pack/SwLk_Armor_001_Head.bactorpack")], 400)
        self.assertEqual(got[crc("Model/SwLk_Link.bfres")], 1200)

    def test_copy_without_an_entry_in_her_table_is_left_out(self):
        make(self.her, {"romfs/Model/Armor_002.sbfres": b"no entry"})
        p, copies = self.plan()
        self.assertIsNone(p.winner("Model/SwLk_Armor_002.sbfres"))
        self.assertFalse(any(c["from"] == "Model/Armor_002.sbfres" for c in copies))
        self.assertTrue(any("Armor_002" in w for w in p.warnings))

    def test_no_copies_when_the_size_table_cant_take_their_entries(self):
        with mock.patch.object(rm, "_sizes_into_table", side_effect=rm.DeployError("can't be read")):
            p, copies = self.plan()
        self.assertEqual(copies, [])
        self.assertEqual(p.beside, [])
        self.assertIsNone(p.winner("Model/SwLk_Armor_001.sbfres"))
        self.assertTrue(any("left out" in w for w in p.warnings))

    def test_a_new_folder_is_refused(self):
        make(self.tmp / "extracted", {})
        os.rename(self.tmp / "extracted" / "romfs" / "Actor", self.tmp / "extracted" / "romfs" / "Actor_gone")
        self.game = rm.GameIndex.scan(str(self.tmp / "extracted"))
        p, copies = self.plan()
        self.assertIsNone(p.winner("Actor/Pack/SwLk_Armor_001_Head.sbactorpack"))
        self.assertTrue(any(r == rm.R_BESIDE_FOLDER for mod in p.mods for _, r in mod.skipped))

    def test_no_copy_name_clashes_with_a_game_file(self):
        make(self.tmp / "extracted", {"romfs/Model/SwLk_Armor_001.sbfres": b"a game file of that name"})
        self.game = rm.GameIndex.scan(str(self.tmp / "extracted"))
        with self.assertRaises(rm.DeployError):
            self.plan()
        with self.assertRaises(rm.DeployError):
            self.plan("bad prefix")

    def test_a_pack_other_mods_merge_keeps_his_body_and_gets_her_copies(self):
        # BUG1: two other mods change TitleBG.pack too, so bnp merges it (generated:merged). Her pack was in that merge
        # (her body went in under his name) and the merged pack was written over the one with her copies. Now: the
        # merge leaves her out, and her copies go into the merged pack.
        import bnp
        his = {"Model/Link.sbfres": b"HIS BODY", "Model/Link.Tex.sbfres": b"HIS TEX",
               "Actor/Pack/GameROMPlayer.sbactorpack": b"HIS PLAYER"}
        a = make(self.tmp / "title-a", {"romfs/" + PACK: sarc({**his, "Actor/Pack/GameROMPlayer.sbactorpack": b"A"})})
        b = make(self.tmp / "title-b", {"romfs/" + PACK: sarc({**his, "Model/Link.Tex.sbfres": b"B TEX"})})
        her = str(self.her)
        mods = [("The Linkle Mod", her), ("Title A", str(a)), ("Title B", str(b))]
        hook = bnp.deploy_hook(mods, str(self.tmp / "extracted"), files=True, beside=[her])
        key = next(k for k in hook.files if k.lower() == PACK.lower())
        merged = dict(wc.sarc_files(hook.files[key]))
        self.assertEqual(merged["Model/Link.sbfres"], b"HIS BODY")    # (not hers: she isn't in the merge)
        self.assertEqual(merged["Actor/Pack/GameROMPlayer.sbactorpack"], b"A")
        self.assertEqual(merged["Model/Link.Tex.sbfres"], b"B TEX")
        self.assertFalse(any("Linkle" in n for n in hook.notes if "TitleBG" in n), hook.notes)
        sources = [rm.Beside(her, "SwLk_", "the-linkle-mod"), str(a), str(b)]
        attention = []
        p, copies = mm.plan_overlay(sources, self.game, hook.rstb, hook.files, attention)
        w = p.winner(PACK)
        self.assertEqual((w.mod, p.generated[w.target]["built_on"]), (rm.BESIDE_OWNER, mm.MERGED_OWNER))
        members = dict(wc.sarc_files(w.read()))
        self.assertEqual(members["Model/Link.sbfres"], b"HIS BODY")            # (his stays)
        self.assertEqual(members["Model/SwLk_Link.sbfres"], b"HER BODY!")      # (hers beside it)
        self.assertEqual(members["Actor/Pack/GameROMPlayer.sbactorpack"], b"A")   # (the other mods' changes stay)
        self.assertEqual(members["Model/Link.Tex.sbfres"], b"B TEX")
        self.assertIn({"from": "Model/Link.sbfres", "to": "Model/SwLk_Link.sbfres", "dev": "archive", "pack": PACK,
                       "mod": "the-linkle-mod"}, copies)
        self.assertEqual(attention, [])
        # without "beside" she is merged like any mod (the later mod wins a member): unchanged
        plain = bnp.deploy_hook(mods, str(self.tmp / "extracted"), files=True)
        self.assertEqual(dict(wc.sarc_files(plain.files[key]))["Model/Link.sbfres"], b"HER BODY!")

    def test_not_beside_is_unchanged(self):
        p = rm.plan([str(self.her)], self.game)
        self.assertEqual(p.winner("Model/Armor_001.sbfres").read(), b"her armor")
        self.assertEqual(p.beside, [])
        self.assertEqual(rm.add_beside(p), [])


class ModmanBeside(unittest.TestCase):
    """modman: a library entry's "beside" makes a Beside source; The Linkle Mod beside gives made.linkle "live" and
    linkle-live.json with every copy and its device; nothing else does (add-ons only: none, review N8)."""

    def test_sources(self):
        scans = [({"id": "the-linkle-mod", "beside": "SwLk_"}, "/lib/a/romfs", mock.Mock(romfs=True)),
                 ({"id": "other", "beside": "bad"}, "/lib/b/romfs", mock.Mock(romfs=True)),
                 ({"id": "plain"}, "/lib/c/romfs", mock.Mock(romfs=True))]
        out = mm.romfs_sources(scans, [])
        self.assertEqual(out[0], rm.Beside("/lib/a/romfs", "SwLk_", "the-linkle-mod"))
        self.assertEqual(out[1:], ["/lib/b/romfs", "/lib/c/romfs"])

    def test_live_map_and_made(self):
        lib = mock.Mock(mods=[{"id": "the-linkle-mod", "name": "The Linkle Mod"}, {"id": "hair", "name": "Hair"}],
                        root="/lib")
        copies = [{"from": "Model/Armor_001.sbfres", "to": "Model/SwLk_Armor_001.sbfres", "dev": "file",
                   "mod": "the-linkle-mod"},
                  {"from": "Model/Link.sbfres", "to": "Model/SwLk_Link.sbfres", "dev": "archive", "pack": PACK,
                   "mod": "the-linkle-mod"},
                  {"from": "Model/Armor_002.sbfres", "to": "Model/SwLk_Armor_002.sbfres", "dev": "file", "mod": "hair"}]
        with mock.patch.object(mm, "_feature_call", side_effect=lambda f, fn, mid, *a, **k: mid == "the-linkle-mod"):
            live = mm.linkle_live(lib, copies, [])
            none = mm.linkle_live(lib, copies[2:], [])
        self.assertIsNone(none)
        self.assertEqual(live["made"], {"body": "live", "start": "link", "map": mm.LINKLE_LIVE, "mod": "the-linkle-mod"})
        self.assertEqual(live["map"]["group"], "linkle")
        self.assertEqual([f["dev"] for f in live["map"]["files"]], ["file", "archive"])
        self.assertEqual(live["map"]["files"][1], {"from": "Model/Link.sbfres", "to": "Model/SwLk_Link.sbfres",
                                                   "dev": "archive", "pack": PACK})
        json.dumps(live)  # (written as JSON)
        self.assertIn(mm.ENV_FILE_REDIRECT, mm.MOD_ENV)  # (cleared at run unless this deploy sets it)

    def test_beside_changes_the_deploy_hash(self):
        """`modman beside` on or off makes Play deploy again; without it the hash is the same as before."""
        with tempfile.TemporaryDirectory() as t:
            mods = [{"id": "the-linkle-mod"}]
            lib = mock.Mock(install="", root=t, mods_dir=t)
            lib.enabled.return_value = mods
            with mock.patch.object(mm, "deploy_setting_values", return_value={}), \
                    mock.patch.object(mm, "_feature_call", return_value=None), \
                    mock.patch.object(mm, "feature", return_value=False):
                plain = mm.inputs_hash(lib)
                mods[0]["beside"] = "SwLk_"
                beside = mm.inputs_hash(lib)
                mods[0].pop("beside")
                again = mm.inputs_hash(lib)
        self.assertNotEqual(plain, beside)
        self.assertEqual(plain, again)


if __name__ == "__main__":
    unittest.main()
