#!/usr/bin/env python3
"""Dragonbreath's model, made in code (a Breathalyzer weapon mod by giruka): an extremely slim, gently curved
katana-like blade with a dragon engraved along it, a round guard with three claw notches, a red-wrapped handle and
Goron-gold fittings. No downloaded or ripped model and no picture from anywhere: every vertex and every texel comes
from the formulas below, the same every time (no random numbers).

    from dragonbreath_model import Shape, make_mesh, paint_textures
    shape = Shape(hilt=-0.213, top=1.051)          # the donor's hilt end and blade tip (tools/dragonbreath_build.py)
    m = make_mesh(shape)                           # an ak47_build.WeaponMesh in the donor's space
    tex = paint_textures(512, 512, 256, 256, 128, 128, shape)   # colour, height -> normal, shine (menu_bg.Picture)

Space (the donor sword's, as tools/ak47_build.py uses it): metres, +Z the blade's direction, +X the edge, Y the flat
side (the blade's faces look along +Y and -Y). The handle runs from `hilt` (below 0) up to the guard at 0; Link holds
the donor there, so he holds ours there too.

The texture atlas (v = 0 is the top row):
  rows 0.00-0.60   the blade in 4 bands (each a quarter of its length, left to right): the main face (edge to ridge,
                   hamon and dragon), the ridge face (a groove) and the back. Both faces of the blade share these
                   texels (the far face mirrored), so the dragon is on both.
  rows 0.62-0.80   the handle wrap (along the handle x around it)
  rows 0.81-0.90   Goron gold (pommel cap, collar)
  rows 0.90-1.00   the guard's faces (a disc map, left 0.1 of the width)
Pure Python; tools/ak47_build.py and tools/menu_bg.py give the mesh type and the pictures.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import ak47_build as ak  # noqa: E402
import menu_bg  # noqa: E402

BANDS = 4                         # the blade's length is split into this many texture bands
BAND_H = 0.15                     # each band's height in the atlas (v)
HIRA, SHINOGI, MUNE = 0.105, 0.030, 0.015   # a band's rows: main face, ridge face, back (sum = BAND_H)
HANDLE_V = (0.62, 0.80)
GOLD_V = (0.81, 0.90)
GUARD_V = (0.90, 1.00)
GUARD_U = 0.10
INSET = 1.5 / 512                 # UVs stay this far inside their region (mipmaps don't bleed into the next one)


@dataclass
class Shape:
    """The blade's numbers (metres). hilt and top come from the donor's model (its lowest and highest z)."""
    hilt: float = -0.213          # the handle's end (the pommel), below 0
    top: float = 1.051            # the blade's tip
    base_width: float = 0.022     # edge to back at the collar
    tip_width: float = 0.015      # edge to back where the tip begins
    thickness: float = 0.006      # at the ridge line
    sori: float = 0.015           # the curve: how far the tip sits toward the back
    tip_len: float = 0.045        # the angled tip (kissaki)
    guard_r: float = 0.036        # the round guard's radius
    guard_z: tuple = (-0.011, -0.002)
    collar_z: tuple = (-0.002, 0.030)
    handle_rx: float = 0.0155     # the handle's half sizes (edge way, flat way)
    handle_ry: float = 0.0125
    cap_len: float = 0.013        # the gold pommel cap
    edge_side: int = 1            # +1: the edge looks along +X; -1: along -X (if the game swings the back first)
    segments: int = 12            # blade segments per band

    @property
    def length(self) -> float:
        return self.top - self.hilt


# ------------------------------------------------------------------------------------------------ the blade's line

def center_x(sh: Shape, z: float) -> float:
    """The blade's middle line: straight at the collar, bending toward the back near the tip (the sori)."""
    t = max(0.0, min(1.0, z / sh.top))
    return -sh.sori * t ** 1.8


