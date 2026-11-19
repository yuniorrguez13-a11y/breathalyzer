#!/usr/bin/env python3
"""Put Switch RomFS mods into one folder for Savage Wind's RomFS overlay (SAVAGEWIND_ROMFS_OVERLAY).

    python tools/romfs_mods.py deploy --out <overlay folder> [--game-romfs <folder>] <mod> [<mod>...]
    python tools/romfs_mods.py env --out <overlay folder> [--format plain|ps|cmd]

`deploy` fills the overlay folder (a new or empty one, or one this tool made) from the mods. When several
mods ship the same file, the later mod wins. A redeploy builds the new overlay next to the old one
(<folder>~new) and swaps it in, so the old overlay stays when something fails; it only deletes files its
marker file lists, and refuses a folder holding anything else. A mod is a folder or .zip in any common
layout: romfs/..., 01007EF00011E000/romfs/..., atmosphere/contents/01007EF00011E000/romfs/..., DLC in
01007EF00011F001/romfs/... (it goes to aoc/0010/), UKMM or BCML output folders, or bare RomFS folders
(Actor/, Pack/, System/...). A mod holding several RomFS trees (option folders) is refused. Files become
hard links when the mod and the overlay are on the same drive and the drive has them, and copies
otherwise; files inside a .zip are always extracted (copied).
`--game-romfs` (the game's extracted\\romfs, or the extracted folder) checks the paths against the game.
`env` prints the environment variables to start Savage Wind with.

This tool does not merge packs, texts or the RSTB: a file that several mods change comes from the last
mod only. Merge such mods with UKMM (Switch mode) first and deploy UKMM's output folder. (tools/modman.py deploy
passes one size table merged from the mods' own, which then takes the RSTB's place: see tools/bnp.py.)
A packed BCML package (.bnp, or a folder with logs/) is refused: tools/modman.py add installs it (tools/bnp.py).
Wii U mods (content/, aoc/0010/, Cemu graphic packs) are refused: convert them first with tools/wiiu_convert.py.

How Savage Wind reads the overlay (0.4.1-alpha.4, runtime/services/fs.c, MIT): the folder mirrors RomFS
paths, DLC files are in aoc/0010/, bytes are served as they are (no Yaz0 work), files are only added in
folders the game's RomFS has, names starting with '.' are ignored, and a mod's RSTB is used as is.
"""
from __future__ import annotations

import argparse
import filecmp
import importlib
import json
import os
import re
import shutil
import stat
import sys
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mod_inspect as mi  # noqa: E402

MARKER = ".mod-deploy.json"           # Savage Wind skips names starting with '.', so the game never sees it
TOOL_ID = "botw-mod-loader romfs_mods"
FORMAT = 1
DLC_PREFIX = "aoc/0010/"              # every DLC (aoc) image reads its overlay files from here (fs.c ov_sub)
RSTB = "System/Resource/ResourceSizeTable.product.srsizetable"
BOOTUP = "Pack/Bootup.pack"
# DLC files that, in the game's place, turn on Savage Wind's "DLC merged into the RomFS" RSTB fix (fs.c)
DLC_MARKS = {"system/aocversion.txt", "pack/aocmainfield.pack"}
ENV_OVERLAY = "SAVAGEWIND_ROMFS_OVERLAY"
ENV_NO_HUD = "SAVAGEWIND_NO_HUD_LETTERS"
LONG_PATH = 259
# A redeploy builds in <overlay>~new, moves the old overlay to <overlay>~old, then deletes that
STAGE, ASIDE = "~new", "~old"
# .zip compression methods Python reads, and the module each needs
UNZIP = {zipfile.ZIP_STORED: None, zipfile.ZIP_DEFLATED: "zlib", zipfile.ZIP_BZIP2: "bz2", zipfile.ZIP_LZMA: "lzma"}
ZIP_METHODS = {9: "Deflate64", 93: "Zstandard", 95: "XZ", 98: "PPMd", 99: "AES"}

