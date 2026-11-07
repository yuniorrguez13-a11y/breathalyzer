#!/usr/bin/env python3
"""Fail if files look like Nintendo game files, keys, saves or anything made from the game.

    python tools/check_clean.py            the repo: the files git would commit (tracked + untracked, minus .gitignore)
    python tools/check_clean.py FOLDER     every file in FOLDER, ignored by git or not (only .git folders are skipped),
                                           e.g. a release folder before you upload it

Exit code 0: clean. 1: something was found (one BLOCKED line per file). 2: FOLDER isn't a folder.
Without git, the repo check walks the repo, skipping .git and upstream/ (the local Savage Wind clone).
In FOLDER, a link to another folder is blocked rather than followed: check that folder on its own.

It looks for: game dump and RomFS files (by type, magic bytes or RomFS path, wherever they are); folders the tools fill
from the game (extracted, exefs, romfs, out, generated, saves' save_N, and any folder holding a marker file of
sw_rebuild, romfs_mods or modman); C code and objects made by Savage Wind's generator; saves; code maps; Savage Wind
logs; screenshots from Savage Wind's `shot` command; console keys and title keys; files over 10 MB.

A path listed in .cleanallow (in the repo, or in FOLDER; one per line, a line starting with # is a comment) is
skipped: use that only for files we made ourselves that a rule mistakes for game files, with a comment line saying why.
"""
from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
from itertools import islice
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAX_BYTES = 10 * 1024 * 1024
HEAD_BYTES = 1024 * 1024

BLOCKED_EXT = {
    # dumps, containers, executables, tickets
    ".nsp", ".nsz", ".xci", ".xcz", ".nca", ".ncz", ".nso", ".wua", ".wux", ".wud", ".rpx", ".rpl", ".keys", ".tik",
    # BotW game data
    ".sarc", ".ssarc", ".pack", ".spack", ".bactorpack", ".sbactorpack", ".blarc", ".sblarc",
    ".bfres", ".sbfres", ".bntx", ".bflim", ".sbflim", ".bitemico", ".sbitemico", ".gtx",
    ".bfstm", ".bars", ".bfwav", ".bwav", ".bfsar", ".bffnt",
    ".msbt", ".smsbt", ".byml", ".sbyml", ".mubin", ".smubin", ".bgdata", ".sbgdata",
    ".bfevfl", ".sbfevfl", ".bquestpack", ".sbquestpack", ".beventpack", ".sbeventpack",
    ".hkx", ".hknm2", ".shknm2", ".hksc", ".shksc", ".hkrb", ".shkrb", ".hkrg", ".shkrg", ".tscb",
    # saves (game_data.sav, option.sav, caption.sav, tracker/trackblockNN.sav)
    ".sav",
}
BLOCKED_NAMES = {"swgame.dll", "swgame.so", "swgame.dylib", "main.npdm", "prod.keys", "title.keys", "dev.keys",
                 "savagewind.log", "savagewind.prev.log"}
BLOCKED_DIRS = {"extracted", "generated", "exefs", "romfs", "out"}
SAVE_DIR_RE = re.compile(r"save_\d+")  # the game's save folder (SAVAGEWIND_SAVE_DIR holds save_0/<slot>/...)
# Marker files our tools leave in the folders they fill from the game: sw_rebuild's work and cache folders (generated
# C, objects, logs), romfs_mods' overlay and modman's deploy folder (copies of and links to game files).
TOOL_MARKERS = (".sw_rebuild-work", ".sw_rebuild-cache", ".mod-deploy.json", ".modman-deploy.json")
MAGICS = (b"SARC", b"Yaz0", b"Yaz1", b"NSO0", b"FRES", b"BNTX", b"PFS0", b"HFS0", b"MsgStdBn",
          b"AAMP", b"FSTM", b"BARS", b"BFEVFL", b"Gfx2", b"CFNT", b"FFNT", b"TSCB")
# Key lines (one line each, [ \t] so a match never runs into the next line):
# - prod.keys / dev.keys: <name with key or kek> = <32+ hex>: header_key, master_key_0a, master_kek_00, mariko_kek,
#   titlekek_00, tsec_root_key_02, eticket_rsa_kek, key_area_key_*, package1_key_00, bis_key_00, ...
# - title.keys: <32-hex rights id> = <32-hex title key>
# - Cemu's keys.txt: <32-hex key> # game name
KEY_RE = re.compile(rb"^[ \t]*[a-z0-9_]{0,48}(?:key|kek)[a-z0-9_]{0,48}[ \t]*=[ \t]*[0-9a-f]{32,}"
                    rb"|^[ \t]*[0-9a-f]{32}[ \t]*=[ \t]*[0-9a-f]{32}[ \t]*\r?$"
                    rb"|^[ \t]*[0-9a-f]{32}[ \t]+#", re.M | re.I)