def ring(sh: Shape, z: float) -> list[tuple[float, float, float]]:
    """The blade's cross-section at z: edge, ridge (+Y), back (+Y), back (-Y), ridge (-Y). A slim diamond-like
    section: sharp edge, the ridge line about a third of the way from the back, a narrow flat back."""
    t = max(0.0, min(1.0, z / sh.top))
    w = sh.base_width + (sh.tip_width - sh.base_width) * t
    thick = sh.thickness * (1.0 - 0.35 * t)
    tip0 = sh.top - sh.tip_len
    xc = center_x(sh, z)
    back = xc - w / 2
    edge = xc + w / 2
    if z > tip0:   # the tip: the edge curves up to meet the back, the blade thins to a point
        s = min(1.0, (z - tip0) / sh.tip_len)
        k = math.sqrt(max(0.0, 1.0 - s * s))
        edge = back + w * k
        thick *= max(0.0, 1.0 - s ** 1.5)
        w = edge - back
    ridge = back + 0.38 * w
    e = sh.edge_side
    pts = [(edge, 0.0), (ridge, thick / 2), (back, 0.3 * thick / 2), (back, -0.3 * thick / 2), (ridge, -thick / 2)]
    return [(e * x, y, z) for x, y in pts]


# ------------------------------------------------------------------------------------------------ the mesh

class _Builder:
    def __init__(self):
        self.pos: list = []
        self.uv: list = []
        self.idx: list = []
        self.inside: list = []    # per triangle: a point inside the part (for the outward winding)

    def v(self, p, uv) -> int:
        self.pos.append(tuple(p))
        self.uv.append(tuple(uv))
        return len(self.pos) - 1

    def tri(self, a: int, b: int, c: int, inside) -> None:
        """A triangle (one with no area, where the tip's ring closes to a point, is left out)."""
        P = self.pos
        n = ak._cross(ak._sub(P[b], P[a]), ak._sub(P[c], P[a]))
        if ak._dot(n, n) < 1e-20:
            return
        self.idx += [a, b, c]
        self.inside.append(tuple(inside))

    def quad(self, a, b, c, d, inside) -> None:
        self.tri(a, b, c, inside)
        self.tri(a, c, d, inside)

    def finish(self) -> "ak.WeaponMesh":
        """Every triangle turned to face away from its part's inside (front faces counter-clockwise, as glTF and the
        AK-47's model), normals summed per vertex (parts and facets have their own vertices, so edges stay crisp)."""
        P, idx = self.pos, self.idx
        for t, ins in enumerate(self.inside):
            a, b, c = idx[3 * t:3 * t + 3]
            n = ak._cross(ak._sub(P[b], P[a]), ak._sub(P[c], P[a]))
            cen = tuple((P[a][k] + P[b][k] + P[c][k]) / 3 for k in range(3))
            if ak._dot(n, ak._sub(cen, ins)) < 0:
                idx[3 * t + 1], idx[3 * t + 2] = c, b
        acc = [[0.0, 0.0, 0.0] for _ in P]
        for t in range(0, len(idx), 3):
            a, b, c = idx[t:t + 3]
            fn = ak._cross(ak._sub(P[b], P[a]), ak._sub(P[c], P[a]))
            for i in (a, b, c):
                for k in range(3):
                    acc[i][k] += fn[k]
        nrm = [ak._norm(tuple(n), (0.0, 1.0, 0.0)) for n in acc]
        m = ak.WeaponMesh(pos=list(P), nrm=nrm, uv=list(self.uv), idx=list(idx), slots=[], notes=[],
                          raw_uv=list(self.uv), slot_ix=[0] * len(P))
        m.tan = ak.tangents(m)
        return m


def _clampuv(u: float, v: float, region: tuple) -> tuple[float, float]:
    """(u, v) in 0..1 of a region (u0, v0, u1, v1) -> atlas UV, kept INSET inside it."""
    u0, v0, u1, v1 = region
    return (u0 + INSET + max(0.0, min(1.0, u)) * (u1 - u0 - 2 * INSET),
            v0 + INSET + max(0.0, min(1.0, v)) * (v1 - v0 - 2 * INSET))


def band_regions(k: int) -> dict[str, tuple]:
    """Band k's three texture regions (u0, v0, u1, v1)."""
    v = k * BAND_H
    return {"hira": (0.0, v, 1.0, v + HIRA), "shinogi": (0.0, v + HIRA, 1.0, v + HIRA + SHINOGI),
            "mune": (0.0, v + HIRA + SHINOGI, 1.0, v + BAND_H)}


