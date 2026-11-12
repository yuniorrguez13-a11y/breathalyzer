#!/usr/bin/env python3
"""The .breath file: one mod in one file, to share it. Breathalyzer makes one ("Export as .breath") and adds one
("Add a .breath", or a double-click on it). docs/breath-format.md describes the format.

    python tools/breath.py info <file.breath>                   what it holds, after every check (nothing written)
    python tools/breath.py unpack <file.breath> <new folder>    checked, unpacked, every file's SHA-256 verified
    python tools/breath.py export <mod id> <file.breath> [--library <folder>]   a mod of the library as a .breath

A .breath is a zip file: breath.json (the manifest: the mod's id, title, version, author, description, kind, every
file with its size and SHA-256, and whether it has code) and the files, under mod/ (the mod's own files) or package/
(the original package the player added: a BCML .bnp, a UKMM package or a Wii U mod, which Breathalyzer sets up again
from the other player's own game files, or a plain mod's own .zip, .7z or .rar download). Never anything made from
the game: a mod built from the player's game files (Breathalyzer Plus' recipes) can't be exported, a package mod
exports its original package, not the library's copy (which holds the player's game files merged in), and a plain
mod exports the files it was added from while they are there (a library copy with a whole Pack/*.pack, which may
have been filled in from the game, is never exported).

A .breath comes from someone else, so everything in it is checked before anything is written, with bnp.py's archive
rules: every name (nothing outside the folder, no names Windows can't use), the number and sizes of the files (no
more than a whole game), no zip bomb (only stored or deflated members, no impossible packing ratio, no members that
overlap), no link, no password, no packed file inside (except the one original package); then the manifest must list
exactly the files that are there, with their sizes, and say truly whether there is code (a DLL or any program). While
unpacking, every file's SHA-256 must match the manifest: on any mismatch nothing is kept. Nothing in it is run.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import sys
import time
import tomllib
import zipfile
import zlib
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bnp  # noqa: E402 (its archive rules: check_name and the size limits)

EXT = ".breath"
MANIFEST = "breath.json"
FORMAT = "breathalyzer-mod"
FORMAT_VERSION = 1
MOD_DIR, PACKAGE_DIR = "mod", "package"
KINDS = ("mod", "package")
PACKAGE_TYPES = {"bnp": "a BCML package (.bnp)", "ukmm": "a UKMM package", "wiiu": "a Wii U mod",
                 "download": "the mod's own download (.zip, .7z or .rar)"}
MAX_MANIFEST = 1 << 20
MAX_MEMBERS = bnp.MAX_MEMBERS          # files in one .breath (as in a .bnp)
MAX_UNPACKED = bnp.MAX_UNPACKED        # all of them together: never more than the whole game
MAX_MEMBER = bnp.MAX_MEMBER            # one file
MAX_RATIO = 1100                       # deflate can't pack more than about 1032 to 1: a member claiming more lies
SPARE = bnp.SPARE_MIN                  # free space unpacking leaves on the drive
METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)   # what export writes, and all a .breath may use
# Code: programs and libraries that run on the PC (the mod loader loads a mod's DLLs into the game)
CODE_EXTS = {".dll", ".exe", ".com", ".scr", ".cpl", ".sys", ".drv", ".ocx", ".msi", ".msp", ".bat", ".cmd", ".ps1",
             ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".py", ".pyw", ".pyd", ".jar",
             ".so", ".dylib", ".reg"}
# Packed files: never inside a .breath, but for the one original package under package/
ARCHIVE_EXTS = {".breath", ".zip", ".7z", ".rar", ".bnp", ".tar", ".gz", ".tgz", ".xz", ".txz", ".bz2", ".tbz2",
                ".zst", ".lz", ".lzma", ".cab", ".iso", ".wim", ".arj", ".lzh", ".z", ".zipx"}
PACKAGE_EXTS = {".bnp", ".zip", ".7z", ".rar"}
DOWNLOAD_EXTS = {".zip", ".7z", ".rar"}  # a plain mod added from one of these exports it as it came
# A whole Pack/*.pack in the library's copy of a plain mod may hold files filled in from the player's own game
# (bnp.fill_old_packs): such a copy is never exported, only the mod's own files.
WHOLE_PACK = re.compile(r"(?:^|/)pack/[^/]+\.pack$", re.IGNORECASE)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
TEXT_MAX = {"title": 80, "version": 40, "author": 120, "description": 2000, "made_with": 80}
WAITING = ".waiting"                   # in a received folder until its mod is in the library
STALE_AFTER = 24 * 3600                # a received folder still waiting after this long is left over: removed
BUILT_MARKS = ("built.txt",)           # tools/quality.py leaves this in a mod it built from the game's files
FIXED_DATE = (1980, 1, 1, 0, 0, 0)


class BreathError(Exception):
    """A .breath that can't be made or added. The message is for the player: plain words, what to do."""


