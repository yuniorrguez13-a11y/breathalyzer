"""Tests for tools/layout_patch.py. Every layout, texture file, archive and pack here is made up in memory (check_clean
keeps BFLYT/BNTX/SARC/Yaz0 data out of the repo): no game files."""
import random
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import layout_patch as lp  # noqa: E402
import wiiu_convert as wc  # noqa: E402
from wiiu_model import nx_bntx  # noqa: E402
from wiiu_model.neutral import Texture, TextureSet  # noqa: E402


def sec(magic: bytes, body: bytes) -> bytes:
    return magic + struct.pack("<I", 8 + len(body)) + body


def pane(magic: bytes, name: str, extra: bytes = b"", visible: bool = True) -> bytes:
    body = bytes([1 if visible else 0, 0, 255, 0]) + name.encode().ljust(24, b"\0") + bytes(8) \
        + struct.pack("<3f3f2f2f", 0, 0, 0, 0, 0, 0, 1, 1, 30, 30) + extra
    return sec(magic, body)


def pic(name: str, mat: int, visible: bool = True) -> bytes:
    return pane(b"pic1", name, bytes(16) + struct.pack("<HBB", mat, 1, 0) + struct.pack("<8f", 0, 0, 1, 0, 0, 1, 1, 1),
                visible)


def material(name: str, tex: int = 0) -> bytes:
    m = bytearray(0x54)
    m[0:0x1C] = name.encode().ljust(0x1C, b"\0")
    struct.pack_into("<I", m, 0x1C, 0x15)
    struct.pack_into("<h", m, 0x2C, tex)
    return bytes(m)


def flyt(texs: list[str], mats: list[bytes], tree: list[bytes]) -> bytes:
    secs = [sec(b"lyt1", bytes(20)), bytes(lp._strlist("txl1", texs)), bytes(lp._mat_section(mats))] + tree
    body = b"".join(secs)
    return b"FLYT\xff\xfe" + struct.pack("<HIIHH", 0x14, 0x08000000, 0x14 + len(body), len(secs), 0) + body


def rupee() -> bytes:
    icon = bytearray(pic("P_Icon_00", 0))
    struct.pack_into("<2f", icon, 68, 0.67, 0.67)   # (the game's rupee icon is drawn at 0.67)
    return flyt(["Rupee_00^s"], [material("P_Icon_00")],
                [pane(b"pan1", "RootPane"), sec(b"pas1", b""), bytes(icon), sec(b"pae1", b"")])


def option_window() -> bytes:
    return flyt(["FBLayout_00^r"], [material("P_Capture_00")],
                [pane(b"pan1", "RootPane"), sec(b"pas1", b""), pic("P_Capture_00", 0), pane(b"pan1", "N_Option_00"),
                 sec(b"pae1", b"")])


def bntx() -> bytes:
    rnd = random.Random(5)
    a = Texture("Game_00^s", 0x0B01, 8, 8, [bytes(rnd.getrandbits(8) for _ in range(8 * 8 * 4))])
    b = Texture("Game_01^t", 0x1A01, 16, 16, [bytes(rnd.getrandbits(8) for _ in range(4 * 4 * 8))])
    return nx_bntx.write(TextureSet("__Combined", [a, b], "model"))


def filler(seed: int, n: int) -> bytes:
    rnd = random.Random(seed)
    words = [bytes(rnd.getrandbits(8) for _ in range(rnd.randint(3, 9))) for _ in range(40)]
    out = bytearray()
    while len(out) < n:
        out += rnd.choice(words)
    return bytes(out[:n])


def archive() -> bytes:
    files = [("blyt/Rupee_00.bflyt", rupee()), ("blyt/OptionWindow_00.bflyt", option_window()),
             ("blyt/PaOptionBtnL_00.bflyt", option_window()), ("blyt/PaOptionBtnLevel_00.bflyt", option_window()),
             (lp.BNTX_NAME, bntx())]
    files += [(f"anim/Filler_{k:02d}.bflan", filler(k, 12000)) for k in range(8)]
    return wc.sarc_build(files, align={"bntx": 0x1000})


