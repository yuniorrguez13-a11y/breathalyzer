"""Texture verification on the user's own Switch files (milestone M1). Read-only on the game; small samples.

V1: Switch X.Tex -> neutral -> Switch: byte-identical, self-check, same structure and blocks.
V2: Switch X.Tex -> neutral -> Wii U Tex1/Tex2 -> neutral -> Switch: every mip's linear blocks identical, names /
    formats / channels / mips / swizzle hint equal, Wii U self-check passes, Switch self-check passes; the output is
    byte-compared with the Switch writer's own output for the original set without Switch-only extras.
Speed: GX2 and Tegra tile/untile on made-up 2048x2048 BC1 and 1024x1024 BC3 mip chains (cold = index cache cleared).
"""
from __future__ import annotations

import copy
import json
import os
import random
import sys
import time

from . import checks, formats, gx2, nx_bfres, tegra, wiiu_bfres
from .errors import ModelError
from .neutral import Texture, TextureSet, diff

ICON_PREFS = ("Armor_001_Head.sbitemico", "Weapon_Sword_001.sbitemico", "Item_Fruit_A.sbitemico")


def _yaz0(data: bytes) -> bytes:
    if data[:4] != b"Yaz0":
        return data
    tools = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import wiiu_convert  # one-way import (verify only); the package itself never imports other tools
    return wiiu_convert.yaz0_decompress(data)[0]


