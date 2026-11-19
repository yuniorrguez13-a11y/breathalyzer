"""Tests for tools/mod_pictures.py. Libraries, mods and pictures are made up in temp folders; the game's pack is a
made-up one built in memory (tests/test_layout_patch.py's builders): no game files."""
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
sys.path.insert(0, str(HERE))
import layout_patch as lp  # noqa: E402
import menu_bg as mb  # noqa: E402
import mod_pictures as mp  # noqa: E402
import modman  # noqa: E402
import test_layout_patch as tl  # noqa: E402
import wiiu_convert as wc  # noqa: E402


def png(w: int, h: int, rgba=(200, 40, 90, 255)) -> bytes:
    return mb.png_bytes(mb.Picture(w, h, bytearray(bytes(rgba) * (w * h))))


class Man:
    """The parts of modman.Manifest the pictures read."""

    def __init__(self, icon=None, background=None, title_picture=None):
        self.icon, self.background, self.title_picture = icon, background, title_picture

    @staticmethod
    def rel(path):
        return path.replace("\\", "/")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.lib = modman.Library(str(self.tmp / "lib"))

    def tearDown(self):
        self._tmp.cleanup()

    def mod(self, mid: str, name: str, files: dict | None = None) -> dict:
        root = self.tmp / "lib" / "mods" / mid
        root.mkdir(parents=True, exist_ok=True)
        for rel, data in (files or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        entry = {"id": mid, "name": name, "enabled": True}
        self.lib.mods.append(entry)
        return entry


class Sources(Base):
    def test_order_choice_icon_package_thumbnail_letter(self):
        e = self.mod("m", "Better Sprint", {"icon.png": png(8, 8), "pkg.png": png(8, 8), "thumbnail.png": png(8, 8)})
        man = Man(icon="icon.png")
        self.assertEqual(mp.source_for(self.lib, e, man, {"image": "pkg.png"}).kind, "icon")
        self.assertEqual(mp.source_for(self.lib, e, Man(), {"image": "pkg.png"}).kind, "package")
        self.assertEqual(mp.source_for(self.lib, e, Man(), {"image": "https://gamebanana.com/x.png"}).kind,
                         "thumbnail")   # (a web address is never used: S7)
        self.assertEqual(mp.source_for(self.lib, e, Man(), {"image": "../../x.png"}).kind, "thumbnail")
        pick = self.tmp / "mine.png"
        pick.write_bytes(png(16, 16))
        mp.choose(self.lib, "m", str(pick))
        s = mp.source_for(self.lib, e, man, {"image": "pkg.png"})
        self.assertEqual((s.kind, os.path.basename(s.path)), ("chosen", "chosen.png"))
        self.assertTrue(mp.clear_choice(self.lib, "m"))
        self.assertEqual(mp.source_for(self.lib, self.mod("n", "Nothing"), Man(), {}).kind, "letter")

    def test_a_bnp_picture_in_the_package(self):
        e = self.mod("m", "M", {".bnp/info.json": json.dumps({"name": "M", "image": "shots/a.png"}).encode(),
                                "shots/a.png": png(8, 8)})
        s = mp.source_for(self.lib, e, Man())
        self.assertEqual((s.kind, os.path.basename(s.path)), ("package", "a.png"))
        with mock.patch.object(modman, "_package_info", side_effect=AttributeError):
            self.assertEqual(mp.source_for(self.lib, e, Man()).kind, "package")

    def test_a_damaged_picture_is_skipped(self):
        e = self.mod("m", "M", {"icon.png": b"\x89PNG\r\n\x1a\nbroken", "thumbnail.png": png(8, 8)})
        self.assertEqual(mp.source_for(self.lib, e, Man(icon="icon.png"), {}).kind, "thumbnail")

    def test_choose_refuses_what_it_cant_show(self):
        bad = self.tmp / "notes.txt"
        bad.write_text("hi")
        with self.assertRaises(mp.PictureError):
            mp.choose(self.lib, "m", str(bad))
        with self.assertRaises(mp.PictureError):
            mp.choose(self.lib, "../x", str(bad))

    def test_letter_tile(self):
        a = mp.letter_tile("better sprint", 128)
        self.assertEqual((a.w, a.h, mp.letter_of("better sprint")), (128, 128, "B"))
        self.assertIn(b"\xff\xff\xff\xff", bytes(a.px))           # (a white letter)
        self.assertEqual(bytes(a.px[:4]), bytes(mp.letter_tile("better sprint", 128).px[:4]))
        self.assertEqual(mp.letter_of("  #1 mod"), "1")
        self.assertEqual(mp.letter_of("ñandu"), "?")


class Check(Base):
    def test_problems_and_notes(self):
        root = self.tmp / "m"
        root.mkdir()
        (root / "icon.png").write_bytes(png(64, 16))
        (root / "bg.png").write_bytes(b"not a picture at all")
        (root / "title.png").write_bytes(png(100, 100))
        problems, notes = mp.check(Man(icon="icon.png", background="bg.png", title_picture="title.png"), str(root))
        self.assertEqual(problems, [])   # (never a refusal: the next source or a letter tile shows)
        self.assertTrue(any("background" in n and "isn't a PNG" in n for n in notes))
        self.assertTrue(any("square" in n for n in notes))
        self.assertTrue(any("blurry" in n for n in notes))

    def test_jpg_without_pillow_is_a_note(self):
        root = self.tmp / "m"
        root.mkdir()
        (root / "icon.jpg").write_bytes(b"\xff\xd8\xff\xe0" + bytes(60))
        with mock.patch.object(mp, "_pillow", lambda: None):
            problems, notes = mp.check(Man(icon="icon.jpg"), str(root))
        self.assertEqual(problems, [])
        self.assertTrue(any("Pillow" in n for n in notes))


class Cache(Base):
    def test_made_once_and_again_when_the_source_changes(self):
        e = self.mod("m", "M", {"thumbnail.png": png(40, 30)})
        notes = mp.on_add(self.lib, e, str(self.tmp / "lib" / "mods" / "m"), Man())
        self.assertEqual(notes, ["picture: the mod's thumbnail.png"])
        d = self.tmp / "lib" / "pictures" / "m"
        big, small = d / "icon-128.png", d / "icon-64.png"
        self.assertEqual(mb.png_info(big.read_bytes())[:2], (128, 128))
        self.assertEqual(mb.png_info(small.read_bytes())[:2], (64, 64))
        info = json.loads((d / "pictures.json").read_text())
        self.assertEqual(info["source"], "thumbnail")
        t = big.stat().st_mtime_ns
        self.assertEqual(mp.refresh(self.lib, e, Man())["hash"], info["hash"])
        self.assertEqual(big.stat().st_mtime_ns, t)          # (nothing made again)
        (self.tmp / "lib" / "mods" / "m" / "thumbnail.png").write_bytes(png(40, 30, (0, 0, 255, 255)))
        self.assertNotEqual(mp.refresh(self.lib, e, Man())["hash"], info["hash"])
        self.assertEqual(mp.icon_path(self.lib, "m", 128), str(big))

    def test_a_letter_tile_isnt_kept_as_a_file(self):
        e = self.mod("m", "Better Sprint")
        self.assertEqual(mp.on_add(self.lib, e, str(self.tmp / "lib" / "mods" / "m"), Man()),
                         ["picture: a letter tile"])
        self.assertIsNone(mp.icon_path(self.lib, "m"))
        self.assertEqual(json.loads((self.tmp / "lib" / "pictures" / "m" / "pictures.json").read_text())["source"],
                         "letter")

    def test_background_is_small_dark_and_soft(self):
        e = self.mod("m", "M", {"bg.png": png(64, 36, (250, 250, 250, 255))})
        mp.refresh(self.lib, e, Man(background="bg.png"))
        pic = mb.read_png((self.tmp / "lib" / "pictures" / "m" / "bg-320.png").read_bytes())
        self.assertEqual((pic.w, pic.h), mp.BG)
        self.assertLess(max(pic.px[0::4]), 120)


def table(entries: dict) -> bytes:
    crcs = [(zlib.crc32(k.encode()) & 0xFFFFFFFF, v) for k, v in entries.items()]
    return wc.yaz0_compress(wc.rstb_build(crcs, []))


class W:
    def __init__(self, data: bytes, mod=None):
        self.data_, self.mod = data, mod

    def read(self):
        return self.data_


class Plan:
    def __init__(self, pack: bytes | None, rstb: bytes | None, mod=None):
        self.pack, self.rstb, self.mod = pack, rstb, mod

    def winner(self, path):
        if path == mp.BOOTUP:
            return W(self.pack, self.mod) if self.pack else None
        if path == mp.RSTB:
            return W(self.rstb) if self.rstb else None
        return None


class Deploy(Base):
    def setUp(self):
        super().setUp()
        self.mod("a", "Better Sprint", {"thumbnail.png": png(20, 20)})
        self.mod("b", "Linkle")
        self.pack = tl.bootup(tl.archive())
        raw = wc.yaz0_decompress(bytes(dict(wc.sarc_files(self.pack))[lp.COMMON]))[0]
        self.old = len(raw)
        self.plan = Plan(self.pack, table({"Layout/Common.blarc": self.old + 100}), "linkle")

    def test_off_until_turned_on(self):
        res = mp.deploy_files(self.lib, self.plan)
        self.assertEqual((list(res), res.in_game), ([], False))
        self.assertFalse(mp.load_settings(self.lib)["in_game"])
        self.assertIsNone(mp.hash_inputs(self.lib))     # (off: modman's deploy hash stays as without pictures)

    def test_hash_inputs_follow_the_pictures(self):
        mp.save_settings(self.lib, in_game=True)
        a = mp.hash_inputs(self.lib)
        self.assertEqual(json.loads(json.dumps(a)), a)
        pick = self.tmp / "p.png"
        pick.write_bytes(png(8, 8))
        mp.choose(self.lib, "b", str(pick))
        self.assertNotEqual(mp.hash_inputs(self.lib), a)
        b = mp.hash_inputs(self.lib)
        mp.save_settings(self.lib, rows=True)
        self.assertNotEqual(mp.hash_inputs(self.lib), b)

    def test_on_builds_over_the_winning_pack(self):
        mp.save_settings(self.lib, in_game=True)
        res = mp.deploy_files(self.lib, self.plan)
        self.assertEqual(res.attention, [])
        items = {p: (d, e) for p, d, e in res}
        self.assertEqual(set(items), {mp.ATLAS_JSON, mp.BOOTUP})
        data, entry = items[mp.BOOTUP]
        self.assertEqual(lp.archive_check(self.pack, data, {lp.COMMON}), [])   # (S1)
        raw = wc.yaz0_decompress(bytes(dict(wc.sarc_files(data))[lp.COMMON]))[0]
        self.assertEqual(entry[0], "Layout/Common.blarc")
        self.assertGreaterEqual(entry[1], len(raw) + 100)      # (the table in use's margin is kept)
        atlas = json.loads(items[mp.ATLAS_JSON][0])
        self.assertEqual(atlas["grids"][0]["tex"], mp.ICONS_TEX)
        self.assertEqual(atlas["cells"], {"a": {"icon": 0, "bg": -1}, "b": {"icon": 1, "bg": -1}})
        self.assertTrue(any("linkle" in n for n in res.notes))
        files = dict(wc.sarc_files(raw))
        self.assertIn("P_ModPanel_00", lp.picture_panes(bytes(files["blyt/OptionWindow_00.bflyt"])))
        again = mp.deploy_files(self.lib, self.plan)
        self.assertTrue(again.cached)
        self.assertEqual(dict((p, d) for p, d, _ in again)[mp.BOOTUP], data)
        kept = next((self.tmp / "lib" / "cache" / "pictures").glob("*.pack"))
        kept.write_bytes(kept.read_bytes()[:-10])   # (a damaged kept pack is never used)
        bad = mp.deploy_files(self.lib, self.plan)
        self.assertEqual(list(bad), [])
        self.assertIn("damaged", bad.attention[0])
        self.assertFalse(mp.deploy_files(self.lib, self.plan).cached)

    def test_rows_and_background(self):
        mp.save_settings(self.lib, in_game=True, rows=True, background=True)
        pick = self.tmp / "page.png"
        pick.write_bytes(png(32, 18))
        mp.choose(self.lib, "_mods-page", str(pick))
        res = mp.deploy_files(self.lib, self.plan)
        self.assertEqual(res.attention, [])
        items = {p: d for p, d, _ in res}
        atlas = json.loads(items[mp.ATLAS_JSON])
        self.assertEqual([g["tex"] for g in atlas["grids"]], [mp.ICONS_TEX, mp.BG_TEX])
        self.assertEqual(atlas["cells"]["_mods-page"], {"icon": -1, "bg": 0})
        raw = wc.yaz0_decompress(bytes(dict(wc.sarc_files(items[mp.BOOTUP]))[lp.COMMON]))[0]
        files = dict(wc.sarc_files(raw))
        self.assertIn("P_ModIcon_00", lp.picture_panes(bytes(files["blyt/PaOptionBtnL_00.bflyt"])))
        self.assertIn("P_ModBg_00", lp.picture_panes(bytes(files["blyt/OptionWindow_00.bflyt"])))

    def test_without_menu_bg_letter_tiles_in_rgba8(self):
        mp.save_settings(self.lib, in_game=True, background=True)
        mp.refresh(self.lib, self.lib.mods[0])   # (the cache made earlier stays usable)
        with mock.patch.object(mp, "mb", None):
            png_ = mp.png_bytes(mp.letter_tile("Z", 16))
            pic = mp._read_png(png_)
            self.assertEqual((pic.w, pic.h, bytes(pic.px)), (16, 16, bytes(mp.letter_tile("Z", 16).px)))
            res = mp.deploy_files(self.lib, self.plan)
        self.assertEqual(res.attention, [])
        self.assertTrue(any("menu_bg" in n for n in res.notes))
        data = dict((p, d) for p, d, _ in res)[mp.BOOTUP]
        raw = wc.yaz0_decompress(bytes(dict(wc.sarc_files(data))[lp.COMMON]))[0]
        bntx = bytes(dict(wc.sarc_files(raw))[lp.BNTX_NAME])
        from wiiu_model import nx_bntx
        tex = nx_bntx.read(bntx).textures[-1]
        self.assertEqual((tex.name, tex.format, tex.width), (mp.ICONS_TEX, lp.RGBA8, 512))
        cached = mb.read_png((self.tmp / "lib" / "pictures" / "a" / "icon-128.png").read_bytes())
        self.assertEqual(tex.levels[0][:128 * 4], bytes(cached.px[:128 * 4]))   # (cell 0 = mod a's picture)

    def test_a_check_failure_leaves_pictures_out(self):
        mp.save_settings(self.lib, in_game=True)
        with mock.patch.object(lp, "rebuild_bootup", side_effect=lp.PatchError("self-check failed: x")):
            res = mp.deploy_files(self.lib, self.plan)
        self.assertEqual((list(res), res.in_game), ([], False))
        self.assertIn("self-check failed", res.attention[0])
        res = mp.deploy_files(self.lib, Plan(None, None))
        self.assertIn("Bootup.pack", res.attention[0])


class Cli(Base):
    def run_cli(self, *args):
        import contextlib
        import io
        self.lib.save()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = mp.main([*args, "--library", str(self.tmp / "lib")])
        return code, out.getvalue()

    def test_on_off_status_refresh(self):
        self.mod("a", "Better Sprint", {"thumbnail.png": png(20, 20)})
        code, out = self.run_cli("on", "--rows")
        self.assertEqual((code, out.strip().splitlines()[0]), (0, "Pictures in the game: on at your next start "
                                                                   "(deploy, or Play in Mod Manager)."))
        self.assertIn("the game doesn't show these pictures yet", out)   # (until drawing lands: review finding)
        self.assertEqual(mp.load_settings(self.lib), {"in_game": True, "rows": True, "background": False})
        code, out = self.run_cli("status")
        self.assertIn("Better Sprint: the mod's thumbnail.png", out)
        self.assertEqual(self.run_cli("refresh", "better sprint")[0], 0)
        self.assertEqual(self.run_cli("refresh", "nothing")[0], 2)
        self.assertEqual(self.run_cli("off")[0], 0)
        self.assertFalse(mp.load_settings(self.lib)["in_game"])


if __name__ == "__main__":
    unittest.main()
