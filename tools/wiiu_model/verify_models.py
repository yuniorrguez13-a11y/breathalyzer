"""Model verification on the user's own Switch files (milestone M2). Read-only on the game; small samples; run it at
below-normal priority (the user may be playing).

V1  Switch -> neutral -> Switch: byte-identical (target), self-check, the same neutral tree.
V2  Switch -> neutral -> Wii U (test writer) -> neutral -> Switch: every model value equal (`describe(.., 'cross')`:
    skeleton, vertex buffers, indices, shapes, bounds, materials, user data, external files); the Wii U file passes
    its self-check (re-reads without warnings, every non-zero byte read, sorted string pool); the Switch output passes
    its self-check. Embedded animations go through Wii U FSKA / FSHU / FTXP (wiiu_anims) and must come back equal;
    TexInfo.txt is left out of the Wii U file (a real one has none): tools/wiiu_convert.py writes it.
V2b The same with Wii U materials as a real Wii U file has them (the 16 render infos dropped, only the RenderState
    kept): how often the regenerated render infos and `gsys_alpha_test_ref_value` equal the originals.
Decoded checks on every file: normals unit length, indices below the vertex count, bone indices below the matrix
count, weights summing to 255 (checks.model_stats). Any failure fails a Switch file.
V3  When `out/wiiu-samples` exists: every Wii U model found (loose or inside SARC packs, Yaz0 or not) is parsed,
    checked (coverage, decoded checks) and converted to the Switch (self-check). A sample fails on any index or bone
    index out of range, or when more than 1% of its normals or weight sets are bad (a wrong byte or bit order breaks
    nearly all of them). Samples are only read.
"""
from __future__ import annotations

import copy
import json
import os
import time

from . import checks, materials, nx_model, wiiu_anims, wiiu_models
from .errors import ModelError
from .neutral import diff
from .verify import _yaz0

ROMFS_MAX_BYTES = 2_000_000
SAMPLE_TOLERANCE = 0.01         # share of bad normals / weight sets a real Wii U sample may have
_DECODED = (("bad_indices", "indices", "indices past the vertex count", False),
            ("bad_bone_refs", "bone_refs", "bone indices past the matrix count", False),
            ("bad_normals", "normals", "normals off unit length", True),
            ("bad_weights", "weights", "weight sets not summing to 255", True))


def decoded_problems(st: dict, tolerance: float = 0.0) -> list[str]:
    """Failures of the decoded checks (checks.model_stats): any index or bone index out of range; normals and weight
    sets when more than `tolerance` of them are bad."""
    out = []
    for bad, total, label, tolerated in _DECODED:
        if st[bad] > (tolerance * st[total] if tolerated else 0):
            out.append(f"{st[bad]}/{st[total]} {label}")
    return out


def decoded_summary(st: dict) -> str:
    return ", ".join(f"{st[bad]}/{st[total]} {label}" for bad, total, label, _ in _DECODED)