@dataclass
class FileEntry:
    path: str        # "/"-separated, under mod/ or package/
    size: int
    sha256: str


@dataclass
class Manifest:
    id: str
    title: str
    version: str = ""
    author: str = ""
    description: str = ""
    kind: str = "mod"              # mod: the mod's files; package: its original package
    package: str = ""              # for kind package: bnp, ukmm or wiiu
    has_code: bool = False
    code: list[str] = field(default_factory=list)
    files: list[FileEntry] = field(default_factory=list)
    made_with: str = ""

    def to_json(self) -> dict:
        return {"format": FORMAT, "format_version": FORMAT_VERSION, "id": self.id, "title": self.title,
                "version": self.version, "author": self.author, "description": self.description, "kind": self.kind,
                "package": self.package, "has_code": self.has_code, "code": list(self.code),
                "files": [{"path": f.path, "size": f.size, "sha256": f.sha256} for f in self.files],
                "made_with": self.made_with}

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    def what(self) -> str:
        """What it is, in plain words."""
        if self.kind == "package":
            kind = PACKAGE_TYPES.get(self.package, "a mod package")
            how = "Breathalyzer adds it as it came" if self.package == "download" else \
                "Breathalyzer sets it up from your own game files"
            return f"{kind[0].upper()}{kind[1:]}: {how}."
        return "Files for the game, with code." if self.has_code else "Files for the game."


# ---------------------------------------------------------------------------------------------------- small parts

# (marks that turn text around or hide it: a title could pretend to be something else with them)
HIDDEN = set("  ‪‫‬‭‮⁦⁧⁨⁩​‎‏﻿")


def clean_text(value, limit: int, one_line: bool = True) -> str:
    """Text from a manifest, for the window: no control characters or hidden marks, at most `limit` characters;
    line breaks only where one_line is False (at most one empty line in a row)."""
    if not isinstance(value, str):
        return ""
    chars = []
    for c in value:
        if c == "\n" and not one_line:
            chars.append("\n")
        elif ord(c) < 32 or 0x7F <= ord(c) < 0xA0 or c in HIDDEN:
            chars.append(" ")
        else:
            chars.append(c)
    text = "".join(chars)
    if one_line:
        text = " ".join(text.split())
    else:
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(" ".join(line.split()) for line in text.split("\n"))).strip()
    return text[:limit].rstrip()


def ext_of(path: str) -> str:
    return os.path.splitext(path.rsplit("/", 1)[-1])[1].lower()


def is_code(path: str) -> bool:
    return ext_of(path) in CODE_EXTS


def is_packed(path: str) -> bool:
    return ext_of(path) in ARCHIVE_EXTS


def safe_rel(raw: str, where: str = "This .breath file") -> list[str]:
    """bnp.py's name rules for one file name: its parts, or BreathError in plain words."""
    try:
        parts = bnp.check_name(raw, where)
    except bnp.BnpError:
        raise BreathError(f"Not added: {where} holds a file with a name that isn't safe ({raw!r}).") from None
    if parts[0].startswith("-"):
        raise BreathError(f"Not added: {where} holds a name starting with '-' ({raw!r}).")
    return parts


def new_name() -> str:
    """A new folder name for a received .breath: the time and a random part."""
    return time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)


def free_space(folder: str) -> int:
    p = os.path.abspath(folder)
    while not os.path.isdir(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    return shutil.disk_usage(p).free


def remove_tree(path: str) -> bool:
    """Remove a folder this module made (a received .breath). Links inside are removed, never followed. False when
    something is left (a file in use)."""
    if not os.path.lexists(path):
        return True
    if os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path)):
        with contextlib.suppress(OSError):
            os.unlink(path) if os.path.islink(path) else os.rmdir(path)
        return not os.path.lexists(path)

    def onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=onerror)
    else:
        shutil.rmtree(path, onerror=onerror)
    return not os.path.lexists(path)


def sweep(received: str, older: float = STALE_AFTER) -> list[str]:
    """Remove received folders still waiting (their mod never went into the library: the window closed, a power
    cut) after `older` seconds. Returns the ones that couldn't be removed."""
    left = []
    try:
        names = os.listdir(received)
    except OSError:
        return left
    now = time.time()
    for n in names:
        p = os.path.join(received, n)
        mark = os.path.join(p, WAITING)
        try:
            if os.path.islink(p) or not os.path.isfile(mark) or now - os.path.getmtime(mark) < older:
                continue
        except OSError:
            continue
        if not remove_tree(p):
            left.append(p)
    return left