def _blade(b: _Builder, sh: Shape) -> None:
    """4 bands x `segments` rings; each facet its own vertices. Facets (ring point i -> i+1): 0 main face (+Y),
    1 ridge face (+Y), 2 back, 3 ridge face (-Y), 4 main face (-Y). Both sides map onto the same texels; on the
    far side v runs the other way round, so the picture is mirrored, not cut."""
    facets = [(0, 1, "hira", False), (1, 2, "shinogi", False), (2, 3, "mune", False), (3, 4, "shinogi", True),
              (4, 0, "hira", True)]
    for k in range(BANDS):
        reg = band_regions(k)
        z0, z1 = sh.top * k / BANDS, sh.top * (k + 1) / BANDS
        zs = [z0 + (z1 - z0) * j / sh.segments for j in range(sh.segments + 1)]
        rings = [ring(sh, z) for z in zs]
        for i0, i1, name, far in facets:
            rows = []
            for j, z in enumerate(zs):
                u = j / sh.segments
                a, c = rings[j][i0], rings[j][i1]
                va, vc = (0.0, 1.0) if not far else (1.0, 0.0)
                rows.append((b.v(a, _clampuv(u, va, reg[name])), b.v(c, _clampuv(u, vc, reg[name]))))
            for j in range(sh.segments):
                z = (zs[j] + zs[j + 1]) / 2
                ins = (sh.edge_side * center_x(sh, z), 0.0, z)
                (a0, c0), (a1, c1) = rows[j], rows[j + 1]
                b.quad(a0, a1, c1, c0, ins)


def _ellipse_tube(b: _Builder, z0: float, z1: float, rx: float, ry: float, n_around: int, n_along: int,
                  region: tuple, caps: tuple[bool, bool] = (False, False), u_scale: float = 1.0) -> None:
    """An elliptical tube along Z (u along it, v around it), with optional flat caps at z0 / z1."""
    grid = []
    for j in range(n_along + 1):
        z = z0 + (z1 - z0) * j / n_along
        row = []
        for i in range(n_around + 1):
            a = 2 * math.pi * i / n_around
            row.append(b.v((rx * math.cos(a), ry * math.sin(a), z), _clampuv(j / n_along * u_scale, i / n_around,
                                                                              region)))
        grid.append(row)
    zc = (z0 + z1) / 2
    for j in range(n_along):
        z = (z0 + (z1 - z0) * (j + 0.5) / n_along)
        for i in range(n_around):
            b.quad(grid[j][i], grid[j + 1][i], grid[j + 1][i + 1], grid[j][i + 1], (0.0, 0.0, z))
    for end, z in ((caps[0], z0), (caps[1], z1)):
        if not end:
            continue
        mid = b.v((0.0, 0.0, z), _clampuv(0.5, 0.5, region))
        ring_ = [b.v((rx * math.cos(2 * math.pi * i / n_around), ry * math.sin(2 * math.pi * i / n_around), z),
                     _clampuv(0.5 + 0.5 * math.cos(2 * math.pi * i / n_around),
                              0.5 + 0.5 * math.sin(2 * math.pi * i / n_around), region)) for i in range(n_around)]
        for i in range(n_around):
            b.tri(mid, ring_[i], ring_[(i + 1) % n_around], (0.0, 0.0, zc))


def guard_radius(sh: Shape, a: float) -> float:
    """The guard's outline: round, with three claw notches (at 90, 210 and 330 degrees)."""
    r = sh.guard_r
    for c in (math.pi / 2, math.pi * 7 / 6, math.pi * 11 / 6):
        d = math.atan2(math.sin(a - c), math.cos(a - c))
        r -= sh.guard_r * 0.22 * math.exp(-(d / 0.16) ** 2)
    return r


