"""Tests for tools/wiiu_convert.py. Every file here is made up in the test (tiny SARC, BYML, MSBT, RSTB, AAMP and
Yaz0 files written by the small writers below, in both byte orders): no game files."""
import contextlib
import io
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import romfs_mods as rm  # noqa: E402
import wiiu_convert as wc  # noqa: E402

BE, LE = wc.BE, wc.LE


# ------------------------------------------------------------------------------------------------ made-up files

def yaz0_stored(data: bytes, align: int = 0) -> bytes:
    """Valid Yaz0 with no compression at all (every byte a literal)."""
    out = bytearray(b"Yaz0" + struct.pack(">II", len(data), align) + bytes(4))
    for i in range(0, len(data), 8):
        out += b"\xff" + data[i:i + 8]
    return bytes(out)


class U:  # BYML uint
    def __init__(self, v): self.v = v


class I64:
    def __init__(self, v): self.v = v


class U64:
    def __init__(self, v): self.v = v


class Dbl:
    def __init__(self, v): self.v = v


def byml(root, order: str, version: int = 2) -> bytes:
    """A BYML document (v2 layout) in either byte order. The same list/dict object used twice is written once."""
    keys, strings = set(), set()

    def scan(v):
        if isinstance(v, dict):
            keys.update(v)
            for x in v.values():
                scan(x)
        elif isinstance(v, list):
            for x in v:
                scan(x)
        elif isinstance(v, str):
            strings.add(v)
    scan(root)
    keys, strings = sorted(keys, key=str.encode), sorted(strings, key=str.encode)
    buf = bytearray(16)

    def pad4():
        buf.extend(bytes(-len(buf) % 4))

    def table(items):
        if not items:
            return 0
        pad4()
        at = len(buf)
        buf.extend(bytes([0xC2]) + _u24(len(items), order))
        offs = []
        body = bytearray()
        start = 4 + 4 * (len(items) + 1)
        for s in items:
            offs.append(start + len(body))
            body += s.encode() + b"\0"
        offs.append(start + len(body))
        buf.extend(struct.pack(order + f"{len(offs)}I", *offs) + body)
        return at

    key_off, str_off = table(keys), table(strings)
    done = {}

    def typed(v):
        if v is None:
            return 0xFF
        if isinstance(v, bool):
            return 0xD0
        if isinstance(v, int):
            return 0xD1
        if isinstance(v, float):
            return 0xD2
        if isinstance(v, str):
            return 0xA0
        if isinstance(v, bytes):
            return 0xA1
        if isinstance(v, list):
            return 0xC0
        if isinstance(v, dict):
            return 0xC1
        return {U: 0xD3, I64: 0xD4, U64: 0xD5, Dbl: 0xD6}[type(v)]

    def container(v) -> int:
        if id(v) in done:
            return done[id(v)]
        pad4()
        at = len(buf)
        done[id(v)] = at
        items = list(v.items()) if isinstance(v, dict) else list(enumerate(v))
        if isinstance(v, list):
            buf.extend(bytes([0xC0]) + _u24(len(v), order) + bytes(typed(x) for x in v))
            pad4()
            slots = [len(buf) + 4 * k for k in range(len(v))]
            buf.extend(bytes(4 * len(v)))
        else:
            buf.extend(bytes([0xC1]) + _u24(len(v), order))
            slots = []
            for k, x in v.items():
                buf.extend(_u24(keys.index(k), order) + bytes([typed(x)]))
                slots.append(len(buf))
                buf.extend(bytes(4))
        for slot, (_, x) in zip(slots, items):
            struct.pack_into(order + "I", buf, slot, value(x))
        return at

    def value(x) -> int:
        t = typed(x)
        if t == 0xFF:
            return 0
        if t == 0xD0:
            return int(x)
        if t == 0xD1:
            return x & 0xFFFFFFFF
        if t == 0xD2:
            return struct.unpack("<I", struct.pack("<f", x))[0]
        if t == 0xD3:
            return x.v
        if t == 0xA0:
            return strings.index(x)
        if t in (0xC0, 0xC1):
            return container(x)
        pad4()
        at = len(buf)
        if t == 0xA1:
            buf.extend(struct.pack(order + "I", len(x)) + x)
        elif t == 0xD6:
            buf.extend(struct.pack(order + "d", x.v))
        else:
            buf.extend(struct.pack(order + ("q" if t == 0xD4 else "Q"), x.v))
        return at

    root_off = container(root) if root is not None else 0
    buf[0:2] = b"BY" if order == BE else b"YB"
    struct.pack_into(order + "HIII", buf, 2, version, key_off, str_off, root_off)
    return bytes(buf)


def _u24(n: int, order: str) -> bytes:
    b = n.to_bytes(3, "big")
    return b if order == BE else b[::-1]


def msbt(order: str, labels: dict, attrs: list, texts: list, *, extra: bytes = b"", encoding: int = 1) -> bytes:
    """An MSBT with LBL1 (3 slots), ATR1 (string attributes, or none) and TXT2. A text is a list of str and tags:
    ("tag", group, type, [(unit, value)]) with units h (16-bit), i (32-bit), b (raw bytes), s (string with its
    16-bit byte length), or ("end", group, type)."""
    enc = "utf-16-be" if order == BE else "utf-16-le"

    def section(magic: bytes, body: bytes) -> bytes:
        out = magic + struct.pack(order + "I", len(body)) + bytes(8) + body
        return out + b"\xab" * (-len(out) % 16)

    slots = [[] for _ in range(3)]
    for name, idx in labels.items():
        slots[sum(name.encode()) % 3].append((name, idx))
    lbl = bytearray(struct.pack(order + "I", 3))
    data = bytearray()
    for group in slots:
        lbl += struct.pack(order + "II", len(group), 4 + 8 * 3 + len(data))
        for name, idx in group:
            data += bytes([len(name)]) + name.encode() + struct.pack(order + "I", idx)
    lbl += data
    if attrs:
        table_end = 8 + 4 * len(attrs)
        strs, offs = bytearray(), []
        for a in attrs:
            offs.append(table_end + len(strs))
            strs += (a + "\0").encode(enc)
        atr = struct.pack(order + "II", len(attrs), 4) + struct.pack(order + f"{len(offs)}I", *offs) + strs
    else:
        atr = struct.pack(order + "II", len(texts), 0)
    bodies = []
    for text in texts:
        b = bytearray()
        for tok in text:
            if isinstance(tok, str):
                b += tok.encode(enc)
            elif tok[0] == "end":
                b += struct.pack(order + "HHH", 0x0F, tok[1], tok[2])
            else:
                p = bytearray()
                for unit, v in tok[3]:
                    if unit == "h":
                        p += struct.pack(order + "H", v)
                    elif unit == "i":
                        p += struct.pack(order + "I", v)
                    elif unit == "b":
                        p += v
                    else:
                        s = v.encode(enc)
                        p += struct.pack(order + "H", len(s)) + s
                b += struct.pack(order + "HHHH", 0x0E, tok[1], tok[2], len(p)) + p
        b += "\0".encode(enc)
        bodies.append(bytes(b))
    offs, pos = [], 4 + 4 * len(texts)
    for b in bodies:
        offs.append(pos)
        pos += len(b)
    txt = struct.pack(order + "I", len(texts)) + struct.pack(order + f"{len(offs)}I", *offs) + b"".join(bodies)
    secs = section(b"LBL1", bytes(lbl)) + section(b"ATR1", atr) + section(b"TXT2", txt) + extra
    count = 3 + (1 if extra else 0)
    bom = b"\xfe\xff" if order == BE else b"\xff\xfe"
    head = b"MsgStdBn" + bom + struct.pack(order + "H", 0) + bytes([encoding, 3]) + struct.pack(order + "HH", count, 0)
    head += struct.pack(order + "I", 0x20 + len(secs)) + bytes(10)
    return head + secs


