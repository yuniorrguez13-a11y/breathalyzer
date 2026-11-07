"""Tests for tools/ukmm.py (UKMM packages), tools/zstdec.py (its zstd decoder) and their use in tools/modman.py.
Every package, pack, parameter file, size table and game file here is made up, built in code in temp folders. The
zstd frames below were made with Python 3.14's own zstd from the made-up text the generators here give back, and the
dictionary was trained on that made-up text (nothing of UKMM's, a mod's or the game's)."""
import contextlib
import io
import json
import math
import os
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import bnp  # noqa: E402
import modman as mm  # noqa: E402
import romfs_mods as rm  # noqa: E402
import ukmm  # noqa: E402
import wiiu_convert as wc  # noqa: E402
import zstdec  # noqa: E402

TID = "01007EF00011E000"
R = f"{TID}/romfs"
F32, STR64, STRREF, BOOL = 1, 8, 20, 0

WORDS = ("ember", "river", "stone", "lantern", "meadow", "copper", "willow", "falcon", "harbor", "pebble", "thistle",
         "glimmer", "orchard", "saddle", "quill", "marble")


def made_up_text(n: int, seed: int) -> bytes:
    x, out, size = seed, [], 0
    while size < n:
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        w = WORDS[x % len(WORDS)]
        if x % 7 == 0:
            w += str(x % 1000)
        out.append(w)
        size += len(w) + 1
    return (" ".join(out) + ".\n")[:n].encode()


def made_up_bytes(n: int, seed: int) -> bytes:
    x, out = seed, bytearray()
    for _ in range(n):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(x >> 16 & 0xFF)
    return bytes(out)


# made_up_text(6000, 7), level 19 with a checksum: Huffman literals (4 streams), FSE and predefined sequence tables
TEXT_FRAME = bytes.fromhex(
    "28b52ffd647016f51000f6594118804bda404c6d6ac7783c8f82817925b27bd3664bbb09dd054f003500310049627898c32fc76158cc"
    "a20016a460d25ecee1604808e6248c85a1c030cb49249480112c4821b0280b723049626828cc4900809330cb011c8e412c8781401273"
    "100a63614e2210200bf35004c83904c79b91a9a995b56c7a59ef108f34eb48cd5613f574dbb233d34e8f57759bff5ff817d9bcb212f7"
    "bcb56a7793368412489081792801fab3eba6d97e93d7563fbdf7f31dbe7e86fee6197f498589979b8aba36a19a7da77de291a9ca5c87"
    "9a6a446a3b1b4b6b03f6bb399da745ddc253e25daab768cd533a6e6d5edeedbc9bd9ae47af0bedcee635325b3dab5daa49d1c8a968da"
    "753a490379a0710ca5efff73404a2441ab7103bf84c799a36da0f3ee27fcdeeb8f048c0cf299e41de561ce45760f311395b3266d9393"
    "99741481df7fc77e0e74a8bf9d65a3fc19f2afd5479297f147e22972484048db20a92c6404f8295f39c7f69e04889ada503fe54ab259"
    "8af24c9387e5cd2c0f69a88df8983c9d9c13f6237c5cb0f68f65ce741b7f53a3be81d2d264102930b72493893a1c29bfeca3757aa83b"
    "ea169096e24e6a809b92cf0f8d9ee87e3b224414a8cb37f09c39c2063a34844d635e530a0bf24eeeb64b37fe47be32a543fed291e1f9"
    "6126ec38fceb6dc4103e53cae9482fd0395fa5ca91fe02dfc9e31e4fc4f2e47a0ae01458b3a79db3a321dd8e0a65813e7bd5b13d18b8"
    "d3b21ff20cc157ac04d6a00683d07256")
# made_up_text(3000, 11), level 1: one literals stream, every table FSE-coded
FAST_FRAME = bytes.fromhex(
    "28b52ffd60b80a150d00628f2a197017b80311880c2b263d6a043ffeff1391643793ac50fa5f5e00c64fb61b5fc73eccaa27d3dbd91d"
    "d335b9dd5bf970f538aa7a77de359e632b261fea2ea7c135ed472ff330231bf4e4a3e779e62c9a66cff6dbf6cbcc75f5dceec5b3f5c7"
    "612401fbb42846b1d8fa245183110b15c352f64958e8b0db4dc92018b04f82f089ad178e3764fd82a0648fa845b163f8134120c5068a"
    "407f18a28684026ca0523206b6bec058b694b0501169a82116e4fb7f0740c23db21e12d0f0b808429013500409c290048a080d133821"
    "63868c89d89407d03d897f5ae9fce74577d4b3365dccd818c7d6ab8d8d7c07dc93d17e38bb229f99569fab5c6db6d4103c74173dbe68"
    "37d1e36f3f6de497f75b358e27eb39953fb7f2a68ca6cc93dea5905f65af912645d9adbe9fb10527abaa9bc1b92be345de63bf5c995e"
    "6d027cce61bb1b678f99b0f9cccd71296aa350e7426d78ee387c3ebda1c3c7ddd6f6ce796c21bced3c5bb58a53f7041d9579f5ee0fa0"
    "e5563aba8f44c34ab75b6ed870f1bdbc6d7ef2117863f389f4e332a39d68d97b66d04e797d61fe31cf2e26c25b939dbb7815")
# a 1 KB dictionary trained on made_up_text(300 + (i * 37) % 500, 100 + i) for i in range(300)
TRAINED_DICT = bytes.fromhex(
    "37a430ec0524e6742310d0926438ffffffffff0fd07f3e225afb4ff6c527b65955bc644a52a624ed65f54f183302000000031412eeb6"
    "010004600040840e27090802052b1e1f080611071d1e0685a1b0985c0c0502a0c0588ae7519ae7bc1a000000c42fe3a77b0c00000000"
    "000000000000000000010000000400000008000000696c6c6f772066616c636f6e206d6561646f7720736164646c653334392073746f"
    "6e65206c616e7465726e20656d62657220706562626c65207175696c6c206d6172626c65343135206f72636861726466616c636f6e34"
    "3339206d6561646f7720736164646c652073746f6e65206c616e7465726e20656d62657238323420706562626c65323831207175696c"
    "6c206d6172626c65206f72636861726435373220636f707065722074686973746c6520676c696d6d657220686172626f722072697665"
    "722077696c6c6f772066616c636f6e206d6561646f7720736164646c6534352073746f6e65206c616e7465726e20656d626572207065"
    "62626c65207175696c6c206d6172626c65206f72636861726420636f707065723834352074686973746c6520676c696d6d6572353739"
    "20686172626f722072697665722077696c6c6f773931382066616c636f6e206d6561646f7737393620736164646c653831332073746f"
    "6e65206c616e7465726e343320656d62657220706562626c65207175696c6c373538206d6172626c65206f72636861726420636f7070"
    "65723130312074686973746c6520676c696d6d657220686172626f723431362072697665722077696c6c6f772066616c636f6e313833"
    "2e6d6561646f7720736164646c652073746f6e65206c616e7465726e20656d62657220706562626c65207175696c6c206d6172626c65"
    "206f72636861726420636f707065722074686973746c6520676c696d6d657220686172626f722072697665722077696c6c6f77206661"
    "6c636f6e353833206d6561646f7720736164646c652073746f6e65353730206c616e7465726e20656d62657220706562626c65207175"
    "696c6c343934206d6172626c65206f72636861726431353620636f707065722074686973746c6520676c696d6d657220686172626f72"
    "2072697665722077696c6c6f772066616c636f6e206d6561646f7720736164646c652073746f6e65313232206c616e7465726e20656d"
    "62657220706562626c65207175696c6c206d6172626c65206f72636861726420636f707065723933332074686973746c6520676c696d"
    "6d657220686172626f722072697665722077696c6c6f773732362066616c636f6e206d6561646f7720736164646c652073746f6e6520"
    "6c616e7465726e20656d62657220706562626c65207175696c6c206d6172626c65206f72636861726420636f7070726976657220")