def bootup(arc: bytes) -> bytes:
    files = [("GameData/gamedata.ssarc", wc.yaz0_compress(filler(90, 3000))),
             ("GameData/savedataformat.ssarc", wc.yaz0_compress(filler(91, 2000))),
             (lp.COMMON, wc.yaz0_compress(arc, 0x1000)), ("Actor/Thing.bxml", filler(92, 700))]
    return wc.sarc_build(files)


ATLAS = lp.NewTexture("BzModIcons_00^l", lp.RGBA8, 16, 16, bytes(range(256)) * 4)


class Panes(unittest.TestCase):
    def test_add_picture_copies_the_icon_pane(self):
        src_pic, src_mat = lp.picture_source(rupee(), "P_Icon_00")
        d = lp.add_picture(option_window(), "P_ModPanel_00", src_pic, src_mat, "BzModIcons_00^l", (460, 20),
                           (256, 256))
        panes = lp.picture_panes(d)
        self.assertEqual(panes["P_ModPanel_00"], {"texture": "BzModIcons_00^l", "pos": (460.0, 20.0),
                                                  "size": (256.0, 256.0), "scale": (1.0, 1.0), "visible": False})
        self.assertEqual(panes["P_Capture_00"]["texture"], "FBLayout_00^r")   # (the others keep theirs)
        self.assertEqual(lp.pane_names(d), ["RootPane", "P_Capture_00", "N_Option_00", "P_ModPanel_00"])
        self.assertEqual(struct.unpack_from("<I", d, 0x0C)[0], len(d))
        d2 = lp.add_picture(d, "P_ModBg_00", src_pic, src_mat, "BzModBg_00^l", (0, 0), (1280, 720),
                            after="P_Capture_00")
        self.assertEqual(lp.pane_names(d2), ["RootPane", "P_Capture_00", "P_ModBg_00", "N_Option_00",
                                             "P_ModPanel_00"])
        with self.assertRaises(lp.PatchError):
            lp.add_picture(d, "P_ModPanel_00", src_pic, src_mat, "X", (0, 0), (1, 1))

    def test_unknown_layouts_are_refused(self):
        with self.assertRaises(lp.PatchError):
            lp.pane_names(b"FLAN" + bytes(40))
        with self.assertRaises(lp.PatchError):
            lp.picture_source(option_window(), "Nothing_00")


class Textures(unittest.TestCase):
    def test_new_textures_go_after_the_others_and_old_ones_keep_their_bytes(self):
        old = bntx()
        new, runs = lp.bntx_add_textures(old, [ATLAS])
        self.assertEqual(lp.texture_names(new), ["Game_00^s", "Game_01^t", "BzModIcons_00^l"])
        a, b = nx_bntx.read(old), nx_bntx.read(new)
        for t_old, t_new in zip(a.textures, b.textures):
            self.assertEqual((t_old.name, t_old.format, t_old.levels), (t_new.name, t_new.format, t_new.levels))
        self.assertEqual(b.textures[2].levels[0], ATLAS.data)
        for o, n, ln in runs:
            self.assertEqual(old[o:o + ln], new[n:n + ln])

    def test_no_new_texture_reads_the_same(self):
        old = bntx()
        same, _ = lp.bntx_add_textures(old, [])
        self.assertEqual([t.levels for t in nx_bntx.read(same).textures],
                         [t.levels for t in nx_bntx.read(old).textures])

    def test_refuses_a_name_twice_and_wrong_sizes(self):
        with self.assertRaises(lp.PatchError):
            lp.bntx_add_textures(bntx(), [lp.NewTexture("Game_00^s", lp.RGBA8, 16, 16, bytes(1024))])
        with self.assertRaises(ValueError):
            lp.bntx_add_textures(bntx(), [lp.NewTexture("New", lp.BC3, 16, 16, bytes(10))])
        with self.assertRaises(lp.PatchError):
            lp.bntx_add_textures(b"not a bntx" + bytes(64), [])


