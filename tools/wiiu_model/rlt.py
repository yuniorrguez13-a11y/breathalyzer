"""Switch relocation table (`_RLT`): encode and decode. Rules: out/model-work/switch-format.md section 2.5 (the
encoder reproduced 301/301 relocation sections of the user's files exactly).

Entry: u32 position, u16 struct count, u8 pointers per struct, u8 skipped u64 slots. Pointer positions of an entry:
position + 8 * (j * (k + s) + i) for j < count, i < k. A section's entries list the pointers whose *target* lies in
that section, sorted by position.
"""
from __future__ import annotations

import struct
from bisect import bisect_left

from .errors import Damaged


def encode(positions) -> list[tuple[int, int, int, int]]:
    """Entries for a set of pointer positions, as the game's tool makes them: take the lowest position p and its run
    of k consecutive pointers; if a later run of exactly k starts within 255 skipped slots, repeat with that stride
    as long as runs of k follow. Candidates are looked up in a sorted list, and only inside the reachable window."""
    left = set(positions)
    order = sorted(left)
    ents = []
    i = 0

    def run(p: int) -> int:
        k = 0
        while p + 8 * k in left and k < 255:
            k += 1
        return k

    def starts_run(p: int, k: int) -> bool:
        return p in left and (p - 8) not in left and run(p) == k

    while left:
        while order[i] not in left:
            i += 1
        p = order[i]
        k = run(p)
        q = None
        hi = p + 8 * (k + 255)
        j = bisect_left(order, p + 8 * k, i)
        while j < len(order) and order[j] <= hi:
            c = order[j]
            if starts_run(c, k):
                q = c
                break
            j += 1
        n, skip = 1, 0
        if q is not None:
            stride = (q - p) // 8
            skip = stride - k
            n = 2
            while n < 0xFFFF and starts_run(p + n * stride * 8, k):
                n += 1
        ents.append((p, n, k, skip))
        for jj in range(n):
            for ii in range(k):
                left.discard(p + 8 * (jj * (k + skip) + ii))
    ents.sort()
    return ents


def decode(entries) -> list[int]:
    out = []
    for p, n, k, s in entries:
        for j in range(n):
            for i in range(k):
                out.append(p + 8 * (j * (k + s) + i))
    return out


def build(rlt_off: int, sections: list[tuple[int, int]], pointers: list[tuple[int, int]]) -> bytes:
    """`_RLT` bytes at offset `rlt_off`. sections: [(offset, size)]; pointers: [(position, target)]. A pointer goes
    to the section holding its target (an empty section never holds one)."""
    per = [[] for _ in sections]
    for pos, tgt in pointers:
        for i, (so, ss) in enumerate(sections):
            if so <= tgt < so + ss:
                per[i].append(pos)
                break
        else:
            raise ValueError(f"pointer at {pos:#x} -> {tgt:#x} is in no relocation section")
    encoded = [encode(p) for p in per]
    out = bytearray(b"_RLT" + struct.pack("<III", rlt_off, len(sections), 0))
    first = 0
    for (so, ss), ents in zip(sections, encoded):
        out += struct.pack("<QIIii", 0, so, ss, first, len(ents))
        first += len(ents)
    for ents in encoded:
        for e in ents:
            out += struct.pack("<IHBB", *e)
    return bytes(out)


def parse(data, off: int, base: int = 0) -> tuple[list[tuple[int, int]], list[list[int]]]:
    """(sections [(offset, size)], pointer positions per section) of the `_RLT` at base + off."""
    a = base + off
    if bytes(data[a:a + 4]) != b"_RLT":
        raise Damaged(f"no relocation table at {a:#x}")
    n = struct.unpack_from("<I", data, a + 8)[0]
    if n > 16:
        raise Damaged(f"relocation table with {n} sections")
    secs, idx = [], []
    for i in range(n):
        _, so, ss, first, cnt = struct.unpack_from("<QIIii", data, a + 0x10 + 0x18 * i)
        secs.append((so, ss))
        idx.append((first, cnt))
    eoff = a + 0x10 + 0x18 * n
    out = []
    for first, cnt in idx:
        if first < 0 or cnt < 0 or eoff + 8 * (first + cnt) > len(data):
            raise Damaged("relocation entries run past the end of the file")
        out.append(decode([struct.unpack_from("<IHBB", data, eoff + 8 * e) for e in range(first, first + cnt)]))
    return secs, out


def size(sections: int, entries: int) -> int:
    return 0x10 + 0x18 * sections + 8 * entries