# made_up_text(900, 555), level 3, with TRAINED_DICT: its Huffman and FSE tables reused, its content matched
DICT_FRAME = bytes.fromhex(
    "28b52ffd630524e6748402f50300130408bcab8d97195a7a76e45cd1a8bed84c9d5a34f472e6518dc8339ce9979b65b84c2cfc3c8484"
    "47e6f14f366841e0b902b5a5fad07e420c6821d21dde4a9c9c78f8b8ce7bbc8b3fb340449e32aaa453665779fb2f9e97bb434ea11a4a"
    "11ebf3ee833fb1bef432273fd1b50ccd1c973387b5b1714e2e95fef86237331a")
RLE_FRAME = bytes.fromhex("28b52ffd60e8024d00001000000100e32b8005")      # bytes(1000)
RAW_FRAME = bytes.fromhex(                                                 # made_up_bytes(64, 3): a raw block
    "28b52ffd204001020053c37d788eb44db7482f6d463d19e570244cbba0e358fc7874fa8cb1955cafb5321253fe93d1232c45ed4ce9c9"
    "990d7dffdc013051552c63a0b0c76deee4cc36")


def frame(blocks: list[tuple[int, bytes, int]], size: int | None = None) -> bytes:
    """A zstd frame by hand: blocks of (type, content, size field); the last one is marked last."""
    out = struct.pack("<I", zstdec.MAGIC)
    if size is None:
        out += bytes([0x00, 0x00])                       # (no content size; window descriptor 1 KB)
    else:
        out += bytes([0x20, size])                       # (single segment, 1-byte content size)
    for k, (kind, content, n) in enumerate(blocks):
        head = (1 if k == len(blocks) - 1 else 0) | kind << 1 | n << 3
        out += head.to_bytes(3, "little") + content
    return out


def raw_frame(data: bytes) -> bytes:
    return frame([(0, data, len(data))])


def rle_bomb(blocks: int) -> bytes:
    """A frame with no content size whose 128 KB RLE blocks take 4 bytes each (zstd allows it: 32768 to 1)."""
    out = struct.pack("<I", zstdec.MAGIC) + bytes([0x00, 0x38])       # (window 128 KB)
    for k in range(blocks):
        out += ((1 if k == blocks - 1 else 0) | 1 << 1 | (128 << 10) << 3).to_bytes(3, "little") + b"A"
    return out