# Top-level folders of BotW's RomFS, to recognise a mod that is a bare RomFS tree (lowercase)
ROMFS_TOP = mi.ROMFS_TOP
TID_RE = re.compile(r"0100[0-9a-f]{12}")
DLC_TID_RE = re.compile(r"01007ef00011f(?!000)[0-9a-f]{3}")
BASE_TIDS = {mi.TITLE, "01007ef00011e800"}  # the game and its update
JUNK = {"thumbs.db", "desktop.ini", "__macosx"}
# BIDI marks (breath.BIDI) make a name show other than it is (gpj.exe shown as exe.jpg): no game file has one
BIDI = set("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
BAD_CHARS = set('<>:"|?*\\') | {chr(i) for i in range(32)} | BIDI
# Names Windows keeps for devices, whatever the extension: COM and LPT with a superscript 1-3 are COM1-3 / LPT1-3
# too, and CONIN$ / CONOUT$ are the console. Opening one opens the device, not a file.
RESERVED_RE = re.compile(r"(con|prn|aux|nul|conin\$|conout\$|com[0-9\u00b9\u00b2\u00b3]|lpt[0-9\u00b9\u00b2\u00b3])",
                         re.I)

# why a file isn't deployed
R_JUNK = "system or hidden"
R_OUTSIDE = "not RomFS"
R_OTHER_GAME = "for another game"
R_UNKNOWN_TOP = "not in a RomFS folder"
R_AOC = "in an aoc folder Savage Wind doesn't read"
R_BAD_NAME = "bad file name"
R_ROOT = "at the RomFS root"
R_DLC_MARK = "DLC file in the game's place"
R_SAME = "same file twice"
R_EDITING = "image or 3D editor file"   # (mod_inspect.EDITING_EXT: .dds, .gtx, .png...: the game reads none of them)


class DeployError(Exception):
    pass


class WiiUModError(DeployError):
    """A Wii U mod (content/, only aoc/0010/, or a Cemu graphic pack with game files): tools/wiiu_convert.py turns it
    into a Switch mod, and modman add does that by itself."""


class OtherGameError(DeployError):
    """A Cemu graphic pack made for another Wii U game (no rules.txt names BotW): nothing can make it usable."""


# What to do with a Wii U mod. (<mod> stays a placeholder: modman runs these checks on its library copy, whose
# path would only mislead.)
WIIU_HINT = ("convert it on your PC with python tools/wiiu_convert.py convert <mod> --out <new folder> "
             "(python tools/modman.py add converts it for you)")
# ... and for one with only files wiiu_convert keeps as Wii U data (mod_inspect's "later")
WIIU_LATER = ("its files are models, textures, audio or other kinds tools/wiiu_convert.py can't convert yet, so "
              "Savage Wind can't use it")


@dataclass
class Entry:
    member: str          # path inside the mod, "/"-separated
    rel: str             # path inside the RomFS it belongs to (the game's, or the DLC's when dlc)
    dlc: bool = False
    size: int = 0
    src: str = ""        # the file on disk ("" for a .zip member)
    zname: str = ""      # the .zip member name
    crc: int | None = None  # the .zip member's CRC-32
    dev: int | None = None
    alias: bool = False  # a DLC movie, also put where Savage Wind's movie player looks
    how: str = ""        # "link" or "copy"
    data: bytes | None = None  # bytes to write instead of a file (the merged RSTB)

    @property
    def target(self) -> str:
        return DLC_PREFIX + self.rel if self.dlc else self.rel


@dataclass
class Mod:
    path: str
    name: str
    layouts: list[str] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    zip: zipfile.ZipFile | None = None
    layer_of: str = ""   # an option folder (Layer): the name of the mod it belongs to
    owner: str = ""      # files made at deploy (add_generated): "generated:<what>", and path is ""


@dataclass(frozen=True)
class Layer:
    """A mod's option folder (docs/mod-settings.md, `romfs` / `dlc_romfs` of a setting): a bare RomFS tree (Actor/,
    Pack/, ... directly inside, no romfs/ or title-id folder), deployed right after the mod's own files, so it wins
    over them and later mods win over it. `mod` is the mod's name in the plan (its folder name, the library id);
    `dlc` puts its files into the DLC (aoc/0010/)."""
    folder: str
    mod: str
    dlc: bool = False


@dataclass
class Winner:
    """What the game gets for one overlay path (Plan.winner): a mod's file, a file made at deploy, or (mod None) the
    game's own file."""
    target: str
    mod: str | None
    entry: Entry | None
    game_path: str | None = None
    zip: zipfile.ZipFile | None = None

    @property
    def data(self) -> bytes | None:
        """The bytes when they are in memory (a file made at deploy, the merged RSTB), else None."""
        return self.entry.data if self.entry is not None else None

    @property
    def src(self) -> str:
        """The file on disk ("" for a .zip member or bytes in memory)."""
        return self.game_path or "" if self.entry is None else self.entry.src

    def read(self) -> bytes:
        """The bytes the game would read for this path."""
        e = self.entry
        if e is None:
            with open(self.game_path, "rb") as f:
                return f.read()
        if e.data is not None:
            return e.data
        if e.src:
            with open(e.src, "rb") as f:
                return f.read()
        return self.zip.read(e.zname)


@dataclass
class GameIndex:
    """Names in the game's RomFS and its DLC folders: lowercase path -> (path as spelled, size) / folder."""
    romfs: str
    files: dict[str, tuple[str, int]]
    dirs: dict[str, str]
    dlc_files: dict[str, tuple[str, int]] = field(default_factory=dict)
    dlc_dirs: dict[str, str] = field(default_factory=dict)
    aoc: list[str] = field(default_factory=list)

    @property
    def has_dlc(self) -> bool:
        return bool(self.aoc)

    @property
    def top_dirs(self) -> set[str]:
        return {k for k in [*self.dirs, *self.dlc_dirs] if k and "/" not in k}

    @classmethod
    def scan(cls, path: str) -> GameIndex:
        """`path` is the game's romfs folder or the folder holding it (Savage Wind's extracted\\)."""
        p = os.path.abspath(path)
        romfs = os.path.join(p, "romfs") if os.path.isdir(os.path.join(p, "romfs")) else p
        if not os.path.isdir(romfs):
            raise DeployError(f"{path}: folder not found")
        files, dirs = _scan(romfs)
        if not {"pack", "system", "actor"} & set(dirs):
            raise DeployError(f"{romfs} doesn't look like the game's RomFS (no Pack, System or Actor folder)")
        parent = os.path.dirname(romfs)
        aoc = sorted(d for d in os.listdir(parent)
                     if re.fullmatch(r"aoc\d+", d, re.I) and os.path.isdir(os.path.join(parent, d)))
        dlc_files: dict[str, tuple[str, int]] = {}
        dlc_dirs: dict[str, str] = {}
        for a in aoc:
            f, d = _scan(os.path.join(parent, a))
            for k, v in f.items():
                dlc_files.setdefault(k, v)
            for k, v in d.items():
                dlc_dirs.setdefault(k, v)
        return cls(romfs, files, dirs, dlc_files, dlc_dirs, [os.path.join(parent, a) for a in aoc])


def _scan(root: str) -> tuple[dict[str, tuple[str, int]], dict[str, str]]:
    files: dict[str, tuple[str, int]] = {}
    dirs = {"": ""}
    stack = [""]
    while stack:
        rel = stack.pop()
        with os.scandir(os.path.join(root, *rel.split("/")) if rel else root) as it:
            for e in it:
                r = f"{rel}/{e.name}" if rel else e.name
                if e.is_dir():
                    dirs[r.lower()] = r
                    stack.append(r)
                elif e.is_file():
                    files[r.lower()] = (r, e.stat().st_size)
    return files, dirs


@dataclass
class MergedRstb:
    """A size table made from several mods' own (tools/bnp.py deploy_hook): deployed in the RSTB's place."""
    data: bytes
    mods: list[str]


@dataclass
class Plan:
    mods: list[Mod]
    winners: dict[str, tuple[int, Entry]]  # overlay path -> (mod index, entry), sorted by path
    conflicts: list[dict]
    game: GameIndex | None
    notes: list[str]
    rstb_merged: list[str] = field(default_factory=list)  # the mods whose size tables the RSTB merges
    sources: list = field(default_factory=list)   # what plan() was given (paths and Layers), for add_layer
    rstb: MergedRstb | None = None
    generated: dict[str, dict] = field(default_factory=dict)  # overlay path -> {"owner", "built_on"} (add_generated)
    warnings: list[str] = field(default_factory=list)       # from add_generated, shown with the deploy's

    def winner(self, path: str) -> Winner | None:
        """What the game reads for a RomFS path ("Font/Font_US.sbfarc"; "aoc/0010/..." for a DLC file): the file of
        the mod that wins it, a file made at deploy, or the game's own file when no mod has it (needs the plan's
        game). None when neither has it. Case doesn't matter."""
        low = path.replace("\\", "/").strip("/").lower()
        hit = next(((t, v) for t, v in self.winners.items() if t.lower() == low), None)
        if hit:
            t, (i, e) = hit
            m = self.mods[i]
            return Winner(t, m.name, e, zip=m.zip)
        g = self.game
        if g:
            dlc = low.startswith(DLC_PREFIX)
            files = g.dlc_files if dlc else g.files
            found = files.get(low[len(DLC_PREFIX):] if dlc else low)
            if found:
                base = next((a for a in g.aoc if os.path.isfile(os.path.join(a, *found[0].split("/")))), None) \
                    if dlc else g.romfs
                if base:
                    return Winner(DLC_PREFIX + found[0] if dlc else found[0], None, None,
                                  os.path.join(base, *found[0].split("/")))
        return None

    def add_layer(self, mod: str, folder: str, dlc: bool = False) -> None:
        """Put an option folder right after the mod named `mod` (its own files and its earlier layers), and plan
        again. Add layers before add_generated. plan(..., [path, Layer(...), ...]) does several at once."""
        if self.generated:
            raise DeployError("add option folders before the files made at deploy")
        src = list(self.sources)
        at = max((k for k, s in enumerate(src)
                  if (s.mod if isinstance(s, Layer) else os.path.basename(os.path.abspath(s))) == mod), default=None)
        if at is None:
            raise DeployError(f"{mod} isn't in this deploy, so its option folder {folder} can't follow it")
        src.insert(at + 1, Layer(folder, mod, dlc))
        for m in self.mods:
            if m.zip:
                m.zip.close()
        new = plan(src, self.game, self.rstb)
        for k in ("mods", "winners", "conflicts", "notes", "rstb_merged", "sources"):
            setattr(self, k, getattr(new, k))


# ---------------------------------------------------------------- mapping a mod's files to RomFS paths

def _bad_name(part: str) -> bool:
    return (not part or part in (".", "..") or part[-1] in " ." or any(c in BAD_CHARS for c in part)
            or bool(RESERVED_RE.fullmatch(part.split(".", 1)[0].rstrip())))


def _bare_root(dirs_of: list[list[str]], known_top: set[str]) -> tuple[str, ...] | None:
    """The shallowest folder holding RomFS top-level folders (or aoc/0010), as lowercase parts."""
    roots = set()
    for dirs in dirs_of:
        for i, d in enumerate(dirs):
            if d in known_top or (d == "aoc" and dirs[i + 1:i + 2] == ["0010"]):
                roots.add(tuple(dirs[:i]))
                break
    if not roots:
        return None
    depth = min(len(r) for r in roots)
    top = sorted(r for r in roots if len(r) == depth)
    if len(top) > 1:
        names = ", ".join("/".join(r) for r in top)
        raise DeployError(f"holds several RomFS trees ({names}): pass each one as its own mod")
    return top[0]


def _nearest_tid(parts) -> str | None:
    return next((x for x in reversed(list(parts)) if TID_RE.fullmatch(x)), None)


def _root_key(before: list[str]) -> tuple[str, ...]:
    """The folders above a romfs/ folder without title ids (and the atmosphere/contents above one), so a mod's
    base-game and DLC trees (01007EF00011E000/romfs, 01007EF00011F001/romfs) count as one."""
    key: list[str] = []
    for x in before:
        if TID_RE.fullmatch(x):
            if key[-1:] == ["contents"]:
                key.pop()
                if key[-1:] == ["atmosphere"]:
                    key.pop()
            continue
        key.append(x)
    return tuple(key)


def map_paths(paths: list[str], known_top: set[str] = ROMFS_TOP, context: tuple[str, ...] = (), *,
              wiiu_hint: str = WIIU_HINT) -> tuple[list[Entry], list[tuple[str, str]], list[str]]:
    """Map a mod's files ("/"-separated paths as in the mod) to RomFS paths.

    `context` is the mod folder's own path (lowercase parts), for a title id above it, as in
    01007EF00011F001\\romfs passed directly. Returns (entries, skipped as (path, reason), layouts seen).
    Raises DeployError for a Wii U mod (content/, or only aoc/0010/; `wiiu_hint` says what to do with it) or a mod
    holding several RomFS trees.
    """
    lows = [p.lower() for p in paths]
    dirs_of = [lp.split("/")[:-1] for lp in lows]
    anchored = any("romfs" in d for d in dirs_of)
    root = None
    if not anchored:
        if any(d == "content" and dirs[j + 1:j + 2] and dirs[j + 1] in known_top
               for dirs in dirs_of for j, d in enumerate(dirs)):
            raise WiiUModError(f"is a Wii U mod (content/ folder): {wiiu_hint}")
        root = _bare_root(dirs_of, known_top)
    entries: list[Entry] = []
    skipped: list[tuple[str, str]] = []
    layouts: set[str] = set()
    roots: dict[tuple[str, ...], str] = {}  # each romfs/ tree with files in folders -> how the mod spells it
    for path, lp, dirs in zip(paths, lows, dirs_of):
        parts = path.split("/")
        if any(x.startswith(".") or x in JUNK for x in lp.split("/")):
            skipped.append((path, R_JUNK))
            continue
        root_of = None
        if anchored:
            if "romfs" not in dirs:
                skipped.append((path, R_OUTSIDE))
                continue
            i = dirs.index("romfs")
            tid = _nearest_tid(dirs[:i]) or _nearest_tid(context)
            rel = parts[i + 1:]
            root_of = (_root_key(dirs[:i]), "/".join(parts[:i + 1]))
        else:
            if root is None or tuple(dirs[:len(root)]) != root:
                skipped.append((path, R_OUTSIDE))
                continue
            i = -1
            tid = _nearest_tid(context)
            rel = parts[len(root):]
            if len(rel) > 1 and rel[0].lower() not in known_top and rel[0].lower() != "aoc":
                skipped.append((path, R_UNKNOWN_TOP))
                continue
        dlc = False
        if tid is None:
            layout = "romfs/" if anchored else "RomFS folders"
        elif DLC_TID_RE.fullmatch(tid):
            dlc, layout = True, "DLC title-id folder"
        elif tid in BASE_TIDS:
            layout = "atmosphere/contents" if "atmosphere" in dirs[:max(i, 0)] else "title-id folder"
        else:
            skipped.append((path, R_OTHER_GAME))
            continue
        if not dlc and len(rel) > 1 and rel[0].lower() == "aoc":
            if len(rel) > 2 and rel[1] == "0010":
                dlc, rel, layout = True, rel[2:], "DLC aoc/0010"
            else:
                skipped.append((path, R_AOC))
                continue
        if any(_bad_name(x) for x in rel):
            skipped.append((path, R_BAD_NAME))
            continue
        if "." in rel[-1] and rel[-1].rsplit(".", 1)[1].lower() in mi.EDITING_EXT:
            skipped.append((path, R_EDITING))
            continue
        entries.append(Entry(member=path, rel="/".join(rel), dlc=dlc))
        layouts.add(layout)
        if root_of and len(rel) > 1:
            roots.setdefault(*root_of)
    if len(roots) > 1:  # option folders (30fps/romfs, 60fps/romfs) or an emulator folder holding several mods
        raise DeployError(f"holds several RomFS trees ({_some(sorted(roots.values()), 4)}): "
                          "pass each one as its own mod")
    if (not anchored and _nearest_tid(context) is None and any(e.dlc for e in entries)
            and not any(not e.dlc and "/" in e.rel for e in entries)):
        raise WiiUModError(f"has only an aoc/0010/ folder, which is how Wii U mods hold DLC files: {wiiu_hint}. "
                           "Switch DLC files go in 01007EF00011F001/romfs/.")
    return entries, skipped, sorted(layouts)


def load_mod(path: str, known_top: set[str] = ROMFS_TOP) -> Mod:
    p = Path(path)
    name = p.name or str(p)
    if not p.exists():
        raise DeployError(f"{path}: not found")
    try:
        src = mi.open_source(p)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        raise DeployError(f"{name}: {e}") from None
    try:
        return _read_mod(src, path, name, known_top)
    except BaseException:
        if isinstance(src, mi.ZipSource):
            src.zf.close()
        raise


def _can_unzip(method: int) -> bool:
    if method not in UNZIP:
        return False
    try:
        if UNZIP[method]:
            importlib.import_module(UNZIP[method])
        return True
    except ImportError:
        return False


def _cemu_pack(src: mi.Source) -> str:
    """Whether the mod has a Cemu graphic pack's rules.txt (a Wii U mod): "botw" when one names BotW's title id,
    "other" when every one is for another game, "" without one."""
    found = ""
    for f in src.files:
        if f.lower().rpartition("/")[2] == "rules.txt":
            try:
                text = src.read(f, 65536).decode("utf-8", "replace").lower()
            except (OSError, RuntimeError, zipfile.BadZipFile, zlib.error, EOFError):
                continue
            if "[definition]" in text:
                if any(t in text for t in mi.WIIU_TITLES):
                    return "botw"
                found = "other"
    return found


def _read_mod(src: mi.Source, path: str, name: str, known_top: set[str]) -> Mod:
    try:
        findings = mi.inspect(src)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, zlib.error, EOFError) as e:
        # (RuntimeError: an encrypted .zip; NotImplementedError, one of them: a compression Python can't read)
        raise DeployError(f"{name}: {e}") from None
    kinds = list(dict.fromkeys(f.kind for f in findings))
    later = any(f.kind == "Wii U game data" and f.verdict == mi.LATER for f in findings)
    hint = WIIU_LATER if later else WIIU_HINT
    if "UKMM package" in kinds:
        raise DeployError(f"{name} is a UKMM package: install it in UKMM (Switch mode), deploy there, "
                          "then pass UKMM's output folder")
    if "BCML mod (BNP)" in kinds and any(f.lower().startswith("logs/") for f in src.files):
        raise DeployError(f"{name} is a BCML package (BNP), whose packs only hold the changes: add it with "
                          "python tools/modman.py add (it fills them from your game), or install it in UKMM or BCML "
                          "(Switch mode) and pass the merged output folder")
    members = [src.prefix + f for f in src.files]
    paths = [m.replace("\\", "/") for m in members]
    pack = "" if any("romfs" in p.lower().split("/")[:-1] for p in paths) else _cemu_pack(src)
    if pack == "other":
        raise OtherGameError(f"{name} is a Cemu graphic pack made for another game, not BotW (its rules.txt has no "
                             "BotW title id), so Savage Wind can't use it")
    if pack:
        if any(mi.WIIU_DATA_RE.search(p.lower()) for p in paths):
            raise WiiUModError(f"{name} is a Cemu graphic pack (rules.txt) with Wii U game files: {hint}")
        raise DeployError(f"{name} is a Cemu graphic pack (rules.txt) without game files: it only changes Cemu "
                          "(code, shaders or settings), so Savage Wind can't use it")
    context = () if isinstance(src, mi.ZipSource) else tuple(Path(os.path.abspath(path)).as_posix().lower().split("/"))
    try:
        entries, skipped, layouts = map_paths(paths, known_top, context, wiiu_hint=hint)
    except DeployError as e:
        raise type(e)(f"{name} {e}") from None
    mod = Mod(path=os.path.abspath(path), name=name, layouts=layouts, entries=entries, skipped=skipped)
    for k in kinds:
        if k == "BCML mod (BNP)":
            mod.notes.append("BCML mod folder: its files are deployed as they are")
        elif k not in ("Switch RomFS files", "Wii U game data"):
            mod.notes.append(f"also has {k} files: not deployed by this tool")
    if isinstance(src, mi.ZipSource):
        mod.zip = src.zf
        names = dict(zip(paths, members))
        for e in entries:
            info = src.zf.getinfo(names[e.member])
            if info.flag_bits & 1:
                raise DeployError(f"{name} is a password-protected .zip: extract it first")
            m = info.compress_type
            if not _can_unzip(m):
                raise DeployError(f"{name} uses a .zip compression Python can't read "
                                  f"({ZIP_METHODS.get(m, f'method {m}')}): extract it with 7-Zip and pass the folder")
            e.zname, e.size, e.crc = info.filename, info.file_size, info.CRC
    else:
        root = os.path.abspath(src.root)
        for e in entries:
            e.src = os.path.join(root, *e.member.split("/"))
            if os.path.islink(e.src):
                e.src = os.path.realpath(e.src)
            st = os.stat(e.src)
            e.size, e.dev = st.st_size, st.st_dev
    if not entries:
        raise DeployError(f"{name}: no RomFS files found (expected romfs/, 01007EF00011E000/romfs/ "
                          "or folders like Actor/ and Pack/)")
    return mod


