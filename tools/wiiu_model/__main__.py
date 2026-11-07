"""Developer CLI (not for players yet; tools/wiiu_convert.py will call the package in milestone M3).

    python tools/wiiu_model inspect FILE                 list the textures of a Switch or Wii U texture file
    python tools/wiiu_model roundtrip FILE               Switch X.Tex -> Wii U -> Switch check of one file
    python tools/wiiu_model convert TEX1 [TEX2] -o OUT [--vanilla X.Tex.sbfres]
                                                         Wii U Tex1 (+Tex2) -> Switch X.Tex (decompressed output)
    python tools/wiiu_model verify --dec DIR [--romfs ROMFS] [--icons N] --out REPORT.txt [--json R.json]
    python tools/wiiu_model model-inspect FILE           list models, shapes, materials of a Switch or Wii U model
    python tools/wiiu_model model-roundtrip FILE         Switch model -> Switch and -> Wii U -> Switch checks
    python tools/wiiu_model model-convert FILE -o OUT    Wii U model -> Switch model (decompressed output)
    python tools/wiiu_model verify-models --dec DIR [--romfs ROMFS] [--extra N] [--samples DIR] --out R.txt
                                                         [--json R.json]

Inputs may be Yaz0-compressed. Game files are only read.
"""
from __future__ import annotations

import argparse
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wiiu_model import (convert, formats, model_to_switch, nx_bfres, read_model, sniff, verify,  # noqa: E402
                        verify_models, wiiu_bfres)
from wiiu_model.errors import ModelError  # noqa: E402


def _load(path: str) -> bytes:
    with open(path, "rb") as f:
        return verify._yaz0(f.read())


def cmd_inspect(a) -> int:
    data = _load(a.file)
    kind = sniff(data)
    if kind == "switch":
        ts = nx_bfres.read_textures(data)
    elif kind == "wiiu":
        warn: list[str] = []
        ts = wiiu_bfres.read_textures(data, None, warn)
        for w in warn:
            print("note:", w)
    else:
        print("not a BFRES texture file")
        return 1
    print(f"{kind} {ts.kind} {ts.name!r}: {len(ts.textures)} textures")
    for t in ts.textures:
        print(f"  {t.name}: {formats.format_name(t.format)} {t.width}x{t.height}, {t.mips} mips, channels "
              f"{t.channels}, swizzle hint {t.swizzle_hint}")
    return 0


def cmd_roundtrip(a) -> int:
    r = verify.check_file(os.path.basename(a.file), _load(a.file))
    for k, v in r.items():
        print(f"{k}: {v}")
    return 0 if r["ok"] else 1


def cmd_convert(a) -> int:
    notes: list[str] = []
    out, ts = convert.textures_to_switch(_load(a.tex1), _load(a.tex2) if a.tex2 else None,
                                         _load(a.vanilla) if a.vanilla else None, notes)
    with open(a.output, "wb") as f:
        f.write(out)
    print(f"wrote {a.output}: {len(ts.textures)} textures")
    for n in notes:
        print("note:", n)
    return 0


def cmd_verify(a) -> int:
    rep = verify.run(a.dec, a.romfs, a.icons)
    verify.save(rep, a.out, a.json)
    print(verify.text(rep).split("\n\n")[1])
    return 0 if all(r.get("ok") for r in rep["files"]) else 1


def cmd_model_inspect(a) -> int:
    warn: list[str] = []
    data = _load(a.file)
    res = read_model(data, warn)
    print(f"{sniff(data)} {res.name!r}: {len(res.models)} models, {len(res.skel_anims)} skeletal and "
          f"{len(res.mat_anims)} material animations, external files {[e.name for e in res.external]}")
    for m in res.models:
        print(f"  model {m.name}: {len(m.skeleton.bones)} bones, {len(m.vertex_buffers)} vertex buffers, "
              f"{len(m.shapes)} shapes, {len(m.materials)} materials")
        for vb in m.vertex_buffers[:a.limit]:
            print(f"    vertices {vb.vertex_count}: " + ", ".join(f"{x.name} {formats.attr_format_name(x.format)}"
                                                           for x in vb.attributes))
        for sh in m.shapes[:a.limit]:
            print(f"    shape {sh.name}: material {sh.material}, {len(sh.meshes)} meshes "
                  f"({', '.join(str(x.index_count) for x in sh.meshes)} indices)")
        for mat in m.materials[:a.limit]:
            print(f"    material {mat.name}: textures {mat.textures}")
    for w in warn:
        print("note:", w)
    return 0


def cmd_model_roundtrip(a) -> int:
    r = verify_models.check_file(os.path.basename(a.file), _load(a.file))
    for k, v in r.items():
        print(f"{k}: {v}")
    return 0 if r["ok"] else 1


def cmd_model_convert(a) -> int:
    warn: list[str] = []
    out, res = model_to_switch(_load(a.file), warn)
    with open(a.output, "wb") as f:
        f.write(out)
    print(f"wrote {a.output}: {len(res.models)} models")
    for w in warn:
        print("note:", w)
    return 0


def cmd_verify_models(a) -> int:
    rep = verify_models.run(a.dec, a.romfs, a.extra, a.samples)
    verify_models.save(rep, a.out, a.json)
    print(verify_models.text(rep).split("\n\n")[1])
    ok = all(r.get("ok") for r in rep["files"]) and all(s.get("ok") for s in rep["samples"] or [])
    return 0 if ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="wiiu_model")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("inspect")
    s.add_argument("file")
    s = sub.add_parser("roundtrip")
    s.add_argument("file")
    s = sub.add_parser("convert")
    s.add_argument("tex1")
    s.add_argument("tex2", nargs="?")
    s.add_argument("-o", "--output", required=True)
    s.add_argument("--vanilla")
    s = sub.add_parser("verify")
    s.add_argument("--dec", required=True)
    s.add_argument("--romfs")
    s.add_argument("--icons", type=int, default=3)
    s.add_argument("--out", required=True)
    s.add_argument("--json")
    s = sub.add_parser("model-inspect")
    s.add_argument("file")
    s.add_argument("--limit", type=int, default=8)
    s = sub.add_parser("model-roundtrip")
    s.add_argument("file")
    s = sub.add_parser("model-convert")
    s.add_argument("file")
    s.add_argument("-o", "--output", required=True)
    s = sub.add_parser("verify-models")
    s.add_argument("--dec", required=True)
    s.add_argument("--romfs")
    s.add_argument("--extra", type=int, default=6)
    s.add_argument("--samples")
    s.add_argument("--out", required=True)
    s.add_argument("--json")
    a = p.parse_args(argv)
    try:
        return {"inspect": cmd_inspect, "roundtrip": cmd_roundtrip, "convert": cmd_convert, "verify": cmd_verify,
                "model-inspect": cmd_model_inspect, "model-roundtrip": cmd_model_roundtrip,
                "model-convert": cmd_model_convert, "verify-models": cmd_verify_models}[a.cmd](a)
    except ModelError as e:
        print(f"{type(e).__name__}: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