def rstb(order: str, crcs: dict, names: dict) -> bytes:
    out = b"RSTB" + struct.pack(order + "II", len(crcs), len(names))
    for c in sorted(crcs):
        out += struct.pack(order + "II", c, crcs[c])
    for n, size in names.items():
        out += n.encode().ljust(128, b"\0") + struct.pack(order + "I", size)
    return out


AAMP_LE = b"AAMP" + struct.pack("<III", 2, 3, 0x40) + bytes(0x30)
AAMP_BE = b"AAMP" + struct.pack(">III", 2, 2, 0x40) + bytes(0x30)
FRES = b"FRES    " + bytes(0x40)

DOC = {"Name": "Lynel", "Flags": [True, False, None], "Hp": 2000, "Neg": -5, "Speed": 1.5, "Id": U(0xDEADBEEF),
       "Big": I64(-(2 ** 40)), "Huge": U64(2 ** 63 + 5), "Pi": Dbl(3.25), "Blob": b"\x01\x02\x03\x04\x05",
       "Objs": [{"Name": "a", "Pos": [1.0, 2.0, 3.0]}, {"Name": "b", "Pos": [4.0, 5.0, 6.0]}],
       "Mixed": ["x", 7, U(8), [], {}]}
TEXTS = [
    ["Hello ", ("tag", 0, 3, [("h", 2)]), "red", ("tag", 0, 3, [("h", 0xFFFF)])],
    [("tag", 1, 0, [("i", 30)]), ("tag", 1, 1, [("i", 0x3F000000)]), "Pick:",
     ("tag", 1, 4, [("h", 0x157C), ("h", 0x157D), ("b", b"\x00\x01")])],
    [("tag", 1, 7, [("b", b"\x27\xcd")]), ("tag", 4, 2, [("s", "Ease_00")]), ("tag", 2, 9, [("s", "Var"), ("h", 0)]),
     ("tag", 201, 0, [("b", b"\x02\x03\x03\x01")]), ("tag", 5, 1, []), ("end", 0, 3), "end"],
]
LABELS = {"Talk00": 0, "Talk01": 1, "Choice": 2}
ATTRS = ["", "Npc_A", "Npc_B"]


def run_cli(*args):
    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
        code = wc.main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


