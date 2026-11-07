"""Endian-aware reading with bounds checks (and an optional coverage map), plus small writing helpers."""
from __future__ import annotations

import struct
from functools import lru_cache

from .errors import Damaged


@lru_cache(maxsize=256)
def S(fmt: str) -> struct.Struct:
    return struct.Struct(fmt)


def align(n: int, a: int) -> int:
    return -(-n // a) * a


class Reader:
    """Reads numbers from `data` in byte order `order` ('<' or '>'). Offsets are absolute in `data`.
    `cover` (optional) records every range read, for finding unread bytes."""

    def __init__(self, data, order: str = "<", what: str = "file", cover: bool = False):
        self.data = data if isinstance(data, (bytes, bytearray)) else bytes(data)
        self.mv = memoryview(self.data)
        self.order = order
        self.what = what
        self.size = len(self.data)
        self.cover: list[tuple[int, int]] | None = [] if cover else None

    def need(self, off: int, n: int, what: str = "") -> None:
        if off < 0 or n < 0 or off + n > self.size:
            raise Damaged(f"{self.what}: {what or 'data'} at {off:#x} (+{n:#x}) is outside the file ({self.size:#x})")
        if self.cover is not None and n:
            self.cover.append((off, off + n))

    def unpack(self, fmt: str, off: int, what: str = ""):
        s = S(self.order + fmt)
        self.need(off, s.size, what)
        return s.unpack_from(self.data, off)

    def u8(self, off: int, what: str = "") -> int:
        self.need(off, 1, what)
        return self.data[off]

    def u16(self, off: int, what: str = "") -> int:
        return self.unpack("H", off, what)[0]

    def s16(self, off: int, what: str = "") -> int:
        return self.unpack("h", off, what)[0]

    def u32(self, off: int, what: str = "") -> int:
        return self.unpack("I", off, what)[0]

    def s32(self, off: int, what: str = "") -> int:
        return self.unpack("i", off, what)[0]

    def u64(self, off: int, what: str = "") -> int:
        return self.unpack("Q", off, what)[0]

    def raw(self, off: int, n: int, what: str = "") -> bytes:
        self.need(off, n, what)
        return self.data[off:off + n]

    def view(self, off: int, n: int, what: str = "") -> memoryview:
        self.need(off, n, what)
        return self.mv[off:off + n]

    def cstr(self, off: int, what: str = "", limit: int = 4096) -> str:
        """NUL-terminated string at `off`."""
        if not 0 <= off < self.size:
            raise Damaged(f"{self.what}: {what or 'string'} at {off:#x} is outside the file")
        end = self.data.find(b"\0", off, min(self.size, off + limit))
        if end < 0:
            raise Damaged(f"{self.what}: {what or 'string'} at {off:#x} has no end")
        if self.cover is not None:
            self.cover.append((off, end + 1))
        return self.data[off:end].decode("utf-8", "replace")

    def uncovered(self, start: int = 0, end: int | None = None) -> list[tuple[int, int]]:
        """Byte ranges in [start, end) that were never read and are not all zero."""
        end = self.size if end is None else end
        spans = sorted(self.cover or [])
        out, pos = [], start
        for a, b in spans + [(end, end)]:
            if a > pos:
                gap = self.data[pos:min(a, end)]
                if gap.strip(b"\0"):
                    out.append((pos, min(a, end)))
            pos = max(pos, b)
            if pos >= end:
                break
        return out


def put(buf: bytearray, fmt: str, off: int, *values) -> None:
    s = S(fmt)
    if off + s.size > len(buf):
        buf.extend(bytes(off + s.size - len(buf)))
    s.pack_into(buf, off, *values)


def pad_to(buf: bytearray, n: int) -> None:
    if len(buf) < n:
        buf.extend(bytes(n - len(buf)))
