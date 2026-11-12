#!/usr/bin/env python3
"""Install BCML mod packages (.bnp) as Switch mods for Savage Wind. `tools/modman.py add` uses this.

    python tools/bnp.py info <.bnp, or a BNP folder or .zip>     the package's name, version, options and logs

A .bnp is a 7-Zip archive holding info.json (name, version, options), the mod's files in the Switch layout
(01007EF00011E000/romfs, 01007EF00011F001/romfs for the DLC), logs/ (what BCML merges itself: rstb.json, packs.json,
deepmerge.aamp, aslist.aamp, gamedata/savedata/texts/map logs...) and options/<folder>/ with the same layout.
BCML removes from the packs every file the mod doesn't change, and keeps some changes only as logs, so its files
can't be used as they are. Installing one here (each step in install()):

1. lists the archive (7-Zip's 7z.exe, else Windows' own tar.exe), refuses any name that would land outside the
   folder (.., absolute paths, drive letters, reserved names), a name starting with '-' (a tool could read it as an
   option) or a link, then extracts it into a new folder;
2. lays the base files out as a Switch mod, then each chosen option over them (options marked default, unless the
   player picks others);
3. fills each pack (SARC) back from the game's own copy: the files the mod left out come from the game, the mod's
   own files win, nested packs too (BCML's pack merge for one mod);
4. applies logs/deepmerge.aamp (actor parameter changes) to the game's parameter files, then logs/aslist.aamp
   (animation lists, .baslist: new animation archives, sequences and blend rules, matched by name) to the game's lists;
   then the data logs, each kind of the base and the options combined and merged by its module into the mod's copy of
   the game file (else the game's), back in its packs (merge_logs): tools/bcml_data.py (actorinfo, gamedata,
   savedata, eventinfo, effects, areadata, residents), bcml_maps.py (map, mainstatic, dstatic, quests) and
   bcml_texts.py (texts.json in every language the game has, drops.json, shop.aamp);
5. writes a size table (RSTB) for the mod when that changes the game's: the game's own, with the sizes from
   logs/rstb.json and sizes for the files it rebuilt (never smaller than the game's; a size of 0 removes the entry, as
   BCML means it);
6. names every log it can't merge (BCML 2's, unknown or damaged ones, parts a merger leaves out): modman shows them as
   "Needs attention".

The library copy keeps .bnp/ next to the mod's files (info.json, the logs, bnp.json, rstb.json): `deploy` merges
the size table changes of several enabled mods into one table (the larger size wins) instead of using the last one.
Packs and the files the logs made are not merged between mods yet: the later mod's copy wins, and deploy names what
an earlier package loses (deploy_hook).
Nothing here runs anything from the package, and no name from it is read as an option (names starting with '-' are
refused, the others come after "--"); the game's files are only read.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mod_inspect as mi  # noqa: E402
import romfs_mods as rm  # noqa: E402
import wiiu_convert as wc  # noqa: E402

TOOL_ID = "botw-mod-loader bnp"
META = ".bnp"                         # in the library copy: names starting with '.' are never deployed
SEVEN_ZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"
BASE_TID, DLC_TID = "01007EF00011E000", "01007EF00011F001"
RSTB_REL = f"{BASE_TID}/romfs/{rm.RSTB}"
DLC_CANON = "Aoc/0010/"               # how the size table names DLC files
MAX_INFO = 1 << 20
MAX_MEMBERS = 200_000
MAX_UNPACKED = 20 << 30               # a package listing more (BotW's whole RomFS is ~15 GB) is refused
MAX_MEMBER = 4 << 30                  # and one listing a single file bigger than this
SPARE_MIN = 2 << 30                   # free space kept on the drive when extracting: this, or 1% of it if more
BELOW_NORMAL = 0x00004000             # Windows process priority for the extractor: keeps the PC responsive
NO_WINDOW = 0x08000000
ADDING = ".adding-"                   # modman's stage folders (and this module's work folders next to them)
STALE_AFTER = 3600                    # seconds untouched before such a folder counts as left by an add cut short
# Packs (SARC archives) by extension, without the "s" a Yaz0-compressed copy adds
PACK_EXTS = {"pack", "sarc", "bactorpack", "beventpack", "blarc", "bgenvb", "stera", "stats", "bfarc"}
# BCML 3's logs, all merged: the first four here, the others by tools/bcml_data.py, bcml_texts.py and bcml_maps.py
# (see log_mergers)
LOG_NAMES = {
    "rstb.json": "sizes", "packs.json": "pack list", "deepmerge.aamp": "actor parameter changes",
    "aslist.aamp": "animation lists",
    "actorinfo.yml": "actor info", "gamedata.yml": "game data flags", "savedata.yml": "save data flags",
    "eventinfo.yml": "event info", "effects.yml": "status effects", "areadata.yml": "area data",
    "residents.yml": "resident actors", "texts.json": "texts", "drops.json": "drop tables", "shop.aamp": "shop data",
    "map.yml": "map changes", "mainstatic.yml": "main field static actors", "dstatic.yml": "shrine entrances",
    "quests.yml": "quests",
}
# BCML 2's logs (BCML 3 refuses such packages too): named, not merged
OLD_LOGS = {"savedata.sarc": "save data", "deepmerge.yml": "actor parameter changes"}


class BnpError(Exception):
    """A package that can't be installed, or no tool to read it. The message is for the player."""


# ---------------------------------------------------------------------------------------------------- extracting

@dataclass
class Member:
    name: str        # "/"-separated, as checked
    size: int
    is_dir: bool
    raw: str = ""    # the name as the archive spells it (".\info.json", say): what the tool that listed it is given


def _run(argv: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    flags = (BELOW_NORMAL | NO_WINDOW) if os.name == "nt" else 0
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                              creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise BnpError(f"{os.path.basename(argv[0])} didn't run: {e}") from None


def archive_tools() -> list[tuple[str, str]]:
    """The programs that can read a .bnp (7-Zip archive), best first: ("7z", 7z.exe) and ("tar", Windows' tar.exe,
    whose libarchive reads 7-Zip archives). Only these two are run, by full path."""
    found: list[tuple[str, str]] = []
    for env in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base and os.path.isfile(os.path.join(base, "7-Zip", "7z.exe")):
            found.append(("7z", os.path.join(base, "7-Zip", "7z.exe")))
    which = shutil.which("7z.exe") or shutil.which("7z")
    if which:
        found.append(("7z", os.path.abspath(which)))
    tar = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe")
    if os.name == "nt" and os.path.isfile(tar):
        found.append(("tar", tar))
    out, seen = [], set()
    for kind, path in found:
        if os.path.normcase(path) not in seen:
            seen.add(os.path.normcase(path))
            out.append((kind, path))
    return out


def _no_tool() -> BnpError:
    return BnpError("Can't open a .bnp (a 7-Zip archive) on this PC: install 7-Zip (7-zip.org), or extract the .bnp "
                    "with any archive tool and add the folder.")


UNIX_MODE = re.compile(r"[-?a-z][-rwxsStT]{9}")


def _list_7z(exe: str, path: str) -> list[tuple[str, int, str, bool]]:
    r = _run([exe, "l", "-slt", "-sccUTF-8", "--", os.path.abspath(path)], timeout=300)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).decode("utf-8", "replace").strip().splitlines()
        raise BnpError(f"7-Zip can't read {os.path.basename(path)}" + (f": {msg[-1]}" if msg else ""))
    text = r.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
    if "\n----------\n" not in text:
        return []
    body = text.split("\n----------\n", 1)[1]
    out = []
    for block in body.split("\n\n"):
        kv = {}
        for line in block.split("\n"):
            k, sep, v = line.partition(" = ")
            if sep:
                kv[k.strip()] = v
        if "Path" not in kv:
            continue
        if kv.get("Encrypted") == "+":
            raise BnpError(f"{os.path.basename(path)} is password-protected: extract it yourself, then add the "
                           "folder.")
        attr = kv.get("Attributes", "").split()   # Windows' letters ("DL": a junction), a Unix mode: either may lack
        unix = next((a for a in attr if UNIX_MODE.fullmatch(a)), "")
        win = next((a for a in attr if not UNIX_MODE.fullmatch(a)), "")
        is_dir = "D" in win or unix.startswith("d") or kv.get("Folder") == "+"
        link = ("L" in win or unix.startswith("l") or bool(kv.get("Symbolic Link")) or bool(kv.get("Hard Link"))
                or bool(kv.get("Link")))
        try:
            size = int(kv.get("Size") or 0)
        except ValueError:
            size = 0
        out.append((kv["Path"], size, "link" if link else "dir" if is_dir else "file", link))
    return out


TAR_LINE = re.compile(r"^(\S)\S*\s+\d+\s+\S+\s+\S+\s+(\d+)\s")


def _tar_text(data: bytes) -> str:
    """tar.exe's output: Windows' bsdtar writes names in the ANSI code page ("mbcs"), not UTF-8."""
    return data.decode("mbcs" if os.name == "nt" else "utf-8", "replace").replace("\r\n", "\n")


def _list_tar(exe: str, path: str) -> list[tuple[str, int, str, bool]]:
    path = os.path.abspath(path)
    names = _run([exe, "-tf", path], timeout=300)
    long = _run([exe, "-tvf", path], timeout=300)
    if names.returncode != 0 or long.returncode != 0:
        msg = _tar_text(names.stderr or long.stderr).strip()
        raise BnpError(f"tar.exe can't read {os.path.basename(path)}" + (f": {msg.splitlines()[-1]}" if msg else ""))
    nlines = [x for x in _tar_text(names.stdout).split("\n") if x]
    llines = [x for x in _tar_text(long.stdout).split("\n") if x]
    odd = next((x for x in nlines if not x.isascii() or "?" in x), None)
    if odd is not None:
        # (a letter the code page lacks comes out as '?', and Windows' "best fit" turns others into look-alikes, so
        # such a name can't be matched with the files tar.exe writes)
        raise BnpError(f"Windows' tar.exe can't read {os.path.basename(path)} reliably: it holds a name with letters "
                       f"beyond plain ASCII ({odd!r}). Install 7-Zip (7-zip.org) and add it again, or extract the "
                       "package with any archive tool and add the folder.")
    if len(nlines) != len(llines):
        raise BnpError(f"{os.path.basename(path)} holds names tar.exe can't list cleanly (a line break in a name?)")
    out = []
    for name, line in zip(nlines, llines):
        m = TAR_LINE.match(line)
        kind = m.group(1) if m else "?"
        size = int(m.group(2)) if m else 0
        link = kind in ("l", "h")
        out.append((name, size, "link" if link else "dir" if kind == "d" or name.endswith("/") else "file", link))
    return out


def check_name(raw: str, archive: str = "the package") -> list[str]:
    """The safe "/"-separated parts of an archive member's name. Refuses one that would land outside the folder
    (absolute, a drive letter, ..), and names Windows can't hold or treats specially (reserved names like CON or
    NUL, : streams, trailing dots)."""
    n = raw.replace("\\", "/")
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n) or ".." in parts or not parts:
        raise BnpError(f"Not added: {archive} holds a file with an unsafe path ({raw!r}) that would be written "
                       "outside the mod folder.")
    if any(rm._bad_name(p) for p in parts):
        raise BnpError(f"Not added: {archive} holds a name Windows can't use safely ({raw!r}).")
    return parts


def list_archive(path: str) -> tuple[list[Member], str]:
    """The checked members of a .bnp (or any archive 7-Zip or tar.exe reads) and the tool that read it ("7z" or
    "tar": extract() and read_member() use only that one). Refuses unsafe names, names starting with '-', links,
    password protection, and two files with the same name (in any case)."""
    tools = archive_tools()
    if not tools:
        raise _no_tool()
    name = os.path.basename(path)
    errors = []
    for kind, exe in tools:
        try:
            raw = _list_7z(exe, path) if kind == "7z" else _list_tar(exe, path)
        except BnpError as e:
            errors.append(str(e))
            continue
        break
    else:
        raise BnpError(errors[0] if len(errors) == 1 else " ".join(errors))
    if len(raw) > MAX_MEMBERS:
        raise BnpError(f"Not added: {name} holds {len(raw)} files: too many for a mod.")
    members: list[Member] = []
    seen: set[str] = set()
    for n, size, what, link in raw:
        if what == "dir" and not [p for p in n.replace("\\", "/").split("/") if p not in ("", ".")]:
            continue  # (the archive's own top folder, "./")
        parts = check_name(n, name)
        if n.startswith("-") or parts[0].startswith("-"):
            raise BnpError(f"Not added: {name} holds a name starting with '-' ({n!r}), which an archive tool could "
                           "take for an option.")
        if link:
            raise BnpError(f"Not added: {name} holds a link ({n}). Mods don't need links; extract it yourself if "
                           "you trust it.")
        rel = "/".join(parts)
        if what == "dir":
            members.append(Member(rel, 0, True, n))
            continue
        key = rel.lower()
        if key in seen:
            raise BnpError(f"Not added: {name} has two files named {rel}.")
        seen.add(key)
        members.append(Member(rel, size, False, n))
    return members, kind


def extract(path: str, dest: str) -> list[Member]:
    """Extract a .bnp into dest, a new folder (it must not exist yet). Lists and checks every name first, then checks
    that only the listed files came out and that none is a link. On any refusal dest is removed again."""
    if os.path.lexists(dest):
        raise BnpError(f"{dest} already exists: extracting needs a new folder.")
    members, kind = list_archive(path)
    files = [m for m in members if not m.is_dir]
    need = sum(m.size for m in files)
    big = max(files, key=lambda m: m.size, default=None)
    if need > MAX_UNPACKED:
        raise BnpError(f"Not added: {os.path.basename(path)} unpacks to {need >> 30} GB, more than the whole game: "
                       "damaged, or not a mod.")
    if big is not None and big.size > MAX_MEMBER:
        raise BnpError(f"Not added: {os.path.basename(path)} holds a file of {big.size >> 30} GB ({big.name}), "
                       "bigger than any game file: damaged, or not a mod.")
    parent = os.path.dirname(os.path.abspath(dest))
    while not os.path.isdir(parent) and os.path.dirname(parent) != parent:
        parent = os.path.dirname(parent)
    usage = shutil.disk_usage(parent)
    spare = max(SPARE_MIN, usage.total // 100)   # (room kept for the game, Windows and the rebuilt files)
    if need + spare > usage.free:
        raise BnpError(f"Not enough free space to extract {os.path.basename(path)}: {need >> 20} MB needed and "
                       f"{spare >> 20} MB kept free, {usage.free >> 20} MB free.")
    exe = next(e for k, e in archive_tools() if k == kind)
    os.makedirs(dest)
    try:
        if kind == "7z":
            r = _run([exe, "x", "-y", "-sccUTF-8", "-snl-", f"-o{os.path.abspath(dest)}", "--", os.path.abspath(path)])
        else:
            r = _run([exe, "-xf", os.path.abspath(path), "-C", os.path.abspath(dest)])
        if r.returncode != 0:
            msg = (r.stderr or r.stdout).decode("utf-8", "replace").strip().splitlines()
            raise BnpError(f"Couldn't extract {os.path.basename(path)}" + (f": {msg[-1]}" if msg else "") + ".")
        want = {m.name.lower(): m for m in files}
        got = _walk(dest)
        extra = sorted(r for r in got if r.lower() not in want)
        if extra:
            raise BnpError(f"Extracting {os.path.basename(path)} gave files it didn't list ({', '.join(extra[:3])}): "
                           "not added.")
        missing = sorted(m.name for k, m in want.items() if k not in {g.lower() for g in got})
        if missing:
            raise BnpError(f"Couldn't extract {len(missing)} file(s) of {os.path.basename(path)} "
                           f"({', '.join(missing[:3])}): the archive may be damaged.")
        _writable(got.values())
        return members
    except BaseException:
        _remove(dest)
        raise


def _writable(paths) -> None:
    """Clear the read-only setting 7-Zip brings back from the archive, on files this module extracted (so they can be
    moved into the mod and replaced there)."""
    for p in paths:
        try:
            if not os.lstat(p).st_mode & stat.S_IWRITE:
                os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
        except OSError:
            pass


def _walk(root: str) -> dict[str, str]:
    """"/"-separated relative path -> absolute path of every file under root. Refuses links and junctions."""
    out: dict[str, str] = {}
    stack = [""]
    while stack:
        rel = stack.pop()
        with os.scandir(os.path.join(root, *rel.split("/")) if rel else root) as it:
            for e in it:
                r = f"{rel}/{e.name}" if rel else e.name
                if e.is_symlink() or e.is_junction():
                    raise BnpError(f"{r} is a link: not added.")
                if e.is_dir():
                    stack.append(r)
                elif e.is_file():
                    out[r] = e.path
    return out


def _remove(path: str) -> bool:
    """Remove a folder this module made (extraction or work folder). Never follows links (there are none: _walk
    refuses them, and the extractors are told not to make them). False when something is left (a file in use)."""
    if not os.path.lexists(path):
        return True
    def onerror(func, p, _exc):
        try:
            os.chmod(p, 0o666)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onexc=onerror) if sys.version_info >= (3, 12) else shutil.rmtree(path, onerror=onerror)
    return not os.path.lexists(path)