class Archives(unittest.TestCase):
    def test_changed_files_grow_in_place_and_the_rest_keeps_its_bytes(self):
        arc = archive()
        big = filler(77, 20000)
        new, runs, starts = lp.patch_archive(arc, {"blyt/Rupee_00.bflyt": big})
        self.assertEqual(lp.archive_check(arc, new, {"blyt/Rupee_00.bflyt"}), [])
        self.assertEqual(dict(wc.sarc_files(new))["blyt/Rupee_00.bflyt"], big)
        s_old, s_new = wc.sarc_parse(arc), wc.sarc_parse(new)
        for a, b in zip(s_old.nodes, s_new.nodes):   # (alignment kept: moves are multiples of 0x2000)
            self.assertEqual((s_new.data_offset + b.start - s_old.data_offset - a.start) % lp.SHIFT, 0)
        for o, n, ln in runs:
            self.assertEqual(arc[o:o + ln], new[n:n + ln])
        same, _, _ = lp.patch_archive(arc, {})
        self.assertEqual(same, arc)

    def test_yaz0_splice_reuses_the_old_groups(self):
        arc = archive()
        comp = wc.yaz0_compress(arc, 0x1000)
        raw, align, gs, go = lp.yaz0_index(comp)
        self.assertEqual((raw, align), (arc, 0x1000))
        new, runs = lp.patch_common(raw, {"blyt/OptionWindow_00.bflyt": [("P_ModPanel_00", ATLAS.name, (460, 20),
                                                                          (256, 256), None)]}, [ATLAS])
        coded = []
        real = lp._tokens

        def count(data, i, end):
            coded.append(end - i)
            return real(data, i, end)
        with mock.patch.object(lp, "_tokens", count):
            out = lp.yaz0_splice(comp, (gs, go), new, runs, align)
        self.assertEqual(wc.yaz0_decompress(out), (new, 0x1000))
        self.assertLess(sum(coded), len(new) // 2)   # (most of it was copied as the old groups)


class Bootup(unittest.TestCase):
    def patch(self, raw):
        return lp.patch_common(raw, {"blyt/OptionWindow_00.bflyt": [("P_ModPanel_00", ATLAS.name, (460, 20),
                                                                     (256, 256), None)]}, [ATLAS])

    def test_rebuild_keeps_every_other_entry_rule_s1(self):
        pack = bootup(archive())
        r = lp.rebuild_bootup(pack, self.patch)
        old, new = dict(wc.sarc_files(pack)), dict(wc.sarc_files(r.pack))
        self.assertEqual(sorted(old), sorted(new))
        for name in old:
            if name != lp.COMMON:
                self.assertEqual(old[name], new[name], name)
        raw = wc.yaz0_decompress(bytes(new[lp.COMMON]))[0]
        self.assertEqual(len(raw), r.common_size)
        files = dict(wc.sarc_files(raw))
        self.assertIn("P_ModPanel_00", lp.picture_panes(bytes(files["blyt/OptionWindow_00.bflyt"])))
        self.assertIn(ATLAS.name, lp.texture_names(bytes(files[lp.BNTX_NAME])))

    def test_a_difference_in_another_entry_stops_it(self):
        pack = bootup(archive())
        real = lp.patch_archive

        def broken(data, changes):
            out, runs, starts = real(data, changes)
            if lp.COMMON in changes:   # (the pack level: damage the save format entry)
                b = bytearray(out)
                s = wc.sarc_parse(out)
                nd = next(n for n in s.nodes if n.name == "GameData/savedataformat.ssarc")
                b[s.data_offset + nd.start + 20] ^= 0xFF
                out = bytes(b)
            return out, runs, starts
        with mock.patch.object(lp, "patch_archive", broken):
            with self.assertRaises(lp.PatchError) as cm:
                lp.rebuild_bootup(pack, self.patch)
        self.assertIn("savedataformat", str(cm.exception))

    def test_a_pack_without_the_layouts_is_refused(self):
        pack = wc.sarc_build([("Actor/Thing.bxml", b"x")])
        with self.assertRaises(lp.PatchError):
            lp.rebuild_bootup(pack, self.patch)


if __name__ == "__main__":
    unittest.main()