def pick_inputs(dec_dir: str, romfs: str | None, extra: int) -> list[tuple[str, str]]:
    """Model files of `dec_dir` (decompressed, not `.Tex`), plus `extra` evenly spaced `.sbfres` models of
    romfs/Model (not textures, not already sampled, compressed size at most 2 MB)."""
    out = [(f, os.path.join(dec_dir, f)) for f in sorted(os.listdir(dec_dir))
           if f.endswith(".bfres") and not f.endswith(".Tex.bfres")]
    if romfs and extra:
        d = os.path.join(romfs, "Model")
        if os.path.isdir(d):
            have = {f[:-len(".bfres")] for f, _ in out}
            names = [n for n in sorted(os.listdir(d)) if n.endswith(".sbfres") and ".Tex" not in n
                     and n[:-len(".sbfres")] not in have]
            step = max(1, len(names) // (extra + 1))
            chosen = []
            for n in names[step::step]:
                if len(chosen) >= extra:
                    break
                p = os.path.join(d, n)
                if os.path.getsize(p) <= ROMFS_MAX_BYTES:
                    chosen.append((n, p))
            out += chosen
    return out


def _wiiu_view(res):
    """What a Wii U file can carry of a Switch ResFile: the animations that have a Wii U form (wiiu_anims), no
    Switch-only external files (TexInfo.txt)."""
    c = copy.copy(res)
    c.skel_anims = [a for a in res.skel_anims if not wiiu_anims.unwritable(a)]
    c.mat_anims = [a for a in res.mat_anims if not wiiu_anims.unwritable(a)]
    c.external = [e for e in res.external if e.name not in wiiu_models.SWITCH_ONLY_EXTERNAL]
    return c


def _v2b(res) -> dict:
    """Switch -> Wii U without the 16 render infos -> Switch: per-material agreement of the regenerated render infos
    and the alpha reference param."""
    notes, warn = [], []
    wu = wiiu_models.write(res, keep_render_infos=False, notes=notes)
    back = wiiu_models.read(wu, warn)
    for m in back.models:
        materials.to_switch(m, warn)
    total = agree = whole = 0
    fields: dict[str, int] = {}
    examples: list[str] = []
    values: dict[str, set] = {}
    for m0, m1 in zip(res.models, back.models):
        for a, b in zip(m0.materials, m1.materials):
            total += 1
            for r in a.render_infos:
                if r.name in materials.RENDER_STATE_NAMES and r.kind == "string":
                    values.setdefault(r.name, set()).update(r.values)
            va = {r.name: r.values for r in a.render_infos}
            vb = {r.name: r.values for r in b.render_infos}
            bad = [n for n in materials.RENDER_STATE_NAMES if va.get(n) != vb.get(n)]
            if materials.alpha_ref(a) != materials.alpha_ref(b):
                bad.append(materials.ALPHA_REF_PARAM)
            for n in bad:
                fields[n] = fields.get(n, 0) + 1
                if len(examples) < 5:
                    examples.append(f"{m0.name}/{a.name} {n}: {va.get(n)} -> {vb.get(n)}")
            agree += not bad
            whole += not diff(a, b, "cross", 1)
    return {"materials": total, "agree": agree, "whole_material_equal": whole, "fields": fields,
            "examples": examples, "warnings": warn[:5], "values": {k: sorted(v) for k, v in values.items()}}


def check_file(name: str, data: bytes) -> dict:
    res_ = {"file": name, "size": len(data)}
    t0 = time.perf_counter()
    res = nx_model.read(data)
    t1 = time.perf_counter()
    res_["models"] = len(res.models)
    res_["shapes"] = sum(len(m.shapes) for m in res.models)
    res_["materials"] = sum(len(m.materials) for m in res.models)
    res_["bones"] = sum(len(m.skeleton.bones) for m in res.models)
    res_["vertices"] = sum(vb.vertex_count for m in res.models for vb in m.vertex_buffers)
    res_["animations"] = [len(res.skel_anims), len(res.mat_anims)]
    # V1
    v1 = nx_model.write(res)
    t2 = time.perf_counter()
    res_["v1_identical"] = v1 == data
    if not res_["v1_identical"]:
        n = min(len(v1), len(data))
        res_["v1_first_diff"] = hex(next((i for i in range(n) if v1[i] != data[i]), n))
    res_["v1_selfcheck"] = checks.switch_model_problems(v1)
    res_["v1_diff"] = diff(res, nx_model.read(v1), "same")
    # V2
    notes: list[str] = []
    t3 = time.perf_counter()
    wu = wiiu_models.write(res, notes=notes)
    t4 = time.perf_counter()
    res_["wiiu_selfcheck"] = wiiu_models.self_check(wu)
    warn: list[str] = []
    t5 = time.perf_counter()
    back = wiiu_models.read(wu, warn)
    for m in back.models:
        materials.to_switch(m, warn)
    v2 = nx_model.write(back)
    t6 = time.perf_counter()
    plain = _wiiu_view(res)
    res_["left_out"] = notes
    res_["wiiu_warnings"] = warn[:5]
    res_["wiiu_size"] = len(wu)
    res_["v2_diff"] = diff(plain, back, "cross")
    res_["v2_selfcheck"] = checks.switch_model_problems(v2)
    res_["v2_reread_diff"] = diff(plain, nx_model.read(v2), "cross")
    res_["texinfo"] = any(e.name == "TexInfo.txt" for e in res.external)
    res_["texinfo_lost"] = res_["texinfo"] and not any(e.name == "TexInfo.txt" for e in back.external)
    res_["v2b"] = _v2b(res)
    res_["stats"] = checks.model_stats(back)
    res_["decoded_problems"] = decoded_problems(res_["stats"])
    res_["time"] = {"switch_read": round(t1 - t0, 3), "switch_write": round(t2 - t1, 3),
                    "switch_to_wiiu": round(t4 - t3, 3), "wiiu_to_switch": round(t6 - t5, 3)}
    res_["ok"] = (not res_["v1_selfcheck"] and not res_["v1_diff"] and not res_["wiiu_selfcheck"]
                  and not res_["v2_diff"] and not res_["v2_selfcheck"] and not res_["v2_reread_diff"]
                  and not res_["decoded_problems"])
    return res_


# ------------------------------------------------------------------------------------------------- V3 (samples)

def _walk_samples(root: str):
    """(label, decompressed bytes) of every BFRES under `root`, loose or inside SARC packs (two levels)."""
    import sys
    tools = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import wiiu_convert

    def visit(label: str, data: bytes, depth: int):
        try:
            data = _yaz0(data)
        except Exception as e:  # noqa: BLE001 - an untrusted file: report, keep going
            yield label, None, f"Yaz0: {e}"
            return
        if data[:4] == b"FRES":
            yield label, data, None
        elif data[:4] == b"SARC" and depth < 2:
            try:
                files = wiiu_convert.sarc_files(data)
            except Exception as e:  # noqa: BLE001
                yield label, None, f"SARC: {e}"
                return
            for n, d in files:
                yield from visit(f"{label}//{n}", d, depth + 1)

    for dirpath, _, files in os.walk(root):
        for f in sorted(files):
            p = os.path.join(dirpath, f)
            if os.path.getsize(p) > 64_000_000:
                continue
            with open(p, "rb") as fh:
                head = fh.read(4)
                if head not in (b"Yaz0", b"FRES", b"SARC"):
                    continue
                data = head + fh.read()
            yield from visit(os.path.relpath(p, root), data, 0)


def check_sample(label: str, data: bytes) -> dict:
    """V3 on one real Wii U file (models only; texture files are the texture verify's job)."""
    out = {"file": label}
    if not wiiu_models.is_wiiu_bfres(data):
        out["skip"] = "not a Wii U BFRES"
        return out
    from . import wiiu_bfres
    try:
        h = wiiu_bfres.read_header(data)
    except ModelError as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["version"] = hex(h["version"])
    out["counts"] = h["counts"]
    if not h["counts"][0]:
        out["skip"] = "no models (texture or animation file)"
        return out
    warn: list[str] = []
    try:
        res = wiiu_models.read(data, warn)
        out["uncovered"] = [(hex(a), hex(b)) for a, b in wiiu_models.uncovered(data)[:5]]
        out["stats"] = checks.model_stats(res)
        for m in res.models:
            materials.to_switch(m, warn)
        sw = nx_model.write(res)
        out["switch_selfcheck"] = checks.switch_model_problems(sw)
        out["models"] = [m.name for m in res.models]
        out["formats"] = sorted({hex(a.format) for m in res.models for vb in m.vertex_buffers
                                 for a in vb.attributes})
        out["decoded_problems"] = decoded_problems(out["stats"], SAMPLE_TOLERANCE)
        out["ok"] = not out["switch_selfcheck"] and not out["uncovered"] and not out["decoded_problems"]
    except ModelError as e:
        out["error"] = f"{type(e).__name__}: {e}"
    out["warnings"] = warn[:10]
    return out


# ------------------------------------------------------------------------------------------------------ harness

def run(dec_dir: str, romfs: str | None = None, extra: int = 6, samples: str | None = None, say=print) -> dict:
    t0 = time.perf_counter()
    results = []
    for name, path in pick_inputs(dec_dir, romfs, extra):
        with open(path, "rb") as f:
            raw = f.read()
        try:
            r = check_file(name, _yaz0(raw))
        except ModelError as e:
            r = {"file": name, "ok": False, "error": f"{type(e).__name__}: {e}"}
        say(f"{name}: {'ok' if r.get('ok') else 'FAILED'}")
        results.append(r)
    rep = {"files": results, "samples": None, "seconds": 0.0}
    if samples and os.path.isdir(samples):
        rep["samples"] = []
        for label, data, err in _walk_samples(samples):
            s = {"file": label, "error": err} if err else check_sample(label, data)
            if "skip" not in s:
                say(f"sample {label}: {'ok' if s.get('ok') else 'FAILED'}")
                rep["samples"].append(s)
    rep["seconds"] = round(time.perf_counter() - t0, 1)
    return rep


def _v2b_values(good: list) -> str:
    vals: dict[str, set] = {}
    for r in good:
        for k, v in r["v2b"].get("values", {}).items():
            vals.setdefault(k, set()).update(v)
    short = {"gsys_render_state_mode": "mode", "gsys_render_state_display_face": "face",
             "gsys_render_state_blend_mode": "blend mode", "gsys_depth_test_func": "depth func",
             "gsys_alpha_test_func": "alpha func"}
    parts = [f"{label} {'/'.join(sorted(vals.get(k, ())))}" for k, label in short.items()]
    funcs = {n: vals.get(n, set()) for n in materials.RENDER_STATE_NAMES[6:12]}
    parts.append(f"blend functions and ops {'/'.join(sorted(set().union(*funcs.values())))}")
    return ", ".join(parts)


def text(rep: dict) -> str:
    files = rep["files"]
    good = [r for r in files if "error" not in r]
    n = len(files)
    ok = sum(1 for r in files if r.get("ok"))
    ident = sum(1 for r in good if r["v1_identical"])
    v2eq = sum(1 for r in good if not r["v2_diff"] and not r["v2_reread_diff"])
    wsc = sum(1 for r in good if not r["wiiu_selfcheck"])
    ssc = sum(1 for r in good if not r["v1_selfcheck"] and not r["v2_selfcheck"])
    mats = sum(r["v2b"]["materials"] for r in good)
    agree = sum(r["v2b"]["agree"] for r in good)
    fields: dict[str, int] = {}
    for r in good:
        for k, v in r["v2b"]["fields"].items():
            fields[k] = fields.get(k, 0) + v
    st = {k: sum(r["stats"][k] for r in good) for k in ("normals", "bad_normals", "indices", "bad_indices",
                                                         "bone_refs", "bad_bone_refs", "weights", "bad_weights")}
    anims = [r for r in good if any(r["animations"])]
    tot = {k: sum(r["time"][k] for r in good) for k in ("switch_read", "switch_write", "switch_to_wiiu",
                                                         "wiiu_to_switch")}
    a21 = next((r for r in good if r["file"].startswith("Armor_021.")), None)
    lines = [
        "Model verification (milestone M2: models)", "",
        "Results first:",
        f"- {ok}/{n} files pass V1 and V2 ({sum(r['models'] for r in good)} models, "
        f"{sum(r['shapes'] for r in good)} shapes, {sum(r['materials'] for r in good)} materials, "
        f"{sum(r['bones'] for r in good)} bones, {sum(r['vertices'] for r in good)} vertices).",
        f"- V1 Switch -> Switch: {ident}/{n} byte-identical; same neutral tree in "
        f"{sum(1 for r in good if not r['v1_diff'])}/{n}; Switch self-check passes in {ssc}/{n} (V1 and V2 output).",
        f"- V2 Switch -> Wii U -> Switch: every model value equal in {v2eq}/{n} files; Wii U self-check passes in "
        f"{wsc}/{n}. That includes the embedded animations of {len(anims)} files "
        f"({sum(r['animations'][0] for r in anims)} skeletal, {sum(r['animations'][1] for r in anims)} material, "
        f"as Wii U FSKA / FSHU / FTXP); left out: "
        f"{sum(1 for r in good for x in r['left_out'] if x.startswith('animation '))}.",
        f"- V2b materials without the 16 render infos (as a real Wii U file has them): {agree}/{mats} materials get "
        f"all 16 render infos and {materials.ALPHA_REF_PARAM} back from the RenderState"
        + (f"; disagreements by field: {fields}." if fields else ".")
        + f" This covers only the values your files use: {_v2b_values(good)}; other values are written with a "
          f"warning.",
        f"- Decoded checks: {st['bad_normals']}/{st['normals']} normals off unit length, {st['bad_indices']}/"
        f"{st['indices']} indices past the vertex count, {st['bad_bone_refs']}/{st['bone_refs']} bone indices past "
        f"the matrix count, {st['bad_weights']}/{st['weights']} weight sets not summing to 255.",
        f"- Time: Switch read {tot['switch_read']:.1f} s, write {tot['switch_write']:.1f} s, Switch -> Wii U "
        f"{tot['switch_to_wiiu']:.1f} s, Wii U -> Switch {tot['wiiu_to_switch']:.1f} s; whole run "
        f"{rep['seconds']:.0f} s"
        + (f"; Armor_021 Switch -> Wii U -> Switch "
           f"{a21['time']['switch_to_wiiu'] + a21['time']['wiiu_to_switch']:.2f} s." if a21 else "."),
    ]
    tex = [r for r in good if r.get("texinfo")]
    lines.append(f"- TexInfo.txt: {len(tex)}/{n} files have one; a Wii U file has none, so it is missing after this "
                 f"round trip in {sum(1 for r in tex if r.get('texinfo_lost'))} of them (tools/wiiu_convert.py "
                 f"writes it from the X.Tex the game loads).")
    if rep.get("samples") is None:
        lines.append("- Not proven on real Wii U files (no samples in out/wiiu-samples): the Wii U side is only "
                     "checked against our own writer.")
    else:
        s = rep["samples"]
        withst = [x for x in s if "stats" in x]
        sst = {k: sum(x["stats"][k] for x in withst) for k in ("normals", "bad_normals", "indices", "bad_indices",
                                                                "bone_refs", "bad_bone_refs", "weights",
                                                                "bad_weights")}
        lines.append(f"- V3 real Wii U samples: {sum(1 for x in s if x.get('ok'))}/{len(s)} model files parse, "
                     f"convert, pass the Switch self-check and the decoded checks. Decoded: {decoded_summary(sst)}.")
    lines += ["", "Per file:"]
    for r in files:
        if "error" in r:
            lines.append(f"- {r['file']}: ERROR {r['error']}")
            continue
        bits = [f"{r['models']} models / {r['shapes']} shapes / {r['materials']} materials",
                "V1 identical" if r["v1_identical"] else f"V1 differs at {r.get('v1_first_diff')}",
                "V2 equal" if not r["v2_diff"] and not r["v2_reread_diff"] else f"V2 DIFF {r['v2_diff'][:2]}",
                f"V2b {r['v2b']['agree']}/{r['v2b']['materials']}",
                f"{r['time']['switch_to_wiiu'] + r['time']['wiiu_to_switch']:.2f} s"]
        if any(r["animations"]):
            bits.append(f"animations: {r['animations'][0]} skeletal, {r['animations'][1]} material")
        gone = [x for x in r["left_out"] if x.startswith("animation ")]
        if gone:
            bits.append(f"left out: {gone[:2]}")
        for key in ("v1_selfcheck", "wiiu_selfcheck", "v2_selfcheck", "v1_diff", "wiiu_warnings"):
            if r.get(key):
                bits.append(f"{key}: {r[key][:2]}")
        if r["stats"]["problems"] or r.get("decoded_problems"):
            bits.append(f"decoded: {r.get('decoded_problems')} {r['stats']['problems'][:2]}")
        if r["v2b"]["examples"]:
            bits.append(f"V2b: {r['v2b']['examples'][:2]}")
        lines.append(f"- {r['file']}: " + "; ".join(bits))
    if rep.get("samples"):
        lines += ["", "Wii U samples:"]
        for s in rep["samples"]:
            if "error" in s:
                lines.append(f"- {s['file']}: ERROR {s['error']}")
                continue
            dec = decoded_summary(s["stats"]) if "stats" in s else "not decoded"
            lines.append(f"- {s['file']}: {'ok' if s.get('ok') else 'FAILED'}; version {s.get('version')}, models "
                         f"{s.get('models')}, uncovered {s.get('uncovered')}, Switch self-check "
                         f"{s.get('switch_selfcheck')}; decoded: {dec}"
                         + (f"; decoded FAILS: {s['decoded_problems']}" if s.get("decoded_problems") else "")
                         + f"; warnings {s.get('warnings', [])[:3]}")
    return "\n".join(lines) + "\n"


def save(rep: dict, txt_path: str, json_path: str | None = None) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(txt_path)), exist_ok=True)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text(rep))
    if json_path:
        os.makedirs(os.path.dirname(os.path.abspath(json_path)), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1, default=str)


__all__ = ["run", "text", "save", "check_file", "check_sample", "pick_inputs"]