# ---------------------------------------------------------------------------------------------------- the manifest

def parse_manifest(data: bytes) -> Manifest:
    """breath.json, every value checked (BreathError says what is wrong, in plain words)."""
    bad = "Not added: this .breath file's list of what it holds (breath.json) is damaged"
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise BreathError(f"{bad}.") from None
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise BreathError("Not added: this isn't a .breath file made by Breathalyzer.")
    version = raw.get("format_version")
    if type(version) is not int or version < 1:
        raise BreathError(f"{bad}.")
    if version > FORMAT_VERSION:
        raise BreathError("This .breath file was made by a newer Breathalyzer. Get the newest Breathalyzer, then "
                          "open it again.")
    mod_id = raw.get("id")
    if not isinstance(mod_id, str) or not ID_RE.fullmatch(mod_id) or ".." in mod_id:
        raise BreathError(f"{bad} (its mod id).")
    title = clean_text(raw.get("title"), TEXT_MAX["title"])
    if not title:
        raise BreathError(f"{bad} (it has no title).")
    kind = raw.get("kind")
    package = raw.get("package") or ""
    if kind not in KINDS or (kind == "package") != bool(package) or (package and package not in PACKAGE_TYPES):
        raise BreathError(f"{bad} (what kind of mod it is).")
    has_code = raw.get("has_code")
    code = raw.get("code")
    files = raw.get("files")
    if type(has_code) is not bool or not isinstance(code, list) or not all(isinstance(c, str) for c in code) \
            or not isinstance(files, list) or not files:
        raise BreathError(f"{bad}.")
    if len(files) > MAX_MEMBERS:
        raise BreathError(f"Not added: this .breath file lists {len(files)} files: too many for a mod.")
    entries = []
    for f in files:
        if not isinstance(f, dict) or not isinstance(f.get("path"), str) or type(f.get("size")) is not int \
                or f["size"] < 0 or not isinstance(f.get("sha256"), str) or not SHA_RE.fullmatch(f["sha256"]):
            raise BreathError(f"{bad} (a file in its list).")
        entries.append(FileEntry(f["path"], f["size"], f["sha256"]))
    return Manifest(mod_id, title, clean_text(raw.get("version"), TEXT_MAX["version"]),
                    clean_text(raw.get("author"), TEXT_MAX["author"]),
                    clean_text(raw.get("description"), TEXT_MAX["description"], one_line=False), kind, package,
                    has_code, sorted(code), entries, clean_text(raw.get("made_with"), TEXT_MAX["made_with"]))


# ---------------------------------------------------------------------------------------------------- checking one

@dataclass
class Checked:
    manifest: Manifest
    infos: dict[str, zipfile.ZipInfo]     # path -> member, for every file the manifest lists
    manifest_bytes: bytes


