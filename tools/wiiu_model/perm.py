"""Fast pure-Python byte permutation for the texture tilers (no numpy).

A tiler gives an index list: for each element of the linear image (row-major), the position of that element in the
tiled data, counted in `unit`-byte units. Untiling gathers along that list, tiling scatters along it. An element is
`unit * lanes` bytes (lanes = 2 for 16-byte elements moved as two 8-byte halves).

Measured on this machine (CPython 3.13): a gather of 262144 8-byte units takes ~15 ms with `operator.itemgetter`
(about 130 MB/s), a scatter ~16 ms; building the index list with nested comprehensions ~9 ms.
"""
from __future__ import annotations

from array import array
from operator import itemgetter

from .errors import Damaged

TYPECODE = {1: "B", 2: "H", 4: "I", 8: "Q"}
for _tc, _n in (("H", 2), ("I", 4), ("Q", 8)):
    if array(_tc).itemsize != _n:  # pragma: no cover - CPython on every 64-bit platform has these sizes
        raise ImportError(f"array typecode {_tc} is not {_n} bytes here")


def _units(data, unit: int, min_units: int, what: str) -> array:
    tc = TYPECODE[unit]
    n = len(data)
    if n < min_units * unit:
        raise Damaged(f"{what}: {n} bytes of data, the layout needs {min_units * unit}")
    a = array(tc)
    a.frombytes(data[:n - n % unit] if n % unit else data)
    return a


def _pick(a: array, idx):
    if len(idx) == 1:
        return (a[idx[0]],)
    return itemgetter(*idx)(a)


def gather(src, idx, unit: int, lanes: int = 1, what: str = "texture") -> bytes:
    """Linear bytes from tiled `src`: element i (unit*lanes bytes) comes from unit position idx[i]."""
    if not len(idx):
        return b""
    need = max(idx) + lanes
    tc = TYPECODE[unit]
    a = _units(src, unit, need, what)
    if lanes == 1:
        return array(tc, _pick(a, idx)).tobytes()
    out = array(tc, bytes(len(idx) * lanes * unit))
    for k in range(lanes):
        view = a if k == 0 else a[k:]
        out[k::lanes] = array(tc, _pick(view, idx))
    return out.tobytes()


def scatter(src, idx, unit: int, lanes: int, out_size: int, what: str = "texture") -> bytes:
    """Tiled bytes (out_size, zero padding) from linear `src`: element i goes to unit position idx[i]."""
    tc = TYPECODE[unit]
    if out_size % unit:
        raise ValueError("out_size must be a multiple of the unit")
    out = array(tc, bytes(out_size))
    if not len(idx):
        return out.tobytes()
    if max(idx) + lanes > len(out):
        raise Damaged(f"{what}: layout writes past the end of the {out_size}-byte level")
    s = _units(src, unit, len(idx) * lanes, what)
    if lanes == 1:
        for i, v in zip(idx, s):
            out[i] = v
    elif lanes == 2:
        for i, v0, v1 in zip(idx, s[0::2], s[1::2]):
            out[i] = v0
            out[i + 1] = v1
    else:
        parts = [s[k::lanes] for k in range(lanes)]
        for n, i in enumerate(idx):
            for k in range(lanes):
                out[i + k] = parts[k][n]
    return out.tobytes()