# ---------------------------------------------------------------- planning (no writes)

def _canon(rel: str, dlc: bool, case: dict[str, str], gfiles: dict[str, tuple[str, int]]) -> str:
    """`rel` with folder names spelled as the game (or the first mod) spells them: Windows folders ignore
    case but Savage Wind matches new files' folders case-sensitively."""
    spelled = DLC_PREFIX.rstrip("/") if dlc else ""
    parts = rel.split("/")
    for part in parts[:-1]:
        full = f"{spelled}/{part}" if spelled else part
        spelled = case.setdefault(full.lower(), full)
    hit = gfiles.get(rel.lower())
    name = hit[0].rpartition("/")[2] if hit else parts[-1]
    full = f"{spelled}/{name}" if spelled else name
    return full[len(DLC_PREFIX):] if dlc else full


def _same_file(a: Entry, b: Entry) -> bool:
    if a.size != b.size:
        return False
    if a.src and b.src:
        try:
            return filecmp.cmp(a.src, b.src, shallow=False)
        except OSError:
            return False
    return a.crc is not None and a.crc == b.crc


def plan(mod_paths: list, game: GameIndex | None = None, rstb: MergedRstb | None = None) -> Plan:
    """What a deploy would put where (reads only). `mod_paths` holds mod folders or .zip files, later ones winning,
    and may hold Layers (a mod's option folders) right after their mod."""
    known_top = ROMFS_TOP | (game.top_dirs if game else set())
    mods: list[Mod] = []
    try:
        p = _plan(mods, mod_paths, game, known_top, rstb)
    except BaseException:
        for m in mods:
            if m.zip:
                m.zip.close()
        raise
    p.sources, p.rstb = list(mod_paths), rstb
    return p