def _newest(path: str) -> float:
    """The latest change time of a folder and the folders in it (an add still working in it keeps touching them)."""
    out = os.lstat(path).st_mtime
    stack = [path]
    while stack:
        with os.scandir(stack.pop()) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False) and not e.is_junction():
                    out = max(out, e.stat(follow_symlinks=False).st_mtime)
                    stack.append(e.path)
    return out


def sweep_stale(mods_dir: str, remove=None, older: float = STALE_AFTER) -> list[str]:
    """Remove what an add cut short (killed, a power cut) left in the library's mods folder: ".adding-*" folders
    (modman's stage folders and this module's ".bnp-raw" work folders, hundreds of MB for a big package) untouched for
    `older` seconds. `remove` deletes one folder (modman's remove_tree, which never follows links). Returns the
    folders that couldn't be removed."""
    try:
        names = sorted(os.listdir(mods_dir))
    except OSError:
        return []
    now, left = time.time(), []
    for n in names:
        p = os.path.join(mods_dir, n)
        try:
            if (not n.startswith(ADDING) or os.path.islink(p) or os.path.isjunction(p) or not os.path.isdir(p)
                    or now - _newest(p) < older):
                continue
        except OSError:
            continue
        try:
            (remove or _remove)(p)
        except Exception:  # noqa: BLE001 (best effort: what is left is returned; modman's remover has its own error)
            pass
        if os.path.lexists(p):
            left.append(p)
    return left


def read_member(path: str, member: str, limit: int = MAX_INFO,
                listed: tuple[list[Member], str] | None = None) -> bytes:
    """One small file of an archive, read to memory (info.json, say). Nothing is written. `listed`: list_archive's
    result for it (else it is listed now). The file is read with the tool that listed the archive, never another one,
    by the name the archive spells it, after "--" (so no name is read as an option); what comes out must be the size
    the listing gave."""
    members, kind = listed or list_archive(path)
    base = os.path.basename(path)
    want = member.replace("\\", "/").strip("/")
    files = [m for m in members if not m.is_dir]
    hit = next((m for m in files if m.name == want), None) or next(
        (m for m in files if m.name.lower() == want.lower()), None)
    if hit is None:
        raise BnpError(f"{base} has no {member}.")
    if hit.size > limit:
        raise BnpError(f"{member} in {base} is too big.")
    raw = hit.raw or hit.name
    if raw.startswith("-") or check_name(raw, base)[0].startswith("-"):
        raise BnpError(f"Not read: {base} names a file starting with '-' ({raw!r}).")
    exe = next((e for k, e in archive_tools() if k == kind), None)
    if exe is None:
        raise _no_tool()
    src = os.path.abspath(path)
    r = _run([exe, "e", "-so", "-sccUTF-8", "-spd", "--", src, raw] if kind == "7z" else [exe, "-xOf", src, "--", raw],
             timeout=300)
    if r.returncode != 0 or len(r.stdout) != hit.size:
        raise BnpError(f"Can't read {member} from {base} with {'7-Zip' if kind == '7z' else 'tar.exe'}"
                       + ("" if r.returncode else f" (it gave {len(r.stdout)} of its {hit.size} bytes)")
                       + ": the archive may be damaged.")
    return r.stdout


def is_archive(path: str) -> bool:
    """A 7-Zip archive (a .bnp), by its first bytes."""
    try:
        with open(path, "rb") as f:
            return f.read(6) == SEVEN_ZIP_MAGIC
    except OSError:
        return False


# ---------------------------------------------------------------------------------------------------- packed downloads
# A download modman add can't use as it is: a .rar, a .7z that isn't a .bnp, a .zip holding archives. It is unpacked
# into a work folder with the same tools and checks as a .bnp (extract()), level by level, and the mods in it are
# found with mod_inspect. Nothing from it is run.

RAR_MAGIC = b"Rar!\x1a\x07"
ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06")
PACKED_EXTS = (".7z", ".rar", ".zip", ".bnp")
UNPACK_LEVELS = 3            # the download, an archive in it, one in that: nothing deeper is opened
BOMB_RATIO = 1000            # an archive listing more than this many times its own size...
BOMB_MIN = 1 << 30           # ...and more than this in all is refused (made to fill the disk)
GAME_VERSION_RE = re.compile(r"(?<![\d.])v?(1\.\d{1,2}\.\d{1,2})(?![\d.])", re.I)  # in a folder's name
VERSION_WORDS = {"v", "ver", "version", "for", "switch", "nx", "botw", "game", "update", "only"}  # around it
KEEP_WRAPPER = {"sdcafiine"}   # (folders _unwrap doesn't go into: the mod would be named after them)


def archive_magic(path: str) -> str | None:
    """"7z", "rar" or "zip", by the file's first bytes (None: none of them, or it can't be read)."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if head.startswith(SEVEN_ZIP_MAGIC):
        return "7z"
    if head.startswith(RAR_MAGIC):
        return "rar"
    return "zip" if head[:4] in ZIP_MAGICS else None


def packed_download(src: str, findings: list = (), names: list[str] = ()) -> bool:
    """Whether modman add unpacks src first (unpack_download): a .rar, a 7-Zip archive without a BCML info.json (so
    not a .bnp), or a .zip that holds archives and isn't a BCML or UKMM package itself. `findings` and `names`:
    mod_inspect's for src. Reads only."""
    if not os.path.isfile(src):
        return False
    kind = archive_magic(src)
    if kind == "rar":
        return True
    if kind == "7z":
        try:
            members, _ = list_archive(src)
        except BnpError:
            return False  # (peek() says why)
        return find_info([m.name for m in members if not m.is_dir]) is None
    if kind == "zip":
        package = any(getattr(f, "kind", "") in ("BCML mod (BNP)", "UKMM package") for f in findings)
        return not package and any(n.lower().endswith(PACKED_EXTS) for n in names)
    return False


@dataclass
class Found:
    """One mod in a download."""
    label: str    # its name for --option: the file name of a package in it, else the folder's (or the download's)
    path: str     # what modman add is given: a folder, or a package file, in the work folder
    what: str     # what it holds, for the player


@dataclass
class Download:
    name: str                                         # the download's file name
    found: list[Found] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)    # what was left out (folders for other game versions...)
    levels: int = 1                                   # archives opened one inside the other
    files: int = 0                                    # listed by every archive opened, together
    size: int = 0
    boxes: int = 0                                    # work subfolders made

    def pick(self, picks: list[str] = ()) -> tuple[Found, list[str]]:
        """The mod to add, and the picks left for its own options. One mod: that one. Several: the one a pick names
        (its whole name, or a part only it has); none named, or nothing found: BnpError saying what is there."""
        left = "".join(f"\n  - left out: {n}" for n in self.notes)
        if not self.found:
            raise BnpError(f"Not added: {self.name} holds no BotW mod (no RomFS files, patches, cheats, DLLs or "
                           f"mod packages, also in the archives inside it).{left}")
        if len(self.found) == 1:
            return self.found[0], list(picks)
        for k, pick in enumerate(picks):
            want = pick.strip().casefold()
            hits = [f for f in self.found if f.label.casefold() == want] or \
                   [f for f in self.found if want and want in f.label.casefold()]
            if len(hits) == 1:
                return hits[0], [p for j, p in enumerate(picks) if j != k]
        shown = "".join(f'\n  - "{f.label}": {f.what}' for f in self.found)
        raise BnpError(f"Not added: {self.name} holds {len(self.found)} mods. Add it again with --option and the "
                       f"name of the one you want (once for each one you want):{shown}{left}")

    def box(self, work: str, name: str) -> str:
        """A new folder in work for one archive's files, named like it (modman names a mod after its folder)."""
        self.boxes += 1
        clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:60].strip(" .") or "mod"
        if rm._bad_name(clean):
            clean = "mod-" + clean
        return os.path.join(work, str(self.boxes), clean)


def unpack_download(src: str, work: str, say=print) -> Download:
    """Unpack a download (packed_download) into work, a new folder, and find the mods in it: each archive with
    extract()'s checks (names, links, sizes, free space) plus: every file the size the archive lists, a budget for
    all levels together (MAX_MEMBERS, MAX_UNPACKED), no archive listing over BOMB_RATIO times its size above
    BOMB_MIN, at most UNPACK_LEVELS archives one inside the other. A .bnp or a mod .zip in it is a mod as it is (not
    opened); another archive is opened in turn. Of folders named for game versions (1.6.0/, 1.9.0/), only the
    one for 1.9.0 (else the newest) is kept. Nothing from the download is run. On a refusal work is left for the
    caller to remove (it is named ".adding-...", so an add cut short leaves nothing for long: sweep_stale)."""
    if os.path.lexists(work):
        raise BnpError(f"{work} already exists: unpacking needs a new folder.")
    os.makedirs(work)
    dl = Download(os.path.basename(src))
    stem = os.path.splitext(dl.name)[0]
    root = dl.box(work, stem)
    _unpack_one(src, root, 1, dl, say)
    _find_mods(root, os.path.basename(root), 1, work, dl, say)
    for f in dl.found:   # (two named alike, as two archives with one name in two folders: numbered)
        same = [g for g in dl.found if g.label.casefold() == f.label.casefold()]
        if len(same) > 1:
            for k, g in enumerate(same, 1):
                g.label = f"{g.label} ({k})"
    return dl


def _unpack_one(path: str, dest: str, level: int, dl: Download, say) -> None:
    name = os.path.basename(path)
    if archive_magic(path) == "rar" and not any(k == "7z" for k, _ in archive_tools()):
        raise BnpError(f"Can't open {name}: a .rar needs 7-Zip on this PC (7-zip.org). Or extract it with any "
                       "archive tool and add the folder.")
    members, _ = list_archive(path)
    files = [m for m in members if not m.is_dir]
    listed = sum(m.size for m in files)
    packed = max(os.path.getsize(path), 1)
    if listed > BOMB_MIN and listed > packed * BOMB_RATIO:
        raise BnpError(f"Not added: {name} would unpack to {listed >> 30} GB from {max(packed >> 20, 1)} MB: that "
                       "isn't a mod (an archive made to fill the disk).")
    dl.files += len(files)
    dl.size += listed
    if dl.files > MAX_MEMBERS:
        raise BnpError(f"Not added: {dl.name} holds over {MAX_MEMBERS} files with the archives in it: too many for "
                       "a mod.")
    if dl.size > MAX_UNPACKED:
        raise BnpError(f"Not added: {dl.name} unpacks to over {MAX_UNPACKED >> 30} GB with the archives in it: "
                       "damaged, or not a mod.")
    say(f"Unpacking {name}...")
    extract(path, dest)
    got = {k.lower(): v for k, v in _walk(dest).items()}
    for m in files:
        p = got.get(m.name.lower())
        if p is None or os.path.getsize(p) != m.size:
            raise BnpError(f"Not added: {name} gave {m.name} with another size than it lists: damaged, or made to "
                           "fool archive tools.")
    dl.levels = max(dl.levels, level)


def _unwrap(folder: str) -> str:
    """The folder inside the wrapper folders an archive often has (one folder holding everything, not one with a
    meaning like romfs/ or a title id)."""
    while True:
        try:
            with os.scandir(folder) as it:
                kids = [e for e in it if e.name.lower() not in mi.JUNK]
        except OSError:
            return folder
        low = kids[0].name.lower() if len(kids) == 1 else ""
        if (not low or not kids[0].is_dir(follow_symlinks=False) or low in mi.STRUCTURAL | mi.ROMFS_TOP | KEEP_WRAPPER
                or re.fullmatch(r"[0-9a-f]{16}", low)):
            return folder
        folder = kids[0].path


def _game_version(folder: str, name: str) -> str | None:
    """The game version a folder is for: its name ("1.6.0", "v1.9.0", "For Switch 1.6.0": a version and only such
    words, not a mod's own "Armor v1.2.0"), else the build its patches name."""
    m = GAME_VERSION_RE.search(name)
    if m and set(re.sub(r"[\W_]+", " ", f"{name[:m.start()]} {name[m.end():]}".lower()).split()) <= VERSION_WORDS:
        return m.group(1)
    for rel, path in _walk(folder).items():
        if rel.lower().endswith((".pchtxt", ".ips")):
            try:
                with open(path, "rb") as f:
                    head = f.read(4096)
            except OSError:
                continue
            hit = mi.NSOBID_RE.search(head)
            stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
            build = hit.group(1).decode().lower() if hit else stem if re.fullmatch(r"[0-9a-f]{16,64}", stem) else ""
            if mi.KNOWN_BUILDS.get(build[:16]):
                return mi.KNOWN_BUILDS[build[:16]]
    return None


