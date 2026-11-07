"""Tests for tools/bnp.py (BCML packages) and its use in tools/modman.py. Every package, pack, parameter file, size
table and game file here is made up, built in code in temp folders. The .bnp archive tests build their archive with
7-Zip or Windows' tar.exe and are skipped (with the reason) when neither is there."""
import contextlib
import io
import json
import os
import struct
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import bnp  # noqa: E402
import modman as mm  # noqa: E402
import romfs_mods as rm  # noqa: E402
import wiiu_convert as wc  # noqa: E402

TID = "01007EF00011E000"
R = f"{TID}/romfs"
F32, INT, STR32, STR64, BUF_INT, BUF_BIN, BOOL, VEC3 = 1, 2, 7, 8, 13, 19, 0, 4
PLAYER_ASLIST = f"{R}/Pack/Title.pack//Actor/Pack/Inner.sbactorpack//Actor/ASList/Player.baslist"


def crc(s: str) -> int:
    return zlib.crc32(s.encode())


def write(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return root


def sarc(files: dict, align: int = 4) -> bytes:
    return bnp.sarc_build([(n, d if isinstance(d, bytes) else d.encode(), align) for n, d in files.items()])


def yaz0(data: bytes) -> bytes:
    return wc.yaz0_compress(data)


def pio(objs: dict | None = None, lists: dict | None = None) -> bnp.Pio:
    p = bnp.Pio()
    p.root = plist(objs, lists)
    return p


def plist(objs: dict | None = None, lists: dict | None = None) -> bnp.PList:
    pl = bnp.PList()
    for name, params in (objs or {}).items():
        pl.objs[crc(name)] = bnp.PObj({crc(k): v for k, v in params.items()})
    for name, sub in (lists or {}).items():
        pl.lists[crc(name)] = sub
    return pl


def f32(x: float) -> tuple[int, bytes]:
    return F32, struct.pack("<f", x)


def i32(x: int) -> tuple[int, bytes]:
    return INT, struct.pack("<i", x)


def rstb(crcs: dict, names: dict | None = None) -> bytes:
    out = b"RSTB" + struct.pack("<II", len(crcs), len(names or {}))
    for k in sorted(crcs):
        out += struct.pack("<II", k, crcs[k])
    for n, v in (names or {}).items():
        out += n.encode().ljust(128, b"\0") + struct.pack("<I", v)
    return out


def physics(mass: float) -> bytes:
    return bnp.aamp_write(pio({"Rigid": {"Mass": f32(mass), "Count": i32(2)}}))


def s64(text: str) -> tuple[int, bytes]:
    return STR64, text.encode()


def as_define(name: str, filename: str) -> dict:
    return {"Name": s64(name), "Filename": s64(filename)}


def cf_define(name: str, excepts: list[str] = (), posts: list[tuple[str, float]] = ()) -> bnp.PList:
    """A made-up blend rule: CFPre names the sequence, CFExcepts (Name_N) and CFPosts (CFPost_N: Name, Frame)."""
    objs = {"CFPre": {"Name": (STR32, name.encode())}}
    if excepts:
        objs["CFExcepts"] = {f"Name_{k}": (STR32, e.encode()) for k, e in enumerate(excepts)}
    post_objs = {f"CFPost_{k}": {"Name": (STR32, p.encode()), "Frame": f32(fr), "StartFrameRate": f32(-1)}
                 for k, (p, fr) in enumerate(posts)}
    return plist(objs, {"CFPosts": plist(post_objs)})


def as_list(anims: list[str] = (), defines: list[tuple[str, str]] = (), cfs: list[bnp.PList] = ()) -> bnp.PList:
    """A made-up animation list (.baslist) or the changes to one, as BCML's aslist.aamp holds them."""
    return plist(lists={
        "AddReses": plist({f"AddRes_{k}": {"Anim": s64(a)} for k, a in enumerate(anims)}),
        "ASDefines": plist({f"ASDefine_{k}": as_define(n, f) for k, (n, f) in enumerate(defines)}),
        "CFDefines": plist(lists={f"CFDefine_{k}": cf for k, cf in enumerate(cfs)})})


def game_aslist() -> bytes:
    return bnp.aamp_write(bnp.Pio(root=as_list(
        ["Hero_Animation", "Bike_Animation"], [("Wait", "Hero_Wait"), ("Run", "Hero_Run")],
        [cf_define("Jump", ["Freeze"], [("Wait", 5.0), ("Run", 4.0)]), cf_define("Land", [], [("", 6.0)])])))


def mod_aslist_diff() -> bnp.PList:
    """A new animation archive (and one the game has), a new sequence, a known one pointed at another file, a known
    blend rule with a new except and a changed and a new post, and a new blend rule."""
    return as_list(["Extra_Animation", "Hero_Animation"], [("Spin", "Extra_Spin"), ("Run", "Extra_Run")],
                   [cf_define("Jump", ["Freeze", "Spin"], [("Wait", 8.0), ("Spin", 3.0)]),
                    cf_define("Spin", ["Freeze"], [("Wait", 2.0)])])


def game_files() -> dict:
    """A made-up game RomFS: an actor pack (Yaz0) with parameter files, a title pack holding a nested actor pack (with
    an animation list), and a size table."""
    hero = sarc({"Actor/ActorLink/Hero.bxml": bnp.aamp_write(pio({"Link": {"Model": (STR32, b"Hero")}})),
                 "Actor/Physics/Hero.bphysics": physics(1.0), "Model/Hero.txt": "game model"})
    inner = sarc({"A.txt": "game a", "B.txt": "game b", "Actor/ASList/Player.baslist": game_aslist()})
    title = sarc({"Actor/Pack/Inner.sbactorpack": yaz0(inner), "Other.txt": "game other"})
    table = rstb({crc("Actor/Pack/Hero.bactorpack"): len(hero) + 0x200, crc("Actor/Physics/Hero.bphysics"): 400,
                  crc("Actor/Pack/Inner.bactorpack"): len(inner) + 0x200, crc("Model/Kept.txt"): 50,
                  crc("Actor/ASList/Player.baslist"): 1200, crc("Pack/Title.pack"): len(title) + 0x200},
                 {"Model/Named.txt": 70})
    return {"romfs/Actor/Pack/Hero.sbactorpack": yaz0(hero), "romfs/Pack/Title.pack": title,
            "romfs/Model/Kept.txt": "game kept", "romfs/Model/Named.txt": "game named",
            f"romfs/{rm.RSTB}": yaz0(table)}


def deepmerge_log(path: str, diff: bnp.PList) -> bytes:
    p = pio({"FileTable": {"File0": (STR32, path.encode())}})
    p.root.lists[crc(path)] = diff
    return bnp.aamp_write(p)


def package(**over) -> dict:
    """A made-up BCML package as files: shrunk packs (only the changed files), logs, two options and a choose-one
    group."""
    info = {"name": "Made Up Mod", "version": "1.2", "platform": "switch",
            "options": {"multi": [{"name": "Extra Bits", "folder": "Extra", "default": True},
                                  {"name": "Off By Default", "folder": "Off"}],
                        "single": [{"name": "Colour", "required": True,
                                    "options": [{"name": "Red", "folder": "Red"}, {"name": "Blue", "folder": "Blue"}]}]}}
    hero_path = f"{R}/Actor/Pack/Hero.sbactorpack//Actor/Physics/Hero.bphysics"
    files = {
        "info.json": json.dumps(info),
        f"{R}/Actor/Pack/Hero.sbactorpack": yaz0(sarc({"Model/Hero.txt": "mod model"})),
        f"{R}/Pack/Title.pack": sarc({"Actor/Pack/Inner.sbactorpack": yaz0(sarc({"A.txt": "mod a"}))}),
        f"{R}/Model/New.txt": "mod new",
        "logs/rstb.json": json.dumps({"Actor/Pack/Hero.bactorpack": 100, "Model/New.txt": 900, "Model/Kept.txt": 0}),
        "logs/packs.json": json.dumps({"Actor/Pack/Hero.bactorpack": f"{TID}\\romfs\\Actor\\Pack\\Hero.sbactorpack"}),
        "logs/deepmerge.aamp": deepmerge_log(hero_path, plist({"Rigid": {"Mass": f32(9.5), "Extra": i32(7)}})),
        "logs/aslist.aamp": deepmerge_log(PLAYER_ASLIST, mod_aslist_diff()),
        "logs/gamedata.yml": "made up: flags\n",
        f"options/Extra/{R}/Model/Extra.txt": "extra",
        f"options/Extra/{R}/Pack/Title.pack": sarc({"Other.txt": "option other"}),
        "options/Extra/logs/rstb.json": json.dumps({"Model/Extra.txt": 3000, "Model/New.txt": 800}),
        f"options/Off/{R}/Model/Off.txt": "off",
        f"options/Red/{R}/Model/Colour.txt": "red",
        f"options/Blue/{R}/Model/Colour.txt": "blue",
    }
    files.update(over)
    return files


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        return fn(*a, **kw), out.getvalue()


def big_diff(n: int = 400) -> bnp.PList:
    """A parameter change that grows its file by a few KB."""
    return plist({"Rigid": {f"P{k}": i32(k) for k in range(n)}})


def shared_aamp(n_obj: int, n_par: int) -> bytes:
    """A made-up AAMP whose objects all point at the same parameter headers (offsets are relative, so nothing in the
    format stops it): objects x parameters to read from a few KB."""
    type_b = b"xml\0"
    base = 0x30 + len(type_b)
    objs_at = base + 12
    params_at = objs_at + 8 * n_obj
    data_at = params_at + 8 * n_par
    out = bytearray(data_at + 4)
    struct.pack_into("<IHHHH", out, base, bnp.PARAM_ROOT, 0, 0, (objs_at - base) // 4, n_obj)
    for k in range(n_obj):
        at = objs_at + 8 * k
        struct.pack_into("<IHH", out, at, k, (params_at - at) // 4, n_par)
    for k in range(n_par):
        at = params_at + 8 * k
        struct.pack_into("<II", out, at, k, ((data_at - at) // 4) | (17 << 24))
    out[:0x30] = b"AAMP" + struct.pack("<11I", 2, 3, len(out), 0, len(type_b), 1, n_obj, n_par, 4, 0, 0)
    out[0x30:base] = type_b
    return bytes(out)


def overlapping_buffers(n_par: int, size: int) -> bytes:
    """A made-up AAMP with one object whose parameters each point at a different place in one data area filled with
    big counts: every one reads as a buffer of `size` bytes, overlapping the others."""
    type_b = b"xml\0"
    base = 0x30 + len(type_b)
    obj_at = base + 12
    params_at = obj_at + 8
    data_at = params_at + 8 * n_par
    out = bytearray(data_at + 4 * n_par + size + 4)
    struct.pack_into("<IHHHH", out, base, bnp.PARAM_ROOT, 0, 0, (obj_at - base) // 4, 1)
    struct.pack_into("<IHH", out, obj_at, 1, (params_at - obj_at) // 4, n_par)
    for k in range(n_par):
        at = params_at + 8 * k
        struct.pack_into("<II", out, at, k, ((data_at + 4 + 4 * k - at) // 4) | (19 << 24))
    for at in range(data_at, len(out) - 3, 4):
        struct.pack_into("<I", out, at, size)
    out[:0x30] = b"AAMP" + struct.pack("<11I", 2, 3, len(out), 0, len(type_b), 1, 1, n_par, 4, 0, 0)
    out[0x30:base] = type_b
    return bytes(out)


def slt(*blocks: dict) -> bytes:
    """7-Zip's `l -slt` output for made-up members."""
    text = "7-Zip 26.00\n\nListing archive: x.bnp\n\n--\nPath = x.bnp\nType = 7z\n\n----------\n"
    text += "\n".join("".join(f"{k} = {v}\n" for k, v in b.items()) for b in blocks)
    return text.replace("\n", "\r\n").encode()


def done(stdout: bytes = b"", code: int = 0):
    return mock.Mock(returncode=code, stdout=stdout, stderr=b"")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bnp-test-"))
        self.addCleanup(lambda: mm.remove_tree(str(self.tmp)))
        self.game = write(self.tmp / "game", game_files())

    def files_of(self, stage: Path) -> dict:
        return {p.relative_to(stage).as_posix(): p.read_bytes() for p in stage.rglob("*") if p.is_file()}


# ---------------------------------------------------------------- formats

class SizeTable(Base):
    def test_round_trip_and_rules(self):
        """The larger size wins, 0 removes an entry, names stay names, and the table reads back the same (Yaz0)."""
        t = bnp.Rstb(yaz0(rstb({crc("A.txt"): 100, crc("B.txt"): 200, crc("C.txt"): 5}, {"N.txt": 10})))
        changed = t.apply({"A.txt": 50, "B.txt": 300, "C.txt": 0, "D.txt": 7, "N.txt": 99})
        self.assertEqual(changed, 4)
        back = bnp.Rstb(t.to_bytes())
        self.assertEqual(back.get("A.txt"), 100)   # (smaller than the game's: the game's stays)
        self.assertEqual(back.get("B.txt"), 300)
        self.assertIsNone(back.get("C.txt"))
        self.assertEqual(back.get("D.txt"), 7)
        self.assertEqual(back.names, {"N.txt": 99})
        self.assertEqual(sorted(back.crc), sorted(back.crc.keys()))
        raw = t.to_bytes(compress=False)
        self.assertEqual(wc.rstb_order(raw), wc.LE)
        self.assertEqual(struct.unpack_from("<II", raw, 4), (3, 1))

    def test_merge_and_diff(self):
        self.assertEqual(bnp.merge_entries([{"a": 5, "b": 0, "c": 1}, {"a": 9, "b": 4, "c": 0}, {"a": 2}]),
                         {"a": 9, "b": 0, "c": 0})
        game = bnp.Rstb(rstb({1: 10, 2: 20}, {"N": 5}))
        mod = bnp.Rstb(rstb({1: 10, 2: 25, 3: 30}, {}))
        self.assertEqual(game.diff(mod), {2: 25, 3: 30, "N": 0})

    def test_wii_u_table_is_refused(self):
        be = b"RSTB" + struct.pack(">II", 1, 0) + struct.pack(">II", 1, 2)
        with self.assertRaises(bnp.BnpError):
            bnp.Rstb(be)

    def test_canonical_names(self):
        self.assertEqual(bnp.canon("Actor/Pack/X.sbactorpack", b"Yaz0..."), "Actor/Pack/X.bactorpack")
        self.assertEqual(bnp.canon("Pack/X.sarc", b"SARC"), "Pack/X.sarc")
        self.assertEqual(bnp.canon("Map/X.smubin", b"Yaz0", dlc=True), "Aoc/0010/Map/X.mubin")
        self.assertEqual(bnp.needed_size(1000, 400, 400, True), 1000)
        self.assertEqual(bnp.needed_size(1000, 400, 500, True), 1000 + 512 - 416)
        self.assertEqual(bnp.needed_size(1000, 400, 800, False), 2016)
        # (an animation list: the game's entry, its growth and what the game allocates for the new entries)
        self.assertEqual(bnp.needed_size(1000, 400, 440, False, 0xA0), 1000 + 64 + 0xA0)
        self.assertEqual(bnp.needed_size(1000, 400, 400, False, 0), 1000)

    def test_animation_list_size_counts_the_new_entries(self):
        """A list's size table entry grows by what the game allocates for each new entry, not in proportion to the
        file: 10 excepts whose names the list already has add 8 bytes each to the file but 0x28 to the game's memory,
        so the proportional rule (at the game's own entry/file ratio) fell short."""
        seqs = [f"Sequence_{k:03d}" for k in range(60)]
        game = as_list(["Hero_Animation"], [(s, s + "_file") for s in seqs],
                       [cf_define(s, [seqs[0]], [(seqs[1], 5.0)]) for s in seqs[:20]])
        game_len = len(bnp.aamp_write(bnp.Pio(root=game)))
        entry = game_len * 418 // 100
        cases = {"one archive": (as_list(["Extra_Animation"]), 0xA0),
                 "10 excepts": (as_list(cfs=[cf_define(s, [seqs[0], seqs[30 + k]]) for k, s in enumerate(seqs[:10])]),
                                10 * 0x28),
                 "10 posts": (as_list(cfs=[cf_define(s, [], [(seqs[40 + k], 3.0)]) for k, s in enumerate(seqs[:10])]),
                              10 * 0x98),
                 "a sequence and a rule": (as_list([], [("Spin", "Spin_file")],
                                                   [cf_define("Spin", ["Run"], [("A", 1)])]), 0x88 + 0x138 + 0x28 + 0x98)}
        for label, (diff, memory) in cases.items():
            with self.subTest(label):
                merged = bnp.aamp_read(bnp.aamp_write(bnp.Pio(root=game))).root
                bnp.aslist_merge(merged, diff)
                grown = len(bnp.aamp_write(bnp.Pio(root=merged))) - game_len
                self.assertEqual(bnp.aslist_growth(game, merged), memory)
                need = bnp.needed_size(entry, game_len, game_len + grown, False, bnp.aslist_growth(game, merged))
                self.assertGreaterEqual(need, entry + grown + memory)
        self.assertEqual(bnp.aslist_counts(game), (1, 60, 20, 20, 20))
        self.assertEqual(bnp.aslist_counts(bnp.PList()), (0, 0, 0, 0, 0))


class Aamp(unittest.TestCase):
    def test_round_trip_every_kind_of_value(self):
        p = pio({"Obj": {"F": f32(1.5), "I": i32(-3), "S": (STR32, b"name"), "B": (BOOL, struct.pack("<I", 1)),
                         "V": (VEC3, struct.pack("<3f", 1, 2, 3)), "Buf": (BUF_INT, struct.pack("<3i", 1, 2, 3)),
                         "Bin": (BUF_BIN, b"\x01\x02\x03"), "Same": (STR32, b"name")}},
                {"Child": plist({"Deep": {"X": i32(1)}}, {"Leaf": plist({"Y": {"Z": f32(2)}})})})
        data = bnp.aamp_write(p)
        self.assertEqual(data[:4], b"AAMP")
        back = bnp.aamp_read(data)
        self.assertEqual(back, p)
        self.assertEqual(struct.unpack_from("<3I", data, 0x18), (3, 3, 10))  # (lists, objects, params with the root)

    def test_deep_merge(self):
        base = plist({"Rigid": {"Mass": f32(1), "Count": i32(2)}}, {"L": plist({"O": {"a": i32(1)}})})
        diff = plist({"Rigid": {"Mass": f32(9)}, "New": {"b": i32(5)}}, {"L": plist({"O": {"c": i32(3)}})})
        bnp.aamp_merge(base, diff)
        self.assertEqual(base.objs[crc("Rigid")].params[crc("Mass")], f32(9))
        self.assertEqual(base.objs[crc("Rigid")].params[crc("Count")], i32(2))
        self.assertIn(crc("New"), base.objs)
        self.assertEqual(set(base.lists[crc("L")].objs[crc("O")].params), {crc("a"), crc("c")})

    def test_animation_list_merge(self):
        """BCML's aslist merge: entries matched by name, not by key; new ones numbered after the game's; nothing the
        game has is removed or moved; merging the same changes again changes nothing."""
        base = bnp.aamp_read(game_aslist()).root
        self.assertEqual(bnp.aslist_merge(base, mod_aslist_diff()), 7)
        anims = [o.params[crc("Anim")] for o in base.lists[crc("AddReses")].objs.values()]
        self.assertEqual(anims, [s64("Hero_Animation"), s64("Bike_Animation"), s64("Extra_Animation")])
        self.assertIn(crc("AddRes_2"), base.lists[crc("AddReses")].objs)
        defines = base.lists[crc("ASDefines")].objs
        self.assertEqual(list(defines), [crc(f"ASDefine_{k}") for k in range(3)])
        self.assertEqual(defines[crc("ASDefine_1")].params,
                         {crc(k): v for k, v in as_define("Run", "Extra_Run").items()})
        self.assertEqual(defines[crc("ASDefine_2")].params[crc("Name")], s64("Spin"))
        cfs = base.lists[crc("CFDefines")].lists
        self.assertEqual(list(cfs), [crc(f"CFDefine_{k}") for k in range(3)])
        jump = cfs[crc("CFDefine_0")]
        self.assertEqual([v for _, v in jump.objs[crc("CFExcepts")].params.values()], [b"Freeze", b"Spin"])
        posts = {p.params[crc("Name")][1]: p.params[crc("Frame")] for p in jump.lists[crc("CFPosts")].objs.values()}
        self.assertEqual(posts, {b"Wait": f32(8), b"Run": f32(4), b"Spin": f32(3)})
        self.assertEqual(cfs[crc("CFDefine_1")].objs[crc("CFPre")].params[crc("Name")], (STR32, b"Land"))
        self.assertEqual(cfs[crc("CFDefine_2")].objs[crc("CFPre")].params[crc("Name")], (STR32, b"Spin"))
        again = bnp.aamp_read(bnp.aamp_write(bnp.Pio(root=base))).root
        self.assertEqual(bnp.aslist_merge(again, mod_aslist_diff()), 0)
        self.assertEqual(again, base)
        # (keys the game skips: a new entry takes a free one, never one in use)
        gap = as_list(["A"])
        gap.lists[crc("AddReses")].objs[crc("AddRes_1")] = gap.lists[crc("AddReses")].objs.pop(crc("AddRes_0"))
        bnp.aslist_merge(gap, as_list(["B"]))
        self.assertEqual(set(gap.lists[crc("AddReses")].objs), {crc("AddRes_1"), crc("AddRes_2")})

    def test_animation_list_entries_without_a_name_are_named_not_matched_by_key(self):
        """A log entry without its name (BCML always writes it) is left out and described, never matched by its key:
        a log's keys are numbered among the logged entries, so ASDefine_0 there isn't the game's ASDefine_0."""
        base = bnp.aamp_read(game_aslist()).root
        before = bnp.aamp_read(game_aslist()).root
        jump_post = cf_define("Jump", [], [("Ignored", 1.0)])
        del jump_post.lists[crc("CFPosts")].objs[crc("CFPost_0")].params[crc("Name")]
        diff = plist(lists={"AddReses": plist({"AddRes_0": {"RetargetModel": s64("X")}}),
                            "ASDefines": plist({"ASDefine_0": {"Filename": s64("Z")}}),
                            "CFDefines": plist(lists={"CFDefine_0": plist({"CFExcepts": {"Name_0": (STR32, b"Run")}}),
                                                      "CFDefine_1": jump_post})})
        bad: list[str] = []
        self.assertEqual(bnp.aslist_merge(base, diff, bad), 0)
        self.assertEqual(base, before)   # (the game's ASDefine_0 keeps its Filename, CFDefine_0 (Jump) its excepts)
        self.assertEqual(sorted(bad), ["a blend post (CFPost) without its Name",
                                       "a blend rule (CFDefine) without its CFPre Name",
                                       "a sequence (ASDefine) without its Name",
                                       "an animation archive (AddRes) without its Anim"])
        self.assertEqual(bnp.aslist_merge(base, diff), 0)   # (no list to name them in: still left out)

    def test_damaged_files_are_refused(self):
        for data in (b"AAMP" + bytes(8), b"NOPE" + bytes(60), bnp.aamp_write(pio({"O": {"F": f32(1)}}))[:0x40]):
            with self.subTest(data=data[:8]), self.assertRaises(wc.FormatError):
                bnp.aamp_read(data)

    def test_shared_or_overlapping_parts_are_refused_quickly(self):
        """A few KB that point every object at the same parameters (objects x parameters to read), or every parameter
        at a big overlapping buffer, are refused instead of hanging `add` and filling the memory."""
        small = shared_aamp(4, 3)   # (sharing a little stays within what the file's size allows)
        self.assertEqual(len(bnp.aamp_read(small).root.objs), 4)
        for name, data in (("shared headers", shared_aamp(300, 300)), ("overlapping buffers",
                                                                       overlapping_buffers(250, 3000))):
            with self.subTest(name), self.assertRaises(wc.FormatError) as cm:
                bnp.aamp_read(data)
            self.assertIn("more than it holds", str(cm.exception))
        big = bnp.aamp_write(pio({f"O{k}": {f"P{j}": i32(j) for j in range(50)} for k in range(200)}))
        self.assertEqual(len(bnp.aamp_read(big).root.objs), 200)   # (a real file of the same size still reads)


class Packs(unittest.TestCase):
    def test_fill_keeps_the_mods_files_and_adds_the_games(self):
        game = bnp.Blob(sarc({"A.txt": "game a", "B.txt": "game b", "N.sbactorpack": yaz0(sarc({"x": "gx", "y": "gy"}))},
                             align=8))
        mod = bnp.Blob(yaz0(sarc({"A.txt": "mod a", "M.txt": "mod only", "N.sbactorpack": yaz0(sarc({"x": "mx"}))})))
        grown = []
        self.assertEqual(bnp.fill(mod, game, "", grown), 2)   # B.txt, and y inside N
        out = mod.data()
        self.assertEqual(out[:4], b"Yaz0")
        files = dict(wc.sarc_files(wc.yaz0_decompress(out)[0]))
        self.assertEqual(files["A.txt"], b"mod a")
        self.assertEqual(files["B.txt"], b"game b")
        self.assertEqual(files["M.txt"], b"mod only")
        inner = dict(wc.sarc_files(wc.yaz0_decompress(files["N.sbactorpack"])[0]))
        self.assertEqual(inner, {"x": b"mx", "y": b"gy"})
        self.assertEqual([g[0] for g in grown], ["N.sbactorpack"])

    def test_alignment_is_kept(self):
        data = bnp.sarc_build([("a", b"x" * 5, 4), ("b", b"y" * 3, 0x100), ("c", b"z", 0x2000)])
        s = wc.sarc_parse(data)
        for node in s.nodes:
            want = {"a": 4, "b": 0x100, "c": 0x2000}[node.name]
            self.assertEqual((s.data_offset + node.start) % want, 0, node.name)
        blob = bnp.Blob(data)
        self.assertEqual({n: a >= {"a": 4, "b": 0x100, "c": 0x2000}[n] for n, (_, a) in blob.open().items()},
                         {"a": True, "b": True, "c": True})

    def test_yaz0_head(self):
        raw = b"SARC" + bytes(100)
        self.assertEqual(bnp._yaz0_head(yaz0(raw)), b"SARC")
        self.assertTrue(bnp.Blob(yaz0(raw)).magic == b"SARC")


# ---------------------------------------------------------------- package, options, names

class Options(unittest.TestCase):
    def setUp(self):
        self.info = bnp.parse_info(json.dumps(json.loads(package()["info.json"])).encode())

    def names(self, picks=(), no=False):
        return [o.folder for o in bnp.choose(self.info, list(picks), no)]

    def test_defaults_and_required_group(self):
        self.assertEqual(self.names(), ["Extra", "Red"])  # (a required group gets its first option)

    def test_picks_by_name_or_folder_and_group_swap(self):
        self.assertEqual(self.names(["off by default", "Blue"]), ["Extra", "Off", "Blue"])
        self.assertEqual(self.names(["red", "Off"], no=True), ["Off", "Red"])  # (in the package's order)

    def test_no_defaults(self):
        self.assertEqual(self.names(["Red"], no=True), ["Red"])
        with self.assertRaises(bnp.BnpError) as cm:
            self.names(no=True)
        self.assertIn("needs one option of 'Colour'", str(cm.exception))

    def test_unknown_option_lists_the_real_ones(self):
        with self.assertRaises(bnp.BnpError) as cm:
            self.names(["Nope"])
        self.assertIn("'Extra Bits'", str(cm.exception))

    def test_bad_option_folder_is_refused(self):
        for folder in ("../x", "a/b", "con"):
            with self.subTest(folder=folder), self.assertRaises(bnp.BnpError):
                bnp.parse_info(json.dumps({"options": {"multi": [{"name": "x", "folder": folder}]}}).encode())


class Names(unittest.TestCase):
    def test_paths_that_escape_are_refused(self):
        for bad in ("../evil.txt", "a/../../evil", "/abs/x", "\\abs\\x", "C:/x", "c:x", "con.txt", "a/aux/b", "a/x:y",
                    "a/b. ", ".."):
            with self.subTest(name=bad), self.assertRaises(bnp.BnpError):
                bnp.check_name(bad)
        self.assertEqual(bnp.check_name("a\\b/./c.txt"), ["a", "b", "c.txt"])

    def test_listing_with_an_unsafe_name_or_a_link_is_refused(self):
        listings = {"escape": [("info.json", 2, "file", False), ("..\\..\\x.txt", 1, "file", False)],
                    "link": [("info.json", 2, "file", False), ("a", 0, "link", True)],
                    "twice": [("a/X.txt", 1, "file", False), ("a/x.TXT", 2, "file", False)],
                    "twice, same size": [("a/X.txt", 1, "file", False), ("a/x.TXT", 1, "file", False)]}
        for why, raw in listings.items():
            with self.subTest(why=why), \
                    mock.patch.object(bnp, "archive_tools", return_value=[("7z", "7z.exe")]), \
                    mock.patch.object(bnp, "_list_7z", return_value=raw), self.assertRaises(bnp.BnpError):
                bnp.list_archive("x.bnp")

    def test_a_name_starting_with_a_dash_is_refused(self):
        """A top folder named like an option (--use-compress-program=...) would make tar.exe run a command."""
        for top in ("--use-compress-program=made up;#", "-x", ".\\-x", "./--y"):
            raw = [(f"{top}/info.json", 2, "file", False), (f"{top}/logs/rstb.json", 2, "file", False)]
            with self.subTest(top=top), mock.patch.object(bnp, "archive_tools", return_value=[("tar", "tar.exe")]), \
                    mock.patch.object(bnp, "_list_tar", return_value=raw), \
                    mock.patch.object(bnp, "_run") as run, self.assertRaises(bnp.BnpError) as cm:
                bnp.peek("x.bnp", "archive")
            self.assertIn("starting with '-'", str(cm.exception))
            run.assert_not_called()   # (nothing is read from such an archive)

    def test_a_member_is_read_by_the_tool_that_listed_it_after_dashes(self):
        members = [bnp.Member("Mod/info.json", 5, False, ".\\Mod\\info.json")]
        for kind in ("7z", "tar"):
            with self.subTest(kind=kind), \
                    mock.patch.object(bnp, "archive_tools", return_value=[("7z", "7z.exe"), ("tar", "tar.exe")]), \
                    mock.patch.object(bnp, "_run", return_value=done(b"{...}")) as run:
                self.assertEqual(bnp.read_member("x.bnp", "Mod/info.json", listed=(members, kind)), b"{...}")
                argv = run.call_args.args[0]
                self.assertEqual(argv[0], f"{kind}.exe")
                self.assertEqual(argv[-3:], ["--", os.path.abspath("x.bnp"), ".\\Mod\\info.json"] if kind == "7z"
                                 else [os.path.abspath("x.bnp"), "--", ".\\Mod\\info.json"])
        # a failure (a bad CRC, say) is an error: no other tool is tried; nothing out, or less, is a failure too
        for result in (done(b"", 2), done(b""), done(b"{..")):
            with self.subTest(result=result.stdout), \
                    mock.patch.object(bnp, "archive_tools", return_value=[("7z", "7z.exe"), ("tar", "tar.exe")]), \
                    mock.patch.object(bnp, "_run", return_value=result) as run, self.assertRaises(bnp.BnpError):
                bnp.read_member("x.bnp", "Mod/info.json", listed=(members, "7z"))
            self.assertEqual(run.call_count, 1)

    def test_a_junction_listed_by_7zip_is_refused(self):
        """7-Zip lists a junction (a Windows reparse point) as Attributes = DL, with no Unix mode."""
        out = slt({"Path": "info.json", "Size": "2", "Attributes": "A", "Encrypted": "-"},
                  {"Path": "j", "Size": "0", "Attributes": "DL", "Encrypted": "-"})
        with mock.patch.object(bnp, "archive_tools", return_value=[("7z", "7z.exe")]), \
                mock.patch.object(bnp, "_run", return_value=done(out)), self.assertRaises(bnp.BnpError) as cm:
            bnp.list_archive("x.bnp")
        self.assertIn("holds a link (j)", str(cm.exception))
        plain = slt({"Path": "sub", "Size": "0", "Attributes": "D drwxr-xr-x", "Encrypted": "-"},
                    {"Path": "sub\\a.txt", "Size": "3", "Attributes": "A -rw-r--r--", "Encrypted": "-"})
        with mock.patch.object(bnp, "archive_tools", return_value=[("7z", "7z.exe")]), \
                mock.patch.object(bnp, "_run", return_value=done(plain)):
            members, _ = bnp.list_archive("x.bnp")
        self.assertEqual([(m.name, m.is_dir, m.raw) for m in members], [("sub", True, "sub"),
                                                                        ("sub/a.txt", False, "sub\\a.txt")])

    @unittest.skipUnless(os.name == "nt", "tar.exe's ANSI code page output is Windows'")
    def test_tar_names_beyond_ascii_say_to_install_7zip(self):
        """tar.exe lists names in the ANSI code page: one beyond ASCII is refused with what to do, not taken for a
        tampered archive or a name Windows can't use."""
        listing = "./info.json\r\n./options/Fée Ünï/x.txt\r\n".encode("mbcs", "replace")
        long = ("-rw-r--r--  0 0 0 2 Nov 07 12:00 ./info.json\r\n"
                "-rw-r--r--  0 0 0 1 Nov 07 12:00 ./options/Fée Ünï/x.txt\r\n").encode("mbcs", "replace")
        with mock.patch.object(bnp, "archive_tools", return_value=[("tar", "tar.exe")]), \
                mock.patch.object(bnp, "_run", side_effect=[done(listing), done(long)]), \
                self.assertRaises(bnp.BnpError) as cm:
            bnp.list_archive("x.bnp")
        text = str(cm.exception)
        self.assertIn("Install 7-Zip", text)
        self.assertIn("options/F", text)
        self.assertNotIn("didn't list", text)
        self.assertNotIn("�", text)   # (read in the code page it was written in, not as UTF-8)

    def test_no_tool_says_what_to_do(self):
        with mock.patch.object(bnp, "archive_tools", return_value=[]), self.assertRaises(bnp.BnpError) as cm:
            bnp.list_archive("x.bnp")
        self.assertIn("install 7-Zip", str(cm.exception))

    def test_extract_refuses_files_it_did_not_list(self):
        with tempfile.TemporaryDirectory() as d:
            dest = os.path.join(d, "out")

            def fake_run(argv, timeout=0):
                write(Path(dest), {"info.json": "{}", "surprise.txt": "x"})
                return mock.Mock(returncode=0, stdout=b"", stderr=b"")
            with mock.patch.object(bnp, "archive_tools", return_value=[("7z", "7z.exe")]), \
                    mock.patch.object(bnp, "_list_7z", return_value=[("info.json", 2, "file", False)]), \
                    mock.patch.object(bnp, "_run", side_effect=fake_run), self.assertRaises(bnp.BnpError) as cm:
                bnp.extract("x.bnp", dest)
            self.assertIn("didn't list", str(cm.exception))
            self.assertFalse(os.path.exists(dest))


# ---------------------------------------------------------------- installing

class Install(Base):
    def install(self, files=None, **kw):
        src = write(self.tmp / "pkg", files or package())
        info = bnp.peek(str(src), "folder")
        stage = self.tmp / "stage"
        res, _ = quiet(bnp.install, str(src), str(stage), str(self.game), info=info, **kw)
        return res, stage

    def test_layout_options_packs_deepmerge_and_size_table(self):
        res, stage = self.install()
        f = self.files_of(stage)
        self.assertEqual([o.folder for o in res.chosen], ["Extra", "Red"])
        self.assertEqual(f[f"{R}/Model/New.txt"], b"mod new")
        self.assertEqual(f[f"{R}/Model/Extra.txt"], b"extra")
        self.assertEqual(f[f"{R}/Model/Colour.txt"], b"red")
        self.assertNotIn(f"{R}/Model/Off.txt", f)
        # the actor pack: the mod's model, the game's other files, the physics change merged in
        hero = dict(wc.sarc_files(wc.yaz0_decompress(f[f"{R}/Actor/Pack/Hero.sbactorpack"])[0]))
        self.assertEqual(hero["Model/Hero.txt"], b"mod model")
        self.assertIn("Actor/ActorLink/Hero.bxml", hero)
        rigid = bnp.aamp_read(hero["Actor/Physics/Hero.bphysics"]).root.objs[crc("Rigid")].params
        self.assertEqual((rigid[crc("Mass")], rigid[crc("Count")], rigid[crc("Extra")]), (f32(9.5), i32(2), i32(7)))
        # the title pack: the option's file and the base's nested change, filled from the game
        title = dict(wc.sarc_files(f[f"{R}/Pack/Title.pack"]))
        self.assertEqual(title["Other.txt"], b"option other")
        inner = dict(wc.sarc_files(wc.yaz0_decompress(title["Actor/Pack/Inner.sbactorpack"])[0]))
        self.assertEqual((inner["A.txt"], inner["B.txt"]), (b"mod a", b"game b"))
        self.assertEqual(set(inner), {"A.txt", "B.txt", "Actor/ASList/Player.baslist"})
        self.assertEqual((res.packs_filled, res.deepmerged, res.aslisted), (2, 1, 1))
        # the animation list: the game's (filled into the nested pack) with the mod's changes merged in
        merged = bnp.aamp_read(inner["Actor/ASList/Player.baslist"]).root
        want = bnp.aamp_read(game_aslist()).root
        bnp.aslist_merge(want, mod_aslist_diff())
        self.assertEqual(merged, want)
        self.assertEqual(len(merged.lists[crc("AddReses")].objs), 3)
        # the size table: the game's, with the mod's sizes (never smaller), 0 removing an entry, the rebuilt packs
        table = bnp.Rstb(f[f"{R}/{rm.RSTB}"])
        game = bnp.Rstb((self.game / "romfs" / rm.RSTB).read_bytes())
        self.assertEqual(table.get("Model/New.txt"), 900)        # (base 900, option 800: the larger)
        self.assertEqual(table.get("Model/Extra.txt"), 3000)
        self.assertIsNone(table.get("Model/Kept.txt"))
        self.assertGreaterEqual(table.get("Actor/Pack/Hero.bactorpack"), game.get("Actor/Pack/Hero.bactorpack"))
        self.assertEqual(table.names, {"Model/Named.txt": 70})
        stored = json.loads(f[".bnp/rstb.json"])
        self.assertEqual(stored["Model/Kept.txt"], 0)
        # (the grown animation list and every pack around it get sizes that hold them)
        vanilla = len(game_aslist())
        grown = len(inner["Actor/ASList/Player.baslist"])
        self.assertGreater(grown, vanilla)
        # (the game's entry, the growth, and the memory for 1 archive, 1 sequence, 1 rule, 2 posts and 2 excepts)
        self.assertEqual(table.get("Actor/ASList/Player.baslist"),
                         1200 + bnp.round32(grown - vanilla) + 0xA0 + 0x88 + 0x138 + 2 * 0x98 + 2 * 0x28)
        self.assertGreaterEqual(table.get("Pack/Title.pack"), len(f[f"{R}/Pack/Title.pack"]))
        self.assertGreaterEqual(table.get("Actor/Pack/Inner.bactorpack"),
                                len(wc.yaz0_decompress(title["Actor/Pack/Inner.sbactorpack"])[0]))
        # what wasn't merged is named, nothing silently dropped; the logs are kept
        meta = json.loads(f[".bnp/bnp.json"])
        text = " | ".join(meta["not_merged"])
        self.assertNotIn("aslist", text)
        self.assertEqual(meta["aslist_merged"], 1)
        self.assertIn("1 animation list(s) (aslist.aamp)", "\n".join(res.summary()))
        self.assertIn("logs/gamedata.yml (game data flags)", text)
        self.assertEqual(meta["not_merged"], bnp.stored_problems(str(stage)))
        for log in ("logs/rstb.json", "logs/aslist.aamp", "options/Extra/logs/rstb.json"):
            self.assertIn(f".bnp/{log}", f)
        # the library copy deploys like any Switch mod (romfs_mods never sees .bnp/)
        mod = rm.load_mod(str(stage))
        self.assertNotIn(".bnp", {e.member.split("/")[0] for e in mod.entries})

    def test_options_off_and_picked(self):
        res, stage = self.install(picks=["Blue", "Off"], no_defaults=True)
        f = self.files_of(stage)
        self.assertEqual([o.folder for o in res.chosen], ["Off", "Blue"])
        self.assertEqual(f[f"{R}/Model/Colour.txt"], b"blue")
        self.assertNotIn(f"{R}/Model/Extra.txt", f)
        self.assertEqual(dict(wc.sarc_files(f[f"{R}/Pack/Title.pack"]))["Other.txt"], b"game other")

    def test_wii_u_package_and_missing_game_are_refused(self):
        with self.assertRaises(bnp.BnpError) as cm:
            self.install(package(**{"info.json": json.dumps({"name": "W", "platform": "wiiu"})}))
        self.assertIn("Wii U", str(cm.exception))
        mm.remove_tree(str(self.game / "romfs"))
        with self.assertRaises(bnp.BnpError):
            mm.remove_tree(str(self.tmp / "stage"))
            self.install()

    def test_a_deepmerge_for_a_file_nobody_has_is_named(self):
        bad = f"{R}/Actor/Pack/Nobody.sbactorpack//Actor/Physics/X.bphysics"
        res, _ = self.install(package(**{"logs/deepmerge.aamp": deepmerge_log(bad, plist({"O": {"a": i32(1)}}))}))
        self.assertEqual(res.deepmerged, 0)
        self.assertTrue(any("1 file(s) not merged" in x and "X.bphysics" in x for x in res.not_merged), res.not_merged)

    def test_animation_lists_from_the_options_and_lists_nobody_has(self):
        """An option's aslist.aamp merges after the base's (into the same list); a change for a list the game's pack
        lacks is named, not made up from the changes alone; a damaged log is named."""
        opt = as_list(["Option_Animation"], [("Run", "Option_Run")])
        nobody = PLAYER_ASLIST.replace("Player.baslist", "Nobody.baslist")
        res, stage = self.install(package(**{
            "options/Extra/logs/aslist.aamp": deepmerge_log(PLAYER_ASLIST, opt),
            "options/Red/logs/aslist.aamp": deepmerge_log(nobody, as_list(["X"]))}))
        self.assertEqual(res.aslisted, 2)
        title = dict(wc.sarc_files((stage / R / "Pack/Title.pack").read_bytes()))
        inner = dict(wc.sarc_files(wc.yaz0_decompress(title["Actor/Pack/Inner.sbactorpack"])[0]))
        self.assertNotIn("Actor/ASList/Nobody.baslist", inner)
        merged = bnp.aamp_read(inner["Actor/ASList/Player.baslist"]).root
        anims = [o.params[crc("Anim")][1] for o in merged.lists[crc("AddReses")].objs.values()]
        self.assertEqual(anims, [b"Hero_Animation", b"Bike_Animation", b"Extra_Animation", b"Option_Animation"])
        run = next(o for o in merged.lists[crc("ASDefines")].objs.values() if o.params[crc("Name")] == s64("Run"))
        self.assertEqual(run.params[crc("Filename")], s64("Option_Run"))   # (the option's, merged last, wins)
        text = " | ".join(res.not_merged)
        self.assertIn("options/Red/logs/aslist.aamp (animation lists): 1 file(s) not merged "
                      "(Actor/ASList/Nobody.baslist: its pack has no such file)", text)
        mm.remove_tree(str(self.tmp / "pkg"))
        mm.remove_tree(str(stage))
        res, _ = self.install(package(**{"logs/aslist.aamp": b"AAMP" + bytes(60)}))
        self.assertEqual(res.aslisted, 0)
        self.assertIn("logs/aslist.aamp (animation lists) can't be read", " | ".join(res.not_merged))

    def test_an_animation_list_log_that_changes_nothing_leaves_the_game_pack_out(self):
        """A log that only repeats the game's entries (BCML logs often do) for a pack the mod doesn't ship: nothing is
        merged, the game's pack isn't copied into the mod, and no size table is made for it."""
        info = json.dumps({"name": "Repeats", "version": "1", "platform": "switch"})
        same = as_list(["Hero_Animation"], [("Wait", "Hero_Wait")], [cf_define("Jump", ["Freeze"], [("Wait", 5.0)])])
        res, stage = self.install({"info.json": info, f"{R}/Model/New.txt": "x",
                                   "logs/aslist.aamp": deepmerge_log(PLAYER_ASLIST, same)})
        self.assertEqual((res.aslisted, res.not_merged, res.packs, res.rstb_changed), (0, [], [], 0))
        self.assertEqual(set(self.files_of(stage)) - {f for f in self.files_of(stage) if f.startswith(".bnp/")},
                         {f"{R}/Model/New.txt"})
        self.assertEqual(json.loads((stage / ".bnp" / "rstb.json").read_text()), {})
        self.assertNotIn("animation list", "\n".join(res.summary()))

    def test_animation_list_entries_without_a_name_need_attention(self):
        """A damaged aslist.aamp entry (no name) is named under needs attention; the log's other changes merge."""
        diff = as_list(["Extra_Animation"])
        diff.lists[crc("ASDefines")].objs[crc("ASDefine_0")] = bnp.PObj({crc("Filename"): s64("Z")})
        res, stage = self.install(package(**{"logs/aslist.aamp": deepmerge_log(PLAYER_ASLIST, diff)}))
        self.assertEqual(res.aslisted, 1)
        title = dict(wc.sarc_files((stage / R / "Pack/Title.pack").read_bytes()))
        inner = dict(wc.sarc_files(wc.yaz0_decompress(title["Actor/Pack/Inner.sbactorpack"])[0]))
        merged = bnp.aamp_read(inner["Actor/ASList/Player.baslist"]).root
        self.assertEqual(len(merged.lists[crc("AddReses")].objs), 3)
        self.assertEqual(merged.lists[crc("ASDefines")], bnp.aamp_read(game_aslist()).root.lists[crc("ASDefines")])
        self.assertIn("logs/aslist.aamp (animation lists): 1 entry without a name not merged "
                      "(Actor/ASList/Player.baslist: a sequence (ASDefine) without its Name)",
                      " | ".join(res.not_merged))

    def test_packs_changed_only_by_deepmerge_get_sizes(self):
        """A pack the mod doesn't ship (copied from the game) and a nested pack fill() added nothing to grow with the
        parameter change: each gets a size table entry that holds it, and the deploy check knows the pack."""
        info = json.dumps({"name": "Params Only", "version": "1", "platform": "switch"})
        hero = f"{R}/Actor/Pack/Hero.sbactorpack//Actor/Physics/Hero.bphysics"
        res, stage = self.install({"info.json": info, f"{R}/Model/New.txt": "x",
                                   "logs/deepmerge.aamp": deepmerge_log(hero, big_diff())})
        game = bnp.Rstb((self.game / "romfs" / rm.RSTB).read_bytes())
        table = bnp.Rstb((stage / R / rm.RSTB).read_bytes())
        grown = len(wc.yaz0_decompress((stage / R / "Actor/Pack/Hero.sbactorpack").read_bytes())[0])
        vanilla = len(wc.yaz0_decompress((self.game / "romfs/Actor/Pack/Hero.sbactorpack").read_bytes())[0])
        self.assertGreater(grown, vanilla + 3000)
        self.assertGreaterEqual(table.get("Actor/Pack/Hero.bactorpack"), grown)
        self.assertGreater(table.get("Actor/Pack/Hero.bactorpack"), game.get("Actor/Pack/Hero.bactorpack"))
        self.assertIn("Actor/Pack/Hero.sbactorpack", res.packs)
        self.assertIn("Actor/Pack/Hero.sbactorpack", json.loads((stage / ".bnp/bnp.json").read_bytes())["packs"])
        mm.remove_tree(str(self.tmp / "pkg"))
        mm.remove_tree(str(stage))
        inner = f"{R}/Pack/Title.pack//Actor/Pack/Inner.sbactorpack//Actor/Physics/Inner.bphysics"
        res, stage = self.install({"info.json": info, f"{R}/Pack/Title.pack": sarc({"Other.txt": "mod other"}),
                                   "logs/deepmerge.aamp": deepmerge_log(inner, big_diff())})
        title = dict(wc.sarc_files((stage / R / "Pack/Title.pack").read_bytes()))
        grown = len(wc.yaz0_decompress(title["Actor/Pack/Inner.sbactorpack"])[0])
        table = bnp.Rstb((stage / R / rm.RSTB).read_bytes())
        self.assertEqual(res.deepmerged, 1)
        self.assertGreaterEqual(table.get("Actor/Pack/Inner.bactorpack"), grown)
        self.assertGreater(table.get("Actor/Pack/Inner.bactorpack"), game.get("Actor/Pack/Inner.bactorpack"))

    def test_sizes_that_change_nothing_ship_no_size_table(self):
        """A size table in the overlay turns off Savage Wind's own size fixes, so none is shipped for nothing."""
        res, stage = self.install({"info.json": json.dumps({"name": "Small", "version": "1"}),
                                   f"{R}/Model/Kept.txt": "x", f"{R}/{rm.RSTB}": yaz0(rstb({})),
                                   "logs/rstb.json": json.dumps({"Model/Kept.txt": 10, "Model/Named.txt": 70})})
        self.assertEqual((res.rstb_entries, res.rstb_changed), (2, 0))
        self.assertFalse((stage / R / rm.RSTB).exists())   # (the package's own is left out too)
        self.assertEqual(json.loads((stage / ".bnp/rstb.json").read_bytes()), {})
        self.assertIn("none of them bigger than the game's", "\n".join(res.summary()))
        # only what changes the game's table is kept
        res, stage = self.install({"info.json": json.dumps({"name": "Mixed", "version": "1"}),
                                   "logs/rstb.json": json.dumps({"Model/Kept.txt": 10, "Model/New.txt": 9})})
        self.assertEqual(json.loads((stage / ".bnp/rstb.json").read_bytes()), {"Model/New.txt": 9})
        self.assertEqual(bnp.Rstb((stage / R / rm.RSTB).read_bytes()).get("Model/New.txt"), 9)

    def test_read_only_files_and_errors_while_installing(self):
        """7-Zip brings back the read-only setting of the files it extracts: they are installed all the same. A file
        error while installing is the player's message, not a traceback."""
        def unzip(src, dest):
            write(Path(dest), package())
            for p in Path(dest).rglob("*"):
                if p.is_file():
                    os.chmod(p, 0o444)
        info = bnp.parse_info(package()["info.json"].encode())
        stage = self.tmp / "mods" / ".adding-x"
        res, _ = quiet(bnp.add, "x.zip", "zip", str(stage), str(self.game), info, unzip=unzip)
        self.assertEqual(res.packs_filled, 2)
        self.assertFalse(Path(str(stage) + ".bnp-raw").exists())
        with mock.patch.object(bnp, "install", side_effect=PermissionError(5, "Access is denied")), \
                self.assertRaises(bnp.BnpError) as cm:
            quiet(bnp.add, "x.zip", "zip", str(self.tmp / "mods" / ".adding-y"), str(self.game), info, unzip=unzip)
        self.assertIn("Not added: installing x.zip failed", str(cm.exception))
        self.assertFalse((self.tmp / "mods" / ".adding-y.bnp-raw").exists())

    def test_a_work_folder_that_stays_is_said(self):
        info = bnp.parse_info(package()["info.json"].encode())
        with mock.patch.object(bnp, "_remove", return_value=False):
            res, _ = quiet(bnp.add, "x.zip", "zip", str(self.tmp / "mods" / ".adding-x"), str(self.game), info,
                           unzip=lambda src, dest: write(Path(dest), package()))
        self.assertTrue(any("couldn't be removed" in n and ".adding-x.bnp-raw" in n for n in res.notes), res.notes)

    def test_the_users_own_folder_is_never_written(self):
        src = write(self.tmp / "pkg", package())
        before = {p: p.read_bytes() for p in src.rglob("*") if p.is_file()}
        quiet(bnp.install, str(src), str(self.tmp / "stage"), str(self.game), info=bnp.peek(str(src), "folder"))
        self.assertEqual({p: p.read_bytes() for p in src.rglob("*") if p.is_file()}, before)


@unittest.skipUnless(bnp.archive_tools(), "neither 7-Zip nor Windows' tar.exe is here to build a made-up .bnp")
class Archive(Base):
    def make_bnp(self, files: dict, tool: tuple[str, str], read_only: bool = False) -> Path:
        src = write(self.tmp / "bnp-src", files)
        if read_only:
            for p in src.rglob("*"):
                if p.is_file():
                    os.chmod(p, 0o444)
        out = self.tmp / "made.bnp"
        kind, exe = tool
        argv = ([exe, "a", "-t7z", "-bd", str(out), "."] if kind == "7z"
                else [exe, "--format", "7zip", "-cf", str(out), "."])
        r = bnp.subprocess.run(argv, cwd=src, capture_output=True)
        if r.returncode:
            self.skipTest(f"{kind} couldn't build a test archive: {r.stderr[-200:]!r}")
        mm.remove_tree(str(src))
        return out

    def test_each_tool_lists_extracts_and_installs(self):
        for tool in bnp.archive_tools():
            with self.subTest(tool=tool[0]):
                with mock.patch.object(bnp, "archive_tools", return_value=[tool]):
                    path = self.make_bnp(package(), tool)
                    self.assertTrue(bnp.is_archive(str(path)))
                    info = bnp.peek(str(path), "archive")
                    self.assertEqual((info.name, info.version), ("Made Up Mod", "1.2"))
                    stage = self.tmp / "lib" / "mods" / ".adding-x"
                    res, _ = quiet(bnp.add, str(path), "archive", str(stage), str(self.game), info)
                    self.assertEqual(res.packs_filled, 2)
                    self.assertFalse(Path(str(stage) + ".bnp-raw").exists())  # (the work folder is gone)
                    self.assertIn(f"{R}/Model/Extra.txt", self.files_of(stage))
                mm.remove_tree(str(self.tmp / "lib"))
                path.unlink()

    def test_mod_inspect_reads_a_bnp(self):
        import mod_inspect as mi
        path = self.make_bnp(package(), bnp.archive_tools()[0])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mi.main([str(path), "--json"]), 0)
        parts = json.loads(out.getvalue())["parts"]
        self.assertIn(("BCML mod (BNP)", "works"), [(p["kind"], p["verdict"]) for p in parts])

    def test_a_top_folder_named_like_an_option_is_refused(self):
        files = {f"-x/{k}": v for k, v in package().items()}
        for tool in bnp.archive_tools():
            with self.subTest(tool=tool[0]), mock.patch.object(bnp, "archive_tools", return_value=[tool]):
                path = self.make_bnp(files, tool)
                with self.assertRaises(bnp.BnpError) as cm:
                    bnp.peek(str(path), "archive")
                self.assertIn("starting with '-'", str(cm.exception))
                path.unlink()

    def test_read_only_files_in_the_archive(self):
        """7-Zip extracts them read-only: the install moves and replaces them all the same."""
        for tool in bnp.archive_tools():
            with self.subTest(tool=tool[0]), mock.patch.object(bnp, "archive_tools", return_value=[tool]):
                path = self.make_bnp(package(), tool, read_only=True)
                stage = self.tmp / "lib" / "mods" / ".adding-x"
                res, _ = quiet(bnp.add, str(path), "archive", str(stage), str(self.game),
                               bnp.peek(str(path), "archive"))
                self.assertEqual(res.packs_filled, 2)
                self.assertFalse(Path(str(stage) + ".bnp-raw").exists())
                mm.remove_tree(str(self.tmp / "lib"))
                path.unlink()

    @unittest.skipUnless({"7z", "tar"} <= {k for k, _ in bnp.archive_tools()}, "needs both 7-Zip and tar.exe")
    def test_names_tar_spells_with_dot_slash_read_by_7zip(self):
        """tar.exe stores ./info.json: 7-Zip reads it by that name, not by info.json (which gives nothing)."""
        tools = dict(bnp.archive_tools())
        path = self.make_bnp(package(), ("tar", tools["tar"]))
        with mock.patch.object(bnp, "archive_tools", return_value=[("7z", tools["7z"])]):
            members, _ = bnp.list_archive(str(path))
            self.assertIn(".\\info.json", [m.raw for m in members])
            self.assertEqual(bnp.peek(str(path), "archive").name, "Made Up Mod")

    @unittest.skipUnless(os.name == "nt" and "7z" in {k for k, _ in bnp.archive_tools()}, "needs 7-Zip on Windows")
    def test_a_junction_stored_by_7zip_is_refused(self):
        import _winapi
        src = write(self.tmp / "bnp-src", {"info.json": "{}", "sub/a.txt": "a"})
        _winapi.CreateJunction(str(src / "sub"), str(src / "j"))
        exe = dict(bnp.archive_tools())["7z"]
        out = self.tmp / "junction.bnp"
        r = bnp.subprocess.run([exe, "a", "-t7z", "-snl", "-bd", str(out), "info.json", "j", "sub"], cwd=src,
                               capture_output=True)
        mm.remove_tree(str(src))
        if r.returncode:
            self.skipTest(f"7-Zip couldn't store the junction: {r.stderr[-200:]!r}")
        with mock.patch.object(bnp, "archive_tools", return_value=[("7z", exe)]), \
                self.assertRaises(bnp.BnpError) as cm:
            bnp.list_archive(str(out))
        self.assertIn("holds a link (j)", str(cm.exception))


# ---------------------------------------------------------------- modman

def fake_install(inst: Path) -> Path:
    return write(inst, {"savagewind.exe": b"MZ made-up exe", "extracted/swgame.dll": b"made-up game code",
                        "extracted/swgame.dll.id": "test-id\n", "extracted/romfs/System/Version.txt": "made up",
                        **{f"extracted/{k}": v for k, v in game_files().items()}})


class Modman(Base):
    def setUp(self):
        super().setUp()
        self.install = fake_install(self.tmp / "SavageWind" / "Savage Wind")
        self.libdir = self.tmp / "SavageWind" / "modloader"
        mm.Library(str(self.libdir), str(self.install)).save()
        for p in (mock.patch.object(mm, "game_running", return_value=False),
                  mock.patch.object(mm, "MODHOST_DIR", str(self.tmp / "no-modhost"))):
            p.start()
            self.addCleanup(p.stop)

    @property
    def lib(self):
        return mm.Library.load(str(self.libdir))

    def add(self, name, files, **kw):
        src = write(self.tmp / "downloads" / name, files)
        (entry, scan, notes), out = quiet(mm.add_mod, self.lib, str(src), **kw)
        return entry, notes

    def test_add_and_deploy_a_package(self):
        entry, notes = self.add("pkg", package())
        self.assertEqual((entry["name"], entry["version"], entry["bnp"]["options"]), ("Made Up Mod", "1.2",
                                                                                      ["Extra Bits", "Red"]))
        self.assertIn("romfs", entry["kinds"])
        text = "\n".join(notes)
        self.assertIn("2 pack(s) filled", text)
        self.assertIn("needs attention: logs/gamedata.yml", text)
        launch, out = quiet(mm.deploy, self.lib)
        overlay = Path(launch["parts"]["overlay"]["path"])
        self.assertTrue((overlay / "Model" / "Extra.txt").is_file())
        self.assertFalse(any(".bnp" in str(p) for p in overlay.rglob("*")))
        self.assertTrue(any("logs/gamedata.yml" in a for a in launch["attention"]))
        self.assertEqual(launch["env"].get(rm.ENV_NO_HUD), "1")   # (the mod ships a size table now)

    def test_two_packages_get_one_merged_size_table(self):
        self.add("one", package())
        other = package(**{"info.json": json.dumps({"name": "Second Mod", "version": "2"}),
                           "logs/rstb.json": json.dumps({"Model/New.txt": 5000, "Model/Two.txt": 40}),
                           f"{R}/Model/Two.txt": "two"})
        for k in list(other):
            if k.startswith(("options/", "logs/deepmerge", "logs/aslist", "logs/gamedata")):
                del other[k]
        self.add("two", other)
        launch, out = quiet(mm.deploy, self.lib, dry_run=True)
        self.assertTrue(any("merged into one" in n for n in launch["notes"]), launch["notes"])
        launch, out = quiet(mm.deploy, self.lib)
        overlay = Path(launch["parts"]["overlay"]["path"])
        table = bnp.Rstb((overlay / rm.RSTB).read_bytes())
        self.assertEqual(table.get("Model/New.txt"), 5000)     # (the larger of 900 and 5000)
        self.assertEqual(table.get("Model/Extra.txt"), 3000)   # (from the first mod only)
        self.assertEqual(table.get("Model/Two.txt"), 40)
        self.assertIsNone(table.get("Model/Kept.txt"))
        marker = json.loads((overlay / rm.MARKER).read_text(encoding="utf-8"))
        self.assertEqual(marker["rstb"]["merged_from"], ["Made Up Mod", "Second Mod"])
        self.assertFalse(any(c["path"] == rm.RSTB for c in marker["conflicts"]))
        # the second mod replaces packs of the first: said, not hidden
        self.assertTrue(any("Second Mod replaces" in a and "Pack/Title.pack" in a for a in launch["attention"]),
                        launch["attention"])

    def test_a_package_whose_sizes_change_nothing_ships_no_table(self):
        self.add("small", {"info.json": json.dumps({"name": "Small", "version": "1"}), f"{R}/Model/Kept.txt": "x",
                           "logs/rstb.json": json.dumps({"Model/Kept.txt": 10})})
        self.add("small2", {"info.json": json.dumps({"name": "Small Two", "version": "1"}), f"{R}/Model/K2.txt": "x",
                            "logs/rstb.json": json.dumps({"Model/Named.txt": 70})})
        launch, _ = quiet(mm.deploy, self.lib)
        self.assertNotIn(rm.ENV_NO_HUD, launch["env"])
        self.assertFalse(any("merged into one" in n for n in launch["notes"]), launch["notes"])
        self.assertFalse((Path(launch["parts"]["overlay"]["path"]) / rm.RSTB).exists())

    def test_stale_work_folders_are_swept_by_add(self):
        mods = self.libdir / "mods"
        old = [write(mods / ".adding-old", {"a/x.txt": "x"}), write(mods / ".adding-old.bnp-raw", {"b/y.txt": "y"})]
        fresh = write(mods / ".adding-busy.bnp-raw", {"c/z.txt": "z"})
        hours_ago = time.time() - 2 * 3600
        for top in old:
            for p in [top, *top.rglob("*")]:
                os.utime(p, (hours_ago, hours_ago))
        self.add("pkg", package())
        self.assertFalse(any(p.exists() for p in old))
        self.assertTrue(fresh.exists())   # (an add still running: touched within the hour)

    def test_cli_options(self):
        src = write(self.tmp / "downloads" / "pkg", package())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = mm.main(["add", str(src), "--library", str(self.libdir), "--bnp-no-options", "--bnp-option", "Blue"])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual(self.lib.mods[0]["bnp"]["options"], ["Blue"])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = mm.main(["add", str(src), "--library", str(self.libdir), "--bnp-option", "Nope"])
        self.assertEqual(rc, 2)
        self.assertIn("has no option 'Nope'", out.getvalue())
        self.assertEqual(len(self.lib.mods), 1)
        self.assertEqual([p.name for p in (self.libdir / "mods").iterdir()], [self.lib.mods[0]["id"]])

    def test_zip_of_an_extracted_package(self):
        import zipfile
        z = self.tmp / "downloads" / "pkg.zip"
        z.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z, "w") as zf:
            for rel, data in package().items():
                zf.writestr(f"Made Up/{rel}", data)
        (entry, _, notes), _ = quiet(mm.add_mod, self.lib, str(z))
        self.assertEqual(entry["name"], "Made Up Mod")
        mod = self.libdir / "mods" / entry["id"]
        self.assertTrue((mod / R / "Model" / "Extra.txt").is_file())
        self.assertFalse(any(p.name.endswith(".bnp-raw") for p in (self.libdir / "mods").iterdir()))


class Deploy(Base):
    """bnp.deploy_hook on made-up library copies: a BCML package installed here, and plain mods shipping a table."""

    def package_copy(self, name: str, files: dict) -> Path:
        src = write(self.tmp / f"src-{name}", {"info.json": json.dumps({"name": name, "version": "1"}), **files})
        quiet(bnp.install, str(src), str(self.tmp / name), str(self.game), info=bnp.peek(str(src), "folder"))
        return self.tmp / name

    def plain_table(self, name: str, drop: str, add: dict, extra: dict | None = None) -> Path:
        t = bnp.Rstb((self.game / "romfs" / rm.RSTB).read_bytes())
        del t.crc[crc(drop)]
        t.crc.update({crc(k): v for k, v in add.items()})
        return write(self.tmp / name, {f"romfs/{rm.RSTB}": t.to_bytes(), **(extra or {})})

    def test_one_key_per_file_whatever_the_order(self):
        """A BCML package names a file, another mod's table holds its CRC-32: the same file, so the result doesn't
        depend on the mods' order, and it is counted once."""
        a = self.package_copy("A", {f"{R}/Model/Kept.txt": "a", "logs/rstb.json": json.dumps({"Model/Kept.txt": 500})})
        b = self.plain_table("B", "Model/Kept.txt", {"Model/B.txt": 1234}, {"romfs/Model/B.txt": "b"})
        results = []
        for order in ([("A", a), ("B", b)], [("B", b), ("A", a)]):
            hook = bnp.deploy_hook([(n, str(p)) for n, p in order], str(self.game))
            table = bnp.Rstb(hook.rstb.data)
            results.append((table.get("Model/Kept.txt"), table.get("Model/B.txt")))
            self.assertIn("(2 entries;", hook.notes[0])
        self.assertEqual(results, [(None, 1234), (None, 1234)])   # (a removal wins, in either order)

    def test_without_the_game_table_every_mod_is_still_looked_at(self):
        c = self.plain_table("C", "Model/Kept.txt", {"Model/C.txt": 5}, {"romfs/Model/C.txt": "c"})
        a = self.package_copy("A", {f"{R}/Pack/Title.pack": sarc({"Other.txt": "mod other"}),
                                    "logs/rstb.json": json.dumps({"Model/New.txt": 900})})
        d = write(self.tmp / "D", {"romfs/Pack/Title.pack": sarc({"Other.txt": "d"})})
        (self.game / "romfs" / rm.RSTB).unlink()
        hook = bnp.deploy_hook([("C", str(c)), ("A", str(a)), ("D", str(d))], str(self.game))
        text = " | ".join(hook.attention)
        self.assertIn("size tables can't be merged", text)
        self.assertIn("A: D replaces 1 of its pack(s) (Pack/Title.pack)", text)
        self.assertIsNone(hook.rstb)

    def test_packages_whose_sizes_change_nothing_are_left_out(self):
        a = self.package_copy("A", {f"{R}/Model/Kept.txt": "a", "logs/rstb.json": json.dumps({"Model/New.txt": 9})})
        b = self.package_copy("B", {f"{R}/Model/K2.txt": "b", "logs/rstb.json": json.dumps({"Model/Two.txt": 40})})
        # (a copy added before this check kept sizes that change nothing in its rstb.json, and no table)
        bnp.write_json(str(b / ".bnp" / "rstb.json"), {"Model/Kept.txt": 10})
        (b / R / rm.RSTB).unlink()
        self.assertIsNone(bnp.deploy_hook([("A", str(a)), ("B", str(b))], str(self.game)).rstb)
        # such a copy that ships a table anyway is merged, so the merged table takes its place
        write(b, {f"{R}/{rm.RSTB}": (self.game / "romfs" / rm.RSTB).read_bytes()})
        hook = bnp.deploy_hook([("A", str(a)), ("B", str(b))], str(self.game))
        self.assertEqual(bnp.Rstb(hook.rstb.data).get("Model/New.txt"), 9)


if __name__ == "__main__":
    unittest.main()