def _is_link(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def check_archive(zf: zipfile.ZipFile, name: str = "This .breath file") -> Checked:
    """Every check that needs no unpacking: names, counts, sizes, packing, overlaps, the manifest and that it lists
    exactly what is there, the code it says it has, and nothing packed inside but the one original package."""
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS + 1:
        raise BreathError(f"Not added: {name} holds {len(infos)} files: too many for a mod.")
    files: dict[str, zipfile.ZipInfo] = {}
    seen: set[str] = set()
    total = 0
    for info in infos:
        raw = info.filename
        if raw.endswith("/") and info.file_size == 0:
            if raw.strip("/"):
                safe_rel(raw, name)
            continue
        parts = safe_rel(raw, name)
        rel = "/".join(parts)
        if rel != raw:
            raise BreathError(f"Not added: {name} holds a file with an odd name ({raw!r}).")
        if _is_link(info):
            raise BreathError(f"Not added: {name} holds a link ({raw}). Mods don't need links.")
        if info.flag_bits & 1:
            raise BreathError(f"Not added: {name} is password-protected.")
        if info.compress_type not in METHODS:
            raise BreathError(f"Not added: {name} is packed in a way Breathalyzer doesn't make. Ask for a new copy.")
        if info.file_size > MAX_MEMBER:
            raise BreathError(f"Not added: {name} holds a file of {info.file_size >> 30} GB ({raw}), bigger than any "
                              "game file: damaged, or not a mod.")
        if (info.compress_type == zipfile.ZIP_STORED and info.file_size != info.compress_size) or \
                info.file_size > (info.compress_size + 1024) * MAX_RATIO:
            raise BreathError(f"Not added: {name} claims sizes that can't be true ({raw}): damaged, or made to fill "
                              "your disk.")
        total += info.file_size
        if total > MAX_UNPACKED:
            raise BreathError(f"Not added: {name} unpacks to more than {MAX_UNPACKED >> 30} GB, more than the whole "
                              "game: damaged, or not a mod.")
        if rel.lower() in seen:
            raise BreathError(f"Not added: {name} has two files named {rel}.")
        seen.add(rel.lower())
        files[rel] = info
    # (members that share bytes: the trick behind the worst zip bombs; each needs its own place in the file)
    order = sorted((i for i in infos), key=lambda i: i.header_offset)
    end = getattr(zf, "start_dir", None)
    for a, b in zip(order, [*order[1:], None]):
        limit = b.header_offset if b is not None else end
        if limit is not None and a.header_offset + 30 + a.compress_size > limit:
            raise BreathError(f"Not added: {name} is damaged (its files overlap). Ask for a new copy.")
    head = files.pop(MANIFEST, None)
    if head is None:
        raise BreathError(f"Not added: {name} isn't a .breath file (it has no {MANIFEST}).")
    if head.file_size > MAX_MANIFEST:
        raise BreathError(f"Not added: {name}'s {MANIFEST} is too big.")
    try:
        with zf.open(head) as f:
            data = f.read(MAX_MANIFEST + 1)
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError, RuntimeError) as e:
        raise BreathError(f"Not added: {name} is damaged ({e}). Ask for a new copy.") from None
    man = parse_manifest(data)
    top = MOD_DIR if man.kind == "mod" else PACKAGE_DIR
    listed = {f.path: f for f in man.files}
    if len(listed) != len(man.files):
        raise BreathError(f"Not added: {name}'s list names a file twice.")
    extra = sorted(p for p in files if p not in listed)
    missing = sorted(p for p in listed if p not in files)
    if extra or missing:
        what = (f"holds files its list doesn't name ({', '.join(extra[:3])})" if extra
                else f"misses files its list names ({', '.join(missing[:3])})")
        raise BreathError(f"Not added: {name} {what}. It may have been changed after it was made.")
    for p, f in listed.items():
        if p.split("/", 1)[0] != top or "/" not in p:
            raise BreathError(f"Not added: {name} has a file outside its {top} folder ({p}).")
        if files[p].file_size != f.size:
            raise BreathError(f"Not added: {name} doesn't match its list ({p} has another size). It may have been "
                              "changed after it was made.")
    code = sorted(p for p in listed if is_code(p))
    if code and not man.has_code:
        names = ", ".join(c.rsplit("/", 1)[-1] for c in code[:3])
        raise BreathError(f"Not added: {name} says it has no code, but it has {names}. It may have been changed to "
                          "hide that.")
    if code != man.code or bool(code) != man.has_code:
        raise BreathError(f"Not added: {name}'s list of code files doesn't match what is in it.")
    if man.kind == "mod":
        packed = [p for p in listed if is_packed(p)]
        if packed:
            raise BreathError(f"Not added: {name} has a packed file inside it ({packed[0].rsplit('/', 1)[-1]}). "
                              ".breath files don't hold packed files.")
    else:
        tops = {p.split("/")[1] for p in listed}
        if len(tops) != 1:
            raise BreathError(f"Not added: {name} holds more than one package.")
        only = tops.pop()
        single = f"{PACKAGE_DIR}/{only}"
        if single in listed:
            if ext_of(only) not in PACKAGE_EXTS:
                raise BreathError(f"Not added: {name}'s package ({only}) isn't a .bnp, .zip or .7z file.")
        else:
            packed = [p for p in listed if is_packed(p)]
            if packed:
                raise BreathError(f"Not added: {name} has a packed file inside its package "
                                  f"({packed[0].rsplit('/', 1)[-1]}).")
    return Checked(man, {p: files[p] for p in listed}, data)


def peek(path: str) -> Manifest:
    """What a .breath holds, after every check but the SHA-256s (they need it unpacked). Nothing is written."""
    with open_zip(path) as zf:
        return check_archive(zf, os.path.basename(path)).manifest


@contextlib.contextmanager
def open_zip(path: str):
    name = os.path.basename(path)
    try:
        if os.path.getsize(path) > MAX_UNPACKED:
            raise BreathError(f"Not added: {name} is bigger than the whole game: not a mod.")
        zf = zipfile.ZipFile(path)
    except FileNotFoundError:
        raise BreathError(f"{name} isn't there any more.") from None
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, EOFError) as e:
        raise BreathError(f"Not added: {name} isn't a .breath file, or it is damaged ({type(e).__name__}).") from None
    try:
        yield zf
    finally:
        zf.close()