def _drop_other_versions(base: str, dl: Download) -> None:
    """Of two or more folders named for game versions side by side, keep the one for 1.9.0 (else the newest) and
    remove the others from the work folder (named in dl.notes)."""
    try:
        with os.scandir(base) as it:
            dirs = [(e.name, e.path) for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        return
    versions = {path: (name, v) for name, path in dirs if (v := _game_version(path, name))}
    if len(versions) < 2:
        return
    def key(path):
        return tuple(int(x) for x in versions[path][1].split("."))
    keep = next((p for p in versions if versions[p][1] == mi.TARGET_VERSION), None) or max(versions, key=key)
    for path, (name, v) in sorted(versions.items()):
        if path != keep:
            _remove(path)
            dl.notes.append(f"{name}/ (for game version {v})")
    name, v = versions[keep]
    if v != mi.TARGET_VERSION:
        dl.notes.append(f"kept {name}/, for game version {v}: the newest it has (yours is {mi.TARGET_VERSION}), so its "
                        "patches may need porting")


def _what(findings: list) -> str:
    return ", ".join(dict.fromkeys(f.kind for f in findings))


def _find_mods(folder: str, label: str, level: int, work: str, dl: Download, say) -> None:
    """Add the mods in an unpacked folder to dl.found: its own files when mod_inspect finds a mod in them (archives
    moved out first), and each archive in it (a package as it is, any other one opened, one level deeper)."""
    base = _unwrap(folder)
    if base != folder:
        label = os.path.basename(base)
    _drop_other_versions(base, dl)
    nested = []
    for rel, path in sorted(_walk(base).items()):
        if rel.lower().endswith(PACKED_EXTS) and archive_magic(path):
            stem = os.path.splitext(os.path.basename(path))[0]
            moved = os.path.join(os.path.dirname(dl.box(work, stem)), os.path.basename(path))
            os.makedirs(os.path.dirname(moved))
            os.replace(path, moved)
            nested.append((rel, moved, stem))
    own = _walk(base)
    findings = mi.inspect(mi.FolderSource(Path(base))) if own else []
    if findings or any(r.lower().endswith(".dll") for r in own):
        path = base
        if any(f.kind == mi.BARE_WIIU for f in findings):
            # (a Wii U mod's content folder without its name: modman converts a folder named content)
            path = os.path.join(dl.box(work, label), "content")
            os.makedirs(os.path.dirname(path))
            top = (mi.bare_root(list(own)) or "").strip("/")
            os.replace(os.path.join(base, *top.split("/")) if top else base, path)
        dl.found.append(Found(label, path, _what(findings) or "DLLs"))
    for rel, path, stem in nested:
        kind = archive_magic(path)
        if kind == "7z":
            members, _ = list_archive(path)
            if find_info([m.name for m in members if not m.is_dir]) is not None:
                dl.found.append(Found(stem, path, "BCML package (.bnp)"))
                continue
        elif kind == "zip":
            found, inner = [], []
            try:
                src = mi.ZipSource(Path(path))
                try:
                    found = mi.inspect(src)
                    inner = [n for n in src.files if n.lower().endswith(PACKED_EXTS)]
                finally:
                    src.zf.close()
            except (OSError, ValueError, RuntimeError, EOFError, NotImplementedError, zipfile.BadZipFile, zlib.error):
                found = []   # (a .zip Python can't read, as one packed with Deflate64: 7-Zip opens it below)
            if found and not inner:
                dl.found.append(Found(stem, path, _what(found)))
                continue
        if level >= UNPACK_LEVELS:
            raise BnpError(f"Not added: {dl.name} holds archives packed more than {UNPACK_LEVELS} deep ({rel} is "
                           "one more): not opened.")
        dest = dl.box(work, stem)
        _unpack_one(path, dest, level + 1, dl, say)
        _find_mods(dest, os.path.basename(dest), level + 1, work, dl, say)


# ---------------------------------------------------------------------------------------------------- the package

@dataclass
class Option:
    name: str
    folder: str
    desc: str = ""
    default: bool = False
    group: str = ""          # a "single" group (choose one); "" for a "multi" option
    required: bool = False   # the group needs a choice


@dataclass
class Info:
    name: str
    version: str
    platform: str
    options: list[Option]
    raw: dict
    prefix: str = ""         # the folder inside the archive or folder that holds info.json ("" at the top)

    def summary(self) -> list[str]:
        out = [f"{self.name}" + (f" {self.version}" if self.version else "") + f" ({self.platform or 'switch'})"]
        if not self.options:
            out.append("  no options")
        for o in self.options:
            where = f"one of '{o.group}'" if o.group else "option"
            out.append(f"  {where}: {o.name} (folder {o.folder})" + (" [default]" if o.default else ""))
        return out


def parse_info(data: bytes, where: str = "info.json") -> Info:
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        raise BnpError(f"{where} can't be read ({e}).") from None
    if not isinstance(raw, dict):
        raise BnpError(f"{where} isn't a BCML info file.")
    name = str(raw.get("name") or "").strip()
    version = str(raw.get("version") or "").strip()
    platform = str(raw.get("platform") or "").strip().lower()
    options: list[Option] = []
    opts = raw.get("options") or {}
    if not isinstance(opts, dict):
        opts = {}
    for o in opts.get("multi") or []:
        if isinstance(o, dict) and o.get("folder"):
            options.append(Option(str(o.get("name") or o["folder"]), _folder(o["folder"], where),
                                  str(o.get("desc") or ""), bool(o.get("default"))))
    for g in opts.get("single") or []:
        if not isinstance(g, dict):
            continue
        gname = str(g.get("name") or "choice")
        for o in g.get("options") or []:
            if isinstance(o, dict) and o.get("folder"):
                options.append(Option(str(o.get("name") or o["folder"]), _folder(o["folder"], where),
                                      str(o.get("desc") or ""), bool(o.get("default")), gname,
                                      bool(g.get("required"))))
    return Info(name, version, platform, options, raw)


def _folder(value, where: str) -> str:
    folder = str(value).replace("\\", "/").strip("/")
    if "/" in folder or rm._bad_name(folder):
        raise BnpError(f"{where} names an option folder that isn't a plain folder name ({value!r}).")
    return folder


def choose(info: Info, picks: list[str] = (), no_defaults: bool = False) -> list[Option]:
    """The options to install, in the package's order: the default ones (none with no_defaults), plus the ones
    picked by name or folder. Picking one of a group ("single") replaces the group's default. A required group
    gets its first option when nothing in it is chosen (refused with no_defaults)."""
    chosen = [] if no_defaults else [o for o in info.options if o.default]
    for pick in picks:
        want = pick.strip().casefold()
        hit = next((o for o in info.options if want in (o.name.casefold(), o.folder.casefold())), None)
        if hit is None:
            names = ", ".join(f"'{o.name}'" for o in info.options) or "none"
            raise BnpError(f"{info.name or 'The package'} has no option '{pick}'. Its options: {names}.")
        if hit.group:
            chosen = [o for o in chosen if o.group != hit.group]
        if hit not in chosen:
            chosen.append(hit)
    groups = {o.group: o.required for o in info.options if o.group}
    for g, required in groups.items():
        if required and not any(o.group == g for o in chosen):
            if no_defaults:
                raise BnpError(f"{info.name or 'The package'} needs one option of '{g}': pick it with --bnp-option.")
            chosen.append(next(o for o in info.options if o.group == g))
    return [o for o in info.options if o in chosen]


def find_info(files: list[str]) -> str | None:
    """Where info.json is among a package's files: at the top, or in the one folder that holds everything."""
    low = {f.lower(): f for f in files}
    if "info.json" in low:
        return low["info.json"]
    tops = {f.split("/", 1)[0] for f in files if "/" in f}
    if len(tops) == 1 and not any("/" not in f for f in files):
        top = tops.pop()
        hit = low.get(f"{top.lower()}/info.json")
        if hit:
            return hit
    return None


def package_kind(src: str, findings: list = (), names: list[str] = ()) -> str | None:
    """What modman add installs with this module: "archive" (a .bnp: any 7-Zip archive), "folder" or "zip" (an
    extracted Switch BCML package: mod_inspect's BCML finding, and logs/ among `names`), else None. A BCML folder
    without logs/ is used as it is, and a Wii U one goes to tools/wiiu_convert.py as before. Reads only."""
    if os.path.isfile(src) and is_archive(src):
        return "archive"
    if any(getattr(f, "kind", "") == "BCML mod (BNP)" and getattr(f, "platform", "") != "wiiu" for f in findings)             and any(n.lower().startswith("logs/") for n in names):
        return "zip" if os.path.isfile(src) else "folder"
    return None


def peek(src: str, kind: str) -> Info:
    """The package's info.json, read without extracting anything (an archive member goes to memory only)."""
    base = os.path.basename(src)
    if kind == "archive":
        members, _ = list_archive(src)
        listed = list_archive(src)
        rel = find_info([m.name for m in listed[0] if not m.is_dir])
        if rel is None:
            raise BnpError(f"Not added: {base} is a 7-Zip archive without a BCML info.json, so it isn't a .bnp. "
                           "Extract it, then add the folder.")
        info = parse_info(read_member(src, rel, listed=listed), f"{base}: info.json")
        info.prefix = rel[:-len("info.json")]
        return info
    if kind == "zip":
        with zipfile.ZipFile(src) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            rel = find_info(names)
            if rel is None:
                raise BnpError(f"Not added: {base} has no BCML info.json.")
            with zf.open(rel) as f:
                info = parse_info(f.read(MAX_INFO + 1)[:MAX_INFO], f"{base}: info.json")
        info.prefix = rel[:-len("info.json")]
        return info
    rel = find_info(list(_walk(src)))
    if rel is None:
        raise BnpError(f"Not added: {base} has no BCML info.json.")
    with open(os.path.join(src, *rel.split("/")), "rb") as f:
        info = parse_info(f.read(MAX_INFO), f"{base}: info.json")
    info.prefix = rel[:-len("info.json")]
    return info


# ---------------------------------------------------------------------------------------------------- RSTB

def round32(n: int) -> int:
    return (n + 31) & ~31


class Rstb:
    """The Switch size table: CRC-32 of a resource path -> size, plus a few full names (paths whose CRC-32 is
    shared). Read from the game's file (Yaz0 or not), changed, written back."""

    def __init__(self, data: bytes):
        self.yaz0_align = 0
        try:
            if data[:4] == b"Yaz0":
                data, self.yaz0_align = unyaz0(data)
            order = wc.rstb_order(data)
            crcs, named = wc.rstb_load(data)
        except wc.FormatError as e:
            raise BnpError(f"The size table can't be read ({e}).") from None
        if order != wc.LE and len(data) > 12:
            raise BnpError("The size table is a Wii U (big-endian) one, not the Switch's.")
        self.crc: dict[int, int] = dict(crcs)
        self.names: dict[str, int] = {}
        for raw, size in named:
            self.names[raw.split(b"\0", 1)[0].decode("utf-8", "replace")] = size

    def key(self, name: str):
        return name if name in self.names else zlib.crc32(name.encode())

    def get(self, key) -> int | None:
        if isinstance(key, str):
            key = self.key(key)
        return self.names.get(key) if isinstance(key, str) else self.crc.get(key)

    def _changes(self, k, size: int) -> bool:
        old = self.get(k)
        return old is not None if size == 0 else (old is None or size > old)

    def changes(self, entries: dict) -> dict:
        """The entries (a name or a CRC-32 -> size, keys kept as given) that apply() would change."""
        return {k: v for k, v in entries.items() if self._changes(k, v)}

    def keyed(self, entries: dict) -> dict:
        """The entries with this table's keys (a CRC-32, or the name for a path it lists by name), so one file has
        one key whether a mod named it or another mod's table holds its CRC-32 (merge_entries' rules within)."""
        return merge_entries([{self.key(k) if isinstance(k, str) else k: v} for k, v in entries.items()])

    def apply(self, entries: dict) -> int:
        """Set the sizes (a name or a CRC-32 -> size): the larger of the game's and the given one; 0 removes the
        entry. Returns how many entries changed."""
        n = 0
        for k, size in entries.items():
            k = self.key(k) if isinstance(k, str) else k
            table = self.names if isinstance(k, str) else self.crc
            if not self._changes(k, size):
                continue
            if size == 0:
                del table[k]
            else:
                table[k] = size
            n += 1
        return n

    def to_bytes(self, compress: bool = True) -> bytes:
        out = bytearray(b"RSTB" + struct.pack("<II", len(self.crc), len(self.names)))
        for k in sorted(self.crc):
            out += struct.pack("<II", k, self.crc[k])
        for name, size in self.names.items():
            out += name.encode()[:127].ljust(128, b"\0") + struct.pack("<I", size)
        return wc.yaz0_compress(bytes(out), self.yaz0_align) if compress else bytes(out)

    def diff(self, other: Rstb) -> dict:
        """What `other` (a mod's table) changes against this one (the game's): key -> size, 0 for a removed entry."""
        out: dict = {}
        for k, v in other.crc.items():
            if self.crc.get(k) != v:
                out[k] = v
        for k in self.crc:
            if k not in other.crc:
                out[k] = 0
        for k, v in other.names.items():
            if self.names.get(k) != v:
                out[k] = v
        for k in self.names:
            if k not in other.names:
                out[k] = 0
        return out


def merge_entries(tables: list[dict]) -> dict:
    """Several mods' size changes as one: the larger size of each file; a removal (0) wins, as the file it stands
    for has no size anyone could work out."""
    out: dict = {}
    for t in tables:
        for k, v in t.items():
            if not isinstance(v, int) or v < 0:
                continue
            if k in out and out[k] == 0:
                continue
            out[k] = 0 if v == 0 else max(v, out.get(k, 0))
    return out


def read_rstb_json(data: bytes, where: str) -> dict[str, int]:
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        raise BnpError(f"{where} can't be read ({e}).") from None
    if isinstance(raw, dict) and isinstance(raw.get("hash_map"), dict):  # (another BCML version's layout)
        raw = {**{k: v for k, v in raw.get("name_map", {}).items()}, **raw["hash_map"]}
    if not isinstance(raw, dict):
        raise BnpError(f"{where} isn't a size list.")
    out: dict[str, int] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2**32:
            out[k.replace("\\", "/")] = v
    return out


def canon(rel: str, data: bytes | None = None, dlc: bool = False) -> str:
    """A resource's name in the size table: its path in the RomFS (DLC files under Aoc/0010/), with the "s" a
    Yaz0-compressed copy adds to the extension taken off (.sbactorpack -> .bactorpack)."""
    head, _, base = rel.rpartition("/")
    stem, dot, ext = base.rpartition(".")
    if dot and len(ext) > 1 and ext[0].lower() == "s" and (data[:4] == b"Yaz0" if data is not None
                                                           else ext.lower() != "sarc"):
        base = f"{stem}.{ext[1:]}"
    return (DLC_CANON if dlc else "") + (f"{head}/{base}" if head else base)


def needed_size(game_size: int, old_len: int, new_len: int, pack: bool, structs: int | None = None) -> int:
    """The size table entry a rebuilt file needs, from the game's entry for its vanilla version: a pack needs its
    bytes plus the same overhead; an animation list (structs: what the game allocates for its new entries, see
    aslist_growth) its growth plus those; another file grows its entry in proportion."""
    if structs is not None:
        return game_size + round32(max(0, new_len - old_len)) + structs
    if new_len <= old_len or old_len <= 0:
        return game_size
    if pack:
        return game_size + round32(new_len) - round32(old_len)
    return round32(math.ceil(game_size * new_len / old_len))


# ---------------------------------------------------------------------------------------------------- packs

def _ext(name: str) -> str:
    base = name.rsplit("/", 1)[-1].lower()
    return base.rsplit(".", 1)[-1] if "." in base else ""


def is_packish(name: str) -> bool:
    e = _ext(name)
    return e in PACK_EXTS or (e.startswith("s") and e[1:] in PACK_EXTS)


YAZ0_MAX = 256 << 20       # a Yaz0 file that says it unpacks to more is refused (the game's own are far smaller)
YAZ0_RATIO = 128           # Yaz0 can't unpack to more than about 87 times its size: a header claiming more lies


def unyaz0(data: bytes) -> tuple[bytes, int]:
    """wc.yaz0_decompress (the bytes and the header's alignment) after checking the size the header claims: more
    than YAZ0_MAX, or more than the stream could ever unpack to, is refused (wc.FormatError) before any memory is
    taken, so a small damaged or hostile file can't make an add use gigabytes. Every Yaz0 a mod or the game gives the
    BCML mergers goes through here."""
    if len(data) >= 16 and data[:4] == b"Yaz0":
        size = struct.unpack_from(">I", data, 4)[0]
        if size > YAZ0_MAX:
            raise wc.FormatError(f"a Yaz0 file that says it unpacks to {size >> 20} MB (more than {YAZ0_MAX >> 20} "
                                 "MB: damaged, or made to fill the memory)")
        if size > YAZ0_RATIO * len(data) + 4096:
            raise wc.FormatError(f"a Yaz0 file of {len(data)} bytes that says it unpacks to {size} (it can't: "
                                 "damaged)")
    return wc.yaz0_decompress(data)


def _yaz0_head(data: bytes, n: int = 4) -> bytes:
    """The first n bytes a Yaz0 stream decompresses to, without decompressing the rest."""
    out = bytearray()
    i = 16
    try:
        while len(out) < n:
            code = data[i]
            i += 1
            for bit in (0x80, 0x40, 0x20, 0x10, 8, 4, 2, 1):
                if len(out) >= n:
                    break
                if code & bit:
                    out.append(data[i])
                    i += 1
                else:
                    b1, b2 = data[i], data[i + 1]
                    i += 2
                    dist = ((b1 & 0x0F) << 8 | b2) + 1
                    cnt = (b1 >> 4) + 2 if b1 >> 4 else data[i] + 0x12
                    if not b1 >> 4:
                        i += 1
                    for _ in range(cnt):
                        if len(out) < dist:
                            return bytes(out)
                        out.append(out[-dist])
    except IndexError:
        pass
    return bytes(out[:n])


def _hash(name: str, key: int) -> int:
    v = 0
    for c in name.encode():
        v = (v * key + (c - 256 if c >= 128 else c)) & 0xFFFFFFFF
    return v


class Blob:
    """A file's bytes, opened as a SARC pack on demand (Yaz0 undone and redone). Files inside an opened pack are
    Blobs too once opened, so changes deep inside rebuild every pack around them."""

    def __init__(self, data: bytes):
        self.orig = data
        self.yaz0 = data[:4] == b"Yaz0" and len(data) >= 16
        self._raw: bytes | None = None if self.yaz0 else data
        self.align = struct.unpack_from(">I", data, 8)[0] if self.yaz0 else 0
        self.files: dict[str, list] | None = None   # name -> [bytes or Blob, alignment]
        self.key = 0x65
        self.changed = False

    @property
    def raw(self) -> bytes:
        if self._raw is None:
            self._raw = unyaz0(self.orig)[0]
        return self._raw

    @property
    def magic(self) -> bytes:
        return _yaz0_head(self.orig) if self.yaz0 and self._raw is None else self.raw[:4]

    def is_sarc(self) -> bool:
        return self.magic == b"SARC"

    def open(self) -> dict[str, list]:
        if self.files is None:
            raw = self.raw
            s = wc.sarc_parse(raw)
            if s.order != wc.LE:
                raise wc.FormatError("a Wii U (big-endian) pack")
            self.key = s.hash_key
            files: dict[str, list] = {}
            for node in s.nodes:
                if not node.attr >> 24:
                    raise wc.FormatError("a pack with unnamed files")
                files[node.name] = [raw[s.data_offset + node.start:s.data_offset + node.end],
                                    wc._alignment_of(s.data_offset + node.start)]
            self.files = files
        return self.files

    def child(self, name: str) -> Blob | None:
        files = self.open()
        entry = files.get(name)
        if entry is None:
            return None
        if not isinstance(entry[0], Blob):
            entry[0] = Blob(entry[0])
        return entry[0]

    def set(self, name: str, data: bytes, align: int = 8) -> None:
        files = self.open()
        old = files.get(name)
        files[name] = [data, max(old[1], align) if old else align]
        self.changed = True

    def build_raw(self) -> bytes:
        """The pack's bytes (undecompressed) with every change inside it."""
        if self.files is None:
            return self.raw
        items = []
        for name, (content, align) in self.files.items():
            data = content.data() if isinstance(content, Blob) else content
            items.append((name, data, align))
        return sarc_build(items, self.key)

    def data(self) -> bytes:
        """The file's bytes as stored (Yaz0 again when it was)."""
        if not self.dirty():
            return self.orig
        raw = self.build_raw()
        # (Yaz0 really compressed only when small, else stored as is: pure-Python compression of a big pack takes many
        # seconds, and the size table counts unpacked bytes anyway. ukmm imports this module: imported here.)
        if self.yaz0:
            import ukmm
            out = ukmm.yaz0(raw, self.align)
        else:
            out = raw
        self.orig, self._raw, self.files, self.changed = out, raw, None, False
        return out

    def dirty(self) -> bool:
        if self.changed:
            return True
        if self.files:
            return any(isinstance(c, Blob) and c.dirty() for c, _ in self.files.values())
        return False


def sarc_build(items: list[tuple[str, bytes, int]], key: int = 0x65) -> bytes:
    """A little-endian (Switch) SARC from (name, data, alignment): files sorted by name hash, each at an offset that
    is a multiple of its alignment (counted from the start of the pack, as the game loads packs aligned)."""
    items = sorted(items, key=lambda t: (_hash(t[0], key), t[0]))
    names = bytearray()
    offs = []
    for name, _, _ in items:
        offs.append(len(names))
        names += name.encode() + b"\0"
        names += bytes(-len(names) % 4)
    header_end = 0x14 + 0xC + 16 * len(items) + 8 + len(names)
    top = max([a for _, _, a in items] + [4])
    doff = wc._align_up(header_end, top)
    body = bytearray()
    spans = []
    for _, data, align in items:
        pos = wc._align_up(doff + len(body), max(align, 1))
        body += bytes(pos - doff - len(body))
        spans.append((len(body), len(body) + len(data)))
        body += data
    out = bytearray(b"SARC" + struct.pack("<H", 0x14) + b"\xff\xfe")
    out += struct.pack("<IIHH", doff + len(body), doff, 0x100, 0)
    out += b"SFAT" + struct.pack("<HHI", 0xC, len(items), key)
    for (name, _, _), off, (s, e) in zip(items, offs, spans):
        out += struct.pack("<IIII", _hash(name, key), 0x01000000 | off // 4, s, e)
    out += b"SFNT" + struct.pack("<HH", 8, 0) + names
    out += bytes(doff - len(out)) + body
    return bytes(out)


def fill(upper: Blob, lower: Blob, path: str = "", grown: list | None = None) -> int:
    """Put into the pack `upper` every file of the pack `lower` it lacks, and do the same inside the packs both hold
    (BCML's pack merge: upper's files win). Returns how many files were added. `grown` gets (path, pack Blob, lower's
    size) for each pack that got files, to size it in the RSTB."""
    ufiles, lfiles = upper.open(), lower.open()
    added = 0
    for name, (content, align) in lfiles.items():
        if name not in ufiles:
            ufiles[name] = [content.data() if isinstance(content, Blob) else content, align]
            added += 1
            continue
        if not is_packish(name):
            continue
        ub, lb = upper.child(name), lower.child(name)
        if ub.is_sarc() and lb.is_sarc():
            sub = f"{path}//{name}" if path else name
            n = fill(ub, lb, sub, grown)
            if n and grown is not None:
                grown.append((sub, ub, len(lb.raw)))
            added += n
    if added:
        upper.changed = True
    return added


# ---------------------------------------------------------------------------------------------------- AAMP

# Parameter types and their data: fixed sizes, strings (in the string section) and buffers (u32 count before them)
AAMP_FIXED = {0: 4, 1: 4, 2: 4, 3: 8, 4: 12, 5: 16, 6: 16, 9: 0x80, 10: 0x100, 11: 0x180, 12: 0x200, 16: 16, 17: 4}
AAMP_STRING = {7, 8, 15, 20}
AAMP_BUFFER = {13: 4, 14: 4, 18: 4, 19: 1}
FILE_TABLE = zlib.crc32(b"FileTable")
PARAM_ROOT = zlib.crc32(b"param_root")


@dataclass
class PObj:
    params: dict[int, tuple[int, bytes]] = field(default_factory=dict)   # name CRC -> (type, value bytes)


@dataclass
class PList:
    lists: dict[int, PList] = field(default_factory=dict)
    objs: dict[int, PObj] = field(default_factory=dict)


@dataclass
class Pio:
    version: int = 0
    flags: int = 3
    type: bytes = b"xml\0"
    root_crc: int = PARAM_ROOT
    root: PList = field(default_factory=PList)


def aamp_read(data: bytes) -> Pio:
    """A Switch (little-endian, version 2) parameter archive."""
    if len(data) < 0x30 or data[:4] != b"AAMP":
        raise wc.FormatError("not an AAMP file")
    version, flags = struct.unpack_from("<II", data, 4)
    if version != 2 or not flags & 1:
        raise wc.FormatError("an AAMP file that isn't the Switch's (version 2, little-endian)")
    pver, pio_off = struct.unpack_from("<II", data, 0x10)
    root_at = 0x30 + pio_off
    # The work allowed: a list, object or parameter has its own header of 8 bytes or more, and a value its own bytes,
    # in any file Nintendo's tools or BCML write. Offsets are relative, so a made-up file can point many headers at
    # the same ones (objects x parameters from a few KB): refused once it reads more than its size allows.
    left = [len(data) // 8 + 64, 2 * len(data) + 0x10000]
    values: dict[tuple[int, int], bytes] = {}

    def need(off: int, size: int) -> None:
        if off < 0 or off + size > len(data):
            raise wc.FormatError("AAMP data runs past the end of the file")

    def spend(headers: int, nbytes: int = 0) -> None:
        left[0] -= headers
        left[1] -= nbytes
        if left[0] < 0 or left[1] < 0:
            raise wc.FormatError("AAMP file reads as more than it holds (shared or repeated parts)")

    def value(t: int, d: int) -> bytes:
        v = values.get((t, d))
        if v is not None:
            return v
        if t in AAMP_FIXED:
            need(d, AAMP_FIXED[t])
            v = data[d:d + AAMP_FIXED[t]]
        elif t in AAMP_STRING:
            end = data.find(b"\0", d, min(len(data), d + 0x10000))
            if d < 0 or end < 0:
                raise wc.FormatError("AAMP string without an end")
            v = data[d:end]
        elif t in AAMP_BUFFER:
            need(d - 4, 4)
            n = struct.unpack_from("<I", data, d - 4)[0] * AAMP_BUFFER[t]
            need(d, n)
            v = data[d:d + n]
        else:
            raise wc.FormatError(f"AAMP parameter type {t} isn't known")
        spend(0, len(v) + 4)
        values[(t, d)] = v
        return v

    def read_obj(off: int) -> tuple[int, PObj]:
        need(off, 8)
        crc, po, pn = struct.unpack_from("<IHH", data, off)
        spend(1 + pn)
        obj = PObj()
        for k in range(pn):
            at = off + po * 4 + 8 * k
            need(at, 8)
            pcrc, x = struct.unpack_from("<II", data, at)
            t, d = x >> 24, at + (x & 0xFFFFFF) * 4
            obj.params[pcrc] = (t, value(t, d))
        return crc, obj

    def read_list(off: int, depth: int) -> tuple[int, PList]:
        if depth > 64:
            raise wc.FormatError("AAMP lists nest too deep")
        spend(1)
        need(off, 12)
        crc, lo, ln, oo, on = struct.unpack_from("<IHHHH", data, off)
        pl = PList()
        for k in range(ln):
            c, sub = read_list(off + lo * 4 + 12 * k, depth + 1)
            pl.lists[c] = sub
        for k in range(on):
            c, obj = read_obj(off + oo * 4 + 8 * k)
            pl.objs[c] = obj
        return crc, pl

    root_crc, root = read_list(root_at, 0)
    return Pio(pver, flags, bytes(data[0x30:root_at]), root_crc, root)


def aamp_write(pio: Pio) -> bytes:
    lists = [pio.root]
    i = 0
    while i < len(lists):
        lists += list(lists[i].lists.values())
        i += 1
    crc_of = {id(pio.root): pio.root_crc}
    for pl in lists:
        for c, sub in pl.lists.items():
            crc_of[id(sub)] = c
    objs = [(c, o) for pl in lists for c, o in pl.objs.items()]
    params = [(c, t, v) for _, o in objs for c, (t, v) in o.params.items()]
    type_b = pio.type + bytes(-len(pio.type) % 4)
    base = 0x30 + len(type_b)
    obj_base = base + 12 * len(lists)
    param_base = obj_base + 8 * len(objs)
    data_base = param_base + 8 * len(params)
    data = bytearray()
    where: list[int] = []
    shared: dict[tuple, int] = {}   # (values written once and shared, as Nintendo's files do)
    for _, t, v in params:
        if t in AAMP_STRING:
            where.append(-1)
            continue
        head = struct.pack("<I", len(v) // AAMP_BUFFER[t]) if t in AAMP_BUFFER else b""
        key = (t in AAMP_BUFFER, head + v)
        if key not in shared:
            data += head
            shared[key] = data_base + len(data)
            data += v + bytes(-len(v) % 4)
        where.append(shared[key])
    str_base = data_base + len(data)
    strings = bytearray()
    shared_s: dict[bytes, int] = {}
    for k, (_, t, v) in enumerate(params):
        if t in AAMP_STRING:
            if v not in shared_s:
                shared_s[v] = str_base + len(strings)
                strings += v + b"\0"
                strings += bytes(-len(strings) % 4)
            where[k] = shared_s[v]
    out = bytearray(str_base + len(strings))

    def rel(target: int, at: int, limit: int) -> int:
        r = (target - at) // 4
        if r < 0 or r >= limit:
            raise wc.FormatError("AAMP file too big to write")
        return r

    list_at = {id(pl): base + 12 * k for k, pl in enumerate(lists)}
    first_obj: dict[int, int] = {}
    n = 0
    for pl in lists:
        first_obj[id(pl)] = obj_base + 8 * n
        n += len(pl.objs)
    obj_first_param: list[int] = []
    n = 0
    for _, o in objs:
        obj_first_param.append(param_base + 8 * n)
        n += len(o.params)
    for pl in lists:
        at = list_at[id(pl)]
        kids = list(pl.lists.values())
        lo = rel(list_at[id(kids[0])], at, 0x10000) if kids else 0
        oo = rel(first_obj[id(pl)], at, 0x10000) if pl.objs else 0
        struct.pack_into("<IHHHH", out, at, crc_of[id(pl)], lo, len(kids), oo, len(pl.objs))
    for k, (c, o) in enumerate(objs):
        at = obj_base + 8 * k
        po = rel(obj_first_param[k], at, 0x10000) if o.params else 0
        struct.pack_into("<IHH", out, at, c, po, len(o.params))
    for k, (c, t, _) in enumerate(params):
        at = param_base + 8 * k
        struct.pack_into("<II", out, at, c, rel(where[k], at, 1 << 24) | t << 24)
    out[data_base:str_base] = data
    out[str_base:] = strings
    out[:0x30] = b"AAMP" + struct.pack("<11I", 2, pio.flags | 1, len(out), pio.version, len(type_b), len(lists),
                                       len(objs), len(params), len(data), len(strings), 0)
    out[0x30:base] = type_b
    return bytes(out)


def aamp_merge(base: PList, diff: PList) -> None:
    """BCML's deep merge: the diff's lists and objects are added or merged in, its parameters replace the base's."""
    for c, sub in diff.lists.items():
        if c in base.lists:
            aamp_merge(base.lists[c], sub)
        else:
            base.lists[c] = sub
    for c, obj in diff.objs.items():
        if c in base.objs:
            base.objs[c].params.update(obj.params)
        else:
            base.objs[c] = obj


# Animation lists (.baslist), as BCML's aslist.aamp records changes to them: "AddReses" (AddRes_N: the animation
# archives the actor loads, "Anim"), "ASDefines" (ASDefine_N: an animation sequence's "Name" and its .bas
# "Filename"), "CFDefines" (CFDefine_N lists: "CFPre" names the sequence, "CFExcepts" the ones it doesn't blend from,
# "CFPosts" (CFPost_N) the blends into others by their "Name"). Entries are matched by those names, not their keys.
ADD_RESES, AS_DEFINES, CF_DEFINES = (zlib.crc32(n) for n in (b"AddReses", b"ASDefines", b"CFDefines"))
P_NAME, P_FILENAME, P_ANIM = (zlib.crc32(n) for n in (b"Name", b"Filename", b"Anim"))
CF_PRE, CF_EXCEPTS, CF_POSTS = (zlib.crc32(n) for n in (b"CFPre", b"CFExcepts", b"CFPosts"))


def _text(obj: PObj | None, param: int) -> bytes | None:
    p = obj.params.get(param) if obj is not None else None
    return p[1] if p is not None and p[0] in AAMP_STRING else None


def _free_key(taken, prefix: str) -> int:
    """The first "<prefix><n>" name (its CRC-32) not taken, n counting from how many there are (BCML numbers new
    entries after the game's)."""
    n = len(taken)
    while zlib.crc32(f"{prefix}{n}".encode()) in taken:
        n += 1
    return zlib.crc32(f"{prefix}{n}".encode())


def _cf_name(pl: PList) -> bytes | None:
    return _text(pl.objs.get(CF_PRE), P_NAME)


def _merge_cfdefine(base: PList, diff: PList, bad: list[str]) -> int:
    """One CFDefine: the diff's excepts added (by value), its posts merged by their Name (new ones added, a known
    one's values replaced; one without a Name named in `bad`), anything else merged as usual. Returns how many
    entries were added or changed."""
    n = 0
    for c, obj in diff.objs.items():
        if c == CF_EXCEPTS and c in base.objs:
            have = base.objs[c].params
            values = {v for t, v in have.values() if t in AAMP_STRING}
            for t, v in obj.params.values():
                if t in AAMP_STRING and v not in values:
                    have[_free_key(have, "Name_")] = (t, v)
                    values.add(v)
                    n += 1
        elif c in base.objs:
            changed = {k: v for k, v in obj.params.items() if base.objs[c].params.get(k) != v}
            base.objs[c].params.update(changed)
            n += bool(changed)
        else:
            base.objs[c] = obj
            n += 1
    for c, sub in diff.lists.items():
        if c == CF_POSTS and c in base.lists:
            posts = base.lists[c].objs
            by_name = {_text(o, P_NAME): o for o in posts.values() if _text(o, P_NAME) is not None}
            for post in sub.objs.values():
                name = _text(post, P_NAME)
                hit = by_name.get(name)
                if name is None:
                    bad.append("a blend post (CFPost) without its Name")
                elif hit is None:
                    posts[_free_key(posts, "CFPost_")] = post
                    by_name[name] = post
                    n += 1
                else:
                    changed = {pk: v for pk, v in post.params.items() if hit.params.get(pk) != v}
                    hit.params.update(changed)
                    n += bool(changed)
        elif c in base.lists:
            n += aslist_merge(base.lists[c], sub, bad)
        else:
            base.lists[c] = sub
            n += 1
    return n


def aslist_merge(base: PList, diff: PList, bad: list[str] | None = None) -> int:
    """BCML's merge of an animation list (aslist.aamp) into the game's: animation archives (AddReses) added when new,
    sequences (ASDefines) matched by Name (a new one added after the game's, a known one gets the diff's Filename),
    blend rules (CFDefines) matched by their CFPre's Name and merged; nothing is removed, the game's entries keep their
    place. Other lists and objects merge as in deepmerge. An entry without its name (Anim, Name, CFPre) is left out
    and described in `bad`: BCML always writes it, and the log's keys are numbered among the logged entries only, so
    matching by key would change an unrelated entry. Returns how many entries were added or changed."""
    bad = [] if bad is None else bad
    n = 0
    for c, obj in diff.objs.items():
        if c in base.objs:
            changed = {k: v for k, v in obj.params.items() if base.objs[c].params.get(k) != v}
            base.objs[c].params.update(changed)
            n += bool(changed)
        else:
            base.objs[c] = obj
            n += 1
    for c, sub in diff.lists.items():
        mine = base.lists.get(c)
        if mine is None:
            base.lists[c] = sub
            n += len(sub.objs) + len(sub.lists) if c in (ADD_RESES, AS_DEFINES, CF_DEFINES) else 1
        elif c == ADD_RESES:
            have = {_text(o, P_ANIM) for o in mine.objs.values()}
            for obj in sub.objs.values():
                anim = _text(obj, P_ANIM)
                if anim is None:
                    bad.append("an animation archive (AddRes) without its Anim")
                elif anim not in have:
                    mine.objs[_free_key(mine.objs, "AddRes_")] = obj
                    have.add(anim)
                    n += 1
        elif c == AS_DEFINES:
            by_name = {_text(o, P_NAME): o for o in mine.objs.values() if _text(o, P_NAME) is not None}
            for obj in sub.objs.values():
                name = _text(obj, P_NAME)
                hit = by_name.get(name)
                if name is None:
                    bad.append("a sequence (ASDefine) without its Name")
                elif hit is None:
                    mine.objs[_free_key(mine.objs, "ASDefine_")] = obj
                    by_name[name] = obj
                    n += 1
                else:
                    changed = {pk: v for pk, v in obj.params.items() if hit.params.get(pk) != v}
                    hit.params.update(changed)
                    n += bool(changed)
        elif c == CF_DEFINES:
            by_name = {_cf_name(pl): pl for pl in mine.lists.values() if _cf_name(pl) is not None}
            for pl in sub.lists.values():
                name = _cf_name(pl)
                hit = by_name.get(name)
                if name is None:
                    bad.append("a blend rule (CFDefine) without its CFPre Name")
                elif hit is None:
                    mine.lists[_free_key(mine.lists, "CFDefine_")] = pl
                    by_name[name] = pl
                    n += 1
                else:
                    n += _merge_cfdefine(hit, pl, bad)
            n += aslist_merge(mine, PList(objs=sub.objs), bad)
        else:
            n += aslist_merge(mine, sub, bad)
    return n


# What the game allocates for each entry of a loaded animation list, on top of the file's bytes (the decomp's
# resResourceASList.h): an animation archive (AddRes), a sequence (ASDefine), a blend rule (CFDefine), a blend post
# (CFPost), an except (CFExcept). The size table entry has to hold them too, so it grows by more than the file.
ASLIST_STRUCTS = (0xA0, 0x88, 0x138, 0x98, 0x28)


def aslist_counts(root: PList) -> tuple[int, int, int, int, int]:
    """How many animation archives, sequences, blend rules, blend posts and excepts an animation list holds."""
    def sub(c: int) -> PList:
        return root.lists.get(c) or PList()
    rules = list(sub(CF_DEFINES).lists.values())
    return (len(sub(ADD_RESES).objs), len(sub(AS_DEFINES).objs), len(rules),
            sum(len(r.lists[CF_POSTS].objs) for r in rules if CF_POSTS in r.lists),
            sum(len(r.objs[CF_EXCEPTS].params) for r in rules if CF_EXCEPTS in r.objs))


def aslist_growth(game: PList, merged: PList) -> int:
    """The bytes the game allocates for the entries a merged animation list has beyond the game's list."""
    return sum(max(0, m - g) * size for g, m, size in zip(aslist_counts(game), aslist_counts(merged), ASLIST_STRUCTS))


def file_table(pio: Pio) -> list[str]:
    """The files a BCML log (deepmerge.aamp, aslist.aamp) holds changes for: its FileTable's strings."""
    obj = pio.root.objs.get(FILE_TABLE)
    if not obj:
        return []
    return [v.decode("utf-8", "replace") for t, v in obj.params.values() if t in AAMP_STRING]


# ---------------------------------------------------------------------------------------------------- installing

@dataclass
class Layer:
    """The base package or one option: its folder and logs."""
    label: str              # "" for the base, else "options/<folder>"
    root: str
    logs: dict[str, str] = field(default_factory=dict)   # log name (lowercase, "/"-separated) -> file


@dataclass
class Result:
    info: Info
    chosen: list[Option]
    files: int = 0
    packs_filled: int = 0
    files_added: int = 0
    deepmerged: int = 0
    aslisted: int = 0        # animation lists merged (aslist.aamp)
    rstb_entries: int = 0
    rstb_changed: int = 0
    rstb_ok: bool = False    # the entries were compared with the game's table (False: it is missing)
    notes: list[str] = field(default_factory=list)
    not_merged: list[str] = field(default_factory=list)
    packs: list[str] = field(default_factory=list)
    logs_merged: dict[str, list[str]] = field(default_factory=dict)   # log -> the game files it changed (targets)
    # log -> every game file it names, changed here or not (BCML ships shops and drop tables changed and keeps their
    # log: merging it into the mod's copy changes nothing, but deploy has to merge that file between mods again)
    log_targets: dict[str, list[str]] = field(default_factory=dict)
    generated: list[str] = field(default_factory=list)   # whole files the logs made (ActorInfo, map units...)

    def summary(self) -> list[str]:
        """Lines for modman add: what was installed, then what wasn't merged."""
        i = self.info
        opts = ", ".join(o.name for o in self.chosen) or "none"
        n = f"{self.rstb_entries} entr{'y' if self.rstb_entries == 1 else 'ies'}"
        logs = [f"{k} ({len(v)} file{'' if len(v) == 1 else 's'})" if v else f"{k} (no change)"
                for k, v in self.logs_merged.items()]
        out = [f"BCML package {i.name or '?'}" + (f" {i.version}" if i.version else "") + f": options {opts}"
               + (f" (of {len(i.options)})" if i.options else ""),
               f"{self.files} file(s); {self.packs_filled} pack(s) filled with {self.files_added} file(s) from your "
               f"game; {self.deepmerged} parameter file(s) merged (deepmerge.aamp)"
               + (f", {self.aslisted} animation list(s) (aslist.aamp)" if self.aslisted else ""),
               *([f"logs merged: {', '.join(logs)}"] if logs else []),
               f"size table: {n}, {self.rstb_changed} changed from the game's" if self.rstb_changed else
               f"size table: {n}, none of them bigger than the game's, so the game's is used" if self.rstb_ok else
               f"size table: {n}, not made (see below)" if self.rstb_entries else
               "size table: no changes (the game's is used)"]
        out += self.notes
        out += [f"needs attention: {x}" for x in self.not_merged]
        return out


def _aoc_dirs(game: str) -> list[str]:
    try:
        names = os.listdir(game)
    except OSError:
        return []
    return sorted(os.path.join(game, d) for d in names
                  if re.fullmatch(r"aoc\d+", d, re.I) and os.path.isdir(os.path.join(game, d)))


def _game_file(game: str, rel: str) -> str | None:
    """The game's copy of a mod file (a Switch-layout path: 01007EF00011E000/romfs/... or the DLC's), or None."""
    parts = rel.split("/")
    if len(parts) < 3 or parts[1].lower() != "romfs":
        return None
    tid = parts[0].upper()
    roots = [os.path.join(game, "romfs")] if tid == BASE_TID else _aoc_dirs(game) if tid == DLC_TID else []
    for r in roots:
        p = os.path.join(r, *parts[2:])
        if os.path.isfile(p):
            return p
    return None


def _write_new(path: str, data: bytes) -> None:
    """Write a file as a new one (never into an existing file, which may be a hard link to someone's own file)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".bnp-tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _put(src: str, dst: str, move: bool) -> None:
    """Put a package file in the mod: moved (out of a folder made for this), else a hard link (a copy for a
    read-only file, or across drives). Files here are never written in place later (_write_new), so a link is safe."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.lexists(dst):
        os.unlink(dst)
    if move:
        os.replace(src, dst)
        return
    try:
        if os.stat(src).st_mode & 0o200:
            os.link(src, dst)
            return
    except OSError:
        pass
    shutil.copyfile(src, dst)


def _layers(root: str, chosen: list[Option], info: Info) -> list[Layer]:
    layers = [Layer("", root)]
    for o in chosen:
        p = os.path.join(root, "options", o.folder)
        if not os.path.isdir(p):
            raise BnpError(f"Not added: {info.name}'s option '{o.name}' has no folder options/{o.folder} in the "
                           "package.")
        layers.append(Layer(f"options/{o.folder}", p))
    for layer in layers:
        logs = os.path.join(layer.root, "logs")
        if os.path.isdir(logs):
            layer.logs = {rel.lower(): path for rel, path in _walk(logs).items()}
    return layers


def _layer_files(layer: Layer) -> dict[str, str]:
    """A layer's own files (relative path -> file), leaving out its logs, the options and info.json."""
    out = {}
    for rel, path in _walk(layer.root).items():
        low = rel.lower()
        if low.startswith("logs/") or (not layer.label and (low.startswith("options/") or low == "info.json")):
            continue
        out[rel] = path
    return out


def _target(rel: str) -> str:
    """A Switch-layout mod path as the overlay names it (DLC files under aoc/0010/)."""
    parts = rel.split("/")
    if len(parts) > 2 and parts[1].lower() == "romfs":
        inner = "/".join(parts[2:])
        return (rm.DLC_PREFIX + inner) if parts[0].upper() == DLC_TID else inner
    return rel


def _some(items: list[str], n: int = 3) -> str:
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def _target_top(target: str) -> str:
    """The file in the RomFS a log merger's target is in ("Pack/Bootup.pack" for "Pack/Bootup.pack//GameData/...")."""
    return target.split("//", 1)[0]


def _unmerged_log(where: str, name: str, data: bytes) -> str:
    old = OLD_LOGS.get(name) or ("texts" if name.startswith("texts_") else None)
    what = f"{old}, a BCML 2 log, which BCML 3 refuses too" if old else LOG_NAMES.get(name)
    files = ""
    if name.endswith(".aamp"):
        try:
            listed = file_table(aamp_read(data))
            if listed:
                files = f" for {len(listed)} file(s) ({_some([p.split('//')[-1] for p in listed], 2)})"
        except (wc.FormatError, struct.error):
            pass
    label = f"{where}{name}" + (f" ({what})" if what else " (a BCML log this loader doesn't know)")
    return (f"{label}{files} not merged: the game's own are used, so the parts of the mod that need them may not "
            "work (merging them needs BCML or UKMM)")


def log_mergers() -> dict:
    """Log name -> the module that merges it (bcml_data, bcml_texts or bcml_maps: read_log, combine, apply). Imported
    here, when needed: they import this module."""
    import bcml_data
    import bcml_maps
    import bcml_texts
    return {name: mod for mod in (bcml_data, bcml_maps, bcml_texts) for name in mod.LOGS}


def fixed_targets(mergers: dict | None = None) -> dict[str, str]:
    """Log name -> the one game file (target) it changes, for the kinds that change one fixed file (game data, event
    info, the statics, quests...; not texts, drops, shops or map units)."""
    mergers = log_mergers() if mergers is None else mergers
    out = {}
    for name, m in mergers.items():
        kinds = getattr(m, "KINDS", None)
        if isinstance(kinds, dict) and name in kinds:
            out[name] = kinds[name][0]
    m = mergers.get("quests.yml")
    if m is not None:
        out.update({"mainstatic.yml": m.MAINSTATIC, "dstatic.yml": m.DSTATIC, "quests.yml": m.QUESTS})
    return out


def _log_error(where: str, name: str, e: Exception) -> str:
    """A log a merger can't read, for "needs attention" (the merger's message starts with the log's name)."""
    msg = str(e).strip()
    if msg.lower().startswith(name):
        msg = msg[len(name):].lstrip(" :")
    msg = msg.rstrip(".")
    return f"{where}{name} ({LOG_NAMES.get(name, 'a BCML log')}) {msg}; not merged, the game's are used"


class Targets:
    """The mod's files by RomFS target ("Pack/Bootup.pack//GameData/gamedata.ssarc", "//" inside packs), for the log
    mergers. read() gives the mod's copy of one (its own file, or a file of a pack the install filled), or None; put()
    sets one: in its pack (the mod's, else copied from the game, kept in `blobs` to be written), every pack on the way
    sized as deepmerge's are, or as a whole file (`loose`, written at the end)."""

    def __init__(self, stage: str, game: str, blobs: dict, game_blobs: dict, sizes: Sizes):
        self.stage, self.game, self.blobs, self.game_blobs, self.sizes = stage, game, blobs, game_blobs, sizes
        self.files_now = {r.lower(): r for r in _walk(stage)} if os.path.isdir(stage) else {}
        self.opened: dict[str, Blob] = {}      # rel -> a pack of the mod read but not changed (yet)
        self.loose: dict[str, bytes] = {}      # rel -> a whole file's new bytes

    def rel(self, top: str) -> str:
        rel = f"{BASE_TID}/romfs/{'/'.join(check_name(top, 'a log'))}"   # (a name from a log: checked like any)
        return self.files_now.get(rel.lower(), rel)

    def _game_top(self, rel: str) -> Blob | None:
        g = self.game_blobs.get(rel)
        if g is None:
            gpath = _game_file(self.game, rel)
            if gpath:
                g = self.game_blobs[rel] = Blob(_read(gpath))
        return g

    def _top(self, rel: str, from_game: bool) -> Blob | None:
        blob = self.blobs.get(rel) or self.opened.get(rel)
        if blob is None:
            own = os.path.join(self.stage, *rel.split("/"))
            if os.path.isfile(own):
                blob = Blob(_read(own))
            elif from_game and self._game_top(rel) is not None:
                blob = Blob(self._game_top(rel).orig)   # (a pack the mod changes only through its logs)
            else:
                return None
            self.opened[rel] = blob
        return blob

    def read(self, target: str) -> bytes | None:
        parts = target.split("//")
        try:
            rel = self.rel(parts[0])
        except BnpError:
            return None    # (put names it)
        if len(parts) == 1:
            if rel in self.loose:
                return self.loose[rel]
            own = os.path.join(self.stage, *rel.split("/"))
            return _read(own) if os.path.isfile(own) else None
        top = self._top(rel, False)
        return get_inside(top, parts[1:]) if top is not None else None

    def put(self, target: str, data: bytes) -> str | None:
        """Set a target's bytes in the mod. Returns why it can't be, or None."""
        parts = target.split("//")
        try:
            rel = self.rel(parts[0])
        except BnpError:
            return "its path isn't a safe game file path"
        if len(parts) == 1:
            self.loose[rel] = data
            return None
        top = self._top(rel, True)
        if top is None:
            return "neither the mod nor the game has its pack"
        tparts = rel.split("/")
        why = put_inside(top, self._game_top(rel), canon("/".join(tparts[2:]), top.orig, tparts[0].upper() == DLC_TID),
                         parts[1:], data, self.sizes)
        if why is None:
            self.blobs[rel] = top
            self.opened.pop(rel, None)
        return why


def put_inside(top: Blob, gtop: Blob | None, top_name: str, inner: list[str], data: bytes, sizes: Sizes) -> str | None:
    """Set the file at `inner` (its path through the packs inside `top`, a pack) to data; every pack on the way gets a
    size (the game's copy of it, gtop and inside: its entry plus what it grew). Returns why it can't be, or None."""
    chain = [(top_name, top, gtop)]
    blob, gblob = top, gtop
    for name in inner[:-1]:
        if not blob.is_sarc():
            return f"{chain[-1][0]} isn't a pack"
        blob = blob.child(name)
        if blob is None:
            return f"no {name} in its pack"
        gblob = gblob.child(name) if gblob is not None and gblob.is_sarc() else None
        chain.append((canon(name, blob.orig), blob, gblob))
    if not blob.is_sarc():
        return f"{chain[-1][0]} isn't a pack"
    blob.set(inner[-1], data)
    for size_name, pack, gpack in chain:
        if gpack is not None and gpack.is_sarc():
            sizes.add(size_name, len(gpack.raw), pack, True)
    return None


def get_inside(top: Blob, inner: list[str]) -> bytes | None:
    """The file at `inner` (its path through the packs inside `top`) as it is now, or None. A file rebuilt here is
    settled (data()), its packs kept marked as changed."""
    blob, chain = top, []
    for name in inner:
        if blob is None or not blob.is_sarc():
            return None
        chain.append(blob)
        blob = blob.child(name)
    if blob is None:
        return None
    if blob.dirty():
        data = blob.data()
        for pack in chain:
            pack.changed = True   # (data() settled the change in the file itself: its packs still hold it)
        return data
    return blob.orig


class Sizes:
    """Size table entries for the files the install rebuilds, worked out once they are final (canonical name ->
    the game's entry for the vanilla file, its vanilla size, and the new file or its new size)."""

    def __init__(self, game: Rstb | None):
        self.game = game
        self.items: dict[str, tuple[int, int, object, bool, int | None]] = {}

    def add(self, name: str, old_len: int, new, pack: bool, structs: int | None = None) -> None:
        g = self.game.get(name) if self.game else None
        if g is not None:
            self.items[name] = (g, old_len, new, pack, structs)

    def final(self) -> dict[str, int]:
        out = {}
        for name, (g, old_len, new, pack, structs) in self.items.items():
            new_len = new if isinstance(new, int) else len(new.raw)
            out[name] = needed_size(g, old_len, new_len, pack, structs)
        return out


def install(src_root: str, stage: str, game: str, *, info: Info, picks: list[str] = (), no_defaults: bool = False,
            move: bool = False, say=print) -> Result:
    """Install the extracted package in src_root (its info.json at the top) into stage: the base files, then the
    chosen options over them, the packs filled from the game (`game`: Savage Wind's extracted folder, only read),
    deepmerge.aamp and aslist.aamp applied, and a size table for the mod. move: take the files out of src_root (a
    folder made for this) instead of linking or copying them."""
    if "wii" in info.platform or any(os.path.isdir(os.path.join(src_root, d)) for d in ("content", "aoc")):
        raise BnpError(f"Not added: {info.name or 'this package'} is a Wii U BCML package, and modman installs "
                       "Switch ones. Install it in UKMM (Switch mode), deploy there, then add UKMM's output folder.")
    if not os.path.isdir(os.path.join(game, "romfs")):
        raise BnpError("Savage Wind's game files aren't set up (no extracted\\romfs), and a BCML package is filled "
                       "from them. Set them up in Savage Wind first.")
    chosen = choose(info, picks, no_defaults)
    res = Result(info, chosen)
    layers = _layers(src_root, chosen, info)
    os.makedirs(stage, exist_ok=True)

    # 1. the files: the base, then each option over it (a pack both have is merged, the option's files winning)
    placed: dict[str, str] = {}          # lowercase rel -> rel as spelled in stage
    merged_packs = 0
    for layer in layers:
        for rel, path in sorted(_layer_files(layer).items()):
            key = rel.lower()
            dst = os.path.join(stage, *placed.get(key, rel).split("/"))
            if key in placed and is_packish(rel):
                try:
                    lower, upper = Blob(_read(dst)), Blob(_read(path))
                    if lower.is_sarc() and upper.is_sarc():
                        fill(upper, lower)
                        _write_new(dst, upper.data())
                        merged_packs += 1
                        continue
                except (wc.FormatError, struct.error, IndexError) as e:
                    res.not_merged.append(f"{layer.label}/{rel}: can't be merged with the base package's copy ({e}); "
                                          "the option's copy is used")
            _put(path, dst, move)
            placed.setdefault(key, rel)
    res.files = len(placed)
    if merged_packs:
        res.notes.append(f"{merged_packs} pack(s) of the options merged with the base package's")

    # 2. the logs: sizes, packs and parameter changes are merged below, the data logs by their mergers (log_mergers);
    # what nothing merges is named
    rstb_tables: list[dict] = []
    diffs: list[tuple[str, Pio]] = []
    aslists: list[tuple[str, Pio]] = []
    mergers = log_mergers()
    data_logs: dict[str, list] = {}      # log name -> its parsed logs: the base's, then each option's
    for layer in layers:
        where = f"{layer.label}/logs/" if layer.label else "logs/"
        for name, path in sorted(layer.logs.items()):
            data = _read(path)
            if name == "rstb.json":
                rstb_tables.append(read_rstb_json(data, where + name))
            elif name == "packs.json":
                pass  # (every pack the mod has is filled from the game's copy, listed or not)
            elif name in ("deepmerge.aamp", "aslist.aamp"):
                try:
                    (diffs if name == "deepmerge.aamp" else aslists).append((where + name, aamp_read(data)))
                except (wc.FormatError, struct.error) as e:
                    res.not_merged.append(f"{where}{name} ({LOG_NAMES.get(name, 'actor parameter changes')}) can't "
                                          f"be read ({e}): not merged, the game's are used")
            elif name in mergers:
                m = mergers[name]
                try:
                    log = m.read_log(name, data, where) if m.__name__ == "bcml_texts" else m.read_log(name, data)
                except (m.LogError, ValueError, TypeError, RecursionError) as e:
                    res.not_merged.append(_log_error(where, name, e))
                    continue
                data_logs.setdefault(name, []).append(log)
            else:
                res.not_merged.append(_unmerged_log(where, name, data))

    # 3. packs: filled from the game's own copy, nested packs too
    game_rstb = load_game_rstb(game)
    sizes = Sizes(game_rstb)
    blobs: dict[str, Blob] = {}          # rel (as in stage) -> Blob, written at the end
    game_blobs: dict[str, Blob] = {}
    candidates = sorted(r for r in _walk(stage) if is_packish(r))
    no_dlc: list[str] = []
    has_dlc = bool(_aoc_dirs(game))
    step = max(1, len(candidates) // 5)
    for k, rel in enumerate(candidates):
        gpath = _game_file(game, rel)
        if gpath is None:
            if rel.upper().startswith(DLC_TID + "/") and not has_dlc:
                no_dlc.append(_target(rel))
            continue
        if len(candidates) > 20 and k % step == 0:
            say(f"  filling packs from the game: {k}/{len(candidates)}")
        try:
            mine = Blob(_read(os.path.join(stage, *rel.split("/"))))
            theirs = game_blobs[rel] = Blob(_read(gpath))
            if not (mine.is_sarc() and theirs.is_sarc()):
                continue
            grown: list = []
            n = fill(mine, theirs, "", grown)
        except (wc.FormatError, struct.error, IndexError) as e:
            res.not_merged.append(f"{_target(rel)}: can't be filled from the game's copy ({e}); it is used as BCML "
                                  "left it, which may lack files the game needs")
            continue
        if n:
            blobs[rel] = mine
            res.packs_filled += 1
            res.files_added += n
            parts = rel.split("/")
            sizes.add(canon("/".join(parts[2:]), mine.orig, parts[0].upper() == DLC_TID), len(theirs.raw), mine, True)
            for sub, blob, old_len in grown:
                sizes.add(canon(sub.rsplit("//", 1)[-1], blob.orig), old_len, blob, True)
    if no_dlc:
        res.not_merged.append(f"{len(no_dlc)} DLC pack(s) can't be filled ({_some(no_dlc, 2)}): your game has no DLC "
                              "set up, so they hold only the mod's own files")

    # 4. deepmerge.aamp: parameter changes applied to the game's files (inside the filled packs)
    for where, pio in diffs:
        done, failed, _ = _apply_deepmerge(pio, stage, game, blobs, game_blobs, sizes)
        res.deepmerged += done
        if failed:
            res.not_merged.append(f"{where} (actor parameter changes): {len(failed)} file(s) not merged "
                                  f"({_some([f'{p}: {why}' for p, why in failed], 2)}); the game's are used")
    # aslist.aamp: animation list changes, merged into the game's lists the same way (after the parameter changes)
    for where, pio in aslists:
        done, failed, nameless = _apply_deepmerge(pio, stage, game, blobs, game_blobs, sizes, aslist=True)
        res.aslisted += done
        if failed:
            res.not_merged.append(f"{where} (animation lists): {len(failed)} file(s) not merged "
                                  f"({_some([f'{p}: {why}' for p, why in failed], 2)}); the game's are used, so "
                                  "the mod's own animations there may not play")
        if nameless:
            res.not_merged.append(f"{where} (animation lists): {len(nameless)} entr"
                                  f"{'y' if len(nameless) == 1 else 'ies'} without a name not merged "
                                  f"({_some(nameless, 2)}): BCML always names them, so the log may be damaged; "
                                  "the rest of it is merged")

    # 4b. the data logs (game data, save data, actor info, texts, drops, shops, maps, quests...): each kind's logs of
    # the base and the options combined, merged into the mod's copy of its file (else the game's), put back in its
    # packs
    targets = Targets(stage, game, blobs, game_blobs, sizes)
    log_sizes = merge_logs({n: [logs] for n, logs in data_logs.items()}, game, targets, res, say)
    # (the mod's packs: its own and the ones only its logs change, which it gets from the game)
    res.packs = sorted({_target(r).lower(): _target(r) for r in [*candidates, *blobs]}.values(), key=str.lower)
    res.generated = sorted(_target(r) for r in targets.loose)

    # 5. write what changed
    if blobs or targets.loose:
        say(f"  writing {len(blobs) + len(targets.loose)} rebuilt file(s)...")
    for rel, blob in sorted(blobs.items()):
        _write_new(os.path.join(stage, *rel.split("/")), blob.data())
    for rel, data in sorted(targets.loose.items()):
        _write_new(os.path.join(stage, *rel.split("/")), data)

    # 6. the size table: the mod's sizes and the rebuilt files', never below the game's (0: no entry)
    entries = merge_entries(rstb_tables)
    for name, need in [*sizes.final().items(), *log_sizes.items()]:
        if entries.get(name) != 0:
            entries[name] = max(need, entries.get(name, 0))
    res.rstb_entries = len(entries)
    shipped = os.path.join(stage, *RSTB_REL.split("/"))
    if os.path.lexists(shipped):
        os.unlink(shipped)
        res.notes.append("the package's own size table is left out: one is made from your game's instead")
    if entries:
        if game_rstb is None:
            res.not_merged.append("the size table (RSTB) can't be made: the game's own isn't in extracted\\romfs, so "
                                  "new or bigger files may fail to load")
        else:
            # (only what changes the game's: a table in the overlay that changes nothing would still turn off
            # Savage Wind's own size fixes, see romfs_mods ENV_NO_HUD)
            entries = game_rstb.changes(entries)
            res.rstb_changed, res.rstb_ok = game_rstb.apply(entries), True
            if res.rstb_changed:
                _write_new(shipped, game_rstb.to_bytes())

    # 7. what the library keeps about the package (never deployed: the folder name starts with '.')
    meta = os.path.join(stage, META)
    for layer in layers:
        for name, path in layer.logs.items():
            dst = os.path.join(meta, *(layer.label.split("/") if layer.label else []), "logs", *name.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(path, dst)
    write_json(os.path.join(meta, "info.json"), info.raw)
    write_json(os.path.join(meta, "rstb.json"), dict(sorted(entries.items())))
    write_json(os.path.join(meta, "bnp.json"), {
        "tool": TOOL_ID, "name": info.name, "version": info.version, "options": [o.name for o in chosen],
        "option_folders": [o.folder for o in chosen], "packs": res.packs, "packs_filled": res.packs_filled,
        "files_added_from_game": res.files_added, "deepmerged": res.deepmerged, "aslist_merged": res.aslisted,
        "logs_merged": res.logs_merged, "log_targets": res.log_targets, "generated": res.generated,
        "rstb_entries": res.rstb_entries,
        "not_merged": res.not_merged, "notes": res.notes})
    return res


# The data logs in the order they are merged (texts last: 16 languages, the slowest)
MERGE_ORDER = ("actorinfo.yml", "gamedata.yml", "savedata.yml", "eventinfo.yml", "effects.yml", "areadata.yml",
               "residents.yml", "mainstatic.yml", "dstatic.yml", "map.yml", "quests.yml", "drops.json", "shop.aamp",
               "texts.json")
MERGE_ERRORS = (wc.FormatError, ValueError, TypeError, KeyError, IndexError, struct.error, RecursionError, OSError,
                BnpError)


def _combine(m, name: str, logs: dict[str, list]):
    """m.combine of one kind's logs (logs: name -> its items), save data with the game data's in view: a save data
    delete of a flag a later mod's game data change keeps is dropped (bcml_data.keep_saved)."""
    diff = m.combine(name, logs[name])
    if name == "savedata.yml" and "gamedata.yml" in logs and hasattr(m, "keep_saved"):
        try:
            diff, _ = m.keep_saved(diff, m.combine("gamedata.yml", logs["gamedata.yml"]))
        except (m.LogError, *MERGE_ERRORS):
            pass   # (the game data log is named when it is merged)
    return diff


def merge_logs(logs: dict[str, list], game: str, targets: Targets, res: Result, say=print) -> dict[str, int]:
    """Merge each kind of data log into `targets`: logs maps a log name to one item per package (lower priority
    first), each the list of its parsed logs (the base's, then each chosen option's); its module combines them and
    merges the result into the mod's copy of the game file (`targets.read`, else the game's), and each file that
    changes goes back into its packs (`targets.put`). res.logs_merged gets what changed, res.not_merged what was left
    out. Returns size table entries for the merged files (never below the game's)."""
    mergers = log_mergers()
    sizes: dict[str, int] = {}
    for name in sorted(logs, key=lambda n: (MERGE_ORDER.index(n) if n in MERGE_ORDER else len(MERGE_ORDER), n)):
        m = mergers[name]
        what = f"{name} ({LOG_NAMES.get(name, 'a BCML log')})"
        say(f"  merging {what}...")
        try:
            out = m.apply(name, _combine(m, name, logs), game, read=targets.read)
        except (m.LogError, *MERGE_ERRORS) as e:
            res.not_merged.append(f"{what} not merged ({type(e).__name__}: {e}): the game's are used")
            continue
        done = res.logs_merged.setdefault(name, [])
        named = sorted({t for t in out.get("targets", ()) if isinstance(t, str)} - set(out["files"]))
        if named:
            res.log_targets[name] = named
        for target, data in sorted(out["files"].items()):
            try:
                why = targets.put(target, data)
            except MERGE_ERRORS as e:
                why = f"{type(e).__name__}: {e}"
            if why:
                res.not_merged.append(f"{what}: {target} not merged ({why})")
            else:
                done.append(target)
        for k, v in out["sizes"].items():
            sizes[k] = max(v, sizes.get(k, 0))
        res.not_merged += out["notes"]
    return sizes


def _apply_deepmerge(pio: Pio, stage: str, game: str, blobs: dict[str, Blob], game_blobs: dict[str, Blob],
                     sizes: Sizes, aslist: bool = False, only: set[str] | None = None,
                     files_now: dict[str, str] | None = None) -> tuple[int, list[tuple[str, str]], list[str]]:
    """Apply one deepmerge.aamp (or, with aslist, an aslist.aamp): each listed parameter file inside a pack gets the
    changes (the pack is the mod's, already filled, or the game's when the mod has none). A deepmerge.aamp makes a
    file the pack lacks from the changes alone; an aslist.aamp names it, leaves a list its changes don't change as
    it is (and a pack only it would change out of the mod), and names its entries without a name. only: the packs
    (their Switch-layout paths, lowercase) to change, the others' files skipped; files_now: the stage's files
    (lowercase -> as spelled), else read from it. Returns (files merged, [(file, why not)], ["file: entry without a
    name"])."""
    log = "aslist.aamp" if aslist else "deepmerge.aamp"
    files_now = {r.lower(): r for r in _walk(stage)} if files_now is None else files_now
    done, failed, nameless = 0, [], []
    for path in file_table(pio):
        parts = path.replace("\\", "/").split("//")
        short = parts[-1]
        diff = pio.root.lists.get(zlib.crc32(path.encode()))
        if only is not None and parts[0].lower() not in only:
            continue
        try:
            if diff is None:
                raise wc.FormatError("no changes for it in the log")
            if len(parts) < 2:
                raise wc.FormatError("a whole-file change, not one inside a pack")
            check_name(parts[0], log)
            rel = files_now.get(parts[0].lower(), parts[0])
            gpath = _game_file(game, rel)
            top = blobs.get(rel)   # (kept in blobs, to be written, once something in it changes)
            if top is None:
                own = os.path.join(stage, *rel.split("/"))
                if os.path.isfile(own):
                    top = Blob(_read(own))
                elif gpath:
                    top = Blob(_read(gpath))   # (a pack the mod changes only through this log)
                else:
                    raise wc.FormatError("neither the mod nor the game has its pack")
            blob = top
            gblob = game_blobs.get(rel)
            if gblob is None and gpath:
                gblob = game_blobs[rel] = Blob(_read(gpath))
            # (every pack on the way grows with the change: (its size table name, it, the game's copy of it))
            tparts = rel.split("/")
            chain = [(canon("/".join(tparts[2:]), blob.orig, tparts[0].upper() == DLC_TID), blob, gblob)]
            for name in parts[1:-1]:
                blob = blob.child(name) if blob.is_sarc() else None
                if blob is None:
                    raise wc.FormatError(f"no {name} in its pack")
                gblob = gblob.child(name) if gblob is not None and gblob.is_sarc() else None
                chain.append((canon(name, blob.orig), blob, gblob))
            if not blob.is_sarc():
                raise wc.FormatError("its pack isn't a pack")
            name = parts[-1]
            old = blob.open().get(name)
            old_data = (old[0].data() if isinstance(old[0], Blob) else old[0]) if old else None
            if not old_data and aslist:
                raise wc.FormatError("its pack has no such file")
            yaz0 = bool(old_data) and old_data[:4] == b"Yaz0"
            base = aamp_read(unyaz0(old_data)[0] if yaz0 else old_data) if old_data else Pio()
            if aslist:
                bad: list[str] = []
                changed = aslist_merge(base.root, diff, bad)
                nameless += [f"{short}: {x}" for x in bad]
                if not changed:
                    continue   # (the log repeats what the list already has: nothing to write or size)
            else:
                aamp_merge(base.root, diff)
            new = aamp_write(base)
            g_entry = gblob.open().get(name) if gblob is not None and gblob.is_sarc() else None
            if g_entry:
                g_data = g_entry[0].data() if isinstance(g_entry[0], Blob) else g_entry[0]
                g_raw = unyaz0(g_data)[0] if g_data[:4] == b"Yaz0" else g_data
                structs = aslist_growth(aamp_read(g_raw).root, base.root) if _ext(name) == "baslist" else None
            blob.set(name, wc.yaz0_compress(new, struct.unpack_from(">I", old_data, 8)[0]) if yaz0 else new)
            blobs[rel] = top
            done += 1
            for size_name, pack, gpack in chain:
                if gpack is not None:
                    sizes.add(size_name, len(gpack.raw), pack, True)
            if g_entry:
                sizes.add(canon(name, g_data), len(g_raw), len(new), False, structs)
        except (wc.FormatError, struct.error, IndexError, BnpError) as e:
            failed.append((short, str(e)))
    return done, failed, nameless


def load_game_rstb(game: str) -> Rstb | None:
    p = os.path.join(game, "romfs", *rm.RSTB.split("/"))
    return Rstb(_read(p)) if os.path.isfile(p) else None


def write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")


def add(src: str, kind: str, stage: str, game: str, info: Info, *, picks: list[str] = (), no_defaults: bool = False,
        unzip=None, say=print) -> Result:
    """modman add's way in: a .bnp ("archive") or an extracted package in a .zip is extracted into a work folder
    next to stage (removed again afterwards), a folder is read where it is; then install() fills stage."""
    choose(info, picks, no_defaults)  # (an unknown option is refused before anything is extracted)
    sub = info.prefix.strip("/").split("/") if info.prefix.strip("/") else []
    what = os.path.basename(src)
    if kind == "folder":
        try:
            return install(os.path.join(src, *sub), stage, game, info=info, picks=picks, no_defaults=no_defaults,
                           move=False, say=say)
        except OSError as e:
            raise BnpError(f"Not added: installing {what} failed ({e}).") from None
    work = stage + ".bnp-raw"
    _remove(work)  # (left by an add that was cut short; the name is this module's own)
    res = None
    try:
        if kind == "archive":
            say(f"Extracting {what} (a big package takes a while)...")
            extract(src, work)
        else:
            unzip(src, work)
            _writable(_walk(work).values())
        say("Installing it as a Switch mod (options, packs filled from your game, logs merged, size table)...")
        res = install(os.path.join(work, *sub), stage, game, info=info, picks=picks, no_defaults=no_defaults,
                      move=True, say=say)
        return res
    except OSError as e:
        raise BnpError(f"Not added: installing {what} failed ({e}).") from None
    finally:
        if not _remove(work):
            note = f"the work folder {work} couldn't be removed (a file in it is in use?): delete it yourself"
            if res is not None:
                res.notes.append(note)
            else:
                say(f"  {note}")


# ---------------------------------------------------------------------------------------------------- the library

STORED_LISTS = ("packs", "generated", "not_merged", "option_folders", "options")
STORED_MAPS = ("logs_merged", "log_targets")
DAMAGED = "_damaged"     # (set by stored(): the keys of bnp.json that had the wrong type)


def stored(root: str) -> dict | None:
    """The bnp.json a library copy keeps (None for a mod that wasn't a BCML package). Its lists and maps are cleaned
    up (lists of text, maps of a log name to a list of text; anything else dropped), so a damaged copy can't crash
    deploy; the keys that had the wrong type are listed under DAMAGED (stored_problems names them)."""
    try:
        with open(os.path.join(root, META, "bnp.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("tool") != TOOL_ID:
        return None
    bad = []
    for k in STORED_LISTS:
        v = data.get(k)
        if v is not None and not isinstance(v, list):
            bad.append(k)
        data[k] = [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
    for k in STORED_MAPS:
        v = data.get(k)
        if v is not None and not isinstance(v, dict):
            bad.append(k)
        data[k] = ({n: [t for t in ts if isinstance(t, str)] for n, ts in v.items() if isinstance(ts, list)}
                   if isinstance(v, dict) else {})
    data[DAMAGED] = bad
    return data


def stored_problems(root: str) -> list[str]:
    """What the package's install couldn't merge (modman lists it under "Needs attention"), and a damaged copy."""
    info = stored(root) or {}
    out = [x for x in info.get("not_merged", []) if isinstance(x, str)]
    if info.get(DAMAGED):
        out.append(f"its library copy's {META}/bnp.json is damaged ({', '.join(info[DAMAGED])}): remove the mod and "
                   "add it again")
    return out


@dataclass
class DeployHook:
    rstb: rm.MergedRstb | None = None
    attention: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)   # overlay path -> merged bytes (deploy_hook(files=True))


@dataclass
class PackMerge:
    files: dict[str, bytes] = field(default_factory=dict)   # overlay path -> the merged file
    sizes: dict[str, int] = field(default_factory=dict)     # size table entries the merged files need
    merged_from: dict[str, list[str]] = field(default_factory=dict)   # overlay path -> the mods merged into it
    attention: list[str] = field(default_factory=list)


def package_logs(root: str, info: dict, mergers: dict | None = None) -> tuple[dict[str, list], list, list, list[str]]:
    """The logs a library copy keeps (.bnp/logs, then each chosen option's, in the package's order), read again:
    (data logs by name: [each layer's parsed log], [deepmerge.aamp Pio of each layer], [aslist.aamp ones], [what
    can't be read])."""
    mergers = log_mergers() if mergers is None else mergers
    folders = [""] + [f"options/{f}" for f in info.get("option_folders", []) if isinstance(f, str)]
    data: dict[str, list] = {}
    deep: list[tuple[str, Pio]] = []
    asl: list[tuple[str, Pio]] = []
    bad: list[str] = []
    for label in folders:
        where = f"{label}/logs/" if label else "logs/"
        folder = os.path.join(root, META, *(label.split("/") if label else []), "logs")
        if not os.path.isdir(folder):
            continue
        for rel, path in sorted(_walk(folder).items()):
            name = rel.lower()
            try:
                raw = _read(path)
                if name in ("deepmerge.aamp", "aslist.aamp"):
                    (deep if name == "deepmerge.aamp" else asl).append((where + name, aamp_read(raw)))
                elif name in mergers:
                    m = mergers[name]
                    log = m.read_log(name, raw, where) if m.__name__ == "bcml_texts" else m.read_log(name, raw)
                    data.setdefault(name, []).append(log)
            except Exception as e:  # noqa: BLE001 (any merger's LogError, a damaged file: named, the rest merged)
                bad.append(f"{where}{name}: {e}")
    return data, deep, asl, bad


def _inner(blob: Blob | None, path: str) -> bytes | None:
    return get_inside(blob, path.split("//")) if blob is not None else None


def _overlay(dst: Blob, src: Blob, game: Blob, path: str, skip: set[str], sizes: Sizes,
             hit: set[str] | None = None) -> None:
    """Put into dst (a pack, the game's copy and earlier mods' files) every file of src (a mod's copy) that differs
    from the game's: BCML's pack merge between mods, the later mod's file winning; packs inside packs merged the same
    way. skip: paths inside the top pack ("a//b" inside nested packs) whose copy in src is left out (what a package's
    logs made: they are merged again afterwards, so the earlier mods' own changes there stay). hit gets the paths put
    in."""
    dfiles, gfiles = dst.open(), game.open()
    for name, (content, align) in src.open().items():
        sub = f"{path}//{name}" if path else name
        if sub in skip:
            continue
        data = content.data() if isinstance(content, Blob) else content
        g = gfiles.get(name)
        gdata = None if g is None else (g[0].data() if isinstance(g[0], Blob) else g[0])
        if data == gdata:
            continue
        if gdata is not None and name in dfiles and is_packish(name):
            sb, db, gb = src.child(name), dst.child(name), game.child(name)
            if sb.is_sarc() and db.is_sarc() and gb.is_sarc():
                _overlay(db, sb, gb, sub, skip, sizes, hit)
                sizes.add(canon(name, gb.orig), len(gb.raw), db, True)
                continue
        dst.set(name, data, align)
        if hit is not None:
            hit.add(sub)


def merge_packages(mods: list[tuple[str, str, dict | None, dict]], extracted: str) -> PackMerge:
    """The files two or more enabled mods ship that a BCML package installed here also has (a pack, or a whole file
    its data logs made), merged the way BCML merges several mods: the game's copy, then each mod's own files over it
    in order (the files that differ from the game's; packs inside packs the same way; a package's files that its logs
    made are left out); then every package's deepmerge.aamp and aslist.aamp, then its data logs, applied again in
    order to that merged copy (so a change a mod made by hand, a mod that isn't a package say, stays next to them).
    A whole file (ActorInfo...) starts from the last copy no package's logs made, else the game's. mods: (name,
    library folder, bnp.json or None, {overlay path (lowercase): romfs_mods Entry}), lowest priority first. Reads
    only."""
    out = PackMerge()
    pkgs = [k for k, m in enumerate(mods) if m[2]]
    if not pkgs:
        return out
    owned: dict[int, set[str]] = {}
    done_by: dict[int, dict[str, set[str]]] = {}     # package -> log -> the targets it merged
    for k in pkgs:
        info = mods[k][2]
        owned[k] = {p.lower() for p in [*info.get("packs", []), *(info.get("generated") or [])] if isinstance(p, str)}
        done_by[k] = {}
        # (the files its logs changed at install, and the ones they name but didn't change: a shop or drop table
        # BCML shipped changed already; its log makes the change again on the merged copy)
        for key in ("logs_merged", "log_targets"):
            lm = info.get(key)
            for n, v in (lm.items() if isinstance(lm, dict) else []):
                if isinstance(v, list):
                    done_by[k].setdefault(n, set()).update(t for t in v if isinstance(t, str))
    claims: dict[str, list[int]] = {}
    for k, m in enumerate(mods):
        for t in m[3]:
            claims.setdefault(t, []).append(k)
    contested = {t: ks for t, ks in claims.items()
                 if len(ks) > 1 and not t.startswith(rm.DLC_PREFIX.lower()) and any(t in owned.get(k, ()) for k in ks)}
    if not contested:
        return out

    mergers = log_mergers()
    fixed_at = fixed_targets(mergers)
    logs = {}
    made: dict[int, set[tuple[str, str]]] = {}   # package -> (overlay path (lowercase), path inside; "": whole file)
    for k in pkgs:
        logs[k] = package_logs(mods[k][1], mods[k][2], mergers)
        out.attention += [f"{mods[k][0]}: {x}; not merged with the other mods" for x in logs[k][3]]
        mine = made[k] = set()
        # (what its logs made at install, and the files of the kinds BCML ships changed in the pack and logs too
        # (event info, quests...), when the log can be read: its log makes the same change again)
        for x in [t for targets in done_by[k].values() for t in targets] + [fixed_at[n] for n in logs[k][0]
                                                                              if n in fixed_at]:
            top, _, inner = x.partition("//")
            mine.add((top.lower(), inner))
        for _, pio in logs[k][1] + logs[k][2]:
            for path in file_table(pio):
                parts = path.replace("\\", "/").split("//")
                if len(parts) > 1:
                    mine.add((_target(parts[0]).lower(), "//".join(parts[1:])))

    romfs = os.path.join(extracted, "romfs")
    sizes = Sizes(load_game_rstb(extracted))
    merged: dict[str, Blob] = {}          # overlay path (lowercase) -> the merged pack
    games: dict[str, Blob] = {}
    whole: dict[str, int | None] = {}     # a whole file (lowercase) -> the mod whose copy it starts from (None: game)
    spelled: dict[str, str] = {}
    size_name: dict[str, str] = {}
    own_changes: dict[tuple[int, str], set[str]] = {}   # (mod, file) -> paths inside that its own copy changed
    for t, ks in sorted(contested.items()):
        last = mods[ks[-1]][3][t]
        spelled[t] = last.target
        names = [mods[k][0] for k in ks]
        if not is_packish(t):
            whole[t] = next((k for k in reversed(ks) if (t, "") not in made.get(k, ())), None)
            for k in ks:
                own_changes[(k, t)] = {""}
            continue
        gpath = os.path.join(romfs, *last.rel.split("/"))
        try:
            game = Blob(_read(gpath)) if os.path.isfile(gpath) else None
            if game is None or not game.is_sarc():
                continue   # (a pack the game lacks: the later mod's wins, as deploy_hook names)
            dst = Blob(game.orig)
            for k in ks:
                src = Blob(_read(mods[k][3][t].src))
                if not src.is_sarc():
                    raise wc.FormatError(f"{mods[k][0]}'s copy isn't a pack")
                _overlay(dst, src, game, "", {inner for top, inner in made.get(k, ()) if top == t and inner}, sizes,
                         own_changes.setdefault((k, t), set()))
        except (wc.FormatError, struct.error, IndexError, OSError) as e:
            out.attention.append(f"{last.target} can't be merged from {', '.join(names)} ({e}): {names[-1]}'s copy "
                                 "is used")
            continue
        merged[t], games[t] = dst, game
        size_name[t] = canon(last.rel, game.orig)
        sizes.add(size_name[t], len(game.raw), dst, True)
        out.merged_from[last.target] = names
    if not merged and not whole:
        return out

    # parameter changes: every package's, in order, into the merged packs (which hold no package's own result)
    blobs = {f"{BASE_TID}/romfs/{mods[contested[t][-1]][3][t].rel}": merged[t] for t in merged}
    files_now = {r.lower(): r for r in blobs}
    game_blobs = {r: games[t] for r, t in zip(blobs, merged)}
    for k in pkgs:
        for aslist, items in ((False, logs[k][1]), (True, logs[k][2])):
            for where, pio in items:
                _, failed, _ = _apply_deepmerge(pio, "", extracted, blobs, game_blobs, sizes, aslist=aslist,
                                                only=set(files_now), files_now=files_now)
                if failed:
                    out.attention.append(f"{mods[k][0]}: {where}: {len(failed)} file(s) not merged with the other "
                                         f"mods ({_some([f'{p}: {why}' for p, why in failed], 2)})")

    # data logs: each kind that changed a merged file (or whose file is in one: BCML ships some changes in the pack
    # and keeps the log, so the log changed nothing at install), every package's logs of it in order
    fixed = {n: _target_top(t).lower() for n, t in fixed_at.items()}
    kinds = sorted({n for k in pkgs for n in logs[k][0]
                    if fixed.get(n) in merged or fixed.get(n) in whole
                    or any(_target_top(x).lower() in merged or _target_top(x).lower() in whole
                           for x in done_by[k].get(n, ()))},
                   key=lambda n: (MERGE_ORDER.index(n) if n in MERGE_ORDER else len(MERGE_ORDER), n))
    # (a mod that isn't a package, later in the order than a package, whose own copy changes a file that package's
    # logs change too: the logs are applied after every mod's own files, so where both change the same entry (a text,
    # a flag, an actor...) the package's wins, though BCML, which logs every mod, would keep the later mod's: named)
    for name in kinds:
        for k in pkgs:
            if name not in logs[k][0]:
                continue
            for x in sorted(done_by[k].get(name, set()) | ({fixed_at[name]} if name in fixed_at else set())):
                top, _, inner = x.partition("//")
                for j in range(k + 1, len(mods)):
                    hits = () if mods[j][2] else own_changes.get((j, top.lower()), ())
                    if any(h == inner or (inner and h.startswith(inner + "//")) for h in hits):
                        out.attention.append(
                            f"{mods[j][0]} (later in the order, not a BCML package) and {mods[k][0]}'s {name} "
                            f"({LOG_NAMES.get(name, 'a BCML log')}) both change {x.split('//')[-1]}: where they change "
                            f"the same entry, {mods[k][0]}'s change is used, not {mods[j][0]}'s")
    whole_now: dict[str, bytes] = {}      # a whole file (lowercase) -> its merged bytes so far
    for name in kinds:
        m = mergers[name]

        def read(target: str) -> bytes | None:
            """The merged copy (the game's, the mods' own changes, the logs merged so far), or None: the game's."""
            parts = target.split("//")
            t = parts[0].lower()
            if t in merged and len(parts) > 1:
                return _inner(merged[t], "//".join(parts[1:]))
            if t in whole and len(parts) == 1:
                if t in whole_now:
                    return whole_now[t]
                k = whole[t]
                return _read(mods[k][3][t].src) if k is not None else None
            return None

        try:
            by_name = {n: [logs[k][0][n] for k in pkgs if n in logs[k][0]] for n in (name, "gamedata.yml")}
            res = m.apply(name, _combine(m, name, {n: v for n, v in by_name.items() if v}), extracted, read=read)
        except (m.LogError, *MERGE_ERRORS) as e:
            out.attention.append(f"{name} ({LOG_NAMES.get(name, 'a BCML log')}) can't be merged between the mods "
                                 f"({type(e).__name__}: {e}): the merged files hold the mods' own files without "
                                 "these logs' changes")
            continue
        for target, data in sorted(res["files"].items()):
            parts = target.split("//")
            t = parts[0].lower()
            if t in whole and len(parts) == 1:
                whole_now[t] = data
            elif t in merged:
                why = put_inside(merged[t], games[t], size_name[t], parts[1:], data, sizes)
                if why:
                    out.attention.append(f"{name}: {target} not merged between the mods ({why})")
        for k, v in res["sizes"].items():
            out.sizes[k] = max(v, out.sizes.get(k, 0))
    for t, k in whole.items():
        # (the merged copy; else, when the copy it started from isn't the one deployed anyway, that copy, so a log
        # made copy of a later package (the game's file and its logs, which changed nothing here) doesn't replace it)
        data = whole_now.get(t)
        if data is None and k is not None and k != contested[t][-1]:
            data = _read(mods[k][3][t].src)
        if data is not None:
            out.files[spelled[t]] = data
            out.merged_from[spelled[t]] = [mods[x][0] for x in contested[t]]
    for t, blob in merged.items():
        out.files[spelled[t]] = blob.data()
    for k, v in sizes.final().items():
        out.sizes[k] = max(v, out.sizes.get(k, 0))
    return out


def deploy_hook(mods: list[tuple[str, str]], extracted: str, files: bool = False) -> DeployHook:
    """What modman deploy adds for the enabled RomFS mods ((name, library folder), lowest priority first): one size
    table merging every mod's size changes when two or more change it (a BCML package's from its rstb.json, another
    mod's by comparing the table it ships with the game's), and a line for each BCML package whose packs a later mod
    replaces. files: also merge those packs (merge_packages) into hook.files (overlay path -> bytes, to deploy in
    place of every mod's copy), their sizes in the table (made even when only they need it); modman doesn't put them
    in the overlay yet (romfs_mods.add_generated could, with no size entry: hook.rstb holds them), so it calls this
    without. Reads only."""
    hook = DeployHook()
    # (name, a BCML package's entries or None, the size table a mod that isn't one ships or None, whether it ships one)
    pending: list[tuple[str, dict | None, str | None, bool]] = []
    seen: list[tuple[str, dict | None, set[str]]] = []
    loaded: list[tuple[str, str, dict | None, dict]] = []
    for name, root in mods:
        try:
            mod = rm.load_mod(root)
        except (rm.DeployError, OSError):
            continue
        info = stored(root)
        seen.append((name, info, {e.target.lower() for e in mod.entries}))
        loaded.append((name, root, info, {e.target.lower(): e for e in mod.entries}))
        own = os.path.join(root, META, "rstb.json")
        ship = next((e for e in mod.entries if not e.dlc and e.rel.lower() == rm.RSTB.lower()), None)
        if info is not None and os.path.isfile(own):
            try:
                entries = read_rstb_json(_read(own), f"{META}/rstb.json")
            except (BnpError, OSError) as e:
                hook.attention.append(f"{name}: its size changes can't be read ({e}); add it again")
                continue
            if entries or ship is not None:
                pending.append((name, entries, None, ship is not None))
        elif ship is not None:
            pending.append((name, None, ship.src, True))
    pm = PackMerge()
    if files:
        pm = merge_packages(loaded, extracted)
        hook.files = pm.files
        hook.attention += pm.attention
        for path in sorted(pm.files, key=str.lower):
            hook.notes.append(f"{path}: merged from {', '.join(pm.merged_from.get(path, []))} (their own files, "
                              "then their logs again, in order)")
    done = {p.lower() for p in pm.files}
    for k, (name, info, _) in enumerate(seen):
        if not info:
            continue
        mine = [p for p in [*info.get("packs", []), *(info.get("generated") or [])] if isinstance(p, str)]
        packs = {p.lower(): p for p in mine if p.lower() not in done}
        logs_in: dict[str, set[str]] = {}    # a file of the mod (lowercase) -> the logs merged into it
        for key in ("logs_merged", "log_targets"):
            merged = info.get(key)
            for log, targets in (merged.items() if isinstance(merged, dict) else []):
                for t in targets if isinstance(targets, list) else []:
                    if isinstance(t, str):
                        logs_in.setdefault(_target_top(t).lower(), set()).add(log)
        for later, _, theirs in seen[k + 1:]:
            lost = sorted(packs[p] for p in packs if p in theirs)
            if lost:
                kinds = sorted({x for p in packs if p in theirs for x in logs_in.get(p, ())})
                what = "pack(s)" if all(is_packish(p) for p in lost) else "file(s)"
                hook.attention.append(f"{name}: {later} replaces {len(lost)} of its {what} ({_some(lost, 3)}), so "
                                      f"{name}'s changes in them are lost"
                                      + (f", its merged {', '.join(kinds)} too" if kinds else "")
                                      + " (packs aren't merged between mods)")
    if pm.sizes:
        pending.append(("", pm.sizes, None, False))   # (the merged files' own sizes, one more table)
    if len(pending) < 2 and not pm.sizes:
        return hook
    try:
        game = load_game_rstb(extracted)
    except (BnpError, OSError) as e:
        hook.attention.append(f"The mods' size tables can't be merged: the game's own can't be read ({e})")
        return hook
    if game is None:
        hook.attention.append("The mods' size tables can't be merged: the game's own isn't in extracted\\romfs")
        return hook
    changes: list[tuple[str, dict]] = []
    for name, entries, ship, ships in pending:
        if ship is not None:
            try:
                entries = game.diff(Rstb(_read(ship)))
            except (BnpError, OSError) as e:
                hook.attention.append(f"{name}: its size table can't be read ({e}), so it isn't merged with the "
                                      "others'")
                continue
            changes.append((name, entries))
        else:
            # (one key per file: the table's own, as another mod's table gives them. A package whose sizes change
            # nothing leaves the table alone, unless its copy ships one anyway (added before 2026-11-07): the merged
            # table then takes that one's place.)
            entries = game.changes(game.keyed(entries))
            if entries or ships:
                changes.append((name, entries))
    own = any(not n for n, _ in changes)      # (the merged files need entries the game's table lacks)
    if len(changes) < 2 and not own:
        return hook
    sizes = merge_entries([c for _, c in changes])
    game.apply(sizes)
    names = [n for n, _ in changes if n]
    hook.rstb = rm.MergedRstb(game.to_bytes(), names)
    what = " and ".join(([f"the changes of {', '.join(names)}"] if names else []) + (["the merged files' sizes"]
                                                                                     if own else []))
    hook.notes.append(f"Size table (RSTB): {what} merged into one ({len(sizes)} entries; the larger size of each file "
                      "wins)")
    return hook


# ---------------------------------------------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info", help="show a package's name, version, options and logs (reads only)")
    p.add_argument("package", help="a .bnp, or an extracted BCML package (folder or .zip)")
    args = ap.parse_args(argv)
    try:
        src = os.path.abspath(args.package)
        kind = "archive" if os.path.isfile(src) and is_archive(src) else (
            "zip" if os.path.isfile(src) and zipfile.is_zipfile(src) else "folder" if os.path.isdir(src) else None)
        if kind is None:
            raise BnpError(f"{args.package} isn't a .bnp, a folder or a .zip.")
        info = peek(src, kind)
        print("\n".join(info.summary()))
        if kind == "archive":
            members, tool = list_archive(src)
            logs = sorted(m.name[len(info.prefix):] for m in members if not m.is_dir and "/logs/" in f"/{m.name}")
            print(f"  logs: {', '.join(logs) or 'none'}")
            print(f"  {sum(1 for m in members if not m.is_dir)} file(s), "
                  f"{sum(m.size for m in members) >> 20} MB (read with {tool})")
        print("Add it: python tools/modman.py add <package> [--bnp-option NAME] [--bnp-no-options]")
        return 0
    except (BnpError, OSError, zipfile.BadZipFile) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
