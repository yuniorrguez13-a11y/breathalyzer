"""Dictionaries (Patricia tries) of Switch BFRES/BNTX, and the Wii U index group.

Switch build and lookup rules: out/model-work/switch-format.md section 2.4 (the builder rebuilt 1827/1827 dictionaries
of the user's files byte for byte). Bit b of a key is bit (b % 8) of the byte b // 8 counted from the key's last byte.
The Wii U index group uses the same trie with its own node layout (u32 bit, u16 left, u16 right, s32 name, s32 data);
our Wii U files are only for tests and the reader does not use the trie.
"""
from __future__ import annotations

import struct

from .errors import Damaged


def bit(key: bytes, b: int) -> int:
    i = b >> 3
    if i >= len(key):
        return 0
    return (key[len(key) - 1 - i] >> (b & 7)) & 1


def first_diff(a: bytes, b: bytes) -> int:
    x = int.from_bytes(a, "big") ^ int.from_bytes(b, "big")
    if not x:
        raise ValueError(f"duplicate dictionary key {a!r}")
    return (x & -x).bit_length() - 1


def build(keys: list[bytes]) -> list[tuple[int, int, int]]:
    """[(ref bit, left, right)] for the root (index 0) and each key in order (index i+1). A key is handled as the
    number int.from_bytes(key, 'big'): its bit b is the key's bit b counted from the last byte."""
    ref, left, right = [-1], [0], [0]
    nums = [0]
    for key in keys:
        x = int.from_bytes(key, "big")
        prev, cur = 0, left[0]
        while ref[cur] > ref[prev]:
            prev, cur = cur, (right[cur] if (x >> ref[cur]) & 1 else left[cur])
        d = x ^ nums[cur]
        if not d:
            raise ValueError(f"duplicate dictionary key {key!r}")
        b = (d & -d).bit_length() - 1
        prev, cur = 0, left[0]
        while ref[cur] > ref[prev] and ref[cur] < b:
            prev, cur = cur, (right[cur] if (x >> ref[cur]) & 1 else left[cur])
        n = len(ref)
        ref.append(b)
        nums.append(x)
        if (x >> b) & 1:
            left.append(cur)
            right.append(n)
        else:
            left.append(n)
            right.append(cur)
        if prev == 0:
            left[0] = n
        elif (x >> ref[prev]) & 1:
            right[prev] = n
        else:
            left[prev] = n
    return list(zip(ref, left, right))


def lookup(nodes: list[tuple[int, int, int]], keys: list[bytes], key: bytes) -> int:
    """Index (0-based, into `keys`) of `key`, or -1. `nodes` as from build()/read."""
    if len(nodes) < 2:
        return -1
    prev, cur = 0, nodes[0][1]
    while nodes[cur][0] > nodes[prev][0]:
        prev, cur = cur, (nodes[cur][2] if bit(key, nodes[cur][0]) else nodes[cur][1])
    return cur - 1 if cur and keys[cur - 1] == key else -1


def nx_dict_size(n: int) -> int:
    return 8 + 16 * (n + 1)


def nx_dict(magic: bytes, keys: list[str], key_ptrs: list[int], empty_ptr: int) -> bytes:
    """Switch dictionary bytes. key_ptrs: string pointers (absolute in the container) of the keys; empty_ptr: pointer
    of the empty string (root key)."""
    nodes = build([k.encode("utf-8") for k in keys])
    out = bytearray(magic + struct.pack("<i", len(keys)))
    for i, (r, lf, rt) in enumerate(nodes):
        out += struct.pack("<iHHQ", r, lf, rt, empty_ptr if i == 0 else key_ptrs[i - 1])
    return bytes(out)


def read_nx_dict(data, off: int) -> tuple[list[tuple[int, int, int]], list[int]]:
    """(nodes, key pointers of entries 1..n) of a Switch dictionary at `off`."""
    if off + 8 > len(data):
        raise Damaged(f"dictionary at {off:#x} is outside the file")
    n = struct.unpack_from("<i", data, off + 4)[0]
    if n < 0 or off + nx_dict_size(n) > len(data):
        raise Damaged(f"dictionary at {off:#x} has a bad entry count {n}")
    nodes, ptrs = [], []
    for i in range(n + 1):
        r, lf, rt, k = struct.unpack_from("<iHHQ", data, off + 8 + 16 * i)
        nodes.append((r, lf, rt))
        if i:
            ptrs.append(k)
    return nodes, ptrs


def wiiu_group_size(n: int) -> int:
    return 8 + 16 * (n + 1)


def wiiu_group(base: int, keys: list[str], name_offs: list[int], data_offs: list[int]) -> bytes:
    """Wii U index group at absolute offset `base`. name_offs/data_offs: absolute targets (0 = none); written as
    offsets relative to their own field."""
    nodes = build([k.encode("utf-8") for k in keys])
    out = bytearray(struct.pack(">Ii", wiiu_group_size(len(keys)), len(keys)))
    for i, (r, lf, rt) in enumerate(nodes):
        at = base + len(out)
        name = 0 if i == 0 else name_offs[i - 1]
        dat = 0 if i == 0 else data_offs[i - 1]
        out += struct.pack(">IHHii", r & 0xFFFFFFFF, lf, rt, name - (at + 8) if name else 0,
                           dat - (at + 12) if dat else 0)
    return bytes(out)