# Savage Wind's generator (tools/gen.py) writes one C function per game function, `void f_<address>(Cpu *c)`, into
# <module>_NNN.c chunks plus <module>_table.c, and the module layout into layout.c (game_modules[]).
C_EXT = {".c", ".h", ".i", ".inc", ".cc", ".cpp", ".cxx", ".hh", ".hpp"}
GEN_FN_RE = re.compile(rb"\bf_[0-9a-f]{6,16}[ \t]*\([ \t]*(?:struct[ \t]+)?Cpu[ \t]*\*")
# In a file that isn't C, this many generated-function signatures means pasted generator output (a chunk holds up to
# 1500 functions). Tests that feed made-up chunks to our tools hold a few dozen, which is fine.
GEN_FN_MANY = 100
GEN_LAYOUT_RE = re.compile(rb"\bGameLibModule[ \t]+game_modules[ \t]*\[")
OBJ_EXT = {".o", ".obj", ".a", ".lib"}
GEN_SYM_RE = re.compile(rb"(?<![0-9A-Za-z])f_[0-9a-f]{8,16}(?![0-9A-Za-z])")  # a generated function's symbol
GEN_OBJ_NAME_RE = re.compile(r"(?:main|sdk|subsdk\d+|rtld)_(?:\d{3}|table)")  # main_000.o, sdk_table.obj, ...

# RomFS files that no type or magic rule catches: system text files, cutscenes, compendium and album pictures.
ROMFS_FILES = ("system/version.txt", "system/regionlangmask.txt")
VIDEO_EXT = {".webm", ".mp4", ".mkv"}
CUTSCENE_RE = re.compile(r"demo\d{3}_\d+")  # Movie/Demo104_1.webm
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp"}
ROMFS_IMAGE_DIRS = {("ui", "picturebook"), ("ui", "album")}

# codemap.py's functions.csv, classes.csv and summary.txt
CODEMAP_RE = re.compile(rb"\A(?:\xef\xbb\xbf)?(?:address,name,kind,notes\r?\n|class,typeinfo,vtables,"
                        rb"|Code map of \w+, build [0-9a-f]{8})")
# Savage Wind's log (savagewind.log, or its stdout/stderr): it repeats the game's own log text
LOG_RE = re.compile(rb"^\[savagewind\] Savage Wind |^\[gamelib\] ", re.M)


def is_game_screenshot(head: bytes) -> bool:
    """A PNG as Savage Wind's `shot` command writes it (stb_image_write): 8-bit RGBA, IHDR followed directly by the
    IDAT, stb's zlib header 78 5E, and the game's 16:9 picture."""
    if len(head) < 43 or not head.startswith(b"\x89PNG\r\n\x1a\n") or head[12:16] != b"IHDR":
        return False
    w, h, depth, color = struct.unpack(">IIBB", head[16:26])
    return (depth == 8 and color == 6 and w > 0 and w * 9 == h * 16
            and head[37:41] == b"IDAT" and head[41:43] == b"\x78\x5e")


def tool_marker(folder: str, cache: dict[str, str]) -> str:
    """The marker file of a folder our tools fill from the game, held by folder or any folder above it ('' if none)."""
    key = os.path.normcase(os.path.abspath(folder))
    if key not in cache:
        found = next((m for m in TOOL_MARKERS if os.path.isfile(os.path.join(key, m))), "")
        parent = os.path.dirname(key)
        cache[key] = found or (tool_marker(parent, cache) if parent != key else "")
    return cache[key]


def walk_files(root: Path, skip: frozenset[str] = frozenset({".git"})) -> tuple[list[str], list[tuple[str, str]]]:
    """Every file under root (paths relative to it, with /), and the folders that couldn't be read or are links to
    other folders (not followed: a junction to the game would pull it all in, or loop), with the reason."""
    files: list[str] = []
    bad: list[tuple[str, str]] = []
    stack = [""]
    while stack:
        rel = stack.pop()
        try:
            with os.scandir(root / rel if rel else root) as it:
                entries = list(it)
        except OSError as e:
            bad.append((rel + "/", f"can't read this folder: {e.strerror or e}"))
            continue
        for e in entries:
            r = f"{rel}/{e.name}" if rel else e.name
            try:
                link = e.is_symlink() or getattr(e, "is_junction", lambda: False)()
                is_dir = e.is_dir()  # (follows a link: a link to a file is checked as that file)
            except OSError as ex:
                bad.append((r, f"can't read it: {ex.strerror or ex}"))
                continue
            if is_dir and link:
                bad.append((r + "/", "a link to another folder (not followed): check that folder on its own"))
            elif is_dir:
                if e.name not in skip:
                    stack.append(r)
            else:
                files.append(r)
    return sorted(files), sorted(bad)