def make(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return root


# ------------------------------------------------------------------------------------------------------- tests

class Yaz0(unittest.TestCase):
    def test_hand_made_stream(self):
        # literals a b c, then a 6-byte copy 3 back (overlapping), then a 0x20-byte copy 1 back (3-byte form)
        stream = b"Yaz0" + struct.pack(">II", 41, 0x80) + bytes(4) + bytes([0b11100000]) + b"abc"
        stream += bytes([0x40, 0x02, 0x00, 0x00, 0x20 - 0x12])
        data, align = wc.yaz0_decompress(stream)
        self.assertEqual(data, b"abcabcabc" + b"c" * 0x20)
        self.assertEqual(align, 0x80)

    def test_round_trip(self):
        import random
        rnd = random.Random(5)
        samples = [b"", b"a", b"ab", b"abc", b"a" * 1000, bytes(range(256)) * 40,
                   bytes(rnd.randrange(4) for _ in range(20000)), bytes(rnd.randrange(256) for _ in range(5000)),
                   b"xyz" * 300 + bytes(rnd.randrange(256) for _ in range(300)) + b"xyz" * 300]
        for data in samples:
            packed = wc.yaz0_compress(data, 0x2000)
            self.assertEqual(packed[:4], b"Yaz0")
            self.assertEqual(struct.unpack(">II", packed[4:12]), (len(data), 0x2000))
            self.assertEqual(wc.yaz0_decompress(packed), (data, 0x2000))
        self.assertLess(len(wc.yaz0_compress(b"a" * 1000)), 40)

    def test_cut_short_and_bad(self):
        packed = wc.yaz0_compress(bytes(range(256)) * 4)
        with self.assertRaises(wc.FormatError):
            wc.yaz0_decompress(packed[:40])
        with self.assertRaises(wc.FormatError):  # a copy from before the start
            wc.yaz0_decompress(b"Yaz0" + struct.pack(">II", 4, 0) + bytes(4) + bytes([0]) + b"\x10\x05")
        with self.assertRaises(wc.FormatError):
            wc.yaz0_decompress(b"Yaz1" + bytes(12))


class Sarc(unittest.TestCase):
    FILES = [("Actor/Physics/A.bphysics", AAMP_LE), ("Actor/AnimationInfo/A.baniminfo", byml(DOC, LE)),
             ("Model/A.bfres", FRES), ("Readme.txt", b"hello")]

    def test_header_fields_and_round_trip(self):
        le = wc.sarc_build(self.FILES, LE)
        conv = wc.Converter(BE)
        r = conv.file(le, "A.pack")
        self.assertEqual(r.status, wc.PARTLY)  # the bfres stays a Wii U... (here: Switch) file
        be = r.data
        self.assertEqual(len(be), len(le))
        self.assertEqual(be[6:8], b"\xfe\xff")
        self.assertEqual(struct.unpack(">HHIIH", be[4:18])[2:], struct.unpack("<HHIIH", le[4:18])[2:])
        self.assertEqual(be[0x14:0x18], b"SFAT")
        self.assertEqual(struct.unpack(">I", be[0x18 + 4:0x18 + 8]), (0x65,))
        s_le, s_be = wc.sarc_parse(le), wc.sarc_parse(be)
        self.assertEqual([(n.name, n.hash, n.attr, n.start, n.end) for n in s_le.nodes],
                         [(n.name, n.hash, n.attr, n.start, n.end) for n in s_be.nodes])
        files_be = dict(wc.sarc_files(be))
        self.assertEqual(files_be["Actor/AnimationInfo/A.baniminfo"], byml(DOC, BE))
        self.assertEqual(files_be["Actor/Physics/A.bphysics"], AAMP_LE)
        self.assertEqual(files_be["Model/A.bfres"], FRES)
        self.assertEqual(wc.Converter(LE).file(be, "A.pack").data, le)
        self.assertEqual(wc.describe(le), wc.describe(be))
        kinds = {(n.path, n.status) for n in conv.notes}
        self.assertIn(("A.pack//Model/A.bfres", wc.CANT), kinds)
        self.assertIn(("A.pack//Readme.txt", wc.SAME), kinds)

    def test_independent_build_matches(self):
        files = [("a.byml", byml(DOC, LE)), ("b.bxml", AAMP_LE)]
        le, be = wc.sarc_build(files, LE), wc.sarc_build([("a.byml", byml(DOC, BE)), ("b.bxml", AAMP_LE)], BE)
        self.assertEqual(wc.Converter(BE).file(le, "x").data, be)
        self.assertEqual(wc.Converter(LE).file(be, "x").data, le)

    def test_nested_yaz0_relayout_keeps_order_and_alignment(self):
        inner = wc.sarc_build([("Msg/A.msbt", msbt(LE, LABELS, ATTRS, TEXTS)), ("Msg/B.msbt",
                               msbt(LE, {"X": 0}, [], [["only text"]]))], LE)
        files = [("Message/Msg.ssarc", yaz0_stored(inner)), ("Model/Big.bfres", FRES), ("Tips/T.sbyml",
                 yaz0_stored(byml(DOC, LE) * 1)), ("Actor/X.bxml", AAMP_LE)]
        le = wc.sarc_build(files, LE, align={"ssarc": 0x80, "bfres": 0x2000, "sbyml": 0x10, "bxml": 0x100})
        r = wc.Converter(BE).file(le, "Bootup_XXxx.pack")
        self.assertEqual(r.status, wc.PARTLY)
        be = r.data
        self.assertNotEqual(len(be), len(le))  # the stored Yaz0 children were compressed: laid out again
        a, b = wc.sarc_parse(le), wc.sarc_parse(be)
        self.assertEqual([n.name for n in a.nodes], [n.name for n in b.nodes])
        self.assertEqual([n.hash for n in a.nodes], [n.hash for n in b.nodes])
        self.assertEqual(sorted(a.nodes, key=lambda n: n.start), sorted(a.nodes, key=lambda n: n.start))
        order_a = [n.name for n in sorted(a.nodes, key=lambda n: n.start)]
        order_b = [n.name for n in sorted(b.nodes, key=lambda n: n.start)]
        self.assertEqual(order_a, order_b)
        for na, nb in zip(a.nodes, b.nodes):
            self.assertEqual((b.data_offset + nb.start) % wc._alignment_of(a.data_offset + na.start), 0, na.name)
        self.assertEqual(b.file_size, len(be))
        self.assertEqual(wc.describe(le), wc.describe(be))
        back = wc.Converter(LE).file(be, "x").data
        self.assertEqual(wc.same_unpacked(le, back), (True, []))
        msgs = dict(wc.sarc_files(wc.yaz0_decompress(dict(wc.sarc_files(be))["Message/Msg.ssarc"])[0]))
        self.assertEqual(msgs["Msg/A.msbt"], msbt(BE, LABELS, ATTRS, TEXTS))

    def test_pack_of_only_unconvertible_files_is_kept_whole(self):
        le = yaz0_stored(wc.sarc_build([("Layout/A.bflyt", b"FLYT" + bytes(28)), ("Layout/B.bflim", FRES)], LE))
        conv = wc.Converter(BE)
        r = conv.file(le, "Layout/Common.sblarc")
        self.assertEqual((r.status, r.data), (wc.CANT, le))
        self.assertIn("UI layout 1", r.detail)

    def test_pack_holding_a_partly_converted_pack_is_converted(self):
        inner = wc.sarc_build([("Physics/A.hkrb", bytes(32)), ("Actor/A.byml", byml([1], BE))], BE)
        outer = wc.sarc_build([("Actor/Pack/A.bactorpack", inner)], BE)
        r = wc.Converter(LE).file(outer, "TitleBG.pack")
        self.assertEqual(r.status, wc.PARTLY)
        self.assertEqual(r.data[6:8], b"\xff\xfe")
        got = dict(wc.sarc_files(dict(wc.sarc_files(r.data))["Actor/Pack/A.bactorpack"]))
        self.assertEqual(got, {"Physics/A.hkrb": bytes(32), "Actor/A.byml": byml([1], LE)})

    def test_already_and_errors(self):
        le = wc.sarc_build([("a.byml", byml(DOC, LE))], LE)
        self.assertEqual(wc.Converter(LE).file(le, "x").status, wc.ALREADY)
        bad = bytearray(le)
        bad[6:8] = b"\x00\x00"
        r = wc.Converter(BE).file(bytes(bad), "x")
        self.assertEqual((r.status, r.data), (wc.BROKEN, bytes(bad)))
        cut = le[:0x30]
        self.assertEqual(wc.Converter(BE).file(cut, "x").status, wc.BROKEN)


class Byml(unittest.TestCase):
    def test_all_node_types_match_independent_build(self):
        le, be = byml(DOC, LE), byml(DOC, BE)
        self.assertNotEqual(le, be)
        counts = wc.Counter()
        self.assertEqual(wc.byml_convert(le, BE, counts), be)
        self.assertEqual(wc.byml_convert(be, LE), le)
        self.assertEqual(wc.byml_load(le), wc.byml_load(be))
        for t in ("string", "bool", "int", "float", "uint", "int64", "uint64", "double", "null", "binary", "array",
                  "dictionary"):
            self.assertIn(t, counts)
        loaded = wc.byml_load(be)
        self.assertEqual(loaded["Hp"], ("int", 2000))
        self.assertEqual(loaded["Neg"], ("int", (-5) & 0xFFFFFFFF))
        self.assertEqual(loaded["Blob"], b"\x01\x02\x03\x04\x05")
        self.assertEqual(loaded["Huge"], ("uint64", 2 ** 63 + 5))

    def test_shared_node_swapped_once(self):
        shared = {"Name": "same", "Pos": [1.0]}
        doc = {"A": shared, "B": shared, "L": [shared, shared]}
        le, be = byml(doc, LE), byml(doc, BE)
        self.assertEqual(wc.byml_convert(le, BE), be)
        self.assertEqual(wc.byml_convert(be, LE), le)

    def test_array_root_empty_doc_and_version(self):
        for doc in ([1, "a", [2.0]], [], None):
            le, be = byml(doc, LE, 3), byml(doc, BE, 3)
            self.assertEqual(wc.byml_convert(le, BE), be)
        with self.assertRaises(wc.FormatError):
            wc.byml_convert(byml([1], LE, 1), BE)

    def test_errors(self):
        le = bytearray(byml({"A": [1, 2]}, LE))
        arr = le.index(bytes([0xC0]))
        le[arr + 4] = 0x99  # unknown node type
        with self.assertRaises(wc.FormatError):
            wc.byml_convert(bytes(le), BE)
        le = bytearray(byml({"A": [1, 2]}, LE))
        struct.pack_into("<I", le, 12, len(le) + 100)  # root past the end
        with self.assertRaises(wc.FormatError):
            wc.byml_convert(bytes(le), BE)
        r = wc.Converter(BE).file(bytes(le), "x.byml")
        self.assertEqual((r.status, r.data), (wc.BROKEN, bytes(le)))


class Msbt(unittest.TestCase):
    def test_matches_independent_build(self):
        le, be = msbt(LE, LABELS, ATTRS, TEXTS), msbt(BE, LABELS, ATTRS, TEXTS)
        guesses = []
        self.assertEqual(wc.msbt_convert(le, BE, guesses), be)
        self.assertEqual(wc.msbt_convert(be, LE), le)
        self.assertEqual(guesses, [])
        self.assertEqual(wc.msbt_load(le), wc.msbt_load(be))
        self.assertEqual(be[8:10], b"\xfe\xff")

    def test_no_attributes_and_extra_sections(self):
        tsy = b"TSY1" + struct.pack(">I", 8) + bytes(8) + struct.pack(">II", 1, 2) + b"\xab" * 8
        tsy_le = b"TSY1" + struct.pack("<I", 8) + bytes(8) + struct.pack("<II", 1, 2) + b"\xab" * 8
        le, be = msbt(LE, LABELS, [], TEXTS, extra=tsy_le), msbt(BE, LABELS, [], TEXTS, extra=tsy)
        self.assertEqual(wc.msbt_convert(be, LE), le)

    def test_unknown_tag_is_a_guess(self):
        texts = [["a", ("tag", 9, 9, [("h", 1), ("h", 2)])]]
        conv = wc.Converter(BE)
        r = conv.file(msbt(LE, {"A": 0}, [], texts), "x.msbt")
        self.assertEqual(r.data, msbt(BE, {"A": 0}, [], texts))
        self.assertEqual(r.status, wc.CONVERTED)
        self.assertIn("9.9", r.detail)
        self.assertEqual(dict(conv.tag_guesses), {"9.9": 1})

    def test_errors(self):
        for data in (msbt(LE, LABELS, [], TEXTS, encoding=0),
                     msbt(LE, LABELS, [], TEXTS, extra=b"ATO1" + struct.pack("<I", 4) + bytes(12) + b"\xab" * 12),
                     msbt(LE, {"A": 0}, [], [[("tag", 9, 9, [("b", b"\x01")])]])):
            r = wc.Converter(BE).file(data, "x.msbt")
            self.assertEqual((r.status, r.data), (wc.BROKEN, data))
        good = bytearray(msbt(LE, {"A": 0}, [], [[("tag", 0, 3, [("h", 1)])]]))
        at = good.index(b"\x0e\x00\x00\x00\x03\x00")
        good[at + 6] = 0x40  # parameters longer than the string
        self.assertEqual(wc.Converter(BE).file(bytes(good), "x.msbt").status, wc.BROKEN)


class OtherFormats(unittest.TestCase):
    def test_rstb(self):
        le = rstb(LE, {5: 100, 1: 200}, {"Actor/Pack/X.bactorpack": 4096})
        be = rstb(BE, {5: 100, 1: 200}, {"Actor/Pack/X.bactorpack": 4096})
        self.assertEqual(wc.rstb_convert(le, BE), be)
        self.assertEqual(wc.rstb_convert(be, LE), le)
        self.assertEqual(wc.rstb_load(le), wc.rstb_load(be))
        self.assertEqual(wc.Converter(LE, rstb="convert").file(be, "r").data, le)
        r = wc.Converter(LE).file(yaz0_stored(be), "System/Resource/ResourceSizeTable.product.srsizetable")
        self.assertEqual(r.status, wc.LEFT_OUT)
        self.assertIn("Wii U resource sizes", r.detail)
        self.assertEqual(wc.Converter(LE, rstb="convert").file(rstb(BE, {}, {}), "r").status, wc.ALREADY)
        with self.assertRaises(wc.FormatError):
            wc.rstb_order(be + b"x")

    def test_aamp(self):
        self.assertEqual(wc.Converter(LE).file(AAMP_LE, "a.bxml").status, wc.SAME)
        r = wc.Converter(LE).file(AAMP_BE, "a.bxml")
        self.assertEqual((r.status, r.data), (wc.CANT, AAMP_BE))

    def test_platform_specific_and_unknown(self):
        cases = {"Model/A.sbfres": yaz0_stored(FRES), "Sound/A.bfstm": b"FSTM" + bytes(20),
                 "Physics/A.hkrb": bytes(16), "Physics/B.shksc": b"\x00\x00\x00\x10TAG0" + bytes(8),
                 "Effect/A.sesetlist": yaz0_stored(b"VFXB" + bytes(20)), "Movie/A.mp4": bytes(30),
                 "Font/A.bfotf": bytes(10), "Event/A.bfevfl": b"BFEVFL\0\0" + bytes(20),
                 "Model/A.Tex1.sbfres": yaz0_stored(FRES)}
        for name, data in cases.items():
            r = wc.Converter(LE).file(data, name)
            self.assertEqual((r.status, r.data), (wc.CANT, data), name)
        self.assertIn("Tex1/Tex2", wc.Converter(LE).file(cases["Model/A.Tex1.sbfres"], "Model/A.Tex1.sbfres").detail)
        r = wc.Converter(LE).file(b"\x01\x02\x03whatever", "A.bin")
        self.assertEqual((r.status, r.data), (wc.UNKNOWN, b"\x01\x02\x03whatever"))
        self.assertEqual(wc.Converter(LE).file(b"<?xml version='1.0'?><a/>", "A.fxparam").status, wc.SAME)

    def test_yaz0_unchanged_content_keeps_original_bytes(self):
        data = yaz0_stored(byml(DOC, LE), 0x80)  # already Switch byte order: nothing to do
        r = wc.Converter(LE).file(data, "a.sbyml")
        self.assertEqual((r.status, r.data), (wc.ALREADY, data))
        r = wc.Converter(LE).file(yaz0_stored(byml(DOC, BE), 0x80), "a.sbyml")
        self.assertEqual(r.status, wc.CONVERTED)
        self.assertEqual(wc.yaz0_decompress(r.data), (byml(DOC, LE), 0x80))
        bad = yaz0_stored(byml(DOC, BE))[:30]
        self.assertEqual(wc.Converter(LE).file(bad, "a.sbyml").status, wc.BROKEN)

    def test_empty_file_is_the_same_on_both(self):
        for name in ("Actor/Empty.bxml", "Model/Empty.sbfres", "Empty.bin"):
            r = wc.Converter(LE).file(b"", name)
            self.assertEqual((r.status, r.data, r.detail), (wc.SAME, b"", "empty file"), name)
        pack = wc.sarc_build([("Actor/A.byml", byml({"a": 1}, BE)), ("Actor/Empty.bxml", b"")], BE)
        r = wc.Converter(LE).file(pack, "A.pack")
        self.assertEqual((r.status, r.detail), (wc.CONVERTED, ""))
        self.assertEqual(dict(wc.sarc_files(r.data)), {"Actor/A.byml": byml({"a": 1}, LE), "Actor/Empty.bxml": b""})


def wiiu_mod() -> dict:
    pack = yaz0_stored(wc.sarc_build([("Actor/Physics/A.bphysics", AAMP_LE),
                                      ("Actor/AnimationInfo/A.baniminfo", byml(DOC, BE))], BE))
    return {
        "content/Actor/Pack/Armor_999.sbactorpack": pack,
        "content/Map/MainField/A-1/A-1_Dynamic.smubin": yaz0_stored(byml(DOC, BE)),
        "content/Pack/Bootup_USen.pack": wc.sarc_build([("Message/Msg_USen.product.ssarc", yaz0_stored(
            wc.sarc_build([("Msg/A.msbt", msbt(BE, LABELS, ATTRS, TEXTS))], BE)))], BE),
        "content/Model/Armor_999.sbfres": yaz0_stored(FRES),
        "content/System/Resource/ResourceSizeTable.product.srsizetable": yaz0_stored(rstb(BE, {1: 2}, {})),
        "aoc/0010/Map/MainField/B-1/B-1_Dynamic.smubin": yaz0_stored(byml({"Objs": []}, BE)),
        "rules.txt": "[Definition]\ntitleIds = 00050000101C9400\n",
        "readme.txt": "made-up mod",
    }


class Layout(unittest.TestCase):
    def test_content_and_aoc(self):
        lay = wc.plan_layout(["content/Actor/Pack/A.sbactorpack", "aoc/0010/Pack/AocMainField.pack", "rules.txt",
                              "Thumbs.db"])
        self.assertEqual(lay.files, [("content/Actor/Pack/A.sbactorpack", "romfs/Actor/Pack/A.sbactorpack"),
                                     ("aoc/0010/Pack/AocMainField.pack",
                                      "01007EF00011F001/romfs/Pack/AocMainField.pack")])
        self.assertEqual(lay.other, ["rules.txt", "Thumbs.db"])

    def test_graphic_pack_and_wrapper(self):
        lay = wc.plan_layout(["My Pack/rules.txt", "My Pack/content/System/A.txt", "My Pack/aoc/0010/B/x.byml"])
        self.assertEqual(lay.root, "My Pack")
        self.assertEqual([t for _, t in lay.files], ["romfs/System/A.txt", "01007EF00011F001/romfs/B/x.byml"])

    def test_content_folder_given_directly(self):
        lay = wc.plan_layout(["Actor/Pack/A.sbactorpack"], "content")
        self.assertEqual(lay.files, [("Actor/Pack/A.sbactorpack", "romfs/Actor/Pack/A.sbactorpack")])
        lay = wc.plan_layout(["Pack/AocMainField.pack"], "0010")
        self.assertEqual(lay.files[0][1], "01007EF00011F001/romfs/Pack/AocMainField.pack")

    def test_refusals(self):
        cases = {("A/content/x/a.byml", "B/content/x/a.byml"): "several",
                 ("romfs/Actor/a.byml",): "Switch mod",
                 ("meta.yml", "manifest.yml", "options/x"): "UKMM",
                 ("Actor/a.byml",): "no content/",
                 ("content/Pack/A.pack", "Content/pack/a.pack"): "same file",
                 ("info.json", "logs/packs.json", "content/Pack/A.pack"): "BCML package (BNP)"}
        for paths, words in cases.items():
            with self.assertRaises(wc.ConvertError) as cm:
                wc.plan_layout(list(paths))
            self.assertIn(words, str(cm.exception), paths)

    def test_one_mod_for_several_regions_keeps_one_copy(self):
        """linkle-dialogue-fix: sdcafiine/00050000101C9400 and .../101C9500, the same files for two regions of the
        game: one copy is converted (101C9400 first), the other left out and named. Other folders stay refused."""
        us, eu = "sdcafiine/00050000101C9400/Content", "sdcafiine/00050000101C9500/Content"
        lay = wc.plan_layout([f"{eu}/Pack/Bootup_USen.pack", f"{us}/Pack/Bootup_USen.pack", "readme.txt"])
        self.assertEqual(lay.root, "sdcafiine/00050000101C9400")
        self.assertEqual(lay.files, [(f"{us}/Pack/Bootup_USen.pack", "romfs/Pack/Bootup_USen.pack")])
        self.assertEqual((lay.copies, lay.copy_roots), ({f"{eu}/Pack/Bootup_USen.pack": f"{us}/Pack/Bootup_USen.pack"},
                                                        ["sdcafiine/00050000101C9500"]))
        self.assertEqual(wc.plan_layout(["x/00050000101c9300/content/a.txt", "x/00050000101c9500/content/a.txt"]).root,
                         "x/00050000101c9500")
        for paths in (["a/00050000101C9400/content/x.txt", "b/00050000101C9500/content/x.txt"],     # (two parents)
                      ["00050000101C9400/content/x.txt", "0005000E101C9500/content/x.txt"],     # (base and update)
                      ["00050000101C9400/content/x.txt", "00050000101C9400x/content/x.txt"]):
            with self.subTest(paths=paths), self.assertRaises(wc.ConvertError) as cm:
                wc.plan_layout(paths)
            self.assertIn("several mods or options", str(cm.exception))

    def test_region_copies_are_compared_and_named(self):
        us, eu = "sdcafiine/00050000101C9400/Content", "sdcafiine/00050000101C9500/Content"
        doc = byml(DOC, BE)
        with tempfile.TemporaryDirectory() as d:
            for eu_doc, words in ((doc, "the same mod for another region of the game"), (byml({"x": 1}, BE),
                                                                                          "differs from it")):
                root = Path(d) / f"mod-{len(words)}"
                make(root, {f"{us}/Map/MainField/A-1/A-1_Dynamic.smubin": wc.yaz0_compress(doc),
                            f"{eu}/Map/MainField/A-1/A-1_Dynamic.smubin": wc.yaz0_compress(eu_doc)})
                rep = wc.convert_mod(str(root), str(Path(d) / f"out-{len(words)}"))
                with self.subTest(words=words):
                    self.assertEqual(len(rep.files), 1)
                    self.assertIn(words, rep.regions[0])
                    self.assertIn("sdcafiine/00050000101C9500", rep.text())
                    self.assertEqual(rep.verdict(), "everything converted")

    def test_a_zip_member_bigger_than_any_game_file_is_not_read(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "mod.zip"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("content/Pack/Big.pack", b"\0" * 5000)
            src = wc.ModSource(str(path))
            try:
                self.assertEqual(src.read("content/Pack/Big.pack", 4), b"\0" * 4)
                with mock.patch.object(wc, "MAX_ZIP_READ", 4096), self.assertRaises(wc.ConvertError) as cm:
                    src.read("content/Pack/Big.pack")
                self.assertIn("bigger than any game file", str(cm.exception))
            finally:
                src.zip.close()

    def test_unsafe_names_are_never_mapped(self):
        bad = ["content/C:../C:../ESCAPED.txt", "content/D:/x.txt", "content/Actor\\..\\..\\x.txt",
               "content/Pack/a:b.txt", "content/Pack/con.txt", "content/Pack/Aux.pack", "content/Model/Bad./x",
               "content/Model/x ", "content//x.txt", "aoc/0010/C:/x.byml"]
        lay = wc.plan_layout(["content/Actor/Pack/ok.txt"] + bad)
        self.assertEqual(lay.files, [("content/Actor/Pack/ok.txt", "romfs/Actor/Pack/ok.txt")])
        self.assertEqual(lay.unsafe, bad)
        lay = wc.plan_layout(["Actor/ok.txt", "C:../x.txt", "D:/x.txt"], "content")
        self.assertEqual((lay.files, lay.unsafe),
                         ([("Actor/ok.txt", "romfs/Actor/ok.txt")], ["C:../x.txt", "D:/x.txt"]))
        # only unsafe names: reported, not mistaken for "no content/ folder"
        self.assertEqual(wc.plan_layout(["content/C:../x.txt"]).unsafe, ["content/C:../x.txt"])
        # a wrapper folder outside content/ isn't written, so its name doesn't matter
        self.assertEqual(wc.plan_layout(["Mod v1. /content/a.txt"]).files, [("Mod v1. /content/a.txt", "romfs/a.txt")])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mod = make(self.root / "Armor Mod", wiiu_mod())

    def tearDown(self):
        self.tmp.cleanup()

    def test_check(self):
        code, out, err = run_cli("check", self.mod)
        self.assertEqual(code, 1, out + err)  # the model can't be converted
        self.assertIn("6 game file(s): 5 base game, 1 DLC", out)
        self.assertIn("content/Model/Armor_999.sbfres: model/texture", out)
        self.assertIn("ResourceSizeTable.product.srsizetable: left out", out)
        self.assertIn("not game data, left out (2)", out)
        self.assertIn("converted in part", out)
        self.assertFalse(any(self.root.glob("**/romfs")))  # check writes nothing
        code, out, _ = run_cli("check", self.mod, "--json")
        import json
        data = json.loads(out)
        self.assertEqual(data["verdict"], "converted in part")
        self.assertEqual(data["counts"]["converted"], 4)

    def test_convert_writes_switch_layout(self):
        out = self.root / "Armor Mod (Switch)"
        code, text, err = run_cli("convert", self.mod, "--out", out)
        self.assertEqual(code, 1, text + err)
        self.assertTrue((out / wc.REPORT).is_file())
        written = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
        self.assertEqual(written, ["01007EF00011F001/romfs/Map/MainField/B-1/B-1_Dynamic.smubin",
                                   "romfs/Actor/Pack/Armor_999.sbactorpack",
                                   "romfs/Map/MainField/A-1/A-1_Dynamic.smubin",
                                   "romfs/Model/Armor_999.sbfres",
                                   "romfs/Pack/Bootup_USen.pack", wc.REPORT])
        smubin = (out / "romfs/Map/MainField/A-1/A-1_Dynamic.smubin").read_bytes()
        self.assertEqual(wc.yaz0_decompress(smubin)[0], byml(DOC, LE))
        boot = (out / "romfs/Pack/Bootup_USen.pack").read_bytes()
        msgs = wc.sarc_files(wc.yaz0_decompress(dict(wc.sarc_files(boot))["Message/Msg_USen.product.ssarc"])[0])
        self.assertEqual(msgs, [("Msg/A.msbt", msbt(LE, LABELS, ATTRS, TEXTS))])
        self.assertEqual((out / "romfs/Model/Armor_999.sbfres").read_bytes(), yaz0_stored(FRES))
        # romfs_mods reads the result as one Switch mod, base game and DLC
        paths = [p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()]
        entries, skipped, _ = rm.map_paths(paths)
        self.assertEqual(sorted((e.rel, e.dlc) for e in entries),
                         [("Actor/Pack/Armor_999.sbactorpack", False),
                          ("Map/MainField/A-1/A-1_Dynamic.smubin", False),
                          ("Map/MainField/B-1/B-1_Dynamic.smubin", True),
                          ("Model/Armor_999.sbfres", False), ("Pack/Bootup_USen.pack", False)])
        self.assertEqual(skipped, [(wc.REPORT, rm.R_OUTSIDE)])

    def test_convert_drop_and_zip(self):
        z = self.root / "mod.zip"
        with zipfile.ZipFile(z, "w") as zf:
            for rel, data in wiiu_mod().items():
                zf.writestr("Armor Mod/" + rel, data)
        out = self.root / "out"
        code, text, err = run_cli("convert", z, "--out", out, "--drop-unconverted")
        self.assertEqual(code, 1, text + err)
        self.assertFalse((out / "romfs/Model/Armor_999.sbfres").exists())
        self.assertTrue((out / "romfs/Actor/Pack/Armor_999.sbactorpack").exists())
        self.assertIn("left out with --drop-unconverted: 1", text)

    def test_everything_converts_exit_0(self):
        mod = make(self.root / "Text Mod", {"content/Pack/Bootup_USen.pack": wiiu_mod()["content/Pack/Bootup_USen.pack"]})
        code, out, _ = run_cli("check", mod)
        self.assertEqual(code, 0, out)
        self.assertIn("Result: everything converted.", out)

    def test_zip_names_cant_write_outside_the_output(self):
        work = self.root / "evil"
        work.mkdir()
        z = work / "evil.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("content/Actor/Pack/ok.txt", "ok")
            zf.writestr("content/C:../C:../ESCAPED.txt", "bad")
            zf.writestr("content/Model/Bad./x.txt", "bad")
            back = zipfile.ZipInfo("content/Pack/x")
            back.filename = "content/Pack\\..\\..\\..\\..\\BACKSLASH.txt"
            zf.writestr(back, "bad")
        out = work / "outer" / "conv"
        code, text, err = run_cli("convert", z, "--out", out)
        self.assertEqual(code, 1, text + err)
        files = sorted(p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file())
        self.assertEqual(files, ["evil.zip", "outer/conv/romfs/Actor/Pack/ok.txt", f"outer/conv/{wc.REPORT}"])
        self.assertIn("Left out, unsafe file names", text)
        self.assertIn("content/C:../C:../ESCAPED.txt", text)
        self.assertIn("Result: converted in part.", text)
        import json
        code, out_json, _ = run_cli("check", z, "--json")
        unsafe = json.loads(out_json)["unsafe_names_left_out"]
        # (zipfile on Windows reads "\" as "/", so the backslash name lands in unsafe or in other files)
        self.assertEqual([p for p in unsafe if "BACKSLASH" not in p],
                         ["content/C:../C:../ESCAPED.txt", "content/Model/Bad./x.txt"])
        # a zip of nothing but such names: reported, nothing written
        only = work / "only.zip"
        with zipfile.ZipFile(only, "w") as zf:
            zf.writestr("content/C:../x.txt", "bad")
        code, text, _ = run_cli("convert", only, "--out", work / "out2")
        self.assertEqual(code, 2)
        self.assertIn("Result: nothing could be converted.", text)
        self.assertFalse(any(p.name == "x.txt" for p in work.rglob("*")))
        self.assertFalse((work / "out2").exists())

    def test_writer_checks_every_destination(self):
        # second guard, behind plan_layout: a target that leads out is refused before anything is made for it
        bad = wc.Layout([("content/a.txt", "romfs/C:../C:../ESCAPED.txt")], [], "")
        mod = make(self.root / "m", {"content/a.txt": "x"})
        out = self.root / "outer" / "conv"
        with mock.patch.object(wc, "plan_layout", return_value=bad):
            with self.assertRaises(wc.ConvertError) as cm:
                wc.convert_mod(str(mod), str(out))
        self.assertIn("outside the output folder", str(cm.exception))
        self.assertFalse(any(p.name == "ESCAPED.txt" for p in self.root.rglob("*")))
        self.assertFalse(out.exists())  # cleaned up
        self.assertFalse(wc._inside(os.path.join(str(out), "romfs", "D:", "x.txt"), str(out)))
        # and never over a file already written
        twice = wc.Layout([("content/a.txt", "romfs/a.txt"), ("content/a.txt", "romfs/a.txt")], [], "")
        with mock.patch.object(wc, "plan_layout", return_value=twice):
            with self.assertRaises(wc.ConvertError) as cm:
                wc.convert_mod(str(mod), str(out))
        self.assertIn("over a file already written", str(cm.exception))

    def test_only_left_out_files_is_not_success(self):
        mod = make(self.root / "Rstb Mod", {"content/System/Resource/ResourceSizeTable.product.srsizetable":
                                            yaz0_stored(rstb(BE, {1: 2}, {}))})
        out = self.root / "Rstb Mod (Switch)"
        code, text, err = run_cli("convert", mod, "--out", out)
        self.assertEqual(code, 2, text + err)  # (as modman add refuses it)
        self.assertIn("Result: nothing to convert.", text)
        self.assertIn("Nothing written", text)
        self.assertNotIn("everything converted", text)
        self.assertFalse(out.exists())
        self.assertEqual(wc.check_mod(str(mod)).verdict(), "nothing to convert")
        self.assertEqual(run_cli("check", mod)[0], 2)

    def test_nothing_convertible_writes_nothing(self):
        """convert wrote a folder with only the Wii U model in it and said 'Next: python tools/modman.py add', and
        modman then added that folder as a working Switch mod (made-up mod)."""
        mod = make(self.root / "Model Only", {"content/Model/Armor_999.sbfres": yaz0_stored(FRES)})
        out = self.root / "Model Out"
        code, text, err = run_cli("convert", mod, "--out", out)
        self.assertEqual(code, 2, text + err)
        self.assertIn("Result: nothing could be converted.", text)
        self.assertIn("Nothing written", text)
        self.assertNotIn("Next:", text)
        self.assertNotIn("Switch mod written to", text)
        self.assertFalse(out.exists())
        out.mkdir()  # (an empty folder given as --out stays, empty)
        self.assertEqual(run_cli("convert", mod, "--out", out)[0], 2)
        self.assertEqual(list(out.iterdir()), [])
        code, text, _ = run_cli("check", mod)
        self.assertEqual(code, 2, text)

    def test_report_counts_read_back(self):
        """modman reads a convert output folder's report back into the counts it keeps for a Wii U mod."""
        mod = make(self.root / "Many", {**wiiu_mod(), **{f"content/Model/M{i:03}.sbfres": yaz0_stored(FRES)
                                                         for i in range(205)}})
        with zipfile.ZipFile(self.root / "unsafe.zip", "w") as zf:
            zf.writestr("content/Pack/ok.txt", "ok")
            zf.writestr("content/C:../x.txt", "bad")
            zf.writestr("readme.txt", "x")
        for src in (self.mod, mod, self.root / "unsafe.zip"):
            with self.subTest(src=src.name):
                rep = wc.check_mod(str(src))
                got = wc.report_counts(rep.text())
                self.assertEqual(got, {"verdict": rep.verdict(), **rep.counts()})
        self.assertEqual(wc.report_counts(wc.check_mod(str(mod)).text())["wiiu_data"], 206)  # (200 listed + more)
        self.assertIsNone(wc.report_counts("Some other text\nResult: everything converted.\n"))
        self.assertIsNone(wc.report_counts(""))

    def test_empty_files_convert(self):
        mod = make(self.root / "Empty Mod", {"content/Actor/Empty.bxml": b""})
        code, text, _ = run_cli("check", mod)
        self.assertEqual(code, 0, text)
        self.assertIn("same on both", text)
        self.assertNotIn("Still Wii U data", text)

    def test_refusals(self):
        out = make(self.root / "busy", {"keep.txt": "mine"})
        code, _, err = run_cli("convert", self.mod, "--out", out)
        self.assertEqual(code, 2)
        self.assertIn("isn't an empty folder", err)
        self.assertEqual((out / "keep.txt").read_text(), "mine")
        code, _, err = run_cli("convert", self.mod, "--out", self.mod / "inside")
        self.assertEqual(code, 2)
        self.assertIn("can't be inside", err)
        switch = make(self.root / "switch", {"romfs/Actor/a.byml": byml([], LE)})
        self.assertEqual(run_cli("check", switch)[0], 2)
        self.assertEqual(run_cli("check", self.root / "missing")[0], 2)
        notzip = make(self.root / "x", {"a.7z": b"7z\xbc\xaf"}) / "a.7z"
        code, _, err = run_cli("check", notzip)
        self.assertEqual(code, 2)
        self.assertIn("Extract it first", err)

    def test_error_names_each_path_once(self):
        """Errors printed the mod's path twice ('error: X X doesn't exist'), or the mod's path in front of the
        output folder's."""
        missing = self.root / "Nope Mod"
        notzip = make(self.root / "x", {"a.bnp": b"7z\xbc\xaf"}) / "a.bnp"
        busy = make(self.root / "busy", {"keep.txt": "mine"})
        cases = [(("check", missing), f"error: {missing} doesn't exist\n"),
                 (("check", notzip), f"error: {notzip} isn't a folder or a .zip. Extract it first (7-Zip opens .7z, "
                                     ".rar and .bnp), then use the folder\n"),
                 (("convert", self.mod, "--out", busy),
                  f"error: {busy} already exists and isn't an empty folder: pick a new one\n"),
                 (("convert", self.mod, "--out", self.mod / "in"),
                  "error: the output folder can't be inside the mod (or the other way round)\n"),
                 (("verify", self.root, "--out", self.root / "rep"),
                  f"error: {self.root} isn't a game RomFS folder (no Pack folder)\n")]
        for args, want in cases:
            with self.subTest(args=args[0]):
                code, out, err = run_cli(*args)
                self.assertEqual((code, err), (2, want), out)
        bad = self.root / "bad.zip"
        bad.write_bytes(b"PK\x03\x04 not really")
        self.assertTrue(run_cli("check", bad)[2].startswith(f"error: {bad} is a damaged .zip ("))

    def test_packages_and_other_games_are_refused(self):
        """A Cemu pack for another game and a packed BNP were converted ('everything converted'), though modman add
        and romfs_mods refuse both (made-up mods)."""
        boot = wiiu_mod()["content/Pack/Bootup_USen.pack"]
        other = make(self.root / "Other Game Pack", {"rules.txt": "[Definition]\ntitleIds = 0005000010101C00\n",
                                                     "content/Pack/Madeup.pack": boot})
        bnp = make(self.root / "bnp-wiiu", {"info.json": '{"name": "x", "platform": "wiiu"}', "logs/rstb.log": "x",
                                            "content/Pack/Bootup_USen.pack": boot})
        wrapped = make(self.root / "Wrapped", {"My Mod/info.json": "{}", "My Mod/logs/packs.json": "{}",
                                               "My Mod/content/Pack/Bootup_USen.pack": boot})
        cases = {other: "is a Cemu graphic pack made for another game, not BotW (its rules.txt has no BotW title id)",
                 bnp: "is a BCML package (BNP), whose packs only hold the changes",
                 wrapped: "is a BCML package (BNP)"}
        for mod, want in cases.items():
            for args in (("check", mod), ("convert", mod, "--out", self.root / "out")):
                with self.subTest(mod=mod.name, cmd=args[0]):
                    code, out, err = run_cli(*args)
                    self.assertEqual(code, 2, out + err)
                    self.assertIn(f"error: {mod} {want}", err)
                    self.assertNotIn("Next:", out)
                    self.assertFalse((self.root / "out").exists())
        # a pack for BotW is converted; a BCML mod folder without logs/ is read like any Wii U mod
        botw = make(self.root / "BotW Pack", {"rules.txt": "[Definition]\ntitleIds = 00050000101C9400\n",
                                              "content/Pack/Madeup.pack": boot})
        loose = make(self.root / "bcml-folder", {"info.json": "{}", "content/Pack/Bootup_USen.pack": boot})
        for mod in (botw, loose):
            self.assertEqual(run_cli("check", mod)[0], 0, mod.name)


class Verify(unittest.TestCase):
    def test_round_trip_on_made_up_romfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = make(Path(tmp) / "romfs", {
                "Actor/Pack/A.sbactorpack": wc.yaz0_compress(wc.sarc_build(
                    [("Actor/Physics/A.bphysics", AAMP_LE), ("Actor/AnimationInfo/A.baniminfo", byml(DOC, LE))],
                    LE)),
                "Pack/Bootup_USen.pack": wc.sarc_build([("Message/Msg_USen.product.ssarc", wc.yaz0_compress(
                    wc.sarc_build([("Msg/A.msbt", msbt(LE, LABELS, ATTRS, TEXTS))], LE)))], LE),
                "Pack/Bootup.pack": wc.sarc_build([("Actor/ResidentActors.byml", byml(DOC, LE)),
                                                   ("Layout/Common.sblarc", yaz0_stored(wc.sarc_build(
                                                       [("a.bflyt", b"FLYT" + bytes(28))], LE)))], LE),
                "Pack/Dungeon000.pack": wc.sarc_build(
                    [("Map/CDungeon/Dungeon000/Dungeon000_Static.smubin", wc.yaz0_compress(byml(DOC, LE))),
                     ("Physics/StaticCompound/CDungeon/Dungeon000.shksc", yaz0_stored(bytes(64))),
                     ("Model/DgnMrgPrt_Dungeon000.sbfres", yaz0_stored(FRES))], LE,
                    align={"smubin": 0x80, "sbfres": 0x2000}),
                "Event/E.sbeventpack": wc.yaz0_compress(wc.sarc_build(  # (not sampled)
                    [("EventFlow/E.bfevfl", b"BFEVFL\0\0" + bytes(20)), ("Event/E.byml", byml([1, 2], LE))], LE)),
                "Map/MainField/A-1/A-1_Dynamic.smubin": wc.yaz0_compress(byml(DOC, LE)),
                "Actor/ActorInfo.product.sbyml": wc.yaz0_compress(byml({"Actors": [DOC]}, LE)),
                "System/Resource/ResourceSizeTable.product.srsizetable": wc.yaz0_compress(
                    rstb(LE, {1: 2, 3: 4}, {"a": 5})),
            })
            out = Path(tmp) / "report"
            with contextlib.redirect_stdout(io.StringIO()):
                result = wc.verify(str(game.parent), str(out), count=3)
            self.assertEqual(result["samples"], 7)
            self.assertEqual(result["failed"], [])
            self.assertEqual(result["byte_identical"] + result["same_after_decompression"], 7)
            self.assertIn("MSBT converted", result["files_by_format"])
            self.assertTrue((out / (wc.VERIFY_REPORT + ".txt")).is_file())
            with self.assertRaises(wc.ConvertError):
                wc.verify(str(game), str(game / "inside"))

    def test_same_unpacked_spots_differences(self):
        a = wc.sarc_build([("a.byml", byml([1], LE))], LE)
        b = wc.sarc_build([("a.byml", byml([2], LE))], LE)
        ok, why = wc.same_unpacked(a, b)
        self.assertFalse(ok)
        self.assertIn("a.byml: bytes differ", why)
        self.assertEqual(wc.same_unpacked(yaz0_stored(a), wc.yaz0_compress(a)), (True, []))


if __name__ == "__main__":
    unittest.main()