def load_layer(layer: Layer, known_top: set[str] = ROMFS_TOP) -> Mod:
    """An option folder as a mod of its own, named "<mod> option <folder>"."""
    if not os.path.isdir(layer.folder):
        raise DeployError(f"{layer.mod}: its option folder {layer.folder} is missing")
    mod = load_mod(layer.folder, known_top)
    mod.name, mod.layer_of = f"{layer.mod} option {os.path.basename(os.path.abspath(layer.folder))}", layer.mod
    if layer.dlc:
        for e in mod.entries:
            e.dlc = True
    return mod


def _plan(mods: list[Mod], mod_paths: list, game: GameIndex | None, known_top: set[str],
          rstb: MergedRstb | None = None) -> Plan:
    seen: set[str] = set()
    for path in mod_paths:
        mod = load_layer(path, known_top) if isinstance(path, Layer) else load_mod(path, known_top)
        base, k = mod.name, 2
        while mod.name.lower() in seen:
            mod.name, k = f"{base} ({k})", k + 1
        seen.add(mod.name.lower())
        mods.append(mod)

    case = {"aoc": "aoc", "aoc/0010": "aoc/0010"}
    if game:
        case.update(game.dirs)
        case.update({f"{DLC_PREFIX}{k}": f"{DLC_PREFIX}{v}" for k, v in game.dlc_dirs.items() if k})
    claims: dict[str, list[tuple[int, Entry]]] = {}
    for i, mod in enumerate(mods):
        kept = []
        own: dict[str, Entry] = {}
        for e in mod.entries:
            gfiles = (game.dlc_files if e.dlc else game.files) if game else {}
            low = e.rel.lower()
            if "/" not in e.rel and low not in gfiles:
                mod.skipped.append((e.member, R_ROOT))
                continue
            if not e.dlc and low in DLC_MARKS:
                mod.skipped.append((e.member, R_DLC_MARK))
                continue
            e.rel = _canon(e.rel, e.dlc, case, gfiles)
            first = own.get(e.target.lower())
            if first:  # (romfs/ and atmosphere/contents/01007EF00011E000/romfs/, or names differing in case)
                if not _same_file(first, e):
                    raise DeployError(f"{mod.name} has two different files for {e.target}: {first.member} and "
                                      f"{e.member}. Keep the one you want and deploy again.")
                mod.skipped.append((e.member, R_SAME))
                continue
            own[e.target.lower()] = e
            kept.append(e)
            claims.setdefault(e.target.lower(), []).append((i, e))
        mod.entries = kept
        if not kept:
            raise DeployError(f"{mod.name}: no RomFS files to deploy (all skipped: "
                              f"{', '.join(sorted({r for _, r in mod.skipped}))})")

    if rstb is not None and mods:  # (one size table merged from the mods' own: it takes every RSTB's place)
        key = RSTB.lower()
        lst = claims.get(key, [])
        e = Entry(member="(merged size table)", rel=_canon(RSTB, False, case, game.files if game else {}),
                  size=len(rstb.data), data=rstb.data)
        claims[key] = [(lst[-1][0] if lst else len(mods) - 1, e)]
    # (an option folder replacing its own mod's file is the option at work, not a clash between mods)
    conflicts = [{"path": lst[-1][1].target, "mods": [mods[i].name for i, _ in lst], "winner": mods[lst[-1][0]].name}
                 for _, lst in sorted(claims.items())
                 if len({mods[i].layer_of or mods[i].name for i, _ in lst}) > 1]

    # Savage Wind's movie player opens <overlay>/<path inside the DLC> for a DLC movie (movie.c), not aoc/0010/
    notes: list[str] = []
    aliases: dict[str, list[tuple[int, Entry]]] = {}
    for i, mod in enumerate(mods):
        for e in mod.entries:
            if e.dlc and e.rel.lower().startswith("movie/"):
                a = replace(e, rel=_canon(e.rel, False, case, game.files if game else {}), dlc=False, alias=True)
                aliases.setdefault(a.target.lower(), []).append((i, a))
    for key, lst in aliases.items():
        if key in claims:
            notes.append(f"{claims[key][-1][1].target}: a base-game file of this name is played, not the DLC movie")
            continue
        claims[key] = lst

    winners = {lst[-1][1].target: lst[-1] for _, lst in sorted(claims.items())}
    return Plan(mods, winners, conflicts, game, notes, list(rstb.mods) if rstb is not None and mods else [])


GENERATED = "generated:"   # the owner of files made at deploy: generated:pictures, generated:fonts, generated:title


def add_generated(p: Plan, files: list, owner: str) -> list[str]:
    """Files made at deploy (tools/font.py, tools/menu_bg.py, tools/mod_pictures.py) as overlay files that win over
    every mod's. `files`: (RomFS path, bytes, size-table entry or None) tuples; the entry is (the file's name in the
    RSTB, the size it needs there), like ("Font/Font_US.bfarc", 3650016); None: it fits the entry already there.
    `owner` is "generated:<what>", listed as the file's mod in .mod-deploy.json, with "built_on": the mod whose file
    it was made from. Returns the paths taken.

    Size-table entries (rule S2, docs/mod-pictures.md): they go into the size table in use (a mod's own, the one
    merged from the mods' tables, or the game's when no mod ships one), and only ever raise an entry. That table is
    then deployed as a made file too. When no table can be read, a file that needs an entry is left out, with a
    warning, so the game never reads a file bigger than its entry."""
    if not owner.startswith(GENERATED) or len(owner) == len(GENERATED):
        raise ValueError(f"owner must be '{GENERATED}<what>', not {owner!r}")
    items = []
    for item in files:
        path, data, rstb = item
        rel = str(path).replace("\\", "/").strip("/")
        if not rel or "/" not in rel or any(_bad_name(x) for x in rel.split("/")) \
                or rel.lower().startswith(("aoc/", DLC_PREFIX)) or not isinstance(data, (bytes, bytearray)):
            raise ValueError(f"{owner}: {path!r} isn't a RomFS file path with bytes")
        if isinstance(rstb, int) and not isinstance(rstb, bool):   # (a size alone: the file's own name in the table)
            rstb = (rstb_name(rel), rstb)
        if rstb is not None:
            name, size = rstb
            if not isinstance(name, str) or not name or len(name.encode()) > 127 or not isinstance(size, int) \
                    or size <= 0:
                raise ValueError(f"{owner}: {rel}'s size-table entry {rstb!r} isn't (name, size)")
        items.append((rel, data, rstb))
    sizes: dict[str, int] = {}
    for _, _, rstb in items:
        if rstb is not None:
            sizes[rstb[0]] = max(sizes.get(rstb[0], 0), rstb[1])
    table = None
    if sizes:
        try:
            table = _sizes_into_table(p, sizes)
        except DeployError as e:
            p.warnings.append(f"{owner}: {e}")
            table = False
        if table is False or table is None and not p.winner(RSTB):
            if table is None:
                p.warnings.append(f"{owner}: no size table (RSTB) to add entries to (the game's files aren't known)")
            for rel, _, rstb in items:
                if rstb is not None:
                    p.warnings.append(f"{owner}: {rel} needs a bigger size-table (RSTB) entry: left out.")
            items = [x for x in items if x[2] is None]
            table = None
    taken: list[str] = []
    for rel, data, _ in items:
        taken.append(_put_generated(p, rel, data, owner))
    if table is not None:   # (the table in use with our entries: it wins like any made file)
        data, base, changed = table
        rel = _put_generated(p, RSTB, data, owner)
        p.generated[rel]["built_on"] = base
        p.generated[rel]["sizes"] = changed
    p.winners = dict(sorted(p.winners.items()))
    return taken


