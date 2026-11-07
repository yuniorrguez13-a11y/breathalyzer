#!/usr/bin/env python3
"""Read and write Switch NSO executables (the format of exefs/main, sdk and subsdk0).

Pure Python, no dependencies. The mod loader uses it to apply ExeFS patches to the player's own `main` before
Savage Wind recompiles it. Nothing here prints, stores or ships game code: `info` shows only the header.

    python tools/nso.py info <nso>
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from dataclasses import dataclass, field

MAGIC = b"NSO0"
HEADER_SIZE = 0x100
SEGMENTS = ("text", "rodata", "data")

# Header flags
FLAG_COMPRESSED = (1 << 0, 1 << 1, 1 << 2)   # text, rodata, data
FLAG_HASHED = (1 << 3, 1 << 4, 1 << 5)

# Header field offsets
_SEG_HEADER = (0x10, 0x20, 0x30)   # file offset, memory offset, size (each u32)
_FILE_SIZE = (0x60, 0x64, 0x68)    # compressed size in the file
_HASH = (0xA0, 0xC0, 0xE0)         # sha256 of the decompressed segment
_NAME_OFF, _NAME_SIZE = 0x1C, 0x2C
_BSS_SIZE = 0x3C
_BUILD_ID = 0x40


class NsoError(ValueError):
    """The file isn't a valid NSO, or an operation on it can't be done."""


# --- LZ4 (block format, as NSO uses it) ---------------------------------------------------------------------------

def lz4_decompress(src: bytes, out_size: int) -> bytes:
    """Decompress one LZ4 block of known output size."""
    dst = bytearray()
    i, n = 0, len(src)
    try:
        while i < n:
            token = src[i]
            i += 1
            lit = token >> 4
            if lit == 15:
                while True:
                    b = src[i]
                    i += 1
                    lit += b
                    if b != 255:
                        break
            if i + lit > n:
                raise NsoError("lz4: literals run past the end of the block")
            dst += src[i:i + lit]
            i += lit
            if i >= n:
                break  # the last sequence has literals only
            off = src[i] | (src[i + 1] << 8)
            i += 2
            if off == 0:
                raise NsoError("lz4: match offset 0")
            ml = token & 15
            if ml == 15:
                while True:
                    b = src[i]
                    i += 1
                    ml += b
                    if b != 255:
                        break
            ml += 4
            start = len(dst) - off
            if start < 0:
                raise NsoError("lz4: match starts before the block")
            if off >= ml:
                dst += dst[start:start + ml]
            else:  # overlapping copy: the match repeats the last `off` bytes
                chunk = bytes(dst[start:])
                dst += (chunk * (ml // off + 1))[:ml]
    except IndexError:
        raise NsoError("lz4: block ends in the middle of a sequence") from None
    if len(dst) != out_size:
        raise NsoError(f"lz4: decompressed {len(dst)} bytes, header says {out_size}")
    return bytes(dst)


def lz4_store(data: bytes) -> bytes:
    """A valid LZ4 block that holds `data` as literals only (no matching).

    Larger than real compression, but any LZ4 reader accepts it, so a patched segment can keep its
    "compressed" flag for loaders that require it.
    """
    n = len(data)
    if n < 15:
        return bytes([n << 4]) + data
    rest = n - 15
    ext = b"\xff" * (rest // 255) + bytes([rest % 255])
    return b"\xf0" + ext + data


# --- NSO -----------------------------------------------------------------------------------------------------------

@dataclass
class Segment:
    name: str
    mem_offset: int
    data: bytes          # decompressed
    compressed: bool     # stored compressed in the original file
    hashed: bool         # the header flag asks the loader to check its hash

    @property
    def end(self) -> int:
        return self.mem_offset + len(self.data)


@dataclass
class Nso:
    header: bytes                       # the original 0x100 bytes
    segments: list[Segment]
    module_name: bytes = b""
    bss_size: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def build_id(self) -> bytes:
        return self.header[_BUILD_ID:_BUILD_ID + 0x20]

    @property
    def build_id_hex(self) -> str:
        return self.build_id.hex()

    def segment(self, name: str) -> Segment:
        for s in self.segments:
            if s.name == name:
                return s
        raise KeyError(name)

    @property
    def image_size(self) -> int:
        return max(s.end for s in self.segments)

    # -- reading / writing guest memory offsets (relative to the module base) --

    def read(self, offset: int, size: int) -> bytes:
        out = bytearray()
        pos, left = offset, size
        while left:
            seg = self._segment_at(pos)
            take = min(left, seg.end - pos)
            rel = pos - seg.mem_offset
            out += seg.data[rel:rel + take]
            pos += take
            left -= take
        return bytes(out)

    def write(self, offset: int, data: bytes) -> None:
        """Write bytes at a module offset. Spans segments if it has to; fails outside them."""
        pos, view = offset, memoryview(data)
        while view:
            seg = self._segment_at(pos)
            take = min(len(view), seg.end - pos)
            rel = pos - seg.mem_offset
            buf = bytearray(seg.data)
            buf[rel:rel + take] = view[:take]
            seg.data = bytes(buf)
            pos += take
            view = view[take:]

    def _segment_at(self, pos: int) -> Segment:
        for s in self.segments:
            if s.mem_offset <= pos < s.end:
                return s
        raise NsoError(f"offset 0x{pos:x} is outside the module's segments")

    # -- serialising --

    def to_bytes(self, compress: bool | None = None) -> bytes:
        """Build the NSO file.

        compress=None keeps each segment's original compressed flag (written as an LZ4 block of literals);
        False stores every segment uncompressed. Segment hashes are recomputed and their flags kept.
        """
        hdr = bytearray(self.header)
        flags = struct.unpack_from("<I", hdr, 0x0C)[0]
        body = bytearray()
        pos = HEADER_SIZE
        name_off = pos
        body += self.module_name
        pos += len(self.module_name)
        for i, seg in enumerate(self.segments):
            use_lz4 = seg.compressed if compress is None else compress
            blob = lz4_store(seg.data) if use_lz4 else seg.data
            if use_lz4:
                flags |= FLAG_COMPRESSED[i]
            else:
                flags &= ~FLAG_COMPRESSED[i]
            struct.pack_into("<III", hdr, _SEG_HEADER[i], pos, seg.mem_offset, len(seg.data))
            struct.pack_into("<I", hdr, _FILE_SIZE[i], len(blob))
            hdr[_HASH[i]:_HASH[i] + 0x20] = hashlib.sha256(seg.data).digest()
            body += blob
            pos += len(blob)
        struct.pack_into("<I", hdr, 0x0C, flags)
        struct.pack_into("<I", hdr, _NAME_OFF, name_off)
        struct.pack_into("<I", hdr, _NAME_SIZE, len(self.module_name))
        return bytes(hdr) + bytes(body)


def parse(data: bytes, *, check_hashes: bool = True) -> Nso:
    if len(data) < HEADER_SIZE or data[:4] != MAGIC:
        raise NsoError("not an NSO file (no NSO0 header)")
    flags = struct.unpack_from("<I", data, 0x0C)[0]
    segments = []
    for i, name in enumerate(SEGMENTS):
        file_off, mem_off, size = struct.unpack_from("<III", data, _SEG_HEADER[i])
        file_size = struct.unpack_from("<I", data, _FILE_SIZE[i])[0]
        compressed = bool(flags & FLAG_COMPRESSED[i])
        stored = file_size if compressed else size
        if file_off + stored > len(data):
            raise NsoError(f"{name}: segment runs past the end of the file")
        raw = data[file_off:file_off + stored]
        seg_data = lz4_decompress(raw, size) if compressed else bytes(raw)
        hashed = bool(flags & FLAG_HASHED[i])
        if check_hashes and hashed:
            want = data[_HASH[i]:_HASH[i] + 0x20]
            if hashlib.sha256(seg_data).digest() != want:
                raise NsoError(f"{name}: segment hash doesn't match the header (damaged file?)")
        segments.append(Segment(name, mem_off, seg_data, compressed, hashed))
    name_off, name_size = struct.unpack_from("<I", data, _NAME_OFF)[0], struct.unpack_from("<I", data, _NAME_SIZE)[0]
    module_name = data[name_off:name_off + name_size] if name_size and name_off + name_size <= len(data) else b""
    bss = struct.unpack_from("<I", data, _BSS_SIZE)[0]
    return Nso(bytes(data[:HEADER_SIZE]), segments, module_name, bss)


def read_build_id(path: str) -> str:
    """The full build id (64 hex digits) from an NSO header, without decompressing anything."""
    with open(path, "rb") as fh:
        head = fh.read(HEADER_SIZE)
    if len(head) < HEADER_SIZE or head[:4] != MAGIC:
        raise NsoError(f"{path}: not an NSO file")
    return head[_BUILD_ID:_BUILD_ID + 0x20].hex()


def read_layout(path: str) -> list[tuple[str, int, int]]:
    """[(segment name, module offset, size)] from the header alone, without decompressing."""
    with open(path, "rb") as fh:
        head = fh.read(HEADER_SIZE)
    if len(head) < HEADER_SIZE or head[:4] != MAGIC:
        raise NsoError(f"{path}: not an NSO file")
    out = []
    for i, name in enumerate(SEGMENTS):
        _, mem_off, size = struct.unpack_from("<III", head, _SEG_HEADER[i])
        out.append((name, mem_off, size))
    return out


def load(path: str, *, check_hashes: bool = True) -> Nso:
    with open(path, "rb") as fh:
        return parse(fh.read(), check_hashes=check_hashes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Show an NSO's header (no code is printed).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info")
    p.add_argument("nso")
    args = ap.parse_args(argv)
    nso = load(args.nso)
    print(f"build id   {nso.build_id_hex}")
    for s in nso.segments:
        state = "compressed" if s.compressed else "stored"
        print(f"{s.name:7}    0x{s.mem_offset:08x}-0x{s.end:08x}  ({len(s.data)} bytes, {state}"
              f"{', hash checked' if s.hashed else ''})")
    print(f"bss        {nso.bss_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
