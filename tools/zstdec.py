#!/usr/bin/env python3
"""A Zstandard decoder in pure Python (RFC 8878), with dictionaries. tools/ukmm.py reads UKMM packages with it.

    import zstdec
    data = zstdec.decompress(frame_bytes, dictionary=zstdec.Dictionary(dict_bytes))

Python 3.14 has its own decoder (compression.zstd, in C): decompress() uses it when it is there and the frames
decode with it, and this one otherwise. Written from the RFC; only decoding, no checksum check (XXH64 would cost as
much as the decoding in pure Python), skippable frames skipped. Speed: a few MB a second.
"""
from __future__ import annotations

import struct

MAGIC = 0xFD2FB528
DICT_MAGIC = 0xEC30A437
MAX_BLOCK = 128 << 10
MAX_OUTPUT = 1 << 31          # a frame that says it is bigger than this is refused

try:  # Python 3.14+
    from compression import zstd as _czstd  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - depends on the Python version
    _czstd = None


class ZstdError(ValueError):
    pass


# ------------------------------------------------------------------------------------------------ tables (RFC 8878)

LL_BASE = list(range(16)) + [16, 18, 20, 22, 24, 28, 32, 40, 48, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384,
                             32768, 65536]
LL_BITS = [0] * 16 + [1, 1, 1, 1, 2, 2, 3, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
ML_BASE = list(range(3, 35)) + [35, 37, 39, 41, 43, 47, 51, 59, 67, 83, 99, 131, 259, 515, 1027, 2051, 4099, 8195,
                                16387, 32771, 65539]
ML_BITS = [0] * 32 + [1, 1, 1, 1, 2, 2, 3, 3, 4, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
LL_DEFAULT = ([4, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 2, 1, 1, 1, 1, 1, -1, -1,
               -1, -1], 6)
ML_DEFAULT = ([1, 4, 3, 2, 2, 2, 2, 2, 2] + [1] * 37 + [-1] * 7, 6)
OF_DEFAULT = ([1, 1, 1, 1, 1, 1, 2, 2, 2] + [1] * 15 + [-1] * 5, 5)


class Fse:
    """An FSE decoding table: per state its symbol, the bits to read and the base of the next state."""
    __slots__ = ("log", "sym", "nb", "base", "cache")

    def __init__(self, log: int, sym: list[int], nb: list[int], base: list[int]):
        self.log, self.sym, self.nb, self.base = log, sym, nb, base
        self.cache = None

    @classmethod
    def build(cls, probs: list[int], log: int) -> Fse:
        size = 1 << log
        sym = [0] * size
        high = size - 1
        nxt = [0] * len(probs)
        for s, p in enumerate(probs):
            if p == -1:
                sym[high] = s
                high -= 1
                nxt[s] = 1
            elif p > 0:
                nxt[s] = p
        pos, step, mask = 0, (size >> 1) + (size >> 3) + 3, size - 1
        for s, p in enumerate(probs):
            for _ in range(p if p > 0 else 0):
                sym[pos] = s
                pos = (pos + step) & mask
                while pos > high:
                    pos = (pos + step) & mask
        if pos:
            raise ZstdError("bad FSE distribution")
        nb, base = [0] * size, [0] * size
        for u in range(size):
            x = nxt[sym[u]]
            nxt[sym[u]] = x + 1
            n = log - (x.bit_length() - 1)
            nb[u] = n
            base[u] = (x << n) - size
        return cls(log, sym, nb, base)

    @classmethod
    def rle(cls, symbol: int) -> Fse:
        return cls(0, [symbol], [0], [0])


_PREDEFINED: dict[str, Fse] = {}


def _predefined(kind: str) -> Fse:
    if kind not in _PREDEFINED:
        probs, log = {"ll": LL_DEFAULT, "ml": ML_DEFAULT, "of": OF_DEFAULT}[kind]
        _PREDEFINED[kind] = Fse.build(probs, log)
    return _PREDEFINED[kind]


def read_ncount(src, pos: int, end: int, max_log: int, max_sym: int) -> tuple[list[int], int, int]:
    """An FSE table description (normalized counts), read forward: (probabilities, accuracy log, next position)."""
    if pos >= end:
        raise ZstdError("FSE table description cut short")
    window = int.from_bytes(src[pos:min(end, pos + 8)], "little")
    have = 8 * min(8, end - pos)
    nxt = pos + 8
    used = 0                                    # bits taken from the description so far

    def bits(n: int) -> int:
        nonlocal window, have, nxt
        while have < n:
            if nxt >= end:
                window_bits = 0
            else:
                window_bits = src[nxt]
            window |= window_bits << have
            have += 8
            nxt += 1
        return window & ((1 << n) - 1)

    def take(n: int) -> None:
        nonlocal window, have, used
        window >>= n
        have -= n
        used += n

    log = bits(4) + 5
    take(4)
    if log > max_log:
        raise ZstdError("FSE accuracy too high")
    remaining = (1 << log) + 1
    threshold = 1 << log
    nbits = log + 1
    probs: list[int] = []
    while remaining > 1:
        if len(probs) > max_sym:
            raise ZstdError("FSE table has too many symbols")
        maxv = 2 * threshold - 1 - remaining
        low = bits(nbits) & (threshold - 1)
        if low < maxv:
            count = low
            take(nbits - 1)
        else:
            count = bits(nbits) & (2 * threshold - 1)
            if count >= threshold:
                count -= maxv
            take(nbits)
        count -= 1
        remaining -= -count if count < 0 else count
        probs.append(count)
        if count == 0:
            while True:
                rep = bits(2)
                take(2)
                probs.extend([0] * rep)
                if rep != 3:
                    break
        while remaining < threshold:
            nbits -= 1
            threshold >>= 1
    if remaining != 1 or len(probs) > max_sym + 1:
        raise ZstdError("bad FSE table description")
    nxt_pos = pos + (used + 7) // 8
    if nxt_pos > end:
        raise ZstdError("FSE table description cut short")
    return probs, log, nxt_pos


class _Back:
    """A bitstream read backwards (from its last byte's marker bit down), as zstd's are."""
    __slots__ = ("src", "start", "pos")

    def __init__(self, src, start: int, end: int):
        if end <= start or not src[end - 1]:
            raise ZstdError("bitstream without its end marker")
        self.src, self.start = src, start
        self.pos = (end - start - 1) * 8 + src[end - 1].bit_length() - 1   # bits left below the marker

    def read(self, n: int) -> int:
        """n bits; past the start of the stream reads zeros (pos goes negative: callers check overflow)."""
        if not n:
            return 0
        p = self.pos - n
        self.pos = p
        if p >= 0:
            lo = p >> 3
            v = int.from_bytes(self.src[self.start + lo:self.start + ((p + n + 7) >> 3)], "little") >> (p & 7)
            return v & ((1 << n) - 1)
        top = p + n
        if top <= 0:
            return 0
        v = int.from_bytes(self.src[self.start:self.start + ((top + 7) >> 3)], "little") & ((1 << top) - 1)
        return v << -p


# ------------------------------------------------------------------------------------------------ Huffman (literals)

class Huffman:
    """A literals Huffman table: a lookup of `bits` bits -> (symbol, length), and the same packed for many symbols."""
    __slots__ = ("bits", "sym", "length", "_multi")

    def __init__(self, weights: list[int]):
        total = sum(1 << (w - 1) for w in weights if w)
        if not total:
            raise ZstdError("Huffman table without symbols")
        bits = total.bit_length()
        if bits > 11:
            raise ZstdError("Huffman codes too long")
        left = (1 << bits) - total
        if left & (left - 1):
            raise ZstdError("bad Huffman weights")
        weights = weights + [left.bit_length()]
        if len(weights) > 256:
            raise ZstdError("too many Huffman symbols")
        rank_count = [0] * (bits + 2)
        for w in weights:
            if w > bits:
                raise ZstdError("bad Huffman weight")
            rank_count[w] += 1
        start = [0] * (bits + 2)
        nxt = 0
        for w in range(1, bits + 1):
            start[w] = nxt
            nxt += rank_count[w] << (w - 1)
        size = 1 << bits
        sym = bytearray(size)
        length = bytearray(size)
        for s, w in enumerate(weights):
            if not w:
                continue
            n = (1 << w) >> 1
            at = start[w]
            sym[at:at + n] = bytes([s]) * n
            length[at:at + n] = bytes([bits + 1 - w]) * n
            start[w] = at + n
        self.bits, self.sym, self.length, self._multi = bits, bytes(sym), bytes(length), None

    def multi(self) -> list[tuple[bytes, int]]:
        """For each `bits`-bit value: every symbol it decodes whole, and the bits they take (decodes several symbols
        per lookup)."""
        if self._multi is None:
            bits, sym, length = self.bits, self.sym, self.length
            mask = (1 << bits) - 1
            out = []
            for idx in range(1 << bits):
                got = bytearray()
                used = 0
                while True:
                    probe = (idx << used) & mask
                    n = length[probe]
                    if used + n > bits:
                        break
                    got.append(sym[probe])
                    used += n
                out.append((bytes(got), used))
            self._multi = out
        return self._multi


def read_huffman(src, pos: int, end: int) -> tuple[Huffman, int]:
    """A Huffman tree description: (table, next position)."""
    if pos >= end:
        raise ZstdError("Huffman tree description cut short")
    head = src[pos]
    pos += 1
    if head >= 128:
        n = head - 127
        nbytes = (n + 1) // 2
        if pos + nbytes > end:
            raise ZstdError("Huffman weights cut short")
        weights = []
        for i in range(n):
            b = src[pos + i // 2]
            weights.append(b >> 4 if i % 2 == 0 else b & 15)
        return Huffman(weights), pos + nbytes
    if pos + head > end:
        raise ZstdError("Huffman weights cut short")
    stop = pos + head
    probs, log, p = read_ncount(src, pos, stop, 6, 255)
    table = Fse.build(probs, log)
    bs = _Back(src, p, stop)
    s1 = bs.read(log)
    s2 = bs.read(log)
    weights: list[int] = []
    sym, nb, base = table.sym, table.nb, table.base
    while True:
        weights.append(sym[s1])
        s1 = base[s1] + bs.read(nb[s1])
        if bs.pos < 0:
            weights.append(sym[s2])
            break
        weights.append(sym[s2])
        s2 = base[s2] + bs.read(nb[s2])
        if bs.pos < 0:
            weights.append(sym[s1])
            break
        if len(weights) > 255:
            raise ZstdError("too many Huffman weights")
    return Huffman(weights), stop


def _huf_stream(src, start: int, end: int, count: int, huf: Huffman, out: bytearray) -> None:
    """Decode one Huffman-coded literals stream of `count` symbols into out."""
    if not count:
        return
    if end <= start or not src[end - 1]:
        raise ZstdError("literals stream without its end marker")
    bits = huf.bits
    mask = (1 << bits) - 1
    pos = (end - start - 1) * 8 + src[end - 1].bit_length() - 1   # bits left below the marker
    table = huf.multi()
    left = count
    acc, acc_lo = 0, pos                         # acc holds bits [acc_lo, pos) of the stream
    # many symbols per lookup while far from both ends
    while left > bits and pos >= bits:
        if pos - acc_lo < bits:
            lo = pos - 120 if pos > 120 else 0
            lo &= ~7
            acc = int.from_bytes(src[start + (lo >> 3):start + ((pos + 7) >> 3)], "little")
            acc_lo = lo
        got, used = table[(acc >> (pos - bits - acc_lo)) & mask]
        out += got
        left -= len(got)
        pos -= used
    sym, length = huf.sym, huf.length
    while left:
        if pos >= bits:
            if pos - acc_lo < bits:
                lo = max(0, pos - 120) & ~7
                acc = int.from_bytes(src[start + (lo >> 3):start + ((pos + 7) >> 3)], "little")
                acc_lo = lo
            idx = (acc >> (pos - bits - acc_lo)) & mask
        else:
            v = int.from_bytes(src[start:start + ((pos + 7) >> 3)], "little") & ((1 << pos) - 1) if pos > 0 else 0
            idx = (v << (bits - pos)) & mask
        n = length[idx]
        if n > pos:
            raise ZstdError("literals stream ends early")
        out.append(sym[idx])
        pos -= n
        left -= 1
    if pos:
        raise ZstdError("literals stream has bits left over")


# ------------------------------------------------------------------------------------------------ dictionaries

class Dictionary:
    """A zstd dictionary: one made by zstd's trainer (its magic, id, entropy tables, offsets, then content) or plain
    content (id 0)."""

    def __init__(self, data: bytes):
        try:
            self._load(bytes(data))
        except ZstdError:
            raise
        except (IndexError, KeyError, TypeError, ValueError, struct.error) as e:
            raise ZstdError(f"damaged zstd dictionary ({e})") from None

    def _load(self, data: bytes) -> None:
        self.raw = data
        self.huf: Huffman | None = None
        self.ll = self.of = self.ml = None
        self.rep = [1, 4, 8]
        if len(data) >= 8 and struct.unpack_from("<I", data)[0] == DICT_MAGIC:
            self.id = struct.unpack_from("<I", data, 4)[0]
            end = len(data)
            self.huf, pos = read_huffman(data, 8, end)
            probs, log, pos = read_ncount(data, pos, end, 8, 31)
            self.of = Fse.build(probs, log)
            probs, log, pos = read_ncount(data, pos, end, 9, 52)
            self.ml = Fse.build(probs, log)
            probs, log, pos = read_ncount(data, pos, end, 9, 35)
            self.ll = Fse.build(probs, log)
            if pos + 12 > end:
                raise ZstdError("dictionary cut short")
            self.rep = list(struct.unpack_from("<III", data, pos))
            if not all(0 < r <= end - pos - 12 for r in self.rep):
                raise ZstdError("dictionary has bad repeat offsets")
            self.content = data[pos + 12:]
        else:
            self.id = 0
            self.content = data


# ------------------------------------------------------------------------------------------------ frames

class _State:
    __slots__ = ("huf", "ll", "of", "ml", "rep")

    def __init__(self, d: Dictionary | None):
        self.huf = d.huf if d else None
        self.ll = d.ll if d else None
        self.of = d.of if d else None
        self.ml = d.ml if d else None
        self.rep = list(d.rep) if d else [1, 4, 8]


def frame_info(data: bytes, pos: int = 0) -> tuple[int, int | None]:
    """(dictionary id, content size or None) of the frame at pos."""
    if len(data) < pos + 5 or struct.unpack_from("<I", data, pos)[0] != MAGIC:
        raise ZstdError("not a zstd frame")
    fhd = data[pos + 4]
    single = fhd >> 5 & 1
    did_size = (0, 1, 2, 4)[fhd & 3]
    fcs_size = (single, 2, 4, 8)[fhd >> 6]
    p = pos + 5 + (0 if single else 1)
    if len(data) < p + did_size + fcs_size:
        raise ZstdError("zstd frame header cut short")
    did = int.from_bytes(data[p:p + did_size], "little")
    p += did_size
    fcs = int.from_bytes(data[p:p + fcs_size], "little") if fcs_size else None
    if fcs_size == 2:
        fcs += 256
    return did, fcs


def _literals(src, pos: int, end: int, st: _State) -> tuple[bytes | bytearray, int]:
    b0 = src[pos]
    kind = b0 & 3
    fmt = (b0 >> 2) & 3
    if kind < 2:                                         # raw or RLE
        if fmt in (0, 2):
            size, hdr = b0 >> 3, 1
        elif fmt == 1:
            size, hdr = (b0 >> 4) + (src[pos + 1] << 4), 2
        else:
            size, hdr = (b0 >> 4) + (src[pos + 1] << 4) + (src[pos + 2] << 12), 3
        pos += hdr
        if size > MAX_BLOCK:
            raise ZstdError("more literals than a zstd block holds")
        if kind == 0:
            if pos + size > end:
                raise ZstdError("raw literals cut short")
            return src[pos:pos + size], pos + size
        if pos >= end:
            raise ZstdError("RLE literals cut short")
        return bytes([src[pos]]) * size, pos + 1
    if fmt < 2:
        h = int.from_bytes(src[pos:pos + 3], "little")
        regen, comp, hdr, streams = (h >> 4) & 0x3FF, (h >> 14) & 0x3FF, 3, 1 if fmt == 0 else 4
    elif fmt == 2:
        h = int.from_bytes(src[pos:pos + 4], "little")
        regen, comp, hdr, streams = (h >> 4) & 0x3FFF, (h >> 18) & 0x3FFF, 4, 4
    else:
        h = int.from_bytes(src[pos:pos + 5], "little")
        regen, comp, hdr, streams = (h >> 4) & 0x3FFFF, (h >> 22) & 0x3FFFF, 5, 4
    pos += hdr
    stop = pos + comp
    if stop > end or regen > MAX_BLOCK:
        raise ZstdError("compressed literals cut short")
    if kind == 2:
        st.huf, pos = read_huffman(src, pos, stop)
    elif st.huf is None:
        raise ZstdError("literals reuse a Huffman table that isn't there")
    out = bytearray()
    if streams == 1:
        _huf_stream(src, pos, stop, regen, st.huf, out)
    else:
        if pos + 6 > stop:
            raise ZstdError("literals jump table cut short")
        s1, s2, s3 = struct.unpack_from("<HHH", src, pos)
        p1 = pos + 6
        p2, p3, p4 = p1 + s1, p1 + s1 + s2, p1 + s1 + s2 + s3
        if p4 > stop:
            raise ZstdError("literals streams cut short")
        seg = (regen + 3) // 4
        if 3 * seg > regen:
            raise ZstdError("too few literals for four streams")
        _huf_stream(src, p1, p2, seg, st.huf, out)
        _huf_stream(src, p2, p3, seg, st.huf, out)
        _huf_stream(src, p3, p4, seg, st.huf, out)
        _huf_stream(src, p4, stop, regen - 3 * seg, st.huf, out)
    return out, stop


def _table(src, pos: int, end: int, mode: int, kind: str, max_log: int, max_sym: int, prev: Fse | None
           ) -> tuple[Fse, int]:
    if mode == 0:
        return _predefined(kind), pos
    if mode == 1:
        if pos >= end:
            raise ZstdError("RLE table cut short")
        if src[pos] > max_sym:
            raise ZstdError("RLE symbol out of range")
        return Fse.rle(src[pos]), pos + 1
    if mode == 2:
        probs, log, pos = read_ncount(src, pos, end, max_log, max_sym)
        return Fse.build(probs, log), pos
    if prev is None:
        raise ZstdError("sequences reuse a table that isn't there")
    return prev, pos


def _block(src, pos: int, end: int, out: bytearray, st: _State) -> None:
    lits, pos = _literals(src, pos, end, st)
    if pos >= end:
        raise ZstdError("sequences section missing")
    b0 = src[pos]
    if b0 == 0:
        if pos + 1 != end:
            raise ZstdError("block has bytes after its sequences")
        out += lits
        return
    if b0 < 128:
        nseq, pos = b0, pos + 1
    elif b0 < 255:
        nseq, pos = ((b0 - 128) << 8) + src[pos + 1], pos + 2
    else:
        nseq, pos = src[pos + 1] + (src[pos + 2] << 8) + 0x7F00, pos + 3
    if pos >= end:
        raise ZstdError("sequences section cut short")
    modes = src[pos]
    pos += 1
    if modes & 3:
        raise ZstdError("reserved bits set in the sequences header")
    st.ll, pos = _table(src, pos, end, modes >> 6, "ll", 9, 35, st.ll)
    st.of, pos = _table(src, pos, end, (modes >> 4) & 3, "of", 8, 31, st.of)
    st.ml, pos = _table(src, pos, end, (modes >> 2) & 3, "ml", 9, 52, st.ml)
    _sequences(src, pos, end, nseq, lits, out, st)


def _seq_values(t: Fse, base: list[int], bits: list[int]) -> tuple[list[int], list[int]]:
    """Per state of a literal or match length table: its baseline and its extra bits (kept on the table)."""
    if t.cache is None:
        t.cache = ([base[s] for s in t.sym], [bits[s] for s in t.sym])
    return t.cache


def _sequences(src, start: int, end: int, nseq: int, lits, out: bytearray, st: _State) -> None:
    if end <= start or not src[end - 1]:
        raise ZstdError("sequences bitstream without its end marker")
    bp = (end - start - 1) * 8 + src[end - 1].bit_length() - 1
    ll_t, of_t, ml_t = st.ll, st.of, st.ml
    ll_log, of_log, ml_log = ll_t.log, of_t.log, ml_t.log
    init = ll_log + of_log + ml_log
    bp -= init
    if bp < 0:
        raise ZstdError("sequences bitstream cut short")
    w = int.from_bytes(src[start + (bp >> 3):start + ((bp + init + 7) >> 3)], "little") >> (bp & 7)
    ml_s = w & ((1 << ml_log) - 1)
    of_s = (w >> ml_log) & ((1 << of_log) - 1)
    ll_s = (w >> (ml_log + of_log)) & ((1 << ll_log) - 1)
    # (the tables' symbols are all in range: read_ncount and _table check them)
    llv, llx = _seq_values(ll_t, LL_BASE, LL_BITS)
    mlv, mlx = _seq_values(ml_t, ML_BASE, ML_BITS)
    ofx = of_t.sym
    ll_nb, ll_base = ll_t.nb, ll_t.base
    of_nb, of_base = of_t.nb, of_t.base
    ml_nb, ml_base = ml_t.nb, ml_t.base
    r0, r1, r2 = st.rep
    lp = 0
    nlit = len(lits)
    olen = len(out)
    cap = olen + MAX_BLOCK              # (a block decodes to at most 128 KB, as zstd's own decoder checks)
    frombytes = int.from_bytes
    wv, wlo = 0, bp                     # wv holds the stream's bits from wlo up (to bp or a little above)
    last_i = nseq - 1
    for i in range(nseq):
        oc = ofx[of_s]
        mb = mlx[ml_s]
        lb = llx[ll_s]
        if i != last_i:
            un = ll_nb[ll_s] + ml_nb[ml_s] + of_nb[of_s]
            total = oc + mb + lb + un
        else:
            un = 0
            total = oc + mb + lb
        if total:
            nbp = bp - total
            if nbp < wlo:
                if nbp < 0:
                    raise ZstdError("sequences bitstream cut short")
                lo = (nbp - 448) & ~7 if nbp > 448 else 0
                wv = frombytes(src[start + (lo >> 3):start + ((bp + 7) >> 3)], "little")
                wlo = lo
            w = (wv >> (nbp - wlo)) & ((1 << total) - 1)
            bp = nbp
        else:
            w = 0
        sh = total - oc
        ofv = (1 << oc) | ((w >> sh) & ((1 << oc) - 1))
        sh -= mb
        ml = mlv[ml_s] + ((w >> sh) & ((1 << mb) - 1))
        sh -= lb
        ll = llv[ll_s] + ((w >> sh) & ((1 << lb) - 1))
        if un:
            n = ll_nb[ll_s]
            sh -= n
            nll = ll_base[ll_s] + ((w >> sh) & ((1 << n) - 1))
            n = ml_nb[ml_s]
            sh -= n
            nml = ml_base[ml_s] + ((w >> sh) & ((1 << n) - 1))
            n = of_nb[of_s]
            of_s = of_base[of_s] + ((w >> (sh - n)) & ((1 << n) - 1))
            ll_s, ml_s = nll, nml
        elif i != last_i:
            ll_s, ml_s, of_s = ll_base[ll_s], ml_base[ml_s], of_base[of_s]
        # the offset, with the three repeat offsets
        if ofv > 3:
            off = ofv - 3
            r2, r1, r0 = r1, r0, off
        else:
            idx = ofv - 1 + (ll == 0)
            if idx == 0:
                off = r0
            else:
                off = r1 if idx == 1 else r2 if idx == 2 else r0 - 1
                if not off:
                    raise ZstdError("zero offset")
                if idx != 1:
                    r2 = r1
                r0, r1 = off, r0
        # literals, then the match
        if ll:
            if lp + ll > nlit:
                raise ZstdError("sequence takes more literals than the block has")
            out += lits[lp:lp + ll]
            lp += ll
            olen += ll
        at = olen - off
        if at < 0:
            raise ZstdError("match offset before the start of the data")
        if off >= ml:
            out += out[at:at + ml]
        else:
            out += (out[at:] * (ml // off + 1))[:ml]
        olen += ml
        if olen > cap:
            raise ZstdError("zstd block decodes to more than zstd allows")
    if bp:
        raise ZstdError("sequences bitstream has bits left over")
    st.rep = [r0, r1, r2]
    if lp < nlit:
        out += lits[lp:]


def _frame(src, pos: int, dicts: dict[int, Dictionary], default: Dictionary | None, limit: int,
           head: int | None = None) -> tuple[bytearray, int]:
    did, fcs = frame_info(src, pos)
    fhd = src[pos + 4]
    single = fhd >> 5 & 1
    if fhd & 8:
        raise ZstdError("reserved bit set in a zstd frame header")
    checksum = fhd >> 2 & 1
    p = pos + 5 + (0 if single else 1) + (0, 1, 2, 4)[fhd & 3] + (single, 2, 4, 8)[fhd >> 6]
    d = None
    if did:
        d = dicts.get(did)
        if d is None:
            raise ZstdError(f"the data needs zstd dictionary {did}, which isn't loaded")
    elif default is not None and default.id == 0:
        d = default                     # (a content-only dictionary has no id: the caller passed it for this)
    if fcs is not None and fcs > limit:
        raise ZstdError("zstd frame too big")
    prefix = d.content if d else b""
    out = bytearray(prefix)
    st = _State(d)
    n = len(src)
    while True:
        if p + 3 > n:
            raise ZstdError("zstd block header cut short")
        h = src[p] | src[p + 1] << 8 | src[p + 2] << 16
        p += 3
        last, kind, size = h & 1, (h >> 1) & 3, h >> 3
        if kind < 2 and size > MAX_BLOCK:
            raise ZstdError("zstd block bigger than zstd allows")   # (as zstd's own decoder refuses it)
        if kind == 0:
            if p + size > n:
                raise ZstdError("raw block cut short")
            out += src[p:p + size]
            p += size
        elif kind == 1:
            if p >= n:
                raise ZstdError("RLE block cut short")
            out += bytes([src[p]]) * size
            p += 1
        elif kind == 2:
            if size > MAX_BLOCK or p + size > n:
                raise ZstdError("compressed block cut short")
            _block(src, p, p + size, out, st)
            p += size
        else:
            raise ZstdError("reserved block type")
        if len(out) - len(prefix) > limit:
            raise ZstdError("zstd frame too big")
        if head is not None and len(out) - len(prefix) >= head:
            del out[:len(prefix)]
            return out[:head], p
        if last:
            break
    if checksum:
        p += 4
    if p > n:
        raise ZstdError("zstd frame cut short")
    if prefix:
        del out[:len(prefix)]
    if fcs is not None and len(out) != fcs:
        raise ZstdError("zstd frame decodes to the wrong size")
    return out, p


def decompress_py(data, dictionary: Dictionary | None = None, limit: int = MAX_OUTPUT) -> bytes:
    """Every frame in data, decoded by this module (skippable frames skipped)."""
    data = memoryview(data).tobytes() if not isinstance(data, (bytes, bytearray)) else data
    try:
        return _decompress_py(data, dictionary, limit)
    except ZstdError:
        raise
    except (IndexError, KeyError, TypeError, ValueError, struct.error) as e:
        raise ZstdError(f"damaged zstd data ({e})") from None


def _decompress_py(data, dictionary: Dictionary | None, limit: int) -> bytes:
    dicts = {dictionary.id: dictionary} if dictionary is not None and dictionary.id else {}
    out = bytearray()
    pos = 0
    while pos < len(data):
        if len(data) < pos + 4:
            raise ZstdError("data after the last zstd frame")
        magic = struct.unpack_from("<I", data, pos)[0]
        if magic & 0xFFFFFFF0 == 0x184D2A50:
            if len(data) < pos + 8:
                raise ZstdError("skippable frame cut short")
            pos += 8 + struct.unpack_from("<I", data, pos + 4)[0]
            continue
        part, pos = _frame(data, pos, dicts, dictionary, limit - len(out))
        out += part
    return bytes(out)


def decompress_head(data, dictionary: Dictionary | None = None, n: int = 256) -> bytes:
    """The first n bytes the first frame decodes to (only its first blocks are decoded)."""
    if _czstd is not None:
        did, _ = frame_info(data)
        try:
            return _czstd.ZstdDecompressor(zstd_dict=_c_dict(dictionary, did)).decompress(data, max_length=n)[:n]
        except _czstd.ZstdError as e:
            raise ZstdError(str(e)) from None
    data = data if isinstance(data, (bytes, bytearray)) else bytes(data)
    dicts = {dictionary.id: dictionary} if dictionary is not None and dictionary.id else {}
    try:
        return bytes(_frame(data, 0, dicts, dictionary, MAX_OUTPUT, n)[0])
    except ZstdError:
        raise
    except (IndexError, KeyError, TypeError, ValueError, struct.error) as e:
        raise ZstdError(f"damaged zstd data ({e})") from None


_cdicts: dict[int, object] = {}


def _c_dict(dictionary: Dictionary | None, did: int):
    """Python's zstd dictionary for a frame naming dictionary id `did` (None: the frame is decoded without one; a
    frame that names none gets a content-only dictionary, which has no id)."""
    if dictionary is None or (not did and dictionary.id):
        return None
    key = id(dictionary)
    cd = _cdicts.get(key)
    if cd is None or cd[0] is not dictionary:
        cd = _cdicts[key] = (dictionary, _czstd.ZstdDict(dictionary.raw, is_raw=dictionary.id == 0))
    return cd[1]


def frame_end(data, pos: int = 0) -> int:
    """Where the zstd frame at pos ends (its block headers walked, nothing decoded)."""
    frame_info(data, pos)
    fhd = data[pos + 4]
    single = fhd >> 5 & 1
    p = pos + 5 + (0 if single else 1) + (0, 1, 2, 4)[fhd & 3] + (single, 2, 4, 8)[fhd >> 6]
    n = len(data)
    while True:
        if p + 3 > n:
            raise ZstdError("zstd block header cut short")
        h = data[p] | data[p + 1] << 8 | data[p + 2] << 16
        p += 3
        last, kind, size = h & 1, (h >> 1) & 3, h >> 3
        if kind == 3:
            raise ZstdError("reserved block type")
        p += 1 if kind == 1 else size
        if last:
            break
    if fhd >> 2 & 1:
        p += 4
    if p > n:
        raise ZstdError("zstd frame cut short")
    return p


def _decompress_c(data, dictionary: Dictionary | None, limit: int) -> bytes:
    """Every frame in data (skippable frames skipped) with Python's own decoder, never decoding more than limit + 1
    bytes: each frame is fed on its own, its output capped at what is left of the limit."""
    data = data if isinstance(data, (bytes, bytearray)) else bytes(data)
    parts: list[bytes] = []
    total, pos, n = 0, 0, len(data)
    while pos < n:
        if n < pos + 4:
            raise ZstdError("data after the last zstd frame")
        if struct.unpack_from("<I", data, pos)[0] & 0xFFFFFFF0 == 0x184D2A50:
            if n < pos + 8:
                raise ZstdError("skippable frame cut short")
            pos += 8 + struct.unpack_from("<I", data, pos + 4)[0]
            if pos > n:
                raise ZstdError("skippable frame cut short")
            continue
        did, fcs = frame_info(data, pos)
        if fcs is not None and fcs > limit - total:
            raise ZstdError("zstd data too big")
        end = frame_end(data, pos)
        dec = _czstd.ZstdDecompressor(zstd_dict=_c_dict(dictionary, did))
        part = dec.decompress(data if pos == 0 and end == n else data[pos:end], max_length=limit - total + 1)
        total += len(part)
        if total > limit:
            raise ZstdError("zstd data too big")
        if not dec.eof or dec.unused_data:
            raise ZstdError("zstd frame cut short")
        parts.append(part)
        pos = end
    return parts[0] if len(parts) == 1 else b"".join(parts)


def decompress(data, dictionary: Dictionary | None = None, limit: int = MAX_OUTPUT) -> bytes:
    """Decode zstd data: with Python's own decoder when it has one (3.14+), else with this module's. A frame that
    names no dictionary is decoded without one (its repeat offsets start from zstd's defaults, not the
    dictionary's). More than `limit` bytes of output is refused before it is made, either way."""
    if _czstd is not None:
        try:
            return _decompress_c(data, dictionary, limit)
        except _czstd.ZstdError as e:
            raise ZstdError(str(e)) from None
        except (IndexError, struct.error) as e:
            raise ZstdError(f"damaged zstd data ({e})") from None
    return decompress_py(data, dictionary, limit)