def _guard(b: _Builder, sh: Shape, n: int = 36) -> None:
    z0, z1 = sh.guard_z
    zc = (z0 + z1) / 2
    region = (0.0, GUARD_V[0], GUARD_U, GUARD_V[1])
    outline = [(guard_radius(sh, 2 * math.pi * i / n), 2 * math.pi * i / n) for i in range(n)]
    for z in (z0, z1):                    # the two faces: a fan, disc-mapped
        mid = b.v((0.0, 0.0, z), _clampuv(0.5, 0.5, region))
        rim = [b.v((r * math.cos(a), r * math.sin(a), z),
                   _clampuv(0.5 + 0.5 * r / sh.guard_r * math.cos(a), 0.5 + 0.5 * r / sh.guard_r * math.sin(a),
                            region)) for r, a in outline]
        for i in range(n):
            b.tri(mid, rim[i], rim[(i + 1) % n], (0.0, 0.0, zc))
    gold = (0.0, GOLD_V[0], 1.0, GOLD_V[1])
    for i in range(n):                    # the rim: gold
        (r0, a0), (r1, a1) = outline[i], outline[(i + 1) % n]
        p = [(r0 * math.cos(a0), r0 * math.sin(a0), z0), (r1 * math.cos(a1), r1 * math.sin(a1), z0),
             (r1 * math.cos(a1), r1 * math.sin(a1), z1), (r0 * math.cos(a0), r0 * math.sin(a0), z1)]
        q = [b.v(p[0], _clampuv(i / n, 0.0, gold)), b.v(p[1], _clampuv((i + 1) / n, 0.0, gold)),
             b.v(p[2], _clampuv((i + 1) / n, 1.0, gold)), b.v(p[3], _clampuv(i / n, 1.0, gold))]
        b.quad(q[0], q[1], q[2], q[3], (0.0, 0.0, zc))


def _collar(b: _Builder, sh: Shape) -> None:
    """The gold collar (habaki): the blade's section, 1.6 mm bigger all round, a closed sleeve."""
    z0, z1 = sh.collar_z
    gold = (0.0, GOLD_V[0], 1.0, GOLD_V[1])
    rs = []
    for z in (z0, z1):
        r = ring(sh, max(0.0, z))
        cx = sum(p[0] for p in r) / len(r)
        rs.append([(cx + (p[0] - cx) * 1.15 + math.copysign(0.0016, p[0] - cx), p[1] * 1.15 +
                    math.copysign(0.0016, p[1]) if p[1] else 0.0, z) for p in r])
    cen = [(sum(p[0] for p in r) / 5, 0.0, r[0][2]) for r in rs]
    n = len(rs[0])
    for i in range(n):
        a0, a1 = rs[0][i], rs[0][(i + 1) % n]
        c0, c1 = rs[1][i], rs[1][(i + 1) % n]
        q = [b.v(a0, _clampuv(i / n, 0.0, gold)), b.v(a1, _clampuv((i + 1) / n, 0.0, gold)),
             b.v(c1, _clampuv((i + 1) / n, 1.0, gold)), b.v(c0, _clampuv(i / n, 1.0, gold))]
        b.quad(q[0], q[1], q[2], q[3], ((cen[0][0] + cen[1][0]) / 2, 0.0, (z0 + z1) / 2))
    for r, c in zip(rs, cen):             # its two ends
        mid = b.v(c, _clampuv(0.5, 0.5, gold))
        vs = [b.v(p, _clampuv(0.5, 0.5, gold)) for p in r]
        for i in range(n):
            b.tri(mid, vs[i], vs[(i + 1) % n], (c[0], 0.0, (z0 + z1) / 2))


def make_mesh(sh: Shape) -> "ak.WeaponMesh":
    """The whole weapon as one mesh (one shape, one material: the donor's)."""
    if not (sh.hilt < -0.05 and sh.top > 0.3):
        raise ValueError(f"the donor's hilt ({sh.hilt:.3f}) and tip ({sh.top:.3f}) don't look like a sword's")
    b = _Builder()
    _blade(b, sh)
    _collar(b, sh)
    _guard(b, sh)
    handle = (0.0, HANDLE_V[0], 1.0, HANDLE_V[1])
    gold = (0.0, GOLD_V[0], 1.0, GOLD_V[1])
    _ellipse_tube(b, sh.hilt + sh.cap_len, sh.guard_z[0], sh.handle_rx, sh.handle_ry, 12, 16, handle)
    _ellipse_tube(b, sh.hilt, sh.hilt + sh.cap_len, sh.handle_rx * 1.1, sh.handle_ry * 1.1, 12, 1, gold,
                  caps=(True, False))
    return b.finish()


