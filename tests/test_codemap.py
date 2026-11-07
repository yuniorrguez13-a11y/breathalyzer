"""Tests for tools/codemap.py. Uses only made-up bytes and names, never game files."""
import csv
import os
import struct
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import codemap  # noqa: E402
import nso  # noqa: E402

BASE = codemap.MAIN_BASE
R_ABS64, R_GLOB_DAT, R_JUMP_SLOT, R_RELATIVE = 257, 1025, 1026, 1027
STB_GLOBAL = 1
STT_OBJECT, STT_FUNC = 1, 2
NOP = 0xD503201F
BID_190 = bytes.fromhex("cd57b23fa4bbad65") + bytes(24)   # the 1.9.0 build id prefix (an identifier, not game data)
BID_OTHER = bytes.fromhex("0123456789abcdef") + bytes(24)
BID_182 = bytes.fromhex("de0b6ac4efc1dda7") + bytes(24)   # the 1.8.2 build id prefix (also only an identifier)


def relr_encode(addrs):
    """Pack word offsets the way DT_RELR does: an address, then bitmaps of the 63 words after it."""
    addrs = sorted(addrs)
    out, i = [], 0
    while i < len(addrs):
        out.append(addrs[i])
        where = addrs[i] + 8
        i += 1
        while True:
            bits = 0
            while i < len(addrs) and 0 <= addrs[i] - where < 63 * 8:
                bits |= 1 << ((addrs[i] - where) // 8)
                i += 1
            if not bits:
                break
            out.append((bits << 1) | 1)
            where += 63 * 8
    return out


def adrp(rd, pc, target):
    imm = ((target & ~0xFFF) - (pc & ~0xFFF)) >> 12
    return 0x90000000 | ((imm & 3) << 29) | (((imm >> 2) & 0x7FFFF) << 5) | rd


class Builder:
    """A tiny made-up main: text 0x0-0x1000, rodata 0x1000-0x2000, data 0x2000-0x3000.

    As in a real main, .dynamic is in the data segment (at DYN): .data.rel.ro (vtables) before it, .data after it."""

    TEXT, RODATA, DATA, END = 0x0, 0x1000, 0x2000, 0x3000
    DYN = 0x2C00
    WRITABLE = 0x2E00                    # past .dynamic: writable .data

    def __init__(self):
        self.img = bytearray(self.END)
        for off in range(0, 0x1000, 4):
            struct.pack_into("<I", self.img, off, NOP)
        self.relr, self.rela, self.jmprel = [], [], []
        self.syms = [("", 0, 0, 0)]      # (name, value, info, shndx)
        self.dyn_extra = []              # more (tag, value) entries for .dynamic
        self.dyn_set = {}                # tag -> value that replaces the one nso_bytes works out

    def u32(self, off, v):
        struct.pack_into("<I", self.img, off, v & 0xFFFFFFFF)

    def u64(self, off, v):
        struct.pack_into("<Q", self.img, off, v & 0xFFFFFFFFFFFFFFFF)

    def text(self, off, s):
        self.img[off:off + len(s) + 1] = s.encode() + b"\0"

    def sym(self, name, value=0, stype=STT_FUNC, shndx=0):
        self.syms.append((name, value, (STB_GLOBAL << 4) | stype, shndx))
        return len(self.syms) - 1

    def rel(self, off, target, packed=True):
        if packed:
            self.u64(off, target)        # DT_RELR: the word holds the addend
            self.relr.append(off)
        else:
            self.rela.append((off, R_RELATIVE, 0, target))

    def symrel(self, off, si, rtype=R_ABS64, addend=0):
        (self.jmprel if rtype == R_JUMP_SLOT else self.rela).append((off, rtype, si, addend))

    def code_ref(self, at, target, reg=8):
        """adrp + add at `at` (in text) that build `target`'s address, as a constructor storing a vptr does."""
        self.u32(at, adrp(reg, at, target))
        self.u32(at + 4, 0x91000000 | ((target & 0xFFF) << 10) | (reg << 5) | reg)

    def nso_bytes(self, build_id):
        img = self.img
        # MOD0 at 0x1000; text+4 holds its offset; the dynamic section at DYN
        self.u32(4, 0x1000)
        img[0x1000:0x1004] = b"MOD0"
        self.u32(0x1004, self.DYN - 0x1000)
        strtab, symtab, rela, relr, jmprel, hash_ = 0x1200, 0x1300, 0x1400, 0x1600, 0x1700, 0x1900
        names = bytearray(b"\0")
        for k, (name, value, info, shndx) in enumerate(self.syms):
            noff = 0
            if name:
                noff = len(names)
                names += name.encode() + b"\0"
            struct.pack_into("<IBBHQQ", img, symtab + 24 * k, noff, info, 0, shndx, value, 0)
        assert len(names) <= 0x100
        img[strtab:strtab + len(names)] = names
        for k, (off, t, si, add) in enumerate(self.rela):
            struct.pack_into("<QQq", img, rela + 24 * k, off, (si << 32) | t, add)
        for k, (off, t, si, add) in enumerate(self.jmprel):
            struct.pack_into("<QQq", img, jmprel + 24 * k, off, (si << 32) | t, add)
        packed = relr_encode(self.relr)
        for k, e in enumerate(packed):
            self.u64(relr + 8 * k, e)
        self.u32(hash_, 1)
        self.u32(hash_ + 4, len(self.syms))
        dyn = [(5, strtab), (6, symtab), (10, len(names)), (11, 24), (4, hash_),
               (7, rela), (8, 24 * len(self.rela)), (9, 24),
               (36, relr), (35, 8 * len(packed)), (37, 8),
               (23, jmprel), (2, 24 * len(self.jmprel))]
        dyn = [(tag, self.dyn_set.get(tag, val)) for tag, val in dyn] + self.dyn_extra + [(0, 0)]
        assert self.DYN + 16 * len(dyn) <= self.WRITABLE
        for k, (tag, val) in enumerate(dyn):
            struct.pack_into("<QQ", img, self.DYN + 16 * k, tag, val)
        hdr = bytearray(nso.HEADER_SIZE)
        hdr[0:4] = nso.MAGIC
        hdr[0x40:0x60] = build_id
        segs = [nso.Segment(name, lo, bytes(img[lo:hi]), True, True)
                for name, lo, hi in (("text", 0, 0x1000), ("rodata", 0x1000, 0x2000), ("data", 0x2000, 0x3000))]
        flags = 0b111111
        struct.pack_into("<I", hdr, 0x0C, flags)
        return nso.Nso(bytes(hdr), segs, b"\0", 0x100).to_bytes()


def sample(build_id=BID_190):
    """Four classes in test::, one with an outside base, vtables, a PLT import, exports and some decoys."""
    b = Builder()
    CLASS = b.sym("_ZTVN10__cxxabiv117__class_type_infoE", stype=STT_OBJECT)
    SI = b.sym("_ZTVN10__cxxabiv120__si_class_type_infoE", stype=STT_OBJECT)
    VMI = b.sym("_ZTVN10__cxxabiv121__vmi_class_type_infoE", stype=STT_OBJECT)
    PURE = b.sym("__cxa_pure_virtual")
    EXPORT = b.sym("_ZN4test4Base6exportEv", 0x140, STT_FUNC, shndx=1)
    SLEEP = b.sym("_ZN2nn2os11SleepThreadENS_8TimeSpanE")
    THING = b.sym("_ZTIN2nn5ThingE", stype=STT_OBJECT)
    IMPDATA = b.sym("nnImportedData", stype=STT_OBJECT)
    b.sym("g_testExport", 0x2A00, STT_OBJECT, shndx=3)

    for off, s in ((0x1100, "N4test4BaseE"), (0x1120, "N4test7DerivedE"), (0x1140, "N4test5OtherE"),
                   (0x1160, "N4test5MultiE"), (0x1180, "N4test3ExtE")):
        b.text(off, s)
    b.img[0x11E0:0x11E4] = b"\xff\xfe\x01\x00"            # not a type name

    # typeinfo objects
    b.symrel(0x2000, CLASS, addend=16)
    b.rel(0x2008, 0x1100)                                 # test::Base
    b.symrel(0x2010, SI, addend=16)
    b.rel(0x2018, 0x1120)
    b.rel(0x2020, 0x2000)                                 # test::Derived : test::Base
    b.symrel(0x2028, CLASS, addend=16)
    b.rel(0x2030, 0x1140, packed=False)                   # test::Other (explicit addend)
    b.symrel(0x2038, VMI, addend=16)
    b.rel(0x2040, 0x1160)
    b.u32(0x2048, 0)
    b.u32(0x204C, 2)
    b.rel(0x2050, 0x2010)
    b.u64(0x2058, 2)                                      # public, at 0
    b.rel(0x2060, 0x2028)
    b.u64(0x2068, (0x10 << 8) | 2)                        # public, at +0x10
    b.symrel(0x2070, SI, addend=16)
    b.rel(0x2078, 0x1180)
    b.symrel(0x2080, THING)                               # test::Ext : nn::Thing (typeinfo outside main)
    b.symrel(0x2500, CLASS, addend=16)
    b.rel(0x2508, 0x11E0)                                 # a bad name: ignored

    # vtables
    def vtable(off, ti, slots, ott=0, packed=True):
        b.u64(off, ott)
        b.rel(off + 8, ti, packed)
        for k, t in enumerate(slots):
            if isinstance(t, tuple):                      # ("sym", symbol index)
                b.symrel(off + 16 + 8 * k, t[1])
            else:
                b.rel(off + 16 + 8 * k, t, packed)

    vtable(0x2100, 0x2000, [0x100, 0x110, ("sym", PURE), ("sym", EXPORT)])
    vtable(0x2140, 0x2010, [0x100, 0x120, 0x130, 0x140, 0x150])
    vtable(0x2180, 0x2028, [0x160, 0x170])
    vtable(0x21C0, 0x2038, [0x100, 0x120, 0x180, 0x140, 0x150, 0x190])
    vtable(0x2200, 0x2038, [0x1A0, 0x170], ott=-0x10)
    vtable(0x2240, 0x2070, [0x1B0, 0x1C0], packed=False)

    # imports: one through a PLT stub at 0x800, one through a GOT slot only
    b.symrel(0x2300, SLEEP, R_JUMP_SLOT)
    b.symrel(0x2308, IMPDATA, R_GLOB_DAT)
    b.u32(0x800, adrp(16, 0x800, 0x2300))
    b.u32(0x804, 0xF9400211 | ((0x300 // 8) << 10))       # ldr x17, [x16, #0x300]
    b.u32(0x808, 0x91000210 | (0x300 << 10))              # add x16, x16, #0x300
    b.u32(0x80C, 0xD61F0220)                              # br x17

    # decoys that point at a typeinfo but are not vtables
    b.rel(0x2400, 0x100)
    b.rel(0x2408, 0x2000)                                 # the word before is a pointer
    b.rel(0x2410, 0x110)
    b.u64(0x2440, 0)
    b.rel(0x2448, 0x2000)
    b.u64(0x2450, 0x5555)                                 # no function pointers after it
    b.u64(0x2480, 8)                                      # offset-to-top can't be positive
    b.rel(0x2488, 0x2028)
    b.rel(0x2490, 0x100)
    b.rel(0x2800, 0x1100)                                 # far away: a new DT_RELR address entry

    # vtables without RTTI: [offset-to-top][0][functions]
    def bare_vtable(off, slots, ott=0):
        b.u64(off, ott)
        b.u64(off + 8, 0)
        for k, t in enumerate(slots):
            b.rel(off + 16 + 8 * k, t)

    bare_vtable(0x2600, [0x1D0, 0x1E0])                   # used by code at 0x900 (its address point)
    bare_vtable(0x2620, [0x1F0], ott=-8)                  # its secondary vtable, straight after it
    b.u32(0x900, adrp(8, 0x900, 0x2600))
    b.u32(0x904, 0x91000000 | (0x610 << 10) | (8 << 5) | 8)   # add x8, x8, #0x610
    bare_vtable(0x2680, [0x200])                          # used through a pointer in data
    b.rel(0x2A10, 0x2690)
    bare_vtable(0x26C0, [0x210, 0x100])                   # used by code at 0x910 (its start)
    b.u32(0x910, adrp(9, 0x910, 0x26C0))
    b.u32(0x914, 0x91000000 | (0x6C0 << 10) | (9 << 5) | 9)   # add x9, x9, #0x6c0
    bare_vtable(0x2700, [0x100])                          # nothing uses it: not a vtable
    bare_vtable(0x2740, [0x110], ott=-0x10)               # secondary that follows nothing: not a vtable
    return b.nso_bytes(build_id)


SITES_TEXT = """
Version = namedtuple('Version', 'name build_id supported')
VERSIONS = [
    Version('1.8.2', 'de0b6ac4efc1dda7', True),
    Version('1.9.0', 'cd57b23fa4bbad65', True),
]
Site = namedtuple('Site', 'name kind pattern at')
SITES = [
    Site('fake_entry', 'call', '94000000/fc000000', 0),
    Site('fake_code', 'code', 'd503201f '
         'd503201f', 1),
    Site('fake_code_entry', 'code', 'd503201f', 0),
    Site('fake_ref', 'ref', '90000008/9f00001f', 0),
    Site('fake_imm', 'imm', 'b9400008/ffc003ff', 0),
    Site('main_data', 'data', '', 0),
    Site('main_end', 'end', '', 0),
]
KNOWN = {
    '1.8.2': {
        'fake_entry': 0x7000000999,
    },
    '1.9.0': {
        'fake_entry': 0x7000000140,
        'fake_code': 0x7000000184,
        'fake_code_entry': 0x7000000100,
        'fake_ref': 0x7000002a00,
        'fake_imm': 0x36b0,
        'main_data': 0x7000002000,
        'main_end': 0x7000003100,
        'far_away': 0x7100000000,
    },
}
"""


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class Demangle(unittest.TestCase):
    def test_types(self):
        cases = {
            "N4ksys3act5ActorE": "ksys::act::Actor",
            "3Foo": "Foo",
            "St9exception": "std::exception",
            "N3foo3BarIiEE": "foo::Bar<int>",
            "N4sead9SafeArrayINS_15FixedSafeStringILi32EEELi4EEE": "sead::SafeArray<sead::FixedSafeString<32>, 4>",
            "NSt3__112basic_stringIcNS_11char_traitsIcEENS_9allocatorIcEEEE":
                "std::__1::basic_string<char, std::__1::char_traits<char>, std::__1::allocator<char>>",
            "St6vectorIiSaIiEE": "std::vector<int, std::allocator<int>>",
            "N12_GLOBAL__N_15ThingE": "(anonymous namespace)::Thing",
            "ZN3foo3barEvE5Local": "foo::bar()::Local",
            "N3foo3BazIPKcLb1ELj7EEE": "foo::Baz<char const*, true, 7u>",
            "N3foo4FuncIFviEEE": "foo::Func<void (int)>",
            "*N3foo3BarE": "foo::Bar",
        }
        for mangled, want in cases.items():
            self.assertEqual(codemap.demangle_type(mangled), want, mangled)

    def test_symbols(self):
        cases = {
            "_ZN4ksys3act5Actor4initEPN4sead4HeapE": "ksys::act::Actor::init(sead::Heap*)",
            "_ZNK3foo3Bar3getEv": "foo::Bar::get() const",
            "_ZN3foo3BarC1Ev": "foo::Bar::Bar()",
            "_ZN3foo3BarD0Ev": "foo::Bar::~Bar()",
            "_ZN3foo3BarIiEC2Ei": "foo::Bar<int>::Bar(int)",
            "_ZTVN3foo3BarE": "vtable for foo::Bar",
            "_ZTIN3foo3BarE": "typeinfo for foo::Bar",
            "_Z3maxIiET_S0_S0_": "int max<int>(int, int)",
            "_ZN3foo3bazEPFviE": "foo::baz(void (*)(int))",
            "_ZN3fooeqERKS_": "foo::operator==(foo const&)",
            "_ZThn16_N3foo3Bar1fEv": "non-virtual thunk to foo::Bar::f()",
            "_ZN2nn2os11SleepThreadENS_8TimeSpanE": "nn::os::SleepThread(nn::TimeSpan)",
            "_ZN4sead6System5sInstE": "sead::System::sInst",
            "_ZN3foo3barEv.cold": "foo::bar() [clone .cold]",
            "_ZL5localv": "local()",
            # pack expansions: the pattern once for each element of the pack (as c++filt prints them)
            "_Z1fIJidEEvDpT_": "void f<int, double>(int, double)",
            "_Z1fIJidEEvDpRKT_": "void f<int, double>(int const&, double const&)",
            "_Z1fIJEEvDpT_": "void f<>()",
            "_Z1fIiJcdEEvT_DpPT0_": "void f<int, char, double>(int, char*, double*)",
        }
        for mangled, want in cases.items():
            self.assertEqual(codemap.demangle(mangled), want, mangled)

    def test_fallback(self):
        for raw in ("N3foo", "garbage!", "", "N3fooE3extra", "S_", "T_", "N3foo3BarIiE"):
            self.assertEqual(codemap.demangle_type(raw), raw, raw)
        for raw in ("__cxa_pure_virtual", "nnMain", "_Z", "_ZN3foo", "_Zgarbage"):
            self.assertEqual(codemap.demangle(raw), raw, raw)


class Relocations(unittest.TestCase):
    def test_relr_round_trip(self):
        m = codemap.Module(nso.parse(sample()))
        self.assertEqual(m.ptr[0x2008], 0x1100)              # packed (DT_RELR)
        self.assertEqual(m.ptr[0x2030], 0x1140)              # RELA with an explicit addend
        self.assertEqual(m.ptr[0x2800], 0x1100)              # a second DT_RELR address entry
        self.assertEqual(m.ptr[0x2128], 0x140)               # ABS64 against main's own export
        self.assertEqual(m.u64(0x2030), 0x1140)              # the relocated view holds the offset
        self.assertNotIn(0x2120, m.ptr)                       # an import, not a pointer into main
        self.assertEqual(m.sym_at(0x2120).name, "__cxa_pure_virtual")
        self.assertEqual(m.stats["unknown"], 0)
        self.assertGreater(m.stats["relr"], 30)

    def test_relr_matches_loader_c(self):
        # the same bitmap rules as Savage Wind's loader.c relocate_relr
        enc = relr_encode([0x10, 0x18, 0x20, 0x210, 0x400])
        self.assertEqual(enc[0], 0x10)
        self.assertEqual(enc[1], (0b11 << 1) | 1)                # 0x18, 0x20: bits 0 and 1 of the words after 0x10
        self.assertEqual(enc[2], (1 << 63) | 0b11)               # next 63 words start at 0x210: bits 0 and 62
        b = Builder()
        for off in (0x2010, 0x2018, 0x2020, 0x2210, 0x2400, 0x2F00):
            b.rel(off, off + 1)
        m = codemap.Module(nso.parse(b.nso_bytes(BID_OTHER)))
        self.assertEqual(m.ptr, {off: off + 1 for off in (0x2010, 0x2018, 0x2020, 0x2210, 0x2400, 0x2F00)})
        self.assertEqual(m.stats["relr"], 6)

    def test_plt(self):
        m = codemap.Module(nso.parse(sample()))
        self.assertEqual(m.plt_stubs(), {0x2300: 0x800})

    def test_not_a_main(self):
        b = Builder()
        raw = bytearray(b.nso_bytes(BID_OTHER))
        n = nso.parse(bytes(raw))
        n.write(0x1000, b"NOPE")
        with self.assertRaises(codemap.CodeMapError):
            codemap.Module(n)

    def test_android_packed_refused(self):
        b = Builder()
        b.dyn_extra.append((0x60000011, 0x1400))
        with self.assertRaises(codemap.CodeMapError):
            codemap.Module(nso.parse(b.nso_bytes(BID_OTHER)))

    def test_damaged_relocation_table(self):
        # a table that runs past the module, or entries of size 0: a plain error, not a Python traceback
        for tag, value in ((8, 0x100000), (9, 0), (9, 8), (2, 0x7FFFFFF0)):   # RELASZ, RELAENT, PLTRELSZ
            b = Builder()
            b.rela.append((0x2000, R_RELATIVE, 0, 0x100))
            b.jmprel.append((0x2008, R_JUMP_SLOT, 0, 0))
            b.dyn_set[tag] = value
            with self.subTest(tag=tag, value=value), self.assertRaises(codemap.CodeMapError):
                codemap.Module(nso.parse(b.nso_bytes(BID_OTHER)))

    def test_damaged_main_cli(self):
        import contextlib
        import io
        b = Builder()
        b.dyn_set[9] = 0                                       # DT_RELAENT 0
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main"
            path.write_bytes(b.nso_bytes(BID_OTHER))
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                rc = codemap.main([str(path), "--out", str(Path(tmp) / "out"), "--no-sites"])
        self.assertEqual(rc, 1)
        self.assertTrue(err.getvalue().startswith("codemap: "))
        self.assertIn("DT_RELAENT", err.getvalue())

    def test_symbol_reloc_outside_module_ignored(self):
        b = Builder()
        si = b.sym("someImport")
        b.symrel(0x7FFFFFF0, si)
        m = codemap.Module(nso.parse(b.nso_bytes(BID_OTHER)))
        self.assertEqual(m.symref, {})


class Rtti(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cm = codemap.build(nso.parse(sample()), SITES_TEXT)
        cls.by_name = {t.name: t for t in cls.cm.typeinfos.values()}

    def test_typeinfos(self):
        self.assertEqual(sorted(self.by_name), ["test::Base", "test::Derived", "test::Ext", "test::Multi",
                                                "test::Other"])
        self.assertEqual(self.by_name["test::Derived"].kind, "si")
        self.assertEqual([b.ti for b in self.by_name["test::Derived"].bases], [0x2000])
        multi = self.by_name["test::Multi"]
        self.assertEqual(multi.kind, "vmi")
        self.assertEqual([(b.ti, b.offset, b.public) for b in multi.bases], [(0x2010, 0, True), (0x2028, 0x10, True)])
        self.assertEqual(self.by_name["test::Ext"].bases[0].mangled, "N2nn5ThingE")
        self.assertEqual(multi.depth, 2)

    def test_vtables(self):
        base = self.by_name["test::Base"]
        self.assertEqual([(v.offset, v.ott) for v in base.vtables], [(0x2100, 0)])
        self.assertEqual(base.vtables[0].slots, [0x100, 0x110, "__cxa_pure_virtual", 0x140])
        multi = self.by_name["test::Multi"]
        self.assertEqual([(v.offset, v.ott, v.label) for v in multi.vtables],
                         [(0x21C0, 0, ""), (0x2200, -0x10, "@0x10")])
        self.assertEqual(len(multi.primary.slots), 6)        # stops at the secondary's offset-to-top
        self.assertEqual(self.by_name["test::Ext"].vtables[0].slots, [0x1B0, 0x1C0])
        self.assertEqual(self.cm.stats["rtti_vtables"], 6)   # none of the decoys
        self.assertEqual(self.cm.stats["vtables"], 10)

    def test_vtables_without_rtti(self):
        groups = {g.name: g for g in self.cm.unnamed}
        self.assertEqual(sorted(groups), ["vt_7000002600", "vt_7000002680", "vt_70000026c0"])
        g = groups["vt_7000002600"]
        self.assertEqual([(v.offset, v.ott, v.label, v.slots) for v in g.vtables],
                         [(0x2600, 0, "", [0x1D0, 0x1E0]), (0x2620, -8, "@0x8", [0x1F0])])
        self.assertEqual(groups["vt_70000026c0"].primary.slots, [0x210, 0x100])
        self.assertEqual(groups["vt_7000002680"].kind, "none")
        refs = codemap.Module(nso.parse(sample())).code_refs()
        self.assertTrue({0x2610, 0x26C0, 0x2300} <= refs)    # the two vtables and the PLT's GOT slot

    def test_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "map"
            codemap.write(self.cm, out)
            funcs = read_csv(out / "functions.csv")
            classes = {r["class"]: r for r in read_csv(out / "classes.csv")}
            summary = (out / "summary.txt").read_text(encoding="utf-8")
        rows = {(r["address"], r["kind"]): r for r in funcs}

        vf = rows[("0x7000000100", "vfunc")]
        self.assertEqual(vf["name"], "test::Base::vf_0")      # the base-most class wins
        self.assertIn("test::Derived::vf_0", vf["notes"])
        self.assertIn("test::Multi::vf_0", vf["notes"])
        self.assertEqual(rows[("0x7000000110", "vfunc")]["notes"], "")
        self.assertEqual(rows[("0x7000000120", "vfunc")]["name"], "test::Derived::vf_1")
        self.assertEqual(rows[("0x70000001a0", "vfunc")]["name"], "test::Multi::vf_0@0x10")
        self.assertEqual(rows[("0x7000000170", "vfunc")]["name"], "test::Other::vf_1")
        self.assertIn("test::Multi::vf_1@0x10", rows[("0x7000000170", "vfunc")]["notes"])
        self.assertEqual(rows[("0x7000000140", "export")]["name"], "test::Base::export()")
        self.assertEqual(rows[("0x7000000140", "vfunc")]["name"], "test::Base::vf_3")
        self.assertEqual(rows[("0x7000002a00", "export")]["name"], "g_testExport")
        imp = rows[("0x7000000800", "import")]
        self.assertEqual(imp["name"], "nn::os::SleepThread(nn::TimeSpan)")
        self.assertIn("PLT stub", imp["notes"])
        self.assertIn("pointer slot", rows[("0x7000002308", "import")]["notes"])
        self.assertFalse(any(r["name"] == "__cxa_pure_virtual" for r in funcs))
        self.assertIn("vt_70000026c0::vf_1", vf["notes"])      # a class without RTTI never wins over one with it
        self.assertEqual(rows[("0x70000001d0", "vfunc")]["name"], "vt_7000002600::vf_0")
        self.assertEqual(rows[("0x70000001f0", "vfunc")]["name"], "vt_7000002600::vf_0@0x8")
        self.assertNotIn("vt_7000002700", vf["notes"])         # the unused decoy
        # sites: only addresses (no imm, data or end), only inside main
        sites = {r["name"]: r["address"] for r in funcs if r["kind"] == "site"}
        self.assertEqual(sites, {"fake_entry": "0x7000000140", "fake_code": "0x7000000184",
                                 "fake_code_entry": "0x7000000100", "fake_ref": "0x7000002a00"})
        self.assertIn("function entry", rows[("0x7000000140", "site")]["notes"])
        # a code site is "an instruction"; it says "function entry" only where a function is known to start
        self.assertEqual(rows[("0x7000000184", "site")]["notes"], "Savage Wind site: an instruction")
        self.assertEqual(rows[("0x7000000100", "site")]["notes"],
                         "Savage Wind site: an instruction (here also a function entry)")
        # rows come sorted by address
        addrs = [int(r["address"], 16) for r in funcs]
        self.assertEqual(addrs, sorted(addrs))

        self.assertEqual(classes["test::Multi"]["vtables"], "0x70000021c0; 0x7000002200@0x10")
        self.assertEqual(classes["test::Multi"]["bases"], "test::Derived; test::Other +0x10")
        self.assertEqual(classes["test::Multi"]["virtuals"], "6")
        self.assertEqual(classes["test::Base"]["virtuals"], "4")
        self.assertEqual(classes["test::Ext"]["bases"], "nn::Thing")
        self.assertEqual(classes["test::Derived"]["typeinfo"], "0x7000002010")
        self.assertEqual(classes["test::Derived"]["mangled"], "N4test7DerivedE")
        bare = classes["vt_7000002600"]
        self.assertEqual((bare["typeinfo"], bare["vtables"], bare["virtuals"], bare["rtti_kind"]),
                         ("", "0x7000002600; 0x7000002620@0x8", "2", "none"))
        self.assertEqual(list(classes)[:5], ["test::Base", "test::Derived", "test::Ext", "test::Multi",
                                             "test::Other"])           # classes with RTTI first
        self.assertIn("never share or commit", summary)
        self.assertIn("4 from Savage Wind 76e61af (version 1.9.0)", summary)

    def test_sites_need_matching_build(self):
        for bid in (BID_OTHER, BID_182):
            cm = codemap.build(nso.parse(sample(bid)), SITES_TEXT)
            self.assertEqual(cm.stats["sites"], 0)
            self.assertFalse(any(r.kind == "site" for r in cm.functions))
            self.assertIn("not used", cm.sites_note)
            self.assertIn("1.9.0 only", cm.sites_note)
        cm = codemap.build(nso.parse(sample()), None)
        self.assertEqual(cm.stats["sites"], 0)

    def test_sites_note_names_its_source(self):
        cm = codemap.build(nso.parse(sample()), SITES_TEXT, sites_source="my_sites.py")
        self.assertEqual(cm.sites_note, "4 from my_sites.py (version 1.9.0)")
        self.assertNotIn("76e61af", cm.sites_note)

    def test_parse_sites(self):
        version, sites = codemap.parse_sites(SITES_TEXT, "cd57b23fa4bbad65" + "0" * 48)
        self.assertEqual(version, "1.9.0")
        kinds = {n: k for n, _a, k in sites}
        self.assertEqual(kinds["fake_code"], "code")
        self.assertEqual(kinds["far_away"], "")
        self.assertNotIn(0x7000000999, {a for _n, a, _k in sites})   # nothing from KNOWN['1.8.2']
        self.assertEqual(codemap.parse_sites(SITES_TEXT, "ff" * 32), (None, []))

    def test_parse_sites_only_190(self):
        # 1.8.2 is listed (and in KNOWN) in the release, but only 1.9.0's addresses are used
        self.assertEqual(codemap.parse_sites(SITES_TEXT, "de0b6ac4efc1dda7" + "0" * 48), (None, []))
        # a copy whose VERSIONS gives 1.9.0 another build id: its KNOWN['1.9.0'] is not for this main
        other = SITES_TEXT.replace("'1.9.0', 'cd57b23fa4bbad65'", "'1.9.0', '0123456789abcdef'")
        self.assertEqual(codemap.parse_sites(other, "cd57b23fa4bbad65" + "0" * 48), (None, []))


def unnamed_of(b):
    """{class name: [(vtable offset, slots)]} for the vtables without RTTI that build() finds in Builder b."""
    cm = codemap.build(nso.parse(b.nso_bytes(BID_OTHER)))
    return {g.name: [(v.offset, v.slots) for v in g.vtables] for g in cm.unnamed}, cm


class VtablesWithoutRtti(unittest.TestCase):
    def test_init_array_at_data_start_is_not_a_class(self):
        # .init_array starts the data segment; the zero words before it are rodata padding, and startup code
        # loads its address. It holds constructors, not virtual functions.
        for tags in (True, False):
            b = Builder()
            for k, t in enumerate((0x300, 0x340, 0x380)):
                b.rel(0x2000 + 8 * k, t)
            b.code_ref(0x900, 0x2000)
            if tags:
                b.dyn_extra += [(25, 0x2000), (27, 24)]        # DT_INIT_ARRAY, DT_INIT_ARRAYSZ
            found, cm = unnamed_of(b)
            with self.subTest(dt_tags=tags):
                self.assertEqual(found, {})
                self.assertFalse(any(r.kind == "vfunc" for r in cm.functions))

    def test_func_arrays_inside_data_are_skipped(self):
        # a fini array (and a preinit one) behind two zero words, used by code: not vtables because the dynamic
        # section says what they are. Without those tags the same bytes would pass as a vtable.
        for tag, size in ((26, 28), (32, 33), (None, None)):
            b = Builder()
            b.rel(0x2410, 0x300)
            b.rel(0x2418, 0x340)
            b.code_ref(0x900, 0x2410)
            if tag:
                b.dyn_extra += [(tag, 0x2410), (size, 16)]
            found, _cm = unnamed_of(b)
            with self.subTest(tag=tag):
                self.assertEqual(found, {} if tag else {"vt_7000002400": [(0x2400, [0x300, 0x340])]})

    def test_writable_data_after_dynamic_is_skipped(self):
        # C++ vtables are read-only once relocated (.data.rel.ro, before .dynamic); a [0][0][functions] struct in
        # writable .data after it is something else
        for at, want in ((Builder.WRITABLE, {}), (0x2600, {"vt_7000002600": [(0x2600, [0x300, 0x340])]})):
            b = Builder()
            b.rel(at + 16, 0x300)
            b.rel(at + 24, 0x340)
            b.code_ref(0x900, at + 16)
            with self.subTest(at=hex(at)):
                self.assertEqual(unnamed_of(b)[0], want)

    def test_abstract_class_import_first(self):
        # [0][0][__cxa_pure_virtual][f][g], and its secondary vtable [-8][0][__cxa_pure_virtual][h] straight after
        b = Builder()
        pure = b.sym("__cxa_pure_virtual")
        b.symrel(0x2610, pure)
        b.rel(0x2618, 0x300)
        b.rel(0x2620, 0x340)
        b.u64(0x2628, -8)
        b.symrel(0x2638, pure)
        b.rel(0x2640, 0x380)
        b.code_ref(0x900, 0x2610)
        found, cm = unnamed_of(b)
        self.assertEqual(found, {"vt_7000002600": [(0x2600, ["__cxa_pure_virtual", 0x300, 0x340]),
                                                   (0x2628, ["__cxa_pure_virtual", 0x380])]})
        names = {r.offset: r.name for r in cm.functions if r.kind == "vfunc"}
        self.assertEqual(names, {0x300: "vt_7000002600::vf_1", 0x340: "vt_7000002600::vf_2",
                                 0x380: "vt_7000002600::vf_1@0x8"})

    def test_got_run_is_not_a_vtable(self):
        # GOT entries (GLOB_DAT / JUMP_SLOT) behind two zero words: imports, but not vtable slots (those are ABS64)
        b = Builder()
        f, g = b.sym("importedF"), b.sym("importedG")
        b.symrel(0x2610, f, R_GLOB_DAT)
        b.symrel(0x2618, g, R_GLOB_DAT)
        b.code_ref(0x900, 0x2610)
        self.assertEqual(unnamed_of(b)[0], {})


class Cli(unittest.TestCase):
    def test_refuses_output_inside_repo(self):
        with self.assertRaises(codemap.CodeMapError):
            codemap.check_out_dir(codemap.REPO / "docs" / "map")
        codemap.check_out_dir(codemap.REPO / "out" / "codemap")   # allowed: git ignores out/

    @unittest.skipUnless(os.name == "nt", "Windows path forms")
    def test_refuses_other_spellings_of_the_repo(self):
        repo = str(codemap.REPO.resolve())
        docs = repo + "\\docs\\map"
        refused = ["\\\\?\\" + docs, "//?/" + docs.replace("\\", "/"), "\\\\.\\" + docs, docs.upper()]
        drive, rest = repo[0], repo[2:]
        shares = ["\\\\localhost\\" + drive + "$" + rest, "\\\\?\\UNC\\localhost\\" + drive + "$" + rest,
                  "\\\\127.0.0.1\\" + drive.lower() + "$" + rest]
        for share in shares:
            try:
                os.stat(share)               # an admin share: only checkable where this user can reach it
            except OSError:
                continue
            refused.append(share + "\\docs\\map")
        volumes = getattr(os, "listvolumes", None)
        if volumes:
            try:
                vol = next((v for v in volumes() if drive.upper() + ":\\" in os.listmounts(v)), None)
            except OSError:
                vol = None
            if vol:
                refused.append(vol + rest.lstrip("\\") + "\\docs\\map")    # \\?\Volume{...}\Users\...
        for p in refused:
            with self.subTest(path=p), self.assertRaises(codemap.CodeMapError):
                codemap.check_out_dir(Path(p))
        codemap.check_out_dir(Path("\\\\?\\" + repo + "\\out\\codemap"))   # still allowed
        self.assertEqual(codemap._plain_path("\\\\?\\C:\\x\\y"), "C:\\x\\y")
        self.assertEqual(codemap._plain_path("\\\\?\\UNC\\host\\share\\x"), "\\\\host\\share\\x")
        self.assertEqual(codemap._plain_path("\\\\?\\Volume{1}\\x"), "\\\\?\\Volume{1}\\x")   # left as it is

    def test_refuses_repo_by_identity(self):
        # a path whose name doesn't look like the repo but is the same folder (as a share or a subst drive is)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp).resolve()
            repo = tmp / "repo"
            (repo / "out").mkdir(parents=True)
            alias = tmp / "alias"
            real_stat = os.stat

            def fake_stat(p, *a, **k):        # alias is "the same folder" as repo
                p = Path(p)
                if p == alias or alias in p.parents:
                    p = repo / p.relative_to(alias)
                return real_stat(p, *a, **k)

            old = codemap.REPO
            codemap.REPO = repo
            try:
                with unittest.mock.patch.object(codemap.os, "stat", fake_stat):
                    with self.assertRaises(codemap.CodeMapError):
                        codemap.check_out_dir(alias / "docs" / "map")
                    with self.assertRaises(codemap.CodeMapError):
                        codemap.check_out_dir(alias)
                    codemap.check_out_dir(alias / "out" / "codemap")
                    codemap.check_out_dir(tmp / "elsewhere")
            finally:
                codemap.REPO = old

    def test_cli_prints_counts(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main"
            path.write_bytes(sample())
            sites = Path(tmp) / "game_sites.txt"
            sites.write_text(SITES_TEXT, encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = codemap.main([str(path), "--out", str(Path(tmp) / "out"), "--sites", str(sites)])
            self.assertEqual(rc, 0)
            self.assertTrue((Path(tmp) / "out" / "functions.csv").is_file())
        text = buf.getvalue()
        self.assertIn("classes   5 with RTTI", text)
        self.assertIn("3 without", text)
        self.assertIn("vtables   10", text)
        self.assertNotIn("1f2003d5", text.lower())             # no code bytes
        # names from --sites FILE are credited to that file, not to the release
        self.assertIn(f"sites     4 from {sites} (version 1.9.0)", text)
        self.assertNotIn("76e61af", text)


if __name__ == "__main__":
    unittest.main()