def rstb_name(rel: str) -> str:
    """A RomFS file's name in the size table: its path with Yaz0's "s" taken off the extension
    ("Layout/Common.sblarc" -> "Layout/Common.blarc")."""
    d, _, name = rel.replace("\\", "/").strip("/").rpartition("/")
    stem, dot, ext = name.rpartition(".")
    if dot and stem and ext[:1] == "s" and len(ext) > 1:
        name = f"{stem}.{ext[1:]}"
    return f"{d}/{name}" if d else name


def _put_generated(p: Plan, rel: str, data: bytes, owner: str) -> str:
    idx = next((k for k, m in enumerate(p.mods) if m.owner == owner), None)
    if idx is None:
        p.mods.append(Mod(path="", name=owner, owner=owner))
        idx = len(p.mods) - 1
    case = dict(p.game.dirs) if p.game else {}
    old = next((t for t in p.winners if t.lower() == rel.lower()), None)
    rel = old or _canon(rel, False, case, p.game.files if p.game else {})  # (spelled as the file it replaces)
    built_on = p.mods[p.winners[old][0]].name if old else None
    if old:
        del p.winners[old]
    e = Entry(member="(made at deploy)", rel=rel, size=len(data), data=bytes(data))
    p.mods[idx].entries.append(e)
    p.winners[rel] = (idx, e)
    p.generated[rel] = {"owner": owner, "built_on": built_on}
    return rel


def merge_sizes(table: bytes, sizes: dict[str, int]) -> tuple[bytes, dict[str, int]]:
    """A size table (Yaz0 or plain, as given) with `sizes` ({name in the table: size}) put in, raising entries only:
    a name the table lists by name keeps its name, any other goes in by its CRC-32. Returns (table, the entries that
    changed); the table comes back as given when nothing changed. A big table is written as stored Yaz0 (fast)."""
    import wiiu_convert as wc  # (lazy: only deploys with made files that need entries read size tables)
    try:
        yaz = bytes(table[:4]) == b"Yaz0"
        raw, align = wc.yaz0_decompress(bytes(table)) if yaz else (bytes(table), 0)
        if wc.rstb_order(raw) != wc.LE and len(raw) > 12:
            raise DeployError("the size table (RSTB) is a Wii U one")
        crcs, named = wc.rstb_load(raw)
    except wc.FormatError as e:
        raise DeployError(f"the size table (RSTB) can't be read ({e})") from None
    crc = dict(crcs)
    names = {n.split(b"\0", 1)[0].decode("utf-8", "replace"): v for n, v in named}
    changed: dict[str, int] = {}
    for name, size in sizes.items():
        if name in names:
            if size > names[name]:
                names[name] = changed[name] = size
        else:
            k = zlib.crc32(name.encode()) & 0xFFFFFFFF
            if size > crc.get(k, -1):
                crc[k] = changed[name] = size
    if not changed:
        return bytes(table), {}
    out = wc.rstb_build(sorted(crc.items()), [(n.encode()[:127], v) for n, v in names.items()])
    if not yaz:
        return out, changed
    return (wc.yaz0_compress(out, align) if len(out) <= 16 << 10 else wc.yaz0_store(out, align)), changed


def _sizes_into_table(p: Plan, sizes: dict[str, int]):
    """(new table, the mod it was built on or None for the game's, changed entries) for the table in use, or None
    when there is no table to read (no game files known and no mod ships one)."""
    w = p.winner(RSTB)
    if w is None:
        return None
    try:
        data = w.read()
    except (OSError, KeyError) as e:
        raise DeployError(f"the size table (RSTB) can't be read ({e})") from None
    new, changed = merge_sizes(data, sizes)
    if not changed:   # (the table in use fits already: it stays as it is)
        return None
    base = w.mod
    if base and base.startswith(GENERATED):    # (an earlier maker's table: keep what it was built on)
        g = p.generated.get(w.target) or {}
        base = g.get("built_on")
        changed = {**(g.get("sizes") or {}), **changed}
    return new, base, changed


def _game_check(p: Plan) -> dict:
    g = p.game
    replaced = new = 0
    outside, unused, bigger = [], [], []
    for target, (_, e) in p.winners.items():
        if e.dlc and not g.has_dlc:
            unused.append(target)
            continue
        gfiles, gdirs = (g.dlc_files, g.dlc_dirs) if e.dlc else (g.files, g.dirs)
        hit = gfiles.get(e.rel.lower())
        if hit:
            replaced += 1
            if e.size > hit[1]:
                bigger.append(target)
        elif e.rel.rpartition("/")[0].lower() in gdirs:
            new += 1
        else:
            outside.append(target)
    return {"replaced": replaced, "new": new, "outside_game_dirs": outside, "dlc_unused": unused,
            "bigger_than_game": bigger}


def _some(items: list[str], n: int = 5) -> str:
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


# A mod folder holding one of these is a BCML or UKMM package tools/bnp.py or tools/ukmm.py installed: the size-table
# entries its files need were worked out when it was added (.bnp/rstb.json, even an empty one; the UKMM install's own),
# so the deploy never says it may need entries
SIZES_CHECKED = (".bnp/rstb.json", ".ukmm/ukmm.json")