# ------------------------------------------------------------------------------------------------ the pictures

STEEL_EDGE = (234, 238, 244)      # the hardened edge (below the hamon): bright, blue-white
STEEL = (188, 196, 210)           # the polished flat
STEEL_DARK = (150, 158, 172)      # the ridge face, burnished
GROOVE = (70, 76, 90)            # engraved lines
WRAP = (128, 26, 30)              # dark red cord
SAME = (26, 22, 24)               # black under the wrap
GOLD = (214, 170, 70)             # Goron gold
IRON = (60, 56, 54)               # the guard's face


def _mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(a[k] + (b[k] - a[k]) * t for k in range(3))


def dragon(s: float, y: float) -> float:
    """1 where the dragon's engraving is (a groove), else 0. s: mm along the blade from the collar, y: 0 (edge) to 1
    (ridge) across the main face. Our own drawing from formulas: a long serpent body (a sum of sine waves) from the
    collar to two thirds up, scales as small chevrons, spines along its back, the head toward the tip, flame curls
    in front of it, the tail ending in a curl."""
    s0, h0 = 40.0, 690.0                       # the tail's tip and where the head starts (mm)
    if s < s0 - 12 or s > h0 + 95:
        return 0.0
    env = min(1.0, max(0.0, (s - s0) / 120.0))
    yc = 0.62 + 0.10 * env * math.sin(2 * math.pi * s / 170.0) + 0.02 * math.sin(2 * math.pi * s / 61.0 + 1.1)
    if s < s0:                                 # the tail's curl: a small ring
        d = math.hypot((s - (s0 - 6)) / 6.0, (y - 0.60) / 0.09)
        return 1.0 if abs(d - 1.0) < 0.22 else 0.0
    if s <= h0:
        r = 0.07 + 0.11 * env
        if s > h0 - 40:                        # the neck narrows a little
            r *= 0.85 + 0.15 * abs((s - (h0 - 20)) / 20.0)
        d = abs(y - yc)
        if d > r:                              # the spines along its back (the ridge side)
            ph = (s / 11.0) % 1.0
            return 1.0 if (y > yc and s > s0 + 30 and d - r < 0.08 * (1.0 - ph)
                           and abs(d - r - 0.08 * (1.0 - ph)) < 0.03) else 0.0
        if r - d < 0.035:                      # its outline
            return 1.0
        a = s / 4.0 - d / 0.18 * 1.6           # the scales: chevrons pointing toward the head
        return 1.0 if a % 1.0 < 0.24 else 0.0
    yh = 0.62 + 0.10 * math.sin(2 * math.pi * h0 / 170.0) + 0.02 * math.sin(2 * math.pi * h0 / 61.0 + 1.1)
    q = s - h0
    if q <= 38.0:                              # the head: a wedge, its jaw, an eye, two horns swept back
        r = 0.24 * (1.0 - (q / 38.0) ** 1.4) + 0.03
        d = abs(y - yh)
        if d <= r and r - d < 0.04:
            return 1.0
        if q > 14 and abs(y - yh) < 0.022 + 0.03 * (q - 14) / 24:   # the open jaw
            return 1.0
        if math.hypot((q - 9.0) / 2.6, (y - (yh + 0.09)) / 0.045) < 1.0:
            return 1.0
        return 0.0
    if q <= 90.0:                              # flame curls in front of the jaw
        k = (q - 38.0) / 52.0
        spread = 0.24 * (1.0 - k) + 0.03
        if abs(y - yh) > spread:
            return 0.0
        return 1.0 if abs(math.sin(q / 4.0 + (y - yh) * 16.0 + 3.0 * k * k)) < 0.26 else 0.0
    return 0.0


