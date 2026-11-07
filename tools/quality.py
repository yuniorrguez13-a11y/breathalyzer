#!/usr/bin/env python3
"""Quality presets for Savage Wind, like a PC game's: Low, Medium, High and Original.

    python tools/quality.py list                      the presets and what each one turns on
    python tools/quality.py set low [--deploy]        pick one for a mod library (default: modman's library)
    python tools/quality.py toggle ssao off           change one lever on top of the preset
    python tools/quality.py status                    what the library uses now
    python tools/quality.py run [--port 7777]         start the game (modman run) with the preset's start-up values
    python tools/quality.py env                       the start-up values as PowerShell lines
    python tools/quality.py json                      the presets as JSON (for the in-game mod menu)

Each preset is a bundle of levers (docs/performance.md):

- start-up values Savage Wind reads (grass distance, object draw distance): kept in <library>\\quality.json and
  set by `quality.py run` (modman run passes them on);
- small RomFS data mods built from your own game files (sun-shadow range; ambient occlusion, bloom and far blur):
  recipes in native/examples/perf_shadows and native/examples/perf_effects, built into the library with modman;
- a third-party mod you added yourself (Remove Grass), turned on or off;
- Savage Wind's own Video settings, which this tool prints for you to choose in the game's menu.

Other programs (tools/modmenu.py) can call it: set_quality(library, level), toggle(library, lever, value),
presets() (JSON-ready), status(library) and launch_env(library). Nothing here changes Savage Wind's folder or your
savagewind.ini, and only the mods this tool builds or the role mods (Remove Grass) are turned on or off.

The effects mod ships Pack/Bootup.pack: while it is on, Savage Wind 0.4.1-alpha.4 skips two of its own fixes (its HUD
button letters and its voice size-table fix), so only Low uses it and every result says so.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import bnp  # noqa: E402  (other people's files: used only through the wrappers under "packs" below)
import modman as mm  # noqa: E402
import wiiu_convert as wc  # noqa: E402

REPO = os.path.dirname(TOOLS)
EXAMPLES = os.path.join(REPO, "native", "examples")
TOOL_ID = "botw-mod-loader quality"
FORMAT = 1
STATE = "quality.json"
LEVELS = ("low", "medium", "high", "original")
CUSTOM = "custom"
BUILD_VERSION = 2  # (part of each built mod's key: a change in how mods are built rebuilds them)


class QualityError(Exception):
    """Something the player has to fix; the message says what."""


# ---------------------------------------------------------------- levers and presets

@dataclass(frozen=True)
class Lever:
    id: str
    label: str
    kind: str        # "env": a start-up value; "build": a RomFS mod built from the game's files; "mod": a role mod
    original: object
    help: str
    low: float = 0.0  # (number levers: the range accepted)
    high: float = 0.0
    recipe: str = ""  # (build levers: the folder in native/examples)


LEVERS: dict[str, Lever] = {lv.id: lv for lv in (
    Lever("grass", "Grass distance", "env", 1.0,
          "How far grass is drawn, as a share of the game's (blades ~21 m, tufts ~46 m). Grass near Link stays.",
          0.25, 1.0),
    Lever("objects", "Object draw distance", "env", 1.0,
          "How far trees, rocks, enemies and animals appear, as a share of the game's (Savage Wind only tested 1 "
          "and up). Below 1, forests can show a gap before the distant trees, and enemies exist only nearer. "
          "In no preset.", 0.5, 1.0),
    Lever("shadow_range", "Sun-shadow range (m)", "build", 3000.0,
          "The far end of the sun-shadow setting, in metres (the game: 3000). No visible change was found, so it "
          "may not be the shadow range at all. In no preset.", 300.0, 3000.0, "perf_shadows"),
    Lever("ssao", "Ambient occlusion", "build", True,
          "Soft contact shadows in corners and under objects. Off ships Pack/Bootup.pack (see the note).",
          recipe="perf_effects"),
    Lever("bloom", "Bloom (glow)", "build", True,
          "The glow around bright light. Off ships Pack/Bootup.pack (see the note).", recipe="perf_effects"),
    Lever("dof", "Far blur (depth of field)", "build", True,
          "The blur on far mountains in the open world. Off ships Pack/Bootup.pack (see the note).",
          recipe="perf_effects"),
    Lever("no_grass", "Remove Grass mod", "mod", False,
          "Sen Uii's Remove Grass (a mod you add yourself): no grass at all, so no grass fires or updrafts."),
)}

# The presets. Values are the levers' (numbers: shares of the game's, or metres; True: the game's effect on).
# Only levers with a measured gain or no game file replaced are in them (docs/performance.md, round 3).
PRESETS: dict[str, dict[str, object]] = {
    "original": {"grass": 1.0, "objects": 1.0, "shadow_range": 3000.0, "ssao": True, "bloom": True, "dof": True,
                 "no_grass": False},
    "high": {"grass": 1.0, "objects": 1.0, "shadow_range": 3000.0, "ssao": True, "bloom": True, "dof": True,
             "no_grass": False},
    "medium": {"grass": 0.6, "objects": 1.0, "shadow_range": 3000.0, "ssao": True, "bloom": True, "dof": True,
               "no_grass": False},
    "low": {"grass": 0.4, "objects": 1.0, "shadow_range": 3000.0, "ssao": False, "bloom": False, "dof": False,
            "no_grass": False},  # (Remove Grass: no gain over short grass in our test, and no grass fires: a toggle)
}
LABELS = {"low": "Low", "medium": "Medium", "high": "High", "original": "Original", CUSTOM: "Custom"}
BLURBS = {
    "low": "Most fps: short grass, no glow, no ambient occlusion, no far blur. Replaces Pack/Bootup.pack, so "
           "Savage Wind's HUD button letters and voice size fix are off.",
    "medium": "Shorter grass only; no game file replaced. Not measured on its own.",
    "high": "The game's look, no mods; only Savage Wind's upscaler (Ultra performance) differs from Original.",
    "original": "The game as it is: no quality mods, no start-up values.",
}

# Savage Wind's own settings for each preset (its Video page; names as in savagewind.ini). Printed, never written.
SETTINGS: dict[str, dict[str, str]] = {
    "low": {"frame_rate": "60", "upscaler": "fsr1", "upscaler_quality": "ultra_performance",
            "shadow_resolution": "console", "draw_distance": "original", "antialias": "off"},
    "medium": {"frame_rate": "60", "upscaler": "fsr1", "upscaler_quality": "ultra_performance",
               "shadow_resolution": "console", "draw_distance": "original", "antialias": "off"},
    "high": {"frame_rate": "60", "upscaler": "fsr1", "upscaler_quality": "ultra_performance",
             "shadow_resolution": "console", "draw_distance": "original", "antialias": "off"},
    "original": {"frame_rate": "60", "upscaler": "off", "upscaler_quality": "-",
                 "shadow_resolution": "console", "draw_distance": "original", "antialias": "off"},
}
SETTING_LABELS = {"frame_rate": "Frame rate", "upscaler": "Upscaler", "upscaler_quality": "Upscaler quality",
                  "shadow_resolution": "Shadow resolution", "draw_distance": "Draw distance",
                  "antialias": "Anti-aliasing"}
VALUE_LABELS = {"fsr1": "FSR 1", "off": "Off", "ultra_performance": "Ultra performance", "performance": "Performance",
                "balanced": "Balanced", "quality": "Quality", "console": "Console", "original": "Original",
                "-": "-"}
SETTINGS_TIP = ("Frame rate 60 (not 30): at 30 the game can never show more than 30 fps. On a laptop: plug in the "
                "charger and pick Windows' best-performance power mode.")
# What a menu shows while the effects mod is on (it ships Pack/Bootup.pack; Savage Wind 0.4.1-alpha.4 then skips
# its own Bootup.pack changes, and with them its voice size-table fix: seen in every game log of round 1 and 2).
BOOTUP_PACK = "Pack/Bootup.pack"
BOOTUP_NOTE = ("Performance: effects off replaces Pack/Bootup.pack, so Savage Wind (0.4.1-alpha.4) skips its HUD "
               "button letters (the game's own letters show) and its voice size-table fix (needed when a voice bank "
               "is bigger than the game's own; without it that voice may be silent or crash its scene).")

# The start-up values `run` sets (and clears first): Savage Wind reads them once, when it starts.
ENV_KEYS = ("SAVAGEWIND_GRASS_BLADES", "SAVAGEWIND_GRASS_QUADS", "SAVAGEWIND_TRAVERSE_SCALE", "SAVAGEWIND_FAR_SCALE")


def presets() -> dict:
    """Everything a menu needs to show the presets and levers (JSON-ready)."""
    return {
        "tool": TOOL_ID, "format": FORMAT, "levels": list(LEVELS),
        "presets": {lvl: {"label": LABELS[lvl], "about": BLURBS[lvl], "levers": dict(PRESETS[lvl]),
                          "savage_wind": dict(SETTINGS[lvl])} for lvl in LEVELS},
        "levers": {lv.id: {"label": lv.label, "kind": lv.kind, "original": lv.original, "about": lv.help,
                           **({"min": lv.low, "max": lv.high} if isinstance(lv.original, float) else {})}
                   for lv in LEVERS.values()},
        "settings_tip": SETTINGS_TIP,
        "bootup_note": BOOTUP_NOTE,  # (show it while a preset turns the effects mod on: ssao, bloom or dof off)
    }


def parse_value(lever: str, value) -> object:
    """A lever's value from text or a number: on/off for switches, a number in range for the others."""
    lv = LEVERS.get(lever)
    if lv is None:
        raise QualityError(f"No lever '{lever}'. Levers: {', '.join(LEVERS)}.")
    if isinstance(lv.original, bool):
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("on", "true", "1", "yes"):
            return True
        if text in ("off", "false", "0", "no"):
            return False
        raise QualityError(f"{lv.label}: use on or off.")
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise QualityError(f"{lv.label}: use a number from {lv.low:g} to {lv.high:g}.") from None
    if isinstance(value, bool) or not lv.low <= num <= lv.high:
        raise QualityError(f"{lv.label}: use a number from {lv.low:g} to {lv.high:g}.")
    return num