def sizes_checked(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    return any(os.path.isfile(os.path.join(path, *rel.split("/"))) for rel in SIZES_CHECKED)


def manifest_for(p: Plan, out: str, *, complete: bool, dry_run: bool = False) -> dict:
    """The report, also written as the overlay's marker file. No times: the same input gives the same file."""
    files: dict[str, dict] = {}
    won: Counter[int] = Counter()
    for target, (i, e) in p.winners.items():
        d = {"mod": p.mods[i].name, "from": e.member, "size": e.size, "how": e.how}
        if target in p.generated:  # (made at deploy: the owner is "generated:<what>")
            d["generated"] = True
            if p.generated[target].get("built_on"):
                d["built_on"] = p.generated[target]["built_on"]
        elif e.data is not None:
            d["merged_from"] = p.rstb_merged
        if e.dlc:
            d["dlc"] = True
        if e.alias:
            d["dlc_movie_copy"] = True
        else:
            won[i] += 1
        files[target] = d
    mods = [{"name": m.name, "path": m.path, "layout": ", ".join(m.layouts), "files": len(m.entries),
             "dlc_files": sum(e.dlc for e in m.entries), "deployed": won[i],
             "skipped": dict(sorted(Counter(r for _, r in m.skipped).items())), "notes": m.notes}
            for i, m in enumerate(p.mods)]

    rstb_from = next((p.mods[i].name for t, (i, _) in p.winners.items() if t.lower() == RSTB.lower()), None)
    rstb_mods = [m.name for m in p.mods if any(not e.dlc and e.rel.lower() == RSTB.lower() for e in m.entries)]
    check = _game_check(p) if p.game else None
    made_rstb = next((g for t, g in p.generated.items() if t.lower() == RSTB.lower() and t in files), None)
    if made_rstb is not None:   # (the table in use with entries for files made at deploy: S2)
        rstb_from = made_rstb.get("built_on")

    warnings: list[str] = list(p.warnings)
    for m in p.mods:
        for member, reason in m.skipped:
            if reason == R_DLC_MARK:
                warnings.append(f"{m.name}: skipped {member}. At the overlay's root it would turn on Savage Wind's "
                                "fix for a RomFS with the DLC merged in; DLC files go in 01007EF00011F001/romfs.")
    if made_rstb is not None:
        what = made_rstb["owner"][len(GENERATED):]
        warnings.append(f"The {what} made at deploy need bigger size-table (RSTB) entries, so the deploy ships "
                        f"{'the game' if not rstb_from else rstb_from}'s size table with them added. Start Savage Wind "
                        f"with {ENV_NO_HUD}=1 (modman run does): 0.4.1-alpha.4 stops its own RSTB fixes when the "
                        "overlay has one (issue #505). Keep the voice language the same as the text language.")
        others = [m.name for i, m in enumerate(p.mods) if won[i] and not m.owner and not sizes_checked(m.path)
                  and m.name != rstb_from and m.name not in p.rstb_merged]
        if others:
            warnings.append(f"The {'merged ' if p.rstb_merged else ''}RSTB has no size changes from "
                            f"{_some(others, 3)}; their files may need entries in it. Merge the mods with UKMM "
                            "(Switch mode) first.")
    elif rstb_from and p.rstb_merged:
        warnings.append(f"The RSTB is merged from the size tables of {', '.join(p.rstb_merged)}, so start Savage Wind "
                        f"with {ENV_NO_HUD}=1 (modman run does): 0.4.1-alpha.4 stops its own RSTB fixes when the "
                        "overlay has one (issue #505). Keep the voice language the same as the text language.")
        others = [m.name for i, m in enumerate(p.mods) if won[i] and m.name not in p.rstb_merged and not m.owner
                  and not sizes_checked(m.path)]
        if others:
            warnings.append(f"The merged RSTB has no size changes from {', '.join(others)}; their files may need "
                            "entries in it. Merge the mods with UKMM (Switch mode) first.")
    elif rstb_from:
        warnings.append(f"{rstb_from} ships an RSTB, so start Savage Wind with {ENV_NO_HUD}=1 (the env command "
                        "adds it): 0.4.1-alpha.4 stops its own RSTB fixes when the overlay has one (issue #505). "
                        "Keep the voice language the same as the text language.")
        others = [m.name for i, m in enumerate(p.mods) if won[i] and m.name != rstb_from and not m.owner
                  and not sizes_checked(m.path)]
        if others:
            warnings.append(f"The RSTB comes from {rstb_from} only; files from {', '.join(others)} may need entries "
                            "in it. Merge the mods with UKMM (Switch mode) first.")
    elif any(t not in p.generated for t in files):
        w = ("No mod ships an RSTB (ResourceSizeTable): new or bigger files may fail to load without entries "
             "in it. Merge the mods with UKMM (Switch mode) first and deploy its output.")
        if check and check["bigger_than_game"]:
            w += f" {len(check['bigger_than_game'])} file(s) are bigger than the game's."
        warnings.append(w)
    if check:
        if check["outside_game_dirs"]:
            o = check["outside_game_dirs"]
            warnings.append(f"{len(o)} file(s) are in folders the game's RomFS doesn't have; Savage Wind skips them: "
                            f"{_some(o)}")
        if check["dlc_unused"]:
            warnings.append(f"{len(check['dlc_unused'])} DLC file(s), but the game has no DLC set up "
                            "(no aoc folder next to its romfs): they won't be used.")
    long = [t for t in files if len(os.path.join(out, *t.split("/"))) > LONG_PATH]
    if long:
        warnings.append(f"{len(long)} path(s) are longer than {LONG_PATH} characters and may not open: "
                        f"use a shorter overlay folder ({_some(long, 2)})")

    notes = list(p.notes)
    boot = files.get(BOOTUP) or next((f for t, f in files.items() if t.lower() == BOOTUP.lower()), None)
    if boot:
        who = (f"The {boot['mod'][len(GENERATED):]} made at deploy" + (f" (over {boot['built_on']}'s)" if
               boot.get("built_on") else "")) if boot.get("generated") else boot["mod"]
        notes.append(f"{who} {'replace' if boot.get('generated') else 'replaces'} {BOOTUP}: Savage Wind's HUD button "
                     "letters stay as the game's.")
    if not p.game:
        notes.append("Paths not checked against the game: add --game-romfs <extracted\\romfs> to check them.")

    man = {"tool": TOOL_ID, "format": FORMAT, "complete": complete, "overlay": out,
           "game_romfs": p.game.romfs if p.game else None, "mods": mods, "files": files,
           "conflicts": p.conflicts, "rstb": {"shipped": bool(rstb_from) or made_rstb is not None, "from": rstb_from,
                                              "mods": rstb_mods,
                                              **({"merged_from": p.rstb_merged} if p.rstb_merged else {}),
                                              **({"made_by": made_rstb["owner"], "added": made_rstb.get("sizes") or {}}
                                                 if made_rstb is not None else {})},
           "game_check": check, "warnings": warnings, "notes": notes}
    if p.generated:  # (files made at deploy, by owner: "generated:pictures" -> its paths)
        gen: dict[str, list[str]] = {}
        for t, g in p.generated.items():
            if t in files:
                gen.setdefault(g["owner"], []).append(t)
        man["generated"] = gen
    man["env"] = env_for(out, man)
    if dry_run:
        man["dry_run"] = True
    return man


# ---------------------------------------------------------------- the overlay folder

def read_manifest(overlay_dir: str) -> dict | None:
    """The overlay's marker file, or None when there is none. Raises DeployError for someone else's file."""
    path = os.path.join(overlay_dir, MARKER)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            man = json.load(f)
    except (OSError, ValueError) as e:
        raise DeployError(f"can't read {path}: {e}") from None
    if not isinstance(man, dict) or man.get("tool") != TOOL_ID:
        raise DeployError(f"{path} wasn't written by this tool")
    return man


def env_for(overlay_dir: str, manifest: dict | None = None) -> dict[str, str]:
    """Environment variables to start Savage Wind with for this overlay folder."""
    if manifest is None:
        manifest = read_manifest(overlay_dir)
        if manifest is None:
            raise DeployError(f"{overlay_dir} has no {MARKER}: deploy mods there first")
        if manifest.get("complete") is not True:
            raise DeployError(f"the last deploy to {overlay_dir} didn't finish: run deploy again")
    env = {ENV_OVERLAY: os.path.abspath(overlay_dir)}
    if (manifest.get("rstb") or {}).get("shipped"):
        env[ENV_NO_HUD] = "1"  # 0.4.1-alpha.4: the HUD letters grow Common.blarc, which a mod's RSTB doesn't know
    return env


def _inside(child: str, parent: str) -> bool:
    c, p = os.path.normcase(os.path.abspath(child)), os.path.normcase(os.path.abspath(parent))
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:  # different drives
        return False


def _existing(path: str) -> str:
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def _ours(name: str) -> bool:
    """A name this tool writes at a folder's top besides the files: the marker, its .tmp, a link test."""
    return name.lower().startswith(MARKER)


def _listed(man: dict | None) -> set[str]:
    """The paths a marker file lists (lowercase), leaving out any that aren't plain relative paths."""
    files = (man or {}).get("files")
    if not isinstance(files, dict):
        return set()
    return {k.lower() for k in files if isinstance(k, str) and not any(_bad_name(x) for x in k.split("/"))}


def _foreign(folder: str, listed: set[str]) -> list[str]:
    """What `folder` holds besides the listed files and the folders they're in (folders end with "/")."""
    need = {p.rsplit("/", k)[0] for p in listed for k in range(1, p.count("/") + 1)}
    found = []
    stack = [""]
    while stack:
        rel = stack.pop()
        with os.scandir(os.path.join(folder, *rel.split("/")) if rel else folder) as it:
            for e in it:
                r = f"{rel}/{e.name}" if rel else e.name
                if not rel and _ours(e.name):
                    continue
                if e.is_dir(follow_symlinks=False) and not e.is_junction():
                    if r.lower() in need:
                        stack.append(r)
                    else:
                        found.append(r + "/")
                elif r.lower() not in listed:
                    found.append(r)
    return sorted(found)


def _delete_name_only(path: str) -> bool:
    """Delete one name of a read-only file that has other names (hard links) without clearing its read-only
    setting, which the names share: clearing it here would clear it on the player's or the game's own file too.
    Windows 10 1809 or newer, NTFS (FILE_DISPOSITION_IGNORE_READONLY_ATTRIBUTE). False when that isn't possible."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.SetFileInformationByHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    DELETE, SHARE_ALL, OPEN_EXISTING, OPEN_REPARSE_POINT = 0x10000, 7, 3, 0x200000
    FILE_DISPOSITION_INFO_EX = 21
    DELETE_FLAG, POSIX, IGNORE_READONLY = 0x1, 0x2, 0x10
    h = k32.CreateFileW(os.path.abspath(path), DELETE, SHARE_ALL, None, OPEN_EXISTING, OPEN_REPARSE_POINT, None)
    if h is None or h == wintypes.HANDLE(-1).value:
        return False
    try:
        for flags in (DELETE_FLAG | POSIX | IGNORE_READONLY, DELETE_FLAG | IGNORE_READONLY):
            info = wintypes.ULONG(flags)
            if k32.SetFileInformationByHandle(h, FILE_DISPOSITION_INFO_EX, ctypes.byref(info), ctypes.sizeof(info)):
                return True
        return False
    finally:
        k32.CloseHandle(h)


def _unlink(path: str) -> None:
    """Delete a file the deploy put in the overlay. A read-only hard link loses only this name; the read-only
    setting of the mod's or the game's own file stays as it is."""
    try:
        os.unlink(path)
        return
    except PermissionError:
        st = os.lstat(path)
        if st.st_mode & stat.S_IWRITE:
            raise  # (not read-only: something has it open)
        if st.st_nlink > 1:
            if _delete_name_only(path):
                return
            raise DeployError(f"Can't delete {path}: it is read-only and is also a file somewhere else (a hard "
                              "link), so clearing read-only would change that one too. Delete it yourself.") from None
    os.chmod(path, stat.S_IWRITE)  # (the overlay's own copy: nothing else shares the setting)
    os.unlink(path)


def _clear(folder: str, listed: set[str]) -> None:
    """Delete the listed files in `folder` (a hard link's other names keep the data), then the folders that
    leaves empty. Nothing else is touched."""
    dirs = set()
    for rel in listed:
        path = os.path.join(folder, *rel.split("/"))
        if os.path.isfile(path) or os.path.islink(path):
            _unlink(path)
        dirs.update(rel.rsplit("/", k)[0] for k in range(1, rel.count("/") + 1))
    for d in sorted(dirs, key=lambda x: -x.count("/")):
        try:
            os.rmdir(os.path.join(folder, *d.split("/")))
        except OSError:
            pass  # gone already, or not empty


def _remove_folder(folder: str, listed: set[str]) -> None:
    """Delete a folder this tool made (checked with _foreign first): its listed files, its marker, then it."""
    _clear(folder, listed)
    for n in os.listdir(folder):
        if _ours(n):
            _unlink(os.path.join(folder, n))
    os.rmdir(folder)


def _owned(out: str) -> dict | None:
    """The old manifest when `out` is ours, None when it's new or empty; refuses anything else."""
    if not os.path.lexists(out):
        return None
    if os.path.islink(out) or os.path.isjunction(out):
        raise DeployError(f"{out} is a link: pick a real folder")
    if not os.path.isdir(out):
        raise DeployError(f"{out} is a file, not a folder")
    man = read_manifest(out)
    if man is None:
        if [n for n in os.listdir(out) if not _ours(n)]:
            raise DeployError(f"{out} isn't empty and has no {MARKER}, so this tool didn't make it. "
                              "Pick a new or empty folder.")
        return None  # (empty, or our first write was cut short)
    foreign = _foreign(out, _listed(man))
    if foreign:
        raise DeployError(f"{out} has files this tool didn't put there: {_some(foreign)}. "
                          "Move them out of the folder, then deploy again.")
    return man


def _leftover(folder: str) -> set[str] | None:
    """The listed files of a ~new or ~old folder a cut-short deploy left (None when there's none);
    refuses a folder this tool didn't make."""
    if not os.path.lexists(folder):
        return None
    if os.path.islink(folder) or os.path.isjunction(folder) or not os.path.isdir(folder):
        raise DeployError(f"{folder} is in the way: move it, then deploy again")
    listed = _listed(read_manifest(folder))
    foreign = _foreign(folder, listed)
    if foreign:
        raise DeployError(f"{folder} is in the way (it holds {_some(foreign, 3)}): move it, then deploy again")
    return listed


def _reclaimable(folder: str, listed: set[str]) -> int:
    """Bytes freed by deleting the listed files: those with no other name (copies, not hard links)."""
    total = 0
    for rel in listed:
        try:
            st = os.stat(os.path.join(folder, *rel.split("/")), follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and st.st_nlink <= 1:
            total += st.st_size
    return total


def _links_work(folder: str) -> bool:
    """Whether hard links can be made in `folder` (an existing one): FAT32 and exFAT drives can't."""
    a = os.path.join(folder, f"{MARKER}.linktest-{os.getpid()}")
    b = a + "-2"
    try:
        with open(a, "wb"):
            pass
        os.link(a, b)
        return True
    except OSError:
        return False
    finally:
        for f in (b, a):
            try:
                os.unlink(f)
            except OSError:
                pass


def _write_marker(out: str, data: dict) -> None:
    tmp = os.path.join(out, MARKER + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, os.path.join(out, MARKER))


def _start_marker(out: str, files: dict) -> dict:
    """The marker written before any file: it lists every file the deploy may leave, so a cut-short deploy
    can be cleaned up without touching anything else."""
    return {"tool": TOOL_ID, "format": FORMAT, "complete": False, "overlay": out, "files": files}


def _size(n: int) -> str:
    return f"{n >> 20} MB" if n >= 1 << 20 else f"{n >> 10} KB" if n >= 1 << 10 else f"{n} bytes"


def _not_enough(need: int, free: int) -> DeployError:
    return DeployError(f"Not enough free space: {_size(need)} to copy, {_size(free)} free. Extract .zip mods "
                       "to folders on the overlay's drive (those are linked, not copied, where the drive allows).")


def _place(e: Entry, dest: str, zf: zipfile.ZipFile | None, mod_name: str = "") -> str:
    if e.data is not None:
        with open(dest, "wb") as f:
            f.write(e.data)
        return "copy"
    if e.src:
        # A read-only file is copied, not linked: a link would share its read-only setting with the mod's own file.
        if e.how == "link" and os.stat(e.src).st_mode & stat.S_IWRITE:
            try:
                os.link(e.src, dest)
                return "link"
            except OSError:  # another drive, or a drive without hard links (FAT, exFAT): copy, if it fits
                free = shutil.disk_usage(os.path.dirname(dest)).free
                if e.size > free:
                    raise _not_enough(e.size, free) from None
        shutil.copyfile(e.src, dest)
        return "copy"
    bad = f"{mod_name}: can't extract {e.member} from the .zip"
    try:
        fsrc = zf.open(e.zname)
    except Exception as x:  # (BadZipFile, NotImplementedError, RuntimeError, ...): a damaged or unusual .zip
        raise DeployError(f"{bad} ({x}). Extract it with 7-Zip and pass the folder.") from None
    with fsrc, open(dest, "wb") as fdst:
        while True:
            try:
                chunk = fsrc.read(1 << 20)
            except Exception as x:  # (a bad CRC, zlib.error, EOFError, ...): damaged data
                raise DeployError(f"{bad} ({x}): the .zip is damaged. Download it again.") from None
            if not chunk:
                return "copy"
            fdst.write(chunk)


def _fill(folder: str, p: Plan) -> None:
    made: set[str] = set()
    for target, (i, e) in p.winners.items():
        dest = os.path.join(folder, *target.split("/"))
        parent = os.path.dirname(dest)
        if parent not in made:
            os.makedirs(parent, exist_ok=True)
            made.add(parent)
        e.how = _place(e, dest, p.mods[i].zip, p.mods[i].name)


def _planned(p: Plan) -> dict:
    return {t: {"mod": p.mods[i].name, "size": e.size} for t, (i, e) in p.winners.items()}


def _deploy_in_place(p: Plan, out: str, old: dict | None) -> dict:
    """Fill `out` directly: for a new or empty folder, an unfinished deploy, or too little space for two."""
    os.makedirs(out, exist_ok=True)
    gone = _listed(old)
    old_files = (old or {}).get("files")
    old_files = ({k: v for k, v in old_files.items() if isinstance(k, str) and k.lower() in gone}
                 if isinstance(old_files, dict) else {})
    _write_marker(out, _start_marker(out, {**old_files, **_planned(p)}))
    _clear(out, gone)
    _fill(out, p)
    man = manifest_for(p, out, complete=True)
    _write_marker(out, man)
    return man


def _deploy_staged(p: Plan, out: str, old: dict) -> dict:
    """Build the new overlay in <out>~new, then swap it in. The old overlay stays when anything fails."""
    stage, aside = out + STAGE, out + ASIDE
    os.makedirs(stage)
    try:
        _write_marker(stage, _start_marker(out, _planned(p)))
        _fill(stage, p)
        man = manifest_for(p, out, complete=True)
        _write_marker(stage, man)
        try:
            os.rename(out, aside)
        except OSError as e:  # (Windows: a file in it is open, as when Savage Wind runs)
            raise DeployError(f"Couldn't replace {out} ({e.strerror or e}): close Savage Wind and anything else "
                              "using files in it, then deploy again. The old overlay is unchanged.") from None
        try:
            os.rename(stage, out)
        except OSError as e:
            try:
                os.rename(aside, out)
            except OSError:
                raise DeployError(f"Couldn't move the new overlay into place ({e}). The old one is in {aside}, the "
                                  f"new one in {stage}: rename one of them to {os.path.basename(out)}.") from None
            raise DeployError(f"Couldn't move the new overlay into place ({e}). The old overlay is unchanged.") \
                from None
    except BaseException:
        if os.path.isdir(out):  # (else the old overlay is in ~old and the new one in ~new: keep both)
            try:
                _remove_folder(stage, {t.lower() for t in p.winners})
            except OSError:
                pass  # the next deploy removes it
        raise
    try:
        _remove_folder(aside, _listed(old))
    except OSError as e:
        man = {**man, "warnings": [*man["warnings"], f"Couldn't delete the old overlay in {aside} "
                                   f"({e.strerror or e}): the next deploy deletes it."]}
    return man


def deploy(mod_paths: list, out: str, game_romfs: str | GameIndex | None = None, *,
           copy: bool = False, dry_run: bool = False, rstb: MergedRstb | None = None,
           plan_: Plan | None = None) -> dict:
    """Fill the overlay folder `out` from the mods (later mods win) and return the manifest. `rstb`: a size table
    merged from the mods' own (tools/bnp.py), deployed in the place of theirs. `plan_`: a plan made already (with
    option folders and files made at deploy added); then mod_paths, game_romfs and rstb are not used."""
    out = os.path.abspath(out)
    if os.path.dirname(out) == out:
        raise DeployError(f"{out} is a drive root: pick a folder on it")
    if plan_ is not None:
        p, game = plan_, plan_.game
    else:
        game = game_romfs if isinstance(game_romfs, GameIndex) or game_romfs is None else GameIndex.scan(game_romfs)
        p = plan(mod_paths, game, rstb)
    try:
        for src in [m.path for m in p.mods if not m.owner] + ([game.romfs, *game.aoc] if game else []):
            if _inside(src, out) or _inside(out, src):
                raise DeployError(f"The overlay folder and {src} overlap: use a separate folder for the overlay.")
        old = _owned(out)
        leftovers = {f: _leftover(f) for f in (out + STAGE, out + ASIDE)}

        where = _existing(out)
        out_dev = os.stat(where).st_dev
        candidates = {id(e) for _, e in p.winners.values() if e.src and not copy and e.dev == out_dev}
        links = bool(candidates) and _links_work(where)
        for _, e in p.winners.values():
            e.how = "link" if links and id(e) in candidates else "copy"
        staged = bool(old and old.get("complete") is True)  # (keep a working overlay until the new one is ready)
        need = sum(e.size for _, e in p.winners.values() if e.how == "copy")
        if need:
            free = shutil.disk_usage(where).free
            if need > free:
                more = _reclaimable(out, _listed(old)) if old else 0
                if need > free + more:
                    raise _not_enough(need, free + more)
                staged = False  # room only once the old overlay's copies are deleted
        if dry_run:
            return manifest_for(p, out, complete=False, dry_run=True)

        for f, listed in leftovers.items():
            if listed is not None:
                _remove_folder(f, listed)
        return _deploy_staged(p, out, old) if staged else _deploy_in_place(p, out, old)
    finally:
        for m in p.mods:
            if m.zip:
                m.zip.close()


# ---------------------------------------------------------------- command line

def format_report(man: dict) -> str:
    files = man["files"]
    links = sum(f["how"] == "link" for f in files.values())
    copies = len(files) - links
    n = len(man["mods"])
    if man.get("dry_run"):
        out = [f"Would deploy {n} mod(s): {len(files)} file(s) to {man['overlay']} "
               f"({links} as links, {copies} as copies). Nothing written."]
    else:
        out = [f"Deployed {n} mod(s): {len(files)} file(s) in {man['overlay']} ({links} linked, {copies} copied)."]
    for i, m in enumerate(man["mods"], 1):
        line = f"  {i}. {m['name']} ({m['layout']}): {m['files']} file(s)"
        if m["dlc_files"]:
            line += f", {m['dlc_files']} DLC"
        if m["files"] > m["deployed"]:
            line += f", {m['files'] - m['deployed']} overridden"
        if m["skipped"]:
            line += "; skipped " + ", ".join(f"{c} {r}" for r, c in m["skipped"].items())
        out.append(line)
        out += [f"     {note}" for note in m["notes"]]
    conflicts = man["conflicts"]
    if conflicts:
        out.append(f"Conflicts: {len(conflicts)} file(s) in several mods; the last one wins:")
        out += [f"  {c['path']}: {', '.join(c['mods'])} -> {c['winner']}" for c in conflicts[:10]]
        if len(conflicts) > 10:
            out.append(f"  ... and {len(conflicts) - 10} more (see {MARKER})")
    if man["game_check"]:
        g = man["game_check"]
        out.append(f"Against the game: {g['replaced']} replaced, {g['new']} new, "
                   f"{len(g['outside_game_dirs'])} in folders it lacks.")
    if man["warnings"]:
        out.append("Warnings:")
        out += [f"  - {w}" for w in man["warnings"]]
    if man["notes"]:
        out.append("Notes:")
        out += [f"  - {w}" for w in man["notes"]]
    out.append("Start Savage Wind with:")
    out += [f"  {k}={v}" for k, v in man["env"].items()]
    if not man.get("dry_run"):
        out.append(f"Full list: {os.path.join(man['overlay'], MARKER)}")
    return "\n".join(out)


def _ps_string(value: str) -> str:
    """`value` as a PowerShell expression in plain ASCII ('...' + [char]0x3A9 + '...'), so no code page between
    here and Invoke-Expression can change it. (PowerShell also takes curly quotes as quotes: those are escaped.)"""
    parts, run = [], ""
    for ch in value:
        if ch.isascii():
            run += ch
            continue
        if run:
            parts.append("'" + run.replace("'", "''") + "'")
            run = ""
        units = ch.encode("utf-16-le", "surrogatepass")
        parts += [f"[char]0x{int.from_bytes(units[k:k + 2], 'little'):04X}" for k in range(0, len(units), 2)]
    if run or not parts:
        parts.append("'" + run.replace("'", "''") + "'")
    if not parts[0].startswith("'"):
        parts.insert(0, "''")  # (so + joins strings)
    return " + ".join(parts)


def _env_line(name: str, value: str, fmt: str) -> str:
    if fmt == "ps":
        return f"$env:{name} = {_ps_string(value)}"
    if fmt == "cmd":
        return f'set "{name}={value.replace("%", "%%")}"'  # (for a .cmd file, where %% is a %)
    return f"{name}={value}"


def env_lines(env: dict[str, str], fmt: str) -> list[str]:
    """The lines `env` prints. ps and cmd also clear SAVAGEWIND_NO_HUD_LETTERS when it isn't needed, so a
    window used for an earlier deploy doesn't keep it (Savage Wind only checks that it is set)."""
    lines = [_env_line(k, v, fmt) for k, v in env.items()]
    if ENV_NO_HUD not in env:
        if fmt == "ps":
            lines.append(f"Remove-Item Env:{ENV_NO_HUD} -ErrorAction SilentlyContinue")
        elif fmt == "cmd":
            lines.append(f'set "{ENV_NO_HUD}="')
    if fmt == "cmd" and not all(v.isascii() for v in env.values()):
        lines.insert(0, "chcp 65001 >nul")  # (cmd reads the rest of the file as UTF-8, which main() prints)
    return lines


def main(argv: list[str] | None = None) -> int:
    try:  # UTF-8 when redirected or piped (a console gets Unicode anyway), so paths keep every letter
        if sys.stdout.isatty():
            sys.stdout.reconfigure(errors="replace")
        else:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deploy", help="fill the overlay folder from the mods (later mods win)")
    d.add_argument("--out", required=True, help="overlay folder: new, empty, or made by this tool")
    d.add_argument("--game-romfs", help="the game's romfs folder (or Savage Wind's extracted folder), to check paths")
    d.add_argument("--copy", action="store_true", help="copy files instead of hard-linking them")
    d.add_argument("--dry-run", action="store_true", help="say what would happen; write nothing")
    d.add_argument("--json", action="store_true", help="print the report as JSON")
    d.add_argument("mods", nargs="+", help="mod folders or .zip files, lowest priority first")
    e = sub.add_parser("env", help="print the environment variables to start Savage Wind with")
    e.add_argument("--out", required=True, help="the overlay folder")
    e.add_argument("--format", choices=("plain", "ps", "cmd"), default="plain",
                   help="plain NAME=value, PowerShell, or cmd.exe lines")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "deploy":
            man = deploy(args.mods, args.out, args.game_romfs, copy=args.copy, dry_run=args.dry_run)
            # (--json is ASCII, with \u escapes: it reads back the same whatever the reader's code page)
            print(json.dumps(man, indent=1, sort_keys=True, ensure_ascii=True) if args.json else format_report(man))
            return 0
        env = env_for(args.out)
        if args.format != "ps":
            for v in env.values():
                try:
                    v.encode("utf-8")
                except UnicodeEncodeError:
                    raise DeployError(f"{v} has characters that can't be printed: use --format ps") from None
        print("\n".join(env_lines(env, args.format)))
        return 0
    except (DeployError, OSError, zipfile.BadZipFile, RuntimeError) as e:  # (RuntimeError: NotImplementedError too)
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