def _horns(s: float, y: float) -> float:
    """The two horns: lines from the head back along the body (mm, 0..1 across)."""
    h0 = 690.0
    yh = 0.62 + 0.10 * math.sin(2 * math.pi * h0 / 170.0) + 0.02 * math.sin(2 * math.pi * h0 / 61.0 + 1.1)
    if not h0 - 22 <= s <= h0 + 8:
        return 0.0
    k = (h0 + 8 - s) / 30.0
    for side in (1, -1):
        if abs(y - (yh + side * (0.16 + 0.10 * k))) < 0.026:
            return 1.0
    return 0.0


def hamon(s: float) -> float:
    """The temper line across the main face (0 edge .. 1 ridge): a gentle wave."""
    return 0.30 + 0.05 * math.sin(s / 9.0) + 0.025 * math.sin(s / 3.7 + 1.3)


def texel(u: float, v: float, sh: Shape) -> tuple[tuple, float, float]:
    """(colour RGB, height 0..1 (0 engraved, 0.5 flat, 1 raised), shine 0..1) at atlas (u, v)."""
    if v < BANDS * BAND_H:
        k = min(BANDS - 1, int(v / BAND_H))
        w = v - k * BAND_H
        s = (k + u) / BANDS * sh.top * 1000.0          # mm along the blade
        grain = ak._noise(s / 6.0, w * 300.0, 7) * 0.5 + ak._noise(s / 1.3, w * 900.0, 11) * 0.5
        if w < HIRA:
            y = w / HIRA
            hy = hamon(s)
            c = _mix(STEEL_EDGE, STEEL, (y - hy) / 0.05 + 0.5)
            c = tuple(x * (0.96 + 0.06 * grain) for x in c)
            g = max(dragon(s, y), _horns(s, y))
            if g:
                return _mix(c, GROOVE, 0.85), 0.0, 0.3
            return c, 0.5, 1.0 if y < hy else 0.85
        if w < HIRA + SHINOGI:
            y = (w - HIRA) / SHINOGI
            c = tuple(x * (0.95 + 0.07 * grain) for x in STEEL_DARK)
            if abs(y - 0.5) < 0.22 and s < sh.top * 1000.0 * 0.82:      # the groove (a fuller) in the ridge face
                return _mix(c, GROOVE, 0.55), 0.15, 0.6
            return c, 0.5, 0.8
        return tuple(x * (0.97 + 0.04 * grain) for x in STEEL_DARK), 0.5, 0.7
    if HANDLE_V[0] <= v < HANDLE_V[1] + 0.01:
        a = (v - HANDLE_V[0]) / (HANDLE_V[1] - HANDLE_V[0])     # around
        along = u * (sh.guard_z[0] - sh.hilt - sh.cap_len) * 1000.0   # mm along the handle
        p = (along / 13.0 + a * 4.0) % 1.0
        q = (along / 13.0 - a * 4.0) % 1.0
        dp, dq = abs(p - 0.5), abs(q - 0.5)
        cord = min(dp, dq)
        if cord < 0.17:
            lit = 1.0 - (cord / 0.17) ** 2 * 0.45
            twist = 0.9 + 0.1 * math.sin((p + q) * 40.0)
            return tuple(x * lit * twist for x in WRAP), 0.5 + 0.5 * lit, 0.25
        n = ak._noise(u * 400.0, v * 900.0, 3)
        return tuple(x * (0.8 + 0.5 * n) for x in SAME), 0.3, 0.15
    if GOLD_V[0] - 0.01 <= v < GOLD_V[1]:
        n = ak._noise(u * 160.0, v * 40.0, 5)
        line = 0.82 if (u * 48.0) % 1.0 < 0.08 else 1.0
        return tuple(x * (0.9 + 0.15 * n) * line for x in GOLD), 0.5 if line == 1.0 else 0.35, 0.8
    if u < GUARD_U + 0.01 and v >= GUARD_V[0] - 0.01:       # the guard's faces: iron with a gold rim and rings
        x = (u / GUARD_U) * 2 - 1
        y = ((v - GUARD_V[0]) / (GUARD_V[1] - GUARD_V[0])) * 2 - 1
        r = math.hypot(x, y)
        if r > 0.86:
            return GOLD, 0.6, 0.8
        ringed = 0.85 if abs((r * 7.0) % 1.0 - 0.5) < 0.08 else 1.0
        return tuple(c * ringed for c in IRON), 0.5 if ringed == 1.0 else 0.35, 0.45
    return STEEL_DARK, 0.5, 0.6