def levers_for(level: str, overrides: dict | None = None) -> dict[str, object]:
    if level not in PRESETS:
        raise QualityError(f"No preset '{level}'. Pick one of: {', '.join(LEVELS)}.")
    out = dict(PRESETS[level])
    for k, v in (overrides or {}).items():
        out[k] = parse_value(k, v)
    return out


def env_for(levers: dict) -> dict[str, str]:
    """The start-up values for these levers (none at the game's own values, so Savage Wind's setting applies)."""
    env: dict[str, str] = {}
    g = float(levers.get("grass", 1.0))
    if g != 1.0:
        env["SAVAGEWIND_GRASS_BLADES"] = env["SAVAGEWIND_GRASS_QUADS"] = f"{g:g}"
    o = float(levers.get("objects", 1.0))
    if o != 1.0:
        env["SAVAGEWIND_TRAVERSE_SCALE"] = f"{o:g}"
        env["SAVAGEWIND_FAR_SCALE"] = "1"  # (else the far models and distant trees shrink by the same factor)
    return env


# ---------------------------------------------------------------- AAMP values, changed in place

def aamp_params(data: bytes) -> dict[tuple[int, int], tuple[int, int]]:
    """(object name hash, parameter name hash) -> (type, offset of its value) for every parameter of an AAMP v2
    file (little-endian). Objects with the same name in several lists all count: the last one wins here."""
    return {(o, p): (t, off) for o, p, t, off, _ in _walk_aamp(data)}