def long_matches(nseq: int) -> bytes:
    """A compressed block by hand: one literal per sequence, each sequence a match of 65539 bytes (offset 1). Two
    of them make more than the 128 KB a block may decode to."""
    lits = bytes([1 | nseq << 3]) + b"A"                        # RLE literals, nseq of them
    seqs = bytes([nseq, 0x54, 1, 2, 52])                        # RLE tables: literal length 1, offset code 2, match 52
    bits = 18 * nseq                                            # (2 offset bits and 16 match length bits, all 0)
    stream = (1 << bits).to_bytes(bits // 8 + 1, "little")
    block = lits + seqs + stream
    head = (1 | 2 << 1 | len(block) << 3).to_bytes(3, "little")
    return struct.pack("<I", zstdec.MAGIC) + bytes([0x00, 0x38]) + head + block      # (window 128 KB)


# ---------------------------------------------------------------------------------------------------- CBOR

def _head(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([major << 5 | n])
    for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if n < 1 << (8 * size):
            return bytes([major << 5 | ai]) + n.to_bytes(size, "big")
    raise ValueError(n)


def cbor(v) -> bytes:
    """CBOR the way UKMM's serializer writes serde data (bytes as an array of uints, as Vec<u8> is)."""
    if v is None:
        return b"\xf6"
    if v is True or v is False:
        return b"\xf5" if v else b"\xf4"
    if isinstance(v, int):
        return _head(0, v) if v >= 0 else _head(1, -1 - v)
    if isinstance(v, float):
        return b"\xfa" + struct.pack(">f", v)
    if isinstance(v, str):
        b = v.encode()
        return _head(3, len(b)) + b
    if isinstance(v, (bytes, bytearray)):
        return _head(4, len(v)) + b"".join(_head(0, x) for x in v)
    if isinstance(v, (list, tuple)):
        return _head(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return _head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(type(v))


def binary(data: bytes) -> bytes:
    return cbor({"Binary": data})


def sarc_res(files: dict, align: int = 4) -> bytes:
    return cbor({"Sarc": {"alignment": align, "files": files}})


def mergeable(kind: str, value) -> bytes:
    return cbor({"Mergeable": {kind: value}})


def crc(s: str) -> int:
    return zlib.crc32(s.encode())


def pio_cbor(objs: dict, lists: dict | None = None) -> dict:
    return {"version": 0, "data_type": "xml", "param_root": plist_cbor(objs, lists)}


def plist_cbor(objs: dict, lists: dict | None = None) -> dict:
    return {"objects": {crc(o): {crc(k): v for k, v in ps.items()} for o, ps in objs.items()},
            "lists": {crc(n): sub for n, sub in (lists or {}).items()}}


# ---------------------------------------------------------------------------------------------------- game files

def write(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return root


def sarc(files: dict, align: int = 4) -> bytes:
    return bnp.sarc_build([(n, d if isinstance(d, bytes) else d.encode(), align) for n, d in files.items()])


def aamp(objs: dict | None = None, lists: dict | None = None) -> bytes:
    p = bnp.Pio()
    p.root = plist(objs, lists)
    return bnp.aamp_write(p)


def plist(objs: dict | None = None, lists: dict | None = None) -> bnp.PList:
    pl = bnp.PList()
    for name, params in (objs or {}).items():
        pl.objs[crc(name)] = bnp.PObj({crc(k): v for k, v in params.items()})
    for name, sub in (lists or {}).items():
        pl.lists[crc(name)] = sub
    return pl


def s64(text: str) -> tuple[int, bytes]:
    return STR64, text.encode()


def f32(x: float) -> tuple[int, bytes]:
    return F32, struct.pack("<f", x)


def ref(text: str) -> tuple[int, bytes]:
    return STRREF, text.encode()


def rstb(crcs: dict) -> bytes:
    return b"RSTB" + struct.pack("<II", len(crcs), 0) + b"".join(struct.pack("<II", k, crcs[k]) for k in sorted(crcs))


def actor_link() -> bytes:
    return aamp({"LinkTarget": {"ModelUser": ref("Foo"), "PhysicsUser": ref("Foo")},
                 "Tags": {"Tag0": ref("A"), "Tag1": ref("B")}})


def model_list() -> bytes:
    unit = plist({"Unit_0": {"UnitName": s64("Body"), "BindBone": s64("")}})
    md = plist({"Base": {"Folder": s64("Foo")}}, {"Unit": unit})
    return aamp({"ControllerInfo": {"BaseScale": (4, struct.pack("<3f", 1, 1, 1))},
                 "Attention": {"CursorOffsetY": f32(0)}},
                {"ModelData": plist(None, {"ModelData_0": md}), "AnmTarget": plist()})


def as_list() -> bytes:
    adds = plist({"AddRes_0": {"Anim": s64("Basic")}})
    defs = plist({"ASDefine_0": {"Name": s64("Wait"), "Filename": s64("Player_Wait")},
                  "ASDefine_1": {"Name": s64("Run"), "Filename": s64("Player_Run")}})
    return aamp({"Common": {"Speed": f32(1)}}, {"AddReses": adds, "ASDefines": defs})


GAME_SIZES = {"Actor/Pack/Foo.bactorpack": 9000, "Pack/Title.pack": 50000, "Actor/Pack/Player.bactorpack": 7000,
              "Model/Old.bfres": 300, "Actor/ActorLink/Foo.bxml": 1000}


def game_files() -> dict:
    foo = sarc({"Actor/ActorLink/Foo.bxml": actor_link(),
                "Actor/GeneralParamList/Foo.bgparamlist": aamp({"General": {"Speed": f32(1), "Life": f32(5)}}),
                "Actor/ModelList/Foo.bmodellist": model_list(),
                "Actor/Physics/Foo.bphysics": aamp({"Rigid": {"Mass": f32(10)}}),
                "Actor/Misc/Keep.bin": b"keep me"})
    player = sarc({"Actor/ASList/Player.baslist": as_list(), "Actor/AS/Idle.bas": aamp({"Idle": {"X": f32(1)}})})
    title = sarc({"Actor/Pack/Player.sbactorpack": wc.yaz0_compress(player), "Other/Thing.txt": b"thing"})
    return {"romfs/Actor/Pack/Foo.sbactorpack": wc.yaz0_compress(foo), "romfs/Pack/Title.pack": title,
            "romfs/Model/Old.sbfres": wc.yaz0_compress(b"FRES" + bytes(60)),
            "romfs/Pack/Bootup_USen.pack": sarc({"Message/Msg_USen.product.ssarc": b"texts"}),
            "romfs/System/Resource/ResourceSizeTable.product.srsizetable":
                wc.yaz0_compress(rstb({crc(k): v for k, v in GAME_SIZES.items()}))}


META = """api: 0.17.1
name: Made Up Mod
version: 1.2.0
author: Someone
category: Other
description: |-
  A made-up package.

  Second paragraph.
platform: !Specific Switch
url: null
option_groups:
- type: Exclusive
  name: Colour
  description: ''
  required: true
  default: Red
  options:
  - name: Red
    description: Warm
    path: Red
    requires: []
  - name: Blue
    description: "Cold \\"blue\\""
    path: Blue
    requires: []
- type: Multiple
  name: Extras
  description: Select one or more of these
  required: false
  defaults: []
  options:
  - name: Extra Bits
    description: ''
    path: Extra
    requires:
    - Red
masters: {}
"""


def package(meta: str = META, **over) -> dict:
    """A made-up UKMM package's files (stored resources are plain CBOR, as UKMM's own "unzip" leaves them, unless a
    test zstd-wraps them)."""
    files = {
        "meta.yml": meta,
        "manifest.yml": "content:\n- Actor/Pack/Foo.sbactorpack\n- Model/New.sbfres\n- Model/Old.sbfres\n"
                        "- Pack/Title.pack\naoc: []\n",
        "Actor/Pack/Foo.bactorpack": sarc_res({"Actor/Misc/New.bin": False, "Actor/Misc/Keep.bin": True}),
        "Actor/Misc/New.bin": binary(b"FRES-like new file " * 3),
        "Actor/ActorLink/Foo.bxml": mergeable("ActorLink", {
            "targets": {crc("ModelUser"): {"StringRef": "FooMod"}}, "tags": {"B": True, "C": False},
            "fit_tags": None}),
        "Actor/GeneralParamList/Foo.bgparamlist": mergeable("GeneralParamList", pio_cbor({"General": {
            "Speed": {"F32": 2.0}}})),
        "Actor/ModelList/Foo.bmodellist": mergeable("ModelList", {
            "controller_info": {}, "attention": {crc("CursorOffsetY"): {"F32": 0.5}},
            "model_data": {0: {"folder": "FooMod", "units": {"Cape": ["Spine", False]}}}, "anm_target": {},
            "locators": [[{crc("Pos"): {"Vec3": {"x": 1.0, "y": 2.0, "z": 3.0}}}, False]]}),
        "Actor/Physics/Foo.bphysics": mergeable("Physics", {"made": "up"}),
        "Actor/ASList/Player.baslist": mergeable("ASList", {
            "common": None, "add_reses": {"Glide": [{"anim": "Glide", "retarget_model": None,
                                                     "retarget_nocorrect": True}, False]},
            "as_defines": {"Run": ["Player_RunFast", False], "Dash": ["Player_Dash", False]}, "cf_defines": None}),
        "Model/Old.bfres": binary(b"FRES" + bytes(200)),
        "Model/New.bfres": binary(b"FRES" + bytes(30)),
        "options/Red/manifest.yml": "content:\n- Actor/Pack/Foo.sbactorpack\naoc: []\n",
        "options/Red/Actor/GeneralParamList/Foo.bgparamlist": mergeable("GeneralParamList", pio_cbor({"General": {
            "Life": {"F32": 9.0}}})),
        "options/Blue/manifest.yml": "content:\n- Actor/Pack/Foo.sbactorpack\naoc: []\n",
        "options/Blue/Actor/GeneralParamList/Foo.bgparamlist": mergeable("GeneralParamList", pio_cbor({"General": {
            "Life": {"F32": 1.0}}})),
        "options/Extra/manifest.yml": "content:\n- Model/Extra.sbfres\naoc: []\n",
        "options/Extra/Model/Extra.bfres": binary(b"FRES" + bytes(10)),
    }
    files.update(over)
    return {k: v for k, v in files.items() if v is not None}


def make_zip(path: Path, files: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for rel, data in files.items():
            z.writestr(rel, data if isinstance(data, bytes) else data.encode())
    return path


def quiet(fn, *a, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        return fn(*a, **kw), out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ukmm-test-"))
        self.addCleanup(lambda: mm.remove_tree(str(self.tmp)))
        self.game = write(self.tmp / "game", game_files())

    def install(self, files: dict, picks=(), no_defaults=False, zipped=True, **kw):
        src = make_zip(self.tmp / "dl" / "pkg.zip", files) if zipped else write(self.tmp / "dl" / "pkg", files)
        meta = ukmm.peek(str(src))
        stage = self.tmp / "stage"
        res, _ = quiet(ukmm.install, str(src), str(stage), str(self.game), meta=meta, picks=list(picks),
                       no_defaults=no_defaults, **kw)
        return res, stage

    def pack(self, stage: Path, rel: str) -> dict:
        data = (stage / R / rel).read_bytes()
        files = bnp.Blob(data).open()
        return {n: (c.data() if isinstance(c, bnp.Blob) else c) for n, (c, _) in files.items()}


# ---------------------------------------------------------------------------------------------------- zstd

class Zstd(unittest.TestCase):
    def test_frames_made_by_zstd_decode(self):
        d = zstdec.Dictionary(TRAINED_DICT)
        self.assertEqual(d.id, 0x74E62405)
        for data, want, dictionary in ((TEXT_FRAME, made_up_text(6000, 7), None),
                                       (FAST_FRAME, made_up_text(3000, 11), None),
                                       (DICT_FRAME, made_up_text(900, 555), d), (RLE_FRAME, bytes(1000), None),
                                       (RAW_FRAME, made_up_bytes(64, 3), None)):
            self.assertEqual(zstdec.decompress_py(data, dictionary), want)
            self.assertEqual(zstdec.decompress(data, dictionary), want)
        self.assertEqual(zstdec.frame_info(DICT_FRAME), (d.id, 900))
        self.assertEqual(zstdec.decompress_head(TEXT_FRAME, None, 40), made_up_text(6000, 7)[:40])

    def test_a_frame_needing_a_dictionary_says_so(self):
        with self.assertRaisesRegex(zstdec.ZstdError, "dictionary"):
            zstdec.decompress_py(DICT_FRAME)

    def test_hand_made_frames(self):
        two = frame([(0, b"abc", 3), (1, b"z", 4)], size=7)                     # a raw block, then an RLE block
        skip = struct.pack("<II", 0x184D2A50, 3) + b"xyz"
        self.assertEqual(zstdec.decompress_py(skip + two + raw_frame(b"!")), b"abczzzz!")
        # Huffman literals with directly stored weights: symbols 0 and 1, one bit each, read from the end down
        bits = [1, 0, 0, 1, 1, 0, 1]
        v = 1 << len(bits)
        for k, b in enumerate(bits):
            v |= b << (len(bits) - 1 - k)
        stream = v.to_bytes((v.bit_length() + 7) // 8, "little")
        tree = bytes([128, 0x10])                                                # one weight given (1), last implied
        comp = len(tree) + len(stream)
        lit = (2 | len(bits) << 4 | comp << 14).to_bytes(3, "little")
        block = lit + tree + stream + b"\x00"                                    # (no sequences)
        self.assertEqual(zstdec.decompress_py(frame([(2, block, len(block))], size=len(bits))), bytes(bits))

    def test_damaged_frames_raise_zstd_errors(self):
        for data in (TEXT_FRAME, FAST_FRAME, RLE_FRAME, RAW_FRAME):
            for cut in range(5, len(data), max(1, len(data) // 25)):
                with self.assertRaises(zstdec.ZstdError):
                    zstdec.decompress_py(data[:cut])
        flipped = bytearray(TEXT_FRAME)
        for at in range(20, len(flipped), 37):
            flipped[at] ^= 0x5A
            try:
                zstdec.decompress_py(bytes(flipped))
            except zstdec.ZstdError:
                pass
        with self.assertRaises(zstdec.ZstdError):
            zstdec.Dictionary(TRAINED_DICT[:40])

    def test_output_is_capped_before_it_is_made(self):
        """ukmm-zstd-bomb: a frame with no content size is cut off at the limit by both decoders (Python 3.14's own
        decoder decoded it whole before), and so are several frames that each fit."""
        import tracemalloc
        bomb = rle_bomb(2000)                                   # 8 KB -> 250 MB
        for fn in (zstdec.decompress, zstdec.decompress_py):
            with self.subTest(fn=fn.__name__):
                tracemalloc.start()
                try:
                    with self.assertRaisesRegex(zstdec.ZstdError, "too big"):
                        fn(bomb, None, 1 << 20)
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
                self.assertLess(peak, 16 << 20)
                two = rle_bomb(5)                               # 640 KB each
                self.assertEqual(len(fn(two + two, None, 2 << 20)), 2 * 5 * (128 << 10))
                with self.assertRaisesRegex(zstdec.ZstdError, "too big"):
                    fn(two + two, None, 1 << 20)
                with self.assertRaises(zstdec.ZstdError):
                    fn(TEXT_FRAME[:-20])                        # (cut short)
                skip = struct.pack("<II", 0x184D2A50, 3) + b"xyz"
                self.assertEqual(fn(skip + RLE_FRAME + skip + RAW_FRAME), bytes(1000) + made_up_bytes(64, 3))
        old = ukmm.MAX_RESOURCE
        with mock.patch.object(ukmm, "MAX_RESOURCE", 1 << 20):
            with self.assertRaisesRegex(ukmm.UkmmError, "too big"):
                ukmm.load_resource(rle_bomb(4000), None, "x")
        self.assertEqual(ukmm.MAX_RESOURCE, old)

    def test_blocks_bigger_than_zstd_allows_are_refused(self):
        """ukmm-zstd-block-max: an RLE or raw block, RLE literals or a block's sequences past 128 KB are refused, as
        zstd's own decoder refuses them (9 bytes made 2 MB in the pure decoder before)."""
        rle = frame([(1, b"B", (1 << 21) - 1)])
        raw = frame([(0, bytes(130 << 10), 130 << 10)])
        lits = (1 | 3 << 2 | (200 << 10) << 4).to_bytes(3, "little") + b"C" + b"\x00"   # RLE literals, 200 KB
        big_lits = frame([(2, lits, len(lits))])
        ok = long_matches(1)
        self.assertEqual(zstdec.decompress_py(ok), b"A" * 65540)
        for fn in (zstdec.decompress, zstdec.decompress_py):
            with self.subTest(fn=fn.__name__):
                self.assertEqual(fn(ok), b"A" * 65540)
                for data in (rle, raw, big_lits, long_matches(3)):
                    with self.assertRaises(zstdec.ZstdError):
                        fn(data)

    @unittest.skipUnless(zstdec._czstd, "Python's own zstd (3.14+) isn't here to compare with")
    def test_same_as_pythons_own_zstd(self):
        czstd = zstdec._czstd
        samples = [made_up_text(n, n) for n in (1, 50, 700, 40000, 300000)] + [made_up_bytes(9000, 5),
                                                                             bytes(70000) + made_up_bytes(500, 1)]
        for level in (1, 3, 9, 19):
            for data in samples:
                self.assertEqual(zstdec.decompress_py(czstd.compress(data, level=level)), data)
        zd = czstd.ZstdDict(TRAINED_DICT)
        d = zstdec.Dictionary(TRAINED_DICT)
        for data in samples[:4]:
            self.assertEqual(zstdec.decompress_py(czstd.compress(data, level=5, zstd_dict=zd), d), data)


# ---------------------------------------------------------------------------------------------------- formats

class Formats(unittest.TestCase):
    def test_cbor_and_byte_arrays(self):
        value = {"a": [1, -2, 3.5, None, True, False, "x"], 7: {"k": [b"\x00\x18" for _ in range(2)]}}
        enc = cbor(value)
        got, end = ukmm.cbor_decode(enc)
        self.assertEqual(end, len(enc))
        self.assertEqual(got["a"], [1, -2, 3.5, None, True, False, "x"])
        data = bytes(range(256)) + b"\x18\x18\x18\x17\x18" + bytes(3)
        enc = cbor(data)
        self.assertEqual(ukmm.u8_array(enc, 0, to_end=True), (data, len(enc)))
        self.assertEqual(ukmm.u8_array(enc + b"\x05", 0), (data, len(enc)))
        with self.assertRaises(ukmm.UkmmError):
            ukmm.u8_array(enc + b"\x05", 0, to_end=True)
        self.assertEqual(ukmm.load_resource(binary(data), None, "x").data, data)
        r = ukmm.load_resource(raw_frame(sarc_res({"a": False, "b": True}, 8)), None, "x")
        self.assertEqual((r.kind, r.files, r.align), ("sarc", {"a": False, "b": True}, 8))
        r = ukmm.load_resource(mergeable("BinaryOverride", [b"raw!", "why"]), None, "x")
        self.assertEqual((r.kind, r.data), ("binary", b"raw!"))
        with self.assertRaisesRegex(ukmm.UkmmError, "dictionary"):
            ukmm.load_resource(DICT_FRAME, None, "x")
        with self.assertRaisesRegex(ukmm.UkmmError, "can't be read"):
            ukmm.load_resource(cbor({"Nope": 1}), None, "x")
        self.assertEqual(ukmm.resource_kind(raw_frame(mergeable("Physics", {})), None), "merge:Physics")

    def test_meta_and_manifest(self):
        m = ukmm.parse_meta(META)
        self.assertEqual((m.name, m.version, m.author, m.platform), ("Made Up Mod", "1.2.0", "Someone", "switch"))
        self.assertEqual(m.description, "A made-up package.\n\nSecond paragraph.")
        self.assertEqual([(o.name, o.path, o.group, o.exclusive, o.required, o.default) for o in m.options],
                         [("Red", "Red", "Colour", True, True, True), ("Blue", "Blue", "Colour", True, True, False),
                          ("Extra Bits", "Extra", "Extras", False, False, False)])
        self.assertEqual(m.options[1].desc, 'Cold "blue"')
        self.assertEqual(m.options[2].requires, ["Red"])
        for text, want in (("platform: !Specific Wii U", "wiiu"), ("platform: Universal", "any"),
                           ("platform:\n  Specific: Switch", "switch")):
            self.assertEqual(ukmm.parse_meta(f"name: x\n{text}\n").platform, want)
        self.assertEqual(ukmm.parse_manifest("content:\n- A/B.sbfres\n- 'C/D E.txt'\naoc:\n- Pack/X.pack\n"),
                         (["A/B.sbfres", "C/D E.txt"], ["Pack/X.pack"]))
        with self.assertRaisesRegex(ukmm.UkmmError, "unsafe path"):
            ukmm.parse_manifest("content:\n- ../evil.txt\naoc: []\n")
        with self.assertRaisesRegex(ukmm.UkmmError, "plain folder"):
            ukmm.parse_meta("name: x\noption_groups:\n- type: Multiple\n  name: g\n  options:\n  - name: o\n"
                            "    path: ../up\n")

    def test_options(self):
        m = ukmm.parse_meta(META)
        names = lambda opts: [o.name for o in opts]  # noqa: E731
        self.assertEqual(names(ukmm.choose(m)), ["Red"])
        self.assertEqual(names(ukmm.choose(m, ["blue"])), ["Blue"])
        self.assertEqual(names(ukmm.choose(m, ["Extra"])), ["Red", "Extra Bits"])
        # ukmm-requires-exclusive: Extra Bits requires Red, and Blue is picked in Red's choose-one group: refused,
        # not both installed
        with self.assertRaisesRegex(ukmm.UkmmError, "'Extra Bits' needs 'Red', but 'Blue' is chosen in 'Colour'"):
            ukmm.choose(m, ["Blue", "Extra Bits"])
        self.assertEqual(names(ukmm.choose(m, ["Red", "Extra Bits"])), ["Red", "Extra Bits"])
        free = ukmm.parse_meta(META.replace("    requires:\n    - Red\n", "    requires: []\n"))
        self.assertEqual(names(ukmm.choose(free, ["Blue", "Extra Bits"])), ["Blue", "Extra Bits"])
        with self.assertRaisesRegex(ukmm.UkmmError, "needs an option of 'Colour'"):
            ukmm.choose(m, no_defaults=True)
        with self.assertRaisesRegex(ukmm.UkmmError, "Its options: 'Red', 'Blue', 'Extra Bits'"):
            ukmm.choose(m, ["Green"])

    def test_masters_are_read(self):
        """ukmm-masters: a package made on top of another mod names it (it was ignored before)."""
        self.assertEqual(ukmm.parse_meta(META).masters, [])
        m = ukmm.parse_meta(META.replace("masters: {}", "masters:\n  Base Mod:\n  - Someone\n  - 1.0\n"))
        self.assertEqual(m.masters, ["Base Mod"])
        self.assertIn("made on top of 'Base Mod'", "\n".join(m.summary()))

    def test_damaged_resources_are_refused_cleanly(self):
        """ukmm-malformed: a frame header cut short, a pack alignment that isn't a small power of two, a byte buffer
        that is a number and YAML nested too deep each give a clean refusal (a traceback, a 75 MB pack or a
        MemoryError before)."""
        cut = bytes.fromhex("28b52ffd03")
        self.assertEqual(ukmm.dict_id(cut), 0)
        self.assertEqual(ukmm.resource_kind(cut, None), "unreadable")
        with self.assertRaisesRegex(ukmm.UkmmError, "can't be decompressed"):
            ukmm.load_resource(cut, None, "x")
        for align in (1 << 40, 1 << 26, 3, -4, 0x4000):
            with self.subTest(align=align), self.assertRaisesRegex(ukmm.UkmmError, "aligned to"):
                ukmm.load_resource(cbor({"Sarc": {"alignment": align, "files": {}}}), None, "x")
        for align in (0, 1, 4, 0x80, 0x2000):
            self.assertEqual(ukmm.load_resource(sarc_res({}, align), None, "x").align, align)
        with self.assertRaisesRegex(ukmm.UkmmError, "isn't bytes"):
            ukmm.param_from({"BufferBinary": 1 << 40})
        self.assertEqual(ukmm.param_from({"BufferBinary": [1, 2, 255]}), (19, b"\x01\x02\xff"))
        self.assertEqual(ukmm.param_from({"BufferBinary": "ab"}), (19, b"ab"))
        deep = "name: x\nplatform: Universal\nextra:\n" + "".join(" " * (i + 1) + f"k{i}:\n" for i in range(1200))
        with self.assertRaisesRegex(ukmm.UkmmError, "nests too deep"):
            ukmm.parse_meta(deep)
        import time
        t = time.time()
        many = ukmm.yaml_load("list:\n" + "- a: b\n  c: d\n" * 60000)
        self.assertLess(time.time() - t, 10)                   # (each "- a: b" line copied the list before)
        self.assertEqual((len(many["list"]), many["list"][-1]), (60000, {"a": "b", "c": "d"}))

    def test_canonical_names_and_yaz0(self):
        self.assertEqual(ukmm.canonical("Actor/Pack/Foo.sbactorpack"), "Actor/Pack/Foo.bactorpack")
        self.assertEqual(ukmm.canonical("01007EF00011F001/romfs/Pack/AocMainField.pack"),
                         "Aoc/0010/Pack/AocMainField.pack")
        self.assertTrue(ukmm.compressed_name("Model/A.sbfres"))
        self.assertFalse(ukmm.compressed_name("Pack/A.sarc") or ukmm.compressed_name("Pack/Title.pack"))
        for n in (0, 1, 8, 9, 1000):
            data = made_up_bytes(n, n + 1)
            stored = ukmm.yaz0_stored(data, 0x80)
            self.assertEqual(wc.yaz0_decompress(stored), (data, 0x80))
        self.assertEqual(len(ukmm.yaz0(bytes(1 << 17))), 16 + (1 << 17) + (1 << 14))      # (big: stored)
        self.assertLess(len(ukmm.yaz0(bytes(1000))), 100)                                   # (small: compressed)

    def test_dictionary_found_in_a_program_by_its_header_and_hash(self):
        tmp = Path(tempfile.mkdtemp(prefix="ukmm-dict-"))
        self.addCleanup(lambda: mm.remove_tree(str(tmp)))
        exe = tmp / "ukmm.exe"
        exe.write_bytes(b"MZ" + bytes(5000) + TRAINED_DICT[:8] + b"decoy" + bytes(100) + TRAINED_DICT + b"tail")
        self.assertIsNone(ukmm.dictionary_from(str(exe)))                      # (not UKMM's: its hash differs)
        import hashlib
        with mock.patch.multiple(ukmm, DICT_HEAD=TRAINED_DICT[:8], DICT_SIZE=len(TRAINED_DICT),
                                 DICT_SHA256=hashlib.sha256(TRAINED_DICT).hexdigest()):
            d = ukmm.dictionary_from(str(exe))
            self.assertEqual(d.id, 0x74E62405)
            with mock.patch.dict(os.environ, {"UKMM_DICT": str(exe)}):
                self.assertEqual(ukmm.find_dictionary()[1], str(exe))


# ---------------------------------------------------------------------------------------------------- installing

class Install(Base):
    def test_packs_merges_options_and_size_table(self):
        res, stage = self.install(package())
        self.assertEqual([o.name for o in res.chosen], ["Red"])
        # the actor pack: one file added, one removed, the rest from the game, parameter files merged
        foo = self.pack(stage, "Actor/Pack/Foo.sbactorpack")
        self.assertNotIn("Actor/Misc/Keep.bin", foo)
        self.assertEqual(foo["Actor/Misc/New.bin"], b"FRES-like new file " * 3)
        link = bnp.aamp_read(foo["Actor/ActorLink/Foo.bxml"]).root
        targets = link.objs[crc("LinkTarget")].params
        self.assertEqual((targets[crc("ModelUser")], targets[crc("PhysicsUser")]), (ref("FooMod"), ref("Foo")))
        self.assertEqual(list(link.objs[crc("Tags")].params.values()), [ref("A"), ref("C")])
        general = bnp.aamp_read(foo["Actor/GeneralParamList/Foo.bgparamlist"]).root.objs[crc("General")].params
        self.assertEqual((general[crc("Speed")], general[crc("Life")]), (f32(2.0), f32(9.0)))   # (base, then Red)
        ml = bnp.aamp_read(foo["Actor/ModelList/Foo.bmodellist"]).root
        md = ml.lists[crc("ModelData")].lists[crc("ModelData_0")]
        self.assertEqual(md.objs[crc("Base")].params[crc("Folder")], s64("FooMod"))
        units = [o.params[crc("UnitName")] for o in md.lists[crc("Unit")].objs.values()]
        self.assertEqual(units, [s64("Body"), s64("Cape")])
        self.assertEqual(ml.objs[crc("Locator_0")].params[crc("Pos")], (4, struct.pack("<3f", 1, 2, 3)))
        self.assertEqual(ml.objs[crc("Attention")].params[crc("CursorOffsetY")], f32(0.5))
        self.assertEqual(foo["Actor/Physics/Foo.bphysics"], aamp({"Rigid": {"Mass": f32(10)}}))   # (not merged)
        # the title pack: no resource of its own, rebuilt because a file deep inside it changes
        title = self.pack(stage, "Pack/Title.pack")
        self.assertEqual(title["Other/Thing.txt"], b"thing")
        player = {n: (c.data() if isinstance(c, bnp.Blob) else c)
                  for n, (c, _) in bnp.Blob(title["Actor/Pack/Player.sbactorpack"]).open().items()}
        self.assertEqual(player["Actor/AS/Idle.bas"], aamp({"Idle": {"X": f32(1)}}))
        asl = bnp.aamp_read(player["Actor/ASList/Player.baslist"]).root
        adds = [(o.params[crc("Anim")], o.params.get(crc("RetargetNoCorrect")))
                for o in asl.lists[crc("AddReses")].objs.values()]
        self.assertEqual(adds, [(s64("Basic"), None), (s64("Glide"), (BOOL, struct.pack("<I", 1)))])
        defs = [(o.params[crc("Name")], o.params[crc("Filename")]) for o in asl.lists[crc("ASDefines")].objs.values()]
        self.assertEqual(defs, [(s64("Wait"), s64("Player_Wait")), (s64("Run"), s64("Player_RunFast")),
                                (s64("Dash"), s64("Player_Dash"))])
        self.assertEqual(asl.objs[crc("Common")].params[crc("Speed")], f32(1))
        # binaries: compressed as their names say, the Extra option's file left out
        self.assertEqual(wc.yaz0_decompress((stage / R / "Model/Old.sbfres").read_bytes())[0], b"FRES" + bytes(200))
        self.assertTrue((stage / R / "Model/New.sbfres").is_file())
        self.assertFalse((stage / R / "Model/Extra.sbfres").exists())
        # the size table: grown entries for files the game's lists; new files estimated from the game's files of
        # their kind (Model/New.bfres, 34 bytes, from Model/Old.sbfres: 64 bytes, entry 300; the larger of its
        # overhead kept (300) and its entry in proportion (160), plus a quarter: 375, rounded up to 384)
        table = bnp.Rstb((stage / R / rm.RSTB).read_bytes())
        self.assertGreater(table.get("Model/Old.bfres"), 300)
        self.assertGreater(table.get("Actor/Pack/Foo.bactorpack"), 9000)
        self.assertEqual(table.get("Model/New.bfres"), 384)
        self.assertEqual(res.rstb_new, 1)
        # (Actor/Misc/New.bin: the game has no .bin with a size table entry to go by: named)
        self.assertIn("1 new file(s) got no size table entry (Actor/Pack/Foo.sbactorpack//Actor/Misc/New.bin)",
                      "\n".join(res.summary()))
        self.assertEqual(table.get("Actor/Pack/Player.bactorpack"), 7000 + bnp.round32(len(player_raw(title)))
                         - bnp.round32(len(player_raw(sarc_of_game(self.game)))))
        # what wasn't merged is named, and the library keeps it
        text = "\n".join(res.summary())
        self.assertIn("1 physics file(s) (Actor/Pack/Foo.sbactorpack//Actor/Physics/Foo.bphysics) not merged", text)
        self.assertEqual(res.summary()[1], "1 of 10 resource(s) are kinds this loader can't merge yet (1 physics): "
                                           "the parts of the mod they make are left out")
        stored = ukmm.stored(str(stage))
        self.assertEqual(stored["options"], ["Red"])
        self.assertTrue(any("physics" in p for p in ukmm.stored_problems(str(stage))))

    def test_options_picked_and_from_a_folder(self):
        meta = META.replace("    requires:\n    - Red\n", "    requires: []\n")
        res, stage = self.install(package(meta), picks=["Blue", "Extra Bits"], zipped=False)
        self.assertEqual([o.name for o in res.chosen], ["Blue", "Extra Bits"])
        self.assertTrue((stage / R / "Model/Extra.sbfres").is_file())
        general = bnp.aamp_read(self.pack(stage, "Actor/Pack/Foo.sbactorpack")[
            "Actor/GeneralParamList/Foo.bgparamlist"]).root.objs[crc("General")].params
        self.assertEqual(general[crc("Life")], f32(1.0))                    # (Blue's, not Red's)

    def test_zstd_resources_and_the_dictionary(self):
        files = package(**{"Model/New.bfres": raw_frame(binary(b"FRES" + bytes(30)))})
        res, stage = self.install(files)
        self.assertEqual(wc.yaz0_decompress((stage / R / "Model/New.sbfres").read_bytes())[0], b"FRES" + bytes(30))
        files = package(**{"Model/New.bfres": DICT_FRAME})
        with mock.patch.object(ukmm, "find_dictionary", return_value=(None, None)):
            with self.assertRaisesRegex(ukmm.UkmmError, "UKMM's dictionary"):
                self.install(files)

    def test_new_pack_with_files_shared_with_the_game(self):
        files = package(**{
            "manifest.yml": "content:\n- Actor/Pack/Bar.sbactorpack\naoc: []\n",
            "Actor/Pack/Bar.bactorpack": sarc_res({"Actor/ActorLink/Foo.bxml": False, "Actor/Misc/Bar.bin": False,
                                                   "Actor/Misc/Gone.bin": False}),
            "Actor/Misc/Bar.bin": binary(b"bar"), "Actor/ActorLink/Foo.bxml": None})
        res, stage = self.install(files)
        bar = self.pack(stage, "Actor/Pack/Bar.sbactorpack")
        self.assertEqual(bar["Actor/ActorLink/Foo.bxml"], actor_link())        # (found in the game's Foo pack)
        self.assertEqual(sorted(bar), ["Actor/ActorLink/Foo.bxml", "Actor/Misc/Bar.bin"])
        self.assertIn("neither the package nor your game has (Actor/Pack/Bar.sbactorpack//Actor/Misc/Gone.bin)",
                      "\n".join(res.summary()))

    def test_texts_and_unknown_types_are_named(self):
        files = package(**{
            "manifest.yml": "content:\n- Pack/Bootup_USen.pack\n- Actor/Pack/Foo.sbactorpack\naoc: []\n",
            "Message/Msg_USen.product.sarc": mergeable("MessagePack", {}),
            "Actor/Pack/Foo.bactorpack": sarc_res({"Actor/AS/New.bas": False}),
            "Actor/AS/New.bas": mergeable("AnimSeq", {"elements": {}})})
        res, stage = self.install(files)
        text = "\n".join(res.summary())
        self.assertIn("texts (Pack/Bootup_USen.pack) not merged", text)
        self.assertIn("1 animation sequences file(s) (Actor/Pack/Foo.sbactorpack//Actor/AS/New.bas) not merged", text)
        self.assertNotIn("Actor/AS/New.bas", self.pack(stage, "Actor/Pack/Foo.sbactorpack"))
        self.assertNotIn("neither the package nor your game has", text)          # (named once, as not merged)
        self.assertFalse((stage / R / "Pack/Bootup_USen.pack").exists())

    def test_wii_u_unknown_platform_and_missing_game(self):
        with self.assertRaisesRegex(ukmm.UkmmError, "Wii U UKMM package"):
            self.install(package(META.replace("!Specific Switch", "!Specific Wii U")))
        with self.assertRaisesRegex(ukmm.UkmmError, "platform this loader doesn't know"):
            self.install(package(META.replace("!Specific Switch", "Moon")))
        mm.remove_tree(str(self.game / "romfs"))
        with self.assertRaisesRegex(ukmm.UkmmError, "game files aren't set up"):
            self.install(package())

    def test_nothing_grows_no_size_table(self):
        files = {"meta.yml": NO_OPTIONS, "manifest.yml": "content:\n- Model/Old.sbfres\naoc: []\n",
                 "Model/Old.bfres": binary(b"FRES smaller")}
        res, stage = self.install(files)
        self.assertTrue((stage / R / "Model/Old.sbfres").is_file())
        self.assertFalse((stage / R / rm.RSTB).exists())
        self.assertIn("size table: no changes", "\n".join(res.summary()))

    def test_new_files_get_estimated_sizes(self):
        """ukmm-rstb-new-files: a new file gets a size table entry estimated from the game's files of its kind (it got
        none before): a pack its bytes plus a game pack's overhead, another file the largest estimate from the game
        files of its kind nearest in size, plus a quarter. A new file of a kind the game doesn't have is named."""
        new_link = aamp({"LinkTarget": {"ModelUser": ref("Bar")}})
        files = {"meta.yml": NO_OPTIONS,
                 "manifest.yml": "content:\n- Actor/Pack/Foo.sbactorpack\n- Actor/Pack/Bar.sbactorpack\n"
                                 "- Model/Big.sbfres\n- Effect/New.sesetlist\naoc: []\n",
                 "Actor/Pack/Foo.bactorpack": sarc_res({"Actor/ActorLink/Extra.bxml": False}),
                 "Actor/ActorLink/Extra.bxml": binary(new_link),
                 "Actor/Pack/Bar.bactorpack": sarc_res({"Actor/Misc/Bar.bin": False}),
                 "Actor/Misc/Bar.bin": binary(b"bar" * 50),
                 "Model/Big.bfres": binary(b"FRES" + bytes(5000)),
                 "Effect/New.esetlist": binary(b"effects")}
        res, stage = self.install(files)
        table = bnp.Rstb((stage / R / rm.RSTB).read_bytes())
        margin = lambda n: bnp.round32(math.ceil(n * ukmm.KIN_MARGIN))  # noqa: E731
        # a parameter file in a game pack: from the game's Foo.bxml next to it (entry 1000)
        self.assertEqual(table.get("Actor/ActorLink/Extra.bxml"),
                         margin(ukmm.estimate_entry(len(actor_link()), 1000, len(new_link), False)))
        # a new actor pack: its bytes plus the overhead of the game's Foo pack (entry 9000)
        game_foo = wc.yaz0_decompress((self.game / "romfs/Actor/Pack/Foo.sbactorpack").read_bytes())[0]
        bar = wc.yaz0_decompress((stage / R / "Actor/Pack/Bar.sbactorpack").read_bytes())[0]
        self.assertEqual(table.get("Actor/Pack/Bar.bactorpack"),
                         bnp.round32(len(bar)) + 9000 - bnp.round32(len(game_foo)))
        # a model 80 times the game's only one: its overhead kept, not grown in proportion
        self.assertEqual(table.get("Model/Big.bfres"), margin(bnp.round32(5004) + 300 - bnp.round32(64)))
        self.assertEqual(res.rstb_new, 3)
        text = "\n".join(res.summary())
        self.assertIn("size table: 4 entries (3 estimated for new files)", text)       # (and Foo's grown one)
        self.assertIn("2 new file(s) got no size table entry (Actor/Pack/Bar.sbactorpack//Actor/Misc/Bar.bin, "
                      "Effect/New.sesetlist)", text)
        self.assertEqual(sorted(ukmm.stored(str(stage))["rstb_new"]),
                         ["Actor/ActorLink/Extra.bxml", "Actor/Pack/Bar.bactorpack", "Model/Big.bfres"])

    def test_game_pack_with_nothing_of_the_package_is_left_alone(self):
        """ukmm-noop-pack: a game pack the package changes nothing in that this loader can use (its only changes are
        kinds not merged) isn't written, and no size table ships for it (a game-only copy shipped before)."""
        for files in ({"manifest.yml": "content:\n- Pack/Title.pack\naoc: []\n",
                       "Actor/AS/Idle.bas": mergeable("AnimSeq", {"elements": {}})},
                      {"manifest.yml": "content:\n- Actor/Pack/Foo.sbactorpack\naoc: []\n",
                       "Actor/Pack/Foo.bactorpack": sarc_res({"Actor/Physics/Foo.bphysics": False}),
                       "Actor/Physics/Foo.bphysics": mergeable("Physics", {"made": "up"})}):
            with self.subTest(files=sorted(files)):
                if (self.tmp / "stage").exists():
                    mm.remove_tree(str(self.tmp / "stage"))
                res, stage = self.install({"meta.yml": NO_OPTIONS, **files})
                self.assertEqual(res.files, 0)
                self.assertFalse((stage / TID).exists())
                self.assertEqual(res.from_game, 0)
                text = "\n".join(res.summary())
                self.assertIn("size table: no changes", text)
                self.assertIn(f"1 of {len(files) - 1} resource(s) are kinds this loader can't merge yet", text)

    def test_masters_named(self):
        meta = NO_OPTIONS.replace("masters: {}", "masters:\n  Base Mod:\n  - Someone\n  - 1.0\n")
        res, _ = self.install({"meta.yml": meta, "manifest.yml": "content:\n- Model/New.sbfres\naoc: []\n",
                               "Model/New.bfres": binary(b"new")})
        self.assertIn("needs attention: made on top of 'Base Mod'", "\n".join(res.summary()))

    def test_install_size_is_capped(self):
        """ukmm-install-cap: an install's files are capped in all, and by the disk's free space (only each resource
        was capped before: 9.7 KB of package wrote 288 MB)."""
        files = {"meta.yml": NO_OPTIONS, "manifest.yml": "content:\n- Model/A.sbfres\n- Model/B.sbfres\naoc: []\n",
                 "Model/A.bfres": binary(made_up_bytes(40000, 1)), "Model/B.bfres": binary(made_up_bytes(40000, 2))}
        with mock.patch.object(ukmm, "MAX_INSTALL", 60000):           # (each file is about 45 KB)
            with self.assertRaisesRegex(ukmm.UkmmError, "builds more than"):
                self.install(files)
        least = sum(len(v if isinstance(v, bytes) else v.encode()) for v in files.values()) // 2   # (about 77 KB)
        with mock.patch.object(ukmm, "_free_space", return_value=ukmm.SPARE_SPACE + least + 1000):
            with self.assertRaisesRegex(ukmm.UkmmError, "free on the disk"):
                self.install(files)
        with mock.patch.object(ukmm, "_free_space", return_value=1000):
            with self.assertRaisesRegex(ukmm.UkmmError, "not enough free space"):
                self.install(files)


NO_OPTIONS = META.replace("option_groups:", "option_groups: []\nold_groups:")


def sarc_of_game(game: Path) -> bytes:
    return (game / "romfs/Pack/Title.pack").read_bytes()


def player_raw(title: bytes | dict) -> bytes:
    data = title["Actor/Pack/Player.sbactorpack"] if isinstance(title, dict) else \
        bnp.Blob(title).open()["Actor/Pack/Player.sbactorpack"][0]
    return wc.yaz0_decompress(data)[0]


# ---------------------------------------------------------------------------------------------------- modman

def fake_install(inst: Path) -> Path:
    return write(inst, {"savagewind.exe": b"MZ made-up exe", "extracted/swgame.dll": b"made-up game code",
                        "extracted/swgame.dll.id": "test-id\n", "extracted/romfs/System/Version.txt": "made up",
                        **{f"extracted/{k}": v for k, v in game_files().items()}})


class Modman(Base):
    def setUp(self):
        super().setUp()
        self.install_dir = fake_install(self.tmp / "SavageWind" / "Savage Wind")
        self.libdir = self.tmp / "SavageWind" / "modloader"
        mm.Library(str(self.libdir), str(self.install_dir)).save()
        for p in (mock.patch.object(mm, "game_running", return_value=False),
                  mock.patch.object(mm, "MODHOST_DIR", str(self.tmp / "no-modhost"))):
            p.start()
            self.addCleanup(p.stop)

    @property
    def lib(self):
        return mm.Library.load(str(self.libdir))

    def test_add_and_deploy_a_package(self):
        src = make_zip(self.tmp / "downloads" / "made_up.zip", package())
        (entry, scan, notes), _ = quiet(mm.add_mod, self.lib, str(src), bnp_options=["Blue"])
        self.assertEqual((entry["name"], entry["version"], entry["ukmm"]["options"]), ("Made Up Mod", "1.2.0",
                                                                                       ["Blue"]))
        self.assertIn("romfs", entry["kinds"])
        text = "\n".join(notes)
        self.assertIn("UKMM package Made Up Mod 1.2.0: options Blue (of 3)", text)
        self.assertIn("1 of 10 resource(s) are kinds this loader can't merge yet (1 physics)", text)
        self.assertIn("needs attention: 1 physics file(s)", text)
        launch, _ = quiet(mm.deploy, self.lib)
        overlay = Path(launch["parts"]["overlay"]["path"])
        self.assertTrue((overlay / "Model" / "New.sbfres").is_file())
        self.assertFalse(any(".ukmm" in str(p) for p in overlay.rglob("*")))
        self.assertTrue(any("physics" in a for a in launch["attention"]))

    def test_cli_options_and_wii_u_refusal(self):
        src = make_zip(self.tmp / "downloads" / "made_up.zip", package())
        _, out = quiet(mm.main, ["--library", str(self.libdir), "add", str(src), "--no-options", "--option", "Red"])
        self.assertEqual(self.lib.mods[-1]["ukmm"]["options"], ["Red"])
        wiiu = make_zip(self.tmp / "downloads" / "wiiu.zip", package(META.replace("!Specific Switch",
                                                                                  "!Specific Wii U")))
        code, out = quiet(mm.main, ["--library", str(self.libdir), "add", str(wiiu)])
        self.assertEqual(code, 2)
        self.assertIn("Wii U UKMM package", out)

    def test_info_command(self):
        src = make_zip(self.tmp / "downloads" / "made_up.zip", package())
        code, out = quiet(ukmm.main, ["info", str(src)])
        self.assertEqual(code, 0)
        self.assertIn("Made Up Mod 1.2.0 (Switch, UKMM 0.17.1)", out)
        self.assertIn("1 not merged: Physics", out)
        self.assertIn("one of 'Colour': Red (folder Red) [default]", out)
        self.assertEqual(out.splitlines()[1], "  1 of 12 resource(s) are kinds this loader can't merge yet "
                                              "(1 physics): the parts of the mod they make are left out (all "
                                              "options counted)")

    def test_damaged_packages_are_refused_cleanly(self):
        """ukmm-malformed: modman add and ukmm.py info refuse a damaged package with a message, not a traceback."""
        deep = "".join(" " * (i + 1) + f"k{i}:\n" for i in range(1200))
        cases = {"cut": ({"Model/New.bfres": bytes.fromhex("28b52ffd03")}, "can't be decompressed"),
                 "align": ({"Actor/Pack/Foo.bactorpack": cbor({"Sarc": {"alignment": 1 << 40, "files": {}}})},
                           "aligned to"),
                 "deep": ({"meta.yml": NO_OPTIONS + "extra:\n" + deep}, "nests too deep")}
        for label, (over, want) in cases.items():
            with self.subTest(label):
                files = {"meta.yml": NO_OPTIONS, "manifest.yml": "content:\n- Model/New.sbfres\n"
                         "- Actor/Pack/Foo.sbactorpack\naoc: []\n", "Model/New.bfres": binary(b"new"), **over}
                src = make_zip(self.tmp / "downloads" / f"{label}.zip", files)
                code, out = quiet(mm.main, ["--library", str(self.libdir), "add", str(src)])
                self.assertEqual(code, 2)
                self.assertIn(want, out)
                self.assertNotIn("Traceback", out)
        src = make_zip(self.tmp / "downloads" / "cut-info.zip", {
            "meta.yml": NO_OPTIONS, "manifest.yml": "content:\n- Model/New.sbfres\naoc: []\n",
            "Model/New.bfres": bytes.fromhex("28b52ffd03")})
        code, out = quiet(ukmm.main, ["info", str(src)])
        self.assertEqual(code, 0)
        self.assertIn("1 unreadable", out)
        self.assertEqual(self.lib.mods, [])


if __name__ == "__main__":
    unittest.main()