def picture(w: int, h: int, fn) -> menu_bg.Picture:
    """A w x h opaque picture; fn(u, v) -> RGB at each texel's centre."""
    px = bytearray(w * h * 4)
    for yy in range(h):
        v = (yy + 0.5) / h
        o = yy * w * 4
        for xx in range(w):
            r, g, b = fn((xx + 0.5) / w, v)
            px[o:o + 4] = bytes((max(0, min(255, round(r))), max(0, min(255, round(g))), max(0, min(255, round(b))),
                                 255))
            o += 4
    return menu_bg.Picture(w, h, px)


def height_map(w: int, h: int, sh: Shape) -> list[list[float]]:
    return [[texel((x + 0.5) / w, (y + 0.5) / h, sh)[1] for x in range(w)] for y in range(h)]


def normal_rg(hm: list[list[float]], strength: float = 2.5) -> list[list[tuple[int, int]]]:
    """Tangent-space normals (as BC5's two channels, 128 = flat) from a height map: engraved lines sink in."""
    h, w = len(hm), len(hm[0])
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            dx = hm[y][min(w - 1, x + 1)] - hm[y][max(0, x - 1)]
            dy = hm[min(h - 1, y + 1)][x] - hm[max(0, y - 1)][x]
            nx, ny, nz = -dx * strength, -dy * strength, 1.0
            ln = math.sqrt(nx * nx + ny * ny + nz * nz)
            row.append((max(0, min(255, round((nx / ln * 0.5 + 0.5) * 255))),
                        max(0, min(255, round((ny / ln * 0.5 + 0.5) * 255)))))
        out.append(row)
    return out


def shine_map(w: int, h: int, sh: Shape) -> list[list[float]]:
    return [[texel((x + 0.5) / w, (y + 0.5) / h, sh)[2] for x in range(w)] for y in range(h)]


@dataclass
class Painted:
    albedo: menu_bg.Picture
    normal: list          # rows of (r, g)
    shine: list           # rows of 0..1


def paint_textures(aw: int, ah: int, nw: int, nh: int, sw: int, sh_: int, shape: Shape) -> Painted:
    """The three pictures at the donor's sizes: colour (aw x ah), normal (nw x nh), shine (sw x sh_)."""
    alb = picture(aw, ah, lambda u, v: texel(u, v, shape)[0])
    nrm = normal_rg(height_map(nw, nh, shape))
    return Painted(alb, nrm, shine_map(sw, sh_, shape))


# ------------------------------------------------------------------------------------------------ the preview