# ---------------------------------------------------------------------------------------------------- unpacking one

@dataclass
class Received:
    manifest: Manifest
    folder: str           # where it was unpacked (a new folder)
    payload: str          # what `modman add` takes: <folder>/mod, or <folder>/package/<name>
    source: str           # the .breath file
    code: list[str]       # its code files, and code files inside a .zip package (by name)
    breath_size: int      # bytes of the .breath file


def unpack(path: str, dest: str, spare: int | None = None) -> Received:
    """Check a .breath, then unpack it into dest (a new folder) and verify every file's SHA-256 against its list.
    On any problem nothing is kept (dest is removed) and BreathError says why. spare: free space to leave on the
    drive (default 2 GB, or 1% of what it unpacks to when more)."""
    name = os.path.basename(path)
    if os.path.lexists(dest):
        raise BreathError(f"{dest} already exists: unpacking needs a new folder.")
    with open_zip(path) as zf:
        checked = check_archive(zf, name)
        man = checked.manifest
        need = man.size
        free = free_space(os.path.dirname(os.path.abspath(dest)))
        spare = max(SPARE if spare is None else spare, need // 100)
        if need + spare > free:
            raise BreathError(f"Not enough free space for {name}: {need >> 20} MB needed and {spare >> 20} MB kept "
                              f"free, {free >> 20} MB free.")
        os.makedirs(dest)
        try:
            with open(os.path.join(dest, WAITING), "w", encoding="utf-8") as f:
                f.write(f"{os.path.abspath(path)}\n")
            for entry in man.files:
                _unpack_one(zf, checked.infos[entry.path], entry, dest, name)
            with open(os.path.join(dest, MANIFEST), "wb") as f:
                f.write(checked.manifest_bytes)
        except BaseException:
            remove_tree(dest)
            raise
    top = man.files[0].path.split("/")
    payload = os.path.join(dest, MOD_DIR) if man.kind == "mod" else os.path.join(dest, PACKAGE_DIR, top[1])
    code = list(man.code)
    if man.kind == "package" and os.path.isfile(payload) and zipfile.is_zipfile(payload):
        with contextlib.suppress(zipfile.BadZipFile, OSError, ValueError):
            with zipfile.ZipFile(payload) as inner:
                code += [f"{PACKAGE_DIR}/{top[1]}/{n}" for n in inner.namelist() if is_code(n)][:20]
    return Received(man, dest, payload, os.path.abspath(path), code, os.path.getsize(path))


def _unpack_one(zf: zipfile.ZipFile, info: zipfile.ZipInfo, entry: FileEntry, dest: str, name: str) -> None:
    parts = safe_rel(entry.path, name)
    target = os.path.join(dest, *parts)
    base = os.path.abspath(dest)
    if os.path.commonpath([base, os.path.abspath(target)]) != base:   # (the name checks already rule this out)
        raise BreathError(f"Not added: {name} holds an unsafe name ({entry.path}).")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    h, n = hashlib.sha256(), 0
    try:
        with zf.open(info) as src, open(target, "xb") as out:
            while True:
                block = src.read(1 << 20)
                if not block:
                    break
                n += len(block)
                if n > entry.size:
                    break
                h.update(block)
                out.write(block)
    except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError) as e:
        raise BreathError(f"Not added: {name} is damaged ({entry.path}: {e}). Ask for a new copy.") from None
    if n != entry.size or h.hexdigest() != entry.sha256:
        raise BreathError(f"Not added: {name} was changed after it was made, or is damaged: "
                          f"{entry.path.rsplit('/', 1)[-1]} doesn't match its SHA-256. Ask for a new copy.")


def added(rec: Received) -> None:
    """The mod is in the library now: a mod's own files go (the library has its copy), a package stays (it is the
    original package the library entry names, for the next export)."""
    if rec.manifest.kind == "mod":
        remove_tree(rec.folder)
    else:
        with contextlib.suppress(OSError):
            os.remove(os.path.join(rec.folder, WAITING))


def dropped(rec: Received) -> None:
    """Not added (Not now, or the add failed): nothing kept."""
    remove_tree(rec.folder)


# ---------------------------------------------------------------------------------------------------- making one

@dataclass
class Exported:
    path: str
    manifest: Manifest
    notes: list[str]
    size: int             # bytes of the .breath file


def _inside(child: str, parent: str) -> bool:
    try:
        c, p = os.path.normcase(os.path.abspath(child)), os.path.normcase(os.path.abspath(parent))
        return os.path.commonpath([c, p]) == p
    except ValueError:   # (another drive)
        return False


