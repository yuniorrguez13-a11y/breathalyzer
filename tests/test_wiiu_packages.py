"""Tests for Wii U mod packages: a Wii U BCML package (.bnp) installed as a Switch mod (tools/bnp.py install_wiiu),
a Wii U UKMM package (tools/ukmm.py WiiuBinaries), UKMM's game data and save data merges and its maps used as map keys
(the Guardians crash), and Wii U event flows (tools/wiiu_convert.py). Every package, pack, log and game file here is
made up, built in code in temp folders."""
import contextlib
import io
import json
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import bcml_data as bd  # noqa: E402
import bnp  # noqa: E402
import modman as mm  # noqa: E402
import romfs_mods as rm  # noqa: E402
import ukmm  # noqa: E402
import wiiu_convert as wc  # noqa: E402

TID = "01007EF00011E000"
R = f"{TID}/romfs"
F32, STR64, STR256 = 1, 8, 15
HAVOK = b"\x57\xe0\xe0\x57"


def crc(s: str) -> int:
    return zlib.crc32(s.encode())


def write(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return root


def sarc(files: dict) -> bytes:
    """A Switch (little-endian) pack."""
    return bnp.sarc_build([(n, d if isinstance(d, bytes) else d.encode(), 4) for n, d in files.items()])


def be_sarc(files: dict) -> bytes:
    """A Wii U (big-endian) pack."""
    return wc.sarc_build([(n, d if isinstance(d, bytes) else d.encode()) for n, d in files.items()], wc.BE)


def yaz0(data: bytes) -> bytes:
    return wc.yaz0_compress(data)


def byml(v) -> bytes:
    return bd.byml_dump(v)


def be_byml(v) -> bytes:
    return wc.byml_convert(bd.byml_dump(v), wc.BE)


def plist(objs: dict | None = None, lists: dict | None = None) -> bnp.PList:
    pl = bnp.PList()
    for name, params in (objs or {}).items():
        pl.objs[crc(name)] = bnp.PObj({crc(k): v for k, v in params.items()})
    for name, sub in (lists or {}).items():
        pl.lists[crc(name)] = sub
    return pl


def aamp(objs: dict) -> bytes:
    p = bnp.Pio()
    p.root = plist(objs)
    return bnp.aamp_write(p)


def f32(x: float) -> tuple[int, bytes]:
    return F32, struct.pack("<f", x)


def mass(data: bytes) -> float:
    return struct.unpack("<f", bnp.aamp_read(data).root.objs[crc("Rigid")].params[crc("Mass")][1])[0]


def deepmerge_log(path: str, diff: bnp.PList) -> bytes:
    p = bnp.Pio()
    p.root = plist({"FileTable": {"File0": (STR256, path.encode())}})
    p.root.lists[crc(path)] = diff
    return bnp.aamp_write(p)


def rstb(entries: dict) -> bytes:
    crcs = {crc(k): v for k, v in entries.items()}
    return b"RSTB" + struct.pack("<II", len(crcs), 0) + b"".join(struct.pack("<II", k, crcs[k]) for k in sorted(crcs))


def actor_info(actors: list[dict]) -> bytes:
    actors = sorted(actors, key=lambda a: crc(a["name"]))
    return yaz0(byml({"Actors": actors, "Hashes": [bd.U32(crc(a["name"])) if crc(a["name"]) >= 1 << 31
                                                   else crc(a["name"]) for a in actors]}))


def pack_files(data: bytes) -> dict:
    raw = wc.yaz0_decompress(data)[0] if data[:4] == b"Yaz0" else data
    s = wc.sarc_parse(raw)
    return {"order": s.order, **{n.name: raw[s.data_offset + n.start:s.data_offset + n.end] for n in s.nodes}}


def game_files() -> dict:
    """A made-up Switch game: two actor packs (one with a Havok cloth file), the actor info and a size table."""
    hero = sarc({"Actor/ActorLink/Hero.bxml": aamp({"Link": {"Model": (STR64, b"Hero")}}),
                 "Actor/Physics/Hero.bphysics": aamp({"Rigid": {"Mass": f32(1.0)}}),
                 "Physics/Cloth/Hero.hkcl": HAVOK + b" the game's own cloth"})
    other = sarc({"Actor/ActorLink/Other.bxml": aamp({"Link": {"Model": (STR64, b"Other")}})})
    sizes = {"Actor/Pack/Hero.bactorpack": len(hero) + 0x400, "Actor/Pack/Other.bactorpack": len(other) + 0x400,
             "Actor/ActorLink/Hero.bxml": 600, "Actor/ActorLink/Other.bxml": 700,
             "Actor/Physics/Hero.bphysics": 500, "Physics/Cloth/Hero.hkcl": 300}
    return {"romfs/Actor/Pack/Hero.sbactorpack": yaz0(hero), "romfs/Actor/Pack/Other.sbactorpack": yaz0(other),
            "romfs/Actor/ActorInfo.product.sbyml": actor_info([
                {"name": "Hero", "instSize": 4000, "profile": "Armor", "bfres": "Hero"},
                {"name": "Twin", "instSize": 5000, "profile": "Armor", "bfres": "Hero"},
                {"name": "Lone", "instSize": 900, "profile": "Prop", "bfres": "Lone"}]),
            f"romfs/{rm.RSTB}": yaz0(rstb(sizes))}


def wiiu_package(**over) -> dict:
    """A made-up Wii U BCML package: a shrunk Wii U actor pack (a BYML file, a Wii U Havok file), an option adding a
    file to that pack, a new actor's pack, a readme in content/, Wii U sizes, a parameter change and an actor info
    log with Wii U instance sizes."""
    info = {"name": "Wii U Mod", "version": "2.0", "platform": "wiiu",
            "options": {"multi": [{"name": "Extra", "folder": "Extra", "default": True},
                                  {"name": "Off", "folder": "Off"}]}}
    log = {str(crc("NewThing")): {"name": "NewThing", "instSize": 1000, "profile": "Armor", "bfres": "Hero"},
           str(crc("Hero")): {"instSize": 100}}
    files = {
        "info.json": json.dumps(info),
        "thumbnail.jpg": b"\xff\xd8 not a game file",
        "content/Readme.txt": "a readme",
        "content/Actor/Pack/Hero.sbactorpack": yaz0(be_sarc({"Actor/Misc/Data.byml": be_byml({"Speed": 2}),
                                                             "Physics/Cloth/Hero.hkcl": HAVOK + b" wii u cloth"})),
        "content/Actor/Pack/NewThing.sbactorpack": yaz0(be_sarc({
            "Actor/ActorLink/NewThing.bxml": aamp({"Link": {"Model": (STR64, b"Hero")}})})),
        "logs/rstb.json": json.dumps({"Actor/Pack/Hero.bactorpack": 5, "Actor/Pack/NewThing.bactorpack": 7}),
        "logs/packs.json": json.dumps({"Actor/Pack/Hero.bactorpack": "content\\Actor\\Pack\\Hero.sbactorpack"}),
        "logs/deepmerge.aamp": deepmerge_log("content/Actor/Pack/Hero.sbactorpack//Actor/Physics/Hero.bphysics",
                                             plist({"Rigid": {"Mass": f32(9.5)}})),
        "logs/actorinfo.yml": bd.to_text(log),
        "options/Extra/content/Actor/Pack/Hero.sbactorpack": yaz0(be_sarc({"Actor/Misc/More.byml":
                                                                           be_byml({"More": 1})})),
        "options/Off/content/Actor/Pack/Off.sbactorpack": yaz0(be_sarc({"Off.byml": be_byml({"Off": 1})})),
    }
    files.update(over)
    return {k: v for k, v in files.items() if v is not None}


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        return fn(*a, **kw), out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wiiu-pkg-test-"))
        self.addCleanup(lambda: mm.remove_tree(str(self.tmp)))
        self.game = write(self.tmp / "game", game_files())


# ---------------------------------------------------------------------------------------------------- Wii U .bnp

class WiiuBnpParts(unittest.TestCase):
    def test_options_merge_into_the_base_pack_in_wii_u_byte_order(self):
        lower = yaz0(be_sarc({"A.txt": "base a", "Inner.sbactorpack": yaz0(be_sarc({"x": "1", "y": "2"}))}))
        upper = yaz0(be_sarc({"B.txt": "opt b", "Inner.sbactorpack": yaz0(be_sarc({"y": "3"}))}))
        out = pack_files(bnp.merge_pack_bytes(lower, upper))
        self.assertEqual(out["order"], wc.BE)
        self.assertEqual((out["A.txt"], out["B.txt"]), (b"base a", b"opt b"))
        inner = pack_files(out["Inner.sbactorpack"])
        self.assertEqual((inner["order"], inner["x"], inner["y"]), (wc.BE, b"1", b"3"))
        with self.assertRaises(wc.FormatError):
            bnp.merge_pack_bytes(sarc({"A.txt": "switch"}), be_sarc({"A.txt": "wii u"}))

    def test_log_paths_and_keys_become_the_switch_ones(self):
        old = "content/Actor/Pack/X.sbactorpack//Actor/Physics/X.bphysics"
        new, n = bnp.translate_aamp_log(deepmerge_log(old, plist({"Rigid": {"Mass": f32(3.0)}})))
        pio = bnp.aamp_read(new)
        self.assertEqual((n, bnp.file_table(pio)), (1, [f"{R}/Actor/Pack/X.sbactorpack//Actor/Physics/X.bphysics"]))
        self.assertIn(crc(bnp.file_table(pio)[0]), pio.root.lists)          # (keyed by the new path's CRC-32)
        self.assertNotIn(crc(old), pio.root.lists)
        self.assertEqual(bnp.wiiu_log_path("aoc/0010/Pack/AocMainField.pack"),
                         "01007EF00011F001/romfs/Pack/AocMainField.pack")
        same = deepmerge_log(f"{R}/A.sbactorpack//B.bphysics", plist())
        self.assertEqual(bnp.translate_aamp_log(same), (same, 0))          # (a Switch path is left as it is)

    def test_wii_u_choice_controls_get_the_switch_size(self):
        log = {"EUen": {"EventFlowMsg/A.msyt": {"Talk": {"contents": [
            {"text": "Yes or no?"},
            {"control": {"kind": "choice", "choice_labels": [3, 4], "selected_index": 0, "cancel_index": 1,
                         "unknown": 8}},
            {"control": {"kind": "choice", "choice_labels": [1, 2, 3], "selected_index": 0, "cancel_index": 2,
                         "unknown": 10}}]}}}}
        data, n = bnp.fix_wiiu_texts(json.dumps(log).encode())
        got = [c["control"]["unknown"] for c in json.loads(data)["EUen"]["EventFlowMsg/A.msyt"]["Talk"]["contents"]
               if "control" in c]
        self.assertEqual((n, got), (2, [6, 8]))
        same = json.dumps({"EUen": {}}).encode()
        self.assertEqual(bnp.fix_wiiu_texts(same), (same, 0))

    def test_instance_sizes_for_the_switch(self):
        actors = {crc("Hero"): {"name": "Hero", "instSize": 4000, "profile": "Armor", "bfres": "Hero"},
                  crc("Twin"): {"name": "Twin", "instSize": 5000, "profile": "Armor", "bfres": "Hero"},
                  crc("A"): {"name": "A", "instSize": 100, "profile": "Prop", "bfres": "A"},
                  crc("B"): {"name": "B", "instSize": 300, "profile": "Prop", "bfres": "B"},
                  crc("C"): {"name": "C", "instSize": 20000, "profile": "Prop", "bfres": "C"}}
        log = {crc("New"): {"name": "New", "instSize": 1000, "profile": "Armor", "bfres": "Hero"},   # twins: 5000
               crc("Big"): {"name": "Big", "instSize": 4000, "profile": "Armor", "bfres": "Hero"},   # 3x: 12000
               crc("Prop"): {"name": "Prop", "instSize": 50, "profile": "Prop", "bfres": "Z"},       # median: 300
               crc("Hero"): {"instSize": 100},                                                        # game's: 4000
               crc("Twin"): {"instSize": 9000}}                                                       # bigger: kept
        notes = bnp.fix_inst_sizes(log, actors)
        self.assertEqual([log[crc(n)]["instSize"] for n in ("New", "Big", "Prop", "Hero", "Twin")],
                         [5000, 12000, 300, 4000, 9000])
        self.assertEqual(len(notes), 3)


class WiiuBnpInstall(Base):
    def install(self, files=None, **kw):
        src = write(self.tmp / "pkg", files or wiiu_package())
        info = bnp.peek(str(src), "folder")
        stage = self.tmp / "mods" / ".adding-wiiu"
        res, out = quiet(bnp.install, str(src), str(stage), str(self.game), info=info, **kw)
        return res, stage

    def test_a_wii_u_package_installs_as_a_switch_one(self):
        res, stage = self.install()
        self.assertEqual([o.folder for o in res.chosen], ["Extra"])
        files = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}
        self.assertNotIn(f"{R}/Readme.txt", files)                      # (no game file right in content/)
        self.assertFalse(any("Off" in f for f in files))
        # the actor pack: Switch byte order, the option's file merged in, the game's files filled in, the
        # parameter change (logged with a Wii U path) merged, the Wii U Havok file swapped for the game's
        hero = pack_files((stage / R / "Actor/Pack/Hero.sbactorpack").read_bytes())
        self.assertEqual(hero["order"], wc.LE)
        self.assertEqual(bd.byml_load(hero["Actor/Misc/Data.byml"]), {"Speed": 2})
        self.assertEqual(bd.byml_load(hero["Actor/Misc/More.byml"]), {"More": 1})
        self.assertIn("Actor/ActorLink/Hero.bxml", hero)
        self.assertEqual(mass(hero["Actor/Physics/Hero.bphysics"]), 9.5)
        self.assertEqual(hero["Physics/Cloth/Hero.hkcl"], HAVOK + b" the game's own cloth")
        self.assertEqual(pack_files((stage / R / "Actor/Pack/NewThing.sbactorpack").read_bytes())["order"], wc.LE)
        # actor info: the new actor sized for the Switch, the game's actor never below the game's size
        actors = {a["name"]: a for a in bd.byml_load(wc.yaz0_decompress(
            (stage / R / "Actor/ActorInfo.product.sbyml").read_bytes())[0])["Actors"]}
        self.assertEqual((actors["NewThing"]["instSize"], actors["Hero"]["instSize"]), (5000, 4000))
        # the size table: worked out again (never the Wii U package's 5 and 7), new files estimated
        table = bnp.Rstb((stage / R / rm.RSTB).read_bytes())
        self.assertGreater(table.get("Actor/Pack/NewThing.bactorpack"), 7)
        self.assertGreater(table.get("Actor/ActorLink/NewThing.bxml"), 0)
        self.assertGreater(table.get("Actor/Pack/Hero.bactorpack"), 5)
        stored = json.loads((stage / ".bnp" / "bnp.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["converted_from"], "wiiu")
        self.assertTrue((stage / ".bnp" / wc.REPORT).is_file())
        logged = bnp.file_table(bnp.aamp_read((stage / ".bnp" / "logs" / "deepmerge.aamp").read_bytes()))
        self.assertEqual(logged, [f"{R}/Actor/Pack/Hero.sbactorpack//Actor/Physics/Hero.bphysics"])
        text = "\n".join(res.summary())
        self.assertIn("converted from Wii U", text)
        self.assertIn("needs attention: 1 file(s) in packs are Wii U data (Havok, sound...) that can't be converted: "
                      "your game's file of the same path is used (Physics/Cloth/Hero.hkcl), so that part of the mod is "
                      "lost", text)
        self.assertIn("instance sizes estimated for the Switch for 1 new actor(s)", text)
        self.assertFalse(list((self.tmp / "mods").glob("*.bnp-*")))      # (work folders removed)

    def test_wii_u_file_with_no_game_twin_leaves_its_pack(self):
        res, stage = self.install(wiiu_package(**{
            "content/Actor/Pack/NewThing.sbactorpack": yaz0(be_sarc({"Physics/Cloth/New.hkcl": HAVOK + b" new",
                                                                     "Actor/Misc/N.byml": be_byml({"N": 1})}))}))
        new = pack_files((stage / R / "Actor/Pack/NewThing.sbactorpack").read_bytes())
        self.assertNotIn("Physics/Cloth/New.hkcl", new)
        self.assertIn("Actor/Misc/N.byml", new)
        self.assertIn("left out of their packs", "\n".join(res.summary()))

    def test_loose_files_that_stay_wii_u_data_need_attention(self):
        """Loose Wii U files the converter can't convert are left out: one that changes a game file loses that part
        of the mod (the game's is used), a new one is missing. Both under Needs attention, not only in the notes."""
        write(self.game, {"romfs/Physics/Cloth/Lost.hkcl": HAVOK + b" the game's loose cloth"})
        res, stage = self.install(wiiu_package(**{"content/Physics/Cloth/Lost.hkcl": HAVOK + b" wii u lost",
                                                  "content/Physics/Cloth/Gone.hkcl": HAVOK + b" wii u new"}))
        bad = "\n".join(res.not_merged)
        self.assertRegex(bad, r"1 file\(s\) stay Wii U data and are left out \(1 [^)]*\): your game's are used "
                              r"\(Lost\.hkcl\), so that part of the mod is lost")
        self.assertRegex(bad, r"1 new file\(s\) stay Wii U data and your game has none \(1 [^;]*; Gone\.hkcl\): "
                              r"missing, so what uses them may fail")
        files = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}
        self.assertFalse(any(f.endswith((".hkcl",)) and "Cloth/" in f and "Pack" not in f for f in files))

    def test_entries_for_files_the_game_keeps_in_another_pack(self):
        """A file the size table lists but the game holds only in another pack: a shared file changed in a new actor
        pack (Hero.bxml in NewThing's pack), an actor pack shipped loose that the game keeps only in TitleBG.pack.
        Their entries grow from the game's copy found there, never left at the game's smaller one."""
        inside = sarc({"Actor/ActorLink/Inside.bxml": aamp({"Link": {"Model": (STR64, b"Inside")}})})
        crcs = dict(bnp.Rstb((self.game / "romfs" / rm.RSTB).read_bytes()).crc)
        crcs[crc("Actor/Pack/Inside.bactorpack")] = len(inside) + 0x400
        write(self.game, {"romfs/Pack/TitleBG.pack": sarc({"Actor/Pack/Inside.sbactorpack": yaz0(inside)}),
                          f"romfs/{rm.RSTB}": yaz0(b"RSTB" + struct.pack("<II", len(crcs), 0) + b"".join(
                              struct.pack("<II", k, crcs[k]) for k in sorted(crcs)))})
        big = aamp({"Link": {"Model": (STR64, b"Hero"), **{f"P{k}": (STR64, b"x" * 40) for k in range(40)}}})
        bigger = be_sarc({"Actor/ActorLink/Inside.bxml": aamp({"Link": {f"Q{k}": (STR64, b"y" * 40)
                                                                         for k in range(40)}}),
                          "Actor/Misc/More.byml": be_byml({"More": "z" * 4000})})
        res, stage = self.install(wiiu_package(**{
            "content/Actor/Pack/NewThing.sbactorpack": yaz0(be_sarc({"Actor/ActorLink/Hero.bxml": big})),
            "content/Actor/Pack/Inside.sbactorpack": yaz0(bigger)}))
        out = bnp.Rstb((stage / R / rm.RSTB).read_bytes())
        self.assertGreater(out.get("Actor/ActorLink/Hero.bxml"), 600)        # (the game's: 600, for a smaller file)
        self.assertGreater(out.get("Actor/Pack/Inside.bactorpack"), len(inside) + 0x400)

    def test_switch_layout_under_a_wii_u_info_is_refused(self):
        files = {"info.json": json.dumps({"name": "W", "platform": "wiiu"}), f"{R}/Model/A.txt": "x"}
        with self.assertRaisesRegex(bnp.BnpError, "Switch's layout"):
            self.install(files)

    def test_package_kind_takes_an_extracted_wii_u_package(self):
        finding = type("F", (), {"kind": "BCML mod (BNP)", "platform": "wiiu"})()
        self.assertEqual(bnp.package_kind(str(self.tmp), [finding], ["info.json", "options/A/content/x"]), "folder")
        self.assertIsNone(bnp.package_kind(str(self.tmp), [finding], ["info.json", "content/x"]))


class EventFlows(unittest.TestCase):
    def flow(self, bom: bytes) -> bytes:
        return b"BFEVFL\0\0" + b"\0\x03\0\0" + bom + bytes(0x22)

    def test_little_endian_event_flows_are_the_same_on_both(self):
        conv = wc.Converter(wc.LE, models=None)
        r = conv.file(self.flow(b"\xff\xfe"), "Event/A.bfevfl")
        self.assertEqual((r.status, r.data), (wc.SAME, self.flow(b"\xff\xfe")))
        self.assertEqual(conv.file(self.flow(b"\xfe\xff"), "Event/B.bfevfl").status, wc.CANT)
        pack = yaz0(be_sarc({"EventFlow/A.bfevfl": self.flow(b"\xff\xfe")}))
        r = wc.Converter(wc.LE, models=None).file(pack, "Event/A.sbeventpack")
        self.assertEqual(r.status, wc.CONVERTED)
        out = pack_files(r.data)
        self.assertEqual((out["order"], out["EventFlow/A.bfevfl"]), (wc.LE, self.flow(b"\xff\xfe")))


# ---------------------------------------------------------------------------------------------------- UKMM

def _head(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([major << 5 | n])
    for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if n < 1 << (8 * size):
            return bytes([major << 5 | ai]) + n.to_bytes(size, "big")
    raise ValueError(n)


def cbor(v) -> bytes:
    """CBOR as UKMM writes serde data; a list of (key, value) pairs stands for a map whose keys may be maps."""
    if v is None:
        return b"\xf6"
    if v is True or v is False:
        return b"\xf5" if v else b"\xf4"
    if isinstance(v, int):
        return _head(0, v) if v >= 0 else _head(1, -1 - v)
    if isinstance(v, str):
        return _head(3, len(v.encode())) + v.encode()
    if isinstance(v, (bytes, bytearray)):
        return _head(4, len(v)) + b"".join(_head(0, x) for x in v)
    if isinstance(v, Pairs):
        return _head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v)
    if isinstance(v, list):
        return _head(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return _head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(type(v))


class Pairs(list):
    pass


def save_flags(*flags: tuple[str, bool]) -> Pairs:
    return Pairs(({"name": n, "hash": bd._signed(crc(n))}, removed) for n, removed in flags)


def save_doc(fname: str, names: list[str]) -> bytes:
    head = {"IsCommon": False, "IsCommonAtSameAccount": False, "IsSaveSecureCode": True, "file_name": fname}
    entries = sorted(({"DataName": n, "HashValue": bd._signed(crc(n))} for n in names), key=lambda e: e["HashValue"])
    return byml({"file_list": [head, entries], "save_info": [{"directory_num": 8, "revision": 18203}]})


def flag_entry(name: str) -> dict:
    return {"DataName": name, "DeleteRev": -1, "HashValue": bd._signed(crc(name)), "InitValue": 0,
            "IsEventAssociated": False, "IsOneTrigger": False, "IsProgramReadable": True, "IsProgramWritable": True,
            "IsSave": True, "MaxValue": True, "MinValue": False, "ResetType": 0}


def ukmm_game() -> dict:
    files = game_files()
    saves = sarc({"/saveformat_0.bgsvdata": save_doc("game_data.sav", ["OldFlag", "GameFlag"]),
                  "/saveformat_1.bgsvdata": save_doc("caption.sav", ["Cap"]),
                  "/saveformat_2.bgsvdata": save_doc("option.sav", ["Opt"])})
    gamedata = sarc({"/bool_data_0.bgdata": byml({"bool_data": [flag_entry("GameFlag"), flag_entry("OldFlag")]})})
    files["romfs/Pack/Bootup.pack"] = sarc({"GameData/savedataformat.ssarc": yaz0(saves),
                                            "GameData/gamedata.ssarc": yaz0(gamedata), "Other.txt": "boot"})
    return files


def ukmm_flag(name: str) -> dict:
    return {"category": None, "data_name": name, "delete_rev": -1, "hash_value": bd._signed(crc(name)),
            "init_value": {"I32": 0}, "is_event_associated": False, "is_one_trigger": False,
            "is_program_readable": True, "is_program_writable": True, "is_save": True, "max_value": {"Bool": True},
            "min_value": {"Bool": False}, "reset_type": 0}


META = "name: Made Up\nversion: 1.0.0\nplatform: !Specific {plat}\napi: 1.0.0\noption_groups: []\n"


def ukmm_package(plat: str = "Switch", **over) -> dict:
    save = {"game_data.sav": {"header": {"file_name": "game_data.sav"},
                              "flags": save_flags(("NewFlag", False), ("OldFlag", True))},
            "option.sav": {"header": {"file_name": "option.sav"}, "flags": save_flags(("NewOpt", False))},
            "caption.sav": {"header": {"file_name": "caption.sav"}, "flags": Pairs()}}
    game = {"bool_data": {"data_type": "bool_data", "flags": {"ModFlag": [ukmm_flag("ModFlag"), False],
                                                              "OldFlag": [ukmm_flag("OldFlag"), True]}},
            "s32_data": {"data_type": "s32_data", "flags": {}}}
    files = {
        "meta.yml": META.format(plat=plat),
        "manifest.yml": "content:\n- Pack/Bootup.pack\n- Actor/Pack/Hero.sbactorpack\naoc: []\n",
        "Pack/Bootup.pack": cbor({"Sarc": {"alignment": 4, "files": {"GameData/savedataformat.ssarc": False,
                                                                     "GameData/gamedata.ssarc": False}}}),
        "GameData/savedataformat.sarc": cbor({"Mergeable": {"SaveDataPack": save}}),
        "GameData/gamedata.sarc": cbor({"Mergeable": {"GameDataPack": game}}),
        "Actor/Pack/Hero.bactorpack": cbor({"Sarc": {"alignment": 4, "files": {"Actor/Misc/Data.byml": False}}}),
        "Actor/Misc/Data.byml": cbor({"Binary": byml({"Speed": 3})}),
    }
    files.update(over)
    return {k: v for k, v in files.items() if v is not None}


def make_zip(path: Path, files: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for rel, data in files.items():
            z.writestr(rel, data if isinstance(data, bytes) else data.encode())
    return path


class Ukmm(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wiiu-ukmm-test-"))
        self.addCleanup(lambda: mm.remove_tree(str(self.tmp)))
        self.game = write(self.tmp / "game", ukmm_game())

    def install(self, files: dict):
        src = make_zip(self.tmp / "dl" / "pkg.zip", files)
        meta = ukmm.peek(str(src))
        stage = self.tmp / "stage"
        res, _ = quiet(ukmm.install, str(src), str(stage), str(self.game), meta=meta)
        return res, stage

    def boot(self, stage: Path, name: str) -> dict:
        return pack_files(pack_files((stage / R / "Pack/Bootup.pack").read_bytes())[name])

    def test_maps_as_map_keys_decode(self):
        """ukmm-guardians: UKMM keys save data flags by {name, hash} maps (CBOR allows it, Python dicts don't)."""
        value, end = ukmm.cbor_decode(cbor(save_flags(("A", False), ("B", True))))
        key = next(iter(value))
        self.assertEqual((dict(key), end), ({"name": "A", "hash": bd._signed(crc("A"))}, len(cbor(save_flags(
            ("A", False), ("B", True))))))
        self.assertEqual(list(value.values()), [False, True])
        self.assertEqual(hash(key), hash(ukmm.frozen({"name": "A", "hash": bd._signed(crc("A"))})))
        with self.assertRaises(TypeError):
            key["name"] = "changed"
        nested, _ = ukmm.cbor_decode(cbor(Pairs([([{"a": [1, 2]}], "x")])))
        self.assertEqual(list(nested.values()), ["x"])

    def test_save_data_and_game_data_flags_are_merged(self):
        res, stage = self.install(ukmm_package())
        saves = {}
        for name, data in self.boot(stage, "GameData/savedataformat.ssarc").items():
            if name != "order":
                fl = bd.byml_load(data)["file_list"]
                saves[fl[0]["file_name"]] = [e["DataName"] for e in fl[1]]
        self.assertEqual(sorted(saves["game_data.sav"]), ["GameFlag", "NewFlag", "OldFlag"])  # (the delete kept)
        self.assertEqual(sorted(saves["option.sav"]), ["NewOpt", "Opt"])
        self.assertEqual(saves["caption.sav"], ["Cap"])
        flags = [e["DataName"] for name, data in self.boot(stage, "GameData/gamedata.ssarc").items()
                 if name != "order" for e in bd.byml_load(data)["bool_data"]]
        self.assertEqual(sorted(flags), ["GameFlag", "ModFlag", "OldFlag"])
        text = "\n".join(res.summary())
        self.assertIn("1 flag(s) the mod deletes kept (OldFlag)", text)
        self.assertNotIn("not merged", text)
        self.assertNotIn("can't be", text)

    def test_one_unreadable_resource_is_named_and_the_rest_installs(self):
        res, stage = self.install(ukmm_package(**{"GameData/gamedata.sarc": cbor({"Mergeable": 5})}))
        text = "\n".join(res.summary())
        self.assertIn("needs attention: GameData/gamedata.sarc can't be read", text)
        self.assertIn("NewFlag", str([bd.byml_load(d) for n, d in self.boot(stage, "GameData/savedataformat.ssarc")
                                      .items() if n != "order"]))

    def test_a_wii_u_package_is_converted(self):
        files = ukmm_package("Wii U", **{
            "Actor/Misc/Data.byml": cbor({"Binary": be_byml({"Speed": 4})}),
            "Actor/Pack/Hero.bactorpack": cbor({"Sarc": {"alignment": 4, "files": {
                "Actor/Misc/Data.byml": False, "Physics/Cloth/Hero.hkcl": False, "Physics/Cloth/Gone.hkcl": False}}}),
            "Physics/Cloth/Hero.hkcl": cbor({"Binary": HAVOK + b" wii u"}),
            "Physics/Cloth/Gone.hkcl": cbor({"Binary": HAVOK + b" no twin"})})
        res, stage = self.install(files)
        hero = pack_files((stage / R / "Actor/Pack/Hero.sbactorpack").read_bytes())
        self.assertEqual(bd.byml_load(hero["Actor/Misc/Data.byml"]), {"Speed": 4})       # (made little-endian)
        self.assertEqual(hero["Physics/Cloth/Hero.hkcl"], HAVOK + b" the game's own cloth")
        self.assertNotIn("Physics/Cloth/Gone.hkcl", hero)
        text = "\n".join(res.summary())
        self.assertIn("Wii U package: 3 binary file(s) converted for the Switch", text)
        self.assertIn("Havok physics", text)
        # (the Wii U data that can't be converted: under Needs attention, not only in the notes)
        self.assertIn("needs attention: 1 file(s) are Wii U data that can't be converted (1 Havok physics): your "
                      "game's are used (Hero.hkcl), so that part of the mod is lost", text)
        self.assertIn("needs attention: 1 new file(s) are Wii U data that can't be converted and your game has none "
                      "(1 Havok physics; Gone.hkcl): left out, so what uses them may fail", text)

    def test_wii_u_texture_names_follow_the_switch(self):
        b = type("B", (), {"wanted": set()})()
        w = ukmm.WiiuBinaries(b)
        w.renamed["Model/X.Tex1.bfres"] = "Model/X.Tex.bfres"
        w.dropped.add("Model/X.Tex2.bfres")
        self.assertEqual(w.real_name(False, "Model/X.Tex1.sbfres"), "Model/X.Tex.sbfres")
        self.assertIsNone(w.real_name(False, "Model/X.Tex2.sbfres"))
        self.assertEqual(w.pack_names({"Model/X.Tex1.sbfres": False, "Model/X.Tex2.sbfres": False, "A": True}, False),
                         {"Model/X.Tex.sbfres": False, "A": True})


if __name__ == "__main__":
    unittest.main()