def _walk_aamp(data: bytes):
    """(object hash, parameter hash, type, offset of the value, offset of the parameter) for every parameter."""
    if len(data) < 0x30 or data[:4] != b"AAMP" or struct.unpack_from("<I", data, 4)[0] != 2:
        raise QualityError("not an AAMP version 2 file")
    pio_off = struct.unpack_from("<I", data, 0x14)[0]
    seen = set()

    def lists(at: int):
        if at in seen or at + 12 > len(data):
            raise QualityError("a damaged AAMP file")
        seen.add(at)
        _, lrel, lcount, orel, ocount = struct.unpack_from("<IHHHH", data, at)
        for i in range(ocount):
            o = at + orel * 4 + 8 * i
            oname, prel, pcount = struct.unpack_from("<IHH", data, o)
            for j in range(pcount):
                p = o + prel * 4 + 8 * j
                pname, word = struct.unpack_from("<II", data, p)
                yield oname, pname, word >> 24, p + (word & 0xFFFFFF) * 4, p
        for i in range(lcount):
            yield from lists(at + lrel * 4 + 12 * i)

    yield from lists(0x30 + pio_off)


def _data_section(data: bytes) -> tuple[int, int]:
    """Where the AAMP file's data section (the values that aren't strings) starts and ends."""
    pio_off, nl, no, np_, dsize = struct.unpack_from("<5I", data, 0x14)
    start = 0x30 + pio_off + 12 * nl + 8 * no + 8 * np_
    return start, min(start + dsize, len(data))


