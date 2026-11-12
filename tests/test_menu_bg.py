"""Tests for tools/menu_bg.py (the main menu picture). Every picture, layout, animation, texture, pack, size table,
install and library here is made up in code, in temp folders: no game file is read, no game is started (deploy and
the game check are mocked), and nothing is written into the repo."""
import contextlib
import io
import json
import os
import random
import re
import shlex
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import menu_bg as mb  # noqa: E402
import modman as mm  # noqa: E402
import test_modman as tm  # noqa: E402  (as a module: its tests don't run twice)
import wiiu_convert as wc  # noqa: E402
from test_nso import make_nso  # noqa: E402
from wiiu_model import nx_bntx  # noqa: E402
from wiiu_model.neutral import Texture, TextureSet  # noqa: E402

try:
    from PIL import Image as PILImage
except ImportError:  # (Pillow is optional)
    PILImage = None


def quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = fn(*a, **kw)
    return r, buf.getvalue()


def run(argv):
    return quiet(mb.main, argv)


# ---------------------------------------------------------------- made-up pictures

def gradient(w, h, alpha=None) -> mb.Picture:
    px = bytearray()
    for y in range(h):
        for x in range(w):
            a = 255 if alpha is None else alpha(x, y)
            px += bytes((x * 255 // max(1, w - 1), y * 255 // max(1, h - 1), (x + y) * 255 // max(1, w + h - 2), a))
    return mb.Picture(w, h, px)


def write_pic(path: Path, pic: mb.Picture) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mb.png_bytes(pic))
    return path


# ---------------------------------------------------------------- a tiny PNG writer for every colour type

def _filter_line(ft, line, prev, bpp):
    out = bytearray(len(line))
    for i, x in enumerate(line):
        a = line[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if ft == 0:
            p = 0
        elif ft == 1:
            p = a
        elif ft == 2:
            p = b
        elif ft == 3:
            p = (a + b) // 2
        else:
            pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
            p = a if pa <= pb and pa <= pc else b if pb <= pc else c
        out[i] = (x - p) & 255
    return out


def _pack(samples, depth):
    if depth == 16:
        return b"".join(struct.pack(">H", s) for s in samples)
    if depth == 8:
        return bytes(samples)
    out, acc, n = bytearray(), 0, 0
    for s in samples:
        acc = (acc << depth) | s
        n += depth
        if n == 8:
            out.append(acc)
            acc = n = 0
    if n:
        out.append(acc << (8 - n))
    return bytes(out)


def make_png(w, h, ctype, depth, sample, *, plte=None, trns=None, interlace=False, filters=(0, 1, 2, 3, 4)):
    """sample(x, y) -> tuple of channel samples."""
    ch = mb.PNG_CHANNELS[ctype]
    bpp = max(1, ch * depth // 8)
    raw = bytearray()
    passes = ([(x0, y0, dx, dy) for x0, y0, dx, dy in mb.ADAM7] if interlace else [(0, 0, 1, 1)])
    k = 0
    for x0, y0, dx, dy in passes:
        xs, ys = list(range(x0, w, dx)), list(range(y0, h, dy))
        if not xs or not ys:
            continue
        prev = bytes(len(_pack([v for x in xs for v in sample(x, 0)], depth)))
        for y in ys:
            line = _pack([v for x in xs for v in sample(x, y)], depth)
            ft = filters[k % len(filters)]
            k += 1
            raw += bytes((ft,)) + _filter_line(ft, line, prev, bpp)
            prev = line

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    out = mb.PNG_SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, 1 if interlace else 0))
    if plte is not None:
        out += chunk(b"PLTE", plte)
    if trns is not None:
        out += chunk(b"tRNS", trns)
    z = zlib.compress(bytes(raw))
    return out + chunk(b"IDAT", z[:len(z) // 2]) + chunk(b"IDAT", z[len(z) // 2:]) + chunk(b"IEND", b"")


# ---------------------------------------------------------------- a tiny layout (FLYT) and animation (FLAN) writer

def sec(magic: bytes, body: bytes) -> bytes:
    body = body + bytes(-len(body) % 4)
    return magic + struct.pack("<I", 8 + len(body)) + body


def material(name, tex=None, *, black=(0, 0, 0, 0), white=(255, 255, 255, 255), srt=(0, 0, 0, 1, 1), source=0,
             blend=None, proj=None, wrap=0):
    flags, tail = 0, b""
    if tex is not None:
        flags |= 1
        tail += struct.pack("<HBB", tex, wrap | 4, wrap | 4)
    flags |= 1 << 2
    tail += struct.pack("<5f", *srt)
    flags |= 1 << 4
    tail += bytes((0, source)) + bytes(14)
    if blend is not None:
        flags |= 0x400
        tail += bytes(blend)
    if proj is not None:
        flags |= 1 << 15
        tail += struct.pack("<4fB3x", *proj)
    return name.encode().ljust(0x1C, b"\0") + struct.pack("<I", flags) + bytes((0, 2, 4, 8)) + bytes(black) \
        + bytes(white) + tail


def usd1(items: dict) -> bytes:
    n = len(items)
    base = 12 + 12 * n
    blob, ents = bytearray(), []
    for k, (name, val) in enumerate(items.items()):
        e = 12 + 12 * k
        noff = base + len(blob) - e
        blob += name.encode() + b"\0"
        blob += bytes(-len(blob) % 4)
        if isinstance(val, str):
            typ, data, cnt = 0, val.encode(), len(val.encode())
        elif all(isinstance(v, int) for v in val):
            typ, data, cnt = 1, struct.pack(f"<{len(val)}i", *val), len(val)
        else:
            typ, data, cnt = 2, struct.pack(f"<{len(val)}f", *val), len(val)
        doff = base + len(blob) - e
        blob += data + bytes(-len(data) % 4)
        ents.append(struct.pack("<IIHBB", noff, doff, cnt, typ, 0))
    return sec(b"usd1", struct.pack("<HH", n, 0) + b"".join(ents) + bytes(blob))


def pane(kind, name, children=(), *, flags=1, origin=0, alpha=255, t=(0, 0), r=(0, 0, 0), s=(1, 1), size=(30, 40),
         mat=None, colors=None, uvs=(mb.STD_UV,), user=None):
    return dict(kind=kind, name=name, children=list(children), flags=flags, origin=origin, alpha=alpha, t=t, r=r,
                s=s, size=size, mat=mat, colors=colors, uvs=uvs, user=user)


def pane_sections(p) -> list[bytes]:
    body = struct.pack("<4B", p["flags"], p["origin"], p["alpha"], 0) + p["name"].encode().ljust(24, b"\0") \
        + bytes(8) + struct.pack("<10f", p["t"][0], p["t"][1], 0, *p["r"], p["s"][0], p["s"][1], *p["size"])
    if p["kind"] == "pic1":
        cols = p["colors"] or [(255, 255, 255, 255)] * 4
        body += b"".join(bytes(c) for c in cols) + struct.pack("<HBB", p["mat"], len(p["uvs"]), 0)
        body += b"".join(struct.pack("<8f", *uv) for uv in p["uvs"])
    out = [sec(p["kind"].encode(), body)]
    if p["user"]:
        out.append(usd1(p["user"]))
    if p["children"]:
        out.append(sec(b"pas1", b""))
        for c in p["children"]:
            out += pane_sections(c)
        out.append(sec(b"pae1", b""))
    return out


def flyt(textures, materials, root, size=(1280.0, 720.0), centered=1) -> bytes:
    sections = [sec(b"lyt1", struct.pack("<B3x4f", centered, size[0], size[1], 0, 0) + b"Fake_00\0")]
    names = b"".join(t.encode() + b"\0" for t in textures)
    offs, o = [], 4 * len(textures)
    for t in textures:
        offs.append(o)
        o += len(t.encode()) + 1
    sections.append(sec(b"txl1", struct.pack("<HH", len(textures), 0) + struct.pack(f"<{len(offs)}I", *offs) + names))
    mats = [m + bytes(-len(m) % 4) for m in materials]
    moffs, o = [], 12 + 4 * len(mats)
    for m in mats:
        moffs.append(o)
        o += len(m)
    sections.append(sec(b"mat1", struct.pack("<HH", len(mats), 0) + struct.pack(f"<{len(mats)}I", *moffs)
                        + b"".join(mats)))
    sections += pane_sections(root)
    body = b"".join(sections)
    return b"FLYT\xff\xfe" + struct.pack("<HIIHH", 0x14, 0x08000000, 0x14 + len(body), len(sections), 0) + body


def flan(frames: int, loop: bool, panes: dict, mats: dict | None = None) -> bytes:
    """panes: {name: [(tag, target, keys, step)]}; mats: {name: [(tag, index, target, keys, step)]}.
    keys: [(frame, value, slope)]."""
    entries = [(n, 0, [(tag, 0, tgt, keys, step) for tag, tgt, keys, step in tags]) for n, tags in panes.items()]
    entries += [(n, 1, tags) for n, tags in (mats or {}).items()]
    blobs = []
    for name, kind, tags in entries:
        by_tag: dict = {}
        for tag, idx, tgt, keys, step in tags:
            by_tag.setdefault(tag, []).append((idx, tgt, keys, step))
        tag_blobs = []
        for tag, groups in by_tag.items():
            gb = []
            for idx, tgt, keys, step in groups:
                if step:
                    kb = b"".join(struct.pack("<fHH", f, int(v), 0) for f, v, _ in keys)
                else:
                    kb = b"".join(struct.pack("<3f", f, v, s) for f, v, s in keys)
                gb.append(struct.pack("<BBBBHHI", idx, tgt, 1 if step else 2, 0, len(keys), 0, 12) + kb)
            head = 8 + 4 * len(gb)
            offs, o = [], head
            for g in gb:
                offs.append(o)
                o += len(g)
            tag_blobs.append(tag.encode() + struct.pack("<B3x", len(gb)) + struct.pack(f"<{len(offs)}I", *offs)
                             + b"".join(gb))
        head = 32 + 4 * len(tag_blobs)
        offs, o = [], head
        for t in tag_blobs:
            offs.append(o)
            o += len(t)
        blobs.append(name.encode().ljust(28, b"\0") + struct.pack("<BBH", len(tag_blobs), kind, 0)
                     + struct.pack(f"<{len(offs)}I", *offs) + b"".join(tag_blobs))
    table_at = 20
    offs, o = [], table_at + 4 * len(blobs)
    for b in blobs:
        offs.append(o)
        o += len(b)
    body = struct.pack("<HBBHHI", frames, int(loop), 0, 0, len(blobs), table_at) \
        + struct.pack(f"<{len(offs)}I", *offs) + b"".join(blobs)
    section = b"pai1" + struct.pack("<I", 8 + len(body)) + body
    return b"FLAN\xff\xfe" + struct.pack("<HIIHH", 0x14, 0x08000000, 0x14 + len(section), 1, 0) + section


def lin(f0, v0, f1, v1):
    s = (v1 - v0) / (f1 - f0)
    return [(f0, v0, s), (f1, v1, s)]


def fade(a, b, c, d):
    return [(a, 0, 0), (b, 255, 0), (c, 255, 0), (d, 0, 0)]


def plateau(v1, v2, v3):
    return [(0, v1, 0), (95, v1, 0), (105, v2, 0), (195, v2, 0), (205, v3, 0), (300, v3, 0)]


# The made-up menu: 3 slideshow sets (back + front, a single in a half-scale parent, a right-anchored single that
# pans down), a pattern with animated tint, a capture copy (glow) at half colour, a light pane, a logo, 4 parts.
TEXTURES = [("Fake_Pattern^r", 0x1D01, 16, 16, (2, 2, 2, 1)), ("Pic_Back^o", 0x1A06, 64, 36, (2, 3, 4, 5)),
            ("Pic_Front^q", 0x1C06, 32, 16, (2, 3, 4, 5)), ("Pic_A^q", 0x1C06, 40, 44, (2, 3, 4, 5)),
            ("Pic_B^q", 0x1C06, 48, 32, (2, 3, 4, 5)), ("Logo^l", 0x0B06, 16, 8, (2, 3, 4, 5)),
            ("Panel^r", 0x0201, 8, 8, (1, 1, 1, 2)), ("Blank^r", 0x1D01, 8, 8, (2, 2, 2, 1))]
SLOT_NAMES = ["Pic_Back^o", "Pic_Front^q", "Pic_A^q", "Pic_B^q"]
OTHER_NAMES = ["Fake_Pattern^r", "Logo^l", "Panel^r", "Blank^r"]


def fake_layout(*, rotate_a=False, slots=True) -> bytes:
    mats = [material("Cap_A", 3, white=(128, 128, 128, 255)),
            material("P_Bg", 0, srt=(0, 0, 0, 2, 2), source=4, proj=(0, 0, 1, 1, 4), wrap=1),
            material("Back", 1), material("Front", 2), material("Single_A", 3), material("Single_B", 4),
            material("Light", 7, blend=(1, 4, 1, 0)), material("Logo", 5),
            material("Black", 7, black=(0, 0, 0, 255), white=(0, 0, 0, 255)),
            material("Panel", 6, white=(10, 20, 30, 255))]
    words = [pane("prt1", f"Pa_Word_{k:02d}", t=(491, -40 * k), size=(1020, 70)) for k in range(4)]
    root = pane("pan1", "RootPane", size=(1280, 720), children=[
        pane("pan1", "N_Cap", size=(1280, 720),
             user={"DynamicCaptureOn": "RGBA8", "CaptureScale": (0.25,), "CaptureBGColor": (0, 0, 0)},
             children=[pane("pan1", "N_CapA", flags=3, alpha=0, children=[
                 pane("pic1", "Cap_A", mat=0, size=(40, 44), s=(15, 15), t=(-300, 0))])]),
        pane("pic1", "Black", mat=8, size=(1280, 720)),
        pane("pan1", "N_InOut", flags=3, alpha=0, children=[
            pane("pic1", "P_Bg", mat=1, size=(1280, 720)),
            pane("pan1", "N_Pics", children=[
                pane("pic1", "Back", mat=2, size=(64, 36), s=(21, 21), t=(40, 0)),
                pane("pic1", "Front", mat=3, size=(32, 16), s=(20, 20), t=(0, -200)),
                pane("pan1", "N_Half", s=(0.5, 0.5), t=(-100, 0), children=[
                    pane("pic1", "Single_A", mat=4, size=(40, 44), s=(30, 30), t=(-410, 0),
                         r=(0, 0, 15 if rotate_a else 0))]),
                pane("pic1", "Single_B", mat=5, size=(48, 32), s=(10, 10), origin=2),
                pane("pic1", "Light", mat=6, size=(1280, 720), alpha=144,
                     user={"DynamicCaptureUseName": "N_Cap", "DynamicCaptureUseIndex": (0,)}),
            ]),
            pane("pan1", "N_All", flags=3, children=[
                pane("pic1", "Panel", mat=9, size=(480, 720), t=(400, 0),
                     colors=[(255, 255, 255, 0), (255, 255, 255, 200), (255, 255, 255, 0), (255, 255, 255, 200)]),
                pane("pic1", "Logo", mat=7, size=(16, 8), s=(10, 10), t=(450, 250)),
                pane("pan1", "N_Words", t=(0, 40), children=words)]),
        ])])
    return flyt([t[0] for t in TEXTURES], mats, root)


# The pattern colour (black, white) per set, as in the game's menu: the single picture of set 2 sits on a dark blue
# pattern; the back set (1) shows a near-white colour carried over from the set before it (3), which is hidden under
# the back picture in the game's own menu.
TINT_1 = ((230, 220, 200), (240, 230, 222))
TINT_2 = ((6, 24, 51), (12, 31, 65))
TINT_3 = ((231, 218, 197), (241, 229, 220))


def fake_loop(slots=True, wrap_b=False) -> bytes:
    """wrap_b: set 3 (Single_B) runs past the loop's end (frames 251-349, mid-hold 300 = frame 0)."""
    panes = {}
    if slots:
        b_alpha = ([(0, 255, 0), (40, 255, 0), (50, 0, 0), (250, 0, 0), (260, 255, 0), (300, 255, 0)] if wrap_b
                   else fade(200, 210, 290, 300))
        panes = {
            "Back": [("FLPA", 0, lin(0, 40, 100, 20), False), ("FLVC", 16, fade(0, 10, 90, 100), False)],
            "Front": [("FLPA", 0, lin(0, 0, 100, 50), False), ("FLVC", 16, fade(0, 15, 85, 100), False)],
            "Single_A": [("FLPA", 0, lin(100, -410, 200, -390), False), ("FLVC", 16, fade(100, 110, 190, 200), False)],
            "Cap_A": [("FLPA", 0, lin(100, -410, 200, -390), False)],
            "N_CapA": [("FLVC", 16, fade(100, 101, 199, 200), False)],
            "Single_B": [("FLPA", 1, lin(200, -20, 300, 20), False), ("FLVC", 16, b_alpha, False)],
        }
    panes["Light"] = [("FLVC", 16, [(0, 144, 0), (300, 144, 0)], False)]
    mats = {"P_Bg": [("FLMC", 0, 4 * k + c, plateau(TINT_1[k][c], TINT_2[k][c], TINT_3[k][c]), False)
                     for k in (0, 1) for c in range(3)]}
    return flan(300, True, panes, mats)


def fake_in() -> bytes:
    return flan(30, False, {"N_InOut": [("FLVC", 16, [(0, 0, 0), (30, 255, 0)], False)],
                            "N_All": [("FLVC", 16, [(0, 0, 0), (30, 255, 0)], False)]})


def fake_textures(seed: int = 1) -> bytes:
    rng = random.Random(seed)
    texs = []
    for name, fmt, w, h, ch in TEXTURES:
        lay = fmt >> 8
        if lay in (0x1A, 0x1C):
            img = gradient(w, h, (lambda x, y: (x * 37 + y * 11) % 256) if lay == 0x1C else None)
            data = mb.encode_bc(img.px, w, h, lay == 0x1C)
        elif lay == 0x1D:
            data = bytes(rng.randrange(256) for _ in range(((w + 3) // 4) * ((h + 3) // 4) * 8))
        elif lay == 0x0B:
            data = bytes(rng.randrange(256) for _ in range(w * h * 4))
        else:
            data = bytes(rng.randrange(256) for _ in range(w * h))
        texs.append(Texture(name, fmt, w, h, [data], ch))
    return nx_bntx.write(TextureSet("fake", texs, "model", {"nx.bntx_name": "__Combined"}))


def fake_archive(**kw) -> bytes:
    files = [("blyt/Fake_00.bflyt", fake_layout(**{k: v for k, v in kw.items() if k == "rotate_a"})),
             ("anim/Fake_00_Loop.bflan", fake_loop(kw.get("slots", True))),
             ("anim/Fake_00_In.bflan", fake_in()), ("anim/Fake_00_Other.bflan", b"made-up animation bytes"),
             ("timg/__Combined.bntx", fake_textures(kw.get("seed", 1)))]
    return wc.sarc_build(files, "<", align={"bntx": 0x1000}, data_align=0x1000)


def fake_pack(**kw) -> bytes:
    return wc.sarc_build([("Layout/Title.sblarc", wc.yaz0_compress(fake_archive(**kw), 0x1000))], "<", data_align=4)


def fake_rstb(value: int) -> bytes:
    crc = zlib.crc32(b"Layout/Title.blarc") & 0xFFFFFFFF
    return wc.yaz0_compress(wc.rstb_build([(crc, value), (0x12345678, 99)], []))


PACK = None
ARCHIVE_SIZE = 0


def setUpModule():
    global PACK, ARCHIVE_SIZE
    PACK = fake_pack()
    ARCHIVE_SIZE = len(fake_archive())


def menu() -> mb.MenuFile:
    return mb.open_menu(PACK)


# ================================================================ PNG

class Png(unittest.TestCase):
    def check(self, data, want):
        pic = mb.read_png(data)
        self.assertEqual((pic.w, pic.h), (len(want[0]), len(want)))
        self.assertEqual(bytes(pic.px), b"".join(bytes(p) for row in want for p in row))

    def test_every_colour_type_depth_and_filter(self):
        w, h = 13, 7
        for ctype, depths in mb.PNG_DEPTHS.items():
            for depth in depths:
                for inter in (False, True):
                    top = (1 << depth) - 1
                    rng = random.Random(ctype * 100 + depth)
                    vals = {(x, y): tuple(rng.randrange(top + 1) for _ in range(4)) for x in range(w) for y in range(h)}
                    plte = trns = None
                    if ctype == 3:
                        n = min(1 << depth, 7)
                        plte = bytes(rng.randrange(256) for _ in range(3 * n))
                        trns = bytes(rng.randrange(256) for _ in range(n - 2))
                        vals = {k: (v[0] % n,) for k, v in vals.items()}

                    def sample(x, y):
                        return vals[(x, y)][:mb.PNG_CHANNELS[ctype]]

                    data = make_png(w, h, ctype, depth, sample, plte=plte, trns=trns, interlace=inter)

                    def to8(v):
                        return v >> 8 if depth == 16 else v * 255 // top

                    want = []
                    for y in range(h):
                        row = []
                        for x in range(w):
                            s = sample(x, y)
                            if ctype == 0:
                                row.append((to8(s[0]),) * 3 + (255,))
                            elif ctype == 2:
                                row.append(tuple(to8(v) for v in s) + (255,))
                            elif ctype == 4:
                                row.append((to8(s[0]),) * 3 + (to8(s[1]),))
                            elif ctype == 6:
                                row.append(tuple(to8(v) for v in s))
                            else:
                                i = s[0]
                                a = trns[i] if i < len(trns) else 255
                                row.append(tuple(plte[3 * i:3 * i + 3]) + (a,))
                        want.append(row)
                    with self.subTest(ctype=ctype, depth=depth, interlace=inter):
                        self.check(data, want)

    def test_colour_key_transparency(self):
        key = struct.pack(">HHH", 10, 20, 30)
        data = make_png(2, 1, 2, 8, lambda x, y: (10, 20, 30) if x == 0 else (1, 2, 3), trns=key)
        self.check(data, [[(10, 20, 30, 0), (1, 2, 3, 255)]])
        data = make_png(2, 1, 0, 16, lambda x, y: (1000,) if x else (65535,), trns=struct.pack(">H", 1000))
        self.check(data, [[(255, 255, 255, 255), (3, 3, 3, 0)]])
        key = struct.pack(">HHH", 1000, 2000, 3000)   # 16-bit colour: the key compares all 16 bits
        data = make_png(3, 1, 2, 16, lambda x, y: ((1000, 2000, 3000), (1000, 2000, 3001), (5, 6, 7))[x], trns=key)
        self.check(data, [[(3, 7, 11, 0), (3, 7, 11, 255), (0, 0, 0, 255)]])

    def test_long_palette(self):
        plte = bytes(range(256)) * 3 + bytes(3 * 44)   # 300 colours (only 256 can be used)
        data = make_png(2, 1, 3, 8, lambda x, y: (x * 255,), plte=plte)
        self.check(data, [[(0, 1, 2, 255), (253, 254, 255, 255)]])

    def test_damaged_files(self):
        good = mb.png_bytes(gradient(8, 8))
        with self.assertRaises(mb.PngError) as cm:
            mb.read_png(good[:-20])
        self.assertEqual(cm.exception.why, "short")
        bad = bytearray(good)
        bad[40] ^= 0xFF
        with self.assertRaises(mb.PngError) as cm:
            mb.read_png(bytes(bad))
        self.assertEqual(cm.exception.why, "crc")
        with self.assertRaises(mb.PngError):
            mb.read_png(b"GIF89a not a png")

    def test_writer_round_trip(self):
        pic = gradient(17, 9, lambda x, y: (x * 15) % 256)
        self.assertEqual(mb.read_png(mb.png_bytes(pic)).px, pic.px)

    @unittest.skipUnless(PILImage, "Pillow isn't installed")
    def test_same_as_pillow(self):
        rng = random.Random(5)
        for mode in ("1", "L", "LA", "P", "RGB", "RGBA", "I;16"):
            w, h = 23, 11
            base = PILImage.frombytes("RGBA", (w, h), bytes(rng.randrange(256) for _ in range(w * h * 4)))
            im = base.convert(mode) if mode != "I;16" else base.convert("L").convert("I;16")
            if mode == "P":
                im.info["transparency"] = 3
            buf = io.BytesIO()
            im.save(buf, "PNG")
            ours = mb.read_png(buf.getvalue())
            theirs = PILImage.open(io.BytesIO(buf.getvalue()))
            if mode == "I;16":
                vals = struct.unpack(f"<{w * h}H", theirs.tobytes())
                want = bytes(v for p in vals for v in ((p >> 8,) * 3 + (255,)))
            else:
                want = theirs.convert("RGBA").tobytes()
            with self.subTest(mode=mode):
                self.assertEqual(bytes(ours.px), want)


class Loading(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="menu-bg-test-"))
        self.addCleanup(lambda: mm.remove_tree(str(self.tmp)))

    def test_messages(self):
        p = write_pic(self.tmp / "shot.png", gradient(320, 180))
        with self.assertRaises(mb.MenuBgError) as cm:
            mb.load_picture(str(p), "full")
        self.assertEqual(str(cm.exception), "shot.png is too small: 320 x 180. Use at least 640 x 360.")
        heic = self.tmp / "photo.heic"
        heic.write_bytes(b"\0\0\0\x18ftypheic made up")
        with self.assertRaises(mb.MenuBgError) as cm:
            mb.load_picture(str(heic))
        self.assertEqual(str(cm.exception), "Can't read photo.heic. This tool reads PNG (and JPG, WEBP, BMP with "
                                            "Pillow). Save it as PNG.")
        big = self.tmp / "huge.png"
        big.write_bytes(mb.PNG_SIG + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 20000, 9000, 8, 6, 0, 0, 0)
                        + b"\0\0\0\0")
        with self.assertRaises(mb.MenuBgError) as cm:
            mb.load_picture(str(big))
        self.assertEqual(str(cm.exception), "huge.png is too big: 20000 x 9000. Use at most 8192 pixels a side.")
        cut = self.tmp / "cut.png"
        cut.write_bytes(mb.png_bytes(gradient(400, 400))[:-40])
        with mock.patch.dict(sys.modules, {"PIL": None}), self.assertRaises(mb.MenuBgError) as cm:
            mb.load_picture(str(cut))
        self.assertEqual(str(cm.exception), "cut.png is damaged (it ends too early). Save it again.")

    def test_without_pillow(self):
        p = write_pic(self.tmp / "pic.png", gradient(400, 380))
        jpg = self.tmp / "shot.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xe0 made-up jpeg")
        with mock.patch.dict(sys.modules, {"PIL": None}):
            self.assertIsNone(mb.pillow())
            pic = mb.load_picture(str(p))
            self.assertEqual((pic.w, pic.h), (400, 380))
            with self.assertRaises(mb.MenuBgError) as cm:
                mb.load_picture(str(jpg))
        self.assertEqual(str(cm.exception), "Can't read shot.jpg: JPG needs Pillow, and this PC doesn't have it. Save "
                                            "it as PNG, or run: py -m pip install pillow")

    def test_cutout_threshold(self):
        n = 100 * 100
        few = gradient(100, 100, lambda x, y: 0 if x + 100 * y < n // 100 else 255)          # exactly 1 %
        more = gradient(100, 100, lambda x, y: 249 if x + 100 * y <= n // 100 else 255)      # just over 1 %
        self.assertFalse(mb.is_cutout(few))
        self.assertTrue(mb.is_cutout(more))
        self.assertFalse(mb.is_cutout(gradient(10, 10, lambda x, y: 250)))

    def test_a_few_clear_pixels_become_black(self):
        corner = gradient(400, 400, lambda x, y: 0 if x + y < 20 else 255)   # rounded corner: 0.13 % clear
        p = write_pic(self.tmp / "shot.png", corner)
        for pil in (False, True):
            if pil and not PILImage:
                continue
            ctx = contextlib.nullcontext() if pil else mock.patch.dict(sys.modules, {"PIL": None})
            with ctx:
                pic = mb.load_picture(str(p))
            self.assertEqual((pic.cutout, pic.cleared), (False, True))
            self.assertEqual(set(pic.px[3::4]), {255})
            self.assertEqual(bytes(pic.px[19 * 4:20 * 4]), b"\0\0\0\xff")       # the clear corner: black
            self.assertEqual(pic.px[(200 * 400 + 200) * 4:(200 * 400 + 200) * 4 + 4],
                             corner.px[(200 * 400 + 200) * 4:(200 * 400 + 200) * 4 + 4])
        cut = write_pic(self.tmp / "cut.png", gradient(400, 400, lambda x, y: 0 if x < 100 else 255))
        pic = mb.load_picture(str(cut))
        self.assertEqual((pic.cutout, pic.cleared), (True, False))

    @unittest.skipUnless(PILImage, "Pillow isn't installed")
    def test_any_kind_can_be_a_cutout(self):
        im = PILImage.new("RGBA", (400, 400), (10, 200, 30, 255))
        im.paste((0, 0, 0, 0), (0, 0, 100, 400))
        buf = io.BytesIO()
        im.save(buf, "WEBP", lossless=True)
        (self.tmp / "cut.webp").write_bytes(buf.getvalue())
        self.assertTrue(mb.load_picture(str(self.tmp / "cut.webp")).cutout)

    def test_too_much_memory_is_one_line(self):
        p = write_pic(self.tmp / "pic.png", gradient(400, 380))
        with mock.patch.object(mb, "is_cutout", side_effect=MemoryError), self.assertRaises(mb.MenuBgError) as cm:
            mb.load_picture(str(p))
        self.assertEqual(str(cm.exception), "pic.png is too big for this PC's memory. Save it smaller (for example "
                                            "1920 x 1080).")


# ================================================================ BC codec

def dds(data: bytes, w: int, h: int, fourcc: bytes) -> bytes:
    head = struct.pack("<7I", 124, 0x1007 | 0x80000, h, w, len(data), 0, 1) + bytes(44)
    pf = struct.pack("<II4s5I", 32, 4, fourcc, 0, 0, 0, 0, 0)
    return b"DDS " + head + pf + struct.pack("<5I", 0x1000, 0, 0, 0, 0) + data


def random_blocks(n: int, bpb: int, seed: int) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(n * bpb))


class Codec(unittest.TestCase):
    @unittest.skipUnless(PILImage, "Pillow isn't installed")
    def test_decode_same_as_pillow(self):
        w, h = 16, 12
        for fourcc, bc3 in ((b"DXT1", False), (b"DXT5", True)):
            data = random_blocks(12, 16 if bc3 else 8, 7)
            ours = mb.decode_bc1(data, w, h, bc3)
            theirs = PILImage.open(io.BytesIO(dds(data, w, h, fourcc))).convert("RGBA").tobytes()
            with self.subTest(fourcc=fourcc):
                self.assertEqual(bytes(ours), theirs)

    def test_quality(self):
        w, h = 64, 64
        grad = gradient(w, h)
        rng = random.Random(2)
        noise = mb.Picture(w, h, bytearray(v for x in range(w * h) for v in (
            min(255, max(0, 128 + rng.randint(-24, 24))), min(255, max(0, (x % w) * 3 + rng.randint(-24, 24))),
            min(255, max(0, 90 + rng.randint(-24, 24))), 255)))
        for bc3 in (False, True):
            for pic, floor in ((grad, 36), (noise, 22)):
                dec = mb.decode_bc1(mb.encode_bc(pic.px, w, h, bc3), w, h, bc3)
                with self.subTest(bc3=bc3, floor=floor):
                    self.assertGreaterEqual(mb.psnr(dec, pic.px, (0, 1, 2)), floor)

    def test_blocks_from_bc_data_are_exact(self):
        rng = random.Random(3)
        data = bytearray()
        for _ in range(64):   # 4-colour blocks (c0 > c1), as an opaque texture has them
            c0 = rng.randrange(1, 65536)
            data += struct.pack("<HHI", c0, rng.randrange(c0), rng.randrange(1 << 32))
        w, h = 32, 32
        once = mb.decode_bc1(bytes(data), w, h)
        twice = mb.decode_bc1(mb.encode_bc(once, w, h, False), w, h)
        self.assertEqual(once, twice)
        for v in (0, 1, 77, 128, 200, 255):   # one-colour blocks within one step of the colour
            px = bytes((v, 255 - v, v // 2, 255)) * 16
            got = mb.decode_bc1(mb.encode_bc(px, 4, 4, False), 4, 4)
            self.assertLessEqual(max(abs(a - b) for a, b in zip(got, px)), 2)

    def test_bc1_never_uses_the_see_through_mode(self):
        rng = random.Random(9)
        w, h = 64, 64
        pics = [gradient(w, h), mb.Picture(w, h, bytearray(rng.randrange(256) if i % 4 != 3 else 255
                                                           for i in range(w * h * 4))),
                mb.Picture(w, h, bytearray(bytes((rng.randrange(256),) * 3 + (255,)) * (w * h)))]
        for pic in pics:
            enc = mb.encode_bc(pic.px, w, h, False)
            for k in range(0, len(enc), 8):
                c0, c1, bits = struct.unpack_from("<HHI", enc, k)
                self.assertTrue(c0 > c1 or (c0 == c1 and bits == 0), f"block {k // 8}: {c0:#x} {c1:#x} {bits:#x}")

    def test_clear_areas_are_the_clear_block(self):
        enc = mb.encode_bc(bytes(8 * 8 * 4), 8, 8, True)
        self.assertEqual(enc, mb.CLEAR_BC3 * 4)
        self.assertEqual(mb.decode_bc3(mb.CLEAR_BC3, 4, 4), bytes(64))

    def test_bc4_r8_rgba8_decode(self):
        block = bytes((200, 100)) + (0b001 | 0b010 << 3).to_bytes(6, "little")   # texel 0: index 1, texel 1: 2
        vals = mb.decode_bc4(block, 4, 4)
        self.assertEqual((vals[0], vals[1], vals[2]), (100, (6 * 200 + 100) // 7, 200))
        pic = mb.decode_texture(0x0201, 2, 1, bytes((7, 9)), (1, 1, 1, 2))
        self.assertEqual(bytes(pic.px), bytes((255, 255, 255, 7, 255, 255, 255, 9)))
        pic = mb.decode_texture(0x0B06, 1, 1, bytes((1, 2, 3, 4)))
        self.assertEqual(bytes(pic.px), bytes((1, 2, 3, 4)))


# ================================================================ image operations

class ImageOps(unittest.TestCase):
    def test_resize(self):
        const = mb.Picture(50, 30, bytearray(bytes((10, 20, 30, 255)) * 1500))
        for pil in (False, True):
            ctx = mock.patch.dict(sys.modules, {"PIL": None}) if not pil else contextlib.nullcontext()
            if pil and not PILImage:
                continue
            with ctx:
                for w, h in ((17, 9), (120, 70), (50, 30)):
                    out = mb.resample(const, (0, 0, 50, 30), w, h)
                    self.assertEqual(out.px, bytes((10, 20, 30, 255)) * (w * h))
        rng = random.Random(4)
        pic = mb.Picture(8, 6, bytearray(rng.randrange(256) for _ in range(8 * 6 * 4)))
        with mock.patch.dict(sys.modules, {"PIL": None}):
            half = mb.resample(pic, (0, 0, 8, 6), 4, 3)
        for y in range(3):
            for x in range(4):
                for c in range(4):
                    vals = [pic.px[((2 * y + dy) * 8 + 2 * x + dx) * 4 + c] for dy in (0, 1) for dx in (0, 1)]
                    self.assertLessEqual(abs(half.px[(y * 4 + x) * 4 + c] - sum(vals) / 4), 1)

    def test_fit(self):
        pic = mb.Picture(200, 100, bytearray(200 * 100 * 4))
        p = mb.fit(pic, (0, 0, 100, 100), False, "center")      # cover: crop the middle 100 x 100
        self.assertEqual(mb.source_box((0, 0, 100, 100), p), (50.0, 0.0, 150.0, 100.0))
        p = mb.fit(pic, (0, 0, 100, 100), False, "left")
        self.assertEqual(mb.source_box((0, 0, 100, 100), p), (0.0, 0.0, 100.0, 100.0))
        p = mb.fit(pic, (0, 0, 100, 100), False, "right")
        self.assertEqual(mb.source_box((0, 0, 100, 100), p), (100.0, 0.0, 200.0, 100.0))
        p = mb.fit(pic, (10, 10, 110, 110), True, "center")     # contain: whole, on the bottom edge
        self.assertEqual((p.s, p.ox, p.oy), (0.5, 10.0, 60.0))
        tall = mb.Picture(100, 300, bytearray(100 * 300 * 4))   # portrait into a wide box: 100 x 50 of it shows
        p = mb.fit(tall, (0, 0, 100, 50), False, "center")
        box = mb.source_box((0, 0, 100, 50), p)
        self.assertAlmostEqual((box[1] + box[3]) / 2, 100.0)   # the upper third (the head) in the middle
        p = mb.fit(tall, (0, 0, 100, 50), False, "top")
        self.assertEqual(mb.source_box((0, 0, 100, 50), p), (0.0, 0.0, 100.0, 50.0))

    def test_feather(self):
        img = mb.Picture(100, 10, bytearray(b"\x80\x80\x80\xff" * 1000))
        rect = (0, 0, 100, 10)
        mb.feather(img, rect, rect, {"left": False, "right": True, "top": False, "bottom": False}, rect)
        a = list(img.px[3::4][:100])
        self.assertEqual(a[:75], [255] * 75)                  # inside: untouched
        self.assertLessEqual(a[-1], 2)                        # at the on-screen edge: (nearly) clear
        self.assertEqual(a, sorted(a, reverse=True))          # monotonic
        img2 = mb.Picture(100, 10, bytearray(b"\x80\x80\x80\xff" * 1000))
        sides = mb.edges_on_screen((-5, 0, 100, 10), (0, 0, 0, 0), 100, 10)
        self.assertEqual(sides, {"left": False, "right": False, "top": False, "bottom": False})
        mb.feather(img2, rect, (-5, 0, 100, 10), sides, rect)
        self.assertEqual(img2.px, bytearray(b"\x80\x80\x80\xff" * 1000))   # off-screen edges: nothing
        self.assertTrue(mb.edges_on_screen((-5, 0, 100, 10), (0, 10, 0, 0), 100, 10)["left"])  # pans in: soft

    def test_push_pull(self):
        img = mb.Picture(8, 8, bytearray(8 * 8 * 4))
        for y in range(8):
            for x in range(4):
                img.px[(y * 8 + x) * 4:(y * 8 + x) * 4 + 4] = bytes((200, 10, 20, 255 if x < 3 else 100))
        before = bytes(img.px)
        mb.push_pull(img)
        for y in range(8):
            for x in range(8):
                o = (y * 8 + x) * 4
                if x < 4:
                    self.assertEqual(img.px[o:o + 4], before[o:o + 4])       # visible: kept
                else:
                    self.assertEqual(img.px[o + 3], 0)                        # alpha kept
                    self.assertEqual(tuple(img.px[o:o + 3]), (200, 10, 20))   # colour from the visible ones

    def test_gain_and_composite(self):
        img = mb.Picture(2, 1, bytearray((200, 100, 50, 77, 255, 255, 255, 255)))
        mb.apply_gain(img, 0.5)
        self.assertEqual(img.px[3], 77)
        self.assertLess(img.px[0], 200)
        top = mb.Picture(3, 1, bytearray((255, 0, 0, 255, 255, 0, 0, 0, 255, 0, 0, 128)))
        base = mb.Picture(3, 1, bytearray((0, 0, 255, 255) * 3))
        out = mb.composite(top, base)
        self.assertEqual(bytes(out.px[:8]), bytes((255, 0, 0, 255, 0, 0, 255, 255)))
        self.assertGreater(out.px[8], 150)                    # half red over blue, in linear light
        self.assertGreater(out.px[10], 150)


# ================================================================ layout readers and the slideshow model

class Model(unittest.TestCase):
    def test_hermite_and_step(self):
        c = mb.Curve(False, [(0, 0, 0), (10, 10, 0)])
        self.assertEqual(c.at(-5), 0)
        self.assertEqual(c.at(5), 5)
        self.assertAlmostEqual(c.at(2.5), 10 * (3 * 0.25 ** 2 - 2 * 0.25 ** 3))
        self.assertEqual(c.at(20), 10)
        lin_c = mb.Curve(False, lin(0, 0, 100, 50))
        self.assertAlmostEqual(lin_c.at(30), 15)
        jump = mb.Curve(False, [(0, 0, 0), (5, 0, 255), (6, 255, 255), (6, 255, 0)])
        self.assertEqual(jump.at(6), 255)
        self.assertEqual(mb.Curve(True, [(0, 1, 0), (10, 0, 0)]).at(9.9), 1)

    def test_the_made_up_menu(self):
        m = menu().model
        self.assertEqual([st.number for st in m.sets], [1, 2, 3])
        s1, s2, s3 = m.sets
        self.assertEqual([(s.texture, s.role) for s in s1.slots], [("Pic_Back^o", "back"), ("Pic_Front^q", "front")])
        self.assertEqual([(s.texture, s.role) for s in s2.slots], [("Pic_A^q", "single")])
        self.assertEqual([(s.texture, s.role) for s in s3.slots], [("Pic_B^q", "single")])
        self.assertEqual((s1.window, s1.hold, s1.mid), ((1, 99), (15, 85), 50.0))
        self.assertEqual((s2.hold, s2.mid, s3.mid), ((110, 190), 150.0, 250.0))
        back, front = s1.slots

        def close(a, b):
            for x, y in zip(a, b):
                self.assertAlmostEqual(x, y, places=3)

        close(back.rect, (-2, -18, 1342, 738))
        close(back.pan, (-9.8, 9.8, 0, 0))
        close(front.rect, (345, 400, 985, 720))
        close(s2.slots[0].rect, (40, 30, 640, 690))           # half-scale parent: position and size halved
        close(s3.slots[0].rect, (160, 200, 640, 520))         # right-anchored
        close(s3.slots[0].pan, (0, 0, -19.6, 19.6))
        self.assertEqual(s2.slots[0].copies, ["Cap_A"])
        self.assertAlmostEqual(s2.slots[0].glow, 128 / 255, places=3)
        self.assertEqual(back.glow, 0.0)
        for st, tint in ((s1, TINT_1), (s2, TINT_2), (s3, TINT_3)):
            close(st.tint[0], [v / 255 for v in tint[0]])
            close(st.tint[1], [v / 255 for v in tint[1]])
        self.assertEqual((m.pattern.pane, m.pattern.texture, m.pattern.scale, m.pattern.center),
                         ("P_Bg", "Fake_Pattern^r", (2.0, 2.0), (640.0, 360.0)))
        close(m.art_box(), (40, 30, 640, 690))
        self.assertEqual(m.art_tint(), s2.tint)             # (where the art box comes from: not set 1's near white)
        # the words: 4 parts at x 491 (from the centre), y 40, 0, -40, -80; bars 90 x 14 ending 85 right of them
        close(m.words, (1126 - mb.WORDS_PAD, 313 - mb.WORDS_PAD, 1216 + mb.WORDS_PAD, 447 + mb.WORDS_PAD))

    def test_a_set_past_the_loop_end(self):
        lay = mb.read_flyt(fake_layout())
        info = {n: (f, w, h, 1) for n, f, w, h, _ in TEXTURES}
        m = mb.build_model(lay, {"loop": mb.read_flan(fake_loop(wrap_b=True))}, info)
        s3 = m.sets[2]
        self.assertEqual((s3.window, s3.hold, s3.mid), ((251, 349), (260, 340), 300.0))
        for got, want in zip(s3.tint, TINT_1):              # the colour at frame 0, not the last key's
            for x, y in zip(got, want):
                self.assertAlmostEqual(x, y / 255, places=3)

    def test_refused_layouts(self):
        for kw, why in (({"rotate_a": True}, "is rotated"), ({"slots": False}, "no ")):
            with self.subTest(why=why), self.assertRaises(mb.MenuBgError) as cm:
                mb.open_menu(fake_pack(**kw), "x", "Some Mod")
            self.assertIn("The menu file (Pack/Title.pack) isn't one this tool knows. It comes from Some Mod.",
                          str(cm.exception))
            self.assertIn(why, str(cm.exception))
        with self.assertRaises(mb.MenuBgError):
            mb.open_menu(b"SARC but not really", "game", "the game")
        with self.assertRaises(mb.LayoutError):
            mb.read_flyt(b"FLYT\xff\xfe\x14\x00\x00\x00\x00\x07" + bytes(20))


# ================================================================ build

class Build(unittest.TestCase):
    def test_both_modes(self):
        m = menu()
        pic = gradient(640, 400)
        plane = m.pattern_plane()
        for mode in ("art", "full"):
            images = mb.make_slots(m.model, [pic], mode, {1, 2, 3}, "center", plane)
            self.assertEqual(set(images), set(SLOT_NAMES))
            self.assertIsNone(images["Pic_Front^q"])
            for name in ("Pic_Back^o", "Pic_A^q", "Pic_B^q"):
                img = images[name]
                slot = next(s for s in m.model.slots() if s.texture == name)
                self.assertEqual((img.w, img.h), (slot.w, slot.h))
            self.assertEqual(set(images["Pic_Back^o"].px[3::4]), {255})
            levels = mb.encode_slots(m.model, images)
            pack, size = mb.build_pack(m, levels)
            self.assertEqual(size, ARCHIVE_SIZE)
            q = mb.self_check(m, pack, levels, images)
            self.assertGreater(q, 30)
            new = mb.open_menu(pack)
            old = dict(m.arc_files)
            for n, d in new.arc_files:
                if not n.endswith(".bntx"):
                    self.assertEqual(d, old[n], n)
            ot = {t.name: t for t in m.textures.textures}
            nt = new.textures
            self.assertEqual([t.name for t in nt.textures], [t.name for t in m.textures.textures])
            self.assertEqual(nt.extra, m.textures.extra)
            for t in nt.textures:
                o = ot[t.name]
                self.assertEqual((t.format, t.width, t.height, t.channels), (o.format, o.width, o.height, o.channels))
                if t.name in OTHER_NAMES:
                    self.assertEqual(t.levels, o.levels, t.name)
            for name in ("Pic_Back^o", "Pic_A^q", "Pic_B^q"):
                t = new.texture(name)
                got = mb.decode_texture(t.format, t.width, t.height, t.levels[0])
                chans = (0, 1, 2) if t.format == mb.BC1_SRGB else (0, 1, 2, 3)
                self.assertGreater(mb.psnr(got.px, images[name].px, chans), 30, (mode, name))

    def test_one_set_changes_one_texture(self):
        m = menu()
        images = mb.make_slots(m.model, [gradient(640, 400)], "art", {2}, "center", m.pattern_plane())
        self.assertEqual(set(images), {"Pic_A^q"})
        new = mb.open_menu(mb.build_pack(m, mb.encode_slots(m.model, images))[0])
        ot = {t.name: t for t in m.textures.textures}
        changed = [t.name for t in new.textures.textures if t.levels != ot[t.name].levels]
        self.assertEqual(changed, ["Pic_A^q"])

    def test_archive_must_keep_its_size(self):
        m = menu()
        levels = mb.encode_slots(m.model, mb.make_slots(m.model, [gradient(640, 400)], "art", {2}, "center", None))
        real = wc.sarc_build
        with mock.patch.object(mb.wc, "sarc_build", side_effect=lambda *a, **k: real(*a, **k) + b"\0" * 16), \
                self.assertRaises(mb.MenuBgError) as cm:
            mb.build_pack(m, levels)
        self.assertEqual(str(cm.exception), "The new menu file came out a different size. Nothing was changed.")

    def test_art_mode_looks(self):
        m = menu()
        pic = gradient(640, 400)
        images = mb.make_slots(m.model, [pic], "art", {1, 2, 3}, "center", m.pattern_plane())
        a = images["Pic_A^q"]                 # x 40..640 on screen: left and right edges soft, top/bottom too
        alpha = a.px[3::4]
        self.assertEqual(alpha[(a.h // 2) * a.w + a.w // 2], 255)
        self.assertLess(alpha[(a.h // 2) * a.w + a.w - 1], 40)
        back = images["Pic_Back^o"]           # outside the art box (x 40..640): the pattern in set 2's blue
        black, white = TINT_2
        x = int((900 + 2) / 1344 * 64)
        o = ((back.h // 2) * back.w + x) * 4
        for c in range(3):
            lut = [mb.lin_to_srgb((black[c] + (white[c] - black[c]) * v / 255) / 255) for v in (0, 255)]
            self.assertTrue(min(lut) - 3 <= back.px[o + c] <= max(lut) + 3, (c, back.px[o:o + 4]))
        light, cols = mb.words_light(back, m.model.sets[0].slots[0].rect, m.model.sets[0].slots[0].pan, m.model.words)
        self.assertIsNotNone(cols)
        self.assertLessEqual(light, mb.WORDS_LIGHT)   # under the words: dark (set 1's own colour is near white)

    def test_words_stay_readable_on_a_white_picture(self):
        m = menu()
        white = mb.Picture(640, 400, bytearray(b"\xff" * (640 * 400 * 4)))
        slot = m.model.sets[0].slots[0]
        for mode in ("full", "art"):
            images = mb.make_slots(m.model, [white], mode, {1}, "center", m.pattern_plane())
            back = images["Pic_Back^o"]
            light, cols = mb.words_light(back, slot.rect, slot.pan, m.model.words)
            self.assertLessEqual(light, mb.WORDS_LIGHT, mode)
            if mode == "full":                # the left of the screen keeps the picture's light
                x = int((200 + 2) / 1344 * 64)
                self.assertGreater(back.px[((back.h // 2) * back.w + x) * 4], 230)
        img = mb.Picture(64, 36, bytearray(b"\x10\x10\x10\xff" * (64 * 36)))   # already dark: left alone
        before = bytes(img.px)
        self.assertEqual(mb.guard_words(img, slot.rect, slot.pan, m.model.words, 1280), 1.0)
        self.assertEqual(bytes(img.px), before)

    def test_pictures_go_in_the_order_of_the_chosen_sets(self):
        m = menu()
        red = mb.Picture(400, 400, bytearray(b"\xff\x00\x00\xff" * (400 * 400)))
        blue = mb.Picture(400, 400, bytearray(b"\x00\x00\xff\xff" * (400 * 400)))
        images = mb.make_slots(m.model, [red, blue], "art", {2, 3}, "center", None)
        for name, want in (("Pic_A^q", 0), ("Pic_B^q", 2)):
            img = images[name]
            o = ((img.h // 2) * img.w + img.w // 2) * 4
            self.assertEqual(max(range(3), key=lambda c: img.px[o + c]), want, name)

    def test_cutout(self):
        m = menu()
        cut = gradient(400, 400, lambda x, y: 255 if (x - 200) ** 2 + (y - 200) ** 2 < 150 ** 2 else 0)
        cut.cutout = mb.is_cutout(cut)
        self.assertTrue(cut.cutout)
        images = mb.make_slots(m.model, [cut], "art", {2}, "center", None)
        a = images["Pic_A^q"]
        self.assertEqual(a.px[3], 0)                                   # corner: clear
        self.assertEqual(a.px[((a.h * 3 // 4) * a.w + a.w // 2) * 4 + 3], 255)   # inside the circle: solid
        self.assertNotEqual(tuple(a.px[0:3]), (0, 0, 0))               # colour under clear parts pulled in


# ================================================================ library, guards, deploy, preview, CLI

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="menu-bg-test-"))
        self.addCleanup(lambda: mm.remove_tree(str(self.tmp)))
        self.install = tm.fake_install(self.tmp / "SavageWind" / "Savage Wind")
        self.romfs = self.install / "extracted" / "romfs"
        tm.write(self.romfs, {"Pack/Title.pack": PACK,
                              "System/Resource/ResourceSizeTable.product.srsizetable": fake_rstb(ARCHIVE_SIZE + 4096)})
        self.libdir = self.tmp / "SavageWind" / "modloader"
        mm.Library(str(self.libdir), str(self.install)).save()
        self.deploys = []
        self.rebuild_plan = "none"
        patches = [mock.patch.object(mm, "game_running", return_value=False),
                   mock.patch.object(mm, "deploy", side_effect=self.fake_deploy),
                   mock.patch.object(mb, "lower_priority"),
                   mock.patch.dict(os.environ, {"USERPROFILE": str(self.tmp / "home")})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop(mm.ENV_LIBRARY, None)
        self.pic = write_pic(self.tmp / "pics" / "linkle.png", gradient(640, 400))

    def fake_deploy(self, lib, *, no_rebuild=False, dry_run=False):
        self.deploys.append(dry_run)
        parts = {"exefs": {"rebuild": self.rebuild_plan}} if self.rebuild_plan != "none" else {}
        return {"parts": parts, "attention": [], "mods": [{"id": m["id"]} for m in lib.enabled()]}

    @property
    def lib(self):
        return mm.Library.load(str(self.libdir))

    def set(self, *extra, code=0):
        rc, out = run(["set", str(self.pic), "--library", str(self.libdir), *extra])
        self.assertEqual(rc, code, out)
        return out

    def config(self) -> bytes:
        return (self.libdir / "config.json").read_bytes()


class Library(Base):
    def test_set_adds_the_mod(self):
        out = self.set()
        self.assertTrue(out.splitlines()[-3].startswith("Done: your picture is on the main menu (all 3 slideshow"),
                        out)
        self.assertIn("Deployed. Start the game to see it: python tools/modman.py run", out)
        lib = self.lib
        self.assertEqual([m["id"] for m in lib.mods], [mb.MOD_ID])
        entry = lib.mods[0]
        self.assertTrue(entry["enabled"])
        self.assertEqual(entry["kinds"], ["romfs"])
        self.assertNotIn("problems", entry)
        self.assertEqual(entry["name"], "Menu picture: linkle.png")
        folder = self.libdir / "mods" / mb.MOD_ID
        man, errs = mm.parse_manifest((folder / "mod.toml").read_text(encoding="utf-8"))
        self.assertEqual(errs, [])
        self.assertEqual((man.id, man.authors, man.game_versions, man.romfs), (mb.MOD_ID, ["giruka"], ["1.9.0"],
                                                                               "romfs"))
        info = json.loads((folder / mb.INFO_FILE).read_text(encoding="utf-8"))
        self.assertEqual((info["mode"], info["sets"], info["pictures"][0]["name"], info["base"]["source"]),
                         ("art", [1, 2, 3], "linkle.png", "game"))
        files = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
        self.assertEqual(files, ["menu-picture.json", "mod.toml", "romfs/Pack/Title.pack"])   # (no size table)
        scan = mm.scan_mod(str(folder), mm.game_info(str(self.install)), "x")
        self.assertEqual((scan.kinds, scan.problems), (["romfs"], []))
        self.assertEqual(self.deploys, [True, False])

    def test_set_again_keeps_its_place_and_reset_removes_it(self):
        self.set("--no-deploy")
        other = tm.write(self.tmp / "dl" / "Other", {"romfs/Actor/Pack/Thing.sbactorpack": b"made-up"})
        quiet(mm.add_mod, self.lib, str(other))
        lib = self.lib
        lib.mods.insert(0, lib.mods.pop())   # the other mod first, ours second
        lib.save()
        self.set("--no-deploy", "--mode", "full", "--name", 'My "menu"')
        self.assertEqual([m["id"] for m in self.lib.mods], ["other", mb.MOD_ID])
        self.assertEqual(self.lib.mods[1]["name"], 'My "menu"')
        rc, out = run(["reset", "--library", str(self.libdir)])
        self.assertEqual(rc, 0)
        self.assertIn("Done: the menu picture is gone from your library.", out)
        self.assertEqual([m["id"] for m in self.lib.mods], ["other"])
        self.assertFalse((self.libdir / "mods" / mb.MOD_ID).exists())
        rc, out = run(["reset", "--library", str(self.libdir)])
        self.assertEqual(out.strip(), "No menu picture in the library. Nothing to do.")

    def test_another_menu_mod_is_the_base(self):
        theirs = fake_pack(seed=5)
        src = tm.write(self.tmp / "dl" / "Their Menu", {"romfs/Pack/Title.pack": theirs})
        quiet(mm.add_mod, self.lib, str(src))
        self.set("--no-deploy")
        lib = self.lib
        self.assertEqual([m["id"] for m in lib.mods], ["their-menu", mb.MOD_ID])
        lib.mods.reverse()   # ours first: set again puts it after the other menu mod
        lib.save()
        out = self.set("--no-deploy")
        self.assertIn("Building on the menu file of Their Menu.", out)
        self.assertEqual([m["id"] for m in self.lib.mods], ["their-menu", mb.MOD_ID])
        info = json.loads((self.libdir / "mods" / mb.MOD_ID / mb.INFO_FILE).read_text(encoding="utf-8"))
        self.assertEqual(info["base"]["source"], "their-menu")
        ours = mb.open_menu((self.libdir / "mods" / mb.MOD_ID / "romfs" / "Pack" / "Title.pack").read_bytes())
        base = mb.open_menu(theirs)
        self.assertEqual(ours.texture("Logo^l").levels, base.texture("Logo^l").levels)

    def test_library_from_variable_and_default_place(self):
        env_lib = self.tmp / "env-lib"
        mm.Library(str(env_lib), str(self.install)).save()
        with mock.patch.dict(os.environ, {mm.ENV_LIBRARY: str(env_lib)}):
            rc, out = run(["set", str(self.pic), "--no-deploy"])
        self.assertEqual(rc, 0, out)
        self.assertEqual([m["id"] for m in mm.Library.load(str(env_lib)).mods], [mb.MOD_ID])
        home_install = tm.fake_install(self.tmp / "home" / "Documents" / "SavageWind" / "Savage Wind")
        tm.write(home_install / "extracted" / "romfs", {"Pack/Title.pack": PACK})
        rc, out = run(["set", str(self.pic), "--no-deploy"])
        self.assertEqual(rc, 0, out)
        lib = mm.Library.load(str(self.tmp / "home" / "Documents" / "SavageWind" / "modloader"))
        self.assertEqual((lib.install, [m["id"] for m in lib.mods]), (str(home_install), [mb.MOD_ID]))
        self.assertIn("Made a mod library in", out)

    def test_status(self):
        rc, out = run(["status", "--library", str(self.libdir)])
        self.assertEqual(out.strip(), "No menu picture in the library.")
        self.set("--no-deploy", "--sets", "2")
        rc, out = run(["status", "--library", str(self.libdir)])
        self.assertIn("Menu picture: on, mode art, slideshow picture 2, picture: linkle.png.", out)
        self.assertIn("Not deployed yet", out)
        self.assertNotIn("Run set again", out)
        info_path = self.libdir / "mods" / mb.MOD_ID / mb.INFO_FILE
        info = json.loads(info_path.read_text(encoding="utf-8"))
        info["version"] = "1.0.0"
        info_path.write_text(json.dumps(info), encoding="utf-8")
        rc, out = run(["status", "--library", str(self.libdir)])
        self.assertIn("It was made with an older version of this tool. Run set again to get the fixed menu.", out)
        theirs = tm.write(self.tmp / "dl" / "Their Menu", {"romfs/Pack/Title.pack": fake_pack(seed=5)})
        quiet(mm.add_mod, self.lib, str(theirs))
        rc, out = run(["status", "--library", str(self.libdir)])
        self.assertIn("It was made over the menu file of the game, but now Their Menu's is used. Run set again.", out)

    def test_a_chosen_title_picture_wins(self):
        chosen = self.libdir / "pictures" / "_title" / "chosen.png"
        write_pic(chosen, gradient(400, 400))
        out = self.set()
        lines = out.splitlines()
        self.assertIn("Done: the menu picture is in your library (all 3 slideshow pictures).", lines)
        self.assertIn("Your title picture chosen.png is shown instead. Remove it in the Mods page to see this one.",
                      lines)
        self.assertNotIn("Done: your picture is on the main menu", out)
        rc, out = run(["status", "--library", str(self.libdir)])
        self.assertIn("Your title picture chosen.png is shown instead.", out)
        rc, out = run(["reset", "--library", str(self.libdir)])
        self.assertIn("Your title picture chosen.png stays on the menu. Remove it in the Mods page to get the game's "
                      "own menu back.", out)
        self.assertNotIn("The game's own menu is back", out)
        chosen.unlink()
        toml = '[mod]\nid = "test.pics"\nname = "Pics"\nversion = "1.0"\ntitle_picture = "title.png"\n' \
               '[data]\nromfs = "romfs"\n'
        mod = tm.write(self.tmp / "dl" / "Pics", {"mod.toml": toml, "title.png": mb.png_bytes(gradient(400, 400)),
                                                  "romfs/Actor/Pack/Thing.sbactorpack": b"made-up"})
        quiet(mm.add_mod, self.lib, str(mod))
        self.assertEqual(mb.title_choice(self.lib), ("title.png", "Pics"))

    def test_only_menu_picture_deploy_notes(self):
        notes = ["the-other-mod ships an RSTB, so start Savage Wind with SOMETHING=1 (made up)",
                 "The RSTB comes from the-other-mod only; files from botw-mod-loader.menu-picture may need entries",
                 "Title: the title picture x.png isn't used this time: made up"]
        orig = self.fake_deploy

        def deploy(lib, **kw):
            r = orig(lib, **kw)
            r["attention"] = list(notes)
            return r

        with mock.patch.object(mm, "deploy", side_effect=deploy):
            out = self.set()
        self.assertIn("Note: Title: the title picture x.png isn't used this time: made up", out)
        self.assertNotIn("RSTB", out)


class Guards(Base):
    def refused(self, *extra, msg: str, pic=None):
        before = self.config()
        rc, out = run(["set", str(pic or self.pic), "--library", str(self.libdir), *extra])
        self.assertEqual(rc, 2, out)
        self.assertIn(msg, out)
        self.assertEqual(self.config(), before)
        self.assertFalse((self.libdir / "mods" / mb.MOD_ID).exists())
        return out

    def test_wrong_build(self):
        (self.install / "extracted" / "exefs" / "main").write_bytes(
            make_nso(bytes.fromhex("de0b6ac4efc1dda7") + bytes(24), tm.SEGS))
        self.refused(msg="This tool works with BotW 1.9.0 only. Your game is 1.8.2 (build de0b6ac4efc1dda7).")
        (self.install / "extracted" / "exefs" / "main").write_bytes(make_nso(bytes(32), tm.SEGS))
        self.refused(msg="Your game is a version this tool does not know (build 0000000000000000).")

    def test_not_set_up_and_missing_pack(self):
        (self.romfs / "Pack" / "Title.pack").unlink()
        self.refused(msg="The menu file (Pack/Title.pack) is missing from the game.")
        mm.remove_tree(str(self.install / "extracted" / "exefs"))
        self.refused(msg="The game files aren't set up. Start Savage Wind and press Set up game files.")

    def test_unknown_menu_and_dlc(self):
        (self.romfs / "Pack" / "Title.pack").write_bytes(wc.sarc_build([("Layout/Title.sblarc", b"made up")], "<"))
        self.refused(msg="The menu file (Pack/Title.pack) isn't one this tool knows. It comes from the game.")
        (self.romfs / "Pack" / "Title.pack").write_bytes(PACK)
        tm.write(self.install / "extracted" / "aoc1", {"Pack/Title.pack": b"made up"})
        self.refused(msg="Your DLC has its own menu file. That isn't supported yet.")

    def test_bad_pictures(self):
        small = write_pic(self.tmp / "small.png", gradient(320, 180))
        self.refused("--mode", "full", pic=small, msg="small.png is too small: 320 x 180. Use at least 640 x 360.")
        junk = self.tmp / "photo.heic"
        junk.write_bytes(b"made up")
        self.refused(pic=junk, msg="Can't read photo.heic.")
        self.refused("--sets", "9", msg="--sets 9: the menu has 3 slideshow pictures")

    def test_size_table(self):
        tm.write(self.romfs, {"System/Resource/ResourceSizeTable.product.srsizetable": fake_rstb(ARCHIVE_SIZE - 1)})
        self.refused(msg="The new menu file would be too big for the game's size table. Nothing was changed.")
        tm.write(self.romfs, {"System/Resource/ResourceSizeTable.product.srsizetable": fake_rstb(ARCHIVE_SIZE)})
        rstb = wc.yaz0_compress(wc.rstb_build([(1, 2)], []))
        src = tm.write(self.tmp / "dl" / "Sizes", {"romfs/System/Resource/ResourceSizeTable.product.srsizetable": rstb})
        quiet(mm.add_mod, self.lib, str(src))
        out = self.set("--no-deploy")
        self.assertIn("Sizes's size table has no entry for the menu file", out)


class Deploying(Base):
    def test_game_running(self):
        with mock.patch.object(mm, "game_running", return_value=True):
            out = self.set()
        self.assertIn("Done: the menu picture is in your library (all 3 slideshow pictures).", out)
        self.assertIn("Close the game, then deploy: python tools/modman.py deploy", out)
        self.assertEqual(self.deploys, [])

    def test_rebuild_needed_and_no_deploy(self):
        self.rebuild_plan = "build"
        out = self.set()
        self.assertIn("Not deployed: your other mods need the game code rebuilt first", out)
        self.assertEqual(self.deploys, [True])
        self.deploys.clear()
        out = self.set("--no-deploy")
        self.assertIn("Not deployed (--no-deploy).", out)
        self.assertEqual(self.deploys, [])

    def test_deploy_refused_keeps_the_picture(self):
        with mock.patch.object(mm, "deploy", side_effect=mm.ModmanError("Can't deploy (made-up reason).")):
            out = self.set()
        self.assertIn("Done: the menu picture is in your library", out)
        self.assertIn("Not deployed: Can't deploy (made-up reason).", out)
        self.assertEqual([m["id"] for m in self.lib.mods], [mb.MOD_ID])

    def test_reset_deploys(self):
        self.set("--no-deploy")
        rc, out = run(["reset", "--library", str(self.libdir)])
        self.assertIn("Deployed. The game's own menu is back.", out)
        self.assertEqual(self.deploys, [True, False])


class MadeAtDeploy(Base):
    """deploy_files: modman's deploy asks for the title picture (Mods page v2 contract)."""

    class FakePlan:
        def __init__(self, files: dict, rstb=None):
            self.files, self.rstb = files, rstb

        def winner(self, rel):
            data = self.files.get(rel)
            return None if data is None else mock.Mock(read=lambda: data, mod="Their Menu")

    def test_no_picture(self):
        gen = mb.deploy_files(self.lib, None, None)
        self.assertEqual((list(gen), gen.notes, gen.attention), ([], [], []))
        self.assertEqual(vars(gen), {"notes": [], "attention": []})   # (nothing for launch.json's "made")
        self.assertIs(mm.feature("menu_bg", "deploy_files"), mb.deploy_files)

    def test_over_the_game_and_cached(self):
        gen = mb.deploy_files(self.lib, None, str(self.pic))
        self.assertEqual(gen.attention, [])
        [(rel, pack, rstb)] = gen
        self.assertEqual((rel, rstb, gen.cached, gen.mode, gen.picture), ("Pack/Title.pack", None, False, "art",
                                                                          "linkle.png"))
        self.assertEqual(gen.notes, ["linkle.png is on the main menu (all 3 slideshow pictures)."])
        new, old = mb.open_menu(pack), menu()
        self.assertEqual(len(new.archive), len(old.archive))
        changed = {t.name for t, o in zip(new.textures.textures, old.textures.textures) if t.levels != o.levels}
        self.assertTrue({"Pic_Back^o", "Pic_A^q", "Pic_B^q"} <= changed <= set(SLOT_NAMES), changed)
        again = mb.deploy_files(self.lib, None, str(self.pic))
        self.assertTrue(again.cached)
        self.assertEqual(again[0][1], pack)
        self.assertEqual(len(os.listdir(self.libdir / "cache" / "menu-bg")), 1)
        self.assertFalse((self.libdir / "mods").exists() and os.listdir(self.libdir / "mods"))   # (no mod added)

    def test_over_the_winning_menu_file(self):
        theirs = fake_pack(seed=5)
        gen = mb.deploy_files(self.lib, self.FakePlan({"Pack/Title.pack": theirs}), str(self.pic))
        self.assertIn("Made over the menu file of Their Menu.", gen.notes[0])
        new, base = mb.open_menu(gen[0][1]), mb.open_menu(theirs)
        keep = dict(base.arc_files)
        self.assertTrue(all(keep[n] == d for n, d in new.arc_files if n != new.bntx_name))
        small = self.FakePlan({"Pack/Title.pack": theirs,
                               "System/Resource/ResourceSizeTable.product.srsizetable": fake_rstb(ARCHIVE_SIZE - 1)})
        other = write_pic(self.tmp / "pics" / "other.png", gradient(500, 400))
        gen = mb.deploy_files(self.lib, small, str(other))
        self.assertEqual(list(gen), [])
        self.assertIn("too big for the game's size table", gen.attention[0])

    def test_problems_are_one_line(self):
        small = write_pic(self.tmp / "small.png", gradient(200, 100))
        gen = mb.deploy_files(self.lib, None, str(small))
        self.assertEqual(list(gen), [])
        self.assertEqual(gen.attention, ["the title picture small.png isn't used this time: small.png is too small: "
                                         "200 x 100. Use at least 360 pixels on the short side."])
        (self.install / "extracted" / "exefs" / "main").write_bytes(make_nso(bytes(32), tm.SEGS))
        gen = mb.deploy_files(self.lib, None, str(self.pic))
        self.assertIn("This tool works with BotW 1.9.0 only.", gen.attention[0])
        gen = mb.deploy_files(self.lib, None, str(self.tmp / "missing.png"))
        self.assertEqual(list(gen), [])
        self.assertTrue(gen.attention)

    def test_a_too_big_file_is_not_read(self):
        with mock.patch.object(mb, "MAX_FILE", 1000), mock.patch.object(mb, "load_picture") as load:
            gen = mb.deploy_files(self.lib, None, str(self.pic))
        self.assertEqual(list(gen), [])
        self.assertIn("linkle.png is too big", gen.attention[0])
        load.assert_not_called()


class Preview(Base):
    def test_preview(self):
        before = self.config()
        listing = sorted(os.listdir(self.libdir))
        out_png = self.tmp / "out" / "frame.png"
        rc, out = run(["preview", str(self.pic), "--out", str(out_png), "--library", str(self.libdir), "--set", "2"])
        self.assertEqual(rc, 0, out)
        pic = mb.read_png(out_png.read_bytes())
        self.assertEqual((pic.w, pic.h), (1280, 720))
        rc, out = run(["preview", "--out", str(self.tmp / "out" / "sheet.png"), "--library", str(self.libdir),
                       "--set", "all", "--mode", "full", str(self.pic)])
        self.assertEqual(rc, 0, out)
        sheet = mb.read_png((self.tmp / "out" / "sheet.png").read_bytes())
        self.assertEqual((sheet.w, sheet.h), (1280, 720))   # 3 frames of 640 x 360, 2 a row
        self.assertEqual((self.config(), sorted(os.listdir(self.libdir))), (before, listing))
        rc, out = run(["preview", "--out", str(self.install / "x.png"), "--library", str(self.libdir)])
        self.assertEqual(rc, 2)
        self.assertIn("Write the preview outside", out)
        self.assertFalse((self.install / "x.png").exists())

    def test_render_shows_the_set(self):
        m = menu()
        r = mb.Renderer(m)
        frame = r.render(150.0, 0.25)   # set 2 at quarter size: the picture on the left, the pattern elsewhere
        self.assertEqual((frame.w, frame.h), (320, 180))


class Cli(unittest.TestCase):
    def test_help(self):
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            mb.main(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_doc_commands_parse(self):
        text = (ROOT / "docs" / "menu-background.md").read_text(encoding="utf-8")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("python tools/menu_bg.py")]
        self.assertGreater(len(lines), 5)
        for ln in lines:
            argv = shlex.split(ln)[2:]
            argv = [re.sub(r"<[^>]+>", "x.png", a) for a in argv if not a.startswith("[")]
            if any(c in " ".join(argv) for c in "[]|"):
                continue
            with self.subTest(line=ln):
                mb.parser().parse_args(argv)

    def test_window_logic(self):
        cmd = mb.gui_command(["a.png", "b b.jpg"], "full", r"C:\lib")
        self.assertEqual(cmd[1:], ["-u", mb.SCRIPT, "set", "a.png", "b b.jpg", "--mode", "full", "--library", r"C:\lib"])
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "python.exe").write_bytes(b"")
            with mock.patch.object(mb.sys, "executable", str(Path(d) / "pythonw.exe")):
                self.assertEqual(mb.console_python(), str(Path(d) / "python.exe"))
        self.assertEqual(mb.gui_result(0, ["Step 1 of 5: x", "Done: your picture is on the main menu (all 7)."]),
                         ("ok", "Done: your picture is on the main menu (all 7)."))
        self.assertEqual(mb.gui_result(2, ["Step 1 of 5: x", "shot.png is damaged. Save it again."]),
                         ("error", "It didn't work: shot.png is damaged. Save it again."))
        self.assertEqual(mb.gui_result(130, []), ("error", "Stopped."))

    def test_clean(self):
        import check_clean
        for rel in ("tools/menu_bg.py", "tests/test_menu_bg.py", "docs/menu-background.md"):
            self.assertEqual(check_clean.problems_for(ROOT, rel), [], rel)


@unittest.skipUnless(os.environ.get("MENU_BG_SLOW") == "1", "slow: set MENU_BG_SLOW=1")
class Slow(unittest.TestCase):
    def test_full_size_timing(self):
        """A 1920 x 1080 made-up picture into the made-up layout with full-size textures (1920 x 1080 back and
        front, about 800 x 800 singles), with and without Pillow."""
        import time
        lay = mb.read_flyt(fake_layout())
        anims = {"loop": mb.read_flan(fake_loop()), "in": mb.read_flan(fake_in())}
        sizes = {"Pic_Back^o": (1920, 1080), "Pic_Front^q": (1920, 800), "Pic_A^q": (732, 800),
                 "Pic_B^q": (876, 840)}
        info = {n: (f, *sizes.get(n, (w, h)), 1) for n, f, w, h, _ in TEXTURES}
        model = mb.build_model(lay, anims, info)
        rng = random.Random(1)
        plane = (bytes(rng.randrange(256) for _ in range(512 * 512)), 512, 512)
        pic = gradient(1920, 1080)
        pic.px[0::7] = bytes(rng.randrange(256) for _ in range(len(pic.px[0::7])))   # (some detail)
        pic.px[3::4] = b"\xff" * (1920 * 1080)
        for mode in ("art", "full"):
            for pil in (True, False):
                if pil and not PILImage:
                    continue
                ctx = contextlib.nullcontext() if pil else mock.patch.dict(sys.modules, {"PIL": None})
                with ctx:
                    t = time.time()
                    images = mb.make_slots(model, [pic], mode, {1, 2, 3}, "center", plane)
                    t1 = time.time()
                    mb.encode_slots(model, images)
                    print(f"\n  {mode}, {'with' if pil else 'without'} Pillow: fit {t1 - t:.1f} s, "
                          f"encode {time.time() - t1:.1f} s", end="")


if __name__ == "__main__":
    unittest.main()
