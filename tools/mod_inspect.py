#!/usr/bin/env python3
"""Say what kind of BotW mod a folder or .zip is, and whether our loader can use it.

It only reads file names and small headers, and never changes the mod.

    python tools/mod_inspect.py <mod folder or .zip> [--json]

Covers Switch RomFS mods, ExeFS patches (.ips / .pchtxt), exlaunch / Skyline code mods,
Atmosphere cheats, BCML (.bnp) and UKMM packages, Wii U content folders and Cemu graphic packs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

TITLE = "01007ef00011e000"      # BotW, Switch
DLC_TITLE = "01007ef00011f001"  # BotW DLC, Switch
WIIU_TITLES = ("00050000101c9300", "00050000101c9400", "00050000101c9500")  # US, EU, JP
TARGET_VERSION = "1.9.0"
# First 8 bytes of the `main` build id, from Savage Wind's runtime/game_versions.h.
KNOWN_BUILDS = {"cd57b23fa4bbad65": "1.9.0", "de0b6ac4efc1dda7": "1.8.2"}

WORKS, CONVERT, MERGE, LATER, RETARGET, REWRITE = "works", "convert", "merge", "later", "retarget", "rewrite"
PARTIAL = "partial"
VERDICTS = {
    WORKS: "works as is",
    CONVERT: "works after conversion on your PC",
    PARTIAL: "partly works: the parts the loader can't use yet are named, and it isn't tried in the game yet",
    MERGE: "works after merging in UKMM (Switch mode)",
    LATER: "planned, not supported yet",
    RETARGET: "needs porting to game version 1.9.0",
    REWRITE: "can't be used; must be remade as a native mod",
}
ORDER = [WORKS, CONVERT, PARTIAL, MERGE, LATER, RETARGET, REWRITE]
USABLE = {WORKS, CONVERT}  # (MERGE isn't: modman and romfs_mods refuse the package itself, and take UKMM's output)
BNP_ADD = "tools/modman.py add installs it (tools/bnp.py)"
BCML2_LOGS = ("savedata.sarc", "deepmerge.yml")   # BCML 2's logs (and texts_*), which nothing here merges
UKMM_ADD = ("tools/modman.py add builds it from your game files (tools/ukmm.py): its options, packs, plain files, "
            "the actor parameter files it can merge and a size table. Physics, animation sequences, texts, actor "
            "info, game data, maps and the other kinds UKMM keeps as its own data aren't merged yet: they are left "
            "out and named (`python tools/ukmm.py info` says how much of the package that is). Not tried in the "
            "game yet (see docs/ukmm.md)")
UKMM_ANY = ("cross-platform, so it holds only data UKMM merges (no models or textures), and most of those kinds "
            "(BYML, texts, game data, actor info, maps) aren't merged here yet")
UKMM_OUTPUT = "then use UKMM's output folder"

# Kinds of files UKMM can't merge or move between Wii U and Switch.
NOT_MERGEABLE = {
    "models/textures": (".bfres", ".sbfres", ".bntx", ".bflim", ".sbflim", ".bitemico", ".sbitemico", ".gtx"),
    "audio": (".bfstm", ".bars", ".bfwav", ".bwav", ".bfsar"),
    "physics": (".hkx", ".hknm2", ".shknm2", ".hksc", ".shksc", ".hkrb", ".shkrb", ".hkrg", ".shkrg"),
    "movies": (".mp4",),
    "fonts": (".bffnt", ".bfotf", ".bfttf"),
}

# Wii U models, Tex1/Tex2 textures and item icons, which tools/wiiu_convert.py converts with tools/wiiu_model (its
# MODEL_EXT; a Yaz0 copy adds an "s": .sbfres)
WIIU_MODEL_EXT = {"bfres", "bitemico"}
TEX_RE = re.compile(r"^(.+)\.tex([12])\.s?bfres$")
# What tools/wiiu_convert.py keeps as Wii U data (the game can't read it), by file extension; a Yaz0 copy adds an "s"
# (.sbfres). The same kinds and extensions as its PLATFORM_EXT (tests/test_mod_inspect.py keeps the two the same),
# plus layout archives (.blarc), which hold only layouts and their textures. Havok physics files (.hk*) are kept too.
WIIU_KEPT = {
    "model/texture": {"bntx", "gtx", "bflim", "bmaptex", "bstftex", "breviewtex"},
    "audio": {"bfstm", "bfwav", "bars", "bfsar", "bwav", "bagst", "blal", "bfgrp", "bfwsd", "bfstp", "baatn",
              "baacl", "baroc"},
    "shader": {"sharc", "sharcb", "sharcfb", "bfsha", "bnsh", "bushvt"},
    "effect": {"ptcl", "esetlist", "bptcl"},
    "font": {"bffnt", "bfotf", "bfttf"},
    "movie": {"mp4", "webm", "mov", "mkv", "bfmov"},
    "UI layout": {"bflyt", "bflan", "blarc"},
    "event flow": {"bfevfl", "bfevtm"},
    "effect/sound link": {"belnk", "bslnk"},
    "object placement (PrOD)": {"blwp"},
    "terrain": {"hght", "mate", "extm", "tscb", "tera"},
    "ecosystem": {"beco"},
}
WIIU_KEPT_EXT = {ext: kind for kind, exts in WIIU_KEPT.items() for ext in exts}
# Kinds above that tools/wiiu_convert.py uses as they are in BotW's own form: its event flows and timelines are
# little-endian on the Wii U too (wiiu_convert.is_le_event_flow)
WIIU_SAME_KINDS = {"event flow"}
RSTB_EXT = ("rsizetable", "srsizetable")  # the size table: wiiu_convert leaves a Wii U one out
JUNK = {"thumbs.db", "desktop.ini", "__macosx", ".ds_store"}
# Top-level folders of BotW's RomFS, to recognise a bare RomFS tree (lowercase; tools/romfs_mods.py uses these too)
ROMFS_TOP = {"actor", "camera", "cooking", "ecosystem", "effect", "elink2", "event", "eventflow", "font", "game",
             "layout", "local", "map", "message", "model", "movie", "navmesh", "pack", "physics", "quest",
             "shader", "slink2", "sound", "system", "terrain", "ui", "voice"}

STRUCTURAL = {"romfs", "exefs", "content", "aoc", "cheats", "logs", "options", "atmosphere", "contents",
              "exefs_patches", "skyline", TITLE, DLC_TITLE}
# A bare RomFS tree (Pack/, Model/... with no romfs/ or content/ folder): its platform is read from a few files'
# headers (byte order), these kinds (a Yaz0 copy adds an "s": .sbfres)
BARE_WIIU = "Wii U files in bare RomFS folders"
SNIFF_EXT = {"pack", "sarc", "bactorpack", "beventpack", "bquestpack", "byml", "bgdata", "bgsvdata", "mubin", "bfres",
             "msbt", "blarc", "bfarc"}
SNIFF_MAX = 8
# Files of image and 3D editors that mods sometimes ship next to the game files: the game reads none of them
EDITING_EXT = {"dds", "gtx", "png", "psd", "tga", "xcf", "kra", "blend", "fbx", "dae", "obj"}
SHADER_RE = re.compile(r"(^|/)[0-9a-f]{16}_[0-9a-f]{16}_(vs|ps|gs)(_msl)?\.txt$")
WIIU_DATA_RE = re.compile(r"(^|/)(content|aoc)/.")
WIIU_GAME_RE = re.compile(r"(^|/)(content|aoc/0010)/.")  # the files wiiu_convert converts (or keeps)
OVERLAY_DLC_RE = re.compile(r"(^|/)aoc/0010/.")
ANCHOR_RE = re.compile(r"(^|/)(content|romfs)/.")
ROMFS_RE = re.compile(r"(^|/)romfs/.")
SUBSDK_RE = re.compile(r"(^|/)(subsdk[1-9]|main\.npdm)$")
SKYLINE_RE = re.compile(r"(^|/)skyline/plugins/[^/]+\.nro$")
CHEAT_RE = re.compile(r"(^|/)cheats/([0-9a-f]{16})\.txt$")
NSOBID_RE = re.compile(rb"@nsobid-([0-9a-fA-F]{16,64})")


@dataclass
class Finding:
    kind: str
    platform: str  # "switch", "wiiu" or "any"
    verdict: str
    detail: str


class Source:
    """A mod as a list of relative file paths (with "/" separators) plus a way to read them."""

    def __init__(self, files: list[str]):
        files = sorted(files)
        # Archives often wrap everything in one folder: look inside it, but keep folders that mean something
        # (romfs/, a title id, and Pack/ or Model/ of a bare RomFS tree).
        self.prefix = ""
        while files and all("/" in f for f in files):
            top = {f.split("/", 1)[0] for f in files}
            if len(top) != 1 or next(iter(top)).lower() in STRUCTURAL | ROMFS_TOP:
                break
            self.prefix += top.pop() + "/"
            files = [f.split("/", 1)[1] for f in files]
        self.files = files

    def read(self, rel: str, limit: int = 65536) -> bytes:
        raise NotImplementedError


class FolderSource(Source):
    def __init__(self, root: Path):
        self.root = root
        found = []
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                found.append((Path(dirpath) / n).relative_to(root).as_posix())
        super().__init__(found)

    def read(self, rel: str, limit: int = 65536) -> bytes:
        with open(self.root / (self.prefix + rel), "rb") as f:
            return f.read(limit)


class ZipSource(Source):
    def __init__(self, path: Path):
        self.zf = zipfile.ZipFile(path)
        super().__init__([n for n in self.zf.namelist() if not n.endswith("/")])

    def read(self, rel: str, limit: int = 65536) -> bytes:
        with self.zf.open(self.prefix + rel) as f:
            return f.read(limit)


def open_source(path: Path) -> Source:
    if path.is_dir():
        return FolderSource(path)
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic.startswith(b"PK"):
        return ZipSource(path)
    raise ValueError(
        f"{path.name} isn't a folder or a .zip. Extract it first (7-Zip opens .7z, .rar and .bnp), "
        "then run this on the folder. A .bnp (BCML package): python tools/modman.py add installs it."
    )


class ArchiveSource(Source):
    """A .bnp or other 7-Zip archive, listed by tools/bnp.py (7-Zip or Windows' tar.exe); a file is read by
    extracting it to memory. Only for this tool's command line: the installers extract archives themselves."""

    def __init__(self, path: Path):
        import bnp  # (bnp imports this module)
        self.path, self._bnp = path, bnp
        self._listed = bnp.list_archive(str(path))   # (a file is read with the tool that listed it, by its own name)
        super().__init__([m.name for m in self._listed[0] if not m.is_dir])

    def read(self, rel: str, limit: int = 65536) -> bytes:
        return self._bnp.read_member(str(self.path), self.prefix + rel, max(limit, 1 << 20),
                                     listed=self._listed)[:limit]


def _text(src: Source, rel: str) -> str:
    return src.read(rel).decode("utf-8", "replace")


def _count_not_mergeable(paths: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in paths:
        for label, exts in NOT_MERGEABLE.items():
            if p.endswith(exts):
                counts[label] = counts.get(label, 0) + 1
    return counts


def _describe_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{n} {label}" for label, n in counts.items())


def _build_version(build_id: str) -> str | None:
    return KNOWN_BUILDS.get(build_id[:16].lower())


def _ukmm(src: Source, low: list[str]) -> Finding | None:
    if "meta.yml" not in low or "manifest.yml" not in low:
        return None
    import ukmm  # (here, not at the top: ukmm imports romfs_mods, which imports this module)
    text = src.read(src.files[low.index("meta.yml")], ukmm.MAX_META).decode("utf-8-sig", "replace")
    try:
        value = ukmm.parse_meta(text).platform        # (the rule modman add goes by)
    except ukmm.UkmmError as e:
        return Finding("UKMM package", "any", LATER, f"its meta.yml can't be read ({e}): tools/modman.py add "
                                                     "refuses it")
    # (tools/romfs_mods.py and tools/wiiu_convert.py refuse a UKMM package itself; modman builds a Switch one)
    if value == "any":
        return Finding("UKMM package", "any", PARTIAL, f"{UKMM_ANY}. {UKMM_ADD}")
    if value not in ("switch", "wiiu"):
        return Finding("UKMM package", "any", LATER,
                       f"its meta.yml names no platform this loader knows ({value or 'none'}; UKMM writes Switch, "
                       "Wii U or Universal): tools/modman.py add refuses it")
    if value == "wiiu":
        manifest = src.read(src.files[low.index("manifest.yml")], 4 * 1024 * 1024).decode("utf-8", "replace")
        listed = [ln.strip()[2:].strip().lower() for ln in manifest.splitlines() if ln.strip().startswith("- ")]
        counts = _count_not_mergeable(listed)
        if not counts:
            return Finding("UKMM package", "wiiu", MERGE,
                           f"Wii U only, but all {len(listed)} file(s) can be merged: install it in UKMM (Switch "
                           f"mode), deploy there, {UKMM_OUTPUT}")
        return Finding("UKMM package", "wiiu", LATER,
                       f"Wii U only: {len(listed)} file(s), with {_describe_counts(counts)} that can't be converted "
                       "yet. To use the rest: deploy it in UKMM (Wii U mode), then add UKMM's output folder with "
                       "tools/modman.py add (it converts what it can)")
    return Finding("UKMM package", "switch", PARTIAL, UKMM_ADD)


def _bnp(src: Source, low: list[str]) -> Finding | None:
    if "info.json" not in low:
        return None
    if not any(p.startswith(("logs/", "content/", "aoc/", TITLE + "/", DLC_TITLE + "/")) for p in low):
        return None
    platform = ""
    try:
        platform = str(json.loads(_text(src, src.files[low.index("info.json")])).get("platform", "")).lower()
    except (ValueError, AttributeError):
        pass
    if not platform:
        platform = "wiiu" if any(p.startswith(("content/", "aoc/")) for p in low) else "switch"
    wiiu = "wii" in platform
    if any(p.startswith("logs/") for p in low):
        # a packed BNP: its packs hold only what the mod changes. modman add installs a Switch one (tools/bnp.py:
        # options, packs filled from the game, every BCML 3 log merged, size table), and a Wii U one converted first
        # (bnp.install_wiiu); romfs_mods and wiiu_convert refuse both
        if not wiiu:
            old = sorted({n for p in low if "/logs/" in f"/{p}" and ((n := p.rsplit("/", 1)[-1]) in BCML2_LOGS
                                                                      or n.startswith("texts_"))})
            if old:
                return Finding("BCML mod (BNP)", "switch", PARTIAL,
                               f"BCML 2 package: {BNP_ADD}, but its {', '.join(old)} log(s) are BCML 2's and aren't "
                               "merged (BCML 3 refuses such packages too): the parts of the mod that need them may "
                               "not work")
            return Finding("BCML mod (BNP)", "switch", WORKS,
                           f"BCML package: {BNP_ADD}: its options, its packs filled from your game, its logs merged "
                           "(actor parameters, animation lists, game data, save data, actor info, texts, drops, "
                           "shops, maps, quests) and a size table. Several packages' packs aren't merged with each "
                           "other yet (see docs/bnp.md)")
        return Finding("BCML mod (BNP)", "wiiu", PARTIAL,
                       f"Wii U BCML package: {BNP_ADD} as a Switch one: its chosen options converted with "
                       "tools/wiiu_convert.py, its logs made the Switch's and merged, and a size table. What doesn't "
                       "convert (voice lines, models the converter can't read yet) gives way to the game's files and "
                       "is named. Not tried in the game yet")
    if wiiu:
        return Finding("BCML mod (BNP)", "wiiu", CONVERT, "Wii U BNP: its contents are checked below")
    return Finding("BCML mod (BNP)", "switch", WORKS, "a BCML mod folder (no logs/): its files are used as they are")


def _graphic_packs(src: Source, low: list[str]) -> list[Finding]:
    out = []
    for i, p in enumerate(low):
        if not (p == "rules.txt" or p.endswith("/rules.txt")):
            continue
        text = _text(src, src.files[i]).lower()
        if "[definition]" not in text:
            continue
        base = p[: -len("rules.txt")]
        inside = [q for q in low if q.startswith(base)]
        name = base.rstrip("/") or "(root)"
        if not any(t in text for t in WIIU_TITLES):
            out.append(Finding("Cemu graphic pack", "wiiu", REWRITE, f"{name}: not for BotW (no BotW title id)"))
            continue
        asm = [q for q in inside if q.endswith(".asm") or q.endswith("/patches.txt")]
        shaders = [q for q in inside if SHADER_RE.search(q)]
        has_data = any(WIIU_DATA_RE.search(q[len(base):]) for q in inside)
        if asm:
            out.append(Finding("Cemu code patch (PowerPC)", "wiiu", REWRITE,
                               f"{name}: {len(asm)} patch file(s). Wii U code; remake it as a native mod "
                               "(FPS, FOV and draw distance are already Savage Wind options)"))
        if shaders:
            out.append(Finding("Cemu shader replacement", "wiiu", REWRITE,
                               f"{name}: {len(shaders)} Wii U shader(s); Cemu only"))
        if not (asm or shaders or has_data):
            out.append(Finding("Cemu settings preset", "wiiu", REWRITE,
                               f"{name}: Cemu setting only; use Savage Wind's own options"))
    return out


def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1] if "." in name else ""


def wiiu_kept_kind(path: str) -> str | None:
    """The kind of a Wii U file tools/wiiu_convert.py keeps as Wii U data, by its name (None: one it converts, or
    may: packs, BYML, MSBT, AAMP, text...)."""
    ext = _ext(path.lower())
    if ext.startswith(("hk", "shk")):
        return "Havok physics"
    return WIIU_KEPT_EXT.get(ext) or (WIIU_KEPT_EXT.get(ext[1:]) if ext[:1] == "s" else None)


def _overlay(low: list[str]) -> tuple[list[str], list[str]]:
    """A Switch overlay folder: aoc/0010/ (DLC files) next to bare RomFS folders (Actor/, Pack/...), with no content/
    or romfs/ folder. tools/romfs_mods.py deploys it as a Switch mod; a Wii U mod keeps its game files in content/.
    Returns (its files, the DLC ones), or two empty lists."""
    if any(ANCHOR_RE.search(p) for p in low):
        return [], []
    aoc: dict[str, list[str]] = {}  # the folder holding aoc/ -> its aoc/0010 files
    for p in low:
        m = OVERLAY_DLC_RE.search(p)
        if m:
            aoc.setdefault(p[:m.start() + len(m.group(1))], []).append(p)
    files: list[str] = []
    dlc: list[str] = []
    for base, inside in aoc.items():
        beside = [q for q in low if q.startswith(base) and "/" in q[len(base):]
                  and q[len(base):].split("/", 1)[0] in ROMFS_TOP]
        if beside:
            dlc += inside
            files += beside + inside
    return sorted(set(files)), dlc


def _wiiu_data(low: list[str], overlay: list[str]) -> Finding | None:
    skip = set(overlay)
    data = [p for p in low if WIIU_DATA_RE.search(p) and p not in skip]
    if not data:
        return None
    # wiiu_convert's game files: the RSTB (left out) and files that aren't game data (Thumbs.db...) don't count
    game = [p for p in data if WIIU_GAME_RE.search(p) and _ext(p) not in RSTB_EXT
            and not any(x in JUNK or x.startswith(".") for x in p.split("/"))]
    kept: dict[str, int] = {}
    same = 0
    for p in game:
        kind = wiiu_kept_kind(p)
        if kind in WIIU_SAME_KINDS:
            same += 1
        elif kind:
            kept[kind] = kept.get(kind, 0) + 1
    models, no_mips, lone = wiiu_model_files(game)
    if lone:
        kept["model/texture"] = kept.get("model/texture", 0) + len(lone)
    if not game:
        return Finding("Wii U game data", "wiiu", LATER,
                       f"{len(data)} file(s), but none tools/wiiu_convert.py can use (only the size table (RSTB), or "
                       "files that aren't game data)")
    used = f"; its {same} event flow(s) are used as they are (little-endian on the Wii U too)" if same else ""
    if same == len(game):
        return Finding("Wii U game data", "wiiu", WORKS,
                       f"{same} file(s), all event flows: little-endian on the Wii U too, so used as they are "
                       "(tools/modman.py add copies them)")
    if sum(kept.values()) + same == len(game):
        # only audio, layouts, physics...: tools/wiiu_convert.py can't convert any of it yet
        return Finding("Wii U game data", "wiiu", LATER,
                       f"{len(game)} file(s), {'' if same else 'all '}{_describe_counts(kept)}: tools/wiiu_convert.py "
                       f"can't convert these yet (it would keep them as Wii U data, which the game can't read){used}")
    what = "text, maps, actor data" + (f", {len(models) - len(lone)} model/texture file(s)" if models else "")
    detail = (f"{len(game)} file(s): tools/wiiu_convert.py converts them on your PC ({what}); tools/modman.py add "
              "does it for you")
    if models:
        detail += (". Models and textures: modman add uses your game files for them (vanilla materials, mipmaps, size "
                   "table); not proven on real Wii U files yet, so check them in game")
    if no_mips:
        detail += (f". {len(no_mips)} X.Tex1 without its X.Tex2 (mipmaps): its textures keep the game's mipmaps only "
                   "where unchanged, the others get none")
    if kept:
        detail += f". Not converted yet, kept as Wii U data: {_describe_counts(kept)}"
        if lone:
            detail += f" (incl. {len(lone)} X.Tex2 without its X.Tex1)"
    if same:
        detail += f". Used as they are: {same} event flow(s) (little-endian on the Wii U too)"
    return Finding("Wii U game data", "wiiu", CONVERT, detail)


def wiiu_model_files(game: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(model/texture files tools/wiiu_convert.py converts, X.Tex1 files with no X.Tex2 in the mod, X.Tex2 files with
    no X.Tex1: kept as Wii U data), by name. Pairs inside packs are not seen here; the converter pairs those."""
    models = [p for p in game if _ext(p) in WIIU_MODEL_EXT or (_ext(p)[:1] == "s" and _ext(p)[1:] in WIIU_MODEL_EXT)]
    tex = {}
    for p in models:
        m = TEX_RE.match(p.rsplit("/", 1)[-1])
        if m:
            tex.setdefault(m.group(1), {})[m.group(2)] = p
    no_mips = sorted(v["1"] for v in tex.values() if "1" in v and "2" not in v)
    lone = sorted(v["2"] for v in tex.values() if "2" in v and "1" not in v)
    return models, no_mips, lone


def bare_root(files: list[str]) -> str | None:
    """Where a bare RomFS tree starts: the shallowest folder holding RomFS top folders (Actor/, Pack/, Model/...),
    tools/romfs_mods.py's rule for a mod with no romfs/ folder. "" for the mod's top, else "a/b/" as the mod spells
    it; None when there is none, or several side by side."""
    roots: dict[tuple[str, ...], str] = {}
    for f in files:
        parts = f.split("/")[:-1]
        for i, d in enumerate(parts):
            if d.lower() in ROMFS_TOP:
                roots.setdefault(tuple(x.lower() for x in parts[:i]), "".join(x + "/" for x in parts[:i]))
                break
    if not roots:
        return None
    depth = min(len(k) for k in roots)
    top = [v for k, v in roots.items() if len(k) == depth]
    return top[0] if len(top) == 1 else None


def _byte_order_platform(head: bytes) -> str | None:
    """"wiiu" (big-endian) or "switch" (little-endian) by a SARC, BYML, MSBT or BFRES header; None for others."""
    if head[:4] == b"SARC":
        return {b"\xfe\xff": "wiiu", b"\xff\xfe": "switch"}.get(head[6:8])
    if head[:8] == b"MsgStdBn":
        return {b"\xfe\xff": "wiiu", b"\xff\xfe": "switch"}.get(head[8:10])
    if head[:2] in (b"BY", b"YB"):
        return "wiiu" if head[:2] == b"BY" else "switch"
    if head[:4] == b"FRES" and len(head) >= 8:
        return "switch" if head[4:8] == b"    " else "wiiu"
    return None


def sniff_platform(src: Source, files: list[str]) -> tuple[int, int]:
    """(Wii U, Switch) counts among up to SNIFF_MAX of these files (as src names them) whose header shows it."""
    wiiu = switch = tried = 0
    for rel in files:
        ext = _ext(rel.lower())
        if not (ext in SNIFF_EXT or (ext[:1] == "s" and ext[1:] in SNIFF_EXT)):
            continue
        if tried >= SNIFF_MAX:
            break
        tried += 1
        try:
            head = src.read(rel, 1 << 16)
            if head[:4] == b"Yaz0":
                import bnp  # (as ArchiveSource: only when needed)
                head = bnp._yaz0_head(head, 16)
        except Exception:  # noqa: BLE001 (any file that can't be read just isn't counted)
            continue
        found = _byte_order_platform(head)
        wiiu += found == "wiiu"
        switch += found == "switch"
    return wiiu, switch


def _bare_tree(src: Source, low: list[str]) -> Finding | None:
    """A Switch (or Wii U) mod as bare RomFS folders (Pack/TitleBG.pack, Model/X.sbfres) with no romfs/ or
    content/ folder: tools/romfs_mods.py deploys it from the folder that holds them."""
    if any(ANCHOR_RE.search(p) or WIIU_DATA_RE.search(p) for p in low):
        return None  # (romfs/ or content/; aoc/ on its own is Wii U DLC, next to bare folders a Switch overlay)
    root = bare_root(src.files)
    if root is None:
        return None
    files = [f for f, p in zip(src.files, low) if p.startswith(root.lower()) and "/" in p[len(root):]
             and not any(x in JUNK or x.startswith(".") for x in p.split("/"))]
    if not files:
        return None
    tops = sorted({f[len(root):].split("/", 1)[0] for f in files})
    where = f"{len(files)} file(s) in bare RomFS folders ({', '.join(tops[:4])}{', ...' if len(tops) > 4 else ''})"
    wiiu, switch = sniff_platform(src, files)
    if wiiu and not switch:
        return Finding(BARE_WIIU, "wiiu", LATER,
                       f"{where} without a content/ folder, and they are Wii U files: put those folders in a folder "
                       "named content, then add that folder (tools/modman.py add converts it)")
    detail = f"{where} with no romfs/ folder: used through the mod overlay"
    detail += " (Switch files, checked)" if switch else " (taken as Switch files: none of them shows its platform)"
    editing = [f for f in files if _ext(f.lower()) in EDITING_EXT]
    if editing:
        detail += (f". {len(editing)} of them are image or 3D editor files (.dds, .gtx, .png...), which the game "
                   "doesn't read: left out")
    return Finding("Switch RomFS files", "switch", WORKS, detail)


def _switch_romfs(low: list[str], overlay: tuple[list[str], list[str]]) -> Finding | None:
    files, aoc = overlay
    data = [p for p in low if ROMFS_RE.search(p)] + files
    if not data:
        return None
    dlc = sum(1 for p in data if DLC_TITLE in p) + len(aoc)
    detail = f"{len(data)} file(s)" + (f", {dlc} of them DLC" if dlc else "") + ": used through the mod overlay"
    if aoc:
        detail += " (a Switch overlay folder: its DLC files are in aoc/0010/)"
    if any(p.endswith("pack/bootup.pack") for p in data):
        detail += ". Has its own Bootup.pack (often from an older version): the game's Layout/Common is kept"
    return Finding("Switch RomFS files", "switch", WORKS, detail)


def _exefs_patches(src: Source, low: list[str]) -> list[Finding]:
    out = []
    for i, p in enumerate(low):
        if not p.endswith((".ips", ".pchtxt")):
            continue
        stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        head = src.read(src.files[i], 4096)
        build = stem if re.fullmatch(r"[0-9a-f]{16,64}", stem) else ""
        m = NSOBID_RE.search(head)
        if m:
            build = m.group(1).decode().lower()
        note = ""
        if p.endswith(".ips") and not head.startswith((b"PATCH", b"IPS32")):
            note = " (not a valid IPS file)"
        version = _build_version(build) if build else None
        label = build[:16] if build else "unknown build"
        if version == TARGET_VERSION:
            out.append(Finding("ExeFS patch", "switch", WORKS,
                               f"{p}: for 1.9.0; tools/exefs_patch.py applies it (patches to code also need a "
                               f"rebuild with tools/sw_rebuild.py){note}"))
        else:
            out.append(Finding("ExeFS patch", "switch", RETARGET,
                               f"{p}: for {version or 'another version'} ({label}){note}"))
    return out


def _code_mods(low: list[str]) -> Finding | None:
    hits = [p for p in low if SUBSDK_RE.search(p) or SKYLINE_RE.search(p)]
    if not hits:
        return None
    return Finding("Switch code mod (exlaunch / Skyline)", "switch", REWRITE,
                   f"{len(hits)} file(s): Switch ARM code, which Savage Wind can't run; remake it as a native mod "
                   "(a DLL using native/modhost/swmod.h)")


def _cheats(low: list[str]) -> list[Finding]:
    out = []
    for p in low:
        m = CHEAT_RE.search(p)
        if not m:
            continue
        version = _build_version(m.group(2))
        if version == TARGET_VERSION:
            out.append(Finding("Atmosphere cheats", "switch", WORKS,
                               f"{p}: for 1.9.0; tools/cheats.py runs its data cheats (cheats that write game code "
                               "do nothing)"))
        else:
            out.append(Finding("Atmosphere cheats", "switch", RETARGET, f"{p}: for {version or 'another version'}"))
    return out


def inspect(src: Source) -> list[Finding]:
    low = [f.lower() for f in src.files]
    findings: list[Finding] = []
    if "mod.toml" in low:
        findings.append(Finding("Native mod (this loader)", "any", WORKS,
                                "has a mod.toml: tools/modman.py adds and deploys it (docs/mod-format.md)"))
    ukmm = _ukmm(src, low)
    if ukmm:
        return findings + [ukmm]  # UKMM stores its own processed data; nothing else to read
    bnp = _bnp(src, low)
    if bnp and bnp.platform == "wiiu" and bnp.verdict == PARTIAL:
        # a packed Wii U one: its packs hold only the changes, and modman add converts it as a whole (bnp.install_wiiu)
        return findings + [bnp]
    overlay = _overlay(low)
    packs = _graphic_packs(src, low)   # (a Cemu graphic pack's folder isn't a bare RomFS tree, whatever its name)
    romfs = _switch_romfs(low, overlay) or (None if bnp or packs else _bare_tree(src, low))
    for f in (bnp, _wiiu_data(low, overlay[1]), romfs, _code_mods(low)):
        if f:
            findings.append(f)
    findings += packs + _exefs_patches(src, low) + _cheats(low)
    return findings


def overall(findings: list[Finding]) -> str:
    if not findings:
        return "doesn't look like a BotW mod (or it's still packed)"
    verdicts = {f.verdict for f in findings}
    usable = verdicts & USABLE
    if usable and verdicts - USABLE:
        return "partly usable"
    worst_first = sorted(verdicts, key=ORDER.index)
    pick = worst_first[-1] if usable else worst_first[0]
    return VERDICTS[pick]


def _safe_stdout() -> None:
    """Let print() take any mod name. Redirected to a file or a pipe, Windows Python writes stdout in the ANSI code
    page with strict errors, so a letter it lacks (a mod named in Chinese, say) ended the run in a
    UnicodeEncodeError; such letters now print as '?'. Left alone when the stream already has a lenient handler."""
    try:
        if sys.stdout.errors in ("strict", "surrogateescape"):
            sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    _safe_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mod", type=Path, help="mod folder or .zip")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)
    try:
        src = open_source(args.mod)
    except ValueError as e:
        import bnp  # (a .bnp: listed by 7-Zip or tar.exe, see ArchiveSource)
        if not bnp.is_archive(str(args.mod)):
            print(f"error: {e}", file=sys.stderr)
            return 2
        try:
            src = ArchiveSource(args.mod)
        except (bnp.BnpError, OSError) as e2:
            print(f"error: {e2}", file=sys.stderr)
            return 2
    except (OSError, zipfile.BadZipFile) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    findings = inspect(src)
    if args.json:
        print(json.dumps({"mod": args.mod.name, "overall": overall(findings),
                          "parts": [asdict(f) for f in findings]}, indent=2))
        return 0
    print(f"Mod: {args.mod.name}")
    for f in findings:
        print(f"  [{f.verdict}] {f.kind}: {f.detail}")
    print(f"Overall: {overall(findings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