def edit_aamp(data: bytes, obj: str, param: str, kind: str, value) -> tuple[bytes, int]:
    """Set every obj.param to value; the file keeps its size. AAMP writers store equal values once and let every
    parameter with that value point to it, so a value another parameter shares isn't overwritten: the parameter is
    pointed at a word of the data section that already holds the new value (a refusal when there is none). Returns
    (new data, how many were set)."""
    want = {"bool": 0, "f32": 1, "int": 2}[kind]
    packed = {"bool": lambda v: struct.pack("<I", 1 if v else 0), "f32": lambda v: struct.pack("<f", float(v)),
              "int": lambda v: struct.pack("<i", int(v))}[kind](value)
    oh, ph = zlib.crc32(obj.encode()), zlib.crc32(param.encode())
    out = bytearray(data)
    targets = [w for w in _walk_aamp(data) if (w[0], w[1]) == (oh, ph)]
    for o, p, t, off, at in targets:
        if t != want or off + 4 > len(out):
            raise QualityError(f"{obj}.{param} isn't a {kind} value in this file")
    target_at = {w[4] for w in targets}
    lo, hi = _data_section(data)
    for o, p, t, off, at in targets:
        if out[off:off + 4] == packed:
            continue
        shared = any(w[3] == off and w[4] not in target_at for w in _walk_aamp(bytes(out)))
        if not shared:
            out[off:off + 4] = packed
            continue
        slot = next((s for s in range(max(lo, at + 8) + (-max(lo, at + 8)) % 4, hi - 3, 4)
                     if out[s:s + 4] == packed and (s - at) // 4 < 1 << 24), None)
        if slot is None:
            raise QualityError(f"{obj}.{param} shares its value with other parameters, and the file has no "
                               f"{value} to point it at")
        struct.pack_into("<I", out, at + 4, (t << 24) | (slot - at) // 4)
    return bytes(out), len(targets)


# ---------------------------------------------------------------- the RomFS mods built from the game's files

def recipe_dir(name: str) -> str:
    return os.path.join(EXAMPLES, name)


def load_recipe(name: str) -> dict:
    path = os.path.join(recipe_dir(name), "recipe.json")
    try:
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError) as e:
        raise QualityError(f"can't read {path}: {e}") from None
    if not all(isinstance(r.get(k), str) for k in ("id", "pack", "inner")) or not isinstance(r.get("edits"), dict):
        raise QualityError(f"{path} is damaged")
    return r


def recipe_edits(recipe: dict, levers: dict) -> list[tuple[str, str, str, str, object]]:
    """(lever, file, object, parameter, type, value) for the levers this recipe changes away from the game's own
    value."""
    out = []
    for lever, edits in recipe["edits"].items():
        lv = LEVERS[lever]
        v = levers.get(lever, lv.original)
        if v == lv.original:
            continue
        for e in edits:
            out.append((lever, e["file"], e["object"], e["param"], e.get("type", "bool"), v))
    return out


# ---------------------------------------------------------------- packs (helpers borrowed from other tools)
# quality.py reads and writes packs with tools/bnp.py (Blob) and tools/wiiu_convert.py (sarc_parse, yaz0_compress,
# FormatError), which other people change. They are reached only through these three wrappers, so a change there
# gives one clear error here, and tests/test_quality.py (Borrowed) checks the parts used are still there.
BORROWED = {"bnp": ("Blob",), "wiiu_convert": ("sarc_parse", "yaz0_compress", "FormatError")}


def _borrowed_error(e: Exception) -> QualityError:
    return QualityError(f"tools/bnp.py or tools/wiiu_convert.py changed in a way quality.py doesn't know ({e!r}): "
                        "quality.py needs updating")


def _open_inner(base: bytes, pack: str, inner_name: str):
    """(outer Blob, inner Blob, {name: (start, end)} of the inner SARC's files, inside inner.raw)."""
    try:
        outer = bnp.Blob(base)
        try:
            inner = outer.child(inner_name)
        except (wc.FormatError, ValueError) as e:
            raise QualityError(f"{pack} can't be read ({e})") from None
        if inner is None:
            raise QualityError(f"{pack} has no {inner_name}")
        s = wc.sarc_parse(bytes(inner.raw))
        spans = {n.name: (s.data_offset + n.start, s.data_offset + n.end) for n in s.nodes}
    except (AttributeError, TypeError) as e:
        raise _borrowed_error(e) from None
    return outer, inner, spans


def _repack(outer, inner, inner_name: str, raw: bytes) -> bytes:
    """The outer pack with the inner SARC's bytes replaced (Yaz0 again when it was), keeping its alignment."""
    try:
        data = wc.yaz0_compress(raw, inner.align) if inner.yaz0 else raw
        old_align = outer.open()[inner_name][1]
        outer.set(inner_name, data, old_align)
        return outer.data()
    except (AttributeError, TypeError, KeyError) as e:
        raise _borrowed_error(e) from None


def build_pack(base: bytes, recipe: dict, edits: list) -> tuple[bytes, list[str]]:
    """The pack `base` with the edits made inside its Yaz0 SARC `recipe["inner"]`. Every file inside keeps its
    size (values are changed in place), so the game's size table (RSTB) needs no change. Returns (pack, notes)."""
    outer, inner, spans = _open_inner(base, recipe["pack"], recipe["inner"])
    raw = bytearray(inner.raw)
    notes = []
    for lever, name, obj, param, kind, value in edits:
        if name not in spans:
            raise QualityError(f"{recipe['pack']} > {recipe['inner']} has no {name}")
        a, b = spans[name]
        new, n = edit_aamp(bytes(raw[a:b]), obj, param, kind, value)
        if not n:
            raise QualityError(f"{name} has no {obj}.{param}: is this BotW 1.9.0's {recipe['pack']}?")
        raw[a:b] = new
        notes.append(f"{name}: {obj}.{param} = {value}")
    return _repack(outer, inner, recipe["inner"], bytes(raw)), notes


ROMFS_PREFIXES = ("romfs/", "01007EF00011E000/romfs/", "")


def mod_file(root: str, rel: str) -> str | None:
    for pre in ROMFS_PREFIXES:
        p = os.path.join(root, *(pre + rel).split("/"))
        if os.path.isfile(p):
            return p
    return None


def base_pack(lib: mm.Library, rel: str, own: set[str]) -> tuple[str, str | None]:
    """(the pack to build on, the name of the mod it comes from or None for the game's): the last enabled mod
    (not one of ours) that ships it wins in the game, so our edits go on top of its copy."""
    for m in reversed(lib.enabled()):
        if m["id"] in own:
            continue
        p = mod_file(os.path.join(lib.mods_dir, m["id"]), rel)
        if p:
            return p, m["name"]
    install = lib.require_install()
    p = os.path.join(install, "extracted", "romfs", *rel.split("/"))
    if not os.path.isfile(p):
        raise QualityError(f"Your game's {rel} isn't there ({p}). Set up the game files in Savage Wind first.")
    return p, None


def _file_key(path: str) -> str:
    st = os.stat(path)
    return f"{os.path.normcase(os.path.abspath(path))}|{st.st_size}|{st.st_mtime_ns}"


def build_key(recipe: dict, edits: list, base: str) -> str:
    h = hashlib.sha256(json.dumps([BUILD_VERSION, recipe, [list(map(str, e)) for e in edits], _file_key(base)],
                                  sort_keys=True).encode())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- library state

def open_lib(library) -> mm.Library:
    if isinstance(library, mm.Library):
        return library
    root = mm.resolve_library(library)
    lib = mm.Library.load(root)
    if lib is None:
        raise QualityError(f"No mod library in {root} yet. Run: python tools/modman.py init")
    return lib


def load_state(lib: mm.Library) -> dict:
    data = mm.read_json(os.path.join(lib.root, STATE)) or {}
    if data and data.get("tool") != TOOL_ID:
        raise QualityError(f"{os.path.join(lib.root, STATE)} wasn't written by quality.py")
    data.setdefault("level", "original")
    data.setdefault("base", data["level"] if data["level"] in LEVELS else "original")
    data.setdefault("overrides", {})
    data.setdefault("built", {})
    data.setdefault("roles", {})
    return data


def save_state(lib: mm.Library, st: dict) -> None:
    st = {**st, "tool": TOOL_ID, "format": FORMAT}
    mm.write_json(os.path.join(lib.root, STATE), st)


def own_ids() -> dict[str, str]:
    """recipe folder -> the id of the mod it builds."""
    return {name: load_recipe(name)["id"] for name in sorted({lv.recipe for lv in LEVERS.values() if lv.recipe})}


# Remove Grass is found by its file's SHA-256, so another terrain mod (also only a MainField.tscb) is never taken
# for it and turned off. The file of Sen Uii's "Remove Water & Grass" (GameBanana 328281), option "Remove Grass"
# (the water options break the game and are not listed).
NO_GRASS_SHA256 = frozenset({"9c17d2cbdd0699006a3798cc2afe7344ccf222cee210c877d012dbb6488a2feb"})
NO_GRASS_FILE = "Terrain/A/MainField.tscb"


def is_no_grass(root: str) -> bool:
    """A mod whose only game file is Remove Grass's terrain index (Terrain/A/MainField.tscb, by its SHA-256)."""
    files = []
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            rel = os.path.relpath(os.path.join(dirpath, n), root).replace("\\", "/")
            if rel.lower() in ("mod.toml",) or rel.lower().endswith((".txt", ".md", ".png", ".jpg", ".json")):
                continue
            files.append(rel)
    if len(files) != 1 or not any(files[0].lower() == (pre + NO_GRASS_FILE).lower() for pre in ROMFS_PREFIXES):
        return False
    h = hashlib.sha256()
    try:
        with open(os.path.join(root, *files[0].split("/")), "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return False
    return h.hexdigest() in NO_GRASS_SHA256


def find_role(lib: mm.Library, st: dict, role: str) -> dict | None:
    pinned = st["roles"].get(role)
    if pinned:
        hit = next((m for m in lib.mods if m["id"] == pinned), None)
        if hit:
            return hit
    if role == "no_grass":
        for m in lib.mods:
            if is_no_grass(os.path.join(lib.mods_dir, m["id"])):
                return m
    return None


# ---------------------------------------------------------------- set / toggle

def _log(log, msg: str) -> None:
    if log:
        log(msg)


def apply_levers(lib: mm.Library, st: dict, levers: dict, log=None) -> dict:
    """Make the library match the levers: build, add, turn on or off our mods and the role mods. Returns what
    changed: {"on": [...], "off": [...], "built": [...], "notes": [...]}. Changes only our mods and role mods."""
    res = {"on": [], "off": [], "built": [], "notes": []}
    ids = own_ids()
    own = set(ids.values())
    for folder, mod_id in ids.items():
        recipe = load_recipe(folder)
        edits = recipe_edits(recipe, levers)
        idx = next((i for i, m in enumerate(lib.mods) if m["id"] == mod_id), None)
        if not edits:
            if idx is not None and lib.mods[idx]["enabled"]:
                lib.mods[idx]["enabled"] = False
                res["off"].append(lib.mods[idx]["name"])
            continue
        base, from_mod = base_pack(lib, recipe["pack"], own)
        key = build_key(recipe, edits, base)
        if idx is not None and st["built"].get(mod_id) == key and os.path.isdir(lib.mod_path(mod_id)):
            if not lib.mods[idx]["enabled"]:
                lib.mods[idx]["enabled"] = True
                res["on"].append(lib.mods[idx]["name"])
        else:
            _log(log, f"Building {recipe.get('name', mod_id)} from "
                      f"{('the ' + from_mod + ' mod') if from_mod else 'your game'}'s {recipe['pack']}...")
            with open(base, "rb") as f:
                data, notes = build_pack(f.read(), recipe, edits)
            tmp = tempfile.mkdtemp(prefix="quality-build-")
            try:
                folder_out = os.path.join(tmp, mod_id)
                dest = os.path.join(folder_out, "romfs", *recipe["pack"].split("/"))
                os.makedirs(os.path.dirname(dest))
                with open(dest, "wb") as f:
                    f.write(data)
                shutil.copyfile(os.path.join(recipe_dir(folder), "mod.toml"), os.path.join(folder_out, "mod.toml"))
                with open(os.path.join(folder_out, "built.txt"), "w", encoding="utf-8") as f:
                    f.write(f"Built by tools/quality.py from {base}\n" + "".join(f"{n}\n" for n in notes))
                if idx is not None:
                    mm.remove_mod(lib, mod_id)
                elif os.path.isdir(os.path.join(lib.mods_dir, mod_id)):
                    raise QualityError(f"{os.path.join(lib.mods_dir, mod_id)} is there but the library doesn't "
                                       "list it: move that folder away and try again.")
                entry, _scan, _notes = mm.add_mod(lib, folder_out)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            st["built"][mod_id] = key
            res["built"].append(entry["name"])
            if from_mod:
                res["notes"].append(f"{entry['name']} is built on {from_mod}'s {recipe['pack']}: set the preset again "
                                    f"after you change {from_mod}.")
        # ours go last, so they win over another mod's copy of the same pack (they hold its changes)
        i = next(i for i, m in enumerate(lib.mods) if m["id"] == mod_id)
        lib.mods.append(lib.mods.pop(i))
        if recipe["pack"] == BOOTUP_PACK:
            res["notes"].append(BOOTUP_NOTE)
    role = find_role(lib, st, "no_grass")
    want = bool(levers.get("no_grass"))
    if role is None:
        if want:
            res["notes"].append("Remove Grass isn't in the library, so this preset keeps the grass (shorter). "
                                "To add it: docs/performance.md.")
    elif role["enabled"] != want:
        role["enabled"] = want
        res["on" if want else "off"].append(role["name"])
    lib.save()
    return res


def set_quality(library, level: str, *, overrides: dict | None = None, deploy: bool = False, log=None) -> dict:
    """Pick a preset for a mod library (a folder, None for modman's default, or a modman Library). `overrides`
    changes single levers on top of it (the level is then "custom"). With deploy=True, runs modman deploy after.
    Returns {"level", "base", "levers", "env", "settings", "changes", "deploy_needed", "deployed"}."""
    lib = open_lib(library)
    if level == CUSTOM:
        level = load_state(lib)["base"]
    if level not in LEVELS:
        raise QualityError(f"No preset '{level}'. Pick one of: {', '.join(LEVELS)}.")
    overrides = {k: parse_value(k, v) for k, v in (overrides or {}).items()}
    overrides = {k: v for k, v in overrides.items() if PRESETS[level][k] != v}
    levers = levers_for(level, overrides)
    st = load_state(lib)
    before = [(m["id"], m["enabled"]) for m in lib.mods]
    changes = apply_levers(lib, st, levers, log)
    after = [(m["id"], m["enabled"]) for m in lib.mods]
    st.update(level=CUSTOM if overrides else level, base=level, overrides=overrides, levers=levers,
              env=env_for(levers))
    save_state(lib, st)
    out = {"level": st["level"], "base": level, "levers": levers, "env": st["env"], "settings": dict(SETTINGS[level]),
           "changes": changes, "deploy_needed": before != after or bool(changes["built"]), "deployed": False}
    if deploy:
        try:
            launch = mm.deploy(lib)
        except mm.ModmanError as e:  # (the preset is set; only the deploy waits: say why)
            out["deploy_error"] = str(e)
        else:
            out["deployed"] = True
            out["deploy_text"] = mm.format_deploy(launch)
    return out


def toggle(library, lever: str, value, *, deploy: bool = False, log=None) -> dict:
    """Change one lever on top of the library's preset."""
    lib = open_lib(library)
    st = load_state(lib)
    ov = dict(st["overrides"])
    ov[lever] = parse_value(lever, value)
    return set_quality(lib, st["base"], overrides=ov, deploy=deploy, log=log)


def status(library) -> dict:
    lib = open_lib(library)
    st = load_state(lib)
    levers = levers_for(st["base"], st["overrides"])
    role = find_role(lib, st, "no_grass")
    mods, notes = [], []
    for folder, mod_id in own_ids().items():
        m = next((m for m in lib.mods if m["id"] == mod_id), None)
        if m:
            mods.append({"id": m["id"], "name": m["name"], "enabled": m["enabled"]})
            if m["enabled"] and load_recipe(folder)["pack"] == BOOTUP_PACK:
                notes.append(BOOTUP_NOTE)
    if role:
        mods.append({"id": role["id"], "name": role["name"], "enabled": role["enabled"], "role": "no_grass"})
    return {"level": st["level"], "base": st["base"], "levers": levers, "env": env_for(levers),
            "settings": dict(SETTINGS[st["base"]]), "mods": mods, "notes": notes, "library": lib.root}


def launch_env(library) -> dict[str, str]:
    """The start-up values the library's preset needs (for modman run, or any launcher)."""
    return status(library)["env"]


def run(library, *, port: int | None = None, no_cheats: bool = False) -> int:
    """modman run with the preset's start-up values (it passes variables it doesn't own on to the game)."""
    lib = open_lib(library)
    env = launch_env(lib)
    for k in ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(env)
    if env:
        mm.say("Start-up values: " + ", ".join(f"{k}={v}" for k, v in env.items()))
    return mm.run(lib, port=port, no_cheats=no_cheats)


# ---------------------------------------------------------------- text

def show_value(lever: str, v) -> str:
    lv = LEVERS[lever]
    if isinstance(lv.original, bool):
        return "on" if v else "off"
    if lever == "shadow_range":
        return f"{v:g} m"
    return f"{v:.0%}"


def settings_text(level: str) -> list[str]:
    s = SETTINGS[level]
    lines = ["Savage Wind settings to choose (in the game: F1 or Esc, Video):"]
    for k, label in SETTING_LABELS.items():
        v = s.get(k)
        if v is None or (k == "upscaler_quality" and s.get("upscaler") == "off"):
            continue
        lines.append(f"  {label}: {VALUE_LABELS.get(v, v)}")
    lines.append(f"  Tip: {SETTINGS_TIP}")
    return lines


def list_text(current: str | None = None) -> str:
    w = max(len(lv.label) for lv in LEVERS.values()) + 2
    head = "".ljust(w) + "".join(LABELS[lvl].ljust(10) for lvl in LEVELS)
    lines = [head]
    for lv in LEVERS.values():
        lines.append(lv.label.ljust(w) + "".join(show_value(lv.id, PRESETS[lvl][lv.id]).ljust(10) for lvl in LEVELS))
    lines.append("")
    for lvl in LEVELS:
        mark = "  <- this library" if lvl == current else ""
        lines.append(f"{LABELS[lvl]}: {BLURBS[lvl]}{mark}")
    lines.append("")
    lines.append("Pick one: python tools/quality.py set low|medium|high|original --deploy")
    return "\n".join(lines)


def result_text(r: dict) -> str:
    lines = [f"Quality: {LABELS.get(r['level'], r['level'])}" +
             (f" (from {LABELS[r['base']]})" if r["level"] == CUSTOM else "")]
    ch = r["changes"]
    if ch["built"]:
        lines.append("Built: " + ", ".join(ch["built"]))
    if ch["on"]:
        lines.append("Turned on: " + ", ".join(ch["on"]))
    if ch["off"]:
        lines.append("Turned off: " + ", ".join(ch["off"]))
    for n in ch["notes"]:
        lines.append(f"Note: {n}")
    lines.append("Start-up values: " + (", ".join(f"{k}={v}" for k, v in r["env"].items()) or "none"))
    lines += settings_text(r["base"])
    if r.get("deploy_error"):
        lines.append(f"Not deployed: {r['deploy_error']}")
        lines.append("Then: python tools/modman.py deploy, and python tools/quality.py run")
    elif r.get("deployed"):
        lines.append(r["deploy_text"])
        lines.append("Start the game with: python tools/quality.py run")
    elif r["deploy_needed"]:
        lines.append("Next: python tools/modman.py deploy, then python tools/quality.py run")
    else:
        lines.append("Start the game with: python tools/quality.py run")
    return "\n".join(lines)


# ---------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    lib_help = "the mod library folder (default: as modman: SWMOD_LIBRARY, else next to Savage Wind)"
    common = argparse.ArgumentParser(add_help=False)  # (SUPPRESS: given before the command, it isn't reset after)
    common.add_argument("--library", help=lib_help, default=argparse.SUPPRESS)
    ap = argparse.ArgumentParser(prog="quality.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--library", help=lib_help)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", parents=[common], help="the presets")
    p = sub.add_parser("set", parents=[common], help="pick a preset")
    p.add_argument("level", choices=LEVELS)
    p.add_argument("--deploy", action="store_true", help="run modman deploy after")
    p = sub.add_parser("toggle", parents=[common], help="change one lever on top of the preset")
    p.add_argument("lever", choices=list(LEVERS))
    p.add_argument("value", help="on/off, or a number")
    p.add_argument("--deploy", action="store_true", help="run modman deploy after")
    sub.add_parser("status", parents=[common], help="what the library uses now")
    sub.add_parser("json", parents=[common], help="the presets as JSON")
    sub.add_parser("env", parents=[common], help="the start-up values as PowerShell lines")
    p = sub.add_parser("run", parents=[common], help="start the game with the preset (modman run)")
    p.add_argument("--port", type=mm.port_arg, help="the control port (as modman run --port)")
    p.add_argument("--no-cheats", action="store_true")
    a = ap.parse_args(argv)
    say = mm.say
    try:
        if a.cmd == "json":
            print(json.dumps(presets(), indent=1))
        elif a.cmd == "list":
            current = None
            try:
                lib = mm.Library.load(mm.resolve_library(a.library))
                current = load_state(lib)["level"] if lib else None
            except (mm.ModmanError, QualityError):
                pass
            say(list_text(current))
        elif a.cmd in ("set", "toggle"):
            r = (set_quality(a.library, a.level, deploy=a.deploy, log=say) if a.cmd == "set"
                 else toggle(a.library, a.lever, a.value, deploy=a.deploy, log=say))
            say(result_text(r))
            return 1 if r.get("deploy_error") else 0
        elif a.cmd == "status":
            s = status(a.library)
            say(f"Quality: {LABELS.get(s['level'], s['level'])} (library {s['library']})")
            for k, v in s["levers"].items():
                say(f"  {LEVERS[k].label}: {show_value(k, v)}")
            for m in s["mods"]:
                say(f"  mod {m['name']}: {'on' if m['enabled'] else 'off'}")
            for n in s["notes"]:
                say(f"Note: {n}")
            say("Start-up values: " + (", ".join(f"{k}={v}" for k, v in s["env"].items()) or "none"))
            say("\n".join(settings_text(s["base"])))
        elif a.cmd == "env":
            env = launch_env(a.library)
            for k in ENV_KEYS:
                say(f"$env:{k} = '{env[k]}'" if k in env else f"Remove-Item Env:{k} -ErrorAction SilentlyContinue")
        elif a.cmd == "run":
            return run(a.library, port=a.port, no_cheats=a.no_cheats)
        return 0
    except (QualityError, mm.ModmanError) as e:
        say(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
