"""String pools. Switch (`_STR`): u16 length, bytes, NUL, pad to 2; the empty string first; a pointer points at the
length. Wii U: u32 length, bytes, NUL, pad to 4; sorted; an offset points at the characters."""
from __future__ import annotations

import struct

from .errors import Damaged


def nx_pool(strings: list[str]) -> tuple[bytes, dict[str, int]]:
    """Body of a Switch `_STR` block after its 16-byte header: u32 count, the empty string, then `strings` (unique, in
    the order given). Returns (bytes, {string: offset of its u16 length, relative to the body start})."""
    out = bytearray(struct.pack("<I", 0))
    offs: dict[str, int] = {"": 4}
    out += b"\0\0\0\0"
    for s in strings:
        if s in offs:
            continue
        b = s.encode("utf-8")
        offs[s] = len(out)
        out += struct.pack("<H", len(b)) + b + b"\0"
        if len(out) % 2:
            out += b"\0"
    struct.pack_into("<I", out, 0, len(offs) - 1)
    return bytes(out), offs


def nx_string(data, off: int, what: str = "string") -> str:
    """Switch string at `off` (points at the u16 length)."""
    if off < 0 or off + 2 > len(data):
        raise Damaged(f"{what} at {off:#x} is outside the file")
    n = struct.unpack_from("<H", data, off)[0]
    if off + 2 + n > len(data):
        raise Damaged(f"{what} at {off:#x} runs past the end of the file")
    return bytes(data[off + 2:off + 2 + n]).decode("utf-8", "replace")


def wiiu_pool(strings, base: int) -> tuple[bytes, dict[str, int]]:
    """Wii U string pool at absolute offset `base` (4-aligned): sorted unique strings. Returns (bytes, {string:
    absolute offset of its characters})."""
    out = bytearray()
    offs: dict[str, int] = {}
    for s in sorted(set(strings), key=lambda x: x.encode("utf-8")):
        b = s.encode("utf-8")
        out += struct.pack(">I", len(b))
        offs[s] = base + len(out)
        out += b + b"\0"
        while len(out) % 4:
            out += b"\0"
    return bytes(out), offs