def repo_files(root: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """The files git would commit in the repo at root (tracked + untracked, minus .gitignore). Without git, or if git
    lists nothing, every file, skipping .git and upstream/."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
                             capture_output=True, check=True).stdout
        files = sorted(p for p in out.decode("utf-8", "replace").split("\0") if p)
        if files:
            return files, []
    except (OSError, subprocess.CalledProcessError):
        pass
    return walk_files(root, frozenset({".git", "upstream"}))


def problems_for(root: Path, rel: str, markers: dict[str, str] | None = None) -> list[str]:
    path = root / rel
    parts = rel.lower().split("/")
    name, dirs = parts[-1], parts[:-1]
    stem, ext = os.path.splitext(name)
    out = []
    if BLOCKED_DIRS & set(dirs):
        out.append("inside a folder for game files or generated code")
    if any(SAVE_DIR_RE.fullmatch(d) for d in dirs):
        out.append("inside a game save folder (save_N)")
    mark = tool_marker(str(path.parent), {} if markers is None else markers)
    if mark:
        out.append(f"inside a folder a tool filled from the game (it holds {mark})")
    if name in BLOCKED_NAMES:
        out.append("file name of a game file, key file or game log")
    if ext in BLOCKED_EXT:
        out.append(f"game file type ({ext})")
    if any(rel.lower() == p or rel.lower().endswith("/" + p) for p in ROMFS_FILES):
        out.append("a file of the game's RomFS (same path)")
    if ext in VIDEO_EXT and ((dirs and dirs[-1] == "movie") or CUTSCENE_RE.fullmatch(stem)):
        out.append("a cutscene video of the game (RomFS Movie/)")
    if ext in IMAGE_EXT and tuple(dirs[-2:]) in ROMFS_IMAGE_DIRS:
        out.append("a picture of the game's RomFS (UI/PictureBook, UI/Album)")
    if name == "caption.jpg" and (path.parent / "caption.sav").is_file():
        out.append("a save's picture (next to caption.sav)")
    if ext in OBJ_EXT and GEN_OBJ_NAME_RE.fullmatch(stem):
        out.append("an object file named like Savage Wind's generated game code")
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:  # (an object's symbols are at its end: read all of it)
            head = f.read(size if ext in OBJ_EXT and size <= MAX_BYTES else min(size, HEAD_BYTES))
    except OSError as e:
        return out + [f"can't read it: {e}"]
    if size > MAX_BYTES:
        out.append(f"bigger than {MAX_BYTES // (1024 * 1024)} MB")
    if head.startswith(MAGICS):
        out.append(f"starts like a game file ({head[:4]!r})")
    if len(head) >= 4 and ((head[:2] == b"BY" and head[2] == 0 and 1 <= head[3] <= 7)
                           or (head[:2] == b"YB" and head[3] == 0 and 1 <= head[2] <= 7)):
        out.append("starts like a BYML game file")
    if KEY_RE.search(head):
        out.append("looks like console keys")
    if ext in C_EXT and (GEN_FN_RE.search(head) or GEN_LAYOUT_RE.search(head)):
        out.append("C code generated from the game (Savage Wind's generator output)")
    elif ext not in OBJ_EXT and len(list(islice(GEN_FN_RE.finditer(head), GEN_FN_MANY))) == GEN_FN_MANY:
        out.append("holds C code generated from the game (Savage Wind's generator output)")
    if ext in OBJ_EXT and GEN_SYM_RE.search(head):
        out.append("compiled from C code generated from the game (has its f_<address> functions)")
    if CODEMAP_RE.match(head):
        out.append("a code map made from the game (tools/codemap.py output)")
    if LOG_RE.search(head):
        out.append("a Savage Wind log (it repeats the game's own log text)")
    if is_game_screenshot(head):
        out.append("a screenshot of the game (as Savage Wind's `shot` command saves them)")
    return out


def read_allow(root: Path) -> set[str]:
    try:
        text = (root / ".cleanallow").read_text(encoding="utf-8")
    except OSError:
        return set()
    lines = (ln.strip().replace("\\", "/") for ln in text.splitlines())
    return {ln[2:] if ln.startswith("./") else ln for ln in lines if ln and not ln.startswith("#")}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) > 1:
        print("usage: python tools/check_clean.py [FOLDER]", file=sys.stderr)
        return 2
    if argv:
        root = Path(argv[0])
        if not root.is_dir():
            print(f"{root} isn't a folder", file=sys.stderr)
            return 2
        files, unreadable = walk_files(root)
    else:
        root = REPO
        files, unreadable = repo_files(root)
    allowed = read_allow(root)
    markers: dict[str, str] = {}
    bad = 0
    for rel, why in unreadable:
        if rel.rstrip("/") not in allowed:
            bad += 1
            print(f"BLOCKED {rel}: {why}")
    for rel in files:
        if rel in allowed:
            continue
        found = problems_for(root, rel, markers)
        if found:
            bad += 1
            print(f"BLOCKED {rel}: {'; '.join(found)}")
    print(f"{len(files)} file(s) checked, {bad} blocked." if bad else f"Clean: {len(files)} file(s) checked.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