def render(m: "ak.WeaponMesh", alb: menu_bg.Picture, w: int, h: int, view: str, bg=(36, 38, 44),
           margin: float = 0.04) -> menu_bg.Picture:
    """An orthographic picture of the mesh: view "side" (looking at the blade's face, tip to the right) or "edge"
    (looking at the edge). Textured, lit from the top left."""
    if view == "side":
        scr = [(p[2], -p[0], p[1]) for p in m.pos]          # right = +Z, down = -X, toward the viewer = +Y
        facing = 1
    else:
        scr = [(p[2], -p[1], p[0] * m_edge_sign(m)) for p in m.pos]
        facing = 0
    xs, ys = [s[0] for s in scr], [s[1] for s in scr]
    k = min(w * (1 - 2 * margin) / ((max(xs) - min(xs)) or 1), h * (1 - 2 * margin) / ((max(ys) - min(ys)) or 1))
    ox = w / 2 - (max(xs) + min(xs)) / 2 * k
    oy = h / 2 - (max(ys) + min(ys)) / 2 * k
    pts = [(s[0] * k + ox, s[1] * k + oy, s[2]) for s in scr]
    light = ak._norm((-0.4, 0.5, 0.75))
    color = bytearray(bytes((*bg, 255)) * (w * h))
    depth = [-1e30] * (w * h)
    tw, th, tpx = alb.w, alb.h, alb.px
    for t in range(0, len(m.idx), 3):
        a, b, c = m.idx[t:t + 3]
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = pts[a], pts[b], pts[c]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        if abs(area) < 1e-9:
            continue
        n = ak._norm(tuple(m.nrm[a][q] + m.nrm[b][q] + m.nrm[c][q] for q in range(3)))
        nv = (n[2], -n[0], n[1]) if facing else (n[2], -n[1], n[0] * m_edge_sign(m))
        if nv[2] < -0.05:
            continue
        shade = 0.45 + 0.65 * max(0.0, ak._dot(nv, light))
        bx0, bx1 = max(0, int(min(x0, x1, x2))), min(w - 1, int(max(x0, x1, x2)) + 1)
        by0, by1 = max(0, int(min(y0, y1, y2))), min(h - 1, int(max(y0, y1, y2)) + 1)
        (u0, v0), (u1, v1), (u2, v2) = m.uv[a], m.uv[b], m.uv[c]
        inv = 1.0 / area
        for py in range(by0, by1 + 1):
            cy = py + 0.5
            for px_ in range(bx0, bx1 + 1):
                cx = px_ + 0.5
                w0 = ((x1 - cx) * (y2 - cy) - (x2 - cx) * (y1 - cy)) * inv
                w1 = ((x2 - cx) * (y0 - cy) - (x0 - cx) * (y2 - cy)) * inv
                w2 = 1.0 - w0 - w1
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                z = w0 * z0 + w1 * z1 + w2 * z2
                o = py * w + px_
                if z <= depth[o]:
                    continue
                depth[o] = z
                u = w0 * u0 + w1 * u1 + w2 * u2
                v = w0 * v0 + w1 * v1 + w2 * v2
                ti = (min(th - 1, max(0, int(v * th))) * tw + min(tw - 1, max(0, int(u * tw)))) * 4
                q = o * 4
                color[q] = min(255, int(tpx[ti] * shade))
                color[q + 1] = min(255, int(tpx[ti + 1] * shade))
                color[q + 2] = min(255, int(tpx[ti + 2] * shade))
    return menu_bg.Picture(w, h, color)


def m_edge_sign(m) -> float:
    """+1 when the edge looks along +X (the edge view shows it toward the viewer)."""
    return 1.0 if max(p[0] for p in m.pos) >= -min(p[0] for p in m.pos) else -1.0


def preview(shape: Shape, width: int = 1600, scale: int = 2) -> menu_bg.Picture:
    """A picture for the user to judge the look: the blade's face (whole weapon), the edge view below it, and a
    close-up of the dragon's head, on one sheet. No game file needed."""
    m = make_mesh(shape)
    tex = paint_textures(512 * scale, 512 * scale, 8, 8, 8, 8, shape)
    side = render(m, tex.albedo, width, width // 8, "side")
    edge = render(m, tex.albedo, width, width // 16, "edge")
    # the dragon's head: the atlas region around it, enlarged (texture space: blade band 3)
    k = int((690.0 / (shape.top * 1000.0)) * BANDS)
    s_in_band = (690.0 / (shape.top * 1000.0)) * BANDS - k
    x0 = max(0.0, s_in_band - 0.12) * tex.albedo.w
    x1 = min(1.0, s_in_band + 0.30) * tex.albedo.w
    y0 = k * BAND_H * tex.albedo.h
    y1 = (k * BAND_H + HIRA) * tex.albedo.h
    mm_long = (x1 - x0) / tex.albedo.w / BANDS * shape.top * 1000.0     # how much blade the close-up shows
    mm_wide = 0.62 * (shape.base_width + shape.tip_width) / 2 * 1000.0  # about the main face's width there
    close = menu_bg.resample(tex.albedo, (x0, y0, x1, y1), width, max(8, int(width * mm_wide / mm_long * 2)))
    h = side.h + edge.h + close.h
    out = bytearray(bytes((36, 38, 44, 255)) * (width * h))
    y = 0
    for pic in (side, edge, close):
        out[y * width * 4:(y + pic.h) * width * 4] = pic.px
        y += pic.h
    return menu_bg.Picture(width, h, out)