def pick_inputs(dec_dir: str, romfs: str | None, icons: int) -> list[tuple[str, str]]:
    out = [(f, os.path.join(dec_dir, f)) for f in sorted(os.listdir(dec_dir)) if f.endswith(".Tex.bfres")]
    if romfs and icons:
        d = os.path.join(romfs, "UI", "StockItem")
        if os.path.isdir(d):
            names = sorted(os.listdir(d))
            chosen = [n for n in ICON_PREFS if n in names][:icons]
            step = max(1, len(names) // (icons + 1))
            for n in names[step::step]:
                if len(chosen) >= icons:
                    break
                if n.endswith(".sbitemico") and n not in chosen:
                    chosen.append(n)
            out += [(n, os.path.join(d, n)) for n in chosen]
    return out


def _strip(ts: TextureSet) -> TextureSet:
    c = copy.deepcopy(ts)
    for t in c.textures:
        t.extra = {k: v for k, v in t.extra.items() if not k.startswith("nx.")}
    c.extra = {k: v for k, v in c.extra.items() if not k.startswith("nx.")}
    return c


def check_file(name: str, data: bytes) -> dict:
    res = {"file": name, "size": len(data)}
    t0 = time.perf_counter()
    ts = nx_bfres.read_textures(data)
    res["textures"] = len(ts.textures)
    res["levels"] = sum(t.mips for t in ts.textures)
    res["formats"] = sorted({formats.format_name(t.format) for t in ts.textures})
    res["nonstandard_bh"] = [t.name for t in ts.textures if t.extra.get("nx.block_height_log2") !=
                             tegra.game_block_height_log2(formats.level_dims(t.format, t.width, t.height, 0)[1])]
    # V1
    v1 = nx_bfres.write_textures(ts)
    t1 = time.perf_counter()
    res["v1_identical"] = v1 == data
    if not res["v1_identical"]:
        n = min(len(v1), len(data))
        res["v1_first_diff"] = next((hex(i) for i in range(n) if v1[i] != data[i]), hex(n))
    res["v1_selfcheck"] = checks.switch_tex_problems(v1)
    res["v1_diff"] = diff(ts, nx_bfres.read_textures(v1), "same")
    # V2
    t2 = time.perf_counter()
    tex1, tex2 = wiiu_bfres.write_textures(ts)
    t3 = time.perf_counter()
    res["wiiu_selfcheck"] = wiiu_bfres.self_check(tex1) + (wiiu_bfres.self_check(tex2) if tex2 else [])
    warn: list[str] = []
    t4 = time.perf_counter()
    back = wiiu_bfres.read_textures(tex1, tex2, warn)
    v2 = nx_bfres.write_textures(back)
    t5 = time.perf_counter()
    res["wiiu_warnings"] = warn
    res["mip_check"] = _mip_check(back)
    res["wiiu_sizes"] = [len(tex1), len(tex2) if tex2 else 0]
    res["v2_neutral_diff"] = diff(ts, back, "cross")
    res["v2_selfcheck"] = checks.switch_tex_problems(v2)
    res["v2_reread_diff"] = diff(ts, nx_bfres.read_textures(v2), "cross")
    res["v2_identical_to_original"] = v2 == data
    res["v2_identical_to_writer"] = v2 == nx_bfres.write_textures(_strip(ts))
    res["time"] = {"v1_read_write": round(t1 - t0, 3), "switch_to_wiiu": round(t3 - t2, 3),
                   "wiiu_to_switch": round(t5 - t4, 3)}
    res["ok"] = (not res["v1_selfcheck"] and not res["v1_diff"] and not res["wiiu_selfcheck"]
                 and not res["v2_neutral_diff"] and not res["v2_selfcheck"] and not res["v2_reread_diff"]
                 and res["v2_identical_to_writer"])
    return res


def _mip_check(ts: TextureSet) -> dict:
    """The reader's mip check (mip 1 vs 2x2 average of mip 0) on textures read back from Wii U files: how many were
    checked and which fell below the warning level. On real Switch textures these are false alarms."""
    checked, low = 0, []
    for t in ts.textures:
        c = t.extra.get("wiiu.mip_coherence")
        if c is None or t.mips < 2:
            continue
        bx, by, _ = formats.level_dims(t.format, t.width, t.height, 1)
        if min(bx, by) < wiiu_bfres.COHERENCE_MIN_BLOCKS:
            continue
        checked += 1
        if c < wiiu_bfres.COHERENCE_WARN:
            low.append(f"{t.name} {c:.2f}")
    return {"checked": checked, "low": low}


def _chain(fmt: int, size: int, seed: int) -> Texture:
    rnd = random.Random(seed)
    levels = []
    m = 0
    while True:
        n = formats.level_size(fmt, size, size, m)
        levels.append(rnd.randbytes(n))
        if size >> m <= 1:
            break
        m += 1
    return Texture(f"bench_{fmt:x}", fmt, size, size, levels)


def speed(repeat: int = 3) -> dict:
    """MB/s of linear data per operation, best of `repeat` runs (the game may be running on the same machine)."""
    out = {}
    for label, fmt, size in (("BC1 2048x2048", 0x1A01, 2048), ("BC3 1024x1024", 0x1C01, 1024)):
        t = _chain(fmt, size, 7)
        mb = sum(len(x) for x in t.levels) / 1e6
        bh = tegra.game_block_height_log2(formats.level_dims(fmt, size, size, 0)[1])
        lay, sw, image, mip = gx2.tile_texture(t.levels, fmt, size, size, 0)
        img = tegra.tile_texture(t.levels, fmt, size, size, bh)
        ops = {
            "tegra_tile": lambda: tegra.tile_texture(t.levels, fmt, size, size, bh),
            "tegra_untile": lambda: tegra.untile_texture(img, fmt, size, size, t.mips, bh),
            "gx2_tile": lambda: gx2.tile_texture(t.levels, fmt, size, size, 0),
            "gx2_untile": lambda: gx2.untile_texture(fmt, size, size, t.mips, gx2.TILED_2D_THIN1, sw, lay.pitch,
                                                     image, mip, lay.mip_offsets),
        }
        assert ops["tegra_untile"]() == t.levels and ops["gx2_untile"]() == t.levels
        res = {"MB": round(mb, 2), "mips": t.mips, "cold": {}, "warm": {}}
        for name, fn in ops.items():
            for tag in ("cold", "warm"):
                best = float("inf")
                for _ in range(repeat):
                    if tag == "cold":
                        tegra._index.cache_clear()
                        gx2._index.cache_clear()
                    else:
                        fn()
                    a = time.perf_counter()
                    fn()
                    best = min(best, time.perf_counter() - a)
                res[tag][name] = round(mb / best, 1)
        out[label] = res
    return out


def run(dec_dir: str, romfs: str | None = None, icons: int = 3, say=print) -> dict:
    files = pick_inputs(dec_dir, romfs, icons)
    results = []
    for name, path in files:
        with open(path, "rb") as f:
            raw = f.read()
        try:
            r = check_file(name, _yaz0(raw))
        except ModelError as e:
            r = {"file": name, "ok": False, "error": f"{type(e).__name__}: {e}"}
        say(f"{name}: {'ok' if r.get('ok') else 'FAILED'}")
        results.append(r)
    return {"files": results, "speed": speed(),
            "samples": os.path.isdir(os.path.join(os.path.dirname(os.path.dirname(dec_dir)), "wiiu-samples"))}


def text(rep: dict) -> str:
    files = rep["files"]
    ok = [r for r in files if r.get("ok")]
    tex = sum(r.get("textures", 0) for r in files)
    lev = sum(r.get("levels", 0) for r in files)
    v1_same = sum(1 for r in files if r.get("v1_identical"))
    v2_orig = sum(1 for r in files if r.get("v2_identical_to_original"))
    total_mb = sum(r.get("size", 0) for r in files) / 1e6
    w2s = sum(r.get("time", {}).get("wiiu_to_switch", 0) for r in files)
    s2w = sum(r.get("time", {}).get("switch_to_wiiu", 0) for r in files)
    lines = ["Texture verification (milestone M1: textures)", "",
             "Results first:",
             f"- {len(ok)}/{len(files)} files pass V1 and V2 ({tex} textures, {lev} mip levels).",
             f"- V1 Switch -> Switch: {v1_same}/{len(files)} files byte-identical; structure and blocks the same in "
             f"{sum(1 for r in files if not r.get('v1_diff') and 'error' not in r)}/{len(files)}.",
             f"- V2 Switch -> Wii U Tex1/Tex2 -> Switch: identical linear blocks at every mip in "
             f"{sum(1 for r in files if not r.get('v2_neutral_diff') and 'error' not in r)}/{len(files)} files; "
             f"output equals the Switch writer's own output in "
             f"{sum(1 for r in files if r.get('v2_identical_to_writer'))}/{len(files)}; byte-identical to the "
             f"original in {v2_orig}/{len(files)} (differences: string pool order and non-standard block heights, "
             f"which only a Switch file carries).",
             f"- Wii U -> Switch speed on this sample: {total_mb:.1f} MB in {w2s:.2f} s "
             f"({total_mb / w2s if w2s else 0:.0f} MB/s; a 150 MB mod's textures take about "
             f"{150 / (total_mb / w2s) if w2s else 0:.0f} s). Switch -> Wii U: {s2w:.2f} s.",
             f"- Mip check (warns below {wiiu_bfres.COHERENCE_WARN}) on these real textures read back from Wii U files: "
             f"{sum(len(r.get('mip_check', {}).get('low', [])) for r in files)} false alarms in "
             f"{sum(r.get('mip_check', {}).get('checked', 0) for r in files)} textures checked.",
             "- Not proven on real Wii U files (no samples in out/wiiu-samples)." if not rep.get("samples") else
             "- Wii U samples exist: run the V3 checks.",
             "", "Tiler speed on made-up chains (MB/s of linear data, best of 3; cold = index cache cleared):"]
    for label, s in rep["speed"].items():
        for tag in ("cold", "warm"):
            v = s[tag]
            lines.append(f"- {label} ({s['MB']} MB, {s['mips']} mips) {tag}: Tegra tile {v['tegra_tile']}, untile "
                         f"{v['tegra_untile']}; GX2 tile {v['gx2_tile']}, untile {v['gx2_untile']}")
    lines += ["", "Per file:"]
    for r in files:
        if "error" in r:
            lines.append(f"- {r['file']}: ERROR {r['error']}")
            continue
        bits = [f"{r['textures']} textures", f"{r['levels']} levels", "/".join(r["formats"]),
                "V1 identical" if r["v1_identical"] else f"V1 differs at {r.get('v1_first_diff')}",
                "V2 blocks equal" if not r["v2_neutral_diff"] else f"V2 DIFF {r['v2_neutral_diff'][:2]}",
                f"Wii U->Switch {r['time']['wiiu_to_switch']:.2f}s"]
        if r["nonstandard_bh"]:
            bits.append(f"non-standard block height: {', '.join(r['nonstandard_bh'])}")
        if r.get("mip_check", {}).get("low"):
            bits.append(f"mip check below {wiiu_bfres.COHERENCE_WARN}: {', '.join(r['mip_check']['low'])}")
        for key in ("v1_selfcheck", "wiiu_selfcheck", "v2_selfcheck", "v1_diff", "v2_reread_diff", "wiiu_warnings"):
            if r.get(key):
                bits.append(f"{key}: {r[key][:2]}")
        lines.append(f"- {r['file']}: " + "; ".join(bits))
    return "\n".join(lines) + "\n"


def save(rep: dict, txt_path: str, json_path: str | None = None) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(txt_path)), exist_ok=True)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text(rep))
    if json_path:
        os.makedirs(os.path.dirname(os.path.abspath(json_path)), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
