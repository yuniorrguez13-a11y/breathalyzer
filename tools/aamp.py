#!/usr/bin/env python3
"""Read and write AAMP version 2 parameter files (little-endian, as on the Switch): .bxml, .bmodellist, .baslist,
.bas, .baiprog and the other actor parameter files inside actor packs.

    from aamp import load, dump, PList, PObject, Param, name_hash
    root = load(data)                                  # the root list ("param_root")
    obj = root.objects[name_hash("LinkTarget")]        # keys are crc32 hashes of the names
    obj[name_hash("ModelUser")].value = "Foo"
    data = dump(root)

Layout (our own reading of the format, which several open-source tools document the same way):
header 0x30 bytes ("AAMP", u32 version 2, u32 flags (1 little-endian | 2 UTF-8), u32 file size, u32 pio version,
u32 offset of the root list from the end of the header, u32 list / object / parameter counts, u32 data section size,
u32 string section size, u32 0), then a type name ("xml"), then the root list. A list is 12 bytes (u32 name hash,
u16 offset of its child lists / 4 from the list, u16 count, u16 offset of its objects / 4, u16 count), an object 8
(u32 name hash, u16 offset of its parameters / 4 from the object, u16 count), a parameter 8 (u32 name hash, u32: low
24 bits the offset of its value / 4 from the parameter, high 8 bits the type). Strings live in the string section,
other values in the data section; a buffer's u32 element count sits just before its first element.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

ROOT = 0xA4F6CB6C          # crc32("param_root")

# Parameter types
BOOL, F32, INT, VEC2, VEC3, VEC4, COLOR, STRING32, STRING64, CURVE1, CURVE2, CURVE3, CURVE4 = range(13)
BUFFER_INT, BUFFER_F32, STRING256, QUAT, U32, BUFFER_U32, BUFFER_BINARY, STRING_REF = range(13, 21)
STRING_TYPES = {STRING32: 32, STRING64: 64, STRING256: 256, STRING_REF: None}   # (the longest text, with its 0)
FLOATS = {VEC2: 2, VEC3: 3, VEC4: 4, COLOR: 4, QUAT: 4}
CURVES = {CURVE1: 1, CURVE2: 2, CURVE3: 3, CURVE4: 4}
BUFFERS = {BUFFER_INT: "i", BUFFER_F32: "f", BUFFER_U32: "I", BUFFER_BINARY: "B"}


class AampError(ValueError):
    pass


def name_hash(name: str | int) -> int:
    """The key of a name: its crc32 (an int is taken as a hash already)."""
    return name if isinstance(name, int) else zlib.crc32(name.encode("utf-8")) & 0xFFFFFFFF


@dataclass
class Param:
    type: int
    value: object

    def __eq__(self, other):
        """Equal type and value; floats compare as the 32-bit floats the file holds."""
        if not isinstance(other, Param) or other.type != self.type:
            return False
        a, b = self.value, other.value
        if self.type in CURVES:
            return len(a) == len(b) and all(x[:2] == y[:2] and _same_floats(x[2], y[2]) for x, y in zip(a, b))
        if self.type == F32:
            return _same_floats([a], [b])
        if self.type in FLOATS or self.type == BUFFER_F32:
            return _same_floats(a, b)
        return a == b


def _same_floats(a, b) -> bool:
    try:
        return len(a) == len(b) and all(struct.pack("<f", x) == struct.pack("<f", y) for x, y in zip(a, b))
    except (struct.error, TypeError):
        return False


class PObject(dict):
    """An object: {name hash: Param}, in file order."""

    def get_value(self, name, default=None):
        p = self.get(name_hash(name))
        return default if p is None else p.value

    def set(self, name, type_: int, value) -> None:
        self[name_hash(name)] = Param(type_, value)


@dataclass
class PList:
    lists: dict = field(default_factory=dict)      # {name hash: PList}
    objects: dict = field(default_factory=dict)    # {name hash: PObject}

    def list(self, name) -> "PList | None":
        return self.lists.get(name_hash(name))

    def object(self, name) -> PObject | None:
        return self.objects.get(name_hash(name))


# ------------------------------------------------------------------------------------------------------------- read

def _value(data: bytes, at: int, type_: int, size_limit: int):
    if not 0 <= at <= size_limit:
        raise AampError(f"value offset 0x{at:x} outside the file")
    if type_ == BOOL:
        return struct.unpack_from("<I", data, at)[0] != 0
    if type_ == F32:
        return struct.unpack_from("<f", data, at)[0]
    if type_ == INT:
        return struct.unpack_from("<i", data, at)[0]
    if type_ == U32:
        return struct.unpack_from("<I", data, at)[0]
    if type_ in FLOATS:
        return list(struct.unpack_from(f"<{FLOATS[type_]}f", data, at))
    if type_ in CURVES:
        n = CURVES[type_]
        out = []
        for k in range(n):
            a, b = struct.unpack_from("<II", data, at + k * 0x80)
            out.append((a, b, list(struct.unpack_from("<30f", data, at + k * 0x80 + 8))))
        return out
    if type_ in STRING_TYPES:
        end = data.find(b"\0", at)
        if end < 0:
            raise AampError("a string has no end")
        return data[at:end].decode("utf-8")
    if type_ in BUFFERS:
        count = struct.unpack_from("<I", data, at - 4)[0]
        fmt = BUFFERS[type_]
        if at + count * struct.calcsize(fmt) > len(data):
            raise AampError("a buffer runs past the end of the file")
        if type_ == BUFFER_BINARY:
            return bytes(data[at:at + count])
        return list(struct.unpack_from(f"<{count}{fmt}", data, at))
    raise AampError(f"unknown parameter type {type_}")


def load(data: bytes) -> PList:
    """The root list of an AAMP file. Raises AampError for anything else."""
    if len(data) < 0x34 or data[:4] != b"AAMP":
        raise AampError("not an AAMP file")
    version, flags, size, _pio_version, pio_offset = struct.unpack_from("<IIIII", data, 4)
    if version != 2:
        raise AampError(f"AAMP version {version}, only 2 is supported")
    if not flags & 1:
        raise AampError("big-endian AAMP (Wii U) is not supported")
    if size > len(data):
        raise AampError("the file is cut short")
    depth = [0]
    # Work budget: each list, object and parameter header takes 8 bytes or more of the file, so a file whose lists
    # point at the same children again (read again each time: exponential) runs out of it and is refused.
    budget = [size // 8 + 64]

    def spend(n: int) -> None:
        budget[0] -= n
        if budget[0] < 0:
            raise AampError("the file's lists point at the same entries again and again (damaged or made to hang)")

    def read_list(at: int) -> PList:
        if not 0 <= at <= size - 12:
            raise AampError(f"list offset 0x{at:x} outside the file")
        depth[0] += 1
        if depth[0] > 64:
            raise AampError("lists nested too deep")
        spend(1)
        _h, loff, ln, ooff, on = struct.unpack_from("<IHHHH", data, at)
        out = PList()
        for k in range(ln):
            c = at + loff * 4 + k * 12
            out.lists[struct.unpack_from("<I", data, c)[0]] = read_list(c)
        spend(on)
        for k in range(on):
            o = at + ooff * 4 + k * 8
            oh, poff, pn = struct.unpack_from("<IHH", data, o)
            obj = PObject()
            spend(pn)
            for j in range(pn):
                p = o + poff * 4 + j * 8
                if p + 8 > size:
                    raise AampError("parameter outside the file")
                ph, word = struct.unpack_from("<II", data, p)
                value = _value(data, p + (word & 0xFFFFFF) * 4, word >> 24, size)
                if isinstance(value, (str, bytes, list)):
                    spend(len(value) // 8)          # (texts and buffers read count too)
                obj[ph] = Param(word >> 24, value)
            out.objects[oh] = obj
        depth[0] -= 1
        return out

    root_at = 0x30 + pio_offset
    if struct.unpack_from("<I", data, root_at)[0] != ROOT:
        raise AampError("the root list isn't param_root")
    return read_list(root_at)


def type_name(data: bytes) -> str:
    """The type name after the header ("xml" in the game's files)."""
    end = data.find(b"\0", 0x30)
    return data[0x30:end].decode("ascii", "replace") if end > 0 else ""


# ------------------------------------------------------------------------------------------------------------ write

def _pack_value(p: Param) -> tuple[bytes, bool]:
    """(bytes, is a string) of a parameter's value. Strings are 0-terminated; buffers carry their count first."""
    t, v = p.type, p.value
    if t in STRING_TYPES:
        b = str(v).encode("utf-8")
        cap = STRING_TYPES[t]
        if cap is not None and len(b) >= cap:
            raise AampError(f"text too long for its type ({len(b)} bytes, at most {cap - 1}): {v!r}")
        return b + b"\0", True
    if t == BOOL:
        return struct.pack("<I", 1 if v else 0), False
    if t == F32:
        return struct.pack("<f", v), False
    if t == INT:
        return struct.pack("<i", v), False
    if t == U32:
        return struct.pack("<I", v), False
    if t in FLOATS:
        if len(v) != FLOATS[t]:
            raise AampError("wrong number of floats")
        return struct.pack(f"<{FLOATS[t]}f", *v), False
    if t in CURVES:
        if len(v) != CURVES[t]:
            raise AampError("wrong number of curves")
        return b"".join(struct.pack("<II30f", a, b, *f) for a, b, f in v), False
    if t in BUFFERS:
        fmt = BUFFERS[t]
        body = bytes(v) if t == BUFFER_BINARY else struct.pack(f"<{len(v)}{fmt}", *v)
        return struct.pack("<I", len(v)) + body, False
    raise AampError(f"unknown parameter type {t}")


def _align(b: bytearray, a: int = 4) -> None:
    b += bytes(-len(b) % a)


def dump(root: PList, type_name: str = "xml", pio_version: int = 0) -> bytes:
    """An AAMP file (version 2, little-endian, UTF-8) for this root list. Lists, objects and parameters keep their
    order; each list's child lists, objects and each object's parameters are written next to each other."""
    lists: list[tuple[int, PList]] = [(ROOT, root)]       # breadth first: each list's children sit together
    i = 0
    while i < len(lists):
        lists += list(lists[i][1].lists.items())
        i += 1
    objects: list[tuple[int, PObject]] = [o for _, pl in lists for o in pl.objects.items()]
    params: list[tuple[int, Param]] = [p for _, ob in objects for p in ob.items()]

    head = bytearray(type_name.encode("ascii") + b"\0")
    _align(head)
    list_at = 0x30 + len(head)
    obj_at = list_at + 12 * len(lists)
    param_at = obj_at + 8 * len(objects)
    values_at = param_at + 8 * len(params)

    data, strings = bytearray(), bytearray()
    spots: list[tuple[bool, int]] = []                     # per parameter: (in the string section, offset there)
    seen: dict[tuple[bool, bytes], int] = {}               # the same value is stored once (as the game's files do)
    for _, p in params:
        raw, is_str = _pack_value(p)
        buffer = p.type in BUFFERS
        key = (is_str, raw)
        if not buffer and key in seen:
            spots.append((is_str, seen[key]))
            continue
        if is_str:
            spots.append((True, len(strings)))
            strings += raw
            _align(strings)
        else:
            lead = 4 if buffer else 0                      # (the offset points past a buffer's count)
            spots.append((False, len(data) + lead))
            data += raw
            _align(data)
        if not buffer:
            seen[key] = spots[-1][1]
    strings_at = values_at + len(data)

    def rel(frm: int, to: int, bits: int) -> int:
        d = to - frm
        if d < 0 or d % 4 or d // 4 >= 1 << bits:
            raise AampError("the file is too large for AAMP offsets")
        return d // 4

    out = bytearray()
    child, owned = 1, 0                                    # the next unassigned list and object index
    for k, (h, pl) in enumerate(lists):
        here = list_at + 12 * k
        lo = rel(here, list_at + 12 * child, 16)          # (with no children: where they would start)
        oo = rel(here, obj_at + 8 * owned, 16)
        out += struct.pack("<IHHHH", h, lo, len(pl.lists), oo, len(pl.objects))
        child += len(pl.lists)
        owned += len(pl.objects)
    first = 0
    for k, (h, ob) in enumerate(objects):
        here = obj_at + 8 * k
        po = rel(here, param_at + 8 * first, 16)
        out += struct.pack("<IHH", h, po, len(ob))
        first += len(ob)
    for k, ((h, p), (is_str, off)) in enumerate(zip(params, spots)):
        here = param_at + 8 * k
        target = (strings_at if is_str else values_at) + off
        out += struct.pack("<II", h, rel(here, target, 24) | (p.type << 24))
    body = head + out + data + strings
    header = b"AAMP" + struct.pack("<IIIIIIIIIII", 2, 3, 0x30 + len(body), pio_version, len(head), len(lists),
                                   len(objects), len(params), len(data), len(strings), 0)
    return bytes(header + body)


# ------------------------------------------------------------------------------------------------------------ text

def describe(root: PList, names: dict[int, str] | None = None, indent: int = 0) -> str:
    """A readable outline (for debugging): names where known, else hashes."""
    names = names or {}
    pad = "  " * indent
    out = []
    for h, ob in root.objects.items():
        out.append(f"{pad}{names.get(h, f'0x{h:08x}')} (object)")
        for ph, p in ob.items():
            out.append(f"{pad}  {names.get(ph, f'0x{ph:08x}')} = {p.value!r} (type {p.type})")
    for h, pl in root.lists.items():
        out.append(f"{pad}{names.get(h, f'0x{h:08x}')} (list)")
        out.append(describe(pl, names, indent + 1))
    return "\n".join(x for x in out if x)