def _walk(root: str, prefix: str, notes: list[str], skip_top_dots: bool = False) -> list[tuple[str, str, int]]:
    """(path in the .breath, file, size) for every file under root, by name. Links are left out (noted)."""
    out = []
    stack = [("", root)]
    links = 0
    while stack:
        rel, folder = stack.pop()
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError as e:
            raise BreathError(f"Can't read {folder}: {e.strerror or e}.") from None
        for e in entries:
            r = f"{rel}/{e.name}" if rel else e.name
            if skip_top_dots and not rel and e.name.startswith("."):
                continue
            if e.is_symlink() or (hasattr(e, "is_junction") and e.is_junction()):
                links += 1
                continue
            if e.is_dir(follow_symlinks=False):
                stack.append((r, e.path))
            elif e.is_file(follow_symlinks=False):
                out.append((f"{prefix}/{r}", e.path, e.stat(follow_symlinks=False).st_size))
    if links:
        notes.append(f"Left out {links} link(s): a .breath holds files only.")
    return sorted(out)


def read_mod_toml(folder: str) -> dict:
    """The [mod] table of a mod.toml at the folder's top (or in the one folder it holds); {} without one."""
    paths = [os.path.join(folder, "mod.toml")]
    with contextlib.suppress(OSError):
        subs = [e.path for e in os.scandir(folder) if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")]
        if len(subs) == 1:
            paths.append(os.path.join(subs[0], "mod.toml"))
    for p in paths:
        try:
            if os.path.getsize(p) > 256 * 1024:
                continue
            with open(p, "rb") as f:
                data = tomllib.loads(f.read().decode("utf-8-sig"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        mod = data.get("mod")
        return mod if isinstance(mod, dict) else {}
    return {}


def _package_texts(folder: str) -> tuple[str, str]:
    """(description, author) the library keeps for a package mod: BCML's info.json, UKMM's meta.yml."""
    try:
        with open(os.path.join(folder, ".bnp", "info.json"), encoding="utf-8-sig") as f:
            info = json.loads(f.read(1 << 20))
        if isinstance(info, dict):
            return (info.get("desc") if isinstance(info.get("desc"), str) else "",
                    info.get("author") if isinstance(info.get("author"), str) else "")
    except (OSError, ValueError):
        pass
    out = {"description": "", "author": ""}
    with contextlib.suppress(OSError, UnicodeDecodeError):
        with open(os.path.join(folder, ".ukmm", "meta.yml"), encoding="utf-8-sig") as f:
            for line in f.read(1 << 20).splitlines():
                m = re.match(r"^(description|author):\s*(.*)$", line)
                if m and m.group(2).strip() not in ("", "|", ">", "|-", ">-"):
                    out[m.group(1)] = m.group(2).strip().strip("'\"")
    return out["description"], out["author"]


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECIPES = {"giruka.castle-date"}   # (castle\ isn't in the zips; tools/castle_build.py builds it from the game's map)


def recipe_ids(repo: str = REPO) -> set[str]:
    """The ids of the mods Breathalyzer builds from the player's own game files: each native/examples/<x> with a
    recipe.json (tools/quality.py), the castle, and RECIPES."""
    out = set(RECIPES)
    folders = []
    with contextlib.suppress(OSError):
        folders = [e.path for e in os.scandir(os.path.join(repo, "native", "examples")) if e.is_dir()]
    for folder in [*folders, os.path.join(repo, "castle")]:
        if folder.endswith("castle") or os.path.isfile(os.path.join(folder, "recipe.json")):
            mid = read_mod_toml(folder).get("id")
            if isinstance(mid, str):
                out.add(mid)
    return out


def game_made(library: str, entry: dict, recipes=()) -> bool:
    """A mod built from the player's own game files (Breathalyzer Plus' recipes, tools/quality.py, the castle):
    never shared. recipes: more ids (the Plus mod list's recipes)."""
    ids = recipe_ids() | set(recipes)
    folder = os.path.join(library, "mods", str(entry.get("id", "")))
    if entry.get("id") in ids:
        return True
    if any(os.path.isfile(os.path.join(folder, m)) for m in BUILT_MARKS):
        return True
    man = read_mod_toml(folder)
    return isinstance(man.get("id"), str) and man["id"] in ids


def package_type(entry: dict) -> str:
    for key in ("bnp", "ukmm", "wiiu"):
        if key in entry:
            return key
    return ""


def export(library: str, entry: dict, dest: str, *, recipes=(), install: str | None = None,
           made_with: str = "Breathalyzer") -> Exported:
    """Write a library mod (its config.json entry) as a .breath file at dest. A package mod (BCML, UKMM, Wii U)
    carries its original package (its `source`), never the library's copy; a mod built from the player's game files
    is refused (recipes: more such ids)."""
    mod_id = entry.get("id")
    title = clean_text(entry.get("name") or mod_id, TEXT_MAX["title"]) or "mod"
    if not isinstance(mod_id, str) or not ID_RE.fullmatch(mod_id) or ".." in mod_id:
        raise BreathError(f"{title} can't be exported: its id in your mod list is odd.")
    folder = os.path.join(library, "mods", mod_id)
    if not os.path.isdir(folder):
        raise BreathError(f"{title} can't be exported: its folder isn't in your mods folder any more.")
    if game_made(library, entry, recipes):
        raise BreathError(f"{title} is made from your own game files, so it can't go in a .breath file. "
                          "Breathalyzer Plus makes it on each PC.")
    if not dest.lower().endswith(EXT):
        dest += EXT
    dest = os.path.abspath(dest)
    if _inside(dest, library) or (install and _inside(dest, install)):
        raise BreathError("Save the .breath file somewhere else, like your Desktop (not in the mods or Savage Wind "
                          "folder).")
    notes: list[str] = []
    ptype = package_type(entry)
    man = read_mod_toml(folder)
    one_package = False   # (a package file: the one packed file a .breath may hold)
    src = entry.get("source")
    mod_from = folder     # (a plain mod: its own files as they came when they are still there, else the library's)
    if not ptype and isinstance(src, str) and src and os.path.exists(src) and not _inside(src, library) \
            and not (install and _inside(src, install)) and not _inside(dest, src):
        if os.path.isdir(src):
            mod_from = src
        elif ext_of(os.path.basename(src)) in DOWNLOAD_EXTS:
            ptype = "download"
    if ptype:
        if not isinstance(src, str) or not src:
            raise BreathError(f"{title} can't be exported: Breathalyzer doesn't know where its original package is.")
        if not os.path.exists(src):
            raise BreathError(f"{title} can't be exported: its original package isn't there any more ({src}). Put "
                              "it back there, or add the mod again from it.")
        if _inside(src, library) or (install and _inside(src, install)):
            raise BreathError(f"{title} can't be exported: its package is in your mods or Savage Wind folder, "
                              "which hold your game's files.")
        base = os.path.basename(os.path.normpath(src))
        safe_rel(base, "The package")
        if os.path.isdir(src):
            files = _walk(src, f"{PACKAGE_DIR}/{base}", notes)
        else:
            if ext_of(base) not in PACKAGE_EXTS:
                raise BreathError(f"{title} can't be exported: its package ({base}) isn't a .bnp, .zip or .7z file.")
            files = [(f"{PACKAGE_DIR}/{base}", src, os.path.getsize(src))]
            one_package = True
        description, author = _package_texts(folder)
    else:
        files = _walk(mod_from, MOD_DIR, notes, skip_top_dots=True)
        if mod_from == folder and any(WHOLE_PACK.search(a) for a, _, _ in files):
            raise BreathError(f"{title} can't be exported: its own files aren't there any more"
                              + (f" ({src})" if isinstance(src, str) and src else "") + ", and the copy in your mods "
                              "folder may hold files from your game. Put them back there, or add the mod again from "
                              "its download.")
        description = man.get("description") if isinstance(man.get("description"), str) else ""
        authors = man.get("authors")
        author = ", ".join(a for a in authors if isinstance(a, str)) if isinstance(authors, list) else ""
    keep = []
    for arc, path, size in files:
        if is_packed(arc) and not one_package:
            notes.append(f"Left out {arc.rsplit('/', 1)[-1]}: a .breath doesn't hold packed files.")
            continue
        safe_rel(arc, "This mod")
        keep.append((arc, path, size))
    files = keep
    if not files:
        raise BreathError(f"{title} can't be exported: it has no files.")
    if len(files) > MAX_MEMBERS:
        raise BreathError(f"{title} can't be exported: it has more than {MAX_MEMBERS} files.")
    total = sum(s for _, _, s in files)
    big = max(files, key=lambda f: f[2])
    if total > MAX_UNPACKED or big[2] > MAX_MEMBER:
        raise BreathError(f"{title} can't be exported: it is bigger than the whole game.")
    lower = [a.lower() for a, _, _ in files]
    if len(set(lower)) != len(lower):
        raise BreathError(f"{title} can't be exported: two of its files have the same name.")
    if free_space(os.path.dirname(dest)) < total + (64 << 20):
        raise BreathError(f"Not enough free space for the .breath file: about {total >> 20} MB needed.")
    mid = man.get("id") if isinstance(man.get("id"), str) and ID_RE.fullmatch(man["id"]) and ".." not in man["id"] \
        else mod_id
    version = entry.get("version") or (man.get("version") if isinstance(man.get("version"), str) else "")
    code = sorted(a for a, _, _ in files if is_code(a))
    out = Manifest(mid, title, clean_text(version, TEXT_MAX["version"]), clean_text(author, TEXT_MAX["author"]),
                   clean_text(description, TEXT_MAX["description"], one_line=False),
                   "package" if ptype else "mod", ptype, bool(code), code, [],
                   clean_text(made_with, TEXT_MAX["made_with"]))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = f"{dest}.part-{os.getpid()}"
    try:
        with zipfile.ZipFile(tmp, "w", allowZip64=True) as zf:
            for arc, path, size in files:
                out.files.append(_write_one(zf, arc, path, size))
            info = zipfile.ZipInfo(MANIFEST, FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, json.dumps(out.to_json(), indent=1, ensure_ascii=False) + "\n")
        os.replace(tmp, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    return Exported(dest, out, notes, os.path.getsize(dest))


def _write_one(zf: zipfile.ZipFile, arc: str, path: str, size: int) -> FileEntry:
    try:
        t = time.localtime(max(os.path.getmtime(path), 315532800 + 86400))
    except OSError:
        t = None
    info = zipfile.ZipInfo(arc, tuple(t[:6]) if t else FIXED_DATE)
    info.compress_type = zipfile.ZIP_STORED if is_packed(arc) else zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.file_size = size
    h, n = hashlib.sha256(), 0
    try:
        with open(path, "rb") as src, zf.open(info, "w", force_zip64=size >= (1 << 31)) as out:
            while True:
                block = src.read(1 << 20)
                if not block:
                    break
                n += len(block)
                if n > MAX_MEMBER:
                    raise BreathError(f"{arc.rsplit('/', 1)[-1]} grew while it was packed: try again.")
                h.update(block)
                out.write(block)
    except OSError as e:
        raise BreathError(f"Can't read {path}: {e.strerror or e}.") from None
    return FileEntry(arc, n, h.hexdigest())


# ---------------------------------------------------------------------------------------------------- command line

def size_text(n: int) -> str:
    return f"{n / 2**30:.1f} GB" if n >= 2**30 else f"{n / 2**20:.1f} MB" if n >= 2**20 else f"{max(1, n // 1024)} KB"


def describe(man: Manifest) -> list[str]:
    out = [f"{man.title}" + (f" {man.version}" if man.version else "") + f" ({man.id})"]
    if man.author:
        out.append(f"By {man.author}")
    if man.description:
        out.append(man.description)
    out.append(man.what())
    out.append(f"{len(man.files)} file(s), {size_text(man.size)} unpacked")
    if man.has_code:
        out.append("Code: " + ", ".join(man.code))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="breath.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info", help="what a .breath holds (checked; nothing written)")
    p.add_argument("file")
    p = sub.add_parser("unpack", help="check a .breath and unpack it into a new folder (SHA-256s verified)")
    p.add_argument("file")
    p.add_argument("dest")
    p = sub.add_parser("export", help="a mod of the library as a .breath file")
    p.add_argument("mod", help="the mod's id, number or name (as `modman list` shows it)")
    p.add_argument("file")
    p.add_argument("--library", help="the mod library (default: as modman finds it)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "info":
            for line in describe(peek(a.file)):
                print(line)
        elif a.cmd == "unpack":
            rec = unpack(a.file, a.dest)
            for line in describe(rec.manifest):
                print(line)
            print(f"Unpacked into {rec.folder}; add it with: python tools/modman.py add \"{rec.payload}\"")
        else:
            import modman as mm
            lib = mm.Library.load(mm.resolve_library(a.library))
            if lib is None:
                print("Error: no mod library there.")
                return 2
            _, entry = lib.find(a.mod)
            res = export(lib.root, entry, a.file, install=lib.install)
            for note in res.notes:
                print(f"Note: {note}")
            print(f"Wrote {res.path} ({size_text(res.size)}): {res.manifest.title}, {len(res.manifest.files)} file(s)"
                  + (", with code" if res.manifest.has_code else ""))
    except BreathError as e:
        print(f"Error: {e}")
        return 1
    except Exception as e:  # (modman's own errors, for export)
        if type(e).__name__ == "ModmanError":
            print(f"Error: {e}")
            return 2
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
